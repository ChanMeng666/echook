# echook (audio-hooks plugin)

echook tells you what Claude Code did when you are not looking at it. It plays a sound at your desk, can speak a summary when you are away, and can send a desktop toast or a webhook message when you are in another window. It also ships an optional status line. It is configured by an `audio-hooks` command-line tool that Claude Code (or you) runs; nothing here is interactive.

This file describes what the plugin runs, what it can send and where, and what it writes, so you can judge it before installing. Everything below is in this folder as readable source.

## What it runs

- **A Python hook runner on lifecycle events.** `hooks/hooks.json` registers one handler per event (30 Claude Code event types, 47 matcher variants). Each runs `python "${CLAUDE_PLUGIN_ROOT}/runner/run.py" <event>`, asynchronously, and reads the event payload Claude Code passes on stdin. By default only `notification`, `stop` and `permission_request` make a sound; every other event is off until you switch it on with `audio-hooks hooks enable <name>`.
- **The `audio-hooks` CLI** (`bin/`), which reads and writes the plugin's own settings. A bundled skill teaches Claude when to call it (for example "snooze for 30 minutes" or "Claude is too loud").
- **A status line script, only if you ask for it.** `audio-hooks statusline install` registers it; until then it is never run.
- **Local helpers for sound and notifications:** PowerShell (Windows media player, toast and speech), `afplay`, `say` and `osascript` (macOS), `mpg123`/`ffplay`/`paplay`/`aplay`, `notify-send` and `espeak`/`spd-say` (Linux). `git` is run by the status line to show the branch.

## What it can send, and where

By default nothing leaves your machine. The only network traffic echook itself creates is the webhook POST, and only after you configure a webhook.

| Feature | Default | What it handles | Where it goes |
|---|---|---|---|
| Sounds | on for 3 events | Which event fired | Local audio player |
| Desktop notification | on | Event name and a short context line (for a finished turn, the first characters of Claude's last message) | Your operating system's notification centre |
| Text-to-speech | off | Fixed phrases, or with `speak_assistant_message` the start of Claude's last message (200 characters by default) | Your operating system's speech engine |
| Webhook | off | See below | The URL **you** set, and nowhere else |

A webhook POST goes out only for the event types you list (default `stop`, `notification`, `permission_request`, `posttoolusefailure`, `stop_failure`). The Slack, Discord, Teams and ntfy formats send one line of text, which for a finished turn includes the first characters of Claude's last message. The `raw` format sends the full event as JSON: the last assistant message, the tool name and tool input, the session id and the rest of the hook payload (not the transcript path, and not the Cursor user email unless you opt in). Treat the destination as having read access to that content. echook operates no server and receives nothing.

The webhook URL is declared as a sensitive plugin option, so Claude Code keeps it in its credential store rather than in `settings.json`.

## What it writes

- **Its data directory** (`${CLAUDE_PLUGIN_DATA}`, normally `~/.claude/plugins/data/audio-hooks-chanmeng-audio-hooks/`): `user_preferences.json`, a rotated `logs/events.ndjson` (event names, outcomes, errors; with text-to-speech on, the first 100 characters of what was spoken), a `queue/` folder of snooze and debounce markers, and status line caches. Setting `CLAUDE_HOOKS_DEBUG=1` additionally writes the latest status line payload there.
- **Preference backups** in `~/.claude-audio-hooks-backups/`, made when you change a setting through the CLI.
- **`~/.claude/settings.json`**, only when you run `audio-hooks statusline install` (or `uninstall`, or the `subagent` form). The hooks themselves never edit settings.
- **`~/.cursor/hooks.json` or `$CODEX_HOME/hooks.json`**, only when you run `audio-hooks install --cursor` or `--codex`.

Commands that only report (`status`, `diagnose`, `get`, `hooks list`, `manifest` and similar) change nothing on disk.

## What it never does

- No telemetry, analytics or update checks; no connection other than the webhook you configure.
- It does not download or execute remote code, and it does not read your transcript or conversation history: it only sees the payload Claude Code hands to each hook.
- Under Claude Code its hooks print nothing back, so they cannot inject context, approve or deny a permission, or block a tool call. It registers no blocking hook. (The one exception in the code is the opt-in `terminalSequence` setting, which is off by default and which current Claude Code does not act on for async hooks; it can only ask the terminal for a bell or title.)
- It does not change Claude's permissions or safety settings.

## Requirements

- Claude Code with plugin support, and Python 3.9 or newer available as `python` on your `PATH` (tested on 3.9 to 3.13).
- Windows (PowerShell; Git Bash for Claude Code itself), macOS, or Linux with one of the audio players above. Without a player the hooks run silently and `audio-hooks diagnose` says so.

## Install and documentation

```bash
claude plugin marketplace add ChanMeng666/echook
claude plugin install audio-hooks@chanmeng-audio-hooks
```

Then type `/reload-plugins` in Claude Code, and run `audio-hooks status` and `audio-hooks diagnose` to confirm.

Full documentation, the complete command reference (`audio-hooks manifest`), troubleshooting and the changelog live in the repository: <https://github.com/ChanMeng666/echook>. Privacy policy: [PRIVACY.md](https://github.com/ChanMeng666/echook/blob/master/PRIVACY.md). Support and security reports: see `SUPPORT.md` and `SECURITY.md` there. Licensed under MIT.
