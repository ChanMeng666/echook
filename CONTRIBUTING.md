# Contributing to Echook

Thank you for your interest in contributing! This guide explains how to get involved.

## How to Contribute

### Reporting Bugs

If you find a bug, please [open an issue](https://github.com/ChanMeng666/echook/issues/new) with:

- Steps to reproduce the problem
- Expected vs. actual behavior (screenshots or logs help)
- Your environment (OS, and relevant runtime/version)

### Suggesting Features

Have an idea? [Open a feature request](https://github.com/ChanMeng666/echook/issues/new) describing the problem you want to solve and your proposed solution.

### Submitting Changes

1. **Fork** the repository and **clone** your fork:
   ```bash
   git clone https://github.com/<your-username>/echook.git
   cd echook
   ```
2. **Create a branch** for your changes:
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **Make your changes** and verify them locally (see Development Setup below).
4. **Commit** with a clear message following [Conventional Commits](https://www.conventionalcommits.org/):
   ```bash
   git commit -m "feat: short description of your change"
   ```
5. **Push** and open a Pull Request against the `master` branch.

## Development Setup

echook keeps a **single canonical source** that is synced into the Claude Code plugin layout by a build script. Always edit the canonical files, never the `plugins/audio-hooks/` mirror.

### Repository layout

```
echook/
├── .claude-plugin/marketplace.json
├── plugins/audio-hooks/              # plugin layout — MIRROR, populated by build-plugin.sh
│   ├── .claude-plugin/plugin.json
│   ├── hooks/hooks.json              # matcher-scoped registration (hand-edited here)
│   ├── runner/run.py
│   ├── skills/audio-hooks/SKILL.md
│   ├── bin/  ·  audio/  ·  config/
│   ├── cursor-hooks/  ·  codex-hooks/
├── hooks/                            # CANONICAL: hook_runner.py, invoker.py, user_preferences.py
├── bin/                              # CANONICAL: audio-hooks(.py/.cmd) + audio-hooks-statusline
├── audio/                            # CANONICAL: default/ (voice) + custom/ (chimes)
├── config/                           # default_preferences.json, schema, audio_manifest.json
├── cursor-hooks/hooks.json           # CANONICAL: Cursor IDE install template
├── codex-hooks/hooks.json            # CANONICAL: Codex CLI install template
├── scripts/                          # install / build-plugin / uninstall / bump-version / generate-audio
└── tests/                            # unittest suite (Cursor + Codex bridge contracts)
```

### Workflow

1. Edit canonical files (`/hooks/`, `/bin/`, `/audio/`, `/config/`, `/cursor-hooks/`, `/codex-hooks/`).
2. Run `bash scripts/build-plugin.sh` to sync into the plugin layout.
3. CI verifies in-sync via `bash scripts/build-plugin.sh --check`.
4. Validate: `claude plugin validate plugins/audio-hooks`.
5. Test: `python -m unittest discover -v tests` (Ubuntu/Windows/macOS × Python 3.9/3.12/3.13 in CI). **Not** pytest.
6. Bump version (when releasing): `bash scripts/bump-version.sh <new_version>` — atomically updates all canonical version locations and re-runs `build-plugin.sh`.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full system design, hook lifecycle, and how to add a new hook event or audio file. The release procedure — verification, version bump, CI, tagging, and upgrading a live install — is in [docs/RELEASING.md](docs/RELEASING.md), and [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) says where the project stands and what has already been evaluated and set aside. Since 2026-10-04 every push to `master` is scanned by Anthropic's plugin directory; see [docs/DIRECTORY_LISTING.md](docs/DIRECTORY_LISTING.md) before changing anything under `plugins/audio-hooks/`.

## Code of Conduct

By participating, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md). For questions or
support, see [SUPPORT.md](SUPPORT.md). For security issues, see [SECURITY.md](SECURITY.md).
