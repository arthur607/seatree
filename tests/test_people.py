from test_seahorse import Base, write, task, message, remove  # sets up the throwaway vault
# Tests of the People feature: the fields of a page, birthdays, briefings before an encounter, friends to call, the
# limits of the Messenger, the buttons and the 1.3.0 migration. They never call an AI or Telegram: both are fakes,
# and an AI call that a test did not expect fails it.
import json
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from test_telegram import FakeMessage, FakeQuery, context

from seahorse import bot, companion, config, i18n, lint, llm, messenger, migrations, notes, people, tasks, telegram_api, vault

TZ = config.TIMEZONE
BRANDAO = ("## Current\nFriend of Ada since school.\n## Stories\n"
           "- As kids, Ada flushed his Torterra card down the toilet. Today they laugh about it.\n"
           "- Gift idea: a new Torterra card.\n## Appearances\n## History\n")
EMPTY_PAGE = ("## Current\nWho they are to Ada.\n## Stories\nShared memories and stories, with their date when known.\n"
              "## Appearances\n- {{YYYY-MM-DD}} · [[note]] · context\n## History\n")
WRITTEN = '{"text": "🎂 Brandão turns 30 on Monday. Remember the Torterra card?\\nPut the gift in the week?", "gift": "a new Torterra card"}'


def person(name: str, body: str = BRANDAO, **fields: str) -> Path:
    head = "".join(f"{key}: {value}\n" for key, value in fields.items())
    path = config.VAULT / people.FOLDER / f"{name}.md"
    write(path, f"---\ntype: entity\nentity: person\naliases: []\n{head}---\n{body}")
    return path


def a_task(name: str, subject: str, **fields: str) -> Path:
    head = "".join(f"{key}: {value}\n" for key, value in fields.items())
    path = config.TASKS / f"{name}.md"
    write(path, f"---\ntype: task\ndone: false\nestimate: 30m\n{head}subject: \"{subject}\"\n---\n")
    return path


def record(when: str, kind: str, source: str = "[[_Seahorse]]", text: str = "x", reaction: str = "pending") -> str:
    return f"- {when} · {kind} · id:{kind[:3]}{when[-5:].replace(':', '')} · tg:1 · {source} · reaction: {reaction} · {text}"


def history(*lines: str) -> None:
    write(config.MESSAGES, "---\ntype: log\n---\n" + "".join(f"{line}\n" for line in lines))


def summary(day: str) -> str:
    return record(f"{day} 08:00", "summary", reaction="—", text="☀️")


class PeopleBase(Base):
    """Telegram, git, the vault lock, the clock, the Companion and the AI are fakes. `self.sent` has the body of each
    Telegram call; `self.calls` has (role, lock held) for each AI run, and `self.prompts` its text."""

    def setUp(self):
        super().setUp()
        self.sent, self.calls, self.prompts, self.answers, self.held = [], [], [], [], False
        self.moment = datetime(2026, 10, 5, 13, 0, tzinfo=TZ)
        self.saved = [(telegram_api, "_call", telegram_api._call), (vault, "lock", vault.lock),
                      (vault, "pull", vault.pull), (vault, "publish", vault.publish), (llm, "run", llm.run),
                      (messenger, "now", messenger.now), (companion, "prepare", companion.prepare)]
        telegram_api._call = self.fake_call
        vault.lock = self.fake_lock
        vault.pull = lambda: None
        vault.publish = lambda text: True
        llm.run = self.fake_ai
        messenger.now = lambda: self.moment
        companion.prepare = lambda moment: (None, None)  # the Companion stays silent here
        bot._pressed.clear()
        history(summary("2026-10-05"))

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
        if not self.answers:
            raise AssertionError("a test called the AI without a fake answer")
        self.calls.append((role, self.held))
        self.prompts.append(text)
        found = self.answers.pop(0)
        return found if isinstance(found, llm.Answer) else llm.Answer(found, None, False)

    def at(self, *when: int) -> datetime:
        self.moment = datetime(*when, tzinfo=TZ)
        return self.moment

    def buttons(self, body: dict | None = None) -> list[tuple[str, str]]:
        body = body or self.sent[-1]
        return [(b["text"], b["callback_data"]) for row in body.get("reply_markup", {}).get("inline_keyboard", []) for b in row]

    def message_id(self) -> str:
        return self.buttons()[0][1].split("|")[1]


