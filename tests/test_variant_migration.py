"""A variant added after a user said "only these" must not become audible.

A variant key that is absent inherits its parent. ``hooks enable-only
stop_failure_rate_limit`` leaves the parent ``stop_failure`` true and every
sibling variant that existed at the time explicitly false, so the two StopFailure
variants added in 6.7 had no key and inherited ``true``: a user who asked for
rate-limit failures only started hearing them. A per-variant default of false is
the wrong fix (it would silence them for someone who ran ``hooks enable
stop_failure`` and wants everything), so the preferences migration decides from
the shape of the stored file. These tests drive it with configs produced by the
real ``hooks`` subcommands.
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

try:
    from test_read_only_commands import _Base
except ImportError:
    from tests.test_read_only_commands import _Base

REPO = Path(__file__).resolve().parent.parent
NEW_STOP_FAILURE = ("stop_failure_cloud_credential_error", "stop_failure_verification_required")
NEW_NOTIFICATION = "notification_auth_storage_failure"


def _load_hook_runner():
    sys.modules.pop("hook_runner", None)
    spec = importlib.util.spec_from_file_location("hook_runner", REPO / "hooks" / "hook_runner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestVariantMigration(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.prefs = self.cli._prefs()
        self.template = json.loads((REPO / "config" / "default_preferences.json").read_text(encoding="utf-8"))
        self.target = self.template["_version"]

    # -- helpers -----------------------------------------------------------

    def _v66(self, *hook_args) -> dict:
        """The config a 6.6 install would hold after the given `hooks` subcommand."""
        rc, doc = self._invoke(["hooks", *hook_args])
        self.assertEqual(rc, 0, doc)
        cfg = json.loads((self.data / "user_preferences.json").read_text(encoding="utf-8"))
        for name, (_parent, release) in self.prefs.VARIANT_INTRODUCED.items():
            if release == "6.7.0":
                cfg["enabled_hooks"].pop(name, None)  # 6.6 had never heard of them
        cfg["_version"] = cfg["version"] = "6.6.0"
        return cfg

    def _migrate(self, cfg: dict):
        return self.prefs._migrate_if_needed(copy.deepcopy(cfg), self.template)

    def _enabled(self, cfg: dict, hook: str, variant: str) -> bool:
        hr = _load_hook_runner()
        hr.load_config = lambda: cfg
        return hr.is_hook_enabled(hook, variant)

    # -- the rule ----------------------------------------------------------

    def test_enable_only_a_variant_keeps_new_siblings_off(self) -> None:
        cfg = self._v66("enable-only", "stop_failure_rate_limit")
        self.assertIs(cfg["enabled_hooks"]["stop_failure"], True)
        for name in NEW_STOP_FAILURE:  # the bug: absent key inherits the parent
            self.assertTrue(self._enabled(cfg, "stop_failure", name))
        migrated, did, notes = self._migrate(cfg)
        self.assertTrue(did)
        for name in NEW_STOP_FAILURE:
            self.assertIs(migrated["enabled_hooks"][name], False)
            self.assertIn("enabled_hooks." + name, notes)
            self.assertFalse(self._enabled(migrated, "stop_failure", name))
        self.assertTrue(self._enabled(migrated, "stop_failure", "stop_failure_rate_limit"))
        self.assertEqual(migrated["_version"], self.target)

    def test_enable_only_a_notification_variant_stays_off_too(self) -> None:
        cfg = self._v66("enable-only", "notification_idle_prompt")
        migrated, _, _ = self._migrate(cfg)
        self.assertIs(migrated["enabled_hooks"][NEW_NOTIFICATION], False)
        self.assertFalse(self._enabled(migrated, "notification", NEW_NOTIFICATION))
        self.assertTrue(self._enabled(migrated, "notification", "notification_idle_prompt"))
        # Without the rule it was already off by SYNTHETIC_VARIANT_DEFAULTS: same behaviour.
        self.assertFalse(self._enabled(cfg, "notification", NEW_NOTIFICATION))

    def test_enabling_the_whole_parent_leaves_new_variants_inheriting(self) -> None:
        cfg = self._v66("enable", "stop_failure")
        migrated, _, _ = self._migrate(cfg)
        for name in NEW_STOP_FAILURE:
            self.assertNotIn(name, migrated["enabled_hooks"])
            self.assertTrue(self._enabled(migrated, "stop_failure", name))

    def test_one_sibling_disabled_by_hand_is_not_an_enumeration(self) -> None:
        cfg = self._v66("enable", "stop_failure")
        rc, _ = self._invoke(["hooks", "disable", "stop_failure_rate_limit"])
        cfg["enabled_hooks"]["stop_failure_rate_limit"] = False
        migrated, _, _ = self._migrate(cfg)
        for name in NEW_STOP_FAILURE:
            self.assertNotIn(name, migrated["enabled_hooks"])
            self.assertTrue(self._enabled(migrated, "stop_failure", name))

    def test_a_disabled_parent_writes_nothing(self) -> None:
        cfg = self._v66("enable-only", "stop_failure_rate_limit")
        cfg["enabled_hooks"]["stop_failure"] = False
        migrated, _, notes = self._migrate(cfg)
        for name in NEW_STOP_FAILURE:
            self.assertNotIn(name, migrated["enabled_hooks"])
            self.assertFalse(self._enabled(migrated, "stop_failure", name))  # kill switch
        self.assertFalse([n for n in notes if n.startswith("enabled_hooks.stop_failure_")])

    def test_a_missing_parent_writes_nothing(self) -> None:
        cfg = self._v66("enable-only", "stop_failure_rate_limit")
        del cfg["enabled_hooks"]["stop_failure"]
        migrated, _, _ = self._migrate(cfg)
        # the template re-adds the parent as false; either way, no variant key appears
        for name in NEW_STOP_FAILURE:
            self.assertNotIn(name, migrated["enabled_hooks"])

    def test_migrating_twice_is_a_no_op(self) -> None:
        cfg = self._v66("enable-only", "stop_failure_rate_limit")
        once, did1, _ = self._migrate(cfg)
        twice, did2, notes2 = self.prefs._migrate_if_needed(copy.deepcopy(once), self.template)
        self.assertTrue(did1)
        self.assertFalse(did2)
        self.assertEqual(notes2, [])
        self.assertEqual(twice, once)

    def test_a_current_file_is_not_touched(self) -> None:
        rc, doc = self._invoke(["hooks", "enable-only", "stop_failure_rate_limit"])
        cfg = json.loads((self.data / "user_preferences.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["_version"], self.target)
        cfg["enabled_hooks"].pop(NEW_STOP_FAILURE[0])  # deliberately left out by hand
        migrated, did, _ = self._migrate(cfg)
        self.assertFalse(did)
        self.assertNotIn(NEW_STOP_FAILURE[0], migrated["enabled_hooks"])

    def test_a_file_without_a_usable_version_is_left_alone(self) -> None:
        cfg = self._v66("enable-only", "stop_failure_rate_limit")
        for bad in (None, "", "next", 6):
            with self.subTest(version=bad):
                broken = copy.deepcopy(cfg)
                broken["_version"] = bad
                migrated, _, _ = self._migrate(broken)
                for name in NEW_STOP_FAILURE:
                    self.assertNotIn(name, migrated["enabled_hooks"])

    def test_the_rule_runs_on_disk_via_a_state_change_and_in_memory_when_reading(self) -> None:
        cfg = self._v66("enable-only", "stop_failure_rate_limit")
        path = self.data / "user_preferences.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        before = path.read_bytes()
        rc, doc = self._invoke(["get", "enabled_hooks." + NEW_STOP_FAILURE[0]])  # read-only
        self.assertIs(doc["value"], False)
        self.assertEqual(path.read_bytes(), before)
        rc, doc = self._invoke(["theme", "set", "default"])  # state-changing: migrates and saves
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertIs(on_disk["enabled_hooks"][NEW_STOP_FAILURE[1]], False)


class TestVariantTable(_Base):
    def test_the_table_matches_the_registered_variants(self) -> None:
        hr = _load_hook_runner()
        table = self.cli._prefs().VARIANT_INTRODUCED
        self.assertEqual(set(table), set(hr.SYNTHETIC_EVENT_MAP),
                         "add a VARIANT_INTRODUCED row (parent, release) for every new variant")
        for name, (parent, release) in table.items():
            with self.subTest(variant=name):
                self.assertEqual(parent, hr.SYNTHETIC_EVENT_MAP[name][0])
                self.assertRegex(release, r"^\d+\.\d+\.\d+$")

    def test_no_variant_claims_a_release_later_than_the_template(self) -> None:
        prefs = self.cli._prefs()
        template = json.loads((REPO / "config" / "default_preferences.json").read_text(encoding="utf-8"))
        limit = prefs._parse_release(template["_version"])
        for name, (_p, release) in prefs.VARIANT_INTRODUCED.items():
            self.assertLessEqual(prefs._parse_release(release), limit, name)


if __name__ == "__main__":
    unittest.main()
