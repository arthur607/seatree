"""The Companion: the active side of Seahorse. It starts conversations with the user at drawn moments of the day.

The code decides WHEN: each morning it draws how many conversations the day will have and at what times, so it
feels like a friend and not an alarm clock, plus one check-in at night about the day. The model decides WHAT:
it reads Identity, tastes, projects and what came in lately, and it searches the web so it does not make
things up.
"""
import json
import logging
import random
import re
import uuid
from datetime import datetime, timedelta

from . import config, llm, messenger, tasks

log = logging.getLogger(__name__)

# How many conversations in a day, and the weight of each count in the draw. Two or three is the most common.
COUNTS, WEIGHTS = (1, 2, 3, 4), (1, 4, 4, 2)
MIN_GAP = 100  # minutes between two conversations
CHECKIN_FROM, CHECKIN_UNTIL = 20 * 60 + 30, 22 * 60 + 45
# What to bring up in a normal conversation, and the weight of each kind in the draw. News from outside weighs most.
FOCUS = {"release": 4, "news": 2, "question": 2, "revival": 2, "recommendation": 1, "curiosity": 1, "research": 1, "connection": 1}
# A task that moved to another day this many times is stuck: instead of another push, the Companion asks why.
STUCK_AFTER = 3


def _hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def window() -> tuple[int, int]:
    """The minutes of the day when it may talk: from 45 minutes after the morning summary to 30 minutes before
    the quiet hours."""
    hours, minutes = map(int, config.SCHEDULE["morning"].split(":"))
    first = hours * 60 + minutes + 45
    last = (config.QUIET_FROM or 24 * 60) - 30
    return first, last


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
        _, last = window()
        checkin = luck.randint(min(CHECKIN_FROM, last - 15), min(CHECKIN_UNTIL, last))
        plan = {"day": f"{moment:%Y-%m-%d}", "times": draw_times(moment, luck), "used": 0,
                "checkin": _hhmm(checkin), "checkin_done": False}
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
    """Open tasks that moved day after day, when the Companion did not ask about them in the last week."""
    if any(m.kind == "unblock" and moment - m.when < timedelta(days=7) for m in history):
        return []  # one such conversation a week, about all the stuck tasks together
    return [t for t in tasks.all_tasks() if not t.done and t.count("moved") >= STUCK_AFTER]


def pick_focus(history: list[messenger.Message], luck: random.Random | None = None) -> str:
    """Draws the kind of the conversation, except the last two used. The model may change it."""
    luck = luck or random.Random()
    recent = [m.kind for m in history if m.kind in messenger.CONVERSATION][-2:]
    options = {k: v for k, v in FOCUS.items() if k not in recent}
    return luck.choices(list(options), list(options.values()))[0]


def history_text(lines: list[str]) -> str:
    """The last conversations, with the reaction to each, so the Companion varies and learns."""
    recent = [line for line in lines if (m := messenger.LINE.match(line)) and m[2] in messenger.CONVERSATION | {"delivery"}]
    return "\n".join(recent[-30:]) or "none yet"


def parse(text: str) -> dict | None:
    """The model's answer: a JSON object with kind, source, text and buttons. None if it is not usable."""
    found = re.search(r"\{.*\}", text, re.S)
    if not found:
        return None
    try:
        data = json.loads(found[0])
    except json.JSONDecodeError:
        return None
    kind, message = str(data.get("kind", "")).strip(), str(data.get("text", "")).strip()
    if kind not in messenger.CONVERSATION or not message:
        return None
    source = str(data.get("source", "")).strip()
    if not re.fullmatch(r"\[\[[^\]]+\]\]", source):
        source = "[[_Seahorse]]"
    # Buttons are the exception: only for an offer that one tap fully answers. A question gets text or voice.
    buttons = [str(b).strip()[:32] for b in data.get("buttons", []) if str(b).strip()][:2]
    return {"kind": kind, "source": source, "text": message[:900], "buttons": buttons}


def talk(moment: datetime) -> str | None:
    """The Messenger calls this, with the lock. At a drawn time, it sends what the other agents left in the queue
    or, with nothing there, asks the model for a new conversation."""
    what = turn(moment)
    if not what:
        return None
    message_id = uuid.uuid4().hex[:6]
    history = messenger.messages()
    queued = messenger.from_queue(moment, deliveries=False) if what == "conversation" else []
    if queued:
        c = queued[0]
        sent = messenger.send(c, c.text, messenger.conversation_buttons(message_id, []), moment, message_id)
        return f"{c.kind} about {c.source}" if sent else None
    blocked = stuck(history, moment) if what == "conversation" else []
    focus = "checkin" if what == "checkin" else "unblock" if blocked else pick_focus(history)
    listing = "\n".join(f"- [[{t.name}]] · moved to another day {t.count('moved')} times" for t in blocked) or "none"
    from . import agents  # imported here: agents imports this module through the Messenger
    lines = config.MESSAGES.read_text(encoding="utf-8").splitlines() if config.MESSAGES.exists() else []
    answer = llm.run(agents.request("companion", history=history_text(lines), focus=focus, stuck=listing),
                     "companion", max_turns=60, timeout=900, web=True)
    conversation = None if answer.error else parse(answer.text)
    if not conversation:
        log.info("companion: nothing to say now (%s)", answer.text[:120].replace("\n", " "))
        return None
    c = messenger.Candidate(conversation["kind"], conversation["source"], conversation["text"], False, "normal")
    buttons = messenger.conversation_buttons(message_id, conversation["buttons"])
    return f"{c.kind} about {c.source}" if messenger.send(c, c.text, buttons, moment, message_id) else None
