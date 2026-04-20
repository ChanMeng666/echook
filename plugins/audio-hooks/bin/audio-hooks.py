#!/usr/bin/env python3
"""audio-hooks — single JSON CLI for the claude-code-audio-hooks project.

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
            message="Could not locate the claude-code-audio-hooks project directory.",
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

PROJECT_VERSION = "5.1.2"

# Canonical hook catalogue. Order matches CLAUDE.md and the install scripts.
HOOK_CATALOG: List[Dict[str, Any]] = [
    {"name": "notification",         "default": True,  "audio": "notification-urgent.mp3",   "description": "Authorization or plan confirmation requested"},
    {"name": "stop",                 "default": True,  "audio": "task-complete.mp3",         "description": "Claude finished responding"},
    {"name": "subagent_stop",        "default": True,  "audio": "subagent-complete.mp3",     "description": "Background subagent task done"},
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
    {"name": "worktree_create",      "default": False, "audio": "worktree-create.mp3",       "description": "Worktree created"},
    {"name": "worktree_remove",      "default": False, "audio": "worktree-remove.mp3",       "description": "Worktree removed"},
    {"name": "elicitation",          "default": False, "audio": "elicitation.mp3",           "description": "MCP server requested user input"},
    {"name": "elicitation_result",   "default": False, "audio": "elicitation-result.mp3",    "description": "User responded to MCP elicitation"},
    # New in v5.0 (dedicated audio shipped in v5.0.1, generated via ElevenLabs).
    {"name": "permission_denied",    "default": True,  "audio": "permission-denied.mp3",     "description": "Auto mode classifier denied a tool call (v5.0)"},
    {"name": "cwd_changed",          "default": False, "audio": "cwd-changed.mp3",           "description": "Working directory changed (v5.0)"},
    {"name": "file_changed",         "default": False, "audio": "file-changed.mp3",          "description": "Watched file changed on disk (v5.0)"},
    {"name": "task_created",         "default": True,  "audio": "task-created.mp3",          "description": "Task created via TaskCreate (v5.0)"},
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
            "message": "Both the legacy script install and the plugin install are active. This causes double audio. Run `bash scripts/uninstall.sh --yes` from the project directory to remove the legacy script install.",
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


def _is_running_from_plugin() -> bool:
    """True if this CLI is invoked from a plugin install context."""
    if os.environ.get("CLAUDE_PLUGIN_DATA"):
        return True
    try:
        here = Path(__file__).resolve()
        # bin/audio-hooks.py -> plugin root is parent.parent
        plugin_root = here.parent.parent
        if (plugin_root / ".claude-plugin" / "plugin.json").exists():
            return True
    except Exception:
        pass
    return False


def _resolve_plugin_data_dir() -> Path:
    """Compute the plugin data dir even when CLAUDE_PLUGIN_DATA isn't set."""
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
    return canonical


def _auto_init_user_prefs(target: Path) -> None:
    """Copy default_preferences.json into target if target doesn't exist."""
    if target.exists():
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        template = PROJECT_ROOT / "config" / "default_preferences.json"
        if template.exists():
            import shutil as _sh
            _sh.copy2(str(template), str(target))
    except OSError:
        pass


