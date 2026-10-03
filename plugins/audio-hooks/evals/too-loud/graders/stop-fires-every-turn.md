---
type: llm
---

PASS if the reply explains that the `stop` hook fires at the end of every turn (not only when the whole task is finished), which is why it sounds so often.
FAIL if it blames something else, says `stop` marks task completion, or never explains why the audio is frequent.
