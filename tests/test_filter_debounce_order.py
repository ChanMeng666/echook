"""Tests for the v6.7 ordering of the filter and debounce gates in ``run_hook``.

``should_debounce`` is not a pure predicate: when it lets an event through it
stamps ``<hook>_last_played``, which opens the debounce window. Through v6.6 it
ran before ``should_filter``, so an event that a filter then discarded (a
``stop`` skipped by ``skip_if_background_tasks_running``, say) still opened the
window, and the next genuine event of that kind -- the one that should have
played -- was reported DEBOUNCED although nothing had been delivered. Filters
run first now, so only an event that will be delivered stamps the file.

``run_hook`` is driven in-process against a throwaway queue directory with
every side-effecting call patched out.

Run with::

    python -m unittest discover tests
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
HOOK_RUNNER = REPO / "hooks" / "hook_runner.py"

CONFIG = {
    "enabled_hooks": {"stop": True},
    "filters": {"stop": {"skip_if_background_tasks_running": True}},
    "playback_settings": {"debounce_ms": 60000},
    "notification_settings": {"mode": "audio_only"},
}
BUSY = {"session_id": "t", "background_tasks": [{"id": "1", "type": "teammate", "status": "running"}]}
IDLE = {"session_id": "t", "background_tasks": []}


def _load_hook_runner():
    spec = importlib.util.spec_from_file_location("hook_runner", HOOK_RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestFilterRunsBeforeDebounce(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hr = _load_hook_runner()

    def setUp(self) -> None:
        hr = self.hr
        self.queue = Path(tempfile.mkdtemp(prefix="echook-debounce-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.queue, ignore_errors=True))
        self.stamp = self.queue / "stop_last_played"
        self.statuses = []
        patches = {
            "prefs": mock.patch.object(hr, "_prefs", return_value=SimpleNamespace(queue_dir=self.queue)),
            "ensured": mock.patch.object(hr, "_queue_dir_ensured", True),
            "config": mock.patch.object(hr, "load_config", return_value=CONFIG),
            "log_event": mock.patch.object(hr, "log_event"),
            "log_trigger": mock.patch.object(
                hr, "log_trigger", side_effect=lambda h, s, d="": self.statuses.append(s)),
            "invoker": mock.patch.object(hr, "_get_invoker", return_value="claude-code"),
            "marker": mock.patch.object(hr, "_read_install_marker", return_value={}),
            "snoozed": mock.patch.object(hr, "is_snoozed", return_value=False),
            "rate": mock.patch.object(hr, "check_rate_limits"),
            "update": mock.patch.object(hr, "check_and_self_update"),
            "audio_file": mock.patch.object(hr, "get_audio_file", return_value=Path(__file__)),
            "play_audio": mock.patch.object(hr, "play_audio", return_value=True),
            "terminal": mock.patch.object(hr, "emit_terminal_sequence", return_value=False),
        }
        self.mocks = {}
        for name, p in patches.items():
            self.mocks[name] = p.start()
            self.addCleanup(p.stop)

    def test_filtered_event_does_not_stamp_the_debounce_file(self) -> None:
        self.hr.run_hook("stop", BUSY)
        self.assertEqual(self.statuses, ["FILTERED"])
        self.assertFalse(self.stamp.exists(), "a filtered event opened the debounce window")
        self.mocks["play_audio"].assert_not_called()

    def test_genuine_event_after_a_filtered_one_is_not_debounced(self) -> None:
        self.hr.run_hook("stop", BUSY)
        self.hr.run_hook("stop", IDLE)
        self.assertEqual(self.statuses, ["FILTERED", "PLAYED"])
        self.mocks["play_audio"].assert_called_once()

    def test_delivered_event_still_stamps_and_debounces_the_next(self) -> None:
        """Unfiltered behaviour is unchanged."""
        self.hr.run_hook("stop", IDLE)
        self.assertTrue(self.stamp.exists())
        self.hr.run_hook("stop", IDLE)
        self.assertEqual(self.statuses, ["PLAYED", "DEBOUNCED"])
        self.mocks["play_audio"].assert_called_once()

    def test_filtered_event_inside_an_open_window_reports_filtered(self) -> None:
        self.hr.run_hook("stop", IDLE)
        self.hr.run_hook("stop", BUSY)
        self.assertEqual(self.statuses, ["PLAYED", "FILTERED"])


if __name__ == "__main__":
    unittest.main()
