"""The Messenger: the code that decides what goes out on Telegram, and when. Rules in `6. ⚙️ System/_MESSENGER.md`.

It runs every ten minutes and sends at most one message per run: first a deadline reminder, then something
Seahorse promised (a delivery), then, at a drawn moment, a conversation of the Companion.
It never sends during the quiet hours, and it keeps a daily limit for what the user did not ask for.
A model only writes the extra line of a reminder and the Companion's text. Code decides the rest.
"""
import glob
import html
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode

from . import config, i18n, llm, notes, tasks, telegram_api, vault, week

log = logging.getLogger(__name__)

# The messages about the people of the user: code chooses when (people.py), and a model writes them. They are
# conversations: a reply links to them, they count toward the daily limit, and one with no answer is ignored.
PEOPLE = {"birthday", "briefing", "reconnect"}
# The kinds of conversation the Companion starts. "proposal" comes from the other agents, through the queue.
CONVERSATION = {"revival", "question", "recommendation", "curiosity", "research", "connection", "proposal",
                "checkin", "release", "news", "unblock"} | PEOPLE
SPONTANEOUS = CONVERSATION | {"reminder", "delivery"}
# What the user did not ask for. It counts toward the daily limit. Deliveries, the summary and the week do not, and
# neither do the `now` messages (now.py): the user asked for them.
PROACTIVE = CONVERSATION | {"reminder"}
DAILY_LIMIT = 5  # the default; `config.DAILY_MESSAGES` changes it
LINE = re.compile(r"^- (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) · (\S+) · id:(\w+) · tg:(\d*) · (.*?) · reaction: (.+?)(?: · (.*))?$")
# The queue date may carry a time: the item waits for that moment.
QUEUE_ITEM = re.compile(r"^- \[ \] (\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}))? · (\S+) · (high|normal) · (.+) · (\[\[[^\]]+\]\])\s*$")
IGNORE = "ign"
USEFUL = "useful"  # the "Thanks" button of a delivery
REMIND_DAYS_BEFORE = 7
ONE_HOUR = timedelta(hours=1)
# The morning summary lists every deadline of the next seven days: a reminder in the hours after it only repeats it.
# Before the summary comes, reminders wait for it, but not more than an hour after its time, in case it fails.
SUMMARY_COVERS, SUMMARY_WAIT = timedelta(hours=4), timedelta(hours=1)
# The kinds of reminder. Code writes the kind at the start of the text of each reminder in Messages.
STAGES = ("week-before", "day-before", "due-today")
# A kind of reminder that the user ignored this many times in a row stops, until the user acts on a reminder again.
# A reminder of something due today always goes out.
STOP_AFTER, CALIBRATED, ENGAGED = 3, ("week-before", "day-before"), {"acted", "replied"}
STOPPED = "## Stopped reminders"
STOPPED_LINE = re.compile(r"^- (week-before|day-before)\b.*?(\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?)")
SNOOZED_LINE = re.compile(r"^- .*?\[\[([^\]]+)\]\].*?(\d{4}-\d{2}-\d{2})")
# "Open in Obsidian": Telegram buttons take only web links, so a small page (docs/open.html) opens the obsidian:// link.
OPEN_PAGE = "https://lorenzoa7.github.io/seahorse/open.html"
NOTE_LIMIT = 9000  # characters of a delivered note: more would be many messages, and the note is in Obsidian
SHORT_NOTE = 700  # a longer note goes folded, in a quote that opens with a tap


@dataclass
class Message:
    when: datetime
    kind: str
    id: str
    tg: str
    source: str
    reaction: str
    text: str = ""


@dataclass
class Candidate:
    kind: str
    source: str
    text: str | None
    urgent: bool
    priority: str
    task: tasks.Task | None = None
    queue_line: str | None = None
    stage: str = ""  # the kind of a reminder


def now() -> datetime:
    return datetime.now(config.TIMEZONE)


