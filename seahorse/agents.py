"""The agents. The bot runs the Integrator after each message; the scheduler runs the rest."""
import hashlib
import json
import logging
import re
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config, i18n, llm, messenger, notes, retro, tasks, telegram_api, vault, week, week_image

log = logging.getLogger(__name__)
ENGRAM = r"\d{4}-\d{2}-\d{2} \d{4}(?: \d+)?"
# A result line is `<engram> => <what was done>`. Models add a bullet, a link, bold, backticks or the path: all are read.
RESULT = re.compile(rf"^(?:[-*•+]\s+|\d+[.)]\s+)?[\[`*_\s]*(?:[^\[\]`*]*/)?({ENGRAM})(?:\.md)?[\]`*_\s]*(?:=>|->|→)\s*(.+)$")
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


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _number(value) -> int:
    return int(re.sub(r"\D", "", str(value or "")) or 0)


FAILURES = config.STATE / "failures.json"
ALERTS = config.STATE / "alerts.json"
FAILURE_QUIET_HOURS = 6
# The names that the agents give to `_report_failure`, and the key of each agent's name in the texts.
AGENT_KEYS = {"integrator": "integrator", "notes": "notes", "morning": "morning", "morning summary": "morning",
              "reviewer": "reviewer", "planner": "planner", "observer": "observer"}
NEXT_TRY = {"integrator": "alert.next_integrator", "notes": "alert.next_notes"}


def failure_signature(detail: str) -> str:
    """The failure without what changes from one run to the next: links, ids and numbers."""
    text = detail.lower()
    text = re.sub(r"https?://\S+", "<url>", text)
    text = re.sub(r"\b(?=[\w-]*\d)(?=[\w-]*[a-z])[\w-]{12,}\b", "<id>", text)
    text = re.sub(r"\d+", "#", text)
    return " ".join(text.split())[:120]


def _report_failure(agent: str, detail: str) -> None:
    """Tells the user once. The same failure of the same agent is only logged again for some hours, so a retry
    every few minutes does not fill the chat. The message is in the user's language, says what happens next,
    and ends with the raw detail, short."""
    log.error("%s failed: %s", agent, detail)
    key = AGENT_KEYS.get(agent.lower(), agent)
    seen = _load(FAILURES)
    signature = f"{key}: {failure_signature(detail)}"
    if time.time() - seen.get(signature, 0) < FAILURE_QUIET_HOURS * 3600:
        return
    seen[signature] = time.time()
    _save(FAILURES, seen)
    name = i18n.t(f"agent.{key}") if key in AGENT_KEYS.values() else agent
    short = " ".join(detail.split())
    short = short if len(short) <= 160 else short[:159] + "…"
    _alert(i18n.t("alert.failed", agent=name, next=i18n.t(NEXT_TRY.get(key, "alert.next_other")), detail=short),
           signature)


def _alert(text: str, key: str | None = None) -> None:
    """Sends an alert to the user. In the quiet hours it waits, and goes out at the first tick after them."""
    if messenger.quiet(datetime.now(config.TIMEZONE)):
        held = _load(ALERTS)
        held[key or text] = text
        _save(ALERTS, held)
        return
    send_held_alerts()
    telegram_api.send(text)


def send_held_alerts() -> None:
    """The alerts kept during the quiet hours. The timers call this, and it does nothing in the quiet hours."""
    held = _load(ALERTS)
    if not held or messenger.quiet(datetime.now(config.TIMEZONE)):
        return
    _save(ALERTS, {})
    for text in held.values():
        telegram_api.send(text)


RETRY_AFTER_MINUTES = 10


def retry_pending() -> str | None:
    """Captures that wait for more than 10 minutes get another Integrator run: the AI failed on them, or a
    restart stopped the run that had them. After three failed runs a capture becomes unclear, so this stops."""
    send_held_alerts()
    old = time.time() - RETRY_AFTER_MINUTES * 60
    if not any(p.stat().st_mtime < old for p in vault.engrams("pending")):
        return None
    return integrator()


LINT_STATE = config.STATE / "lint.json"


