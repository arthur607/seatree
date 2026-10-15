<p align="center"><img src="assets/seahorse.png" width="120" alt="Seahorse logo"></p>

<h1 align="center">Seahorse</h1>

<p align="center">An external hippocampus. You talk to it on Telegram. It keeps your life in an Obsidian vault, plans your week, and starts conversations with you.</p>

---

## What Seahorse is

Seahorse is a second brain that works for you, not a second brain that you must maintain.

- **You only capture.** You send a text or a voice note on Telegram, or you write a note in Obsidian. That is all you do.
- **AI agents do the rest.** They file each capture in the right place, link it to what you already know, turn actions into tasks, and plan your week.
- **It talks first.** Each day it asks how your day was, tells you about new releases you would like, asks about a project that went quiet, and reminds you of real deadlines.
- **Everything stays yours.** Your vault is plain Markdown in a private Git repository. You can read and edit every note in Obsidian.

The name comes from the hippocampus: the part of the brain that takes in what you live and turns it into long-term memory. In Greek, *hippocampus* means seahorse.

<p align="center"><img src="assets/week.png" width="720" alt="The week as an image on Telegram"></p>

## The idea: the areas of the mind

Most second-brain systems organize files. Seahorse organizes a person. It splits what you are into four areas, in the order of how memory works: what you will do, what happened, what you know, and who you are.

| Area | What it holds | The question it answers |
|---|---|---|
| **🎯 Intention** | Tasks, the week, goals, and the things you want to watch, read, play, visit or buy | What will I do? |
| **📓 Memory** | Your journal, trips, career, the people in your life, and what you watched, read and visited | What happened? |
| **📚 Knowledge** | One folder for each subject: your projects, your studies, what you build | What do I know? |
| **🪞 Identity** | Your profile, your routine, your patterns, your tastes, your traits | Who am I? |

Two more folders support them:

- **💭 Engrams** keep each capture exactly as you sent it. An engram is the raw trace of a memory. Agents never change its text, so each claim in the vault points to your own words.
- **⚙️ System** holds the rules the agents follow, in plain language. You can read them, and you can change them.

### Why these four areas help

- **A new fact changes old notes.** When you say "I started a new job", Seahorse does not only save the sentence. It updates your career, your Identity, your routine, and the people involved. Integrating is not appending.
- **Actions live in one place.** Any action, from any capture, ends in Intention. Subject notes never hide a to-do list. You always know where to look.
- **Identity is a hypothesis until there is evidence.** Seahorse infers your patterns from what you do, not from what you say you value. A trait starts as a hypothesis, becomes "supported" after three separate signals, and becomes "confirmed" only when you agree.
- **The vault is alive.** Agents rewrite summaries, merge duplicates, move finished wishes to Memory, and learn which messages you like. Nothing is deleted: old understanding moves to a History section.

## How it compares

Seahorse borrows from well-known methods. This table shows what it keeps from each, and what it adds.

| Method | What Seahorse keeps | What Seahorse adds |
|---|---|---|
| **GTD** (Getting Things Done) | Capture everything, process an inbox, plan the week | The processing and the weekly plan are automatic. Unfinished tasks move to the next free day by themselves. Real deadlines get reminders |
| **PARA** (Projects, Areas, Resources, Archives) | Projects separate from reference material | The top level follows the mind, not the type of file. Memory and Identity have their own places, and PARA has no place for "who I am" |
| **Zettelkasten** | Atomic notes and dense links | The agents make the links when you capture something. You do not need to file or link by hand |
| **Building a Second Brain** (CODE) | Capture, organize, distill, express | You only do the first step. The agents organize and distill. Seahorse brings ideas back to you, unasked |
| **"Chat with your notes" tools** | Ask questions about your notes | Seahorse does not wait for questions. It keeps the vault current, and it starts conversations: check-ins, suggestions, deadlines |
| **AI-maintained wikis** (the "LLM wiki" idea) | An AI that keeps a Markdown knowledge base | Each claim cites the engram it came from, your own notes are never rewritten, and a validator checks the vault after each run |

In short: other systems give you a better place to store things. Seahorse gives you a partner that remembers, organizes and comes back to you.

## How it works

