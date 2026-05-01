# Troubleshooting

> **Version:** 5.1.5 | **Last Updated:** 2026-05-01

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
| `DUAL_INSTALL_DETECTED` | Both legacy script install and plugin install are active | `bash scripts/uninstall.sh --yes` (removes legacy, preserves config + audio) |
| `PROJECT_DIR_NOT_FOUND` | Could not locate project directory | Ensure the project files are present at the install location |
| `INTERACTIVE_SCRIPT` | You tried to invoke a human-only menu non-interactively (configure.sh / test-audio.sh) | Use `audio-hooks` instead |
| `INTERNAL_ERROR` | Unexpected internal error | `audio-hooks logs tail --level error --n 50` and report it as a GitHub issue |

## Symptoms

### Two sounds overlapping (voice + chime)

You have both the legacy script install and the plugin install active. Diagnose reports `DUAL_INSTALL_DETECTED`. Fix:

```bash
bash scripts/uninstall.sh --yes        # preserves config + audio by default
```

Then `/reload-plugins` inside Claude Code.

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

Issues: https://github.com/ChanMeng666/claude-code-audio-hooks/issues

## See also

- [README.md](../README.md) — public introduction with the full `audio-hooks` CLI reference
- [CLAUDE.md](../CLAUDE.md) — canonical AI-facing operating guide
- [docs/ARCHITECTURE.md](ARCHITECTURE.md) — developer-facing architecture deep dive
- `audio-hooks manifest` — live machine description of every subcommand and config key (always up to date)
