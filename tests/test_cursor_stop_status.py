"""Tests for the ``status`` field of Cursor's native ``stop`` payload.

Cursor documents the input of its ``stop`` hook as (cursor.com/docs/hooks)::

    {"status": "completed" | "aborted" | "error", "loop_count": 0}

Two opt-in keys read it, and neither changes anything while it is absent:

* ``filters.<hook>.skip_if_aborted`` filters an event whose ``status`` is
  exactly ``"aborted"``.
* ``filters.stop.error_as_stop_failure``: under the Cursor invoker, a ``stop``
  whose ``status`` is exactly ``"error"`` becomes ``stop_failure``. The key is
  the whole opt-in -- ``stop_failure`` being enabled does not trigger it -- and
  once re-routed the plain ``stop_failure`` switch decides whether it plays.

Configurations are produced by the real CLI (``hooks enable``, ``hooks
enable-only``, ``set``) rather than written by hand, because the first version
of this feature was tested against a shape ``hooks enable-only`` never writes
and so missed that a user who enumerated one ``stop_failure`` variant has the
parent left on.

No live Cursor payload was captured; the payloads below are built from the
documentation.

Run with::

    python -m unittest discover tests
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
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
HOOK_RUNNER = REPO / "hooks" / "hook_runner.py"
CLI = REPO / "bin" / "audio-hooks.py"

STATUSES = ("completed", "aborted", "error")
REROUTE_KEY = "filters.stop.error_as_stop_failure"
ABORT_KEY = "filters.stop.skip_if_aborted"
CONTEXT = "Agent stopped with an error"

# The four shapes that matter, as CLI command lines.
FRESH: Tuple[Tuple[str, ...], ...] = ()
STOP_FAILURE_ON = (("hooks", "enable", "stop_failure"),)
# What a user gets from "only alert me on rate limits": stop false,
# stop_failure true (kept on as the variant's parent), that variant true and
# every other stop_failure variant false.
ENUMERATED = (("hooks", "enable-only", "stop_failure_rate_limit"),)
REROUTE_ON = (("set", REROUTE_KEY, "true"),)


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


def _child_env(state_dir: Path) -> Dict[str, str]:
    """Environment for a spawned CLI or runner, pinned to ``state_dir``."""
    env = os.environ.copy()
    for k in ("CLAUDE_PLUGIN_DATA", "PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT",
              "CLAUDE_AUDIO_HOOKS_DATA", "CURSOR_VERSION", "CLAUDE_HOOKS_DEBUG"):
        env.pop(k, None)
    _isolation.pin_data_dir(env, state_dir)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _cli(env: Dict[str, str], *args: str) -> Dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO), env=env, timeout=60,
    )
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        raise AssertionError(f"audio-hooks {' '.join(args)} printed no JSON (rc={proc.returncode})")
    if out.get("ok") is not True:
        raise AssertionError(f"audio-hooks {' '.join(args)} failed: {out.get('error')}")
    return out


def _write_config(state_dir: Path, env: Dict[str, str], commands) -> Dict[str, Any]:
    """Run CLI commands against ``state_dir``; return the stored preferences."""
    # A harmless write first, so the file exists even for the FRESH shape.
    _cli(env, "set", "playback_settings.debounce_ms", "60000")
    for command in commands:
        _cli(env, *command)
    return json.loads((state_dir / "user_preferences.json").read_text(encoding="utf-8"))


_CONFIG_CACHE: Dict[Any, Dict[str, Any]] = {}


def cli_config(*command_groups) -> Dict[str, Any]:
    """The preferences the real CLI stores after the given commands (cached)."""
    commands = tuple(c for group in command_groups for c in group)
    if commands not in _CONFIG_CACHE:
        state = Path(tempfile.mkdtemp(prefix="echook-cursor-stop-cfg-"))
        try:
            _CONFIG_CACHE[commands] = _write_config(state, _child_env(state), commands)
        finally:
            shutil.rmtree(state, ignore_errors=True)
    return copy.deepcopy(_CONFIG_CACHE[commands])


class TestCliShapes(unittest.TestCase):
    """Pins the shapes the rest of this module relies on."""

    def test_fresh_config_has_neither_key_and_no_stop_filters(self) -> None:
        config = cli_config(FRESH)
        self.assertNotIn("stop", config.get("filters", {}))
        self.assertEqual(config["tts_settings"]["messages"]["stop_failure"], "API error occurred")

    def test_enable_only_a_variant_leaves_the_parent_on(self) -> None:
        eh = cli_config(ENUMERATED)["enabled_hooks"]
        self.assertIs(eh["stop"], False)
        self.assertIs(eh["stop_failure"], True)
        self.assertIs(eh["stop_failure_rate_limit"], True)
        self.assertIs(eh["stop_failure_unknown"], False)

    def test_set_stores_real_booleans_and_get_reads_them(self) -> None:
        config = cli_config(REROUTE_ON, ((("set", ABORT_KEY, "true")),))
        self.assertIs(config["filters"]["stop"]["error_as_stop_failure"], True)
        self.assertIs(config["filters"]["stop"]["skip_if_aborted"], True)

    def test_keys_are_not_in_the_template(self) -> None:
        template = json.loads((REPO / "config" / "default_preferences.json").read_text(encoding="utf-8"))
        text = json.dumps(template)
        self.assertNotIn("error_as_stop_failure", text)
        self.assertNotIn("skip_if_aborted", text)


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
            self.assertFalse(self._skipped(_payload(value)), f"{value!r} was treated as aborted")
        self.assertFalse(self._skipped({"session_id": "t", "status": None}))

    def test_off_by_default(self) -> None:
        for config in ({}, {"filters": {}}, {"filters": {"stop": {}}},
                       {"filters": {"stop": {"skip_if_aborted": False}}},
                       cli_config(FRESH)):
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

    def test_the_reroute_key_is_never_read_as_a_filter(self) -> None:
        """It sits in ``filters.stop`` but must not filter anything, whatever
        type a hand-edited file gives it."""
        for value in (True, False, "true", "^error$", 1):
            config = {"filters": {"stop": {"error_as_stop_failure": value}}}
            for status in STATUSES + (None,):
                self.assertFalse(self._skipped(_payload(status), config), (value, status))


class TestRerouteKeyReader(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hr = _load_hook_runner()

    def test_only_exactly_true(self) -> None:
        on = self.hr._stop_error_reroute_enabled
        self.assertTrue(on({"filters": {"stop": {"error_as_stop_failure": True}}}))
        self.assertTrue(on(cli_config(REROUTE_ON)))
        for config in (None, {}, [], {"filters": None}, {"filters": []}, {"filters": {"stop": None}},
                       {"filters": {"stop": "x"}}, {"filters": {"stop": {}}},
                       {"filters": {"stop": {"error_as_stop_failure": False}}},
                       {"filters": {"stop": {"error_as_stop_failure": "true"}}},
                       {"filters": {"stop": {"error_as_stop_failure": 1}}},
                       {"filters": {"stop_failure": {"error_as_stop_failure": True}}},
                       {"error_as_stop_failure": True},
                       cli_config(FRESH), cli_config(STOP_FAILURE_ON), cli_config(ENUMERATED)):
            self.assertFalse(on(config), repr(config)[:80])


class _RunHookCase(unittest.TestCase):
    """Drives ``run_hook`` in-process with every side effect patched out.

    ``shape`` is a tuple of CLI command groups; the stored result is overlaid
    with the few settings the assertions need (audio-only mode, a long
    debounce, a webhook that is mocked out).
    """

    invoker = "cursor"
    shape: Tuple[Any, ...] = (FRESH,)

    @classmethod
    def setUpClass(cls) -> None:
        cls.hr = _load_hook_runner()

    def setUp(self) -> None:
        self.start(self.shape, self.invoker)

    def start(self, shape, invoker: str = "cursor") -> None:
        hr = self.hr
        for p in getattr(self, "_patchers", []):
            p.stop()
        self._patchers: List[Any] = []
        queue = Path(tempfile.mkdtemp(prefix="echook-cursor-stop-"))
        self.addCleanup(lambda: shutil.rmtree(queue, ignore_errors=True))
        self.queue = queue
        self.triggers: List[Tuple[str, str]] = []
        self.audio_for: List[str] = []
        self.contexts: List[Tuple[str, str]] = []

        def _audio(hook: str) -> Path:
            self.audio_for.append(hook)
            return Path(__file__)

        config = cli_config(*shape)
        config["playback_settings"]["debounce_ms"] = 60000
        config["notification_settings"]["mode"] = "audio_only"
        config["webhook_settings"]["enabled"] = True
        config["webhook_settings"]["url"] = "http://127.0.0.1:9/never"
        self.loaded = config
        patches = {
            "prefs": mock.patch.object(hr, "_prefs", return_value=SimpleNamespace(queue_dir=queue)),
            "ensured": mock.patch.object(hr, "_queue_dir_ensured", True),
            "config": mock.patch.object(hr, "load_config", return_value=config),
            "log_event": mock.patch.object(hr, "log_event"),
            "log_trigger": mock.patch.object(
                hr, "log_trigger", side_effect=lambda h, s, d="": self.triggers.append((h, s))),
            "invoker": mock.patch.object(hr, "_get_invoker", return_value=invoker),
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
            self._patchers.append(p)

    def tearDown(self) -> None:
        for p in self._patchers:
            p.stop()
        self._patchers = []

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

    def assert_silent(self, hook: str = "stop") -> None:
        self.assertEqual(self.triggers, [(hook, "DISABLED")])
        self.mocks["play_audio"].assert_not_called()
        self.mocks["desktop"].assert_not_called()
        self.mocks["tts"].assert_not_called()
        self.mocks["webhook"].assert_not_called()
        self.assertEqual(list(self.queue.iterdir()), [])

    def assert_rerouted(self) -> None:
        self.assertEqual(self.triggers, [("stop_failure", "PLAYED")])
        self.assertEqual(self.audio_for, ["stop_failure"])
        self.assertEqual(self.contexts, [("stop_failure", CONTEXT)])
        self.assertTrue(self.stamp("stop_failure").exists())
        self.assertFalse(self.stamp("stop").exists(),
                         "a re-routed error opened the stop debounce window")


class TestWithoutTheRerouteKeyNothingChanges(_RunHookCase):
    """Every status, under every CLI-written shape, behaves as on master: the
    event stays ``stop`` and only the ``stop`` switch decides."""

    def test_fresh_install_plays_stop_for_every_status(self) -> None:
        for status in STATUSES + (None,):
            with self.subTest(status=status):
                self.start((FRESH,))
                self.run_stop(status)
                self.assert_plain_stop()

    def test_stop_failure_enabled_still_plays_stop(self) -> None:
        """Someone who enabled stop_failure for Claude Code's API errors."""
        for status in STATUSES + (None,):
            with self.subTest(status=status):
                self.start((STOP_FAILURE_ON,))
                self.run_stop(status)
                self.assert_plain_stop()

    def test_enumerated_variant_shape_stays_silent(self) -> None:
        """``hooks enable-only stop_failure_rate_limit`` turns ``stop`` off and
        leaves the parent ``stop_failure`` on. The first version of this
        feature re-routed an errored Cursor turn here and played the generic
        failure sound; on master the turn was silent."""
        for status in STATUSES + (None,):
            with self.subTest(status=status):
                self.start((ENUMERATED,))
                self.run_stop(status)
                self.assert_silent("stop")

    def test_enable_only_stop_failure_stays_silent(self) -> None:
        self.start(((("hooks", "enable-only", "stop_failure"),),))
        self.run_stop("error")
        self.assert_silent("stop")

    def test_key_explicitly_false(self) -> None:
        self.start((STOP_FAILURE_ON, (("set", REROUTE_KEY, "false"),)))
        self.run_stop("error")
        self.assert_plain_stop()

    def test_stop_filters_and_webhook_allowlist_still_see_a_stop(self) -> None:
        self.start((STOP_FAILURE_ON,))
        self.loaded["filters"] = {"stop": {"status_exclude": "^error$"}}
        self.run_stop("error")
        self.assertEqual(self.triggers, [("stop", "FILTERED")])


