# Architecture

What an agent cannot read off the code, the tests or `audio-hooks manifest`. Engineering rules and the test guarding each are in [INVARIANTS.md](INVARIANTS.md); operating rules are in [AGENTS.md](../AGENTS.md).

## Map

- Canonical sources: `hooks/` (`hook_runner.py` and the modules it is split into, `invoker.py`, `user_preferences.py`), `bin/` (the `audio-hooks` CLI, the status line scripts and their wrappers), `audio/{default,custom}/`, `config/`, `cursor-hooks/`, `codex-hooks/`. `scripts/` and `tests/` have no mirror.
- `plugins/audio-hooks/` is a build mirror produced by `bash scripts/build-plugin.sh` (`--check` is what CI runs). It copies `hooks/{hook_runner,user_preferences,invoker}.py` and the runner modules named in its `RUNNER_MODULES`, the `bin/` files, `config/default_preferences.json`, `cursor-hooks/hooks.json`, `codex-hooks/{hooks,plugin-hooks}.json` and both audio themes. Never edit those copies.
- Hand-edited inside `plugins/audio-hooks/` and not synced: `hooks/hooks.json` (no repo-root counterpart), `runner/run.py`, `skills/`, `.claude-plugin/`, `.codex-plugin/`, `README.md`, `evals/`.
- Entry points: plugin install runs `${CLAUDE_PLUGIN_ROOT}/runner/run.py <event>` (`CLAUDE_PLUGIN_DATA` set); a script install made before 7.0.0 (the installer is removed; the runner still supports it) runs `~/.claude/hooks/hook_runner.py <event>` (not set). Codex plugin uses `${PLUGIN_ROOT}/runner/run.py`; Codex native passes `--invoker codex`.
- Runtime order in `run_hook` (`hooks/hook_runner.py`), verified against the source: invoker guards (Cursor-unsupported events, `DUPLICATE_BRIDGE_RUNTIME_SKIP`, the internal-`SubagentStop` skip, the Cursor stop-error re-route) -> `is_hook_enabled` -> `is_snoozed` -> `load_config` -> `check_rate_limits` -> `should_filter` -> `should_debounce` -> `check_and_self_update` -> audio / desktop notification / TTS / webhook -> NDJSON log. Filters run before debounce because `should_debounce` stamps the window whenever it lets an event through.
- Runner modules are flat files in `hooks/`, not a package (`runner/run.py` and the Cursor/Codex native installs put that directory on `sys.path` and run `hook_runner.py`). `hook_runner.py` keeps every catalog table, the gating (`is_hook_enabled`, `is_snoozed`, `should_filter`, `should_debounce`), `run_hook` and `main`. `runner_core.py` owns the shared state: preferences handle, NDJSON log and its context, queue-dir flag, invoker lookup. On top of it: `platform_shell.py`, then `audio_playback.py`, `desktop_notification.py`, `text_to_speech.py`, `webhook_dispatch.py`, `rate_limit_alerts.py`, `terminal_sequence.py`; `notification_context.py` is pure. No module imports `hook_runner` (it runs as `__main__`); what must stay in it but is needed below (error hints, version, `NOTIFICATION_TYPE_LABELS`, project dir) is bound into `runner_core` / `notification_context` at import. Names are called through their module (`runner_core.log_event(...)`), so a test patches one place, and `hook_runner` re-exports nothing, so a patch aimed at a moved name fails loudly. A fresh load of `hook_runner.py` drops the cached siblings first; that is how the tests reset state. Guard: `tests/test_runner_modules.py`.
- Data dir resolution is `UserPreferences._resolve_data_dir()` (6-level chain, see Cursor section). `audio-hooks manifest` -> `pointers` lists the docs.

## The registration chain, and why it needs tests

Matcher routing happens in the registration template, not in Python branching. One logical fact is therefore spread across three places linked only by naming convention:

```
hooks.json  "matcher": "idle_prompt"
  -> command arg               notification_idle_prompt
  -> SYNTHETIC_EVENT_MAP[...]  ("notification", "notif-idle-prompt.mp3")
  -> audio/{default,custom}/   notif-idle-prompt.mp3 / chime-notif-idle-prompt.mp3
```

Nothing validates the chain at runtime. `_resolve_synthetic_event` returns an unknown arg unchanged, `run_hook` receives a hook type nothing recognises, and the event becomes a permanent no-op: no crash, no user-visible log. Two defects of exactly this shape survived undetected until v6.4: four `session_end_*` variants defined but never registered, and four `Notification` matchers never registered at all, so those notification types made no sound.

`tests/test_plugin_hooks_contract.py` pins the chain in both directions (every registered arg resolves, every map key is reachable or allowlisted, every audio override exists in both themes, every notification subtype has wording, `HOOK_CATALOG` agrees with the preferences template). It is the reason a break is loud. `plugins/audio-hooks/hooks/hooks.json` is hand-edited and `build-plugin.sh` does not sync it.

The variant reaches the gate as an explicit `run_hook(..., variant=...)` parameter, not via the `_current_synthetic_variant` module global: `bin/audio-hooks.py`'s `_run_one_test` drives `run_hook` in a loop, where stale global state from a previous iteration could decide the next hook's fate. Variant keys are plain booleans in the flat `enabled_hooks` map, so the v6.4 variant feature needed no schema change, no config `_version` bump and no user migration. The gating precedence is in INVARIANTS.md ("Events and matcher variants").

## Plugin hooks auto-discovery

Do not put `"hooks": "./hooks/hooks.json"` in `plugin.json`. Claude Code auto-discovers `hooks/hooks.json` from the standard location, and declaring it as well causes "Duplicate hooks file detected" load errors. Per-handler `async: true` in `hooks.json` also means a slow handler for one matcher does not block another.

## Python launcher: the Microsoft Store stub

`bin/audio-hooks` is a bash wrapper that probes `python3` / `python` / `py` with a `-c "import sys"` test and execs the first that works. It exists because Git Bash on Windows does not reliably handle Python shebangs, and the Microsoft Store `python3` stub at `WindowsApps\python3.exe` exits 49 silently when invoked; probing, rather than checking for existence, is what skips it. If all candidates fail it prints `PYTHON_NOT_FOUND` JSON.

## Status line: context-usage numerator

When stdin carries both `context_window.used_percentage` and `context_window.context_window_size`, the segment shows absolute counts, e.g. `Context: 83% (166K/200K)`. The numerator is derived as `int(round(used_percentage * context_window_size / 100))` (v5.1.3+). It deliberately does not use `total_input_tokens`: that field counts only literal input tokens, excluding `cache_read_input_tokens` / `cache_creation_input_tokens`, and understates real context usage by about 30x in cache-heavy sessions such as Claude Code itself. Deriving from the percentage keeps the displayed math self-consistent. A missing, malformed or non-positive `context_window_size` falls back silently to `Context: 83%`. Guard: `tests/test_statusline.py::TestContextSegment`.

A sudden jump after `/model` is not a bug: the percentage is `current_tokens / context_window_size`, so moving from a 1M window to a 200K one keeps the tokens and shrinks the denominator 5x. `CLAUDE_HOOKS_DEBUG=1` makes the status line write the last stdin JSON to `${state_dir}/statusline.last_input.json` (may contain paths and the last assistant message).

Width: `COLUMNS` is the full terminal width, the usable width is smaller (registered `padding`, the rightmost cell), so `WIDTH_SAFETY_MARGIN` is 8 columns since v6.3.1 (was 4) and `statusline install` registers `padding: 0`. Claude Code exports `COLUMNS` from v2.1.153; before that the script falls back to 80. The subscription plan name is not exposed to status line scripts, so it is not rendered. Codex's status line is not command-backed (openai/codex#17827 asks for it); echook only curates the fixed `[tui]` item lists by surgical text edit (`_codex_apply_tui_array`), never round-tripping the TOML.

## Cursor integration

