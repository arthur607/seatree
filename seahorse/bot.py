"""The Telegram bot and the scheduler, in one process: `seahorse run`.

Every message from the user becomes an engram. A question also gets an answer at once.
The flow of a capture:
1. The message becomes an engram and the bot confirms: "✓ Saved. Integrating…".
2. Some seconds later the Integrator runs and replaces that confirmation with what it did.
3. Under the confirmation: "Read transcript" (voice only) and, once integrated, "Fix".
Photos and files go to the attachments folder, and their engram embeds them. Locations and contacts become text.
An edited message becomes a correction of its engram.
The bot handles updates at the same time: a long answer or an Integrator run never holds back a button press.
The scheduler runs the other agents at their times: the morning summary, the Messenger every ten minutes,
the reader of the user's notes every five minutes, the night Reviewer and the Sunday planning.
"""
import asyncio
import contextlib
import html
import io
import logging
import mimetypes
import re
import tempfile
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from telegram import BotCommand, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, Conflict, NetworkError, RetryAfter, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from . import __version__, agents, config, i18n, intro, llm, messenger, notes, now, retro, telegram_api, transcribe, updates, vault, week_image

log = logging.getLogger(__name__)
CORRECTION = re.compile(r"(\d{4}-\d{2}-\d{2} \d{4}(?: \d+)?)")
WEEKDAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}  # the job queue counts from Sunday
# Web addresses: a "?" inside them, such as youtube.com/watch?v=…, does not make a question.
URL = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+(?:\.[\w-]+)+/\S*", re.IGNORECASE)
FILE_LIMIT = 20 * 1024 * 1024  # the Bot API gives bots files of up to 20 MB
KEEP_LIMIT = 10 * 1024 * 1024  # photos and files kept in the notes stay small: the phone syncs the whole folder
REPLY_ROOM = 3000  # characters of a Seahorse message kept as the context of an answer
EDIT_HOURS = 48  # an edit of an older message does not change what was saved
LINK_HOURS = 2  # a message without Reply may answer what Seahorse asked in the last two hours
THREAD_MINUTES = 10  # after an answer, the messages that follow it this closely continue it
TYPING_EVERY = 4  # Telegram shows "typing…" for 5 seconds
CLASSIFY_SECONDS = 90  # the longest wait for the fast model to say if a message is a question
ALBUM_SECONDS = 2  # the photos of an album arrive one by one, this close together
STATUS_SHOWN = 10
KEPT = 500  # how many message ids the bot keeps in memory for the checks below
COMMANDS = ("help", "now", "status", "integrate", "plan", "retro", "intro", "update")
LABELS = {"photo": "Photo", "file": "File", "voice": "Voice"}
BUSY, FAILED = object(), object()  # what _in_thread returns when the job did not run, or failed

_running: set[str] = set()
_again: dict[str, object] = {}  # job name → the job to run once more, right after the current run
_saved: OrderedDict = OrderedDict()  # Telegram message id → its engram, so an error can say it is safe
_pressed: OrderedDict = OrderedDict()  # buttons already handled, so a double tap does the work once
_told: OrderedDict = OrderedDict()  # messages that already got an error reply


def _keep(store: OrderedDict, key, value=None) -> None:
    store[key] = value
    store.move_to_end(key)
    while len(store) > KEPT:
        store.popitem(last=False)


# --- What a message says ---

def looks_like_question(text: str) -> bool:
    """A cheap filter: a question mark outside the web addresses, or a question word at the start."""
    lowered = text.strip().lower()
    return "?" in URL.sub(" ", text) or any(re.match(rf"{re.escape(w)}\b", lowered) for w in i18n.words("question_words"))


def _is_question(text: str, about: str = "") -> bool:
    """A cheap filter first. Only what looks like a question goes to the fast model to confirm."""
    if not looks_like_question(text):
        return False
    context = f'It replies to this message from Seahorse:\n"{about[:1500]}"' if about else ""
    answer = llm.run(llm.prompt("classify", text=text, context=context), "classify", max_turns=1,
                     timeout=CLASSIFY_SECONDS)
    return not answer.error and answer.text.strip().lower().startswith("question")


def _word(text: str) -> str:
    return text.strip().strip("!?.").strip().lower()


def asks_for_week(text: str) -> bool:
    return _word(text) in {w.lower() for w in i18n.words("week_words")}


def asks_for_day(text: str) -> bool:
    return _word(text) in {w.lower() for w in i18n.words("day_words")}


def asks_for_retro(text: str) -> bool:
    return _word(text) in {w.lower() for w in i18n.words("retro.words")}


def with_hidden_links(text: str, entities) -> str:
    """Adds the address of each link hidden under words: "this video (https://…)". Telegram counts the
    positions of the links in UTF-16 units."""
    raw, pieces, last = text.encode("utf-16-le"), [], 0
    for entity in sorted(entities or (), key=lambda e: e.offset):
        url = getattr(entity, "url", None)
        if entity.type != "text_link" or not url or url in text:
            continue
        end = (entity.offset + entity.length) * 2
        pieces += [raw[last:end].decode("utf-16-le"), f" ({url})"]
        last = end
    return "".join(pieces) + raw[last:].decode("utf-16-le")


def origin_name(origin) -> str:
    """Who wrote a forwarded message first: a person, a person who hides their account, a group or a channel."""
    user = getattr(origin, "sender_user", None)
    if user:
        return user.full_name
    if getattr(origin, "sender_user_name", None):
        return origin.sender_user_name
    chat = getattr(origin, "sender_chat", None) or getattr(origin, "chat", None)
    name = (getattr(chat, "title", None) or getattr(chat, "full_name", None)) if chat else None
    signature = getattr(origin, "author_signature", None)
    return " · ".join(p for p in (name, signature) if p) or "?"


def message_text(message) -> str:
    """What a message says: its text or caption, with the address of each hidden link, and who wrote it first
    when it is forwarded."""
    if message.text:
        text, entities = message.text, message.entities
    else:
        text, entities = message.caption or "", message.caption_entities
    text = with_hidden_links(text, entities)
    origin = getattr(message, "forward_origin", None)
    if origin and text.strip():
        text = i18n.t("chat.forwarded", name=origin_name(origin)) + "\n" + text
    return text


