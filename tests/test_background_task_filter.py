"""Tests for the v6.4 ``skip_if_background_tasks_running`` filter.

Claude Code's ``Stop`` payload carries an undocumented ``background_tasks``
array — observed on Claude Code 2.1.215 during the v6.4 hook capture — listing
teammates, subagents and background shells that are still in flight::

    "background_tasks": [
      {"id": "<opaque-id>", "type": "teammate", "status": "running", ...},
      {"id": "<opaque-id>", "type": "shell",    "status": "running", ...}
    ]

``Stop`` fires at the end of every turn, so a session driving ten teammates
chimes on every one of them. This filter suppresses the turn-end sound while
anything is still running, which is as close to "the work is actually finished"
as Claude Code's payloads allow.

Run with::

    python -m unittest discover tests
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
