# Troubleshooting

> **Version:** 6.0.0 | **Last Updated:** 2026-06-23

The troubleshooting story is one command:

```text
> run audio-hooks diagnose
```

It returns a JSON document listing the platform, audio player binary, the state of `~/.claude/settings.json` (including `disableAllHooks`), any audio files missing for the active theme, dual-install detection, and explicit error codes. **Every error includes a `suggested_command` you can run next.** You don't have to read prose troubleshooting guides — the binary tells you what to fix.

## Common error codes

| Code | Meaning | Fix |
|---|---|---|
| `AUDIO_FILE_MISSING` | An audio file referenced by the active theme is missing | `audio-hooks diagnose` reports which files; restore them or `audio-hooks theme set default` |
| `AUDIO_PLAYER_NOT_FOUND` | No audio player binary in PATH | Linux: `sudo apt install mpg123`. macOS: `afplay` is built-in. Windows: ensure PowerShell is available |
| `AUDIO_PLAY_FAILED` | Player exited with an error | `audio-hooks test <hook>` to reproduce; check `audio-hooks logs tail --level error` |
| `INVALID_CONFIG` | `user_preferences.json` is missing or malformed | `audio-hooks manifest --schema` for the schema; or just run any `audio-hooks set` command — it auto-initialises from the default template |
| `WEBHOOK_HTTP_ERROR` / `WEBHOOK_TIMEOUT` | Webhook unreachable | `audio-hooks webhook test`; check the URL and network |
| `TTS_FAILED` | TTS engine failed or missing | `audio-hooks tts set --enabled false` or install: macOS `say` (built-in), Linux `apt install espeak`, Windows SAPI (built-in) |
| `SETTINGS_DISABLE_ALL_HOOKS` | `~/.claude/settings.json` has `"disableAllHooks": true` | Edit the settings file to remove or set `false` |
| `DUAL_INSTALL_DETECTED` | Both the script install and the plugin install are active | `audio-hooks uninstall` (removes the script install, preserves config + audio) |
| `PROJECT_DIR_NOT_FOUND` | Could not locate project directory | Ensure the project files are present at the install location |
| `DUPLICATE_BRIDGE` | `install --cursor` aborted because Claude Code's plugin already auto-bridges to Cursor (would cause double audio) | `audio-hooks uninstall --plugin` first, **or** pass `--force` to `install --cursor` if you want both paths active (rare) |
| `DUPLICATE_BRIDGE_RUNTIME_SKIP` | Runtime skipped a Cursor invocation because `install_marker.json` records `duplicate_bridge_forced: true` (you ran `install --cursor --force` over an active bridge) | `audio-hooks uninstall --cursor` to remove the native install — Claude Code's bridge then handles Cursor normally |
| `CURSOR_NOT_FOUND` | `install --cursor` couldn't find `~/.cursor/` | Install Cursor IDE first, then re-run |
| `CODEX_HOOKS_DISABLED` | Codex hooks are installed but `[features].hooks = false` is set in `~/.codex/config.toml`. Codex won't invoke any hooks. | Remove the opt-out or set `hooks = true` under `[features]`, then restart Codex. Surfaced by `audio-hooks status` as `editor_targets.codex.warning`. |
| `CODEX_CONFIG_PARSE_ERROR` | Codex hooks are installed but `~/.codex/config.toml` could not be parsed. | Fix the TOML syntax. Hooks are enabled by default unless `[features].hooks = false` is present. |
| `INTERNAL_ERROR` | Unexpected internal error | `audio-hooks logs tail --level error --n 50` and report it as a GitHub issue |

## Symptoms

### Two sounds overlapping (voice + chime)

You have both the script install and the plugin install active. Diagnose reports `DUAL_INSTALL_DETECTED`. Fix:

```bash
audio-hooks uninstall        # removes the script install; preserves config + audio
```

Then `/reload-plugins` inside Claude Code. (Or just say *"audio-hooks is playing double sounds, fix it."*)

### No sound at all

```text
> run audio-hooks diagnose
```

Look for any error in the output. The most common causes:

1. **Hook is disabled.** Many hooks are off by default (`pretooluse`, `posttooluse`, `cwd_changed`, `file_changed`, `session_start`, etc.). Run `audio-hooks hooks list` to see the current state. Enable with `audio-hooks hooks enable <name>`.

2. **Snoozed.** Run `audio-hooks snooze status`. If active, run `audio-hooks snooze off`.

3. **`disableAllHooks: true`** in `~/.claude/settings.json`. Diagnose reports `SETTINGS_DISABLE_ALL_HOOKS`.

4. **Audio files missing** for the active theme. Diagnose reports `AUDIO_FILE_MISSING`. Switch themes (`audio-hooks theme set default`) or restore the files.

