"""Tests of the parts that are code: engrams, tasks, the week, the Messenger, the Companion's timing, the bot's
texts, the template and the images. They never call an AI or Telegram.

Usage: python -m unittest discover -s tests
"""
import io
import json
import os
import random
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

_FOLDER = Path(tempfile.mkdtemp())
os.environ.update({
    "SEAHORSE_HOME": str(_FOLDER / "home"),
    "SEAHORSE_VAULT": str(_FOLDER / "vault"),
    "SEAHORSE_TIMEZONE": "America/Sao_Paulo",
    "SEAHORSE_LANGUAGE": "en",
    "SEAHORSE_USER_NAME": "Ada",
})

from seahorse import (agents, companion, config, i18n, lint, messenger, notes, tasks, telegram_api,  # noqa: E402
                      vault, week, week_image)
from seahorse.bot import asks_for_day, asks_for_week, looks_like_question  # noqa: E402

TZ = config.TIMEZONE


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def task(name: str, due: date | None = None, done: bool = False, repeat: str = "", tags: str = "[home]") -> Path:
    fields = f"type: task\ndone: {'true' if done else 'false'}\nestimate: 15m\n"
    fields += f"due: {due}\n" if due else ""
    fields += f"repeat: {repeat}\n" if repeat else ""
    path = config.TASKS / f"{name}.md"
    write(path, f"---\n{fields}tags: {tags}\nsubject: \"[[Car]]\"\n---\n")
    return path


def message(when: datetime, kind: str, source: str, reaction: str = "pending") -> messenger.Message:
    return messenger.Message(when, kind, "abc123", "1", source, reaction)


class Base(unittest.TestCase):
    """Monday 5 October 2026 is the first day of week 41. "Today" is Monday at 10:30 unless a test says otherwise."""

    def setUp(self):
        shutil.rmtree(_FOLDER, ignore_errors=True)
        config.VAULT.mkdir(parents=True)
        self.today = date(2026, 10, 5)
        self.morning = datetime(2026, 10, 5, 10, 30, tzinfo=TZ)

    def week_file(self, **cards: str) -> Path:
        days = [("Mon", "05/10"), ("Tue", "06/10"), ("Wed", "07/10"), ("Thu", "08/10"), ("Fri", "09/10"),
                ("Sat", "10/10"), ("Sun", "11/10")]
        parts = ["---\ntype: week\nweek: 2026-W41\n---"]
        for label, day in days:
            parts.append(f"> [!day] {label} {day} · 0h of 4h" + cards.get(label, ""))
            parts.append("")
        path = config.WEEKS / "2026-W41.md"
        write(path, "\n".join(parts) + "> [!review]- Review\n> - Planned:\n")
        return path


class TestEngrams(Base):
    def test_engram_has_frontmatter_and_no_blank_lines(self):
        path = vault.new_engram("pay the car tax\n\n\nby the 15th", "telegram-voice", self.morning, {"in_reply_to": 'he said "hi": ok'})
        fm, body = notes.read(path)
        self.assertEqual(path.name, "2026-10-05 1030.md")
        self.assertEqual((fm["state"], fm["received"], fm["in_reply_to"]), ("pending", "2026-10-05 10:30", "he said 'hi': ok"))
        self.assertEqual(body.strip(), "pay the car tax\nby the 15th")

    def test_two_engrams_in_the_same_minute_get_a_suffix(self):
        first = vault.new_engram("a", "telegram-text", self.morning)
        second = vault.new_engram("b", "telegram-text", self.morning)
        self.assertEqual((first.stem, second.stem), ("2026-10-05 1030", "2026-10-05 1030 2"))
        self.assertEqual(len(vault.engrams("pending")), 2)

    def test_set_field_keeps_the_body(self):
        path = vault.new_engram("original text", "telegram-voice", self.morning)
        notes.set_field(path, "confirmation", "777")
        notes.set_field(path, "confirmation", "778")
        fm, body = notes.read(path)
        self.assertEqual((fm["confirmation"], fm["state"], body.strip()), ("778", "pending", "original text"))
        self.assertEqual(vault.engram_by_confirmation(778), path)


