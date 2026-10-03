---
type: regex
pattern: 'audio-hooks\s+set\s+notification_settings\.terminal_sequence\S*\s+(?:true|on|1)'
flags: i
match: not_contains
---

The reply never tells the user to turn `terminal_sequence` on.
