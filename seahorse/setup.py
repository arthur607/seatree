"""`seahorse setup`: the interactive setup.

It runs on the person's own computer (Windows, macOS or Linux), also when Seahorse will run on a server: then it
sends each step to the server through ssh. The logins run where Seahorse runs, but their links open in the
browser of the computer and their one-time codes go to the clipboard. At the end it opens the vault in Obsidian
on this computer.

It asks one thing at a time and saves each answer at once, so Ctrl+C loses nothing. It starts in English. After
the first question it speaks the chosen language. The texts are short: one instruction for each action. When
something fails, it says what failed, with the real error, and what to do.
"""
import copy
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import tomllib
import unicodedata
from contextlib import contextmanager, nullcontext
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import questionary
import requests

from . import __version__, config, i18n
from .machine import Local, Server, asked_user, launch, ssh_detail, ssh_problem, wsl

STEPS = 9
INSTALL_SCRIPT = "https://raw.githubusercontent.com/lorenzoa7/seahorse/main/install.sh"
GUIDE = "https://github.com/lorenzoa7/seahorse/blob/main/docs/phone.md"
SERVER_GUIDE = "https://github.com/lorenzoa7/seahorse/blob/main/docs/server.md"
TOKEN_PAGE = "https://github.com/settings/personal-access-tokens/new"
TELEGRAM_WAIT = 600  # seconds to wait for the first message, after the nudge
# `gh auth token` needs gh 2.17 and `gh auth logout --user` needs 2.40. The gh of Ubuntu 22.04 is 2.4.
GH_MIN = (2, 40)
GH_APT = ("bash -c 'set -e; mkdir -p -m 755 /etc/apt/keyrings; "
          "curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli-archive-keyring.gpg; "
          "chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg; "
          "echo \"deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] "
          "https://cli.github.com/packages stable main\" > /etc/apt/sources.list.d/github-cli.list; "
          "apt-get update -qq; apt-get install -y -qq gh'")
# macOS does not let a background program read these folders of the home folder.
MAC_BLOCKED = ("Documents", "Desktop", "Downloads", "Library/Mobile Documents")
# The steps that a second run of the setup can change alone, with the title of each.
CHANGES = {"about": "name_title", "ai": "ai_title", "telegram": "telegram_title", "voice": "voice_title",
           "routine": "routine_title", "vault": "vault_title", "obsidian": "obsidian_title"}
# Common time zones per language, so nobody has to type "America/Sao_Paulo".
ZONES = {
    "pt-BR": ["America/Sao_Paulo", "America/Manaus", "America/Recife", "America/Cuiaba", "America/Rio_Branco",
              "Europe/Lisbon"],
    "es": ["Europe/Madrid", "America/Mexico_City", "America/Bogota", "America/Argentina/Buenos_Aires",
           "America/Santiago", "America/Lima", "America/Caracas"],
    "en": ["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "Europe/London",
           "Europe/Berlin", "Asia/Kolkata", "Asia/Tokyo", "Australia/Sydney"],
}
# Only the line under the arrow is in color. Without "noreverse", some terminals keep the first default line
# in reverse video while the arrow moves.
STYLE = questionary.Style([
    ("qmark", "fg:ansicyan bold"), ("question", "bold"), ("answer", "fg:ansicyan bold"),
    ("pointer", "fg:ansicyan bold"), ("highlighted", "fg:ansicyan bold noreverse"), ("selected", "noreverse"),
    ("instruction", "fg:ansibrightblack"),
])

# questionary marks the default line as "selected" for good, and that class wins over the highlight of the
# arrow, so the first line never looked like the line under the arrow. The setup asks single choices only.
# This changes a private part of questionary: if a new version changes it, the menus still work.
try:
    from questionary.prompts import common as _common
    _common.InquirerControl._is_selected = lambda self, choice: bool(choice.checked)
except (ImportError, AttributeError):
    pass

M: Local | Server = Local()  # the machine that runs Seahorse
BROWSER = True  # False when the setup runs on the server itself, where no browser can open
SECRETS: dict[str, str] = {}
GITHUB: dict[str, str] = {}  # the login and token of GitHub, kept only while the setup runs
PERSON = ""  # the first name given for a new person on a server
MOVING = False  # Seahorse ran on this computer, and moves to a server now
MOVED_FROM: Path | None = None  # the vault folder on this computer, before the move
PASTE = "Cmd+V" if sys.platform == "darwin" else "Ctrl+V"


# ---------------------------------------------------------------- talking
# The text names come first and only by position, so a text can have any {field}, even {name}.

def _t(text: str, /, **values) -> str:
    return i18n.t(f"setup.{text}", **({"paste": PASTE} | values))


def _say(text: str) -> None:
    print(f"\n{text}")


def _notice(text: str, /, **values) -> None:
    print(f"\n\033[1m  → {_t(text, **values)}\033[0m", flush=True)


def _title(n: int, text: str, /) -> None:
    print(f"\n\033[1m━━ {_t('step', n=n, total=STEPS, title=_t(text))} ━━\033[0m")


def _drain() -> None:
    """Throws away the keys pressed while the setup was busy, so they do not answer the next question."""
    try:
        if sys.platform == "win32":
            import msvcrt
            while msvcrt.kbhit():
                msvcrt.getwch()
        else:
            import termios
            termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except Exception:  # noqa: BLE001 - no terminal: nothing to throw away
        pass


def _must(answer):
    if answer is None:  # Ctrl+C: what was answered is already saved
        _say(_t("stopped"))
        sys.exit(1)
    return answer


def _ask(text: str, /, default: str = "", validate=None, **values) -> str:
    _drain()
    question = questionary.text(_t(text, **values), default=str(default or ""), validate=validate, style=STYLE)
    return _must(question.ask()).strip()


def _secret(text: str, /) -> str:
    _drain()
    return _must(questionary.password(_t(text), style=STYLE).ask()).strip()


