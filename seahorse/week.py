"""The week: reads the day cards of `1. 🎯 Intention/Week/<year>-W<nn>.md` and moves what was left behind.

A task with no due date that was not done on its day is never "late": it moves to the next day with room.
With no room until Sunday, it leaves the week and stays an open task, for the Planner to take next week.
Everything here is code, without a model.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config, i18n, notes, tasks, vault

log = logging.getLogger(__name__)

CARD = re.compile(r"^> \[!day\] (\S+) (\d\d/\d\d) · (.*?) of (\S+)\s*$")
ITEM = re.compile(r"^> - \[([ xX])\] (.*)$")
DURATION = re.compile(r"\((\d+h)?(\d+)?m?\??\)\s*$")
CARD_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def minutes(text: str) -> int:
    """"2h", "1h30", "45m" or "15m?" in minutes. Zero when it is not a duration."""
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m?)?\??", text.strip())
    if not m or not (m[1] or m[2]):
        return 0
    return int(m[1] or 0) * 60 + int(m[2] or 0)


def duration(total: int) -> str:
    hours, rest = divmod(total, 60)
    if not hours:
        return f"{rest}m" if rest else "0h"
    return f"{hours}h{rest:02d}" if rest else f"{hours}h"


@dataclass
class Item:
    line: str
    done: bool
    text: str

    @property
    def name(self) -> str:
        """The task note's name if the line is a link. Otherwise the text without the estimate."""
        found = notes.links(self.text)
        return found[0] if found else DURATION.sub("", self.text).strip()

    @property
    def minutes(self) -> int:
        m = DURATION.search(self.text)
        return minutes(m[0].strip()[1:-1]) if m else 0


@dataclass
class Day:
    label: str
    day_month: str
    capacity: int
    date: date
    lines: list[str] = field(default_factory=list)

    @property
    def items(self) -> list[Item]:
        return [Item(line, m[1] != " ", m[2]) for line in self.lines if (m := ITEM.match(line))]

    def total(self, work: set[str]) -> int:
        return sum(i.minutes for i in self.items if i.name not in work)

    def title(self, work: set[str]) -> str:
        return f"> [!day] {self.label} {self.day_month} · {duration(self.total(work))} of {duration(self.capacity)}"


@dataclass
class Week:
    path: Path
    before: list[str]
    days: list[Day]
    after: list[str]

    def text(self, work: set[str]) -> str:
        parts = list(self.before)
        for day in self.days:
            parts += [day.title(work), *day.lines, ""]
        return "\n".join(parts + self.after)


def read(day: date) -> Week | None:
    """The week that holds the day. The cards count by their order: the first one is Monday."""
    path = config.WEEKS / f"{vault.week_name(day)}.md"
    if not path.exists():
        return None
    monday = day - timedelta(days=day.weekday())
    before, days, after = [], [], []
    current = None
    for line in path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n"):
        card = CARD.match(line)
        if card and len(days) < 7:
            current = Day(card[1], card[2], minutes(card[4]) or 240, monday + timedelta(days=len(days)))
            days.append(current)
        elif current is not None and line.startswith(">") and not line.startswith("> [!"):
            current.lines.append(line)
        elif days and not after and not line.strip():
            current = None  # the blank line between two cards: `text` puts it back
        elif not days:
            before.append(line)
        else:
            current = None
            after.append(line)
    return Week(path, before, days, after) if days else None


def create_if_missing(day: date) -> Path:
    """Writes the week that holds the day, with empty cards, if it does not exist yet."""
    path = config.WEEKS / f"{vault.week_name(day)}.md"
    if path.exists():
        return path
    monday = day - timedelta(days=day.weekday())
    capacity = config.DAILY_CAPACITY
    cards = "\n".join(f"> [!day] {CARD_DAYS[i]} {monday + timedelta(days=i):%d/%m} · 0h of {capacity}\n" for i in range(7))
    review = "> [!review]- Review\n> - Planned:\n> - Done:\n> - Estimated vs actual:\n> - Postponed:\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((f"---\ntype: week\nweek: {vault.week_name(day)}\ndaily_capacity: {capacity}\n---\n{cards}\n{review}").encode("utf-8"))
    return path


@dataclass
class Move:
    name: str
    old: date
    new: date | None

    def __str__(self) -> str:
        days = i18n.words("days_short")
        target = days[self.new.weekday()] if self.new else "out of the week, no room"
        return f"{self.name}: {days[self.old.weekday()]} → {target}"


def reschedule(today: date | None = None) -> list[Move]:
    """Moves forward the open tasks of the days that passed.

    - It goes to the first day, from today to Sunday, where it fits the capacity. A work task always fits.
    - With a real due date: it never goes past that day. If the date passed, or there is no room before it,
      it stays on today, even over the limit.
    - With no due date and no room: it leaves the week, and the note gets one more `postponed`.
    Every move adds one to the note's `moved`. At three, the Companion asks what is in the way.
    """
    today = today or datetime.now(config.TIMEZONE).date()
    week = read(today)
    if not week:
        return []
    work = tasks.work_tasks()
    by_name = {t.name: t for t in tasks.all_tasks()}
    ahead = [d for d in week.days if d.date >= today]
    moves = []
    for day in week.days:
        if day.date >= today:
            continue
        for item in [i for i in day.items if not i.done]:
            note = by_name.get(item.name)
            if note and note.done:
                continue  # checked in the table: `tasks.sync` checks the line, it does not move
            due = note.due if note else None
            options = [d for d in ahead if not due or d.date <= due]
            target = next((d for d in options if item.name in work or d.total(work) + item.minutes <= d.capacity), None)
            if target is None and due and ahead:
                target = ahead[0]
            day.lines.remove(item.line)
            if target:
                target.lines.append(item.line)
                if note:
                    notes.set_field(note.path, "moved", str(note.count("moved") + 1))
            elif note:
                notes.set_field(note.path, "postponed", str(note.count("postponed") + 1))
            moves.append(Move(item.name, day.date, target.date if target else None))
    if moves:
        week.path.write_text(week.text(work), encoding="utf-8")
        log.info("week rescheduled: %s", "; ".join(str(m) for m in moves))
    return moves
