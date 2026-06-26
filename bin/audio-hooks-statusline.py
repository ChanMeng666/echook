#!/usr/bin/env python3
"""audio-hooks-statusline — Claude Code status line script.

Reads the JSON session document Claude Code pipes to stdin and prints up to
two lines to stdout.  Which segments appear is controlled by the user config
key ``statusline_settings.visible_segments`` (an array of segment names).
When the array is empty (default) every segment is shown.

Available segments
------------------
Line 1: model, effort, cc_version, cwd, version, sounds, webhook, theme
Line 2: snooze, branch, api_quota, weekly_quota, context, cost

``effort``, ``cc_version`` (Claude Code's own version), ``weekly_quota`` (the
7-day rate-limit window + reset time) and ``cost`` mirror the Claude Code
startup banner so that information stays visible after it scrolls off the top
of the terminal. The subscription plan name ("Claude Max"/"Pro") is *not*
piped to status line scripts, so it is intentionally not shown.

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
# smaller: the status line's `padding` setting indents it and most terminals
# reserve the rightmost cell. Without this slack the packer overfills the last
# row and Claude Code truncates it with an ellipsis. 4 covers padding ≤ 1 plus
# the edge with room to spare; users on a narrower-than-reported terminal can
# pin it exactly via `statusline_settings.max_width`.
WIDTH_SAFETY_MARGIN = 4

ALL_SEGMENTS = {"model", "effort", "cc_version", "cwd", "version", "sounds",
                "webhook", "theme",
                "snooze", "branch", "api_quota", "weekly_quota", "context", "cost"}
LINE1_SEGMENTS = {"model", "effort", "cc_version", "cwd", "version", "sounds",
                  "webhook", "theme"}
LINE2_SEGMENTS = {"snooze", "branch", "api_quota", "weekly_quota", "context", "cost"}

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
    git_worktree = (session.get("workspace") or {}).get("git_worktree") if isinstance(session.get("workspace"), dict) else None
    ctx_window = session.get("context_window") or {}
    cost = (session.get("cost") or {}) if isinstance(session.get("cost"), dict) else {}

    # Current working directory: prefer the top-level `cwd` Claude Code pipes
    # in, falling back to workspace.current_dir / project_dir.
    cwd = session.get("cwd")
    if not (isinstance(cwd, str) and cwd):
        ws = session.get("workspace") if isinstance(session.get("workspace"), dict) else {}
        cwd = ws.get("current_dir") or ws.get("project_dir")
    cwd = cwd if isinstance(cwd, str) and cwd else None

    status = _get_status(session_id)

    # Determine which segments to show
    sl_cfg = (status.get("statusline") or {}) if status else {}
    raw_vis = sl_cfg.get("visible_segments") or []
    visible = _normalise_segments(raw_vis) if raw_vis else ALL_SEGMENTS

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
    if show("effort") and effort:
        l1_parts.append(f"\U0001f9e0 {effort}")
    if show("cc_version") and cc_version:
        l1_parts.append(f"⚡ CC v{cc_version}")
    if show("cwd") and cwd:
        l1_parts.append(f"\U0001f4c1 {_abbrev_path(cwd)}")
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
