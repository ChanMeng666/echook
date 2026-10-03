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


def _is_help_flag(tok: str) -> bool:
    """``--help`` / ``-h`` plus the spellings agents and shells reach for next.

    v6.6: ``/?`` (Windows), ``-?`` and ``--help=<anything>`` used to be ordinary
    unknown arguments, so on a state-changing subcommand they were ignored and
    the command ran.
    """
    return tok in ("-h", "--help", "-?", "/?") or tok.startswith("--help=")


def _looks_like_flag(tok: str) -> bool:
    return (tok.startswith("-") and tok != "-") or tok == "/?"


def _check_args(cmd: str, args: List[str], *, flags: Tuple[str, ...] = (),
                valued: Tuple[str, ...] = (), max_positionals: Optional[int] = None) -> Optional[int]:
    """Reject arguments a state-changing subcommand does not define.

    v6.6: these subcommands used to skip anything they did not recognise and
    then act -- ``statusline install --dry-run`` rewrote settings.json,
    ``tts set --bogus x`` wrote a ``bogus`` key. Returns None when the
    arguments are fine, else emits ``INVALID_USAGE`` (nothing was changed) and
    returns the exit code.

    ``valued`` flags consume the next token whatever it looks like. Anything
    else that starts with ``-`` (or is ``/?``) is an unknown flag; a bare token
    is a positional, counted against ``max_positionals`` (None = unlimited).
    The decision is per call site, from that subcommand's real grammar: a
    positional that may legitimately start with ``-`` (the value of
    ``set <key> <value>``) is simply never passed through here.
    """
    accepted = list(flags) + list(valued)
    hint = ("Accepted flags: " + ", ".join(accepted)) if accepted else "This subcommand takes no flags."
    usage_cmd = f"audio-hooks {cmd.split(' ')[0]} --help"
    positionals = 0
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in valued:
            if i + 1 >= len(args):
                return emit_error("INVALID_USAGE", f"{tok} requires a value. Nothing was changed.",
                                  hint=hint, suggested_command=usage_cmd)
            i += 2
            continue
        if tok in flags:
            i += 1
            continue
        if _looks_like_flag(tok):
            return emit_error("INVALID_USAGE", f"Unknown argument for `audio-hooks {cmd}`: {tok}. Nothing was changed.",
                              hint=hint, suggested_command=usage_cmd, unknown_args=[tok])
        positionals += 1
        if max_positionals is not None and positionals > max_positionals:
            return emit_error("INVALID_USAGE", f"Unexpected argument for `audio-hooks {cmd}`: {tok}. Nothing was changed.",
                              hint=hint, suggested_command=usage_cmd, unknown_args=[tok])
        i += 1
    return None


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

PROJECT_VERSION = "6.7.1"

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
    as "installed" made ``install --scripts`` refuse with a remedy that could
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
    file (placed there by scripts/install-complete.sh).

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
    the remedy no longer depends on scripts/uninstall.sh (which the plugin
    layout does not ship and which cannot run under a plain Windows shell).
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


def _log_dir() -> Path:
    """Directory holding events.ndjson. HR.get_log_dir() creates it; a read-only
    invocation must not, so it takes the path without the mkdir."""
    if _READ_ONLY and PROJECT_ROOT is not None:
        return _prefs().log_dir
    return HR.get_log_dir() if HR else Path("/tmp")


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
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
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

# hook_runner.is_hook_enabled's built-in default set. Only these are on when
# enabled_hooks says nothing; subagent_stop is not among them, so an absent
# subagent_stop key is off, not on.
_DEFAULT_ENABLED_HOOKS = ("notification", "stop", "permission_request")


