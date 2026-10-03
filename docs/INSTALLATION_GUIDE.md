# Installation Guide

> **Version:** 6.7.0 | **Last Updated:** 2026-10-03

**echook is AI-agent-first.** A human doesn't follow these steps — your AI agent (Claude Code, Cursor, or Codex) does. Point it at this repo and ask it to install/configure/uninstall; it runs every command below and reports back. This page documents the full pull → install → configure → verify → uninstall flow so the agent (and a curious human) can see exactly what happens. There are no interactive prompts and no human-only steps — the one exception is Claude Code's `/reload-plugins`, which has no CLI equivalent.

> **Upgrading from 5.1.4 or earlier?** Don't `/plugin uninstall + install` manually — that destroys your `user_preferences.json`. Run `audio-hooks upgrade` instead. It auto-detects the install scope, tries `claude plugin update` (data-preserving) first, and falls back to `uninstall --keep-data + install` if needed. Migration on next load merges any new template keys into your config without overwriting your customizations. Disaster recovery: `audio-hooks backup list` / `audio-hooks backup restore latest-external`.

## Recommended: plugin install

Your agent runs these two commands (`audio-hooks install --plugin` prints exactly them and changes nothing itself):

```bash
claude plugin marketplace add ChanMeng666/echook --json
claude plugin install audio-hooks@chanmeng-audio-hooks --json
```

Then it asks you to type `/reload-plugins` inside Claude Code — the one step with no CLI equivalent. (Claude Code 2.1.268 made the `/plugin` *menu* take effect when it closes, but whether a shell `claude plugin install` reaches an already-running session without `/reload-plugins` is not established, so the step stays.)

Then verify and smoke-test:

```text
> run audio-hooks status
> run audio-hooks test all
```

That's it. All 39 hook events register, every audio file is bundled, and `${CLAUDE_PLUGIN_DATA}/user_preferences.json` is auto-initialised by the first hook event or the first state-changing command (`audio-hooks test all` is one); since v6.7.0 `status` and `diagnose` only report and write nothing, so a fresh install may show no preferences file until then. The `/audio-hooks` SKILL ships with the plugin so you can configure everything via natural language afterwards.

## Alternative: script install (cloned repo, no plugin system)

For setups that don't use the plugin system — your agent runs this:

```bash
git clone https://github.com/ChanMeng666/echook.git
cd echook
bash scripts/install-complete.sh
```

The installer is **always non-interactive** — it never prompts, so AI agents and CI run it unattended. It registers `hook_runner.py` in `~/.claude/settings.json`. For Windows native (PowerShell), use `.\scripts\install-windows.ps1`. `audio-hooks install --scripts` runs the same installer through the CLI — it must be given that explicit flag, because since v6.6.0 `audio-hooks install` has **no default mode** (a bare `install`, an unknown argument, or two modes returns `INVALID_USAGE` and changes nothing), and it is refused with `DUAL_INSTALL_DETECTED` while the plugin is installed unless you pass `--force`. Uninstall with `audio-hooks uninstall` (bare = `--scripts`). Since v6.6.0 it removes the script install natively on Windows, macOS and Linux: it backs up to `~/.claude/backups/audio-hooks-uninstall-<timestamp>/`, removes only echook's own hook and permission entries from `settings.json` / `settings.local.json` and only files in `~/.claude/hooks/` that carry echook's content marker (your own `stop_hook.sh` or `shared/` is left alone and reported), and preserves your config and audio (before v6.6.0 it did nothing on Windows). If it reports `UNINSTALL_INCOMPLETE`, read `unmatched_references` and then run `audio-hooks uninstall --remove-unmatched`. Its `--purge` applies to `--cursor` / `--codex` only; in a source checkout, `bash scripts/uninstall.sh --yes --purge` (a wrapper around `uninstall --scripts`) also removes that checkout's config and audio defaults after backing them up.

**Don't enable both paths** — they fire on every event independently and you'll hear double audio. `audio-hooks diagnose` reports `DUAL_INSTALL_DETECTED` if it finds both and tells you exactly how to fix it.

## Cursor IDE

