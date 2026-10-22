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


TASK_TEMPLATE = "_Templates/Task.md"


def _to_1_3_0_week() -> None:
    """Tasks have a `planned` day, the day the user chose to do it: the Task template gets the field, after
    `due:` when the template still has it, else at the end of its frontmatter."""
    path = config.SYSTEM / TASK_TEMPLATE
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if "\nplanned:" in text:
        return
    if "\ndue:\n" in text:
        text = text.replace("\ndue:\n", "\ndue:\nplanned:\n", 1)
    elif text.startswith("---\n") and text.find("\n---", 4) != -1:
        end = text.find("\n---", 4)
        text = text[:end] + "\nplanned:" + text[end:]
    else:
        return
    path.write_bytes(text.encode("utf-8"))


MIGRATIONS: list = [("1.2.0", _to_1_2_0), ("1.3.0", _to_1_3_0_week)]