class TestRerouteKeyOn(_RunHookCase):
    shape = (STOP_FAILURE_ON, REROUTE_ON)

    def test_error_is_delivered_as_stop_failure(self) -> None:
        self.run_stop("error")
        self.assert_rerouted()

    def test_reroute_is_logged_and_log_context_follows(self) -> None:
        self.run_stop("error")
        actions = [c.args[1] for c in self.mocks["log_event"].call_args_list]
        self.assertIn("stop_rerouted_to_stop_failure", actions)
        self.assertEqual(self.hr._current_hook_type, "stop_failure")

    def test_completed_aborted_absent_and_unknown_stay_stop(self) -> None:
        for status in ("completed", "aborted", None, "Error", "ERROR", "failed", "", 1, True,
                       ["error"]):
            with self.subTest(status=status):
                self.start(self.shape)
                self.run_stop(status)
                self.assert_plain_stop()

    def test_error_does_not_debounce_a_following_completion(self) -> None:
        self.run_stop("error")
        self.run_stop("completed")
        self.assertEqual(self.triggers, [("stop_failure", "PLAYED"), ("stop", "PLAYED")])

    def test_stop_filters_no_longer_apply_but_stop_failure_filters_do(self) -> None:
        self.loaded["filters"]["stop"]["status_exclude"] = "^error$"
        self.run_stop("error")
        self.assertEqual(self.triggers, [("stop_failure", "PLAYED")])
        self.start(self.shape)
        self.loaded["filters"]["stop_failure"] = {"status_exclude": "^error$"}
        self.run_stop("error")
        self.assertEqual(self.triggers, [("stop_failure", "FILTERED")])

    def test_per_hook_mode_of_stop_failure_is_used(self) -> None:
        self.loaded["notification_settings"]["per_hook"] = {"stop_failure": "notification_only"}
        self.run_stop("error")
        self.mocks["play_audio"].assert_not_called()
        self.mocks["desktop"].assert_called_once()
        self.assertEqual(self.mocks["desktop"].call_args.args[1], CONTEXT)
        self.assertEqual(self.mocks["desktop"].call_args.args[2], "critical")

    def test_snooze_still_silences_it(self) -> None:
        self.mocks["snoozed"].return_value = True
        self.run_stop("error")
        self.assertEqual(self.triggers, [("stop_failure", "SNOOZED")])

    def test_abort_filter_and_reroute_together(self) -> None:
        self.start((STOP_FAILURE_ON, REROUTE_ON, (("set", ABORT_KEY, "true"),)))
        self.run_stop("aborted")
        self.assertEqual(self.triggers, [("stop", "FILTERED")])
        self.run_stop("error")
        self.assertEqual(self.triggers[-1], ("stop_failure", "PLAYED"))


