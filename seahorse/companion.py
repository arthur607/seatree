"""The Companion: the active side of Seahorse. It starts conversations with the user at drawn moments of the day.

The code decides WHEN: each morning it draws how many conversations the day will have and at what times, so it
feels like a friend and not an alarm clock, plus one check-in in the morning about yesterday. The model decides
WHAT: it reads Identity, tastes, projects and what came in lately, and it searches the web so it does not make
things up. A release, a piece of news or a curiosity must name the web pages that confirm it, or it is not sent.
The model runs without the vault lock: it only reads, and the captures do not wait for it.
"""
import html
import json
import logging
import random
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlparse

from . import config, i18n, llm, messenger, notes, tasks, vault

log = logging.getLogger(__name__)

# How many conversations in a day, and the weight of each count in the draw. Two or three is the most common.
COUNTS, WEIGHTS = (1, 2, 3, 4), (1, 4, 4, 2)
MIN_GAP = 100  # minutes between two conversations
# The day check-in asks about yesterday, in the morning: at a random moment in the first 75 minutes of the
# window, that is, from 45 minutes to 2 hours after the morning summary. No other conversation within an hour of it.
CHECKIN_SPREAD, CHECKIN_ROOM = 75, 60
# A check-in planned for a morning that is long gone (the bot was down) is skipped: yesterday is far by then.
CHECKIN_TOO_LATE = 3 * 60
# What to bring up in a normal conversation, and the weight of each kind in the draw. News from outside weighs most.
FOCUS = {"release": 4, "news": 2, "question": 2, "revival": 2, "recommendation": 1, "curiosity": 1, "research": 1, "connection": 1}
# A task that moved to another day this many times is stuck: instead of another push, the Companion asks why.
STUCK_AFTER = 3
# Days to wait after an unblock conversation: when it got an answer, when it was ignored, and ignored twice in a row.
UNBLOCK_WAIT = (7, 14, 30)
# Days to wait when the model had nothing good to say about the stuck tasks, so other kinds get their turn.
UNBLOCK_PAUSE = 7
# Kinds that code chooses for a turn of their own. The model writes the text; the kind stays. The messages about
# people (people.py) have turns of their own too: the Companion never sends one of those kinds.
FORCED = {"checkin", "unblock"} | messenger.PEOPLE
# Kinds that bring facts from outside: they must name the web pages that confirm them.
NEEDS_SOURCES = {"release", "news", "curiosity"}
# Kinds that never get a button: the user answers with a reply, by text or voice.
NO_BUTTONS = {"checkin", "question", "unblock", "news", "curiosity", "connection"}
URL = re.compile(r"https?://[^\s<>\"']+")


@dataclass
class Ask:
    """A conversation for the model to write, with what code already chose for it."""
    focus: str
    request: str
    message_id: str


def _hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def window() -> tuple[int, int]:
    """The minutes of the day when it may talk: from 45 minutes after the morning summary to 30 minutes before
    the quiet hours."""
    hours, minutes = map(int, config.SCHEDULE["morning"].split(":"))
    first = hours * 60 + minutes + 45
    if config.QUIET_FROM == config.QUIET_UNTIL:  # no quiet hours
        return first, 24 * 60 - 30
    # Quiet hours that start after midnight (such as 01:00) end the day at midnight.
    end = config.QUIET_FROM if config.QUIET_FROM > first else 24 * 60
    return first, end - 30


def draw_times(moment: datetime, luck: random.Random | None = None) -> list[str]:
    """Today's times, from now on. Fewer times when the day is near its end."""
    luck = luck or random.Random()
    first, last = window()
    start = max(moment.hour * 60 + moment.minute, first)
    count = luck.choices(COUNTS, WEIGHTS)[0]
    while count and (last - start) < (count - 1) * MIN_GAP:
        count -= 1
    if last <= start or not count:
        return []
    for _ in range(300):
        picked = sorted(luck.randint(start, last) for _ in range(count))
        if all(b - a >= MIN_GAP for a, b in zip(picked, picked[1:])):
            return [_hhmm(m) for m in picked]
    return [_hhmm(start)]


