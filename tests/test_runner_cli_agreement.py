"""The CLI reports what the runner will do, because both run the same code.

Three things used to be written twice, once in ``hooks/hook_runner.py`` and
once in ``bin/audio-hooks.py``:

* the hooks that are on by default  -> ``hook_runner.DEFAULT_ENABLED_HOOKS``
* the enable precedence             -> ``hook_runner.resolve_hook_enabled``
* reading the snooze marker         -> ``hook_runner.read_snooze_until``

The CLI's copy of the precedence had drifted: it ignored a non-boolean value
(the runner treats anything but ``true`` as off) and gave a variant its
parent's *catalog* default when the parent was explicitly ``true`` (the runner
follows the parent). These tests pin CLI == runner for booleans, missing keys
and non-boolean values, with and without a parent kill switch -- in process,
and end to end against the real runner, which is spawned with a pinned and
snoozed data dir: the enabled check runs before the snooze check, so a row
logged ``SNOOZED`` was enabled and one logged ``DISABLED`` was not, and no
sound is ever played.
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "bin" / "audio-hooks.py"
HOOK_RUNNER = REPO / "hooks" / "hook_runner.py"
TEMPLATE = REPO / "config" / "default_preferences.json"

# enabled_hooks mappings, as stored. Keys absent here are simply absent.
CONFIGS: Dict[str, Dict[str, Any]] = {
    "empty": {},
    "booleans": {"stop": False, "notification": True, "stop_failure": True, "pretooluse": True},
    "kill_switch": {"notification": False, "stop_failure": False},
    "kill_switch_with_explicit_variants": {
        "notification": False, "notification_idle_prompt": True, "notification_agent_completed": True,
        "stop_failure": False, "stop_failure_rate_limit": True, "stop_failure_unknown": False,
    },
    "parent_on_variants_mixed": {
        "stop_failure": True, "stop_failure_rate_limit": False,
        "session_start": True, "notification": True, "notification_push_notification": True,
    },
    "non_boolean_parents": {"stop": "yes", "notification": 1, "permission_request": None,
                            "stop_failure": "true", "session_start": 0, "precompact": [True]},
    "non_boolean_variants": {
        "notification_idle_prompt": "true", "notification_permission_prompt": 1,
        "notification_agent_completed": None, "stop_failure": True, "stop_failure_rate_limit": "on",
        "session_start_resume": 0,
    },
    "non_boolean_variant_under_kill_switch": {"notification": False, "notification_idle_prompt": "true",
                                              "stop_failure": False, "stop_failure_unknown": 1},
    "non_boolean_parent_with_explicit_variant": {"notification": "off", "notification_idle_prompt": True,
                                                 "notification_auth_success": False},
}

# Hand-written expectations, so the comparison below is not only "the CLI and
# the runner agree" (they share the function) but also "on the right answer".
EXPECTED = {
    "empty": {"stop": True, "notification": True, "permission_request": True, "pretooluse": False,
              "notification_idle_prompt": True, "notification_agent_completed": False,
              "stop_failure_rate_limit": False},
    "booleans": {"stop": False, "pretooluse": True, "stop_failure": True,
                 # the parent is explicitly on and the variant says nothing: it follows the parent
                 "stop_failure_rate_limit": True, "notification_idle_prompt": True,
                 "notification_agent_completed": False},
    "kill_switch": {"notification": False, "notification_idle_prompt": False,
                    "notification_agent_completed": False, "stop_failure_rate_limit": False},
    "kill_switch_with_explicit_variants": {"notification_idle_prompt": True, "notification_agent_completed": True,
                                           "notification_permission_prompt": False,
                                           "stop_failure_rate_limit": True, "stop_failure_unknown": False},
    "parent_on_variants_mixed": {"stop_failure_rate_limit": False, "stop_failure_unknown": True,
                                 "session_start_resume": True, "notification_push_notification": True,
                                 "notification_computer_use_enter": False},
    # anything but the boolean true is off, for a parent ...
    "non_boolean_parents": {"stop": False, "notification": False, "permission_request": False,
                            "stop_failure": False, "session_start": False, "precompact": False,
                            # ... and for the variants that inherit it
                            "notification_idle_prompt": False, "stop_failure_rate_limit": False,
                            "session_start_resume": False, "precompact_auto": False,
                            "notification_agent_completed": False},
    # a variant key that is present but not true is off, whatever the parent says
    "non_boolean_variants": {"notification_idle_prompt": False, "notification_permission_prompt": False,
                             "notification_agent_completed": False, "stop_failure_rate_limit": False,
                             "stop_failure_unknown": True, "session_start_resume": False},
    "non_boolean_variant_under_kill_switch": {"notification_idle_prompt": False, "stop_failure_unknown": False,
                                              "notification": False},
    "non_boolean_parent_with_explicit_variant": {"notification": False, "notification_idle_prompt": True,
                                                 "notification_auth_success": False,
                                                 "notification_permission_prompt": False},
}


def _load_cli():
    sys.modules.pop("audio_hooks_cli", None)
    for name in ("hook_runner", "invoker", "user_preferences"):
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location("audio_hooks_cli", CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["audio_hooks_cli"] = module
    spec.loader.exec_module(module)
    return module


class TestOneDefinition(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cli = _load_cli()
        cls.hr = cls.cli.HR

    def test_default_set_is_the_runners_and_matches_the_catalog(self) -> None:
        self.assertEqual(self.hr.DEFAULT_ENABLED_HOOKS, frozenset({"notification", "stop", "permission_request"}))
        catalog_defaults = {h["name"] for h in self.cli.HOOK_CATALOG if h["default"]}
        self.assertEqual(catalog_defaults, set(self.hr.DEFAULT_ENABLED_HOOKS))

    def test_cli_source_restates_none_of_the_three(self) -> None:
        source = CLI.read_text(encoding="utf-8")
        self.assertNotIn("_DEFAULT_ENABLED_HOOKS", source)
        self.assertNotRegex(source, r'\(\s*"notification",\s*"stop",\s*"permission_request"\s*\)')
        self.assertNotIn("parent kill switch (rule 2)", source)
        self.assertEqual(source.count("resolve_hook_enabled("), 1)
        self.assertEqual(source.count("read_snooze_until("), 1)
        self.assertNotRegex(source, r"float\(sf\.read_text")
        runner = HOOK_RUNNER.read_text(encoding="utf-8")
        self.assertEqual(len([l for l in runner.splitlines() if '"permission_request"' in l and "frozenset" in l]), 1)


class TestEnabledDecisionAgrees(unittest.TestCase):
    """In process: every row ``hooks list --variants`` builds, against is_hook_enabled."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cli = _load_cli()
        cls.hr = cls.cli.HR

    def rows(self, enabled_hooks: Dict[str, Any]) -> List[Dict[str, Any]]:
        with mock.patch.object(self.cli, "_load_config_raw", return_value={"enabled_hooks": enabled_hooks}):
            return self.cli._hooks_state(include_variants=True)

    def runner_says(self, enabled_hooks: Dict[str, Any], hook: str, variant: Optional[str]) -> bool:
        with mock.patch.object(self.hr, "load_config", return_value={"enabled_hooks": enabled_hooks}):
            return self.hr.is_hook_enabled(hook, variant)

    def test_every_row_equals_the_runners_decision(self) -> None:
        self.assertEqual(set(CONFIGS), set(EXPECTED))
        for label, enabled_hooks in CONFIGS.items():
            rows = self.rows(enabled_hooks)
            self.assertEqual(len(rows), len(self.cli.HOOK_CATALOG) + len(self.hr.SYNTHETIC_EVENT_MAP))
            for row in rows:
                hook = row["variant_of"] if row["is_variant"] else row["name"]
                variant = row["name"] if row["is_variant"] else None
                with self.subTest(config=label, row=row["name"]):
                    self.assertIs(row["enabled"], self.runner_says(enabled_hooks, hook, variant))

    def test_the_decisions_are_the_expected_ones(self) -> None:
        for label, expected in EXPECTED.items():
            by_name = {r["name"]: r["enabled"] for r in self.rows(CONFIGS[label])}
            for name, want in expected.items():
                with self.subTest(config=label, row=name):
                    self.assertIs(by_name[name], want)

    def test_completion_signal_check_uses_the_same_rule(self) -> None:
        check = self.cli._check_completion_signal
        self.assertEqual(check({"enabled_hooks": {}})["enabled"], ["stop", "notification"])
        self.assertEqual(check({"enabled_hooks": {"stop": "yes", "notification": 1}})["enabled"], [])
        self.assertEqual(check({"enabled_hooks": {"stop": False, "subagent_stop": True}})["enabled"],
                         ["subagent_stop", "notification"])


