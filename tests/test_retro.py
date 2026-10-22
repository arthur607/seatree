from test_seahorse import Base, write, task, message, remove  # sets up the throwaway vault

# Tests of the retrospective of the week: the numbers from the week file, the engrams and git, the image, the text
# in the Review, the Sunday delivery before the Planner, the quiet hours, /retro and its words. No test calls an AI
# or Telegram: `llm.run` and the Telegram calls are fakes, and an AI call without a fake fails the test.
import asyncio
import io
import os
import subprocess
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from PIL import Image, ImageDraw

from seahorse import agents, bot, config, i18n, llm, messenger, notes, retro, telegram_api, vault, week, week_image

TZ = config.TIMEZONE
MONDAY, SUNDAY = date(2026, 10, 5), date(2026, 10, 11)  # week 41
PEOPLE, KNOWLEDGE, EXPERIENCES = "2. 📓 Memory/People", "3. 📚 Knowledge", "2. 📓 Memory/Experiences"


def at(day: int, hour: int = 10, minute: int = 0, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=TZ)


def answer(text: str, error: bool = False) -> llm.Answer:
    return llm.Answer(text, None, error)


def note(path: str, text: str) -> None:
    write(config.VAULT / path, text)


def commit(when: datetime) -> None:
    """Commits everything at a given moment, as the server does."""
    env = {**os.environ, "GIT_AUTHOR_DATE": when.isoformat(), "GIT_COMMITTER_DATE": when.isoformat()}
    for args in (["add", "-A"], ["-c", "user.name=Seahorse", "-c", "user.email=s@x", "commit", "-q", "--allow-empty",
                                 "-m", f"change {when}"]):
        subprocess.run(["git", "-C", str(config.VAULT), *args], env=env, check=True, capture_output=True)


class RetroBase(Base):
    """A vault, no quiet hours, a fake Telegram, and an AI that fails the test when it is called."""

    def setUp(self):
        super().setUp()
        self.events = []
        saved = (llm.run, telegram_api.send, telegram_api.send_photo, vault.validate, retro.now, config.QUIET_FROM,
                 config.QUIET_UNTIL, config.LANGUAGE)

        def restore():
            (llm.run, telegram_api.send, telegram_api.send_photo, vault.validate, retro.now, config.QUIET_FROM,
             config.QUIET_UNTIL, config.LANGUAGE) = saved
        self.addCleanup(restore)
        llm.run = self.no_ai
        telegram_api.send = lambda text, buttons=None, chat_id=None: self.events.append(("text", text)) or 7
        telegram_api.send_photo = lambda image, caption="": self.events.append(("photo", caption, image)) or 8
        vault.validate = lambda: (True, "")
        config.QUIET_FROM = config.QUIET_UNTIL = 0
        self.clock(at(11, 18))

    def no_ai(self, *args, **kwargs):
        raise AssertionError("a test called the AI without a fake")

    def clock(self, moment: datetime) -> None:
        retro.now = lambda: moment

    def git_vault(self) -> None:
        vault.git("init", "-q", "-b", "main")
        vault.git("config", "core.autocrlf", "false")

    def photos(self) -> list:
        return [e for e in self.events if e[0] == "photo"]

    def plain_week(self) -> None:
        """A week with five task lines, one of them work, and some captures."""
        task("Write the report", tags="[work]")
        notes.set_field(task("Fix the bike"), "postponed", "2")
        self.week_file(Mon="\n> - [x] [[Study]] (2h)\n> - [ ] [[Fix the bike]] (1h)",
                       Tue="\n> - [x] [[Call grandma]] (30m)\n> - [x] [[Write the report]] (3h)",
                       Sun="\n> - [x] [[Gym]] (1h)")
        vault.new_engram("buy bread", "telegram-text", at(6, 9))
        vault.new_engram("a voice note", "telegram-voice", at(7, 21))


