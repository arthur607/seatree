from test_seahorse import Base, write, task, message, remove  # sets up the throwaway vault

# Tests of the agents' code: the Integrator's result lines and confirmations, its batches and retries, the
# alerts, the fix runs, lint, the reader of the user's notes, the AI's command line and the vault migration.
# They never call an AI or Telegram: `llm.run` and the Telegram calls are fakes, and an AI call without a fake
# fails the test.
import json
import os
import re
import unittest
from datetime import datetime, timedelta
from unittest import mock

from seahorse import agents, config, i18n, lint, llm, migrations, notes, telegram_api, vault, week_image

TZ = config.TIMEZONE
LISTED = re.compile(r"^- (5\. .+\.md)$", re.M)


def answer(text: str, error: bool = False) -> llm.Answer:
    return llm.Answer(text, None, error)


class AgentBase(Base):
    """A vault in git, no quiet hours, and fakes for the AI and Telegram."""

    def setUp(self):
        super().setUp()
        vault.git("init", "-q", "-b", "main")
        self.edits, self.sent, self.prompts = [], [], []
        self.saved = (llm.run, telegram_api.edit, telegram_api.send, vault.validate, config.QUIET_FROM,
                      config.QUIET_UNTIL, config.LANGUAGE)
        telegram_api.edit = lambda message_id, text, buttons=None: self.edits.append((message_id, text, buttons)) or True
        telegram_api.send = lambda text, buttons=None, chat_id=None: self.sent.append(text) or 1
        vault.validate = lambda: (True, "")
        llm.run = self.no_ai
        config.QUIET_FROM = config.QUIET_UNTIL = 0

    def no_ai(self, *args, **kwargs):
        raise AssertionError("a test called the AI without a fake")

    def tearDown(self):
        (llm.run, telegram_api.edit, telegram_api.send, vault.validate, config.QUIET_FROM, config.QUIET_UNTIL,
         config.LANGUAGE) = self.saved

    def capture(self, text: str, minutes_ago: int, channel: str = "telegram-text", **extra) -> str:
        when = datetime.now(TZ).replace(second=0, microsecond=0) - timedelta(minutes=minutes_ago)
        return vault.new_engram(text, channel, when, extra).stem

    def ai(self, act):
        """A fake AI. `act` gets the engram paths of the prompt and returns the answer."""
        def run(text, role, write=False, session=None, max_turns=80, timeout=3600, web=False):
            self.prompts.append((role, text))
            return act([config.VAULT / p for p in LISTED.findall(text)])
        llm.run = run

    def state(self, name: str, value: str) -> None:
        notes.set_field(vault.engram(name), "state", value)


class TestResultLines(unittest.TestCase):
    def test_the_parser_takes_what_models_write(self):
        text = "\n".join([
            "- 2026-10-05 0814 => Task: eye doctor",
            "[[2026-10-05 0815]] => Book in your reading list: Dune",
            "**2026-10-05 0816** => **Brandão: the story of the card**",
            "`2026-10-05 0817 2 => ? Task or idea?`",
            "5. 💭 Engrams/2026-10/2026-10-05 0818.md -> Journal of Friday",
            "* 2026-10-05 0819 → Filed",
            "2026-10-05 0814 => Brandão: the flushed card",
            "Some other line => not a result",
        ])
        self.assertEqual(agents.parse_results(text), {
            "2026-10-05 0814": "Task: eye doctor | Brandão: the flushed card",
            "2026-10-05 0815": "Book in your reading list: Dune",
            "2026-10-05 0816": "Brandão: the story of the card",
            "2026-10-05 0817 2": "? Task or idea?",
            "2026-10-05 0818": "Journal of Friday",
            "2026-10-05 0819": "Filed",
        })

    def test_one_line_per_part(self):
        result = "Task: buy Brandão's gift (idea: a Torterra card) | Brandão: the story of the flushed card | I am looking into bundles, the answer comes in a few minutes"
        self.assertEqual(telegram_api.capture_text("telegram-voice", False, result),
                         "✓ Voice note saved\n→ Task: buy Brandão's gift (idea: a Torterra card)\n"
                         "→ Brandão: the story of the flushed card\n→ I am looking into bundles, the answer comes in a few minutes")
        self.assertEqual(telegram_api.result_lines("a | b | c | d | e"), ["a", "b", "c", "d; e"])
        self.assertEqual(telegram_api.capture_text("telegram-photo", False, "→ Receipt in the task"), "✓ Photo saved\n→ Receipt in the task")

    def test_full_week_and_doubt_keep_working_with_parts(self):
        full = telegram_api.capture_text("telegram-text", False, "! Task: review the budget (2h) · Friday is full | Ana: the dinner")
        self.assertEqual(full, "✓ Saved\n→ Task: review the budget (2h) · Friday is full\n→ Ana: the dinner\n\n" + i18n.t("capture.week_full"))
        self.assertEqual([d for _, d in telegram_api.capture_buttons("x", "telegram-text", False, " ! a | b")], ["eng|week|x", "eng|backlog|x"])
        doubt = telegram_api.capture_text("telegram-text", False, "Ana: the dinner | ? Is the trip in May or June?")
        self.assertEqual(doubt, i18n.t("capture.doubt", title="✓ Saved\n→ Ana: the dinner", question="Is the trip in May or June?"))


