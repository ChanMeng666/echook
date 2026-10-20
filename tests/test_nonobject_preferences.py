"""A ``user_preferences.json`` that is valid JSON but not an object.

Through 7.1.0 ``UserPreferences.load()`` fell back to the defaults only when the
file did not parse. An array, a string, a number or ``null`` came back as-is,
``is_hook_enabled`` raised ``AttributeError`` on it, every hook event was silent,
and the runner never replaced the file. The hook path now treats such a file
exactly like an unparseable one: the event fires on the defaults and the
migration save replaces the file, backing the old bytes up to the sibling
``.bak``. Reporting commands still only detect it
(``tests/test_read_only_commands.py``, ``TestReadOnlyCommandsCorruptPreferences``).

Run with::

    python -m unittest discover tests
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
HOOK_RUNNER = REPO / "hooks" / "hook_runner.py"

NON_OBJECT_PREFS = {
    "array": b"[1, 2]",
    "string": b'"custom"',
    "number": b"42",
    "null": b"null",
}

STOP = {"session_id": "s", "hook_event_name": "Stop", "cwd": "."}


def _load_hook_runner():
    """Re-execute hook_runner.py with fresh module state (prefs singleton included)."""
    for name in ("hook_runner", "runner_core", "user_preferences", "invoker"):
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location("hook_runner", HOOK_RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_repaired(case: unittest.TestCase, data: Path, raw: bytes) -> None:
    prefs = json.loads((data / "user_preferences.json").read_text(encoding="utf-8"))
    case.assertIsInstance(prefs, dict, "the non-object file was not replaced")
    case.assertIn("_version", prefs)
    case.assertIsInstance(prefs.get("enabled_hooks"), dict)
    # The same backup an unparseable file gets: the old bytes, in the sibling .bak.
    case.assertEqual((data / "user_preferences.json.bak").read_bytes(), raw)


class TestHookEventFiresOnNonObjectPreferences(unittest.TestCase):
    """In process: the stop event plays its sound on the defaults."""

    def test_stop_plays_and_the_file_is_repaired(self) -> None:
        for name, raw in NON_OBJECT_PREFS.items():
            with self.subTest(file=name), tempfile.TemporaryDirectory() as td:
                data = Path(td) / "data"
                data.mkdir()
                (data / "user_preferences.json").write_bytes(raw)
                env = {"CLAUDE_AUDIO_HOOKS_DATA": str(data)}
                with mock.patch.dict(os.environ, env):
                    os.environ.pop("CLAUDE_PLUGIN_DATA", None)
                    hr = _load_hook_runner()
                    with mock.patch.object(hr, "is_snoozed", return_value=False), \
                         mock.patch.object(hr, "check_and_self_update"), \
                         mock.patch.object(hr.audio_playback, "play_audio", return_value=True) as play, \
                         mock.patch.object(hr.desktop_notification, "send_desktop_notification",
                                           return_value=False) as toast, \
                         mock.patch.object(hr.text_to_speech, "play_tts", return_value=False), \
                         mock.patch.object(hr.terminal_sequence, "emit_terminal_sequence", return_value=False), \
                         mock.patch.object(hr.webhook_dispatch, "send_webhook"), \
                         mock.patch.object(hr.runner_core, "log_trigger") as trigger:
                        rc = hr.run_hook("stop", dict(STOP))
                self.assertEqual(rc, 0)
                play.assert_called_once()
                statuses = [c.args[1] for c in trigger.call_args_list]
                self.assertIn("PLAYED", statuses)
                toast.assert_called_once()
                _assert_repaired(self, data, raw)


class TestRealRunnerRepairsNonObjectPreferences(unittest.TestCase):
    """A real ``hook_runner.py`` process: past the enabled gate, file replaced.

    The data dir is snoozed (see ``_isolation.seed_snooze``) so the process
    stops before a real audio player; snooze is checked right after
    ``is_hook_enabled``, which is where a non-object file used to crash.
    """

    def test_stop_reaches_the_snooze_gate_and_repairs_the_file(self) -> None:
        for name, raw in NON_OBJECT_PREFS.items():
            with self.subTest(file=name), tempfile.TemporaryDirectory() as td:
                data = Path(td) / "data"
                data.mkdir()
                (data / "user_preferences.json").write_bytes(raw)
                env = os.environ.copy()
                for k in ("CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT", "CURSOR_VERSION",
                          "CODEX_HOME", "CLAUDE_HOOKS_DEBUG"):
                    env.pop(k, None)
                _isolation.pin_data_dir(env, data)
                env["PYTHONIOENCODING"] = "utf-8"
                proc = subprocess.run([sys.executable, str(HOOK_RUNNER), "stop"],
                                      input=json.dumps(STOP), capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", env=env, timeout=30)
                self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
                log = data / "logs" / "events.ndjson"
                self.assertTrue(log.exists(), "no NDJSON log written")
                events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
                          if line.strip()]
                statuses = [e.get("status") for e in events
                            if e.get("action") == "hook_status" and e.get("hook") == "stop"]
                self.assertEqual(statuses, ["SNOOZED"], events)
                self.assertFalse([e for e in events if e.get("level") == "error"], events)
                _assert_repaired(self, data, raw)


if __name__ == "__main__":
    unittest.main()
