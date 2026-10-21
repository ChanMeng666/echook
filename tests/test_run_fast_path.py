"""The disabled-event fast path in ``plugins/audio-hooks/runner/run.py``.

Every registered hook event spawns Python; for an event the user switched off
the full runner used to import a dozen modules only to log DISABLED. The entry
now answers that from the preferences file with the standard library alone.
These tests pin the two halves of that bargain: a disabled event really skips
the heavy path, and the fast path never says "disabled" when the runner would
have done something.
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import copy
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

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "plugins" / "audio-hooks"
RUN_PY = PLUGIN / "runner" / "run.py"
HOOKS_DIR = PLUGIN / "hooks"
TEMPLATE = json.loads((REPO / "config" / "default_preferences.json").read_text(encoding="utf-8"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RUN = _load("echook_run_entry", RUN_PY)


def _prefs(**enabled) -> dict:
    cfg = copy.deepcopy(TEMPLATE)
    cfg["enabled_hooks"].update(enabled)
    return cfg


class FastPathBase(unittest.TestCase):
    def setUp(self) -> None:
        self.data = Path(tempfile.mkdtemp(prefix="echook-fastpath-"))
        self.addCleanup(shutil.rmtree, self.data, ignore_errors=True)
        self.env = {"CLAUDE_PLUGIN_DATA": str(self.data)}

    def write(self, cfg) -> None:
        text = cfg if isinstance(cfg, str) else json.dumps(cfg)
        (self.data / "user_preferences.json").write_text(text, encoding="utf-8")

    def fast(self, event: str, *extra: str, env=None) -> bool:
        argv = ["run.py", event, *extra]
        return RUN.event_is_disabled_fast(str(HOOKS_DIR), argv, env if env is not None else self.env)


class TestDecision(FastPathBase):
    def test_explicitly_disabled_event(self) -> None:
        self.write(_prefs())
        self.assertTrue(self.fast("cwd_changed"))

    def test_enabled_event_is_not_short_circuited(self) -> None:
        self.write(_prefs())
        for ev in ("stop", "notification", "permission_request"):
            self.assertFalse(self.fast(ev), ev)

    def test_dashes_and_case_are_normalised_like_main(self) -> None:
        self.write(_prefs())
        self.assertTrue(self.fast("CWD-Changed"))

    def test_invoker_flag_is_stripped_before_reading_the_event(self) -> None:
        self.write(_prefs())
        self.assertTrue(self.fast("--invoker", "codex", "cwd_changed"))
        self.assertTrue(self.fast("cwd_changed", "--invoker=codex"))

    def test_variant_follows_a_disabled_parent(self) -> None:
        self.write(_prefs(stop_failure=False))
        self.assertTrue(self.fast("stop_failure_rate_limit"))

    def test_variant_explicit_true_beats_disabled_parent(self) -> None:
        self.write(_prefs(stop_failure=False, stop_failure_rate_limit=True))
        self.assertFalse(self.fast("stop_failure_rate_limit"))

    def test_variant_of_enabled_parent_is_left_to_the_full_path(self) -> None:
        # Variant defaults live in hook_runner (SYNTHETIC_VARIANT_DEFAULTS).
        self.write(_prefs())
        self.assertFalse(self.fast("notification_idle_prompt"))

    def test_explicit_variant_false_under_enabled_parent(self) -> None:
        self.write(_prefs(notification_idle_prompt=False))
        self.assertTrue(self.fast("notification_idle_prompt"))
        self.assertFalse(self.fast("notification_permission_prompt"))

    def test_non_boolean_value_is_not_guessed(self) -> None:
        self.write(_prefs(cwd_changed="no"))
        self.assertFalse(self.fast("cwd_changed"))

    def test_event_absent_from_prefs_is_not_guessed(self) -> None:
        cfg = _prefs()
        del cfg["enabled_hooks"]["cwd_changed"]
        self.write(cfg)
        self.assertFalse(self.fast("cwd_changed"))

    def test_missing_corrupt_or_non_object_prefs_fall_through(self) -> None:
        self.assertFalse(self.fast("cwd_changed"))  # no file
        for text in ("{not json", "[]", "null", '"x"', ""):
            self.write(text)
            self.assertFalse(self.fast("cwd_changed"), repr(text))

    def test_stale_version_stamp_falls_through_so_migration_runs(self) -> None:
        cfg = _prefs()
        cfg["_version"] = "0.0.1"
        self.write(cfg)
        self.assertFalse(self.fast("cwd_changed"))

    def test_malformed_enabled_hooks_falls_through(self) -> None:
        cfg = _prefs()
        cfg["enabled_hooks"] = ["cwd_changed"]
        self.write(cfg)
        self.assertFalse(self.fast("cwd_changed"))

    def test_debug_flag_falls_through(self) -> None:
        self.write(_prefs())
        env = dict(self.env, CLAUDE_HOOKS_DEBUG="1")
        self.assertFalse(self.fast("cwd_changed", env=env))

    def test_unresolved_data_dir_falls_through(self) -> None:
        self.write(_prefs())
        self.assertFalse(self.fast("cwd_changed", env={}))

    def test_data_dir_variable_precedence_matches_the_resolver(self) -> None:
        other = Path(tempfile.mkdtemp(prefix="echook-fastpath-other-"))
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        (other / "user_preferences.json").write_text(
            json.dumps(_prefs(cwd_changed=True)), encoding="utf-8")
        self.write(_prefs())
        # CLAUDE_PLUGIN_DATA outranks PLUGIN_DATA and CLAUDE_AUDIO_HOOKS_DATA.
        env = {"CLAUDE_PLUGIN_DATA": str(self.data), "PLUGIN_DATA": str(other),
               "CLAUDE_AUDIO_HOOKS_DATA": str(other)}
        self.assertTrue(self.fast("cwd_changed", env=env))
        self.assertFalse(self.fast("cwd_changed", env={"PLUGIN_DATA": str(other)}))
        self.assertTrue(self.fast("cwd_changed", env={"PLUGIN_DATA": str(self.data)}))
        self.assertTrue(self.fast("cwd_changed", env={"CLAUDE_AUDIO_HOOKS_DATA": str(self.data)}))

    def test_cursor_session_start_and_stop_always_run_fully(self) -> None:
        self.write(_prefs(session_start=False, stop=False))
        cursor_env = dict(self.env, CURSOR_VERSION="3.2.16")
        for ev in ("session_start", "session_start_startup", "stop"):
            self.assertFalse(self.fast(ev, env=cursor_env), ev)
            self.assertFalse(self.fast(ev, "--invoker", "cursor"), ev)
            # Outside Cursor the same switches are honoured.
            self.assertTrue(self.fast(ev), ev)
        # An explicit non-Cursor invoker beats CURSOR_VERSION, as in invoker.py.
        self.assertTrue(self.fast("stop", "--invoker", "claude-code", env=cursor_env))

    def test_no_event_argument(self) -> None:
        self.write(_prefs())
        self.assertFalse(RUN.event_is_disabled_fast(str(HOOKS_DIR), ["run.py"], self.env))


class TestAgreesWithTheRunner(FastPathBase):
    """The fast path may only say "disabled" where the runner's gate does."""

    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(HOOKS_DIR))
        cls.hr = _load("hook_runner_for_fast_path", HOOKS_DIR / "hook_runner.py")
        names = set(cls.hr.SYNTHETIC_EVENT_MAP) | set(TEMPLATE["enabled_hooks"])
        cls.events = sorted(e for e in names if not e.startswith("_"))

    def _check(self, enabled_hooks: dict) -> int:
        cfg = _prefs()
        cfg["enabled_hooks"] = enabled_hooks
        self.write(cfg)
        short = 0
        for raw in self.events:
            canonical, _audio, variant = self.hr._resolve_synthetic_event(raw)
            runner_enabled, _rule = self.hr.resolve_hook_enabled(enabled_hooks, canonical, variant)
            if self.fast(raw):
                short += 1
                self.assertFalse(runner_enabled, f"{raw} short-circuited but the runner enables it")
        return short

    def test_variant_parent_derivation_matches_the_catalog(self) -> None:
        """Switching a parent off must silence each of its variants, as the runner does."""
        for raw, (canonical, _audio) in self.hr.SYNTHETIC_EVENT_MAP.items():
            self.write(_prefs(**{canonical: False}))
            self.assertTrue(self.fast(raw), f"{raw} did not follow its parent {canonical}")

    def test_agreement_across_configurations(self) -> None:
        base = dict(TEMPLATE["enabled_hooks"])
        variants = list(self.hr.SYNTHETIC_EVENT_MAP)
        configs = [base, {k: False for k in base}, {k: True for k in base}]
        for canonical in sorted({c for c, _ in self.hr.SYNTHETIC_EVENT_MAP.values()}):
            off = dict(base)
            off[canonical] = False
            configs.append(off)
            on = {k: False for k in base}
            on[canonical] = True
            configs.append(on)
        for variant in variants:
            mixed = {k: False for k in base}
            mixed[variant] = True
            configs.append(mixed)
            muted = {k: True for k in base}
            muted[variant] = False
            configs.append(muted)
        short = sum(self._check(c) for c in configs)
        self.assertGreater(short, 0)


