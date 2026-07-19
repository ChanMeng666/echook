# Observed event behaviour

What Claude Code's hook events **actually do**, measured against a running install — as distinct from what [code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks) documents. Everything here was captured from real sessions, not inferred.

This file exists because of v6.3.4. That release was an emergency rollback: echook registered a sound on `WorktreeCreate`, but `WorktreeCreate` is a *provider* hook — registering any command hook on it makes Claude Code delegate worktree creation to that hook and demand a path back. The audio hook returned exit 0 with no path, so every worktree-isolated subagent failed. The event's name said "notify me when a worktree is created"; its contract said "you are now responsible for creating worktrees".

**The lesson, and the rule for this project: verify an event's real semantics before shipping a hook on it.** Record what you observed here, and cite it in the CHANGELOG.

---

## How to capture

Register a shim against the events you care about in `~/.claude/settings.json` (hot-reloads, no restart needed), pointing at a script that appends stdin to a file and exits 0. Keep the shim outside the repo.

For matcher-scoped events, register **a catch-all (`"matcher": ""`) alongside the named matchers**. This is what makes a negative result interpretable: the catch-all sees every value of that matcher field, so if a type never appears there, the type genuinely never occurred — as opposed to the matcher string being unrecognised by this Claude Code version. Without the catch-all, "no sound" has two indistinguishable explanations.

Exercise the paths deliberately — long turns, `Task` subagents, background shells, plan-mode approvals, going idle, ending the session — and correlate by `session_id` and timestamp. Then remove the shim and the `hooks` block.

`CLAUDE_HOOKS_DEBUG=1` also makes echook dump the last status-line stdin, but note that echook's own `hook_start` NDJSON event does **not** record raw stdin, so it cannot substitute for a shim when you need payload fields.

---

## Findings

### `Stop` carries an undocumented `background_tasks` array

Not in the upstream field list. Observed on Claude Code 2.1.215:

```json
{
  "hook_event_name": "Stop",
  "stop_hook_active": false,
  "session_crons": [],
  "background_tasks": [
    {"id": "<opaque-id>", "type": "teammate", "status": "running", "description": "<agent task description>"},
    {"id": "<opaque-id>", "type": "shell",    "status": "running", "description": "<shell command description>"}
  ]
}
```

(Field shapes reproduced from a real capture; ids and descriptions replaced.)

`type` observed as `teammate` and `shell`; `status` observed as `running`. `session_crons` appears alongside it and was empty throughout.

**Why it matters.** `Stop` fires at the end of every turn and nothing in the payload marks a turn as final — but `background_tasks` does tell you whether work is still in flight. That is the closest available proxy for "the batch is finished". echook exposes it as `filters.stop.skip_if_background_tasks_running`. Across 12 real captured `Stop` payloads from a session driving 10–15 teammates, 11 had running tasks (suppressed) and 1 did not (played).

Treat the field as best-effort: it is undocumented, so it may change shape or disappear. The filter reads it defensively and no-ops when it is absent, which is also what happens under Cursor and Codex.

### `agent_completed` and `agent_needs_input` do not fire for local subagents

`Notification` documents eight `notification_type` values. Two of them — `agent_needs_input` and `agent_completed`, both added in Claude Code v2.1.198 — did not fire at all during capture.

| Captured over several concurrent sessions, Claude Code 2.1.215 | Count |
|---|--:|
| `Stop` | 17 |
| `SubagentStop` | 14 |
| `Notification` / `idle_prompt` | 6 |
| `SessionEnd` | 1 |
| `Notification` / `agent_completed` | **0** |
| `Notification` / `agent_needs_input` | **0** |

Captured via a catch-all matcher, with `inputNeededNotifEnabled` and `agentPushNotifEnabled` both `true` in settings.

**Fourteen subagent completions producing zero `agent_completed` establishes that it is not a `Task`-tool subagent signal.** The naming of the two settings that gate it (`agentPushNotifEnabled`) suggests it belongs to the push-notification path for background or remote agents. Unconfirmed.

**Consequence for echook:** both are registered for completeness and forward compatibility, both ship `default: false`, and neither is presented to users as a working "task finished" cue. If you are asked for that cue, recommend `notification` / `idle_prompt` or the `background_tasks` filter instead.

### `idle_prompt` is the real "waiting for you" signal

Fires with `message: "Claude is waiting for your input"` when a session is genuinely parked on the user — not on every turn boundary. Payload keys observed: `session_id`, `transcript_path`, `cwd`, `prompt_id`, `hook_event_name`, `notification_type`, `message`.

This, not `Stop`, is what users mean when they ask for a "task done" sound.

### `Stop` is per-turn and has no finality marker

Confirmed by both the upstream docs ("fires at the end of each turn… not when the session ends") and by capture: 17 `Stop` events across normal working turns. `stop_hook_active` was `false` throughout and denotes re-entrancy, not finality. Use `SessionEnd` for genuine session termination.

### `settings.json` hot-reloads hook registrations

Adding a `hooks` block took effect on the next event with no restart. Useful for capture work; also means a session can pick up registration changes mid-flight.

### Sibling config directories can share `settings.json`

Not a Claude Code behaviour as such, but it bit this investigation: a multi-account setup using `CLAUDE_CONFIG_DIR` may symlink `settings.json` between config dirs, so hook registrations are shared while plugin *data* (`user_preferences.json`) stays per-directory. Check with `realpath` before assuming two accounts are independent — a capture registered for one account will observe both.

---

## Matcher coverage as of v6.4.0

| Event | Matchers registered | Notes |
|---|---|---|
| `Notification` | all 8 documented types | 4 added in v6.4; `agent_*` pair unverified (above) |
| `SessionEnd` | `clear`, `resume`, `logout`, `prompt_input_exit`, `bypass_permissions_disabled\|other` | first four were dead code until v6.4 — defined in `SYNTHETIC_EVENT_MAP` but the event was registered with no matcher, so nothing invoked them |
| `SessionStart` | `startup`, `resume`, `clear`, `compact` | |
| `StopFailure` | `rate_limit`, `authentication_failed`, and the remaining five collapsed onto one handler | the five collapsed types stay in `SYNTHETIC_EVENT_MAP` and are allowlisted in the contract test |
| `PreCompact` / `PostCompact` / `Setup` | both/both/both | |
| `PermissionRequest` | `""` (catch-all) | |

`Notification` and `PermissionRequest` have no Cursor or Codex equivalent; the runner hard-skips them for those invokers regardless of registration.
