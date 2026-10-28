"""OpenCode in headless mode (`opencode run`). xAI is an API key in XAI_API_KEY, not a SuperGrok login.

`opencode run --help` on this machine takes the prompt as an argument. There is no stdin flag. On a shared
server, `ps` can show that argument. The Debian user that runs Seahorse is the fence for that. Do not copy
Codex's missing deny list: this file writes an agent whose last rules deny paths outside the vault.

The run gets its own config and data folders. The data folder is the one that matters: OpenCode keeps the
logins of the desktop user in `~/.local/share/opencode`, and with that folder in reach it answers with the
stored login and never looks at `XAI_API_KEY`.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

from .. import config
from . import Answer, safe_env, workdir

LOGIN = {"XAI_API_KEY"}
# Listed by `opencode models` on 2026-10-28. [ai.models] overrides a tier. A missing id fails the run.
TIERS = {
    "fast": "xai/grok-4.20-0309-non-reasoning",
    "standard": "xai/grok-4.3",
    "deep": "xai/grok-4.7",
}


def binary() -> str:
    return os.environ.get("OPENCODE_BIN") or shutil.which("opencode") or "opencode"


def home() -> Path:
    """State of this backend only. Not the interactive ~/.config/opencode, not its database, and not the vault."""
    return config.HOME / "opencode-xdg"


def config_home() -> Path:
    return home() / "config"


def data_home() -> Path:
    """Its own auth store. Without this, OpenCode reads the login of the desktop user and ignores XAI_API_KEY."""
    return home() / "data"


def _agent(write: bool, web: bool, steps: int) -> str:
    """Permissions: a broad deny, then the allows, then the denies that must win. The last match wins."""
    allows = [
        ("read", "*"),
        ("glob", "*"),
        ("grep", "*"),
        ("shell", "git mv *"),
        ("shell", "mkdir *"),
    ]
    if write:
        allows.append(("edit", "*"))
    if write or web:
        allows.extend((("webfetch", "*"), ("websearch", "*")))
    denies = [
        ("external_directory", "*"),
        ("read", "$HOME/.seahorse/**"),
        ("edit", "$HOME/.seahorse/**"),
        ("read", "**/.env"),
        ("edit", "**/.env"),
        ("read", "**/*secrets.env"),
        ("edit", "**/*secrets.env"),
        ("read", "$HOME/.ssh/**"),
        ("edit", "$HOME/.ssh/**"),
        ("edit", "**/.git/**"),
        ("edit", "**/.obsidian/**"),
        ("skill", "*"),
        ("subagent", "*"),
        ("question", "*"),
    ]
    if not write:
        denies.append(("edit", "*"))
    rules = [{"action": "*", "resource": "*", "effect": "deny"}]
    rules += [{"action": action, "resource": resource, "effect": "allow"} for action, resource in allows]
    rules += [{"action": action, "resource": resource, "effect": "deny"} for action, resource in denies]
    lines = ["---", "description: Seahorse vault worker", "mode: primary", f"steps: {steps}", "permissions:"]
    for rule in rules:
        lines.append(f"  - action: {json.dumps(rule['action'])}")
        lines.append(f"    resource: {json.dumps(rule['resource'])}")
        lines.append(f"    effect: {rule['effect']}")
    lines += ["---", "", "Do the task in the message. Stay inside the working directory.", ""]
    return "\n".join(lines)


def prepare(write: bool, web: bool, steps: int) -> None:
    """Writes the agent into the isolated config home. The vault never holds this file."""
    root = config_home() / "opencode"
    agents = root / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "seahorse.md").write_text(_agent(write, web, steps), encoding="utf-8")
    (root / "opencode.jsonc").write_text(
        '{\n  "$schema": "https://opencode.ai/config.json",\n  "default_agent": "seahorse"\n}\n',
        encoding="utf-8",
    )


def events_from(stdout: str) -> tuple[str, str | None, str | None]:
    """Text, session id, and error message from `opencode run --format json`.

    A text event is `{"type":"text","sessionID":"...","part":{"text":"..."}}`.
    An error event is `{"type":"error","error":{"message":"..."}}`.
    """
    text: list[str] = []
    session = None
    error = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("sessionID"):
            session = str(event["sessionID"])
        if event.get("type") == "text":
            part = event.get("part") or {}
            if part.get("text"):
                text.append(str(part["text"]))
        if event.get("type") == "error":
            body = event.get("error") or {}
            error = str(body.get("message") or body.get("type") or "error")
    return "".join(text).strip(), session, error


def run(text: str, tier: str, effort: str, model: str | None, write: bool, web: bool,
        session: str | None, max_turns: int, timeout: int) -> Answer:
    del effort  # OpenCode has no --effort. The tier picks the model.
    model = model or TIERS.get(tier)
    if not model:
        return Answer(f"no model configured for tier {tier}", None, True)
    if not os.environ.get("XAI_API_KEY"):
        return Answer("no XAI_API_KEY in secrets.env", None, True)
    try:
        prepare(write, web, max_turns)
    except OSError as error:
        return Answer(str(error), None, True)
    cmd = [binary(), "run", "--standalone", "--format", "json", "--auto", "--agent", "seahorse",
           "--model", model]
    if session:
        cmd += ["--session", session]
    cmd.append(text)
    env = safe_env(LOGIN)
    folder = str(workdir())
    env["PWD"] = folder  # OpenCode resolves the project from PWD, not from the process cwd
    env["XDG_CONFIG_HOME"] = str(config_home())
    env["XDG_DATA_HOME"] = str(data_home())  # the login of the desktop user must not answer for this key
    try:
        out = subprocess.run(cmd, cwd=folder, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return Answer(f"timed out after {timeout}s", None, True)
    except FileNotFoundError:
        return Answer("the opencode command is not installed", None, True)
    answer, found, error = events_from(out.stdout)
    if error or out.returncode != 0:
        detail = error or (out.stderr or out.stdout or "opencode failed")[-800:]
        return Answer(detail.strip(), found, True)
    if not answer:
        return Answer((out.stderr or "opencode returned no text")[-800:], found, True)
    return Answer(answer, found, False)


def available() -> tuple[bool, str]:
    if not shutil.which(binary()) and not Path(binary()).exists():
        return False, "OpenCode is not installed"
    if not os.environ.get("XAI_API_KEY"):
        return False, "no XAI_API_KEY in secrets.env"
    return True, "OpenCode is ready"