class TestRerouteKeyOnGatingIsStopFailuresOwn(_RunHookCase):
    """The re-route is unconditional on the key; after it the ordinary
    ``stop_failure`` switch decides, and there is no fallback to ``stop``."""

    def test_stop_failure_off_means_an_errored_turn_is_silent(self) -> None:
        """Key on, stop_failure never enabled (default off): silence, by
        design -- the documented instruction is "enable both"."""
        self.start((REROUTE_ON,))
        self.run_stop("error")
        self.assert_silent("stop_failure")
        self.start((REROUTE_ON,))
        self.run_stop("completed")
        self.assert_plain_stop()

    def test_stop_failure_explicitly_disabled(self) -> None:
        self.start((REROUTE_ON, (("hooks", "disable", "stop_failure"),)))
        self.run_stop("error")
        self.assert_silent("stop_failure")

    def test_plays_even_when_stop_is_off(self) -> None:
        """"Tell me only when it failed"."""
        shape = ((("hooks", "enable-only", "stop_failure"),), REROUTE_ON)
        self.start(shape)
        self.run_stop("error")
        self.assert_rerouted()
        self.start(shape)
        self.run_stop("completed")
        self.assert_silent("stop")

    def test_enumerated_variant_shape_plays_the_generic_failure_sound(self) -> None:
        """A re-routed event carries no variant, so with the key on the parent
        switch that ``enable-only <variant>`` left on lets it through. This is
        the stated behaviour, pinned so a change to it is deliberate."""
        self.start((ENUMERATED, REROUTE_ON))
        self.run_stop("error")
        self.assert_rerouted()
        self.start((ENUMERATED, REROUTE_ON))
        self.run_stop("completed")
        self.assert_silent("stop")