The project ships AI-first install paths for Cursor IDE 3.2.16+. There are two of them; the right one depends on whether you also have Claude Code.

### Path A — Cursor + Claude Code (auto-bridge)

If you already have Claude Code on the same machine, run the recommended plugin install above. Cursor 3.2.16+ then auto-bridges every Claude Code plugin per [cursor.com/docs/reference/third-party-hooks](https://cursor.com/docs/reference/third-party-hooks). Enable Cursor Settings → "Third-party skills" if not already on.

Verify by asking your agent:

```text
> run audio-hooks status
```

Expected: `editor_targets.cursor.state` == `bridged-via-claude-code`. 8 events bridge (`pretooluse`, `posttooluse`, `userpromptsubmit`, `stop`, `subagent_stop`, `session_start`, `session_end`, `precompact`) — `Notification` and `PermissionRequest` have no Cursor equivalent (per Cursor's docs) and stay silent under Cursor by design. The live list is `supported_editors.cursor.bridged_events_subset` in `audio-hooks manifest`.

### Path B — Cursor without Claude Code (native install)

Paste a single prompt into Cursor's agent chat:

> *"Clone https://github.com/ChanMeng666/echook into ~/audio-hooks, then run `python ~/audio-hooks/bin/audio-hooks install --cursor`. After it succeeds, restart Cursor."*

The `install --cursor` subcommand:

- Reads the canonical `cursor-hooks/hooks.json` template.
- Substitutes `{{PYTHON}}` and `{{HOOK_RUNNER}}` with absolute paths.
- Merges into `~/.cursor/hooks.json` (preserves any of your other Cursor hooks).
- Tags every entry with `_managed_by: "audio-hooks"` so uninstall is scope-safe.
- Seeds `~/.cursor/audio-hooks-data/user_preferences.json` from the bundled defaults.

It is fully non-interactive (no prompts, no menus) and idempotent (re-running does not duplicate entries).

The native install registers 11 Cursor-native event types — the 8 bridge-mapped events plus `subagentStart`, `postToolUseFailure`, and `afterFileEdit` (Cursor-only events with no Claude Code equivalent and so absent from the auto-bridge).

**`DUPLICATE_BRIDGE` guard:** if Claude Code's plugin is already installed, `install --cursor` aborts to prevent double audio. Pass `--force` only if you understand the trade-off.

### Upgrading the Cursor-only install

```bash
cd ~/audio-hooks && git pull && python bin/audio-hooks install --cursor
```

Re-running the install is idempotent and preserves `~/.cursor/audio-hooks-data/user_preferences.json`. There is no separate `audio-hooks upgrade --cursor` subcommand — `audio-hooks upgrade` targets Claude Code's plugin cache.

### Uninstalling the Cursor-only install

```bash
python ~/audio-hooks/bin/audio-hooks uninstall --cursor
```

Removes only entries tagged `_managed_by: "audio-hooks"` from `~/.cursor/hooks.json`. Preserves `~/.cursor/audio-hooks-data/user_preferences.json` so a future re-install picks up your settings. Pass `--purge` to delete that data dir as well.

## Codex

OpenAI's Codex does NOT auto-bridge Claude Code plugins. Use the Codex plugin path when available; use the native registration at `~/.codex/hooks.json` when the user prefers a cloned repo or is on an older Codex build.

Plugin install prompt:

> *"Run `codex plugin marketplace add ChanMeng666/echook`, then `codex plugin add audio-hooks@chanmeng-audio-hooks`. Ask me to reload plugins if Codex requires it, then verify with `audio-hooks status` and `audio-hooks test all`."*

Native install prompt:

> *"Clone https://github.com/ChanMeng666/echook into ~/audio-hooks, then run `python ~/audio-hooks/bin/audio-hooks install --codex`. Read the JSON output: only follow `next_steps` if `feature_flag_state` is `disabled`, `disabled_legacy`, or `parse_error`. Then restart Codex."*

The `install --codex` subcommand:

- Reads the canonical `codex-hooks/hooks.json` template.
- Substitutes `{{PYTHON}}` (`python`/`python3`) and `{{HOOK_RUNNER}}` (absolute path) into every command, with Windows backslashes JSON-escaped.
- Bakes a `--invoker codex` CLI flag into every command (Codex sets no env var we could detect by, unlike Cursor's `CURSOR_VERSION`).
- Merges into `$CODEX_HOME/hooks.json` (default `~/.codex/hooks.json`), tagging each entry with `_managed_by: "audio-hooks"` so future uninstalls leave foreign hooks untouched.
- Seeds `$CODEX_HOME/audio-hooks-data/user_preferences.json` from the bundled defaults.
- Writes `$CODEX_HOME/audio-hooks-data/install_marker.json` for diagnostics.

**Hooks feature-state handling (AI-first):** Codex hooks are enabled by default. The install:

- **Leaves `config.toml` untouched** when it is missing or has no hooks feature entry.
- **Skips silently** when hooks are enabled by default or explicitly enabled.
- **Emits a `next_steps` instruction** in JSON only when the file explicitly disables hooks with `[features].hooks = false` or cannot be parsed. We never round-trip user-authored TOML — formatting and comments would be destroyed.

The 10 events Codex supports (`SessionStart`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `UserPromptSubmit`, `SubagentStart`, `SubagentStop`, `Stop`) are all registered. Other audio-hooks canonical events have no Codex equivalent and the runner no-ops them with a `skipped_no_codex_equivalent` debug NDJSON event.

### Verifying the Codex install

```bash
audio-hooks status
# expect editor_targets.codex.state == "active"
# (or "active-but-hooks-disabled" if [features].hooks = false is still present)
```

### Upgrading the Codex install

```bash
cd ~/audio-hooks && git pull && python bin/audio-hooks install --codex
```

Re-running the install is idempotent: it strips any prior `_managed_by: "audio-hooks"` entries from `~/.codex/hooks.json` before writing the fresh ones, and preserves your `user_preferences.json` automatically. There is no separate `audio-hooks upgrade --codex` subcommand.

### Uninstalling the Codex install

```bash
python ~/audio-hooks/bin/audio-hooks uninstall --codex
```

Removes only entries tagged `_managed_by: "audio-hooks"` from `~/.codex/hooks.json`. Preserves `~/.codex/audio-hooks-data/user_preferences.json`. Pass `--purge` to also delete that directory. **Never touches `~/.codex/config.toml`**.

## Prerequisites

| Requirement | Plugin install | Script install |
|---|---|---|
| Claude Code v2.1.80+ | ✓ | ✓ |
| Python 3.6+ | ✓ (auto-detected, prefers `python3` then `python` then `py`) | ✓ |
| PowerShell (Windows) | ✓ (for audio playback) | ✓ |
| `mpg123` / `ffplay` / `paplay` / `aplay` (Linux) | one of these | one of these |

## Verifying your install

```text
> run audio-hooks diagnose
```

Expected output for a healthy install: `ok: true`, `errors: []`, `warnings: []`, `audio_files: { present: 26, expected: 26 }`, `install: { script_install: ..., plugin_install: ... }` (exactly one of these `true`).

If anything is broken, the diagnose output includes a `suggested_command` for each error. Run that command.

## See also

- [README.md](../README.md) — public introduction (features, value, mermaid diagrams)
- [docs/CLI_REFERENCE.md](CLI_REFERENCE.md) — `audio-hooks` CLI subcommands, config keys, env vars, error codes
- [docs/NATURAL_LANGUAGE_CONTROL.md](NATURAL_LANGUAGE_CONTROL.md) — every natural-language prompt, with diagrams
- [AGENTS.md](../AGENTS.md) — canonical AI-facing operating guide (decision tree for natural-language requests)
- [docs/ARCHITECTURE.md](ARCHITECTURE.md) — developer-facing architecture deep dive
- [docs/TROUBLESHOOTING.md](TROUBLESHOOTING.md) — troubleshooting (mostly a pointer to `audio-hooks diagnose`)
- [CHANGELOG.md](../CHANGELOG.md) — full changelog, including the 5.1.5 painless-upgrades release notes
