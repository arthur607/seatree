"""`seahorse setup`: the interactive setup.

It runs on the person's own computer (Windows, macOS or Linux), also when Seahorse will run on a server: then it
sends each step to the server through ssh. The logins run where Seahorse runs, but their links open in the
browser of the computer and their one-time codes go to the clipboard. At the end it opens the vault in Obsidian
on this computer.

It asks one thing at a time and saves each answer at once, so Ctrl+C loses nothing. It starts in English. After
the first question it speaks the chosen language. The texts are short: one instruction for each action.
"""
import os
import re
import shlex
import shutil
import sys
import threading
import tomllib
import webbrowser
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import questionary
import requests

from . import __version__, config, i18n
from .machine import Local, Server

STEPS = 9
INSTALL_SCRIPT = "https://raw.githubusercontent.com/lorenzoa7/seahorse/main/install.sh"
GUIDE = "https://github.com/lorenzoa7/seahorse#open-your-vault-on-your-phone"
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

M: Local | Server = Local()  # the machine that runs Seahorse
BROWSER = True  # False when the setup runs on the server itself, where no browser can open
SECRETS: dict[str, str] = {}
GITHUB: dict[str, str] = {}  # the login and token of GitHub, kept only while the setup runs


# ---------------------------------------------------------------- talking
# The text names come first and only by position, so a text can have any {field}, even {name}.

def _t(text: str, /, **values) -> str:
    return i18n.t(f"setup.{text}", **values)


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


def _confirm(text: str, /, **values) -> bool:
    _drain()
    return _must(questionary.confirm(_t(text, **values), default=True, style=STYLE).ask())


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
    """Opens a page in the browser of this computer, when there is one."""
    if BROWSER and webbrowser.open(url):
        _notice("link_opened")


def _last_line(result) -> str:
    lines = (result.stdout or "").strip().splitlines() or (result.stderr or "").strip().splitlines() or [""]
    return lines[-1]


# ---------------------------------------------------------------- saving

def _settings_text(settings: dict) -> str:
    lines = []
    for section, fields in settings.items():
        if not isinstance(fields, dict):
            continue
        lines.append(f"[{section}]")
        for key, value in fields.items():
            if isinstance(value, dict):
                continue
            lines.append(f"{key} = {value}" if isinstance(value, int) else f'{key} = "{value}"')
        lines.append("")
    return "\n".join(lines)


def _parse_secrets(text: str | None) -> dict[str, str]:
    found = {}
    for line in (text or "").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            found[key.strip()] = value.strip()
    return found


def _save(settings: dict, secrets: dict | None = None) -> None:
    """Writes config.toml and secrets.env on the machine of Seahorse after every step."""
    M.write_file("config.toml", _settings_text(settings))
    if secrets:
        SECRETS.update({k: v for k, v in secrets.items() if v})
        M.write_file("secrets.env", "".join(f"{k}={v}\n" for k, v in SECRETS.items()), private=True)


def _load() -> dict:
    text = M.read_file("config.toml")
    settings = config._merge(config.DEFAULTS, tomllib.loads(text) if text else {})
    if M.remote and not text:
        settings["vault"]["path"] = "~/seahorse-vault"
    SECRETS.clear()
    SECRETS.update(_parse_secrets(M.read_file("secrets.env")))
    return settings


def _install(tool: str, linux: str, mac: str, hint: str) -> bool:
    with _working("installing", tool=tool):
        result = M.sh(mac if M.system == "Darwin" else linux)
    if result.returncode != 0:
        _say(_t("install_failed", tool=tool, hint=hint, detail=_last_line(result)))
    return result.returncode == 0


# ---------------------------------------------------------------- 1. where

def _on_a_server_now() -> bool:
    """True when the person runs the setup on the server itself (through ssh, or on a machine with no screen)."""
    no_screen = sys.platform.startswith("linux") and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY")
    return bool(os.environ.get("SSH_CONNECTION")) or no_screen


def _where() -> None:
    global M, BROWSER
    _title(1, "where_title")
    if _on_a_server_now():
        M, BROWSER = Local(), False
        _say(_t("where_here_server"))
        return
    computer = _t("where_computer_windows") if sys.platform == "win32" else _t("where_computer")
    if _choose("where_ask", {"server": _t("where_server"), "computer": computer}, "server") == "computer":
        if sys.platform == "win32":
            _say(_t("windows_wsl"))
            sys.exit(1)
        M = Local()
        _say(_t("pc_warning"))
        return
    if not shutil.which("ssh"):
        _say(_t("ssh_missing"))
        sys.exit(1)
    _connect_server()


