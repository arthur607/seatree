"""The Telegram bot and the scheduler, in one process: `seahorse run`.

Every message from the user becomes an engram. A question also gets an answer at once.
The flow of a capture:
1. The message becomes an engram and the bot confirms: "✓ Saved. Integrating…".
2. Some seconds later the Integrator runs and replaces that confirmation with what it did.
3. Under the confirmation: "Read transcript" (voice only) and, once integrated, "Fix".
The scheduler runs the other agents at their times: the morning summary, the Messenger every ten minutes,
the reader of the user's notes every five minutes, the night Reviewer and the Sunday planning.
"""
import asyncio
import logging
import re
import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from . import __version__, agents, config, i18n, intro, llm, messenger, notes, telegram_api, transcribe, updates, vault, week_image

log = logging.getLogger(__name__)
CORRECTION = re.compile(r"(\d{4}-\d{2}-\d{2} \d{4}(?: \d+)?)")
ANSWER_FIELDS = {"corrects", "message_id", "answers_engram"}
WEEKDAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}  # the job queue counts from Sunday
_running: set[str] = set()


def looks_like_question(text: str) -> bool:
    lowered = text.strip().lower()
    return "?" in text or any(re.match(rf"{re.escape(w)}\b", lowered) for w in i18n.words("question_words"))


def _is_question(text: str) -> bool:
    """A cheap filter first. Only what looks like a question goes to the fast model to confirm."""
    if not looks_like_question(text):
        return False
    answer = llm.run(llm.prompt("classify", text=text), "classify", max_turns=1, timeout=90)
    return not answer.error and answer.text.strip().lower().startswith("question")


def _word(text: str) -> str:
    return text.strip().strip("!?.").strip().lower()


def asks_for_week(text: str) -> bool:
    return _word(text) in {w.lower() for w in i18n.words("week_words")}


def asks_for_day(text: str) -> bool:
    return _word(text) in {w.lower() for w in i18n.words("day_words")}


def _keyboard(buttons: list[tuple[str, str]]) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data) for label, data in row]
                                 for row in telegram_api.rows(buttons)])


async def _in_thread(name: str, job) -> None:
    """Runs an agent in a thread. The same job never runs twice at once: a late tick is skipped."""
    if name in _running:
        log.info("%s is still running, skipping this tick", name)
        return
    _running.add(name)
    try:
        result = await asyncio.to_thread(job)
        log.info("%s finished: %s", name, (result or "nothing to do")[:300])
    except Exception:  # noqa: BLE001 - one failed agent must not stop the bot
        log.exception("%s failed", name)
    finally:
        _running.discard(name)


def _job(name: str, function):
    """A job queue callback that runs one agent in a thread."""
    async def callback(context: ContextTypes.DEFAULT_TYPE) -> None:
        await _in_thread(name, function)
    return callback


