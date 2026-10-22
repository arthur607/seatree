"""The week: reads the day cards of `1. 🎯 Intention/Week/<year>-W<nn>.md` and moves what was left behind.

Each card is the day of the date in its title (dd/mm), or of its day name, or of its place in the file. The
titles can be in English, Portuguese or Spanish, folded (`[!day]-`) and with text after the totals. Code writes
the titles again, with the totals, in the user's language.

A task with no due date that was not done on its day is never "late": it moves to the next day with room.
With no room until Sunday, it leaves the week and stays an open task, for the Planner to take next week.
Everything here is code, without a model.
"""
import json
import logging
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path

from . import config, i18n, notes, tasks, vault

log = logging.getLogger(__name__)

TITLE = re.compile(r"^>\s*\[!day\]([+-]?)(.*)$", re.I)
ITEM = re.compile(r"^>\s*[-*+] \[([ xX])\] (.*)$")
DATE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})(?!\d)")
GROUP = re.compile(r"\(([^()]*)\)")
HOURS = r"h|hr|hrs|hour|hours|hora|horas"
MINUTES = r"m|min|mins|minute|minutes|minuto|minutos"
DURATION = re.compile(rf"(\d+(?:[.,]\d+)?)\s*(?:{HOURS})\s*(?:(\d+)\s*(?:{MINUTES})?)?|(\d+)\s*(?:{MINUTES})", re.I)
REVIEW = ["> [!review]- Review", "> - Planned:", "> - Done:", "> - Estimated vs actual:", "> - Postponed:"]
# Tasks that left a week this many times are not brought into the next week by code: the Planner asks about them.
POSTPONED_LIMIT = 3


def minutes(text: str) -> int:
    """"2h", "1h30", "1h 30m", "1.5h", "90 min" or "~15m?" in minutes. Zero when it is not a duration."""
    m = DURATION.fullmatch(text.strip().lstrip("~≈").rstrip("?").strip())
    if not m:
        return 0
    return round(float(m[1].replace(",", ".")) * 60) + int(m[2] or 0) if m[1] else int(m[3])


def duration(total: int) -> str:
    hours, rest = divmod(total, 60)
    if not hours:
        return f"{rest}m" if rest else "0h"
    return f"{hours}h{rest:02d}" if rest else f"{hours}h"


def _estimate(text: str) -> re.Match | None:
    """The last group in parentheses that is a duration, such as `(1h 30m)` in `[[Task]] (1h 30m) ⚠️ due Thu`."""
    found = [m for m in GROUP.finditer(text) if minutes(m[1])]
    return found[-1] if found else None


@dataclass
class Item:
    line: str
    done: bool
    text: str
    fallback: int = 0  # the `estimate` of the task note, when the line has none

    @property
    def name(self) -> str:
        """The task note's name if the line is a link. Otherwise the text without the estimate."""
        found = notes.links(self.text)
        if found:
            return found[0]
        m = _estimate(self.text)
        return " ".join((self.text[:m.start()] + self.text[m.end():]).split()) if m else self.text.strip()

    @property
    def minutes(self) -> int:
        m = _estimate(self.text)
        return minutes(m[1]) if m else self.fallback


@dataclass
class Day:
    label: str
    day_month: str
    capacity: int
    date: date
    lines: list[str] = field(default_factory=list)
    fold: str = ""
    extra: str = ""  # text after the totals in the title, kept as it is
    estimates: dict = field(default_factory=dict, repr=False)

    @property
    def items(self) -> list[Item]:
        found = []
        for line in self.lines:
            m = ITEM.match(line)
            if m:
                item = Item(line, m[1] != " ", m[2])
                item.fallback = self.estimates.get(item.name, 0)
                found.append(item)
        return found

    def total(self, work: set[str]) -> int:
        return sum(i.minutes for i in self.items if i.name not in work)

    def title(self, work: set[str]) -> str:
        """The title in the user's language, such as `> [!day] Seg 05/10 · 1h de 4h`. The date stays dd/mm."""
        label = i18n.words("days_short")[self.date.weekday()].capitalize()
        title = (f"> [!day]{self.fold} {label} {self.date:%d/%m} · {duration(self.total(work))} "
                 f"{i18n.t('week.of')} {duration(self.capacity)}")
        return f"{title} {self.extra}" if self.extra else title

    def add(self, line: str) -> None:
        """Adds a task line after the last line of the card that is inside the callout."""
        inside = [i for i, text in enumerate(self.lines) if text.startswith(">")]
        self.lines.insert(inside[-1] + 1 if inside else 0, line)


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

    def day(self, when: date) -> Day | None:
        return next((d for d in self.days if d.date == when), None)


