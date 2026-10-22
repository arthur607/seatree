"""The AI behind the agents. Each provider is an agentic CLI that can read and edit the vault and search the web:
Claude Code or Codex. Both work with a subscription login or with an API key.

The agents ask for a role. The role gives the model tier and the reasoning effort, and the provider turns them
into its own models and flags.
"""
import logging
import os
from dataclasses import dataclass
from string import Template

from .. import config, i18n

log = logging.getLogger(__name__)

# Tokens that no model needs. The CLI of the provider keeps only its own login.
OTHER_SECRETS = {"TELEGRAM_BOT_TOKEN", "GROQ_API_KEY", "GH_TOKEN", "GITHUB_TOKEN"}
# A commit that a model makes, against the rules, is signed as Seahorse: it never passes as a note of the user.
AGENT_GIT = {"GIT_AUTHOR_NAME": "Seahorse", "GIT_AUTHOR_EMAIL": "seahorse@localhost",
             "GIT_COMMITTER_NAME": "Seahorse", "GIT_COMMITTER_EMAIL": "seahorse@localhost"}


def safe_env(keep: set[str]) -> dict[str, str]:
    """The environment of the AI's CLI: without the keys of secrets.env and the tokens of other services, except
    the login that this provider needs (`keep`), and with git signing as Seahorse."""
    secrets = set(OTHER_SECRETS)
    if config.SECRETS_FILE.exists():
        for line in config.SECRETS_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                secrets.add(line.split("=", 1)[0].strip())
    return {k: v for k, v in os.environ.items() if k not in secrets - keep} | AGENT_GIT

# Role → (tier, effort). "fast" is for one-line jobs, "deep" for the weekly planning and the patterns.
ROLES = {
    "classify": ("fast", "low"),
    "phrase": ("fast", "low"),
    "now": ("fast", "low"),
    "question": ("standard", "low"),
    "morning": ("standard", "low"),
    "fix": ("standard", "low"),
    "retro": ("standard", "low"),
    "integrator": ("standard", "medium"),
    "notes": ("standard", "medium"),
    "companion": ("standard", "medium"),
    "reviewer": ("standard", "medium"),
    "planner": ("deep", "high"),
    "observer": ("deep", "high"),
}


@dataclass
class Answer:
    text: str
    session: str | None
    error: bool


def workdir():
    """The folder the AI works in: the vault. The setup tests the AI before the vault exists, so then Seahorse's home."""
    if config.VAULT.is_dir():
        return config.VAULT
    config.HOME.mkdir(parents=True, exist_ok=True)
    return config.HOME


def prompt(name: str, **values) -> str:
    """The text of `prompts/<name>.md`, with its `$placeholders` filled in."""
    body = (config.PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    common = {"user": config.USER_NAME, "language": i18n.language_name(), "work_tag": config.WORK_TAG,
              "capacity": config.DAILY_CAPACITY, "reply_done": i18n.t("reply.done"),
              "reply_later": i18n.t("reply.later"), "reply_mute": i18n.t("reply.mute")}
    return Template(body).safe_substitute(common | values)


def _backend():
    if config.PROVIDER == "codex":
        from . import codex
        return codex
    from . import claude
    return claude


def run(text: str, role: str, write: bool = False, session: str | None = None,
        max_turns: int = 80, timeout: int = 3600, web: bool = False) -> Answer:
    """Runs one agent turn in the vault folder.

    - `write`: the agent may create and edit files in the vault, and search the web.
    - `web`: read-only, but with web search.
    - `session`: continue an earlier conversation, for chat questions.
    """
    tier, effort = ROLES[role]
    model = config.MODEL_OVERRIDES.get(tier)
    log.info("ai: provider=%s role=%s tier=%s write=%s web=%s", config.PROVIDER, role, tier, write, web)
    return _backend().run(text, tier=tier, effort=effort, model=model, write=write, web=web,
                          session=session, max_turns=max_turns, timeout=timeout)


def available() -> tuple[bool, str]:
    """Is the provider installed and logged in? Used by `seahorse doctor` and the setup."""
    return _backend().available()