def _choose(text: str, /, options: dict[str, str], default: str | None = None) -> str:
    """`options` maps the value to save to the label to show."""
    _drain()
    choices = [questionary.Choice(label, value=value) for value, label in options.items()]
    return _must(questionary.select(_t(text), choices=choices, default=default if default in options else None,
                                    style=STYLE).ask())


def _confirm(text: str, /, default: bool = True, **values) -> bool:
    _drain()
    return _must(questionary.confirm(_t(text, **values), default=default, style=STYLE).ask())


def _wait(text: str = "press_enter", /) -> None:
    _drain()
    _must(questionary.text(_t(text), default="", style=STYLE).ask())


@contextmanager
def _working(text: str, /, **values):
    """A spinner while the setup waits for the network or the server, so the person knows it did not stop."""
    label, done = _t(text, **values), threading.Event()

    def spin():
        frames, n = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", 0
        while not done.wait(0.1):
            print(f"\r  {frames[n % len(frames)]} {label}", end="", flush=True)
            n += 1
    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join()
        print("\r\033[2K", end="", flush=True)


def _open(url: str) -> None:
    """Opens a page in the browser of this computer. The link always shows: with no browser, the person opens it."""
    if BROWSER and launch(url):
        _notice("link_opened_url", url=url)
    else:
        _notice("link_open_yourself", url=url)


def _forget_screen(step: int, title: str) -> None:
    """Clears the screen and its scrollback, so that a token a login printed does not stay there."""
    print("\033[2J\033[3J\033[H", end="", flush=True)
    _title(step, title)


def _last_line(result) -> str:
    lines = (result.stdout or "").strip().splitlines() or (result.stderr or "").strip().splitlines() or [""]
    return lines[-1]


def clean_path(text: str) -> str:
    """A path as people paste it: in quotes (Windows "Copy as path"), or with a backslash before each space (a
    folder dragged into the Terminal of macOS)."""
    text = text.strip()
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1]
    return text if sys.platform == "win32" else re.sub(r"\\(.)", r"\1", text)


def linux_name(name: str) -> str:
    """The Linux user name for a name: letters and digits, with no accents ("João" gives "joao"). "" when the name
    cannot be one, such as two words."""
    plain = unicodedata.normalize("NFKD", name.strip()).encode("ascii", "ignore").decode().lower()
    return plain if re.fullmatch(r"[a-z][a-z0-9]{0,19}", plain) else ""


def in_quiet(moment: str, start: str, end: str) -> bool:
    """True when a time ("HH:MM") is in the quiet hours from `start` to `end`. They can pass midnight; the same
    time twice means no quiet hours."""
    minutes = [int(text.split(":")[0]) * 60 + int(text.split(":")[1]) for text in (moment, start, end)]
    now, first, last = minutes
    if first == last:
        return False
    return first <= now < last if first < last else (now >= first or now < last)


def mac_blocked(folder: Path, home: Path | None = None) -> bool:
    """True when the folder is in a part of the home folder that macOS protects from background programs."""
    home = home or Path.home()
    return any(folder == home / name or home / name in folder.parents for name in MAC_BLOCKED)


def _online(url: str) -> bool:
    try:
        requests.get(url, timeout=10)
        return True
    except requests.RequestException:
        return False


# ---------------------------------------------------------------- saving

def _parse_secrets(text: str | None) -> dict[str, str]:
    found = {}
    for line in (text or "").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            found[key.strip()] = value.strip()
    return found


def _save(settings: dict, secrets: dict | None = None) -> None:
    """Writes config.toml and secrets.env on the machine of Seahorse after every step."""
    with _working("saving"):
        M.write_file("config.toml", config.dumps(settings))
        if secrets is not None:
            SECRETS.update({k: v for k, v in secrets.items() if v})
            M.write_file("secrets.env", "".join(f"{k}={v}\n" for k, v in SECRETS.items()), private=True)


def _load() -> dict:
    """The settings on the machine of Seahorse. A config.toml with an error gives the defaults: the setup asks
    each thing again and writes a good file."""
    with _working("checking"):
        text = M.read_file("config.toml")
        secrets = M.read_file("secrets.env")
    try:
        data = tomllib.loads(text) if text else {}
    except tomllib.TOMLDecodeError as error:
        _say(_t("config_broken", detail=f"~/.seahorse/config.toml ({M.label}): {error}"))
        data = {}
    settings = config._merge(copy.deepcopy(config.DEFAULTS), data)
    if M.remote and not data.get("vault", {}).get("path"):  # never the default path of this computer
        settings["vault"]["path"] = "~/seahorse-vault"
    SECRETS.clear()
    SECRETS.update(_parse_secrets(secrets))
    return settings


def _server_file() -> dict:
    """What this computer remembers of the server it set up: host, admin, user and language."""
    return config.read_toml(config.HOME / "server.toml")[0].get("server", {})


def _install(tool: str, linux: str, mac: str, hint: str, system: bool = False) -> bool:
    """`system`: the Linux command changes the system (apt-get, npm -g), so it runs as root."""
    with _working("installing", tool=tool):
        if M.system == "Darwin":
            result = M.sh(mac)
        else:
            result = M.root(linux) if system else M.sh(linux)
    if result.returncode != 0:
        _say(_t("install_failed", tool=tool, hint=hint, detail=_last_line(result)))
    return result.returncode == 0


# ---------------------------------------------------------------- 1. where

def _on_a_server_now() -> bool:
    """True when the person runs the setup on the server itself (through ssh, or on a machine with no screen).
    WSL has no screen of its own, but it opens links in the browser of Windows."""
    no_screen = sys.platform.startswith("linux") and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY")
    return bool(os.environ.get("SSH_CONNECTION")) or (no_screen and not wsl())


