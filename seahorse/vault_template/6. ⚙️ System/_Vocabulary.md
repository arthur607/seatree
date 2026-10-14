# Vocabulary
Allowed values for frontmatter fields. The validator `tools/lint.py` reads the block below and rejects any value outside it.
To add a value, edit the block. Agents propose in the log, and {{user}} or the Reviewer decide.
```vocab
type: engram, journal, experience, trip, knowledge, goal, project, wish, entity, trait, week, summary, log, profile, record, dashboard, task
state: pending, integrated, unclear, discarded
channel: telegram-voice, telegram-text, claude, obsidian, other
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
written_by: owner
imported_from: obsidian, notion, other
```
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
| `repeat` | tasks that repeat | a value from the block above |
| `last_done` | tasks that repeat | when the last occurrence was done. Set by code |
| `postponed` | tasks | how many times the Planner moved it to another week |
| `moved` | tasks | how many times it moved to another day because it was not done. Set by code. At three, the Companion asks what is in the way |
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
| `confirmation` | Telegram engrams | id of the confirmation message, used by the bot |
| `written_by` | notes that {{user}} wrote | `owner`. Set by the Integrator when it reads the note |
| `imported_from` | notes brought from the old systems of {{user}} | where it came from |
| `original_date` | imported notes | when {{user}} wrote it. Its facts were true then, not necessarily now |
| `relationship` | people in Memory/People | how the person relates to {{user}}, in the words of {{user}} |
| `genre`, `platform`, `author`, `imdb`, `released`, `category`, `city`, `kind` | wishes, experiences, knowledge | free text about the item |
