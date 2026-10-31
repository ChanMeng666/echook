# Project status

**Snapshot taken 2026-10-31, at v7.2.3.** v7.2.3 is the last release that changed the plugin folder. This page says where the project stands: what shipped recently, what is waiting on someone, what is claimed but not verified, and what was looked at and deliberately left alone. Everything here can go stale; each section says how to re-check it. When you change something listed here, update this page in the same commit.

For how the code works, read `AGENTS.md`. For measured upstream behaviour, `docs/EVENT_BEHAVIOR_NOTES.md`. For the release mechanics, `docs/RELEASING.md`. For the directory listing, `docs/DIRECTORY_LISTING.md`.

## What shipped in this cycle

Per-version detail is in `CHANGELOG.md`; counts come from `audio-hooks manifest`.

The latest piece is 7.2.0 (2026-10-30, PR #33, `3c573f7`): the status line follows the agent in view, through the first hooks module echook has shipped, `hooks/view-sensor.ts`. The module exists because Claude Code gives a status line script no way to know the view. The same work moved slow steps out of a status line run, because a shorter refresh interval could otherwise have frozen the line. `docs/STATUS_LINE.md` has the design and `docs/LIVE_TESTING.md` the method.

The cycle began with an incident, and most of 6.6.0 follows from it. On 2026-10-03 an AI agent probing for usage ran `audio-hooks install --help` on a machine that already had the plugin. The command ignored the flag, ran the legacy script installer and registered every hook a second time in `~/.claude/settings.json`. Following that thread found that `upgrade --help` ran a real upgrade, that several `set`-style commands wrote config when probed, that `uninstall` did nothing on Windows while reporting success, that the uninstall script could delete a user's own hooks, and that the test suite wrote to the real plugin data directory. All of those are fixed; the reasoning is in the 6.6.0 changelog entry and in `docs/INVARIANTS.md`.

## Waiting on someone

| Item | Who | How to check |
|---|---|---|
| Plugin directory review. Submitted 2026-10-04; security scan passed; held for content policy review. | Anthropic reviewer, then the maintainer selects **Publish** | The plugin page in the developer portal (`docs/DIRECTORY_LISTING.md`) |
| GitGuardian incident 37836366, a false positive on a sound-file mapping in `hooks/hook_runner.py` (see `docs/RELEASING.md`). It stays open until resolved in the dashboard. | Maintainer | GitGuardian dashboard |
| A reviewer's decision on `hooks/view-sensor.ts`. Read in the portal on 2026-10-31 for 7.2.0 and 7.2.1: the scans passed and the module was not refused, but it adds holds, listed in `docs/DIRECTORY_LISTING.md`. Every version was already waiting for a reviewer. If it is refused, removing the `modules` line from `hooks.json` restores the old behaviour and nothing else depends on it. | Anthropic reviewer | The plugin page in the developer portal |
| The six sound files added in 6.7.0 have not been listened to by a person. They are valid, distinct and of normal length. | Maintainer | Play `audio/default/{notif-auth-storage,fail-cloud-credential,fail-verification}.mp3` and `audio/custom/chime-*` counterparts |

## Claimed but not verified

These are stated in the changelog's "Not verified" sections; they are collected here so that nobody builds on them as if they were measured.

**Never provoked in a live session**
- The three matcher values registered in 6.7.0 (`cloud_credential_error`, `verification_required`, `auth_storage_failure`). They are present in the 2.1.288 binary; no real event was captured.
- An internal-agent `SubagentStop` payload (the `agent_type == ""` case). The guard rests on the documentation.
- A `pending` background task and a maintenance-type task in a `Stop` payload. The filter change rests on the binary.
- `rate_limits.spend_limit` and `remote` in a real status line payload. Both segments were tested on synthetic input only.

**Measured only in part**
- `async` SessionEnd hooks surviving past session end: measured for a headless `claude -p` exit, and on 2.1.316 for an interactive close (6 of 6, Windows, 4 seconds of work). Longer work, "Move to background and exit" and the real audio player were not tested.
- The hooks `args` exec form on Windows: works on 2.1.288 with an absolute path to `python.exe`. A bare `python` resolved through `PATH`, which is what echook would use, was not tested, and upstream issue #90495 is still open.
- `terminalSequence` being inert for async hooks: measured on 2.1.251, read from the binary at 2.1.288, not re-measured.
- The native `uninstall`: exercised end to end in a contained fake home on Windows 11. Not run against a real Linux or macOS install, a non-UTF-8 locale, or a real (as opposed to fabricated) plugin install; write-failure rollback is unit-tested only.
- Mods: `$.audio.play` playing nothing was observed in a headless run on Windows. Interactive sessions and the Desktop app were not tested.
- Cursor's `stop` `status` (6.8.0): `filters.stop.skip_if_aborted` and `filters.stop.error_as_stop_failure` rest on Cursor's documentation (`cursor.com/docs/hooks.md`, read 2026-10-07). No live Cursor payload was captured, and whether a `Stop` bridged from the Claude Code plugin carries `status` is unknown.
- Codex hooks (7.1.0): run live for the first time on 2026-10-15, on Codex 0.143.0 and 0.162.1, Windows, headless `codex exec` against a local stand-in for the model API. That run found echook silent under Codex on both versions and both install routes, and the fix was measured on all four. `PermissionRequest`, compaction and the subagent events were not provoked; an interactive session was not run. Detail in `docs/EVENT_BEHAVIOR_NOTES.md`.
- The Codex status-line item list (6.8.0): read from the Rust source at `rust-v0.160.1`; Codex itself was not run, and the releases that introduced `hostname` and `thread-name` were bracketed by sampling tags, not pinned.
- The skill eval suite: two single runs on the smallest model. One case (`pomodoro-out-of-scope`) passed once and failed once on the same skill.

- The status line following the viewed agent (7.2.0): measured on Claude Code 2.1.314, Windows, interactive through a pseudo-terminal, one background subagent, with `--plugin-dir`, through a directory-source marketplace, and from a cached 7.2.0 installed from GitHub; a teammate's view too. Re-measured on 2.1.316 with `--plugin-dir`, 12 of 12 transitions. A workflow's agents have no view to follow. Not run: a live session on macOS or Linux. The Cursor auto-bridge ignores the `modules` key by its source, read but not run.
- A viewed agent's context window (7.2.3): the windows were measured on 2.1.316 on one account. `sonnet-4-0` and `sonnet-4-5` rest on the binary. The estimate reads a private catalog cache that can be absent, stale or wrong for a third-party provider.
- `--resume` loading the installed plugin version: measured on 2.1.316, Windows, a mock model API, user scope. The `/resume` picker and a session open across an update ran once each.
- Echook's hooks on Windows without Git Bash: the documentation's rule that a failing shell-form `PreToolUse` or `PermissionRequest` command blocks the tool should not reach async hooks, but it does not say so and this was not run.

**Unknown**
- Whether the classic status line renders in the Claude Desktop app. This decides whether a separate "band" mod for Desktop users would have any purpose.
- Whether `model_refusal_fallback` ever fires as a `Notification`. It is declared in the binary; no emitter was found, so it is not registered.
- What a reviewer does with the versions that piled up while the first one was in review. Observed on 2026-10-21: **every** push to `master` became a new version, each scanned and then "Held by directory policy", 14 by then, from v6.7.2 at `f692417` to v7.1.0 at `1979632`, including two pushes that changed nothing in `plugins/audio-hooks/` (`c5e1b66`, `a148c71`). Whether the reviewer reviews the newest, the first, or each one is not known; see `docs/DIRECTORY_LISTING.md`.

## Known limits that are accepted for now

- The preferences migration that keeps new variants silent for `hooks enable-only` users cannot recognise an enumeration made before an earlier variant addition it never answered; those users keep inheriting the parent.
- `audio-hooks uninstall` does not strip or report a registration that reaches an echook script through command substitution, a variable whose name contains neither `HOME` nor `USERPROFILE`, `~user/…`, or a relative path. It reports the other unrecognised spellings and removes them with `--remove-unmatched`.
- Most read-only commands (`status`, `diagnose`, `get`, `version`, `update`, `manifest`, `hooks list`, `theme list`, `statusline show|segments`) still ignore unknown flags. They are read-only. `logs tail` and `backup list|show` reject them.
- `uninstall --purge` is rejected for the script and plugin modes; it applies to `--cursor` and `--codex`.
- The plugin can be listed only for Claude Code: a top-level `bin/` directory rules out Cowork and the Claude apps.

## Looked at and deliberately not done

Do not reopen these without new evidence; each has a reason on record.

| Idea | Why not | Where it is recorded |
|---|---|---|
| Move either track onto Claude Code mods (a view sensor for the status line is the one exception, `docs/INVARIANTS.md`) | No sound on a Windows terminal, `$.ui.notify` (2.1.295) goes through the user's notification channel with no per-event sound, cannot write to the terminal, cannot draw the status line | `docs/EVENT_BEHAVIOR_NOTES.md`, "Mods: investigated, not adopted" and "Re-sync to 2.1.316" |
| Ship a plugin-level default for the per-subagent status row | Claude Code does not substitute `${CLAUDE_PLUGIN_ROOT}` in a plugin `settings.json`, the plugin's `bin/` is not on that command's `PATH`, and there is no CLI-only way to turn such a default off | Same file, "A plugin-level `subagentStatusLine` default" |
| Migrate `hooks.json` to the `args` exec form | A bare `python` is untested, the docs restrict exec form on Windows to a real executable, and the plugin cannot require a minimum Claude Code version | `docs/INVARIANTS.md`; `docs/EVENT_BEHAVIOR_NOTES.md`, "The `args` exec form" |
| Register `PreModelSwitch` or `WorktreeCreate` | Both are blocking / provider hooks | `docs/INVARIANTS.md` |
| Register `model_refusal_fallback` | No emitter found in the binary | `docs/INVARIANTS.md`, "The three variants added in v6.7.0" |
| Add a variant for `plugin_notification` (decided 2026-10-31) | It fires only when another plugin's mod calls `$.ui.notify`, and that call already reaches the person through their own notification channel. Two new sounds and a release are not worth it for that. Reopen if a widely used mod relies on it, or if someone asks; it needs a sound in both themes, so an ElevenLabs key | `docs/EVENT_BEHAVIOR_NOTES.md`, "Re-sync to 2.1.316" |
| Make the existing handlers synchronous so `terminalSequence` works | It would put a Python start-up on the end of every turn | `docs/INVARIANTS.md` |
| Keep the script installer (`install --scripts`, `scripts/install-complete.sh`, `scripts/install-windows.ps1`, `scripts/uninstall.sh`, `hooks/shared/path_utils.sh`) | Removed in 7.0.0: it double-registered every hook on 2026-10-03, was about 2400 lines of shell and PowerShell with almost no CI coverage, and no supported install path needs it. `install --scripts` now returns `SCRIPT_INSTALL_REMOVED`. Kept for machines that still have a script install: `audio-hooks uninstall` and the runner's `.project_path` / self-update support. The tracked `config/user_preferences.json` stays one more release as the sentinel an old installed runner tests for on the first hook after `git pull`; delete it one release after 7.0.0 | `docs/INVARIANTS.md`, "The script installer is gone (7.0.0)" |
| Drop `bypass_permissions_disabled` from the SessionEnd matcher | Dead upstream since 2.1.234, but harmless to keep and needed by older builds | Changelog 6.6.0 / 6.7.0 |
| Register Codex's `Interrupt` hook event (seen in the Codex hooks documentation, 2026-10-07) | It fires when the user interrupts a turn: the person is at the keyboard and caused it, so a notification tells them nothing, and a new event needs its own sound in both themes | This table |
| Register Cursor's `preToolUse` | It is a gating hook (it can deny a tool call), the same trap as `PreModelSwitch`. `postToolUse` is safe but overlaps the shell, MCP and file events already registered; left out as low value | This table; Changelog 6.8.0 |
| Step back on any platform because of its native notifications (surveyed 2026-10-07) | None covers echook's ground. Claude Code: desktop notification only in Ghostty, Kitty and iTerm2, a bell elsewhere, and account-bound mobile push with two toggles. Codex: a `notify` program for turn completion and OSC 9 / BEL. Cursor: one completion chime in the IDE and a terminal notification in the CLI. No per-event sounds, speech, webhooks, filters or snooze anywhere. The one real overlap, Cursor's chime, is described in `docs/TROUBLESHOOTING.md` | "Native features surveyed" below |
| Restructure the plugin to clear the directory's policy holds | They describe what the plugin is; a hold is not a rejection | `docs/DIRECTORY_LISTING.md` |

## Candidates for a later release

None of these is committed to. They are what the investigation left on the table.

- **`PostModelSwitch`** as an opt-in event. It is observational and safe to register; its stdout reaches the model on exit 0, so the runner must print nothing there. Payload and registration steps are in `docs/EVENT_BEHAVIOR_NOTES.md`.
- **A second, minimal synchronous handler** that only emits `terminalSequence`, beside the async one — the recorded design for making the terminal bell and title work. Any synchronous handler must never exit with code 2 (the per-event effects are tabulated in `docs/EVENT_BEHAVIOR_NOTES.md`).
- **Notification triggers from `turn.complete` / `session.measure`** (turn duration, rate-limit movement). Mods have left early access, so this is open; it would need a mod-to-Python hand-off.
- **Unused hook payload fields**: `prompt_id` (one id per user prompt, a candidate for de-duplication), `is_interrupt` on `PostToolUseFailure`, `session_title` and the resume fields on `SessionStart`.
- **Unused status line fields**: `context_window.remaining_percentage`, `pr.url`, `worktree.original_branch`. `model.id` is now read, for the viewed agent's context window.
- **An exact context window for a viewed agent.** `subagentStatusLine` is the one place that receives `contextWindowSize`. Its script could leave the value where the main script can read it, removing the `~` estimate for people who install that row. Not done because the row is optional and not installed by default; 7.2.3 improved the estimate instead.
- **A `diagnose` finding for a directory-source install**, saying that the reported install path is not what runs. It would have saved the 2026-10-30 trace.
- **Replacing the sensor if upstream ships #96895** (see "Upstream items being watched"). A `viewed_agent` field and a repaint on switch would remove the module, the sidecar and the need for a short `refreshInterval`.
- **A status line for the Cursor CLI.** Its `statusLine` entry in `~/.cursor/cli-config.json` (`{"type": "command", "command": …}`, JSON on stdin, since about CLI v2026.06.22) has the same shape as Claude Code's, and echook has no Cursor status line. Not started because the entry is absent from Cursor's configuration reference, the payload is unpublished and reported to be thin, a custom command replaces the native footer, and the Cursor CLI is not installed on the maintainer's machine. The first step is to capture a real payload.
- **A native Cursor plugin** (`.cursor-plugin/plugin.json`, which can carry hooks, with a reviewed marketplace) in place of `install --cursor` writing `~/.cursor/hooks.json`. Unknown: the variable a plugin hook command uses for its root, where plugin data lives, and how it interacts with the Claude Code bridge (both present would double-fire again).
- **Making the skill fire for a status-line request.** On 2026-10-13 the eval case `statusline-context-only` scored 0 of 3 with Haiku because the skill was never invoked, identically on v6.8.0 and on the shortened 7.0.0 skill, so it is a triggering problem in the description, not in the body. The other six cases passed (one run each).
- **Deleting the tracked `config/user_preferences.json`** one release after 7.0.0. It is only the sentinel an already-installed script install's old runner looks for on the first hook after `git pull`.
- **Tightening the skill's scope paragraph** so the model does not offer a `/loop` workaround after declining an out-of-scope feature (the flaky eval case).
- **A `termsOfServiceUrl`** for the directory listing, which is the one listing link not set.

## Upstream items being watched

| Item | State on 2026-10-04 | Why it matters |
|---|---|---|
| [anthropics/claude-code#90997](https://github.com/anthropics/claude-code/issues/90997) — `terminalSequence` dropped for async hooks | Open, no maintainer reply | If fixed, the `terminal_sequence` feature starts working without any echook change |
| [anthropics/claude-code#90495](https://github.com/anthropics/claude-code/issues/90495) — hooks `args` dropped on Windows | Open, no maintainer reply (re-checked 2026-10-07); did not reproduce on 2.1.288 | Part of the reason `hooks.json` stays in shell form |
| [anthropics/claude-code#96895](https://github.com/anthropics/claude-code/issues/96895) — expose the viewed agent to the status line and repaint on a switch | Open on 2026-10-31, opened at 2.1.282, one comment, no maintainer reply; nothing shipped in 2.1.316 | The exact gap `hooks/view-sensor.ts` works around. If it ships, drop the module and go back to a slower refresh |
| Claude Code mods | Past early access on 2026-10-31; `$.ui.notify` added in 2.1.295 and assessed, decision unchanged | Re-evaluate if it gains a Windows audio player |

Re-check with `gh issue view <n> --repo anthropics/claude-code --json state,updatedAt`.

## Native features surveyed (2026-10-07)

A survey of what the three editors now do by themselves, made to answer whether echook is still needed. Sources were official documentation, changelogs and forum posts, mostly read through a summarising fetch tool, so treat exact names as leads; what echook changed in 6.8.0 was re-read from raw sources first.

| Editor | Native notification | Native status line | What echook did about it |
|---|---|---|---|
| Claude Code | `preferredNotifChannel`: desktop notification in Ghostty, Kitty and iTerm2, terminal bell elsewhere; mobile push through Remote Control; no sound files | A `statusLine` command and `/statusline` to generate a script; no rich built-in default | Nothing new; `diagnose` already reports `NATIVE_NOTIFICATIONS_ACTIVE` |
| Codex | `notify` program on `agent-turn-complete`; `tui.notifications` over OSC 9 / BEL, focus-aware | A fixed item list (`tui.status_line`); a command-driven one is still an open request (openai/codex#17827) | Item list refreshed to 0.160.1 in 6.8.0 |
| Cursor | IDE: one completion chime (custom file allowed) and system notifications. CLI: terminal notification, tab-title indicators | CLI only: an undocumented command-driven `statusLine` | `stop` `status` read through two opt-in keys in 6.8.0; the chime overlap documented; CLI status line listed as a candidate |

Two findings of that survey were wrong and are recorded so they are not repeated: Codex hooks being on by default (`features.hooks`, with `codex_hooks` as a legacy alias) was already handled by `install --codex`, and a Codex status-line item called `daybreak` does not exist in either item enum at `rust-v0.160.1`.

Not surveyed: the Claude Desktop app's notifications, and Codex's desktop app, IDE extension and cloud notifications.

## The next upstream sync

echook is synced against Claude Code **2.1.316** (2026-10-31; full sync of 2.1.293–2.1.316, recorded in "Re-sync to 2.1.316" in `docs/EVENT_BEHAVIOR_NOTES.md`). When a newer version is installed, follow "How to re-sync against a new Claude Code release" in the same file. The range to read starts at 2.1.317. The evidence is `D:\.claude-scratch\2026-10-31\echook-2.1.316-evidence.zip` on the maintainer's machine. `claude update` follows a pointer that can lag npm, so check `claude --version` against the range. One lead from the 2.1.292 sync: `claude plugin install <name> --marketplace <source>` (new in 2.1.292, read from `--help`, not run) might replace the two-command Claude Code install; try it in a throwaway `CLAUDE_CONFIG_DIR` first.
