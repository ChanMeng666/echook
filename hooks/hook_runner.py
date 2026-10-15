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
import platform
import re
from pathlib import Path
from typing import Optional, Dict, Any, Tuple


# Ensure the hooks/ dir is importable so `invoker` and the sibling runner
# modules resolve whether this module is invoked directly
# (`python hooks/hook_runner.py`) or loaded via
# importlib.util.spec_from_file_location (used by the test harness).
_HOOKS_DIR = str(Path(__file__).resolve().parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)


def _posix_drive_path_to_windows(path_str: str) -> Tuple[str, Optional[str]]:
    """Rewrite a POSIX-style drive path as ``X:/...``; pure, no logging.

    Returns ``(path, style)`` where style names the form that matched
    ("WSL2", "Cygwin", "Git Bash") or is None when the path is returned as-is.
    Kept above the sibling imports because the script-install bootstrap below
    needs it before any sibling module can be imported.
    """
    # Handle WSL2 style paths: /mnt/c/... -> C:/...
    if path_str.startswith("/mnt/") and len(path_str) >= 6:
        drive_letter = path_str[5].upper()
        if drive_letter.isalpha():
            rest = path_str[6:] if len(path_str) > 6 else "/"
            return f"{drive_letter}:{rest}", "WSL2"

    # Handle Cygwin style paths: /cygdrive/c/... -> C:/...
    if path_str.startswith("/cygdrive/") and len(path_str) >= 11:
        drive_letter = path_str[10].upper()
        if drive_letter.isalpha():
            rest = path_str[11:] if len(path_str) > 11 else "/"
            return f"{drive_letter}:{rest}", "Cygwin"

    # Handle Git Bash/MSYS2 style paths: /d/... -> D:/...
    if len(path_str) >= 2 and path_str[0] == '/' and path_str[1].isalpha():
        drive_letter = path_str[1].upper()
        if len(path_str) == 2:
            return f"{drive_letter}:/", "Git Bash"
        if path_str[2] == '/':
            return f"{drive_letter}:{path_str[2:]}", "Git Bash"
        # Not a drive path, return as-is

    return path_str, None


def _add_checkout_hooks_for_script_install() -> None:
    """Let a pre-7.0 script install find the sibling runner modules.

    Such an install has only ``hook_runner.py``, ``user_preferences.py``,
    ``invoker.py`` and ``.project_path`` in ``~/.claude/hooks/``, and its
    self-update copies nothing but ``hook_runner.py``. The modules this file
    was split into exist only in the checkout ``.project_path`` names, so that
    checkout's ``hooks/`` goes on ``sys.path`` -- directly *after* this
    directory. ``user_preferences`` and ``invoker`` therefore still load from
    the installed copies, exactly as before the split (they decide the data
    directory from their own location, so switching copies could move a
    user's preferences); the checkout supplies them only when the install
    lacks one. Does nothing when the siblings sit beside this file (plugin,
    Cursor/Codex native, in-place run). Never raises.
    """
    try:
        here = Path(_HOOKS_DIR)
        if (here / "runner_core.py").exists():
            return
        project_path_file = here / ".project_path"
        if not project_path_file.exists():
            return
        # Same normalisation get_project_dir() applies: BOM-tolerant, MSYS/WSL/Cygwin forms on Windows.
        recorded = project_path_file.read_text(encoding="utf-8-sig").strip()
        if platform.system() == "Windows":
            recorded = _posix_drive_path_to_windows(recorded)[0]
        if not recorded:
            return
        checkout_hooks = Path(recorded) / "hooks"
        if not (checkout_hooks / "runner_core.py").exists():
            return
        entry = str(checkout_hooks)
        if entry in sys.path:
            return
        position = sys.path.index(_HOOKS_DIR) + 1 if _HOOKS_DIR in sys.path else len(sys.path)
        sys.path.insert(position, entry)
    except Exception:
        pass


_add_checkout_hooks_for_script_install()

from invoker import detect_invoker, strip_invoker_args  # type: ignore  # noqa: E402

