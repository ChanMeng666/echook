"""Tests for the native ``audio-hooks uninstall`` (legacy script install removal).

``uninstall`` used to shell out to ``scripts/uninstall.sh``. The plugin layout
does not ship ``scripts/`` (and ``PROJECT_ROOT`` is the plugin directory
precisely when ``DUAL_INSTALL_DETECTED`` is reported), and the old code did
nothing on Windows. The removal now lives in the CLI, so these tests run it
against a fabricated home -- ``Path.home`` is patched to a per-test temp dir on
top of the suite's isolated home -- and with ``PROJECT_ROOT`` pointing at a
directory that has no ``scripts/``.
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

OUR_EVENTS = [
    "Notification", "Stop", "StopFailure", "SessionStart", "SessionEnd",
    "SubagentStart", "SubagentStop", "PermissionRequest", "PermissionDenied",
    "TaskCreated", "TaskCompleted", "TeammateIdle", "PreToolUse", "PostToolUse",
    "PostToolUseFailure", "UserPromptSubmit", "PreCompact", "PostCompact",
    "ConfigChange", "InstructionsLoaded",
]


def _load_cli():
    sys.modules.pop("audio_hooks_cli", None)
    spec = importlib.util.spec_from_file_location("audio_hooks_cli", CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["audio_hooks_cli"] = module
    spec.loader.exec_module(module)
    return module


def _cmd(c):
    return {"type": "command", "command": c, "timeout": 10, "async": True}


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _load_cli()
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.home = Path(self._td.name) / "home"
        self.home.mkdir()
        self.claude = self.home / ".claude"
        self.hooks = self.claude / "hooks"
        self.hooks.mkdir(parents=True)
        # A project root with no scripts/ -- the plugin layout.
        self.root = Path(self._td.name) / "plugin-root"
        self.root.mkdir()
        for p in (mock.patch.object(Path, "home", return_value=self.home),
                  mock.patch.object(self.cli, "PROJECT_ROOT", self.root),
                  mock.patch("subprocess.run"), mock.patch("subprocess.Popen")):
            self.addCleanup(p.stop)
            p.start()
        self.run_mock = sys.modules["subprocess"].run

    def uninstall(self, args=()):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.cli.cmd_uninstall(list(args))
        return rc, json.loads(out.getvalue().strip().splitlines()[-1])

    def home_fwd(self) -> str:
        return str(self.home).replace(os.sep, "/")

    def windows_command(self, event: str) -> str:
        return f'py "{self.home_fwd()}/.claude/hooks/hook_runner.py" {event} || true'

    def write_settings(self, doc, bom=False, name="settings.json") -> bytes:
        raw = json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")
        if bom:
            raw = b"\xef\xbb\xbf" + raw
        (self.claude / name).write_bytes(raw)
        return raw

    WRAPPER = ('#!/bin/bash\n# Claude Code Stop Hook\nSCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
               'source "$SCRIPT_DIR/shared/hook_config.sh"\nget_and_play_audio "stop" "task-complete.mp3"\n')
    CONTENT = {
        "hook_runner.py": '#!/usr/bin/env python3\n"""\necho' + 'ok - Python Hook Runner\n"""\n',
        "invoker.py": '"""Invoker detection for the audio-hooks runner.\n"""\n',
        "user_preferences.py": '"""UserPreferences \u2014 single source of truth for user_preferences.json access.\n"""\n',
        "play_audio.sh": '#!/bin/bash\n# Claude Code Stop Hook - Play notification audio\n# Plays audio\n',
    }
    SHARED = {
        "hook_config.sh": "#!/bin/bash\n# echook - Shared Configuration Library\n",
        "hook_logger.sh": "#!/bin/bash\n# Hook Logger - Records all hook triggers for debugging\n",
    }

    def install_files(self) -> None:
        for n in ("hook_runner.py", "invoker.py", "user_preferences.py", "play_audio.sh"):
            (self.hooks / n).write_text(self.CONTENT[n], encoding="utf-8")
        (self.hooks / "stop_hook.sh").write_text(self.WRAPPER, encoding="utf-8")
        (self.hooks / ".project_path").write_text(self.home_fwd() + "/project", encoding="utf-8")
        (self.hooks / "shared").mkdir(exist_ok=True)
        for n, c in self.SHARED.items():
            (self.hooks / "shared" / n).write_text(c, encoding="utf-8")
        cache = self.hooks / "__pycache__"
        cache.mkdir(exist_ok=True)
        for m in ("hook_runner", "invoker", "user_preferences"):
            (cache / f"{m}.cpython-312.pyc").write_bytes(b"\0")
        self.foreign_pyc = cache / "my_other_module.cpython-312.pyc"
        self.foreign_pyc.write_bytes(b"\0")
        self.user_file = self.hooks / "my_own_hook.py"
        self.user_file.write_text("keep me", encoding="utf-8")

    def windows_install_doc(self) -> dict:
        hooks = {e: [{"hooks": [_cmd(self.windows_command(e))]}] for e in OUR_EVENTS}
        # user hooks: in the same event as ours (separate group), in other events,
        # and sharing a group with one of ours.
        hooks["Stop"].append({"hooks": [_cmd("bash ~/bin/my_stop_hook.sh")]})
        hooks["Notification"][0]["hooks"].append(_cmd("notify-send hi"))
        hooks["PreToolUse"][0]["matcher"] = "Bash"
        hooks["PreToolUse"].append({"matcher": "Edit", "hooks": [_cmd("python ~/.claude/hooks/my_own_hook.py")]})
        home_back = self.home_fwd().replace("/", "\\")
        hooks["SessionEnd"] = [{"hooks": [_cmd(
            f'py "{home_back}\\.claude\\hooks\\hook_runner.py" session_end || true')]}]
        hooks["UserPromptSubmit"].append({"hooks": [_cmd("/opt/x.claude/hooks/hook_runner.py")]})
        # ANOTHER directory's .claude/hooks must survive -- the rule is anchored to the home.
        hooks["UserPromptSubmit"].append({"hooks": [_cmd("node D:/proj/.claude/hooks/hook_runner.py")]})
        hooks["SessionStart"] = [{"hooks": [_cmd(self.windows_command("SessionStart")),
                                            _cmd("python /srv/app/.claude/hooks/stop_hook.sh")]}]
        hooks["WorktreeCreate"] = [{"hooks": [_cmd("my-worktree-provider")]}]
        hooks["CustomUserEvent"] = [{"hooks": [_cmd("echo custom")]}]
        return {
            "theme": "dark",
            "note": "caf\u00e9 \u2603",
            "permissions": {"allow": ["Bash(git status)"]},
            "hooks": hooks,
            "model": "opus",
        }


class TestNativeUninstall(_Base):
    def test_windows_shaped_install_is_removed_precisely(self) -> None:
        doc = self.windows_install_doc()
        original = self.write_settings(doc)
        self.install_files()

        rc, out = self.uninstall()
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["ok"])
        self.assertEqual(out["mode"], "scripts")
        # 20 events were ours, one with an extra backslash-path variant for SessionEnd
        # (replacing the forward-slash one): 20 entries removed in total.
        self.assertEqual(out["removed_hook_entries"], 20)

        raw = (self.claude / "settings.json").read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(raw.endswith(b"\n"))
        text = raw.decode("utf-8")
        self.assertIn("caf\u00e9 \u2603", text, "non-ASCII must be preserved, not \\u-escaped")
        after = json.loads(text)
        self.assertEqual(list(after.keys()), ["theme", "note", "permissions", "hooks", "model"])
        self.assertEqual(after["permissions"], {"allow": ["Bash(git status)"]})
        hooks = after["hooks"]

        # events that only held ours are gone
        for ev in ("StopFailure", "SessionEnd", "SubagentStart", "PostToolUse",
                   "PreCompact", "ConfigChange"):
            self.assertNotIn(ev, hooks, ev)
        # unrelated hook in a separate group of the same event survives
        self.assertEqual(hooks["Stop"], [{"hooks": [_cmd("bash ~/bin/my_stop_hook.sh")]}])
        # a user hook sharing a group with ours survives; the group keeps its other keys
        self.assertEqual(hooks["Notification"], [{"hooks": [_cmd("notify-send hi")]}])
        # matcher group of ours is dropped entirely; the user's group is untouched
        self.assertEqual(hooks["PreToolUse"],
                         [{"matcher": "Edit", "hooks": [_cmd("python ~/.claude/hooks/my_own_hook.py")]}])
        # look-alike path is not ours
        self.assertEqual(hooks["UserPromptSubmit"], [
            {"hooks": [_cmd("/opt/x.claude/hooks/hook_runner.py")]},
            {"hooks": [_cmd("node D:/proj/.claude/hooks/hook_runner.py")]},
        ])
        # a group holding ours and another project's script keeps only the other project's
        self.assertEqual(hooks["SessionStart"], [{"hooks": [_cmd("python /srv/app/.claude/hooks/stop_hook.sh")]}])
        # events outside the legacy list are never touched
        self.assertEqual(hooks["WorktreeCreate"], [{"hooks": [_cmd("my-worktree-provider")]}])
        self.assertEqual(hooks["CustomUserEvent"], [{"hooks": [_cmd("echo custom")]}])

        # files: ours gone, the user's survives, foreign bytecode keeps __pycache__ alive
        for n in ("hook_runner.py", "invoker.py", "user_preferences.py", ".project_path",
                  "stop_hook.sh", "play_audio.sh"):
            self.assertFalse((self.hooks / n).exists(), n)
        self.assertFalse((self.hooks / "shared").exists(), "shared/ held only echook's files, so it goes")
        self.assertTrue(self.user_file.exists())
        self.assertTrue(self.foreign_pyc.exists())
        self.assertEqual(sorted(p.name for p in (self.hooks / "__pycache__").iterdir()),
                         ["my_other_module.cpython-312.pyc"])
        self.assertTrue(self.hooks.is_dir())

        # backup
        backup = Path(out["backup_dir"])
        self.assertEqual(backup.parent, self.claude / "backups")
        self.assertTrue(backup.name.startswith("audio-hooks-uninstall-"))
        self.assertEqual((backup / "settings.json.backup").read_bytes(), original)
        self.assertTrue((backup / "hooks" / "hook_runner.py").exists())
        # everything deleted was backed up first (bytecode excepted)
        for n in ("hook_runner.py", "invoker.py", "user_preferences.py", ".project_path",
                  "stop_hook.sh", "play_audio.sh"):
            self.assertTrue((backup / "hooks" / n).exists(), f"{n} was deleted but not backed up")
        self.assertTrue((backup / "hooks" / "shared" / "hook_config.sh").exists())
        self.assertTrue(out["removed_files"])
        self.run_mock.assert_not_called()

    def test_pycache_is_removed_when_only_our_bytecode_is_in_it(self) -> None:
        self.install_files()
        self.foreign_pyc.unlink()
        self.write_settings({"hooks": {}})
        rc, out = self.uninstall()
        self.assertEqual(rc, 0)
        self.assertFalse((self.hooks / "__pycache__").exists())

    def test_bom_prefixed_settings_are_read_and_rewritten_without_bom(self) -> None:
        self.write_settings(self.windows_install_doc(), bom=True)
        rc, out = self.uninstall()
        self.assertEqual(rc, 0, out)
        raw = (self.claude / "settings.json").read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(out["removed_hook_entries"], 20)

    def test_corrupt_settings_change_nothing(self) -> None:
        self.install_files()
        (self.claude / "settings.json").write_text("{not json", encoding="utf-8")
        local = {"permissions": {"allow": ["Bash(~/.claude/hooks/stop_hook.sh:*)"]}}
        local_raw = self.write_settings(local, name="settings.local.json")
        before = sorted(str(p) for p in self.claude.rglob("*"))
        rc, out = self.uninstall()
        self.assertEqual(rc, 1)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "CONFIG_READ_ERROR")
        self.assertIn("Nothing was changed", out["error"]["message"])
        self.assertEqual(sorted(str(p) for p in self.claude.rglob("*")), before)
        self.assertEqual((self.claude / "settings.local.json").read_bytes(), local_raw)
        self.assertFalse((self.claude / "backups").exists())

    def test_non_object_settings_are_an_error_too(self) -> None:
        (self.claude / "settings.json").write_text("[1, 2]", encoding="utf-8")
        rc, out = self.uninstall()
        self.assertEqual(rc, 1)
        self.assertEqual(out["error"]["code"], "CONFIG_READ_ERROR")

    def test_second_run_finds_nothing_and_makes_no_backup(self) -> None:
        self.write_settings(self.windows_install_doc())
        self.install_files()
        self.assertEqual(self.uninstall()[0], 0)
        backups = sorted((self.claude / "backups").iterdir())
        settings_before = (self.claude / "settings.json").read_bytes()
        mtime = (self.claude / "settings.json").stat().st_mtime_ns

        rc, out = self.uninstall()
        self.assertEqual(rc, 0)
        self.assertTrue(out["ok"])
        self.assertTrue(out["nothing_to_remove"])
        self.assertEqual(out["removed_hook_entries"], 0)
        self.assertEqual(out["removed_files"], [])
        self.assertIsNone(out["backup_dir"])
        self.assertEqual(sorted((self.claude / "backups").iterdir()), backups)
        self.assertEqual((self.claude / "settings.json").read_bytes(), settings_before)
        self.assertEqual((self.claude / "settings.json").stat().st_mtime_ns, mtime)
        self.assertTrue(self.user_file.exists())

    def test_nothing_installed_creates_no_backup_directory(self) -> None:
        rc, out = self.uninstall()
        self.assertEqual(rc, 0)
        self.assertTrue(out["nothing_to_remove"])
        self.assertFalse((self.claude / "backups").exists())

    def test_settings_local_permissions(self) -> None:
        local = {
            "permissions": {"allow": [
                "Bash(~/.claude/hooks/stop_hook.sh:*)",
                "Bash(" + self.home_fwd().replace("/", "\\") + "\\.claude\\hooks\\hook_runner.py:*)",
                "Bash(node D:/proj/.claude/hooks/hook_runner.py)",
                "Bash(git status)",
                "Bash(bash ~/bin/my_stop_hook.sh)",
            ]},
            "z": 1, "a": 2,
        }
        self.write_settings(local, name="settings.local.json")
        rc, out = self.uninstall()
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["removed_permissions"], 2)
        after = json.loads((self.claude / "settings.local.json").read_text(encoding="utf-8"))
        self.assertEqual(after["permissions"]["allow"], [
            "Bash(node D:/proj/.claude/hooks/hook_runner.py)",
            "Bash(git status)", "Bash(bash ~/bin/my_stop_hook.sh)"])
        self.assertEqual(list(after.keys()), ["permissions", "z", "a"])

    def test_works_with_a_project_root_that_ships_no_scripts_directory(self) -> None:
        self.assertFalse((self.root / "scripts").exists())
        self.write_settings(self.windows_install_doc())
        self.install_files()
        rc, out = self.uninstall(["--scripts"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["removed_hook_entries"], 20)
        self.run_mock.assert_not_called()

    def test_temp_queue_directory_is_left_in_place(self) -> None:
        tmp = Path(os.environ["TEMP"])
        queue = tmp / "claude_audio_hooks_queue"
        queue.mkdir(exist_ok=True)
        marker = queue / "statusline.cache.live"
        marker.write_text("live plugin state", encoding="utf-8")
        self.addCleanup(lambda: marker.unlink() if marker.exists() else None)
        self.write_settings(self.windows_install_doc())
        self.install_files()
        rc, out = self.uninstall()
        self.assertEqual(rc, 0)
        self.assertTrue(marker.exists())
        self.assertIn(str(queue), out["left_in_place"])

    def test_a_failed_second_write_restores_the_first_file(self) -> None:
        main_raw = self.write_settings(self.windows_install_doc())
        self.write_settings({"permissions": {"allow": ["Bash(~/.claude/hooks/stop_hook.sh:*)"]}},
                            name="settings.local.json")
        self.install_files()
        real = self.cli._write_bytes_atomic
        calls = []

        def flaky(path, data):
            calls.append(path.name)
            if len(calls) == 2:
                raise OSError("disk full")
            return real(path, data)

        with mock.patch.object(self.cli, "_write_bytes_atomic", side_effect=flaky):
            rc, out = self.uninstall()
        self.assertEqual(rc, 1)
        self.assertFalse(out["ok"])
        self.assertIn("restored from", out["error"]["message"])
        self.assertNotIn("could NOT", out["error"]["message"])
        self.assertEqual((self.claude / "settings.json").read_bytes(), main_raw)
        self.assertTrue((self.hooks / "hook_runner.py").exists(), "no file may be deleted after a failed edit")


class TestOwnershipByContent(_Base):
    """A name alone is not proof: a user's own stop_hook.sh or shared/ must survive."""

    def test_user_owned_files_survive_beside_a_genuine_legacy_wrapper(self) -> None:
        user_stop = self.hooks / "stop_hook.sh"
        user_stop.write_text("#!/bin/bash\necho my own stop hook\n", encoding="utf-8")
        wrapper = self.hooks / "notification_hook.sh"
        wrapper.write_text(self.WRAPPER, encoding="utf-8")
        shared = self.hooks / "shared"
        shared.mkdir()
        (shared / "my_lib.sh").write_text("# mine\n", encoding="utf-8")
        (shared / "hook_logger.sh").write_text("# not the echook logger\n", encoding="utf-8")
        (shared / "hook_config.sh").write_text(self.SHARED["hook_config.sh"], encoding="utf-8")
        doc = {"hooks": {
            "Stop": [{"hooks": [_cmd("~/.claude/hooks/stop_hook.sh")]}],
            "Notification": [{"hooks": [_cmd("~/.claude/hooks/notification_hook.sh")]}],
        }}
        self.write_settings(doc)

        rc, out = self.uninstall()
        self.assertEqual(rc, 0, out)
        self.assertFalse(out["incomplete"])
        # the genuine wrapper and its registration are gone
        self.assertFalse(wrapper.exists())
        after = json.loads((self.claude / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn("Notification", after["hooks"])
        # the user's stop hook and its registration are untouched
        self.assertTrue(user_stop.exists())
        self.assertEqual(user_stop.read_text(encoding="utf-8"), "#!/bin/bash\necho my own stop hook\n")
        self.assertEqual(after["hooks"]["Stop"], [{"hooks": [_cmd("~/.claude/hooks/stop_hook.sh")]}])
        # shared/: only the genuine echook library went; the directory stays
        # a wrapper was kept (the user's stop_hook.sh), so echook's shared/ libraries stay with it
        self.assertTrue((shared / "hook_config.sh").exists())
        self.assertTrue(any("hook_config.sh" in x and "sourced by a kept" in x for x in out["left_in_place"]))
        self.assertTrue((shared / "my_lib.sh").exists())
        self.assertTrue((shared / "hook_logger.sh").exists())
        self.assertTrue(shared.is_dir())
        reported = {Path(e["path"]).name for e in out["skipped_not_ours"]}
        self.assertEqual(reported, {"stop_hook.sh", "hook_logger.sh"})
        for e in out["skipped_not_ours"]:
            self.assertIn("no echook marker", e["reason"])

    def test_user_owned_shared_directory_alone_is_never_touched(self) -> None:
        shared = self.hooks / "shared"
        shared.mkdir()
        (shared / "mine.sh").write_text("x", encoding="utf-8")
        (self.hooks / "hook_runner.py").write_text(self.CONTENT["hook_runner.py"], encoding="utf-8")
        rc, out = self.uninstall()
        self.assertEqual(rc, 0, out)
        self.assertTrue((shared / "mine.sh").exists())

    def test_an_empty_user_shared_directory_is_left_alone(self) -> None:
        (self.hooks / "shared").mkdir()
        (self.hooks / "hook_runner.py").write_text(self.CONTENT["hook_runner.py"], encoding="utf-8")
        self.assertEqual(self.uninstall()[0], 0)
        self.assertTrue((self.hooks / "shared").is_dir())

    def test_a_foreign_runner_and_its_registration_are_left_alone(self) -> None:
        (self.hooks / "hook_runner.py").write_text("print('not echook')\n", encoding="utf-8")
        (self.hooks / "invoker.py").write_text("# mine\n", encoding="utf-8")
        (self.hooks / "__pycache__").mkdir()
        keep_pyc = self.hooks / "__pycache__" / "invoker.cpython-312.pyc"
        keep_pyc.write_bytes(b"\0")
        self.write_settings({"hooks": {"Stop": [{"hooks": [_cmd(self.windows_command("stop"))]}]}})
        rc, out = self.uninstall()
        self.assertEqual(rc, 0, out)
        self.assertTrue((self.hooks / "hook_runner.py").exists())
        self.assertTrue(keep_pyc.exists(), "bytecode of a module we do not own is not ours")
        self.assertEqual(out["removed_hook_entries"], 0)
        after = json.loads((self.claude / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("Stop", after["hooks"])

    def test_a_registration_for_a_script_that_is_gone_is_stripped(self) -> None:
        self.write_settings({"hooks": {"Stop": [{"hooks": [_cmd("~/.claude/hooks/stop_hook.sh")]}]}})
        rc, out = self.uninstall()
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["removed_hook_entries"], 1)

    def test_project_path_needs_an_echook_runner_beside_it_or_a_checkout(self) -> None:
        (self.hooks / ".project_path").write_text("/somewhere/else", encoding="utf-8")
        rc, out = self.uninstall()
        self.assertEqual(rc, 0, out)
        self.assertTrue((self.hooks / ".project_path").exists())
        self.assertEqual([Path(e["path"]).name for e in out["skipped_not_ours"]], [".project_path"])
        (self.hooks / "hook_runner.py").write_text(self.CONTENT["hook_runner.py"], encoding="utf-8")
        rc, out = self.uninstall()
        self.assertFalse((self.hooks / ".project_path").exists())

    def test_markers_cover_every_name_the_cli_removes(self) -> None:
        names = set(self.cli.LEGACY_COMMAND_SCRIPTS) | set(self.cli.LEGACY_EXTRA_HOOK_FILES)
        self.assertEqual(set(self.cli.LEGACY_FILE_MARKERS) | {".project_path"}, names)

    def test_every_marker_matches_the_historical_content_it_was_derived_from(self) -> None:
        import re
        samples = {
            "hook_runner.py": ['"""\nClaude Code Audio Hooks - Python Hook Runner\n', '"""\necho' + 'ok - Python Hook Runner\n'],
            "invoker.py": ['"""Invoker detection for the audio-hooks runner.\n'],
            "user_preferences.py": ['"""UserPreferences \u2014 single source of truth for user_preferences.json access.\n'],
            "play_audio.sh": ['#!/bin/bash\n# Claude Code Stop Hook - Play notification audio\n'],
        }
        for name, texts in samples.items():
            for t in texts:
                self.assertTrue(re.search(self.cli.LEGACY_FILE_MARKERS[name], t, re.M), (name, t))
        for name in self.cli.LEGACY_COMMAND_SCRIPTS:
            if name.endswith("_hook.sh"):
                self.assertTrue(re.search(self.cli.LEGACY_FILE_MARKERS[name], self.WRAPPER, re.M), name)
        for text, name in (("# Claude Code Audio Hooks - Shared Configuration Library", "hook_config.sh"),
                           ("# echook - Shared Configuration Library", "hook_config_with_path_utils.sh"),
                           ("# Claude Code Audio Hooks - Path Utilities", "path_utils.sh"),
                           ("# echook - Path Utilities", "path_utils.sh"),
                           ("# Hook Logger - Records all hook triggers for debugging", "hook_logger.sh")):
            self.assertTrue(re.search(self.cli.LEGACY_SHARED_MARKERS[name], text + "\n", re.M), text)


class TestWriteFidelity(_Base):
    def _doc(self):
        return {"theme": "dark", "hooks": {"Stop": [{"hooks": [_cmd(self.windows_command("stop"))]}]}}

    def test_a_symlinked_settings_file_stays_a_symlink(self) -> None:
        managed = Path(self._td.name) / "dotfiles" / "settings.json"
        managed.parent.mkdir()
        managed.write_bytes(json.dumps(self._doc(), indent=2).encode("utf-8"))
        link = self.claude / "settings.json"
        try:
            link.symlink_to(managed)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available here")
        rc, out = self.uninstall()
        self.assertEqual(rc, 0, out)
        self.assertTrue(link.is_symlink(), "the link must survive")
        self.assertNotIn("hooks", json.loads(managed.read_text(encoding="utf-8")))

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits")
    def test_file_mode_is_preserved(self) -> None:
        self.write_settings(self._doc())
        path = self.claude / "settings.json"
        os.chmod(path, 0o600)
        self.assertEqual(self.uninstall()[0], 0)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_lone_surrogate_fails_before_any_write(self) -> None:
        raw_main = (json.dumps(self._doc())).encode("utf-8")
        (self.claude / "settings.json").write_bytes(raw_main)
        local = '{"permissions": {"allow": ["Bash(~/.claude/hooks/stop_hook.sh:*)", "\\ud83d"]}}'
        (self.claude / "settings.local.json").write_text(local, encoding="utf-8")
        self.install_files()
        rc, out = self.uninstall()
        self.assertEqual(rc, 1)
        self.assertIn("Nothing was changed", out["error"]["message"])
        self.assertEqual((self.claude / "settings.json").read_bytes(), raw_main)
        self.assertEqual((self.claude / "settings.local.json").read_text(encoding="utf-8"), local)
        self.assertTrue((self.hooks / "hook_runner.py").exists())
        self.assertFalse((self.claude / "backups").exists())

    def test_absurd_nesting_is_an_error_not_a_crash(self) -> None:
        (self.claude / "settings.json").write_text("[" * 5000 + "]" * 5000, encoding="utf-8")
        rc, out = self.uninstall()
        self.assertEqual(rc, 1)
        self.assertEqual(out["error"]["code"], "CONFIG_READ_ERROR")

    def test_a_failed_restore_is_reported_honestly(self) -> None:
        self.write_settings(self._doc())
        self.write_settings({"permissions": {"allow": ["Bash(~/.claude/hooks/stop_hook.sh:*)"]}},
                            name="settings.local.json")
        real = self.cli._write_bytes_atomic
        calls = []

        def flaky(path, data):
            calls.append(path.name)
            if len(calls) >= 2:  # the second write AND the restore both fail
                raise OSError("disk full")
            return real(path, data)

        with mock.patch.object(self.cli, "_write_bytes_atomic", side_effect=flaky):
            rc, out = self.uninstall()
        self.assertEqual(rc, 1)
        self.assertIn("could NOT be restored", out["error"]["message"])
        self.assertNotIn("Settings restored", out["error"]["message"])
        self.assertTrue(Path(out["backup_dir"]).is_dir())


class TestIncompleteIsDistinguishable(_Base):
    def test_unrecognised_home_spelling_keeps_the_script_and_reports_incomplete(self) -> None:
        self.install_files()
        doc = {"hooks": {
            "Stop": [{"hooks": [_cmd(r'powershell -c "& $env:USERPROFILE\.claude\hooks\hook_runner.py stop"')]}],
            "Notification": [{"hooks": [_cmd(self.windows_command("notification"))]}],
        }}
        self.write_settings(doc)
        rc, out = self.uninstall()
        self.assertEqual(rc, 1)
        self.assertFalse(out["ok"])
        self.assertTrue(out["incomplete"])
        self.assertEqual(out["error"]["code"], "UNINSTALL_INCOMPLETE")
        self.assertEqual(out["removed_hook_entries"], 1, "the recognised registration was still removed")
        self.assertEqual(len(out["unmatched_references"]), 1)
        self.assertEqual(out["unmatched_references"][0]["file"], "settings.json")
        self.assertIn("USERPROFILE", out["unmatched_references"][0]["value"])
        # the script that registration still points at is NOT deleted
        self.assertTrue((self.hooks / "hook_runner.py").exists())
        self.assertFalse((self.hooks / "stop_hook.sh").exists(), "unrelated ours-files are still removed")
        self.assertTrue(any("hook_runner.py" in x for x in out["left_in_place"]))
        self.assertTrue(out["next_steps"])

    def test_msys_path_to_the_real_hooks_dir_is_recognised_as_unmatched(self) -> None:
        self.install_files()
        drive, rest = os.path.splitdrive(str(self.home))
        if not drive:
            self.skipTest("needs a drive-letter path")
        msys = "/" + drive[0].lower() + rest.replace("\\", "/")
        self.write_settings({"hooks": {"Stop": [{"hooks": [_cmd(f"python {msys}/.claude/hooks/hook_runner.py stop")]}]}})
        # An MSYS path is only resolvable through the drive-letter rewrite.
        rc, out = self.uninstall()
        self.assertEqual(rc, 1, out)
        self.assertEqual(len(out["unmatched_references"]), 1)

    def test_another_projects_hooks_are_neither_removed_nor_reported(self) -> None:
        self.install_files()
        self.write_settings({"hooks": {"Stop": [{"hooks": [_cmd("node D:/proj/.claude/hooks/hook_runner.py")]}]}})
        rc, out = self.uninstall()
        self.assertEqual(rc, 0, out)
        self.assertFalse(out["incomplete"])
        self.assertEqual(out["unmatched_references"], [])
        self.assertEqual(out["removed_hook_entries"], 0)

    def test_a_file_that_cannot_be_deleted_makes_the_result_incomplete(self) -> None:
        self.install_files()
        self.write_settings(self.windows_install_doc())
        real_unlink = Path.unlink

        def unlink(self_path, *a, **k):
            if self_path.name == "invoker.py":
                raise PermissionError("locked")
            return real_unlink(self_path, *a, **k)

        with mock.patch.object(Path, "unlink", unlink):
            rc, out = self.uninstall()
        self.assertEqual(rc, 1)
        self.assertTrue(out["incomplete"])
        self.assertEqual(out["error"]["code"], "UNINSTALL_INCOMPLETE")
        self.assertTrue(any("invoker.py" in x and "could not be deleted" in x for x in out["left_in_place"]))
        self.assertTrue((self.hooks / "invoker.py").exists())
        self.assertFalse((self.hooks / "hook_runner.py").exists())

    def test_a_complete_run_says_incomplete_false(self) -> None:
        self.install_files()
        self.write_settings(self.windows_install_doc())
        rc, out = self.uninstall()
        self.assertEqual(rc, 0)
        self.assertIs(out["incomplete"], False)
        self.assertEqual(out["unmatched_references"], [])


class TestGoldenFixtureTable(_Base):
    """One table, run through the CLI helpers: the cases from the ownership and
    home-anchoring rules (scripts/uninstall.sh delegates to these helpers, so
    there is no second copy to drift from)."""

    def test_strip_hooks_golden(self) -> None:
        home = self.home_fwd()
        ours = lambda c: {"hooks": [_cmd(c)]}  # noqa: E731
        doc = {"hooks": {
            "Stop": [ours(f'py "{home}/.claude/hooks/hook_runner.py" stop'),
                     ours("node D:/proj/.claude/hooks/hook_runner.py"),
                     ours("bash ~/bin/my_stop_hook.sh"),
                     ours("~/.claude/hooks/stop_hook.sh")],
            "Notification": [{"hooks": [_cmd("$HOME/.claude/hooks/notification_hook.sh"), _cmd("keep")]}],
            "PreToolUse": [ours("%USERPROFILE%\\.claude\\hooks\\hook_runner.py")],
        }}
        # stop_hook.sh belongs to the user (owned() says no); the rest of ours is ours
        removed = self.cli._strip_legacy_hooks(doc, owned=lambda name: name != "stop_hook.sh")
        self.assertEqual(removed, 3)
        self.assertEqual(doc["hooks"]["Stop"], [ours("node D:/proj/.claude/hooks/hook_runner.py"),
                                                ours("bash ~/bin/my_stop_hook.sh"),
                                                ours("~/.claude/hooks/stop_hook.sh")])
        self.assertEqual(doc["hooks"]["Notification"], [{"hooks": [_cmd("keep")]}])
        self.assertNotIn("PreToolUse", doc["hooks"])

    def test_strip_permissions_golden(self) -> None:
        doc = {"permissions": {"allow": ["Bash(~/.claude/hooks/stop_hook.sh:*)", "Bash(git status)",
                                         "Bash(~/.claude/hooks/hook_runner.py)",
                                         "Bash(node D:/proj/.claude/hooks/hook_runner.py)"]}}
        removed = self.cli._strip_legacy_permissions(doc, owned=lambda name: name != "stop_hook.sh")
        self.assertEqual(removed, 1)
        self.assertEqual(doc["permissions"]["allow"], [
            "Bash(~/.claude/hooks/stop_hook.sh:*)", "Bash(git status)",
            "Bash(node D:/proj/.claude/hooks/hook_runner.py)"])


class TestKeptScriptKeepsItsDependencies(_Base):
    def _runner_with_imports(self) -> str:
        return '"""\necho' + 'ok - Python Hook Runner\n"""\nimport invoker\nimport user_preferences\n'

    def _importable(self, name: str) -> bool:
        import importlib.machinery
        return importlib.machinery.PathFinder.find_spec(name, [str(self.hooks)]) is not None

    def test_a_runner_kept_for_a_registration_still_has_its_modules(self) -> None:
        self.install_files()
        (self.hooks / "hook_runner.py").write_text(self._runner_with_imports(), encoding="utf-8")
        self.write_settings({"hooks": {"Stop": [{"hooks": [_cmd('powershell -c "& $env:USERPROFILE\\.claude\\hooks\\hook_runner.py stop"')]}]}})
        rc, out = self.uninstall()
        self.assertEqual(rc, 1)
        self.assertTrue(out["incomplete"])
        self.assertTrue((self.hooks / "hook_runner.py").exists())
        for name in ("invoker", "user_preferences"):
            self.assertTrue(self._importable(name), f"{name} was deleted but the kept runner imports it")
        self.assertTrue((self.hooks / ".project_path").exists())
        # bytecode of the kept modules stays too
        self.assertTrue((self.hooks / "__pycache__" / "invoker.cpython-312.pyc").exists())
        self.assertTrue((self.hooks / "__pycache__" / "hook_runner.cpython-312.pyc").exists())
        why = [x for x in out["left_in_place"] if "(kept:" in x]
        self.assertTrue(any("hook_runner.py" in x and "registration still points" in x for x in why))
        self.assertTrue(any("invoker.py" in x and "needed by the kept hook_runner.py" in x for x in why))
        self.assertTrue(any("user_preferences.py" in x for x in why))
        # unrelated ours-files still go
        self.assertFalse((self.hooks / "play_audio.sh").exists())

    def test_a_user_owned_runner_keeps_the_modules_installed_beside_it(self) -> None:
        self.install_files()
        (self.hooks / "hook_runner.py").write_text("print('mine')\n", encoding="utf-8")
        rc, out = self.uninstall()
        self.assertEqual(rc, 0, out)
        for name in ("invoker", "user_preferences"):
            self.assertTrue(self._importable(name))
        self.assertTrue(any("needed by the kept hook_runner.py" in x for x in out["left_in_place"]))

    def test_shared_libraries_stay_while_a_wrapper_is_kept(self) -> None:
        self.install_files()
        self.write_settings({"hooks": {"Stop": [{"hooks": [_cmd('bash "${HOME}"/.claude/hooks/stop_hook.sh')]}]}})
        rc, out = self.uninstall()
        self.assertEqual(rc, 1)
        self.assertTrue((self.hooks / "stop_hook.sh").exists())
        self.assertTrue((self.hooks / "shared" / "hook_config.sh").exists())
        self.assertTrue(any("hook_config.sh" in x and "sourced by a kept" in x for x in out["left_in_place"]))

    def test_shared_libraries_go_when_no_wrapper_is_kept(self) -> None:
        self.install_files()
        rc, out = self.uninstall()
        self.assertEqual(rc, 0, out)
        self.assertFalse((self.hooks / "shared").exists())


class TestUnmatchedForms(_Base):
    """Forms the strict rule misses but the scan must catch, so a registration never
    survives silently with its script deleted."""

    FORMS = [
        "true;~/.claude/hooks/hook_runner.py stop",
        "true&&~/.claude/hooks/hook_runner.py stop",
        "echo `~/.claude/hooks/hook_runner.py stop`",
        'bash "$HOME"/.claude/hooks/hook_runner.py stop',
        'bash "${HOME}"/.claude/hooks/hook_runner.py stop',
        "powershell -c \"& $env:USERPROFILE\\.claude\\hooks\\hook_runner.py\"",
        "%HOMEDRIVE%%HOMEPATH%\\.claude\\hooks\\hook_runner.py stop",
    ]
    NOT_HOME = [
        "./.claude/hooks/hook_runner.py",
        'bash "$CLAUDE_PROJECT_DIR"/.claude/hooks/hook_runner.py',
        "node D:/proj/.claude/hooks/hook_runner.py",
        ".claude/hooks/hook_runner.py",
        "$PROJECT_DIR/.claude/hooks/hook_runner.py",
        "/srv/app/.claude/hooks/hook_runner.py",
    ]

    def test_each_form_is_reported_and_keeps_its_script(self) -> None:
        for command in self.FORMS:
            with self.subTest(command=command):
                self.install_files()
                self.write_settings({"hooks": {"Stop": [{"hooks": [_cmd(command)]}]}})
                rc, out = self.uninstall()
                self.assertEqual(rc, 1, out)
                self.assertEqual(len(out["unmatched_references"]), 1)
                self.assertEqual(out["unmatched_references"][0]["scripts"], ["hook_runner.py"])
                self.assertTrue((self.hooks / "hook_runner.py").exists())

    def test_relative_and_foreign_prefixes_are_not_reported_from_any_cwd(self) -> None:
        for cwd in (self.home, self.root):
            for command in self.NOT_HOME:
                with self.subTest(command=command, cwd=str(cwd)):
                    old = os.getcwd()
                    os.chdir(cwd)
                    try:
                        self.install_files()
                        if cwd != self.home:  # a project-local hook the relative prefix would resolve to
                            (cwd / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)
                            (cwd / ".claude" / "hooks" / "hook_runner.py").write_text("x")
                        self.write_settings({"hooks": {"Stop": [{"hooks": [_cmd(command)]}]}})
                        rc, out = self.uninstall()
                    finally:
                        os.chdir(old)
                    self.assertEqual(rc, 0, out)
                    self.assertEqual(out["unmatched_references"], [])

    def test_a_reference_past_character_200_still_keeps_its_script(self) -> None:
        self.install_files()
        command = "echo " + "x" * 300 + " ;~/.claude/hooks/hook_runner.py stop"
        self.write_settings({"hooks": {"Stop": [{"hooks": [_cmd(command)]}]}})
        rc, out = self.uninstall()
        self.assertEqual(rc, 1, out)
        ref = out["unmatched_references"][0]
        self.assertEqual(len(ref["value"]), 200)
        self.assertNotIn("hook_runner.py", ref["value"], "the display value is truncated before the reference")
        self.assertEqual(ref["scripts"], ["hook_runner.py"])
        self.assertTrue((self.hooks / "hook_runner.py").exists(), "the script must be kept although its name is not in the display text")


class TestRemoveUnmatched(_Base):
    def _doc(self):
        return {"hooks": {
            "Stop": [{"hooks": [_cmd(r'powershell -c "& $env:USERPROFILE\.claude\hooks\hook_runner.py stop"'),
                                _cmd("keep me")]}],
            "Notification": [{"hooks": [_cmd(self.windows_command("notification"))]}],
            "CustomEvent": [{"hooks": [_cmd("true;~/.claude/hooks/hook_runner.py x")]}],
        }}

    def test_without_the_flag_nothing_changes_for_those_entries(self) -> None:
        self.install_files()
        self.write_settings(self._doc())
        rc, out = self.uninstall()
        self.assertEqual(rc, 1)
        self.assertEqual(out["error"]["code"], "UNINSTALL_INCOMPLETE")
        self.assertEqual(len(out["unmatched_references"]), 2)
        self.assertTrue((self.hooks / "hook_runner.py").exists())
        self.assertIn("audio-hooks uninstall --remove-unmatched", " ".join(out["next_steps"]))
        self.assertIn("another tool", " ".join(out["next_steps"]))
        self.assertNotIn("by hand", " ".join(out["next_steps"]) + out["error"]["hint"])

    def test_with_the_flag_the_reported_entries_and_their_scripts_are_removed(self) -> None:
        self.install_files()
        self.write_settings(self._doc())
        rc, out = self.uninstall(["--remove-unmatched"])
        self.assertEqual(rc, 0, out)
        self.assertFalse(out["incomplete"])
        self.assertEqual(out["unmatched_references"], [])
        self.assertEqual(out["removed_hook_entries"], 3)
        after = json.loads((self.claude / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(after["hooks"], {"Stop": [{"hooks": [_cmd("keep me")]}]})
        self.assertFalse((self.hooks / "hook_runner.py").exists())
        self.assertFalse((self.hooks / "invoker.py").exists())

    def test_the_flag_still_leaves_other_projects_and_user_files_alone(self) -> None:
        (self.hooks / "stop_hook.sh").write_text("#!/bin/bash\necho mine\n", encoding="utf-8")
        self.write_settings({"hooks": {"Stop": [
            {"hooks": [_cmd("node D:/proj/.claude/hooks/hook_runner.py")]},
            {"hooks": [_cmd("true;~/.claude/hooks/stop_hook.sh")]},
        ]}})
        rc, out = self.uninstall(["--remove-unmatched"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["removed_hook_entries"], 0)
        self.assertTrue((self.hooks / "stop_hook.sh").exists())

    def test_a_false_positive_variable_is_caught_by_the_flag_and_so_must_be_read_first(self) -> None:
        """The loose rule matches any variable containing HOME: that is why the result
        lists it, why next_steps says to check the list, and why the flag is opt-in."""
        self.install_files()
        self.write_settings({"hooks": {"Stop": [{"hooks": [_cmd("$XDG_CONFIG_HOME/.claude/hooks/hook_runner.py")]}]}})
        rc, out = self.uninstall()
        self.assertEqual(rc, 1)
        self.assertIn("XDG_CONFIG_HOME", out["unmatched_references"][0]["value"])
        self.assertTrue((self.hooks / "hook_runner.py").exists())
        rc, out = self.uninstall(["--remove-unmatched"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["removed_hook_entries"], 1)

    def test_the_flag_is_for_the_script_install_only(self) -> None:
        for mode in ("--cursor", "--codex", "--plugin"):
            with self.subTest(mode=mode):
                rc, out = self.uninstall([mode, "--remove-unmatched"])
                self.assertEqual(rc, 1)
                self.assertEqual(out["error"]["code"], "INVALID_USAGE")
        rc, out = self.uninstall(["--scripts", "--remove-unmatched"])
        self.assertEqual(rc, 0)

    def test_the_flag_applies_to_settings_local_permissions_too(self) -> None:
        self.install_files()
        self.write_settings({"permissions": {"allow": ['Bash("$HOME"/.claude/hooks/hook_runner.py)', "Bash(git status)"]}},
                            name="settings.local.json")
        rc, out = self.uninstall()
        self.assertEqual(rc, 1)
        rc, out = self.uninstall(["--remove-unmatched"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["removed_permissions"], 1)


class TestUnreadableAndModeDetails(_Base):
    def test_an_unreadable_file_is_not_described_as_markerless(self) -> None:
        self.install_files()
        real = self.cli._read_head

        def read_head(path, limit=8192):
            if Path(path).name == "stop_hook.sh":
                raise PermissionError("denied")
            return real(path, limit)

        with mock.patch.object(self.cli, "_read_head", read_head):
            rc, out = self.uninstall()
        entry = next(e for e in out["skipped_not_ours"] if e["path"].endswith("stop_hook.sh"))
        self.assertIn("could not be read", entry["reason"])
        self.assertNotIn("no echook marker", entry["reason"])
        self.assertTrue((self.hooks / "stop_hook.sh").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits")
    def test_the_temp_file_is_created_private(self) -> None:
        seen = {}
        real_open = os.open

        def spy(path, flags, mode=0o777, *a, **k):
            if str(path).endswith(".uninstall-tmp"):
                seen["mode"] = mode
            return real_open(path, flags, mode, *a, **k)

        self.write_settings({"hooks": {"Stop": [{"hooks": [_cmd(self.windows_command("stop"))]}]}})
        with mock.patch("os.open", spy):
            self.assertEqual(self.uninstall()[0], 0)
        self.assertEqual(seen["mode"], 0o600)


class TestMatchingRule(_Base):
    def _check(self, texts, expected, windows=False):
        with mock.patch("platform.system", return_value="Windows" if windows else "Linux"):
            for text in texts:
                with self.subTest(text=text, windows=windows):
                    self.assertIs(self.cli._is_legacy_script_ref(text), expected)

    def test_every_accepted_home_spelling(self) -> None:
        home = self.home_fwd()
        home_back = home.replace("/", "\\")
        self._check([
            "~/.claude/hooks/stop_hook.sh",
            "bash $HOME/.claude/hooks/notification_hook.sh",
            'bash "${HOME}/.claude/hooks/notification_hook.sh"',
            "%USERPROFILE%\\.claude\\hooks\\hook_runner.py",
            'py "%USERPROFILE%/.claude/hooks/hook_runner.py" stop',
            f'py "{home}/.claude/hooks/hook_runner.py" stop || true',
            f'py "{home_back}\\.claude\\hooks\\hook_runner.py" stop',
            f"test -f {home}/.claude/hooks/hook_runner.py && python3 {home}/.claude/hooks/hook_runner.py stop || true",
            "Bash(~/.claude/hooks/play_audio.sh:*)",
            f'"{home}/.claude/hooks/session_end_hook.sh"',
        ], True)

    def test_other_directories_and_look_alikes_are_not_ours(self) -> None:
        self._check([
            "node D:/proj/.claude/hooks/hook_runner.py",
            "python /srv/app/.claude/hooks/stop_hook.sh",
            "python /home/other/.claude/hooks/hook_runner.py",
            "python ~other/.claude/hooks/hook_runner.py",
            "python $HOMEDIR/.claude/hooks/hook_runner.py",
            ".claude/hooks/hook_runner.py",
            "bash ~/bin/my_stop_hook.sh",
            "python ~/.claude/hooks/my_own.py",
            "/opt/x.claude/hooks/hook_runner.py",
            "py ~/.claude/hooks/hook_runner.py.bak",
            "py ~/.claude/hooks/hook_runner.pyc",
            "~/.claude/hooks/sub/hook_runner.py",
            "notify-send stop_hook.sh",
            f"{self.home_fwd()}x/.claude/hooks/hook_runner.py",
            "",
            None,
            ["hook_runner.py"],
        ], False)

    def test_home_comparison_is_case_insensitive_on_windows_only(self) -> None:
        swapped = self.home_fwd().swapcase()
        text = f'py "{swapped}/.claude/hooks/hook_runner.py" stop'
        if swapped != self.home_fwd():
            self._check([text], True, windows=True)
            self._check([text], False, windows=False)

    def test_malformed_settings_shapes_do_not_raise(self) -> None:
        for doc in ({"hooks": []}, {"hooks": {"Stop": "x"}}, {"hooks": {"Stop": [None, 3, {"hooks": "x"}, {"hooks": [None]}]}},
                    {"permissions": {"allow": "x"}}, {"permissions": []}, {}):
            with self.subTest(doc=doc):
                self.assertEqual(self.cli._strip_legacy_hooks(json.loads(json.dumps(doc))), 0)
                self.assertEqual(self.cli._strip_legacy_permissions(json.loads(json.dumps(doc))), 0)


if __name__ == "__main__":
    unittest.main()
