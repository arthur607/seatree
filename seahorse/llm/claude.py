"""Claude Code in headless mode (`claude -p`). Login with `claude setup-token` (a Claude subscription) or with
ANTHROPIC_API_KEY (API billing)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

from .. import config
from . import Answer, safe_env, workdir

TIERS = {"fast": "haiku", "standard": "sonnet", "deep": "opus"}
READ = ["Read", "Glob", "Grep"]
WEB = ["WebFetch", "WebSearch"]
WRITE = READ + ["Write", "Edit", "Bash(git mv:*)", "Bash(mkdir:*)"] + WEB
NEVER_WHEN_READING = ["Write", "Edit", "NotebookEdit", "Bash"]
LOGIN = {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"}
# What no agent may read or change, whatever a text in the vault or on the web asks: the secrets of Seahorse and
# of the AI, ssh keys, git and GitHub logins, the environment of the process, and the git folder of the vault.
# "~/" is the home folder, "//" the root of the disk, and "./" the vault. Edit rules also cover Write.
SECRETS = ["~/.seahorse/**", "~/.claude/**", "~/.claude.json", "~/.codex/**", "~/.ssh/**", "~/.config/**",
           "~/.git-credentials", "~/.netrc", "~/.bash_history", "//proc/**", "//**/.env", "//**/*.env", "./.git/**"]
# Obsidian runs the code of its plugins: an agent never writes there.
NEVER_EDIT = ["./.obsidian/**"]


def denied() -> list[str]:
    """The deny rules of every run. Deny rules win over allow rules."""
    paths = list(SECRETS)
    for folder in (config.HOME, config.SECRETS_FILE.parent):
        absolute = folder.resolve().as_posix()
        if absolute.startswith("/") and not set(absolute) & {" ", ","}:
            paths.append(f"/{absolute}/**")  # a Seahorse home outside ~/.seahorse
    rules = [f"Read({p})" for p in paths] + [f"Edit({p})" for p in paths + NEVER_EDIT]
    try:
        hidden = any(part.startswith(".") for part in config.VAULT.resolve().relative_to(Path.home().resolve()).parts)
    except ValueError:
        hidden = False
    if not hidden:  # the dotfiles of the home folder, such as .bashrc, run code at the next login
        rules += ["Edit(~/.*)", "Edit(~/.*/**)"]
    return rules


def binary() -> str:
    """The native Claude Code of this user first. A global install from npm can be old and break on an old Node.js."""
    native = Path.home() / ".local/bin/claude"
    if os.environ.get("CLAUDE_BIN"):
        return os.environ["CLAUDE_BIN"]
    return str(native) if native.exists() else shutil.which("claude") or str(native)


def answer_from(data: dict) -> Answer:
    """The answer in Claude Code's JSON. A run that stops on an error can say `is_error: false` with an empty
    result: its subtype and its `errors` tell the truth."""
    text = (data.get("result") or "").strip()
    failed = bool(data.get("is_error")) or data.get("subtype", "success") != "success"
    if failed and not text:
        errors = [str(e).strip().splitlines()[0] for e in data.get("errors") or [] if str(e).strip()]
        text = "; ".join(errors) or str(data.get("subtype", "error"))
    return Answer(text, data.get("session_id"), failed)


def run(text: str, tier: str, effort: str, model: str | None, write: bool, web: bool,
        session: str | None, max_turns: int, timeout: int) -> Answer:
    model = model or TIERS[tier]
    cmd = [binary(), "-p", text, "--model", model, "--output-format", "json",
           "--strict-mcp-config", "--max-turns", str(max_turns)]
    if tier != "fast":  # the fast model takes no effort setting
        cmd += ["--effort", effort]
    if write:
        cmd += ["--permission-mode", "acceptEdits", "--allowedTools", *WRITE, "--disallowedTools", *denied()]
    else:
        cmd += ["--allowedTools", *READ, *(WEB if web else []), "--disallowedTools", *NEVER_WHEN_READING, *denied()]
    if session:
        cmd += ["--resume", session]
    try:
        out = subprocess.run(cmd, cwd=workdir(), stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=timeout, env=safe_env(LOGIN))
    except subprocess.TimeoutExpired:
        return Answer(f"timed out after {timeout}s", None, True)
    except FileNotFoundError:
        return Answer("the claude command is not installed", None, True)
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return Answer((out.stderr or out.stdout)[-800:], None, True)
    return answer_from(data)


def available() -> tuple[bool, str]:
    if not shutil.which(binary()) and not Path(binary()).exists():
        return False, "Claude Code is not installed"
    if not (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")):
        return False, "no CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in secrets.env"
    return True, "Claude Code is ready"
