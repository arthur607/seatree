"""Tests of the Messenger and the Companion: readable deliveries, the reminder rules, the daily limit, reactions
without the lock, the Companion's model outside the lock, sources, and the 1.3.0 migration. They never call an AI
or Telegram: both are fakes here.

Usage: python -m unittest discover -s tests
"""
from test_seahorse import Base, write, task, message, remove  # sets up the throwaway vault

import json
import re
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from seahorse import companion, config, i18n, llm, messenger, migrations, notes, telegram_api, vault, week

TZ = config.TIMEZONE
DOCS = Path(__file__).resolve().parent.parent / "docs"


def lines(*records: str) -> None:
    """Messages.md with these record lines."""
    write(config.MESSAGES, "---\ntype: log\n---\n" + "".join(f"{r}\n" for r in records))


def reminder(when: str, stage: str, reaction: str, name: str = "Pay the bill", message_id: str = "a1b2c3") -> str:
    return f"- {when} · reminder · id:{message_id} · tg:1 · [[{name}]] · reaction: {reaction} · {stage} · ⏰ {name}"


@contextmanager
def setting(name: str, value):
    """A setting of config for one test. The old value, or its absence, comes back after."""
    missing = object()
    old = getattr(config, name, missing)
    setattr(config, name, value)
    try:
        yield
    finally:
        if old is missing:
            delattr(config, name)
        else:
            setattr(config, name, old)


def plain_task(name: str, due: date | None = None, subject: str = "[[Home]]") -> Path:
    """A task note with its own subject, unlike `task`, whose subject is always [[Car]]."""
    path = config.TASKS / f"{name}.md"
    write(path, f"---\ntype: task\ndone: false\nestimate: 15m\n" + (f"due: {due}\n" if due else "")
          + f"subject: \"{subject}\"\n---\n")
    return path


class Fakes(Base):
    """Telegram, git, the vault lock, the clock and the AI are fakes. `self.sent` has the body of each Telegram call;
    `self.calls` has (role, lock held) for each AI run."""

    def setUp(self):
        super().setUp()
        self.sent, self.calls, self.answers, self.held = [], [], [], False
        self.moment = datetime(2026, 10, 5, 13, 0, tzinfo=TZ)
        self.saved = [(telegram_api, "_call", telegram_api._call), (vault, "lock", vault.lock),
                      (vault, "pull", vault.pull), (vault, "publish", vault.publish), (llm, "run", llm.run),
                      (messenger, "now", messenger.now), (week, "reschedule", week.reschedule)]
        telegram_api._call = self.fake_call
        vault.lock = self.fake_lock
        vault.pull = lambda: None
        vault.publish = lambda text: True
        llm.run = self.fake_ai
        messenger.now = lambda: self.moment
        week.reschedule = lambda today=None: []
        lines()

    def tearDown(self):
        for module, name, value in self.saved:
            setattr(module, name, value)

    def fake_call(self, method: str, body: dict) -> dict:
        self.sent.append(dict(body, method=method))
        return {"message_id": 100 + len(self.sent)}

    @contextmanager
    def fake_lock(self, wait: bool = True):
        self.held = True
        try:
            yield
        finally:
            self.held = False

    def fake_ai(self, text: str, role: str, *args, **kwargs) -> llm.Answer:
        self.calls.append((role, self.held))
        return llm.Answer(self.answers.pop(0) if self.answers else "", None, False)

    def plan(self, **fields) -> None:
        base = {"day": f"{self.moment:%Y-%m-%d}", "times": [], "used": 0, "checkin": "09:00", "checkin_done": True}
        write(config.COMPANION_STATE, json.dumps(base | fields))


