"""Tests of the week, the tasks, the week image, the vault sync and the morning and weekly runs. They never call an
AI or Telegram.

Usage: python -m unittest discover -s tests
"""
from test_seahorse import Base, write, task, message, remove  # sets up the throwaway vault

import io
import subprocess
from datetime import date, datetime, timedelta

from PIL import Image

from seahorse import config, i18n, notes, tasks, telegram_api, vault, week, week_image

TZ = config.TIMEZONE
W42 = [("Mon", "12/10"), ("Tue", "13/10"), ("Wed", "14/10"), ("Thu", "15/10"), ("Fri", "16/10"), ("Sat", "17/10"),
       ("Sun", "18/10")]


def fields(path) -> dict:
    return notes.read(path)[0]


def text(path) -> str:
    return path.read_text(encoding="utf-8")


def check(path, name: str, checked: bool = True) -> None:
    """The user checks (or unchecks) the line of a task, as in Obsidian."""
    old, new = (f"- [ ] [[{name}]]", f"- [x] [[{name}]]") if checked else (f"- [x] [[{name}]]", f"- [ ] [[{name}]]")
    write(path, text(path).replace(old, new, 1))


class WeekBase(Base):
    def other_week(self, name: str = "2026-W42", days=None, **cards: str):
        """A week file like `week_file`, for another week."""
        parts = [f"---\ntype: week\nweek: {name}\n---"]
        for label, day in days or W42:
            parts += [f"> [!day] {label} {day} · 0h of 4h" + cards.get(label, ""), ""]
        path = config.WEEKS / f"{name}.md"
        write(path, "\n".join(parts) + "> [!review]- Review\n")
        return path

    def in_language(self, language: str):
        old = config.LANGUAGE
        config.LANGUAGE = language
        self.addCleanup(setattr, config, "LANGUAGE", old)


