#!/usr/bin/env python3
"""
echook - Python Hook Runner
Cross-platform hook runner that works on Windows, macOS, and Linux.
This replaces the bash-based hooks for better Windows compatibility.

Usage:
    python hook_runner.py <hook_type>

Hook types: notification, stop, pretooluse, posttooluse, posttoolusefailure,
            userpromptsubmit, subagent_stop, subagent_start, precompact,
            session_start, session_end, permission_request,
            teammate_idle, task_completed, stop_failure, postcompact,
            config_change, instructions_loaded, elicitation,
            elicitation_result

Environment Variables:
    CLAUDE_HOOKS_DEBUG=1    Enable debug logging
"""

import json
import os
import shutil
import sys
import time
import subprocess
import platform
import re
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

# Ensure the hooks/ dir is importable so `user_preferences` resolves whether
# this module is invoked directly (`python hooks/hook_runner.py`) or loaded
# via importlib.util.spec_from_file_location (used by the test harness).
_HOOKS_DIR = str(Path(__file__).resolve().parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from user_preferences import UserPreferences, get_prefs  # type: ignore  # noqa: E402
from invoker import detect_invoker, get_invoker as _get_invoker, strip_invoker_args  # type: ignore  # noqa: E402

# Version used for auto-sync: when the installed copy in ~/.claude/hooks/
# detects a newer version in the project directory, it self-updates.
HOOK_RUNNER_VERSION = "6.7.2"

# =============================================================================
# STRUCTURED LOGGING (NDJSON)
# =============================================================================
#
# All log events are written as one JSON object per line to events.ndjson.
# Schema is versioned ("audio-hooks.v1") so downstream consumers can pin.
# Error events include a stable `code` enum, a one-sentence `hint`, and an
# optional `suggested_command` Claude Code can run to fix the issue.
#
# Storage location, in priority order (all derived from UserPreferences.data_dir):
#   1. ${CLAUDE_PLUGIN_DATA}/logs/                  (Claude Code plugin invoke)
#   2. ${CLAUDE_AUDIO_HOOKS_DATA}/logs/             (explicit override)
#   3. ~/.claude/plugins/data/audio-hooks-chanmeng-audio-hooks/logs/
#                                                    (Claude Code plugin shared
#                                                    state, used when CLAUDE_PLUGIN_DATA
#                                                    is unset — e.g. Cursor's
#                                                    auto-bridge or CLI run from
#                                                    plugin's bin/ PATH)
#   4. ~/.cursor/audio-hooks-data/logs/             (Cursor-native install,
#                                                    when no Claude Code present)
#   5. <temp>/claude_audio_hooks_queue/logs/        (legacy script install)

DEBUG = os.environ.get("CLAUDE_HOOKS_DEBUG", "").lower() in ("1", "true", "yes")

LOG_SCHEMA = "audio-hooks.v1"
LOG_FILE_NAME = "events.ndjson"
LOG_ROTATE_BYTES = 5 * 1024 * 1024  # 5 MB
LOG_KEEP_FILES = 3

# Stable error code enum. Add new codes here, never rename existing ones.
class ErrorCode:
    AUDIO_FILE_MISSING = "AUDIO_FILE_MISSING"
    AUDIO_PLAYER_NOT_FOUND = "AUDIO_PLAYER_NOT_FOUND"
    AUDIO_PLAY_FAILED = "AUDIO_PLAY_FAILED"
    INVALID_CONFIG = "INVALID_CONFIG"
    CONFIG_READ_ERROR = "CONFIG_READ_ERROR"
    WEBHOOK_HTTP_ERROR = "WEBHOOK_HTTP_ERROR"
    WEBHOOK_TIMEOUT = "WEBHOOK_TIMEOUT"
    NOTIFICATION_FAILED = "NOTIFICATION_FAILED"
    TTS_FAILED = "TTS_FAILED"
    SETTINGS_DISABLE_ALL_HOOKS = "SETTINGS_DISABLE_ALL_HOOKS"
    PROJECT_DIR_NOT_FOUND = "PROJECT_DIR_NOT_FOUND"
    SELF_UPDATE_FAILED = "SELF_UPDATE_FAILED"
    UNKNOWN_HOOK_TYPE = "UNKNOWN_HOOK_TYPE"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    # v5.1.6: emitted by run_hook() when invoker == cursor and the install
    # marker records duplicate_bridge_forced: true. The native Cursor path
    # silently defers to Claude Code's auto-bridge to prevent double audio.
    DUPLICATE_BRIDGE_RUNTIME_SKIP = "DUPLICATE_BRIDGE_RUNTIME_SKIP"


# Hints and suggested commands for each error code. Used by log_error_event().
_ERROR_HINTS: Dict[str, Dict[str, str]] = {
    ErrorCode.AUDIO_FILE_MISSING: {
        "hint": "The configured audio file does not exist on disk.",
        "suggested_command": "audio-hooks diagnose",
    },
    ErrorCode.AUDIO_PLAYER_NOT_FOUND: {
        "hint": "No audio player binary found on this system.",
        "suggested_command": "audio-hooks diagnose",
    },
    ErrorCode.AUDIO_PLAY_FAILED: {
        "hint": "The audio player exited with an error.",
        "suggested_command": "audio-hooks test",
    },
    ErrorCode.INVALID_CONFIG: {
        "hint": "user_preferences.json is missing or invalid JSON.",
        "suggested_command": "audio-hooks manifest --schema",
    },
    ErrorCode.CONFIG_READ_ERROR: {
        "hint": "Could not read user_preferences.json.",
        "suggested_command": "audio-hooks status",
    },
    ErrorCode.WEBHOOK_HTTP_ERROR: {
        "hint": "Webhook endpoint returned a non-2xx response.",
        "suggested_command": "audio-hooks webhook test",
    },
    ErrorCode.WEBHOOK_TIMEOUT: {
        "hint": "Webhook request timed out.",
        "suggested_command": "audio-hooks webhook test",
    },
    ErrorCode.NOTIFICATION_FAILED: {
        "hint": "Desktop notification dispatch failed.",
        "suggested_command": "audio-hooks diagnose",
    },
    ErrorCode.TTS_FAILED: {
        "hint": "Text-to-speech engine failed or is not installed.",
        "suggested_command": "audio-hooks tts set --enabled false",
    },
    ErrorCode.SETTINGS_DISABLE_ALL_HOOKS: {
        "hint": "Claude Code settings.json has disableAllHooks: true; no hooks fire.",
        "suggested_command": "audio-hooks diagnose",
    },
    ErrorCode.PROJECT_DIR_NOT_FOUND: {
        "hint": "Could not locate the project directory.",
        "suggested_command": "audio-hooks status",
    },
    ErrorCode.SELF_UPDATE_FAILED: {
        "hint": "Auto-sync from the project directory failed.",
        "suggested_command": "audio-hooks update",
    },
    ErrorCode.UNKNOWN_HOOK_TYPE: {
        "hint": "Hook runner was invoked with an unrecognized hook type.",
        "suggested_command": "audio-hooks hooks list",
    },
    ErrorCode.INTERNAL_ERROR: {
        "hint": "An unexpected internal error occurred.",
        "suggested_command": "audio-hooks logs tail",
    },
    ErrorCode.DUPLICATE_BRIDGE_RUNTIME_SKIP: {
        "hint": (
            "Cursor-native install was forced over an active Claude Code"
            " bridge; the runtime is skipping the native firing path so"
            " Claude Code's bridge handles the event alone."
        ),
        "suggested_command": "audio-hooks uninstall --cursor",
    },
}


# detect_invoker / _get_invoker live in invoker.py (imported above) so
# user_preferences.py can ask the same question without a circular import.
# When the runner is launched with ``--invoker codex`` (the form Codex's
# hooks.json template uses), ``detect_invoker`` returns ``"codex"`` from
# argv parsing before any env-var checks.


def _invoker_display_name() -> str:
    """Human-readable app name for desktop and webhook notifications."""
    invoker = _get_invoker()
    if invoker == "codex":
        return "Codex"
    if invoker == "cursor":
        return "Cursor"
    return "Claude Code"


def _prefs() -> UserPreferences:
    """Return the process-wide UserPreferences singleton, anchored at PROJECT_DIR."""
    return get_prefs(PROJECT_DIR)


def get_log_dir() -> Path:
    """Resolve the log directory, creating it if necessary.

    Backwards-compatible wrapper around :attr:`UserPreferences.log_dir`.
    """
    log_dir = _prefs().log_dir
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return log_dir


def _rotate_log_if_needed(log_file: Path) -> None:
    """Rotate the log file when it exceeds LOG_ROTATE_BYTES."""
    try:
        if not log_file.exists():
            return
        if log_file.stat().st_size < LOG_ROTATE_BYTES:
            return
        # Shift existing rotated files: events.ndjson.2 -> .3, .1 -> .2, base -> .1
        for i in range(LOG_KEEP_FILES - 1, 0, -1):
            src = log_file.with_suffix(log_file.suffix + f".{i}")
            dst = log_file.with_suffix(log_file.suffix + f".{i + 1}")
            if src.exists():
                if dst.exists():
                    try:
                        dst.unlink()
                    except OSError:
                        pass
                try:
                    src.rename(dst)
                except OSError:
                    pass
        try:
            log_file.rename(log_file.with_suffix(log_file.suffix + ".1"))
        except OSError:
            pass
    except Exception:
        pass


# Per-process session_id, set by run_hook() once stdin has been parsed.
_current_session_id: Optional[str] = None
_current_hook_type: Optional[str] = None


def _set_log_context(session_id: Optional[str], hook_type: Optional[str]) -> None:
    """Set the per-process log context once at hook entry."""
    global _current_session_id, _current_hook_type
    _current_session_id = session_id
    _current_hook_type = hook_type


# _get_invoker is imported from invoker.py — see import block above.


# install_marker.json is written by ``audio-hooks install --cursor`` into the
# Cursor-native data dir. We read it once per process (None means "not yet
# attempted"; {} means "attempted, none found or unreadable") so the runtime
# can detect whether the operator forced a native install on top of an already-
# active Claude Code bridge — the only way a Cursor session can fire audio
# twice.
_install_marker_cache: Optional[Dict[str, Any]] = None


def _read_install_marker() -> Dict[str, Any]:
    """Read ``${data_dir}/install_marker.json`` once per process.

    Returns an empty dict when the file is missing, unreadable, or not JSON.
    Never raises. The marker is only present after ``audio-hooks install
    --cursor`` has run (Claude Code's plugin install does not write one), so
    its absence is the common case and must be silent.
    """
    global _install_marker_cache
    if _install_marker_cache is not None:
        return _install_marker_cache
    try:
        marker_path = _prefs().data_dir / "install_marker.json"
        if marker_path.exists():
            with open(marker_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                _install_marker_cache = data
                return _install_marker_cache
    except Exception:
        pass
    _install_marker_cache = {}
    return _install_marker_cache


def log_event(level: str, action: str, hook: Optional[str] = None, **fields: Any) -> None:
    """Write one NDJSON event line.

    Always non-blocking on errors. Never raises. Never writes to stdout/stderr.
    """
    if level == "debug" and not DEBUG:
        return
    try:
        event: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int((time.time() % 1) * 1000):03d}Z",
            "schema": LOG_SCHEMA,
            "level": level,
            "hook": hook if hook is not None else _current_hook_type,
        }
        if _current_session_id:
            event["session_id"] = _current_session_id
        # invoker is populated unconditionally so a single events.ndjson can be
        # filtered by editor (claude-code vs cursor) for cross-IDE diagnostics.
        event["invoker"] = _get_invoker()
        event["action"] = action
        for k, v in fields.items():
            if v is not None:
                event[k] = v
        log_file = get_log_dir() / LOG_FILE_NAME
        _rotate_log_if_needed(log_file)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


def log_error_event(code: str, action: str, message: str = "", hook: Optional[str] = None, **fields: Any) -> None:
    """Emit a structured error event with hint and suggested_command."""
    meta = _ERROR_HINTS.get(code, {})
    error_obj: Dict[str, Any] = {"code": code, "message": message}
    if meta.get("hint"):
        error_obj["hint"] = meta["hint"]
    if meta.get("suggested_command"):
        error_obj["suggested_command"] = meta["suggested_command"]
    log_event("error", action, hook=hook, error=error_obj, **fields)


# ---------------------------------------------------------------------------
# Backwards-compatible wrappers
# ---------------------------------------------------------------------------
# The legacy log_debug / log_error / log_trigger functions stay so existing
# call sites in this file (and any third-party patches) keep working.
# Internally they all funnel through log_event() now, so the on-disk format is
# always NDJSON regardless of which helper was called.

def log_debug(message: str) -> None:
    if DEBUG:
        log_event("debug", "debug", message=message)


def log_error(message: str) -> None:
    log_event("error", "legacy_error", message=message)


def log_trigger(hook_type: str, status: str, details: str = "") -> None:
    """Legacy hook-status trigger. Maps to log_event with action=hook_status."""
    fields: Dict[str, Any] = {"status": status}
    if details:
        fields["details"] = details
    log_event("info", "hook_status", hook=hook_type, **fields)

# =============================================================================
# PATH UTILITIES
# =============================================================================

def normalize_path(path_str: str) -> str:
    """Convert various path formats to the platform's native format.

    Handles:
    - Git Bash/MSYS2: /c/Users/... -> C:/Users/...
    - WSL2: /mnt/c/Users/... -> C:/Users/...
    - Cygwin: /cygdrive/c/... -> C:/...
    """
    if platform.system() != "Windows":
        return path_str

    path_str = path_str.strip()

    log_debug(f"normalize_path input: {path_str}")

    # Handle WSL2 style paths: /mnt/c/... -> C:/...
    if path_str.startswith("/mnt/") and len(path_str) >= 6:
        drive_letter = path_str[5].upper()
        if drive_letter.isalpha():
            rest = path_str[6:] if len(path_str) > 6 else "/"
            result = f"{drive_letter}:{rest}"
            log_debug(f"normalize_path WSL2: {path_str} -> {result}")
            return result

    # Handle Cygwin style paths: /cygdrive/c/... -> C:/...
    if path_str.startswith("/cygdrive/") and len(path_str) >= 11:
        drive_letter = path_str[10].upper()
        if drive_letter.isalpha():
            rest = path_str[11:] if len(path_str) > 11 else "/"
            result = f"{drive_letter}:{rest}"
            log_debug(f"normalize_path Cygwin: {path_str} -> {result}")
            return result

    # Handle Git Bash/MSYS2 style paths: /d/... -> D:/...
    if len(path_str) >= 2 and path_str[0] == '/' and path_str[1].isalpha():
        drive_letter = path_str[1].upper()
        if len(path_str) == 2:
            result = f"{drive_letter}:/"
        elif path_str[2] == '/':
            result = f"{drive_letter}:{path_str[2:]}"
        else:
            # Not a drive path, return as-is
            return path_str
        log_debug(f"normalize_path Git Bash: {path_str} -> {result}")
        return result

    return path_str


def escape_powershell_string(s: str) -> str:
    """Escape a string for safe use in PowerShell double-quoted strings."""
    # Escape backticks, double quotes, and dollar signs
    s = s.replace('`', '``')
    s = s.replace('"', '`"')
    s = s.replace('$', '`$')
    return s


