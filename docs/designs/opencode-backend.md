# OpenCode backend on the Seahorse fork

Published from `concepts_brief.md`. The GitHub account that can push is `arthur607`, so the remote is `arthur607/seatree`. `arthur/seatree` does not exist.

* Author: arthur
* Date: 2026-10-28

## Overview

This cut is a fork of the Seahorse checkout at `/home/arthur/seahorse` (upstream `lorenzoa7/seahorse`, package `seahorse-brain` 1.4.2, MIT). It adds one command-line backend, OpenCode, beside Claude and Codex. OpenCode talks to xAI with an API key. The bot stays on Telegram. The process runs on a Debian server. Arch and macOS only reach that server over the NetBird mesh that already exists, and only for SSH and git.

The idea is smaller than it sounds, and it is not safe to start coding the backend yet.

What is still wrong after the owner's answers:

- The provider switch is a lie until the code changes. In `seahorse/llm/__init__.py`, `_backend()` loads Codex only when `config.PROVIDER == "codex"`. Every other value loads Claude. The owner wants setup to configure xAI. That choice does not exist in `setup.py` today. Anything that is not Claude is treated as Codex, and "logged in" means `~/.codex/auth.json` exists.
- The integrator stays on the CLI. That is the owner's call. It is also the hole: `parse_results` does not write the vault. If the CLI files a task and leaves the engram `pending`, the next run can file it again. The exam still counts task files. This cut does not add a second store, and it does not turn retries off. "It should work" is not a check the code runs.
- Update must point at `arthur/seatree`, not `lorenzoa7/seahorse`. The owner named that remote. `updates.py` and `install.sh` still say `lorenzoa7/seahorse`. A laptop with only `server.toml` forwards `seahorse update` to Debian. Until those two files say `arthur/seatree`, that command still wipes the backend. If `arthur/seatree` has no GitHub release, `latest()` returns empty and update installs nothing. That is safer than upstream, and it is not an update that delivers this backend. Until a release exists, install from the local tree with `SEAHORSE_SOURCE_DIR`.
- OpenCode's defaults are the wrong shape to copy from Codex. What is not denied is approved. The default permissions allow `external_directory`. The prompt-on-stdin behavior, the config directory, and the JSON event fields have not been read from `opencode run --help` on the Debian host. Treating them as known would make the outage check and the secret check fictional.

Who this is for: one person who already runs Seahorse, wants the agent on a Debian box they control, and already has a NetBird name for that box. Success is not "OpenCode starts." Success is a copied template vault in which one pending engram becomes one task, that engram's state is `integrated`, and a second integrator run does not add a second task.

The six review lenses were not run as separate agents. The harness refused nested subagents twice (`Subagent depth limit reached`). The notes below are one pass over those lenses against the checkout, not six independent reviews.

## Analysis

### The fork deletes itself

`seahorse/updates.py` sets `REPO` to `lorenzoa7/seahorse`. `install.sh` does the same. `updates.install` runs `uv tool install --force` on the upstream zip. `updates._give_up` installs the previous upstream version again after a failed update. `seahorse version` asks GitHub for the latest upstream tag and, when that tag is newer, prints `run seahorse update`. From Arch or macOS, `update` is in `ON_THE_SERVER` in `seahorse/cli.py`, so the laptop does not update itself. It updates the server.

A local backend that is not in that zip does not survive. "Remember not to press the button" is not a fix. The button is in Telegram, and the version line recommends the command.

`install.sh` already has a way out: if `SEAHORSE_SOURCE_DIR` is set, it installs that directory and does not download a release. That is the install path for this cut. The update command still has to refuse, in words a person can act on, or the version line will keep offering the wipe.

Do not invent a GitHub organization or a release process for the fork in this cut. None was named.

### The success test is a hole in the current state machine

An engram is a captured note. `pending` means it is waiting. `integrated` means each piece has been filed into other notes. `unclear` means three non-outage tries failed, or the agent needs an answer. `discarded` means there was nothing to keep. Those words are in `seahorse/vault_template/6. ⚙️ System/_Vocabulary.md`. The code does not check them. The prompt in `seahorse/prompts/integrator.md` tells the model to set `state` and to look for an existing task before creating one.

`agents._integrate_batch` calls `llm.run(..., write=True)`. Then `parse_results` scans the answer for `<engram> => ...` lines and `_update_confirmations` edits Telegram. The vault write is whatever the CLI did. `_count_attempts` adds one attempt when the state is still `pending` and the answer was not classed as an outage. At `MAX_ATTEMPTS` (3) the engram becomes `unclear`, unless it was a question the bot already answered, in which case it becomes `discarded`.

