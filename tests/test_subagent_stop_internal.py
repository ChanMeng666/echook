"""Tests for the v6.6 guard against SubagentStop from Claude Code's internal agents.

The hooks reference: "Not every SubagentStop event comes from a subagent Claude
spawned. Claude Code also runs internal agents for some of its own features,
such as prompt suggestions and /btw side questions, and SubagentStop fires when
one of those finishes too. For those events, ``agent_type`` is the agent name
the session itself runs as ... and an empty string when the session runs
without one."

An empty-string ``agent_type`` is therefore the marker; an absent key is not
(older Claude Code builds and other editors omit it). The guard is scoped to the
``claude-code`` invoker: nothing establishes what an empty ``agent_type`` means
in Cursor or Codex payloads, so those must keep announcing.

``run_hook`` is driven in-process with every side-effecting call patched out.
For the "not skipped" cases ``is_hook_enabled`` is patched to False so the run
ends at the DISABLED gate -- far enough to prove the guard let it through,
without playing audio or raising a toast.
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

REPO = Path(__file__).resolve().parent.parent
HOOK_RUNNER = REPO / "hooks" / "hook_runner.py"


def _load_hook_runner():
    spec = importlib.util.spec_from_file_location("hook_runner", HOOK_RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestInternalSubagentGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hr = _load_hook_runner()

    def setUp(self) -> None:
        hr = self.hr
        self.events = []
        patches = {
            "log_event": mock.patch.object(
                hr, "log_event", side_effect=lambda *a, **k: self.events.append((a, k))),
            "is_hook_enabled": mock.patch.object(hr, "is_hook_enabled", return_value=False),
            "invoker": mock.patch.object(hr, "_get_invoker", return_value="claude-code"),
            # The cursor path reads the install marker; keep it off the real disk.
            "marker": mock.patch.object(hr, "_read_install_marker", return_value={}),
            "log_trigger": mock.patch.object(hr, "log_trigger"),
            "play_audio": mock.patch.object(hr, "play_audio", return_value=True),
            "play_tts": mock.patch.object(hr, "play_tts", return_value=True),
            "send_desktop_notification": mock.patch.object(
                hr, "send_desktop_notification", return_value=True),
        }
        self.mocks = {}
        for name, p in patches.items():
            self.mocks[name] = p.start()
            self.addCleanup(p.stop)

    def _run(self, hook: str, payload: dict) -> int:
        return self.hr.run_hook(hook, payload)

    def _skip_events(self):
        return [e for e in self.events if e[0][:2] == ("debug", "skipped_internal_subagent")]

    def test_empty_agent_type_is_skipped_and_recorded(self) -> None:
        rc = self._run("subagent_stop", {"session_id": "t", "agent_type": ""})
        self.assertEqual(rc, 0)
        self.assertEqual(len(self._skip_events()), 1)
        # Skipped before the enabled gate, so nothing downstream ran.
        self.mocks["is_hook_enabled"].assert_not_called()
        self.mocks["play_audio"].assert_not_called()
        self.mocks["play_tts"].assert_not_called()
        self.mocks["send_desktop_notification"].assert_not_called()

    def test_absent_agent_type_is_not_skipped(self) -> None:
        self._run("subagent_stop", {"session_id": "t"})
        self.assertEqual(self._skip_events(), [])
        self.mocks["is_hook_enabled"].assert_called_once()

    def test_normal_agent_type_is_not_skipped(self) -> None:
        self._run("subagent_stop", {"session_id": "t", "agent_type": "Explore"})
        self.assertEqual(self._skip_events(), [])
        self.mocks["is_hook_enabled"].assert_called_once()

    def test_null_agent_type_is_not_skipped(self) -> None:
        """Only the documented empty string is a marker."""
        self._run("subagent_stop", {"session_id": "t", "agent_type": None})
        self.assertEqual(self._skip_events(), [])
        self.mocks["is_hook_enabled"].assert_called_once()

    def test_other_events_with_empty_agent_type_are_untouched(self) -> None:
        for hook in ("stop", "subagent_start"):
            with self.subTest(hook=hook):
                self.mocks["is_hook_enabled"].reset_mock()
                self._run(hook, {"session_id": "t", "agent_type": ""})
                self.assertEqual(self._skip_events(), [])
                self.mocks["is_hook_enabled"].assert_called_once()

    def test_same_payload_is_not_skipped_under_other_invokers(self) -> None:
        """Cursor (native and auto-bridge both report "cursor"), Codex, and an
        undetected invoker must keep announcing an empty-agent_type event."""
        for invoker in ("cursor", "codex", "unknown"):
            with self.subTest(invoker=invoker):
                self.events.clear()
                self.mocks["is_hook_enabled"].reset_mock()
                self.mocks["invoker"].return_value = invoker
                self._run("subagent_stop", {"session_id": "t", "agent_type": ""})
                self.assertEqual(self._skip_events(), [])
                self.mocks["is_hook_enabled"].assert_called_once()

    def test_non_dict_payload_does_not_raise(self) -> None:
        for bad in (None, [], "x"):
            with self.subTest(payload=bad):
                self.mocks["is_hook_enabled"].reset_mock()
                self._run("subagent_stop", bad)
                self.assertEqual(self._skip_events(), [])


if __name__ == "__main__":
    unittest.main()
