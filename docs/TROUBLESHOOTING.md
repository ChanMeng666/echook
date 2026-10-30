# Troubleshooting

Start with `audio-hooks diagnose`: it returns JSON with the platform, player, `settings.json` state (including `disableAllHooks`), missing audio, dual-install detection and coded findings, each with a `suggested_command`. Every code and its meaning: `audio-hooks manifest` -> `error_codes`. This page keeps only symptoms whose cause is not readable from that output. Logs: `audio-hooks logs tail --n 50 [--level error|debug]`. Debug-only events (`skipped_internal_subagent`, `stop_rerouted_to_stop_failure`, `skipped_no_*_equivalent`) are written only with `CLAUDE_HOOKS_DEBUG=1`.

## Audio fires after every message

Cause: `stop` maps to Claude Code's `Stop`, which fires at the end of every turn; the payload has no field distinguishing a final turn, so no setting makes it mean "work is done". Several open sessions each chime per turn. Fixes in order:

- Only when you must act: `audio-hooks hooks enable-only notification permission_request`. `idle_prompt` is the "waiting for you" signal; per Claude Code docs it arrives about 60 s after Claude finishes and only if you have not typed since, and 2.1.288 stopped it firing while background agents run (neither re-measured here). `permission_request` is the immediate cue.
- Quiet while work is in flight: `audio-hooks set filters.stop.skip_if_background_tasks_running true`. Reads `background_tasks` on the `Stop` payload; since 6.6.0 `pending` counts as well as `running`, and Claude Code's own maintenance tasks (`dream`, `auto-mode scan`, `memory import`) are ignored.
- Quiet while a `/loop` / `CronCreate` / `ScheduleWakeup` is pending: `audio-hooks set filters.stop.skip_if_session_crons_scheduled true`. Separate opt-in because a recurring cron stays in `session_crons` for the whole session and would silence every turn.
- Last resort: `audio-hooks set playback_settings.debounce_ms 60000`. Per subtype: `audio-hooks hooks list --variants`, `audio-hooks hooks disable notification_idle_prompt`.
- Since 6.7.0 filters run before debounce, so a filtered `stop` no longer opens the debounce window; a filtered event inside an open window logs `FILTERED`, not `DEBOUNCED`.

## No completion sound and no toast

Mirror image of the above, and almost never an upstream regression. `audio-hooks hooks list`: if `stop` is `false`, someone turned it off (often following the advice above). `NO_COMPLETION_SIGNAL` appears only when `notification` is off too. Restore: `audio-hooks hooks enable stop` and `audio-hooks set filters.stop.skip_if_background_tasks_running true`. Also check `notification_settings.mode` (`audio_only` means no toast by design) and `audio-hooks snooze status`.

## Windows: audio works, no desktop toast

Two causes, both fixed in 6.5.1.

