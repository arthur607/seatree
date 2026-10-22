"""People: Seahorse takes care of the relationships of the user. Rules in `6. ⚙️ System/_MESSENGER.md`.

Code reads the pages in `2. 📓 Memory/People/` and decides WHEN. A model only writes the text, from the page:
- `birthday`: a week before, with who the person is, a story and the gift, and on the day, with a message that
  the user can send. Each one once a year. The one on the day goes out also over the daily limit.
- `briefing`: a task of today links a person with something worth recalling: about 2 hours before the time in the
  task, or after the morning summary when it has no time. One a day for each person.
- `reconnect`: a person with `keep_in_touch` and no contact for longer than that: an offer to call on a day with
  room. At most one a day, and one a week for each person.
The Messenger asks `prepare` with the lock, and runs `converse` after it, as it does for the Companion. The
buttons under these messages act in code, with no model, and never wait for the vault lock: a press waits in a
small file (`press`) until the vault is free (`apply_now`, or `update` on the next Messenger run).
"""
import calendar
import html
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path

from . import companion, config, i18n, llm, messenger, notes, tasks, vault, week

log = logging.getLogger(__name__)

FOLDER = "2. 📓 Memory/People"
WEEK_BEFORE = 7  # days before a birthday when it comes back
BRIEFING_BEFORE = timedelta(hours=2)  # a briefing comes this long before the time of the encounter
AFTER_SUMMARY = timedelta(minutes=20)  # these messages come after the morning summary, not on top of it
GAP = timedelta(hours=1)  # between two of these messages, except a briefing before a time
RECONNECT_FROM = timedelta(hours=4)  # after the time of the morning summary: a call offer comes from midday on
ASK_AGAIN, SNOOZE_DAYS = 7, 14  # days before a new call offer for the same person, and after "Not now"
RECENT_DAYS, NEAR_BIRTHDAY = 60, 30  # an appearance or a birthday this near is worth recalling
MAX_TRIES = 3  # model runs for the same message before it gives up, so a failure does not repeat all day
OFFER_DAYS = 30  # days that the buttons of a message keep working
GIFT_ESTIMATE, CALL_ESTIMATE = "1h", "30m"
NOTE_ROOM = 4000  # characters of the page that the model reads
CADENCES = {"weekly": 7, "biweekly": 14, "monthly": 30, "quarterly": 91, "yearly": 365}
ACTIONS = {"birthday": ("gift", "sorted", "sent"), "reconnect": ("week", "later")}
ENGLISH_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---- the fields of a page ---------------------------------------------------------------------------------

ISO = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")
NO_YEAR = re.compile(r"--?(\d{1,2})-(\d{1,2})")
SHORT = re.compile(r"(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{4}|\d{2}))?")
# The forms that the Vocabulary asks for. Lint warns about the others, which code still reads.
CANONICAL = {"birthday": re.compile(r"\d{4}-\d{2}-\d{2}|--\d{2}-\d{2}"),
             "keep_in_touch": re.compile(r"weekly|monthly|quarterly|\d+d"),
             "last_contact": re.compile(r"\d{4}-\d{2}-\d{2}")}


@dataclass(frozen=True)
class Birthday:
    month: int
    day: int
    year: int | None = None

    def on(self, year: int) -> date:
        """The birthday in a year. A 29 February is on 28 February in a common year."""
        day = 28 if (self.month, self.day) == (2, 29) and not calendar.isleap(year) else self.day
        return date(year, self.month, day)

    def next(self, today: date) -> date:
        """The next birthday, today included."""
        this = self.on(today.year)
        return this if this >= today else self.on(today.year + 1)


def _birthday(month: int, day: int, year: int | None = None) -> Birthday | None:
    try:
        date(year or 2000, month, day)  # 2000 is a leap year: 29 February is a date
    except ValueError:
        return None
    return Birthday(month, day, year)


def _year(text: str | None) -> int | None:
    if not text:
        return None
    if len(text) == 4:
        return int(text)
    year = 2000 + int(text)
    return year if year <= date.today().year else year - 100


def parse_birthday(value) -> Birthday | None:
    """`YYYY-MM-DD`, or `--MM-DD` when the year is unknown. Code also reads what a model or the user may write:
    `13/11`, `13/11/1990` or `13.11`, with the day first as in the week cards, or the month first when that is
    not a date, such as `11/30`."""
    text = str(value or "").strip().strip("\"'")
    if m := ISO.fullmatch(text):
        return _birthday(int(m[2]), int(m[3]), int(m[1]))
    if m := NO_YEAR.fullmatch(text):
        return _birthday(int(m[1]), int(m[2]))
    if m := SHORT.fullmatch(text):
        year, first, second = _year(m[3]), int(m[1]), int(m[2])
        return _birthday(second, first, year) or _birthday(first, second, year)
    return None


