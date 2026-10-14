"""The AI behind the agents. Each provider is an agentic CLI that can read and edit the vault and search the web:
Claude Code or Codex. Both work with a subscription login or with an API key.

The agents ask for a role. The role gives the model tier and the reasoning effort, and the provider turns them
into its own models and flags.
"""
import logging
from dataclasses import dataclass
from string import Template

from .. import config, i18n

log = logging.getLogger(__name__)

# Role → (tier, effort). "fast" is for one-line jobs, "deep" for the weekly planning and the patterns.
ROLES = {
    "classify": ("fast", "low"),
    "phrase": ("fast", "low"),
    "question": ("standard", "low"),
    "morning": ("standard", "low"),
    "fix": ("standard", "low"),
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