class TestTasks(Base):
    def test_checking_in_the_week_checks_the_task(self):
        note = task("Review the doc")
        self.week_file(Tue="\n> - [x] [[Review the doc]] (1h)")
        self.assertEqual(tasks.sync(self.today), 1)
        self.assertEqual(notes.read(note)[0]["done"], "true")
        self.assertEqual(tasks.sync(self.today), 0)

    def test_checking_in_the_table_checks_the_week(self):
        task("Review the doc", done=True)
        path = self.week_file(Tue="\n> - [ ] [[Review the doc]] (1h)")
        tasks.sync(self.today)
        self.assertIn("> - [x] [[Review the doc]] (1h)\n", path.read_text(encoding="utf-8"))

    def test_a_repeating_task_comes_back_with_the_next_date(self):
        note = task("Pay the car tax", date(2026, 10, 6), repeat="yearly")
        self.week_file(Mon="\n> - [x] [[Pay the car tax]] (15m)")
        tasks.sync(self.today)
        fm, body = notes.read(note)
        self.assertEqual((fm["done"], fm["due"], fm["last_done"]), ("false", "2027-10-06", "2026-10-05"))
        self.assertIn("## History\n- 2026-10-05 · done", body)
        self.assertEqual(tasks.sync(self.today), 0)

    def test_next_date(self):
        self.assertEqual(tasks.next_date(date(2026, 1, 31), "monthly"), date(2026, 2, 28))
        self.assertEqual(tasks.next_date(date(2026, 12, 15), "monthly"), date(2027, 1, 15))
        self.assertEqual(tasks.next_date(date(2028, 2, 29), "yearly"), date(2029, 2, 28))
        self.assertIsNone(tasks.next_date(date(2026, 1, 1), ""))

    def test_work_tag_comes_from_the_config(self):
        task("Write the report", tags="\n  - work")
        task("Buy bread")
        self.assertEqual(tasks.work_tasks(), {"Write the report"})


