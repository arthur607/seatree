"""Tasks: one note per task in `1. 🎯 Intention/Tasks/`, shown as a table in `Tasks.base`.

The week lists the tasks of each day as links, `> - [ ] [[Review the budget]] (2h)`. Checking one side checks
the other: `sync` does it, without a model, after every agent and on every Messenger tick.
"""
import calendar
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config, i18n, notes

log = logging.getLogger(__name__)

WEEK_LINE = re.compile(r"^([\s>]*- \[)([ xX])(\] \[\[([^\]|#]+)[^\]]*\]\].*)$")


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
        try:
            return date.fromisoformat(str(self.fm.get("due", ""))[:10])
        except ValueError:
            return None

    @property
    def repeat(self) -> str:
        return str(self.fm.get("repeat", "")).strip()

    def count(self, field: str) -> int:
        """A number field such as `moved` or `postponed`. Zero when it is empty."""
        return int(re.sub(r"\D", "", str(self.fm.get(field, ""))) or 0)


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


def work_tasks() -> set[str]:
    """The names of the tasks with the work tag. They go into the week but do not count toward its hours."""
    names = set()
    for task in all_tasks():
        raw = task.path.read_text(encoding="utf-8")
        frontmatter = raw[:raw.find("\n---", 4)]
        tags = re.search(r"^tags:(.*?)(?=^\S|\Z)", frontmatter, re.S | re.M)
        if tags and re.search(rf"\b{re.escape(config.WORK_TAG)}\b", tags[1], re.I):
            names.add(task.name)
    return names


def next_date(due: date, repeat: str) -> date | None:
    if repeat == "weekly":
        return due + timedelta(days=7)
    if repeat in {"monthly", "yearly"}:
        year, month = (due.year + 1, due.month) if repeat == "yearly" else (due.year + due.month // 12, due.month % 12 + 1)
        return date(year, month, min(due.day, calendar.monthrange(year, month)[1]))
    return None


def _week_end(week_file: Path) -> date | None:
    m = re.fullmatch(r"(\d{4})-W(\d{2})", week_file.stem)
    return date.fromisocalendar(int(m[1]), int(m[2]), 7) if m else None


def _already_counted(task: Task, end: date | None) -> bool:
    try:
        last = date.fromisoformat(str(task.fm.get("last_done", ""))[:10])
    except ValueError:
        return False
    return end is None or last >= end - timedelta(days=6)


def _mark_done(task: Task, today: date) -> None:
    notes.set_field(task.path, "done", "true")
    task.fm["done"] = "true"
    new = next_date(task.due, task.repeat) if task.due else None
    if new:
        _renew(task, new, today)


def _renew(task: Task, new: date, today: date) -> None:
    """A task that repeats: once done, it opens again with the next date, and its history gets one line."""
    notes.set_field(task.path, "due", new.isoformat())
    notes.set_field(task.path, "last_done", today.isoformat())
    notes.set_field(task.path, "done", "false")
    text = task.path.read_text(encoding="utf-8").rstrip("\n")
    line = f"- {today} · " + i18n.t("task.renewed", old=f"{task.due:%Y-%m-%d}", new=f"{new:%Y-%m-%d}")
    if "\n## History" not in text:
        text += "\n## History"
    task.path.write_text(text + "\n" + line + "\n", encoding="utf-8")
    task.fm.update(done="false", due=new.isoformat(), last_done=today.isoformat())


def sync(today: date | None = None) -> int:
    """Links the checkboxes of the week to the task notes. Returns how many changes it made.

    - A line checked in the week, an open task: the task becomes done. If the task repeats and was already done
      since the start of that week, the line is of an old occurrence, and nothing changes.
    - A done task, an open line in the week: the line becomes checked.
    - A task that repeats and was checked in the table: it opens again with the next date.
    """
    today = today or datetime.now(config.TIMEZONE).date()
    by_name = {t.name: t for t in all_tasks()}
    changes, renewed = 0, {}
    for task in by_name.values():
        if task.done and task.repeat and task.due:
            new = next_date(task.due, task.repeat)
            if new:
                renewed[task.name] = task.due
                _renew(task, new, today)
                changes += 1
    for week_file in sorted(config.WEEKS.glob("*.md")) if config.WEEKS.exists() else []:
        end = _week_end(week_file)
        lines = week_file.read_text(encoding="utf-8").split("\n")
        changed = False
        for i, line in enumerate(lines):
            m = WEEK_LINE.match(line)
            if not m or m[4].strip() not in by_name:
                continue
            task = by_name[m[4].strip()]
            checked = m[2] != " "
            if checked and not task.done and not _already_counted(task, end):
                _mark_done(task, today)
                changes += 1
            elif not checked and (task.done or (task.name in renewed and (not end or renewed[task.name] <= end))):
                lines[i] = f"{m[1]}x{m[3]}"
                changed = True
                changes += 1
        if changed:
            week_file.write_text("\n".join(lines), encoding="utf-8")
    if changes:
        log.info("tasks synced: %s change(s)", changes)
    return changes
