"""`seahorse setup`: the interactive setup. It asks a few questions, connects the AI and Telegram, creates the
vault, and starts Seahorse in the background.

It starts in English. After the first question, it speaks the chosen language. Running it again keeps the earlier
answers as defaults, so it also works to change one setting.
"""
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import questionary

from . import config, i18n

TOTAL = 7


def _t(key: str, **values) -> str:
    return i18n.t(f"setup.{key}", **values)


def _say(text: str) -> None:
    print(f"\n{text}")


def _title(n: int, key: str) -> None:
    print(f"\n━━ {_t('step', n=n, total=TOTAL, title=_t(key))} ━━")


def _ask(key: str, default: str = "", **values) -> str:
    answer = questionary.text(_t(key, **values), default=str(default or "")).ask()
    if answer is None:
        sys.exit(1)
    return answer.strip()


def _secret(key: str) -> str:
    answer = questionary.password(_t(key)).ask()
    if answer is None:
        sys.exit(1)
    return answer.strip()


def _choose(key: str, options: dict[str, str], default: str | None = None) -> str:
    """`options` maps the stored value to the label shown."""
    choices = [questionary.Choice(label, value=value) for value, label in options.items()]
    answer = questionary.select(_t(key), choices=choices, default=default if default in options else None).ask()
    if answer is None:
        sys.exit(1)
    return answer


def _confirm(text: str) -> bool:
    answer = questionary.confirm(text, default=True).ask()
    if answer is None:
        sys.exit(1)
    return answer


def _local_timezone() -> str:
    tz = os.environ.get("TZ")
    if tz:
        return tz
    try:
        link = os.readlink("/etc/localtime")
        return link.split("zoneinfo/")[-1]
    except OSError:
        return "UTC"


def _write_config(values: dict, secrets: dict) -> None:
    """Writes config.toml and secrets.env. Secrets are readable only by the owner."""
    config.HOME.mkdir(parents=True, exist_ok=True)
    lines = []
    for section, fields in values.items():
        lines.append(f"[{section}]")
        for key, value in fields.items():
            lines.append(f"{key} = {value}" if isinstance(value, int) else f'{key} = "{value}"')
        lines.append("")
    config.CONFIG_FILE.write_text("\n".join(lines), encoding="utf-8")
    old = {}
    if config.SECRETS_FILE.exists():
        for line in config.SECRETS_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                old[key.strip()] = value.strip()
    merged = old | {k: v for k, v in secrets.items() if v}
    config.SECRETS_FILE.write_text("".join(f"{k}={v}\n" for k, v in merged.items()), encoding="utf-8")
    os.chmod(config.SECRETS_FILE, 0o600)
    for key, value in merged.items():
        os.environ[key] = value


def _install(tool: str, command: str, hint: str) -> bool:
    if not _confirm(_t("install_ask", tool=tool)):
        return False
    _say(_t("installing", tool=tool))
    if subprocess.run(command, shell=True).returncode != 0:
        _say(_t("install_failed", tool=tool, hint=hint))
        return False
    return True


def _setup_ai(current: dict) -> tuple[str, dict]:
    _title(2, "ai_title")
    _say(_t("ai_explain"))
    default = {"claude": "claude-sub", "codex": "codex-sub"}.get(current.get("provider", ""), "claude-sub")
    choice = _choose("ai_title", {"claude-sub": _t("ai_claude_sub"), "claude-api": _t("ai_claude_api"),
                                  "codex-sub": _t("ai_codex_sub"), "codex-api": _t("ai_codex_api")}, default)
    secrets = {}
    if choice.startswith("claude"):
        local_bin = str(Path.home() / ".local/bin")
        os.environ["PATH"] = f"{local_bin}:{os.environ.get('PATH', '')}"
        if not shutil.which("claude") and not _install("Claude Code", "curl -fsSL https://claude.ai/install.sh | bash",
                                                       "https://docs.claude.com/en/docs/claude-code/setup"):
            sys.exit(1)
        if choice == "claude-sub":
            _say(_t("claude_token"))
            subprocess.run([shutil.which("claude") or f"{local_bin}/claude", "setup-token"])
            secrets["CLAUDE_CODE_OAUTH_TOKEN"] = _secret("paste_token")
        else:
            secrets["ANTHROPIC_API_KEY"] = _secret("paste_key")
        return "claude", secrets
    if not shutil.which("codex"):
        command = "brew install codex" if platform.system() == "Darwin" and shutil.which("brew") else "npm install -g @openai/codex"
        if not _install("Codex CLI", command, "https://developers.openai.com/codex/cli"):
            sys.exit(1)
    if choice == "codex-sub":
        _say(_t("codex_login"))
        subprocess.run(["codex", "login", "--device-auth"])
    else:
        key = _secret("paste_key")
        subprocess.run(["codex", "login", "--with-api-key"], input=key, text=True)
    return "codex", secrets


def _test_ai(provider: str) -> None:
    from . import llm
    os.environ["SEAHORSE_PROVIDER"] = provider
    config.PROVIDER = provider
    _say(_t("ai_testing"))
    answer = llm.run("Reply with exactly: OK", "classify", max_turns=1, timeout=180)
    if answer.error or "OK" not in answer.text.upper():
        _say(_t("ai_failed", detail=answer.text[:300]))
        sys.exit(1)
    print(_t("ai_ok"))