```mermaid
flowchart LR
    You["You<br/>text · voice · Obsidian notes"] --> Bot["Telegram bot"]
    Bot --> Engrams["💭 Engrams<br/>raw captures"]
    Engrams --> Integrator["Integrator<br/>files and links each capture"]
    Integrator --> Vault["🎯 Intention · 📓 Memory<br/>📚 Knowledge · 🪞 Identity"]
    Vault --> Agents["Morning summary · Companion<br/>Planner · Observer · Reviewer"]
    Agents --> Bot
```

| Agent | When | What it does |
|---|---|---|
| **Integrator** | Seconds after each message | Puts each capture in the right notes, creates tasks, and tells you in one line what it did |
| **Notes reader** | Every 5 minutes | Reads the notes you write in Obsidian, after 15 quiet minutes, and connects them. It never changes your text |
| **Morning summary** | Every morning (08:00 by default) | Today's tasks, moved tasks, next deadlines, what changed, and what needs you. The week image follows it |
| **Companion** | 1 to 4 random moments a day, plus a check-in at night | Asks about your day, brings new releases and news you would like, revives quiet projects, asks about tasks you keep moving |
| **Messenger** | Every 10 minutes | Sends deadline reminders and what Seahorse promised. Never during your quiet hours |
| **Planner** | Sunday | Builds the next week from your tasks and your real daily capacity |
| **Observer** | Sunday | Finds patterns and updates Identity |
| **Reviewer** | Every night | Fixes errors, merges duplicates, and checks each note against its sources |

## What you need

| You need | Why | Cost |
|---|---|---|
| **Telegram** | It is how you talk to Seahorse | Free |
| **Obsidian** | It is how you read and edit your vault | Free |
| **An AI**: Claude or ChatGPT | The agents run on it | A Claude Pro/Max or ChatGPT Plus/Pro subscription that you may already have, or an API key that you pay per use |
| **A machine that is always on** | The bot must answer and the agents must run at night | A small server (VPS) costs about US$5 a month. Your own computer works too, but only while it is on |
| **GitHub** (optional) | To sync your vault to your phone and laptop | Free |
| **Groq** (optional) | Fast, free transcription of voice notes | Free |

## Before you install

You install Seahorse from your own computer: Windows, Mac or Linux. The setup runs there, also when Seahorse will run on a server. Then the setup connects to the server and sets it up for you. You never type commands on the server.

### 1. Choose where Seahorse runs

Seahorse must stay on: it answers your messages and its agents work at night.

| | A server (VPS) | Your own computer |
|---|---|---|
| **On day and night** | Yes | Only while the computer is on and awake |
| **Cost** | About US$5 a month | Free |
| **Works from** | Windows, Mac and Linux | Mac and Linux. On Windows, only inside WSL |
| **Best for** | Daily use | A first test |

You can start on your computer and move to a server later.

<details>
<summary><b>Rent a server (VPS)</b></summary>

Any provider with Ubuntu works, such as Hetzner, DigitalOcean or Oracle Cloud. These steps use Hetzner.

1. Create an account at <https://www.hetzner.com/cloud>, then open the Cloud Console at <https://console.hetzner.cloud>.
2. Open your project (or create one) and click **Add Server**.
3. Choose these options:
   - **Location**: the one nearest to you.
   - **Image**: **Ubuntu** (the newest version, such as 24.04).
   - **Type**: the smallest and cheapest type. Seahorse needs little power.
   - **SSH key**: you can skip it. Then Hetzner sends the password of the server to your email.
4. Click **Create & Buy now**. After a few seconds, the server shows its **IPv4 address**, such as `203.0.113.10`.

Keep the address and the password. The setup asks for them once.

</details>

<details>
<summary><b>Use your own Windows computer (WSL)</b></summary>

Seahorse runs in Linux. To run it on a Windows computer, use WSL (Windows Subsystem for Linux), which is free and comes from Microsoft. To run Seahorse on a server, you do not need WSL.

1. Open the Start menu, type `PowerShell`, right-click it, and choose **Run as administrator**.
2. Run this command, then restart the computer:

   ```bash
   wsl --install
   ```

3. After the restart, open **Ubuntu** from the Start menu. The first time, it asks for a new user name and password for Linux. Choose any.
4. In this Ubuntu window, use the install command for Mac and Linux.

Seahorse stops while Windows sleeps.

</details>

### 2. Have these ready

