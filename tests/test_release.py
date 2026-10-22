from test_seahorse import Base, write, task, message, remove  # sets up the throwaway vault

"""Fixes from the reviews of 1.3.0: an AI outage, the unclear captures of the summary, free-text lines of the week,
a deleted checked line, the bot token kept out of the logs; then the vault sync, the link to Obsidian, the day
titles, the Integrator runs, the durations and the Done of Now, the birthday dates, the texts and the retro."""
import logging
import unittest
from datetime import date, datetime

import requests

from seahorse import agents, config, i18n, messenger, notes, tasks, telegram_api, vault, week
from seahorse.llm import Answer

TZ = config.TIMEZONE


class TestOutage(unittest.TestCase):
    def test_a_limit_or_a_login_is_an_outage(self):
        for text in ("You've hit your session limit · resets 11pm", "API Error: 529 Overloaded",
                     "Invalid API key · Please run /login", "the claude command is not installed",
                     "Claude AI usage limit reached|1761091200"):
            self.assertTrue(agents.outage(Answer(text, None, True)), text)

    def test_a_failed_capture_is_not_an_outage(self):
        self.assertFalse(agents.outage(Answer("error_max_turns", None, True)))
        self.assertFalse(agents.outage(Answer("timed out after 3600s", None, True)))
        self.assertFalse(agents.outage(Answer("usage limit", None, False)))  # a run that worked


class TestCaptures(Base):
    def capture(self, minute: int, state: str = "pending") -> object:
        path = vault.new_engram(f"capture {minute}", "telegram-text", datetime(2026, 10, 5, 9, minute, tzinfo=TZ))
        notes.set_field(path, "state", state)
        return path

    def run_integrator(self, text: str) -> None:
        old = agents.llm.run
        agents.llm.run = lambda *a, **k: Answer(text, None, True)
        try:
            agents.integrator()
        finally:
            agents.llm.run = old

    def quiet(self, module, *names) -> None:
        """Turns functions with side effects outside the vault into no-ops for this test."""
        for name in names:
            old = getattr(module, name)
            setattr(module, name, lambda *a, **k: None)
            self.addCleanup(setattr, module, name, old)

    def test_an_outage_counts_no_attempt(self):
        path = self.capture(1)
        self.quiet(telegram_api, "send", "edit")
        self.quiet(vault, "pull", "publish")
        self.quiet(agents, "_after_writing", "_report_failure")
        for _ in range(4):
            self.run_integrator("You've hit your session limit · resets 11pm")
        fm = notes.read(path)[0]
        self.assertEqual((fm.get("state"), fm.get("attempts")), ("pending", None))
        self.run_integrator("error_max_turns")
        self.assertEqual(notes.read(path)[0].get("attempts"), "1")

    def test_the_summary_counts_only_the_captures_it_asks_about(self):
        paths = [self.capture(m, "unclear") for m in (1, 2, 3)]
        ask, drop = agents._unclear_for_summary()
        self.assertEqual((ask, drop), (paths[:2], []))
        agents._after_summary(ask, drop)
        self.assertEqual([notes.read(p)[0].get("asked_in_summary") for p in paths], ["1", "1", None])


class TestWeek(Base):
    def test_a_free_text_line_is_never_lost(self):
        for name in ("Wed task", "Thu task", "Fri task", "Sat task", "Sun task"):
            path = task(name)
            notes.set_field(path, "estimate", "4h")
        path = self.week_file(Mon="\n> - [ ] Clean the garage (3h)",
                              **{d: f"\n> - [ ] [[{d} task]] (4h)" for d in ("Wed", "Thu", "Fri", "Sat", "Sun")})
        week.reschedule(date(2026, 10, 7))
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count("Clean the garage"), 1)
        self.assertGreater(text.index("Clean the garage"), text.index("Sun 11/10"))


class TestChecks(Base):
    def test_a_deleted_checked_line_does_not_open_the_task(self):
        path = task("Study")
        week_path = self.week_file(Mon="\n> - [x] [[Study]] (15m)", Tue="\n> - [x] [[Study]] (15m)")
        tasks.sync(self.today)
        self.assertEqual(notes.read(path)[0].get("done"), "true")
        write(week_path, week_path.read_text(encoding="utf-8").replace("> - [x] [[Study]] (15m)\n", "", 1))
        tasks.sync(self.today)
        self.assertEqual(notes.read(path)[0].get("done"), "true")


