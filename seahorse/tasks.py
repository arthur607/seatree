"""Tasks: one note per task in `1. 🎯 Intention/Tasks/`, shown as a table in `Tasks.base`.

The week lists the tasks of each day as links, `> - [ ] [[Review the budget]] (2h)`. Checking one side checks
the other: `sync` does it, without a model, after every agent and on every Messenger tick. It remembers the
last state it saw of each note and of the lines of each week, so it copies only what really changed, in both
directions, and an uncheck stays unchecked.
"""
import calendar
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config, i18n, notes

log = logging.getLogger(__name__)

WEEK_LINE = re.compile(r"^([\s>]*[-*+] \[)([ xX])(\] \[\[([^\]|#]+)[^\]]*\]\].*)$")
REPEATS = {"weekly", "monthly", "yearly"}
# An uncheck can take back the renewal of a repeating task for this many days.
UNDO_DAYS = 7


@dataclass
class Task:
    path: Path
    fm: dict

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def done(self) -> bool:
        return str(self.fm.get("done", "")).lower() == "true"

    @property
    def due(self) -> date | None:
        return _date(self.fm.get("due"))

    @property
    def planned(self) -> date | None:
        """The day the user chose to do it. It is not a deadline."""
        return _date(self.fm.get("planned"))

    @property
    def repeat(self) -> str:
        return str(self.fm.get("repeat", "")).strip()

    @property
    def repeats(self) -> bool:
        return self.repeat in REPEATS

    def count(self, field: str) -> int:
        """A number field such as `moved` or `postponed`. Zero when it is empty."""
        return int(re.sub(r"\D", "", str(self.fm.get(field, ""))) or 0)


