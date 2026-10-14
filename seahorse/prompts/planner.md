## Task: Sunday Planner
The week that ends: `1. 🎯 Intention/Week/$current_week.md`. The new week: `1. 🎯 Intention/Week/$next_week.md`.
1. If the week that ends exists, fill in its Review: planned, done, estimated against actual where $user wrote it, and what was postponed.
2. Update `4. 🪞 Identity/Capacity.md` with what the week showed.
3. Create the new week from `6. ⚙️ System/_Templates/Week.md`, with the week and the date of each day. Each day is a callout card, `> [!day] Mon 12/01 · 2h30 of $capacity`, with its tasks inside as `> - [ ] [[<task note>]] (<estimate>)`, and a blank line between cards. The card titles stay in English, in this exact format, because code reads them. Tasks are notes in `1. 🎯 Intention/Tasks/`. Plan the open tasks (`done: false`): first the ones not finished in the week that ends, then the ones whose `due` is in the new week (on that day or before), then the rest by priority, goals and active projects. A task carried over from the week that ends gets `postponed: N` in its note, where N counts how many times it moved.
4. Respect the capacity of each day from Capacity.md: about $capacity a day for things that are not work. Work tasks, tagged `$work_tag`, can go into the week, but they do not count toward the capacity. The title of each card shows the total without work. What does not fit stays open in Tasks for another week.
5. Do not silently plan again a task that was postponed three times or more. Leave it out of the week and ask if it still makes sense.
6. Do not repeat suggestions that the logs record as rejected.
Your final answer is the Telegram message for $user. It is a list, never prose: in $language, plain text, no markdown, no greeting, no exclamation marks. Use this shape, with the words translated into $language:
```
🗓️ Week 12/01 to 18/01 · 14h of 28h

MON · 2h30
• Study pointers (2h)
• Pay the car tax (30m) ⚠️ due Thursday

TUE · 1h
• Book the dentist (1h)

📥 LEFT OUT
• Sort the photos (3h) · no room

❓ I NEED YOU
• "Learn Rust" was postponed 3 times: keep it or drop it?
```
One block for each day that has tasks, with the total of the day without work. Skip empty days. One task per line with its estimate, and the due marker when its `due` is in that week. `LEFT OUT` lists what did not fit, with the reason in two or three words. `I NEED YOU` has two questions at most. Leave out each section that is empty.