So a run can create the task, leave the engram pending, and the next run creates another task. A run can also set `integrated` and create two tasks in the same turn. The second run then looks clean because it only selects `state == pending`. Counting the state field is not the exam. Counting task files is.

`lint.py` warns when an engram is marked integrated and no note cites it. It does not warn when two tasks cite one engram. `seahorse status` prints the pending count. It does not print "this engram already produced a task."

The nightly reviewer is told to merge duplicates. That is a later, paid, best-effort cleanup. It is not the invariant. Turning retries off would hide the bug: the second run would not happen, so the exam could not fail. A side database of processed engrams would be a second source of truth next to the file the model is already supposed to edit. Setting `integrated` from the Telegram line alone is worse: that line can claim success when the file was not written.

The design draft has to pick a check the code performs, not the model. The check is not picked here, because the OpenCode result format is unread and the proof has not shown which failure happens first. The rejected checks are in Alternatives Weighed. The draft does not get to leave this as "the prompt says so."

The proof engram has to be one task and nothing else. The integrator prompt files people, journal entries, and research as well as tasks. A rich note would make "one task" the wrong exam. Use a copied template, not real notes, and a single sentence that is only a task.

### OpenCode is not a drop-in, and the contract is unread

Claude (`seahorse/llm/claude.py`) puts the prompt on stdin so another user on the box cannot read it with `ps`. It has a deny list for Seahorse secrets, SSH keys, git credentials, `.git`, and Obsidian plugin code. Deny wins over allow.

Codex (`seahorse/llm/codex.py`) puts the prompt on the command line, sets `approval_policy=never`, and has no deny list. The workspace-write sandbox is the only fence, and the comment in that file says it is the strictest sandbox that can still edit the vault. Copying that file is the wrong pattern for a tool whose default is to allow `external_directory` and to approve what is not denied.

What is known about OpenCode V2, and only this: `opencode run` accepts `--standalone`, `--format json`, `--model`, `--agent`, `--auto`, and `--session`. xAI is a built-in provider package. Auth is an API key or `XAI_API_KEY`. A SuperGrok device login is not a proven path. `--auto` approves what is not denied. Agent permission rules are appended last.

What is not known, and must not be filled in: whether the prompt can go on stdin, which flag sets the config directory, and which JSON fields mean session id, final text, and error. `agents.OUTAGE` is a regex over `answer.text`. If a missing key or a bad model does not match that regex, the engram burns a real attempt and can become `unclear` after three misses. If a half-written run is reported as success, the retry path duplicates notes. Both depend on fields that have not been read on the Debian host.

`llm.safe_env` strips every key named in `secrets.env`, except the provider's `keep` set. Claude keeps `CLAUDE_CODE_OAUTH_TOKEN` and `ANTHROPIC_API_KEY`. Codex keeps `OPENAI_API_KEY` and `CODEX_API_KEY`. An OpenCode keep set that omits `XAI_API_KEY` means the subprocess never sees the key, even when `secrets.env` has it. The systemd unit only sets `SEAHORSE_HOME` and `PATH`. That is fine, because `config.load` reads `secrets.env` into the environment before the subprocess starts. It is not fine if the keep set is copied from Codex.

`llm.available` is what `seahorse doctor` prints. Codex's message says to run `codex login --device-auth`. OpenCode must not copy that sentence. A missing key has to say the key is missing.

Setup will also undo this if it is not taught the third provider. In `seahorse/setup.py`, anything other than `claude` is "logged in" only when `~/.codex/auth.json` exists and `codex` is on `PATH`. The chooser offers Claude and Codex only. A later `seahorse setup` on the server can overwrite `provider` and drop the xAI key. The wizard is not optional polish. It is a footgun on the next setup run.

Model names are the same class of unknown. Roles map to a tier and an effort (`integrator` is standard / medium, 120 turns, one hour). Claude turns the tier into haiku, sonnet, or opus. This cut must not hardcode an xAI model the Debian host has not listed. A missing model fails doctor. It does not guess.

### Cost is one long run, not a traffic spike

There is no request rate to plan for. One process, one integrator at a time (`_integrating` in `agents.py`). A batch is 5 engrams, and one call can run 10 batches. The expensive case is one agentic run: up to 120 turns, up to an hour, with writes and, when `write=True`, web search. The planner is worse (high effort, 90 minutes) and is not part of this exam.