def get_safe_temp_dir() -> Path:
    """Get a safe temporary directory that exists and is writable."""
    candidates: List[Path] = []

    if platform.system() == "Windows":
        # Windows: prefer TEMP, then TMP, then USERPROFILE/Temp, then fallback
        for env_var in ["TEMP", "TMP"]:
            val = os.environ.get(env_var)
            if val:
                candidates.append(Path(val))

        userprofile = os.environ.get("USERPROFILE")
        if userprofile:
            candidates.append(Path(userprofile) / "AppData" / "Local" / "Temp")

        # Windows fallback
        windir = os.environ.get("WINDIR", "C:/Windows")
        candidates.append(Path(windir) / "Temp")
        candidates.append(Path("C:/Windows/Temp"))
    else:
        # Unix: prefer TMPDIR, then standard locations
        tmpdir = os.environ.get("TMPDIR")
        if tmpdir:
            candidates.append(Path(tmpdir))
        candidates.extend([
            Path("/tmp"),
            Path("/var/tmp"),
            Path.home() / ".cache" / "claude_hooks_temp",
        ])

    # Find first existing and writable directory
    for candidate in candidates:
        try:
            if candidate.exists() and os.access(str(candidate), os.W_OK):
                log_debug(f"Using temp dir: {candidate}")
                return candidate
        except Exception:
            continue

    # Last resort: create in home directory
    fallback = Path.home() / ".cache" / "claude_hooks_temp"
    fallback.mkdir(parents=True, exist_ok=True)
    log_debug(f"Using fallback temp dir: {fallback}")
    return fallback

# =============================================================================
# AUTO-SYNC (self-update from project directory)
# =============================================================================

def check_and_self_update() -> None:
    """If running from ~/.claude/hooks/, check the project copy for a newer version.

    When a newer version is found in the project directory, copy it over the
    installed copy and re-execute so the user always runs the latest code after
    a `git pull`.  The entire function is wrapped in a try/except so it never
    blocks hook execution.
    """
    try:
        installed_path = Path(__file__).resolve()

        # Only run when executing from ~/.claude/hooks/ (not the project dir)
        claude_hooks_dir = Path.home() / ".claude" / "hooks"
        if not str(installed_path).startswith(str(claude_hooks_dir)):
            return

        # Read .project_path to find the project copy
        project_path_file = claude_hooks_dir / ".project_path"
        if not project_path_file.exists():
            return

        raw_path = project_path_file.read_text(encoding="utf-8-sig").strip()
        raw_path = normalize_path(raw_path)
        project_runner = Path(raw_path) / "hooks" / "hook_runner.py"
        if not project_runner.exists():
            return

        # Extract HOOK_RUNNER_VERSION from the project copy
        project_source = project_runner.read_text(encoding="utf-8")
        match = re.search(r'^HOOK_RUNNER_VERSION\s*=\s*["\']([^"\']+)["\']',
                          project_source, re.MULTILINE)
        if not match:
            return

        project_version = match.group(1)
        # Simple tuple comparison: "4.2.2" -> (4, 2, 2)
        def ver_tuple(v: str):
            return tuple(int(x) for x in v.split("."))

        if ver_tuple(project_version) <= ver_tuple(HOOK_RUNNER_VERSION):
            return

        # Project copy is newer — update ourselves
        shutil.copy2(str(project_runner), str(installed_path))

        # Re-execute with the same arguments so the new code runs
        os.execv(sys.executable, [sys.executable, str(installed_path)] + sys.argv[1:])

    except Exception:
        # Never block hook execution
        pass

# =============================================================================
# CONFIGURATION
# =============================================================================

def get_project_dir() -> Path:
    """Determine the project directory."""
    script_dir = Path(__file__).resolve().parent
    log_debug(f"Script dir: {script_dir}")

    # Strategy 1: Read from .project_path file
    project_path_file = script_dir / ".project_path"
    if project_path_file.exists():
        try:
            recorded_path = project_path_file.read_text(encoding="utf-8-sig").strip()  # utf-8-sig handles BOM
            log_debug(f"Read .project_path: {recorded_path}")
            # Normalize path format for Windows compatibility
            recorded_path = normalize_path(recorded_path)
            recorded_path_obj = Path(recorded_path)
            if recorded_path_obj.exists() and (recorded_path_obj / "config" / "user_preferences.json").exists():
                log_debug(f"Using project dir from .project_path: {recorded_path_obj}")
                return recorded_path_obj
            else:
                log_debug(f"Project path invalid or config missing: {recorded_path_obj}")
        except Exception as e:
            log_error(f"Failed to read .project_path: {e}")

    # Strategy 2: Check if we're in the project structure
    candidate = script_dir.parent
    if (candidate / "config" / "user_preferences.json").exists():
        log_debug(f"Using parent dir as project dir: {candidate}")
        return candidate

    # Strategy 3: Search common locations
    home = Path.home()
    common_locations = [
        home / "echook",
        home / "projects" / "echook",
        home / "Documents" / "echook",
        home / "repos" / "echook",
    ]

    for loc in common_locations:
        if loc.exists() and (loc / "config" / "user_preferences.json").exists():
            log_debug(f"Found project in common location: {loc}")
            return loc

    # Fallback
    log_debug(f"Using fallback project dir: {candidate}")
    return candidate


# Initialize paths
PROJECT_DIR = get_project_dir()
AUDIO_DIR = PROJECT_DIR / "audio"


# Path resolution lives in :class:`UserPreferences` (see
# ``hooks/user_preferences.py``). The pre-5.1.5 module-level globals and
# resolver helpers are gone; every consumer now goes through ``_prefs()``
# so the CLI and the runtime always agree on which ``user_preferences.json``
# file is canonical.

_queue_dir_ensured = False


def ensure_queue_dir() -> None:
    """Ensure queue directory exists (lazy, called on first use)."""
    global _queue_dir_ensured
    if not _queue_dir_ensured:
        _prefs().queue_dir.mkdir(parents=True, exist_ok=True)
        _queue_dir_ensured = True

# Default audio files for each hook type
DEFAULT_AUDIO_FILES = {
    "notification": "notification-urgent.mp3",
    "stop": "task-complete.mp3",
    "pretooluse": "task-starting.mp3",
    "posttooluse": "task-progress.mp3",
    "userpromptsubmit": "prompt-received.mp3",
    "subagent_stop": "subagent-complete.mp3",
    "precompact": "pre-compact.mp3",
    "session_start": "session-start.mp3",
    "session_end": "session-end.mp3",
    "permission_request": "permission-request.mp3",
    "posttoolusefailure": "tool-failed.mp3",
    "subagent_start": "subagent-start.mp3",
    "teammate_idle": "teammate-idle.mp3",
    "task_completed": "team-task-done.mp3",
    "stop_failure": "stop-failure.mp3",
    "postcompact": "post-compact.mp3",
    "config_change": "config-change.mp3",
    "instructions_loaded": "instructions-loaded.mp3",
    "elicitation": "elicitation.mp3",
    "elicitation_result": "elicitation-result.mp3",
    # v5.0 hooks (dedicated audio shipped in v5.0.1, generated via ElevenLabs)
    "permission_denied": "permission-denied.mp3",
    "cwd_changed": "cwd-changed.mp3",
    "file_changed": "file-changed.mp3",
    # v6.5.0. Both reuse a semantic sibling's sound rather than ship a
    # dedicated one: generating audio needs an ElevenLabs key, and a sound
    # that exists beats a catalogue entry pointing at a missing file.
    "directory_added": "directory-added.mp3",
    "worktree_remove": "worktree-removed.mp3",
    "task_created": "task-created.mp3",
    # v6.2 hooks — new editor lifecycle events.
    # Claude Code (Setup / UserPromptExpansion / PostToolBatch / MessageDisplay):
    "setup": "setup-ready.mp3",
    "user_prompt_expansion": "prompt-expanded.mp3",
    "post_tool_batch": "batch-complete.mp3",
    "message_display": "message-display.mp3",
    # Cursor granular per-tool-type events (shell / MCP / file-read split apart):
    "shell_before": "shell-starting.mp3",
    "shell_after": "shell-done.mp3",
    "mcp_before": "mcp-starting.mp3",
    "mcp_after": "mcp-done.mp3",
    "file_read": "file-read.mp3",
    "agent_response": "agent-response.mp3",
    "agent_thinking": "thinking-done.mp3",
    "workspace_open": "workspace-open.mp3",
    "tab_file_edit": "tab-edit.mp3",
}

# =============================================================================
# CONFIGURATION FUNCTIONS
# =============================================================================

_config_cache: Optional[Dict[str, Any]] = None


def load_config() -> Dict[str, Any]:
    """Load user_preferences.json (cached per invocation).

    Auto-init from template, auto-migrate, and CLAUDE_PLUGIN_OPTION_* overlay
    are all handled by :meth:`UserPreferences.load`.
    """
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    try:
        _config_cache = _prefs().load()
        log_debug(f"Loaded config from {_prefs().config_path}")
    except Exception as e:
        log_error(f"Could not load config: {e}")
        _config_cache = {}
    return _config_cache


def is_hook_enabled(hook_type: str, variant: Optional[str] = None) -> bool:
    """Check if a hook is enabled, honouring per-variant overrides (v6.4).

    ``variant`` is a synthetic event name such as ``notification_idle_prompt``;
    ``hook_type`` is its canonical parent (``notification``). Before v6.4 only
    the parent was consulted, so a user could not say "chime on permission
    prompts but not idle ones". Variant keys are ordinary flat booleans in
    ``enabled_hooks``, so the JSON schema and the migration deep-merge need no
    changes to accommodate them.

    Precedence, highest first:

      1. An explicit ``enabled_hooks[variant]`` — the user spoke about this
         exact event, so nothing outranks it.
      2. An explicit ``enabled_hooks[parent] is False`` — a disabled parent is a
         hard kill switch, because ``hooks disable notification`` has to
         actually produce silence. To keep one variant of a muted parent, set
         the variant key explicitly (rule 1) instead of relying on the parent.
      3. ``SYNTHETIC_VARIANT_DEFAULTS[variant]`` — lets a variant ship opt-in
         under an on-by-default parent.
      4. An explicit ``enabled_hooks[parent] is True``.
      5. The built-in default set.
    """
    config = load_config()

    # Default enabled hooks (v5.0 adds permission_denied + task_created)
    default_enabled = {"notification", "stop", "permission_request"}

    enabled_hooks = config.get("enabled_hooks", {})

    # 1. Explicit per-variant override wins outright.
    if variant and variant in enabled_hooks:
        result = enabled_hooks[variant] is True
        log_debug(f"Hook {hook_type} variant {variant} explicitly set to {result}")
        return result

    # 2. A parent switched off silences every variant under it.
    if enabled_hooks.get(hook_type) is False:
        log_debug(f"Hook {hook_type} explicitly disabled (variant={variant})")
        return False

    # 3. Variant-specific default, for opt-in variants of an on-by-default parent.
    if variant and variant in SYNTHETIC_VARIANT_DEFAULTS:
        result = SYNTHETIC_VARIANT_DEFAULTS[variant]
        log_debug(f"Hook {hook_type} variant {variant} using variant default: {result}")
        return result

    # 4. Explicit parent value (the True case; False was handled by rule 2).
    if hook_type in enabled_hooks:
        result = enabled_hooks[hook_type] is True
        log_debug(f"Hook {hook_type} explicitly set to {result}")
        return result

    # 5. Built-in default.
    result = hook_type in default_enabled
    log_debug(f"Hook {hook_type} using default: {result}")
    return result


def is_snoozed() -> bool:
    """Check if hooks are temporarily snoozed via marker file."""
    ensure_queue_dir()
    snooze_file = _prefs().queue_dir / "snooze_until"
    if not snooze_file.exists():
        return False
    try:
        snooze_until = float(snooze_file.read_text(encoding="utf-8").strip())
        if time.time() < snooze_until:
            remaining = snooze_until - time.time()
            log_debug(f"Snoozed: {remaining:.0f}s remaining")
            return True
        else:
            log_debug("Snooze expired")
            return False
    except (ValueError, OSError) as e:
        log_debug(f"Error reading snooze file: {e}")
        return False


# =============================================================================
# SYNTHETIC EVENT VARIANTS (v5.0 — native matcher routing)
# =============================================================================
#
# Claude Code's matcher engine fires hooks per source/notification_type/error
# subtype. Plugin hooks/hooks.json registers a separate handler per matcher
# value, each invoking hook_runner.py with a synthetic event name like
# "session_start_resume" or "stop_failure_rate_limit". The runner resolves
# the synthetic name to the canonical hook plus an audio file override and
# logs the variant. Legacy installs that still register one wildcard handler
# per event keep working unchanged because the canonical hook names are also
# accepted directly.

