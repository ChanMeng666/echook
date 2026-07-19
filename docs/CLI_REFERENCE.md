# CLI & Configuration Reference

> **Agents:** this page is a static, human-readable mirror. The live source of truth is **`audio-hooks manifest`** — it prints every subcommand, hook, config key, error code, and env var as JSON, always current. Prefer it over this page.

Humans rarely need this — you operate echook by [talking to your AI agent](NATURAL_LANGUAGE_CONTROL.md). It's here for the curious and for offline reference.

## `audio-hooks` CLI

Single Python binary on PATH. JSON output, no prompts, no spinners.

| Subcommand | Purpose |
|---|---|
| `audio-hooks manifest` | Canonical introspection — every subcommand, hook, config key, error code, env var |
| `audio-hooks manifest --schema` | JSON Schema for `user_preferences.json` |
| `audio-hooks status` | Full state snapshot |
| `audio-hooks version` | Version + install mode detection |
| `audio-hooks get <dotted.key>` | Read any config key |
| `audio-hooks set <dotted.key> <value>` | Write any config key (auto-coerces) |
| `audio-hooks hooks list` | All 37 hooks with current state (`--variants` adds the 30 matcher variants) |
| `audio-hooks hooks list --variants` | Adds a `variants` key listing the 30 matcher variants; `hooks` stays at 37 rows |
| `audio-hooks hooks enable/disable <name>` | Toggle a hook **or a matcher variant** (`notification_idle_prompt`, `stop_failure_rate_limit`, …) |
| `audio-hooks hooks enable-only <a> <b>` | Exclusive enable. Accepts variants; a named variant keeps its parent enabled, since a disabled parent silences all its variants |
| `audio-hooks theme list/set <name>` | Audio theme |
| `audio-hooks snooze [duration]/off/status` | Mute hooks (default 30m) |
| `audio-hooks webhook/set/clear/test` | Webhook config + test |
| `audio-hooks tts set ...` | TTS config |
| `audio-hooks rate-limits set ...` | Rate-limit alert thresholds |
| `audio-hooks test <hook\|all>` | Smoke-test hooks |
| `audio-hooks diagnose` | System check |
| `audio-hooks logs tail/clear` | NDJSON event log |
| `audio-hooks install/uninstall` | Non-interactive install/uninstall |
| `audio-hooks statusline show/install/uninstall` | Claude Code status line registration |
| `audio-hooks statusline segments` | List all 29 Claude Code status line segments (name, line, source field, conditional) |
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
| `filters.stop.skip_if_background_tasks_running` | bool | `false` | v6.4. Stay silent while any `background_tasks` entry on the `Stop` payload has `status: running` — teammates, subagents, background shells. The practical fix for "a chime after every message" |
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
| `statusline_settings.visible_segments` | string[] | `[]` (all) | Whitelist: when non-empty, only these segments show. Run `audio-hooks statusline segments` for the full list of 29 names |
| `statusline_settings.hidden_segments` | string[] | `[]` | Blacklist applied when `visible_segments` is empty: show all segments except these |
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
audio-hooks status                                    # .variants.overridden shows what you changed
```

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
| `DUAL_INSTALL_DETECTED` | Both install methods active | `audio-hooks uninstall` |
| `PROJECT_DIR_NOT_FOUND` | Can't locate project | `audio-hooks status` |
| `UNKNOWN_HOOK_TYPE` | Unrecognised hook name | `audio-hooks hooks list` |
| `INTERNAL_ERROR` | Unexpected error | `audio-hooks logs tail` |

(See `audio-hooks manifest` → `error_codes` for the complete, live list including Cursor/Codex-specific codes.)

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
