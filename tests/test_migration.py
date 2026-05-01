"""Tests for UserPreferences migration semantics.

Each row in the migration table from the spec is pinned here:
- _version / version / $schema / _comment* always overwrite from template
- Other top-level keys: user wins if present, template adopted if missing
- Nested dicts: recurse with same rules
- Lists: atomic — user list wins entirely (no element merge)
- Type mismatch (scalar vs scalar): keep user
- Type mismatch (scalar vs container): reset to template default
- User has key template doesn't: keep user
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
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


if __name__ == "__main__":
    unittest.main()