5. **Audio player missing** (Linux). Diagnose reports `AUDIO_PLAYER_NOT_FOUND`. `sudo apt install mpg123`.

### Plugin won't install

```bash
claude plugin validate plugins/audio-hooks
```

This catches manifest schema errors. v5.1.5 has been verified clean on Claude Code v2.1.101+.

### My config got wiped after upgrading the plugin

You ran `/plugin uninstall` then `/plugin install` (the 5.1.4 manual cache-refresh recipe), which deleted your `user_preferences.json`. Two things to do:

1. **Restore from backup** if you had at least one prior save in 5.1.5+:
   ```bash
   audio-hooks backup list                       # show available timestamps
   audio-hooks backup restore latest-external    # restores newest off-data-dir backup
   ```

2. **Use `audio-hooks upgrade` next time** — it wraps `claude plugin update` (data-preserving) with a fallback to `uninstall --keep-data + install`, so your config survives:
   ```bash
   audio-hooks upgrade --check-only              # see current vs target version
   audio-hooks upgrade                           # do it
   ```

If you have no backups (e.g. you upgraded straight from 5.1.4), reapply your customizations via `audio-hooks set` / `audio-hooks hooks enable-only` / `audio-hooks theme set` / `audio-hooks webhook set`. Going forward, every `audio-hooks set ...` call snapshots the prior state to `~/.claude-audio-hooks-backups/<plugin_id>/<ts>.json` (kept outside the plugin data dir so `claude plugin uninstall` can't erase them; rotation keeps the 20 newest).

### Suddenly hearing 3× more audio after a 5.1.4 install

5.1.4 flipped `enabled_hooks.subagent_stop`, `permission_denied`, and `task_created` to `true` by default. 5.1.5 reverts those defaults to `false`, but if your `user_preferences.json` was reinitialised at 5.1.4 (e.g. via a `claude plugin uninstall` without `--keep-data`), you ended up with the three keys explicitly persisted as `true`. Migration to 5.1.5 preserves user values, so they stay enabled. Disable them with one command:

```bash
audio-hooks hooks disable subagent_stop permission_denied task_created
```

### `audio-hooks upgrade` aborted with `PRIOR_UPGRADE_INCOMPLETE`

A previous upgrade crashed before completing. The marker at `~/.claude-audio-hooks-backups/.upgrade_in_progress.json` records what happened. Read it, confirm the plugin state with `claude plugin list --json` or `audio-hooks status`, then retry with `--force`:

```bash
audio-hooks upgrade --force
```

If the marker shows `recovery_command`, run that command directly.

### `audio-hooks` command not found in Bash

The bash wrapper at `bin/audio-hooks` probes `python3` / `python` / `py` and skips broken stubs (notably the Microsoft Store python3.exe stub on Windows). If all three fail, you'll see `PYTHON_NOT_FOUND` JSON. Install Python 3.6+.

If the wrapper is found but exits non-zero, run it directly with the Python interpreter to see the error:

```bash
python bin/audio-hooks.py status
```

### `pretooluse` / `posttooluse` audio missing

By design — these are disabled by default because they fire on every tool execution including Read, Glob, Grep (very noisy). Enable explicitly:

```bash
audio-hooks hooks enable pretooluse
audio-hooks hooks enable posttooluse
```

### Rate-limit alert never fires

The alert requires Claude Code to report `rate_limits` in stdin. This only happens for **Claude.ai subscribers (Pro/Max)** and only **after the first API response in a session**. Confirm the field is being sent: `audio-hooks logs tail --n 50` and look for any event with a `rate_limit_alert` action.

To force-test the alert with a synthetic stdin payload:

```bash
echo '{"session_id":"test","rate_limits":{"five_hour":{"used_percentage":85,"resets_at":9999999999}}}' | python hooks/hook_runner.py stop
```

Should fire the warning audio once, then be debounced for that `(window, threshold, resets_at)` tuple.

### Context: 97% (or any sudden jump) right after switching models

Not a bug. The percentage Claude Code calculates is `current_tokens / context_window_size`. Switching from a 1M-context variant (e.g. `claude-opus-4-7[1m]`) to a 200K-window model (e.g. default `claude-sonnet-4-6`) keeps your accumulated tokens identical but **shrinks the denominator 5×** — so 17% on Opus 1M legitimately becomes ~83% on Sonnet 200K. Since v5.1.3 the status line displays the underlying numbers explicitly, e.g. `Context: 83% (166K/200K) 🛑 /compact`, so the math is self-evident.

If you want to verify what Claude Code is actually piping to the status line:

```bash
# Linux/macOS
export CLAUDE_HOOKS_DEBUG=1 && claude
# Windows PowerShell
$env:CLAUDE_HOOKS_DEBUG = "1"; claude
```

After any status line refresh, the latest stdin JSON is dumped to `${state_dir}/statusline.last_input.json`. Check `context_window.context_window_size` to see what window Claude Code thinks it's using.

> ⚠️ The dump may contain workspace paths and the last assistant message — disable `CLAUDE_HOOKS_DEBUG` when not actively diagnosing.

### Status line is cut off with an ellipsis (`Webho…`, `+743/-…`)

Since v6.1.0 each line auto-reflows into as many rows as your terminal width needs, so this should not happen. If it still does, one of these applies:

- **Old Claude Code (< v2.1.153).** Those versions don't export the `COLUMNS` env var, so the script can't detect your width and falls back to assuming 80 columns. Either update Claude Code, or pin your real width: `audio-hooks set statusline_settings.max_width <columns>` (e.g. `120`).
- **Your terminal reports a width wider than what's usable** (unusual padding, a wrapping prompt, etc.). Pin it lower: `audio-hooks set statusline_settings.max_width <columns>`. Set it back to auto with `audio-hooks set statusline_settings.max_width 0`.
- **Too many rows instead?** That's the no-truncation trade-off — trim segments to taste, e.g. `audio-hooks set statusline_settings.visible_segments '["model","cwd","context","weekly_quota"]'`.
- **Re-running `audio-hooks statusline install`** re-registers the line with `padding: 0` (full terminal width), which maximises usable space.

### Cursor IDE: no audio at all

Run `audio-hooks status` and look at `editor_targets.cursor.state`:

| State | Meaning | Fix |
|---|---|---|
| `bridged-via-claude-code` | Cursor is auto-bridging the Claude Code plugin (8 of 10 hooks). | Working as designed — confirm Cursor Settings → "Third-party skills" is enabled. |
| `native` | You ran `audio-hooks install --cursor`; Cursor reads `~/.cursor/hooks.json`. | Restart Cursor, then `audio-hooks test all`. |
| `inactive` | No integration. Either Cursor's "Third-party skills" is off, or no hooks file exists. | Either run `audio-hooks install --cursor`, or install the Claude Code plugin and toggle Cursor's setting on. |
| `double-registered` | Both bridge AND native install present — see "fires twice" below. | `audio-hooks uninstall --cursor`. |

If the state looks right but audio still doesn't fire, check `audio-hooks logs tail --n 50` for `skipped_no_cursor_equivalent` events — `Notification` and `PermissionRequest` are deliberately silent under Cursor (no equivalent events; this is per [cursor.com/docs/reference/third-party-hooks](https://cursor.com/docs/reference/third-party-hooks)).

### Cursor IDE: audio fires twice on every event

You have both Cursor's auto-bridge AND a native install firing. Confirm with `audio-hooks status` — it reports `editor_targets.cursor.state: "double-registered"`. Fix:

```bash
audio-hooks uninstall --cursor          # removes the native install; bridge stays
```

Or if you specifically need the native path (rare — `--force`-installed): uninstall the Claude Code plugin instead:

```bash
audio-hooks uninstall --plugin
```

If you intentionally want both paths active despite the double-fire, the runtime since 5.1.6 will detect `install_marker.json` records `duplicate_bridge_forced: true` and silently skip the native firing under Cursor (logs `DUPLICATE_BRIDGE_RUNTIME_SKIP` warn-level event) so audio still plays exactly once. Verify with `audio-hooks logs tail --level warn`.

### `audio-hooks install --cursor` fails with `INTERNAL_ERROR: Template is not valid JSON after substitution`

You're on a project version older than 5.1.6 on Windows. Pre-5.1.6, paths like `D:\github\echook\hooks\hook_runner.py` were substituted directly into the JSON template, and the backslashes were interpreted as invalid JSON escapes (`\g`, `\h`, etc).

Fix: upgrade to 5.1.6 or later. Either `git pull` if you cloned the repo, or `audio-hooks upgrade` for plugin installs.

### `audio-hooks install --cursor` aborts with `DUPLICATE_BRIDGE`

Claude Code's plugin is already installed, so Cursor is already auto-bridging this project. Adding a native install on top would fire every event twice. Either:

- **Recommended:** Don't run native install. The auto-bridge already covers Cursor. Verify with `audio-hooks status`.
- **If you really want both paths:** pass `--force`. The runtime will then runtime-skip the native firing path (5.1.6+), so audio still plays exactly once via Claude Code's bridge.

### Cursor is playing the wrong audio theme even after I changed it

Cursor reads cached plugin code at `~/.claude/plugins/cache/<id>/<ver>/`. After changing themes via `audio-hooks theme set`, Cursor should pick up the new setting on its next session start (the `session_start` hook emits `{"env": {"CLAUDE_PLUGIN_DATA": "<path>"}}` to stdout, which Cursor propagates to subsequent hooks in the same session per its own docs).

If it doesn't:

1. Restart Cursor (this re-reads `~/.claude/plugins/installed_plugins.json` and refreshes the bridge).
2. If the issue persists, refresh the cached plugin code with `audio-hooks upgrade` — it wraps `claude plugin update` (data-preserving) with a fallback to `uninstall --keep-data + install`.

If you're running 5.1.3 or earlier, the runner had a known bug where it fell back to bundled defaults when `CLAUDE_PLUGIN_DATA` wasn't injected (which Cursor does not inject). 5.1.4+ fixed this via the 6-level path-resolution chain in `hooks/user_preferences.py:_resolve_data_dir()`.

### Codex CLI: no audio at all

Run `audio-hooks status` and look at `editor_targets.codex`:

| State | Fix |
|---|---|
| `inactive` | The native install isn't in place. Run `audio-hooks install --codex`. |
| `active-but-hooks-disabled` | The install is there but `[features].hooks = false` disables Codex hooks. Remove that opt-out or set `hooks = true`, then restart Codex. |
| `active-but-config-unreadable` | `config.toml` may have a syntax error. Read it and fix the TOML; hooks are enabled by default unless `[features].hooks = false` is present. |
| `active` | Audio should be working. If it isn't, check `audio-hooks logs tail --level error` and run `audio-hooks diagnose` for player/file issues. |

If you've never installed Codex itself, `~/.codex/` won't exist — install Codex from [openai/codex](https://github.com/openai/codex) first, then re-run `audio-hooks install --codex`.

### Codex CLI: hooks fire but no audio plays

This means Codex IS calling the runner but the runner's playback path is failing. Likely causes:

1. **Audio player not in PATH.** Run `audio-hooks diagnose` — it'll report `AUDIO_PLAYER_NOT_FOUND` if so. Linux: `sudo apt install mpg123`. macOS: `afplay` is built-in. Windows: ensure PowerShell is available.
2. **Wrong data dir.** If audio plays under Claude Code but not Codex, the runner may be reading the wrong `user_preferences.json`. Run a synthetic Codex hook with debug logging:
   ```bash
   echo '{}' | CLAUDE_HOOKS_DEBUG=1 python ~/audio-hooks/hooks/hook_runner.py stop --invoker codex
   tail -20 ~/.codex/audio-hooks-data/logs/events.ndjson
   ```
   Confirm `invoker: "codex"` appears on every event line. If you see `invoker: "unknown"` or `"claude-code"`, the `--invoker codex` flag isn't reaching the runner — re-run `audio-hooks install --codex` to refresh the template substitutions.

### Codex CLI: how do I see which events are firing?

Codex doesn't surface hook activity in its UI by default. Tail the audio-hooks NDJSON log:

```bash
tail -f ~/.codex/audio-hooks-data/logs/events.ndjson
```

Each line has `invoker: "codex"`, `hook: "<canonical_name>"`, and `level`. Filter the unsupported-event no-ops with:

```bash
audio-hooks logs tail --n 50 | grep -v skipped_no_codex_equivalent
```

### Webhook not receiving events

```bash
audio-hooks webhook                    # show current config (URL is redacted)
audio-hooks webhook test               # POST a test payload
audio-hooks logs tail --level error    # check for WEBHOOK_TIMEOUT or WEBHOOK_HTTP_ERROR
```

The webhook fires asynchronously via subprocess so the parent hook process exits immediately. Failures land in the NDJSON log, not as visible errors.

## Reading the NDJSON event log

```bash
audio-hooks logs tail --n 50              # last 50 events
audio-hooks logs tail --n 100 --level error
audio-hooks logs clear                    # truncate
```

Events are at `${CLAUDE_PLUGIN_DATA}/logs/events.ndjson` (plugin install) or `<temp>/claude_audio_hooks_queue/logs/` (script install). Schema: `audio-hooks.v1`. Log rotation: 5 MB cap, 3 files kept.

## Reporting bugs

Before opening a GitHub issue, please attach:

1. `audio-hooks diagnose` JSON output
2. `audio-hooks logs tail --n 100 --level error` output
3. `audio-hooks version` output
4. Your platform (`uname -a` on Unix; PowerShell version on Windows)
5. Steps to reproduce

Issues: https://github.com/ChanMeng666/echook/issues

## See also

- [README.md](../README.md) — public introduction with the full `audio-hooks` CLI reference
- [CLAUDE.md](../CLAUDE.md) — canonical AI-facing operating guide
- [docs/ARCHITECTURE.md](ARCHITECTURE.md) — developer-facing architecture deep dive
- `audio-hooks manifest` — live machine description of every subcommand and config key (always up to date)
