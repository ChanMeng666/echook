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

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import importlib.util
import json
import re
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
        # Pin UTF-8 for both writing stdin and decoding stdout/stderr.
        # On Windows runners the default is the system codepage (cp1252)
        # which cannot decode the box-drawing chars and emoji the renderer
        # emits — leaving subprocess.run to raise UnicodeDecodeError before
        # our test even gets to assert anything. errors="replace" is
        # belt-and-braces in case the script ever emits a sequence outside
        # UTF-8 (it shouldn't, but tests must never crash silently).
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=15,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


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


class TestAbbrevPath(unittest.TestCase):
    """``_abbrev_path`` shortens the cwd for the status line: collapse home to
    ``~`` and trim long paths to ``<root>…<last folder>``. It must never raise
    on surprising input — the renderer wraps it but the helper is the contract."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_short_path_unchanged(self):
        self.assertEqual(self.mod._abbrev_path("D:\\proj"), "D:\\proj")
        self.assertEqual(self.mod._abbrev_path("/srv/app"), "/srv/app")

    def test_home_collapses_to_tilde(self):
        home = os.path.expanduser("~")
        # A short path under home stays whole but with ~ prefix.
        self.assertEqual(self.mod._abbrev_path(os.path.join(home, "x")),
                         "~" + os.sep + "x")

    def test_long_windows_path_keeps_drive_and_last(self):
        out = self.mod._abbrev_path(
            "D:\\github_repository\\some\\deeply\\nested\\claude-code-audio-hooks"
        )
        self.assertEqual(out, "D:\\…\\claude-code-audio-hooks")

    def test_long_posix_path_keeps_root_and_last(self):
        out = self.mod._abbrev_path(
            "/home/someuser/work/repositories/very/deep/echook-project-folder"
        )
        # First non-empty segment is "home"; last is the project folder.
        self.assertEqual(out, "home/…/echook-project-folder")

    def test_extremely_long_last_segment_falls_back(self):
        # When even "<root>…<tail>" exceeds max_len, drop the root.
        tail = "a" * 60
        out = self.mod._abbrev_path("/root/middle/" + tail)
        self.assertEqual(out, "…/" + tail)

    def test_empty_and_bad_input_never_raise(self):
        self.assertEqual(self.mod._abbrev_path(""), "")
        # Non-string input degrades to the original object without raising.
        self.assertIsNone(self.mod._abbrev_path(None))


# ---------------------------------------------------------------------------
# Integration tests for status line rendering
# ---------------------------------------------------------------------------


class _StatuslineRenderBase(unittest.TestCase):
    """Pre-populates the statusline cache file so ``_get_status()`` returns
    immediately without spawning a nested ``audio-hooks status`` subprocess.

    Why: the renderer tests assert specific stdout content. The renderer's
    Line 1 short-circuits to "echook (status unavailable)" when
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


