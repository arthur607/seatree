# Changelog

Each version is also a GitHub release. The bot sends these notes when a new version is out.

## 1.3.3

- In the image of your week, each task, experience and note is an item of its own: a blue square starts it, and a thin line separates it from the next. A long name that needs two lines stays inside its item.

## 1.3.2

- `/retro` shows this week, as it is today. Send `retro last week` (or `/retro last week`) to see the week before.
- The image of your week shows every task, experience and note while it fits one phone screen. Only a very full week shows "+N more".
- "Did" lists every task that you finished, not only the 3 longest.
- "Lived" no longer shows the number of journal days.
- "Learned" shows only notes of knowledge: a project or the main note of a subject is not something you learned.

## 1.3.1

- Your week shows only what the week lived. A movie, a book or a trip counts in a week only when it happened in that week, not when Seahorse filed it. A favorite movie from years ago no longer shows as this week.
- Seahorse writes the day that an experience happened ("last night", "on Saturday"). When you give no date, or it happened long ago, the date stays empty. It never writes the date of today for it.
- The image of your week is simpler: what you did, lived and learned, and the pattern of the week. "Lived" shows up to 10 items.

## 1.3.0

New:
- **People.** Seahorse keeps the stories of the people in your life and brings them back: a week before a birthday (with your gift idea), on the day (with a message you can send), before you meet someone, and when someone you want to keep in touch with has been quiet for too long.
- **`/now`.** Send "I have 30 min", `/now`, or tap 🎲 under the morning summary. Seahorse picks one task that fits, says why, gives a first step, and asks later if you finished.
- **Your week.** Every Sunday, before the plan, one image of the week: what you did, lived and learned, who you met, and one pattern with the change it makes next week. Send `retro` to see it again.
- **Photos and files.** Send a photo, a PDF or a file with a caption: Seahorse keeps it in your notes and reads it. Locations and contacts work too.
- **Edit to correct.** Edit a message that you sent, and Seahorse corrects what it filed.
- **Answers from the web.** A question about the world gets an answer from the web, with its source.
- `/help`, a menu of commands, and `/plan` to plan this week again.

Better:
- Nothing that you say is lost. Every story, feeling and detail goes to a note, and the reply shows each place.
- Research arrives as text that you can read, with a button that opens the note in Obsidian.
- The bot answers at once, also while it works. A message sent during a run no longer waits for 15 minutes.
- The first questions wait for your answer, and a question that you ask meanwhile gets an answer.
- The morning summary tells you which tasks moved to another day, and why.
- Fewer messages: at most 5 a day that you did not ask for, no reminder right after the morning summary, and no more day-before reminders when you always ignore them.
- The week image is made for the phone and uses your language.
- The sync between the server and your phone never stops on a conflict.
- Install and setup: work on a Mac with no developer tools, on servers that ask for a new password or accept only a key, and when GitHub limits requests. An update checks that the bot started again, and goes back to the old version when it did not.

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
