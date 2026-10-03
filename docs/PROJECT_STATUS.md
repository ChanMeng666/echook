# Project status

**Snapshot taken 2026-10-04, at v6.7.2.** The plugin folder last changed at commit `f692417`; the commits after it on `master` are documentation only. This page says where the project stands: what shipped recently, what is waiting on someone, what is claimed but not verified, and what was looked at and deliberately left alone. Everything here can go stale; each section says how to re-check it. When you change something listed here, update this page in the same commit.

For how the code works, read `AGENTS.md`. For measured upstream behaviour, `docs/EVENT_BEHAVIOR_NOTES.md`. For the release mechanics, `docs/RELEASING.md`. For the directory listing, `docs/DIRECTORY_LISTING.md`.

## Live numbers

Take these from the CLI, not from this table: `audio-hooks manifest`, `audio-hooks hooks list --variants`, `audio-hooks statusline segments`.

| | v6.7.2 |
|---|---|
| Canonical hook events | 39 (Claude Code registers 30 of them; Claude Code itself has 33 events at 2.1.288) |
| Matcher variants | 47 |
| Handlers in `plugins/audio-hooks/hooks/hooks.json` | 70, all `async: true` |
| Distinct sounds | 86 per theme, 87 mp3 files per theme (one fallback) |
| Status line segments | 33, of which 31 are in the default set; `prompt_cache` and `remote` are opt-in |
| CLI subcommands | 21 top-level, 41 forms |
| Error codes in the manifest | 37 |
| Tests | 689 (2 skipped on Windows) |
| Synced against Claude Code | 2.1.288 |

## What shipped in this cycle

| Version | Date | In one line |
|---|---|---|
| 6.6.0 | 2026-10-03 | The CLI stopped acting on arguments it does not understand; `uninstall` removes a script install natively on every platform; the test suite isolates itself; synced to Claude Code 2.1.288. |
| 6.7.0 | 2026-10-03 | Three new matcher variants with their own sounds; four status line segments; reporting commands became read-only; `audio-hooks migrate`; plugin README, sensitive webhook option, skill evals; `AGENTS.md` became the single guide. |
| 6.7.1 | 2026-10-04 | `PRIVACY.md` and the directory listing links in `plugin.json`. No behaviour change. |
| 6.7.2 | 2026-10-04 | Listing icon. No behaviour change. |

The cycle began with an incident, and most of 6.6.0 follows from it. On 2026-10-03 an AI agent probing for usage ran `audio-hooks install --help` on a machine that already had the plugin. The command ignored the flag, ran the legacy script installer and registered every hook a second time in `~/.claude/settings.json`. Following that thread found that `upgrade --help` ran a real upgrade, that several `set`-style commands wrote config when probed, that `uninstall` did nothing on Windows while reporting success, that the uninstall script could delete a user's own hooks, and that the test suite wrote to the real plugin data directory. All of those are fixed; the reasoning is in the 6.6.0 changelog entry and in the gotchas at the end of `AGENTS.md`.

## Waiting on someone

| Item | Who | How to check |
|---|---|---|
| Plugin directory review. Submitted 2026-10-04; security scan passed; held for content policy review. | Anthropic reviewer, then the maintainer selects **Publish** | The plugin page in the developer portal (`docs/DIRECTORY_LISTING.md`) |
| GitGuardian incident 37836366, a false positive on a sound-file mapping in `hooks/hook_runner.py` (see `docs/RELEASING.md`). It stays open until resolved in the dashboard. | Maintainer | GitGuardian dashboard |
| The six sound files added in 6.7.0 have not been listened to by a person. They are valid, distinct and of normal length. | Maintainer | Play `audio/default/{notif-auth-storage,fail-cloud-credential,fail-verification}.mp3` and `audio/custom/chime-*` counterparts |

## Claimed but not verified

These are stated in the changelog's "Not verified" sections; they are collected here so that nobody builds on them as if they were measured.

**Never provoked in a live session**
- The three matcher values registered in 6.7.0 (`cloud_credential_error`, `verification_required`, `auth_storage_failure`). They are present in the 2.1.288 binary; no real event was captured.
- An internal-agent `SubagentStop` payload (the `agent_type == ""` case). The guard rests on the documentation.
- A `pending` background task and a maintenance-type task in a `Stop` payload. The filter change rests on the binary.
- `rate_limits.spend_limit` and `remote` in a real status line payload. Both segments were tested on synthetic input only.

**Measured only in part**
- `async` SessionEnd hooks surviving past session end: measured for a headless `claude -p` exit on Windows, 4 seconds of work. Closing an interactive session, longer work and the real audio player were not tested.
- The hooks `args` exec form on Windows: works on 2.1.288 with an absolute path to `python.exe`. A bare `python` resolved through `PATH`, which is what echook would use, was not tested, and upstream issue #90495 is still open.
- `terminalSequence` being inert for async hooks: measured on 2.1.251, read from the binary at 2.1.288, not re-measured.
- The native `uninstall`: exercised end to end in a contained fake home on Windows 11. Not run against a real Linux or macOS install, a non-UTF-8 locale, or a real (as opposed to fabricated) plugin install; write-failure rollback is unit-tested only.
- Mods: `$.audio.play` playing nothing was observed in a headless run on Windows. Interactive sessions and the Desktop app were not tested.
- The skill eval suite: two single runs on the smallest model. One case (`pomodoro-out-of-scope`) passed once and failed once on the same skill.

