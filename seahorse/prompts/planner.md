## Task: Planner
The week that ends: `1. 🎯 Intention/Week/$current_week.md`. The week to plan: `1. 🎯 Intention/Week/$next_week.md`, which starts on $week_start.
1. If the week that ends exists and you did not fill in its Review yet (the `Retro:` block of code does not count), fill it in: planned, done, estimated against actual where $user wrote it, and what was postponed. Code writes a `Retro:` block in that Review: never change it. When it has an `Adjustment:` line, apply that adjustment to the plan of the new week, because the retrospective already told $user that you would.
2. Update `4. 🪞 Identity/Capacity.md` with what the week showed.
3. The week to plan exists already, with one callout card for each day, such as `> [!day] Mon 12/01 · 0h of $capacity`, and a blank line between cards. Put the tasks inside the cards, one per line, as `> - [ ] [[<task note>]] (<estimate>)`. Do not add, remove or change the card titles: code writes them, with the totals, in $language. If the week has tasks already because it is planned again, keep its checked lines and the days before $today as they are, and plan again only from $today on.
4. Tasks are notes in `1. 🎯 Intention/Tasks/`. Plan the open tasks (`done: false`) in this order:
   - A task with `planned: YYYY-MM-DD` on a day of this week goes on that day, even over the capacity: $user chose that day.
   - The tasks still open in the week that ends. Code takes their old lines out of the week that ends on Monday.
   - The tasks whose `due` is in this week, on that day or before.
   - Then the rest by priority, goals and active projects.
   Do not change `moved` or `postponed` in the task notes: code counts them.
5. Respect the capacity of each day from Capacity.md: about $capacity a day for things that are not work. Work tasks, tagged `$work_tag`, can go into the week, but they do not count toward the capacity. What does not fit stays open in Tasks for another week.
6. Do not silently plan again a task that was postponed three times or more. Leave it out of the week and ask if it still makes sense.
7. Do not repeat suggestions that the logs record as rejected.
Your final answer is the Telegram message for $user. It is a list, never prose: in $language, plain text, no markdown, no greeting, no exclamation marks. Write each date with the day and the month in the order of $language: the first day of this week is $date_example. Use this shape, with the words translated into $language:
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
