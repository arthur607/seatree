# Changelog

Each version is also a GitHub release. The bot sends these notes when a new version is out.

## 1.2.0

- The day check-in moved to the morning: from 45 minutes to 2 hours after the morning summary, Seahorse asks how yesterday was, by name ("How was your Saturday?"). The answer goes to the journal of yesterday. No other conversation comes within an hour of it.
- The rules of the Messenger in your vault say so too: the update changes them by itself.

## 1.1.2

- Fix: when Claude Code stopped on an error in the middle of a run, Seahorse took it as "nothing to do", and the capture waited with no word. Now it counts as a failure, and the bot tells you once.
- Captures that wait for more than 10 minutes get another run by themselves, every 15 minutes.
- Seahorse uses the native Claude Code of its own user. The setup installs it, and never uses an old global install from npm, which can break on an old Node.js.

## 1.1.1

- Update from Telegram: the notice of a new version has an "Update now" button. `/update` also works.
- Fix: an update from the bot installed the new version but never said so, because the restart stopped it. The update now runs as its own short systemd unit, and the bot tells you when it is done.

## 1.1.0

- The first conversation: right after the setup, the bot asks 6 short questions to get to know you, one at a time. Each answer goes to the right place, like any capture. Skip any question, or send `/intro` to answer them again.
- The setup tells you how to give your bot the Seahorse picture.

## 1.0.1

- `seahorse version` shows the version on this computer, on the server, and the newest release.
- The README starts with what Seahorse is for: a living map of your mind. It lists the commands.

## 1.0.0

The first public version.

- Capture by text or voice on Telegram, and by writing notes in Obsidian.
- The Integrator files each capture into Intention, Memory, Knowledge and Identity, and links it to what you already know.
- Tasks as notes, in a table, with a week of day cards. Unfinished tasks move to the next free day by themselves.
- A morning summary, and the week or today as an image: send `week` or `today`.
- The Companion starts conversations at random moments, and asks about your day every night.
- Deadline reminders, a Sunday planner, a weekly Observer of your patterns, and a night Reviewer.
- Claude (subscription or API key) or Codex (subscription or API key).
- English, Brazilian Portuguese and Spanish.
- One-command install on Windows, macOS and Linux. The setup runs on your computer and sets up your server through ssh:
  the login pages open in your browser, and the one-time codes are copied for you.
- Updates with `seahorse update` or `/update`.
