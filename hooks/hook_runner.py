#!/usr/bin/env python3
"""
Claude Code Audio Hooks - Python Hook Runner
Cross-platform hook runner that works on Windows, macOS, and Linux.
This replaces the bash-based hooks for better Windows compatibility.

Usage:
    python hook_runner.py <hook_type>

Hook types: notification, stop, pretooluse, posttooluse, posttoolusefailure,
            userpromptsubmit, subagent_stop, subagent_start, precompact,
            session_start, session_end, permission_request,
            teammate_idle, task_completed, stop_failure, postcompact,
            config_change, instructions_loaded, worktree_create,
            worktree_remove, elicitation, elicitation_result

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

# Version used for auto-sync: when the installed copy in ~/.claude/hooks/
# detects a newer version in the project directory, it self-updates.
HOOK_RUNNER_VERSION = "5.0.3"

# =============================================================================
# STRUCTURED LOGGING (NDJSON)
# =============================================================================
#
# All log events are written as one JSON object per line to events.ndjson.
# Schema is versioned ("audio-hooks.v1") so downstream consumers can pin.
# Error events include a stable `code` enum, a one-sentence `hint`, and an
# optional `suggested_command` Claude Code can run to fix the issue.
#
# Storage location, in priority order:
#   1. ${CLAUDE_PLUGIN_DATA}/logs/                  (plugin install)
#   2. ${CLAUDE_AUDIO_HOOKS_DATA}/logs/             (explicit override)
#   3. <temp>/claude_audio_hooks_queue/logs/        (legacy script install)

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
}


def get_log_dir() -> Path:
    """Resolve the log directory, creating it if necessary.

    Priority: CLAUDE_PLUGIN_DATA > CLAUDE_AUDIO_HOOKS_DATA > legacy temp dir.
    """
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        log_dir = Path(plugin_data) / "logs"
    else:
        explicit = os.environ.get("CLAUDE_AUDIO_HOOKS_DATA")
        if explicit:
            log_dir = Path(explicit) / "logs"
        else:
            if platform.system() == "Windows":
                base = Path(os.environ.get("TEMP", os.environ.get("TMP", "C:/Windows/Temp")))
            else:
                base = Path("/tmp")
            log_dir = base / "claude_audio_hooks_queue" / "logs"
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
        home / "claude-code-audio-hooks",
        home / "projects" / "claude-code-audio-hooks",
        home / "Documents" / "claude-code-audio-hooks",
        home / "repos" / "claude-code-audio-hooks",
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


def _is_running_from_plugin() -> bool:
    """True if this script is being invoked from a plugin install context.

    Two signals:
      1. CLAUDE_PLUGIN_DATA is set (hook fire context — Claude Code injects it).
      2. This file lives under <plugin_root>/hooks/ where
         <plugin_root>/.claude-plugin/plugin.json exists.
    """
    if os.environ.get("CLAUDE_PLUGIN_DATA"):
        return True
    try:
        here = Path(__file__).resolve()
        plugin_root = here.parent.parent  # hooks/hook_runner.py -> plugin root
        if (plugin_root / ".claude-plugin" / "plugin.json").exists():
            return True
    except Exception:
        pass
    return False


def _resolve_plugin_data_dir() -> Path:
    """Compute the plugin data dir even when CLAUDE_PLUGIN_DATA isn't set.

    Per Claude Code docs, the data dir is at ~/.claude/plugins/data/{id}/
    where {id} is the plugin name with non-[a-zA-Z0-9_-] chars replaced by -.
    For audio-hooks@chanmeng-audio-hooks the id is
    'audio-hooks-chanmeng-audio-hooks'.

    Used when the binary is invoked from a Bash tool call via the plugin's
    bin/ PATH (which doesn't inherit CLAUDE_PLUGIN_DATA from the hook fire
    context).
    """
    home = Path.home()
    data_root = home / ".claude" / "plugins" / "data"
    canonical = data_root / "audio-hooks-chanmeng-audio-hooks"
    if canonical.exists():
        return canonical
    if data_root.exists():
        try:
            for child in data_root.iterdir():
                if child.is_dir() and "audio-hooks" in child.name:
                    return child
        except OSError:
            pass
    # No data dir yet — return canonical so it can be created on first write
    return canonical


def _auto_init_user_prefs(target: Path) -> None:
    """Copy default_preferences.json into target if target doesn't exist."""
    if target.exists():
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        template = PROJECT_DIR / "config" / "default_preferences.json"
        if template.exists():
            import shutil as _sh
            _sh.copy2(str(template), str(target))
    except OSError:
        pass


