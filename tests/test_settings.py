from test_seahorse import Base, write  # sets up the throwaway vault

# Tests of the settings that change in the chat: the values that are checked, the lines that say what changes,
# config.toml written only after the button, the timetable moved without a restart, and /settings. Also the next
# message without Reply that answers the question of a confirmation, and the 1.4.0 rules of the vault.
import asyncio
import copy
from datetime import datetime, timedelta
from types import SimpleNamespace

from apscheduler.triggers.cron import CronTrigger

from seahorse import agents, bot, config, i18n, intro, messenger, migrations, settings, vault

TZ = config.TIMEZONE


class SettingsBase(Base):
    def setUp(self):
        super().setUp()
        saved = (copy.deepcopy(config.SETTINGS), config.QUIET_FROM, config.QUIET_UNTIL, config.DAILY_CAPACITY,
                 config.DAILY_MESSAGES, config.LANGUAGE, config.TIMEZONE)
        self.addCleanup(self.restore, saved)
        config.HOME.mkdir(parents=True, exist_ok=True)
        config.CONFIG_FILE.write_text(config.dumps({"user": {"name": "Ada", "language": "en", "timezone": "UTC"},
                                                    "ai": {"provider": "claude"}, "telegram": {"user_id": 7}}),
                                      encoding="utf-8")

    def restore(self, saved):
        values, *rest = saved
        for table, fields in values.items():
            if isinstance(fields, dict):
                config.SETTINGS[table].clear()
                config.SETTINGS[table].update(fields)
        (config.QUIET_FROM, config.QUIET_UNTIL, config.DAILY_CAPACITY, config.DAILY_MESSAGES, config.LANGUAGE,
         config.TIMEZONE) = rest


class TestValues(SettingsBase):
    def test_each_value_is_checked_and_kept_in_one_form(self):
        cases = [("quiet_from", "9h", "09:00"), ("quiet_from", "21:30", "21:30"), ("morning", "25:00", None),
                 ("weekly_day", "Saturday", "sat"), ("weekly_day", "someday", None), ("daily_messages", "3", "3"),
                 ("daily_messages", "500", None), ("timezone", "Europe/Lisbon", "Europe/Lisbon"),
                 ("timezone", "Mars/Base", None), ("timezone", "America", None), ("language", "pt-BR", None),
                 ("daily_capacity", "3h", None)]  # the language and the capacity change elsewhere
        for key, value, expected in cases:
            self.assertEqual(settings.clean(key, value), expected, (key, value))

    def test_a_part_gives_only_known_keys_with_new_values(self):
        part = "→ ⚙️ quiet_from=22:00; quiet_until=08:00, vault=/tmp; morning=99:00; weekly_day=sat"
        self.assertTrue(settings.is_change(part))
        self.assertEqual(settings.parse(part), {"quiet_from": "22:00", "weekly_day": "sat"})

    def test_the_lines_say_what_changes(self):
        lines = settings.describe({"quiet_from": "22:00", "weekly_day": "sat", "weekly_time": "10:00", "daily_messages": "2"})
        self.assertEqual(lines, ["Quiet hours: 00:00–08:00 → 22:00–08:00", "Weekly plan: sun 18:00 → sat 10:00",
                                 f"{i18n.t('settings.daily_messages')}: 5 → 2"])
        self.assertEqual(settings.describe({"quiet_from": "08:00"}), ["Quiet hours: 00:00–08:00 → none"])

    def test_settings_shows_each_value_and_how_to_change_it(self):
        text = settings.show()
        self.assertTrue(text.startswith(i18n.t("settings.title")))
        self.assertIn("• Quiet hours: 00:00–08:00", text)
        self.assertTrue(text.endswith(i18n.t("settings.how")))


