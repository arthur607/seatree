"""The first conversation: a few questions to get to know the person, one at a time, right after the setup.

Each answer is a normal capture with the question as its context (`in_reply_to`), so the Integrator files it in
the right area: the work and the routine in Identity, the people in Memory, the plans and tasks in Intention,
the tastes in Identity. The person can skip a question, and `/intro` asks them all again. A question that waits
more than a day ends the conversation, so a later message is never read as an old answer.
"""
import json
import time

from . import config, i18n, telegram_api

STATE = config.STATE / "intro.json"
WAIT_HOURS = 24
SKIP = "intro|skip|"


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


def waiting() -> str | None:
    """The question that waits for an answer, or None when the first conversation is over."""
    state = _load()
    if state.get("done") or "question" not in state:
        return None
    if time.time() - state.get("asked_at", 0) > WAIT_HOURS * 3600:
        return None
    return state["question"]


def _ask(step: int) -> None:
    all_questions = questions()
    text = i18n.t("intro.counter", n=step + 1, total=len(all_questions)) + "\n" + all_questions[step]
    telegram_api.send(text, [(i18n.t("intro.skip"), f"{SKIP}{step}")])
    _save({"step": step, "question": all_questions[step], "asked_at": time.time()})


def start() -> None:
    """Says what comes, and asks the first question."""
    telegram_api.send(i18n.t("intro.hello", total=len(questions())))
    _ask(0)


def next_question() -> None:
    """After an answer, or a skip: the next question, or the end of the first conversation."""
    state = _load()
    step = state.get("step", -1) + 1
    if step < len(questions()):
        _ask(step)
        return
    _save({"done": True})
    telegram_api.send(i18n.t("intro.done"))