class TestFields(unittest.TestCase):
    def test_birthdays_in_the_forms_that_people_and_models_write(self):
        found = {value: people.parse_birthday(value) for value in
                 ("1996-11-13", "--11-13", "13/11", "13/11/1996", "13.11", "13/11/96", "11/30", "29/02", "1996/11/13")}
        self.assertEqual(found["1996-11-13"], people.Birthday(11, 13, 1996))
        self.assertEqual(found["--11-13"], people.Birthday(11, 13))
        self.assertEqual(found["13/11"], people.Birthday(11, 13))  # the day first, as in the week cards
        self.assertEqual(found["13/11/1996"], people.Birthday(11, 13, 1996))
        self.assertEqual(found["13.11"], people.Birthday(11, 13))
        self.assertEqual(found["13/11/96"], people.Birthday(11, 13, 1996))
        self.assertEqual(found["11/30"], people.Birthday(11, 30))  # not a day first: the month first
        self.assertEqual(found["29/02"], people.Birthday(2, 29))
        self.assertEqual(found["1996/11/13"], people.Birthday(11, 13, 1996))
        for wrong in ("", "soon", "31/02", "29/02/2023", "2026-13-01", "13 November"):
            self.assertIsNone(people.parse_birthday(wrong), wrong)

    def test_a_birthday_on_29_february(self):
        leap = people.Birthday(2, 29)
        self.assertEqual(leap.next(date(2027, 1, 10)), date(2027, 2, 28))  # a common year: the day before March
        self.assertEqual(leap.next(date(2027, 3, 1)), date(2028, 2, 29))
        self.assertEqual(leap.next(date(2028, 2, 29)), date(2028, 2, 29))

    def test_how_often_to_keep_in_touch(self):
        found = {value: people.parse_cadence(value) for value in
                 ("weekly", "monthly", "quarterly", "Monthly", "30d", "45", "45 days", "2w", "0d", "often", "")}
        self.assertEqual(found, {"weekly": 7, "monthly": 30, "quarterly": 91, "Monthly": 30, "30d": 30, "45": 45,
                                 "45 days": 45, "2w": 14, "0d": None, "often": None, "": None})

    def test_the_last_contact_is_a_full_date(self):
        self.assertEqual(people.parse_day("2026-09-01"), date(2026, 9, 1))
        self.assertEqual(people.parse_day("01/09/2026"), date(2026, 1, 9))  # the tests run in English: month first
        self.assertEqual(people.parse_day("13/09/2026"), date(2026, 9, 13))
        self.assertIsNone(people.parse_day("--09-01"))
        self.assertIsNone(people.parse_day("yesterday"))

    def test_times_written_in_a_task(self):
        found = {text: people.event_time(text) for text in
                 ("Lunch with [[Ana]] 12h30", "Dinner 20h", "Meeting at 17:00", "Call at 5pm", "Coffee 9:30am",
                  "[[Gym]] (2h)", "Study 2h", "Run 1h30m", "Lunch with Ana")}
        self.assertEqual(found, {"Lunch with [[Ana]] 12h30": 750, "Dinner 20h": 1200, "Meeting at 17:00": 1020,
                                 "Call at 5pm": 1020, "Coffee 9:30am": 570, "[[Gym]] (2h)": None, "Study 2h": None,
                                 "Run 1h30m": None, "Lunch with Ana": None})


