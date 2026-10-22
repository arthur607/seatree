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


# Readable messages: Telegram HTML, cut at paragraphs, with link buttons. A vault note becomes text to read here.
import re  # noqa: E402 - kept with the code that uses it, at the end of the file

HTML_LIMIT = 3800  # Telegram takes 4096 characters. The rest is room for tags that close and open again at a cut.
TAG = re.compile(r"<(/?)([a-z]+)\b[^>]*>")


def plain(text: str) -> str:
    """Telegram HTML as plain text: no tags, and the escaped characters back."""
    return html.unescape(TAG.sub("", text))


def _open_tags(text: str) -> list[str]:
    """The tags still open at the end of a piece of HTML, as they were written."""
    stack = []
    for m in TAG.finditer(text):
        if not m[1]:
            stack.append(m[0])
        elif stack:
            stack.pop()
    return stack


def _hard_cut(text: str, limit: int) -> int:
    """Where to cut a text that has no break: before the limit, and never inside a tag or an escaped character."""
    cut = limit
    if text.rfind("<", 0, cut) > text.rfind(">", 0, cut):
        cut = text.rfind("<", 0, cut)
    if text.rfind("&", 0, cut) > text.rfind(";", 0, cut):
        cut = text.rfind("&", 0, cut)
    return cut or limit


def _cuts(text: str, limit: int) -> list[str]:
    """Cuts at the largest break that fits: between paragraphs, then between lines, then between words."""
    if len(text) <= limit:
        return [text]
    for separator in ("\n\n", "\n", " "):
        parts = re.split(r" (?![^<>]*>)", text) if separator == " " else text.split(separator)
        if len(parts) > 1:
            break
    else:
        cut = _hard_cut(text, limit)
        return [text[:cut]] + _cuts(text[cut:], limit)
    pieces, current = [], ""
    for part in parts:
        joined = f"{current}{separator}{part}" if current else part
        if len(joined) <= limit:
            current = joined
            continue
        if current:
            pieces.append(current)
        smaller = _cuts(part, limit)
        pieces += smaller[:-1]
        current = smaller[-1]
    return pieces + ([current] if current else [])


def split_html(text: str, limit: int = HTML_LIMIT) -> list[str]:
    """Telegram HTML in pieces that fit in one message each. A tag open at a cut, such as a quote, closes at the end
    of its piece and opens again at the start of the next. Pieces with no text are left out."""
    pieces, carry = [], []
    for raw in _cuts(text.strip(), limit):
        body = "".join(carry) + raw.strip("\n")
        carry = _open_tags(body)
        closing = "".join(f"</{TAG.match(tag)[2]}>" for tag in reversed(carry))
        if plain(body).strip():
            pieces.append(body + closing)
    return pieces


def link_keyboard(buttons: list[tuple[str, str]] | None, links: list[tuple[str, str]] | None) -> dict | None:
    """Link buttons (label, url) first, then the usual buttons (label, callback_data)."""
    keys = [[{"text": label, "url": url} for label, url in row] for row in rows(links)] if links else []
    keys += keyboard(buttons)["inline_keyboard"] if buttons else []
    return {"inline_keyboard": keys} if keys else None


def send_html(text: str, buttons: list[tuple[str, str]] | None = None, links: list[tuple[str, str]] | None = None,
              chat_id: int | None = None) -> int | None:
    """Sends Telegram HTML, cut at paragraphs when it is long. The buttons and the link buttons go under the last
    piece. If Telegram refuses the HTML of a piece, that piece goes again as plain text. Returns the last message id."""
    last, pieces = None, split_html(text)
    for i, piece in enumerate(pieces):
        body = {"chat_id": chat_id or config.USER_ID, "text": piece, "parse_mode": "HTML", "disable_web_page_preview": True}
        if i == len(pieces) - 1 and (buttons or links):
            body["reply_markup"] = link_keyboard(buttons, links)
        result = _call("sendMessage", body)
        if result is None:
            log.warning("telegram refused the HTML of a message, it goes as plain text")
            body.pop("parse_mode")
            body["text"] = plain(piece)
            result = _call("sendMessage", body)
        if result is None:
            return None
        last = result["message_id"]
        remember(last, plain(piece))
    return last


