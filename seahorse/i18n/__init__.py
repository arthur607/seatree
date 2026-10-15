"""The fixed texts of the bot, the images and the setup, in each supported language.

The agents write in the user's language by themselves: their prompts are in English and say which language to use.
This module only holds the texts that code writes. A missing key falls back to English.
"""
import tomllib
from functools import cache
from pathlib import Path

from .. import config

FOLDER = Path(__file__).resolve().parent
LANGUAGES = {"en": "English", "pt-BR": "Português (Brasil)", "es": "Español"}


@cache
def _catalog(language: str) -> dict:
    path = FOLDER / f"{language}.toml"
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _find(language: str, key: str):
    node = _catalog(language)
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def t(key: str, language: str | None = None, /, **values) -> str:
    """The text for `key`, with `{placeholders}` filled in."""
    text = _find(language or config.LANGUAGE, key)
    if text is None:
        text = _find("en", key)
    if text is None:
        return key
    return str(text).format(**values) if values else str(text)


def words(key: str, language: str | None = None) -> list[str]:
    """A list from the catalog, such as the day names."""
    found = _find(language or config.LANGUAGE, key)
    return list(found if isinstance(found, list) else _find("en", key) or [])


def language_name(language: str | None = None) -> str:
    """The name of the content language in English, for the prompts: "Brazilian Portuguese"."""
    return t("language_name_in_english", language)
