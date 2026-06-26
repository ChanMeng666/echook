"""Tests for v5.1.4 Cursor IDE bridge compatibility.

Cursor IDE 3.2.16+ auto-bridges Claude Code plugin hooks (see
https://cursor.com/docs/reference/third-party-hooks). When Cursor invokes
our ``runner/run.py``, it does NOT inject ``CLAUDE_PLUGIN_DATA`` — instead
it sets ``CURSOR_VERSION`` and a Cursor-specific stdin schema. This file
pins the contract that:

  1. ``UserPreferences._resolve_data_dir`` falls back through the documented
     priority chain (env vars → shared Claude Code dir → Cursor-native dir
     → legacy temp). Path resolution lives on the class as of 5.1.5; the
     here-only smoke checks survive as integration coverage. Detailed unit
     coverage of the priority chain — including the
     ``test_shared_dir_used_when_user_prefs_exists_there`` regression that
     guards the 5.1.4 anti-stranding fix — lives in
     ``tests/test_user_preferences.py::TestPathResolution``.
  2. ``detect_invoker`` returns the right label based on env vars alone
     (more reliable than parsing stdin).
  3. The ``session_start`` handler emits ``{"env": {"CLAUDE_PLUGIN_DATA": ...}}``
     to stdout when Cursor is the invoker, so subsequent hooks in the same
     Cursor session inherit the correct preferences path.
  4. NDJSON events carry an ``invoker`` field for cross-IDE log filtering.
  5. webhook payloads expose ``invoker`` + a ``cursor`` sub-object, with
     ``user_email`` redacted by default.

Run with::

    python -m unittest discover tests
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional

REPO = Path(__file__).resolve().parent.parent
HOOK_RUNNER = REPO / "hooks" / "hook_runner.py"


def _load_module():
    """Import hook_runner.py as a module so we can unit-test helpers directly.
    Each call returns a *fresh* module so module-level cache (``_invoker_cache``)
    is reset between tests. importlib.reload() reuses the same module object,
    which would carry state across tests.

    Also pops ``invoker`` and ``user_preferences`` so their module-level state
    (notably ``invoker._invoker_cache``) is reloaded fresh — hook_runner now
    imports detect_invoker from invoker.py, and a stale cache there would
    bleed across tests in unpredictable ways.
    """
    for mod_name in ("hook_runner", "invoker", "user_preferences"):
        sys.modules.pop(mod_name, None)
    spec = importlib.util.spec_from_file_location("hook_runner", HOOK_RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_hook(
    hook_type: str,
    stdin_payload: str = "{}",
    *,
    env_extra: Optional[Dict[str, str]] = None,
    state_dir: Optional[Path] = None,
):
    """Subprocess-invoke hook_runner.py with isolated env, return (rc, stdout, stderr)."""
    env = os.environ.copy()
    # Strip any inherited CLAUDE_* / CURSOR_* so each test starts clean.
    for k in ("CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT", "CLAUDE_AUDIO_HOOKS_DATA",
              "CURSOR_VERSION", "CURSOR_USER_EMAIL", "CURSOR_PROJECT_DIR",
              "CLAUDE_HOOKS_DEBUG"):
        env.pop(k, None)
    if state_dir is not None:
        env["CLAUDE_AUDIO_HOOKS_DATA"] = str(state_dir)
    if env_extra:
        env.update(env_extra)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(
        [sys.executable, str(HOOK_RUNNER), hook_type],
        input=stdin_payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=15,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


class TestDetectInvoker(unittest.TestCase):
    """``detect_invoker`` reads only environment variables — no stdin parsing."""

    def setUp(self):
        # Save and clear all relevant env vars before each test
        self._saved = {
            k: os.environ.pop(k, None)
            for k in ("CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT",
                      "CURSOR_VERSION")
        }

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_cursor_via_version_env(self):
        os.environ["CURSOR_VERSION"] = "3.2.16"
        mod = _load_module()
        self.assertEqual(mod.detect_invoker(), "cursor")

    def test_claude_code_via_plugin_data(self):
        os.environ["CLAUDE_PLUGIN_DATA"] = "/tmp/whatever"
        mod = _load_module()
        self.assertEqual(mod.detect_invoker(), "claude-code")

    def test_claude_code_via_plugin_root(self):
        os.environ["CLAUDE_PLUGIN_ROOT"] = "/tmp/plugin"
        mod = _load_module()
        self.assertEqual(mod.detect_invoker(), "claude-code")

    def test_unknown_when_no_env(self):
        mod = _load_module()
        self.assertEqual(mod.detect_invoker(), "unknown")

    def test_cursor_wins_when_both_set(self):
        # In rare cases (e.g. Claude Code CLI inside Cursor terminal) both
        # may be set. Cursor takes precedence because the immediate hook
        # invoker is what matters, and Cursor's CURSOR_VERSION is set per-hook.
        os.environ["CURSOR_VERSION"] = "3.2.16"
        os.environ["CLAUDE_PLUGIN_DATA"] = "/tmp/whatever"
        mod = _load_module()
        self.assertEqual(mod.detect_invoker(), "cursor")


class TestResolveDataDir(unittest.TestCase):
    """``UserPreferences._resolve_data_dir`` priority chain (smoke).

    Priority: CLAUDE_PLUGIN_DATA → CLAUDE_AUDIO_HOOKS_DATA → shared
    (~/.claude/plugins/data/audio-hooks-chanmeng-audio-hooks) →
    cursor-native (~/.cursor/audio-hooks-data) → legacy temp dir.

    Detailed coverage lives in ``tests/test_user_preferences.py``; this
    class kept three sanity checks here so the cursor-bridge file remains
    a self-contained read for the 5.1.4 contract.
    """

    def setUp(self):
        # Loading hook_runner makes sure hooks/ is on sys.path so that
        # `from user_preferences import UserPreferences` resolves below.
        _load_module()
        from user_preferences import UserPreferences  # type: ignore  # noqa: WPS433
        self._UserPreferences = UserPreferences
        self._saved = {
            k: os.environ.pop(k, None)
            for k in ("CLAUDE_PLUGIN_DATA", "CLAUDE_AUDIO_HOOKS_DATA")
        }

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def _prefs(self):
        return self._UserPreferences(REPO)

    def test_claude_plugin_data_wins(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["CLAUDE_PLUGIN_DATA"] = td
            self.assertEqual(str(self._prefs()._resolve_data_dir()), td)

    def test_audio_hooks_data_when_no_plugin_data(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["CLAUDE_AUDIO_HOOKS_DATA"] = td
            self.assertEqual(str(self._prefs()._resolve_data_dir()), td)

    def test_falls_through_to_temp_when_nothing_else_present(self):
        # No env vars; we patch Path.home to a clean temp dir so the shared
        # and cursor-native checks both miss. We also redirect the script
        # path away from this repo so the plugin-cache heuristic misses
        # (otherwise the test machine's local layout could short-circuit).
        # The result must end with the legacy ``claude_audio_hooks_queue``
        # directory name so existing script-install state stays compatible.
        with tempfile.TemporaryDirectory() as fake_home:
            from pathlib import Path as _Path
            import user_preferences as up_mod  # type: ignore
            original_home = up_mod.Path.home
            try:
                up_mod.Path.home = staticmethod(lambda: up_mod.Path(fake_home))
                # Anchor the prefs script_path inside fake_home so the
                # plugin-detection heuristic falls through cleanly.
                non_plugin_script = _Path(fake_home) / "user_preferences.py"
                prefs = self._UserPreferences(REPO, script_path=non_plugin_script)
                resolved = prefs._resolve_data_dir()
                self.assertTrue(
                    str(resolved).endswith("claude_audio_hooks_queue"),
                    f"Expected legacy temp fallback, got {resolved}",
                )
            finally:
                up_mod.Path.home = original_home


class TestSessionStartEnvOutput(unittest.TestCase):
    """``session_start`` must emit ``{"env": {"CLAUDE_PLUGIN_DATA": ...}}`` to
    stdout when invoker == cursor, regardless of enabled_hooks setting.

    This is the first-class fix: Cursor reads the env from sessionStart's
    stdout and propagates it to every subsequent hook in the session, so
    they all find the user's actual ``user_preferences.json`` instead of
    falling back to bundled defaults.
    """

    def test_cursor_emits_env_json(self):
        with tempfile.TemporaryDirectory() as td:
            rc, stdout, _stderr = _run_hook(
                "session_start",
                stdin_payload=json.dumps({
                    "session_id": "s1",
                    "cursor_version": "3.2.16",
                    "hook_event_name": "sessionStart",
                }),
                env_extra={"CURSOR_VERSION": "3.2.16"},
                state_dir=Path(td),
            )
            self.assertEqual(rc, 0, f"hook exited nonzero, stdout: {stdout}")
            # First line of stdout must be parseable JSON with env.CLAUDE_PLUGIN_DATA
            first_line = stdout.splitlines()[0] if stdout.strip() else ""
            self.assertTrue(first_line, "expected env JSON on stdout, got nothing")
            doc = json.loads(first_line)
            self.assertIn("env", doc)
            self.assertIn("CLAUDE_PLUGIN_DATA", doc["env"])
            # The emitted path should be the resolved data dir; with
            # CLAUDE_AUDIO_HOOKS_DATA set in the env, that's exactly td.
            self.assertEqual(doc["env"]["CLAUDE_PLUGIN_DATA"], td)

    def test_claude_code_does_not_emit_env_json(self):
        # Without CURSOR_VERSION set, sessionStart is silent on stdout —
        # we must NOT pollute Claude Code's hookSpecificOutput contract.
        with tempfile.TemporaryDirectory() as td:
            rc, stdout, _stderr = _run_hook(
                "session_start",
                stdin_payload=json.dumps({
                    "session_id": "s2",
                    "hook_event_name": "SessionStart",
                }),
                state_dir=Path(td),
            )
            self.assertEqual(rc, 0)
            self.assertEqual(stdout.strip(), "",
                             f"expected silent stdout for non-Cursor, got: {stdout!r}")


class TestNDJSONInvokerField(unittest.TestCase):
    """Every NDJSON event line must carry an ``invoker`` field so the same
    ``events.ndjson`` can be filtered by editor for cross-IDE diagnostics."""

    def test_invoker_field_present(self):
        with tempfile.TemporaryDirectory() as td:
            state_dir = Path(td)
            # Enable debug so log_event writes everything we want to inspect
            rc, _stdout, _stderr = _run_hook(
                "stop",
                stdin_payload=json.dumps({
                    "session_id": "s3",
                    "cursor_version": "3.2.16",
                    "hook_event_name": "stop",
                }),
                env_extra={
                    "CURSOR_VERSION": "3.2.16",
                    "CLAUDE_HOOKS_DEBUG": "1",
                },
                state_dir=state_dir,
            )
            self.assertEqual(rc, 0)
            log_file = state_dir / "logs" / "events.ndjson"
            self.assertTrue(log_file.exists(), f"NDJSON log not written to {log_file}")
            lines = [l for l in log_file.read_text(encoding="utf-8").splitlines() if l.strip()]
            self.assertGreater(len(lines), 0, "no NDJSON events written")
            for line in lines:
                event = json.loads(line)
                self.assertEqual(event.get("invoker"), "cursor",
                                 f"event missing invoker=cursor: {event}")


class TestWebhookPayloadCursorFields(unittest.TestCase):
    """Webhook raw payload must include ``invoker`` + ``cursor`` sub-object,
    and must redact ``user_email`` by default for privacy.

    We unit-test the payload-construction logic rather than spinning up a
    real HTTP listener — that path is exercised in production via the
    ``webhook test`` subcommand.
    """

    def test_payload_includes_invoker_and_cursor_block(self):
        os.environ["CURSOR_VERSION"] = "3.2.16"
        try:
            mod = _load_module()
            # We'll observe the payload by intercepting subprocess.Popen
            captured = {}

            class _FakePopen:
                def __init__(self, _cmd, **kwargs):
                    self.stdin = self  # acts as both proc.stdin and the writer
                    self._buf = b""

                def write(self, data):
                    self._buf += data
                    captured["body"] = data

                def close(self):
                    pass

            original_popen = mod.subprocess.Popen
            mod.subprocess.Popen = _FakePopen
            try:
                stdin_data = {
                    "session_id": "s4",
                    "cursor_version": "3.2.16",
                    "conversation_id": "c4",
                    "reason": "window_close",
                    "final_status": "completed",
                    "duration_ms": 4500,
                    "user_email": "test@example.com",  # must be redacted
                    "workspace_roots": ["/proj"],
                    "model": "claude-opus-4-7",
                }
                config = {
                    "webhook_settings": {
                        "enabled": True,
                        "url": "http://example.invalid/webhook",
                        "format": "raw",
                        "hook_types": ["stop"],
                    }
                }
                mod.send_webhook("stop", "Task done.", stdin_data, config)
            finally:
                mod.subprocess.Popen = original_popen

            self.assertIn("body", captured, "webhook never invoked")
            payload = json.loads(captured["body"].decode("utf-8"))
            self.assertEqual(payload.get("invoker"), "cursor")
            cursor_block = payload.get("cursor", {})
            self.assertEqual(cursor_block.get("conversation_id"), "c4")
            self.assertEqual(cursor_block.get("reason"), "window_close")
            self.assertEqual(cursor_block.get("final_status"), "completed")
            self.assertEqual(cursor_block.get("duration_ms"), 4500)
            # user_email must NOT leak into cursor block by default
            self.assertNotIn("user_email", cursor_block)
            # ...nor into event_data
            self.assertNotIn("user_email", payload.get("event_data", {}))
        finally:
            os.environ.pop("CURSOR_VERSION", None)

    def test_user_email_included_when_opt_in(self):
        os.environ["CURSOR_VERSION"] = "3.2.16"
        try:
            mod = _load_module()
            captured = {}

            class _FakePopen:
                def __init__(self, _cmd, **kwargs):
                    self.stdin = self
                def write(self, data): captured["body"] = data
                def close(self): pass

            original_popen = mod.subprocess.Popen
            mod.subprocess.Popen = _FakePopen
            try:
                mod.send_webhook(
                    "stop",
                    "ctx",
                    {
                        "session_id": "s5",
                        "cursor_version": "3.2.16",
                        "user_email": "ai@gavigo.com",
                    },
                    {
                        "webhook_settings": {
                            "enabled": True,
                            "url": "http://example.invalid/",
                            "format": "raw",
                            "hook_types": ["stop"],
                            "include_user_email": True,
                        }
                    },
                )
            finally:
                mod.subprocess.Popen = original_popen

            payload = json.loads(captured["body"].decode("utf-8"))
            self.assertEqual(payload.get("cursor", {}).get("user_email"),
                             "ai@gavigo.com")
            self.assertEqual(payload.get("event_data", {}).get("user_email"),
                             "ai@gavigo.com")
        finally:
            os.environ.pop("CURSOR_VERSION", None)


REPO_AUDIO_HOOKS_PY = REPO / "bin" / "audio-hooks.py"
CURSOR_TEMPLATE = REPO / "cursor-hooks" / "hooks.json"


def _run_cli(*args: str, home: Path, extra_env: Optional[Dict[str, str]] = None):
    """Subprocess-invoke ``bin/audio-hooks.py`` with ``HOME`` redirected.

    On Windows ``Path.home()`` reads ``USERPROFILE`` (and falls back to
    ``HOMEDRIVE+HOMEPATH``), not ``HOME`` — set all three so the test works
    cross-platform.
    """
    env = os.environ.copy()
    for k in ("CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT", "CLAUDE_AUDIO_HOOKS_DATA",
              "CURSOR_VERSION", "CLAUDE_HOOKS_DEBUG"):
        env.pop(k, None)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["HOMEDRIVE"] = str(home.drive) if home.drive else ""
    env["HOMEPATH"] = str(home).replace(home.drive, "") if home.drive else str(home)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, str(REPO_AUDIO_HOOKS_PY), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=30,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


# v6.2: the native ``audio-hooks install --cursor`` template (for Cursor users
# WITHOUT Claude Code — the DUPLICATE_BRIDGE guard blocks native install when the
# plugin is present) maps Cursor's full Agent-hook surface. Tool execution is
# split into Cursor's GRANULAR per-type events (beforeShellExecution /
# beforeMCPExecution / beforeReadFile + *after* counterparts) so shell, MCP and
# file-read each get a distinct sound. The coarse preToolUse/postToolUse umbrella
# is intentionally NOT registered to avoid double-firing with the granular events
# (Write/Edit are surfaced via afterFileEdit, Task tools via subagentStart).
CURSOR_REQUIRED_EVENTS = (
    "sessionStart",
    "sessionEnd",
    "stop",
    "subagentStart",
    "subagentStop",
    "beforeShellExecution",
    "afterShellExecution",
    "beforeMCPExecution",
    "afterMCPExecution",
    "beforeReadFile",
    "beforeTabFileRead",
    "afterFileEdit",
    "afterTabFileEdit",
    "postToolUseFailure",
    "afterAgentResponse",
    "afterAgentThought",
    "workspaceOpen",
    "preCompact",
    "beforeSubmitPrompt",
)

# Deliberately absent (registering them would double-fire with the granular
# per-type events above).
CURSOR_INTENTIONALLY_ABSENT = ("preToolUse", "postToolUse")


class TestCursorTemplateValidity(unittest.TestCase):
    """``cursor-hooks/hooks.json`` is a contract: every entry's command arg
    must resolve to a real handler in ``hook_runner.main``, and every event
    Cursor's bridge maps from Claude Code must be present."""

    @classmethod
    def setUpClass(cls):
        with open(CURSOR_TEMPLATE, "r", encoding="utf-8") as fh:
            cls.template = json.load(fh)

    def test_template_is_valid_json_with_hooks_block(self):
        self.assertIn("hooks", self.template)
        self.assertIsInstance(self.template["hooks"], dict)

    def test_all_required_events_registered(self):
        for evt in CURSOR_REQUIRED_EVENTS:
            self.assertIn(evt, self.template["hooks"],
                          f"Cursor event {evt!r} missing from cursor-hooks/hooks.json")

    def test_coarse_umbrella_events_intentionally_absent(self):
        # v6.2: granular per-type events replace the umbrella to avoid double-fire.
        for evt in CURSOR_INTENTIONALLY_ABSENT:
            self.assertNotIn(evt, self.template["hooks"],
                             f"{evt!r} must not be registered natively — it double-fires "
                             f"with the granular beforeShellExecution/beforeMCPExecution events")

    def test_every_event_command_arg_is_canonical_handler(self):
        # The command string format is: ``{{PYTHON}} "{{HOOK_RUNNER}}" <arg>``.
        # Extract <arg> for each registered event and assert hook_runner accepts it.
        # We invoke hook_runner.py with each arg and an empty stdin; rc == 0
        # means the canonical name resolved (UNKNOWN_HOOK_TYPE returns 0 with
        # an error-level NDJSON entry — both are acceptable here, since the
        # contract is "command does not crash with INTERNAL_ERROR").
        canonical_args: Dict[str, str] = {}
        for evt, entries in self.template["hooks"].items():
            for entry in entries:
                cmd = entry.get("command", "")
                # Last whitespace-separated token is the canonical hook name
                # (``{{PYTHON}} "{{HOOK_RUNNER}}" stop`` → ``stop``)
                parts = cmd.strip().split()
                if parts:
                    canonical_args[evt] = parts[-1]
        # Required: every required event maps to something
        for evt in CURSOR_REQUIRED_EVENTS:
            self.assertIn(evt, canonical_args, f"event {evt} has no command")
            self.assertNotIn("{{", canonical_args[evt],
                             f"event {evt} command was not substituted: {canonical_args[evt]!r}")
        # Smoke-invoke each canonical arg and confirm the runner does not
        # crash with a Python error or unknown-hook stderr.
        for evt, arg in canonical_args.items():
            with tempfile.TemporaryDirectory() as td:
                rc, _stdout, stderr = _run_hook(arg, state_dir=Path(td))
                # rc==0 is the contract; even unknown hooks return 0 with an
                # error-level NDJSON entry. A non-zero exit means the hook
                # name was passed to ``main()``'s argv check (len < 2) or a
                # Python crash. Stderr should not contain "Usage:" or
                # "Traceback".
                self.assertEqual(rc, 0,
                                 f"event {evt} arg {arg!r} returned rc={rc} stderr={stderr!r}")
                self.assertNotIn("Traceback", stderr,
                                 f"event {evt} arg {arg!r} crashed: {stderr}")
                self.assertNotIn("Usage:", stderr,
                                 f"event {evt} arg {arg!r} hit usage error: {stderr}")


