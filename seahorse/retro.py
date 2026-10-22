"""Your week: the retrospective of the week that ends, in one image.

Every Sunday, before the plan of the next week, Seahorse sends one image about the week that ends: what the user
did, lived and learned, who came up, what they sent, one pattern it saw, and what it changes because of it.
- Code counts everything: the task lines of the week file (with git, also the ones of the plan that left the week),
  the engrams received, and the notes of Memory and Knowledge that git saw made or changed. Without git, the dates
  in the notes, or the dates of the files.
- One model call writes the pattern and the change, from those numbers. It runs without the vault lock and with a
  short timeout. When it fails, the image goes without these two lines.
- The two lines stay in a state file: `/retro` draws the image again with new numbers and no new model call.
- A short text version goes into the Review of the week file, where the Planner and the Observer read it.
In the quiet hours the image waits, and the bot sends it when they end. It does not count toward the daily limit.
"""
import json
import logging
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from PIL import Image, ImageDraw

from . import config, i18n, llm, messenger, notes, tasks, telegram_api, vault, week, week_image
from .week_image import ACCENT, FAINT, INK, MUTED, PAPER, RULE

log = logging.getLogger(__name__)

MEMORY, KNOWLEDGE = "2. 📓 Memory/", "3. 📚 Knowledge/"
EXPERIENCES, PEOPLE, TRIPS, JOURNAL = (MEMORY + name for name in ("Experiences/", "People/", "Trips/", "Journal/"))
KINDS = ("Movies", "Series", "Books", "Games", "Places")  # the order of the experiences in the image
# The kind of each capture, from the channel of its engram. Any other channel is "other".
CHANNELS = {"telegram-text": "text", "telegram-voice": "voice", "telegram-photo": "photo", "telegram-file": "file",
            "telegram-video": "video", "telegram-location": "place", "telegram-contact": "contact",
            "claude": "claude", "obsidian": "obsidian"}
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git's empty tree: the start of a vault with no history
DATE_FIELDS = ("created", "date", "updated", "start", "end")  # dates of a note, when there is no git
DATED_LINE = re.compile(r"^- (\d{4}-\d{2}-\d{2})\b", re.M)  # such as the appearances of a person
PARTS = (("night", 0), ("morning", 6), ("afternoon", 12), ("evening", 18))
MODEL_SECONDS = 240  # the longest wait for the two lines of the model
LINE_LIMIT = 200  # characters of each line of the model, at most
KEEP_WEEKS = 8  # weeks whose lines the state file keeps
HELD_DAYS = 2  # an image held in the quiet hours that waits longer than this is dropped


@dataclass
class Planned:
    """One task line of the week file."""
    name: str
    day: date
    minutes: int
    done: bool
    work: bool
    moved: int = 0
    postponed: int = 0


@dataclass
class Stats:
    """What the week had, counted by code. The two lines of the model come at the end."""
    monday: date
    lines: list[Planned] = field(default_factory=list)
    captures: list[tuple[datetime, str]] = field(default_factory=list)  # (received, kind)
    experiences: list[tuple[str, str]] = field(default_factory=list)  # (folder of the kind, name)
    journal: list[date] = field(default_factory=list)
    trips: list[str] = field(default_factory=list)
    learned: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    pattern: str = ""
    adjustment: str = ""

    @property
    def sunday(self) -> date:
        return self.monday + timedelta(days=6)

    @property
    def name(self) -> str:
        return vault.week_name(self.monday)

    @property
    def done(self) -> int:
        return sum(line.done for line in self.lines)

    @property
    def total(self) -> int:
        return len(self.lines)

    @property
    def minutes_done(self) -> int:
        """Outside work, as in the week image."""
        return sum(line.minutes for line in self.lines if line.done and not line.work)

    @property
    def minutes_planned(self) -> int:
        return sum(line.minutes for line in self.lines if not line.work)

    @property
    def empty(self) -> bool:
        """No task, nothing lived and nothing learned: nothing to show."""
        return not (self.lines or self.experiences or self.trips or self.learned)

    def top(self, count: int = 3) -> list[tuple[str, int]]:
        """The biggest tasks done, by their time. The sessions of one task on several days add up."""
        found: dict[str, int] = {}
        for line in self.lines:
            if line.done:
                found[line.name] = found.get(line.name, 0) + line.minutes
        return sorted(found.items(), key=lambda pair: -pair[1])[:count]

    def kinds(self) -> list[tuple[str, int]]:
        """The captures by kind, the most first."""
        return Counter(kind for _, kind in self.captures).most_common()


def now() -> datetime:
    return datetime.now(config.TIMEZONE)


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


# ---- counting ---------------------------------------------------------------------------------------------

