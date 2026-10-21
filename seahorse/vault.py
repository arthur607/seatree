"""The vault: engrams, the lock between processes, git, validation and creation from the template."""
import json
import logging
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from . import config, i18n, notes

log = logging.getLogger(__name__)

# Commits by these authors are agents. Every other commit is the user, from Obsidian.
AGENT_AUTHORS = {"Seahorse"}
# The week, the tasks and the Dashboard change at every checkbox: they are not notes for the Integrator to read.
NOT_NOTES = ("5. 💭 Engrams/", "6. ⚙️ System/", ".obsidian/", "1. 🎯 Intention/Week/",
             "1. 🎯 Intention/Tasks/", "1. 🎯 Intention/Dashboard.md")


class Busy(Exception):
    """Another process has the vault, and the caller asked not to wait."""


@contextmanager
def lock(wait: bool = True):
    """Only one writer at a time. The bot, the timers and the CLI take turns."""
    config.LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(config.LOCK, "a+") as handle:
        if os.name == "nt":  # tests and development on Windows: no lock between processes
            yield
            return
        import fcntl
        try:
            fcntl.flock(handle, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Busy from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(config.VAULT), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=check)


def has_remote() -> bool:
    return bool(git("remote", check=False).stdout.strip())


def save_user_edits() -> None:
    """Commits, as the user, what changed in this folder since the last commit: the edits made in Obsidian on this
    same machine. Agents call it (through pull) before they write. Without it, the next agent commit would take
    these edits as its own, and the notes reader would never see them."""
    if not (config.VAULT / ".git").exists():
        return
    git("add", "-A")
    if git("diff", "--cached", "--quiet", check=False).returncode != 0:
        name = config.USER_NAME if config.USER_NAME not in AGENT_AUTHORS else "owner"
        git("-c", f"user.name={name}", "-c", "user.email=owner@localhost", "commit", "-q", "-m", "edits on this machine")


def pull() -> None:
    """Saves the edits made on this machine, then brings in what changed on other devices. On a conflict, it gives
    up the rebase and goes on."""
    save_user_edits()
    if not has_remote():
        return
    result = git("pull", "--rebase", "--autostash", "-q", check=False)
    if result.returncode != 0:
        git("rebase", "--abort", check=False)
        log.warning("pull failed: %s", result.stderr.strip())


def publish(message: str) -> bool:
    """Commits everything as Seahorse and pushes. True if there was something to commit."""
    git("add", "-A")
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return False
    git("-c", "user.name=Seahorse", "-c", "user.email=seahorse@localhost", "commit", "-q", "-m", message)
    if has_remote() and git("push", "-q", check=False).returncode != 0:
        pull()
        result = git("push", "-q", check=False)
        if result.returncode != 0:
            log.error("push failed: %s", result.stderr.strip())
    return True


def new_engram(text: str, channel: str, when: datetime, extra: dict | None = None) -> Path:
    """Saves the raw capture. No lock and no git: it is only a new file, and the Integrator commits it."""
    folder = config.ENGRAMS / when.strftime("%Y-%m")
    folder.mkdir(parents=True, exist_ok=True)
    base = when.strftime("%Y-%m-%d %H%M")
    path, n = folder / f"{base}.md", 2
    while path.exists():
        path, n = folder / f"{base} {n}.md", n + 1
    lines = ["---", "type: engram", f"received: {when:%Y-%m-%d %H:%M}", f"channel: {channel}", "state: pending"]
    for key, value in (extra or {}).items():
        lines.append(f"{key}: {notes.quoted(value)}")
    lines += ["---", notes.without_blank_lines(text)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("engram saved: %s", path.name)
    return path


def engrams(state: str) -> list[Path]:
    if not config.ENGRAMS.exists():
        return []
    found = [p for p in config.ENGRAMS.rglob("*.md") if not p.stem.startswith("_") and notes.read(p)[0].get("state") == state]
    return sorted(found, key=lambda p: p.stem)


def engram(name: str) -> Path | None:
    found = list(config.ENGRAMS.rglob(f"{name}.md")) if config.ENGRAMS.exists() else []
    return found[0] if found else None


def engram_by_confirmation(message_id: int) -> Path | None:
    """The engram whose Telegram confirmation has this id. It looks at the newest first."""
    if not config.ENGRAMS.exists():
        return None
    for path in sorted(config.ENGRAMS.rglob("*.md"), key=lambda p: p.stem, reverse=True)[:300]:
        if notes.read(path)[0].get("confirmation") == str(message_id):
            return path
    return None


def rel(path: Path) -> str:
    return path.relative_to(config.VAULT).as_posix()


def notes_being_written() -> list[str]:
    """The user's notes that changed and that Seahorse has not read yet. No agent touches them until then."""
    try:
        return list(json.loads(config.NOTES_STATE.read_text(encoding="utf-8")).get("pending", {}))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def validate() -> tuple[bool, str]:
    env = {**os.environ, "SEAHORSE_SKIP": "\n".join(notes_being_written())}
    result = subprocess.run([sys.executable, "-m", "seahorse.lint", str(config.VAULT)],
                            capture_output=True, text=True, env=env)
    return result.returncode == 0, result.stdout.strip()


def user_changes(since: str) -> dict[str, int]:
    """Notes the user changed in Obsidian since a commit, with the time of the last change, in seconds.

    A commit belongs to the user when no agent made it. Agents commit as "Seahorse".
    """
    result = git("-c", "core.quotepath=off", "log", f"{since}..HEAD", "--no-merges", "--name-only",
                 "--format=__C__%an%x1f%ct", check=False)
    changes: dict[str, int] = {}
    human, moment = False, 0
    for line in result.stdout.splitlines():
        if line.startswith("__C__"):
            author, when = (line[5:].split("\x1f") + [""])[:2]
            human, moment = author not in AGENT_AUTHORS, int(when or 0)
        elif human and line.endswith(".md") and not line.startswith(NOT_NOTES):
            changes[line] = max(changes.get(line, 0), moment)
    return changes


def changed_since(hours: int) -> list[str]:
    result = git("-c", "core.quotepath=off", "log", f"--since={hours} hours ago", "--name-only", "--pretty=format:", check=False)
    return sorted({line for line in result.stdout.splitlines() if line.endswith(".md")})


def week_name(day: date) -> str:
    year, number, _ = day.isocalendar()
    return f"{year}-W{number:02d}"


def fill(text: str, today: date) -> str:
    """Fills the placeholders of a template file: `{{user}}`, `{{t:key}}` and the others."""
    values = {"user": config.USER_NAME, "language": i18n.language_name(), "capacity": config.DAILY_CAPACITY,
              "work_tag": config.WORK_TAG, "week": week_name(today), "today": today.isoformat()}
    text = re.sub(r"\{\{t:([a-z_.]+)\}\}", lambda m: i18n.t(m[1]), text)
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values.get(m[1], m[0]), text)


def create(today: date) -> list[str]:
    """Creates a new vault from the template, with this week ready. Existing files are never replaced."""
    created = []
    for source in sorted(config.TEMPLATE.rglob("*")):
        if source.is_dir() or "__pycache__" in source.parts:
            continue
        target = config.VAULT / source.relative_to(config.TEMPLATE)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix in {".md", ".base", ".json", ".css", ""} or source.name.startswith("."):
            target.write_bytes(fill(source.read_text(encoding="utf-8"), today).encode("utf-8"))
        else:
            target.write_bytes(source.read_bytes())
        created.append(rel(target))
    from . import week
    week.create_if_missing(today)
    if not (config.VAULT / ".git").exists():
        git("init", "-q", "-b", "main")
    return created
