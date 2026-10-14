"""The Messenger: the code that decides what goes out on Telegram, and when. Rules in `6. ⚙️ System/_MESSENGER.md`.

It runs every ten minutes and sends at most one message per run: first a deadline reminder, then something
Seahorse promised (a delivery), then, at a drawn moment, a conversation of the Companion.
It never sends during the quiet hours. A model only writes the extra line of a reminder.
"""
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config, i18n, llm, notes, tasks, telegram_api, vault, week

log = logging.getLogger(__name__)

# The kinds of conversation the Companion starts. "proposal" comes from the other agents, through the queue.
CONVERSATION = {"revival", "question", "recommendation", "curiosity", "research", "connection", "proposal",
                "checkin", "release", "news", "unblock"}
SPONTANEOUS = CONVERSATION | {"reminder", "delivery"}
LINE = re.compile(r"^- (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) · (\S+) · id:(\w+) · tg:(\d*) · (.*?) · reaction: (.+?)(?: · (.*))?$")
# The queue date may carry a time: the item waits for that moment.
QUEUE_ITEM = re.compile(r"^- \[ \] (\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}))? · (\S+) · (high|normal) · (.+) · (\[\[[^\]]+\]\])\s*$")
IGNORE = "ign"
REMIND_DAYS_BEFORE = 7
ONE_HOUR = timedelta(hours=1)


@dataclass
class Message:
    when: datetime
    kind: str
    id: str
    tg: str
    source: str
    reaction: str


@dataclass
class Candidate:
    kind: str
    source: str
    text: str | None
    urgent: bool
    priority: str
    task: tasks.Task | None = None
    queue_line: str | None = None


def now() -> datetime:
    return datetime.now(config.TIMEZONE)


def quiet(moment: datetime) -> bool:
    """Inside the quiet hours. They may cross midnight, such as 22:00 to 07:00."""
    minute = moment.hour * 60 + moment.minute
    start, end = config.QUIET_FROM, config.QUIET_UNTIL
    return start <= minute < end if start <= end else minute >= start or minute < end


def messages() -> list[Message]:
    if not config.MESSAGES.exists():
        return []
    found = []
    for line in config.MESSAGES.read_text(encoding="utf-8").splitlines():
        m = LINE.match(line)
        if m:
            when = datetime.strptime(m[1], "%Y-%m-%d %H:%M").replace(tzinfo=config.TIMEZONE)
            found.append(Message(when, m[2], m[3], m[4], m[5], m[6].strip()))
    return found


def by_telegram(tg_id: int) -> Message | None:
    return next((m for m in messages() if m.tg == str(tg_id)), None)


def record(kind: str, source: str, text: str, tg_id: int | None, message_id: str) -> None:
    reaction = "pending" if kind in SPONTANEOUS else "—"
    summary = " ".join(text.split())[:90]
    line = f"- {now():%Y-%m-%d %H:%M} · {kind} · id:{message_id} · tg:{tg_id or ''} · {source} · reaction: {reaction} · {summary}"
    config.MESSAGES.parent.mkdir(parents=True, exist_ok=True)
    with config.MESSAGES.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _set_reaction(match, new: str) -> int:
    if not config.MESSAGES.exists():
        return 0
    lines, changed = config.MESSAGES.read_text(encoding="utf-8").splitlines(), 0
    for i, line in enumerate(lines):
        m = LINE.match(line)
        if m and match(m):
            lines[i] = line.replace(f"reaction: {m[6].strip()}", f"reaction: {new}", 1)
            changed += 1
    if changed:
        config.MESSAGES.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed


def record_reactions(engrams: list[Path]) -> None:
    """Engrams that answer a message mark the reaction on it. The Integrator calls this, with the lock."""
    replies = {i18n.t("reply.done").lower(): "acted", i18n.t("reply.liked").lower(): "acted",
               i18n.t("reply.later").lower(): "replied", i18n.t("reply.mute").lower(): "muted"}
    for engram in engrams:
        fm, body = notes.read(engram)
        message_id = fm.get("message_id")
        if message_id:
            # A button press is an action. Free text is a reply, unless it is one of the known shortcuts.
            reaction = "acted" if fm.get("via_button") else replies.get(body.strip().lower(), "replied")
            _set_reaction(lambda m, target=message_id: m[3] == target, reaction)


