---
name: audio-hooks
description: "Configure, test, mute/snooze, or troubleshoot echook (audio-hooks): sound, desktop-toast, TTS and webhook notifications plus the status line for Claude Code, Cursor and Codex. Use when the user wants to be notified, silenced, or to change the status line/context display."
---

# audio-hooks skill

This plugin is the AI control surface for the echook project. The user does NOT operate it by hand. You operate it for them through the `audio-hooks` binary, which this plugin adds to your Bash tool's PATH.

## Rules

- **The `audio-hooks` CLI is the only interface.** Every command is non-interactive and returns JSON with stable error codes. Never edit `user_preferences.json` by hand: use `audio-hooks set <dotted.key> <value>` and the typed setters (`hooks enable`, `theme set`, `webhook set`, `tts set`, `rate-limits set`).
- **Run `audio-hooks manifest` first** for anything you do not immediately recognise (subcommands, hooks, config keys, error codes, env vars). It never lags the running version; this file can. `audio-hooks manifest --schema` gives the config JSON Schema.
- **Diagnose before guessing:** `audio-hooks status`, then `audio-hooks diagnose` (its errors carry a `code` and a `suggested_command` to run), then `audio-hooks logs tail --n 20`. Verify a fix with `audio-hooks test all`.
- **Do not ask the user to confirm** ("[y/N]"). If they gave an instruction, run it.
- **Do not translate JSON output.** Keys and values are stable contracts.
- **Reporting commands are read-only** (`status`, `diagnose`, `get`, `hooks list`, `snooze status`, `manifest`, `statusline segments`, every `--help`, ...). A stale preferences file (`PREFS_SCHEMA_STALE`) is not fixed by running them; the next hook event or any state-changing command migrates it, or run `audio-hooks migrate` (v6.7, no flags, idempotent).
- **No subcommand acts on an argument it does not understand.** State-changing subcommands answer an unknown flag or stray positional with `INVALID_USAGE` and change nothing; `--help` is always safe.

## Installing (three entry points)

- **Claude Code (plugin, the recommended path):** `claude plugin marketplace add ChanMeng666/echook --json` then `claude plugin install audio-hooks@chanmeng-audio-hooks --json` (`audio-hooks install --plugin` prints exactly these and changes nothing). Then **ask the user to type `/reload-plugins`** in any already-open session. It is REPL-only; never fake it via Bash.
- **Cursor:** `audio-hooks install --cursor` (see the duplicate-bridge section below).
- **Codex:** plugin path `codex plugin marketplace add ChanMeng666/echook` then `codex plugin add audio-hooks@chanmeng-audio-hooks`, or the native fallback `audio-hooks install --codex`; follow the `next_steps` / `feature_flag_state` in its output. Codex does not auto-bridge Claude Code plugins.

`audio-hooks install` has **no default mode**: it needs exactly one of `--plugin`, `--cursor`, `--codex`, else `INVALID_USAGE`. The script installer was removed in 7.0.0: `install --scripts` returns `SCRIPT_INSTALL_REMOVED` and installs nothing. A script install left by an older release plus the plugin makes every hook fire twice (`diagnose`: `DUAL_INSTALL_DETECTED`); the fix is bare `audio-hooks uninstall`. Upgrade the plugin without losing config with `audio-hooks upgrade` (`--check-only` to look). Uninstall: `audio-hooks uninstall --plugin` prints the `claude plugin uninstall ... --keep-data --json` command (then `/reload-plugins`); a script install is removed by bare `audio-hooks uninstall`. If that returns `UNINSTALL_INCOMPLETE`, show the user `unmatched_references` before running `--remove-unmatched`.

## Mapping a request

Match the request to a subcommand in `audio-hooks manifest`, then run it:

- mute: `audio-hooks snooze <30m|1h|90s|2d|bare minutes|off|status>`
- single hook or variant on/off: `audio-hooks hooks enable|disable|enable-only <name>...` (`hooks list --variants` shows the 47 matcher variants)
- theme, webhook, TTS, rate-limit alerts: `theme set`, `webhook set|test|clear`, `tts set`, `rate-limits set`
- delivery mode: `notification_settings.mode` (`audio_only`, `notification_only`, `audio_and_notification`, `disabled`), with `notification_settings.per_hook.<hook>` overrides
- anything else: `audio-hooks get|set <dotted.key>`
- backups: `audio-hooks backup list`, `backup restore latest-external`

Precedence for variants: an explicit variant key wins; otherwise a parent set to `false` is a hard kill switch for every variant under it. To keep exactly one variant of a muted category, set that variant key explicitly, never the parent.

