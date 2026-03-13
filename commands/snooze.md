---
name: snooze
description: Temporarily mute Claude Code audio hooks
argument-hint: [30m|1h|2h|status|off]
allowed-tools:
  - Bash
---

Snooze (temporarily mute) Claude Code audio hooks, or check/cancel snooze status.

<process>
1. Read the project path: `cat ~/.claude/hooks/.project_path`
2. Run: `bash "$PROJECT_PATH/scripts/snooze.sh" $ARGUMENTS`
3. Show the output to the user.

If ~/.claude/hooks/.project_path does not exist or the script is not found, tell the user the full install is required: `bash scripts/install-complete.sh`
</process>
