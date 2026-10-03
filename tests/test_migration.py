"""Tests for UserPreferences migration semantics.

Each row in the migration table from the spec is pinned here:
- _version / version / $schema / _comment* always overwrite from template
- Other top-level keys: user wins if present, template adopted if missing
- Nested dicts: recurse with same rules
- Lists: atomic — user list wins entirely (no element merge)
- Type mismatch (scalar vs scalar): keep user
- Type mismatch (scalar vs container): reset to template default
- User has key template doesn't: keep user

TestStructuralMigrationGate pins the 6.5.1 fix: the gate is structural, not a
_version string compare.
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO / "hooks" / "user_preferences.py"


def _load_module():
    sys.modules.pop("user_preferences", None)
    spec = importlib.util.spec_from_file_location("user_preferences", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestDeepMergeMissing(unittest.TestCase):
    """Pure-function tests of _deep_merge_missing — no IO."""

    def setUp(self):
        self.mod = _load_module()
        self.prefs = self.mod.UserPreferences(REPO)

    def test_empty_user_takes_full_template(self):
        template = {"audio_theme": "default", "x": {"y": 1}}
        merged, added = self.prefs._deep_merge_missing(template, {})
        self.assertEqual(merged, template)
        self.assertIn("audio_theme", added)
        self.assertIn("x.y", added)

    def test_existing_scalar_preserved_even_when_template_flips(self):
        template = {"subagent_stop": True}
        user = {"subagent_stop": False}
        merged, added = self.prefs._deep_merge_missing(template, user)
        self.assertEqual(merged["subagent_stop"], False)
        self.assertEqual(added, [])

    def test_new_key_added(self):
        template = {"a": 1, "b": 2}
        user = {"a": 99}
        merged, added = self.prefs._deep_merge_missing(template, user)
        self.assertEqual(merged, {"a": 99, "b": 2})
        self.assertEqual(added, ["b"])

    def test_user_extra_key_preserved(self):
        template = {"a": 1}
        user = {"a": 1, "future_key": "still_here"}
        merged, added = self.prefs._deep_merge_missing(template, user)
        self.assertEqual(merged["future_key"], "still_here")

    def test_nested_dict_recurses(self):
        template = {"webhook_settings": {"enabled": False, "format": "raw", "include_user_email": False}}
        user = {"webhook_settings": {"enabled": True, "format": "slack"}}
        merged, added = self.prefs._deep_merge_missing(template, user)
        self.assertEqual(merged["webhook_settings"]["enabled"], True)        # user wins
        self.assertEqual(merged["webhook_settings"]["format"], "slack")      # user wins
        self.assertEqual(merged["webhook_settings"]["include_user_email"], False)  # added
        self.assertIn("webhook_settings.include_user_email", added)

    def test_list_user_wins_entirely(self):
        template = {"hooks": ["stop", "notification", "permission_request"]}
        user = {"hooks": ["stop"]}  # user explicitly chose only one
        merged, added = self.prefs._deep_merge_missing(template, user)
        self.assertEqual(merged["hooks"], ["stop"])  # NOT merged with template

    def test_type_mismatch_scalar_vs_scalar_keeps_user(self):
        template = {"thresh": 80}
        user = {"thresh": "high"}  # weird, but recoverable
        merged, _added = self.prefs._deep_merge_missing(template, user)
        self.assertEqual(merged["thresh"], "high")

    def test_type_mismatch_scalar_vs_container_resets(self):
        """User's `enabled_hooks: true` (legacy) cannot be kept when template
        wants a dict — every downstream `.get(...)` would crash."""
        template = {"enabled_hooks": {"stop": True, "notification": True}}
        user = {"enabled_hooks": True}
        merged, _added = self.prefs._deep_merge_missing(template, user)
        self.assertEqual(merged["enabled_hooks"], {"stop": True, "notification": True})

    def test_comment_fields_always_overwritten(self):
        template = {"_comment": "v5.1.5 docs"}
        user = {"_comment": "v5.0.0 docs"}
        merged, _added = self.prefs._deep_merge_missing(template, user)
        self.assertEqual(merged["_comment"], "v5.1.5 docs")

    def test_metadata_fields_always_overwritten(self):
        template = {"_version": "5.1.5", "version": "5.1.5", "$schema": "./new.json"}
        user = {"_version": "5.1.3", "version": "5.1.3", "$schema": "./old.json"}
        merged, _added = self.prefs._deep_merge_missing(template, user)
        self.assertEqual(merged["_version"], "5.1.5")
        self.assertEqual(merged["version"], "5.1.5")
        self.assertEqual(merged["$schema"], "./new.json")


class TestMigrationFlow(unittest.TestCase):
    """Migration is triggered from load() when _version differs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_PLUGIN_DATA"] = self.tmp.name
        self.mod = _load_module()

    def tearDown(self):
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        self.tmp.cleanup()

    def test_no_op_when_versions_match(self):
        prefs = self.mod.UserPreferences(REPO)
        template = prefs._load_template()
        user = dict(template)
        user["audio_theme"] = "custom"  # one customisation
        Path(self.tmp.name, "user_preferences.json").write_text(
            json.dumps(user), encoding="utf-8"
        )
        cfg = prefs.load()
        self.assertEqual(cfg["audio_theme"], "custom")
        # _version stayed same, file should be unchanged on disk
        on_disk = json.loads(Path(self.tmp.name, "user_preferences.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["audio_theme"], "custom")

    def test_migration_bumps_version_and_writes_to_disk(self):
        prefs = self.mod.UserPreferences(REPO)
        old = {"_version": "5.1.3", "version": "5.1.3", "audio_theme": "custom"}
        Path(self.tmp.name, "user_preferences.json").write_text(
            json.dumps(old), encoding="utf-8"
        )
        cfg = prefs.load()
        # User's audio_theme preserved
        self.assertEqual(cfg["audio_theme"], "custom")
        # Version bumped to template's
        template_version = prefs._load_template().get("_version")
        self.assertEqual(cfg["_version"], template_version)
        # Persisted to disk
        on_disk = json.loads(Path(self.tmp.name, "user_preferences.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["_version"], template_version)
        # New keys merged in (e.g., enabled_hooks block from template)
        self.assertIn("enabled_hooks", cfg)


class TestStructuralMigrationGate(unittest.TestCase):
    """6.5.1: `user._version == template._version` must not be able to veto a
    migration that is structurally needed.

    config/default_preferences.json's stamp sat at 5.1.5 from 5.1.5 through
    6.5.0, so every install stamped 5.1.5 compared equal to the template and
    migration never ran — configs silently missed four minor versions of keys.
    """

    def setUp(self):
        self.mod = _load_module()
        self.prefs = self.mod.UserPreferences(REPO)

    def test_template_stamp_tracks_project_version(self):
        """The root cause: the template's stamp must equal the plugin version."""
        template_v = self.prefs._load_template().get("_version")
        plugin = json.loads(
            (REPO / "plugins" / "audio-hooks" / ".claude-plugin" / "plugin.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(
            template_v, plugin["version"],
            "config/default_preferences.json _version drifted from the project "
            "version — scripts/bump-version.sh owns it; do not hand-edit",
        )

    def test_same_version_but_missing_key_still_migrates(self):
        """THE regression. Versions match; a newer key is absent; migrate anyway."""
        template = {"_version": "6.5.0", "version": "6.5.0", "a": 1, "new_block": {"x": True}}
        user = {"_version": "6.5.0", "version": "6.5.0", "a": 1}
        merged, did_migrate, notes = self.prefs._migrate_if_needed(user, template)
        self.assertTrue(did_migrate, "equal _version must not veto a needed migration")
        self.assertEqual(merged["new_block"], {"x": True})
        self.assertIn("new_block", notes)

    def test_older_version_gains_missing_template_key(self):
        template = {"_version": "6.5.0", "version": "6.5.0", "notification_settings": {"mode": "audio_and_notification", "terminal_sequence": {"enabled": False}}}
        user = {"_version": "5.1.5", "version": "5.1.5", "notification_settings": {"mode": "audio_only"}}
        merged, did_migrate, notes = self.prefs._migrate_if_needed(user, template)
        self.assertTrue(did_migrate)
        self.assertEqual(merged["notification_settings"]["terminal_sequence"], {"enabled": False})
        self.assertEqual(merged["_version"], "6.5.0")
        self.assertIn("notification_settings.terminal_sequence", notes)

    def test_user_value_preserved_verbatim_across_migration(self):
        template = {"_version": "6.5.0", "audio_theme": "default",
                    "enabled_hooks": {"stop": True, "directory_added": False},
                    "playback_settings": {"debounce_ms": 500}}
        user = {"_version": "5.1.5", "audio_theme": "custom",
                "enabled_hooks": {"stop": False},
                "playback_settings": {"debounce_ms": 4000}}
        merged, did_migrate, _notes = self.prefs._migrate_if_needed(user, template)
        self.assertTrue(did_migrate)
        self.assertEqual(merged["audio_theme"], "custom")
        self.assertIs(merged["enabled_hooks"]["stop"], False)
        self.assertEqual(merged["playback_settings"]["debounce_ms"], 4000)
        self.assertIs(merged["enabled_hooks"]["directory_added"], False)  # adopted

    def test_no_migration_when_structurally_identical(self):
        """Steady state must not rewrite the file on every load."""
        template = {"_version": "6.5.0", "version": "6.5.0", "_comment": "docs", "a": 1}
        user = {"_version": "6.5.0", "version": "6.5.0", "_comment": "docs", "a": 99}
        merged, did_migrate, notes = self.prefs._migrate_if_needed(user, template)
        self.assertFalse(did_migrate)
        self.assertEqual(notes, [])
        self.assertIs(merged, user)

    def test_dropped_key_removed_and_reported(self):
        """focus_flow went away in 6.0.0; worktree_create in 6.3.4."""
        template = {"_version": "6.5.0", "enabled_hooks": {"stop": True}}
        user = {
            "_version": "6.5.0",
            "enabled_hooks": {"stop": True, "worktree_create": True},
            "focus_flow": {"enabled": True, "interval_minutes": 30},
        }
        merged, did_migrate, notes = self.prefs._migrate_if_needed(user, template)
        self.assertTrue(did_migrate, "a dropped key present in the config needs a migration")
        self.assertNotIn("focus_flow", merged)
        self.assertNotIn("worktree_create", merged["enabled_hooks"])
        self.assertIn("removed:focus_flow", notes)
        self.assertIn("removed:enabled_hooks.worktree_create", notes)

    def test_unknown_extra_key_reported_stale_but_kept(self):
        """Forward compat: a key from a *newer* echook is reported, not deleted."""
        template = {"_version": "6.5.0", "a": 1}
        user = {"_version": "5.1.5", "a": 1, "future_block": {"x": 1}}
        merged, did_migrate, notes = self.prefs._migrate_if_needed(user, template)
        self.assertTrue(did_migrate)
        self.assertEqual(merged["future_block"], {"x": 1})
        self.assertIn("stale:future_block", notes)

    def test_never_raises_on_malformed_input(self):
        for cfg, template in (
            ([], {"_version": "6.5.0"}),
            ("not a dict", {"_version": "6.5.0"}),
            ({"_version": "6.5.0"}, {}),
            ({"enabled_hooks": 5}, {"_version": "6.5.0", "enabled_hooks": {"stop": True}}),
        ):
            with self.subTest(cfg=cfg):
                out, did, notes = self.prefs._migrate_if_needed(cfg, template)
                self.assertIsInstance(notes, list)

    def test_migration_is_idempotent_on_disk(self):
        """Second load of a just-migrated config must be a no-op."""
        with tempfile.TemporaryDirectory() as td:
            os.environ["CLAUDE_PLUGIN_DATA"] = td
            try:
                mod = _load_module()
                prefs = mod.UserPreferences(REPO)
                Path(td, "user_preferences.json").write_text(
                    json.dumps({"_version": "5.1.5", "version": "5.1.5",
                                "audio_theme": "custom",
                                "focus_flow": {"enabled": True}}),
                    encoding="utf-8",
                )
                first = prefs.load()
                self.assertEqual(first["audio_theme"], "custom")
                self.assertNotIn("focus_flow", first)
                # A .bak of the pre-migration state exists for recovery.
                self.assertTrue(Path(td, "user_preferences.json.bak").exists())
                after_first = Path(td, "user_preferences.json").read_bytes()
                prefs.load()
                self.assertEqual(
                    Path(td, "user_preferences.json").read_bytes(), after_first,
                    "second load rewrote the config — migration is not idempotent",
                )
            finally:
                os.environ.pop("CLAUDE_PLUGIN_DATA", None)


_RACING_LOADER = """
import importlib.util, os, sys, time
repo, start = sys.argv[1], float(sys.argv[2])
spec = importlib.util.spec_from_file_location(
    "user_preferences", os.path.join(repo, "hooks", "user_preferences.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
# Sleep to just before the start line, then spin — sleep alone jitters by up to
# ~15ms on Windows, which is wide enough for the winner to finish first and let
# the race quietly not happen.
time.sleep(max(0.0, start - time.time() - 0.02))
while time.time() < start:
    pass
m.UserPreferences(repo).load()
"""


class TestConcurrentMigrationBackup(unittest.TestCase):
    """The sibling .bak must hold PRE-migration content or nothing.

    Many hooks fire per turn, so the first load after an upgrade races N
    processes that all read the same stale config. Losers must not copy the
    already-migrated file over the winner's good backup — a .bak that is itself
    migrated looks like a recovery point and isn't one.
    """

    STALE = {
        "_version": "5.1.5",
        "version": "5.1.5",
        "audio_theme": "custom",
        "enabled_hooks": {"stop": True, "worktree_create": True},
        "focus_flow": {"enabled": True},
    }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.cfg_path = self.data_dir / "user_preferences.json"
        self.bak_path = self.data_dir / "user_preferences.json.bak"
        self.cfg_path.write_text(json.dumps(self.STALE), encoding="utf-8")
        self._saved = os.environ.get("CLAUDE_PLUGIN_DATA")
        os.environ["CLAUDE_PLUGIN_DATA"] = self.tmp.name

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        else:
            os.environ["CLAUDE_PLUGIN_DATA"] = self._saved
        self.tmp.cleanup()

    def _assert_bak_is_pre_migration(self):
        self.assertTrue(self.bak_path.exists(), "no .bak written for the migration")
        bak = json.loads(self.bak_path.read_text(encoding="utf-8"))
        self.assertEqual(
            bak["_version"], "5.1.5",
            ".bak holds post-migration content — it is not a recovery point",
        )
        self.assertIn("focus_flow", bak, ".bak lost the dropped block it exists to preserve")

    def _assert_config_migrated(self):
        disk = json.loads(self.cfg_path.read_text(encoding="utf-8"))
        template_v = self.mod.UserPreferences(REPO)._load_template()["_version"]
        self.assertEqual(disk["_version"], template_v)
        self.assertEqual(disk["audio_theme"], "custom")       # user value survived
        self.assertNotIn("focus_flow", disk)                  # dropped key gone
        self.assertNotIn("worktree_create", disk["enabled_hooks"])

    def test_loser_of_race_does_not_touch_bak(self):
        """Deterministic stand-in for the race: persist twice, second is a loser.

        Runs even when the OS never actually interleaves the processes below.
        """
        self.mod = _load_module()
        prefs = self.mod.UserPreferences(REPO)
        template = prefs._load_template()
        merged, did_migrate, _notes = prefs._migrate_if_needed(dict(self.STALE), template)
        self.assertTrue(did_migrate)

        prefs._persist_migration(merged, template)            # winner
        self._assert_bak_is_pre_migration()
        self._assert_config_migrated()
        bak_bytes = self.bak_path.read_bytes()
        cfg_bytes = self.cfg_path.read_bytes()

        result = prefs._persist_migration(merged, template)   # loser, same input
        self.assertEqual(self.bak_path.read_bytes(), bak_bytes,
                         "loser overwrote the pre-migration .bak")
        self.assertEqual(self.cfg_path.read_bytes(), cfg_bytes,
                         "loser rewrote an already-migrated config")
        self.assertEqual(result["audio_theme"], "custom")

    def test_racing_loaders_never_clobber_pre_migration_bak(self):
        """Eight real processes hitting load() on the same stale config.

        Cannot false-fail: if the OS serialises them the invariant still holds.
        Before the fix this reproduced every run.
        """
        self.mod = _load_module()
        child = self.data_dir / "_racing_loader.py"
        child.write_text(_RACING_LOADER, encoding="utf-8")
        start = time.time() + 1.0
        procs = [
            subprocess.Popen(
                [sys.executable, str(child), str(REPO), str(start)],
                env=dict(os.environ, CLAUDE_PLUGIN_DATA=self.tmp.name),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for _ in range(8)
        ]
        for p in procs:
            _out, err = p.communicate(timeout=120)
            self.assertEqual(p.returncode, 0, f"a racing loader raised:\n{err}")

        self._assert_bak_is_pre_migration()
        self._assert_config_migrated()
        self.assertEqual(
            [p.name for p in self.data_dir.glob("*.tmp.*")], [],
            "orphaned atomic-write temp file left behind",
        )

    def test_persist_never_raises_on_read_only_data_dir(self):
        """A read-only data dir must degrade to in-memory, not kill the hook."""
        self.mod = _load_module()
        prefs = self.mod.UserPreferences(REPO)
        template = prefs._load_template()
        merged, _did, _notes = prefs._migrate_if_needed(dict(self.STALE), template)

        def _boom(*_a, **_kw):
            raise OSError(13, "read-only file system")

        original = self.mod.UserPreferences._acquire_lock
        try:
            self.mod.UserPreferences._acquire_lock = _boom
            result = prefs._persist_migration(merged, template)
        finally:
            self.mod.UserPreferences._acquire_lock = original
        # Caller still gets a correct, fully migrated config.
        self.assertEqual(result["audio_theme"], "custom")
        self.assertNotIn("focus_flow", result)
        self.assertFalse(self.bak_path.exists())
        # And the on-disk config was left exactly as it was.
        self.assertEqual(json.loads(self.cfg_path.read_text(encoding="utf-8"))["_version"], "5.1.5")


if __name__ == "__main__":
    unittest.main()
