"""Voice to text. Groq when there is a key: fast, and free on the basic plan. A local Whisper model otherwise."""
import logging
from pathlib import Path

import requests

from . import config, i18n

log = logging.getLogger(__name__)
_local_model = None


def transcribe(audio: Path) -> str:
    if config.GROQ_API_KEY:
        try:
            return _groq(audio)
        except Exception as error:  # noqa: BLE001 - any Groq failure falls back to the local model
            log.warning("groq failed, using local whisper: %s", error)
    return _local(audio)


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


def _local(audio: Path) -> str:
    global _local_model
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log.error("no GROQ_API_KEY and no local whisper: run `seahorse setup` to add a Groq key")
        return ""
    if _local_model is None:
        _local_model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _ = _local_model.transcribe(str(audio), language=i18n.t("whisper"), vad_filter=True)
    return " ".join(s.text.strip() for s in segments).strip()
