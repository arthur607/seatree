## Task: Reviewer
Validator output:
```text
$lint
```
Notes changed in the last 24 hours:
$changed
Do these steps, in this order:
1. Fix the validator errors.
2. Compare each changed note with the engrams it cites in `source` or in `^[[...]]`. If the note claims something the engrams do not say, fix the note. Concrete details from the engrams, such as numbers, dates and names, must stay.
3. Check the other way too: read each engram integrated in the last 24 hours, and check that every fact, memory, story, feeling, opinion and plan in it is in at least one note. File what is missing, as Principle 2 of _AGENTS.md says.
4. Merge duplicate People pages, and combine their aliases, stories and appearances.
5. Create a People page for each real person who has no page yet, as Principle 5 of _AGENTS.md says: a person with a relationship to $user, a story, a date such as a birthday, or a mention in an answer to Seahorse gets one at once, and any other real person when they appear in two or more engrams or notes. Fictional characters never get People pages.
6. Split notes over 200 lines.
7. Add missing links between notes about the same subject.
8. Update `6. ⚙️ System/_Messenger/Calibration.md` from the reactions in `6. ⚙️ System/_Messenger/Messages.md`. Leave its `## Stopped reminders` section as code wrote it: code owns that section.
9. Rewrite the area summaries and `_Seahorse.md` where they are out of date. Only the Reviewer rewrites them.
Create no content that does not come from engrams or from the own notes of $user. At night, prefer to remove and reorganize.
The notes of $user, marked `written_by: owner`, are sources, not something to review: never split, merge, rewrite or reformat them. Check the other notes against them.
Lists: a wish in `1. 🎯 Intention/Wishlist/` with `done: true` moves to the same subfolder under `2. 📓 Memory/Experiences/`, with `type: experience`. Purchases are the exception: they stay in Shopping.
Tasks: fill in the missing `tags`, `estimate` and `subject` of the task notes in `1. 🎯 Intention/Tasks/`, and reuse the tags that exist. Merge duplicate tasks. Never rename or delete a task that $user created.
