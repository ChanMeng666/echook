# Troubleshooting

> **Version:** 6.8.0 | **Last Updated:** 2026-10-07

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
| `INVALID_CONFIG` | `user_preferences.json` is missing or malformed | `audio-hooks manifest --schema` for the schema; or run any state-changing `audio-hooks` command such as `audio-hooks set` — it auto-initialises from the default template (the reporting commands, `status` and `diagnose` among them, do not create it since 6.7.0) |
| `WEBHOOK_HTTP_ERROR` / `WEBHOOK_TIMEOUT` | Webhook unreachable | `audio-hooks webhook test`; check the URL and network |
| `TTS_FAILED` | TTS engine failed or missing. **Before 6.5.1 this was never emitted**, and on Windows/WSL any spoken text containing a `"` produced an unparseable PowerShell script, so TTS went silent with no error | `audio-hooks tts set --enabled false` or install: macOS `say` (built-in), Linux `apt install espeak`, Windows SAPI (built-in) |
| `SETTINGS_DISABLE_ALL_HOOKS` | `~/.claude/settings.json` has `"disableAllHooks": true` | Edit the settings file to remove or set `false` |
| `DUAL_INSTALL_DETECTED` | Both the script install and the plugin install are active — or, since 6.6.0, `install --scripts` was refused because the plugin is installed | Both active: `audio-hooks uninstall` (removes the script install on every platform; backs up first; preserves config + audio). `install --scripts` refused: that command is the wrong direction — to switch to the script install deliberately run `audio-hooks uninstall --plugin` first (it lists a `--keep-data` command), or pass `--force` |
| `INVALID_USAGE` | *(6.6.0)* An unknown subcommand, flag or stray positional on a state-changing subcommand (`set`, `hooks`, `theme set`, `snooze`, `webhook`, `tts set`, `rate-limits set`, `logs clear`, `backup`, `statusline …`, `upgrade`, `uninstall`); `install` without exactly one mode flag; `--purge` with `uninstall --scripts` / `--plugin`; `--remove-unmatched` outside scripts mode; a help-like token (`--help`, `-h`, `-?`, `/?`) as the value of `set`; a non-finite rate-limit threshold. **Nothing was changed** — before 6.6.0 the same input ran the script installer and reported `ok: true` | Follow the error's `suggested_command`: `audio-hooks install --help` lists the four modes (`--plugin`, `--scripts`, `--cursor`, `--codex`) |
| `UNINSTALL_INCOMPLETE` | *(6.6.0)* `audio-hooks uninstall` removed what it could, but a file could not be deleted or a registration uses a home spelling it does not recognise | Read `unmatched_references` and `next_steps` in the output, then `audio-hooks uninstall --remove-unmatched` — see "Two sounds overlapping" below |
| `PROJECT_DIR_NOT_FOUND` | Could not locate project directory | Ensure the project files are present at the install location |
| `DUPLICATE_BRIDGE` | `install --cursor` aborted because Claude Code's plugin already auto-bridges to Cursor (would cause double audio) | `audio-hooks uninstall --plugin` first, **or** pass `--force` to `install --cursor` if you want both paths active (rare) |
| `DUPLICATE_BRIDGE_RUNTIME_SKIP` | Runtime skipped a Cursor invocation because `install_marker.json` records `duplicate_bridge_forced: true` (you ran `install --cursor --force` over an active bridge) | `audio-hooks uninstall --cursor` to remove the native install — Claude Code's bridge then handles Cursor normally |
| `CURSOR_NOT_FOUND` | `install --cursor` couldn't find `~/.cursor/` | Install Cursor IDE first, then re-run |
| `CODEX_HOOKS_DISABLED` | Codex hooks are installed but `[features].hooks = false` is set in `~/.codex/config.toml`. Codex won't invoke any hooks. | Remove the opt-out or set `hooks = true` under `[features]`, then restart Codex. Surfaced by `audio-hooks status` as `editor_targets.codex.warning`. |
| `CODEX_CONFIG_PARSE_ERROR` | Codex hooks are installed but `~/.codex/config.toml` could not be parsed. | Fix the TOML syntax. Hooks are enabled by default unless `[features].hooks = false` is present. |
| `NATIVE_NOTIFICATIONS_ACTIVE` | *(warning)* Claude Code's own `preferredNotifChannel` is set to something other than `notifications_disabled`, so it signals the same events echook does — expect a double bell or duplicate toast | Keep both, or set `"preferredNotifChannel": "notifications_disabled"` in `~/.claude/settings.json` to hear only echook, or `audio-hooks hooks disable notification` to hear only Claude Code |
| `CODEX_MANAGED_HOOKS_ONLY` | *(warning)* A managed Codex config sets `allow_managed_hooks_only`, so `$CODEX_HOME/hooks.json` is silently ignored — `install --codex` reports success and then never fires | Ask the owner of the named `requirements.toml` to permit user hooks, or install via the Codex plugin marketplace instead |
| `NO_COMPLETION_SIGNAL` | *(warning)* None of `stop`, `subagent_stop` or `notification` is enabled, so nothing can tell you a turn finished. echook is healthy and stays silent for the thing most people install it for | `audio-hooks hooks enable stop && audio-hooks set filters.stop.skip_if_background_tasks_running true` — or enable `notification` and rely on its `idle_prompt` variant |
| `NOTIFICATION_FAILED` | Every desktop-notification backend for this platform failed. The NDJSON line names which one was tried and why it failed | Windows: check that notifications are on for the machine and not suppressed by Do Not Disturb. Linux: `sudo apt install libnotify-bin` for `notify-send`. The audio track is unaffected |
| `PREFS_SCHEMA_STALE` | *(warning)* `user_preferences.json` is stamped at an older version than the install, or still carries a key this version removed — so keys added since are absent and silently defaulted | The next hook event migrates it, as does any state-changing `audio-hooks` command. Read-only commands (`status`, `diagnose`, `get`, `hooks list`, …) deliberately do not. To migrate now run `audio-hooks migrate` (v6.7: no flags, idempotent, never creates a missing file; it reports `changed`, `from_version`, `to_version`, `added[]`, `removed[]`, `stale[]` and `backup`) |
| `STALE_PLUGIN_CACHE` | *(warning)* `installed_plugins.json` records a version or install path that is not the code now running. Harmless for a `directory`-source marketplace; **not** harmless when the recorded path is gone | Reload plugins or restart Claude Code to re-pin. See [#90135](https://github.com/anthropics/claude-code/issues/90135) — a re-materialised marketplace deletes the path live sessions are pinned to and their plugin hooks stop firing silently |
| `WINDOWS_NO_GIT_BASH` | *(warning)* Windows with no Git Bash on PATH. Claude Code runs command hooks through bash by default and refuses them outright when it is missing, so every handler fails at once | Install [Git for Windows](https://git-scm.com/downloads/win). Claude Code reports this itself as *"requires bash but Git Bash was not found"* |
| `TERMINAL_SEQUENCE_INERT` | *(warning)* `notification_settings.terminal_sequence.enabled` is true, but Claude Code only writes a hook's `terminalSequence` from a **synchronous** completion path and every echook handler is registered `async` — so no escape is ever emitted | Use the desktop-notification channel instead: `audio-hooks set notification_settings.mode audio_and_notification`. Background in [EVENT_BEHAVIOR_NOTES.md](EVENT_BEHAVIOR_NOTES.md) |
| `INTERNAL_ERROR` | Unexpected internal error | `audio-hooks logs tail --level error --n 50` and report it as a GitHub issue |

## Symptoms

### A chime after every single message ("too frequent", "it never shuts up")

This is the `stop` hook, and it is working as designed — the design is just not what most people assume. `stop` maps to Claude Code's `Stop` event, which fires at the **end of every turn**, not when a task completes. Claude Code's payload carries **no field** distinguishing a final turn from an intermediate one, so no amount of configuration can make `stop` mean "the work is done". If you have several sessions open, each one chimes per turn independently and the effect compounds.

Pick the fix that matches what you actually want:

**"Only make noise when you need something from me."** Drop `stop` entirely and keep the events that fire when you must act:

```bash
audio-hooks hooks enable-only notification permission_request
```

`notification`'s `idle_prompt` subtype is the genuine "Claude is waiting for your input" signal — it fires when the session is actually parked on you, not on every turn boundary. Per Claude Code's documentation it arrives about 60 seconds after Claude finishes and only if you have not typed since, so expect it to be late; `permission_request` is the immediate cue for "I need approval". Claude Code 2.1.288 also stopped it firing while background agents are still running. (Neither the delay nor that fix was re-measured here.)

**"Keep the completion sound, but not while there's still work running."** v6.4 reads the `background_tasks` array Claude Code puts on the `Stop` payload and stays quiet until nothing is in flight:

```bash
audio-hooks set filters.stop.skip_if_background_tasks_running true
```

On a session driving ten teammates this is the difference between a chime per turn and a chime when the batch finishes. Since 6.7.0 an event the filter discards no longer starts the debounce window (filters run before debounce); before, a skipped `stop` could still suppress the next genuine one for up to `debounce_ms` although nothing had played. In `audio-hooks logs tail`, a filtered event inside an open window now reads `FILTERED`, not `DEBOUNCED`. Since 6.6.0 a task still `pending` counts as in flight as well as one `running`, and Claude Code's own maintenance tasks (`dream`, `auto-mode scan`, `memory import`) are ignored, so they cannot hold the chime back forever.

**"Keep the completion sound, but not while a `/loop` or scheduled wakeup is pending."** A session with a pending `CronCreate` / `ScheduleWakeup` / `/loop` entry carries it in `session_crons` on the `Stop` payload. That is a separate opt-in, because a recurring cron stays there for the whole session and would silence every turn for someone who only asked about running work:

```bash
audio-hooks set filters.stop.skip_if_session_crons_scheduled true
```

**"I just want fewer of them."** Blunt but effective, and worth trying only after the two above:

```bash
audio-hooks set playback_settings.debounce_ms 60000
```

Finer control is available per subtype — e.g. keep permission prompts but drop idle ones:

```bash
audio-hooks hooks list --variants
audio-hooks hooks disable notification_idle_prompt
```

### Two sounds overlapping (voice + chime)

You have both the script install and the plugin install active. Diagnose reports `DUAL_INSTALL_DETECTED`. Since 6.6.0 `audio-hooks install --scripts` refuses to run on top of the plugin (it needs `--force`), and `audio-hooks install` with no mode flag no longer runs the script installer at all — before 6.6.0 a bare `install`, `install --help` or any unrecognised flag did, and that is how a machine that already had the plugin ended up here. Fix:

```bash
audio-hooks uninstall        # removes the script install natively; backs up first; preserves config + audio
```

Then `/reload-plugins` inside Claude Code. (Or just say *"audio-hooks is playing double sounds, fix it."*)

`audio-hooks uninstall` (6.6.0) works the same on Windows, macOS and Linux and does not need `scripts/uninstall.sh`, which the plugin layout does not ship. It copies `settings.json`, `settings.local.json` and everything it will delete to `~/.claude/backups/audio-hooks-uninstall-<timestamp>/` first, then removes echook's own hook and permission entries and only files that are echook's. The JSON it prints has `removed_hook_entries`, `removed_permissions`, `removed_files`, `backup_dir`, `skipped_not_ours`, `unmatched_references`, `left_in_place` and `incomplete`. Restart Claude Code afterwards. Before 6.6.0 a bare `uninstall` on Windows returned `ok: true` with a hint and removed nothing, so a Windows machine told to run it may still have the script install — run it again and check `removed_hook_entries`.

- **`"nothing_to_remove": true`** — there was no script install (or it was already removed); no backup is created. The command is idempotent.
- **`CONFIG_READ_ERROR`** — `settings.json` or `settings.local.json` could not be parsed. Nothing was changed; fix or move the file and re-run. (A file that parses but cannot be written back out is an `INTERNAL_ERROR`, also with nothing changed.)
- **`UNINSTALL_INCOMPLETE` (`ok: false`, exit 1).** Everything that could be removed was; two things can remain. Either a file could not be deleted (something holds it open — close it and re-run `audio-hooks uninstall`), or a registration refers to an echook script through a spelling of the home directory the strict rule does not recognise: `$env:USERPROFILE\…`, `%HOMEDRIVE%%HOMEPATH%\…`, an MSYS `/c/Users/…` path, an 8.3 short path, `"$HOME"/…`, `;~/…`. Those are listed in `unmatched_references` (`{file, value, scripts}`), and the script they name is *kept*, together with the modules it needs (`invoker.py`, `user_preferences.py`, `.project_path`, and `shared/` libraries for a wrapper), so the surviving hook still runs. **Read the list, then run `audio-hooks uninstall --remove-unmatched`** (scripts mode only), which also strips those entries and removes the scripts they pointed at. Read it first because the loose rule can catch another tool's variable — `$XDG_CONFIG_HOME/.claude/hooks/hook_runner.py`, `%ANDROID_HOME%/…`.
- **Why a file of yours was not removed (`skipped_not_ours`).** Ownership is decided by content as well as name: a file in `~/.claude/hooks/` is removed only if it carries echook's marker. A `stop_hook.sh` or a `shared/` directory of your own is left alone, listed with a reason, and its registration is kept; `shared/` loses only echook's four libraries and is removed only if that empties it. `.project_path` has no marker and is judged by a heuristic (a single path line beside echook's runner, or naming an echook checkout). A registration is removed only when the file it names is echook's or no longer exists.
- **Accepted limits.** These are neither stripped nor reported: `$(echo ~)/…`, variables whose name contains neither HOME nor USERPROFILE, `~user/…`, relative prefixes, and a `cd … && ./hook_runner.py` form. A compound command of yours that mentions an echook script is removed as a whole entry (it is in the backup). A settings file that had removals is rewritten in a normalised format (indentation, LF, no BOM). There is no lock against a concurrent writer, so close Claude Code first.
- **Source checkout:** `bash scripts/uninstall.sh` is a thin wrapper that runs `audio-hooks uninstall --scripts` (and forwards `--remove-unmatched`). Its `--purge` additionally removes only that checkout's `config/user_preferences.json` and `audio/default/*`, after backing them up to `~/.claude/backups/audio-hooks-purge-<timestamp>`.

### "A subagent finished" cue when I started no subagent

Claude Code fires `SubagentStop` for its own internal agents too (prompt suggestions, `/btw` side questions), not only for subagents your session spawned. Since 6.6.0 echook skips a `SubagentStop` whose `agent_type` is an empty string — Claude Code's documented marker for those events — and records it as a debug-level NDJSON event, `skipped_internal_subagent` (written only with `CLAUDE_HOOKS_DEBUG=1`; read it with `audio-hooks logs tail --level debug`). Two limits: it applies under the Claude Code plugin only (not Cursor, Codex, or the legacy script install), and in a session started with `--agent` the internal agents carry that agent's name, so they cannot be told apart from real subagents and still announce. If the cue still fires, `audio-hooks hooks disable subagent_stop` silences it outright.

### No sound when a task finishes, and no desktop popup

The single most common shape of "echook stopped working", and it is almost never
an upstream regression. Run `audio-hooks diagnose` first — as of 6.5.1 it names
both halves of this directly.

**No completion sound.** Check `audio-hooks hooks list` for `stop`. If it is
`false`, someone turned it off — very likely you, or an agent acting on the
advice in this file, because `stop` fires at the end of *every* turn and gets
noisy fast. Diagnose reports `NO_COMPLETION_SIGNAL` only when `notification` is
off too; with `notification` on you still have `idle_prompt`, which is the real
"waiting for you" cue.

The fix that keeps `stop` bearable:

```text
> audio-hooks hooks enable stop
> audio-hooks set filters.stop.skip_if_background_tasks_running true
```

The filter reads the `background_tasks` array Claude Code puts in the `Stop`
payload, so the chime is suppressed while subagents and teammates are still
running and lands on the turn where the batch actually settles. Nothing in the
payload marks a turn as final, so this is the closest available proxy — see
[EVENT_BEHAVIOR_NOTES.md](EVENT_BEHAVIOR_NOTES.md).

**No desktop popup, but audio works.** Two causes, both fixed in 6.5.1 and worth
knowing if you are on an older version:

1. **Any notification containing a `"` produced no toast on Windows.** The
   Windows branch escaped the text for a POSIX shell (`\"`) and then interpolated
   it into a PowerShell double-quoted string, where `\` is not an escape
   character — so the generated script failed to parse and nothing appeared.
   `permission_request` bodies embed tool commands, so quotes were routine. `$`
   and backticks were silently deleted from the text even when it did parse.
2. **The tray-balloon API is unreliable on Windows 11.** 6.5.1 sends a real WinRT
   toast (`Windows.UI.Notifications`) addressed to a registered AppUserModelID,
   falling back to BurntToast if you happen to have it, and to the old balloon
   last. The working backend is probed once and cached in
   `<data dir>/queue/notify_backend`; delete that file to force a re-probe.

Either way, the outcome is now in the event log at `info` level, so you can see
which backend ran without turning on debug:

```text
> audio-hooks logs tail --n 20
{"action": "desktop_notification", "backend": "winrt", "status": "DISPATCHED", ...}
```

A failure logs `NOTIFICATION_FAILED` with the reason instead. Before 6.5.1 this
path returned success unconditionally and logged only at debug level, which is
why a completely dead toast looked identical to a working one.

Also check `notification_settings.mode` — `audio_only` means no toast by design.
`audio-hooks set notification_settings.mode audio_and_notification`.

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

This catches manifest schema errors. v6.4.0 has been verified clean on Claude Code v2.1.215.

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

### Status line is cut off with an ellipsis (`Webho…`, `Theme: Chim…`, `+743/-…`)

Since v6.1.0 each line auto-reflows into as many rows as your terminal width needs, and v6.3.1 widened the safety margin (4→8) so emoji-dense rows landing on the budget boundary wrap instead of clipping. So this should not happen on a current build. If it still does, one of these applies:

- **Old Claude Code (< v2.1.153).** Those versions don't export the `COLUMNS` env var, so the script can't detect your width and falls back to assuming 80 columns. Either update Claude Code, or pin your real width: `audio-hooks set statusline_settings.max_width <columns>` (e.g. `120`).
- **Your terminal reports a width wider than what's usable** (unusual padding, a wrapping prompt, etc.). Pin it lower: `audio-hooks set statusline_settings.max_width <columns>`. Set it back to auto with `audio-hooks set statusline_settings.max_width 0`.
- **Too many rows instead?** That's the no-truncation trade-off — trim segments to taste, e.g. `audio-hooks set statusline_settings.hidden_segments '["burn_rate","api_time","duration"]'` (drop a few) or `audio-hooks set statusline_settings.visible_segments '["model","cwd","context","weekly_quota"]'` (whitelist only these).
- **Re-running `audio-hooks statusline install`** re-registers the line with `padding: 0` (full terminal width), which maximises usable space.

### A status-line segment is missing: `prompt_cache`, `remote`, `spend_limit`, `fast_mode` (v6.7.0)

- **`prompt_cache` and `remote` are opt-in** and never appear by default (so upgrading changed no existing status line). Check `audio-hooks statusline segments` for `"default": false`, then `audio-hooks set statusline_settings.extra_segments '["prompt_cache"]'`. `hidden_segments` still wins, and `extra_segments` is ignored when `visible_segments` is non-empty (name the segment there instead).
- **`fast_mode` draws only while fast mode is on, and `spend_limit` only behind a Claude apps gateway that sets a spend limit.** Nothing on a plain subscription session is correct, not a fault. `prompt_cache` is also absent until the session's first API response, and when caching is off.
- Segments are Claude Code only; Codex cannot render them. Restart Claude Code after `statusline install`, as for any status line change.

### Codex status bar is truncated, redundant, or shows too little

Codex's status line is **not** command-backed — echook can't render it, only **curate** the fixed `[tui].status_line` / `terminal_title` item lists in `~/.codex/config.toml`. Common cases:

- **Truncated with `…` / duplicated items** (e.g. `gpt-5.5 xhigh … gpt-5.5 · xhigh`, `Context 100% left · Context 0% used`): too many redundant IDs on Codex's single line. Fix: `audio-hooks statusline codex apply --preset balanced` (add `--target both` to also fix the tab title). Restart Codex or run `/statusline`.
- **A configured item shows nothing:** an item with no value isn't drawn — e.g. `git-branch`/`branch-changes` outside a git repo, `five-hour-limit` before any usage. That's Codex behaviour, not a bug. A sparse bar usually means a fresh session / non-repo cwd. Use `--preset full` for the maximum set; some IDs still fill in only once their data exists.
- **Inspect / preview first:** `audio-hooks statusline codex show` (current arrays + overflow flag), `audio-hooks statusline codex preview --preset full --target both` (no write). `apply` always backs up `config.toml` first. See [STATUS_LINE.md](STATUS_LINE.md#codex-status-line-curation-only).

### Cursor IDE: no audio at all

Run `audio-hooks status` and look at `editor_targets.cursor.state`:

| State | Meaning | Fix |
|---|---|---|
| `bridged-via-claude-code` | Cursor is auto-bridging the Claude Code plugin (8 coarse events: see `supported_editors.cursor.bridged_events_subset`). | Working as designed — confirm Cursor Settings → "Third-party skills" is enabled. |
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

### Cursor plays the finished sound when I cancel a turn, or when the agent errored (v6.8.0)

Cursor documents its native `stop` payload as carrying `status`: `completed`, `aborted` or `error`. echook reads it only through two opt-in keys, so by default all three sound the same.

**A turn I cancelled.** A `stop` whose status is `aborted` is then skipped:

```bash
audio-hooks set filters.stop.skip_if_aborted true
```

**A turn that ended in an error.** Run both:

```bash
audio-hooks set filters.stop.error_as_stop_failure true
audio-hooks hooks enable stop_failure
```

A Cursor `stop` whose status is `error` is then delivered as `stop_failure` (its sound; "Agent stopped with an error" in the toast and in speech). Enabling `stop_failure` alone does nothing under Cursor. Note that `stop_failure` also turns on API-error alerts in Claude Code.

**After setting `error_as_stop_failure`, errored Cursor turns are silent.** `stop_failure` is off. The key converts the event; the `stop_failure` switch decides whether it plays, and there is no fallback to the `stop` sound. Check `audio-hooks hooks list`, then `audio-hooks hooks enable stop_failure`. The log shows `stop_failure` with status `DISABLED` for such a turn.

**Neither key seems to do anything.** Both rest on Cursor's documentation and have not been confirmed against a live payload. With `CLAUDE_HOOKS_DEBUG=1`, a re-route logs `stop_rerouted_to_stop_failure` and an aborted turn logs `FILTERED` (`audio-hooks logs tail --level debug`); if neither appears, the payload carried no `status` (it is not known whether a `Stop` bridged from the Claude Code plugin does).

### Cursor IDE: I hear Cursor's own chime as well as echook's sound

Cursor has a built-in completion sound (Cursor Settings → General → Notifications, off by default) that plays one chime when the agent finishes or needs attention. It is independent of hooks, so with it on you hear it alongside echook's `stop` sound. Keep whichever you want: turn Cursor's setting off, or `audio-hooks hooks disable stop`. If one undifferentiated chime is all you need in Cursor, the built-in setting is enough on its own; echook adds per-event sounds, spoken summaries, webhooks, filters and snooze. (The Cursor setting is described from its forum and documentation as of 2026-10; echook does not detect it.)

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

- [README.md](../README.md) — public introduction (features, value, mermaid diagrams)
- [docs/CLI_REFERENCE.md](CLI_REFERENCE.md) — the full `audio-hooks` CLI + config + error-code reference
- [AGENTS.md](../AGENTS.md) — canonical AI-facing operating guide
- [docs/ARCHITECTURE.md](ARCHITECTURE.md) — developer-facing architecture deep dive
- `audio-hooks manifest` — live machine description of every subcommand and config key (always up to date)

### No sound for a login or credentials failure (v6.7.0)

Three matcher values Claude Code sends had no handler before 6.7.0, so each was a permanent silent event: `StopFailure` `cloud_credential_error` and `verification_required`, and `Notification` `auth_storage_failure` ("Claude Code login needs attention: credentials could not be saved"). 6.7.0 registers all three as variants with their own sound in both themes.

- **`stop_failure_cloud_credential_error` / `stop_failure_verification_required`** follow the `stop_failure` switch like their eleven siblings: silent unless you enabled `stop_failure`. If you enabled the whole parent (`hooks enable stop_failure`), you will now hear two error types that were silent before. If you had instead enumerated variants (`hooks enable-only <variant>`: parent on, every existing sibling an explicit true/false), the migration wrote the two new ones explicitly `false`, so nothing new became audible; `hooks enable <variant>` turns either on. (A config whose enumeration predates an earlier variant it never answered keeps inheriting.) To have only these, not every API error: `audio-hooks hooks enable-only stop_failure_cloud_credential_error stop_failure_verification_required`.
- **`notification_auth_storage_failure` is off by default**, because `notification` is on by default and a new variant must not make noise on every existing install. A login-storage failure is therefore silent until you run `audio-hooks hooks enable notification_auth_storage_failure`. Confirm with `audio-hooks hooks list --variants`.
- `model_refusal_fallback`, another `StopFailure` value declared in the Claude Code 2.1.288 binary, is deliberately not registered: no emitter for it was found.

### Forked sessions made no sound (fixed in v6.4.1)

**Symptom.** Everything chimes normally, except sessions started as a fork — those are completely silent from the first event.

**Cause.** Claude Code 2.1.213 changed `SessionStart` to report `source: "fork"` where it previously reported `"resume"`. echook registered `startup`/`resume`/`clear`/`compact` only, so a forked session matched no handler and the registration was simply never invoked. Nothing failed and nothing logged.

**Fix.** Upgrade to v6.4.1 or later, which registers the `fork` matcher. Confirm with `audio-hooks hooks list --variants | grep session_start_fork`. Note the variant inherits `session_start`'s default, which is **off** — enable it with `audio-hooks hooks enable session_start_fork` if you want a sound on fork specifically.

### I hear two notifications for the same event (bell plus echook's sound)

**Cause.** Claude Code has its own notification channel, controlled by `preferredNotifChannel` in `~/.claude/settings.json`. Anything other than `"notifications_disabled"` means Claude Code signals the same moments echook does — most visibly with `terminal_bell` or `iterm2_with_bell`.

`audio-hooks diagnose` reports this as the warning `NATIVE_NOTIFICATIONS_ACTIVE`.

**Fix — pick whichever channel you actually want:**
- keep only echook: set `"preferredNotifChannel": "notifications_disabled"` in `~/.claude/settings.json`
- keep only Claude Code's: `audio-hooks hooks disable notification`
- keep both: nothing to do, the warning is informational

This is not a bug in either tool; they are independent notification systems that happen to agree on when something interesting happened.

### `install --codex` succeeded but Codex is permanently silent

**Cause.** A managed Codex deployment can set `allow_managed_hooks_only` in `requirements.toml`. When it does, `$CODEX_HOME/hooks.json` — exactly what `install --codex` writes — is **ignored with no error at all**. The install reports success and echook never fires, which is indistinguishable from a bug in echook unless you know to look.

`audio-hooks diagnose` reports this as `CODEX_MANAGED_HOOKS_ONLY` and names the file.

**Fix.** Either ask whoever owns the managed config to permit user hooks, or install through the Codex plugin marketplace instead of the native path (`codex plugin marketplace add ChanMeng666/echook`), since plugin-bundled hooks load by a different route.

### Desktop notifications (`terminalSequence`) do not appear

> **As of 6.5.1 this is expected and cannot be configured away.** `terminalSequence` is inert on every echook event: Claude Code emits the escape only from a synchronous hook-completion path, and all 67 handlers are registered `async: true`. `diagnose` reports `TERMINAL_SEQUENCE_INERT`. For a real desktop toast use `notification_settings.mode = audio_and_notification`, which on Windows sends a WinRT toast. Details and the cost of a proper fix are in [EVENT_BEHAVIOR_NOTES.md](EVENT_BEHAVIOR_NOTES.md). Reading the 2.1.288 binary shows no change, so this still holds there; the checklist below only becomes relevant if a later Claude Code release delivers `terminalSequence` from async hooks.


Check these in order:

1. **Is it on?** It ships off. `audio-hooks get notification_settings.terminal_sequence.enabled` → must be `true`.
2. **Are you on Claude Code?** Cursor and Codex have no equivalent and the runner never emits there by design. Use a webhook (`webhook set --url … --format ntfy`) to reach a phone from those editors.
3. **Does your terminal honour the OSC?** `osc9` (the default) works in iTerm2, kitty, WezTerm and Ghostty. If yours ignores it, try `--style osc777`, or fall back to `title` or `bell`, which almost everything supports.
4. **Is the event on the allowlist?** Only events that are safe to write stdout on can emit. `message_display`, `elicitation` and `elicitation_result` are hard-excluded because Claude Code reads a hook's stdout JSON on those events to change what it *does* — `MessageDisplay` would replace Claude's visible output, and the elicitation pair would answer an MCP prompt on your behalf. There is deliberately no setting that overrides this.
5. **Is your OS notification centre suppressing it?** A focus/do-not-disturb mode swallows OSC 9 toasts silently. `--style bell` is a quick way to tell whether the sequence is arriving at all.

### The per-subagent status line shows nothing

1. `audio-hooks statusline subagent show` — confirm `registered: true`.
2. **Restart Claude Code.** The setting is read at startup.
3. **It only renders in the agent panel**, with subagents actually running. A session with no tasks has no rows to draw — this is not a failure.
4. It is **independent of the main status line**: `statusline install` does not install it, and vice versa.
5. If rows are still blank, check Claude Code's own logs for `subagentStatusLine emitted non-JSON line` or `emitted invalid schema`. Those mean the renderer's output drifted from the required contract — one JSON object per line, each with both `id` and `content`, `id` matching a task from stdin.

