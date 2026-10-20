"""The status line: `audio-hooks statusline …` for Claude Code, Codex and subagents.

Holds the segment catalog the Claude Code renderer publishes, the Codex
``[tui]`` item presets and the text-level TOML edit that applies them, and the
``statusLine`` / ``subagentStatusLine`` registration in settings.json.
"""

from __future__ import annotations

import json
import platform
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .codex_config import _codex_home
from .output import _check_args, emit, emit_error

# ---------------------------------------------------------------------------
# Status line catalog (Claude Code) + Codex curation
# ---------------------------------------------------------------------------

# Every segment the Claude Code status line script (bin/audio-hooks-statusline.py)
# can render, with the stdin field it reads. `conditional` segments render only
# when their data is present. Keep this in lock-step with the script's
# LINE1_SEGMENTS / LINE2_SEGMENTS so `statusline segments` is authoritative.
STATUSLINE_SEGMENTS: List[Dict[str, Any]] = [
    {"name": "model", "line": 1, "source": "model.display_name", "conditional": False, "description": "Active model display name", "default": True},
    {"name": "session_name", "line": 1, "source": "session_name", "conditional": True, "description": "Custom session name set via --name or /rename", "default": True},
    {"name": "agent", "line": 1, "source": "agent.name", "conditional": True, "description": "Agent name when running with --agent", "default": True},
    {"name": "remote", "line": 1, "source": "remote.session_id", "conditional": True, "description": "Remote/cloud-session indicator (undocumented upstream field). Opt-in: add to statusline_settings.extra_segments", "default": False},
    {"name": "effort", "line": 1, "source": "effort.level", "conditional": True, "description": "Reasoning effort (low/medium/high/xhigh/max)", "default": True},
    {"name": "fast_mode", "line": 1, "source": "fast_mode", "conditional": True, "description": "Fast-mode indicator; drawn only while fast mode is on", "default": True},
    {"name": "thinking", "line": 1, "source": "thinking.enabled", "conditional": True, "description": "Shown when extended thinking is enabled", "default": True},
    {"name": "vim", "line": 1, "source": "vim.mode", "conditional": True, "description": "Vim editing mode (when vim mode is on)", "default": True},
    {"name": "output_style", "line": 1, "source": "output_style.name", "conditional": True, "description": "Active output style (hidden when 'default')", "default": True},
    {"name": "cc_version", "line": 1, "source": "version", "conditional": True, "description": "Claude Code's own version", "default": True},
    {"name": "cwd", "line": 1, "source": "cwd", "conditional": True, "description": "Working directory (abbreviated)", "default": True},
    {"name": "repo", "line": 1, "source": "workspace.repo", "conditional": True, "description": "Git remote owner/name", "default": True},
    {"name": "version", "line": 1, "source": "audio-hooks status", "conditional": False, "description": "echook version", "default": True},
    {"name": "sounds", "line": 1, "source": "audio-hooks status", "conditional": False, "description": "Enabled / total sound hooks", "default": True},
    {"name": "webhook", "line": 1, "source": "audio-hooks status", "conditional": False, "description": "Webhook on/off + format", "default": True},
    {"name": "theme", "line": 1, "source": "audio-hooks status", "conditional": False, "description": "Audio theme (Voice/Chimes)", "default": True},
    {"name": "snooze", "line": 2, "source": "audio-hooks status", "conditional": True, "description": "Mute countdown when snoozed", "default": True},
    {"name": "branch", "line": 2, "source": "workspace.git_worktree", "conditional": True, "description": "Git branch / worktree", "default": True},
    {"name": "git_dirty", "line": 2, "source": "git status --porcelain", "conditional": True, "description": "Uncommitted-change count (shells out to git; cached)", "default": True},
    {"name": "worktree", "line": 2, "source": "worktree.name", "conditional": True, "description": "Managed worktree name", "default": True},
    {"name": "pr", "line": 2, "source": "pr.number", "conditional": True, "description": "Pull request number + review state", "default": True},
    {"name": "added_dirs", "line": 2, "source": "workspace.added_dirs", "conditional": True, "description": "Count of /add-dir directories", "default": True},
    {"name": "api_quota", "line": 2, "source": "rate_limits.five_hour", "conditional": True, "description": "5-hour rate-limit usage + reset clock (date shown if not today)", "default": True},
    {"name": "weekly_quota", "line": 2, "source": "rate_limits.seven_day", "conditional": True, "description": "7-day rate-limit usage + reset clock (date + time, e.g. 'Jul 4 5am')", "default": True},
    {"name": "spend_limit", "line": 2, "source": "rate_limits.spend_limit", "conditional": True, "description": "Claude apps gateway spend limit: usage %, $used/$limit + period when sent, reset clock", "default": True},
    {"name": "context", "line": 2, "source": "context_window", "conditional": True, "description": "Context-window usage % + token counts", "default": True},
    {"name": "tokens", "line": 2, "source": "context_window.current_usage", "conditional": True, "description": "Cache-hit ratio (cache reads ÷ input)", "default": True},
    {"name": "prompt_cache", "line": 2, "source": "prompt_cache", "conditional": True, "description": "Prompt-cache state: warm + time to expiry, or cold; the cause of a recent miss. Opt-in: add to statusline_settings.extra_segments", "default": False},
    {"name": "exceeds_200k", "line": 2, "source": "exceeds_200k_tokens", "conditional": True, "description": "Warning flag when tokens exceed 200K", "default": True},
    {"name": "cost", "line": 2, "source": "cost.total_cost_usd", "conditional": True, "description": "Session cost + lines added/removed", "default": True},
    {"name": "duration", "line": 2, "source": "cost.total_duration_ms", "conditional": True, "description": "Wall-clock session duration", "default": True},
    {"name": "api_time", "line": 2, "source": "cost.total_api_duration_ms", "conditional": True, "description": "Share of wall-clock spent waiting on the API", "default": True},
    {"name": "burn_rate", "line": 2, "source": "derived", "conditional": True, "description": "Cost velocity ($/hour)", "default": True},
]

