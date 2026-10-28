# seahorse Blueprint

## System Overview

Seahorse is a long-running personal agent. The phone talks to a Telegram bot. The bot writes each message into an Obsidian vault as a pending engram. Agents, spawned as Claude Code or Codex inside that vault, file the engram into Intention, Memory, Knowledge, and Identity. Notes stay Markdown in a git repository on the machine that runs the process. This tree is the upstream package `seahorse-brain` 1.4.2. The human [README.md](./README.md) is the install guide and is not this contract.

The process is [`seahorse run`](./seahorse/cli.py), kept up by systemd or launchd. Arch and macOS are not a second runtime in this tree: a laptop that only set up a server stores `server.toml` and forwards `seahorse` commands over SSH.

## Local Interface & Invariants

[`install.sh`](./install.sh) is the only source file in the repository root.

- [`install.sh:fail`](./install.sh): prints to stderr and exits 1.
- [`install.sh:latest_tag`](./install.sh): prints a GitHub release tag, or nothing.
- [`install.sh:REPO`](./install.sh): `arthur607/seatree`.
- Non-Linux and non-Darwin abort. Linux installs `git` only through `apt-get` when it is missing. Darwin does not install git.
- `SEAHORSE_SOURCE_DIR`, if set, is the install source and skips the release lookup. `SEAHORSE_NO_SETUP`, if non-empty, exits 0 before `seahorse setup`.
- The script may pipe `https://astral.sh/uv/install.sh` to `sh`, then `uv tool install --force --python 3.12` of the tag zip or of `SEAHORSE_SOURCE_DIR`.

## Subdirectories & System Boundaries

- [seahorse](./seahorse/EGM.md): bot, agents, vault, week, people, setup, and the Claude/Codex backends. Child contracts: [llm](./seahorse/llm/EGM.md), [i18n](./seahorse/i18n/EGM.md).

`tests/` is unittest only and has no contract. `docs/`, `assets/`, `prompts/`, `fonts/`, and `vault_template/` have no source files in the recognized set and have no contract.

## Global Invariants

- One machine runs the agents. [`cli.ON_THE_SERVER`](./seahorse/cli.py) forwards `update`, `migrate`, `status`, `doctor`, `logs`, `agent`, `lint`, and `uninstall` over SSH when this computer has `server.toml` and no `config.toml`.
- [`config.PROVIDER`](./seahorse/config.py) selects the CLI. Only the string `codex` leaves Claude. [`llm.run`](./seahorse/llm/__init__.py) does not call an HTTP chat API.
- The integrator's Telegram line is not the write. [`agents.parse_results`](./seahorse/agents.py) only edits the confirmation. `state` leaves `pending` only if the CLI wrote the engram file.
- Agent commits are `Seahorse <seahorse@localhost>` ([`vault.AGENT_AUTHORS`](./seahorse/vault.py), [`llm.AGENT_GIT`](./seahorse/llm/__init__.py)). Any other author counts as the user.
- Claude deny rules cover `~/.seahorse`, secrets, `.ssh`, and `.obsidian`. Codex does not install those deny rules.
- Chat settings ([`settings.KEYS`](./seahorse/settings.py)) are applied only after the user confirms. The model must not write `config.toml` for them.
- [`updates.update`](./seahorse/updates.py) installs from `arthur607/seatree`.
- Vault folder names stay English for every language ([`config.use_vault`](./seahorse/config.py)).

## Global Side Effects

- Telegram Bot API, from [`bot.run`](./seahorse/bot.py) and [`telegram_api.send`](./seahorse/telegram_api.py). Outbound HTTPS. No inbound port in this code.
- Claude Code or Codex subprocess, cwd the vault, from [`llm.run`](./seahorse/llm/__init__.py). The prompt for Claude is stdin. The prompt for Codex is argv.
- Git fetch, rebase or merge, commit, and push of the vault, from [`vault.pull`](./seahorse/vault.py) and [`vault.publish`](./seahorse/vault.py). GitHub is optional and only if `origin` exists.
- Groq transcription when `GROQ_API_KEY` is set ([`transcribe.transcribe`](./seahorse/transcribe.py)). Otherwise a local Whisper model, or audio kept in the vault.
- `~/.seahorse/config.toml`, `secrets.env`, and `state/` on the machine that runs the process. SSH setup key `~/.seahorse/server_key` when a server was provisioned ([`machine.Server`](./seahorse/machine.py)).
- [`install.sh`](./install.sh) and [`updates.install`](./seahorse/updates.py) download and install the tool with `uv`. [`setup.run`](./seahorse/setup.py) may `apt-get` on the server and open login URLs in the browser.

## System Verification

- Run: `uv run python -m unittest discover -s tests`
