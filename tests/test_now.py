from test_seahorse import Base, write, task, message, remove  # sets up the throwaway vault

# Tests of "Now": the messages about free time, the choice of a task in code, the line that code writes when the
# model fails, the buttons (Start, Another, Not now, Done, 15 more), the question after a restart, the quiet hours,
# the button under the morning summary, and the records that never count toward the daily limit.
# No test calls an AI or Telegram: `llm.run` and the Telegram calls are fakes, and an AI call without a fake fails.
import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from test_agents import AgentBase, answer
from test_telegram import FakeJobs, FakeMessage, FakeQuery, Patch, TelegramBase, context, update

from seahorse import bot, config, i18n, llm, messenger, notes, now, telegram_api, vault

TZ = config.TIMEZONE


def make(name: str, estimate: str = "", due=None, tags: str = "[home]", extra: str = "", body: str = "",
         done: bool = False):
    """A task note. With no estimate, the note has no `estimate` field."""
    fields = f"type: task\ndone: {'true' if done else 'false'}\n"
    fields += f"estimate: {estimate}\n" if estimate else ""
    fields += f"due: {due}\n" if due else ""
    path = config.TASKS / f"{name}.md"
    write(path, f"---\n{fields}{extra}tags: {tags}\n---\n{body}")
    return path


def failed(*_, **__) -> llm.Answer:
    """A fake model that fails, as a slow one does after its timeout."""
    return llm.Answer("timeout", None, True)


def datas(markup) -> list[str]:
    return [b.callback_data for row in (markup.inline_keyboard if markup else []) for b in row]


def labels(markup) -> list[str]:
    return [b.text for row in (markup.inline_keyboard if markup else []) for b in row]


class TestFreeTimeMessages(unittest.TestCase):
    def test_messages_about_free_time_in_three_languages(self):
        cases = [
            ("en", "I have 20 minutes", 20), ("en", "what should I do now?", 0), ("en", "I have half an hour free", 30),
            ("en", "I've got 1h30", 90), ("en", "I'm free now", 0), ("en", "1.5h free", 90),
            ("en", "I have an hour and a half", 90),
            ("pt-BR", "tenho 30 min", 30), ("pt-BR", "tenho meia hora livre", 30), ("pt-BR", "1h livre", 60),
            ("pt-BR", "o que eu faço agora?", 0), ("pt-BR", "O que eu faco agora", 0), ("pt-BR", "tô livre agora", 0),
            ("pt-BR", "tenho uma hora", 60), ("pt-BR", "tenho 1 hora e meia", 90), ("pt-BR", "Tenho uns 40 minutos", 40),
            ("pt-BR", "tenho 30 min, o que eu faço?", 30),
            ("es", "tengo media hora", 30), ("es", "¿qué hago ahora?", 0), ("es", "tengo 20 minutos libres", 20),
            ("es", "tengo una hora y media", 90),
        ]
        for language, text, minutes in cases:
            self.assertEqual(now.trigger(text, language), minutes, (language, text))

    def test_normal_captures_are_not_caught(self):
        cases = [
            ("en", "I have 20 minutes to finish the report"), ("en", "what should I do about Ana?"), ("en", "30 min"),
            ("en", "buy a gift for Ana"), ("en", "I have 1h free tomorrow"), ("en", "what now"),
            ("pt-BR", "tenho 30 min de reunião amanhã"), ("pt-BR", "o que eu faço com o carro?"),
            ("pt-BR", "comprar presente da Ana"), ("pt-BR", "tenho 2 horas de aula"), ("pt-BR", "1h livre no sábado"),
            ("es", "tengo media hora mañana"), ("es", "¿qué hago con el coche?"), ("es", "comprar pan"),
            ("pt-BR", "tenho meia hora livre " + "e quero escrever sobre o livro que li ontem à noite"),
            ("en", "I have 20 minutes\nthen the meeting"),
        ]
        for language, text in cases:
            self.assertIsNone(now.trigger(text, language), (language, text))

    def test_minutes_in_numbers_and_words(self):
        cases = [("30 min", 30), ("1h", 60), ("1h30", 90), ("1 h 30 m", 90), ("2 hours", 120), ("45min", 45),
                 ("1,5h", 90), ("half an hour", 30), ("an hour", 60), ("nothing", 0), ("", 0)]
        for text, minutes in cases:
            self.assertEqual(now.parse_minutes(text, "en"), minutes, text)
        self.assertEqual(now.parse_minutes("meia hora", "pt-BR"), 30)
        self.assertEqual(now.parse_minutes("uma hora", "pt-BR"), 60)
        self.assertEqual(now.parse_minutes("1 hora e 30 min", "pt-BR"), 90)
        self.assertEqual(now.parse_minutes("media hora", "es"), 30)
        self.assertEqual(now.parse_minutes("dos horas", "es"), 120)

    def test_the_time_buttons(self):
        self.assertEqual(now.time_buttons(), [("15 min", "now|min|15"), ("30 min", "now|min|30"), ("1 h", "now|min|60"),
                                              ("2 h", "now|min|120")])
        self.assertEqual([d for _, d in now.time_buttons(above=30)], ["now|min|60", "now|min|120"])


