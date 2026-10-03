"""Tests for the silent-failure checks `audio-hooks diagnose` gained in 6.5.1.

Each of these codes exists because the condition it names produced a healthy
`diagnose` on a real install while the user heard nothing at all. They are
therefore tested against synthetic configs rather than the live machine.
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "bin" / "audio-hooks.py"


def _load_cli():
    sys.modules.pop("audio_hooks_cli", None)
    spec = importlib.util.spec_from_file_location("audio_hooks_cli", CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["audio_hooks_cli"] = module
    spec.loader.exec_module(module)
    return module


class TestCompletionSignal(unittest.TestCase):
    def setUp(self):
        self.cli = _load_cli()

    def test_all_three_off_reports_no_signal(self):
        cfg = {"enabled_hooks": {"stop": False, "subagent_stop": False, "notification": False}}
        result = self.cli._check_completion_signal(cfg)
        self.assertFalse(result["any_enabled"])

    def test_the_shape_that_caused_the_bug(self):
        # `hooks enable-only notification permission_request` leaves notification
        # on, which IS a completion signal via idle_prompt. Muting notification
        # too is what leaves a user with nothing.
        cfg = {"enabled_hooks": {"stop": False, "notification": True, "permission_request": True}}
        self.assertTrue(self.cli._check_completion_signal(cfg)["any_enabled"])

        cfg["enabled_hooks"]["notification"] = False
        self.assertFalse(self.cli._check_completion_signal(cfg)["any_enabled"])

    def test_absent_key_counts_as_on(self):
        # Mirrors hook_runner's built-in default set: stop and notification are
        # on unless explicitly disabled.
        result = self.cli._check_completion_signal({"enabled_hooks": {}})
        self.assertTrue(result["any_enabled"])
        self.assertIn("stop", result["enabled"])

    def test_malformed_enabled_hooks_does_not_warn(self):
        # Never turn a broken config into a second, misleading warning.
        self.assertTrue(self.cli._check_completion_signal({"enabled_hooks": []})["any_enabled"])
        self.assertTrue(self.cli._check_completion_signal({})["any_enabled"])


class TestPrefsSchemaStale(unittest.TestCase):
    def setUp(self):
        self.cli = _load_cli()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "user_preferences.json"
        self.addCleanup(self.tmp.cleanup)

    def _check(self, payload):
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.object(self.cli, "_config_path", return_value=self.path):
            return self.cli._check_prefs_schema()

    def test_version_behind_project_is_stale(self):
        result = self._check({"_version": "5.1.5"})
        self.assertTrue(result["stale"])
        self.assertEqual(result["config_version"], "5.1.5")

    def test_retired_key_is_stale_even_at_current_version(self):
        # focus_flow was removed in 6.0.0; its presence proves no migration ran.
        result = self._check({"_version": self.cli.PROJECT_VERSION, "focus_flow": {}})
        self.assertTrue(result["stale"])
        self.assertEqual(result["retired_keys"], ["focus_flow"])

    def test_current_config_is_not_stale(self):
        self.assertFalse(self._check({"_version": self.cli.PROJECT_VERSION})["stale"])

    def test_reads_disk_not_the_overlaid_config(self):
        # _load_config_raw() overlays the template, so its _version always
        # matches PROJECT_VERSION. Reading that instead of the file would make
        # this check structurally incapable of ever firing.
        self.path.write_text(json.dumps({"_version": "5.1.5"}), encoding="utf-8")
        with mock.patch.object(self.cli, "_config_path", return_value=self.path), \
                mock.patch.object(self.cli, "_load_config_raw",
                                  return_value={"_version": self.cli.PROJECT_VERSION}):
            self.assertTrue(self.cli._check_prefs_schema()["stale"])

    def test_unreadable_config_does_not_warn(self):
        self.path.write_text("{ not json", encoding="utf-8")
        with mock.patch.object(self.cli, "_config_path", return_value=self.path):
            self.assertFalse(self.cli._check_prefs_schema()["stale"])


class TestStalePluginRecord(unittest.TestCase):
    def setUp(self):
        self.cli = _load_cli()
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.installed = self.home / ".claude" / "plugins" / "installed_plugins.json"
        self.installed.parent.mkdir(parents=True, exist_ok=True)

    def _check(self, payload):
        self.installed.write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.object(self.cli.Path, "home", staticmethod(lambda: self.home)):
            return self.cli._check_plugin_record()

    def _record(self, version, install_path):
        return {"plugins": {"audio-hooks@chanmeng-audio-hooks": [
            {"scope": "user", "version": version, "installPath": str(install_path)}]}}

    def test_version_drift_is_stale(self):
        result = self._check(self._record("6.3.4", self.home))
        self.assertTrue(result["stale"])
        self.assertTrue(result["records"][0]["version_drift"])

    def test_missing_install_path_is_stale(self):
        # anthropics/claude-code#90135: the pinned path is deleted underneath
        # live sessions and their plugin hooks stop firing with no error.
        result = self._check(self._record(self.cli.PROJECT_VERSION, self.home / "gone"))
        self.assertTrue(result["stale"])
        self.assertFalse(result["records"][0]["path_exists"])

    def test_matching_record_is_not_stale(self):
        result = self._check(self._record(self.cli.PROJECT_VERSION, self.home))
        self.assertFalse(result["stale"])

    def test_other_plugins_are_ignored(self):
        payload = {"plugins": {"something-else@market": [
            {"scope": "user", "version": "0.0.1", "installPath": str(self.home / "gone")}]}}
        result = self._check(payload)
        self.assertEqual(result["records"], [])
        self.assertFalse(result["stale"])

    def test_absent_file_does_not_warn(self):
        self.assertFalse(self.installed.exists())
        with mock.patch.object(self.cli.Path, "home", staticmethod(lambda: self.home)):
            self.assertFalse(self.cli._check_plugin_record().get("stale"))


class TestHookShell(unittest.TestCase):
    def setUp(self):
        self.cli = _load_cli()

    def test_non_windows_is_not_applicable(self):
        with mock.patch.object(self.cli.platform, "system", return_value="Linux"):
            self.assertFalse(self.cli._check_hook_shell()["applicable"])

    def test_windows_without_git_bash_is_unavailable(self):
        with mock.patch.object(self.cli.platform, "system", return_value="Windows"), \
                mock.patch.object(self.cli.shutil, "which", return_value=None):
            result = self.cli._check_hook_shell()
            self.assertTrue(result["applicable"])
            self.assertFalse(result["available"])

    def test_windows_with_git_bash_is_available(self):
        with mock.patch.object(self.cli.platform, "system", return_value="Windows"), \
                mock.patch.object(self.cli.shutil, "which", return_value="C:/Git/bin/bash.exe"):
            self.assertTrue(self.cli._check_hook_shell()["available"])


class TestTerminalSequenceInert(unittest.TestCase):
    """v6.5.0 shipped terminalSequence on hooks that can never emit it."""

    def setUp(self):
        self.cli = _load_cli()

    def test_enabled_is_inert(self):
        cfg = {"notification_settings": {"terminal_sequence": {"enabled": True}}}
        self.assertTrue(self.cli._check_terminal_sequence_inert(cfg)["inert"])

    def test_disabled_is_not_reported(self):
        cfg = {"notification_settings": {"terminal_sequence": {"enabled": False}}}
        self.assertFalse(self.cli._check_terminal_sequence_inert(cfg)["inert"])

    def test_absent_block_is_not_reported(self):
        # The shipped default, and what a config predating v6.5.0 looks like.
        self.assertFalse(self.cli._check_terminal_sequence_inert({})["inert"])
        self.assertFalse(
            self.cli._check_terminal_sequence_inert({"notification_settings": {}})["inert"])

    def test_malformed_block_does_not_crash(self):
        cfg = {"notification_settings": {"terminal_sequence": "yes"}}
        self.assertFalse(self.cli._check_terminal_sequence_inert(cfg)["inert"])


if __name__ == "__main__":
    unittest.main()