class TestBirthdays(PeopleBase):
    def test_a_week_before_comes_with_the_story_and_the_gift_once(self):
        person("Brandão", birthday="1996-10-12", relationship="friend from school")
        self.answers = [WRITTEN]
        self.assertEqual(messenger.run(), "birthday about [[Brandão]]")
        self.assertEqual(self.calls, [("companion", False)])  # the model writes outside the vault lock
        self.assertIn("The kind of message: `birthday`.", self.prompts[0])
        self.assertIn("- Birthday: Monday 2026-10-12, in 7 days. Turns 30.", self.prompts[0])
        self.assertIn("- Relationship to the user: friend from school", self.prompts[0])
        self.assertIn("flushed his Torterra card", self.prompts[0])  # the page goes to the model
        self.assertIn("- A button can put the gift in the week, on Tuesday 2026-10-06.", self.prompts[0])
        self.assertEqual(self.sent[-1]["text"], "🎂 Brandão turns 30 on Monday. Remember the Torterra card?\nPut the gift in the week?")
        message_id = self.message_id()
        self.assertEqual(self.buttons(), [("🎁 Put the gift in the week", f"ppl|{message_id}|gift"),
                                          ("✅ Already sorted", f"ppl|{message_id}|sorted")])
        sent = messenger.messages()[-1]
        self.assertEqual((sent.kind, sent.source, sent.reaction), ("birthday", "[[Brandão]]", "pending"))
        self.assertTrue(sent.text.startswith("week-before · 🎂 Brandão turns 30"), sent.text)
        self.at(2026, 10, 6, 14, 0)
        history(*config.MESSAGES.read_text(encoding="utf-8").splitlines()[3:], summary("2026-10-06"))
        self.assertEqual(messenger.run(), "silence")  # once for this birthday
        self.assertEqual(len(self.calls), 1)

    def test_the_gift_button_plans_a_new_task_once(self):
        vault.create(self.today)
        history(summary("2026-10-05"))
        person("Brandão", birthday="1996-10-12")
        self.answers = [WRITTEN]
        messenger.run()
        message_id = self.message_id()
        self.assertEqual(people.press(message_id, "gift"), "🎁 Buy a gift for Brandão: planned for Tuesday, 10/06.")
        self.assertIsNone(people.press(message_id, "gift"))  # a double tap
        self.assertIsNone(people.press(message_id, "sorted"))
        self.assertEqual(people.apply_now(), ["Buy a gift for Brandão on 2026-10-06"])
        note = config.TASKS / "Buy a gift for Brandão.md"
        fm, body = notes.read(note)
        self.assertEqual((fm["type"], fm["done"], fm["estimate"], fm["planned"], fm["subject"]),
                         ("task", "false", "1h", "2026-10-06", "[[Brandão]]"))
        self.assertEqual(body.strip(), "The birthday of [[Brandão]] is on Monday, 10/12.\nIdea: a new Torterra card")
        week_text = (config.WEEKS / "2026-W41.md").read_text(encoding="utf-8")
        self.assertIn("> [!day] Tue 06/10 · 1h of 4h\n> - [ ] [[Buy a gift for Brandão]] (1h)\n", week_text)
        self.assertEqual(people.apply_now(), [])
        self.assertEqual(people.update(self.today), [])
        self.assertEqual([p.name for p in config.TASKS.glob("*.md")], ["Buy a gift for Brandão.md"])
        self.assertEqual(messenger.messages()[-1].reaction, "acted")
        self.assertEqual(lint.check(config.VAULT)[0], [])

    def test_an_open_gift_task_is_planned_and_the_text_of_code_comes_when_the_model_fails(self):
        person("Brandão", birthday="--10-12")
        gift = a_task("Buy Brandão's present", "[[Brandão]]")
        self.answers = [llm.Answer("the AI is down", None, True)]
        self.assertEqual(messenger.run(), "birthday about [[Brandão]]")
        self.assertIn('- Open gift task: "Buy Brandão\'s present", not in the week yet.', self.prompts[0])
        self.assertEqual(self.sent[-1]["text"], "🎂 The birthday of Brandão is on Monday, 10/12.\nGift: Buy Brandão's present.")
        self.assertEqual(people.press(self.message_id(), "gift"), "🎁 Buy Brandão's present: planned for Tuesday, 10/06.")
        people.apply_now()
        self.assertEqual(notes.read(gift)[0]["planned"], "2026-10-06")
        self.assertEqual([p.name for p in config.TASKS.glob("*.md")], ["Buy Brandão's present.md"])

    def test_a_gift_already_planned_leaves_only_the_sorted_button(self):
        person("Brandão", birthday="--10-12")
        gift = a_task("Comprar presente do Brandão", "[[Hobbies]]", planned="2026-10-08")  # named, not linked
        self.answers = [WRITTEN]
        messenger.run()
        self.assertIn('- Open gift task: "Comprar presente do Brandão", in the week on Thursday 2026-10-08.', self.prompts[0])
        self.assertEqual([label for label, _ in self.buttons()], ["✅ Already sorted"])
        self.assertEqual(people.press(self.message_id(), "sorted"), "✅ OK, it is sorted.")
        self.assertEqual(people.apply_now(), [])
        self.assertEqual(notes.read(gift)[0]["planned"], "2026-10-08")

    def test_on_the_day_a_message_to_send_also_over_the_daily_limit(self):
        page = person("Brandão", birthday="1996-10-12")
        self.at(2026, 10, 12, 10, 0)
        history(summary("2026-10-12"), *(record(f"2026-10-12 09:{m:02d}", "question") for m in range(5)))
        self.assertTrue(messenger.over_limit(self.moment, messenger.messages()))
        self.answers = ['{"text": "🎂 Today is the birthday of Brandão. 30 years, and one card in the toilet.", '
                        '"suggestion": "Parabéns, Brandão! Ainda te devo aquela Torterra 😄"}']
        self.assertEqual(messenger.run(), "birthday about [[Brandão]]")
        self.assertIn("The kind of message: `birthday-today`.", self.prompts[0])
        body = self.sent[-1]
        self.assertEqual(body["parse_mode"], "HTML")
        self.assertEqual(body["text"], "🎂 Today is the birthday of Brandão. 30 years, and one card in the toilet.\n\n"
                                       "✉️ A message for Brandão. Tap it to copy it:\n"
                                       "<code>Parabéns, Brandão! Ainda te devo aquela Torterra 😄</code>")
        self.assertEqual([label for label, _ in self.buttons()], ["✅ Sent"])
        self.assertTrue(messenger.messages()[-1].text.startswith("today · 🎂 Today is"))
        self.assertEqual(people.press(self.message_id(), "sent"), "✅ Sent. I recorded that you talked today.")
        people.apply_now()
        self.assertEqual(notes.read(page)[0]["last_contact"], "2026-10-12")

    def test_each_birthday_message_once_a_year(self):
        person("Brandão", birthday="1996-10-12")
        history(summary("2026-10-12"),
                record("2026-10-05 13:00", "birthday", "[[Brandão]]", "week-before · 🎂 Brandão turns 30"),
                record("2026-10-12 10:00", "birthday", "[[Brandão]]", "today · 🎂 Today is the birthday"))
        self.assertIsNone(people.prepare(self.at(2026, 10, 12, 15, 0)))
        history(summary("2027-10-05"), record("2026-10-05 13:00", "birthday", "[[Brandão]]", "week-before · 🎂"))
        ask = people.prepare(self.at(2027, 10, 5, 13, 0))
        self.assertEqual((ask.kind, ask.stage, ask.person), ("birthday", "week-before", "Brandão"))
        self.assertIn("Turns 31.", ask.request)
        history(summary("2027-10-12"))
        ask = people.prepare(self.at(2027, 10, 12, 10, 0))
        self.assertEqual((ask.stage, ask.urgent), ("today", True))

    def test_a_birthday_seen_late_still_comes_and_29_february(self):
        person("Brandão", birthday="--10-08")
        self.assertEqual(people.prepare(self.moment).stage, "week-before")  # 3 days before
        remove(config.STATE)
        person("Brandão", birthday="--10-06")
        self.assertEqual(people.prepare(self.moment).offer["day"], "2026-10-05")  # tomorrow: the gift is for today
        remove(config.VAULT / people.FOLDER)
        person("Leap", birthday="2000-02-29")
        history(summary("2027-02-28"))
        self.assertEqual(people.prepare(self.at(2027, 2, 28, 10, 0)).stage, "today")
        history(summary("2028-02-28"))
        self.assertEqual(people.prepare(self.at(2028, 2, 28, 10, 0)).stage, "week-before")