# ---- reading --------------------------------------------------------------------------------------------

def _plain(word: str) -> str:
    """A word to compare day names: lowercase, without accents, without "-feira" and the final dot."""
    word = unicodedata.normalize("NFKD", word.lower()).encode("ascii", "ignore").decode()
    return word.strip(".,:;").removesuffix("-feira")


@cache
def _day_names() -> dict[str, int]:
    """Every day name of every language, short and long, to its weekday: "qua" → 2."""
    found = {"tues": 1, "thur": 3, "thurs": 3}
    for language in i18n.LANGUAGES:
        for key in ("days_short", "days_long"):
            for number, name in enumerate(i18n.words(key, language)):
                found[_plain(name)] = number
    return found


@cache
def _totals() -> re.Pattern:
    """`1h of 4h`, `30m de 4h` or `1h/4h`, with the connector words of every language."""
    words = sorted({i18n.t("week.of", language) for language in i18n.LANGUAGES}, key=len, reverse=True)
    connector = "|".join(re.escape(w) for w in words)
    return re.compile(rf"(\d[^\s/]*)\s*(?:(?<=\s)(?:{connector})(?=\s)|/)\s*(\d[\dhHmM.,]*)", re.I)


@dataclass
class _Card:
    fold: str
    rest: str
    body: list[str]
    number: int  # line number in the file

    def parts(self, monday: date) -> tuple[date | None, date | None, bool]:
        """The day of its date and the day of its name, in this week. And if the title has a date at all."""
        dates = [monday + timedelta(days=i) for i in range(7)]
        m = DATE.search(self.rest)
        by_date = None
        if m:
            a, b = int(m[1]), int(m[2])
            by_date = next((d for d in dates if (d.day, d.month) == (a, b)), None) \
                or next((d for d in dates if (d.month, d.day) == (a, b)), None)  # 10/05 for an English user
        words = re.findall(r"[^\s\d·/|:,()\[\]]+", self.rest[:m.start()] if m else self.rest)
        number = _day_names().get(_plain(words[0])) if words else None
        return by_date, (dates[number] if number is not None else None), bool(m)

    def _name_end(self) -> int | None:
        """Where the day name of a title with no date ends, such as after "Tuesday". None with no day name."""
        m = re.search(r"[^\s\d·/|:,()\[\]]+", self.rest)
        return m.end() if m and _plain(m[0]) in _day_names() else None

    def _totals(self) -> tuple[re.Match | None, int]:
        """The totals, and where the text after the date (or the day name) starts."""
        found = DATE.search(self.rest)
        if found:
            return _totals().search(self.rest, found.end()), found.end()
        name = self._name_end()
        return _totals().search(self.rest), len(self.rest) if name is None else name

    def capacity(self) -> int:
        m, _ = self._totals()
        return minutes(m[2]) if m else 0

    def extra(self) -> str:
        """The text after the totals, such as a note the user wrote in the title. With no totals, the text after the
        date, or after the day name: "Tuesday - dentist day" keeps "dentist day"."""
        m, start = self._totals()
        if m:
            return self.rest[m.end():].strip()
        tail = self.rest[start:].strip().strip("·-–—:").strip()
        return "" if minutes(tail) or tail == "0h" else tail


def _tidy(lines: list[str]) -> list[str]:
    """No blank line at the start or the end of a card, and never two in a row."""
    out: list[str] = []
    for line in lines:
        if not line.strip():
            if out and out[-1].strip():
                out.append("")
            continue
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return out


