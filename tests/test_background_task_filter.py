"""Tests for the v6.4 ``skip_if_background_tasks_running`` filter.

Claude Code's ``Stop`` payload carries a ``background_tasks`` array (now in the
hooks reference; first observed on Claude Code 2.1.215 during the v6.4 hook
capture) listing teammates, subagents and background shells that are still in
flight::

    "background_tasks": [
      {"id": "<opaque-id>", "type": "teammate", "status": "running", ...},
      {"id": "<opaque-id>", "type": "shell",    "status": "running", ...}
    ]

``Stop`` fires at the end of every turn, so a session driving ten teammates
chimes on every one of them. This filter suppresses the turn-end sound while
anything is still running, which is as close to "the work is actually finished"
as Claude Code's payloads allow.

v6.6 additions: ``pending`` counts as in flight (Claude Code's own predicate
treats it so), the three internal maintenance labels never count, and the
separate ``skip_if_session_crons_scheduled`` key reads ``session_crons``.

Run with::

    python -m unittest discover tests
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import importlib.util
import unittest
from pathlib import Path
from unittest import mock
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parent.parent
HOOK_RUNNER = REPO / "hooks" / "hook_runner.py"


def _load_hook_runner():
    spec = importlib.util.spec_from_file_location("hook_runner", HOOK_RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _task(status: str, kind: str = "teammate") -> Dict[str, Any]:
    return {"id": "t1", "type": kind, "status": status, "description": "x"}


ON = {"filters": {"stop": {"skip_if_background_tasks_running": True}}}


class TestBackgroundTaskFilter(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hr = _load_hook_runner()

    def _skipped(self, tasks: Any, config: Dict[str, Any] = None) -> bool:
        stdin: Dict[str, Any] = {"session_id": "t", "hook_event_name": "Stop"}
        if tasks is not None:
            stdin["background_tasks"] = tasks
        return self.hr.should_filter("stop", stdin, ON if config is None else config)

    def test_skips_while_a_task_is_running(self) -> None:
        self.assertTrue(self._skipped([_task("running")]))

    def test_skips_when_any_of_several_is_running(self) -> None:
        self.assertTrue(self._skipped(
            [_task("completed"), _task("running"), _task("completed")]))

    def test_fires_when_all_tasks_finished(self) -> None:
        self.assertFalse(self._skipped([_task("completed"), _task("failed")]))

    def test_fires_when_task_list_is_empty(self) -> None:
        self.assertFalse(self._skipped([]))

    def test_fires_when_field_absent(self) -> None:
        """Cursor and Codex payloads have no background_tasks at all."""
        self.assertFalse(self._skipped(None))

    def test_counts_shell_tasks_too(self) -> None:
        self.assertTrue(self._skipped([_task("running", "shell")]))

    def test_opt_in_only(self) -> None:
        """Absent config must not change behaviour for existing users."""
        self.assertFalse(self._skipped([_task("running")], {}))
        self.assertFalse(self._skipped([_task("running")], {"filters": {}}))
        self.assertFalse(self._skipped(
            [_task("running")],
            {"filters": {"stop": {"skip_if_background_tasks_running": False}}}))

    def test_applies_per_hook(self) -> None:
        """Configured for stop only — notification must be unaffected."""
        stdin = {"session_id": "t", "background_tasks": [_task("running")]}
        self.assertFalse(self.hr.should_filter("notification", stdin, ON))

    def test_malformed_payloads_do_not_raise(self) -> None:
        for bad in ("not-a-list", 42, [None], [{"no_status": 1}], [[]]):
            with self.subTest(payload=bad):
                self.assertFalse(self._skipped(bad))

    def test_pending_counts_as_in_flight(self) -> None:
        """Claude Code's own in-flight predicate is running|pending."""
        self.assertTrue(self._skipped([_task("pending")]))
        self.assertTrue(self._skipped([_task("completed"), _task("pending", "shell")]))

    def test_internal_maintenance_tasks_do_not_hold_back_the_sound(self) -> None:
        for label in ("dream", "auto-mode scan", "memory import"):
            with self.subTest(type=label):
                self.assertFalse(self._skipped([_task("running", label)]))
                self.assertFalse(self._skipped([_task("pending", label)]))

    def test_internal_task_does_not_mask_a_user_task(self) -> None:
        self.assertTrue(self._skipped([_task("running", "dream"), _task("running", "subagent")]))

    def test_every_user_facing_label_counts(self) -> None:
        for label in ("subagent", "workflow", "shell", "monitor", "MCP task",
                      "teammate", "cloud session"):
            with self.subTest(type=label):
                self.assertTrue(self._skipped([_task("running", label)]))

    def test_entry_without_type_still_counts(self) -> None:
        """An older payload shape must not be mistaken for an internal task."""
        self.assertTrue(self._skipped([{"id": "t", "status": "running"}]))

    def test_unhashable_status_or_type_does_not_raise(self) -> None:
        """Set membership on a list/dict raises TypeError; a hook must not."""
        for bad in ([], ["running"], {}, {"running": 1}, 3, None):
            with self.subTest(status=bad):
                self.assertFalse(self._skipped([{"id": "t", "type": "shell", "status": bad}]))
            with self.subTest(type=bad):
                # A non-string type cannot be an internal label, so the task
                # still counts when its status says it is in flight.
                self.assertTrue(self._skipped([{"id": "t", "type": bad, "status": "running"}]))

    def test_malformed_entries_next_to_a_good_one(self) -> None:
        self.assertTrue(self._skipped([None, "x", [], {"status": 3}, _task("running")]))

    def test_does_not_disturb_regex_filters(self) -> None:
        """The boolean key must not be treated as a regex pattern."""
        config = {"filters": {"stop": {
            "skip_if_background_tasks_running": True,
            "last_assistant_message": "deploy",
        }}}
        stdin = {"session_id": "t", "last_assistant_message": "deploy done"}
        self.assertFalse(self.hr.should_filter("stop", stdin, config))
        stdin_nomatch = {"session_id": "t", "last_assistant_message": "nope"}
        self.assertTrue(self.hr.should_filter("stop", stdin_nomatch, config))