def _resolve_config_file() -> Path:
    """Resolve user_preferences.json path.

    Resolution order:
      1. CLAUDE_PLUGIN_DATA env var (set by Claude Code in hook fire context)
      2. Plugin context detected from script path (CLI invocation via plugin bin/)
      3. CLAUDE_AUDIO_HOOKS_DATA explicit override
      4. Legacy <project_dir>/config/user_preferences.json (script install)

    For plugin contexts, the file is auto-initialized from
    default_preferences.json on first read.
    """
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        target = Path(plugin_data) / "user_preferences.json"
        _auto_init_user_prefs(target)
        return target

    if _is_running_from_plugin():
        target = _resolve_plugin_data_dir() / "user_preferences.json"
        _auto_init_user_prefs(target)
        return target

    explicit = os.environ.get("CLAUDE_AUDIO_HOOKS_DATA")
    if explicit:
        return Path(explicit) / "user_preferences.json"
    return PROJECT_DIR / "config" / "user_preferences.json"


CONFIG_FILE = _resolve_config_file()


def _resolve_queue_dir() -> Path:
    """Resolve the runtime state directory.

    Plugin installs set CLAUDE_PLUGIN_DATA, which is persistent across plugin
    updates. CLAUDE_AUDIO_HOOKS_DATA is an explicit override for any install
    type. Otherwise fall back to the legacy temp dir.
    """
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        return Path(plugin_data) / "queue"
    explicit = os.environ.get("CLAUDE_AUDIO_HOOKS_DATA")
    if explicit:
        return Path(explicit) / "queue"
    return get_safe_temp_dir() / "claude_audio_hooks_queue"


QUEUE_DIR = _resolve_queue_dir()
LOCK_FILE = QUEUE_DIR / "audio.lock"
_queue_dir_ensured = False


def ensure_queue_dir() -> None:
    """Ensure queue directory exists (lazy, called on first use)."""
    global _queue_dir_ensured
    if not _queue_dir_ensured:
        QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        _queue_dir_ensured = True

# Default audio files for each hook type
DEFAULT_AUDIO_FILES = {
    "notification": "notification-urgent.mp3",
    "stop": "task-complete.mp3",
    "pretooluse": "task-starting.mp3",
    "posttooluse": "task-progress.mp3",
    "userpromptsubmit": "prompt-received.mp3",
    "subagent_stop": "subagent-complete.mp3",
    "precompact": "notification-info.mp3",
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
    "worktree_create": "worktree-create.mp3",
    "worktree_remove": "worktree-remove.mp3",
    "elicitation": "elicitation.mp3",
    "elicitation_result": "elicitation-result.mp3",
    # v5.0 hooks (dedicated audio shipped in v5.0.1, generated via ElevenLabs)
    "permission_denied": "permission-denied.mp3",
    "cwd_changed": "cwd-changed.mp3",
    "file_changed": "file-changed.mp3",
    "task_created": "task-created.mp3",
}

# =============================================================================
# CONFIGURATION FUNCTIONS
# =============================================================================

_config_cache: Optional[Dict[str, Any]] = None

