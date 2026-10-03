"""``audio-hooks migrate``: the explicit, idempotent way to bring preferences up to date.

PREFS_SCHEMA_STALE used to suggest ``audio-hooks get audio_theme`` as its remedy.
Once the reporting commands became read-only that command no longer migrates
anything, so an agent that ran the suggestion verbatim would loop. ``migrate``
is state-changing (it rewrites the file and keeps a sibling .bak), reports what
it did, is a no-op when the file is current, and never creates a missing file.
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import json

try:
    from test_read_only_commands import OLD_PREFS, _Base, _snapshot
except ImportError:
    from tests.test_read_only_commands import OLD_PREFS, _Base, _snapshot

import unittest


class TestMigrateFresh(_Base):
    def test_an_absent_file_is_reported_not_created(self) -> None:
        before = _snapshot(self.root)
        rc, doc = self._invoke(["migrate"])
        self.assertEqual(rc, 0, doc)
        self.assertTrue(doc["ok"])
        self.assertFalse(doc["exists"])
        self.assertFalse(doc["changed"])
        self.assertEqual(_snapshot(self.root), before)
        self.assertFalse(self.data.exists())

    def test_unknown_arguments_are_rejected_before_anything_runs(self) -> None:
        for tokens in (["migrate", "--dry-run"], ["migrate", "now"], ["migrate", "--force"]):
            with self.subTest(command=" ".join(tokens)):
                rc, doc = self._invoke(tokens)
                self.assertEqual(rc, 1)
                self.assertEqual(doc["error"]["code"], "INVALID_USAGE")
        self.assertFalse(self.data.exists())

    def test_help_does_not_migrate(self) -> None:
        rc, doc = self._invoke(["migrate", "--help"])
        self.assertEqual(rc, 0)
        self.assertEqual(doc["command"], "migrate")
        self.assertFalse(self.data.exists())

    def test_migrate_is_registered_everywhere_a_subcommand_is(self) -> None:
        self.assertIn("migrate", self.cli.DISPATCH)
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cli.cmd_manifest([])
        names = [e["name"] for e in json.loads(out.getvalue())["subcommands"]]
        self.assertIn("migrate", names)
        self.assertFalse(self.cli._is_read_only_invocation("migrate", []))


class TestMigrateOlderFile(_Base):
    older_prefs = True

    def test_migrates_once_and_reports_what_changed(self) -> None:
        rc, doc = self._invoke(["migrate"])
        self.assertEqual(rc, 0, doc)
        self.assertTrue(doc["changed"])
        self.assertTrue(doc["exists"])
        self.assertEqual(doc["from_version"], OLD_PREFS["_version"])
        self.assertNotEqual(doc["to_version"], OLD_PREFS["_version"])
        self.assertTrue(doc["added"], "an old file must gain keys")
        self.assertIsInstance(doc["removed"], list)
        self.assertIsInstance(doc["stale"], list)
        self.assertTrue(doc["backup"] and doc["backup"].endswith(".bak"))
        stored = json.loads((self.data / "user_preferences.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["_version"], doc["to_version"])
        self.assertEqual(stored["audio_theme"], "custom")  # the user's value survives
        self.assertIs(stored["enabled_hooks"]["stop"], False)
        bak = json.loads((self.data / "user_preferences.json.bak").read_text(encoding="utf-8"))
        self.assertEqual(bak["_version"], OLD_PREFS["_version"])  # pre-migration content

    def test_a_second_run_is_a_no_op(self) -> None:
        self._invoke(["migrate"])
        path = self.data / "user_preferences.json"
        before = _snapshot(self.root)
        rc, doc = self._invoke(["migrate"])
        self.assertEqual(rc, 0, doc)
        self.assertFalse(doc["changed"])
        self.assertEqual(doc["added"], [])
        self.assertEqual(doc["from_version"], doc["to_version"])
        self.assertEqual(_snapshot(self.root), before)
        self.assertTrue(path.exists())

    def test_the_diagnose_remedy_actually_clears_the_warning(self) -> None:
        """Run the suggested command verbatim, as an agent would."""
        def stale_warning():
            rc, doc = self._invoke(["diagnose"])
            return next((w for w in doc.get("warnings", []) if w["code"] == "PREFS_SCHEMA_STALE"), None)

        warning = stale_warning()
        self.assertIsNotNone(warning, "an older file must raise PREFS_SCHEMA_STALE")
        self.assertEqual(warning["suggested_command"], "audio-hooks migrate")
        tokens = warning["suggested_command"].split()[1:]
        rc, doc = self._invoke(tokens)
        self.assertEqual(rc, 0, doc)
        self.assertIsNone(stale_warning())

    def test_the_manifest_entry_points_at_migrate_too(self) -> None:
        self.assertEqual(self.cli.CLI_ERROR_CODES["PREFS_SCHEMA_STALE"]["suggested_command"], "audio-hooks migrate")


class TestMigrateCorruptFile(_Base):
    def test_an_unreadable_file_is_an_error_and_left_alone(self) -> None:
        self.data.mkdir()
        path = self.data / "user_preferences.json"
        for body in ("{not json", "[1, 2]"):
            with self.subTest(body=body):
                path.write_text(body, encoding="utf-8")
                before = _snapshot(self.root)
                rc, doc = self._invoke(["migrate"])
                self.assertEqual(rc, 1)
                self.assertEqual(doc["error"]["code"], "CONFIG_READ_ERROR")
                self.assertEqual(_snapshot(self.root), before)


if __name__ == "__main__":
    unittest.main()