- **Your phone, with Telegram installed and logged in.** During the setup you create your private bot with @BotFather, the official Telegram bot that makes bots. The setup shows each step.
- **A Claude account (Pro or Max plan) or a ChatGPT account (Plus or Pro plan).** An API key also works.
- **For a server: its address (IP) and its password.**
- **Optional: a GitHub account** (free, at <https://github.com/signup>), to open your notes on your phone and on other computers.

## Install

Open a terminal on your own computer and run the command for your system.

**Windows**: open the Start menu, type `PowerShell`, open it, and run:

```powershell
irm https://raw.githubusercontent.com/lorenzoa7/seahorse/main/install.ps1 | iex
```

**Mac**: press `Cmd + Space`, type `Terminal`, open it, and run (on Linux, open the Terminal app):

```bash
curl -fsSL https://raw.githubusercontent.com/lorenzoa7/seahorse/main/install.sh | bash
```

The installer installs `uv` and Seahorse, then starts a setup that asks one thing at a time. It takes about 15 minutes. First it asks your language: English, Português (Brasil) or Español. After that, it speaks your language.

To answer, use the arrow keys and Enter, or type and press Enter. During the setup:

- **The pages open by themselves** in your browser: the logins and the page of the Groq key.
- **The one-time codes are copied for you.** You paste them in the browser.
- **The tokens of the logins are caught by the setup.** You do not copy them.

| Step | What it asks | What you do |
|---|---|---|
| 1. **Where** | A server or this computer | Choose one. For a server, type its address. Then type its password once, when ssh asks for it (the password does not show while you type). The setup installs Seahorse on the server |
| 2. **About you** | Your first name and your time zone | Type your name. The setup suggests your time zone from the clock of your computer. Press Enter to accept it, or choose another city |
| 3. **The AI** | Claude or ChatGPT, then subscription or API key | Choose them. Your browser opens the login. Log in and approve. If the page shows a code, paste it in the terminal. The setup tests the AI |
| 4. **Telegram** | A token for your bot | On your phone, talk to @BotFather: send `/newbot`, choose a name and a user name, and paste the token that BotFather sends. Then tap the link to your new bot in the same message, and tap **Start**. The setup finds you by itself |
| 5. **Voice notes** (optional) | If you want to send voice notes | Choose *Yes* (recommended): the Groq page opens, and you make a free key. Or choose *No* to use only text |
| 6. **Your routine** | Your free hours a day, the time of the morning summary, and if you want quiet hours | Press Enter to keep each suggestion, or type a new value. Quiet hours are optional: in them, Seahorse sends no messages |
| 7. **The vault** | Where you will open your notes | Choose the GitHub sync (recommended) to open your notes on your phone and other computers, or *Only on this machine*. For GitHub, choose *Automatically*: GitHub opens, you paste the code, and the setup creates a private repository for you |
| 8. **Background** | Nothing | The setup starts Seahorse, which also starts again after a reboot. Seahorse sends you a first message on Telegram |
| 9. **Obsidian** | If you want your notes on this computer | Choose *Yes* (recommended). The setup copies the vault from GitHub, adds the Git plugin, and opens the vault in Obsidian. If Obsidian is open, close it when the setup asks |

<details>
<summary><b>Create the Telegram bot (step 4), with more detail</b></summary>

Your bot is a private Telegram account that Seahorse controls. Only you can talk to it: Seahorse ignores all other people.

1. Open Telegram and search for `@BotFather`. Choose the one with the blue check mark. Or open <https://t.me/BotFather>.
2. Tap **Start**. Then send `/newbot`.
3. BotFather asks for a name. This is the name you see in your chats. Send any name, such as `My Seahorse`.
4. BotFather asks for a user name. It must end in `bot`, and nobody else can have it. Send one such as `ada_seahorse_bot`. If BotFather says it is taken, try another.
5. BotFather sends a message with a token, a line such as `7123456789:AAH4k-xyz…`. Copy it, and paste it in the setup. If you did this on your phone, send the token to yourself in Telegram's *Saved Messages*, and copy it in Telegram on the computer (or at <https://web.telegram.org>).
6. In the same message, BotFather sends a link such as `t.me/ada_seahorse_bot`. Tap it, then tap **Start**.

The token is the password of your bot. Do not share it. If somebody sees it, send `/revoke` to @BotFather and run `seahorse setup` again with the new token.

To give your bot a picture, send `/setuserpic` to @BotFather.

</details>

If a step fails, the setup tells you what to do. Your answers are saved after each step: run `seahorse setup` again, and it asks to keep what already works.

**Two people, one server.** Each person gets their own Linux user on the server, such as `seahorse-ana`, with their own notes, tokens, bot and service. To add a person, run `seahorse setup` again (from any computer), type the same server, and choose *A new person's*. Each person needs their own Telegram bot and AI account.

**After the install**, the `seahorse` commands on your computer act on the machine that runs Seahorse, also when it is a server: `seahorse doctor` checks each part, `seahorse logs` shows what Seahorse does, `seahorse update` installs a new version, and `seahorse setup` changes a setting.

<details>
<summary><b>Run the setup on the server itself</b></summary>

If you prefer, connect to the server with `ssh root@YOUR-IP` and run the command for Mac and Linux there. The setup then sees that it runs on a server. It cannot open your browser, so it shows each link for you to copy and open on your computer.

</details>

## Open your vault in Obsidian

Obsidian is a free app for notes: <https://obsidian.md>. The vault comes with its Obsidian settings: the tables (Bases), the style of the week, and the settings of the sync plugin. You only install the sync plugin on the devices that need it.

### On the computer that runs Seahorse

In Obsidian, choose *Open folder as vault* and pick the vault folder (`~/seahorse-vault` by default). You do not need any plugin on this computer: Seahorse saves this folder, and it knows which changes are yours.

### Open your vault on your phone

This needs the GitHub sync from step 7 of the setup.

1. **Make a key for the phone.** On GitHub, open <https://github.com/settings/personal-access-tokens/new>.
   - **Token name**: `Obsidian phone`.
   - **Expiration**: the longest one. When it ends, make a new key.
   - **Repository access**: *Only select repositories*, and choose your vault repository.
   - **Permissions → Repository permissions → Contents**: *Read and write*.
   - Click **Generate token** and copy the token.
2. **Install Obsidian** on your phone and create a new empty vault, such as `Seahorse`.
3. **Install the Git plugin.** In Obsidian, open *Settings → Community plugins*, turn them on, tap *Browse*, search for **Git** (by Vinzent), install it, and enable it.
4. **Log in.** In *Settings → Git*:
   - In *Authentication*, type your GitHub user name, and paste the token as the password.
   - In *Commit author*, type your name and your email.
5. **Get your notes.** Open the command palette (swipe down, or tap the `>` button) and run *Git: Clone an existing remote repo*.
   - URL: `https://github.com/YOUR-USER/seahorse-vault.git` (with your user name and repository name).
   - Folder: leave it empty, so the notes go in the vault root.
   - When it asks if the repository has a `.obsidian` folder, answer *Yes*.
   - When it finishes, close Obsidian and open it again. If Obsidian asks if you trust the plugins of this vault, answer yes.

The sync settings come with the vault: Obsidian saves and gets the changes every minute, and when it opens. Now Seahorse sees the notes you write and the boxes you check, and you see what Seahorse writes.

### On another computer

On the computer where you ran the setup, step 9 does this for you. On other computers:

1. Install Git from <https://git-scm.com>.
2. In a terminal, run this command with your user name and repository name:

   ```bash
   git clone https://github.com/YOUR-USER/seahorse-vault.git
   ```

   On Windows, a browser window opens to log in to GitHub. On a Mac or Linux, type your GitHub user name, and paste a token as the password (make one as in step 1 above).
3. In Obsidian, choose *Open folder as vault* and pick the new `seahorse-vault` folder.
4. Install the **Git** plugin as in step 3 above. The sync settings come with the vault.

### Keep the vault small

Keep big files out of the vault. On a phone, the Git plugin loads the repository into memory, and large images can make Obsidian close.

## Use it

- **Capture anything.** Send text or a voice note: "Pay the car tax by the 15th", "I watched Dune, loved it", "Idea for the app: offline mode". Each message gets a short confirmation that says where it went.
- **Ask anything.** "When is my dentist appointment?" gets an answer from your vault.
- **See the week.** Send only `week` to get the image of the week, or `today` for the image of today. The words follow your language: `semana` and `hoje` in Portuguese, `semana` and `hoy` in Spanish.
- **Answer the Companion.** Reply to its messages with text or voice. Most messages have only an `Ignore` button. One-tap buttons appear only when one tap fully answers, such as "Add to my list".
- **Write in Obsidian.** Write any note, anywhere. Seahorse reads it after 15 quiet minutes, connects it to the rest, and marks it as yours. It never changes your text.

### Bot commands

| Command | What it does |
|---|---|
| `/status` | Shows how many captures are pending, and the version |
| `/integrate` | Integrates the pending captures now |
| `/update` | Installs the newest version, if there is one |

### Terminal commands

| Command | What it does |
|---|---|
| `seahorse setup` | Sets up Seahorse, or changes a setting |
| `seahorse doctor` | Checks each part and says what to fix |
| `seahorse status` | Shows what is running |
| `seahorse update` | Installs the newest release |
| `seahorse logs` | Shows what Seahorse does |
| `seahorse agent <name>` | Runs one agent now: `integrator`, `notes`, `morning`, `reviewer`, `weekly`, `messenger` |
| `seahorse capture` | Saves text from the standard input as a capture |
| `seahorse uninstall` | Removes Seahorse. The vault stays |

On the computer that set up a server, these commands run on the server.

## Updates

When a new version is out, the bot sends you its release notes once. To install it, send `/update` to the bot or run `seahorse update`. The update installs the new code, applies the vault changes of that version, and restarts Seahorse. It never changes the text of your notes.

Each version is a [GitHub release](https://github.com/lorenzoa7/seahorse/releases), with its notes in [CHANGELOG.md](CHANGELOG.md).

## Settings

The setup writes two files in `~/.seahorse`:

- `config.toml`: your name, language, time zone, AI provider, schedule and capacity.
- `secrets.env`: the tokens and keys. Only your user can read it.

```toml
[user]
name = "Ada"
language = "en"            # en, pt-BR or es
timezone = "Europe/Lisbon"

[ai]
provider = "claude"        # claude or codex

[ai.models]                # optional: a model for each tier
# fast = "haiku"
# standard = "sonnet"
# deep = "opus"

[vault]
path = "/home/ada/seahorse-vault"

[telegram]
user_id = 123456789

[schedule]
morning = "08:00"
reviewer = "03:30"
weekly_day = "sun"
weekly_time = "18:00"
quiet_from = "00:00"
quiet_until = "08:00"

[week]
daily_capacity = "4h"
work_tag = "work"
```

The rules the agents follow are in your vault, in `6. ⚙️ System/`. They are plain language. If an agent does something you do not like, change the rule.

## Uninstall

Run this on the computer where you installed Seahorse:

```bash
seahorse uninstall
```

It stops Seahorse, and deletes the program and `~/.seahorse` (the settings, keys and tokens). When Seahorse runs on a server, the command removes it from the server, and this computer stops connecting to the server. Your vault stays, unless you answer *yes* when the command asks to delete it. Some things are outside Seahorse, and they stay: the GitHub repository, the Telegram bot, and tools such as Claude Code and uv. At the end, the command tells you how to remove them.

## Privacy and costs

- Your vault is plain Markdown in your own Git repository. Seahorse has no server and collects no data.
- The AI provider you choose (Anthropic or OpenAI) receives the notes that the agents read, under the terms of your account.
- With a subscription, Seahorse uses the limits of your plan. A normal day uses a few dozen agent runs. The weekly planning uses the deepest model once a week.
- If you choose Groq, Groq receives your voice notes to transcribe them.
- The setup logs in to GitHub on the server only to make the repository, then logs out: the server keeps only a deploy key for the vault. When step 9 copies the vault to your computer, it saves the GitHub login in the credential store of your computer, so Obsidian can sync.
- To suggest your time zone, the setup reads the clock of your computer. On Windows, it asks ipinfo.io once for the time zone of your internet connection.

## Languages

The setup, the bot and the agents support English, Brazilian Portuguese and Spanish. The folder names and the agent rules stay in English for every language. Your notes and the messages you get use your language.

To add a language, copy `seahorse/i18n/en.toml`, translate the values, and open a pull request.

## Contributing

Issues and pull requests are welcome. Run the tests with:

```bash
uv run python -m unittest discover -s tests
```

To try your copy of the code on a server before a release, install it on your computer with `uv tool install --editable .`, and run the setup with `SEAHORSE_SOURCE` set to the folder of the code. The setup copies the code to the server and installs it from there.

## License

MIT