def _split(text: str) -> tuple[list[str], list[_Card], list[str]]:
    """The text before the first card, the cards, and the part after them: the first callout that is not a
    day, or a heading. Free text between cards stays in the card above it."""
    before, cards, after = [], [], []
    for number, line in enumerate(text.split("\n"), 1):
        title = TITLE.match(line)
        if title:
            if after and cards:  # a card after other text: that text stays with the card before it
                cards[-1].body += after
                after = []
            cards.append(_Card(title[1], title[2].strip(), [], number))
        elif not cards:
            before.append(line)
        elif after or line.startswith(("> [!", "#")):
            after.append(line)
        else:
            cards[-1].body.append(line)
    return before, cards, after


def _assign(cards: list[_Card], monday: date) -> tuple[list[date | None], dict[int, date]]:
    """The date of each card, and the cards that repeat a day and join the card of that day.

    1. A date that agrees with the day name, or with no day name. 2. A date with another day name: the date if
    it is free, else the day name. 3. Only a day name. 4. Nothing readable: the next free day in the file."""
    dates = [monday + timedelta(days=i) for i in range(7)]
    parts = [c.parts(monday) for c in cards]
    owner: list[date | None] = [None] * len(cards)
    joins: dict[int, date] = {}
    taken: set[date] = set()

    def take(i: int, day: date | None) -> bool:
        if day is None or day in taken:
            return False
        taken.add(day)
        owner[i] = day
        return True

    for i, (by_date, by_label, _) in enumerate(parts):
        if by_date and by_label in (None, by_date) and not take(i, by_date):
            joins[i] = by_date
    for i, (by_date, by_label, _) in enumerate(parts):
        if by_date and by_label and by_label != by_date and not take(i, by_date) and not take(i, by_label):
            joins[i] = by_date
    for i, (by_date, by_label, _) in enumerate(parts):
        if not by_date and by_label and not take(i, by_label):
            joins[i] = by_label
    for i, (by_date, by_label, _) in enumerate(parts):
        if by_date or by_label:
            continue
        previous = next((owner[j] for j in range(i - 1, -1, -1) if owner[j]), monday - timedelta(days=1))
        free = [d for d in dates if d > previous and d not in taken] or [d for d in dates if d not in taken]
        if not (free and take(i, free[0])):
            joins[i] = max(previous, monday)
    return owner, joins


def _capacity(before: list[str]) -> int:
    for line in before:
        if line.startswith("daily_capacity:"):
            found = minutes(line.split(":", 1)[1].strip().strip("\"'"))
            if found:
                return found
    return minutes(config.DAILY_CAPACITY) or 240


def _estimates() -> dict[str, int]:
    return {t.name: minutes(str(t.fm.get("estimate", ""))) for t in tasks.all_tasks()}


def parse(text: str, path: Path, monday: date, estimates: dict | None = None) -> Week | None:
    """A week from the text of its file. None when it has no day card. A day with no card gets an empty one,
    so the week always has its seven days, in order."""
    before, cards, after = _split(text.replace("\r\n", "\n"))
    if not cards:
        return None
    estimates = _estimates() if estimates is None else estimates
    default = _capacity(before)
    owner, joins = _assign(cards, monday)
    days: dict[date, Day] = {}
    for card, when in zip(cards, owner):
        if when:
            days[when] = Day(card.rest.split(" ")[0] if card.rest else "", f"{when:%d/%m}", card.capacity() or default,
                             when, _tidy(card.body), card.fold, card.extra(), estimates)
    for i, when in joins.items():
        days[when].lines = _tidy(days[when].lines + cards[i].body)
    for i in range(7):
        when = monday + timedelta(days=i)
        days.setdefault(when, Day(i18n.words("days_short")[i].capitalize(), f"{when:%d/%m}", default, when,
                                  estimates=estimates))
    return Week(path, before, [days[d] for d in sorted(days)], after)