class TestReadableText(Fakes):
    NOTE = ("---\ntype: knowledge\nsource: \"[[2026-10-21 1432]]\"\n---\n# Torterra cards\n## Current\n"
            "Bundles cost **R$ 40** & up <in Brazil> ^[[2026-10-21 1432]]\n"
            "- Best at [[Liga Pokémon|Liga]], see [eBay](https://www.ebay.com/sch?q=torterra&x=1)\n"
            "- [[3. 📚 Knowledge/Misc/Card care]]\n## History\n- 2026-10-20 · before: nothing\n")

    def test_a_note_becomes_telegram_html(self):
        found = telegram_api.note_html(self.NOTE)
        self.assertEqual(found, "<b>Torterra cards</b>\n"
                                "Bundles cost <b>R$ 40</b> &amp; up &lt;in Brazil&gt;\n"
                                '• Best at Liga, see <a href="https://www.ebay.com/sch?q=torterra&amp;x=1">eBay</a>\n'
                                "• Card care")

    def test_long_html_is_cut_between_paragraphs_and_tags_close(self):
        paragraphs = "\n\n".join(f"Paragraph {i}. " + "word " * 150 for i in range(12))
        pieces = telegram_api.split_html(f"<b>Title</b>\n\n<blockquote expandable>{paragraphs}</blockquote>\n\nend")
        self.assertGreater(len(pieces), 1)
        for piece in pieces:
            self.assertLessEqual(len(piece), 4096)
            self.assertEqual(piece.count("<blockquote expandable>"), piece.count("</blockquote>"), piece[-80:])
            self.assertTrue(piece.startswith(("<b>Title", "<blockquote expandable>Paragraph")), piece[:40])
        self.assertTrue(pieces[-1].endswith("</blockquote>\n\nend"))

    def test_buttons_and_links_go_under_the_last_piece(self):
        telegram_api.send_html("a\n" + "b " * 3000, [("👍 Thanks", "msg|x|useful")], [("📖 Open", "https://example.com")])
        self.assertEqual([len(body["text"]) for body in self.sent], [1, 3799, 2199])  # a line, then the words that fit
        self.assertNotIn("reply_markup", self.sent[0])
        self.assertNotIn("reply_markup", self.sent[1])
        self.assertEqual(self.sent[2]["reply_markup"]["inline_keyboard"],
                         [[{"text": "📖 Open", "url": "https://example.com"}], [{"text": "👍 Thanks", "callback_data": "msg|x|useful"}]])
        self.assertTrue(all(body["parse_mode"] == "HTML" for body in self.sent))

    def test_html_that_telegram_refuses_goes_as_plain_text(self):
        def refuse_html(method, body):
            self.sent.append(body)
            return None if "parse_mode" in body else {"message_id": 7}
        telegram_api._call = refuse_html
        self.assertEqual(telegram_api.send_html("<b>Bold</b> &amp; more"), 7)
        self.assertEqual(self.sent[-1]["text"], "Bold & more")