class TestCwdSegment(_StatuslineRenderBase):
    """The ``cwd`` segment shows the current working directory on Line 1 so the
    user can tell which project a session belongs to. Shown by default (it is
    in ALL_SEGMENTS), hidden when ``visible_segments`` excludes it, and silently
    absent when Claude Code sends no path."""

    FOLDER = "📁"

    def _set_visible(self, segments):
        status = dict(self._MINIMAL_STATUS)
        status["statusline"] = {"visible_segments": segments}
        for sid in ("t", "default"):
            (self.tmp / f"statusline.cache.{sid}").write_text(
                json.dumps(status), encoding="utf-8"
            )

    def test_cwd_shown_by_default(self):
        payload = {
            "session_id": "t",
            "cwd": "D:\\github_repository\\claude-code-audio-hooks",
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn(self.FOLDER, out)
        self.assertIn("claude-code-audio-hooks", out)

    def test_cwd_falls_back_to_workspace_current_dir(self):
        payload = {
            "session_id": "t",
            "workspace": {"current_dir": "/srv/myproject"},
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn(self.FOLDER, out)
        self.assertIn("myproject", out)

    def test_cwd_hidden_when_excluded(self):
        self._set_visible(["context"])
        payload = {
            "session_id": "t",
            "cwd": "D:\\github_repository\\claude-code-audio-hooks",
            "context_window": {"used_percentage": 40, "context_window_size": 200000},
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertNotIn(self.FOLDER, out)
        self.assertIn("Context: 40%", out)

    def test_cwd_absent_when_no_path(self):
        payload = {"session_id": "t"}
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertNotIn(self.FOLDER, out)


class TestResetClock(unittest.TestCase):
    """``_fmt_reset_clock`` turns a Unix epoch into a banner-style local clock
    time. It must never raise and must blank out on bad/absent input."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_absent_and_zero_blank(self):
        for v in (None, 0, -1, "", "abc", {}, True, False):
            with self.subTest(value=v):
                self.assertEqual(self.mod._fmt_reset_clock(v), "")

    def test_on_the_hour_strips_minutes(self):
        # 2021-01-01 21:00:00 UTC. We can't assume the runner's timezone, so
        # assert the shape rather than an exact hour: no ":00", ends am/pm.
        import time as _t
        epoch = _t.mktime(_t.struct_time((2021, 1, 1, 21, 0, 0, 0, 0, -1)))
        out = self.mod._fmt_reset_clock(epoch)
        self.assertRegex(out, r"^\d{1,2}(am|pm)$")

    def test_with_minutes_keeps_them(self):
        import time as _t
        epoch = _t.mktime(_t.struct_time((2021, 1, 1, 21, 30, 0, 0, 0, -1)))
        out = self.mod._fmt_reset_clock(epoch)
        self.assertRegex(out, r"^\d{1,2}:30(am|pm)$")

    def test_string_epoch_coerced(self):
        import time as _t
        epoch = _t.mktime(_t.struct_time((2021, 1, 1, 9, 0, 0, 0, 0, -1)))
        out = self.mod._fmt_reset_clock(str(int(epoch)))
        self.assertRegex(out, r"^\d{1,2}(am|pm)$")

    def test_with_date_same_day_is_time_only(self):
        import time as _t
        now = _t.mktime(_t.struct_time((2026, 6, 30, 12, 0, 0, 0, 0, -1)))
        reset = _t.mktime(_t.struct_time((2026, 6, 30, 17, 0, 0, 0, 0, -1)))
        # Reset later today → no date prefix even with_date=True.
        self.assertEqual(self.mod._fmt_reset_clock(reset, with_date=True, now=now), "5pm")

    def test_with_date_different_day_prepends_date(self):
        import time as _t
        now = _t.mktime(_t.struct_time((2026, 6, 30, 12, 0, 0, 0, 0, -1)))
        reset = _t.mktime(_t.struct_time((2026, 7, 5, 5, 0, 0, 0, 0, -1)))
        # Weekly reset days away → "Jul 5 5am".
        self.assertEqual(self.mod._fmt_reset_clock(reset, with_date=True, now=now), "Jul 5 5am")

    def test_with_date_keeps_minutes(self):
        import time as _t
        now = _t.mktime(_t.struct_time((2026, 6, 30, 12, 0, 0, 0, 0, -1)))
        reset = _t.mktime(_t.struct_time((2026, 7, 1, 3, 30, 0, 0, 0, -1)))
        self.assertEqual(self.mod._fmt_reset_clock(reset, with_date=True, now=now), "Jul 1 3:30am")

    def test_default_never_shows_date(self):
        # Back-compat: without with_date, a far-future reset is still time-only.
        import time as _t
        reset = _t.mktime(_t.struct_time((2026, 7, 5, 5, 0, 0, 0, 0, -1)))
        self.assertEqual(self.mod._fmt_reset_clock(reset), "5am")


class TestWeeklyQuotaSegment(_StatuslineRenderBase):
    """The ``weekly_quota`` segment mirrors the banner's "82% of your weekly
    limit · resets 9pm". Present only when the 7-day window is in the payload
    (Claude.ai subscribers); silently absent otherwise."""

    def test_present_with_reset(self):
        payload = {
            "session_id": "t",
            "rate_limits": {"seven_day": {"used_percentage": 82, "resets_at": 1609495200}},
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn("Weekly: 82%", out)
        self.assertIn("· resets", out)

    def test_present_without_reset(self):
        payload = {
            "session_id": "t",
            "rate_limits": {"seven_day": {"used_percentage": 50}},
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn("Weekly: 50%", out)
        weekly_seg = [l for l in out.splitlines() if "Weekly:" in l][0]
        self.assertNotIn("resets", weekly_seg.split("Weekly:")[1])

    def test_absent_when_no_rate_limits(self):
        payload = {"session_id": "t", "context_window": {"used_percentage": 30}}
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertNotIn("Weekly:", out)

    def test_filter_shows_only_weekly(self):
        # Pin a status cache that restricts visible_segments to weekly_quota.
        status = dict(self._MINIMAL_STATUS)
        status["statusline"] = {"visible_segments": ["weekly_quota"]}
        for sid in ("t", "default"):
            (self.tmp / f"statusline.cache.{sid}").write_text(
                json.dumps(status), encoding="utf-8"
            )
        payload = {
            "session_id": "t",
            "rate_limits": {"seven_day": {"used_percentage": 82}},
            "context_window": {"used_percentage": 30, "context_window_size": 200000},
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn("Weekly: 82%", out)
        self.assertNotIn("Context:", out)


class TestApiQuotaReset(_StatuslineRenderBase):
    """The existing 5-hour ``api_quota`` segment gains a reset clock for
    symmetry with the weekly segment."""

    def test_reset_appended(self):
        payload = {
            "session_id": "t",
            "rate_limits": {"five_hour": {"used_percentage": 40, "resets_at": 1609495200}},
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn("API Quota: 40%", out)
        self.assertIn("· resets", out)


class TestCcVersionSegment(_StatuslineRenderBase):
    """The ``cc_version`` segment shows Claude Code's own version from the
    stdin ``version`` field — distinct from echook's own version."""

    def test_present(self):
        payload = {"session_id": "t", "version": "2.1.193"}
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn("CC v2.1.193", out)

    def test_absent_when_no_version(self):
        payload = {"session_id": "t", "model": {"display_name": "Opus"}}
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertNotIn("CC v", out)


class TestEffortSegment(_StatuslineRenderBase):
    """The ``effort`` segment shows the reasoning effort level, present only on
    models that report it."""

    def test_present(self):
        payload = {"session_id": "t", "effort": {"level": "high"}}
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn("high", out.splitlines()[0])

    def test_absent_when_no_effort(self):
        payload = {"session_id": "t", "model": {"display_name": "Opus"}}
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        # The effort chip uses the brain emoji; it must not appear.
        self.assertNotIn("\U0001f9e0", out)


class TestCostSegment(_StatuslineRenderBase):
    """The ``cost`` segment shows session spend and the lines added/removed
    diff, mirroring the cost data the banner/`/cost` surface."""

    def test_present_with_diff(self):
        payload = {
            "session_id": "t",
            "cost": {"total_cost_usd": 0.42, "total_lines_added": 156, "total_lines_removed": 23},
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn("$0.42", out)
        self.assertIn("+156", out)
        self.assertIn("-23", out)

    def test_no_diff_when_zero_lines(self):
        payload = {
            "session_id": "t",
            "cost": {"total_cost_usd": 0.05, "total_lines_added": 0, "total_lines_removed": 0},
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn("$0.05", out)
        cost_seg = [l for l in out.splitlines() if "$0.05" in l][0]
        self.assertNotIn("+0", cost_seg)

    def test_absent_when_no_cost(self):
        payload = {"session_id": "t", "model": {"display_name": "Opus"}}
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertNotIn("$", out)


class TestBannerSegmentsRobustness(_StatuslineRenderBase):
    """The new banner segments must tolerate junk types without crashing —
    same contract as the rest of the renderer."""

    def test_junk_values_exit_clean(self):
        payload = {
            "session_id": "t",
            "version": 12345,                       # non-string version
            "effort": "high",                        # wrong shape (str not dict)
            "rate_limits": {
                "five_hour": {"used_percentage": "x", "resets_at": "y"},
                "seven_day": {"used_percentage": None, "resets_at": {}},
            },
            "cost": {"total_cost_usd": "free", "total_lines_added": "lots"},
        }
        rc, _, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)


class TestVwidth(unittest.TestCase):
    """``_vwidth`` measures the *visible* width of a rendered segment so the
    reflow can pack lines that actually fit the terminal."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_plain_ascii(self):
        self.assertEqual(self.mod._vwidth("hello"), 5)

    def test_ansi_is_zero_width(self):
        colored = "\033[36m[Opus]\033[0m"
        self.assertEqual(self.mod._vwidth(colored), len("[Opus]"))

    def test_emoji_counts_two(self):
        # Each of the emoji the renderer emits should measure 2 cells.
        for emoji in ("\U0001f9e0", "⚡", "\U0001f4c1", "\U0001f50a",
                      "\U0001f4b2", "\U0001f33f"):
            with self.subTest(emoji=emoji):
                self.assertEqual(self.mod._vwidth(emoji), 2)

    def test_variation_selector_is_zero_width(self):
        # ⚠ + FE0F renders as one glyph; the selector adds no width.
        self.assertEqual(self.mod._vwidth("⚠️"), self.mod._vwidth("⚠"))

    def test_box_drawing_counts_one(self):
        # The progress-bar glyphs must be one cell each or the bars mis-measure.
        self.assertEqual(self.mod._vwidth("████░░░░"), 8)


class TestPackLines(unittest.TestCase):
    """``_pack_lines`` greedily wraps segments at boundaries to fit a width."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_all_fit_one_line(self):
        out = self.mod._pack_lines(["aaa", "bbb", "ccc"], " | ", 80)
        self.assertEqual(out, ["aaa | bbb | ccc"])

    def test_wraps_at_boundary(self):
        # width 9 holds "aaa | bbb" (9) but not a third segment.
        out = self.mod._pack_lines(["aaa", "bbb", "ccc"], " | ", 9)
        self.assertEqual(out, ["aaa | bbb", "ccc"])

    def test_never_splits_a_segment(self):
        # A segment wider than the budget gets its own line, intact.
        out = self.mod._pack_lines(["short", "a-very-long-segment-here"], "  ", 10)
        self.assertEqual(out, ["short", "a-very-long-segment-here"])

    def test_no_packed_line_exceeds_width(self):
        parts = [f"seg{i}" for i in range(20)]
        for line in self.mod._pack_lines(parts, "  ", 20):
            # Lines with more than one segment must respect the budget.
            if "  " in line:
                self.assertLessEqual(self.mod._vwidth(line), 20)

    def test_empty_input(self):
        self.assertEqual(self.mod._pack_lines([], " | ", 80), [])


class TestTerminalWidth(unittest.TestCase):
    """``_terminal_width`` resolves the packing width: explicit override first,
    then the COLUMNS env var Claude Code provides, then a safe fallback."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_max_width_override_wins(self):
        status = {"statusline": {"max_width": 42}}
        self.assertEqual(self.mod._terminal_width(status), 42)

    def test_zero_override_falls_through(self):
        # max_width 0 means auto; with COLUMNS set we should read it.
        prev = os.environ.get("COLUMNS")
        os.environ["COLUMNS"] = "137"
        try:
            self.assertEqual(self.mod._terminal_width({"statusline": {"max_width": 0}}), 137)
        finally:
            if prev is None:
                os.environ.pop("COLUMNS", None)
            else:
                os.environ["COLUMNS"] = prev

    def test_empty_status_does_not_raise(self):
        # Must return a positive int regardless of input.
        self.assertGreater(self.mod._terminal_width({}), 0)
        self.assertGreater(self.mod._terminal_width(None), 0)


class TestReflow(_StatuslineRenderBase):
    """The full renderer must wrap a content-rich status line across multiple
    physical rows so no segment is ever truncated by Claude Code. This is the
    regression guard for the "Webho…" overflow bug."""

    _RICH_PAYLOAD = {
        "session_id": "t",
        "version": "2.1.193",
        "model": {"display_name": "Opus 4.8 (1M context)"},
        "effort": {"level": "high"},
        "cwd": "/home/user/projects/claude-code-audio-hooks",
        "rate_limits": {
            "five_hour": {"used_percentage": 62, "resets_at": 1609495200},
            "seven_day": {"used_percentage": 83, "resets_at": 1609495200},
        },
        "cost": {"total_cost_usd": 6.23, "total_lines_added": 466, "total_lines_removed": 28},
        "context_window": {"used_percentage": 13, "context_window_size": 1000000},
    }

    def _load_vwidth(self):
        return _load_module()._vwidth

    def test_no_line_exceeds_width(self):
        # At width 50 every individual segment fits, so every emitted row must
        # stay within the budget (width - 1) — nothing should overflow.
        rc, out, _ = _run(
            json.dumps(self._RICH_PAYLOAD),
            state_dir=self.tmp,
            env_extra={"COLUMNS": "50"},
        )
        self.assertEqual(rc, 0)
        vwidth = self._load_vwidth()
        lines = [l for l in out.splitlines() if l]
        self.assertGreater(len(lines), 2)  # it wrapped beyond two rows
        for line in lines:
            self.assertLessEqual(vwidth(line), 49, msg=f"overflow: {line!r}")

    def test_webhook_segment_intact(self):
        # The original bug truncated "Webhook: off" to "Webho…". Assert it
        # survives whole even on a width that forces wrapping.
        rc, out, _ = _run(
            json.dumps(self._RICH_PAYLOAD),
            state_dir=self.tmp,
            env_extra={"COLUMNS": "60"},
        )
        self.assertEqual(rc, 0)
        self.assertIn("Webhook: off", out)
        self.assertNotIn("Webho…", out)

    def test_max_width_override_forces_wrap(self):
        # Pin a narrow max_width via the status cache (no COLUMNS dependency).
        status = dict(self._MINIMAL_STATUS)
        status["statusline"] = {"visible_segments": [], "max_width": 30}
        for sid in ("t", "default"):
            (self.tmp / f"statusline.cache.{sid}").write_text(
                json.dumps(status), encoding="utf-8"
            )
        rc, out, _ = _run(json.dumps(self._RICH_PAYLOAD), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        vwidth = self._load_vwidth()
        for line in [l for l in out.splitlines() if l and ("  " in l or " | " in l)]:
            self.assertLessEqual(vwidth(line), 29, msg=f"overflow: {line!r}")


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


class TestNewSegments(_StatuslineRenderBase):
    """The v6.3 segment catalog: each new segment renders when its field is
    present and is silently absent otherwise (so the comprehensive default
    stays clean on a plain session)."""

    def test_session_name_agent_thinking_output_style(self):
        payload = {
            "session_id": "t",
            "session_name": "my-feature",
            "agent": {"name": "security-reviewer"},
            "thinking": {"enabled": True},
            "output_style": {"name": "Explanatory"},
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn("my-feature", out)
        self.assertIn("security-reviewer", out)
        self.assertIn("thinking", out)
        self.assertIn("Explanatory", out)

    def test_output_style_default_is_hidden(self):
        payload = {"session_id": "t", "output_style": {"name": "default"}}
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertNotIn("\U0001f3a8", out)  # palette emoji absent

    def test_repo_segment(self):
        payload = {
            "session_id": "t",
            "workspace": {"repo": {"owner": "ChanMeng666", "name": "echook"}},
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn("ChanMeng666/echook", out)

    def test_pr_and_added_dirs_and_worktree(self):
        payload = {
            "session_id": "t",
            "pr": {"number": 1234, "review_state": "pending"},
            "workspace": {"added_dirs": ["/a", "/b"]},
            "worktree": {"name": "wt-feature"},
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn("PR #1234 (pending)", out)
        self.assertIn("+2 dirs", out)
        self.assertIn("wt-feature", out)

    def test_duration_api_time_burn_rate(self):
        payload = {
            "session_id": "t",
            "cost": {
                "total_cost_usd": 6.0,
                "total_duration_ms": 720000,       # 12 minutes
                "total_api_duration_ms": 180000,   # 25% of wall
            },
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn("12m", out)
        self.assertIn("API 25%", out)
        self.assertIn("$30.00/h", out)  # $6 over 0.2h

    def test_burn_rate_absent_for_short_session(self):
        payload = {
            "session_id": "t",
            "cost": {"total_cost_usd": 6.0, "total_duration_ms": 5000},  # 5s
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertNotIn("/h", out)

    def test_tokens_cache_ratio(self):
        payload = {
            "session_id": "t",
            "context_window": {
                "used_percentage": 40,
                "current_usage": {
                    "input_tokens": 1000,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 9000,
                },
            },
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn("cache 90%", out)

    def test_exceeds_200k_flag(self):
        payload = {"session_id": "t", "exceeds_200k_tokens": True}
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn(">200K", out)

    def test_new_segments_absent_on_plain_session(self):
        payload = {"session_id": "t", "model": {"display_name": "Opus"}}
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        for token in ("PR #", "thinking", "/h", ">200K", "cache ", "dirs"):
            self.assertNotIn(token, out)


class TestHiddenSegments(_StatuslineRenderBase):
    """``hidden_segments`` is a blacklist applied when ``visible_segments`` is
    empty: show everything except the listed names."""

    def _set_hidden(self, hidden):
        status = dict(self._MINIMAL_STATUS)
        status["statusline"] = {"visible_segments": [], "hidden_segments": hidden}
        for sid in ("t", "default"):
            (self.tmp / f"statusline.cache.{sid}").write_text(
                json.dumps(status), encoding="utf-8"
            )

    def test_hidden_segment_dropped_others_kept(self):
        self._set_hidden(["cost"])
        payload = {
            "session_id": "t",
            "cost": {"total_cost_usd": 1.5},
            "context_window": {"used_percentage": 40, "context_window_size": 200000},
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertNotIn("$1.50", out)
        self.assertIn("Context: 40%", out)  # other segments still show

    def test_visible_segments_takes_precedence_over_hidden(self):
        # When visible_segments is non-empty it wins; hidden_segments ignored.
        status = dict(self._MINIMAL_STATUS)
        status["statusline"] = {
            "visible_segments": ["context"], "hidden_segments": ["context"]
        }
        for sid in ("t", "default"):
            (self.tmp / f"statusline.cache.{sid}").write_text(
                json.dumps(status), encoding="utf-8"
            )
        payload = {
            "session_id": "t",
            "context_window": {"used_percentage": 40, "context_window_size": 200000},
        }
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn("Context: 40%", out)


class TestGitDirty(unittest.TestCase):
    """``_git_dirty`` shells out once and caches; non-repos cache -1 (=> None)
    so they don't re-shell. The cache file is the deterministic test hook."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gitdirty_"))
        # Restore, don't pop: the suite-level isolation relies on this variable.
        self._saved_data = os.environ.get("CLAUDE_AUDIO_HOOKS_DATA")
        os.environ["CLAUDE_AUDIO_HOOKS_DATA"] = str(self.tmp)

    def tearDown(self):
        if self._saved_data is None:
            os.environ.pop("CLAUDE_AUDIO_HOOKS_DATA", None)
        else:
            os.environ["CLAUDE_AUDIO_HOOKS_DATA"] = self._saved_data
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_none_for_missing_cwd(self):
        self.assertIsNone(self.mod._git_dirty(None))
        self.assertIsNone(self.mod._git_dirty(""))

    def test_cached_value_is_read(self):
        import hashlib
        cwd = "/some/project"
        key = hashlib.md5(os.fsencode(cwd)).hexdigest()[:12]
        (self.tmp / f"statusline.git.{key}").write_text("7", encoding="utf-8")
        self.assertEqual(self.mod._git_dirty(cwd), 7)

    def test_cached_negative_means_not_a_repo(self):
        import hashlib
        cwd = "/not/a/repo"
        key = hashlib.md5(os.fsencode(cwd)).hexdigest()[:12]
        (self.tmp / f"statusline.git.{key}").write_text("-1", encoding="utf-8")
        self.assertIsNone(self.mod._git_dirty(cwd))


class TestDurationHelper(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_formats(self):
        self.assertEqual(self.mod._format_duration_ms(5000), "5s")
        self.assertEqual(self.mod._format_duration_ms(120000), "2m")
        self.assertEqual(self.mod._format_duration_ms(3600000), "1h")
        self.assertEqual(self.mod._format_duration_ms(5400000), "1h30m")

    def test_bad_input_blank(self):
        for v in (None, "x", {}, -1000):
            with self.subTest(value=v):
                self.assertEqual(self.mod._format_duration_ms(v), "")


if __name__ == "__main__":
    unittest.main()


class TestSegmentCatalogLockStep(unittest.TestCase):
    """`STATUSLINE_SEGMENTS` and `LINE1/LINE2_SEGMENTS` must agree.

    They are two hand-maintained lists of the same thing: the renderer's
    `LINE1_SEGMENTS`/`LINE2_SEGMENTS` decide what is drawn, and the CLI's
    `STATUSLINE_SEGMENTS` is the catalogue `audio-hooks statusline segments`
    publishes and users configure against. `bin/audio-hooks.py` has carried a
    "Keep this in lock-step" comment since v6.3.0 with nothing enforcing it.

    Drift here is silent in the worst way: a segment in the catalogue but not
    the renderer is advertised and never appears, and the status line swallows
    every error (`sys.exit(0)`), so there is no diagnostic either way.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_module()
        src = (Path(__file__).resolve().parent.parent / "bin" / "audio-hooks.py").read_text(encoding="utf-8")
        block = re.search(r"STATUSLINE_SEGMENTS\s*[:=].*?\n\]", src, re.DOTALL)
        assert block, "STATUSLINE_SEGMENTS not found"
        cls.catalog_names = re.findall(r'"name":\s*"([a-z0-9_]+)"', block.group(0))
        cls.catalog_lines = {
            name: int(line)
            for name, line in re.findall(
                r'"name":\s*"([a-z0-9_]+)",\s*"line":\s*(\d+)', block.group(0)
            )
        }

    def test_catalog_and_renderer_have_the_same_segments(self) -> None:
        self.assertEqual(
            set(self.catalog_names),
            set(self.mod.ALL_SEGMENTS),
            "statusline segment catalogue has drifted from the renderer",
        )

    def test_catalog_line_numbers_match_the_renderer(self) -> None:
        for name, line in self.catalog_lines.items():
            expected = 1 if name in self.mod.LINE1_SEGMENTS else 2
            self.assertEqual(
                line, expected, f"segment {name!r} is on line {line} in the catalogue"
            )

    def test_catalog_has_no_duplicates(self) -> None:
        self.assertEqual(len(self.catalog_names), len(set(self.catalog_names)))

    def test_every_alias_resolves_to_a_real_segment(self) -> None:
        """`_normalise_segments` silently drops an unknown name, so an alias
        pointing at a non-segment is a no-op the user cannot debug."""
        for alias, target in self.mod._SEGMENT_ALIASES.items():
            self.assertIn(
                target, self.mod.ALL_SEGMENTS, f"alias {alias!r} -> {target!r} is not a segment"
            )


# ---------------------------------------------------------------------------
# v6.7 segments: prompt_cache, spend_limit, fast_mode, remote
# ---------------------------------------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


class TestPromptCacheFormat(unittest.TestCase):
    """``_fmt_prompt_cache`` turns the ``prompt_cache`` object into one compact
    segment. Whatever Claude Code sends it must return a string and never raise
    -- an exception here makes the whole status line print nothing."""

    NOW = 1_800_000_000

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def fmt(self, cache):
        return _plain(self.mod._fmt_prompt_cache(cache, now=self.NOW))

    def warm(self, remaining, **extra):
        cache = {"warm": True, "caching_observed": True, "ttl": "5m",
                 "expires_at": self.NOW + remaining}
        cache.update(extra)
        return cache

    def test_warm_shows_countdown(self):
        self.assertEqual(self.fmt(self.warm(252)), "cache warm 4m")
        self.assertEqual(self.fmt(self.warm(3500)), "cache warm 58m")

    def test_expiry_countdown_boundaries(self):
        # Seconds until expires_at, and what the segment says about it.
        cases = {
            3600: "cache warm 1h",
            61: "cache warm 1m",
            60: "cache warm 1m",
            59: "cache warm 59s",
            1: "cache warm 1s",
            0: "cache cold",       # expires_at reached: cold, even though warm:true
            -1: "cache cold",
            -3600: "cache cold",
        }
        for remaining, expected in cases.items():
            with self.subTest(remaining=remaining):
                self.assertEqual(self.fmt(self.warm(remaining)), expected)

    def test_colour_turns_warning_at_the_last_minute(self):
        green = self.mod._fmt_prompt_cache(self.warm(61), now=self.NOW)
        yellow = self.mod._fmt_prompt_cache(self.warm(60), now=self.NOW)
        self.assertIn(self.mod.GREEN, green)
        self.assertNotIn(self.mod.YELLOW, green)
        self.assertIn(self.mod.YELLOW, yellow)
        self.assertIn(self.mod.YELLOW, self.mod._fmt_prompt_cache(self.warm(0), now=self.NOW))

    def test_warm_without_expiry_has_no_countdown(self):
        for expires in (None, "soon", True, [], float("nan"), float("inf"), 0, -5):
            with self.subTest(expires_at=expires):
                cache = {"warm": True, "caching_observed": True, "expires_at": expires}
                self.assertEqual(self.fmt(cache), "cache warm")

    def test_implausible_expiry_is_not_shown_as_a_huge_countdown(self):
        # A millisecond epoch is ~1000x too far out; "1000h" would be wrong.
        cache = self.warm(0)
        cache["expires_at"] = (self.NOW + 300) * 1000
        self.assertEqual(self.fmt(cache), "cache warm")

    def test_cold(self):
        cache = {"warm": False, "caching_observed": True, "expires_at": None}
        self.assertEqual(self.fmt(cache), "cache cold")

    def test_cold_reports_recache_size_when_known(self):
        base = {"warm": False, "caching_observed": True}
        self.assertEqual(self.fmt(dict(base, recache_tokens_if_cold=45000)),
                         "cache cold · 45K to re-cache")
        for v in (None, 0, 999, "45000", True, float("nan"), -5):
            with self.subTest(recache=v):
                self.assertEqual(self.fmt(dict(base, recache_tokens_if_cold=v)), "cache cold")

    def test_warm_state_inferred_from_expiry_when_flag_is_missing_or_malformed(self):
        for warm in (None, "true", 1, "yes"):
            with self.subTest(warm=warm):
                live = {"warm": warm, "expires_at": self.NOW + 120}
                dead = {"warm": warm, "expires_at": self.NOW - 120}
                self.assertEqual(self.fmt(live), "cache warm 2m")
                self.assertEqual(self.fmt(dead), "cache cold")

    def test_caching_not_observed_renders_nothing(self):
        # Prompt caching is off, or the provider doesn't report it: there is no
        # cache state to describe, and "cold" would be a false alarm.
        cache = {"warm": False, "caching_observed": False, "expires_at": None}
        self.assertEqual(self.fmt(cache), "")

    def test_absent_null_and_wrong_type_render_nothing(self):
        for v in (None, {}, [], "warm", 5, 1.5, True, False, [{"warm": True}]):
            with self.subTest(value=v):
                self.assertEqual(self.fmt(v), "")

    def test_no_usable_signal_renders_nothing(self):
        for cache in ({"caching_observed": True}, {"warm": "maybe"},
                      {"warm": None, "expires_at": None}, {"requests": 3, "misses": 1}):
            with self.subTest(cache=cache):
                self.assertEqual(self.fmt(cache), "")

    def test_recent_miss_names_the_cause(self):
        cache = self.warm(200, last_miss_at=self.NOW - 30,
                          last_miss_cause={"causes": ["tools_changed"], "tools_added": 2, "tools_removed": 0})
        self.assertEqual(self.fmt(cache), "cache warm 3m · miss: tools changed")

    def test_cold_with_recent_miss_cause(self):
        cache = {"warm": False, "caching_observed": True, "recache_tokens_if_cold": 45000,
                 "last_miss_at": self.NOW - 90,
                 "last_miss_cause": {"causes": ["ttl_expired_5m"]}}
        self.assertEqual(self.fmt(cache),
                         "cache cold · 45K to re-cache · miss: idle past 5m TTL")

    def test_miss_without_diagnosed_cause_is_a_bare_miss(self):
        for cause in (None, {}, {"causes": []}, {"causes": None}, "tools_changed", 7,
                      {"causes": [1, None, ""]}):
            with self.subTest(cause=cause):
                cache = self.warm(200, last_miss_at=self.NOW - 30, last_miss_cause=cause)
                self.assertEqual(self.fmt(cache), "cache warm 3m · miss")

    def test_old_miss_is_not_shown(self):
        # Boundary: shown through RECENT_MISS_SEC, gone one second later.
        limit = self.mod.RECENT_MISS_SEC
        cause = {"causes": ["tools_changed"]}
        shown = self.warm(200, last_miss_at=self.NOW - limit, last_miss_cause=cause)
        gone = self.warm(200, last_miss_at=self.NOW - limit - 1, last_miss_cause=cause)
        self.assertIn("miss: tools changed", self.fmt(shown))
        self.assertEqual(self.fmt(gone), "cache warm 3m")

    def test_miss_timestamp_garbage_is_ignored(self):
        cause = {"causes": ["tools_changed"]}
        future = self.NOW + 10_000
        for at in (None, "yesterday", True, 0, -5, float("nan"), future, []):
            with self.subTest(last_miss_at=at):
                cache = self.warm(200, last_miss_at=at, last_miss_cause=cause)
                self.assertEqual(self.fmt(cache), "cache warm 3m")

    def test_small_clock_skew_still_counts_as_recent(self):
        cache = self.warm(200, last_miss_at=self.NOW + 5, last_miss_cause={"causes": ["tools_changed"]})
        self.assertIn("miss: tools changed", self.fmt(cache))

    def test_unknown_cause_falls_back_to_its_name(self):
        cache = self.warm(200, last_miss_at=self.NOW - 1,
                          last_miss_cause={"causes": ["brand_new_cause"]})
        self.assertIn("miss: brand new cause", self.fmt(cache))

    def test_every_known_cause_has_a_short_label(self):
        for name, label in self.mod._CACHE_CAUSE_LABELS.items():
            with self.subTest(cause=name):
                self.assertTrue(label)
                self.assertLessEqual(len(label), 28)

    def test_long_cause_text_is_clipped(self):
        causes = ["x" * 60, "y" * 60, "z" * 60]
        cache = self.warm(200, last_miss_at=self.NOW - 1, last_miss_cause={"causes": causes})
        out = self.fmt(cache)
        self.assertIn("… +2", out)  # first cause, clipped, plus a count of the rest
        self.assertLessEqual(len(out), 60)
        self.assertNotIn("yyyyy", out)

    def test_several_causes_show_the_first_and_a_count(self):
        cache = self.warm(200, last_miss_at=self.NOW - 1,
                          last_miss_cause={"causes": ["tools_changed", "model_changed"]})
        self.assertIn("miss: tools changed +1", self.fmt(cache))

    def test_never_raises_on_hostile_values(self):
        hostile = [None, True, "x", -1, 10 ** 400, float("nan"), float("inf"), [], {}, {"a": 1}, [[]]]
        for v in hostile:
            for key in ("warm", "caching_observed", "expires_at", "last_miss_at",
                        "last_miss_cause", "recache_tokens_if_cold", "ttl"):
                with self.subTest(key=key, value=repr(v)[:20]):
                    out = self.mod._fmt_prompt_cache({"warm": True, key: v}, now=self.NOW)
                    self.assertIsInstance(out, str)

    def test_uses_wall_clock_when_now_is_omitted(self):
        import time as _t
        out = _plain(self.mod._fmt_prompt_cache({"warm": True, "expires_at": _t.time() + 600}))
        self.assertRegex(out, r"^cache warm (9|10)m$")


class TestSpendLimitFormat(unittest.TestCase):
    """``_fmt_spend_limit``: the gateway spend limit. Only ``used_percentage``
    is guaranteed; every other field can be absent (upstream says so)."""

    NOW = 1_800_000_000

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def fmt(self, spend):
        return _plain(self.mod._fmt_spend_limit(spend, now=self.NOW))

    def test_percentage_only_for_older_gateways(self):
        self.assertEqual(self.fmt({"used_percentage": 62.8}),
                         "████░░░░ Spend: 62%")

    def test_full_payload(self):
        out = self.fmt({"used_percentage": 62.8, "used_usd": 314.12, "limit_usd": 500,
                        "period": "monthly", "resets_at": self.NOW + 5 * 86400})
        self.assertIn("Spend: 62% · $314.12/$500 monthly · resets ", out)

    def test_whole_dollar_amounts_drop_the_cents(self):
        out = self.fmt({"used_percentage": 50, "used_usd": 250, "limit_usd": 500.0})
        self.assertIn("$250/$500", out)

    def test_amounts_need_both_halves(self):
        for spend in ({"used_percentage": 40, "used_usd": 200},
                      {"used_percentage": 40, "limit_usd": 500}):
            with self.subTest(spend=spend):
                self.assertNotIn("$", self.fmt(spend))

    def test_unknown_period_is_dropped_not_echoed(self):
        out = self.fmt({"used_percentage": 40, "used_usd": 1, "limit_usd": 2, "period": "fortnightly"})
        self.assertTrue(out.endswith("$1/$2"), out)

    def test_over_the_limit_keeps_the_real_number(self):
        self.assertIn("Spend: 112%", self.fmt({"used_percentage": 112.4}))
        raw = self.mod._fmt_spend_limit({"used_percentage": 112.4}, now=self.NOW)
        self.assertIn(self.mod.RED, raw)

    def test_colour_thresholds_follow_the_rate_limit_bar(self):
        for pct, colour in ((10, "GREEN"), (70, "YELLOW"), (90, "RED")):
            with self.subTest(pct=pct):
                self.assertIn(getattr(self.mod, colour),
                              self.mod._fmt_spend_limit({"used_percentage": pct}))

    def test_absent_null_and_wrong_type_render_nothing(self):
        for v in (None, {}, [], "62", 62, True, {"used_percentage": None},
                  {"used_percentage": "62"}, {"used_percentage": True},
                  {"used_percentage": -3}, {"used_percentage": float("nan")},
                  {"used_usd": 1, "limit_usd": 2}):
            with self.subTest(value=v):
                self.assertEqual(self.fmt(v), "")

    def test_malformed_optional_fields_degrade_to_the_percentage(self):
        out = self.fmt({"used_percentage": 30, "used_usd": "lots", "limit_usd": None,
                        "period": 7, "resets_at": "soon"})
        self.assertEqual(out, "██░░░░░░ Spend: 30%")
        out = self.fmt({"used_percentage": 30, "used_usd": -5, "limit_usd": 100})
        self.assertNotIn("$", out)

    def test_never_raises_on_hostile_values(self):
        hostile = [None, True, "x", -1, 10 ** 400, float("nan"), float("inf"), [], {}]
        for v in hostile:
            for key in ("used_percentage", "resets_at", "used_usd", "limit_usd", "period"):
                with self.subTest(key=key, value=repr(v)[:20]):
                    out = self.mod._fmt_spend_limit({"used_percentage": 50, key: v}, now=self.NOW)
                    self.assertIsInstance(out, str)


class TestFastModeAndRemoteFormat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_fast_mode_only_when_actually_true(self):
        self.assertEqual(self.mod._fmt_fast_mode(True), "\U0001f680 fast")
        for v in (False, None, 0, 1, "true", "on", {}, [], {"enabled": True}):
            with self.subTest(value=v):
                self.assertEqual(self.mod._fmt_fast_mode(v), "")

    def test_remote_needs_a_session_id(self):
        self.assertEqual(self.mod._fmt_remote({"session_id": "abc"}), "☁ remote")
        for v in (None, {}, [], "abc", True, {"session_id": ""}, {"session_id": None},
                  {"session_id": 5}, {"other": "x"}):
            with self.subTest(value=v):
                self.assertEqual(self.mod._fmt_remote(v), "")


class TestV67SegmentRendering(_StatuslineRenderBase):
    """End to end through the real script: default visibility, opt-in, and the
    guarantee that an upgrade changes nothing unless new data is present."""

    def _status(self, **sl):
        status = dict(self._MINIMAL_STATUS)
        status["statusline"] = dict({"visible_segments": []}, **sl)
        for sid in ("t", "default"):
            (self.tmp / f"statusline.cache.{sid}").write_text(json.dumps(status), encoding="utf-8")

    def _render(self, payload, columns="200"):
        payload = dict({"session_id": "t"}, **payload)
        rc, out, _ = _run(json.dumps(payload), state_dir=self.tmp, env_extra={"COLUMNS": columns})
        self.assertEqual(rc, 0)
        return _plain(out)

    def _cache(self, **extra):
        import time as _t
        cache = {"warm": True, "caching_observed": True, "ttl": "5m",
                 "expires_at": int(_t.time()) + 250, "requests": 4, "misses": 0, "hit_ratio": 0.9}
        cache.update(extra)
        return cache

    def test_default_set_excludes_the_opt_in_segments(self):
        out = self._render({"prompt_cache": self._cache(), "remote": {"session_id": "abc"}})
        self.assertNotIn("cache warm", out)
        self.assertNotIn("remote", out)

    def test_upgrade_leaves_a_plain_session_byte_identical(self):
        # What an existing user sees today must not change when none of the new
        # default-on fields are present -- nor when only the opt-in ones are.
        plain = {"model": {"display_name": "Opus"}, "cwd": "/srv/proj",
                 "context_window": {"used_percentage": 30, "context_window_size": 200000},
                 "fast_mode": False}
        before = self._render(plain)
        with_optin_data = self._render(dict(plain, prompt_cache=self._cache(),
                                            remote={"session_id": "abc"}))
        self.assertEqual(before, with_optin_data)
        self.assertNotIn("fast", before)
        self.assertNotIn("Spend", before)

    def test_extra_segments_opts_in(self):
        self._status(extra_segments=["prompt_cache", "remote"])
        out = self._render({"prompt_cache": self._cache(), "remote": {"session_id": "abc"}})
        self.assertIn("cache warm 4m", out)
        self.assertIn("☁ remote", out)

    def test_extra_segments_is_per_segment(self):
        self._status(extra_segments=["prompt_cache"])
        out = self._render({"prompt_cache": self._cache(), "remote": {"session_id": "abc"}})
        self.assertIn("cache warm", out)
        self.assertNotIn("☁ remote", out)

    def test_hidden_segments_beats_extra_segments(self):
        self._status(extra_segments=["prompt_cache"], hidden_segments=["prompt_cache"])
        self.assertNotIn("cache warm", self._render({"prompt_cache": self._cache()}))

    def test_whitelist_can_name_an_opt_in_segment_directly(self):
        self._status(visible_segments=["prompt_cache", "model"])
        out = self._render({"model": {"display_name": "Opus"}, "prompt_cache": self._cache()})
        self.assertIn("cache warm", out)

    def test_extra_segments_ignored_when_whitelist_is_set(self):
        self._status(visible_segments=["model"], extra_segments=["prompt_cache"])
        self.assertNotIn("cache warm", self._render({"prompt_cache": self._cache()}))

    def test_malformed_extra_segments_is_ignored(self):
        for bad in ("prompt_cache", 5, {"prompt_cache": True}, None, [5, None, "nonsense"]):
            with self.subTest(extra=bad):
                self._status(extra_segments=bad)
                out = self._render({"prompt_cache": self._cache()})
                self.assertNotIn("cache warm", out)

    def test_fast_mode_and_spend_limit_show_by_default_when_present(self):
        out = self._render({
            "fast_mode": True,
            "rate_limits": {"spend_limit": {"used_percentage": 62.8, "used_usd": 314.12,
                                            "limit_usd": 500, "period": "monthly"}},
        })
        self.assertIn("\U0001f680 fast", out)
        self.assertIn("Spend: 62% · $314.12/$500 monthly", out)

    def test_spend_limit_does_not_disturb_the_subscriber_quotas(self):
        out = self._render({"rate_limits": {
            "five_hour": {"used_percentage": 20, "resets_at": 1893456000},
            "spend_limit": {"used_percentage": 10}}})
        self.assertIn("API Quota: 20%", out)
        self.assertIn("Spend: 10%", out)

    def test_hidden_segments_can_drop_the_new_default_segments(self):
        self._status(hidden_segments=["fast_mode", "spend_limit"])
        out = self._render({"fast_mode": True,
                            "rate_limits": {"spend_limit": {"used_percentage": 10}}})
        self.assertNotIn("fast", out)
        self.assertNotIn("Spend", out)

    def test_malformed_new_fields_never_break_the_line(self):
        self._status(extra_segments=["prompt_cache", "remote"])
        junk = [None, True, "x", 5, [], {}, {"warm": "yes", "expires_at": {}}, [1, 2]]
        for v in junk:
            with self.subTest(value=v):
                out = self._render({
                    "model": {"display_name": "Opus"},
                    "fast_mode": v, "remote": v, "prompt_cache": v,
                    "rate_limits": {"spend_limit": v},
                })
                self.assertIn("[Opus]", out)  # the line still rendered

    def test_junk_entries_in_a_segment_list_are_ignored_not_fatal(self):
        """An unhashable entry used to raise out of _normalise_segments and blank the line."""
        junk = [["prompt_cache"], {"a": 1}, None, 5, True, 1.5, ["model"]]
        base = {"model": {"display_name": "Opus"}, "prompt_cache": self._cache()}
        for key in ("extra_segments", "hidden_segments", "visible_segments"):
            for entries in ([junk[0]], [junk[1]], junk, junk + ["model"]):
                with self.subTest(key=key, entries=entries):
                    self._status(**{key: entries})
                    out = self._render(base)
                    self.assertTrue(out.strip(), "the line went blank")
                    if "model" not in entries:
                        self.assertIn("[Opus]", out)
        # a junk-only whitelist is "unset" (default segments), not "show nothing"
        self._status(visible_segments=[["model"], {"a": 1}])
        self.assertIn("[Opus]", self._render(base))
        # string entries beside junk still apply
        self._status(extra_segments=[["x"], "prompt_cache"])
        self.assertIn("cache warm", self._render(base))
        self._status(hidden_segments=[{"a": 1}, "model"])
        self.assertNotIn("[Opus]", self._render(base))
        # a non-list value is ignored too
        for bad in ("model", 5, {"model": True}):
            self._status(hidden_segments=bad, visible_segments=bad)
            self.assertIn("[Opus]", self._render(base))

    def test_absurd_percentages_are_clamped_for_display(self):
        out = self._render({"model": {"display_name": "Opus"},
                            "context_window": {"used_percentage": 1e308},
                            "rate_limits": {"five_hour": {"used_percentage": 1e308},
                                            "seven_day": {"used_percentage": -1e308},
                                            "spend_limit": {"used_percentage": 1e308}}})
        self.assertIn("[Opus]", out)
        self.assertIn("Context: 9999%", out)
        self.assertIn("API Quota: 9999%", out)
        self.assertIn("Spend: 9999%", out)
        self.assertNotRegex(out, r"\d{6,}")

    def test_a_boolean_resets_at_is_not_a_date(self):
        out = self._render({"model": {"display_name": "Opus"},
                            "rate_limits": {"five_hour": {"used_percentage": 20, "resets_at": True},
                                            "seven_day": {"used_percentage": 30, "resets_at": False}}})
        self.assertIn("API Quota: 20%", out)
        self.assertNotIn("1970", out)
        self.assertNotIn("resets", out)

    def test_non_dict_rate_limits_is_safe(self):
        for rl in (None, [], "x", 5):
            with self.subTest(rate_limits=rl):
                out = self._render({"model": {"display_name": "Opus"}, "rate_limits": rl})
                self.assertIn("[Opus]", out)

    def test_cache_segment_lands_on_line_two_and_fast_on_line_one(self):
        self._status(extra_segments=["prompt_cache"])
        out = self._render({"model": {"display_name": "Opus"}, "fast_mode": True,
                            "prompt_cache": self._cache()})
        lines = [l for l in out.splitlines() if l.strip()]
        self.assertIn("fast", lines[0])
        self.assertTrue(any("cache warm" in l for l in lines[1:]))
        self.assertNotIn("cache warm", lines[0])

    def test_width_truncation_keeps_segments_whole_and_rows_in_budget(self):
        # Worst case: every new segment populated, with the longest cause text,
        # on a narrow terminal. Rows wrap at segment boundaries; a row may
        # exceed the budget only when it is a single unsplittable segment, and
        # that segment must still be intact rather than clipped mid-way.
        import time as _t
        self._status(extra_segments=["prompt_cache", "remote"])
        now = int(_t.time())
        payload = {
            "model": {"display_name": "Opus"}, "fast_mode": True,
            "remote": {"session_id": "abc"},
            "prompt_cache": self._cache(
                warm=False, expires_at=None, recache_tokens_if_cold=1_250_000,
                last_miss_at=now - 5,
                last_miss_cause={"causes": ["system_prompt_changed", "tools_changed", "model_changed"]}),
            "rate_limits": {"spend_limit": {"used_percentage": 140, "used_usd": 1234.56,
                                            "limit_usd": 1000, "period": "monthly",
                                            "resets_at": now + 86400 * 20}},
        }
        mod = _load_module()
        for columns in (40, 60, 80):
            with self.subTest(columns=columns):
                out = self._render(payload, columns=str(columns))
                budget = max(20, columns - mod.WIDTH_SAFETY_MARGIN)
                for line in [l for l in out.splitlines() if l.strip()]:
                    if "  " in line or " | " in line:  # more than one segment on the row
                        self.assertLessEqual(mod._vwidth(line), budget,
                                             msg=f"overflow at {columns}: {line!r}")
                self.assertIn("cache cold · 1.2M to re-cache · miss: system prompt changed +2", out)
                self.assertIn("Spend: 140%", out)
                self.assertIn("\U0001f680 fast", out)


class TestOptInSegmentCatalog(unittest.TestCase):
    """The catalogue's ``default`` flag and the renderer's ``OPT_IN_SEGMENTS``
    are two hand-kept copies of one decision; drift would advertise a segment
    as default that never shows (or the reverse)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_module()
        src = (REPO / "bin" / "audio-hooks.py").read_text(encoding="utf-8")
        block = re.search(r"STATUSLINE_SEGMENTS\s*[:=].*?\n\]", src, re.DOTALL).group(0)
        cls.defaults = {
            name: flag == "True"
            for name, flag in re.findall(r'"name":\s*"([a-z0-9_]+)".*?"default":\s*(True|False)\}', block)
        }

    def test_every_segment_declares_its_default(self):
        self.assertEqual(set(self.defaults), set(self.mod.ALL_SEGMENTS))

    def test_default_flag_matches_opt_in_set(self):
        off = {n for n, d in self.defaults.items() if not d}
        self.assertEqual(off, set(self.mod.OPT_IN_SEGMENTS))

    def test_opt_in_segments_are_real_segments(self):
        self.assertTrue(self.mod.OPT_IN_SEGMENTS <= self.mod.ALL_SEGMENTS)

    def test_segment_count(self):
        self.assertEqual(len(self.mod.ALL_SEGMENTS), 33)

    def test_template_ships_the_extra_segments_key(self):
        template = json.loads((REPO / "config" / "default_preferences.json").read_text(encoding="utf-8"))
        self.assertEqual(template["statusline_settings"]["extra_segments"], [])
