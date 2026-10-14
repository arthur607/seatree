"""`seahorse setup`: the interactive setup.

It asks one thing at a time, explains each step for someone who never did it, saves each answer at once (so Ctrl+C
loses nothing), connects the AI and Telegram, creates the vault, and starts Seahorse in the background.
It starts in English. After the first question it speaks the chosen language.
"""
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import questionary
import requests

from . import config, i18n

STEPS = 7
WHERE = "computer"  # run() sets it: "server" or "computer"
# Common time zones per language, so nobody has to type "America/Sao_Paulo".
ZONES = {
    "pt-BR": ["America/Sao_Paulo", "America/Manaus", "America/Recife", "America/Cuiaba", "America/Rio_Branco",
              "Europe/Lisbon"],
    "es": ["Europe/Madrid", "America/Mexico_City", "America/Bogota", "America/Argentina/Buenos_Aires",
           "America/Santiago", "America/Lima", "America/Caracas"],
    "en": ["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "Europe/London",
           "Europe/Berlin", "Asia/Kolkata", "Asia/Tokyo", "Australia/Sydney"],
}


def _t(key: str, **values) -> str:
    return i18n.t(f"setup.{key}", **values)


def _say(text: str) -> None:
    print(f"\n{text}")


def _title(n: int, key: str) -> None:
    print(f"\n\033[1m━━ {_t('step', n=n, total=STEPS, title=_t(key))} ━━\033[0m")


def _must(answer):
    if answer is None:  # Ctrl+C: what was answered is already saved
        _say(_t("stopped"))
        sys.exit(1)
    return answer


def _ask(key: str, default: str = "", validate=None, **values) -> str:
    return _must(questionary.text(_t(key, **values), default=str(default or ""), validate=validate).ask()).strip()


def _secret(key: str) -> str:
    return _must(questionary.password(_t(key)).ask()).strip()


def _choose(key: str, options: dict[str, str], default: str | None = None) -> str:
    """`options` maps the value to save to the label to show."""
    choices = [questionary.Choice(label, value=value) for value, label in options.items()]
    return _must(questionary.select(_t(key), choices=choices, default=default if default in options else None).ask())


def _confirm(key: str, **values) -> bool:
    return _must(questionary.confirm(_t(key, **values), default=True).ask())


def _wait(key: str = "press_enter") -> None:
    _must(questionary.text(_t(key), default="").ask())


# ---------------------------------------------------------------- saving

def _save(settings: dict, secrets: dict | None = None) -> None:
    """Writes config.toml and secrets.env after every step. Secrets are readable only by the owner."""
    config.HOME.mkdir(parents=True, exist_ok=True)
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
    config.CONFIG_FILE.write_text("\n".join(lines), encoding="utf-8")
    if not secrets:
        return
    old = {}
    if config.SECRETS_FILE.exists():
        for line in config.SECRETS_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                old[key.strip()] = value.strip()
    merged = old | {k: v for k, v in secrets.items() if v}
    config.SECRETS_FILE.write_text("".join(f"{k}={v}\n" for k, v in merged.items()), encoding="utf-8")
    os.chmod(config.SECRETS_FILE, 0o600)
    os.environ.update(merged)


def _run(command: str) -> bool:
    return subprocess.run(command, shell=True).returncode == 0


def _install(tool: str, linux: str, mac: str, hint: str) -> bool:
    if not _confirm("install_ask", tool=tool):
        return False
    _say(_t("installing", tool=tool))
    command = mac if platform.system() == "Darwin" else linux
    ok = _run(command)
    os.environ["PATH"] = f"{Path.home() / '.local/bin'}:{os.environ.get('PATH', '')}"
    if not ok:
        _say(_t("install_failed", tool=tool, hint=hint))
    return ok


def _sudo() -> str:
    return "" if hasattr(os, "geteuid") and os.geteuid() == 0 else "sudo "


# ---------------------------------------------------------------- steps

def _where() -> str:
    """"server" when this machine has no screen or the person reached it with ssh. It changes how the logins work
    (a server has no browser, so the person opens the links on the computer in front of them) and some advice."""
    no_screen = platform.system() == "Linux" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY")
    return "server" if os.environ.get("SSH_CONNECTION") or no_screen else "computer"