class TestTheChoice(Base):
    def test_today_first_and_a_due_date_wins(self):
        make("Call the bank", "15m", due=self.today)
        make("Read a chapter", "30m")
        make("Wash the car", "30m")
        make("Pay the rent", "20m", due=self.today + timedelta(days=2))  # out of the week, due soon
        make("Renew the passport", "15m", due=self.today + timedelta(days=20))  # out of the week, too far
        make("Cancelled", "15m", extra="status: cancelled\n")
        make("Old task", "15m", done=True)
        self.week_file(Mon="\n> - [ ] [[Call the bank]] (15m)\n> - [ ] [[Read a chapter]] (30m)\n> - [ ] [[Cancelled]] (15m)",
                       Wed="\n> - [ ] [[Wash the car]] (30m)\n> - [x] [[Old task]] (15m)")
        fits, _ = now.ranked(30, self.morning, now.Routine())
        self.assertEqual([c.name for c in fits], ["Call the bank", "Read a chapter", "Pay the rent", "Wash the car"])
        self.assertEqual([c.points for c in fits], [9, 4, 4, 2])
        self.assertIn("due today", fits[0].facts)
        self.assertIn("on today's plan", fits[0].facts)
        self.assertEqual([c.name for c in now.ranked(15, self.morning, now.Routine())[0]], ["Call the bank"])

    def test_the_estimate_must_fit_and_no_estimate_counts_as_30_minutes(self):
        make("Sort the photos")
        make("Paint the door", "2h")
        self.week_file(Tue="\n> - [ ] [[Sort the photos]]\n> - [ ] [[Paint the door]] (2h)")
        fits, shortest = now.ranked(15, self.morning, now.Routine())
        self.assertEqual((fits, shortest.name, shortest.estimate()), ([], "Sort the photos", "~30m"))
        self.assertEqual([c.name for c in now.ranked(30, self.morning, now.Routine())[0]], ["Sort the photos"])
        self.assertEqual([c.name for c in now.ranked(60, self.morning, now.Routine())[0]], ["Sort the photos"])
        self.assertEqual(len(now.ranked(120, self.morning, now.Routine())[0]), 2)

    def test_work_tasks_only_when_nothing_else_fits_or_in_the_work_hours(self):
        make("Write the report", "30m", tags="[work]")
        other = make("Read a chapter", "30m")
        self.week_file(Mon="\n> - [ ] [[Write the report]] (30m)\n> - [ ] [[Read a chapter]] (30m)")
        self.assertEqual([c.name for c in now.ranked(30, self.morning, now.Routine())[0]], ["Read a chapter"])
        at_work = now.Routine(work=(9 * 60, 18 * 60))
        self.assertEqual(len(now.ranked(30, self.morning, at_work)[0]), 2)
        saturday = self.morning + timedelta(days=5)
        self.assertFalse(at_work.at_work(saturday))
        notes.set_field(other, "done", "true")
        self.assertEqual([c.name for c in now.ranked(30, self.morning, now.Routine())[0]], ["Write the report"])

    def test_a_task_that_moved_again_and_again_gets_a_chance(self):
        make("Clean the desk", "30m")
        make("Buy Ana's gift", "30m", extra="moved: 3\n")
        self.week_file(Wed="\n> - [ ] [[Clean the desk]] (30m)\n> - [ ] [[Buy Ana's gift]] (30m)")
        fits, _ = now.ranked(30, self.morning, now.Routine())
        self.assertEqual(fits[0].name, "Buy Ana's gift")
        self.assertIn("moved to another day 3 times: a chance to unstick it", fits[0].facts)
        self.assertEqual(fits[0].why, ("now.why_moved", {"count": 3}))

    def test_the_routine_gives_the_focus_time_and_the_work_hours(self):
        self.assertEqual((now.read_routine().excerpt, now.read_routine().focus, now.read_routine().work), ("none", [], None))
        write(config.VAULT / now.IDENTITY / "Routine.md",
              "---\ntype: profile\n---\nThe routine of Ada, written by Ada. The Planner reads it.\n## Work\n"
              "Monday to Friday, 9am to 6pm, at the office.\n## Personal\nI focus best in the morning, before lunch.\n"
              "## Rules for scheduling\n")
        routine = now.read_routine()
        self.assertEqual((routine.focus, routine.work), ([(5 * 60, 12 * 60)], (9 * 60, 18 * 60)))
        self.assertIn("Monday to Friday, 9am to 6pm", routine.excerpt)
        self.assertNotIn("The routine of", routine.excerpt)
        self.assertTrue(routine.in_focus(self.morning))
        self.assertFalse(routine.in_focus(self.morning.replace(hour=15)))
        self.assertTrue(routine.at_work(self.morning))
        self.assertFalse(routine.at_work(self.morning.replace(hour=19)))
        write(config.VAULT / now.IDENTITY / "Routine.md", "---\ntype: profile\n---\nTrabalho das 9h às 17h.\nEu rendo melhor à noite.\n")
        routine = now.read_routine()
        self.assertEqual((routine.focus, routine.work), ([(18 * 60, 2 * 60)], (9 * 60, 17 * 60)))
        self.assertTrue(routine.in_focus(self.morning.replace(hour=23)))

    def test_heavier_tasks_in_the_focus_time_and_light_ones_at_other_times(self):
        heavy = make("Write the essay", "45m")
        light = make("Water the plants", "15m")
        routine = now.Routine(focus=[(5 * 60, 12 * 60)])
        afternoon = self.morning.replace(hour=15)
        from seahorse import tasks

        def points(path, moment):
            note = tasks.Task(path, notes.read(path)[0])
            minutes = 45 if path == heavy else 15
            return now.score(now.Choice(note, minutes, True, 1), 60, moment, routine).points
        self.assertEqual(points(heavy, self.morning) - points(heavy, afternoon), 1)
        self.assertEqual(points(light, afternoon) - points(light, self.morning), 1)
        self.assertEqual(now.period(self.morning), "morning")
        self.assertEqual(now.period(afternoon), "afternoon")