class TestIntegrator(AgentBase):
    def integrate_all(self, results: dict | None = None, state: str = "integrated"):
        def act(paths):
            for p in paths:
                notes.set_field(p, "state", state)
            return answer("\n".join(f"{p.stem} => {(results or {}).get(p.stem, 'Task: x')}" for p in paths))
        self.ai(act)

    def test_batches_oldest_first(self):
        names = [self.capture(f"capture {i}", 30 - i) for i in range(7)]
        self.integrate_all()
        self.assertTrue(agents.integrator())
        listed = [LISTED.findall(text) for _, text in self.prompts if "## Task: Integrator" in text]
        self.assertEqual([len(x) for x in listed], [5, 2])
        self.assertEqual([p.rsplit("/", 1)[1][:-3] for p in listed[0]], names[:5])
        self.assertEqual(vault.engrams("pending"), [])
        self.assertIsNone(agents.integrator())  # safe to call again

    def test_a_batch_with_a_capture_left_behind_stops_the_loop(self):
        for i in range(7):
            self.capture(f"capture {i}", 30 - i)
        def act(paths):
            notes.set_field(paths[0], "state", "integrated")
            return answer(f"{paths[0].stem} => Task: x")
        self.ai(act)
        agents.integrator()
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(notes.read(vault.engrams("pending")[0])[0]["attempts"], "1")

    def test_every_confirmation_is_edited_even_without_a_result_line(self):
        a = self.capture("a", 5, confirmation="101")
        b = self.capture("b", 4, channel="telegram-voice", confirmation="102")
        def act(paths):
            for p in paths:
                notes.set_field(p, "state", "integrated")
            return answer(f"- **[[{a}]]** => Task: buy bread | Journal of today: the market")
        self.ai(act)
        agents.integrator()
        edits = {m: (text, buttons) for m, text, buttons in self.edits}
        self.assertEqual(edits[101][0], "✓ Saved\n→ Task: buy bread\n→ Journal of today: the market")
        self.assertEqual(edits[102][0], "✓ Voice note saved\n→ " + i18n.t("result.filed"))
        self.assertEqual([label for label, _ in edits[102][1]], [i18n.t("button.read"), i18n.t("button.fix")])

    def test_a_capture_that_a_stopped_run_left_is_edited_later(self):
        name = self.capture("buy bread", 5, confirmation="103")
        agents._remember([vault.engram(name)])  # the run started, the model integrated it, then a restart
        self.state(name, "integrated")
        self.assertIsNone(agents.integrator())
        self.assertEqual(self.edits, [(103, "✓ Saved\n→ " + i18n.t("result.filed"), [(i18n.t("button.fix"), f"eng|fix|{name}")])])
        agents.integrator()
        self.assertEqual(len(self.edits), 1)  # once

    def test_a_failed_edit_stops_nothing_and_comes_back(self):
        self.capture("a", 5, confirmation="105")
        self.capture("b", 4, confirmation="106")
        calls = []

        def edit(message_id, text, buttons=None):
            calls.append(message_id)
            if calls == [105]:
                raise ConnectionError("the network is down")
            return True
        telegram_api.edit = edit
        self.integrate_all()
        agents.integrator()
        self.assertEqual(calls, [105, 106])
        agents.integrator()  # nothing pending: the edit that failed goes again
        self.assertEqual(calls, [105, 106, 105])

    def test_three_failed_runs_make_it_unclear_and_tell_once(self):
        with_message = self.capture("pay the gym", 50, confirmation="104")
        without = self.capture("from the computer", 49, channel="claude")
        self.ai(lambda paths: answer("error_max_turns", error=True))
        for _ in range(3):
            agents.integrator()
        for name in (with_message, without):
            fm = notes.read(vault.engram(name))[0]
            self.assertEqual((fm["state"], fm["attempts"]), ("unclear", "3"))
        self.assertEqual(len(self.edits), 1)
        self.assertIn(i18n.t("result.gave_up", tries=3), self.edits[0][1])
        self.assertEqual(len([s for s in self.sent if i18n.t("agent.integrator") in s]), 1)
        self.assertIn(i18n.t("alert.gave_up", count=2, tries=3), self.sent)
        log = (config.LOGS / f"Log {datetime.now(TZ):%Y-%m-%d}.md").read_text(encoding="utf-8")
        self.assertIn(f"[[{with_message}]], [[{without}]]", log)
        self.assertNotIn("\n\n", log)
        self.ai(lambda paths: self.fail("no run after three failures"))
        self.assertIsNone(agents.retry_pending())

    def test_put_in_the_week_keeps_the_first_confirmation(self):
        first = self.capture("review the budget, and dinner with Ana", 10, channel="telegram-voice", confirmation="200")
        self.integrate_all({first: "! Task: review the budget (2h) · Friday is full | Ana: the dinner"})
        agents.integrator()
        self.assertTrue(self.edits[-1][1].endswith(i18n.t("capture.week_full")))
        decision = self.capture(i18n.t("capture.put_anyway"), 5, answers_engram=f"[[{first}]]", confirmation="200")
        self.integrate_all({decision: "Task in the week on Friday, over the limit"})
        agents.integrator()
        message_id, text, buttons = self.edits[-1]
        self.assertEqual(message_id, 200)
        self.assertEqual(text, "✓ Voice note saved\n→ Task: review the budget (2h) · Friday is full\n→ Ana: the dinner\n"
                               "→ Task in the week on Friday, over the limit")
        self.assertEqual([data for _, data in buttons], [f"eng|read|{first}", f"eng|fix|{first}"])

    def test_an_answered_doubt_loses_its_question(self):
        doubt = self.capture("Ana friday", 10, confirmation="300")
        self.integrate_all({doubt: "? Is this a dinner or a task?"}, state="unclear")
        agents.integrator()
        self.assertIn("Is this a dinner or a task?", self.edits[-1][1])
        reply = self.capture("a dinner", 5, answers_engram=f"[[{doubt}]]", confirmation="301")
        def act(paths):
            for name in (reply, doubt):
                self.state(name, "integrated")
            return answer(f"{reply} => Journal of Friday: dinner with Ana")
        self.ai(act)
        agents.integrator()
        edits = {m: text for m, text, _ in self.edits}
        self.assertEqual(edits[300], "✓ Saved\n→ Journal of Friday: dinner with Ana")
        self.assertEqual(edits[301], "✓ Saved\n→ Journal of Friday: dinner with Ana")

    def test_a_reply_to_a_normal_confirmation_does_not_rewrite_it(self):
        first = self.capture("buy a gift for Ana", 10, confirmation="310")
        self.integrate_all({first: "Task: buy a gift for Ana"})
        agents.integrator()
        self.capture("it is for Friday", 5, answers_engram=f"[[{first}]]", confirmation="311")
        self.integrate_all({})
        agents.integrator()
        self.assertEqual([m for m, _, _ in self.edits], [310, 311])

    def test_the_prompt_says_when_a_delivery_arrives(self):
        self.capture("look up Torterra card bundles", 5)
        self.integrate_all()
        agents.integrator()
        self.assertIn("in a few minutes", self.prompts[0][1])
        self.assertEqual(re.findall(r"\$[a-z_]+", self.prompts[0][1]), [])  # every placeholder is filled

    def test_every_agent_writes_dates_in_the_order_of_the_language(self):
        today = datetime.now(config.TIMEZONE).date()
        for language, shown in (("en", f"{today:%m/%d}"), ("pt-BR", f"{today:%d/%m}")):
            config.LANGUAGE = language
            text = agents.request("integrator", engrams="x", delivery_time="20:00")
            self.assertIn(f"as today is written: {shown}.", text)
            self.assertIn(f"as today is written: {shown}.", llm.prompt("question", text="x", context="", **agents.moment()))
        config.LANGUAGE = "en"
        monday = today - timedelta(days=today.weekday())
        planner = agents.request("planner", current_week="a", next_week="b", week_start="c",
                                 date_example=week_image.day_month(monday))
        self.assertIn(week_image.day_month(monday), planner)  # a value of the agent wins over the one of now