def _detect_zone() -> str | None:
    """The time zone of this computer. On a server, the time zone of the place the person connects from, by the IP
    of their ssh connection, because the clock of a server is usually UTC."""
    found = []
    if WHERE == "computer":
        found.append(os.environ.get("TZ", ""))
        found.append(os.path.realpath("/etc/localtime").partition("zoneinfo/")[2])
    else:
        ip = os.environ.get("SSH_CONNECTION", "").split(" ")[0]
        for url in (f"https://ipinfo.io/{ip}/timezone", f"https://ipapi.co/{ip}/timezone/") if ip else ():
            try:
                found.append(requests.get(url, timeout=5, headers={"User-Agent": "seahorse"}).text.strip())
            except requests.RequestException:
                pass
    for zone in found:
        if "/" in zone and not zone.startswith("Etc/"):
            try:
                ZoneInfo(zone)
                return zone
            except Exception:  # noqa: BLE001 - an error page instead of a zone name
                pass
    return None


def _about_you(settings: dict) -> None:
    _title(1, "name_title")
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
    now = datetime.now(ZoneInfo(zone))
    _say(_t("timezone_check", time=f"{now:%H:%M}"))


def _ai(settings: dict) -> dict:
    _title(2, "ai_title")
    current = settings["ai"]["provider"]
    logged_in = (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")) if current == "claude" \
        else (Path.home() / ".codex" / "auth.json").exists()
    if logged_in and shutil.which(current) and _confirm("keep_ai", provider=current.capitalize()):
        _test_ai(current)
        return {}
    _say(_t("ai_explain"))
    default = {"claude": "claude-sub", "codex": "codex-sub"}.get(settings["ai"]["provider"], "claude-sub")
    choice = _choose("ai_ask", {"claude-sub": _t("ai_claude_sub"), "claude-api": _t("ai_claude_api"),
                                "codex-sub": _t("ai_codex_sub"), "codex-api": _t("ai_codex_api")}, default)
    secrets = {}
    os.environ["PATH"] = f"{Path.home() / '.local/bin'}:{os.environ.get('PATH', '')}"
    if choice.startswith("claude"):
        if not shutil.which("claude") and not _install("Claude Code", "curl -fsSL https://claude.ai/install.sh | bash",
                                                       "curl -fsSL https://claude.ai/install.sh | bash",
                                                       "https://docs.claude.com/en/docs/claude-code/setup"):
            sys.exit(1)
        if choice == "claude-sub":
            _say(_t(f"claude_token_{WHERE}"))
            _wait()
            subprocess.run([shutil.which("claude") or "claude", "setup-token"])
            secrets["CLAUDE_CODE_OAUTH_TOKEN"] = _secret("paste_claude_token")
        else:
            _say(_t("claude_key"))
            secrets["ANTHROPIC_API_KEY"] = _secret("paste_key")
        provider = "claude"
    else:
        if not shutil.which("codex"):
            # Ubuntu's own Node.js can be too old for Codex, so take a current one from NodeSource.
            node = f"curl -fsSL https://deb.nodesource.com/setup_22.x | {_sudo()}bash - && {_sudo()}apt-get install -y -qq nodejs"
            if not shutil.which("npm") and not _install("Node.js", node,
                                                         "brew install node", "https://nodejs.org"):
                sys.exit(1)
            if not _install("Codex CLI", f"{_sudo()}npm install -g @openai/codex", "npm install -g @openai/codex",
                            "https://developers.openai.com/codex/cli"):
                sys.exit(1)
        if choice == "codex-sub":
            _say(_t(f"codex_login_{WHERE}"))
            _wait()
            # On a computer the browser opens and logs in by itself. A server has no browser: it shows a code instead.
            subprocess.run(["codex", "login"] + (["--device-auth"] if WHERE == "server" else []))
        else:
            _say(_t("codex_key"))
            subprocess.run(["codex", "login", "--with-api-key"], input=_secret("paste_key"), text=True)
        provider = "codex"
    settings["ai"]["provider"] = provider
    _save(settings, secrets)
    _test_ai(provider)
    return secrets


def _test_ai(provider: str) -> None:
    from . import llm
    config.PROVIDER = provider
    _say(_t("ai_testing"))
    answer = llm.run("Reply with exactly: OK", "classify", max_turns=1, timeout=180)
    if answer.error or "OK" not in answer.text.upper():
        _say(_t("ai_failed", detail=answer.text[:300]))
        sys.exit(1)
    print(_t("ai_ok"))


def _telegram(settings: dict) -> str:
    from . import telegram_api
    _title(3, "telegram_title")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    bot = telegram_api.bot_name(token) if token and settings["telegram"]["user_id"] else None
    if bot and _confirm("keep_telegram", bot=bot):
        return token
    _say(_t("telegram_explain"))
    while True:
        token = _secret("telegram_token")
        bot = telegram_api.bot_name(token) if re.fullmatch(r"\d+:[\w-]{30,}", token) else None
        if bot:
            break
        _say(_t("telegram_bad"))
    _say(_t("telegram_start", bot=bot))
    user_id = telegram_api.wait_for_first_message(token)
    while not user_id:
        _say(_t("telegram_timeout", bot=bot))
        user_id = telegram_api.wait_for_first_message(token)
    settings["telegram"]["user_id"] = user_id
    _save(settings, {"TELEGRAM_BOT_TOKEN": token})
    print(_t("telegram_ok", bot=bot))
    _say(_t("telegram_picture"))
    return token


def _voice(settings: dict) -> None:
    _title(4, "voice_title")
    if os.environ.get("GROQ_API_KEY") and _confirm("keep_voice"):
        return
    _say(_t("voice_explain"))
    if _choose("voice_ask", {"yes": _t("voice_yes"), "no": _t("voice_no")}, "yes") == "yes":
        _say(_t("groq_steps"))
        _save(settings, {"GROQ_API_KEY": _secret("groq_key")})


def _routine(settings: dict) -> None:
    _title(5, "routine_title")
    hours = re.compile(r"^\d{1,2}h(\d{2})?$")
    time_of_day = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
    settings["week"]["daily_capacity"] = _ask("capacity_ask", settings["week"]["daily_capacity"],
                                              validate=lambda v: bool(hours.match(v.strip())) or _t("capacity_bad"))
    settings["week"]["work_tag"] = _ask("work_tag_ask", settings["week"]["work_tag"]).lstrip("#").lower() or "work"
    settings["schedule"]["morning"] = _ask("morning_ask", settings["schedule"]["morning"],
                                           validate=lambda v: bool(time_of_day.match(v.strip())) or _t("time_bad"))
    settings["schedule"]["quiet_until"] = _ask("quiet_ask", settings["schedule"]["quiet_until"],
                                               validate=lambda v: bool(time_of_day.match(v.strip())) or _t("time_bad"))
    _save(settings)


def _github_automatic(vault_path: Path) -> str | None:
    """Creates the private repository with the GitHub CLI and adds the deploy key. Returns the repository."""
    from . import vault
    if not shutil.which("gh") and not _install("GitHub CLI", f"{_sudo()}apt-get install -y -qq gh", "brew install gh",
                                               "https://cli.github.com"):
        return None
    if subprocess.run(["gh", "auth", "status"], capture_output=True).returncode != 0:
        _say(_t(f"github_login_{WHERE}"))
        _wait()
        if subprocess.run(["gh", "auth", "login", "--hostname", "github.com", "--git-protocol", "https", "--web"]).returncode != 0:
            return None
    name = _ask("github_name", "seahorse-vault", validate=lambda v: bool(re.fullmatch(r"[\w.-]+", v.strip())) or _t("required"))
    created = subprocess.run(["gh", "repo", "create", name, "--private", "--description", "My Seahorse vault"],
                             capture_output=True, text=True)
    owner = subprocess.run(["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True).stdout.strip()
    repo = f"{owner}/{name}"
    if created.returncode != 0 and "already exists" not in created.stderr:
        _say(_t("push_failed", detail=created.stderr.strip()[-300:]))
        return None
    key = _deploy_key()
    added = subprocess.run(["gh", "repo", "deploy-key", "add", str(key.with_suffix(".pub")), "--repo", repo,
                            "--allow-write", "--title", "Seahorse"], capture_output=True, text=True)
    if added.returncode != 0 and "already" not in added.stderr:  # an old gh: the person adds the key by hand
        _say(_t("deploy_key", public_key=key.with_suffix(".pub").read_text(encoding="utf-8").strip()))
        print(f"https://github.com/{repo}/settings/keys/new")
        _wait("deploy_wait")
    return _connect(vault, f"git@github.com:{repo}.git", key)


def _github_manual() -> str | None:
    from . import vault
    _say(_t("github_explain"))
    url = _ask("github_url", validate=lambda v: v.strip().startswith("git@github.com:") or _t("github_url_bad"))
    key = _deploy_key()
    _say(_t("deploy_key", public_key=key.with_suffix(".pub").read_text(encoding="utf-8").strip()))
    _wait("deploy_wait")
    return _connect(vault, url, key)


def _deploy_key() -> Path:
    key = config.HOME / "vault_key"
    if not key.exists():
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "seahorse", "-f", str(key)], capture_output=True)
    return key


def _connect(vault, url: str, key: Path) -> str | None:
    vault.git("config", "core.sshCommand", f"ssh -i {key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new")
    if vault.git("remote", "get-url", "origin", check=False).returncode == 0:
        vault.git("remote", "set-url", "origin", url)
    else:
        vault.git("remote", "add", "origin", url)
    vault.publish("seahorse: new vault")
    result = vault.git("push", "-u", "origin", "main", check=False)
    if result.returncode != 0:
        _say(_t("push_failed", detail=result.stderr.strip()[-300:]))
        return None
    print(_t("push_ok"))
    return url


def _vault(settings: dict) -> str | None:
    """Creates the vault and, if wanted, connects it to a private GitHub repository. Returns the repository URL."""
    from . import vault
    _title(6, "vault_title")
    _say(_t("vault_explain"))
    path = Path(_ask("vault_path", settings["vault"]["path"])).expanduser()
    settings["vault"]["path"] = str(path)
    _save(settings)
    config.use_vault(path)
    config.USER_NAME = settings["user"]["name"]
    config.DAILY_CAPACITY, config.WORK_TAG = settings["week"]["daily_capacity"], settings["week"]["work_tag"]
    existed = (config.SYSTEM / "_AGENTS.md").exists()
    created = vault.create(datetime.now(config.TIMEZONE).date())
    print(_t("vault_exists") if existed else _t("vault_created", count=len(created)))
    if vault.has_remote():
        return vault.git("remote", "get-url", "origin", check=False).stdout.strip()
    _say(_t("sync_explain"))
    options = {"github": _t("sync_github"), "local": _t("sync_local")}
    if _choose("sync_ask", options, "github") == "local":
        if WHERE == "server":
            _say(_t("sync_local_server"))
        vault.publish("seahorse: new vault")
        return None
    if _choose("github_how", {"auto": _t("github_auto"), "manual": _t("github_manual")}, "auto") == "auto":
        return _github_automatic(path) or _github_manual()
    return _github_manual()


def run() -> None:
    global WHERE
    WHERE = _where()
    settings = config.load()
    language = questionary.select(
        "Choose your language / Escolha seu idioma / Elige tu idioma:",
        choices=[questionary.Choice(label, value=code) for code, label in i18n.LANGUAGES.items()],
        default=settings["user"]["language"] if settings["user"]["language"] in i18n.LANGUAGES else "en").ask()
    if language is None:
        sys.exit(1)
    config.LANGUAGE = settings["user"]["language"] = language
    _say(_t("welcome"))
    _say(_t("checklist"))
    _wait("ready")
    _say(_t(f"where_{WHERE}"))
    _wait()
    _save(settings)

    _about_you(settings)
    config.TIMEZONE = ZoneInfo(settings["user"]["timezone"])
    _save(settings)
    _ai(settings)
    token = _telegram(settings)
    _voice(settings)
    _routine(settings)
    remote = _vault(settings)

    from . import service, telegram_api
    _title(7, "service_title")
    result = service.install()
    print(_t("service_ok") if "installed" in result else _t("service_failed", detail=result))
    config.TELEGRAM_TOKEN, config.USER_ID = token, settings["telegram"]["user_id"]
    telegram_api.send(i18n.t("bot.first_message"))
    # Where to open the notes: the folder itself on a computer, and the GitHub copy on any other device.
    places = ([_t("obsidian_here", path=settings["vault"]["path"])] if WHERE == "computer" else []) + \
             ([_t("obsidian_devices", url=remote)] if remote else [])
    obsidian = "\n".join(f"  • {place}" for place in places) or f"  • {_t('obsidian_none')}"
    _say(_t("done", name=settings["user"]["name"], obsidian=obsidian, logs=service.logs_command()))