class TestTheWords(Base):
    def setUp(self):
        super().setUp()
        self.calls = []
        self.patch = Patch(llm, run=failed)
        self.patch.__enter__()

    def tearDown(self):
        self.patch.__exit__()

    def gift(self):
        make("Buy Ana's gift", "30m", due=self.today,
             body="Idea: a Torterra card.\n- [x] ask Bia\n- [ ] check the price of the card\n## History\n- [ ] old\n")
        self.week_file(Mon="\n> - [ ] [[Buy Ana's gift]] (30m)")

    def test_without_the_model_code_writes_the_line(self):
        self.gift()
        found = now.offer(30, self.morning)
        self.assertEqual(found.text, "🎯 Buy Ana's gift (30m)\nIt is due today. It fits in your 30 min.\n"
                                     "First step: check the price of the card")
        self.assertEqual([d for _, d in found.buttons], ["now|start|1", "now|skip|1"])  # only one task: no Another
        self.assertEqual((found.task, found.minutes), ("Buy Ana's gift", 30))

    def test_a_model_that_breaks_changes_nothing(self):
        self.gift()
        def broken(*_, **__):
            raise TimeoutError("slow")
        with Patch(llm, run=broken), self.assertLogs("seahorse.now", "ERROR"):
            found = now.offer(30, self.morning)
        self.assertIn("First step: check the price of the card", found.text)

    def test_the_model_writes_the_line_and_the_step(self):
        make("Call the bank", "15m", due=self.today)
        for name in ("Read a chapter", "Wash the car", "Sort the photos"):
            make(name, "30m")
        self.week_file(Mon="\n> - [ ] [[Call the bank]] (15m)\n> - [ ] [[Read a chapter]] (30m)",
                       Tue="\n> - [ ] [[Wash the car]] (30m)\n> - [ ] [[Sort the photos]] (30m)")

        def model(text, role, write=False, session=None, max_turns=80, timeout=3600, web=False):
            self.calls.append((text, role, max_turns, timeout, write, web))
            return llm.Answer('Sure: {"task": "Read a chapter", "why": "Your mind is fresh now.", '
                              '"step": "Open the book at the bookmark."}', None, False)
        with Patch(llm, run=model):
            found = now.offer(30, self.morning)
        self.assertEqual(found.text, "🎯 Read a chapter (30m)\nYour mind is fresh now.\nFirst step: Open the book at the bookmark.")
        self.assertIn("now|next|1", [d for _, d in found.buttons])
        text, role, max_turns, timeout, write_, web = self.calls[0]
        self.assertEqual((role, max_turns, timeout, write_, web), ("now", 1, now.MODEL_SECONDS, False, False))
        self.assertIn("Ada has 30 minutes free now", text)
        self.assertIn("1. Call the bank · 15m · due today", text)
        self.assertIn("3. ", text)
        self.assertNotIn("4. ", text)  # only the best three
        self.assertIn("in English", text)
        self.assertEqual(llm.ROLES["now"], ("fast", "low"))

    def test_a_model_answer_about_another_task_is_not_used(self):
        self.gift()
        with Patch(llm, run=lambda *a, **k: llm.Answer('{"task": "Fly to Mars", "why": "x", "step": "y"}', None, False)):
            found = now.offer(30, self.morning)
        self.assertIn("It is due today.", found.text)

    def test_nothing_fits_names_the_shortest_task(self):
        make("Paint the door", "2h")
        make("Clean the garage", "1h")
        self.week_file(Sat="\n> - [ ] [[Paint the door]] (2h)\n> - [ ] [[Clean the garage]] (1h)")
        found = now.offer(15, self.morning)
        self.assertEqual(found.text, "Nothing fits in 15 min. The shortest task is “Clean the garage” (1h).")
        self.assertEqual([d for _, d in found.buttons], ["now|min|30", "now|min|60", "now|min|120"])
        self.assertEqual(found.task, "")
        remove(config.TASKS)
        self.assertEqual((now.offer(15, self.morning).text, now.offer(15, self.morning).buttons),
                         (i18n.t("now.nothing_open"), []))


