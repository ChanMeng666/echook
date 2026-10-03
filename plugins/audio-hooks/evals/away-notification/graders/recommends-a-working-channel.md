---
type: llm
---

PASS if the reply recommends at least one channel that works away from the screen, with a concrete `audio-hooks` command: a webhook (for example `audio-hooks webhook set --url ... --format ntfy`), the desktop notification mode (`notification_settings.mode`), or spoken text-to-speech (`tts`). Mentioning the idle_prompt / notification hook as the "waiting for you" signal is a plus.
FAIL if it recommends none of these, or if it recommends enabling `notification_settings.terminal_sequence` / terminalSequence, a terminal bell, or a window title as a way to be notified.