class TestDeliveries(Fakes):
    def setUp(self):
        super().setUp()
        self.note = config.VAULT / "3. 📚 Knowledge" / "Misc" / "Torterra cards.md"
        write(self.note, TestReadableText.NOTE)
        write(config.QUEUE, "---\ntype: log\n---\n"
                            "- [ ] 2026-10-05 10:00 · delivery · high · Found three [[Torterra cards|bundles]] under R$ 50. · [[Torterra cards]]\n")

    def test_a_delivery_is_the_note_as_text_with_an_obsidian_button(self):
        self.assertEqual(messenger._deliver(self.moment), "delivery about [[Torterra cards]]")
        self.assertEqual([body["method"] for body in self.sent], ["sendMessage"])  # no file to download
        body = self.sent[0]
        self.assertEqual(body["parse_mode"], "HTML")
        self.assertTrue(body["text"].startswith("Found three bundles under R$ 50.\n\n<b>Torterra cards</b>"))
        self.assertNotIn("type: knowledge", body["text"])
        self.assertTrue(body["text"].endswith("\n\n📁 Knowledge › Misc › Torterra cards"))
        (open_button,), answers = body["reply_markup"]["inline_keyboard"]
        self.assertEqual(open_button["text"], "📖 Open in Obsidian")
        link = urlparse(open_button["url"])
        self.assertEqual(f"{link.scheme}://{link.netloc}{link.path}", messenger.OPEN_PAGE)
        name = getattr(config, "OBSIDIAN_VAULT", None) or config.VAULT.name
        self.assertEqual(parse_qs(link.query), {"vault": [name], "file": ["3. 📚 Knowledge/Misc/Torterra cards"]})
        self.assertEqual([b["text"] for b in answers], ["👍 Thanks", "👎 Not useful"])
        self.assertTrue(answers[0]["callback_data"].endswith("|useful") and answers[1]["callback_data"].endswith("|ign"))
        self.assertEqual([(m.kind, m.source) for m in messenger.messages()], [("delivery", "[[Torterra cards]]")])
        self.assertIn("- [x] 2026-10-05 10:00 · delivery", config.QUEUE.read_text(encoding="utf-8"))

    def test_the_vault_name_for_obsidian_comes_from_the_settings(self):
        with setting("OBSIDIAN_VAULT", "My Brain"):
            query = parse_qs(urlparse(messenger.obsidian_link(self.note)).query)
        self.assertEqual(query["vault"], ["My Brain"])

    def test_a_long_note_goes_folded(self):
        write(self.note, "---\ntype: knowledge\n---\n" + "\n".join(f"- point {i} " + "x" * 60 for i in range(40)))
        text = messenger.delivery_html("Summary.", self.note)
        self.assertIn("\n\n<blockquote expandable>• point 0", text)
        self.assertIn("</blockquote>\n\n📁 Knowledge", text)

    def test_the_open_page_has_no_outside_files(self):
        page = (DOCS / "open.html").read_text(encoding="utf-8")
        self.assertIn('"obsidian://open?vault=" + encodeURIComponent(vault) + "&file=" + encodeURIComponent(file)', page)
        self.assertIsNone(re.search(r'(src|href)="(https?:)?//', page))
        self.assertTrue(messenger.OPEN_PAGE.endswith("/open.html"))


