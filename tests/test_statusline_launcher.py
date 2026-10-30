"""A status line registered from the plugin cache must survive an upgrade.

A cached plugin's folder is named for its version and removed some time after an
upgrade, and `claude plugin update` touches no settings. Through 7.2.1 the
registration named that folder, so the line would stop, silently.
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))

from echook_cli import statusline  # noqa: E402

KEY = "audio-hooks@chanmeng-audio-hooks"


class LauncherTestBase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="launcher_"))
        self.addCleanup(shutil.rmtree, self.home, True)
        patcher = mock.patch.object(Path, "home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.settings = self.home / ".claude" / "settings.json"
        self.settings.parent.mkdir(parents=True)

    def cached_root(self, version: str) -> Path:
        root = self.home / ".claude" / "plugins" / "cache" / "chanmeng-audio-hooks" / "audio-hooks" / version
        (root / "bin").mkdir(parents=True)
        for name in ("audio-hooks-statusline.py", "audio-hooks-subagent-statusline.py"):
            (root / "bin" / name).write_text(f'import sys\nprint("{version}", sys.stdin.read())\n', encoding="utf-8")
        return root

    def set_installed(self, root: Path) -> None:
        path = self.home / ".claude" / "plugins" / "installed_plugins.json"
        path.write_text(json.dumps({"plugins": {KEY: [{"scope": "user", "installPath": str(root)}]}}), encoding="utf-8")

    def read_settings(self):
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def install(self, root: Path, *args) -> dict:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            statusline.cmd_statusline(["install", *args], root)
        return json.loads(out.getvalue())

    def run_registered(self, key: str) -> str:
        command = self.read_settings()[key]["command"]
        env = {**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home)}
        proc = subprocess.run(command.replace("python3 ", f'"{sys.executable}" ', 1).replace("python ", f'"{sys.executable}" ', 1),
                              shell=True, input="payload", capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()


class TestInstallFromTheCache(LauncherTestBase):
    def test_the_registration_names_no_version(self):
        root = self.cached_root("1.0.0")
        self.install(root)
        command = self.read_settings()["statusLine"]["command"]
        self.assertNotIn("1.0.0", command)
        self.assertNotIn("cache", command)
        self.assertIn("statusline-launcher.py", command)

    def test_the_line_survives_an_upgrade_and_the_old_folder_going(self):
        old = self.cached_root("1.0.0")
        self.set_installed(old)
        self.install(old)
        self.assertEqual(self.run_registered("statusLine"), "1.0.0 payload")

        self.set_installed(self.cached_root("2.0.0"))
        shutil.rmtree(old)
        self.assertEqual(self.run_registered("statusLine"), "2.0.0 payload")

    def test_a_missing_install_prints_nothing_and_exits_cleanly(self):
        self.install(self.cached_root("1.0.0"))
        self.assertEqual(self.run_registered("statusLine"), "")

    def test_the_subagent_row_is_registered_the_same_way(self):
        root = self.cached_root("1.0.0")
        self.set_installed(root)
        with contextlib.redirect_stdout(io.StringIO()):
            statusline.cmd_statusline(["subagent", "install"], root)
        self.assertIn("audio-hooks-subagent-statusline.py", self.read_settings()["subagentStatusLine"]["command"])
        self.assertEqual(self.run_registered("subagentStatusLine"), "1.0.0 payload")

    def test_a_checkout_still_registers_its_own_script(self):
        self.install(REPO)
        command = self.read_settings()["statusLine"]["command"]
        self.assertIn("audio-hooks-statusline.py", command)
        self.assertNotIn("statusline-launcher", command)


class TestReinstallAndRepoint(LauncherTestBase):
    def test_a_chosen_padding_is_kept(self):
        self.settings.write_text(json.dumps({"statusLine": {"type": "command", "command": "x", "padding": 1}}), encoding="utf-8")
        self.install(self.cached_root("1.0.0"))
        self.assertEqual(self.read_settings()["statusLine"]["padding"], 1)

    def test_a_first_install_uses_no_padding(self):
        self.install(self.cached_root("1.0.0"))
        self.assertEqual(self.read_settings()["statusLine"]["padding"], 0)

    def test_repoint_moves_pinned_registrations_and_keeps_everything_else(self):
        pinned = str(self.home / ".claude/plugins/cache/m/audio-hooks/1.0.0/bin/audio-hooks-statusline.py")
        self.settings.write_text(json.dumps({
            "model": "opus",
            "statusLine": {"type": "command", "command": f'python "{pinned}"', "padding": 1, "refreshInterval": 2},
        }), encoding="utf-8")
        self.assertEqual(statusline.repoint_cached_registrations(self.settings), ["statusLine"])
        settings = self.read_settings()
        self.assertEqual(settings["model"], "opus")
        self.assertEqual((settings["statusLine"]["padding"], settings["statusLine"]["refreshInterval"]), (1, 2))
        self.assertIn("statusline-launcher.py", settings["statusLine"]["command"])

    def test_repoint_leaves_a_checkout_or_foreign_line_alone(self):
        original = {"statusLine": {"type": "command", "command": 'python "D:/code/my-line.py"'}}
        self.settings.write_text(json.dumps(original), encoding="utf-8")
        self.assertEqual(statusline.repoint_cached_registrations(self.settings), [])
        self.assertEqual(self.read_settings(), original)

    def test_repoint_tolerates_missing_or_broken_settings(self):
        self.assertEqual(statusline.repoint_cached_registrations(self.settings), [])
        self.settings.write_text("{not json", encoding="utf-8")
        self.assertEqual(statusline.repoint_cached_registrations(self.settings), [])

    def test_show_flags_a_pinned_registration(self):
        self.settings.write_text(json.dumps({"statusLine": {
            "type": "command", "command": 'python "C:\\\\Users\\\\x\\\\.claude\\\\plugins\\\\cache\\\\m\\\\p\\\\1.0.0\\\\bin\\\\s.py"', "refreshInterval": 2}}), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            statusline.cmd_statusline(["show"], REPO)
        steps = json.loads(out.getvalue())["next_steps"]
        self.assertEqual(len(steps), 1)
        self.assertIn("cached version", steps[0])


if __name__ == "__main__":
    unittest.main()
