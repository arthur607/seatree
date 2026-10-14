"""Settings. Everything that is personal lives in ~/.seahorse, never in the code.

- `config.toml`: name, language, time zone, AI provider, schedule, capacity. Written by `seahorse setup`.
- `secrets.env`: tokens and keys, readable only by the owner.
- `state/`: small files the agents keep between runs.

Environment variables override the files. The tests use them.
"""
import os
import tomllib
from pathlib import Path
from zoneinfo import ZoneInfo

HOME = Path(os.environ.get("SEAHORSE_HOME", Path.home() / ".seahorse"))
CONFIG_FILE = HOME / "config.toml"
SECRETS_FILE = Path(os.environ.get("SEAHORSE_SECRETS", HOME / "secrets.env"))
CODE = Path(__file__).resolve().parent
PROMPTS = CODE / "prompts"
TEMPLATE = CODE / "vault_template"

DEFAULTS = {
    "user": {"name": "", "language": "en", "timezone": "UTC"},
    "ai": {"provider": "claude", "models": {}},
    "vault": {"path": str(Path.home() / "seahorse-vault")},
    "telegram": {"user_id": 0},
    "schedule": {"morning": "08:00", "reviewer": "03:30", "weekly_day": "sun", "weekly_time": "18:00",
                 "quiet_from": "00:00", "quiet_until": "08:00"},
    "week": {"daily_capacity": "4h", "work_tag": "work"},
}


def _load_secrets() -> None:
    if not SECRETS_FILE.exists():
        return
    for line in SECRETS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def _merge(base: dict, extra: dict) -> dict:
    merged = dict(base)
    for key, value in extra.items():
        merged[key] = _merge(base[key], value) if isinstance(value, dict) and isinstance(base.get(key), dict) else value
    return merged


def load() -> dict:
    """The settings: the defaults, then config.toml on top."""
    _load_secrets()
    data = tomllib.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}
    return _merge(DEFAULTS, data)


SETTINGS = load()


def _hhmm(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


USER_NAME = os.environ.get("SEAHORSE_USER_NAME", SETTINGS["user"]["name"]) or "the user"
LANGUAGE = os.environ.get("SEAHORSE_LANGUAGE", SETTINGS["user"]["language"])
TIMEZONE = ZoneInfo(os.environ.get("SEAHORSE_TIMEZONE", SETTINGS["user"]["timezone"]))
PROVIDER = os.environ.get("SEAHORSE_PROVIDER", SETTINGS["ai"]["provider"])
MODEL_OVERRIDES: dict = SETTINGS["ai"].get("models", {})
VAULT = Path(os.environ.get("SEAHORSE_VAULT", SETTINGS["vault"]["path"])).expanduser()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
USER_ID = int(os.environ.get("TELEGRAM_USER_ID", SETTINGS["telegram"]["user_id"]) or 0)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

SCHEDULE = SETTINGS["schedule"]
QUIET_FROM, QUIET_UNTIL = _hhmm(SCHEDULE["quiet_from"]), _hhmm(SCHEDULE["quiet_until"])
DAILY_CAPACITY = SETTINGS["week"]["daily_capacity"]
WORK_TAG = SETTINGS["week"]["work_tag"]

STATE = Path(os.environ.get("SEAHORSE_STATE", HOME / "state"))
LOCK = Path(os.environ.get("SEAHORSE_LOCK", STATE / "vault.lock"))
NOTES_STATE = STATE / "notes.json"
COMPANION_STATE = STATE / "companion.json"

def use_vault(path: Path) -> None:
    """Points every vault path to a vault folder. The structure is in English for every language."""
    global VAULT, INTENTION, ENGRAMS, SYSTEM, TASKS, WEEKS, DASHBOARD, LOGS, MESSAGES, QUEUE, CALIBRATION
    VAULT = Path(path).expanduser()
    INTENTION = VAULT / "1. 🎯 Intention"
    ENGRAMS = VAULT / "5. 💭 Engrams"
    SYSTEM = VAULT / "6. ⚙️ System"
    TASKS = INTENTION / "Tasks"
    WEEKS = INTENTION / "Week"
    DASHBOARD = INTENTION / "Dashboard.md"
    LOGS = SYSTEM / "_Logs"
    MESSAGES = SYSTEM / "_Messenger" / "Messages.md"
    QUEUE = SYSTEM / "_Messenger" / "Queue.md"
    CALIBRATION = SYSTEM / "_Messenger" / "Calibration.md"


use_vault(VAULT)

# A note the user wrote is read only after it stays unchanged this long, to not interrupt the writing.
NOTES_QUIET_SECONDS = int(os.environ.get("SEAHORSE_NOTES_QUIET", str(15 * 60)))
# How many of the user's notes one run reads. The rest wait for the next run.
NOTES_PER_RUN = 8
# The bot waits for more messages before it runs the Integrator, to integrate them together.
INTEGRATOR_DELAY_SECONDS = int(os.environ.get("SEAHORSE_INTEGRATOR_DELAY", "20"))
# A chat question continues the same AI session if the last one was less than this ago.
CHAT_WINDOW_MINUTES = 30