def compute(monday: date, today: date | None = None) -> Stats:
    """The numbers of the week that starts on `monday`. An empty week, a missing week file or a vault without git
    give empty parts, never an error."""
    today = today or now().date()
    over = today > monday + timedelta(days=6)
    stats = Stats(monday, _task_lines(monday, over), _captures(monday))
    changes = _changes(monday, over)
    if changes is None:
        _from_files(stats)
    else:
        _from_git(stats, changes)
    stats.journal = _journal(monday)
    return stats


def _has_git() -> bool:
    try:
        return (config.VAULT / ".git").exists() and \
            vault.git("rev-parse", "-q", "--verify", "HEAD", check=False).returncode == 0
    except OSError:  # no git on this machine
        return False


def _commit_before(day: date) -> str:
    """The last commit before the day starts, in the user's time zone. Empty when there is none."""
    moment = datetime.combine(day, time(), config.TIMEZONE)
    result = vault.git("rev-list", "-1", f"--before={moment.isoformat()}", "HEAD", check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def _week_text(monday: date, over: bool) -> str | None:
    """The week file as it was when the week ended: from Monday on, code moves its open lines into the new week,
    and git still has them. While the week runs, or with no git, the file as it is now."""
    path = week.path_for(monday)
    if over and _has_git():
        commit = _commit_before(monday + timedelta(days=7))
        old = vault.text_at(commit, vault.rel(path)) if commit else None
        if old is not None:
            return old
    return path.read_text(encoding="utf-8") if path.exists() else None


def _plan_text(monday: date) -> str | None:
    """The week file as it was before its Monday: the plan. None with no git, or when the file came later."""
    if not _has_git():
        return None
    commit = _commit_before(monday)
    return vault.text_at(commit, vault.rel(week.path_for(monday))) if commit else None


def _task_lines(monday: date, over: bool) -> list[Planned]:
    """The task lines of the week file, and the task lines of the plan that left the week (see `_left`)."""
    text = _week_text(monday, over)
    w = week.parse(text, week.path_for(monday), monday) if text else None
    work = tasks.work_tasks()
    by_name = {t.name: t for t in tasks.all_tasks()}
    found = []
    for day in w.days if w else []:
        for item in day.items:
            found.append(_planned(item, day.date, by_name.get(item.name), over, work))
    return found + _left(monday, found, over, by_name, work)


def _planned(item: week.Item, day: date, note: tasks.Task | None, over: bool, work: set[str]) -> Planned:
    # While the week runs, a task checked in its table counts too: `tasks.sync` checks its line soon.
    done = item.done or (not over and bool(note and note.done))
    return Planned(item.name, day, item.minutes, done, item.name in work,
                   note.count("moved") if note else 0, note.count("postponed") if note else 0)


def _left(monday: date, found: list[Planned], over: bool, by_name: dict, work: set[str]) -> list[Planned]:
    """The task lines of the plan that are no longer in the week file: code takes out an open line when no day
    of the week has room for it. They count as planned and not done. A task that only moved inside the week
    is in the week file still, so it counts once. A line with no task note, or of a cancelled task, is left out.
    Without git there is no plan to compare: nothing more."""
    text = _plan_text(monday)
    plan = week.parse(text, week.path_for(monday), monday) if text else None
    still = Counter(line.name for line in found)
    left = []
    for day in plan.days if plan else []:
        for item in day.items:
            note = by_name.get(item.name)
            if still[item.name]:
                still[item.name] -= 1
            elif note and str(note.fm.get("status", "")).strip().lower() != "cancelled":
                left.append(_planned(item, day.date, note, over, work))
    return left


def _received(fm: dict, path: Path) -> datetime | None:
    """When the engram came in: its `received` field, or else its name."""
    for text, shape in ((str(fm.get("received", "")).strip()[:16], "%Y-%m-%d %H:%M"), (path.stem[:15], "%Y-%m-%d %H%M")):
        try:
            return datetime.strptime(text, shape).replace(tzinfo=config.TIMEZONE)
        except ValueError:
            continue
    return None


def _captures(monday: date) -> list[tuple[datetime, str]]:
    """The engrams received in the week, with the kind of each. A tap on a button is an answer, not a capture."""
    if not config.ENGRAMS.exists():
        return []
    sunday = monday + timedelta(days=6)
    first, last = monday.isoformat(), sunday.isoformat()
    found = []
    for path in config.ENGRAMS.rglob("*.md"):
        if path.name.startswith("_") or "(" in path.stem or not first <= path.name[:10] <= last:
            continue
        fm = notes.read(path)[0]
        when = _received(fm, path)
        if fm.get("via_button") or not when or not monday <= when.date() <= sunday:
            continue
        found.append((when, CHANNELS.get(str(fm.get("channel", "")), "other")))
    return sorted(found)


@dataclass
class Change:
    """What happened to one note in the week, from git."""
    status: str  # A: made, M: changed, R: moved or renamed
    old: str  # where an R came from
    grew: int  # lines added minus lines taken out


def _git_z(command: str, *args: str) -> list[str]:
    result = vault.git("-c", "core.quotepath=off", command, "-z", *args, check=False)
    return result.stdout.split("\0") if result.returncode == 0 else []


def _growth(tokens: list[str]) -> dict[str, int]:
    """Lines added minus lines taken out, by path, from `--numstat -z`. A rename gives its two paths after it."""
    found, i = {}, 0
    while i < len(tokens):
        added, removed, path = (tokens[i].split("\t", 2) + ["", ""])[:3]
        i += 1
        if not added:
            continue
        if not path and i + 1 < len(tokens):
            path, i = tokens[i + 1], i + 2
        found[path] = int(added) - int(removed) if added.isdigit() and removed.isdigit() else 0
    return found


def _changes(monday: date, over: bool) -> dict[str, Change] | None:
    """The notes of Memory and Knowledge that changed in the week, by path: git between the last commit before
    Monday and the last one before the next Monday (or now, while the week runs). A note made and then changed in
    the week is one A. None when the vault has no git history."""
    if not _has_git():
        return None
    start = _commit_before(monday) or EMPTY_TREE
    end = _commit_before(monday + timedelta(days=7)) if over else "HEAD"
    if not end:
        return {}
    where = ["-M", start, end, "--", MEMORY.rstrip("/"), KNOWLEDGE.rstrip("/")]
    grew = _growth(_git_z("diff", "--numstat", *where))
    tokens, found, i = _git_z("diff", "--name-status", *where), {}, 0
    while i + 1 < len(tokens):
        status = tokens[i][:1]
        if not status:
            i += 1
            continue
        if status in "RC" and i + 2 < len(tokens):
            old, path, i = tokens[i + 1], tokens[i + 2], i + 3
        else:
            old, path, i = "", tokens[i + 1], i + 2
        if status in "AMRC" and path.endswith(".md"):
            found[path] = Change(status, old, grew.get(path, 0))
    return found


def _copy_names() -> set[str]:
    return {i18n.t("sync.copy_name", language) for language in i18n.LANGUAGES}


def _is_note(path: str) -> bool:
    """A note of the user's life: not a summary (`_People.md`) and not a conflict copy."""
    name = Path(path).name
    return name.endswith(".md") and not name.startswith("_") and not any(f"({c} " in name for c in _copy_names())


def _new_in(change: Change, folder: str) -> bool:
    return change.status == "A" or (change.status in "RC" and not change.old.startswith(folder))


def _kind(path: str) -> str:
    """The kind of an experience: its folder, such as Movies."""
    rest = path[len(EXPERIENCES):]
    return rest.split("/")[0] if "/" in rest else ""


def _order(experiences: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return sorted(experiences, key=lambda pair: (KINDS.index(pair[0]) if pair[0] in KINDS else len(KINDS), pair))


def _from_git(stats: Stats, changes: dict[str, Change]) -> None:
    """People: new, or with new lines, the most new lines first. Learned: notes of knowledge new in Knowledge, from
    what came in this week. A fix of a link or of a field changes lines but adds none: it does not count. What the week lived
    comes from the dates of the notes, not from their changes: see `_lived`."""
    people = []
    for path, change in sorted(changes.items()):
        if not _is_note(path):
            continue
        name = Path(path).stem
        if path.startswith(PEOPLE) and (_new_in(change, PEOPLE) or change.grew > 0):
            people.append((-change.grew, name))
        elif (path.startswith(KNOWLEDGE) and _new_in(change, KNOWLEDGE) and _taught(config.VAULT / path)
              and _from_this_week(config.VAULT / path, stats.monday)):
            stats.learned.append(name)
    _lived(stats)
    stats.people = [name for _, name in sorted(people)]


def _happened(path: Path, monday: date) -> bool:
    """An experience or a trip of the week: the day it happened is in the week. An experience gives that day in
    `when`, a trip in `start` and `end`. A month alone is not a day, and a note with no date never counts: a
    favorite movie seen years ago and filed this week is not something that this week lived."""
    sunday = monday + timedelta(days=6)
    try:
        fm = notes.read(path)[0]
    except (OSError, UnicodeDecodeError):
        return False
    start, end = _day(fm.get("start")), _day(fm.get("end"))
    if start or end:
        start, end = start or end, end or start
        return start <= sunday and end >= monday
    when = _day(fm.get("when"))
    return bool(when) and monday <= when <= sunday


def _day(value) -> date | None:
    """A full date, `YYYY-MM-DD`. A month alone, such as `2026-10`, gives None."""
    text = str(value or "").strip()
    return _date(text) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None


def _lived(stats: Stats) -> None:
    stats.experiences = _order([(_kind(vault.rel(p)), p.stem) for p in _notes_in(EXPERIENCES) if _happened(p, stats.monday)])
    stats.trips = [p.stem for p in _notes_in(TRIPS) if _happened(p, stats.monday)]


def _taught(path: Path) -> bool:
    """A note of knowledge: `type: knowledge`, or no type when the user wrote it. A project is not something
    learned, and neither is the index note of a subject folder, named as its folder, such as `Politics/Politics`."""
    if path.stem == path.parent.name:
        return False
    try:
        kind = notes.read(path)[0].get("type")
    except (OSError, UnicodeDecodeError):
        return False
    return kind in (None, "", "knowledge")


def _from_this_week(path: Path, monday: date) -> bool:
    """A new Knowledge note counts when it came from a capture of this week, or when the user wrote it (no
    `source`). A note that an agent split or moved from older captures does not."""
    try:
        sources = notes.read(path)[0].get("source")
    except (OSError, UnicodeDecodeError):
        return False
    days = [_date(name[:10]) for name in re.findall(r"\[\[([^\]|]+)", str(sources or ""))]
    days = [day for day in days if day]
    return not days or any(monday <= day <= monday + timedelta(days=6) for day in days)


def _date(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def _dated(path: Path, monday: date) -> bool:
    """Without git: a date of the week in the frontmatter, or in a dated line such as an appearance of a person.
    A note with no date at all counts by the date of its file."""
    sunday = monday + timedelta(days=6)
    fm, body = notes.read(path)
    found = [_date(fm.get(key)) for key in DATE_FIELDS] + [_date(day) for day in DATED_LINE.findall(body)]
    found = [day for day in found if day]
    if found:
        return any(monday <= day <= sunday for day in found)
    return monday <= datetime.fromtimestamp(path.stat().st_mtime, config.TIMEZONE).date() <= sunday


def _notes_in(folder: str) -> list[Path]:
    root = config.VAULT / folder
    return sorted(p for p in root.rglob("*.md") if _is_note(p.name)) if root.exists() else []


def _from_files(stats: Stats) -> None:
    """The same parts as `_from_git`, for a vault with no git history."""
    monday = stats.monday
    _lived(stats)
    stats.people = [p.stem for p in _notes_in(PEOPLE) if _dated(p, monday)]
    stats.learned = [p.stem for p in _notes_in(KNOWLEDGE) if _dated(p, monday) and _taught(p)]


def _journal(monday: date) -> list[date]:
    """The days of the week that have a Journal note. The note is named by its day."""
    days = {_date(p.stem) for p in _notes_in(JOURNAL)}
    return sorted(day for day in days if day and monday <= day <= monday + timedelta(days=6))


# ---- the two lines of the model ---------------------------------------------------------------------------

def _part(when: datetime) -> str:
    return next(name for name, start in reversed(PARTS) if when.hour >= start)


def facts(stats: Stats) -> str:
    """The week in plain lines for the model. The names stay as they are."""
    lines = []
    if stats.lines:
        lines.append(f"Tasks: {stats.done} of {stats.total} done. Outside work: {week.duration(stats.minutes_done)} "
                     f"done of {week.duration(stats.minutes_planned)} planned.")
        for line in stats.lines:
            extra = ["work"] if line.work else []
            extra += [f"moved {line.moved} times"] if line.moved else []
            extra += [f"postponed {line.postponed} times"] if line.postponed else []
            lines.append(f"- {line.name} · {week.ENGLISH_DAYS[line.day.weekday()]} · "
                         f"{week.duration(line.minutes) if line.minutes else 'no estimate'} · "
                         f"{'done' if line.done else 'not done'}" + "".join(f" · {e}" for e in extra))
    else:
        lines.append("Tasks: the week had no plan.")
    if stats.captures:
        lines.append(f"Captures: {len(stats.captures)} ({', '.join(f'{n} {kind}' for kind, n in stats.kinds())}).")
        by_day = Counter(when.weekday() for when, _ in stats.captures)
        lines.append("- By day: " + ", ".join(f"{week.ENGLISH_DAYS[i]} {by_day[i]}" for i in range(7)))
        by_part = Counter(_part(when) for when, _ in stats.captures)
        lines.append("- By time of day: " + ", ".join(f"{name} {by_part[name]}" for name, _ in PARTS))
    else:
        lines.append("Captures: none.")
    lived = [f"{name} ({kind.lower() or 'experience'})" for kind, name in stats.experiences]
    lived += [f"{name} (trip)" for name in stats.trips]
    for label, items in (("Lived", lived), ("Learned, new notes", stats.learned), ("People", stats.people)):
        lines.append(f"{label}: {'; '.join(items) if items else 'none'}.")
    days = ", ".join(week.ENGLISH_DAYS[day.weekday()] for day in stats.journal)
    lines.append(f"Journal days: {len(stats.journal)}" + (f" ({days})." if days else "."))
    return "\n".join(lines)


def _line(value) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= LINE_LIMIT else text[:LINE_LIMIT - 1].rstrip() + "…"


def parse(text: str) -> tuple[str, str]:
    """The pattern and the change of the model's JSON answer, each on one short line. A change with no pattern is
    dropped: it would have no reason. Two empty lines when the answer is not usable."""
    found = re.search(r"\{.*\}", text or "", re.S)
    try:
        data = json.loads(found[0]) if found else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        return "", ""
    pattern, adjustment = _line(data.get("pattern")), _line(data.get("adjustment"))
    return (pattern, adjustment) if pattern else ("", "")


def ask(stats: Stats) -> tuple[str, str]:
    """One model call, read-only: one pattern of the week and what Seahorse changes because of it. The caller
    must not hold the vault lock. Two empty lines when the model fails."""
    patterns = config.VAULT / "4. 🪞 Identity" / "Patterns.md"
    known = notes.read(patterns)[1].strip()[:4000] if patterns.exists() else ""
    text = llm.prompt("retro", week_name=stats.name, first=stats.monday.isoformat(), last=stats.sunday.isoformat(),
                      facts=facts(stats), patterns=known or "(nothing yet)",
                      week_file=vault.rel(week.path_for(stats.monday)))
    answer = llm.run(text, "retro", max_turns=8, timeout=MODEL_SECONDS)
    if answer.error:
        log.warning("retro: the pattern line failed: %s", answer.text[:300])
        return "", ""
    return parse(answer.text)


# ---- what Seahorse keeps between runs ---------------------------------------------------------------------

def _state_path() -> Path:
    return config.STATE / "retro.json"


def _held_path() -> Path:
    return config.STATE / "retro.png"


def _load() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def remembered(name: str) -> dict:
    """What the state file keeps of a week: its two lines, and when its image went out."""
    return _load().get("weeks", {}).get(name, {})


def _remember(name: str, **values) -> None:
    data = _load()
    weeks = data.get("weeks", {})
    weeks[name] = {**weeks.get(name, {}), **values}
    data["weeks"] = dict(sorted(weeks.items())[-KEEP_WEEKS:])
    _save(data)


# ---- texts ------------------------------------------------------------------------------------------------

def caption(stats: Stats) -> str:
    return i18n.t("retro.caption", number=stats.monday.isocalendar()[1], first=week_image.day_month(stats.monday),
                  last=week_image.day_month(stats.sunday))


def _kind_word(kind: str) -> str:
    return i18n.t(f"retro.kind.{kind.lower()}") if kind in KINDS else ""


def _days(count: int) -> str:
    return i18n.t("retro.day") if count == 1 else i18n.t("retro.days", count=count)


def _kinds_text(stats: Stats) -> str:
    return ", ".join(counted(kind, n) for kind, n in stats.kinds())


def review_lines(stats: Stats) -> list[str]:
    """The short text version for the Review of the week file. The labels are structure, so they are in English,
    as the other lines of the Review. Empty parts are left out."""
    head = f"{stats.done}/{stats.total}"
    if stats.minutes_planned:
        head += f" · {week.duration(stats.minutes_done)}/{week.duration(stats.minutes_planned)}"
    lived = [f"{name} ({_kind_word(kind)})" if _kind_word(kind) else name for kind, name in stats.experiences]
    lived += [f"{name} ({i18n.t('retro.trip')})" for name in stats.trips]
    if stats.journal:
        lived.append(f"{i18n.t('retro.journal')}: {_days(len(stats.journal))}")
    parts = [("Did", ", ".join(f"{name} ({week.duration(m)})" if m else name for name, m in stats.top())),
             ("Lived", ", ".join(lived)), ("Learned", ", ".join(stats.learned)), ("People", ", ".join(stats.people)),
             ("Captured", f"{len(stats.captures)} ({_kinds_text(stats)})" if stats.captures else ""),
             ("Pattern", stats.pattern), ("Adjustment", stats.adjustment)]
    return [f"> - Retro: {head}"] + [f">   - {label}: {' '.join(text.split())}" for label, text in parts if text]


REVIEW = re.compile(r"^>\s*\[!review\]", re.I)
RETRO = re.compile(r"^>\s*[-*+] Retro:")
NESTED = re.compile(r"^>\s{2,}[-*+] ")


def write_review(stats: Stats) -> bool:
    """Writes the text version into the Review callout of the week file, at its end. Code owns these lines: a
    second run writes them again, and the rest of the file stays as it is. A file with no Review gets one, after a
    blank line. True if the file changed. The caller holds the vault lock."""
    path = week.path_for(stats.monday)
    if not path.exists():
        return False
    old = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    lines, skipping = [], False
    for line in old.split("\n"):
        if RETRO.match(line) or (skipping and NESTED.match(line)):
            skipping = True
            continue
        skipping = False
        lines.append(line)
    block = review_lines(stats)
    starts = [i for i, line in enumerate(lines) if REVIEW.match(line)]
    if starts:
        end = starts[-1] + 1
        while end < len(lines) and lines[end].startswith(">") and not lines[end].startswith("> [!"):
            end += 1
        lines[end:end] = block
    else:
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ["", *week.REVIEW, *block, ""]
    new = "\n".join(lines)
    if new == old:
        return False
    path.write_bytes(new.encode("utf-8"))
    return True


# ---- the image --------------------------------------------------------------------------------------------

WIDTH, MARGIN, LABEL = week_image.WIDTH, 64, 236  # LABEL: the column of the section labels
STEP, GAP = 50, 34  # the height of a line of text, and the space between two sections
MAX_HEIGHT = 3000  # far below Telegram's limit: width and height add up to 10000 at most
FIT = 2800  # about one phone screen at this width: every row shows while the image is this tall or less
LEAST = 3  # the rows that a section keeps when the image must be shorter


class _Fonts:
    """The sizes of the image, loaded when it is drawn."""

    def __init__(self):
        font = week_image.font
        self.title, self.sub, self.big, self.hours = font(56, True), font(30), font(112, True), font(60, True)
        self.unit, self.label, self.text, self.small, self.pattern = font(36), font(30), font(36), font(28), font(40)


def clean(text: str) -> str:
    """No emoji: the typewriter font has none, and they would show as empty boxes."""
    return week_image.clean(text)


def _rows(stats: Stats) -> list[tuple[str, list[tuple[str, str]], int]]:
    """The sections that have something: their label, all their rows (text, note at the right), and how many rows
    are hidden, none yet. What the week was: done, lived and learned. The people, the number of captures and the
    days of the journal stay out of the image: alone, they say little about the week."""
    work = {line.name for line in stats.lines if line.work}

    def time_of(name: str, total: int) -> str:  # a work task shows its time in parentheses, as in the week image
        return "" if not total else f"({week.duration(total)})" if name in work else week.duration(total)

    did = [(name, time_of(name, m)) for name, m in stats.top(len(stats.lines))]
    lived = [(name, _kind_word(kind)) for kind, name in stats.experiences]
    lived += [(name, i18n.t("retro.trip")) for name in stats.trips]
    learned = [(name, "") for name in stats.learned]
    return [(key, rows, 0) for key, rows in (("did", did), ("lived", lived), ("learned", learned)) if rows]


def _row_lines(d: ImageDraw.ImageDraw, f: _Fonts, text: str, note: str) -> list[str]:
    """The lines of one row: its text, wrapped beside its note, in two lines at most."""
    room = WIDTH - 2 * MARGIN - LABEL - (int(d.textlength(note, font=f.small)) + 28 if note else 0)
    return _cut(d, text, f.text, room, 2)


def _fit(d: ImageDraw.ImageDraw, f: _Fonts, sections: list, excess: int) -> list:
    """Hides rows until the image is `excess` pixels shorter: each time, the last row of the longest section. A
    section keeps LEAST rows at least. A section with hidden rows ends with "+N more", which takes one line."""
    rows = {key: list(found) for key, found, _ in sections}
    hidden = dict.fromkeys(rows, 0)
    while excess > 0:
        key = max(rows, key=lambda k: len(rows[k]))
        if len(rows[key]) <= LEAST:
            break
        text, note = rows[key].pop()
        excess -= len(_row_lines(d, f, text, note)) * STEP - (0 if hidden[key] else STEP)
        hidden[key] += 1
    return [(key, rows[key], hidden[key]) for key in rows]


def _cut(d: ImageDraw.ImageDraw, text: str, face, width: int, most: int) -> list[str]:
    """The text without emoji, wrapped to the width, in `most` lines at most. A cut text ends with "…"."""
    pieces = week_image.wrap(d, clean(text), face, width)
    if len(pieces) <= most:
        return pieces
    last = pieces[most - 1].rstrip("-")
    while last and d.textlength(last + "…", font=face) > width:
        last = last[:-1]
    return pieces[:most - 1] + [last.rstrip() + "…"]


def _header(d: ImageDraw.ImageDraw, f: _Fonts, stats: Stats) -> int:
    year, number, _ = stats.monday.isocalendar()
    title = f"{i18n.t('retro.title')} {number}"
    d.text((MARGIN, 108), title, font=f.title, fill=INK, anchor="ls")
    d.text((MARGIN + d.textlength(title, font=f.title) + 28, 106),
           f"{year} · {week_image.day_month(stats.monday)} → {week_image.day_month(stats.sunday)}",
           font=f.sub, fill=FAINT, anchor="ls")
    d.line((MARGIN, 152, WIDTH - MARGIN, 152), fill=RULE, width=2)
    return 196


def _numbers(d: ImageDraw.ImageDraw, f: _Fonts, stats: Stats, y: int) -> int:
    """The big line: tasks done of the planned ones, the hours outside work, and a square for each task. A week
    with no task has no big line: the sections tell it."""
    base = y + 96
    if not stats.lines:
        return y
    big = f"{stats.done}/{stats.total}"
    d.text((MARGIN, base), big, font=f.big, fill=ACCENT, anchor="ls")
    d.text((MARGIN + d.textlength(big, font=f.big) + 22, base), i18n.t("image.done"), font=f.unit, fill=FAINT,
           anchor="ls")
    if stats.minutes_planned:
        planned = f" {i18n.t('week.of')} {week.duration(stats.minutes_planned)}"
        d.text((WIDTH - MARGIN, base), planned, font=f.unit, fill=FAINT, anchor="rs")
        d.text((WIDTH - MARGIN - d.textlength(planned, font=f.unit), base), week.duration(stats.minutes_done),
               font=f.hours, fill=INK, anchor="rs")
    y = base + 30
    week_image.progress(d, MARGIN, y, WIDTH - 2 * MARGIN, min(stats.total, 40), stats.done, stats.total, 24)
    y += 24
    if any(line.work for line in stats.lines) and stats.minutes_planned:
        d.text((WIDTH - MARGIN, y + 40), i18n.t("retro.work"), font=f.small, fill=MUTED, anchor="rs")
        y += 40
    return y + GAP + 30


def _section(d: ImageDraw.ImageDraw, f: _Fonts, key: str, rows: list, hidden: int, y: int) -> int:
    """One section: the label at the left and, at the right, its rows, each with a note at the right edge."""
    x = MARGIN + LABEL
    base = y + 36
    d.text((MARGIN, base), i18n.t(f"retro.{key}"), font=f.label, fill=FAINT, anchor="ls")
    for text, note in rows:
        pieces = _row_lines(d, f, text, note)
        for j, piece in enumerate(pieces):
            d.text((x, base + j * STEP), piece, font=f.text, fill=INK, anchor="ls")
        if note:
            d.text((WIDTH - MARGIN, base), clean(note), font=f.small, fill=FAINT, anchor="rs")
        base += len(pieces) * STEP
    if hidden:
        d.text((x, base), i18n.t("image.more", n=hidden), font=f.small, fill=FAINT, anchor="ls")
        base += STEP
    return base - 36 + GAP


def _pattern(d: ImageDraw.ImageDraw, f: _Fonts, stats: Stats, y: int) -> int:
    """The pattern, beside a thin bar in the accent colour, and the change for next week under it."""
    if not stats.pattern:
        return y
    y += 6
    d.line((MARGIN, y, WIDTH - MARGIN, y), fill=RULE, width=2)
    y += 60
    d.text((MARGIN, y), i18n.t("retro.pattern"), font=f.label, fill=FAINT, anchor="ls")
    y += 60
    x, top = MARGIN + 28, y - 40
    for piece in _cut(d, stats.pattern, f.pattern, WIDTH - MARGIN - x, 5):
        d.text((x, y), piece, font=f.pattern, fill=INK, anchor="ls")
        y += 56
    d.line((MARGIN + 2, top, MARGIN + 2, y - 40), fill=ACCENT, width=5)
    if stats.adjustment:
        y += 20
        arrow = "→ "
        d.text((x, y), arrow, font=f.text, fill=ACCENT, anchor="ls")
        indent = x + int(d.textlength(arrow, font=f.text))
        for piece in _cut(d, stats.adjustment, f.text, WIDTH - MARGIN - indent, 4):
            d.text((indent, y), piece, font=f.text, fill=INK, anchor="ls")
            y += STEP
    return y


def _draw(f: _Fonts, stats: Stats, sections: list) -> tuple[Image.Image, ImageDraw.ImageDraw, int]:
    """Draws the image on a canvas of MAX_HEIGHT. Returns it with the height that its content needs."""
    img = Image.new("RGB", (WIDTH, MAX_HEIGHT), PAPER)
    d = ImageDraw.Draw(img)
    y = _numbers(d, f, stats, _header(d, f, stats))
    for key, rows, hidden in sections:
        y = _section(d, f, key, rows, hidden, y)
    y = _pattern(d, f, stats, y) + 40
    d.text((WIDTH - MARGIN, y + 30), "seahorse", font=f.small, fill=MUTED, anchor="rs")
    return img, d, y + 30 + MARGIN


def draw(stats: Stats) -> Image.Image:
    """The week in one image, in the style of the week image: header, the big line, the sections, the pattern.
    Every row shows while the image is FIT tall or less. A taller week hides rows of its longest sections."""
    f = _Fonts()
    sections = _rows(stats)
    img, d, height = _draw(f, stats, sections)
    if height > FIT:
        img, _, height = _draw(f, stats, _fit(d, f, sections, height - FIT))
    return img.crop((0, 0, WIDTH, min(height, MAX_HEIGHT)))


def png(stats: Stats) -> bytes:
    return week_image.png(draw(stats))


# ---- delivery ---------------------------------------------------------------------------------------------

def _source(name: str) -> str:
    """The link of the week in Messages, or the overview when the week has no file: a link must not break."""
    return f"[[{name}]]" if (config.WEEKS / f"{name}.md").exists() else "[[_Seahorse]]"


def _deliver(name: str, image: bytes, text: str, pattern: str = "") -> bool:
    """Sends the image with its caption, and records it in Messages as `retro`, a kind that does not count toward
    the daily limit. The caller holds the vault lock."""
    try:
        tg_id = telegram_api.send_photo(image, text)
    except Exception:  # noqa: BLE001 - a network error: the caller keeps what it must try again
        log.exception("retro: the image of week %s did not go out", name)
        return False
    if tg_id is None:
        return False
    messenger.record("retro", _source(name), f"{text} · {pattern}" if pattern else text, tg_id, uuid.uuid4().hex[:6])
    _remember(name, sent=now().isoformat(timespec="minutes"))
    return True


def _hold(name: str, image: bytes, text: str, pattern: str) -> None:
    _held_path().parent.mkdir(parents=True, exist_ok=True)
    _held_path().write_bytes(image)
    data = _load()
    data["held"] = {"week": name, "caption": text, "pattern": pattern, "at": now().isoformat(timespec="minutes")}
    _save(data)


def send_week(today: date | None = None) -> bool:
    """Sunday, before the plan: the image of the week that ends. True when it went out, or waits for the end of
    the quiet hours. Once a week. It never stops the weekly run: an error is only logged."""
    try:
        return _send_week(today or now().date())
    except Exception:  # noqa: BLE001 - the retrospective is an extra: the plan must still come
        log.exception("the retrospective failed")
        return False


def _send_week(today: date) -> bool:
    monday = monday_of(today)
    name = vault.week_name(monday)
    if remembered(name).get("sent"):
        log.info("retro: the image of week %s went out already", name)
        return False
    with vault.lock():
        vault.pull()
        stats = compute(monday, today)
    if stats.empty:
        log.info("retro: week %s had no task and no capture, no image", name)
        return False
    stats.pattern, stats.adjustment = ask(stats)  # without the lock: the captures do not wait for the model
    _remember(name, pattern=stats.pattern, adjustment=stats.adjustment)
    image, text = png(stats), caption(stats)
    with vault.lock():
        vault.pull()
        write_review(stats)
        if messenger.quiet(now()):
            _hold(name, image, text, stats.pattern)
            log.info("retro: the image of week %s waits for the end of the quiet hours", name)
            sent = True
        else:
            sent = _deliver(name, image, text, stats.pattern)
        vault.publish(f"retro: week {name}")
    return sent


def after_quiet() -> str:
    """When the bot sends an image held in the quiet hours: one minute after they end, as HH:MM."""
    minute = (config.QUIET_UNTIL + 1) % (24 * 60)
    return f"{minute // 60:02d}:{minute % 60:02d}"


def send_held() -> str | None:
    """The image held in the quiet hours, now that they ended. Nothing in the quiet hours. An image that waited
    more than HELD_DAYS days is dropped. A failed send stays for the next call."""
    held = _load().get("held")
    if not held or messenger.quiet(now()):
        return None
    image = _held_path().read_bytes() if _held_path().exists() else None
    try:
        late = now() - datetime.fromisoformat(held["at"]) > timedelta(days=HELD_DAYS)
    except (KeyError, TypeError, ValueError):
        late = True
    sent = False
    if image and not late:
        with vault.lock():
            vault.pull()
            sent = _deliver(held.get("week", ""), image, held.get("caption", ""), held.get("pattern", ""))
            vault.publish("messenger: retrospective")
    if sent or late or not image:
        data = _load()
        data.pop("held", None)
        _save(data)
        _held_path().unlink(missing_ok=True)
    return f"retrospective of week {held.get('week')}" if sent else None


def on_demand(today: date | None = None, previous: bool = False) -> tuple[bytes, str] | None:
    """For `/retro`: the image of this week as it is today, or of the week before with `previous`. The numbers are
    counted again, and the two lines kept from its Sunday come back. No model call. None when the week has
    nothing to show."""
    today = today or now().date()
    stats = compute(monday_of(today) - timedelta(days=7 if previous else 0), today)
    if stats.empty:
        return None
    kept = remembered(stats.name)
    stats.pattern, stats.adjustment = kept.get("pattern", ""), kept.get("adjustment", "")
    return png(stats), caption(stats)


def counted(kind: str, n: int) -> str:
    """A count of captures of one kind, with the word in the right number, such as "1 photo" or "3 photos"."""
    return f"{n} {i18n.t(f'retro.channel.{kind}' if n == 1 else f'retro.channels.{kind}')}"