def first_words(text: str, count: int = 8) -> str:
    """The start of a capture, to name it for the user. Embedded files are left out."""
    lines = [line for line in text.splitlines() if line.strip() and not line.strip().startswith("![[")]
    words = " ".join(lines).split()
    if not words:
        return text.strip()[:60]
    return " ".join(words[:count]) + ("…" if len(words) > count else "")


def location_text(message) -> str:
    venue = message.venue
    place = venue.location if venue else message.location
    link = f"https://maps.google.com/?q={place.latitude:.6f},{place.longitude:.6f}"
    if venue:
        return f"📍 {venue.title}, {venue.address}\n{link}"
    return f"📍 {i18n.t('chat.location')}: {place.latitude:.5f}, {place.longitude:.5f}\n{link}"


def contact_text(contact) -> str:
    name = " ".join(p for p in (contact.first_name, contact.last_name) if p)
    return " · ".join(p for p in (f"👤 {i18n.t('chat.contact')}: {name}", contact.phone_number) if p)


def safe_name(name: str) -> str:
    """A file name that works in Obsidian links and on every system."""
    clean = " ".join(re.sub(r'[\\/:*?"<>|#^\[\]\x00-\x1f]+', " ", name).split()).strip(" .")
    stem, suffix = Path(clean).stem, Path(clean).suffix
    return f"{stem[:80].strip()}{suffix[:10]}" if stem else ""


def attachment_name(kind: str, when: datetime, original: str | None = None, suffix: str = "") -> str:
    """The name of an attachment: the name the file came with, or its kind and time, such as
    "Photo 2026-10-21 0814.webp"."""
    given = safe_name(original or "")
    if given:
        return given if Path(given).suffix else given + suffix
    return f"{LABELS[kind]} {when:%Y-%m-%d %H%M}{suffix}"


def as_webp(data: bytes) -> bytes | None:
    """A photo as WebP, smaller than the JPEG that Telegram sends, so the notes stay light on the phone."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            out = io.BytesIO()
            image.save(out, "WEBP", quality=80)
    except Exception:  # noqa: BLE001 - the photo stays as Telegram sent it
        log.warning("could not convert a photo to WebP", exc_info=True)
        return None
    return out.getvalue() if out.tell() < len(data) else None


# --- What a message answers ---

@dataclass
class Link:
    """What a message answers. `fields` go into its engram."""
    fields: dict
    step: int | None = None  # the intro question it answers
    sent: str | None = None  # the Messenger id of the Seahorse message it answers
    tg: int | None = None  # that message on Telegram, to take its buttons away


def last_asking(history: list[messenger.Message], now: datetime, thread: dict | None = None) -> messenger.Message | None:
    """The Seahorse message that a message sent without Telegram's Reply most likely answers.

    It is the newest conversation of the last LINK_HOURS: a check-in, a question, any message of the Companion,
    which always asks one thing. Reminders, deliveries and the morning summary do not count. It links:
    - while it waits for an answer, that is, while its reaction is still pending;
    - after an answer, while the user goes on writing: each message less than THREAD_MINUTES after the one
      before. `thread` holds the last answer: {"id": Messenger id, "at": time}.
    A longer pause means the user moved on, and a new message is a new subject."""
    recent = [m for m in history if m.kind in messenger.CONVERSATION and m.tg and now - m.when < timedelta(hours=LINK_HOURS)]
    if not recent:
        return None
    last = max(reversed(recent), key=lambda m: m.when)
    if last.reaction == "pending":
        return last
    if thread and thread.get("id") == last.id and now - thread["at"] < timedelta(minutes=THREAD_MINUTES):
        return last
    return None


def _fix_target(message_id: int, text: str) -> str | None:
    """The engram that a "What should I change?" message is about. Older versions wrote its name in the text."""
    found = vault.engrams_with("fix_prompt", str(message_id))
    if found:
        return found[0].stem
    asked = re.escape(i18n.t("bot.fix_ask", name="\x00")).replace("\x00", CORRECTION.pattern)
    old = re.search(asked, text)
    return old[1] if old else None


async def _reply_link(update: Update) -> Link:
    """When the user replies to a bot message, this finds what that message was about."""
    original = update.effective_message.reply_to_message
    if not (original and original.from_user and original.from_user.is_bot):
        return Link({})
    text = original.text or original.caption or ""
    fixed = await asyncio.to_thread(_fix_target, original.message_id, text)
    if fixed:
        return Link({"corrects": f"[[{fixed}]]"})
    sent = await asyncio.to_thread(messenger.by_telegram, original.message_id)
    if sent:
        return Link({"in_reply_to": text[:REPLY_ROOM], "message_id": sent.id}, sent=sent.id, tg=original.message_id)
    engram = await asyncio.to_thread(vault.engram_by_confirmation, original.message_id)
    if engram:
        return Link({"answers_engram": f"[[{engram.stem}]]"})
    state = await asyncio.to_thread(intro.current)
    step = state["step"] if state and state.get("message") == original.message_id else None
    return Link({"in_reply_to": text[:REPLY_ROOM]} if text else {}, step=step)


async def _soft_link(context: ContextTypes.DEFAULT_TYPE) -> Link:
    """A message without Reply: the intro question that waits, or what Seahorse asked last (see last_asking).
    When both exist, the newer one wins."""
    now = datetime.now(config.TIMEZONE)
    state, history = await asyncio.to_thread(lambda: (intro.current(), messenger.messages()))
    asked = last_asking(history, now, context.bot_data.get("thread"))
    if state and (not asked or state.get("asked_at", 0) >= asked.when.timestamp()):
        return Link({"in_reply_to": state["question"]}, step=state["step"])
    if not asked:
        return Link({})
    tg = int(asked.tg)
    text = telegram_api.sent_text(tg)
    fields = {"in_reply_to": text[:REPLY_ROOM]} if text else {}
    return Link(fields | {"message_id": asked.id}, sent=asked.id, tg=tg)


async def _drop_buttons(bot, message_id: int | None) -> None:
    """Takes away the buttons of a message that got its answer. It may have none, or they may be gone already."""
    if not message_id:
        return
    try:
        await bot.edit_message_reply_markup(chat_id=config.USER_ID, message_id=message_id, reply_markup=None)
    except TelegramError:
        pass


# --- Jobs ---

def _keyboard(buttons: list[tuple[str, str]]) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data) for label, data in row]
                                 for row in telegram_api.rows(buttons)])


async def _in_thread(name: str, job, again: bool = False):
    """Runs an agent in a thread and returns its result. The same job never runs twice at once: a late tick is
    skipped. With `again`, a skipped tick is not lost: the job runs once more right after the current run, for
    what came in meanwhile, such as a capture sent while the Integrator works. The first run that did something
    gives the result."""
    if name in _running:
        if again:
            _again[name] = job
        log.info("%s is still running, %s", name, "it runs again right after" if again else "skipping this tick")
        return BUSY
    _running.add(name)
    outcome = None
    try:
        while True:
            try:
                result = await asyncio.to_thread(job)
                log.info("%s finished: %s", name, str(result or "nothing to do")[:300])
            except Exception:  # noqa: BLE001 - one failed agent must not stop the bot
                log.exception("%s failed", name)
                result = FAILED
            outcome = outcome or result
            job = _again.pop(name, None)
            if job is None:
                return outcome
    finally:
        _running.discard(name)


def _job(name: str, function, again: bool = False):
    """A job queue callback that runs one agent in a thread."""
    async def callback(context: ContextTypes.DEFAULT_TYPE) -> None:
        await _in_thread(name, function, again)
    return callback


def _schedule_integrator(context: ContextTypes.DEFAULT_TYPE, extra_seconds: int = 0) -> None:
    """Waits some seconds for more messages, so one Integrator run handles all of them."""
    for job in context.job_queue.get_jobs_by_name("integrator"):
        job.schedule_removal()
    context.job_queue.run_once(_job("integrator", agents.integrator, again=True),
                               config.INTEGRATOR_DELAY_SECONDS + extra_seconds, name="integrator")


async def _retry(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Every 15 minutes: the voice notes that wait for their text, then the captures that wait too long."""
    made = await _in_thread("voice", transcribe.retry_waiting)
    if isinstance(made, int) and made:
        _schedule_integrator(context)
    await _in_thread("integrator", agents.retry_pending)


