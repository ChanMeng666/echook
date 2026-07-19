# echook — AI Operator Guide

> v6.4.0 · Multi-platform: Claude Code (plugin) · Cursor (native + auto-bridge) · Codex (plugin + native). Source-of-truth for every capability is `audio-hooks manifest` (live JSON, includes `pointers`, `editor_targets`, `supported_editors`). This file is orientation only.

<critical>
1. **`audio-hooks` CLI is the only interface.** Single Python binary, JSON output, stable error codes. Never hand-edit `user_preferences.json` — use `audio-hooks set <dotted.key> <value>`.
2. **Run `audio-hooks manifest` first** for any non-trivial task. It returns the live list of subcommands, hooks, config keys, error codes, env vars, `editor_targets`, and `pointers` (paths to SKILL/README/ARCHITECTURE/etc). Anything you want to know about this project is one command away.
3. **After editing `/hooks/`, `/bin/`, `/audio/`, `/config/`, `/cursor-hooks/`, or `/codex-hooks/`, run `bash scripts/build-plugin.sh`** to sync into `/plugins/audio-hooks/`. CI runs `--check` and fails on drift.
4. **Scope guard (two tracks only).** echook does exactly two things: **(1) audio + out-of-band notification** of editor lifecycle events — telling a user *what happened* when they can't see the Claude window (sound at the desk, spoken summary when away, glanceable desktop toast / webhook when in another app), and **(2) the status line**. Anything that is neither a notification nor a status-line segment is **out of scope by design**: wellness/breathing exercises, pomodoro/timers, gamification, opening URLs, or running side-commands during a session. The `focus_flow` feature was removed in v6.0.0 for this reason. If asked to add such a feature, push back and explain it's intentionally not part of echook rather than implementing it.
5. **AI-agent-first: no human-interactive paths.** Every operation is a non-interactive `audio-hooks` subcommand (JSON in, JSON out) or a non-interactive script. There are **no** human menus, prompts, or `curl | bash` flows — the install/uninstall scripts never prompt and emit machine-readable `next_steps` for the rare step an agent can't do (e.g. `/reload-plugins`). Do not add interactive scripts, `read -p` prompts, or "run this menu" instructions, and do not tell the user to manually edit files — drive everything through the CLI. (The human-only `configure.sh` / `test-audio.sh` / `snooze.sh` / `diagnose.py` / `quick-*` scripts were removed in v6.0.0.)
</critical>

## Install commands

| Platform | Command |
|---|---|
| Claude Code | `claude plugin marketplace add ChanMeng666/echook` → `claude plugin install audio-hooks@chanmeng-audio-hooks` → **ask the user to type `/reload-plugins`** (REPL-only, no CLI equivalent — do not fake it via Bash). |
| Cursor (native) | `audio-hooks install --cursor`. Aborts with `DUPLICATE_BRIDGE` if the Claude Code plugin is already installed (Cursor 3.2.16+ auto-bridges it — double-fire). Pass `--force` only if the user accepts the trade-off; runtime guard `DUPLICATE_BRIDGE_RUNTIME_SKIP` then suppresses the native path. |
| Codex | Plugin path: `codex plugin marketplace add ChanMeng666/echook` → `codex plugin add audio-hooks@chanmeng-audio-hooks` → ask the user to reload plugins if the REPL requires it. Native hooks.json path: `audio-hooks install --codex`; only follow `next_steps` when `feature_flag_state` is `disabled`, `disabled_legacy`, or `parse_error`. The install never round-trips user TOML. |

Verify with `audio-hooks status` + `audio-hooks diagnose` + `audio-hooks test all`.

## Hook events and matcher variants (v6.4)

**37 canonical events + 30 matcher variants.** A variant is one matcher value of a matcher-scoped event — `notification` has 8 (`notification_idle_prompt`, `notification_agent_completed`, …), `stop_failure` 8, `session_start`/`session_end` 4 each, `precompact`/`postcompact`/`setup` 2 each. Since v6.4 each is independently switchable; before that they all shared their parent's single flag.

- Variant keys are ordinary booleans in `enabled_hooks`, alongside canonical names. No nesting, no schema change.
- `audio-hooks hooks list --variants` to enumerate; `hooks enable|disable|enable-only` accept variant names.
- Live truth: `audio-hooks manifest` → `variants` and `variant_gating`.

**Gating precedence** (`hook_runner.is_hook_enabled(hook, variant)`), highest first — the second rule is the one that surprises people:

1. explicit `enabled_hooks[<variant>]`
2. `enabled_hooks[<parent>] is false` — **hard kill switch for every variant under it**
3. per-variant default (`SYNTHETIC_VARIANT_DEFAULTS`)
4. explicit `enabled_hooks[<parent>] is true`
5. built-in default set: `notification`, `stop`, `permission_request`

To keep exactly one variant of a muted parent, set that variant key explicitly — rule 1 outranks rule 2. Never reach for the parent to express "all but one".

