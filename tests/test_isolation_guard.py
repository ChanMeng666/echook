"""Guards the suite-level isolation in ``tests/_isolation.py``.

If this fails, the suite can read or write the developer's real
``~/.claude/plugins/data/<id>/`` -- see the module docstring there for how that
went unnoticed.
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import os
import platform
import re
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


class TestIsolation(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(os.environ["ECHOOK_TESTS_ISOLATION_ROOT"])

    def test_home_is_a_throwaway_tree(self) -> None:
        self.assertTrue(_inside(Path.home(), self.root), "Path.home() escaped isolation")
        real = Path(os.environ["ECHOOK_TESTS_REAL_HOME"])
        self.assertNotEqual(Path.home().resolve(), real.resolve(),
                            "tests are running against the real home directory")

    def test_temp_and_plugin_data_are_throwaway(self) -> None:
        self.assertTrue(_inside(Path(tempfile.gettempdir()), self.root))
        # CLAUDE_PLUGIN_DATA outranks every other data-dir variable, so a stale
        # value from the developer's shell would defeat the whole guard.
        plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
        self.assertTrue(plugin_data is None or _inside(Path(plugin_data), self.root))
        self.assertTrue(_inside(Path(os.environ["CLAUDE_AUDIO_HOOKS_DATA"]), self.root))

    def test_editor_markers_are_cleared(self) -> None:
        # assertFalse(name in ...) rather than assertNotIn(name, os.environ): the
        # latter prints the whole environment on failure.
        for name in ("CURSOR_VERSION", "CLAUDE_PLUGIN_ROOT", "PLUGIN_DATA"):
            self.assertFalse(name in os.environ, f"{name} leaked into the test environment")

    def test_prefs_singleton_resolves_inside_the_tree_even_without_the_env_var(self) -> None:
        """Several tests pop the data-dir variables in teardown; the home dir is the real guard."""
        import sys
        hooks = str(TESTS.parent / "hooks")
        if hooks not in sys.path:
            sys.path.insert(0, hooks)
        import user_preferences  # noqa: E402

        saved = {k: os.environ.pop(k, None)
                 for k in ("CLAUDE_PLUGIN_DATA", "CLAUDE_AUDIO_HOOKS_DATA", "PLUGIN_DATA")}
        try:
            _isolation.reset_state()
            data_dir = user_preferences.get_prefs(TESTS.parent).data_dir
            if platform.system() == "Windows":
                self.assertTrue(_inside(data_dir, self.root), f"data dir escaped isolation: {data_dir}")
            else:
                # The legacy fallback is a literal /tmp path on POSIX; the real
                # ~/.claude data dir is what must never be chosen.
                self.assertFalse(_inside(data_dir, Path(os.environ["ECHOOK_TESTS_REAL_HOME"])))
        finally:
            os.environ.update({k: v for k, v in saved.items() if v is not None})
            _isolation.reset_state()

    def test_spawned_hook_runners_always_get_an_isolated_data_dir(self) -> None:
        """A runner started without CLAUDE_AUDIO_HOOKS_DATA falls back, on POSIX, to
        the literal /tmp/claude_audio_hooks_queue (a legacy install's real data dir).
        Both runner-spawning helpers must pin the variable, with or without a state_dir."""
        from unittest import mock
        import subprocess
        try:
            import test_codex_hooks as codex_tests
            import test_cursor_bridge as cursor_tests
        except ImportError:
            from tests import test_codex_hooks as codex_tests
            from tests import test_cursor_bridge as cursor_tests
        fake = mock.Mock(returncode=0, stdout="", stderr="")
        for module in (codex_tests, cursor_tests):
            for state_dir in (None, Path(tempfile.mkdtemp(dir=os.environ["TEMP"]))):
                with self.subTest(helper=module.__name__, state_dir=state_dir),                         mock.patch.object(subprocess, "run", return_value=fake) as run:
                    module._run_hook("stop", state_dir=state_dir)
                    pinned = run.call_args.kwargs["env"].get("CLAUDE_AUDIO_HOOKS_DATA")
                    self.assertTrue(pinned, "the runner would resolve its own data dir")
                    self.assertTrue(_inside(Path(pinned), self.root), pinned)
                    if state_dir is not None:
                        self.assertEqual(Path(pinned), state_dir)
                    self.assertTrue((Path(pinned) / "queue" / "snooze_until").exists()
                                    or Path(pinned).name == "claude_audio_hooks_queue",
                                    "the real runner would play audio")

    def test_every_runner_spawning_test_module_uses_the_pin(self) -> None:
        """A new helper that starts hook_runner.py must go through pin_data_dir.

        Heuristic: a module whose argv puts the runner path right after the
        interpreter (``[sys.executable, str(HOOK_RUNNER), ...]``) is a spawner."""
        spawner = re.compile(r"sys\.executable,\s*str\(\w*(?:HOOK_)?RUNNER\w*\)")
        offenders = []
        for p in sorted(TESTS.glob("test_*.py")):
            if p.name == Path(__file__).name:
                continue
            text = p.read_text(encoding="utf-8")
            if spawner.search(text) and "pin_data_dir" not in text:
                offenders.append(p.name)
        self.assertEqual(offenders, [], "these start the hook runner without pinning its data dir")
        spawners = [p.name for p in TESTS.glob("test_*.py") if spawner.search(p.read_text(encoding="utf-8"))]
        self.assertGreaterEqual(len(spawners), 2, "the heuristic no longer finds the known spawners")

    def test_every_test_module_imports_the_isolation(self) -> None:
        pattern = re.compile(r"^\s*(import _isolation|from tests import _isolation)", re.M)
        missing = [p.name for p in sorted(TESTS.glob("test_*.py"))
                   if not pattern.search(p.read_text(encoding="utf-8"))]
        self.assertEqual(missing, [], "these test modules would run un-isolated if launched alone")

    def test_a_stale_inherited_root_is_not_trusted(self) -> None:
        """If ECHOOK_TESTS_ISOLATION_ROOT names a directory this process is not
        actually inside, activate() must build a fresh tree instead of returning."""
        import subprocess
        import sys
        import tempfile as _tf
        with _tf.TemporaryDirectory() as stray:
            env = dict(os.environ, ECHOOK_TESTS_ISOLATION_ROOT=stray)
            code = (
                "import sys; sys.path.insert(0, %r); import _isolation, os; from pathlib import Path; "
                "root = Path(os.environ['ECHOOK_TESTS_ISOLATION_ROOT']); "
                "print(root); print(Path.home())"
            ) % str(TESTS)
            proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                                  text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        root, home = proc.stdout.strip().splitlines()[:2]
        self.assertNotEqual(Path(root).resolve(), Path(stray).resolve(), "stale root was trusted")
        self.assertTrue(_inside(Path(home), Path(root)))

    def test_restore_baseline_repairs_a_deleted_variable_and_names_the_culprit(self) -> None:
        saved = os.environ.pop("CLAUDE_AUDIO_HOOKS_DATA")
        marker = ("test_isolation_guard.deliberate", "CLAUDE_AUDIO_HOOKS_DATA")
        try:
            _isolation.restore_baseline(marker[0])
            self.assertEqual(os.environ.get("CLAUDE_AUDIO_HOOKS_DATA"), saved)
            self.assertIn(marker, _isolation.REPAIRS)
        finally:
            os.environ["CLAUDE_AUDIO_HOOKS_DATA"] = saved
            # this repair was deliberate; don't let test_zzz_isolation_final blame it
            while marker in _isolation.REPAIRS:
                _isolation.REPAIRS.remove(marker)

    def test_the_unittest_hook_repairs_before_the_next_test(self) -> None:
        import unittest as _ut

        class _Victim(_ut.TestCase):
            seen = None

            def test_it(self):
                type(self).seen = os.environ.get("CLAUDE_AUDIO_HOOKS_DATA")

        saved = os.environ.pop("CLAUDE_AUDIO_HOOKS_DATA")
        try:
            _ut.TextTestRunner(stream=open(os.devnull, "w")).run(_ut.defaultTestLoader.loadTestsFromTestCase(_Victim))
            self.assertEqual(_Victim.seen, saved)
        finally:
            os.environ["CLAUDE_AUDIO_HOOKS_DATA"] = saved
            _isolation.REPAIRS[:] = [r for r in _isolation.REPAIRS if "_Victim" not in r[0]]

    def test_leaked_cleared_variables_are_repaired_and_reported(self) -> None:
        """CLAUDE_PLUGIN_DATA outranks every data-dir variable; PLUGIN_DATA and
        CURSOR_VERSION steer the runner. Left set outside the tree they must be
        popped before the next test and named in REPAIRS."""
        outside = os.environ["ECHOOK_TESTS_REAL_HOME"]
        names = ("CLAUDE_PLUGIN_DATA", "PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT", "CURSOR_VERSION")
        saved = {k: os.environ.get(k) for k in names}
        marker_who = "test_isolation_guard.leaked"
        try:
            for k in names:
                os.environ[k] = outside if k != "CURSOR_VERSION" else "3.2.16"
            _isolation.restore_baseline(marker_who)
            for k in names:
                self.assertFalse(k in os.environ, f"{k} was left set")
                self.assertIn((marker_who, k), _isolation.REPAIRS)
            # a path inside the tree (a test's own temp dir) is not a leak
            os.environ["CLAUDE_PLUGIN_DATA"] = str(self.root / "somewhere")
            before = len(_isolation.REPAIRS)
            _isolation.restore_baseline(marker_who)
            self.assertEqual(len(_isolation.REPAIRS), before)
            self.assertTrue("CLAUDE_PLUGIN_DATA" in os.environ)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            _isolation.REPAIRS[:] = [r for r in _isolation.REPAIRS if r[0] != marker_who]


if __name__ == "__main__":
    unittest.main()