SYNTHETIC_EVENT_MAP: Dict[str, Tuple[str, Optional[str]]] = {
    # session_start subtypes (matcher: source)
    "session_start_startup": ("session_start", "session-startup.mp3"),
    "session_start_resume":  ("session_start", "session-resumed.mp3"),
    "session_start_clear":   ("session_start", "session-cleared.mp3"),
    "session_start_compact": ("session_start", "session-after-compact.mp3"),
    # v6.4.1 — Claude Code 2.1.213 reports source "fork" (not "resume") when a
    # session begins as a fork. Without this matcher forked sessions were silent.
    "session_start_fork":    ("session_start", "session-forked.mp3"),

    # session_end subtypes (matcher: source)
    "session_end_clear":             ("session_end", "end-clear.mp3"),
    "session_end_resume":            ("session_end", "end-resume.mp3"),
    "session_end_logout":            ("session_end", "end-logout.mp3"),
    "session_end_prompt_input_exit": ("session_end", "end-exit.mp3"),

    # stop_failure subtypes (matcher: error_type). v6.4.1: the full 11-value
    # upstream set, one handler per value. Before 6.4.1 six of these collapsed
    # onto a single "stop_failure_other" handler, so their per-variant toggles
    # silently did nothing -- and "other" was never a real Claude Code value.
    # Audio follows Claude Code's own bucketing: auth / billing /
    # model_unavailable / transient.
    "stop_failure_rate_limit":            ("stop_failure", "fail-rate-limit.mp3"),
    "stop_failure_authentication_failed": ("stop_failure", "fail-auth.mp3"),
    "stop_failure_oauth_org_not_allowed": ("stop_failure", "fail-oauth-org.mp3"),
    "stop_failure_account_on_hold":       ("stop_failure", "fail-account-hold.mp3"),
    "stop_failure_billing_error":         ("stop_failure", "fail-billing.mp3"),
    "stop_failure_model_not_found":       ("stop_failure", "fail-model-not-found.mp3"),
    "stop_failure_invalid_request":       ("stop_failure", "fail-invalid-request.mp3"),
    "stop_failure_server_error":          ("stop_failure", "fail-server-error.mp3"),
    "stop_failure_overloaded":            ("stop_failure", "fail-overloaded.mp3"),
    "stop_failure_max_output_tokens":     ("stop_failure", "fail-max-tokens.mp3"),
    "stop_failure_unknown":               ("stop_failure", "fail-unknown.mp3"),
    # v6.7 -- two more error_type values Claude Code sends. cloud_credential_error
    # is documented (v2.1.267+); verification_required is in the 2.1.288 binary's
    # error union but not the docs table. Neither had a registered matcher, so
    # both were silent. StopFailure is off by default, so -- like its eleven
    # siblings -- they need no SYNTHETIC_VARIANT_DEFAULTS entry.
    "stop_failure_cloud_credential_error": ("stop_failure", "fail-cloud-credential.mp3"),
    "stop_failure_verification_required":  ("stop_failure", "fail-verification.mp3"),

    # notification subtypes (matcher: notification_type)
    "notification_permission_prompt":  ("notification", "notif-permission-prompt.mp3"),
    "notification_idle_prompt":        ("notification", "notif-idle-prompt.mp3"),
    "notification_auth_success":       ("notification", "notif-auth-success.mp3"),
    "notification_elicitation_dialog": ("notification", "notif-elicitation-dialog.mp3"),
    # v6.4 — the remaining four notification_type matchers Claude Code
    # documents. agent_needs_input / agent_completed require Claude Code
    # v2.1.198+; see SYNTHETIC_VARIANT_DEFAULTS for why all four ship off.
    "notification_agent_needs_input":    ("notification", "notif-agent-needs-input.mp3"),
    "notification_agent_completed":      ("notification", "notif-agent-completed.mp3"),
    "notification_elicitation_complete": ("notification", "notif-elicitation-complete.mp3"),
    "notification_elicitation_response": ("notification", "notif-elicitation-response.mp3"),
    # v6.5 — the remaining six typed notification_type values. Until now these
    # fell through to the catch-all and shared one sound with no per-variant
    # toggle. worker_permission_prompt and the quota_auto_resume_* trio are the
    # away-from-desk ones: a subagent blocked on approval, and a session that
    # resumed itself after a rate-limit window reset.
    "notification_elicitation_url_dialog":    ("notification", "notif-elicitation-url.mp3"),
    "notification_worker_permission_prompt":  ("notification", "notif-worker-permission.mp3"),
    "notification_push_notification":         ("notification", "notif-push.mp3"),
    "notification_computer_use_enter":        ("notification", "notif-computer-use-enter.mp3"),
    "notification_computer_use_exit":         ("notification", "notif-computer-use-exit.mp3"),
    "notification_quota_auto_resume_fired":   ("notification", "notif-quota-resumed.mp3"),
    "notification_quota_auto_resume_stale":   ("notification", "notif-quota-stale.mp3"),
    "notification_quota_auto_resume_disabled": ("notification", "notif-quota-disabled.mp3"),
    # v6.7 -- emitted when Claude Code cannot save its login credentials
    # ("Claude Code login needs attention: credentials could not be saved").
    # Seen in the 2.1.288 binary, not in the documented notification_type list.
    "notification_auth_storage_failure":      ("notification", "notif-auth-storage.mp3"),

    # precompact / postcompact subtypes (matcher: trigger)
    "precompact_manual": ("precompact", "precompact-manual.mp3"),
    "precompact_auto":   ("precompact", "precompact-auto.mp3"),
    "postcompact_manual": ("postcompact", "postcompact-manual.mp3"),
    "postcompact_auto":   ("postcompact", "postcompact-auto.mp3"),

    # v6.5 — DirectoryAdded subtypes (matcher: source). Payload carries
    # `directory` (absolute path) and source slash_command|register_repo_root.
    "directory_added_slash_command":     ("directory_added", "dir-added-slash.mp3"),
    "directory_added_register_repo_root": ("directory_added", "dir-added-repo-root.mp3"),

    # v6.2 — Setup subtypes (matcher: trigger init|maintenance)
    "setup_init":        ("setup", "setup-init.mp3"),
    "setup_maintenance": ("setup", "setup-maintenance.mp3"),
}


# Wording for each Notification subtype, keyed by the ``notification_type``
# field Claude Code puts on stdin. Data rather than an if/elif chain so
# tests/test_plugin_hooks_contract.py can assert every registered subtype has
# copy of its own — the old catch-all worded anything unrecognised as
# "Authorization needed", which was wrong and silent about being wrong.
NOTIFICATION_TYPE_LABELS: Dict[str, str] = {
    "permission_prompt":    "Authorization needed",
    "idle_prompt":          "Idle prompt",
    "auth_success":         "Authentication succeeded",
    "elicitation_dialog":   "Elicitation dialog",
    # v6.4 additions.
    "elicitation_complete": "Elicitation complete",
    "elicitation_response": "Elicitation answered",
    "agent_needs_input":    "Background agent needs input",
    "agent_completed":      "Background agent finished",
    # v6.5 additions.
    "elicitation_url_dialog":     "Elicitation link opened",
    "worker_permission_prompt":   "Subagent needs authorization",
    "push_notification":          "Push notification",
    "computer_use_enter":         "Computer use started",
    "computer_use_exit":          "Computer use finished",
    "quota_auto_resume_fired":    "Session auto-resumed after quota reset",
    "quota_auto_resume_stale":    "Auto-resume expired",
    "quota_auto_resume_disabled": "Auto-resume disabled",
    # v6.7 addition.
    "auth_storage_failure":       "Login needs attention",
}


# v6.4: per-variant default states. Only variants whose default differs from
# their parent hook's belong here — everything absent inherits the parent, which
# is what keeps the ~24 pre-6.4 variants behaving byte-identically.
#
# Newly registered variants of an on-by-default parent (``notification`` is on)
# must be listed as False, otherwise adding a matcher would start making noise
# on every existing install. New events ship opt-in; new variants do too.
SYNTHETIC_VARIANT_DEFAULTS: Dict[str, bool] = {
    # The four notification subtypes added in v6.4. Their parent
    # (``notification``) is on by default, so without an entry here they would
    # start firing on every existing install the moment 6.4 lands.
    #
    # agent_needs_input / agent_completed additionally could not be observed
    # firing at all during a ~1h capture on Claude Code 2.1.215 that did record
    # 5 SubagentStop, 10 Stop and 5 idle_prompt events — so they do not fire for
    # local Task-tool subagents, and appear to belong to the push-notification
    # path for background agents. Registering them is a completeness and
    # forward-compatibility move, not a feature we can demonstrate.
    "notification_agent_needs_input": False,
    "notification_agent_completed": False,
    "notification_elicitation_complete": False,
    "notification_elicitation_response": False,
    # v6.5: same rule, same reason -- `notification` is on by default, so each
    # of these would start firing on every existing install the moment the
    # matcher is registered.
    "notification_elicitation_url_dialog": False,
    "notification_worker_permission_prompt": False,
    "notification_push_notification": False,
    "notification_computer_use_enter": False,
    "notification_computer_use_exit": False,
    "notification_quota_auto_resume_fired": False,
    "notification_quota_auto_resume_stale": False,
    "notification_quota_auto_resume_disabled": False,
    # v6.7: same rule -- `notification` is on by default.
    "notification_auth_storage_failure": False,
}