def _check_local() -> None:
    """Seahorse keeps the notes with git. On a Mac, git comes with the Command Line Tools of Apple."""
    from . import obsidian
    if sys.platform == "darwin" and not obsidian.mac_tools():
        _say(_t("mac_tools"))
        subprocess.run(["xcode-select", "--install"], capture_output=True)  # opens the window of Apple
        sys.exit(1)
    if not shutil.which("git") and not _install("Git", "apt-get install -y -qq git", "xcode-select --install",
                                                "https://git-scm.com", system=True):
        sys.exit(1)


def _where() -> None:
    global M, BROWSER, MOVING
    _title(1, "where_title")
    if _on_a_server_now():
        M, BROWSER = Local(), False
        _say(_t("where_here_server"))
        _check_local()
        return
    here = config.CONFIG_FILE.exists()  # Seahorse runs on this computer now
    computer = _t("where_computer_windows") if sys.platform == "win32" else _t("where_computer")
    if _choose("where_ask", {"server": _t("where_server"), "computer": computer}, "computer" if here else "server") == "computer":
        if sys.platform == "win32":
            _say(_t("windows_wsl"))
            sys.exit(1)
        server = _server_file().get("host")
        if server and not here and not _confirm("local_while_server", default=False, host=server):
            _say(_t("nothing_changed"))
            sys.exit(0)
        M = Local()
        _check_local()
        _say(_t("pc_warning_wsl" if wsl() else "pc_warning"))
        return
    if not shutil.which("ssh"):
        _say(_t("ssh_missing"))
        sys.exit(1)
    if here:
        if not _confirm("move_ask"):
            _say(_t("move_no"))
            sys.exit(0)
        MOVING = True
    _connect_server()


def _connect_server() -> None:
    global M
    local = Local()
    remembered = _server_file()
    address = remembered.get("host", "")
    if address and remembered.get("admin", "root") != "root":
        address = f"{remembered['admin']}@{address}"
    server = None
    while not server:
        address = _ask("server_address", address, validate=lambda v: bool(v.strip()) or _t("required"))
        server = _open_server(address)
    print(_t("server_ok", host=server.host))
    with _working("checking"):
        systems, name = server.system_name()
        admin = server.can_admin()
    if not systems & {"ubuntu", "debian"}:
        _say(_t("server_not_ubuntu", name=name))
        sys.exit(1)
    # Root, or a user with sudo, makes a Linux user for each person. Another user runs Seahorse itself.
    M = _person(server, remembered.get("user")) if admin else server
    local.write_file("server.toml", config.dumps({"server": {"host": server.host, "admin": server.user, "user": M.user,
                                                             "language": config.LANGUAGE}}))
    _install_on_server()


def _key_file() -> Path:
    """Asks for the key file of the server, and copies it into ~/.seahorse with mode 600, as ssh wants. The setup
    deletes the copy after it connects."""
    def exists(value: str):
        return Path(clean_path(value)).expanduser().is_file() or _t("server_key_file_bad")
    source = Path(clean_path(_ask("server_key_file", validate=exists))).expanduser()
    config.HOME.mkdir(parents=True, exist_ok=True)
    target = config.HOME / "login_key"
    handle = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    if hasattr(os, "fchmod"):
        os.fchmod(handle, 0o600)
    with os.fdopen(handle, "wb") as file:
        file.write(source.read_bytes())
    return target


def _open_server(address: str) -> Server | None:
    """Lets the setup key into the server, once, with the password or a key file. Returns the server. Returns None
    after it says, in plain words, what is wrong."""
    user, _, host = address.strip().rpartition("@")
    server, key_file, error, added = Server(host.strip(), user.strip() or "root"), None, "", False
    try:
        for _ in range(6):
            with _working("connecting", host=server.host):
                error = server.check()
            if not error:
                return server
            problem = ssh_problem(error)
            if problem == "host_changed":
                if not _confirm("server_host_changed", default=False, host=server.host):
                    return None
                server.forget_host()
                continue
            if problem in {"unknown_host", "unreachable"}:
                _say(_t(f"server_{problem}", host=server.host, detail=ssh_detail(error)))
                return None
            if added:  # the key went in, and still does not open the server
                break
            # The setup key does not open the server yet: the person lets it in once.
            _say(_t("server_key_login" if key_file else "server_password"))
            error = server.add_key(key_file)
            problem = ssh_problem(error)
            added = not problem
            if problem == "password_change":
                _say(_t("server_password_changed"))
            elif problem == "other_user" and asked_user(error):
                server = Server(server.host, asked_user(error))
                _say(_t("server_other_user", user=server.user))
            elif problem == "key_only" and not key_file:
                _say(_t("server_key_only"))
                key_file = _key_file()
                if server.user == "root":
                    user = _ask("server_user", "ubuntu", validate=lambda v: bool(re.fullmatch(r"[a-z_][a-z0-9_.-]*", v.strip()))
                                or _t("required"))
                    server = Server(server.host, user)
            elif problem in {"key_only", "password"}:
                _say(_t("server_key_refused" if key_file else "server_password_wrong", detail=ssh_detail(error)))
                return None
            elif problem in {"unknown_host", "unreachable"}:
                _say(_t(f"server_{problem}", host=server.host, detail=ssh_detail(error)))
                return None
        _say(_t("server_failed", host=server.host, detail=ssh_detail(error)))
        return None
    finally:
        if key_file:
            key_file.unlink(missing_ok=True)