class TestReading(WeekBase):
    def test_estimates_in_many_formats(self):
        cases = {"2h": 120, "1h30": 90, "1h 30m": 90, "90 min": 90, "30min": 30, "1.5h": 90, "1,5h": 90, "2 h": 120,
                 "~1h": 60, "15m?": 15, "1 hora": 60, "45 minutos": 45, "": 0, "2026": 0, "part 2": 0}
        self.assertEqual({k: week.minutes(k) for k in cases}, cases)
        self.week_file(Mon="\n> - [ ] [[A]] (2h) ⚠️ due Thu\n> - [ ] [[B]] (~1h)\n> - [ ] Taxes (2026) (30m)\n> - [ ] [[C]]")
        task("C")  # its note says 15m
        items = week.read(self.today).days[0].items
        self.assertEqual([(i.name, i.minutes) for i in items], [("A", 120), ("B", 60), ("Taxes (2026)", 30), ("C", 15)])

    def test_each_card_is_the_day_of_its_date(self):
        path = config.WEEKS / "2026-W41.md"
        write(path, "---\ntype: week\n---\n> [!day] Tue 06/10 · 0h of 4h\n> - [ ] [[On tuesday]] (1h)\n\n"
                    "> [!day] Mon 05/10\n> - [ ] [[On monday]] (30m)\n\n"
                    "> [!day]- Qua 07/10 · 30m de 4h (cheio)\n> - [ ] [[On wednesday]] (30m)\n\n"
                    "> [!day]+ Jueves 08/10 · 1h de 4h\n\n> [!review]- Review\n")
        w = week.read(self.today)
        self.assertEqual([d.date.day for d in w.days], [5, 6, 7, 8, 9, 10, 11])
        self.assertEqual([[i.name for i in d.items] for d in w.days[:3]], [["On monday"], ["On tuesday"], ["On wednesday"]])
        self.assertEqual((w.days[2].fold, w.days[2].extra, w.days[3].fold), ("-", "(cheio)", "+"))
        self.assertTrue(week.refresh(self.today))
        written = text(path)
        self.assertIn("> [!day] Mon 05/10 · 30m of 4h\n> - [ ] [[On monday]] (30m)\n\n> [!day] Tue 06/10", written)
        self.assertIn("> [!day]- Wed 07/10 · 30m of 4h (cheio)\n", written)
        self.assertIn("> [!day] Sun 11/10 · 0h of 4h\n\n> [!review]- Review\n", written)  # the missing days are back
        self.assertFalse(week.refresh(self.today))

    def test_a_wrong_or_repeated_card(self):
        path = config.WEEKS / "2026-W41.md"
        write(path, "---\ntype: week\n---\n> [!day] Mon 05/10 · 0h of 4h\n> - [ ] [[A]] (1h)\n\n"
                    "> [!day] Tue 05/10 · 0h of 4h\n> - [ ] [[B]] (1h)\n\n"
                    "> [!day] Mon 05/10 · 0h of 4h\n> - [ ] [[C]] (1h)\n\n"
                    "> [!day] Something\n> - [ ] [[D]] (1h)\n")
        w = week.read(self.today)
        self.assertEqual([[i.name for i in d.items] for d in w.days[:3]], [["A", "C"], ["B"], ["D"]])
        self.assertEqual(week.problems(path), [
            "line 13: the card has no date or day name of this week",
            "3 cards for Mon 05/10", "no card for Tue 06/10", "no card for Wed 07/10", "no card for Thu 08/10",
            "no card for Fri 09/10", "no card for Sat 10/10", "no card for Sun 11/10"])
        self.assertEqual(week.problems(week.create_if_missing(date(2026, 10, 12))), [])
        self.assertEqual(week.problems(config.WEEKS / "plan.md"), ["the file name is not YYYY-Www, such as 2026-W41"])

    def test_free_text_stays_in_its_card(self):
        path = self.week_file(Mon="\n> - [ ] [[A]] (1h)\nA note of mine\n\n\n> - [ ] [[B]] (30m)", Tue="\n> - [ ] [[C]] (1h)")
        week.refresh(self.today)
        first = text(path)
        self.assertIn("> [!day] Mon 05/10 · 1h30 of 4h\n> - [ ] [[A]] (1h)\nA note of mine\n\n> - [ ] [[B]] (30m)\n\n"
                      "> [!day] Tue 06/10", first)
        task("A")
        week.reschedule(date(2026, 10, 7))
        week.refresh(date(2026, 10, 7))
        self.assertIn("A note of mine", text(path).split("> [!day] Tue")[0])
        self.assertNotIn("\n\n\n", text(path))

    def test_titles_in_the_language_of_the_user(self):
        self.in_language("pt-BR")
        path = self.week_file(Wed="\n> - [ ] [[A]] (1h)")
        week.refresh(self.today)
        self.assertIn("> [!day] Qua 07/10 · 1h de 4h\n", text(path))
        self.assertEqual([d.date.day for d in week.read(self.today).days], [5, 6, 7, 8, 9, 10, 11])
        self.in_language("es")
        week.refresh(self.today)
        self.assertIn("> [!day] Mié 07/10 · 1h de 4h\n", text(path))

    def test_the_images_of_a_broken_week_do_not_crash(self):
        path = config.WEEKS / "2026-W41.md"
        write(path, "---\ntype: week\n---\n> [!day] Mon 05/10\n> - [ ] [[A]] (1h)\n\n> [!day] Qua 07/10 · 30m de 4h\n")
        for day in range(5, 12):
            self.assertIsNotNone(week_image.day_png(date(2026, 10, day)))
        self.assertIsNotNone(week_image.week_png(self.today))


