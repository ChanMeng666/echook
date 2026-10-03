"""UserPreferences — single source of truth for user_preferences.json access.

This module is intentionally side-effect-free at import time. All filesystem
probing happens lazily on first use of a UserPreferences instance. Both
hook_runner.py and bin/audio-hooks.py acquire an instance via get_prefs()
(lazy module-level singleton) so they share path resolution, load
semantics, and backup state.

See docs/specs/2026-05-01-painless-upgrades-design.md for the design.
"""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class UserPreferences:
    """Single source of truth for user_preferences.json access.

    Owns path resolution, load (with auto-migration), save (with auto-backup),
    backup management, and diff-from-default reporting.
    """

    PLUGIN_ID = "audio-hooks-chanmeng-audio-hooks"
    EXTERNAL_BACKUP_DIRNAME = ".claude-audio-hooks-backups"
    EXTERNAL_BACKUP_KEEP = 20
    LOCK_TIMEOUT_SECONDS = 5

    # Release that first shipped each matcher variant, and its parent hook.
    # Migration reads it to decide which variants a stored file predates (see
    # _seed_new_variants). It lives here, not beside SYNTHETIC_EVENT_MAP, because
    # hook_runner imports this module and the migration cannot import it back.
    # tests/test_variant_migration.py fails when this and SYNTHETIC_EVENT_MAP
    # disagree on the variant set or a parent, so adding a variant means adding
    # one row here (the release it ships in) -- no new migration code.
    # Release = the first git tag whose hook_runner.py carried the variant.
    VARIANT_INTRODUCED: Dict[str, Tuple[str, str]] = {
        "session_start_startup": ("session_start", "5.0.0"),
        "session_start_resume": ("session_start", "5.0.0"),
        "session_start_clear": ("session_start", "5.0.0"),
        "session_start_compact": ("session_start", "5.0.0"),
        "session_start_fork": ("session_start", "6.5.0"),
        "session_end_clear": ("session_end", "5.0.0"),
        "session_end_resume": ("session_end", "5.0.0"),
        "session_end_logout": ("session_end", "5.0.0"),
        "session_end_prompt_input_exit": ("session_end", "5.0.0"),
        "stop_failure_rate_limit": ("stop_failure", "5.0.0"),
        "stop_failure_authentication_failed": ("stop_failure", "5.0.0"),
        "stop_failure_oauth_org_not_allowed": ("stop_failure", "6.5.0"),
        "stop_failure_account_on_hold": ("stop_failure", "6.5.0"),
        "stop_failure_billing_error": ("stop_failure", "5.0.0"),
        "stop_failure_model_not_found": ("stop_failure", "6.5.0"),
        "stop_failure_invalid_request": ("stop_failure", "5.0.0"),
        "stop_failure_server_error": ("stop_failure", "5.0.0"),
        "stop_failure_overloaded": ("stop_failure", "6.5.0"),
        "stop_failure_max_output_tokens": ("stop_failure", "5.0.0"),
        "stop_failure_unknown": ("stop_failure", "5.0.0"),
        "stop_failure_cloud_credential_error": ("stop_failure", "6.7.0"),
        "stop_failure_verification_required": ("stop_failure", "6.7.0"),
        "notification_permission_prompt": ("notification", "5.0.0"),
        "notification_idle_prompt": ("notification", "5.0.0"),
        "notification_auth_success": ("notification", "5.0.0"),
        "notification_elicitation_dialog": ("notification", "5.0.0"),
        "notification_agent_needs_input": ("notification", "6.4.0"),
        "notification_agent_completed": ("notification", "6.4.0"),
        "notification_elicitation_complete": ("notification", "6.4.0"),
        "notification_elicitation_response": ("notification", "6.4.0"),
        "notification_elicitation_url_dialog": ("notification", "6.5.0"),
        "notification_worker_permission_prompt": ("notification", "6.5.0"),
        "notification_push_notification": ("notification", "6.5.0"),
        "notification_computer_use_enter": ("notification", "6.5.0"),
        "notification_computer_use_exit": ("notification", "6.5.0"),
        "notification_quota_auto_resume_fired": ("notification", "6.5.0"),
        "notification_quota_auto_resume_stale": ("notification", "6.5.0"),
        "notification_quota_auto_resume_disabled": ("notification", "6.5.0"),
        "notification_auth_storage_failure": ("notification", "6.7.0"),
        "precompact_manual": ("precompact", "5.0.0"),
        "precompact_auto": ("precompact", "5.0.0"),
        "postcompact_manual": ("postcompact", "5.0.0"),
        "postcompact_auto": ("postcompact", "5.0.0"),
        "directory_added_slash_command": ("directory_added", "6.5.0"),
        "directory_added_register_repo_root": ("directory_added", "6.5.0"),
        "setup_init": ("setup", "6.2.0"),
        "setup_maintenance": ("setup", "6.2.0"),
    }

    METADATA_KEYS = ("_version", "version", "$schema")
    COMMENT_PREFIX = "_"

    # Dotted paths for features the product dropped. Migration deletes these
    # and reports each one in its notes list; everything else the user has that
    # the template lacks is left alone (a config written by a *newer* echook
    # must survive being read by an older one — see
    # tests/test_migration.py::test_user_extra_key_preserved).
    DROPPED_KEYS = (
        "focus_flow",                     # removed in 6.0.0 — out of scope (wellness/timers)
        "enabled_hooks.worktree_create",  # removed in 6.3.4 — a provider hook, not a notification
    )

    def __init__(self, project_dir: Path, *, script_path: Optional[Path] = None):
        self.project_dir = Path(project_dir)
        self._script_path = Path(script_path) if script_path else Path(__file__).resolve()
        self._cached_data_dir: Optional[Path] = None

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    @property
    def data_dir(self) -> Path:
        if self._cached_data_dir is not None:
            return self._cached_data_dir
        self._cached_data_dir = self._resolve_data_dir()
        return self._cached_data_dir

    @property
    def config_path(self) -> Path:
        return self.data_dir / "user_preferences.json"

    @property
    def queue_dir(self) -> Path:
        d = self.data_dir
        # The legacy temp fallback is itself named claude_audio_hooks_queue;
        # don't double-nest by appending another /queue.
        if d.name == "claude_audio_hooks_queue":
            return d
        return d / "queue"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    def _resolve_data_dir(self) -> Path:
        """Seven-level priority chain. See spec for rationale.

        Priority order:
          1. ``CLAUDE_PLUGIN_DATA`` env var (set by Claude Code plugin loader)
          2. ``PLUGIN_DATA`` env var (set by Codex plugin loader)
          3. ``CLAUDE_AUDIO_HOOKS_DATA`` env var (explicit override)
          4. **Codex-native dir** at ``$CODEX_HOME/audio-hooks-data/`` —
             gated by ``detect_invoker() == "codex"``. Sits ahead of the
             Claude Code shared dir so a developer machine that happens to
             have both Claude Code and Codex installed still lands at the
             right dir under Codex sessions.
          5. Plugin cache dir (when running from plugin layout)
          6. Shared Claude Code dir at ``~/.claude/plugins/data/<id>/``
          7. Cursor-native dir at ``~/.cursor/audio-hooks-data/``
          8. Legacy temp fallback
        """
        v = os.environ.get("CLAUDE_PLUGIN_DATA")
        if v:
            return Path(v)
        v = os.environ.get("PLUGIN_DATA")
        if v:
            return Path(v)
        v = os.environ.get("CLAUDE_AUDIO_HOOKS_DATA")
        if v:
            return Path(v)
        home = Path.home()
        # Codex-native dir, gated by invoker so we don't hijack Cursor/Claude
        # Code sessions when only Codex happens to be installed too.
        # Imported lazily to avoid any chance of an import loop and so this
        # module stays import-time side-effect-free for non-Codex callers.
        try:
            import sys as _sys
            _hooks_dir = str(Path(__file__).resolve().parent)
            if _hooks_dir not in _sys.path:
                _sys.path.insert(0, _hooks_dir)
            from invoker import get_invoker  # type: ignore
            if get_invoker() == "codex":
                codex_home = Path(os.environ.get("CODEX_HOME") or str(home / ".codex"))
                return codex_home / "audio-hooks-data"
        except Exception:
            pass
        if self._is_running_from_plugin():
            return self._plugin_cache_data_dir()
        shared = home / ".claude" / "plugins" / "data" / self.PLUGIN_ID
        if (shared / "user_preferences.json").exists():
            return shared
        cursor_native = home / ".cursor" / "audio-hooks-data"
        if (cursor_native / "user_preferences.json").exists():
            return cursor_native
        if platform.system() == "Windows":
            base = Path(os.environ.get("TEMP", os.environ.get("TMP", "C:/Windows/Temp")))
        else:
            base = Path("/tmp")
        return base / "claude_audio_hooks_queue"

    def _is_running_from_plugin(self) -> bool:
        """True if the script lives under a plugin layout (cache dir).

        Looks for `.claude-plugin/plugin.json` two levels up from the script.
        """
        try:
            plugin_root = self._script_path.parent.parent
            return (plugin_root / ".claude-plugin" / "plugin.json").exists()
        except Exception:
            return False

    def _plugin_cache_data_dir(self) -> Path:
        """Resolve data dir when running from plugin cache layout.

        Plugin data lives at ~/.claude/plugins/data/<id>/, persistent across
        plugin updates. Falls back to a glob search if the canonical path
        is missing (e.g., older plugin manager versions used a different id
        normalisation).
        """
        home = Path.home()
        canonical = home / ".claude" / "plugins" / "data" / self.PLUGIN_ID
        if canonical.exists():
            return canonical
        data_root = home / ".claude" / "plugins" / "data"
        if data_root.exists():
            try:
                for child in data_root.iterdir():
                    if child.is_dir() and "audio-hooks" in child.name:
                        return child
            except OSError:
                pass
        return canonical  # canonical path; will be created on first write

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def _template_path(self) -> Path:
        return self.project_dir / "config" / "default_preferences.json"

    def _load_template(self) -> Dict[str, Any]:
        try:
            return json.loads(self._template_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _auto_init(self) -> None:
        """Copy template into config_path if it doesn't exist yet."""
        if self.config_path.exists():
            return
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            template = self._template_path()
            if template.exists():
                import shutil
                shutil.copy2(str(template), str(self.config_path))
        except OSError:
            pass

    def load(self, *, read_only: bool = False) -> Dict[str, Any]:
        """Read user_preferences.json, auto-init from template if missing,
        auto-migrate if older _version detected, apply plugin-option env overlay.

        ``read_only=True`` is for commands that only report: the same result is
        computed in memory (template defaults merged under whatever is on disk,
        env overlay applied) but nothing is created, migrated or re-stamped on
        disk -- not the file, not its directory, not the lock or the ``.bak``.
        The hook runner and every state-changing command keep the default.
        """
        if not read_only:
            self._auto_init()
        try:
            cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cfg = {}
        template = self._load_template()
        cfg, did_migrate, _notes = self._migrate_if_needed(cfg, template)
        if did_migrate and not read_only:
            cfg = self._persist_migration(cfg, template)
        cfg = self._apply_plugin_overlay(cfg)
        return cfg

    def _persist_migration(self, cfg: Dict[str, Any], template: Dict[str, Any]) -> Dict[str, Any]:
        """Write a migrated cfg back to disk, capturing a pre-migration .bak.

        **Invariant: the sibling .bak holds pre-migration content or nothing —
        never post-migration content.** A .bak that has already been migrated
        looks like a recovery point and isn't one, which is worse than no .bak
        at all.

        That invariant is what forces the re-read below. This project spawns a
        hook per event and routinely has many in flight, so the first load
        after an upgrade races N processes that all read the same stale config
        and all compute the same merge (the merge is deterministic — see
        test_migration_is_idempotent_on_disk). Without the re-read, every loser
        would reach _write_sibling_backup() *after* the winner had already
        rewritten the file, and copy the winner's migrated config over the good
        backup. Measured: 16 racing loaders reliably destroyed it.

        So the whole thing is a check-then-act under the lock, using the same
        predicate that decided to migrate in the first place: if the file on
        disk no longer needs migrating, someone else did the work — return
        their result and touch neither file. This also stops us clobbering a
        concurrent `audio-hooks set` that landed between our read and the lock.

        Re-run the predicate; do NOT byte-compare the file against
        json.dumps(cfg). _atomic_write_json persists via write_text, which
        translates \\n to \\r\\n on Windows, so those bytes never match and
        every loser would fall through to the backup anyway. A byte guard
        reviews as correct and passes on Linux CI while guarding nothing on
        the platform this project is developed on.

        Returns the config to use. Never raises: a read-only data dir or a lock
        timeout leaves the migrated cfg correct in memory (and returns it), so
        the event still fires and the next load retries the write.
        """
        try:
            with self._acquire_lock():
                # Re-read under the lock — our caller's copy is pre-lock and
                # may be stale by now.
                current: Optional[Dict[str, Any]] = None
                try:
                    parsed = json.loads(self.config_path.read_text(encoding="utf-8"))
                    if isinstance(parsed, dict):
                        current = parsed
                except (OSError, ValueError):
                    pass  # missing or corrupt — fall through and repair it
                if current is not None:
                    fresh, still_needed, _notes = self._migrate_if_needed(current, template)
                    if not still_needed:
                        return current
                else:
                    fresh = cfg
                # Sibling .bak only, not the rotating external dir: this fires
                # unattended on the first load after an upgrade, so
                # `backup restore latest-sibling` stays available without
                # burning a rotation slot on every install.
                self._write_sibling_backup()
                self._atomic_write_json(self.config_path, fresh)
                return fresh
        except (OSError, UserPreferences._LockTimeout):
            return cfg

    def migrate(self) -> Dict[str, Any]:
        """Bring the stored preferences file up to the template, explicitly.

        What ``load()`` does lazily for the hook runner and state-changing
        commands, as one idempotent call. Never creates the file or its
        directory: an absent file is reported (``exists: False``), not
        initialised. Returns a report dict; raises ``ValueError`` if the file is
        not a JSON object and ``OSError`` if the write could not be completed.
        """
        path = self.config_path
        report: Dict[str, Any] = {"config_path": str(path), "exists": path.exists(), "changed": False,
                                  "from_version": None, "to_version": None,
                                  "added": [], "removed": [], "stale": [], "backup": None}
        if not report["exists"]:
            return report
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise ValueError(f"not valid JSON: {e}") from e
        if not isinstance(cfg, dict):
            raise ValueError("not a JSON object")
        template = self._load_template()
        report["from_version"] = cfg.get("_version")
        report["to_version"] = cfg.get("_version")
        merged, did, notes = self._migrate_if_needed(cfg, template)
        if not did:
            return report
        self._persist_migration(merged, template)
        # _persist_migration swallows a lock timeout (right for a hook); here the
        # caller asked for the write, so check it landed.
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise OSError(f"could not re-read {path} after migrating: {e}") from e
        if self._migrate_if_needed(stored, template)[1]:
            raise OSError(f"could not write {path} (lock held or directory read-only); nothing changed")
        report.update(
            changed=True,
            to_version=stored.get("_version"),
            # Report real settings: template comment/metadata keys ("_comment_x",
            # "$schema") are rewritten on every migration and say nothing, and a
            # variant key is not "stale" just because the template does not list it.
            added=[n for n in notes if not n.startswith(("removed:", "stale:"))
                   and not n.rsplit(".", 1)[-1].startswith(self.COMMENT_PREFIX)
                   and n.rsplit(".", 1)[-1] not in self.METADATA_KEYS],
            removed=[n[len("removed:"):] for n in notes if n.startswith("removed:")],
            stale=[n[len("stale:"):] for n in notes if n.startswith("stale:")
                   and n.rsplit(".", 1)[-1] not in self.VARIANT_INTRODUCED],
            backup=str(self.sibling_backup_path) if self.sibling_backup_path.exists() else None,
        )
        return report

    def _apply_plugin_overlay(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """Overlay CLAUDE_PLUGIN_OPTION_* env vars onto config."""
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
                self._set_dotted_in(cfg, dotted_key, coerce(raw))
            except Exception:
                pass
        # Side-effect: setting the webhook URL via plugin userConfig should
        # auto-enable webhooks so they actually fire. Pre-5.1.5 only the CLI
        # side did this; consolidating here means hook_runner gets it too.
        if os.environ.get("CLAUDE_PLUGIN_OPTION_WEBHOOK_URL", "").strip():
            self._set_dotted_in(cfg, "webhook_settings.enabled", True)
        return cfg

    @staticmethod
    def _set_dotted_in(cfg: Dict[str, Any], dotted_key: str, value: Any) -> None:
        parts = dotted_key.split(".")
        node = cfg
        for p in parts[:-1]:
            if p not in node or not isinstance(node[p], dict):
                node[p] = {}
            node = node[p]
        node[parts[-1]] = value

    # ------------------------------------------------------------------
    # Migration
    # ------------------------------------------------------------------

    def _deep_merge_missing(
        self,
        template: Dict[str, Any],
        user: Dict[str, Any],
        _path: str = "",
    ) -> Tuple[Dict[str, Any], List[str]]:
        """Return (merged_dict, list_of_added_dotted_paths).

        Rules (see spec):
          - METADATA_KEYS + comment fields (_*): always take template
          - dict in template, dict in user → recurse
          - dict in template, scalar in user → reset to template (unrecoverable)
          - any other case where user has a value: keep user value
          - key in template but not user: adopt template value
        """
        merged: Dict[str, Any] = dict(user)
        added: List[str] = []
        for k, t_val in template.items():
            full_path = f"{_path}.{k}" if _path else k
            # Metadata + comment fields: always overwrite
            if k in self.METADATA_KEYS or k.startswith(self.COMMENT_PREFIX):
                if k not in user:
                    added.append(full_path)
                merged[k] = t_val
                continue
            # New key: adopt template
            if k not in user:
                merged[k] = t_val
                added.append(full_path)
                if isinstance(t_val, dict):
                    # Enumerate nested paths so callers can report every new key.
                    _, sub_added = self._deep_merge_missing(t_val, {}, full_path)
                    added.extend(sub_added)
                continue
            u_val = user[k]
            # dict in template, scalar/list in user → reset
            if isinstance(t_val, dict) and not isinstance(u_val, dict):
                merged[k] = t_val
                continue
            # Both dicts: recurse
            if isinstance(t_val, dict) and isinstance(u_val, dict):
                sub, sub_added = self._deep_merge_missing(t_val, u_val, full_path)
                merged[k] = sub
                added.extend(sub_added)
                continue
            # Otherwise (scalar-vs-scalar, list-vs-anything, etc.): keep user
            merged[k] = u_val
        return merged, added

    def _strip_dropped_keys(self, cfg: Dict[str, Any]) -> List[str]:
        """Delete DROPPED_KEYS from cfg in place. Returns the paths removed.

        Only exact known-dead paths are touched — never "anything the template
        no longer has", which would eat forward-compatible keys.
        """
        removed: List[str] = []
        for dotted in self.DROPPED_KEYS:
            parts = dotted.split(".")
            node: Any = cfg
            for p in parts[:-1]:
                if not isinstance(node, dict) or p not in node:
                    node = None
                    break
                node = node[p]
            if isinstance(node, dict) and parts[-1] in node:
                del node[parts[-1]]
                removed.append(dotted)
        return removed

    @staticmethod
    def _parse_release(value: Any) -> Optional[Tuple[int, ...]]:
        """"6.7.0" -> (6, 7, 0); None for anything that is not dotted integers."""
        if not isinstance(value, str):
            return None
        try:
            return tuple(int(p) for p in value.strip().split("."))
        except ValueError:
            return None

    def _seed_new_variants(self, merged: Dict[str, Any], stored_version: Any,
                           template_version: Any) -> List[str]:
        """Write ``enabled_hooks[variant] = False`` for variants an enumerated config predates.

        A variant with no key inherits its parent. That is right for a user who
        enabled the whole parent, and wrong for one who used ``hooks enable-only``
        (or listed variants by hand) to say "only these": the variants added after
        that choice would become audible the moment they ship, against what the
        user asked for. ``enable-only`` leaves a signature -- the parent explicitly
        true and *every* then-known sibling variant an explicit boolean -- so when
        a variant newer than the stored file (``stored < introduced <= template``)
        has no key, that signature is present, and the parent is explicitly true,
        the new variant is written ``False``. In every other case it stays absent
        and inherits:

        * parent false/absent: nothing to inherit, nothing audible, nothing written;
        * ``hooks enable <parent>`` (no sibling keys) or a single hand-disabled
          sibling: not an enumeration, the user wants the parent's behaviour;
        * a file already at the template version: no variant is newer, untouched.

        Idempotent (the written key makes the variant "known"). Release ranges come
        from VARIANT_INTRODUCED. Limit: a file whose enumeration predates an earlier
        addition that it never answered (siblings introduced after its stored
        version were left absent) fails the "every older sibling explicit" test
        and keeps inheriting, as it did before this rule existed.
        """
        enabled = merged.get("enabled_hooks")
        stored, target = self._parse_release(stored_version), self._parse_release(template_version)
        if not isinstance(enabled, dict) or stored is None or target is None:
            return []
        by_parent: Dict[str, List[Tuple[str, Tuple[int, ...]]]] = {}
        for name, (parent, release) in self.VARIANT_INTRODUCED.items():
            parsed = self._parse_release(release)
            if parsed is not None:
                by_parent.setdefault(parent, []).append((name, parsed))
        written: List[str] = []
        for parent, items in sorted(by_parent.items()):
            if enabled.get(parent) is not True:
                continue
            older = [n for n, r in items if r <= stored]
            new = [n for n, r in items if stored < r <= target and n not in enabled]
            if new and older and all(isinstance(enabled.get(n), bool) for n in older):
                for n in sorted(new):
                    enabled[n] = False
                    written.append("enabled_hooks." + n)
        return written

    def _collect_stale_keys(
        self,
        template: Dict[str, Any],
        user: Dict[str, Any],
        _path: str = "",
    ) -> List[str]:
        """Dotted paths the user has that the template doesn't. Report-only —
        these are NOT removed (they may come from a newer echook)."""
        stale: List[str] = []
        for k, u_val in user.items():
            if k in self.METADATA_KEYS or k.startswith(self.COMMENT_PREFIX):
                continue
            full = f"{_path}.{k}" if _path else k
            if k not in template:
                stale.append(full)
                continue
            t_val = template[k]
            if isinstance(t_val, dict) and isinstance(u_val, dict):
                stale.extend(self._collect_stale_keys(t_val, u_val, full))
        return stale

    def _migrate_if_needed(self, cfg: Dict[str, Any], template: Dict[str, Any]) -> Tuple[Dict[str, Any], bool, List[str]]:
        """Return (cfg_after_migration, did_migrate, notes).

        Notes entries: a bare dotted path = key added from the template;
        ``removed:<path>`` = a DROPPED_KEYS entry deleted; ``stale:<path>`` =
        a user key the template no longer has, reported but left in place.

        The gate is **structural**, not a version-string compare. Through 6.5.0
        this returned early whenever ``user._version == template._version``;
        because config/default_preferences.json's stamp was left at 5.1.5 while
        the project moved to 6.5.0, that was true for a large class of installs
        and migration silently never ran — configs missed four minor versions
        of new keys. The version stamp is now one trigger among several, not
        the gate.

        Never raises: this runs inside a hook, and a malformed config must
        degrade to "use what's on disk", not kill the event.
        """
        if not isinstance(cfg, dict) or not isinstance(template, dict) or not template:
            return cfg, False, []
        try:
            template_v = template.get("_version", "0.0.0")
            merged, added = self._deep_merge_missing(template, cfg)
            added = added + self._seed_new_variants(merged, cfg.get("_version"), template_v)
            removed = self._strip_dropped_keys(merged)
            # _deep_merge_missing always rewrites metadata + comment fields, so
            # comparing merged against cfg would report a migration on every
            # single load. Only real structural drift (or a stamp that lags the
            # template) counts as needing one.
            if not added and not removed and cfg.get("_version") == template_v:
                return cfg, False, []
            merged["_version"] = template_v
            merged["version"] = template_v
            notes = list(added)
            notes.extend("removed:" + p for p in removed)
            notes.extend("stale:" + p for p in self._collect_stale_keys(template, merged))
            return merged, True, notes
        except Exception:
            return cfg, False, []

    def _atomic_write_json(self, target: Path, cfg: Dict[str, Any]) -> None:
        """Atomic write via tempfile + os.replace."""
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)

    # ------------------------------------------------------------------
    # Save + backup
    # ------------------------------------------------------------------

    @property
    def external_backup_dir(self) -> Path:
        return Path.home() / self.EXTERNAL_BACKUP_DIRNAME / self.PLUGIN_ID

    @property
    def sibling_backup_path(self) -> Path:
        return self.config_path.with_suffix(".json.bak")

    @staticmethod
    def _id_to_filename(backup_id: str) -> str:
        return backup_id.replace(":", "-") + ".json"

    @staticmethod
    def _filename_to_id(filename: str) -> str:
        # 2026-05-01T07-42-13.041Z.json -> 2026-05-01T07:42:13.041Z
        stem = filename
        if stem.endswith(".json"):
            stem = stem[:-5]
        # Restore : at positions 13 and 16
        if len(stem) >= 17 and stem[13] == "-" and stem[16] == "-":
            return stem[:13] + ":" + stem[14:16] + ":" + stem[17:]
        return stem

    def _current_iso_id(self) -> str:
        import time
        t = time.time()
        ms = int((t - int(t)) * 1000)
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{ms:03d}Z"

    def _write_sibling_backup(self) -> Optional[bytes]:
        """Copy the live config over its sibling .bak (overwrite, no rotation).

        Returns the bytes copied, or None when there was nothing to copy —
        callers use that to distinguish "first write" from "backed up".
        """
        if not self.config_path.exists():
            return None
        try:
            current_bytes = self.config_path.read_bytes()
        except OSError:
            return None
        try:
            self.sibling_backup_path.write_bytes(current_bytes)
        except OSError:
            pass
        return current_bytes

    def _snapshot_backup(self) -> Optional[str]:
        """Snapshot current config file content to sibling .bak + external dir.

        Returns the ID of the newly created external backup, or None if no
        backup was needed (first save / dedup hit).
        """
        current_bytes = self._write_sibling_backup()
        if current_bytes is None:
            return None

        # External: dedup
        ext_dir = self.external_backup_dir
        try:
            ext_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None

        existing = sorted(
            ext_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if existing:
            try:
                if existing[0].read_bytes() == current_bytes:
                    return None  # dedup
            except OSError:
                pass

        backup_id = self._current_iso_id()
        target = ext_dir / self._id_to_filename(backup_id)
        try:
            target.write_bytes(current_bytes)
        except OSError:
            return None

        # Rotation
        self.prune_backups()
        return backup_id

    def prune_backups(self, keep: Optional[int] = None) -> int:
        """Trim external dir to KEEP most recent. Returns count removed."""
        if keep is None:
            keep = self.EXTERNAL_BACKUP_KEEP
        ext_dir = self.external_backup_dir
        if not ext_dir.exists():
            return 0
        files = sorted(
            ext_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        removed = 0
        for f in files[keep:]:
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    def save(self, cfg: Dict[str, Any]) -> Optional[str]:
        """Atomically write cfg to disk, snapshotting prior content first.

        Returns the ID of the external backup created (None on first save
        or when content is byte-identical to the latest backup).
        """
        with self._acquire_lock():
            backup_id = self._snapshot_backup()
            self._atomic_write_json(self.config_path, cfg)
            return backup_id

    def list_backups(self) -> List[Dict[str, Any]]:
        """Return list of backup entries, newest first."""
        import datetime
        entries: List[Dict[str, Any]] = []
        # External
        ext_dir = self.external_backup_dir
        if ext_dir.exists():
            for f in ext_dir.glob("*.json"):
                try:
                    stat = f.stat()
                except OSError:
                    continue
                backup_id = self._filename_to_id(f.name)
                try:
                    body = json.loads(f.read_text(encoding="utf-8"))
                    from_version = body.get("_version", "unknown")
                except (OSError, ValueError):
                    from_version = "unknown"
                entries.append({
                    "id": backup_id,
                    "location": "external",
                    "path": str(f),
                    "size_bytes": stat.st_size,
                    "from_version": from_version,
                    "mtime_iso": datetime.datetime.utcfromtimestamp(
                        stat.st_mtime
                    ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })
        # Sibling
        sib = self.sibling_backup_path
        if sib.exists():
            try:
                stat = sib.stat()
                body = json.loads(sib.read_text(encoding="utf-8"))
                from_version = body.get("_version", "unknown")
                entries.append({
                    "id": "latest-sibling",
                    "location": "sibling",
                    "path": str(sib),
                    "size_bytes": stat.st_size,
                    "from_version": from_version,
                    "mtime_iso": datetime.datetime.utcfromtimestamp(
                        stat.st_mtime
                    ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })
            except OSError:
                pass
        # Sort newest first by mtime_iso
        entries.sort(key=lambda e: e["mtime_iso"], reverse=True)
        return entries

    def restore_from(self, backup_id: str) -> Dict[str, Any]:
        """Restore config from a backup. Magic strings: latest, latest-sibling,
        latest-external. Or an exact ISO timestamp matching an external backup.

        Returns the restored config dict. The current state is itself
        snapshotted via save() before being overwritten.
        """
        entries = self.list_backups()
        target_path: Optional[Path] = None
        if backup_id == "latest":
            if entries:
                target_path = Path(entries[0]["path"])
        elif backup_id == "latest-sibling":
            for e in entries:
                if e["location"] == "sibling":
                    target_path = Path(e["path"])
                    break
        elif backup_id == "latest-external":
            for e in entries:
                if e["location"] == "external":
                    target_path = Path(e["path"])
                    break
        else:
            # Exact ID match (external only)
            for e in entries:
                if e["id"] == backup_id and e["location"] == "external":
                    target_path = Path(e["path"])
                    break
        if target_path is None or not target_path.exists():
            raise FileNotFoundError(f"backup not found: {backup_id}")
        try:
            cfg = json.loads(target_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ValueError(f"backup unreadable: {e}") from e
        self.save(cfg)  # itself triggers a backup of pre-restore state
        return cfg

    # ------------------------------------------------------------------
    # File lock (cross-platform)
    # ------------------------------------------------------------------

    def _lock_path(self) -> Path:
        return self.data_dir / ".user_prefs.lock"

    class _LockTimeout(Exception):
        pass

    def _acquire_lock(self):
        """Context manager: exclusive lock on .user_prefs.lock file."""
        return _UserPrefsLock(self._lock_path(), self.LOCK_TIMEOUT_SECONDS)

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def get_dotted(self, dotted_key: str, *, read_only: bool = False) -> Any:
        cfg = self.load(read_only=read_only)
        node: Any = cfg
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    def set_dotted(self, dotted_key: str, value: Any) -> None:
        cfg = self.load()
        self._set_dotted_in(cfg, dotted_key, value)
        self.save(cfg)

    def diff_from_default(self, *, read_only: bool = False) -> Dict[str, Any]:
        """Return a flat dotted-key dict of values where user differs from
        bundled default_preferences.json. Excludes metadata + comment fields.
        """
        user = self.load(read_only=read_only)
        template = self._load_template()
        out: Dict[str, Any] = {}
        self._collect_diff(template, user, "", out)
        return out

    def _collect_diff(
        self,
        template: Dict[str, Any],
        user: Dict[str, Any],
        prefix: str,
        out: Dict[str, Any],
    ) -> None:
        for k, u_val in user.items():
            if k in self.METADATA_KEYS or k.startswith(self.COMMENT_PREFIX):
                continue
            full = f"{prefix}.{k}" if prefix else k
            if k not in template:
                out[full] = u_val
                continue
            t_val = template[k]
            if isinstance(t_val, dict) and isinstance(u_val, dict):
                self._collect_diff(t_val, u_val, full, out)
            elif u_val != t_val:
                out[full] = u_val


class _UserPrefsLock:
    """Cross-platform exclusive file lock context manager."""

    def __init__(self, lock_path: Path, timeout_seconds: float):
        self.lock_path = lock_path
        self.timeout = timeout_seconds
        self._fh = None

    def __enter__(self):
        import time
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.lock_path, "a+b")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._try_lock()
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    self._fh.close()
                    raise UserPreferences._LockTimeout(
                        f"could not acquire {self.lock_path} within {self.timeout}s"
                    )
                time.sleep(0.05)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._fh is not None:
            try:
                self._unlock()
            finally:
                self._fh.close()
                self._fh = None
        return False

    def _try_lock(self):
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(self):
        if os.name == "nt":
            import msvcrt
            try:
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


# ----------------------------------------------------------------------
# Module-level lazy singleton
# ----------------------------------------------------------------------

_prefs_instance: Optional[UserPreferences] = None


def get_prefs(project_dir: Optional[Path] = None, *, script_path: Optional[Path] = None) -> UserPreferences:
    """Return the process-wide UserPreferences singleton. Lazy-initialised."""
    global _prefs_instance
    if _prefs_instance is None:
        if project_dir is None:
            # Walk up from this file to find a project root with config/default_preferences.json
            here = Path(__file__).resolve()
            for ancestor in [here.parent] + list(here.parents):
                if (ancestor / "config" / "default_preferences.json").exists():
                    project_dir = ancestor
                    break
            if project_dir is None:
                raise RuntimeError("Cannot locate project_dir for UserPreferences")
        _prefs_instance = UserPreferences(project_dir, script_path=script_path)
    return _prefs_instance


def _reset_prefs() -> None:
    """Test-only: clear the singleton so the next get_prefs() reinitialises."""
    global _prefs_instance
    _prefs_instance = None