def _person(admin: Server, remembered: str | None) -> Server:
    """Whose Seahorse this is. Each person has their own Linux user on the server (seahorse-ana), so two people
    can share one server: their notes, tokens and bots stay apart."""
    global PERSON
    with _working("checking"):
        people = [user for user in admin.people() if user != "root" or admin.user == "root"]
    choice = "new"
    if people:
        options = {user: _t("person_existing", user=user) for user in people} | {"new": _t("person_new")}
        choice = _choose("person_ask", options, remembered if remembered in people else people[0])
    if choice == admin.user:  # an install from before people had their own users
        return admin
    if choice == "new":
        PERSON = _ask("person_name", validate=lambda v: bool(linux_name(v)) or _t("person_bad"))
        PERSON = PERSON[:1].upper() + PERSON[1:]
        choice = f"seahorse-{linux_name(PERSON)}"
    with _working("person_making", user=choice):
        error = admin.make_person(choice)  # for a person already there, it only lets this computer in
    if error:
        _say(_t("person_failed", user=choice, detail=error))
        sys.exit(1)
    return Server(admin.host, choice, admin)


def _install_on_server() -> None:
    from .updates import newer
    with _working("checking"):
        there = M.sh("seahorse --version").stdout.strip().removeprefix("seahorse ")
    source = os.environ.get("SEAHORSE_SOURCE")  # a local copy of the code, to test it before a release
    if not source and there and newer(there):  # never put an older version on the server
        _say(_t("server_newer", server=there, here=__version__))
        return
    if not source and there == __version__:
        return
    with _working("installing", tool="Seahorse"):
        if source:
            M.upload_source(Path(source))
            result = M.sh("SEAHORSE_SOURCE_DIR=~/.seahorse/source SEAHORSE_NO_SETUP=1 bash ~/.seahorse/source/install.sh")
        else:
            result = M.sh(f"curl -fsSL {INSTALL_SCRIPT} | SEAHORSE_NO_SETUP=1 SEAHORSE_VERSION=v{__version__} bash")
    if result.returncode != 0 or not M.which("seahorse"):
        _say(_t("install_failed", tool="Seahorse", hint=INSTALL_SCRIPT, detail=_last_line(result)))
        sys.exit(1)
    print(_t("installed", tool="Seahorse"))


def _move_to_server() -> None:
    """Seahorse ran on this computer, and moves to the server: its settings, tokens and notes go there, and it stops
    here, so two copies never answer the same bot. This computer then only drives the server. When a part of the
    move fails, Seahorse runs on this computer again, and nothing here is deleted."""
    from . import service
    with _working("move_stopping"):
        service.stop()  # no new notes while they move
    with _working("move_notes"):
        error = _move_settings() or _move_vault()
    if error:
        service.install()
        _say(_t("move_failed", detail=error))
        sys.exit(1)
    service.uninstall()
    # Without its own config.toml, this computer drives the server: `seahorse logs` and the others act there.
    config.CONFIG_FILE.replace(config.CONFIG_FILE.with_name("config.toml.moved"))
    config.SECRETS_FILE.unlink(missing_ok=True)
    print(_t("move_done"))


def _move_settings() -> str:
    """Copies the settings, the tokens and the state of this computer to a new Seahorse on the server. A Seahorse
    that is already there keeps its own. Returns "" or the error."""
    if M.read_file("config.toml"):
        return ""
    settings = config._merge(copy.deepcopy(config.DEFAULTS), config.read_toml(config.CONFIG_FILE)[0])
    settings["vault"]["path"] = "~/seahorse-vault"
    secrets = config.SECRETS_FILE.read_text(encoding="utf-8") if config.SECRETS_FILE.exists() else ""
    if secrets and not M.write_file("secrets.env", secrets, private=True):
        return "could not write secrets.env on the server"
    if config.STATE.is_dir() and M.upload(config.STATE, "~/.seahorse/state").returncode != 0:
        return "could not copy ~/.seahorse/state to the server"
    # config.toml comes last: when it is there, a second run knows that the settings are on the server.
    return "" if M.write_file("config.toml", config.dumps(settings)) else "could not write config.toml on the server"


def shell_path(path: str) -> str:
    """A path for the shell of the server, in quotes. A ~ at the start stays out of the quotes, so that it still
    means the home folder."""
    if path == "~" or path.startswith("~/"):
        return '"$HOME"' + (f"/{shlex.quote(path[2:])}" if path[2:] else "")
    return shlex.quote(path)


def _move_vault() -> str:
    """Puts the vault of this computer on the server: a clone of its GitHub repository with the same deploy key,
    or a copy of the folder. Returns "" or the error."""
    global MOVED_FROM
    from . import vault
    if not (config.SYSTEM / "_AGENTS.md").exists():
        return ""  # no notes on this computer
    MOVED_FROM = config.VAULT
    there = config.read_toml_text(M.read_file("config.toml") or "").get("vault", {}).get("path") or "~/seahorse-vault"
    if M.sh(f"test -e {shell_path(there)}/.git").returncode == 0:
        return ""  # the server has the notes already
    vault.save_user_edits()
    if vault.has_remote():
        url = vault.git("remote", "get-url", "origin", check=False).stdout.strip()
        vault.git("push", "-q", check=False)  # the last changes on this computer go first
        key = config.HOME / "vault_key"
        if key.exists() and not (M.write_file("vault_key", key.read_text(encoding="utf-8"), private=True)
                                 and M.write_file("vault_key.pub", key.with_suffix(".pub").read_text(encoding="utf-8"))):
            return "could not copy the deploy key to the server"
        result = M.seahorse("provision", "clone", url)
        return "" if result.returncode == 0 else _last_line(result)
    result = M.upload(config.VAULT, shell_path(there))
    return "" if result.returncode == 0 else (result.stderr.decode("utf-8", "replace").strip()[-300:] or "tar")


# ---------------------------------------------------------------- 2. about you

