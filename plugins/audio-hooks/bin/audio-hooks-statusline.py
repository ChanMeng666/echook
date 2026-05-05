#!/usr/bin/env python3
"""audio-hooks-statusline — Claude Code status line script.

Reads the JSON session document Claude Code pipes to stdin and prints up to
two lines to stdout.  Which segments appear is controlled by the user config
key ``statusline_settings.visible_segments`` (an array of segment names).
When the array is empty (default) every segment is shown.

Available segments
------------------
Line 1: model, version, sounds, webhook, theme
Line 2: snooze, focus, branch, api_quota, context

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
import shutil
import subprocess
import sys
import time
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

ALL_SEGMENTS = {"model", "version", "sounds", "webhook", "theme",
                "snooze", "focus", "branch", "api_quota", "context"}
LINE1_SEGMENTS = {"model", "version", "sounds", "webhook", "theme"}
LINE2_SEGMENTS = {"snooze", "focus", "branch", "api_quota", "context"}

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


def _fmt_tokens(n: int) -> str:
    """Render a token count as a compact human string (e.g. 194000 -> 194K)."""
    if n >= 1_000_000:
        if n % 1_000_000 == 0:
            return f"{n // 1_000_000}M"
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n // 1_000}K"
    return str(n)


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

    rate_limits = (session.get("rate_limits") or {}) if isinstance(session.get("rate_limits"), dict) else {}
    git_worktree = (session.get("workspace") or {}).get("git_worktree") if isinstance(session.get("workspace"), dict) else None
    ctx_window = session.get("context_window") or {}

    status = _get_status(session_id)

    # Determine which segments to show
    sl_cfg = (status.get("statusline") or {}) if status else {}
    raw_vis = sl_cfg.get("visible_segments") or []
    visible = _normalise_segments(raw_vis) if raw_vis else ALL_SEGMENTS

    def show(segment: str) -> bool:
        return segment in visible

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
    if show("version"):
        l1_parts.append(f"\U0001f50a echook v{version}")
    if show("sounds"):
        l1_parts.append(f"{enabled_count}/{total_count} Sounds")
    if show("webhook"):
        l1_parts.append(webhook_part)
    if show("theme"):
        l1_parts.append(f"Theme: {theme_label}")

    if visible & LINE1_SEGMENTS:
        print(" | ".join(l1_parts) if len(l1_parts) > 1 else (l1_parts[0] if l1_parts else ""))

    # Line 2: conditional state
    parts = []

    snooze = status.get("snooze") or {}
    if show("snooze") and snooze.get("active"):
        remaining = int(snooze.get("remaining_seconds", 0))
        parts.append(f"{YELLOW}[MUTED {_format_remaining(remaining)}]{RESET}")

    focus = status.get("focus_flow") or {}
    if show("focus") and focus.get("enabled") and focus.get("mode") not in (None, "disabled", ""):
        parts.append(f"{CYAN}[Focus: {focus.get('mode')}]{RESET}")

    if show("branch") and git_worktree:
        parts.append(f"\U0001f33f {git_worktree}")

    if show("api_quota"):
        five_hour = (rate_limits.get("five_hour") or {}) if isinstance(rate_limits, dict) else {}
        used = five_hour.get("used_percentage")
        if used is not None:
            try:
                pct = float(used)
                parts.append(f"{_bar(pct)} API Quota: {int(pct)}%")
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

    if parts:
        print("  ".join(parts))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Never break the user's terminal — degrade silently.
        sys.exit(0)