class TestCounting(RetroBase):
    def test_tasks_from_the_week_file(self):
        self.plain_week()
        stats = retro.compute(MONDAY, SUNDAY)
        self.assertEqual((stats.done, stats.total), (4, 5))
        self.assertEqual((stats.minutes_done, stats.minutes_planned), (210, 270))  # work is not in the hours
        self.assertEqual(stats.top(), [("Write the report", 180), ("Study", 120), ("Gym", 60)])
        facts = retro.facts(stats)
        self.assertIn("Tasks: 4 of 5 done. Outside work: 3h30 done of 4h30 planned.", facts)
        self.assertIn("- Fix the bike · Monday · 1h · not done · postponed 2 times", facts)
        self.assertIn("- Write the report · Tuesday · 3h · done · work", facts)
        self.assertIn("Captures: 2 (1 text, 1 voice).", facts)
        self.assertIn("evening 1", facts)

    def test_captures_by_kind_only_in_the_week(self):
        for when, channel in ((at(5, 0, 1), "telegram-text"), (at(6, 9), "telegram-text"), (at(6, 9), "telegram-voice"),
                              (at(8, 13), "telegram-photo"), (at(11, 23, 59), "claude")):
            vault.new_engram("x", channel, when)
        vault.new_engram("done", "telegram-text", at(6, 10), {"via_button": "yes"})  # a tap on a button
        vault.new_engram("before", "telegram-text", at(4, 23, 59))
        vault.new_engram("after", "telegram-voice", at(12, 0, 1))
        stats = retro.compute(MONDAY, SUNDAY)
        self.assertEqual(dict(stats.kinds()), {"text": 2, "voice": 1, "photo": 1, "claude": 1})
        self.assertFalse(stats.empty)

    def test_notes_made_and_changed_in_each_area_from_git(self):
        self.git_vault()
        note(f"{PEOPLE}/Ana.md", "---\ntype: entity\n---\n## Appearances\n- 2026-09-01 · [[x]] · met\n")
        note(f"{PEOPLE}/Marta.md", "---\ntype: entity\n---\nSee [[Old]].\n")
        note("1. 🎯 Intention/Wishlist/Movies/Dune.md", "---\ntype: wish\ndone: false\n---\n")
        note(f"{EXPERIENCES}/Books/Old Book.md", "---\ntype: experience\ndone: true\n---\n")
        note(f"{KNOWLEDGE}/Misc/Loose.md", "---\ntype: knowledge\n---\nA loose idea.\n")
        commit(at(4, 12))
        # The week: a wish watched, a new series, a lunch with Ana, a new person, a link fixed, a new subject note,
        # a note moved between subjects, two journal days and a trip.
        (config.VAULT / f"{EXPERIENCES}/Movies").mkdir(parents=True)
        vault.git("mv", "--", "1. 🎯 Intention/Wishlist/Movies/Dune.md", f"{EXPERIENCES}/Movies/Dune.md")
        note(f"{EXPERIENCES}/Series/The Bear.md", "---\ntype: experience\ndone: true\n---\n")
        note(f"{PEOPLE}/Ana.md", "---\ntype: entity\n---\n## Appearances\n- 2026-09-01 · [[x]] · met\n- 2026-10-07 · [[y]] · lunch\n")
        note(f"{PEOPLE}/João.md", "---\ntype: entity\n---\n## Current\nA friend from work.\n## Stories\n- The trip.\n")
        note(f"{PEOPLE}/Marta.md", "---\ntype: entity\n---\nSee [[New]].\n")
        note(f"{PEOPLE}/_People.md", "---\ntype: summary\n---\nAll the people.\n")
        note(f"{KNOWLEDGE}/Rust/Ownership.md", "---\ntype: knowledge\n---\nOne owner.\n")
        (config.VAULT / f"{KNOWLEDGE}/Rust").mkdir(parents=True, exist_ok=True)
        vault.git("mv", "--", f"{KNOWLEDGE}/Misc/Loose.md", f"{KNOWLEDGE}/Rust/Loose.md")
        for day in ("2026-10-06", "2026-10-07"):
            note(f"2. 📓 Memory/Journal/{day}.md", "---\ntype: journal\n---\nA day.\n")
        note("2. 📓 Memory/Trips/Lisbon.md", "---\ntype: trip\n---\nThe plan.\n")
        commit(at(7))
        note(f"{KNOWLEDGE}/Rust/Later.md", "---\ntype: knowledge\n---\nNext week.\n")
        note(f"{PEOPLE}/Late.md", "---\ntype: entity\n---\nNext week.\n")
        note("2. 📓 Memory/Journal/2026-10-12.md", "---\ntype: journal\n---\nNext week.\n")
        commit(at(12, 9))

        stats = retro.compute(MONDAY, date(2026, 10, 13))  # the week is over: only its own commits count
        self.assertEqual(stats.experiences, [("Movies", "Dune"), ("Series", "The Bear")])
        self.assertEqual(stats.people, ["João", "Ana"])  # the most new lines first; a fixed link is no news
        self.assertEqual(stats.learned, ["Ownership"])
        self.assertEqual(stats.journal, [date(2026, 10, 6), date(2026, 10, 7)])
        self.assertEqual(stats.trips, ["Lisbon"])
        while_it_runs = retro.compute(MONDAY, SUNDAY)  # on Sunday, up to now: the later commit counts too
        self.assertIn("Later", while_it_runs.learned)

    def test_the_week_as_it_ended_after_monday_moved_its_open_lines(self):
        self.git_vault()
        self.plain_week()
        commit(at(11, 20))
        path = week.path_for(MONDAY)
        write(path, path.read_text(encoding="utf-8").replace("> - [ ] [[Fix the bike]] (1h)\n", ""))
        commit(at(12, 0, 5))
        self.assertEqual(retro.compute(MONDAY, date(2026, 10, 13)).total, 5)
        self.assertEqual(retro.compute(MONDAY, SUNDAY).total, 4)  # while the week runs: the file as it is

    def test_an_empty_week_and_a_missing_week_file(self):
        stats = retro.compute(MONDAY, SUNDAY)
        self.assertTrue(stats.empty)
        self.assertEqual((stats.done, stats.total, stats.top(), stats.kinds()), (0, 0, [], []))
        self.assertIsInstance(retro.png(stats), bytes)
        self.assertFalse(retro.send_week(SUNDAY))  # no model call, nothing sent
        self.assertIsNone(retro.on_demand(date(2026, 10, 13)))
        self.assertEqual(self.events, [])

    def test_without_git_the_dates_of_the_notes(self):
        note(f"{PEOPLE}/Ana.md", "---\ntype: entity\n---\n## Appearances\n- 2026-10-07 · [[x]] · lunch\n")
        note(f"{PEOPLE}/Bia.md", "---\ntype: entity\n---\n## Appearances\n- 2026-09-01 · [[x]] · met\n")
        note(f"{KNOWLEDGE}/Rust/Ownership.md", "---\ntype: knowledge\ncreated: 2026-10-06\n---\nOne owner.\n")
        note(f"{KNOWLEDGE}/Rust/Old.md", "---\ntype: knowledge\n---\nNo date.\n")
        note(f"{EXPERIENCES}/Movies/Dune.md", "---\ntype: experience\ndone: true\n---\n")
        for path, day in ((f"{KNOWLEDGE}/Rust/Old.md", at(1, month=9)), (f"{EXPERIENCES}/Movies/Dune.md", at(8))):
            os.utime(config.VAULT / path, (day.timestamp(), day.timestamp()))
        stats = retro.compute(MONDAY, date(2026, 10, 13))
        self.assertEqual((stats.people, stats.learned, stats.experiences), (["Ana"], ["Ownership"], [("Movies", "Dune")]))