def path_for(day: date) -> Path:
    return config.WEEKS / f"{vault.week_name(day)}.md"


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def read(day: date) -> Week | None:
    """The week that holds the day, or None when it has no file or no day card."""
    path = path_for(day)
    if not path.exists():
        return None
    return parse(path.read_text(encoding="utf-8"), path, _monday(day))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def problems(path: Path) -> list[str]:
    """What is wrong with the day cards of a week file, for the lint and the Planner check. Empty when each of
    the seven days has one card with a readable date or day name."""
    m = re.fullmatch(r"(\d{4})-W(\d{2})", path.stem)
    try:
        monday = date.fromisocalendar(int(m[1]), int(m[2]), 1) if m else None
    except ValueError:
        monday = None
    if not monday:
        return ["the file name is not YYYY-Www, such as 2026-W41"]
    if not path.exists():
        return ["the file does not exist"]
    _, cards, _ = _split(path.read_text(encoding="utf-8").replace("\r\n", "\n"))
    if not cards:
        return ["no day card, such as `> [!day] Mon 05/10 · 0h of 4h`"]
    found, claims = [], Counter()
    for card in cards:
        by_date, by_label, has_date = card.parts(monday)
        if has_date and not by_date:
            found.append(f"line {card.number}: the date is not in this week")
        if not (by_date or by_label):
            found.append(f"line {card.number}: the card has no date or day name of this week")
        claims[by_date or by_label] += 1
    for i in range(7):
        day = monday + timedelta(days=i)
        label = f"{('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')[i]} {day:%d/%m}"
        if not claims[day]:
            found.append(f"no card for {label}")
        elif claims[day] > 1:
            found.append(f"{claims[day]} cards for {label}")
    return found


def create_if_missing(day: date) -> Path:
    """Writes the week that holds the day, with empty cards, if it does not exist yet."""
    path = path_for(day)
    if path.exists():
        return path
    monday = _monday(day)
    capacity = minutes(config.DAILY_CAPACITY) or 240
    days = [Day("", "", capacity, monday + timedelta(days=i)) for i in range(7)]
    before = ["---", "type: week", f"week: {vault.week_name(day)}", f"daily_capacity: {config.DAILY_CAPACITY}", "---"]
    _write(path, Week(path, before, days, [*REVIEW, ""]).text(set()))
    return path


def refresh(today: date | None = None) -> bool:
    """Writes again the titles of the week that holds the day, with the totals computed in code, and puts the
    cards in order. It changes the file only when something changed. True if it did."""
    w = read(today or datetime.now(config.TIMEZONE).date())
    if not w:
        return False
    new = w.text(tasks.work_tasks())
    if new == w.path.read_text(encoding="utf-8").replace("\r\n", "\n"):
        return False
    _write(w.path, new)
    return True


# ---- moves ----------------------------------------------------------------------------------------------

@dataclass
class Move:
    name: str
    old: date
    new: date | None
    reason: str = ""  # why it left the week: "no room" or "due later"

    def __str__(self) -> str:
        days = i18n.words("days_short")
        target = days[self.new.weekday()] if self.new else f"out of the week, {self.reason or 'no room'}"
        return f"{self.name}: {days[self.old.weekday()]} → {target}"


ENGLISH_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def describe(move: Move, today: date) -> str:
    """One line in plain English for the morning prompt, such as `Book the doctor: Tuesday 06/10 → today`."""
    def day(when: date) -> str:
        return "today" if when == today else f"{ENGLISH_DAYS[when.weekday()]} {when:%d/%m}"
    if move.new:
        return f"{move.name}: {day(move.old)} → {day(move.new)}"
    why = {"due later": "its due date is later", "postponed": f"postponed {POSTPONED_LIMIT} times, the Sunday plan asks about it"}
    return f"{move.name}: {day(move.old)} → out of the week ({why.get(move.reason, 'no room in the week')}); it waits in the open tasks"


def _state(name: str) -> Path:
    return config.STATE / name