def _moment(text: str) -> datetime:
    """A "2026-10-21 14:30" or "2026-10-21" of the vault files, in the user's time zone."""
    shape = "%Y-%m-%d %H:%M" if " " in text.strip() else "%Y-%m-%d"
    return datetime.strptime(text.strip(), shape).replace(tzinfo=config.TIMEZONE)


def quiet(moment: datetime) -> bool:
    """Inside the quiet hours. They may cross midnight, such as 22:00 to 07:00."""
    minute = moment.hour * 60 + moment.minute
    start, end = config.QUIET_FROM, config.QUIET_UNTIL
    return start <= minute < end if start <= end else minute >= start or minute < end


# Reactions. A button press must never wait for the vault lock, which an agent can hold for minutes: the bot leaves
# the reaction in a small file, and the next writer of Messages, which holds the lock, applies it.

def _waiting_folder() -> Path:
    return config.STATE / "reactions"


def _waiting() -> list[tuple[Path, str, str]]:
    """The reactions that wait to go into Messages, oldest first: (file, message id, reaction)."""
    folder = _waiting_folder()
    found = []
    for path in sorted(folder.glob("*.json")) if folder.exists() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            found.append((path, str(data["id"]), str(data["reaction"])))
        except (OSError, ValueError, KeyError):
            continue
    return found


def messages() -> list[Message]:
    """Every message in Messages, with the reactions that still wait in their files."""
    if not config.MESSAGES.exists():
        return []
    found = []
    for line in config.MESSAGES.read_text(encoding="utf-8").splitlines():
        m = LINE.match(line)
        if m:
            found.append(Message(_moment(m[1]), m[2], m[3], m[4], m[5], m[6].strip(), (m[7] or "").strip()))
    waiting = {message_id: reaction for _, message_id, reaction in _waiting()}
    for message in found:
        message.reaction = waiting.get(message.id, message.reaction)
    return found


def by_telegram(tg_id: int) -> Message | None:
    return next((m for m in messages() if m.tg == str(tg_id)), None)


def record(kind: str, source: str, text: str, tg_id: int | None, message_id: str, tag: str = "") -> None:
    """One line in Messages. `tag` goes before the text, such as the kind of a reminder."""
    reaction = "pending" if kind in SPONTANEOUS else "—"
    summary = " ".join(text.split())[:90]
    summary = f"{tag} · {summary}" if tag else summary
    line = f"- {now():%Y-%m-%d %H:%M} · {kind} · id:{message_id} · tg:{tg_id or ''} · {source} · reaction: {reaction} · {summary}"
    config.MESSAGES.parent.mkdir(parents=True, exist_ok=True)
    with config.MESSAGES.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _rewrite_reactions(choose) -> int:
    """Rewrites the reaction of each line for which `choose(match)` gives a new one."""
    if not config.MESSAGES.exists():
        return 0
    lines, changed = config.MESSAGES.read_text(encoding="utf-8").splitlines(), 0
    for i, line in enumerate(lines):
        m = LINE.match(line)
        new = choose(m) if m else None
        if new and new != m[6].strip():
            lines[i] = line.replace(f"reaction: {m[6].strip()}", f"reaction: {new}", 1)
            changed += 1
    if changed:
        config.MESSAGES.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed


def _set_reaction(match, new: str) -> int:
    return _rewrite_reactions(lambda m: new if match(m) else None)


def apply_reactions() -> int:
    """Writes the reactions that wait in their files into Messages. The caller holds the vault lock."""
    waiting = _waiting()
    if not waiting:
        return 0
    latest = {message_id: reaction for _, message_id, reaction in waiting}
    changed = _rewrite_reactions(lambda m: latest.get(m[3]))
    for path, _, _ in waiting:
        path.unlink(missing_ok=True)
    return changed


def record_reactions(engrams: list[Path]) -> None:
    """Engrams that answer a message mark the reaction on it. The Integrator calls this, with the lock."""
    apply_reactions()  # first the button presses that came before these answers
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
    """For the bot: records the reaction to a message at once, without the vault lock. It waits in a small file
    until the next run of the Messenger, the Integrator or the morning summary writes it into Messages."""
    folder = _waiting_folder()
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{time.time_ns()}-{message_id}"
    (folder / f"{name}.tmp").write_text(json.dumps({"id": message_id, "reaction": reaction}), encoding="utf-8")
    os.replace(folder / f"{name}.tmp", folder / f"{name}.json")  # whole, or not there: a reader never sees half