**Registration chain.** `hooks.json` matcher `X` → command arg `<parent>_X` → `SYNTHETIC_EVENT_MAP["<parent>_X"]` → `(canonical, audio_override)`. Nothing enforces this at runtime — an unresolvable arg falls through `_resolve_synthetic_event` and the event silently becomes a permanent no-op. `tests/test_plugin_hooks_contract.py` is what makes a break loud; run it after touching any of those three surfaces.

## `stop` does not mean "task complete"

The single most common user complaint is audio firing too often, and it is almost always `stop`. It maps to Claude Code's `Stop`, which fires at the **end of every turn**; the payload carries **no field** distinguishing a final turn from an intermediate one, so no configuration can make it mean "the work is done". Say this rather than tuning debounce and hoping. Real fixes, in order: `hooks enable-only notification permission_request` (fires only when the user must act — `idle_prompt` is the genuine "waiting for you" signal); `filters.stop.skip_if_background_tasks_running true` (v6.4, stays quiet while teammates/subagents are still running); debounce last.

## Tests, CI, and version bumps

- **Run tests:** `python -m unittest discover -v tests` (292 tests). NOT pytest — no `pyproject.toml` / `pytest.ini`.
- **CI:** `.github/workflows/smoke.yml` — Ubuntu/Windows/macOS × Python 3.9/3.12/3.13, plus `bash scripts/build-plugin.sh --check`.
- **Bump version:** `bash scripts/bump-version.sh <new_version>` — rewrites all 8 canonical version locations and runs `build-plugin.sh`. Idempotent. Outputs JSON with `files_changed` and `next_steps`.

## Pointers (also exposed as `audio-hooks manifest.pointers`)

- **Natural-language → CLI mapping:** `plugins/audio-hooks/skills/audio-hooks/SKILL.md` (auto-loaded on audio-related prompts — covers the full decision tree).
- **Status line (both editors):** `docs/STATUS_LINE.md` — the complete reference for track 2 (Claude Code renders 29 segments; Codex curates a fixed item list). Live truth: `audio-hooks statusline segments` / `audio-hooks statusline codex show`.
- **Observed event behaviour:** `docs/EVENT_BEHAVIOR_NOTES.md` — what Claude Code's hook events actually do, measured, where that differs from or is absent from the upstream docs. Consult before trusting an event name.
- **Human docs:** `README.md`, `docs/INSTALLATION_GUIDE.md`, `CHANGELOG.md`, `docs/ARCHITECTURE.md`, `docs/TROUBLESHOOTING.md`.
- **Canonical sources:** `/hooks/`, `/bin/`, `/audio/`, `/config/`, `/cursor-hooks/`, `/codex-hooks/`. `/plugins/audio-hooks/{audio,bin,hooks,config,cursor-hooks,codex-hooks}/` mirror these — never edit by hand. `plugin.json`, `runner/run.py`, `skills/` are hand-edited under `/plugins/audio-hooks/` directly.

## Silent-bite gotchas

- **Cursor does not inject `CLAUDE_PLUGIN_DATA`** when bridging — `UserPreferences._resolve_data_dir()` in `hooks/user_preferences.py` is the fallback chain. Do not assume the env var exists.
- **Codex sets no `CODEX_VERSION` env var.** Invoker detection uses the `--invoker codex` CLI flag baked into the Codex install template, parsed by `hooks/invoker.py`.
- **Claude Code maps all 37 canonical events plus 30 matcher variants; Cursor (native: 19 of 37 — incl. granular per-tool shell/MCP/file events; auto-bridge: 8 coarse) and Codex (10 of 37) have smaller hook surfaces and no variant support.** The runner no-ops unsupported events with `skipped_no_*_equivalent` debug NDJSON. Live mapping: `audio-hooks manifest` → `supported_editors`.
- **Windows paths in install templates must be JSON-escaped** (`D:\path` → `D:\\path`). 5.1.6 fix; covered by `tests/test_codex_hooks.py` and `tests/test_cursor_bridge.py`.
- **`plugins/audio-hooks/hooks/hooks.json` is hand-edited and has no repo-root counterpart.** It sits inside an otherwise generated directory and `build-plugin.sh` does **not** sync it, so it looks generated but isn't. Edit it in place; `build-plugin.sh` will not carry your change and will not warn you.
- **A new variant of an on-by-default parent needs `SYNTHETIC_VARIANT_DEFAULTS[<variant>] = False`.** Otherwise merely registering the matcher starts making noise on every existing install the moment it ships. New events are opt-in; new variants are too.
- **Verify an event's real semantics empirically before relying on its name.** v6.3.4 was an emergency rollback because `WorktreeCreate` turned out to be a *provider* hook that hijacked worktree creation. Observed-vs-documented behaviour for Claude Code's events — including payload fields the docs omit and matchers that never fire — lives in `docs/EVENT_BEHAVIOR_NOTES.md`. Read it before adding or trusting an event.