class TestImage(RetroBase):
    def drawn(self) -> list[str]:
        """Every text that the image draws."""
        texts, original = [], ImageDraw.ImageDraw.text

        def spy(d, xy, text, *args, **kwargs):
            texts.append(text)
            return original(d, xy, text, *args, **kwargs)
        ImageDraw.ImageDraw.text = spy
        self.addCleanup(setattr, ImageDraw.ImageDraw, "text", original)
        return texts

    def full(self) -> retro.Stats:
        lines = [retro.Planned(f"Task {i} 🚗", MONDAY + timedelta(days=i % 7), 30 * (i + 1), i % 3 != 0, i == 13)
                 for i in range(14)]
        return retro.Stats(MONDAY, lines, [(at(6, 9), "text"), (at(6, 10), "voice")],
                           [("Movies", "Dune 🎬"), ("Series", "The Bear")], [MONDAY], ["Lisbon"], [],
                           ["Ana 🎉", "João"], "You finish 7 of 9 tasks before Wednesday.", "Next week I keep Friday light.")

    def test_the_image_has_the_width_of_the_week_image_and_no_emoji(self):
        texts = self.drawn()
        image = Image.open(io.BytesIO(retro.png(self.full())))
        self.assertEqual(image.size[0], week_image.WIDTH)
        self.assertLessEqual(image.size[1], retro.MAX_HEIGHT)
        self.assertTrue(all(week_image._has_glyph(c) for text in texts for c in text), texts)
        for label in ("did", "lived", "people", "captured", "pattern"):
            self.assertIn(i18n.t(f"retro.{label}"), texts)
        self.assertNotIn(i18n.t("retro.learned"), texts)  # an empty section is left out
        self.assertIn("(7h)", texts)  # a work task shows its time in parentheses

    def test_empty_sections_and_no_pattern_make_a_short_image(self):
        texts = self.drawn()
        small = retro.Stats(MONDAY, [], [(at(6, 9), "voice")])
        image = Image.open(io.BytesIO(retro.png(small)))
        self.assertLess(image.size[1], 800)
        self.assertEqual([t for t in texts if t in {i18n.t(f"retro.{k}") for k in ("did", "lived", "learned", "pattern")}], [])
        self.assertIn("1", texts)  # with no task, the number of captures is the big line

    def test_a_huge_week_stays_inside_the_limits(self):
        texts = self.drawn()
        lines = [retro.Planned(f"Task {i} with a long name that needs two lines or more to fit", MONDAY, 30, True, False)
                 for i in range(90)]
        stats = retro.Stats(MONDAY, lines, [(at(6, 9), "text")] * 200,
                            [("Movies", f"Movie {i} with a title that is long enough to wrap twice and more") for i in range(30)],
                            [MONDAY + timedelta(days=i) for i in range(7)], [f"Trip {i}" for i in range(9)],
                            [f"Note {i}" for i in range(40)], [f"Person {i} with a long surname" for i in range(80)],
                            "word " * 300, "other " * 300)
        image = Image.open(io.BytesIO(retro.png(stats)))
        self.assertLessEqual(image.size[1], retro.MAX_HEIGHT)
        self.assertLessEqual(sum(image.size), 10000)  # Telegram's limit for a photo
        self.assertIn(i18n.t("image.more", n=34), texts)  # the lived section: 30 + 9 + journal, 6 shown
        self.assertTrue(any(t.endswith("…") for t in texts))

    def test_texts_in_the_language_of_the_user(self):
        config.LANGUAGE = "pt-BR"
        texts = self.drawn()
        retro.png(self.full())
        self.assertIn("sua semana 41", texts)
        self.assertIn("2026 · 05/10 → 11/10", texts)
        self.assertIn("filme", texts)
        self.assertEqual(retro.caption(self.full()), "🪞 Sua semana 41 · 05/10 → 11/10")


