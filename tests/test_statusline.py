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
        for v in (None, 0, -1, "", "abc", {}):
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


if __name__ == "__main__":
    unittest.main()
