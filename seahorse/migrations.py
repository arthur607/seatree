"""Vault changes between versions. Each step runs once, when a vault goes past its version.

A step changes only what Seahorse owns (system files, the structure, frontmatter fields), never the text of the
user's notes. Add steps at the end, oldest first. One version can have more than one step:

    def _to_1_3_0_week() -> None:
        ...

    MIGRATIONS = [..., ("1.3.0", _to_1_3_0_week)]

How they run (updates.migrate_vault):
- A step runs when the vault's version (6. ⚙️ System/.seahorse-version) is older than the step's version, and the
  step's version is not newer than the code. A new vault gets the version of the code that made it, so no step
  runs on it.
- The steps of one version run in the order of this list. The vault's version moves on only after all of them
  worked: a step that fails runs again at the next update, so each step must be safe to run twice.
- The template files that are new in a version are added before the steps. A step does not need to add them.
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
