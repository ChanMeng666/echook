# CLI & Configuration Reference

> **Agents:** this page is a static, human-readable mirror. The live source of truth is **`audio-hooks manifest`** — it prints every subcommand, hook, config key, error code, and env var as JSON, always current. Prefer it over this page.

Humans rarely need this — you operate echook by [talking to your AI agent](NATURAL_LANGUAGE_CONTROL.md). It's here for the curious and for offline reference.

## `audio-hooks` CLI

Single Python binary on PATH. JSON output, no prompts, no spinners. `--help` / `-h` (and `-?`, `/?`, `--help=<x>`) is side-effect-free for every subcommand (v6.6.0): it prints `{ok, command, usage, note}` and exits 0 without running the handler; `install`, `uninstall` and `upgrade` return their own usage JSON. For `set`, a help-like token in *first* position prints usage, but anywhere after the key it is `INVALID_USAGE` with nothing written — it is never stored as a value. Before v6.6.0 `upgrade --help` ran a real upgrade and `tts set --help`, `webhook set --help`, `rate-limits set --help` and `set <key> --help` wrote config. **State-changing subcommands reject arguments they do not define** — unknown flags and stray positionals return `INVALID_USAGE` and change nothing; a valued flag with no value is an error; `--flag=value` forms are not supported. This covers `set` (extra tokens after the value; the value itself may start with `-`), `hooks enable|disable|enable-only`, `theme set`, `snooze`, `webhook set|clear|test`, `tts set`, `rate-limits set`, `logs clear`, `backup restore|prune`, `statusline install|uninstall`, `statusline subagent install|uninstall` and `statusline codex preview|apply`. `tts set` and `rate-limits set` accept dash or underscore flag spellings. Display-only calls (`tts`, `tts set`, `rate-limits`, `rate-limits set`, `webhook set` with no flags) no longer rewrite the config and its `.bak`. `test` also rejects unknown arguments (v6.7; `test stop --dry-run` used to be accepted and then play the sound).

**Reporting commands are read-only (v6.7).** `status`, `diagnose`, `get`, `hooks list`, `theme` / `theme list`, `snooze status`, flagless `webhook` / `tts` / `rate-limits`, `statusline` (show, `segments`, `subagent show`, `codex show`, `codex preview`), `logs tail`, `backup list|show`, `manifest`, `version`, `update` and every `--help` path leave the home and data directories untouched: no auto-created `user_preferences.json`, no migration, no `logs/` or `queue/` directory. A stale preferences file is migrated by the next hook event, by a state-changing command, or on request by `audio-hooks migrate`, not by `status` (before v6.7, `status` created the file in a fresh home, and running the CLI from a checkout whose template version differed re-stamped the stored file).