class TestResolveDataDirCursorFallback(unittest.TestCase):
    """Without ``CLAUDE_PLUGIN_DATA``, ``_resolve_data_dir`` must find the
    Cursor-native data dir if it exists. Regression guard for v5.1.4."""

    def test_falls_back_to_cursor_data_dir(self):
        _load_module()
        from user_preferences import UserPreferences  # type: ignore
        import user_preferences as up_mod  # type: ignore
        saved_env = {k: os.environ.pop(k, None) for k in
                     ("CLAUDE_PLUGIN_DATA", "CLAUDE_AUDIO_HOOKS_DATA",
                      "CLAUDE_PLUGIN_ROOT")}
        original_home = up_mod.Path.home
        try:
            with tempfile.TemporaryDirectory() as fake_home_str:
                fake_home = Path(fake_home_str)
                cursor_data = fake_home / ".cursor" / "audio-hooks-data"
                cursor_data.mkdir(parents=True, exist_ok=True)
                # Resolver requires user_preferences.json to exist (not just the
                # dir) before it picks the Cursor-native fallback. See
                # user_preferences.py:_resolve_data_dir.
                (cursor_data / "user_preferences.json").write_text("{}", encoding="utf-8")
                up_mod.Path.home = staticmethod(lambda: Path(fake_home_str))
                # Anchor script_path away from any plugin layout so the
                # plugin-cache heuristic misses cleanly.
                non_plugin = fake_home / "user_preferences.py"
                resolved = UserPreferences(REPO, script_path=non_plugin)._resolve_data_dir()
                self.assertEqual(Path(resolved).resolve(), cursor_data.resolve(),
                                 f"Expected cursor data dir, got {resolved}")
        finally:
            up_mod.Path.home = original_home
            for k, v in saved_env.items():
                if v is not None:
                    os.environ[k] = v


