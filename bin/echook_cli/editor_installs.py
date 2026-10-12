"""Native hook installs for Cursor and Codex: `install|uninstall --cursor|--codex`.

Each install renders that editor's template from the checkout into the
editor's own hooks.json and tags the entries it wrote; each uninstall removes
only those tagged entries. Mode parsing and the Claude Code paths stay in the
entry file (`cmd_install` / `cmd_uninstall`).
"""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

from .codex_config import _check_codex_feature_flag, _codex_home
from .output import emit, emit_error


def _install_cursor(*, force: bool, project_root: Path, project_version: str,
                    detect_install_mode: Callable[[], Dict[str, Any]]) -> int:
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

    bridged = bool(detect_install_mode().get("plugin_install"))
    if bridged and not force:
        return emit_error(
            "DUPLICATE_BRIDGE",
            "Cursor IDE already auto-bridges the Claude Code audio-hooks plugin. Installing the native Cursor hook on top would fire every event twice. Pass --force to install anyway, or uninstall the Claude Code plugin first.",
            suggested_command="audio-hooks uninstall --plugin",
        )

    template_path = project_root / "cursor-hooks" / "hooks.json"
    if not template_path.exists():
        return emit_error("INTERNAL_ERROR", f"Template not found: {template_path}")

    try:
        template_text = template_path.read_text(encoding="utf-8")
    except Exception as e:
        return emit_error("INTERNAL_ERROR", f"Cannot read template: {e}")

    # Substitute placeholders. {{PYTHON}} -> 'python' on Windows ('python3' fails
    # there because of the Microsoft Store stub); 'python3' on POSIX.
    python_bin = "python" if platform.system() == "Windows" else "python3"
    hook_runner_abs = str((project_root / "hooks" / "hook_runner.py").resolve())
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
        default_prefs = project_root / "config" / "default_preferences.json"
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
                "version": project_version,
                "project_dir": str(project_root),
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


def _install_codex(*, project_root: Path, project_version: str) -> int:
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

    template_path = project_root / "codex-hooks" / "hooks.json"
    if not template_path.exists():
        return emit_error("INTERNAL_ERROR", f"Template not found: {template_path}")

    try:
        template_text = template_path.read_text(encoding="utf-8")
    except Exception as e:
        return emit_error("INTERNAL_ERROR", f"Cannot read template: {e}")

    python_bin = "python" if platform.system() == "Windows" else "python3"
    hook_runner_abs = str((project_root / "hooks" / "hook_runner.py").resolve())
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
        default_prefs = project_root / "config" / "default_preferences.json"
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
                "version": project_version,
                "project_dir": str(project_root),
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