CRON_ON = {"filters": {"stop": {"skip_if_session_crons_scheduled": True}}}


def _cron(recurring: bool = True) -> Dict[str, Any]:
    return {"id": "c1", "schedule": "0 9 * * 1-5", "recurring": recurring, "prompt": "check"}


class TestSessionCronFilter(unittest.TestCase):
    """``skip_if_session_crons_scheduled`` -- a separate key on purpose: a
    session with a recurring cron always has an entry, so folding it into the
    background-task key would silence every turn for users who never asked."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.hr = _load_hook_runner()

    def _skipped(self, crons: Any, config: Dict[str, Any] = None,
                 extra: Dict[str, Any] = None) -> bool:
        stdin: Dict[str, Any] = {"session_id": "t", "hook_event_name": "Stop"}
        if crons is not None:
            stdin["session_crons"] = crons
        stdin.update(extra or {})
        return self.hr.should_filter("stop", stdin, CRON_ON if config is None else config)

    def test_skips_while_a_cron_is_scheduled(self) -> None:
        self.assertTrue(self._skipped([_cron()]))
        self.assertTrue(self._skipped([_cron(recurring=False)]))

    def test_fires_when_no_crons(self) -> None:
        self.assertFalse(self._skipped([]))

    def test_fires_when_field_absent(self) -> None:
        self.assertFalse(self._skipped(None))

    def test_opt_in_only(self) -> None:
        self.assertFalse(self._skipped([_cron()], {}))
        self.assertFalse(self._skipped([_cron()], {"filters": {}}))
        self.assertFalse(self._skipped(
            [_cron()], {"filters": {"stop": {"skip_if_session_crons_scheduled": False}}}))

    def test_independent_of_the_background_task_key(self) -> None:
        # A cron must not trip the background-task filter ...
        bg_only = {"filters": {"stop": {"skip_if_background_tasks_running": True}}}
        self.assertFalse(self._skipped([_cron()], bg_only))
        # ... and a running task must not trip the cron filter.
        self.assertFalse(self._skipped([], CRON_ON, {"background_tasks": [_task("running")]}))

    def test_both_keys_together(self) -> None:
        both = {"filters": {"stop": {"skip_if_background_tasks_running": True,
                                     "skip_if_session_crons_scheduled": True}}}
        self.assertTrue(self._skipped([_cron()], both))
        self.assertTrue(self._skipped([], both, {"background_tasks": [_task("pending")]}))
        self.assertFalse(self._skipped([], both, {"background_tasks": []}))

    def test_malformed_payloads_do_not_raise(self) -> None:
        for bad in ("not-a-list", 42, {"id": "c"}, {}, 0, False):
            with self.subTest(payload=bad):
                self.assertFalse(self._skipped(bad))

    def test_non_dict_entries_do_not_raise(self) -> None:
        """The filter only asks whether the list is non-empty."""
        self.assertTrue(self._skipped([None]))

    def test_key_is_not_treated_as_a_regex_field(self) -> None:
        config = {"filters": {"stop": {"skip_if_session_crons_scheduled": True,
                                        "last_assistant_message": "deploy"}}}
        stdin = {"session_id": "t", "last_assistant_message": "deploy done"}
        self.assertFalse(self.hr.should_filter("stop", stdin, config))


class TestRateLimitThresholdTolerance(unittest.TestCase):
    """`rate-limits set --five-hour-thresholds 90` used to store the integer 90;
    sorted(90) then raised inside check_rate_limits, before any audio."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.hr = _load_hook_runner()

    def _run(self, thresholds):
        payload = {"rate_limits": {"five_hour": {"used_percentage": 99, "resets_at": 1234567890}}}
        cfg = {"rate_limit_alerts": {"enabled": True, "five_hour_thresholds": thresholds,
                                     "seven_day_thresholds": []}}
        with mock.patch.object(self.hr, "play_audio", return_value=True) as play, \
                mock.patch.object(self.hr, "ensure_queue_dir"), \
                mock.patch.object(self.hr, "_prefs") as prefs, \
                mock.patch.object(self.hr, "log_event"), \
                mock.patch.object(self.hr, "log_error_event"):
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                prefs.return_value.queue_dir = Path(td)
                self.hr.check_rate_limits(payload, cfg)
        return play

    def test_scalar_string_and_malformed_values_do_not_raise(self) -> None:
        for value in (90, 90.0, "80,95", True, None, {"a": 1}, [None, "x", 90], "not,numbers",
                      float("inf"), float("-inf"), [float("inf"), 80], float("nan"), 1e999):
            with self.subTest(value=value):
                self._run(value)  # must not raise

    def test_a_scalar_still_alerts(self) -> None:
        # Audio file resolution is real; only playback is mocked. A scalar 90 must
        # behave like [90]: 99% >= 90 fires.
        self.assertTrue(self._run(90).called)
        self.assertTrue(self._run("80,95").called)
        self.assertFalse(self._run(None).called)
        # an infinite threshold is skipped (int(inf) raises OverflowError), the finite one still alerts
        self.assertTrue(self._run([float("inf"), 80]).called)

    def test_infinite_used_percentage_does_not_raise(self) -> None:
        payload = {"rate_limits": {"five_hour": {"used_percentage": float("inf"), "resets_at": 1}}}
        cfg = {"rate_limit_alerts": {"enabled": True, "five_hour_thresholds": [80]}}
        with mock.patch.object(self.hr, "play_audio", return_value=True):
            self.hr.check_rate_limits(payload, cfg)


if __name__ == "__main__":
    unittest.main()