class TestBriefings(PeopleBase):
    def test_two_hours_before_the_time_of_the_encounter_once_a_day(self):
        person("Brandão", birthday="1996-12-20")
        self.week_file(Mon="\n> - [ ] Lunch with [[Brandão]] 15h (1h)")
        self.assertIsNone(people.prepare(self.at(2026, 10, 5, 12, 50)))
        self.at(2026, 10, 5, 13, 10)
        self.answers = ['{"text": "Lunch with Brandão at 15h. He wanted to open a card shop: ask him about it."}']
        self.assertEqual(messenger.run(), "briefing about [[Brandão]]")
        self.assertIn("- Today: Lunch with Brandão 15h\n- Time: 15:00", self.prompts[0])
        self.assertIn("- The page has 2 story line(s) under Stories.", self.prompts[0])
        self.assertNotIn("reply_markup", self.sent[-1])  # a briefing has no buttons
        self.assertIsNone(people.prepare(self.at(2026, 10, 5, 14, 0)))
        history(summary("2026-10-05"))
        self.assertIsNone(people.prepare(self.at(2026, 10, 5, 15, 30)))  # the lunch began

    def test_a_task_of_today_with_no_time_waits_for_the_morning_summary(self):
        person("Ana", "## Current\nThe sister of Ada.\n## Stories\n- 2026-09-12 · She started a new job at the museum.\n")
        a_task("Call her about the trip", "[[Ana]]", due="2026-10-05")
        self.assertIsNone(people.prepare(self.at(2026, 10, 5, 8, 10)))
        ask = people.prepare(self.at(2026, 10, 5, 8, 25))
        self.assertEqual((ask.kind, ask.person), ("briefing", "Ana"))
        self.assertIn("- Today: Call her about the trip", ask.request)

    def test_nothing_worth_recalling_is_no_briefing(self):
        person("Bob", EMPTY_PAGE)
        a_task("Meet Bob", "[[Bob]]", planned="2026-10-05")
        self.assertIsNone(people.prepare(self.moment))

    def test_the_model_finds_nothing_and_does_not_run_again(self):
        person("Ana", "## Current\nThe sister of Ada.\n## Stories\n- She started a new job.\n")
        a_task("Call Ana", "[[Ana]]", planned="2026-10-05")
        self.answers = ['{"text": ""}']
        self.assertEqual(messenger.run(), "silence")
        self.assertEqual(self.sent, [])
        self.assertIsNone(people.prepare(self.at(2026, 10, 5, 14, 0)))
        self.assertEqual(len(self.calls), 1)