class TestReminderRules(Fakes):
    def test_no_reminder_while_the_morning_summary_covers_it(self):
        plain_task("Pay the bill", self.today)
        summary = message(datetime(2026, 10, 5, 8, 0, tzinfo=TZ), "summary", "[[_Seahorse]]", "—")
        self.assertIsNone(messenger.next_reminder(datetime(2026, 10, 5, 11, 50, tzinfo=TZ), [summary]))
        found = messenger.next_reminder(datetime(2026, 10, 5, 12, 10, tzinfo=TZ), [summary])
        self.assertEqual((found.source, found.stage), ("[[Pay the bill]]", "due-today"))
        # Before the summary comes, reminders wait for it, up to an hour after its time.
        self.assertIsNone(messenger.next_reminder(datetime(2026, 10, 5, 8, 30, tzinfo=TZ), []))
        self.assertIsNotNone(messenger.next_reminder(datetime(2026, 10, 5, 9, 10, tzinfo=TZ), []))

    def test_a_reminder_records_its_kind(self):
        plain_task("Pay the bill", self.today + timedelta(days=1))
        self.assertEqual(messenger._remind(self.moment), "reminder about [[Pay the bill]]")
        sent = messenger.messages()[0]
        self.assertTrue(sent.text.startswith("day-before · ⏰ Pay the bill Due tomorrow, 10/06"), sent.text)
        self.assertEqual(messenger.reminder_stage(sent), "day-before")

    def test_old_reminders_get_their_kind_from_their_words(self):
        def stage(text: str) -> str | None:
            return messenger.reminder_stage(messenger.Message(self.moment, "reminder", "x", "1", "[[X]]", "ignored", text))
        self.assertEqual(stage("⏰ Pay the bill Due tomorrow, 08/10 · 15m"), "day-before")
        self.assertEqual(stage("⏰ Pay the bill Due today, 08/10"), "due-today")
        self.assertEqual(stage("⏰ Pay the bill Due Thursday, 08/10 · 15m"), "week-before")
        self.assertEqual(stage("week-before · ⏰ Pay the bill Due tomorrow"), "week-before")
        self.assertIsNone(stage("something else"))

    def test_three_ignored_reminders_of_a_kind_stop_it_until_the_user_acts(self):
        lines(reminder("2026-09-28 13:00", "day-before", "ignored"), reminder("2026-10-01 13:00", "day-before", "ignored"),
              "- 2026-10-02 13:00 · reminder · id:old123 · tg:1 · [[Old]] · reaction: ignored · ⏰ Old Due tomorrow, 10/03 · 15m")
        write(config.CALIBRATION, "---\ntype: summary\n---\n## Muted topics\nNone.\n")
        self.assertEqual(messenger.calibrate_reminders(self.moment), ["day-before reminders stop: the last 3 were ignored"])
        text = config.CALIBRATION.read_text(encoding="utf-8")
        self.assertIn("## Stopped reminders\nCode writes this part.", text)
        self.assertTrue(text.endswith("\n- day-before · since 2026-10-05 13:00 · the last 3 were ignored\n"), text)
        plain_task("Pay tomorrow", self.today + timedelta(days=1))
        self.assertIsNone(messenger.next_reminder(self.moment, messenger.messages()))
        plain_task("Pay today", self.today)
        self.assertEqual(messenger.next_reminder(self.moment, messenger.messages()).stage, "due-today")
        # The user acts on a reminder: the kind starts again.
        with config.MESSAGES.open("a", encoding="utf-8") as handle:
            handle.write(reminder("2026-10-05 14:00", "due-today", "acted", "Pay today", "new456") + "\n")
        self.assertEqual(messenger.calibrate_reminders(self.moment), ["day-before reminders start again"])
        self.assertTrue(config.CALIBRATION.read_text(encoding="utf-8").endswith("delete its line.\nNone.\n"))
        self.assertEqual(messenger.calibrate_reminders(self.moment), [])

    def test_a_stop_that_the_user_deleted_does_not_come_back_at_once(self):
        lines(*(reminder(f"2026-10-0{d} 13:00", "week-before", "ignored", message_id=f"id{d}") for d in (1, 2, 3)))
        messenger.calibrate_reminders(self.moment)
        self.assertIn("week-before", messenger.stopped_reminders())
        text = config.CALIBRATION.read_text(encoding="utf-8")
        write(config.CALIBRATION, re.sub(r"- week-before .*\n", "", text))
        self.assertEqual(messenger.calibrate_reminders(self.moment + timedelta(hours=1)), [])
        self.assertEqual(messenger.stopped_reminders(), {})

    def test_the_daily_limit(self):
        with setting("DAILY_MESSAGES", 5):
            self.check_the_daily_limit()

    def check_the_daily_limit(self):
        at = [datetime(2026, 10, 5, h, 0, tzinfo=TZ) for h in (8, 9, 10, 11, 12)]
        history = [message(at[0], "summary", "[[_Seahorse]]", "—"), message(at[1], "delivery", "[[R]]"),
                   message(at[1], "checkin", "[[_Seahorse]]"), message(at[2], "question", "[[A]]"),
                   message(at[3], "reminder", "[[B]]"), message(at[4], "release", "[[C]]")]
        self.assertFalse(messenger.over_limit(self.moment, history))  # the summary and the delivery do not count
        history.append(message(at[4], "news", "[[D]]"))
        self.assertTrue(messenger.over_limit(self.moment, history))
        plain_task("Pay next week", self.today + timedelta(days=5))
        self.assertIsNone(messenger.next_reminder(self.moment, history))
        plain_task("Pay today", self.today)
        self.assertEqual(messenger.next_reminder(self.moment, history).stage, "due-today")  # due today always goes
        with setting("DAILY_MESSAGES", 10):
            self.assertFalse(messenger.over_limit(self.moment, history))

    def test_the_companion_keeps_the_daily_limit(self):
        lines(*(f"- 2026-10-05 1{i}:00 · question · id:q{i} · tg:{i} · [[X]] · reaction: pending · ?" for i in range(5)))
        self.plan(times=["12:50"])
        with setting("DAILY_MESSAGES", 5):
            self.assertEqual(companion.prepare(self.moment), (None, None))
        self.assertEqual(json.loads(config.COMPANION_STATE.read_text(encoding="utf-8"))["used"], 0)

    def test_dates_follow_the_language(self):
        plain_task("Pay the bill", date(2026, 10, 8))
        found = messenger.tasks.with_due_date()[0]
        with setting("LANGUAGE", "pt-BR"):
            self.assertEqual(messenger.reminder_header(found, self.today), "⏰ Pay the bill\nVence quinta, 08/10 · 15m")
        self.assertEqual(messenger.reminder_header(found, self.today), "⏰ Pay the bill\nDue Thursday, 10/08 · 15m")


