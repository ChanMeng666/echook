"""echook hook runner: the process state every other runner module depends on.

The preferences handle, the NDJSON event log, the queue directory and the
invoker lookup. Owns their module-level state (log context, queue-dir flag,
project dir); every other module reads it through this one, never by copy.
Imports nothing from its sibling runner modules.
"""

import json
import os
import time
from pathlib import Path
from typing import Optional, Dict, Any

from user_preferences import UserPreferences, get_prefs  # type: ignore
from invoker import get_invoker as _get_invoker  # type: ignore


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


# Bound once by hook_runner.py, which must keep the tables it comes from
# (ErrorCode, HOOK_RUNNER_VERSION) and resolves the project directory from its
# own install location. Same objects, handed over at import; never rebound.
ERROR_HINTS: Dict[str, Dict[str, str]] = {}
RUNNER_VERSION: str = ""
PROJECT_DIR: Optional[Path] = None
AUDIO_DIR: Optional[Path] = None


def set_project_dir(project_dir: Path) -> None:
    """Record the project directory hook_runner resolved (and its audio dir)."""
    global PROJECT_DIR, AUDIO_DIR
    PROJECT_DIR = project_dir
    AUDIO_DIR = project_dir / "audio"


# _get_invoker lives in invoker.py (imported above) so user_preferences.py can
# ask the same question without a circular import. When the runner is launched
# with ``--invoker codex`` (the form Codex's hooks.json template uses), it
# answers ``"codex"`` from argv parsing before any env-var checks. Every runner
# module calls it through this module, so one patch point covers them all.


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
    if PROJECT_DIR is None:
        # hook_runner logs while it is still resolving the project directory.
        # Those calls have always been no-ops (log_event swallows the error);
        # raising keeps them so instead of anchoring the singleton at a guess.
        raise RuntimeError("project directory not resolved yet")
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
    meta = ERROR_HINTS.get(code, {})
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
# call sites (and any third-party patches) keep working.
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