class TestMoves(WeekBase):
    WEDNESDAY = date(2026, 10, 7)

    def test_sundays_open_tasks_come_into_the_new_week(self):
        call, done = task("Call mom"), task("Read", done=True)
        old = self.week_file(Sun="\n> - [ ] [[Call mom]] (30m)\n> - [ ] [[Read]] (1h)\n> - [x] [[Gym]] (1h)")
        new = self.other_week()
        moves = week.reschedule(date(2026, 10, 12))
        self.assertEqual([str(m) for m in moves], ["Call mom: sun → mon"])
        self.assertIn("> [!day] Mon 12/10 · 30m of 4h\n> - [ ] [[Call mom]] (30m)\n", text(new))
        self.assertNotIn("Call mom", text(old))
        self.assertIn("[[Read]]", text(old))  # done in the table: sync checks it, it does not move
        self.assertEqual(fields(call)["postponed"], "1")  # it left a week unfinished
        self.assertEqual(week.reschedule(date(2026, 10, 12)), [])

    def test_a_task_the_planner_already_carried_is_not_doubled(self):
        task("Call mom")
        old = self.week_file(Sun="\n> - [ ] [[Call mom]] (30m)")
        new = self.other_week(Tue="\n> - [ ] [[Call mom]] (30m)")
        self.assertEqual(week.reschedule(date(2026, 10, 12)), [])
        self.assertNotIn("Call mom", text(old))
        self.assertEqual(text(new).count("Call mom"), 1)

    def test_a_task_postponed_again_and_again_waits_for_the_planner(self):
        note = task("Learn Rust")
        notes.set_field(note, "postponed", "2")
        self.week_file(Sun="\n> - [ ] [[Learn Rust]] (1h)")
        new = self.other_week()
        self.assertEqual([str(m) for m in week.reschedule(date(2026, 10, 12))], ["Learn Rust: sun → out of the week, postponed"])
        self.assertNotIn("Learn Rust", text(new))
        self.assertEqual(fields(note)["postponed"], "3")

    def test_a_due_date_far_ahead_leaves_a_full_week_without_counting(self):
        note = task("Buy a gift", date(2026, 11, 13))
        full = "\n> - [ ] [[Other]] (4h)"
        path = self.week_file(Mon="\n> - [ ] [[Buy a gift]] (1h)", Wed=full, Thu=full, Fri=full, Sat=full, Sun=full)
        moves = week.reschedule(self.WEDNESDAY)
        self.assertEqual([str(m) for m in moves], ["Buy a gift: mon → out of the week, due later"])
        self.assertNotIn("Buy a gift", text(path))
        self.assertNotIn("moved", fields(note))
        self.assertNotIn("postponed", fields(note))

    def test_a_due_date_this_week_stays_even_over_the_limit_and_counts(self):
        note = task("Pay the bill", date(2026, 10, 9))
        full = "\n> - [ ] [[Other]] (4h)"
        self.week_file(Mon="\n> - [ ] [[Pay the bill]] (30m)", Wed=full, Thu=full, Fri=full)
        self.assertEqual([str(m) for m in week.reschedule(self.WEDNESDAY)], ["Pay the bill: mon → wed"])
        self.assertEqual(fields(note)["moved"], "1")  # due this week: the user committed to it

    def test_a_day_the_user_chose(self):
        note = task("Book the doctor")
        notes.set_field(note, "planned", "2026-10-06")
        path = self.week_file()
        week.reschedule(self.today)
        self.assertIn("> [!day] Tue 06/10 · 15m of 4h\n> - [ ] [[Book the doctor]] (15m)\n", text(path))
        moves = week.reschedule(self.WEDNESDAY)
        self.assertEqual([str(m) for m in moves], ["Book the doctor: tue → wed"])
        self.assertEqual(fields(note)["moved"], "1")  # a day the user chose: a real slip
        write(path, text(path).replace("> - [ ] [[Book the doctor]] (15m)\n", ""))
        week.reschedule(self.WEDNESDAY)
        self.assertNotIn("Book the doctor", text(path))  # the user took it out: it is placed only once

    def test_a_planned_day_of_a_week_not_planned_yet(self):
        note = task("Call the bank")
        notes.set_field(note, "planned", "2026-10-12")  # "on Monday", said on Friday
        self.week_file()
        week.reschedule(date(2026, 10, 9))
        new = self.other_week()
        week.reschedule(date(2026, 10, 12))
        self.assertIn("> [!day] Mon 12/10 · 15m of 4h\n> - [ ] [[Call the bank]] (15m)\n", text(new))

    def test_the_moves_wait_for_the_morning(self):
        task("Book the doctor")
        task("Sort the photos")
        self.week_file(Mon="\n> - [ ] [[Book the doctor]] (30m)", Tue="\n> - [ ] [[Book the doctor]] (30m)")
        week.reschedule(date(2026, 10, 6))  # the Messenger at midnight
        week.reschedule(self.WEDNESDAY)
        self.assertEqual(week.reschedule(self.WEDNESDAY), [])  # the morning run moves nothing more
        moves = week.take_moves(self.WEDNESDAY)
        self.assertEqual([week.describe(m, self.WEDNESDAY) for m in moves], ["Book the doctor: Monday 05/10 → today"])
        self.assertEqual(week.take_moves(self.WEDNESDAY), [])
        gone = week.Move("Sort the photos", date(2026, 10, 6), None, "no room")
        self.assertEqual(week.describe(gone, self.WEDNESDAY),
                         "Sort the photos: Tuesday 06/10 → out of the week (no room in the week); it waits in the open tasks")

    def test_work_does_not_fill_the_day(self):
        task("Write the report", tags="\n- work")
        task("Plan the sprint", tags="[work/meeting]")
        task("Balance work-life", tags="[work-life]")
        self.assertEqual(tasks.work_tasks(), {"Write the report", "Plan the sprint"})


