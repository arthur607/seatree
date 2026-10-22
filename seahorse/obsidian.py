"""Opens the vault in Obsidian on the person's computer, at the end of the setup.

With GitHub sync, it copies the repository (or brings the newest notes into a copy that is already there), keeps
the GitHub login in the safe credential store of the computer for later pushes, and adds the Obsidian Git plugin,
so the notes sync from the first minute. Then it adds the vault to Obsidian's list and opens it.
"""
import json
import os
import platform
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

PLUGIN = "https://github.com/Vinzent03/obsidian-git/releases/latest/download/"
PLUGIN_FILES = ("main.js", "manifest.json", "styles.css")
# Git gets the GitHub token from the environment, never from its command line, which other programs can read.
TOKEN_HELPER = '!f() { test "$1" = get && echo username=x-access-token && echo "password=$SEAHORSE_GITHUB_TOKEN"; }; f'
# Windows does not accept these characters, or a dot or a space at the end, in a file name.
WINDOWS_BAD = re.compile(r'[<>:"|?*\\\x00-\x1f]|[. ]$')
WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10))}


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


def mac_tools() -> bool:
    """True when the Command Line Tools of macOS are there. Without them, /usr/bin/git only asks to install them."""
    try:
        return subprocess.run(["xcode-select", "-p"], capture_output=True).returncode == 0
    except OSError:
        return False


def git() -> str | None:
    found = shutil.which("git")
    if found and platform.system() == "Darwin" and Path(found) == Path("/usr/bin/git") and not mac_tools():
        return None
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
    try:
        subprocess.run(command, capture_output=True)
    except OSError:
        return False
    return git() is not None


def repository(url: str) -> str:
    """"owner/name" of a GitHub address (https or ssh), in lowercase, to compare two addresses."""
    found = re.search(r"github\.com[:/]+([^/\s:]+/[^/\s]+?)(?:\.git)?/?$", url.strip())
    return found[1].lower() if found else ""


def windows_bad(path: str) -> bool:
    """True when Windows cannot have this path of the vault as a file, such as a note named "Why?.md"."""
    return any(WINDOWS_BAD.search(part) or part.split(".")[0].lower() in WINDOWS_RESERVED for part in path.split("/"))


def windows_path(folder: Path) -> str:
    """The path of a WSL folder for the apps of Windows, such as \\\\wsl.localhost\\Ubuntu\\home\\ana\\seahorse-vault."""
    return f"\\\\wsl.localhost\\{os.environ.get('WSL_DISTRO_NAME', 'Ubuntu')}" + str(folder).replace("/", "\\")


def _run(args: list[str], token: str | None = None, interactive: bool = False, input: str | None = None,
         ) -> subprocess.CompletedProcess:
    """Runs git. With `token`, git logs in to GitHub with it. `interactive`: git can ask for the GitHub login in
    this terminal (or in a window, on Windows), so only its errors are kept."""
    env, options = dict(os.environ), []
    if token:
        env["SEAHORSE_GITHUB_TOKEN"] = token
        options = ["-c", "credential.helper=", "-c", f"credential.helper={TOKEN_HELPER}"]
    if platform.system() == "Windows":
        options += ["-c", "core.longpaths=true"]  # deep folders and long note names
    return subprocess.run([git(), *options, *args], env=env, input=input, text=True, encoding="utf-8",
                          errors="replace", stdout=None if interactive else subprocess.PIPE, stderr=subprocess.PIPE)


def _error(result: subprocess.CompletedProcess, token: str | None) -> str:
    text = (result.stderr or "").strip()
    return (text.replace(token, "***") if token else text)[-300:] or f"git exit code {result.returncode}"


def _remove(folder: Path, whole: bool) -> None:
    """Deletes a failed copy: the folder, or only what is in it when the folder was there before (empty)."""
    def writable(function, path, *_):
        os.chmod(path, stat.S_IWRITE)  # git makes its object files read-only
        function(path)
    handler = {"onexc": writable} if sys.version_info >= (3, 12) else {"onerror": writable}
    for target in [folder] if whole else list(folder.iterdir()):
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target, **handler)
        else:
            target.unlink(missing_ok=True)