# Codex's status line is NOT command-backed: it accepts only a fixed, ordered
# list of built-in item IDs under [tui].status_line in config.toml (command
# rendering is open feature request openai/codex#17827). echook can therefore
# only *curate* that list. These presets are de-duplicated and ordered to fit
# Codex's single rendered line so it no longer truncates with an ellipsis.
CODEX_STATUSLINE_PRESETS: Dict[str, List[str]] = {
    "minimal": [
        "model-with-reasoning", "git-branch", "approval-mode", "context-remaining",
    ],
    "balanced": [
        "model-with-reasoning", "git-branch", "branch-changes", "approval-mode",
        "context-remaining", "five-hour-limit", "weekly-limit", "codex-version",
    ],
    "full": [
        # v6.5 additions: thread-title (session identity -- `full` had none),
        # fast-mode (the /fast toggle is user-visible state nothing surfaced),
        # context-used (echook's Claude Code line is context-centric; Codex only
        # had the remaining half), and the two 0.148 Enterprise items. Items with
        # no value are simply not drawn by Codex, so the Enterprise-only pair is
        # harmless on personal plans rather than an empty slot.
        "model-with-reasoning", "thread-title", "project-name", "git-branch",
        "branch-changes", "pull-request-number", "run-state", "approval-mode",
        "context-remaining", "context-used", "used-tokens", "context-window-size",
        "five-hour-limit", "weekly-limit", "fast-mode", "codex-version",
        "task-progress", "thread-credits", "estimated-thread-cost",
    ],
}