class TestSync(WeekBase):
    def test_an_uncheck_in_the_week_stays(self):
        note = task("Book the doctor")
        path = self.week_file(Tue="\n> - [x] [[Book the doctor]] (30m)")
        tasks.sync(self.today)
        self.assertEqual(fields(note)["done"], "true")
        check(path, "Book the doctor", False)
        tasks.sync(self.today)
        self.assertEqual(fields(note)["done"], "false")
        tasks.sync(self.today)
        self.assertIn("> - [ ] [[Book the doctor]]", text(path))

    def test_an_uncheck_in_the_table_stays(self):
        note = task("Book the doctor")
        path = self.week_file(Tue="\n> - [x] [[Book the doctor]] (30m)")
        tasks.sync(self.today)
        notes.set_field(note, "done", "false")
        tasks.sync(self.today)
        self.assertIn("> - [ ] [[Book the doctor]]", text(path))
        tasks.sync(self.today)
        self.assertEqual(fields(note)["done"], "false")

    def test_an_old_task_opened_again_stays_open(self):
        note = task("Fix the bike")
        old = self.week_file(Tue="\n> - [x] [[Fix the bike]] (1h)")
        tasks.sync(self.today)
        notes.set_field(note, "done", "false")  # weeks later, the bike broke again
        later = date(2026, 11, 2)
        tasks.sync(later)
        tasks.sync(later)
        self.assertEqual(fields(note)["done"], "false")
        self.assertIn("> - [x] [[Fix the bike]]", text(old))  # history stays

    def test_a_task_in_sessions(self):
        note = task("Study pointers")
        path = self.week_file(Mon="\n> - [ ] [[Study pointers]] (1h)", Wed="\n> - [ ] [[Study pointers]] (1h)")
        tasks.sync(self.today)
        check(path, "Study pointers")
        tasks.sync(self.today)
        self.assertEqual(fields(note)["done"], "false")
        self.assertIn("> - [ ] [[Study pointers]]", text(path))
        check(path, "Study pointers")
        tasks.sync(self.today)
        self.assertEqual(fields(note)["done"], "true")

    def test_a_check_on_sunday_closes_the_copy_of_next_week(self):
        note = task("Call mom")
        old = self.week_file(Sun="\n> - [ ] [[Call mom]] (30m)")
        new = self.other_week(Mon="\n> - [ ] [[Call mom]] (30m)")
        tasks.sync(date(2026, 10, 11))
        check(old, "Call mom")
        tasks.sync(date(2026, 10, 11))
        self.assertEqual(fields(note)["done"], "true")
        self.assertIn("> - [x] [[Call mom]]", text(new))

    def test_a_late_repeating_task_comes_back_after_today(self):
        note = task("Water the plants", date(2026, 8, 31), repeat="weekly")
        self.week_file(Mon="\n> - [x] [[Water the plants]] (15m)")
        tasks.sync(self.today)
        self.assertEqual((fields(note)["due"], fields(note)["done"]), ("2026-10-12", "false"))

    def test_a_repeating_task_with_no_due_date_starts_today(self):
        note = task("Gym", repeat="weekly")
        path = self.week_file(Mon="\n> - [x] [[Gym]] (1h)")
        tasks.sync(self.today)
        self.assertEqual((fields(note)["due"], fields(note)["done"]), ("2026-10-12", "false"))
        self.assertIn("> - [x] [[Gym]]", text(path))

    def test_a_check_in_the_table_closes_only_its_occurrence(self):
        note = task("Water the plants", date(2026, 10, 11), repeat="weekly")
        old = self.week_file(Sun="\n> - [ ] [[Water the plants]] (15m)")
        new = self.other_week(Sun="\n> - [ ] [[Water the plants]] (15m)")
        notes.set_field(note, "done", "true")
        tasks.sync(date(2026, 10, 11))
        self.assertEqual(fields(note)["due"], "2026-10-18")
        self.assertIn("> - [x] [[Water the plants]]", text(old))
        self.assertIn("> - [ ] [[Water the plants]]", text(new))

    def test_two_occurrences_closed_in_the_same_week(self):
        note = task("Water the plants", date(2026, 10, 4), repeat="weekly")
        last = self.other_week("2026-W40", [("Mon", "28/09"), ("Tue", "29/09"), ("Wed", "30/09"), ("Thu", "01/10"),
                                            ("Fri", "02/10"), ("Sat", "03/10"), ("Sun", "04/10")],
                               Sun="\n> - [ ] [[Water the plants]] (15m)")
        this = self.week_file(Mon="\n> - [ ] [[Water the plants]] (15m)")
        tuesday = date(2026, 10, 6)
        tasks.sync(tuesday)
        check(last, "Water the plants")
        tasks.sync(tuesday)
        self.assertEqual(fields(note)["due"], "2026-10-11")
        check(this, "Water the plants")
        tasks.sync(tuesday)
        self.assertEqual((fields(note)["due"], fields(note)["done"]), ("2026-10-18", "false"))

    def test_a_check_by_mistake_is_taken_back(self):
        note = task("Pay the rent", date(2026, 10, 10), repeat="monthly")
        notes.set_field(note, "moved", "2")
        path = self.week_file(Sat="\n> - [ ] [[Pay the rent]] (15m)")
        tasks.sync(self.today)
        check(path, "Pay the rent")
        tasks.sync(self.today)
        self.assertEqual((fields(note)["due"], fields(note)["moved"]), ("2026-11-10", "0"))
        check(path, "Pay the rent", False)
        tasks.sync(self.today)
        fm, body = notes.read(note)
        self.assertEqual((fm["due"], fm["moved"], fm["done"]), ("2026-10-10", "2", "false"))
        self.assertNotIn("History", body)

    def test_a_weekly_habit_is_never_stuck(self):
        note = task("Water the plants", date(2026, 10, 6), repeat="weekly")
        notes.set_field(note, "moved", "2")
        notes.set_field(note, "postponed", "1")
        notes.set_field(note, "done", "true")
        tasks.sync(date(2026, 10, 7))
        self.assertEqual((fields(note)["moved"], fields(note)["postponed"]), ("0", "0"))

    def test_a_monthly_task_keeps_its_day(self):
        note = task("Pay the card", date(2027, 1, 31), repeat="monthly")
        for today in (date(2027, 1, 31), date(2027, 2, 28)):
            notes.set_field(note, "done", "true")
            tasks.sync(today)
        self.assertEqual(fields(note)["due"], "2027-03-31")
        self.assertEqual(tasks.next_date(date(2027, 2, 28), "monthly", 31), date(2027, 3, 31))


