"""Tests for v5.1.4 Cursor IDE bridge compatibility.

Cursor IDE 3.2.16+ auto-bridges Claude Code plugin hooks (see
https://cursor.com/docs/reference/third-party-hooks). When Cursor invokes
our ``runner/run.py``, it does NOT inject ``CLAUDE_PLUGIN_DATA`` — instead
it sets ``CURSOR_VERSION`` and a Cursor-specific stdin schema. This file
pins the contract that:

  1. ``_resolve_data_dir`` falls back through the documented priority chain
     (env vars → shared Claude Code dir → Cursor-native dir → legacy temp).
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
    """
    # Drop any prior copy from sys.modules so importlib re-executes it.
    sys.modules.pop("hook_runner", None)
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
    """``_resolve_data_dir`` priority chain.

    Priority: CLAUDE_PLUGIN_DATA → CLAUDE_AUDIO_HOOKS_DATA → shared
    (~/.claude/plugins/data/audio-hooks-chanmeng-audio-hooks) →
    cursor-native (~/.cursor/audio-hooks-data) → legacy temp dir.
    """

    def setUp(self):
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

    def test_claude_plugin_data_wins(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["CLAUDE_PLUGIN_DATA"] = td
            mod = _load_module()
            self.assertEqual(str(mod._resolve_data_dir()), td)

    def test_audio_hooks_data_when_no_plugin_data(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["CLAUDE_AUDIO_HOOKS_DATA"] = td
            mod = _load_module()
            self.assertEqual(str(mod._resolve_data_dir()), td)

    def test_falls_through_to_temp_when_nothing_else_present(self):
        # No env vars; we patch Path.home to a clean temp dir so the shared
        # and cursor-native checks both miss. The result must end with the
        # legacy ``claude_audio_hooks_queue`` directory name so existing
        # script-install state stays compatible.
        with tempfile.TemporaryDirectory() as fake_home:
            mod = _load_module()
            original_home = mod.Path.home
            try:
                mod.Path.home = staticmethod(lambda: mod.Path(fake_home))
                resolved = mod._resolve_data_dir()
                self.assertTrue(
                    str(resolved).endswith("claude_audio_hooks_queue"),
                    f"Expected legacy temp fallback, got {resolved}",
                )
            finally:
                mod.Path.home = original_home


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


if __name__ == "__main__":
    unittest.main()