def _detect_zone() -> str | None:
    """The time zone of the person: the clock of this computer, or the place of their internet connection.
    When the setup runs on the server itself, the place comes from the IP of the ssh connection, because the
    clock of a server is usually UTC."""
    found = []
    if BROWSER:
        found.append(os.environ.get("TZ", ""))
        if sys.platform != "win32":
            found.append(os.path.realpath("/etc/localtime").partition("zoneinfo/")[2])
        urls = ["https://ipinfo.io/timezone"]
    else:
        ip = os.environ.get("SSH_CONNECTION", "").split(" ")[0]
        urls = [f"https://ipinfo.io/{ip}/timezone", f"https://ipapi.co/{ip}/timezone/"] if ip else []
    for zone in found:
        if "/" in zone and not zone.startswith("Etc/"):
            return zone
    for url in urls:
        try:
            zone = requests.get(url, timeout=5, headers={"User-Agent": "seahorse"}).text.strip()
            ZoneInfo(zone)
            if "/" in zone:
                return zone
        except Exception:  # noqa: BLE001 - no internet, or an error page instead of a zone name
            pass
    return None


def _about_you(settings: dict) -> None:
    _title(2, "name_title")
    settings["user"]["name"] = _ask("name_ask", settings["user"]["name"] or PERSON, validate=lambda v: bool(v.strip()) or _t("required"))
    zones = ZONES.get(config.LANGUAGE, ZONES["en"])
    current = settings["user"]["timezone"]
    detected = _detect_zone()

    def label(zone: str) -> str:
        return zone.split("/")[-1].replace("_", " ") + f"  ({zone})"
    options = {detected: f"{label(detected)}  · {_t('timezone_detected')}"} if detected else {}
    for zone in ([current] if current != "UTC" else []) + zones:
        options.setdefault(zone, label(zone))
    options["other"] = _t("timezone_other")
    zone = _choose("timezone_ask", options, current if current != "UTC" else (detected or zones[0]))
    if zone == "other":
        def valid(value: str):
            try:
                ZoneInfo(value.strip())
                return True
            except (ZoneInfoNotFoundError, ValueError):
                return _t("timezone_bad")
        zone = _ask("timezone_type", validate=valid)
    settings["user"]["timezone"] = zone
    print(_t("timezone_check", time=f"{datetime.now(ZoneInfo(zone)):%H:%M}"))
    _save(settings)


# ---------------------------------------------------------------- 3. the AI

def _claude() -> None:
    """Installs the native Claude Code of this user when it is missing, never an old global one: an old Claude
    Code breaks on an old Node.js."""
    claude = "curl -fsSL https://claude.ai/install.sh | bash"
    with _working("checking"):
        missing = M.sh("test -x ~/.local/bin/claude").returncode != 0
    if missing and not _install("Claude Code", claude, claude, "https://docs.claude.com/en/docs/claude-code/setup"):
        sys.exit(1)


def _ai(settings: dict) -> None:
    _title(3, "ai_title")
    current = settings["ai"]["provider"]
    with _working("checking"):
        if current == "claude":
            logged_in = bool(SECRETS.get("CLAUDE_CODE_OAUTH_TOKEN") or SECRETS.get("ANTHROPIC_API_KEY"))
        else:
            logged_in = M.sh("test -f ~/.codex/auth.json").returncode == 0 and M.which("codex")
    if logged_in and current == "claude":
        _claude()  # a token that came from another machine needs Claude Code here too
    if not (logged_in and _confirm("keep_ai", provider=current.capitalize())):
        _ai_login(settings)
    while not _test_ai():
        if not _confirm("ai_retry"):
            _say(_t("stopped"))
            sys.exit(1)
        _ai_login(settings)


def _ai_login(settings: dict) -> None:
    which = _choose("ai_ask", {"claude": "Claude", "codex": "ChatGPT (Codex)"}, settings["ai"]["provider"])
    how = _choose("ai_how", {"sub": _t("ai_subscription"), "api": _t("ai_api_key")}, "sub")
    choice = f"{which}-{how}"
    secrets = {}
    guide = "_browser" if BROWSER else "_server"
    if choice.startswith("claude"):
        _claude()
        if choice == "claude-sub":
            _say(_t("claude_token" + guide))
            found = re.findall(r"sk-ant-oat01-[\w-]{20,}", M.login("claude setup-token", _notice))
            if found:
                _forget_screen(3, "ai_title")  # the token was on the screen
                _notice("token_caught")
            secrets["CLAUDE_CODE_OAUTH_TOKEN"] = found[-1] if found else _secret("paste_claude_token")
            SECRETS.pop("ANTHROPIC_API_KEY", None)  # an API key would win over the subscription
        else:
            _say(_t("claude_key"))
            _open("https://console.anthropic.com/settings/keys")
            secrets["ANTHROPIC_API_KEY"] = _secret("paste_key")
            SECRETS.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        provider = "claude"
    else:
        with _working("checking"):
            missing, no_npm = not M.which("codex"), not M.which("npm")
        if missing:
            # Ubuntu's own Node.js can be too old for Codex, so take a current one from NodeSource.
            node = "bash -c 'curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y -qq nodejs'"
            if no_npm and not _install("Node.js", node, "brew install node", "https://nodejs.org", system=True):
                sys.exit(1)
            if not _install("Codex CLI", "npm install -g @openai/codex", "npm install -g @openai/codex",
                            "https://developers.openai.com/codex/cli", system=True):
                sys.exit(1)
        if choice == "codex-sub":
            _say(_t("codex_login" + guide))
            # On this computer the browser logs in by itself. On a server, Codex shows a code instead.
            M.login("codex login" if not M.remote and BROWSER else "codex login --device-auth", _notice, typing=False)
        else:
            _say(_t("codex_key"))
            _open("https://platform.openai.com/api-keys")
            M.sh("codex login --with-api-key", input=_secret("paste_key"))
        provider = "codex"
    settings["ai"]["provider"] = provider
    _save(settings, secrets)


def _test_ai() -> bool:
    with _working("ai_testing"):
        result = M.seahorse("provision", "test-ai")
    if result.returncode != 0:
        _say(_t("ai_failed", detail=_last_line(result)))
        return False
    print(_t("ai_ok"))
    return True


# ---------------------------------------------------------------- 4. Telegram

