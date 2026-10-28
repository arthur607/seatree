# llm

## Overview

The AI behind the agents. Each provider is an agentic CLI that can read and edit the vault: Claude Code, Codex, or OpenCode. [`run`](./__init__.py) picks the backend from [`config.PROVIDER`](../config.py) and returns an [`Answer`](./__init__.py). There is no HTTP client in this package.

## Interface

- [`Answer`](./__init__.py): `text`, `session`, `error`.
- [`ROLES`](./__init__.py): role to `(tier, effort)`. `classify` / `phrase` / `now` are `(fast, low)`. `planner` / `observer` are `(deep, high)`. The rest of the named roles are `standard`.
- [`run`](./__init__.py): `run(text, role, write=False, session=None, max_turns=80, timeout=3600, web=False) -> Answer`. `write` may create and edit vault files. `web` is read-only with web search. `session` continues an earlier conversation when the backend supports it.
- [`available`](./__init__.py): `available() -> tuple[bool, str]`. Used by `seahorse doctor` and setup.
- [`safe_env`](./__init__.py): environment for the CLI, with Seahorse secrets removed except `keep`, and git identity forced to Seahorse.
- [`workdir`](./__init__.py): the vault if it is a directory, otherwise `config.HOME`.
- [`prompt`](./__init__.py): text of `prompts/<name>.md` with `$placeholders` filled.
- [`OTHER_SECRETS`](./__init__.py): `TELEGRAM_BOT_TOKEN`, `GROQ_API_KEY`, `GH_TOKEN`, `GITHUB_TOKEN`.
- [`AGENT_GIT`](./__init__.py): author and committer `Seahorse <seahorse@localhost>`.
- Claude: [`claude.run`](./claude.py), [`claude.available`](./claude.py), [`claude.denied`](./claude.py), [`claude.binary`](./claude.py), [`TIERS`](./claude.py) (`fast`/`standard`/`deep` to `haiku`/`sonnet`/`opus`).
- Codex: [`codex.run`](./codex.py), [`codex.available`](./codex.py), [`codex.binary`](./codex.py).

## Invariants

- [`_backend`](./__init__.py): `codex` selects Codex. `opencode` selects [`opencode.py`](./opencode.py). `claude` selects Claude. Any other value raises `ValueError`.
- [`run`](./__init__.py): tier and effort come only from `ROLES[role]`. There is no default role. The model forwarded is `config.MODEL_OVERRIDES.get(tier)`, which may be `None`.
- [`safe_env`](./__init__.py): strips `OTHER_SECRETS` and every key parsed from `secrets.env`, except names in `keep`. `AGENT_GIT` is merged last. `os.environ` is not assigned.
- [`claude.run`](./claude.py): the prompt is stdin, not argv. `--effort` is omitted when `tier == "fast"`. `write=True` uses `--permission-mode acceptEdits` and [`WRITE`](./claude.py) (`git mv` and `mkdir` only, plus web tools). `write=False` disallows `Write`, `Edit`, `NotebookEdit`, and `Bash`. [`denied`](./claude.py) is always passed; deny wins over allow. A deny rule is omitted when its folder holds the vault.
- [`claude.answer_from`](./claude.py): `error` is true when `is_error` is set or `subtype` is not `success`. The subprocess return code is not checked.
- [`claude.available`](./claude.py): ready only when the `claude` binary exists and `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` is in the environment. It does not read `secrets.env` itself.
- [`codex.run`](./codex.py): each run starts a new session. `tier`, `session`, and `max_turns` are accepted and unused. The prompt is argv. Sandbox is `workspace-write` when `write` is true, otherwise `read-only`. `approval_policy` is `never`. This file does not apply Claude's deny list.
- [`codex.available`](./codex.py): not installed when `which` fails. Otherwise login is `codex login status`. API key env vars are not checked here.
- [`NEVER_EDIT`](./claude.py): `./.obsidian/**` is always an edit disallow.

## Side Effects

- [`run`](./__init__.py): logs provider, role, tier, `write`, and `web`, then spawns the selected CLI.
- [`workdir`](./__init__.py): creates `config.HOME` when the vault directory does not exist.
- [`prompt`](./__init__.py): reads `prompts/<name>.md`.
- [`safe_env`](./__init__.py): reads `secrets.env` when it exists. Does not write it.
- [`claude.run`](./claude.py): spawns `claude -p` with cwd the vault, timeout as given, and an environment that keeps only Claude login secrets.
- [`codex.run`](./codex.py): spawns `codex exec` the same way. Writes a temporary `-o` file and deletes the temp directory when the block exits.
- [`codex.available`](./codex.py): spawns `codex login status`.

## Verification

- Run: `uv run python -m unittest discover -s tests`