class TestReview(RetroBase):
    def test_the_text_goes_into_the_review_once(self):
        self.plain_week()
        stats = retro.compute(MONDAY, SUNDAY)
        stats.pattern, stats.adjustment = "You did 4 of 5.", "Next week I keep Monday light."
        self.assertTrue(retro.write_review(stats))
        self.assertFalse(retro.write_review(stats))  # the same lines: the file stays as it is
        stats.pattern = "Another line."
        retro.write_review(stats)
        text = week.path_for(MONDAY).read_text(encoding="utf-8")
        self.assertEqual(text.count("> - Retro:"), 1)
        self.assertIn("> [!review]- Review\n> - Planned:\n> - Retro: 4/5 · 3h30/4h30\n"
                      ">   - Did: Write the report (3h), Study (2h), Gym (1h)\n", text)
        self.assertIn(">   - Captured: 2 (1 text, 1 voice)\n>   - Pattern: Another line.\n"
                      ">   - Adjustment: Next week I keep Monday light.\n", text)
        self.assertEqual(week.problems(week.path_for(MONDAY)), [])
        self.assertEqual(len(week.read(MONDAY).days[0].items), 2)

    def test_a_week_with_no_review_gets_one(self):
        path = week.create_if_missing(MONDAY)
        write(path, path.read_text(encoding="utf-8").split("> [!review]")[0].rstrip("\n") + "\n")
        retro.write_review(retro.Stats(MONDAY, captures=[(at(6, 9), "text")]))
        text = path.read_text(encoding="utf-8")
        self.assertIn("\n\n> [!review]- Review\n> - Planned:", text)
        self.assertTrue(text.endswith("> - Retro: 0/0\n>   - Captured: 1 (1 text)\n"))
        self.assertEqual(week.problems(path), [])