def _telegram(settings: dict) -> None:
    from . import telegram_api
    _title(4, "telegram_title")
    token = SECRETS.get("TELEGRAM_BOT_TOKEN", "")
    with _working("checking"):
        bot = telegram_api.bot_name(token) if token and settings["telegram"]["user_id"] else None
        offline = bool(token) and not bot and not _online("https://api.telegram.org")
    if offline:
        _say(_t("telegram_offline"))
        sys.exit(1)
    if bot and _confirm("keep_telegram", bot=bot):
        return
    _say(_t("telegram_explain"))
    while True:
        token = _secret("telegram_token")
        with _working("checking"):
            bot = telegram_api.bot_name(token) if re.fullmatch(r"\d+:[\w-]{30,}", token) else None
            online = bool(bot) or _online("https://api.telegram.org")
        if bot:
            break
        _say(_t("telegram_bad" if online else "telegram_offline"))
    _say(_t("telegram_start", bot=bot))
    # An old chat with the bot has no Start button, so the person may need to send any message.
    with _working("telegram_waiting"):
        user_id = telegram_api.wait_for_first_message(token, timeout=30)
    if not user_id:
        _say(_t("telegram_nudge", bot=bot))
        with _working("telegram_waiting"):
            user_id = telegram_api.wait_for_first_message(token, timeout=TELEGRAM_WAIT)
    if not user_id:
        _say(_t("telegram_nothing", bot=bot))
        sys.exit(1)
    settings["telegram"]["user_id"] = user_id
    _save(settings, {"TELEGRAM_BOT_TOKEN": token})
    print(_t("telegram_ok", bot=bot))
    _say(_t("telegram_picture"))


# ---------------------------------------------------------------- 5. voice

def _voice(settings: dict) -> None:
    _title(5, "voice_title")
    if SECRETS.get("GROQ_API_KEY") and _confirm("keep_voice"):
        return
    if _choose("voice_ask", {"yes": _t("voice_yes"), "no": _t("voice_no")}, "yes") == "no":
        return
    _say(_t("groq_steps"))
    _open("https://console.groq.com/keys")
    while True:
        key = _secret("groq_key")
        if not key:  # Enter with no key: no voice notes for now
            _say(_t("voice_skipped"))
            return
        with _working("checking"):
            try:
                good = requests.get("https://api.groq.com/openai/v1/models", timeout=15,
                                    headers={"Authorization": f"Bearer {key}"}).status_code != 401
            except requests.RequestException:
                good = True  # no answer from Groq: keep the key, and voice notes will tell
        if good:
            break
        _say(_t("groq_bad"))
    _save(settings, {"GROQ_API_KEY": key})


# ---------------------------------------------------------------- 6. routine

def _routine(settings: dict) -> None:
    _title(6, "routine_title")
    hours = re.compile(r"^\d{1,2}h(\d{2})?$")
    time_of_day = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

    def a_time(value: str):
        return bool(time_of_day.match(value.strip())) or _t("time_bad")
    week, schedule = settings["week"], settings["schedule"]
    week["daily_capacity"] = _ask("capacity_ask", week["daily_capacity"],
                                  validate=lambda v: bool(hours.match(v.strip())) or _t("capacity_bad"))
    schedule["morning"] = _ask("morning_ask", schedule["morning"], validate=a_time)
    had_quiet = schedule["quiet_from"] != schedule["quiet_until"]
    if _choose("quiet_ask", {"yes": _t("quiet_yes"), "no": _t("quiet_no")}, "yes" if had_quiet else "no") == "yes":
        schedule["quiet_from"] = _ask("quiet_from_ask", schedule["quiet_from"] if had_quiet else "23:00", validate=a_time)
        schedule["quiet_until"] = _ask("quiet_until_ask", schedule["quiet_until"] if had_quiet else "08:00", validate=a_time)
    else:
        schedule["quiet_from"] = schedule["quiet_until"] = "00:00"  # the same time: no quiet hours
    # Seahorse holds a morning summary that falls in the quiet hours until they end: say so now, not later.
    start, end = schedule["quiet_from"], schedule["quiet_until"]
    if in_quiet(schedule["morning"], start, end) and _confirm("morning_in_quiet", morning=schedule["morning"],
                                                              start=start, end=end):
        schedule["morning"] = end
    _save(settings)


# ---------------------------------------------------------------- 7. the vault

def _deploy_key() -> tuple[str, str] | None:
    """The path (on the machine of Seahorse) and the text of the public key that pushes the vault."""
    result = M.seahorse("provision", "deploy-key")
    lines = result.stdout.strip().splitlines()
    if result.returncode != 0 or len(lines) < 2:
        _say(_t("deploy_key_failed", detail=_last_line(result)))
        return None
    return lines[-2], lines[-1]


def _show_deploy_key(repo: str) -> bool:
    key = _deploy_key()
    if not key:
        return False
    _say(_t("deploy_key", public_key=key[1]))
    _open(f"https://github.com/{repo}/settings/keys/new")
    _wait("deploy_wait")
    return True


def _gh_version() -> tuple[int, int]:
    found = re.search(r"gh version (\d+)\.(\d+)", M.sh("gh --version").stdout)
    return (int(found[1]), int(found[2])) if found else (0, 0)


def _gh_ready() -> bool:
    """The GitHub CLI, new enough for `gh auth token` and `gh auth logout --user`. The gh of Ubuntu is too old,
    so the setup takes gh from the apt repository of GitHub."""
    with _working("checking"):
        version = _gh_version()
    if version >= GH_MIN:
        return True
    if not _install("GitHub CLI", GH_APT, "brew upgrade gh 2>/dev/null || brew install gh", "https://cli.github.com", system=True):
        return False
    with _working("checking"):
        version = _gh_version()
    if version < GH_MIN:
        _say(_t("gh_old", version=".".join(map(str, version))))
        return False
    return True


