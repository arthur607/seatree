"""Updates: find a new release on GitHub, tell the user, install it, and migrate the vault.

A release is a git tag `vX.Y.Z` with notes. `seahorse update` installs it with uv, applies the vault
migrations of the versions in between, and restarts the service. The bot checks once a day and sends the
release notes when a new version is out.
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
SOURCE = f"git+https://github.com/{REPO}"
VAULT_VERSION = "6. ⚙️ System/.seahorse-version"


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


def notify_if_new() -> str:
    """Sends the release notes once per new version."""
    found = latest()
    if not found or not newer(found["version"]):
        return "up to date"
    marker = config.STATE / "notified-version"
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == found["version"]:
        return "already notified"
    notes = "\n".join(found["notes"].strip().splitlines()[:12])
    telegram_api.send(i18n.t("bot.update_available", version=found["version"], notes=notes))
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(found["version"], encoding="utf-8")
    return f"notified {found['version']}"


def install(version: str) -> None:
    """Installs one version of the code with uv, in place of the current one."""
    uv = shutil.which("uv") or str(config.HOME.parent / ".local/bin/uv")
    # Keep the local voice model when it is installed: a plain reinstall would remove it.
    local_voice = ["--with", "faster-whisper"] if importlib.util.find_spec("faster_whisper") else []
    subprocess.run([uv, "tool", "install", "--force", "--python", "3.12", *local_voice, f"{SOURCE}@v{version}"], check=True)


def migrate_vault(today: date | None = None) -> list[str]:
    """Applies what changed in the vault between versions. Files from the template that do not exist yet are
    added; a file that exists is never replaced. Version-specific changes go in MIGRATIONS."""
    from . import migrations
    today = today or date.today()
    marker = config.VAULT / VAULT_VERSION
    current = marker.read_text(encoding="utf-8").strip() if marker.exists() else "0.0.0"
    done = []
    with vault.lock():
        vault.pull()
        done += [f"added {p}" for p in vault.create(today)]
        for version, step in migrations.MIGRATIONS:
            if newer(version, current) and not newer(version, __version__):
                step()
                done.append(f"migrated to {version}")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(__version__ + "\n", encoding="utf-8")
        vault.publish(f"seahorse: vault at version {__version__}")
    return done


def start_update_in_background() -> None:
    """For the /update command: runs `seahorse update --yes` outside the bot, because it restarts the bot."""
    subprocess.Popen([sys.argv[0] if sys.argv[0].endswith("seahorse") else "seahorse", "update", "--yes"],
                     start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def update(assume_yes: bool = False) -> str:
    found = latest()
    if not found:
        return "Could not reach GitHub. Check the internet connection of this machine and try again."
    if not found["version"]:
        return f"There is no release on GitHub yet. You have Seahorse {__version__}."
    if not newer(found["version"]):
        return f"Seahorse is up to date ({__version__})."
    if not assume_yes:
        print(f"Seahorse {found['version']} is available (you have {__version__}).\n\n{found['notes']}\n")
        if input("Install it now? [Y/n] ").strip().lower() not in {"", "y", "yes", "s", "sim", "si"}:
            return "Nothing changed."
    try:
        install(found["version"])
        # The new code migrates the vault and restarts the service.
        subprocess.run(["seahorse", "migrate"], check=True)
        service.restart()
    except (subprocess.CalledProcessError, OSError) as error:
        telegram_api.send(i18n.t("bot.update_failed", detail=str(error)[:200]))
        return f"Update failed: {error}"
    telegram_api.send(i18n.t("bot.updated", version=found["version"]))
    return f"Updated to {found['version']}."


def state_summary() -> str:
    return json.dumps({"version": __version__, "repo": REPO}, indent=1)