def _read_plan() -> dict:
    try:
        return json.loads(config.COMPANION_STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_plan(plan: dict) -> None:
    config.COMPANION_STATE.parent.mkdir(parents=True, exist_ok=True)
    config.COMPANION_STATE.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")


def _plan(moment: datetime) -> dict:
    plan = _read_plan()
    if plan.get("day") != f"{moment:%Y-%m-%d}":
        luck = random.Random()
        first, last = window()
        checkin = luck.randint(first, max(first, min(first + CHECKIN_SPREAD, last)))
        times = [t for t in draw_times(moment, luck) if abs(_minutes(t) - checkin) >= CHECKIN_ROOM]
        late = moment.hour * 60 + moment.minute > checkin + CHECKIN_TOO_LATE
        kept = {key: plan[key] for key in ("unblock_after",) if key in plan}  # what lasts more than a day
        plan = {"day": f"{moment:%Y-%m-%d}", "times": times, "used": 0,
                "checkin": _hhmm(checkin), "checkin_done": late, **kept}
        _save_plan(plan)
        log.info("companion: today %s, and the day check-in at %s", plan["times"], plan["checkin"])
    return plan


def turn(moment: datetime) -> str | None:
    """What fits now: "checkin", "conversation" or nothing. A used time is spent: a time that passed while the
    bot was down does not become a late conversation, and a failure does not repeat every ten minutes."""
    plan, now = _plan(moment), f"{moment:%H:%M}"
    if plan.get("checkin") and not plan.get("checkin_done") and now >= plan["checkin"]:
        plan["checkin_done"] = True
        _save_plan(plan)
        return "checkin"
    passed = sum(1 for t in plan["times"] if t <= now)
    if passed <= plan["used"]:
        return None
    plan["used"] = passed
    _save_plan(plan)
    return "conversation"


def stuck(history: list[messenger.Message], moment: datetime) -> list[tasks.Task]:
    """Open tasks that moved day after day. One conversation about all of them together, at most once a week; after
    the user ignores it, the next one waits 14 days, and 30 after two in a row."""
    asked = [m for m in history if m.kind == "unblock"]
    ignored = 0
    for m in reversed(asked):
        if m.reaction != "ignored":
            break
        ignored += 1
    if asked and moment - asked[-1].when < timedelta(days=UNBLOCK_WAIT[min(ignored, len(UNBLOCK_WAIT) - 1)]):
        return []
    if f"{moment:%Y-%m-%d}" < _read_plan().get("unblock_after", ""):
        return []  # the model had nothing good to say about them a few days ago
    return [t for t in tasks.all_tasks() if not t.done and t.count("moved") >= STUCK_AFTER]


def _pause_unblock(moment: datetime) -> None:
    plan = _read_plan()
    plan["unblock_after"] = f"{moment + timedelta(days=UNBLOCK_PAUSE):%Y-%m-%d}"
    _save_plan(plan)


def pick_focus(history: list[messenger.Message], luck: random.Random | None = None) -> str:
    """Draws the kind of the conversation, except the last two used. The model may change it."""
    luck = luck or random.Random()
    recent = [m.kind for m in history if m.kind in messenger.CONVERSATION][-2:]
    options = {k: v for k, v in FOCUS.items() if k not in recent}
    return luck.choices(list(options), list(options.values()))[0]


def history_text(lines: list[str]) -> str:
    """The last conversations, with the reaction to each, so the Companion varies and learns. The `now` lines
    say which tasks the user started and finished."""
    recent = [line for line in lines if (m := messenger.LINE.match(line)) and m[2] in messenger.CONVERSATION | {"delivery", "now"}]
    return "\n".join(recent[-30:]) or "none yet"


def parse(text: str, focus: str | None = None) -> dict | None:
    """The model's answer: a JSON object with kind, source, text, buttons and sources. None if it is not usable.
    - A check-in or an unblock turn keeps its kind, whatever kind the model wrote; and these two kinds come only in
      their own turns.
    - A release, news or curiosity with no web page as its source is dropped: an invented fact is the worst failure."""
    found = re.search(r"\{.*\}", text, re.S)
    if not found:
        return None
    try:
        data = json.loads(found[0])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    kind, message = str(data.get("kind", "")).strip(), str(data.get("text", "")).strip()
    if kind == "none" or not message:
        return None
    if focus in FORCED:
        kind = focus
    elif kind in FORCED:
        return None
    if kind not in messenger.CONVERSATION:
        return None
    raw = data.get("sources") or []
    sources = [s for s in (str(x).strip() for x in ([raw] if isinstance(raw, str) else raw)) if URL.fullmatch(s)]
    if kind in NEEDS_SOURCES and not sources:
        log.info("companion: a %s with no source is not sent", kind)
        return None
    source = str(data.get("source", "")).strip()
    if not re.fullmatch(r"\[\[[^\]]+\]\]", source):
        source = "[[_Seahorse]]"
    # Buttons are the exception: only for an offer that one tap fully answers. A question gets text or voice.
    buttons = [] if kind in NO_BUTTONS else [str(b).strip()[:32] for b in data.get("buttons", []) if str(b).strip()][:2]
    return {"kind": kind, "source": source, "text": message[:900], "buttons": buttons, "sources": sources[:3]}


def message_html(conversation: dict) -> str | None:
    """A conversation with a source as Telegram HTML: the text, then its first source as a link,
    "🔗 Source: imdb.com". None when it has no source: then it goes as plain text."""
    if not conversation["sources"]:
        return None
    url = conversation["sources"][0]
    site = urlparse(url).netloc.removeprefix("www.") or url
    link = f'<a href="{html.escape(url)}">{html.escape(site, quote=False)}</a>'
    line = html.escape(i18n.t("companion.source", site="\x00"), quote=False).replace("\x00", link)
    return f"{html.escape(conversation['text'], quote=False)}\n{line}"


def prepare(moment: datetime) -> tuple[str | None, Ask | None]:
    """The Messenger calls this with the lock. At a drawn time, it sends what the other agents left in the queue or,
    with nothing there, prepares the request for a new conversation, which `converse` runs after the lock is free.
    Returns what was sent, and the conversation to write."""
    history = messenger.messages()
    if messenger.over_limit(moment, history):
        return None, None
    what = turn(moment)
    if not what:
        return None, None
    message_id = uuid.uuid4().hex[:6]
    queued = messenger.from_queue(moment, deliveries=False) if what == "conversation" else []
    if queued:
        c = queued[0]
        sent = messenger.send(c, c.text, messenger.conversation_buttons(message_id, []), moment, message_id)
        return (f"{c.kind} about {c.source}" if sent else None), None
    blocked = stuck(history, moment) if what == "conversation" else []
    focus = "checkin" if what == "checkin" else "unblock" if blocked else pick_focus(history)
    listing = "\n".join(f"- [[{t.name}]] · moved to another day {t.count('moved')} times" for t in blocked) or "none"
    from . import agents  # imported here: agents imports this module through the Messenger
    lines = config.MESSAGES.read_text(encoding="utf-8").splitlines() if config.MESSAGES.exists() else []
    request = agents.request("companion", history=history_text(lines), focus=focus, stuck=listing)
    return None, Ask(focus, request, message_id)


def converse(ask: Ask) -> str | None:
    """Runs the model WITHOUT the vault lock: it only reads, and it can take minutes. Then, with the lock, it reads
    the records again and checks the moment again (quiet hours, the daily limit, a muted or snoozed topic) before
    it sends and records."""
    answer = llm.run(ask.request, "companion", max_turns=60, timeout=900, web=True)
    conversation = None if answer.error else parse(answer.text, ask.focus)
    if not conversation:
        log.info("companion: nothing to say now (%s)", answer.text[:120].replace("\n", " "))
        if ask.focus == "unblock" and not answer.error:
            _pause_unblock(messenger.now())
        return None
    with vault.lock():
        vault.pull()
        moment = messenger.now()
        if messenger.quiet(moment) or messenger.over_limit(moment, messenger.messages()):
            log.info("companion: the moment passed while the model wrote, so nothing goes out")
            return None
        if messenger.silenced(notes.links(conversation["source"]), conversation["text"], moment.date()):
            log.info("companion: %s is muted or snoozed, so nothing goes out", conversation["source"])
            return None
        c = messenger.Candidate(conversation["kind"], conversation["source"], conversation["text"], False, "normal")
        buttons = messenger.conversation_buttons(ask.message_id, conversation["buttons"])
        sent = messenger.send(c, c.text, buttons, moment, ask.message_id, message_html(conversation))
        done = f"{c.kind} about {c.source}" if sent else None
        if sent:
            vault.publish(f"messenger: {done}")
    return done