def _github_automatic() -> str | None:
    """Creates the private repository with the GitHub CLI, on the machine of Seahorse, and adds the deploy key."""
    if not _gh_ready():
        return None
    with _working("checking"):
        must_log_in = M.sh("gh auth status").returncode != 0
    if must_log_in:
        _say(_t("github_login" + ("_browser" if BROWSER else "_server")))
        # No input: gh then asks no questions and does not wait for Enter. It shows the code and waits.
        M.login("gh auth login --hostname github.com --git-protocol https --web", _notice, typing=False)
        with _working("checking"):
            logged_in = M.sh("gh auth status").returncode == 0
        if not logged_in:
            return None
    try:
        with _working("checking"):
            GITHUB["login"] = M.sh("gh api user --jq .login").stdout.strip()
            GITHUB["token"] = M.sh("gh auth token").stdout.strip()  # to open the vault on this computer, at the end
        return _create_repository() if GITHUB["login"] else None
    finally:
        # The GitHub login opens every repository of the person. Seahorse needs only the deploy key of the vault.
        if must_log_in:
            M.sh(f"gh auth logout --hostname github.com --user {shlex.quote(GITHUB.get('login', ''))}")


def _create_repository() -> str | None:
    name = _ask("github_name", "seahorse-vault", validate=lambda v: bool(re.fullmatch(r"[\w.-]+", v.strip())) or _t("required"))
    repo = f"{GITHUB['login']}/{name}"
    with _working("github_creating", repo=repo):
        created = M.sh(f"gh repo create {shlex.quote(name)} --private --description 'My Seahorse vault'")
    if created.returncode != 0 and "already exists" not in created.stderr:
        _say(_t("push_failed", detail=created.stderr.strip()[-300:]))
        return None
    key = _deploy_key()
    if not key:
        return None
    with _working("github_creating", repo=repo):
        added = M.sh(f"gh repo deploy-key add {shlex.quote(key[0])} --repo {repo} --allow-write --title Seahorse")
    if added.returncode != 0 and "already" not in added.stderr and not _show_deploy_key(repo):
        return None  # gh could not add the key, and the person could not either
    return _connect(f"git@github.com:{repo}.git")


def _github_manual() -> str | None:
    _say(_t("github_explain"))
    _open("https://github.com/new")
    url = _ask("github_url", validate=lambda v: v.strip().startswith("git@github.com:") or _t("github_url_bad"))
    if not _show_deploy_key(url.removeprefix("git@github.com:").removesuffix(".git")):
        return None
    return _connect(url)


def _connect(url: str) -> str | None:
    with _working("github_sending"):
        result = M.seahorse("provision", "connect", url)
    if result.returncode != 0:
        _say(_t("push_failed", detail=_last_line(result)))
        return None
    print(_t("push_ok"))
    return url


def _vault_status() -> str:
    """Makes the vault when it is missing: "created N", "exists", or "remote <url>"."""
    with _working("vault_creating"):
        result = M.seahorse("provision", "vault")
    if result.returncode != 0:
        _say(_t("vault_failed", detail=_last_line(result)))
        sys.exit(1)
    return _last_line(result)


def _migrate() -> None:
    """Brings a vault made by an older version to this version, as an update does."""
    with _working("vault_updating"):
        result = M.seahorse("migrate")
    if result.returncode != 0:
        _say(_t("migrate_failed", detail=_last_line(result)))


def _ask_vault_path(settings: dict) -> None:
    def allowed(value: str):
        folder = Path(clean_path(value)).expanduser()
        if not value.strip():
            return _t("required")
        if sys.platform == "darwin" and mac_blocked(folder) and not (folder / "6. ⚙️ System/_AGENTS.md").exists():
            return _t("mac_folder_blocked")
        return True
    settings["vault"]["path"] = str(Path(clean_path(_ask("vault_path", settings["vault"]["path"], validate=allowed))).expanduser())


def _vault(settings: dict) -> str | None:
    """Creates the vault and, if wanted, connects it to a private GitHub repository. Returns the repository URL."""
    _title(7, "vault_title")
    if not M.remote and BROWSER:  # on a server, the folder does not matter to the person
        _ask_vault_path(settings)
    _save(settings)
    status = _vault_status()
    if status == "exists" or status.startswith("remote "):
        print(_t("vault_exists"))
        _migrate()
    else:
        print(_t("vault_created"))
    if status.startswith("remote "):
        return status.removeprefix("remote ")
    if _choose("sync_ask", {"github": _t("sync_github"), "local": _t("sync_local")}, "github") == "local":
        if M.remote or not BROWSER:
            _say(_t("sync_local_server"))
        return None
    if _choose("github_how", {"auto": _t("github_auto"), "manual": _t("github_manual")}, "auto") == "auto":
        return _github_automatic() or _github_manual()
    return _github_manual()


# ---------------------------------------------------------------- 8. background, 9. Obsidian

def _service() -> bool:
    _title(8, "service_title")
    with _working("service_starting"):
        result = M.seahorse("provision", "start")
    if result.returncode != 0:
        _say(_t("service_failed", detail=_last_line(result)))
        return False
    print(_t("service_ok"))
    return True