class TestReconnect(PeopleBase):
    def test_a_call_offer_when_the_last_contact_is_older_than_the_wish(self):
        person("Grandma", EMPTY_PAGE, relationship="grandmother", keep_in_touch="monthly", last_contact="2026-08-28")
        person("Ana", EMPTY_PAGE, keep_in_touch="monthly", last_contact="2026-09-15")  # 20 days: not yet
        self.assertIsNone(people.prepare(self.at(2026, 10, 5, 11, 50)))  # a call offer comes from midday
        self.at(2026, 10, 5, 13, 0)
        self.answers = [llm.Answer("", None, True)]
        self.assertEqual(messenger.run(), "reconnect about [[Grandma]]")
        self.assertIn("- Last contact: 2026-08-28, 38 days ago.", self.prompts[0])
        self.assertEqual(self.sent[-1]["text"], "You last talked to Grandma 38 days ago. Do you want to call on Tuesday, 10/06?")
        self.assertEqual([label for label, _ in self.buttons()], ["📅 Put it in the week", "💤 Not now"])

    def test_with_no_last_contact_code_counts_from_the_day_it_saw_the_wish(self):
        person("Grandma", EMPTY_PAGE, keep_in_touch="30d")
        people.update(self.today)
        self.assertIsNone(people.prepare(self.moment))
        history(summary("2026-11-04"))
        ask = people.prepare(self.at(2026, 11, 4, 13, 0))
        self.assertEqual((ask.kind, ask.person), ("reconnect", "Grandma"))
        self.assertEqual(ask.fallback, "You want to keep in touch with Grandma. Do you want to call on Thursday, 11/05?")

    def test_one_a_day_the_most_overdue_first(self):
        person("Grandma", EMPTY_PAGE, keep_in_touch="monthly", last_contact="2026-07-01")
        person("Uncle", EMPTY_PAGE, keep_in_touch="weekly", last_contact="2026-09-21")
        self.assertEqual(people.prepare(self.moment).person, "Grandma")
        history(summary("2026-10-05"), record("2026-10-05 12:30", "reconnect", "[[Grandma]]"))
        self.assertIsNone(people.prepare(self.at(2026, 10, 5, 15, 0)))
        history(summary("2026-10-06"), record("2026-10-05 12:30", "reconnect", "[[Grandma]]"))
        self.assertEqual(people.prepare(self.at(2026, 10, 6, 13, 0)).person, "Uncle")

    def test_put_it_in_the_week_and_the_call_done_is_the_last_contact(self):
        page = person("Grandma", EMPTY_PAGE, keep_in_touch="monthly", last_contact="2026-08-28")
        self.answers = ['{"text": "38 days since you talked to your grandmother. Call her on Tuesday?"}']
        messenger.run()
        self.assertEqual(people.press(self.message_id(), "week"), "📅 Call Grandma: planned for Tuesday, 10/06.")
        people.apply_now()
        call = config.TASKS / "Call Grandma.md"
        fm = notes.read(call)[0]
        self.assertEqual((fm["planned"], fm["estimate"], fm["subject"]), ("2026-10-06", "30m", "[[Grandma]]"))
        notes.set_field(call, "done", "true")
        self.assertEqual(people.update(date(2026, 10, 6)), ["Grandma: last contact 2026-10-06"])
        self.assertEqual(notes.read(page)[0]["last_contact"], "2026-10-06")
        history(summary("2026-10-20"))
        self.assertIsNone(people.prepare(self.at(2026, 10, 20, 13, 0)))

    def test_not_now_waits_14_days(self):
        person("Grandma", EMPTY_PAGE, keep_in_touch="monthly", last_contact="2026-08-28")
        self.answers = ['{"text": "Call your grandmother on Tuesday?"}']
        messenger.run()
        self.assertEqual(people.press(self.message_id(), "later"), "💤 OK. I'll ask again in 14 days.")
        people.apply_now()
        self.assertEqual(people._load()["reconnect"]["Grandma"]["snooze"], "2026-10-19")
        lines = config.MESSAGES.read_text(encoding="utf-8").splitlines()[3:]
        history(*lines, summary("2026-10-18"))
        self.assertIsNone(people.prepare(self.at(2026, 10, 18, 13, 0)))
        history(*lines, summary("2026-10-19"))
        self.assertEqual(people.prepare(self.at(2026, 10, 19, 13, 0)).person, "Grandma")


