"""The agents. The bot runs the Integrator after each message; the scheduler runs the rest."""
import json
import logging
import re
import time
import uuid
from datetime import date, datetime, timedelta

from . import config, i18n, llm, messenger, notes, tasks, telegram_api, vault, week, week_image

log = logging.getLogger(__name__)
RESULT = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{4}(?: \d+)?)\s*=>\s*(.+)$")
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def moment() -> dict:
    now = datetime.now(config.TIMEZONE)
    yesterday = now - timedelta(days=1)
    return {"today": f"{now:%Y-%m-%d}", "weekday": DAYS[now.weekday()], "time": f"{now:%H:%M}",
            "timezone": str(config.TIMEZONE), "yesterday": f"{yesterday:%Y-%m-%d}",
            "yesterday_weekday": DAYS[yesterday.weekday()]}


def request(name: str, **values) -> str:
    """The common block of rules, then the task of one agent."""
    now = moment()
    return llm.prompt("common", **now) + "\n\n" + llm.prompt(name, **now, **values)


FAILURES = config.STATE / "failures.json"
FAILURE_QUIET_HOURS = 6


def _report_failure(agent: str, detail: str) -> None:
    """Tells the user once. The same failure of the same agent is only logged again for some hours, so a retry
    every few minutes does not fill the chat."""
    log.error("%s failed: %s", agent, detail)
    try:
        seen = json.loads(FAILURES.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        seen = {}
    key = f"{agent}: {detail[:120]}"
    if time.time() - seen.get(key, 0) < FAILURE_QUIET_HOURS * 3600:
        return
    seen[key] = time.time()
    FAILURES.parent.mkdir(parents=True, exist_ok=True)
    FAILURES.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")
    telegram_api.send(i18n.t("bot.agent_failed", agent=agent, detail=detail[:300]))


RETRY_AFTER_MINUTES = 10


def retry_pending() -> str | None:
    """Captures that wait for more than 10 minutes get another Integrator run: the AI failed on them, or a
    restart stopped the run that had them."""
    old = time.time() - RETRY_AFTER_MINUTES * 60
    if not any(p.stat().st_mtime < old for p in vault.engrams("pending")):
        return None
    return integrator()


def _after_writing() -> None:
    """After each agent that writes: a short fix run if the vault broke the rules, and the week's
    checkboxes linked to the task notes again."""
    ok, output = vault.validate()
    if not ok:
        answer = llm.run(request("fix", lint=output), "fix", write=True, max_turns=40)
        if answer.error:
            log.warning("fix run failed: %s", answer.text)
    tasks.sync()


def integrator() -> str | None:
    with vault.lock():
        vault.pull()
        pending = vault.engrams("pending")
        if not pending:
            return None
        messenger.record_reactions(pending)
        vault.publish(f"capture: {len(pending)} engram(s)")
        listing = "\n".join(f"- {vault.rel(p)}" for p in pending)
        answer = llm.run(request("integrator", engrams=listing), "integrator", write=True)
        _after_writing()
        results = {} if answer.error else parse_results(answer.text)
        vault.publish(f"integrator: {len(pending)} engram(s)\n\n{answer.text[:2000]}")
    if answer.error:
        _report_failure("Integrator", answer.text)
    _update_confirmations(pending, results)
    return answer.text


def parse_results(text: str) -> dict[str, str]:
    """Reads the `<engram> => <what was done>` lines of the Integrator's answer."""
    found = {}
    for line in text.splitlines():
        m = RESULT.match(line.strip().strip("`"))
        if m:
            found[m[1]] = m[2].strip().strip("`")
    return found


def _update_confirmations(engrams: list, results: dict[str, str]) -> None:
    """Replaces the "Integrating…" of each capture with what the Integrator did with it."""
    for engram in engrams:
        fm, _ = notes.read(engram)
        confirmation = fm.get("confirmation")
        if not confirmation or engram.stem not in results:
            continue
        channel, correction = fm.get("channel", ""), bool(fm.get("corrects"))
        telegram_api.edit(int(confirmation), telegram_api.capture_text(channel, correction, results[engram.stem]),
                          telegram_api.capture_buttons(engram.stem, channel, correction, results[engram.stem]))


def _deadlines(today: date, days: int = 7) -> str:
    lines = [f"- {t.name} · due {t.due} · {(t.due - today).days} days left"
             for t in tasks.with_due_date() if (t.due - today).days <= days]
    return "\n".join(lines) or "none"


# An unclear capture is asked in this many morning summaries. With no answer, it is then set aside.
UNCLEAR_ASKS = 2


def _quiet_left(moment: datetime) -> float:
    """Seconds until the quiet hours end, or 0 outside them."""
    if not messenger.quiet(moment):
        return 0
    end = moment.replace(hour=config.QUIET_UNTIL // 60, minute=config.QUIET_UNTIL % 60, second=0, microsecond=0)
    if end <= moment:
        end += timedelta(days=1)
    return (end - moment).total_seconds()


def _asked(path) -> int:
    return int(re.sub(r"\D", "", str(notes.read(path)[0].get("asked_in_summary", ""))) or 0)


def _unclear_for_summary() -> tuple[list, list]:
    """The unclear captures to ask about in the summary, and the ones already asked in two summaries with no
    answer, to set aside."""
    ask, drop = [], []
    for path in vault.engrams("unclear"):
        (drop if _asked(path) >= UNCLEAR_ASKS else ask).append(path)
    return ask, drop


def _after_summary(ask: list, drop: list) -> None:
    """Counts the summary in each capture it asked about. The ones set aside become `discarded`: the summary said
    so once, and their text stays as it is."""
    for path in ask:
        notes.set_field(path, "asked_in_summary", str(_asked(path) + 1))
    for path in drop:
        notes.set_field(path, "state", "discarded")


def _send_week_image(today: date) -> None:
    """The week as an image. It needs no AI, so it goes out even when the text of the summary failed."""
    try:
        image = week_image.week_png(today)
        if image:
            telegram_api.send_photo(image)
    except Exception:  # noqa: BLE001 - the image is an extra
        log.exception("week image failed")


def morning() -> str:
    """The morning summary: today, what code moved since the last summary, deadlines and doubts, then the week as
    an image. When the time of the summary is inside the quiet hours, it waits for their end."""
    wait = _quiet_left(datetime.now(config.TIMEZONE))
    if wait:
        log.info("the morning summary waits %d minutes, until the quiet hours end", wait // 60)
        time.sleep(wait)
    integrator()
    with vault.lock():
        vault.pull()
        messenger.mark_ignored()
        today = datetime.now(config.TIMEZONE).date()
        week.create_if_missing(today)
        tasks.sync(today)
        week.reschedule(today)
        week.refresh(today)
        _point_dashboard(vault.week_name(today))
        moves = week.take_moves(today)  # the Messenger moved most of them at midnight
        ask, drop = _unclear_for_summary()
        answer = llm.run(request(
            "morning",
            moved="\n".join(f"- {week.describe(m, today)}" for m in moves) or "none",
            week=f"1. 🎯 Intention/Week/{vault.week_name(today)}.md",
            log_yesterday=f"6. ⚙️ System/_Logs/Log {today - timedelta(days=1)}.md",
            log_today=f"6. ⚙️ System/_Logs/Log {today}.md",
            deadlines=_deadlines(today),
            unclear="\n".join(f"- {vault.rel(p)}" for p in ask) or "none",
            dropped="\n".join(f"- {vault.rel(p)}" for p in drop) or "none",
            date_example=week_image.day_month(today),
        ), "morning", max_turns=30)
        failed = answer.error or not answer.text
        if failed:
            week.record_moves(moves)  # they wait for the next summary
        else:
            tg_id = telegram_api.send(answer.text)
            messenger.record("summary", "[[_Seahorse]]", answer.text, tg_id, uuid.uuid4().hex[:6])
            _after_summary(ask, drop)
        _send_week_image(today)
        vault.publish("messenger: morning summary")
    if failed:
        _report_failure("Morning summary", answer.text)
    return answer.text


def reviewer() -> str:
    with vault.lock():
        vault.pull()
        messenger.mark_ignored()
        _, output = vault.validate()
        changed = "\n".join(f"- {c}" for c in vault.changed_since(24)) or "none"
        answer = llm.run(request("reviewer", lint=output, changed=changed), "reviewer", write=True)
        _after_writing()
        vault.publish(f"reviewer: {answer.text[:150]}")
    if answer.error:
        _report_failure("Reviewer", answer.text)
    return answer.text


def week_after(day: date) -> date:
    """The Monday of the week that starts after the day: tomorrow on a Sunday, the next Monday on any other day."""
    return day + timedelta(days=7 - day.weekday())


def weekly() -> str:
    """The Planner plans the week that starts after today, whatever the day of the run, and the plan goes out at
    once. Then the Observer. Each one holds the vault lock only for its own run, so captures and reminders do
    not wait for both."""
    integrator()
    today = datetime.now(config.TIMEZONE).date()
    plan = plan_week(week_after(today))
    with vault.lock():
        vault.pull()
        observer = llm.run(request("observer", current_week=vault.week_name(today)), "observer", write=True,
                           max_turns=120, timeout=5400)
        _after_writing()
        vault.publish(f"observer: {observer.text[:150]}")
    if observer.error:
        _report_failure("Observer", observer.text)
    return plan


PLAN_FIX = ("The week file `1. 🎯 Intention/Week/{name}.md` is not right yet:\n{problems}\nFix it: seven cards, one for "
            "each day, each title with the weekday and the date as dd/mm, such as `> [!day] Mon 12/01`. Keep the tasks "
            "you planned. Then give your final answer again.")


def plan_week(monday: date) -> str:
    """Runs the Planner for the week that starts on `monday`, checks the file, and sends the plan.

    Code writes the week file first, with its seven empty cards, and the Planner fills them. Then the file must
    have seven readable cards (`week.problems`). If not, the Planner runs once more with the problems, code
    repairs what it can (the titles, a missing day), and the user hears about what was wrong."""
    name, ending = vault.week_name(monday), vault.week_name(monday - timedelta(days=7))
    path = config.WEEKS / f"{name}.md"
    today = datetime.now(config.TIMEZONE).date()
    with vault.lock():
        vault.pull()
        week.create_if_missing(monday)
        ask = request("planner", current_week=ending, next_week=name, week_start=monday.isoformat(),
                      date_example=week_image.day_month(monday))
        plan = llm.run(ask, "planner", write=True, max_turns=120, timeout=5400)
        _after_writing()
        problems = week.problems(path)
        if problems and not plan.error:
            fix = PLAN_FIX.format(name=name, problems="\n".join(f"- {p}" for p in problems))
            again = llm.run(f"{ask}\n\n{fix}", "planner", write=True, max_turns=60, timeout=3600)
            _after_writing()
            plan = again if not again.error and again.text else plan
            problems = week.problems(path)
        week.create_if_missing(monday)
        week.refresh(monday)
        if monday <= today + timedelta(days=1):
            _point_dashboard(name)
        vault.publish(f"planner: week {name}")
        if not plan.error and plan.text:
            tg_id = telegram_api.send(plan.text)
            messenger.record("week", f"[[{name}]]", plan.text, tg_id, uuid.uuid4().hex[:6])
            vault.publish("messenger: week proposal")
    if plan.error or not plan.text:
        _report_failure("Planner", plan.text)
    elif problems:
        _report_failure("Planner", f"the week {name} had problems, code repaired what it could: " + "; ".join(problems))
    return plan.text


def plan_again(today: date | None = None) -> str:
    """Plans the current week again, from today on. For a `/plan` command."""
    today = today or datetime.now(config.TIMEZONE).date()
    return plan_week(today - timedelta(days=today.weekday()))


def _point_dashboard(week_name: str) -> None:
    """The Dashboard embeds the current week. This points the embed to the given week, if it exists. The morning
    summary and the Planner call it."""
    if not config.DASHBOARD.exists() or not (config.WEEKS / f"{week_name}.md").exists():
        return
    text = config.DASHBOARD.read_text(encoding="utf-8")
    new = re.sub(r"!\[\[\d{4}-W\d{2}\]\]", f"![[{week_name}]]", text)
    if new != text:
        config.DASHBOARD.write_text(new, encoding="utf-8")


def _read_notes_state() -> dict:
    try:
        return json.loads(config.NOTES_STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_notes_state(state: dict) -> None:
    config.NOTES_STATE.parent.mkdir(parents=True, exist_ok=True)
    config.NOTES_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def user_notes() -> str | None:
    """Reads the notes the user wrote in Obsidian and integrates what they say into the rest of the vault.

    It runs every few minutes. With no change from the user, it is only git, with no model. A note is read only
    after it stays quiet for NOTES_QUIET_SECONDS, so the agent does not interrupt the writing.
    """
    with vault.lock():
        vault.pull()
        head = vault.git("rev-parse", "HEAD", check=False).stdout.strip()
        if not head:
            return None
        state = _read_notes_state()
        if not state.get("last_commit"):
            _save_notes_state({"last_commit": head, "pending": {}})
            return None
        pending = state.get("pending", {})
        for path, when in vault.user_changes(state["last_commit"]).items():
            base = pending.get(path, {}).get("base", state["last_commit"])
            pending[path] = {"base": base, "when": when}
        pending = {p: d for p, d in pending.items() if (config.VAULT / p).exists()}
        now = time.time()
        quiet = sorted((p for p, d in pending.items() if now - d["when"] >= config.NOTES_QUIET_SECONDS),
                       key=lambda p: pending[p]["when"])
        # In batches: a big import enters little by little, without using the whole AI quota at once.
        ready = {p: pending[p] for p in quiet[:config.NOTES_PER_RUN]}
        if not ready:
            _save_notes_state({"last_commit": head, "pending": pending})
            return None
        blocks = []
        for path, data in ready.items():
            diff = vault.git("diff", data["base"], "HEAD", "--", path, check=False).stdout.strip()
            blocks.append(f"### {path}\n```diff\n{diff[:6000] or '(no diff: read the whole note)'}\n```")
        answer = llm.run(request("notes", notes="\n".join(blocks)), "notes", write=True)
        _after_writing()
        vault.publish(f"integrator: notes by {config.USER_NAME}\n\n{answer.text[:2000]}")
        if not answer.error:
            for path in ready:
                pending.pop(path, None)
        _save_notes_state({"last_commit": vault.git("rev-parse", "HEAD").stdout.strip(), "pending": pending})
    if answer.error:
        _report_failure("Integrator", answer.text)
        return answer.text
    lines = [line.split("=>", 1)[1].strip() for line in answer.text.splitlines() if "=>" in line]
    # Imports do not message on Telegram: it would be dozens of messages about old notes.
    only_imported = all(notes.read(config.VAULT / p)[0].get("imported_from") for p in ready if (config.VAULT / p).exists())
    if lines and not only_imported and not messenger.quiet(datetime.now(config.TIMEZONE)):
        telegram_api.send(i18n.t("bot.notes_read") + "\n" + "\n".join(f"→ {line}" for line in lines))
    return answer.text


AGENTS = {"integrator": integrator, "notes": user_notes, "morning": morning, "reviewer": reviewer,
          "weekly": weekly, "messenger": messenger.run}