def set_reaction(message_id: str, reaction: str) -> None:
    """For the bot: records the reaction to a message, with the vault lock."""
    with vault.lock():
        _set_reaction(lambda m: m[3] == message_id, reaction)


def mark_ignored() -> None:
    limit = now() - timedelta(hours=24)

    def old_and_unanswered(m) -> bool:
        when = datetime.strptime(m[1], "%Y-%m-%d %H:%M").replace(tzinfo=config.TIMEZONE)
        return m[6].strip() == "pending" and m[2] in SPONTANEOUS and when < limit

    if _set_reaction(old_and_unanswered, "ignored"):
        log.info("old messages marked as ignored")


def muted() -> set[str]:
    if not config.CALIBRATION.exists():
        return set()
    text = config.CALIBRATION.read_text(encoding="utf-8")
    section = text.split("## Muted topics", 1)[-1].split("\n## ", 1)[0]
    items = set(notes.links(section))
    items |= {line.strip()[2:].strip() for line in section.splitlines() if line.strip().startswith("- ") and "[[" not in line}
    return {i for i in items if i and i.lower() not in {"none", "none."}}


def _is_muted(text: str, silenced: set[str]) -> bool:
    return any(s.lower() in text.lower() for s in silenced)


def conversation_buttons(message_id: str, labels: list[str]) -> list[tuple[str, str]]:
    """The buttons of a Companion message: the ones it proposed, and always the ignore button."""
    buttons = [(label, f"msg|{message_id}|b{i}") for i, label in enumerate(labels[:4])]
    return buttons + [(i18n.t("button.ignore"), f"msg|{message_id}|{IGNORE}")]


def _reminders(today: date, history: list[Message], silenced: set[str]) -> list[Candidate]:
    """Each task with a due date gets at most two reminders: a week before, and the day before."""
    found = []
    for task in tasks.with_due_date():
        source = f"[[{task.name}]]"
        if _is_muted(task.name, silenced):
            continue
        left = (task.due - today).days
        earlier = [m for m in history if m.kind == "reminder" and m.source == source
                   and m.when.date() >= task.due - timedelta(days=REMIND_DAYS_BEFORE + 1)]
        if left < 0 or any(m.when.date() == today for m in earlier):
            continue
        if left <= 1 and len(earlier) < 2:
            found.append(Candidate("reminder", source, None, True, "high", task))
        elif left <= REMIND_DAYS_BEFORE and not earlier:
            found.append(Candidate("reminder", source, None, False, "normal", task))
    return found


def next_reminder(moment: datetime, history: list[Message]) -> Candidate | None:
    """The deadline reminder to send now, if any. At most one per hour, so they never come in a burst."""
    if quiet(moment) or any(m.kind == "reminder" and moment - m.when < ONE_HOUR for m in history):
        return None
    found = _reminders(moment.date(), history, muted())
    found.sort(key=lambda c: not c.urgent)
    return found[0] if found else None


def from_queue(moment: datetime, deliveries: bool) -> list[Candidate]:
    """Queue items that may go out now. `deliveries` picks what was promised, or everything else."""
    found = []
    if not config.QUEUE.exists():
        return found
    silenced = muted()
    for line in config.QUEUE.read_text(encoding="utf-8").splitlines():
        m = QUEUE_ITEM.match(line)
        if not m or (m[3] == "delivery") != deliveries or notes.links(m[6])[0] in silenced:
            continue
        start = datetime.strptime(f"{m[1]} {m[2] or '00:00'}", "%Y-%m-%d %H:%M").replace(tzinfo=config.TIMEZONE)
        if start > moment or (moment.date() - start.date()).days > 7:
            continue
        found.append(Candidate(m[3], m[6], m[5].strip(), deliveries, m[4], queue_line=line))
    found.sort(key=lambda c: c.priority != "high")
    return found


