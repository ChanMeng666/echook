# Changelog

All notable changes to **echook** (formerly *Claude Code Audio Hooks*) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> Historical entries below this point use the project's previous name. They are preserved verbatim as a record of what was shipped at the time. The rename to **echook** landed in 5.2.1 — see that entry for the full mitigation guidance.

## [Unreleased]

## [6.3.2] - 2026-06-30

### Changed

- **Rate-limit reset clocks now show the date when the reset isn't today.** A bare time was ambiguous for the 7-day *weekly* window (it can reset days out), so `weekly_quota` now renders e.g. `resets Jul 4 5am`. The always-soon 5-hour `api_quota` stays a bare time unless its reset crosses midnight onto another day. `_fmt_reset_clock()` gained a `with_date` flag (and an injectable `now` for tests); the date prefix appears only when the reset falls on a different local calendar day.

## [6.3.1] - 2026-06-30

### Fixed

- **Status line truncation on emoji-dense rows** (`Theme: Chim…`). A packed row landing exactly on the width budget could still be clipped by Claude Code because emoji render a hair wider than `_vwidth()` estimates and the terminal reserves the edge. `WIDTH_SAFETY_MARGIN` raised from 4 to 8 so a boundary row wraps to the next physical row instead of being truncated. Users on an unusually narrow terminal can still pin the exact width via `statusline_settings.max_width`.

## [6.3.0] - 2026-06-29

Status-line release: the Claude Code status line grows from 14 to **29 segments** (every useful field Claude Code pipes to a status line script), and echook gains the ability to **curate the Codex status line** so it stops truncating with an ellipsis.

### Added

- **15 new Claude Code status line segments** (verified against code.claude.com/docs/en/statusline), each individually toggleable and most self-omitting when their data is absent:
  - Line 1: `session_name`, `agent`, `thinking`, `vim`, `output_style`, `repo`
  - Line 2: `git_dirty` (cached `git status --porcelain`), `worktree`, `pr`, `added_dirs`, `tokens` (cache-hit ratio), `exceeds_200k`, `duration`, `api_time`, `burn_rate` ($/hour)
