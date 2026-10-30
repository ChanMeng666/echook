"""`audio-hooks upgrade`: refresh the Claude Code plugin without losing the user's config.

Drives ``claude plugin update`` (falling back to uninstall --keep-data +
install), guarded by an in-progress marker, then lets the preferences migrate.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

from . import statusline
from .output import _is_help_flag, emit, emit_error

# Listed in `audio-hooks manifest` for `upgrade`.
UPGRADE_FLAGS = ("--check-only", "--force")


def cmd_upgrade(args: List[str], *, project_root: Path, project_version: str,
                require_project_root: Callable[[], int],
                prefs_factory: Callable[[], Any]) -> int:
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
    unknown = [a for a in args if a not in UPGRADE_FLAGS]
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
            "would_upgrade": current_version != project_version,
            "target_version": project_version,
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
        "recovery_command": f"python {project_root}/bin/audio-hooks.py upgrade --force",
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
        prefs = prefs_factory()
        cfg = prefs.load()
        migration_info = {"current_version": cfg.get("_version")}
    except Exception as e:
        migration_info = {"warning": f"migration_skipped: {e}"}

    # 9. A status line registered by version would stop when that folder goes
    try:
        repointed = statusline.repoint_cached_registrations(Path.home() / ".claude" / "settings.json")
    except OSError:
        repointed = []

    emit({
        "ok": True,
        "from_version": current_version,
        "to_version": new_version,
        "scope": current_scope,
        "data_preserved": True,
        "via": used_path,
        "config": migration_info,
        "status_line_repointed": repointed,
    })
    return 0
