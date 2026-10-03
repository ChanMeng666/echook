# Observed event behaviour

What Claude Code's hook events **actually do**, measured against a running install — as distinct from what [code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks) documents. Findings up to and including the 2.1.251 sync were captured from real sessions or read out of the binary. From the 2.1.288 sync onward every fact carries an evidence tag (see [Evidence tags](#evidence-tags-used-from-the-21288-sync-onward)) so a reader can tell a measurement from a reading of the docs or of minified code.

This file exists because of v6.3.4. That release was an emergency rollback: echook registered a sound on `WorktreeCreate`, but `WorktreeCreate` is a *provider* hook — registering any command hook on it makes Claude Code delegate worktree creation to that hook and demand a path back. The audio hook returned exit 0 with no path, so every worktree-isolated subagent failed. The event's name said "notify me when a worktree is created"; its contract said "you are now responsible for creating worktrees".

**The lesson, and the rule for this project: verify an event's real semantics before shipping a hook on it.** Record what you observed here, and cite it in the CHANGELOG.

---

## How to capture

Register a shim against the events you care about in `~/.claude/settings.json` (hot-reloads, no restart needed), pointing at a script that appends stdin to a file and exits 0. Keep the shim outside the repo.

For matcher-scoped events, register **a catch-all (`"matcher": ""`) alongside the named matchers**. This is what makes a negative result interpretable: the catch-all sees every value of that matcher field, so if a type never appears there, the type genuinely never occurred — as opposed to the matcher string being unrecognised by this Claude Code version. Without the catch-all, "no sound" has two indistinguishable explanations.

Exercise the paths deliberately — long turns, `Task` subagents, background shells, plan-mode approvals, going idle, ending the session — and correlate by `session_id` and timestamp. Then remove the shim and the `hooks` block.

`CLAUDE_HOOKS_DEBUG=1` also makes echook dump the last status-line stdin, but note that echook's own `hook_start` NDJSON event does **not** record raw stdin, so it cannot substitute for a shim when you need payload fields.

A less intrusive way to load a probe, used throughout the 2.1.288 sync: put the probe in a throwaway plugin directory outside the repo and run `claude -p "<a trivial prompt>" --plugin-dir <probe> --no-session-persistence --model haiku --settings '{"enabledPlugins":{"audio-hooks@chanmeng-audio-hooks":false}}'`. Nothing in `~/.claude/settings.json` is touched and echook stays silent for the run. It leaves an empty `~/.claude/plugins/data/<probe>-inline` directory behind; remove it afterwards. A headless run cannot show anything that belongs to the interactive terminal UI (the per-subagent status row, toasts, bands) — those need an interactive session.

---

## How to re-sync against a new Claude Code release

This is the procedure that produced the 2.1.288 findings below. Four sources, in this order, because each one is cheaper than the next and each catches what the previous one cannot.

1. **The changelog, for the whole range.** `https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md`, from the version echook last synced at to the newest. Read all of it; then grep it for the surfaces that matter (`hook`, the event names, `matcher`, `async`, `terminalSequence`, `statusline`, `rate_limits`, `plugin`, `reload-plugins`). It is the only source that says *when* something changed, and it omits most payload-field changes.
2. **The current documentation, as raw Markdown.** Append `.md` to a `code.claude.com/docs/en/…` URL and fetch it with `curl`: `hooks`, `hooks-guide`, `statusline`, `plugins-reference`, `plugins/manifest-reference`, `plugins/loading`, `plugins/cli-reference`. **Do not rely on a tool that summarises the page**: during the 2.1.288 sync a summary reported `session_crons` entry fields that do not exist and misquoted the `SubagentStop` passage. Diff the event list, each event's matcher values, the documented payload fields and the status line's stdin schema against `audio-hooks manifest`, `plugins/audio-hooks/hooks/hooks.json` and the maps in `hooks/hook_runner.py`.
3. **The installed binary, for what the documentation omits.** `claude.exe` (or the file under `~/.local/share/claude/versions/`) embeds its JavaScript as plain text. Scan the file in chunks for a printable run containing a known anchor, carve that region out to a scratch file, and search it with a small regex script. Minified function names change with every build, so anchor on **literal strings**: the event array (`"PreToolUse","PostToolUse",…`), `notification_type`, `background_tasks:`, `session_crons`, `prompt_cache`, `Exit code 2`, an event's description text, an error message. Record the file offset of each finding so it can be re-checked. Two kinds of result come out of this and must be kept apart: a literal that was read (**[BIN]**) and control flow inferred from minified code (**[INFER]**). Minified control flow is easy to misread, so an **[INFER]** finding gets a live check before echook relies on it.
4. **A live experiment, for anything echook will rely on.** A blocking event, a payload field a filter depends on, a timing budget, a Windows-specific spawn path. Use the probe-plugin method above, run it at least three times, and write down what the run does *not* establish (a headless exit is not an interactive close; an absolute path to `python.exe` is not a bare `python`).

Then record each finding below with its evidence tag and the version it was observed on, state its limits, and change echook only for what is established. Three things to check every time, because each has bitten this project:

- **Is a new event blocking?** Look for `permissionDecision`, `decision`, a "blocked by a … hook" error string, or a result the caller waits for. A blocking or provider event must never be registered by an async notification plugin (`WorktreeCreate`, `PreModelSwitch`).
- **Are there matcher values with no variant?** `hooks.json` has no catch-all under `Notification` or `StopFailure`, so an unregistered value is a permanently silent event, and a failure that Claude Code reclassifies under a new value goes quiet without anything in echook changing.
- **Did the status line's stdin gain fields?** Compare the builder in the binary with the fields the status line script reads.

Keep the carved extract, the offset notes, the fetched documentation and the raw experiment logs somewhere outside the repository until the findings are no longer in dispute: the binary is replaced when Claude Code updates and the documentation pages change, so none of it can be regenerated later.

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

*Update, 2.1.288:* the field has since been documented, and the capture above under-described it. `status` can also be `pending`, `type` is a friendly label drawn from a larger set, and three of those labels are Claude Code's own maintenance work. v6.6.0 changed the filter accordingly — see [The `Stop` / `SubagentStop` payload at 2.1.288](#the-stop--subagentstop-payload-at-21288).

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

