# System Architecture

> **Version:** 5.0.1 | **Last Updated:** 2026-04-11

This document explains the technical architecture of claude-code-audio-hooks v5.0.1. It is the developer-facing deep dive — for operating the project, see [CLAUDE.md](../CLAUDE.md) (the canonical AI doc) or [README.md](../README.md). For the live machine description of every subcommand and config key, run `audio-hooks manifest`.

## Design constraints

The project is **AI-operated**, not human-operated:

1. **No interactive CLI prompts.** All scripts auto-engage non-interactive mode on non-TTY or when `CLAUDE_NONINTERACTIVE=1` is set.
2. **No human-readable error logs.** All logs are NDJSON (`audio-hooks.v1` schema) with stable `code` enums and machine-actionable `hint` + `suggested_command` fields.
3. **No GUIs.**
4. **No 2FA / CAPTCHA gates.**
5. **Every config knob is settable in one shot** via `audio-hooks set` or a typed setter.
6. **Every state read returns a single JSON document** in <100ms.
7. **All documentation that Claude Code reads is self-contained, structured, and current.**
8. **Single monolith, one repo, one codebase.** No microservices. The plugin lives inside the same repo as a subdirectory.

## High-level architecture

```mermaid
flowchart LR
    CC[Claude Code event] -->|stdin JSON| MR{native matcher<br/>routing}
    MR -->|session_start_resume| HR[hook_runner.py]
    MR -->|stop_failure_rate_limit| HR
    MR -->|notification_idle_prompt| HR
    MR -->|...| HR

    HR -->|reads| RL[rate-limit pre-check<br/>marker debounce]
    HR -->|reads| CFG[user_preferences.json]
    HR -->|reads| MARK[snooze + focus-flow markers]

    HR -->|fires| AUDIO[Audio playback<br/>26 MP3s, 2 themes]
    HR -->|fires| NOTIF[Desktop notification]
    HR -->|fires| TTS[TTS announcement]
    HR -->|fires| WH[Webhook subprocess<br/>fire-and-forget]
    HR -->|writes| LOG[(NDJSON event log<br/>schema audio-hooks.v1)]

    style CC fill:#4A90E2,color:#fff
    style HR fill:#7ED321,color:#000
    style RL fill:#F5A623,color:#000
    style AUDIO fill:#F5A623,color:#000
    style WH fill:#9013FE,color:#fff
    style LOG fill:#50E3C2,color:#000
```

## Components

### 1. `hooks/hook_runner.py` (canonical, ~1700 lines)

The Python hook runner is the **single source of truth** for hook event handling. It is invoked in two ways:

| Invocation | Trigger | `CLAUDE_PLUGIN_DATA` set? |
|---|---|---|
| Plugin install | `${CLAUDE_PLUGIN_ROOT}/runner/run.py <event>` from `hooks/hooks.json` | yes |
| Script install | `python ~/.claude/hooks/hook_runner.py <event>` from `~/.claude/settings.json` | no |

The runner accepts both **canonical hook names** (`stop`, `notification`, `session_start`) and **synthetic matcher variants** (`session_start_resume`, `stop_failure_rate_limit`, `notification_idle_prompt`). Synthetic names are mapped to a canonical hook plus a per-variant audio override via `SYNTHETIC_EVENT_MAP`.

**Per-invocation flow:**

```mermaid
flowchart TD
    EVT[Hook event fires<br/>e.g. Stop] --> SETCTX[set log context<br/>session_id + hook_type]
    SETCTX --> ENABLED{is_hook_enabled?}
    ENABLED -->|no| EXIT[exit 0 silent]
    ENABLED -->|yes| SNOOZE{is_snoozed?}
    SNOOZE -->|yes| EXIT
    SNOOZE -->|no| LOAD[load_config + plugin_option overlay]
    LOAD --> RLCHK[check_rate_limits<br/>marker debounced]
    RLCHK --> DEB{should_debounce?}
    DEB -->|yes| EXIT
    DEB -->|no| FILTER{should_filter?<br/>user regex on stdin fields}
    FILTER -->|yes, exclude| EXIT
    FILTER -->|no| AUDIT[check_and_self_update]
    AUDIT --> FF[Focus Flow start/stop]
    FF --> CTX[get_notification_context<br/>+ universal suffix]
    CTX --> AUDIO{mode=audio*?}
    AUDIO -->|yes| PLAY[play_audio]
    PLAY --> NOTIF{mode=notif*?}
    AUDIO -->|no| NOTIF
    NOTIF -->|yes| SEND[send_desktop_notification]
    SEND --> TTS{tts.enabled?}
    NOTIF -->|no| TTS
    TTS -->|yes + speak_assistant_message| SPEAK1[play_tts last_assistant_message]
    TTS -->|yes| SPEAK2[play_tts static or context]
    SPEAK1 --> WH{webhook.enabled?}
    SPEAK2 --> WH
    TTS -->|no| WH
    WH -->|yes| WEBHOOK[send_webhook<br/>fire-and-forget subprocess]
    WH -->|no| LOG
    WEBHOOK --> LOG[log_event<br/>NDJSON]
    LOG --> EXIT_OK[exit 0]

    style EVT fill:#4A90E2,color:#fff
    style PLAY fill:#7ED321,color:#000
    style WEBHOOK fill:#9013FE,color:#fff
    style LOG fill:#50E3C2,color:#000
```

