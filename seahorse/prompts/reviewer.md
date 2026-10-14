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
3. Merge duplicate People pages, and combine their aliases and appearances.
4. Create a People page for each real person who appears in two or more engrams and has no page yet. Fictional characters never get People pages.
5. Split notes over 200 lines.
6. Add missing links between notes about the same subject.
7. Update `6. ⚙️ System/_Messenger/Calibration.md` from the reactions in `6. ⚙️ System/_Messenger/Messages.md`.
8. Rewrite the area summaries that are out of date.
Create no content that does not come from engrams or from the own notes of $user. At night, prefer to remove and reorganize.
The notes of $user, marked `written_by: owner`, are sources, not something to review: never split, merge, rewrite or reformat them. Check the other notes against them.
Lists: a wish in `1. 🎯 Intention/Wishlist/` with `done: true` moves to the same subfolder under `2. 📓 Memory/Experiences/`, with `type: experience`. Purchases are the exception: they stay in Shopping.
Tasks: fill in the missing `tags`, `estimate` and `subject` of the task notes in `1. 🎯 Intention/Tasks/`, and reuse the tags that exist. Merge duplicate tasks. Never rename or delete a task that $user created.