# Every item ID (and strum alias) Codex accepts, verified against the Rust
# source at rust-v0.160.1 (latest stable on 2026-10-07); the 0.143-era IDs stay
# so older installs still validate. echook curates from this set; it cannot render
# custom text (upstream FR openai/codex#17827 is still open). Kept as data so
# `statusline codex --items` can reject a typo instead of writing a silent
# no-op into config.toml.
CODEX_KNOWN_STATUSLINE_ITEMS = frozenset({
    "model", "model-with-reasoning", "reasoning", "current-dir", "project-name",
    "git-branch", "pull-request-number", "branch-changes", "run-state",
    "approval", "approval-mode", "context-remaining", "context-used",
    "five-hour-limit", "weekly-limit", "codex-version", "context-window-size",
    "used-tokens", "total-input-tokens", "total-output-tokens", "thread-id",
    "fast-mode", "raw-output", "thread-title", "workspace-headline",
    "task-progress",
    # 0.148.0, Enterprise workspaces only (#38282).
    "thread-credits", "estimated-thread-cost",
    # Verified against rust-v0.160.1 (status_line_setup.rs + title_setup.rs).
    # "permissions" is in the 0.143 enum too (earlier revisions of this list
    # missed it). "hostname" arrived in 0.149-0.152 and "thread-name" in
    # 0.153-0.156 (status line; thread-name is in the title enum as well), so
    # an older Codex silently ignores them. "thread-title" now falls back to
    # the thread id when unnamed; "thread-name" is omitted when unnamed.
    "permissions", "hostname", "thread-name",
    # terminal_title only (not in the status_line enum).
    "activity", "app-name",
    # Legacy / alias spellings Codex 0.160.1 still parses to the same item
    # (strum `serialize =`). `approval` is accepted in BOTH enums as an alias of
    # approval-mode (it is not a canonical ID). Codex writes the canonical name
    # back, so prefer that in new config.
    "model-name", "project", "project-root", "status", "context-usage",
    "session-id",
    # terminal_title only aliases: `thread` -> thread-title, `spinner` -> activity.
    "thread", "spinner",
})

# The terminal title (tab/window title) shares the same item-ID family and the
# same redundancy/truncation problem — a title is short, so a 20-item list is
# pointless. These presets keep it to what identifies the tab at a glance.
CODEX_TERMINAL_TITLE_PRESETS: Dict[str, List[str]] = {
    "minimal": ["project-name", "git-branch"],
    "balanced": ["activity", "project-name", "git-branch", "run-state"],
    "full": [
        "activity", "project-name", "git-branch", "run-state",
        "model-with-reasoning", "context-remaining",
    ],
}

# Which [tui] array each --target curates, and its preset table.
CODEX_TUI_TARGETS: Dict[str, Dict[str, Any]] = {
    "status_line": {"key": "status_line", "presets": CODEX_STATUSLINE_PRESETS},
    "terminal_title": {"key": "terminal_title", "presets": CODEX_TERMINAL_TITLE_PRESETS},
}


def _codex_tui_array(key: str, items: List[str]) -> str:
    """Render a TOML ``<key> = [...]`` assignment for the given item IDs."""
    inner = ", ".join('"%s"' % i for i in items)
    return "%s = [%s]" % (key, inner)


def _codex_read_tui_array(text: str, key: str = "status_line") -> Optional[List[str]]:
    """Return the current ``[tui].<key>`` array from config TOML text.

    Uses ``tomllib`` when available (Python 3.11+); falls back to a tolerant
    line scan. Returns ``None`` when absent or unparseable.
    """
    try:
        import tomllib  # type: ignore
        try:
            data = tomllib.loads(text)
        except Exception:
            return None
        tui = data.get("tui") if isinstance(data, dict) else None
        val = tui.get(key) if isinstance(tui, dict) else None
        return val if isinstance(val, list) else None
    except ImportError:
        in_tui = False
        buf = ""
        collecting = False
        assign = re.compile(r"^%s\s*=" % re.escape(key))
        for line in text.splitlines():
            stripped = line.strip()
            m = re.match(r"^\[([^\]]+)\]\s*$", stripped)
            if m:
                in_tui = m.group(1).strip() == "tui"
                continue
            if not in_tui:
                continue
            if not collecting and assign.match(stripped):
                buf = stripped.split("=", 1)[1].strip()
                collecting = True
                if buf.count("[") <= buf.count("]"):
                    break
                continue
            if collecting:
                buf += " " + stripped
                if buf.count("[") <= buf.count("]"):
                    break
        if not collecting:
            return None
        items = re.findall(r'"([^"]*)"', buf)
        return items or []


