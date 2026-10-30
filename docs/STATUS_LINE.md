# Status Line

> Orientation for echook's **second track**, the status line. The live source of
> truth is the CLI: `audio-hooks statusline segments` (Claude Code catalogue) and
> `audio-hooks statusline codex show` (Codex state). This page keeps only what
> those commands do not say. Current as of **v6.7.0**.

## The one thing to understand first

The two editors expose the status line in **fundamentally different** ways, and echook treats them differently:

| Editor | Mechanism | What echook does |
|---|---|---|
| **Claude Code** | Runs a **command script** and prints whatever it returns (`bin/audio-hooks-statusline.py`, registered in `~/.claude/settings.json`). | **Renders** the whole line. echook can show any segment it wants. |
| **Codex** | Renders only a **fixed list of built-in item IDs** under `[tui].status_line` / `[tui].terminal_title` in `~/.codex/config.toml`. No command/script hook (open feature request [openai/codex#17827](https://github.com/openai/codex/issues/17827)). | **Curates** that fixed list. echook *cannot* render custom text or new segments in Codex — it can only pick/order/de-duplicate the built-in IDs so the line stops truncating. |
| **Cursor** | IDE: none. CLI (`cursor-agent`): a custom `statusLine` command exists upstream but is unverified — see below. | — |

If you remember nothing else: **Claude Code = render (rich, 33 segments); Codex = curate (fixed menu).**

## Claude Code

Two logical lines, each auto-reflowed into as many physical rows as the terminal width needs (segments are never split). The segment catalogue (name, line, source field, `default` flag) is `audio-hooks statusline segments`; it is not copied here.

- `git_dirty` is the only segment that shells out, from a detached process (see `docs/INVARIANTS.md`); every other one comes from the
  stdin JSON or echook's own `status`. The subscription **plan name** ("Max"/"Pro") is not piped to status-line scripts, so it is intentionally not shown.
- Reset clocks show a date when it is not today: `weekly_quota` renders
  `resets Jul 4 5am`; `api_quota` stays a bare time unless its reset crosses midnight (v6.3.2).

### Choosing segments

Three keys under `statusline_settings`:

- `visible_segments` — whitelist. When non-empty, only these show.
- `hidden_segments` — blacklist. Applied only when `visible_segments` is empty.
- `extra_segments` (v6.7) — opt-in additions. Applied only when `visible_segments`
  is empty: segments the catalogue marks `"default": false` appear only when named here. `hidden_segments` still wins. A non-empty `visible_segments` can name an opt-in segment directly.

All three are read defensively, because a status line script that raises prints nothing at all: a non-string entry is ignored, and a non-list value counts as unset. A whitelist containing only junk is therefore "unset" (the default set shows), not "show nothing".

### Opt-in segments (v6.7.0)

Every earlier segment joined the default set, which was safe only because each is data-gated: an upgrade changes nothing for a session that does not carry the field. That stops being true for a field Claude Code sends to everyone. Two segments are therefore **opt-in through `statusline_settings.extra_segments`**:

- `prompt_cache` — off. `prompt_cache` is sent to every session after its first
  response, so default-on would add a permanent item to every existing user's line on upgrade, and its countdown changes on every refresh. Enable it with `audio-hooks set statusline_settings.extra_segments '["prompt_cache"]'`.
- `remote` — off. The field is undocumented and the condition that makes Claude Code
  send it was not traced, so it must not start appearing by default.

`fast_mode` and `spend_limit` are in the default set but draw only when Claude Code sends the data (`fast_mode` is always sent as a boolean; only `true` draws). `cache cold` after an idle gap is normal, not a fault, hence yellow rather than red.

### Width and truncation

Width is resolved as: `statusline_settings.max_width` override, then the `COLUMNS` env var Claude Code exports (v2.1.153+), then a fallback of 80, minus `WIDTH_SAFETY_MARGIN` (8 cells since v6.3.1; it was 4, and an emoji-dense row on the budget boundary could clip, e.g. `Theme: Chim…`). `max_width` `0` means auto-detect.

## Following the viewed agent

When the person switches the main view to a subagent's transcript (the agent list under the prompt, `↓` then `Enter`), the line shows that agent, not the main conversation:

```
[▸ Haiku 5.5] | 🤖 ▸ general-purpose | 🧠 medium | …  █░░░░░░░ ▸ Context: 14% (29K/~200K)  cache 84%  💲 $0.12
```

`▸` marks whatever describes the viewed agent. Swapped: `model`, `agent`, `effort`, `context`, `tokens`. Dropped, having no counterpart for an agent: `thinking`, `fast_mode`, `prompt_cache`, `exceeds_200k`, and the `/compact` hint. Unchanged: everything session-wide, so `cost` is still the whole session's and not the agent's share.

**Why it needs a module.** Read in the 2.1.314 binary **[BIN]**: the status line's stdin is built from `mainLoopModel` and the main conversation's messages whatever is on screen, `viewingAgentTaskId` is in neither it nor the `subagentStatusLine` payload, and it is not among the inputs that re-run the command. No command hook fires on a switch. The only published reading of the view is `e.props.view` at a `ui.render` site, so `plugins/audio-hooks/hooks/view-sensor.ts` hooks `AbovePrompt`, draws nothing, and writes `{"agent_id": …, "name": …, "type": …}` when the view changes.

**The pieces.**

| Step | Where |
|---|---|
| Read the view; write `$TEMP/claude_audio_hooks_queue/viewed_agent.<session_id>.json` | `hooks/view-sensor.ts` |
| Read that file; `agent_id: null`, no file or anything malformed means the main conversation | `_read_viewed_agent` |
| Find `<transcript without .jsonl>/subagents/agent-<id>.jsonl`, or under `workflows/<run>/` | `_agent_transcript` |
| Take the newest assistant record with `usage`, from the file's tail | `_last_agent_turn` |
| Replace the session document's fields; the segments render from it unchanged | `_apply_viewed_agent` |

**Refresh.** Nothing tells the script the view changed, so `refreshInterval` is how soon it follows: `statusline install` registers 2 seconds. An existing registration keeps its old interval until `statusline install` runs again; `statusline show` says so in `next_steps`. Do not lower it to 1: Claude Code aborts a run still going at the next tick, and bare Python alone took up to 1.3 s on the measured machine under load.

**The context window is partly an estimate.** A transcript records the tokens but not the window. When the agent runs the main conversation's model, the main window is exact. Otherwise it is 200K, or 1M if the request asked for `[1m]` or the agent has already passed 200K, and the figure shows as `~200K`. The per-subagent row (`statusline subagent`) has the exact value because Claude Code hands it that one.

**Known limits.** Before an agent's first response there is no record, so the model reads `[▸ agent]` and no context shows. An agent whose transcript is not on disk (session persistence off) shows its name only. A teammate's view has not been run; if its id does not name a transcript file, it falls into that same name-only case. The sidecar has no heartbeat: `session.start` clears it, but if the module is removed while an agent is in view, that session's line keeps naming the agent.

Measured live on Claude Code 2.1.314, Windows 11, interactive session through a pseudo-terminal, `--plugin-dir`, Sonnet main conversation and Haiku subagent: main → agent → main → agent, each switch shown within about 3 seconds.

## Upstream fields added in v6.7.0

Four fields Claude Code (checked against 2.1.288) pipes to a status line script were read by no segment until v6.7.0. **[DOC]** means the field is in the official status line page (<https://code.claude.com/docs/en/statusline>, read as raw Markdown); **[CHANGELOG]** and **[BIN]** mean the Claude Code changelog and the 2.1.288 binary.

| Segment | Field | Minimum Claude Code | Evidence | Behaviour worth knowing |
|---|---|---|---|---|
| `prompt_cache` | `prompt_cache` (`warm`, `caching_observed`, `ttl`, `expires_at`, `recache_tokens_if_cold`, `last_miss_at`, `last_miss_cause`, …) | 2.1.251 | **[DOC]** field table + "Prompt cache fields"; **[CHANGELOG]** 2.1.251 | Absent until the main conversation's first API response; subagent requests are not counted. A warm cache reaching `expires_at` makes Claude Code re-run the script, so the payload can say `warm: true` for an instant after expiry — the segment treats `expires_at <= now` as cold. `caching_observed: false` (caching off, or a provider that does not report it) renders nothing. |
| `prompt_cache` miss cause | `prompt_cache.last_miss_at`, `last_miss_cause.causes[]` | 2.1.260 | **[DOC]** "Last miss cause"; **[CHANGELOG]** 2.1.260; the cause names and their meanings come from the binary's closed set (**[BIN]**, `promptCacheLedger`) | `last_miss_cause` is `null` until a miss, and again when no cause could be identified. The cause is shown only for a miss newer than 10 minutes (`RECENT_MISS_SEC`), so a stale diagnosis never sits beside a live state. Several causes show the first plus `+N`. |
| `spend_limit` | `rate_limits.spend_limit.used_percentage`, `resets_at` | 2.1.251 | **[DOC]** "Spend limit fields" | Claude apps gateway users only. `used_percentage` can exceed 100 once the limit is passed (the bar clamps, the number does not). |
| `spend_limit` amounts | `rate_limits.spend_limit.used_usd`, `limit_usd`, `period` | 2.1.284, on both Claude Code **and** the gateway | **[DOC]**; **[CHANGELOG]** 2.1.284 | Each can be absent even when `spend_limit` is present (and stays absent under `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`); the amounts arrive up to ~5 minutes after the percentage. `period` is one of `daily`, `weekly`, `monthly`. |
| `fast_mode` | `fast_mode` | not stated in the docs | **[DOC]** field table ("Whether fast mode is enabled for the session"); **[BIN]** the stdin builder always emits it, as a boolean | Only a literal `true` draws anything. |
| `remote` | `remote.session_id` | unknown | **[BIN] only — undocumented.** Added when the session's surface reports a remote attachment (`surfaceCapabilities.remote`, `null` for an ordinary local session); the exact trigger was not traced | Opt-in for that reason. May change or vanish without notice. |

The rest of `prompt_cache` (`requests`, `misses`, `expected_rebuilds`, `hit_ratio`, `cache_write_tokens`, `miss_recache_tokens`, `miss_causes`) is still not rendered: the `tokens` segment already shows the per-call cache-hit ratio, and a second ratio beside it would disagree for good reasons that a one-line segment cannot explain.

**Every field here can be absent, `null`, or the wrong type** on an older or newer Claude Code, and a status line script that raises prints nothing at all. Each formatter (`_fmt_prompt_cache`, `_fmt_spend_limit`, `_fmt_fast_mode`, `_fmt_remote` in `bin/audio-hooks-statusline.py`) therefore returns `""` on anything it does not recognise; numbers must be real numbers (a `bool` or a numeric string is rejected), a boolean `resets_at` is ignored (`True` would otherwise read as 1970-01-01 00:00:01), and a timestamp implausibly far in the future (a millisecond epoch) drops the countdown rather than showing `1000h`. Displayed percentages are clamped (to ±9999 for the quota, spend and context numbers; the bars clamp to 0–100), so a malformed `used_percentage` such as `1e308` cannot print a 300-digit number or raise.

**Codex is unaffected.** It renders only a fixed list of item IDs; it already has `fast-mode` and its own rate-limit items, and nothing equivalent to `prompt_cache`, `spend_limit` or `remote`.

## Subagent status line (Claude Code, v6.5)

`subagentStatusLine` is a **second, separate** settings key from `statusLine`. It renders one row per task in the agent panel rather than one line for the session. Manage it with `audio-hooks statusline subagent show|install|uninstall`; installing one status line does not install the other.

**The output contract differs from the main status line, and getting it wrong produces silence rather than an error.** Claude Code pipes one JSON object on stdin (`columns`, `tasks[]` with `id`, `name`, `type`, `status`, `description`, `label`, `startTime`, `model`, `effort`, `contextWindowSize`, `tokenCount`, `tokenSamples`, `cwd`, plus session fields) and expects **NDJSON** back, one object per line: `{"id": "<the task's id>", "content": "<rendered row>"}`.

Claude Code keys the result by `id`, so a row whose `id` does not match a task is dropped. A line that is not JSON is logged as *"subagentStatusLine emitted non-JSON line"*; one that parses but lacks `id`/`content` is *"emitted invalid schema"*. The timeout is **5 s**, so the renderer does no I/O beyond stdin. The row is compact — status icon, model, reasoning effort, context used (with window size), elapsed — and every field is data-gated:

```
▶ · opus-5 · 🧠high · ◔24% 48.0k/200.0k · ⏱3m05s
```

**Why the plugin does not install it for you (v6.7.0).** A plugin-level default (`settings.json` shipped inside the plugin) was evaluated and not shipped. Claude Code loads such a file and keeps only `subagentStatusLine`, but does not substitute `${CLAUDE_PLUGIN_ROOT}` in its command (it arrived empty) and does not put the plugin's `bin/` on PATH for it (exit 127), so a marketplace-installed plugin cannot name its own script; and `statusline subagent uninstall` removes only the user's own setting, so a plugin default would have no CLI-only off switch. Measured on Claude Code
2.1.288 on Windows in an interactive session; the row was seen to execute, not seen on
screen. Details: [EVENT_BEHAVIOR_NOTES.md](EVENT_BEHAVIOR_NOTES.md#a-plugin-level-subagentstatusline-default-evaluated-not-shipped-v670). Run `audio-hooks statusline subagent install` yourself.

## Codex (curation only)

Codex accepts only fixed built-in item IDs; echook **curates**, it does not render. Two `[tui]` arrays are curated, `status_line` (footer) and `terminal_title`, via `audio-hooks statusline codex show|preview|apply` (presets `minimal|balanced|full`, `--target status_line|terminal_title|both`, or `--items a,b,c`).

- `apply` backs up `config.toml` to a timestamped `.echook-*.bak` sibling first, then
  makes a surgical edit: only the targeted array (and, if absent, a `[tui]` header) changes. When `tomllib` is available (Python 3.11+) the result is parse- and round-trip-validated before writing. Restart Codex (or run `/statusline`) to reload.
- `--items` rejects unknown IDs with `INVALID_USAGE`. Accepted IDs were verified
  against the Codex source at `rust-v0.160.1` (`codex-rs/tui/src/bottom_pane/status_line_setup.rs` and `title_setup.rs`), the latest stable on 2026-10-07; the list was originally recovered from the 0.143 binary and no 0.143 ID was dropped, so older installs still validate. An ID valid for only one target is ignored by Codex on the other; echook validates against the union and does not check the target.
- Aliases Codex still parses to the same item are accepted by echook (Codex writes the
  canonical name back). `approval` is an alias of `approval-mode`, not a canonical ID.
- An item ID with no value is simply not drawn (`git-branch` outside a git repo,
  `five-hour-limit` before any rate-limit usage). That is Codex behaviour, not an echook bug.

## Cursor: investigated, not implemented

**The Cursor IDE has no third-party-writable status bar.** Checked directly against the installed build (`resources/app/out/vs/workbench/workbench.desktop.main.js`): every occurrence of `statusLine` is an *internal transcript row kind* (`kind:"statusLine"`, `rowId:` `status-line:${id}`) used to render rows inside the agent transcript. There is no settings key, no command contribution, and no schema accepting a user command. The same bundle *does* contain the hook event names (`beforeShellExecution`, `afterFileEdit`, …), so this is a genuine absence rather than a failed search. The upstream request to expose a status-bar API to extensions is still open.

**`cursor-agent` (the CLI) is a different story, and remains unverified.** Cursor's CLI changelog describes a custom `statusLine` — *"Point `statusLine` at your own command and render its output (with live token usage data) in the prompt footer"* (April 2026), later *"a custom `statusLine` command keeps its throttle during streaming updates"* (August 2026) — and the mechanism is explicitly modelled on Claude Code's convention (`type: "command"`, a spawned script, JSON on stdin), which would make echook's existing renderer largely reusable.

It is not implemented because **`cursor-agent` is not installable on the machine this was investigated from** (only the IDE is present), so the settings-file location and the exact stdin schema could not be confirmed first-hand. Given this project's rule — verify an interface before shipping against it — guessing here would produce either silence or a wrong-shaped payload, with no diagnostic either way. A known caveat is already on record: a custom `statusLine` currently *replaces* the native CLI footer rather than composing with it, so the Auto-review indicator is lost.

**To pick this up:** install `cursor-agent`, run `/statusline`, capture the stdin payload with a shim that appends stdin to a file and exits 0, and compare it against Claude Code's schema. If it matches, wiring it is mostly a matter of pointing a second registration at `bin/audio-hooks-statusline.py`.

Code: `bin/audio-hooks-statusline.py` (renderer), `bin/echook_cli/statusline.py` (`STATUSLINE_SEGMENTS`, `CODEX_*_PRESETS`, the `statusline` subcommands), tests `tests/test_statusline.py` and `tests/test_codex_statusline.py`; internals in [ARCHITECTURE.md](ARCHITECTURE.md).