class TestApply(SettingsBase):
    def test_config_changes_only_after_the_button_and_the_other_settings_stay(self):
        timezone = config.TIMEZONE
        settings.propose("2026-10-05 1030", {"quiet_from": "22:00", "daily_messages": "3"})
        self.assertNotIn("quiet_from", config.CONFIG_FILE.read_text(encoding="utf-8"))  # nothing written yet
        lines, error = settings.apply("2026-10-05 1030")
        self.assertEqual(error, "")
        self.assertEqual(len(lines), 2)
        data = config.read_toml(config.CONFIG_FILE)[0]
        self.assertEqual(data["schedule"]["quiet_from"], "22:00")
        self.assertEqual(data["messenger"]["daily_messages"], 3)
        self.assertEqual((data["user"]["name"], data["telegram"]["user_id"], data["ai"]["provider"]), ("Ada", 7, "claude"))
        self.assertEqual((config.QUIET_FROM, config.DAILY_MESSAGES), (22 * 60, 3))
        self.assertEqual(config.TIMEZONE, timezone)  # only what changed: the time zone of the process stays
        self.assertIsNone(settings.pending("2026-10-05 1030"))
        self.assertEqual(settings.apply("2026-10-05 1030"), ([], i18n.t("settings.gone")))  # a second tap does nothing

    def test_a_broken_config_is_not_touched(self):
        config.CONFIG_FILE.write_text("[user\nname = ", encoding="utf-8")
        settings.propose("x", {"morning": "07:00"})
        lines, error = settings.apply("x")
        self.assertEqual(lines, [])
        self.assertIn("config.toml", error)
        self.assertEqual(config.CONFIG_FILE.read_text(encoding="utf-8"), "[user\nname = ")
        self.assertEqual(config.SCHEDULE["morning"], "08:00")


class FakeJob:
    """A job of the job queue, with the cron trigger that run_daily makes."""

    def __init__(self, jobs, name, at, days, now):
        week = ",".join(("sun", "mon", "tue", "wed", "thu", "fri", "sat")[d] for d in days) if days else "*"
        trigger = CronTrigger(hour=at.hour, minute=at.minute, day_of_week=week, timezone=at.tzinfo)
        self.name, self.jobs = name, jobs
        self.next_t = trigger.get_next_fire_time(None, now)
        self.job = SimpleNamespace(trigger=trigger, modify=lambda next_run_time: setattr(self, "next_t", next_run_time))

    def schedule_removal(self):
        self.jobs.removed.append(self.name)
        self.jobs.jobs[self.name].remove(self)


class FakeJobs:
    def __init__(self, now=None):
        self.daily, self.removed, self.jobs = [], [], {}
        self.now = now or datetime.now(TZ)

    def get_jobs_by_name(self, name):
        return list(self.jobs.get(name, []))

    def run_daily(self, callback, time, days=None, name=None):
        self.daily.append((name, f"{time:%H:%M}"))
        self.jobs.setdefault(name, []).append(FakeJob(self, name, time, days, self.now))


class FakeQuery:
    def __init__(self, text):
        self.message = SimpleNamespace(text=text)
        self.edits = []

    async def edit_message_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


class TestButtons(SettingsBase):
    def tap(self, change: bool) -> tuple[FakeQuery, FakeJobs]:
        query = FakeQuery("✓ Saved\n→ ⚙ Morning summary: 08:00 → 07:00\n\n" + i18n.t("settings.confirm"))
        jobs = FakeJobs()
        bot._schedule_daily(jobs)  # the timetable as the bot started it
        jobs.daily.clear()
        context = SimpleNamespace(application=SimpleNamespace(job_queue=jobs), bot=None)
        asyncio.run(bot._setting_button(query, context, "2026-10-05 1030", change))
        return query, jobs

    def test_change_it_writes_and_moves_the_timetable_without_a_restart(self):
        settings.propose("2026-10-05 1030", {"morning": "07:00"})
        query, jobs = self.tap(True)
        self.assertEqual(query.edits[-1][0], "✓ Saved\n→ ⚙ Morning summary: 08:00 → 07:00\n\n" + i18n.t("settings.changed"))
        self.assertEqual(sorted(set(jobs.removed)), sorted(bot.DAILY_JOBS))
        self.assertIn(("morning", "07:00"), jobs.daily)
        self.assertEqual(config.read_toml(config.CONFIG_FILE)[0]["schedule"]["morning"], "07:00")

    def test_keep_it_changes_nothing(self):
        settings.propose("2026-10-05 1030", {"morning": "07:00"})
        query, jobs = self.tap(False)
        self.assertEqual(query.edits[-1][0], "✓ Saved\n→ ⚙ Morning summary: 08:00 → 07:00\n\n" + i18n.t("settings.kept"))
        self.assertEqual(jobs.daily, [])
        self.assertIsNone(settings.pending("2026-10-05 1030"))
        self.assertNotIn("morning", config.CONFIG_FILE.read_text(encoding="utf-8"))

    def test_a_job_that_ran_today_does_not_run_again_today(self):
        now = datetime(2026, 10, 5, 8, 20, tzinfo=TZ)  # a Monday: the summary went out at 8
        jobs = FakeJobs(now)
        bot._schedule_daily(jobs)
        original = bot.datetime
        bot.datetime = SimpleNamespace(now=lambda tz=None: now)
        self.addCleanup(setattr, bot, "datetime", original)
        config.SCHEDULE.update(morning="09:00", weekly_day="mon", weekly_time="20:00")
        bot.reschedule(SimpleNamespace(job_queue=jobs))
        morning, = jobs.get_jobs_by_name("morning")
        weekly, = jobs.get_jobs_by_name("weekly")
        self.assertEqual(morning.next_t, datetime(2026, 10, 6, 9, 0, tzinfo=TZ))  # tomorrow, not twice today
        self.assertEqual(weekly.next_t, datetime(2026, 10, 5, 20, 0, tzinfo=TZ))  # it was for Sunday: tonight it runs

    def test_the_command(self):
        self.assertIn("settings", bot.COMMANDS)
        replies = []

        async def reply_text(text, **_):
            replies.append(text)
        update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=reply_text))
        asyncio.run(bot.on_settings(update, None))
        self.assertEqual(replies, [settings.show()])


