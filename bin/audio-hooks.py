#!/usr/bin/env python3
"""audio-hooks — single JSON CLI for the echook project.

This binary is the canonical machine interface for the project. It is designed
for Claude Code (and other AI agents) to operate the project end-to-end without
any human interaction.

Hard rules:
  - All output is JSON to stdout. No stderr in normal operation.
  - Nonzero exit codes carry a JSON error body on stdout.
  - No prompts, no colors, no spinners, no menus.
  - Every config knob is settable in one shot via `set` or a typed setter.
  - Every state read returns a single JSON document in <100ms.

The keystone subcommand is `manifest`: it returns the complete machine
description of every other subcommand, every config key, every hook, every
audio file, and every error code. Read it once and the entire surface area is
known.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# The larger concerns live in the echook_cli package beside this file (its
# docstring maps module -> concern). A normal launch already has this directory
# on sys.path; a loader that executes the file by path (the test suite's
# importlib.util.spec_from_file_location) does not, so add it explicitly.
_BIN_DIR = str(Path(__file__).resolve().parent)
if _BIN_DIR not in sys.path:
    sys.path.insert(0, _BIN_DIR)

from echook_cli import config_backup, plugin_upgrade, statusline  # noqa: E402
from echook_cli.codex_config import _check_codex_feature_flag, _codex_home  # noqa: E402
from echook_cli.editor_installs import (  # noqa: E402
    _install_codex, _install_cursor, _uninstall_codex, _uninstall_cursor,
)
from echook_cli.error_codes import CLI_ERROR_CODES  # noqa: E402
from echook_cli.legacy_uninstall import LEGACY_FILE_MARKERS, _has_marker, _uninstall_scripts  # noqa: E402
from echook_cli.manifest_schema import _build_manifest_schema  # noqa: E402
from echook_cli.output import _check_args, _is_help_flag, _looks_like_flag, emit, emit_error  # noqa: E402

# ---------------------------------------------------------------------------
# Path discovery — find the project root and import hook_runner helpers
# ---------------------------------------------------------------------------

def _find_project_root() -> Optional[Path]:
    """Discover the project root by walking up from this script.

    Mirrors hook_runner.get_project_dir() but starts from bin/ instead of
    hooks/. Honors CLAUDE_AUDIO_HOOKS_PROJECT for explicit override.
    """
    explicit = os.environ.get("CLAUDE_AUDIO_HOOKS_PROJECT")
    if explicit:
        p = Path(explicit)
        if (p / "hooks" / "hook_runner.py").exists():
            return p

    here = Path(__file__).resolve()
    # Walk up looking for the project signature: hooks/hook_runner.py + config/
    for ancestor in [here.parent] + list(here.parents):
        if (ancestor / "hooks" / "hook_runner.py").exists() and (ancestor / "config").is_dir():
            return ancestor

    # Plugin install: ${CLAUDE_PLUGIN_ROOT}
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        p = Path(plugin_root)
        # The plugin layout symlinks hooks/ -> ../../../hooks/ so this works.
        if (p / "hooks" / "hook_runner.py").exists():
            return p
        # Or the plugin might point at the runner subdir directly
        runner = p / "runner" / "hook_runner.py"
        if runner.exists():
            return p.parent.parent.parent if (p.parent.parent.parent / "config").is_dir() else None

    return None


PROJECT_ROOT = _find_project_root()


def _import_hook_runner():
    """Import the hook_runner module so we can reuse its helpers."""
    if PROJECT_ROOT is None:
        return None
    hooks_dir = PROJECT_ROOT / "hooks"
    if str(hooks_dir) not in sys.path:
        sys.path.insert(0, str(hooks_dir))
    try:
        import hook_runner  # type: ignore
        return hook_runner
    except ImportError:
        return None


HR = _import_hook_runner()


# Import UserPreferences from hooks/. Path is already on sys.path if HR
# imported successfully; we still re-add defensively so the module loads
# even when the runner import failed (e.g. partial install).
if PROJECT_ROOT is not None:
    hooks_dir = PROJECT_ROOT / "hooks"
    if str(hooks_dir) not in sys.path:
        sys.path.insert(0, str(hooks_dir))
try:
    from user_preferences import UserPreferences, get_prefs  # type: ignore
except ImportError:
    UserPreferences = None  # type: ignore
    def get_prefs(*_a, **_k):  # type: ignore
        raise RuntimeError(
            "user_preferences module unavailable; reinstall the project"
        )


def _prefs():
    """Return the process-wide UserPreferences singleton anchored at PROJECT_ROOT."""
    return get_prefs(PROJECT_ROOT)


def require_project_root() -> int:
    """Bail with a structured error if the project root could not be found."""
    if PROJECT_ROOT is None:
        return emit_error(
            code="PROJECT_DIR_NOT_FOUND",
            message="Could not locate the echook project directory.",
            hint="Set CLAUDE_AUDIO_HOOKS_PROJECT or run from inside the repo.",
            suggested_command="audio-hooks status",
        )
    if HR is None:
        return emit_error(
            code="INTERNAL_ERROR",
            message="Could not import hook_runner.py from the project directory.",
            hint="The project layout may be corrupted.",
            suggested_command="audio-hooks diagnose",
        )
    return 0


# ---------------------------------------------------------------------------
# Project state — version, install detection, hook catalogue
# ---------------------------------------------------------------------------

PROJECT_VERSION = "7.1.0"

# Canonical hook catalogue. Order matches CLAUDE.md and the install scripts.
HOOK_CATALOG: List[Dict[str, Any]] = [
    {"name": "notification",         "default": True,  "audio": "notification-urgent.mp3",   "description": "Authorization or plan confirmation requested"},
    {"name": "stop",                 "default": True,  "audio": "task-complete.mp3",         "description": "End of EVERY turn, not task completion — Claude Code exposes no field distinguishing a final turn. For 'the work is done', use notification (idle_prompt)"},
    {"name": "subagent_stop",        "default": False, "audio": "subagent-complete.mp3",     "description": "Background subagent task done"},
    {"name": "permission_request",   "default": True,  "audio": "permission-request.mp3",    "description": "Permission dialog appeared"},
    {"name": "session_start",        "default": False, "audio": "session-start.mp3",         "description": "Session began (matchers: startup|resume|clear|compact)"},
    {"name": "session_end",          "default": False, "audio": "session-end.mp3",           "description": "Session ended"},
    {"name": "pretooluse",           "default": False, "audio": "task-starting.mp3",         "description": "Before each tool execution (noisy)"},
    {"name": "posttooluse",          "default": False, "audio": "task-progress.mp3",         "description": "After each tool execution (very noisy)"},
    {"name": "posttoolusefailure",   "default": False, "audio": "tool-failed.mp3",           "description": "Tool execution failed"},
    {"name": "userpromptsubmit",     "default": False, "audio": "prompt-received.mp3",       "description": "User submitted a prompt"},
    {"name": "precompact",           "default": False, "audio": "pre-compact.mp3",     "description": "Before context compaction"},
    {"name": "postcompact",          "default": False, "audio": "post-compact.mp3",          "description": "After context compaction"},
    {"name": "subagent_start",       "default": False, "audio": "subagent-start.mp3",        "description": "Subagent spawned"},
    {"name": "teammate_idle",        "default": False, "audio": "teammate-idle.mp3",         "description": "Agent Teams teammate going idle"},
    {"name": "task_completed",       "default": False, "audio": "team-task-done.mp3",        "description": "Agent Teams task completed"},
    {"name": "stop_failure",         "default": False, "audio": "stop-failure.mp3",          "description": "API error (matchers: rate_limit|authentication_failed|...)"},
    {"name": "config_change",        "default": False, "audio": "config-change.mp3",         "description": "Configuration file changed"},
    {"name": "instructions_loaded",  "default": False, "audio": "instructions-loaded.mp3",   "description": "CLAUDE.md or rules loaded"},
    {"name": "elicitation",          "default": False, "audio": "elicitation.mp3",           "description": "MCP server requested user input"},
    {"name": "elicitation_result",   "default": False, "audio": "elicitation-result.mp3",    "description": "User responded to MCP elicitation"},
    # New in v5.0 (dedicated audio shipped in v5.0.1, generated via ElevenLabs).
    {"name": "permission_denied",    "default": False, "audio": "permission-denied.mp3",     "description": "Auto mode classifier denied a tool call (v5.0)"},
    {"name": "cwd_changed",          "default": False, "audio": "cwd-changed.mp3",           "description": "Working directory changed (v5.0)"},
    {"name": "directory_added",      "default": False, "audio": "directory-added.mp3",           "description": "A directory was added to the session via /add-dir or register_repo_root (v6.5)"},
    {"name": "worktree_remove",      "default": False, "audio": "worktree-removed.mp3",           "description": "A git worktree was removed (v6.5). Not a provider hook, unlike WorktreeCreate"},
    {"name": "file_changed",         "default": False, "audio": "file-changed.mp3",          "description": "Watched file changed on disk (v5.0)"},
    {"name": "task_created",         "default": False, "audio": "task-created.mp3",          "description": "Task created via TaskCreate (v5.0)"},
    # New in v6.2 — Claude Code lifecycle events added since v5.0.
    {"name": "setup",                "default": False, "audio": "setup-ready.mp3",           "description": "First-run/maintenance setup finished (Claude Code Setup; matchers: init|maintenance) (v6.2)"},
    {"name": "user_prompt_expansion","default": False, "audio": "prompt-expanded.mp3",       "description": "A typed command/skill expanded into a prompt (Claude Code; noisy) (v6.2)"},
    {"name": "post_tool_batch",      "default": False, "audio": "batch-complete.mp3",        "description": "A batch of parallel tool calls resolved (Claude Code) (v6.2)"},
    {"name": "message_display",      "default": False, "audio": "message-display.mp3",       "description": "Assistant message displayed (Claude Code; very noisy) (v6.2)"},
    # New in v6.2 — Cursor granular per-tool-type events (Cursor-only).
    {"name": "shell_before",         "default": False, "audio": "shell-starting.mp3",        "description": "Shell command about to run (Cursor beforeShellExecution) (v6.2)"},
    {"name": "shell_after",          "default": False, "audio": "shell-done.mp3",            "description": "Shell command finished (Cursor afterShellExecution) (v6.2)"},
    {"name": "mcp_before",           "default": False, "audio": "mcp-starting.mp3",          "description": "MCP tool about to run (Cursor beforeMCPExecution) (v6.2)"},
    {"name": "mcp_after",            "default": False, "audio": "mcp-done.mp3",              "description": "MCP tool finished (Cursor afterMCPExecution) (v6.2)"},
    {"name": "file_read",            "default": False, "audio": "file-read.mp3",             "description": "Agent reading a file (Cursor beforeReadFile/beforeTabFileRead) (v6.2)"},
    {"name": "agent_response",       "default": False, "audio": "agent-response.mp3",        "description": "Assistant message completed (Cursor afterAgentResponse) (v6.2)"},
    {"name": "agent_thinking",       "default": False, "audio": "thinking-done.mp3",         "description": "Reasoning block finished (Cursor afterAgentThought) (v6.2)"},
    {"name": "workspace_open",       "default": False, "audio": "workspace-open.mp3",        "description": "Workspace opened / folder changed (Cursor workspaceOpen) (v6.2)"},
    {"name": "tab_file_edit",        "default": False, "audio": "tab-edit.mp3",              "description": "Tab inline edit applied (Cursor afterTabFileEdit; very noisy) (v6.2)"},
]


def _in_orphaned_cache_dir(entry: Path, cache_dir: Path) -> bool:
    """True when ``entry`` sits under a plugin-cache version dir Claude Code orphaned.

    v6.6: after an uninstall Claude Code leaves the old
    ``cache/<marketplace>/<plugin>/<version>/`` directory behind and drops an
    ``.orphaned_at`` file (a millisecond epoch) in it; the live version dir has
    none (it carries ``.in_use/`` instead). Observed on disk: 7 of 8 context7
    version dirs carry the marker, the one in use does not. Counting an orphan
    as "installed" made a machine look dual-installed, with a remedy that could
    not help.
    """
    try:
        for ancestor in entry.parents:
            if (ancestor / ".orphaned_at").exists():
                return True
            if ancestor == cache_dir:
                break
    except OSError:
        pass
    return False


def _detect_install_mode() -> Dict[str, Any]:
    """Detect whether the script install and/or plugin install are present.

    The script install is detected by the legacy ~/.claude/hooks/hook_runner.py
    file (placed there by the script installer, which was removed in 7.0.0;
    installs made before that keep working and are still detected here).

    The plugin install is detected by:
      1. CLAUDE_PLUGIN_ROOT being set (we're invoked from inside a hook), OR
      2. ~/.claude/plugins/installed_plugins.json containing audio-hooks, OR
      3. ~/.claude/plugins/cache/<id>/ existing for any audio-hooks plugin
         (ignoring version dirs Claude Code marked with ``.orphaned_at``).
    """
    home = Path.home()
    # By content, like the removal: a user's own (or marker-less) hook_runner.py is
    # not an echook script install, and reporting it as one would send an agent to
    # `audio-hooks uninstall`, which correctly refuses to touch it -- forever.
    runner = home / ".claude" / "hooks" / "hook_runner.py"
    script_install = runner.is_file() and _has_marker(runner, LEGACY_FILE_MARKERS["hook_runner.py"])

    plugin_install = bool(os.environ.get("CLAUDE_PLUGIN_ROOT"))
    if not plugin_install:
        installed_json = home / ".claude" / "plugins" / "installed_plugins.json"
        if installed_json.exists():
            try:
                data = json.loads(installed_json.read_text(encoding="utf-8"))
                # Schema may be {"plugins": {...}} or a flat dict; check both
                blob = json.dumps(data).lower()
                if "audio-hooks" in blob:
                    plugin_install = True
            except Exception:
                pass
    if not plugin_install:
        cache_dir = home / ".claude" / "plugins" / "cache"
        if cache_dir.exists():
            try:
                for entry in cache_dir.rglob("plugin.json"):
                    try:
                        if _in_orphaned_cache_dir(entry, cache_dir):
                            continue
                        if "audio-hooks" in entry.parent.name.lower():
                            plugin_install = True
                            break
                        manifest = json.loads(entry.read_text(encoding="utf-8"))
                        if manifest.get("name") == "audio-hooks":
                            plugin_install = True
                            break
                    except Exception:
                        continue
            except Exception:
                pass

    result: Dict[str, Any] = {"script_install": script_install, "plugin_install": plugin_install}
    if script_install and plugin_install:
        remedy_text, _remedy_cmd = _script_uninstall_remedy()
        result["warning"] = {
            "code": "DUAL_INSTALL_DETECTED",
            "message": "Both the script install and the plugin install are active. This causes double audio. " + remedy_text,
        }
    return result


def _script_uninstall_remedy() -> Tuple[str, str]:
    """(prose, runnable command) that removes the script install.

    v6.6: ``audio-hooks uninstall`` removes it natively on every platform, so
    the remedy needs no shell script (the plugin layout never shipped one).
    """
    return (
        "Run `audio-hooks uninstall` to remove the script install (preserves config + audio).",
        "audio-hooks uninstall",
    )


def _detect_codex_native_install() -> bool:
    """True iff ``$CODEX_HOME/hooks.json`` contains audio-hooks-managed entries.

    Codex doesn't auto-bridge Claude Code plugins, so this is the only way
    audio-hooks can fire under Codex.
    """
    codex_dir = _codex_home()
    codex_hooks = codex_dir / "hooks.json"
    if not codex_hooks.exists():
        return False
    try:
        doc = json.loads(codex_hooks.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(doc, dict):
        return False
    # Files written by 7.0.0 and earlier carry a top-level marker Codex rejects;
    # current ones are recognised by the per-entry tag alone.
    hooks = doc.get("hooks")
    tagged = isinstance(hooks, dict) and any(
        isinstance(e, dict) and e.get("_managed_by") == "audio-hooks"
        for entries in hooks.values() if isinstance(entries, list)
        for e in entries
    )
    return tagged or bool(doc.get("_audio_hooks_managed"))


def _detect_codex_plugin_install() -> Optional[str]:
    """Return Codex plugin cache path when audio-hooks appears installed."""
    codex_dir = _codex_home()
    roots = [codex_dir / "plugins" / "cache", codex_dir / "plugins"]
    for root in roots:
        if not root.exists():
            continue
        try:
            manifests = list(root.glob("**/.codex-plugin/plugin.json"))
        except OSError:
            continue
        for manifest in manifests:
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict) and data.get("name") == "audio-hooks":
                return str(manifest.parent.parent)
    return None


def _codex_feature_enabled_from_state(state: str) -> Optional[bool]:
    if state in ("disabled", "disabled_legacy"):
        return False
    if state == "parse_error":
        return None
    return True


def _detect_codex_hooks_feature_state() -> str:
    """Read ``$CODEX_HOME/config.toml`` and return Codex hooks feature state."""
    return _check_codex_feature_flag(_codex_home() / "config.toml")["state"]


def _detect_codex_feature_flag() -> Optional[bool]:
    """Backward-compatible bool for callers that predate hooks_feature_state."""
    return _codex_feature_enabled_from_state(_detect_codex_hooks_feature_state())


def _detect_cursor_native_install() -> bool:
    """True iff ``~/.cursor/hooks.json`` contains audio-hooks-managed entries.

    Cursor-native install is what ``audio-hooks install --cursor`` writes.
    Distinct from the auto-bridge: the bridge fires whenever Claude Code
    plugins are present (and, in the IDE, the third-party-plugins toggle in
    Cursor Settings is on; cursor-agent bridges regardless), requiring no
    file in ``~/.cursor/``.
    """
    cursor_hooks = Path.home() / ".cursor" / "hooks.json"
    if not cursor_hooks.exists():
        return False
    try:
        data = json.loads(cursor_hooks.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(data.get("_audio_hooks_managed"))


def _detect_editor_targets() -> Dict[str, Any]:
    """Report registration state for each editor target (claude-code, cursor).

    States:
      * ``active`` — installed and primary integration path
      * ``bridged-via-claude-code`` — Cursor IDE auto-bridges Claude Code
        plugins (cursor.com/docs/reference/third-party-hooks). Fires when
        the user has Claude Code's audio-hooks plugin installed. In the IDE
        the bridge can be switched off at Cursor Settings > Rules, Skills,
        Subagents > "Include third-party Plugins, Skills, and other configs";
        that toggle has no effect on cursor-agent, where bridging is hardcoded.
      * ``native`` — Cursor-only ``~/.cursor/hooks.json`` install
      * ``double-registered`` — both bridge AND native — causes double audio
      * ``inactive`` — no integration detected
    """
    install = _detect_install_mode()
    cc_state = "active" if (install.get("plugin_install") or install.get("script_install")) else "inactive"
    cc_via = (
        "plugin" if install.get("plugin_install")
        else ("script" if install.get("script_install") else None)
    )

    cursor_native = _detect_cursor_native_install()
    cursor_bridged = bool(install.get("plugin_install"))

    if cursor_native and cursor_bridged:
        cursor_state = "double-registered"
    elif cursor_native:
        cursor_state = "native"
    elif cursor_bridged:
        cursor_state = "bridged-via-claude-code"
    else:
        cursor_state = "inactive"

    codex_native = _detect_codex_native_install()
    codex_plugin_path = _detect_codex_plugin_install()
    codex_feature_state = _detect_codex_hooks_feature_state()
    codex_flag = _codex_feature_enabled_from_state(codex_feature_state)
    codex_via: List[str] = []
    if codex_plugin_path:
        codex_via.append("plugin")
    if codex_native:
        codex_via.append("native")
    if codex_via:
        if codex_feature_state in ("disabled", "disabled_legacy"):
            codex_state = "active-but-hooks-disabled"
        elif codex_feature_state == "parse_error":
            codex_state = "active-but-config-unreadable"
        else:
            codex_state = "active"
    else:
        codex_state = "inactive"

    codex_dir = _codex_home()

    result: Dict[str, Any] = {
        "claude-code": {"state": cc_state, "via": cc_via},
        "cursor": {
            "state": cursor_state,
            "native": cursor_native,
            "bridged": cursor_bridged,
        },
        "codex": {
            "state": codex_state,
            "via": codex_via,
            "hooks_file": str(codex_dir / "hooks.json") if codex_native else None,
            "plugin_path": codex_plugin_path,
            "config_path": str(codex_dir / "config.toml"),
            "feature_flag_enabled": codex_flag,
            "hooks_feature_state": codex_feature_state,
            "data_dir": str(codex_dir / "audio-hooks-data"),
        },
    }
    if cursor_state == "double-registered":
        result["cursor"]["warning"] = {
            "code": "DUPLICATE_BRIDGE",
            "message": (
                "Cursor IDE is configured both via the Claude Code plugin auto-bridge "
                "AND via a native ~/.cursor/hooks.json install. Each session-end event "
                "will fire the audio twice. Run `audio-hooks uninstall --cursor` to "
                "remove the native registration, OR uninstall the Claude Code plugin."
            ),
        }
    if cursor_state == "bridged-via-claude-code":
        result["cursor"]["note"] = (
            "Cursor IDE auto-bridges Claude Code plugins. Notification and "
            "PermissionRequest hooks have no Cursor equivalent and never fire here "
            "(cursor.com/docs/reference/third-party-hooks)."
        )
    if codex_state == "active-but-hooks-disabled":
        result["codex"]["warning"] = {
            "code": "CODEX_HOOKS_DISABLED",
            "message": (
                f"Codex hooks are installed at {codex_dir / 'hooks.json'} but the "
                f"`[features].hooks` flag is false in {codex_dir / 'config.toml'}. "
                "Codex won't invoke any hooks until that opt-out is removed or set to true."
            ),
        }
    elif codex_state == "active-but-config-unreadable":
        result["codex"]["warning"] = {
            "code": "CODEX_CONFIG_PARSE_ERROR",
            "message": (
                f"Codex hooks are installed but {codex_dir / 'config.toml'} could not be "
                "read or parsed. Fix the TOML file; hooks are enabled by default unless "
                "`[features].hooks = false` is present."
            ),
        }
    return result


def _redact_url(url: str) -> str:
    """Redact secrets from a webhook URL for safe display."""
    if not url:
        return ""
    # Strip basic-auth and query strings that might contain tokens
    out = re.sub(r"://[^@]+@", "://***@", url)
    out = re.sub(r"\?.*$", "?***", out)
    return out


def _config_path() -> Path:
    """Backwards-compatible thin wrapper around UserPreferences.config_path.

    Path resolution (CLAUDE_PLUGIN_DATA → CLAUDE_AUDIO_HOOKS_DATA → plugin
    cache → shared Claude Code dir → Cursor-native → legacy temp) lives in
    :class:`UserPreferences`. New code should call ``_prefs().config_path``.
    """
    return _prefs().config_path


# v6.7: set by main() for invocations that only report (see
# _is_read_only_invocation). Such a command works from the in-memory defaults
# merged with whatever is on disk and creates, migrates and re-stamps nothing.
_READ_ONLY = False


def _load_config_raw() -> Dict[str, Any]:
    """Load user_preferences.json (auto-init from template + plugin-option overlay).

    In a read-only invocation nothing is created or migrated on disk; the same
    merged result is computed in memory instead.
    """
    if PROJECT_ROOT is None:
        return {}
    try:
        return _prefs().load(read_only=_READ_ONLY)
    except Exception:
        return {}


def _save_config_raw(cfg: Dict[str, Any]) -> Tuple[bool, str]:
    """Save user_preferences.json (atomic write + auto-backup snapshot)."""
    if PROJECT_ROOT is None:
        return False, "PROJECT_ROOT not detected"
    if _READ_ONLY:
        return False, "refusing to write during a read-only invocation"
    try:
        _prefs().save(cfg)
        return True, ""
    except Exception as e:
        return False, str(e)


def _get_dotted(cfg: Dict[str, Any], key: str) -> Any:
    parts = key.split(".")
    cur: Any = cfg
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _set_dotted(cfg: Dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    cur: Dict[str, Any] = cfg
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def _coerce_value(raw: str) -> Any:
    """Best-effort coercion: bool, int, float, JSON, else string."""
    s = raw.strip()
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    if s.lower() in ("null", "none"):
        return None
    if s and (s[0] in "{[\"" or s.lstrip("-").replace(".", "").isdigit()):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
    return raw


# ---------------------------------------------------------------------------
# Snooze marker (read through hook_runner.read_snooze_until, like is_snoozed)
# ---------------------------------------------------------------------------

def _queue_dir() -> Path:
    if PROJECT_ROOT is not None:
        try:
            return _prefs().queue_dir
        except Exception:
            pass
    return Path("/tmp/claude_audio_hooks_queue")


def _snooze_file() -> Path:
    return _queue_dir() / "snooze_until"


def _log_dir() -> Path:
    """Directory holding events.ndjson. HR.runner_core.get_log_dir() creates it; a read-only
    invocation must not, so it takes the path without the mkdir."""
    if _READ_ONLY and PROJECT_ROOT is not None:
        return _prefs().log_dir
    return HR.runner_core.get_log_dir() if HR else Path("/tmp")


def _snooze_status() -> Dict[str, Any]:
    # The runner's own reader, which creates nothing (is_snoozed itself makes
    # the queue directory, which a reporting command must not).
    until, error = HR.read_snooze_until(_queue_dir())
    if error is not None or until is None:
        return {"active": False, "remaining_seconds": 0, "until": None}
    now = time.time()
    if not now < until:  # the runner's test, negated: snoozed while now < until
        return {"active": False, "remaining_seconds": 0, "until": until}
    return {
        "active": True,
        "remaining_seconds": int(until - now),
        "until": until,
        "until_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(until)),
    }


def _parse_duration(s: str) -> Optional[int]:
    """Parse '30m', '1h', '90s', or bare integer (minutes). Return seconds."""
    s = s.strip().lower()
    if not s:
        return None
    m = re.match(r"^(\d+)\s*([smhd]?)$", s)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2) or "m"
    if unit == "s":
        return n
    if unit == "m":
        return n * 60
    if unit == "h":
        return n * 3600
    if unit == "d":
        return n * 86400
    return None


# ---------------------------------------------------------------------------
# Subcommand: version
# ---------------------------------------------------------------------------

def cmd_version(_args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    install = _detect_install_mode()
    emit({
        "ok": True,
        "version": PROJECT_VERSION,
        "hook_runner_version": getattr(HR, "HOOK_RUNNER_VERSION", PROJECT_VERSION),
        "project_dir": str(PROJECT_ROOT),
        "script_install": install["script_install"],
        "plugin_install": install["plugin_install"],
    })
    return 0


# ---------------------------------------------------------------------------
# Subcommand: status
# ---------------------------------------------------------------------------

def cmd_status(_args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    cfg = _load_config_raw()
    enabled_hooks_cfg = cfg.get("enabled_hooks", {}) if isinstance(cfg.get("enabled_hooks"), dict) else {}

    enabled = [h["name"] for h in HOOK_CATALOG if _hook_enabled(enabled_hooks_cfg, h["name"])]

    customizations: Dict[str, Any] = {}
    try:
        customizations = _prefs().diff_from_default(read_only=_READ_ONLY)
    except Exception:
        pass

    webhook = cfg.get("webhook_settings", {}) or {}
    tts = cfg.get("tts_settings", {}) or {}
    rl = cfg.get("rate_limit_alerts", {}) or {}
    sl = cfg.get("statusline_settings", {}) or {}
    install = _detect_install_mode()

    # Resolve the effective plugin data dir even when CLAUDE_PLUGIN_DATA isn't set.
    # When this CLI binary lives inside a plugin layout (parent.parent has
    # `.claude-plugin/plugin.json`), surface the plugin's shared data dir so
    # `audio-hooks status` reports the same path the runtime reads.
    plugin_data_dir = os.environ.get("CLAUDE_PLUGIN_DATA")
    if not plugin_data_dir and UserPreferences is not None and PROJECT_ROOT is not None:
        cli_script = Path(__file__).resolve()
        cli_plugin_marker = cli_script.parent.parent / ".claude-plugin" / "plugin.json"
        if cli_plugin_marker.exists():
            plugin_data_dir = str(
                UserPreferences(PROJECT_ROOT, script_path=cli_script)._plugin_cache_data_dir()
            )

    emit({
        "ok": True,
        "version": PROJECT_VERSION,
        "project_dir": str(PROJECT_ROOT),
        "plugin_data_dir": plugin_data_dir,
        "queue_dir": str(_queue_dir()),
        "log_dir": str(_log_dir()) if HR else None,
        "theme": cfg.get("audio_theme", "default"),
        "enabled_hooks": enabled,
        "enabled_hook_count": len(enabled),
        "total_hook_count": len(HOOK_CATALOG),
        # v6.4: a variant override is invisible in enabled_hooks above (which
        # lists canonical hooks only), so surface it here — otherwise "why is
        # notification on but silent for idle prompts?" has no answer in status.
        "variants": _variant_status_summary(cfg),
        "snooze": _snooze_status(),
        "webhook": {
            "enabled": bool(webhook.get("enabled")),
            "format": webhook.get("format", "raw"),
            "url_redacted": _redact_url(webhook.get("url", "")),
        },
        "tts": {
            "enabled": bool(tts.get("enabled")),
            "speak_assistant_message": bool(tts.get("speak_assistant_message")),
        },
        "rate_limit_alerts": {
            "enabled": bool(rl.get("enabled", True)),
            "five_hour_thresholds": rl.get("five_hour_thresholds", [80, 95]),
            "seven_day_thresholds": rl.get("seven_day_thresholds", [80, 95]),
        },
        "install": install,
        "editor_targets": _detect_editor_targets(),
        "statusline": {
            "visible_segments": sl.get("visible_segments", []),
            "hidden_segments": sl.get("hidden_segments", []),
            "extra_segments": sl.get("extra_segments", []),
            "max_width": sl.get("max_width", 0),
        },
        "customizations": customizations,
    })
    return 0


# ---------------------------------------------------------------------------
# Subcommand: get / set
# ---------------------------------------------------------------------------

def cmd_get(args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    if not args:
        return emit_error("INVALID_USAGE", "Usage: audio-hooks get <key>", suggested_command="audio-hooks manifest")
    key = args[0]
    cfg = _load_config_raw()
    val = _get_dotted(cfg, key)
    emit({"ok": True, "key": key, "value": val})
    return 0


def cmd_set(args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    if len(args) < 2:
        return emit_error("INVALID_USAGE", "Usage: audio-hooks set <key> <value>", suggested_command="audio-hooks manifest")
    key = args[0]
    if _looks_like_flag(key):
        # Only the key is checked: the value may legitimately start with "-"
        # (a negative number).
        return emit_error("INVALID_USAGE", f"Unknown argument for `audio-hooks set`: {key}. Nothing was changed.",
                          hint="Usage: audio-hooks set <dotted.key> <value>", suggested_command="audio-hooks set --help",
                          unknown_args=[key])
    if any(_is_help_flag(a) for a in args[1:]):
        # A leading help token is intercepted by main() and prints usage. One
        # after the key would be stored as the value (`notification_settings.mode`
        # = "--help" silences every hook), so it is an error, never data.
        bad = next(a for a in args[1:] if _is_help_flag(a))
        return emit_error("INVALID_USAGE",
                          f"`audio-hooks set` does not accept a value spelled like a help flag ({bad}). Nothing was changed.",
                          hint="Put --help first to see usage: audio-hooks set --help",
                          suggested_command="audio-hooks set --help", unknown_args=[bad])
    if len(args) > 2:
        return emit_error("INVALID_USAGE", f"Unexpected argument for `audio-hooks set`: {args[2]}. Nothing was changed.",
                          hint="Usage: audio-hooks set <dotted.key> <value> (quote a value that contains spaces)",
                          suggested_command="audio-hooks set --help", unknown_args=args[2:])
    value = _coerce_value(args[1])
    cfg = _load_config_raw()
    old = _get_dotted(cfg, key)
    _set_dotted(cfg, key, value)
    ok, err = _save_config_raw(cfg)
    if not ok:
        return emit_error("CONFIG_READ_ERROR", f"Could not write config: {err}")
    emit({"ok": True, "key": key, "old_value": old, "new_value": value, "restart_required": False})
    return 0


# ---------------------------------------------------------------------------
# Subcommand: hooks list / enable / disable / enable-only
# ---------------------------------------------------------------------------

def _variant_catalog() -> List[Dict[str, Any]]:
    """Matcher-scoped variants, derived from hook_runner rather than restated.

    A hand-written third list would be a fourth place to forget to update; the
    contract tests in tests/test_plugin_hooks_contract.py exist precisely to
    stop these surfaces drifting, so this one reads the map directly.
    """
    by_name = {h["name"]: h for h in HOOK_CATALOG}
    out = []
    for variant, entry in sorted(getattr(HR, "SYNTHETIC_EVENT_MAP", {}).items()):
        parent, audio_override = entry
        parent_meta = by_name.get(parent, {})
        out.append({
            "name": variant,
            "variant_of": parent,
            "audio_file": audio_override or parent_meta.get("audio"),
            # Absent from the defaults table means "inherit the parent".
            "default": getattr(HR, "SYNTHETIC_VARIANT_DEFAULTS", {}).get(
                variant, parent_meta.get("default", False)),
        })
    return out


def _hook_enabled(enabled_hooks: Dict[str, Any], hook: str, variant: Optional[str] = None) -> bool:
    """Whether the runner will fire ``hook`` (arriving as ``variant``) for this
    ``enabled_hooks`` mapping. hook_runner.resolve_hook_enabled is the only
    implementation of the precedence; nothing here restates it."""
    return HR.resolve_hook_enabled(enabled_hooks, hook, variant)[0]


def _hooks_state(include_variants: bool = False) -> List[Dict[str, Any]]:
    cfg = _load_config_raw()
    enabled_cfg = cfg.get("enabled_hooks", {}) if isinstance(cfg.get("enabled_hooks"), dict) else {}
    out = []
    for h in HOOK_CATALOG:
        out.append({
            "name": h["name"],
            "enabled": _hook_enabled(enabled_cfg, h["name"]),
            "default": h["default"],
            "audio_file": h["audio"],
            "description": h["description"],
            "is_variant": False,
        })
    if not include_variants:
        return out
    for var in _variant_catalog():
        out.append({
            "name": var["name"],
            "enabled": _hook_enabled(enabled_cfg, var["variant_of"], var["name"]),
            "default": var["default"],
            "audio_file": var["audio_file"],
            "description": f"Matcher variant of {var['variant_of']}",
            "is_variant": True,
            "variant_of": var["variant_of"],
        })
    return out


def _variant_names() -> set:
    return {v["name"] for v in _variant_catalog()}


def _variant_status_summary(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Which matcher variants the user has explicitly overridden.

    ``status.enabled_hooks`` lists canonical hooks only, so a variant override
    is otherwise invisible: an agent asked "notification is on, why is there no
    idle-prompt sound?" would have nothing to go on.
    """
    enabled_cfg = cfg.get("enabled_hooks", {})
    if not isinstance(enabled_cfg, dict):
        enabled_cfg = {}
    catalog = _variant_catalog()
    overridden = {v["name"]: enabled_cfg[v["name"]]
                  for v in catalog if isinstance(enabled_cfg.get(v["name"]), bool)}
    return {
        "total": len(catalog),
        "overridden_count": len(overridden),
        "overridden": overridden,
        "hint": "audio-hooks hooks list --variants",
    }


def cmd_hooks(args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    if not args:
        return emit_error("INVALID_USAGE", "Usage: audio-hooks hooks <list|enable|disable|enable-only> [name...]")
    sub = args[0]
    rest = args[1:]
    if sub in ("enable", "disable", "enable-only"):
        rc = _check_args(f"hooks {sub}", rest)
        if rc is not None:
            return rc
    if sub == "list":
        # Variants are opt-in: `hooks list` is read by AI agents, and tripling
        # the row count by default would cost every caller context it does not
        # need. They surface under their own key so callers that index "hooks"
        # see no shape change.
        if "--variants" in rest:
            rows = _hooks_state(include_variants=True)
            emit({"ok": True,
                  "hooks": [r for r in rows if not r["is_variant"]],
                  "variants": [r for r in rows if r["is_variant"]]})
        else:
            emit({"ok": True, "hooks": _hooks_state()})
        return 0
    if sub in ("enable", "disable"):
        if not rest:
            return emit_error("INVALID_USAGE", f"Usage: audio-hooks hooks {sub} <name> [name ...]")
        valid = {h["name"] for h in HOOK_CATALOG} | _variant_names()
        # v6.6: every name given is applied (it used to act on the first and
        # return ok for the rest). All-or-nothing: validate them all first.
        for name in rest:
            if name not in valid:
                return emit_error("UNKNOWN_HOOK_TYPE", f"Unknown hook: {name}. Nothing was changed.", hint="Run `audio-hooks hooks list --variants` to see all hooks and matcher variants.", suggested_command="audio-hooks hooks list --variants")
        cfg = _load_config_raw()
        eh = cfg.setdefault("enabled_hooks", {})
        for name in rest:
            eh[name] = (sub == "enable")
        ok, err = _save_config_raw(cfg)
        if not ok:
            return emit_error("CONFIG_READ_ERROR", err)
        emit({"ok": True, "hook": rest[0], "hooks": list(rest), "enabled": sub == "enable"})
        return 0
    if sub == "enable-only":
        if not rest:
            return emit_error("INVALID_USAGE", "Usage: audio-hooks hooks enable-only <name1> [name2 ...]")
        variants = {v["name"]: v["variant_of"] for v in _variant_catalog()}
        valid = {h["name"] for h in HOOK_CATALOG} | set(variants)
        for n in rest:
            if n not in valid:
                return emit_error("UNKNOWN_HOOK_TYPE", f"Unknown hook: {n}", suggested_command="audio-hooks hooks list --variants")

        wanted_variants = [n for n in rest if n in variants]
        wanted_hooks = [n for n in rest if n not in variants]
        # A named variant needs its parent left on: a disabled parent is a hard
        # kill switch (is_hook_enabled rule 2) and would silence the very thing
        # the user just asked for.
        parents_of_variants = {variants[n] for n in wanted_variants}

        cfg = _load_config_raw()
        eh = cfg.setdefault("enabled_hooks", {})
        for h in HOOK_CATALOG:
            eh[h["name"]] = h["name"] in wanted_hooks or h["name"] in parents_of_variants
        # Only touch variant keys under a parent the user named a variant of;
        # writing all ~26 would bury the user's file in noise.
        disabled_variants = []
        for name, parent in variants.items():
            if parent not in parents_of_variants:
                continue
            eh[name] = name in wanted_variants
            if name not in wanted_variants:
                disabled_variants.append(name)

        ok, err = _save_config_raw(cfg)
        if not ok:
            return emit_error("CONFIG_READ_ERROR", err)
        emit({"ok": True,
              "enabled": list(rest),
              "disabled": [h["name"] for h in HOOK_CATALOG
                           if h["name"] not in wanted_hooks
                           and h["name"] not in parents_of_variants],
              "disabled_variants": sorted(disabled_variants),
              "parents_kept_enabled": sorted(parents_of_variants)})
        return 0
    return emit_error("INVALID_USAGE", f"Unknown hooks subcommand: {sub}")


# ---------------------------------------------------------------------------
# Subcommand: theme
# ---------------------------------------------------------------------------

def cmd_theme(args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    if not args or args[0] == "list":
        emit({"ok": True, "current": _load_config_raw().get("audio_theme", "default"), "available": ["default", "custom"]})
        return 0
    if args[0] == "set":
        if len(args) < 2:
            return emit_error("INVALID_USAGE", "Usage: audio-hooks theme set <default|custom>")
        rc = _check_args("theme set", args[1:], max_positionals=1)
        if rc is not None:
            return rc
        theme = args[1]
        if theme not in ("default", "custom"):
            return emit_error("INVALID_USAGE", f"Invalid theme: {theme}")
        cfg = _load_config_raw()
        cfg["audio_theme"] = theme
        ok, err = _save_config_raw(cfg)
        if not ok:
            return emit_error("CONFIG_READ_ERROR", err)
        emit({"ok": True, "theme": theme})
        return 0
    return emit_error("INVALID_USAGE", f"Unknown theme subcommand: {args[0]}")


# ---------------------------------------------------------------------------
# Subcommand: snooze
# ---------------------------------------------------------------------------

def cmd_snooze(args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    rc = _check_args("snooze", args, max_positionals=1)
    if rc is not None:
        return rc
    arg = args[0] if args else "30m"
    sf = _snooze_file()
    if arg == "status":
        emit({"ok": True, **_snooze_status()})
        return 0
    sf.parent.mkdir(parents=True, exist_ok=True)
    if arg in ("off", "resume", "cancel"):
        try:
            sf.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            return emit_error("INTERNAL_ERROR", str(e))
        emit({"ok": True, "active": False})
        return 0
    secs = _parse_duration(arg)
    if secs is None or secs <= 0:
        return emit_error("INVALID_USAGE", f"Invalid duration: {arg}", hint="Use forms like 30m, 1h, 90s, 2d.")
    until = time.time() + secs
    try:
        sf.write_text(str(until), encoding="utf-8")
    except OSError as e:
        return emit_error("INTERNAL_ERROR", str(e))
    emit({
        "ok": True,
        "active": True,
        "remaining_seconds": secs,
        "until": until,
        "until_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(until)),
    })
    return 0


# ---------------------------------------------------------------------------
# Subcommand: webhook
# ---------------------------------------------------------------------------

def cmd_webhook(args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    if not args:
        cfg = _load_config_raw()
        w = cfg.get("webhook_settings", {})
        emit({
            "ok": True,
            "enabled": bool(w.get("enabled")),
            "format": w.get("format", "raw"),
            "url_redacted": _redact_url(w.get("url", "")),
            "hook_types": w.get("hook_types", []),
        })
        return 0
    sub = args[0]
    rest = args[1:]
    if sub in ("set", "clear", "test"):
        rc = _check_args(
            f"webhook {sub}", rest,
            valued=("--url", "--format", "--hook-types", "--enabled") if sub == "set" else (),
            max_positionals=0,
        )
        if rc is not None:
            return rc
    if sub == "set":
        # Parse --url, --format, --hook-types flags
        parsed: Dict[str, Any] = {}
        i = 0
        while i < len(rest):
            tok = rest[i]
            if tok == "--url" and i + 1 < len(rest):
                parsed["url"] = rest[i + 1]; i += 2; continue
            if tok == "--format" and i + 1 < len(rest):
                parsed["format"] = rest[i + 1]; i += 2; continue
            if tok == "--hook-types" and i + 1 < len(rest):
                parsed["hook_types"] = [s.strip() for s in rest[i + 1].split(",") if s.strip()]
                i += 2; continue
            if tok == "--enabled" and i + 1 < len(rest):
                parsed["enabled"] = rest[i + 1].lower() in ("true", "1", "yes")
                i += 2; continue
            i += 1
        cfg = _load_config_raw()
        w = cfg.setdefault("webhook_settings", {})
        if not parsed:
            # v6.6: nothing to change means nothing to write (a save also
            # overwrites the .bak with the current file).
            emit({"ok": True, "webhook_settings": {"enabled": bool(w.get("enabled")), "format": w.get("format", "raw"), "url_redacted": _redact_url(w.get("url", ""))}})
            return 0
        for k, v in parsed.items():
            w[k] = v
        if "url" in parsed and parsed["url"]:
            w["enabled"] = True
        ok, err = _save_config_raw(cfg)
        if not ok:
            return emit_error("CONFIG_READ_ERROR", err)
        emit({"ok": True, "webhook_settings": {"enabled": bool(w.get("enabled")), "format": w.get("format", "raw"), "url_redacted": _redact_url(w.get("url", ""))}})
        return 0
    if sub == "clear":
        cfg = _load_config_raw()
        w = cfg.setdefault("webhook_settings", {})
        w["enabled"] = False
        w["url"] = ""
        ok, err = _save_config_raw(cfg)
        if not ok:
            return emit_error("CONFIG_READ_ERROR", err)
        emit({"ok": True, "enabled": False})
        return 0
    if sub == "test":
        cfg = _load_config_raw()
        w = cfg.get("webhook_settings", {})
        url = w.get("url", "")
        if not url:
            return emit_error("INVALID_CONFIG", "No webhook URL configured.", suggested_command="audio-hooks webhook set --url ...")
        try:
            import urllib.request
            payload = json.dumps({
                "schema": "audio-hooks.webhook.v1",
                "test": True,
                "ts": time.time(),
                "version": PROJECT_VERSION,
            }).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            resp = urllib.request.urlopen(req, timeout=5)
            emit({"ok": True, "status": resp.status, "url_redacted": _redact_url(url)})
            return 0
        except Exception as e:
            return emit_error("WEBHOOK_HTTP_ERROR", str(e), url_redacted=_redact_url(url))
    return emit_error("INVALID_USAGE", f"Unknown webhook subcommand: {sub}")


# ---------------------------------------------------------------------------
# Subcommand: tts / rate-limits
# ---------------------------------------------------------------------------

def _kv_flags(rest: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    i = 0
    while i < len(rest):
        if rest[i].startswith("--") and i + 1 < len(rest):
            key = rest[i][2:].replace("-", "_")
            out[key] = _coerce_value(rest[i + 1])
            i += 2
        else:
            i += 1
    return out


# The keys `tts set` / `rate-limits set` may write, mirroring the
# tts_settings / rate_limit_alerts objects in the preferences schema.
def _both_spellings(*flags: str) -> Tuple[str, ...]:
    """``--five-hour-thresholds`` and ``--five_hour_thresholds``: HEAD's
    ``_kv_flags`` turned either into the same key, so both stay accepted."""
    out: List[str] = []
    for f in flags:
        out.extend((f, "--" + f[2:].replace("-", "_")))
    return tuple(dict.fromkeys(out))


_TTS_SET_FLAGS = _both_spellings("--enabled", "--speak-assistant-message", "--assistant-message-max-chars", "--messages")
_RATE_LIMITS_SET_FLAGS = _both_spellings("--enabled", "--five-hour-thresholds", "--seven-day-thresholds", "--audio")


def _threshold_list(value: Any) -> List[int]:
    """Normalise a thresholds value to a list of ints (raises ValueError/TypeError)."""
    if isinstance(value, bool):
        raise TypeError("thresholds must be numbers")
    if isinstance(value, (int, float)):
        return [int(value)]
    if isinstance(value, str):
        return [int(x.strip()) for x in value.split(",") if x.strip()]
    if isinstance(value, (list, tuple)):
        return [int(x) for x in value]
    raise TypeError("thresholds must be a number, a comma-separated string or a list")


def cmd_tts(args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    if not args or args[0] == "set":
        rest = args[1:] if args else []
        rc = _check_args("tts set", rest, valued=_TTS_SET_FLAGS, max_positionals=0)
        if rc is not None:
            return rc
        flags = _kv_flags(rest)
        cfg = _load_config_raw()
        if not flags:
            # v6.6: bare `tts` only displays. It used to save, which also
            # overwrote the .bak with the current file.
            emit({"ok": True, "tts_settings": cfg.get("tts_settings", {})})
            return 0
        t = cfg.setdefault("tts_settings", {})
        for k, v in flags.items():
            t[k] = v
        ok, err = _save_config_raw(cfg)
        if not ok:
            return emit_error("CONFIG_READ_ERROR", err)
        emit({"ok": True, "tts_settings": t})
        return 0
    return emit_error("INVALID_USAGE", f"Unknown tts subcommand: {args[0]}")


def cmd_rate_limits(args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    if not args or args[0] == "set":
        rest = args[1:] if args else []
        rc = _check_args("rate-limits set", rest, valued=_RATE_LIMITS_SET_FLAGS, max_positionals=0)
        if rc is not None:
            return rc
        flags = _kv_flags(rest)
        cfg = _load_config_raw()
        if not flags:
            emit({"ok": True, "rate_limit_alerts": cfg.get("rate_limit_alerts", {})})
            return 0
        r = cfg.setdefault("rate_limit_alerts", {})
        new_values: Dict[str, Any] = {}
        for k, v in flags.items():
            if k in ("five_hour_thresholds", "seven_day_thresholds"):
                # v6.6: always a list. `--five-hour-thresholds 90` used to store
                # the integer 90, which check_rate_limits then tried to sort().
                try:
                    v = _threshold_list(v)
                except (TypeError, ValueError, OverflowError):
                    return emit_error("INVALID_USAGE", f"--{k.replace('_', '-')} needs whole numbers such as 80,95. Nothing was changed.",
                                      suggested_command="audio-hooks rate-limits set --five-hour-thresholds 80,95")
            new_values[k] = v
        r.update(new_values)
        ok, err = _save_config_raw(cfg)
        if not ok:
            return emit_error("CONFIG_READ_ERROR", err)
        emit({"ok": True, "rate_limit_alerts": r})
        return 0
    return emit_error("INVALID_USAGE", f"Unknown rate-limits subcommand: {args[0]}")


# ---------------------------------------------------------------------------
# Subcommand: test
# ---------------------------------------------------------------------------

_MOCK_STDIN: Dict[str, Dict[str, Any]] = {
    "stop": {"hook_event_name": "Stop", "last_assistant_message": "Test complete.", "session_id": "test-session"},
    "notification": {"hook_event_name": "Notification", "message": "Test notification", "notification_type": "permission_prompt", "session_id": "test-session"},
    "permission_request": {"hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "echo test"}, "session_id": "test-session"},
    "permission_denied": {"hook_event_name": "PermissionDenied", "tool_name": "Bash", "reason": "auto mode classifier", "session_id": "test-session"},
    "subagent_stop": {"hook_event_name": "SubagentStop", "agent_type": "Explore", "last_assistant_message": "Done.", "session_id": "test-session"},
    "session_start": {"hook_event_name": "SessionStart", "source": "startup", "session_id": "test-session"},
    "cwd_changed": {"hook_event_name": "CwdChanged", "new_cwd": "/tmp", "session_id": "test-session"},
    "directory_added": {"hook_event_name": "DirectoryAdded", "directory": "/tmp/added", "source": "slash_command", "session_id": "test-session"},
    "worktree_remove": {"hook_event_name": "WorktreeRemove", "worktree_path": "/tmp/wt", "session_id": "test-session"},
    "file_changed": {"hook_event_name": "FileChanged", "file_path": "/tmp/.env", "session_id": "test-session"},
    "task_created": {"hook_event_name": "TaskCreated", "task_subject": "Test task", "session_id": "test-session"},
}


def _mock_for(hook_name: str) -> Dict[str, Any]:
    return _MOCK_STDIN.get(hook_name, {"hook_event_name": hook_name, "session_id": "test-session"})


def _run_one_test(hook_name: str) -> Dict[str, Any]:
    """Invoke hook_runner.run_hook with a synthetic stdin payload."""
    if HR is None:
        return {"hook": hook_name, "ok": False, "error": "hook_runner not importable"}
    start = time.time()
    try:
        rc = HR.run_hook(hook_name, _mock_for(hook_name))
        elapsed_ms = int((time.time() - start) * 1000)
        return {"hook": hook_name, "ok": rc == 0, "exit_code": rc, "duration_ms": elapsed_ms}
    except Exception as e:
        return {"hook": hook_name, "ok": False, "error": str(e)}


def cmd_test(args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    # v6.7: `test stop --dry-run` used to be accepted and then played the sound
    # (the flag was never read); like every other subcommand, an argument the
    # grammar does not define is an error and nothing runs.
    rc = _check_args("test", args, max_positionals=1)
    if rc is not None:
        return rc
    if not args:
        return emit_error("INVALID_USAGE", "Usage: audio-hooks test <hook_name|all>",
                          suggested_command="audio-hooks hooks list")
    target = args[0]
    if target == "all":
        results = [_run_one_test(h["name"]) for h in HOOK_CATALOG]
        passed = [r for r in results if r.get("ok")]
        failed = [r for r in results if not r.get("ok")]
        emit({"ok": len(failed) == 0, "passed": len(passed), "failed": failed, "total": len(results)})
        return 0 if not failed else 1
    valid = {h["name"] for h in HOOK_CATALOG}
    if target not in valid:
        return emit_error("UNKNOWN_HOOK_TYPE", f"Unknown hook: {target}", suggested_command="audio-hooks hooks list")
    result = _run_one_test(target)
    emit({"ok": result.get("ok", False), **result})
    return 0 if result.get("ok") else 1


# ---------------------------------------------------------------------------
# Subcommand: diagnose
# ---------------------------------------------------------------------------

def _detect_audio_player() -> Dict[str, Any]:
    sysname = platform.system()
    import shutil as _sh
    if sysname == "Windows":
        return {"platform": sysname, "player": "powershell-mediaplayer", "available": bool(_sh.which("powershell.exe") or _sh.which("powershell"))}
    if sysname == "Darwin":
        return {"platform": sysname, "player": "afplay", "available": bool(_sh.which("afplay"))}
    candidates = ["mpg123", "ffplay", "paplay", "aplay"]
    found = next((c for c in candidates if _sh.which(c)), None)
    return {"platform": sysname, "player": found, "available": found is not None}


def _check_settings_json() -> Dict[str, Any]:
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        return {"path": str(settings_path), "exists": False}
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"path": str(settings_path), "exists": True, "parse_error": str(e)}
    return {
        "path": str(settings_path),
        "exists": True,
        "disable_all_hooks": bool(data.get("disableAllHooks")),
        "disable_skill_shell_execution": bool(data.get("disableSkillShellExecution")),
        "hooks_registered": isinstance(data.get("hooks"), dict) and bool(data.get("hooks")),
        # v6.4.1: Claude Code delivers its OWN terminal notification on the same
        # events echook plays a sound for. Anything other than
        # "notifications_disabled" means the user hears/sees both.
        "preferred_notif_channel": data.get("preferredNotifChannel"),
    }


def _check_codex_managed_hooks_only() -> Dict[str, Any]:
    """Detect the enterprise setting that silently ignores a user hooks.json.

    Codex's managed ``requirements.toml`` may set ``allow_managed_hooks_only``.
    When it does, ``$CODEX_HOME/hooks.json`` — exactly what
    ``audio-hooks install --codex`` writes — is ignored with no error at all.
    The install reports success and echook is then permanently mute, which is
    indistinguishable from a bug in echook unless you know to look here.
    """
    codex_home = _codex_home()
    for name in ("requirements.toml", "managed_config.toml"):
        path = codex_home / name
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        # Deliberately a text scan, not a TOML parse: tomllib is 3.11+ and this
        # only needs to spot one boolean in a file we never write.
        match = re.search(
            r"^\s*allow_managed_hooks_only\s*=\s*(true|false)\s*$",
            text,
            re.MULTILINE | re.IGNORECASE,
        )
        if match and match.group(1).lower() == "true":
            return {"active": True, "path": str(path)}
    return {"active": False}


def _check_codex_hooks_file_keys() -> Dict[str, Any]:
    """Find top-level keys that make Codex reject ``$CODEX_HOME/hooks.json``.

    Codex accepts only ``description`` and ``hooks`` at the top level and
    drops the whole file on anything else, so every hook in it goes silent.
    ``install --codex`` up to 7.0.0 wrote ``_comment`` and friends there.
    """
    path = _codex_home() / "hooks.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"rejected": False}
    if not isinstance(doc, dict):
        return {"rejected": False}
    unknown = sorted(k for k in doc if k not in ("description", "hooks"))
    return {"rejected": bool(unknown), "keys": unknown, "path": str(path)}


def _check_audio_files() -> Dict[str, Any]:
    if PROJECT_ROOT is None:
        return {"missing": [], "present": 0}
    audio_dir = PROJECT_ROOT / "audio"
    missing = []
    present = 0
    cfg = _load_config_raw()
    theme = cfg.get("audio_theme", "default")
    for h in HOOK_CATALOG:
        # Check both themes' file existence
        default_p = audio_dir / "default" / h["audio"]
        custom_p = audio_dir / "custom" / ("chime-" + h["audio"])
        active = custom_p if theme == "custom" else default_p
        if active.exists():
            present += 1
        else:
            missing.append({"hook": h["name"], "expected": str(active)})
    return {"missing": missing, "present": present, "expected": len(HOOK_CATALOG)}


# Keys the product removed but which linger in configs written before the
# migration gate was repaired. Their presence proves the config never migrated,
# which also means every key added since is absent and silently defaulted.
_RETIRED_CONFIG_KEYS = ("focus_flow",)

# The only three hooks that can tell a user "the work is finished". With none of
# them on, echook is installed, healthy, and audibly does nothing for the thing
# it is installed for — a state indistinguishable from broken, and the exact
# shape that sent one user hunting through the Claude Code binary for a
# regression that did not exist.
_COMPLETION_SIGNAL_HOOKS = ("stop", "subagent_stop", "notification")


def _check_completion_signal(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Report whether any hook capable of signalling "done" is enabled."""
    enabled_hooks = cfg.get("enabled_hooks") or {}
    if not isinstance(enabled_hooks, dict):
        return {"any_enabled": True, "checked": []}
    # Decided by the runner's own rule (and its DEFAULT_ENABLED_HOOKS): an absent
    # subagent_stop key is off, not on, because it is not in that default set.
    on = [h for h in _COMPLETION_SIGNAL_HOOKS if _hook_enabled(enabled_hooks, h)]
    return {"any_enabled": bool(on), "enabled": on, "checked": list(_COMPLETION_SIGNAL_HOOKS)}


def _check_terminal_sequence_inert(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """terminalSequence is enabled but cannot fire, because our hooks are async.

    Claude Code writes a hook's ``terminalSequence`` from exactly one function,
    and every call site for it sits on a *synchronous* completion path — the
    one that has already collected the hook's stdout and exit status. A hook
    declared ``"async": true`` is backgrounded instead; its stdout is read, but
    the result is routed to the model-response attachment path, which never
    emits the escape. Every handler in the Claude Code template is async (it
    has to be — synchronous plugin hooks can hang Claude Code's startup on
    Windows, claude-plugins-official#351), so the feature is inert as shipped
    in v6.5.0.
    """
    ts = ((cfg.get("notification_settings") or {}).get("terminal_sequence") or {})
    enabled = ts.get("enabled") is True if isinstance(ts, dict) else False
    return {"enabled": enabled, "inert": enabled}


def _check_prefs_schema() -> Dict[str, Any]:
    """Detect a user_preferences.json on disk that never migrated.

    Before 6.5.1 the migration ran only when the config's ``_version`` differed
    from the template's, and the template's own ``_version`` had been left at
    5.1.5 for four minor releases. Equal strings meant no migration, so configs
    froze: no key added since 5.1.5 ever landed, and keys the product dropped
    were never cleaned up.

    Reads the file directly rather than ``_load_config_raw()`` — that overlays
    the template, so its ``_version`` always matches and the drift this exists
    to catch would be invisible.
    """
    try:
        path = _config_path()
    except Exception:
        return {"stale": False}
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"stale": False, "config_path": str(path)}
    if not isinstance(cfg, dict):
        return {"stale": False, "config_path": str(path)}
    version = cfg.get("_version") or cfg.get("version")
    retired = [k for k in _RETIRED_CONFIG_KEYS if k in cfg]
    stale = bool(retired) or (version is not None and version != PROJECT_VERSION)
    return {
        "stale": stale,
        "config_path": str(path),
        "config_version": version,
        "project_version": PROJECT_VERSION,
        "retired_keys": retired,
    }


def _check_plugin_record() -> Dict[str, Any]:
    """Compare the installed-plugin record against the code actually running.

    Two distinct failures share this shape. The benign one is an ordinary stale
    record after the plugin moved to a directory-source marketplace. The other
    is anthropics/claude-code#90135: a marketplace re-materialisation deletes
    the versioned cache path that live sessions are pinned to, and every one of
    their plugin hooks stops firing with no error anywhere — including the
    plugin's own hooks, which would be what reported it.
    """
    installed_json = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    result: Dict[str, Any] = {"checked": str(installed_json), "records": []}
    try:
        data = json.loads(installed_json.read_text(encoding="utf-8"))
    except Exception:
        return result
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return result

    live_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    for key, entries in plugins.items():
        if not key.lower().startswith("audio-hooks@"):
            continue
        for entry in (entries if isinstance(entries, list) else [entries]):
            if not isinstance(entry, dict):
                continue
            install_path = entry.get("installPath") or ""
            record = {
                "id": key,
                "scope": entry.get("scope"),
                "recorded_version": entry.get("version"),
                "install_path": install_path,
                "path_exists": bool(install_path) and Path(install_path).exists(),
            }
            record["version_drift"] = (
                record["recorded_version"] is not None
                and record["recorded_version"] != PROJECT_VERSION
            )
            result["records"].append(record)

    result["live_plugin_root"] = live_root
    result["stale"] = any(
        r["version_drift"] or not r["path_exists"] for r in result["records"]
    )
    return result


def _check_hook_shell() -> Dict[str, Any]:
    """On Windows, plugin hook commands run through Git Bash unless told otherwise.

    Claude Code runs a command hook through bash by default, falling back to
    PowerShell on Windows only when Git Bash is absent — and 2.1.251 refuses a
    bash hook outright in that case ("requires bash but Git Bash was not
    found"). echook's template is written for a POSIX shell, so a Windows box
    without Git Bash loses every handler at once rather than one.
    """
    if platform.system() != "Windows":
        return {"applicable": False}
    bash = shutil.which("bash")
    return {"applicable": True, "git_bash": bash, "available": bool(bash)}


def cmd_diagnose(_args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    settings = _check_settings_json()
    install = _detect_install_mode()
    if settings.get("disable_all_hooks"):
        errors.append({
            "code": "SETTINGS_DISABLE_ALL_HOOKS",
            "message": "Claude Code settings.json has disableAllHooks: true; no hooks will fire.",
            "hint": "Remove or set disableAllHooks: false in ~/.claude/settings.json.",
            "suggested_command": "audio-hooks status",
        })
    # Only warn HOOKS_NOT_REGISTERED when neither install path is active.
    # Plugin installs register their hooks in the plugin's own hooks/hooks.json,
    # not in ~/.claude/settings.json — so an absent settings.json `hooks` key
    # is normal and expected when only the plugin is installed.
    if (settings.get("exists")
            and not settings.get("hooks_registered")
            and not install.get("plugin_install")
            and not install.get("script_install")):
        warnings.append({
            "code": "HOOKS_NOT_REGISTERED",
            "message": "No hooks block found in ~/.claude/settings.json and no plugin install detected.",
            "suggested_command": "audio-hooks install --plugin",
        })

    notif_channel = settings.get("preferred_notif_channel")
    if notif_channel and notif_channel != "notifications_disabled":
        warnings.append({
            "code": "NATIVE_NOTIFICATIONS_ACTIVE",
            "message": (
                "Claude Code's own notifications are on "
                f"(preferredNotifChannel: {notif_channel}), so it signals the same "
                "events echook does — expect a double bell or a duplicate toast."
            ),
            "hint": (
                "Keep both if you want belt-and-braces. To hear only echook, set "
                "preferredNotifChannel to \"notifications_disabled\" in "
                "~/.claude/settings.json. To hear only Claude Code, run "
                "audio-hooks hooks disable notification."
            ),
            "suggested_command": "audio-hooks hooks list",
        })

    codex_managed = _check_codex_managed_hooks_only()
    if codex_managed.get("active"):
        warnings.append({
            "code": "CODEX_MANAGED_HOOKS_ONLY",
            "message": (
                "Codex is configured with allow_managed_hooks_only, so "
                "$CODEX_HOME/hooks.json is ignored. A native --codex install "
                "reports success and then never fires."
            ),
            "hint": (
                "Ask whoever owns "
                f"{codex_managed.get('path')} to allow user hooks, or install "
                "echook through the Codex plugin marketplace instead."
            ),
            "suggested_command": "audio-hooks install --codex",
        })

    codex_keys = _check_codex_hooks_file_keys()
    if codex_keys.get("rejected"):
        errors.append({
            "code": "CODEX_HOOKS_FILE_REJECTED",
            "message": (
                f"Codex refuses to load {codex_keys.get('path')}: it has top-level "
                f"keys other than description and hooks ({', '.join(codex_keys['keys'])}), "
                "so no hook in that file runs."
            ),
            "hint": (
                "Re-run audio-hooks install --codex; it removes the keys echook "
                "wrote up to 7.0.0. Move any key you added yourself out of the file."
            ),
            "suggested_command": "audio-hooks install --codex",
        })

    audio_player = _detect_audio_player()
    if not audio_player.get("available"):
        errors.append({
            "code": "AUDIO_PLAYER_NOT_FOUND",
            "message": f"No audio player available on {audio_player.get('platform')}.",
            "hint": "Install mpg123 (Linux) or ensure PowerShell is available (Windows).",
            "suggested_command": "audio-hooks diagnose",
        })

    audio_files = _check_audio_files()
    if audio_files["missing"]:
        warnings.append({
            "code": "AUDIO_FILE_MISSING",
            "message": f"{len(audio_files['missing'])} audio files missing for the active theme.",
            "hint": "Some hooks will be silent. Switch themes or restore the files.",
            "suggested_command": "audio-hooks theme list",
            "missing_count": len(audio_files["missing"]),
        })

    cfg = _load_config_raw()
    if not cfg:
        warnings.append({
            "code": "INVALID_CONFIG",
            "message": "user_preferences.json is missing or empty.",
            "suggested_command": "audio-hooks manifest --schema",
        })

    if install.get("warning", {}).get("code") == "DUAL_INSTALL_DETECTED":
        errors.append({
            "code": "DUAL_INSTALL_DETECTED",
            "message": install["warning"]["message"],
            "hint": "Both the script install and the plugin install fire on every event, causing duplicate audio.",
            "suggested_command": _script_uninstall_remedy()[1],
        })

    editor_targets = _detect_editor_targets()
    if editor_targets.get("cursor", {}).get("state") == "double-registered":
        errors.append({
            "code": "DUPLICATE_BRIDGE",
            "message": editor_targets["cursor"]["warning"]["message"],
            "hint": "Cursor is fed by both Claude Code's auto-bridge AND ~/.cursor/hooks.json — every event fires twice.",
            "suggested_command": "audio-hooks uninstall --cursor",
        })

    completion_signal = _check_completion_signal(cfg)
    if not completion_signal["any_enabled"]:
        warnings.append({
            "code": "NO_COMPLETION_SIGNAL",
            "message": (
                "None of stop, subagent_stop or notification is enabled, so no "
                "hook can tell you a turn finished. echook is healthy and will "
                "stay silent for the thing most people install it for."
            ),
            "hint": (
                "stop fires at the end of EVERY turn and carries no finality "
                "marker, which is why it gets muted. Re-enable it together with "
                "the background-task filter so it only sounds once the "
                "subagents are done, or enable notification and rely on its "
                "idle_prompt variant, which is the genuine \"waiting for you\" "
                "signal."
            ),
            "suggested_command": (
                "audio-hooks hooks enable stop && audio-hooks set "
                "filters.stop.skip_if_background_tasks_running true"
            ),
        })

    terminal_sequence = _check_terminal_sequence_inert(cfg)
    if terminal_sequence.get("inert"):
        warnings.append({
            "code": "TERMINAL_SEQUENCE_INERT",
            "message": (
                "notification_settings.terminal_sequence.enabled is true, but "
                "no escape will ever be emitted: Claude Code only writes a "
                "hook's terminalSequence from a synchronous completion path, "
                "and every echook handler is registered async."
            ),
            "hint": (
                "Use the desktop-notification channel instead "
                "(notification_settings.mode = audio_and_notification), which "
                "sends a real OS toast. Tracked for a future release; dropping "
                "async would risk the Windows startup hang that made every "
                "handler async in the first place."
            ),
            "suggested_command": "audio-hooks set notification_settings.terminal_sequence.enabled false",
        })

    prefs_schema = _check_prefs_schema()
    if prefs_schema.get("stale"):
        warnings.append({
            "code": "PREFS_SCHEMA_STALE",
            "message": (
                "user_preferences.json is stamped "
                f"{prefs_schema.get('config_version')} against a "
                f"{prefs_schema.get('project_version')} install"
                + (
                    " and still carries keys this version removed ("
                    + ", ".join(prefs_schema["retired_keys"]) + ")"
                    if prefs_schema.get("retired_keys") else ""
                )
                + ". Keys added since are absent and silently defaulted."
            ),
            "hint": (
                "Before 6.5.1 migration ran only when the config's _version "
                "differed from the template's, and the template's was left at "
                "5.1.5 for four releases — so equal strings meant no migration "
                "ever ran. The next hook event migrates it (the hook runner and "
                "every state-changing command do); read-only commands such as "
                "status and diagnose deliberately do not. To do it now, run "
                "`audio-hooks migrate`."
            ),
            "suggested_command": "audio-hooks migrate",
            "retired_keys": prefs_schema.get("retired_keys", []),
        })

    plugin_record = _check_plugin_record()
    if plugin_record.get("stale"):
        missing = [r for r in plugin_record["records"] if not r["path_exists"]]
        warnings.append({
            "code": "STALE_PLUGIN_CACHE",
            "message": (
                "installed_plugins.json records a version or install path that "
                "is not the code now running"
                + (
                    f"; {len(missing)} recorded install path(s) no longer exist"
                    if missing else ""
                )
                + "."
            ),
            "hint": (
                "Harmless when the marketplace is a directory source, since "
                "${CLAUDE_PLUGIN_ROOT} then resolves to the checkout. It is not "
                "harmless when the path is gone: per anthropics/claude-code"
                "#90135 a re-materialised marketplace deletes the versioned "
                "path live sessions are pinned to, and their plugin hooks stop "
                "firing silently. Reload plugins or restart to re-pin."
            ),
            "suggested_command": "audio-hooks status",
            "records": plugin_record["records"],
        })

    hook_shell = _check_hook_shell()
    if hook_shell.get("applicable") and not hook_shell.get("available"):
        warnings.append({
            "code": "WINDOWS_NO_GIT_BASH",
            "message": (
                "Windows without Git Bash on PATH. Claude Code runs command "
                "hooks through bash by default and refuses them outright when "
                "it is missing, so every echook handler fails at once."
            ),
            "hint": (
                "Install Git for Windows (https://git-scm.com/downloads/win). "
                "Claude Code reports this itself as \"requires bash but Git "
                "Bash was not found\"."
            ),
            "suggested_command": "audio-hooks diagnose",
        })

    emit({
        "ok": len(errors) == 0,
        "version": PROJECT_VERSION,
        "platform": platform.system(),
        "platform_release": platform.release(),
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "project_dir": str(PROJECT_ROOT),
        "settings_json": settings,
        "audio_player": audio_player,
        "audio_files": audio_files,
        "install": install,
        "editor_targets": editor_targets,
        "completion_signal": completion_signal,
        "terminal_sequence": terminal_sequence,
        "prefs_schema": prefs_schema,
        "plugin_record": plugin_record,
        "hook_shell": hook_shell,
        "errors": errors,
        "warnings": warnings,
    })
    return 0 if not errors else 1


# ---------------------------------------------------------------------------
# Subcommand: logs tail / clear
# ---------------------------------------------------------------------------

def cmd_logs(args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    if not args:
        return emit_error("INVALID_USAGE", "Usage: audio-hooks logs <tail|clear>")
    sub = args[0]
    if sub not in ("tail", "clear"):
        return emit_error("INVALID_USAGE", f"Unknown logs subcommand: {sub}")
    if sub == "clear":
        # Validate before touching anything: a rejected call says "Nothing was
        # changed" and must not have created the data or log directory.
        rc = _check_args("logs clear", args[1:], max_positionals=0)
        if rc is not None:
            return rc
    # Neither subcommand needs the log directory to exist (tail of a missing log
    # is empty, clear of a missing log is a no-op), so take the path without
    # the mkdir that HR.runner_core.get_log_dir() performs.
    log_file = (_prefs().log_dir if PROJECT_ROOT is not None else _log_dir()) / "events.ndjson"
    if sub == "clear":
        try:
            if log_file.exists():
                log_file.unlink()
        except OSError as e:
            return emit_error("INTERNAL_ERROR", str(e))
        emit({"ok": True, "cleared": True, "file": str(log_file)})
        return 0
    if sub == "tail":
        n = 50
        level_filter: Optional[str] = None
        i = 1
        while i < len(args):
            if args[i] == "--n" and i + 1 < len(args):
                try:
                    n = int(args[i + 1])
                except ValueError:
                    return emit_error("INVALID_USAGE", "--n requires an integer")
                i += 2; continue
            if args[i] == "--level" and i + 1 < len(args):
                level_filter = args[i + 1]
                i += 2; continue
            i += 1
        if not log_file.exists():
            emit({"ok": True, "events": [], "file": str(log_file)})
            return 0
        try:
            lines = log_file.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            return emit_error("INTERNAL_ERROR", str(e))
        events: List[Dict[str, Any]] = []
        for line in lines[-max(n * 4, n):]:  # over-read in case of filter
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if level_filter and ev.get("level") != level_filter:
                continue
            events.append(ev)
        emit({"ok": True, "file": str(log_file), "events": events[-n:]})
        return 0
    return emit_error("INVALID_USAGE", f"Unknown logs subcommand: {sub}")


# ---------------------------------------------------------------------------
# Subcommand: install / uninstall
# ---------------------------------------------------------------------------

# v6.6: install/uninstall used to default to the legacy script installer and
# silently ignore any argument they did not recognise, so `install --help`,
# `install --bogus` and a bare `install` all rewrote ~/.claude/settings.json and
# returned ok:true. These tables are the single source of truth for what the two
# commands accept; anything outside them is an error, never a mode.
_INSTALL_MODE_FLAGS = {
    "--plugin": "plugin",
    "--scripts": "scripts",
    "--cursor": "cursor",
    "--codex": "codex",
}
_INSTALL_MODE_HELP = {
    "--plugin": "Claude Code plugin. Emits the `claude plugin` commands to run; changes nothing itself.",
    "--cursor": "Native Cursor hooks: writes ~/.cursor/hooks.json. Refused when the plugin is installed unless --force.",
    "--codex": "Native Codex hooks: writes $CODEX_HOME/hooks.json.",
}
# 7.0.0: the script installer is gone. `--scripts` is still parsed as a mode so
# `install --scripts` gets a dedicated answer (SCRIPT_INSTALL_REMOVED) instead of
# a generic "unknown argument", and so `uninstall --scripts` keeps working.
_INSTALL_REMOVED_MODES = {
    "--scripts": "Removed in 7.0.0: returns SCRIPT_INSTALL_REMOVED and installs nothing. Use --plugin. An existing script install keeps working; `audio-hooks uninstall` removes it.",
}


def _parse_mode_args(args: List[str], extra_flags: Dict[str, str]):
    """Split install/uninstall args into (modes, flags, unknown).

    ``modes`` keeps the order given so a conflicting pair can be reported;
    ``flags`` maps each recognised non-mode flag to True.
    """
    modes: List[str] = []
    flags: Dict[str, bool] = {}
    unknown: List[str] = []
    for a in args:
        if a in _INSTALL_MODE_FLAGS:
            if _INSTALL_MODE_FLAGS[a] not in modes:
                modes.append(_INSTALL_MODE_FLAGS[a])
        elif a in extra_flags:
            flags[a] = True
        else:
            unknown.append(a)
    return modes, flags, unknown


_UNINSTALL_MODE_HELP = {
    "--plugin": "Claude Code plugin. Lists the `claude plugin uninstall … --keep-data --json` command to run; changes nothing itself.",
    "--scripts": "Legacy script install (the default when no mode is given). Backs up, then removes echook's registrations from ~/.claude/settings.json and settings.local.json and echook's own files from ~/.claude/hooks. Files are judged by content, not name alone.",
    "--cursor": "Native Cursor hooks: removes audio-hooks-managed entries from ~/.cursor/hooks.json.",
    "--codex": "Native Codex hooks: removes audio-hooks-managed entries from $CODEX_HOME/hooks.json.",
}


def _install_usage(verb: str, extra_flags: Dict[str, str]) -> Dict[str, Any]:
    uninstall = verb == "uninstall"
    modes_txt = "|".join(_UNINSTALL_MODE_HELP if uninstall else _INSTALL_MODE_HELP)
    usage: Dict[str, Any] = {
        "ok": True,
        "usage": f"audio-hooks {verb} " + (f"[{modes_txt}]" if uninstall else modes_txt)
                 + "".join(f" [{f}]" for f in extra_flags),
        "modes": dict(_UNINSTALL_MODE_HELP if uninstall else _INSTALL_MODE_HELP),
        "flags": dict(extra_flags),
        "note": (
            "Bare `uninstall` (no mode) means --scripts. Unknown arguments are rejected, not ignored." if verb == "uninstall"
            else "A mode flag is required; there is no default. Unknown arguments are rejected, not ignored."
        ),
    }
    if not uninstall:
        usage["removed"] = dict(_INSTALL_REMOVED_MODES)
    return usage


_INSTALL_EXTRA_FLAGS = {"--force": "Override the DUPLICATE_BRIDGE refusal of --cursor (accept double-firing hooks)."}
_UNINSTALL_EXTRA_FLAGS = {
    "--purge": "Also delete the audio-hooks-data directory. Only valid with --cursor / --codex; rejected with --scripts / --plugin.",
    "--remove-unmatched": "Scripts mode only. When a result is UNINSTALL_INCOMPLETE because registrations spell the home directory in a form uninstall does not recognise ($env:USERPROFILE, %HOMEDRIVE%%HOMEPATH%, an MSYS /c/Users path, `\"$HOME\"/…`, `true;~/…`), also strip every entry listed in unmatched_references and then remove the scripts they pointed at. Read unmatched_references first: the loose match can catch another tool's variable such as $XDG_CONFIG_HOME or %ANDROID_HOME%.",
}


def cmd_install(args: List[str]) -> int:
    if any(_is_help_flag(a) for a in args):
        emit(_install_usage("install", _INSTALL_EXTRA_FLAGS))
        return 0
    modes, flags, unknown = _parse_mode_args(args, _INSTALL_EXTRA_FLAGS)
    if unknown:
        return emit_error(
            "INVALID_USAGE",
            f"Unknown argument(s) for install: {' '.join(unknown)}. Nothing was changed.",
            hint="Run `audio-hooks install --help` for the accepted flags.",
            suggested_command="audio-hooks install --help",
            unknown_args=unknown,
        )
    if not modes:
        return emit_error(
            "INVALID_USAGE",
            "install needs a mode; there is no default. Nothing was changed.",
            hint="Pick the editor you are installing for.",
            suggested_command="audio-hooks install --plugin",
            modes=dict(_INSTALL_MODE_HELP),
            next_steps=[f"audio-hooks install {f}" for f in _INSTALL_MODE_HELP],
        )
    if len(modes) > 1:
        return emit_error(
            "INVALID_USAGE",
            f"install modes are mutually exclusive, got: {', '.join(modes)}. Nothing was changed.",
            suggested_command="audio-hooks install --help",
        )
    mode = modes[0]
    if mode == "scripts":
        # 7.0.0: nothing is installed, with or without --force. Answered before
        # anything else is looked at, so the reply never depends on machine state.
        return emit_error(
            "SCRIPT_INSTALL_REMOVED",
            "The script installer was removed in 7.0.0. Nothing was changed.",
            hint="Install the Claude Code plugin instead (`audio-hooks install --plugin` lists the commands); "
                 "for Cursor or Codex use --cursor / --codex. An existing script install keeps working, "
                 "and `audio-hooks uninstall` removes it.",
            suggested_command="audio-hooks install --plugin",
            next_steps=["audio-hooks install --plugin"],
        )
    if require_project_root() != 0:
        return 1
    force = "--force" in flags
    if mode == "plugin":
        # v6.6: Claude Code ships CLI equivalents (with --json) for the
        # marketplace and install steps, so an agent can run them directly.
        # /reload-plugins is the one step with no CLI form: Claude Code 2.1.288
        # has no `claude plugin reload`, and the command reloads only the
        # session it is typed into. A session started after the install loads
        # the plugin by itself.
        emit({
            "ok": True,
            "mode": "plugin",
            "next_steps": [
                "claude plugin marketplace add ChanMeng666/echook --json",
                "claude plugin install audio-hooks@chanmeng-audio-hooks --json",
                "Ask the user to type /reload-plugins in any Claude Code session that is already open (no CLI equivalent; a new session needs no reload)",
                "Verify: audio-hooks status",
            ],
            "hint": "Plugin installation is performed by Claude Code itself; this command only lists the commands to run.",
        })
        return 0
    if mode == "cursor":
        return _install_cursor(force=force, project_root=PROJECT_ROOT, project_version=PROJECT_VERSION,
                               detect_install_mode=_detect_install_mode)
    return _install_codex(project_root=PROJECT_ROOT, project_version=PROJECT_VERSION)


def cmd_uninstall(args: List[str]) -> int:
    if any(_is_help_flag(a) for a in args):
        emit(_install_usage("uninstall", _UNINSTALL_EXTRA_FLAGS))
        return 0
    modes, flags, unknown = _parse_mode_args(args, _UNINSTALL_EXTRA_FLAGS)
    if unknown:
        return emit_error(
            "INVALID_USAGE",
            f"Unknown argument(s) for uninstall: {' '.join(unknown)}. Nothing was changed.",
            hint="Run `audio-hooks uninstall --help` for the accepted flags.",
            suggested_command="audio-hooks uninstall --help",
            unknown_args=unknown,
        )
    if len(modes) > 1:
        return emit_error(
            "INVALID_USAGE",
            f"uninstall modes are mutually exclusive, got: {', '.join(modes)}. Nothing was changed.",
            suggested_command="audio-hooks uninstall --help",
        )
    if require_project_root() != 0:
        return 1
    # Bare `uninstall` is the script-install removal, and since v6.6 it is done
    # natively (see _uninstall_scripts) on every platform: it is the documented
    # remedy for DUAL_INSTALL_DETECTED, and PROJECT_ROOT is the plugin directory
    # in exactly that situation. 7.0.0 removed the installer; this stays so an
    # existing script install can still be removed.
    mode = modes[0] if modes else "scripts"
    purge = "--purge" in flags
    if "--remove-unmatched" in flags and mode != "scripts":
        return emit_error(
            "INVALID_USAGE",
            f"--remove-unmatched applies to the script install (`uninstall` / `uninstall --scripts`), not --{mode}. Nothing was changed.",
            suggested_command="audio-hooks uninstall --help",
        )
    if purge and mode in ("scripts", "plugin"):
        return emit_error(
            "INVALID_USAGE",
            f"--purge applies to `uninstall --cursor` and `uninstall --codex` (it removes their audio-hooks-data directory); it does nothing for --{mode}. Nothing was changed.",
            suggested_command="audio-hooks uninstall --help",
        )
    if mode == "plugin":
        # --keep-data matches cmd_upgrade: without it `claude plugin uninstall`
        # deletes ~/.claude/plugins/data/<id>/ (user_preferences.json, backups).
        emit({
            "ok": True,
            "mode": "plugin",
            "next_steps": ["claude plugin uninstall audio-hooks@chanmeng-audio-hooks --keep-data --json"],
            "data_note": "--keep-data preserves ~/.claude/plugins/data/<id>/ (your preferences and backups). Without it Claude Code's uninstall does not preserve that directory.",
        })
        return 0
    if mode == "cursor":
        return _uninstall_cursor(purge=purge)
    if mode == "codex":
        return _uninstall_codex(purge=purge)
    return _uninstall_scripts(remove_unmatched="--remove-unmatched" in flags)



# ---------------------------------------------------------------------------
# Subcommand: statusline (implemented in echook_cli/statusline.py)
# ---------------------------------------------------------------------------

def cmd_statusline(args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    return statusline.cmd_statusline(args, PROJECT_ROOT)


def cmd_migrate(args: List[str]) -> int:
    """Bring the stored user_preferences.json up to the current template.

    State-changing, idempotent, and never creates the file: the explicit form of
    what the hook runner and every write already do on first load. It is the
    remedy for PREFS_SCHEMA_STALE, which the read-only commands deliberately do
    not apply.
    """
    if require_project_root() != 0:
        return 1
    rc = _check_args("migrate", args, max_positionals=0)
    if rc is not None:
        return rc
    try:
        report = _prefs().migrate()
    except ValueError as e:
        return emit_error("CONFIG_READ_ERROR", f"{_prefs().config_path} is {e}. Nothing was changed.",
                          suggested_command="audio-hooks backup list")
    except OSError as e:
        return emit_error("INTERNAL_ERROR", str(e))
    emit({"ok": True, **report})
    return 0


def cmd_update(args: List[str]) -> int:
    """Stub: report current version. Real update goes through Claude Code's plugin system."""
    if require_project_root() != 0:
        return 1
    emit({
        "ok": True,
        "current_version": PROJECT_VERSION,
        "hint": "Updates are managed by Claude Code's plugin system. Run /plugin update audio-hooks inside Claude Code.",
    })
    return 0


# ---------------------------------------------------------------------------
# Subcommand: backup (implemented in echook_cli/config_backup.py)
# ---------------------------------------------------------------------------

def cmd_backup(args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    return config_backup.cmd_backup(args, _prefs)



# ---------------------------------------------------------------------------
# Subcommand: upgrade (implemented in echook_cli/plugin_upgrade.py)
# ---------------------------------------------------------------------------

def cmd_upgrade(args: List[str]) -> int:
    return plugin_upgrade.cmd_upgrade(
        args, project_root=PROJECT_ROOT, project_version=PROJECT_VERSION,
        require_project_root=require_project_root, prefs_factory=_prefs,
    )


# ---------------------------------------------------------------------------
# Subcommand: manifest (the keystone)
# ---------------------------------------------------------------------------

# Mirrors the command shape in plugins/audio-hooks/hooks/hooks.json:
#   python "${CLAUDE_PLUGIN_ROOT}/runner/run.py" <arg>
_TEMPLATE_ARG_RE = re.compile(r'run\.py"?\s+([a-z_]+)')


def _claude_code_registered_events() -> List[str]:
    """Canonical events echook actually registers with Claude Code.

    Derived from ``plugins/audio-hooks/hooks/hooks.json`` — the real template —
    rather than from ``HOOK_CATALOG``. The catalog holds every canonical event
    across all three editors, including the nine Cursor-only ones
    (``shell_before``/``shell_after``, ``mcp_before``/``mcp_after``,
    ``file_read``, ``agent_response``, ``agent_thinking``, ``workspace_open``,
    ``tab_file_edit``) that Claude Code has no equivalent for and that the
    template never registers. Reporting the catalog here claimed 37 supported
    events when the true surface is 28, and CLAUDE.md tells operators the
    manifest is the live source of truth — so the lie propagated.

    Falls back to the catalog if the template cannot be read, so the manifest
    still renders on a partial checkout.
    """
    template = PROJECT_ROOT / "plugins" / "audio-hooks" / "hooks" / "hooks.json"
    try:
        data = json.loads(template.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [h["name"] for h in HOOK_CATALOG]

    registered = set()
    for groups in (data.get("hooks") or {}).values():
        for group in groups or []:
            for handler in (group or {}).get("hooks") or []:
                match = _TEMPLATE_ARG_RE.search(str((handler or {}).get("command", "")))
                if not match:
                    continue
                arg = match.group(1)
                # Variant args (session_start_fork) map back to their parent.
                canonical, _audio = getattr(HR, "SYNTHETIC_EVENT_MAP", {}).get(
                    arg, (arg, None)
                )
                registered.add(canonical)

    # Preserve HOOK_CATALOG order so the list stays stable and readable.
    return [h["name"] for h in HOOK_CATALOG if h["name"] in registered]


def _build_manifest() -> Dict[str, Any]:
    error_codes: Dict[str, Dict[str, str]] = {}
    if HR is not None:
        for name in dir(HR.ErrorCode):
            if name.startswith("_"):
                continue
            code = getattr(HR.ErrorCode, name)
            meta = HR._ERROR_HINTS.get(code, {})
            error_codes[code] = {
                "hint": meta.get("hint", ""),
                "suggested_command": meta.get("suggested_command", ""),
            }
    # CLI-level codes; the runner's text wins where both define a code.
    for code, meta in CLI_ERROR_CODES.items():
        error_codes.setdefault(code, dict(meta))
    return {
        "ok": True,
        "name": "audio-hooks",
        "version": PROJECT_VERSION,
        "schema": "audio-hooks.manifest.v1",
        "description": "AI-operated audio notification system for Claude Code, Cursor IDE & Codex CLI. Single JSON CLI for every project operation.",
        "subcommands": [
            {"name": "manifest", "args": ["[--schema]"], "description": "Print this manifest, or the user_preferences.json JSON Schema"},
            {"name": "version", "args": [], "description": "Project version + install detection"},
            {"name": "status", "args": [], "description": "Full project state snapshot (theme, enabled hooks, snooze, webhook, tts, rate limits)"},
            {"name": "get", "args": ["<dotted.key>"], "description": "Read any user_preferences.json key"},
            {"name": "set", "args": ["<dotted.key>", "<value>"], "description": "Write any user_preferences.json key (auto-coerces bool/int/JSON)"},
            {"name": "hooks list", "args": [], "description": "List all hooks with current state"},
            {"name": "hooks enable", "args": ["<name>"], "description": "Enable a hook"},
            {"name": "hooks disable", "args": ["<name>"], "description": "Disable a hook"},
            {"name": "hooks enable-only", "args": ["<name>...", ], "description": "Enable only the listed hooks, disable all others"},
            {"name": "theme list", "args": [], "description": "List audio themes"},
            {"name": "theme set", "args": ["<default|custom>"], "description": "Switch audio theme"},
            {"name": "snooze", "args": ["[duration]"], "description": "Snooze all hooks. Default 30m. Forms: 30m, 1h, 90s, 2d"},
            {"name": "snooze off", "args": [], "description": "Cancel snooze"},
            {"name": "snooze status", "args": [], "description": "Snooze remaining time"},
            {"name": "webhook", "args": [], "description": "Show webhook config"},
            {"name": "webhook set", "args": ["[--url <url>]", "[--format <slack|discord|teams|ntfy|raw>]", "[--hook-types <a,b,c>]"], "description": "Configure webhook (enables automatically when --url is set)"},
            {"name": "webhook clear", "args": [], "description": "Disable webhook"},
            {"name": "webhook test", "args": [], "description": "POST a test payload to the configured webhook"},
            {"name": "tts set", "args": ["[--enabled <true|false>]", "[--speak-assistant-message <true|false>]"], "description": "Configure TTS"},
            {"name": "rate-limits set", "args": ["[--enabled <true|false>]", "[--five-hour-thresholds <80,95>]"], "description": "Configure rate-limit alerts"},
            {"name": "test", "args": ["<hook_name|all>"], "description": "Run a hook with synthetic stdin and verify it fires"},
            {"name": "diagnose", "args": [], "description": "System diagnostic: settings.json, audio player, audio files, errors, warnings"},
            {"name": "logs tail", "args": ["[--n N]", "[--level info|warn|error|debug]"], "description": "Tail recent NDJSON log events"},
            {"name": "logs clear", "args": [], "description": "Truncate the event log"},
            {"name": "install", "args": ["<--plugin|--cursor|--codex>", "[--force]", "[--help]"], "description": "Install non-interactively. A mode flag is required (no default); unknown arguments are rejected with INVALID_USAGE and change nothing. --plugin lists the `claude plugin` commands to run. --cursor writes ~/.cursor/hooks.json for Cursor IDE users. --codex writes $CODEX_HOME/hooks.json for Codex CLI users. --force overrides the DUPLICATE_BRIDGE check (--cursor). The script installer was removed in 7.0.0: --scripts returns SCRIPT_INSTALL_REMOVED and installs nothing."},
            {"name": "uninstall", "args": ["[--plugin|--scripts|--cursor|--codex]", "[--purge]", "[--remove-unmatched]", "[--help]"], "description": "Uninstall non-interactively. Bare `uninstall` (= --scripts) removes a script install made before 7.0.0 (the installer itself is gone) natively on every platform: it backs up to ~/.claude/backups/audio-hooks-uninstall-<ts>/, edits settings.json / settings.local.json (only entries that reference ~/.claude/hooks/<known script>), then deletes only the files echook installed; it leaves the temp queue directory alone. Files are judged by content, not name; an incomplete result is ok:false / UNINSTALL_INCOMPLETE and --remove-unmatched finishes it (read unmatched_references first). Unknown arguments are rejected with INVALID_USAGE. --cursor / --codex remove audio-hooks-managed entries from the corresponding hooks.json (--purge also removes the audio-hooks-data directory)."},
            {"name": "statusline show", "args": [], "description": "Show Claude Code status line registration state"},
            {"name": "statusline install", "args": [], "description": "Register the echook status line in ~/.claude/settings.json"},
            {"name": "statusline uninstall", "args": [], "description": "Remove the echook status line registration"},
            {"name": "statusline segments", "args": [], "description": "List every Claude Code status line segment (name, line, source field, conditional) for configuring visible_segments / hidden_segments"},
        {"name": "statusline subagent show|install|uninstall", "args": [], "description": "Manage Claude Code's per-subagent status line (subagentStatusLine) — one rendered row per task in the agent panel. Separate settings key and a different output contract (NDJSON keyed by task id) from the main status line."},
            {"name": "statusline codex show", "args": [], "description": "Show the current Codex [tui].status_line + terminal_title and whether they likely overflow"},
            {"name": "statusline codex preview", "args": ["[--preset minimal|balanced|full]", "[--items a,b,c]", "[--target status_line|terminal_title|both]"], "description": "Print the curated Codex status_line / terminal_title that would be written (no write)"},
            {"name": "statusline codex apply", "args": ["[--preset minimal|balanced|full]", "[--items a,b,c]", "[--target status_line|terminal_title|both]"], "description": "Curate Codex [tui].status_line and/or terminal_title in config.toml (backs up first) so they stop truncating. Codex accepts only fixed item IDs — echook curates, it cannot render custom text."},
            {"name": "migrate", "args": [], "description": "Bring the stored user_preferences.json up to this version's template (new keys added, dropped keys removed, a sibling .bak kept). Idempotent; no-op when current or absent. Never creates the file."},
            {"name": "update", "args": ["[--check]"], "description": "Show current version (real updates go through /plugin update)"},
            {"name": "upgrade", "args": ["[--check-only]", "[--force]"], "description": "Refresh the plugin code (and ~/.claude/plugins/cache/) without losing config. Tries `claude plugin update` first; falls back to uninstall --keep-data + install."},
            {"name": "backup list", "args": [], "description": "JSON array of available backups, newest first"},
            {"name": "backup show", "args": ["<id>"], "description": "Print full content of one backup"},
            {"name": "backup restore", "args": ["<id|latest|latest-sibling|latest-external>"], "description": "Restore config from a backup; current state is itself backed up before overwrite"},
            {"name": "backup prune", "args": [], "description": "Trim external backup dir to EXTERNAL_BACKUP_KEEP=20"},
        ],
        "hooks": HOOK_CATALOG,
        # v6.4: matcher-scoped variants of the events above. Each is
        # independently switchable via enabled_hooks.<variant_name>; see
        # variant_gating for how a variant and its parent interact.
        "variants": _variant_catalog(),
        "variant_gating": {
            "description": (
                "Precedence used by hook_runner.is_hook_enabled(hook, variant), "
                "highest first. Variant keys are ordinary booleans in "
                "enabled_hooks alongside canonical hook names."
            ),
            "precedence": [
                "explicit enabled_hooks[<variant>]",
                "enabled_hooks[<parent>] is false (hard kill switch for all its variants)",
                "built-in per-variant default (see variants[].default)",
                "explicit enabled_hooks[<parent>] is true",
                "built-in default set: notification, stop, permission_request",
            ],
            "note": (
                "To keep exactly one variant of a disabled parent, set that "
                "variant key explicitly — rule 1 outranks the parent kill switch."
            ),
        },
        "config_keys": [
            "audio_theme",
            "enabled_hooks.<hook_name>",
            "enabled_hooks.<variant_name>",
            "playback_settings.debounce_ms",
            "notification_settings.mode",
            "notification_settings.detail_level",
            "notification_settings.per_hook.<hook_name>",
            "filters.<hook_name>.<field_name>",
            "filters.<hook_name>.<field_name>_exclude",
            "filters.stop.skip_if_background_tasks_running",
            "filters.stop.skip_if_session_crons_scheduled",
            "filters.stop.skip_if_aborted",
            "filters.stop.error_as_stop_failure",
            "webhook_settings.enabled",
            "webhook_settings.url",
            "webhook_settings.format",
            "webhook_settings.hook_types",
            "webhook_settings.include_user_email",
            "tts_settings.enabled",
            "tts_settings.speak_assistant_message",
            "tts_settings.assistant_message_max_chars",
            "rate_limit_alerts.enabled",
            "rate_limit_alerts.five_hour_thresholds",
            "rate_limit_alerts.seven_day_thresholds",
            "statusline_settings.visible_segments",
            "statusline_settings.hidden_segments",
            "statusline_settings.extra_segments",
            "statusline_settings.max_width",
        ],
        "themes": ["default", "custom"],
        "log_schema": "audio-hooks.v1",
        "webhook_schema": "audio-hooks.webhook.v1",
        "error_codes": error_codes,
        "editor_targets": _detect_editor_targets(),
        "supported_editors": {
            "claude-code": {
                "events": _claude_code_registered_events(),
                "install_via": "claude plugin marketplace add ChanMeng666/echook --json && claude plugin install audio-hooks@chanmeng-audio-hooks --json (then ask the user to type /reload-plugins)",
            },
            "cursor": {
                "auto_bridge": "Cursor IDE 3.2.16+ auto-bridges Claude Code plugin hooks. In the IDE, the bridge can be switched off at Cursor Settings > Rules, Skills, Subagents > 'Include third-party Plugins, Skills, and other configs'; that toggle has no effect on cursor-agent, where bridging is hardcoded.",
                "bridged_events_subset": [
                    "pretooluse", "posttooluse", "userpromptsubmit",
                    "stop", "subagent_stop", "session_start",
                    "session_end", "precompact",
                ],
                # v6.2: the native `--cursor` template maps Cursor's full Agent-hook
                # surface, splitting tool execution into per-type events (shell / MCP /
                # file-read) so each gets its own sound — something the coarse auto-bridge
                # cannot do. The umbrella preToolUse/postToolUse are dropped natively to
                # avoid double-firing with the granular events.
                "native_events_subset": [
                    "session_start", "session_end", "stop",
                    "subagent_start", "subagent_stop", "posttoolusefailure",
                    "file_changed", "precompact", "userpromptsubmit",
                    "shell_before", "shell_after", "mcp_before", "mcp_after",
                    "file_read", "agent_response", "agent_thinking",
                    "workspace_open", "tab_file_edit",
                ],
                "unbridged_events": [
                    "notification",  # No Cursor equivalent
                    "permission_request",  # No Cursor equivalent
                ],
                "native_install_via": "audio-hooks install --cursor",
                "doc_url": "https://cursor.com/docs/hooks",
            },
            "codex": {
                "auto_bridge": False,
                "auto_bridge_note": "Codex does NOT auto-bridge Claude Code plugins. Install via the Codex plugin marketplace or native `audio-hooks install --codex`.",
                "supported_events": [
                    "session_start",
                    "pretooluse",
                    "permission_request",
                    "posttooluse",
                    "precompact",
                    "postcompact",
                    "userpromptsubmit",
                    "subagent_start",
                    "subagent_stop",
                    "stop",
                    "session_end",
                ],
                "session_end_note": (
                    "Codex gained SessionEnd in 0.145.0 (#33895). `install --codex` "
                    "registers it only when the installed Codex is >= 0.145.0, because "
                    "the hooks event map rejects unknown keys and a hooks.json that "
                    "fails to parse disables every hook, not just the unknown one. "
                    "Teardown constraints upstream: 1s default timeout, 3s cap, async "
                    "forced synchronous, root threads only."
                ),
                "unsupported_events": [
                    "notification",
                    "elicitation",
                    "elicitation_result",
                    "cwd_changed",
                    "directory_added",
                    "worktree_remove",
                    "file_changed",
                    "task_created",
                    "task_completed",
                    "teammate_idle",
                    "config_change",
                    "instructions_loaded",
                    "permission_denied",
                    # v6.2 — Codex has no equivalent for the new Claude Code / Cursor events.
                    "setup",
                    "user_prompt_expansion",
                    "post_tool_batch",
                    "message_display",
                    "shell_before",
                    "shell_after",
                    "mcp_before",
                    "mcp_after",
                    "file_read",
                    "agent_response",
                    "agent_thinking",
                    "workspace_open",
                    "tab_file_edit",
                ],
                "feature_flag": "Codex hooks are enabled by default. `[features].hooks = false` in $CODEX_HOME/config.toml disables all hooks; remove it or set hooks = true to re-enable. Legacy `[features].codex_hooks = true` is recognized as explicitly enabled.",
                "plugin_install_via": "codex plugin marketplace add ChanMeng666/echook && codex plugin add audio-hooks@chanmeng-audio-hooks",
                "native_install_via": "audio-hooks install --codex",
                "doc_url": "https://learn.chatgpt.com/docs/hooks",
            },
        },
        "env_vars": {
            "CLAUDE_PLUGIN_DATA": "Plugin install state directory (auto-set by Claude Code).",
            "CLAUDE_PLUGIN_ROOT": "Plugin install root (auto-set by Claude Code).",
            "CLAUDE_AUDIO_HOOKS_DATA": "Explicit override for state directory.",
            "PLUGIN_DATA": "Codex plugin state directory (auto-set by Codex plugin loader).",
            "PLUGIN_ROOT": "Codex plugin install root (auto-set by Codex plugin loader).",
            "CLAUDE_AUDIO_HOOKS_PROJECT": "Explicit override for project root.",
            "CLAUDE_HOOKS_DEBUG": "Set to 1/true/yes (case-insensitive) to write debug-level events to the NDJSON log AND dump the latest status line input JSON to ${state_dir}/statusline.last_input.json. Disable when not actively diagnosing — the dump may include workspace paths and the last assistant message.",
            "CURSOR_VERSION": "Set by Cursor IDE when invoking a hook (per cursor.com/docs/hooks). Used by detect_invoker() to identify Cursor as the caller.",
            "CLAUDE_PROJECT_DIR": "Set by Cursor IDE as a Claude-Code-compatible alias for the workspace root.",
            "CODEX_HOME": "Codex CLI home directory (defaults to ~/.codex). Used by audio-hooks install --codex to locate hooks.json and config.toml, and by the runner to resolve the Codex-native data dir.",
        },
        "pointers": {
            "agents_md": "AGENTS.md",
            "claude_md": "CLAUDE.md",
            "skill": "plugins/audio-hooks/skills/audio-hooks/SKILL.md",
            "readme": "README.md",
            "installation_guide": "docs/INSTALLATION_GUIDE.md",
            "changelog": "CHANGELOG.md",
            "architecture": "docs/ARCHITECTURE.md",
            "troubleshooting": "docs/TROUBLESHOOTING.md",
            "status_line": "docs/STATUS_LINE.md",
            "invariants": "docs/INVARIANTS.md",
            "event_behavior_notes": "docs/EVENT_BEHAVIOR_NOTES.md",
            "project_status": "docs/PROJECT_STATUS.md",
            "releasing": "docs/RELEASING.md",
            "directory_listing": "docs/DIRECTORY_LISTING.md",
            "privacy_policy": "PRIVACY.md",
            "canonical_sources": [
                "hooks/", "bin/", "audio/", "config/",
                "cursor-hooks/", "codex-hooks/",
            ],
            "_note": "All paths are relative to the project root reported in `audio-hooks status.project_dir`. agents_md is the full operating guide; claude_md only imports it (Claude Code ignores AGENTS.md when a CLAUDE.md exists).",
        },
    }


def cmd_manifest(args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    if args and args[0] == "--schema":
        emit(_build_manifest_schema())
        return 0
    emit(_build_manifest())
    return 0


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

DISPATCH = {
    "manifest": cmd_manifest,
    "version": cmd_version,
    "status": cmd_status,
    "get": cmd_get,
    "set": cmd_set,
    "hooks": cmd_hooks,
    "theme": cmd_theme,
    "snooze": cmd_snooze,
    "webhook": cmd_webhook,
    "tts": cmd_tts,
    "rate-limits": cmd_rate_limits,
    "test": cmd_test,
    "diagnose": cmd_diagnose,
    "logs": cmd_logs,
    "install": cmd_install,
    "uninstall": cmd_uninstall,
    "update": cmd_update,
    "statusline": cmd_statusline,
    "backup": cmd_backup,
    "upgrade": cmd_upgrade,
    "migrate": cmd_migrate,
}


# Subcommands whose handler answers --help itself, with a richer payload.
_SELF_DOCUMENTING = frozenset({"install", "uninstall", "upgrade"})


# v6.7: invocations that only report. main() marks them read-only so they leave
# the home and data directories byte-identical -- no auto-initialised
# user_preferences.json, no migration save, no logs/ or queue/ directory. Each
# entry maps a subcommand to the first-argument forms that are read-only;
# None means "no arguments" (a bare `webhook` displays), "*" means any form.
# Everything else (set, hooks enable, snooze 30m, install, test, ...) keeps
# initialising and migrating as before, and so does the hook runner.
# tests/test_read_only_commands.py walks every manifest subcommand against this.
_READ_ONLY_FORMS: Dict[str, Tuple[Optional[str], ...]] = {
    "manifest": ("*",),
    "version": ("*",),
    "status": ("*",),
    "diagnose": ("*",),
    "get": ("*",),
    "update": ("*",),
    "hooks": ("list",),
    "theme": (None, "list"),
    "snooze": ("status",),
    # Bare form and the flagless `set` both only display (v6.6).
    "webhook": (None,),
    "tts": (None,),
    "rate-limits": (None,),
    "logs": ("tail",),
    "backup": ("list", "show"),
}


def _is_read_only_invocation(cmd: str, args: List[str]) -> bool:
    """True when ``cmd args`` only reports and must not touch disk state."""
    if any(_is_help_flag(a) for a in (args[:1] if cmd == "set" else args)):
        return True  # usage is printed, nothing runs
    if cmd == "statusline":
        sub = args[0] if args else "show"
        if sub in ("show", "segments"):
            return True
        if sub in ("subagent", "codex"):
            action = args[1] if len(args) > 1 else "show"
            return action in (("show",) if sub == "subagent" else ("show", "preview"))
        return False
    forms = _READ_ONLY_FORMS.get(cmd)
    if forms is None:
        return False
    if "*" in forms:
        return True
    first = args[0] if args else None
    if first in forms:
        return True
    # `webhook set` / `tts set` / `rate-limits set` with no flags only display.
    return cmd in ("webhook", "tts", "rate-limits") and args == ["set"]


def _emit_subcommand_usage(cmd: str) -> int:
    """Print the manifest entries for ``cmd``; run nothing, write nothing."""
    entries: List[Dict[str, Any]] = []
    try:
        entries = [e for e in _build_manifest().get("subcommands", [])
                   if str(e.get("name", "")).split(" ")[0] == cmd]
    except Exception:
        # Usage must survive a broken checkout; the entry list is a convenience.
        pass
    emit({
        "ok": True,
        "command": cmd,
        "usage": entries,
        "note": "--help only prints usage; nothing was executed. `audio-hooks manifest` lists every subcommand.",
    })
    return 0


def main(argv: List[str]) -> int:
    global _READ_ONLY
    _READ_ONLY = False
    if len(argv) < 2:
        # No-arg invocation returns the manifest as the canonical introspection target
        return cmd_manifest([])
    cmd = argv[1]
    if cmd in ("-h", "--help", "help"):
        return cmd_manifest([])
    fn = DISPATCH.get(cmd)
    if fn is None:
        return emit_error("INVALID_USAGE", f"Unknown subcommand: {cmd}", suggested_command="audio-hooks manifest")
    # v6.6: --help / -h must never reach a handler. Before this, `tts set --help`,
    # `webhook set --help` and the like parsed it as an ordinary argument and
    # still rewrote the config, and `install --help` ran the installer -- an agent
    # probing a subcommand for usage changed real state. No subcommand takes a
    # literal "-h" or "--help" as a value. install/uninstall/upgrade keep the
    # richer usage their handlers emit themselves.
    # `set <key> <value>`: only a leading help token is help; one anywhere after
    # the key is rejected by cmd_set (it must never be stored as a value).
    help_args = argv[2:3] if cmd == "set" else argv[2:]
    if cmd not in _SELF_DOCUMENTING and any(_is_help_flag(a) for a in help_args):
        _READ_ONLY = True
        try:
            return _emit_subcommand_usage(cmd)
        finally:
            _READ_ONLY = False
    _READ_ONLY = _is_read_only_invocation(cmd, argv[2:])
    try:
        return fn(argv[2:])
    except Exception as e:
        return emit_error("INTERNAL_ERROR", str(e))
    finally:
        _READ_ONLY = False


if __name__ == "__main__":
    sys.exit(main(sys.argv))