def _apply_plugin_option_overlay(config: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay CLAUDE_PLUGIN_OPTION_* env vars onto the loaded config (v5.0).

    The plugin manifest declares userConfig keys (audio_theme, webhook_url,
    webhook_format, tts_enabled). Claude Code exposes them to the hook
    runner as CLAUDE_PLUGIN_OPTION_<KEY> environment variables. This overlay
    lets Claude Code populate plugin config at install time without writing
    to user_preferences.json directly.

    Env vars are case-sensitive lowercase per the Claude Code docs.
    Empty string means "user did not set this — preserve existing value".
    """
    overlays = {
        "CLAUDE_PLUGIN_OPTION_AUDIO_THEME":   ("audio_theme", str),
        "CLAUDE_PLUGIN_OPTION_WEBHOOK_URL":   ("webhook_settings.url", str),
        "CLAUDE_PLUGIN_OPTION_WEBHOOK_FORMAT": ("webhook_settings.format", str),
        "CLAUDE_PLUGIN_OPTION_TTS_ENABLED":   ("tts_settings.enabled", lambda v: v.lower() in ("1", "true", "yes")),
    }
    for env_var, (dotted_key, coerce) in overlays.items():
        raw = os.environ.get(env_var, "").strip()
        if not raw:
            continue
        try:
            value = coerce(raw)
        except (TypeError, ValueError):
            continue
        # Walk into the config and set the dotted key
        parts = dotted_key.split(".")
        cur: Dict[str, Any] = config
        for p in parts[:-1]:
            if not isinstance(cur.get(p), dict):
                cur[p] = {}
            cur = cur[p]
        cur[parts[-1]] = value
        # Auto-enable webhook if a URL was provided via plugin options
        if env_var == "CLAUDE_PLUGIN_OPTION_WEBHOOK_URL" and value:
            config.setdefault("webhook_settings", {})["enabled"] = True
    return config


def load_config() -> Dict[str, Any]:
    """Load configuration from user_preferences.json (cached per invocation)."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    if not CONFIG_FILE.exists():
        log_debug(f"Config file not found: {CONFIG_FILE}")
        _config_cache = _apply_plugin_option_overlay({})
        return _config_cache
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        log_debug(f"Loaded config from {CONFIG_FILE}")
        _config_cache = _apply_plugin_option_overlay(config)
        return _config_cache
    except json.JSONDecodeError as e:
        log_error(f"Invalid JSON in config file: {e}")
        _config_cache = _apply_plugin_option_overlay({})
        return _config_cache
    except PermissionError as e:
        log_error(f"Permission denied reading config: {e}")
        _config_cache = _apply_plugin_option_overlay({})
        return _config_cache
    except OSError as e:
        log_error(f"OS error reading config: {e}")
        _config_cache = _apply_plugin_option_overlay({})
        return _config_cache


def is_hook_enabled(hook_type: str) -> bool:
    """Check if a hook is enabled in configuration."""
    config = load_config()

    # Default enabled hooks (v5.0 adds permission_denied + task_created)
    default_enabled = {"notification", "stop", "subagent_stop", "permission_request",
                       "permission_denied", "task_created"}

    enabled_hooks = config.get("enabled_hooks", {})

    # Check if explicitly set, otherwise use default
    if hook_type in enabled_hooks:
        result = enabled_hooks[hook_type] is True
        log_debug(f"Hook {hook_type} explicitly set to {result}")
        return result

    result = hook_type in default_enabled
    log_debug(f"Hook {hook_type} using default: {result}")
    return result


def is_snoozed() -> bool:
    """Check if hooks are temporarily snoozed via marker file."""
    ensure_queue_dir()
    snooze_file = QUEUE_DIR / "snooze_until"
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
    "session_start_startup": ("session_start", None),
    "session_start_resume":  ("session_start", None),
    "session_start_clear":   ("session_start", None),
    "session_start_compact": ("session_start", "post-compact.mp3"),

    # session_end subtypes (matcher: source)
    "session_end_clear":             ("session_end", None),
    "session_end_resume":            ("session_end", None),
    "session_end_logout":            ("session_end", None),
    "session_end_prompt_input_exit": ("session_end", None),

    # stop_failure subtypes (matcher: error_type)
    "stop_failure_rate_limit":            ("stop_failure", "notification-urgent.mp3"),
    "stop_failure_authentication_failed": ("stop_failure", "notification-urgent.mp3"),
    "stop_failure_billing_error":         ("stop_failure", "tool-failed.mp3"),
    "stop_failure_invalid_request":       ("stop_failure", "tool-failed.mp3"),
    "stop_failure_server_error":          ("stop_failure", "tool-failed.mp3"),
    "stop_failure_max_output_tokens":     ("stop_failure", "tool-failed.mp3"),
    "stop_failure_unknown":               ("stop_failure", None),
    "stop_failure_other":                 ("stop_failure", None),

    # notification subtypes (matcher: notification_type)
    "notification_permission_prompt":  ("notification", "permission-request.mp3"),
    "notification_idle_prompt":        ("notification", "notification-info.mp3"),
    "notification_auth_success":       ("notification", "session-start.mp3"),
    "notification_elicitation_dialog": ("notification", "elicitation.mp3"),

    # precompact / postcompact subtypes (matcher: trigger)
    "precompact_manual": ("precompact", None),
    "precompact_auto":   ("precompact", None),
    "postcompact_manual": ("postcompact", None),
    "postcompact_auto":   ("postcompact", None),
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
    "precompact": "chime-notification-info.mp3",
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
    "worktree_create": "chime-worktree-create.mp3",
    "worktree_remove": "chime-worktree-remove.mp3",
    "elicitation": "chime-elicitation.mp3",
    "elicitation_result": "chime-elicitation-result.mp3",
    # v5.0 hooks (dedicated chimes shipped in v5.0.1, generated via ElevenLabs)
    "permission_denied": "chime-permission-denied.mp3",
    "cwd_changed": "chime-cwd-changed.mp3",
    "file_changed": "chime-file-changed.mp3",
    "task_created": "chime-task-created.mp3",
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
    debounce_file = QUEUE_DIR / f"{hook_type}_last_played"
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


def should_filter(hook_type: str, stdin_data: dict, config: Dict[str, Any]) -> bool:
    """Check user-defined filters. Returns True if hook should be skipped.

    Filters are per-hook regex patterns matched against stdin JSON fields.
    A field ending with '_exclude' inverts the match (skip if pattern matches).
    Otherwise, skip if the pattern does NOT match the field value.
    """
    filters = config.get("filters", {}).get(hook_type, {})
    if not filters:
        return False

    for field, pattern in filters.items():
        if not isinstance(pattern, str) or not pattern:
            continue
        if field.startswith("_"):
            continue  # skip comment keys

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

    # Method 1: Direct PowerShell command with MediaPlayer
    try:
        ps_cmd = (
            'Add-Type -AssemblyName presentationCore; '
            '$p = New-Object System.Windows.Media.MediaPlayer; '
            f'$p.Open("{win_path_escaped}"); '
            'Start-Sleep -Milliseconds 500; '
            '$p.Play(); '
            'Start-Sleep -Seconds 3; '
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
Start-Sleep -Milliseconds 500
$player.Play()
Start-Sleep -Seconds 3
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
        ps_cmd = f'$w = New-Object -ComObject WMPlayer.OCX; $w.URL = "{win_path_escaped}"; Start-Sleep -Seconds 3'
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
Start-Sleep -Milliseconds 500
$player.Play()
Start-Sleep -Seconds 4
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


def _get_tool_detail(stdin_data: dict, max_len: int = 60) -> str:
    """Extract a brief detail string from tool_input (command, file_path, etc.)."""
    tool_input = stdin_data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return ""
    # Try common fields in priority order
    for key in ("command", "file_path", "pattern", "query", "url", "prompt"):
        val = tool_input.get(key, "")
        if val:
            return _truncate(str(val), max_len)
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
            return f"Task completed: {_truncate(str(last_msg), max(max_len * 2, 80))}"
        return "Task completed"
    elif hook_type == "notification":
        msg = stdin_data.get("message", "")
        nt = stdin_data.get("notification_type", "")
        # The notification_type matcher (v5.0) lets us word the alert correctly
        # without re-parsing the free-text message.
        if nt == "idle_prompt":
            base = "Idle prompt"
        elif nt == "auth_success":
            base = "Authentication succeeded"
        elif nt == "elicitation_dialog":
            base = "Elicitation dialog"
        else:
            base = "Authorization needed"
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
            base += f": {_truncate(str(last_msg), max(max_len * 2, 80))}"
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
    elif hook_type == "worktree_create":
        name = stdin_data.get("name", "")
        return "Worktree created" + (f": {name}" if name else "")
    elif hook_type == "worktree_remove":
        wt_path = stdin_data.get("worktree_path", "")
        name = Path(wt_path).name if wt_path else ""
        return "Worktree removed" + (f": {name}" if name else "")
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
    return hook_type.replace("_", " ").title()

# =============================================================================
# DESKTOP NOTIFICATIONS
# =============================================================================

def _escape_notification_string(s: str) -> str:
    """Escape a string for safe use in notification commands."""
    # Remove characters that could cause shell/osascript injection
    return s.replace('"', '\\"').replace("'", "\\'").replace('`', '').replace('$', '')


def send_desktop_notification(title: str, message: str, urgency: str = "normal") -> bool:
    """Send a desktop notification using platform-native methods.

    Args:
        title: Notification title
        message: Notification body text
        urgency: 'normal' or 'critical'

    Returns:
        True if notification was dispatched, False otherwise
    """
    system = platform.system()
    safe_title = _escape_notification_string(title)
    safe_message = _escape_notification_string(message)

    try:
        if system == "Darwin":
            # Audio is handled separately by play_audio_macos() via afplay.
            # Omit "sound name" to avoid double sound and to work on macOS 15+
            # where osascript notifications may be silently blocked.
            script = f'display notification "{safe_message}" with title "{safe_title}"'
            subprocess.Popen(
                ["osascript", "-e", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            log_debug(f"Sent macOS notification: {title} - {message}")
            return True

        elif system == "Linux":
            if is_wsl():
                # WSL: use PowerShell NotifyIcon balloon tip (non-blocking toast)
                ps_cmd = (
                    '[void][System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms"); '
                    '$n = New-Object System.Windows.Forms.NotifyIcon; '
                    '$n.Icon = [System.Drawing.SystemIcons]::Information; '
                    '$n.Visible = $true; '
                    f'$n.ShowBalloonTip(5000, "{safe_title}", "{safe_message}", '
                    f'[System.Windows.Forms.ToolTipIcon]::{"Warning" if urgency == "critical" else "Info"}); '
                    'Start-Sleep -Seconds 6; '
                    '$n.Dispose()'
                )
                subprocess.Popen(
                    ["powershell.exe", "-WindowStyle", "Hidden", "-Command", ps_cmd],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                log_debug(f"Sent WSL balloon notification via PowerShell: {title}")
                return True
            else:
                # Native Linux: use notify-send
                if shutil.which("notify-send"):
                    cmd = ["notify-send"]
                    if urgency == "critical":
                        cmd.extend(["-u", "critical"])
                    cmd.extend([title, message])
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    log_debug(f"Sent Linux notification: {title} - {message}")
                    return True
                else:
                    log_debug("notify-send not found, skipping desktop notification")
                    return False

        elif system == "Windows":
            # Windows: use NotifyIcon balloon tip (non-blocking toast)
            ps_cmd = (
                '[void][System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms"); '
                '$n = New-Object System.Windows.Forms.NotifyIcon; '
                '$n.Icon = [System.Drawing.SystemIcons]::Information; '
                '$n.Visible = $true; '
                f'$n.ShowBalloonTip(5000, "{safe_title}", "{safe_message}", '
                f'[System.Windows.Forms.ToolTipIcon]::{"Warning" if urgency == "critical" else "Info"}); '
                'Start-Sleep -Seconds 6; '
                '$n.Dispose()'
            )
            subprocess.Popen(
                ["powershell.exe", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", ps_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            log_debug(f"Sent Windows balloon notification: {title}")
            return True

    except FileNotFoundError as e:
        log_debug(f"Notification command not found: {e}")
    except Exception as e:
        log_error(f"Desktop notification failed: {e}")

    return False

# =============================================================================
# TEXT-TO-SPEECH
# =============================================================================

def play_tts(message: str) -> bool:
    """Speak a message using platform-native TTS.

    Args:
        message: Text to speak

    Returns:
        True if TTS was dispatched, False otherwise
    """
    system = platform.system()
    # Sanitize message for shell safety
    safe_message = _escape_notification_string(message)

    try:
        if system == "Darwin":
            subprocess.Popen(
                ["say", message],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            log_debug(f"TTS (macOS say): {message}")
            return True

        elif system == "Linux":
            if is_wsl():
                # WSL: use Windows SAPI via PowerShell
                ps_cmd = (
                    'Add-Type -AssemblyName System.Speech; '
                    f'(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak("{safe_message}")'
                )
                subprocess.Popen(
                    ["powershell.exe", "-WindowStyle", "Hidden", "-Command", ps_cmd],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                log_debug(f"TTS (WSL PowerShell): {message}")
                return True
            else:
                # Native Linux: try espeak, then spd-say
                for cmd_name in ["espeak", "spd-say"]:
                    if shutil.which(cmd_name):
                        subprocess.Popen(
                            [cmd_name, message],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                        log_debug(f"TTS (Linux {cmd_name}): {message}")
                        return True
                log_debug("No Linux TTS engine found (espeak, spd-say)")
                return False

        elif system == "Windows":
            ps_cmd = (
                'Add-Type -AssemblyName System.Speech; '
                f'(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak("{safe_message}")'
            )
            subprocess.Popen(
                ["powershell.exe", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", ps_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            log_debug(f"TTS (Windows SAPI): {message}")
            return True

    except FileNotFoundError as e:
        log_debug(f"TTS command not found: {e}")
    except Exception as e:
        log_error(f"TTS failed: {e}")

    return False

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

    # Format payload based on target service
    if fmt == "slack":
        payload: Any = {"text": f"\U0001f514 Claude Code: {context}"}
    elif fmt == "discord":
        payload = {"content": f"\U0001f514 Claude Code: {context}"}
    elif fmt == "teams":
        payload = {"text": f"\U0001f514 Claude Code: {context}"}
    elif fmt == "ntfy":
        # ntfy.sh uses plain text body with header-based metadata
        headers.setdefault("Title", "Claude Code")
        headers.setdefault("Priority", "default")
        headers.setdefault("Tags", "robot")
        payload = context  # plain text
    elif fmt == "raw":
        # v5.0 enriched schema: surface every new stdin field at the top level
        # and tag with a versioned schema string so consumers can pin.
        payload = {
            "schema": "audio-hooks.webhook.v1",
            "version": HOOK_RUNNER_VERSION,
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
            "event_data": {k: v for k, v in stdin_data.items()
                           if k not in ("transcript_path",)},
        }
    else:
        payload = {"text": f"Claude Code: {context}"}

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
# FOCUS FLOW (MICRO-TASK ANTI-DISTRACTION)
# =============================================================================

def start_focus_flow(hook_type: str, config: Dict[str, Any]) -> None:
    """Launch a Focus Flow micro-task when UserPromptSubmit fires.

    Spawns scripts/focus-flow.py in the background, which waits for
    min_thinking_seconds before starting the task. If Claude finishes
    before the delay, stop_focus_flow() deletes the marker file and
    focus-flow.py exits without doing anything.
    """
    if hook_type != "userpromptsubmit":
        return

    ff = config.get("focus_flow", {})
    if not ff.get("enabled"):
        return

    mode = ff.get("mode", "breathing")
    if mode == "disabled":
        return

    min_seconds = ff.get("min_thinking_seconds", 15)
    ensure_queue_dir()
    marker = QUEUE_DIR / "focus_flow_active"

    # Write marker with timestamp
    try:
        marker.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        return

    # Launch the delayed micro-task starter
    launcher = PROJECT_DIR / "scripts" / "focus-flow.py"
    if not launcher.exists():
        log_debug(f"Focus Flow: launcher not found at {launcher}")
        return

    breathing_pattern = ff.get("breathing_pattern", "4-7-8")
    url = ff.get("url", "")
    command = ff.get("command", "")

    try:
        creation_flags = {}
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            creation_flags["creationflags"] = subprocess.CREATE_NO_WINDOW

        subprocess.Popen(
            [sys.executable, str(launcher), mode, str(min_seconds),
             str(marker), url, command, breathing_pattern],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **creation_flags
        )
        log_debug(f"Focus Flow: launcher started (mode={mode}, delay={min_seconds}s)")
    except Exception as e:
        log_debug(f"Focus Flow: failed to start launcher: {e}")


# =============================================================================
# RATE LIMIT PRE-CHECK (v5.0)
# =============================================================================

def check_rate_limits(stdin_data: Dict[str, Any], config: Dict[str, Any]) -> None:
    """Inspect stdin `rate_limits` and play a warning audio when crossing thresholds.

    Side effect only: plays one audio cue per (window, threshold, resets_at)
    tuple, debounced via marker file in QUEUE_DIR. Snooze and the user's
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

    five_thresholds = rl_cfg.get("five_hour_thresholds", [80, 95]) or []
    seven_thresholds = rl_cfg.get("seven_day_thresholds", [80, 95]) or []
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
        except (TypeError, ValueError):
            continue
        # Fire only the highest crossed threshold per call. Each marker is
        # keyed on resets_at so a new reset window can re-fire.
        for threshold in sorted(thresholds, reverse=True):
            try:
                t_int = int(threshold)
            except (TypeError, ValueError):
                continue
            if used_int < t_int:
                continue
            ensure_queue_dir()
            marker = QUEUE_DIR / f"rate_limit_{window_name}_{t_int}_{resets_int}"
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


def stop_focus_flow() -> None:
    """Stop any running Focus Flow micro-task when Claude finishes."""
    ensure_queue_dir()
    marker = QUEUE_DIR / "focus_flow_active"
    pid_file = QUEUE_DIR / "focus_flow_pid"

    # Kill micro-task process if running
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            else:
                os.kill(pid, 15)  # SIGTERM
            log_debug(f"Focus Flow: killed micro-task PID {pid}")
        except (ValueError, OSError, ProcessLookupError):
            pass
        try:
            pid_file.unlink()
        except OSError:
            pass

    # Remove marker (also signals the launcher to exit if still in delay)
    if marker.exists():
        try:
            marker.unlink()
        except OSError:
            pass


# =============================================================================
# MAIN HOOK EXECUTION
# =============================================================================

def run_hook(hook_type: str, stdin_data: dict = None) -> int:
    """
    Main hook execution function.

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
              queue_dir=str(QUEUE_DIR),
              synthetic_variant=_current_synthetic_variant)

    # Check if hook is enabled
    if not is_hook_enabled(hook_type):
        log_trigger(hook_type, "DISABLED")
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

    # Check debounce
    if should_debounce(hook_type):
        log_trigger(hook_type, "DEBOUNCED")
        return 0

    # Check user-defined filters
    if should_filter(hook_type, stdin_data or {}, config):
        log_trigger(hook_type, "FILTERED")
        return 0

    # Auto-update from project directory if a newer version exists
    # (deferred to after enabled/snoozed/debounced/filtered checks for performance)
    check_and_self_update()

    # Focus Flow: start micro-task on prompt submit, stop on completion
    if hook_type == "userpromptsubmit":
        start_focus_flow(hook_type, config)
    elif hook_type in ("stop", "stop_failure"):
        stop_focus_flow()

    # Determine notification mode with per-hook override support
    notification_settings = config.get("notification_settings", {})
    global_mode = notification_settings.get("mode", "audio_only")
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
        notif_sent = send_desktop_notification("Claude Code", context, urgency)
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
            tts_message = _truncate(str(last_msg), max_chars) if last_msg else custom_messages.get(hook_type, context)
        else:
            tts_message = custom_messages.get(hook_type, context)
        tts_sent = play_tts(tts_message)
        if tts_sent:
            log_event("info", "tts_spoken", message=_truncate(tts_message, 100))

    # Webhook (only if enabled)
    if webhook_enabled:
        send_webhook(hook_type, context, stdin_data or {}, config)

    return 0


def main() -> int:
    """Main entry point."""
    # Check Python version
    if sys.version_info < (3, 6):
        print("Error: Python 3.6 or higher is required", file=sys.stderr)
        return 1

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

    # Parse stdin JSON from Claude Code (provides context about the hook event)
    stdin_data = parse_stdin()

    return run_hook(canonical_hook, stdin_data)


if __name__ == "__main__":
    sys.exit(main())
