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


def morning() -> str:
    integrator()
    with vault.lock():
        vault.pull()
        messenger.mark_ignored()
        today = datetime.now(config.TIMEZONE).date()
        week.create_if_missing(today)
        tasks.sync(today)
        moved = "\n".join(f"- {m}" for m in week.reschedule(today)) or "none"
        unclear = "\n".join(f"- {vault.rel(p)}" for p in vault.engrams("unclear")) or "none"
        answer = llm.run(request(
            "morning",
            moved=moved,
            week=f"1. 🎯 Intention/Week/{vault.week_name(today)}.md",
            log_yesterday=f"6. ⚙️ System/_Logs/Log {today - timedelta(days=1)}.md",
            log_today=f"6. ⚙️ System/_Logs/Log {today}.md",
            deadlines=_deadlines(today),
            unclear=unclear,
        ), "morning", max_turns=30)
        if answer.error or not answer.text:
            _report_failure("Morning summary", answer.text)
            return answer.text
        message_id = uuid.uuid4().hex[:6]
        tg_id = telegram_api.send(answer.text)
        messenger.record("summary", "[[_Seahorse]]", answer.text, tg_id, message_id)
        try:
            image = week_image.week_png(today)
            if image:
                telegram_api.send_photo(image)
        except Exception:  # noqa: BLE001 - the image is an extra: the text summary already went out
            log.exception("week image failed")
        vault.publish("messenger: morning summary")
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


def weekly() -> str:
    integrator()
    today = datetime.now(config.TIMEZONE).date()
    current, upcoming = vault.week_name(today), vault.week_name(today + timedelta(days=7))
    with vault.lock():
        vault.pull()
        plan = llm.run(request("planner", current_week=current, next_week=upcoming), "planner",
                       write=True, max_turns=120, timeout=5400)
        _after_writing()
        _point_dashboard(upcoming)
        vault.publish(f"planner: week {upcoming}")
        observer = llm.run(request("observer", current_week=current), "observer", write=True, max_turns=120, timeout=5400)
        _after_writing()
        vault.publish(f"observer: {observer.text[:150]}")
        if plan.error or not plan.text:
            _report_failure("Planner", plan.text)
        else:
            message_id = uuid.uuid4().hex[:6]
            tg_id = telegram_api.send(plan.text)
            messenger.record("week", f"[[{upcoming}]]", plan.text, tg_id, message_id)
            vault.publish("messenger: week proposal")
    if observer.error:
        _report_failure("Observer", observer.text)
    return plan.text


def _point_dashboard(week_name: str) -> None:
    """The Dashboard embeds the current week. This points the embed to the new week, if it exists."""
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