class TestModelLine(RetroBase):
    def test_parse_the_answer(self):
        self.assertEqual(retro.parse('Here:\n```json\n{"pattern": " You did\\n 4 of 5. ", "adjustment": "Less on Friday."}\n```'),
                         ("You did 4 of 5.", "Less on Friday."))
        self.assertEqual(retro.parse('{"pattern": "", "adjustment": "Less on Friday."}'), ("", ""))  # no reason
        self.assertEqual(retro.parse("no json at all"), ("", ""))
        self.assertEqual(len(retro.parse('{"pattern": "' + "x" * 500 + '"}')[0]), retro.LINE_LIMIT)

    def test_one_short_read_only_call(self):
        calls = []

        def run(text, role, write=False, session=None, max_turns=80, timeout=3600, web=False):
            calls.append((role, write, web, timeout, text))
            return answer('{"pattern": "You did 4 of 5.", "adjustment": ""}')
        llm.run = run
        self.plain_week()
        self.assertEqual(retro.ask(retro.compute(MONDAY, SUNDAY)), ("You did 4 of 5.", ""))
        role, write_, web, timeout, text = calls[0]
        self.assertEqual((role, write_, web, llm.ROLES["retro"]), ("retro", False, False, ("standard", "low")))
        self.assertLessEqual(timeout, 300)
        self.assertIn("Tasks: 4 of 5 done.", text)
        self.assertIn("in English", text)


class TestDelivery(RetroBase):
    def setUp(self):
        super().setUp()
        self.git_vault()
        self.plain_week()

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return at(11, 18)
        saved = agents.datetime, agents.integrator
        agents.datetime, agents.integrator = Clock, lambda: None
        self.addCleanup(lambda: (setattr(agents, "datetime", saved[0]), setattr(agents, "integrator", saved[1])))

    def ai(self, retro_answer: llm.Answer):
        def run(text, role, *args, **kwargs):
            self.events.append(("ai", role))
            return retro_answer if role == "retro" else answer("🗓️ Week 12/10 to 18/10" if role == "planner" else "ok")
        llm.run = run

    def test_on_sunday_the_week_goes_out_before_the_plan(self):
        self.ai(answer('{"pattern": "You did 4 of 5.", "adjustment": "Next week I keep Monday light."}'))
        agents.weekly()
        kinds = [e[0] if e[0] != "ai" else e[1] for e in self.events]
        self.assertEqual(kinds, ["retro", "photo", "planner", "text", "observer"])
        self.assertEqual(self.events[1][1], "🪞 Your week 41 · 10/05 → 10/11")
        self.assertIn(">   - Pattern: You did 4 of 5.", week.path_for(MONDAY).read_text(encoding="utf-8"))
        sent = [m for m in messenger.messages() if m.kind == "retro"]
        self.assertEqual((len(sent), sent[0].source, sent[0].reaction), (1, "[[2026-W41]]", "—"))
        self.assertFalse(messenger.over_limit(at(11, 18), [message(at(11, 18), "retro", "x")] * 9))
        self.assertEqual(retro.remembered("2026-W41")["pattern"], "You did 4 of 5.")
        self.assertFalse(retro.send_week(SUNDAY))  # once a week
        self.assertEqual(len(self.photos()), 1)

    def test_a_failed_model_still_sends_the_image(self):
        self.ai(answer("timed out after 240s", error=True))
        self.assertTrue(retro.send_week(SUNDAY))
        self.assertEqual(len(self.photos()), 1)
        self.assertEqual(retro.remembered("2026-W41")["pattern"], "")
        self.assertNotIn("Pattern:", week.path_for(MONDAY).read_text(encoding="utf-8"))

    def test_an_error_never_stops_the_plan(self):
        self.ai(answer("{}"))
        retro.compute, old = (lambda *a: 1 / 0), retro.compute
        self.addCleanup(setattr, retro, "compute", old)
        agents.weekly()
        self.assertEqual([e[1] for e in self.events if e[0] == "ai"], ["planner", "observer"])

    def test_in_the_quiet_hours_the_image_waits_for_their_end(self):
        config.QUIET_FROM, config.QUIET_UNTIL = 22 * 60, 8 * 60
        self.assertEqual(retro.after_quiet(), "08:01")
        self.ai(answer('{"pattern": "You did 4 of 5.", "adjustment": ""}'))
        self.clock(at(11, 23))
        self.assertTrue(retro.send_week(SUNDAY))
        self.assertEqual(self.photos(), [])
        self.assertIn("Retro:", week.path_for(MONDAY).read_text(encoding="utf-8"))  # the text is written at once
        self.clock(at(12, 7, 30))
        self.assertIsNone(retro.send_held())
        self.clock(at(12, 8, 1))
        self.assertEqual(retro.send_held(), "retrospective of week 2026-W41")
        self.assertEqual(len(self.photos()), 1)
        self.assertEqual([m.kind for m in messenger.messages()], ["retro"])
        self.assertIsNone(retro.send_held())  # nothing held any more
        self.assertFalse((config.STATE / "retro.png").exists())

    def test_a_held_image_that_waited_too_long_is_dropped(self):
        config.QUIET_FROM, config.QUIET_UNTIL = 22 * 60, 8 * 60
        self.ai(answer("{}"))
        self.clock(at(11, 23))
        retro.send_week(SUNDAY)
        self.clock(at(14, 9))
        self.assertIsNone(retro.send_held())
        self.assertEqual(self.photos(), [])
        self.assertNotIn("held", retro._load())