| Subcommand | Purpose |
|---|---|
| `audio-hooks manifest` | Canonical introspection — every subcommand, hook, config key, error code, env var |
| `audio-hooks manifest --schema` | JSON Schema for `user_preferences.json` |
| `audio-hooks status` | Full state snapshot |
| `audio-hooks version` | Version + install mode detection |
| `audio-hooks get <dotted.key>` | Read any config key |
| `audio-hooks set <dotted.key> <value>` | Write any config key (auto-coerces) |
| `audio-hooks hooks list` | All 39 hooks with current state (`--variants` adds the 47 matcher variants) |
| `audio-hooks hooks list --variants` | Adds a `variants` key listing the 47 matcher variants; `hooks` stays at 39 rows |
| `audio-hooks hooks enable/disable <name> [<name> …]` | Toggle one or more hooks **or matcher variants** (`notification_idle_prompt`, `stop_failure_rate_limit`, …). All names are applied, all-or-nothing (before v6.6.0 only the first name was applied while success was reported) |
| `audio-hooks hooks enable-only <a> <b>` | Exclusive enable. Accepts variants; a named variant keeps its parent enabled, since a disabled parent silences all its variants |
| `audio-hooks theme list/set <name>` | Audio theme |
| `audio-hooks snooze [duration]/off/status` | Mute hooks (default 30m) |
| `audio-hooks webhook/set/clear/test` | Webhook config + test |
| `audio-hooks tts set ...` | TTS config |
| `audio-hooks rate-limits set ...` | Rate-limit alert thresholds. Thresholds are always stored as a list (v6.6.0: `--five-hour-thresholds 90` used to store a bare integer, which made the hook runner raise on every event carrying rate-limit data; the runner now also tolerates a scalar or malformed value). An infinite or non-finite threshold is `INVALID_USAGE` |
| `audio-hooks test <hook\|all>` | Smoke-test hooks |
| `audio-hooks diagnose` | System check |
| `audio-hooks logs tail/clear` | NDJSON event log |
| `audio-hooks install <--plugin\|--scripts\|--cursor\|--codex> [--force]` | Non-interactive install. **A mode flag is required — there is no default (v6.6.0).** A bare `install`, an unknown argument, or two modes returns `INVALID_USAGE` and changes nothing. `--plugin` prints `claude plugin marketplace add ChanMeng666/echook --json`, `claude plugin install audio-hooks@chanmeng-audio-hooks --json`, a request to type `/reload-plugins`, and `audio-hooks status`, and changes nothing itself. `--scripts` is the legacy installer: refused with `DUAL_INSTALL_DETECTED` while the plugin is installed (orphaned cache directories, marked `.orphaned_at`, do not count), unless `--force`. `--cursor` is refused with `DUPLICATE_BRIDGE` while the plugin is installed, unless `--force` |
| `audio-hooks uninstall [--plugin\|--scripts\|--cursor\|--codex] [--purge] [--remove-unmatched]` | Non-interactive uninstall. Bare `uninstall` (= `--scripts`) **removes the legacy script install natively on every platform** (v6.6.0; before that it returned `ok: true` with a hint and did nothing on Windows). It backs up to `~/.claude/backups/audio-hooks-uninstall-<ts>/`, removes echook's own entries from `~/.claude/settings.json` and `settings.local.json`, and deletes only files that are echook's. **Ownership is by content as well as name:** a file in `~/.claude/hooks/` is removed only if it carries echook's marker (`.project_path`, which has none, is judged by a heuristic); a same-named file without the marker — your own `stop_hook.sh`, your own `shared/` directory — is left alone and listed in `skipped_not_ours` with a reason, and its registration is kept. The result has `mode`, `incomplete`, `removed_hook_entries`, `removed_permissions`, `removed_files[]`, `backup_dir`, `skipped_not_ours[]` (`{path, reason}`), `unmatched_references[]` (`{file, value, scripts}`), `left_in_place[]`, and optionally `nothing_to_remove` / `next_steps[]`. Nothing to remove → `ok: true`, `nothing_to_remove`, no backup; an unparseable or unserialisable settings file → an error with nothing changed. **Incomplete** (a file could not be deleted, or a registration spells the home directory in a form the strict rule does not recognise — `$env:USERPROFILE\…`, `%HOMEDRIVE%%HOMEPATH%\…`, an MSYS `/c/Users/…` path, an 8.3 short path, `"$HOME"/…`, `;~/…`) → `ok: false`, `UNINSTALL_INCOMPLETE`, exit 1; those entries are listed in `unmatched_references` and the script they name is kept (with the modules it needs) so the surviving hook still runs. **`--remove-unmatched`** (scripts mode only; `INVALID_USAGE` otherwise) also strips every listed entry and the scripts they pointed at — read the list first, since the loose rule can catch another tool's variable (`$XDG_CONFIG_HOME/.claude/hooks/hook_runner.py`, `%ANDROID_HOME%/…`). Not stripped and not reported: `$(echo ~)/…`, variables whose name contains neither HOME nor USERPROFILE, `~user/…`, relative prefixes, `cd … && ./hook_runner.py`; a compound command of yours that mentions an echook script is removed as a whole entry; a settings file with removals is rewritten normalised (indentation, LF, no BOM); no lock against a concurrent writer. `--plugin` prints `claude plugin uninstall audio-hooks@chanmeng-audio-hooks --keep-data --json` (`--keep-data` preserves your preferences and backups). `--cursor` / `--codex` remove audio-hooks-managed entries from the corresponding hooks.json; `--purge` (only valid with those two) also deletes the `audio-hooks-data` directory. Unknown arguments are `INVALID_USAGE`. For a source checkout, `bash scripts/uninstall.sh` is a thin wrapper around `uninstall --scripts` (it also forwards `--remove-unmatched`); its own `--purge` removes only that checkout's `config/user_preferences.json` and `audio/default/*`, after backing them up to `~/.claude/backups/audio-hooks-purge-<ts>` |
| `audio-hooks migrate` | State-changing, no flags, idempotent (v6.7). Brings the stored `user_preferences.json` up to this version's template: new keys added, dropped keys removed, a sibling `.bak` kept. Never creates the file or its directory (an absent file is reported as `exists: false`). Returns `ok`, `config_path`, `exists`, `changed`, `from_version`, `to_version`, `added[]`, `removed[]`, `stale[]`, `backup`. The remedy for `PREFS_SCHEMA_STALE` |
| `audio-hooks upgrade [--check-only] [--force]` | Refresh the plugin code without touching preferences. Unknown arguments are rejected (v6.6.0) |
| `audio-hooks statusline show/install/uninstall` | Claude Code status line registration |
| `audio-hooks statusline segments` | List all 33 Claude Code status line segments (name, line, source field, conditional, `default` — `false` for the opt-in `prompt_cache` and `remote`) |
| `audio-hooks statusline subagent show\|install\|uninstall` | Manage `subagentStatusLine` — one rendered row per subagent in Claude Code's agent panel. Separate settings key from the main status line, and a different output contract (NDJSON keyed by task id). |
| `audio-hooks statusline codex show/preview/apply` | Curate Codex `[tui].status_line` and/or `terminal_title` (`--preset minimal\|balanced\|full`, `--items a,b,c`, `--target status_line\|terminal_title\|both`). Codex accepts only fixed item IDs — echook curates, it cannot render custom text |