class NowBase(TelegramBase):
    """A vault in git, a fake model that fails (so code writes the line), and a clock fixed on Monday 10:30."""

    def setUp(self):
        super().setUp()
        vault.git("init", "-q", "-b", "main")
        self.clock = self.morning
        self.patches += [Patch(llm, run=failed), Patch(messenger, now=lambda: self.clock)]
        for p in self.patches[-2:]:
            p.__enter__()
        self.ctx = context()

    def plan(self):
        make("Call the bank", "15m", due=self.today)
        make("Read a chapter", "30m")
        make("Wash the car", "30m")
        return self.week_file(Mon="\n> - [ ] [[Call the bank]] (15m)\n> - [ ] [[Read a chapter]] (30m)",
                              Wed="\n> - [ ] [[Wash the car]] (30m)")

    async def press(self, data: str, message_id: int, text: str = "") -> FakeQuery:
        query = FakeQuery(data, FakeMessage(message_id, text))
        await bot.on_button(SimpleNamespace(callback_query=query), self.ctx)
        return query

    async def pick(self, text: str = "I have 30 min") -> FakeMessage:
        msg = FakeMessage(1, text)
        await bot.on_text(update(msg), self.ctx)
        return msg

    async def fire(self) -> None:
        """Runs the last question job, as the job queue does when its time comes."""
        job = self.ctx.job_queue.jobs[-1]
        self.ctx.job = job
        await job.callback(self.ctx)

    def state(self) -> dict:
        return json.loads((config.STATE / "now.json").read_text(encoding="utf-8"))

    def now_lines(self) -> list[messenger.Message]:
        return [m for m in messenger.messages() if m.kind == "now"]


