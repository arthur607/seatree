from test_seahorse import Base, write, task, message, remove  # sets up the throwaway vault
# Tests of the Telegram side: what a message says, what it answers, the first conversation, photos, files, voice
# notes, edits, buttons and errors. Small fakes stand in for Telegram's objects. No test calls an AI or Telegram.
import asyncio
import io
import threading
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from seahorse import bot, config, i18n, intro, llm, messenger, notes, telegram_api, transcribe, updates, vault

TZ = config.TIMEZONE


class Patch:
    """Replaces attributes of an object for one test, and puts them back."""

    def __init__(self, target, **values):
        self.target, self.values, self.old = target, values, {}

    def __enter__(self):
        for name, value in self.values.items():
            self.old[name] = getattr(self.target, name)
            setattr(self.target, name, value)
        return self

    def __exit__(self, *_):
        for name, value in self.old.items():
            setattr(self.target, name, value)


def entity(kind: str, offset: int, length: int, url: str | None = None):
    return SimpleNamespace(type=kind, offset=offset, length=length, url=url)


def jpeg() -> bytes:
    """A small photo with a gradient, as Telegram sends photos: JPEG."""
    from PIL import Image
    image = Image.new("RGB", (320, 240))
    image.putdata([(x % 256, y % 256, (x + y) % 256) for y in range(240) for x in range(320)])
    out = io.BytesIO()
    image.save(out, "JPEG", quality=95)
    return out.getvalue()


class FakeFile:
    def __init__(self, data: bytes):
        self.data = data

    async def download_as_bytearray(self):
        return bytearray(self.data)


def media(data: bytes = b"audio", size: int | None = None, **fields):
    async def get_file():
        return FakeFile(data)
    return SimpleNamespace(file_size=len(data) if size is None else size, get_file=get_file, **fields)


class FakeMessage:
    def __init__(self, message_id: int = 1, text: str | None = None, date: datetime | None = None, **fields):
        self.message_id, self.text = message_id, text
        self.date = date or datetime.now(timezone.utc)
        self.edit_date = None
        self.caption, self.entities, self.caption_entities = None, (), ()
        self.reply_to_message = self.forward_origin = self.media_group_id = None
        self.voice = self.audio = self.video_note = self.document = self.location = self.venue = None
        self.photo = []
        self.reply_markup = None
        self.from_user = SimpleNamespace(id=config.USER_ID, is_bot=False)
        self.replies = []
        for name, value in fields.items():
            setattr(self, name, value)

    async def reply_text(self, text, **options):
        self.replies.append((text, options))
        return SimpleNamespace(message_id=1000 + len(self.replies))

    def texts(self) -> list[str]:
        return [text for text, _ in self.replies]


class FakeJobs:
    def __init__(self):
        self.jobs = []

    def run_once(self, callback, when, data=None, name=None):
        job = SimpleNamespace(callback=callback, when=when, data=data, name=name, removed=False)
        job.schedule_removal = lambda: setattr(job, "removed", True)
        self.jobs.append(job)
        return job

    def get_jobs_by_name(self, name):
        return [j for j in self.jobs if j.name == name and not j.removed]


class FakeBot:
    def __init__(self):
        self.dropped = []

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.dropped.append(message_id)


class FakeChat:
    def __init__(self):
        self.actions = []

    async def send_action(self, action):
        self.actions.append(action)


def context():
    return SimpleNamespace(bot_data={}, job_queue=FakeJobs(), bot=FakeBot(), job=None)


def update(msg: FakeMessage, edited: bool = False):
    return SimpleNamespace(effective_message=msg, effective_chat=FakeChat(), edited_message=msg if edited else None,
                           effective_user=SimpleNamespace(id=config.USER_ID), callback_query=None)


class FakeQuery:
    def __init__(self, data: str, msg: FakeMessage):
        self.data, self.message = data, msg
        self.from_user = SimpleNamespace(id=config.USER_ID)
        self.answers, self.edits, self.options, self.markups = [], [], [], []

    async def answer(self, text=None):
        self.answers.append(text)

    async def edit_message_text(self, text, reply_markup=None, **options):
        self.edits.append((text, reply_markup))
        self.options.append(options)

    async def edit_message_reply_markup(self, reply_markup=None):
        self.markups.append(reply_markup)


def no_ai(*_, **__):
    raise AssertionError("a test must not call the AI")