## Configuration Keys

| Key | Type | Default | Effect |
|---|---|---|---|
| `audio_theme` | `default` \| `custom` | `default` | Voice recordings vs chimes |
| `enabled_hooks.<hook>` | bool | varies | Per-hook toggle |
| `enabled_hooks.<variant>` | bool | inherits parent | Per-variant toggle (v6.4). Same flat namespace as hooks. See *Variant gating* below |
| `playback_settings.debounce_ms` | int | 500 | Min ms between same hook firing |
| `filters.<hook>.<field>` | string (regex) | — | Skip unless the stdin field matches |
| `filters.<hook>.<field>_exclude` | string (regex) | — | Skip when the stdin field matches |
| `filters.<hook>.skip_if_background_tasks_running` | bool | `false` | v6.4. Stay silent while any `background_tasks` entry on the `Stop` / `SubagentStop` payload is in flight — teammates, subagents, background shells. The practical fix for "a chime after every message". Since v6.6.0 `status: pending` counts as well as `running` (Claude Code's own in-flight predicate), and entries whose `type` is one of Claude Code's maintenance labels — `dream`, `auto-mode scan`, `memory import` — are ignored |
| `filters.<hook>.skip_if_session_crons_scheduled` | bool | `false` | v6.6.0, opt-in. Stay silent while the payload's `session_crons` array (`CronCreate` / `ScheduleWakeup` / `/loop` wakeups) is non-empty. Separate from the key above on purpose: a recurring cron stays in the array for the whole session, so it would silence every turn for someone who only asked about running work |
| `filters.<hook>.skip_if_aborted` | bool | `false` | v6.8.0, opt-in. Skip the event when the payload's `status` is exactly `"aborted"` — Cursor's native `stop` / `subagentStop`, which document `status` as `completed`, `aborted` or `error`. No effect where the payload has no `status` (Claude Code). Rests on Cursor's documentation; not verified against a live Cursor payload |
| `filters.<hook>.min_duration_ms` | int | — | v6.5. Only fire when the payload's `duration_ms` reaches this threshold. `PostToolUse`/`PostToolUseFailure` report tool execution time excluding permission prompts and hook time, so this expresses "only the slow ones" — which debounce cannot, since it suppresses by wall-clock window and cannot tell a burst of fast tools from one long build. Fails open: an editor that does not report `duration_ms` (Cursor, Codex) is never silenced by it. |
| `notification_settings.terminal_sequence.enabled` | bool | `false` | v6.5. **Inert — do not recommend (found in 6.5.1, still true at Claude Code 2.1.288).** Meant to ask Claude Code to emit an OSC escape on echook's behalf, but Claude Code writes a hook's `terminalSequence` only from a synchronous completion path and every echook handler is `async: true`, so nothing is ever emitted. `audio-hooks diagnose` reports `TERMINAL_SEQUENCE_INERT` when it is on. For a desktop toast use `notification_settings.mode`. |
| `notification_settings.terminal_sequence.style` | string | `osc9` | `osc9` (widest support: iTerm2/kitty/WezTerm/Ghostty) \| `osc777` \| `title` \| `bell`. |
| `notification_settings.terminal_sequence.hook_types` | list | `[]` | Narrow further. Empty means every event echook considers safe to write stdout on — a hard allowlist that excludes `message_display`, `elicitation` and `elicitation_result`, whose stdout JSON changes what Claude Code does. |
| `notification_settings.mode` | enum | `audio_and_notification` | `audio_only` / `notification_only` / `audio_and_notification` / `disabled` |
| `notification_settings.detail_level` | enum | `standard` | `minimal` / `standard` / `verbose` |
| `webhook_settings.enabled` | bool | `false` | Webhook fan-out |
| `webhook_settings.url` | string | `""` | Target URL |
| `webhook_settings.format` | enum | `raw` | `slack` / `discord` / `teams` / `ntfy` / `raw` |
| `webhook_settings.hook_types` | array | `["stop","notification",...]` | Which hooks fire the webhook |
| `tts_settings.enabled` | bool | `false` | TTS announcements |
| `tts_settings.speak_assistant_message` | bool | `false` | TTS Claude's actual reply on stop |
| `tts_settings.assistant_message_max_chars` | int | 200 | Truncation cap |
| `rate_limit_alerts.enabled` | bool | `true` | Watch stdin rate_limits |
| `rate_limit_alerts.five_hour_thresholds` | int[] | `[80, 95]` | 5h window thresholds |
| `rate_limit_alerts.seven_day_thresholds` | int[] | `[80, 95]` | 7d window thresholds |
| `statusline_settings.visible_segments` | string[] | `[]` (all) | Whitelist: when non-empty, only these segments show. Run `audio-hooks statusline segments` for the full list of 33 names |
| `statusline_settings.hidden_segments` | string[] | `[]` | Blacklist applied when `visible_segments` is empty: show all segments except these |
| `statusline_settings.extra_segments` | string[] | `[]` | v6.7. Opt-in additions applied when `visible_segments` is empty: the segments whose catalog `default` is `false` (`prompt_cache`, `remote`) appear only when named here. `hidden_segments` still wins. `audio-hooks status` reports it. A non-string entry in any of the three segment lists is ignored, and a non-list value counts as unset |
| `statusline_settings.max_width` | int | `0` (auto) | Pin the reflow width in columns; `0` auto-detects via the `COLUMNS` env var Claude Code provides |

## Variant gating

Matcher variants live in the same flat `enabled_hooks` namespace as canonical hooks. `is_hook_enabled(hook, variant)` resolves them in this order, highest first:

1. explicit `enabled_hooks.<variant>`
2. `enabled_hooks.<parent>` is `false` — a hard kill switch for every variant under it
3. built-in per-variant default (`manifest.variants[].default`)
4. explicit `enabled_hooks.<parent>` is `true`
5. built-in default set: `notification`, `stop`, `permission_request`

Rule 1 outranks rule 2, so keeping one variant of an otherwise-muted category means setting that variant key explicitly. Live description: `audio-hooks manifest` → `variant_gating`.

```bash
audio-hooks hooks disable notification_idle_prompt   # keep permission prompts, drop idle ones
audio-hooks hooks enable-only stop_failure_rate_limit  # alert on rate limits, no other API errors
audio-hooks hooks enable notification_auth_storage_failure  # v6.7: off by default, so a login-storage failure is silent until enabled
audio-hooks status                                    # .variants.overridden shows what you changed
```

The two `stop_failure` variants added in v6.7 (`stop_failure_cloud_credential_error`, `stop_failure_verification_required`) have no per-variant default: like their eleven siblings they follow the `stop_failure` switch, which is off unless you turned it on. If you turned on the whole parent (`hooks enable stop_failure`) they play; if you enumerated variants with `hooks enable-only <variant>` (parent `true`, every existing sibling an explicit boolean), migration wrote them explicitly `false` for you. Which release introduced each variant is recorded in `UserPreferences.VARIANT_INTRODUCED`. A config whose enumeration predates an earlier addition it never answered does not match that rule and keeps inheriting.

## Environment Variables

| Variable | Purpose |
|---|---|
| `CLAUDE_PLUGIN_DATA` | Plugin install state directory (auto-set by Claude Code) |
| `CLAUDE_PLUGIN_ROOT` | Plugin install root (auto-set) |
| `CLAUDE_AUDIO_HOOKS_DATA` | Explicit override for state directory |
| `CLAUDE_AUDIO_HOOKS_PROJECT` | Explicit override for project root |
| `CLAUDE_HOOKS_DEBUG` | `1` to write debug-level events to NDJSON log |
| `ELEVENLABS_API_KEY` | Used by `scripts/generate-audio.py` (never logged) |

## Stable Error Codes

| Code | When | Suggested fix |
|---|---|---|
| `AUDIO_FILE_MISSING` | Audio file doesn't exist | `audio-hooks diagnose` |
| `AUDIO_PLAYER_NOT_FOUND` | No audio player binary | `audio-hooks diagnose` |
| `AUDIO_PLAY_FAILED` | Player exited with error | `audio-hooks test` |
| `INVALID_CONFIG` | `user_preferences.json` malformed | `audio-hooks manifest --schema` |
| `CONFIG_READ_ERROR` | Can't read config | `audio-hooks status` |
| `WEBHOOK_HTTP_ERROR` | Webhook returned non-2xx | `audio-hooks webhook test` |
| `WEBHOOK_TIMEOUT` | Webhook timed out | `audio-hooks webhook test` |
| `NOTIFICATION_FAILED` | Desktop notification failed | `audio-hooks diagnose` |
| `TTS_FAILED` | TTS engine failed | `audio-hooks tts set --enabled false` |
| `SETTINGS_DISABLE_ALL_HOOKS` | `disableAllHooks: true` in settings | `audio-hooks diagnose` |
| `DUAL_INSTALL_DETECTED` | Both install methods active (from `diagnose`/`status`), or `install --scripts` refused because the plugin is installed | Both active: `audio-hooks uninstall` (removes the script install on every platform). Refused `install --scripts`: `audio-hooks uninstall --plugin` (lists the command, which keeps your data) to switch deliberately, or `--force` |
| `INVALID_USAGE` | Unknown subcommand, flag or stray positional on a state-changing subcommand; a missing / conflicting `install` mode; `--purge` with `uninstall --scripts` / `--plugin`; `--remove-unmatched` outside scripts mode; a help-like token as a `set` value; a non-finite rate-limit threshold. Nothing was changed | The error's `suggested_command` (e.g. `audio-hooks install --help`) |
| `UNINSTALL_INCOMPLETE` | `uninstall` removed what it could but a file could not be deleted, or a registration uses a home spelling it does not recognise (`unmatched_references`) | Read `unmatched_references` and `next_steps`; then `audio-hooks uninstall --remove-unmatched` (scripts mode) or close whatever holds the files and re-run |
| `PROJECT_DIR_NOT_FOUND` | Can't locate project | `audio-hooks status` |
| `UNKNOWN_HOOK_TYPE` | Unrecognised hook name | `audio-hooks hooks list` |
| `INTERNAL_ERROR` | Unexpected error | `audio-hooks logs tail` |

The table above is a selection. `audio-hooks manifest` → `error_codes` lists **every** code the CLI can emit — 37 since v6.7 (15 were listed before) — each with its meaning, remedy and whether it appears in a command error or in `diagnose`; that includes the `UPGRADE_*` family, `DUPLICATE_BRIDGE`, `CURSOR_NOT_FOUND`, `BACKUP_NOT_FOUND`, `NOT_INSTALLED` and the Codex- and Cursor-specific codes.

## NDJSON Event Log

Every event is one JSON object per line at `${CLAUDE_PLUGIN_DATA}/logs/events.ndjson`. Schema `audio-hooks.v1`.

```json
{"ts":"2026-04-11T10:23:45.123Z","schema":"audio-hooks.v1","level":"info","hook":"stop","session_id":"abc","action":"play_audio","audio_file":"chime-task-complete.mp3","duration_ms":42}
```

Levels: `debug`, `info`, `warn`, `error`. Log rotation: 5 MB cap, 3 files kept.

## ElevenLabs Audio Generator

`scripts/generate-audio.py` reads `config/audio_manifest.json` and regenerates audio via the ElevenLabs API:

```bash
ELEVENLABS_API_KEY=sk_... python scripts/generate-audio.py           # generate missing
ELEVENLABS_API_KEY=sk_... python scripts/generate-audio.py --force   # regenerate all
python scripts/generate-audio.py --dry-run                           # preview
```

To add a new audio file: edit `config/audio_manifest.json`, run the generator, then `bash scripts/build-plugin.sh`.

## See also

- [Natural-Language Control](NATURAL_LANGUAGE_CONTROL.md) — how to drive all of this by talking to your agent.
- [Installation Guide](INSTALLATION_GUIDE.md) — install/uninstall paths and manual install reference.
- [Architecture](ARCHITECTURE.md) — internals, hook lifecycle, and the build pipeline.