class TestAlerts(AgentBase):
    def test_alerts_wait_for_the_end_of_the_quiet_hours(self):
        config.QUIET_FROM, config.QUIET_UNTIL = 0, 24 * 60  # always quiet
        agents._report_failure("Reviewer", "API Error: 500 req_011CUxABCDEF1234")
        agents._report_failure("Reviewer", "API Error: 529 req_022XYZ987654321")  # the same failure
        self.assertEqual(self.sent, [])
        config.QUIET_FROM = config.QUIET_UNTIL = 0
        agents.send_held_alerts()
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0].startswith(f"⚠️ {i18n.t('agent.reviewer')} failed. {i18n.t('alert.next_other')}"))
        self.assertTrue(self.sent[0].endswith("Detail: API Error: 500 req_011CUxABCDEF1234"))
        agents.send_held_alerts()
        self.assertEqual(len(self.sent), 1)

    def test_agent_names_in_the_language_of_the_user(self):
        config.LANGUAGE = "pt-BR"
        agents._report_failure("Morning summary", "timed out after 3600s")
        self.assertTrue(self.sent[0].startswith("⚠️ O resumo da manhã falhou."))
        self.assertNotIn("Morning summary", self.sent[0])

    def test_the_signature_leaves_out_what_changes(self):
        a = agents.failure_signature("API Error: 500 {\"request_id\": \"req_011CUxABCDEF1234\"} see https://status.x/1")
        b = agents.failure_signature("API Error: 529 {\"request_id\": \"req_022XYZ987654321\"} see https://status.y/2")
        self.assertEqual(a, b)
        self.assertNotEqual(a, agents.failure_signature("timed out after 3600s"))

    def test_a_long_detail_is_cut(self):
        agents._report_failure("integrator", "x" * 500)
        self.assertLess(len(self.sent[0]), 400)
        self.assertIn(i18n.t("alert.next_integrator"), self.sent[0])