def parse_day(value) -> date | None:
    """A full date, such as `last_contact`: `YYYY-MM-DD`, or `15/01/2026` read as a birthday is."""
    found = parse_birthday(value)
    return date(found.year, found.month, found.day) if found and found.year else None


def parse_cadence(value) -> int | None:
    """The days of `keep_in_touch`: `weekly`, `monthly`, `quarterly`, or a number of days such as `30d`.
    Code also reads `45`, `45 days` and `2w`."""
    text = str(value or "").strip().strip("\"'").lower()
    if text in CADENCES:
        return CADENCES[text]
    m = re.fullmatch(r"(\d+)\s*(d|days?|w|weeks?)?", text)
    if not m or not int(m[1]):
        return None
    return int(m[1]) * (7 if m[2] and m[2].startswith("w") else 1)


def _date(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


@dataclass
class Person:
    path: Path
    fm: dict
    body: str

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def link(self) -> str:
        return f"[[{self.name}]]"

    @property
    def names(self) -> set[str]:
        """The name of the page and its aliases, in lowercase, to match links."""
        aliases = self.fm.get("aliases") or []
        aliases = aliases if isinstance(aliases, list) else [aliases]
        return {str(n).strip().lower() for n in [self.name, *aliases] if str(n).strip()}

    def field(self, key: str) -> str:
        value = self.fm.get(key)
        return "" if value is None or isinstance(value, list) else str(value).strip()

    @property
    def birthday(self) -> Birthday | None:
        return parse_birthday(self.field("birthday"))

    @property
    def cadence(self) -> int | None:
        return parse_cadence(self.field("keep_in_touch"))

    @property
    def last_contact(self) -> date | None:
        return parse_day(self.field("last_contact"))


def folder() -> Path:
    return config.VAULT / FOLDER


def everyone() -> list[Person]:
    """The People pages. The summary `_People.md`, any page that is not a person, and a file that is not text are
    left out."""
    base = folder()
    found = []
    for path in sorted(base.rglob("*.md")) if base.exists() else []:
        if path.name.startswith("_") or any(part.startswith(".") for part in path.relative_to(base).parts):
            continue
        try:
            fm, body = notes.read(path)
        except (OSError, UnicodeDecodeError):
            log.warning("people: could not read %s", path.name)
            continue
        if fm.get("type", "entity") == "entity" and fm.get("entity", "person") in ("person", ""):
            found.append(Person(path, fm, body))
    return found


# Words that tell a birthday in the text of a page, in the three languages.
BIRTHDAY_WORDS = ("aniversário", "aniversario", "birthday", "cumpleaños", "nasceu", "nació", "born on")


def missing_birthdays(limit: int = 20) -> list[str]:
    """The People pages that tell a birthday in their text but have no readable `birthday` field. The nightly
    Reviewer fills the field from the text, so the pages made before 1.3.0 get birthday messages too."""
    found = []
    for person in everyone():
        if person.birthday or not any(word in person.body.lower() for word in BIRTHDAY_WORDS):
            continue
        found.append(vault.rel(person.path))
        if len(found) == limit:
            break
    return found


def find(name: str) -> Person | None:
    return next((p for p in everyone() if p.name == name), None)


@cache
def _examples() -> frozenset[str]:
    """The lines of the Person template that only show what goes in a section."""
    path = config.TEMPLATE / "6. ⚙️ System" / "_Templates" / "Person.md"
    body = notes.parse(vault.fill(path.read_text(encoding="utf-8"), date.today()))[1] if path.exists() else ""
    return frozenset(line.strip() for line in body.splitlines() if line.strip())


def section(body: str, title: str) -> list[str]:
    """The lines of one `## ` section of a page, without the blank ones and the example lines of the template."""
    m = re.search(rf"(?m)^## {re.escape(title)}[ \t]*$", body)
    if not m:
        return []
    rest = body[m.end():]
    end = re.search(r"(?m)^#{1,2} ", rest)
    lines = (rest[:end.start()] if end else rest).splitlines()
    return [line.strip() for line in lines if line.strip() and "{{" not in line and line.strip() not in _examples()]


# ---- tasks and days ---------------------------------------------------------------------------------------

def _links(task: tasks.Task) -> set[str]:
    """The notes that a task links, in lowercase: in its fields, such as its subject, and in its body."""
    text = " ".join(str(value) for value in task.fm.values()) + "\n" + notes.read(task.path)[1]
    return {link.lower() for link in notes.links(text)}


def _has(text: str, word: str) -> bool:
    """The word, or its plural with "s", is in the text as a whole word."""
    return bool(word) and re.search(rf"(?<!\w){re.escape(word)}s?(?!\w)", text, re.I) is not None


def gift_task(person: Person, open_tasks: list[tasks.Task], links: dict) -> tasks.Task | None:
    """An open task about a gift for the person: it links the page or names the person, and its name has a gift
    word of any language, such as "Buy Brandão's gift"."""
    words = [w for language in i18n.LANGUAGES for w in i18n.words("people.gift_words", language)]
    for task in open_tasks:
        named = links.get(task.name, set()) & person.names or any(_has(task.name, n) for n in person.names)
        if named and any(_has(task.name, w) for w in words):
            return task
    return None


def in_week(name: str, today: date) -> date | None:
    """The day of an open line of the task in the week files, from today on."""
    for start in (today, today + timedelta(days=7)):
        found = week.read(start)
        for day in found.days if found else []:
            if day.date >= today and any(i.name == name and not i.done for i in day.items):
                return day.date
    return None


def planned_day(task: tasks.Task, today: date) -> date | None:
    """The day the task has in the week, or its `planned` day, when it is today or later."""
    return in_week(task.name, today) or (task.planned if task.planned and task.planned >= today else None)


def best_day(first: date, last: date, estimate: str) -> date:
    """The day for a task that code plans: the first day from `first` to `last` with room for it in the week,
    else the day with the most room. A day of a week with no file yet has all its room."""
    need, work, weeks, options = week.minutes(estimate), tasks.work_tasks(), {}, []
    day = first
    while day <= max(first, last):
        monday = day - timedelta(days=day.weekday())
        if monday not in weeks:
            weeks[monday] = week.read(day)
        card = weeks[monday].day(day) if weeks[monday] else None
        room = card.capacity - card.total(work) if card else (week.minutes(config.DAILY_CAPACITY) or 240)
        if room >= need:
            return day
        options.append((room, day))
        day += timedelta(days=1)
    return max(options, key=lambda option: (option[0], -option[1].toordinal()))[1]


def day_words(when: date) -> str:
    """A day as the user reads it: "Friday, 11/13" in English, "sexta, 13/11" in Portuguese."""
    return f"{i18n.words('days_long')[when.weekday()]}, {when.strftime(i18n.t('reminder.date_format'))}"


def _english(when: date) -> str:
    return f"{ENGLISH_DAYS[when.weekday()]} {when.isoformat()}"


# ---- times --------------------------------------------------------------------------------------------------

CLOCK = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
HOUR = re.compile(r"(?<![\w.,~])([01]?\d|2[0-3])h([0-5]\d)?(?!\w)", re.I)
TWELVE = re.compile(r"(?<![\d:])(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(am|pm)\b", re.I)
FIRST_HOUR = 6  # "2h" is a duration, not a time: a time written with "h" starts at 6h


def event_time(text: str) -> int | None:
    """The time written in a task, in minutes of the day: "17h", "17h30", "17:00" or "5pm". A duration in
    parentheses, such as "(2h)", is not a time."""
    text = week.GROUP.sub(lambda m: " " if week.minutes(m[1]) else m[0], text or "")
    if m := TWELVE.search(text):
        return (int(m[1]) % 12 + (12 if m[3].lower() == "pm" else 0)) * 60 + int(m[2] or 0)
    if m := CLOCK.search(text):
        return int(m[1]) * 60 + int(m[2])
    for m in HOUR.finditer(text):
        if int(m[1]) >= FIRST_HOUR:
            return int(m[1]) * 60 + int(m[2] or 0)
    return None


def _morning(moment: datetime) -> datetime:
    hours, minutes = map(int, config.SCHEDULE["morning"].split(":"))
    return moment.replace(hour=hours, minute=minutes, second=0, microsecond=0)


def after_summary(moment: datetime, history: list[messenger.Message]) -> bool:
    """The morning summary of today went out some minutes ago. When it did not come, an hour after its time."""
    sent = [m.when for m in history if m.kind == "summary" and m.when.date() == moment.date()]
    if sent:
        return moment - max(sent) >= AFTER_SUMMARY
    return moment >= _morning(moment) + messenger.SUMMARY_WAIT


# ---- what is worth a message ------------------------------------------------------------------------------

@dataclass
class Plan:
    """A message about a person that code chose. The model writes its text from the facts and the page."""
    kind: str  # birthday, briefing or reconnect
    stage: str  # for a birthday: week-before or today
    person: Person
    facts: list[str]
    key: str  # its tries in the state
    fallback: str = ""  # the text when the model writes none; a briefing has none, and then it stays silent
    urgent: bool = False  # it goes out over the daily limit
    timed: bool = False  # a briefing before a time: it does not wait for the gap between these messages
    buttons: list = field(default_factory=list)  # (label, action)
    offer: dict = field(default_factory=dict)  # what the buttons do

    @property
    def priority(self) -> int:
        if self.kind == "briefing":
            return 0 if self.timed else 2
        if self.kind == "birthday":
            return 1 if self.stage == "today" else 3
        return 4


def _about(person: Person) -> list[str]:
    facts = [f"- Person: {person.name}"]
    if person.field("relationship"):
        facts.append(f"- Relationship to the user: {person.field('relationship')}")
    return facts


def _sent(history: list[messenger.Message], kind: str, person: Person, stage: str, since: date) -> bool:
    return any(m.kind == kind and m.source == person.link and m.when.date() >= since
               and (not stage or m.text.split(" · ", 1)[0].strip() == stage) for m in history)


def birthdays(today: date, people: list[Person], history: list[messenger.Message], open_tasks: list[tasks.Task],
              links: dict) -> list[Plan]:
    """A birthday in the next 7 days that had no message yet, or a birthday today. A birthday that a page got
    later, 3 days before, still comes."""
    found = []
    for person in people:
        birthday = person.birthday
        when = birthday.next(today) if birthday else None
        if not when or (when - today).days > WEEK_BEFORE:
            continue
        stage = "today" if when == today else "week-before"
        if _sent(history, "birthday", person, stage, when - timedelta(days=WEEK_BEFORE + 1)):
            continue
        facts = _about(person)
        age = f" Turns {when.year - birthday.year}." if birthday.year and when.year > birthday.year else ""
        offer = {"kind": "birthday", "person": person.name, "date": when.isoformat()}
        if stage == "today":
            facts.append(f"- Birthday: today.{age}")
            found.append(Plan("birthday", stage, person, facts, f"birthday|{person.name}|{when}|{stage}",
                              i18n.t("people.birthday_today", name=person.name), urgent=True,
                              buttons=[(i18n.t("people.sent_button"), "sent")], offer=offer))
            continue
        facts.append(f"- Birthday: {_english(when)}, in {(when - today).days} days.{age}")
        gift = gift_task(person, open_tasks, links)
        planned = planned_day(gift, today) if gift else None
        if gift:
            status = f"in the week on {_english(planned)}" if planned else "not in the week yet"
            facts.append(f'- Open gift task: "{gift.name}", {status}.')
        else:
            facts.append("- Open gift task: none.")
        buttons = []
        if not planned:
            estimate = str(gift.fm.get("estimate", "")).strip() if gift else ""
            last = when - timedelta(days=1)  # the day before the birthday; today, when it is tomorrow
            day = best_day(min(today + timedelta(days=1), last), last, estimate or GIFT_ESTIMATE)
            name = gift.name if gift else i18n.t("people.gift_task", name=person.name)
            offer |= {"task": name, "day": day.isoformat()}
            buttons.append((i18n.t("people.gift_button"), "gift"))
            facts.append(f"- A button can put the gift in the week, on {_english(day)}.")
        buttons.append((i18n.t("people.sorted_button"), "sorted"))
        fallback = i18n.t("people.birthday_soon", name=person.name, day=day_words(when))
        if gift:
            fallback += "\n" + i18n.t("people.birthday_gift", task=gift.name)
        found.append(Plan("birthday", stage, person, facts, f"birthday|{person.name}|{when}|{stage}", fallback,
                          buttons=buttons, offer=offer))
    return found


def recall(person: Person, today: date, open_tasks: list[tasks.Task], links: dict, skip: set[str]) -> list[str]:
    """What is worth recalling about a person, as facts for the model: the stories, the recent appearances, the
    open tasks about the person, a near birthday. Empty when there is nothing."""
    found = []
    stories = section(person.body, "Stories")
    if stories:
        found.append(f"- The page has {len(stories)} story line(s) under Stories.")
    oldest = (today - timedelta(days=RECENT_DAYS)).isoformat()
    dates = [m[0] for line in section(person.body, "Appearances") if (m := re.search(r"\d{4}-\d{2}-\d{2}", line))]
    recent = [d for d in dates if d >= oldest]
    if recent:
        found.append(f"- Recent appearances: {len(recent)}, the last on {max(recent)}.")
    about = [t.name for t in open_tasks if t.name not in skip and links.get(t.name, set()) & person.names]
    if about:
        found.append("- Open tasks about the person: " + "; ".join(f'"{name}"' for name in about[:3]) + ".")
    birthday = person.birthday.next(today) if person.birthday else None
    if birthday and 0 < (birthday - today).days <= NEAR_BIRTHDAY:
        found.append(f"- Birthday: {_english(birthday)}, in {(birthday - today).days} days.")
    return found


@dataclass
class Encounter:
    person: Person
    what: list[str]  # the lines of today about the person
    tasks: set[str]
    minute: int | None = None  # the earliest time written in them, in minutes of the day


def _readable(text: str) -> str:
    """A line of the week as plain words: links become their names, and the estimate goes."""
    text = week.GROUP.sub(lambda m: "" if week.minutes(m[1]) else m[0], text)
    return " ".join(re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m[2] or m[1], text).split())