# The runner's own modules. runner_core owns the shared state (preferences
# handle, event log, queue dir, invoker lookup); the rest are one concern
# each. Always call through the owning module (`runner_core.log_event(...)`),
# never `from x import name`: one binding per function is what lets a test
# patch it once for every caller.
#
# A fresh load of this file gets fresh copies of them, as it got fresh module
# state when all of this was one file: the test suite and the CLI harness
# re-execute hook_runner.py (and drop `invoker` / `user_preferences`) to reset
# state, and a cached runner_core would keep the previous load's preferences
# singleton and invoker cache. In a hook process nothing is cached yet, so
# this does nothing.
_RUNNER_MODULES = ("runner_core", "platform_shell", "audio_playback",
                   "notification_context", "desktop_notification",
                   "text_to_speech", "webhook_dispatch", "rate_limit_alerts",
                   "terminal_sequence")
for _runner_module in _RUNNER_MODULES:
    sys.modules.pop(_runner_module, None)
import runner_core  # type: ignore  # noqa: E402
import audio_playback  # type: ignore  # noqa: E402
import notification_context  # type: ignore  # noqa: E402
import desktop_notification  # type: ignore  # noqa: E402
import text_to_speech  # type: ignore  # noqa: E402
import webhook_dispatch  # type: ignore  # noqa: E402
import rate_limit_alerts  # type: ignore  # noqa: E402
import terminal_sequence  # type: ignore  # noqa: E402


# Version used for auto-sync: when the installed copy in ~/.claude/hooks/
# detects a newer version in the project directory, it self-updates.
HOOK_RUNNER_VERSION = "7.1.0"


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


# The catalog tables stay in this file; the modules that need one get the same
# object here (see the matching declarations in runner_core / notification_context).
runner_core.RUNNER_VERSION = HOOK_RUNNER_VERSION
runner_core.ERROR_HINTS = _ERROR_HINTS


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
        marker_path = runner_core._prefs().data_dir / "install_marker.json"
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

    runner_core.log_debug(f"normalize_path input: {path_str}")

    result, style = _posix_drive_path_to_windows(path_str)
    if style:
        runner_core.log_debug(f"normalize_path {style}: {path_str} -> {result}")
    return result


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

def _has_project_config(project_dir: Path) -> bool:
    """True when ``project_dir`` looks like an echook checkout (either config file is enough)."""
    # The tracked config/user_preferences.json stays one more release: a script install's old runner
    # tests for it on the first hook after `git pull`, before it self-updates. Delete it one release after 7.0.0.
    config_dir = project_dir / "config"
    return (config_dir / "user_preferences.json").exists() or (config_dir / "default_preferences.json").exists()


def get_project_dir() -> Path:
    """Determine the project directory."""
    script_dir = Path(__file__).resolve().parent
    runner_core.log_debug(f"Script dir: {script_dir}")

    # Strategy 1: Read from .project_path file
    project_path_file = script_dir / ".project_path"
    if project_path_file.exists():
        try:
            recorded_path = project_path_file.read_text(encoding="utf-8-sig").strip()  # utf-8-sig handles BOM
            runner_core.log_debug(f"Read .project_path: {recorded_path}")
            # Normalize path format for Windows compatibility
            recorded_path = normalize_path(recorded_path)
            recorded_path_obj = Path(recorded_path)
            if recorded_path_obj.exists() and _has_project_config(recorded_path_obj):
                runner_core.log_debug(f"Using project dir from .project_path: {recorded_path_obj}")
                return recorded_path_obj
            else:
                runner_core.log_debug(f"Project path invalid or config missing: {recorded_path_obj}")
        except Exception as e:
            runner_core.log_error(f"Failed to read .project_path: {e}")

    # Strategy 2: Check if we're in the project structure
    candidate = script_dir.parent
    if _has_project_config(candidate):
        runner_core.log_debug(f"Using parent dir as project dir: {candidate}")
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
        if loc.exists() and _has_project_config(loc):
            runner_core.log_debug(f"Found project in common location: {loc}")
            return loc

    # Fallback
    runner_core.log_debug(f"Using fallback project dir: {candidate}")
    return candidate