class TestFixRuns(AgentBase):
    def test_the_same_errors_start_no_new_fix_run(self):
        output = {"text": "ERROR  a.md: broken link [[x]]\n\n1 notes · 1 errors · 0 warnings"}
        vault.validate = lambda: (False, output["text"])
        self.ai(lambda paths: answer("fixed nothing"))
        agents._after_writing()
        agents._after_writing()
        self.assertEqual(len(self.prompts), 1)
        output["text"] = "ERROR  b.md: broken link [[y]]\n\n1 notes · 1 errors · 0 warnings"
        agents._after_writing()
        self.assertEqual(len(self.prompts), 2)
        vault.validate = lambda: (True, "")
        agents._after_writing()
        output["text"] = "ERROR  a.md: broken link [[x]]\n\n1 notes · 1 errors · 0 warnings"
        vault.validate = lambda: (False, output["text"])
        agents._after_writing()  # errors that came back after a clean vault: a new try
        self.assertEqual(len(self.prompts), 3)


class TestLint(Base):
    def test_the_users_notes_get_warnings_and_bad_names_are_caught(self):
        vault.create(self.today)
        knowledge = config.VAULT / "3. 📚 Knowledge"
        write(knowledge / "Misc/Idea #1.md", "---\ntype: knowledge\n---\nAn idea.")
        write(knowledge / "Misc/Mine #2.md", "---\ntype: knowledge\nwritten_by: owner\n---\nMy note.")
        write(knowledge / "Misc/Plan.md", "---\ntype: knowledge\n---\nA plan.")
        write(knowledge / "Projects/Acme/Plan.md", "---\ntype: project\nstatus: weird\nwritten_by: owner\n---\nMine.")
        errors, warnings, _ = lint.check(config.VAULT)
        self.assertEqual([e.split(":")[0] for e in errors], ["3. 📚 Knowledge/Misc/Idea #1.md"])
        joined = "\n".join(warnings)
        self.assertIn("Mine #2.md: the file name has a character", joined)
        self.assertIn("same name as", joined)
        self.assertIn("status = 'weird' is not in the Vocabulary", joined)

    def test_a_cancelled_task_and_the_new_channels_are_valid(self):
        vault.create(self.today)
        note = task("Call the old bank")
        notes.set_field(note, "status", "cancelled")
        vault.new_engram("![[receipt.jpg]]", "telegram-photo", self.morning,
                         {"asked": "question", "telegram_id": "4242", "fix_prompt": "[[Other note]] what to fix"})
        vault.new_engram("🎤 Voice note, not turned into text\n![[Voice 2026-10-05 1030.ogg]]", "telegram-voice", self.morning)
        write(config.SYSTEM / "Attachments/receipt.jpg", "jpg")
        write(config.SYSTEM / "Attachments/Voice 2026-10-05 1030.ogg", "ogg")
        errors, _, _ = lint.check(config.VAULT)
        self.assertEqual(errors, [])