def encounters(today: date, people: list[Person], open_tasks: list[tasks.Task], links: dict) -> list[Encounter]:
    """The people that the user meets or talks to today: the open lines of the card of today and the open tasks
    due or planned today that link a People page, in the line itself, in the task's fields or in its body."""
    names = {name: p for p in people for name in p.names}
    by_task = {t.name: t for t in open_tasks}
    found = week.read(today)
    card = found.day(today) if found else None
    lines = [(item.text, by_task.get(item.name)) for item in (card.items if card else []) if not item.done]
    listed = {task.name for _, task in lines if task}
    lines += [(t.name, t) for t in open_tasks if t.name not in listed and today in (t.due, t.planned)]
    people_today: dict[str, Encounter] = {}
    for text, task in lines:
        targets = {link.lower() for link in notes.links(text)} | (links.get(task.name, set()) if task else set())
        minute = event_time(text)
        if minute is None and task:
            minute = event_time(task.name)
            minute = event_time(notes.read(task.path)[1][:300]) if minute is None else minute
        for target in targets:
            person = names.get(target)
            if not person:
                continue
            e = people_today.setdefault(person.name, Encounter(person, [], set()))
            if _readable(text) not in e.what:
                e.what.append(_readable(text))
            if task:
                e.tasks.add(task.name)
            if minute is not None and (e.minute is None or minute < e.minute):
                e.minute = minute
    return list(people_today.values())