def _lint_errors(output: str) -> list[str]:
    return sorted(line for line in output.splitlines() if line.startswith("ERROR"))


def _digest(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest() if lines else ""


def _after_writing() -> None:
    """After each agent that writes: a short fix run if the vault broke the rules, and the week's
    checkboxes linked to the task notes again. Errors that a fix run already tried and could not fix do not
    start another paid run: only a different set of errors does."""
    ok, output = vault.validate()
    errors = [] if ok else _lint_errors(output)
    if errors and _digest(errors) != _load(LINT_STATE).get("errors"):
        answer = llm.run(request("fix", lint=output), "fix", write=True, max_turns=40)
        if answer.error:
            log.warning("fix run failed: %s", answer.text)
        ok, output = vault.validate()
        errors = [] if ok else _lint_errors(output)
    elif errors:
        log.info("lint: the same %s error(s) that a fix run could not fix, no new fix run", len(errors))
    _save(LINT_STATE, {"errors": _digest(errors)})
    tasks.sync()
    week.refresh()  # the totals in the day-card titles come from code, never from the model


CAPTURES = config.STATE / "captures.json"
BATCH = 5
MAX_BATCHES = 10
MAX_ATTEMPTS = 3
KEEP_RESULTS = 300
WAIT_DAYS = 2


def integrator() -> str | None:
    """Integrates the pending captures in batches of a few, oldest first, so one run never reaches the turn
    limit. It goes on while batches succeed. It is safe to call at any time: what a run leaves behind goes
    into the next one, and every confirmation still on "Integrating…" is edited when its capture is done."""
    send_held_alerts()
    answers = []
    for _ in range(MAX_BATCHES):
        answer, go_on = _integrate_batch()
        if answer is None:
            break
        answers.append(answer)
        if not go_on:
            break
    if not answers:
        _update_confirmations([], {})
        return None
    return "\n".join(answers)


def _integrate_batch() -> tuple[str | None, bool]:
    """One Integrator run on the oldest pending captures. Returns its answer, and whether the next batch may
    start: only when the run worked and no capture of the batch is still pending."""
    with vault.lock():
        vault.pull()
        pending = vault.engrams("pending")
        if not pending:
            return None, False
        batch = pending[:BATCH]
        _remember(batch)
        messenger.record_reactions(batch)
        vault.publish(f"capture: {len(pending)} engram(s)")
        listing = "\n".join(f"- {vault.rel(p)}" for p in batch)
        answer = llm.run(request("integrator", engrams=listing, delivery_time=_delivery_time()), "integrator",
                         write=True, max_turns=120)
        _after_writing()
        results = parse_results(answer.text)
        left = _count_attempts(batch)
        vault.publish(f"integrator: {len(batch)} engram(s)\n\n{answer.text[:2000]}")
    if answer.error:
        _report_failure("integrator", answer.text)
    _update_confirmations(batch, results)
    return answer.text, not answer.error and not left


def _delivery_time() -> str:
    """When a delivery queued now reaches the user: the Messenger sends it in minutes, or after the quiet hours."""
    if messenger.quiet(datetime.now(config.TIMEZONE)):
        return f"at {config.QUIET_UNTIL // 60:02d}:{config.QUIET_UNTIL % 60:02d}, after the quiet hours"
    return "in a few minutes"


def _count_attempts(batch: list[Path]) -> list[Path]:
    """Captures that a run left pending get one more attempt. At the third, they become unclear: no more paid
    runs on them, the log asks the user through the morning summary, and the user hears it once.
    Returns the captures that are still pending."""
    left, given_up = [], []
    for path in batch:
        if not path.exists():
            continue
        fm = notes.read(path)[0]
        if fm.get("state") != "pending":
            continue
        attempts = _number(fm.get("attempts")) + 1
        notes.set_field(path, "attempts", str(attempts))
        if attempts < MAX_ATTEMPTS:
            left.append(path)
            continue
        notes.set_field(path, "state", "unclear")
        given_up.append(path)
    if given_up:
        _log_given_up(given_up)
        # A capture with a confirmation shows it there. The others have no message to edit.
        if any(not notes.read(p)[0].get("confirmation") for p in given_up):
            _alert(i18n.t("alert.gave_up", count=len(given_up), tries=MAX_ATTEMPTS))
    return left


def _log_given_up(paths: list[Path]) -> None:
    now = datetime.now(config.TIMEZONE)
    links = ", ".join(f"[[{p.stem}]]" for p in paths)
    entry = (f"## [{now:%Y-%m-%d %H:%M}] integrator | gave up after {MAX_ATTEMPTS} failed runs\n"
             f"- Engrams read: {links}\n"
             f"- Doubts: the Integrator failed {MAX_ATTEMPTS} times on {links}. Ask {config.USER_NAME} what to do with each one.\n")
    path = config.LOGS / f"Log {now:%Y-%m-%d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8").rstrip("\n") + "\n" if path.exists() else "---\ntype: log\n---\n"
    path.write_text(text + entry, encoding="utf-8")


