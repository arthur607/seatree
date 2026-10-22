"""Now: one concrete task for the free time the user has, and a question when its time ends.

`/now`, a short message such as "I have 30 min" or "what do I do now?", or the button under the morning summary
starts it. Without a time, the bot asks with buttons: 15 min, 30 min, 1 h, 2 h.

Code chooses. The candidates are the open tasks (not done, not cancelled): first the ones on today's card, then
the ones on the other days of this week, then the tasks out of the week with a due date in the next 7 days or
passed. A task stays only when its estimate fits in the free time; a task with no estimate counts as 30 min.
Work tasks (the work tag) take part only during the work hours of the routine, or when no other task fits.

Points (`score`), simple on purpose:

| Fact                                                                   | Points  |
|------------------------------------------------------------------------|---------|
| overdue, or due today                                                  | 6       |
| due in the next 3 days                                                 | 3       |
| `planned` for today                                                    | 2       |
| on today's card                                                        | 2       |
| moved to another day 2 times or more: a chance to unstick it           | 2       |
| how well it fills the time: 2 × estimate ÷ free time, rounded          | 0 to 2  |
| a heavier task (45 min or more) in the focus time of the routine, or a | 1       |
| light task (30 min or less) outside it                                 |         |

Ties: today's card first, then the other days of the week, then the rest; then the nearest due date.

The words come from one fast model call: it gets the best three, the time of day and a part of the routine, and
writes one line on why and a first step. When it fails or is slow, code writes the line.

Start: when the task's time ends (at most the free time), the bot asks "Did you finish it?". Done checks, in code,
the line of the day the task was offered for: a task with sessions on several days is done only when no other line
of the week is open (see `_finish`). Done never waits for the vault: when an agent holds it, the next Messenger run
applies the press. 15 more asks again later, Not now changes nothing. No question goes out in the quiet hours.
Each round and each question lives in `state/now.json`, so a restart loses nothing. Messages records these
messages with the kind `now`: the user asked for them, so they never count toward the daily limit.
"""
import json
import logging
import re
import threading
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config, i18n, llm, messenger, notes, tasks, vault, week

log = logging.getLogger(__name__)

TIMES = (15, 30, 60, 120)  # the time buttons, in minutes
SHORT = 60  # a longer message is never a request for a task: it is a capture
MOST_MINUTES = 240  # more "free time" than this is a time of day, such as "14h", or not free time at all
DEFAULT_ESTIMATE = 30  # the minutes of a task with no estimate
NEAR_DAYS = 7  # a task out of the week takes part when its due date is this near, or passed
SOON_DAYS = 3
HEAVY, LIGHT = 45, 30  # minutes: a heavier task for the focus time, a light one for the other times
POINTS = {"due": 6, "soon": 3, "planned": 2, "today": 2, "moved": 2, "fit": 2, "time_of_day": 1}
MOVED = 2  # a task that moved this many times gets the points of a chance to unstick it
TOP = 3  # the candidates that the model sees
MODEL_SECONDS = 30  # the longest wait for the model: then code writes the line
MORE_MINUTES = 15
LATE = timedelta(hours=2)  # after a restart, a question whose time passed longer ago is not asked
KEEP_DAYS = 3  # rounds and questions older than this leave the state file
IDENTITY = "4. 🪞 Identity"
_lock = threading.Lock()  # the bot calls this module from several threads


# ---- what a message says -------------------------------------------------------------------------------

def _plain(text: str) -> str:
    """Lowercase, without accents and punctuation, for the word lists. A dot or a comma between digits stays."""
    text = unicodedata.normalize("NFKD", text.replace("’", "'").lower()).encode("ascii", "ignore").decode()
    text = re.sub(r"(?<!\d)[.,]|[.,](?!\d)", " ", text)
    return " ".join(re.sub(r"[^\w\s.,]", " ", text).split())


def _take(text: str, phrases: list[str]) -> tuple[str, bool]:
    """Removes each phrase of the list from a plain text, as whole words. True when one was there."""
    found = False
    for phrase in sorted({_plain(p) for p in phrases if _plain(p)}, key=len, reverse=True):
        text, count = re.subn(rf"(?<!\w){re.escape(phrase)}(?!\w)", " ", text)
        found = found or count > 0
    return text, found


# What may come after hours: "and a half", "e meia", "y media"; or "and 30", "e 30 min".
AFTER_HOURS = (rf"\s*(?:(?P<half>(?:e|and|y)\s+(?:a\s+)?(?:meia|half|media))(?![a-z\d])"
               rf"|(?:e|and|y)\s+(?P<more>\d{{1,2}})(?:\s*(?:{week.MINUTES}))?(?![a-z\d]))")