def _codex_apply_tui_array(text: str, key: str, items: List[str]) -> str:
    """Return config.toml text with ``[tui].<key>`` set to ``items``.

    Surgical: only the ``<key>`` array (and, if missing, a ``[tui]`` header) is
    touched. All other tables, comments, and formatting are preserved verbatim —
    this is deliberately NOT a parse-and-rewrite, so the user's config.toml
    round-trips byte-for-byte apart from the one array.
    """
    new_line = _codex_tui_array(key, items)
    lines = text.splitlines(keepends=True)
    header_re = re.compile(r"^\s*\[([^\]]+)\]\s*$")
    tui_start: Optional[int] = None
    tui_end = len(lines)
    for i, ln in enumerate(lines):
        m = header_re.match(ln)
        if not m:
            continue
        name = m.group(1).strip()
        if tui_start is None and name == "tui":
            tui_start = i
        elif tui_start is not None and i > tui_start:
            tui_end = i
            break

    if tui_start is None:
        sep = "" if (text == "" or text.endswith("\n")) else "\n"
        return text + "%s\n[tui]\n%s\n" % (sep, new_line)

    # Match the exact key (so status_line does not match status_line_use_colors).
    assign_re = re.compile(r"^\s*%s\s*=" % re.escape(key))
    for i in range(tui_start + 1, tui_end):
        if assign_re.match(lines[i]):
            # The array may span multiple lines; consume until brackets balance.
            depth = 0
            started = False
            j = i
            while j < tui_end:
                depth += lines[j].count("[") - lines[j].count("]")
                started = started or "[" in lines[j]
                if started and depth <= 0:
                    break
                j += 1
            indent = re.match(r"^(\s*)", lines[i]).group(1)
            return "".join(lines[:i] + [indent + new_line + "\n"] + lines[j + 1:])

    # [tui] exists but has no such key — insert right after the header.
    insert_at = tui_start + 1
    return "".join(lines[:insert_at] + [new_line + "\n"] + lines[insert_at:])


# Back-compat thin wrappers (status_line is the common case; tests use these).
def _codex_status_line_array(items: List[str]) -> str:
    return _codex_tui_array("status_line", items)


def _codex_read_status_line(text: str) -> Optional[List[str]]:
    return _codex_read_tui_array(text, "status_line")


def _codex_apply_status_line(text: str, items: List[str]) -> str:
    return _codex_apply_tui_array(text, "status_line", items)


def _backup_file(path: Path) -> Optional[Path]:
    """Copy ``path`` to a timestamped ``.echook-bak`` sibling. Best-effort —
    returns the backup path, or None if the source doesn't exist / copy fails."""
    if not path.exists():
        return None
    try:
        stamp = time.strftime("%Y%m%d%H%M%S", time.localtime())
        backup = path.with_name(f"{path.name}.echook-{stamp}.bak")
        shutil.copy2(path, backup)
        return backup
    except OSError:
        return None


def _codex_config_path() -> Path:
    return _codex_home() / "config.toml"


# Listed in `audio-hooks manifest` for `statusline codex preview|apply`.
CODEX_STATUSLINE_FLAGS = ("--preset", "--items", "--target")


