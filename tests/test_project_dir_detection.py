"""``hook_runner.get_project_dir`` accepts a checkout by either config file.

A script install (made before 7.0.0) runs ``~/.claude/hooks/hook_runner.py``,
which finds the checkout through ``.project_path``. It used to accept that
directory only when the tracked ``config/user_preferences.json`` was in it -- a
stale file acting as a sentinel. ``config/default_preferences.json`` is the file
a checkout really always has, so either one is enough now, and the tracked
``user_preferences.json`` can be deleted one release after 7.0.0 (an installed
old runner still tests for it on the first hook after ``git pull``, before it
updates itself).

``get_project_dir`` is called in-process with ``__file__`` and ``Path.home``
patched to a temp directory, so no runner is spawned and no real home is read.
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "hooks"))

import hook_runner as HR  # noqa: E402


class TestProjectDirAcceptance(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name).resolve()
        self.home = self.root / "home"
        # The installed runner's directory: ~/.claude/hooks, whose parent is not a checkout.
        self.script_dir = self.home / ".claude" / "hooks"
        self.script_dir.mkdir(parents=True)
        self.project = self.root / "checkout"
        (self.project / "config").mkdir(parents=True)

    def _resolve(self) -> Path:
        with mock.patch.object(HR, "__file__", str(self.script_dir / "hook_runner.py")), \
                mock.patch.object(Path, "home", return_value=self.home):
            return HR.get_project_dir()

    def _record(self, project: Path) -> None:
        (self.script_dir / ".project_path").write_text(str(project), encoding="utf-8")

    def _fallback(self) -> Path:
        return self.script_dir.parent

    # -- the helper ---------------------------------------------------------

    def test_helper_accepts_either_file_and_rejects_neither(self) -> None:
        self.assertFalse(HR._has_project_config(self.project))
        (self.project / "config" / "default_preferences.json").write_text("{}", encoding="utf-8")
        self.assertTrue(HR._has_project_config(self.project))
        (self.project / "config" / "default_preferences.json").unlink()
        (self.project / "config" / "user_preferences.json").write_text("{}", encoding="utf-8")
        self.assertTrue(HR._has_project_config(self.project))

    # -- strategy 1: .project_path -----------------------------------------

    def test_recorded_dir_with_only_default_preferences_is_accepted(self) -> None:
        (self.project / "config" / "default_preferences.json").write_text("{}", encoding="utf-8")
        self._record(self.project)
        self.assertEqual(self._resolve(), self.project)

    def test_recorded_dir_with_only_user_preferences_is_still_accepted(self) -> None:
        (self.project / "config" / "user_preferences.json").write_text("{}", encoding="utf-8")
        self._record(self.project)
        self.assertEqual(self._resolve(), self.project)

    def test_recorded_dir_with_neither_is_rejected(self) -> None:
        (self.project / "config" / "audio_manifest.json").write_text("{}", encoding="utf-8")
        self._record(self.project)
        self.assertEqual(self._resolve(), self._fallback())

    def test_recorded_dir_that_does_not_exist_is_rejected(self) -> None:
        self._record(self.root / "gone")
        self.assertEqual(self._resolve(), self._fallback())

    # -- strategy 2: the runner sits in <checkout>/hooks --------------------

    def test_parent_dir_with_only_default_preferences_is_accepted(self) -> None:
        in_checkout = self.project / "hooks"
        in_checkout.mkdir()
        (self.project / "config" / "default_preferences.json").write_text("{}", encoding="utf-8")
        # A common location that would otherwise win if strategy 2 rejected the parent.
        decoy = self.home / "echook" / "config"
        decoy.mkdir(parents=True)
        (decoy / "default_preferences.json").write_text("{}", encoding="utf-8")
        with mock.patch.object(HR, "__file__", str(in_checkout / "hook_runner.py")), \
                mock.patch.object(Path, "home", return_value=self.home):
            self.assertEqual(HR.get_project_dir(), self.project)

    # -- strategy 3: common locations --------------------------------------

    def test_common_location_with_only_default_preferences_is_accepted(self) -> None:
        loc = self.home / "projects" / "echook"
        (loc / "config").mkdir(parents=True)
        (loc / "config" / "default_preferences.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self._resolve(), loc)

    def test_common_location_with_neither_is_rejected(self) -> None:
        (self.home / "projects" / "echook" / "config").mkdir(parents=True)
        self.assertEqual(self._resolve(), self._fallback())

    # -- the real checkout --------------------------------------------------

    def test_this_checkout_qualifies_by_the_template_alone(self) -> None:
        self.assertTrue((REPO / "config" / "default_preferences.json").is_file())
        self.assertTrue(HR._has_project_config(REPO))


if __name__ == "__main__":
    unittest.main()