# "30 min", "1h", "1h30", "1 h 30 m", "1.5h", "2 horas", "1 hora e 30 min", "1h e 30", "2 horas e meia", "20 minutes".
NUMBER = re.compile(
    rf"(?<![\w.,])(?P<hours>\d+(?:[.,]\d+)?)\s*(?:{week.HOURS})(?![a-z])"
    rf"(?:{AFTER_HOURS}|\s*(?P<rest>\d{{1,2}})\s*(?:{week.MINUTES})?(?![a-z\d]))?"
    rf"|(?<![\w.,])(?P<minutes>\d+)\s*(?:{week.MINUTES})(?![a-z])")
TAIL = re.compile(AFTER_HOURS)


def _after_hours(m: re.Match) -> int:
    """The minutes that "and a half" or "and 30" add to the hours."""
    return 30 if m["half"] else int(m["more"] or 0)


def _amounts(language: str | None) -> list[tuple[str, int]]:
    """The durations in words of the language, such as "meia hora = 30", the longest first."""
    found = []
    for item in i18n.words("now.amounts", language):
        phrase, _, value = item.rpartition("=")
        if _plain(phrase) and value.strip().isdigit():
            found.append((_plain(phrase), int(value)))
    return sorted(found, key=lambda pair: len(pair[0]), reverse=True)


def _duration(text: str, language: str | None = None) -> tuple[str, int]:
    """The first duration in a plain text, in minutes, and the text without it. Zero when there is none. Whole
    hours in words can take "and a half" or "and 30" after them: "duas horas e meia" is 150."""
    for phrase, value in _amounts(language):
        m = re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text)
        if not m:
            continue
        end = m.end()
        if value % 60 == 0 and (tail := TAIL.match(text, end)):
            value, end = value + _after_hours(tail), tail.end()
        return f"{text[:m.start()]} {text[end:]}", value
    m = NUMBER.search(text)
    if not m:
        return text, 0
    if m["hours"]:
        total = round(float(m["hours"].replace(",", ".")) * 60) + (_after_hours(m) or int(m["rest"] or 0))
    else:
        total = int(m["minutes"])
    return f"{text[:m.start()]} {text[m.end():]}", total


def parse_minutes(text: str, language: str | None = None) -> int:
    """"30 min", "meia hora", "1h", "1h30", "uma hora", "half an hour" or "media hora" in minutes. Zero when the
    text gives no time."""
    return _duration(_plain(text or ""), language)[1]


def trigger(text: str, language: str | None = None) -> int | None:
    """Is a short message about free time, or about what to do now? None when it is not: it stays a normal
    capture. Else the minutes it gives, or 0 when it gives no time.

    The message is the words of the lists in the `[now]` texts, with almost nothing else:
    - a question such as "what should I do now?", with at most 1 other word;
    - a time and a word such as "free" ("1h livre"), with at most 2 other words;
    - a time and a word such as "I have" ("tenho 30 min"), with at most 1 other word;
    - "free" and "now" ("I am free now"), with no other word.
    A word about another moment, such as "tomorrow" or a day name, makes it a capture."""
    text = (text or "").strip()
    if not text or len(text) > SHORT or "\n" in text:
        return None
    rest = _plain(text)
    if _take(rest, i18n.words("now.later_words", language))[1]:
        return None
    says_now = _take(rest, i18n.words("now.now_words", language))[1]
    rest, asked = _take(rest, i18n.words("now.ask_phrases", language))
    rest, minutes = _duration(rest, language)
    if minutes > MOST_MINUTES:
        return None  # "tenho dentista 14h" is a time of day, not 14 hours of free time
    rest, free = _take(rest, i18n.words("now.free_words", language))
    rest, has = _take(rest, i18n.words("now.have_words", language))
    rest, right_now = _take(rest, i18n.words("now.now_words", language))
    fillers = {_plain(w) for w in i18n.words("now.filler_words", language)}
    left = [w for w in rest.split() if w not in fillers]
    # In doubt, a message stays a capture: a lost capture is worse than a missed offer. So "what do I do?" alone
    # is not enough (it is often a follow-up), and "I have" takes no other word ("I have a 2h meeting").
    if (asked and (minutes or says_now) and len(left) <= 1) or (minutes and free and len(left) <= 2) \
            or (minutes and has and not left):
        return minutes
    if free and right_now and not left:
        return 0
    return None


