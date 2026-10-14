"""The `seahorse` command."""
import argparse
import logging
import sys

from . import __version__


def main() -> None:
    parser = argparse.ArgumentParser(prog="seahorse", description="Seahorse: your second brain on Telegram and Obsidian.")
    parser.add_argument("--version", action="version", version=f"seahorse {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="set up Seahorse, or change the settings")
    commands.add_parser("run", help="run the bot and the agents (the background service runs this)")
    update = commands.add_parser("update", help="install the newest release")
    update.add_argument("--yes", action="store_true", help="do not ask before installing")
    commands.add_parser("migrate", help="apply the vault changes of this version")
    commands.add_parser("status", help="show what is running")
    commands.add_parser("doctor", help="check the setup and say what is wrong")
    commands.add_parser("logs", help="show how to read the logs")
    capture = commands.add_parser("capture", help="save text from standard input as an engram")
    capture.add_argument("channel", nargs="?", default="other", choices=["claude", "obsidian", "other"])
    agent = commands.add_parser("agent", help="run one agent now")
    agent.add_argument("name", choices=["integrator", "notes", "morning", "reviewer", "weekly", "messenger"])
    commands.add_parser("lint", help="check the vault against its rules")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if args.command == "setup":
        from . import setup
        setup.run()
    elif args.command == "run":
        from . import bot
        bot.run()
    elif args.command == "update":
        from . import updates
        print(updates.update(assume_yes=args.yes))
    elif args.command == "migrate":
        from . import updates
        for line in updates.migrate_vault() or ["nothing to migrate"]:
            print(line)
    elif args.command == "status":
        from . import config, service, vault
        print(f"Seahorse {__version__}\nservice: {service.status()}\nvault: {config.VAULT}\nprovider: {config.PROVIDER}")
        if config.VAULT.exists():
            print(f"pending engrams: {len(vault.engrams('pending'))} · unclear: {len(vault.engrams('unclear'))}")
    elif args.command == "doctor":
        sys.exit(doctor())
    elif args.command == "logs":
        from . import service
        print(service.logs_command())
    elif args.command == "capture":
        from . import capture as capture_module
        capture_module.main(args.channel)
    elif args.command == "agent":
        from . import agents
        print(agents.AGENTS[args.name]() or "nothing to do")
    elif args.command == "lint":
        from . import config, lint
        sys.argv = ["lint", str(config.VAULT)]
        sys.exit(lint.main())


def doctor() -> int:
    """Checks each part of the setup and prints what to fix. Exit code 1 if something is wrong."""
    from . import config, llm, service, telegram_api, vault
    checks = [
        ("config file", config.CONFIG_FILE.exists(), f"run `seahorse setup` ({config.CONFIG_FILE} is missing)"),
        ("vault", (config.SYSTEM / "_AGENTS.md").exists(), f"no vault at {config.VAULT}: run `seahorse setup`"),
        ("telegram", bool(config.TELEGRAM_TOKEN and telegram_api.bot_name(config.TELEGRAM_TOKEN)), "bad or missing TELEGRAM_BOT_TOKEN"),
        ("telegram user", bool(config.USER_ID), "no telegram user_id: run `seahorse setup`"),
        ("ai", *llm.available()),
        ("voice", bool(config.GROQ_API_KEY) or _has_local_whisper(), "no GROQ_API_KEY and no local whisper: voice notes will fail"),
        ("service", service.status() in {"active", "running"}, f"the service is {service.status()}: run `seahorse setup`"),
    ]
    if config.VAULT.exists() and vault.has_remote():
        pushed = vault.git("push", "--dry-run", check=False).returncode == 0
        checks.append(("vault sync", pushed, "cannot push the vault: check the deploy key has write access"))
    failed = 0
    for name, ok, hint in checks:
        print(f"{'✓' if ok else '✗'} {name}" + ("" if ok else f": {hint}"))
        failed += not ok
    return 1 if failed else 0


def _has_local_whisper() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    main()