class TestNewEngram(Base):
    def test_a_taken_name_is_never_written_over(self):
        first = config.ENGRAMS / "2026-10" / "2026-10-05 1030.md"
        write(first, "written by another process")
        path = vault.new_engram("mine", "telegram-text", self.morning)
        self.assertEqual(path.name, "2026-10-05 1030 2.md")
        self.assertEqual(first.read_text(encoding="utf-8"), "written by another process")
        self.assertNotIn(b"\r", path.read_bytes())


class TestNotesReader(AgentBase):
    def commit(self, author: str, path: str, text: str) -> None:
        write(config.VAULT / path, text)
        vault.git("add", "-A")
        vault.git("-c", f"user.name={author}", "-c", "user.email=x@x", "commit", "-q", "-m", "change")

    def setUp(self):
        super().setUp()
        self.quiet = config.NOTES_QUIET_SECONDS
        config.NOTES_QUIET_SECONDS = 0
        self.commit("Seahorse", "_Seahorse.md", "---\ntype: summary\n---\nOverview.")
        self.assertIsNone(agents.user_notes())  # the first run only records where it starts

    def tearDown(self):
        config.NOTES_QUIET_SECONDS = self.quiet
        super().tearDown()

    def test_a_wish_checked_in_the_table_moves_without_a_model(self):
        wish = "1. 🎯 Intention/Wishlist/Movies/Dune.md"
        self.commit("Seahorse", wish, "---\ntype: wish\nwish: movie\ndone: false\n---\n## Why\nA friend said so.")
        agents.user_notes()
        self.commit("Ada", wish, "---\ntype: wish\nwish: movie\ndone: true\n---\n## Why\nA friend said so.")
        self.ai(lambda paths: self.fail("no model for a checkbox"))
        self.assertIsNone(agents.user_notes())
        moved = config.VAULT / "2. 📓 Memory/Experiences/Movies/Dune.md"
        fm, body = notes.read(moved)
        self.assertEqual((fm["type"], fm["done"], body.strip()), ("experience", "true", "## Why\nA friend said so."))
        self.assertFalse((config.VAULT / wish).exists())
        self.assertEqual(agents._read_notes_state()["pending"], {})

    def test_only_the_notes_the_user_made_are_theirs(self):
        self.commit("Seahorse", "2. 📓 Memory/People/Brandão.md", "---\ntype: entity\n---\n## Current\nA friend.")
        self.assertIsNone(agents.user_notes())  # an agent commit: nothing to read
        self.commit("Ada", "3. 📚 Knowledge/Misc/Idea.md", "An offline mode.")
        self.commit("Ada", "2. 📓 Memory/People/Brandão.md", "---\ntype: entity\n---\n## Current\nA friend since school.")
        self.ai(lambda paths: answer("- [[Brandão]] => Since school, in his page\nIdea => Saved under Misc"))
        agents.user_notes()
        prompt = self.prompts[-1][1]
        self.assertIn("### 2. 📓 Memory/People/Brandão.md (Seahorse note, edited by Ada)", prompt)
        self.assertIn("### 3. 📚 Knowledge/Misc/Idea.md (own note of Ada)", prompt)
        self.assertEqual(self.sent[-1], i18n.t("bot.notes_read") + "\n→ Brandão: Since school, in his page\n→ Idea: Saved under Misc")

    def test_a_note_that_fails_three_runs_is_left(self):
        self.commit("Ada", "3. 📚 Knowledge/Misc/Idea.md", "An offline mode.")
        self.ai(lambda paths: answer("timed out after 3600s", error=True))
        for _ in range(3):
            agents.user_notes()
        self.assertEqual(agents._read_notes_state()["pending"], {})
        self.assertEqual(len([s for s in self.sent if i18n.t("agent.notes") in s]), 1)
        self.assertIn(i18n.t("alert.notes_gave_up", notes="Idea", tries=3), self.sent)
        agents.user_notes()
        self.assertEqual(len(self.prompts), 3)