class TestToken(unittest.TestCase):
    def test_a_network_error_never_logs_the_token(self):
        old_post, old_token = requests.post, config.TELEGRAM_TOKEN
        config.TELEGRAM_TOKEN = "123456:SECRET-TOKEN"

        def fail(url, **kwargs):
            raise requests.ConnectionError(f"Max retries exceeded with url: {url}")
        requests.post = fail
        self.addCleanup(lambda: (setattr(requests, "post", old_post), setattr(config, "TELEGRAM_TOKEN", old_token)))
        with self.assertLogs("seahorse.telegram_api", level=logging.ERROR) as logs:
            self.assertIsNone(telegram_api.send("hello"))
            self.assertIsNone(telegram_api.send_photo(b"png"))
        self.assertNotIn("SECRET-TOKEN", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()


class TestNowTrigger(unittest.TestCase):
    """A message about an appointment, or a follow-up in a chat, stays a capture. Free time asks for a task."""
    CAPTURES = {
        "pt-BR": ["tenho dentista 14h", "tenho reunião 15h30", "tenho aula 19h", "tenho prova 8h", "tenho 1h reunião",
                  "o que eu faço?", "o que eu faço primeiro", "o que eu faço então?", "tenho 2h de reunião amanhã",
                  "tenho 30 minutos pra entregar o relatório", "livre amanhã às 15h"],
        "es": ["tengo médico 15h", "tengo reunión 10h", "libre a las 15h", "qué hago?"],
        "en": ["I have a 2h meeting", "I have a 30 min call", "got gym 7h", "I got 2h sleep", "what do I do here",
               "free tonight?"],
    }
    REQUESTS = {
        "pt-BR": {"tenho 30 min": 30, "tenho meia hora livre": 30, "1h livre": 60, "o que eu faço agora?": 0},
        "es": {"tengo media hora": 30, "qué hago ahora?": 0},
        "en": {"I have 20 minutes": 20, "what should I do now?": 0, "I have 30 min, what should I do?": 30},
    }

    def test_appointments_and_follow_ups_stay_captures(self):
        from seahorse import now
        for language, texts in self.CAPTURES.items():
            for text in texts:
                self.assertIsNone(now.trigger(text, language), f"{language}: {text}")

    def test_free_time_still_asks_for_a_task(self):
        from seahorse import now
        for language, texts in self.REQUESTS.items():
            for text, minutes in texts.items():
                self.assertEqual(now.trigger(text, language), minutes, f"{language}: {text}")


class TestNowInAConversation(Base):
    """"tenho 30 min" as a Reply, or inside a chat with Seahorse, answers that conversation: it is a capture."""

    def ask(self, msg, ctx) -> bool:
        import asyncio
        from seahorse import bot
        return asyncio.run(bot._asks_for_now(__import__("test_telegram").update(msg), ctx))

    def test_a_reply_or_a_chat_is_not_a_request(self):
        from datetime import timedelta
        from test_telegram import FakeMessage, context
        alone = FakeMessage(5, "tenho 30 min")
        self.assertTrue(self.ask(alone, context()))
        reply = FakeMessage(6, "tenho 30 min", reply_to_message=FakeMessage(4, "⏰ Pagar o IPVA"))
        self.assertFalse(self.ask(reply, context()))
        chatting = context()
        chatting.bot_data["session_at"] = datetime.now() - timedelta(minutes=5)
        self.assertFalse(self.ask(FakeMessage(7, "tenho 30 min"), chatting))


# ---- second review of 1.3.0 ---------------------------------------------------------------------------------

from test_week import SyncBase, git  # noqa: E402


class TestVaultMerge(SyncBase):
    """Sync between the server and the phone: two renames, an edit of the last line, the tasks after a merge."""

    def test_a_note_renamed_on_both_sides_leaves_no_empty_file(self):
        folder = config.VAULT / "3. 📚 Knowledge"
        write(folder / "Old name.md", "---\ntype: knowledge\n---\nbody\n")
        self.start()
        vault.pull()
        git(self.phone, "mv", "3. 📚 Knowledge/Old name.md", "3. 📚 Knowledge/Phone name.md")
        git(self.phone, "-c", "user.name=Ada", "-c", "user.email=ada@x", "commit", "-q", "-m", "vault backup")
        self.assertEqual(git(self.phone, "push", "-q"), "")
        git(config.VAULT, "mv", "3. 📚 Knowledge/Old name.md", "3. 📚 Knowledge/Agent name.md")
        vault.publish("reviewer: a better name")
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["Agent name.md", "Phone name.md"])
        self.assertNotIn("Old name", git(config.VAULT, "-c", "core.quotepath=off", "ls-files"))
        self.in_step()

    def test_an_edit_of_the_last_line_is_not_an_addition(self):
        base = "---\ntype: knowledge\n---\nI like pizza\n"
        merged = vault.merge_note(base, base.replace("pizza", "pizza and pasta"), base + "And sushi.\n")
        self.assertNotIn("\n and pasta", merged or "")
        self.assertEqual(vault.merge_note(base, base + "- agent line\n", base + "- phone line\n"),
                         base + "- phone line\n- agent line\n")

    def test_the_tasks_synced_after_a_merge_are_committed_as_seahorse(self):
        note = task("Book the doctor")
        path = self.week_file(Mon="\n> - [ ] [[Book the doctor]] (15m)")
        idea = config.VAULT / "3. 📚 Knowledge" / "Idea.md"
        write(idea, "---\ntype: knowledge\n---\nline 1\nline 2\nline 3\n")
        tasks.sync(self.today)
        self.start()
        vault.pull()
        self.on_phone(vault.rel(path), lambda t: t.replace("- [ ] [[Book the doctor]]", "- [x] [[Book the doctor]]"))
        self.on_phone(vault.rel(idea), lambda t: t.replace("line 2", "line 2 phone"))
        write(idea, idea.read_text(encoding="utf-8").replace("line 2", "line 2 agent"))
        vault.publish("reviewer: idea")
        self.assertEqual(notes.read(note)[0]["done"], "true")
        self.assertEqual(git(config.VAULT, "log", "-1", "--format=%an", "--", vault.rel(note)), "Seahorse")
        self.in_step()


