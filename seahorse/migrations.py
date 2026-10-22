"""Vault changes between versions. Each step runs once, when a vault goes past its version.

A step changes only what Seahorse owns (system files, the structure, frontmatter fields), never the text of the
user's notes. Add steps at the end, oldest first:

    def _to_1_1_0() -> None:
        ...

    MIGRATIONS = [("1.1.0", _to_1_1_0)]
"""
from . import config

MESSENGER_RULES = "_MESSENGER.md"
CHECKIN_LINES = [
    ("| checkin | Companion | every night, once | \"How was Friday? Did you go out?\" The answer becomes the journal and the experiences of the day |",
     "| checkin | Companion | every morning, once, about yesterday | \"How was your Friday? Did you go out?\" The answer becomes the journal and the experiences of that day |"),
    ("- **Every night, the day.** Once, at a random moment between 8:30pm and 10:45pm, the Companion asks how the day was.",
     "- **Every morning, yesterday.** Once, at a random moment from 45 minutes to 2 hours after the morning summary, the Companion asks how yesterday was."),
]


def _to_1_2_0() -> None:
    """The day check-in moved from the night to the next morning: the rules of the Messenger say so."""
    path = config.SYSTEM / MESSENGER_RULES
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    for old, new in CHECKIN_LINES:
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


MIGRATIONS: list = [("1.2.0", _to_1_2_0)]