def _resolve_synthetic_event(raw_arg: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Map a synthetic event name to (canonical_hook, audio_override, variant_label)."""
    entry = SYNTHETIC_EVENT_MAP.get(raw_arg)
    if entry is None:
        return raw_arg, None, None
    canonical, audio = entry
    return canonical, audio, raw_arg


# Module-level state set by main() before run_hook() is called.
_current_audio_override: Optional[str] = None
_current_synthetic_variant: Optional[str] = None


CUSTOM_AUDIO_FILES = {
    "notification": "chime-notification-urgent.mp3",
    "stop": "chime-task-complete.mp3",
    "pretooluse": "chime-task-starting.mp3",
    "posttooluse": "chime-task-progress.mp3",
    "userpromptsubmit": "chime-prompt-received.mp3",
    "subagent_stop": "chime-subagent-complete.mp3",
    "precompact": "chime-pre-compact.mp3",
    "session_start": "chime-session-start.mp3",
    "session_end": "chime-session-end.mp3",
    "permission_request": "chime-permission-request.mp3",
    "posttoolusefailure": "chime-tool-failed.mp3",
    "subagent_start": "chime-subagent-start.mp3",
    "teammate_idle": "chime-teammate-idle.mp3",
    "task_completed": "chime-team-task-done.mp3",
    "stop_failure": "chime-stop-failure.mp3",
    "postcompact": "chime-post-compact.mp3",
    "config_change": "chime-config-change.mp3",
    "instructions_loaded": "chime-instructions-loaded.mp3",
    "elicitation": "chime-elicitation.mp3",
    "elicitation_result": "chime-elicitation-result.mp3",
    # v5.0 hooks (dedicated chimes shipped in v5.0.1, generated via ElevenLabs)
    "permission_denied": "chime-permission-denied.mp3",
    "cwd_changed": "chime-cwd-changed.mp3",
    "directory_added": "chime-directory-added.mp3",
    "worktree_remove": "chime-worktree-removed.mp3",
    "file_changed": "chime-file-changed.mp3",
    "task_created": "chime-task-created.mp3",
    # v6.2 hooks — new editor lifecycle events (chime variants).
    "setup": "chime-setup-ready.mp3",
    "user_prompt_expansion": "chime-prompt-expanded.mp3",
    "post_tool_batch": "chime-batch-complete.mp3",
    "message_display": "chime-message-display.mp3",
    "shell_before": "chime-shell-starting.mp3",
    "shell_after": "chime-shell-done.mp3",
    "mcp_before": "chime-mcp-starting.mp3",
    "mcp_after": "chime-mcp-done.mp3",
    "file_read": "chime-file-read.mp3",
    "agent_response": "chime-agent-response.mp3",
    "agent_thinking": "chime-thinking-done.mp3",
    "workspace_open": "chime-workspace-open.mp3",
    "tab_file_edit": "chime-tab-edit.mp3",
}


def get_audio_file(hook_type: str) -> Optional[Path]:
    """Get the audio file path for a hook type.

    Resolution order:
    0. Synthetic-variant audio override (v5.0 native matchers)
    1. Per-hook override in audio_files config (only if user customized it)
    2. audio_theme setting ("default" or "custom")
    3. Fallback to audio/default/
    """
    config = load_config()
    theme = config.get("audio_theme", "default")

    # 0. v5.0 synthetic variant override (native matcher routing)
    if _current_audio_override:
        override_name = _current_audio_override
        if theme == "custom":
            candidates = [
                AUDIO_DIR / "custom" / ("chime-" + override_name),
                AUDIO_DIR / "default" / override_name,
            ]
        else:
            candidates = [
                AUDIO_DIR / "default" / override_name,
                AUDIO_DIR / "custom" / ("chime-" + override_name),
            ]
        for cand in candidates:
            if cand.exists():
                log_event("debug", "audio_override_resolved",
                          variant=_current_synthetic_variant,
                          override=override_name,
                          path=str(cand))
                return cand

    default_file = DEFAULT_AUDIO_FILES.get(hook_type, "notification-info.mp3")

    # 1. Check per-hook override — only if it differs from the default mapping
    #    Paths like "default/<filename>" match the default template and should
    #    not override the audio_theme setting.
    audio_files = config.get("audio_files", {})
    default_pattern = f"default/{default_file}"
    if hook_type in audio_files and audio_files[hook_type] != default_pattern:
        override_path = AUDIO_DIR / audio_files[hook_type]
        if override_path.exists():
            log_debug(f"Audio file for {hook_type} (override): {override_path}")
            return override_path

    # 2. Use audio_theme setting
    if theme == "custom":
        custom_file = CUSTOM_AUDIO_FILES.get(hook_type, default_file)
        theme_path = AUDIO_DIR / "custom" / custom_file
    else:
        theme_path = AUDIO_DIR / "default" / default_file

    if theme_path.exists():
        log_debug(f"Audio file for {hook_type} (theme={theme}): {theme_path}")
        return theme_path

    # 3. Fallback to default
    fallback_path = AUDIO_DIR / "default" / default_file
    if fallback_path.exists():
        log_debug(f"Using fallback audio for {hook_type}: {fallback_path}")
        return fallback_path

    log_debug(f"No audio file found for {hook_type}")
    return None


def get_debounce_ms() -> int:
    """Get debounce time in milliseconds."""
    config = load_config()
    playback_settings = config.get("playback_settings", {})
    return playback_settings.get("debounce_ms", 500)

# =============================================================================
# DEBOUNCE SYSTEM
# =============================================================================

def should_debounce(hook_type: str) -> bool:
    """Check if we should skip this notification due to debounce."""
    ensure_queue_dir()
    debounce_file = _prefs().queue_dir / f"{hook_type}_last_played"
    debounce_sec = get_debounce_ms() / 1000.0

    current_time = time.time()

    if debounce_file.exists():
        try:
            last_time = float(debounce_file.read_text(encoding="utf-8").strip())
            if current_time - last_time < debounce_sec:
                log_debug(f"Debouncing {hook_type}: {current_time - last_time:.2f}s < {debounce_sec}s")
                return True
        except (ValueError, OSError) as e:
            log_debug(f"Error reading debounce file: {e}")

    # Update debounce timestamp
    try:
        debounce_file.write_text(str(current_time), encoding="utf-8")
    except OSError as e:
        log_error(f"Failed to write debounce file: {e}")

    return False


# v6.6: Claude Code's own "is this task in flight" predicate counts "pending"
# as well as "running", and the Stop payload builder only ever passes tasks
# that satisfy it -- so every entry arrives as one or the other. Counting
# "running" alone under-counted a task that was queued but not yet started.
_IN_FLIGHT_TASK_STATUSES = frozenset({"running", "pending"})

# ``type`` in a background_tasks entry is Claude Code's friendly label, not the
# internal discriminant. Three labels are maintenance work Claude Code runs for
# itself ("dream", "auto-mode scan", "memory import");
# none is something the user started, so none should hold back a turn-end sound.
_INTERNAL_TASK_TYPES = frozenset({"dream", "auto-mode scan", "memory import"})


def _is_user_background_task(task: Any) -> bool:
    """True for a background_tasks entry that is in flight and user-facing.

    Tolerates any malformed entry (non-dict, missing keys, non-string values)
    by returning False: a set-membership test on a list or dict value raises
    TypeError (unhashable), and a hook that raises is worse than one that plays
    a sound.
    """
    if not isinstance(task, dict):
        return False
    status = task.get("status")
    if not isinstance(status, str) or status not in _IN_FLIGHT_TASK_STATUSES:
        return False
    kind = task.get("type")
    return not (isinstance(kind, str) and kind in _INTERNAL_TASK_TYPES)


def should_filter(hook_type: str, stdin_data: dict, config: Dict[str, Any]) -> bool:
    """Check user-defined filters. Returns True if hook should be skipped.

    Filters are per-hook regex patterns matched against stdin JSON fields.
    A field ending with '_exclude' inverts the match (skip if pattern matches).
    Otherwise, skip if the pattern does NOT match the field value.
    """
    filters = config.get("filters", {}).get(hook_type, {})
    if not filters:
        return False

    # v6.4: reserved non-regex filter. Claude Code's Stop/SubagentStop payload
    # carries a ``background_tasks`` array (documented since the hooks reference
    # added it: it lets a hook "distinguish 'session is done' from 'session is
    # paused waiting for background work to wake it back up'") listing the
    # teammates, subagents and background shells still in flight. Because
    # ``Stop`` fires at the end of every turn, a session running ten teammates
    # chimes constantly; this lets a user hear the turn-end sound only once
    # nothing is still working. Expressing it as a regex over the stringified
    # array would depend on Python's repr of a Claude Code payload, which is far
    # too brittle to ask of a user's config file.
    if filters.get("skip_if_background_tasks_running") is True:
        tasks = stdin_data.get("background_tasks")
        if isinstance(tasks, list):
            running = sum(1 for t in tasks if _is_user_background_task(t))
            if running:
                log_debug(f"Filter: {hook_type} skipped — {running} background task(s) still in flight")
                return True

    # v6.6: separate opt-in for scheduled wakeups. ``session_crons`` lists the
    # session's CronCreate / ScheduleWakeup / /loop entries; a session with a
    # recurring cron always has one, so folding this into the key above would
    # silence every turn of such a session for users who never asked for that.
    if filters.get("skip_if_session_crons_scheduled") is True:
        crons = stdin_data.get("session_crons")
        if isinstance(crons, list) and crons:
            log_debug(f"Filter: {hook_type} skipped — {len(crons)} session cron(s) scheduled")
            return True

    # v6.5: another reserved non-regex filter. PostToolUse and
    # PostToolUseFailure carry ``duration_ms`` ("Tool execution time in
    # milliseconds. Excludes permission-prompt and hook time"), which finally
    # makes "only tell me about the slow ones" expressible. Debounce cannot do
    # this -- it suppresses by wall-clock window, so it silences a burst of fast
    # tools and a genuinely long build alike. A numeric threshold is also not
    # something a regex over a stringified integer could express safely.
    min_duration = filters.get("min_duration_ms")
    if min_duration is not None:
        try:
            threshold = float(min_duration)
        except (TypeError, ValueError):
            threshold = 0.0
        if threshold > 0:
            raw = stdin_data.get("duration_ms")
            try:
                actual = float(raw)
            except (TypeError, ValueError):
                # Absent on older Claude Code, and on Cursor/Codex. Treat an
                # unknown duration as "do not suppress" so the filter can never
                # silence an editor that simply does not report it.
                actual = None
            if actual is not None and actual < threshold:
                log_debug(
                    f"Filter: {hook_type} skipped — took {actual:.0f}ms, "
                    f"under the {threshold:.0f}ms threshold"
                )
                return True

    for field, pattern in filters.items():
        if not isinstance(pattern, str) or not pattern:
            continue
        if field.startswith("_"):
            continue  # skip comment keys
        if field in ("skip_if_background_tasks_running", "skip_if_session_crons_scheduled", "min_duration_ms"):
            continue  # reserved non-regex filters, handled above

        try:
            if field.endswith("_exclude"):
                real_field = field[:-8]
                value = str(stdin_data.get(real_field, ""))
                if value and re.search(pattern, value):
                    log_debug(f"Filter: {hook_type} excluded by {real_field} matching '{pattern}'")
                    return True
            else:
                value = str(stdin_data.get(field, ""))
                if value and not re.search(pattern, value):
                    log_debug(f"Filter: {hook_type} skipped — {field}='{value}' doesn't match '{pattern}'")
                    return True
        except re.error as e:
            log_debug(f"Filter regex error for {field}: {e}")

    return False


# =============================================================================
# AUDIO PLAYBACK FUNCTIONS
# =============================================================================

def play_audio_windows(audio_file: Path) -> bool:
    """Play audio on Windows using multiple fallback methods."""
    # Escape path for PowerShell
    win_path = str(audio_file).replace("\\", "/")
    win_path_escaped = escape_powershell_string(win_path)

    log_debug(f"Windows audio playback: {win_path}")

    # Method 1: Direct PowerShell command with MediaPlayer.
    # MediaPlayer.Open() is async: we poll NaturalDuration.HasTimeSpan with a
    # short ceiling, then sleep for the real clip length + a tail buffer so
    # Stop()/Close() don't truncate playback (issue #14). Fallback to a
    # generous-but-bounded sleep if Open() never resolves.
    try:
        ps_cmd = (
            'Add-Type -AssemblyName presentationCore; '
            '$p = New-Object System.Windows.Media.MediaPlayer; '
            f'$p.Open("{win_path_escaped}"); '
            '$deadline = (Get-Date).AddMilliseconds(1500); '
            'while (-not $p.NaturalDuration.HasTimeSpan -and (Get-Date) -lt $deadline) '
            '{ Start-Sleep -Milliseconds 50 }; '
            '$p.Play(); '
            'if ($p.NaturalDuration.HasTimeSpan) '
            '{ Start-Sleep -Milliseconds ([int]($p.NaturalDuration.TimeSpan.TotalMilliseconds + 500)) } '
            'else { Start-Sleep -Seconds 10 }; '
            '$p.Stop(); $p.Close()'
        )
        proc = subprocess.Popen(
            ["powershell.exe", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", ps_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        log_debug(f"Started PowerShell MediaPlayer (PID: {proc.pid})")
        return True
    except FileNotFoundError:
        log_debug("PowerShell not found, trying fallback")
    except Exception as e:
        log_error(f"PowerShell MediaPlayer failed: {e}")

    # Method 2: Use PowerShell script file
    try:
        temp_dir = get_safe_temp_dir()
        script_file = temp_dir / f"claude_audio_{os.getpid()}_{int(time.time())}.ps1"

        ps_script = f'''
Add-Type -AssemblyName presentationCore
$player = New-Object System.Windows.Media.MediaPlayer
$player.Open("{win_path_escaped}")
$deadline = (Get-Date).AddMilliseconds(1500)
while (-not $player.NaturalDuration.HasTimeSpan -and (Get-Date) -lt $deadline) {{
    Start-Sleep -Milliseconds 50
}}
$player.Play()
if ($player.NaturalDuration.HasTimeSpan) {{
    Start-Sleep -Milliseconds ([int]($player.NaturalDuration.TimeSpan.TotalMilliseconds + 500))
}} else {{
    Start-Sleep -Seconds 10
}}
$player.Stop()
$player.Close()
Remove-Item -Path $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
'''
        script_file.write_text(ps_script, encoding="utf-8")
        log_debug(f"Created PowerShell script: {script_file}")

        proc = subprocess.Popen(
            ["powershell.exe", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(script_file)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        log_debug(f"Started PowerShell script (PID: {proc.pid})")
        return True
    except Exception as e:
        log_error(f"PowerShell script method failed: {e}")

    # Method 3: Use WMPlayer.OCX COM object
    try:
        ps_cmd = (
            f'$w = New-Object -ComObject WMPlayer.OCX; $w.URL = "{win_path_escaped}"; '
            '$deadline = (Get-Date).AddMilliseconds(1500); '
            'while (($w.currentMedia -eq $null -or $w.currentMedia.duration -eq 0) '
            '-and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 50 }; '
            'if ($w.currentMedia -ne $null -and $w.currentMedia.duration -gt 0) '
            '{ Start-Sleep -Milliseconds ([int]($w.currentMedia.duration * 1000 + 500)) } '
            'else { Start-Sleep -Seconds 10 }'
        )
        proc = subprocess.Popen(
            ["powershell.exe", "-Command", ps_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        log_debug(f"Started WMPlayer.OCX (PID: {proc.pid})")
        return True
    except Exception as e:
        log_error(f"WMPlayer.OCX method failed: {e}")
        return False


def play_audio_macos(audio_file: Path) -> bool:
    """Play audio on macOS using afplay."""
    log_debug(f"macOS audio playback: {audio_file}")
    try:
        proc = subprocess.Popen(
            ["afplay", str(audio_file)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        log_debug(f"Started afplay (PID: {proc.pid})")
        return True
    except FileNotFoundError:
        log_error("afplay not found")
        return False
    except Exception as e:
        log_error(f"afplay failed: {e}")
        return False


def play_audio_linux(audio_file: Path) -> bool:
    """Play audio on Linux using available players."""
    log_debug(f"Linux audio playback: {audio_file}")

    players = [
        (["mpg123", "-q"], "mpg123"),
        (["ffplay", "-nodisp", "-autoexit", "-hide_banner", "-loglevel", "quiet"], "ffplay"),
        (["paplay"], "paplay"),
        (["aplay"], "aplay"),
    ]

    for player_cmd, player_name in players:
        try:
            cmd = player_cmd + [str(audio_file)]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            log_debug(f"Started {player_name} (PID: {proc.pid})")
            return True
        except FileNotFoundError:
            log_debug(f"{player_name} not found, trying next")
            continue
        except Exception as e:
            log_debug(f"{player_name} failed: {e}")
            continue

    log_error("No audio player found on Linux")
    return False


def play_audio_wsl(audio_file: Path) -> bool:
    """Play audio in WSL by copying to Windows temp and using PowerShell."""
    log_debug(f"WSL audio playback: {audio_file}")

    try:
        import shutil

        # Get Windows temp directory
        # Try multiple methods to find a writable Windows temp
        win_temp_candidates = []

        # Method 1: Use WSLENV or inherited Windows env vars
        for env_var in ["TEMP", "TMP", "USERPROFILE"]:
            val = os.environ.get(env_var)
            if val and val.startswith("/mnt/"):
                win_temp_candidates.append(Path(val))

        # Method 2: Use wslvar to get Windows TEMP
        try:
            win_temp_path = subprocess.check_output(
                ["wslvar", "TEMP"],
                text=True,
                stderr=subprocess.DEVNULL
            ).strip()
            if win_temp_path:
                # Convert Windows path to WSL path
                wsl_path = subprocess.check_output(
                    ["wslpath", "-u", win_temp_path],
                    text=True,
                    stderr=subprocess.DEVNULL
                ).strip()
                win_temp_candidates.append(Path(wsl_path))
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        # Method 3: Standard Windows temp locations via /mnt
        windir = os.environ.get("WINDIR", "")
        if windir and windir.startswith("/mnt/"):
            win_temp_candidates.append(Path(windir) / "Temp")

        win_temp_candidates.extend([
            Path("/mnt/c/Windows/Temp"),
            Path("/mnt/c/Users") / os.environ.get("USER", "Public") / "AppData/Local/Temp",
        ])

        # Find first writable temp directory
        win_temp = None
        for candidate in win_temp_candidates:
            try:
                if candidate.exists() and os.access(str(candidate), os.W_OK):
                    win_temp = candidate
                    break
            except Exception:
                continue

        if not win_temp:
            log_error("Could not find writable Windows temp directory from WSL")
            # Fallback to native Linux playback
            return play_audio_linux(audio_file)

        log_debug(f"Using Windows temp: {win_temp}")

        # Copy audio file to Windows temp
        temp_filename = f"claude_audio_{int(time.time())}_{os.getpid()}.mp3"
        wsl_temp_file = win_temp / temp_filename
        shutil.copy(str(audio_file), str(wsl_temp_file))
        log_debug(f"Copied audio to: {wsl_temp_file}")

        # Convert to Windows path for PowerShell
        try:
            win_path = subprocess.check_output(
                ["wslpath", "-w", str(wsl_temp_file)],
                text=True,
                stderr=subprocess.DEVNULL
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Manual conversion
            path_str = str(wsl_temp_file)
            if path_str.startswith("/mnt/"):
                drive = path_str[5].upper()
                win_path = f"{drive}:{path_str[6:]}".replace("/", "\\")
            else:
                log_error("Could not convert WSL path to Windows path")
                return play_audio_linux(audio_file)

        log_debug(f"Windows path: {win_path}")
        win_path_escaped = escape_powershell_string(win_path.replace("\\", "/"))

        # Play using PowerShell
        ps_command = f'''
Add-Type -AssemblyName presentationCore
$player = New-Object System.Windows.Media.MediaPlayer
$player.Open("{win_path_escaped}")
$deadline = (Get-Date).AddMilliseconds(1500)
while (-not $player.NaturalDuration.HasTimeSpan -and (Get-Date) -lt $deadline) {{
    Start-Sleep -Milliseconds 50
}}
$player.Play()
if ($player.NaturalDuration.HasTimeSpan) {{
    Start-Sleep -Milliseconds ([int]($player.NaturalDuration.TimeSpan.TotalMilliseconds + 500))
}} else {{
    Start-Sleep -Seconds 10
}}
$player.Stop()
$player.Close()
Remove-Item -Path "{win_path_escaped}" -ErrorAction SilentlyContinue
'''

        proc = subprocess.Popen(
            ["powershell.exe", "-Command", ps_command],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        log_debug(f"Started WSL PowerShell playback (PID: {proc.pid})")
        return True

    except Exception as e:
        log_error(f"WSL audio playback failed: {e}")
        # Fallback to native Linux playback
        log_debug("Falling back to native Linux playback")
        return play_audio_linux(audio_file)


def is_wsl() -> bool:
    """Check if running in WSL."""
    try:
        with open("/proc/version", "r") as f:
            content = f.read().lower()
            return "microsoft" in content or "wsl" in content
    except (FileNotFoundError, PermissionError):
        return False


def play_audio(audio_file: Path) -> bool:
    """Play audio file using platform-specific method."""
    system = platform.system()
    log_debug(f"Platform: {system}")

    if system == "Windows":
        return play_audio_windows(audio_file)
    elif system == "Darwin":
        return play_audio_macos(audio_file)
    elif system == "Linux":
        if is_wsl():
            log_debug("Detected WSL environment")
            return play_audio_wsl(audio_file)
        return play_audio_linux(audio_file)
    else:
        log_error(f"Unsupported platform: {system}")
        return False

# =============================================================================
# STDIN PARSING
# =============================================================================

def parse_stdin() -> dict:
    """Parse JSON data from Claude Code via stdin."""
    try:
        raw = sys.stdin.read()
        if raw.strip():
            data = json.loads(raw)
            log_debug(f"Parsed stdin JSON: {list(data.keys()) if isinstance(data, dict) else type(data)}")
            return data if isinstance(data, dict) else {}
    except json.JSONDecodeError as e:
        log_debug(f"stdin was not valid JSON: {e}")
    except Exception as e:
        log_debug(f"Failed to read stdin: {e}")
    return {}

# =============================================================================
# CONTEXT EXTRACTION
# =============================================================================

def _truncate(s: str, max_len: int = 60) -> str:
    """Truncate a string with ellipsis if too long."""
    return (s[:max_len - 3] + "...") if len(s) > max_len else s


# Secret/token patterns redacted before any text is spoken aloud or shown in a
# desktop toast. Order matters: structured tokens first, then key=value pairs,
# then long opaque blobs (last, so it doesn't swallow ordinary prose).
_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),                       # OpenAI-style
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),    # GitHub tokens
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                          # AWS access key id
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),  # JWT
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),    # bearer header
    re.compile(r"(?i)\b(password|passwd|token|api[_-]?key|secret|access[_-]?key)\b\s*[=:]\s*\S+"),
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),                          # long hex blob
]


def _redact_secrets(s: str) -> str:
    """Replace anything that looks like a credential with [redacted]."""
    for pat in _SECRET_PATTERNS:
        s = pat.sub("[redacted]", s)
    return s


def _clean_for_output(text: str, max_len: int, *, for_speech: bool = False) -> str:
    """Turn raw model/tool text into a safe, readable one-liner.

    Used by both TTS reply-reading and verbose desktop toasts so neither speaks
    nor displays code blocks, markdown syntax, or secrets. Deterministic and
    offline (no LLM/network) — a hook must stay instant.

    Steps: strip fenced/inline code, strip markdown, redact secrets, collapse
    whitespace, then truncate on a sentence/word boundary (not mid-word).
    """
    if not text:
        return ""
    s = str(text)

    # 1. Fenced code blocks -> a short marker (do NOT read code aloud / leak it).
    code_marker = "[code omitted]" if for_speech else "[code]"
    s = re.sub(r"```.*?```", f" {code_marker} ", s, flags=re.DOTALL)
    s = re.sub(r"~~~.*?~~~", f" {code_marker} ", s, flags=re.DOTALL)
    # Inline code -> keep the inner text, drop the backticks.
    s = re.sub(r"`([^`]*)`", r"\1", s)

    # 2. Redact credentials BEFORE markdown stripping — otherwise the bold/italic
    #    pass would eat the underscores in tokens like ghp_… and break the match.
    s = _redact_secrets(s)

    # 3. Markdown syntax.
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", s)          # [text](url) -> text
    if for_speech:
        s = re.sub(r"https?://\S+", "a link", s)             # bare URLs unspeakable
    s = re.sub(r"^\s{0,3}#{1,6}\s*", "", s, flags=re.MULTILINE)  # headers
    s = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", s, flags=re.MULTILINE)  # list markers
    s = re.sub(r"(\*\*|__|\*|_|~~)", "", s)                   # bold/italic/strike

    # 4. Collapse whitespace.
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return ""

    # 5. Boundary truncation.
    if len(s) <= max_len:
        return s
    window = s[:max_len]
    # Prefer the last sentence end within the window.
    m = list(re.finditer(r"[.!?](?:\s|$)", window))
    if m and m[-1].end() >= max_len * 0.5:
        return window[:m[-1].end()].strip()
    # Else cut at the last word boundary.
    cut = window.rsplit(" ", 1)[0].strip() if " " in window else window.strip()
    return cut + "…"


def _get_tool_detail(stdin_data: dict, max_len: int = 60) -> str:
    """Extract a brief detail string from tool_input (command, file_path, etc.)."""
    tool_input = stdin_data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return ""
    # Try common fields in priority order
    for key in ("command", "file_path", "pattern", "query", "url", "prompt"):
        val = tool_input.get(key, "")
        if val:
            # Path-like fields collapse to a basename; everything else is
            # sanitized (secrets redacted, code/markdown stripped) so a verbose
            # toast never dumps a raw command or credential.
            if key == "file_path":
                return Path(str(val)).name
            return _clean_for_output(str(val), max_len)
    return ""


def _format_context_suffix(stdin_data: dict, detail_level: str) -> str:
    """Build a session/worktree/agent suffix appended to every notification.

    Returns ' [session: foo, worktree: bar]' style string when detail_level
    allows. Empty string for 'minimal'.
    """
    if detail_level == "minimal":
        return ""
    parts: List[str] = []
    sn = stdin_data.get("session_name")
    if sn:
        parts.append(f"session: {sn}")
    wt = stdin_data.get("worktree")
    if isinstance(wt, dict):
        wt_label = wt.get("name") or wt.get("branch")
        if wt_label:
            parts.append(f"worktree: {wt_label}")
    agent_name = None
    agent_obj = stdin_data.get("agent")
    if isinstance(agent_obj, dict):
        agent_name = agent_obj.get("name")
    if agent_name:
        parts.append(f"agent: {agent_name}")
    return f" [{', '.join(parts)}]" if parts else ""


def get_notification_context(hook_type: str, stdin_data: dict, detail_level: str = "standard") -> str:
    """Generate human-readable context from hook data.

    detail_level: 'minimal' (hook name only), 'standard' (tool + brief context), 'verbose' (full detail)
    """
    max_len = 40 if detail_level == "standard" else 120 if detail_level == "verbose" else 0

    if hook_type == "stop":
        last_msg = stdin_data.get("last_assistant_message", "")
        if last_msg and detail_level != "minimal":
            summary = _clean_for_output(str(last_msg), max(max_len * 2, 80))
            return f"Task completed: {summary}" if summary else "Task completed"
        return "Task completed"
    elif hook_type == "notification":
        msg = stdin_data.get("message", "")
        nt = stdin_data.get("notification_type", "")
        # The notification_type matcher (v5.0) lets us word the alert correctly
        # without re-parsing the free-text message.
        base = NOTIFICATION_TYPE_LABELS.get(nt)
        if base is None:
            # An unknown subtype used to be worded "Authorization needed",
            # which is a confident lie about an event we do not recognise.
            # Degrade to the type itself instead.
            base = nt.replace("_", " ").capitalize() if nt else "Authorization needed"
        return base + (f": {_truncate(msg, 80)}" if msg else "")
    elif hook_type == "pretooluse":
        tool = stdin_data.get("tool_name", "unknown")
        if detail_level == "minimal":
            return f"Running: {tool}"
        detail = _get_tool_detail(stdin_data, max_len)
        return f"Running {tool}" + (f": {detail}" if detail else "")
    elif hook_type == "posttooluse":
        tool = stdin_data.get("tool_name", "unknown")
        if detail_level == "minimal":
            return f"Completed: {tool}"
        detail = _get_tool_detail(stdin_data, max_len)
        return f"Completed {tool}" + (f": {detail}" if detail else "")
    elif hook_type == "subagent_stop":
        agent = stdin_data.get("agent_type", "")
        last_msg = stdin_data.get("last_assistant_message", "")
        base = "Background task finished" + (f" ({agent})" if agent else "")
        if last_msg and detail_level == "verbose":
            summary = _clean_for_output(str(last_msg), max(max_len * 2, 80))
            if summary:
                base += f": {summary}"
        return base
    elif hook_type == "session_start":
        source = stdin_data.get("source", "")
        return "Session started" + (f" ({source})" if source and detail_level != "minimal" else "")
    elif hook_type == "session_end":
        reason = stdin_data.get("reason", "")
        return "Session ended" + (f" ({reason})" if reason and detail_level != "minimal" else "")
    elif hook_type == "precompact":
        trigger = stdin_data.get("trigger", "")
        return "Compacting context" + (f" ({trigger})" if trigger and detail_level != "minimal" else "")
    elif hook_type == "userpromptsubmit":
        return "Prompt received"
    elif hook_type == "permission_request":
        tool = stdin_data.get("tool_name", "unknown")
        if detail_level == "minimal":
            return f"Permission needed: {tool}"
        detail = _get_tool_detail(stdin_data, max_len)
        base = f"Permission needed: {tool}" + (f" — {detail}" if detail else "")
        suggestions = stdin_data.get("permission_suggestions")
        if isinstance(suggestions, list) and suggestions and detail_level == "verbose":
            base += f" ({len(suggestions)} suggestions)"
        return base
    elif hook_type == "posttoolusefailure":
        tool = stdin_data.get("tool_name", "unknown")
        error = stdin_data.get("error", "")
        if detail_level == "minimal":
            return f"Tool failed: {tool}"
        detail = _get_tool_detail(stdin_data, max_len)
        base = f"{tool} failed"
        if detail:
            base += f": {detail}"
        if error:
            base += f" — {_truncate(error, max_len)}"
        return base
    elif hook_type == "subagent_start":
        agent_type = stdin_data.get("agent_type", "")
        return "Subagent starting" + (f": {agent_type}" if agent_type else "")
    elif hook_type == "teammate_idle":
        teammate = stdin_data.get("teammate_name", "unknown")
        team = stdin_data.get("team_name", "")
        return f"Teammate idle: {teammate}" + (f" ({team})" if team else "")
    elif hook_type == "task_completed":
        subject = stdin_data.get("task_subject", "")
        return "Task completed" + (f": {_truncate(subject, 60)}" if subject else "")
    elif hook_type == "stop_failure":
        # error_type is the v5.0 field; fall back to legacy `error` for older payloads.
        error = stdin_data.get("error_type") or stdin_data.get("error", "unknown")
        details = stdin_data.get("error_message") or stdin_data.get("error_details", "")
        return f"API error: {error}" + (f" — {_truncate(details, max_len)}" if details else "")
    elif hook_type == "postcompact":
        trigger = stdin_data.get("trigger", "")
        return "Context compaction complete" + (f" ({trigger})" if trigger else "")
    elif hook_type == "config_change":
        source = stdin_data.get("source", "unknown")
        file_path = stdin_data.get("file_path", "")
        name = Path(file_path).name if file_path and detail_level != "minimal" else ""
        return f"Configuration changed: {source}" + (f" ({name})" if name else "")
    elif hook_type == "instructions_loaded":
        file_path = stdin_data.get("file_path", "")
        reason = stdin_data.get("load_reason", "")
        name = Path(file_path).name if file_path else "unknown"
        return f"Instructions loaded: {name}" + (f" ({reason})" if reason else "")
    elif hook_type == "elicitation":
        server = stdin_data.get("mcp_server_name", "unknown")
        msg = stdin_data.get("message", "")
        return f"Input requested by {server}" + (f": {_truncate(msg, 60)}" if msg else "")
    elif hook_type == "elicitation_result":
        server = stdin_data.get("mcp_server_name", "unknown")
        action = stdin_data.get("action", "")
        return f"Elicitation response: {action}" + (f" ({server})" if server else "")
    # ---- v5.0 hooks ----
    elif hook_type == "permission_denied":
        tool = stdin_data.get("tool_name", "unknown")
        reason = stdin_data.get("reason", "")
        base = f"Permission denied: {tool}"
        if reason and detail_level != "minimal":
            base += f" — {_truncate(str(reason), max_len)}"
        return base
    elif hook_type == "cwd_changed":
        new_cwd = stdin_data.get("new_cwd", "")
        if not new_cwd:
            return "Working directory changed"
        if detail_level == "minimal":
            return "Working directory changed"
        return f"cd {Path(str(new_cwd)).name}"

    elif hook_type == "directory_added":
        # v6.5. Sibling of cwd_changed: /add-dir or the SDK's register_repo_root
        # bringing another root into the session.
        directory = stdin_data.get("directory", "")
        if not directory or detail_level == "minimal":
            return "Directory added"
        return f"Added {Path(str(directory)).name}"

    elif hook_type == "worktree_remove":
        # v6.5. Safe to sound on: unlike WorktreeCreate this is NOT a provider
        # hook — it has no hookSpecificOutput variant and its stdout is
        # discarded. See docs/EVENT_BEHAVIOR_NOTES.md.
        path = stdin_data.get("worktree_path", "")
        if not path or detail_level == "minimal":
            return "Worktree removed"
        return f"Worktree removed: {Path(str(path)).name}"
    elif hook_type == "file_changed":
        fp = stdin_data.get("file_path", "")
        if not fp:
            return "Watched file changed"
        return f"File changed: {Path(str(fp)).name}"
    elif hook_type == "task_created":
        subj = stdin_data.get("task_subject", "")
        teammate = stdin_data.get("teammate_name", "")
        base = "Task created" + (f": {_truncate(str(subj), 60)}" if subj else "")
        if teammate and detail_level != "minimal":
            base += f" → {teammate}"
        return base
    # ---- v6.2 hooks ----
    elif hook_type == "setup":
        # Claude Code Setup hook: trigger is "init" or "maintenance".
        trigger = stdin_data.get("trigger", "")
        return "Environment ready" + (f" ({trigger})" if trigger and detail_level != "minimal" else "")
    elif hook_type == "user_prompt_expansion":
        cmd = stdin_data.get("original_command", "")
        return "Command expanded" + (f": {_truncate(str(cmd), 60)}" if cmd else "")
    elif hook_type == "post_tool_batch":
        tools = stdin_data.get("tools")
        n = len(tools) if isinstance(tools, list) else 0
        return f"Tool batch finished ({n} tools)" if n else "Tool batch finished"
    elif hook_type == "message_display":
        return "Message displayed"
    elif hook_type == "shell_before":
        cmd = stdin_data.get("command", "")
        if detail_level == "minimal" or not cmd:
            return "Shell command starting"
        return f"Shell: {_truncate(str(cmd), max_len)}"
    elif hook_type == "shell_after":
        cmd = stdin_data.get("command", "")
        if detail_level == "minimal" or not cmd:
            return "Shell command finished"
        return f"Shell done: {_truncate(str(cmd), max_len)}"
    elif hook_type == "mcp_before":
        tool = stdin_data.get("tool_name", "")
        return "MCP tool starting" + (f": {tool}" if tool else "")
    elif hook_type == "mcp_after":
        tool = stdin_data.get("tool_name", "")
        return "MCP tool finished" + (f": {tool}" if tool else "")
    elif hook_type == "file_read":
        fp = stdin_data.get("file_path", "")
        if not fp:
            return "Reading file"
        return f"Reading {Path(str(fp)).name}"
    elif hook_type == "agent_response":
        text = stdin_data.get("text", "")
        if text and detail_level != "minimal":
            return f"Response ready: {_clean_for_output(str(text), max(max_len * 2, 80))}"
        return "Response ready"
    elif hook_type == "agent_thinking":
        dur = stdin_data.get("duration_ms", "")
        if dur and detail_level != "minimal":
            try:
                return f"Finished thinking ({int(dur) / 1000:.1f}s)"
            except (TypeError, ValueError):
                pass
        return "Finished thinking"
    elif hook_type == "workspace_open":
        roots = stdin_data.get("workspace_roots")
        if isinstance(roots, list) and roots:
            return f"Workspace opened: {Path(str(roots[0])).name}"
        return "Workspace opened"
    elif hook_type == "tab_file_edit":
        fp = stdin_data.get("file_path", "")
        if not fp:
            return "Inline edit applied"
        return f"Inline edit: {Path(str(fp)).name}"
    return hook_type.replace("_", " ").title()

# =============================================================================
# DESKTOP NOTIFICATIONS
# =============================================================================

def _escape_notification_string(s: str) -> str:
    """Escape a string for safe use in an osascript literal.

    macOS only. PowerShell does not treat ``\\`` as an escape character, so
    using this on a PowerShell command produces a script that fails to parse
    the moment the body contains a quote — use escape_powershell_string()
    there instead (v6.5.1 fix).
    """
    # Remove characters that could cause shell/osascript injection
    return s.replace('"', '\\"').replace("'", "\\'").replace('`', '').replace('$', '')


# Windows toast backends, in preference order. Probed once and cached: the
# probe costs a synchronous PowerShell launch, which a hook must not pay on
# every event.
_WINDOWS_BACKENDS: Tuple[str, ...] = ("winrt", "burnttoast", "notifyicon")
_NOTIFY_BACKEND_FILE = "notify_backend"
_NOTIFY_BACKEND_TTL_SEC = 7 * 24 * 3600  # re-probe weekly; OS upgrades change what works

# Wall-clock ceiling for the whole probe chain. Every handler in
# plugins/audio-hooks/hooks/hooks.json is registered with "timeout": 10, and
# Windows kills an async hook's process tree (docs/EVENT_BEHAVIOR_NOTES.md), so
# a chain that overran would be reaped mid-flight. The budget stays under that
# so the probe always survives long enough to persist its verdict.
_NOTIFY_PROBE_BUDGET_SEC = 8.0

# Per-backend timeouts. Generous against measured cost (a warm WinRT toast and
# the module query each resolve in ~0.3 s here) but they must *sum* to less than
# the budget, or a backend late in the chain could never be reached and a host
# where WinRT fails would re-probe forever instead of settling on a verdict.
# A backend is either given its full timeout or not run at all — the budget is
# never spent by truncating one, because a squeezed timeout would condemn a
# working backend and pin the host to a worse one for a week. A genuinely slow
# host overruns instead, which is inconclusive rather than wrong: nothing is
# cached, the balloon still fires, and the retry finds PowerShell warm.
_WINRT_PROBE_TIMEOUT_SEC = 3.0
_MODULE_QUERY_TIMEOUT_SEC = 1.5
_BURNTTOAST_PROBE_TIMEOUT_SEC = 3.0

# _run_powershell outcomes. The failed/inconclusive split is load-bearing: a
# *failed* backend is genuinely broken here and the next one down may be cached
# as the winner, but an *inconclusive* one may merely be slow, so caching past
# it would be a guess with a week-long lifetime.
PROBE_OK = "ok"
PROBE_FAILED = "failed"
PROBE_INCONCLUSIVE = "inconclusive"

_BURNTTOAST_QUERY = "if (Get-Module -ListAvailable -Name BurntToast) { exit 0 } else { exit 1 }"

# Well-known shell AppUserModelID for Windows PowerShell. A WinRT toast must be
# addressed to a *registered* AUMID or Windows drops it silently; borrowing the
# shell's own PowerShell entry means nothing has to be installed or registered.
_POWERSHELL_AUMID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"


def _build_windows_toast_script(backend: str, title: str, message: str,
                                urgency: str = "normal") -> str:
    """Build the PowerShell source for one Windows notification backend.

    Split out from the dispatcher so the generated script can be parsed in a
    test without spawning a toast (see tests/test_desktop_notification.py).
    """
    safe_title = escape_powershell_string(title)
    safe_message = escape_powershell_string(message)

    if backend == "winrt":
        # Text is inserted as XML *text nodes*, not interpolated into the XML
        # source, so XML metacharacters in a tool command cannot break the
        # document. Escaping still applies to the PowerShell literal itself.
        # silent="true" because the audio track already played the sound.
        duration = 'long' if urgency == "critical" else 'short'
        return (
            '$ErrorActionPreference = "Stop"; '
            '$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]; '
            '$null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]; '
            '$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent('
            '[Windows.UI.Notifications.ToastTemplateType]::ToastText02); '
            '$nodes = $xml.GetElementsByTagName("text"); '
            f'$null = $nodes.Item(0).AppendChild($xml.CreateTextNode("{safe_title}")); '
            f'$null = $nodes.Item(1).AppendChild($xml.CreateTextNode("{safe_message}")); '
            '$audio = $xml.CreateElement("audio"); '
            '$audio.SetAttribute("silent", "true"); '
            '$null = $xml.DocumentElement.AppendChild($audio); '
            f'$xml.DocumentElement.SetAttribute("duration", "{duration}"); '
            '$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); '
            '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('
            f'"{_POWERSHELL_AUMID}").Show($toast)'
        )

    if backend == "burnttoast":
        return (
            '$ErrorActionPreference = "Stop"; '
            'Import-Module BurntToast -ErrorAction Stop; '
            f'New-BurntToastNotification -Text "{safe_title}", "{safe_message}" -Silent'
        )

    # notifyicon: pre-Win10 tray balloon. Windows 11 drops these unpredictably,
    # hence its place at the end of the chain, but it is still correct on 7/8
    # and on hosts where the WinRT projection is unavailable.
    icon = "Warning" if urgency == "critical" else "Info"
    return (
        '[void][System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms"); '
        '$n = New-Object System.Windows.Forms.NotifyIcon; '
        '$n.Icon = [System.Drawing.SystemIcons]::Information; '
        '$n.Visible = $true; '
        f'$n.ShowBalloonTip(5000, "{safe_title}", "{safe_message}", '
        f'[System.Windows.Forms.ToolTipIcon]::{icon}); '
        'Start-Sleep -Seconds 6; '
        '$n.Dispose()'
    )


def _powershell_command(script: str) -> List[str]:
    """Always ``powershell.exe`` (Windows PowerShell 5.1), never ``pwsh`` — the
    WinRT projection the toast backend relies on needs extra assembly loading
    under PowerShell 7. ``-NoProfile`` keeps a hook from paying for the user's
    profile on every notification or spoken message.
    """
    return [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-WindowStyle", "Hidden", "-Command", script,
    ]


def _creation_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def _spawn_powershell(script: str) -> bool:
    """Fire-and-forget a hidden PowerShell script. True means it was spawned.

    Never raises.
    """
    try:
        subprocess.Popen(
            _powershell_command(script),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_creation_flags(),
        )
        return True
    except Exception as e:
        log_debug(f"PowerShell dispatch failed: {e}")
        return False


def _run_powershell(script: str, timeout: float) -> str:
    """Run a hidden PowerShell script to completion. Never raises.

    Returns PROBE_OK (exit 0), PROBE_FAILED (non-zero exit — broken here), or
    PROBE_INCONCLUSIVE (timed out or could not be launched, so this host has
    told us nothing we may act on).
    """
    try:
        proc = subprocess.run(
            _powershell_command(script),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            creationflags=_creation_flags(),
        )
        return PROBE_OK if proc.returncode == 0 else PROBE_FAILED
    except subprocess.TimeoutExpired:
        log_debug(f"PowerShell probe timed out after {timeout}s")
        return PROBE_INCONCLUSIVE
    except Exception as e:
        log_debug(f"PowerShell probe could not run: {e}")
        return PROBE_INCONCLUSIVE


def _notify_backend_cache_path() -> Optional[Path]:
    """Marker file recording which Windows toast backend works on this host."""
    try:
        ensure_queue_dir()
        return _prefs().queue_dir / _NOTIFY_BACKEND_FILE
    except Exception:
        return None


def _read_cached_notify_backend() -> Optional[str]:
    """Return the cached backend, or None when missing, stale or unreadable."""
    path = _notify_backend_cache_path()
    if path is None:
        return None
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        backend = data.get("backend")
        if backend not in _WINDOWS_BACKENDS:
            return None
        if data.get("release") != platform.release():
            return None
        if time.time() - float(data.get("ts", 0)) > _NOTIFY_BACKEND_TTL_SEC:
            return None
        return str(backend)
    except Exception as e:
        log_debug(f"Could not read notify backend cache: {e}")
        return None


def _write_cached_notify_backend(backend: str) -> None:
    path = _notify_backend_cache_path()
    if path is None:
        return
    try:
        path.write_text(
            json.dumps({"backend": backend, "ts": time.time(), "release": platform.release()}),
            encoding="utf-8",
        )
    except OSError as e:
        log_debug(f"Could not write notify backend cache: {e}")


def _clear_cached_notify_backend() -> None:
    path = _notify_backend_cache_path()
    if path is None:
        return
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _probe_windows_backend(title: str, message: str, urgency: str) -> Tuple[Optional[str], bool]:
    """Walk the chain until a backend is confirmed, showing *this* notification
    through it. Returns (backend, cacheable).

    The whole chain shares one wall-clock budget (_NOTIFY_PROBE_BUDGET_SEC): a
    probe that overran the hook's registered timeout would be reaped by the
    harness, and a reaped probe could leave behind a cache entry naming a
    backend it never finished validating. So a backend runs only when its full
    timeout still fits in the budget, and anything inconclusive — a timeout, a
    launch failure, or a budget too small to finish the chain — makes the whole
    probe non-cacheable. The notification still goes out best-effort through the
    tray balloon; only the week-long verdict is withheld, and the next
    notification probes again. First-of-the-week may therefore be best-effort
    with the cache written on the second one.
    """
    deadline = time.monotonic() + _NOTIFY_PROBE_BUDGET_SEC
    inconclusive = False

    for backend in _WINDOWS_BACKENDS:
        if backend == "notifyicon":
            break  # last resort, handled below — it can never be validated

        if backend == "burnttoast":
            if deadline - time.monotonic() < _MODULE_QUERY_TIMEOUT_SEC + _BURNTTOAST_PROBE_TIMEOUT_SEC:
                inconclusive = True
                break
            available = _run_powershell(_BURNTTOAST_QUERY, _MODULE_QUERY_TIMEOUT_SEC)
            if available == PROBE_INCONCLUSIVE:
                inconclusive = True
                break
            if available != PROBE_OK:
                continue  # not installed; never install it
            timeout = _BURNTTOAST_PROBE_TIMEOUT_SEC
        else:
            timeout = _WINRT_PROBE_TIMEOUT_SEC

        if deadline - time.monotonic() < timeout:
            inconclusive = True
            break

        result = _run_powershell(
            _build_windows_toast_script(backend, title, message, urgency), timeout
        )
        if result == PROBE_OK:
            return backend, True
        if result == PROBE_INCONCLUSIVE:
            inconclusive = True
            break

    # The balloon only lives as long as its host process, so it can never be run
    # synchronously; a successful spawn is all the signal there is.
    if _spawn_powershell(_build_windows_toast_script("notifyicon", title, message, urgency)):
        return "notifyicon", not inconclusive
    return None, False


def _send_windows_notification(title: str, message: str, urgency: str) -> Tuple[bool, str]:
    """Dispatch on Windows via the cached backend, probing the chain if needed.

    Returns (dispatched, backend_name).
    """
    backend = _read_cached_notify_backend()
    if backend:
        if _spawn_powershell(_build_windows_toast_script(backend, title, message, urgency)):
            return True, backend
        # The cached choice can no longer even be spawned: re-probe the chain.
        _clear_cached_notify_backend()

    backend, cacheable = _probe_windows_backend(title, message, urgency)
    if backend is None:
        return False, "none"
    if cacheable:
        _write_cached_notify_backend(backend)
    return True, backend


def send_desktop_notification(title: str, message: str, urgency: str = "normal") -> bool:
    """Send a desktop notification using platform-native methods.

    Args:
        title: Notification title
        message: Notification body text
        urgency: 'normal' or 'critical'

    Returns:
        True when a backend accepted the notification, False otherwise. For a
        fire-and-forget dispatch that means the backend is known-good on this
        host and the process spawned.
    """
    system = platform.system()
    backend = "unsupported"
    dispatched = False
    reason = f"no desktop notification backend for platform {system}"

    try:
        if system == "Darwin":
            # Audio is handled separately by play_audio_macos() via afplay.
            # Omit "sound name" to avoid double sound and to work on macOS 15+
            # where osascript notifications may be silently blocked.
            safe_title = _escape_notification_string(title)
            safe_message = _escape_notification_string(message)
            script = f'display notification "{safe_message}" with title "{safe_title}"'
            subprocess.Popen(
                ["osascript", "-e", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            backend, dispatched = "osascript", True

        elif system == "Linux":
            if is_wsl():
                # WSL: the toast is drawn by the Windows host, so it goes
                # through powershell.exe with PowerShell escaping.
                backend = "notifyicon_wsl"
                dispatched = _spawn_powershell(
                    _build_windows_toast_script("notifyicon", title, message, urgency)
                )
                if not dispatched:
                    reason = "powershell.exe dispatch failed"
            else:
                # Native Linux: use notify-send
                if shutil.which("notify-send"):
                    cmd = ["notify-send"]
                    if urgency == "critical":
                        cmd.extend(["-u", "critical"])
                    cmd.extend([title, message])
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    backend, dispatched = "notify-send", True
                else:
                    backend = "none"
                    reason = "notify-send not found"

        elif system == "Windows":
            dispatched, backend = _send_windows_notification(title, message, urgency)
            if not dispatched:
                reason = "every Windows toast backend failed (winrt, burnttoast, notifyicon)"

    except FileNotFoundError as e:
        reason = f"notification command not found: {e}"
    except Exception as e:
        reason = f"desktop notification failed: {e}"
        log_error(reason)

    if dispatched:
        log_event("info", "desktop_notification", backend=backend,
                  status="DISPATCHED", urgency=urgency)
    else:
        log_error_event(ErrorCode.NOTIFICATION_FAILED, "desktop_notification",
                        message=reason, backend=backend, urgency=urgency)
    return dispatched

# =============================================================================
# TEXT-TO-SPEECH
# =============================================================================

def _build_sapi_script(message: str) -> str:
    """PowerShell source for the SAPI speech path (Windows and WSL).

    Same escaping trap as the toast: this is interpolated into a PowerShell
    double-quoted string, so it needs backtick escaping. Until v6.5.1 it used
    the osascript escaper, and any spoken text containing a quote produced a
    script that failed to parse — silent TTS, with `$` and backticks dropped
    from the wording even when it did parse.
    """
    return (
        'Add-Type -AssemblyName System.Speech; '
        '(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('
        f'"{escape_powershell_string(message)}")'
    )


def play_tts(message: str) -> bool:
    """Speak a message using platform-native TTS.

    Args:
        message: Text to speak

    Returns:
        True when an engine accepted the message, False otherwise.
    """
    system = platform.system()
    backend = "unsupported"
    dispatched = False
    reason = f"no TTS engine for platform {system}"

    try:
        if system == "Darwin":
            # argv, not a shell string: `say` needs no escaping.
            subprocess.Popen(
                ["say", message],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            backend, dispatched = "say", True

        elif system == "Linux":
            if is_wsl():
                # WSL: speech is synthesised by the Windows host via SAPI.
                backend = "sapi_wsl"
                dispatched = _spawn_powershell(_build_sapi_script(message))
                if not dispatched:
                    reason = "powershell.exe dispatch failed"
            else:
                # Native Linux: try espeak, then spd-say
                backend = "none"
                reason = "no Linux TTS engine found (espeak, spd-say)"
                for cmd_name in ["espeak", "spd-say"]:
                    if shutil.which(cmd_name):
                        subprocess.Popen(
                            [cmd_name, message],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                        backend, dispatched = cmd_name, True
                        break

        elif system == "Windows":
            backend = "sapi"
            dispatched = _spawn_powershell(_build_sapi_script(message))
            if not dispatched:
                reason = "powershell.exe dispatch failed"

    except FileNotFoundError as e:
        reason = f"TTS command not found: {e}"
    except Exception as e:
        reason = f"TTS failed: {e}"
        log_error(reason)

    if dispatched:
        log_event("info", "tts_dispatch", backend=backend, status="DISPATCHED")
    else:
        log_error_event(ErrorCode.TTS_FAILED, "tts_dispatch",
                        message=reason, backend=backend)
    return dispatched

# =============================================================================
# WEBHOOK
# =============================================================================

def send_webhook(hook_type: str, context: str, stdin_data: dict, config: Dict[str, Any]) -> None:
    """Send hook event to a configured webhook URL (Slack, Discord, Teams, ntfy, or custom).

    v5.0: fire-and-forget via subprocess so the parent process can exit
    immediately even on slow webhooks. Raw payloads carry the
    `audio-hooks.webhook.v1` schema with all enriched stdin fields surfaced
    as top-level keys for downstream consumers to pin.
    """
    webhook = config.get("webhook_settings", {})
    if not webhook.get("enabled"):
        return

    url = webhook.get("url", "")
    if not url:
        return

    # Check if this hook type should trigger webhook
    allowed = webhook.get("hook_types", [])
    if allowed and hook_type not in allowed:
        return

    fmt = webhook.get("format", "raw")
    headers: Dict[str, str] = {str(k): str(v) for k, v in (webhook.get("headers") or {}).items()}
    display_name = _invoker_display_name()

    # Format payload based on target service
    if fmt == "slack":
        payload: Any = {"text": f"\U0001f514 {display_name}: {context}"}
    elif fmt == "discord":
        payload = {"content": f"\U0001f514 {display_name}: {context}"}
    elif fmt == "teams":
        payload = {"text": f"\U0001f514 {display_name}: {context}"}
    elif fmt == "ntfy":
        # ntfy.sh uses plain text body with header-based metadata
        headers.setdefault("Title", display_name)
        headers.setdefault("Priority", "default")
        headers.setdefault("Tags", "robot")
        payload = context  # plain text
    elif fmt == "raw":
        # v5.0 enriched schema: surface every new stdin field at the top level
        # and tag with a versioned schema string so consumers can pin.
        # v5.1.4: add invoker + cursor.* sub-object for cross-IDE consumers.
        # user_email is redacted from event_data by default for privacy; opt
        # in via webhook_settings.include_user_email = true if needed.
        include_email = bool(webhook.get("include_user_email"))
        cursor_specific = {
            k: stdin_data.get(k)
            for k in (
                "cursor_version", "conversation_id", "generation_id",
                "reason", "final_status", "duration_ms",
                "is_background_agent", "workspace_roots", "model",
                "error_message",
            )
            if stdin_data.get(k) is not None
        }
        if include_email and "user_email" in stdin_data:
            cursor_specific["user_email"] = stdin_data["user_email"]
        # Codex stdin (per developers.openai.com/codex/hooks) carries the same
        # snake_case shape as Claude Code, plus a few Codex-specifics. Surface
        # them in their own sub-object so downstream consumers can branch on
        # `invoker == "codex"` cleanly.
        codex_specific = {
            k: stdin_data.get(k)
            for k in (
                "turn_id", "tool_use_id", "permission_mode", "tool_response",
                "stop_hook_active",
            )
            if stdin_data.get(k) is not None
        }
        sanitized_event = {
            k: v for k, v in stdin_data.items()
            if k not in ("transcript_path",)
            and (include_email or k != "user_email")
        }
        payload = {
            "schema": "audio-hooks.webhook.v1",
            "version": HOOK_RUNNER_VERSION,
            "invoker": _get_invoker(),
            "hook_type": hook_type,
            "context": context,
            "timestamp": time.time(),
            "session_id": stdin_data.get("session_id"),
            "session_name": stdin_data.get("session_name"),
            "worktree": stdin_data.get("worktree"),
            "agent_id": stdin_data.get("agent_id"),
            "agent_type": stdin_data.get("agent_type"),
            "agent": stdin_data.get("agent"),
            "rate_limits": stdin_data.get("rate_limits"),
            "last_assistant_message": stdin_data.get("last_assistant_message"),
            "notification_type": stdin_data.get("notification_type"),
            "error_type": stdin_data.get("error_type"),
            "source": stdin_data.get("source"),
            "trigger": stdin_data.get("trigger"),
            "load_reason": stdin_data.get("load_reason"),
            "permission_suggestions": stdin_data.get("permission_suggestions"),
            "tool_name": stdin_data.get("tool_name"),
            "tool_input": stdin_data.get("tool_input"),
            "cursor": cursor_specific or None,
            "codex": codex_specific or None,
            "event_data": sanitized_event,
        }
    else:
        payload = {"text": f"{display_name}: {context}"}

    if isinstance(payload, str):
        body_bytes = payload.encode("utf-8")
        headers.setdefault("Content-Type", "text/plain; charset=utf-8")
    else:
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    # Fire-and-forget: spawn a tiny Python subprocess that does the urlopen
    # and exits. The parent can exit immediately. Survives main process exit
    # because the child is fully detached.
    sender = (
        "import sys, json, urllib.request, urllib.error\n"
        "url = sys.argv[1]\n"
        "headers = json.loads(sys.argv[2])\n"
        "timeout = float(sys.argv[3])\n"
        "data = sys.stdin.buffer.read()\n"
        "try:\n"
        "    req = urllib.request.Request(url, data=data, headers=headers, method='POST')\n"
        "    urllib.request.urlopen(req, timeout=timeout)\n"
        "except Exception:\n"
        "    pass\n"
    )
    try:
        creation_flags = 0
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            creation_flags = subprocess.CREATE_NO_WINDOW
        proc = subprocess.Popen(
            [sys.executable, "-c", sender, url, json.dumps(headers), "5"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        if proc.stdin is not None:
            proc.stdin.write(body_bytes)
            proc.stdin.close()
        log_event("info", "webhook_dispatched", hook=hook_type, format=fmt)
    except Exception as e:
        log_error_event(ErrorCode.WEBHOOK_HTTP_ERROR, "webhook_dispatch", message=str(e), hook=hook_type)


# =============================================================================
# RATE LIMIT PRE-CHECK (v5.0)
# =============================================================================

def _threshold_values(raw: Any) -> List[Any]:
    """Coerce a configured thresholds value to a list without raising.

    v6.6: ``rate-limits set --five-hour-thresholds 90`` used to store the integer
    90, and ``sorted(90)`` then raised before any audio for every event whose
    payload carried ``rate_limits``. The setter now stores a list; this keeps a
    config written by an older version (or by hand) from crashing the hook.
    """
    if raw is None or isinstance(raw, bool):
        return []
    if isinstance(raw, (int, float)):
        return [raw]
    if isinstance(raw, str):
        return [x.strip() for x in raw.split(",") if x.strip()]
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return []


def check_rate_limits(stdin_data: Dict[str, Any], config: Dict[str, Any]) -> None:
    """Inspect stdin `rate_limits` and play a warning audio when crossing thresholds.

    Side effect only: plays one audio cue per (window, threshold, resets_at)
    tuple, debounced via marker file in the queue dir. Snooze and the user's
    `rate_limit_alerts.enabled` flag both gate this.

    Stdin schema (Claude Code v2.1.80+, Claude.ai subscribers only):
        {
          "rate_limits": {
            "five_hour": {"used_percentage": 78, "resets_at": 1738425600},
            "seven_day": {"used_percentage": 41, "resets_at": 1738857600}
          }
        }
    """
    rl_cfg = config.get("rate_limit_alerts", {}) or {}
    if rl_cfg.get("enabled", True) is False:
        return
    rate_limits = stdin_data.get("rate_limits") if isinstance(stdin_data, dict) else None
    if not isinstance(rate_limits, dict):
        return

    five_thresholds = _threshold_values(rl_cfg.get("five_hour_thresholds", [80, 95]))
    seven_thresholds = _threshold_values(rl_cfg.get("seven_day_thresholds", [80, 95]))
    audio_file_name = rl_cfg.get("audio", "notification-urgent.mp3")

    windows = (("five_hour", five_thresholds), ("seven_day", seven_thresholds))
    for window_name, thresholds in windows:
        window = rate_limits.get(window_name)
        if not isinstance(window, dict):
            continue
        used = window.get("used_percentage")
        resets_at = window.get("resets_at")
        if used is None or resets_at is None:
            continue
        try:
            used_int = int(used)
            resets_int = int(resets_at)
        except (TypeError, ValueError, OverflowError):
            continue
        # Fire only the highest crossed threshold per call. Each marker is
        # keyed on resets_at so a new reset window can re-fire.
        parsed: List[int] = []
        for threshold in thresholds:
            try:
                parsed.append(int(threshold))
            except (TypeError, ValueError, OverflowError):  # OverflowError: 1e999 / inf
                continue
        for t_int in sorted(parsed, reverse=True):
            if used_int < t_int:
                continue
            ensure_queue_dir()
            marker = _prefs().queue_dir / f"rate_limit_{window_name}_{t_int}_{resets_int}"
            if marker.exists():
                break
            try:
                marker.write_text(str(time.time()), encoding="utf-8")
            except OSError:
                pass
            # Resolve audio file across both themes
            theme_cfg = config.get("audio_theme", "default")
            candidates: List[Path] = []
            if theme_cfg == "custom":
                candidates.append(AUDIO_DIR / "custom" / ("chime-" + audio_file_name))
                candidates.append(AUDIO_DIR / "default" / audio_file_name)
            else:
                candidates.append(AUDIO_DIR / "default" / audio_file_name)
                candidates.append(AUDIO_DIR / "custom" / ("chime-" + audio_file_name))
            audio_path = next((p for p in candidates if p.exists()), None)
            if audio_path is None:
                log_error_event(ErrorCode.AUDIO_FILE_MISSING, "rate_limit_alert",
                                message=f"rate-limit alert audio not found: {audio_file_name}")
                break
            try:
                play_audio(audio_path)
            except Exception as e:
                log_error_event(ErrorCode.AUDIO_PLAY_FAILED, "rate_limit_alert", message=str(e))
                break
            log_event("warn", "rate_limit_alert",
                      window=window_name,
                      threshold=t_int,
                      used_percentage=used_int,
                      resets_at=resets_int,
                      audio_file=audio_file_name)
            break  # one alert per call


# =============================================================================
# MAIN HOOK EXECUTION
# =============================================================================

# ---------------------------------------------------------------------------
# terminalSequence (v6.5) — Claude Code emits an OSC escape on our behalf.
# ---------------------------------------------------------------------------
#
# Claude Code 2.1.141+ reads a `terminalSequence` field from a hook's stdout
# JSON and writes the sequence to the terminal itself. That is worth having:
# it produces a desktop toast / window title / bell on every platform with no
# dependencies, and it works even though a hook has no controlling terminal —
# which is precisely the away-from-screen case echook exists for.
#
# It is also the one feature that requires this runner to write to stdout, and
# writing to stdout is exactly what caused the v6.3.4 outage: `WorktreeCreate`
# is a *provider* hook whose command form treats stdout as a return value, so a
# hook that printed anything hijacked worktree creation. The containment rules
# below are not defensive padding, they are the reason this is safe to ship:
#
#   1. Only events on TERMINAL_SEQUENCE_SAFE_EVENTS may emit. Everything else
#      stays byte-for-byte silent on stdout.
#   2. Emit ONLY {"terminalSequence": ...}. Never `hookSpecificOutput`, never
#      `continue`, never `decision` — those fields change Claude Code's
#      behaviour, and several events we register would act on them:
#        * MessageDisplay.displayContent REPLACES Claude's visible output
#        * Elicitation / ElicitationResult .action answers an MCP prompt
#          (accept | decline | cancel) on the user's behalf
#        * TeammateIdle / TaskCompleted honour {"continue": false}
#   3. Claude Code only, by invoker. Cursor and Codex have no such contract and
#      would treat the JSON as ordinary output.
#   4. Opt-in. Default off.
#
# Upstream allows only OSC 0/1/2/9/99/777 and BEL; anything else is dropped.
TERMINAL_SEQUENCE_SAFE_EVENTS = frozenset({
    "notification",
    "stop",
    "stop_failure",
    "permission_request",
    "permission_denied",
    "subagent_stop",
    "task_completed",
    "teammate_idle",
    "session_end",
})

# Events that consume a hook's stdout JSON to change what Claude Code does.
# Kept as data so tests/test_terminal_sequence.py can assert the two sets never
# intersect, rather than trusting the comment above.
TERMINAL_SEQUENCE_FORBIDDEN_EVENTS = frozenset({
    "message_display",
    "elicitation",
    "elicitation_result",
    "pretooluse",
    "posttooluse",
    "post_tool_batch",
    "userpromptsubmit",
    "user_prompt_expansion",
    "session_start",
    "setup",
    "subagent_start",
    "cwd_changed",
    "file_changed",
})

_BEL = chr(7)   #  — terminates an OSC sequence
_ESC = chr(27)  # \e — introduces one


def _sanitise_osc_text(text: str, limit: int = 120) -> str:
    """Strip anything that could terminate or re-open an escape sequence.

    An unsanitised title is a terminal-injection vector: a BEL or ESC inside
    the body closes our sequence early and hands the remainder to the terminal
    as a fresh command. Semicolons delimit fields in OSC 777, so they go too.
    """
    unsafe = (_BEL, _ESC, ";")
    cleaned = []
    for ch in str(text):
        if ch in unsafe:
            cleaned.append(" ")
        elif ord(ch) < 32 or ord(ch) == 127:
            cleaned.append(" ")
        else:
            cleaned.append(ch)
    return " ".join("".join(cleaned).split())[:limit]


def build_terminal_sequence(style: str, title: str, body: str) -> Optional[str]:
    """Build one allowed OSC sequence, or None if it cannot be made safely."""
    title = _sanitise_osc_text(title, 60)
    body = _sanitise_osc_text(body, 120)
    if style == "bell":
        return _BEL
    if style == "title":
        # OSC 2 — window title.
        return f"{_ESC}]2;{title or 'Claude Code'}{_BEL}"
    if style == "osc777":
        # OSC 777 — rxvt/kitty/WezTerm desktop notification.
        return f"{_ESC}]777;notify;{title or 'Claude Code'};{body}{_BEL}"
    # Default: OSC 9 — iTerm2 / kitty / WezTerm / Ghostty.
    message = f"{title}: {body}".strip(": ") if body else title
    if not message:
        return None
    # Upstream rejects an OSC 9 body starting with a digit unless it is the
    # 9;4 progress form, so make sure we never generate one by accident.
    if message[0].isdigit():
        message = "Claude Code " + message
    return f"{_ESC}]9;{message}{_BEL}"


def emit_terminal_sequence(hook_type: str, context: str, config: Dict[str, Any]) -> bool:
    """Print {"terminalSequence": ...} for Claude Code to write out.

    Returns True if something was printed. Every failure path returns False
    silently: a notification channel must never be able to break the hook.
    """
    try:
        settings = config.get("notification_settings", {}) or {}
        ts = settings.get("terminal_sequence", {}) or {}
        if not isinstance(ts, dict) or not ts.get("enabled"):
            return False
        if _get_invoker() != "claude-code":
            return False
        if hook_type in TERMINAL_SEQUENCE_FORBIDDEN_EVENTS:
            return False
        if hook_type not in TERMINAL_SEQUENCE_SAFE_EVENTS:
            return False
        allowed = ts.get("hook_types")
        if isinstance(allowed, list) and allowed and hook_type not in allowed:
            return False

        style = str(ts.get("style", "osc9") or "osc9")
        sequence = build_terminal_sequence(style, "Claude Code", context or hook_type)
        if not sequence:
            return False
        sys.stdout.write(json.dumps({"terminalSequence": sequence}))
        sys.stdout.write(chr(10))
        sys.stdout.flush()
        return True
    except Exception:
        return False


def run_hook(hook_type: str, stdin_data: dict = None, variant: Optional[str] = None) -> int:
    """
    Main hook execution function.

    ``variant`` is the synthetic event name this invocation arrived under (e.g.
    ``notification_idle_prompt``), or None for a bare canonical invocation. It
    is passed explicitly rather than read from ``_current_synthetic_variant`` so
    that callers driving run_hook directly — notably ``audio-hooks test`` — get
    a clean gating decision instead of inheriting stale module state from a
    previous iteration.

    Returns:
        0 on success (hook executed or disabled)
        Non-zero on error
    """
    # Pin log context for the rest of this invocation so every NDJSON event
    # carries session_id and hook type without per-call repetition.
    sid = (stdin_data or {}).get("session_id") if isinstance(stdin_data, dict) else None
    _set_log_context(sid, hook_type)
    log_event("debug", "hook_start",
              project_dir=str(PROJECT_DIR),
              audio_dir=str(AUDIO_DIR),
              queue_dir=str(_prefs().queue_dir),
              synthetic_variant=_current_synthetic_variant)

    # v5.1.6: Cursor bridge invariants. Cursor's third-party-hooks bridge maps
    # 8 Claude Code events (PreToolUse, PostToolUse, UserPromptSubmit, Stop,
    # SubagentStop, SessionStart, SessionEnd, PreCompact) to Cursor events;
    # ``Notification`` and ``PermissionRequest`` have no Cursor equivalent (per
    # cursor.com/docs/reference/third-party-hooks). Cursor never invokes them
    # under the auto-bridge, but a hand-edited ``~/.cursor/hooks.json`` could,
    # and a future Cursor release might add equivalents. Skip cleanly so the
    # behaviour is locked-down and observable in logs.
    # ``notification`` / ``permission_request`` have no Cursor equivalent. The
    # v6.2 Claude-Code-only events (Setup / UserPromptExpansion / PostToolBatch /
    # MessageDisplay) likewise never originate from Cursor — Cursor's granular
    # events (shell_before, mcp_before, file_read, agent_thinking, …) cover its
    # own surface instead.
    _CURSOR_UNSUPPORTED = {
        "notification", "permission_request",
        "setup", "user_prompt_expansion", "post_tool_batch", "message_display",
        # v6.5 — Claude Code only.
        "directory_added", "worktree_remove",
    }
    if _get_invoker() == "cursor" and hook_type in _CURSOR_UNSUPPORTED:
        log_event("debug", "skipped_no_cursor_equivalent", hook=hook_type)
        return 0

    # Codex supports 10 events: SessionStart, PreToolUse, PermissionRequest,
    # PostToolUse, PreCompact, PostCompact, UserPromptSubmit, SubagentStart,
    # SubagentStop, Stop. Other audio-hooks canonical events have no Codex
    # equivalent. The
    # bundled codex-hooks/hooks.json template never registers them, but a
    # hand-edited ~/.codex/hooks.json could, and a future Codex release might
    # add equivalents — skip cleanly so the behaviour is locked-down and
    # observable in the NDJSON log.
    # session_end is NOT here since v6.5: Codex gained SessionEnd in 0.145.0 and
    # `install --codex` registers it only when the installed Codex is new
    # enough. On older builds it is simply never registered, so it cannot fire
    # and needs no runtime guard.
    _CODEX_UNSUPPORTED = {
        "notification",
        "elicitation", "elicitation_result", "cwd_changed",
        "directory_added",
        "worktree_remove", "file_changed",
        "task_created", "task_completed", "teammate_idle", "config_change",
        "instructions_loaded", "permission_denied",
        # v6.2 — Codex has none of the new Claude Code / Cursor lifecycle events.
        "setup", "user_prompt_expansion", "post_tool_batch", "message_display",
        "shell_before", "shell_after", "mcp_before", "mcp_after", "file_read",
        "agent_response", "agent_thinking", "workspace_open", "tab_file_edit",
    }
    if _get_invoker() == "codex" and hook_type in _CODEX_UNSUPPORTED:
        log_event("debug", "skipped_no_codex_equivalent", hook=hook_type)
        return 0

    # v6.6: Claude Code fires SubagentStop for its own internal agents too
    # (prompt suggestions, /btw side questions), not only for subagents the
    # user's session spawned. For those, agent_type is the session's own agent
    # name (--agent / the `agent` setting) or, when the session runs without
    # one, an empty string -- so an empty string is the one reliable marker, and
    # it would otherwise announce "Background task finished" for work the user
    # never started. The key must be present: older Claude Code builds omit it,
    # and absence is not evidence of an internal agent. In a session started
    # with --agent, internal agents carry that agent's name and cannot be told
    # apart from real subagents; they still announce.
    # Scoped to Claude Code because that is the only editor whose docs and
    # binary establish the meaning: nothing says what an empty agent_type is in
    # a Cursor (native or auto-bridge, both report as "cursor") or Codex
    # SubagentStop payload, and guessing could silence a real subagent there.
    # A Claude Code hook that does not run as the plugin (legacy script install,
    # or a terminal that inherited CURSOR_VERSION) does not report "claude-code"
    # and keeps the old behaviour.
    # SubagentStart is left alone: the hooks reference documents the internal-
    # agent case for SubagentStop only.
    if (
        hook_type == "subagent_stop"
        and _get_invoker() == "claude-code"
        and isinstance(stdin_data, dict)
        and stdin_data.get("agent_type") == ""
    ):
        log_event("debug", "skipped_internal_subagent", hook=hook_type, agent_type="")
        return 0

    # v5.1.6: when ``audio-hooks install --cursor --force`` was used to install
    # the Cursor-native hooks on top of an already-active Claude Code plugin
    # bridge, both paths fire on every event and audio plays twice. The marker
    # file records ``duplicate_bridge_forced: true``; under Cursor we treat it
    # as a runtime opt-out for the native path so Claude Code's bridge handles
    # the event alone. Operators who want both paths active should remove the
    # marker; ``audio-hooks status`` already warns them they are in this state.
    if _get_invoker() == "cursor" and _read_install_marker().get("duplicate_bridge_forced") is True:
        meta = _ERROR_HINTS.get(ErrorCode.DUPLICATE_BRIDGE_RUNTIME_SKIP, {})
        log_event(
            "warn",
            "duplicate_bridge_runtime_skip",
            hook=hook_type,
            error={
                "code": ErrorCode.DUPLICATE_BRIDGE_RUNTIME_SKIP,
                "hint": meta.get("hint", ""),
                "suggested_command": meta.get("suggested_command", ""),
            },
        )
        return 0

    # Check if hook is enabled
    if not is_hook_enabled(hook_type, variant):
        log_trigger(hook_type, "DISABLED", variant or "")
        return 0

    # Check if snoozed
    if is_snoozed():
        log_trigger(hook_type, "SNOOZED")
        return 0

    # Load config once for notification/TTS/filter/webhook settings
    # (loaded before debounce so check_rate_limits can use it)
    config = load_config()

    # Rate-limit pre-check (v5.0): inspect stdin `rate_limits` and play a one-shot
    # warning audio when crossing thresholds. Runs before debounce so a chatty
    # PreToolUse stream doesn't suppress the alert.
    check_rate_limits(stdin_data or {}, config)

    # Check user-defined filters. This must run before the debounce check:
    # should_debounce() stamps the debounce file whenever it lets an event
    # through, so an event discarded here afterwards would still have opened the
    # window and suppressed the next genuine event of its kind (v6.7).
    if should_filter(hook_type, stdin_data or {}, config):
        log_trigger(hook_type, "FILTERED")
        return 0

    # Check debounce -- last gate, so only an event that will be delivered
    # stamps the window.
    if should_debounce(hook_type):
        log_trigger(hook_type, "DEBOUNCED")
        return 0

    # Auto-update from project directory if a newer version exists
    # (deferred until every gate has passed -- enabled, snoozed, filtered, then
    # debounced, in that order -- so a suppressed event never pays for the check)
    check_and_self_update()

    # Determine notification mode with per-hook override support
    notification_settings = config.get("notification_settings", {})
    # Matches config/default_preferences.json: a config predating the key must
    # behave like a fresh install, not like a stricter audio-only one.
    global_mode = notification_settings.get("mode", "audio_and_notification")
    per_hook_modes = {k: v for k, v in notification_settings.get("per_hook", {}).items() if not k.startswith("_")}
    mode = per_hook_modes.get(hook_type, global_mode)

    # Validate mode (fall back to global if invalid)
    valid_modes = ("audio_only", "notification_only", "audio_and_notification", "disabled")
    if mode not in valid_modes:
        log_debug(f"Invalid per_hook mode '{mode}' for {hook_type}, falling back to '{global_mode}'")
        mode = global_mode
    log_debug(f"Notification mode for {hook_type}: {mode} (global={global_mode})")

    # Get detail level for context messages
    detail_level = notification_settings.get("detail_level", "standard")
    if detail_level not in ("minimal", "standard", "verbose"):
        detail_level = "standard"

    # Play audio (unless mode is notification_only or disabled)
    if mode in ("audio_only", "audio_and_notification"):
        audio_file = get_audio_file(hook_type)
        if not audio_file:
            log_trigger(hook_type, "NO_AUDIO_CONFIG")
        elif not audio_file.exists():
            log_trigger(hook_type, "FILE_NOT_FOUND", str(audio_file))
            log_error(f"Audio file not found: {audio_file}")
        else:
            success = play_audio(audio_file)
            if success:
                log_trigger(hook_type, "PLAYED", audio_file.name)
            else:
                log_trigger(hook_type, "PLAY_FAILED", audio_file.name)
                log_error(f"Failed to play audio: {audio_file}")
    elif mode == "notification_only":
        log_trigger(hook_type, "AUDIO_SKIPPED", f"mode={mode}")
    elif mode == "disabled":
        log_trigger(hook_type, "AUDIO_SKIPPED", "mode=disabled")

    # Pre-compute notification context once for all channels that need it
    tts_settings = config.get("tts_settings", {})
    tts_enabled = tts_settings.get("enabled", False)
    webhook_settings = config.get("webhook_settings", {})
    webhook_enabled = webhook_settings.get("enabled", False)
    needs_context = (
        mode in ("notification_only", "audio_and_notification")
        or tts_enabled
        or webhook_enabled
    )
    context = get_notification_context(hook_type, stdin_data or {}, detail_level) if needs_context else ""
    if needs_context:
        context += _format_context_suffix(stdin_data or {}, detail_level)

    # Desktop notification (unless mode is audio_only or disabled)
    if mode in ("notification_only", "audio_and_notification"):
        urgency = "critical" if hook_type in ("notification", "permission_request", "posttoolusefailure", "stop_failure", "elicitation") else "normal"
        notif_sent = send_desktop_notification(_invoker_display_name(), context, urgency)
        if notif_sent:
            log_debug(f"Desktop notification sent for {hook_type}: {context}")
    elif mode == "disabled":
        log_trigger(hook_type, "NOTIFICATION_SKIPPED", "mode=disabled")

    # TTS (text-to-speech)
    if tts_enabled:
        custom_messages = tts_settings.get("messages", {})
        # v5.0: optionally speak Claude's actual reply for stop/subagent_stop.
        speak_msg = bool(tts_settings.get("speak_assistant_message", False))
        if speak_msg and hook_type in ("stop", "subagent_stop"):
            try:
                max_chars = int(tts_settings.get("assistant_message_max_chars", 200) or 200)
            except (TypeError, ValueError):
                max_chars = 200
            last_msg = (stdin_data or {}).get("last_assistant_message", "") if isinstance(stdin_data, dict) else ""
            spoken = _clean_for_output(str(last_msg), max_chars, for_speech=True) if last_msg else ""
            tts_message = spoken if spoken else custom_messages.get(hook_type, context)
        else:
            tts_message = custom_messages.get(hook_type, context)
        tts_sent = play_tts(tts_message)
        if tts_sent:
            log_event("info", "tts_spoken", message=_truncate(tts_message, 100))

    # Webhook (only if enabled)
    if webhook_enabled:
        send_webhook(hook_type, context, stdin_data or {}, config)

    # terminalSequence — last, and the only thing in this runner that writes to
    # stdout. Gated by an event allowlist; see emit_terminal_sequence.
    if emit_terminal_sequence(hook_type, context, config):
        log_event("info", "terminal_sequence_emitted", hook=hook_type)

    return 0


def main() -> int:
    """Main entry point."""
    # Check Python version
    if sys.version_info < (3, 6):
        print("Error: Python 3.6 or higher is required", file=sys.stderr)
        return 1

    # The Codex install template invokes us as `python hook_runner.py <hook> --invoker codex`.
    # Prime the invoker cache from the original argv BEFORE stripping (otherwise
    # downstream callers like user_preferences._resolve_data_dir would see a
    # stripped argv and return "unknown"). Then strip so the rest of this
    # function can use sys.argv[1] as the hook positional, unchanged.
    _get_invoker()
    sys.argv = strip_invoker_args(sys.argv)

    if len(sys.argv) < 2:
        print("Usage: python hook_runner.py <hook_type>", file=sys.stderr)
        print("Hook types: notification, stop, pretooluse, posttooluse, posttoolusefailure,", file=sys.stderr)
        print("            userpromptsubmit, subagent_stop, subagent_start, precompact,", file=sys.stderr)
        print("            session_start, session_end, permission_request,", file=sys.stderr)
        print("            teammate_idle, task_completed", file=sys.stderr)
        print("\nEnvironment variables:", file=sys.stderr)
        print("  CLAUDE_HOOKS_DEBUG=1  Enable debug logging", file=sys.stderr)
        return 1

    raw_arg = sys.argv[1].lower().replace("-", "_")

    # v5.0 native matcher routing: a synthetic event name like
    # "session_start_resume" or "stop_failure_rate_limit" resolves to a
    # canonical hook plus a per-variant audio override.
    canonical_hook, audio_override, variant_label = _resolve_synthetic_event(raw_arg)
    global _current_audio_override, _current_synthetic_variant
    _current_audio_override = audio_override
    _current_synthetic_variant = variant_label

    log_debug(f"Hook runner started: {raw_arg} (canonical={canonical_hook})")
    log_debug(f"Python version: {sys.version}")
    log_debug(f"Platform: {platform.system()} {platform.release()}")

    # v5.1.4: when Cursor's auto-bridge invokes our session_start hook, emit
    # an `env` override so every subsequent hook in this Cursor session sees
    # CLAUDE_PLUGIN_DATA pointing at the shared/native data dir. This is the
    # first-class fix for "Cursor plays the wrong theme because preferences
    # aren't found" — without it, each hook firing in a Cursor session falls
    # back to UserPreferences.data_dir at runtime (also correct, but slower
    # and depends on the file existing already).
    #
    # Per cursor.com/docs/hooks: "Session-scoped environment variables from
    # `sessionStart` hooks are passed to all subsequent hook executions within
    # that session." We deliberately emit this regardless of
    # enabled_hooks.session_start because the env override is a session-level
    # setup concern, not a notification concern.
    if canonical_hook == "session_start" and detect_invoker() == "cursor":
        try:
            data_dir = _prefs().data_dir
            sys.stdout.write(json.dumps({"env": {"CLAUDE_PLUGIN_DATA": str(data_dir)}}) + "\n")
            sys.stdout.flush()
        except Exception:
            pass  # never fatal — UserPreferences.data_dir is the runtime backstop

    # Parse stdin JSON from Claude Code (provides context about the hook event)
    stdin_data = parse_stdin()

    return run_hook(canonical_hook, stdin_data, variant_label)


if __name__ == "__main__":
    sys.exit(main())