def _cmd_statusline_codex(args: List[str]) -> int:
    """`audio-hooks statusline codex <show|preview|apply>` — curate Codex's fixed
    [tui] arrays (status_line and/or terminal_title) so they stop truncating."""
    action = args[0] if args else "show"
    rest = args[1:]
    if action in ("preview", "apply"):
        rc = _check_args(f"statusline codex {action}", rest,
                         valued=CODEX_STATUSLINE_FLAGS, max_positionals=0)
        if rc is not None:
            return rc
    preset = None
    items_flag = None
    target = "status_line"
    i = 0
    while i < len(rest):
        if rest[i] == "--preset" and i + 1 < len(rest):
            preset = rest[i + 1]
            i += 2
        elif rest[i] == "--items" and i + 1 < len(rest):
            items_flag = rest[i + 1]
            i += 2
        elif rest[i] == "--target" and i + 1 < len(rest):
            target = rest[i + 1]
            i += 2
        else:
            i += 1

    config_path = _codex_config_path()

    # Which [tui] arrays this invocation acts on.
    if target == "both":
        targets = ["status_line", "terminal_title"]
    elif target in CODEX_TUI_TARGETS:
        targets = [target]
    else:
        return emit_error(
            "INVALID_USAGE",
            f"Unknown --target '{target}'. Use status_line, terminal_title, or both.",
        )

    if action == "show":
        text = ""
        if config_path.exists():
            try:
                text = config_path.read_text(encoding="utf-8")
            except OSError as e:
                return emit_error("CONFIG_READ_ERROR", str(e))
        arrays = {}
        for key in CODEX_TUI_TARGETS:
            cur = _codex_read_tui_array(text, key)
            arrays[key] = {
                "current": cur,
                "item_count": len(cur) if cur is not None else 0,
                # Codex renders each on ONE line; a long list truncates with an
                # ellipsis. Flag the likely-overflow / redundancy cases.
                "likely_overflows": (len(cur) if cur is not None else 0) > 10,
                "presets": list(CODEX_TUI_TARGETS[key]["presets"].keys()),
            }
        emit({
            "ok": True,
            "config_path": str(config_path),
            "config_exists": config_path.exists(),
            # Back-compat top-level mirror of status_line.
            "current": arrays["status_line"]["current"],
            "item_count": arrays["status_line"]["item_count"],
            "likely_overflows": arrays["status_line"]["likely_overflows"],
            "presets": arrays["status_line"]["presets"],
            "targets": arrays,
            "recommended_preset": "balanced",
            "note": (
                "Codex's status line / terminal title accept only fixed built-in "
                "item IDs (no command/script rendering). echook curates them; e.g. "
                "audio-hooks statusline codex apply --preset balanced --target both"
            ),
        })
        return 0

    if action not in ("preview", "apply"):
        return emit_error(
            "INVALID_USAGE",
            f"Unknown codex statusline action: {action}. Use show|preview|apply.",
        )

    # --items only makes sense for a single target.
    if items_flag is not None and len(targets) > 1:
        return emit_error(
            "INVALID_USAGE",
            "--items cannot be combined with --target both; pick one target.",
        )

    # Resolve the item list per target.
    resolved: Dict[str, List[str]] = {}
    for key in targets:
        presets = CODEX_TUI_TARGETS[key]["presets"]
        if items_flag is not None:
            items = [s.strip() for s in items_flag.split(",") if s.strip()]
            source = "items"
        else:
            chosen = preset or "balanced"
            if chosen not in presets:
                return emit_error(
                    "INVALID_USAGE",
                    f"Unknown preset '{chosen}' for {key}. Choose: {', '.join(presets)}",
                )
            items = list(presets[chosen])
            source = f"preset:{chosen}"
        if not items:
            return emit_error("INVALID_USAGE", f"No items resolved for {key} (empty --items?)")
        # Codex silently ignores an item ID it does not know, so a typo would be
        # written into config.toml and then simply never render -- with no way to
        # tell that apart from an item that has no value yet. Reject it here.
        unknown = [i for i in items if i not in CODEX_KNOWN_STATUSLINE_ITEMS]
        if unknown:
            return emit_error(
                "INVALID_USAGE",
                f"Unknown Codex status line item(s) for {key}: {', '.join(unknown)}. "
                f"Codex renders a fixed set; run 'audio-hooks statusline codex show' "
                f"for the valid IDs.",
            )
        resolved[key] = items

    if action == "preview":
        emit({
            "ok": True,
            "config_path": str(config_path),
            "source": source,
            "target": target,
            "items": resolved.get("status_line", resolved[targets[0]]),
            "resolved": resolved,
            "toml": {k: _codex_tui_array(k, v) for k, v in resolved.items()},
            "applied": False,
        })
        return 0

    # action == "apply"
    try:
        text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    except OSError as e:
        return emit_error("CONFIG_READ_ERROR", str(e))
    new_text = text
    for key, items in resolved.items():
        new_text = _codex_apply_tui_array(new_text, key, items)
    # Validate the result parses and round-trips when tomllib is available.
    try:
        import tomllib  # type: ignore
        try:
            parsed = tomllib.loads(new_text)
        except Exception as e:
            return emit_error(
                "INVALID_CONFIG",
                f"Refusing to write — result is not valid TOML: {e}",
            )
        tui = parsed.get("tui") or {}
        for key, items in resolved.items():
            if tui.get(key) != items:
                return emit_error(
                    "INTERNAL_ERROR",
                    f"Refusing to write — {key} did not round-trip as expected.",
                )
    except ImportError:
        pass  # 3.9/3.10: best-effort surgical edit, no validation pass
    backup = _backup_file(config_path)
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(new_text, encoding="utf-8")
    except OSError as e:
        return emit_error("INTERNAL_ERROR", str(e))
    emit({
        "ok": True,
        "config_path": str(config_path),
        "source": source,
        "target": target,
        "items": resolved.get("status_line", resolved[targets[0]]),
        "resolved": resolved,
        "applied": True,
        "backup": str(backup) if backup else None,
        "next_steps": [
            "Restart Codex (or run /statusline) to reload the status line.",
        ],
    })
    return 0