Two paths with different runtime invariants.

- **Auto-bridge.** Cursor 3.2.16+ scans `~/.claude/plugins/installed_plugins.json` on every workspace open and registers the plugin's `hooks/hooks.json` events as its own. It invokes the cached `runner/run.py`, but does not inject `CLAUDE_PLUGIN_DATA` and does not pass Claude Code's stdin schema: Cursor's own payload has camelCase event names and fields such as `cursor_version`, `conversation_id`, `final_status`, `duration_ms`, `is_background_agent`, `workspace_roots`, `model`, `error_message`, plus compat fields `session_id`, `hook_event_name`, `transcript_path`.
- **Native.** `audio-hooks install --cursor` writes `~/.cursor/hooks.json` from `cursor-hooks/hooks.json`, substituting `{{PYTHON}}` and `{{HOOK_RUNNER}}` with absolute paths; Windows backslashes must be JSON-escaped before substitution (pre-5.1.6 produced invalid JSON). Entries carry `"_managed_by": "audio-hooks"`, which scopes `uninstall --cursor`.

Bridge mapping (Cursor's responsibility, per cursor.com/docs/reference/third-party-hooks): PreToolUse, PostToolUse, UserPromptSubmit (`beforeSubmitPrompt`), Stop, SubagentStop, SessionStart, SessionEnd, PreCompact bridge. `Notification` and `PermissionRequest` have no Cursor equivalent. `subagentStart`, `postToolUseFailure` and `afterFileEdit` are Cursor-native with no Claude Code equivalent, so only the native template registers them. Tool names: `Bash` -> `Shell`, `Edit` -> `Write`; `Glob` / `WebFetch` / `WebSearch` matchers never fire under Cursor.

Facts the code relies on:

- `detect_invoker()` reads `CURSOR_VERSION`, `CLAUDE_PLUGIN_DATA`, `CLAUDE_PLUGIN_ROOT`; Cursor wins when both are set.
- `_resolve_data_dir()` order: `CLAUDE_PLUGIN_DATA` -> `CLAUDE_AUDIO_HOOKS_DATA` -> plugin-cache detection -> `~/.claude/plugins/data/<id>/` (if `user_preferences.json` exists) -> `~/.cursor/audio-hooks-data/` (if it exists) -> legacy temp dir. The Cursor branch is the 5.1.4 anti-stranding fix; `<id>` is `audio-hooks-chanmeng-audio-hooks`.
- On a Cursor `session_start` the runner prints `{"env": {"CLAUDE_PLUGIN_DATA": "<path>"}}`; per Cursor's docs `sessionStart` env outputs propagate to every later hook in the session.
- Runtime guards, before any firing: Cursor + `notification` / `permission_request` logs `skipped_no_cursor_equivalent`; Cursor + `duplicate_bridge_forced: true` in `install_marker.json` logs `DUPLICATE_BRIDGE_RUNTIME_SKIP`.
- Webhook payloads under Cursor carry `invoker: "cursor"` and a `cursor` sub-object; `user_email` is redacted unless `webhook_settings.include_user_email` is set (the URL may be third-party). Every NDJSON event has an `invoker` field.
- Guard: `tests/test_cursor_bridge.py`.

## Single source of truth for preferences (5.1.5)

`hooks/user_preferences.py` is the only code that reads or writes `user_preferences.json`; `hook_runner.py` and `bin/audio-hooks.py` consolidated onto it after dual implementations drifted and produced the 5.1.4 anti-stranding bug. Backups go to `<data>/user_preferences.json.bak` and `~/.claude-audio-hooks-backups/<plugin_id>/<ts>.json`, the latter outside `~/.claude/plugins/data/` so `claude plugin uninstall` cannot wipe them. Codex support (5.2.x) needed `hooks/invoker.py`, extracted so `user_preferences.py` can ask which editor invoked it without a circular import.

## Script installs and the split runner

A script install made before 7.0.0 has only `hook_runner.py`, `user_preferences.py`, `invoker.py` and `.project_path` in `~/.claude/hooks/`, and its self-update copies nothing but `hook_runner.py`, so the modules that file was split into never arrive there. `hook_runner.py` therefore runs `_add_checkout_hooks_for_script_install()` before importing them: when `runner_core.py` is not beside it, it reads `.project_path` (BOM-tolerant; MSYS/WSL/Cygwin forms on Windows) and inserts `<checkout>/hooks` on `sys.path` directly after its own directory. `user_preferences` and `invoker` still load from the installed copies, as before the split (they resolve the data directory from their own location); the checkout supplies them only when the install has none. Nothing happens for plugin, Cursor/Codex or in-place runs, and it never raises. This is why that function and the pure `_posix_drive_path_to_windows` sit above the imports and use nothing from a sibling. Guard: `tests/test_script_install_bootstrap.py`.

## Adding a new hook event

Skipping a step produces a silent no-op, not an error; the contract tests are what make a break loud.

1. `hooks/hook_runner.py`: `DEFAULT_AUDIO_FILES` and `CUSTOM_AUDIO_FILES` (two tables; both need the entry); a branch in `get_notification_context()` in `hooks/notification_context.py` (a missing one degrades silently to `hook_type.replace("_"," ").title()`; for a `Notification` subtype use `NOTIFICATION_TYPE_LABELS` in `hook_runner.py` instead); `TERMINAL_SEQUENCE_FORBIDDEN_EVENTS` in `hooks/terminal_sequence.py` if the event reads a hook's stdout; `_CURSOR_UNSUPPORTED` / `_CODEX_UNSUPPORTED` if that editor has no equivalent; `SYNTHETIC_EVENT_MAP` if matcher-scoped.
2. A variant of an on-by-default parent needs `SYNTHETIC_VARIANT_DEFAULTS[<variant>] = False` and a `UserPreferences.VARIANT_INTRODUCED` row.
3. `bin/audio-hooks.py`: `HOOK_CATALOG`; optionally `_MOCK_STDIN`.
4. `config/default_preferences.json`: `enabled_hooks` (canonical events only, must equal `HOOK_CATALOG[].default`) and `tts_settings.messages`; `config/_defaults_baseline.json` (a new key is fine, a flipped one fails `test_defaults_stability.py`).
5. `config/audio_manifest.json`: two entries (default and custom theme), then `ELEVENLABS_API_KEY=... python scripts/generate-audio.py --only <files>`. Variants need their own sound too (`TestAudioUniqueness`).
6. Templates: `plugins/audio-hooks/hooks/hooks.json` (hand-edited); `cursor-hooks/hooks.json` and `codex-hooks/hooks.json` only if the editor has the event (Codex commands must carry `--invoker codex`); `codex-hooks/plugin-hooks.json` separately, with `${PLUGIN_ROOT}`.
7. `LEGACY_HOOK_EVENTS` in `bin/echook_cli/legacy_uninstall.py` (the script installer was removed in 7.0.0; `tests/test_legacy_scripts_contract.py` still requires the uninstaller's list to cover every registered event).
8. `bash scripts/build-plugin.sh`, `python bin/audio-hooks.py test <hook>`, `python -m unittest discover tests`.
9. Counts in docs are not touched by `bump-version.sh`; update them by hand. Bump the version and write the CHANGELOG entry.

Verify the event's real semantics before trusting its name: v6.3.4 was an emergency rollback because `WorktreeCreate` is a provider hook that hijacked worktree creation. Register a capture shim, exercise it, and record the result in `docs/EVENT_BEHAVIOR_NOTES.md`.

## See also

- [AGENTS.md](../AGENTS.md), [INVARIANTS.md](INVARIANTS.md), [EVENT_BEHAVIOR_NOTES.md](EVENT_BEHAVIOR_NOTES.md), [STATUS_LINE.md](STATUS_LINE.md), [CHANGELOG.md](../CHANGELOG.md)
- `audio-hooks manifest` for subcommands, hooks, config keys, `error_codes`, `editor_targets`.