class TestProcess(FastPathBase):
    def run_entry(self, event: str, *extra: str, stdin: str = "{}", env_extra=None):
        env = dict(os.environ)
        env.pop("CLAUDE_HOOKS_DEBUG", None)
        env["CLAUDE_PLUGIN_DATA"] = str(self.data)
        env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN)
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, "-X", "importtime", str(RUN_PY), event, *extra],
            input=stdin, capture_output=True, text=True, env=env, timeout=60,
        )

    def statuses(self):
        log = self.data / "logs" / "events.ndjson"
        if not log.exists():
            return []
        events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [(e.get("hook"), e.get("status")) for e in events if e.get("action") == "hook_status"]

    def test_disabled_event_exits_without_loading_the_runner(self) -> None:
        self.write(_prefs())
        before = (self.data / "user_preferences.json").read_bytes()
        proc = self.run_entry("cwd_changed", stdin='{"session_id": "s1"}')
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        self.assertEqual(proc.stdout, "")
        self.assertNotIn("hook_runner", proc.stderr)
        self.assertNotIn("user_preferences", proc.stderr)
        self.assertFalse((self.data / "logs").exists())
        self.assertEqual((self.data / "user_preferences.json").read_bytes(), before)

    def test_enabled_event_still_runs_the_full_path(self) -> None:
        self.write(_prefs())
        queue = self.data / "queue"
        queue.mkdir(parents=True)
        # Snoozed: the runner gets past the enabled gate and stops, silently.
        (queue / "snooze_until").write_text(str(time.time() + 3600), encoding="utf-8")
        proc = self.run_entry("stop", stdin='{"session_id": "s2"}')
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        self.assertIn("hook_runner", proc.stderr)
        self.assertEqual(self.statuses(), [("stop", "SNOOZED")])

    def test_corrupt_preferences_fall_through_and_are_repaired(self) -> None:
        self.write("{this is not json")
        proc = self.run_entry("cwd_changed")
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        self.assertIn("hook_runner", proc.stderr)
        self.assertEqual(self.statuses(), [("cwd_changed", "DISABLED")])
        json.loads((self.data / "user_preferences.json").read_text(encoding="utf-8"))

    def test_missing_preferences_fall_through_and_initialise(self) -> None:
        proc = self.run_entry("cwd_changed")
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        self.assertIn("hook_runner", proc.stderr)
        self.assertTrue((self.data / "user_preferences.json").exists())

    def test_debug_mode_keeps_the_disabled_log_line(self) -> None:
        self.write(_prefs())
        proc = self.run_entry("cwd_changed", env_extra={"CLAUDE_HOOKS_DEBUG": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        self.assertEqual(self.statuses(), [("cwd_changed", "DISABLED")])

    def test_cursor_session_start_still_emits_its_env_override(self) -> None:
        self.write(_prefs(session_start=False))
        proc = self.run_entry("session_start_startup", env_extra={"CURSOR_VERSION": "3.2.16"})
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        self.assertEqual(json.loads(proc.stdout.strip().splitlines()[0]),
                         {"env": {"CLAUDE_PLUGIN_DATA": str(self.data)}})


if __name__ == "__main__":
    unittest.main()
