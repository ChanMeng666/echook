"""Tests for the ``status`` field of Cursor's native ``stop`` payload.

Cursor documents the input of its ``stop`` hook as (cursor.com/docs/hooks)::

    {"status": "completed" | "aborted" | "error", "loop_count": 0}

Two opt-in behaviours read it, and neither changes anything by default:

* ``filters.<hook>.skip_if_aborted`` filters an event whose ``status`` is
  exactly ``"aborted"``.
* Under the Cursor invoker, a ``stop`` whose ``status`` is exactly ``"error"``
  is delivered as ``stop_failure`` -- but only when ``stop_failure`` is enabled.

No live Cursor payload was captured; the payloads below are built from the
documentation. ``run_hook`` is driven in-process against a throwaway queue
directory with every side-effecting call patched out.

Run with::

    python -m unittest discover tests
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
HOOK_RUNNER = REPO / "hooks" / "hook_runner.py"

STATUSES = ("completed", "aborted", "error")


def _load_hook_runner():
    spec = importlib.util.spec_from_file_location("hook_runner", HOOK_RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _payload(status: Any = None, **extra: Any) -> Dict[str, Any]:
    data: Dict[str, Any] = {"session_id": "t", "hook_event_name": "stop", "loop_count": 0}
    if status is not None:
        data["status"] = status
    data.update(extra)
    return data


class TestSkipIfAbortedFilter(unittest.TestCase):
    """``should_filter`` in isolation."""

    ON = {"filters": {"stop": {"skip_if_aborted": True}}}

    @classmethod
    def setUpClass(cls) -> None:
        cls.hr = _load_hook_runner()

    def _skipped(self, payload: Dict[str, Any], config: Optional[Dict[str, Any]] = None,
                 hook: str = "stop") -> bool:
        return self.hr.should_filter(hook, payload, self.ON if config is None else config)

    def test_aborted_is_skipped_when_the_key_is_on(self) -> None:
        self.assertTrue(self._skipped(_payload("aborted")))

    def test_completed_and_error_are_not_skipped(self) -> None:
        self.assertFalse(self._skipped(_payload("completed")))
        self.assertFalse(self._skipped(_payload("error")))

    def test_absent_status_is_not_skipped(self) -> None:
        self.assertFalse(self._skipped(_payload()))

    def test_only_the_exact_string_counts(self) -> None:
        for value in ("Aborted", "ABORTED", "aborted ", "cancelled", "", 0, True,
                      ["aborted"], {"status": "aborted"}):
            self.assertFalse(self._skipped(_payload(value) if value is not None else _payload()),
                             f"{value!r} was treated as aborted")
        self.assertFalse(self._skipped({"session_id": "t", "status": None}))

    def test_off_by_default(self) -> None:
        for config in ({}, {"filters": {}}, {"filters": {"stop": {}}},
                       {"filters": {"stop": {"skip_if_aborted": False}}}):
            self.assertFalse(self._skipped(_payload("aborted"), config))

    def test_only_a_real_true_turns_it_on(self) -> None:
        """A truthy non-bool must neither enable the filter nor be read as a
        regex over a field called ``skip_if_aborted``."""
        for value in ("true", "yes", 1):
            config = {"filters": {"stop": {"skip_if_aborted": value}}}
            for status in STATUSES:
                self.assertFalse(self._skipped(_payload(status), config), (value, status))

    def test_key_is_per_hook(self) -> None:
        self.assertFalse(self._skipped(_payload("aborted"), hook="subagent_stop"))
        config = {"filters": {"subagent_stop": {"skip_if_aborted": True}}}
        self.assertTrue(self._skipped(_payload("aborted"), config, hook="subagent_stop"))
        self.assertFalse(self._skipped(_payload("aborted"), config, hook="stop"))

    def test_task_status_inside_background_tasks_is_not_the_field(self) -> None:
        payload = _payload(background_tasks=[{"id": "1", "type": "shell", "status": "aborted"}])
        self.assertFalse(self._skipped(payload))

    def test_composes_with_sibling_filters(self) -> None:
        config = {"filters": {"stop": {"skip_if_aborted": True,
                                       "skip_if_background_tasks_running": True}}}
        self.assertTrue(self._skipped(_payload("aborted"), config))
        self.assertFalse(self._skipped(_payload("completed", background_tasks=[]), config))
        busy = [{"id": "1", "type": "teammate", "status": "running"}]
        self.assertTrue(self._skipped(_payload("completed", background_tasks=busy), config))


class _RunHookCase(unittest.TestCase):
    """Drives ``run_hook`` with every side effect patched out."""

    invoker = "cursor"
    config: Dict[str, Any] = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.hr = _load_hook_runner()

    def setUp(self) -> None:
        hr = self.hr
        self.queue = Path(tempfile.mkdtemp(prefix="echook-cursor-stop-"))
        self.addCleanup(lambda: shutil.rmtree(self.queue, ignore_errors=True))
        self.triggers = []   # (hook, status)
        self.audio_for = []  # hook names get_audio_file was asked about
        self.contexts = []   # (hook, context) handed to the webhook

        def _audio(hook: str) -> Path:
            self.audio_for.append(hook)
            return Path(__file__)

        base = {
            "playback_settings": {"debounce_ms": 60000},
            "notification_settings": {"mode": "audio_only"},
            "webhook_settings": {"enabled": True, "url": "http://127.0.0.1:9/never"},
        }
        base.update(self.config)
        self.loaded = base
        patches = {
            "prefs": mock.patch.object(hr, "_prefs", return_value=SimpleNamespace(queue_dir=self.queue)),
            "ensured": mock.patch.object(hr, "_queue_dir_ensured", True),
            "config": mock.patch.object(hr, "load_config", return_value=base),
            "log_event": mock.patch.object(hr, "log_event"),
            "log_trigger": mock.patch.object(
                hr, "log_trigger", side_effect=lambda h, s, d="": self.triggers.append((h, s))),
            "invoker": mock.patch.object(hr, "_get_invoker", return_value=self.invoker),
            "marker": mock.patch.object(hr, "_read_install_marker", return_value={}),
            "snoozed": mock.patch.object(hr, "is_snoozed", return_value=False),
            "rate": mock.patch.object(hr, "check_rate_limits"),
            "update": mock.patch.object(hr, "check_and_self_update"),
            "audio_file": mock.patch.object(hr, "get_audio_file", side_effect=_audio),
            "play_audio": mock.patch.object(hr, "play_audio", return_value=True),
            "desktop": mock.patch.object(hr, "send_desktop_notification", return_value=True),
            "tts": mock.patch.object(hr, "play_tts", return_value=True),
            "webhook": mock.patch.object(
                hr, "send_webhook",
                side_effect=lambda h, c, s, cfg: self.contexts.append((h, c))),
            "terminal": mock.patch.object(hr, "emit_terminal_sequence", return_value=False),
        }
        self.mocks = {}
        for name, p in patches.items():
            self.mocks[name] = p.start()
            self.addCleanup(p.stop)

    def run_stop(self, status: Any = None, **extra: Any) -> None:
        self.assertEqual(self.hr.run_hook("stop", _payload(status, **extra)), 0)

    def stamp(self, hook: str) -> Path:
        return self.queue / f"{hook}_last_played"

    def assert_plain_stop(self) -> None:
        self.assertEqual(self.triggers, [("stop", "PLAYED")])
        self.assertEqual(self.audio_for, ["stop"])
        self.assertEqual([h for h, _ in self.contexts], ["stop"])
        self.assertEqual(self.contexts[0][1], "Task completed")
        self.assertTrue(self.stamp("stop").exists())
        self.assertFalse(self.stamp("stop_failure").exists())


class TestDefaultBehaviourIsUnchanged(_RunHookCase):
    """No key set, ``stop_failure`` not enabled: every status is a plain stop."""

    config = {"enabled_hooks": {"stop": True}}

    def test_each_documented_status_plays_stop(self) -> None:
        for status in STATUSES:
            with self.subTest(status=status):
                self.setUp()
                self.run_stop(status)
                self.assert_plain_stop()

    def test_absent_status_plays_stop(self) -> None:
        self.run_stop()
        self.assert_plain_stop()

    def test_no_enabled_hooks_section_at_all(self) -> None:
        """Built-in defaults: stop on, stop_failure off."""
        self.loaded.pop("enabled_hooks")
        self.run_stop("error")
        self.assert_plain_stop()

    def test_stop_failure_explicitly_false(self) -> None:
        self.loaded["enabled_hooks"] = {"stop": True, "stop_failure": False}
        self.run_stop("error")
        self.assert_plain_stop()

    def test_enabling_only_a_stop_failure_variant_does_not_reroute(self) -> None:
        self.loaded["enabled_hooks"] = {"stop": True, "stop_failure_rate_limit": True}
        self.run_stop("error")
        self.assert_plain_stop()

    def test_stop_disabled_and_stop_failure_off_stays_silent(self) -> None:
        self.loaded["enabled_hooks"] = {"stop": False}
        self.run_stop("error")
        self.assertEqual(self.triggers, [("stop", "DISABLED")])
        self.mocks["play_audio"].assert_not_called()


class TestAbortedWithTheKeyOn(_RunHookCase):
    config = {"enabled_hooks": {"stop": True, "stop_failure": True},
              "filters": {"stop": {"skip_if_aborted": True}}}

    def test_aborted_is_filtered_and_opens_no_debounce_window(self) -> None:
        self.run_stop("aborted")
        self.assertEqual(self.triggers, [("stop", "FILTERED")])
        self.mocks["play_audio"].assert_not_called()
        self.mocks["webhook"].assert_not_called()
        self.assertFalse(self.stamp("stop").exists())

    def test_completed_after_an_aborted_one_still_plays(self) -> None:
        self.run_stop("aborted")
        self.run_stop("completed")
        self.assertEqual(self.triggers, [("stop", "FILTERED"), ("stop", "PLAYED")])

    def test_completed_and_absent_still_play(self) -> None:
        self.run_stop("completed")
        self.assertEqual(self.triggers, [("stop", "PLAYED")])
        self.setUp()
        self.run_stop()
        self.assertEqual(self.triggers, [("stop", "PLAYED")])


class TestAbortedFilterIsNotInvokerScoped(_RunHookCase):
    """The filter keys on the payload, like its siblings."""

    invoker = "claude-code"
    config = {"enabled_hooks": {"stop": True},
              "filters": {"stop": {"skip_if_aborted": True}}}

    def test_filters_wherever_the_payload_says_aborted(self) -> None:
        self.run_stop("aborted")
        self.assertEqual(self.triggers, [("stop", "FILTERED")])

    def test_a_payload_without_status_is_untouched(self) -> None:
        self.run_stop()
        self.assertEqual(self.triggers, [("stop", "PLAYED")])


class TestErrorWithStopFailureEnabled(_RunHookCase):
    config = {"enabled_hooks": {"stop": True, "stop_failure": True}}

    def test_error_is_delivered_as_stop_failure(self) -> None:
        self.run_stop("error")
        self.assertEqual(self.triggers, [("stop_failure", "PLAYED")])
        self.assertEqual(self.audio_for, ["stop_failure"])
        self.assertEqual(self.contexts, [("stop_failure", "Agent stopped with an error")])
        self.assertTrue(self.stamp("stop_failure").exists())
        self.assertFalse(self.stamp("stop").exists(),
                         "a re-routed error opened the stop debounce window")

    def test_reroute_is_logged_and_log_context_follows(self) -> None:
        self.run_stop("error")
        actions = [c.args[1] for c in self.mocks["log_event"].call_args_list]
        self.assertIn("stop_rerouted_to_stop_failure", actions)
        self.assertEqual(self.hr._current_hook_type, "stop_failure")

    def test_completed_aborted_absent_and_unknown_stay_stop(self) -> None:
        for status in ("completed", "aborted", None, "Error", "ERROR", "failed", "", 1, True,
                       ["error"]):
            with self.subTest(status=status):
                self.setUp()
                self.run_stop(status)
                self.assert_plain_stop()

    def test_error_does_not_debounce_a_following_completion(self) -> None:
        self.run_stop("error")
        self.run_stop("completed")
        self.assertEqual(self.triggers, [("stop_failure", "PLAYED"), ("stop", "PLAYED")])

    def test_stop_failure_plays_even_when_stop_is_off(self) -> None:
        """Its own gating: "tell me only when it failed"."""
        self.loaded["enabled_hooks"] = {"stop": False, "stop_failure": True}
        self.run_stop("error")
        self.assertEqual(self.triggers, [("stop_failure", "PLAYED")])
        self.setUp()
        self.loaded["enabled_hooks"] = {"stop": False, "stop_failure": True}
        self.run_stop("completed")
        self.assertEqual(self.triggers, [("stop", "DISABLED")])

    def test_stop_filters_no_longer_apply_but_stop_failure_filters_do(self) -> None:
        self.loaded["filters"] = {"stop": {"status_exclude": "^error$"}}
        self.run_stop("error")
        self.assertEqual(self.triggers, [("stop_failure", "PLAYED")])
        self.setUp()
        self.loaded["filters"] = {"stop_failure": {"status_exclude": "^error$"}}
        self.run_stop("error")
        self.assertEqual(self.triggers, [("stop_failure", "FILTERED")])

    def test_per_hook_mode_of_stop_failure_is_used(self) -> None:
        self.loaded["notification_settings"] = {
            "mode": "audio_only", "per_hook": {"stop_failure": "notification_only"}}
        self.run_stop("error")
        self.mocks["play_audio"].assert_not_called()
        self.mocks["desktop"].assert_called_once()
        self.assertEqual(self.mocks["desktop"].call_args.args[1], "Agent stopped with an error")
        self.assertEqual(self.mocks["desktop"].call_args.args[2], "critical")

    def test_tts_uses_the_stop_failure_message_not_the_assistant_reply(self) -> None:
        self.loaded["tts_settings"] = {"enabled": True, "speak_assistant_message": True}
        self.run_stop("error", last_assistant_message="All done")
        self.mocks["tts"].assert_called_once_with("Agent stopped with an error")

    def test_snooze_still_silences_it(self) -> None:
        self.mocks["snoozed"].return_value = True
        self.run_stop("error")
        self.assertEqual(self.triggers, [("stop_failure", "SNOOZED")])


class TestErrorIsNotReroutedOutsideCursor(_RunHookCase):
    config = {"enabled_hooks": {"stop": True, "stop_failure": True}}

    def test_other_invokers_keep_a_plain_stop(self) -> None:
        for invoker in ("claude-code", "codex", "unknown"):
            with self.subTest(invoker=invoker):
                self.invoker = invoker
                self.setUp()
                self.run_stop("error")
                self.assert_plain_stop()


class TestOnlyStopIsRerouted(_RunHookCase):
    config = {"enabled_hooks": {"subagent_stop": True, "stop_failure": True}}

    def test_subagent_stop_with_error_status_is_left_alone(self) -> None:
        self.hr.run_hook("subagent_stop", _payload("error"))
        self.assertEqual(self.triggers, [("subagent_stop", "PLAYED")])


class TestStopFailureContextWording(unittest.TestCase):
    """The Claude Code wording is untouched; the Cursor one claims no more
    than the payload says."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.hr = _load_hook_runner()

    def _context(self, payload: Dict[str, Any]) -> str:
        return self.hr.get_notification_context("stop_failure", payload, "standard")

    def test_claude_code_payloads_are_worded_as_before(self) -> None:
        self.assertEqual(self._context({"error_type": "rate_limit"}), "API error: rate_limit")
        self.assertEqual(self._context({"error": "overloaded"}), "API error: overloaded")
        self.assertEqual(self._context({}), "API error: unknown")
        self.assertEqual(self._context({"error": ""}), "API error: ")
        self.assertEqual(self._context({"status": "completed"}), "API error: unknown")

    def test_error_type_wins_over_status(self) -> None:
        self.assertEqual(self._context({"status": "error", "error_type": "billing_error"}),
                         "API error: billing_error")
        self.assertEqual(self._context({"status": "error", "error": "x"}), "API error: x")

    def test_cursor_error_status(self) -> None:
        self.assertEqual(self._context({"status": "error"}), "Agent stopped with an error")
        self.assertEqual(self._context({"status": "error", "error_message": "boom"}),
                         "Agent stopped with an error — boom")


