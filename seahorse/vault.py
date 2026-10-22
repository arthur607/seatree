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
    """Saves the edits made on this machine, then brings in what changed on other devices: a rebase, so the
    history stays a line. When both sides changed the same lines, it never gives up: it merges, `_resolve`
    keeps both sides of each file, and the merge is committed and pushed. The user hears about it once."""
    _end_unfinished()
    save_user_edits()
    if not has_remote():
        return
    fetched = git("fetch", "-q", check=False)
    if fetched.returncode != 0:
        log.warning("fetch failed: %s", fetched.stderr.strip())
        return
    if git("rev-parse", "-q", "--verify", "@{u}", check=False).returncode != 0:
        return  # no upstream yet: the setup pushes the first time
    if git(*SEAHORSE, "rebase", "-q", "@{u}", check=False).returncode == 0:
        _tell_later()
        return
    git("rebase", "--abort", check=False)
    merged = git(*SEAHORSE, "merge", "--no-edit", "-q", "@{u}", check=False)
    if merged.returncode != 0:
        _resolve_merge(merged)
    _tell_later()


def publish(message: str) -> bool:
    """Commits everything as Seahorse and pushes. True if there was something to commit. It also pushes what an
    earlier push left behind, so the server never stays ahead of the other devices."""
    git("add", "-A")
    committed = git("diff", "--cached", "--quiet", check=False).returncode != 0
    if committed:
        git(*SEAHORSE, "commit", "-q", "-m", message)
    if has_remote():
        _push()
    return committed


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


# ---- sync between devices -------------------------------------------------------------------------------

SEAHORSE = ("-c", "user.name=Seahorse", "-c", "user.email=seahorse@localhost")
SYNC_STATE = "sync.json"


def _end_unfinished() -> None:
    """A merge or a rebase that a crash left open is undone, so the next commit does not take it."""
    folder = config.VAULT / ".git"
    if (folder / "MERGE_HEAD").exists():
        git("merge", "--abort", check=False)
    if (folder / "rebase-merge").exists() or (folder / "rebase-apply").exists():
        git("rebase", "--abort", check=False)


def _push() -> None:
    """Pushes the commits the remote does not have. If another device pushed first, it pulls and tries again."""
    if git("rev-list", "--count", "@{u}..HEAD", check=False).stdout.strip() in ("", "0"):
        return
    for _ in range(3):
        result = git("push", "-q", check=False)
        if result.returncode == 0:
            return
        pull()
    log.error("push failed: %s", result.stderr.strip())


def _resolve_merge(merged: subprocess.CompletedProcess) -> None:
    """Resolves each file in conflict, commits the merge as Seahorse, syncs the tasks again and pushes."""
    out = git("-c", "core.quotepath=off", "diff", "--name-only", "--diff-filter=U", "-z", check=False).stdout
    conflicted = [p for p in out.split("\0") if p]
    if not conflicted:
        git("merge", "--abort", check=False)
        log.error("pull failed: %s", (merged.stderr or merged.stdout).strip())
        return
    copies = {path: copy for path in conflicted if (copy := _resolve(path))}
    git("add", "-A")
    git(*SEAHORSE, "commit", "-q", "--no-edit")
    log.warning("sync conflict resolved: %s", ", ".join(conflicted))
    from . import tasks
    tasks.sync()
    git("push", "-q", check=False)
    _tell(conflicted, copies)


def _stage(number: int, path: str) -> bytes | None:
    """A file as one side of a merge has it: 1 the common base, 2 this machine, 3 the other device."""
    result = subprocess.run(["git", "-C", str(config.VAULT), "show", f":{number}:{path}"], capture_output=True)
    return result.stdout if result.returncode == 0 else None


def merge_text(base: str, ours: str, theirs: str, union: bool = False) -> tuple[str, int]:
    """A three-way merge of texts with `git merge-file`. Returns the text and the number of conflicts. With
    `union`, a conflict keeps the lines of both sides."""
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        files = []
        for name, content in (("ours", ours), ("base", base), ("theirs", theirs)):
            file = Path(folder) / name
            file.write_bytes(content.encode("utf-8"))
            files.append(str(file))
        result = subprocess.run(["git", "merge-file", "-p", *(["--union"] if union else []), *files], capture_output=True)
    return result.stdout.decode("utf-8", errors="replace"), result.returncode


def _split_note(text: str) -> tuple[dict[str, str] | None, str]:
    """The frontmatter as {field: its lines} and the rest of the note. None when there is no frontmatter."""
    end = text.find("\n---", 4)
    if not text.startswith("---\n") or end == -1:
        return None, text
    found: dict[str, str] = {}
    key = ""
    for line in text[4:end].split("\n"):
        m = re.match(r"([^\s:#-][^:]*):", line)
        if m:
            key = m[1].strip()
            found[key] = line
        else:
            found[key] = f"{found[key]}\n{line}" if key in found else line
    return found, text[end + 4:]