class TestLimits(PeopleBase):
    def test_quiet_hours(self):
        person("Brandão", birthday="1996-10-12")
        history(summary("2026-10-11"))
        self.at(2026, 10, 12, 7, 30)
        self.assertEqual(messenger.run(), "silence")
        self.assertEqual((self.sent, self.calls), ([], []))
        ask = people.prepare(self.at(2026, 10, 12, 9, 10))
        self.answers = ['{"text": "🎂 Today is the birthday of Brandão."}']
        self.at(2026, 10, 13, 0, 30)  # the quiet hours began while the model wrote
        self.assertIsNone(people.converse(ask))
        self.assertEqual(self.sent, [])

    def test_the_daily_limit_and_the_gap_between_messages(self):
        person("Brandão", birthday="1996-10-12")
        history(summary("2026-10-05"), *(record(f"2026-10-05 09:{m:02d}", "question") for m in range(5)))
        self.assertIsNone(people.prepare(self.moment))  # a week before: within the daily limit only
        history(summary("2026-10-05"), record("2026-10-05 12:30", "briefing", "[[Ana]]"))
        self.assertIsNone(people.prepare(self.moment))  # an hour between two of these messages
        self.assertIsNotNone(people.prepare(self.at(2026, 10, 5, 13, 31)))

    def test_a_muted_or_snoozed_person_gets_no_message(self):
        person("Brandão", birthday="1996-10-12")
        person("Grandma", EMPTY_PAGE, keep_in_touch="monthly", last_contact="2026-08-28")
        write(config.CALIBRATION, "## Snoozed\n- [[Grandma]] until 2026-11-01\n## Muted topics\n- [[Brandão]]\n")
        self.assertIsNone(people.prepare(self.moment))
        write(config.CALIBRATION, "## Snoozed\nNone.\n## Muted topics\nNone.\n")
        self.assertEqual(people.prepare(self.moment).person, "Brandão")


