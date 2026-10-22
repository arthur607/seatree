"""`seahorse provision <step>`: the steps of the setup that run on the machine of Seahorse, with no questions.

The setup runs on the person's computer and calls these steps on the server through ssh (or on this machine).
They read config.toml and secrets.env, which the setup writes first. Each step prints its result on the last
line and exits with 1 when it fails.
"""
import shlex
import subprocess
import sys
from datetime import datetime

from . import config

START_SECONDS = 8  # how long the service must stay up after the setup starts it


def _ssh_command() -> str:
    """The ssh command of the vault's git: only the deploy key of this machine."""
    return f"ssh -i {shlex.quote(str(config.HOME / 'vault_key'))} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"


def test_ai() -> int:
    from . import llm
    answer = llm.run("Reply with exactly: OK", "classify", max_turns=1, timeout=180)
    ok = not answer.error and "OK" in answer.text.upper()
    print("OK" if ok else answer.text[:300].replace("\n", " "))
    return 0 if ok else 1


def vault() -> int:
    """Creates the vault if it is missing. Prints "created N", "exists", or "remote <url>" when it is on GitHub.
    An existing vault is not made again: `seahorse migrate` adds the template files that are new in a version."""
    from . import updates, vault as vault_module
    existed = (config.SYSTEM / "_AGENTS.md").exists()
    if existed:
        created = []
        if not (config.VAULT / ".git").exists():
            vault_module.git("init", "-q", "-b", "main")
    else:
        created = vault_module.create(datetime.now(config.TIMEZONE).date())
        updates.mark_vault()  # a new vault is at this version: no old migration runs on it
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
    """Connects the vault to its private repository and pushes it. The remote stays only when the push worked:
    else the vault has no remote, so the next `seahorse setup` asks for GitHub again and tries again."""
    from . import vault as vault_module
    vault_module.git("config", "core.sshCommand", _ssh_command())
    vault_module.publish("seahorse: new vault")  # before the remote: a commit only
    before = vault_module.git("remote", "get-url", "origin", check=False)
    vault_module.git("remote", "set-url" if before.returncode == 0 else "add", "origin", url)
    result = vault_module.git("push", "-u", "origin", "main", check=False)
    if result.returncode != 0:
        if before.returncode == 0:
            vault_module.git("remote", "set-url", "origin", before.stdout.strip())
        else:
            vault_module.git("remote", "remove", "origin", check=False)
    print("OK" if result.returncode == 0 else result.stderr.strip()[-300:].replace("\n", " "))
    return result.returncode


def clone(url: str) -> int:
    """Copies the vault from its private repository with the deploy key of this machine, for a Seahorse that moves
    here from another machine. Prints "OK", or the error."""
    from . import vault as vault_module
    if (config.VAULT / ".git").exists():
        print("OK")
        return 0
    if config.VAULT.exists() and any(config.VAULT.iterdir()):
        print(f"{config.VAULT} is not empty")
        return 1
    ssh = _ssh_command()
    result = subprocess.run(["git", "-c", f"core.sshCommand={ssh}", "clone", "-q", "-b", "main", url, str(config.VAULT)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode == 0:
        vault_module.git("config", "core.sshCommand", ssh)
    print("OK" if result.returncode == 0 else result.stderr.strip()[-300:].replace("\n", " "))
    return result.returncode


def start() -> int:
    """Installs and starts the background service, checks that it stays up, and sends the first message on
    Telegram. A new Seahorse then starts its first conversation."""
    from . import i18n, intro, service, telegram_api
    result = service.install()
    if "installed" not in result:
        print(result.replace("\n", " "))
        return 1
    if not service.stays_up(START_SECONDS):
        print(f"Seahorse stopped right after it started. To see why, run: {service.logs_command()}")
        return 1
    new = not (config.STATE / "intro.json").exists()
    telegram_api.send(i18n.t("setup.connected_new" if new else "setup.connected_again"))
    if new:  # a new Seahorse: the first conversation starts
        intro.start()
    print(f"OK {service.logs_command()}")
    return 0


def restart() -> int:
    """Starts the service again, so that it reads the new settings."""
    from . import service
    service.restart()
    print("OK")
    return 0


STEPS = {"test-ai": test_ai, "vault": vault, "deploy-key": deploy_key, "connect": connect, "clone": clone,
         "start": start, "restart": restart}


def main(step: str, args: list[str]) -> int:
    return STEPS[step](*args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))
