"""``--help`` / ``-h`` must be side-effect free for every ``audio-hooks`` subcommand.

An agent probing a subcommand for usage once ran ``audio-hooks install --help``,
which fell through to the legacy installer and rewrote ``~/.claude/settings.json``.
``tts set``, ``rate-limits set``, ``webhook set`` and ``upgrade`` shared the
flaw. The guard sits at the dispatch point (``main``), so this test walks every
subcommand the manifest lists rather than the ones someone remembered.

Everything runs in-process with ``subprocess.run`` patched, the data dir and
home pointed at empty temp directories, and the config writer replaced by a
mock, then asserts nothing was written and nothing was spawned.
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
import tempfile
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


def _tree(root: Path):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def _snapshot(root: Path):
    """Path -> (size, mtime_ns): catches a rewrite that leaves the name list unchanged."""
    out = {}
    for p in root.rglob("*"):
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return out


class TestHelpIsSideEffectFree(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        self.home = self.root / "home"
        self.data = self.root / "data"
        self.home.mkdir()
        self.data.mkdir()

        env = {
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "CLAUDE_PLUGIN_DATA": str(self.data),
            "CODEX_HOME": str(self.home / ".codex"),
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("CLAUDE_PLUGIN_ROOT", "PLUGIN_DATA", "CLAUDE_AUDIO_HOOKS_DATA", "CURSOR_VERSION"):
            os.environ.pop(name, None)

        # get_prefs() is a process-wide singleton that caches its data dir, and
        # hook_runner / user_preferences / invoker are cached in sys.modules by
        # whichever test imported them first. Without a fresh import the CLI
        # would keep resolving to that earlier directory, the temp-dir
        # assertions below would watch a directory it never touches, and a
        # regressed guard would run `logs clear` / `snooze` against it.
        for name in ("hook_runner", "user_preferences", "invoker"):
            sys.modules.pop(name, None)
        self.addCleanup(self._forget_cached_modules)
        self.cli = _load_cli()
        self.assertTrue(
            str(self.cli._prefs().data_dir).startswith(str(self.data)),
            "CLI did not pick up the temp data dir; isolation is not effective",
        )
        # Any spawned process, config write, or network call fails the test.
        self.run = self._patch("subprocess.run")
        self.popen = self._patch("subprocess.Popen")
        self.save = self._patch_obj(self.cli, "_save_config_raw")
        self.urlopen = self._patch("urllib.request.urlopen")

    @staticmethod
    def _forget_cached_modules() -> None:
        for name in ("hook_runner", "user_preferences", "invoker"):
            sys.modules.pop(name, None)
        _isolation.reset_state()

    def _patch(self, target: str):
        p = mock.patch(target)
        m = p.start()
        self.addCleanup(p.stop)
        return m

    def _patch_obj(self, obj, name: str):
        p = mock.patch.object(obj, name)
        m = p.start()
        self.addCleanup(p.stop)
        return m

    def _invoke(self, tokens, flag=None):
        out = io.StringIO()
        argv = ["audio-hooks", *tokens] + ([flag] if flag else [])
        with contextlib.redirect_stdout(out):
            rc = self.cli.main(argv)
        text = out.getvalue().strip()
        self.assertTrue(text, f"{tokens} {flag}: no output")
        return rc, json.loads(text.splitlines()[-1])

    def _manifest_commands(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cli.cmd_manifest([])
        entries = json.loads(out.getvalue())["subcommands"]
        return [e["name"].split(" ") for e in entries]

    def test_every_manifest_subcommand_help_is_inert(self) -> None:
        commands = self._manifest_commands()
        self.assertGreater(len(commands), 20, "manifest walk found suspiciously few subcommands")
        # Sub-paths ("hooks list") and the bare first token ("hooks") both.
        cases = {tuple(c) for c in commands} | {(c[0],) for c in commands}
        # The manifest's own first tokens must all be real dispatch entries.
        self.assertTrue({c[0] for c in commands} <= set(self.cli.DISPATCH))

        before_home, before_data = _tree(self.home), _tree(self.data)
        for tokens in sorted(cases):
            for flag in ("--help", "-h", "/?", "-?", "--help=1"):
                with self.subTest(command=" ".join(tokens), flag=flag):
                    rc, doc = self._invoke(tokens, flag)
                    self.assertEqual(rc, 0)
                    self.assertTrue(doc["ok"])

        self.assertEqual(_tree(self.home), before_home, "--help wrote under the home dir")
        self.assertEqual(_tree(self.data), before_data, "--help wrote under the data dir")
        self.save.assert_not_called()
        self.run.assert_not_called()
        self.popen.assert_not_called()
        self.urlopen.assert_not_called()

    def test_every_dispatch_entry_is_covered_by_the_manifest(self) -> None:
        """A subcommand missing from the manifest would escape the walk above."""
        listed = {c[0] for c in self._manifest_commands()}
        self.assertEqual(set(self.cli.DISPATCH) - listed, set())

    def test_the_cases_that_used_to_write(self) -> None:
        for tokens in (["tts", "set"], ["rate-limits", "set"], ["webhook", "set"],
                       ["set"], ["snooze"], ["logs", "clear"],
                       ["hooks", "enable", "stop"], ["backup", "restore", "latest"],
                       ["statusline", "install"], ["statusline", "codex", "apply"]):
            with self.subTest(command=" ".join(tokens)):
                rc, doc = self._invoke(tokens, "--help")
                self.assertEqual(rc, 0)
                self.assertTrue(doc["ok"])
        self.save.assert_not_called()
        self.assertEqual(_tree(self.home), [])
        self.assertEqual(_tree(self.data), [])

    def test_usage_payload_names_the_command(self) -> None:
        _, doc = self._invoke(["tts", "set"], "--help")
        self.assertEqual(doc["command"], "tts")
        self.assertTrue(any(e["name"] == "tts set" for e in doc["usage"]))

    # ---- unknown flags on state-changing subcommands -------------------------

    # (tokens, why). Each used to be ignored and the command then ran.
    REJECTED = [
        (["statusline", "install", "--dry-run"], "rewrote ~/.claude/settings.json"),
        (["statusline", "install", "anything"], "stray positional"),
        (["statusline", "uninstall", "--dry-run"], ""),
        (["statusline", "subagent", "install", "--dry-run"], ""),
        (["statusline", "subagent", "uninstall", "--x"], ""),
        (["statusline", "codex", "apply", "--bogus"], "wrote Codex config.toml"),
        (["statusline", "codex", "apply", "--preset"], "flag with no value"),
        (["statusline", "codex", "preview", "--bogus"], ""),
        (["webhook", "clear", "--dry-run"], ""),
        (["webhook", "test", "--x"], ""),
        (["webhook", "set", "--bogus", "x"], ""),
        (["webhook", "set", "--url"], "flag with no value"),
        (["logs", "clear", "--dry-run"], ""),
        (["logs", "clear", "extra"], "created logs/ before validating"),
        (["logs", "clear", "extra", "--dry-run"], "created logs/ before validating"),
        (["logs", "rotate"], "unknown logs subcommand created logs/"),
        (["logs", "TAIL"], "unknown logs subcommand created logs/"),
        (["migrate", "--dry-run"], ""),
        (["backup", "restore", "latest", "--dry-run"], ""),
        (["backup", "prune", "--dry-run"], ""),
        (["hooks", "enable", "stop", "--dry-run"], ""),
        (["hooks", "disable", "stop", "--x"], ""),
        (["hooks", "enable-only", "stop", "--x"], ""),
        (["theme", "set", "default", "--dry-run"], ""),
        (["snooze", "30m", "--dry-run"], ""),
        (["snooze", "--bogus"], ""),
        (["tts", "set", "--bogus", "x"], "wrote tts_settings.bogus"),
        (["tts", "set", "--enabled=true"], "= form was consumed as a flag"),
        (["rate-limits", "set", "--bogus", "x"], ""),
        (["set", "--bogus", "1"], "wrote a key literally named --bogus"),
        (["test", "stop", "--dry-run"], "played the sound; the flag was never read"),
        (["test", "all", "--bogus"], "ran every hook"),
        (["test", "--bogus", "stop"], ""),
        (["test", "stop", "notification"], "second hook name was ignored"),
    ]

    def test_unknown_flags_are_rejected_without_side_effects(self) -> None:
        self._invoke(["get", "audio_theme"])  # first load may create the prefs file
        before_home, before_data = _snapshot(self.home), _snapshot(self.data)
        tree_home, tree_data = _tree(self.home), _tree(self.data)  # names incl. empty directories
        for tokens, why in self.REJECTED:
            with self.subTest(command=" ".join(tokens), why=why):
                rc, doc = self._invoke(tokens)
                self.assertEqual(rc, 1)
                self.assertFalse(doc["ok"])
                self.assertEqual(doc["error"]["code"], "INVALID_USAGE")
                self.assertEqual(_tree(self.data), tree_data, "a rejected call created a directory")
        self.assertEqual(_snapshot(self.home), before_home)
        self.assertEqual(_snapshot(self.data), before_data)
        self.assertEqual(_tree(self.home), tree_home)
        self.save.assert_not_called()
        self.run.assert_not_called()
        self.popen.assert_not_called()
        self.urlopen.assert_not_called()

    def test_test_plays_only_the_named_hook(self) -> None:
        """`audio-hooks test` makes a sound, so the playback is patched out."""
        with mock.patch.object(self.cli.HR, "run_hook", return_value=0) as run_hook:
            rc, doc = self._invoke(["test", "stop"])
            self.assertEqual(rc, 0, doc)
            self.assertEqual(run_hook.call_count, 1)
            self.assertEqual(run_hook.call_args[0][0], "stop")
            run_hook.reset_mock()
            rc, doc = self._invoke(["test", "stop", "--dry-run"])
            self.assertEqual(rc, 1)
            self.assertEqual(doc["error"]["code"], "INVALID_USAGE")
            rc, doc = self._invoke(["test"])
            self.assertEqual(rc, 1)
            self.assertEqual(doc["error"]["code"], "INVALID_USAGE")
            rc, doc = self._invoke(["test", "not_a_hook"])
            self.assertEqual(doc["error"]["code"], "UNKNOWN_HOOK_TYPE")
            run_hook.assert_not_called()

    def test_bare_tts_and_rate_limits_only_display(self) -> None:
        self._invoke(["get", "audio_theme"])
        before = _snapshot(self.data)
        for tokens in (["tts"], ["tts", "set"], ["rate-limits"], ["rate-limits", "set"],
                       ["webhook", "set"]):
            with self.subTest(command=" ".join(tokens)):
                rc, doc = self._invoke(tokens)
                self.assertEqual(rc, 0)
                self.assertTrue(doc["ok"])
        self.assertEqual(_snapshot(self.data), before, "a display-only call rewrote the config or its .bak")
        self.save.assert_not_called()

    def test_documented_invocations_still_work(self) -> None:
        """Real writes (the mock is lifted): the grammar changes must not reject these."""
        with mock.patch.object(self.cli, "_save_config_raw", return_value=(True, "")) as save:
            for tokens in (
                ["tts", "set", "--enabled", "true", "--speak-assistant-message", "true"],
                ["tts", "set", "--assistant-message-max-chars", "200"],
                ["rate-limits", "set", "--five-hour-thresholds", "80,95"],
                ["webhook", "set", "--url", "https://example.invalid/h", "--format", "ntfy"],
                ["hooks", "enable", "stop"],
                ["hooks", "disable", "stop"],
                ["hooks", "enable-only", "notification", "permission_request"],
                ["theme", "set", "default"],
                ["webhook", "clear"],
            ):
                with self.subTest(command=" ".join(tokens)):
                    rc, doc = self._invoke(tokens)
                    self.assertEqual(rc, 0, doc)
                    self.assertTrue(doc["ok"])
            self.assertGreaterEqual(save.call_count, 9)

    def test_set_accepts_a_value_that_starts_with_a_dash(self) -> None:
        with mock.patch.object(self.cli, "_save_config_raw", return_value=(True, "")):
            rc, doc = self._invoke(["set", "playback_settings.debounce_ms", "-5"])
        self.assertEqual(rc, 0, doc)
        self.assertEqual(doc["new_value"], -5)

    def test_hooks_enable_and_disable_apply_every_name(self) -> None:
        saved = {}

        def fake_save(cfg):
            saved["cfg"] = json.loads(json.dumps(cfg))
            return True, ""

        with mock.patch.object(self.cli, "_save_config_raw", side_effect=fake_save):
            rc, doc = self._invoke(["hooks", "disable", "subagent_stop", "permission_denied", "task_created"])
        self.assertEqual(rc, 0, doc)
        self.assertEqual(doc["hooks"], ["subagent_stop", "permission_denied", "task_created"])
        eh = saved["cfg"]["enabled_hooks"]
        for name in ("subagent_stop", "permission_denied", "task_created"):
            self.assertIs(eh[name], False, name)
        with mock.patch.object(self.cli, "_save_config_raw", side_effect=fake_save):
            rc, doc = self._invoke(["hooks", "enable", "stop", "notification"])
        self.assertEqual(rc, 0, doc)
        self.assertIs(saved["cfg"]["enabled_hooks"]["stop"], True)
        self.assertIs(saved["cfg"]["enabled_hooks"]["notification"], True)

    def test_hooks_disable_is_all_or_nothing(self) -> None:
        rc, doc = self._invoke(["hooks", "disable", "stop", "not_a_hook", "notification"])
        self.assertEqual(rc, 1)
        self.assertEqual(doc["error"]["code"], "UNKNOWN_HOOK_TYPE")
        self.assertIn("not_a_hook", doc["error"]["message"])
        self.save.assert_not_called()

    def test_underscore_flag_spellings_still_work(self) -> None:
        with mock.patch.object(self.cli, "_save_config_raw", return_value=(True, "")) as save:
            for tokens in (
                ["tts", "set", "--speak_assistant_message", "true"],
                ["tts", "set", "--assistant_message_max_chars", "120"],
                ["rate-limits", "set", "--five_hour_thresholds", "80,95"],
                ["rate-limits", "set", "--seven_day_thresholds", "70"],
            ):
                with self.subTest(command=" ".join(tokens)):
                    rc, doc = self._invoke(tokens)
                    self.assertEqual(rc, 0, doc)
            self.assertEqual(save.call_count, 4)

    def test_rate_limit_thresholds_are_always_stored_as_a_list(self) -> None:
        saved = {}

        def fake_save(cfg):
            saved["cfg"] = json.loads(json.dumps(cfg))
            return True, ""

        for value, expected in (("90", [90]), ("80,95", [80, 95]), ("[70, 90]", [70, 90])):
            with self.subTest(value=value), mock.patch.object(self.cli, "_save_config_raw", side_effect=fake_save):
                rc, doc = self._invoke(["rate-limits", "set", "--five-hour-thresholds", value])
                self.assertEqual(rc, 0, doc)
                self.assertEqual(saved["cfg"]["rate_limit_alerts"]["five_hour_thresholds"], expected)
        rc, doc = self._invoke(["rate-limits", "set", "--five-hour-thresholds", "abc"])
        self.assertEqual(rc, 1)
        self.assertEqual(doc["error"]["code"], "INVALID_USAGE")

    def test_a_help_like_value_is_rejected_never_stored(self) -> None:
        """`set <key> --help` once wrote "--help" into the config (mode = "--help"
        silenced every hook). A leading help token is usage; one after the key is an error."""
        self._invoke(["get", "audio_theme"])
        before = _snapshot(self.data)
        for value in ("/?", "-h", "--help", "-?", "--help=1"):
            with self.subTest(value=value), \
                    mock.patch.object(self.cli, "_save_config_raw", return_value=(True, "")) as save:
                rc, doc = self._invoke(["set", "notification_settings.mode", value])
                self.assertEqual(rc, 1, doc)
                self.assertFalse(doc["ok"])
                self.assertEqual(doc["error"]["code"], "INVALID_USAGE")
                self.assertIn("help flag", doc["error"]["message"])
                save.assert_not_called()
        # also when the help token follows other tokens
        rc, doc = self._invoke(["set", "notification_settings.mode", "audio_only", "--help"])
        self.assertEqual(rc, 1)
        self.assertEqual(_snapshot(self.data), before)
        self.save.assert_not_called()
        # first position stays usage
        rc, doc = self._invoke(["set"], "--help")
        self.assertEqual(rc, 0)
        self.assertEqual(doc["command"], "set")

    def test_infinite_threshold_is_rejected_by_the_setter(self) -> None:
        rc, doc = self._invoke(["rate-limits", "set", "--five-hour-thresholds", "1e999"])
        self.assertEqual(rc, 1)
        self.assertEqual(doc["error"]["code"], "INVALID_USAGE")
        self.save.assert_not_called()

    def test_set_rejects_extra_tokens(self) -> None:
        self._invoke(["get", "audio_theme"])
        before = _snapshot(self.data)
        rc, doc = self._invoke(["set", "audio_theme", "custom", "--dry-run"])
        self.assertEqual(rc, 1)
        self.assertEqual(doc["error"]["code"], "INVALID_USAGE")
        self.assertEqual(_snapshot(self.data), before)
        self.save.assert_not_called()

    def test_snooze_documented_forms(self) -> None:
        for tokens in (["snooze", "30m"], ["snooze", "status"], ["snooze", "off"]):
            with self.subTest(command=" ".join(tokens)):
                rc, doc = self._invoke(tokens)
                self.assertEqual(rc, 0, doc)

    def test_dedicated_usage_is_kept_for_install_uninstall_upgrade(self) -> None:
        for cmd in ("install", "uninstall", "upgrade"):
            with self.subTest(command=cmd):
                _, doc = self._invoke([cmd], "--help")
                self.assertIn("usage", doc)
                self.assertIsInstance(doc["usage"], str)
                self.assertIn("flags", doc)

    def test_top_level_help_still_prints_the_manifest(self) -> None:
        rc, doc = self._invoke([], "--help")
        self.assertEqual(rc, 0)
        self.assertEqual(doc["schema"], "audio-hooks.manifest.v1")


if __name__ == "__main__":
    unittest.main()
