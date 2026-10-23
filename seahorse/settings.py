"""The settings that the user changes in the chat, such as "no messages before 9".

The Integrator proposes a change as one part of its result, `⚙ key=value; key=value`. Code checks each value,
shows the change under the message with two buttons, and writes it into config.toml only when the user taps the
one that changes it. Only the keys below change this way. Tokens, keys, the AI and the vault never do, and neither
do the language and the daily capacity: the rules in the vault and `Capacity.md` say them too, and they change with
`seahorse setup` and in Identity."""
import json
import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import config, i18n

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
HHMM = re.compile(r"([01]?\d|2[0-3])[:h]([0-5]\d)?")
MOST_MESSAGES = 20  # the most messages a day that Seahorse may start, as a setting

# Each key that the chat can change: its table and field in config.toml.
KEYS = {
    "quiet_from": ("schedule", "quiet_from"),
    "quiet_until": ("schedule", "quiet_until"),
    "morning": ("schedule", "morning"),
    "weekly_day": ("schedule", "weekly_day"),
    "weekly_time": ("schedule", "weekly_time"),
    "daily_messages": ("messenger", "daily_messages"),
    "timezone": ("user", "timezone"),
}
MARK = "⚙"  # a part of an Integrator result that starts with it is a proposed change
_lock = threading.Lock()  # settings.json and config.toml, one writer at a time


def current() -> dict[str, str]:
    """The value of each key now, as text."""
    s = config.SCHEDULE
    return {"quiet_from": s["quiet_from"], "quiet_until": s["quiet_until"], "morning": s["morning"],
            "weekly_day": s["weekly_day"], "weekly_time": s["weekly_time"],
            "daily_messages": str(config.DAILY_MESSAGES), "timezone": str(config.TIMEZONE)}


def for_prompt() -> str:
    """The keys and their values now, for the Integrator."""
    return ", ".join(f"{key}={value}" for key, value in current().items())


def clean(key: str, value: str) -> str | None:
    """The value in the form config.toml keeps, or None when it is not a valid value for the key."""
    value = str(value).strip().strip("\"'`").strip()
    if key in ("quiet_from", "quiet_until", "morning", "weekly_time"):
        m = HHMM.fullmatch(value.lower())
        return f"{int(m[1]):02d}:{m[2] or '00'}" if m else None
    if key == "weekly_day":
        day = value.lower()[:3]
        return day if day in DAYS else None
    if key == "daily_messages":
        return value if value.isdigit() and int(value) <= MOST_MESSAGES else None
    if key == "timezone":
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, OSError):  # a folder of zones, such as "America", is an OSError
            return None
        return value
    return None


def _body(part: str) -> str:
    return part.strip().lstrip("→").strip()


def is_change(part: str) -> bool:
    return _body(part).startswith(MARK)


def parse(part: str, new_only: bool = True) -> dict[str, str]:
    """The valid changes in one `⚙ key=value; key=value` part. Unknown keys and bad values are left out, and so
    are the values that are set already, unless `new_only` is False."""
    now, found = current(), {}
    body = _body(part).lstrip(MARK).lstrip("️").strip()
    for piece in re.split(r"[;,]", body):
        if "=" not in piece:
            continue
        key, value = (s.strip() for s in piece.split("=", 1))
        good = clean(key, value) if key in KEYS else None
        if good is not None and (good != now[key] or not new_only):
            found[key] = good
    return found


def _day(code: str) -> str:
    return i18n.words("days_short")[DAYS.index(code)] if code in DAYS else code


def _quiet(start: str, end: str) -> str:
    return i18n.t("settings.no_quiet") if start == end else f"{start}–{end}"