*Update, 2.1.288:* upstream has since fixed it firing while background agents are still running, which strengthens it as this signal. It is documented to arrive about 60 seconds after Claude finishes and only if the user has not typed since, so it is a "parked" signal, not an instant one. None of this was re-measured here — see [`idle_prompt` and the notification timing gates](#idle_prompt-and-the-notification-timing-gates).

### `Stop` is per-turn and has no finality marker

Confirmed by both the upstream docs ("fires at the end of each turn… not when the session ends") and by capture: 17 `Stop` events across normal working turns. `stop_hook_active` was `false` throughout and denotes re-entrancy, not finality. Use `SessionEnd` for genuine session termination.

### `settings.json` hot-reloads hook registrations

Adding a `hooks` block took effect on the next event with no restart. Useful for capture work; also means a session can pick up registration changes mid-flight.

### Sibling config directories can share `settings.json`

Not a Claude Code behaviour as such, but it bit this investigation: a multi-account setup using `CLAUDE_CONFIG_DIR` may symlink `settings.json` between config dirs, so hook registrations are shared while plugin *data* (`user_preferences.json`) stays per-directory. Check with `realpath` before assuming two accounts are independent — a capture registered for one account will observe both.

### `SessionStart` gained a `fork` source, and echook was silent for it

Claude Code 2.1.213: *"Changed SessionStart hooks to report source `"fork"` when a session begins as a fork instead of `"resume"`."*

Extracted from the 2.1.239 binary — the union is closed, not free-form:

```
SessionStart"),source:Or(["startup","resume","clear","compact","fork"])
```

echook registered the first four. Forked sessions used to land on `resume` and
made a sound; after 2.1.213 they report `fork`, matched nothing, and went
**completely silent**. Nothing failed and nothing logged — the registration was
simply never invoked. Fixed in v6.4.1.

This is the archetype for the drift this file exists to catch: the *event* name
never changed, so no contract test could have noticed. Only the set of values a
matcher can take moved underneath us.

### `StopFailure` has eleven error types; `other` was never one of them

Union from the same binary:

```
["authentication_failed","oauth_org_not_allowed","account_on_hold","billing_error",
 "rate_limit","overloaded","invalid_request","model_not_found","server_error",
 "unknown","max_output_tokens"]
```

Before v6.4.1 echook collapsed six of these onto a single handler registered as
`stop_failure_other`. Two consequences, both invisible from the outside:

- `other` is **not** a Claude Code value (zero occurrences in the binary), so
  the arg named a matcher that could never be emitted.
- Because `is_hook_enabled` received `stop_failure_other` as the variant,
  setting `enabled_hooks.stop_failure_billing_error` had **no effect at all** —
  while `hooks list --variants` and the manifest advertised all five collapsed
  types as independently switchable.

v6.4.1 registers one handler per real type and drops `other`. Claude Code's own
bucketing of these types is a useful guide for choosing sounds: auth
(`authentication_failed`, `oauth_org_not_allowed`, `account_on_hold`), billing
(`billing_error`), model-unavailable (`model_not_found`), and the rest
transient.

*Update, v6.7.0:* the binary's union has since grown to 13 values and echook registers all 13 (`cloud_credential_error` and `verification_required` added); see [Matcher values: three registered in v6.7.0](#matcher-values-three-registered-in-v670-one-deliberately-not).

### `"async": true` discards the hook's stdout — `terminalSequence` never fires

**Measured on Claude Code 2.1.251, Windows 11, 2026-09-01.** This is the
constraint that makes v6.5.0's `terminalSequence` inert: all 67 handlers in
`plugins/audio-hooks/hooks/hooks.json` are declared `"async": true`.

One shim script registered on `PreToolUse` matcher `Glob` in
`~/.claude/settings.json`, printing
`{"terminalSequence":"\u001b]2;PROBE-<phase>-<ms>\u0007"}` (OSC 2, set window
title) and logging its own invocations. A second process polled
`[Console]::Title` every 5 ms. Same script, same event, same payload — only the
`async` field differed between phases:

| phase | invocations | OSC reached the terminal |
|---|---|---|
| `"async": true` | 3 | 0 |
| sync (no `async`) | 2 | 2, within ~28 ms |

The hook ran in both phases (`stdin_bytes=568` — a real hook payload), which is
what rules out a registration failure rather than a stdout failure. Two
incidental confirmations: `settings.json` hook registration hot-reloads with no
restart, and Claude Code writes OSC 2 titles itself (the console title during
the run was `◐ <branch-name>`).

**Why, from the 2.1.251 binary.** `Ege()` is the only function that writes an
escape to the terminal, and its only caller is
`jie(e,t){if(!e||!_p(e)||!e.terminalSequence)return;…Ege(vge(e.terminalSequence))}`.
`jie` has four call sites, all on **synchronous** result paths (command-hook
stdout, HTTP, MCP, callback), e.g. `let{json:Ke,…}=I0e(We.stdout);…jie(Ke,O);`.
A config-async hook never reaches them — it returns before stdout is collected:

    if((e.async||e.asyncRewake&&bn)&&!B){…
      if(Kxt({…asyncResponse:{async:!0,asyncTimeout:nn}…}))
        return{stdout:"",stderr:"",output:"",status:0,backgrounded:!0}}

so `jie` is later handed `I0e("")` → no JSON → immediate return.

The backgrounded stdout is **not** discarded, it is delivered somewhere that
cannot reach the terminal. `Kxt`→`z2e` registers the process; `G2e` polls it and
on completion parses the first non-`async` JSON line via `j2e`; `DYn` wraps that
in an `async_hook_response` attachment. `terminalSequence` **is** in the
hook-output schema (`terminalSequence:i().optional().describe("A terminal escape
sequence …")`), so `j2e` validates and preserves it — and then the attachment
renderer reads only two fields:

    case"async_hook_response":{…"systemMessage"in d?…;
      …"hookSpecificOutput"…additionalContext…;return hs(u)}

The field is carried the whole way and dropped at the last step. That makes this
an upstream oversight, not a deliberate design: a single `jie(o,d)` inside
`DYn`'s map would make async `terminalSequence` work. Filed upstream as
[anthropics/claude-code#90997](https://github.com/anthropics/claude-code/issues/90997)
with the A/B above as the repro; watch it before investing in the workaround
below. (Neighbouring prior art: #58858 added `terminalSequence` to the reference
but says nothing about `async`; #24794, closed not-planned, is a different
defect in the same line-oriented async-stdout parser, which is still
line-oriented in 2.1.251.)

**What a backgrounded hook can still deliver:** `systemMessage`,
`hookSpecificOutput.additionalContext`, `metrics`, and — with `asyncRewake` —
exit code 2. **Not** `terminalSequence`, `decision`, `continue`, or
`permissionDecision`. The published docs' blanket *"doesn't read its stdout"* is
correct for terminal purposes and slightly overstated in general.

**What 6.5.1 does about it:** `audio-hooks diagnose` reports
`TERMINAL_SEQUENCE_INERT` when the flag is on and points at the desktop-toast
channel instead. The code is unchanged and the feature is not silently removed.

**Status at 2.1.288 (2026-10-03):** unchanged. [BIN] the writer is still called
only from four synchronous result paths and first checks that the handler is not
`async: true`; the async path returns before stdout is read. Upstream issue
#90997 was still open, with no maintainer reply, when checked on 2026-10-03.
This is a reading of the 2.1.288 binary, not a re-run of the A/B above. Claude
Code mods cannot emit it either (see [Mods](#mods-investigated-not-adopted)).

**Fix shape, if it is ever built.** The emitting handler must be synchronous.
[`claude-plugins-official#351`](https://github.com/anthropics/claude-plugins-official/issues/351)'s
Windows startup-hang argument does **not** cover these events — none of the 9
`TERMINAL_SEQUENCE_SAFE_EVENTS` is a startup event, and `SessionStart`/`Setup`
are in `TERMINAL_SEQUENCE_FORBIDDEN_EVENTS` either way. The honest cost is
per-turn latency: a Python spawn on `Stop` and `Notification` for every install,
serving a feature that is off by default. So do not flip the 38 existing
handlers on those 9 events (`Notification` 16, `StopFailure` 11, `SessionEnd` 5,
one each for `Stop`, `PermissionRequest`, `PermissionDenied`, `SubagentStop`,
`TaskCompleted`, `TeammateIdle`). Register instead a **second, sync, minimal
handler** beside each async audio handler, whose only job is to check
`terminal_sequence.enabled`, write the one JSON line, and exit — the audio path
keeps async and its Windows safety, and only the opt-in escape pays the sync
cost. Two constraints: gate it Claude-Code-only by invoker, since Cursor runs
all matching hooks from every source without de-duplicating; and make the
`SessionEnd` one the cheapest of the set, since `SessionEnd` hooks share a 1.5 s
budget.

### `PreModelSwitch` is a blocking gate, not a notification — do not register it

Claude Code 2.1.251 added `PreModelSwitch` and `PostModelSwitch`. The changelog
describes them as *"block, confirm, or annotate a model switch"*, and the first
verb is the whole story. Extracted from the 2.1.251 binary:

```
p({hookEventName:N("PreModelSwitch"),
   permissionDecision:ie(["allow","deny","ask"]).optional()
     .describe("Same contract as PreToolUse: allow proceeds (skipping the
      interactive cache-miss confirm), deny cancels the switch, ask asks the
      user to confirm (a headless session refuses instead)"),
   permissionDecisionReason:i().optional()})
```

and, from the same binary, the user-visible consequences of getting it wrong:

```
model switch blocked by a PreModelSwitch hook
Model switch blocked by a PreModelSwitch hook: confirmation required, and this session cannot ask
A PreModelSwitch hook asked you to confirm
PreModelSwitch hooks did not complete:
... did not respond before its timeout
Fast mode was not changed: the PreModelSwitch check failed
Fast mode was not enabled: the model changed while PreModelSwitch hooks ran; try again
plugin hooks could not be loaded, so PreModelSwitch hooks could not be checked
```

Claude Code **waits** for `PreModelSwitch` and treats a non-answer as a failure
mode worth six distinct error strings. That is the same shape as `WorktreeCreate`
in v6.3.4: an event whose name reads like a notification and whose contract makes
the hook responsible for an outcome. echook has nothing to contribute to a model
switch except a sound, and a sound is not worth sitting in the path of `/model`
and fast-mode promotion.

**`PreModelSwitch` will not be registered. `PostModelSwitch` is cleared for a
later release but is not registered in 6.5.1** — 6.5.1 is a fix release and adds
no capability, and a new event needs its own sound in both themes, which needs an
ElevenLabs regeneration run. Everything needed to add it is here.
`PostModelSwitch`'s only output field is `additionalContext`, it is not waited on
for a decision, and it carries the same payload:

```
from_model, to_model, requested_model (nullable), source, context_tokens
source union — PreModelSwitch:  ["command","picker","sdk"]
               PostModelSwitch: ["command","picker","sdk","auto","resume"]
```

When `PostModelSwitch` is added, `PreModelSwitch` must go into
`TERMINAL_SEQUENCE_FORBIDDEN_EVENTS` alongside `MessageDisplay` and the
`Elicitation` pair, for the same reason they are there: its stdout is read as an
answer on the user's behalf.

**Addendum, 2.1.288** [BIN]. `PreModelSwitch` is still blocking and gained one
more error string, returned as a block when the model changed underneath the hook:
`the session model changed while a PreModelSwitch hook was running; pick again`.
Both model-switch payloads gained four fields (schema near offset 205438424):
`prompt_cache_warm` (bool), `cache_ttl` (`"5m"` | `"1h"`),
`estimated_cache_write_usd` (number) and `pricing`
(`"configured"` | `"catalog"` | `"default"`). The per-event contract table says,
for `PostModelSwitch`: *"Exit code 0 - stdout shown to Claude on the next
request"* — so a runner registered there **must print nothing** on stdout.
Nothing is registered; this is the checklist for whoever adds it. [DOC] The hooks
reference additionally says *"A PreModelSwitch hook that doesn't respond before
its timeout blocks the switch"* and gives the event a 30-second default timeout.

### The `args` exec form: keep the shell form

The decision is unchanged since 2.1.251; the *reason* changed at 2.1.288 (see
the re-test below). 2.1.251's command-hook schema offers an exec form that would
remove echook's entire Windows quoting risk class in one move:

> `args` — *"Argument list for exec form. When present, `command` is resolved as
> an executable and spawned directly with these arguments — no shell. Path
> placeholders like `${CLAUDE_PLUGIN_ROOT}` are substituted per-element as plain
> strings, so paths with quotes, `$`, or backticks never reach a shell parser.
> When absent, `command` runs through a shell (bash on POSIX, PowerShell on
> Windows without Git Bash)."*

Do not adopt it yet. [anthropics/claude-code#90495](https://github.com/anthropics/claude-code/issues/90495)
(open, `platform:windows`) reports the exec form being dropped on Windows and
still routed through `bash.exe` with no argv, breaking all 48 of a reporter's
converted hooks. Windows is this project's primary development platform, so the
shell form stays. (At 2.1.251 the stated condition was "until that issue
closes"; the issue was still open on 2026-10-03 — but see below, the report did
not reproduce.)

**Re-tested on 2.1.288 (2026-10-03), single machine.**

- [INFER] Read from minified control flow, not executed: 2.1.288 spawns
  exec-form hooks directly, ahead of the PowerShell and bash branches (spawn
  site near offset 214075616, branch `yt=e.args!==void 0` near 214072351).
- [LIVE, 2.1.288, Windows 11, headless `claude -p`, plugin loaded with
  `--plugin-dir`, 3 runs × 2 events (`SessionStart`, `Stop`), exec-form
  `command` = absolute path to `python.exe`]:
  - the exec-form hook was invoked **6/6** with `claude.exe` as its direct
    parent. The shell-form hook beside it ran `claude.exe` → Git `bash.exe` →
    `bash.exe` → `python`;
  - every argument arrived intact — a path containing a space, a
    backslash path, and, in 2 of the 3 runs, a trailing backslash, embedded
    double quotes, an empty string and `;&|` (the first run used a shorter
    argument list);
  - `$HOME` was **not** expanded (it arrived as the literal `$HOME`; the shell
    form expanded it);
  - `${CLAUDE_PLUGIN_ROOT}` was substituted in both forms — backslash-separated
    in the exec form, forward-slash-separated in the shell form.
- **Not tested:** a *bare* command name resolved through `PATH` — which is what
  every echook hook uses (`python`); `.cmd` / `.bat` shims; any Claude Code
  version other than 2.1.288; interactive sessions; macOS and Linux.
- [DOC] *"On Windows, exec form requires `command` to resolve to a real
  executable such as a `.exe`. The `.cmd` and `.bat` shims that npm, npx,
  eslint, and other tools install in `node_modules/.bin` are not executables and
  can't be spawned without a shell."*

**Why the rule stands.** It is no longer "`args` is dropped on Windows" — that
did not reproduce on 2.1.288. It is: the one form echook would actually use (a
bare `python`) is untested; the docs themselves restrict exec form on Windows to
a real executable; and the plugin has no way to require a minimum Claude Code
version (no such field was found in a search of the 2.1.288 binary for the
obvious names, which is absence of evidence, not proof), so a template that
works on 2.1.288 could silently fail on whichever older build a user is still
running. Revisit with a test of the bare-`python` case on the oldest Claude Code
build the project intends to support.

Two neighbouring fields, for the record. `shell` accepts `"bash"` or
`"powershell"` and *"Defaults to bash, or to powershell on Windows when Git Bash
isn't installed"* — but 2.1.251 does not silently fall back for a hook written
for bash; it refuses with *"requires bash but Git Bash was not found. Install Git
for Windows … or add `\"shell\": \"powershell\"` to this hook's config."* That is
loud, and it takes every handler down at once, which is why `diagnose` gained
`WINDOWS_NO_GIT_BASH`. And `commandWindows` is not a real field —
[#90122](https://github.com/anthropics/claude-code/issues/90122) confirms it was
never implemented and is silently ignored, which the Codex template already says.

### Re-verified against 2.1.251: the unions did not move

The whole investigation behind 6.5.1 started from "Claude Code broke the hooks."
It had not. Extracted from the 2.1.251 binary and compared against what v6.5.0
registers:

| Contract | 2.1.251 | Verdict |
|---|---|---|
| `SessionStart.source` | `["startup","resume","clear","compact","fork"]` | unchanged since 2.1.239 |
| `Notification.notification_type` | 14 values, `permission_prompt` … `quota_auto_resume_disabled` | unchanged; echook registers all 14 |
| `Notification` payload | `{message, title?, notification_type}` | unchanged — echook reads `message` |
| `StopFailure.error_type` | 11 values | unchanged |
| `Stop` payload | `stop_hook_active`, `last_assistant_message`, `background_tasks` | unchanged, now *documented* |
| plugin `hooks/hooks.json` discovery | *"The standard hooks/hooks.json is loaded automatically"* | unchanged, undeprecated |
| `async` / `timeout` hook fields | still in the command-hook schema | unchanged |

Two corrections to what the published docs say, in echook's favour:

- **`Stop`'s `background_tasks` and `last_assistant_message` are no longer
  undocumented.** The 2.1.239 note above called `background_tasks` absent from
  the upstream field list; at 2.1.251 both are in the schema with descriptions,
  `background_tasks` explicitly framed as *"Lets hooks distinguish 'session is
  done' from 'session is paused waiting for background work'"* — which is what
  `filters.stop.skip_if_background_tasks_running` already does with it.
- **Four `Notification` matchers echook registers are absent from the docs but
  present in the binary**: `worker_permission_prompt`, `push_notification`,
  `computer_use_enter`, `computer_use_exit`. They are in the closed
  `notification_type` union at 2.1.251. The documentation is behind, not echook.

Also: the hooks reference moved. `docs.claude.com/en/docs/claude-code/hooks` now
301-redirects to [code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks).

### New gates that skip hooks entirely, none of which fired here

Worth knowing, because each produces total silence with no error in the plugin:

- **Workspace trust.** `hooksSkippedForTrust: () => !isWorkspaceTrusted()`, and
  every hook call site short-circuits with `Skipping <event> hook execution -
  workspace trust not accepted`. The status line and `subagentStatusLine` are
  skipped by the same gate. `hasTrustDialogAccepted` per project lives in
  `~/.claude.json`.
- **`disableAllHooks`** (user or managed), **`allowManagedHooksOnly`**, and
  `--bare`, which reports `hooks are disabled in this mode (--bare)`.
- **`SessionEnd` hooks share a 1.5-second budget**, raised only to match a longer
  per-hook `timeout` up to 60 s. Combined with Windows killing an async hook's
  **process tree** at session end, a `session_end` sound can be cut off or never
  start. Do not present `session_end` as a reliable cue on Windows. (Re-measured
  on 2.1.288 for a *headless* exit only, where the async handler was **not**
  truncated — see [`async` and the SessionEnd budget](#async-hooks-and-the-sessionend-budget-at-21288).
  Closing an interactive session was not tested, so the advice above stands.)

### Cursor's `stop` *does* carry finality; Claude Code's does not

The rule that `stop` cannot mean "task complete" is a **Claude Code** fact and
must not be generalised. Cursor's `stop` payload carries `status`
(`completed` / `aborted` / `error`) and `loop_count`, per
[cursor.com/docs/hooks](https://cursor.com/docs/hooks). On Cursor you can at
minimum give aborted and errored turns a different sound, or mute them.

Sibling payload fields worth knowing on Cursor: `sessionEnd.reason`
(`completed|aborted|error|window_close|user_close`), and `subagentStop`'s
`status` / `duration_ms` / `tool_call_count` / `modified_files[]` / `summary` —
that `summary` is a ready-made spoken notification with no transcript parsing.

### Cursor bridges Claude Code hooks without de-duplicating

[cursor.com/docs/reference/third-party-hooks](https://cursor.com/docs/reference/third-party-hooks):
*"All matching hooks from every source run. When responses conflict,
higher-priority sources take precedence during merge."*

Priority decides whose *verdict* wins, not whether a duplicate side effect
executes — and playing a sound is a side effect, not a verdict. So the native
`--cursor` install genuinely double-fires alongside the bridged plugin, and
`DUPLICATE_BRIDGE` is load-bearing. The user-facing toggle (Settings → Rules,
Skills, Subagents → "Include third-party Plugins, Skills, and other configs")
has **no effect on `cursor-agent`**, where bridging is hardcoded, so on the CLI
the abort is the only defence available.

### Windows: Cursor mis-executes bridged Claude Code hooks

Reported on the Cursor forum and acknowledged by staff (2026-07-29): hooks
imported from Claude Code are composed as PowerShell but executed with bash,
which silently blocks every tool call. Relevant to this project specifically,
since Windows is its primary development platform.

### Evidence tags used from the 2.1.288 sync onward

The v6.6.0 sync moved the project's reference point from Claude Code 2.1.251 to 2.1.288. Every fact in the entries below carries one of these tags, and none is stated with more certainty than its tag allows.

| Tag | Meaning |
|---|---|
| **[DOC]** | The official hooks reference, read from the raw page saved on 2026-10-03. Not from a summarising fetch of it — those were found to misreport fields. |
| **[CHANGELOG]** | Official changelog text for 2.1.252–2.1.288, quoted verbatim. |
| **[BIN]** | A literal read in the 2.1.288 binary (offsets are into the native executable). It says what the code *contains*, not that the path runs. |
| **[LIVE]** | Measured by running 2.1.288 on one machine (Windows 11), with the conditions and limits stated beside it. |
| **[INFER]** | Read from minified control flow. A reading, not an observation. |

Entries above this one stand for the versions they name (2.1.215–2.1.251) unless an entry below says otherwise. Nothing in the 2.1.288 entries was measured on macOS or Linux, and none of the [LIVE] runs in the entries from the v6.6.0 sync was an interactive session. The two v6.7.0 entries after "Plugin tooling changes in the range" ([plugin `subagentStatusLine`](#a-plugin-level-subagentstatusline-default-evaluated-not-shipped-v670) and [`sensitive` options](#a-sensitive-userconfig-option-still-reaches-a-shell-form-hook-v670)) are [LIVE] too and say where each ran; the first was an interactive session.

### No new hook events between 2.1.251 and 2.1.288

- **[BIN]** 33 event names in the event array (near offset 204512306).
- **[DOC]** 33 rows in the hooks reference's event table.
- **[CHANGELOG]** No entry for 2.1.252–2.1.288 adds a hook event.

echook registers 30 of the 33 on Claude Code. The three it does not are unchanged: `PreModelSwitch` (a blocking decision hook — never register), `WorktreeCreate` (a provider hook — never register, v6.3.4) and `PostModelSwitch` (observational; cleared for a later release, see above).

The closed unions, re-read in the 2.1.288 binary and compared with the 2.1.251 table above:

| Contract | 2.1.251 | 2.1.288 **[BIN]** | Verdict |
|---|---|---|---|
| `SessionStart.source` | `startup`, `resume`, `clear`, `compact`, `fork` | same five | unchanged |
| `Notification.notification_type` | 14 values | 16: the same 14 plus `model_refusal_fallback` and `auth_storage_failure` | two added; `auth_storage_failure` registered in v6.7.0, `model_refusal_fallback` deliberately not |
| `StopFailure.error_type` | 11 values | 13: the same 11 plus `cloud_credential_error` and `verification_required` | two added; both registered in v6.7.0 |
| `SessionEnd.reason` | — | `clear`, `resume`, `logout`, `prompt_input_exit`, `other` | `bypass_permissions_disabled` is absent (0 occurrences) |

### Matcher values: three registered in v6.7.0, one deliberately not

Four matcher values exist upstream that echook had no variant for at v6.6.0. Nothing was registered for them, so each was **completely silent**: `plugins/audio-hooks/hooks/hooks.json` carries no catch-all (`""`) entry under `Notification` or `StopFailure`, so a value outside the named matchers matches no handler. **v6.7.0 registers the first three** (`Notification` now has 17 named matchers, `StopFailure` 13), each with its own sound in both themes; the fourth stays unregistered.

| Registered as | Event | Value | Default |
|---|---|---|---|
| `stop_failure_cloud_credential_error` | `StopFailure` | `cloud_credential_error` | follows `stop_failure` (off unless enabled), like the other eleven |
| `stop_failure_verification_required` | `StopFailure` | `verification_required` | follows `stop_failure` |
| `notification_auth_storage_failure` | `Notification` | `auth_storage_failure` | **off** (explicit per-variant default, because `notification` is on by default); label "Login needs attention" |
| *(not registered)* | `Notification` | `model_refusal_fallback` | no emitter found, so a handler would be dead code |

The evidence for each value, as recorded at v6.6.0:

| Event | Value | Evidence |
|---|---|---|
| `StopFailure` | `cloud_credential_error` | **[DOC]** listed in the error-type table; *"Matching `StopFailure` on `cloud_credential_error` requires Claude Code v2.1.267 or later, the first version that reports credential-load failures under that value rather than `server_error` or `unknown`."* |
| `StopFailure` | `verification_required` | **[BIN]** in the closed error-type union (near offset 205458838); **not** in the documented table. |
| `Notification` | `auth_storage_failure` | **[BIN]** in the `notification_type` list (near 204254955) and a live emitter (near 228124621), sent with the message `Claude Code login needs attention: credentials could not be saved` — or, in the other branch of the same expression, `…credentials may not have been saved`. Not in the documented table. |
| `Notification` | `model_refusal_fallback` | **[BIN]** declared in the `notification_type` list (near 204254955). **No emitter was found** — the other places the string appears in a search of the binary are SDK message-schema text, not a `Notification` call — so it is **not confirmed to fire as a hook**. |

`cloud_credential_error` was the one with a consequence. By the **[DOC]** sentence above, on 2.1.267 and later a credential-load failure that used to arrive as `server_error` or `unknown` — both of which echook has a variant and a sound for — arrives as `cloud_credential_error`, which echook did not handle until v6.7.0. That follows from the documentation plus the registration table; it was not reproduced.

The `StopFailure` union in the binary now has 13 values (the 11 echook registers plus these two), and `account_on_hold` is conditional in the per-event metadata table (`…KSt()?["account_on_hold"]:[]…`) but unconditional in the zod union. Adding the variants was deferred in v6.6.0 because each needs its own sound in both themes (see the audio-uniqueness rule in `AGENTS.md`) and no ElevenLabs key was available for that release; v6.7.0 added the three sounds.

### `idle_prompt` and the notification timing gates

- **[CHANGELOG 2.1.288]** *"Fixed `idle_prompt` notification hooks firing while background agents are still running (anthropics/claude-code#93672)"*
- **[CHANGELOG 2.1.269]** *"Fixed remote and headless sessions reporting "waiting for your input" while background agents were still running (set `CLAUDE_CODE_BG_TASKS_REPORT_RUNNING=0` to restore the old behavior)"* — this one names the report, not the hook, so it is an adjacent fix rather than the same one.
- **[DOC]** *"The `permission_prompt`, `idle_prompt`, `elicitation_dialog`, and `elicitation_url_dialog` types share their timing with desktop notifications, so in terminal sessions you only see them when you appear to be away from the terminal"*, with these specifics:
  - *"Expect `permission_prompt` once you haven't typed for about six seconds. The timer starts when the permission prompt appears, and each keystroke defers it. To run a hook immediately when Claude asks for permission to use a tool, use PermissionRequest instead."*
  - *"Expect `idle_prompt` about 60 seconds after Claude finishes responding, and only if you haven't typed since and no background agent, such as a background subagent, is still running. Claude Code doesn't send `idle_prompt` while it waits for a claude.ai usage limit to reset."*
  - `agent_completed`: *"A background session finishes or fails. Fires only while agent view is open in a terminal"* — which fits the zero captured in the 2.1.215 table above, without proving it.
- **[BIN]** The `permission_prompt` `Notification` is scheduled behind a 6000 ms timer (`FLt=6000`, defined near offset 214020036 and used near 229974135), and the scheduling function returns a no-op when the environment variable `CLAUDE_CODE_DISABLE_PERMISSION_PROMPT_NOTIFY_HOOKS` is set.

So `idle_prompt` is better as the "waiting for you" signal than it was, but it is a *parked* signal: late by design and conditional on the user not having typed. `permission_request` is the immediate one. **Not re-measured** — neither the fix, the 60-second figure nor the six-second gate was observed on this machine.

### The `Stop` / `SubagentStop` payload at 2.1.288

- **[BIN]** (builder near offset 214048143) The payload is the common fields plus `stop_hook_active`, `last_assistant_message`, `background_tasks[]` and `session_crons[]`. There is still **no field** marking a final turn, queued messages or turn duration. **[DOC]** lists the same four fields.
- **[BIN]** `background_tasks` is built only from tasks passing Claude Code's own in-flight predicate (`function mg`, near 209747898): `status` is `running` **or `pending`**, and the task is not marked `isBackgrounded === false`.
- **[BIN]** Each entry's `type` is a friendly label from a map near offset 209746976, falling back to the raw discriminant for an unmapped type:

  | Internal discriminant | `type` label |
  |---|---|
  | `local_agent` | `subagent` |
  | `local_workflow` | `workflow` |
  | `local_bash` | `shell` |
  | `monitor_mcp`, `monitor_ws` | `monitor` |
  | `mcp_task` | `MCP task` |
  | `in_process_teammate` | `teammate` |
  | `dream` | `dream` |
  | `auto_mode_scan` | `auto-mode scan` |
  | `local_memory_import` | `memory import` |
  | `remote_agent` | `cloud session` |

  **[DOC]** lists seven of these (`shell`, `subagent`, `monitor`, `workflow`, `teammate`, `cloud session`, `MCP task`); `dream`, `auto-mode scan` and `memory import` are in the binary's map and not in the documented list. **[INFER]** From their names they are maintenance work Claude Code runs for itself rather than anything the user started.
- **[DOC]** `session_crons` entries are `{id, schedule, recurring, prompt}`, *"sourced from `CronCreate`, `ScheduleWakeup`, and `/loop`"*.
- **[DOC]** *"The `background_tasks` and `session_crons` arrays let hooks distinguish "session is done" from "session is paused waiting for background work to wake it back up". Both arrays are present when the task registry is reachable and are empty when nothing is in flight or scheduled."* On `SubagentStop`: *"Both arrays are scoped to the parent session, not the subagent."*

**What v6.6.0 does with it.** `filters.<hook>.skip_if_background_tasks_running` now counts `pending` as well as `running` and ignores the three maintenance labels. A new opt-in key, `filters.<hook>.skip_if_session_crons_scheduled`, skips when `session_crons` is non-empty. It is deliberately **separate**: a session with a recurring cron carries that entry for its whole life, so folding it into the existing key would silence every turn of such a session for users who only asked about running work. **Limits:** no live capture of a `pending` entry or of a maintenance-type entry was made; the behaviour rests on the reads above.

### `SubagentStop` fires for Claude Code's own internal agents

**[DOC]** *"Not every SubagentStop event comes from a subagent Claude spawned. Claude Code also runs internal agents for some of its own features, such as prompt suggestions and `/btw` side questions, and SubagentStop fires when one of those finishes too. For those events, `agent_type` is the agent name the session itself runs as, such as one set with `--agent` or the `agent` setting, and an empty string when the session runs without one."*

The next paragraph matters for matchers: *"A `matcher` that names agent types doesn't match an empty `agent_type`. A hook whose matcher is omitted, `""`, or `"*"`, or is a regular expression that matches an empty string, runs for events with an empty `agent_type` too."* echook's `SubagentStop` registration has no matcher, so it runs for them. **[CHANGELOG 2.1.275]** *"Fixed `SubagentStop` hooks with a specific `matcher` firing for every stopping subagent whose agent type was empty"* — the same area, fixed for the matcher case only.

Without a guard, echook would announce "background task finished" for work the user never started. v6.6.0 skips a `SubagentStop` whose `agent_type` is exactly `""` (debug NDJSON action `skipped_internal_subagent`). Conditions and limits:

- **Claude Code invoker only.** Not applied under Cursor, Codex, or an `unknown` invoker — which includes the legacy script install — because nothing establishes what an empty `agent_type` means in those payloads, and guessing could silence a real subagent.
- **An absent key is not the marker**; only the empty string is. Older builds omit the field.
- **Not detectable in a session started with `--agent`** (or the `agent` setting): there the internal agents carry that agent's name and still announce.
- **A forked subagent is not an internal agent.** **[BIN]** The built-in fork agent's `agentType` is the literal `"fork"` (near offset 213219709), so a fork arrives with `agent_type` `"fork"`, not `""`, and still announces.
- **Not measured live.** That a real internal agent produces an empty `agent_type` is taken from **[DOC]**; no capture of one was made.

### `async` hooks and the SessionEnd budget at 2.1.288

- **[DOC]** *"Once an async hook is running in the background, Claude Code doesn't enforce `timeout` on it. Claude Code still enforces `timeout` on a hook you run with `asyncRewake`."*
- **[DOC]** *"`SessionEnd` hooks have a default timeout of 1.5 seconds. It applies when you exit, run `/clear`, or switch sessions with interactive `/resume`."* A longer per-hook `timeout` raises the budget, up to 60 s — but *"Timeouts set on plugin-provided hooks don't raise the budget"*, which covers every echook handler — or `CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS` sets it explicitly.
- **[BIN]** The default is a literal `1500` (`jMo=1500`, near offset 214057834), the ceiling `60000`, and the environment variable overrides both.
- **[DOC]** *"In non-interactive mode with the `-p` flag, Claude Code kills any async hook still running at teardown and finalizes it with outcome `cancelled`"*, and *"If your hook's work must outlive a `claude -p` session, start a fully detached process from it."*

**[LIVE, 2.1.288, Windows 11]** Headless `claude -p "Reply with the single word ok."` (`--model haiku`, `--no-session-persistence`), with a probe plugin loaded by `--plugin-dir` and echook itself disabled for the run. The probe writes a timestamp when the hook starts and another when its 4-second job ends. 3 runs per variant:

| Variant | Result |
|---|---|
| `async: true`; the hook process itself works for 4 s | **Completed 3/3.** The end line was written 3.1–3.3 s *after* `claude` had returned. |
| `async: true`; the hook starts a fully detached child that works for 4 s | **Completed 3/3**, 3.3–3.4 s after `claude` returned. |
| synchronous (no `async`); works for 4 s | **Cut off 3/3** — no end line. `claude` returned about 1.8 s after the hook started (1.78–1.89 s). |
| synchronous, `CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS=6000` | Completed (1 run). An async hook with the variable set also completed (1 run). |

**Limits.** Headless exit only — closing an interactive session, `/clear` and `/resume` were not tested. Only a 4-second job was tested, not longer. The probe wrote timestamps; the real audio player was not involved. The probe observed the *operating-system process* finishing, not the outcome label Claude Code assigns it, so this neither confirms nor refutes the **[DOC]** statement that such a hook is finalised as `cancelled`. It does contradict the reading that a headless exit kills the process: here it survived.

**Consequence.** In this condition the project's async `SessionEnd` handler is **not truncated**, and a synchronous 4-second one is, at about the documented 1.5 s. That is one more reason the handlers must stay `async: true`. It also bounds the recorded `terminalSequence` design above: a synchronous `SessionEnd` handler would have to finish inside the 1.5 s budget.

### Exit code 2 has a different effect on every event

Relevant to any future **synchronous** handler (today every echook handler is `async: true`, and **[DOC]** *"Async hooks can't block or control Claude's behavior"*; an `asyncRewake` hook that exits 2 wakes Claude). **[BIN]** Verbatim from the per-event description table in the binary (`function pot`, near offset 230447080), the `Exit code 2` line of each event that has one:

| Event | `Exit code 2` |
|---|---|
| `PreToolUse` | show stderr to model and block tool call |
| `PostToolUse`, `PostToolUseFailure` | show stderr to model immediately |
| `PostToolBatch` | stop the agentic loop (stderr shown to user only) |
| `UserPromptSubmit` | block processing, erase original prompt, and show stderr to user only |
| `UserPromptExpansion` | block expansion and show stderr to user only |
| `SessionStart`, `SubagentStart`, `Setup` | show stderr to user only |
| `Stop` | show stderr to model and continue conversation |
| `SubagentStop` | show stderr to subagent and continue having it run |
| `PreCompact` | block compaction |
| `PreModelSwitch` | block the switch and show stderr to user |
| `TeammateIdle` | show stderr to teammate and prevent idle (teammate continues working) |
| `TaskCreated` | show stderr to model and prevent task creation |
| `TaskCompleted` | show stderr to model and prevent task completion |
| `Elicitation` | deny the elicitation |
| `ElicitationResult` | block the response (action becomes decline) |
| `ConfigChange` | block the change from being applied to the session |

Two events say so in other words: `StopFailure` is *"Fire-and-forget — hook output and exit codes are ignored"*, and `InstructionsLoaded` *"is observability-only and does not support blocking"*. Events with **no** `Exit code 2` line, where 2 falls under that event's `Other exit codes` text: `Notification`, `PermissionRequest`, `PermissionDenied`, `PostCompact`, `PostModelSwitch`, `SessionEnd`, `WorktreeCreate` (*"worktree creation failed"*), `WorktreeRemove`, `CwdChanged`, `FileChanged`, `DirectoryAdded`, `MessageDisplay` (*"display the original delta"*); most say *"show stderr to user only"*.

These are descriptions in a metadata table, which appears to be what the `/hooks` menu is built from; they are Claude Code's own statement of each contract, not a trace of the dispatch code. Two further contract notes from the same table: `PostModelSwitch` exit 0 — *"stdout shown to Claude on the next request"*; `PreCompact` exit 0 — *"stdout appended as custom compact instructions"*. A handler on either must not print.

### Mods: investigated, not adopted

**[CHANGELOG 2.1.287]** *"Added Claude Mods: plugins may now modify deeper behavior"*. A mod is declared by a top-level `"modules"` array in `hooks/hooks.json` — **[BIN]** at most one entry (schema near offset 204564686). Investigated against both of echook's tracks and adopted for neither:

- **Audio.** **[LIVE, headless `-p`, Windows 11]** `$.audio.play` resolved in 3 ms and played **no sound**; `$.audio.speak` rejected with *"no speech synthesizer on windows"*. **[BIN]** `$.audio.play` uses `afplay` on macOS and otherwise logs `$.audio.play (<plugin>): no audio player on <os>; not played` (near offset 213100999); `$.audio.speak` rejects with `no speech synthesizer on <os>` (near 213104175).
- **Out-of-band notification.** **[BIN]** `$.ui.toast` is documented in the schema as *"what the REPL shows on the notification bar under the prompt for a few seconds"* (near 205599642) — an in-app bar, not an operating-system notification, so it does not serve the "tell me when I am in another app" purpose. A mod has no terminal-write call and no `terminalSequence` field in its `classic.*` result, so it **cannot fix** the inert `terminalSequence` either.
- **Status line.** None of the 13 render sites is the status line, so a mod can neither draw nor replace `statusLine`.
- **Events.** `turn.complete` carries `reason` and `durationMs` but **no finality marker**; `classic.Stop` has the same payload as the command hook. A mod therefore cannot make `Stop` mean "done" either.
- **Coexistence.** **[LIVE]** Classic command hooks and a module coexist in one `hooks.json` on 2.1.288, and the classic hooks still fire if the module fails to load. On 2.1.251 the module is skipped, but `claude plugin validate` rejects the plugin because the event vocabulary changed.
- **Codex.** Codex's hooks-file parser rejects unknown keys (`deny_unknown_fields` in openai/codex `hook_config.rs`), so a `modules` key must never appear in a hooks file Codex reads. echook is insulated only because `.codex-plugin/plugin.json` points at `codex-hooks/plugin-hooks.json`, not at the Claude Code `hooks/hooks.json`.
- **Not tested:** interactive sessions and the Claude Desktop app, where `$.audio.play` may behave differently.

*Provenance:* the `$.audio.*` timings, the coexistence runs, the Codex parser fact, the "no terminal-write call" and "13 render sites" statements and the `turn.complete` / `classic.Stop` payload descriptions come from the investigation behind this release; their raw logs are not archived in this repository and were not re-checked when these notes were written. Re-read in the 2.1.288 binary: the `modules` schema (an array of at most one path, refused beyond that), the `$.audio.*` fallbacks and the `$.ui.toast` description.

### Plugin tooling changes in the range

All **[CHANGELOG]**, verbatim:

- 2.1.268: *"Improved `/plugin`: installing, enabling or disabling a plugin now takes effect when you close the menu; `/reload-plugins` is no longer needed afterwards"*. This is about the **menu**. Whether a `claude plugin install` run from a shell reaches an already-running session without `/reload-plugins` is **not established**, so the install instructions keep that step.
- 2.1.268: *"Added `--json` to `claude plugin install`, `uninstall`, `update`, `enable` and `disable`, and `errorDetails`/`noteDetails` to each row of `claude plugin list --json`"* — the reason `install --plugin` can now emit runnable `claude plugin … --json` commands.
- 2.1.285: *"Added `claude plugin configure <plugin>` to show a plugin's options and which are unset, or save new values read from stdin with `--values-stdin`"*.
- 2.1.257: *"Fixed plugins being able to read files outside their own directory through a declared command, agent, skill, hooks or other component path that is a symlink; such paths are now refused with an error"*.
- 2.1.260: *"Fixed model switching staying blocked for the rest of the session after a plugin hook load failure; each switch now re-checks and the refusal names the cause"*.
- 2.1.259: *"Added `--json` to `claude plugin validate` for a machine-readable validation report"*; 2.1.281: *"…added a `claude plugin validate` warning when a shell-form hook leaves `${CLAUDE_PLUGIN_ROOT}` unquoted (it breaks on plugin paths with spaces)"*. Both bear on the v6.6.0 `plugin-validate` CI job, which has since run on a real runner and passed on 6.6.0 (the full test matrix passed there too).
- **Orphaned plugin-cache directories** (observed on disk, one machine; not in [DOC] or [CHANGELOG]): after an uninstall, Claude Code leaves `cache/<marketplace>/<plugin>/<version>/` behind and drops an `.orphaned_at` file (a millisecond epoch) in it; the live version directory has none (it has `.in_use/`). Seen on a real install: 7 of 8 version directories of one plugin carried the marker, the one in use did not. The meaning of the marker is **inferred** from that, not documented. v6.6.0's plugin detection (`install --scripts` → `DUAL_INSTALL_DETECTED`) skips marked directories, because counting an orphan as "installed" refused the install with a remedy that could not help.

### A plugin-level `subagentStatusLine` default: evaluated, not shipped (v6.7.0)

A plugin can ship a `settings.json`. The idea was a default `subagentStatusLine` so that installing the plugin gives every user the per-subagent row without running `audio-hooks statusline subagent install`. It was tested and **not shipped**.

Findings, **[LIVE, Claude Code 2.1.288, Windows, an interactive session driven through a pseudo-terminal]**:

- Claude Code loads a plugin's `settings.json` and **keeps only `subagentStatusLine`**; other keys are dropped.
- It does **not** substitute `${CLAUDE_PLUGIN_ROOT}` in that setting's command: the variable arrived empty. The plugin therefore cannot point the command at its own script.
- The plugin's `bin/` directory is **not on PATH** for that command (exit 127, command not found), so naming the script by its bare name does not work either.
- A marketplace-installed plugin therefore has no way to name its own script, which defeats the point of a default.
- There is **no CLI-only way to turn a plugin default off**: `audio-hooks statusline subagent uninstall` removes only the user's own `settings.json` entry, not a plugin's.

Limits: the row was seen to **execute** (the command ran); it was **not seen on screen**. One machine, one Claude Code version. The first three bullets are the reason the default was dropped; the user's own setting, written by `statusline subagent install`, remains the only supported route.

### A `sensitive` `userConfig` option still reaches a shell-form hook (v6.7.0)

v6.7.0 marks `userConfig.webhook_url` as `sensitive`. **[LIVE, Claude Code 2.1.288, in a throwaway config directory]**:

- A sensitive option's value is stored in the **credentials file**, not in `settings.json`.
- It is still exported to a shell-form hook as `CLAUDE_PLUGIN_OPTION_WEBHOOK_URL`, which is the only place the runner reads it.
- A value stored **before** the flag was added is still reported as configured after the update.
- **[DOC]** Only `options`, not `sensitive`, carries a minimum-version floor, which is why `options` is never added to a `userConfig` field (a Claude Code older than 2.1.271 would refuse to load the plugin).

Not measured: behaviour on a Claude Code older than 2.1.288, on macOS or Linux, or with an unavailable credential store.

---

## Matcher coverage as of v6.4.1

47 variants across 8 matcher-scoped events as of v6.7.0. The table below is the v6.4.1 snapshot; corrections follow it.

| Event | Matchers registered | Notes |
|---|---|---|
| `Notification` | 8 of the 14 typed values | 4 added in v6.4; `agent_*` pair unverified (above). The six unregistered types — `elicitation_url_dialog`, `worker_permission_prompt`, `push_notification`, `computer_use_enter`/`_exit`, `quota_auto_resume_fired`/`_stale`/`_disabled` — still reach the catch-all, but share one sound with no per-variant toggle. `notification_type` is a bare string in the payload, not an enum, so unknown values are expected |
| `SessionEnd` | `clear`, `resume`, `logout`, `prompt_input_exit`, `bypass_permissions_disabled\|other` | first four were dead code until v6.4 — defined in `SYNTHETIC_EVENT_MAP` but the event was registered with no matcher, so nothing invoked them. `bypass_permissions_disabled` is not sent by Claude Code ≥ 2.1.234 (upstream [DOC]: *"Removed in v2.1.234; Claude Code doesn't send it"*; 0 occurrences in the 2.1.288 binary, whose reasons are exactly `clear`, `resume`, `logout`, `prompt_input_exit`, `other`). It is **deliberately retained** as a harmless alternation: it costs nothing, and dropping it would lose the `other` SessionEnd sound on older Claude Code builds that still send it, which a plugin cannot exclude (it has no minimum-version gate). Nothing changed here in v6.6.0 |
| `SessionStart` | `startup`, `resume`, `clear`, `compact`, **`fork`** | `fork` added in v6.4.1 — see above; forked sessions were silent from Claude Code 2.1.213 until then |
| `StopFailure` | **all 11 upstream types, one handler each** | v6.4.1 unwound the five-way collapse onto `stop_failure_other` and dropped `other`, which was never a Claude Code value. Every variant toggle now actually works; the contract-test allowlist is empty as a result |
| `PreCompact` / `PostCompact` / `Setup` | both/both/both | |
| `PermissionRequest` | `""` (catch-all) | |

`Notification` and `PermissionRequest` have no Cursor or Codex equivalent; the runner hard-skips them for those invokers regardless of registration.

**Since v6.4.1.** The `Notification` row above is a v6.4.1 snapshot: v6.5.0 registered the missing types, so all 16 matchers up to Claude Code 2.1.251 now have a variant, and `plugins/audio-hooks/hooks/hooks.json` has no catch-all `Notification` or `StopFailure` entry — which means the "still reach the catch-all" remark no longer describes what ships. A value outside the named matchers matches no handler and is silent; the four such values known at 2.1.288 are listed in [Matcher values: three registered in v6.7.0, one deliberately not](#matcher-values-three-registered-in-v670-one-deliberately-not) — three have a variant since v6.7.0, `model_refusal_fallback` does not. `StopFailure` is now "all 13 upstream types, one handler each" and `Notification` has 17 named matchers. The `SessionEnd` row was reworded in v6.6.0 to record the upstream removal; the registration itself is unchanged.