1. A notification containing `"` produced no toast. The Windows branch escaped text for a POSIX shell (`\"`) and interpolated it into a PowerShell double-quoted string, where `\` is not an escape, so the script failed to parse. `permission_request` bodies embed tool commands, so quotes were routine; `$` and backticks were also silently deleted when it did parse. The same wrong escaper broke Windows/WSL TTS (`TTS_FAILED` was never emitted before 6.5.1).
2. The tray-balloon API is unreliable on Windows 11. 6.5.1 sends a WinRT toast (`Windows.UI.Notifications`) to a registered AppUserModelID, falling back to BurntToast, then to the balloon. The working backend is probed once and cached in `<data dir>/queue/notify_backend`; delete that file to force a re-probe.

The outcome is logged at `info` (`desktop_notification` with `backend`, e.g. `winrt`); a total failure logs `NOTIFICATION_FAILED`. Before 6.5.1 the path returned success unconditionally and logged only at debug, so a dead toast looked like a working one.

## Two sounds overlapping, or double bell

- Script install plus plugin install (`DUAL_INSTALL_DETECTED`): `audio-hooks uninstall`, then `/reload-plugins` in Claude Code. The script installer was removed in 7.0.0 (`install --scripts` returns `SCRIPT_INSTALL_REMOVED`), so this state only comes from an older release: before 6.6.0 a bare `install`, `install --help` or any unknown flag ran it, and a bare `uninstall` on Windows reported `ok: true` and removed nothing; check `removed_hook_entries` on a re-run. Removal is by content, not name: a file of yours in `~/.claude/hooks/` is left alone (`skipped_not_ours`). `UNINSTALL_INCOMPLETE` lists registrations using a home spelling it does not recognise in `unmatched_references`; read them, then `audio-hooks uninstall --remove-unmatched` (the loose rule can catch another tool's variable). Unparsed `settings.json` gives `CONFIG_READ_ERROR` with nothing changed. Close Claude Code first; there is no lock against a concurrent writer.
- Bell plus echook sound: Claude Code's own channel, `preferredNotifChannel` in `~/.claude/settings.json`, is anything other than `"notifications_disabled"` (worst with `terminal_bell` / `iterm2_with_bell`). Keep only echook: set it to `"notifications_disabled"`. Keep only Claude Code: `audio-hooks hooks disable notification`. They are independent systems, not a bug (`NATIVE_NOTIFICATIONS_ACTIVE`).
- Cursor's own completion chime (Settings -> General -> Notifications, off by default) plays alongside echook's `stop`. Turn it off, or `audio-hooks hooks disable stop`. Described from Cursor's forum and docs as of 2026-10; echook does not detect it.

## "A subagent finished" cue with no subagent started

Claude Code fires `SubagentStop` for its own internal agents (prompt suggestions, `/btw` side questions). Since 6.6.0 echook skips a `SubagentStop` whose `agent_type` is an empty string (Claude Code only; not Cursor, Codex or the legacy script install). In a session started with `--agent` internal agents carry that agent's name and still announce. Based on the hooks reference; no real internal-agent payload was captured. Otherwise `audio-hooks hooks disable subagent_stop`.

## Forked sessions silent (fixed in 6.4.1)

Claude Code 2.1.213 changed `SessionStart` to report `source: "fork"` where it previously reported `"resume"`. echook registered only `startup` / `resume` / `clear` / `compact`, so a fork matched no handler; nothing failed and nothing logged. 6.4.1 registers `fork`. The variant inherits `session_start`, which is off by default: `audio-hooks hooks enable session_start_fork`.

## No sound for a login or credentials failure (6.7.0)

Three matcher values had no handler before 6.7.0: `StopFailure` `cloud_credential_error` and `verification_required`, and `Notification` `auth_storage_failure`. The two `stop_failure_*` variants follow the `stop_failure` switch; if you had enumerated variants with `hooks enable-only`, migration wrote them explicitly `false`. `notification_auth_storage_failure` is off by default: `audio-hooks hooks enable notification_auth_storage_failure`. `model_refusal_fallback` (declared in the 2.1.288 binary) is deliberately not registered; no emitter was found.

## Config wiped after upgrading (installs at or below 5.1.4)

Upgrading from 5.1.4 or earlier: do not `/plugin uninstall` then `/plugin install`; that deletes `user_preferences.json`. Run `audio-hooks upgrade` instead (tries `claude plugin update`, which preserves data, then falls back to `uninstall --keep-data` + install; migration merges new template keys without overwriting yours). Recovery: `audio-hooks backup list`, `audio-hooks backup restore latest-external`. If you have no backup (straight from 5.1.4), reapply with `audio-hooks set` / `hooks enable-only` / `theme set` / `webhook set`. Since 5.1.5 each `audio-hooks set` snapshots to `~/.claude-audio-hooks-backups/<plugin_id>/<ts>.json`, kept outside the plugin data dir; rotation keeps the 20 newest.

5.1.4 also defaulted `subagent_stop`, `permission_denied` and `task_created` to `true`; 5.1.5 reverted that, but a config reinitialised at 5.1.4 persisted them. Fix: `audio-hooks hooks disable subagent_stop permission_denied task_created`.

`PRIOR_UPGRADE_INCOMPLETE`: a crashed upgrade left `~/.claude-audio-hooks-backups/.upgrade_in_progress.json`. Read it, confirm with `audio-hooks status`, then `audio-hooks upgrade --force` (or run its `recovery_command`).

## Rate-limit alert never fires

Claude Code reports `rate_limits` on stdin only for Claude.ai subscribers (Pro/Max), and only after the first API response in a session. The one-shot warning is keyed on `(window, threshold, resets_at)`.

## Status line

- Cut off with an ellipsis: current builds reflow. On Claude Code older than v2.1.153 `COLUMNS` is not exported and the width falls back to 80; pin it with `audio-hooks set statusline_settings.max_width <columns>` (`0` = auto). Re-running `audio-hooks statusline install` re-registers with `padding: 0`.
- `prompt_cache` and `remote` are opt-in (`statusline_settings.extra_segments`; ignored when `visible_segments` is non-empty; `hidden_segments` wins). `fast_mode` draws only while fast mode is on, `spend_limit` only behind a gateway that sets one, `prompt_cache` only after the first API response. Absent is not a fault. Segments are Claude Code only.
- Codex status bar truncated or sparse: Codex's status line is not command-backed; `audio-hooks statusline codex apply --preset balanced [--target both]` curates the `[tui]` lists. An item with no value (`git-branch` outside a repo, `five-hour-limit` before usage) is not drawn; that is Codex behaviour.
- Stays on the main agent while you view a subagent: run `audio-hooks statusline show` and follow `next_steps`, which catches a `refreshInterval` above 2. A session open during an upgrade also needs `/reload-plugins`, because the module loads with the plugin. It follows within about 3 seconds, never instantly: Claude Code does not re-run the script on a switch ([#96895](https://github.com/anthropics/claude-code/issues/96895)). Check that you are in the view: the input reads `Message @<agent>…`, and `Esc` does not leave it.
- Viewing a subagent shows `[▸ agent]` and no context: it has not finished its first response, or its transcript is not on disk (session persistence off). `~200K` is an estimate, used when its model differs from the main conversation's. Cost and quotas are the session's and do not change with the view.
- `echook (status unavailable)` for a moment on a first render: the status cache is still being built by a detached process, and the next refresh fills it in. If it stays, run `audio-hooks status`. `git_dirty` is missing from a first render for the same reason.
- Per-subagent status line blank: it renders only in the agent panel with subagents running, is read at startup (restart Claude Code), and is independent of the main status line. Claude Code logs `subagentStatusLine emitted non-JSON line` / `emitted invalid schema` if output drifted (one JSON object per line, each with `id` and `content`, `id` matching a stdin task).

## Desktop notification via `terminalSequence` never appears

Expected and not configurable (6.5.1+): Claude Code writes the escape only from a synchronous completion path and every echook handler is `async: true`. `TERMINAL_SEQUENCE_INERT`. Use `audio-hooks set notification_settings.mode audio_and_notification`. Still inert in the 2.1.288 binary; see EVENT_BEHAVIOR_NOTES.md. `message_display`, `elicitation` and `elicitation_result` are hard-excluded from any stdout writes because Claude Code reads hook stdout JSON on them to change behaviour (it would replace visible output or answer an MCP prompt).

## Cursor

- No audio: `audio-hooks status` -> `editor_targets.cursor.state` (`bridged-via-claude-code`, `native`, `inactive`, `double-registered`). `Notification` and `PermissionRequest` are silent under Cursor by design (`skipped_no_cursor_equivalent`).
- Twice per event (`double-registered`): `audio-hooks uninstall --cursor`. `install --cursor` aborts with `DUPLICATE_BRIDGE` when the Claude Code plugin is installed; with `--force` the runtime guard `DUPLICATE_BRIDGE_RUNTIME_SKIP` (5.1.6+) suppresses the native path.
- `Template is not valid JSON after substitution`: Windows paths before 5.1.6 were not JSON-escaped; upgrade.
- Wrong theme or default settings: Cursor does not inject `CLAUDE_PLUGIN_DATA`; 5.1.4+ resolves the data dir by fallback chain and `session_start` emits the env. Restart Cursor to refresh the bridge; `audio-hooks upgrade` refreshes cached plugin code. 5.1.3 and earlier fell back to bundled defaults.
- Finished sound on a cancelled or errored turn (6.8.0): Cursor documents `stop` with `status` of `completed`, `aborted` or `error`; echook reads it only via opt-in keys. `audio-hooks set filters.stop.skip_if_aborted true`. For errors: `audio-hooks set filters.stop.error_as_stop_failure true` and `audio-hooks hooks enable stop_failure` (this also enables API-error alerts in Claude Code). With the key on and `stop_failure` off the errored turn is silent, with no fallback to `stop` (log: `stop_failure` / `DISABLED`). Both rest on Cursor documentation and were not confirmed against a live payload; if neither `stop_rerouted_to_stop_failure` nor `FILTERED` appears under debug, the payload carried no `status` (unknown whether a bridged Claude Code `Stop` does).

## Codex

- Silent: `audio-hooks status` -> `editor_targets.codex`. `active-but-hooks-disabled` means `[features].hooks = false` in `~/.codex/config.toml`; `active-but-config-unreadable` means TOML syntax error (hooks are on by default).
- Silent on every event, with ``failed to parse hooks config … unknown field `_comment` `` (or `failed to parse plugin hooks config`) in Codex's output: the file was written by echook 7.0.0 or earlier, and Codex refuses the whole file. `audio-hooks diagnose` reports `CODEX_HOOKS_FILE_REJECTED`. Re-run `audio-hooks install --codex`; for the plugin route, update the plugin (`codex plugin add audio-hooks@chanmeng-audio-hooks`).
- `skipping async hook … async hooks are not supported yet`: Codex older than 0.148.0. Re-run `audio-hooks install --codex`, which writes synchronous handlers for that version, or update Codex. The plugin route keeps only `Stop` on such a version.
- `install --codex` succeeded but Codex never fires: a managed deployment sets `allow_managed_hooks_only` in `requirements.toml`, so `$CODEX_HOME/hooks.json` is ignored with no error (`CODEX_MANAGED_HOOKS_ONLY`, names the file). Ask the owner to permit user hooks, or install via `codex plugin marketplace add ChanMeng666/echook`; plugin hooks load by a different route.
- Hooks fire, no audio: confirm `invoker: "codex"` on each line of `~/.codex/audio-hooks-data/logs/events.ndjson`. If it reads `unknown` or `claude-code`, `--invoker codex` is not reaching the runner; re-run `audio-hooks install --codex`.

## Other

- Plugin will not install: `claude plugin validate plugins/audio-hooks`.
- `pretooluse` / `posttooluse` silent: off by default (fire on every tool call).
- Webhook: `audio-hooks webhook test`; it fires from a detached subprocess, so failures land in the log (`WEBHOOK_TIMEOUT`, `WEBHOOK_HTTP_ERROR`), not as visible errors.
- `PREFS_SCHEMA_STALE`: read-only commands never migrate; the next hook event or state-changing command does, or run `audio-hooks migrate`.
- `STALE_PLUGIN_CACHE`: a re-materialised marketplace deletes the path live sessions are pinned to and their hooks stop firing silently (anthropics/claude-code#90135); reload plugins or restart Claude Code.
- `WINDOWS_NO_GIT_BASH`: Claude Code runs command hooks through bash and refuses them when Git Bash is missing ("requires bash but Git Bash was not found").

## Reporting bugs

Attach `audio-hooks diagnose`, `audio-hooks logs tail --n 100 --level error`, `audio-hooks version`, the platform and repro steps to https://github.com/ChanMeng666/echook/issues.