class TelegramBase(Base, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        for store in (bot._saved, bot._pressed, bot._told, bot._again):
            store.clear()
        bot._running.clear()
        self.sent = []
        self.patches = [Patch(telegram_api, send=lambda text, buttons=None, chat_id=None: self.sent.append((text, buttons)) or 77,
                              edit=lambda *a, **k: True),
                        Patch(llm, run=no_ai)]
        for p in self.patches:
            p.__enter__()

    def tearDown(self):
        for p in reversed(self.patches):
            p.__exit__()

    def engrams(self) -> list:
        return sorted(config.ENGRAMS.rglob("*.md"), key=lambda p: p.stem) if config.ENGRAMS.exists() else []


class TestWhatAMessageSays(unittest.TestCase):
    def test_a_question_mark_inside_a_link_is_not_a_question(self):
        self.assertFalse(bot.looks_like_question("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
        self.assertFalse(bot.looks_like_question("watch later youtube.com/watch?v=abc"))
        self.assertTrue(bot.looks_like_question("is this good? https://example.com/a?b=1"))
        self.assertTrue(bot.looks_like_question("what do I have today"))

    def test_hidden_links_are_kept(self):
        text = "👍 see this video"  # the emoji takes two UTF-16 units
        found = bot.with_hidden_links(text, [entity("text_link", 7, 4, "https://x.org/v?id=1"), entity("bold", 0, 2)])
        self.assertEqual(found, "👍 see this (https://x.org/v?id=1) video")
        self.assertEqual(bot.with_hidden_links("go to https://x.org", [entity("text_link", 6, 13, "https://x.org")]),
                         "go to https://x.org")

    def test_forwarded_messages_say_who_wrote_them(self):
        person = SimpleNamespace(sender_user=SimpleNamespace(full_name="Ana Lima"))
        channel = SimpleNamespace(chat=SimpleNamespace(title="Tech News", full_name=None), author_signature="Bob")
        msg = FakeMessage(text="the meeting moved to 3pm", forward_origin=person)
        self.assertEqual(bot.message_text(msg), "Forwarded from Ana Lima:\nthe meeting moved to 3pm")
        self.assertEqual(bot.origin_name(channel), "Tech News · Bob")
        self.assertEqual(bot.origin_name(SimpleNamespace(sender_user_name="Hidden")), "Hidden")
        photo = FakeMessage(caption="read this", caption_entities=[entity("text_link", 0, 4, "https://a.b/c")])
        self.assertEqual(bot.message_text(photo), "read (https://a.b/c) this")

    def test_first_words_name_a_capture(self):
        self.assertEqual(bot.first_words("buy a gift for Ana before her birthday on Friday"), "buy a gift for Ana before her birthday…")
        self.assertEqual(bot.first_words("📷 Photo\n![[Photo 2026-10-05 1030.webp]]"), "📷 Photo")

    def test_file_names_work_in_links(self):
        when = datetime(2026, 10, 5, 10, 30, tzinfo=TZ)
        self.assertEqual(bot.attachment_name("photo", when, None, ".webp"), "Photo 2026-10-05 1030.webp")
        self.assertEqual(bot.attachment_name("file", when, "Contract [final] #2.pdf", ".pdf"), "Contract final 2.pdf")
        self.assertEqual(bot.attachment_name("file", when, "README", ".txt"), "README.txt")
        self.assertEqual(bot.attachment_name("voice", when, "  ..  ", ".ogg"), "Voice 2026-10-05 1030.ogg")

    def test_places_and_contacts_become_text(self):
        place = SimpleNamespace(latitude=-23.55052, longitude=-46.633308)
        self.assertEqual(bot.location_text(FakeMessage(location=place)),
                         "📍 Location: -23.55052, -46.63331\nhttps://maps.google.com/?q=-23.550520,-46.633308")
        venue = SimpleNamespace(title="Café Girondino", address="Rua Boa Vista 365", location=place)
        self.assertTrue(bot.location_text(FakeMessage(location=place, venue=venue)).startswith("📍 Café Girondino, Rua Boa Vista 365\n"))
        self.assertFalse(bot.looks_like_question(bot.location_text(FakeMessage(location=place))))
        contact = SimpleNamespace(first_name="Ana", last_name=None, phone_number="+55 11 99999-0000")
        self.assertEqual(bot.contact_text(contact), "👤 Contact: Ana · +55 11 99999-0000")

    def test_photos_become_smaller_webp(self):
        data = jpeg()
        webp = bot.as_webp(data)
        self.assertTrue(webp.startswith(b"RIFF") and len(webp) < len(data))
        with self.assertLogs("seahorse.bot", "WARNING"):
            self.assertIsNone(bot.as_webp(b"not an image"))


class TestLongMessages(unittest.TestCase):
    def test_a_long_text_is_cut_between_paragraphs_lines_and_words(self):
        paragraphs = "\n\n".join(f"Paragraph {i}. " + "word " * 150 for i in range(12))
        pieces = telegram_api.split(paragraphs)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(len(p) <= telegram_api.LIMIT for p in pieces))
        self.assertTrue(all(p.startswith("Paragraph") for p in pieces))
        one_line = "word " * 2000
        self.assertEqual(" ".join(telegram_api.split(one_line)).split(), one_line.split())
        self.assertEqual(telegram_api.split("x" * 9000, 4000), ["x" * 4000, "x" * 4000, "x" * 1000])
        self.assertEqual(telegram_api.split("short"), ["short"])


class TestWhatAMessageAnswers(Base):
    def setUp(self):
        super().setUp()
        self.now = datetime(2026, 10, 5, 10, 30, tzinfo=TZ)

    def ask(self, minutes_ago: int, kind: str = "checkin", reaction: str = "pending", id: str = "abc123"):
        return messenger.Message(self.now - timedelta(minutes=minutes_ago), kind, id, "55", "[[_Seahorse]]", reaction)

    def test_a_message_without_reply_answers_the_last_question(self):
        self.assertEqual(bot.last_asking([self.ask(30)], self.now).id, "abc123")
        self.assertIsNone(bot.last_asking([self.ask(150)], self.now))  # more than two hours ago
        self.assertIsNone(bot.last_asking([self.ask(30, kind="reminder")], self.now))
        self.assertIsNone(bot.last_asking([self.ask(30, reaction="ignored")], self.now))
        newest = bot.last_asking([self.ask(60, id="old111"), self.ask(20, kind="question", id="new222")], self.now)
        self.assertEqual(newest.id, "new222")

    def test_after_the_answer_only_the_messages_that_follow_closely(self):
        answered = [self.ask(30, reaction="replied")]
        self.assertIsNone(bot.last_asking(answered, self.now))
        thread = {"id": "abc123", "at": self.now - timedelta(minutes=4)}
        self.assertEqual(bot.last_asking(answered, self.now, thread).id, "abc123")
        thread["at"] = self.now - timedelta(minutes=25)  # a long pause: the user moved on
        self.assertIsNone(bot.last_asking(answered, self.now, thread))


class TestIntroButtons(Base):
    def setUp(self):
        super().setUp()
        self.sent, self.ids = [], iter(range(100, 200))
        self.patch = Patch(telegram_api, send=lambda text, buttons=None, chat_id=None: self.sent.append((text, buttons)) or next(self.ids))
        self.patch.__enter__()

    def tearDown(self):
        self.patch.__exit__()

    def test_a_button_acts_only_on_its_own_question(self):
        intro.start()
        first = intro.current()  # message 100 is the hello, 101 the first question
        self.assertEqual((first["step"], first["message"]), (0, 101))
        self.assertIsNone(intro.advance(1))  # not the question that waits
        self.assertIsNone(intro.advance(0, message=999))  # the same step of an earlier /intro
        self.assertEqual(intro.advance(0, message=101)["message"], 101)
        self.assertIsNone(intro.advance(0, message=101))  # a double press
        self.assertEqual(intro.current()["step"], 1)

    def test_stop_ends_the_conversation(self):
        intro.start()
        self.assertFalse(intro.stop(3))
        self.assertTrue(intro.stop(0))
        self.assertIsNone(intro.current())
        self.assertEqual(self.sent[-1][0], i18n.t("intro.stopped"))
        self.assertIsNone(intro.advance(0))  # an old Skip after the end does not start it again


class TestVaultHelpers(Base):
    def test_attachments_never_overwrite(self):
        first = vault.save_attachment(b"a", "Photo 2026-10-05 1030.webp")
        second = vault.save_attachment(b"b", "Photo 2026-10-05 1030.webp")
        self.assertEqual((first.name, second.name), ("Photo 2026-10-05 1030.webp", "Photo 2026-10-05 1030 2.webp"))
        self.assertEqual(first.parent, config.SYSTEM / "Attachments")
        self.assertEqual(first.read_bytes(), b"a")

    def test_engrams_by_field(self):
        when = datetime(2026, 10, 5, 10, 30, tzinfo=TZ)
        old = vault.save_engram("first", "telegram-text", when, {"telegram_id": "10"})
        new = vault.save_engram("second", "telegram-text", when, {"telegram_id": "10", "corrects": "[[x]]"})
        vault.save_engram("other", "telegram-text", when, {"telegram_id": "11"})
        self.assertEqual(vault.engrams_with("telegram_id", "10"), [new, old])


class TestWaitingVoice(Base):
    def setUp(self):
        super().setUp()
        self.edits = []
        self.patch = Patch(telegram_api, edit=lambda *args: self.edits.append(args) or True)
        self.patch.__enter__()

    def tearDown(self):
        self.patch.__exit__()

    def test_a_voice_note_waits_until_it_becomes_text(self):
        when = datetime(2026, 10, 5, 10, 30, tzinfo=TZ)
        transcribe.wait(b"ogg", "Voice 2026-10-05 1030.ogg", "telegram-voice", when, {"telegram_id": "5"}, 321)
        with Patch(transcribe, transcribe=lambda audio: None):
            self.assertEqual(transcribe.retry_waiting(), 0)  # Groq is still down
        with Patch(transcribe, transcribe=lambda audio: "call the bank tomorrow"):
            self.assertEqual(transcribe.retry_waiting(), 1)
        engram = vault.engrams("pending")[0]
        fm, body = notes.read(engram)
        self.assertEqual((body.strip(), fm["telegram_id"], fm["confirmation"], fm["received"]),
                         ("call the bank tomorrow", "5", "321", "2026-10-05 10:30"))
        self.assertEqual(self.edits[0][0], 321)
        self.assertEqual(transcribe.retry_waiting(), 0)  # nothing waits any more
        self.assertEqual(list(transcribe.WAITING.glob("*.ogg")), [])

    def test_after_some_hours_the_audio_is_kept_as_it_is(self):
        when = datetime(2026, 10, 5, 10, 30, tzinfo=TZ)
        transcribe.wait(b"ogg", "Voice 2026-10-05 1030.ogg", "telegram-voice", when, {}, None)
        with Patch(transcribe, transcribe=lambda audio: None, GIVE_UP_HOURS=0):
            self.assertEqual(transcribe.retry_waiting(), 1)
        body = notes.read(vault.engrams("pending")[0])[1]
        self.assertEqual(body.strip(), transcribe.without_text("Voice 2026-10-05 1030.ogg"))
        self.assertTrue((vault.attachments() / "Voice 2026-10-05 1030.ogg").exists())


class TestJobs(TelegramBase):
    async def test_a_skipped_run_comes_back_right_after(self):
        started, release, runs = threading.Event(), threading.Event(), []

        def slow():
            runs.append("slow")
            started.set()
            release.wait(5)
            return "first run"

        def late():
            runs.append("late")
            return None

        first = asyncio.create_task(bot._in_thread("integrator", slow, again=True))
        await asyncio.to_thread(started.wait, 5)
        self.assertIs(await bot._in_thread("integrator", late, again=True), bot.BUSY)
        release.set()
        self.assertEqual(await first, "first run")
        self.assertEqual(runs, ["slow", "late"])
        self.assertNotIn("integrator", bot._running)

    async def test_integrate_while_it_runs_answers_at_once(self):
        bot._running.add("integrator")
        msg = FakeMessage(text="/integrate")
        await bot.on_integrate(update(msg), context())
        self.assertEqual(msg.texts(), [i18n.t("chat.integrate_busy")])
        self.assertIn("integrator", bot._again)


class TestCaptures(TelegramBase):
    async def test_a_capture_is_saved_and_confirmed(self):
        msg, ctx = FakeMessage(42, "pay the car tax by the 15th"), context()
        engram = await bot._capture(update(msg), ctx, msg.text, "telegram-text")
        fm, body = notes.read(engram)
        self.assertEqual((body.strip(), fm["telegram_id"], fm["confirmation"]), ("pay the car tax by the 15th", "42", "1001"))
        self.assertEqual(msg.texts(), ["✓ Saved. Integrating…"])
        self.assertEqual(len(ctx.job_queue.get_jobs_by_name("integrator")), 1)
        self.assertIn(42, bot._saved)

    async def test_a_question_gets_an_answer_and_no_saved_message(self):
        calls = []

        def answer(text, role, write=False, session=None, max_turns=80, timeout=3600, web=False):
            calls.append((role, web))
            return llm.Answer("November 4, at 9am.", "s1", False)

        msg, ctx = FakeMessage(43, "when is the eye doctor?"), context()
        with Patch(bot, _is_question=lambda text, about="": True), Patch(llm, run=answer):
            engram = await bot._capture(update(msg), ctx, msg.text, "telegram-text")
        self.assertEqual(notes.read(engram)[0]["asked"], "question")
        self.assertEqual(msg.texts(), ["November 4, at 9am."])
        self.assertEqual(calls, [("question", True)])  # the answer may search the web
        self.assertEqual(ctx.bot_data["session"], "s1")

    async def test_a_voice_question_shows_what_was_heard(self):
        msg = FakeMessage(44)
        with Patch(bot, _is_question=lambda text, about="": True), \
                Patch(llm, run=lambda *a, **k: llm.Answer("At 9am.", None, False)):
            await bot._capture(update(msg), context(), "when is the dentist", "telegram-voice")
        self.assertEqual(msg.replies[0], ("🎤 <i>“when is the dentist”</i>", {"parse_mode": "HTML"}))
        self.assertEqual(msg.texts()[1], "At 9am.")

    async def test_an_answer_typed_without_reply_goes_to_the_checkin(self):
        messenger.record("checkin", "[[_Seahorse]]", "How was your Sunday?", 55, "abc123")
        telegram_api.remember(55, "How was your Sunday? Did you go to the beach?")
        msg, ctx = FakeMessage(45, "it was great, we went to the beach"), context()
        engram = await bot._capture(update(msg), ctx, msg.text, "telegram-text")
        fm = notes.read(engram)[0]
        self.assertEqual((fm["message_id"], fm["in_reply_to"]), ("abc123", "How was your Sunday? Did you go to the beach?"))
        self.assertEqual(ctx.bot.dropped, [55])
        self.assertEqual(ctx.bot_data["thread"]["id"], "abc123")

    async def test_a_question_is_not_taken_as_the_answer_to_the_checkin(self):
        messenger.record("checkin", "[[_Seahorse]]", "How was your Sunday?", 55, "abc123")
        msg = FakeMessage(46, "what is on my week?")
        with Patch(bot, _is_question=lambda text, about="": True), \
                Patch(llm, run=lambda *a, **k: llm.Answer("Two tasks.", None, False)):
            engram = await bot._capture(update(msg), context(), msg.text, "telegram-text")
        self.assertNotIn("message_id", notes.read(engram)[0])

    async def test_a_reply_keeps_its_link_even_on_a_question(self):
        messenger.record("recommendation", "[[Tastes]]", "Dune Part Two is out. Want it on your list?", 56, "rec456")
        original = FakeMessage(56, "Dune Part Two is out. Want it on your list?", from_user=SimpleNamespace(is_bot=True))
        msg, seen = FakeMessage(47, "is it long?", reply_to_message=original), []
        with Patch(bot, _is_question=lambda text, about="": seen.append(about) or True), \
                Patch(llm, run=lambda *a, **k: llm.Answer("2h46.", None, False)):
            engram = await bot._capture(update(msg), context(), msg.text, "telegram-text")
        fm = notes.read(engram)[0]
        self.assertEqual((fm["message_id"], fm["asked"]), ("rec456", "question"))
        self.assertEqual(seen, ["Dune Part Two is out. Want it on your list?"])

    async def test_a_reply_to_the_summary_keeps_its_whole_text(self):
        summary = "Good morning. " + "A line of the day. " * 40 + "Do you want to move the gym to Friday?"
        messenger.record("summary", "[[_Seahorse]]", summary, 57, "sum789")
        original = FakeMessage(57, summary, from_user=SimpleNamespace(is_bot=True))
        msg = FakeMessage(48, "yes, move it", reply_to_message=original)
        engram = await bot._capture(update(msg), context(), msg.text, "telegram-text")
        self.assertTrue(notes.read(engram)[0]["in_reply_to"].endswith("move the gym to Friday?"))


class TestFirstConversation(TelegramBase):
    async def test_one_answer_in_several_messages_moves_one_question(self):
        intro.start()
        ctx = context()
        for n, text in enumerate(["I am a developer", "at a bank", "and I study music"]):
            engram = await bot._capture(update(FakeMessage(50 + n, text)), ctx, text, "telegram-text")
            self.assertEqual(notes.read(engram)[0]["in_reply_to"], intro.questions()[0])
        jobs = ctx.job_queue.get_jobs_by_name("intro")
        self.assertEqual([j.data for j in jobs], [0])  # one pending move, pushed back by each message
        ctx.job = jobs[0]
        await bot._intro_next(ctx)
        self.assertEqual(intro.current()["step"], 1)
        self.assertEqual(ctx.bot.dropped, [77])  # the buttons of the answered question go away

    async def test_a_question_during_the_intro_is_answered_and_moves_nothing(self):
        intro.start()
        ctx, msg = context(), FakeMessage(60, "what is this for?")
        with Patch(bot, _is_question=lambda text, about="": True), \
                Patch(llm, run=lambda *a, **k: llm.Answer("I keep your notes.", None, False)):
            engram = await bot._capture(update(msg), ctx, msg.text, "telegram-text")
        self.assertEqual(msg.texts(), ["I keep your notes."])
        self.assertNotIn("in_reply_to", notes.read(engram)[0])
        self.assertEqual(ctx.job_queue.get_jobs_by_name("intro"), [])

    async def test_skip_on_an_old_question_does_nothing(self):
        intro.start()
        await bot._intro_button(FakeQuery("intro|skip|0", FakeMessage(77, "1/6\nWhat do you do?")), "skip", "0")
        self.assertEqual(intro.current()["step"], 1)
        old = FakeQuery("intro|skip|0", FakeMessage(77, "1/6\nWhat do you do?"))
        await bot._intro_button(old, "skip", "0")
        self.assertEqual(intro.current()["step"], 1)
        self.assertEqual(old.edits, [("1/6\nWhat do you do?", None)])  # only its buttons go away


class TestEdits(TelegramBase):
    async def test_an_edit_becomes_a_correction(self):
        ctx = context()
        original = await bot._capture(update(FakeMessage(70, "dentist on Tuesday")), ctx, "dentist on Tuesday", "telegram-text")
        edited = FakeMessage(70, "dentist on Thursday", edit_date=datetime.now(timezone.utc))
        await bot.on_edit(update(edited, edited=True), ctx)
        correction = [p for p in self.engrams() if p != original][0]
        fm, body = notes.read(correction)
        self.assertEqual((fm["corrects"], body.strip()), (f"[[{original.stem}]]", "dentist on Thursday"))
        self.assertEqual(edited.texts(), ["✓ Correction saved. Integrating…"])
        again = FakeMessage(70, "dentist on Friday", edit_date=datetime.now(timezone.utc))
        await bot.on_edit(update(again, edited=True), ctx)
        self.assertEqual(notes.read(self.engrams()[-1])[0]["corrects"], f"[[{original.stem}]]")  # always the original

    async def test_an_edited_correction_corrects_the_same_capture(self):
        ctx = context()
        original = await bot._capture(update(FakeMessage(74, "gym on Monday")), ctx, "gym on Monday", "telegram-text")
        await bot._capture(update(FakeMessage(75, "on Tuesday")), ctx, "on Tuesday", "telegram-text",
                           {"corrects": f"[[{original.stem}]]"})  # the reply to "What should I change?"
        edited = FakeMessage(75, "on Wednesday", edit_date=datetime.now(timezone.utc))
        await bot.on_edit(update(edited, edited=True), ctx)
        self.assertEqual(notes.read(self.engrams()[-1])[0]["corrects"], f"[[{original.stem}]]")
        self.assertEqual(edited.texts(), ["✓ Correction saved. Integrating…"])

    async def test_old_or_unknown_messages(self):
        old = FakeMessage(71, "x", date=datetime.now(timezone.utc) - timedelta(days=3))
        await bot.on_edit(update(old, edited=True), context())
        self.assertEqual(old.texts(), [i18n.t("chat.edit_too_old")])
        unknown = FakeMessage(72, "y")
        await bot.on_edit(update(unknown, edited=True), context())
        self.assertEqual(unknown.texts(), [i18n.t("chat.edit_unknown")])
        moving = FakeMessage(73, None, location=SimpleNamespace(latitude=1, longitude=2))
        await bot.on_edit(update(moving, edited=True), context())  # a live location: silence
        self.assertEqual((moving.texts(), self.engrams()), ([], []))


class TestMedia(TelegramBase):
    async def test_a_photo_is_kept_with_its_caption(self):
        msg = FakeMessage(80, photo=[media(b"small"), media(jpeg())], caption="the new office")
        await bot.on_photo(update(msg), context())
        name = "Photo " + msg.date.astimezone(TZ).strftime("%Y-%m-%d %H%M") + ".webp"
        self.assertTrue((vault.attachments() / name).exists())
        fm, body = notes.read(self.engrams()[0])
        self.assertEqual((fm["channel"], body.strip()), ("telegram-photo", f"the new office\n![[{name}]]"))
        self.assertEqual(msg.texts(), [telegram_api.capture_text("telegram-photo", False, None)])

    async def test_a_file_too_big_is_explained(self):
        msg = FakeMessage(81, document=media(b"x", size=35 * 2 ** 20, file_name="Talk.pdf", mime_type="application/pdf"))
        await bot.on_file(update(msg), context())
        self.assertEqual(msg.texts(), [i18n.t("chat.file_too_big", size=35, limit=10)])
        self.assertEqual(self.engrams(), [])

    async def test_an_album_is_one_capture(self):
        ctx = context()
        for n in range(3):
            msg = FakeMessage(90 + n, photo=[media(jpeg())], media_group_id="g1", caption="trip to Rio" if n == 0 else None)
            await bot.on_photo(update(msg), ctx)
        self.assertEqual(self.engrams(), [])  # it waits for the whole album
        ctx.job = ctx.job_queue.get_jobs_by_name("album g1")[0]
        await bot._album_done(ctx)
        engrams = self.engrams()
        self.assertEqual(len(engrams), 1)
        body = notes.read(engrams[0])[1]
        self.assertTrue(body.startswith("trip to Rio\n![[Photo "))
        self.assertEqual(body.count("![["), 3)

    async def test_stickers_get_a_word_and_no_capture(self):
        msg = FakeMessage(95)
        await bot.on_sticker(update(msg), context())
        self.assertEqual(msg.texts(), [i18n.t("chat.sticker")])
        self.assertEqual(self.engrams(), [])

    async def test_a_location_is_a_capture(self):
        msg = FakeMessage(96, location=SimpleNamespace(latitude=-22.9, longitude=-43.2))
        await bot.on_location(update(msg), context())
        self.assertEqual(notes.read(self.engrams()[0])[0]["channel"], "telegram-location")


class TestVoice(TelegramBase):
    async def test_with_voice_off_the_audio_is_kept(self):
        msg = FakeMessage(100, voice=media(b"ogg data"))
        with Patch(transcribe, available=lambda: False):
            await bot.on_voice(update(msg), context())
        self.assertEqual(msg.texts()[0], i18n.t("chat.voice_off"))
        body = notes.read(self.engrams()[0])[1]
        name = "Voice " + msg.date.astimezone(TZ).strftime("%Y-%m-%d %H%M") + ".ogg"
        self.assertEqual(body.strip(), transcribe.without_text(name))
        self.assertEqual((vault.attachments() / name).read_bytes(), b"ogg data")

    async def test_a_failed_transcription_waits_for_the_retry(self):
        msg = FakeMessage(101, voice=media(b"ogg data"))
        with Patch(transcribe, available=lambda: True, transcribe=lambda audio: None):
            await bot.on_voice(update(msg), context())
        self.assertEqual(msg.texts(), [i18n.t("chat.voice_later")])
        self.assertEqual(self.engrams(), [])
        self.assertEqual(len(list(transcribe.WAITING.glob("*.ogg"))), 1)

    async def test_a_voice_note_is_a_capture(self):
        msg = FakeMessage(102, voice=media(b"ogg data"))
        with Patch(transcribe, available=lambda: True, transcribe=lambda audio: "buy chocolate"):
            await bot.on_voice(update(msg), context())
        self.assertEqual(msg.texts(), ["✓ Voice note saved. Integrating…"])
        self.assertEqual(notes.read(self.engrams()[0])[1].strip(), "buy chocolate")


class TestButtons(TelegramBase):
    def reminder(self):
        msg = FakeMessage(110, "⏰ Pay the car tax\nDue tomorrow, 06/10")
        msg.reply_markup = SimpleNamespace(inline_keyboard=[[SimpleNamespace(text="✅ Done", callback_data="msg|abc123|done")]])
        return msg

    async def test_a_double_tap_does_the_work_once(self):
        msg, ctx = self.reminder(), context()
        first, second = FakeQuery("msg|abc123|done", msg), FakeQuery("msg|abc123|done", msg)
        await bot.on_button(SimpleNamespace(callback_query=first), ctx)
        await bot.on_button(SimpleNamespace(callback_query=second), ctx)
        self.assertEqual(len(self.engrams()), 1)
        self.assertEqual(msg.texts(), ["⏳ Done…"])  # the notice that the Integrator turns into what it did
        self.assertEqual((first.answers, second.answers), ([None], [i18n.t("chat.already_done")]))
        fm, body = notes.read(self.engrams()[0])
        self.assertEqual((body.strip(), fm["confirmation"], fm["message_id"]), ("done", "1001", "abc123"))
        self.assertEqual(first.edits[0][0], "⏰ Pay the car tax\nDue tomorrow, 06/10\n\n→ ✅ Done")

    def delivery(self, text: str = "Developer salaries in 2026"):
        """A research delivery: HTML text, a link button and two buttons that are only recorded."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        msg = FakeMessage(112, text, text_html=f"<b>{text}</b>")
        msg.reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📖 Open in Obsidian", url="https://example.com/open")],
            [InlineKeyboardButton("👍 Thanks", callback_data="msg|res123|useful"),
             InlineKeyboardButton("👎 Not useful", callback_data="msg|res123|ign")]])
        return msg

    async def test_a_delivery_keeps_its_format_and_its_link(self):
        reactions = []
        for action, label, reaction in (("ign", "👎 Not useful", "ignored"), ("useful", "👍 Thanks", "acted")):
            query = FakeQuery(f"msg|res123|{action}", self.delivery())
            bot._pressed.clear()
            with Patch(messenger, set_reaction=lambda message_id, value: reactions.append((message_id, value))):
                await bot.on_button(SimpleNamespace(callback_query=query), context())
            text, markup = query.edits[0]
            self.assertEqual(text, f"<b>Developer salaries in 2026</b>\n\n→ {label}")  # the tapped label, not "Ignore"
            self.assertEqual(query.options[0]["parse_mode"], "HTML")
            self.assertEqual([b.text for row in markup.inline_keyboard for b in row], ["📖 Open in Obsidian"])
            self.assertEqual(query.answers, [label])
        self.assertEqual(reactions, [("res123", "ignored"), ("res123", "acted")])
        self.assertEqual(self.engrams(), [])  # only recorded: no engram, no Integrator run

    async def test_a_long_delivery_only_loses_its_buttons(self):
        query = FakeQuery("msg|res123|useful", self.delivery("x" * 4094))
        with Patch(messenger, set_reaction=lambda message_id, value: None):
            await bot.on_button(SimpleNamespace(callback_query=query), context())
        self.assertEqual(query.edits, [])
        self.assertEqual([b.text for row in query.markups[0].inline_keyboard for b in row], ["📖 Open in Obsidian"])

    async def test_an_old_update_button_checks_first(self):
        msg = FakeMessage(111, "🆕 Seahorse 1.0.0 is out.")
        for latest, expected in ((None, None), ({"version": "0.0.1", "notes": ""}, "up_to_date")):
            query = FakeQuery("upd|now|", msg)
            with Patch(updates, latest=lambda found=latest: found, start_update_in_background=no_ai):
                await bot.on_button(SimpleNamespace(callback_query=query), context())
            if expected is None:
                self.assertEqual(msg.texts()[-1], i18n.t("chat.update_unknown"))
                self.assertNotIn("upd 111", bot._pressed)  # it can be pressed again
            else:
                self.assertIn(i18n.t("bot.up_to_date", version=bot.__version__), query.edits[0][0])

    async def test_the_fix_question_names_the_capture_by_its_words(self):
        ctx = context()
        engram = await bot._capture(update(FakeMessage(120, "lunch with Ana on Friday")), ctx, "lunch with Ana on Friday", "telegram-text")
        query = FakeQuery(f"eng|fix|{engram.stem}", FakeMessage(1001, "✓ Saved\n→ Task"))
        await bot.on_button(SimpleNamespace(callback_query=query), ctx)
        self.assertEqual(query.message.texts()[0], i18n.t("chat.fix_ask", words="lunch with Ana on Friday"))
        self.assertNotIn(engram.stem, query.message.texts()[0])
        prompt = FakeMessage(1001, query.message.texts()[0], from_user=SimpleNamespace(is_bot=True))
        reply = FakeMessage(121, "it is on Saturday", reply_to_message=prompt)
        self.assertEqual((await bot._reply_link(update(reply))).fields, {"corrects": f"[[{engram.stem}]]"})
        legacy = FakeMessage(5, i18n.t("bot.fix_ask", name="2026-10-01 0800 2"), from_user=SimpleNamespace(is_bot=True))
        self.assertEqual((await bot._reply_link(update(FakeMessage(122, "x", reply_to_message=legacy)))).fields,
                         {"corrects": "[[2026-10-01 0800 2]]"})

    async def test_a_long_transcript_comes_in_pieces(self):
        when = datetime(2026, 10, 5, 10, 30, tzinfo=TZ)
        engram = vault.save_engram("word " * 2000, "telegram-voice", when)
        query = FakeQuery(f"eng|read|{engram.stem}", FakeMessage(130, "✓ Voice note saved"))
        await bot.on_button(SimpleNamespace(callback_query=query), context())
        self.assertGreater(len(query.message.replies), 1)
        self.assertTrue(all(len(text) <= 4096 for text in query.message.texts()))


class TestCommandsAndErrors(TelegramBase):
    async def test_status_lists_what_was_unclear(self):
        when = datetime(2026, 10, 5, 10, 30, tzinfo=TZ)
        path = vault.save_engram("the thing with Marcos next week, call him", "telegram-text", when)
        notes.set_field(path, "state", "unclear")
        msg = FakeMessage(140, "/status")
        await bot.on_status(update(msg), context())
        text = msg.texts()[0]
        self.assertIn(i18n.t("chat.status_unclear"), text)
        self.assertIn("• “the thing with Marcos next week, call him”", text)

    async def test_help_unknown_and_update_without_github(self):
        msg = FakeMessage(141)
        await bot.on_help(update(msg), context())
        await bot.on_unknown(update(msg), context())
        with Patch(updates, latest=lambda: None, start_update_in_background=no_ai):
            await bot.on_update(update(msg), context())
        self.assertEqual(msg.texts(), [i18n.t("chat.help"), i18n.t("chat.unknown_command"), i18n.t("chat.update_unknown")])
        self.assertNotIn("engram", i18n.t("bot.start").lower())
        self.assertEqual(len(bot.COMMANDS), len({i18n.t(f"chat.menu_{name}") for name in bot.COMMANDS}))

    async def test_an_error_says_once_if_the_message_is_safe(self):
        from telegram.error import NetworkError
        saved, lost = FakeMessage(150, "x"), FakeMessage(151, "y")
        bot._keep(bot._saved, 150, "2026-10-05 1030")
        broken, quiet = SimpleNamespace(error=RuntimeError("boom")), FakeMessage(152, "z")
        with self.assertLogs("seahorse.bot", "WARNING") as logs:
            for msg in (saved, saved, lost):
                await bot.on_error(update(msg), broken)
            await bot.on_error(update(quiet), SimpleNamespace(error=NetworkError("connection reset")))
        self.assertEqual(saved.texts(), [i18n.t("chat.error_saved")])
        self.assertEqual(lost.texts(), [i18n.t("chat.error_not_saved")])
        self.assertEqual(quiet.texts(), [])
        self.assertEqual(logs.output[-1], "WARNING:seahorse.bot:telegram connection: connection reset")  # one line

    def test_buttons_that_act_once(self):
        self.assertEqual(bot.press_key("msg", "abc", 5), "msg 5")
        self.assertIsNone(bot.press_key("eng", "read", 5))
        self.assertEqual(bot.press_key("eng", "week", 5), bot.press_key("eng", "backlog", 5))


if __name__ == "__main__":
    unittest.main()