def _check_completion_signal(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Report whether any hook capable of signalling "done" is enabled."""
    enabled_hooks = cfg.get("enabled_hooks") or {}
    if not isinstance(enabled_hooks, dict):
        return {"any_enabled": True, "checked": []}
    on = [
        h for h in _COMPLETION_SIGNAL_HOOKS
        if enabled_hooks.get(h, h in _DEFAULT_ENABLED_HOOKS) is True
    ]
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
    # the mkdir that HR.get_log_dir() performs.
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
# Subcommand: install / uninstall (delegates to existing scripts)
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
    "--scripts": "Legacy script install into ~/.claude (rewrites settings.json). Refused when the plugin is installed unless --force.",
    "--cursor": "Native Cursor hooks: writes ~/.cursor/hooks.json. Refused when the plugin is installed unless --force.",
    "--codex": "Native Codex hooks: writes $CODEX_HOME/hooks.json.",
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
    modes_txt = "|".join(_INSTALL_MODE_FLAGS)
    return {
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


_INSTALL_EXTRA_FLAGS = {"--force": "Override the DUPLICATE_BRIDGE / DUAL_INSTALL_DETECTED refusal (accept double-firing hooks)."}
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
            next_steps=[f"audio-hooks install {f}" for f in _INSTALL_MODE_FLAGS],
        )
    if len(modes) > 1:
        return emit_error(
            "INVALID_USAGE",
            f"install modes are mutually exclusive, got: {', '.join(modes)}. Nothing was changed.",
            suggested_command="audio-hooks install --help",
        )
    if require_project_root() != 0:
        return 1
    mode = modes[0]
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
        return _install_cursor(force=force)
    if mode == "codex":
        return _install_codex()
    # Script install: delegate to existing installer. v6.6: with the plugin
    # present this registers every hook twice, so it needs an explicit --force
    # (same stance as the Cursor path).
    if not force and _detect_install_mode().get("plugin_install"):
        return emit_error(
            "DUAL_INSTALL_DETECTED",
            "The Claude Code audio-hooks plugin is already installed. A script install on top of it would fire every hook twice. Nothing was changed. To switch to the script install deliberately, uninstall the plugin first (`audio-hooks uninstall --plugin` lists the command, which keeps your preferences), or pass --force to install anyway.",
            suggested_command="audio-hooks uninstall --plugin",
        )
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


def _codex_version():
    """Detect the installed Codex CLI version, or None if it cannot be read."""
    exe = shutil.which("codex")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=15
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", out or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


# Codex gained the SessionEnd hook in 0.145.0 (PR #33895). Registering it on an
# older build is not a harmless no-op: the hooks event map is a serde struct
# that rejects unknown keys ("unknown field" / "unexpected map key" sit beside
# the event-name table in the binary, next to "failed to parse hooks config"),
# and a hooks.json that fails to parse takes *every* hook down with it, not
# just the unknown one. So it is injected at install time only when the
# installed Codex is new enough, rather than shipped in the template.
CODEX_SESSION_END_MIN_VERSION = (0, 145, 0)

# SessionEnd runs during thread teardown, which upstream keeps deliberately
# tight: 1s default timeout, capped at 3s, and async hooks forced synchronous
# with a warning. Root threads only -- subagents do not fire it.
CODEX_SESSION_END_ENTRY = {
    "hooks": [{
        "type": "command",
        "command": '{{PYTHON}} "{{HOOK_RUNNER}}" session_end --invoker codex',
        "timeout": 1,
    }],
}


def _install_codex() -> int:
    """Install audio-hooks for Codex CLI via ``$CODEX_HOME/hooks.json``.

    Codex (per learn.chatgpt.com/docs/hooks) does NOT auto-bridge Claude
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

    # SessionEnd is version-gated -- see CODEX_SESSION_END_MIN_VERSION.
    codex_ver = _codex_version()
    session_end_registered = False
    if codex_ver is not None and codex_ver >= CODEX_SESSION_END_MIN_VERSION:
        entry = json.loads(
            json.dumps(CODEX_SESSION_END_ENTRY)
            .replace("{{PYTHON}}", python_bin)
            .replace("{{HOOK_RUNNER}}", hook_runner_for_json)
        )
        new_doc.setdefault("hooks", {})["SessionEnd"] = [entry]
        session_end_registered = True

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
    # in exactly that situation, which ships no scripts/.
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
# Native removal of the legacy script install (v6.6)
# ---------------------------------------------------------------------------
#
# `audio-hooks uninstall` used to shell out to scripts/uninstall.sh, which the
# plugin layout does not ship (PROJECT_ROOT is the plugin directory there, the
# exact situation in which DUAL_INSTALL_DETECTED is reported) and which has no
# Windows implementation. The removal therefore lives here, and scripts/uninstall.sh
# now delegates to it, so there is exactly one implementation and one rule.

# Every hook event install-complete.sh can register (and so uninstall must clear).
LEGACY_HOOK_EVENTS: Tuple[str, ...] = (
    "Notification", "Stop", "StopFailure", "SessionStart", "SessionEnd",
    "SubagentStart", "SubagentStop", "PermissionRequest",
    "PermissionDenied", "TaskCreated", "TaskCompleted", "TeammateIdle",
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "UserPromptSubmit",
    "PreCompact", "PostCompact", "ConfigChange", "InstructionsLoaded",
    "Elicitation", "ElicitationResult", "CwdChanged", "DirectoryAdded",
    "WorktreeRemove", "FileChanged",
    "Setup", "UserPromptExpansion", "PostToolBatch", "MessageDisplay",
)

# Scripts the installers register as hook *commands* (the names a settings.json
# command or a settings.local.json permission may reference).
LEGACY_COMMAND_SCRIPTS: Tuple[str, ...] = (
    "notification_hook.sh", "stop_hook.sh", "pretooluse_hook.sh",
    "posttooluse_hook.sh", "userprompt_hook.sh", "subagent_hook.sh",
    "precompact_hook.sh", "session_start_hook.sh", "session_end_hook.sh",
    "play_audio.sh",   # legacy v1.0
    "hook_runner.py",  # v3.0+ Python runner
)

# Files the installers place in ~/.claude/hooks/ besides the command scripts:
# hook_runner.py imports the two modules at runtime; .project_path records the checkout.
LEGACY_EXTRA_HOOK_FILES: Tuple[str, ...] = ("invoker.py", "user_preferences.py", ".project_path")

# Bytecode of the modules we installed; nothing else in __pycache__ is ours.
LEGACY_INSTALLED_MODULES: Tuple[str, ...] = ("hook_runner", "invoker", "user_preferences")

# --- Ownership by content --------------------------------------------------
# A file name alone proves nothing: `~/.claude/hooks/stop_hook.sh` is an ordinary
# name for a user's own hook, and `shared/` an ordinary directory. A file is
# echook's only when its first 8 KiB also carry a marker taken from every
# historical revision of that file (checked with `git log --all` / `git show`):
#   * the nine wrappers (*_hook.sh): all 9 files, in every revision, contain
#     `source "$SCRIPT_DIR/shared/hook_config.sh"`;
#   * play_audio.sh (v1.0): every revision has the header line
#     `# Claude Code Stop Hook - Play notification audio`;
#   * hook_runner.py: all 44 revisions open with the docstring title
#     `Claude Code Audio Hooks - Python Hook Runner` (30) or `echook - Python Hook Runner` (14);
#   * invoker.py / user_preferences.py: the module docstrings below (one and five revisions);
#   * shared/*.sh: the header comment of each library, across both product names.
_LEGACY_WRAPPER_MARKER = r'source "\$SCRIPT_DIR/shared/hook_config\.sh"'
LEGACY_FILE_MARKERS: Dict[str, str] = {
    **{n: _LEGACY_WRAPPER_MARKER for n in LEGACY_COMMAND_SCRIPTS if n.endswith("_hook.sh")},
    "play_audio.sh": r"^# Claude Code Stop Hook - Play notification audio$",
    "hook_runner.py": r"(?:echook|Claude Code Audio Hooks) - Python Hook Runner",
    "invoker.py": r"Invoker detection for the audio-hooks runner",
    "user_preferences.py": r"single source of truth for user_preferences\.json access",
}
LEGACY_SHARED_MARKERS: Dict[str, str] = {
    "hook_config.sh": r"^# (?:echook|Claude Code Audio Hooks) - Shared Configuration Library$",
    "hook_config_with_path_utils.sh": r"^# (?:echook|Claude Code Audio Hooks) - Shared Configuration Library$",
    "path_utils.sh": r"^# (?:echook|Claude Code Audio Hooks) - Path Utilities$",
    "hook_logger.sh": r"^# Hook Logger - Records all hook triggers for debugging$",
}

# A command or permission is ours only when, after backslashes become forward
# slashes, it references `<home>/.claude/hooks/<known script>`: <home> is one of
# _LEGACY_HOME_SPELLINGS or the actual home directory (case-insensitive on
# Windows); it starts at a word boundary (start of string, whitespace, a quote,
# `=`, `:` or an opening parenthesis); and the script name is not followed by
# more path characters. Another directory's `.claude/hooks` (`node
# D:/proj/.claude/hooks/hook_runner.py`) and a bare substring (`bash
# ~/bin/my_stop_hook.sh`) are NOT ours. Whether the *file* it names is echook's
# is a separate question, decided by content (above).
_LEGACY_HOME_SPELLINGS: Tuple[str, ...] = (r"~", r"\$HOME", r"\$\{HOME\}", r"%USERPROFILE%")
_LEGACY_REF_TEMPLATE = r"""(?:^|[\s"'=:(])(?:%s)/\.claude/hooks/(?:%s)(?![\w.\-/])"""


def _legacy_names_pattern() -> str:
    return "(?P<name>" + "|".join(re.escape(n) for n in LEGACY_COMMAND_SCRIPTS) + ")"


def _legacy_ref_regex() -> "re.Pattern[str]":
    home = str(Path.home()).replace("\\", "/").rstrip("/")
    homes = list(_LEGACY_HOME_SPELLINGS) + ([re.escape(home)] if home else [])
    flags = re.IGNORECASE if platform.system() == "Windows" else 0
    return re.compile(_LEGACY_REF_TEMPLATE % ("|".join(homes), _legacy_names_pattern()), flags)


def _legacy_ref_name(text: Any, rx: Optional["re.Pattern[str]"] = None) -> Optional[str]:
    """The known script a command/permission references (home-anchored), else None."""
    if not isinstance(text, str):
        return None
    m = (rx or _legacy_ref_regex()).search(text.replace("\\", "/"))
    return m.group("name") if m else None


def _is_legacy_script_ref(text: Any, _rx: Optional["re.Pattern[str]"] = None) -> bool:
    return _legacy_ref_name(text, _rx) is not None


def _strip_legacy_hooks(settings: Dict[str, Any],
                        owned: Optional[Callable[[str], bool]] = None,
                        extra_ref: Optional[Callable[[str], Optional[str]]] = None) -> int:
    """Remove only our hook entries from ``settings["hooks"]`` (in place); return the count.

    Per entry, not per group: a group is dropped only when every hook in it was
    ours, an event key only when it had removals and no groups remain, and the
    ``hooks`` object only when removals emptied it. ``owned(name)`` says whether
    the script file an entry names is echook's (or gone); an entry that points at
    a user's own file of the same name is kept.

    ``extra_ref(command)`` (used by ``--remove-unmatched``) names the script of a
    command whose home is spelled in a form the strict rule does not recognise.
    When given, every event key is examined, not only the 30 echook registers.
    """
    removed = 0
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    rx = _legacy_ref_regex()

    def is_ours_entry(h: Any) -> bool:
        if not isinstance(h, dict):
            return False
        cmd = h.get("command")
        name = _legacy_ref_name(cmd, rx)
        if name is None and extra_ref is not None and isinstance(cmd, str):
            name = extra_ref(cmd)
        return name is not None and (owned is None or owned(name))

    events = list(LEGACY_HOOK_EVENTS)
    if extra_ref is not None:
        events += [e for e in hooks if e not in LEGACY_HOOK_EVENTS]
    for event_name in events:
        groups = hooks.get(event_name)
        if not isinstance(groups, list):
            continue
        kept_groups: List[Any] = []
        event_removed = 0
        for group in groups:
            entries = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(entries, list):
                kept_groups.append(group)
                continue
            kept = [h for h in entries if not is_ours_entry(h)]
            gone = len(entries) - len(kept)
            event_removed += gone
            if gone == 0:
                kept_groups.append(group)
            elif kept:
                group = dict(group)  # a copy keeps key order
                group["hooks"] = kept
                kept_groups.append(group)
        if event_removed:
            removed += event_removed
            if kept_groups:
                hooks[event_name] = kept_groups
            else:
                del hooks[event_name]
    if removed and not hooks:
        del settings["hooks"]
    return removed


def _strip_legacy_permissions(settings: Dict[str, Any],
                              owned: Optional[Callable[[str], bool]] = None,
                              extra_ref: Optional[Callable[[str], Optional[str]]] = None) -> int:
    """Remove our entries from ``settings["permissions"]["allow"]`` (in place)."""
    perms = settings.get("permissions")
    if not isinstance(perms, dict) or not isinstance(perms.get("allow"), list):
        return 0
    before = len(perms["allow"])
    rx = _legacy_ref_regex()

    def is_ours_perm(p: Any) -> bool:
        name = _legacy_ref_name(p, rx)
        if name is None and extra_ref is not None and isinstance(p, str):
            name = extra_ref(p)
        return name is not None and (owned is None or owned(name))

    perms["allow"] = [p for p in perms["allow"] if not is_ours_perm(p)]
    return before - len(perms["allow"])


def _read_head(path: Path, limit: int = 8192) -> str:
    with open(path, "rb") as f:
        data = f.read(limit)
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n").lstrip("﻿")


def _has_marker(path: Path, pattern: str) -> bool:
    try:
        return re.search(pattern, _read_head(path), re.MULTILINE) is not None
    except OSError:
        return False


def _marker_state(path: Path, pattern: str) -> Tuple[str, str]:
    """('yes'|'no'|'unreadable', detail) -- an unreadable file is not 'no marker'."""
    try:
        head = _read_head(path)
    except OSError as e:
        return "unreadable", str(e)
    return ("yes" if re.search(pattern, head, re.MULTILINE) else "no"), ""


def _classify_legacy_files(hooks_dir: Path) -> Dict[str, Any]:
    """Split the name-matching files in ~/.claude/hooks into ours and not-ours.

    Returns ``ours`` (paths), ``ours_names`` (set), ``not_ours`` ({path, reason}
    dicts), ``not_ours_names`` (set), ``shared_ours`` (paths in shared/) and
    ``shared_present`` (the shared dir exists).
    """
    ours: List[Path] = []
    not_ours: List[Dict[str, str]] = []
    ours_names: set = set()
    not_ours_names: set = set()

    for name in LEGACY_COMMAND_SCRIPTS + LEGACY_EXTRA_HOOK_FILES:
        p = hooks_dir / name
        if not p.is_file() or name == ".project_path":
            continue
        state, detail = _marker_state(p, LEGACY_FILE_MARKERS[name])
        if state == "yes":
            ours.append(p)
            ours_names.add(name)
        else:
            reason = ("name matches an echook file but its content has no echook marker" if state == "no"
                      else f"name matches an echook file but it could not be read ({detail}); left alone")
            not_ours.append({"path": str(p), "reason": reason})
            not_ours_names.add(name)

    # .project_path holds the checkout path the installers record. It is ours when
    # it is a single path line and sits beside echook's runner, or names a checkout.
    pp = hooks_dir / ".project_path"
    if pp.is_file():
        head = ""
        unreadable = ""
        try:
            head = _read_head(pp, 4096).strip()
        except OSError as e:
            unreadable = str(e)
        single_line = bool(head) and "\n" not in head and "\0" not in head
        points_at_checkout = False
        if single_line:
            try:
                points_at_checkout = (Path(head) / "hooks" / "hook_runner.py").is_file()
            except (OSError, ValueError):
                pass
        if single_line and ("hook_runner.py" in ours_names or points_at_checkout):
            ours.append(pp)
            ours_names.add(".project_path")
        else:
            not_ours.append({"path": str(pp),
                             "reason": (f"could not be read ({unreadable}); left alone" if unreadable else
                                        "not a single-line path beside echook's hook_runner.py or naming an echook checkout")})
            not_ours_names.add(".project_path")

    shared = hooks_dir / "shared"
    shared_ours: List[Path] = []
    if shared.is_dir():
        for name, marker in LEGACY_SHARED_MARKERS.items():
            p = shared / name
            if not p.is_file():
                continue
            state, detail = _marker_state(p, marker)
            if state == "yes":
                shared_ours.append(p)
            else:
                reason = ("name matches an echook library but its content has no echook marker" if state == "no"
                          else f"name matches an echook library but it could not be read ({detail}); left alone")
                not_ours.append({"path": str(p), "reason": reason})
    return {"ours": ours, "ours_names": ours_names, "not_ours": not_ours,
            "not_ours_names": not_ours_names, "shared_ours": shared_ours,
            "shared_present": shared.is_dir()}


_LEGACY_LOOSE_REF_RE = re.compile(
    r"""/\.claude/hooks/%s(?![\w.\-/])""" % _legacy_names_pattern())


def _prefix_segment(norm: str, start: int) -> str:
    """The path-ish word that ends at ``norm[start]`` (the '/' of '/.claude/hooks/…').

    Runs back to the previous whitespace or '(' and drops quotes, then keeps what
    follows the last shell separator, so `"$HOME"`, `true;~`, `true&&~` and
    backtick-~ all reduce to the home spelling and `./` or `$PROJECT/` to a
    relative or foreign one.
    """
    j = start
    while j > 0 and not norm[j - 1].isspace() and norm[j - 1] != "(":
        j -= 1
    segment = norm[j:start].replace('"', "").replace("'", "")
    return re.split(r"[;&|`<>=]", segment)[-1]


def _prefix_denotes_home(prefix: str, name: str, hooks_dir: Path) -> bool:
    """Could ``prefix`` be a spelling of the user's home that the strict rule missed?

    True for a lone `~` (however it is attached to a shell separator),
    environment-variable spellings (`$env:USERPROFILE`, `%HOMEDRIVE%%HOMEPATH%`,
    anything like `$…HOME…`), and an ABSOLUTE path (also an MSYS `/c/Users/…` path)
    that resolves to the very same file as the one in the real hooks directory
    (8.3 short names included). Relative prefixes (`./`, `$PROJECT_DIR/`) and other
    projects' directories are not: they would otherwise be judged against the
    current working directory.
    """
    if prefix == "~":
        return True
    if re.search(r"(?i)\$env:|\$\{?\w*(?:HOME|USERPROFILE)|%\w*(?:HOME|USERPROFILE)", prefix):
        return True
    candidates = []
    if os.path.isabs(prefix) or re.match(r"^[A-Za-z]:[/\\]", prefix):
        candidates.append(prefix)
    m = re.match(r"^/([A-Za-z])/(.*)$", prefix)
    if m:
        candidates.append(f"{m.group(1)}:/{m.group(2)}")
    target = hooks_dir / name
    for cand in candidates:
        try:
            other = Path(cand) / ".claude" / "hooks" / name
            if other.exists() and target.exists() and os.path.samefile(other, target):
                return True
        except (OSError, ValueError):
            continue
    return False


def _unmatched_names(text: str, hooks_dir: Path, not_ours_names: set) -> List[str]:
    """Known scripts that ``text`` references with a home spelling the strict rule
    missed (excluding scripts that are a user's own files)."""
    norm = text.replace("\\", "/")
    names: List[str] = []
    for m in _LEGACY_LOOSE_REF_RE.finditer(norm):
        name = m.group("name")
        if name in not_ours_names:
            continue
        if _prefix_denotes_home(_prefix_segment(norm, m.start()), name, hooks_dir) and name not in names:
            names.append(name)
    return names


def _candidate_strings(doc: Dict[str, Any]):
    """The strings uninstall edits: hook commands (any event) and permissions.allow."""
    hooks = doc.get("hooks")
    if isinstance(hooks, dict):
        for groups in hooks.values():
            for group in groups if isinstance(groups, list) else []:
                entries = group.get("hooks") if isinstance(group, dict) else None
                for h in entries if isinstance(entries, list) else []:
                    if isinstance(h, dict) and isinstance(h.get("command"), str):
                        yield h["command"]
    perms = doc.get("permissions")
    if isinstance(perms, dict) and isinstance(perms.get("allow"), list):
        for p in perms["allow"]:
            if isinstance(p, str):
                yield p


def _unmatched_legacy_references(label: str, doc: Dict[str, Any], hooks_dir: Path,
                                 not_ours_names: set) -> List[Dict[str, Any]]:
    """Registrations that survive stripping because the home is spelled in a form
    the strict rule does not recognise. ``scripts`` carries the full set of script
    names (taken from the untruncated text); ``value`` is only for display."""
    out: List[Dict[str, Any]] = []
    for text in _candidate_strings(doc):
        names = _unmatched_names(text, hooks_dir, not_ours_names)
        if names:
            out.append({"file": label, "value": text[:200], "scripts": names})
    return out


def _encode_json(data: Any) -> bytes:
    """UTF-8, non-ASCII preserved, trailing newline. May raise (RecursionError,
    UnicodeEncodeError for a lone surrogate) -- callers do this BEFORE any write."""
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` atomically, keeping its identity.

    Writes through a symlink to the resolved target (a dotfiles-managed
    settings.json stays a link to the managed file) and keeps the original
    permission bits. The temp file is created 0600 from the start, so a secret-
    bearing file is never briefly readable more widely than the original.
    """
    import stat
    target = Path(os.path.realpath(path))
    tmp = target.with_name(target.name + ".uninstall-tmp")
    try:
        mode = None
        try:
            mode = stat.S_IMODE(os.stat(target).st_mode)
        except OSError:
            pass
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if mode is not None and os.name == "posix":
            os.chmod(tmp, mode)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _uninstall_scripts(*, remove_unmatched: bool = False) -> int:
    """Remove the legacy script install (``~/.claude/hooks`` + its settings entries).

    Order matters: nothing is deleted until the settings edits succeeded, so an
    abort leaves a working install. Both settings files are parsed, edited and
    serialised to bytes before either is written; each write is atomic and keeps
    symlinks and permissions; a file with nothing to remove is not rewritten.
    Only files whose *content* identifies them as echook's are removed, and a
    registration is stripped only if the file it names is echook's or gone. The
    temp lock/queue directory is deliberately left alone: with the plugin also
    installed (the dual-install case this exists for) it holds live state.

    Result: ``ok:true, incomplete:false`` when everything echook put there is gone.
    If something could not be removed, or a registration spells the home in a form
    the strict rule does not recognise (so its script is kept rather than broken),
    the result is ``ok:false`` with error ``UNINSTALL_INCOMPLETE`` and the same
    fields. ``remove_unmatched`` (``--remove-unmatched``) additionally strips those
    registrations and then removes the scripts they pointed at.
    """
    import shutil

    claude_dir = Path.home() / ".claude"
    hooks_dir = claude_dir / "hooks"
    targets = (
        (claude_dir / "settings.json", _strip_legacy_hooks),
        (claude_dir / "settings.local.json", _strip_legacy_permissions),
    )

    # 1. What in ~/.claude/hooks is ours (by content).
    cls = _classify_legacy_files(hooks_dir)
    not_ours_names = cls["not_ours_names"]

    def owned(name: str) -> bool:
        return name not in not_ours_names  # ours, or the file is gone

    def extra_ref(text: str) -> Optional[str]:
        names = _unmatched_names(text, hooks_dir, not_ours_names)
        return names[0] if names else None

    # 2. Parse both settings files first; compute the new contents.
    pending: List[Tuple[Path, Dict[str, Any], int]] = []
    for path, strip in targets:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("top-level JSON value is not an object")
        except Exception as e:  # OSError, ValueError, RecursionError on absurd nesting
            return emit_error(
                "CONFIG_READ_ERROR",
                f"Cannot read {path}: {e}. Nothing was changed.",
                hint="Fix or move the file, then re-run `audio-hooks uninstall`.",
            )
        pending.append((path, data, strip(data, owned, extra_ref if remove_unmatched else None)))
    entries_removed = {p.name: n for p, _d, n in pending}

    # 3. Registrations that survived because the home was spelled in an
    #    unrecognised form. The names come from the untruncated text. Their
    #    script files are kept: deleting a script a registration still points at
    #    would break that hook.
    unmatched: List[Dict[str, Any]] = []
    for path, data, _n in pending:
        unmatched.extend(_unmatched_legacy_references(path.name, data, hooks_dir, not_ours_names))
    blocked_names = {n for u in unmatched for n in u["scripts"]}

    # 4. What gets deleted, and what has to stay so a kept script still runs.
    kept_reason: Dict[Path, str] = {}
    runner_kept = "hook_runner.py" in blocked_names or "hook_runner.py" in not_ours_names
    wrapper_kept = any(n.endswith("_hook.sh") and (n in blocked_names or n in not_ours_names)
                       for n in LEGACY_COMMAND_SCRIPTS)
    files: List[Path] = []
    for f in cls["ours"]:
        if f.name in blocked_names:
            kept_reason[f] = "a registration still points at it"
        elif runner_kept and f.name in ("invoker.py", "user_preferences.py", ".project_path"):
            kept_reason[f] = "needed by the kept hook_runner.py (it imports it / reads it at runtime)"
        else:
            files.append(f)
    shared_ours: List[Path] = cls["shared_ours"]
    if wrapper_kept and shared_ours:
        for f in shared_ours:
            kept_reason[f] = "sourced by a kept *_hook.sh wrapper"
        shared_ours = []
    shared = hooks_dir / "shared"
    pycache = hooks_dir / "__pycache__"
    pyc: List[Path] = []
    if pycache.is_dir():
        removed_or_absent = {f.stem for f in files if f.suffix == ".py"} | {
            m for m in LEGACY_INSTALLED_MODULES if not (hooks_dir / (m + ".py")).exists()}
        for mod in LEGACY_INSTALLED_MODULES:
            if mod in removed_or_absent:
                pyc.extend(sorted(pycache.glob(mod + ".*.pyc")))

    tmp_base = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp")
    left_in_place = [str(p) for p in (tmp_base / "claude_audio_hooks_queue", tmp_base / "claude_audio_hooks.lock")
                     if p.exists()]
    left_in_place.append("config/user_preferences.json and audio/ (preserved)")
    left_in_place.extend(f"{f} (kept: {why})" for f, why in kept_reason.items())
    skipped = list(cls["not_ours"])

    def result(**extra: Any) -> Dict[str, Any]:
        base: Dict[str, Any] = {
            "mode": "scripts",
            "removed_hook_entries": entries_removed.get("settings.json", 0),
            "removed_permissions": entries_removed.get("settings.local.json", 0),
            "removed_files": [], "backup_dir": None,
            "skipped_not_ours": skipped, "unmatched_references": unmatched,
            "left_in_place": left_in_place,
        }
        base.update(extra)
        return base

    def finish(res: Dict[str, Any], failed: List[str]) -> int:
        incomplete = bool(unmatched or failed)
        res["incomplete"] = incomplete
        if not incomplete:
            res["ok"] = True
            emit(res)
            return 0
        problems = []
        steps = []
        if unmatched:
            problems.append(f"{len(unmatched)} registration(s) spell the home directory in a form this command does not "
                            "recognise, so they were left and the script files they point at were kept")
            steps.append("Read unmatched_references (a variable such as $XDG_CONFIG_HOME or %ANDROID_HOME% may belong to "
                         "another tool), then run: audio-hooks uninstall --remove-unmatched")
        if failed:
            problems.append(f"{len(failed)} file(s) could not be deleted")
            steps.append("Close whatever holds the files listed in left_in_place, then run: audio-hooks uninstall")
        res["next_steps"] = steps
        res.pop("ok", None)
        return emit_error("UNINSTALL_INCOMPLETE", "Uninstall is INCOMPLETE: " + "; ".join(problems) + ".",
                          hint="Everything else that was echook's has been removed. Check unmatched_references before using "
                               "--remove-unmatched: it removes every entry listed there and then the scripts they point at.",
                          **{k: v for k, v in res.items() if k != "error"})

    nothing = (not any(n for _p, _d, n in pending)) and not (files or pyc or shared_ours)
    if nothing:
        res = result(nothing_to_remove=not unmatched)
        return finish(res, [])

    # 5. Serialise BEFORE writing anything: encoding failures (a lone surrogate,
    #    absurd nesting) must surface while the install is still untouched.
    payloads: List[Tuple[Path, bytes]] = []
    for path, data, count in pending:
        if count:  # nothing removed -> leave the file byte-for-byte alone
            try:
                payloads.append((path, _encode_json(data)))
            except Exception as e:
                return emit_error("INTERNAL_ERROR",
                                  f"Cannot serialise the edited {path.name}: {e}. Nothing was changed.")

    # 6. Backup: everything that will be changed or deleted (bytecode excepted).
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = claude_dir / "backups" / f"audio-hooks-uninstall-{stamp}"
    n = 1
    while backup_dir.exists():
        n += 1
        backup_dir = claude_dir / "backups" / f"audio-hooks-uninstall-{stamp}-{n}"
    try:
        backup_dir.mkdir(parents=True)
        for path, _d, _n in pending:
            shutil.copy2(path, backup_dir / (path.name + ".backup"))
        if files or shared_ours:
            (backup_dir / "hooks").mkdir()
            for f in files:
                shutil.copy2(f, backup_dir / "hooks" / f.name)
            if shared_ours:
                (backup_dir / "hooks" / "shared").mkdir()
                for f in shared_ours:
                    shutil.copy2(f, backup_dir / "hooks" / "shared" / f.name)
    except OSError as e:
        return emit_error("INTERNAL_ERROR", f"Cannot create backup in {backup_dir}: {e}. Nothing was changed.")

    # 7. Settings edits; on ANY failure restore what was written from the backup.
    written: List[Path] = []
    try:
        for path, payload in payloads:
            _write_bytes_atomic(path, payload)
            written.append(path)
    except Exception as e:
        not_restored: List[str] = []
        for path in written:
            try:
                _write_bytes_atomic(path, (backup_dir / (path.name + ".backup")).read_bytes())
            except Exception:
                not_restored.append(str(path))
        if not_restored:
            return emit_error(
                "INTERNAL_ERROR",
                f"Cannot update settings: {e}. These file(s) were already rewritten and could NOT be restored automatically: "
                f"{', '.join(not_restored)}. The originals are in {backup_dir}; copy them back. No script files were deleted.",
                backup_dir=str(backup_dir))
        return emit_error("INTERNAL_ERROR",
                          f"Cannot update settings: {e}. Settings restored from {backup_dir}; no files were deleted.",
                          backup_dir=str(backup_dir))

    # 8. Only now delete what we installed.
    removed_files: List[str] = []
    failed: List[str] = []
    for f in files + pyc + shared_ours:
        try:
            f.unlink()
            removed_files.append(str(f))
        except OSError:
            failed.append(str(f))
            left_in_place.append(f"{f} (could not be deleted)")
    if shared_ours and shared.is_dir():
        # Only a directory we emptied is removed; a user's shared/ keeps living.
        try:
            shared.rmdir()
            removed_files.append(str(shared))
        except OSError:
            left_in_place.append(f"{shared} (still holds files echook did not install)")
    if pycache.is_dir():
        try:
            pycache.rmdir()  # only succeeds if our bytecode was all that was in it
        except OSError:
            pass
    left_in_place.append(f"{hooks_dir} (the directory itself, and any file echook did not install)")

    res = result(removed_files=removed_files, backup_dir=str(backup_dir),
                 next_steps=["Restart Claude Code so it stops invoking the removed hook scripts"])
    return finish(res, failed)


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
            " uninstall the plugin via `claude plugin uninstall"
            " audio-hooks@chanmeng-audio-hooks --keep-data --json`. (Switching off"
            " 'Include third-party Plugins, Skills, and other configs' in Cursor"
            " Settings stops the bridge in the IDE only; it has no effect on"
            " cursor-agent.)"
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

# Every item ID Codex 0.143 accepts, recovered from the binary's enum + the
# parallel description table. echook curates from this set; it cannot render
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
    # terminal_title only.
    "activity", "app-name",
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
    codex_dir = Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"))
    return codex_dir / "config.toml"


def _cmd_statusline_codex(args: List[str]) -> int:
    """`audio-hooks statusline codex <show|preview|apply>` — curate Codex's fixed
    [tui] arrays (status_line and/or terminal_title) so they stop truncating."""
    action = args[0] if args else "show"
    rest = args[1:]
    if action in ("preview", "apply"):
        rc = _check_args(f"statusline codex {action}", rest,
                         valued=("--preset", "--items", "--target"), max_positionals=0)
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


def _cmd_statusline_subagent(args: List[str]) -> int:
    """Manage `subagentStatusLine` — Claude Code's per-subagent row.

    A second settings key, entirely separate from `statusLine`: it renders one
    row per task in the agent panel instead of one line for the session. Note
    the different output contract — NDJSON keyed by task id, not free text —
    which is why it gets its own renderer rather than reusing the main one.
    """
    settings_path = Path.home() / ".claude" / "settings.json"
    script = PROJECT_ROOT / "bin" / "audio-hooks-subagent-statusline.py"
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


def cmd_statusline(args: List[str]) -> int:
    """Manage the status line: Claude Code registration + segment catalog, and
    Codex [tui].status_line curation."""
    if require_project_root() != 0:
        return 1
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
        return _cmd_statusline_subagent(args[1:])

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
    if sub in ("restore", "prune"):
        rc = _check_args(f"backup {sub}", rest, max_positionals=1 if sub == "restore" else 0)
        if rc is not None:
            return rc
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
    # v6.6: same class of bug as install/uninstall — `upgrade --help` or
    # `upgrade --bogus` used to fall through to the real upgrade, which can
    # uninstall and reinstall the plugin.
    if any(_is_help_flag(a) for a in args):
        emit({
            "ok": True,
            "usage": "audio-hooks upgrade [--check-only] [--force]",
            "flags": {
                "--check-only": "Report whether an upgrade is available; change nothing.",
                "--force": "Upgrade even if the installed version is current.",
            },
        })
        return 0
    unknown = [a for a in args if a not in ("--check-only", "--force")]
    if unknown:
        return emit_error(
            "INVALID_USAGE",
            f"Unknown argument(s) for upgrade: {' '.join(unknown)}. Nothing was changed.",
            suggested_command="audio-hooks upgrade --check-only",
            unknown_args=unknown,
        )
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


# ---------------------------------------------------------------------------
# CLI-level error codes
# ---------------------------------------------------------------------------

# v6.7: codes the CLI emits that hook_runner.ErrorCode does not define -- the
# runner's catalogue covers what a *hook* can report, so the manifest used to
# omit INVALID_USAGE, DUPLICATE_BRIDGE, UNINSTALL_INCOMPLETE and the rest, which
# CLAUDE.md names as the place an agent looks codes up. _build_manifest() merges
# this over HR.ErrorCode. "appears_in" says where the code shows up: "error"
# (the {"ok": false, "error": {"code": ...}} a command returns) or "diagnose"
# (an entry in `audio-hooks diagnose` errors/warnings).
# tests/test_cli_error_codes.py scans this file for every code it can emit and
# fails when one is in neither this table nor HR.ErrorCode.
CLI_ERROR_CODES: Dict[str, Dict[str, str]] = {
    "INVALID_USAGE": {
        "appears_in": "error",
        "hint": "The command was called with an unknown flag, a missing value, a stray argument or a help-like value. Nothing was changed.",
        "suggested_command": "audio-hooks manifest",
    },
    "DUAL_INSTALL_DETECTED": {
        "appears_in": "error, diagnose",
        "hint": "The script install and the Claude Code plugin are both active, so every hook fires twice; or a script install was refused because the plugin is present.",
        "suggested_command": "audio-hooks uninstall",
    },
    "DUPLICATE_BRIDGE": {
        "appears_in": "error, diagnose",
        "hint": "Cursor receives echook both through Claude Code's auto-bridge and through a native ~/.cursor/hooks.json, so events fire twice. `install --cursor` refuses unless --force.",
        "suggested_command": "audio-hooks uninstall --cursor",
    },
    "CURSOR_NOT_FOUND": {
        "appears_in": "error",
        "hint": "~/.cursor/ does not exist, so there is nothing to install the native Cursor hooks into. Install Cursor first.",
        "suggested_command": "audio-hooks status",
    },
    "UNINSTALL_INCOMPLETE": {
        "appears_in": "error",
        "hint": "Uninstall removed what it could but left entries it did not recognise or could not delete; the response lists them. Read unmatched_references before --remove-unmatched.",
        "suggested_command": "audio-hooks uninstall",
    },
    "BACKUP_NOT_FOUND": {
        "appears_in": "error",
        "hint": "No preferences backup has that id.",
        "suggested_command": "audio-hooks backup list",
    },
    "RESTORE_FAILED": {
        "appears_in": "error",
        "hint": "The chosen backup could not be read or restored.",
        "suggested_command": "audio-hooks backup list",
    },
    "NOT_INSTALLED": {
        "appears_in": "error",
        "hint": "upgrade found the audio-hooks plugin in no scope; there is nothing to upgrade.",
        "suggested_command": "audio-hooks install --plugin",
    },
    "PRIOR_UPGRADE_INCOMPLETE": {
        "appears_in": "error",
        "hint": "A previous `upgrade` left its in-progress marker behind; the response carries it. Check state, then retry with --force.",
        "suggested_command": "audio-hooks status",
    },
    "UPGRADE_UNINSTALL_FAILED": {
        "appears_in": "error",
        "hint": "The `claude plugin uninstall` step of upgrade failed; the installed plugin was not replaced.",
        "suggested_command": "audio-hooks upgrade --check-only",
    },
    "UPGRADE_REINSTALL_FAILED": {
        "appears_in": "error",
        "hint": "The `claude plugin install` step of upgrade failed after the uninstall, so the plugin may be absent now. Run the suggested install command.",
        "suggested_command": "audio-hooks install --plugin",
    },
    "UPGRADE_VERIFY_FAILED": {
        "appears_in": "error",
        "hint": "The upgrade ran but the final `claude plugin list` check failed; it may well have completed.",
        "suggested_command": "audio-hooks upgrade --check-only",
    },
    "HOOKS_NOT_REGISTERED": {
        "appears_in": "diagnose",
        "hint": "No hooks block in ~/.claude/settings.json and no plugin install found, so nothing triggers echook.",
        "suggested_command": "audio-hooks install --plugin",
    },
    "NATIVE_NOTIFICATIONS_ACTIVE": {
        "appears_in": "diagnose",
        "hint": "Claude Code's own notification channel is on and signals the same events, so expect a doubled bell or toast. Turn one of the two off.",
        "suggested_command": "audio-hooks diagnose",
    },
    "NO_COMPLETION_SIGNAL": {
        "appears_in": "diagnose",
        "hint": "None of stop, subagent_stop or notification is enabled, so no hook can say a turn finished. echook is healthy but silent for that.",
        "suggested_command": "audio-hooks hooks enable stop",
    },
    "TERMINAL_SEQUENCE_INERT": {
        "appears_in": "diagnose",
        "hint": "notification_settings.terminal_sequence is enabled but Claude Code never emits it from an async hook. Use notification_settings.mode audio_and_notification for a desktop toast.",
        "suggested_command": "audio-hooks set notification_settings.mode audio_and_notification",
    },
    "PREFS_SCHEMA_STALE": {
        "appears_in": "diagnose",
        "hint": "user_preferences.json is stamped with an older version or carries keys this version removed. The next hook event or any state-changing command migrates it; read-only commands (status, diagnose, get) do not. `audio-hooks migrate` does it on demand.",
        "suggested_command": "audio-hooks migrate",
    },
    "STALE_PLUGIN_CACHE": {
        "appears_in": "diagnose",
        "hint": "installed_plugins.json records a version or install path that is not the code now running.",
        "suggested_command": "audio-hooks upgrade",
    },
    "WINDOWS_NO_GIT_BASH": {
        "appears_in": "diagnose",
        "hint": "Windows without Git Bash on PATH: Claude Code runs command hooks through bash and refuses them when it is missing, so every handler fails.",
        "suggested_command": "audio-hooks diagnose",
    },
    "CODEX_HOOKS_DISABLED": {
        "appears_in": "diagnose",
        "hint": "Codex hooks are installed but [features].hooks is false in config.toml, so Codex invokes none of them.",
        "suggested_command": "audio-hooks install --codex",
    },
    "CODEX_CONFIG_PARSE_ERROR": {
        "appears_in": "diagnose",
        "hint": "Codex hooks are installed but config.toml could not be read or parsed. Fix the TOML; hooks are on unless [features].hooks = false.",
        "suggested_command": "audio-hooks diagnose",
    },
    "CODEX_MANAGED_HOOKS_ONLY": {
        "appears_in": "diagnose",
        "hint": "Codex allows managed hooks only, so $CODEX_HOME/hooks.json is ignored and a native --codex install never fires. Ask whoever owns the Codex policy.",
        "suggested_command": "audio-hooks diagnose",
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
            {"name": "install", "args": ["<--plugin|--scripts|--cursor|--codex>", "[--force]", "[--help]"], "description": "Install non-interactively. A mode flag is required (no default); unknown arguments are rejected with INVALID_USAGE and change nothing. --plugin lists the `claude plugin` commands to run. --cursor writes ~/.cursor/hooks.json for Cursor IDE users. --codex writes $CODEX_HOME/hooks.json for Codex CLI users. --scripts is the legacy installer. --force overrides the DUPLICATE_BRIDGE check (--cursor) and the DUAL_INSTALL_DETECTED check (--scripts)."},
            {"name": "uninstall", "args": ["[--plugin|--scripts|--cursor|--codex]", "[--purge]", "[--remove-unmatched]", "[--help]"], "description": "Uninstall non-interactively. Bare `uninstall` (= --scripts) removes the legacy script install natively on every platform: it backs up to ~/.claude/backups/audio-hooks-uninstall-<ts>/, edits settings.json / settings.local.json (only entries that reference ~/.claude/hooks/<known script>), then deletes only the files echook installed; it leaves the temp queue directory alone. Files are judged by content, not name; an incomplete result is ok:false / UNINSTALL_INCOMPLETE and --remove-unmatched finishes it (read unmatched_references first). Unknown arguments are rejected with INVALID_USAGE. --cursor / --codex remove audio-hooks-managed entries from the corresponding hooks.json (--purge also removes the audio-hooks-data directory)."},
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
