"""Read-only ``audio-hooks`` subcommands must leave the home and data dirs untouched.

``audio-hooks status`` used to create ``user_preferences.json`` and ``logs/`` in
a fresh data dir, and *any* command that loaded preferences re-stamped a stored
file whose ``_version`` lagged the checkout's template (the migration save, plus
its ``.bak`` and lock file). Looking at the state therefore changed it -- and
changed it in the real user's data dir whenever a development checkout was
newer than the installed plugin.

Everything runs in-process through ``main`` (the dispatch point that decides
read-only-ness), with the home and data dir in empty temp directories and every
spawn / network call patched to fail. Two states are walked: a fresh home, and a
home whose preferences file carries an older ``_version`` (the migration case).
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "bin" / "audio-hooks.py"

# Every invocation here only reports. Keep in step with _READ_ONLY_FORMS in the CLI.
READ_ONLY = [
    ["manifest"],
    ["manifest", "--schema"],
    ["version"],
    ["status"],
    ["diagnose"],
    ["get", "audio_theme"],
    ["get", "enabled_hooks.stop"],
    ["hooks", "list"],
    ["hooks", "list", "--variants"],
    ["theme"],
    ["theme", "list"],
    ["snooze", "status"],
    ["webhook"],
    ["webhook", "set"],
    ["tts"],
    ["tts", "set"],
    ["rate-limits"],
    ["rate-limits", "set"],
    ["statusline"],
    ["statusline", "show"],
    ["statusline", "segments"],
    ["statusline", "subagent"],
    ["statusline", "subagent", "show"],
    ["statusline", "codex"],
    ["statusline", "codex", "show"],
    ["statusline", "codex", "preview"],
    ["logs", "tail"],
    ["logs", "tail", "--n", "5"],
    ["backup", "list"],
    ["backup", "show", "latest-sibling"],   # not found is fine; it must not create anything
    ["update"],
    # the help paths, for the commands that write
    ["set", "--help"],
    ["hooks", "enable", "--help"],
    ["snooze", "-h"],
    ["logs", "clear", "--help"],
    ["statusline", "install", "--help"],
]

# Every invocation here changes state on purpose. A manifest subcommand that is
# in neither list fails test_every_manifest_subcommand_is_classified, so adding
# a subcommand forces the read-only-or-not decision.
STATE_CHANGING = [
    ["set", "audio_theme", "custom"],
    ["hooks", "enable", "stop"],
    ["hooks", "disable", "stop"],
    ["hooks", "enable-only", "stop"],
    ["theme", "set", "custom"],
    ["snooze"],
    ["snooze", "30m"],
    ["snooze", "off"],
    ["webhook", "set", "--enabled", "false"],
    ["webhook", "clear"],
    ["webhook", "test"],
    ["tts", "set", "--enabled", "false"],
    ["rate-limits", "set", "--five-hour-thresholds", "90"],
    ["test"],
    ["logs", "clear"],
    ["migrate"],
    ["install"],
    ["uninstall"],
    ["upgrade"],
    ["statusline", "install"],
    ["statusline", "uninstall"],
    ["statusline", "subagent", "install"],
    ["statusline", "subagent", "uninstall"],
    ["statusline", "codex", "apply"],
    ["backup", "restore", "latest"],
    ["backup", "prune"],
]

OLD_PREFS = {
    "_version": "5.0.0",
    "audio_theme": "custom",
    "enabled_hooks": {"stop": False},
}


def _load_cli():
    sys.modules.pop("audio_hooks_cli", None)
    spec = importlib.util.spec_from_file_location("audio_hooks_cli", CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["audio_hooks_cli"] = module
    spec.loader.exec_module(module)
    return module


def _snapshot(*roots: Path):
    """Every path (directories included) -> content hash, so a creation, a
    removal and a same-size rewrite are all visible."""
    out = {}
    for root in roots:
        for p in sorted(root.rglob("*")):
            key = str(p)
            out[key] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "<dir>"
    return out


class _Base(unittest.TestCase):
    older_prefs = False
    patch_spawn = True

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        self.home = self.root / "home"
        self.data = self.root / "data"
        self.home.mkdir()
        if self.older_prefs:
            self.data.mkdir()
            (self.data / "user_preferences.json").write_text(json.dumps(OLD_PREFS), encoding="utf-8")
        env = {
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "CLAUDE_PLUGIN_DATA": str(self.data),
            "CODEX_HOME": str(self.home / ".codex"),
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("CLAUDE_PLUGIN_ROOT", "PLUGIN_DATA", "CLAUDE_AUDIO_HOOKS_DATA", "CURSOR_VERSION",
                     "CLAUDE_PLUGIN_OPTION_AUDIO_THEME", "CLAUDE_PLUGIN_OPTION_WEBHOOK_URL"):
            os.environ.pop(name, None)
        for name in ("hook_runner", "user_preferences", "invoker"):
            sys.modules.pop(name, None)
        self.addCleanup(self._forget_cached_modules)
        self.cli = _load_cli()
        self.assertTrue(str(self.cli._prefs().data_dir).startswith(str(self.data)),
                        "CLI did not pick up the temp data dir; isolation is not effective")
        if not self.patch_spawn:
            return
        # A read-only command spawns nothing and talks to no one.
        for attr, target in (("run", "subprocess.run"), ("Popen", "subprocess.Popen"),
                             ("urlopen", "urllib.request.urlopen")):
            p = mock.patch(target)
            setattr(self, attr, p.start())
            self.addCleanup(p.stop)

    @staticmethod
    def _forget_cached_modules() -> None:
        for name in ("hook_runner", "user_preferences", "invoker"):
            sys.modules.pop(name, None)
        _isolation.reset_state()

    def _invoke(self, tokens):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.cli.main(["audio-hooks", *tokens])
        text = out.getvalue().strip()
        self.assertTrue(text, f"{tokens}: no output")
        return rc, json.loads(text.splitlines()[-1])


class TestReadOnlyCommandsFreshHome(_Base):
    def test_read_only_commands_leave_home_and_data_dir_untouched(self) -> None:
        before = _snapshot(self.root)
        for tokens in READ_ONLY:
            with self.subTest(command=" ".join(tokens)):
                rc, doc = self._invoke(tokens)
                if tokens != ["manifest", "--schema"]:  # the schema document has no ok flag
                    self.assertIn("ok", doc)
                if tokens[:2] != ["backup", "show"]:
                    self.assertEqual(rc, 0, doc)
                self.assertEqual(_snapshot(self.root), before,
                                 f"`audio-hooks {' '.join(tokens)}` changed the home or data dir")
        self.assertFalse(self.data.exists(), "the data dir was created")
        self.run.assert_not_called()
        self.Popen.assert_not_called()
        self.urlopen.assert_not_called()

    def test_a_fresh_home_reports_template_defaults(self) -> None:
        rc, doc = self._invoke(["get", "audio_theme"])
        self.assertEqual(rc, 0)
        self.assertEqual(doc["value"], "default")
        rc, doc = self._invoke(["status"])
        self.assertEqual(rc, 0)
        self.assertGreater(doc["enabled_hook_count"], 0)
        self.assertFalse(self.data.exists())

    def test_state_changing_commands_still_initialise(self) -> None:
        """The other half of the contract: a write still creates the file."""
        rc, doc = self._invoke(["hooks", "disable", "stop"])
        self.assertEqual(rc, 0, doc)
        prefs = json.loads((self.data / "user_preferences.json").read_text(encoding="utf-8"))
        self.assertIs(prefs["enabled_hooks"]["stop"], False)


class TestReadOnlyCommandsRealProcess(_Base):
    """Real processes (no mocks): nothing in-process -- a cached singleton, a
    patched mkdir -- can be hiding a write the CLI performs on its own."""
    patch_spawn = False

    def test_reporting_commands_are_inert(self) -> None:
        env = {k: v for k, v in os.environ.items()
               if k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "PATHEXT", "COMSPEC")}
        env.update(HOME=str(self.home), USERPROFILE=str(self.home),
                   CLAUDE_PLUGIN_DATA=str(self.data), CODEX_HOME=str(self.home / ".codex"))
        before = _snapshot(self.root)
        for tokens in (["status"], ["diagnose"], ["hooks", "list"], ["logs", "tail"], ["snooze", "status"]):
            with self.subTest(command=" ".join(tokens)):
                r = subprocess.run([sys.executable, str(CLI), *tokens], env=env,
                                   capture_output=True, text=True, timeout=120)
                self.assertEqual(r.returncode, 0, r.stderr[-300:])
                self.assertEqual(_snapshot(self.root), before)


class TestReadOnlyCommandsRealProcessOlderPreferences(TestReadOnlyCommandsRealProcess):
    older_prefs = True


class TestReadOnlyCommandsOlderPreferences(_Base):
    older_prefs = True

    def test_read_only_commands_do_not_migrate_or_restamp(self) -> None:
        before = _snapshot(self.root)
        raw_before = (self.data / "user_preferences.json").read_bytes()
        for tokens in READ_ONLY:
            with self.subTest(command=" ".join(tokens)):
                rc, doc = self._invoke(tokens)
                if tokens[:2] != ["backup", "show"]:
                    self.assertEqual(rc, 0, doc)
                self.assertEqual(_snapshot(self.root), before,
                                 f"`audio-hooks {' '.join(tokens)}` changed the home or data dir")
        self.assertEqual((self.data / "user_preferences.json").read_bytes(), raw_before)
        self.assertFalse((self.data / "user_preferences.json.bak").exists())
        self.assertFalse((self.data / ".user_prefs.lock").exists())

    def test_reports_merge_the_stored_file_over_the_defaults(self) -> None:
        """No migration on disk does not mean a stale view: new keys appear in
        memory, and the user's own values win."""
        rc, doc = self._invoke(["get", "audio_theme"])
        self.assertEqual(doc["value"], "custom")
        rc, doc = self._invoke(["get", "_version"])
        self.assertNotEqual(doc["value"], OLD_PREFS["_version"])
        rc, doc = self._invoke(["get", "enabled_hooks.stop"])
        self.assertIs(doc["value"], False)
        rc, doc = self._invoke(["get", "notification_settings.mode"])
        self.assertIsNotNone(doc["value"], "a key added after the stored version is missing from the report")
        rc, doc = self._invoke(["hooks", "list"])
        stop = next(h for h in doc["hooks"] if h["name"] == "stop")
        self.assertFalse(stop["enabled"])

    def test_a_write_still_migrates(self) -> None:
        rc, doc = self._invoke(["theme", "set", "default"])
        self.assertEqual(rc, 0, doc)
        prefs = json.loads((self.data / "user_preferences.json").read_text(encoding="utf-8"))
        self.assertNotEqual(prefs["_version"], OLD_PREFS["_version"])
        self.assertEqual(prefs["audio_theme"], "default")


