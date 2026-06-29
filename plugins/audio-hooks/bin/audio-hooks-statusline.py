#!/usr/bin/env python3
"""audio-hooks-statusline — Claude Code status line script.

Reads the JSON session document Claude Code pipes to stdin and prints up to
two lines to stdout.  Which segments appear is controlled by the user config
key ``statusline_settings.visible_segments`` (an array of segment names).
When the array is empty (default) every segment is shown.

Available segments
------------------
Line 1 (identity / config):
  model, session_name, agent, effort, thinking, vim, output_style, cc_version,
  cwd, repo, version, sounds, webhook, theme
Line 2 (live state / metrics):
  snooze, branch, git_dirty, worktree, pr, added_dirs, api_quota, weekly_quota,
  context, tokens, exceeds_200k, cost, duration, api_time, burn_rate

Every segment maps to a field Claude Code pipes on stdin (see
https://code.claude.com/docs/en/statusline). Most of the richer segments render
*only when their data is present* (e.g. ``pr`` only inside a PR, ``vim`` only in
vim mode, ``output_style`` only when not the default), so the default — show
everything — stays uncluttered for a plain session yet exposes the full picture
when the data exists.

``effort``, ``cc_version`` (Claude Code's own version), ``weekly_quota`` (the
7-day rate-limit window + reset time) and ``cost`` mirror the Claude Code
startup banner so that information stays visible after it scrolls off the top
of the terminal. The subscription plan name ("Claude Max"/"Pro") is *not*
piped to status line scripts, so it is intentionally not shown.

Segment selection
-----------------
Two config keys (under ``statusline_settings``) control which segments appear:
  - ``visible_segments`` — a *whitelist*. When non-empty, only these show
    (back-compat behaviour). Order within a line still follows the canonical
    LINE1/LINE2 order, not the list order.
  - ``hidden_segments`` — a *blacklist*. Applied only when ``visible_segments``
    is empty: every available segment shows except these. This lets a user drop
    a couple of segments from the comprehensive default without having to
    enumerate all the ones they want to keep.

The ``cwd`` segment shows the current working directory as an abbreviated
path (home folder collapsed to ``~``; long paths shortened to
``<root>…<last folder>``) so the user can tell at a glance which project
the session is in.

Example user configuration (via ``audio-hooks set``):
  audio-hooks set statusline_settings.visible_segments '["context"]'
  audio-hooks set statusline_settings.visible_segments '["context","api_quota","branch"]'
  audio-hooks set statusline_settings.visible_segments '[]'   # show all (default)

Context window thresholds (agent-safety):
  GREEN  < 50%  — safe for autonomous agent work
  YELLOW 50-80% — should /compact or /clear ("agent dumb zone" starts ~60%)
  RED    > 80%  — agent performance degrades significantly

Hard rules:
  - No interactive prompts.
  - All errors degrade gracefully (silent fallback to a single line).
  - Output is plain text (with optional ANSI colors) — never JSON.
  - Maximum two lines, no trailing newline noise.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Optional

# ANSI color codes (degrade silently on terminals that don't support them)
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
DIM = "\033[2m"
RESET = "\033[0m"

CACHE_TTL_SEC = 5

# Columns held back from the detected terminal width when packing lines.
# `COLUMNS` reports the *full* terminal width, but the *usable* width is
# smaller: the status line's `padding` setting indents it, most terminals
# reserve the rightmost cell, and emoji-dense rows render a hair wider than
# `_vwidth()` estimates (terminals/Claude Code measure some emoji as wider than
# the 2 cells we assume). Without this slack a row that lands exactly on the
# budget overflows and Claude Code truncates it with an ellipsis (the
# `Theme: Chim…` bug). 8 covers padding + the reserved edge + a couple of cells
# of emoji rounding; users on a narrower-than-reported terminal can still pin it
# exactly via `statusline_settings.max_width`.
WIDTH_SAFETY_MARGIN = 8

# Line 1 — identity / configuration (mostly static within a session).
LINE1_SEGMENTS = ["model", "session_name", "agent", "effort", "thinking", "vim",
                  "output_style", "cc_version", "cwd", "repo", "version", "sounds",
                  "webhook", "theme"]
# Line 2 — live state / metrics (change as the session runs).
LINE2_SEGMENTS = ["snooze", "branch", "git_dirty", "worktree", "pr", "added_dirs",
                  "api_quota", "weekly_quota", "context", "tokens", "exceeds_200k",
                  "cost", "duration", "api_time", "burn_rate"]
# Order is preserved for rendering; the set is used for membership tests.
ALL_SEGMENTS = set(LINE1_SEGMENTS) | set(LINE2_SEGMENTS)

# Backwards compatibility: accept old segment names from existing configs
_SEGMENT_ALIASES = {"hooks": "sounds", "rate_limit": "rate-limit", "ctx": "context"}


def _read_session_input() -> Dict[str, Any]:
    """Read the JSON session document Claude Code pipes to stdin."""
    try:
        raw = sys.stdin.read()
        if raw.strip():
            return json.loads(raw)
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _resolve_audio_hooks_binary() -> Optional[Path]:
    """Find the audio-hooks.py Python entry alongside this script.

    Always prefers the .py file so we can invoke it directly via the
    current Python interpreter (avoiding the bash wrapper which doesn't
    work from a status line subprocess on Windows).
    """
    here = Path(__file__).resolve().parent
    py_entry = here / "audio-hooks.py"
    if py_entry.exists():
        return py_entry
    return None


def _state_dir() -> Path:
    """Resolve a writable state directory for the cache file."""
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        d = Path(plugin_data)
    else:
        explicit = os.environ.get("CLAUDE_AUDIO_HOOKS_DATA")
        if explicit:
            d = Path(explicit)
        else:
            base = os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp"
            d = Path(base) / "claude_audio_hooks_queue"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def _get_status(session_id: str) -> Dict[str, Any]:
    """Return cached `audio-hooks status` JSON, refreshing every CACHE_TTL_SEC."""
    cache_file = _state_dir() / f"statusline.cache.{session_id or 'default'}"
    now = time.time()
    if cache_file.exists():
        try:
            mtime = cache_file.stat().st_mtime
            if now - mtime < CACHE_TTL_SEC:
                return json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    binary = _resolve_audio_hooks_binary()
    if binary is None:
        return {}
    try:
        proc = subprocess.run(
            [sys.executable, str(binary), "status"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return {}
        data = json.loads(proc.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return {}
    try:
        cache_file.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass
    return data


def _format_remaining(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}h{m}m" if m else f"{h}h"


def _format_duration_ms(ms: Any) -> str:
    """Render a millisecond duration as a compact human string (e.g. 45000 ->
    ``45s``, 720000 -> ``12m``). Returns "" on absent/invalid input — must never
    raise. Reuses the ``_format_remaining`` rounding so durations and snooze
    countdowns read identically.
    """
    try:
        seconds = int(float(ms)) // 1000
    except (TypeError, ValueError):
        return ""
    if seconds < 0:
        return ""
    return _format_remaining(seconds)


def _git_dirty(cwd: Optional[str]) -> Optional[int]:
    """Return the number of uncommitted changes in ``cwd``'s git repo.

    ``branch`` already comes from the session JSON (``workspace.git_worktree``);
    the *dirty count* is not piped, so this is the one segment that shells out.
    The result is cached per-cwd for ``CACHE_TTL_SEC`` exactly like
    ``_get_status`` so a fast-refreshing status line doesn't spawn ``git`` on
    every keystroke.

    Returns ``None`` when ``cwd`` is missing, ``git`` is unavailable, or the
    directory is not a repo (that case is cached as ``-1`` so non-repos don't
    re-shell each render). Never raises — the status line degrades silently.
    """
    if not cwd:
        return None
    # hashlib (not the salted built-in hash()) so the cache filename is stable
    # across the separate processes Claude Code spawns per refresh.
    key = hashlib.md5(os.fsencode(cwd)).hexdigest()[:12]
    cache = _state_dir() / f"statusline.git.{key}"
    now = time.time()
    if cache.exists():
        try:
            if now - cache.stat().st_mtime < CACHE_TTL_SEC:
                v = int(cache.read_text(encoding="utf-8").strip())
                return None if v < 0 else v
        except (OSError, ValueError):
            pass
    count = -1
    git = shutil.which("git")
    if git:
        try:
            proc = subprocess.run(
                [git, "-C", cwd, "status", "--porcelain"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=2,
            )
            if proc.returncode == 0:
                count = sum(1 for line in proc.stdout.splitlines() if line.strip())
        except (subprocess.SubprocessError, OSError):
            count = -1
    try:
        cache.write_text(str(count), encoding="utf-8")
    except OSError:
        pass
    return None if count < 0 else count


def _fmt_reset_clock(epoch: Any) -> str:
    """Render a rate-limit reset moment as a local clock time, banner-style.

    Claude Code pipes ``rate_limits.*.resets_at`` as Unix epoch seconds. The
    startup banner shows the reset as a wall-clock time ("resets 9pm"); we
    mirror that — local 12-hour time, lowercase am/pm, a bare ``:00`` stripped
    so ``21:00`` reads as ``9pm`` but ``21:30`` reads as ``9:30pm``.

    Returns "" on absent/invalid input. Must never raise — the status line
    degrades silently on a surprising value.
    """
    try:
        ts = int(float(epoch))
        if ts <= 0:
            return ""
        lt = time.localtime(ts)
        hour12 = lt.tm_hour % 12 or 12
        ampm = "am" if lt.tm_hour < 12 else "pm"
        if lt.tm_min:
            return f"{hour12}:{lt.tm_min:02d}{ampm}"
        return f"{hour12}{ampm}"
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _fmt_tokens(n: int) -> str:
    """Render a token count as a compact human string (e.g. 194000 -> 194K)."""
    if n >= 1_000_000:
        if n % 1_000_000 == 0:
            return f"{n // 1_000_000}M"
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n // 1_000}K"
    return str(n)


def _abbrev_path(cwd: str, max_len: int = 40) -> str:
    """Render a working-directory path compactly for the status line.

    - Collapse the home directory prefix to ``~`` (case-insensitive compare
      via ``os.path.normcase`` so it also works on Windows).
    - If the result is short enough, return it unchanged.
    - Otherwise keep the first segment (a drive like ``D:`` or ``~``) plus an
      ellipsis plus the last folder name, e.g. ``D:\\…\\claude-code-audio-hooks``
      or ``~/…/echook``. If even that is too long, fall back to ``…<sep><last>``.

    Any unexpected input degrades to the original string — the status line
    must never crash on a surprising ``cwd``.
    """
    try:
        display = cwd
        home = os.path.expanduser("~")
        if home and os.path.normcase(cwd).startswith(os.path.normcase(home)):
            display = "~" + cwd[len(home):]
        if len(display) <= max_len:
            return display
        sep = "\\" if "\\" in display else "/"
        parts = [seg for seg in display.split(sep) if seg]
        if not parts:
            return display
        head, tail = parts[0], parts[-1]
        candidate = f"{head}{sep}…{sep}{tail}" if len(parts) > 1 else tail
        if len(candidate) > max_len:
            return f"…{sep}{tail}"
        return candidate
    except (TypeError, ValueError, AttributeError):
        return cwd


def _maybe_dump_session(session: Dict[str, Any]) -> None:
    """When CLAUDE_HOOKS_DEBUG is enabled, persist the latest session JSON for
    inspection (used to diagnose status line input — e.g. context_window_size
    after a /model switch).

    Privacy note: the session payload contains workspace paths, transcript
    location, and possibly the last assistant message. The file lives at
    ``${state_dir}/statusline.last_input.json`` and is overwritten on each
    invocation. Disable by unsetting the env var.

    Truthy values match the hook_runner convention (``1``/``true``/``yes``,
    case-insensitive). Atomic rename avoids leaving a half-written file when
    a second invocation races the first. Failures are swallowed — diagnostics
    must never break status line rendering.
    """
    if os.environ.get("CLAUDE_HOOKS_DEBUG", "").lower() not in ("1", "true", "yes"):
        return
    try:
        d = _state_dir()
        target = d / "statusline.last_input.json"
        tmp = d / f"statusline.last_input.json.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
    except (OSError, TypeError, ValueError):
        pass


def _bar(percent: float, width: int = 8) -> str:
    """Render a unicode progress bar with rate-limit color thresholds."""
    pct = max(0, min(100, int(percent)))
    filled = pct * width // 100
    empty = width - filled
    if pct >= 90:
        color = RED
    elif pct >= 70:
        color = YELLOW
    else:
        color = GREEN
    return f"{color}{'█' * filled}{DIM}{'░' * empty}{RESET}"


def _ctx_bar(percent: float, width: int = 8) -> str:
    """Render a context-window progress bar with agent-safety thresholds.

    Thresholds differ from rate-limit bar:
      GREEN  < 50%   — safe for autonomous agent work
      YELLOW 50-80%  — should /compact or /clear
      RED    > 80%   — agent performance degrades significantly
    """
    pct = max(0, min(100, int(percent)))
    filled = pct * width // 100
    empty = width - filled
    if pct > 80:
        color = RED
    elif pct >= 50:
        color = YELLOW
    else:
        color = GREEN
    return f"{color}{'█' * filled}{DIM}{'░' * empty}{RESET}"


def _normalise_segments(raw: list) -> set:
    """Turn the user's visible_segments list into a set of canonical names.

    Accepts old names (ctx, hooks, rate_limit) for backwards compatibility.
    """
    out = set()
    for s in raw:
        canonical = _SEGMENT_ALIASES.get(s, s)
        if canonical in ALL_SEGMENTS:
            out.add(canonical)
    return out


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _vwidth(text: str) -> int:
    """Return the visible column width of a rendered segment.

    The status line mixes ANSI color escapes (zero width), emoji and other
    wide glyphs (two cells in virtually every terminal), variation selectors
    and combining marks (zero width), and ordinary characters (one cell).
    We need the *visible* width — not ``len()`` — to pack segments into lines
    that fit the terminal without Claude Code truncating them with an ellipsis.

    The estimate errs toward treating symbols/emoji as wide so we wrap a touch
    early rather than overflow. It must never raise.
    """
    try:
        s = _ANSI_RE.sub("", text)
        w = 0
        for ch in s:
            o = ord(ch)
            # Zero-width: combining marks, variation selectors, other format chars.
            if o in (0xFE0E, 0xFE0F) or unicodedata.combining(ch) or \
                    unicodedata.category(ch) in ("Mn", "Me", "Cf"):
                continue
            # Wide: CJK (W/F) plus the emoji/symbol planes we actually emit
            # (🧠 ⚡ 📁 🔊 💲 🌿 🛑 ⚠). Box-drawing █/░ are East-Asian
            # "Ambiguous" → one cell, which is how terminals render them here.
            if unicodedata.east_asian_width(ch) in ("W", "F") or \
                    0x1F300 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF or \
                    0x2B00 <= o <= 0x2BFF or 0x1F000 <= o <= 0x1F2FF:
                w += 2
            else:
                w += 1
        return w
    except (TypeError, ValueError):
        return len(text)


def _terminal_width(status: Dict[str, Any]) -> int:
    """Resolve the terminal width to pack against.

    Priority: an explicit ``statusline_settings.max_width`` override (also the
    deterministic hook for tests) → the ``COLUMNS`` env var that Claude Code
    sets to the real terminal width before each run (v2.1.153+; read via
    ``shutil.get_terminal_size`` which checks ``COLUMNS`` first) → a safe 80.
    A piped stdout means ``os.get_terminal_size`` can't probe directly, which
    is exactly why Claude Code exposes ``COLUMNS``.
    """
    try:
        mw = int(((status or {}).get("statusline") or {}).get("max_width") or 0)
        if mw > 0:
            return mw
    except (TypeError, ValueError, AttributeError):
        pass
    try:
        cols = shutil.get_terminal_size(fallback=(80, 24)).columns
        return cols if isinstance(cols, int) and cols > 0 else 80
    except (OSError, ValueError):
        return 80


def _pack_lines(parts: list, joiner: str, width: int) -> list:
    """Greedily pack rendered segments into physical lines no wider than
    ``width`` visible columns, wrapping only at segment boundaries so a segment
    is never split mid-way. A lone segment wider than ``width`` still gets its
    own line — better than sharing a line that Claude Code would then truncate.
    """
    lines: list = []
    cur: list = []
    cur_w = 0
    jw = _vwidth(joiner)
    for p in parts:
        pw = _vwidth(p)
        if not cur:
            cur, cur_w = [p], pw
        elif cur_w + jw + pw <= width:
            cur.append(p)
            cur_w += jw + pw
        else:
            lines.append(joiner.join(cur))
            cur, cur_w = [p], pw
    if cur:
        lines.append(joiner.join(cur))
    return lines


def _force_utf8_stdout() -> None:
    """Force stdout to UTF-8 with replace-on-error so Unicode output (▌█░🛑⚠️
    plus ANSI escapes) never raises UnicodeEncodeError on terminals or
    captured pipes that default to a legacy codepage (cp1252 on Windows
    GitHub Actions runners is the canonical example).

    Without this, an UnicodeEncodeError raised by ``print()`` is caught by
    the outer ``try/except Exception`` and the script exits 0 with empty
    stdout — silently breaking the status line.

    ``reconfigure`` is available since Python 3.7. If for any reason it
    fails, we degrade silently — the worst case is the pre-fix behaviour.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, OSError, ValueError):
        pass