class TestTheFlow(NowBase):
    async def test_a_free_time_message_gets_one_task_and_is_not_a_capture(self):
        self.plan()
        msg = await self.pick()
        text, options = msg.replies[0]
        self.assertEqual(text, "🎯 Call the bank (15m)\nIt is due today. It fits in your 30 min.\n"
                               "First step: open the task and do the first small part")
        self.assertEqual(labels(options["reply_markup"]), ["▶️ Start", "🔄 Another", "❌ Not now"])
        self.assertEqual(datas(options["reply_markup"]), ["now|start|1", "now|next|1", "now|skip|1"])
        self.assertEqual(self.engrams(), [])
        self.assertEqual([(m.source, m.tg, m.reaction) for m in self.now_lines()], [("[[Call the bank]]", "1001", "—")])

    async def test_a_message_in_portuguese(self):
        self.plan()
        with Patch(config, LANGUAGE="pt-BR"):
            msg = await self.pick("tenho meia hora livre")
        self.assertTrue(msg.texts()[0].startswith("🎯 Call the bank (15m)\nO prazo é hoje."))
        self.assertIn("Primeiro passo:", msg.texts()[0])

    async def test_a_forwarded_message_about_free_time_stays_a_capture(self):
        msg = FakeMessage(1, "I have 30 min", forward_origin=SimpleNamespace(sender_user=SimpleNamespace(full_name="Bia")))
        await bot.on_text(update(msg), self.ctx)
        self.assertEqual(len(self.engrams()), 1)

    async def test_now_without_a_time_asks_with_buttons(self):
        self.plan()
        msg = FakeMessage(2, "/now")
        await bot.on_now(update(msg), SimpleNamespace(args=[]))
        text, options = msg.replies[0]
        self.assertEqual(text, "How much time do you have?")
        self.assertEqual(datas(options["reply_markup"]), ["now|min|15", "now|min|30", "now|min|60", "now|min|120"])
        first = await self.press("now|min|30", 1001, text)
        second = await self.press("now|min|30", 1001, text)
        self.assertTrue(first.edits[0][0].startswith("🎯 Call the bank (15m)"))
        self.assertEqual(datas(first.edits[0][1]), ["now|start|1", "now|next|1", "now|skip|1"])
        self.assertEqual((second.edits, second.answers), ([], [i18n.t("chat.already_done")]))
        self.assertEqual(len(self.now_lines()), 1)
        args = FakeMessage(3, "/now 1h")
        await bot.on_now(update(args), SimpleNamespace(args=["1h"]))
        self.assertTrue(args.texts()[0].startswith("🎯 Call the bank"))
        self.assertIn("now", bot.COMMANDS)
        for language in i18n.LANGUAGES:
            self.assertIn("/now", i18n.t("chat.help", language))

    async def test_start_then_done_marks_the_task_in_code(self):
        week_path = self.plan()
        msg = await self.pick()
        first = await self.press("now|start|1", 1001, msg.texts()[0])
        second = await self.press("now|start|1", 1001, msg.texts()[0])
        self.assertTrue(first.edits[0][0].endswith("\n\n▶️ Go! I'll check back in 15 min."))
        self.assertIsNone(first.edits[0][1])
        self.assertEqual(second.answers, [i18n.t("chat.already_done")])
        jobs = self.ctx.job_queue.jobs
        self.assertEqual([(j.when, j.name.startswith("now ")) for j in jobs], [(15 * 60, True)])
        self.assertEqual([m.reaction for m in self.now_lines()], ["acted"])

        self.clock += timedelta(minutes=15)
        await self.fire()
        question, buttons = self.sent[-1]
        self.assertEqual(question, "⏰ Did you finish “Call the bank”?")
        fid = jobs[0].data
        self.assertEqual([d for _, d in buttons], [f"now|done|{fid}", f"now|more|{fid}", f"now|stop|{fid}"])
        self.assertEqual([l for l, _ in buttons], ["✅ Done", "⏳ 15 more min", "❌ Not now"])
        await self.fire()  # the same job again: the question goes out once
        self.assertEqual(len(self.sent), 1)

        done = await self.press(f"now|done|{fid}", 77, question)
        again = await self.press(f"now|done|{fid}", 77, question)
        self.assertEqual(done.edits[0][0], f"{question}\n\n✅ Good work. 1 of 2 tasks of today's plan are done.")
        self.assertEqual(again.answers, [i18n.t("chat.already_done")])
        self.assertEqual(notes.read(config.TASKS / "Call the bank.md")[0]["done"], "true")
        self.assertIn("> - [x] [[Call the bank]] (15m)", week_path.read_text(encoding="utf-8"))
        self.assertIn("now: Call the bank done", vault.git("log", "--format=%s").stdout)
        self.assertEqual([(m.text.split(" · ")[0], m.reaction) for m in self.now_lines()], [("pick", "acted"), ("check", "acted")])

    async def test_done_on_a_task_that_repeats_opens_it_again(self):
        make("Water the plants", "15m", due=self.today, extra="repeat: weekly\n")
        week_path = self.week_file(Mon="\n> - [ ] [[Water the plants]] (15m)")
        msg = await self.pick("I have 15 min")
        await self.press("now|start|1", 1001, msg.texts()[0])
        await self.fire()
        fid = self.ctx.job_queue.jobs[0].data
        done = await self.press(f"now|done|{fid}", 77, self.sent[-1][0])
        fm = notes.read(config.TASKS / "Water the plants.md")[0]
        self.assertEqual((fm["done"], fm["due"], fm["last_done"]), ("false", "2026-10-12", "2026-10-05"))
        self.assertIn("> - [x] [[Water the plants]] (15m)", week_path.read_text(encoding="utf-8"))
        self.assertTrue(done.edits[0][0].endswith("✅ Good work. All of today's plan is done 🌿"))

    async def test_fifteen_more_then_not_now_changes_nothing(self):
        self.plan()
        note = config.TASKS / "Call the bank.md"
        notes.set_field(note, "moved", "2")
        msg = await self.pick()
        await self.press("now|start|1", 1001, msg.texts()[0])
        self.clock += timedelta(minutes=15)
        await self.fire()
        fid = self.ctx.job_queue.jobs[0].data
        more = await self.press(f"now|more|{fid}", 77, self.sent[-1][0])
        self.assertTrue(more.edits[0][0].endswith("⏳ OK. I'll check back in 15 min."))
        self.assertEqual([j.when for j in self.ctx.job_queue.jobs], [15 * 60, 15 * 60])
        self.assertEqual((await self.press(f"now|more|{fid}", 77)).answers, [i18n.t("chat.already_done")])
        self.clock += timedelta(minutes=15)
        await self.fire()
        self.assertEqual(len(self.sent), 2)  # asked again
        stop = await self.press(f"now|stop|{fid}", 78, self.sent[-1][0])
        self.assertTrue(stop.edits[0][0].endswith("No problem. “Call the bank” stays open for another time."))
        fm = notes.read(note)[0]
        self.assertEqual((fm["done"], fm["moved"], fm.get("postponed")), ("false", "2", None))

    async def test_another_never_shows_a_task_twice(self):
        self.plan()
        msg = await self.pick()
        shown = [msg.texts()[0].split("\n")[0]]
        first = await self.press("now|next|1", 1001, msg.texts()[0])
        double = await self.press("now|next|1", 1001, msg.texts()[0])
        self.assertEqual(double.answers, [i18n.t("chat.already_done")])
        shown.append(first.edits[0][0].split("\n")[0])
        second = await self.press("now|next|2", 1001, first.edits[0][0])
        shown.append(second.edits[0][0].split("\n")[0])
        self.assertEqual(shown, ["🎯 Call the bank (15m)", "🎯 Read a chapter (30m)", "🎯 Wash the car (30m)"])
        self.assertEqual(datas(second.edits[0][1]), ["now|start|3", "now|skip|3"])  # the last one: no Another
        last = await self.press("now|next|3", 1001)
        self.assertEqual((last.edits, last.answers), ([], [i18n.t("now.no_other", minutes=30)]))
        self.assertEqual(self.state()["rounds"]["1001"]["shown"], ["Call the bank", "Read a chapter", "Wash the car"])
        start = await self.press("now|start|3", 1001, second.edits[0][0])  # the round still works
        self.assertTrue(start.edits[0][0].startswith("🎯 Wash the car (30m)"))
        skip = await self.press("now|skip|3", 1001)
        self.assertEqual(skip.answers, [i18n.t("chat.already_done")])

    async def test_not_now_closes_the_round(self):
        self.plan()
        msg = await self.pick()
        skip = await self.press("now|skip|1", 1001, msg.texts()[0])
        self.assertTrue(skip.edits[0][0].endswith(i18n.t("now.closed")))
        self.assertEqual((await self.press("now|start|1", 1001)).answers, [i18n.t("chat.already_done")])
        self.assertEqual(self.ctx.job_queue.jobs, [])

    async def test_nothing_fits_offers_more_time(self):
        make("Paint the door", "1h")
        self.week_file(Sat="\n> - [ ] [[Paint the door]] (1h)")
        msg = await self.pick("I have 15 min")
        self.assertEqual(msg.texts()[0], "Nothing fits in 15 min. The shortest task is “Paint the door” (1h).")
        query = await self.press("now|min|60", 1001, msg.texts()[0])
        self.assertTrue(query.edits[0][0].startswith("🎯 Paint the door (1h)"))