class TestNextMessage(Base):
    def setUp(self):
        super().setUp()
        saved = (intro.current, messenger.messages)
        self.addCleanup(lambda: (setattr(intro, "current", saved[0]), setattr(messenger, "messages", saved[1])))
        intro.current = lambda: None
        messenger.messages = lambda: []

    def test_it_answers_the_question_of_the_last_confirmation_once(self):
        agents._record_question("2026-10-05 1030")
        context = SimpleNamespace(bot_data={})
        link = asyncio.run(bot._soft_link(context, take=False))
        self.assertEqual(link.fields, {"answers_engram": "[[2026-10-05 1030]]"})
        asyncio.run(bot._take_question(link))
        self.assertEqual(asyncio.run(bot._soft_link(context)).fields, {})  # the next one is a new subject

    def test_a_newer_message_of_seahorse_wins(self):
        agents._record_question("2026-10-05 1030")
        later = datetime.now(TZ) + timedelta(minutes=1)
        messenger.messages = lambda: [messenger.Message(later, "question", "abc", "55", "[[x]]", "pending", "Which day?")]
        context = SimpleNamespace(bot_data={})
        original = bot.telegram_api.sent_text
        bot.telegram_api.sent_text = lambda message_id: "Which day?"
        self.addCleanup(setattr, bot.telegram_api, "sent_text", original)
        link = asyncio.run(bot._soft_link(context, take=False))
        self.assertEqual(link.fields.get("message_id"), "abc")


class TestMigration(Base):
    """1.4.0: the rules say that the Integrator asks in the moment, and that settings change in the chat."""
    NAMES = ("_AGENTS.md", "_ROUTER.md")

    def read(self, name: str) -> str:
        return (config.SYSTEM / name).read_text(encoding="utf-8")

    def test_an_old_vault_gets_the_rules_once(self):
        vault.create(self.today)
        fresh = {name: self.read(name) for name in self.NAMES}
        fill = lambda s: vault.fill(s, self.today)  # noqa: E731
        old = (fresh["_AGENTS.md"].replace(fill(migrations.GAP_1_4_0[1]), fill(migrations.GAP_1_4_0[0])),
               fresh["_ROUTER.md"].replace(fill(migrations.UNCLEAR_1_4_0[1]), fill(migrations.UNCLEAR_1_4_0[0]))
                                  .replace(fill(migrations.SETTING_ROW) + "\n", ""))
        for name, text in zip(self.NAMES, old):
            self.assertNotEqual(text, fresh[name])
            write(config.SYSTEM / name, text)
        for _ in range(2):  # a second run changes nothing
            migrations._to_1_4_0()
            self.assertEqual({name: self.read(name) for name in self.NAMES}, fresh)
