from test_seahorse import Base, write, task, message, remove  # sets up the throwaway vault

"""Fixes from the review of 1.3.0: an AI outage, the unclear captures of the summary, free-text lines of the week,
a deleted checked line, and the bot token kept out of the logs."""
import logging
import unittest
from datetime import date, datetime

import requests

from seahorse import agents, config, notes, tasks, telegram_api, vault, week
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