def copy_vault(repo: str, folder: Path, token: str | None = None, url: str | None = None) -> tuple[str, str]:
    """Copies the repository `owner/name` into `folder`, or brings the newest notes when the folder already has it.
    Without a token, git asks for the GitHub login itself. Returns ("", "") when it worked, else (problem, detail):
    "other_repo", "not_empty", "bad_names", "pull_failed" or "failed". `url` replaces GitHub in the tests."""
    url = url or f"https://github.com/{repo}.git"
    if (folder / ".git").exists():
        origin = _run(["-C", str(folder), "remote", "get-url", "origin"]).stdout.strip()
        if (repository(origin) != repository(url)) if repository(url) else origin != url:
            return "other_repo", origin or "?"
        result = _run(["-C", str(folder), "pull", "-q", "--ff-only"], token, interactive=not token)
        return ("", "") if result.returncode == 0 else ("pull_failed", _error(result, token))
    if folder.exists() and any(folder.iterdir()):
        return "not_empty", str(folder)
    made = not folder.exists()
    result = _run(["clone", "-q", url, str(folder)], token, interactive=not token)
    if result.returncode == 0:
        if platform.system() == "Windows":
            _run(["-C", str(folder), "config", "core.longpaths", "true"])
        return "", ""
    # The copy can come but stop at a note whose name cannot be a file here (Git says "invalid path").
    bad = re.findall(r"invalid path '([^']+)'", result.stderr or "")
    if (folder / ".git").exists() and platform.system() == "Windows":
        names = _run(["-C", str(folder), "-c", "core.quotepath=off", "ls-tree", "-r", "--name-only", "HEAD"]).stdout
        bad = [name for name in names.splitlines() if windows_bad(name)] or bad
    if folder.exists():
        _remove(folder, made)
    if bad:
        return "bad_names", "\n".join(f"  • {name}" for name in bad[:20])
    return "failed", _error(result, token)


def gh_login() -> tuple[str, str]:
    """The token and the user name of the GitHub CLI on this computer, when it is logged in. Else ("", "")."""
    gh = shutil.which("gh")
    if not gh:
        return "", ""
    try:
        token = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=30).stdout.strip()
        login = subprocess.run([gh, "api", "user", "--jq", ".login"], capture_output=True, text=True,
                               timeout=30).stdout.strip() if token else ""
    except (OSError, subprocess.TimeoutExpired):
        return "", ""
    return (token, login) if token and login else ("", "")


def origin(folder: Path) -> str:
    """The address of the repository that a copy comes from, or "" when it has none."""
    if not (folder / ".git").exists() or not git():
        return ""
    return _run(["-C", str(folder), "remote", "get-url", "origin"]).stdout.strip()


def safe_store(folder: Path) -> bool:
    """True when git on this computer keeps logins in a safe store (such as the Windows Credential Manager or the
    macOS keychain), at any level of its settings. git's "store" is a plain file, and "cache" forgets the login
    after some minutes, so they do not count."""
    found = _run(["-C", str(folder), "config", "--get-all", "credential.helper"]).stdout.splitlines()
    helpers = [line.split()[0] for line in found if line.strip()]
    return bool(helpers) and not {"store", "git-credential-store", "cache", "git-credential-cache"} & set(helpers)


def keep_login(folder: Path, login: str, token: str) -> bool:
    """Saves the GitHub login in the safe credential store of this computer, so the Obsidian Git plugin can push.
    Never in a plain file: False when this computer has no safe store."""
    run = lambda *args, **kw: _run(["-C", str(folder), *args], **kw)  # noqa: E731
    if login:
        run("config", "user.name", login)
        run("config", "user.email", f"{login}@users.noreply.github.com")
    if not safe_store(folder):
        return False
    run("credential", "approve", input=f"protocol=https\nhost=github.com\nusername={login or 'x-access-token'}\n"
                                       f"password={token}\n\n")
    return True


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