def _connect_server() -> None:
    global M
    local = Local()
    remembered = tomllib.loads(local.read_file("server.toml") or "").get("server", {})
    while True:
        address = _ask("server_address", remembered.get("host", ""), validate=lambda v: bool(v.strip()) or _t("required"))
        user, _, host = address.strip().rpartition("@")
        server = Server(host, user or "root")
        with _working("connecting", host=host):
            reachable = server.reachable()
        if not reachable:
            _say(_t("server_password"))
            server.add_key()
            with _working("connecting", host=host):
                reachable = server.reachable()
        if reachable:
            break
        _say(_t("server_failed", host=host))
    M = server
    local.write_file("server.toml", f'[server]\nhost = "{server.host}"\nuser = "{server.user}"\nlanguage = "{config.LANGUAGE}"\n')
    print(_t("server_ok", host=server.host))
    _install_on_server()


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
    settings["user"]["name"] = _ask("name_ask", settings["user"]["name"], validate=lambda v: bool(v.strip()) or _t("required"))
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

def _ai(settings: dict) -> None:
    _title(3, "ai_title")
    current = settings["ai"]["provider"]
    with _working("checking"):
        logged_in = (SECRETS.get("CLAUDE_CODE_OAUTH_TOKEN") or SECRETS.get("ANTHROPIC_API_KEY")) if current == "claude" \
            else M.sh("test -f ~/.codex/auth.json").returncode == 0
        logged_in = logged_in and M.which(current)
    if logged_in and _confirm("keep_ai", provider=current.capitalize()):
        return _test_ai()
    default = {"claude": "claude-sub", "codex": "codex-sub"}.get(current, "claude-sub")
    choice = _choose("ai_ask", {"claude-sub": _t("ai_claude_sub"), "claude-api": _t("ai_claude_api"),
                                "codex-sub": _t("ai_codex_sub"), "codex-api": _t("ai_codex_api")}, default)
    secrets = {}
    guide = "_browser" if BROWSER else "_server"
    if choice.startswith("claude"):
        claude = "curl -fsSL https://claude.ai/install.sh | bash"
        with _working("checking"):
            missing = not M.which("claude")
        if missing and not _install("Claude Code", claude, claude, "https://docs.claude.com/en/docs/claude-code/setup"):
            sys.exit(1)
        if choice == "claude-sub":
            _say(_t("claude_token" + guide))
            found = re.findall(r"sk-ant-oat01-[\w-]{20,}", M.login("claude setup-token", _notice))
            if found:
                _notice("token_caught")
            secrets["CLAUDE_CODE_OAUTH_TOKEN"] = found[-1] if found else _secret("paste_claude_token")
        else:
            _say(_t("claude_key"))
            _open("https://console.anthropic.com/settings/keys")
            secrets["ANTHROPIC_API_KEY"] = _secret("paste_key")
        provider = "claude"
    else:
        with _working("checking"):
            missing, no_npm = not M.which("codex"), not M.which("npm")
        if missing:
            # Ubuntu's own Node.js can be too old for Codex, so take a current one from NodeSource.
            node = f"curl -fsSL https://deb.nodesource.com/setup_22.x | {M.sudo}bash - && {M.sudo}apt-get install -y -qq nodejs"
            if no_npm and not _install("Node.js", node, "brew install node", "https://nodejs.org"):
                sys.exit(1)
            if not _install("Codex CLI", f"{M.sudo}npm install -g @openai/codex", "npm install -g @openai/codex",
                            "https://developers.openai.com/codex/cli"):
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
    _test_ai()


def _test_ai() -> None:
    with _working("ai_testing"):
        result = M.seahorse("provision", "test-ai")
    if result.returncode != 0:
        _say(_t("ai_failed", detail=_last_line(result)))
        sys.exit(1)
    print(_t("ai_ok"))


# ---------------------------------------------------------------- 4. Telegram

def _telegram(settings: dict) -> None:
    from . import telegram_api
    _title(4, "telegram_title")
    token = SECRETS.get("TELEGRAM_BOT_TOKEN", "")
    with _working("checking"):
        bot = telegram_api.bot_name(token) if token and settings["telegram"]["user_id"] else None
    if bot and _confirm("keep_telegram", bot=bot):
        return
    _say(_t("telegram_explain"))
    while True:
        token = _secret("telegram_token")
        with _working("checking"):
            bot = telegram_api.bot_name(token) if re.fullmatch(r"\d+:[\w-]{30,}", token) else None
        if bot:
            break
        _say(_t("telegram_bad"))
    _say(_t("telegram_start", bot=bot))
    user_id = None
    with _working("telegram_waiting"):
        while not user_id:
            user_id = telegram_api.wait_for_first_message(token)
    settings["telegram"]["user_id"] = user_id
    _save(settings, {"TELEGRAM_BOT_TOKEN": token})
    print(_t("telegram_ok", bot=bot))


