---
name: audio-hooks
description: Use whenever the user asks to install, configure, uninstall, snooze, mute, test, troubleshoot, or change settings for the echook audio notification system. Trigger phrases include "audio hooks", "audio notifications", "snooze audio", "mute claude", "claude is too loud", "test audio", "switch audio theme", "rate limit alerts", "audio webhook", "TTS", "text to speech", "notification mode", "audio only", "notification only", "debounce", "status line", "statusline", "context usage", "context window", "context monitor", "compact reminder", "uninstall audio", "audio status", "audio version", "install for cursor", "install for codex", "codex audio", "codex hooks", "cursor audio", "cursor hooks", and the slash command /audio-hooks. Also use when diagnosing why Claude Code, Cursor, or Codex is silent (or noisy) for the user, or when the user wants to monitor context window usage.
---

# audio-hooks skill

This plugin is the AI control surface for the echook project. The user does NOT operate this project by hand. You operate it on their behalf via the `audio-hooks` binary that this plugin adds to your Bash tool's PATH.

**Golden rule:** when in doubt about the project's current capabilities, run `audio-hooks manifest` first. The manifest is the canonical machine description of every subcommand, every config key, every hook, every audio file, and every error code. It is always up to date with the running version. Treat this SKILL.md as an orientation; treat `audio-hooks manifest` as the source of truth.

## What the user can ask for and what you should run

**Install / set up the project**

The plugin install (which you are using right now) is the recommended path for Claude Code users. If a user is not yet on the plugin, tell them to run `/plugin marketplace add ChanMeng666/echook` and `/plugin install audio-hooks@chanmeng-audio-hooks` inside Claude Code. **Cursor IDE 3.2.16+ users get audio-hooks for free via Cursor's built-in third-party hooks bridge** — no separate Cursor install needed. For users who run Cursor *without* Claude Code, use `audio-hooks install --cursor` (see "Install for Cursor-only users" below). **Codex users** should use the Codex plugin path when available, or `audio-hooks install --codex` as the native hooks.json fallback — Codex does NOT auto-bridge Claude Code plugins (see "Install for Codex users" below). Once installed, verify with:

```bash
audio-hooks status
audio-hooks test all
```

**Snooze / mute / quiet hours**

| User says | Run |
|---|---|
| "snooze audio for 30 minutes" | `audio-hooks snooze 30m` |
| "shut up for an hour" | `audio-hooks snooze 1h` |
| "mute audio for the rest of the day" | `audio-hooks snooze 8h` |
| "unmute" / "resume" / "cancel snooze" | `audio-hooks snooze off` |
| "is audio snoozed?" | `audio-hooks snooze status` |

Duration syntax: `30m`, `1h`, `90s`, `2d`, or a bare integer (interpreted as minutes).

**Upgrade the plugin without losing config**

`audio-hooks upgrade` is the AI-first way to refresh the plugin code (and `~/.claude/plugins/cache/`) without touching the user's preferences. Use it whenever:

| User says | Run |
|---|---|
| "upgrade audio-hooks" / "refresh the cache" / "Cursor still plays old theme" | `audio-hooks upgrade` |
| "is there a new version?" | `audio-hooks upgrade --check-only` |
| "the upgrade got stuck" | `audio-hooks upgrade --force` (only after confirming via `audio-hooks status`) |

`upgrade` auto-detects scope via `claude plugin list --json`, tries `claude plugin update` first (data-preserving), falls back to `uninstall --keep-data + install` if needed. On success, the user's `~/.claude/plugins/data/audio-hooks-chanmeng-audio-hooks/user_preferences.json` is preserved verbatim, then loaded through auto-migration so new keys from the new template are merged in non-destructively.

**Restore from a backup**

| User says | Run |
|---|---|
| "what configurations have I changed?" | `audio-hooks status` (look at the `customizations` field) |
| "show me available backups" | `audio-hooks backup list` |
| "restore my config from before the upgrade" | `audio-hooks backup restore latest-external` |
| "delete old backups" | `audio-hooks backup prune` |

**Enable / disable individual hooks**

Run `audio-hooks hooks list` to see all 39 hooks with their current state. Then:

| User says | Run |
|---|---|
| "stop the audio for tool execution" | `audio-hooks hooks disable pretooluse` and `audio-hooks hooks disable posttooluse` |
| "enable rate-limit warnings" | `audio-hooks set rate_limit_alerts.enabled true` |
| "I only want stop and notification audio" | `audio-hooks hooks enable-only stop notification permission_request` |
| "enable the v5.0 permission_denied hook" | `audio-hooks hooks enable permission_denied` |
| "watch .env files for changes" | `audio-hooks hooks enable file_changed` and `audio-hooks set file_changed.watch '[".env",".envrc"]'` |
| "ping me when setup/init finishes" | `audio-hooks hooks enable setup` |
| "different sound for shell vs MCP calls in Cursor" | `audio-hooks hooks enable shell_before` and `audio-hooks hooks enable mcp_before` (v6.2, Cursor-only granular events) |

**Check project status**

Run `audio-hooks status` when the user asks "what's the current audio config?", "is audio working?", "show me audio status", etc. It returns a full snapshot: version, theme, enabled hooks count, snooze state, webhook, TTS, rate-limit alerts, and install mode.

```bash
audio-hooks status                     # full state snapshot
audio-hooks version                    # version + install type detection
```

**Change audio theme**

The project ships two themes. `default` is voice recordings, `custom` is non-voice chimes.

```bash
audio-hooks theme list                  # show available + current
audio-hooks theme set custom            # switch to chimes
audio-hooks theme set default           # switch to voice
```

**Webhook fan-out (Slack / Discord / Teams / ntfy / raw)**

```bash
audio-hooks webhook                     # view current webhook config
audio-hooks webhook set --url https://ntfy.sh/my-claude-channel --format ntfy
audio-hooks webhook set --url https://hooks.slack.com/services/... --format slack
audio-hooks webhook test                # POST a test payload
audio-hooks webhook clear               # disable
```

Supported formats: `slack`, `discord`, `teams`, `ntfy`, `raw`. The raw format ships the `audio-hooks.webhook.v1` schema. Every event includes `session_id`, `session_name`, `worktree`, `agent`, `rate_limits`, `last_assistant_message`, `notification_type`, `error_type`, `source`, `trigger`, `load_reason`, and `permission_suggestions` at the top level.

**Text-to-speech**

```bash
audio-hooks tts set --enabled true
# Speak a sanitized summary of Claude's final reply on stop/subagent_stop.
# Code blocks and secrets are stripped automatically before anything is spoken,
# but note the reply is read aloud — avoid near bystanders.
audio-hooks tts set --speak-assistant-message true
audio-hooks tts set --assistant-message-max-chars 200
```

**Rate-limit alerts (v5.0)**

The runner inspects every hook's stdin for `rate_limits.{five_hour,seven_day}` and plays a one-shot warning when crossing thresholds. Default thresholds are `[80, 95]`. Each (window, threshold, resets_at) tuple fires exactly once per reset window, so the user is alerted at 80% and again at 95% but never spammed.

```bash
audio-hooks rate-limits set --enabled true
audio-hooks rate-limits set --five-hour-thresholds 75,90,98
audio-hooks rate-limits set --seven-day-thresholds 80,95
```

**Status line / context window monitoring**

The status line displays real-time audio-hooks state and context window usage at the bottom of Claude Code. It includes a color-coded context usage bar that warns the user when context is getting full (the "agent dumb zone" starts around 60%):

- 🟢 Green (< 50%): safe — agent performs well
- 🟡 Yellow (50–80%): caution — user should `/compact` or `/clear`
- 🔴 Red (> 80%): danger — agent performance degrades significantly

| User says | Run |
|---|---|
| "install the status line" / "show context usage" / "monitor context window" | `audio-hooks statusline install` then tell the user to restart Claude Code |
| "remove the status line" | `audio-hooks statusline uninstall` |
| "is the status line installed?" | `audio-hooks statusline show` |