class TestSilence(Fakes):
    def calibration(self, snoozed: str = "None.", muted: str = "None.") -> None:
        write(config.CALIBRATION, f"---\ntype: summary\n---\n## Snoozed\nTopics paused, as `- [[note]] until 2026-03-01`.\n"
                                  f"{snoozed}\n## Muted topics\n{muted}\n")

    def test_mutes_match_whole_words_and_note_links(self):
        self.calibration(muted="- car\n- [[Dentist]]")
        plain_task("Pay the car tax", self.today + timedelta(days=1))
        plain_task("Pagar o Cartão", self.today + timedelta(days=1))
        plain_task("Book the cleaning", self.today + timedelta(days=1), subject="[[Dentist]]")
        found = [c.source for c in messenger._reminders(self.today, [])]
        self.assertEqual(found, ["[[Pagar o Cartão]]"])

    def test_snoozed_topics_wait_until_their_date(self):
        self.calibration(snoozed="- [[Novel]] until 2026-10-08")
        write(config.QUEUE, "---\ntype: log\n---\n- [ ] 2026-10-05 · question · normal · Still writing? · [[Novel]]\n")
        plain_task("Send the chapter", self.today + timedelta(days=1), subject="[[Novel]]")
        self.assertEqual(messenger.from_queue(self.moment, deliveries=False), [])
        self.assertEqual(messenger._reminders(self.today, []), [])
        self.assertEqual(messenger.snoozed(date(2026, 10, 7)), {"Novel"})
        self.assertEqual(len(messenger.from_queue(datetime(2026, 10, 8, 13, 0, tzinfo=TZ), deliveries=False)), 1)


class TestReactions(Fakes):
    def test_a_button_reaction_does_not_wait_for_the_lock(self):
        messenger.record("revival", "[[Book]]", "Still alive?", 9, "a1b2c3")

        @contextmanager
        def busy(wait: bool = True):
            raise AssertionError("the bot must not take the lock")
            yield
        vault.lock = busy
        messenger.set_reaction("a1b2c3", "ignored")
        self.assertEqual(messenger.by_telegram(9).reaction, "ignored")  # readers see it at once
        self.assertIn("reaction: pending", config.MESSAGES.read_text(encoding="utf-8"))
        messenger.apply_reactions()  # the next Messenger run, with the lock
        self.assertIn("reaction: ignored", config.MESSAGES.read_text(encoding="utf-8"))
        self.assertEqual(list((config.STATE / "reactions").glob("*")), [])

    def test_a_later_reply_wins_over_an_earlier_button(self):
        messenger.record("revival", "[[Book]]", "Still alive?", 9, "a1b2c3")
        messenger.set_reaction("a1b2c3", "ignored")
        engram = vault.new_engram("Yes, on Friday", "telegram-text", self.morning, {"message_id": "a1b2c3"})
        messenger.record_reactions([engram])
        self.assertEqual(messenger.messages()[0].reaction, "replied")


