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
from typing import Any, Dict, List, Optional, Tuple

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


# ---------------------------------------------------------------------------
# JSON output helpers
# ---------------------------------------------------------------------------

def emit(payload: Dict[str, Any]) -> None:
    """Print a JSON document to stdout. Compact, no trailing newline noise."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def emit_error(code: str, message: str, hint: str = "", suggested_command: str = "", **extra: Any) -> int:
    """Emit a JSON error to stdout and return exit code 1."""
    err: Dict[str, Any] = {"ok": False, "error": {"code": code, "message": message}}
    if hint:
        err["error"]["hint"] = hint
    if suggested_command:
        err["error"]["suggested_command"] = suggested_command
    for k, v in extra.items():
        err[k] = v
    emit(err)
    return 1


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

PROJECT_VERSION = "6.4.0"

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
    {"name": "precompact",           "default": False, "audio": "notification-info.mp3",     "description": "Before context compaction"},
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


def _detect_install_mode() -> Dict[str, Any]:
    """Detect whether the script install and/or plugin install are present.

    The script install is detected by the legacy ~/.claude/hooks/hook_runner.py
    file (placed there by scripts/install-complete.sh).

    The plugin install is detected by:
      1. CLAUDE_PLUGIN_ROOT being set (we're invoked from inside a hook), OR
      2. ~/.claude/plugins/installed_plugins.json containing audio-hooks, OR
      3. ~/.claude/plugins/cache/<id>/ existing for any audio-hooks plugin.
    """
    home = Path.home()
    script_install = (home / ".claude" / "hooks" / "hook_runner.py").exists()

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
        result["warning"] = {
            "code": "DUAL_INSTALL_DETECTED",
            "message": "Both the script install and the plugin install are active. This causes double audio. Run `audio-hooks uninstall` to remove the script install (preserves config + audio).",
        }
    return result


def _detect_codex_native_install() -> bool:
    """True iff ``$CODEX_HOME/hooks.json`` contains audio-hooks-managed entries.

    Codex doesn't auto-bridge Claude Code plugins, so this is the only way
    audio-hooks can fire under Codex.
    """
    codex_dir = Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"))
    codex_hooks = codex_dir / "hooks.json"
    if not codex_hooks.exists():
        return False
    try:
        doc = json.loads(codex_hooks.read_text(encoding="utf-8"))
    except Exception:
        return False
    return isinstance(doc, dict) and bool(doc.get("_audio_hooks_managed"))


def _detect_codex_plugin_install() -> Optional[str]:
    """Return Codex plugin cache path when audio-hooks appears installed."""
    codex_dir = Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"))
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


def _codex_feature_state_from_text(text: str) -> str:
    """Return Codex hooks feature state from config TOML text.

    Current Codex enables hooks by default. The canonical opt-out is
    ``[features].hooks = false``; the older ``codex_hooks`` alias is still
    recognized for compatibility with existing user configs.
    """
    try:
        import tomllib  # type: ignore
        try:
            data = tomllib.loads(text)
        except Exception:
            return "parse_error"
        features = data.get("features") if isinstance(data, dict) else None
        if not isinstance(features, dict):
            return "enabled_by_default"
        if "hooks" in features:
            if features.get("hooks") is True:
                return "explicitly_enabled"
            if features.get("hooks") is False:
                return "disabled"
            return "parse_error"
        if features.get("codex_hooks") is True:
            return "explicitly_enabled_legacy"
        if features.get("codex_hooks") is False:
            return "disabled_legacy"
        return "enabled_by_default"
    except ImportError:
        in_features = False
        saw_features = False
        hooks_value: Optional[bool] = None
        legacy_value: Optional[bool] = None
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if re.match(r"^\[[^\]]+\]\s*$", stripped):
                in_features = bool(re.match(r"^\[features\]\s*$", stripped, re.IGNORECASE))
                saw_features = saw_features or in_features
                continue
            if not in_features:
                continue
            m = re.match(r"^hooks\s*=\s*(true|false)\b", stripped, re.IGNORECASE)
            if m:
                hooks_value = m.group(1).lower() == "true"
                continue
            m = re.match(r"^codex_hooks\s*=\s*(true|false)\b", stripped, re.IGNORECASE)
            if m:
                legacy_value = m.group(1).lower() == "true"
        if hooks_value is True:
            return "explicitly_enabled"
        if hooks_value is False:
            return "disabled"
        if legacy_value is True:
            return "explicitly_enabled_legacy"
        if legacy_value is False:
            return "disabled_legacy"
        return "enabled_by_default" if saw_features or not saw_features else "enabled_by_default"


def _codex_feature_enabled_from_state(state: str) -> Optional[bool]:
    if state in ("disabled", "disabled_legacy"):
        return False
    if state == "parse_error":
        return None
    return True


def _detect_codex_hooks_feature_state() -> str:
    """Read ``$CODEX_HOME/config.toml`` and return Codex hooks feature state."""
    codex_dir = Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"))
    config_path = codex_dir / "config.toml"
    if not config_path.exists():
        return "enabled_by_default"
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return "parse_error"
    return _codex_feature_state_from_text(text)


def _detect_codex_feature_flag() -> Optional[bool]:
    """Backward-compatible bool for callers that predate hooks_feature_state."""
    return _codex_feature_enabled_from_state(_detect_codex_hooks_feature_state())


def _detect_cursor_native_install() -> bool:
    """True iff ``~/.cursor/hooks.json`` contains audio-hooks-managed entries.

    Cursor-native install is what ``audio-hooks install --cursor`` writes.
    Distinct from the auto-bridge: the bridge fires whenever Claude Code
    plugins are present + Third-party-skills enabled in Cursor Settings,
    requiring no file in ``~/.cursor/``.
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
        the user has Claude Code's audio-hooks plugin installed AND
        "Third-party skills" enabled in Cursor Settings.
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

    codex_dir = Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"))

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


def _load_config_raw() -> Dict[str, Any]:
    """Load user_preferences.json (auto-init from template + plugin-option overlay)."""
    if PROJECT_ROOT is None:
        return {}
    try:
        return _prefs().load()
    except Exception:
        return {}


def _save_config_raw(cfg: Dict[str, Any]) -> Tuple[bool, str]:
    """Save user_preferences.json (atomic write + auto-backup snapshot)."""
    if PROJECT_ROOT is None:
        return False, "PROJECT_ROOT not detected"
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
# Snooze marker (matches hook_runner.is_snoozed)
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


def _snooze_status() -> Dict[str, Any]:
    sf = _snooze_file()
    if not sf.exists():
        return {"active": False, "remaining_seconds": 0, "until": None}
    try:
        until = float(sf.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return {"active": False, "remaining_seconds": 0, "until": None}
    now = time.time()
    if now >= until:
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

    def is_on(name: str, default: bool) -> bool:
        v = enabled_hooks_cfg.get(name)
        return bool(v) if isinstance(v, bool) else default

    enabled = [h["name"] for h in HOOK_CATALOG if is_on(h["name"], h["default"])]

    customizations: Dict[str, Any] = {}
    try:
        customizations = _prefs().diff_from_default()
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
        "log_dir": str(HR.get_log_dir()) if HR else None,
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


def _hooks_state(include_variants: bool = False) -> List[Dict[str, Any]]:
    cfg = _load_config_raw()
    enabled_cfg = cfg.get("enabled_hooks", {}) if isinstance(cfg.get("enabled_hooks"), dict) else {}
    out = []
    for h in HOOK_CATALOG:
        v = enabled_cfg.get(h["name"])
        enabled = bool(v) if isinstance(v, bool) else h["default"]
        out.append({
            "name": h["name"],
            "enabled": enabled,
            "default": h["default"],
            "audio_file": h["audio"],
            "description": h["description"],
            "is_variant": False,
        })
    if not include_variants:
        return out
    for var in _variant_catalog():
        v = enabled_cfg.get(var["name"])
        if isinstance(v, bool):
            enabled = v                       # explicit variant key (rule 1)
        elif enabled_cfg.get(var["variant_of"]) is False:
            enabled = False                   # parent kill switch (rule 2)
        else:
            enabled = var["default"]          # variant default, else parent's
        out.append({
            "name": var["name"],
            "enabled": enabled,
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
            return emit_error("INVALID_USAGE", f"Usage: audio-hooks hooks {sub} <name>")
        name = rest[0]
        valid = {h["name"] for h in HOOK_CATALOG} | _variant_names()
        if name not in valid:
            return emit_error("UNKNOWN_HOOK_TYPE", f"Unknown hook: {name}", hint="Run `audio-hooks hooks list --variants` to see all hooks and matcher variants.", suggested_command="audio-hooks hooks list --variants")
        cfg = _load_config_raw()
        cfg.setdefault("enabled_hooks", {})[name] = (sub == "enable")
        ok, err = _save_config_raw(cfg)
        if not ok:
            return emit_error("CONFIG_READ_ERROR", err)
        emit({"ok": True, "hook": name, "enabled": sub == "enable"})
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
    arg = args[0] if args else "30m"
    sf = _snooze_file()
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
    if arg == "status":
        emit({"ok": True, **_snooze_status()})
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


def cmd_tts(args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    if not args or args[0] == "set":
        rest = args[1:] if args else []
        flags = _kv_flags(rest)
        cfg = _load_config_raw()
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
        flags = _kv_flags(rest)
        cfg = _load_config_raw()
        r = cfg.setdefault("rate_limit_alerts", {})
        for k, v in flags.items():
            if k in ("five_hour_thresholds", "seven_day_thresholds") and isinstance(v, str):
                v = [int(x.strip()) for x in v.split(",") if x.strip()]
            r[k] = v
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
    if not args:
        emit({"ok": False, "error": {"code": "INVALID_USAGE", "message": "Usage: audio-hooks test <hook_name|all>"}})
        return 1
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
    }


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
            "suggested_command": "audio-hooks uninstall",
        })

    editor_targets = _detect_editor_targets()
    if editor_targets.get("cursor", {}).get("state") == "double-registered":
        errors.append({
            "code": "DUPLICATE_BRIDGE",
            "message": editor_targets["cursor"]["warning"]["message"],
            "hint": "Cursor is fed by both Claude Code's auto-bridge AND ~/.cursor/hooks.json — every event fires twice.",
            "suggested_command": "audio-hooks uninstall --cursor",
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
    log_dir = HR.get_log_dir() if HR else Path("/tmp")
    log_file = log_dir / "events.ndjson"
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
# Subcommand: install / uninstall (delegates to existing scripts)
# ---------------------------------------------------------------------------

def cmd_install(args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    mode = "scripts"
    force = False
    for a in args:
        if a == "--plugin":
            mode = "plugin"
        elif a == "--scripts":
            mode = "scripts"
        elif a == "--cursor":
            mode = "cursor"
        elif a == "--codex":
            mode = "codex"
        elif a == "--force":
            force = True
    if mode == "plugin":
        emit({
            "ok": True,
            "mode": "plugin",
            "next_steps": [
                "Run inside Claude Code: /plugin marketplace add ChanMeng666/echook",
                "Run inside Claude Code: /plugin install audio-hooks@chanmeng-audio-hooks",
                "Verify: audio-hooks status",
            ],
            "hint": "Plugin installation is performed by Claude Code itself; this command only documents the steps.",
        })
        return 0
    if mode == "cursor":
        return _install_cursor(force=force)
    if mode == "codex":
        return _install_codex()
    # Script install: delegate to existing installer
    import subprocess
    if platform.system() == "Windows":
        installer = PROJECT_ROOT / "scripts" / "install-windows.ps1"
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer)]
    else:
        installer = PROJECT_ROOT / "scripts" / "install-complete.sh"
        cmd = ["bash", str(installer)]
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
        emit({"ok": proc.returncode == 0, "mode": "scripts", "exit_code": proc.returncode, "installer": str(installer)})
        return 0 if proc.returncode == 0 else 1
    except Exception as e:
        return emit_error("INTERNAL_ERROR", str(e))


def _install_cursor(*, force: bool) -> int:
    """Install audio-hooks for Cursor IDE via ~/.cursor/hooks.json.

    Use this when Cursor is the user's primary editor and Claude Code is NOT
    installed (otherwise the auto-bridge already covers Cursor). When both
    are installed, this aborts with DUPLICATE_BRIDGE unless ``--force`` is
    passed — concurrent registration causes every hook to fire twice.
    """
    cursor_dir = Path.home() / ".cursor"
    if not cursor_dir.exists():
        return emit_error(
            "CURSOR_NOT_FOUND",
            f"~/.cursor/ does not exist at {cursor_dir}. Install Cursor IDE first, then re-run this command.",
            suggested_command="audio-hooks status",
        )

    bridged = bool(_detect_install_mode().get("plugin_install"))
    if bridged and not force:
        return emit_error(
            "DUPLICATE_BRIDGE",
            "Cursor IDE already auto-bridges the Claude Code audio-hooks plugin. Installing the native Cursor hook on top would fire every event twice. Pass --force to install anyway, or uninstall the Claude Code plugin first.",
            suggested_command="audio-hooks uninstall --plugin",
        )

    template_path = PROJECT_ROOT / "cursor-hooks" / "hooks.json"
    if not template_path.exists():
        return emit_error("INTERNAL_ERROR", f"Template not found: {template_path}")

    try:
        template_text = template_path.read_text(encoding="utf-8")
    except Exception as e:
        return emit_error("INTERNAL_ERROR", f"Cannot read template: {e}")

    # Substitute placeholders. {{PYTHON}} -> 'python' on Windows ('python3' fails
    # there because of the Microsoft Store stub); 'python3' on POSIX.
    python_bin = "python" if platform.system() == "Windows" else "python3"
    hook_runner_abs = str((PROJECT_ROOT / "hooks" / "hook_runner.py").resolve())
    # The template is JSON, and the substituted value lands inside a JSON
    # string literal. On Windows, paths contain backslashes that JSON treats
    # as escapes — ``D:\github\...`` would parse as ``\g`` (invalid). Escape
    # backslashes (and any double quotes) before substitution so the
    # post-substitution text remains valid JSON on every platform.
    hook_runner_for_json = hook_runner_abs.replace("\\", "\\\\").replace('"', '\\"')
    template_text = template_text.replace("{{PYTHON}}", python_bin)
    template_text = template_text.replace("{{HOOK_RUNNER}}", hook_runner_for_json)

    try:
        new_doc = json.loads(template_text)
    except json.JSONDecodeError as e:
        return emit_error("INTERNAL_ERROR", f"Template is not valid JSON after substitution: {e}")

    # Tag each hook entry so uninstall --cursor can find ours and leave others.
    for event_name, entries in new_doc.get("hooks", {}).items():
        for entry in entries:
            entry["_managed_by"] = "audio-hooks"

    target_path = cursor_dir / "hooks.json"
    if target_path.exists():
        try:
            existing = json.loads(target_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
        if isinstance(existing, dict):
            # Merge: keep user's non-audio-hooks entries; replace ours.
            existing_hooks = existing.get("hooks") or {}
            if isinstance(existing_hooks, dict):
                merged_hooks: Dict[str, Any] = {}
                # Start by stripping any prior audio-hooks entries from the user's file
                for evt, entries in existing_hooks.items():
                    if isinstance(entries, list):
                        keep = [
                            e for e in entries
                            if not (isinstance(e, dict) and e.get("_managed_by") == "audio-hooks")
                        ]
                        if keep:
                            merged_hooks[evt] = keep
                # Then layer ours on top
                for evt, entries in new_doc["hooks"].items():
                    merged_hooks.setdefault(evt, []).extend(entries)
                existing["hooks"] = merged_hooks
                existing["version"] = 1
                new_doc = existing

    try:
        target_path.write_text(
            json.dumps(new_doc, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        return emit_error("INTERNAL_ERROR", f"Cannot write {target_path}: {e}")

    # Seed Cursor-native data dir from default_preferences.json
    data_dir = cursor_dir / "audio-hooks-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    prefs_target = data_dir / "user_preferences.json"
    if not prefs_target.exists():
        default_prefs = PROJECT_ROOT / "config" / "default_preferences.json"
        if default_prefs.exists():
            try:
                prefs_target.write_text(default_prefs.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception:
                pass

    # Write install marker so uninstall and diagnostics can identify what we
    # touched and when.
    marker_path = data_dir / "install_marker.json"
    try:
        marker_path.write_text(
            json.dumps({
                "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "version": PROJECT_VERSION,
                "project_dir": str(PROJECT_ROOT),
                "hook_runner": hook_runner_abs,
                "python_bin": python_bin,
                "duplicate_bridge_forced": force and bridged,
            }, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    emit({
        "ok": True,
        "mode": "cursor",
        "hooks_file": str(target_path),
        "data_dir": str(data_dir),
        "duplicate_bridge_forced": force and bridged,
        "next_steps": [
            "Restart Cursor IDE so it picks up the new ~/.cursor/hooks.json",
            "Trigger any agent action — sessionEnd / stop should now play audio per ~/.cursor/audio-hooks-data/user_preferences.json",
            "Run `audio-hooks status` to confirm editor_targets.cursor.state == 'native'",
        ],
        "hint": (
            "Notification and PermissionRequest hooks are not registered (Cursor has no equivalent events). "
            "All other hooks behave the same as in Claude Code."
        ),
    })
    return 0


def _codex_home() -> Path:
    """Return the Codex home directory honoring ``CODEX_HOME``.

    Defaults to ``~/.codex`` on every platform (Codex's own default per
    developers.openai.com/codex/config-basic).
    """
    return Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"))


def _check_codex_feature_flag(config_path: Path) -> Dict[str, Any]:
    """Inspect ``~/.codex/config.toml`` for Codex's hooks feature state.

    Hooks are enabled by default in current Codex. This helper is read-only:
    it only reports a next step when the user's config explicitly disables
    hooks or cannot be parsed.
    """
    result: Dict[str, Any] = {"config_path": str(config_path)}
    if not config_path.exists():
        result["state"] = "enabled_by_default"
        result["next_step"] = None
        return result
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as e:
        result["state"] = "parse_error"
        result["error"] = str(e)
        result["next_step"] = (
            f"Could not read {config_path}: {e}. Ensure the file is readable; "
            "hooks are enabled by default unless `[features].hooks = false` is set."
        )
        return result

    state = _codex_feature_state_from_text(text)
    result["state"] = state
    if state in ("enabled_by_default", "explicitly_enabled", "explicitly_enabled_legacy"):
        result["next_step"] = None
    elif state in ("disabled", "disabled_legacy"):
        result["next_step"] = (
            f"In {config_path}, remove `[features].hooks = false` or set "
            "`hooks = true` under `[features]` so Codex invokes hooks."
        )
    else:
        result["next_step"] = (
            f"{config_path} has a TOML parse error. Fix it; hooks are enabled by "
            "default unless `[features].hooks = false` is set."
        )
    return result


def _install_codex() -> int:
    """Install audio-hooks for Codex CLI via ``$CODEX_HOME/hooks.json``.

    Codex (per developers.openai.com/codex/hooks) does NOT auto-bridge Claude
    Code plugins, so there is no ``DUPLICATE_BRIDGE`` concern. The install
    writes ``$CODEX_HOME/hooks.json`` (default ``~/.codex/hooks.json``),
    seeds ``$CODEX_HOME/audio-hooks-data/``, and emits AI-readable
    ``next_steps`` only when the user's ``config.toml`` explicitly disables
    hooks or cannot be parsed.
    """
    codex_dir = _codex_home()
    if not codex_dir.exists():
        try:
            codex_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return emit_error(
                "INTERNAL_ERROR",
                f"Cannot create {codex_dir}: {e}. Install Codex CLI first or set CODEX_HOME.",
                suggested_command="audio-hooks status",
            )

    template_path = PROJECT_ROOT / "codex-hooks" / "hooks.json"
    if not template_path.exists():
        return emit_error("INTERNAL_ERROR", f"Template not found: {template_path}")

    try:
        template_text = template_path.read_text(encoding="utf-8")
    except Exception as e:
        return emit_error("INTERNAL_ERROR", f"Cannot read template: {e}")

    python_bin = "python" if platform.system() == "Windows" else "python3"
    hook_runner_abs = str((PROJECT_ROOT / "hooks" / "hook_runner.py").resolve())
    # Same Windows-backslash-in-JSON precaution as _install_cursor.
    hook_runner_for_json = hook_runner_abs.replace("\\", "\\\\").replace('"', '\\"')
    template_text = template_text.replace("{{PYTHON}}", python_bin)
    template_text = template_text.replace("{{HOOK_RUNNER}}", hook_runner_for_json)

    try:
        new_doc = json.loads(template_text)
    except json.JSONDecodeError as e:
        return emit_error("INTERNAL_ERROR", f"Template is not valid JSON after substitution: {e}")

    # Tag every hook entry so uninstall can find ours and leave foreign entries.
    for event_name, entries in new_doc.get("hooks", {}).items():
        for entry in entries:
            entry["_managed_by"] = "audio-hooks"

    target_path = codex_dir / "hooks.json"
    if target_path.exists():
        try:
            existing = json.loads(target_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
        if isinstance(existing, dict):
            existing_hooks = existing.get("hooks") or {}
            if isinstance(existing_hooks, dict):
                merged_hooks: Dict[str, Any] = {}
                for evt, entries in existing_hooks.items():
                    if isinstance(entries, list):
                        keep: List[Any] = []
                        for e in entries:
                            if isinstance(e, dict):
                                # Codex's hooks.json schema nests command handlers
                                # under {matcher, hooks: [...]}. Tag is on the
                                # outer entry, so this filter works at both levels.
                                if e.get("_managed_by") == "audio-hooks":
                                    continue
                            keep.append(e)
                        if keep:
                            merged_hooks[evt] = keep
                for evt, entries in new_doc["hooks"].items():
                    merged_hooks.setdefault(evt, []).extend(entries)
                existing["hooks"] = merged_hooks
                new_doc = existing

    try:
        target_path.write_text(
            json.dumps(new_doc, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        return emit_error("INTERNAL_ERROR", f"Cannot write {target_path}: {e}")

    # Seed Codex-native data dir from default_preferences.json
    data_dir = codex_dir / "audio-hooks-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    prefs_target = data_dir / "user_preferences.json"
    if not prefs_target.exists():
        default_prefs = PROJECT_ROOT / "config" / "default_preferences.json"
        if default_prefs.exists():
            try:
                prefs_target.write_text(default_prefs.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception:
                pass

    # AI-first feature-state handling. Current Codex enables hooks by default;
    # do not write or rewrite the user's config.toml from install --codex.
    config_path = codex_dir / "config.toml"
    flag_check = _check_codex_feature_flag(config_path)
    flag_state = flag_check["state"]
    next_steps: List[str] = []

    if flag_state in ("disabled", "disabled_legacy", "parse_error"):
        next_step = flag_check.get("next_step")
        if next_step:
            next_steps.append(next_step)

    next_steps.append(
        "Restart Codex (or reload the config) so it picks up the new hooks.json"
    )
    next_steps.append(
        "Trigger any agent action — Stop / PreToolUse should now play audio per "
        f"{data_dir}/user_preferences.json"
    )
    next_steps.append("Run `audio-hooks status` to confirm editor_targets.codex.state == 'active'")

    marker_path = data_dir / "install_marker.json"
    try:
        marker_path.write_text(
            json.dumps({
                "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "version": PROJECT_VERSION,
                "project_dir": str(PROJECT_ROOT),
                "hook_runner": hook_runner_abs,
                "python_bin": python_bin,
                "feature_flag_state": flag_state,
                "hooks_feature_state": flag_state,
                "config_path": str(config_path),
            }, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    emit({
        "ok": True,
        "mode": "codex",
        "hooks_file": str(target_path),
        "data_dir": str(data_dir),
        "config_path": str(config_path),
        "feature_flag_state": flag_state,
        "hooks_feature_state": flag_state,
        "next_steps": next_steps,
        "hint": (
            "Codex supports 10 of audio-hooks' 26 hook events (SessionStart, PreToolUse, "
            "PermissionRequest, PostToolUse, PreCompact, PostCompact, UserPromptSubmit, "
            "SubagentStart, SubagentStop, Stop). Other events no-op cleanly under the "
            "codex invoker."
        ),
    })
    return 0


def _uninstall_codex(*, purge: bool) -> int:
    """Remove audio-hooks-managed entries from ``$CODEX_HOME/hooks.json``.

    Preserves ``$CODEX_HOME/audio-hooks-data/`` by default so a future
    re-install picks up the user's preferences. ``--purge`` removes that
    directory too. Never touches ``$CODEX_HOME/config.toml``.
    """
    codex_dir = _codex_home()
    target_path = codex_dir / "hooks.json"
    removed_count = 0

    if target_path.exists():
        try:
            doc = json.loads(target_path.read_text(encoding="utf-8"))
        except Exception:
            doc = None
        if isinstance(doc, dict):
            hooks_block = doc.get("hooks")
            if isinstance(hooks_block, dict):
                pruned: Dict[str, Any] = {}
                for evt, entries in hooks_block.items():
                    if isinstance(entries, list):
                        keep = []
                        for e in entries:
                            if isinstance(e, dict) and e.get("_managed_by") == "audio-hooks":
                                removed_count += 1
                            else:
                                keep.append(e)
                        if keep:
                            pruned[evt] = keep
                doc["hooks"] = pruned
                for k in (
                    "_audio_hooks_managed", "_audio_hooks_version",
                    "_unsupported_in_codex", "_unsupported_note",
                    "_feature_flag_required",
                ):
                    doc.pop(k, None)
                non_meta_keys = [
                    k for k in doc.keys()
                    if k not in ("hooks",) and not k.startswith("_")
                ]
                if not pruned and not non_meta_keys:
                    try:
                        target_path.unlink()
                    except OSError:
                        pass
                else:
                    try:
                        target_path.write_text(
                            json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8",
                        )
                    except Exception as e:
                        return emit_error("INTERNAL_ERROR", f"Cannot rewrite {target_path}: {e}")

    data_dir = codex_dir / "audio-hooks-data"
    purged_data_dir = False
    if purge and data_dir.exists():
        import shutil as _sh
        try:
            _sh.rmtree(data_dir)
            purged_data_dir = True
        except Exception:
            pass

    emit({
        "ok": True,
        "mode": "codex",
        "removed_entries": removed_count,
        "data_dir": str(data_dir),
        "purged_data_dir": purged_data_dir,
        "next_steps": [
            "Restart Codex (or reload the config) so it stops invoking the audio-hooks runner",
            "Codex config.toml was left untouched",
        ],
    })
    return 0


def cmd_uninstall(args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    mode = "scripts"
    purge = False
    for a in args:
        if a == "--plugin":
            mode = "plugin"
        elif a == "--scripts":
            mode = "scripts"
        elif a == "--cursor":
            mode = "cursor"
        elif a == "--codex":
            mode = "codex"
        elif a == "--purge":
            purge = True
    if mode == "plugin":
        emit({
            "ok": True,
            "mode": "plugin",
            "next_steps": ["Run inside Claude Code: /plugin uninstall audio-hooks@chanmeng-audio-hooks"],
        })
        return 0
    if mode == "cursor":
        return _uninstall_cursor(purge=purge)
    if mode == "codex":
        return _uninstall_codex(purge=purge)
    import subprocess
    if platform.system() == "Windows":
        emit({"ok": True, "mode": "scripts", "hint": "Run scripts/uninstall.sh from Git Bash or WSL."})
        return 0
    cmd = ["bash", str(PROJECT_ROOT / "scripts" / "uninstall.sh")]
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
        emit({"ok": proc.returncode == 0, "mode": "scripts", "exit_code": proc.returncode})
        return 0 if proc.returncode == 0 else 1
    except Exception as e:
        return emit_error("INTERNAL_ERROR", str(e))


def _uninstall_cursor(*, purge: bool) -> int:
    """Remove audio-hooks-managed entries from ``~/.cursor/hooks.json``.

    By default preserves ``~/.cursor/audio-hooks-data/`` (so a future
    re-install picks up the user's preferences). ``--purge`` removes that
    directory too.
    """
    cursor_dir = Path.home() / ".cursor"
    target_path = cursor_dir / "hooks.json"
    removed_count = 0

    if target_path.exists():
        try:
            doc = json.loads(target_path.read_text(encoding="utf-8"))
        except Exception:
            doc = None
        if isinstance(doc, dict):
            hooks_block = doc.get("hooks")
            if isinstance(hooks_block, dict):
                pruned: Dict[str, Any] = {}
                for evt, entries in hooks_block.items():
                    if isinstance(entries, list):
                        keep = []
                        for e in entries:
                            if isinstance(e, dict) and e.get("_managed_by") == "audio-hooks":
                                removed_count += 1
                            else:
                                keep.append(e)
                        if keep:
                            pruned[evt] = keep
                doc["hooks"] = pruned
                # Strip our top-level meta keys if we authored the file alone
                for k in ("_audio_hooks_managed", "_audio_hooks_version",
                          "_unsupported_in_cursor", "_unsupported_note"):
                    doc.pop(k, None)
                # If hooks block is now empty AND the file looks like we own it
                # (no other top-level keys besides version/_comment), delete the file
                # entirely so we leave no trace. Otherwise rewrite preserving user's
                # other content.
                non_meta_keys = [k for k in doc.keys()
                                 if k not in ("version", "hooks") and not k.startswith("_")]
                if not pruned and not non_meta_keys:
                    try:
                        target_path.unlink()
                    except OSError:
                        pass
                else:
                    try:
                        target_path.write_text(
                            json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8",
                        )
                    except Exception as e:
                        return emit_error("INTERNAL_ERROR", f"Cannot rewrite {target_path}: {e}")

    data_dir = cursor_dir / "audio-hooks-data"
    purged_data_dir = False
    if purge and data_dir.exists():
        import shutil as _sh
        try:
            _sh.rmtree(data_dir)
            purged_data_dir = True
        except Exception:
            pass

    emit({
        "ok": True,
        "mode": "cursor",
        "removed_hook_entries": removed_count,
        "purged_data_dir": purged_data_dir,
        "data_dir_preserved": not purged_data_dir and data_dir.exists(),
        "hint": (
            "Restart Cursor IDE so it picks up the change. If you also want to"
            " stop Cursor's auto-bridge from firing the Claude Code plugin,"
            " uninstall the plugin via /plugin uninstall in Claude Code, or"
            " disable 'Third-party skills' in Cursor Settings."
        ),
    })
    return 0


# ---------------------------------------------------------------------------
# Status line catalog (Claude Code) + Codex curation
# ---------------------------------------------------------------------------

# Every segment the Claude Code status line script (bin/audio-hooks-statusline.py)
# can render, with the stdin field it reads. `conditional` segments render only
# when their data is present. Keep this in lock-step with the script's
# LINE1_SEGMENTS / LINE2_SEGMENTS so `statusline segments` is authoritative.
STATUSLINE_SEGMENTS: List[Dict[str, Any]] = [
    {"name": "model", "line": 1, "source": "model.display_name", "conditional": False, "description": "Active model display name"},
    {"name": "session_name", "line": 1, "source": "session_name", "conditional": True, "description": "Custom session name set via --name or /rename"},
    {"name": "agent", "line": 1, "source": "agent.name", "conditional": True, "description": "Agent name when running with --agent"},
    {"name": "effort", "line": 1, "source": "effort.level", "conditional": True, "description": "Reasoning effort (low/medium/high/xhigh/max)"},
    {"name": "thinking", "line": 1, "source": "thinking.enabled", "conditional": True, "description": "Shown when extended thinking is enabled"},
    {"name": "vim", "line": 1, "source": "vim.mode", "conditional": True, "description": "Vim editing mode (when vim mode is on)"},
    {"name": "output_style", "line": 1, "source": "output_style.name", "conditional": True, "description": "Active output style (hidden when 'default')"},
    {"name": "cc_version", "line": 1, "source": "version", "conditional": True, "description": "Claude Code's own version"},
    {"name": "cwd", "line": 1, "source": "cwd", "conditional": True, "description": "Working directory (abbreviated)"},
    {"name": "repo", "line": 1, "source": "workspace.repo", "conditional": True, "description": "Git remote owner/name"},
    {"name": "version", "line": 1, "source": "audio-hooks status", "conditional": False, "description": "echook version"},
    {"name": "sounds", "line": 1, "source": "audio-hooks status", "conditional": False, "description": "Enabled / total sound hooks"},
    {"name": "webhook", "line": 1, "source": "audio-hooks status", "conditional": False, "description": "Webhook on/off + format"},
    {"name": "theme", "line": 1, "source": "audio-hooks status", "conditional": False, "description": "Audio theme (Voice/Chimes)"},
    {"name": "snooze", "line": 2, "source": "audio-hooks status", "conditional": True, "description": "Mute countdown when snoozed"},
    {"name": "branch", "line": 2, "source": "workspace.git_worktree", "conditional": True, "description": "Git branch / worktree"},
    {"name": "git_dirty", "line": 2, "source": "git status --porcelain", "conditional": True, "description": "Uncommitted-change count (shells out to git; cached)"},
    {"name": "worktree", "line": 2, "source": "worktree.name", "conditional": True, "description": "Managed worktree name"},
    {"name": "pr", "line": 2, "source": "pr.number", "conditional": True, "description": "Pull request number + review state"},
    {"name": "added_dirs", "line": 2, "source": "workspace.added_dirs", "conditional": True, "description": "Count of /add-dir directories"},
    {"name": "api_quota", "line": 2, "source": "rate_limits.five_hour", "conditional": True, "description": "5-hour rate-limit usage + reset clock (date shown if not today)"},
    {"name": "weekly_quota", "line": 2, "source": "rate_limits.seven_day", "conditional": True, "description": "7-day rate-limit usage + reset clock (date + time, e.g. 'Jul 4 5am')"},
    {"name": "context", "line": 2, "source": "context_window", "conditional": True, "description": "Context-window usage % + token counts"},
    {"name": "tokens", "line": 2, "source": "context_window.current_usage", "conditional": True, "description": "Cache-hit ratio (cache reads ÷ input)"},
    {"name": "exceeds_200k", "line": 2, "source": "exceeds_200k_tokens", "conditional": True, "description": "Warning flag when tokens exceed 200K"},
    {"name": "cost", "line": 2, "source": "cost.total_cost_usd", "conditional": True, "description": "Session cost + lines added/removed"},
    {"name": "duration", "line": 2, "source": "cost.total_duration_ms", "conditional": True, "description": "Wall-clock session duration"},
    {"name": "api_time", "line": 2, "source": "cost.total_api_duration_ms", "conditional": True, "description": "Share of wall-clock spent waiting on the API"},
    {"name": "burn_rate", "line": 2, "source": "derived", "conditional": True, "description": "Cost velocity ($/hour)"},
]

# Codex's status line is NOT command-backed: it accepts only a fixed, ordered
# list of built-in item IDs under [tui].status_line in config.toml (command
# rendering is open feature request openai/codex#20140). echook can therefore
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
        "model-with-reasoning", "project-name", "git-branch", "branch-changes",
        "pull-request-number", "run-state", "approval-mode", "context-remaining",
        "used-tokens", "context-window-size", "five-hour-limit", "weekly-limit",
        "codex-version", "task-progress",
    ],
}

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
    codex_dir = Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"))
    return codex_dir / "config.toml"


def _cmd_statusline_codex(args: List[str]) -> int:
    """`audio-hooks statusline codex <show|preview|apply>` — curate Codex's fixed
    [tui] arrays (status_line and/or terminal_title) so they stop truncating."""
    action = args[0] if args else "show"
    rest = args[1:]
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


def cmd_statusline(args: List[str]) -> int:
    """Manage the status line: Claude Code registration + segment catalog, and
    Codex [tui].status_line curation."""
    if require_project_root() != 0:
        return 1
    sub = args[0] if args else "show"
    settings_path = Path.home() / ".claude" / "settings.json"

    if sub == "segments":
        emit({
            "ok": True,
            "segments": STATUSLINE_SEGMENTS,
            "line1": [s["name"] for s in STATUSLINE_SEGMENTS if s["line"] == 1],
            "line2": [s["name"] for s in STATUSLINE_SEGMENTS if s["line"] == 2],
            "config": {
                "visible_segments": "Whitelist — when non-empty, only these show.",
                "hidden_segments": "Blacklist — applied when visible_segments is empty; show all except these.",
                "set_example": "audio-hooks set statusline_settings.hidden_segments '[\"burn_rate\",\"api_time\"]'",
            },
        })
        return 0

    if sub == "codex":
        return _cmd_statusline_codex(args[1:])

    if sub == "show":
        statusline_script = PROJECT_ROOT / "bin" / "audio-hooks-statusline.py"
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
        statusline_script = PROJECT_ROOT / "bin" / "audio-hooks-statusline.py"
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
# Subcommand: backup (list / show / restore / prune)
# ---------------------------------------------------------------------------

def cmd_backup(args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    if not args:
        return emit_error(
            "INVALID_USAGE",
            "Usage: audio-hooks backup <list|show|restore|prune>",
            suggested_command="audio-hooks manifest",
        )
    sub = args[0]
    rest = args[1:]
    prefs = _prefs()
    if sub == "list":
        try:
            entries = prefs.list_backups()
        except Exception as e:
            return emit_error("INTERNAL_ERROR", str(e))
        emit({
            "ok": True,
            "backups": entries,
            "count": len(entries),
            "external_dir": str(prefs.external_backup_dir),
            "sibling_path": str(prefs.sibling_backup_path),
        })
        return 0
    if sub == "show":
        if not rest:
            return emit_error("INVALID_USAGE", "Usage: audio-hooks backup show <id>")
        backup_id = rest[0]
        try:
            entries = prefs.list_backups()
        except Exception as e:
            return emit_error("INTERNAL_ERROR", str(e))
        match = next((e for e in entries if e["id"] == backup_id), None)
        if match is None:
            return emit_error(
                "BACKUP_NOT_FOUND",
                f"No backup with id={backup_id}",
                suggested_command="audio-hooks backup list",
            )
        try:
            content = json.loads(Path(match["path"]).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return emit_error(
                "RESTORE_FAILED",
                f"Backup unreadable: {e}",
                suggested_command="audio-hooks backup list",
            )
        emit({
            "ok": True,
            "id": backup_id,
            "location": match["location"],
            "from_version": match.get("from_version"),
            "content": content,
        })
        return 0
    if sub == "restore":
        if not rest:
            return emit_error(
                "INVALID_USAGE",
                "Usage: audio-hooks backup restore <id|latest|latest-sibling|latest-external>",
            )
        backup_id = rest[0]
        try:
            restored = prefs.restore_from(backup_id)
        except FileNotFoundError as e:
            return emit_error(
                "BACKUP_NOT_FOUND",
                str(e),
                suggested_command="audio-hooks backup list",
            )
        except ValueError as e:
            return emit_error(
                "RESTORE_FAILED",
                str(e),
                suggested_command="audio-hooks backup list",
            )
        emit({
            "ok": True,
            "restored_from": backup_id,
            "audio_theme": restored.get("audio_theme"),
            "version": restored.get("_version"),
        })
        return 0
    if sub == "prune":
        try:
            removed = prefs.prune_backups()
        except Exception as e:
            return emit_error("INTERNAL_ERROR", str(e))
        emit({
            "ok": True,
            "removed": removed,
            "kept_max": prefs.EXTERNAL_BACKUP_KEEP,
        })
        return 0
    return emit_error(
        "INVALID_USAGE",
        f"Unknown backup subcommand: {sub}",
        suggested_command="audio-hooks backup list",
    )


# ---------------------------------------------------------------------------
# Subcommand: upgrade (refresh the plugin code without losing config)
# ---------------------------------------------------------------------------

def cmd_upgrade(args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    check_only = "--check-only" in args
    force = "--force" in args
    PLUGIN_ID = "audio-hooks@chanmeng-audio-hooks"

    # Resolve the `claude` executable explicitly. subprocess on Windows
    # without shell=True only finds .exe files for bare names — using
    # shutil.which lets us also locate .cmd/.bat shims (used by tests
    # and by some installer flavors).
    import shutil
    claude_exe = shutil.which("claude")
    if not claude_exe:
        return emit_error(
            "INTERNAL_ERROR",
            "`claude` CLI not on PATH; cannot upgrade",
            suggested_command="install Claude Code first",
        )

    # 1. Detect current install state via claude plugin list --json
    try:
        proc = subprocess.run(
            [claude_exe, "plugin", "list", "--json"],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
    except FileNotFoundError:
        return emit_error(
            "INTERNAL_ERROR",
            "`claude` CLI not on PATH; cannot upgrade",
            suggested_command="install Claude Code first",
        )
    if proc.returncode != 0:
        return emit_error(
            "INTERNAL_ERROR",
            f"`claude plugin list` failed: {proc.stderr.strip()}",
        )
    try:
        plugins = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return emit_error("INTERNAL_ERROR", f"Cannot parse plugin list: {e}")

    entry = next((p for p in plugins if p.get("id") == PLUGIN_ID), None)
    if entry is None:
        return emit_error(
            "NOT_INSTALLED",
            f"{PLUGIN_ID} is not installed in any scope",
            suggested_command="audio-hooks install --plugin",
        )

    current_scope = entry.get("scope", "user")
    current_version = entry.get("version", "unknown")

    # 2. --check-only path
    if check_only:
        emit({
            "ok": True,
            "current_version": current_version,
            "scope": current_scope,
            "would_upgrade": current_version != PROJECT_VERSION,
            "target_version": PROJECT_VERSION,
        })
        return 0

    # 3. Marker
    marker_dir = Path.home() / ".claude-audio-hooks-backups"
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / ".upgrade_in_progress.json"
    if marker.exists() and not force:
        try:
            existing = json.loads(marker.read_text(encoding="utf-8"))
            return emit_error(
                "PRIOR_UPGRADE_INCOMPLETE",
                "A previous upgrade did not complete; investigate before retrying",
                suggested_command="audio-hooks status",
                previous=existing,
            )
        except (OSError, ValueError):
            pass
    marker.write_text(json.dumps({
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "from_version": current_version,
        "scope": current_scope,
        "recovery_command": f"python {PROJECT_ROOT}/bin/audio-hooks.py upgrade --force",
    }, indent=2), encoding="utf-8")

    # 4. Try `claude plugin update` first (data-preserving)
    update_proc = subprocess.run(
        [claude_exe, "plugin", "update", PLUGIN_ID, "--scope", current_scope],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    used_path = "update"
    if update_proc.returncode != 0:
        # 5. Fallback: uninstall --keep-data + install
        uninstall_proc = subprocess.run(
            [claude_exe, "plugin", "uninstall", PLUGIN_ID, "--keep-data",
             "--scope", current_scope, "-y"],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        if uninstall_proc.returncode != 0:
            return emit_error(
                "UPGRADE_UNINSTALL_FAILED",
                uninstall_proc.stderr.strip() or "uninstall failed",
                suggested_command=f"claude plugin uninstall {PLUGIN_ID} --keep-data --scope {current_scope}",
            )
        install_proc = subprocess.run(
            [claude_exe, "plugin", "install", PLUGIN_ID, "--scope", current_scope],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        if install_proc.returncode != 0:
            return emit_error(
                "UPGRADE_REINSTALL_FAILED",
                install_proc.stderr.strip() or "install failed",
                suggested_command=f"claude plugin install {PLUGIN_ID} --scope {current_scope}",
            )
        used_path = "uninstall+install"

    # 6. Verify
    verify_proc = subprocess.run(
        [claude_exe, "plugin", "list", "--json"],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    if verify_proc.returncode != 0:
        # Upgrade itself succeeded; we just couldn't verify. Delete the
        # marker so future runs aren't misleadingly blocked.
        try:
            marker.unlink()
        except OSError:
            pass
        return emit_error(
            "UPGRADE_VERIFY_FAILED",
            "Upgrade completed but post-upgrade `claude plugin list` failed; run `audio-hooks upgrade --check-only` to confirm new version.",
            upgrade_may_have_completed=True,
            via=used_path,
        )
    try:
        new_plugins = json.loads(verify_proc.stdout)
    except json.JSONDecodeError:
        new_plugins = []
    new_entry = next((p for p in new_plugins if p.get("id") == PLUGIN_ID), None)
    new_version = new_entry["version"] if new_entry else "unknown"

    # 7. Delete marker
    try:
        marker.unlink()
    except OSError:
        pass

    # 8. Trigger migration
    migration_info: Dict[str, Any] = {}
    try:
        from user_preferences import _reset_prefs  # type: ignore
        # Reset cache so next load picks up post-upgrade paths
        _reset_prefs()
        prefs = _prefs()
        cfg = prefs.load()
        migration_info = {"current_version": cfg.get("_version")}
    except Exception as e:
        migration_info = {"warning": f"migration_skipped: {e}"}

    emit({
        "ok": True,
        "from_version": current_version,
        "to_version": new_version,
        "scope": current_scope,
        "data_preserved": True,
        "via": used_path,
        "config": migration_info,
    })
    return 0


# ---------------------------------------------------------------------------
# Subcommand: manifest (the keystone)
# ---------------------------------------------------------------------------

def _build_manifest_schema() -> Dict[str, Any]:
    """JSON Schema for user_preferences.json."""
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "echook user preferences",
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "audio_theme": {"type": "string", "enum": ["default", "custom"], "default": "default"},
            "enabled_hooks": {
                "type": "object",
                "additionalProperties": {"type": "boolean"},
                "description": "Per-hook enable flags. Keys are hook names from `audio-hooks hooks list`.",
            },
            "playback_settings": {
                "type": "object",
                "properties": {
                    "debounce_ms": {"type": "integer", "minimum": 0},
                },
            },
            "notification_settings": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["audio_only", "notification_only", "audio_and_notification", "disabled"]},
                    "show_context": {"type": "boolean"},
                    "detail_level": {"type": "string", "enum": ["minimal", "standard", "verbose"]},
                    "per_hook": {"type": "object"},
                },
            },
            "filters": {"type": "object", "description": "Per-hook regex filters on stdin fields."},
            "webhook_settings": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "url": {"type": "string"},
                    "format": {"type": "string", "enum": ["slack", "discord", "teams", "ntfy", "raw"]},
                    "hook_types": {"type": "array", "items": {"type": "string"}},
                    "headers": {"type": "object"},
                },
            },
            "tts_settings": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "speak_assistant_message": {"type": "boolean"},
                    "assistant_message_max_chars": {"type": "integer", "minimum": 10, "maximum": 1000},
                    "messages": {"type": "object"},
                },
            },
            "rate_limit_alerts": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "five_hour_thresholds": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 100}},
                    "seven_day_thresholds": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 100}},
                    "audio": {"type": "string"},
                },
            },
        },
    }


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
            {"name": "install", "args": ["[--plugin|--scripts|--cursor|--codex]", "[--force]"], "description": "Install non-interactively. --cursor writes ~/.cursor/hooks.json for Cursor IDE users. --codex writes $CODEX_HOME/hooks.json for Codex CLI users. --force overrides DUPLICATE_BRIDGE check (cursor only)."},
            {"name": "uninstall", "args": ["[--plugin|--scripts|--cursor|--codex]", "[--purge]"], "description": "Uninstall non-interactively. --cursor / --codex remove audio-hooks-managed entries from the corresponding hooks.json (--purge also removes the audio-hooks-data directory)."},
            {"name": "statusline show", "args": [], "description": "Show Claude Code status line registration state"},
            {"name": "statusline install", "args": [], "description": "Register the echook status line in ~/.claude/settings.json"},
            {"name": "statusline uninstall", "args": [], "description": "Remove the echook status line registration"},
            {"name": "statusline segments", "args": [], "description": "List every Claude Code status line segment (name, line, source field, conditional) for configuring visible_segments / hidden_segments"},
            {"name": "statusline codex show", "args": [], "description": "Show the current Codex [tui].status_line + terminal_title and whether they likely overflow"},
            {"name": "statusline codex preview", "args": ["[--preset minimal|balanced|full]", "[--items a,b,c]", "[--target status_line|terminal_title|both]"], "description": "Print the curated Codex status_line / terminal_title that would be written (no write)"},
            {"name": "statusline codex apply", "args": ["[--preset minimal|balanced|full]", "[--items a,b,c]", "[--target status_line|terminal_title|both]"], "description": "Curate Codex [tui].status_line and/or terminal_title in config.toml (backs up first) so they stop truncating. Codex accepts only fixed item IDs — echook curates, it cannot render custom text."},
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
            "statusline_settings.max_width",
        ],
        "themes": ["default", "custom"],
        "log_schema": "audio-hooks.v1",
        "webhook_schema": "audio-hooks.webhook.v1",
        "error_codes": error_codes,
        "editor_targets": _detect_editor_targets(),
        "supported_editors": {
            "claude-code": {
                "events": [h["name"] for h in HOOK_CATALOG],
                "install_via": "/plugin install audio-hooks@chanmeng-audio-hooks",
            },
            "cursor": {
                "auto_bridge": "Cursor IDE 3.2.16+ auto-bridges Claude Code plugin hooks. Toggleable via Cursor Settings > Third-party skills.",
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
                ],
                "unsupported_events": [
                    "notification",
                    "session_end",
                    "elicitation",
                    "elicitation_result",
                    "cwd_changed",
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
                "doc_url": "https://developers.openai.com/codex/hooks",
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
            "claude_md": "CLAUDE.md",
            "skill": "plugins/audio-hooks/skills/audio-hooks/SKILL.md",
            "readme": "README.md",
            "installation_guide": "docs/INSTALLATION_GUIDE.md",
            "changelog": "CHANGELOG.md",
            "architecture": "docs/ARCHITECTURE.md",
            "troubleshooting": "docs/TROUBLESHOOTING.md",
            "status_line": "docs/STATUS_LINE.md",
            "canonical_sources": [
                "hooks/", "bin/", "audio/", "config/",
                "cursor-hooks/", "codex-hooks/",
            ],
            "_note": "All paths are relative to the project root reported in `audio-hooks status.project_dir`.",
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
}


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        # No-arg invocation returns the manifest as the canonical introspection target
        return cmd_manifest([])
    cmd = argv[1]
    if cmd in ("-h", "--help", "help"):
        return cmd_manifest([])
    fn = DISPATCH.get(cmd)
    if fn is None:
        return emit_error("INVALID_USAGE", f"Unknown subcommand: {cmd}", suggested_command="audio-hooks manifest")
    try:
        return fn(argv[2:])
    except Exception as e:
        return emit_error("INTERNAL_ERROR", str(e))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