def _cmd_statusline_subagent(args: List[str], project_root: Path) -> int:
    """Manage `subagentStatusLine` — Claude Code's per-subagent row.

    A second settings key, entirely separate from `statusLine`: it renders one
    row per task in the agent panel instead of one line for the session. Note
    the different output contract — NDJSON keyed by task id, not free text —
    which is why it gets its own renderer rather than reusing the main one.
    """
    settings_path = Path.home() / ".claude" / "settings.json"
    script = project_root / "bin" / "audio-hooks-subagent-statusline.py"
    sub = args[0] if args else "show"
    if sub in ("install", "uninstall"):
        rc = _check_args(f"statusline subagent {sub}", args[1:], max_positionals=0)
        if rc is not None:
            return rc

    def _read_settings() -> Dict[str, Any]:
        if not settings_path.exists():
            return {}
        try:
            return json.loads(settings_path.read_text(encoding="utf-8")) or {}
        except json.JSONDecodeError:
            return {}

    if sub == "show":
        settings = _read_settings()
        current = settings.get("subagentStatusLine")
        emit({
            "ok": True,
            "script": str(script),
            "exists": script.exists(),
            "settings_file": str(settings_path),
            "registered": isinstance(current, dict) and current.get("type") == "command",
            "command": (current or {}).get("command") if isinstance(current, dict) else None,
            "note": (
                "Separate from statusLine. Renders one row per subagent in the "
                "agent panel; output is NDJSON ({id, content}) keyed by task id."
            ),
        })
        return 0

    if sub == "install":
        if not script.exists():
            return emit_error("INTERNAL_ERROR", f"subagent status line script not found at {script}")
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings = _read_settings()
            cmd_str = f'python "{script}"' if platform.system() == "Windows" else str(script)
            # Only type + command are accepted here; unlike statusLine there is
            # no padding/refreshInterval on this key.
            settings["subagentStatusLine"] = {"type": "command", "command": cmd_str}
            settings_path.write_text(json.dumps(settings, indent=2) + chr(10), encoding="utf-8")
            emit({
                "ok": True,
                "registered": True,
                "settings_file": str(settings_path),
                "command": cmd_str,
                "next_steps": ["Restart Claude Code, or start a session with subagents, to see the rows."],
            })
            return 0
        except OSError as e:
            return emit_error("INTERNAL_ERROR", str(e))

    if sub == "uninstall":
        if not settings_path.exists():
            emit({"ok": True, "registered": False})
            return 0
        settings = _read_settings()
        if "subagentStatusLine" in settings:
            del settings["subagentStatusLine"]
            try:
                settings_path.write_text(json.dumps(settings, indent=2) + chr(10), encoding="utf-8")
            except OSError as e:
                return emit_error("INTERNAL_ERROR", str(e))
        emit({"ok": True, "registered": False})
        return 0

    return emit_error("INVALID_USAGE", f"Unknown statusline subagent subcommand: {sub}")