def _schedule_intro(context: ContextTypes.DEFAULT_TYPE, step: int, delay: int | None = None) -> None:
    """The next intro question comes when the person stops writing their answer for a moment."""
    for job in context.job_queue.get_jobs_by_name("intro"):
        job.schedule_removal()
    context.job_queue.run_once(_intro_next, intro.ANSWER_PAUSE if delay is None else delay, data=step, name="intro")


async def _intro_next(context: ContextTypes.DEFAULT_TYPE) -> None:
    if _questions(context).locked():  # an answer to a question of the user is on its way: it goes first
        _schedule_intro(context, context.job.data)
        return
    answered = await asyncio.to_thread(intro.advance, context.job.data)
    if answered:
        await _drop_buttons(context.bot, answered.get("message"))


# --- Captures and answers ---

def _questions(context: ContextTypes.DEFAULT_TYPE) -> asyncio.Lock:
    """Questions get their answers one at a time, so a follow-up continues the same AI session."""
    return context.bot_data.setdefault("questions", asyncio.Lock())


@contextlib.asynccontextmanager
async def _typing(chat):
    """Shows "typing…" until the block ends."""
    async def keep() -> None:
        while True:
            try:
                await chat.send_action(ChatAction.TYPING)
            except TelegramError:
                pass
            await asyncio.sleep(TYPING_EVERY)
    task = asyncio.create_task(keep())
    try:
        yield
    finally:
        task.cancel()


async def _answer(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, about: str = "") -> None:
    """Answers a question from the vault, or from the web when the vault has no answer. `about` is the Seahorse
    message that the question replies to."""
    async with _typing(update.effective_chat), _questions(context):
        data = context.bot_data
        recent = datetime.now() - data.get("session_at", datetime.min) < timedelta(minutes=config.CHAT_WINDOW_MINUTES)
        session = data.get("session") if recent else None
        context_line = f'It replies to this message from Seahorse:\n"{about}"' if about else ""
        if session:
            request = f"{text}\n\n{context_line}".strip()
        else:
            request = llm.prompt("question", text=text, context=context_line, **agents.moment())
        answer = await asyncio.to_thread(llm.run, request, "question", session=session, max_turns=40, timeout=600,
                                         web=True)
        if not answer.error and answer.text:
            data["session"], data["session_at"] = answer.session, datetime.now()
    message = update.effective_message
    if answer.error or not answer.text:
        await message.reply_text(i18n.t("bot.answer_failed"))
        return
    for piece in telegram_api.split(answer.text):
        await message.reply_text(piece, disable_web_page_preview=True)


async def _classify(update: Update, text: str, about: str) -> bool:
    async with _typing(update.effective_chat):
        return await asyncio.to_thread(_is_question, text, about)


async def _show_heard(message, text: str) -> None:
    """A question by voice: first what was understood, so a wrong transcript is easy to see."""
    for piece in telegram_api.split(text):
        await message.reply_text("🎤 " + telegram_api.quote(piece), parse_mode=ParseMode.HTML)


def _open_thread(context: ContextTypes.DEFAULT_TYPE, sent: str) -> None:
    context.bot_data["thread"] = {"id": sent, "at": datetime.now(config.TIMEZONE)}


