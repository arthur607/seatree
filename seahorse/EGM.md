# seahorse

## Overview

Python package that keeps an Obsidian vault alive. A Telegram bot captures messages as engrams. Scheduled agents file them, plan the week, and message first. The vault is Markdown plus git. The model is not called over HTTP from this package: [`llm.run`](./llm/__init__.py) spawns Claude Code or Codex in the vault directory.

Child contracts: [i18n](./i18n/EGM.md), [llm](./llm/EGM.md).

## Interface

Entry and process:

- [`cli.main`](./cli.py): `seahorse` subcommands `setup`, `version`, `run`, `update`, `migrate`, `status`, `doctor`, `logs`, `capture`, `agent`, `lint`, `uninstall`, `provision`.
- [`bot.run`](./bot.py): long-polling bot. Raises `SystemExit` if `TELEGRAM_TOKEN`, `USER_ID`, or the vault is missing.
- [`service.install`](./service.py): systemd unit or launchd agent that runs `seahorse run`. Linux and Darwin only.
- [`config.PROVIDER`](./config.py): `SEAHORSE_PROVIDER` or `[ai].provider`. Default `claude`.
- [`config.use_vault`](./config.py): rebinds vault paths. Folder names stay English.

Capture and filing:

- [`vault.new_engram`](./vault.py) / [`vault.save_engram`](./vault.py): write a pending engram. `save_engram` serializes threads in one process only.
- [`agents.integrator`](./agents.py): files pending engrams. [`agents.AGENTS`](./agents.py) maps `integrator`, `notes`, `morning`, `reviewer`, `weekly`, `messenger`.
- [`agents.parse_results`](./agents.py): reads `<engram> => …` lines. That text is the Telegram caption, not the filing.
- [`capture.capture`](./capture.py): stdin/other-channel capture. Channel must be `claude`, `obsidian`, or `other`.

Vault and week:

- [`vault.pull`](./vault.py) / [`vault.publish`](./vault.py): fetch/rebase or merge, then commit as Seahorse and push.
- [`vault.lock`](./vault.py): exclusive `fcntl` lock on `config.LOCK`. `wait=False` raises [`vault.Busy`](./vault.py).
- [`week.reschedule`](./week.py): moves open lines off past days. Does not call a model.
- [`week.refresh`](./week.py): rewrites day-card totals from code.
- [`tasks.sync`](./tasks.py): checkbox in the week and `done` on the task note stay aligned.
- [`lint.check`](./lint.py): vault rules. [`vault.validate`](./vault.py) runs it as a subprocess.

People, now, retro, settings:

- [`people.prepare`](./people.py) / [`people.converse`](./people.py): birthday, briefing, reconnect. Caller holds the vault lock for `prepare`.
- [`now.trigger`](./now.py) / [`now.press`](./now.py): "I have N minutes".
- [`retro.send_week`](./retro.py): Sunday image. [`week_image.week_png`](./week_image.py) needs no model.
- [`settings.apply`](./settings.py): writes a confirmed chat setting into `config.toml`. Language, AI, tokens, and vault are not in [`settings.KEYS`](./settings.py).

Setup and updates:

- [`setup.run`](./setup.py): interactive setup. [`provision.STEPS`](./provision.py): `test-ai`, `vault`, `deploy-key`, `connect`, `clone`, `start`, `restart`.
- [`updates.migrate_vault`](./updates.py): runs [`migrations.MIGRATIONS`](./migrations.py) under the vault lock. A failed step runs again.
- [`telegram_api.send`](./telegram_api.py): Bot API. [`transcribe.transcribe`](./transcribe.py): Groq, then local Whisper.

## Invariants

- [`llm._backend`](./llm/__init__.py): `codex` selects Codex. `opencode` selects OpenCode. `claude` selects Claude. Any other value raises `ValueError`.
- [`agents.integrator`](./agents.py): one run per process. A batch is the oldest 5 pending engrams. An outage does not increment attempts. At 3 non-outage failures a capture becomes `unclear`, or `discarded` if it was an already-answered question.
- [`agents.parse_results`](./agents.py): a missing result line does not undo files the model wrote. The confirmation falls back to the engram `state`.
- [`vault.new_engram`](./vault.py): does not take `vault.lock` and does not commit. Frontmatter `state` starts as `pending`. The body is not rewritten by this function.
- [`vault.AGENT_AUTHORS`](./vault.py): a commit whose author is not `Seahorse` counts as the user. Agent commits use `Seahorse <seahorse@localhost>`.
- [`claude.denied`](./llm/claude.py): deny rules are always passed. Codex does not apply that list. Codex `write=True` is `workspace-write` with `approval_policy=never`.
- [`bot.run`](./bot.py): message handlers are filtered to `config.USER_ID`. Commands are registered before free text.
- [`tasks.sync`](./tasks.py): only `*.md` notes with `type: task` under `config.TASKS`. `done` is true only when the field string lowercases to `true`.
- [`settings.KEYS`](./settings.py): chat may change quiet hours, morning, weekly day and time, daily message cap, and timezone. Nothing else.
- [`migrations.MIGRATIONS`](./migrations.py): a step runs only when the vault version is older than the step and the step is not newer than the code. A new vault is stamped so no step runs. Steps must be safe to run twice.
- [`intro.current`](./intro.py): an intro question expires after 24 hours without writing `done`.

## Side Effects

- Importing [`config`](./config.py) reads `config.toml` and `secrets.env` and may `setdefault` secrets into `os.environ`.
- [`llm.run`](./llm/__init__.py): spawns `claude -p` or `codex exec` with cwd the vault. See [llm](./llm/EGM.md).
- [`bot.run`](./bot.py): Telegram long polling. Captures write engrams and schedule the integrator. Voice may call Groq or write audio under `state/voice`.
- [`agents.integrator`](./agents.py): `vault.pull` / `vault.publish`, `llm.run(..., write=True)`, Telegram edits, `captures.json`, `followups.json`.
- [`vault.pull`](./vault.py) / [`vault.publish`](./vault.py): git fetch, rebase or merge, commit, push. Conflict copies stay in the vault. A failed fetch does not raise.
- [`week.reschedule`](./week.py): may rewrite week files, task notes, and `state/moves.json`.
- [`people.converse`](./people.py) / [`companion.converse`](./companion.py): model call outside the lock, then `vault.lock` for send and publish.
- [`messenger.run`](./messenger.py): under `vault.lock`, pull, reminders, queue, people or companion. At most one outbound branch.
- [`updates.update`](./updates.py): `uv tool install` from `https://github.com/arthur607/seatree`. Rolls back to the running version on failure.
- [`service.install`](./service.py): writes a systemd unit or launchd plist and starts it.
- [`setup.run`](./setup.py): SSH, browser logins, `apt-get` on the server, writes `~/.seahorse`.
- [`transcribe.transcribe`](./transcribe.py): POST to Groq when `GROQ_API_KEY` is set. Local model is `WhisperModel("small")` on CPU.

## Verification

- Run: `uv run python -m unittest discover -s tests`
