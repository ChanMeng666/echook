# Status Line — complete reference

> Authoritative orientation for echook's **second track**, the status line. The
> *live* source of truth is always the CLI: `audio-hooks statusline segments`
> (Claude Code catalog) and `audio-hooks statusline codex show` (Codex state).
> This page explains the model behind those commands. Current as of **v6.7.0**.

## The one thing to understand first

The two editors expose the status line in **fundamentally different** ways, and
echook treats them differently:

| Editor | Mechanism | What echook does |
|---|---|---|
| **Claude Code** | Runs a **command script** and prints whatever it returns (`bin/audio-hooks-statusline.py`, registered in `~/.claude/settings.json`). | **Renders** the whole line. echook can show any segment it wants. |
| **Codex** | Renders only a **fixed list of built-in item IDs** under `[tui].status_line` / `[tui].terminal_title` in `~/.codex/config.toml`. No command/script hook (open feature request [openai/codex#17827](https://github.com/openai/codex/issues/17827)). | **Curates** that fixed list. echook *cannot* render custom text or new segments in Codex — it can only pick/order/de-duplicate the built-in IDs so the line stops truncating. |
| **Cursor** | IDE: none. CLI (`cursor-agent`): a custom `statusLine` command exists upstream but is unverified — see below. | — |

If you remember nothing else: **Claude Code = render (rich, 33 segments); Codex = curate (fixed menu).**

---

## Claude Code status line

Two logical lines, each auto-reflowed into as many physical rows as the terminal
width needs (segments are never split). Registered/removed with:

```
audio-hooks statusline show        # is it registered?
audio-hooks statusline install     # register in ~/.claude/settings.json (then restart Claude Code)
audio-hooks statusline uninstall   # remove
audio-hooks statusline segments    # JSON catalog of every segment (the live source of truth)
```

### Segment catalog (33)

Every segment maps to a field Claude Code pipes to the script on stdin
(see <https://code.claude.com/docs/en/statusline>). **data-gated** segments
render only when their field is present, so a plain session stays uncluttered
while a rich one shows the full picture. Two segments (`remote`, `prompt_cache`)
are **opt-in**: they are not part of the "show everything" default, see
[Opt-in segments](#opt-in-segments-v670).

**Line 1 — identity / configuration**

| Segment | When | Source | Shows |
|---|---|---|---|
| `model` | always | `model.display_name` | Active model display name |
| `session_name` | data-gated | `session_name` | Custom session name set via `--name` or `/rename` |
| `agent` | data-gated | `agent.name` | Agent name when running with `--agent` |
| `remote` | data-gated, **opt-in** | `remote.session_id` | `☁ remote` for a session attached to a remote/cloud surface. **Undocumented upstream** |
| `effort` | data-gated | `effort.level` | Reasoning effort (low/medium/high/xhigh/max) |
| `fast_mode` | data-gated | `fast_mode` | `🚀 fast` while [fast mode](https://code.claude.com/docs/en/fast-mode) is on; nothing when off |
| `thinking` | data-gated | `thinking.enabled` | Shown when extended thinking is enabled |
| `vim` | data-gated | `vim.mode` | Vim editing mode (when vim mode is on) |
| `output_style` | data-gated | `output_style.name` | Active output style (hidden when `default`) |
| `cc_version` | data-gated | `version` | Claude Code's own version |
| `cwd` | data-gated | `cwd` | Working directory (abbreviated) |
| `repo` | data-gated | `workspace.repo` | Git remote `owner/name` |
| `version` | always | `audio-hooks status` | echook version |
| `sounds` | always | `audio-hooks status` | Enabled / total sound hooks |
| `webhook` | always | `audio-hooks status` | Webhook on/off + format |
| `theme` | always | `audio-hooks status` | Audio theme (Voice/Chimes) |

**Line 2 — live state / metrics**

| Segment | When | Source | Shows |
|---|---|---|---|
| `snooze` | data-gated | `audio-hooks status` | Mute countdown when snoozed |
| `branch` | data-gated | `workspace.git_worktree` | Git branch / worktree |
| `git_dirty` | data-gated | `git status --porcelain` | Uncommitted-change count (shells out to git; cached ~5s) |
| `worktree` | data-gated | `worktree.name` | Managed worktree name |
| `pr` | data-gated | `pr.number` | Pull request number + review state |
| `added_dirs` | data-gated | `workspace.added_dirs` | Count of `/add-dir` directories |
| `api_quota` | data-gated | `rate_limits.five_hour` | 5-hour rate-limit usage + reset clock (date shown if not today) |
| `weekly_quota` | data-gated | `rate_limits.seven_day` | 7-day rate-limit usage + reset clock — date + time, e.g. `resets Jul 4 5am` |
| `spend_limit` | data-gated | `rate_limits.spend_limit` | Claude apps gateway spend limit: usage %, `$used/$limit period` when sent, reset clock — e.g. `Spend: 62% · $314.12/$500 monthly · resets Nov 1 8am` |
| `context` | data-gated | `context_window` | Context-window usage % + token counts |
| `tokens` | data-gated | `context_window.current_usage` | Cache-hit ratio (cache reads ÷ input) |
| `prompt_cache` | data-gated, **opt-in** | `prompt_cache` | Prompt-cache state: `cache warm 4m` (time to expiry) or `cache cold`, plus the cause of a recent miss |
| `exceeds_200k` | data-gated | `exceeds_200k_tokens` | Warning flag when tokens exceed 200K |
| `cost` | data-gated | `cost.total_cost_usd` | Session cost + lines added/removed |
| `duration` | data-gated | `cost.total_duration_ms` | Wall-clock session duration |
| `api_time` | data-gated | `cost.total_api_duration_ms` | Share of wall-clock spent waiting on the API |
| `burn_rate` | data-gated | `derived` | Cost velocity ($/hour) |

> `git_dirty` is the only segment that shells out; every other segment comes
> from the stdin JSON or echook's own `status`. The subscription **plan name**
> ("Max"/"Pro") is not piped to status-line scripts, so it is intentionally not
> shown.
>
> **Reset clocks show a date when it isn't today.** The 7-day weekly window can
> reset days away, so a bare time ("resets 5am") is ambiguous — `weekly_quota`
> renders `resets Jul 4 5am`. The 5-hour window is always soon, so `api_quota`
> stays a bare time unless its reset crosses midnight onto another day. (v6.3.2)

### Choosing which segments appear

Three config keys under `statusline_settings` (set via `audio-hooks set`):

- **`visible_segments`** — *whitelist*. When non-empty, **only** these show.
- **`hidden_segments`** — *blacklist*. Applied only when `visible_segments` is
  empty: show everything **except** these. Use this to drop a couple of segments
  from the comprehensive default without enumerating all the keepers.
- **`extra_segments`** (v6.7) — *opt-in additions*. Applied only when
  `visible_segments` is empty: segments that are left out of the default
  (the catalog marks them `"default": false`) appear only when named here.
  `hidden_segments` still wins over it. A non-empty `visible_segments`
  whitelist can name an opt-in segment directly instead.

The three lists are read defensively, because a status line script that raises
prints nothing at all: a **non-string entry** (a number, a list, `null`) in any of
them is ignored rather than blanking the line, and a **non-list value** counts as
unset. A `visible_segments` whitelist containing only junk is therefore "unset"
(the default set shows), not "show nothing".

```bash
# Show only the two progress bars:
audio-hooks set statusline_settings.visible_segments '["context","api_quota"]'
# Keep the rich default but drop two metrics:
audio-hooks set statusline_settings.hidden_segments '["burn_rate","api_time"]'
# Back to the full default:
audio-hooks set statusline_settings.visible_segments '[]'
# Turn on the opt-in prompt-cache segment (keeps everything else as is):
audio-hooks set statusline_settings.extra_segments '["prompt_cache"]'
```

### Opt-in segments (v6.7.0)

Every segment so far joined the default set, which is safe only because each is
data-gated: an upgrade changes nothing for a user whose session does not carry
the field. That stops being true for a field Claude Code sends to *everyone*.

| Segment | Default | Why |
|---|---|---|
| `fast_mode` | **on** | Draws only while fast mode is on (`fast_mode` is always sent, as a boolean, so `false` renders nothing). A user in fast mode is exactly who should see it. |
| `spend_limit` | **on** | Draws only behind a Claude apps gateway that sets a spend limit — the same population `api_quota`/`weekly_quota` already serve for subscribers. |
| `prompt_cache` | **off** | `prompt_cache` is sent to every session after its first response, so a default-on segment would add a permanent item to every existing user's line on upgrade, and its countdown changes on every refresh. Enable it with `extra_segments`. |
| `remote` | **off** | The field is undocumented and the condition that makes Claude Code send it was not traced, so it must not start appearing by default. |

`audio-hooks statusline segments` reports each segment's `default` flag.

**What they look like**

```
cache warm 4m                                           warm, 4 minutes to expiry (green; yellow in the last 60 s)
cache warm 4m · miss: tools changed                     ...and the last miss (under 10 minutes ago) was a tool change
cache cold · 45K to re-cache · miss: idle past 5m TTL   expired; the next request re-caches ~45K tokens
████░░░░ Spend: 62% · $314.12/$500 monthly · resets Nov 1 8am
🚀 fast
☁ remote
```

`cache cold` after an idle gap is normal, not a fault, hence yellow rather than red.
`prompt_cache` is on line 2 beside `tokens`; `fast_mode` and `remote` are on line 1
beside `effort` and `agent`; `spend_limit` follows `weekly_quota`.

### Width & truncation

Each line wraps at segment boundaries to fit the terminal; nothing is split.
Width is resolved as: `statusline_settings.max_width` override → the `COLUMNS`
env var Claude Code exports (v2.1.153+) → fallback 80, minus
`WIDTH_SAFETY_MARGIN` (8 cells, since v6.3.1 — covers padding, the reserved edge,
and emoji that render slightly wider than measured; before v6.3.1 it was 4 and an
emoji-dense row on the budget boundary could clip, e.g. `Theme: Chim…`).

```bash
audio-hooks set statusline_settings.max_width 120   # pin width if COLUMNS is unreliable
audio-hooks set statusline_settings.max_width 0     # back to auto-detect
```

### Upstream fields added in v6.7.0

Four fields Claude Code (checked against 2.1.288) pipes to a status line script
were read by no segment until v6.7.0. **[DOC]** means the field is in the
official status line page (<https://code.claude.com/docs/en/statusline>, read as
raw Markdown); **[CHANGELOG]** and **[BIN]** mean the Claude Code changelog and
the 2.1.288 binary.

| Segment | Field | Minimum Claude Code | Evidence | Behaviour worth knowing |
|---|---|---|---|---|
| `prompt_cache` | `prompt_cache` (`warm`, `caching_observed`, `ttl`, `expires_at`, `recache_tokens_if_cold`, `last_miss_at`, `last_miss_cause`, …) | 2.1.251 | **[DOC]** field table + "Prompt cache fields"; **[CHANGELOG]** 2.1.251 | Absent until the main conversation's first API response; subagent requests are not counted. A warm cache reaching `expires_at` makes Claude Code re-run the script, so the payload can say `warm: true` for an instant after expiry — the segment treats `expires_at <= now` as cold. `caching_observed: false` (caching off, or a provider that does not report it) renders nothing. |
| `prompt_cache` miss cause | `prompt_cache.last_miss_at`, `last_miss_cause.causes[]` | 2.1.260 | **[DOC]** "Last miss cause"; **[CHANGELOG]** 2.1.260; the cause names and their meanings come from the binary's closed set (**[BIN]**, `promptCacheLedger`) | `last_miss_cause` is `null` until a miss, and again when no cause could be identified. The cause is shown only for a miss newer than 10 minutes (`RECENT_MISS_SEC`), so a stale diagnosis never sits beside a live state. Several causes show the first plus `+N`. |
| `spend_limit` | `rate_limits.spend_limit.used_percentage`, `resets_at` | 2.1.251 | **[DOC]** "Spend limit fields" | Claude apps gateway users only. `used_percentage` can exceed 100 once the limit is passed (the bar clamps, the number does not). |
| `spend_limit` amounts | `rate_limits.spend_limit.used_usd`, `limit_usd`, `period` | 2.1.284, on both Claude Code **and** the gateway | **[DOC]**; **[CHANGELOG]** 2.1.284 | Each can be absent even when `spend_limit` is present (and stays absent under `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`); the amounts arrive up to ~5 minutes after the percentage. `period` is one of `daily`, `weekly`, `monthly`. |
| `fast_mode` | `fast_mode` | not stated in the docs | **[DOC]** field table ("Whether fast mode is enabled for the session"); **[BIN]** the stdin builder always emits it, as a boolean | Only a literal `true` draws anything. |
| `remote` | `remote.session_id` | unknown | **[BIN] only — undocumented.** Added when the session's surface reports a remote attachment (`surfaceCapabilities.remote`, `null` for an ordinary local session); the exact trigger was not traced | Opt-in for that reason. May change or vanish without notice. |

The rest of `prompt_cache` (`requests`, `misses`, `expected_rebuilds`,
`hit_ratio`, `cache_write_tokens`, `miss_recache_tokens`, `miss_causes`) is still
not rendered: the `tokens` segment already shows the per-call cache-hit ratio, and
a second ratio beside it would disagree for good reasons that a one-line segment
cannot explain.

**Every field here can be absent, `null`, or the wrong type** on an older or newer
Claude Code, and a status line script that raises prints nothing at all. Each
formatter (`_fmt_prompt_cache`, `_fmt_spend_limit`, `_fmt_fast_mode`,
`_fmt_remote` in `bin/audio-hooks-statusline.py`) therefore returns `""` on
anything it does not recognise; numbers must be real numbers (a `bool` or a numeric
string is rejected), a boolean `resets_at` is ignored (`True` would otherwise read as
1970-01-01 00:00:01), and a timestamp implausibly far in the future (a millisecond
epoch) drops the countdown rather than showing `1000h`. Displayed percentages are
clamped (to ±9999 for the quota, spend and context numbers; the bars clamp to
0–100), so a malformed `used_percentage` such as `1e308` cannot print a
300-digit number or raise.

**Codex is unaffected.** Codex renders only a fixed list of item IDs; it already
has `fast-mode` and its own rate-limit items, and has nothing equivalent to
`prompt_cache`, `spend_limit` or `remote`. The new segments are Claude Code only.

---

## Subagent status line (Claude Code, v6.5)

`subagentStatusLine` is a **second, separate** settings key from `statusLine`.
It renders one row per task in the agent panel rather than one line for the
session, so with agent teams it is where most of the live state now is.

```
audio-hooks statusline subagent show        # is it registered?
audio-hooks statusline subagent install     # register in ~/.claude/settings.json
audio-hooks statusline subagent uninstall   # remove
```

**The output contract is different from the main status line, and getting it
wrong produces silence rather than an error.** Claude Code pipes one JSON
object on stdin and expects **NDJSON** back — one object per line:

| Direction | Shape |
|---|---|
| stdin | `{"columns": <int>, "tasks": [{"id", "name", "type", "status", "description", "label", "startTime", "model", "effort", "contextWindowSize", "tokenCount", "tokenSamples", "cwd"}, …], …session fields}` |
| stdout | `{"id": "<the task's id>", "content": "<rendered row>"}`, one per line |

Claude Code keys the result by `id`, so a row whose `id` does not match a task
is dropped. A line that is not JSON is logged as *"subagentStatusLine emitted
non-JSON line"*; one that parses but lacks `id`/`content` is *"emitted invalid
schema"*. The timeout is **5 s**, so the renderer does no I/O beyond stdin.

echook's row is deliberately compact — one panel column, not two full lines:

```
▶ · opus-5 · 🧠high · ◔24% 48.0k/200.0k · ⏱3m05s
✓ · haiku-4-5 · ◔1.2k
```

status icon · model · reasoning effort · context used (with window size) ·
elapsed. Every field is data-gated, so a task that reports only a token count
renders only that.

**Why the plugin does not install it for you (v6.7.0).** A plugin-level default
(`settings.json` shipped inside the plugin) was evaluated and not shipped. Claude
Code loads such a file and keeps only `subagentStatusLine`, but does not substitute
`${CLAUDE_PLUGIN_ROOT}` in its command (it arrived empty) and does not put the
plugin's `bin/` on PATH for it (exit 127), so a marketplace-installed plugin cannot
name its own script; and `statusline subagent uninstall` removes only the user's own
setting, so a plugin default would have no CLI-only off switch. Measured on Claude
Code 2.1.288 on Windows in an interactive session; the row was seen to execute, not
seen on screen. The details are in
[EVENT_BEHAVIOR_NOTES.md](EVENT_BEHAVIOR_NOTES.md#a-plugin-level-subagentstatusline-default-evaluated-not-shipped-v670).
Run `audio-hooks statusline subagent install` yourself.

---

## Codex status line (curation only)

Codex accepts only fixed built-in item IDs — echook **curates**, it does **not**
render. Two `[tui]` arrays are curated: `status_line` (the footer bar) and
`terminal_title` (the tab/window title), which share the same item-ID family and
the same "too many redundant items → ellipsis" problem.

```
audio-hooks statusline codex show                                   # current arrays + overflow flag
audio-hooks statusline codex preview --preset balanced              # what would be written (no write)
audio-hooks statusline codex apply   --preset balanced              # write status_line (backs up first)
audio-hooks statusline codex apply   --preset balanced --target both        # status_line + terminal_title
audio-hooks statusline codex apply   --target terminal_title --preset minimal
audio-hooks statusline codex apply   --items model-with-reasoning,git-branch,context-remaining
```

Flags:
- `--target status_line` (default) | `terminal_title` | `both`
- `--preset minimal | balanced | full`
- `--items a,b,c` — exact ordered list (single target only)

Presets:

| Target | minimal | balanced (recommended) | full |
|---|---|---|---|
| `status_line` | 4 items | 8 items | 14 items |
| `terminal_title` | 2 items | 4 items | 6 items |

`apply` always **backs up `config.toml`** to a timestamped `.echook-*.bak`
sibling first, then does a **surgical** edit — only the targeted array (and, if
absent, a `[tui]` header) changes; every other table, comment, and the file's
formatting are preserved. When `tomllib` is available (Python 3.11+) the result
is parse- and round-trip-validated before writing. Restart Codex (or run
`/statusline`) to reload.

**Accepted item IDs** (`--items` rejects anything else with `INVALID_USAGE`).
Verified against the Codex source at `rust-v0.160.1` (`codex-rs/tui/src/bottom_pane/status_line_setup.rs`
and `title_setup.rs`), the latest stable on 2026-10-07; the list was originally
recovered from the 0.143 binary and no 0.143 ID was dropped, so older installs still validate.
- Both targets: `model`, `model-with-reasoning`, `reasoning`, `current-dir`, `project-name`,
  `git-branch`, `run-state`, `approval-mode`, `context-remaining`, `context-used`,
  `five-hour-limit`, `weekly-limit`, `codex-version`, `used-tokens`, `total-input-tokens`,
  `total-output-tokens`, `thread-id`, `fast-mode`, `task-progress`, `thread-title`, `thread-credits` and
  `estimated-thread-cost` (the last two since 0.148, Enterprise only).
- `status_line` only: `pull-request-number`, `branch-changes`, `permissions`,
  `context-window-size`, `raw-output`, `workspace-headline`, `hostname` (new, 0.149-0.152),
  `thread-name` (new, 0.153-0.156; also a title item).
- `terminal_title` only: `activity`, `app-name`.
- Aliases Codex still parses to the same item (accepted by echook; Codex writes the canonical
  name back): `model-name`, `project`, `project-root`, `status`, `approval`, `context-usage`,
  `session-id`, and title-only `thread` and `spinner`. `approval` is an alias of
  `approval-mode`, not a canonical ID.
- An ID valid for only one target is ignored by Codex on the other; echook validates against
  the union and does not check the target. Versions given are where echook first saw the item
  in the Rust source (sampled at 0.143, 0.148, 0.152, 0.156, 0.160.1), not exact releases.

> **Why a Codex item shows nothing:** an item ID with no value is simply not
> drawn (e.g. `git-branch`/`branch-changes` outside a git repo, `five-hour-limit`
> before any rate-limit usage). That is Codex behaviour, not an echook bug — a
> sparse-looking bar usually means a fresh session / non-repo cwd, not missing
> configuration. Use `--preset full` for the maximum number of item IDs; some
> still only fill in once their data exists.

---

## Cursor: investigated, not implemented

**The Cursor IDE has no third-party-writable status bar.** Checked directly
against the installed build (`resources/app/out/vs/workbench/workbench.desktop.main.js`):
every occurrence of `statusLine` is an *internal transcript row kind*
(`kind:"statusLine"`, `rowId:` `status-line:${id}`) used to render rows inside
the agent transcript. There is no settings key, no command contribution, and no
schema accepting a user command. The same bundle *does* contain the hook event
names (`beforeShellExecution`, `afterFileEdit`, …), so this is a genuine absence
rather than a failed search. The upstream request to expose a status-bar API to
extensions is still open.

**`cursor-agent` (the CLI) is a different story, and remains unverified.**
Cursor's CLI changelog describes a custom `statusLine` — *"Point `statusLine` at
your own command and render its output (with live token usage data) in the
prompt footer"* (April 2026), later *"a custom `statusLine` command keeps its
throttle during streaming updates"* (August 2026) — and the mechanism is
explicitly modelled on Claude Code's convention (`type: "command"`, a spawned
script, JSON on stdin), which would make echook's existing 33-segment renderer
largely reusable.

It is not implemented because **`cursor-agent` is not installable on the machine
this was investigated from** (only the IDE is present), so the settings-file
location and the exact stdin schema could not be confirmed first-hand. Given
this project's rule — verify an interface before shipping against it — guessing
here would produce either silence or a wrong-shaped payload, with no diagnostic
either way. A known caveat is already on record: a custom `statusLine` currently
*replaces* the native CLI footer rather than composing with it, so the
Auto-review indicator is lost.

**To pick this up:** install `cursor-agent`, run `/statusline`, capture the
stdin payload with a shim that appends stdin to a file and exits 0, and compare
it against Claude Code's schema in the table above. If it matches, wiring it is
mostly a matter of pointing a second registration at
`bin/audio-hooks-statusline.py`.

---

## Where this lives in the code

- Renderer (Claude Code): `bin/audio-hooks-statusline.py` (canonical) → synced to
  `plugins/audio-hooks/bin/` by `scripts/build-plugin.sh`.
- Catalog + Codex curation + presets + surgical TOML editor: `bin/audio-hooks.py`
  (`STATUSLINE_SEGMENTS`, `CODEX_*_PRESETS`, `_codex_apply_tui_array`, `cmd_statusline`).
- Config defaults: `config/default_preferences.json` (`statusline_settings`).
- Tests: `tests/test_statusline.py`, `tests/test_codex_statusline.py`.
- Deeper internals: [ARCHITECTURE.md](ARCHITECTURE.md). Natural-language phrasings:
  [SKILL.md](../plugins/audio-hooks/skills/audio-hooks/SKILL.md). Key reference:
  [CLI_REFERENCE.md](CLI_REFERENCE.md).