async def _capture(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, channel: str,
                   extra: dict | None = None, ask: bool = False) -> Path:
    """Saves a message as an engram, then confirms it, or answers it when it is a question.

    The engram is saved first, so nothing is lost. A message that looks like a question goes to the fast model
    with "typing…" on screen: a question gets its answer, the field `asked: question`, and no "Saved"
    confirmation. A Reply links the engram to what it answers, and stays even on a question. Without a Reply,
    the link to the last thing Seahorse asked is added only when the message is not a question.
    `extra` replaces the Reply link, as for an edit. `ask` checks for a question even in a correction."""
    message = update.effective_message
    when = (message.edit_date or message.date).astimezone(config.TIMEZONE)
    link = Link(dict(extra)) if extra else await _reply_link(update)
    correction = "corrects" in link.fields
    soft = Link({}) if link.fields else await _soft_link(context)
    forwarded = getattr(message, "forward_origin", None) is not None
    maybe = (ask or not correction) and not forwarded and looks_like_question(text)
    fields = link.fields | ({} if maybe else soft.fields) | {"telegram_id": str(message.message_id)}
    engram = await asyncio.to_thread(vault.save_engram, text, channel, when, fields)
    _keep(_saved, message.message_id, engram.stem)
    # The Integrator waits for the rest of a burst and, for a possible question, for the check below.
    _schedule_integrator(context, CLASSIFY_SECONDS if maybe else 0)
    about = link.fields.get("in_reply_to", "")
    if maybe and await _classify(update, text, about):
        await asyncio.to_thread(notes.set_field, engram, "asked", "question")
        if channel == "telegram-voice":
            await _show_heard(message, text)
        await _answer(update, context, text, about)
        _schedule_integrator(context)
        return engram
    for key, value in (soft.fields if maybe else {}).items():
        await asyncio.to_thread(notes.set_field, engram, key, notes.quoted(value))
    answered = link if link.fields else soft
    if answered.sent:
        _open_thread(context, answered.sent)
        await _drop_buttons(context.bot, answered.tg)
    confirmation = await message.reply_text(
        telegram_api.capture_text(channel, correction, None),
        reply_markup=_keyboard(telegram_api.capture_buttons(engram.stem, channel, correction)))
    await asyncio.to_thread(notes.set_field, engram, "confirmation", str(confirmation.message_id))
    if answered.step is not None:
        _schedule_intro(context, answered.step)
    _schedule_integrator(context)
    return engram


async def _send_week(update: Update, only_today: bool = False) -> None:
    await update.effective_chat.send_action(ChatAction.UPLOAD_PHOTO)
    try:
        image = await asyncio.to_thread(week_image.day_png if only_today else week_image.week_png)
    except Exception:  # noqa: BLE001 - a broken week file must not leave the user with no answer
        log.exception("the week image failed")
        image = None
    if image:
        await update.effective_message.reply_photo(photo=image)
    else:
        await update.effective_message.reply_text(i18n.t("bot.no_week"))


async def _send_retro(update: Update) -> None:
    """The last week that ended, in one image. Its pattern line comes from the state file: no new AI run."""
    await update.effective_chat.send_action(ChatAction.UPLOAD_PHOTO)
    try:
        found = await asyncio.to_thread(retro.on_demand)
    except Exception:  # noqa: BLE001 - a broken file must not leave the user with no answer
        log.exception("the retrospective failed")
        await update.effective_message.reply_text(i18n.t("retro.failed"))
        return
    if found:
        image, caption = found
        await update.effective_message.reply_photo(photo=image, caption=caption)
    else:
        await update.effective_message.reply_text(i18n.t("retro.nothing"))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if asks_for_week(message.text) or asks_for_day(message.text):
        await _send_week(update, only_today=asks_for_day(message.text))
        return
    if asks_for_retro(message.text):
        await _send_retro(update)
        return
    minutes = now.trigger(message.text)
    if minutes is not None and getattr(message, "forward_origin", None) is None:
        await _now_reply(message, update.effective_chat, minutes)
        return
    await _capture(update, context, message_text(message), "telegram-text")


async def on_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """An edited message becomes a correction of its engram, confirmed like any correction."""
    message = update.edited_message
    text = message_text(message)
    if message.location or not text.strip() or (message.text or "").startswith("/"):
        return  # a live location that moves, a photo that changed, or a command: nothing new to save
    if datetime.now(config.TIMEZONE) - message.date > timedelta(hours=EDIT_HOURS):
        await message.reply_text(i18n.t("chat.edit_too_old"))
        return
    found = await asyncio.to_thread(vault.engrams_with, "telegram_id", str(message.message_id))
    if not found:
        await message.reply_text(i18n.t("chat.edit_unknown"))
        return
    # The oldest engram of this message is the message as it was first sent. When that was itself a correction,
    # the new text corrects the same engram.
    fm = notes.read(found[-1])[0]
    target = fm.get("corrects") or f"[[{found[-1].stem}]]"
    await _capture(update, context, text, fm.get("channel", "telegram-text"), {"corrects": target},
                   ask=fm.get("asked") == "question")


# --- Voice, photos, files and the rest ---

async def _download(item) -> bytes | None:
    """The bytes of a file the user sent. None when Telegram does not give it, as for a file over 20 MB."""
    try:
        file = await item.get_file()
        return bytes(await file.download_as_bytearray())
    except BadRequest as error:
        log.warning("could not download a file: %s", error)
        return None


async def _too_big(message, size: int | None, limit: int = FILE_LIMIT) -> None:
    most = limit // 2 ** 20
    await message.reply_text(i18n.t("chat.file_too_big", size=max(most + 1, round((size or 0) / 2 ** 20)), limit=most))