def _setup_telegram(current_token: str) -> tuple[str, int, str]:
    from . import telegram_api
    _title(3, "telegram_title")
    _say(_t("telegram_explain"))
    while True:
        token = _secret("telegram_token") or current_token
        bot = telegram_api.bot_name(token)
        if bot:
            break
        _say(_t("telegram_bad"))
    _say(_t("telegram_start", bot=bot))
    user_id = telegram_api.wait_for_first_message(token)
    if not user_id:
        _say(_t("telegram_timeout"))
        sys.exit(1)
    print(_t("telegram_ok", bot=bot))
    return token, user_id, bot


def _setup_voice() -> dict:
    _title(4, "voice_title")
    _say(_t("voice_explain"))
    choice = _choose("voice_title", {"groq": _t("voice_groq"), "local": _t("voice_local"), "none": _t("voice_none")}, "groq")
    if choice == "groq":
        return {"GROQ_API_KEY": _secret("groq_key")}
    if choice == "local":
        subprocess.run([shutil.which("uv") or "uv", "pip", "install", "--python", sys.executable, "faster-whisper"])
    return {}


def _setup_vault(path: Path) -> str | None:
    """Creates the vault and, if the user wants, connects it to a private GitHub repository. Returns its URL."""
    from . import vault
    _title(5, "vault_title")
    config.use_vault(path)
    existed = (path / "6. ⚙️ System" / "_AGENTS.md").exists()
    created = vault.create(datetime.now(config.TIMEZONE).date())
    print(_t("vault_exists") if existed else _t("vault_created", count=len(created)))
    if vault.has_remote():
        return vault.git("remote", "get-url", "origin", check=False).stdout.strip()
    if _choose("sync_ask", {"github": _t("sync_github"), "local": _t("sync_local")}, "github") == "local":
        vault.publish("seahorse: new vault")
        return None
    _say(_t("github_explain"))
    url = _ask("github_url")
    key = config.HOME / "vault_key"
    if not key.exists():
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "seahorse", "-f", str(key)], capture_output=True)
    _say(_t("deploy_key", key=key.with_suffix(".pub").read_text(encoding="utf-8").strip()))
    input(_t("deploy_wait"))
    vault.git("config", "core.sshCommand", f"ssh -i {key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new")
    vault.git("remote", "add", "origin", url, check=False)
    vault.publish("seahorse: new vault")
    result = vault.git("push", "-u", "origin", "main", check=False)
    print(_t("push_ok") if result.returncode == 0 else _t("push_failed", detail=result.stderr.strip()[-300:]))
    return url


def run() -> None:
    current = config.load()
    language = questionary.select("Choose your language / Escolha seu idioma / Elige tu idioma:",
                                  choices=[questionary.Choice(label, value=code) for code, label in i18n.LANGUAGES.items()],
                                  default=current["user"]["language"] if current["user"]["language"] in i18n.LANGUAGES else "en").ask()
    if language is None:
        sys.exit(1)
    config.LANGUAGE = language
    _say(_t("welcome"))

    _title(1, "name_title")
    name = _ask("name_ask", current["user"]["name"])
    timezone = _ask("timezone_ask", current["user"]["timezone"] if current["user"]["timezone"] != "UTC" else _local_timezone())
    from zoneinfo import ZoneInfo
    config.TIMEZONE = ZoneInfo(timezone)
    config.USER_NAME = name

    provider, secrets = _setup_ai(current["ai"])
    for key, value in secrets.items():
        os.environ[key] = value
    _test_ai(provider)

    token, user_id, bot = _setup_telegram(os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    secrets["TELEGRAM_BOT_TOKEN"] = token
    secrets |= _setup_voice()

    _title(6, "routine_title")
    capacity = _ask("capacity_ask", current["week"]["daily_capacity"])
    work_tag = _ask("work_tag_ask", current["week"]["work_tag"]).lstrip("#").lower() or "work"
    morning = _ask("morning_ask", current["schedule"]["morning"])
    quiet_until = _ask("quiet_ask", current["schedule"]["quiet_until"])
    config.DAILY_CAPACITY, config.WORK_TAG = capacity, work_tag

    vault_path = Path(_ask("vault_path", current["vault"]["path"])).expanduser()
    _write_config({
        "user": {"name": name, "language": language, "timezone": timezone},
        "ai": {"provider": provider},
        "vault": {"path": str(vault_path)},
        "telegram": {"user_id": user_id},
        "schedule": {**current["schedule"], "morning": morning, "quiet_until": quiet_until},
        "week": {"daily_capacity": capacity, "work_tag": work_tag},
    }, secrets)
    remote = _setup_vault(vault_path)

    from . import service, telegram_api
    _title(7, "service_title")
    if platform.system() == "Darwin" or not Path("/etc/systemd/system").exists():
        _say(_t("pc_warning"))
    result = service.install()
    print(_t("service_ok") if "installed" in result else _t("service_failed", detail=result))
    config.TELEGRAM_TOKEN, config.USER_ID = token, user_id
    telegram_api.send(i18n.t("bot.first_message"))
    obsidian = _t("obsidian_github", url=remote) if remote else _t("obsidian_local", path=vault_path)
    _say(_t("done", name=name, obsidian=obsidian, logs=service.logs_command()))