def reminder_header(task: tasks.Task, today: date) -> str:
    """The two fixed lines of a reminder: the task, and when it is due with the time it takes."""
    left = (task.due - today).days
    when = i18n.t("reminder.today") if left == 0 else i18n.t("reminder.tomorrow") if left == 1 else i18n.words("days_long")[task.due.weekday()]
    estimate = str(task.fm.get("estimate", "")).strip()
    line = i18n.t("reminder.due", when=when, date=f"{task.due:%d/%m}") + (f" · {estimate}" if estimate else "")
    return f"⏰ {task.name}\n{line}"


def _reminder_text(c: Candidate, today: date) -> str:
    """The header is code. The model only adds one line of detail, when the task note has something to say."""
    task = c.task
    text = reminder_header(task, today)
    content = notes.read(task.path)[1].strip()
    if not content:
        return text
    answer = llm.run(llm.prompt("phrase", task=task.name, content=content[:3000], due=task.due.isoformat(),
                                days_left=str((task.due - today).days), repeat=task.repeat or "no"),
                     "phrase", max_turns=1, timeout=120)
    detail = "" if answer.error or not answer.text.strip() else answer.text.strip().splitlines()[0].strip()
    return text if detail in {"", "-"} else f"{text}\n{detail[:120]}"


def _expire_queue() -> None:
    """Items older than seven days leave the active queue, marked as expired."""
    if not config.QUEUE.exists():
        return
    today, lines, changed = now().date(), [], False
    for line in config.QUEUE.read_text(encoding="utf-8").splitlines():
        m = QUEUE_ITEM.match(line)
        if m and (today - date.fromisoformat(m[1])).days > 7:
            line, changed = line.replace("- [ ]", "- [-]", 1) + f" · expired {today}", True
        lines.append(line)
    if changed:
        config.QUEUE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def send(c: Candidate, text: str, buttons: list[tuple[str, str]], moment: datetime, message_id: str) -> bool:
    """Sends, records it in Messages and, if it came from the queue, marks the item as sent."""
    tg_id = telegram_api.send(text, buttons)
    if tg_id is None:
        return False
    record(c.kind, c.source, text, tg_id, message_id)
    if c.queue_line:
        queue = config.QUEUE.read_text(encoding="utf-8")
        sent = c.queue_line.replace("- [ ]", "- [x]", 1) + f" · sent {moment:%Y-%m-%d}"
        config.QUEUE.write_text(queue.replace(c.queue_line, sent, 1), encoding="utf-8")
    return True


def _remind(moment: datetime) -> str | None:
    c = next_reminder(moment, messages())
    if not c:
        return None
    message_id = uuid.uuid4().hex[:6]
    buttons = [(i18n.t("button.done"), f"msg|{message_id}|done"), (i18n.t("button.later"), f"msg|{message_id}|later")]
    return f"reminder about {c.source}" if send(c, _reminder_text(c, moment.date()), buttons, moment, message_id) else None


def _deliver(moment: datetime) -> str | None:
    """What Seahorse promised, such as the summary of a research: the message, with the note attached."""
    ready = from_queue(moment, deliveries=True)
    if not ready:
        return None
    c, message_id = ready[0], uuid.uuid4().hex[:6]
    if not send(c, c.text, conversation_buttons(message_id, []), moment, message_id):
        return None
    note = next(config.VAULT.rglob(f"{notes.links(c.source)[0]}.md"), None)
    if note:
        telegram_api.send_document(note)
    return f"delivery about {c.source}"


def run() -> str:
    """The timer calls this every ten minutes. At most one message per run."""
    from . import companion  # imported here: the Companion uses this module
    with vault.lock():
        vault.pull()
        mark_ignored()
        _expire_queue()
        moment = now()
        tasks.sync(moment.date())
        week.reschedule(moment.date())
        done = None
        if not quiet(moment):
            done = _remind(moment) or _deliver(moment) or companion.talk(moment)
        vault.publish(f"messenger: {done or 'records updated'}")
        return done or "silence"