# Initialize paths
PROJECT_DIR = get_project_dir()
AUDIO_DIR = PROJECT_DIR / "audio"
# From here on runner_core._prefs() works; until this line it raises, which is
# what keeps the log calls made while resolving the directory the no-ops they
# were when PROJECT_DIR was a not-yet-bound global of this module.
runner_core.set_project_dir(PROJECT_DIR)


# The hooks that are on when ``enabled_hooks`` says nothing about them (rule 5
# of is_hook_enabled). The only definition: bin/audio-hooks.py reads this one.
DEFAULT_ENABLED_HOOKS = frozenset({"notification", "stop", "permission_request"})

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
        _config_cache = runner_core._prefs().load()
        runner_core.log_debug(f"Loaded config from {runner_core._prefs().config_path}")
    except Exception as e:
        runner_core.log_error(f"Could not load config: {e}")
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
    enabled_hooks = config.get("enabled_hooks", {})
    result, rule = resolve_hook_enabled(enabled_hooks, hook_type, variant)
    if rule == 1:
        runner_core.log_debug(f"Hook {hook_type} variant {variant} explicitly set to {result}")
    elif rule == 2:
        runner_core.log_debug(f"Hook {hook_type} explicitly disabled (variant={variant})")
    elif rule == 3:
        runner_core.log_debug(f"Hook {hook_type} variant {variant} using variant default: {result}")
    elif rule == 4:
        runner_core.log_debug(f"Hook {hook_type} explicitly set to {result}")
    else:
        runner_core.log_debug(f"Hook {hook_type} using default: {result}")
    return result


def resolve_hook_enabled(enabled_hooks: Any, hook_type: str,
                         variant: Optional[str] = None) -> Tuple[bool, int]:
    """The gating decision itself: ``(enabled, rule)``, no I/O and no logging.

    ``rule`` is the number of the precedence rule in :func:`is_hook_enabled`
    that decided. ``enabled_hooks`` is the mapping as stored; a value that is
    not the boolean ``True`` counts as off wherever a key is consulted, so a
    stored ``"yes"`` or ``1`` disables. The one implementation of the rule:
    ``is_hook_enabled`` calls it with the config it loads, and
    ``bin/audio-hooks.py`` calls it for ``hooks list`` and ``status`` with the
    config it loaded read-only, so the CLI reports what the runner will do.
    """
    # 1. Explicit per-variant override wins outright.
    if variant and variant in enabled_hooks:
        return enabled_hooks[variant] is True, 1

    # 2. A parent switched off silences every variant under it.
    if enabled_hooks.get(hook_type) is False:
        return False, 2

    # 3. Variant-specific default, for opt-in variants of an on-by-default parent.
    if variant and variant in SYNTHETIC_VARIANT_DEFAULTS:
        return SYNTHETIC_VARIANT_DEFAULTS[variant], 3

    # 4. Explicit parent value (the True case; False was handled by rule 2).
    if hook_type in enabled_hooks:
        return enabled_hooks[hook_type] is True, 4

    # 5. Built-in default.
    return hook_type in DEFAULT_ENABLED_HOOKS, 5


def read_snooze_until(queue_dir: Path) -> Tuple[Optional[float], Optional[Exception]]:
    """Read ``<queue_dir>/snooze_until`` without creating anything.

    Returns ``(until, error)``: ``(None, None)`` when there is no marker,
    ``(timestamp, None)`` when it parses, ``(None, error)`` when it exists but
    cannot be read or parsed. Hooks are snoozed while ``time.time() < until``.
    Shared with ``bin/audio-hooks.py``, whose reporting commands must not
    create the queue directory.
    """
    snooze_file = Path(queue_dir) / "snooze_until"
    if not snooze_file.exists():
        return None, None
    try:
        return float(snooze_file.read_text(encoding="utf-8").strip()), None
    except (ValueError, OSError) as e:
        return None, e