def _merge_body(base: str, ours: str, theirs: str) -> str | None:
    if ours in (theirs, base):
        return theirs
    if theirs == base:
        return ours
    merged, conflicts = merge_text(base, ours, theirs)
    if conflicts == 0:
        return merged
    start = base.rstrip("\n")
    if ours.startswith(start) and theirs.startswith(start):  # both only added at the end, as in a log
        return theirs.rstrip("\n") + "\n" + ours[len(start):].lstrip("\n")
    return None


def merge_note(base: str, ours: str, theirs: str) -> str | None:
    """A note changed on both sides. The frontmatter merges field by field: a field changed on one side takes
    that change, and a field changed on both sides takes the other device's (the user's). The rest merges when
    the changes do not touch, or when both sides only added lines at the end. None when the changes collide."""
    base, ours, theirs = (x.replace("\r\n", "\n") for x in (base, ours, theirs))
    (b, b_rest), (o, o_rest), (t, t_rest) = _split_note(base), _split_note(ours), _split_note(theirs)
    if o is None or t is None:
        return _merge_body(base, ours, theirs)
    rest = _merge_body(b_rest if b is not None else base, o_rest, t_rest)
    if rest is None:
        return None
    b = b or {}
    keys = list(t) + [k for k in o if k not in t]
    front = [value for k in keys if (value := o.get(k) if t.get(k) == b.get(k) else t.get(k)) is not None]
    return "---\n" + "\n".join(front) + "\n---" + rest


def _copy_path(target: Path) -> Path:
    when = datetime.now(config.TIMEZONE)
    name = f"{target.stem} ({i18n.t('sync.copy_name')} {when:%Y-%m-%d %H%M})"
    copy, n = target.with_name(name + target.suffix), 2
    while copy.exists():
        copy, n = target.with_name(f"{name} {n}{target.suffix}"), n + 1
    return copy


def _resolve(path: str) -> str | None:
    """Resolves one file in conflict, without losing either side. Returns the conflict copy, if it made one.

    - Changed on one side, deleted on the other: the changed file stays.
    - A week file: the union of the lines of both sides, repaired by code (see `week.merge_conflict`).
    - A note: merged field by field and line by line (see `merge_note`).
    - Otherwise the other device's version (the user's edit) stays, and this machine's version (the agent's)
      becomes a conflict copy next to it."""
    base, ours, theirs = (_stage(n, path) for n in (1, 2, 3))
    target = config.VAULT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if ours is None or theirs is None:
        target.write_bytes(ours if theirs is None else theirs)
        return None
    try:
        b, o, t = ((x or b"").decode("utf-8").replace("\r\n", "\n") for x in (base, ours, theirs))
    except UnicodeDecodeError:
        b = o = t = None
    if b is not None and target.parent == config.WEEKS and re.fullmatch(r"\d{4}-W\d{2}", target.stem):
        from . import week
        union, _ = merge_text(b, o, t, union=True)
        target.write_bytes(week.merge_conflict(target, union, b, o, t).encode("utf-8"))
        return None
    if b is not None and target.suffix == ".md":
        merged = merge_note(b, o, t)
        if merged is not None:
            target.write_bytes(merged.encode("utf-8"))
            return None
    target.write_bytes(theirs)
    copy = _copy_path(target)
    copy.write_bytes(ours)
    return rel(copy)


def _sync_state() -> dict:
    try:
        return json.loads((config.STATE / SYNC_STATE).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_sync_state(state: dict) -> None:
    config.STATE.mkdir(parents=True, exist_ok=True)
    (config.STATE / SYNC_STATE).write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _tell(conflicted: list[str], copies: dict[str, str]) -> None:
    """Keeps a notice for the user about a resolved conflict: always when there is a conflict copy, and at most
    once a day when the two sides were simply kept. `_tell_later` sends it."""
    state = _sync_state()
    lines = [i18n.t("sync.copy", name=Path(p).stem, copy=Path(c).stem) for p, c in copies.items()]
    kept = [Path(p).stem for p in conflicted if p not in copies]
    today = datetime.now(config.TIMEZONE).date().isoformat()
    if kept and state.get("told") != today:
        lines.append(i18n.t("sync.merged", names=", ".join(kept)))
        state["told"] = today
    state["waiting"] = state.get("waiting", []) + lines
    _save_sync_state(state)


def _tell_later() -> None:
    """Sends the conflict notices that wait, outside the quiet hours."""
    state = _sync_state()
    if not state.get("waiting"):
        return
    from . import messenger, telegram_api
    if messenger.quiet(datetime.now(config.TIMEZONE)):
        return
    if telegram_api.send("\n\n".join(state["waiting"])):
        state["waiting"] = []
        _save_sync_state(state)