def _config_path() -> Path:
    """Resolve user_preferences.json path.

    Resolution order:
      1. CLAUDE_PLUGIN_DATA env var (hook fire context).
      2. Plugin context detected from script path (CLI via plugin bin/).
      3. CLAUDE_AUDIO_HOOKS_DATA explicit override.
      4. Legacy <project_dir>/config/user_preferences.json (script install).

    Plugin contexts auto-init from default_preferences.json on first read.
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
    return PROJECT_ROOT / "config" / "user_preferences.json"


def _apply_plugin_option_overlay(config: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay CLAUDE_PLUGIN_OPTION_* env vars onto the loaded config (v5.0.1).

    Mirrors hook_runner._apply_plugin_option_overlay so the CLI sees the same
    effective config as the hook runner. The plugin manifest declares
    userConfig keys; Claude Code exposes them via CLAUDE_PLUGIN_OPTION_<KEY>.
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
        _set_dotted(config, dotted_key, value)
        if env_var == "CLAUDE_PLUGIN_OPTION_WEBHOOK_URL" and value:
            config.setdefault("webhook_settings", {})["enabled"] = True
    return config


def _load_config_raw() -> Dict[str, Any]:
    cp = _config_path()
    if not cp.exists():
        return _apply_plugin_option_overlay({})
    try:
        return _apply_plugin_option_overlay(json.loads(cp.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        return _apply_plugin_option_overlay({})


def _save_config_raw(cfg: Dict[str, Any]) -> Tuple[bool, str]:
    cp = _config_path()
    try:
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return True, ""
    except OSError as e:
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
    if HR is not None:
        return HR.QUEUE_DIR
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

    webhook = cfg.get("webhook_settings", {}) or {}
    tts = cfg.get("tts_settings", {}) or {}
    focus = cfg.get("focus_flow", {}) or {}
    rl = cfg.get("rate_limit_alerts", {}) or {}
    sl = cfg.get("statusline_settings", {}) or {}
    install = _detect_install_mode()

    # Resolve the effective plugin data dir even when CLAUDE_PLUGIN_DATA isn't set
    plugin_data_dir = os.environ.get("CLAUDE_PLUGIN_DATA")
    if not plugin_data_dir and _is_running_from_plugin():
        plugin_data_dir = str(_resolve_plugin_data_dir())

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
        "snooze": _snooze_status(),
        "focus_flow": {
            "enabled": bool(focus.get("enabled")),
            "mode": focus.get("mode", "disabled"),
        },
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
        "statusline": {
            "visible_segments": sl.get("visible_segments", []),
        },
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

def _hooks_state() -> List[Dict[str, Any]]:
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
        })
    return out


def cmd_hooks(args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    if not args:
        return emit_error("INVALID_USAGE", "Usage: audio-hooks hooks <list|enable|disable|enable-only> [name...]")
    sub = args[0]
    rest = args[1:]
    if sub == "list":
        emit({"ok": True, "hooks": _hooks_state()})
        return 0
    if sub in ("enable", "disable"):
        if not rest:
            return emit_error("INVALID_USAGE", f"Usage: audio-hooks hooks {sub} <name>")
        name = rest[0]
        valid = {h["name"] for h in HOOK_CATALOG}
        if name not in valid:
            return emit_error("UNKNOWN_HOOK_TYPE", f"Unknown hook: {name}", hint="Run `audio-hooks hooks list` to see all hooks.", suggested_command="audio-hooks hooks list")
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
        valid = {h["name"] for h in HOOK_CATALOG}
        for n in rest:
            if n not in valid:
                return emit_error("UNKNOWN_HOOK_TYPE", f"Unknown hook: {n}", suggested_command="audio-hooks hooks list")
        cfg = _load_config_raw()
        eh = cfg.setdefault("enabled_hooks", {})
        for h in HOOK_CATALOG:
            eh[h["name"]] = h["name"] in rest
        ok, err = _save_config_raw(cfg)
        if not ok:
            return emit_error("CONFIG_READ_ERROR", err)
        emit({"ok": True, "enabled": list(rest), "disabled": [h["name"] for h in HOOK_CATALOG if h["name"] not in rest]})
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
            "hint": "Both legacy script install and plugin install fire on every event, causing duplicate audio.",
            "suggested_command": "bash scripts/uninstall.sh --yes",
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
    for a in args:
        if a == "--plugin":
            mode = "plugin"
        elif a == "--scripts":
            mode = "scripts"
    if mode == "plugin":
        emit({
            "ok": True,
            "mode": "plugin",
            "next_steps": [
                "Run inside Claude Code: /plugin marketplace add ChanMeng666/claude-code-audio-hooks",
                "Run inside Claude Code: /plugin install audio-hooks@chanmeng-audio-hooks",
                "Verify: audio-hooks status",
            ],
            "hint": "Plugin installation is performed by Claude Code itself; this command only documents the steps.",
        })
        return 0
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


def cmd_uninstall(args: List[str]) -> int:
    if require_project_root() != 0:
        return 1
    mode = "scripts"
    for a in args:
        if a == "--plugin":
            mode = "plugin"
        elif a == "--scripts":
            mode = "scripts"
    if mode == "plugin":
        emit({
            "ok": True,
            "mode": "plugin",
            "next_steps": ["Run inside Claude Code: /plugin uninstall audio-hooks@chanmeng-audio-hooks"],
        })
        return 0
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


def cmd_statusline(args: List[str]) -> int:
    """Manage the Claude Code status line registration."""
    if require_project_root() != 0:
        return 1
    sub = args[0] if args else "show"
    settings_path = Path.home() / ".claude" / "settings.json"

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
                "padding": 1,
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
# Subcommand: manifest (the keystone)
# ---------------------------------------------------------------------------

def _build_manifest_schema() -> Dict[str, Any]:
    """JSON Schema for user_preferences.json."""
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "claude-code-audio-hooks user preferences",
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
                    "queue_enabled": {"type": "boolean"},
                    "max_queue_size": {"type": "integer", "minimum": 1},
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
            "focus_flow": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "mode": {"type": "string", "enum": ["breathing", "hydration", "url", "command", "disabled"]},
                    "min_thinking_seconds": {"type": "integer", "minimum": 0},
                    "url": {"type": "string"},
                    "command": {"type": "string"},
                    "breathing_pattern": {"type": "string"},
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
        "description": "AI-operated audio notification system for Claude Code. Single JSON CLI for every project operation.",
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
            {"name": "install", "args": ["[--plugin|--scripts]"], "description": "Install non-interactively (default: scripts; --plugin documents the plugin install steps)"},
            {"name": "uninstall", "args": ["[--plugin|--scripts]"], "description": "Uninstall non-interactively"},
            {"name": "update", "args": ["[--check]"], "description": "Show current version (real updates go through /plugin update)"},
        ],
        "hooks": HOOK_CATALOG,
        "config_keys": [
            "audio_theme",
            "enabled_hooks.<hook_name>",
            "playback_settings.queue_enabled",
            "playback_settings.debounce_ms",
            "notification_settings.mode",
            "notification_settings.detail_level",
            "notification_settings.per_hook.<hook_name>",
            "filters.<hook_name>.<field_name>",
            "webhook_settings.enabled",
            "webhook_settings.url",
            "webhook_settings.format",
            "webhook_settings.hook_types",
            "tts_settings.enabled",
            "tts_settings.speak_assistant_message",
            "tts_settings.assistant_message_max_chars",
            "rate_limit_alerts.enabled",
            "rate_limit_alerts.five_hour_thresholds",
            "rate_limit_alerts.seven_day_thresholds",
            "focus_flow.enabled",
            "focus_flow.mode",
            "focus_flow.min_thinking_seconds",
            "focus_flow.breathing_pattern",
        ],
        "themes": ["default", "custom"],
        "log_schema": "audio-hooks.v1",
        "webhook_schema": "audio-hooks.webhook.v1",
        "error_codes": error_codes,
        "env_vars": {
            "CLAUDE_PLUGIN_DATA": "Plugin install state directory (auto-set by Claude Code).",
            "CLAUDE_PLUGIN_ROOT": "Plugin install root (auto-set by Claude Code).",
            "CLAUDE_AUDIO_HOOKS_DATA": "Explicit override for state directory.",
            "CLAUDE_AUDIO_HOOKS_PROJECT": "Explicit override for project root.",
            "CLAUDE_HOOKS_DEBUG": "Set to 1 to write debug-level events to the NDJSON log.",
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