**Unknown**
- Whether the classic status line renders in the Claude Desktop app. This decides whether a separate "band" mod for Desktop users would have any purpose.
- Whether `model_refusal_fallback` ever fires as a `Notification`. It is declared in the binary; no emitter was found, so it is not registered.
- What the plugin directory does with a new commit to `plugins/audio-hooks/` while the first version is still with a reviewer. (A documentation-only push outside that folder was observed once not to create a new version; see `docs/DIRECTORY_LISTING.md`.)

## Known limits that are accepted for now

- `audio-hooks status` shows a corrupt preferences file as the defaults and `audio-hooks diagnose` does not flag it. The next hook event replaces the file.
- The preferences migration that keeps new variants silent for `hooks enable-only` users cannot recognise an enumeration made before an earlier variant addition it never answered; those users keep inheriting the parent.
- `audio-hooks uninstall` does not strip or report a registration that reaches an echook script through command substitution, a variable whose name contains neither `HOME` nor `USERPROFILE`, `~user/…`, or a relative path. It reports the other unrecognised spellings and removes them with `--remove-unmatched`.
- `logs tail` and `backup list|show` still ignore unknown flags. They are read-only.
- `uninstall --purge` is rejected for the script and plugin modes; it applies to `--cursor` and `--codex`.
- `bin/audio-hooks.py` is 228.6 KiB. The plugin directory holds any non-image file over 256 KiB for manual review.
- The plugin can be listed only for Claude Code: a top-level `bin/` directory rules out Cowork and the Claude apps.

## Looked at and deliberately not done

Do not reopen these without new evidence; each has a reason on record.

| Idea | Why not | Where it is recorded |
|---|---|---|
| Move either track onto Claude Code mods | No sound on a Windows terminal, toast is in-app only, cannot write to the terminal, cannot draw the status line, early-access API that already changed between builds | `docs/EVENT_BEHAVIOR_NOTES.md`, "Mods: investigated, not adopted" |
| Ship a plugin-level default for the per-subagent status row | Claude Code does not substitute `${CLAUDE_PLUGIN_ROOT}` in a plugin `settings.json`, the plugin's `bin/` is not on that command's `PATH`, and there is no CLI-only way to turn such a default off | Same file, "A plugin-level `subagentStatusLine` default" |
| Migrate `hooks.json` to the `args` exec form | A bare `python` is untested, the docs restrict exec form on Windows to a real executable, and the plugin cannot require a minimum Claude Code version | `AGENTS.md` gotchas; same file, "The `args` exec form" |
| Register `PreModelSwitch` or `WorktreeCreate` | Both are blocking / provider hooks | `AGENTS.md` gotchas |
| Register `model_refusal_fallback` | No emitter found in the binary | `AGENTS.md`, "The three variants added in v6.7.0" |
| Make the existing handlers synchronous so `terminalSequence` works | It would put a Python start-up on the end of every turn | `AGENTS.md` gotchas |
| Drop `bypass_permissions_disabled` from the SessionEnd matcher | Dead upstream since 2.1.234, but harmless to keep and needed by older builds | Changelog 6.6.0 / 6.7.0 |
| Restructure the plugin to clear the directory's policy holds | They describe what the plugin is; a hold is not a rejection | `docs/DIRECTORY_LISTING.md` |

## Candidates for a later release

None of these is committed to. They are what the investigation left on the table.

- **`PostModelSwitch`** as an opt-in event. It is observational and safe to register; its stdout reaches the model on exit 0, so the runner must print nothing there. Payload and registration steps are in `docs/EVENT_BEHAVIOR_NOTES.md`.
- **A second, minimal synchronous handler** that only emits `terminalSequence`, beside the async one — the recorded design for making the terminal bell and title work. Any synchronous handler must never exit with code 2 (the per-event effects are tabulated in `docs/EVENT_BEHAVIOR_NOTES.md`).
- **Notification triggers from `turn.complete` / `session.measure`** (turn duration, rate-limit movement) if mods leave early access. They would need a mod-to-Python hand-off.
- **Unused hook payload fields**: `prompt_id` (one id per user prompt, a candidate for de-duplication), `is_interrupt` on `PostToolUseFailure`, `session_title` and the resume fields on `SessionStart`.
- **Unused status line fields**: `model.id`, `context_window.remaining_percentage`, `pr.url`, `worktree.original_branch`.
- **Splitting `bin/audio-hooks.py`** before it reaches the directory's 256 KiB hold.
- **Tightening the skill's scope paragraph** so the model does not offer a `/loop` workaround after declining an out-of-scope feature (the flaky eval case).
- **A `termsOfServiceUrl`** for the directory listing, which is the one listing link not set.

## Upstream items being watched

| Item | State on 2026-10-04 | Why it matters |
|---|---|---|
| [anthropics/claude-code#90997](https://github.com/anthropics/claude-code/issues/90997) — `terminalSequence` dropped for async hooks | Open, no maintainer reply | If fixed, the `terminal_sequence` feature starts working without any echook change |
| [anthropics/claude-code#90495](https://github.com/anthropics/claude-code/issues/90495) — hooks `args` dropped on Windows | Open, no maintainer reply; did not reproduce on 2.1.288 | Part of the reason `hooks.json` stays in shell form |
| Claude Code mods | Early access since 2.1.287 | Re-evaluate if the API is declared stable or gains an OS-level notification or a Windows audio player |

Re-check with `gh issue view <n> --repo anthropics/claude-code --json state,updatedAt`.

## The next upstream sync

echook is synced against Claude Code **2.1.288**. When a newer version is installed, follow "How to re-sync against a new Claude Code release" in `docs/EVENT_BEHAVIOR_NOTES.md`. The range to read starts at 2.1.289.