# 1.3.0, the Messenger: readable deliveries, sources for facts from outside, a daily limit, the reminder calibration,
# and snoozed and muted topics checked by code. Each change: the file in System, the old text (or None for a new
# line), the new text, and the line after which a new line goes. The template has the same changes.
MESSENGER_CHANGES = [
    ("_MESSENGER.md",
     '| reminder | code | a task with a real `due` is a week away, and again the day before | "⏰ Pay the car tax" |',
     '| reminder | code | a task with a real `due` is a week away, and again the day before or on the day | "⏰ Pay the car tax" |',
     "| summary |"),
    ("_MESSENGER.md",
     "| delivery | Integrator | something that Seahorse promised is ready | the summary of a research, with the note attached |",
     "| delivery | Integrator | something that Seahorse promised is ready | the summary of a research, then the note itself as text, and a button to open it in Obsidian |",
     "| proposal |"),
    ("_MESSENGER.md",
     "- **Reminders are separate.** They do not replace a conversation, and at most one goes out per hour.",
     "- **Reminders are separate.** They do not replace a conversation, and at most one goes out per hour. No reminder goes out before the morning summary or in the 4 hours after it, because the summary already lists the deadlines.",
     "- **Hours.**"),
    ("_MESSENGER.md", None,
     "- **A limit a day.** At most 5 messages a day that {{user}} did not ask for: conversations, the check-in and reminders. Deliveries, replies, the morning summary and the week proposal do not count. A reminder of something due today always goes out.",
     "- **Deliveries go out when ready**"),
    ("_MESSENGER.md",
     "- A task that keeps moving is a signal, not a failure. Ask what is in the way and offer concrete help.",
     "- A task that keeps moving is a signal, not a failure. Ask what is in the way and offer concrete help. Ask at most once a week. When {{user}} ignores the question, the next one waits 14 days, and 30 days after two in a row.",
     "- A suggestion matches"),
    ("_MESSENGER.md",
     "- Check facts on the web before they are sent. An invented fact is the worst failure.",
     "- Check facts on the web before they are sent. An invented fact is the worst failure. A `release`, `news` or `curiosity` names the web pages that confirm it. Code drops it without them, and shows the first page as a link under the message.",
     "- Change the kind."),
    ("_MESSENGER.md",
     "A deadline reminder fires at most twice: a week before the `due` date of the task and on the day before.",
     "A deadline reminder fires at most twice: a week before the `due` date of the task, and on the day before or on the day.",
     "## Repetition"),
    ("_MESSENGER.md", None,
     "When {{user}} ignored the last 3 reminders of one kind, a week before or the day before, code stops that kind and writes it under `## Stopped reminders` in Calibration. It starts again when {{user}} acts on a reminder, or deletes that line. A reminder of something due today always goes out.",
     "After that, silence."),
    ("_MESSENGER.md",
     "- A reminder is two lines, written by code: `⏰ <task>` and `Due Thursday, 15/01 · 15m`. A third line with one useful detail from the task note is optional.",
     "- A reminder is two lines, written by code: `⏰ <task>` and `Due Thursday, 01/15 · 15m`, with the day and the month in the order of {{language}}. A third line with one useful detail from the task note is optional.",
     "- The morning summary opens"),
    ("_MESSENGER.md", None,
     "- A delivery is easy to read on the phone: the summary, the note itself as text, and where the note is saved, such as `📁 Knowledge › Misc › Card prices`. Its buttons are `📖 Open in Obsidian`, `👍 Thanks` and `👎 Not useful`. There is no file to download.",
     "- Buttons are the exception."),
    ("_MESSENGER.md",
     "A `delivery` goes out when its moment comes, with the linked note attached.",
     "A `delivery` goes out when its moment comes, with the linked note as text to read in the chat.",
     "## Queue"),
    ("_MESSENGER.md",
     "ignored ({{user}} tapped Ignore, or a day passed), muted.",
     "ignored ({{user}} tapped Ignore or Not useful, or a day passed), muted.",
     None),
    ("_MESSENGER.md", None,
     'Code checks both before each reminder, queue item and conversation. A topic matches a whole note link or whole words: muting "car" does not silence "Cartão".',
     "- **Snoozed.**"),
    ("_MESSENGER.md",
     "The current state lives in `6. ⚙️ System/_Messenger/Calibration.md`, kept by the Integrator and the Reviewer.",
     "The current state lives in `6. ⚙️ System/_Messenger/Calibration.md`, kept by the Integrator and the Reviewer. Code keeps its `## Stopped reminders` section.",
     None),
    ("_Messenger/Calibration.md", None,
     "## Stopped reminders\nCode writes this part. A kind of reminder stops when the last 3 of that kind were ignored, and starts again when {{user}} acts on a reminder. To start it again now, delete its line.\nNone.",
     None),
    ("_Messenger/Messages.md", None,
     "The text of a reminder starts with its kind, which code reads: `week-before`, `day-before` or `due-today`.",
     "Format: `- 2026-01-15 10:30 · reminder"),
    ("_Messenger/Queue.md",
     "A delivery links the note it delivers, which goes attached.",
     "A delivery links the note it delivers, which goes as text to read in the chat.",
     None),
]
# Logs that grow at the end: a new line goes only after its anchor, never at the end.
MESSENGER_LOGS = {"_Messenger/Messages.md", "_Messenger/Queue.md"}


def messenger_change(text: str, old: str | None, new: str, after: str | None, at_end: bool = True) -> str:
    """One change of MESSENGER_CHANGES on a text: the old text becomes the new one. When the old text is not there,
    the new line goes after the line that holds `after`, or at the end; with no `after`, a changed part of a line
    stays as the user wrote it. A text that has the new line already stays as it is."""
    if new.split("\n", 1)[0] in text:
        return text
    if old and old in text:
        return text.replace(old, new, 1)
    if old and not after:
        return text
    if after and after in text:
        end = text.find("\n", text.index(after))
        end = len(text) if end == -1 else end
        return text[:end] + "\n" + new + text[end:]
    return text.rstrip("\n") + "\n" + new + "\n" if at_end else text


def _to_1_3_0_messenger() -> None:
    """The rules of the Messenger in the vault say what 1.3.0 changed. Only Seahorse's own files change."""
    from datetime import date

    from . import vault
    for name, old, new, after in MESSENGER_CHANGES:
        path = config.SYSTEM / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        fill = (lambda part: vault.fill(part, date.today()) if part else part)
        changed = messenger_change(text, fill(old), fill(new), after, at_end=name not in MESSENGER_LOGS)
        if changed != text:
            path.write_bytes(changed.encode("utf-8"))


MIGRATIONS.append(("1.3.0", _to_1_3_0_messenger))
