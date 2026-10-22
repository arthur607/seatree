$user has $minutes minutes free now and wants one task to do in this time. It is $weekday, $time, in the $period. $focus
Seahorse chose these open tasks. All of them fit in the time. The first one has the most points from the rules of Seahorse:
$candidates
What $user wrote about their routine:
$routine
Choose one task from the list. Choose the first one, unless the time of day and the routine clearly make another one better now.
Then write, in $language:
- "why": one short line, 90 characters at most, that tells why this task now. Use only the facts in the list and the routine: the due date, how the task fits the time, the time of day. No guilt, no pressure, no exclamation marks.
- "step": the first concrete action, which $user can start in the next two minutes. 80 characters at most. Start with a verb. Use the notes of the task when they help.
Answer only with a JSON object, with no other text:
{"task": "<the task name, exactly as in the list>", "why": "<the line>", "step": "<the first step>"}
