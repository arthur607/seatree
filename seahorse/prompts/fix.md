## Task: fix the vault
The validator found the problems below. Fix each error with the smallest possible change, without losing content. Warnings are optional: fix only the easy ones.
Never edit the text of an engram. If an error says an engram was edited, do not try to fix it. Record it in the log.
A duplicate name or a bad character in a file name: rename the note of the agents with `git mv`, such as "Dune Part Two" for "Dune: Part Two", and change the links to it. Never rename a note with `written_by: owner`.
```text
$lint
```
