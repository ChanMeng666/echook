"""`audio-hooks backup <list|show|restore|prune>`: the saved copies of user_preferences.json.

A thin JSON front for the backup methods of ``UserPreferences``; the entry
file passes in the function that returns that object.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, List

from .output import _check_args, emit, emit_error


def cmd_backup(args: List[str], prefs_factory: Callable[[], Any]) -> int:
    if not args:
        return emit_error(
            "INVALID_USAGE",
            "Usage: audio-hooks backup <list|show|restore|prune>",
            suggested_command="audio-hooks manifest",
        )
    sub = args[0]
    rest = args[1:]
    prefs = prefs_factory()
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