def _hear(data: bytes, name: str) -> str | None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"audio{Path(name).suffix}"
        path.write_bytes(data)
        return transcribe.transcribe(path)


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A voice note, an audio file or a round video: its words are the capture.

    With no way to get text (no Groq key and no local model), the audio goes to the attachments folder and the
    engram embeds it. When the transcription fails now, the audio waits for the retry tick (see transcribe.py)."""
    message = update.effective_message
    media = message.voice or message.audio or message.video_note
    when = message.date.astimezone(config.TIMEZONE)
    suffix = ".mp4" if message.video_note else ".ogg" if message.voice else (
        Path(media.file_name or "").suffix or mimetypes.guess_extension(media.mime_type or "") or ".mp3")
    name = attachment_name("voice", when, getattr(media, "file_name", None), suffix)
    if (media.file_size or 0) > FILE_LIMIT:
        await _too_big(message, media.file_size)
        return
    can_hear = transcribe.available()
    async with _typing(update.effective_chat):
        data = await _download(media)
        if data is None:
            await _too_big(message, media.file_size)
            return
        heard = await asyncio.to_thread(_hear, data, name) if can_hear else None
    if heard is None and not can_hear:
        saved = await asyncio.to_thread(vault.save_attachment, data, name)
        await message.reply_text(i18n.t("chat.voice_off"))
        await _capture(update, context, transcribe.without_text(saved.name), "telegram-voice")
    elif heard is None:
        link = await _reply_link(update)
        notice = await message.reply_text(i18n.t("chat.voice_later"))
        fields = link.fields | {"telegram_id": str(message.message_id)}
        await asyncio.to_thread(transcribe.wait, data, name, "telegram-voice", when, fields, notice.message_id)
        _keep(_saved, message.message_id, "waiting")
    elif not heard.strip():
        await message.reply_text(i18n.t("bot.voice_failed"))
    else:
        if message.video_note:
            await message.reply_text(i18n.t("chat.video_note"))
        await _capture(update, context, heard, "telegram-voice")


def _store(data: bytes, kind: str, when: datetime, original: str | None, mime: str | None) -> str:
    """Writes a photo or a file to the attachments folder and returns its name. A photo becomes WebP."""
    if kind == "photo":
        smaller = as_webp(data)
        data, suffix = (smaller, ".webp") if smaller else (data, ".jpg")
    else:
        suffix = Path(original or "").suffix or mimetypes.guess_extension(mime or "") or ""
    return vault.save_attachment(data, attachment_name(kind, when, original, suffix)).name


async def _save_media(item, kind: str, when: datetime) -> str | None:
    """Downloads a photo or a file into the notes. None when it is too big for Telegram to give it."""
    if (item.file_size or 0) > KEEP_LIMIT:
        return None
    data = await _download(item)
    if data is None:
        return None
    return await asyncio.to_thread(_store, data, kind, when, getattr(item, "file_name", None),
                                   getattr(item, "mime_type", None))


async def _media(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str) -> None:
    """A photo or a file: it goes to the attachments folder, and its engram embeds it under the caption."""
    message = update.effective_message
    item = message.photo[-1] if kind == "photo" else message.document
    caption = message_text(message)
    channel = "telegram-photo" if kind == "photo" else "telegram-file"
    part = _album_part(update, context, channel, caption) if message.media_group_id else None
    name = None
    try:
        name = await _save_media(item, kind, message.date.astimezone(config.TIMEZONE))
        if name is None:
            await _too_big(message, item.file_size, KEEP_LIMIT)
    finally:
        if part is not None:
            part["line"], part["done"] = name and f"![[{name}]]", True
            _schedule_album(context, message.media_group_id)
    if part is not None or (name is None and not caption.strip()):
        return
    if name is None:  # too big: the caption is kept, with the name of the file
        file_name = getattr(item, "file_name", None)
        await _capture(update, context, f"{caption}\n📎 {file_name}" if file_name else caption, channel)
        return
    title = caption.strip() or (i18n.t("chat.photo") if kind == "photo" else f"📎 {name}")
    await _capture(update, context, f"{title}\n![[{name}]]", channel)


def _album_part(update: Update, context: ContextTypes.DEFAULT_TYPE, channel: str, caption: str) -> dict:
    """One photo or file of an album. Telegram sends each one alone: the bot joins them in one engram."""
    group = update.effective_message.media_group_id
    album = context.bot_data.setdefault("albums", {}).setdefault(group, {"channel": channel, "parts": []})
    part = {"update": update, "caption": caption, "line": None, "done": False}
    album["parts"].append(part)
    _schedule_album(context, group)
    return part


def _schedule_album(context: ContextTypes.DEFAULT_TYPE, group: str) -> None:
    for job in context.job_queue.get_jobs_by_name(f"album {group}"):
        job.schedule_removal()
    context.job_queue.run_once(_album_done, ALBUM_SECONDS, data=group, name=f"album {group}")


async def _album_done(context: ContextTypes.DEFAULT_TYPE) -> None:
    albums = context.bot_data.get("albums", {})
    album = albums.get(context.job.data)
    if not album:
        return
    if not all(p["done"] for p in album["parts"]):
        _schedule_album(context, context.job.data)  # a download is still on its way
        return
    del albums[context.job.data]
    parts = sorted(album["parts"], key=lambda p: p["update"].effective_message.message_id)
    captions = [p["caption"].strip() for p in parts if p["caption"].strip()]
    lines = [p["line"] for p in parts if p["line"]]
    if not lines and not captions:
        return
    title = captions or [i18n.t("chat.photos" if album["channel"] == "telegram-photo" else "chat.files", count=len(parts))]
    await _capture(parts[0]["update"], context, "\n".join(title + lines), album["channel"])


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _media(update, context, "photo")


async def on_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _media(update, context, "file")


async def on_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A video is not kept: it is too heavy for notes that sync to a phone. Its caption is a normal capture."""
    message = update.effective_message
    caption = message_text(message)
    await message.reply_text(i18n.t("chat.video_not_kept"))
    if caption.strip():
        await _capture(update, context, caption, "telegram-video")


async def on_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _capture(update, context, location_text(update.effective_message), "telegram-location")


async def on_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _capture(update, context, contact_text(update.effective_message.contact), "telegram-contact")


async def on_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stickers, GIFs and games: a friendly word, and nothing to save."""
    await update.effective_message.reply_text(i18n.t("chat.sticker"))


# --- Buttons ---

async def _answer_query(query, text: str | None = None) -> None:
    """Stops the button's loading clock. An old press can no longer be answered: that is no problem."""
    try:
        await query.answer(text)
    except TelegramError:
        pass


async def _edit(query, text: str, markup=None) -> None:
    """Edits the message of a button. A message that already says this stays as it is."""
    try:
        await query.edit_message_text(text, reply_markup=markup)
    except BadRequest as error:
        if "not modified" not in str(error).lower():
            log.warning("could not edit a message: %s", error)


async def _read_transcript(query, name: str) -> None:
    engram = await asyncio.to_thread(vault.engram, name)
    if not engram:
        await query.message.reply_text(i18n.t("bot.capture_missing"))
        return
    _, body = notes.read(engram)
    for piece in telegram_api.split(body.strip()):
        await query.message.reply_text(telegram_api.quote(piece), parse_mode=ParseMode.HTML)