class TestOnDemand(RetroBase):
    def test_the_last_week_with_its_kept_line_and_no_model_call(self):
        self.plain_week()
        retro._remember("2026-W41", pattern="You did 4 of 5.", adjustment="Next week I keep Monday light.", sent="x")
        seen, old = [], retro.png
        retro.png = lambda stats: seen.append(stats) or b"png"
        self.addCleanup(setattr, retro, "png", old)
        image, text = retro.on_demand(date(2026, 10, 13))
        stats = seen[0]
        self.assertEqual((image, stats.name, stats.pattern, stats.total), (b"png", "2026-W41", "You did 4 of 5.", 5))
        self.assertEqual(text, "🪞 Your week 41 · 10/05 → 10/11")

    def test_which_week(self):
        self.assertEqual(retro.last_week(SUNDAY), date(2026, 9, 28))  # before the Sunday image: the week before
        retro._remember("2026-W41", sent="2026-10-11T18:05")
        self.assertEqual(retro.last_week(SUNDAY), MONDAY)
        self.assertEqual(retro.last_week(date(2026, 10, 14)), MONDAY)

    def test_the_command_and_the_words(self):
        self.assertIn("retro", bot.COMMANDS)
        self.assertTrue(bot.asks_for_retro("Retro") and bot.asks_for_retro(" retrospective! "))
        self.assertFalse(bot.asks_for_retro("retro games are fun") or bot.asks_for_week("retro"))
        config.LANGUAGE = "pt-BR"
        self.assertTrue(bot.asks_for_retro("Retrospectiva da semana"))
        for language in i18n.LANGUAGES:
            self.assertIn("/retro", i18n.t("chat.help", language))
            self.assertNotEqual(i18n.t("chat.menu_retro", language), "chat.menu_retro")

    def test_the_bot_sends_the_image_and_saves_no_capture(self):
        self.plain_week()
        self.clock(at(13, 9))
        replies = []

        class Message:
            text = "retro"

            async def reply_photo(self, photo, caption=None):
                replies.append(("photo", caption))

            async def reply_text(self, text, **_):
                replies.append(("text", text))

        class Chat:
            async def send_action(self, action):
                pass

        update = SimpleNamespace(effective_message=Message(), effective_chat=Chat())
        engrams = len(list(config.ENGRAMS.rglob("*.md")))
        asyncio.run(bot.on_text(update, SimpleNamespace(bot_data={})))
        asyncio.run(bot.on_retro(update, None))
        self.assertEqual(replies, [("photo", "🪞 Your week 41 · 10/05 → 10/11")] * 2)
        self.assertEqual(len(list(config.ENGRAMS.rglob("*.md"))), engrams)
        remove(config.WEEKS)
        remove(config.ENGRAMS)
        asyncio.run(bot.on_retro(update, None))
        self.assertEqual(replies[-1], ("text", i18n.t("retro.nothing")))