class TestWeek(Base):
    WEDNESDAY = date(2026, 10, 7)

    def test_durations(self):
        self.assertEqual([week.minutes(x) for x in ("2h", "1h30", "45m", "15m?", "")], [120, 90, 45, 15, 0])
        self.assertEqual([week.duration(x) for x in (0, 45, 60, 90, 240)], ["0h", "45m", "1h", "1h30", "4h"])

    def test_a_task_not_done_moves_to_today(self):
        note = task("Book the doctor")
        path = self.week_file(Tue="\n> - [ ] [[Book the doctor]] (30m)\n> - [x] [[Done one]] (1h)")
        moves = week.reschedule(self.WEDNESDAY)
        self.assertEqual([str(m) for m in moves], ["Book the doctor: tue → wed"])
        text = path.read_text(encoding="utf-8")
        self.assertIn("> [!day] Tue 06/10 · 1h of 4h\n> - [x] [[Done one]] (1h)\n\n", text)
        self.assertIn("> [!day] Wed 07/10 · 30m of 4h\n> - [ ] [[Book the doctor]] (30m)\n\n", text)
        self.assertTrue(text.endswith("> [!review]- Review\n> - Planned:\n"))
        self.assertEqual(notes.read(note)[0]["moved"], "1")
        self.assertEqual(week.reschedule(self.WEDNESDAY), [])

    def test_no_room_leaves_the_week(self):
        note = task("Sort the photos")
        full = "\n> - [ ] [[Other]] (4h)"
        path = self.week_file(Mon="\n> - [ ] [[Sort the photos]] (2h)", Wed=full, Thu=full, Fri=full, Sat=full, Sun=full)
        self.assertEqual(str(week.reschedule(self.WEDNESDAY)[0]), "Sort the photos: mon → out of the week, no room")
        self.assertNotIn("Sort the photos", path.read_text(encoding="utf-8"))
        self.assertEqual(notes.read(note)[0]["postponed"], "1")

    def test_a_due_date_keeps_it_in_the_week(self):
        task("Pay the bill", date(2026, 10, 8))
        full = "\n> - [ ] [[Other]] (4h)"
        path = self.week_file(Mon="\n> - [ ] [[Pay the bill]] (30m)", Wed=full, Thu=full)
        week.reschedule(self.WEDNESDAY)
        self.assertIn("> [!day] Wed 07/10 · 4h30 of 4h\n> - [ ] [[Other]] (4h)\n> - [ ] [[Pay the bill]] (30m)", path.read_text(encoding="utf-8"))

    def test_create_the_week_if_missing(self):
        path = week.create_if_missing(self.today)
        self.assertEqual(path.name, "2026-W41.md")
        w = week.read(self.today)
        self.assertEqual([d.label for d in w.days], ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
        self.assertEqual((w.days[0].date, w.days[0].capacity), (self.today, 240))


class TestMessenger(Base):
    def test_reminds_a_week_before_and_the_day_before(self):
        task("Pay the car tax", self.today + timedelta(days=7))
        chosen = messenger.next_reminder(self.morning, [])
        self.assertEqual((chosen.kind, chosen.source, chosen.urgent), ("reminder", "[[Pay the car tax]]", False))
        task("Pay the car tax", self.today + timedelta(days=1))
        self.assertTrue(messenger.next_reminder(self.morning, []).urgent)

    def test_at_most_two_reminders_and_not_in_a_burst(self):
        task("Pay the car tax", self.today + timedelta(days=1))
        source = "[[Pay the car tax]]"
        both = [message(datetime(2026, 9, 28, 9, tzinfo=TZ), "reminder", source), message(datetime(2026, 10, 3, 9, tzinfo=TZ), "reminder", source)]
        self.assertIsNone(messenger.next_reminder(self.morning, both))
        recent = [message(self.morning - timedelta(minutes=20), "reminder", "[[Other]]")]
        self.assertIsNone(messenger.next_reminder(self.morning, recent))

    def test_quiet_hours(self):
        self.assertTrue(messenger.quiet(datetime(2026, 10, 5, 0, 10, tzinfo=TZ)))
        self.assertTrue(messenger.quiet(datetime(2026, 10, 5, 7, 59, tzinfo=TZ)))
        self.assertFalse(messenger.quiet(datetime(2026, 10, 5, 23, 40, tzinfo=TZ)))

    def test_quiet_hours_across_midnight(self):
        old = config.QUIET_FROM, config.QUIET_UNTIL
        config.QUIET_FROM, config.QUIET_UNTIL = 22 * 60, 7 * 60
        try:
            self.assertTrue(messenger.quiet(datetime(2026, 10, 5, 23, 0, tzinfo=TZ)))
            self.assertTrue(messenger.quiet(datetime(2026, 10, 5, 6, 0, tzinfo=TZ)))
            self.assertFalse(messenger.quiet(datetime(2026, 10, 5, 12, 0, tzinfo=TZ)))
        finally:
            config.QUIET_FROM, config.QUIET_UNTIL = old

    def test_queue_and_deliveries(self):
        write(config.QUEUE, "---\ntype: log\n---\n- [ ] 2026-10-05 · connection · normal · This links to March. · [[Pointers]]\n"
                            "- [ ] 2026-10-05 20:00 · delivery · high · Found three salary ranges. · [[Salaries]]\n"
                            "- [ ] 2026-09-01 · connection · high · Old. · [[Y]]\n")
        self.assertEqual([c.text for c in messenger.from_queue(self.morning, deliveries=False)], ["This links to March."])
        self.assertEqual(messenger.from_queue(self.morning, deliveries=True), [])
        self.assertEqual(len(messenger.from_queue(datetime(2026, 10, 5, 20, 5, tzinfo=TZ), deliveries=True)), 1)

    def test_reminder_header(self):
        task("Pay the car tax", date(2026, 10, 8))
        found = tasks.with_due_date()[0]
        self.assertEqual(messenger.reminder_header(found, self.today), "⏰ Pay the car tax\nDue Thursday, 08/10 · 15m")
        self.assertIn("Due tomorrow", messenger.reminder_header(found, date(2026, 10, 7)))

    def test_reactions(self):
        write(config.MESSAGES, "---\ntype: log\n---\n")
        messenger.record("revival", "[[Book]]", "Still alive?", 9, "a1b2c3")
        self.assertEqual(messenger.by_telegram(9).id, "a1b2c3")
        engram = vault.new_engram("Put it on Friday", "telegram-text", self.morning, {"message_id": "a1b2c3", "via_button": "yes"})
        messenger.record_reactions([engram])
        self.assertEqual(messenger.messages()[0].reaction, "acted")
        messenger.set_reaction("a1b2c3", "ignored")
        self.assertEqual(messenger.messages()[0].reaction, "ignored")

    def test_every_conversation_has_the_ignore_button(self):
        buttons = messenger.conversation_buttons("a1b2c3", ["✍️ Friday", "📅 Other day", "💤 Pause"])
        self.assertEqual(buttons[-1], ("🙈 Ignore", "msg|a1b2c3|ign"))
        self.assertEqual([len(r) for r in telegram_api.rows(buttons)], [2, 2])


class TestCompanion(Base):
    def plan(self, **fields) -> None:
        base = {"day": "2026-10-05", "times": [], "used": 0, "checkin": "21:00", "checkin_done": False}
        write(config.COMPANION_STATE, json.dumps(base | fields))

    def test_drawn_times_fit_the_day(self):
        early = datetime(2026, 10, 5, 7, 0, tzinfo=TZ)
        for seed in range(200):
            times = companion.draw_times(early, random.Random(seed))
            self.assertTrue(1 <= len(times) <= 4, times)
            self.assertTrue(all("08:45" <= t <= "23:30" for t in times), times)
            minutes = [int(t[:2]) * 60 + int(t[3:]) for t in times]
            self.assertTrue(all(b - a >= 100 for a, b in zip(minutes, minutes[1:])), times)
        self.assertEqual(companion.draw_times(datetime(2026, 10, 5, 23, 45, tzinfo=TZ)), [])

    def test_each_time_counts_once_and_the_checkin_once(self):
        self.plan(times=["09:00", "10:00", "15:00"])
        self.assertEqual(companion.turn(self.morning), "conversation")
        self.assertIsNone(companion.turn(self.morning + timedelta(minutes=10)))
        self.assertEqual(companion.turn(datetime(2026, 10, 5, 21, 0, tzinfo=TZ)), "checkin")
        self.assertEqual(companion.turn(datetime(2026, 10, 5, 21, 10, tzinfo=TZ)), "conversation")
        self.assertIsNone(companion.turn(datetime(2026, 10, 5, 21, 20, tzinfo=TZ)))

    def test_focus_varies(self):
        last = [message(self.morning - timedelta(hours=h), kind, "[[X]]") for h, kind in ((5, "release"), (2, "question"))]
        found = {companion.pick_focus(last, random.Random(s)) for s in range(300)}
        self.assertFalse({"release", "question"} & found)

    def test_stuck_tasks(self):
        note = task("Book the doctor")
        notes.set_field(note, "moved", "3")
        self.assertEqual([t.name for t in companion.stuck([], self.morning)], ["Book the doctor"])
        asked = [message(self.morning - timedelta(days=2), "unblock", "[[Book the doctor]]")]
        self.assertEqual(companion.stuck(asked, self.morning), [])

    def test_parse_the_model_answer(self):
        raw = 'Here:\n```json\n{"kind": "revival", "source": "[[My Book]]", "text": "Still alive?", "buttons": ["✍️ Friday", "💤 Pause", "x"]}\n```'
        found = companion.parse(raw)
        self.assertEqual((found["kind"], found["source"], found["buttons"]), ("revival", "[[My Book]]", ["✍️ Friday", "💤 Pause"]))
        self.assertIsNone(companion.parse('{"kind": "none"}'))
        self.assertIsNone(companion.parse('{"kind": "reminder", "text": "x"}'))
        self.assertEqual(companion.parse('{"kind": "question", "text": "Horror?"}')["source"], "[[_Seahorse]]")


class TestBotTexts(Base):
    def test_capture_texts_and_buttons(self):
        self.assertEqual(telegram_api.capture_text("telegram-voice", False, None), "✓ Voice note saved. Integrating…")
        self.assertEqual(telegram_api.capture_text("telegram-text", False, "Book in Wishlist"), "✓ Saved\n→ Book in Wishlist")
        self.assertEqual(telegram_api.capture_buttons("x", "telegram-text", False), [])
        self.assertEqual([r for r, _ in telegram_api.capture_buttons("2026-10-05 0814 12", "telegram-voice", False, "ok")],
                         ["📝 Read transcript", "✏️ Fix"])
        week_full = telegram_api.capture_buttons("2026-10-05 0814", "telegram-text", False, "! Does not fit")
        self.assertEqual([d for _, d in week_full], ["eng|week|2026-10-05 0814", "eng|backlog|2026-10-05 0814"])
        self.assertTrue(all(len(d.encode()) <= 64 for _, d in week_full))

    def test_words_that_ask_for_images(self):
        self.assertTrue(asks_for_week("Week") and asks_for_week(" calendar "))
        self.assertTrue(asks_for_day("today?"))
        self.assertFalse(asks_for_week("this week I need to call the bank"))

    def test_question_detection(self):
        self.assertTrue(looks_like_question("when is the car tax due?"))
        self.assertTrue(looks_like_question("what do I have today"))
        self.assertFalse(looks_like_question("pay the car tax by the 15th"))

    def test_results_of_the_integrator(self):
        text = "2026-10-05 0814 => Task: eye doctor\n`2026-10-05 0815 2 => ? Task or idea?`\nother line"
        self.assertEqual(agents.parse_results(text), {"2026-10-05 0814": "Task: eye doctor", "2026-10-05 0815 2": "? Task or idea?"})


class TestLanguages(unittest.TestCase):
    def test_every_language_has_every_key(self):
        def keys(data: dict, prefix: str = "") -> set[str]:
            found = set()
            for k, v in data.items():
                found |= keys(v, f"{prefix}{k}.") if isinstance(v, dict) else {f"{prefix}{k}"}
            return found
        english = keys(i18n._catalog("en"))
        for language in i18n.LANGUAGES:
            self.assertEqual(keys(i18n._catalog(language)) ^ english, set(), language)

    def test_every_text_fills_its_fields(self):
        """Each {field} must be a name that i18n.t can take, and each text must fill without errors."""
        import string
        for language in i18n.LANGUAGES:
            for section, texts in i18n._catalog(language).items():
                if not isinstance(texts, dict):
                    continue
                for name, text in texts.items():
                    if not isinstance(text, str):
                        continue
                    fields = {f for _, f, _, _ in string.Formatter().parse(text) if f}
                    self.assertFalse(fields & {"key", "language"}, f"{language} {section}.{name}")
                    i18n.t(f"{section}.{name}", language, **{f: "x" for f in fields})

    def test_portuguese_and_spanish(self):
        self.assertEqual(i18n.t("button.ignore", "pt-BR"), "🙈 Ignorar")
        self.assertEqual(i18n.t("capture.working", "es", title="✓ Guardado"), "✓ Guardado. Integrando…")
        self.assertEqual(i18n.language_name("pt-BR"), "Brazilian Portuguese")
        self.assertEqual(len(i18n.words("days_short", "es")), 7)


class TestTemplate(Base):
    def test_a_new_vault_is_complete_and_valid(self):
        created = vault.create(self.today)
        self.assertGreater(len(created), 50)
        for path in created:
            text = (config.VAULT / path).read_bytes()
            self.assertNotIn(b"\r", text, path)
            if "_Templates" not in path:  # note templates keep their own {{fields}} for the agents to fill
                self.assertNotIn(b"{{", text, path)
        dashboard = config.DASHBOARD.read_text(encoding="utf-8")
        self.assertIn("![[2026-W41]]", dashboard)
        self.assertTrue((config.WEEKS / "2026-W41.md").exists())
        errors, _, _ = lint.check(config.VAULT)
        self.assertEqual(errors, [])
        self.assertEqual(vault.create(self.today), [])  # a second run changes nothing

    def test_user_changes_come_from_git(self):
        vault.git("init", "-q", "-b", "main")

        def commit(author: str, path: str, text: str) -> None:
            write(config.VAULT / path, text)
            vault.git("add", "-A")
            vault.git("-c", f"user.name={author}", "-c", "user.email=x@x", "commit", "-q", "-m", "change")
        commit("Seahorse", "1. 🎯 Intention/_Intention.md", "summary")
        base = vault.git("rev-parse", "HEAD").stdout.strip()
        commit("Ada", "3. 📚 Knowledge/Projects/Acme/Acme.md", "I lead billing.")
        commit("Ada", "1. 🎯 Intention/Week/2026-W41.md", "- [x] task")
        commit("Seahorse", "3. 📚 Knowledge/Projects/Acme/Acme.md", "I lead billing.\n## Seahorse\n[[Acme]]")
        self.assertEqual(list(vault.user_changes(base)), ["3. 📚 Knowledge/Projects/Acme/Acme.md"])

    def test_edits_on_this_machine_stay_the_users(self):
        """Obsidian and Seahorse on the same folder: an agent commit must not take the user's edit as its own."""
        vault.create(self.today)
        vault.publish("seahorse: new vault")
        base = vault.git("rev-parse", "HEAD").stdout.strip()
        write(config.VAULT / "3. 📚 Knowledge/Idea.md", "An offline mode for the app.")
        vault.pull()  # every agent pulls before it writes
        write(config.VAULT / "1. 🎯 Intention/_Intention.md", "summary by an agent")
        vault.publish("integrator: something")
        self.assertEqual(list(vault.user_changes(base)), ["3. 📚 Knowledge/Idea.md"])


class TestSetupTexts(unittest.TestCase):
    def test_a_text_can_have_any_field_name(self):
        from seahorse import setup
        self.assertIn("Ada", setup._t("done", name="Ada", notes="-"))  # {name} once broke the end of the setup

    def test_quiet_hours_off_or_after_midnight(self):
        old = config.QUIET_FROM, config.QUIET_UNTIL
        try:
            config.QUIET_FROM = config.QUIET_UNTIL = 0  # no quiet hours
            self.assertEqual(companion.window()[1], 23 * 60 + 30)
            self.assertFalse(messenger.quiet(datetime(2026, 10, 5, 3, 0, tzinfo=TZ)))
            config.QUIET_FROM, config.QUIET_UNTIL = 60, 8 * 60  # 01:00 to 08:00
            self.assertEqual(companion.window()[1], 23 * 60 + 30)
            config.QUIET_FROM = 22 * 60
            self.assertEqual(companion.window()[1], 21 * 60 + 30)
        finally:
            config.QUIET_FROM, config.QUIET_UNTIL = old


class TestMachine(unittest.TestCase):
    def test_login_links_and_codes_in_a_terminal_output(self):
        from seahorse.machine import links_and_codes
        gh = "! First copy your one-time code: 1A2B-3C4D\r\nPress Enter to open \x1b[1mhttps://github.com/login/device\x1b[0m in your browser..."
        self.assertEqual(links_and_codes(gh), (["https://github.com/login/device"], ["1A2B-3C4D"]))
        claude = "Browser didn't open? Use the url below to sign in:\r\n\r\nhttps://claude.ai/oauth/authorize?code=true&client_id=x&state=y"
        self.assertEqual(links_and_codes(claude), ([], []))  # the link is not complete yet
        self.assertEqual(links_and_codes(claude + "\r\n")[0], ["https://claude.ai/oauth/authorize?code=true&client_id=x&state=y"])
        self.assertEqual(links_and_codes("See https://example.com/docs for help.\n"), ([], []))  # not a login


class TestImages(Base):
    def test_week_and_day_images(self):
        from PIL import Image
        task("Pay the bill", date(2026, 10, 9))
        self.week_file(Tue="\n> - [x] [[Done one]] (1h)", Wed="\n> - [ ] [[Pay the bill]] (30m)")
        png = week_image.week_png(date(2026, 10, 7))
        self.assertEqual(Image.open(io.BytesIO(png)).size[0], 1920)
        self.assertLess(len(png), 400_000)
        day = week_image.day_png(date(2026, 10, 7))
        self.assertEqual(Image.open(io.BytesIO(day)).size[0], 1280)
        self.assertIsNone(week_image.week_png(date(2026, 1, 7)))

    def test_warnings_only_for_real_due_dates(self):
        end, today = date(2026, 10, 11), date(2026, 10, 7)
        self.assertEqual(week_image.warning(None, today, end), "")
        self.assertEqual(week_image.warning(today, today, end), "due today")
        self.assertEqual(week_image.warning(date(2026, 10, 10), today, end), "due sat")
        self.assertEqual(week_image.warning(date(2026, 10, 5), today, end), "late")


if __name__ == "__main__":
    unittest.main()