## Judgement calls

### "Claude is too loud" / "it chimes too often"

Lead with `audio-hooks hooks enable-only notification permission_request` as the first command. Then explain why: `stop` fires at the **end of every turn**, not at task completion, and Claude Code exposes no field that distinguishes a final turn from an intermediate one, so no setting makes `stop` mean "the task is done". Say this plainly rather than tuning debounce and hoping. Further fixes, in order:

1. `notification`/`idle_prompt` is the genuine "waiting for you" signal (documented to arrive about 60 s after Claude finishes and only if the user has not typed; since Claude Code 2.1.288 not while background agents run); `permission_request` is the immediate one.
2. `audio-hooks set filters.stop.skip_if_background_tasks_running true` (v6.4): quiet while subagents/shells are in flight (v6.6.0 counts `pending` too and ignores Claude Code's own `dream` / `auto-mode scan` / `memory import` tasks).
3. `audio-hooks set filters.stop.skip_if_session_crons_scheduled true` (v6.6.0, opt-in): quiet while `/loop` or scheduled wakeups are pending. Separate on purpose: a recurring cron stays in the array for the whole session.
4. `audio-hooks set filters.posttooluse.min_duration_ms 30000` (v6.5): only sound for tools that took that long. Fails open on Cursor and Codex, which do not report duration. Prefer this to debounce, which cannot tell a burst of fast tools from one long build.
5. In Cursor, `audio-hooks set filters.stop.skip_if_aborted true` skips a cancelled turn.
6. `audio-hooks set playback_settings.debounce_ms 60000` last (blunt; several sessions chiming at once).

`notification_agent_needs_input` and `notification_agent_completed` need Claude Code 2.1.198+, ship off, and were never observed firing; do not present them as a working "task finished" cue. `model_refusal_fallback` is deliberately not registered; do not offer it.

### "The task-finished sound stopped working"

Almost always the fix above taken earlier: `stop` left disabled in the preferences, which the user reads as an upstream regression. `audio-hooks diagnose` reports `NO_COMPLETION_SIGNAL` when `notification` is off too. Check `audio-hooks hooks list` before investigating Claude Code, then restore:

```
audio-hooks hooks enable stop
audio-hooks set filters.stop.skip_if_background_tasks_running true
```

### "Notify me while I am away from the screen"

Recommend a channel that works, with a concrete command:

- desktop toast: `audio-hooks set notification_settings.mode audio_and_notification` (it ships this way; use `notification_only` for no sound). To toast only when it needs the user: mode `audio_only`, then `per_hook.notification` and `per_hook.permission_request` set to `audio_and_notification`.
- phone or other app: `audio-hooks webhook set --url <url> --format ntfy` (formats `slack`, `discord`, `teams`, `ntfy`, `raw`), then `webhook test`.
- spoken summary: `audio-hooks tts set --enabled true --speak-assistant-message true` (code blocks and secrets are stripped, but the reply is read aloud, so not near bystanders).
- the "waiting for you" trigger is `notification` / `idle_prompt`; `permission_request` is the immediate one.

If every backend fails, `diagnose` reports `NOTIFICATION_FAILED` naming the backend.

**Never recommend `notification_settings.terminal_sequence.*`, and never claim a terminal bell or window title is available.** v6.5.0 shipped it; v6.5.1 found it inert: Claude Code writes a hook's `terminalSequence` only from a synchronous completion path, and every echook handler is `async: true`, so no escape is ever emitted (still inert at Claude Code 2.1.288). `diagnose` reports `TERMINAL_SEQUENCE_INERT` if a user already enabled it; say so and use the channels above. Do not "fix" it by making handlers synchronous. Details: `docs/EVENT_BEHAVIOR_NOTES.md`.

### Out-of-scope features (pomodoro, timers, wellness)

echook does two things only: (1) audio and out-of-band notification of editor lifecycle events, (2) the status line. A pomodoro/break timer, breathing exercise, gamification, opening URLs or side-commands during a session is **out of scope by design** (`focus_flow` was removed in v6.0.0). Decline and say it is intentionally not part of echook. Invent no command, setting or hook for it; suggesting a separate timer tool is fine.

### Cursor install and the duplicate bridge

Cursor 3.2.x+ auto-bridges the Claude Code plugin, so on a machine that already has the plugin, Cursor already plays echook with no install. The command is `audio-hooks install --cursor`, but there it **aborts with `DUPLICATE_BRIDGE`**, because Cursor runs every matching hook from every source without de-duplicating (everything would fire twice). Say so before handing over the command. `--force` is only for someone who accepts that trade-off; the runtime guard `DUPLICATE_BRIDGE_RUNTIME_SKIP` then suppresses the native path. Cursor's bridge toggle (Settings, Rules, Skills, Subagents, "Include third-party Plugins, Skills, and other configs") is global and has no effect on `cursor-agent`. Check `audio-hooks status` -> `editor_targets.cursor.state` (`bridged-via-claude-code`, `native`, `double-registered` - fix with `audio-hooks uninstall --cursor` - or `inactive`). Under the bridge only 8 coarse events fire; `notification` and `permission_request` have no Cursor equivalent. Cursor-only users install from a clone; upgrade is `git pull && install --cursor` (there is no `upgrade --cursor`). Refresh the bridged plugin cache with `audio-hooks upgrade`, never `/plugin uninstall + install`, which destroys config.

Cursor `stop` carries `status` (`completed|aborted|error`), documented but not yet captured live. `filters.stop.error_as_stop_failure true` makes an errored Cursor turn become `stop_failure`; it needs `hooks enable stop_failure` too, and with the key on and `stop_failure` off that turn is silent.

### Status line, and only some segments

Install with `audio-hooks statusline install` (then restart Claude Code); `uninstall`, `show` and `segments` (the live catalogue of 33 segments, 31 on by default) are the others. To show a subset, use the **`visible_segments` whitelist**, not `hidden_segments`: "only context usage" is `audio-hooks set statusline_settings.visible_segments '["context"]'` and nothing else in the list. To add a segment, `get` the current array, append, `set` it back. `'[]'` restores the default. `hidden_segments` (blacklist, only when the whitelist is empty) suits "everything except...". `extra_segments` turns on the opt-in `prompt_cache` and `remote` (`audio-hooks set statusline_settings.extra_segments '["prompt_cache"]'`). `spend_limit` and `fast_mode` are on by default but draw only when Claude Code sends the data, so "why is there no spend limit" is almost always "your session does not send one". Wrapping: lines reflow by segment; `statusline_settings.max_width` pins the width (`0` = auto); truncation with `...` usually means Claude Code older than v2.1.153, which does not export `COLUMNS`. A per-agent row in the agent panel is a separate surface: `audio-hooks statusline subagent install|show|uninstall` (restart Claude Code); the plugin cannot supply it as a default. Full reference: `docs/STATUS_LINE.md`.

**Codex cannot render custom status text.** It draws only a fixed list of built-in item IDs; echook curates them. For a cut-off Codex bar: `audio-hooks statusline codex apply --preset balanced` (`--target terminal_title|both`, `codex show`, `codex preview`), then restart Codex or run `/statusline`.

### "Context jumped to 97%"

Almost always expected arithmetic, not a bug. Switching from a 1M-context model to a 200K window keeps the tokens identical and shrinks the denominator 5x. Since v5.1.3 the indicator shows absolute counts (`Context: 83% (166K/200K)`), so point at that. The bar is green under 50%, yellow 50-80% (consider `/compact` or `/clear`), red above 80%. To check what Claude Code actually pipes in, have the user set `CLAUDE_HOOKS_DEBUG=1` before launching Claude Code; the script then dumps the last stdin JSON to `${CLAUDE_PLUGIN_DATA}/statusline.last_input.json` (inspect `context_window.context_window_size`). It may contain paths and the last assistant message, so tell them to unset it afterwards.

### Snooze

"Mute for 30 minutes" is exactly `audio-hooks snooze 30m`; "unmute" is `audio-hooks snooze off`.

### Unrelated requests

This skill is for echook only. A request with nothing to do with audio notifications, sounds, or the status line (a general coding question, for instance) must not invoke it, and the answer must not mention echook.

## What you should NOT do

- Do NOT edit `user_preferences.json` or `~/.claude/settings.json` hook registrations by hand. The plugin registers its own hooks.
- Do NOT try `audio-hooks install --scripts`: the script installer was removed in 7.0.0 (`SCRIPT_INSTALL_REMOVED`). Use `--plugin`.
- Do NOT recommend `terminal_sequence`, a terminal bell or a window title.
- Do NOT invent commands or settings for out-of-scope features.
- Do NOT claim `stop` means task completion.
- Do NOT fake `/reload-plugins` through Bash; ask the user to type it.
- Do NOT recommend `audio-hooks upgrade --force` unless `audio-hooks status` confirms the upgrade is stuck.
