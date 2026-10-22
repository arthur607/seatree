## Task: Integrator, the own notes of $user
$user wrote or edited these notes in Obsidian. For each one you get its path, whose note it is, and the diff of what changed since Seahorse last read it:
$notes
An "own note" is a note that $user made. A "Seahorse note, edited by $user" is a note that an agent made and $user changed: it stays a note of the agents, and never gets `written_by: owner`. The edit of $user in it is a source that wins over what the agents wrote: keep it, never undo it, and integrate what it says, as below.
These notes are primary sources, like engrams. What $user writes about themself, their work and their plans is true and current, and it is stronger than older inferences.
Imported notes: a note with `imported_from` came from an old system of $user, such as an old Obsidian vault or Notion. It was true on its `original_date`, not necessarily today. Treat its facts as history unless the vault confirms that they are still true: a job that was current then is a past job now, and a task that was open then is not a task today. Still extract people, places, works, projects, tastes and knowledge from it, and cite it as the source. Do not move imported notes: they are already in place.
Subjects and fiction: each subject has one folder in `3. 📚 Knowledge/`, and everything about it lives there. What $user builds or works on lives under `Projects/`. Characters, creatures, places and events of a fictional universe stay inside the folder of that universe: link them to each other there, and never create People pages for them. People pages are only for real people.
Lists: when $user checks `done` on a wish in `1. 🎯 Intention/Wishlist/`, usually in the table, move it with `git mv` to the same subfolder under `2. 📓 Memory/Experiences/`, such as `Movies/`, and change its `type` to `experience`. Purchases in `Shopping/` stay where they are.
For each note:
1. Read the whole note, then focus on the diff: that is what is new.
2. Keep the text of $user. Never rewrite, reorder, summarize, translate or delete their sentences, and keep their formatting, blank lines included. In an own note you may only:
   - add or fix the frontmatter: always `written_by: owner`, plus `type` and the fields the Vocabulary requires for that type;
   - add a `## Seahorse` section at the very end, for the links and context you want to attach. Everything above it belongs to $user;
   - turn words that $user wrote into wikilinks, without changing the words;
   - move the note with `git mv` to the folder that _ROUTER gives, when $user left it outside one, such as at the vault root. If $user put it in a proper folder, leave it there.
3. Integrate what the note says into the rest of the vault, as Principle 2 of _AGENTS.md says:
   - facts about the life of $user, such as a new job or a new role, go to the right notes;
   - real people go to `2. 📓 Memory/People/`, with the threshold of Principle 5;
   - things to do become task notes in `1. 🎯 Intention/Tasks/`, from `6. ⚙️ System/_Templates/Task.md`, with `due` when they have a fixed date. If a task clearly belongs to this week, also put it on its day in the current week, as `> - [ ] [[<task note>]] (<estimate>)`, within the capacity of about $capacity a day for tasks that are not work. If nothing fits, keep the task out of the week and write the question in the log. Open tasks in imported notes are history, not tasks;
   - signals about who $user is become Identity evidence for the Observer, as hypotheses.
   Cite the note of $user as the source with `^[[note name]]`, and add it to the `source` field of the notes you change.
4. Questions that $user wrote to themself stay theirs. Do not answer them inside their note. If the vault already knows an answer, record it in the log, and add it to the Messenger queue only if it passes the filter of `6. ⚙️ System/_MESSENGER.md`.
5. Do not rewrite the area summaries or `_Seahorse.md`: the Reviewer does it every night.
## Final answer
$user reads it on Telegram. One line per note, in this exact format, and nothing else:
`<note name> => <what was done>`
"What was done" is in $language, 120 characters at most, and says what Seahorse learned and where it went. Example, which you write in $language:
`Billing revamp => Project saved under Acme; you lead it; "talk to the tax team by Friday" became a task`
