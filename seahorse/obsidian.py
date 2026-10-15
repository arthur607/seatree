"""Opens the vault in Obsidian on the person's computer, at the end of the setup.

With GitHub sync, it clones the repository, keeps the GitHub login for later pushes, and adds the Obsidian Git
plugin, so the notes sync from the first minute. Then it adds the vault to Obsidian's list and opens it.
"""
import json
import os
import platform
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

import requests

PLUGIN = "https://github.com/Vinzent03/obsidian-git/releases/latest/download/"
PLUGIN_FILES = ("main.js", "manifest.json", "styles.css")


def default_folder() -> Path:
    documents = Path.home() / "Documents"
    return (documents if documents.is_dir() else Path.home()) / "Seahorse"


def _settings_file() -> Path:
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "obsidian/obsidian.json"
    if system == "Darwin":
        return Path.home() / "Library/Application Support/obsidian/obsidian.json"
    return Path.home() / ".config/obsidian/obsidian.json"


def installed() -> bool:
    """Obsidian makes its settings file the first time it opens."""
    return _settings_file().exists()


def running() -> bool:
    system = platform.system()
    if system == "Windows":
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Obsidian.exe"], capture_output=True, text=True).stdout
        return "obsidian.exe" in out.lower()
    return subprocess.run(["pgrep", "-xi", "obsidian"], capture_output=True).returncode == 0


def git() -> str | None:
    found = shutil.which("git")
    windows = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/cmd/git.exe"
    return found or (str(windows) if windows.exists() else None)


def install_git() -> bool:
    system = platform.system()
    if system == "Windows":
        command = ["winget", "install", "--id", "Git.Git", "-e", "--silent",
                   "--accept-package-agreements", "--accept-source-agreements"]
    elif system == "Darwin":
        command = ["brew", "install", "git"] if shutil.which("brew") else ["xcode-select", "--install"]
    else:
        command = ["sudo", "apt-get", "install", "-y", "-qq", "git"]
    subprocess.run(command, capture_output=True)
    return git() is not None


def clone(repo: str, token: str, login: str, folder: Path) -> str | None:
    """Clones `owner/name` with the GitHub token, and saves the token in the credential store of this computer,
    so the Obsidian Git plugin can push. Returns None, or the error."""
    command = git()
    url = f"https://github.com/{repo}.git"
    if not (folder / ".git").exists():
        result = subprocess.run([command, "-c", "credential.helper=", "clone", "-q",
                                 f"https://x-access-token:{token}@github.com/{repo}.git", str(folder)],
                                capture_output=True, text=True)
        if result.returncode != 0:
            return result.stderr.strip().replace(token, "***")[-300:]
    run = lambda *args, **kw: subprocess.run([command, "-C", str(folder), *args], capture_output=True, text=True, **kw)  # noqa: E731
    run("remote", "set-url", "origin", url)
    if not run("config", "--global", "credential.helper").stdout.strip():
        run("config", "credential.helper", "store")  # Linux with no credential store: a file only this user reads
    run("credential", "approve", input=f"protocol=https\nhost=github.com\nusername={login}\npassword={token}\n\n")
    run("config", "user.name", login)
    run("config", "user.email", f"{login}@users.noreply.github.com")
    return None


def add_plugin(folder: Path) -> bool:
    """Puts the Obsidian Git plugin in the vault of this computer. Git ignores these files: each device has its
    own copy of the plugin, and the settings of the plugin come with the vault."""
    target = folder / ".obsidian/plugins/obsidian-git"
    target.mkdir(parents=True, exist_ok=True)
    try:
        for name in PLUGIN_FILES:
            r = requests.get(PLUGIN + name, timeout=60)
            r.raise_for_status()
            (target / name).write_bytes(r.content)
        return True
    except requests.RequestException:
        return False


def register(folder: Path) -> None:
    """Adds the vault to the list of Obsidian. Obsidian must be closed: it writes this file when it closes."""
    path = _settings_file()
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    vaults = data.setdefault("vaults", {})
    for vault in vaults.values():
        vault.pop("open", None)
        if Path(vault.get("path", "")) == folder:
            vault["open"] = True
            break
    else:
        vaults[secrets.token_hex(8)] = {"path": str(folder), "ts": int(time.time() * 1000), "open": True}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def open_vault(folder: Path) -> None:
    from .machine import launch
    launch(f"obsidian://open?path={quote(str(folder))}")