def parse_results(text: str) -> dict[str, str]:
    """Reads the `<engram> => <what was done>` lines of the Integrator's answer. Two lines for the same engram
    become two parts of its result."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        m = RESULT.match(line.strip().strip("`").strip())
        if not m:
            continue
        value = m[2].strip().strip("`").strip()
        if value.startswith("**") and value.endswith("**"):
            value = value[2:-2].strip()
        if value:
            found[m[1]] = f"{found[m[1]]} | {value}" if m[1] in found else value
    return found


def _captures() -> dict:
    data = _load(CAPTURES)
    return {"waiting": data.get("waiting", []), "results": data.get("results", {})}


def _save_captures(data: dict) -> None:
    results = dict(sorted(data["results"].items())[-KEEP_RESULTS:])
    _save(CAPTURES, {"waiting": data["waiting"], "results": results})


def _remember(batch: list[Path]) -> None:
    """Before a run: the captures whose confirmation says "Integrating…". If the run stops halfway, as in a
    restart, a later run still edits them."""
    data = _captures()
    data["waiting"] = list(dict.fromkeys(data["waiting"] + [p.stem for p in batch]))
    _save_captures(data)


def _update_confirmations(batch: list[Path], results: dict[str, str]) -> None:
    """Replaces the "Integrating…" of each capture that left `pending` with what the Integrator did with it:
    its result line, or a short text from its state when the run wrote none. Captures still pending wait for
    the next run."""
    data = _captures()
    oldest = (datetime.now(config.TIMEZONE) - timedelta(days=WAIT_DAYS)).strftime("%Y-%m-%d")
    in_batch = [p.stem for p in batch]
    waiting = []
    for name in dict.fromkeys(in_batch + data["waiting"]):
        path = vault.engram(name)
        if not path or (name not in in_batch and name < oldest):
            continue  # gone, or left from days ago: a later batch has it if it is still pending
        fm = notes.read(path)[0]
        if fm.get("state") == "pending":
            waiting.append(name)
            continue
        if not fm.get("confirmation"):
            continue
        result = results.get(name)
        if not _confirm(path, fm, result, data["results"]):
            waiting.append(name)  # Telegram did not take the edit: the next run tries again
        data["results"][name] = result or _fallback(fm)
    data["waiting"] = waiting
    _save_captures(data)


def _fallback(fm: dict) -> str:
    """What a confirmation says when the Integrator wrote no result line for the capture."""
    state = fm.get("state")
    if state == "unclear":
        return "? " + i18n.t("result.unclear")
    return i18n.t("result.nothing" if state == "discarded" else "result.filed")


def _answered(fm: dict) -> Path | None:
    """The capture that this one answers, such as the doubt it resolves."""
    link = notes.links(str(fm.get("answers_engram", "")))
    return vault.engram(link[0]) if link else None


def _was_doubt(fm: dict, shown: str) -> bool:
    """The confirmation asked the user something: a doubt, or the three failed runs."""
    lines = telegram_api.result_lines(shown.strip().removeprefix("!"))
    return any(line.startswith("?") for line in lines) or _number(fm.get("attempts")) >= MAX_ATTEMPTS


def _confirm(path: Path, fm: dict, result: str | None, results: dict[str, str]) -> bool:
    """Edits the confirmation of one capture. An answer that resolves a doubt also edits the doubt, so its old
    question goes away. "Put in the week" answers on the same message as the capture: that message keeps
    what it said, and only gets the decision under it."""
    original = _answered(fm)
    if original:
        first = notes.read(original)[0]
        if first.get("confirmation") == fm["confirmation"]:
            return _edit_week_decision(original, first, result or _fallback(fm), results)
        resolved = first.get("state") not in ("pending", "unclear")
        if first.get("confirmation") and resolved and _was_doubt(first, results.get(original.stem, "")):
            results[original.stem] = (result or i18n.t("result.filed")).strip().removeprefix("!").strip()
            _edit(original, first, results[original.stem])
    return _edit(path, fm, result)


def _send_edit(fm: dict, text: str, buttons: list) -> bool:
    """One edit on Telegram. A wrong id or a network error is logged, and the other confirmations still go."""
    message_id = str(fm.get("confirmation", ""))
    if not message_id.isdigit():
        return True  # there is nothing that Telegram can edit
    try:
        return telegram_api.edit(int(message_id), text, buttons)
    except Exception:  # noqa: BLE001 - one failed edit must not stop the others
        log.exception("could not edit the confirmation %s", message_id)
        return False


def _edit(path: Path, fm: dict, result: str | None) -> bool:
    channel, correction = fm.get("channel", ""), bool(fm.get("corrects"))
    if result is None and fm.get("state") == "unclear" and _number(fm.get("attempts")) >= MAX_ATTEMPTS:
        text = telegram_api.capture_text(channel, correction, "") + "\n\n" + i18n.t("result.gave_up", tries=MAX_ATTEMPTS)
        return _send_edit(fm, text, telegram_api.capture_buttons(path.stem, channel, correction))
    result = result or _fallback(fm)
    return _send_edit(fm, telegram_api.capture_text(channel, correction, result),
                      telegram_api.capture_buttons(path.stem, channel, correction, result))


def _edit_week_decision(original: Path, fm: dict, decision: str, results: dict[str, str]) -> bool:
    """The confirmation of a task that did not fit, after "Put in the week": the same title, lines and buttons
    as before, without the question, and the decision as the last line."""
    channel, correction = fm.get("channel", ""), bool(fm.get("corrects"))
    before = results.get(original.stem, "").strip().removeprefix("!").strip() or i18n.t("result.filed")
    lines = [f"\n→ {line}" for line in telegram_api.result_lines(decision.strip().removeprefix("!"))
             if not line.startswith("?")]
    text = telegram_api.capture_text(channel, correction, before) + "".join(lines)
    return _send_edit(fm, text, telegram_api.capture_buttons(original.stem, channel, correction, before))


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
    """First the image of the week that ends (`retro.send_week`). Then the Planner plans the week that starts after
    today, whatever the day of the run, and the plan goes out at once. Then the Observer. Each one holds the vault
    lock only for its own run, so captures and reminders do not wait for both."""
    integrator()
    today = datetime.now(config.TIMEZONE).date()
    retro.send_week(today)
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
    return _load(config.NOTES_STATE)


def _save_notes_state(state: dict) -> None:
    _save(config.NOTES_STATE, state)


WISHLIST = "1. 🎯 Intention/Wishlist/"
EXPERIENCES = "2. 📓 Memory/Experiences/"


def _frontmatter_edits(ready: dict) -> list[str]:
    """Edits of the user that change only the frontmatter, such as a checkbox in a table, need no model run.
    A wish checked as done moves to Experiences. Any other such edit stays as it is, recorded in git."""
    handled = []
    for path, data in ready.items():
        before = vault.text_at(data["base"], path)
        if before is None:
            continue
        fm, body = notes.read(config.VAULT / path)
        if notes.parse(before)[1].strip() != body.strip():
            continue
        _finish_wish(path, fm)
        handled.append(path)
    return handled


def _finish_wish(path: str, fm: dict) -> None:
    """A wish with `done: true` becomes an experience, in the same kind under Memory. Purchases stay in Shopping."""
    rest = path.removeprefix(WISHLIST)
    if (rest == path or "/" not in rest or rest.startswith("Shopping/") or fm.get("type") != "wish"
            or str(fm.get("done")).lower() != "true"):
        return
    target = config.VAULT / EXPERIENCES / rest
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if vault.git("mv", "--", path, f"{EXPERIENCES}{rest}", check=False).returncode != 0:
        return
    notes.set_field(target, "type", "experience")
    if not fm.get("when"):
        notes.set_field(target, "when", f"{datetime.now(config.TIMEZONE):%Y-%m}")


def _owner_label(path: str) -> str:
    """Whose note it is. Only a note that the user made becomes `written_by: owner`. An edit of the user in a
    note that an agent made is read, but the note stays the agents'."""
    fm = notes.read(config.VAULT / path)[0]
    if fm.get("written_by") == "owner" or fm.get("imported_from") or vault.created_by_user(path):
        return f"own note of {config.USER_NAME}"
    return f"Seahorse note, edited by {config.USER_NAME}"