class TestImages(WeekBase):
    def test_emoji_are_left_out(self):
        self.assertEqual(week_image.clean("Comprar presente 🎁 para a mãe"), "Comprar presente para a mãe")
        self.assertEqual(week_image.clean("⚠️ due → today"), "⚠ due → today")
        self.assertEqual(week_image.clean("🎁"), "·")

    def test_a_long_day_fits_telegram(self):
        many = "".join(f"\n> - [ ] [[Task number {i} with a name long enough to wrap]] (15m)" for i in range(80))
        self.week_file(Wed=many, Thu=many)
        for png in (week_image.week_png(date(2026, 10, 7)), week_image.day_png(date(2026, 10, 7))):
            self.assertLessEqual(sum(Image.open(io.BytesIO(png)).size), 10000)

    def test_dates_and_title_in_the_language_of_the_user(self):
        day = date(2026, 10, 7)
        self.assertEqual((week_image.day_month(day), week_image.full_date(day)), ("10/07", "10/07/2026"))
        self.in_language("pt-BR")
        self.assertEqual((week_image.day_month(day), i18n.t("image.title")), ("07/10", "semana"))

    def test_work_and_overloaded_days(self):
        task("Write the report", tags="[work]")
        self.week_file(Wed="\n> - [ ] [[Write the report]] (3h)\n> - [ ] [[Read]] (5h)")
        w = week.read(self.today)
        lines = week_image._lines(w.days[2], {}, self.today, w.days[-1].date, tasks.work_tasks())
        self.assertEqual([(line.name, line.work) for line in lines], [("Write the report", True), ("Read", False)])
        self.assertEqual(week_image._hours(w.days[2], tasks.work_tasks()), ("5h/4h", True))
        self.assertEqual(week_image._duration(lines[0]), "(3h)")


def git(folder, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, encoding="utf-8")
    return (result.stdout + result.stderr).strip()