class TestEndToEnd(unittest.TestCase):
    """The stored file, the real CLI and the real runner."""

    def setUp(self) -> None:
        self.state = Path(tempfile.mkdtemp(prefix="echook-agreement-"))
        self.addCleanup(lambda: shutil.rmtree(self.state, ignore_errors=True))
        env = os.environ.copy()
        for k in ("CLAUDE_PLUGIN_DATA", "PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT",
                  "CLAUDE_AUDIO_HOOKS_DATA", "CURSOR_VERSION", "CLAUDE_HOOKS_DEBUG"):
            env.pop(k, None)
        env["PYTHONIOENCODING"] = "utf-8"
        self.env = _isolation.pin_data_dir(env, self.state)

    def store(self, enabled_hooks: Dict[str, Any]) -> None:
        config = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        # Only what the case says: the template's own canonical keys would turn
        # every "missing key" row into an explicit one.
        config["enabled_hooks"] = dict(enabled_hooks)
        (self.state / "user_preferences.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

    def cli(self, *args: str) -> Dict[str, Any]:
        proc = subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=self.env, cwd=str(REPO), timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        return json.loads(proc.stdout)

    def runner_fires(self, arg: str) -> bool:
        log = self.state / "logs" / "events.ndjson"
        before = len(log.read_text(encoding="utf-8").splitlines()) if log.exists() else 0
        proc = subprocess.run([sys.executable, str(HOOK_RUNNER), arg], input="{}", capture_output=True,
                              text=True, encoding="utf-8", errors="replace", env=self.env, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()[before:] if l.strip()]
        statuses = [r["status"] for r in rows if r.get("action") == "hook_status"]
        self.assertEqual(len(statuses), 1, rows)
        self.assertIn(statuses[0], ("SNOOZED", "DISABLED"), "the runner got past the snooze: it may have played")
        return statuses[0] == "SNOOZED"

    def test_hooks_list_and_status_match_the_runner(self) -> None:
        probes = ["stop", "notification", "permission_request", "pretooluse", "stop_failure",
                  "notification_idle_prompt", "notification_agent_completed", "stop_failure_rate_limit",
                  "stop_failure_unknown", "session_start_resume"]
        for label in ("empty", "booleans", "kill_switch_with_explicit_variants", "non_boolean_parents",
                      "non_boolean_variants"):
            self.store(CONFIGS[label])
            listed = self.cli("hooks", "list", "--variants")
            by_name = {r["name"]: r["enabled"] for r in listed["hooks"] + listed["variants"]}
            status_on = set(self.cli("status")["enabled_hooks"])
            self.assertEqual(status_on, {r["name"] for r in listed["hooks"] if r["enabled"]}, label)
            stored = json.loads((self.state / "user_preferences.json").read_text(encoding="utf-8"))
            for arg in probes:
                with self.subTest(config=label, arg=arg):
                    self.assertIs(by_name[arg], self.runner_fires(arg))
            # Whatever the runner's own load did to the file, it did not change the answer.
            after = json.loads((self.state / "user_preferences.json").read_text(encoding="utf-8"))
            for key, value in CONFIGS[label].items():
                self.assertEqual(after["enabled_hooks"].get(key), stored["enabled_hooks"].get(key), key)
                self.assertEqual(after["enabled_hooks"].get(key), value, key)


class TestSnoozeReadAgrees(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cli = _load_cli()
        cls.hr = cls.cli.HR

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="echook-snooze-read-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.queue = self.root / "data" / "queue"

    def both(self):
        core = self.hr.runner_core
        with mock.patch.object(self.cli, "_queue_dir", return_value=self.queue), \
                mock.patch.object(core, "_prefs", return_value=SimpleNamespace(queue_dir=self.queue)), \
                mock.patch.object(core, "_queue_dir_ensured", True):
            return self.cli._snooze_status(), self.hr.is_snoozed()

    def test_reader_and_cli_create_nothing(self) -> None:
        self.assertEqual(self.hr.read_snooze_until(self.queue), (None, None))
        with mock.patch.object(self.cli, "_queue_dir", return_value=self.queue):
            self.assertEqual(self.cli._snooze_status(), {"active": False, "remaining_seconds": 0, "until": None})
        self.assertFalse((self.root / "data").exists())

    def test_cli_and_runner_agree_on_every_marker(self) -> None:
        self.queue.mkdir(parents=True)
        marker = self.queue / "snooze_until"
        now = time.time()
        for text, active in ((str(now + 3600), True), (str(now - 5), False), ("soon", False), ("", False),
                             ("nan", False), ("  %s\n" % (now + 600), True)):
            marker.write_text(text, encoding="utf-8")
            status, snoozed = self.both()
            with self.subTest(marker=text):
                self.assertIs(snoozed, active)
                self.assertIs(status["active"], snoozed)
        marker.unlink()
        status, snoozed = self.both()
        self.assertFalse(snoozed)
        self.assertEqual(status, {"active": False, "remaining_seconds": 0, "until": None})

    def test_unparseable_marker_is_reported_not_raised(self) -> None:
        self.queue.mkdir(parents=True)
        (self.queue / "snooze_until").write_text("soon", encoding="utf-8")
        until, error = self.hr.read_snooze_until(self.queue)
        self.assertIsNone(until)
        self.assertIsInstance(error, ValueError)

    def test_runner_still_creates_the_queue_dir(self) -> None:
        """Unchanged by the extraction: is_snoozed() makes the directory, the reader does not."""
        core = self.hr.runner_core
        with mock.patch.object(core, "_prefs", return_value=SimpleNamespace(queue_dir=self.queue)), \
                mock.patch.object(core, "_queue_dir_ensured", False):
            self.assertFalse(self.hr.is_snoozed())
            self.assertTrue(self.queue.is_dir())


if __name__ == "__main__":
    unittest.main()