def time_buttons(above: int = 0) -> list[tuple[str, str]]:
    """The buttons that say how much time the user has, the times longer than `above` only."""
    def label(minutes: int) -> str:
        return i18n.t("now.minutes_label", minutes=minutes) if minutes < 60 else i18n.t("now.hours_label", hours=minutes // 60)
    return [(label(m), f"now|min|{m}") for m in TIMES if m > above]


# ---- the routine ---------------------------------------------------------------------------------------

PERIODS = {"morning_words": (5 * 60, 12 * 60), "afternoon_words": (12 * 60, 18 * 60), "evening_words": (18 * 60, 2 * 60)}
CLOCK = r"(\d{1,2})(?:[:h.](\d{2}))?\s*(am|pm|h)?"
RANGE = re.compile(rf"(?<![\d:]){CLOCK}\s*(?:-|–|—|to|until|till|as|a|ate|hasta|and|e|y)\s*{CLOCK}(?!\d)")


def _fold(text: str) -> str:
    """Lowercase and without accents, with the punctuation kept: for the times in the routine."""
    return unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()


def _has(line: str, key: str) -> bool:
    """The line has a word of the list `now.<key>` of any language, at the start of a word: "concentr" is in
    "concentrate"."""
    found = {_fold(w) for language in i18n.LANGUAGES for w in i18n.words(f"now.{key}", language)}
    return any(re.search(rf"(?<!\w){re.escape(w)}", line) for w in found if w)


def _clock(hour: str, minute: str | None, half: str | None) -> int | None:
    h, m = int(hour), int(minute or 0)
    if half == "pm" and h < 12:
        h += 12
    if half == "am" and h == 12:
        h = 0
    return h * 60 + m if h <= 24 and m < 60 else None


def _ranges(line: str) -> list[tuple[int, int]]:
    """The time ranges of a line, in minutes of the day: "9h-12h", "9am to 6pm", "das 14 às 18". "9 to 6" is 9:00
    to 18:00; "22h-2h" crosses midnight."""
    found = []
    for m in RANGE.finditer(line):
        start, end = _clock(m[1], m[2], m[3]), _clock(m[4], m[5], m[6])
        if start is None or end is None or start == end:
            continue
        if end < start and end + 720 > start and not {m[3], m[6]} & {"am", "pm"}:
            end += 720
        found.append((start, end % 1440))
    return found


def _inside(minute: int, span: tuple[int, int]) -> bool:
    start, end = span
    return start <= minute < end if start <= end else minute >= start or minute < end


@dataclass
class Routine:
    """What the Identity notes say about the day of the user, read simply."""
    excerpt: str = "none"
    focus: list[tuple[int, int]] = field(default_factory=list)  # when the user focuses best, in minutes of the day
    work: tuple[int, int] | None = None  # the work hours, Monday to Friday

    def in_focus(self, moment: datetime) -> bool | None:
        """None when the notes do not say when the user focuses best."""
        if not self.focus:
            return None
        return any(_inside(moment.hour * 60 + moment.minute, span) for span in self.focus)

    def at_work(self, moment: datetime) -> bool:
        return bool(self.work) and moment.weekday() < 5 and _inside(moment.hour * 60 + moment.minute, self.work)


def _lines(name: str) -> list[str]:
    path = config.VAULT / IDENTITY / f"{name}.md"
    if not path.exists():
        return []
    return [line.strip() for line in notes.read(path)[1].splitlines() if line.strip()]


def read_routine() -> Routine:
    """The routine: a part of `Routine.md` for the model; the focus time from the first line about focus in the
    Identity notes (a time range, or a word such as "morning"); the work hours from the first time range in
    the work part of `Routine.md`."""
    routine = Routine()
    own = _lines("Routine")
    text = [line for line in own if not line.startswith("#") and not line.startswith("The routine of")]
    routine.excerpt = " / ".join(text)[:600] or "none"
    for line in own + _lines("Profile") + _lines("Patterns") + _lines("Identity"):
        low = _fold(line)
        if line.startswith("#") or not _has(low, "focus_words"):
            continue
        routine.focus = _ranges(low) or [span for key, span in PERIODS.items() if _has(low, key)]
        if routine.focus:
            break
    in_work = False
    for line in own:
        low = _fold(line)
        if line.startswith("#"):
            in_work = _has(low, "work_words")
            continue
        found = _ranges(low) if in_work or _has(low, "work_words") else []
        if found:
            routine.work = found[0]
            break
    return routine


def period(moment: datetime) -> str:
    hour = moment.hour
    return "morning" if 5 <= hour < 12 else "afternoon" if hour < 18 else "evening" if hour < 22 else "night"


# ---- the choice ----------------------------------------------------------------------------------------

@dataclass
class Choice:
    task: tasks.Task
    minutes: int  # the estimate; DEFAULT_ESTIMATE when it is unknown
    known: bool  # the estimate is written, on the week line or in the note
    place: int  # 0: on today's card; 1: on another day of this week; 2: out of the week, due soon
    day: date | None = None
    work: bool = False
    points: int = 0
    facts: list[str] = field(default_factory=list)  # for the model, in English
    why: tuple[str, dict] = ("now.why_fits", {})  # the main reason, for the line that code writes

    @property
    def name(self) -> str:
        return self.task.name

    def estimate(self) -> str:
        return week.duration(self.minutes) if self.known else f"~{week.duration(self.minutes)}"


def _open(task: tasks.Task) -> bool:
    return not task.done and str(task.fm.get("status", "")).strip().lower() != "cancelled"


def candidates(today: date) -> list[Choice]:
    """Every open task that may be done now: today's card, then the other days of this week, then the tasks out
    of the week with a near due date. A task with sessions on several days comes once, from its first place."""
    by_name = {t.name: t for t in tasks.all_tasks() if _open(t)}
    work = tasks.work_tasks()
    w = week.read(today)
    days = sorted(w.days, key=lambda d: (d.date != today, d.date)) if w else []
    found: dict[str, Choice] = {}
    for day in days:
        for item in day.items:
            task = by_name.get(item.name)
            if item.done or not task or item.name in found:
                continue
            found[item.name] = Choice(task, item.minutes or DEFAULT_ESTIMATE, bool(item.minutes),
                                      0 if day.date == today else 1, day.date, item.name in work)
    for task in by_name.values():
        if task.name in found or not task.due or (task.due - today).days > NEAR_DAYS:
            continue
        estimate = week.minutes(str(task.fm.get("estimate", "")))
        found[task.name] = Choice(task, estimate or DEFAULT_ESTIMATE, bool(estimate), 2, None, task.name in work)
    return list(found.values())


def score(c: Choice, minutes: int, moment: datetime, routine: Routine) -> Choice:
    """Gives a choice its points (see POINTS and the table above), the facts for the model, and its main reason."""
    today, due = moment.date(), c.task.due
    points, facts, reasons = 0, [], []
    if due and due <= today:
        points += POINTS["due"]
        if due == today:
            facts.append("due today")
            reasons.append(("now.why_due_today", {}))
        else:
            facts.append(f"overdue: it was due on {week.ENGLISH_DAYS[due.weekday()]} {due:%d/%m}")
            reasons.append(("now.why_overdue", {"date": due.strftime(i18n.t("reminder.date_format"))}))
    elif due and (due - today).days <= SOON_DAYS:
        days = (due - today).days
        points += POINTS["soon"]
        facts.append(f"due in {days} day(s), on {week.ENGLISH_DAYS[due.weekday()]}")
        reasons.append(("now.why_tomorrow", {}) if days == 1 else ("now.why_soon", {"days": days}))
    moved = c.task.count("moved")
    if moved >= MOVED:
        points += POINTS["moved"]
        facts.append(f"moved to another day {moved} times: a chance to unstick it")
        reasons.append(("now.why_moved", {"count": moved}))
    if c.task.planned == today:
        points += POINTS["planned"]
        facts.append(f"{config.USER_NAME} chose today for it")
        reasons.append(("now.why_planned", {}))
    if c.place == 0:
        points += POINTS["today"]
        facts.append("on today's plan")
        reasons.append(("now.why_today", {}))
    elif c.day:
        facts.append(f"planned for {week.ENGLISH_DAYS[c.day.weekday()]}")
    points += round(POINTS["fit"] * min(c.minutes, minutes) / minutes)
    facts.append(f"takes {c.estimate()} of the {minutes} free minutes" + ("" if c.known else " (no estimate: counted as 30 min)"))
    focus = routine.in_focus(moment)
    if focus and c.minutes >= HEAVY:
        points += POINTS["time_of_day"]
        facts.append("a heavier task, and now is the focus time")
    elif focus is False and c.minutes <= LIGHT:
        points += POINTS["time_of_day"]
        facts.append("a light task, and now is not the focus time")
    if c.work:
        facts.append("a work task")
    c.points, c.facts = points, facts
    c.why = reasons[0] if reasons else ("now.why_fits", {"minutes": minutes})
    return c


def ranked(minutes: int, moment: datetime, routine: Routine, shown=()) -> tuple[list[Choice], Choice | None]:
    """The tasks that fit in the minutes, the best first, without the ones already shown. When none fits: the
    shortest task that does not fit, to tell the user."""
    every = [c for c in candidates(moment.date()) if c.name not in shown]
    fits = [c for c in every if c.minutes <= minutes]
    if not routine.at_work(moment):
        fits = [c for c in fits if not c.work] or fits  # work tasks only when nothing else fits
    for c in fits:
        score(c, minutes, moment, routine)
    fits.sort(key=lambda c: (-c.points, c.place, c.task.due or date.max, c.day or date.max, c.name.lower()))
    shortest = None if fits else min(every, key=lambda c: (c.work, c.minutes, c.place), default=None)
    return fits, shortest


# ---- the words ----------------------------------------------------------------------------------------

def _body(task: tasks.Task) -> str:
    """The text of a task note without its History."""
    body = notes.read(task.path)[1] if task.path.exists() else ""
    return re.split(r"^#+\s*History\s*$", body, maxsplit=1, flags=re.M | re.I)[0]


def _first_step(task: tasks.Task) -> str:
    """The first open checklist item of the task note, or a general first step."""
    m = re.search(r"^\s*[-*+] \[ \] (.+)$", _body(task), re.M)
    return m[1].strip() if m else i18n.t("now.step_default")


def _line(c: Choice, minutes: int) -> str:
    """The why that code writes: the main reason, and how it fits the time."""
    key, values = c.why
    fits = i18n.t("now.why_fits", minutes=minutes)
    return fits if key == "now.why_fits" else f"{i18n.t(key, **values)} {fits}"


def _parse(text: str, top: list[Choice]) -> tuple[Choice, str, str] | None:
    """The model's JSON: one of the candidates, a line on why and a first step. None when it is not usable."""
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        data = json.loads(m[0]) if m else {}
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    name = str(data.get("task", "")).strip().strip("[]").strip()
    choice = next((c for c in top if c.name.lower() == name.lower()), None)
    why = " ".join(str(data.get("why", "")).split())
    step = " ".join(str(data.get("step", "")).split())
    if not (choice and why and step):
        return None
    return choice, why[:160], step[:140]


def _words(top: list[Choice], minutes: int, moment: datetime, routine: Routine) -> tuple[Choice, str, str]:
    """One fast model call writes the why and the first step. Code writes them when the model fails."""
    lines = []
    for i, c in enumerate(top, 1):
        body = " ".join(_body(c.task).split())[:200]
        lines.append(f"{i}. {c.name} · {c.estimate()} · {'; '.join(c.facts)}" + (f" · notes: {body}" if body else ""))
    focus = routine.in_focus(moment)
    request = llm.prompt(
        "now", minutes=str(minutes), weekday=week.ENGLISH_DAYS[moment.weekday()], time=f"{moment:%H:%M}",
        period=period(moment), candidates="\n".join(lines), routine=routine.excerpt,
        focus={None: "The notes do not say when the user focuses best.", True: "Now is the focus time of the routine.",
               False: "Now is not the focus time of the routine."}[focus])
    try:
        answer = llm.run(request, "now", max_turns=1, timeout=MODEL_SECONDS)
        found = None if answer.error else _parse(answer.text, top)
    except Exception:  # noqa: BLE001 - code writes the line
        log.exception("now: the model failed")
        found = None
    if found:
        return found
    return top[0], _line(top[0], minutes), _first_step(top[0].task)


@dataclass
class Offer:
    """A message of the round: a task with its buttons, or a word that nothing fits."""
    text: str
    buttons: list[tuple[str, str]]
    task: str = ""
    minutes: int = 0  # the estimate of the task
    day: str = ""  # the day of the week card it comes from, as YYYY-MM-DD; empty for a task out of the week


def offer(minutes: int, moment: datetime | None = None, shown=(), n: int = 1) -> Offer | None:
    """The task to do in the minutes, as a message with its buttons; `n` counts the tasks of the round. With
    nothing that fits: the shortest task, and buttons for more time. None when the round showed every task that
    fits. It may call the model, so it runs outside the vault lock."""
    moment = moment or messenger.now()
    routine = read_routine()
    fits, shortest = ranked(minutes, moment, routine, shown)
    if not fits:
        if shown:
            return None
        if shortest:
            return Offer(i18n.t("now.nothing_fits", minutes=minutes, task=shortest.name, estimate=shortest.estimate()),
                         time_buttons(above=minutes))
        return Offer(i18n.t("now.nothing_open"), [])
    choice, why, step = _words(fits[:TOP], minutes, moment, routine)
    buttons = [(i18n.t("now.start"), f"now|start|{n}")]
    if len(fits) > 1:
        buttons.append((i18n.t("now.another"), f"now|next|{n}"))
    buttons.append((i18n.t("now.not_now"), f"now|skip|{n}"))
    text = i18n.t("now.pick", task=choice.name, estimate=choice.estimate(), why=why, step=step)
    return Offer(text, buttons, choice.name, choice.minutes, choice.day.isoformat() if choice.day else "")


# ---- the state of the rounds and the questions ---------------------------------------------------------

def _path() -> Path:
    return config.STATE / "now.json"


def _load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(state: dict) -> None:
    """Writes the state, without the rounds and the questions older than KEEP_DAYS. A question that waits for its
    time stays: after a restart, `waiting` decides what to do with it."""
    limit = messenger.now() - timedelta(days=KEEP_DAYS)

    def recent(item: dict) -> bool:
        try:
            return datetime.fromisoformat(item.get("at", "")) >= limit
        except (TypeError, ValueError):
            return False
    state["rounds"] = {k: r for k, r in state.get("rounds", {}).items() if recent(r)}
    state["follow_ups"] = {k: f for k, f in state.get("follow_ups", {}).items()
                           if f.get("status") in ("waiting", "queued") or recent(f)}
    _path().parent.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _change(function):
    """Reads the state, lets `function` change it, and saves it, one thread at a time."""
    with _lock:
        state = _load()
        result = function(state)
        _save(state)
        return result


def _claim(group: str, key, statuses: set[str], n: int | None = None) -> dict | None:
    """Takes a round or a question for one button press: it must be in one of `statuses` (and, for a round, at
    its task number `n`). It becomes "busy", so a double tap acts once. Returns a copy, or None."""
    def change(state):
        item = state.get(group, {}).get(str(key))
        if not item or item.get("status") not in statuses or (n is not None and item.get("picks") != n):
            return None
        item["status"] = "busy"
        return dict(item)
    return _change(change)


def _update(group: str, key, **fields) -> None:
    def change(state):
        item = state.get(group, {}).get(str(key))
        if item is not None:
            item.update(fields)
    _change(change)


def _show(message_id: int, minutes: int, found: Offer, n: int, shown: list[str]) -> None:
    """The round shows `found` now."""
    status = "showing" if found.task else "asking" if found.buttons else "closed"
    _update("rounds", message_id, minutes=minutes, status=status, picks=n, task=found.task, estimate=found.minutes,
            day=found.day, text=found.text, shown=shown + [found.task] if found.task else shown)


def open_round(message_id: int, minutes: int = 0, found: Offer | None = None) -> None:
    """A new round in a message: the question of the time (no offer), or a first task, or a word that nothing
    fits."""
    def change(state):
        state.setdefault("rounds", {})[str(message_id)] = {
            "at": messenger.now().isoformat(timespec="seconds"), "minutes": minutes, "status": "asking", "picks": 0,
            "shown": [], "task": "", "estimate": 0, "text": "", "record": ""}
    _change(change)
    if found:
        _show(message_id, minutes, found, 1, [])


def record_pick(message_id: int, text: str, task: str) -> None:
    """Writes a task that a round shows in Messages, with the kind `now`. It waits for the vault lock: the bot
    calls it after the message went out."""
    record = uuid.uuid4().hex[:6]
    with vault.lock():
        messenger.record("now", f"[[{task}]]", text, message_id, record, "pick")
    _update("rounds", message_id, record=record)


# ---- the buttons ---------------------------------------------------------------------------------------

@dataclass
class Reply:
    """What the bot does after a button: a short note on the button only (`toast`), or the new text and buttons
    of the message. `wait` seconds later, the question `follow_up` goes out. `record`: a task shown, for Messages."""
    text: str = ""
    buttons: list = field(default_factory=list)
    toast: str | None = None
    wait: float | None = None
    follow_up: str = ""
    record: str = ""


def _already() -> Reply:
    return Reply(toast=i18n.t("chat.already_done"))


def press(message_id: int, action: str, arg: str, moment: datetime | None = None) -> Reply:
    """A `now|<action>|<arg>` button under a message of the round or under a question. Blocking: the bot runs it in
    a thread."""
    moment = moment or messenger.now()
    if action == "min":
        return _choose_time(message_id, int(arg) if arg.isdigit() else 0, moment)
    if action in ("start", "next", "skip"):
        n = int(arg) if arg.isdigit() else -1
        return {"start": _start, "next": _another, "skip": _skip}[action](message_id, n, moment)
    if action in ("done", "more", "stop"):
        return {"done": _done, "more": _more, "stop": _stop}[action](arg, moment)
    return Reply(toast="")


def _offer_in(message_id: int, minutes: int, moment: datetime, shown: list[str], n: int, back: str) -> Reply:
    """Shows the next task of a round. On a failure the round goes back to `back`, so the button works again."""
    try:
        found = offer(minutes, moment, shown, n)
    except Exception:
        _update("rounds", message_id, status=back)
        raise
    if found is None:
        _update("rounds", message_id, status=back)
        return Reply(toast=i18n.t("now.no_other", minutes=minutes))
    _show(message_id, minutes, found, n, shown)
    return Reply(found.text, found.buttons, record=found.task)


def _choose_time(message_id: int, minutes: int, moment: datetime) -> Reply:
    if not minutes or not _claim("rounds", message_id, {"asking"}):
        return _already()
    return _offer_in(message_id, minutes, moment, [], 1, "asking")


def _another(message_id: int, n: int, moment: datetime) -> Reply:
    """The next task, never one that this round showed."""
    r = _claim("rounds", message_id, {"showing"}, n)
    if not r:
        return _already()
    return _offer_in(message_id, r["minutes"], moment, r["shown"], n + 1, "showing")


def _skip(message_id: int, n: int, moment: datetime) -> Reply:
    r = _claim("rounds", message_id, {"showing"}, n)
    if not r:
        return _already()
    _update("rounds", message_id, status="closed")
    return Reply(f"{r['text']}\n\n{i18n.t('now.closed')}")


def _start(message_id: int, n: int, moment: datetime) -> Reply:
    """The user starts the task. The question comes when its time ends: its estimate, at most the free time. In
    the quiet hours there is no question."""
    r = _claim("rounds", message_id, {"showing"}, n)
    if not r:
        return _already()
    if r.get("record"):
        messenger.set_reaction(r["record"], "acted")
    wait = min(r.get("estimate") or DEFAULT_ESTIMATE, r.get("minutes") or DEFAULT_ESTIMATE)
    at = moment + timedelta(minutes=wait)
    if messenger.quiet(at):
        _update("rounds", message_id, status="started")
        return Reply(f"{r['text']}\n\n{i18n.t('now.started_quiet')}")
    fid = uuid.uuid4().hex[:6]

    def change(state):
        state["rounds"][str(message_id)]["status"] = "started"
        state.setdefault("follow_ups", {})[fid] = {
            "task": r["task"], "day": r.get("day", ""), "at": at.isoformat(timespec="seconds"), "status": "waiting",
            "message": 0, "text": "", "record": "", "round": message_id}
    _change(change)
    return Reply(f"{r['text']}\n\n{i18n.t('now.started', minutes=wait)}", wait=wait * 60, follow_up=fid)


# ---- the question when the time ends -------------------------------------------------------------------

def _task(name: str) -> tasks.Task | None:
    return next((t for t in tasks.all_tasks() if t.name == name), None)


def check(fid: str, moment: datetime | None = None) -> Reply | None:
    """The question at the end of a started task: "Did you finish it?". None when it must not go out: it was
    answered, it is the quiet hours, or the task is done already."""
    moment = moment or messenger.now()

    def change(state):
        f = state.get("follow_ups", {}).get(fid)
        if not f or f.get("status") != "waiting":
            return None
        task = _task(f["task"])
        if messenger.quiet(moment) or not task or not _open(task):
            f["status"] = "skipped"
            return None
        f["status"], f["text"] = "asked", i18n.t("now.check", task=f["task"])
        return dict(f)
    f = _change(change)
    if not f:
        return None
    buttons = [(i18n.t("now.done"), f"now|done|{fid}"), (i18n.t("now.more", minutes=MORE_MINUTES), f"now|more|{fid}"),
               (i18n.t("now.not_now"), f"now|stop|{fid}")]
    return Reply(f["text"], buttons, follow_up=fid, record=f["task"])


def sent_check(fid: str, tg_id: int | None) -> None:
    """The question went out (or not): keep its message, and write it in Messages with the kind `now`."""
    if not tg_id:
        _update("follow_ups", fid, status="skipped")
        return
    f = _load().get("follow_ups", {}).get(fid, {})
    record = uuid.uuid4().hex[:6]
    with vault.lock():
        messenger.record("now", f"[[{f.get('task', '')}]]", f.get("text", ""), tg_id, record, "check")
    _update("follow_ups", fid, message=tg_id, record=record)


def _progress(today: date) -> str:
    """A warm line with what is done of today's plan."""
    w = week.read(today)
    items = w.day(today).items if w and w.day(today) else []
    done = sum(1 for i in items if i.done)
    if not items:
        return i18n.t("now.done_line")
    if done == len(items):
        return i18n.t("now.done_all")
    return i18n.t("now.done_plan", done=done, total=len(items))


def _date(value) -> date | None:
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError:
        return None


def _finish(name: str, offered: date | None, today: date) -> None:
    """Done, in code. A task with open lines in this week, such as sessions on several days: only one line is
    checked, the one of the day it was offered for, else today's, else its first open line. `tasks.sync` then
    makes the task done only when no other line of that week is open, and a task that repeats opens again with
    its next date. A task with no open line in the week is done in its note (`tasks.mark_done`)."""
    tasks.sync(today)  # the sync sees the week as it is now, so the check below is the only change it finds
    w = week.read(today)
    days = [d.date for d in w.days if any(i.name == name and not i.done for i in d.items)] if w else []
    if not days:
        tasks.mark_done(name, today)
        return
    week.check(name, next((d for d in (offered, today) if d in days), days[0]))
    tasks.sync(today)


def _done(fid: str, moment: datetime) -> Reply:
    """Done: the task is done in code (see `_finish`). The press never waits for the vault: when an agent holds
    it, the press waits in the state file as "queued", the user reads that it is coming, and the next Messenger
    run applies it (see `apply_waiting`)."""
    f = _claim("follow_ups", fid, {"asked"})
    if not f:
        return _already()
    today = moment.date()
    try:
        with vault.lock(wait=False):
            vault.pull()
            _finish(f["task"], _date(f.get("day")), today)
            line, status = _progress(today), "done"
            vault.publish(f"now: {f['task']} done")
    except vault.Busy:
        line, status = i18n.t("now.done_later"), "queued"
    except Exception:
        _update("follow_ups", fid, status="asked")
        raise
    if f.get("record"):
        messenger.set_reaction(f["record"], "acted")
    _update("follow_ups", fid, status=status, pressed=today.isoformat())
    return Reply(f"{f['text']}\n\n{line}")


def apply_waiting() -> list[str]:
    """For the Messenger, with the vault lock: the Done presses that came while an agent held the vault. Returns
    the names of their tasks."""
    queued = {fid: f for fid, f in _load().get("follow_ups", {}).items() if f.get("status") == "queued"}
    for fid, f in queued.items():
        try:
            _finish(f["task"], _date(f.get("day")), _date(f.get("pressed")) or messenger.now().date())
        except Exception:  # noqa: BLE001 - one broken press must not stop the Messenger run
            log.exception("now: could not apply the Done of %s", f.get("task"))
        _update("follow_ups", fid, status="done")
    return [f["task"] for f in queued.values()]


def _more(fid: str, moment: datetime) -> Reply:
    """15 more minutes: the question comes again then, unless that is in the quiet hours."""
    f = _claim("follow_ups", fid, {"asked"})
    if not f:
        return _already()
    at = moment + timedelta(minutes=MORE_MINUTES)
    if messenger.quiet(at):
        _update("follow_ups", fid, status="closed")
        return Reply(f"{f['text']}\n\n{i18n.t('now.more_quiet')}")
    _update("follow_ups", fid, status="waiting", at=at.isoformat(timespec="seconds"))
    return Reply(f"{f['text']}\n\n{i18n.t('now.more_line', minutes=MORE_MINUTES)}", wait=MORE_MINUTES * 60, follow_up=fid)


def _stop(fid: str, moment: datetime) -> Reply:
    """Not now: a kind word. The task stays as it is: no `moved`, no `postponed`."""
    f = _claim("follow_ups", fid, {"asked"})
    if not f:
        return _already()
    _update("follow_ups", fid, status="closed")
    return Reply(f"{f['text']}\n\n{i18n.t('now.stop_line', task=f['task'])}")


def waiting(moment: datetime | None = None) -> list[tuple[str, float]]:
    """The questions to ask again after a restart, with the seconds until each one. A question whose time passed
    more than LATE ago is dropped; one whose time passed a little goes out in a few seconds."""
    moment = moment or messenger.now()

    def change(state):
        found = []
        for fid, f in state.get("follow_ups", {}).items():
            if f.get("status") != "waiting":
                continue
            at = datetime.fromisoformat(f["at"])
            if at < moment - LATE:
                f["status"] = "expired"
                continue
            found.append((fid, max((at - moment).total_seconds(), 5.0)))
        return found
    return _change(change)