async def _ask_fix(query, name: str) -> None:
    """Asks what to change. The reply to this question becomes a correction of the engram."""
    engram = await asyncio.to_thread(vault.engram, name)
    words = first_words(notes.read(engram)[1]) if engram else name
    prompt = await query.message.reply_text(
        i18n.t("chat.fix_ask", words=words),
        reply_markup=ForceReply(selective=True, input_field_placeholder=i18n.t("bot.fix_placeholder")))
    if engram:
        await asyncio.to_thread(notes.set_field, engram, "fix_prompt", str(prompt.message_id))


# Buttons that are only recorded as the reaction to a message: no engram, so no Integrator run.
RECORDED = {messenger.IGNORE: "ignored", "useful": "acted"}  # "useful" is the 👍 under a research delivery


def tapped(query) -> str:
    """The label of the button that was tapped."""
    markup = query.message.reply_markup
    return next((b.text for row in (markup.inline_keyboard if markup else []) for b in row
                 if b.callback_data == query.data), "")


def link_buttons(markup) -> InlineKeyboardMarkup | None:
    """The buttons of a message that open a link, such as "Open in Obsidian". They stay after an answer."""
    rows = [[b for b in row if getattr(b, "url", None)] for row in (markup.inline_keyboard if markup else [])]
    rows = [row for row in rows if row]
    return InlineKeyboardMarkup(rows) if rows else None


async def _mark_answered(query, label: str) -> None:
    """Writes under a Seahorse message which button was tapped. The message keeps its formatting and its link
    buttons. When the line does not fit in the message, only the other buttons go away."""
    message, links = query.message, link_buttons(query.message.reply_markup)
    body = getattr(message, "text_html", None) or html.escape(message.text or "")
    if message.text and len(message.text) + len(label) + 4 <= 4096:
        try:
            await query.edit_message_text(f"{body}\n\n→ {html.escape(label)}", parse_mode=ParseMode.HTML,
                                          reply_markup=links, disable_web_page_preview=True)
            return
        except BadRequest as error:
            if "not modified" in str(error).lower():
                return
            log.warning("could not edit a message: %s", error)
    try:
        await query.edit_message_reply_markup(reply_markup=links)
    except TelegramError:
        pass


async def _answer_message(query, context: ContextTypes.DEFAULT_TYPE, message_id: str, action: str) -> None:
    """A button under a Seahorse message. It becomes an answer engram, and the Integrator does what it says.
    A notice shows at once that it is on its way, and the Integrator turns it into what was done."""
    label = tapped(query) or i18n.t("button.ignore" if action == messenger.IGNORE else f"reply.{action}")
    if action in RECORDED:
        # Only recorded, so the Companion learns what is useful.
        await asyncio.to_thread(messenger.set_reaction, message_id, RECORDED[action])
        await _mark_answered(query, label)
        return
    text = query.message.text or ""
    extra = {"in_reply_to": text[:REPLY_ROOM], "message_id": message_id, "via_button": "yes"}
    shown = re.sub(r"^[^\w\d]+", "", label).strip() or label
    # A button the Companion proposed: its label is the answer. A reminder button says it in words.
    reply = shown if re.fullmatch(r"b\d", action) else i18n.t(f"reply.{action}")
    notice = await query.message.reply_text(f"⏳ {shown}…")
    extra["confirmation"] = str(notice.message_id)
    await asyncio.to_thread(vault.save_engram, reply, "telegram-text", datetime.now(config.TIMEZONE), extra)
    await _mark_answered(query, label)
    _schedule_integrator(context)


async def _decide_week(query, context: ContextTypes.DEFAULT_TYPE, name: str, put_in: bool) -> None:
    """The answer to a task that does not fit this week. It is in the backlog already: only "put in" needs work."""
    engram = await asyncio.to_thread(vault.engram, name)
    channel = notes.read(engram)[0].get("channel", "telegram-text") if engram else "telegram-text"
    text = (query.message.text or "").split(f"\n\n{i18n.t('capture.week_full')}")[0]
    if put_in:
        # The confirmation is this same message: the Integrator replaces its text with what it did.
        extra = {"answers_engram": f"[[{name}]]", "confirmation": str(query.message.message_id)}
        await asyncio.to_thread(vault.save_engram, i18n.t("capture.put_anyway"), "telegram-text",
                                datetime.now(config.TIMEZONE), extra)
        await _edit(query, f"{text}\n→ {i18n.t('capture.putting_in_week')}")
        _schedule_integrator(context)
    else:
        await _edit(query, f"{text}\n→ {i18n.t('capture.kept_in_backlog')}",
                    _keyboard(telegram_api.capture_buttons(name, channel, False, text)))


async def _update_now(query, key: str) -> None:
    """The button under the news of a new version. It checks first: the button may be old."""
    text = query.message.text or ""
    latest = await asyncio.to_thread(updates.latest)
    if latest is None:
        _pressed.pop(key, None)  # it may be pressed again later
        await query.message.reply_text(i18n.t("chat.update_unknown"))
    elif not latest["version"] or not updates.newer(latest["version"]):
        await _edit(query, f"{text}\n\n→ {i18n.t('bot.up_to_date', version=__version__)}")
    else:
        await _edit(query, f"{text}\n\n→ {i18n.t('bot.updating')}")
        await asyncio.to_thread(updates.start_update_in_background)


async def _intro_button(query, target: str, action: str) -> None:
    """Skip or Stop under an intro question. On an old question, only its buttons go away."""
    step = int(action) if action.isdigit() else -1
    text = query.message.text or ""
    if target == "skip":
        done = await asyncio.to_thread(intro.advance, step, query.message.message_id)
    else:
        done = await asyncio.to_thread(intro.stop, step, query.message.message_id)
    await _edit(query, f"{text}\n\n→ {i18n.t('intro.' + target)}" if done else text)