class TestOpenLink(Base):
    def test_the_note_path_goes_after_the_hash(self):
        from urllib.parse import parse_qs, urlparse
        from seahorse import messenger
        note = config.VAULT / "3. 📚 Knowledge" / "My idea.md"
        link = urlparse(messenger.obsidian_link(note))
        self.assertEqual((link.query, parse_qs(link.fragment)["file"]), ("", ["3. 📚 Knowledge/My idea"]))


class TestDayTitles(Base):
    def test_the_words_after_a_day_name_stay_in_the_title(self):
        path = self.week_file()
        old = path.read_text(encoding="utf-8")
        write(path, old.replace("> [!day] Tue 06/10 · 0h of 4h", "> [!day] Tuesday - dentist day, bring the x-rays")
              .replace("> [!day] Wed 07/10 · 0h of 4h", "> [!day] Wednesday"))
        self.assertTrue(week.refresh(self.today))
        text = path.read_text(encoding="utf-8")
        self.assertIn("> [!day] Tue 06/10 · 0h of 4h dentist day, bring the x-rays\n", text)
        self.assertIn("> [!day] Wed 07/10 · 0h of 4h\n", text)
        self.assertFalse(week.refresh(self.today))  # the next refresh changes nothing


class TestIntegratorRuns(Base):
    """The Integrator never runs twice at once, and captures.json never loses a line."""

    def test_a_waiting_integrator_never_comes_earlier(self):
        from seahorse import bot
        from test_telegram import context
        ctx = context()
        delay = config.INTEGRATOR_DELAY_SECONDS
        bot._schedule_integrator(ctx, bot.CLASSIFY_SECONDS)  # a message that may be a question
        bot._schedule_integrator(ctx)  # the next message comes before the check ends
        self.assertEqual([j.when for j in ctx.job_queue.get_jobs_by_name("integrator")], [delay + bot.CLASSIFY_SECONDS])
        bot._schedule_integrator(ctx, bot.CLASSIFY_SECONDS + 30)  # later is still possible
        self.assertEqual([j.when for j in ctx.job_queue.get_jobs_by_name("integrator")],
                         [delay + bot.CLASSIFY_SECONDS + 30])

    def test_a_second_call_returns_at_once_and_the_first_run_goes_again(self):
        import threading
        started, release, runs = threading.Event(), threading.Event(), []

        def batch():
            runs.append("batch")
            if len(runs) == 1:
                started.set()
                release.wait(5)
                return "first", False
            return None, False
        old = agents._integrate_batch, agents.send_held_alerts, agents._update_confirmations
        agents._integrate_batch, agents.send_held_alerts = batch, lambda: None
        agents._update_confirmations = lambda *a: None
        self.addCleanup(lambda: setattr(agents, "_integrate_batch", old[0]) or setattr(agents, "send_held_alerts", old[1])
                        or setattr(agents, "_update_confirmations", old[2]))
        results = []
        first = threading.Thread(target=lambda: results.append(agents.integrator()))
        first.start()
        self.assertTrue(started.wait(5))
        self.assertTrue(agents.integrating())
        self.assertIsNone(agents.integrator())  # the morning summary, while the bot's run goes
        release.set()
        first.join(5)
        self.assertEqual((results, runs), (["first"], ["batch", "batch"]))
        self.assertFalse(agents.integrating())

    def test_captures_json_loses_nothing_with_two_threads(self):
        import threading
        import time
        from pathlib import Path
        old = agents._load

        def slow(path):
            data = old(path)
            time.sleep(0.02)  # a wide gap between the read and the write
            return data
        agents._load = slow
        self.addCleanup(setattr, agents, "_load", old)
        threads = [threading.Thread(target=agents._remember, args=([Path(f"2026-10-05 10{i:02d}.md")],))
                   for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(len(agents._captures()["waiting"]), 8)

    def test_integrate_while_the_morning_run_goes_says_so(self):
        import asyncio
        from seahorse import bot, i18n
        from test_telegram import FakeMessage, Patch, context, update
        msg = FakeMessage(text="/integrate")
        with Patch(agents, integrator=lambda: None, integrating=lambda: True):
            asyncio.run(bot.on_integrate(update(msg), context()))
        self.assertEqual(msg.texts(), [i18n.t("bot.integrating"), i18n.t("chat.integrate_busy")])


class TestNowDurations(unittest.TestCase):
    """"And a half" and "and 30" after hours, in numbers or in words, in the three languages."""
    CASES = {"pt-BR": {"tenho 1h e 30": 90, "tenho 2 horas e meia": 150, "tenho duas horas e meia": 150,
                       "tenho 1 hora e 30 min": 90, "tenho 1h e meia livre": 90},
             "es": {"tengo 1h y media": 90, "tengo dos horas y media": 150, "tengo 1h y 15": 75},
             "en": {"I have 1h and 30": 90, "I have 1h and a half": 90, "I have two hours and a half": 150,
                    "I have an hour and 15": 75}}

    def test_and_a_half_and_minutes_after_hours(self):
        from seahorse import now
        for language, texts in self.CASES.items():
            for text, minutes in texts.items():
                self.assertEqual(now.trigger(text, language), minutes, f"{language}: {text}")
                self.assertEqual(now.parse_minutes(text, language), minutes, f"{language}: {text}")

    def test_the_trigger_stays_strict(self):
        from seahorse import now
        self.assertIsNone(now.trigger("tenho 4h e meia", "pt-BR"))  # more than MOST_MINUTES
        self.assertIsNone(now.trigger("tenho 1h e 30 de reunião", "pt-BR"))  # "I have" takes no other word


class TestBirthdayOrder(Base):
    """"3/4" is the 4th of March for an English user, and the 3rd of April in the other languages."""

    def in_language(self, language: str) -> None:
        old = config.LANGUAGE
        config.LANGUAGE = language
        self.addCleanup(setattr, config, "LANGUAGE", old)

    def test_the_month_first_in_english_and_the_day_first_in_the_other_languages(self):
        from seahorse import people
        for language, ambiguous in (("en", people.Birthday(3, 4)), ("pt-BR", people.Birthday(4, 3)),
                                    ("es", people.Birthday(4, 3))):
            self.in_language(language)
            self.assertEqual(people.parse_birthday("3/4"), ambiguous, language)
            self.assertEqual(people.parse_birthday("13/11"), people.Birthday(11, 13), language)
            self.assertEqual(people.parse_birthday("11/13/1990"), people.Birthday(11, 13, 1990), language)
            self.assertEqual(people.parse_birthday("--04-03"), people.Birthday(4, 3), language)

    def test_lint_warns_about_a_date_that_reads_in_both_orders(self):
        from seahorse import lint, people
        from test_people import person
        vault.create(self.today)
        person("Ana", birthday="3/4", last_contact="02/09/2026")
        person("Bia", birthday="13/11")
        person("Carla", birthday="--04-03", last_contact="2026-09-02")
        errors, warnings, _ = lint.check(config.VAULT)
        folder = people.FOLDER
        self.assertFalse([e for e in errors if "People/" in e])
        self.assertIn(f"{folder}/Ana.md: birthday = '3/4' can be day/month or month/day, write YYYY-MM-DD, or --MM-DD "
                      "when the year is unknown", warnings)
        self.assertIn(f"{folder}/Ana.md: last_contact = '02/09/2026' can be day/month or month/day, write YYYY-MM-DD",
                      warnings)
        self.assertIn(f"{folder}/Bia.md: birthday = '13/11', write YYYY-MM-DD, or --MM-DD when the year is unknown",
                      warnings)
        self.assertFalse([w for w in warnings if "Carla.md" in w and "write" in w])


from test_people import EMPTY_PAGE, PeopleBase, person  # noqa: E402


class TestTexts(PeopleBase):
    def in_language(self, language: str) -> None:
        old = config.LANGUAGE
        config.LANGUAGE = language
        self.addCleanup(setattr, config, "LANGUAGE", old)

    def test_one_day_without_contact_is_singular(self):
        from seahorse import people
        person("Grandma", EMPTY_PAGE, keep_in_touch="1d", last_contact="2026-10-04")
        starts = {"en": "You last talked to Grandma 1 day ago.", "pt-BR": "Faz 1 dia que você não fala com Grandma.",
                  "es": "Hace 1 día que no hablas con Grandma."}
        for language, start in starts.items():
            self.in_language(language)
            ask = people.prepare(self.at(2026, 10, 5, 13, 0))
            self.assertTrue(ask.fallback.startswith(start), ask.fallback)
        self.assertEqual(people.day_count(38), "38 días")

    def test_a_planned_gift_or_call_is_a_sentence(self):
        from seahorse import people
        offer, today = {"task": "Ana", "day": "2026-10-09"}, date(2026, 10, 5)
        for language, gift, call in (("en", "🎁 Ana: planned for Friday, 10/09.", "📅 Ana: planned for Friday, 10/09."),
                                     ("pt-BR", "🎁 Ana: planejada para sexta, 09/10.", "📅 Ana: planejada para sexta, 09/10."),
                                     ("es", "🎁 Ana: planeada para el viernes, 09/10.", "📅 Ana: planeada para el viernes, 09/10.")):
            self.in_language(language)
            self.assertEqual((people._label(offer, "gift", today), people._label(offer, "week", today)), (gift, call))

    def test_the_why_lines_of_now(self):
        from seahorse import i18n
        self.assertEqual(i18n.t("now.why_overdue", "pt-BR", date="02/10"), "O prazo era dia 02/10.")
        self.assertEqual(i18n.t("now.why_planned", "es"), "La elegiste para hoy.")
        self.assertEqual(i18n.t("now.started", "en", minutes=15), "▶️ Go! I'll check back in 15 min.")


from contextlib import contextmanager  # noqa: E402
from datetime import timedelta  # noqa: E402

from test_now import NowBase, make  # noqa: E402
from test_telegram import Patch  # noqa: E402


@contextmanager
def busy_vault(wait: bool = True):
    """An agent holds the vault: a caller that does not wait gets Busy at once."""
    if not wait:
        raise vault.Busy
    yield


class TestNowDone(NowBase):
    """"✅ Done" under the question of Now: one session of a task, and never a wait for the vault."""

    def sessions(self):
        make("Write the report", "3h")
        line = "\n> - [ ] [[Write the report]] (1h)"
        return self.week_file(Mon=line, Wed=line, Fri=line)

    async def done_now(self, text: str = "I have 1h") -> object:
        """Asks for a task, starts it, waits for the question and taps Done."""
        msg = await self.pick(text)
        await self.press("now|start|1", 1001, msg.texts()[0])
        self.clock += timedelta(hours=1)
        await self.fire()
        fid = self.ctx.job_queue.jobs[-1].data
        return await self.press(f"now|done|{fid}", 77, self.sent[-1][0]), fid

    async def test_done_checks_only_the_session_of_the_day(self):
        path = self.sessions()
        note = config.TASKS / "Write the report.md"
        await self.done_now()
        text = path.read_text(encoding="utf-8")
        self.assertIn("> [!day] Mon 05/10 · 1h of 4h\n> - [x] [[Write the report]] (1h)", text)
        self.assertEqual(text.count("- [ ] [[Write the report]]"), 2)  # Wednesday and Friday stay open
        self.assertEqual(notes.read(note)[0]["done"], "false")
        week.check("Write the report", date(2026, 10, 7))  # Wednesday, checked on the phone
        tasks.sync(date(2026, 10, 7))
        self.assertEqual(notes.read(note)[0]["done"], "false")
        self.clock = datetime(2026, 10, 9, 10, 30, tzinfo=TZ)  # Friday: the last session
        await self.done_now()
        self.assertNotIn("- [ ] [[Write the report]]", path.read_text(encoding="utf-8"))
        self.assertEqual(notes.read(note)[0]["done"], "true")

    async def test_a_task_out_of_the_week_is_done_in_its_note(self):
        make("Renew the passport", "30m", due=self.today)
        self.week_file()
        query, _ = await self.done_now("I have 30 min")
        self.assertTrue(query.edits[0][0].endswith(i18n.t("now.done_line")))
        self.assertEqual(notes.read(config.TASKS / "Renew the passport.md")[0]["done"], "true")

    async def test_done_while_an_agent_holds_the_vault(self):
        path = self.sessions()
        note = config.TASKS / "Write the report.md"
        with Patch(vault, lock=busy_vault):
            query, fid = await self.done_now()
            again = await self.press(f"now|done|{fid}", 77, self.sent[-1][0])
        self.assertTrue(query.edits[0][0].endswith("\n\n" + i18n.t("now.done_later")))
        self.assertEqual(again.answers, [i18n.t("chat.already_done")])
        self.assertEqual(self.state()["follow_ups"][fid]["status"], "queued")
        self.assertNotIn("- [x]", path.read_text(encoding="utf-8"))  # nothing done yet
        self.assertEqual([m.reaction for m in self.now_lines()], ["acted", "acted"])
        with Patch(config, QUIET_FROM=0, QUIET_UNTIL=24 * 60 - 1):  # the next Messenger run sends nothing
            messenger.run()
        self.assertIn("> - [x] [[Write the report]] (1h)", path.read_text(encoding="utf-8"))
        self.assertEqual(path.read_text(encoding="utf-8").count("- [ ] [[Write the report]]"), 2)
        self.assertEqual((notes.read(note)[0]["done"], self.state()["follow_ups"][fid]["status"]), ("false", "done"))


from test_retro import RetroBase, commit  # noqa: E402


class TestRetroPlan(RetroBase):
    """Tasks that code took out of the week, when no day had room, still count as planned and not done."""

    def plan(self) -> None:
        for i in range(8):
            notes.set_field(task(f"Task {i}"), "estimate", "2h")
        cards = {"Mon": (0, 1), "Tue": (2, 3), "Wed": (4, 5), "Thu": (6, 7)}
        path = self.week_file(**{day: "".join(f"\n> - [ ] [[Task {i}]] (2h)" for i in pair)
                                 for day, pair in cards.items()})
        self.git_vault()
        commit(datetime(2026, 10, 4, 20, 0, tzinfo=TZ))  # the Planner on Sunday evening
        text = path.read_text(encoding="utf-8")
        write(path, text.replace("- [ ] [[Task 0]]", "- [x] [[Task 0]]").replace("- [ ] [[Task 2]]", "- [x] [[Task 2]]"))
        for day in range(5, 12):
            week.reschedule(date(2026, 10, day))  # the Messenger, day after day
        self.assertLess(path.read_text(encoding="utf-8").count("[[Task "), 8)  # some tasks left the week

    def test_tasks_that_left_the_week_count_as_not_done(self):
        from seahorse import retro
        self.plan()
        stats = retro.compute(retro.monday_of(date(2026, 10, 5)), date(2026, 10, 11))
        self.assertEqual((stats.done, stats.total), (2, 8))
        self.assertEqual(sorted(line.name for line in stats.lines), [f"Task {i}" for i in range(8)])
        commit(datetime(2026, 10, 11, 22, 0, tzinfo=TZ))
        stats = retro.compute(date(2026, 10, 5), date(2026, 10, 12))  # the week is over: git has its last state
        self.assertEqual((stats.done, stats.total), (2, 8))

    def test_without_git_the_week_file_alone(self):
        from seahorse import retro
        self.plan()
        remove(config.VAULT / ".git")
        stats = retro.compute(date(2026, 10, 5), date(2026, 10, 11))
        self.assertEqual(stats.done, 2)
        self.assertLess(stats.total, 8)
