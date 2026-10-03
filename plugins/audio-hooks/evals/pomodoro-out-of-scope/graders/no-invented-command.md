---
type: regex
pattern: 'audio-hooks\s+(?:set|hooks\s+enable)\s+\S*(?:pomodoro|timer|break|focus)'
flags: i
match: not_contains
---

No invented `audio-hooks set|hooks enable <pomodoro/timer/break/focus>` command appears.
