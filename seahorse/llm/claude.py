"""Claude Code in headless mode (`claude -p`). Login with `claude setup-token` (a Claude subscription) or with
ANTHROPIC_API_KEY (API billing)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

from .. import config
from . import Answer, workdir

TIERS = {"fast": "haiku", "standard": "sonnet", "deep": "opus"}
READ = ["Read", "Glob", "Grep"]
WEB = ["WebFetch", "WebSearch"]
WRITE = READ + ["Write", "Edit", "Bash(git mv:*)", "Bash(mkdir:*)"] + WEB
NEVER_WHEN_READING = ["Write", "Edit", "NotebookEdit", "Bash"]


def binary() -> str:
    return os.environ.get("CLAUDE_BIN") or shutil.which("claude") or str(Path.home() / ".local/bin/claude")


def run(text: str, tier: str, effort: str, model: str | None, write: bool, web: bool,
        session: str | None, max_turns: int, timeout: int) -> Answer:
    model = model or TIERS[tier]
    cmd = [binary(), "-p", text, "--model", model, "--output-format", "json",
           "--strict-mcp-config", "--max-turns", str(max_turns)]
    if tier != "fast":  # the fast model takes no effort setting
        cmd += ["--effort", effort]
    if write:
        cmd += ["--permission-mode", "acceptEdits", "--allowedTools", *WRITE]
    else:
        cmd += ["--allowedTools", *READ, *(WEB if web else []), "--disallowedTools", *NEVER_WHEN_READING]
    if session:
        cmd += ["--resume", session]
    try:
        out = subprocess.run(cmd, cwd=workdir(), stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Answer(f"timed out after {timeout}s", None, True)
    except FileNotFoundError:
        return Answer("the claude command is not installed", None, True)
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return Answer((out.stderr or out.stdout)[-800:], None, True)
    return Answer((data.get("result") or "").strip(), data.get("session_id"), bool(data.get("is_error")))


def available() -> tuple[bool, str]:
    if not shutil.which(binary()) and not Path(binary()).exists():
        return False, "Claude Code is not installed"
    if not (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")):
        return False, "no CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in secrets.env"
    return True, "Claude Code is ready"
