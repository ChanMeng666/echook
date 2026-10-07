"""Tests for Codex status line curation (``audio-hooks statusline codex``).

Codex's status line is NOT command-backed: it accepts only a fixed list of
built-in item IDs under ``[tui].status_line`` in ``config.toml``. echook can
only *curate* that list. These tests pin two contracts:

  1. The pure TOML helpers (``_codex_apply_status_line`` / ``_codex_read_status_line``)
     do a *surgical* single-array edit that preserves every other table,
     comment, and the file's formatting.
  2. The ``statusline codex {show,preview,apply}`` CLI behaves and backs up.

Stdlib-only (unittest, subprocess, tempfile) so it runs on the same matrix as
the smoke workflow without new dependencies.
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
from typing import Dict, Optional

REPO = Path(__file__).resolve().parent.parent
AUDIO_HOOKS_CLI = REPO / "bin" / "audio-hooks.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("audio_hooks_cli", AUDIO_HOOKS_CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_cli(args, *, env_extra: Optional[Dict[str, str]] = None):
    env = os.environ.copy()
    for k in ("CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT", "CLAUDE_AUDIO_HOOKS_DATA",
              "CURSOR_VERSION", "CODEX_HOME", "CLAUDE_HOOKS_DEBUG"):
        env.pop(k, None)
    if env_extra:
        env.update(env_extra)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(
        [sys.executable, str(AUDIO_HOOKS_CLI)] + list(args),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=30,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


# Representative config.toml mirroring the real layout: top-level keys, project
# subtables, a [tui] table holding a multi-line status_line array plus sibling
# keys, then a [tui.*] subtable and unrelated tables after it.
_CONFIG_WITH_MULTILINE_ARRAY = """model = "gpt-5.5"
approvals_reviewer = "user"

[projects.'d:\\repo']
trust_level = "trusted"

[tui]
status_line = [
    "model-with-reasoning",
    "current-dir",
    "model",
    "context-used",
]
status_line_use_colors = true
terminal_title = ["activity", "app-name"]

[tui.model_availability_nux]
"gpt-5.5" = 4