class TestMigration(Base):
    def system(self, name: str, text: str) -> None:
        write(config.SYSTEM / name, vault.fill(text, self.today))

    def read(self, name: str) -> str:
        return (config.SYSTEM / name).read_text(encoding="utf-8")

    def test_old_rules_become_the_new_ones(self):
        self.system("_AGENTS.md", "## Principle 5 — People and subjects\n"
                    "A real person gets a page in `2. 📓 Memory/People/` when they appear in two or more engrams or notes, or when {{user}} asks.\n"
                    "With a single mention, the name stays as plain text. The Reviewer promotes it later.\n"
                    "## Principle 12 — Formatting\nReadable file names with spaces and accents, never kebab-case.\n")
        self.system("_Templates/Person.md", "---\ntype: entity\n---\n## Current\nWho they are to {{user}}.\n## Appearances\n## History\n")
        self.system("_Vocabulary.md", "```vocab\nstate: pending, integrated, unclear, discarded\nchannel: telegram-voice, telegram-text, claude, obsidian, other, fax\n"
                    "status.trait: active, superseded\nimported_from: obsidian, notion, other\n```\n## Types that describe {{user}} directly\n")
        people = config.VAULT / "2. 📓 Memory/People/Ana.md"
        write(people, "---\ntype: entity\n---\n## Current\nWho they are to Ada.\n## Appearances\n")
        migrations._to_1_3_0_agents()
        agents_md = self.read("_AGENTS.md")
        self.assertIn("at the first mention when the person has a relationship to Ada", agents_md)
        self.assertNotIn("With a single mention", agents_md)
        self.assertIn("Write \"Dune Part Two\", not \"Dune: Part Two\".\nA Journal note is named by the day", agents_md)
        self.assertIn("Who they are to Ada.\n## Stories\nShared memories and stories, with their date when known.\n## Appearances",
                      self.read("_Templates/Person.md"))
        vocabulary = self.read("_Vocabulary.md")
        self.assertIn("channel: telegram-voice, telegram-text, claude, obsidian, other, fax, telegram-photo, telegram-file, "
                      "telegram-video, telegram-location, telegram-contact\n", vocabulary)
        self.assertIn("status.trait: active, superseded\nstatus.task: cancelled\n", vocabulary)
        self.assertIn("imported_from: obsidian, notion, other\n```\n## Engram states\n", vocabulary)
        self.assertNotIn("asked:", vocabulary.split("```vocab")[1].split("```")[0])  # any value of a bot field is valid
        self.assertEqual(people.read_text(encoding="utf-8"), "---\ntype: entity\n---\n## Current\nWho they are to Ada.\n## Appearances\n")
        before = {n: self.read(n) for n in ("_AGENTS.md", "_Vocabulary.md", "_Templates/Person.md")}
        migrations._to_1_3_0_agents()
        self.assertEqual({n: self.read(n) for n in before}, before)  # a second run changes nothing

    def test_a_rule_that_changed_since_gets_the_new_rule_at_the_end_of_its_section(self):
        self.system("_AGENTS.md", "## Principle 12 — Formatting\nFile names in Title Case.\nNo blank lines.\n## Principle 13 — Own notes\nText.\n")
        migrations._to_1_3_0_agents()
        text = self.read("_AGENTS.md")
        self.assertIn("No blank lines.\nA file name never has these characters", text)
        self.assertIn("One note per day.\n## Principle 13 — Own notes\nText.\n", text)
        self.assertIn("\nOnly a note that Ada made gets `written_by: owner`.", text.split("## Principle 13")[1])
        self.assertNotIn("\n\n", text)

    def test_a_new_vault_needs_no_migration(self):
        vault.create(self.today)
        names = ("_AGENTS.md", "_ROUTER.md", "_Vocabulary.md", "_Templates/Person.md")
        before = {n: self.read(n) for n in names}
        migrations._to_1_3_0_agents()
        self.assertEqual({n: self.read(n) for n in names}, before)
        self.assertIn(("1.3.0", migrations._to_1_3_0_agents), migrations.MIGRATIONS)


