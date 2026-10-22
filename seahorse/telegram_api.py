"""Send and edit Telegram messages over the HTTP API. The agents and the timers use it, outside the bot."""
import html
import logging
import threading
from pathlib import Path

import requests

from . import config, i18n

log = logging.getLogger(__name__)
LIMIT = 4000
# The last texts sent, by message id, so the bot knows what a later message of the user answers.
_sent: dict[int, str] = {}
_sent_lock = threading.Lock()
SENT_KEPT = 50


def _url(method: str) -> str:
    return f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/{method}"


def _call(method: str, body: dict) -> dict | None:
    r = requests.post(_url(method), json=body, timeout=30)
    if not r.ok:
        log.error("telegram %s: %s", method, r.text[:300])
        return None
    return r.json().get("result")


def rows(buttons: list) -> list[list]:
    """Two buttons fit side by side. With more, two per row, so the labels are not cut."""
    return [buttons] if len(buttons) <= 2 else [buttons[i:i + 2] for i in range(0, len(buttons), 2)]


def keyboard(buttons: list[tuple[str, str]] | None) -> dict | None:
    if not buttons:
        return None
    return {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in row] for row in rows(buttons)]}


def capture_buttons(engram: str, channel: str, correction: bool, result: str | None = None) -> list[tuple[str, str]]:
    """The buttons under a capture confirmation. Without a result the Integrator is still at work: there is
    nothing to fix yet, so only a voice note gets the button to read the transcript.
    A result that starts with "!" is a task that does not fit this week: the bot asks what to do."""
    if result and result.startswith("!"):
        return [(i18n.t("button.put_in_week"), f"eng|week|{engram}"), (i18n.t("button.backlog"), f"eng|backlog|{engram}")]
    if correction:
        return []
    buttons = [(i18n.t("button.read"), f"eng|read|{engram}")] if channel == "telegram-voice" else []
    if result is None:
        return buttons
    return buttons + [(i18n.t("button.fix"), f"eng|fix|{engram}")]


def capture_text(channel: str, correction: bool, result: str | None) -> str:
    """The confirmation text. Without a result it says it is integrating. With one, it says what was done."""
    title = i18n.t("capture.correction" if correction else "capture.voice" if channel == "telegram-voice" else "capture.saved")
    if result is None:
        return i18n.t("capture.working", title=title)
    if result.startswith("?"):
        return i18n.t("capture.doubt", title=title, question=result[1:].strip())
    if result.startswith("!"):
        return f"{title}\n→ {result[1:].strip()}\n\n{i18n.t('capture.week_full')}"
    return f"{title}\n→ {result}"


def quote(text: str) -> str:
    """Text in quotes and italics, as Telegram HTML."""
    return f"<i>“{html.escape(text)}”</i>"


def split(text: str, limit: int = LIMIT) -> list[str]:
    """Cuts a long text into pieces that fit in one Telegram message. A cut falls between paragraphs, else between
    lines, else between words, and only in the middle of a word when a word is longer than a whole message."""
    pieces, rest = [], text.strip()
    while len(rest) > limit:
        for separator in ("\n\n", "\n", " "):
            cut = rest.rfind(separator, limit // 2, limit + len(separator))
            if cut != -1:
                pieces.append(rest[:cut].rstrip())
                rest = rest[cut + len(separator):].lstrip()
                break
        else:
            pieces.append(rest[:limit])
            rest = rest[limit:]
    return pieces + [rest] if rest else pieces


def remember(message_id: int, text: str) -> None:
    """Keeps the text of a message Seahorse sent, for a while."""
    with _sent_lock:
        _sent[message_id] = text
        while len(_sent) > SENT_KEPT:
            _sent.pop(next(iter(_sent)))


def sent_text(message_id: int) -> str | None:
    """The full text of a message Seahorse sent lately, or None when it is not known (such as after a restart)."""
    with _sent_lock:
        return _sent.get(message_id)


def send(text: str, buttons: list[tuple[str, str]] | None = None, chat_id: int | None = None) -> int | None:
    """Sends plain text. Buttons are (label, callback_data) pairs. Returns the message id."""
    last = None
    pieces = split(text) or [""]
    for i, piece in enumerate(pieces):
        body = {"chat_id": chat_id or config.USER_ID, "text": piece, "disable_web_page_preview": True}
        if buttons and i == len(pieces) - 1:
            body["reply_markup"] = keyboard(buttons)
        result = _call("sendMessage", body)
        if result is None:
            return None
        last = result["message_id"]
    if last is not None:
        remember(last, text)
    return last


def send_photo(image: bytes, caption: str = "") -> int | None:
    r = requests.post(_url("sendPhoto"), data={"chat_id": config.USER_ID, "caption": caption},
                      files={"photo": ("week.png", image, "image/png")}, timeout=60)
    if not r.ok:
        log.error("telegram sendPhoto: %s", r.text[:300])
        return None
    return r.json().get("result", {}).get("message_id")


def send_document(path: Path, caption: str = "") -> int | None:
    """Sends a vault file, such as the note of a research, to read in Telegram itself."""
    with open(path, "rb") as handle:
        r = requests.post(_url("sendDocument"), data={"chat_id": config.USER_ID, "caption": caption},
                          files={"document": (path.name, handle, "text/markdown")}, timeout=60)
    if not r.ok:
        log.error("telegram sendDocument: %s", r.text[:300])
        return None
    return r.json().get("result", {}).get("message_id")


def edit(message_id: int, text: str, buttons: list[tuple[str, str]] | None = None) -> bool:
    body = {"chat_id": config.USER_ID, "message_id": message_id, "text": (split(text) or [""])[0],
            "disable_web_page_preview": True}
    if buttons:
        body["reply_markup"] = keyboard(buttons)
    return _call("editMessageText", body) is not None


def bot_name(token: str) -> str | None:
    """The bot's @username, or None when the token is wrong. Used by the setup."""
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=20)
        return r.json()["result"]["username"] if r.ok else None
    except (requests.RequestException, KeyError, ValueError):
        return None


def wait_for_first_message(token: str, timeout: int = 300) -> int | None:
    """Waits for someone to send /start to the bot and returns their user id. Used by the setup."""
    import time
    offset, deadline = None, time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                             params={"timeout": 25, **({"offset": offset} if offset else {})}, timeout=35)
            updates = r.json().get("result", []) if r.ok else []
        except (requests.RequestException, ValueError):
            updates = []
        for update in updates:
            offset = update["update_id"] + 1
            sender = (update.get("message") or {}).get("from")
            if sender and not sender.get("is_bot"):
                requests.get(f"https://api.telegram.org/bot{token}/getUpdates", params={"offset": offset}, timeout=20)
                return sender["id"]
    return None