def _copy_here(remote: str) -> Path | None:
    """Copies the vault from GitHub to this computer, or brings the newest notes into the copy that is there."""
    from . import obsidian
    if not obsidian.git():
        with _working("installing", tool="Git"):
            ok = obsidian.install_git()
        if not ok:
            _say(_t("obsidian_no_git"))
            return None
    repo = obsidian.repository(remote)
    # After a move, the old folder of the notes is the copy, when it comes from the same repository.
    moved = MOVED_FROM and obsidian.repository(obsidian.origin(MOVED_FROM)) == repo
    folder = Path(clean_path(_ask("obsidian_folder", str(MOVED_FROM if moved else obsidian.default_folder())))).expanduser()
    token, login = GITHUB.get("token", ""), GITHUB.get("login", "")
    if not token:
        token, login = obsidian.gh_login()  # the GitHub CLI of this computer, when it has one
    if not token and not (folder / ".git").exists():  # git asks for the GitHub login itself
        if sys.platform == "win32":
            _say(_t("obsidian_login_windows"))
        else:
            _say(_t("obsidian_login_token"))
            _open(TOKEN_PAGE)
    with _working("obsidian_copying") if token else nullcontext():
        problem, detail = obsidian.copy_vault(repo, folder, token or None)
    if problem == "pull_failed":
        _say(_t("obsidian_pull_failed", path=folder, detail=detail))
    elif problem:
        _say(_t(f"obsidian_{problem}", path=folder, detail=detail))
        return None
    if not (obsidian.keep_login(folder, login, token) if token else obsidian.safe_store(folder)):
        _say(_t("obsidian_no_store", url=GUIDE))
    with _working("obsidian_plugin"):
        obsidian.add_plugin(folder)
    return folder


def _obsidian(settings: dict, remote: str | None) -> Path | None:
    """Opens the vault in Obsidian on this computer. With a server, it first copies the vault from GitHub."""
    from . import obsidian
    if not BROWSER or (M.remote and not remote):  # no screen here, or the notes stay on the server only
        return None
    _title(9, "obsidian_title")
    if wsl():  # Obsidian runs on Windows, outside the Linux of WSL
        if M.remote:
            _say(_t("obsidian_wsl_server", url=GUIDE))
        else:
            _say(_t("obsidian_wsl_local", path=obsidian.windows_path(Path(settings["vault"]["path"]).expanduser())))
        return None
    if _choose("obsidian_ask", {"yes": _t("obsidian_yes"), "no": _t("obsidian_no")}, "yes") == "no":
        return None
    folder = _copy_here(remote) if M.remote else Path(settings["vault"]["path"]).expanduser()
    if not folder:
        return None
    if not obsidian.installed():
        _say(_t("obsidian_install"))
        _open("https://obsidian.md/download")
        _wait("obsidian_installed")
    while obsidian.running():
        _say(_t("obsidian_close"))
        _wait()
    obsidian.register(folder)
    obsidian.open_vault(folder)
    print(_t("obsidian_ok", path=folder))
    settings["obsidian"]["vault"] = folder.name  # for the "Open in Obsidian" links of Seahorse
    _save(settings)
    return folder


def _restart() -> None:
    """Starts Seahorse again, so that it reads a setting that changed after it started."""
    with _working("saving"):
        M.seahorse("provision", "restart")


# ---------------------------------------------------------------- the run

def run() -> None:
    try:
        _run()
    except KeyboardInterrupt:  # Ctrl+C during a wait: what was answered is already saved
        _say(_t("stopped"))
        sys.exit(1)


def _what_to_change(settings: dict) -> str:
    """For a setup that is already complete: "" to go through all the steps, or the one step to change."""
    complete = settings["user"]["name"] and settings["telegram"]["user_id"] and SECRETS.get("TELEGRAM_BOT_TOKEN")
    if not complete or MOVING:
        return ""
    options = {"all": _t("rerun_all")} | {step: _t("rerun_one", title=_t(title)) for step, title in CHANGES.items()}
    choice = _choose("rerun_ask", options, "all")
    return "" if choice == "all" else choice


def _change(step: str, settings: dict) -> None:
    """Changes one setting, then starts Seahorse again with it."""
    steps = {"about": _about_you, "ai": _ai, "telegram": _telegram, "voice": _voice, "routine": _routine}
    if step in steps:
        steps[step](settings)
    if step == "vault":
        remote = _vault(settings)
    else:  # the vault gets the changes of this version, as in a full run
        status = _vault_status()
        if status == "exists" or status.startswith("remote "):
            _migrate()
        remote = status.removeprefix("remote ") if status.startswith("remote ") else None
    started = _service()
    folder = _obsidian(settings, remote) if step == "obsidian" else None
    if started and folder:
        _restart()
    _say(_t("changed_ok") if started else _t("changed_failed"))


def _run() -> None:
    if sys.platform == "win32":
        os.system("")  # turns on the colors and the cursor codes in the Windows console
    # Some home networks have a broken IPv6 route: each call to Telegram then waited 20 s before IPv4.
    import urllib3.util.connection
    urllib3.util.connection.HAS_IPV6 = False
    language = questionary.select(
        "Choose your language / Escolha seu idioma / Elige tu idioma:",
        choices=[questionary.Choice(label, value=code) for code, label in i18n.LANGUAGES.items()],
        default=config.LANGUAGE if config.LANGUAGE in i18n.LANGUAGES else "en", style=STYLE).ask()
    if language is None:
        sys.exit(1)
    config.LANGUAGE = language
    _say(_t("welcome", guide=SERVER_GUIDE))
    _wait("ready")

    _where()
    if MOVING:
        _move_to_server()
    settings = _load()
    settings["user"]["language"] = language
    _save(settings)
    step = _what_to_change(settings)
    if step:
        return _change(step, settings)
    _about_you(settings)
    _ai(settings)
    _telegram(settings)
    _voice(settings)
    _routine(settings)
    remote = _vault(settings)
    started = _service()
    folder = _obsidian(settings, remote)
    if started and folder:
        _restart()  # Seahorse reads the name of the vault in Obsidian

    places = []
    if folder:
        places.append(_t("notes_here", path=folder))
    elif not M.remote and BROWSER:
        places.append(_t("notes_here", path=settings["vault"]["path"]))
    if remote:
        places.append(_t("notes_phone", url=GUIDE))
    notes = "\n".join(f"  • {place}" for place in places) or f"  • {_t('notes_none')}"
    _say(_t("done" if started else "done_failed", name=settings["user"]["name"], notes=notes))