After installing, the status line updates every 60 seconds and shows two lines:
```
[Opus 4.8 (1M context)] | 🧠 high | ⚡ CC v2.1.193 | 📁 D:\…\claude-code-audio-hooks | 🔊 echook v6.2.0 | 6/39 Sounds | Webhook: off | Theme: Voice
🌿 main  ████░░░░ API Quota: 60% · resets 2pm  ███████░ Weekly: 82% · resets Jul 4 9pm  █████░░░ Context: 65% (130K/200K) ⚠️ /compact  💲 $0.42 +156/-23
```
The status line pins the key facts from Claude Code's **startup banner** so they stay visible after the banner scrolls off the top of the terminal: the model + reasoning **effort** (`🧠`), Claude Code's own **version** (`⚡ CC v…`, distinct from echook's `🔊 echook v…`), the **cwd**, the **5-hour API quota** and the headline **weekly (7-day) limit + reset date & time** (`Weekly: 82% · resets Jul 4 9pm` — the weekly reset is days out, so it shows the date; the always-soon 5-hour reset stays a bare time), and session **cost + diff** (`💲 $0.42 +156/-23`). The `📁` segment (`cwd`) is abbreviated (home → `~`, long paths shortened to `<root>…<last folder>`) so the user can tell at a glance which project the session is in.

> The subscription **plan name** ("Claude Max"/"Pro") shown in the banner is *not* piped to status line scripts by Claude Code, so echook intentionally does not show it. The presence of the weekly/API-quota bars already implies a Claude.ai subscription.

**Customise which status line segments to show**

The status line exposes **29 segments** — every useful field Claude Code pipes to a status line script. By default all are shown (most richer ones self-omit when their data is absent, so a plain session stays clean). Run **`audio-hooks statusline segments`** for the authoritative live catalog (name, line, source field, conditional flag).

Line 1 (identity / config): `model`, `session_name`, `agent`, `effort`, `thinking`, `vim`, `output_style`, `cc_version`, `cwd`, `repo`, `version`, `sounds`, `webhook`, `theme`
Line 2 (live state / metrics): `snooze`, `branch`, `git_dirty`, `worktree`, `pr`, `added_dirs`, `api_quota`, `weekly_quota`, `context`, `tokens`, `exceeds_200k`, `cost`, `duration`, `api_time`, `burn_rate`

Two ways to choose segments:
- **`visible_segments`** (whitelist) — when non-empty, *only* these show. Best when the user wants a short, fixed line.
- **`hidden_segments`** (blacklist) — applied only when `visible_segments` is empty: show everything *except* these. Best when the user likes the comprehensive default but wants to drop a couple (e.g. `audio-hooks set statusline_settings.hidden_segments '["burn_rate","api_time"]'`).

(`git_dirty` shells out to `git status --porcelain`, cached ~5s; everything else comes from the stdin session JSON. Conditional segments self-omit when Claude Code doesn't supply that data — e.g. `pr` only inside a PR, `vim` only in vim mode, `weekly_quota` only for Claude.ai subscribers, `output_style` only when not the default.)

**Auto-reflow (no truncation).** Each line automatically wraps into as many rows as the terminal width needs — segments are never split, so a content-rich status line shows in full instead of being cut off with an ellipsis (`Webho…`). Width is read from the `COLUMNS` env var Claude Code provides (Claude Code v2.1.153+), falling back to 80 columns. If a user wants fewer rows, trim segments with `visible_segments`. To pin the wrap width explicitly (e.g. when `COLUMNS` is unreliable), set `statusline_settings.max_width` to a column count (`0` = auto):

| User says | Run |
|---|---|
| "my status line wraps too much / pin it to 120 columns" | `audio-hooks set statusline_settings.max_width 120` |
| "status line is getting cut off / truncated with …" | Usually means an old Claude Code (< v2.1.153, which doesn't export `COLUMNS`) — pin it: `audio-hooks set statusline_settings.max_width <your terminal width>` |
| "go back to auto width" | `audio-hooks set statusline_settings.max_width 0` |
| "the status line has too many rows" | Trim segments, e.g. `audio-hooks set statusline_settings.visible_segments '["model","cwd","context","weekly_quota"]'` |

| User says | Run |
|---|---|
| "only show context usage in the status line" | `audio-hooks set statusline_settings.visible_segments '["context"]'` |
| "show context and API quota only" | `audio-hooks set statusline_settings.visible_segments '["context","api_quota"]'` |
| "show my weekly limit only" | `audio-hooks set statusline_settings.visible_segments '["weekly_quota"]'` |
| "show the model, effort, and Claude Code version" | `audio-hooks set statusline_settings.visible_segments '["model","effort","cc_version"]'` |
| "show session cost in the status line" | Read current with `audio-hooks get statusline_settings.visible_segments`, append `"cost"`, then set the updated array |
| "show the current folder and context only" | `audio-hooks set statusline_settings.visible_segments '["cwd","context"]'` |
| "show context, branch, and model" | `audio-hooks set statusline_settings.visible_segments '["model","context","branch"]'` |
| "show everything in the status line" (reset) | `audio-hooks set statusline_settings.visible_segments '[]'` |
| "add webhook to the status line" | Read current with `audio-hooks get statusline_settings.visible_segments`, append `"webhook"` to the array, then set the updated array |
| "remove sounds count from status line" | Read current, remove `"sounds"`, set the updated array |
| "show session duration / how long I've been running" | add `"duration"` (it's on by default; if hidden, remove from `hidden_segments`) |
| "show cost per hour / burn rate" | `burn_rate` (on by default) |
| "show uncommitted git changes" | `git_dirty` (on by default) |
| "hide burn rate and api time but keep everything else" | `audio-hooks set statusline_settings.hidden_segments '["burn_rate","api_time"]'` |
| "what segments are available?" | `audio-hooks statusline segments` |

Examples of what the user sees after customisation:
```
# Only context:
█████░░░ Context: 65% ⚠️ /compact

# context + api_quota:
██████░░ API Quota: 85%  █████░░░ Context: 65% ⚠️ /compact

# model + branch + context:
[Opus]
🌿 main  █████░░░ Context: 65% ⚠️ /compact
```

**Codex status line (curation only — NOT rendered by echook)**

Unlike Claude Code, **Codex does not support a command-backed status line**. Codex renders only *fixed lists of built-in item IDs* configured under `[tui].status_line` and `[tui].terminal_title` in `~/.codex/config.toml` (command rendering is open feature request openai/codex#20140). echook therefore cannot inject custom segments into Codex — it can only **curate** those fixed lists so they stop truncating with an ellipsis (the common cause is a list with 20+ redundant items like `model-with-reasoning` + `model` + `reasoning`, or `context-remaining` + `context-used`, all crammed onto Codex's single line).

| User says | Run |
|---|---|
| "my codex status bar is cut off / shows `…`" | `audio-hooks statusline codex apply --preset balanced` (then restart Codex / `/statusline`) |
| "fix the codex tab/window title too / it's also crammed" | `audio-hooks statusline codex apply --target terminal_title --preset balanced` |
| "fix both the codex status line and title" | `audio-hooks statusline codex apply --target both --preset balanced` |
| "show me the current codex status line + title" | `audio-hooks statusline codex show` |
| "preview before applying" | `audio-hooks statusline codex preview --preset full --target both` |
| "give me a minimal / full codex status line" | `audio-hooks statusline codex apply --preset minimal` \| `--preset full` |
| "use these exact codex items" | `audio-hooks statusline codex apply --items model-with-reasoning,git-branch,context-remaining` |

`--target` selects which `[tui]` array to curate: `status_line` (default), `terminal_title`, or `both`. Presets — status line: `minimal` (4), `balanced` (8, recommended), `full` (14); terminal title: `minimal` (2), `balanced` (4), `full` (6). `apply` backs up `config.toml` first and surgically edits *only* the targeted array(s) (all other tables/comments preserved). `--items` overrides the preset for a single target.

**Notification settings (audio mode, detail level, per-hook overrides)**

Control how notifications are delivered — audio only, desktop notification only, both, or disabled entirely:

| User says | Run |
|---|---|
| "only play audio, no desktop notifications" | `audio-hooks set notification_settings.mode audio_only` |
| "only show desktop notifications, no audio" | `audio-hooks set notification_settings.mode notification_only` |
| "I want both audio and notifications" | `audio-hooks set notification_settings.mode audio_and_notification` |
| "disable all notifications" | `audio-hooks set notification_settings.mode disabled` |
| "make notifications more detailed" | `audio-hooks set notification_settings.detail_level verbose` |
| "minimal notifications" | `audio-hooks set notification_settings.detail_level minimal` |
| "only use desktop notification for the stop hook" | `audio-hooks set notification_settings.per_hook.stop notification_only` |

**Playback settings**

```bash
# Adjust debounce (minimum ms between same hook firing, default 500)
audio-hooks set playback_settings.debounce_ms 300
```

**Scope (what this plugin does — and does not — do)**

echook is two tracks only: **(1) audio + out-of-band notification** of editor lifecycle
events (sounds, desktop toasts, TTS spoken summaries, webhook fan-out, rate-limit sound
alerts) and **(2) the status line**. Anything that is neither a notification nor a status
line segment — breathing/wellness exercises, pomodoro/timers, gamification, opening URLs,
or running side-commands during a session — is out of scope by design. If the user asks
for such a feature, say it's intentionally not part of echook rather than trying to add it.
(The `focus_flow` / breathing feature was removed in v6.0.0.)

**Uninstall**

| User says | Run |
|---|---|
| "uninstall audio hooks" (plugin install) | Tell the user to type `/plugin uninstall audio-hooks@chanmeng-audio-hooks` in Claude Code |
| "uninstall audio hooks" (script install) | `bash scripts/uninstall.sh --yes` |
| "remove everything including config and audio files" | `bash scripts/uninstall.sh --yes --purge` |

**Read or write any config key**

For any setting not covered above, use the generic getter/setter:

```bash
audio-hooks get <dotted.key>           # read any config key
audio-hooks set <dotted.key> <value>   # write any config key (auto-coerces bool/int/JSON)
```

Run `audio-hooks manifest --schema` to see the full JSON Schema for all config keys.

**Test that audio is actually working**

```bash
audio-hooks test all                    # exercise every enabled hook
audio-hooks test stop                   # one specific hook
audio-hooks test session_start_resume   # a v5.0 matcher variant
```

**Troubleshooting "no sound" / "wrong sound"**

Always run `audio-hooks diagnose` first. It returns a JSON document listing the platform, audio player binary, the state of `~/.claude/settings.json` (including `disableAllHooks`), and any audio files missing for the active theme. Errors come with stable `code` enums and a `suggested_command` you should run.

Common error codes you may see:

| Code | What it means | Run |
|---|---|---|
| `SETTINGS_DISABLE_ALL_HOOKS` | The user (or their managed settings) has set `disableAllHooks: true` | Tell the user; Claude Code's config takes precedence |
| `AUDIO_PLAYER_NOT_FOUND` | No mpg123/ffplay/aplay on Linux, or PowerShell missing on Windows | `sudo apt install mpg123` (Linux) |
| `AUDIO_FILE_MISSING` | Audio files for the active theme are missing | `audio-hooks theme set default` to fall back |
| `WEBHOOK_HTTP_ERROR` / `WEBHOOK_TIMEOUT` | Webhook unreachable | `audio-hooks webhook test` and inspect the URL |

After running any fix, verify with `audio-hooks logs tail --n 20` to see the recent NDJSON event stream.

## Diagnosing the status line (v5.1.3+)

If a user complains that the **Context: X%** indicator looks wrong (e.g. "it jumped to 97% after I switched models"), this is almost always **expected math** — switching from a 1M-context variant to a 200K window keeps the user's tokens identical but shrinks the denominator 5×. Since v5.1.3 the indicator displays absolute counts (e.g. `Context: 83% (166K/200K)`) so the math is self-explanatory; point the user at that.

If they want to verify what Claude Code is actually piping to the status line script, set `CLAUDE_HOOKS_DEBUG=1` (or `true`/`yes`, case-insensitive) in their shell **before** launching Claude Code. The script then atomically dumps the most recent stdin JSON to `${state_dir}/statusline.last_input.json` after every refresh. Read it with:

```bash
cat "${CLAUDE_PLUGIN_DATA:-$TEMP/claude_audio_hooks_queue}/statusline.last_input.json"
```

Inspect `context_window.context_window_size` to confirm whether Claude Code refreshed it after the `/model` switch. Privacy note: the dump may contain workspace paths and the last assistant message — instruct the user to disable the env var when they finish diagnosing.

## Reading the logs

All log events are NDJSON (one JSON object per line) under `${CLAUDE_PLUGIN_DATA}/logs/events.ndjson`. Schema is `audio-hooks.v1`. Each event has `ts`, `level`, `hook`, `session_id`, `action`, and event-specific fields. Errors include `error.code`, `error.hint`, and `error.suggested_command`.

```bash
audio-hooks logs tail --n 50              # last 50 events
audio-hooks logs tail --n 100 --level error
audio-hooks logs clear
```

## Install for Cursor-only users (5.1.4+)

**Cursor IDE 3.2.16+ has its own "Cursor Hooks Service" that auto-bridges Claude Code plugins** ([cursor.com/docs/reference/third-party-hooks](https://cursor.com/docs/reference/third-party-hooks)). When the user has both Claude Code and Cursor on the same machine, Cursor calls our hook scripts on its own session events automatically — no extra install needed. Just run `audio-hooks status` and check `editor_targets.cursor.state`:

| State | Meaning |
|---|---|
| `bridged-via-claude-code` | Cursor is auto-bridging; everything works (8 of 10 hooks). |
| `native` | User ran `audio-hooks install --cursor`; Cursor reads `~/.cursor/hooks.json`. |
| `double-registered` | Both bridge AND native install present — fires audio twice. Run `audio-hooks uninstall --cursor` to fix. |
| `inactive` | Cursor isn't running this project at all. |

**For users who run Cursor without Claude Code**, install natively (clone the repo, then run `install --cursor` from inside it):

```bash
# One-time install
git clone https://github.com/ChanMeng666/echook ~/audio-hooks
python ~/audio-hooks/bin/audio-hooks install --cursor       # writes ~/.cursor/hooks.json

# Override DUPLICATE_BRIDGE if Claude Code is also installed (rare; double audio risk)
python ~/audio-hooks/bin/audio-hooks install --cursor --force

# Upgrade to a newer release — idempotent, preserves user_preferences.json
cd ~/audio-hooks && git pull && python bin/audio-hooks install --cursor

# Uninstall (preserves preferences for re-install)
python ~/audio-hooks/bin/audio-hooks uninstall --cursor
python ~/audio-hooks/bin/audio-hooks uninstall --cursor --purge   # also delete ~/.cursor/audio-hooks-data/
```

There is **no `audio-hooks upgrade --cursor` subcommand** — `audio-hooks upgrade` targets Claude Code's plugin cache and intentionally does not touch `~/.cursor/`. For Cursor-only installs, `git pull && install --cursor` is the upgrade recipe.

**Runtime guard for `--force`:** when the install marker records `duplicate_bridge_forced: true` and the runner is invoked under Cursor, the runtime emits a `duplicate_bridge_runtime_skip` warn event (`DUPLICATE_BRIDGE_RUNTIME_SKIP`) and returns 0 — Claude Code's bridge fires alone. Re-enable the native path by `audio-hooks uninstall --cursor` followed by re-install without `--force`.

**Bridge limitations** (these are Cursor's, not ours):

- `Notification` and `PermissionRequest` hooks have no Cursor equivalent — those audio cues never fire under Cursor.
- `Glob` / `WebFetch` / `WebSearch` matchers don't trigger under Cursor (no equivalent tools).
- The only Cursor-side opt-out is the **global** "Third-party skills" toggle in Cursor Settings — disabling it kills auto-bridging for *all* Claude Code plugins.

**Refreshing the cached plugin code Cursor's bridge invokes**: Cursor reads from `~/.claude/plugins/cache/chanmeng-audio-hooks/audio-hooks/<ver>/`, so the user must refresh that cache to pick up new releases. **Use `audio-hooks upgrade`** — it wraps `claude plugin update` (data-preserving) with a fallback to `uninstall --keep-data + install`, so the user's `user_preferences.json` survives. The legacy 5.1.3-era recipe (`/plugin uninstall + /plugin install`) destroys config and should not be recommended.

**Stdin field mapping**: Cursor's `cursor_version`, `conversation_id`, `final_status`, `reason`, `duration_ms`, `is_background_agent`, `workspace_roots`, `model`, `error_message` are surfaced under a `cursor: {...}` sub-object in webhook payloads. `user_email` is **redacted by default**; opt in via `audio-hooks set webhook_settings.include_user_email true`.

## Install for Codex users

**OpenAI's Codex does NOT auto-bridge Claude Code plugins**. Prefer the Codex plugin path when available; use the native registration at `$CODEX_HOME/hooks.json` (default `~/.codex/hooks.json`) as a fallback. Run `audio-hooks status` and check `editor_targets.codex.state`:

| State | Meaning |
|---|---|
| `active` | Installed and hooks are enabled. Audio fires correctly. |
| `active-but-hooks-disabled` | Installed but `[features].hooks = false` disables hooks. Remove that opt-out or set `hooks = true`, then tell the user to restart Codex. |
| `active-but-config-unreadable` | Installed but `~/.codex/config.toml` couldn't be parsed. Fix the TOML; hooks are enabled by default unless `[features].hooks = false` is present. |
| `inactive` | Not installed. Run `audio-hooks install --codex`. |

**Codex plugin install**:

```bash
codex plugin marketplace add ChanMeng666/echook
codex plugin add audio-hooks@chanmeng-audio-hooks
```

If Codex asks for it, tell the user to type `/reload-plugins`, then verify with `audio-hooks status`, `audio-hooks diagnose`, and `audio-hooks test all`.

**Native hooks.json install** (works whether or not they also have Claude Code):

```bash
git clone https://github.com/ChanMeng666/echook ~/audio-hooks
python ~/audio-hooks/bin/audio-hooks install --codex
```

Read the JSON output and act on it:

| `feature_flag_state` | What you do |
|---|---|
| `enabled_by_default` | Nothing more. Tell the user to restart Codex. |
| `explicitly_enabled` | Nothing more. Tell the user to restart Codex. |
| `explicitly_enabled_legacy` | Nothing more. Tell the user to restart Codex. |
| `disabled` / `disabled_legacy` | Use your Edit tool to remove the hooks opt-out or set `hooks = true` under `[features]`, then tell the user to restart Codex. |
| `parse_error` | Fix `~/.codex/config.toml`, then tell the user to restart Codex. |

**Why we don't auto-edit user-authored `config.toml`:** TOML round-trip with comment preservation is unreliable, and getting it wrong destroys the user's formatting. Letting the AI agent do the targeted edit via its Edit tool keeps the user's file safe.

**Upgrading the Codex install** (idempotent, preserves preferences):

```bash
cd ~/audio-hooks && git pull && python bin/audio-hooks install --codex
```

There is **no `audio-hooks upgrade --codex` subcommand** — `audio-hooks upgrade` targets Claude Code's plugin cache. For Codex installs, `git pull && install --codex` is the upgrade recipe.

**Uninstalling**:

```bash
python ~/audio-hooks/bin/audio-hooks uninstall --codex
python ~/audio-hooks/bin/audio-hooks uninstall --codex --purge   # also delete ~/.codex/audio-hooks-data/
```

The uninstall **never touches `~/.codex/config.toml`**.

**Codex limitations** (these are Codex's, not ours, per [developers.openai.com/codex/hooks](https://developers.openai.com/codex/hooks)):

- Codex supports 10 hook events: `SessionStart`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `UserPromptSubmit`, `SubagentStart`, `SubagentStop`, `Stop`. Other audio-hooks canonical events have no Codex equivalent and the runner no-ops them with a `skipped_no_codex_equivalent` debug NDJSON event.
- Codex plugin invokes with `PLUGIN_DATA`; native hooks.json install lands at `$CODEX_HOME/audio-hooks-data/` when `detect_invoker() == "codex"`.

**Stdin field mapping**: Codex's stdin uses **the same snake_case schema** as Claude Code (`session_id`, `tool_name`, `hook_event_name`, `transcript_path`, `turn_id`, `tool_use_id`, `tool_response`, `stop_hook_active`, `last_assistant_message`, `source`). No translation layer needed. Codex-specific fields (`turn_id`, `tool_use_id`, `permission_mode`, `tool_response`, `stop_hook_active`) are surfaced under a `codex: {...}` sub-object in webhook payloads (parallel to `cursor: {...}`).

## Reading the manifest (canonical introspection)

`audio-hooks manifest` returns a single JSON document with:

- `subcommands` — every CLI subcommand with arg list and description
- `hooks` — every hook with default state, audio file, description
- `config_keys` — every dotted config path
- `themes` — available audio themes
- `error_codes` — every stable error code with hint and suggested_command
- `env_vars` — every recognised environment variable
- `log_schema` and `webhook_schema` version strings

`audio-hooks manifest --schema` returns the JSON Schema for `user_preferences.json` (use this when you need to validate or generate config).

If any user request feels like it might involve a project capability you don't immediately recognise, **read the manifest first** instead of guessing. The manifest never lies; this skill might lag behind it after an update.

## What you should NOT do

- Do NOT edit `user_preferences.json` directly. Use `audio-hooks set` and the typed setters (`hooks enable`, `theme set`, `webhook set`, `tts set`, `rate-limits set`). They round-trip through validation and emit structured success/error JSON.
- Do NOT prompt the user with a [y/N]. Defaults are sensible. If the user gave you a specific instruction, just run the command.
- Do NOT try to install via `bash scripts/install-complete.sh` when the plugin install path is available — the plugin path is the recommended one.
- Do NOT modify the four hook files in `~/.claude/settings.json` by hand. Use `audio-hooks install --plugin` to register/deregister, or rely on `/plugin install` to do it.
- Do NOT translate any of the JSON output. The keys and values are stable interface contracts.

## Quick reference

```bash
audio-hooks manifest                       # canonical introspection — read this first
audio-hooks status                         # full project state snapshot
audio-hooks version                        # version + install detection
audio-hooks diagnose                       # system check + warnings + errors

audio-hooks hooks list                     # all 39 hooks
audio-hooks hooks enable <name>            # turn one on
audio-hooks hooks disable <name>           # turn one off
audio-hooks hooks enable-only <a> <b>      # exclusive enable

audio-hooks snooze [duration|off|status]   # mute (default 30m)
audio-hooks theme set <default|custom>     # switch theme
audio-hooks webhook                        # view current webhook config
audio-hooks webhook set --url <u> --format <f>
audio-hooks webhook test                   # POST a test payload
audio-hooks webhook clear                  # disable webhook
audio-hooks tts set --enabled true --speak-assistant-message true
audio-hooks rate-limits set --five-hour-thresholds 80,95

audio-hooks set notification_settings.mode <mode>            # audio_only | notification_only | audio_and_notification | disabled
audio-hooks set notification_settings.detail_level <level>   # minimal | standard | verbose (verbose = full sanitized summary)
audio-hooks set notification_settings.per_hook.<hook> <mode> # per-hook override
audio-hooks set playback_settings.debounce_ms <int>          # min ms between same hook (default 500)

audio-hooks statusline install             # register status line (restart Claude Code after)
audio-hooks statusline uninstall           # remove status line
audio-hooks statusline show                # check registration state
audio-hooks set statusline_settings.visible_segments '["context"]'               # show only context bar
audio-hooks set statusline_settings.visible_segments '["context","api_quota"]'   # show context + API quota
audio-hooks set statusline_settings.visible_segments '[]'                        # show all (default)

audio-hooks test <hook|all>                # play a hook end-to-end
audio-hooks logs tail --n 50               # recent NDJSON events
audio-hooks logs clear                     # truncate the event log

audio-hooks get <dotted.key>               # any config key
audio-hooks set <dotted.key> <value>       # any config key (auto-coerces)
audio-hooks update                         # show current version info
```
