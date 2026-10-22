# Vocabulary
Allowed values for frontmatter fields. The validator of Seahorse, `python -m seahorse.lint`, reads the block below and rejects any value outside it.
To add a value, edit the block. Agents propose in the log, and {{user}} or the Reviewer decide.
```vocab
type: engram, journal, experience, trip, knowledge, goal, project, wish, entity, trait, week, summary, log, profile, record, dashboard, task
state: pending, integrated, unclear, discarded
channel: telegram-voice, telegram-text, claude, obsidian, other, telegram-photo, telegram-file, telegram-video, telegram-location, telegram-contact
entity: person, work, concept, place, company, tool
wish: game, movie, series, book, place, purchase
certainty: hypothesis, supported, confirmed
pace: slow, medium, fast
done: true, false
repeat: weekly, monthly, yearly
status.knowledge: to-study, studying, known, superseded
status.project: active, paused, completed, abandoned, superseded
status.goal: active, achieved, abandoned, superseded
status.trip: dream, planning, booked, done
status.trait: active, superseded
status.task: cancelled
written_by: owner
imported_from: obsidian, notion, other
```
## Engram states
- `pending`: it waits for the Integrator.
- `integrated`: each piece of it is in the notes.
- `unclear`: the Integrator needs an answer of {{user}}, or failed three runs on it. The morning summary asks.
- `discarded`: it has nothing to keep, such as a question that the bot already answered and that gives no fact. The engram stays.
## Types that describe {{user}} directly
- `profile`: a portrait that {{user}} wrote, such as `Profile` or `Routine`. Lives in `4. 🪞 Identity`.
- `record`: a record of the career or life of {{user}}, such as a self-evaluation or an interview preparation. Lives in `2. 📓 Memory/Career/<company>/`.
## Typed relations
Relations between notes live in the frontmatter as lists of links. Only these names:
| Field | Means |
|---|---|
| `part_of` | this note is a piece of that one |
| `causes` | this thing led to that one |
| `inspires` | this thing gave the idea for that one |
| `contradicts` | this note disagrees with that one |
| `supersedes` | this note took the place of that one |
| `related` | a link with no better type. Use it only when necessary |
Example:
```
part_of: ["[[Dev]]"]
inspires: ["[[Side Project]]"]
```
## Common fields
| Field | Where | Format |
|---|---|---|
| `type` | every note outside `_Templates` | a value from the block above |
| `source` | notes created from engrams | list of links to engrams |
| `url` | notes about external material | the link |
| `aliases` | entities | list of alternative names |
| `done` | wishes, experiences and tasks | `true` when {{user}} watched, read, played, visited or bought it. The checkbox in the tables |
| `estimate` | tasks | time it takes, such as `30m` or `2h`. A `?` at the end means the agent guessed |
| `due` | tasks with a fixed date | `2026-01-15` |
| `planned` | tasks | the day {{user}} chose to do it, such as `2026-01-15`. It is not a deadline. Code puts the task on that day |
| `repeat` | tasks that repeat | a value from the block above |
| `last_done` | tasks that repeat | when the last occurrence was done. Set by code |
| `postponed` | tasks | how many times a task with no due date left a week unfinished. Set by code only. At three, the Planner asks about it |
| `moved` | tasks | how many times a task that {{user}} committed to, with a `planned` day or a due date this week, moved to another day because it was not done. Set by code only. At three, the Companion asks what is in the way |
| `subject` | tasks | link to the subject note, such as `"[[Health]]"` |
| `tags` | tasks | one or two tags, such as `[{{work_tag}}]`. Use a tag again before you create a new one: `{{work_tag}}` for work, and the tags already in use |
| `trigger` | wishes | short text, for example "sale below 30" |
| `why` | goals and projects | short text |
| `updated` | summaries | date of the last rewrite |
| `superseded_by` | superseded notes | link to the new note |
| `received` | engrams | `2026-01-15 21:36`, when it was sent |
| `corrects` | an engram that corrects another | link to the corrected engram |
| `corrected_by` | an engram that was corrected | link to the engram that corrects it |
| `answers_engram` | an engram that answers a doubt | link to the unclear engram |
| `in_reply_to` | an engram that replies to a bot message | the text of the bot message |
| `message_id` | an engram that replies to the Messenger | id of the Messenger message |
| `via_button` | an engram created by a tap on a button | `yes`. The text of the engram is the label of the button |
| `confirmation` | Telegram engrams | id of the confirmation message, used by the bot. A question or a capture from a computer has none |
| `telegram_id`, `fix_prompt` | Telegram engrams | used by the bot. Agents ignore them and never change them |
| `asked` | engrams | `question` when the bot answered the message as a question |
| `attempts` | engrams | how many Integrator runs failed on it. Set by code. At three, the engram becomes `unclear` |
| `asked_in_summary` | engrams | how many morning summaries asked about this unclear engram. Set by code. After two, it becomes `discarded` and its text stays |
| `status` | tasks | `cancelled` when {{user}} cancels the task. It also gets `done: true` and no `repeat` |
| `written_by` | notes that {{user}} made | `owner`. Set by the Integrator when it reads the note. A note that an agent made never gets it, also after {{user}} edits it |
| `imported_from` | notes brought from the old systems of {{user}} | where it came from |
| `original_date` | imported notes | when {{user}} wrote it. Its facts were true then, not necessarily now |
| `relationship` | people in Memory/People | how the person relates to {{user}}, in the words of {{user}} |
| `birthday` | people in Memory/People | `YYYY-MM-DD`, or `--MM-DD` when the year is unknown. Code sends a message a week before and on the day |
| `keep_in_touch` | people in Memory/People | how often {{user}} wants to talk to the person: `weekly`, `monthly`, `quarterly`, or a number of days such as `30d`. Only when {{user}} says so |
| `last_contact` | people in Memory/People | `YYYY-MM-DD`, the last day {{user}} talked to or met the person, from what {{user}} says. Code also writes it when a call task is done |
| `genre`, `platform`, `author`, `imdb`, `released`, `category`, `city`, `kind` | wishes, experiences, knowledge | free text about the item |