- **`statusline_settings.hidden_segments`** — a blacklist applied when `visible_segments` is empty, so a user can drop a few segments from the comprehensive default without enumerating every keeper.
- **`audio-hooks statusline segments`** — JSON catalog of every segment (name, line, source field, conditional) for discovery and configuration.
- **`audio-hooks statusline codex show|preview|apply`** — curate Codex's `[tui].status_line` **and `terminal_title`** in `config.toml` (`--target status_line|terminal_title|both`). Codex's status line is **not command-backed** (it accepts only fixed built-in item IDs; command rendering is open feature request openai/codex#20140), so echook curates the fixed lists rather than rendering custom text. Presets — status line: `minimal` (4) / `balanced` (8, recommended) / `full` (14); terminal title: `minimal` (2) / `balanced` (4) / `full` (6), all de-duplicated. `apply` backs up `config.toml` first and surgically edits *only* the targeted array(s), preserving all other tables, comments, and formatting.

### Changed

- The status line default (empty `visible_segments`) now shows the full 29-segment catalog; richer segments render only when Claude Code supplies their data, so a plain session stays uncluttered while a rich one shows the full picture. `visible_segments` whitelisting is unchanged and back-compatible.

## [6.2.0] - 2026-06-26

Complete-the-hook-surface release: every lifecycle event the three editors now document is mapped to echook's two tracks. **13 new canonical events (26 → 39 total)**, all opt-in (default off). Codex was already complete at 10 events and is unchanged.

### Added

- **Four new Claude Code events** (verified against code.claude.com/docs/en/hooks):
  - `setup` — first-run/maintenance setup finished (Claude Code `Setup`, matchers `init`/`maintenance`). Designed for the away-from-desk case: get pinged when a long `claude --init` or maintenance run is ready. Routed via synthetic variants `setup_init` / `setup_maintenance`.
  - `user_prompt_expansion` — a typed command/skill expanded into a prompt (`UserPromptExpansion`).
  - `post_tool_batch` — a batch of parallel tool calls resolved (`PostToolBatch`).
  - `message_display` — an assistant message was displayed (`MessageDisplay`; very noisy, 10s hook timeout).
- **Nine new Cursor events with per-tool-type sounds** (verified against cursor.com/docs/hooks). The native `audio-hooks install --cursor` template now maps Cursor's **granular** execution events so shell, MCP, and file-read each get a *distinct* sound — something the coarse `preToolUse`/`postToolUse` umbrella cannot do, and especially valuable because Cursor has no `Notification`/`PermissionRequest` hook:
  - `shell_before` / `shell_after` (`beforeShellExecution` / `afterShellExecution`)
  - `mcp_before` / `mcp_after` (`beforeMCPExecution` / `afterMCPExecution`)
  - `file_read` (`beforeReadFile` + `beforeTabFileRead`)
  - `agent_response` (`afterAgentResponse`), `agent_thinking` (`afterAgentThought`)
  - `workspace_open` (`workspaceOpen`), `tab_file_edit` (`afterTabFileEdit`)
- New `get_notification_context()` cases for all 13 events (read `command` / `tool_name` / `file_path` / `text` / `duration_ms` / `workspace_roots` from each event's payload), plus `enabled_hooks`, `tts_settings.messages`, `DEFAULT_AUDIO_FILES`, and `CUSTOM_AUDIO_FILES` entries.
- 26 new audio files (13 voice + 13 chime), each generated from a bespoke ElevenLabs prompt recorded in `config/audio_manifest.json` — distinct sounds per event (e.g. shell vs MCP vs file-read).

### Changed

- **Cursor native template (`cursor-hooks/hooks.json`) rewritten for granular per-type events.** The coarse `preToolUse` / `postToolUse` are **no longer registered natively** — they would double-fire with the granular events. Write/Edit are still surfaced after-the-fact via `afterFileEdit → file_changed`, and Task tools via `subagentStart`. (The Claude Code auto-bridge subset is unchanged; this affects only the native `--cursor` install, which is for Cursor users without Claude Code.)
- `manifest.supported_editors`: `claude-code.events` now lists 39; added `cursor.native_events_subset` (19 events) and extended `codex.unsupported_events` with all 13 new events (Codex has no equivalent).
- Invoker gating in `hook_runner.py`: the four Claude-Code-only events are skipped under the Cursor invoker, and all 13 new events are skipped under the Codex invoker, each with the existing `skipped_no_*_equivalent` debug NDJSON.

### Note

- **echook consumes these events to *notify* only.** Cursor's and Claude Code's block/modify powers (`deny` / `ask` / `updatedInput` / `followup_message`) are deliberately ignored — using them to steer or gate the agent would break the two-track scope (notification + status line).
- All 26 new audio files were generated with the project's standard ElevenLabs voice (Jessica) and sound-effect pipeline, matching the existing themes; regenerate any of them via `scripts/generate-audio.py --force --only <filename>`.

## [6.1.0] - 2026-06-26

### Added

- **Status line pins the Claude Code startup banner.** Four new banner-parity segments, on by default, surface the facts that scroll off the top of the terminal as a session grows:
  - `weekly_quota` — the headline *"You've used 82% of your weekly limit · resets 9pm"* (Claude Code's 7-day `rate_limits.seven_day` window), with a color-coded bar and local reset clock time. Self-omits for non-subscribers.
  - `cc_version` — Claude Code's own version (`⚡ CC v2.1.193`), distinct from echook's `🔊 echook v…`.
  - `effort` — the model's reasoning effort level (`🧠 high`), shown next to the model segment.
  - `cost` — session spend and lines changed (`💲 $0.42 +156/-23`) from Claude Code's `cost` object.
  - The existing 5-hour `api_quota` segment now also shows its reset clock time, for symmetry with `weekly_quota`.
  - The status line now has **14 segments** (was 10); all are shown by default and remain freely combinable via `statusline_settings.visible_segments`.
- **Width-aware reflow — no more truncation.** Each line now reflows into as many physical rows as the terminal width needs, wrapping only at segment boundaries, so a content-rich status line is shown **in full** instead of being cut off with an ellipsis (`Webho…`, `+743/-…`). Width comes from the `COLUMNS` env var Claude Code sets (v2.1.153+), falling back to 80, minus a 4-column safety margin (`WIDTH_SAFETY_MARGIN`) — `COLUMNS` reports the full width but the line's `padding` and the terminal's reserved edge cell shrink the *usable* width, so packing against raw `COLUMNS` overfilled the last row. The status line is now registered with `padding: 0` (was `1`) to use the full width. New `statusline_settings.max_width` key (default `0` = auto) pins the width explicitly. Implemented via new `_vwidth()` (ANSI/emoji-aware visible-width), `_pack_lines()` (greedy boundary packing), and `_terminal_width()` helpers. The fix applies automatically on the next refresh; re-running `audio-hooks statusline install` additionally adopts `padding: 0`.
- `_fmt_reset_clock()` helper in `bin/audio-hooks-statusline.py` — formats a Unix-epoch `resets_at` as a banner-style local clock time (`9pm` / `9:30pm`).
- `tests/test_statusline.py` — new test classes for each segment plus reset-clock formatting and junk-input robustness.

### Note

- The subscription **plan name** ("Claude Max"/"Pro") shown in the banner is *not* piped to status line scripts by Claude Code, so it is intentionally not rendered. The presence of the quota bars already implies a Claude.ai subscription.

## [6.0.0] - 2026-06-23

Refocus release: echook is now a two-track tool — **audio + out-of-band notification** and the **status line** — and nothing else. Features that distracted the user or sat outside those tracks were removed; the notification features that serve away-from-screen and window-switching users were kept and hardened instead of cut.

### Removed (BREAKING)

- **Focus Flow removed entirely** (`focus_flow.*` config, `scripts/focus-flow.py`, `scripts/focus-flow-tasks/`, the `start_focus_flow`/`stop_focus_flow` lifecycle in `hook_runner.py`, and the `focus` status-line segment). The breathing/hydration/url/command micro-tasks were anti-distraction wellness features outside echook's scope. Existing `focus_flow` keys in a user's `user_preferences.json` are silently ignored by the non-destructive migration — no action needed.
- **Dead `playback_settings.queue_enabled` / `playback_settings.max_queue_size`** config keys removed. They were never read by the runner. `debounce_ms` is unchanged.
- The status line now has **10 segments** (was 11); the `focus` segment is gone.
- **Legacy bash hook runtime removed** — the nine `hooks/*_hook.sh` event scripts and `hooks/shared/hook_logger.sh`. No install path ever registered them: the script tier registers `hook_runner.py` (Python) and the (since-removed) lite tier wrote self-contained inline commands. The bash hooks were only copied as cruft and were a latent security surface (they parsed Claude's hook JSON in shell). `install-complete.sh` no longer copies them, and its self-test now verifies `hook_runner.py`. `uninstall.sh` still removes the old `*_hook.sh` files from `~/.claude` so existing installs clean up fully.
- **AI-agent-first purge of human-interactive scripts.** Removed the human-only interactive menus and legacy CLI duplicates — `scripts/configure.sh`, `scripts/test-audio.sh`, `scripts/snooze.sh`, `scripts/diagnose.py`, the now-orphaned `hooks/shared/hook_config.sh`, and the `curl | bash` "lite tier" (`scripts/quick-setup.sh`, `scripts/quick-configure.sh`, `scripts/quick-unsetup.sh`). All are fully covered by non-interactive `audio-hooks` subcommands (`set`, `hooks`, `theme`, `test all`, `snooze`, `diagnose`). The obsolete `INTERACTIVE_SCRIPT` error code (only those menus emitted it) is gone; `DUAL_INSTALL_DETECTED` remains and its suggested command is now `audio-hooks uninstall`.

### Fixed

- `install-complete.sh` and `install-windows.ps1` now copy `hook_runner.py`'s sibling modules (`user_preferences.py`, `invoker.py`) alongside it; the full-tier script install previously copied only `hook_runner.py`, which imports both and would fail at runtime without them.

### Changed

- **TTS reply-reading (`speak_assistant_message`) hardened, not removed.** Claude's reply is now routed through a new shared sanitizer before it is spoken: fenced/inline code is replaced with a spoken marker, markdown is stripped, secrets (API keys, tokens, JWTs, `key=value` credentials) are redacted, and truncation lands on a sentence/word boundary instead of mid-word. Away-from-screen users keep their spoken outcome without code or credentials being read aloud.
- **Verbose desktop notifications (`detail_level: verbose`) hardened, not removed.** Tool commands and reply summaries shown in toasts pass through the same sanitizer, so a verbose toast no longer dumps a raw command or a leaked secret. `file_path` details collapse to a basename. Window-switchers keep the rich glance, safely.
- **Installers are now always non-interactive.** `install-complete.sh` and `uninstall.sh` no longer have any human prompt or TTY branch — they always run unattended (uninstall preserves config + audio unless `--purge`). The `--yes` flag is accepted as a no-op for backward compatibility. This makes the whole project operable end-to-end by an AI agent with zero human interaction.

### Added

- New `_clean_for_output()` / `_redact_secrets()` helpers in `hooks/hook_runner.py`: a deterministic, offline (no LLM/network) text sanitizer shared by the TTS and verbose-notification paths.
- `tests/test_sanitize.py` — 17 unit tests covering code stripping, markdown stripping, secret redaction, and boundary truncation. (Previously TTS and verbose notifications had no test coverage.)
- `llms.txt` — an AI-agent entrypoint at the repo root that orients an LLM/agent to operate the project via the `audio-hooks` CLI (`manifest`-first), complementing the existing `AGENTS.md`.

### Documentation

- Documented the two-track scope guard in `CLAUDE.md` and `SKILL.md` so AI operators decline out-of-scope (wellness/timer/gamification) requests by design.

## [5.3.0] - 2026-06-22

### Added

- New Claude Code status line segment `cwd` (the 11th segment) that shows the current working directory on Line 1, right after `model`. Helps users running many terminals / Claude Code sessions tell at a glance which project a session belongs to, avoiding cross-project prompt mix-ups. The path is abbreviated for the status bar: the home directory collapses to `~`, and long paths are shortened to `<root>…<last folder>` (e.g. `D:\…\claude-code-audio-hooks`) via the new `_abbrev_path()` helper, which degrades silently on unexpected input.
- The `cwd` value is read from the stdin `cwd` field Claude Code provides, falling back to `workspace.current_dir` then `workspace.project_dir`.
- `cwd` is part of the default segment set (shown when `statusline_settings.visible_segments` is empty) and can be toggled individually, e.g. `audio-hooks set statusline_settings.visible_segments '["cwd","context"]'`.
- Added `tests/test_statusline.py::TestAbbrevPath` and `::TestCwdSegment` covering path abbreviation, the workspace fallback chain, default visibility, and exclusion.

### Documentation

- Updated `SKILL.md`, `docs/ARCHITECTURE.md` (10 → 11 segments), `README.md`, and the `statusline_settings` config comment to document the `cwd` segment.

## [5.2.2] - 2026-06-12

### Changed

- Updated Codex support for the current hook model verified against Codex CLI 0.139.0: Codex now registers 10 events (`SessionStart`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `UserPromptSubmit`, `SubagentStart`, `SubagentStop`, `Stop`) instead of the older 6-event set.
- Added a Codex plugin manifest at `plugins/audio-hooks/.codex-plugin/plugin.json` plus a plugin-specific hook template at `codex-hooks/plugin-hooks.json` using `${PLUGIN_ROOT}/runner/run.py`. This prevents Codex from falling back to the Claude Code `hooks/hooks.json` file, whose `async: true` handlers are not supported by Codex.
- Changed Codex feature handling to match default-on hooks. `audio-hooks install --codex` no longer creates or rewrites `~/.codex/config.toml`; it only reports `next_steps` when `[features].hooks = false` disables hooks or the TOML cannot be parsed. The legacy `codex_hooks` key is still recognized.
- Codex notifications and webhook labels now display `Codex` instead of `Claude Code` when the runner is invoked with `--invoker codex`.
- Added Codex plugin packaging tests that validate `.codex-plugin/plugin.json`, the Codex plugin hook template, `${PLUGIN_ROOT}` commands, and the absence of Claude-only `${CLAUDE_PLUGIN_ROOT}` references from Codex hook entries.
- Expanded `scripts/bump-version.sh` so releases update the Codex plugin manifest and `codex-hooks/plugin-hooks.json` version metadata alongside the existing Claude, Cursor, and native Codex files.

### Documentation

- Updated README, installation, architecture, troubleshooting, skill, and AI-operator docs to describe the current Claude Code, Cursor, and Codex install paths accurately.
- Added `AGENTS.md` as the Codex-facing operator guide, kept in sync with `CLAUDE.md`.

## [5.2.1] - 2026-05-05

### Renamed: `claude-code-audio-hooks` → `echook`

The repository, the display name in docs, and the status-line brand string are now **echook** (Echo + Hook → /ˈɛkˌhʊk/, always lowercase). The project supports Claude Code, Cursor IDE, and Codex CLI — leading with "Claude Code" in the name was misleading newcomers into thinking it was Claude-Code-locked.

**This is a door-only rename — the machinery is unchanged.** Concretely:

- `audio-hooks` CLI command — **unchanged**
- `audio-hooks-statusline` companion — **unchanged**
- `chanmeng-audio-hooks` plugin marketplace name — **unchanged**
- `audio-hooks` plugin slug — **unchanged**
- `~/.cursor/audio-hooks-data/`, `~/.codex/audio-hooks-data/`, and `${CLAUDE_PLUGIN_DATA}` paths — **all unchanged**
- `user_preferences.json` content + location — **untouched**

So no user data is migrated, no config is rewritten, no install needs to be redone.

### Existing plugin users — your install keeps working

GitHub auto-redirects `github.com/ChanMeng666/claude-code-audio-hooks` to the new URL with HTTP 301, so `git clone`, `git pull`, and Claude Code's marketplace fetch all continue to work transparently. Stars, watchers, forks, open issues, and open PRs are auto-migrated by GitHub.

If `claude plugin update` ever fails to follow the redirect, refresh the marketplace source pointer manually:

```text
/plugin marketplace remove chanmeng-audio-hooks
/plugin marketplace add ChanMeng666/echook
```

The plugin slug, CLI command, and state directory are unchanged, so this is purely a metadata refresh — no reinstall, no reconfiguration, no data loss.

### Existing local clones — optional cleanup

```bash
git remote set-url origin https://github.com/ChanMeng666/echook.git
```

Old URL keeps working via redirect, so this is cosmetic.

### Changed

- **All display-name occurrences** in README, CLAUDE.md, docs/, SKILL.md, plugin/marketplace metadata, status-line brand string, installer script banners, and config-file `_comment`/`_description` fields rewritten from `Claude Code Audio Hooks` (or `Audio Hooks` standalone) to `echook`.
- **All URL slugs** rewritten from `ChanMeng666/claude-code-audio-hooks` to `ChanMeng666/echook`. The promo-video sub-repo (`ChanMeng666/echook-promo-video`, separately renamed from `claude-code-audio-hooks-promo-video` shortly after this release) is a distinct repository — both old URLs continue to resolve via GitHub's automatic HTTP 301 redirect.
- **Logo asset** renamed: `public/claude-code-audio-hooks-logo.svg` → `public/echook-logo.svg`.
- **GitHub repository description** now leads with `🔊 echook —` and a topic tag `echook` is added.

### Preserved (deliberately)

- CHANGELOG entries for 5.2.0 and earlier — preserved verbatim as a historical record.
- `LICENSE` copyright header — incidental project name in legal text, untouched.
- Archived planning docs in `docs/plans/` and `docs/specs/` — preserved as records of what was planned at the time.

## [5.2.0] - 2026-05-04

> **Codex CLI users: you can now operate this project AI-first end-to-end.** Paste a single prompt into Codex (or any AI agent that can run shell commands): *"Clone the repo, run `audio-hooks install --codex`, and follow any next_steps in the JSON output."* The install authors a fresh `~/.codex/config.toml` with `[features].codex_hooks = true` when none exists, and emits machine-readable `next_steps` for the calling AI agent to follow up when an existing one needs editing. No human-only steps in the entire flow.

Codex CLI compatibility on top of 5.1.6's Cursor adaptation. Codex (per [developers.openai.com/codex/hooks](https://developers.openai.com/codex/hooks)) does NOT auto-bridge Claude Code plugins — independent investigation of `openai/codex` confirmed no code reads `~/.claude/plugins/`. Consequence: no `DUPLICATE_BRIDGE` problem to solve; the install path is single and simple. 33 new bridge-contract tests (135 total, all green on Windows). New `hooks/invoker.py` module extracted from `hook_runner.py` so the data-dir resolver can ask "which IDE invoked us?" without a circular import. The `hook_runner` runtime gains a Codex-specific guard that no-ops the 18 audio-hooks canonical events with no Codex equivalent.

### Added

- **`audio-hooks install --codex` subcommand.** Reads `codex-hooks/hooks.json`, substitutes `{{PYTHON}}` (`python`/`python3`) and `{{HOOK_RUNNER}}` (absolute path with Windows backslashes JSON-escaped), tags every entry with `_managed_by: "audio-hooks"`, and merges into `$CODEX_HOME/hooks.json` (default `~/.codex/hooks.json`). Existing user-authored entries are preserved by tag — same merge semantics as `install --cursor`. Re-running is idempotent: prior managed entries are stripped before fresh ones are written.
- **`audio-hooks uninstall --codex` subcommand.** Filters out `_managed_by: "audio-hooks"` entries from `~/.codex/hooks.json`, preserves any user-authored hooks, deletes the file if no foreign content remains. Preserves `~/.codex/audio-hooks-data/user_preferences.json` by default; `--purge` removes that directory too. **Never touches `~/.codex/config.toml`** — the `codex_hooks` feature flag may benefit other Codex hook plugins.
- **AI-first feature-flag handling.** Codex hooks require `[features]\ncodex_hooks = true` in `~/.codex/config.toml`. The install:
  - **Authors a fresh config.toml** with the flag enabled when the file doesn't exist (`feature_flag_state: "freshly_written"`, safe — we own the whole file).
  - **Skips silently** when the flag is already true (`feature_flag_state: "already_enabled"`).
  - **Emits a `next_steps` JSON instruction** when the file exists but the flag is missing or false (`feature_flag_state: "section_missing"` or `"flag_missing_or_false"`), so the calling AI agent can add the flag with its Edit tool. We never round-trip user-authored TOML — formatting and comments would be destroyed.
- **`codex-hooks/hooks.json` template.** Registers the 6 events Codex supports: `SessionStart` (matcher `startup|resume|clear`), `PreToolUse` / `PostToolUse` / `PermissionRequest` (matcher `Bash|apply_patch|mcp__.*`), `UserPromptSubmit`, `Stop`. Every command bakes in a `--invoker codex` CLI flag because Codex sets no env var we could detect by (unlike Cursor's `CURSOR_VERSION`). Plugin-layout copy synced into `plugins/audio-hooks/codex-hooks/`.
- **`hooks/invoker.py` module.** New `_parse_invoker_arg`, `detect_invoker`, `get_invoker` (cached), `strip_invoker_args`, `_reset_cache`. `detect_invoker` checks argv first (`--invoker codex` / `--invoker=codex`), then env vars (`CURSOR_VERSION` → cursor, `CLAUDE_PLUGIN_DATA` → claude-code), then falls back to `"unknown"`. The cache is primed in `hook_runner.main()` before argv stripping so downstream callers (notably `user_preferences._resolve_data_dir`) see the right answer even after the `--invoker` pair is removed from `sys.argv`.
- **Codex-gated step in `UserPreferences._resolve_data_dir()`.** New priority 3 (between the env-var overrides and the plugin-cache layout): when invoker is `"codex"` AND `$CODEX_HOME/audio-hooks-data/user_preferences.json` exists, return that path. Sits ahead of the Claude Code shared dir so a developer machine that happens to have both Claude Code and Codex installed still lands at the right dir under Codex sessions.
- **Runtime no-op guard for unsupported events under Codex.** `hook_runner.run_hook` now skips the 18 audio-hooks canonical events with no Codex equivalent (`notification`, `subagent_*`, `precompact`/`postcompact`, `worktree_*`, `elicitation*`, `cwd_changed`, `file_changed`, `task_*`, `teammate_idle`, `config_change`, `instructions_loaded`, `permission_denied`, `session_end`) when `_get_invoker() == "codex"`. Emits a `skipped_no_codex_equivalent` debug NDJSON event and returns 0 cleanly.
- **`editor_targets.codex` block in `audio-hooks status` / `manifest`.** Reports `state` (`active` / `active-but-flag-disabled` / `active-but-flag-unknown` / `inactive`), `hooks_file`, `config_path`, `feature_flag_enabled`, `data_dir`. Surfaces a `CODEX_FEATURE_FLAG_MISSING` warning when the install is in place but the flag isn't enabled — actionable for the calling AI agent.
- **`codex: {...}` sub-object in webhook payloads.** When `invoker == "codex"`, the raw payload includes a `codex` sub-object with `turn_id`, `tool_use_id`, `permission_mode`, `tool_response`, `stop_hook_active` (parallel to `cursor: {...}`). Schema stays at `audio-hooks.webhook.v1` — additive, no breaking changes.
- **`CODEX_HOME` environment variable** documented and respected by both the install command and the data-dir resolver. Defaults to `~/.codex` when unset.
- **33 new bridge-contract tests in `tests/test_codex_hooks.py`.** Across 8 TestCase classes: invoker detection from argv (4 cases including the `--invoker=codex` form and invalid-value fallback), `strip_invoker_args` correctness, `_resolve_data_dir` Codex fallback (positive + negative gating), template validity (all 6 events, every command carries `--invoker codex`, every canonical handler is known), unsupported-events no-op (all 18), NDJSON `invoker` field, webhook codex sub-object, install end-to-end (writes hooks.json, substitutes paths, seeds user_preferences, writes install_marker, idempotent, preserves foreign entries), feature-flag handling (4 states), uninstall (removes managed, preserves foreign, preserves data dir by default, `--purge`, never touches config.toml), `editor_targets.codex` end-to-end. **135/135 tests pass on Windows.**
- **README.md "Codex CLI — Native Install" section.** Single agent prompt that does the entire `git clone + install --codex + next_steps` flow end-to-end. Plus uninstall recipe and the natural-language-control note that all *Just Say It* prompts work under Codex too.
- **`docs/INSTALLATION_GUIDE.md` Codex CLI section.** Mirrors README's flow plus the feature-flag-handling table, the `--purge` semantics, and the "no separate `audio-hooks upgrade --codex` subcommand" callout (the existing `upgrade` targets Claude Code's plugin cache).
- **CLAUDE.md "Codex CLI compatibility (5.2.0+)" section** with bridge mapping table, install/uninstall mechanics, AI-first feature-flag handling, invoker-detection design notes, stdin-field-mapping note (snake_case shape identical to Claude Code so `parse_stdin` works natively), and an explicit limitations list (no env propagation, no `Notification`/`SubagentStop` events, project-scope install out of scope for v1, no Codex plugin packaging).
- **CLAUDE.md decision tree** gains 4 new entries covering install / uninstall / status / silence-debugging for Codex.

### Changed

- **`.claude-plugin/marketplace.json` and `plugins/audio-hooks/.claude-plugin/plugin.json`** now advertise Codex compatibility (`"codex"` + `"codex-cli"` + `"openai-codex"` keywords, description updated to "Audio notifications + AI-controlled config for Claude Code, Cursor IDE, and Codex CLI events.").
- **`hooks/hook_runner.py: detect_invoker` / `_get_invoker`** are now thin re-exports from `hooks/invoker.py`. Test refactor required: `tests/test_cursor_bridge.py::_load_module` now also pops `invoker` and `user_preferences` from `sys.modules` so the cache resets cleanly between tests. Without this, the cache from one test leaked into the next and produced unstable failures depending on collection order.
- **`scripts/build-plugin.sh`** now syncs `codex-hooks/` and `hooks/invoker.py` into the plugin layout. `--check` mode catches drift on either.

### Out of scope for 5.2.0

- **Project-scope install** (`<repo>/.codex/hooks.json`). User-scope only is the v1 design — mirrors how `--cursor` works. Users wanting per-repo audio config can hand-edit `<repo>/.codex/hooks.json` themselves.
- **Codex plugin packaging** (`~/.codex/plugins/audio-hooks/`). Codex has its own plugin system but we haven't packaged audio-hooks for it; the hooks.json install is sufficient for v1. If a future Codex release reads `CLAUDE_PLUGIN_DATA` automatically the way Cursor does, we'd consider this.
- **Auto-editing existing `~/.codex/config.toml`.** Too risky for user-authored TOML — TOML round-trip with comment preservation is hard, and getting it wrong destroys user formatting. The `next_steps` AI-readable instruction is the right contract for v1.

## [5.1.6] - 2026-05-02

> **Cursor user? You can now operate this project AI-first end-to-end on either install path.** README has a dedicated *Cursor IDE — Same Project, Two Install Paths* section, the marketplace metadata advertises Cursor support, and a `git clone + python bin/audio-hooks install --cursor` flow gets you running on Cursor without Claude Code in two prompts. The Windows install bug surfaced in 5.1.5 (paths like `D:\github\...` produced invalid JSON during template substitution and aborted with `INTERNAL_ERROR`) is fixed.

Cursor IDE adaptation completeness pass on top of 5.1.5's painless-upgrade work. Discoverability gaps closed (README, marketplace.json, plugin.json, INSTALLATION_GUIDE.md), 19 new bridge-contract tests added, two runtime guards landed in `hook_runner.run_hook` (Notification/PermissionRequest no-op under Cursor, runtime double-fire suppression for `--force` installs), Windows JSON-escape bug in `_install_cursor` fixed, new stable error code `DUPLICATE_BRIDGE_RUNTIME_SKIP` exposed via the manifest, Cursor-only upgrade recipe documented in CLAUDE.md and SKILL.md.

### Fixed

- **`audio-hooks install --cursor` produced invalid JSON on Windows** — `bin/audio-hooks.py:_install_cursor` substituted `hook_runner_abs` (e.g., `D:\github\claude-code-audio-hooks\hooks\hook_runner.py`) directly into the JSON template at `cursor-hooks/hooks.json`, causing `\g`, `\h`, etc. to be interpreted as invalid JSON escapes. Install aborted with `INTERNAL_ERROR: Template is not valid JSON after substitution`. Now JSON-escapes backslashes and double quotes before substitution; verified end-to-end on Windows + the new test `test_install_writes_hooks_json_with_substituted_paths` exercises the substitution path under pytest. POSIX users were never affected because `/usr/...` paths contain no characters JSON treats as escapes.

### Added

- **`hooks/hook_runner.py: ErrorCode.DUPLICATE_BRIDGE_RUNTIME_SKIP`** — new stable error code, surfaced via `audio-hooks manifest`'s `error_codes` block. Emitted by the runtime guard added below. Its `suggested_command` is `audio-hooks uninstall --cursor`, which is the correct one-shot recovery for an operator who used `--force` on top of an active Claude Code bridge.
- **Runtime guard in `run_hook()` for Notification + PermissionRequest under Cursor.** Per [cursor.com/docs/reference/third-party-hooks](https://cursor.com/docs/reference/third-party-hooks), Cursor's bridge maps 8 of 10 Claude Code events; `Notification` and `PermissionRequest` have no Cursor equivalent. The runner now emits a `skipped_no_cursor_equivalent` debug NDJSON event and returns 0 cleanly when invoked under Cursor. Today this never matters because Cursor never invokes them, but it locks down the contract against hand-edited `~/.cursor/hooks.json` files and against future Cursor releases that might add equivalents. Regression-guarded: a sibling test confirms the no-op does NOT fire under Claude Code invoker.
- **Runtime double-fire suppression when `install --cursor --force` was used over an active Claude Code bridge.** New `_read_install_marker()` helper reads `${data_dir}/install_marker.json` once per process. When invoker is Cursor and the marker records `duplicate_bridge_forced: true`, the runtime emits a `duplicate_bridge_runtime_skip` warn-level event with `error.code: "DUPLICATE_BRIDGE_RUNTIME_SKIP"` and returns 0 — Claude Code's bridge fires alone, audio plays exactly once. Operators who genuinely want both paths active can remove the marker; `audio-hooks status` already warns them they are in this state.
- **README.md "Cursor IDE — Same Project, Two Install Paths" section.** Path A (auto-bridge with Claude Code) gets a one-line verification prompt; Path B (Cursor only) gets a single agent prompt that does the entire `git clone + install --cursor` end-to-end. Plus an upgrade recipe (`git pull && install --cursor`, idempotent and preserves `user_preferences.json`), an uninstall table, and an explicit "already have Claude Code? Don't run Path B" callout citing the `DUPLICATE_BRIDGE` guard.
- **`docs/INSTALLATION_GUIDE.md` Cursor IDE section.** Mirrors README's two paths, plus the `audio-hooks uninstall --cursor` flow with `--purge` and `_managed_by` semantics, plus a note that there is intentionally no `audio-hooks upgrade --cursor` subcommand (the existing `upgrade` targets Claude Code's plugin cache; conflating the two scopes would be a footgun).
- **CLAUDE.md + SKILL.md Cursor-only upgrade recipe.** Spells out `cd ~/audio-hooks && git pull && python bin/audio-hooks install --cursor` for AI agents operating on a user's behalf. Existing Claude-Code-targeted `audio-hooks upgrade` left unchanged.
- **19 new unit tests in `tests/test_cursor_bridge.py`** across 6 new TestCase classes: template validity (all 8 bridgeable events present, every command arg resolves to a real handler), `_resolve_data_dir` Cursor fallback regression, Notification/PermissionRequest no-op invariant (positive + negative cases), `install --cursor` end-to-end (substituted paths, seeded prefs, install_marker, idempotent re-run), `DUPLICATE_BRIDGE` detection + `--force` override, uninstall preserves user prefs/foreign entries (with `--purge` semantics), runtime double-fire suppression. **102/102 tests pass on Linux + Windows + macOS** (the 32 in `test_cursor_bridge.py` plus 70 elsewhere). The new tests caught the Windows JSON-escape bug fixed above.

### Changed

- **`.claude-plugin/marketplace.json` description and keywords** now advertise Cursor compatibility (`"cursor"` + `"cursor-ide"` keywords, "Auto-bridges to Cursor IDE 3.2.16+; native install for Cursor-only via 'audio-hooks install --cursor'." sentence appended). Also fixes pre-existing version drift: was at 5.1.3 while everything else was at 5.1.5.
- **`plugins/audio-hooks/.claude-plugin/plugin.json`** mirrors the marketplace.json description + keywords change.
- **`cursor-hooks/hooks.json _audio_hooks_version`** bumped from 5.1.5 to 5.1.6 so an uninstall can identify which release wrote the entries it is removing.

### Verified against current Cursor docs (no code change needed)

- `subagentStart`, `postToolUseFailure`, `afterFileEdit` — all confirmed real Cursor-native events via WebFetch of [cursor.com/docs/hooks](https://cursor.com/docs/hooks). They are absent from Cursor's bridge mapping (only 8 events bridge from Claude Code) but valid native targets, which is why `cursor-hooks/hooks.json` registers them for the Path B install. CLAUDE.md now documents this distinction explicitly.

### Compatibility

- **No config schema change.** `config/default_preferences.json` `_version` and `config/_defaults_baseline.json` are intentionally still at 5.1.5 — no new config keys, no default flips, so the auto-migration logic introduced in 5.1.5 has nothing to migrate. Existing users who upgrade from 5.1.5 to 5.1.6 see zero changes in their `user_preferences.json`.
- **No Cursor user action required.** Path A users (auto-bridge): refresh via `audio-hooks upgrade` to pick up the new runtime guards. Path B users: `cd ~/audio-hooks && git pull && python bin/audio-hooks install --cursor` (idempotent; preserves your preferences).

## [5.1.5] - 2026-05-01

> **⚠️ For users who got bit by 5.1.4.** If your `audio_hooks` was reinitialised under 5.1.4 (e.g., via a `claude plugin uninstall` without `--keep-data`) and you now hear `subagent_stop` / `permission_denied` / `task_created` audio you didn't want, run: `audio-hooks hooks disable subagent_stop permission_denied task_created`. Migration logic from 5.1.5 forward will preserve your choice across all future upgrades.

Painless upgrades. New `UserPreferences` class as single source of truth eliminates the dual-implementation bug class. `audio-hooks upgrade` wraps `claude plugin update`/`uninstall+install` with `--keep-data`. Auto-migration on load preserves user values when new keys are added in future versions. Dual-location backups (`~/.claude/plugins/data/<id>/user_preferences.json.bak` for last-good + `~/.claude-audio-hooks-backups/<id>/<ts>.json` for disaster recovery, rotation=20). New `audio-hooks backup list/show/restore/prune` subcommands. New `config/_defaults_baseline.json` + `tests/test_defaults_stability.py` enforce a no-default-flip policy at CI. The 5.1.4 default flips for `subagent_stop`, `permission_denied`, `task_created` are reverted to false.

### Fixed

- **Existing users no longer lose configuration on `claude plugin uninstall + install`** (the path 5.1.4 documented as "do this once to refresh the cache"). The new `audio-hooks upgrade` subcommand drives that flow with `--keep-data` automatically.
- **Default value flips between versions are now CI-enforced policy violations.** `tests/test_defaults_stability.py` snapshots `config/default_preferences.json` into `config/_defaults_baseline.json`; flipping any existing scalar default fails the test until the baseline is also updated.
- **5.1.4 default flips reverted.** `enabled_hooks.subagent_stop`, `enabled_hooks.permission_denied`, `enabled_hooks.task_created` go back to `false`. Existing users who explicitly set these to `true` in 5.1.4 are unaffected (migration preserves user values).

### Added

- **`hooks/user_preferences.py`** — single source of truth for user_preferences.json. ~350 lines. Owns path resolution (6-level chain), load with auto-migration, save with auto-backup, atomic writes guarded by cross-platform file lock, dual-location backup management, `diff_from_default()` for surfacing user customizations, lazy `get_prefs()` singleton.
- **Auto-migration on load.** When `user_preferences.json` `_version` differs from the bundled template's, deep-merge missing keys without overwriting existing user values. Lists are atomic (user list wins entirely). Scalar-vs-container type mismatch resets to template default. `_version` and comment fields always overwrite from template. Migration is logged to NDJSON as `action: config_migrated` with `from_version`, `to_version`, `added_keys`, `backup_id`.
- **Dual-location backups on save.** Every save snapshots the prior file content to `<data_dir>/user_preferences.json.bak` (last good, overwritten on each save) AND to `~/.claude-audio-hooks-backups/<plugin_id>/<ts>.json` (disaster recovery, kept outside `~/.claude/plugins/data/` so `claude plugin uninstall` cannot wipe it). Rotation: keep 20 most recent. Dedup: skip backup when content is byte-identical to the latest. Atomic write via `os.replace()`. Cross-platform file lock (`fcntl.flock` POSIX, `msvcrt.locking` Windows) on `<data_dir>/.user_prefs.lock` prevents concurrent saves from racing.
- **`audio-hooks upgrade [--check-only] [--force]`** — JSON CLI verb that replaces the manual "uninstall + install" two-step. Detects scope via `claude plugin list --json`. Tries `claude plugin update` first; falls back to `uninstall --keep-data + install`. Crash-mid-upgrade leaves a marker at `~/.claude-audio-hooks-backups/.upgrade_in_progress.json` with full recovery instructions. On success, calls `prefs.load()` to trigger automatic migration.
- **`audio-hooks backup list / show / restore / prune`** — JSON-emitting backup management. `restore` itself stamps a backup of the current state before overwriting (so a wrong restore is reversible). Magic IDs: `latest`, `latest-sibling`, `latest-external`, plus exact ISO timestamp for external backups.
- **`status` / `manifest` output extensions.** New `customizations` block (output of `prefs.diff_from_default()`) shows only the keys where the user differs from defaults. New `last_migration` block (latest `config_migrated` NDJSON event, if any).
- **New stable error codes.** `BACKUP_FAILED`, `BACKUP_NOT_FOUND`, `RESTORE_FAILED`, `LOCK_TIMEOUT`, `NOT_INSTALLED`, `UPGRADE_UNINSTALL_FAILED`, `UPGRADE_REINSTALL_FAILED`, `UPGRADE_VERIFY_FAILED`, `PRIOR_UPGRADE_INCOMPLETE`. Each carries a `hint` and `suggested_command` so AI can self-recover.
- **40+ new unit tests** in `tests/test_user_preferences.py`, `tests/test_migration.py`, `tests/test_backups.py`, `tests/test_backup_cli.py`, `tests/test_upgrade_command.py`, `tests/test_defaults_stability.py`. All stdlib-only.

### Refactored

- `hooks/hook_runner.py` and `bin/audio-hooks.py` consolidated onto `UserPreferences`. Deleted ~200 lines of duplicated helpers (`_resolve_plugin_data_dir`, `_is_running_from_plugin`, `_auto_init_user_prefs`, `_apply_plugin_option_overlay`, plus the diverged `_resolve_config_file`/`_config_path` pair). Module-level globals (`CONFIG_FILE`, `QUEUE_DIR`) removed; consumers now call `_prefs().config_path` etc. This eliminates the dual-implementation bug class that caused 5.1.4 to need a follow-up patch.

## [5.1.4] - 2026-05-01

> **⚠️ Cursor users — read this first.** If you have audio-hooks installed in Claude Code, Cursor IDE 3.2.16+ already auto-bridges this project's hooks (per [cursor.com/docs/reference/third-party-hooks](https://cursor.com/docs/reference/third-party-hooks)) — Cursor's *"Cursor Hooks Service"* loads `~/.claude/plugins/installed_plugins.json` on startup and calls our hook scripts on its own session events. This means **even if you uninstalled and reinstalled Claude Code's audio-hooks**, Cursor was probably still calling the *old cached* version of `runner/run.py`. To pick up the 5.1.4 fix, run inside Claude Code: `/plugin uninstall audio-hooks@chanmeng-audio-hooks` then `/plugin install audio-hooks@chanmeng-audio-hooks`. That refreshes `~/.claude/plugins/cache/chanmeng-audio-hooks/audio-hooks/<ver>/` to the new code Cursor will then bridge.

Cursor IDE compatibility. The runner now finds the user's real `user_preferences.json` whether it is invoked from Claude Code (which sets `CLAUDE_PLUGIN_DATA`) or from Cursor's auto-bridge (which does not). A new `audio-hooks install --cursor` subcommand registers natively for users who run Cursor without Claude Code. NDJSON events and webhook payloads now carry `invoker` (`claude-code` / `cursor` / `unknown`) plus a `cursor` sub-object surfacing Cursor-specific stdin fields (`conversation_id`, `reason`, `final_status`, `duration_ms`, ...). 13 new unit tests pin the contract.

### Fixed

- **Cursor IDE was playing the wrong audio theme even after the user switched themes in Claude Code** ([reported in chat by ai@gavigo.com](#)). Cursor's auto-bridge invokes the cached plugin's `runner/run.py` *without* setting `CLAUDE_PLUGIN_DATA`, so `_resolve_config_file()`'s legacy fallback chain in 5.1.3 returned the cached `default_preferences.json` (which ships `audio_theme: "default"`) instead of the user's actual `~/.claude/plugins/data/audio-hooks-chanmeng-audio-hooks/user_preferences.json` (which they had set to `"custom"`). The runner now has a centralized `_resolve_data_dir()` whose priority chain falls through to the well-known shared Claude Code data dir before the temp-dir fallback, so Cursor and Claude Code read the same preferences file. The change is backwards-compatible: behavior is unchanged when `CLAUDE_PLUGIN_DATA` *is* set.

### Added

- **`hooks/hook_runner.py: _resolve_data_dir()`** — single source of truth for the audio-hooks state directory, used by `get_log_dir`, `_resolve_queue_dir`, and `_resolve_config_file`. Priority: `CLAUDE_PLUGIN_DATA` → `CLAUDE_AUDIO_HOOKS_DATA` → `~/.claude/plugins/data/audio-hooks-chanmeng-audio-hooks/` (if `user_preferences.json` exists) → `~/.cursor/audio-hooks-data/` (if `user_preferences.json` exists) → legacy temp dir. Centralizing the chain meant the four call sites that previously had hand-coded fallbacks now agree on one definition.
- **`hooks/hook_runner.py: detect_invoker()`** — returns `"claude-code"` / `"cursor"` / `"unknown"` based on environment variables (`CURSOR_VERSION` for Cursor — set by Cursor's bridge per [cursor.com/docs/hooks](https://cursor.com/docs/hooks); `CLAUDE_PLUGIN_DATA`/`CLAUDE_PLUGIN_ROOT` for Claude Code). Env-var detection is more reliable than parsing stdin because it survives malformed payloads.
- **`session_start` hook auto-emits `{"env": {"CLAUDE_PLUGIN_DATA": "<path>"}}` to stdout when invoker is Cursor.** Per [cursor.com/docs/hooks](https://cursor.com/docs/hooks), `sessionStart` env outputs propagate to every subsequent hook in the same Cursor session — so after this one-time injection, `stop`, `sessionEnd`, `preToolUse`, etc. all see the correct preferences path without depending on the runtime fallback. The handler emits unconditionally regardless of `enabled_hooks.session_start` because env propagation is a session-setup concern, not a notification concern. Claude Code path is unaffected (no env JSON is emitted because `CURSOR_VERSION` is unset).
- **`bin/audio-hooks install --cursor`** — writes `~/.cursor/hooks.json` from `cursor-hooks/hooks.json` (new canonical template, schema v1, camelCase event names). Substitutes `{{PYTHON}}` and `{{HOOK_RUNNER}}` with absolute paths at install time, merges with any existing user hooks (each managed entry tagged `"_managed_by": "audio-hooks"` so we can find and remove only ours later), seeds `~/.cursor/audio-hooks-data/user_preferences.json` from `default_preferences.json`, and writes `~/.cursor/audio-hooks-data/install_marker.json`. Aborts with stable error code `DUPLICATE_BRIDGE` when Claude Code's audio-hooks plugin is already installed (because Cursor's auto-bridge would fire every event a second time); pass `--force` to install anyway.
- **`bin/audio-hooks uninstall --cursor`** — removes only the `_managed_by: "audio-hooks"` entries, preserves any other user hooks, and deletes `~/.cursor/hooks.json` entirely if it would be empty. Default keeps `~/.cursor/audio-hooks-data/` so re-install picks up the user's preferences; `--purge` removes that directory too.
- **`audio-hooks status` / `diagnose` / `manifest` output `editor_targets`** — a per-editor JSON block reporting `{state, via, ...}` for `claude-code` and `cursor`. Cursor states: `active` / `bridged-via-claude-code` / `native` / `double-registered` / `inactive`. The `double-registered` state surfaces a `DUPLICATE_BRIDGE` warning explaining what to fix.
- **`webhook_settings.include_user_email`** (default `false`) — opt-in flag for whether `user_email` from Cursor's stdin schema is included in webhook payloads. Off by default because the webhook URL may be a third-party service.
- **`tests/test_cursor_bridge.py`** — 13 unit tests covering: `detect_invoker` env-var matrix, `_resolve_data_dir` priority chain, `session_start` env-output for Cursor (and silence for non-Cursor), NDJSON `invoker` field on every event, webhook payload `invoker` + `cursor` sub-object, `user_email` redaction by default, opt-in surfacing.
- **`cursor-hooks/hooks.json`** — canonical Cursor IDE hooks template. Maps 11 supported Cursor events to our hook names. `stop` and `subagentStop` set `loop_limit: 0` to defensively prevent any accidental auto-resubmission via `followup_message` (Cursor's loop_limit defaults to 5 for native hooks). Documents `Notification` and `PermissionRequest` as deliberately absent — Cursor has no equivalent events ([cursor.com/docs/reference/third-party-hooks](https://cursor.com/docs/reference/third-party-hooks)).

### Documented (known limitations)

- **Cursor's bridge maps 8 of 10 Claude Code hooks.** `Notification` and `PermissionRequest` have no Cursor equivalent — those audio cues will never fire from Cursor. Use Claude Code if you depend on them.
- **`Glob` / `WebFetch` / `WebSearch` matchers do not fire under Cursor** — Cursor lacks these tool names, so `pretooluse` / `posttooluse` matchers configured for them are silently skipped by Cursor.
- **The only Cursor-side opt-out is the global "Third-party skills" toggle** in Cursor Settings — this disables auto-bridging for *all* Claude Code plugins, not just audio-hooks. There is no per-plugin Cursor opt-out today.
- **Cursor caches the Claude Code plugin code** under `~/.claude/plugins/cache/<id>/<ver>/`. Editing this project's source under `D:\github_repository\...` does not propagate to Cursor until the user runs `/plugin uninstall` + `/plugin install` inside Claude Code (or otherwise refreshes the cache). This is documented in the warning at the top of this entry.

## [5.1.3] - 2026-04-28

Status line context segment now shows absolute token counts so the percentage stops being misleading after `/model` switches. Diagnostic JSON dump for the status line input. New unit-test suite wired into CI.

### Fixed

- **Status line context segment was opaque after `/model` switches between context-window variants** ([#16](https://github.com/ChanMeng666/claude-code-audio-hooks/issues/16), reported by [@ChanMeng666](https://github.com/ChanMeng666)). The percentage Claude Code calculates is `current_tokens / context_window_size`, so switching from a 1M-context variant (e.g. `claude-opus-4-7[1m]`) to a 200K window (e.g. default `claude-sonnet-4-6`) keeps your tokens the same but shrinks the denominator 5× — going from `Context: 17%` to `Context: 83%` (or, with more context, the reported `97%`) is **mathematically correct**, not a bug in either Claude Code or this project. But the status line gave no signal of the underlying numbers, leaving users to guess. The Context segment now appends absolute counts (e.g. `Context: 83% (166K/200K) 🛑 /compact`). The numerator is derived from `used_percentage × context_window_size` so it stays consistent with the percentage Claude Code computed; we explicitly do NOT use the `total_input_tokens` field from the status line JSON because in cache-heavy sessions like Claude Code itself it counts only literal input tokens (excluding `cache_read_input_tokens` / `cache_creation_input_tokens`) and is off by 30× in practice. When `context_window_size` is missing, malformed, or non-positive, the segment falls back silently to the pre-5.1.3 form `Context: 83%`.

### Added

- **`CLAUDE_HOOKS_DEBUG=1` (or `true`/`yes`, case-insensitive) now also dumps the status line stdin JSON** to `${state_dir}/statusline.last_input.json` via atomic temp-file rename. Used to diagnose what Claude Code is actually piping to the script (e.g. confirming whether `context_window_size` updated after a `/model` change). The truthy-value parsing matches `hook_runner.py` for consistency. The dump may include workspace paths, transcript path, and the last assistant message — a privacy note in `CLAUDE.md` instructs users to disable the env var when not actively diagnosing. Failures during dump are swallowed so diagnostics can never break status line rendering.
- **`tests/test_statusline.py`** — 25 unit and integration tests, stdlib-only, wired into the existing `.github/workflows/smoke.yml` import-smoke matrix (Ubuntu / Windows / macOS × Python 3.9 / 3.12 / 3.13 = 9 jobs). Coverage:
  - `_fmt_tokens` edge cases (0, sub-1K, exact 1K, exact 1M, fractional M)
  - Robustness: empty stdin, malformed JSON, `null` `context_window`, string `used_percentage`, string / zero / negative `context_window_size` — none must crash
  - Context segment correctness: the user's empirical 17%/83% scenario, red-threshold `/compact` hint, fallback when no `context_window_size`
  - **Regression guard** that the script does NOT use `total_input_tokens` as the numerator (the bug surfaced and reverted during this release cycle)
  - `CLAUDE_HOOKS_DEBUG` toggle parity with `hook_runner` (`1`/`true`/`yes` enable; `0`/`false`/`no`/anything else disable)
  - Atomic-rename hygiene (no `.tmp` files left behind in the state dir)
- **`CLAUDE.md` decision tree** gains entries for "context jumped to 83% / 97% after I switched models" (explains it is expected) and "diagnose what Claude Code is sending to the status line" (points at `CLAUDE_HOOKS_DEBUG`). The env-var table updates `CLAUDE_HOOKS_DEBUG` to mention both the NDJSON log and the status line dump, and adds the privacy note.

### Changed

- **`bin/audio-hooks-statusline.py:_maybe_dump_session`** uses an atomic write (`os.replace` of a per-PID tempfile) so concurrent status line invocations cannot leave a half-written `statusline.last_input.json`. The truthy parsing was tightened from strict `"1"` to `{1, true, yes}` (case-insensitive) to match `hook_runner.DEBUG`.

## [5.1.2] - 2026-04-20

Windows audio-playback fix. Every default clip >= ~3.0 s was silently truncated on Windows and WSL because all four PowerShell snippets in `play_audio_windows` and `play_audio_wsl` used a **fixed** `Start-Sleep -Seconds 3` (4 s for WSL) before calling `$player.Stop(); $player.Close()`. The bundled `audio/default/permission-request.mp3` is ~3.4 s — users heard the last ~0.4 s cut off every time Claude Code asked for permission. `elicitation.mp3` (~3.1 s) was also clipped; `subagent-start.mp3` and `notification-urgent.mp3` were on the edge.

### Fixed

- **`hooks/hook_runner.py` — `play_audio_windows` and `play_audio_wsl`** ([#14](https://github.com/ChanMeng666/claude-code-audio-hooks/issues/14), reported by [@Basdanucha](https://github.com/Basdanucha)). All four sites (PowerShell `-Command`, PowerShell `-File` heredoc, WMPlayer.OCX COM, WSL `-Command`) now poll the media player for the actual clip length and sleep for `duration + 500 ms` tail buffer before tearing down the player. For PresentationCore MediaPlayer, we poll `$player.NaturalDuration.HasTimeSpan` with a 1.5 s ceiling (Open() is async), then use `TotalMilliseconds`. For WMPlayer.OCX, we poll `$w.currentMedia.duration`. If the media never reports a duration (corrupt file, etc.), we fall back to `Start-Sleep -Seconds 10` — generous enough to cover any plausible default clip, still bounded so the PowerShell host process doesn't leak. The Python subprocess layer was already fire-and-forget (`subprocess.Popen`) — the fix closes the gap that was *inside* the PowerShell command string. macOS `afplay` and Linux `mpg123`/`ffplay`/`paplay`/`aplay` are unaffected: those players block until playback completes by default.

## [5.1.1] - 2026-04-18

Critical import-time crash fix plus regression-prevention CI. Everyone on v5.0.3 or v5.1.0 should upgrade.

### Fixed

- **`hook_runner.py` crashed on import with `NameError: name 'Tuple' is not defined`** ([#10](https://github.com/ChanMeng666/claude-code-audio-hooks/issues/10)). The file used `Tuple` in module-level type annotations (`SYNTHETIC_EVENT_MAP` and `_resolve_synthetic_event`) but did not import it from `typing`. Because the module has no `from __future__ import annotations`, CPython evaluated the annotations at import time and every `audio-hooks` subcommand (`diagnose`, `status`, `version`, `test`, …) crashed before dispatch. Users on v5.0.3 and v5.1.0 were fully blocked. One-line fix: add `Tuple` to the existing `typing` import.

### Added

- **CI import-smoke workflow** (`.github/workflows/smoke.yml`) to prevent regressions of this class of bug. Runs on every push to `master` and every PR across a 3×3 matrix (Ubuntu / Windows / macOS × Python 3.9 / 3.12 / 3.13) and exercises:
  - `import hook_runner` from both the canonical `hooks/` and the synced `plugins/audio-hooks/hooks/` copy
  - `audio-hooks version`, `status`, `diagnose`
  - `audio-hooks test all` (all 26 hooks dispatch successfully)
  - `scripts/build-plugin.sh --check` (the plugin copy is in sync with canonical sources)

### Note on v5.1.0

The `v5.1.0` tag was cut from the "context window monitor" feature commit but the in-tree version strings, CHANGELOG, and release notes were never bumped, so v5.1.0 shipped with the broken import under the 5.0.3 version string. v5.1.1 corrects this: every version reference (`HOOK_RUNNER_VERSION`, `PROJECT_VERSION`, `marketplace.json`, `plugin.json`, `config/default_preferences.json`, `CLAUDE.md` header) is now consistently `5.1.1`.

## [5.0.3] - 2026-04-11

Documentation correction. v5.0.2's README and release notes overclaimed that *"the human never types a command — Claude Code does everything"*. The user immediately caught the overclaim during real testing: the AI inside a Claude Code session **cannot** invoke `/reload-plugins` (or any other slash command) because slash commands are interactive REPL primitives with no CLI equivalent and no tool exposure. The user has to type `/reload-plugins` themselves once after install.

This release does not change any code — only the documentation, which now accurately describes what the AI can and cannot do.

### Fixed

- **README "AI-first way" section** now honestly describes the install flow as **4 user actions** (1 shell command + 2 natural-language prompts + 1 slash command), not "one sentence". The mermaid sequence diagram is updated to highlight the manual `/reload-plugins` step in a contrasting color block, making it visually obvious which step the user must perform.
- **README tagline** rewritten from *"You never type a command"* to *"You type one slash command at install time. Then natural language forever."*
- **README "Why this matters"** section adds an explicit honesty paragraph: *"the AI can run every `audio-hooks` subcommand and every `claude plugin` subcommand via its Bash tool. It cannot run interactive REPL commands like `/reload-plugins` because Claude Code's slash-command parser only accepts user keystrokes, not tool calls."*
- **README "Design philosophy"** section's natural-language operating model paragraph rewritten: *"the human types one slash command in their lifetime with this project (`/reload-plugins`, once, at install time)."*
- **CLAUDE.md "AI quickstart"** section rewritten as a step-by-step guide for Claude Code itself when operating the project on a human's behalf. New "Critical" warning: *"there is exactly one thing the user must type themselves in the entire install flow: `/reload-plugins`. The interactive REPL command has no CLI equivalent. Do NOT pretend you can run it via the Bash tool — you cannot."*
- **CLAUDE.md decision tree** install row updated to: *"Run `claude plugin marketplace add` and `claude plugin install` via the Bash tool. Then ask the user to type `/reload-plugins` (you cannot run this — REPL only)."*

### What the AI actually can and cannot do (verified)

**The AI inside a Claude Code session CAN run via the Bash tool:**
- `claude plugin marketplace add <source>` ✓
- `claude plugin marketplace list` ✓
- `claude plugin marketplace remove <name>` ✓
- `claude plugin marketplace update [name]` ✓
- `claude plugin install <plugin>` ✓
- `claude plugin uninstall <plugin>` ✓
- `claude plugin enable <plugin>` ✓
- `claude plugin disable <plugin>` ✓
- `claude plugin update <plugin>` ✓
- `claude plugin list` ✓
- `claude plugin validate <path>` ✓
- Every `audio-hooks` subcommand (status, manifest, get, set, hooks list/enable/disable, theme, snooze, webhook, tts, rate-limits, test, diagnose, logs, install, uninstall, statusline) ✓

**The AI CANNOT invoke** (REPL-only, no tool exposure):
- `/reload-plugins` ✗
- `/exit` ✗
- `/clear` ✗
- `/doctor` ✗
- Any other slash command typed at the Claude Code REPL prompt

### Why the previous overclaim happened

I assumed that because the SKILL system can invoke skill commands (e.g. `/audio-hooks`), the same mechanism could invoke other slash commands like `/reload-plugins`. It cannot. The Skill tool's documentation is explicit: *"Do not use this tool for built-in CLI commands (like `/help`, `/clear`, etc.)"*. Built-in REPL commands are **not** skills and have no programmatic invocation path.

### Changed

- Project version bumped 5.0.2 → 5.0.3 across `hook_runner.py`, `bin/audio-hooks.py`, `marketplace.json`, `plugin.json`, `default_preferences.json`, `README.md`, `CLAUDE.md`.
- The v5.0.2 GitHub release notes have been edited in place to add a correction banner pointing to v5.0.3.

## [5.0.2] - 2026-04-11

The first end-to-end install of v5.0.1 on a real Claude Code v2.1.101 session surfaced five real bugs that were invisible from outside an actual install. v5.0.2 fixes all of them and rewrites the public README + docs to lead with the project's defining selling point: **users never type a command, they just talk to Claude Code in natural language**.

### Fixed

- **`userConfig` schema rejected by `claude plugin validate`** ([fefe2c9](https://github.com/ChanMeng666/claude-code-audio-hooks/commit/fefe2c9)). The v5.0.1 plugin manifest used `description`+`sensitive` fields per my earlier read of the docs; the validator actually requires `type` (one of `string|number|boolean|directory|file`) plus `title`. Without this fix the plugin failed to install with 8 schema errors. Verified clean with `claude plugin validate plugins/audio-hooks`.
- **"Plugin not found in any marketplace"** ([c3d5809](https://github.com/ChanMeng666/claude-code-audio-hooks/commit/c3d5809)). The marketplace.json combined `metadata.pluginRoot: ./plugins` with `source: ./audio-hooks` — the leading `./` on the source path conflicted with the pluginRoot prefix and the plugin resolver couldn't find the plugin entry. Drop pluginRoot and use the explicit relative path `./plugins/audio-hooks` instead.
- **"Duplicate hooks file detected" load error** ([1537fff](https://github.com/ChanMeng666/claude-code-audio-hooks/commit/1537fff)). Claude Code's plugin loader auto-discovers `hooks/hooks.json` from the standard location; declaring `"hooks": "./hooks/hooks.json"` in the manifest causes a duplicate-load error after install. Drop the redundant field — auto-discovery handles it.
- **`audio-hooks` exits 49 silently from Git Bash on Windows** ([cdea32b](https://github.com/ChanMeng666/claude-code-audio-hooks/commit/cdea32b)). Root cause: the binary was a Python file with `#!/usr/bin/env python3` shebang, but Git Bash on Windows resolves `python3` to a Microsoft Store stub at `WindowsApps\python3.exe` — a placeholder that exits 49 silently when invoked (it's meant to open the Store to install Python). Fix: rename `bin/audio-hooks` → `bin/audio-hooks.py` and replace `bin/audio-hooks` with a portable bash wrapper that probes each Python candidate (`python3`, `python`, `py`) with a `-c "import sys"` test and only exec's the first one that returns 0. The Microsoft Store stub fails the test and is correctly skipped. Same treatment for `bin/audio-hooks-statusline`. Updated `.cmd` shims to invoke the `.py` files directly.
- **Plugin context not detected when `CLAUDE_PLUGIN_DATA` isn't set** ([2c8595f](https://github.com/ChanMeng666/claude-code-audio-hooks/commit/2c8595f)). When `audio-hooks` is invoked from the plugin's `bin/` PATH via Claude Code's Bash tool (not from inside a hook fire), `CLAUDE_PLUGIN_DATA` is not in the environment. The previous `_config_path()` fell back to `<plugin_dir>/config/user_preferences.json` — a path inside the plugin source tree that gets overwritten on plugin updates and isn't where the plugin data dir lives. Symptoms: `audio-hooks diagnose` reported `INVALID_CONFIG`, `audio-hooks theme set custom` wrote into the wrong directory, and `audio-hooks diagnose` warned `HOOKS_NOT_REGISTERED` for a healthy plugin install. Fix: new helpers `_is_running_from_plugin()` (detects plugin context by looking for `<script_parent>/.claude-plugin/plugin.json`) and `_resolve_plugin_data_dir()` (computes `~/.claude/plugins/data/audio-hooks-chanmeng-audio-hooks/`). `_config_path()` now checks plugin-context detection in addition to the env var. `_check_settings_json()` and `cmd_diagnose` only emit `HOOKS_NOT_REGISTERED` when neither install path is detected — plugin installs register hooks in the plugin's own `hooks/hooks.json`, not in `~/.claude/settings.json`.

### Added

- **Dual-install detection**. `audio-hooks diagnose` now parses `~/.claude/plugins/installed_plugins.json` and the plugin cache to detect plugin installs reliably. When both the legacy script install AND the plugin install are active, diagnose reports a `DUAL_INSTALL_DETECTED` error with `bash scripts/uninstall.sh --yes` as the suggested fix. This addresses the "double audio" symptom where users hear both voice and chime overlapping because both install paths fire on every event.

### Documentation

- **README.md rewritten end-to-end** ([69f4e29](https://github.com/ChanMeng666/claude-code-audio-hooks/commit/69f4e29) + [ddb98d5](https://github.com/ChanMeng666/claude-code-audio-hooks/commit/ddb98d5)). The previous README was 2249 lines of v4.7.0-era walkthroughs. The new README is 834 lines and leads with **"🤖 The AI-first way (just talk to Claude Code)"** as the top section after the v5.0 highlights. It contains:
  - A prominent new tagline: *"You never type a command. You never edit a config file. You never read a log."*
  - A complete 4-step ready-to-paste prompt journey (open Claude Code → install → configure → troubleshoot → uninstall), each step requiring exactly one English sentence from the human.
  - A 13-row "you say / paste this" table covering theme, snooze, enable-only, Slack/ntfy webhooks, TTS speak_assistant_message, rate-limit alerts, file_changed watch, test, status, statusline.
  - A mermaid sequence diagram of a real Human ↔ Claude Code ↔ SKILL ↔ CLI conversation showing every internal step Claude Code runs on the human's behalf.
  - The existing slash-command install reference renamed "Install the plugin (manual reference)" with a prominent warning: *"You almost certainly don't need to read this section."*
  - 5 mermaid diagrams total (high-level event flow, AI control surface, hook lifecycle, plugin layout, conversation sequence).

- **`docs/ARCHITECTURE.md` rewritten** for v5.0.2 reality. Component-by-component breakdown of `hook_runner.py`, `bin/audio-hooks`, plugin layout, status line, scripts. Hook event lifecycle as a sequence diagram. Path resolution flowchart (4 resolution paths). NDJSON schema + stable error code enum. Build pipeline. Recipes for adding new hooks and audio. **5 mermaid diagrams.**

- **`docs/INSTALLATION_GUIDE.md` and `docs/TROUBLESHOOTING.md` collapsed** to focused pointers. The v5.0 install is two slash commands and v5.0 troubleshooting is one `audio-hooks diagnose` invocation, so the legacy 900+ lines of walkthroughs were net negative. INSTALLATION_GUIDE is now 86 lines covering the three install paths; TROUBLESHOOTING is 135 lines that's mostly a stable error code table + per-symptom decision tree, all anchored to `audio-hooks` subcommands.

- **Total docs change**: −2198 lines net (from 3579 to 1381 across the four primary docs), 10 mermaid diagrams across `README.md` / `CLAUDE.md` / `docs/ARCHITECTURE.md`, every command example uses the `audio-hooks` CLI, every version reference is consistent at 5.0.2.

### Changed

- Project version bumped 5.0.1 → 5.0.2 across `hook_runner.py`, `bin/audio-hooks.py`, `marketplace.json`, `plugin.json`, `default_preferences.json`, `README.md`, `CLAUDE.md`.

## [5.0.1] - 2026-04-11

### Added

- **Dedicated audio files for the four v5.0 hooks**, generated via ElevenLabs:
  - Default theme (Jessica voice TTS): `permission-denied.mp3`, `cwd-changed.mp3`, `file-changed.mp3`, `task-created.mp3`
  - Custom theme (sound-generation API): `chime-permission-denied.mp3`, `chime-cwd-changed.mp3`, `chime-file-changed.mp3`, `chime-task-created.mp3`
- **`scripts/generate-audio.py`** — non-interactive audio file generator. Reads `config/audio_manifest.json` (a manifest of every audio file with its text prompt + theme + voice/sound-effect type) and regenerates any subset via the ElevenLabs API. Default behavior: skip existing files. Flags: `--force`, `--only filename1,filename2`, `--dry-run`. Reads `ELEVENLABS_API_KEY` from environment; never writes the key to disk. Output is NDJSON per file plus a final summary JSON. Future audio additions are now a one-line manifest edit + one-command rebuild.
- **`config/audio_manifest.json`** — single source of truth for audio file generation prompts, voice IDs, sound-effect parameters, and the TTS model.
- **`CLAUDE_PLUGIN_OPTION_*` env var overlay** in both `hook_runner.py` and `bin/audio-hooks`. The plugin manifest's `userConfig` declarations (`audio_theme`, `webhook_url`, `webhook_format`, `tts_enabled`) now flow from Claude Code's plugin install through to the runtime config without writing to `user_preferences.json`. Webhook auto-enables when a URL is supplied via the env var.

### Changed

- `hooks/hook_runner.py` `DEFAULT_AUDIO_FILES` and `CUSTOM_AUDIO_FILES`: the four v5.0 hooks now point at dedicated audio files instead of the v5.0 placeholder mappings.
- `bin/audio-hooks` `HOOK_CATALOG`: same — real filenames replace placeholders.
- Project version bumped 5.0.0 → 5.0.1 across `hook_runner.py`, `bin/audio-hooks`, `marketplace.json`, `plugin.json`.

## [5.0.0] - 2026-04-11

**AI-first redesign.** Catches up to ~9 months of Claude Code releases (v2.1.69 → v2.1.101) and re-frames every project surface around Claude Code as the operator.

### Added — AI interface layer

- **`bin/audio-hooks` CLI binary** — single Python entry point exposing 27 JSON-output subcommands. No prompts, no colors, no spinners. Output is JSON to stdout; nonzero exit codes carry structured error bodies. Default invocation (`audio-hooks` with no args) returns the canonical machine manifest.
  - `manifest`, `manifest --schema` — canonical introspection (subcommands, hooks, config keys, error codes, env vars) and JSON Schema for `user_preferences.json`
  - `status`, `version`, `diagnose` — full state snapshot, version + install detection, system check
  - `get`, `set` — read/write any config key via dotted path (auto-coerces bool/int/JSON)
  - `hooks list/enable/disable/enable-only` — per-hook state management
  - `theme list/set` — switch between voice and chime audio themes
  - `snooze [duration|off|status]` — temporary mute (forms: 30m, 1h, 90s, 2d)
  - `webhook set/clear/test` — Slack/Discord/Teams/ntfy/raw webhook config + test
  - `tts set` — TTS config including v5.0 `speak_assistant_message`
  - `rate-limits set` — rate-limit alert thresholds
  - `test <hook|all>` — synthetic-stdin smoke test
  - `logs tail/clear` — NDJSON event stream
  - `install/uninstall/update` — non-interactive install management
  - `statusline show/install/uninstall` — manage Claude Code status line registration
- **Plugin SKILL** at `plugins/audio-hooks/skills/audio-hooks/SKILL.md` — natural-language interface that lets Claude Code translate user requests like "snooze for an hour" or "switch to chimes" into the right `audio-hooks` subcommand.
- **NDJSON structured logging** — every event is one JSON line at `${CLAUDE_PLUGIN_DATA}/logs/events.ndjson` with stable schema `audio-hooks.v1`. Errors include `code` (16 stable enum values), `message`, `hint`, and `suggested_command`. Log rotation: 5 MB cap, 3 files kept. The legacy free-text `debug.log`, `errors.log`, and `hook_triggers.log` are removed; the legacy `log_debug`/`log_error`/`log_trigger` helpers are now thin NDJSON wrappers for backwards compatibility.
- **Canonical AI doc rewrite** of `CLAUDE.md` with operating principles, three-command quickstart, full hook catalogue, configuration key reference, error code reference, environment variables, decision tree, and version history.
- **JSON Schema** at `config/user_preferences.schema.json` referenced from `default_preferences.json` via `$schema` for editor validation.

### Added — four new hook events

- `PermissionDenied` (default enabled) — auto mode classifier denials. Hook can return `{retry: true}` when configured.
- `CwdChanged` (default disabled) — Claude changed working directory.
- `FileChanged` (default disabled) — watched file changed on disk; matcher takes literal filenames.
- `TaskCreated` (default enabled) — sibling of the existing `TaskCompleted` hook.

Total hook count is now **26**.

### Added — new stdin field parsing

The notification context, webhook payload, and TTS branches now consume every field Claude Code provides via stdin:

- `last_assistant_message` (Stop, SubagentStop) — TTS can speak Claude's actual final reply when `tts.speak_assistant_message: true`
- `worktree.{name,branch,path,original_cwd,original_branch}`
- `agent.{name}`, `agent_id`, `agent_type`
- `notification_type` (permission_prompt, idle_prompt, auth_success, elicitation_dialog)
- `source` (SessionStart: startup/resume/clear/compact)
- `error_type` (StopFailure: rate_limit/authentication_failed/billing_error/...)
- `trigger` (PreCompact, PostCompact: manual/auto)
- `load_reason` (InstructionsLoaded: session_start/nested_traversal/...)
- `permission_suggestions` (PermissionRequest)
- `rate_limits.{five_hour,seven_day}` for proactive warnings (see below)

A universal context suffix appends `[session: foo, worktree: bar, agent: baz]` to every notification when `notification_settings.detail_level` allows.

### Added — native matcher routing

`hook_runner.py` now accepts synthetic event names like `session_start_resume`, `stop_failure_rate_limit`, `notification_idle_prompt`, `precompact_manual`. Each maps to a canonical hook plus a per-variant audio override. The plugin's `hooks/hooks.json` registers separate handlers per matcher value, so Claude Code's matcher engine routes events at the settings.json layer instead of inside Python branching. Faster, configurable per-matcher, and per-handler `async: true` means a slow rate-limit-failure path doesn't block the auth-failure path. Legacy canonical event names (`session_start`, `stop_failure`, etc.) keep working unchanged for backwards compatibility.

### Added — rate-limit pre-check

On every hook invocation, the runner inspects stdin `rate_limits.{five_hour,seven_day}.used_percentage` and plays a one-shot warning audio when crossing configured thresholds (default `[80, 95]`). Each `(window, threshold, resets_at)` tuple fires exactly once per reset window via marker-file debounce — the user is warned at 80% and again at 95% but never spammed. Configurable via `audio-hooks rate-limits set --five-hour-thresholds 80,95`.

### Added — fire-and-forget webhook subprocess

Webhook dispatch now spawns a tiny detached Python subprocess that does the urlopen and exits. The parent hook process can exit immediately even on slow webhooks. Failures land in NDJSON with `WEBHOOK_TIMEOUT` or `WEBHOOK_HTTP_ERROR` codes. The raw payload is now versioned (`audio-hooks.webhook.v1`) and surfaces every new stdin field as a top-level key for downstream consumers to pin.

### Added — plugin packaging

- `.claude-plugin/marketplace.json` — single-plugin marketplace catalog
- `plugins/audio-hooks/.claude-plugin/plugin.json` — plugin manifest with `userConfig` for headless install-time config
- `plugins/audio-hooks/hooks/hooks.json` — matcher-scoped hook registration for all 26 events using `${CLAUDE_PLUGIN_ROOT}` paths
- `plugins/audio-hooks/runner/run.py` — plugin entry point that imports the bundled `hook_runner.py`
- `scripts/build-plugin.sh` — non-interactive sync script that mirrors canonical files (`/hooks/`, `/bin/`, `/audio/`, `/config/`) into the plugin layout. Run after editing canonical files. `--check` flag for CI verification.
- Plugin install: `/plugin marketplace add ChanMeng666/claude-code-audio-hooks` then `/plugin install audio-hooks@chanmeng-audio-hooks`.

### Added — status line script

- `bin/audio-hooks-statusline` — two-line status line with model + version + enabled-hook count + theme on line 1, and conditional snooze indicator + focus-flow indicator + worktree branch + colored rate-limit progress bar on line 2. Caches `audio-hooks status` for 5 seconds keyed on `session_id`. Designed to be registered with `refreshInterval: 60` so snooze countdowns and rate-limit bars update during idle periods.
- `audio-hooks statusline install` writes the `statusLine` field in `~/.claude/settings.json` non-interactively.

### Changed — non-interactive scripts

Every shell script in `scripts/` now auto-engages non-interactive mode when stdin is not a TTY or `CLAUDE_NONINTERACTIVE=1` is set. Specifically:

- `install-complete.sh` — `NON_INTERACTIVE` auto-engages on non-TTY; the optional audio test prompt is skipped
- `uninstall.sh` — `NON_INTERACTIVE` auto-engages on non-TTY; default behaviour now PRESERVES the user's config and audio files (less destructive). Use `--purge` to remove them in non-interactive mode.
- `configure.sh` — when invoked with no args by a non-TTY caller, emits `INTERACTIVE_SCRIPT` JSON pointer instead of opening the human menu. Programmatic mode (with flags) is unchanged.
- `test-audio.sh` — same: emits `INTERACTIVE_SCRIPT` JSON pointer pointing to `audio-hooks test all`.

### Changed — config storage location

Plugin installs now store `user_preferences.json` at `${CLAUDE_PLUGIN_DATA}/user_preferences.json` (writable, persistent across plugin updates). On first read, the runner copies `default_preferences.json` from the plugin into place. Script installs continue to use `<project_dir>/config/user_preferences.json` as before.

### Changed — project version

Bumped from 4.7.0 to 5.0.0 across `hook_runner.py`, `bin/audio-hooks`, `default_preferences.json`, plugin manifests, and `CLAUDE.md`.

### Backwards compatibility

- The four pre-v5.0 hook entries in `~/.claude/settings.json` keep working unchanged (canonical names still resolve in `hook_runner.main()`).
- The legacy `log_debug`/`log_error`/`log_trigger` helpers stay as thin NDJSON wrappers, so any third-party scripts that call them keep working.
- The pre-v5.0 `user_preferences.json` schema is fully forward-compatible — new keys are optional with sensible defaults.
- Users on the script install path can keep running `bash scripts/install-complete.sh`. The plugin install path is additive.

### Removed

- Free-text `debug.log`, `errors.log`, `hook_triggers.log` files (replaced by NDJSON `events.ndjson`).
- Interactive `[y/N]` prompts in install/uninstall flows when stdin isn't a TTY (auto-non-interactive mode).

## [4.7.0] - 2026-03-22

### Added
- **Focus Flow: Anti-distraction micro-tasks** during Claude's thinking time
  - Automatically launches a micro-task when Claude starts processing (UserPromptSubmit) and auto-closes when Claude finishes (Stop)
  - **Breathing mode**: Guided 4-7-8 / box / energizing breathing exercises in a dedicated terminal window with visual progress bars and emoji prompts
  - **Hydration mode**: Random wellness reminders (drink water, stretch, posture check, eye rest, deep breath) via desktop notifications
  - **URL mode**: Open a custom URL in the browser (GitHub issues, Jira board, etc.)
  - **Command mode**: Run any custom shell command
  - Configurable `min_thinking_seconds` delay (default: 15s) — prevents micro-tasks from flashing for quick responses
  - Marker-file state tracking with PID-based process cleanup (same pattern as snooze system)
  - Cross-platform support: Windows (cmd), macOS (Terminal.app), Linux (xterm/gnome-terminal)
  - New `scripts/focus-flow.py` standalone launcher with `scripts/focus-flow-tasks/breathing_patterns.json` data file
  - New `focus_flow` config section in user_preferences.json (disabled by default)

### Changed
- `hook_runner.py` version bumped to 4.7.0
- `run_hook()` pipeline now includes Focus Flow start/stop lifecycle
- Updated all documentation

---

## [4.6.0] - 2026-03-22

### Added
- **Async hook execution**: All hooks now register with `"async": true` in settings.json — Claude Code fires hooks in the background and never waits for audio playback, eliminating 200-500ms latency per hook invocation
- **Smart matchers**: High-noise hooks now use Claude Code's native regex matchers to reduce notification spam:
  - `PreToolUse` only fires for `Bash` tool (not Read/Glob/Grep)
  - `PostToolUseFailure` only fires for `Bash|Write|Edit` tools
- **User-configurable filters**: New `filters` section in `user_preferences.json` for per-hook regex filtering on stdin JSON fields (e.g., filter by tool_name, error content, agent_type)
- **Richer notification context**: Desktop notifications now show actionable details from stdin JSON:
  - "Bash failed: `npm test` — exit code 1" (instead of "Tool failed: Bash")
  - "Running Bash: `npm install`" (instead of "Running: Bash")
  - "Permission needed: Bash — `rm -rf node_modules`" (instead of "Permission needed: Bash")
- **Notification detail level**: New `notification_settings.detail_level` config option (`minimal`, `standard`, `verbose`)
- **Webhook integration**: Send hook events to external services via HTTP POST:
  - Supported services: Slack, Discord, Microsoft Teams, ntfy.sh, and custom webhook URLs
  - New `webhook_settings` section in config with `url`, `format`, `hook_types`, and `headers`
  - Runs in background thread — never blocks other notifications
  - Uses only Python standard library (urllib.request) — no external dependencies

### Changed
- `hook_runner.py` version bumped to 4.6.0
- All installer scripts (`install-complete.sh`, `install-windows.ps1`, `quick-setup.sh`) now generate async hook registrations
- `get_notification_context()` rewritten with `_truncate()` helper and `_get_tool_detail()` for richer stdin JSON extraction
- `run_hook()` pipeline now includes filter check and webhook step
- Updated all documentation (CLAUDE.md, README.md, CHANGELOG.md, ARCHITECTURE.md)

---

## [4.5.0] - 2026-03-22

### Added
- **8 new hook types** — full coverage of all 22 Claude Code hook events (up from 14):
  - `StopFailure`: Fires when a turn ends due to an API error (rate limit, auth failure, server error)
  - `PostCompact`: Fires after context compaction completes
  - `ConfigChange`: Fires when a configuration file changes during a session
  - `InstructionsLoaded`: Fires when CLAUDE.md or `.claude/rules/*.md` files are loaded into context
  - `WorktreeCreate`: Fires when a worktree is created for isolated tasks
  - `WorktreeRemove`: Fires when a worktree is removed/cleaned up
  - `Elicitation`: Fires when an MCP server requests user input during a tool call
  - `ElicitationResult`: Fires after a user responds to an MCP elicitation
- **16 new audio files** (8 voice + 8 chime) generated via ElevenLabs:
  - Voice (Jessica): stop-failure.mp3, post-compact.mp3, config-change.mp3, instructions-loaded.mp3, worktree-create.mp3, worktree-remove.mp3, elicitation.mp3, elicitation-result.mp3
  - Chime: chime-stop-failure.mp3, chime-post-compact.mp3, chime-config-change.mp3, chime-instructions-loaded.mp3, chime-worktree-create.mp3, chime-worktree-remove.mp3, chime-elicitation.mp3, chime-elicitation-result.mp3
- Context extraction for all 8 new hooks in `get_notification_context()`
- TTS messages for StopFailure, PostCompact, ConfigChange, and Elicitation hooks

### Changed
- `hook_runner.py` version bumped to 4.5.0
- Audio file count per theme: 14 → 22
- Total hook count: 14 → 22
- Updated all installer scripts (`install-complete.sh`, `install-windows.ps1`) with new hook registrations
- Updated `configure.sh` with new hook names, descriptions, and defaults
- Updated `default_preferences.json` and `user_preferences.json` with new hook entries
- Updated `CLAUDE.md` hook tables, mermaid diagrams, and version references
- Updated `README.md` hook count references and added documentation for all 8 new hooks

---

## [4.4.0] - 2026-03-13

### Added
- **Snooze / Temporary Mute** (closes #7): Temporarily silence all audio hooks for a specified duration with automatic resumption
  - New `scripts/snooze.sh` standalone CLI: `bash scripts/snooze.sh 1h` to snooze, `status` to check, `off` to resume
  - Marker-file based design — no daemon or cleanup needed; hooks self-expire
  - Accepts flexible duration formats: `30m`, `1h`, `2h`, `90m`, bare numbers (minutes), `30s`
  - `--snooze`, `--resume`, `--snooze-status` flags added to `scripts/configure.sh`
  - `--snooze`, `--resume`, `--snooze-status` flags added to `scripts/quick-configure.sh` (inline, works via `curl | bash`)
  - Snooze check integrated into both `hooks/hook_runner.py` (Python) and `hooks/shared/hook_config.sh` (Bash)
  - Debug logging: snoozed hooks log "SNOOZED" with remaining time

### Changed
- `hook_runner.py` version bumped to 4.4.0

---

## [4.3.1] - 2026-02-17

### Added
- **`scripts/quick-configure.sh`**: Lightweight hook manager for Quick Setup (Lite tier) users — enable, disable, or list individual hooks without cloning the repository
  - `--list` shows which of the 4 Quick Setup hooks are enabled/disabled
  - `--disable <Hook>` removes a hook from `~/.claude/settings.json`
  - `--enable <Hook>` re-adds a hook with the correct platform-specific command
  - `--only <Hook> [Hook...]` keeps only the specified hooks, removes the rest
  - Works via `curl | bash` (no clone needed), same pattern as `quick-setup.sh`
  - Case-insensitive hook name matching
  - Supports Python and Node.js for JSON manipulation

### Fixed
- **`scripts/quick-unsetup.sh`**: Now removes all 4 installed hooks including `PermissionRequest` (was only removing 3: Stop, Notification, SubagentStop)

---

## [4.3.0] - 2026-02-17

### Added
- **Per-hook notification mode overrides**: New `notification_settings.per_hook` config allows independently controlling audio and desktop notifications per hook type (e.g., `"pretooluse": "audio_only"` to skip desktop notifications for frequent hooks)
- **`disabled` notification mode**: Suppresses both audio and desktop notifications while still allowing TTS and logging — different from `enabled_hooks: false` which skips everything
- **`--hook-mode` CLI flag**: `bash scripts/configure.sh --hook-mode pretooluse=audio_only posttooluse=disabled` for quick per-hook mode configuration
- Per-hook mode validation with automatic fallback to global mode on invalid values

### Changed
- `hook_runner.py` notification mode resolution now checks `per_hook` overrides before falling back to global `notification_settings.mode`
- Debug logging now shows both per-hook and global mode for each hook trigger
- Updated `config/default_preferences.json` and `config/user_preferences.json` with `per_hook` field
- Updated CLAUDE.md, README.md with per-hook notification mode documentation

### Upgrade

No reinstall needed — existing installations self-update automatically on the next hook trigger after `git pull`. The `per_hook` field is fully backward compatible: if absent, all hooks use the global mode as before.

---

## [4.2.2] - 2026-02-14

### Fixed
- **Audio theme switching broken**: The `audio_files` section in config templates hardcoded all 14 hooks to `default/...`, silently overriding the `audio_theme` setting — switching to `"custom"` had no effect
- **`get_audio_file()` logic**: Now ignores `audio_files` entries that match the default template pattern (`default/<filename>`), so `audio_theme` is always respected
- **Stale installed copy**: `~/.claude/hooks/hook_runner.py` was copied once at install and never updated after `git pull`
- **`configure.sh --theme` incomplete**: Only edited JSON config without syncing `hook_runner.py` to `~/.claude/hooks/`

### Added
- **Auto-sync**: `hook_runner.py` now includes `HOOK_RUNNER_VERSION` constant and `check_and_self_update()` — the installed copy in `~/.claude/hooks/` detects newer versions in the project directory and self-updates on next hook trigger
- **configure.sh hook_runner sync**: `--theme` command now copies `hook_runner.py` to `~/.claude/hooks/` after switching theme
- **README "Ask Claude Code" table**: Quick-reference showing users what to say to Claude Code for theme switching, hook toggling, and config checks

### Changed
- Removed `audio_files` block from `config/default_preferences.json` and `config/user_preferences.json` (backward compatible — `get_audio_file()` handles missing section via `config.get("audio_files", {})`)
- Updated README config examples to use `audio_theme` instead of per-hook `audio_files`
- Updated version references to 4.2.2 across CLAUDE.md, README.md

### Upgrade

No reinstall needed — existing installations self-update automatically on the next hook trigger after `git pull`. Or force sync now:
```bash
cd ~/claude-code-audio-hooks
git pull
cp hooks/hook_runner.py ~/.claude/hooks/hook_runner.py
```

---

## [4.2.0] - 2026-02-13

### Added
- **PostToolUseFailure hook**: Audio alert when a tool execution fails (matches on tool name)
- **SubagentStart hook**: Audio alert when a background subagent is spawned (matches on agent type)
- **TeammateIdle hook**: Audio alert when an Agent Teams teammate goes idle
- **TaskCompleted hook**: Audio alert when an Agent Teams task is completed
- 5 new ElevenLabs Jessica voice audio files: `permission-request.mp3`, `tool-failed.mp3`, `subagent-start.mp3`, `teammate-idle.mp3`, `team-task-done.mp3`
- Full coverage of all 14 Claude Code hook events

### Changed
- Total hook types: 10 → 14
- Total audio files: 9 → 14 (each hook now has a unique audio file)
- `permission_request` hook now uses its own `permission-request.mp3` (was sharing `notification-urgent.mp3`)
- `posttoolusefailure` uses critical urgency for desktop notifications
- Updated all documentation to reflect new hook count
- Updated installers to register all 14 hook types with correct matcher support

### Upgrade

Re-run your installer to register the new hooks:
```bash
# Full Install
bash scripts/install-complete.sh      # macOS/Linux/WSL/Git Bash
.\scripts\install-windows.ps1         # Windows PowerShell
```

Note: All 4 new hooks are disabled by default. Enable them in `config/user_preferences.json` if needed.

---

## [4.1.1] - 2026-02-13

### Feature: PermissionRequest Hook Support

Adds `PermissionRequest` hook support — the "Allow this bash command?" permission dialog now triggers audio and desktop notifications. Closes #5.

### Added

- **`PermissionRequest` hook** — 4th default-enabled hook across all installation tiers
  - Quick Setup (macOS): Basso.aiff (distinct from Sosumi for Notification)
  - Quick Setup (Linux): dialog-warning.oga
  - Quick Setup (WSL/Git Bash): SystemSounds.Question
  - Full Install (all platforms): notification-urgent.mp3
- Context extraction for permission_request: shows `Permission needed: <tool_name>`
- Critical urgency desktop notifications for permission_request (same as notification)
- TTS message: "Permission required"

### Changed

- `hooks/hook_runner.py` — Added permission_request to defaults, context extraction, critical urgency
- `scripts/quick-setup.sh` — Added PermissionRequest as 4th hook with distinct system sounds
- `scripts/install-complete.sh` — Registered PermissionRequest with matcher
- `scripts/install-windows.ps1` — Registered PermissionRequest with matcher
- `config/default_preferences.json` / `config/user_preferences.json` — Added permission_request entries
- `CLAUDE.md` — Updated hook diagrams, tables, settings examples
- `README.md` — Updated notification types from 9→10, added PermissionRequest documentation

### Upgrade

Re-run your installer to register the new hook:
```bash
# Quick Setup
curl -sL https://raw.githubusercontent.com/ChanMeng666/claude-code-audio-hooks/master/scripts/quick-setup.sh | bash

# Full Install
bash scripts/install-complete.sh      # macOS/Linux/WSL/Git Bash
.\scripts\install-windows.ps1         # Windows PowerShell
```

---

## [4.1.0] - 2026-02-13

### Fix: macOS Sequoia (15+) Quick Setup No Audio

Quick Setup on macOS 15+ (Sequoia) produced no sound because `osascript` notifications were silently blocked.

### Fixed

- Quick Setup now uses `afplay` for audio playback (works without permissions on all macOS versions)
- `osascript` notification kept as best-effort for desktop popups
- Each hook uses a distinct system sound: Glass (Stop), Sosumi (Notification), Pop (SubagentStop)

---

## [4.0.3] - 2026-02-11

### Bug Fixes: Installer & Uninstaller Correctness

Fixes multiple bugs that prevented correct hook registration on Windows and blocked uninstallation of modern hook_runner.py-based entries.

### Fixed

#### 1. Windows branch in `install-complete.sh` missing defensive wrapping
- **Bug**: Windows branch registered hooks without `|| true` fallback or `timeout`
- **Impact**: A missing `hook_runner.py` would cause Claude Code hook errors instead of silent fallback
- **Fix**: Added `|| true` to command and `timeout: 10` to hook entries, matching the Unix branch

#### 2. `install-windows.ps1` registered all 9 hooks regardless of config
- **Bug**: PowerShell installer ignored `enabled_hooks` preferences and always registered all 9 hooks
- **Impact**: Users heard audio for every tool call (PreToolUse/PostToolUse), making it very noisy
- **Fix**: Reads `user_preferences.json` (or `default_preferences.json`) and only registers enabled hooks
- **Also fixed**: Added `|| true` and `timeout = 10` to all hook commands
- **Also fixed**: Settings.json now written as UTF-8 without BOM (was using `Out-File -Encoding UTF8` which adds BOM on PS 5.x)

#### 3. `uninstall.sh` could not detect or remove hook_runner.py entries
- **Bug**: `HOOK_SCRIPTS` array and Python `hook_scripts` list did not include `hook_runner.py`
- **Bug**: `endswith(script)` matching failed on commands like `py "path/hook_runner.py" stop || true` (command ends with `|| true`, not with the script name)
- **Impact**: Uninstaller left hook entries in `settings.json` and `hook_runner.py`/`.project_path` files on disk
- **Fix**: Added `hook_runner.py` and `.project_path` to removal lists; changed `endswith` to `in` for substring matching

#### 4. `uninstall.sh` temp dir hardcoded to `/tmp/`
- **Bug**: `rm -f /tmp/claude_audio_hooks.lock` fails on Windows (Git Bash) where temp is `$TEMP`
- **Fix**: Uses `${TEMP:-${TMP:-/tmp}}` for cross-platform temp directory

#### 5. `uninstall.sh` `((removed++))` crashes under `set -e`
- **Bug**: `((removed++))` returns exit code 1 when `removed=0`, causing `set -e` to terminate the script
- **Fix**: Changed to `((removed += 1))` which always returns 0

#### 6. `install-complete.sh` verification grep matched wrong pattern
- **Bug**: Test checked for `notification_hook.sh` in settings.json, but modern installs use `hook_runner.py`
- **Fix**: Changed grep pattern to `hook_runner.py`

#### 7. `install-complete.sh` log paths incorrect
- **Bug**: Displayed `/tmp/claude_hooks_log/hook_triggers.log` which is not the actual log path
- **Fix**: Shows platform-appropriate path (`$TEMP/claude_audio_hooks_queue/logs/` on Windows, `/tmp/claude_audio_hooks_queue/logs/` on Unix)

---

## [3.3.5] - 2026-02-04

### 🐛 Bug Fix: UTF-8 BOM Issue on Windows

This release fixes a critical bug that prevented audio from playing on Windows installations.

### Fixed

#### 1. PowerShell UTF-8 BOM Issue (`scripts/install-windows.ps1`)
- **Bug**: PowerShell 5.x's `-Encoding UTF8` writes files with BOM (Byte Order Mark)
- **Impact**: `.project_path` file started with `\xef\xbb\xbf`, causing path resolution failure
- **Fix**: Use `[System.IO.File]::WriteAllText()` with explicit UTF-8 encoding without BOM

#### 2. Defensive BOM Handling (`hooks/hook_runner.py`)
- **Enhancement**: Changed encoding from `utf-8` to `utf-8-sig` when reading `.project_path`
- **Benefit**: Python's `utf-8-sig` codec automatically strips BOM if present
- **Backward Compatible**: Works correctly with both BOM and non-BOM files

### Technical Details

The issue manifested as `NO_AUDIO_CONFIG` in hook trigger logs because:
1. `.project_path` contained `\xef\xbb\xbfD:/path/...` instead of `D:/path/...`
2. Path validation failed since the BOM-prefixed path didn't exist
3. Audio files couldn't be located, resulting in silent failures

---

## [3.3.4] - 2025-12-22

### 🪟 Full Windows Native Support & Cross-Platform Improvements

This release adds comprehensive Windows native support and improves cross-platform compatibility across all environments.

### Added

#### 1. Windows PowerShell Installer (`scripts/install-windows.ps1`)
- **New**: Native PowerShell installer for Windows users who don't use Git Bash
- **Features**:
  - Prerequisite checking (Python 3.6+, Claude Code CLI)
  - Automatic settings.json configuration
  - Installation validation and testing
  - Non-interactive mode (`-NonInteractive` flag)

#### 2. Diagnostic Tool (`scripts/diagnose.py`)
- **New**: Cross-platform diagnostic utility for troubleshooting
- **Checks**:
  - Python version and platform detection (Windows/WSL/macOS/Linux/Git Bash)
  - Hooks directory and hook_runner.py installation status
  - Project path configuration and audio files availability
  - Claude settings.json hook configuration
  - Recent hook trigger logs
- **Options**: `--verbose` for detailed info, `--test-audio` to test playback

#### 3. Debug Logging Mode
- **New**: Set `CLAUDE_HOOKS_DEBUG=1` environment variable to enable detailed logging
- **Logs include**: Hook triggers, path normalization, audio playback attempts, errors
- **Log location**: `$TEMP/claude_audio_hooks_queue/logs/debug.log` (Windows) or `/tmp/claude_audio_hooks_queue/logs/debug.log` (Unix)

### Improved

#### 1. Enhanced `hook_runner.py`
- **Path Normalization**: Handles Git Bash (`/d/...`), WSL2 (`/mnt/c/...`), and Cygwin (`/cygdrive/c/...`) paths
- **PowerShell Safety**: Proper escaping of special characters in audio file paths
- **Temp Directory**: Cross-platform temp directory detection with multiple fallbacks
- **Error Handling**: Granular exception handling with detailed error logging
- **Debug Output**: Comprehensive logging when `CLAUDE_HOOKS_DEBUG=1` is set

#### 2. Improved `install-complete.sh`
- **Temp Directory**: Uses platform-appropriate temp directories (`$TEMP` on Windows, `/tmp` on Unix)
- **Path Format**: Saves `.project_path` in Windows format on Windows environments
- **Python Detection**: Prioritizes `py` launcher on Windows, then `python3`, then `python`

#### 3. Updated `hook_config.sh`
- **Debug Logging**: Added `log_debug()` and `log_error()` functions
- **Temp Directory**: Cross-platform temp directory handling
- **Path Functions**: Unified path conversion with `hook_runner.py`

### Cross-Platform Status
- ✅ **Windows Native**: Full support via PowerShell installer
- ✅ **Windows + Git Bash**: Automatic path conversion
- ✅ **Windows + WSL**: PowerShell audio playback via temp file copy
- ✅ **macOS**: Full support (afplay)
- ✅ **Linux**: Full support (mpg123/ffplay/aplay)
- ✅ **Cygwin**: Full support with path conversion

### Upgrade Instructions

**For existing installations:**
```bash
cd claude-code-audio-hooks
git pull origin master

# Re-run installer to update all components
bash scripts/install-complete.sh  # Linux/macOS/Git Bash
# Or: .\scripts\install-windows.ps1  # Windows PowerShell
```

**To enable debug logging:**
```bash
export CLAUDE_HOOKS_DEBUG=1  # Linux/macOS/Git Bash
# Or: $env:CLAUDE_HOOKS_DEBUG = "1"  # Windows PowerShell
```

---

## [3.3.3] - 2025-11-07

### 🐛 Critical Bug Fixes: WSL Audio & Hooks Format

This release fixes two critical issues affecting WSL users and new installations.

### Fixed

#### 1. WSL Audio Playback Issue
- **Problem**: Windows MediaPlayer could not access audio files via WSL UNC paths (`\\wsl.localhost\...`)
- **Solution**: Audio files are now copied to Windows temp directory (`C:/Windows/Temp`) before playback
- **Impact**: WSL users can now hear audio notifications correctly
- **Technical Details**:
  - Modified `play_audio_internal()` in `hooks/shared/hook_config.sh`
  - Automatic cleanup after playback completes
  - Increased playback wait time from 3s to 4s for reliability
  - Background process handles file cleanup to avoid blocking

#### 2. Hooks Format Compatibility (Credits: @PaddyPatPat)
- **Problem**: Installer generated deprecated hooks format, causing Claude Code v2.0.32+ to report "Invalid Settings"
- **Solution**: Updated installer to generate new array-based format required by Claude Code v2.0.32+
- **Impact**: New installations now work correctly with latest Claude Code
- **Technical Details**:
  - Modified `scripts/install-complete.sh` Python script
  - Old format: `"Notification": "~/.claude/hooks/notification_hook.sh"`
  - New format: `"Notification": [{"hooks": [{"type": "command", "command": "~/.claude/hooks/notification_hook.sh"}]}]`
  - Each hook now formatted as array of matcher objects

### Cross-Platform Status
- ✅ **WSL users**: Both audio playback and hooks format fixed
- ✅ **macOS users**: No changes (continues using afplay)
- ✅ **Linux users**: No changes (continues using mpg123/aplay)
- ✅ **Git Bash users**: No changes (already working)

### Upgrade Instructions

**For existing installations:**
```bash
cd claude-code-audio-hooks
git pull origin master

# Update hook audio playback
cp hooks/shared/hook_config.sh ~/.claude/hooks/shared/hook_config.sh

# Re-run installer to update hooks format
bash scripts/install-complete.sh
```

### Credits
- WSL audio fix: Main development team
- Hooks format fix: Special thanks to [@PaddyPatPat](https://github.com/PaddyPatPat) for identifying and documenting the hooks format issue in [PR #2](https://github.com/ChanMeng666/claude-code-audio-hooks/pull/2)

## [3.3.2] - 2025-11-07

### Note
This version was superseded by v3.3.3 which includes additional hooks format fix. Please upgrade to v3.3.3.

## [3.3.1] - 2025-11-06

### 🐛 Critical Bug Fixes: Installation Script Stability

Fixed critical issues preventing successful installation on WSL and other platforms.

### Fixed
- **Bash arithmetic expression error with `set -e`**:
  - Replaced post-increment operators (`++`) with compound assignment (`+=1`)
  - Post-increment returns 0 when variable is 0, causing `set -e` to exit
  - Affected counters: `STEPS_COMPLETED`, `WARNINGS`, `ERRORS`, and all test counters
  - Installation now completes successfully on all platforms

- **Python type error in configuration validation**:
  - Fixed `TypeError: unsupported operand type(s) for +: 'int' and 'str'`
  - Configuration validation now filters out comment keys (starting with `_`)
  - Properly handles JSON files with inline comments

### Impact
- ✅ **Installation now works reliably on WSL**
- ✅ **All arithmetic operations safe with `set -e`**
- ✅ **Configuration validation handles commented JSON**
- ✅ **No breaking changes** - fully backward compatible

### Technical Details
```bash
# Before (fails with set -e when var=0)
((STEPS_COMPLETED++))

# After (works correctly)
((STEPS_COMPLETED+=1))
```

## [3.3.0] - 2025-11-06

### 🤖 Full Automation Support: Non-Interactive Mode for All Scripts

All core scripts (`install-complete.sh`, `uninstall.sh`, `configure.sh`) now support **non-interactive mode** - enabling complete automation by Claude Code and scripts!

### Added
- **Non-interactive mode for `install-complete.sh`**:
  - `--yes`/`-y`/`--non-interactive` - Skip audio test prompt
  - `--help` - Show comprehensive usage guide
  - Auto-completes installation without user input

- **Non-interactive mode for `uninstall.sh`**:
  - `--yes`/`-y`/`--non-interactive` - Auto-confirm all removals
  - `--help` - Show comprehensive usage guide
  - Automatically removes: hooks, settings, config, audio files
  - Creates backups before deletion
  - Zero prompts, full automation

### Changed
- **Version updates**:
  - `install-complete.sh` → v3.2.0
  - `uninstall.sh` → v3.2
  - Added version info in script headers

### Enhanced
- **Complete Claude Code Automation** - AI assistants can now:
  - Install without prompts: `bash install-complete.sh --yes`
  - Uninstall without prompts: `bash uninstall.sh --yes`
  - Configure hooks: `bash configure.sh --enable notification`
  - Fully automate entire lifecycle

- **CI/CD Ready**:
  - Perfect for deployment pipelines
  - Scriptable setup and teardown
  - No TTY required

### Impact
- ✅ **100% non-interactive capability** across all scripts
- ✅ **Claude Code can fully automate** install/uninstall/configure
- ✅ **Zero user input required** for automation
- ✅ **Backward compatible** - interactive mode still default

### Examples
```bash
# Full automated installation
bash scripts/install-complete.sh --yes

# Full automated uninstallation
bash scripts/uninstall.sh --yes

# Configure hooks programmatically
bash scripts/configure.sh --enable notification stop --disable pretooluse
```

## [3.2.0] - 2025-11-06

### 🤖 Major Enhancement: Dual-Mode Configuration Tool

`configure.sh` now supports **both human-friendly interactive mode AND programmatic CLI interface** - making it usable by Claude Code, scripts, and automation tools!

### Added
- **Programmatic CLI Interface** for `configure.sh`:
  - `--list` - List all hooks and their status
  - `--get <hook>` - Get status of specific hook (returns `true`/`false`)
  - `--enable <hook> [hook2...]` - Enable one or more hooks
  - `--disable <hook> [hook2...]` - Disable one or more hooks
  - `--set <hook>=<value>` - Set hook to specific value
  - `--reset` - Reset to recommended defaults
  - `--help` - Show comprehensive usage guide
- **Batch Operations** - Enable/disable multiple hooks in one command
- **Idempotent Operations** - Safe to run multiple times, only changes what's needed
- **Clear Output** - Visual indicators (✓/✗) for all operations

### Changed
- **configure.sh** is now a **dual-mode tool**:
  - No arguments → Interactive menu (existing functionality preserved)
  - With arguments → Programmatic CLI (new functionality)
- All programmatic commands automatically save changes
- Error handling for unknown hooks (warnings, not failures)

### Enhanced
- **AI Assistant Integration** - Claude Code and other AI tools can now:
  - Query hook configuration programmatically
  - Enable/disable hooks based on user preferences
  - Automate configuration setup
- **Script Automation** - Easy to integrate into deployment scripts
- **Backward Compatible** - Interactive mode works exactly as before

### Impact
- ✅ **Claude Code can now configure hooks!**
- ✅ **Scriptable configuration** - No more manual editing needed
- ✅ **Batch operations** - Change multiple hooks at once
- ✅ **100% backward compatible** - Existing users unaffected

### Examples
```bash
# Check if notification hook is enabled
bash scripts/configure.sh --get notification

# Enable multiple hooks at once
bash scripts/configure.sh --enable notification stop subagent_stop

# Mixed operations in one command
bash scripts/configure.sh --enable notification --disable pretooluse
```

## [3.1.1] - 2025-11-06

### 🧹 Deep Cleanup: Removing All Redundant Scripts

Further simplification by removing truly unnecessary internal scripts and fixing broken references. Now only essential, actively-used files remain.

### Removed
- **`scripts/internal/detect-environment.sh`** (25KB) - Completely redundant
  - Environment detection already integrated in `hooks/shared/path_utils.sh`
  - Never actually called - only mentioned in log messages
  - Removed entire `/scripts/internal/` directory (now empty)
- **`scripts/.internal-tests/check-setup.sh`** (8.3KB) - Unused diagnostic script
  - Not called by install-complete.sh
  - Had broken path references in test-audio.sh
- **`scripts/.internal-tests/test-path-conversion.sh`** (5.7KB) - Never invoked
  - No script in the entire project calls it
  - Pure legacy code

### Fixed
- **Broken references in `test-audio.sh`**:
  - Removed reference to non-existent `./scripts/check-setup.sh`
  - Removed reference to non-existent `docs/AUDIO_CREATION.md`
  - Updated to point users to installer and README.md
- **Misleading suggestions in `install-complete.sh`**:
  - Removed suggestions to manually run `detect-environment.sh`
  - Replaced with advice to re-run installer

### Changed
- **`scripts/.internal-tests/` now contains only 1 file**:
  - `test-path-utils.sh` (8.7KB) - The ONLY test script actually used by installer
  - Everything else eliminated

### Impact
- ✅ **~39KB of truly redundant code removed** (detect-environment.sh + unused tests)
- ✅ **Zero broken references** - All documentation now accurate
- ✅ **Ultra-minimal structure** - Only files that are actually used
- ✅ **No duplicate functionality** - Environment detection in one place only

## [3.1.0] - 2025-11-06

### 🎯 Project Cleanup: Achieving True Single-Installation Simplicity

This release further streamlines the project structure by removing unnecessary files and hiding internal utilities from users. The goal: users clone and run ONE installation command, with ZERO confusion.

### Removed
- **Deleted `/examples/` directory** - Redundant with `/config/` directory
  - Removed outdated v1.0 example files
  - Eliminated duplicate configuration examples
  - Configuration examples now only in `/config/`
- **Deleted `/docs/` directory** - Empty directory, all docs consolidated in README.md
- **Deleted obsolete patch script** - `scripts/internal/apply-windows-fix.sh`
  - v2.x legacy patch script no longer needed
  - All fixes now integrated into `install-complete.sh`
- **Removed personal development files** - Added `.claude/` to `.gitignore`

### Changed
- **Hidden internal test scripts** - Renamed `/scripts/tests/` → `/scripts/.internal-tests/`
  - Test scripts are auto-run by installer, users shouldn't see them
  - Reduces decision paralysis and confusion
  - Updated all internal references to new path
- **Simplified documentation references**
  - Removed suggestions to manually run internal scripts
  - Updated bug report template to request log files instead
  - Simplified project structure diagram
- **Cleaner visible file structure**
  - From 7 top-level directories → 5 directories
  - From 21+ visible files → ~15 essential files
  - Only user-facing scripts visible in `/scripts/`

### Impact
- ✅ **Zero decision anxiety** - One clear installation path
- ✅ **Reduced confusion** - No unnecessary files or scripts visible
- ✅ **Cleaner project** - ~4,100 lines of redundant code removed
- ✅ **Better UX** - Users focus on: Clone → Install → Use

## [3.0.1] - 2025-11-06

### Fixed
- **Uninstall Script**: Fixed bash syntax error on line 115 where `local` keyword was incorrectly used outside function scope

## [3.0.0] - 2025-11-06

### 🎯 Major Release: Streamlined Installation & Zero-Redundancy Project Structure

This release focuses on simplifying the user experience by consolidating all installation, validation, and testing into a single streamlined workflow. Users no longer need to run multiple scripts or worry about patches and upgrades.

### Added
- **Integrated Installation Workflow**: `install-complete.sh` now automatically:
  - Detects environment (WSL, Git Bash, Cygwin, macOS, Linux)
  - Applies platform-specific fixes automatically
  - Validates installation with comprehensive tests
  - Offers optional audio testing at the end
  - All in one smooth, automated process
- **Organized Directory Structure**:
  - `scripts/internal/` - Internal tools auto-run by installer (users don't need to know about these)
  - `scripts/tests/` - Testing tools auto-run by installer (users don't need to run manually)
- **Interactive Audio Testing**: Installer now asks if users want to test audio playback
- **Comprehensive Validation**: Automated 5-point validation during installation

### Changed
- **Simplified Installation**: From 6 manual steps down to 1 command
  - Before v3.0: Clone → Install → Verify → Test → Configure → Restart
  - v3.0: Clone → Install (everything else automatic) → Restart
- **Success Rate Improvement**: From 95% to 98%+ due to integrated diagnostics
- **Installation Time**: Reduced from 2-5 minutes to 1-2 minutes
- **Upgrade Method**: Now recommends uninstall + fresh install instead of upgrade scripts
  - Simpler, cleaner, no conflicts with old structure
  - Takes only 1-2 minutes
  - Guarantees optimal configuration

### Removed (Streamlining)
- **Redundant Scripts**:
  - ❌ `install.sh` - Replaced by enhanced `install-complete.sh`
  - ❌ `upgrade.sh` - Users should uninstall + reinstall for v3.0
  - ❌ Manual `check-setup.sh` runs - Now auto-runs during installation
  - ❌ Manual `detect-environment.sh` runs - Now integrated into installer
  - ❌ Manual path testing - Now automatic during installation
- **Redundant Documentation**:
  - Removed scattered .md files (AI_INSTALL.md, UTILITIES_README.md, etc.)
  - Everything now in README.md only
  - Cleaner, more maintainable documentation

### Relocated (Better Organization)
- `scripts/detect-environment.sh` → `scripts/internal/detect-environment.sh`
- `scripts/apply-windows-fix.sh` → `scripts/internal/apply-windows-fix.sh`
- `scripts/check-setup.sh` → `scripts/tests/check-setup.sh`
- `scripts/test-path-utils.sh` → `scripts/tests/test-path-utils.sh`
- `scripts/test-path-conversion.sh` → `scripts/tests/test-path-conversion.sh`

### Enhanced
- **install-complete.sh v3.0** (was v2.1):
  - Integrated environment detection
  - Automatic platform-specific fixes
  - Comprehensive validation (7 checks)
  - Interactive audio testing option
  - Better error reporting and troubleshooting guidance
- **README.md**:
  - Updated to v3.0 with accurate script references
  - Simplified installation instructions
  - Removed references to deleted scripts
  - Updated troubleshooting section
  - Clearer upgrade instructions
  - Accurate project structure diagram

### User Benefits
- ✅ **One-Command Installation**: Everything handled automatically
- ✅ **No Manual Testing Required**: Installer validates everything
- ✅ **No Patches Needed**: All fixes applied automatically
- ✅ **Cleaner Project**: Only essential user-facing scripts remain
- ✅ **Better Documentation**: Single source of truth (README.md)
- ✅ **Faster Installation**: 1-2 minutes vs 2-5 minutes
- ✅ **Higher Success Rate**: 98%+ vs 95%

### Breaking Changes
- **Directory structure changed**: Old scripts moved to `internal/` and `tests/`
- **Removed scripts**: Users upgrading from v2.x should uninstall first, then install v3.0
- **No upgrade.sh**: Fresh install recommended for cleanest experience

### Migration Guide
For users upgrading from v2.x or earlier:
```bash
cd ~/claude-code-audio-hooks
bash scripts/uninstall.sh  # Remove old version
git pull origin master      # Get v3.0
bash scripts/install-complete.sh  # Fresh install
```

### Technical Details
- Version: 3.0.0
- Scripts reorganized: 11 scripts → 4 user-facing + 5 internal/test scripts
- Installation steps: 11 automated steps (up from 10)
- Total lines of code: Reduced by removing redundancy
- Success rate: 98%+
- Installation time: 1-2 minutes

---

## [2.4.0] - 2025-11-06

### Added
- **Dual Audio System**: Complete flexibility to choose between voice and non-voice notifications
  - 9 new modern UI chime sound effects in `audio/custom/` directory
  - 9 refreshed voice notifications in `audio/default/` directory (Jessica voice from ElevenLabs)
- **Pre-configured Examples**:
  - `config/example_preferences_chimes.json` - All chimes configuration
  - `config/example_preferences_mixed.json` - Mixed voice and chimes with scenario templates
- **Audio Customization Documentation**: New comprehensive section in README explaining:
  - Three audio options (voice-only, chimes-only, mixed)
  - Quick-start guide for switching to chimes
  - Available audio files comparison table
  - Configuration scenarios for different use cases
- **User Choice Philosophy**: System now supports complete user customization
  - Default configuration uses voice (existing behavior preserved)
  - Users can easily switch to chimes or create mixed configurations
  - Simple one-file configuration change to switch audio sets

### Changed
- README.md updated with new "Audio Customization Options" section
- Version badges updated to v2.4.0
- Table of Contents updated with new audio customization section

### Enhanced
- User flexibility: Users can now choose audio style based on personal preference
- Music-friendly option: Chimes don't interfere with background music
- Mixed configurations: Different audio types for different notification priorities

### Background
This release addresses user feedback requesting non-voice notification options, particularly for users who:
- Play music while coding
- Prefer instrumental sounds over AI voices
- Want different audio styles for different notification types

The dual audio system maintains backward compatibility (default voice notifications) while providing complete flexibility for users who want alternatives.

## [2.3.1] - 2025-11-06

### Fixed
- Critical bug in configure.sh save_configuration() function that prevented saving on macOS
- Python heredoc in configure.sh now correctly passes CONFIG_FILE path using shell variable substitution
- Resolved IndexError when accessing sys.argv[1] in Python heredoc

## [2.3.0] - 2025-11-06

### Added
- Full compatibility with macOS default bash 3.2
- Bash version detection in install.sh with helpful warnings
- Compatibility notes in scripts for macOS users

### Fixed
- Replaced bash 4+ associative arrays with indexed arrays in configure.sh and test-audio.sh
- Replaced bash 4+ case conversion operators (${var^^} and ${var,,}) with tr commands in path_utils.sh
- All scripts now work with bash 3.2+ without requiring Homebrew bash on macOS

### Changed
- Refactored configure.sh to use parallel indexed arrays instead of associative arrays
- Refactored test-audio.sh to use parallel indexed arrays for configuration data
- Updated path_utils.sh to use portable tr command for case conversion
- Enhanced README with macOS compatibility information

## [2.2.0] - Previous Release

### Added
- Automatic format compatibility for Claude Code v2.0.32+
- Git Bash path conversion fixes
- Enhanced Windows compatibility

### Fixed
- Path conversion issues on Git Bash
- Audio playback on various Windows environments

## [2.1.0] - Previous Release

### Added
- Hook trigger logging system
- Diagnostic tools for troubleshooting
- View-hook-log.sh script for monitoring hook triggers

## [2.0.0] - Major Release

### Added
- 9 different hook types (up from 1 in v1.0)
- Professional ElevenLabs audio files
- Interactive configuration tool
- JSON-based user preferences
- Audio queue system
- Debounce system
- Automatic v1.0 upgrade support

### Changed
- Complete project restructure
- Modular hook system with shared library
- Cross-platform support improvements

## [1.0.0] - Initial Release

### Added
- Basic stop hook with audio notification
- Simple installation script
- Custom audio support