def _schedule_integrator(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Waits some seconds for more messages, so one Integrator run handles all of them."""
    for job in context.job_queue.get_jobs_by_name("integrator"):
        job.schedule_removal()
    context.job_queue.run_once(_job("integrator", agents.integrator), config.INTEGRATOR_DELAY_SECONDS, name="integrator")


async def _answer(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    await update.effective_chat.send_action(ChatAction.TYPING)
    data = context.bot_data
    recent = datetime.now() - data.get("session_at", datetime.min) < timedelta(minutes=config.CHAT_WINDOW_MINUTES)
    session = data.get("session") if recent else None
    request = text if session else llm.prompt("question", text=text, **agents.moment())
    answer = await asyncio.to_thread(llm.run, request, "question", False, session, 40, 600)
    if answer.error or not answer.text:
        await update.message.reply_text(i18n.t("bot.answer_failed"))
        return
    data["session"], data["session_at"] = answer.session, datetime.now()
    for start in range(0, len(answer.text), 4000):
        await update.message.reply_text(answer.text[start:start + 4000], disable_web_page_preview=True)


async def _link_to_bot_message(update: Update) -> dict:
    """When the user replies to a bot message, this finds what that message was about."""
    original = update.message.reply_to_message
    if not (original and original.from_user and original.from_user.is_bot):
        return {}
    original_text = original.text or ""
    asked = re.escape(i18n.t("bot.fix_ask", name="\x00")).replace("\x00", CORRECTION.pattern)
    found = re.search(asked, original_text)
    if found:
        return {"corrects": f"[[{found[1]}]]"}
    sent = messenger.by_telegram(original.message_id)
    if sent:
        return {"in_reply_to": original_text[:300], "message_id": sent.id}
    engram = await asyncio.to_thread(vault.engram_by_confirmation, original.message_id)
    if engram:
        return {"answers_engram": f"[[{engram.stem}]]"}
    return {"in_reply_to": original_text[:300]}


async def _capture(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, channel: str) -> None:
    message = update.message
    when = message.date.astimezone(config.TIMEZONE)
    extra = await _link_to_bot_message(update)
    intro_question = await asyncio.to_thread(intro.waiting)
    if intro_question and not extra:
        extra = {"in_reply_to": intro_question[:300]}
    if "message_id" in extra:
        # A reply by text or voice to a message with buttons: the buttons go away, so it is clear it was answered.
        try:
            await message.reply_to_message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001 - the message may have no buttons, or they may be gone already
            pass
    question = not intro_question and not (ANSWER_FIELDS & extra.keys()) and await asyncio.to_thread(_is_question, text)
    engram = await asyncio.to_thread(vault.new_engram, text, channel, when, extra)
    if question:
        await _answer(update, context, text)
    else:
        correction = "corrects" in extra
        confirmation = await message.reply_text(
            telegram_api.capture_text(channel, correction, None),
            reply_markup=_keyboard(telegram_api.capture_buttons(engram.stem, channel, correction)))
        await asyncio.to_thread(notes.set_field, engram, "confirmation", str(confirmation.message_id))
    _schedule_integrator(context)
    if intro_question:
        await asyncio.to_thread(intro.next_question)


async def _send_week(update: Update, only_today: bool = False) -> None:
    await update.effective_chat.send_action(ChatAction.UPLOAD_PHOTO)
    image = await asyncio.to_thread(week_image.day_png if only_today else week_image.week_png)
    if image:
        await update.message.reply_photo(photo=image)
    else:
        await update.message.reply_text(i18n.t("bot.no_week"))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    if asks_for_week(text) or asks_for_day(text):
        await _send_week(update, only_today=asks_for_day(text))
        return
    await _capture(update, context, text, "telegram-text")


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_chat.send_action(ChatAction.TYPING)
    audio = await (update.message.voice or update.message.audio).get_file()
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "audio.ogg"
        await audio.download_to_drive(path)
        text = await asyncio.to_thread(transcribe.transcribe, path)
    if not text:
        await update.message.reply_text(i18n.t("bot.voice_failed"))
        return
    await _capture(update, context, text, "telegram-voice")


async def _read_transcript(query, name: str) -> None:
    engram = vault.engram(name)
    if not engram:
        await query.message.reply_text(i18n.t("bot.capture_missing"))
        return
    _, body = notes.read(engram)
    await query.message.reply_text(telegram_api.quote(body.strip()), parse_mode=ParseMode.HTML)


async def _ask_fix(query, name: str) -> None:
    await query.message.reply_text(i18n.t("bot.fix_ask", name=name),
                                   reply_markup=ForceReply(selective=True, input_field_placeholder=i18n.t("bot.fix_placeholder")))


async def _answer_message(query, context: ContextTypes.DEFAULT_TYPE, message_id: str, action: str) -> None:
    """A button under a Seahorse message. It becomes an answer engram, and the Integrator does what it says."""
    text = query.message.text or ""
    extra = {"in_reply_to": text[:300], "message_id": message_id, "via_button": "yes"}
    if action == messenger.IGNORE:
        # Ignore makes no engram and no Integrator run: it is only recorded, so the Companion learns.
        await asyncio.to_thread(messenger.set_reaction, message_id, "ignored")
        await query.edit_message_text(f"{text}\n\n→ {i18n.t('button.ignore')}")
        return
    label = next((b.text for row in query.message.reply_markup.inline_keyboard for b in row
                  if b.callback_data == query.data), action)
    if re.fullmatch(r"b\d", action):
        # A button the Companion proposed: its label is the answer. A confirmation shows later what was done.
        reply = re.sub(r"^[^\w\d]+", "", label).strip() or label
        notice = await query.message.reply_text(f"⏳ {reply}…")
        extra["confirmation"] = str(notice.message_id)
    else:
        reply = i18n.t(f"reply.{action}")
    await asyncio.to_thread(vault.new_engram, reply, "telegram-text", datetime.now(config.TIMEZONE), extra)
    await query.edit_message_text(f"{text}\n\n→ {label}")
    _schedule_integrator(context)


async def _decide_week(query, context: ContextTypes.DEFAULT_TYPE, name: str, put_in: bool) -> None:
    """The answer to a task that does not fit this week. It is in the backlog already: only "put in" needs work."""
    engram = vault.engram(name)
    channel = notes.read(engram)[0].get("channel", "telegram-text") if engram else "telegram-text"
    text = (query.message.text or "").split(f"\n\n{i18n.t('capture.week_full')}")[0]
    if put_in:
        # The confirmation is this same message: the Integrator replaces its text with what it did.
        extra = {"answers_engram": f"[[{name}]]", "confirmation": str(query.message.message_id)}
        await asyncio.to_thread(vault.new_engram, i18n.t("capture.put_anyway"), "telegram-text",
                                datetime.now(config.TIMEZONE), extra)
        await query.edit_message_text(f"{text}\n→ {i18n.t('capture.putting_in_week')}")
        _schedule_integrator(context)
    else:
        await query.edit_message_text(f"{text}\n→ {i18n.t('capture.kept_in_backlog')}",
                                      reply_markup=_keyboard(telegram_api.capture_buttons(name, channel, False, text)))


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query.from_user.id != config.USER_ID:
        return
    await query.answer()
    try:
        kind, target, action = query.data.split("|", 2)
    except ValueError:
        return
    if kind == "msg":
        await _answer_message(query, context, target, action)
    elif kind == "eng" and target == "read":
        await _read_transcript(query, action)
    elif kind == "eng" and target == "fix":
        await _ask_fix(query, action)
    elif kind == "eng" and target in ("week", "backlog"):
        await _decide_week(query, context, action, target == "week")
    elif kind == "upd":
        await query.edit_message_text(f"{query.message.text}\n\n→ {i18n.t('bot.updating')}")
        await asyncio.to_thread(updates.start_update_in_background)
    elif kind == "intro" and target == "skip":
        await query.edit_message_text(f"{query.message.text}\n\n→ {i18n.t('intro.skip')}")
        await asyncio.to_thread(intro.next_question)


async def on_integrate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(i18n.t("bot.integrating"))
    if not await asyncio.to_thread(agents.integrator):
        await update.message.reply_text(i18n.t("bot.nothing_pending"))


async def on_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(i18n.t("bot.status", pending=len(vault.engrams("pending")),
                                           unclear=len(vault.engrams("unclear"))) + f"\nSeahorse {__version__}")


async def on_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    latest = await asyncio.to_thread(updates.latest)
    if not latest or not updates.newer(latest["version"]):
        await update.message.reply_text(i18n.t("bot.up_to_date", version=__version__))
        return
    await update.message.reply_text(i18n.t("bot.updating"))
    await asyncio.to_thread(updates.start_update_in_background)


async def on_intro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await asyncio.to_thread(intro.start)


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(i18n.t("bot.start"))


def _at(hhmm: str) -> time:
    hours, minutes = map(int, hhmm.split(":"))
    return time(hours, minutes, tzinfo=config.TIMEZONE)


def schedule(app: Application) -> None:
    """The agents' timetable. Times are in the user's time zone."""
    jobs = app.job_queue
    s = config.SCHEDULE
    for name, interval, first in (("messenger", 600, 90), ("notes", 300, 45)):
        jobs.run_repeating(_job(name, agents.AGENTS[name]), interval, first=first, name=name)
    jobs.run_repeating(_job("retry", agents.retry_pending), 900, first=120, name="retry")
    jobs.run_daily(_job("morning", agents.morning), _at(s["morning"]), name="morning")
    jobs.run_daily(_job("reviewer", agents.reviewer), _at(s["reviewer"]), name="reviewer")
    jobs.run_daily(_job("weekly", agents.weekly), _at(s["weekly_time"]), days=(WEEKDAYS[s["weekly_day"]],), name="weekly")
    jobs.run_daily(_job("update check", updates.notify_if_new), _at("12:00"), name="update check")


def run() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not config.TELEGRAM_TOKEN or not config.USER_ID:
        raise SystemExit("Telegram is not set up. Run `seahorse setup`.")
    if not config.VAULT.exists():
        raise SystemExit(f"No vault at {config.VAULT}. Run `seahorse setup`.")
    only_owner = filters.User(user_id=config.USER_ID)
    app = Application.builder().token(config.TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", on_start, filters=only_owner))
    app.add_handler(CommandHandler("integrate", on_integrate, filters=only_owner))
    app.add_handler(CommandHandler("status", on_status, filters=only_owner))
    app.add_handler(CommandHandler("update", on_update, filters=only_owner))
    app.add_handler(CommandHandler("intro", on_intro, filters=only_owner))
    app.add_handler(MessageHandler(only_owner & filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(only_owner & (filters.VOICE | filters.AUDIO), on_voice))
    buttons = r"^(msg|eng|intro|upd)\|"  # Seahorse messages, captures, the first conversation, the update
    app.add_handler(CallbackQueryHandler(on_button, pattern=buttons))
    schedule(app)
    log.info("Seahorse %s is running for %s, vault %s, provider %s", __version__, config.USER_NAME, config.VAULT, config.PROVIDER)
    app.run_polling(allowed_updates=Update.ALL_TYPES)