class TestRestartAndQuietHours(NowBase):
    async def test_the_question_survives_a_restart(self):
        self.plan()
        msg = await self.pick()
        await self.press("now|start|1", 1001, msg.texts()[0])
        fid = self.ctx.job_queue.jobs[0].data
        self.clock += timedelta(minutes=5)
        jobs = FakeJobs()
        await bot._now_restore(jobs)  # a new bot process, with the state file of the old one
        self.assertEqual([(j.data, j.when) for j in jobs.jobs], [(fid, 10 * 60)])
        self.clock += timedelta(minutes=20)  # the bot was down when the time came: the question goes out at once
        jobs = FakeJobs()
        await bot._now_restore(jobs)
        self.assertEqual([j.when for j in jobs.jobs], [5])
        self.ctx.job = jobs.jobs[0]
        await jobs.jobs[0].callback(self.ctx)
        self.assertEqual(self.sent[-1][0], "⏰ Did you finish “Call the bank”?")

    async def test_a_question_long_past_is_dropped(self):
        self.plan()
        msg = await self.pick()
        await self.press("now|start|1", 1001, msg.texts()[0])
        fid = self.ctx.job_queue.jobs[0].data
        self.clock += timedelta(hours=3)
        jobs = FakeJobs()
        await bot._now_restore(jobs)
        self.assertEqual(jobs.jobs, [])
        self.assertEqual(self.state()["follow_ups"][fid]["status"], "expired")

    async def test_no_question_in_the_quiet_hours(self):
        self.plan()
        self.clock = self.morning.replace(hour=23, minute=50)  # 15 min later is 00:05, in the quiet hours
        msg = await self.pick()
        start = await self.press("now|start|1", 1001, msg.texts()[0])
        self.assertTrue(start.edits[0][0].endswith("▶️ Go! Good luck."))
        self.assertEqual(self.ctx.job_queue.jobs, [])

    async def test_a_question_that_comes_in_the_quiet_hours_does_not_go_out(self):
        self.plan()
        msg = await self.pick()
        await self.press("now|start|1", 1001, msg.texts()[0])
        self.clock += timedelta(minutes=15)
        with Patch(config, QUIET_FROM=10 * 60 + 40, QUIET_UNTIL=11 * 60):
            await self.fire()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.state()["follow_ups"][self.ctx.job_queue.jobs[0].data]["status"], "skipped")

    async def test_a_task_done_meanwhile_gets_no_question(self):
        self.plan()
        msg = await self.pick()
        await self.press("now|start|1", 1001, msg.texts()[0])
        notes.set_field(config.TASKS / "Call the bank.md", "done", "true")  # checked on the phone
        await self.fire()
        self.assertEqual(self.sent, [])


