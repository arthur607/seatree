---
type: log
---
Record of every message the Messenger sends. One line per message, written by the code.
Format: `- 2026-01-15 10:30 · reminder · id:a1b2c3 · tg:123 · [[source]] · reaction: pending · text`
The text of a reminder starts with its kind, which code reads: `week-before`, `day-before` or `due-today`.
The text of a birthday starts with when it goes out, which code reads: `week-before` or `today`.