# ---------------------------------------------------------------- 5. voice

def _voice(settings: dict) -> None:
    _title(5, "voice_title")
    if SECRETS.get("GROQ_API_KEY") and _confirm("keep_voice"):
        return
    if _choose("voice_ask", {"yes": _t("voice_yes"), "no": _t("voice_no")}, "yes") == "yes":
        _say(_t("groq_steps"))
        _open("https://console.groq.com/keys")
        _save(settings, {"GROQ_API_KEY": _secret("groq_key")})


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
    week["work_tag"] = _ask("work_tag_ask", week["work_tag"]).lstrip("#").lower() or "work"
    schedule["morning"] = _ask("morning_ask", schedule["morning"], validate=a_time)
    had_quiet = schedule["quiet_from"] != schedule["quiet_until"]
    if _choose("quiet_ask", {"yes": _t("quiet_yes"), "no": _t("quiet_no")}, "yes" if had_quiet else "no") == "yes":
        schedule["quiet_from"] = _ask("quiet_from_ask", schedule["quiet_from"] if had_quiet else "23:00", validate=a_time)
        schedule["quiet_until"] = _ask("quiet_until_ask", schedule["quiet_until"] if had_quiet else "08:00", validate=a_time)
    else:
        schedule["quiet_from"] = schedule["quiet_until"] = "00:00"  # the same time: no quiet hours
    _save(settings)


# ---------------------------------------------------------------- 7. the vault

def _deploy_key() -> tuple[str, str]:
    """The path (on the machine of Seahorse) and the text of the public key that pushes the vault."""
    lines = M.seahorse("provision", "deploy-key").stdout.strip().splitlines()
    return lines[-2], lines[-1]


def _show_deploy_key(repo: str) -> None:
    _say(_t("deploy_key", public_key=_deploy_key()[1]))
    _open(f"https://github.com/{repo}/settings/keys/new")
    _wait("deploy_wait")


def _github_automatic() -> str | None:
    """Creates the private repository with the GitHub CLI, on the machine of Seahorse, and adds the deploy key."""
    with _working("checking"):
        missing = not M.which("gh")
    if missing and not _install("GitHub CLI", f"{M.sudo}apt-get install -y -qq gh", "brew install gh", "https://cli.github.com"):
        return None
    with _working("checking"):
        must_log_in = M.sh("gh auth status").returncode != 0
    if must_log_in:
        _say(_t("github_login" + ("_browser" if BROWSER else "_server")))
        # No input: gh then asks no questions and does not wait for Enter. It shows the code and waits.
        M.login("gh auth login --hostname github.com --git-protocol https --web", _notice, typing=False)
        if M.sh("gh auth status").returncode != 0:
            return None
    try:
        GITHUB["login"] = M.sh("gh api user --jq .login").stdout.strip()
        GITHUB["token"] = M.sh("gh auth token").stdout.strip()  # to open the vault on this computer, at the end
        return _create_repository()
    finally:
        # The GitHub login opens every repository of the person. Seahorse needs only the deploy key of the vault.
        if must_log_in:
            M.sh(f"gh auth logout --hostname github.com --user {shlex.quote(GITHUB.get('login', ''))}")


def _create_repository() -> str | None:
    name = _ask("github_name", "seahorse-vault", validate=lambda v: bool(re.fullmatch(r"[\w.-]+", v.strip())) or _t("required"))
    repo = f"{GITHUB['login']}/{name}"
    with _working("github_creating", repo=repo):
        created = M.sh(f"gh repo create {shlex.quote(name)} --private --description 'My Seahorse vault'")
        path = _deploy_key()[0] if created.returncode == 0 or "already exists" in created.stderr else ""
        added = M.sh(f"gh repo deploy-key add {shlex.quote(path)} --repo {repo} --allow-write --title Seahorse") if path else None
    if not path:
        _say(_t("push_failed", detail=created.stderr.strip()[-300:]))
        return None
    if added.returncode != 0 and "already" not in added.stderr:  # an old gh: the person adds the key by hand
        _show_deploy_key(repo)
    return _connect(f"git@github.com:{repo}.git")