class TestCompanionTurns(Fakes):
    def test_the_model_runs_without_the_lock(self):
        self.plan(times=["12:50"])
        self.answers = ['{"kind": "question", "source": "[[Tastes]]", "text": "Horror for the mood?", "buttons": ["A", "B"]}']
        self.assertEqual(messenger.run(), "question about [[Tastes]]")
        self.assertEqual(self.calls, [("companion", False)])
        keys = [b["text"] for row in self.sent[-1]["reply_markup"]["inline_keyboard"] for b in row]
        self.assertEqual(keys, ["🙈 Ignore"])  # a question gets a reply, never buttons
        self.assertEqual([(m.kind, m.source) for m in messenger.messages()], [("question", "[[Tastes]]")])

    def test_nothing_goes_out_when_the_moment_passed_while_the_model_wrote(self):
        self.answers = ['{"kind": "question", "text": "Horror?"}']
        self.moment = datetime(2026, 10, 6, 0, 30, tzinfo=TZ)  # the quiet hours started during the run
        self.assertIsNone(companion.converse(companion.Ask("question", "request", "abc123")))
        self.assertEqual(self.sent, [])

    def test_facts_from_outside_need_a_source(self):
        raw = '{"kind": "release", "source": "[[Tastes]]", "text": "A new horror movie, 7.1 on IMDb.", "buttons": ["🎬 Add"]'
        self.assertIsNone(companion.parse(raw + "}"))
        self.assertIsNone(companion.parse(raw + ', "sources": ["not a link"]}'))
        found = companion.parse(raw + ', "sources": ["https://www.imdb.com/title/tt1/"]}')
        self.assertEqual(found["sources"], ["https://www.imdb.com/title/tt1/"])
        self.assertEqual(companion.message_html(found), 'A new horror movie, 7.1 on IMDb.\n'
                                                        '🔗 Source: <a href="https://www.imdb.com/title/tt1/">imdb.com</a>')
        self.assertIsNone(companion.message_html(companion.parse('{"kind": "question", "text": "Horror?"}')))

    def test_a_sourced_message_goes_as_html_with_its_link(self):
        self.answers = ['{"kind": "news", "text": "The band <X> plays in May.", "sources": ["https://news.example/x"]}']
        self.assertEqual(companion.converse(companion.Ask("news", "request", "abc123")), "news about [[_Seahorse]]")
        self.assertEqual(self.sent[-1]["parse_mode"], "HTML")
        self.assertIn("The band &lt;X&gt; plays in May.\n🔗 Source: <a href=", self.sent[-1]["text"])
        self.assertTrue(messenger.messages()[0].text.startswith("The band <X> plays in May."))

    def test_focused_turns_keep_their_kind(self):
        self.assertEqual(companion.parse('{"kind": "question", "text": "How was Saturday?"}', "checkin")["kind"], "checkin")
        unblock = companion.parse('{"kind": "revival", "text": "What is in the way?", "buttons": ["🔎 Research"]}', "unblock")
        self.assertEqual((unblock["kind"], unblock["buttons"]), ("unblock", []))
        self.assertIsNone(companion.parse('{"kind": "checkin", "text": "How was Saturday?"}', "question"))
        self.assertIsNone(companion.parse('{"kind": "none"}', "checkin"))

    def test_an_ignored_unblock_waits_longer(self):
        note = task("Book the doctor")
        notes.set_field(note, "moved", "3")

        def asked(*days_and_reactions):
            return [message(self.moment - timedelta(days=d), "unblock", "[[Book the doctor]]", r) for d, r in days_and_reactions]
        self.assertEqual(companion.stuck(asked((8, "replied")), self.moment)[0].name, "Book the doctor")
        self.assertEqual(companion.stuck(asked((8, "ignored")), self.moment), [])
        self.assertEqual(len(companion.stuck(asked((15, "ignored")), self.moment)), 1)
        self.assertEqual(companion.stuck(asked((45, "ignored"), (20, "ignored")), self.moment), [])
        self.assertEqual(len(companion.stuck(asked((60, "ignored"), (31, "ignored")), self.moment)), 1)

    def test_an_unblock_with_nothing_to_say_pauses_for_a_week(self):
        note = task("Book the doctor")
        notes.set_field(note, "moved", "3")
        self.plan()
        self.answers = ['{"kind": "none"}']
        self.assertIsNone(companion.converse(companion.Ask("unblock", "request", "abc123")))
        tomorrow = self.moment + timedelta(days=1)
        companion._plan(tomorrow)  # a new day keeps the pause
        self.assertEqual(companion.stuck([], tomorrow), [])
        self.assertEqual(len(companion.stuck([], self.moment + timedelta(days=7))), 1)