def mark_ignored() -> None:
    apply_reactions()
    limit = now() - timedelta(hours=24)

    def old_and_unanswered(m) -> bool:
        return m[6].strip() == "pending" and m[2] in SPONTANEOUS and _moment(m[1]) < limit

    if _set_reaction(old_and_unanswered, "ignored"):
        log.info("old messages marked as ignored")


# Calibration: what the user muted or snoozed, and the kinds of reminder that code stopped.

def _calibration() -> str:
    return config.CALIBRATION.read_text(encoding="utf-8") if config.CALIBRATION.exists() else ""


def _section(text: str, heading: str) -> str:
    """The text of one `## ` section of a note, without its heading. Empty when the note has no such section."""
    return text.split(heading, 1)[1].split("\n## ", 1)[0] if heading in text else ""


def muted() -> set[str]:
    section = _section(_calibration(), "## Muted topics")
    items = set(notes.links(section))
    items |= {line.strip()[2:].strip() for line in section.splitlines() if line.strip().startswith("- ") and "[[" not in line}
    return {i for i in items if i and i.lower() not in {"none", "none."}}


def snoozed(today: date) -> set[str]:
    """The notes snoozed until a date that has not come yet: `- [[note]] until 2026-03-01`."""
    found = set()
    for line in _section(_calibration(), "## Snoozed").splitlines():
        m = SNOOZED_LINE.match(line.strip())
        try:
            if m and date.fromisoformat(m[2]) > today:
                found.add(re.split(r"[|#^]", m[1])[0].strip())
        except ValueError:
            continue
    return found


def _has_words(text: str, topic: str) -> bool:
    """The topic is in the text as whole words: "car" is in "pay the car tax", not in "Cartão"."""
    topic = topic.strip()
    return bool(topic) and re.search(rf"(?<!\w){re.escape(topic)}(?!\w)", text, re.I) is not None


def silenced(links: list[str], text: str, today: date) -> bool:
    """A message about these notes, with this text, touches a muted or snoozed topic: one of its note links is the
    topic, or the topic is in its text as whole words."""
    topics = muted() | snoozed(today)
    names = {link.lower() for link in links}
    return any(t.lower() in names or _has_words(text, t) for t in topics)


def reminder_stage(m: Message) -> str | None:
    """The kind of a reminder in Messages: week-before, day-before or due-today. Code writes it at the start of the
    text. A reminder sent before Seahorse 1.3.0 has none: its kind comes from the words of its header."""
    if m.kind != "reminder":
        return None
    first = m.text.split(" · ", 1)[0].strip()
    if first in STAGES:
        return first

    def due(when: str) -> bool:
        return i18n.t("reminder.due", when=when, date="") in m.text

    if due(i18n.t("reminder.today")):
        return "due-today"
    if due(i18n.t("reminder.tomorrow")):
        return "day-before"
    return "week-before" if any(due(day) for day in i18n.words("days_long")) else None


def stopped_reminders() -> dict[str, datetime]:
    """The kinds of reminder that code stopped, with the moment it did, from Calibration."""
    found = {}
    for line in _section(_calibration(), STOPPED).splitlines():
        m = STOPPED_LINE.match(line.strip())
        if m:
            found[m[1]] = _moment(m[2])
    return found


def stopped_intro() -> str:
    """The line under the heading of the stopped reminders. The template has the same one."""
    return (f"Code writes this part. A kind of reminder stops when the last {STOP_AFTER} of that kind were ignored, "
            f"and starts again when {config.USER_NAME} acts on a reminder. To start it again now, delete its line.")


