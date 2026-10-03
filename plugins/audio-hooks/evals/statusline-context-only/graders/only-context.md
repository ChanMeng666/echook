---
type: regex
pattern: 'visible_segments\s+\S*(?:model|cwd|cost|tokens|branch|quota|snooze)'
flags: i
match: not_contains
---

The whitelist in the command lists only `context`.
