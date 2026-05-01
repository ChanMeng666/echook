"""Tests for UserPreferences path resolution chain.

Pins the 6-level priority documented in the spec:
  1. CLAUDE_PLUGIN_DATA env
  2. CLAUDE_AUDIO_HOOKS_DATA env
  3. plugin-cache layout (script under <plugin>/.claude-plugin/plugin.json)
  4. shared dir ~/.claude/plugins/data/audio-hooks-chanmeng-audio-hooks/
     IF user_preferences.json exists there (5.1.4 anti-stranding fix —
     regression-tested explicitly)
  5. cursor-native dir ~/.cursor/audio-hooks-data/
     IF user_preferences.json exists there
  6. legacy temp dir <TEMP>/claude_audio_hooks_queue/
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO / "hooks" / "user_preferences.py"


def _load_module():
    """Import user_preferences.py fresh each test for isolation."""
    sys.modules.pop("user_preferences", None)
    spec = importlib.util.spec_from_file_location("user_preferences", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPathResolution(unittest.TestCase):
    def setUp(self):
        self._saved = {
            k: os.environ.pop(k, None)
            for k in (
                "CLAUDE_PLUGIN_DATA",
                "CLAUDE_AUDIO_HOOKS_DATA",
                "CLAUDE_PLUGIN_ROOT",
                "CURSOR_VERSION",
            )
        }

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_claude_plugin_data_wins(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["CLAUDE_PLUGIN_DATA"] = td
            mod = _load_module()
            prefs = mod.UserPreferences(REPO)
            self.assertEqual(str(prefs.data_dir), td)
            self.assertEqual(str(prefs.config_path), str(Path(td) / "user_preferences.json"))

    def test_audio_hooks_data_when_no_plugin_data(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["CLAUDE_AUDIO_HOOKS_DATA"] = td
            mod = _load_module()
            prefs = mod.UserPreferences(REPO)
            self.assertEqual(str(prefs.data_dir), td)

    def test_shared_dir_used_when_user_prefs_exists_there(self):
        """5.1.4 anti-stranding regression test."""
        with tempfile.TemporaryDirectory() as fake_home:
            shared = (
                Path(fake_home)
                / ".claude" / "plugins" / "data"
                / "audio-hooks-chanmeng-audio-hooks"
            )
            shared.mkdir(parents=True)
            (shared / "user_preferences.json").write_text(
                '{"audio_theme": "custom"}', encoding="utf-8"
            )
            mod = _load_module()
            original_home = mod.Path.home
            try:
                mod.Path.home = staticmethod(lambda: mod.Path(fake_home))
                prefs = mod.UserPreferences(REPO)
                self.assertEqual(prefs.data_dir, shared)
            finally:
                mod.Path.home = original_home

    def test_cursor_native_used_when_shared_absent(self):
        with tempfile.TemporaryDirectory() as fake_home:
            cursor = Path(fake_home) / ".cursor" / "audio-hooks-data"
            cursor.mkdir(parents=True)
            (cursor / "user_preferences.json").write_text(
                '{"audio_theme": "default"}', encoding="utf-8"
            )
            mod = _load_module()
            original_home = mod.Path.home
            try:
                mod.Path.home = staticmethod(lambda: mod.Path(fake_home))
                prefs = mod.UserPreferences(REPO)
                self.assertEqual(prefs.data_dir, cursor)
            finally:
                mod.Path.home = original_home

    def test_legacy_temp_fallback(self):
        with tempfile.TemporaryDirectory() as fake_home:
            mod = _load_module()
            original_home = mod.Path.home
            try:
                mod.Path.home = staticmethod(lambda: mod.Path(fake_home))
                prefs = mod.UserPreferences(REPO)
                resolved = prefs.data_dir
                self.assertTrue(
                    str(resolved).endswith("claude_audio_hooks_queue"),
                    f"Expected legacy temp fallback, got {resolved}",
                )
            finally:
                mod.Path.home = original_home

    def test_queue_dir_and_log_dir_subdirs_of_data_dir(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["CLAUDE_PLUGIN_DATA"] = td
            mod = _load_module()
            prefs = mod.UserPreferences(REPO)
            self.assertEqual(prefs.queue_dir, Path(td) / "queue")
            self.assertEqual(prefs.log_dir, Path(td) / "logs")

    def test_legacy_temp_data_dir_does_not_double_nest_queue(self):
        """When fallback returns .../claude_audio_hooks_queue, queue_dir
        must not become .../claude_audio_hooks_queue/queue (preserves
        v5.1.3 layout for legacy script-install users)."""
        with tempfile.TemporaryDirectory() as fake_home:
            mod = _load_module()
            original_home = mod.Path.home
            try:
                mod.Path.home = staticmethod(lambda: mod.Path(fake_home))
                prefs = mod.UserPreferences(REPO)
                self.assertTrue(str(prefs.queue_dir).endswith("claude_audio_hooks_queue"))
                self.assertFalse(str(prefs.queue_dir).endswith("claude_audio_hooks_queue/queue"))
                self.assertFalse(str(prefs.queue_dir).endswith("claude_audio_hooks_queue\\queue"))
            finally:
                mod.Path.home = original_home


class TestPluginOverlay(unittest.TestCase):
    """CLAUDE_PLUGIN_OPTION_* env vars overlay onto loaded config."""

    def setUp(self):
        self._saved = {
            k: os.environ.pop(k, None)
            for k in (
                "CLAUDE_PLUGIN_DATA",
                "CLAUDE_PLUGIN_OPTION_AUDIO_THEME",
                "CLAUDE_PLUGIN_OPTION_WEBHOOK_URL",
                "CLAUDE_PLUGIN_OPTION_TTS_ENABLED",
            )
        }

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_plugin_option_overlays_audio_theme(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["CLAUDE_PLUGIN_DATA"] = td
            os.environ["CLAUDE_PLUGIN_OPTION_AUDIO_THEME"] = "custom"
            (Path(td) / "user_preferences.json").write_text(
                json.dumps({"audio_theme": "default", "_version": "5.1.5"}),
                encoding="utf-8",
            )
            mod = _load_module()
            prefs = mod.UserPreferences(REPO)
            cfg = prefs.load()
            self.assertEqual(cfg["audio_theme"], "custom")  # overlay wins

    def test_plugin_option_dotted_keys(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["CLAUDE_PLUGIN_DATA"] = td
            os.environ["CLAUDE_PLUGIN_OPTION_WEBHOOK_URL"] = "https://example.com/hook"
            (Path(td) / "user_preferences.json").write_text(
                json.dumps({"_version": "5.1.5", "webhook_settings": {"url": ""}}),
                encoding="utf-8",
            )
            mod = _load_module()
            prefs = mod.UserPreferences(REPO)
            cfg = prefs.load()
            self.assertEqual(cfg["webhook_settings"]["url"], "https://example.com/hook")

    def test_load_auto_inits_from_template_when_missing(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["CLAUDE_PLUGIN_DATA"] = td
            mod = _load_module()
            prefs = mod.UserPreferences(REPO)
            cfg = prefs.load()
            # default_preferences.json has audio_theme: "default" (post-revert
            # baseline; will become subagent_stop=false after Phase 4 too)
            self.assertEqual(cfg["audio_theme"], "default")
            self.assertTrue((Path(td) / "user_preferences.json").exists())


class TestSingletonAndAccessors(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_PLUGIN_DATA"] = self.tmp.name
        self.mod = _load_module()
        # Reset singleton between tests
        self.mod._reset_prefs()

    def tearDown(self):
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        self.tmp.cleanup()

    def test_get_prefs_returns_singleton(self):
        a = self.mod.get_prefs(REPO)
        b = self.mod.get_prefs(REPO)
        self.assertIs(a, b)

    def test_reset_prefs_clears_singleton(self):
        a = self.mod.get_prefs(REPO)
        self.mod._reset_prefs()
        b = self.mod.get_prefs(REPO)
        self.assertIsNot(a, b)

    def test_get_dotted_walks_nested_dict(self):
        prefs = self.mod.UserPreferences(REPO)
        prefs.save({
            "_version": "5.1.5",
            "audio_theme": "custom",
            "webhook_settings": {"url": "https://x.example"},
        })
        self.assertEqual(prefs.get_dotted("audio_theme"), "custom")
        self.assertEqual(prefs.get_dotted("webhook_settings.url"), "https://x.example")
        self.assertIsNone(prefs.get_dotted("nonexistent.key"))

    def test_set_dotted_persists_via_save(self):
        prefs = self.mod.UserPreferences(REPO)
        prefs.save({"_version": "5.1.5", "audio_theme": "default"})
        prefs.set_dotted("audio_theme", "custom")
        # Round-trip via load
        cfg = prefs.load()
        self.assertEqual(cfg["audio_theme"], "custom")

    def test_diff_from_default_excludes_template_matching_values(self):
        prefs = self.mod.UserPreferences(REPO)
        template = prefs._load_template()
        # Save the template verbatim
        prefs.save(template)
        diff = prefs.diff_from_default()
        # Primary assertion: diff must be empty when user matches template
        self.assertEqual(diff, {}, f"Expected empty diff when user matches template; got: {diff}")
        # Defense-in-depth: even if non-empty, no metadata/comment fields leak
        for k in diff:
            self.assertFalse(k.startswith("_"), f"diff includes comment field {k}")
            self.assertNotIn(k, ("_version", "version", "$schema"))


if __name__ == "__main__":
    unittest.main()