class TestReroutedTts(_RunHookCase):
    """Runs with the shipped ``tts_settings.messages``, whose ``stop_failure``
    entry is "API error occurred"."""

    shape = (STOP_FAILURE_ON, REROUTE_ON, (("set", "tts_settings.enabled", "true"),))

    def test_the_shipped_default_message_is_present(self) -> None:
        self.assertIs(self.loaded["tts_settings"]["enabled"], True)
        self.assertEqual(self.loaded["tts_settings"]["messages"]["stop_failure"], "API error occurred")

    def test_rerouted_event_speaks_the_context_not_the_api_error_default(self) -> None:
        self.run_stop("error")
        self.mocks["tts"].assert_called_once_with(CONTEXT)

    def test_a_customised_stop_failure_message_is_not_used_either(self) -> None:
        self.loaded["tts_settings"]["messages"]["stop_failure"] = "The API fell over"
        self.run_stop("error")
        self.mocks["tts"].assert_called_once_with(CONTEXT)

    def test_assistant_reply_is_not_spoken_for_a_rerouted_event(self) -> None:
        self.loaded["tts_settings"]["speak_assistant_message"] = True
        self.run_stop("error", last_assistant_message="All done")
        self.mocks["tts"].assert_called_once_with(CONTEXT)

    def test_a_plain_stop_still_uses_its_configured_message(self) -> None:
        self.run_stop("completed")
        self.mocks["tts"].assert_called_once_with("Task completed")

    def test_a_real_stop_failure_event_still_uses_messages_stop_failure(self) -> None:
        """Claude Code's own StopFailure is untouched."""
        self.start(self.shape, invoker="claude-code")
        self.hr.run_hook("stop_failure", {"session_id": "t", "error_type": "rate_limit"})
        self.mocks["tts"].assert_called_once_with("API error occurred")

    def test_without_the_key_an_errored_turn_speaks_as_a_stop(self) -> None:
        self.start((STOP_FAILURE_ON, (("set", "tts_settings.enabled", "true"),)))
        self.run_stop("error")
        self.mocks["tts"].assert_called_once_with("Task completed")