class TestMorningAndRecords(NowBase):
    async def test_the_morning_button_asks_the_time_and_works_again_later(self):
        self.plan()
        summary = "☀️ Monday, 05/10 · 0h of 4h"
        first = await self.press("now|ask|", 900, summary)
        second = await self.press("now|ask|", 900, summary)
        for query in (first, second):
            text, options = query.message.replies[0]
            self.assertEqual(text, "How much time do you have?")
            self.assertEqual(datas(options["reply_markup"]), ["now|min|15", "now|min|30", "now|min|60", "now|min|120"])
            self.assertEqual(query.edits, [])  # the summary stays as it is
        pick = await self.press("now|min|15", 1001, "How much time do you have?")
        self.assertTrue(pick.edits[0][0].startswith("🎯 Call the bank (15m)"))

    async def test_now_messages_never_count_toward_the_daily_limit(self):
        self.plan()
        for i in range(messenger.daily_limit() + 1):
            await self.pick()
        self.assertEqual(len(self.now_lines()), messenger.daily_limit() + 1)
        self.assertFalse(messenger.over_limit(self.clock, messenger.messages()))
        self.assertNotIn("now", messenger.PROACTIVE | messenger.SPONTANEOUS | messenger.CONVERSATION)
        for i in range(messenger.daily_limit()):
            messenger.record("question", "[[X]]", "a question", 5, f"q{i}")
        self.assertTrue(messenger.over_limit(self.clock, messenger.messages()))

    async def test_the_companion_sees_the_now_lines(self):
        from seahorse import companion
        self.plan()
        await self.pick()
        history = companion.history_text(config.MESSAGES.read_text(encoding="utf-8").splitlines())
        self.assertIn("· now · id:", history)
        self.assertIn("[[Call the bank]]", history)

    async def test_old_rounds_leave_the_state_file(self):
        self.plan()
        await self.pick()
        self.clock += timedelta(days=now.KEEP_DAYS + 1)
        now.open_round(5000)
        self.assertEqual(list(self.state()["rounds"]), ["5000"])


class TestMorningSummary(AgentBase):
    def test_the_morning_summary_has_the_now_button(self):
        calls = []
        telegram_api.send = lambda text, buttons=None, chat_id=None: calls.append((text, buttons)) or 5
        self.ai(lambda paths: answer("☀️ Monday, 05/10 · 0h of 4h\n📌 TODAY\n• Call the bank"))
        from seahorse import agents
        with Patch(telegram_api, send_photo=lambda image, caption="": 6):
            agents.morning()
        self.assertEqual(calls, [("☀️ Monday, 05/10 · 0h of 4h\n📌 TODAY\n• Call the bank",
                                  [("🎲 What do I do now?", "now|ask|")])])
        self.assertEqual([m.kind for m in messenger.messages()], ["summary"])


if __name__ == "__main__":
    unittest.main()
