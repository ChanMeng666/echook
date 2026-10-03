# Releasing echook

How a change gets from a working tree to users, as it was actually done for v6.6.0 through v6.7.2 (2026-10-03 and 2026-10-04). Follow it in order; each step exists because skipping it went wrong once.

`AGENTS.md` is the operating guide for the code. This page covers only the release mechanics.

## Before you start

- **Work in a git worktree inside the repository**, at `.claude/worktrees/<name>`, on its own branch. The main checkout stays on `master`. Several agents can work in sibling worktrees and their branches merge cleanly when file ownership is agreed first; v6.7.0 was three such branches merged without a conflict.
- **Never run `audio-hooks install`, `uninstall` or `upgrade` while developing**, and never run the CLI from a checkout against the real data directory: set `CLAUDE_PLUGIN_DATA` to a scratch directory first. The test suite isolates itself (`tests/_isolation.py`); a hand-run CLI does not.
- **Do not print the whole environment** (`env`, `set`, `os.environ`) in a test, a script or a debugging aid. A developer's shell can hold credentials, and a full dump puts them in a log.
- **Write files with an editor or a script file, not a shell heredoc, when the content contains backslashes.** In the Bash tool used for this project a heredoc collapsed `\\` to `\` and turned `\x89` into a raw byte, which silently corrupted a test file twice. Python source with escapes, regexes and Windows paths are the usual victims.

## 1. Make the change

1. Edit the canonical sources (`/hooks/`, `/bin/`, `/audio/`, `/config/`, `/cursor-hooks/`, `/codex-hooks/`). `plugins/audio-hooks/hooks/hooks.json`, `plugin.json`, `runner/run.py`, `skills/`, `evals/`, the plugin `README.md` and `.claude-plugin/icon.png` are edited in place.
2. `bash scripts/build-plugin.sh` to sync the mirrors.
3. Update every affected document in the same change: `CHANGELOG.md`, `AGENTS.md`, `README.md`, `llms.txt`, the files under `docs/`, the skill, and — when what the plugin runs, sends or writes has changed — `plugins/audio-hooks/README.md` and `PRIVACY.md`. A release is not split into a code commit and a later docs commit.
4. Take counts from the CLI, not from memory: `audio-hooks manifest`, `audio-hooks hooks list --variants`, `audio-hooks statusline segments`.

### If the change adds a sound

A new event or variant needs its own sound in **both** themes; `tests/test_plugin_hooks_contract.py::TestAudioUniqueness` fails otherwise.

1. Add two entries to `config/audio_manifest.json`: a `voice` entry for the `default` theme (a short spoken phrase) and a `sound_effect` entry for the `custom` theme (a prompt, `duration_seconds`, `prompt_influence`), named and tagged like their nearest siblings.
2. Generate only the new files. The generator needs an ElevenLabs API key in the environment; it is not stored on the maintainer's machine, so the maintainer has to supply it for the session.
   ```bash
   ELEVENLABS_API_KEY=… python scripts/generate-audio.py --dry-run --only a.mp3,chime-a.mp3
   ELEVENLABS_API_KEY=… python scripts/generate-audio.py --only a.mp3,chime-a.mp3
   ```
   Never write the key to a file, a commit or a log, and tell the maintainer to rotate a key that was pasted into a chat.
3. Check the result mechanically — distinct hashes, a valid MP3 header, a duration in line with the siblings (`ffprobe -show_entries format=duration`) — and then **have a person listen**. The six files added in v6.7.0 passed the mechanical checks and had not been auditioned when they shipped.
4. `bash scripts/build-plugin.sh` copies them into the plugin.

## 2. Verify locally

```bash
bash scripts/build-plugin.sh
python -m unittest discover tests            # all pass; 2 POSIX-only tests are skipped on Windows
bash scripts/build-plugin.sh --check         # {"ok":true,"in_sync":true,…}
claude plugin validate --strict plugins/audio-hooks
claude plugin validate .
```

Run the suite once **without** any data-directory override and confirm the real `user_preferences.json`, its `.bak` and `~/.claude/settings.json` hash the same before and after. That is the check that caught the suite writing to the real plugin data directory before v6.6.0.

For anything that edits a user's files or deletes things (the uninstall path was the case in point), exercise it end to end in a **contained fake home** before trusting unit tests: a throwaway copy of the project, `env -i` with `HOME`, `USERPROFILE`, `HOMEDRIVE`/`HOMEPATH`, `TEMP`, `TMP` and `TMPDIR` all pointed inside the scratch directory, a path audit of the code first, and a hash of the real files before and after every run. On Windows the embedded Python reads `USERPROFILE` while the shell reads `HOME`, so both must be redirected.

An independent review by a reader who was given the code and its intent, not the author's account of it, found real defects in every round of v6.6.0 and v6.7.0. It is worth doing for any change to the CLI's argument handling, the preferences migration, or anything that writes outside the data directory.

## 3. Bump the version

```bash
bash scripts/bump-version.sh <new_version>
```

It stamps the 12 canonical version locations and four document headers, runs `build-plugin.sh` and the test suite, validates every target before writing anything, and reports `CHANGELOG.md` and `llms.txt` under `needs_hand_written`. Write those two by hand. Raise the version for every release that changes the plugin folder: the plugin directory requires it (see `docs/DIRECTORY_LISTING.md`).

A documentation-only change that touches nothing under `plugins/audio-hooks/` does not need a version bump.

## 4. Pull request and CI

```bash
git push -u origin <branch>
gh pr create --base master --head <branch> --title … --body …
gh pr checks <number> --watch
```

CI is `.github/workflows/smoke.yml`: the unit tests on Ubuntu, Windows and macOS with Python 3.9, 3.12 and 3.13, `build-plugin.sh --check`, and a `plugin-validate` job that installs the latest Claude Code and runs its validator. Read a job's log rather than trusting its colour at least once per release; `gh run view <run> --job <job> --log` shows the test count and the validator's own "Validation passed" line. The CI matrix is the only place the suite runs on Linux and macOS.

**GitGuardian also runs on every pull request**, and it has produced one false positive. In v6.7.0 it reported "Authentication Tuple" for the `SYNTHETIC_EVENT_MAP` entry of the `notification_auth_storage_failure` variant in `hooks/hook_runner.py`: a key containing `auth` whose value is a tuple of two strings, the parent event and a sound file name. That is a variant name mapped to a sound file, not a credential. The line is not reproduced here on purpose — the first draft of this page quoted it verbatim and the scanner flagged the documentation too, under the same incident. When it happens: confirm what was flagged (`gh api repos/<owner>/<repo>/commits/<sha>/check-runs`), confirm no real secret is in the branch, and report it to the maintainer. Do not merge past a failing secret scan on your own, and do not reword the code to slip under the detector; the maintainer decides, and resolves the incident in the GitGuardian dashboard. `master` has no branch protection, so nothing technical stops a merge — which is exactly why the decision has to be explicit.

## 5. Merge, tag, release

```bash
gh pr merge <number> --rebase        # or --squash for a branch with many commits
git -C <main checkout> pull --ff-only origin master
git tag -a v<version> <merged commit> -m "v<version>"
git push origin v<version>
gh release create v<version> --verify-tag --title "v<version> — <what changed, in the user's terms>" --notes …
```

Release titles and notes are written as a short narrative of what changed and why, followed by what was and was not verified; see the existing releases. Then remove the worktree and delete the branch locally and on the remote.

**A push to `master` is no longer a private event.** A webhook tells Anthropic's plugin directory about it within minutes and the directory scans the new commit. See `docs/DIRECTORY_LISTING.md` before merging anything that changes what the plugin runs, sends or writes.

## 6. After the release

- **A machine with the plugin installed keeps running its cached copy.** `audio-hooks upgrade` moves it to the new version (data preserved), and `/reload-plugins` must then be typed in each Claude Code session that is already open — there is no command-line equivalent, and a session started afterwards needs no reload. Verify with `audio-hooks status`, `audio-hooks diagnose` and `audio-hooks hooks list --variants`.
- **The preferences file is migrated by the first hook event or state-changing command**, or on request by `audio-hooks migrate`. `status` and `diagnose` no longer do it.
- **Check the directory.** The plugin page shows whether the new commit was picked up, scanned and held or passed.
- **Clean up scratch files.** Keep the raw evidence behind anything recorded in `docs/EVENT_BEHAVIOR_NOTES.md` that cannot be regenerated — a carved binary extract disappears when Claude Code updates, and documentation pages change — and delete the rest.

## Re-syncing against a new Claude Code release

The procedure and its evidence standards are in `docs/EVENT_BEHAVIOR_NOTES.md` ("How to re-sync against a new Claude Code release"). In short: read the changelog range, diff the current hooks, status line and plugin documentation against what echook registers, read the installed binary for what the documentation omits, and settle anything that matters with a live experiment. Documentation fetched through a summarising tool misreported field names during the 2.1.288 sync; fetch the raw Markdown.