class TestErrorIsNotReroutedOutsideCursor(_RunHookCase):
    def test_other_invokers_keep_a_plain_stop(self) -> None:
        for invoker in ("claude-code", "codex", "unknown"):
            with self.subTest(invoker=invoker):
                self.start((STOP_FAILURE_ON, REROUTE_ON), invoker=invoker)
                self.run_stop("error")
                self.assert_plain_stop()


class TestAbortedFilterIsNotInvokerScoped(_RunHookCase):
    """The filter keys on the payload, like its siblings."""

    def test_filters_wherever_the_payload_says_aborted(self) -> None:
        for invoker in ("cursor", "claude-code", "codex", "unknown"):
            with self.subTest(invoker=invoker):
                self.start(((("set", ABORT_KEY, "true"),),), invoker=invoker)
                self.run_stop("aborted")
                self.assertEqual(self.triggers, [("stop", "FILTERED")])
                self.assertFalse(self.stamp("stop").exists())
                self.run_stop("completed")
                self.assertEqual(self.triggers[-1], ("stop", "PLAYED"))

    def test_a_payload_without_status_is_untouched(self) -> None:
        self.start(((("set", ABORT_KEY, "true"),),), invoker="claude-code")
        self.run_stop()
        self.assert_plain_stop()


class TestOnlyStopIsRerouted(_RunHookCase):
    shape = (STOP_FAILURE_ON, REROUTE_ON, (("hooks", "enable", "subagent_stop"),))

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
        self.assertEqual(self._context({"status": "error"}), CONTEXT)
        self.assertEqual(self._context({"status": "error", "error_message": "boom"}),
                         CONTEXT + " — boom")


