"""The `seahorse` command."""
import argparse
import logging
import shlex
import sys

from . import __version__


def main() -> None:
    parser = argparse.ArgumentParser(prog="seahorse", description="Seahorse: your second brain on Telegram and Obsidian.")
    parser.add_argument("--version", action="version", version=f"seahorse {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="set up Seahorse, or change the settings")
    commands.add_parser("version", help="show the version here, on the server, and the newest release")
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
    uninstall = commands.add_parser("uninstall", help="remove Seahorse from the machine that runs it (the vault stays)")
    uninstall.add_argument("--yes", action="store_true", help="do not ask; keep the vault")
    provision = commands.add_parser("provision")  # used by the setup, not by people
    provision.add_argument("step")
    provision.add_argument("values", nargs="*")
    args = parser.parse_args()

    # On the person's computer, when Seahorse runs on a server, these commands run on the server.
    server = _server()
    if server and args.command in ON_THE_SERVER:
        logs = "journalctl -u seahorse -f -n 50" if server.user == "root" else "journalctl --user -u seahorse -f -n 50"
        command = logs if args.command == "logs" else "seahorse " + shlex.join(sys.argv[1:])
        code = server.interactive(command)
        if args.command == "uninstall" and code == 0:
            _forget_server(server)
        sys.exit(code)

    # The setup talks to a person: only real problems go to the screen.
    logging.basicConfig(level=logging.WARNING if args.command == "setup" else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if args.command == "version":
        version(server)
    elif args.command == "setup":
        from . import setup
        setup.run()
    elif args.command == "uninstall":
        from . import uninstall as uninstall_module
        sys.exit(uninstall_module.run(assume_yes=args.yes))
    elif args.command == "provision":
        from . import provision as provision_module
        sys.exit(provision_module.main(args.step, args.values))
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


def version(server) -> None:
    """The version on this computer, on the server it set up, and the newest release on GitHub."""
    from . import updates
    print(f"seahorse {__version__}")
    if server:
        there = server.sh("seahorse --version").stdout.strip().removeprefix("seahorse ") or "not installed"
        print(f"on {server.host} ({server.user}): {there}")
    found = updates.latest()
    if found and found["version"]:
        newer = " (run `seahorse update`)" if updates.newer(found["version"]) else ""
        print(f"newest release: {found['version']}{newer}")


ON_THE_SERVER = {"update", "migrate", "status", "doctor", "logs", "agent", "lint", "uninstall"}


def _forget_server(server) -> None:
    """After an uninstall on the server: removes the setup key from the server, and the server from this computer.
    The Linux user of the person goes too, unless their vault stays in it."""
    from . import config, i18n
    admin = server.admin
    # Only the key of this computer: another computer may have set up the same server.
    public = shlex.quote(server.key.with_suffix(".pub").read_text(encoding="utf-8").strip())
    forget = (f"grep -vxF {public} ~/.ssh/authorized_keys > ~/.ssh/.keys; "
              "cat ~/.ssh/.keys > ~/.ssh/authorized_keys; rm -f ~/.ssh/.keys")
    if admin and server.user.startswith("seahorse-") and admin.sh(f"test -d /home/{server.user}/seahorse-vault").returncode != 0:
        admin.sh(f"loginctl disable-linger {server.user}; pkill -u {server.user}; sleep 1; userdel -r {server.user}")
    else:
        server.sh(forget)
    if admin and admin is not server and not admin.people():  # nobody left: root forgets this computer too
        admin.sh(forget)
    for name in ("server.toml", "server_key", "server_key.pub"):
        (config.HOME / name).unlink(missing_ok=True)
    print("\n" + i18n.t("uninstall.local_done", host=server.host))


def _server():
    """The server that runs Seahorse, when this computer only set it up: no config.toml here, but a server.toml."""
    import tomllib
    from . import config
    from .machine import Server
    path = config.HOME / "server.toml"
    if config.CONFIG_FILE.exists() or not path.exists():
        return None
    data = tomllib.loads(path.read_text(encoding="utf-8")).get("server", {})
    config.LANGUAGE = data.get("language", config.LANGUAGE)  # the messages on this computer
    if not data.get("host"):
        return None
    admin = Server(data["host"], data.get("admin", "root"))
    user = data.get("user", admin.user)
    return admin if user == admin.user else Server(data["host"], user, admin)


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
