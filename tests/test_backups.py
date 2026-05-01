"""Tests for save() and backup mechanics (sibling .bak + external rotation)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO / "hooks" / "user_preferences.py"


def _load_module():
    sys.modules.pop("user_preferences", None)
    spec = importlib.util.spec_from_file_location("user_preferences", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestSaveSibling(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake_home = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_PLUGIN_DATA"] = self.tmp.name
        self.mod = _load_module()
        self._original_home = self.mod.Path.home
        self.mod.Path.home = staticmethod(lambda: self.mod.Path(self.fake_home.name))

    def tearDown(self):
        self.mod.Path.home = self._original_home
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        self.tmp.cleanup()
        self.fake_home.cleanup()

    def test_save_writes_atomically(self):
        prefs = self.mod.UserPreferences(REPO)
        prefs.save({"_version": "5.1.5", "audio_theme": "custom"})
        on_disk = json.loads(Path(self.tmp.name, "user_preferences.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["audio_theme"], "custom")

    def test_save_creates_sibling_bak_after_second_save(self):
        prefs = self.mod.UserPreferences(REPO)
        prefs.save({"_version": "5.1.5", "audio_theme": "default"})
        prefs.save({"_version": "5.1.5", "audio_theme": "custom"})
        bak = Path(self.tmp.name, "user_preferences.json.bak")
        self.assertTrue(bak.exists(), "sibling .bak not created on second save")
        bak_content = json.loads(bak.read_text(encoding="utf-8"))
        self.assertEqual(bak_content["audio_theme"], "default")  # prior state

    def test_first_save_no_sibling_bak(self):
        """First save has no prior content — no backup written."""
        prefs = self.mod.UserPreferences(REPO)
        prefs.save({"_version": "5.1.5"})
        bak = Path(self.tmp.name, "user_preferences.json.bak")
        self.assertFalse(bak.exists())


class TestExternalBackups(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake_home = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_PLUGIN_DATA"] = self.tmp.name
        self.mod = _load_module()
        self._original_home = self.mod.Path.home
        self.mod.Path.home = staticmethod(lambda: self.mod.Path(self.fake_home.name))

    def tearDown(self):
        self.mod.Path.home = self._original_home
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        self.tmp.cleanup()
        self.fake_home.cleanup()

    def _external_dir(self) -> Path:
        return Path(self.fake_home.name) / ".claude-audio-hooks-backups" / "audio-hooks-chanmeng-audio-hooks"

    def test_external_backup_created_on_second_save(self):
        prefs = self.mod.UserPreferences(REPO)
        prefs.save({"_version": "5.1.5", "audio_theme": "default"})
        prefs.save({"_version": "5.1.5", "audio_theme": "custom"})
        files = list(self._external_dir().glob("*.json"))
        self.assertEqual(len(files), 1, f"Expected 1 external backup, got {[f.name for f in files]}")

    def test_dedup_skips_byte_identical_save(self):
        prefs = self.mod.UserPreferences(REPO)
        cfg = {"_version": "5.1.5", "audio_theme": "default"}
        prefs.save(cfg)
        prefs.save(cfg)  # identical
        prefs.save(cfg)  # identical again
        files = list(self._external_dir().glob("*.json"))
        self.assertEqual(len(files), 1, "byte-identical saves should not create new backups")

    def test_rotation_at_keep_limit(self):
        """Generate KEEP+5 saves with distinct content, expect KEEP files retained."""
        prefs = self.mod.UserPreferences(REPO)
        for i in range(self.mod.UserPreferences.EXTERNAL_BACKUP_KEEP + 5):
            prefs.save({"_version": "5.1.5", "iteration": i})
        files = list(self._external_dir().glob("*.json"))
        self.assertLessEqual(
            len(files),
            self.mod.UserPreferences.EXTERNAL_BACKUP_KEEP,
            f"Rotation failed: kept {len(files)} files",
        )


class TestBackupListAndRestore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake_home = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_PLUGIN_DATA"] = self.tmp.name
        self.mod = _load_module()
        self._original_home = self.mod.Path.home
        self.mod.Path.home = staticmethod(lambda: self.mod.Path(self.fake_home.name))

    def tearDown(self):
        self.mod.Path.home = self._original_home
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        self.tmp.cleanup()
        self.fake_home.cleanup()

    def test_list_backups_returns_newest_first(self):
        prefs = self.mod.UserPreferences(REPO)
        prefs.save({"_version": "5.1.5", "audio_theme": "default"})
        prefs.save({"_version": "5.1.5", "audio_theme": "custom"})
        prefs.save({"_version": "5.1.5", "audio_theme": "voice"})
        entries = prefs.list_backups()
        self.assertGreater(len(entries), 0)
        # First should be most recent by mtime
        for i in range(len(entries) - 1):
            self.assertGreaterEqual(entries[i]["mtime_iso"], entries[i + 1]["mtime_iso"])

    def test_restore_from_latest_round_trips(self):
        prefs = self.mod.UserPreferences(REPO)
        prefs.save({"_version": "5.1.5", "audio_theme": "default"})
        prefs.save({"_version": "5.1.5", "audio_theme": "custom"})
        # Now restore latest (which is the saved-content of the prior save = "default")
        restored = prefs.restore_from("latest-external")
        self.assertEqual(restored["audio_theme"], "default")

    def test_filename_id_round_trip(self):
        prefs = self.mod.UserPreferences(REPO)
        ids = [
            "2026-05-01T07:42:13.041Z",
            "2026-12-31T23:59:59.999Z",
            "2026-01-01T00:00:00.000Z",
        ]
        for original in ids:
            fname = prefs._id_to_filename(original)
            recovered = prefs._filename_to_id(fname)
            self.assertEqual(recovered, original, f"round-trip failed for {original}")
            self.assertNotIn(":", fname, "filename must not contain : (Windows-incompatible)")


if __name__ == "__main__":
    unittest.main()
