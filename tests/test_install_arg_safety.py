"""Tests for the v6.6 argument handling of ``audio-hooks install`` / ``uninstall``.

Before v6.6 ``cmd_install`` defaulted to the legacy script installer and
ignored every argument it did not recognise, so ``install --help``,
``install --bogus`` and a bare ``install`` all ran ``install-windows.ps1`` (or
``install-complete.sh``), rewrote ``~/.claude/settings.json`` and reported
``ok: true`` — on a machine with the plugin that means double-firing hooks.

Every test here runs the command in-process with ``subprocess.run`` patched, so
no installer can execute and no real home directory is written.
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
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "bin" / "audio-hooks.py"


def _load_cli():
    sys.modules.pop("audio_hooks_cli", None)
    spec = importlib.util.spec_from_file_location("audio_hooks_cli", CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["audio_hooks_cli"] = module
    spec.loader.exec_module(module)
    return module


RUNNER_CONTENT = '"""\necho' + 'ok - Python Hook Runner\n"""\n'  # carries the echook marker


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _load_cli()
        # subprocess.run is the only way either command reaches an installer.
        # The mock must stay in place for every test in this module.
        patcher = mock.patch("subprocess.run")
        self.run = patcher.start()
        self.run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        self.addCleanup(patcher.stop)

    def _call(self, fn_name: str, args, plugin_installed: bool = False):
        out = io.StringIO()
        with mock.patch.object(
            self.cli, "_detect_install_mode",
            return_value={"script_install": False, "plugin_install": plugin_installed},
        ), contextlib.redirect_stdout(out):
            rc = getattr(self.cli, fn_name)(list(args))
        text = out.getvalue().strip()
        return rc, json.loads(text.splitlines()[-1])


class TestInstallArgs(_Base):
    def test_help_flags_emit_usage_without_side_effects(self) -> None:
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                rc, doc = self._call("cmd_install", [flag])
                self.assertEqual(rc, 0)
                self.assertTrue(doc["ok"])
                self.assertEqual(set(doc["modes"]), {"--plugin", "--scripts", "--cursor", "--codex"})
                self.assertIn("--force", doc["flags"])
        self.run.assert_not_called()

    def test_help_wins_over_a_mode_flag(self) -> None:
        rc, doc = self._call("cmd_install", ["--scripts", "--help"])
        self.assertEqual(rc, 0)
        self.assertIn("usage", doc)
        self.run.assert_not_called()

    def test_unknown_flag_is_an_error(self) -> None:
        rc, doc = self._call("cmd_install", ["--bogus"])
        self.assertEqual(rc, 1)
        self.assertFalse(doc["ok"])
        self.assertEqual(doc["error"]["code"], "INVALID_USAGE")
        self.assertEqual(doc["unknown_args"], ["--bogus"])
        self.run.assert_not_called()

    def test_unknown_flag_next_to_a_valid_mode_is_still_an_error(self) -> None:
        rc, doc = self._call("cmd_install", ["--scripts", "--bogus"])
        self.assertEqual(rc, 1)
        self.assertEqual(doc["error"]["code"], "INVALID_USAGE")
        self.run.assert_not_called()

    def test_bare_install_does_not_default_to_scripts(self) -> None:
        rc, doc = self._call("cmd_install", [])
        self.assertEqual(rc, 1)
        self.assertEqual(doc["error"]["code"], "INVALID_USAGE")
        self.assertEqual(set(doc["modes"]), {"--plugin", "--scripts", "--cursor", "--codex"})
        for flag in ("--plugin", "--scripts", "--cursor", "--codex"):
            self.assertIn(f"audio-hooks install {flag}", doc["next_steps"])
        self.run.assert_not_called()

    def test_force_alone_is_not_a_mode(self) -> None:
        rc, doc = self._call("cmd_install", ["--force"])
        self.assertEqual(rc, 1)
        self.run.assert_not_called()

    def test_conflicting_modes_are_rejected(self) -> None:
        rc, doc = self._call("cmd_install", ["--scripts", "--cursor"])
        self.assertEqual(rc, 1)
        self.assertEqual(doc["error"]["code"], "INVALID_USAGE")
        self.run.assert_not_called()

    def test_scripts_refused_when_plugin_detected(self) -> None:
        rc, doc = self._call("cmd_install", ["--scripts"], plugin_installed=True)
        self.assertEqual(rc, 1)
        self.assertEqual(doc["error"]["code"], "DUAL_INSTALL_DETECTED")
        self.run.assert_not_called()

    def test_scripts_runs_installer_when_plugin_absent(self) -> None:
        rc, doc = self._call("cmd_install", ["--scripts"], plugin_installed=False)
        self.assertEqual(rc, 0)
        self.assertEqual(doc["mode"], "scripts")
        self.run.assert_called_once()

    def test_scripts_force_overrides_plugin_detection(self) -> None:
        rc, doc = self._call("cmd_install", ["--scripts", "--force"], plugin_installed=True)
        self.assertEqual(rc, 0)
        self.run.assert_called_once()

    def test_plugin_mode_lists_claude_cli_commands(self) -> None:
        rc, doc = self._call("cmd_install", ["--plugin"])
        self.assertEqual(rc, 0)
        steps = doc["next_steps"]
        self.assertIn("claude plugin marketplace add ChanMeng666/echook --json", steps)
        self.assertIn("claude plugin install audio-hooks@chanmeng-audio-hooks --json", steps)
        # /reload-plugins has no CLI form, so it must be phrased as a request.
        self.assertTrue(any("/reload-plugins" in s and s.startswith("Ask the user") for s in steps))
        self.assertFalse(any(s.startswith("Run inside Claude Code") for s in steps))
        self.run.assert_not_called()


class TestUninstallArgs(_Base):
    def test_help_flags_emit_usage_without_side_effects(self) -> None:
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                rc, doc = self._call("cmd_uninstall", [flag])
                self.assertEqual(rc, 0)
                self.assertTrue(doc["ok"])
                self.assertIn("--purge", doc["flags"])
        self.run.assert_not_called()

    def test_unknown_flag_is_an_error(self) -> None:
        rc, doc = self._call("cmd_uninstall", ["--bogus"])
        self.assertEqual(rc, 1)
        self.assertEqual(doc["error"]["code"], "INVALID_USAGE")
        self.assertEqual(doc["unknown_args"], ["--bogus"])
        self.run.assert_not_called()

    def test_unknown_flag_next_to_a_valid_mode_is_still_an_error(self) -> None:
        rc, doc = self._call("cmd_uninstall", ["--scripts", "--bogus"])
        self.assertEqual(rc, 1)
        self.run.assert_not_called()

    def test_uninstall_usage_has_its_own_mode_text(self) -> None:
        _, doc = self._call("cmd_uninstall", ["--help"])
        text = json.dumps(doc)
        self.assertNotIn("Refused when the plugin is installed", text)
        self.assertNotIn("--force", text)
        self.assertTrue(doc["usage"].startswith("audio-hooks uninstall [--plugin|--scripts|--cursor|--codex]"))
        self.assertIn("by content", doc["modes"]["--scripts"])
        self.assertIn("--cursor", doc["flags"]["--purge"])
        # install keeps its own text
        _, idoc = self._call("cmd_install", ["--help"])
        self.assertIn("Refused when the plugin is installed", idoc["modes"]["--scripts"])
        self.assertTrue(idoc["usage"].startswith("audio-hooks install --plugin|"))

    def test_bare_uninstall_is_native_on_every_platform(self) -> None:
        """The documented remedy for DUAL_INSTALL_DETECTED must work everywhere,
        without shelling out to a scripts/uninstall.sh the plugin does not ship."""
        import tempfile
        for system in ("Linux", "Windows", "Darwin"):
            with self.subTest(system=system), tempfile.TemporaryDirectory() as td, \
                    mock.patch("platform.system", return_value=system), \
                    mock.patch.object(Path, "home", return_value=Path(td)):
                rc, doc = self._call("cmd_uninstall", [])
                self.assertEqual(rc, 0)
                self.assertEqual(doc["mode"], "scripts")
                self.assertTrue(doc["ok"])
        self.run.assert_not_called()

    def test_purge_is_rejected_for_scripts_and_plugin(self) -> None:
        for args in (["--purge"], ["--scripts", "--purge"], ["--plugin", "--purge"]):
            with self.subTest(args=args):
                rc, doc = self._call("cmd_uninstall", args)
                self.assertEqual(rc, 1)
                self.assertEqual(doc["error"]["code"], "INVALID_USAGE")
                self.assertIn("--cursor", doc["error"]["message"])
        self.run.assert_not_called()

    def test_plugin_mode_lists_claude_cli_command(self) -> None:
        rc, doc = self._call("cmd_uninstall", ["--plugin"])
        self.assertEqual(rc, 0)
        self.assertEqual(
            doc["next_steps"],
            ["claude plugin uninstall audio-hooks@chanmeng-audio-hooks --keep-data --json"])
        # Without --keep-data the plugin's data dir (preferences, backups) goes too.
        self.assertIn("--keep-data", doc["data_note"])
        self.run.assert_not_called()



class TestDualInstallMessages(_Base):
    """DUAL_INSTALL_DETECTED names the same, working remedy on every platform."""

    def test_warning_names_the_posix_remedy(self) -> None:
        with mock.patch("platform.system", return_value="Linux"),                 mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_ROOT": "/x"}),                 mock.patch.object(Path, "home", return_value=self.tmp_home):
            (self.tmp_home / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)
            (self.tmp_home / ".claude" / "hooks" / "hook_runner.py").write_text(RUNNER_CONTENT)
            info = self.cli._detect_install_mode()
        self.assertEqual(info["warning"]["code"], "DUAL_INSTALL_DETECTED")
        self.assertIn("Run `audio-hooks uninstall`", info["warning"]["message"])

    def test_warning_names_the_same_remedy_on_windows(self) -> None:
        with mock.patch("platform.system", return_value="Windows"),                 mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_ROOT": "/x"}),                 mock.patch.object(Path, "home", return_value=self.tmp_home):
            (self.tmp_home / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)
            (self.tmp_home / ".claude" / "hooks" / "hook_runner.py").write_text(RUNNER_CONTENT)
            info = self.cli._detect_install_mode()
        msg = info["warning"]["message"]
        self.assertIn("Run `audio-hooks uninstall`", msg)
        self.assertNotIn("uninstall.sh", msg)
        self.assertNotIn("WSL", msg)
        # ... and so does the remedy helper that status/diagnose/the install guard use.
        self.assertEqual(self.cli._script_uninstall_remedy()[1], "audio-hooks uninstall")

    def setUp(self) -> None:
        super().setUp()
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp_home = Path(self._td.name)


class TestScriptInstallDetectionIsByContent(_Base):
    """`status` / `diagnose` must agree with `uninstall`: a hook_runner.py that is not
    echook's is not a script install, or the DUAL_INSTALL_DETECTED remedy loops."""

    def setUp(self) -> None:
        super().setUp()
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.home = Path(self._td.name)
        (self.home / ".claude" / "hooks").mkdir(parents=True)

    def _detect(self):
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_ROOT": "/x"}), \
                mock.patch.object(Path, "home", return_value=self.home):
            return self.cli._detect_install_mode()

    def test_a_user_owned_runner_is_not_a_script_install(self) -> None:
        (self.home / ".claude" / "hooks" / "hook_runner.py").write_text("print('mine')\n")
        info = self._detect()
        self.assertFalse(info["script_install"])
        self.assertNotIn("warning", info)

    def test_an_empty_runner_is_not_a_script_install(self) -> None:
        (self.home / ".claude" / "hooks" / "hook_runner.py").write_text("")
        self.assertFalse(self._detect()["script_install"])

    def test_the_echook_runner_is(self) -> None:
        (self.home / ".claude" / "hooks" / "hook_runner.py").write_text(RUNNER_CONTENT)
        info = self._detect()
        self.assertTrue(info["script_install"])
        self.assertEqual(info["warning"]["code"], "DUAL_INSTALL_DETECTED")

    def test_uninstall_and_detection_agree_for_a_user_owned_runner(self) -> None:
        """The loop being closed: after `uninstall` the warning is not re-raised."""
        runner = self.home / ".claude" / "hooks" / "hook_runner.py"
        runner.write_text("print('mine')\n")
        out = io.StringIO()
        with mock.patch.object(Path, "home", return_value=self.home), contextlib.redirect_stdout(out):
            rc = self.cli.cmd_uninstall([])
        doc = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(rc, 0)
        self.assertTrue(doc["nothing_to_remove"])
        self.assertTrue(runner.exists())
        self.assertNotIn("warning", self._detect())


