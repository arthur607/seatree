"""Voice to text. Groq when there is a key: fast, and free on the basic plan. A local Whisper model otherwise.

A voice note that cannot become text now, because Groq is down, waits in Seahorse's state folder. The bot tries
it again at each retry tick. When it works, or after GIVE_UP_HOURS, it becomes an engram: with its text, or with
the audio in the attachments folder. Nothing the user says is lost.
"""
import importlib.util
import json
import logging
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import requests

from . import config, i18n, notes, telegram_api, vault

log = logging.getLogger(__name__)
_local_model = None
WAITING = config.STATE / "voice"
GIVE_UP_HOURS = 3
_lock = threading.Lock()  # the list of waiting voice notes changes from the bot and from the retry tick


def available() -> bool:
    """True when a voice note can become text: there is a Groq key, or the local model is installed."""
    return bool(config.GROQ_API_KEY) or importlib.util.find_spec("faster_whisper") is not None


def transcribe(audio: Path) -> str | None:
    """The text of a voice note. "" when nobody speaks in it. None when no engine could read it now."""
    if config.GROQ_API_KEY:
        try:
            return _groq(audio)
        except Exception as error:  # noqa: BLE001 - any Groq failure falls back to the local model
            log.warning("groq failed, trying local whisper: %s", error)
    try:
        return _local(audio)
    except Exception:  # noqa: BLE001 - a broken local model must not stop the bot
        log.exception("local whisper failed")
        return None


def _groq(audio: Path) -> str:
    with audio.open("rb") as handle:
        r = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            files={"file": (audio.name, handle)},
            data={"model": "whisper-large-v3-turbo", "language": i18n.t("whisper"), "response_format": "text"},
            timeout=120,
        )
    r.raise_for_status()
    return r.text.strip()


def _local(audio: Path) -> str | None:
    global _local_model
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log.error("no GROQ_API_KEY and no local whisper: run `seahorse setup` to add a Groq key")
        return None
    if _local_model is None:
        _local_model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _ = _local_model.transcribe(str(audio), language=i18n.t("whisper"), vad_filter=True)
    return " ".join(s.text.strip() for s in segments).strip()


def without_text(name: str) -> str:
    """The engram text of a voice note that has no text: a short line, and the audio embedded under it."""
    return f"{i18n.t('chat.voice_untranscribed')}\n![[{name}]]"


def _load() -> list[dict]:
    try:
        return json.loads((WAITING / "waiting.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(items: list[dict]) -> None:
    WAITING.mkdir(parents=True, exist_ok=True)
    (WAITING / "waiting.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def wait(audio: bytes, name: str, channel: str, when: datetime, extra: dict, notice: int | None) -> None:
    """Keeps a voice note that could not become text, to try again later.

    `name` is the file name it gets in the attachments folder if it never becomes text. `extra` holds the engram
    fields, and `notice` is the bot message that says it waits: it becomes the confirmation later."""
    WAITING.mkdir(parents=True, exist_ok=True)
    file = f"{uuid.uuid4().hex[:8]}{Path(name).suffix}"
    (WAITING / file).write_bytes(audio)
    with _lock:
        items = _load()
        items.append({"file": file, "name": name, "channel": channel, "when": when.isoformat(), "at": time.time(),
                      "extra": extra, "notice": notice})
        _save(items)


def retry_waiting() -> int:
    """Tries again each voice note that waits. Returns how many became engrams."""
    with _lock:
        items = _load()
    done = []
    for item in items:
        audio = WAITING / item["file"]
        text = transcribe(audio) if audio.exists() else ""
        if text is None and time.time() - item["at"] < GIVE_UP_HOURS * 3600:
            continue  # it failed again: the next tick tries again
        _to_engram(item, audio, text)
        done.append(item["file"])
    if done:
        with _lock:
            _save([i for i in _load() if i["file"] not in done])
    return len(done)


def _to_engram(item: dict, audio: Path, text: str | None) -> None:
    """Makes the engram of a voice note that waited. Its notice on Telegram becomes the usual confirmation."""
    if not text and audio.exists():
        text = without_text(vault.save_attachment(audio.read_bytes(), item["name"]).name)
    if not text:
        return  # the audio file is gone: there is nothing to save
    engram = vault.save_engram(text, item["channel"], datetime.fromisoformat(item["when"]), item["extra"])
    audio.unlink(missing_ok=True)
    if item.get("notice"):
        correction = "corrects" in item["extra"]
        telegram_api.edit(item["notice"], telegram_api.capture_text(item["channel"], correction, None),
                          telegram_api.capture_buttons(engram.stem, item["channel"], correction))
        notes.set_field(engram, "confirmation", str(item["notice"]))