def is_snoozed() -> bool:
    """Check if hooks are temporarily snoozed via marker file."""
    runner_core.ensure_queue_dir()
    snooze_until, error = read_snooze_until(runner_core._prefs().queue_dir)
    if error is not None:
        runner_core.log_debug(f"Error reading snooze file: {error}")
        return False
    if snooze_until is None:
        return False
    if time.time() < snooze_until:
        remaining = snooze_until - time.time()
        runner_core.log_debug(f"Snoozed: {remaining:.0f}s remaining")
        return True
    else:
        runner_core.log_debug("Snooze expired")
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
notification_context.NOTIFICATION_TYPE_LABELS = NOTIFICATION_TYPE_LABELS


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
                runner_core.log_event("debug", "audio_override_resolved",
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
            runner_core.log_debug(f"Audio file for {hook_type} (override): {override_path}")
            return override_path

    # 2. Use audio_theme setting
    if theme == "custom":
        custom_file = CUSTOM_AUDIO_FILES.get(hook_type, default_file)
        theme_path = AUDIO_DIR / "custom" / custom_file
    else:
        theme_path = AUDIO_DIR / "default" / default_file

    if theme_path.exists():
        runner_core.log_debug(f"Audio file for {hook_type} (theme={theme}): {theme_path}")
        return theme_path

    # 3. Fallback to default
    fallback_path = AUDIO_DIR / "default" / default_file
    if fallback_path.exists():
        runner_core.log_debug(f"Using fallback audio for {hook_type}: {fallback_path}")
        return fallback_path

    runner_core.log_debug(f"No audio file found for {hook_type}")
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
    runner_core.ensure_queue_dir()
    debounce_file = runner_core._prefs().queue_dir / f"{hook_type}_last_played"
    debounce_sec = get_debounce_ms() / 1000.0

    current_time = time.time()

    if debounce_file.exists():
        try:
            last_time = float(debounce_file.read_text(encoding="utf-8").strip())
            if current_time - last_time < debounce_sec:
                runner_core.log_debug(f"Debouncing {hook_type}: {current_time - last_time:.2f}s < {debounce_sec}s")
                return True
        except (ValueError, OSError) as e:
            runner_core.log_debug(f"Error reading debounce file: {e}")

    # Update debounce timestamp
    try:
        debounce_file.write_text(str(current_time), encoding="utf-8")
    except OSError as e:
        runner_core.log_error(f"Failed to write debounce file: {e}")

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
                runner_core.log_debug(f"Filter: {hook_type} skipped — {running} background task(s) still in flight")
                return True

    # v6.6: separate opt-in for scheduled wakeups. ``session_crons`` lists the
    # session's CronCreate / ScheduleWakeup / /loop entries; a session with a
    # recurring cron always has one, so folding this into the key above would
    # silence every turn of such a session for users who never asked for that.
    if filters.get("skip_if_session_crons_scheduled") is True:
        crons = stdin_data.get("session_crons")
        if isinstance(crons, list) and crons:
            runner_core.log_debug(f"Filter: {hook_type} skipped — {len(crons)} session cron(s) scheduled")
            return True

    # Opt-in: Cursor documents its native ``stop`` input as
    # ``{"status": "completed" | "aborted" | "error", "loop_count": 0}``
    # (cursor.com/docs/hooks, "stop"; ``subagentStop`` documents the same three
    # values). A turn the user cancelled is not one they need to be told has
    # finished. Only the exact string "aborted" counts: an absent field (Claude
    # Code's Stop has none) or any other value never suppresses. Not scoped to
    # an invoker -- like its siblings it keys on what the payload carries, and
    # it does nothing unless the user turned it on. No live Cursor payload was
    # captured when this was written; the basis is the documentation alone.
    if filters.get("skip_if_aborted") is True:
        if stdin_data.get("status") == "aborted":
            runner_core.log_debug(f"Filter: {hook_type} skipped — status is aborted")
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
                runner_core.log_debug(
                    f"Filter: {hook_type} skipped — took {actual:.0f}ms, "
                    f"under the {threshold:.0f}ms threshold"
                )
                return True

    for field, pattern in filters.items():
        if not isinstance(pattern, str) or not pattern:
            continue
        if field.startswith("_"):
            continue  # skip comment keys
        if field in ("skip_if_background_tasks_running", "skip_if_session_crons_scheduled",
                     "skip_if_aborted", "error_as_stop_failure", "min_duration_ms"):
            continue  # reserved non-regex filters, handled above

        try:
            if field.endswith("_exclude"):
                real_field = field[:-8]
                value = str(stdin_data.get(real_field, ""))
                if value and re.search(pattern, value):
                    runner_core.log_debug(f"Filter: {hook_type} excluded by {real_field} matching '{pattern}'")
                    return True
            else:
                value = str(stdin_data.get(field, ""))
                if value and not re.search(pattern, value):
                    runner_core.log_debug(f"Filter: {hook_type} skipped — {field}='{value}' doesn't match '{pattern}'")
                    return True
        except re.error as e:
            runner_core.log_debug(f"Filter regex error for {field}: {e}")

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
            runner_core.log_debug(f"Parsed stdin JSON: {list(data.keys()) if isinstance(data, dict) else type(data)}")
            return data if isinstance(data, dict) else {}
    except json.JSONDecodeError as e:
        runner_core.log_debug(f"stdin was not valid JSON: {e}")
    except Exception as e:
        runner_core.log_debug(f"Failed to read stdin: {e}")
    return {}


# =============================================================================
# MAIN HOOK EXECUTION
# =============================================================================

def _is_cursor_stop_error(hook_type: str, stdin_data: Any) -> bool:
    """True for a Cursor ``stop`` whose payload says the agent loop errored.

    Cursor documents the native ``stop`` input as ``{"status": "completed" |
    "aborted" | "error", "loop_count": 0}`` (cursor.com/docs/hooks). Only the
    exact string "error" qualifies; an absent field or any other value is an
    ordinary stop.

    Scoped to the Cursor invoker because that is the only editor whose
    documentation gives a top-level ``status`` on ``stop`` this meaning. Claude
    Code's Stop payload has no such field and reports failures through its own
    StopFailure event; nothing establishes what a ``status`` would mean on a
    Codex Stop. Cursor's native path and its Claude-Code-plugin auto-bridge
    both report as "cursor"; the third-party-hooks page does not say whether a
    bridged Stop carries ``status``, and nothing here assumes it does -- when
    the field is absent this returns False.

    No live Cursor payload was captured when this was written.
    """
    return (
        hook_type == "stop"
        and runner_core._get_invoker() == "cursor"
        and isinstance(stdin_data, dict)
        and stdin_data.get("status") == "error"
    )


def _stop_error_reroute_enabled(config: Any) -> bool:
    """True when ``filters.stop.error_as_stop_failure`` is exactly ``true``.

    The key lives in ``filters.stop`` beside the other opt-in booleans that
    read the Stop payload (``skip_if_background_tasks_running``,
    ``skip_if_session_crons_scheduled``, ``skip_if_aborted``): it is the one
    block that holds per-event behaviour switches, accepts keys the template
    does not list, and survives migration. Absent means off. Tolerates a
    malformed config (non-dict at any level) by answering False.
    """
    if not isinstance(config, dict):
        return False
    filters = config.get("filters")
    if not isinstance(filters, dict):
        return False
    stop_filters = filters.get("stop")
    return isinstance(stop_filters, dict) and stop_filters.get("error_as_stop_failure") is True


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
    runner_core._set_log_context(sid, hook_type)
    runner_core.log_event("debug", "hook_start",
              project_dir=str(PROJECT_DIR),
              audio_dir=str(AUDIO_DIR),
              queue_dir=str(runner_core._prefs().queue_dir),
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
    if runner_core._get_invoker() == "cursor" and hook_type in _CURSOR_UNSUPPORTED:
        runner_core.log_event("debug", "skipped_no_cursor_equivalent", hook=hook_type)
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
    if runner_core._get_invoker() == "codex" and hook_type in _CODEX_UNSUPPORTED:
        runner_core.log_event("debug", "skipped_no_codex_equivalent", hook=hook_type)
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
        and runner_core._get_invoker() == "claude-code"
        and isinstance(stdin_data, dict)
        and stdin_data.get("agent_type") == ""
    ):
        runner_core.log_event("debug", "skipped_internal_subagent", hook=hook_type, agent_type="")
        return 0

    # v5.1.6: when ``audio-hooks install --cursor --force`` was used to install
    # the Cursor-native hooks on top of an already-active Claude Code plugin
    # bridge, both paths fire on every event and audio plays twice. The marker
    # file records ``duplicate_bridge_forced: true``; under Cursor we treat it
    # as a runtime opt-out for the native path so Claude Code's bridge handles
    # the event alone. Operators who want both paths active should remove the
    # marker; ``audio-hooks status`` already warns them they are in this state.
    if runner_core._get_invoker() == "cursor" and _read_install_marker().get("duplicate_bridge_forced") is True:
        meta = _ERROR_HINTS.get(ErrorCode.DUPLICATE_BRIDGE_RUNTIME_SKIP, {})
        runner_core.log_event(
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

    # A Cursor ``stop`` whose documented ``status`` is "error" is delivered as
    # ``stop_failure`` -- only for a user who set
    # ``filters.stop.error_as_stop_failure`` to true. The key is the whole
    # opt-in: whether ``stop_failure`` happens to be enabled does not trigger
    # this (it may be on for Claude Code's API errors, or left on as the parent
    # of a variant by ``hooks enable-only stop_failure_rate_limit``), so without
    # the key every configuration behaves exactly as it did before.
    # From here on the event *is* stop_failure: its switch, its filters, its
    # debounce window, its sound, its per-hook mode, and the ``hook_type`` a
    # webhook consumer sees (the original ``status`` stays in the webhook's
    # ``event_data``). It carries no variant, so the plain ``stop_failure``
    # switch decides: with that off the errored turn is silent (DISABLED) and
    # does not fall back to ``stop`` -- a fallback would make what plays depend
    # on a second switch again, which is the coupling this key removed.
    rerouted_stop_error = False
    if _is_cursor_stop_error(hook_type, stdin_data) and _stop_error_reroute_enabled(load_config()):
        runner_core.log_event("debug", "stop_rerouted_to_stop_failure", hook="stop_failure",
                  rerouted_from="stop", status="error")
        hook_type = "stop_failure"
        variant = None
        rerouted_stop_error = True
        runner_core._set_log_context(sid, hook_type)

    # Check if hook is enabled
    if not is_hook_enabled(hook_type, variant):
        runner_core.log_trigger(hook_type, "DISABLED", variant or "")
        return 0

    # Check if snoozed
    if is_snoozed():
        runner_core.log_trigger(hook_type, "SNOOZED")
        return 0

    # Load config once for notification/TTS/filter/webhook settings
    # (loaded before debounce so rate_limit_alerts.check_rate_limits can use it)
    config = load_config()

    # Rate-limit pre-check (v5.0): inspect stdin `rate_limits` and play a one-shot
    # warning audio when crossing thresholds. Runs before debounce so a chatty
    # PreToolUse stream doesn't suppress the alert.
    rate_limit_alerts.check_rate_limits(stdin_data or {}, config)

    # Check user-defined filters. This must run before the debounce check:
    # should_debounce() stamps the debounce file whenever it lets an event
    # through, so an event discarded here afterwards would still have opened the
    # window and suppressed the next genuine event of its kind (v6.7).
    if should_filter(hook_type, stdin_data or {}, config):
        runner_core.log_trigger(hook_type, "FILTERED")
        return 0

    # Check debounce -- last gate, so only an event that will be delivered
    # stamps the window.
    if should_debounce(hook_type):
        runner_core.log_trigger(hook_type, "DEBOUNCED")
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
        runner_core.log_debug(f"Invalid per_hook mode '{mode}' for {hook_type}, falling back to '{global_mode}'")
        mode = global_mode
    runner_core.log_debug(f"Notification mode for {hook_type}: {mode} (global={global_mode})")

    # Get detail level for context messages
    detail_level = notification_settings.get("detail_level", "standard")
    if detail_level not in ("minimal", "standard", "verbose"):
        detail_level = "standard"

    # Play audio (unless mode is notification_only or disabled)
    if mode in ("audio_only", "audio_and_notification"):
        audio_file = get_audio_file(hook_type)
        if not audio_file:
            runner_core.log_trigger(hook_type, "NO_AUDIO_CONFIG")
        elif not audio_file.exists():
            runner_core.log_trigger(hook_type, "FILE_NOT_FOUND", str(audio_file))
            runner_core.log_error(f"Audio file not found: {audio_file}")
        else:
            success = audio_playback.play_audio(audio_file)
            if success:
                runner_core.log_trigger(hook_type, "PLAYED", audio_file.name)
            else:
                runner_core.log_trigger(hook_type, "PLAY_FAILED", audio_file.name)
                runner_core.log_error(f"Failed to play audio: {audio_file}")
    elif mode == "notification_only":
        runner_core.log_trigger(hook_type, "AUDIO_SKIPPED", f"mode={mode}")
    elif mode == "disabled":
        runner_core.log_trigger(hook_type, "AUDIO_SKIPPED", "mode=disabled")

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
    context = notification_context.get_notification_context(hook_type, stdin_data or {}, detail_level) if needs_context else ""
    if needs_context:
        context += notification_context._format_context_suffix(stdin_data or {}, detail_level)

    # Desktop notification (unless mode is audio_only or disabled)
    if mode in ("notification_only", "audio_and_notification"):
        urgency = "critical" if hook_type in ("notification", "permission_request", "posttoolusefailure", "stop_failure", "elicitation") else "normal"
        notif_sent = desktop_notification.send_desktop_notification(runner_core._invoker_display_name(), context, urgency)
        if notif_sent:
            runner_core.log_debug(f"Desktop notification sent for {hook_type}: {context}")
    elif mode == "disabled":
        runner_core.log_trigger(hook_type, "NOTIFICATION_SKIPPED", "mode=disabled")

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
            spoken = notification_context._clean_for_output(str(last_msg), max_chars, for_speech=True) if last_msg else ""
            tts_message = spoken if spoken else custom_messages.get(hook_type, context)
        elif rerouted_stop_error:
            # ``messages.stop_failure`` -- the shipped "API error occurred" or a
            # user's own wording -- was written for Claude Code's API errors.
            # A Cursor turn that ended in an error is not known to be one, so
            # it always speaks its own context text.
            tts_message = context
        else:
            tts_message = custom_messages.get(hook_type, context)
        tts_sent = text_to_speech.play_tts(tts_message)
        if tts_sent:
            runner_core.log_event("info", "tts_spoken", message=notification_context._truncate(tts_message, 100))

    # Webhook (only if enabled)
    if webhook_enabled:
        webhook_dispatch.send_webhook(hook_type, context, stdin_data or {}, config)

    # terminalSequence — last, and the only thing in this runner that writes to
    # stdout. Gated by an event allowlist; see terminal_sequence.emit_terminal_sequence.
    if terminal_sequence.emit_terminal_sequence(hook_type, context, config):
        runner_core.log_event("info", "terminal_sequence_emitted", hook=hook_type)

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
    runner_core._get_invoker()
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

    runner_core.log_debug(f"Hook runner started: {raw_arg} (canonical={canonical_hook})")
    runner_core.log_debug(f"Python version: {sys.version}")
    runner_core.log_debug(f"Platform: {platform.system()} {platform.release()}")

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
            data_dir = runner_core._prefs().data_dir
            sys.stdout.write(json.dumps({"env": {"CLAUDE_PLUGIN_DATA": str(data_dir)}}) + "\n")
            sys.stdout.flush()
        except Exception:
            pass  # never fatal — UserPreferences.data_dir is the runtime backstop

    # Parse stdin JSON from Claude Code (provides context about the hook event)
    stdin_data = parse_stdin()

    return run_hook(canonical_hook, stdin_data, variant_label)


if __name__ == "__main__":
    sys.exit(main())