def _load(name: str, empty):
    try:
        return json.loads(_state(name).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return empty


def _save(name: str, data) -> None:
    _state(name).parent.mkdir(parents=True, exist_ok=True)
    _state(name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def record_moves(moves: list[Move], when: datetime | None = None) -> None:
    """Keeps the moves for the morning summary. `take_moves` reads them."""
    if not moves:
        return
    when = when or datetime.now(config.TIMEZONE)
    saved = _load("moves.json", [])
    saved += [{"name": m.name, "old": m.old.isoformat(), "new": m.new.isoformat() if m.new else None,
               "reason": m.reason, "at": when.isoformat(timespec="minutes")} for m in moves]
    _save("moves.json", saved)


def take_moves(today: date | None = None) -> list[Move]:
    """The moves recorded since the last call, and it forgets them. A task that moved more than once is one
    move, from its first day to its last. Moves older than a week are left out."""
    today = today or datetime.now(config.TIMEZONE).date()
    saved = _load("moves.json", [])
    _save("moves.json", [])
    by_name: dict[str, Move] = {}
    for m in saved:
        if (today - date.fromisoformat(m["at"][:10])).days > 7:
            continue
        new = date.fromisoformat(m["new"]) if m["new"] else None
        first = by_name.get(m["name"])
        by_name[m["name"]] = Move(m["name"], first.old if first else date.fromisoformat(m["old"]), new, m["reason"])
    return [m for m in by_name.values() if m.new != m.old]


def _committed(note: tasks.Task | None, end: date) -> bool:
    """A task the user committed to: a day they chose (`planned`) or a due date until the end of this week.
    Only these count a `moved` when their day passes: three such moves make a task "stuck", and the Companion
    asks what is in the way. Other tasks just flow to the next day with room."""
    return bool(note and (note.planned or (note.due and note.due <= end)))


def _target(item: Item, note: tasks.Task | None, ahead: list[Day], work: set[str], end: date) -> tuple[Day | None, str]:
    """Where an open task of a past day goes, and if none, why.

    - A due date until the end of this week: the first day with room up to the due date. With no room, or a
      due date that passed, it stays on today, even over the limit.
    - Otherwise: the first day with room, from today to Sunday. With no room it leaves the week."""
    def fits(day: Day) -> bool:
        return item.name in work or day.total(work) + item.minutes <= day.capacity
    due = note.due if note else None
    if due and due <= end:
        return next((d for d in ahead if d.date <= due and fits(d)), ahead[0]), ""
    target = next((d for d in ahead if fits(d)), None)
    return target, "" if target else ("due later" if due else "no room")


def _move(item: Item, old: Day, note: tasks.Task | None, ahead: list[Day], work: set[str], end: date,
          other_week: bool) -> Move | None:
    """Moves one open line of a past day, and counts it in the task note."""
    old.lines.remove(item.line)
    if other_week and any(i.name == item.name and not i.done for d in ahead for i in d.items):
        target, reason = None, "in this week already"  # the Planner already put it in this week
    elif other_week and note and not note.due and note.count("postponed") + 1 >= POSTPONED_LIMIT:
        target, reason = None, "postponed"  # postponed again and again: the Planner asks about it
    else:
        target, reason = _target(item, note, ahead, work, end)
    if note is None and target is None and reason != "in this week already" and ahead:
        target, reason = ahead[-1], ""  # a line with no task note has no other home: it stays in the week, never lost
    if target:
        target.add(item.line)
    if note and target and _committed(note, end):
        notes.set_field(note.path, "moved", str(note.count("moved") + 1))
    if note and not note.due and (other_week or not target):
        notes.set_field(note.path, "postponed", str(note.count("postponed") + 1))  # it left a week unfinished
    if reason == "in this week already":
        return None
    return Move(item.name, old.date, target.date if target else None, reason)


def _place_planned(week: Week, by_name: dict, today: date) -> bool:
    """Puts each open task with `planned: <day>` on that day, once, when the day is today or later and the task
    is not in the week yet. The user chose the day, so it goes in even over the limit."""
    saved = _load("week.json", {}).get("placed", {})
    placed = {name: when for name, when in saved.items() if name in by_name}
    changed = False
    for note in by_name.values():
        day = week.day(note.planned) if note.planned and not note.done else None
        if not day or day.date < today or placed.get(note.name) == day.date.isoformat():
            continue
        placed[note.name] = day.date.isoformat()
        if any(i.name == note.name for d in week.days for i in d.items):
            continue  # already in the week, on that day or on another one the user chose
        estimate = str(note.fm.get("estimate", "")).strip()
        day.add(f"> - [ ] [[{note.name}]]" + (f" ({estimate})" if estimate else ""))
        changed = True
    if placed != saved:
        _save("week.json", {"placed": placed})
    return changed


def reschedule(today: date | None = None) -> list[Move]:
    """Moves forward the open tasks of the days that passed, and records the moves for the morning summary.

    - On the first runs of a new week, the open lines of the week before come into this one too: the Planner
      plans on Sunday evening, before Sunday ends.
    - Where a task goes: see `_target`. A work task always fits.
    - Counters in the task note: `moved` + 1 when a task the user committed to moves to another day (see
      `_committed`); `postponed` + 1 when a task with no due date leaves a week unfinished.
    - A task with `planned: <day>` goes on that day: see `_place_planned`.
    - The titles of the cards are written again when their totals changed, as in `refresh`.
    """
    today = today or datetime.now(config.TIMEZONE).date()
    week = read(today)
    if not week:
        return []
    work = tasks.work_tasks()
    by_name = {t.name: t for t in tasks.all_tasks()}
    ahead = [d for d in week.days if d.date >= today]
    end = week.days[-1].date
    moves, previous_changed = [], False
    previous = read(today - timedelta(days=7))
    for w in (previous, week) if previous else (week,):
        for day in w.days:
            if day.date >= today:
                continue
            for item in [i for i in day.items if not i.done]:
                note = by_name.get(item.name)
                if note and note.done:
                    continue  # checked in the table: `tasks.sync` checks the line, it does not move
                move = _move(item, day, note, ahead, work, end, w is previous)
                previous_changed |= w is previous
                if move:
                    moves.append(move)
                if note:  # the counters changed on disk: read the note again for its next line
                    by_name[note.name] = tasks.Task(note.path, notes.read(note.path)[0])
    _place_planned(week, by_name, today)
    if previous_changed:
        _write(previous.path, previous.text(work))
    new = week.text(work)  # also the titles: their totals stay right even when nothing moved
    if new != week.path.read_text(encoding="utf-8").replace("\r\n", "\n"):
        _write(week.path, new)
    if moves:
        record_moves(moves)
        log.info("week rescheduled: %s", "; ".join(str(m) for m in moves))
    return moves


# ---- git conflicts --------------------------------------------------------------------------------------

def _names(text: str) -> Counter:
    return Counter(Item(line, m[1] != " ", m[2]).name for line in text.split("\n") if (m := ITEM.match(line)))


def merge_conflict(path: Path, union: str, base: str, ours: str, theirs: str) -> str:
    """The text of a week file after a git conflict, made from the union of the lines of both sides.

    Each task keeps as many lines as the side that had more. Of the extra lines, an open line goes before a
    checked one, and a line that was already there in the base goes before a line that moved: so a check on
    the phone wins, and so does a move made by code. The titles are written again."""
    monday = date.fromisocalendar(*map(int, re.fullmatch(r"(\d{4})-W(\d{2})", path.stem).groups()), 1)
    estimates = _estimates()
    w = parse(union, path, monday, estimates)
    if not w:
        return theirs
    old = parse(base, path, monday, estimates) if base else None
    in_base = {(d.date, i.name) for d in old.days for i in d.items} if old else set()
    mine, phone = _names(ours), _names(theirs)
    for name in _names(union):
        found = [(day, item) for day in w.days for item in day.items if item.name == name]
        extra = len(found) - max(mine[name], phone[name], 1)
        order = sorted(range(len(found)), key=lambda k: (found[k][1].done, (found[k][0].date, name) not in in_base,
                                                         found[k][0].date))
        drop = set(order[:max(extra, 0)])
        for k in sorted(drop):
            day, item = found[k]
            at = day.lines.index(item.line)
            # A line that stays, later in the same day, takes the place of the one that goes.
            later = next((found[j][1] for j in range(k + 1, len(found)) if j not in drop and found[j][0] is day), None)
            if later and later.line != item.line:
                day.lines.pop(day.lines.index(later.line, at + 1))
                day.lines[at] = later.line
            else:
                day.lines.pop(at)
    return w.text(tasks.work_tasks())