class TestAICommand(unittest.TestCase):
    def fake(self, stdout: str):
        calls = []

        def run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return mock.Mock(stdout=stdout, stderr="", returncode=0)
        return calls, run

    def test_claude_never_reads_secrets_and_gets_no_other_token(self):
        from seahorse.llm import claude
        calls, run = self.fake(json.dumps({"subtype": "success", "result": "ok"}))
        env = {"TELEGRAM_BOT_TOKEN": "123:abc", "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-x"}
        with mock.patch.object(claude.subprocess, "run", run), mock.patch.dict(os.environ, env):
            self.assertFalse(claude.run("hi", "standard", "low", None, True, False, None, 10, 60).error)
            claude.run("hi", "standard", "low", None, False, True, None, 10, 60)
        for cmd, kwargs in calls:
            denied = cmd[cmd.index("--disallowedTools") + 1:]
            for rule in ("Read(~/.seahorse/**)", "Read(~/.ssh/**)", "Read(~/.claude/**)", "Read(~/.codex/**)",
                         "Read(//**/.env)", "Edit(./.obsidian/**)", "Edit(./.git/**)"):
                self.assertIn(rule, denied)
            self.assertNotIn("TELEGRAM_BOT_TOKEN", kwargs["env"])
            self.assertEqual(kwargs["env"]["CLAUDE_CODE_OAUTH_TOKEN"], "sk-ant-oat01-x")
            self.assertEqual(kwargs["env"]["GIT_AUTHOR_NAME"], "Seahorse")

    def test_no_rule_blocks_a_vault_in_a_hidden_folder(self):
        from pathlib import Path
        from seahorse.llm import claude
        self.assertIn("Edit(~/.*)", claude.denied())
        with mock.patch.object(config, "VAULT", Path.home() / ".seahorse" / "vault"):
            rules = claude.denied()
        self.assertNotIn("Read(~/.seahorse/**)", rules)
        self.assertNotIn("Edit(~/.*)", rules)
        self.assertIn("Read(~/.ssh/**)", rules)

    def test_codex_gets_no_other_token_and_commits_as_seahorse(self):
        from seahorse.llm import codex
        calls, run = self.fake("")
        with mock.patch.object(codex.subprocess, "run", run), mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_x"}):
            codex.run("hi", "standard", "low", None, True, False, None, 10, 60)
        cmd, kwargs = calls[0]
        self.assertEqual(cmd[cmd.index("-s") + 1], "workspace-write")
        self.assertNotIn("GROQ_API_KEY", kwargs["env"])
        self.assertEqual(kwargs["env"]["GIT_COMMITTER_NAME"], "Seahorse")


if __name__ == "__main__":
    unittest.main()