CITATION = re.compile(r"\s*\^\[\[[^\]]*\]\]")  # a source, such as ^[[2026-10-21 1432]]
BLOCK_ID = re.compile(r"\s+\^[\w-]+$")  # a block id at the end of a line, such as ^a1b2c3
EMBED = re.compile(r"!\[\[[^\]]*\]\]|!\[[^\]]*\]\([^)]*\)")  # an embedded note or image
WIKILINK = re.compile(r"\[\[([^\]|]*)(?:\|([^\]]*))?\]\]")
LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
CODE = re.compile(r"`([^`\n]+)`")
BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
ITALIC = re.compile(r"(?<![\w*])\*(?![\s*])([^*<>\n]+?)(?<!\s)\*(?![\w*])|(?<!\w)_(?![\s_])([^_<>\n]+?)(?<!\s)_(?!\w)")
LIST_ITEM = re.compile(r"^(\s*)[-*+]\s+(?:\[([ xX/-])\]\s+)?(.*)$")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
CALLOUT = re.compile(r"^\[!(\w+)\][+-]?\s*(.*)$")


def _wikilink_text(m: re.Match) -> str:
    """`[[note|alias]]` is the alias, `[[folder/note#part]]` is the note name."""
    if m[2] and m[2].strip():
        return m[2].strip()
    target, _, part = m[1].partition("#")
    return target.split("/")[-1].strip() or part.lstrip("^").strip()


def inline_html(text: str) -> str:
    """One line of Markdown as Telegram HTML. Web links stay links, note links become plain names, sources and
    embeds go away, and everything else is escaped."""
    kept: list[str] = []

    def keep(piece: str) -> str:
        kept.append(piece)
        return f"\x00{len(kept) - 1}\x00"

    text = BLOCK_ID.sub("", EMBED.sub("", CITATION.sub("", text)))
    text = CODE.sub(lambda m: keep(f"<code>{html.escape(m[1], quote=False)}</code>"), text)
    text = LINK.sub(lambda m: keep(f'<a href="{html.escape(m[2])}">'
                                   f'{html.escape(WIKILINK.sub(_wikilink_text, m[1]), quote=False)}</a>'), text)
    text = html.escape(WIKILINK.sub(_wikilink_text, text), quote=False)
    text = BOLD.sub(lambda m: f"<b>{m[1] or m[2]}</b>", text)
    text = ITALIC.sub(lambda m: f"<i>{m[1] or m[2]}</i>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m[1])], text).strip()


def _table_row(line: str) -> str | None:
    """A table row as one line, its cells joined by "·". None for the line under the header."""
    cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
    if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
        return None
    return " · ".join(inline_html(c.replace("\\|", "|")) for c in cells if c)


def note_html(markdown: str) -> str:
    """A vault note as Telegram HTML, to read in the chat: no frontmatter, no comments and no History section;
    headings in bold, lists with "•", tables as lines, note links as plain names, web links as links."""
    text = markdown.replace("\r\n", "\n")
    if text.startswith("---\n") and text.find("\n---", 4) != -1:
        text = text[text.find("\n---", 4) + 4:]
    text = re.sub(r"%%.*?%%", "", text, flags=re.S)
    out, skipping, fence = [], 0, False  # skipping: the level of the History heading, while inside it
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fence = not fence
            continue
        if fence:
            out.append(html.escape(line, quote=False))
            continue
        heading = HEADING.match(stripped)
        if heading and (not skipping or len(heading[1]) <= skipping):
            title = heading[2].strip().strip("#").strip()
            skipping = len(heading[1]) if title.lower() == "history" else 0
            if not skipping and title.lower() != "current":
                out.append(f"<b>{inline_html(title)}</b>")
            continue
        if skipping:
            continue
        if stripped.startswith(">"):
            line = re.sub(r"^\s*(>\s?)+", "", line)
            stripped = line.strip()
            callout = CALLOUT.match(stripped)
            if callout:
                out.append(f"<b>{inline_html(callout[2] or callout[1].capitalize())}</b>")
                continue
        if re.fullmatch(r"([-*_])(\s*\1){2,}", stripped):
            continue
        item = LIST_ITEM.match(line)
        if item:
            mark = {"x": "☑", "X": "☑", " ": "☐"}.get(item[2], "•") if item[2] else "•"
            out.append("  " * (len(item[1].expandtabs(4)) // 2) + f"{mark} {inline_html(item[3])}")
        elif stripped.startswith("|"):
            row = _table_row(stripped)
            if row is not None:
                out.append(row)
        else:
            out.append(inline_html(stripped))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