class TestWebhookNamesTheEventThatFired(unittest.TestCase):
    """End to end through the real ``send_webhook``: the raw payload carries
    ``hook_type: stop_failure`` and keeps the original ``status``."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.hr = _load_hook_runner()

    def test_raw_payload(self) -> None:
        import json

        hr = self.hr
        config = {"webhook_settings": {"enabled": True, "url": "http://127.0.0.1:9/never",
                                       "format": "raw"}}
        sent = {}

        class _Proc:
            def __init__(self) -> None:
                self.stdin = mock.Mock()
                self.stdin.write.side_effect = lambda b: sent.setdefault("body", b)

        with mock.patch.object(hr.subprocess, "Popen", return_value=_Proc()), \
                mock.patch.object(hr, "log_event"), \
                mock.patch.object(hr, "_get_invoker", return_value="cursor"):
            hr.send_webhook("stop_failure", "Agent stopped with an error",
                            _payload("error"), config)
        body = json.loads(sent["body"].decode("utf-8"))
        self.assertEqual(body["hook_type"], "stop_failure")
        self.assertEqual(body["invoker"], "cursor")
        self.assertEqual(body["event_data"]["status"], "error")
        self.assertEqual(body["event_data"]["loop_count"], 0)


if __name__ == "__main__":
    unittest.main()