def press_key(kind: str, target: str, message_id: int) -> str | None:
    """The buttons that must act once per message, even on a double tap. Reading and fixing can repeat."""
    if kind == "eng" and target in ("read", "fix"):
        return None
    return f"{kind} {message_id}"


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query.from_user.id != config.USER_ID:
        return
    try:
        kind, target, action = query.data.split("|", 2)
    except ValueError:
        await _answer_query(query)
        return
    if kind == "now":  # its state file knows what a press already did, even after a restart
        await _now_button(query, context, target, action)
        return
    key = press_key(kind, target, query.message.message_id)
    repeated = key is not None and key in _pressed
    if key and not repeated:
        _keep(_pressed, key)
    if repeated:
        await _answer_query(query, i18n.t("chat.already_done"))
        return
    # A button that is only recorded shows its label for a moment: it may be the only sign that it worked.
    await _answer_query(query, (tapped(query) or None) if kind == "msg" and action in RECORDED else None)
    if kind == "msg":
        await _answer_message(query, context, target, action)
    elif kind == "eng" and target == "read":
        await _read_transcript(query, action)
    elif kind == "eng" and target == "fix":
        await _ask_fix(query, action)
    elif kind == "eng" and target in ("week", "backlog"):
        await _decide_week(query, context, action, target == "week")
    elif kind == "upd":
        await _update_now(query, key)
    elif kind == "intro" and target in ("skip", "stop"):
        await _intro_button(query, target, action)


# --- Now: one task for the free time of the user (see now.py) ---

def _now_schedule(jobs, fid: str, seconds: float) -> None:
    jobs.run_once(_now_check, max(seconds, 1), data=fid, name=f"now {fid}")


async def _now_reply(message, chat, minutes: int) -> None:
    """Answers /now or a message about free time: a task for the minutes, or the question of how much time."""
    if not minutes:
        sent = await message.reply_text(i18n.t("now.ask"), reply_markup=_keyboard(now.time_buttons()))
        await asyncio.to_thread(now.open_round, sent.message_id)
        return
    async with _typing(chat) if chat else contextlib.nullcontext():
        found = await asyncio.to_thread(now.offer, minutes)
    sent = await message.reply_text(found.text, reply_markup=_keyboard(found.buttons))
    await asyncio.to_thread(now.open_round, sent.message_id, minutes, found)
    if found.task:
        await asyncio.to_thread(now.record_pick, sent.message_id, found.text, found.task)


async def _now_button(query, context: ContextTypes.DEFAULT_TYPE, action: str, arg: str) -> None:
    """A `now|` button. "ask", under the morning summary, asks the time in a new message, and it can be tapped
    again later. The other buttons change their own message; now.py makes sure that each press acts once."""
    message, chat = query.message, getattr(query.message, "chat", None)
    if action == "ask":
        await _answer_query(query)
        await _now_reply(message, chat, 0)
        return
    async with _typing(chat) if chat and action in ("min", "next") else contextlib.nullcontext():
        reply = await asyncio.to_thread(now.press, message.message_id, action, arg)
    await _answer_query(query, reply.toast or None)
    if reply.toast is not None:
        return
    await _edit(query, reply.text, _keyboard(reply.buttons))
    if reply.wait is not None:
        _now_schedule(context.job_queue, reply.follow_up, reply.wait)
    if reply.record:
        await asyncio.to_thread(now.record_pick, message.message_id, reply.text, reply.record)


async def _now_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    """When the time of a started task ends: did the user finish it? Never in the quiet hours."""
    fid = context.job.data
    reply = await asyncio.to_thread(now.check, fid)
    if reply is None:
        return
    tg_id = await asyncio.to_thread(telegram_api.send, reply.text, reply.buttons)
    await asyncio.to_thread(now.sent_check, fid, tg_id)


async def _now_restore(jobs) -> None:
    """After a restart, the questions of the started tasks wait again for their time."""
    try:
        for fid, seconds in await asyncio.to_thread(now.waiting):
            _now_schedule(jobs, fid, seconds)
    except Exception:  # noqa: BLE001 - a broken state file must not stop the bot
        log.exception("could not restore the questions of the started tasks")


async def on_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/now, or /now 30 min."""
    minutes = now.parse_minutes(" ".join(getattr(context, "args", None) or []))
    await _now_reply(update.effective_message, update.effective_chat, minutes)


# --- Commands ---

async def on_integrate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if "integrator" in _running:
        _again["integrator"] = agents.integrator
        await message.reply_text(i18n.t("chat.integrate_busy"))
        return
    await message.reply_text(i18n.t("bot.integrating"))
    result = await _in_thread("integrator", agents.integrator, again=True)
    if result is FAILED:
        await message.reply_text(i18n.t("chat.integrate_failed"))
    elif not result:
        await message.reply_text(i18n.t("bot.nothing_pending"))


async def on_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Plans this week again, from today on. The Planner sends the plan itself when it is ready."""
    message = update.effective_message
    if "weekly" in _running:
        await message.reply_text(i18n.t("chat.plan_busy"))
        return
    await message.reply_text(i18n.t("chat.plan_start"))
    if await _in_thread("weekly", agents.plan_again) is FAILED:
        await message.reply_text(i18n.t("chat.plan_failed"))


async def on_retro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_retro(update)


async def on_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """What waits, and the captures Seahorse was not sure about, by their first words."""
    pending, unclear = await asyncio.to_thread(lambda: (vault.engrams("pending"), vault.engrams("unclear")))
    lines = [i18n.t("bot.status", pending=len(pending), unclear=len(unclear))]
    if unclear:
        shown = await asyncio.to_thread(lambda: [first_words(notes.read(p)[1]) for p in unclear[-STATUS_SHOWN:]])
        lines += ["", i18n.t("chat.status_unclear")] + [f"• “{words}”" for words in shown]
        if len(unclear) > STATUS_SHOWN:
            lines.append(i18n.t("chat.status_more", count=len(unclear) - STATUS_SHOWN))
    lines += ["", f"Seahorse {__version__}"]
    for piece in telegram_api.split("\n".join(lines)):
        await update.effective_message.reply_text(piece)


async def on_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    latest = await asyncio.to_thread(updates.latest)
    if latest is None:
        await message.reply_text(i18n.t("chat.update_unknown"))
    elif not latest["version"] or not updates.newer(latest["version"]):
        await message.reply_text(i18n.t("bot.up_to_date", version=__version__))
    else:
        await message.reply_text(i18n.t("bot.updating"))
        await asyncio.to_thread(updates.start_update_in_background)


async def on_intro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    for job in context.job_queue.get_jobs_by_name("intro"):
        job.schedule_removal()
    await asyncio.to_thread(intro.start)


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(i18n.t("bot.start"))


async def on_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(i18n.t("chat.help"))