xAI here is API-key billing, not a subscription. Nothing in the code stops a tool loop from spending until the timeout. No dollar figure is invented here. The bound for this cut is the exam: one engram, one integrator run, one template vault, then a second run. Do not enable the nightly reviewer or the weekly planner against that vault. Do not point the systemd service at real notes until a fixture secret outside the vault is denied and the denial is visible in the log.

The server itself is the box already described in the human README, on the order of a small VPS. This cut does not add a second machine.

### The mesh is a hostname, not a feature

NetBird is not in this repository. It is a WireGuard mesh that already exists. SSH and git should use its name the way they already use a host string. `server.toml` stores that string. `seahorse/machine.py` passes it to `ssh`. No exit node, no control plane, and no new NetBird client belong in this cut.

Telegram and the xAI API stay direct HTTPS from Debian. If the mesh is down, the laptop cannot SSH, cannot git, and cannot read `journalctl`. The bot on Debian can still reach Telegram and xAI. That split is the point. Do not route those calls through the mesh to "make the laptop work."

`docs/server.md` disagrees with this deployment. It tells the reader to open port 22 to the internet, to type an IPv4 address, and to rebuild with Ubuntu when the check fails. The code is less strict than the doc: `setup.py` accepts a system whose `/etc/os-release` id is `ubuntu` or `debian`. Debian passes. The doc's "rebuild with Ubuntu" line is the wrong recovery for this box. Arch is a client, not a rejected server, as long as the laptop has `server.toml` and does not also have `config.toml`. If `config.toml` exists, `cli.py` stops forwarding and the laptop becomes a second runtime. `service.py` will install systemd on any Linux, including Arch, and launchd on macOS. This cut does not do that.

Git is the other conflict. The setup creates a private GitHub repo and a deploy key (`git@github.com:...` in `setup.py`). `obsidian.py` clones `https://github.com/...`. The vault then syncs through GitHub, which is direct HTTPS, not the NetBird name. This cut said git uses the NetBird name. Two remotes means the laptop can pull a different history from the one the server just wrote, and the duplicate-task exam would not say which path duplicated it. The proof vault gets one remote: the Debian box, by the NetBird name. It does not also get a GitHub remote. The exact URL is whatever `git ls-remote` accepts over that name. It is not invented here. The existing GitHub flow stays in the upstream code for other installs. It is not the path this exam uses.

`capture` is not forwarded to the server. The proof engram is created on Debian. The laptop does not need a new capture channel for the exam.

### What a person sees when it breaks

`seahorse doctor` and `seahorse status` are the sentences that matter. Doctor must say whether `opencode` is installed, whether the API key is present, and whether the provider value is one of the three names. Status already prints the provider and the pending count. After a proof run it should be possible to see, from the journal, that the run used OpenCode, whether the answer was classed as an outage, and that the second run did not call the model for that engram.

Today `llm.run` logs provider, role, tier, write, and web. It does not log the session id, the return code, or whether `OUTAGE` matched. At 3 AM the path is `journalctl --user -u seahorse` (or without `--user` as root). That is enough if the new lines are in that stream. A tracing product is not. A duplicate that the model then marks integrated never becomes an alert. The exam has to count files, because the log will not.

Words the human README has to define, because they are this project and not general engineering: engram, pending, integrated, provider, and the NetBird name as a hostname rather than a Seahorse feature. The proof vault is a copy of `seahorse/vault_template`, not the person's notes.

`README.md` stays. It is the operator document. The `EGM.md` files are contracts for a different workflow. They are not the README, and this brief must not tell a later draft to point people at them.

### What this adds to the maintenance load

A third backend next to `claude.py` and `codex.py` is the whole new module. `_backend()` grows one explicit branch. Unknown values fail closed instead of loading Claude. That is a behavior change: today a junk provider value silently runs Claude. Untouched installs stay on Claude, because the default in `config.py` is `"claude"`.

The brittle part is not the third file. It is the integrator's trust in the model to both write the notes and flip one field, plus an update command that can delete the file. Leaving either of those as-is means the backend is temporary. A Go rewrite would replace the state machine and is deferred. Starting it here adds a second codebase before the first exam exists.

Two years of this cut, if the update refusal and the duplicate check land, is: one more CLI to install on Debian, one more key in `secrets.env`, and a fork that does not follow upstream releases until someone ports them by hand. That last cost is real. It is still smaller than an update that silently returns the box to Claude.

## Goals vs. Non-Goals