def briefings(moment: datetime, people: list[Person], history: list[messenger.Message], open_tasks: list[tasks.Task],
              links: dict) -> list[Plan]:
    """A person met today, with something worth recalling: about 2 hours before the time of the encounter, never
    after it, or after the morning summary when it has no time. Once a day for each person."""
    today, found = moment.date(), []
    for e in encounters(today, people, open_tasks, links):
        if _sent(history, "briefing", e.person, "", today):
            continue
        if e.minute is not None:
            event = moment.replace(hour=e.minute // 60, minute=e.minute % 60, second=0, microsecond=0)
            if not event - BRIEFING_BEFORE <= moment < event:
                continue
        worth = recall(e.person, today, open_tasks, links, e.tasks)
        if not worth:
            continue
        facts = _about(e.person) + [f"- Today: {what}" for what in e.what]
        if e.minute is not None:
            facts.append(f"- Time: {e.minute // 60:02d}:{e.minute % 60:02d}")
        found.append(Plan("briefing", "", e.person, facts + worth, f"briefing|{e.person.name}|{today}",
                          timed=e.minute is not None))
    return found


def reconnects(moment: datetime, people: list[Person], history: list[messenger.Message], open_tasks: list[tasks.Task],
               links: dict, state: dict) -> list[Plan]:
    """People with `keep_in_touch` whose last contact is older than that, the most overdue first. With no
    `last_contact`, code counts from the day it first saw `keep_in_touch`. None while a task about the person is
    planned, for 7 days after an offer, 14 after "Not now", and none when one went out today."""
    today = moment.date()
    if moment < _morning(moment) + RECONNECT_FROM or any(m.kind == "reconnect" and m.when.date() == today for m in history):
        return []
    found = []
    for person in people:
        every = person.cadence
        if not every:
            continue
        mine = state["reconnect"].get(person.name, {})
        last = person.last_contact
        since = min(last, today) if last else _date(mine.get("since"))
        if since is None or (today - since).days < every or mine.get("snooze", "") > today.isoformat():
            continue
        asked = [m.when.date() for m in history if m.kind == "reconnect" and m.source == person.link]
        if asked and (today - max(asked)).days < ASK_AGAIN:
            continue
        if any(links.get(t.name, set()) & person.names and ((t.due and t.due >= today) or planned_day(t, today))
               for t in open_tasks):
            continue  # they meet or talk soon already
        days = (today - since).days
        sunday = today + timedelta(days=6 - today.weekday() or 7)
        day = best_day(today + timedelta(days=1), sunday, CALL_ESTIMATE)
        facts = _about(person) + [f"- Keep in touch: {person.field('keep_in_touch')}, every {every} days."]
        if last:
            facts.append(f"- Last contact: {last.isoformat()}, {days} days ago.")
            fallback = i18n.t("people.reconnect", days=days, name=person.name, day=day_words(day))
        else:
            facts.append(f"- Last contact: unknown. The user asked to keep in touch {days} days ago, "
                         "and told of no contact since.")
            fallback = i18n.t("people.reconnect_new", name=person.name, day=day_words(day))
        facts.append(f"- Day to offer for a call: {_english(day)}.")
        buttons = [(i18n.t("people.week_button"), "week"), (i18n.t("people.later_button"), "later")]
        offer = {"kind": "reconnect", "person": person.name, "task": i18n.t("people.call_task", name=person.name),
                 "day": day.isoformat()}
        found.append((days / every, Plan("reconnect", "", person, facts, f"reconnect|{person.name}|{today}", fallback,
                                         buttons=buttons, offer=offer)))
    found.sort(key=lambda pair: -pair[0])
    return [plan for _, plan in found]


# ---- the state between runs -------------------------------------------------------------------------------

def _state_path() -> Path:
    return config.STATE / "people.json"


def _presses() -> Path:
    return config.STATE / "people"


def _load() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    for key in ("offers", "reconnect", "calls", "tries"):
        data.setdefault(key, {})
    return data


def _save(state: dict) -> None:
    """Whole, or not there: the bot reads it without the lock."""
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix(".tmp").write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(path.with_suffix(".tmp"), path)


def _prune(state: dict, today: date) -> None:
    """Forgets the buttons of old messages and the tries of past days."""
    oldest = (today - timedelta(days=OFFER_DAYS)).isoformat()
    state["offers"] = {k: v for k, v in state["offers"].items() if v.get("sent", "") >= oldest}
    week_ago = (today - timedelta(days=WEEK_BEFORE + 1)).isoformat()
    state["tries"] = {k: v for k, v in state["tries"].items()
                      if (m := re.search(r"\d{4}-\d{2}-\d{2}", k)) and m[0] >= week_ago}


# ---- the Messenger ----------------------------------------------------------------------------------------

@dataclass
class Ask:
    """A message about a person for the model to write, with what code already chose for it."""
    kind: str
    stage: str
    person: str
    links: list[str]  # the names of the person, to check muted and snoozed topics again before it goes
    request: str
    message_id: str
    key: str
    fallback: str
    urgent: bool
    buttons: list
    offer: dict


def prepare(moment: datetime) -> Ask | None:
    """The Messenger calls this with the lock, when nothing more urgent goes out. The message about a person that
    fits now, if any, in this order: a briefing before a time, a birthday today, a briefing, a birthday in a week,
    a friend to call. All wait for the morning summary, and come at least an hour apart, except a briefing before
    a time. Returns the request that `converse` runs after the lock is free."""
    history = messenger.messages()
    people = everyone()
    if not people or not after_summary(moment, history):
        return None
    today, state = moment.date(), _load()
    open_tasks = [t for t in tasks.all_tasks() if not t.done]
    links = {t.name: _links(t) for t in open_tasks}
    full = messenger.over_limit(moment, history)
    recent = any(m.kind in messenger.PEOPLE and moment - m.when < GAP for m in history)
    late = moment.hour * 60 + moment.minute >= companion.window()[1]
    plans = (briefings(moment, people, history, open_tasks, links) + birthdays(today, people, history, open_tasks, links)
             + reconnects(moment, people, history, open_tasks, links, state))
    for plan in sorted(plans, key=lambda p: p.priority):
        if state["tries"].get(plan.key, 0) >= MAX_TRIES:
            continue
        if (full and not plan.urgent) or (recent and not plan.timed) or (late and not (plan.urgent or plan.timed)):
            continue
        if messenger.silenced(sorted(plan.person.names), plan.person.name, today):
            continue
        state["tries"][plan.key] = state["tries"].get(plan.key, 0) + 1
        _save(state)
        kind = "birthday-today" if plan.stage == "today" else plan.kind
        page = plan.person.path.read_text(encoding="utf-8")[:NOTE_ROOM]
        request = llm.prompt("people", kind=kind, person=plan.person.name, facts="\n".join(plan.facts), note=page,
                             date_example=today.strftime(i18n.t("reminder.date_format")))
        return Ask(plan.kind, plan.stage, plan.person.name, sorted(plan.person.names), request, uuid.uuid4().hex[:6],
                   plan.key, plan.fallback, plan.urgent, plan.buttons, plan.offer)
    return None


def parse(text: str) -> dict | None:
    """The model's answer: a JSON object with text, suggestion and gift. None when it is not usable."""
    found = re.search(r"\{.*\}", text or "", re.S)
    if not found:
        return None
    try:
        data = json.loads(found[0])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    clean = {key: " ".join(str(data.get(key) or "").split()) for key in ("suggestion", "gift")}
    lines = [line.strip() for line in str(data.get("text") or "").splitlines() if line.strip()]
    return {"text": "\n".join(lines)[:600], "suggestion": clean["suggestion"][:400], "gift": clean["gift"][:80]}


def message_text(ask: Ask, text: str, written: dict | None) -> tuple[str, str | None]:
    """The text of the message, and its Telegram HTML when it has a message to send to the person: that goes as
    code, which Telegram copies with one tap."""
    suggestion = (written or {}).get("suggestion", "") if ask.stage == "today" else ""
    if not suggestion:
        return text, None
    label = i18n.t("people.suggestion", name=ask.person)
    rich = (f"{html.escape(text, quote=False)}\n\n{html.escape(label, quote=False)}\n"
            f"<code>{html.escape(suggestion, quote=False)}</code>")
    return f"{text}\n\n{label}\n{suggestion}", rich


def converse(ask: Ask) -> str | None:
    """Runs the model WITHOUT the vault lock, as the Companion does. Then, with the lock, it checks the moment
    again (quiet hours, the daily limit, a muted or snoozed person), sends, records it in Messages, and keeps the
    buttons. When the model fails, code writes the text; a briefing then stays silent."""
    answer = llm.run(ask.request, "companion", max_turns=1, timeout=300)
    written = None if answer.error else parse(answer.text)
    text = (written or {}).get("text") or ask.fallback
    if not text:
        if written is not None:  # the model found nothing worth saying: no new run for it
            state = _load()
            state["tries"][ask.key] = MAX_TRIES
            _save(state)
        log.info("people: nothing to say about %s now", ask.person)
        return None
    with vault.lock():
        vault.pull()
        moment = messenger.now()
        history = messenger.messages()
        if messenger.quiet(moment) or (not ask.urgent and messenger.over_limit(moment, history)):
            log.info("people: the moment passed while the model wrote, so nothing goes out")
            return None
        if messenger.silenced(ask.links, text, moment.date()):
            log.info("people: %s is muted or snoozed, so nothing goes out", ask.person)
            return None
        plain, rich = message_text(ask, text, written)
        c = messenger.Candidate(ask.kind, f"[[{ask.person}]]", plain, ask.urgent, "normal", stage=ask.stage)
        buttons = [(label, f"ppl|{ask.message_id}|{action}") for label, action in ask.buttons]
        if buttons:  # kept before the message goes, so that even the fastest tap finds it
            state = _load()
            idea = (written or {}).get("gift", "")
            offer = dict(ask.offer, sent=moment.date().isoformat())
            state["offers"][ask.message_id] = offer | ({"idea": idea} if idea else {})
            _save(state)
        if not messenger.send(c, plain, buttons, moment, ask.message_id, rich):
            return None
        done = f"{ask.kind} about [[{ask.person}]]"
        vault.publish(f"messenger: {done}")
    return done


# ---- the buttons --------------------------------------------------------------------------------------------

def _label(offer: dict, action: str, today: date) -> str:
    """What the message shows under it after a tap."""
    day = max(_date(offer.get("day")) or today, today)
    if action in ("gift", "week"):
        return i18n.t("people.gift_planned" if action == "gift" else "people.call_planned", task=offer.get("task", ""),
                      day=day_words(day))
    if action == "later":
        return i18n.t("people.later_done", days=SNOOZE_DAYS)
    return i18n.t("people.sent_done" if action == "sent" else "people.sorted_done")


def press(message_id: str, action: str) -> str | None:
    """A button under a message about a person, for the bot: no lock and no model. The press waits in a small
    file until the vault is free (`apply_now`, or the next Messenger run), and it counts as acted in Messages.
    Returns what the message shows under it, or None for an old message or one that got its answer already."""
    if not re.fullmatch(r"\w{1,16}", message_id or ""):
        return None
    offer = _load()["offers"].get(message_id)
    target = _presses() / f"{message_id}.json"
    if not offer or offer.get("done") or target.exists() or action not in ACTIONS.get(offer.get("kind"), ()):
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.with_suffix(".tmp").write_text(json.dumps({"id": message_id, "action": action}), encoding="utf-8")
    os.replace(target.with_suffix(".tmp"), target)
    messenger.set_reaction(message_id, "acted")
    return _label(offer, action, messenger.now().date())


def _from_template(fields: dict, body: str) -> str:
    """A task note from the Task template of the vault, with these fields."""
    template = config.SYSTEM / "_Templates" / "Task.md"
    text = template.read_text(encoding="utf-8").replace("\r\n", "\n") if template.exists() else ""
    end = text.find("\n---", 4)
    lines, rest = (text[4:end].split("\n"), text[end + 4:].strip()) if text.startswith("---\n") and end != -1 else ([], "")
    for key, value in fields.items():
        at = next((i for i, line in enumerate(lines) if line.split(":", 1)[0].strip() == key), None)
        if at is None:
            lines.append(f"{key}: {value}")
        else:
            lines[at] = f"{key}: {value}"
    return "---\n" + "\n".join(lines) + "\n---\n" + "\n".join(part for part in (rest, body.strip()) if part) + "\n"


def plan_task(name: str, day: date, estimate: str, person: str, body: str, today: date) -> Path:
    """Plans a task on a day. An open task with this name gets `planned`, unless it has a day already; a done one
    opens again; a new one comes from the Task template. Code puts it in the week on that day."""
    found = next((t for t in tasks.all_tasks() if t.name == name), None)
    if found:
        if found.done:
            notes.set_field(found.path, "done", "false")
        if not (found.planned and found.planned >= today):
            notes.set_field(found.path, "planned", day.isoformat())
        return found.path
    path = config.TASKS / f"{name}.md"
    fields = {"type": "task", "done": "false", "estimate": estimate, "planned": day.isoformat(),
              "subject": f'"[[{person}]]"'}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_from_template(fields, body).encode("utf-8"))
    return path


