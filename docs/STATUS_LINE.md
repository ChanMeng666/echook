# Status Line — complete reference

> Authoritative orientation for echook's **second track**, the status line. The
> *live* source of truth is always the CLI: `audio-hooks statusline segments`
> (Claude Code catalog) and `audio-hooks statusline codex show` (Codex state).
> This page explains the model behind those commands. Current as of **v6.3.2**.

## The one thing to understand first

The two editors expose the status line in **fundamentally different** ways, and
echook treats them differently:

| Editor | Mechanism | What echook does |
|---|---|---|
| **Claude Code** | Runs a **command script** and prints whatever it returns (`bin/audio-hooks-statusline.py`, registered in `~/.claude/settings.json`). | **Renders** the whole line. echook can show any segment it wants. |
| **Codex** | Renders only a **fixed list of built-in item IDs** under `[tui].status_line` / `[tui].terminal_title` in `~/.codex/config.toml`. No command/script hook (open feature request [openai/codex#20140](https://github.com/openai/codex/issues/20140)). | **Curates** that fixed list. echook *cannot* render custom text or new segments in Codex — it can only pick/order/de-duplicate the built-in IDs so the line stops truncating. |
| **Cursor** | No status-line surface echook targets. | — |

If you remember nothing else: **Claude Code = render (rich, 29 segments); Codex = curate (fixed menu).**

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

### Segment catalog (29)

Every segment maps to a field Claude Code pipes to the script on stdin
(see <https://code.claude.com/docs/en/statusline>). **data-gated** segments
render only when their field is present, so a plain session stays uncluttered
while a rich one shows the full picture.

**Line 1 — identity / configuration**

| Segment | When | Source | Shows |
|---|---|---|---|
| `model` | always | `model.display_name` | Active model display name |
| `session_name` | data-gated | `session_name` | Custom session name set via `--name` or `/rename` |
| `agent` | data-gated | `agent.name` | Agent name when running with `--agent` |
| `effort` | data-gated | `effort.level` | Reasoning effort (low/medium/high/xhigh/max) |
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
| `context` | data-gated | `context_window` | Context-window usage % + token counts |
| `tokens` | data-gated | `context_window.current_usage` | Cache-hit ratio (cache reads ÷ input) |
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

Two config keys under `statusline_settings` (set via `audio-hooks set`):

- **`visible_segments`** — *whitelist*. When non-empty, **only** these show.
- **`hidden_segments`** — *blacklist*. Applied only when `visible_segments` is
  empty: show everything **except** these. Use this to drop a couple of segments
  from the comprehensive default without enumerating all the keepers.

```bash
# Show only the two progress bars:
audio-hooks set statusline_settings.visible_segments '["context","api_quota"]'
# Keep the rich default but drop two metrics:
audio-hooks set statusline_settings.hidden_segments '["burn_rate","api_time"]'
# Back to the full default:
audio-hooks set statusline_settings.visible_segments '[]'
```

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

> **Why a Codex item shows nothing:** an item ID with no value is simply not
> drawn (e.g. `git-branch`/`branch-changes` outside a git repo, `five-hour-limit`
> before any rate-limit usage). That is Codex behaviour, not an echook bug — a
> sparse-looking bar usually means a fresh session / non-repo cwd, not missing
> configuration. Use `--preset full` for the maximum number of item IDs; some
> still only fill in once their data exists.

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