def describe(changes: dict[str, str]) -> list[str]:
    """One line for each setting that changes, from its value now to the new one, in the language of the user.
    The two ends of the quiet hours share a line, and so do the day and the time of the weekly plan."""
    now, lines = current(), []
    new = now | changes
    if "quiet_from" in changes or "quiet_until" in changes:
        lines.append(f"{i18n.t('settings.quiet')}: {_quiet(now['quiet_from'], now['quiet_until'])} → "
                     f"{_quiet(new['quiet_from'], new['quiet_until'])}")
    if "morning" in changes:
        lines.append(f"{i18n.t('settings.morning')}: {now['morning']} → {new['morning']}")
    if "weekly_day" in changes or "weekly_time" in changes:
        lines.append(f"{i18n.t('settings.weekly')}: {_day(now['weekly_day'])} {now['weekly_time']} → "
                     f"{_day(new['weekly_day'])} {new['weekly_time']}")
    if "daily_messages" in changes:
        lines.append(f"{i18n.t('settings.daily_messages')}: {now['daily_messages']} → {new['daily_messages']}")
    if "timezone" in changes:
        lines.append(f"{i18n.t('settings.timezone')}: {now['timezone']} → {new['timezone']}")
    return lines


def show() -> str:
    """The answer to /settings: each setting now, and how to change it."""
    now = current()
    lines = [i18n.t("settings.title"),
             f"• {i18n.t('settings.quiet')}: {_quiet(now['quiet_from'], now['quiet_until'])}",
             f"• {i18n.t('settings.morning')}: {now['morning']}",
             f"• {i18n.t('settings.weekly')}: {_day(now['weekly_day'])} {now['weekly_time']}",
             f"• {i18n.t('settings.daily_messages')}: {now['daily_messages']}",
             f"• {i18n.t('settings.timezone')}: {now['timezone']}"]
    return "\n".join(lines) + "\n\n" + i18n.t("settings.how")


# --- Proposals that wait for a button, by the engram that asked for them ---

def _state_path() -> Path:
    return config.STATE / "settings.json"


def _load() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def propose(engram: str, changes: dict[str, str]) -> None:
    with _lock:
        data = _load()
        pending = data.setdefault("pending", {})
        pending[engram] = {"changes": changes, "at": datetime.now(config.TIMEZONE).isoformat(timespec="minutes")}
        data["pending"] = dict(list(pending.items())[-20:])
        _save(data)


def pending(engram: str) -> dict[str, str] | None:
    with _lock:
        found = _load().get("pending", {}).get(engram)
    return found["changes"] if found else None


def _drop(engram: str) -> None:
    data = _load()
    if data.get("pending", {}).pop(engram, None) is not None:
        _save(data)


def drop(engram: str) -> None:
    with _lock:
        _drop(engram)


def apply(engram: str) -> tuple[list[str], str]:
    """Writes the change that the engram proposed into config.toml, and uses it at once in this process. Returns
    the lines that say what changed, and an error, such as a config.toml that cannot be read."""
    with _lock:
        found = _load().get("pending", {}).get(engram)
        if not found:
            return [], i18n.t("settings.gone")
        changes = found["changes"]
        data, error = config.read_toml(config.CONFIG_FILE)
        if error:
            return [], error
        lines = describe(changes)
        for key, value in changes.items():
            table, field = KEYS[key]
            data.setdefault(table, {})[field] = int(value) if key == "daily_messages" else value
        config.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(prefix="config.", suffix=".toml", dir=config.CONFIG_FILE.parent)
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(config.dumps(data))
        os.chmod(temp, 0o600)
        os.replace(temp, config.CONFIG_FILE)
        _use(changes)
        _drop(engram)
    return lines, ""


def _use(changes: dict[str, str]) -> None:
    """The new values in this process, so the bot does not need to start again. Only what changed: a value that an
    environment variable sets stays as it is."""
    for key, value in changes.items():
        table, field = KEYS[key]
        config.SETTINGS.setdefault(table, {})[field] = int(value) if key == "daily_messages" else value
    s = config.SCHEDULE
    if "quiet_from" in changes or "quiet_until" in changes:
        config.QUIET_FROM, config.QUIET_UNTIL = config._hhmm(s["quiet_from"]), config._hhmm(s["quiet_until"])
    if "daily_messages" in changes:
        config.DAILY_MESSAGES = int(changes["daily_messages"])
    if "timezone" in changes:
        config.TIMEZONE = ZoneInfo(changes["timezone"])