def _date(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def all_tasks() -> list[Task]:
    if not config.TASKS.exists():
        return []
    found = []
    for path in sorted(config.TASKS.rglob("*.md")):
        fm, _ = notes.read(path)
        if fm.get("type") == "task":
            found.append(Task(path, fm))
    return found


def with_due_date() -> list[Task]:
    """Open tasks with a due date, the nearest first."""
    return sorted((t for t in all_tasks() if not t.done and t.due), key=lambda t: t.due)


def mark_done(name: str, today: date | None = None) -> bool:
    """Marks an open task done in code, as a check in its note does: `sync` then checks its open lines in the
    week, or opens a task that repeats again with its next date. False when there is no such open task."""
    task = next((t for t in all_tasks() if t.name == name), None)
    if not task or task.done:
        return False
    _set(task, "done", "true")
    sync(today)
    return True


def tags(path: Path) -> list[str]:
    """The tags of a note: `tags: [a, b]`, `tags: a`, or a YAML list under `tags:`, indented or not."""
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    end = raw.find("\n---", 4)
    if not raw.startswith("---\n") or end == -1:
        return []
    lines = raw[4:end].split("\n")
    for i, line in enumerate(lines):
        if not re.match(r"tags\s*:", line):
            continue
        value = line.split(":", 1)[1].strip()
        items = value.strip("\"'").strip("[]").split(",") if value else []
        for following in [] if value else lines[i + 1:]:
            item = re.match(r"\s*-\s*(.*)$", following)
            if not item:
                break
            items.append(item[1])
        return [t.strip().strip("\"'").lstrip("#").strip() for t in items if t.strip().strip("\"'")]
    return []


def work_tasks() -> set[str]:
    """The names of the tasks with the work tag (or a tag under it, such as `work/meeting`). They go into the
    week but do not count toward its hours."""
    tag = config.WORK_TAG.lower()
    return {t.name for t in all_tasks() if any(x.lower() == tag or x.lower().startswith(f"{tag}/") for x in tags(t.path))}


def next_date(due: date, repeat: str, day: int | None = None) -> date | None:
    """The next date of a task that repeats. `day` is the day of the month to keep, such as 31: a monthly task
    of the 31st is due on 28 February, and on 31 March again."""
    if repeat == "weekly":
        return due + timedelta(days=7)
    if repeat in {"monthly", "yearly"}:
        year, month = (due.year + 1, due.month) if repeat == "yearly" else (due.year + due.month // 12, due.month % 12 + 1)
        return date(year, month, min(day or due.day, calendar.monthrange(year, month)[1]))
    return None


# ---- the memory of the last sync -------------------------------------------------------------------------

def _state_path() -> Path:
    return config.STATE / "tasks.json"


def _load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _week_start(path: Path) -> date | None:
    m = re.fullmatch(r"(\d{4})-W(\d{2})", path.stem)
    try:
        return date.fromisocalendar(int(m[1]), int(m[2]), 1) if m else None
    except ValueError:
        return None


def _set(task: Task, key: str, value: str) -> None:
    """Writes one frontmatter field, or removes it when the value is empty."""
    if value:
        notes.set_field(task.path, key, value)
        task.fm[key] = value
        return
    if key not in task.fm:
        return
    text = task.path.read_text(encoding="utf-8")
    end = text.find("\n---", 4)
    if text.startswith("---\n") and end != -1:
        lines = [line for line in text[4:end].splitlines() if not line.startswith(f"{key}:")]
        task.path.write_text("---\n" + "\n".join(lines) + text[end:], encoding="utf-8")
    task.fm.pop(key, None)


# ---- repeating tasks --------------------------------------------------------------------------------------

def _renew(task: Task, today: date, state: dict, source: str | None) -> None:
    """A task that repeats: one occurrence is done. It opens again with its next date after today, its
    counters start again, and its history gets one line. An uncheck of the same week line can take it back."""
    old = task.due or today  # a task that repeats with no due date starts today
    anchors = state.setdefault("anchors", {})
    day = anchors.get(task.name)
    if not day or task.due is None or task.due.day != min(day, calendar.monthrange(old.year, old.month)[1]):
        day = old.day  # a new task, or the user changed the date: keep its day of the month
    anchors[task.name] = day
    new = next_date(old, task.repeat, day)
    while new <= today:
        new = next_date(new, task.repeat, day)
    before = {key: str(task.fm.get(key, "")) for key in ("due", "last_done", "moved", "postponed", "planned")}
    line = f"- {today} · " + i18n.t("task.renewed", old=f"{old:%Y-%m-%d}", new=f"{new:%Y-%m-%d}")
    for key, value in (("due", new.isoformat()), ("last_done", today.isoformat()), ("done", "false")):
        _set(task, key, value)
    for key in ("moved", "postponed"):
        if key in task.fm:
            _set(task, key, "0")
    _set(task, "planned", "")
    text = task.path.read_text(encoding="utf-8").rstrip("\n")
    if "\n## History" not in text:
        text += "\n## History"
    task.path.write_text(text + "\n" + line + "\n", encoding="utf-8")
    state.setdefault("undo", {})[task.name] = {"file": source, "at": today.isoformat(), "fields": before, "line": line}


def _undo_renewal(task: Task, source: str, today: date, state: dict) -> bool:
    """Takes back the last renewal when the user unchecks the line that made it, within a week."""
    undo = state.get("undo", {}).get(task.name)
    if not undo or undo["file"] != source or (today - date.fromisoformat(undo["at"])).days > UNDO_DAYS:
        return False
    for key, value in undo["fields"].items():
        _set(task, key, value)
    text = task.path.read_text(encoding="utf-8")
    at = text.rfind(undo["line"] + "\n")
    if at != -1:
        text = text[:at] + text[at + len(undo["line"]) + 1:]
        if text.rstrip("\n").endswith("\n## History"):
            text = text.rstrip("\n").removesuffix("\n## History") + "\n"
        task.path.write_text(text, encoding="utf-8")
    del state["undo"][task.name]
    return True


def _already_counted(task: Task, monday: date | None) -> bool:
    """A checked line of a repeating task that sync never saw: an occurrence done since that week started was
    counted already, by a Seahorse from before this memory."""
    last = _date(task.fm.get("last_done"))
    return bool(last and monday and last >= monday)


# ---- the sync ---------------------------------------------------------------------------------------------

class _Weeks:
    """The week files, read once. Changes stay in memory until `save`."""

    def __init__(self):
        files = sorted(config.WEEKS.glob("*.md")) if config.WEEKS.exists() else []
        self.lines = {p: p.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n") for p in files}
        self.changed: set[Path] = set()

    def files(self, start: date | None = None, end: date | None = None) -> list[Path]:
        """The week files whose Monday is from `start` to `end`, the oldest first."""
        found = []
        for path in self.lines:
            monday = _week_start(path)
            if monday and (not start or monday >= start) and (not end or monday <= end):
                found.append(path)
        return found

    def find(self, path: Path, name: str, checked: bool) -> list[int]:
        found = []
        for i, line in enumerate(self.lines[path]):
            m = WEEK_LINE.match(line)
            if m and m[4].strip() == name and (m[2] != " ") == checked:
                found.append(i)
        return found

    def mark(self, path: Path, index: int, checked: bool) -> None:
        m = WEEK_LINE.match(self.lines[path][index])
        self.lines[path][index] = f"{m[1]}{'x' if checked else ' '}{m[3]}"
        self.changed.add(path)

    def counts(self, path: Path) -> dict[str, list[int]]:
        """For each task: [checked lines, all lines] in the file."""
        found: dict[str, list[int]] = {}
        for line in self.lines[path]:
            m = WEEK_LINE.match(line)
            if m:
                pair = found.setdefault(m[4].strip(), [0, 0])
                pair[0] += m[2] != " "
                pair[1] += 1
        return found

    def save(self) -> None:
        for path in self.changed:
            path.write_bytes("\n".join(self.lines[path]).encode("utf-8"))


def _line_checked(task: Task, path: Path, weeks: _Weeks, today: date, state: dict) -> int:
    """The user checked a line. A repeating task: one occurrence is done. Another task is done when no other
    line of it is open in that week (a task can have sessions on several days), and then its open lines in
    the weeks after are checked too."""
    if task.repeats:
        _renew(task, today, state, path.stem)
        return 1
    if task.done or weeks.find(path, task.name, checked=False):
        return 0
    _set(task, "done", "true")
    monday = _week_start(path)
    for later in weeks.files(start=monday + timedelta(days=7)) if monday else []:
        for index in weeks.find(later, task.name, checked=False):
            weeks.mark(later, index, True)
    return 1


def _line_unchecked(task: Task, path: Path, today: date, state: dict) -> int:
    """The user unchecked a line: the task is open again. A repeating task takes back its last renewal."""
    if task.repeats:
        return int(_undo_renewal(task, path.stem, today, state))
    if task.done:
        _set(task, "done", "false")
        return 1
    return 0


def _note_done(task: Task, weeks: _Weeks, today: date, state: dict) -> int:
    """The task became done in its note (in the table, or by an agent). A repeating task opens again with its
    next date, and only the line of the occurrence that was done is checked: in the week of its old due date
    (this week, if that date passed), or the nearest week before it. Another task: its open lines from this
    week on are checked."""
    if task.repeats:
        old = task.due or today
        _renew(task, today, state, None)
        for path in reversed(weeks.files(end=max(old, today))):
            found = weeks.find(path, task.name, checked=False)
            if found:
                weeks.mark(path, found[0], True)
                break
        return 1
    changes = 0
    for path in weeks.files(start=today - timedelta(days=today.weekday())):
        for index in weeks.find(path, task.name, checked=False):
            weeks.mark(path, index, True)
            changes += 1
    return changes


def _note_reopened(task: Task, weeks: _Weeks, today: date) -> int:
    """The task became open again in its note: its checked lines from this week on are open again. The lines
    of older weeks are history and stay as they are."""
    changes = 0
    for path in weeks.files(start=today - timedelta(days=today.weekday())):
        for index in weeks.find(path, task.name, checked=True):
            weeks.mark(path, index, False)
            changes += 1
    return changes


def sync(today: date | None = None) -> int:
    """Links the checkboxes of the week to the task notes. Returns how many changes it made.

    It compares each side with what it saw the last time, so it acts only on real changes:
    - a line checked in the week: `_line_checked`; a line unchecked: `_line_unchecked`;
    - a task done in its note: `_note_done`; a task open again in its note: `_note_reopened`.
    A line it never saw is a new check only in this week and the week before. Older weeks are only remembered.
    """
    today = today or datetime.now(config.TIMEZONE).date()
    state = _load_state()
    by_name = {t.name: t for t in all_tasks()}
    weeks = _Weeks()
    seen_lines, seen_notes = state.get("lines", {}), state.get("notes", {})
    recent = today - timedelta(days=today.weekday() + 7)
    changes, handled = 0, set()
    for path in list(weeks.lines):
        monday = _week_start(path)
        for name, (checked, total) in weeks.counts(path).items():
            task = by_name.get(name)
            if not task:
                continue
            before = seen_lines.get(f"{path.stem}|{name}")
            if before is None:
                if not monday or monday < recent or (task.repeats and _already_counted(task, monday)):
                    continue
                before = [0, total]
            done = 0
            for _ in range(checked - before[0]):
                done += _line_checked(task, path, weeks, today, state)
            if checked < before[0]:
                done += _line_unchecked(task, path, today, state)
            if done:
                changes += done
                handled.add(name)
    for task in by_name.values():
        if task.name in handled:
            continue  # a line already said what happened to it
        was_done = seen_notes.get(task.name, {}).get("done", False)
        if task.done and (task.repeats or not was_done):
            changes += _note_done(task, weeks, today, state)
        elif not task.done and was_done:
            changes += _note_reopened(task, weeks, today)
    weeks.save()
    state["lines"] = {f"{p.stem}|{name}": pair for p in weeks.lines for name, pair in weeks.counts(p).items()}
    state["notes"] = {t.name: {"done": t.done} for t in by_name.values()}
    state["undo"] = {k: v for k, v in state.get("undo", {}).items() if k in by_name}
    state["anchors"] = {k: v for k, v in state.get("anchors", {}).items() if k in by_name}
    _save_state(state)
    if changes:
        log.info("tasks synced: %s change(s)", changes)
    return changes
