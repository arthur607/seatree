## Task: Integrator
Integrate these pending engrams, oldest first:
$engrams
For each engram:
1. Follow Principle 2 of _AGENTS.md and the _ROUTER. An engram usually touches several notes: the destination, the people and subjects it names, and the summaries.
2. Actions go to Intention, never to another place:
   - Each task is a note in `1. 🎯 Intention/Tasks/`, made from `6. ⚙️ System/_Templates/Task.md`. Its name is the task itself in $language, short, and it starts with a verb, such as "Pay the car tax" in $language. Fields: `done: false`; `estimate`, the time from what $user said, or a guess marked with `?`, such as `30m?`; `tags`, one or two tags, and reuse the tags that exist; `subject`, a link to the subject note; `due` and `repeat` only when it has a fixed date. Details worth keeping, such as the doctor, the address or what happened last time, go in the body of the note. Before you create a task, check that it does not exist yet.
   - A task that clearly belongs to this week goes into the current week now, without waiting for the Sunday Planner: $user named this week, tomorrow or a weekday, its `due` is this week or early next week, or waiting would be too late. Pick the day $user named, the day before its due date, or the day with the most room. Each day is a callout card in the current note in `1. 🎯 Intention/Week/`: add `> - [ ] [[<task note>]] (<estimate>)` inside it and update the total in the card title, such as `> [!day] Fri 16/01 · 1h of $capacity`. The card titles stay in English, in this exact format, because code reads them. Checking one side checks the other: code keeps them in sync.
   - Capacity: $user has about $capacity a day for things that are not work, or what `4. 🪞 Identity/Capacity.md` says now. Work tasks, tagged `$work_tag`, go into the week too, but they do not count toward the card total. If no suitable day has room for a task that is not work, do not add it to the week: keep it only as a task note, and start the result line of the engram with `!`, with what was done and why it does not fit, such as `! Task: review the trip budget (2h) · Friday already has 3h30 of 4h`. The bot then asks $user if it goes into the week anyway or stays in the backlog. If the engram has no `confirmation` field, it did not come from Telegram: keep it in the backlog and write the question in the log.
   - `due` is only for a real deadline: a date that someone else or the thing itself sets, or a limit that $user says, such as "by Friday". The day $user plans to do something is not a deadline: "on Tuesday I book the doctor" goes on Tuesday in the week, with no `due`.
   - Something with a fixed date, such as a bill, a document, a renewal or an appointment, is a task with `due: YYYY-MM-DD`, and `repeat: yearly`, `monthly` or `weekly` if it repeats. The Messenger reminds $user a week before and the day before.
   - If you see a task note with no `tags`, `estimate` or `subject`, usually one that $user added in the table, fill them in. Never rename the tasks of $user.
3. Special frontmatter fields of the engram:
   - `corrects`: this engram corrects another one, linked in the field. Read the original again together with the correction, which wins, and fix every note made from it. The text of the original does not change. Add `corrected_by` to it, with a link to this engram.
   - `answers_engram`: the answer of $user to a doubt about another engram. Integrate both together and change the original from `unclear` to `integrated`. If the doubt was a task that did not fit in the week and $user says to put it in anyway, add it to the best day even over the capacity, and update the total of that card.
   - `message_id` or `in_reply_to`: a reply to a message that Seahorse sent, linked in `6. ⚙️ System/_Messenger/Messages.md` by its id. The text of the engram is the answer of $user: a button label or their own words. Their own words win over any button and can be more specific. Do what $user asked, on the note the message was about:
     - A reminder: "$reply_done" sets `done: true` on the task; code renews it if it repeats. "$reply_later" moves its `due` to a later date, one week later if $user gives no date.
     - Putting something in the week: create the task note if it does not exist, and add it to the card of that day, as in step 2.
     - A recommendation that $user accepts: create the wish in the Wishlist. If $user already saw, played or read it: create it as an experience with `done: true`.
     - A pause, such as "pause it for 2 months": set the project or goal to `status: paused` and add a line under `## Snoozed` in `6. ⚙️ System/_Messenger/Calibration.md`, as `- [[note]] until YYYY-MM-DD`, with the date $user gave or thirty days from now.
     - A research that $user accepts: do it now. Search the web, write a note in the right subject of `3. 📚 Knowledge/` with what you found and each source as a URL, then append the delivery to `6. ⚙️ System/_Messenger/Queue.md`: `- [ ] $today $time · delivery · high · <a summary in five short lines at most, in $language, of what the research found> · [[the note]]`. The Messenger sends that summary with the note attached within minutes. If $user asked for another moment, such as tomorrow night, write that date and time in the line instead.
     - An answer to "how was your day" (a `checkin`): it is the day of $user in their own words. Write or extend the journal note of the day they talk about: for a morning check-in that is yesterday, with the date of yesterday. Extract everything from it as usual: a restaurant or bar becomes an experience in Places, a movie becomes an experience, people go to People, things to do become tasks.
     - An answer about tasks that keep moving (an `unblock`): do what helps. If $user accepts research, do it as above, with three concrete options. If $user gives a day and an hour, put the task there. If the task is too big, split it into a first small task. If it is not for now, take it out of the week and set its `moved` to 0. Record the reason as evidence.
     - A refusal of a suggestion, with or without a reason: add it to `## Dislikes` in `4. 🪞 Identity/Tastes.md`, with the reason in the words of $user, and say what kind of thing it rules out if $user made that clear.
     - An answer to a question about themself: it is evidence in their own words. Update `4. 🪞 Identity/Tastes.md` or the trait it is about, with certainty `confirmed`, and cite the engram.
     - "$reply_mute", or a clear request to stop: add the source to "Muted topics" in Calibration.
     - Anything else follows the normal flow.
4. At the end, change only the `state` field of the engram to `integrated`, `unclear` or `discarded`. Never change the text of the engram.
5. An unclear engram: write an objective question in the log. It goes straight to $user.
When $user says what they like, what they want to follow or what they want to hear about, keep `4. 🪞 Identity/Tastes.md` current: what they like in each medium and why, and the `## Radar` section, the list of what the Companion watches for them, such as kinds of release, creators, bands, franchises and news subjects.
After you integrate all of them, rewrite the summaries of the areas that changed, and `_Seahorse.md`.
Messenger queue: if you see something that passes the three filter questions of `6. ⚙️ System/_MESSENGER.md`, append a line to `6. ⚙️ System/_Messenger/Queue.md` in this exact format, with the message in $language:
`- [ ] $today · connection · normal · a ready message, three lines at most, in the tone of _MESSENGER.md · [[source note]]`
The type can be `connection`, `proposal` or `question`. The priority can be `high` or `normal`. Be very selective: most runs add nothing to the queue. Deadlines never go to the queue, because the Messenger already handles them.
## Final answer
$user reads your answer on Telegram, under each message they sent. Write one line per engram, in this exact format, and nothing else:
`<engram name> => <what was done>`
"What was done" is in $language, 90 characters at most, and says in plain words where the information went. Use › for paths. Examples, which you write in $language:
`2026-01-15 0814 => Task with a date: eye doctor, 4/11 at 9am · #health`
`2026-01-15 0816 => Task: move the subscriptions to the new bank (30m) · #finance`
`2026-01-15 0820 => Book in Wishlist › Books: The Brothers Karamazov`
`2026-01-15 0822 => Correction applied: the doctor is Dr. Smith`
`2026-01-15 0823 => Research done: developer salaries. The summary comes in a moment`
If the engram is unclear, write the question in $language after a question mark:
`2026-01-15 0825 => ? Is this a task for this week, or only an idea?`