class TestPluginDetectionIgnoresOrphans(_Base):
    """Claude Code leaves ``<version>/.orphaned_at`` after an uninstall."""

    def setUp(self) -> None:
        super().setUp()
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.home = Path(self._td.name)
        self.versions = self.home / ".claude" / "plugins" / "cache" / "mkt" / "audio-hooks"

    def _make_version(self, name: str, orphaned: bool) -> None:
        d = self.versions / name
        (d / ".claude-plugin").mkdir(parents=True)
        (d / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "audio-hooks"}))
        if orphaned:
            (d / ".orphaned_at").write_text("1790829669990")

    def _plugin_installed(self) -> bool:
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
        with mock.patch.dict(os.environ, env, clear=True),                 mock.patch.object(Path, "home", return_value=self.home):
            return bool(self.cli._detect_install_mode()["plugin_install"])

    def test_live_version_dir_counts(self) -> None:
        self._make_version("6.5.1", orphaned=False)
        self.assertTrue(self._plugin_installed())

    def test_only_orphaned_dirs_do_not_count(self) -> None:
        self._make_version("6.4.0", orphaned=True)
        self._make_version("6.5.0", orphaned=True)
        self.assertFalse(self._plugin_installed())

    def test_live_dir_beside_orphans_counts(self) -> None:
        self._make_version("6.4.0", orphaned=True)
        self._make_version("6.5.1", orphaned=False)
        self.assertTrue(self._plugin_installed())

    def test_install_scripts_not_refused_for_orphans_only(self) -> None:
        self._make_version("6.4.0", orphaned=True)
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True),                 mock.patch.object(Path, "home", return_value=self.home),                 contextlib.redirect_stdout(out):
            rc = self.cli.cmd_install(["--scripts"])
        self.assertEqual(rc, 0)
        self.run.assert_called_once()  # the (patched) installer, not a refusal


class TestUpgradeArgs(_Base):
    """`upgrade` shares the bug class: it can uninstall + reinstall the plugin."""

    def test_help_emits_usage_without_side_effects(self) -> None:
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                rc, doc = self._call("cmd_upgrade", [flag])
                self.assertEqual(rc, 0)
                self.assertIn("--check-only", doc["flags"])
        self.run.assert_not_called()

    def test_unknown_flag_is_an_error(self) -> None:
        rc, doc = self._call("cmd_upgrade", ["--bogus"])
        self.assertEqual(rc, 1)
        self.assertEqual(doc["error"]["code"], "INVALID_USAGE")
        self.run.assert_not_called()

    def test_known_flags_are_not_rejected(self) -> None:
        # No `claude` on PATH here, so it stops at the explicit lookup error
        # (never at the argument check) and still must not spawn anything.
        with mock.patch("shutil.which", return_value=None):
            rc, doc = self._call("cmd_upgrade", ["--check-only", "--force"])
        self.assertEqual(rc, 1)
        self.assertNotIn("unknown_args", doc)
        self.assertIn("claude", doc["error"]["message"])
        self.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
