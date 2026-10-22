"""The first conversation: a few questions to get to know the person, one at a time, right after the setup.

Each answer is a normal capture with the question as its context (`in_reply_to`), so the Integrator files it in
the right area: the work and the routine in Identity, the people in Memory, the plans and tasks in Intention,
the tastes in Identity. `/intro` asks them all again.

How the conversation moves on:
- An answer is a reply to the question, or the messages sent after it. A person may answer in several messages,
  so the bot waits until they stop writing for ANSWER_PAUSE seconds before it asks the next question. One answer
  moves exactly one question on.
- A question from the person, such as "what is this for?", gets its answer and moves nothing.
- "Skip" goes to the next question at once, and "Stop" ends the conversation. A button acts only on its own
  question: an old or a double press does nothing.
- A question that waits more than a day ends the conversation, so a later message is never read as an old answer.
This module only keeps the state and sends the questions. The bot decides what is an answer.
"""
import json
import threading
import time

from . import config, i18n, telegram_api

STATE = config.STATE / "intro.json"
WAIT_HOURS = 24
ANSWER_PAUSE = 40  # seconds without a new message after an answer, before the next question
SKIP = "intro|skip|"
STOP = "intro|stop|"
_lock = threading.Lock()  # the bot calls this module from several threads


def questions() -> list[str]:
    return i18n.words("intro.questions")


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def current() -> dict | None:
    """The question that waits for an answer: its step, text, time and Telegram message. None when the first
    conversation is over, or when the question waited too long."""
    state = _load()
    if state.get("done") or "question" not in state:
        return None
    if time.time() - state.get("asked_at", 0) > WAIT_HOURS * 3600:
        return None
    return state


def waiting() -> str | None:
    """The text of the question that waits for an answer, or None."""
    state = current()
    return state["question"] if state else None


def _ask(step: int) -> None:
    all_questions = questions()
    text = i18n.t("intro.counter", n=step + 1, total=len(all_questions)) + "\n" + all_questions[step]
    message = telegram_api.send(text, [(i18n.t("intro.skip"), f"{SKIP}{step}"), (i18n.t("intro.stop"), f"{STOP}{step}")])
    _save({"step": step, "question": all_questions[step], "asked_at": time.time(), "message": message})


def start() -> None:
    """Says what comes, and asks the first question."""
    with _lock:
        telegram_api.send(i18n.t("intro.hello", total=len(questions())))
        _ask(0)


def _is_this(state: dict | None, step: int, message: int | None) -> bool:
    """True when `step`, and the Telegram message if known, are the question that waits."""
    if not state or state.get("step") != step:
        return False
    return message is None or not state.get("message") or state["message"] == message


def advance(step: int, message: int | None = None) -> dict | None:
    """After the answer to question `step`, or a skip: the next question, or the end of the first conversation.

    Returns the state of the question that got its answer, so the bot can take away its buttons. Returns None and
    does nothing when `step` is not the question that waits: an old or a double press, or a conversation that is
    over. `message` is the Telegram message of the button, to tell apart the same step of an earlier /intro."""
    with _lock:
        state = current()
        if not _is_this(state, step, message):
            return None
        if step + 1 < len(questions()):
            _ask(step + 1)
        else:
            _save({"done": True})
            telegram_api.send(i18n.t("intro.done"))
        return state


def stop(step: int, message: int | None = None) -> bool:
    """The person does not want more questions now. False when `step` is not the question that waits."""
    with _lock:
        if not _is_this(current(), step, message):
            return False
        _save({"done": True, "stopped": True})
        telegram_api.send(i18n.t("intro.stopped"))
        return True


def next_question() -> None:
    """Moves on from the question that waits, whatever its step."""
    advance(_load().get("step", -1))