**Key functions:**

| Function | Purpose |
|---|---|
| `_resolve_synthetic_event(raw_arg)` | Maps synthetic names to canonical + audio override |
| `_resolve_config_file()` | Resolves `user_preferences.json` path: `CLAUDE_PLUGIN_DATA` → plugin context detection → explicit override → legacy script path |
| `_apply_plugin_option_overlay(config)` | Overlays `CLAUDE_PLUGIN_OPTION_*` env vars onto loaded config |
| `is_hook_enabled(hook_type)` | Reads `enabled_hooks.<name>` with v5.0 default-on for `permission_denied` and `task_created` |
| `is_snoozed()` | Reads marker file at `${QUEUE_DIR}/snooze_until` |
| `should_debounce(hook_type)` | Per-hook debounce marker |
| `should_filter(hook_type, stdin, config)` | User-defined regex filters on stdin fields |
| `check_rate_limits(stdin, config)` | v5.0: inspects `rate_limits` field, fires one-shot warning per `(window, threshold, resets_at)` |
| `get_notification_context(hook, stdin, level)` | Builds the notification text with v5.0 enrichment (last_assistant_message, worktree, agent, etc.) |
| `_format_context_suffix(stdin, level)` | Universal `[session: foo, worktree: bar]` suffix |
| `play_audio(file)` | Platform dispatch: `play_audio_windows` / `_macos` / `_linux` / `_wsl` |
| `send_desktop_notification(title, msg, urgency)` | Platform dispatch: osascript / notify-send / PowerShell NotifyIcon |
| `play_tts(message)` | Platform dispatch: `say` / `espeak` / `spd-say` / SAPI |
| `send_webhook(...)` | v5.0: fire-and-forget via subprocess.Popen so parent exits immediately |
| `log_event(level, action, **fields)` | NDJSON writer with stable schema, log rotation 5MB / 3 files |
| `log_error_event(code, action, ...)` | Adds `error.code` + `error.hint` + `error.suggested_command` |

### 2. `bin/audio-hooks` (canonical CLI)

Three files:

| File | Role |
|---|---|
| `bin/audio-hooks.py` | Python entry point (~1100 lines), 27 subcommands |
| `bin/audio-hooks` | Bash wrapper that probes `python3` / `python` / `py` and exec's the .py file. Skips Microsoft Store python3 stub on Windows. |
| `bin/audio-hooks.cmd` | Windows shim that runs `python audio-hooks.py %*` |

The bash wrapper exists because Git Bash on Windows doesn't reliably handle Python shebangs and the Microsoft Store python3 stub at `WindowsApps\python3.exe` exits 49 silently when invoked. The wrapper probes each candidate with a `-c "import sys"` test and skips broken stubs.

**Subcommand dispatch table** lives at the bottom of `audio-hooks.py` (the `DISPATCH` dict). Adding a new subcommand: write `cmd_<name>(args) -> int`, add to `DISPATCH`, add an entry to `_build_manifest()`'s `subcommands` list.