def _github_manual() -> str | None:
    _say(_t("github_explain"))
    _open("https://github.com/new")
    url = _ask("github_url", validate=lambda v: v.strip().startswith("git@github.com:") or _t("github_url_bad"))
    _show_deploy_key(url.removeprefix("git@github.com:").removesuffix(".git"))
    return _connect(url)


def _connect(url: str) -> str | None:
    with _working("github_sending"):
        result = M.seahorse("provision", "connect", url)
    if result.returncode != 0:
        _say(_t("push_failed", detail=_last_line(result)))
        return None
    print(_t("push_ok"))
    return url


def _vault(settings: dict) -> str | None:
    """Creates the vault and, if wanted, connects it to a private GitHub repository. Returns the repository URL."""
    _title(7, "vault_title")
    if not M.remote and BROWSER:  # on a server, the folder does not matter to the person
        settings["vault"]["path"] = str(Path(_ask("vault_path", settings["vault"]["path"])).expanduser())
    _save(settings)
    with _working("vault_creating"):
        status = _last_line(M.seahorse("provision", "vault"))
    if status.startswith("remote "):
        print(_t("vault_exists"))
        return status.removeprefix("remote ")
    print(_t("vault_exists") if status == "exists" else _t("vault_created"))
    if _choose("sync_ask", {"github": _t("sync_github"), "local": _t("sync_local")}, "github") == "local":
        if M.remote or not BROWSER:
            _say(_t("sync_local_server"))
        return None
    if _choose("github_how", {"auto": _t("github_auto"), "manual": _t("github_manual")}, "auto") == "auto":
        return _github_automatic() or _github_manual()
    return _github_manual()


# ---------------------------------------------------------------- 8. background, 9. Obsidian

def _service() -> None:
    _title(8, "service_title")
    with _working("service_starting"):
        result = M.seahorse("provision", "start")
    print(_t("service_ok") if result.returncode == 0 else _t("service_failed", detail=_last_line(result)))


def _obsidian(settings: dict, remote: str | None) -> Path | None:
    """Opens the vault in Obsidian on this computer. With a server, it first copies the vault from GitHub."""
    from . import obsidian
    if not BROWSER or (M.remote and not remote):  # no screen here, or the notes stay on the server only
        return None
    _title(9, "obsidian_title")
    if _choose("obsidian_ask", {"yes": _t("obsidian_yes"), "no": _t("obsidian_no")}, "yes") == "no":
        return None
    if M.remote:
        if not obsidian.git():
            with _working("installing", tool="Git"):
                ok = obsidian.install_git()
            if not ok:
                _say(_t("obsidian_no_git"))
                return None
        folder = Path(_ask("obsidian_folder", str(obsidian.default_folder()))).expanduser()
        repo = remote.removeprefix("git@github.com:").removesuffix(".git")
        if GITHUB.get("token"):
            with _working("obsidian_copying"):
                error = obsidian.clone(repo, GITHUB["token"], GITHUB["login"], folder)
        else:  # the repository was made before: git asks for the GitHub login itself
            error = None if os.system(f'"{obsidian.git()}" clone -q https://github.com/{repo}.git "{folder}"') == 0 else repo
        if error:
            _say(_t("obsidian_failed", detail=error))
            return None
        with _working("obsidian_plugin"):
            obsidian.add_plugin(folder)
    else:
        folder = Path(settings["vault"]["path"]).expanduser()
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
    return folder


def run() -> None:
    try:
        _run()
    except KeyboardInterrupt:  # Ctrl+C during a wait: what was answered is already saved
        _say(_t("stopped"))
        sys.exit(1)


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
    _say(_t("welcome"))
    _wait("ready")

    _where()
    with _working("checking"):
        settings = _load()
    settings["user"]["language"] = language
    _save(settings)
    _about_you(settings)
    _ai(settings)
    _telegram(settings)
    _voice(settings)
    _routine(settings)
    remote = _vault(settings)
    _service()
    folder = _obsidian(settings, remote)

    places = []
    if folder:
        places.append(_t("notes_here", path=folder))
    elif not M.remote and BROWSER:
        places.append(_t("notes_here", path=settings["vault"]["path"]))
    if remote:
        places.append(_t("notes_phone", url=GUIDE))
    notes = "\n".join(f"  • {place}" for place in places) or f"  • {_t('notes_none')}"
    _say(_t("done", name=settings["user"]["name"], notes=notes))
