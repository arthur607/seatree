"""Updates: find a new release on GitHub, tell the user, install it, and migrate the vault.

A release is a git tag `vX.Y.Z` with notes. `seahorse update` installs it with uv, applies the vault
migrations of the versions in between, starts the service again and checks that it stays up. When a step fails,
it puts the previous version back. The bot checks once a day and sends the release notes when a new version is out.
"""
import importlib.util
import json
import logging
import shutil
import subprocess
import sys
from datetime import date

import requests

from . import __version__, config, i18n, service, telegram_api, vault

log = logging.getLogger(__name__)
REPO = "lorenzoa7/seahorse"
VAULT_VERSION = "6. ⚙️ System/.seahorse-version"
# The template files that the vault already got. An update adds only the template files that are new since then,
# so a file that the person deleted does not come back.
TEMPLATE_LIST = "6. ⚙️ System/.seahorse-template"
BASELINE = config.CODE / "template-1.2.0.txt"  # for vaults made before the list existed
UPDATE_BUTTON = "upd|now|"
HEALTH_SECONDS = 20  # how long a new version must stay up after the restart


def _parts(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.lstrip("v").split(".") if p.isdigit())


def newer(version: str, than: str = __version__) -> bool:
    return _parts(version) > _parts(than)


def latest() -> dict | None:
    """The newest release: {"version": "1.2.0", "notes": "..."}. None when GitHub cannot be reached."""
    try:
        r = requests.get(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=20)
        if r.status_code == 404:
            return {"version": "", "notes": ""}  # GitHub answered: there is no release yet
        if not r.ok:
            return None
        data = r.json()
        return {"version": data["tag_name"].lstrip("v"), "notes": data.get("body") or ""}
    except (requests.RequestException, KeyError, ValueError):
        return None


def release_notes(version: str, notes: str, language: str | None = None) -> str:
    """The notes of a release, for the update message. They are in English: other languages get a link."""
    if (language or config.LANGUAGE) == "en":
        return "\n".join(notes.strip().splitlines()[:12])
    return i18n.t("updates.notes_link", language, url=f"https://github.com/{REPO}/releases/tag/v{version}")


def notify_if_new() -> str:
    """Sends the release notes once per new version."""
    found = latest()
    if not found or not newer(found["version"]):
        return "up to date"
    marker = config.STATE / "notified-version"
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == found["version"]:
        return "already notified"
    notes = release_notes(found["version"], found["notes"])
    telegram_api.send(i18n.t("bot.update_available", version=found["version"], notes=notes),
                      [(i18n.t("button.update"), UPDATE_BUTTON)])
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(found["version"], encoding="utf-8")
    return f"notified {found['version']}"


def source(version: str) -> str:
    """The zip of a release on GitHub. uv installs it with no git on the machine."""
    return f"https://github.com/{REPO}/archive/refs/tags/v{version}.zip"


