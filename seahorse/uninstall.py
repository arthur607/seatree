"""`seahorse uninstall`: removes Seahorse from the machine that runs it.

It stops and removes the background service, deletes ~/.seahorse (settings, keys, tokens, state) and the program.
The vault stays, unless the person asks to delete it too. Things outside the machine stay as they are: the
GitHub repository, the Telegram bot, and the tools other programs can use (Claude Code, Codex, gh, uv, git).
"""
import shutil
import subprocess

import questionary

from . import config, i18n


def _t(name: str, **values) -> str:
    return i18n.t(f"uninstall.{name}", **values)


def run(assume_yes: bool = False) -> int:
    from . import service, vault
    print(_t("what", home=config.HOME, vault=config.VAULT))
    if not assume_yes and not questionary.confirm(_t("confirm"), default=False).ask():
        print(_t("cancelled"))
        return 1
    delete_vault = False
    if config.VAULT.exists() and not assume_yes:
        synced = (config.VAULT / ".git").exists() and vault.has_remote()
        delete_vault = bool(questionary.confirm(_t("vault_ask_synced" if synced else "vault_ask", path=config.VAULT),
                                                default=False).ask())
    service.uninstall()
    print(_t("removed_service"))
    # Delete only folders that are clearly Seahorse's, even if a setting points somewhere else by mistake.
    if config.HOME.name == ".seahorse" or (config.HOME / "config.toml").exists():
        shutil.rmtree(config.HOME, ignore_errors=True)
        print(_t("removed_home", home=config.HOME))
    if delete_vault and (config.SYSTEM / "_AGENTS.md").exists():
        shutil.rmtree(config.VAULT, ignore_errors=True)
        print(_t("removed_vault", path=config.VAULT))
    elif config.VAULT.exists():
        print(_t("kept_vault", path=config.VAULT))
    uv = shutil.which("uv") or str(config.HOME.parent / ".local/bin/uv")
    subprocess.run([uv, "tool", "uninstall", "seahorse-brain"], capture_output=True)
    print(_t("removed_program"))
    print("\n" + _t("left"))
    return 0