def touch(name: str, day: date) -> bool:
    """Writes `last_contact` on the page of a person, when the day is later than the one there."""
    person = find(name)
    if not person or (person.last_contact and person.last_contact >= day):
        return False
    notes.set_field(person.path, "last_contact", day.isoformat())
    return True


def _act(offer: dict, action: str, today: date, state: dict) -> str:
    """Does what one button says. Returns it in a few words, for the commit."""
    person = offer.get("person", "")
    if action in ("gift", "week"):
        day = max(_date(offer.get("day")) or today, today)
        if action == "gift":
            when = _date(offer.get("date"))
            lines = [i18n.t("people.gift_body", person=f"[[{person}]]", date=day_words(when) if when else "")]
            lines += [i18n.t("people.gift_idea", idea=offer["idea"])] if offer.get("idea") else []
            plan_task(offer["task"], day, GIFT_ESTIMATE, person, "\n".join(lines), today)
        else:
            plan_task(offer["task"], day, CALL_ESTIMATE, person, i18n.t("people.call_body", person=f"[[{person}]]"), today)
            state["calls"][offer["task"]] = person
        return f"{offer['task']} on {day}"
    if action == "later":
        state["reconnect"].setdefault(person, {})["snooze"] = (today + timedelta(days=SNOOZE_DAYS)).isoformat()
        return f"no call offer about {person} for {SNOOZE_DAYS} days"
    if action == "sent" and touch(person, today):
        return f"{person}: last contact {today}"
    return ""