def _notes_lines(text: str) -> list[str]:
    """The `<note name> => <what was done>` lines, with the name kept, for the message to the user."""
    found = []
    for line in text.splitlines():
        if "=>" not in line:
            continue
        name, what = (part.strip() for part in line.split("=>", 1))
        name, what = name.strip("-*•`[] ").strip(), what.strip("`* ").strip()
        if what:
            found.append(f"{name}: {what}" if name else what)
    return found


def user_notes() -> str | None:
    """Reads the notes the user wrote in Obsidian and integrates what they say into the rest of the vault.

    It runs every few minutes. With no change from the user, it is only git, with no model. A note is read only
    after it stays quiet for NOTES_QUIET_SECONDS, so the agent does not interrupt the writing. An edit that only
    changes the frontmatter is handled in code. A note that fails three runs is left until the user edits it again.
    """
    send_held_alerts()
    given_up: list[str] = []
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
        for path in _frontmatter_edits(ready):
            ready.pop(path)
            pending.pop(path, None)
        if not ready:
            vault.publish(f"notes: frontmatter edits by {config.USER_NAME}")
            _save_notes_state({"last_commit": vault.git("rev-parse", "HEAD").stdout.strip(), "pending": pending})
            return None
        blocks = []
        for path, data in ready.items():
            diff = vault.git("diff", data["base"], "HEAD", "--", path, check=False).stdout.strip()
            blocks.append(f"### {path} ({_owner_label(path)})\n```diff\n{diff[:6000] or '(no diff: read the whole note)'}\n```")
        answer = llm.run(request("notes", notes="\n".join(blocks)), "notes", write=True)
        _after_writing()
        vault.publish(f"integrator: notes by {config.USER_NAME}\n\n{answer.text[:2000]}")
        for path in ready:
            if not answer.error:
                pending.pop(path, None)
                continue
            pending[path]["attempts"] = pending[path].get("attempts", 0) + 1
            if pending[path]["attempts"] >= MAX_ATTEMPTS:
                given_up.append(path)
                pending.pop(path)
        _save_notes_state({"last_commit": vault.git("rev-parse", "HEAD").stdout.strip(), "pending": pending})
    if answer.error:
        _report_failure("notes", answer.text)
        if given_up:
            _alert(i18n.t("alert.notes_gave_up", notes=", ".join(Path(p).stem for p in given_up), tries=MAX_ATTEMPTS))
        return answer.text
    lines = _notes_lines(answer.text)
    # Imports do not message on Telegram: it would be dozens of messages about old notes.
    only_imported = all(notes.read(config.VAULT / p)[0].get("imported_from") for p in ready if (config.VAULT / p).exists())
    if lines and not only_imported and not messenger.quiet(datetime.now(config.TIMEZONE)):
        telegram_api.send(i18n.t("bot.notes_read") + "\n" + "\n".join(f"→ {line}" for line in lines))
    return answer.text


AGENTS = {"integrator": integrator, "notes": user_notes, "morning": morning, "reviewer": reviewer,
          "weekly": weekly, "messenger": messenger.run}
