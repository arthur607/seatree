"""`seahorse provision <step>`: the steps of the setup that run on the machine of Seahorse, with no questions.

The setup runs on the person's computer and calls these steps on the server through ssh (or on this machine).
They read config.toml and secrets.env, which the setup writes first. Each step prints its result on the last
line and exits with 1 when it fails.
"""
import subprocess
import sys
from datetime import datetime

from . import config


def test_ai() -> int:
    from . import llm
    answer = llm.run("Reply with exactly: OK", "classify", max_turns=1, timeout=180)
    ok = not answer.error and "OK" in answer.text.upper()
    print("OK" if ok else answer.text[:300].replace("\n", " "))
    return 0 if ok else 1


def vault() -> int:
    """Creates the vault if it is missing. Prints "created N", "exists", or the URL of its remote."""
    from . import vault as vault_module
    existed = (config.SYSTEM / "_AGENTS.md").exists()
    created = vault_module.create(datetime.now(config.TIMEZONE).date())
    if not vault_module.has_remote():
        vault_module.publish("seahorse: new vault")
    remote = vault_module.git("remote", "get-url", "origin", check=False).stdout.strip() if vault_module.has_remote() else ""
    print(f"remote {remote}" if remote else "exists" if existed else f"created {len(created)}")
    return 0


def deploy_key() -> int:
    """Prints the path and the text of the public key that lets this machine push the vault."""
    key = config.HOME / "vault_key"
    if not key.exists():
        config.HOME.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "seahorse", "-f", str(key)], check=True)
    public = key.with_suffix(".pub")
    print(public)
    print(public.read_text(encoding="utf-8").strip())
    return 0


def connect(url: str) -> int:
    """Connects the vault to its private repository and pushes it."""
    from . import vault as vault_module
    key = config.HOME / "vault_key"
    vault_module.git("config", "core.sshCommand", f"ssh -i {key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new")
    if vault_module.git("remote", "get-url", "origin", check=False).returncode == 0:
        vault_module.git("remote", "set-url", "origin", url)
    else:
        vault_module.git("remote", "add", "origin", url)
    vault_module.publish("seahorse: new vault")
    result = vault_module.git("push", "-u", "origin", "main", check=False)
    print("OK" if result.returncode == 0 else result.stderr.strip()[-300:].replace("\n", " "))
    return result.returncode


def start() -> int:
    """Installs and starts the background service, and sends the first message on Telegram."""
    from . import i18n, service, telegram_api
    result = service.install()
    ok = "installed" in result
    if ok:
        telegram_api.send(i18n.t("bot.first_message"))
    print(f"OK {service.logs_command()}" if ok else result.replace("\n", " "))
    return 0 if ok else 1


STEPS = {"test-ai": test_ai, "vault": vault, "deploy-key": deploy_key, "connect": connect, "start": start}


def main(step: str, args: list[str]) -> int:
    return STEPS[step](*args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))