class TestWebhookNamesTheEventThatFired(unittest.TestCase):
    """Through the real ``send_webhook``: the raw payload carries
    ``hook_type: stop_failure`` and keeps the original ``status``."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.hr = _load_hook_runner()

    def test_raw_payload(self) -> None:
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
            hr.send_webhook("stop_failure", CONTEXT, _payload("error"), config)
        body = json.loads(sent["body"].decode("utf-8"))
        self.assertEqual(body["hook_type"], "stop_failure")
        self.assertEqual(body["invoker"], "cursor")
        self.assertEqual(body["event_data"]["status"], "error")
        self.assertEqual(body["event_data"]["loop_count"], 0)


class TestEndToEndThroughMain(unittest.TestCase):
    """The real ``hook_runner.py`` as a subprocess: real invoker detection
    (``CURSOR_VERSION`` in the environment), a preferences file written by the
    real CLI into a pinned data dir, and assertions on the NDJSON log.

    ``pin_data_dir`` snoozes the directory so a spawned runner stops before any
    sound. Snooze is checked before the filters, so it would hide ``FILTERED``;
    these tests therefore set ``notification_settings.mode`` to ``disabled``
    (no audio, no toast), confirm from the stored file that TTS and the webhook
    are off as well, and only then lift the snooze.
    """

    def setUp(self) -> None:
        self.state = Path(tempfile.mkdtemp(prefix="echook-cursor-stop-e2e-"))
        self.addCleanup(lambda: shutil.rmtree(self.state, ignore_errors=True))
        self.env = _child_env(self.state)

    def configure(self, *command_groups) -> None:
        commands = [c for group in command_groups for c in group]
        commands.append(("set", "notification_settings.mode", "disabled"))
        stored = _write_config(self.state, self.env, commands)
        # Nothing below may run unless the stored config is provably silent.
        self.assertEqual(stored["notification_settings"]["mode"], "disabled")
        self.assertEqual(stored["notification_settings"].get("per_hook", {}).get("stop"), None)
        self.assertEqual(stored["notification_settings"].get("per_hook", {}).get("stop_failure"), None)
        self.assertIs(stored["tts_settings"]["enabled"], False)
        self.assertIs(stored["webhook_settings"]["enabled"], False)
        snooze = self.state / "queue" / "snooze_until"
        self.assertTrue(snooze.exists(), "pin_data_dir did not snooze the data dir")
        snooze.unlink()

    def fire(self, status: Any = None, hook: str = "stop") -> List[Tuple[str, str]]:
        """Run the hook under Cursor; return the (hook, status) rows it logged."""
        log = self.state / "logs" / "events.ndjson"
        before = len(log.read_text(encoding="utf-8").splitlines()) if log.exists() else 0
        env = dict(self.env)
        env["CURSOR_VERSION"] = "3.2.16"
        env["CLAUDE_HOOKS_DEBUG"] = "1"
        proc = subprocess.run(
            [sys.executable, str(HOOK_RUNNER), hook],
            input=json.dumps(_payload(status)),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        self.assertEqual(proc.stdout, "", "the runner must print nothing for a stop")
        lines = log.read_text(encoding="utf-8").splitlines()[before:]
        self.events = [json.loads(line) for line in lines if line.strip()]
        for event in self.events:
            self.assertEqual(event.get("invoker"), "cursor")
        return [(e["hook"], e["status"]) for e in self.events if e.get("action") == "hook_status"]

    def actions(self) -> List[str]:
        return [e.get("action") for e in self.events]

    DELIVERED_SILENTLY = ["AUDIO_SKIPPED", "NOTIFICATION_SKIPPED"]

    def assert_delivered_as(self, rows: List[Tuple[str, str]], hook: str) -> None:
        self.assertEqual(rows, [(hook, s) for s in self.DELIVERED_SILENTLY])

    def test_default_config_every_status_is_a_plain_stop(self) -> None:
        self.configure(FRESH)
        for status in STATUSES + (None,):
            with self.subTest(status=status):
                for stamp in (self.state / "queue").glob("*_last_played"):
                    stamp.unlink()
                self.assert_delivered_as(self.fire(status), "stop")
                self.assertNotIn("stop_rerouted_to_stop_failure", self.actions())
                self.assertFalse((self.state / "queue" / "stop_failure_last_played").exists())

    def test_stop_failure_enabled_without_the_key_is_a_plain_stop(self) -> None:
        self.configure(STOP_FAILURE_ON)
        self.assert_delivered_as(self.fire("error"), "stop")
        self.assertNotIn("stop_rerouted_to_stop_failure", self.actions())

    def test_key_on_and_stop_failure_on_is_rerouted(self) -> None:
        self.configure(STOP_FAILURE_ON, REROUTE_ON)
        self.assert_delivered_as(self.fire("error"), "stop_failure")
        self.assertIn("stop_rerouted_to_stop_failure", self.actions())
        rerouted = [e for e in self.events if e.get("action") == "stop_rerouted_to_stop_failure"][0]
        self.assertEqual(rerouted.get("rerouted_from"), "stop")
        self.assertTrue((self.state / "queue" / "stop_failure_last_played").exists())
        self.assertFalse((self.state / "queue" / "stop_last_played").exists())
        self.assert_delivered_as(self.fire("completed"), "stop")

    def test_key_on_and_stop_failure_off_is_silent(self) -> None:
        self.configure(REROUTE_ON)
        self.assertEqual(self.fire("error"), [("stop_failure", "DISABLED")])
        self.assert_delivered_as(self.fire("completed"), "stop")

    def test_enumeration_shape_without_the_key_is_silent_as_before(self) -> None:
        self.configure(ENUMERATED)
        for status in STATUSES:
            with self.subTest(status=status):
                self.assertEqual(self.fire(status), [("stop", "DISABLED")])
                self.assertNotIn("stop_rerouted_to_stop_failure", self.actions())

    def test_enumeration_shape_with_the_key_on(self) -> None:
        self.configure(ENUMERATED, REROUTE_ON)
        self.assert_delivered_as(self.fire("error"), "stop_failure")
        self.assertEqual(self.fire("completed"), [("stop", "DISABLED")])

    def test_aborted_filter(self) -> None:
        self.configure((("set", ABORT_KEY, "true"),))
        self.assertEqual(self.fire("aborted"), [("stop", "FILTERED")])
        self.assertFalse((self.state / "queue" / "stop_last_played").exists())
        self.assert_delivered_as(self.fire("completed"), "stop")

    def test_aborted_without_the_filter_is_a_plain_stop(self) -> None:
        self.configure(FRESH)
        self.assert_delivered_as(self.fire("aborted"), "stop")


if __name__ == "__main__":
    unittest.main()
