"""A pre-7.0 script install keeps working after ``hook_runner.py`` was split.

Such an install has exactly three Python files in ``~/.claude/hooks/`` --
``hook_runner.py``, ``user_preferences.py``, ``invoker.py`` -- plus
``.project_path`` naming the checkout. Its self-update copies only the new
``hook_runner.py`` over the installed one, so the modules that file was split
into are never beside it. The runner therefore reads ``.project_path`` before
importing them and puts ``<checkout>/hooks`` on ``sys.path`` after its own
directory: the siblings come from the checkout, ``user_preferences`` and
``invoker`` still from the installed copies.

These tests build that exact layout in a temp home and run the installed
runner as a subprocess. The data dir is pinned with ``_isolation.pin_data_dir``,
which snoozes it, so the runner imports everything, runs its gates and stops
before any sound, toast or speech.
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "hooks"
INSTALLED_FILES = ("hook_runner.py", "user_preferences.py", "invoker.py")

# Prints where each module was loaded from, without running a hook.
PROBE = (
    "import importlib.util, json, sys\n"
    "spec = importlib.util.spec_from_file_location('hook_runner', sys.argv[1])\n"
    "mod = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(mod)\n"
    "names = ['runner_core', 'platform_shell', 'audio_playback', 'notification_context',\n"
    "         'desktop_notification', 'text_to_speech', 'webhook_dispatch', 'rate_limit_alerts',\n"
    "         'terminal_sequence', 'user_preferences', 'invoker']\n"
    "print(json.dumps({'files': {n: sys.modules[n].__file__ for n in names},\n"
    "                  'project_dir': str(mod.PROJECT_DIR), 'path': sys.path}))\n"
)


class _ScriptInstall(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="echook-script-install-")).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.home = self.root / "home"
        self.installed = self.home / ".claude" / "hooks"
        self.installed.mkdir(parents=True)
        for name in INSTALLED_FILES:
            shutil.copy2(HOOKS / name, self.installed / name)
        self.state = self.root / "data"
        self.runner = self.installed / "hook_runner.py"

    def record(self, text: str, bom: bool = False) -> None:
        (self.installed / ".project_path").write_text(text, encoding="utf-8-sig" if bom else "utf-8")

    def env(self) -> Dict[str, str]:
        env = os.environ.copy()
        for k in ("CLAUDE_PLUGIN_DATA", "PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT",
                  "CLAUDE_AUDIO_HOOKS_DATA", "CURSOR_VERSION", "CLAUDE_HOOKS_DEBUG",
                  "CLAUDE_AUDIO_HOOKS_PROJECT", "PYTHONPATH"):
            env.pop(k, None)
        env["HOME"] = env["USERPROFILE"] = str(self.home)
        env["PYTHONIOENCODING"] = "utf-8"
        return _isolation.pin_data_dir(env, self.state)

    def run_hook(self, hook: str = "stop") -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.runner), hook],
            input=json.dumps({"session_id": "legacy", "hook_event_name": "Stop"}),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=self.env(), cwd=str(self.root), timeout=60,
        )

    def probe(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", PROBE, str(self.runner)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=self.env(), cwd=str(self.root), timeout=60,
        )

    def events(self) -> List[dict]:
        log = self.state / "logs" / "events.ndjson"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestInstalledRunnerRuns(_ScriptInstall):
    def test_layout_is_the_script_install_layout(self) -> None:
        self.record(str(REPO))
        present = sorted(p.name for p in self.installed.iterdir())
        self.assertEqual(present, sorted(INSTALLED_FILES + (".project_path",)))

    def test_one_event_imports_and_completes(self) -> None:
        self.record(str(REPO))
        proc = self.run_hook("stop")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        self.assertEqual(proc.stderr, "")
        self.assertEqual(proc.stdout, "")
        rows = [(e["hook"], e["status"]) for e in self.events() if e.get("action") == "hook_status"]
        self.assertEqual(rows, [("stop", "SNOOZED")])

    def test_a_variant_argument_resolves(self) -> None:
        self.record(str(REPO))
        proc = self.run_hook("notification_idle_prompt")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        rows = [(e["hook"], e["status"]) for e in self.events() if e.get("action") == "hook_status"]
        self.assertEqual(rows, [("notification", "SNOOZED")])

    def test_project_path_with_a_bom_and_trailing_newline(self) -> None:
        self.record(str(REPO) + "\n", bom=True)
        proc = self.run_hook("stop")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        self.assertEqual(len(self.events()), 1)

    @unittest.skipUnless(os.name == "nt", "MSYS-style drive paths are a Windows form")
    def test_project_path_in_git_bash_form(self) -> None:
        text = str(REPO)
        self.record("/" + text[0].lower() + text[2:].replace("\\", "/"))
        proc = self.run_hook("stop")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        self.assertEqual(len(self.events()), 1)


class TestWhichCopiesLoad(_ScriptInstall):
    def test_siblings_from_the_checkout_and_the_two_installed_modules_stay(self) -> None:
        self.record(str(REPO))
        proc = self.probe()
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        out = json.loads(proc.stdout)
        for name, loaded in out["files"].items():
            parent = Path(loaded).resolve().parent
            expected = self.installed if name in ("user_preferences", "invoker") else HOOKS
            with self.subTest(module=name):
                self.assertEqual(parent, expected.resolve())
        self.assertEqual(Path(out["project_dir"]).resolve(), REPO)
        # The checkout comes directly after the installed directory, never before it.
        path = [str(Path(p).resolve()) if p else p for p in out["path"]]
        self.assertEqual(path.index(str(HOOKS.resolve())), path.index(str(self.installed.resolve())) + 1)

    def test_a_missing_installed_invoker_falls_back_to_the_checkout(self) -> None:
        """Installs older than 5.2 never had ``invoker.py``."""
        self.record(str(REPO))
        (self.installed / "invoker.py").unlink()
        proc = self.probe()
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        files = json.loads(proc.stdout)["files"]
        self.assertEqual(Path(files["invoker"]).resolve().parent, HOOKS.resolve())
        self.assertEqual(Path(files["user_preferences"]).resolve().parent, self.installed.resolve())


class TestBootstrapStaysOutOfTheWay(_ScriptInstall):
    def test_an_in_place_runner_adds_nothing_to_sys_path(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-c", PROBE, str(HOOKS / "hook_runner.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=self.env(), cwd=str(self.root), timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        out = json.loads(proc.stdout)
        hooks_entries = [p for p in out["path"] if p and Path(p).name == "hooks"]
        self.assertEqual([str(Path(p).resolve()) for p in hooks_entries], [str(HOOKS.resolve())])
        for name, loaded in out["files"].items():
            self.assertEqual(Path(loaded).resolve().parent, HOOKS.resolve(), name)

    def test_a_recorded_path_without_the_modules_is_not_added(self) -> None:
        """An empty, missing or wrong ``.project_path`` must not put anything on
        ``sys.path``; the import then fails by name instead of loading a stray file."""
        elsewhere = self.root / "elsewhere"
        (elsewhere / "hooks").mkdir(parents=True)
        for recorded in (str(elsewhere), "", "   ", str(self.root / "gone")):
            with self.subTest(recorded=recorded):
                self.record(recorded)
                proc = self.run_hook("stop")
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn("No module named 'runner_core'", proc.stderr)
                self.assertEqual(self.events(), [])


if __name__ == "__main__":
    unittest.main()