class TestSyncConflicts(WeekBase):
    """The server (the vault) and the phone (another clone) change the same files at the same time."""

    def setUp(self):
        super().setUp()
        root = config.VAULT.parent
        self.remote, self.phone = root / "remote.git", root / "phone"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        git(config.VAULT, "init", "-q", "-b", "main")
        git(config.VAULT, "config", "core.autocrlf", "false")
        git(config.VAULT, "remote", "add", "origin", str(self.remote))
        self.sent = []
        old = telegram_api.send, config.QUIET_FROM, config.QUIET_UNTIL
        telegram_api.send = lambda text, buttons=None, chat_id=None: self.sent.append(text) or 1
        config.QUIET_FROM = config.QUIET_UNTIL = 0  # no quiet hours: the notice goes out at once
        self.addCleanup(lambda: (setattr(telegram_api, "send", old[0]), setattr(config, "QUIET_FROM", old[1]),
                                 setattr(config, "QUIET_UNTIL", old[2])))

    def start(self) -> None:
        """The first commit of the server, and the phone's copy of it."""
        vault.publish("start")
        git(config.VAULT, "push", "-q", "-u", "origin", "main")
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.phone)], check=True)
        git(self.phone, "config", "core.autocrlf", "false")

    def on_phone(self, path: str, change) -> None:
        """The user edits a file on the phone, and Obsidian Git commits and pushes it."""
        file = self.phone / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(change(file.read_text(encoding="utf-8") if file.exists() else "").encode("utf-8"))
        git(self.phone, "add", "-A")
        git(self.phone, "-c", "user.name=Ada", "-c", "user.email=ada@x", "commit", "-q", "-m", "vault backup")
        self.assertEqual(git(self.phone, "push", "-q"), "")

    def in_step(self) -> None:
        git(config.VAULT, "fetch", "-q")
        self.assertEqual(git(config.VAULT, "rev-list", "--left-right", "--count", "HEAD...@{u}").split(), ["0", "0"])
        self.assertEqual(git(config.VAULT, "status", "--porcelain"), "")
        self.assertIn("Fast-forward", git(self.phone, "pull", "--no-rebase"))

    def test_the_integrator_and_a_check_on_the_phone(self):
        task("Book the doctor")
        task("Buy bread")
        path = self.week_file(Wed="\n> - [ ] [[Book the doctor]] (30m)")
        self.start()
        vault.pull()  # the Integrator starts; the model works for a minute
        rel = vault.rel(path)
        self.on_phone(rel, lambda t: t.replace("- [ ] [[Book the doctor]]", "- [x] [[Book the doctor]]"))
        write(path, text(path).replace("> [!day] Wed 07/10 · 0h of 4h\n> - [ ] [[Book the doctor]] (30m)",
                                       "> [!day] Wed 07/10 · 45m of 4h\n> - [ ] [[Book the doctor]] (30m)\n> - [ ] [[Buy bread]] (15m)"))
        vault.publish("integrator: 1 engram(s)")
        merged = git(config.VAULT, "show", f"@{{u}}:{rel}")
        self.assertIn("> [!day] Wed 07/10 · 45m of 4h\n> - [x] [[Book the doctor]] (30m)\n> - [ ] [[Buy bread]] (15m)\n\n", merged)
        self.assertEqual(merged.count("Book the doctor"), 1)
        self.assertNotIn("<<<<", merged)
        self.in_step()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("2026-W41", self.sent[0])

    def test_a_move_on_the_server_and_a_check_on_the_phone(self):
        task("Book the doctor")
        task("Buy bread")
        path = self.week_file(Tue="\n> - [ ] [[Book the doctor]] (30m)\n> - [ ] [[Buy bread]] (15m)")
        self.start()
        vault.pull()
        self.on_phone(vault.rel(path), lambda t: t.replace("- [ ] [[Buy bread]]", "- [x] [[Buy bread]]"))
        week.reschedule(date(2026, 10, 7))
        vault.publish("messenger: records updated")
        merged = text(path)
        # Git decides on which day the checked line lands. What matters: one line each, the move and the check.
        self.assertIn("> - [x] [[Buy bread]] (15m)\n", merged)
        self.assertIn("> [!day] Wed 07/10", merged.split("> - [ ] [[Book the doctor]] (30m)\n")[0])
        self.assertEqual((merged.count("Book the doctor"), merged.count("Buy bread")), (1, 1))
        self.assertNotIn("<<<<", merged)
        self.in_step()

    def test_a_note_changed_on_both_sides_keeps_a_copy(self):
        idea = config.VAULT / "3. 📚 Knowledge" / "Idea.md"
        write(idea, "---\ntype: knowledge\n---\nAn offline mode.\n")
        self.start()
        vault.pull()
        self.on_phone(vault.rel(idea), lambda t: t.replace("An offline mode.", "An offline mode for the app."))
        write(idea, text(idea).replace("An offline mode.", "An offline mode, as in Notion."))
        vault.publish("reviewer: idea")
        self.assertIn("An offline mode for the app.", text(idea))
        copies = [p for p in idea.parent.glob("Idea (conflict copy *).md")]
        self.assertEqual(len(copies), 1)
        self.assertIn("An offline mode, as in Notion.", text(copies[0]))
        self.in_step()
        self.assertIn(f'My version is in "{copies[0].stem}"', self.sent[0])

    def test_lines_added_on_both_sides_and_fields_changed_on_both_sides(self):
        log_file = config.LOGS / "Log 2026-10-07.md"
        write(log_file, "---\ntype: log\n---\n- 09:00 · integrator · one\n")
        note = task("Book the doctor")
        self.start()
        vault.pull()
        self.on_phone(vault.rel(log_file), lambda t: t + "- 09:05 · my own line\n")
        self.on_phone(vault.rel(note), lambda t: t.replace("done: false", "done: true"))
        write(log_file, text(log_file) + "- 09:06 · messenger · two\n")
        notes.set_field(note, "moved", "2")
        vault.publish("messenger: records updated")
        self.assertTrue(text(log_file).endswith("- 09:00 · integrator · one\n- 09:05 · my own line\n- 09:06 · messenger · two\n"))
        self.assertEqual((fields(note)["done"], fields(note)["moved"]), ("true", "2"))
        self.assertEqual(list(config.VAULT.rglob("*conflict copy*")), [])
        self.in_step()

    def test_a_push_that_failed_goes_out_later(self):
        write(config.VAULT / "_Seahorse.md", "---\ntype: summary\n---\nOld.\n")
        self.start()
        git(config.VAULT, "remote", "set-url", "origin", str(self.remote) + "-gone")
        write(config.VAULT / "_Seahorse.md", "---\ntype: summary\n---\nNew.\n")
        self.assertTrue(vault.publish("integrator: offline"))
        git(config.VAULT, "remote", "set-url", "origin", str(self.remote))
        self.assertFalse(vault.publish("messenger: records updated"))  # nothing new, but the old commit goes out
        self.in_step()