def _write_stopped(stopped: dict[str, datetime]) -> None:
    """Rewrites the lines of the stopped reminders in Calibration. The rest of the note stays as it is."""
    text = _calibration().rstrip("\n")
    if STOPPED not in text:
        text = (f"{text}\n" if text else "") + f"{STOPPED}\n{stopped_intro()}\nNone."
    before, rest = text.split(STOPPED, 1)
    body, heading, after = rest.partition("\n## ")
    kept = [line for line in body.split("\n") if line.strip() and not STOPPED_LINE.match(line.strip())
            and line.strip().lower() not in {"none", "none."}]
    items = [f"- {stage} · since {when:%Y-%m-%d %H:%M} · the last {STOP_AFTER} were ignored" for stage, when in stopped.items()]
    text = before + STOPPED + "\n" + "\n".join(kept + (items or ["None."])) + (heading + after if heading else "")
    config.CALIBRATION.parent.mkdir(parents=True, exist_ok=True)
    config.CALIBRATION.write_text(text + "\n", encoding="utf-8")


def _calibration_state() -> dict:
    try:
        return json.loads((config.STATE / "reminders.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def calibrate_reminders(moment: datetime) -> list[str]:
    """Stops a kind of reminder after the user ignored the last 3 of it, and starts it again when the user acts on a
    reminder. Only reminders after the last action of the user, and after the last stop, count: so a stop that the
    user deleted in Calibration does not come back at once. The caller holds the vault lock."""
    reminders = [m for m in messages() if m.kind == "reminder"]
    stopped, state, changes = stopped_reminders(), _calibration_state(), []
    engaged = max((m.when for m in reminders if m.reaction in ENGAGED), default=None)
    for stage in CALIBRATED:
        if stage in stopped:
            if engaged and engaged >= stopped[stage]:
                del stopped[stage]
                changes.append(f"{stage} reminders start again")
            continue
        after = max([t for t in (engaged, _moment(state[stage]) if stage in state else None) if t], default=None)
        last = [m for m in reminders if reminder_stage(m) == stage and (after is None or m.when > after)][-STOP_AFTER:]
        if len(last) == STOP_AFTER and all(m.reaction == "ignored" for m in last):
            stopped[stage] = moment.replace(second=0, microsecond=0)
            state[stage] = f"{moment:%Y-%m-%d %H:%M}"
            changes.append(f"{stage} reminders stop: the last {STOP_AFTER} were ignored")
    if changes:
        _write_stopped(stopped)
        config.STATE.mkdir(parents=True, exist_ok=True)
        (config.STATE / "reminders.json").write_text(json.dumps(state), encoding="utf-8")
        log.info("calibration: %s", "; ".join(changes))
    return changes


def daily_limit() -> int:
    return int(getattr(config, "DAILY_MESSAGES", DAILY_LIMIT))


def over_limit(moment: datetime, history: list[Message]) -> bool:
    """Today already had the most messages that the user did not ask for: conversations and reminders."""
    sent = sum(1 for m in history if m.kind in PROACTIVE and m.when.date() == moment.date())
    return sent >= daily_limit()


def summary_covers(moment: datetime, history: list[Message]) -> bool:
    """The morning summary of today says what a reminder would say: in the hours after it, and before it comes."""
    sent = [m.when for m in history if m.kind == "summary" and m.when.date() == moment.date()]
    if sent:
        return moment - max(sent) < SUMMARY_COVERS
    hours, minutes = map(int, config.SCHEDULE["morning"].split(":"))
    return moment < moment.replace(hour=hours, minute=minutes, second=0, microsecond=0) + SUMMARY_WAIT


def conversation_buttons(message_id: str, labels: list[str]) -> list[tuple[str, str]]:
    """The buttons of a Companion message: the ones it proposed, and always the ignore button."""
    buttons = [(label, f"msg|{message_id}|b{i}") for i, label in enumerate(labels[:4])]
    return buttons + [(i18n.t("button.ignore"), f"msg|{message_id}|{IGNORE}")]


def delivery_buttons(message_id: str) -> list[tuple[str, str]]:
    """Under a delivery: "Thanks" counts as acted, "Not useful" as ignored."""
    return [(i18n.t("button.useful"), f"msg|{message_id}|{USEFUL}"), (i18n.t("button.not_useful"), f"msg|{message_id}|{IGNORE}")]


def _task_links(task: tasks.Task) -> list[str]:
    """The task itself and the notes its frontmatter links, such as its subject."""
    return [task.name] + notes.links(" ".join(str(value) for value in task.fm.values()))


def _stage(days_left: int) -> str:
    return "due-today" if days_left <= 0 else "day-before" if days_left == 1 else "week-before"


def _reminders(today: date, history: list[Message]) -> list[Candidate]:
    """Each task with a due date gets at most two reminders: a week before, and the day before (or on the day)."""
    found = []
    for task in tasks.with_due_date():
        source = f"[[{task.name}]]"
        if silenced(_task_links(task), task.name, today):
            continue
        left = (task.due - today).days
        earlier = [m for m in history if m.kind == "reminder" and m.source == source
                   and m.when.date() >= task.due - timedelta(days=REMIND_DAYS_BEFORE + 1)]
        if left < 0 or any(m.when.date() == today for m in earlier):
            continue
        if left <= 1 and len(earlier) < 2:
            found.append(Candidate("reminder", source, None, True, "high", task, stage=_stage(left)))
        elif left <= REMIND_DAYS_BEFORE and not earlier:
            found.append(Candidate("reminder", source, None, False, "normal", task, stage=_stage(left)))
    return found


def next_reminder(moment: datetime, history: list[Message]) -> Candidate | None:
    """The deadline reminder to send now, if any. At most one per hour, so they never come in a burst. None while
    the morning summary covers the deadlines, none of a kind that code stopped, and within the daily limit, except
    a reminder of something due today."""
    if quiet(moment) or summary_covers(moment, history) or any(m.kind == "reminder" and moment - m.when < ONE_HOUR for m in history):
        return None
    stopped, full = stopped_reminders(), over_limit(moment, history)
    found = [c for c in _reminders(moment.date(), history)
             if c.stage not in stopped and (c.stage == "due-today" or not full)]
    found.sort(key=lambda c: not c.urgent)
    return found[0] if found else None


def from_queue(moment: datetime, deliveries: bool) -> list[Candidate]:
    """Queue items that may go out now. `deliveries` picks what was promised, or everything else. An item about a
    muted or snoozed topic waits; a delivery, which the user asked for, is checked only by its note."""
    found = []
    if not config.QUEUE.exists():
        return found
    for line in config.QUEUE.read_text(encoding="utf-8").splitlines():
        m = QUEUE_ITEM.match(line)
        if not m or (m[3] == "delivery") != deliveries or silenced(notes.links(m[6]), "" if deliveries else m[5], moment.date()):
            continue
        start = datetime.strptime(f"{m[1]} {m[2] or '00:00'}", "%Y-%m-%d %H:%M").replace(tzinfo=config.TIMEZONE)
        if start > moment or (moment.date() - start.date()).days > 7:
            continue
        found.append(Candidate(m[3], m[6], m[5].strip(), deliveries, m[4], queue_line=line))
    found.sort(key=lambda c: c.priority != "high")
    return found


def reminder_header(task: tasks.Task, today: date) -> str:
    """The two fixed lines of a reminder: the task, and when it is due with the time it takes. The date is in the
    order of the language: 10/08 in English, 08/10 in Portuguese and Spanish."""
    left = (task.due - today).days
    when = i18n.t("reminder.today") if left == 0 else i18n.t("reminder.tomorrow") if left == 1 else i18n.words("days_long")[task.due.weekday()]
    estimate = str(task.fm.get("estimate", "")).strip()
    day = task.due.strftime(i18n.t("reminder.date_format"))
    line = i18n.t("reminder.due", when=when, date=day) + (f" · {estimate}" if estimate else "")
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


def send(c: Candidate, text: str, buttons: list[tuple[str, str]], moment: datetime, message_id: str,
         html_text: str | None = None, links: list[tuple[str, str]] | None = None) -> bool:
    """Sends, records it in Messages and, if it came from the queue, marks the item as sent. With `html_text`, that
    goes as Telegram HTML, with `links` as link buttons; `text` is still what Messages records."""
    tg_id = telegram_api.send_html(html_text, buttons, links) if html_text else telegram_api.send(text, buttons)
    if tg_id is None:
        return False
    record(c.kind, c.source, text, tg_id, message_id, c.stage)
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


# Deliveries: what the user asked for, such as a research, to read in the chat itself.

def find_note(name: str) -> Path | None:
    """The vault note with this name, or at this path in the vault. Hidden folders, such as the trash, do not count."""
    direct = config.VAULT / f"{name}.md"
    if direct.is_file():
        return direct
    found = [p for p in config.VAULT.rglob(f"{glob.escape(Path(name).name)}.md")
             if not any(part.startswith(".") for part in p.relative_to(config.VAULT).parts)]
    return min(found, key=lambda p: len(p.parts)) if found else None


def location(note: Path) -> str:
    """Where a note is, in plain words: "Knowledge › Misc › Torterra cards"."""
    parts = note.relative_to(config.VAULT).with_suffix("").parts
    folders = [re.sub(r"^[\d.\s]*[^\w]*", "", part).strip() or part for part in parts[:-1]]
    return " › ".join(folders + [parts[-1]])


def obsidian_link(note: Path) -> str:
    """The web link of the "Open in Obsidian" button. The page opens obsidian://open with the same vault and file."""
    name = getattr(config, "OBSIDIAN_VAULT", None) or config.VAULT.name
    file = note.relative_to(config.VAULT).with_suffix("").as_posix()
    return f"{OPEN_PAGE}?{urlencode({'vault': name, 'file': file}, quote_via=quote)}"


def delivery_html(summary: str, note: Path | None) -> str:
    """A delivery to read in the chat: the summary, the note itself without its frontmatter, and where it is saved.
    A long note goes folded; a very long one stops at a line, and the rest stays in Obsidian."""
    parts = [telegram_api.note_html(summary)]
    if note:
        body = telegram_api.note_html(note.read_text(encoding="utf-8"))
        if len(body) > NOTE_LIMIT:
            cut = body.rfind("\n", 0, NOTE_LIMIT)
            body = body[:cut if cut > 0 else NOTE_LIMIT].rstrip() + "\n…"
        if body:
            parts.append(f"<blockquote expandable>{body}</blockquote>" if len(body) > SHORT_NOTE else body)
        parts.append(html.escape(i18n.t("delivery.saved_in", path=location(note)), quote=False))
    return "\n\n".join(part for part in parts if part)


def _deliver(moment: datetime) -> str | None:
    """What Seahorse promised, such as the summary of a research: the message and the note as text, with a button
    that opens the note in Obsidian. No file to download."""
    ready = from_queue(moment, deliveries=True)
    if not ready:
        return None
    c, message_id = ready[0], uuid.uuid4().hex[:6]
    note = find_note(notes.links(c.source)[0])
    links = [(i18n.t("button.open_obsidian"), obsidian_link(note))] if note else []
    if not send(c, c.text, delivery_buttons(message_id), moment, message_id, delivery_html(c.text, note), links):
        return None
    return f"delivery about {c.source}"


def run() -> str:
    """The timer calls this every ten minutes. At most one message per run. The model of the Companion, or of a
    message about a person, runs after the lock is free again, so the captures and the buttons do not wait for it."""
    from . import companion, people  # imported here: they use this module
    ask = None
    with vault.lock():
        vault.pull()
        mark_ignored()
        _expire_queue()
        moment = now()
        tasks.sync(moment.date())
        people.update(moment.date())  # the buttons pressed under messages about people, and the calls done
        week.reschedule(moment.date())
        calibrate_reminders(moment)
        done = None
        if not quiet(moment):
            done = _remind(moment) or _deliver(moment)
            if not done:
                ask = people.prepare(moment)  # a birthday, an encounter of today or a friend to call
            if not done and not ask:
                done, ask = companion.prepare(moment)
        vault.publish(f"messenger: {done or 'records updated'}")
    if ask:
        done = (people.converse if isinstance(ask, people.Ask) else companion.converse)(ask)
    return done or "silence"