def cmd_statusline(args: List[str], project_root: Path) -> int:
    """Manage the status line: Claude Code registration + segment catalog, and
    Codex [tui].status_line curation."""
    sub = args[0] if args else "show"
    settings_path = Path.home() / ".claude" / "settings.json"
    if sub in ("install", "uninstall"):
        rc = _check_args(f"statusline {sub}", args[1:], max_positionals=0)
        if rc is not None:
            return rc

    if sub == "segments":
        emit({
            "ok": True,
            "segments": STATUSLINE_SEGMENTS,
            "line1": [s["name"] for s in STATUSLINE_SEGMENTS if s["line"] == 1],
            "line2": [s["name"] for s in STATUSLINE_SEGMENTS if s["line"] == 2],
            "config": {
                "visible_segments": "Whitelist — when non-empty, only these show.",
                "hidden_segments": "Blacklist — applied when visible_segments is empty; show all except these.",
                "extra_segments": "Opt-in additions — segments whose catalog entry has \"default\": false appear only when named here (or in a non-empty visible_segments). hidden_segments still wins.",
                "opt_in_example": "audio-hooks set statusline_settings.extra_segments '[\"prompt_cache\"]'",
                "set_example": "audio-hooks set statusline_settings.hidden_segments '[\"burn_rate\",\"api_time\"]'",
            },
        })
        return 0

    if sub == "codex":
        return _cmd_statusline_codex(args[1:])

    if sub == "subagent":
        return _cmd_statusline_subagent(args[1:], project_root)

    if sub == "show":
        statusline_script = project_root / "bin" / "audio-hooks-statusline.py"
        emit({
            "ok": True,
            "script": str(statusline_script),
            "exists": statusline_script.exists(),
            "settings_file": str(settings_path),
            "registered": False if not settings_path.exists() else (
                "statusLine" in (json.loads(settings_path.read_text(encoding="utf-8")) or {})
            ),
        })
        return 0

    if sub == "install":
        statusline_script = project_root / "bin" / "audio-hooks-statusline.py"
        if not statusline_script.exists():
            return emit_error("INTERNAL_ERROR", f"audio-hooks-statusline not found at {statusline_script}")
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings: Dict[str, Any] = {}
            if settings_path.exists():
                try:
                    settings = json.loads(settings_path.read_text(encoding="utf-8")) or {}
                except json.JSONDecodeError:
                    settings = {}
            # On Windows the script needs the python interpreter prefix to run
            cmd_str = f'python "{statusline_script}"' if platform.system() == "Windows" else str(statusline_script)
            settings["statusLine"] = {
                "type": "command",
                "command": cmd_str,
                # padding 0 lets the line use the full terminal width; the
                # script's own WIDTH_SAFETY_MARGIN reserves the edge so nothing
                # is truncated. (1 indented the line and shrank usable width.)
                "padding": 0,
                "refreshInterval": 60,
            }
            settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
            emit({"ok": True, "registered": True, "settings_file": str(settings_path), "command": cmd_str})
            return 0
        except OSError as e:
            return emit_error("INTERNAL_ERROR", str(e))

    if sub == "uninstall":
        if not settings_path.exists():
            emit({"ok": True, "registered": False})
            return 0
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8")) or {}
        except json.JSONDecodeError:
            return emit_error("INTERNAL_ERROR", "settings.json is not valid JSON")
        if "statusLine" in settings:
            del settings["statusLine"]
            try:
                settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
            except OSError as e:
                return emit_error("INTERNAL_ERROR", str(e))
        emit({"ok": True, "registered": False})
        return 0

    return emit_error("INVALID_USAGE", f"Unknown statusline subcommand: {sub}")
