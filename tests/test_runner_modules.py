"""The shape of the split hook runner: who owns what, and how modules reach each other.

``hooks/hook_runner.py`` keeps the catalog tables, the gating and ``run_hook``;
each dispatch concern is a sibling module, and ``runner_core`` owns the state
they share. Three rules keep that honest, and each is cheap to break by
accident:

* a name lives in exactly one module and is called through it
  (``runner_core.log_event(...)``), so a test that patches it once covers every
  caller -- and a patch aimed at a name that moved fails instead of passing
  while the real sound or toast code runs;
* no sibling imports ``hook_runner`` (it runs as ``__main__``; importing it
  again would execute a second copy);
* the list of modules is the same in the runner, on disk, and in the plugin
  build.
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import importlib.util
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "hooks"
HOOK_RUNNER = HOOKS / "hook_runner.py"
PRE_SPLIT_FILES = {"hook_runner", "user_preferences", "invoker"}

# Names that left hook_runner.py for the module named. hook_runner must not
# re-export them: an old ``patch.object(hook_runner, "play_audio")`` has to
# raise, not silently patch a name nothing looks up any more.
MOVED = {
    "runner_core": ["log_event", "log_error_event", "log_debug", "log_error", "log_trigger", "_prefs",
                    "get_log_dir", "ensure_queue_dir", "_queue_dir_ensured", "_set_log_context",
                    "_current_hook_type", "_current_session_id", "_get_invoker", "_invoker_display_name",
                    "DEBUG"],
    "platform_shell": ["escape_powershell_string", "get_safe_temp_dir", "is_wsl", "_spawn_powershell",
                       "_run_powershell", "PROBE_OK", "PROBE_FAILED", "PROBE_INCONCLUSIVE"],
    "audio_playback": ["play_audio", "play_audio_windows", "play_audio_macos", "play_audio_linux",
                       "play_audio_wsl"],
    "notification_context": ["get_notification_context", "_format_context_suffix", "_clean_for_output",
                             "_redact_secrets", "_truncate", "_get_tool_detail"],
    "desktop_notification": ["send_desktop_notification", "_build_windows_toast_script",
                             "_escape_notification_string", "_notify_backend_cache_path"],
    "text_to_speech": ["play_tts", "_build_sapi_script"],
    "webhook_dispatch": ["send_webhook"],
    "rate_limit_alerts": ["check_rate_limits", "_threshold_values"],
    "terminal_sequence": ["emit_terminal_sequence", "build_terminal_sequence",
                          "TERMINAL_SEQUENCE_SAFE_EVENTS", "TERMINAL_SEQUENCE_FORBIDDEN_EVENTS"],
}


def _load_hook_runner():
    spec = importlib.util.spec_from_file_location("hook_runner", HOOK_RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _source(name: str) -> str:
    return (HOOKS / (name + ".py")).read_text(encoding="utf-8")


class TestModuleSet(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hr = _load_hook_runner()

    def test_runner_list_matches_the_files_on_disk(self) -> None:
        on_disk = {p.stem for p in HOOKS.glob("*.py")} - PRE_SPLIT_FILES
        self.assertEqual(set(self.hr._RUNNER_MODULES), on_disk)
        self.assertEqual(set(MOVED), on_disk, "add the new module's names to MOVED")

    def test_plugin_build_syncs_every_module(self) -> None:
        script = (REPO / "scripts" / "build-plugin.sh").read_text(encoding="utf-8")
        listed = set()
        for line in re.findall(r'^RUNNER_MODULES="([^"]*)"', script, re.MULTILINE):
            listed |= {w for w in line.split() if not w.startswith("$")}
        self.assertEqual(listed, set(self.hr._RUNNER_MODULES))
        mirror = REPO / "plugins" / "audio-hooks" / "hooks"
        for name in self.hr._RUNNER_MODULES:
            self.assertEqual((mirror / (name + ".py")).read_bytes().replace(b"\r\n", b"\n"),
                             (HOOKS / (name + ".py")).read_bytes().replace(b"\r\n", b"\n"), name)

    def test_script_install_bootstrap_looks_for_a_module_that_exists(self) -> None:
        self.assertIn('"runner_core.py"', _source("hook_runner"))
        self.assertTrue((HOOKS / "runner_core.py").exists())


class TestOneOwnerPerName(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hr = _load_hook_runner()

    def test_moved_names_live_in_their_module_and_nowhere_in_hook_runner(self) -> None:
        for module, names in MOVED.items():
            owner = getattr(self.hr, module, None) or self.hr.desktop_notification.platform_shell
            self.assertEqual(owner.__name__, module)
            for name in names:
                with self.subTest(module=module, name=name):
                    self.assertTrue(hasattr(owner, name), "not defined in its module")
                    self.assertFalse(hasattr(self.hr, name), "hook_runner re-exports a moved name")

    def test_no_from_import_between_runner_modules(self) -> None:
        modules = "|".join(sorted(MOVED))
        pattern = re.compile(r"^\s*from\s+(%s|hook_runner)\s+import\b" % modules, re.MULTILINE)
        for name in sorted(MOVED) + ["hook_runner"]:
            with self.subTest(module=name):
                self.assertEqual(pattern.findall(_source(name)), [])

    def test_no_sibling_imports_hook_runner(self) -> None:
        pattern = re.compile(r"^\s*(?:import|from)\s+hook_runner\b", re.MULTILINE)
        for name in sorted(MOVED):
            with self.subTest(module=name):
                self.assertIsNone(pattern.search(_source(name)))

    def test_import_layers(self) -> None:
        """runner_core imports no sibling; the others import only what is below them."""
        allowed = {
            "runner_core": set(),
            "notification_context": set(),
            "platform_shell": {"runner_core"},
            "audio_playback": {"runner_core", "platform_shell"},
            "desktop_notification": {"runner_core", "platform_shell"},
            "text_to_speech": {"runner_core", "platform_shell"},
            "webhook_dispatch": {"runner_core"},
            "terminal_sequence": {"runner_core"},
            "rate_limit_alerts": {"runner_core", "audio_playback"},
        }
        self.assertEqual(set(allowed), set(MOVED))
        for name, permitted in allowed.items():
            imported = set(re.findall(r"^import\s+(\w+)", _source(name), re.MULTILINE)) & set(MOVED)
            with self.subTest(module=name):
                self.assertEqual(imported, permitted)

    def test_tables_are_handed_over_by_reference(self) -> None:
        hr = self.hr
        self.assertIs(hr.runner_core.ERROR_HINTS, hr._ERROR_HINTS)
        self.assertIs(hr.notification_context.NOTIFICATION_TYPE_LABELS, hr.NOTIFICATION_TYPE_LABELS)
        self.assertEqual(hr.runner_core.RUNNER_VERSION, hr.HOOK_RUNNER_VERSION)
        self.assertEqual(hr.runner_core.PROJECT_DIR, hr.PROJECT_DIR)
        self.assertEqual(hr.runner_core.AUDIO_DIR, hr.AUDIO_DIR)

    def test_error_codes_written_as_literals_are_catalogued(self) -> None:
        """The siblings cannot import ErrorCode (it stays in hook_runner.py), so
        they pass the code as a string; each must be a real ErrorCode value."""
        codes = {getattr(self.hr.ErrorCode, n) for n in dir(self.hr.ErrorCode) if not n.startswith("_")}
        seen = set()
        for name in sorted(MOVED):
            source = _source(name)
            self.assertNotIn("ErrorCode.", source, name)
            for call in re.finditer(r"log_error_event\(\s*([^,]+),", source):
                arg = call.group(1).strip()
                if arg == "code: str":
                    continue  # the definition itself
                with self.subTest(module=name, code=arg):
                    self.assertRegex(arg, r'^"[A-Z_]+"$', "error code must be a string literal")
                    self.assertIn(arg.strip('"'), codes)
                    seen.add(arg.strip('"'))
        self.assertGreaterEqual(len(seen), 5)


class TestFreshLoadGetsFreshSiblings(unittest.TestCase):
    def test_two_loads_do_not_share_state(self) -> None:
        a = _load_hook_runner()
        b = _load_hook_runner()
        for name in a._RUNNER_MODULES:
            if hasattr(a, name):
                self.assertIsNot(getattr(a, name), getattr(b, name), name)
        # Within one load every module sees the same runner_core.
        for hr in (a, b):
            for name in ("audio_playback", "desktop_notification", "text_to_speech", "webhook_dispatch",
                         "rate_limit_alerts", "terminal_sequence"):
                self.assertIs(getattr(hr, name).runner_core, hr.runner_core, name)
            self.assertIs(hr.desktop_notification.platform_shell, hr.text_to_speech.platform_shell)
            self.assertIs(hr.desktop_notification.platform_shell.runner_core, hr.runner_core)
        a.runner_core._queue_dir_ensured = True
        self.assertFalse(b.runner_core._queue_dir_ensured)
        a.runner_core._queue_dir_ensured = False


if __name__ == "__main__":
    unittest.main()