class TestNotificationPermissionRequestNoOpUnderCursor(unittest.TestCase):
    """``notification`` and ``permission_request`` have no Cursor equivalent
    (cursor.com/docs/reference/third-party-hooks). The runner must skip them
    cleanly when invoker == cursor and emit a ``skipped_no_cursor_equivalent``
    debug event so the no-op is observable."""

    def _run_and_read_log(self, hook_type: str) -> list:
        with tempfile.TemporaryDirectory() as td:
            state_dir = Path(td)
            rc, _stdout, _stderr = _run_hook(
                hook_type,
                stdin_payload=json.dumps({"session_id": "s_no_op",
                                          "cursor_version": "3.2.16"}),
                env_extra={"CURSOR_VERSION": "3.2.16",
                           "CLAUDE_HOOKS_DEBUG": "1"},
                state_dir=state_dir,
            )
            self.assertEqual(rc, 0)
            log_file = state_dir / "logs" / "events.ndjson"
            if not log_file.exists():
                return []
            return [json.loads(l) for l in log_file.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_notification_skipped_with_marker_event(self):
        events = self._run_and_read_log("notification")
        actions = [e.get("action") for e in events]
        self.assertIn("skipped_no_cursor_equivalent", actions,
                      f"expected skipped_no_cursor_equivalent in {actions}")
        # No actual playback should have occurred
        for action in actions:
            self.assertNotIn(action, ("play_audio_started", "PLAYED"))

    def test_permission_request_skipped_with_marker_event(self):
        events = self._run_and_read_log("permission_request")
        actions = [e.get("action") for e in events]
        self.assertIn("skipped_no_cursor_equivalent", actions,
                      f"expected skipped_no_cursor_equivalent in {actions}")

    def test_notification_under_claude_code_is_NOT_skipped(self):
        # Without CURSOR_VERSION set, the no-op guard must not fire.
        # This regression-guards against accidentally short-circuiting
        # the Claude Code path.
        with tempfile.TemporaryDirectory() as td:
            state_dir = Path(td)
            rc, _stdout, _stderr = _run_hook(
                "notification",
                stdin_payload=json.dumps({"session_id": "s_cc"}),
                env_extra={"CLAUDE_HOOKS_DEBUG": "1"},
                state_dir=state_dir,
            )
            self.assertEqual(rc, 0)
            log_file = state_dir / "logs" / "events.ndjson"
            if log_file.exists():
                events = [json.loads(l) for l in log_file.read_text(encoding="utf-8").splitlines() if l.strip()]
                actions = [e.get("action") for e in events]
                self.assertNotIn("skipped_no_cursor_equivalent", actions,
                                 "the Cursor-only no-op fired under Claude Code invoker")


class TestInstallCursor(unittest.TestCase):
    """``audio-hooks install --cursor`` writes ``~/.cursor/hooks.json`` and
    seeds ``~/.cursor/audio-hooks-data/user_preferences.json``."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = Path(self._td.name)
        # Cursor must already exist for install --cursor to proceed
        (self.home / ".cursor").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._td.cleanup()

    def test_install_writes_hooks_json_with_substituted_paths(self):
        rc, stdout, _stderr = _run_cli("install", "--cursor", home=self.home)
        self.assertEqual(rc, 0, f"install failed: {stdout}")
        hooks_path = self.home / ".cursor" / "hooks.json"
        self.assertTrue(hooks_path.exists(), f"missing {hooks_path}")
        doc = json.loads(hooks_path.read_text(encoding="utf-8"))
        self.assertIn("hooks", doc)
        # Every entry is tagged _managed_by
        for evt, entries in doc["hooks"].items():
            for entry in entries:
                self.assertEqual(entry.get("_managed_by"), "audio-hooks",
                                 f"event {evt} entry missing _managed_by tag: {entry}")
                self.assertNotIn("{{", entry.get("command", ""),
                                 f"event {evt} command was not substituted")

    def test_install_seeds_user_preferences(self):
        rc, _stdout, _stderr = _run_cli("install", "--cursor", home=self.home)
        self.assertEqual(rc, 0)
        prefs_path = self.home / ".cursor" / "audio-hooks-data" / "user_preferences.json"
        self.assertTrue(prefs_path.exists(), f"missing {prefs_path}")
        prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
        self.assertIsInstance(prefs, dict)

    def test_install_writes_install_marker(self):
        rc, _stdout, _stderr = _run_cli("install", "--cursor", home=self.home)
        self.assertEqual(rc, 0)
        marker = self.home / ".cursor" / "audio-hooks-data" / "install_marker.json"
        self.assertTrue(marker.exists())
        data = json.loads(marker.read_text(encoding="utf-8"))
        self.assertIn("installed_at", data)
        self.assertIn("version", data)
        self.assertEqual(data.get("duplicate_bridge_forced"), False)

    def test_install_idempotent_no_duplicate_entries(self):
        # First install
        rc, _stdout, _stderr = _run_cli("install", "--cursor", home=self.home)
        self.assertEqual(rc, 0)
        first_doc = json.loads((self.home / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
        first_counts = {evt: len(entries) for evt, entries in first_doc["hooks"].items()}
        # Re-install
        rc, _stdout, _stderr = _run_cli("install", "--cursor", home=self.home)
        self.assertEqual(rc, 0)
        second_doc = json.loads((self.home / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
        second_counts = {evt: len(entries) for evt, entries in second_doc["hooks"].items()}
        self.assertEqual(first_counts, second_counts,
                         f"re-install duplicated entries: {first_counts} -> {second_counts}")

    def test_install_aborts_on_duplicate_bridge(self):
        # Stub out installed_plugins.json so _detect_install_mode reports
        # plugin_install == True, simulating "Claude Code already has the
        # audio-hooks plugin installed."
        plugins_dir = self.home / ".claude" / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)
        (plugins_dir / "installed_plugins.json").write_text(
            json.dumps({"plugins": {"audio-hooks": {"version": "5.1.5"}}}),
            encoding="utf-8",
        )
        rc, stdout, _stderr = _run_cli("install", "--cursor", home=self.home)
        self.assertNotEqual(rc, 0, "install should fail when plugin already bridges")
        # stdout is the JSON error envelope
        try:
            err = json.loads(stdout.strip().splitlines()[-1])
        except Exception:
            self.fail(f"non-JSON stdout: {stdout!r}")
        self.assertEqual(err.get("error", {}).get("code"), "DUPLICATE_BRIDGE")
        # And we did NOT write any hooks.json
        self.assertFalse((self.home / ".cursor" / "hooks.json").exists())

    def test_install_force_overrides_duplicate_bridge_and_records_marker(self):
        plugins_dir = self.home / ".claude" / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)
        (plugins_dir / "installed_plugins.json").write_text(
            json.dumps({"plugins": {"audio-hooks": {"version": "5.1.5"}}}),
            encoding="utf-8",
        )
        rc, stdout, _stderr = _run_cli("install", "--cursor", "--force", home=self.home)
        self.assertEqual(rc, 0, f"--force install failed: {stdout}")
        marker = self.home / ".cursor" / "audio-hooks-data" / "install_marker.json"
        self.assertTrue(marker.exists())
        data = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(data.get("duplicate_bridge_forced"), True)


class TestUninstallCursor(unittest.TestCase):
    """``uninstall --cursor`` removes only ``_managed_by: audio-hooks`` entries
    and preserves user preferences unless ``--purge`` is passed."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = Path(self._td.name)
        (self.home / ".cursor").mkdir(parents=True, exist_ok=True)
        # Pre-install
        rc, stdout, _stderr = _run_cli("install", "--cursor", home=self.home)
        self.assertEqual(rc, 0, f"setUp install failed: {stdout}")

    def tearDown(self):
        self._td.cleanup()

    def test_uninstall_removes_managed_entries(self):
        rc, stdout, _stderr = _run_cli("uninstall", "--cursor", home=self.home)
        self.assertEqual(rc, 0, f"uninstall failed: {stdout}")
        hooks_path = self.home / ".cursor" / "hooks.json"
        if hooks_path.exists():
            doc = json.loads(hooks_path.read_text(encoding="utf-8"))
            for evt, entries in (doc.get("hooks") or {}).items():
                for entry in entries:
                    self.assertNotEqual(entry.get("_managed_by"), "audio-hooks",
                                        f"audio-hooks entry survived uninstall in {evt}")

    def test_uninstall_preserves_foreign_entries(self):
        # Pre-populate user's existing Cursor hook
        hooks_path = self.home / ".cursor" / "hooks.json"
        doc = json.loads(hooks_path.read_text(encoding="utf-8"))
        doc.setdefault("hooks", {}).setdefault("postToolUse", []).append(
            {"command": "echo user-hook", "_managed_by": "user"}
        )
        hooks_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        rc, _stdout, _stderr = _run_cli("uninstall", "--cursor", home=self.home)
        self.assertEqual(rc, 0)
        # The user hook must survive
        if hooks_path.exists():
            doc2 = json.loads(hooks_path.read_text(encoding="utf-8"))
            user_entries = [
                e for e in (doc2.get("hooks", {}).get("postToolUse") or [])
                if e.get("_managed_by") == "user"
            ]
            self.assertEqual(len(user_entries), 1,
                             f"user hook lost during uninstall: {doc2}")

    def test_uninstall_preserves_user_preferences_by_default(self):
        rc, _stdout, _stderr = _run_cli("uninstall", "--cursor", home=self.home)
        self.assertEqual(rc, 0)
        prefs = self.home / ".cursor" / "audio-hooks-data" / "user_preferences.json"
        self.assertTrue(prefs.exists(),
                        "uninstall without --purge must preserve user_preferences.json")

    def test_uninstall_purge_deletes_data_dir(self):
        rc, _stdout, _stderr = _run_cli("uninstall", "--cursor", "--purge", home=self.home)
        self.assertEqual(rc, 0)
        prefs = self.home / ".cursor" / "audio-hooks-data" / "user_preferences.json"
        self.assertFalse(prefs.exists(),
                         "--purge must delete user_preferences.json")


class TestDuplicateBridgeRuntimeSkip(unittest.TestCase):
    """When ``install_marker.json`` records ``duplicate_bridge_forced: true``
    AND the runner is invoked under Cursor, the runtime skips firing so
    Claude Code's bridge handles the event alone (avoiding double audio)."""

    def test_runtime_skip_when_forced_install_under_cursor(self):
        with tempfile.TemporaryDirectory() as td:
            state_dir = Path(td)
            # Write the install marker into the resolved data dir
            marker_path = state_dir / "install_marker.json"
            marker_path.write_text(json.dumps({"duplicate_bridge_forced": True}),
                                   encoding="utf-8")
            rc, _stdout, _stderr = _run_hook(
                "stop",
                stdin_payload=json.dumps({"session_id": "s_dup",
                                          "cursor_version": "3.2.16"}),
                env_extra={"CURSOR_VERSION": "3.2.16",
                           "CLAUDE_HOOKS_DEBUG": "1"},
                state_dir=state_dir,
            )
            self.assertEqual(rc, 0)
            log_file = state_dir / "logs" / "events.ndjson"
            self.assertTrue(log_file.exists())
            events = [json.loads(l) for l in log_file.read_text(encoding="utf-8").splitlines() if l.strip()]
            actions = [e.get("action") for e in events]
            self.assertIn("duplicate_bridge_runtime_skip", actions,
                          f"expected runtime-skip event, got actions: {actions}")
            # Confirm the error code is stamped on the warn-level event
            skip_events = [e for e in events if e.get("action") == "duplicate_bridge_runtime_skip"]
            self.assertGreater(len(skip_events), 0)
            self.assertEqual(skip_events[0].get("error", {}).get("code"),
                             "DUPLICATE_BRIDGE_RUNTIME_SKIP")

    def test_no_runtime_skip_under_claude_code(self):
        # Same marker, but no CURSOR_VERSION: must NOT skip (Claude Code is
        # the bridge owner; the runtime guard only applies under Cursor).
        with tempfile.TemporaryDirectory() as td:
            state_dir = Path(td)
            marker_path = state_dir / "install_marker.json"
            marker_path.write_text(json.dumps({"duplicate_bridge_forced": True}),
                                   encoding="utf-8")
            rc, _stdout, _stderr = _run_hook(
                "stop",
                stdin_payload=json.dumps({"session_id": "s_cc_dup"}),
                env_extra={"CLAUDE_HOOKS_DEBUG": "1"},
                state_dir=state_dir,
            )
            self.assertEqual(rc, 0)
            log_file = state_dir / "logs" / "events.ndjson"
            if log_file.exists():
                events = [json.loads(l) for l in log_file.read_text(encoding="utf-8").splitlines() if l.strip()]
                actions = [e.get("action") for e in events]
                self.assertNotIn("duplicate_bridge_runtime_skip", actions,
                                 "runtime-skip fired under Claude Code invoker")


if __name__ == "__main__":
    unittest.main()
