"""OpenAI Codex CLI in non-interactive mode (`codex exec`). Login with `codex login --device-auth` (a ChatGPT
subscription) or with an OpenAI API key (`codex login --with-api-key`)."""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import config
from . import Answer


def binary() -> str:
    return os.environ.get("CODEX_BIN") or shutil.which("codex") or "codex"


def run(text: str, tier: str, effort: str, model: str | None, write: bool, web: bool,
        session: str | None, max_turns: int, timeout: int) -> Answer:
    with tempfile.TemporaryDirectory() as folder:
        last = Path(folder) / "last.txt"
        # Each run starts a new session: chat follow-ups carry their context in the prompt instead.
        cmd = [binary(), "exec", "--json", "--skip-git-repo-check", "-C", str(config.VAULT), "-o", str(last),
                "-s", "workspace-write" if write else "read-only",
                "-c", 'approval_policy="never"',
                "-c", f'model_reasoning_effort="{effort}"',
                "-c", f'web_search="{"live" if (write or web) else "disabled"}"']
        if model:
            cmd += ["-m", model]
        cmd.append(text)
        try:
            out = subprocess.run(cmd, cwd=config.VAULT, stdin=subprocess.DEVNULL, capture_output=True,
                                 text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return Answer(f"timed out after {timeout}s", None, True)
        except FileNotFoundError:
            return Answer("the codex command is not installed", None, True)
        answer = last.read_text(encoding="utf-8").strip() if last.exists() else ""
    thread = None
    for line in out.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        thread = event.get("thread_id") or thread
    if out.returncode != 0 and not answer:
        return Answer((out.stderr or out.stdout)[-800:], thread, True)
    return Answer(answer, thread, False)


def available() -> tuple[bool, str]:
    if not shutil.which(binary()):
        return False, "Codex CLI is not installed"
    status = subprocess.run([binary(), "login", "status"], capture_output=True, text=True)
    if status.returncode != 0:
        return False, "Codex is not logged in: run `codex login --device-auth`"
    return True, "Codex is ready"
