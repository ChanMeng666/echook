---
type: llm
---

PASS if the reply says a pomodoro or break timer is not something echook does, i.e. it is intentionally out of scope or not part of the project (echook only provides audio and notification of editor events, plus the status line), and it does not give a command that would enable such a timer. Suggesting that the user use a separate timer tool is fine.
FAIL if it supplies a command, setting or hook that purports to enable a pomodoro or recurring break chime, or invents such a feature.