class TestAgents(WeekBase):
    """The morning summary and the weekly plan, with a fake AI and a fake Telegram."""

    def setUp(self):
        super().setUp()
        from seahorse import agents, llm
        self.agents, self.prompts, self.sent, self.photos, self.answers = agents, [], [], [], []
        vault.git("init", "-q", "-b", "main")
        write(config.DASHBOARD, "---\ntype: dashboard\n---\n![[2026-W40]]\n")

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 10, 7, 10, 30, tzinfo=TZ)

        def fake_run(text, role, *args, **kwargs):
            self.prompts.append((role, text))
            answer = self.answers.pop(0) if self.answers else "ok"
            if callable(answer):
                answer = answer()
            return llm.Answer(answer or "", None, answer is None)

        saved = (agents.datetime, agents.integrator, llm.run, vault.validate, telegram_api.send, telegram_api.send_photo)
        agents.datetime, agents.integrator, llm.run = Clock, lambda: None, fake_run
        vault.validate = lambda: (True, "")
        telegram_api.send = lambda text, buttons=None, chat_id=None: self.sent.append(text) or 7
        telegram_api.send_photo = lambda image, caption="": self.photos.append(image) or 8

        def restore():
            agents.datetime, agents.integrator, llm.run, vault.validate, telegram_api.send, telegram_api.send_photo = saved
        self.addCleanup(restore)

    def unclear(self, minute: int, asked: int = 0):
        when = datetime(2026, 10, 3, 9, minute, tzinfo=TZ)
        path = vault.new_engram("strengths test", "telegram-text", when, {"asked_in_summary": str(asked)} if asked else None)
        notes.set_field(path, "state", "unclear")
        return path

    def test_the_morning_tells_what_moved_and_sets_old_doubts_aside(self):
        task("Book the doctor")
        self.week_file(Mon="\n> - [ ] [[Book the doctor]] (30m)")
        week.reschedule(date(2026, 10, 6))  # the Messenger, at midnight on Tuesday
        old, new = self.unclear(1, asked=2), self.unclear(2)
        self.answers = ["☀️ Wednesday"]
        self.agents.morning()
        role, prompt = self.prompts[0]
        self.assertEqual(role, "morning")
        self.assertIn("- Book the doctor: Monday 05/10 → today", prompt)
        self.assertIn(f"Unclear engrams:\n- {vault.rel(new)}\n", prompt)
        self.assertIn(f"Code set them aside now:\n- {vault.rel(old)}\n", prompt)
        self.assertIn("today is 10/07", prompt)  # MM/DD for an English user
        self.assertEqual((fields(old)["state"], fields(new)["asked_in_summary"]), ("discarded", "1"))
        self.assertEqual((self.sent, len(self.photos)), (["☀️ Wednesday"], 1))
        self.assertIn("![[2026-W41]]", text(config.DASHBOARD))
        self.assertEqual(week.take_moves(date(2026, 10, 7)), [])

    def test_a_failed_morning_text_still_sends_the_image_and_keeps_the_moves(self):
        task("Book the doctor")
        self.week_file(Mon="\n> - [ ] [[Book the doctor]] (30m)")
        old = self.unclear(1, asked=2)
        self.answers = [None]
        self.agents.morning()
        self.assertEqual(len(self.photos), 1)
        self.assertIn("Morning summary", self.sent[0])
        self.assertEqual(fields(old)["state"], "unclear")  # the user never saw the summary
        self.assertEqual([m.name for m in week.take_moves(date(2026, 10, 7))], ["Book the doctor"])

    def test_the_morning_waits_for_the_end_of_the_quiet_hours(self):
        self.assertEqual(self.agents._quiet_left(datetime(2026, 10, 7, 7, 0, tzinfo=TZ)), 3600)
        self.assertEqual(self.agents._quiet_left(datetime(2026, 10, 7, 8, 0, tzinfo=TZ)), 0)
        old = config.QUIET_FROM, config.QUIET_UNTIL
        config.QUIET_FROM, config.QUIET_UNTIL = 22 * 60, 6 * 60
        self.addCleanup(lambda: (setattr(config, "QUIET_FROM", old[0]), setattr(config, "QUIET_UNTIL", old[1])))
        self.assertEqual(self.agents._quiet_left(datetime(2026, 10, 7, 23, 0, tzinfo=TZ)), 7 * 3600)

    def test_the_week_after_any_day(self):
        monday = date(2026, 10, 12)
        self.assertEqual([self.agents.week_after(date(2026, 10, d)) for d in (5, 7, 11)], [monday] * 3)

    def broken_plan(self, cards: int):
        """The Planner writes the week again, with only some of its cards: the first part, up to `cards` blocks."""
        def write_it():
            path = self.other_week(Mon="\n> - [ ] [[Gym]] (1h)")
            write(path, "\n\n".join(text(path).split("\n\n")[:cards]))
            return "🗓️ Week 12/10 to 18/10"
        return write_it

    def test_the_planner_gets_a_second_try(self):
        self.answers = [self.broken_plan(3), self.broken_plan(8)]
        self.assertEqual(self.agents.plan_week(date(2026, 10, 12)), "🗓️ Week 12/10 to 18/10")
        self.assertEqual([role for role, _ in self.prompts], ["planner", "planner"])
        self.assertIn("- no card for Thu 15/10", self.prompts[1][1])
        self.assertEqual(self.sent, ["🗓️ Week 12/10 to 18/10"])
        self.assertEqual(week.problems(config.WEEKS / "2026-W42.md"), [])

    def test_a_planner_that_fails_twice_is_reported_and_code_repairs_the_week(self):
        self.answers = [self.broken_plan(3), self.broken_plan(3)]
        self.agents.plan_week(date(2026, 10, 12))
        self.assertEqual(self.sent[0], "🗓️ Week 12/10 to 18/10")
        self.assertIn("no card for Thu 15/10", self.sent[1])
        path = config.WEEKS / "2026-W42.md"
        self.assertEqual(week.problems(path), [])
        self.assertIn("> [!day] Mon 12/10 · 1h of 4h\n> - [ ] [[Gym]] (1h)\n", text(path))

    def test_planning_this_week_again(self):
        self.answers = ["🗓️ again"]
        self.agents.plan_again(date(2026, 10, 7))
        self.assertIn("1. 🎯 Intention/Week/2026-W41.md`, which starts on 2026-10-05", self.prompts[0][1])
        self.assertIn("![[2026-W41]]", text(config.DASHBOARD))


class TestMigration(Base):
    def test_the_task_template_gets_the_planned_day(self):
        from seahorse import migrations
        template = config.SYSTEM / "_Templates" / "Task.md"
        write(template, "---\ntype: task\ndone: false\nestimate: 30m\ndue:\nrepeat:\ntags: []\n---\n")
        migrations._to_1_3_0_week()
        migrations._to_1_3_0_week()
        self.assertEqual(text(template), "---\ntype: task\ndone: false\nestimate: 30m\ndue:\nplanned:\nrepeat:\ntags: []\n---\n")
        write(template, "---\ntype: task\nmine: yes\n---\nMy own body.\n")  # a template the user changed
        migrations._to_1_3_0_week()
        self.assertEqual(text(template), "---\ntype: task\nmine: yes\nplanned:\n---\nMy own body.\n")
        self.assertIn(("1.3.0", migrations._to_1_3_0_week), migrations.MIGRATIONS)
        new = config.TEMPLATE / "6. ⚙️ System" / "_Templates" / "Task.md"
        self.assertIn("\ndue:\nplanned:\n", new.read_text(encoding="utf-8"))
