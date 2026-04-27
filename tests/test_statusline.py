"""Unit and integration tests for ``bin/audio-hooks-statusline.py``.

Run with::

    python -m unittest discover tests

These tests are stdlib-only (unittest, subprocess, tempfile) so they run on the
same matrix the smoke workflow exercises (Ubuntu / Windows / macOS × Python
3.9 / 3.12 / 3.13) without any new dependencies.

Purpose: every other user of this open-source project relies on the status line
script not crashing, regardless of what version of Claude Code pipes JSON to
it. These tests pin the contract.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "audio-hooks-statusline.py"


def _load_module():
    """Import audio-hooks-statusline.py as a module so we can unit-test
    its helper functions directly. The hyphen in the filename means we have
    to use the importlib spec API rather than ``import``."""
    spec = importlib.util.spec_from_file_location("audio_hooks_statusline", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(
    stdin_payload: str,
    *,
    state_dir: Optional[Path] = None,
    env_extra: Optional[Dict[str, str]] = None,
) -> Tuple[int, str, str]:
    """Invoke the status line script via subprocess with isolated state dir.

    Pinning ``CLAUDE_AUDIO_HOOKS_DATA`` to a temp dir prevents tests from
    polluting (or being polluted by) the user's real audio-hooks state.
    """
    env = os.environ.copy()
    # Always unset CLAUDE_HOOKS_DEBUG by default so individual tests start
    # from a known state. Tests that want it on must opt in via env_extra.
    env.pop("CLAUDE_HOOKS_DEBUG", None)
    env.pop("CLAUDE_PLUGIN_DATA", None)
    if state_dir is not None:
        env["CLAUDE_AUDIO_HOOKS_DATA"] = str(state_dir)
    # Pin UTF-8 so the script's Unicode output (box chars, emoji, ANSI) is
    # captured cleanly on Windows runners where the default codepage is
    # cp1252. The script self-defends via _force_utf8_stdout() but pinning
    # PYTHONIOENCODING in tests gives a deterministic baseline that doesn't
    # depend on Python version's reconfigure() availability.
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=stdin_payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ---------------------------------------------------------------------------
# Unit tests for pure helpers
# ---------------------------------------------------------------------------


class TestFmtTokens(unittest.TestCase):
    """``_fmt_tokens`` must produce stable display strings for every numeric
    value Claude Code might plausibly send (and a few it won't)."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_zero(self):
        self.assertEqual(self.mod._fmt_tokens(0), "0")

    def test_under_one_k(self):
        self.assertEqual(self.mod._fmt_tokens(1), "1")
        self.assertEqual(self.mod._fmt_tokens(999), "999")

    def test_exact_one_k(self):
        self.assertEqual(self.mod._fmt_tokens(1000), "1K")

    def test_typical_session(self):
        # The user's empirical case: 166K tokens, Sonnet 200K window
        self.assertEqual(self.mod._fmt_tokens(166000), "166K")
        self.assertEqual(self.mod._fmt_tokens(170000), "170K")

    def test_exact_one_m(self):
        self.assertEqual(self.mod._fmt_tokens(1_000_000), "1M")

    def test_exact_two_m(self):
        self.assertEqual(self.mod._fmt_tokens(2_000_000), "2M")

    def test_fractional_m(self):
        self.assertEqual(self.mod._fmt_tokens(1_500_000), "1.5M")
        self.assertEqual(self.mod._fmt_tokens(1_234_567), "1.2M")


# ---------------------------------------------------------------------------
# Integration tests for status line rendering
# ---------------------------------------------------------------------------


class _StatuslineRenderBase(unittest.TestCase):
    """Pre-populates the statusline cache file so ``_get_status()`` returns
    immediately without spawning a nested ``audio-hooks status`` subprocess.

    Why: the renderer tests assert specific stdout content. The renderer's
    Line 1 short-circuits to "Audio Hooks (status unavailable)" when
    ``_get_status()`` returns empty, which suppresses the Context segment.
    On Windows GitHub Actions runners the nested subprocess chain
    (test → statusline → audio-hooks status, all via Python) is flaky in a
    way that doesn't reproduce locally and doesn't affect production (the
    existing ``audio-hooks version / status / diagnose`` CI step on Windows
    passes — the renderer's own subprocess call is what's flaky).

    Pinning a cache file makes these tests cover the renderer in isolation.
    The status backend has its own CI coverage.
    """

    _MINIMAL_STATUS = {
        "version": "test",
        "enabled_hook_count": 0,
        "total_hook_count": 26,
        "theme": "default",
        "webhook": {"enabled": False, "format": "raw"},
        "snooze": {"active": False},
        "focus_flow": {"enabled": False, "mode": None},
        "statusline": {"visible_segments": []},
    }

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="audio_hooks_tests_"))
        for sid in ("t", "default"):
            (self.tmp / f"statusline.cache.{sid}").write_text(
                json.dumps(self._MINIMAL_STATUS), encoding="utf-8"
            )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestStatuslineRobustness(_StatuslineRenderBase):
    """Every input shape Claude Code (current or future) might send must
    exit cleanly. Crashing the status line script breaks the user's terminal
    prompt — non-negotiable."""

    def test_empty_stdin(self):
        rc, _, _ = _run("", state_dir=self.tmp)
        self.assertEqual(rc, 0)

    def test_empty_object(self):
        rc, _, _ = _run("{}", state_dir=self.tmp)
        self.assertEqual(rc, 0)

    def test_malformed_json(self):
        rc, _, _ = _run("{not json", state_dir=self.tmp)
        self.assertEqual(rc, 0)

    def test_null_context_window(self):
        payload = {"session_id": "t", "context_window": None}
        rc, _, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)

    def test_string_used_percentage(self):
        payload = {"session_id": "t", "context_window": {"used_percentage": "abc"}}
        rc, _, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)

    def test_string_window_size(self):
        # Future Claude Code versions might send window size as a string;
        # we should ignore it and fall back rather than crash.
        payload = {
            "session_id": "t",
            "context_window": {"used_percentage": 50, "context_window_size": "200000"},
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn("Context: 50%", out)
        # No (X/Y) when the type is wrong
        self.assertNotIn("/200K", out)

    def test_zero_window_size(self):
        payload = {
            "session_id": "t",
            "context_window": {"used_percentage": 50, "context_window_size": 0},
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertNotIn("(", out.split("Context")[-1].split("\n")[0])


class TestContextSegment(_StatuslineRenderBase):
    """The (X/Y) display: numerator must always be derived consistently from
    used_percentage × context_window_size, never from a misleading separate
    field. This was the regression that shipped briefly in v5.1.3-rc."""

    def test_only_percentage_falls_back_to_plain(self):
        payload = {"session_id": "t", "context_window": {"used_percentage": 42}}
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn("Context: 42%", out)
        # Specifically: no "(...)" inside the Context segment.
        ctx_line = [l for l in out.splitlines() if "Context:" in l][0]
        self.assertNotIn("(", ctx_line.split("Context:")[1])

    def test_sonnet_post_switch(self):
        # User's empirical case: 83% of 200K should display 166K.
        payload = {
            "session_id": "t",
            "context_window": {"used_percentage": 83, "context_window_size": 200000},
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn("Context: 83% (166K/200K)", out)

    def test_opus_pre_switch(self):
        payload = {
            "session_id": "t",
            "context_window": {"used_percentage": 17, "context_window_size": 1000000},
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn("Context: 17% (170K/1M)", out)

    def test_red_threshold_emits_compact_hint(self):
        payload = {
            "session_id": "t",
            "context_window": {"used_percentage": 90, "context_window_size": 200000},
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn("/compact", out)

    def test_total_input_tokens_is_ignored(self):
        # Regression guard: the v5.1.3-rc shipped briefly using
        # ctx_window['total_input_tokens'] as the numerator, which understates
        # cache-heavy sessions by orders of magnitude (real bug surfaced by
        # GitHub issue #16). The numerator must be derived from the
        # percentage so the displayed math is always self-consistent.
        payload = {
            "session_id": "t",
            "context_window": {
                "used_percentage": 83,
                "context_window_size": 200000,
                "total_input_tokens": 6000,  # misleadingly small
            },
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn("(166K/200K)", out)
        self.assertNotIn("(6K/", out)


class TestDebugDump(_StatuslineRenderBase):
    """``CLAUDE_HOOKS_DEBUG`` must be strictly opt-in and mirror the
    truthy-value parsing used by hook_runner."""

    def _dump_path(self):
        return self.tmp / "statusline.last_input.json"

    def test_unset_creates_no_dump(self):
        payload = {"session_id": "t"}
        rc, _, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertFalse(self._dump_path().exists())

    def test_one_creates_dump(self):
        payload = {"session_id": "t", "marker": "yes"}
        rc, _, _ = _run(
            json.dumps(payload),
            state_dir=self.tmp,
            env_extra={"CLAUDE_HOOKS_DEBUG": "1"},
        )
        self.assertEqual(rc, 0)
        self.assertTrue(self._dump_path().exists())
        data = json.loads(self._dump_path().read_text(encoding="utf-8"))
        self.assertEqual(data.get("marker"), "yes")

    def test_true_creates_dump(self):
        rc, _, _ = _run(
            "{}", state_dir=self.tmp, env_extra={"CLAUDE_HOOKS_DEBUG": "TRUE"}
        )
        self.assertEqual(rc, 0)
        self.assertTrue(self._dump_path().exists())

    def test_yes_creates_dump(self):
        rc, _, _ = _run(
            "{}", state_dir=self.tmp, env_extra={"CLAUDE_HOOKS_DEBUG": "yes"}
        )
        self.assertEqual(rc, 0)
        self.assertTrue(self._dump_path().exists())

    def test_unrecognised_value_does_nothing(self):
        for v in ("0", "false", "no", "off", "anything-else"):
            with self.subTest(value=v):
                if self._dump_path().exists():
                    self._dump_path().unlink()
                rc, _, _ = _run(
                    "{}", state_dir=self.tmp, env_extra={"CLAUDE_HOOKS_DEBUG": v}
                )
                self.assertEqual(rc, 0)
                self.assertFalse(self._dump_path().exists())

    def test_no_tmp_left_behind(self):
        # The atomic-rename pattern must not leave .tmp files in the state dir
        # under the happy path.
        rc, _, _ = _run(
            "{}", state_dir=self.tmp, env_extra={"CLAUDE_HOOKS_DEBUG": "1"}
        )
        self.assertEqual(rc, 0)
        leftovers = [
            p for p in self.tmp.iterdir() if p.name.endswith(".tmp")
        ]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
