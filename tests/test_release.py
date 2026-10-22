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
