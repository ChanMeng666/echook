"""Suite-level isolation: no test may read or write the real home or data dir.

Why this exists. ``user_preferences.get_prefs()`` is a process-wide singleton
that caches its data dir on first use. Tests that exercise the hook runner
(``test_cursor_bridge`` ...) resolved it to the developer's real
``~/.claude/plugins/data/<id>/`` early in ``unittest discover``, after which a
per-test ``CLAUDE_PLUGIN_DATA`` override was silently ignored: the suite
rewrote the real ``user_preferences.json`` (stamping the worktree's version
over the live plugin's) and appended ``webhook_dispatched`` lines to the real
``logs/events.ndjson``. Several tests also ``os.environ.pop("CLAUDE_PLUGIN_DATA")``
in teardown, so an env var alone cannot be the guard -- the home directory is.
Even the variables this module sets can be deleted by a test (one did, at
``test_statusline.TestGitDirty.tearDown``), so :func:`restore_baseline` repairs
them before every test and class fixture and records the culprit.

What it does, once per process, at import time (which for ``unittest discover``
is before any test runs): points HOME / USERPROFILE / HOMEDRIVE+HOMEPATH /
TEMP / TMP / TMPDIR / CODEX_HOME and ``CLAUDE_AUDIO_HOOKS_DATA`` at a throwaway
tree, clears ``CLAUDE_PLUGIN_DATA`` (which outranks the other data-dir
variables, so leaving a developer's value in place would defeat the guard, and
setting one would change the precedence tests such as ``test_statusline`` rely
on), clears the variables that make the code believe it runs inside an editor,
and drops any cached prefs/invoker state. Subprocesses spawned by tests inherit
it. The tree is removed at exit.

Every ``tests/test_*.py`` imports this module as its first import, so a single
module run (``python -m unittest tests.test_x``) is isolated too;
``test_isolation_guard.py`` fails if a test module forgets to.

Tests that swap data dirs themselves must call :func:`reset_state` (setUp and
cleanup) so the singleton and invoker cache follow them.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

_ROOT_VAR = "ECHOOK_TESTS_ISOLATION_ROOT"
_REAL_HOME_VAR = "ECHOOK_TESTS_REAL_HOME"
_CLEARED = (
    "CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT", "PLUGIN_DATA",
    "CURSOR_VERSION", "CLAUDE_CONFIG_DIR", "CLAUDE_AUDIO_HOOKS_PROJECT",
)


# The cleared variables that hold paths (a value inside the tree is harmless;
# one outside it is not). CURSOR_VERSION is handled separately: any value leaks.
_CLEARED_PATHLIKE = (
    "CLAUDE_PLUGIN_DATA", "PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT",
    "CLAUDE_CONFIG_DIR", "CLAUDE_AUDIO_HOOKS_PROJECT",
)


def reset_state() -> None:
    """Drop the prefs singleton and the invoker cache, if those modules are loaded."""
    hooks_dir = str(Path(__file__).resolve().parent.parent / "hooks")
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)
    for mod_name, fn in (("user_preferences", "_reset_prefs"), ("invoker", "_reset_cache")):
        mod = sys.modules.get(mod_name)
        if mod is not None:
            getattr(mod, fn)()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _baseline_for(root: Path) -> dict:
    home = root / "home"
    tmp = root / "tmp"
    return {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "TEMP": str(tmp),
        "TMP": str(tmp),
        "TMPDIR": str(tmp),
        "CODEX_HOME": str(home / ".codex"),
        "CLAUDE_AUDIO_HOOKS_DATA": str(root / "data"),
    }


# Filled in by activate(). REPAIRS records every time a test (or fixture) left
# one of the protected variables missing or pointing outside the tree, which
# test_zzz_isolation_final turns into a failure that names the culprit.
_BASELINE: dict = {}
_ROOT = None
REPAIRS: list = []


def restore_baseline(who: str = "") -> None:
    """Put back any protected variable a test deleted or pointed outside the tree.

    Several tests tear down with ``os.environ.pop("CLAUDE_AUDIO_HOOKS_DATA")``.
    That removes the suite-level value for every later module, and on POSIX the
    fallback is then the literal ``/tmp/claude_audio_hooks_queue`` -- a legacy
    script install's real data dir. An env var the suite relies on cannot be one
    the tests are free to delete, so this runs before every test and every
    class fixture. A value that is merely *changed* but still inside the tree
    (a test pointing the variable at its own temp dir) is left alone.
    """
    if _ROOT is None:
        return
    for key, value in _BASELINE.items():
        cur = os.environ.get(key)
        if cur is None or not _inside(Path(cur), _ROOT):
            REPAIRS.append((who, key))
            os.environ[key] = value
    # The variables activate() cleared must stay cleared. CLAUDE_PLUGIN_DATA
    # outranks every data-dir variable above, so one left pointing outside the
    # tree defeats the whole guard; CURSOR_VERSION changes how the runner behaves.
    for key in _CLEARED_PATHLIKE:
        cur = os.environ.get(key)
        if cur is not None and not _inside(Path(cur), _ROOT):
            REPAIRS.append((who, key))
            del os.environ[key]
    if "CURSOR_VERSION" in os.environ:
        REPAIRS.append((who, "CURSOR_VERSION"))
        del os.environ["CURSOR_VERSION"]


def seed_snooze(data_dir) -> None:
    """Snooze hooks for a data dir so a spawned ``hook_runner.py`` stops before audio.

    ``test_cursor_bridge`` and ``test_codex_hooks`` spawn the real runner; without
    this each spawn starts a real audio player and raises a real toast (on Windows
    that is ``powershell.exe`` creating ``Media Player`` under the fake home with
    an ACL the cleanup cannot remove). Snooze is checked after the enabled gate
    and before any audio, so the runner's early-exit behaviour -- which is what
    those tests assert -- is unchanged.
    """
    import time
    data_dir = Path(data_dir)
    queue = data_dir if data_dir.name == "claude_audio_hooks_queue" else data_dir / "queue"
    try:
        queue.mkdir(parents=True, exist_ok=True)
        (queue / "snooze_until").write_text(str(time.time() + 365 * 86400), encoding="utf-8")
    except OSError:
        pass


def _install_unittest_hooks() -> None:
    import unittest
    import unittest.suite
    if getattr(unittest.TestCase, "_echook_isolation_wrapped", False):
        return
    orig_run = unittest.TestCase.run

    def run(self, result=None):
        restore_baseline(self.id())
        return orig_run(self, result)

    unittest.TestCase.run = run
    suite_cls = unittest.suite.TestSuite
    orig_setup = getattr(suite_cls, "_handleClassSetUp", None)
    if orig_setup is not None:
        def handle_class_setup(self, test, result):
            restore_baseline("setUpClass:" + type(test).__name__)
            return orig_setup(self, test, result)

        suite_cls._handleClassSetUp = handle_class_setup
    unittest.TestCase._echook_isolation_wrapped = True


def _root_is_trustworthy(root: Path) -> bool:
    """An inherited root only counts if this process is really inside it."""
    try:
        return (root.is_dir()
                and _inside(Path.home(), root)
                and _inside(Path(os.environ.get("TEMP", "")), root))
    except OSError:
        return False


def activate() -> Path:
    """Idempotent. Returns the isolation root."""
    global _ROOT, _BASELINE
    existing = os.environ.get(_ROOT_VAR)
    if existing and _root_is_trustworthy(Path(existing)):
        _ROOT = Path(existing)
        _BASELINE = _baseline_for(_ROOT)
        _install_unittest_hooks()
        return _ROOT

    os.environ[_REAL_HOME_VAR] = str(Path.home())
    root = Path(tempfile.mkdtemp(prefix="echook-tests-"))
    for d in ("home", "tmp", "data"):
        (root / d).mkdir()

    for name in _CLEARED:
        os.environ.pop(name, None)
    os.environ[_ROOT_VAR] = str(root)
    os.environ.update(_baseline_for(root))
    home = root / "home"
    if home.drive:
        os.environ["HOMEDRIVE"] = home.drive
        os.environ["HOMEPATH"] = str(home)[len(home.drive):]
    tempfile.tempdir = str(root / "tmp")

    _ROOT = root
    _BASELINE = _baseline_for(root)
    seed_snooze(root / "data")
    seed_snooze(root / "tmp" / "claude_audio_hooks_queue")
    _install_unittest_hooks()
    atexit.register(shutil.rmtree, str(root), ignore_errors=True)
    reset_state()
    return root


activate()
