# ROUTER.md
Decision table for the Integrator. An engram or a note looks like X, its content goes to Y.
One engram can feed several destinations at the same time. The ROUTER picks the main destination. Principle 2 of [[_AGENTS]] decides the rest.
When the Integrator gets it wrong, edit this table. It is the file that must change the most.
## Signals and destinations
The signals below are in English. Match their equivalents in {{language}}.
| It looks like | Signals | Main destination | Template |
|---|---|---|---|
| Something to do, no fixed date | a verb in the infinitive, "I need to", "I have to", an estimate | a note in `1. 🎯 Intention/Tasks/` | Task |
| Something to do this week | "this week", "tomorrow", a weekday | a task note, linked inside its day card in the current `1. 🎯 Intention/Week/` note | Task |
| Something with a fixed date | bill, document, renewal, appointment, deadline | a task note with `due`, and `repeat` if it repeats. Details go in its body | Task |
| A long-term goal | "this year", "by", a big outcome | `1. 🎯 Intention/Goals/` | Goal |
| A movie, series, book, game, place or purchase that {{user}} wants | the name of the thing + "I want", "play", "watch", "read", "go", "buy" | `1. 🎯 Intention/Wishlist/<kind>/` | Wish |
| Something from the list that {{user}} lived | "I finished", "I watched", "I went", "I played", "I read" | move it to `2. 📓 Memory/Experiences/<kind>/` | Experience |
| Something that happened | past tense, "today", "yesterday", emotion | `2. 📓 Memory/Journal/` for the day it was sent | Journal |
| A trip | destination + period | planned: task notes in Intention, with `due` for fixed dates. Done: `2. 📓 Memory/Trips/` | Trip |
| Career history | a past job, promotion, evaluation, interview, offer | `2. 📓 Memory/Career/<company>/` | — |
| A real person | a name in a personal context | `2. 📓 Memory/People/`, if it passes the Principle 5 threshold | Person |
| Anything about an ongoing subject | the current job, a project, a hobby, a fictional universe | the folder of the subject in `3. 📚 Knowledge/`, or in `3. 📚 Knowledge/Projects/` for what {{user}} builds | — |
| A new project | a named initiative with a goal | a folder inside its subject, or in `3. 📚 Knowledge/Projects/` if it stands alone, with a main note `type: project` | Project |
| A subject to study, a link, a video, an article | "study", "learn", a URL | the folder of the subject in `3. 📚 Knowledge/`, with the URL in `url` | Knowledge |
| Something about who {{user}} is | "I am", "I like", "I always" | evidence for the Observer, cited in the log | — |
| A reply to a Messenger question | answers something that the Messenger asked | the note that raised the question | — |
| A refusal of a suggestion | "no", "not interested", a reason | the `## Dislikes` section of `4. 🪞 Identity/Tastes.md`, with the reason in the words of {{user}} | — |
| No clear signal | none of the above | the engram gets `state: unclear` and the question goes to the morning summary | — |
## Tie-breaks
Two signals: the most specific wins. "Study pointers by Friday, 2h" is a task on Friday in the Week, and the subject note in Knowledge links to it.
A person is never a destination alone. The agent extracts the person, and the content goes where the rest points.
Not sure about the subject: use the nearest existing subject folder and ask in the summary. A new subject folder needs two or more notes about it, or a request from {{user}}.
## Subjects in Knowledge
Each subject folder has a main note with the name of the subject, such as `Health/Health.md`. What {{user}} builds or works on lives under `Projects/`, such as `Projects/Acme/Acme.md` for the current company. Projects of a subject are folders inside it, such as `Projects/Acme/Billing Revamp/`, each with a main note `type: project`. The Dashboard lists active projects from everywhere.
| Folder | Covers |
|---|---|
| Projects | what {{user}} builds or works on: the current company, its projects and work context, and side projects. One folder each |
| Misc | odds and ends with no subject of their own, such as an app that {{user}} found |
Add a row to this table for each new subject folder.
Missing a subject? The note goes to Misc. The agent proposes a subject in the log and creates it when it has three notes or when {{user}} asks.
## Wishlist and Experiences kinds
`Movies`, `Series`, `Books`, `Games`, `Places` and `Shopping`. Each kind has a table in the Wishlist, `<Kind>.base`, that shows the Wishlist and Experiences folders together. The first column is the `done` checkbox.