**Plugin context detection** (`_is_running_from_plugin()`): the binary lives at `<plugin_root>/bin/audio-hooks.py` when invoked from a plugin install. We detect this by checking for `<plugin_root>/.claude-plugin/plugin.json`. When detected, `_config_path()` resolves to `~/.claude/plugins/data/audio-hooks-chanmeng-audio-hooks/user_preferences.json` (the canonical plugin data dir per Claude Code's docs) and auto-initialises from `default_preferences.json` on first read.

### 3. `plugins/audio-hooks/` (Claude Code plugin)

Self-contained plugin layout, populated by `bash scripts/build-plugin.sh` from the canonical sources.

```
plugins/audio-hooks/
├── .claude-plugin/
│   └── plugin.json              # name, version, userConfig
├── hooks/
│   ├── hooks.json               # matcher-scoped hook registration (auto-discovered)
│   └── hook_runner.py           # copy of /hooks/hook_runner.py
├── runner/
│   └── run.py                   # imports bundled hook_runner.py and dispatches
├── skills/
│   └── audio-hooks/
│       └── SKILL.md             # natural-language activation
├── bin/
│   ├── audio-hooks              # bash wrapper
│   ├── audio-hooks.py           # Python entry
│   └── audio-hooks.cmd          # Windows shim
├── audio/
│   ├── default/                 # 26 voice files
│   └── custom/                  # 26 chime files
└── config/
    └── default_preferences.json # template (auto-copied to plugin data dir)
```

**`hooks/hooks.json`** registers per-matcher handlers using synthetic event names:

```jsonc
{
  "hooks": {
    "Notification": [
      { "matcher": "permission_prompt",
        "hooks": [{ "type": "command",
                    "command": "python \"${CLAUDE_PLUGIN_ROOT}/runner/run.py\" notification_permission_prompt",
                    "async": true, "timeout": 10 }] },
      { "matcher": "idle_prompt",      "hooks": [...] },
      { "matcher": "auth_success",     "hooks": [...] },
      { "matcher": "elicitation_dialog","hooks": [...] }
    ],
    "SessionStart": [
      { "matcher": "startup", "hooks": [...session_start_startup] },
      { "matcher": "resume",  "hooks": [...session_start_resume] },
      { "matcher": "clear",   "hooks": [...session_start_clear] },
      { "matcher": "compact", "hooks": [...session_start_compact] }
    ],
    "StopFailure": [
      { "matcher": "rate_limit",            "hooks": [...stop_failure_rate_limit] },
      { "matcher": "authentication_failed", "hooks": [...stop_failure_authentication_failed] },
      { "matcher": "billing_error|invalid_request|server_error|max_output_tokens|unknown",
        "hooks": [...stop_failure_other] }
    ]
    // ... and so on for all 25 events
  }
}
```

Native matcher routing happens at the `settings.json` layer (Claude Code's matcher engine), not inside Python branching. Faster, configurable per-matcher, and per-handler `async: true` means a slow rate-limit-failure path doesn't block the auth-failure path.

**Auto-discovery**: don't put `"hooks": "./hooks/hooks.json"` in `plugin.json` — Claude Code auto-discovers `hooks/hooks.json` from the standard location, and declaring it twice causes "Duplicate hooks file detected" load errors.

**`runner/run.py`** is a thin wrapper that walks up from its own directory looking for `hooks/hook_runner.py` (which is bundled inside the plugin), inserts that path into `sys.path`, and calls `hook_runner.main()`.

**`skills/audio-hooks/SKILL.md`** is the natural-language activation surface. YAML frontmatter declares trigger phrases like *"snooze audio"*, *"configure audio hooks"*, *"why is there no sound"*. When Claude detects an intent matching one of these, it loads the SKILL body which is a structured prose-and-table guide telling Claude exactly which `audio-hooks` subcommand to run for any user request. The golden rule baked into the SKILL: **always run `audio-hooks manifest` first** if you're unsure of the project's current surface area.

### 4. `bin/audio-hooks-statusline` (Claude Code status line)

Two-line bottom bar registered in `~/.claude/settings.json` via `audio-hooks statusline install`. Reads stdin JSON Claude Code provides (model name, session_id, workspace.git_worktree, rate_limits, context_window) and emits two lines of plain text with ANSI colors:

```text
[Opus] 🔊 Audio Hooks v5.1.1 | 6/26 Sounds | Webhook: ntfy | Theme: Voice
[MUTED 23m]  🌿 feat/audio-v5  ████░░░░ API Quota: 78%  █████░░░ Context: 65% ⚠️ /compact
```

The API Quota bar uses thresholds GREEN <70%, YELLOW 70-89%, RED ≥90%. The Context bar uses agent-safety thresholds: GREEN <50% (safe), YELLOW 50-80% (should `/compact`), RED >80% (agent "dumb zone"). Actionable hints (`⚠️ /compact` or `🛑 /compact`) appear in yellow/red zones.

Users can customise which segments appear via `statusline_settings.visible_segments` (array of segment names). 10 segments available — Line 1: `model`, `version`, `sounds`, `webhook`, `theme`; Line 2: `snooze`, `focus`, `branch`, `api_quota`, `context`. Empty array (default) shows all. Example: `audio-hooks set statusline_settings.visible_segments '["context","api_quota"]'` shows only the two progress bars.

`refreshInterval: 60` is set in the registration so snooze countdowns, rate-limit bars, and context usage bars update during idle periods. The script caches `audio-hooks status` for 5 seconds keyed on `session_id` to keep render time <100ms.

### 5. `scripts/`

| Script | Purpose | AI-callable? |
|---|---|---|
| `install-complete.sh` | Legacy script install | yes (auto non-interactive on non-TTY) |
| `install-windows.ps1` | PowerShell installer for Windows | yes |
| `quick-setup.sh` | Lite tier (zero deps, no Python) | yes |
| `quick-configure.sh` | Lite tier hook toggling | yes |
| `quick-unsetup.sh` | Lite tier uninstall | yes |
| `snooze.sh` | Legacy snooze CLI | yes (`audio-hooks snooze` is preferred) |
| `uninstall.sh` | Legacy uninstall | yes (auto non-interactive, `--purge` for full removal) |
| `build-plugin.sh` | Sync canonical → plugin layout | yes (NDJSON output, `--check` flag for CI) |
| `generate-audio.py` | ElevenLabs audio generator | yes (NDJSON output, `--force` / `--only` / `--dry-run`) |
| `configure.sh` | Human-only menu | **no** — auto-redirects to `audio-hooks` via `INTERACTIVE_SCRIPT` JSON pointer when invoked non-interactively |
| `test-audio.sh` | Human-only menu | **no** — same |
| `diagnose.py` | Legacy diagnose | yes (`audio-hooks diagnose` is preferred) |

## Hook event lifecycle (full detail)

```mermaid
sequenceDiagram
    participant CC as Claude Code
    participant SJ as ~/.claude/settings.json<br/>(plugin hooks merged)
    participant RUNNER as runner/run.py
    participant HR as hook_runner.py
    participant CONFIG as user_preferences.json
    participant LOG as events.ndjson
    participant AUDIO as Audio player

    CC->>CC: Internal event<br/>(e.g. Stop)
    CC->>SJ: Look up matcher
    SJ->>RUNNER: spawn `python run.py stop` (async)
    RUNNER->>HR: import + main()
    HR->>HR: parse stdin JSON
    HR->>HR: _set_log_context(session_id, hook)
    HR->>LOG: log "hook_start"

    HR->>HR: is_hook_enabled?
    alt disabled
        HR->>LOG: log "hook_status DISABLED"
        HR-->>CC: exit 0
    else enabled
        HR->>HR: is_snoozed?
        alt snoozed
            HR->>LOG: log "hook_status SNOOZED"
            HR-->>CC: exit 0
        else not snoozed
            HR->>CONFIG: load (with plugin_option overlay)
            HR->>HR: check_rate_limits(stdin, config)
            opt threshold crossed
                HR->>AUDIO: play warning audio
                HR->>LOG: log "rate_limit_alert"
            end
            HR->>HR: should_debounce?
            alt debounced
                HR->>LOG: log "hook_status DEBOUNCED"
                HR-->>CC: exit 0
            else not debounced
                HR->>HR: should_filter? (user regex)
                HR->>HR: build context + suffix
                HR->>AUDIO: play_audio
                HR->>HR: send_desktop_notification
                HR->>HR: play_tts (with optional speak_assistant_message)
                HR->>HR: send_webhook (subprocess fire-and-forget)
                HR->>LOG: log "hook_status PLAYED"
                HR-->>CC: exit 0
            end
        end
    end
```

## Path resolution

```mermaid
flowchart TD
    START[hook_runner or audio-hooks startup] --> Q1{CLAUDE_PLUGIN_DATA<br/>set?}
    Q1 -->|yes| PLUGIN_HOOK[plugin hook context]
    PLUGIN_HOOK --> P1[CONFIG: $PLUGIN_DATA/user_preferences.json]
    PLUGIN_HOOK --> P2[QUEUE: $PLUGIN_DATA/queue/]
    PLUGIN_HOOK --> P3[LOGS: $PLUGIN_DATA/logs/]

    Q1 -->|no| Q2{script lives in<br/><plugin_root>/?}
    Q2 -->|yes| PLUGIN_CLI[plugin CLI context]
    PLUGIN_CLI --> R1[CONFIG: ~/.claude/plugins/data/audio-hooks-chanmeng-audio-hooks/user_preferences.json]
    PLUGIN_CLI --> R2[QUEUE: same dir/queue/]
    PLUGIN_CLI --> R3[LOGS: same dir/logs/]

    Q2 -->|no| Q3{CLAUDE_AUDIO_HOOKS_DATA<br/>set?}
    Q3 -->|yes| EXPLICIT[explicit override]
    EXPLICIT --> E1[CONFIG: $CLAUDE_AUDIO_HOOKS_DATA/user_preferences.json]

    Q3 -->|no| LEGACY[legacy script install]
    LEGACY --> L1[CONFIG: <project_dir>/config/user_preferences.json]
    LEGACY --> L2[QUEUE: <temp>/claude_audio_hooks_queue/]

    style PLUGIN_HOOK fill:#7ED321,color:#000
    style PLUGIN_CLI fill:#7ED321,color:#000
    style EXPLICIT fill:#F5A623,color:#000
    style LEGACY fill:#9013FE,color:#fff
```

The plugin data dir is at `~/.claude/plugins/data/{id}/` where `{id}` is the plugin name with non-alnum chars replaced by `-`. For `audio-hooks@chanmeng-audio-hooks` the id is `audio-hooks-chanmeng-audio-hooks`.

## NDJSON event log

Schema: `audio-hooks.v1`. One JSON object per line. Event types are stable.

| `action` | `level` | When |
|---|---|---|
| `hook_start` | `debug` | Every hook invocation, with `synthetic_variant` if matcher-routed |
| `hook_status` | `info` | Final status: `PLAYED`, `DISABLED`, `SNOOZED`, `DEBOUNCED`, `FILTERED`, `NO_AUDIO_CONFIG`, `FILE_NOT_FOUND`, `PLAY_FAILED` |
| `rate_limit_alert` | `warn` | Rate-limit threshold crossed; includes `window`, `threshold`, `used_percentage`, `resets_at` |
| `tts_spoken` | `info` | TTS dispatched |
| `webhook_dispatched` | `info` | Webhook subprocess spawned |
| `audio_override_resolved` | `debug` | Synthetic matcher variant resolved an audio override |
| `play_audio` | `info` | Audio successfully dispatched to platform player |
| `legacy_error` | `error` | Caught from `log_error()` legacy wrapper |
| `lookup_audio` | `error` | `AUDIO_FILE_MISSING` |
| `webhook_dispatch` | `error` | `WEBHOOK_TIMEOUT` or `WEBHOOK_HTTP_ERROR` |

Error events carry an `error` object with `code` (stable enum), `message`, `hint`, and optionally `suggested_command`.

## Stable error code enum

Defined in `hook_runner.py`'s `ErrorCode` class. Add new codes here, never rename existing ones.

```python
class ErrorCode:
    AUDIO_FILE_MISSING = "AUDIO_FILE_MISSING"
    AUDIO_PLAYER_NOT_FOUND = "AUDIO_PLAYER_NOT_FOUND"
    AUDIO_PLAY_FAILED = "AUDIO_PLAY_FAILED"
    INVALID_CONFIG = "INVALID_CONFIG"
    CONFIG_READ_ERROR = "CONFIG_READ_ERROR"
    WEBHOOK_HTTP_ERROR = "WEBHOOK_HTTP_ERROR"
    WEBHOOK_TIMEOUT = "WEBHOOK_TIMEOUT"
    NOTIFICATION_FAILED = "NOTIFICATION_FAILED"
    TTS_FAILED = "TTS_FAILED"
    SETTINGS_DISABLE_ALL_HOOKS = "SETTINGS_DISABLE_ALL_HOOKS"
    PROJECT_DIR_NOT_FOUND = "PROJECT_DIR_NOT_FOUND"
    SELF_UPDATE_FAILED = "SELF_UPDATE_FAILED"
    UNKNOWN_HOOK_TYPE = "UNKNOWN_HOOK_TYPE"
    INTERNAL_ERROR = "INTERNAL_ERROR"
```

The `bin/audio-hooks.py` `cmd_diagnose` function adds two more codes that are CLI-specific (not from hook_runner): `DUAL_INSTALL_DETECTED` and `INTERACTIVE_SCRIPT`.

`_ERROR_HINTS` (a dict in `hook_runner.py`) maps each code to a `hint` (one sentence) and `suggested_command` (a literal `audio-hooks ...` command). When `log_error_event(code, action, message)` is called, the resulting NDJSON event has the full error object populated automatically.

## Backwards compatibility

| Pre-v5.0 surface | v5.0.1 status |
|---|---|
| `~/.claude/settings.json` legacy hook entries (`Notification`, `Stop`, `SubagentStop`, `PermissionRequest`) | Still work — canonical hook names resolve in `hook_runner.main()` |
| Free-text `debug.log`, `errors.log`, `hook_triggers.log` | Replaced by `events.ndjson`. Legacy `log_debug`/`log_error`/`log_trigger` are now thin NDJSON wrappers. |
| `<project>/config/user_preferences.json` (script install) | Still the resolution target for legacy script installs |
| `bash scripts/install-complete.sh` interactive mode | Still works for human users; auto non-interactive on non-TTY |
| `scripts/snooze.sh` CLI | Still works; `audio-hooks snooze` is preferred |
| `scripts/diagnose.py` | Still works; `audio-hooks diagnose` is preferred (returns JSON) |
| Pre-v5 `user_preferences.json` schema | Forward-compatible — new keys are optional with sensible defaults |

## Build pipeline

```mermaid
flowchart LR
    DEV[Developer edits canonical] --> EDIT[/hooks/, /bin/, /audio/, /config/]
    EDIT --> BUILD[bash scripts/build-plugin.sh]
    BUILD --> CHECK[bash scripts/build-plugin.sh --check]
    CHECK -->|in_sync| TEST[python bin/audio-hooks.py test all]
    TEST --> VALIDATE[claude plugin validate plugins/audio-hooks]
    VALIDATE --> COMMIT[git commit + push]

    CHECK -->|out_of_sync| FAIL[CI fails]
    VALIDATE -->|errors| FIX[fix manifest]
    FIX --> BUILD

    style DEV fill:#4A90E2,color:#fff
    style BUILD fill:#7ED321,color:#000
    style VALIDATE fill:#F5A623,color:#000
    style COMMIT fill:#9013FE,color:#fff
```

## Adding a new hook event (when Claude Code adds one)

1. Add the canonical name + audio filename to `DEFAULT_AUDIO_FILES` and `CUSTOM_AUDIO_FILES` in `hook_runner.py`.
2. Add a branch in `get_notification_context(hook_type, ...)` for the notification text.
3. Add the entry to `HOOK_CATALOG` in `bin/audio-hooks.py`.
4. Add an entry to `enabled_hooks` in `config/default_preferences.json` (with default on/off).
5. Add the event handler to `plugins/audio-hooks/hooks/hooks.json` (with matchers if applicable).
6. If matcher-scoped, add synthetic event entries to `SYNTHETIC_EVENT_MAP` in `hook_runner.py`.
7. Add audio entries to `config/audio_manifest.json` and run `python scripts/generate-audio.py`.
8. Run `bash scripts/build-plugin.sh`.
9. Test: `python bin/audio-hooks.py test <new_hook>`.
10. Update `CLAUDE.md` and `README.md` hook tables. Bump version, update CHANGELOG.

## Adding a new audio file

1. Add an entry to `config/audio_manifest.json`: `filename`, `theme`, `type` (`voice` or `sound_effect`), `text` prompt.
2. `ELEVENLABS_API_KEY=... python scripts/generate-audio.py --only <new_file>`.
3. `bash scripts/build-plugin.sh`.
4. Commit the new MP3 + manifest entry.

## Testing locally

```bash
# From a fresh terminal — verify the binary works
python bin/audio-hooks.py manifest
python bin/audio-hooks.py status
python bin/audio-hooks.py test all
python bin/audio-hooks.py diagnose

# Verify the plugin layout
bash scripts/build-plugin.sh --check
claude plugin validate plugins/audio-hooks

# Verify a specific hook with mock stdin
echo '{"session_id":"t","hook_event_name":"Stop","last_assistant_message":"test"}' | \
  python hooks/hook_runner.py stop

# Test rate-limit alert
echo '{"session_id":"t","rate_limits":{"five_hour":{"used_percentage":85,"resets_at":9999999999}}}' | \
  python hooks/hook_runner.py stop
```

## See also

- [CLAUDE.md](../CLAUDE.md) — canonical AI-facing operating guide
- [README.md](../README.md) — public-facing project introduction
- [CHANGELOG.md](../CHANGELOG.md) — version history including the v5.0/v5.0.1 detail
- `audio-hooks manifest` — live machine description of every subcommand and config key