def _apply(state: dict, today: date) -> list[str]:
    """The buttons that wait in their files, oldest first. Each message acts once."""
    done = []
    for path in sorted(_presses().glob("*.json")) if _presses().exists() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            message_id, action = str(data["id"]), str(data["action"])
        except (OSError, ValueError, KeyError):
            path.unlink(missing_ok=True)
            continue
        offer = state["offers"].get(message_id)
        if offer and not offer.get("done") and action in ACTIONS.get(offer.get("kind"), ()):
            done.append(_act(offer, action, today, state))
            offer["done"] = action
        path.unlink(missing_ok=True)
    return [d for d in done if d]


def _calls_done(state: dict, today: date) -> list[str]:
    """A call task that code made is done: the person gets `last_contact`."""
    done, by_name = [], {t.name: t for t in tasks.all_tasks()}
    for name, person in list(state["calls"].items()):
        task = by_name.get(name)
        if task and not task.done:
            continue
        if task and touch(person, today):
            done.append(f"{person}: last contact {today}")
        del state["calls"][name]
    return done


def update(today: date) -> list[str]:
    """The Messenger calls this with the lock, on each run, after the tasks sync: the buttons pressed under the
    messages about people, the call tasks that were done, and the day code starts to count for a person with
    `keep_in_touch` and no `last_contact`. Returns what changed in the vault."""
    state = _load()
    before = json.dumps(state, sort_keys=True)
    done = _apply(state, today) + _calls_done(state, today)
    for person in everyone():
        if person.cadence and not person.last_contact:
            state["reconnect"].setdefault(person.name, {}).setdefault("since", today.isoformat())
    _prune(state, today)
    if json.dumps(state, sort_keys=True) != before:
        _save(state)
    return done


def apply_now() -> list[str]:
    """For the bot, right after a press: applies it at once when the vault is free, and puts a planned task in the
    week. When an agent holds the vault, the next Messenger run does it."""
    if not _presses().exists() or not any(_presses().glob("*.json")):
        return []
    try:
        with vault.lock(wait=False):
            vault.pull()
            today = messenger.now().date()
            done = update(today)
            if done:
                tasks.sync(today)
                week.reschedule(today)
                vault.publish("people: " + "; ".join(done))
    except vault.Busy:
        log.info("people: the vault is busy, the next Messenger run applies the press")
        return []
    return done
