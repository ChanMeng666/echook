---
type: llm
---

PASS if the reply says that Cursor already picks up the Claude Code plugin through its own bridge, so a native `audio-hooks install --cursor` on this machine would be refused with `DUPLICATE_BRIDGE` (or would make every event fire twice), and that `--force` is only for someone who accepts that trade-off. Giving `audio-hooks install --cursor` as the command, with that caveat, is correct.
FAIL if it hands over the install command with no mention of the duplicate-bridge problem, or recommends `--force` as the normal route.
