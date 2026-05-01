# Installation Guide

> **Version:** 5.1.5 | **Last Updated:** 2026-05-01

The install is two slash commands inside Claude Code. This page is a pointer to the canonical install paths — there are no human-only steps to read through.

> **Upgrading from 5.1.4 or earlier?** Don't `/plugin uninstall + install` manually — that destroys your `user_preferences.json`. Run `audio-hooks upgrade` instead. It auto-detects the install scope, tries `claude plugin update` (data-preserving) first, and falls back to `uninstall --keep-data + install` if needed. Migration on next load merges any new template keys into your config without overwriting your customizations. Disaster recovery: `audio-hooks backup list` / `audio-hooks backup restore latest-external`.

## Recommended: plugin install

Inside Claude Code, run:

```text
/plugin marketplace add ChanMeng666/claude-code-audio-hooks
/plugin install audio-hooks@chanmeng-audio-hooks
```

Then verify and smoke-test:

```text
> run audio-hooks status
> run audio-hooks test all
```

That's it. All 26 hook events register, every audio file is bundled, and `${CLAUDE_PLUGIN_DATA}/user_preferences.json` is auto-initialised on first read. The `/audio-hooks` SKILL ships with the plugin so you can configure everything via natural language afterwards.

## Alternative: legacy script install

For users who'd rather not use the plugin system:

```bash
git clone https://github.com/ChanMeng666/claude-code-audio-hooks.git
cd claude-code-audio-hooks
bash scripts/install-complete.sh
```

The installer auto-engages non-interactive mode when stdin is not a TTY or `CLAUDE_NONINTERACTIVE=1` is set, so AI agents and CI can run it without prompts. For Windows native (PowerShell), use `.\scripts\install-windows.ps1`.

**Don't enable both paths** — they fire on every event independently and you'll hear double audio. `audio-hooks diagnose` reports `DUAL_INSTALL_DETECTED` if it finds both and tells you exactly how to fix it.

## Lite tier (zero-dependency, no Python)

For users who want only desktop notifications + system sounds (no MP3s, no TTS, no webhooks):

```bash
curl -sL https://raw.githubusercontent.com/ChanMeng666/claude-code-audio-hooks/master/scripts/quick-setup.sh | bash
```

Customise enabled hooks without cloning:

```bash
curl -sL .../quick-configure.sh | bash -s -- --list
curl -sL .../quick-configure.sh | bash -s -- --disable SubagentStop
curl -sL .../quick-configure.sh | bash -s -- --only Stop Notification
```

Uninstall:

```bash
curl -sL .../quick-unsetup.sh | bash
```

## Prerequisites

| Requirement | Plugin install | Script install | Lite tier |
|---|---|---|---|
| Claude Code v2.1.80+ | ✓ | ✓ | ✓ |
| Python 3.6+ | ✓ (auto-detected, prefers `python3` then `python` then `py`) | ✓ | — |
| PowerShell (Windows) | ✓ (for audio playback) | ✓ | ✓ |
| `mpg123` / `ffplay` / `paplay` / `aplay` (Linux) | one of these | one of these | — |

## Verifying your install

```text
> run audio-hooks diagnose
```

Expected output for a healthy install: `ok: true`, `errors: []`, `warnings: []`, `audio_files: { present: 26, expected: 26 }`, `install: { script_install: ..., plugin_install: ... }` (exactly one of these `true`).

If anything is broken, the diagnose output includes a `suggested_command` for each error. Run that command.

## See also

- [README.md](../README.md) — public introduction with `audio-hooks` CLI reference + mermaid diagrams
- [CLAUDE.md](../CLAUDE.md) — canonical AI-facing operating guide (decision tree for natural-language requests)
- [docs/ARCHITECTURE.md](ARCHITECTURE.md) — developer-facing architecture deep dive
- [docs/TROUBLESHOOTING.md](TROUBLESHOOTING.md) — troubleshooting (mostly a pointer to `audio-hooks diagnose`)
- [CHANGELOG.md](../CHANGELOG.md) — full changelog, including the 5.1.5 painless-upgrades release notes