def install(version: str) -> None:
    """Installs one version of the code with uv, in place of the current one."""
    uv = shutil.which("uv") or str(config.HOME.parent / ".local/bin/uv")
    # Keep the local voice model when it is installed: a plain reinstall would remove it.
    local_voice = ["--with", "faster-whisper"] if importlib.util.find_spec("faster_whisper") else []
    subprocess.run([uv, "tool", "install", "--force", "--python", "3.12", *local_voice, source(version)],
                   check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- the template files of the vault

def template_files() -> list[str]:
    """The files of the vault template of this version, as paths in the vault."""
    return sorted(p.relative_to(config.TEMPLATE).as_posix() for p in config.TEMPLATE.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts)


def _names(path) -> set[str]:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def known_template_files() -> set[str]:
    """The template files that this vault already got: its own list, or the 1.2.0 template for an older vault."""
    own = config.VAULT / TEMPLATE_LIST
    return _names(own if own.exists() else BASELINE)


def add_new_template_files(today: date) -> list[str]:
    """Adds the template files that are new in this version and missing in the vault. A file that the vault got
    before and that the person deleted stays deleted. Existing files are never replaced."""
    known = known_template_files()
    added = []
    for name in template_files():
        target = config.VAULT / name
        if name in known or target.exists():
            continue
        source_file = config.TEMPLATE / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source_file.suffix in {".md", ".base", ".json", ".css", ""} or source_file.name.startswith("."):
            target.write_bytes(vault.fill(source_file.read_text(encoding="utf-8"), today).encode("utf-8"))
        else:
            target.write_bytes(source_file.read_bytes())
        added.append(name)
    return added


def mark_vault() -> None:
    """Writes the version of the vault (this version) and the list of its template files. A new vault gets them at
    once, so no migration of an older version runs on it."""
    marker = config.VAULT / VAULT_VERSION
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(__version__ + "\n", encoding="utf-8", newline="\n")
    (config.VAULT / TEMPLATE_LIST).write_text("\n".join(template_files()) + "\n", encoding="utf-8", newline="\n")


# ---------------------------------------------------------------- migrations

def migrate_vault(today: date | None = None) -> list[str]:
    """Applies what changed in the vault between versions: the template files that are new in this version, then
    the steps in MIGRATIONS of each version after the vault's version, oldest first. The version of the vault
    moves on only after all the steps of a version worked, so a failed step runs again next time."""
    from . import migrations, week
    if not (config.SYSTEM / "_AGENTS.md").exists():
        return []  # no vault here yet: the setup makes it
    today = today or date.today()
    marker = config.VAULT / VAULT_VERSION
    current = marker.read_text(encoding="utf-8").strip() if marker.exists() else "0.0.0"
    done = []
    with vault.lock():
        vault.pull()
        try:
            done += [f"added {p}" for p in add_new_template_files(today)]
            week.create_if_missing(today)
            versions = sorted({v for v, _ in migrations.MIGRATIONS}, key=_parts)
            for version in versions:
                if not newer(version, current) or newer(version, __version__):
                    continue
                for step_version, step in migrations.MIGRATIONS:
                    if step_version == version:
                        step()
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(version + "\n", encoding="utf-8", newline="\n")
                done.append(f"migrated to {version}")
            if not newer(current, __version__):  # older code never moves the version of a vault back
                mark_vault()
        finally:
            # Even after a failed step: what changed is Seahorse's, not an edit of the person.
            vault.publish(f"seahorse: vault at version {__version__}")
    return done


# ---------------------------------------------------------------- the update

def start_update_in_background() -> None:
    """For /update and its button: runs `seahorse update --yes` outside the bot, because the update restarts the
    bot. On Linux it runs as its own short systemd unit: a process started from the bot is in the bot's unit, so
    the restart would stop it before it says on Telegram that the update is done."""
    command = [service._binary(), "update", "--yes"]
    if sys.platform.startswith("linux") and shutil.which("systemd-run"):
        command = ["systemd-run", *([] if service._is_root() else ["--user"]), "--quiet", "--collect",
                   f"--setenv=SEAHORSE_HOME={config.HOME}", f"--setenv=PATH={service._path()}", *command]
    subprocess.Popen(command, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def short(error) -> str:
    """The last line of an error, short enough for a Telegram message."""
    text = error.stderr if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
    lines = [line.strip() for line in str(text).strip().splitlines() if line.strip()]
    return (lines[-1] if lines else "?")[:160]


def _give_up(version: str, detail: str, restart: bool) -> tuple[bool, str]:
    """Puts the previous version back after a failed update, and says so on Telegram."""
    try:
        install(__version__)
        back = True
    except (subprocess.CalledProcessError, OSError) as error:
        log.error("could not put back %s: %s", __version__, short(error))
        back = False
    if restart:
        service.restart()
    text = i18n.t("updates.failed_back" if back else "updates.failed", version=version, previous=__version__, detail=detail)
    telegram_api.send(text)
    return False, text


def update(assume_yes: bool = False) -> tuple[bool, str]:
    """Installs the newest release. Returns True when Seahorse runs the newest version, and what happened."""
    found = latest()
    if not found:
        return False, "Could not reach GitHub. Check the internet connection of this machine and try again."
    if not found["version"]:
        return True, f"There is no release on GitHub yet. You have Seahorse {__version__}."
    if not newer(found["version"]):
        return True, f"Seahorse is up to date ({__version__})."
    version = found["version"]
    if not assume_yes:
        print(f"Seahorse {version} is available (you have {__version__}).\n\n{found['notes']}\n")
        if input("Install it now? [Y/n] ").strip().lower() not in {"", "y", "yes", "s", "sim", "si"}:
            return True, "Nothing changed."
    was_running = service.status() in {"active", "running"}
    try:
        install(version)
    except (subprocess.CalledProcessError, OSError) as error:
        return _give_up(version, short(error), restart=False)
    # The new code migrates the vault. Until the restart, the old code keeps running.
    migrated = subprocess.run([service._binary(), "migrate"], capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
    if migrated.returncode != 0:
        return _give_up(version, short(migrated.stderr or migrated.stdout), restart=False)
    service.restart()
    if was_running and not service.stays_up(HEALTH_SECONDS):
        return _give_up(version, i18n.t("updates.not_started"), restart=True)
    telegram_api.send(i18n.t("updates.done", version=version))
    return True, f"Updated to {version}."


def state_summary() -> str:
    return json.dumps({"version": __version__, "repo": REPO}, indent=1)