* In-Scope Goals:
  * A third backend, selected only when the provider is the OpenCode value, installed on the Debian box from a local source directory.
  * xAI auth by API key (`XAI_API_KEY` or the key in `secrets.env`), passed through the existing `safe_env` keep set. Doctor says the key is missing. It does not suggest a device login.
  * Telegram stays as it is.
  * The process stays up with the existing systemd unit on Debian. Arch and macOS stay clients: `server.toml` host is the NetBird name, commands in `ON_THE_SERVER` go over SSH, and git of the proof vault uses that name.
  * `seahorse update`, Telegram `/update`, and the version line install `arthur/seatree`, not `lorenzoa7/seahorse`. Until `updates.py` and `install.sh` say `arthur/seatree`, update still wipes the backend. Until that repo has a release, install from the local tree with `SEAHORSE_SOURCE_DIR`.
  * Setup configures xAI as a provider: OpenCode on the machine, API key in `secrets.env`, provider value written in `config.toml`. It must not treat that choice as Codex, and it must not require `~/.codex/auth.json`.
  * Unknown provider values fail closed. `claude` and `codex` keep their current behavior.
  * One exam on a copied template: one pending engram that asks for one task, one integrator run, one task file, `state: integrated`, a second run that does not add a task and does not need to call the model for that engram.
  * The human `README.md` states the fork's provider, the update refusal, and the NetBird hostname in plain sentences. `EGM.md` is not that document.
  * Before any adapter is written, `opencode run --help` and one failed auth run are captured on the Debian host.

* Out-of-Scope Non-Goals:
  * Matrix, a Grok Bot, or a Go rewrite.
  * A NetBird exit node, control plane, or any NetBird code in this repository.
  * A SuperGrok subscription or an unproven device login.
  * Running the proof, or the service, against real notes.
  * Replacing Claude or Codex for installs that still set those providers.
  * A new git host, a second GitHub remote on the proof vault, or a published fork release.
  * The nightly reviewer, the weekly planner, or a token-spend cap beyond "this exam does not start those jobs."
  * Making Arch or macOS the runtime. No new launchd agent for this cut.
  * Routing Telegram or the xAI API through the mesh.
  * Inventing OpenCode stdin, config-dir, JSON fields, or model ids that the Debian host has not shown.

## Alternatives Weighed

Each of these was rejected. A later design draft can copy this list into Alternatives Considered.

* Do nothing, and keep Claude or Codex. Rejected because the cut is specifically an xAI API key through OpenCode. Claude's key and Codex's device login are different accounts. The selector cannot express that today.
* Set `PROVIDER` to `opencode` and leave the `else` branch as Claude. Rejected because that branch is Claude. The box would bill the wrong account, or fail as Claude, and the exam would not be an OpenCode run.
* Copy `codex.py`: prompt on the command line, `approval_policy=never`, no deny list. Rejected because another user on the server can read a command line, and OpenCode's default allows paths outside the work directory. Approving everything that is not denied, with nothing denied, is how a vault note gets to `secrets.env`.
* Paste Claude's deny-rule syntax into OpenCode before `opencode run --help` is read. Rejected because the rule format is not known. The intent of that deny list is reused. The syntax is not.
* Authenticate with a SuperGrok subscription or a device login. Rejected because that login is not a proven path. The stated auth is an API key.
* Start the Go rewrite so the state machine can be "done properly." Rejected for this cut. The duplicate hole can be closed in the Python that already owns the engram file. A second language does not answer the unread CLI contract.
* Add Matrix, or a Grok Bot, as the control channel when the mesh is down. Rejected. Telegram stays. The mesh is for SSH and git only. A down mesh does not justify a second messenger.
* Build a NetBird control plane or an exit node so Telegram and xAI ride the mesh. Rejected. Those calls stay direct HTTPS from Debian. The mesh is already there. Seahorse does not operate it.
* Leave `REPO` on `lorenzoa7/seahorse` and rely on nobody pressing `/update`. Rejected because `seahorse version` recommends the command, the laptop forwards it to Debian, and the bot has a button for it.
* Refuse every update, with no fork remote. Rejected by the owner. Update stays. It installs their fork. A missing remote name is still a hole: the code cannot point at a repository that has not been named.
* Publish the fork as GitHub releases under an organization this brief does not name. Rejected for this cut. `SEAHORSE_SOURCE_DIR` already installs a local tree. A release process is a later decision, not a prerequisite for the exam.
* Trust the integrator prompt to avoid duplicate tasks. Rejected because that is the current design, and a missing `state: integrated` still retries.
* Set `state: integrated` from the Telegram result line alone. Rejected because `parse_results` never sees the files. The line can say the task was filed when it was not, and can be missing when it was.
* Turn retries off so the second run cannot duplicate. Rejected because the exam is the second run. Hiding the retry does not prove it is safe. An outage must still not consume an attempt.
* Add a side database of engrams already filed. Rejected. The vault file is the record. A second store will drift from it.
* Keep a GitHub remote on the proof vault and also say git uses the NetBird name. Rejected. Two remotes make the exam ambiguous. The proof vault has one remote, the Debian host by its NetBird name, or no remote until that SSH fetch works. Not both.
* Put the engram text on the process command line if stdin turns out to be unavailable. Not chosen. If `--help` shows no stdin, the exposure is written down and the draft decides. It is not silently copied from Codex.
* Run the exam on a real vault. Rejected. The proof vault is a copy of the template. A deny-list failure on real notes is not an acceptable way to learn the permission syntax.
* Treat `EGM.md` as the operator README, or replace `README.md`. Rejected. `README.md` stays and is edited. The contracts are not the product instructions.
* Install the systemd or launchd service on the Arch or macOS client. Rejected. Those machines forward commands. A local `config.toml` would stop the forwarding. The client setup for this cut is `server.toml` only.
* Hardcode an xAI model id, a JSON field, or a config-dir flag from memory. Rejected until the Debian host prints them.

