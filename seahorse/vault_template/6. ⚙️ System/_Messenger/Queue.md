---
type: log
---
Messages left ready by the agents. The Companion sends them at its drawn moments, and deliveries go out when their moment comes, following [[_MESSENGER]].
Exact format of each line. The message itself is in {{language}}:
`- [ ] 2026-01-15 · connection · normal · ready message in up to three lines · [[source note]]`
Types: connection, proposal, question, delivery. Priorities: high, normal. The date may have a time, `2026-01-15 20:00`, and then the item waits for it. A delivery links the note it delivers, which goes as text to read in the chat. Sent items become `[x]`, expired ones become `[-]`.
