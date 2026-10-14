## Task: morning summary
Write the message that $user gets on Telegram in the morning. Do not change any file.
Read, if they exist: the log of yesterday `$log_yesterday`, the log of today `$log_today`, the week `$week`, the task notes it links in `1. 🎯 Intention/Tasks/`, and the unclear engrams below.
Tasks with a due date in the next seven days, or late:
$deadlines
Unclear engrams:
$unclear
Tasks that code just moved because they were not done on their day:
$moved
The message is a list, never prose. In $language, plain text, no markdown. Use this shape, with every word translated into $language:
```
☀️ Tuesday, 13/01 · 1h30 of 4h

📌 TODAY
• Book the endocrinologist (30m) ⚠️ due today
• Take the monitor to the post office (15m)

↪️ MOVED
• Book the nutritionist: tue → today
• Sort the photos: left the week, no room

📅 NEXT DEADLINES
• Pay the car tax · Thursday, 15/01

✅ INTEGRATED YESTERDAY
• New task: take the monitor to the post office
• Dan = your brother Daniel

❓ I NEED YOU
• "Strengths test": is it a test to take, something to study, or a result of yours?
```
Rules:
- First line: ☀️, the weekday, the date, and the hours planned today for things that are not work, against the capacity, from the card of today in the week. With no week, write that no week is planned in place of the hours.
- `TODAY`: the open tasks in the card of today, one per line, with the estimate. Add the "due today" marker only when the `due` of the task is today, and the "late" marker when its `due` has passed. Late tasks that are not in the card of today also go here. With nothing planned, one line that says so.
- `MOVED`: the moved tasks listed above, one per line, as `• <task>: <old day> → <new day>`, with "today" when the new day is today. A task that left the week says it left the week, with no room. A task that was not done on its day is never called late: the "late" marker is only for a task whose real `due` date has passed.
- `NEXT DEADLINES`: tasks with a due date in the next seven days that are not already under TODAY, as `• <task> · <weekday>, <dd/mm>`.
- `INTEGRATED YESTERDAY`: what Seahorse changed since the summary of yesterday, from the logs. Four lines at most, the ones $user would care about. Facts, not process: never mention notes, links or files.
- `I NEED YOU`: two questions at most, from the unclear engrams, each objective and easy to answer in a few words.
- Leave out each section that is empty, with its heading. One blank line between sections.
- One item per line, starting with `• `. No greeting, no transition and no closing sentences. No exclamation marks. Each line as short as it can be and still clear.
Your final answer is only the message, nothing else.