class TestButtons(PeopleBase, unittest.IsolatedAsyncioTestCase):
    def offer(self) -> None:
        people._save({"offers": {"abc123": {"kind": "reconnect", "person": "Grandma", "task": "Call Grandma",
                                            "day": "2026-10-06", "sent": "2026-10-05"}}})

    async def test_a_tap_plans_the_call_without_a_model_and_a_double_tap_does_it_once(self):
        person("Grandma", EMPTY_PAGE, keep_in_touch="monthly")
        self.offer()
        msg = FakeMessage(130, "38 days since you talked to your grandmother. Call her on Tuesday?")
        first, second = FakeQuery("ppl|abc123|week", msg), FakeQuery("ppl|abc123|week", msg)
        await bot.on_people_button(SimpleNamespace(callback_query=first), context())
        await bot.on_people_button(SimpleNamespace(callback_query=second), context())
        self.assertEqual(first.edits[0][0], "38 days since you talked to your grandmother. Call her on Tuesday?\n\n"
                                            "→ 📅 Call Grandma: planned for Tuesday, 10/06.")
        self.assertEqual((first.answers, second.answers), ([None], [i18n.t("chat.already_done")]))
        self.assertEqual([p.name for p in config.TASKS.glob("*.md")], ["Call Grandma.md"])
        self.assertEqual(people._load()["calls"], {"Call Grandma": "Grandma"})
        self.assertEqual(self.calls, [])

    async def test_an_old_message_only_says_it_is_done(self):
        bot._pressed.clear()
        query = FakeQuery("ppl|zzz999|week", FakeMessage(131, "Old"))
        await bot.on_people_button(SimpleNamespace(callback_query=query), context())
        self.assertEqual((query.answers, query.edits), ([i18n.t("chat.already_done")], []))
        self.assertFalse(config.TASKS.exists())


class TestMigration(Base):
    SYSTEM = config.TEMPLATE / "6. ⚙️ System"
    NAMES = ("_Templates/Person.md", "_Vocabulary.md", "_MESSENGER.md", "_Messenger/Calibration.md",
             "_Messenger/Messages.md")

    def template(self, name: str) -> str:
        return (self.SYSTEM / name).read_text(encoding="utf-8")

    def old(self, name: str) -> str:
        """The template file of 1.2.0 and the other steps of 1.3.0, before the changes for people."""
        text = self.template(name).replace("keep_in_touch:\nlast_contact:\n", "")
        for _, _, _, rows in migrations.PEOPLE_ROWS:
            for row in rows:
                text = text.replace(row + "\n", "")
        text = text.replace(migrations.PEOPLE_RULES + "\n", "").replace(migrations.PEOPLE_MESSAGES[0] + "\n", "")
        return vault.fill(text, self.today)

    def read(self, name: str) -> str:
        return (config.SYSTEM / name).read_text(encoding="utf-8")

    def test_old_vaults_get_the_fields_and_the_rules_and_people_pages_stay(self):
        for name in self.NAMES:
            self.assertNotEqual(self.old(name), vault.fill(self.template(name), self.today), name)
            write(config.SYSTEM / name, self.old(name))
        page = person("Brandão", birthday="13/11")
        before = page.read_bytes()
        migrations._to_1_3_0_people()
        for name in self.NAMES:
            self.assertEqual(self.read(name), vault.fill(self.template(name), self.today), name)
        migrations._to_1_3_0_people()  # a second run changes nothing
        self.assertEqual(self.read("_MESSENGER.md"), vault.fill(self.template("_MESSENGER.md"), self.today))
        self.assertEqual(page.read_bytes(), before)
        self.assertIn(("1.3.0", migrations._to_1_3_0_people), migrations.MIGRATIONS)
        self.assertIn("relationship:\nbirthday:\nkeep_in_touch:\nlast_contact:\n", self.template("_Templates/Person.md"))

    def test_files_that_the_user_changed_get_the_new_parts_where_they_fit(self):
        write(config.SYSTEM / "_Messenger/Calibration.md",
              "## Confidence by type\n| Type | Confidence | Note |\n|---|---|---|\n| delivery | high | Ada reads them all |\n"
              "| reminder | low | ignored |\n## Snoozed\nNone.\n")
        write(config.SYSTEM / "_Vocabulary.md", "## Typed relations\n| Field | Means |\n|---|---|\n| `related` | a link |\n"
                                                "## Common fields\n| Field | Where | Format |\n|---|---|---|\n| `type` | every note | x |\n")
        write(config.SYSTEM / "_MESSENGER.md", "# MESSENGER\n## Message types\n| Type | Who | When | Example |\n|---|---|---|---|\n")
        write(config.SYSTEM / "_Templates/Person.md", "---\ntype: entity\nmine: yes\n---\n## Current\n")
        migrations._to_1_3_0_people()
        self.assertIn("| delivery | high | Ada reads them all |\n| birthday | normal | no data |\n| briefing | normal | no data |\n"
                      "| reconnect | normal | no data |\n| reminder | low | ignored |\n", self.read("_Messenger/Calibration.md"))
        vocabulary = self.read("_Vocabulary.md")
        self.assertIn("| `related` | a link |\n## Common fields", vocabulary)
        self.assertTrue(vocabulary.split("| `type` | every note | x |\n")[1].startswith("| `birthday` |"))
        rules = self.read("_MESSENGER.md")
        self.assertIn("|---|---|---|---|\n| birthday | code, the text by a model |", rules)
        self.assertTrue(rules.rstrip("\n").endswith("A call offer comes from 4 hours after the morning summary."))
        self.assertIn("Code reads the pages in `2. 📓 Memory/People/` and decides when. A model writes the text from "
                      "the page of the person: who the person is to Ada", rules)
        self.assertEqual(self.read("_Templates/Person.md"), "---\ntype: entity\nmine: yes\nkeep_in_touch:\nlast_contact:\n---\n## Current\n")

    def test_a_new_vault_needs_no_migration_and_is_valid(self):
        vault.create(self.today)
        before = {name: self.read(name) for name in self.NAMES}
        migrations._to_1_3_0_people()
        self.assertEqual({name: self.read(name) for name in self.NAMES}, before)
        self.assertEqual(lint.check(config.VAULT)[0], [])