class TestMigration(Base):
    SYSTEM = config.TEMPLATE / "6. ⚙️ System"

    def old(self, name: str) -> str:
        """The template file of 1.2.0, before the Messenger changes, filled in as in a vault."""
        text = (self.SYSTEM / name).read_text(encoding="utf-8")
        for file, old, new, _ in reversed(migrations.MESSENGER_CHANGES):
            if file == name:
                text = text.replace(new, old, 1) if old else text.replace("\n" + new, "", 1)
        return vault.fill(text, self.today)

    def test_old_vaults_get_the_new_rules_of_the_messenger(self):
        names = sorted({name for name, *_ in migrations.MESSENGER_CHANGES})
        for name in names:
            old = self.old(name)
            self.assertNotEqual(old, vault.fill((self.SYSTEM / name).read_text(encoding="utf-8"), self.today), name)
            write(config.SYSTEM / name, old + ("- 2026-10-01 09:00 · reminder · id:a · tg:1 · [[X]] · reaction: ignored · x\n"
                                               if name.endswith("Messages.md") else ""))
        migrations._to_1_3_0_messenger()
        for name in names:
            new = vault.fill((self.SYSTEM / name).read_text(encoding="utf-8"), self.today)
            found = (config.SYSTEM / name).read_text(encoding="utf-8")
            if name.endswith("Messages.md"):
                new += "- 2026-10-01 09:00 · reminder · id:a · tg:1 · [[X]] · reaction: ignored · x\n"
            self.assertEqual(found, new, name)
        migrations._to_1_3_0_messenger()  # a second run changes nothing
        self.assertEqual((config.SYSTEM / "_MESSENGER.md").read_text(encoding="utf-8"),
                         vault.fill((self.SYSTEM / "_MESSENGER.md").read_text(encoding="utf-8"), self.today))
        self.assertIn(("1.3.0", migrations._to_1_3_0_messenger), migrations.MIGRATIONS)

    def test_a_rule_that_the_user_changed_stays(self):
        rules = self.old("_MESSENGER.md").replace("and at most one goes out per hour.", "and one per hour is enough for me.")
        write(config.SYSTEM / "_MESSENGER.md", rules)
        migrations._to_1_3_0_messenger()
        text = (config.SYSTEM / "_MESSENGER.md").read_text(encoding="utf-8")
        self.assertIn("one per hour is enough for me.\n", text)
        self.assertIn("- **Hours.** Nothing between midnight and 8am. The 8am summary opens the day.\n"
                      "- **Reminders are separate.** They do not replace a conversation, and at most one goes out per hour. No reminder", text)

    def test_the_code_and_the_template_say_the_same_about_stopped_reminders(self):
        template = vault.fill((self.SYSTEM / "_Messenger" / "Calibration.md").read_text(encoding="utf-8"), self.today)
        self.assertIn(f"{messenger.STOPPED}\n{messenger.stopped_intro()}\nNone.", template)


if __name__ == "__main__":
    import unittest
    unittest.main()