def main() -> int:
    _force_utf8_stdout()
    session = _read_session_input()
    _maybe_dump_session(session)
    session_id = str(session.get("session_id") or "default")
    model = (session.get("model") or {}).get("display_name", "Claude")
    # Reasoning effort (only present on models that support it) and Claude
    # Code's own version — both straight from the stdin session, distinct from
    # echook's `status["version"]` shown by the `version` segment.
    effort = (session.get("effort") or {}).get("level") if isinstance(session.get("effort"), dict) else None
    cc_version = session.get("version")

    rate_limits = (session.get("rate_limits") or {}) if isinstance(session.get("rate_limits"), dict) else {}
    workspace = session.get("workspace") if isinstance(session.get("workspace"), dict) else {}
    git_worktree = workspace.get("git_worktree")
    ctx_window = session.get("context_window") or {}
    cost = (session.get("cost") or {}) if isinstance(session.get("cost"), dict) else {}

    # Richer optional session fields (see code.claude.com/docs/en/statusline).
    # Each is guarded so a missing or wrong-typed value simply omits its segment.
    def _dict(name: str) -> Dict[str, Any]:
        v = session.get(name)
        return v if isinstance(v, dict) else {}

    session_name = session.get("session_name") if isinstance(session.get("session_name"), str) else None
    agent_name = _dict("agent").get("name")
    thinking_on = bool(_dict("thinking").get("enabled"))
    vim_mode = _dict("vim").get("mode")
    output_style = _dict("output_style").get("name")
    repo = workspace.get("repo") if isinstance(workspace.get("repo"), dict) else {}
    added_dirs = workspace.get("added_dirs") if isinstance(workspace.get("added_dirs"), list) else []
    pr = _dict("pr")
    worktree = _dict("worktree")
    exceeds_200k = bool(session.get("exceeds_200k_tokens"))

    # Current working directory: prefer the top-level `cwd` Claude Code pipes
    # in, falling back to workspace.current_dir / project_dir.
    cwd = session.get("cwd")
    if not (isinstance(cwd, str) and cwd):
        cwd = workspace.get("current_dir") or workspace.get("project_dir")
    cwd = cwd if isinstance(cwd, str) and cwd else None

    status = _get_status(session_id)

    # Determine which segments to show. `visible_segments` is a whitelist
    # (back-compat: when set, only those show). When it is empty the default is
    # "everything", minus any `hidden_segments` blacklist — so a user can drop a
    # couple of segments without enumerating all the ones they want to keep.
    sl_cfg = (status.get("statusline") or {}) if status else {}
    raw_vis = sl_cfg.get("visible_segments") or []
    if raw_vis:
        visible = _normalise_segments(raw_vis)
    else:
        hidden = _normalise_segments(sl_cfg.get("hidden_segments") or [])
        visible = ALL_SEGMENTS - hidden

    def show(segment: str) -> bool:
        return segment in visible

    # Width budget for reflow: hold back a safety margin under the detected
    # terminal width so a packed row never brushes the usable edge (padding +
    # reserved cell) and gets an ellipsis from Claude Code.
    budget = max(20, _terminal_width(status) - WIDTH_SAFETY_MARGIN)

    # Line 1: model + project header
    if not status:
        print(f"{CYAN}[{model}]{RESET} {DIM}echook (status unavailable){RESET}")
        return 0

    version = status.get("version", "?")
    enabled_count = status.get("enabled_hook_count", 0)
    total_count = status.get("total_hook_count", 0)
    theme_raw = status.get("theme", "default")
    theme_label = "Voice" if theme_raw == "default" else "Chimes" if theme_raw == "custom" else theme_raw
    webhook = status.get("webhook") or {}
    if webhook.get("enabled"):
        webhook_part = f"Webhook: {webhook.get('format', 'raw')}"
    else:
        webhook_part = f"{DIM}Webhook: off{RESET}"

    # Build Line 1 from visible segments
    l1_parts = []
    if show("model"):
        l1_parts.append(f"{CYAN}[{model}]{RESET}")
    if show("session_name") and session_name:
        l1_parts.append(f"\U0001f3f7 {session_name}")
    if show("agent") and isinstance(agent_name, str) and agent_name:
        l1_parts.append(f"\U0001f916 {agent_name}")
    if show("effort") and effort:
        l1_parts.append(f"\U0001f9e0 {effort}")
    if show("thinking") and thinking_on:
        l1_parts.append("\U0001f4ad thinking")
    if show("vim") and isinstance(vim_mode, str) and vim_mode:
        l1_parts.append(f"vim:{vim_mode}")
    if show("output_style") and isinstance(output_style, str) and output_style and output_style != "default":
        l1_parts.append(f"\U0001f3a8 {output_style}")
    if show("cc_version") and cc_version:
        l1_parts.append(f"⚡ CC v{cc_version}")
    if show("cwd") and cwd:
        l1_parts.append(f"\U0001f4c1 {_abbrev_path(cwd)}")
    if show("repo") and repo.get("owner") and repo.get("name"):
        l1_parts.append(f"{repo.get('owner')}/{repo.get('name')}")
    if show("version"):
        l1_parts.append(f"\U0001f50a echook v{version}")
    if show("sounds"):
        l1_parts.append(f"{enabled_count}/{total_count} Sounds")
    if show("webhook"):
        l1_parts.append(webhook_part)
    if show("theme"):
        l1_parts.append(f"Theme: {theme_label}")

    # Reflow Line 1 into as many physical rows as the terminal width needs so
    # every segment shows in full (no Claude Code truncation / ellipsis).
    if l1_parts:
        for line in _pack_lines(l1_parts, " | ", budget):
            print(line)

    # Line 2: conditional state
    parts = []

    snooze = status.get("snooze") or {}
    if show("snooze") and snooze.get("active"):
        remaining = int(snooze.get("remaining_seconds", 0))
        parts.append(f"{YELLOW}[MUTED {_format_remaining(remaining)}]{RESET}")

    if show("branch") and git_worktree:
        parts.append(f"\U0001f33f {git_worktree}")

    if show("git_dirty") and cwd:
        dirty = _git_dirty(cwd)
        if dirty is not None:
            if dirty:
                parts.append(f"{YELLOW}±{dirty}{RESET}")
            else:
                parts.append(f"{GREEN}✓ clean{RESET}")

    if show("worktree") and (worktree.get("name") or worktree.get("branch")):
        parts.append(f"\U0001f333 {worktree.get('name') or worktree.get('branch')}")

    if show("pr") and pr.get("number"):
        state = pr.get("review_state")
        state_str = f" ({state})" if isinstance(state, str) and state else ""
        parts.append(f"PR #{pr.get('number')}{state_str}")

    if show("added_dirs") and added_dirs:
        parts.append(f"+{len(added_dirs)} dirs")

    if show("api_quota"):
        five_hour = (rate_limits.get("five_hour") or {}) if isinstance(rate_limits, dict) else {}
        used = five_hour.get("used_percentage")
        if used is not None:
            try:
                pct = float(used)
                resets = _fmt_reset_clock(five_hour.get("resets_at"))
                reset_str = f" · resets {resets}" if resets else ""
                parts.append(f"{_bar(pct)} API Quota: {int(pct)}%{reset_str}")
            except (TypeError, ValueError):
                pass

    if show("weekly_quota"):
        # The headline "You've used 82% of your weekly limit · resets 9pm"
        # banner item — Claude Code's 7-day rate-limit window. Only present
        # for Claude.ai subscribers; silently omitted otherwise.
        seven_day = (rate_limits.get("seven_day") or {}) if isinstance(rate_limits, dict) else {}
        used = seven_day.get("used_percentage")
        if used is not None:
            try:
                pct = float(used)
                resets = _fmt_reset_clock(seven_day.get("resets_at"))
                reset_str = f" · resets {resets}" if resets else ""
                parts.append(f"{_bar(pct)} Weekly: {int(pct)}%{reset_str}")
            except (TypeError, ValueError):
                pass

    if show("context"):
        ctx_used = ctx_window.get("used_percentage")
        if ctx_used is not None:
            try:
                ctx_pct = float(ctx_used)
                hint = ""
                if ctx_pct > 80:
                    hint = f" {RED}\U0001f6d1 /compact{RESET}"
                elif ctx_pct >= 50:
                    hint = f" {YELLOW}\u26a0\ufe0f /compact{RESET}"
                # Surface the window size so a surprising percentage (e.g. 97%
                # after a /model switch from a 1M-context variant to a 200K
                # window) shows what it is a percentage *of*. Derive the
                # numerator from used_percentage × window_size — Claude Code's
                # `total_input_tokens` field counts only literal input, not
                # cache_read/cache_creation, so it understates real usage in
                # cache-heavy sessions like Claude Code itself.
                window_size = ctx_window.get("context_window_size")
                tokens_str = ""
                if isinstance(window_size, (int, float)) and window_size > 0:
                    used_tokens = int(round(ctx_pct * window_size / 100.0))
                    tokens_str = f" ({_fmt_tokens(used_tokens)}/{_fmt_tokens(int(window_size))})"
                parts.append(f"{_ctx_bar(ctx_pct)} Context: {int(ctx_pct)}%{tokens_str}{hint}")
            except (TypeError, ValueError):
                pass

    if show("tokens"):
        # Cache-hit ratio from the last API call: cache_read ÷ total input.
        # A high ratio means the session is reading mostly from cache (cheap);
        # a low ratio means fresh input is being re-sent. Complements `context`.
        usage = ctx_window.get("current_usage") if isinstance(ctx_window, dict) else None
        if isinstance(usage, dict):
            try:
                cache_read = int(usage.get("cache_read_input_tokens") or 0)
                fresh = int(usage.get("input_tokens") or 0)
                cache_create = int(usage.get("cache_creation_input_tokens") or 0)
                total_in = cache_read + fresh + cache_create
                if total_in > 0:
                    parts.append(f"cache {int(round(cache_read * 100.0 / total_in))}%")
            except (TypeError, ValueError):
                pass

    if show("exceeds_200k") and exceeds_200k:
        parts.append(f"{YELLOW}⚠ >200K{RESET}")

    if show("cost"):
        usd = cost.get("total_cost_usd")
        if usd is not None:
            try:
                added = int(cost.get("total_lines_added") or 0)
                removed = int(cost.get("total_lines_removed") or 0)
                diff = f" {GREEN}+{added}{RESET}/{RED}-{removed}{RESET}" if (added or removed) else ""
                parts.append(f"\U0001f4b2 ${float(usd):.2f}{diff}")
            except (TypeError, ValueError):
                pass

    # Wall-clock session duration (from cost.total_duration_ms).
    if show("duration"):
        dur = _format_duration_ms(cost.get("total_duration_ms"))
        if dur:
            parts.append(f"\U0001f550 {dur}")

    # Share of wall-clock spent waiting on the model API.
    if show("api_time"):
        try:
            wall = float(cost.get("total_duration_ms") or 0)
            api = float(cost.get("total_api_duration_ms") or 0)
            if wall > 0 and api > 0:
                parts.append(f"API {int(round(api * 100.0 / wall))}%")
        except (TypeError, ValueError):
            pass

    # Cost velocity ($/hour) — only meaningful once the session has run a bit.
    if show("burn_rate"):
        try:
            usd = float(cost.get("total_cost_usd") or 0)
            wall_ms = float(cost.get("total_duration_ms") or 0)
            if usd > 0 and wall_ms >= 60000:
                rate = usd / (wall_ms / 3_600_000.0)
                parts.append(f"${rate:.2f}/h")
        except (TypeError, ValueError):
            pass

    # Reflow Line 2 the same way — wrap at segment boundaries to fit the width.
    if parts:
        for line in _pack_lines(parts, "  ", budget):
            print(line)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Never break the user's terminal — degrade silently.
        sys.exit(0)