class TestClassification(_Base):
    def test_state_changing_invocations_are_not_read_only(self) -> None:
        for tokens in STATE_CHANGING:
            with self.subTest(command=" ".join(tokens)):
                self.assertFalse(self.cli._is_read_only_invocation(tokens[0], tokens[1:]))

    def test_read_only_invocations_are_classified_read_only(self) -> None:
        for tokens in READ_ONLY:
            with self.subTest(command=" ".join(tokens)):
                self.assertTrue(self.cli._is_read_only_invocation(tokens[0], tokens[1:]))

    def test_every_manifest_subcommand_is_classified(self) -> None:
        """A subcommand that is in neither list above has had no one decide."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cli.cmd_manifest([])
        names = [e["name"] for e in json.loads(out.getvalue())["subcommands"]]
        known = {" ".join(t[:2]) for t in READ_ONLY + STATE_CHANGING}
        known |= {" ".join(t[:3]) for t in READ_ONLY + STATE_CHANGING}
        known |= {t[0] for t in READ_ONLY + STATE_CHANGING}
        for name in names:
            # "statusline subagent show|install|uninstall" lists three forms in one entry
            forms = [name]
            if "|" in name:
                head, tail = name.rsplit(" ", 1)
                forms = [f"{head} {t}" for t in tail.split("|")]
            for form in forms:
                with self.subTest(subcommand=form):
                    self.assertIn(form, known, f"{form!r} is neither in READ_ONLY nor STATE_CHANGING")

    def test_the_saver_refuses_during_a_read_only_invocation(self) -> None:
        with mock.patch.object(self.cli, "_READ_ONLY", True):
            ok, err = self.cli._save_config_raw({"audio_theme": "custom"})
        self.assertFalse(ok)
        self.assertIn("read-only", err)
        self.assertFalse(self.data.exists())

    def test_the_flag_does_not_outlive_the_invocation(self) -> None:
        self._invoke(["status"])
        self.assertFalse(self.cli._READ_ONLY)
        self._invoke(["hooks", "disable", "stop"])
        self.assertFalse(self.cli._READ_ONLY)


if __name__ == "__main__":
    unittest.main()