## Key Decisions & Staging for Next Steps

Decisions this ideation actually settled:

* The change is a third Python module beside `claude.py` and `codex.py`, chosen by an explicit provider value. Unknown values fail closed. Existing `claude` and `codex` installs are unchanged. The default remains Claude.
* Auth is an API key in `secrets.env`, included in that backend's `safe_env` keep set. No device login, no SuperGrok path.
* Update installs `arthur/seatree`, not upstream. `REPO` in `updates.py` and `install.sh` becomes that string. A local `SEAHORSE_SOURCE_DIR` install remains the path until `arthur/seatree` has a GitHub release.
* Setup configures xAI by writing `provider = "opencode"`. The person sees that string in `config.toml`. The mechanism is the OpenCode CLI plus an API key in `secrets.env`. The wizard does not reuse the Codex login test for `opencode`.
* Debian is the runtime. The code already accepts Debian. The human README must not send this operator to "rebuild with Ubuntu" or to a public IP as the way to reach the box. Arch and macOS are clients. The host string is the NetBird name. No mesh code is added.
* The proof vault has a single git path, SSH to that name. It is not also a GitHub repo. GitHub remains how upstream Seahorse is published. It is not how this exam syncs notes.
* The integrator keeps writing through the CLI. Python does not file the task itself, and it does not invent a second record of what was filed. The exam still counts task files on a template copy. If the second run adds a task, the exam failed. Prompt-only, Telegram-line-only, retry-off, and a side database stay rejected.
* The adapter is not written until `opencode run --help` and a failed auth run have been saved from the Debian host. Stdin, config directory, JSON fields, and the model id stay unknown until then. If the failure text does not match `OUTAGE`, the draft changes the classifier. It does not hope.
* `README.md` is updated in plain language. `EGM.md` is not the operator guide.
* Go, Matrix, and a Grok Bot do not start in this cut.

Not settled, and the draft must settle them before code, using the host output rather than this brief:

* The OpenCode command line, including where the prompt goes and how a deny rule is appended last.
* The idempotency check the code runs before a second write.
* The exact git URL shape, after one `git ls-remote` to the NetBird name.
* Settled by the owner: the fork remote is `arthur/seatree`. The config value is `opencode`, not `xai`. xAI is the API key behind that provider, not a second provider string. On 2026-10-28, `https://github.com/arthur/seatree` returned 404. Update cannot install from that name until the repo exists and has a release. Until then, install from the local tree.

Order of work after this brief:

1. On Debian, save `opencode run --help` and one run that fails because the key is missing or wrong. Do not sketch the parser first.
2. Write the design draft. It copies the rejected list above. It picks the idempotency check and the update refusal. It does not add a product that this brief rejected.
3. Only then implement the backend, the fail-closed provider switch, the doctor line, the setup guard, and the update refusal.
4. Run the exam on a copied template. One task after the first run. The same one task after the second. A fixture secret outside the vault does not appear in the answer. The laptop fetches that vault by the NetBird name and sees one task.
5. Do not point the service at real notes until that exam has passed. Do not start the planner or the reviewer as part of it.