[features]
memories = true
"""


class TestCodexHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_array_render(self):
        out = self.mod._codex_status_line_array(["a", "b", "c"])
        self.assertEqual(out, 'status_line = ["a", "b", "c"]')

    def test_read_multiline(self):
        items = self.mod._codex_read_status_line(_CONFIG_WITH_MULTILINE_ARRAY)
        self.assertEqual(
            items, ["model-with-reasoning", "current-dir", "model", "context-used"]
        )

    def test_read_absent(self):
        self.assertIsNone(self.mod._codex_read_status_line("model = \"x\"\n"))

    def test_apply_replaces_multiline_array_only(self):
        new = self.mod._codex_apply_status_line(
            _CONFIG_WITH_MULTILINE_ARRAY, ["model-with-reasoning", "git-branch"]
        )
        # The new array is present, collapsed to a single line.
        self.assertIn('status_line = ["model-with-reasoning", "git-branch"]', new)
        # Everything else survives verbatim.
        self.assertIn("status_line_use_colors = true", new)
        self.assertIn('terminal_title = ["activity", "app-name"]', new)
        self.assertIn("[tui.model_availability_nux]", new)
        self.assertIn("memories = true", new)
        self.assertIn("[projects.'d:\\repo']", new)
        # The old items are gone.
        self.assertNotIn('"context-used"', new)
        # And it round-trips through the reader.
        self.assertEqual(
            self.mod._codex_read_status_line(new),
            ["model-with-reasoning", "git-branch"],
        )

    def test_apply_inserts_when_tui_has_no_status_line(self):
        text = "[tui]\nstatus_line_use_colors = true\n"
        new = self.mod._codex_apply_status_line(text, ["model", "git-branch"])
        self.assertEqual(
            self.mod._codex_read_status_line(new), ["model", "git-branch"]
        )
        self.assertIn("status_line_use_colors = true", new)

    def test_apply_appends_tui_when_absent(self):
        text = 'model = "gpt-5.5"\n'
        new = self.mod._codex_apply_status_line(text, ["model"])
        self.assertIn("[tui]", new)
        self.assertEqual(self.mod._codex_read_status_line(new), ["model"])
        self.assertIn('model = "gpt-5.5"', new)

    def test_apply_to_empty_text(self):
        new = self.mod._codex_apply_status_line("", ["model"])
        self.assertEqual(self.mod._codex_read_status_line(new), ["model"])

    def test_presets_have_no_duplicates(self):
        for table in (self.mod.CODEX_STATUSLINE_PRESETS,
                      self.mod.CODEX_TERMINAL_TITLE_PRESETS):
            for name, items in table.items():
                with self.subTest(preset=name):
                    self.assertEqual(len(items), len(set(items)), f"{name} has dupes")

    def test_known_items_cover_codex_0_160_1(self):
        # Canonical IDs + strum aliases read from rust-v0.160.1
        # (status_line_setup.rs and title_setup.rs).
        canonical = {
            "model", "model-with-reasoning", "reasoning", "current-dir",
            "project-name", "hostname", "git-branch", "pull-request-number",
            "branch-changes", "run-state", "permissions", "approval-mode",
            "context-remaining", "context-used", "five-hour-limit",
            "weekly-limit", "codex-version", "context-window-size",
            "used-tokens", "total-input-tokens", "total-output-tokens",
            "thread-credits", "estimated-thread-cost", "thread-id", "fast-mode",
            "raw-output", "thread-name", "thread-title", "workspace-headline",
            "task-progress", "activity", "app-name",
        }
        aliases = {"model-name", "project", "project-root", "status",
                   "approval", "context-usage", "session-id", "thread", "spinner"}
        known = self.mod.CODEX_KNOWN_STATUSLINE_ITEMS
        self.assertFalse((canonical | aliases) - known)

    def test_presets_use_only_known_items(self):
        known = self.mod.CODEX_KNOWN_STATUSLINE_ITEMS
        for table in (self.mod.CODEX_STATUSLINE_PRESETS,
                      self.mod.CODEX_TERMINAL_TITLE_PRESETS):
            for name, items in table.items():
                with self.subTest(preset=name):
                    self.assertFalse(set(items) - known)

    def test_generic_array_targets_any_key(self):
        text = "[tui]\nstatus_line = [\"a\"]\nterminal_title = [\"x\", \"y\"]\n"
        # Editing terminal_title must not touch status_line and vice versa.
        new = self.mod._codex_apply_tui_array(text, "terminal_title", ["p"])
        self.assertEqual(self.mod._codex_read_tui_array(new, "terminal_title"), ["p"])
        self.assertEqual(self.mod._codex_read_tui_array(new, "status_line"), ["a"])

    def test_status_line_key_not_matched_by_prefix_sibling(self):
        # `status_line_use_colors` must NOT be mistaken for `status_line`.
        text = '[tui]\nstatus_line_use_colors = true\nstatus_line = ["a"]\n'
        new = self.mod._codex_apply_tui_array(text, "status_line", ["b"])
        self.assertIn("status_line_use_colors = true", new)
        self.assertEqual(self.mod._codex_read_tui_array(new, "status_line"), ["b"])


class TestCodexCli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="codex_sl_"))
        self.cfg = self.tmp / "config.toml"
        self.cfg.write_text(_CONFIG_WITH_MULTILINE_ARRAY, encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _env(self):
        return {"CODEX_HOME": str(self.tmp)}

    def test_show_reports_current(self):
        rc, out, _ = _run_cli(["statusline", "codex", "show"], env_extra=self._env())
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertTrue(d["ok"])
        self.assertEqual(d["item_count"], 4)
        self.assertIn("model-with-reasoning", d["current"])

    def test_preview_does_not_write(self):
        before = self.cfg.read_text(encoding="utf-8")
        rc, out, _ = _run_cli(
            ["statusline", "codex", "preview", "--preset", "minimal"],
            env_extra=self._env(),
        )
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertFalse(d["applied"])
        self.assertEqual(d["items"], self._load_preset("minimal"))
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), before)

    def test_apply_writes_and_backs_up(self):
        rc, out, _ = _run_cli(
            ["statusline", "codex", "apply", "--preset", "balanced"],
            env_extra=self._env(),
        )
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertTrue(d["applied"])
        self.assertIsNotNone(d["backup"])
        self.assertTrue(Path(d["backup"]).exists())
        # The live config now holds the balanced preset, nothing else lost.
        text = self.cfg.read_text(encoding="utf-8")
        self.assertIn("status_line_use_colors = true", text)
        self.assertIn("memories = true", text)
        mod = _load_module()
        self.assertEqual(
            mod._codex_read_status_line(text), self._load_preset("balanced")
        )

    def test_apply_items_override(self):
        rc, out, _ = _run_cli(
            ["statusline", "codex", "apply", "--items", "model,git-branch"],
            env_extra=self._env(),
        )
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual(d["items"], ["model", "git-branch"])

    def test_unknown_preset_errors(self):
        rc, out, _ = _run_cli(
            ["statusline", "codex", "apply", "--preset", "nope"],
            env_extra=self._env(),
        )
        self.assertNotEqual(rc, 0)
        d = json.loads(out)
        self.assertFalse(d["ok"])

    def test_apply_terminal_title(self):
        # config has no terminal_title yet — apply must insert it without
        # disturbing status_line.
        rc, out, _ = _run_cli(
            ["statusline", "codex", "apply", "--target", "terminal_title",
             "--preset", "minimal"],
            env_extra=self._env(),
        )
        self.assertEqual(rc, 0)
        mod = _load_module()
        text = self.cfg.read_text(encoding="utf-8")
        self.assertEqual(
            mod._codex_read_tui_array(text, "terminal_title"),
            mod.CODEX_TERMINAL_TITLE_PRESETS["minimal"],
        )
        # status_line untouched (still the fixture's 4 items).
        self.assertEqual(len(mod._codex_read_tui_array(text, "status_line")), 4)

    def test_apply_both(self):
        rc, out, _ = _run_cli(
            ["statusline", "codex", "apply", "--target", "both", "--preset", "balanced"],
            env_extra=self._env(),
        )
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual(d["target"], "both")
        self.assertIn("status_line", d["resolved"])
        self.assertIn("terminal_title", d["resolved"])
        mod = _load_module()
        text = self.cfg.read_text(encoding="utf-8")
        self.assertEqual(
            mod._codex_read_tui_array(text, "status_line"),
            mod.CODEX_STATUSLINE_PRESETS["balanced"],
        )
        self.assertEqual(
            mod._codex_read_tui_array(text, "terminal_title"),
            mod.CODEX_TERMINAL_TITLE_PRESETS["balanced"],
        )

    def test_items_with_both_errors(self):
        rc, out, _ = _run_cli(
            ["statusline", "codex", "apply", "--target", "both", "--items", "a,b"],
            env_extra=self._env(),
        )
        self.assertNotEqual(rc, 0)
        self.assertFalse(json.loads(out)["ok"])

    def test_unknown_target_errors(self):
        rc, out, _ = _run_cli(
            ["statusline", "codex", "apply", "--target", "nope"],
            env_extra=self._env(),
        )
        self.assertNotEqual(rc, 0)
        self.assertFalse(json.loads(out)["ok"])

    def _load_preset(self, name):
        return _load_module().CODEX_STATUSLINE_PRESETS[name]


class TestStatuslineSegments(unittest.TestCase):
    """`statusline segments` exposes the full Claude Code catalog for config."""

    def test_segments_catalog(self):
        rc, out, _ = _run_cli(["statusline", "segments"])
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertTrue(d["ok"])
        names = {s["name"] for s in d["segments"]}
        # Spot-check both legacy and new segments are advertised.
        for expected in ("model", "context", "cost", "duration", "git_dirty",
                         "burn_rate", "pr", "tokens", "session_name"):
            self.assertIn(expected, names)
        # Every segment is assigned to line 1 or 2.
        for s in d["segments"]:
            self.assertIn(s["line"], (1, 2))


if __name__ == "__main__":
    unittest.main()