class TestLint(Base):
    def test_the_fields_that_code_reads_on_a_people_page(self):
        vault.create(self.today)
        person("Ana", birthday="13/11", keep_in_touch="Monthly", last_contact="2026-09-01")
        person("Bob", birthday="soon", keep_in_touch="often", last_contact="yesterday")
        person("Carla", birthday="--11-13", keep_in_touch="30d", last_contact="2026-09-01")
        errors, warnings, _ = lint.check(config.VAULT)
        folder = people.FOLDER
        self.assertEqual(sorted(e for e in errors if "People/" in e), [
            f"{folder}/Bob.md: birthday = 'soon' is not readable, write YYYY-MM-DD, or --MM-DD when the year is unknown",
            f"{folder}/Bob.md: keep_in_touch = 'often' is not readable, write weekly, monthly, quarterly, or a number of days such as 30d",
            f"{folder}/Bob.md: last_contact = 'yesterday' is not readable, write YYYY-MM-DD"])
        self.assertIn(f"{folder}/Ana.md: birthday = '13/11', write YYYY-MM-DD, or --MM-DD when the year is unknown", warnings)
        self.assertIn(f"{folder}/Ana.md: keep_in_touch = 'Monthly', write weekly, monthly, quarterly, or a number of days such as 30d", warnings)
        self.assertFalse([w for w in warnings if "Carla.md" in w and "write" in w])


if __name__ == "__main__":
    unittest.main()


class TestOldPages(PeopleBase):
    def test_a_birthday_in_the_text_waits_for_the_reviewer(self):
        folder = config.VAULT / "2. 📓 Memory" / "People"
        write(folder / "Brandão.md", "---\ntype: entity\nentity: person\n---\n## Current\nAmigo.\n"
                                     "## Seahorse\n- Aniversário: 13 de novembro.\n")
        write(folder / "Ana.md", "---\ntype: entity\nentity: person\nbirthday: --03-02\n---\n## Current\nBirthday party in March.\n")
        write(folder / "Rui.md", "---\ntype: entity\nentity: person\n---\n## Current\nA colleague.\n")
        self.assertEqual(people.missing_birthdays(), ["2. 📓 Memory/People/Brandão.md"])


    def test_the_reviewer_gets_the_pages_to_fill(self):
        folder = config.VAULT / "2. 📓 Memory" / "People"
        write(folder / "Yan.md", "---\ntype: entity\nentity: person\n---\n## Current\nAniversário dia 2 de dezembro.\n")
        asked = []
        old_run, old_validate = llm.run, vault.validate
        llm.run = lambda text, *a, **k: asked.append(text) or llm.Answer("ok", None, False)
        vault.validate = lambda: (True, "0 errors")
        self.addCleanup(lambda: (setattr(llm, "run", old_run), setattr(vault, "validate", old_validate)))
        from seahorse import agents
        agents.reviewer()
        self.assertIn("- 2. 📓 Memory/People/Yan.md", asked[0])
