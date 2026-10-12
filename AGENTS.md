# echook — agent guide

echook does two things for Claude Code, Cursor and Codex: **(1) audio and out-of-band notification** of editor lifecycle events (sound, spoken summary, desktop toast, webhook) and **(2) the status line**. Live truth for every subcommand, hook, config key, error code and env var is `audio-hooks manifest`; this file only holds what that JSON and the code cannot tell you.

<critical>
1. **`audio-hooks` is the only interface.** JSON in, JSON out, stable error codes. Never hand-edit `user_preferences.json`; use `audio-hooks set <dotted.key> <value>`.
2. **Scope guard.** Anything that is neither a notification nor a status-line segment is out of scope by design (wellness, pomodoro/timers, gamification, opening URLs, side-commands). Decline and say why instead of implementing it.
3. **No human-interactive paths.** No prompts, menus or "edit this file" instructions. A step an agent cannot do (`/reload-plugins`) is emitted as machine-readable `next_steps`.
4. **After editing a canonical directory run `bash scripts/build-plugin.sh`.** CI runs `--check` and fails on drift.
5. **Work on a branch.** Every push to `master` is scanned by Anthropic's plugin directory, so `master` must stay releasable.
6. **Read the matching section of `docs/INVARIANTS.md` before changing an area listed below.** Each rule there cost an incident to learn.
</critical>

## Map

| Path | What it is |
|---|---|
| `hooks/` | Runtime, flat modules. `hook_runner.py` is the entry: event tables, gating (enabled, snooze, filters, debounce), `run_hook`, `main`. `runner_core.py` owns shared state (preferences handle, NDJSON log, queue dir, invoker lookup). One concern each: `audio_playback.py`, `desktop_notification.py`, `text_to_speech.py`, `webhook_dispatch.py`, `rate_limit_alerts.py`, `terminal_sequence.py`, `notification_context.py` (wording, sanitising), `platform_shell.py` (PowerShell launch and escaping, WSL). Call across modules as `module.name(...)`, never `from module import name`. Also `user_preferences.py` (config load, migration, data-dir resolution) and `invoker.py` (which editor called). |
| `bin/` | `audio-hooks.py` is the CLI entry: dispatch, config, state detection, small commands, manifest. Larger concerns are one module each in `bin/echook_cli/` (`legacy_uninstall`, `editor_installs`, `statusline`, `plugin_upgrade`, `config_backup`, `error_codes`, …; its `__init__.py` lists them). Add a concern as a new module there, not to the entry file. Also the two status-line renderers and the launchers. |
| `config/` | `default_preferences.json` (the template every install migrates toward), `audio_manifest.json` (prompt per sound file). |
| `audio/default/`, `audio/custom/` | Voice theme and chime theme; one file per event and variant. |
| `cursor-hooks/`, `codex-hooks/` | Install templates for those editors. |
| `plugins/audio-hooks/` | The shipped plugin. Its `audio`, `bin`, `config`, `cursor-hooks`, `codex-hooks` directories and the Python files in its `hooks` directory are a **build mirror — never edit**. Hand-edited in place: `plugins/audio-hooks/hooks/hooks.json` (the Claude Code registration; no repo-root counterpart), `runner`, `skills`, `evals`, `README.md` and the two plugin manifests. |
| `scripts/` | `build-plugin.sh`, `bump-version.sh`, `generate-audio.py`. The script installer was removed in 7.0.0; `audio-hooks uninstall` still removes an old script install. |
| `tests/` | `unittest` suite; every module imports `_isolation` first. |

## Commands

- Tests: `python -m unittest discover tests` (not pytest). CI: `.github/workflows/smoke.yml`, three OSes × Python 3.9/3.12/3.13.
- Sync the mirror: `bash scripts/build-plugin.sh` (`--check` to verify).
- Release: `bash scripts/bump-version.sh <version>`, then follow `docs/RELEASING.md`.
- Verify an install: `audio-hooks status`, `audio-hooks diagnose`, `audio-hooks test all`.
- Running the CLI from a checkout: set `CLAUDE_PLUGIN_DATA` to a scratch directory first, or it resolves the real install's data.

## Before you change… read

| Area | `docs/INVARIANTS.md` section | Guard |
|---|---|---|
| `hooks.json`, a new event or matcher | Upstream editor behaviour; Events, variants and sounds | `tests/test_plugin_hooks_contract.py` |
| A new variant | Events, variants and sounds (`SYNTHETIC_VARIANT_DEFAULTS`, `VARIANT_INTRODUCED`, a distinct sound in both themes) | `tests/test_variant_migration.py` |
| `run_hook` order, escaping, notification dispatch, a runner module | Hook runner | `tests/test_filter_debounce_order.py`, `tests/test_desktop_notification.py`, `tests/test_runner_modules.py` |
| A CLI subcommand or flag | CLI contract (`--help` side-effect-free, unknown arguments are `INVALID_USAGE`, reporting commands are read-only, every code catalogued) | `tests/test_read_only_commands.py`, `tests/test_cli_error_codes.py` |
| Install / uninstall | `docs/INSTALLATION_GUIDE.md`; CLI contract | `tests/test_install_arg_safety.py`, `tests/test_uninstall_scripts_native.py` |
| Config keys, migration, version stamps | Configuration and migration | `tests/test_migration.py`, `tests/test_bump_version.py` |
| Cursor or Codex paths | Upstream editor behaviour | `tests/test_cursor_bridge.py`, `tests/test_codex_hooks.py` |
| Anything under `plugins/audio-hooks/` | Repository and release; `docs/DIRECTORY_LISTING.md` | `tests/test_plugin_packaging.py` |
| A new test module | Tests | `tests/test_isolation_guard.py` |
| A user says audio fires too often, or stopped | `stop` does not mean "task complete" | — |

## Other documents (open on demand)

- `docs/EVENT_BEHAVIOR_NOTES.md` — what Claude Code's events actually do, measured. Read before trusting an event name.
- `docs/ARCHITECTURE.md` — design decisions and the checklist for adding an event.
- `docs/STATUS_LINE.md` — track 2. `docs/TROUBLESHOOTING.md` — symptoms whose cause is upstream.
- `docs/PROJECT_STATUS.md` — what is waiting, unverified, or deliberately not done. Read before proposing work; update it in the same commit as anything it lists.
- `docs/RELEASING.md`, `docs/DIRECTORY_LISTING.md` — release mechanics and the rules the directory listing puts on the plugin folder.
- `plugins/audio-hooks/skills/audio-hooks/SKILL.md` — how to map a user's plain-language request to commands.
- `plugins/audio-hooks/README.md`, `PRIVACY.md` — disclosures. Change them in the same commit as any change to what the plugin runs, sends or writes.