async def on_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(i18n.t("chat.unknown_command"))


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """One line in the log for a connection problem. For anything else, the whole error in the log and, when it
    came from a message of the user, one short reply: is the message safe, and how to look for the cause."""
    error = context.error
    if isinstance(error, (NetworkError, RetryAfter)) and not isinstance(error, BadRequest):
        log.warning("telegram connection: %s", error)
        return
    if isinstance(error, Conflict):
        log.error("another program uses this bot token: %s", error)
        return
    log.error("an update failed", exc_info=error)
    message, user = getattr(update, "effective_message", None), getattr(update, "effective_user", None)
    if not message or getattr(update, "callback_query", None) or not user or user.id != config.USER_ID:
        return  # a job, a button or a stranger: the log is enough
    if message.message_id in _told:
        return
    _keep(_told, message.message_id)
    key = "chat.error_saved" if message.message_id in _saved else "chat.error_not_saved"
    try:
        await message.reply_text(i18n.t(key))
    except TelegramError:
        log.warning("could not tell the user about the error")


# --- People: the buttons under a birthday or a friend to call ---

async def on_people_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A button under a message about a person, `ppl|<id>|<action>`. Code does what it says, with no AI run: it
    plans the gift or the call in the week, waits, or notes the contact. The message shows what was done under it.
    A double tap does it once. The press never waits for the vault: when an agent holds it, the next Messenger
    run applies the press."""
    from . import people  # imported here: it is only for these buttons
    query = update.callback_query
    if query.from_user.id != config.USER_ID:
        return
    key = f"ppl {query.message.message_id}"
    if key in _pressed:
        await _answer_query(query, i18n.t("chat.already_done"))
        return
    _keep(_pressed, key)
    _, target, action = (query.data.split("|") + ["", ""])[:3]
    done = await asyncio.to_thread(people.press, target, action)
    if done is None:  # an old message, or one that got its answer already
        await _answer_query(query, i18n.t("chat.already_done"))
        return
    await _answer_query(query)
    await _mark_answered(query, done)
    await asyncio.to_thread(people.apply_now)


# --- The process ---

def _at(hhmm: str) -> time:
    hours, minutes = map(int, hhmm.split(":"))
    return time(hours, minutes, tzinfo=config.TIMEZONE)


def schedule(app: Application) -> None:
    """The agents' timetable. Times are in the user's time zone."""
    jobs = app.job_queue
    s = config.SCHEDULE
    for name, interval, first in (("messenger", 600, 90), ("notes", 300, 45)):
        jobs.run_repeating(_job(name, agents.AGENTS[name]), interval, first=first, name=name)
    jobs.run_repeating(_retry, 900, first=120, name="retry")
    jobs.run_daily(_job("morning", agents.morning), _at(s["morning"]), name="morning")
    jobs.run_daily(_job("reviewer", agents.reviewer), _at(s["reviewer"]), name="reviewer")
    jobs.run_daily(_job("weekly", agents.weekly), _at(s["weekly_time"]), days=(WEEKDAYS[s["weekly_day"]],), name="weekly")
    jobs.run_daily(_job("update check", updates.notify_if_new), _at("12:00"), name="update check")
    # The image of the week, when the weekly run came in the quiet hours: it goes out as they end.
    jobs.run_daily(_job("retro", retro.send_held), _at(retro.after_quiet()), name="retro")


async def _post_init(app: Application) -> None:
    """Room for slow work in threads, and the menu of commands that Telegram shows after "/"."""
    # The agents, the answers and the voice notes wait in threads for minutes: the default pool is too small.
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=32, thread_name_prefix="seahorse"))
    try:
        await app.bot.set_my_commands([BotCommand(name, i18n.t(f"chat.menu_{name}")) for name in COMMANDS])
    except TelegramError as error:
        log.warning("could not set the command menu: %s", error)
    await _now_restore(app.job_queue)


def run() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not config.TELEGRAM_TOKEN or not config.USER_ID:
        raise SystemExit("Telegram is not set up. Run `seahorse setup`.")
    if not config.VAULT.exists():
        raise SystemExit(f"No vault at {config.VAULT}. Run `seahorse setup`.")
    only_owner = filters.User(user_id=config.USER_ID)
    new = only_owner & filters.UpdateType.MESSAGE
    app = Application.builder().token(config.TELEGRAM_TOKEN).concurrent_updates(True).post_init(_post_init).build()
    for name, callback in (("start", on_start), ("help", on_help), ("integrate", on_integrate),
                           ("status", on_status), ("plan", on_plan), ("update", on_update), ("intro", on_intro),
                           ("retro", on_retro), ("now", on_now)):
        app.add_handler(CommandHandler(name, callback, filters=new))
    app.add_handler(MessageHandler(new & filters.COMMAND, on_unknown))
    app.add_handler(MessageHandler(only_owner & filters.UpdateType.EDITED_MESSAGE, on_edit))
    app.add_handler(MessageHandler(new & filters.TEXT, on_text))
    app.add_handler(MessageHandler(new & (filters.VOICE | filters.AUDIO | filters.VIDEO_NOTE), on_voice))
    games = filters.Sticker.ALL | filters.ANIMATION | filters.Dice.ALL | filters.GAME | filters.POLL
    app.add_handler(MessageHandler(new & games, on_sticker))  # before files: a GIF is also a document
    app.add_handler(MessageHandler(new & filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(new & filters.Document.ALL, on_file))
    app.add_handler(MessageHandler(new & filters.VIDEO, on_video))
    app.add_handler(MessageHandler(new & (filters.LOCATION | filters.VENUE), on_location))
    app.add_handler(MessageHandler(new & filters.CONTACT, on_contact))
    buttons = r"^(msg|eng|intro|upd|now)\|"  # Seahorse messages, captures, the first conversation, the update, Now
    app.add_handler(CallbackQueryHandler(on_button, pattern=buttons))
    app.add_error_handler(on_error)
    app.add_handler(CallbackQueryHandler(on_people_button, pattern=r"^ppl\|"))  # birthdays and friends to call
    schedule(app)
    log.info("Seahorse %s is running for %s, vault %s, provider %s", __version__, config.USER_NAME, config.VAULT, config.PROVIDER)
    app.run_polling(allowed_updates=Update.ALL_TYPES)
