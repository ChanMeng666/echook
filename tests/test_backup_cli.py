"""Tests for `audio-hooks backup *` CLI subcommands."""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "audio-hooks.py"


def _run_cli(args, *, env_extra=None, cwd=None):
    env = os.environ.copy()
    for k in ("CLAUDE_PLUGIN_DATA", "CLAUDE_AUDIO_HOOKS_DATA", "CLAUDE_PLUGIN_ROOT"):
        env.pop(k, None)
    if env_extra:
        env.update(env_extra)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)] + list(args),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=cwd, timeout=15,
    )
    return proc.returncode, proc.stdout, proc.stderr


class TestBackupCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake_home = tempfile.TemporaryDirectory()
        self.env = {
            "CLAUDE_PLUGIN_DATA": self.tmp.name,
            # Force HOME for backup external dir resolution
            "HOME": self.fake_home.name,
            "USERPROFILE": self.fake_home.name,
        }

    def tearDown(self):
        self.tmp.cleanup()
        self.fake_home.cleanup()

    def _seed_two_saves(self):
        """Generate two saves so we have a sibling .bak + 1 external backup."""
        rc, _, _ = _run_cli(["set", "audio_theme", "default"], env_extra=self.env)
        self.assertEqual(rc, 0)
        rc, _, _ = _run_cli(["set", "audio_theme", "custom"], env_extra=self.env)
        self.assertEqual(rc, 0)

    def test_backup_list_returns_json_array(self):
        self._seed_two_saves()
        rc, out, _ = _run_cli(["backup", "list"], env_extra=self.env)
        self.assertEqual(rc, 0)
        doc = json.loads(out.strip())
        self.assertTrue(doc["ok"])
        self.assertIsInstance(doc["backups"], list)
        self.assertGreater(len(doc["backups"]), 0)
        for entry in doc["backups"]:
            self.assertIn("id", entry)
            self.assertIn("location", entry)
            self.assertIn(entry["location"], ("sibling", "external"))

    def test_backup_show_emits_full_content(self):
        self._seed_two_saves()
        rc, list_out, _ = _run_cli(["backup", "list"], env_extra=self.env)
        backups = json.loads(list_out.strip())["backups"]
        first_id = backups[0]["id"]
        rc, out, _ = _run_cli(["backup", "show", first_id], env_extra=self.env)
        self.assertEqual(rc, 0)
        doc = json.loads(out.strip())
        self.assertTrue(doc["ok"])
        self.assertIn("content", doc)
        self.assertIn("audio_theme", doc["content"])

    def test_backup_restore_round_trips(self):
        self._seed_two_saves()
        # We just changed default→custom. Restoring latest-external (which
        # captured the pre-second-save = "default" state) should put us back.
        rc, out, _ = _run_cli(["backup", "restore", "latest-external"], env_extra=self.env)
        self.assertEqual(rc, 0)
        doc = json.loads(out.strip())
        self.assertTrue(doc["ok"])
        # Verify by reading current
        rc, status_out, _ = _run_cli(["status"], env_extra=self.env)
        status = json.loads(status_out.strip())
        self.assertEqual(status["theme"], "default")

    def test_backup_restore_nonexistent_returns_error(self):
        self._seed_two_saves()
        rc, out, _ = _run_cli(["backup", "restore", "9999-01-01T00:00:00.000Z"], env_extra=self.env)
        self.assertNotEqual(rc, 0)
        doc = json.loads(out.strip())
        self.assertFalse(doc["ok"])
        self.assertEqual(doc["error"]["code"], "BACKUP_NOT_FOUND")

    def test_backup_prune_idempotent(self):
        self._seed_two_saves()
        rc, out, _ = _run_cli(["backup", "prune"], env_extra=self.env)
        self.assertEqual(rc, 0)
        doc = json.loads(out.strip())
        self.assertTrue(doc["ok"])
        # Run again — still ok, removed=0
        rc, out, _ = _run_cli(["backup", "prune"], env_extra=self.env)
        doc = json.loads(out.strip())
        self.assertEqual(doc["removed"], 0)


if __name__ == "__main__":
    unittest.main()
