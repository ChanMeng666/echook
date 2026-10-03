"""Tests for the desktop notification path in ``hooks/hook_runner.py``.

Until v6.5.1 the Windows and WSL branches escaped their title/body with
``_escape_notification_string`` — an osascript/shell escaper that turns ``"``
into ``\\"``. PowerShell does not treat ``\\`` as an escape character, so any
notification body containing a quote produced a script that failed to *parse*::

    $n.ShowBalloonTip(5000, "Claude Code", "git commit -m \\"fix\\"", ...)
    → Missing ')' in method call.

``permission_request`` bodies embed tool commands, so quotes are routine and the
toast simply never appeared. Nothing surfaced the failure either: the dispatcher
``Popen``'d to DEVNULL and returned ``True`` unconditionally.

``TestGeneratedScriptParses`` is the regression guard — it hands the generated
script to PowerShell's own parser rather than asserting against a mock.

Run with::

    python -m unittest discover tests
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
HOOK_RUNNER = REPO / "hooks" / "hook_runner.py"

# Bodies that broke the pre-6.5.1 escaper, plus the XML metacharacters the
# WinRT template has to survive now that text is inserted as a text node.
NASTY_BODIES = [
    'Permission needed: Bash - git commit -m "fix: toast"',
    "Cost so far: $12.50 for $HOME",
    "Run `Get-Date` in a backtick block",
    'Mixed: "quoted" $var `tick` & <tag> \'single\'',
    "Trailing backslash path C:\\Users\\0\\",
]


def _load_hook_runner():
    spec = importlib.util.spec_from_file_location("hook_runner", HOOK_RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _LogCapture:
    """Swap ``log_event`` for a recorder so tests never touch events.ndjson."""

    def __init__(self, module):
        self.module = module
        self.events: List[Dict[str, Any]] = []
        self._original = None

    def __enter__(self):
        self._original = self.module.log_event

        def _record(level, action, hook=None, **fields):
            self.events.append(dict(level=level, action=action, hook=hook, **fields))

        self.module.log_event = _record
        return self

    def __exit__(self, *exc):
        self.module.log_event = self._original
        return False

    def find(self, action: str) -> Optional[Dict[str, Any]]:
        for event in self.events:
            if event.get("action") == action:
                return event
        return None


class _SpawnRecorder:
    """Stand in for both PowerShell entry points and record every dispatch.

    ``spawn_result`` drives fire-and-forget ``_spawn_powershell`` (bool, or a
    callable taking the script). ``run_result`` drives the synchronous
    ``_run_powershell`` probe (a PROBE_* string, or a callable taking the script
    and the timeout). ``duration`` lets a test simulate a probe that burns
    wall-clock without actually sleeping.
    """

    def __init__(self, module, spawn_result=True, run_result=None, duration=0.0):
        self.module = module
        self.spawn_result = spawn_result
        self.run_result = module.PROBE_OK if run_result is None else run_result
        self.duration = duration
        self.calls: List[Dict[str, Any]] = []
        self._originals: Dict[str, Any] = {}
        self._clock = 0.0

    def __enter__(self):
        self._originals = {
            "_spawn_powershell": self.module._spawn_powershell,
            "_run_powershell": self.module._run_powershell,
            "monotonic": self.module.time.monotonic,
        }

        def _spawn(script):
            self.calls.append({"script": script, "wait": False, "timeout": None})
            return self.spawn_result(script) if callable(self.spawn_result) else self.spawn_result

        def _run(script, timeout):
            self.calls.append({"script": script, "wait": True, "timeout": timeout})
            self._clock += self.duration
            if callable(self.run_result):
                return self.run_result(script, timeout)
            return self.run_result

        # A fake clock keeps budget tests deterministic and instant.
        self.module._spawn_powershell = _spawn
        self.module._run_powershell = _run
        if self.duration:
            self.module.time.monotonic = lambda: self._clock
        return self

    def __exit__(self, *exc):
        self.module._spawn_powershell = self._originals["_spawn_powershell"]
        self.module._run_powershell = self._originals["_run_powershell"]
        self.module.time.monotonic = self._originals["monotonic"]
        return False

    @property
    def probes(self) -> List[Dict[str, Any]]:
        return [c for c in self.calls if c["wait"]]


class TestPowerShellEscaping(unittest.TestCase):
    """The Windows/WSL scripts must use backtick escaping, and must not strip."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_hook_runner()

    def test_double_quote_is_backtick_escaped(self):
        body = 'git commit -m "fix: toast"'
        for backend in ("winrt", "burnttoast", "notifyicon"):
            with self.subTest(backend=backend):
                script = self.mod._build_windows_toast_script(backend, "Claude Code", body)
                self.assertIn('git commit -m `"fix: toast`"', script)
                self.assertNotIn('\\"', script)

    def test_dollar_and_backtick_survive(self):
        script = self.mod._build_windows_toast_script(
            "winrt", "Claude Code", "Cost $12 via `Get-Date`"
        )
        # Escaped, not deleted: the old escaper dropped $ and ` from the text
        # the user actually reads.
        self.assertIn("Cost `$12 via ``Get-Date``", script)

    def test_title_is_escaped_too(self):
        script = self.mod._build_windows_toast_script(
            "notifyicon", 'Claude "Code" $1', "body"
        )
        self.assertIn('Claude `"Code`" `$1', script)

    def test_winrt_inserts_text_as_xml_text_nodes(self):
        # Text goes through CreateTextNode so XML metacharacters in a tool
        # command cannot break the toast document.
        script = self.mod._build_windows_toast_script(
            "winrt", "Claude Code", "diff <old> & <new>"
        )
        self.assertIn("CreateTextNode(", script)
        self.assertIn("diff <old> & <new>", script)

    def test_winrt_targets_a_registered_aumid(self):
        script = self.mod._build_windows_toast_script("winrt", "t", "m")
        self.assertIn("CreateToastNotifier(", script)
        self.assertIn("{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}", script)

    def test_critical_urgency_uses_long_duration(self):
        normal = self.mod._build_windows_toast_script("winrt", "t", "m", "normal")
        critical = self.mod._build_windows_toast_script("winrt", "t", "m", "critical")
        self.assertIn('"duration", "short"', normal)
        self.assertIn('"duration", "long"', critical)

    def test_notifyicon_urgency_maps_to_tooltip_icon(self):
        normal = self.mod._build_windows_toast_script("notifyicon", "t", "m", "normal")
        critical = self.mod._build_windows_toast_script("notifyicon", "t", "m", "critical")
        self.assertIn("ToolTipIcon]::Info", normal)
        self.assertIn("ToolTipIcon]::Warning", critical)

    def test_burnttoast_is_never_installed(self):
        script = self.mod._build_windows_toast_script("burnttoast", "t", "m")
        self.assertIn("Import-Module BurntToast", script)
        self.assertNotIn("Install-Module", script)

    def test_powershell_invocation_uses_noprofile(self):
        with mock.patch.object(self.mod.subprocess, "Popen") as popen:
            self.assertTrue(self.mod._spawn_powershell("Write-Host hi"))
        cmd = popen.call_args[0][0]
        self.assertEqual(cmd[0], "powershell.exe")
        self.assertIn("-NoProfile", cmd)
        self.assertIn("-ExecutionPolicy", cmd)
        self.assertIn("-WindowStyle", cmd)


@unittest.skipUnless(
    platform.system() == "Windows" and shutil.which("powershell.exe"),
    "PowerShell's own parser is only available on Windows",
)
class TestGeneratedScriptParses(unittest.TestCase):
    """Feed each generated script to ``Parser::ParseInput`` and demand 0 errors.

    This is the regression guard for the escaping bug: the pre-6.5.1 script
    fails here with "Missing ')' in method call." for every body holding a
    double quote.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_hook_runner()
        cls.tmpdir = tempfile.mkdtemp(prefix="echook-ps-parse-")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _parse_errors(self, script: str) -> str:
        path = os.path.join(self.tmpdir, "candidate.ps1")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(script)
        harness = (
            "$src = Get-Content -Raw -Encoding UTF8 -LiteralPath '{path}'; "
            "$errs = $null; "
            "[void][System.Management.Automation.Language.Parser]::ParseInput("
            "$src, [ref]$null, [ref]$errs); "
            "if ($errs.Count -gt 0) {{ $errs | ForEach-Object {{ $_.Message }}; exit 1 }}; "
            "exit 0"
        ).format(path=path)
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", harness],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0:
            return ""
        return (proc.stdout or "") + (proc.stderr or "") or "unknown parse failure"

    def test_all_backends_parse_for_nasty_bodies(self):
        for backend in self.mod._WINDOWS_BACKENDS:
            for body in NASTY_BODIES:
                for urgency in ("normal", "critical"):
                    with self.subTest(backend=backend, body=body, urgency=urgency):
                        script = self.mod._build_windows_toast_script(
                            backend, 'Claude "Code"', body, urgency
                        )
                        errors = self._parse_errors(script)
                        self.assertEqual("", errors, f"{backend} script failed to parse: {errors}")

    def test_sapi_script_parses_for_nasty_bodies(self):
        # play_tts carried the identical defect: a quote in the spoken text made
        # the SAPI script unparseable, so TTS went silent.
        for body in NASTY_BODIES:
            with self.subTest(body=body):
                errors = self._parse_errors(self.mod._build_sapi_script(body))
                self.assertEqual("", errors, f"SAPI script failed to parse: {errors}")

    def test_pre_fix_escaper_would_have_failed(self):
        # Pins the bug itself: the old shell/osascript escaping produces a
        # script PowerShell cannot parse. If this ever starts parsing, the
        # regression guard above has stopped guarding anything.
        bad = self.mod._escape_notification_string('git commit -m "fix: toast"')
        script = f'$n.ShowBalloonTip(5000, "Claude Code", "{bad}", 0)'
        self.assertNotEqual("", self._parse_errors(script))


class TestMacOsBranch(unittest.TestCase):
    """The osascript escaper is still correct for macOS and must stay there."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_hook_runner()

    def test_darwin_uses_osascript_escaper(self):
        with _LogCapture(self.mod), \
                mock.patch.object(self.mod.platform, "system", return_value="Darwin"), \
                mock.patch.object(self.mod.subprocess, "Popen") as popen:
            self.assertTrue(
                self.mod.send_desktop_notification("Claude Code", 'say "hi"')
            )
        cmd = popen.call_args[0][0]
        self.assertEqual(cmd[0], "osascript")
        self.assertIn('say \\"hi\\"', cmd[2])
        self.assertNotIn('`"', cmd[2])

    def test_darwin_dispatch_failure_returns_false(self):
        with _LogCapture(self.mod) as log, \
                mock.patch.object(self.mod.platform, "system", return_value="Darwin"), \
                mock.patch.object(self.mod.subprocess, "Popen",
                                  side_effect=FileNotFoundError("osascript")):
            self.assertFalse(self.mod.send_desktop_notification("Claude Code", "body"))
        event = log.find("desktop_notification")
        self.assertIsNotNone(event)
        self.assertEqual("error", event["level"])


class TestWslBranch(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_hook_runner()

    def test_wsl_uses_powershell_escaping(self):
        with _LogCapture(self.mod), \
                mock.patch.object(self.mod.platform, "system", return_value="Linux"), \
                mock.patch.object(self.mod, "is_wsl", return_value=True), \
                _SpawnRecorder(self.mod) as spawn:
            self.assertTrue(
                self.mod.send_desktop_notification("Claude Code", 'git -m "x" $y')
            )
        self.assertEqual(1, len(spawn.calls))
        script = spawn.calls[0]["script"]
        self.assertIn('git -m `"x`" `$y', script)
        self.assertNotIn('\\"', script)

    def test_wsl_spawn_failure_returns_false(self):
        with _LogCapture(self.mod), \
                mock.patch.object(self.mod.platform, "system", return_value="Linux"), \
                mock.patch.object(self.mod, "is_wsl", return_value=True), \
                _SpawnRecorder(self.mod, spawn_result=False):
            self.assertFalse(self.mod.send_desktop_notification("Claude Code", "body"))


class TestWindowsBackendChain(unittest.TestCase):
    """Backend selection is probed once and cached; dispatch reports the truth."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_hook_runner()

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="echook-notify-cache-")
        self.cache = Path(self.tmpdir) / "notify_backend"
        self._patch = mock.patch.object(
            self.mod, "_notify_backend_cache_path", return_value=self.cache
        )
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

    def test_backend_is_probed_once_then_cached(self):
        with _LogCapture(self.mod), \
                mock.patch.object(self.mod.platform, "system", return_value="Windows"), \
                _SpawnRecorder(self.mod) as spawn:
            self.assertTrue(self.mod.send_desktop_notification("Claude Code", "one"))
            self.assertTrue(self.mod.send_desktop_notification("Claude Code", "two"))
            self.assertTrue(self.mod.send_desktop_notification("Claude Code", "three"))

        # First call probes synchronously (wait=True); the rest are
        # fire-and-forget through the cached winner.
        self.assertEqual(3, len(spawn.calls))
        self.assertTrue(spawn.calls[0]["wait"])
        self.assertFalse(spawn.calls[1]["wait"])
        self.assertFalse(spawn.calls[2]["wait"])

        cached = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual("winrt", cached["backend"])

    def test_falls_through_to_notifyicon_when_winrt_fails(self):
        # winrt fails outright, BurntToast is not installed -> tray balloon.
        # A clean *failure* (not a timeout) is a real verdict, so it is cached.
        with _LogCapture(self.mod) as log, \
                mock.patch.object(self.mod.platform, "system", return_value="Windows"), \
                _SpawnRecorder(self.mod, run_result=self.mod.PROBE_FAILED):
            self.assertTrue(self.mod.send_desktop_notification("Claude Code", "body"))

        cached = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual("notifyicon", cached["backend"])
        event = log.find("desktop_notification")
        self.assertEqual("info", event["level"])
        self.assertEqual("notifyicon", event["backend"])

    def test_all_backends_failing_returns_false_and_logs_error_code(self):
        with _LogCapture(self.mod) as log, \
                mock.patch.object(self.mod.platform, "system", return_value="Windows"), \
                _SpawnRecorder(self.mod, spawn_result=False,
                               run_result=self.mod.PROBE_FAILED):
            self.assertFalse(self.mod.send_desktop_notification("Claude Code", "body"))

        self.assertFalse(self.cache.exists())
        event = log.find("desktop_notification")
        self.assertIsNotNone(event)
        self.assertEqual("error", event["level"])
        self.assertEqual(
            self.mod.ErrorCode.NOTIFICATION_FAILED, event["error"]["code"]
        )
        self.assertIn("suggested_command", event["error"])

    def test_subprocess_failure_returns_false(self):
        # Exercises the real _spawn_powershell error path rather than a stub.
        with _LogCapture(self.mod), \
                mock.patch.object(self.mod.platform, "system", return_value="Windows"), \
                mock.patch.object(self.mod.subprocess, "run",
                                  side_effect=OSError("no powershell")), \
                mock.patch.object(self.mod.subprocess, "Popen",
                                  side_effect=OSError("no powershell")):
            self.assertFalse(self.mod.send_desktop_notification("Claude Code", "body"))

    def test_stale_cache_is_reprobed(self):
        self.cache.write_text(
            json.dumps({"backend": "winrt", "ts": 0, "release": platform.release()}),
            encoding="utf-8",
        )
        self.assertIsNone(self.mod._read_cached_notify_backend())

    def test_cache_from_another_os_release_is_ignored(self):
        self.cache.write_text(
            json.dumps({"backend": "winrt", "ts": self.mod.time.time(), "release": "6.1"}),
            encoding="utf-8",
        )
        self.assertIsNone(self.mod._read_cached_notify_backend())

    def test_unknown_backend_in_cache_is_ignored(self):
        self.cache.write_text(
            json.dumps({"backend": "growl", "ts": self.mod.time.time(),
                        "release": platform.release()}),
            encoding="utf-8",
        )
        self.assertIsNone(self.mod._read_cached_notify_backend())

    def test_corrupt_cache_is_ignored(self):
        self.cache.write_text("not json", encoding="utf-8")
        self.assertIsNone(self.mod._read_cached_notify_backend())

    def test_unspawnable_cached_backend_is_cleared(self):
        self.cache.write_text(
            json.dumps({"backend": "winrt", "ts": self.mod.time.time(),
                        "release": platform.release()}),
            encoding="utf-8",
        )
        with _LogCapture(self.mod), \
                mock.patch.object(self.mod.platform, "system", return_value="Windows"), \
                _SpawnRecorder(self.mod, spawn_result=False,
                               run_result=self.mod.PROBE_FAILED):
            self.assertFalse(self.mod.send_desktop_notification("Claude Code", "body"))
        self.assertFalse(self.cache.exists())


class TestProbeBudget(unittest.TestCase):
    """The chain is bounded, and an unfinished chain must cache nothing.

    Every handler in plugins/audio-hooks/hooks/hooks.json is registered with
    ``"timeout": 10``, so a probe that overran would be reaped by the harness —
    possibly after writing a verdict it never finished validating.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_hook_runner()

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="echook-notify-budget-")
        self.cache = Path(self.tmpdir) / "notify_backend"
        patcher = mock.patch.object(
            self.mod, "_notify_backend_cache_path", return_value=self.cache
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

    def test_budget_is_under_the_registered_hook_timeout(self):
        template = json.loads(
            (REPO / "plugins" / "audio-hooks" / "hooks" / "hooks.json").read_text(
                encoding="utf-8"
            )
        )
        timeouts = set()
        for groups in template.get("hooks", {}).values():
            for group in groups:
                for handler in group.get("hooks", []):
                    if "timeout" in handler:
                        timeouts.add(handler["timeout"])
        self.assertTrue(timeouts, "no handler declares a timeout")
        self.assertLess(self.mod._NOTIFY_PROBE_BUDGET_SEC, min(timeouts))

    def test_whole_chain_fits_in_the_budget(self):
        # The per-call timeouts must *sum* to no more than the budget. If they
        # do not, a backend late in the chain can never be given its full
        # timeout, so it is never reached and a host where the earlier backends
        # fail re-probes forever instead of ever settling on a verdict.
        worst_case = (
            self.mod._WINRT_PROBE_TIMEOUT_SEC
            + self.mod._MODULE_QUERY_TIMEOUT_SEC
            + self.mod._BURNTTOAST_PROBE_TIMEOUT_SEC
        )
        self.assertLessEqual(worst_case, self.mod._NOTIFY_PROBE_BUDGET_SEC)

    def test_burnttoast_is_reachable_when_winrt_fails(self):
        # Guards the arithmetic above end to end: a clean winrt failure must
        # leave room to actually query for and try BurntToast.
        with _LogCapture(self.mod), \
                mock.patch.object(self.mod.platform, "system", return_value="Windows"), \
                _SpawnRecorder(self.mod, run_result=self.mod.PROBE_FAILED) as spawn:
            self.mod.send_desktop_notification("Claude Code", "body")
        self.assertEqual(2, len(spawn.probes), "BurntToast was never queried")
        self.assertIn("Get-Module", spawn.probes[1]["script"])

    def test_timed_out_probe_caches_nothing(self):
        # An inconclusive winrt probe may just be a slow host. Show the balloon
        # so the user still gets the notification, but withhold the verdict.
        with _LogCapture(self.mod), \
                mock.patch.object(self.mod.platform, "system", return_value="Windows"), \
                _SpawnRecorder(self.mod, run_result=self.mod.PROBE_INCONCLUSIVE) as spawn:
            self.assertTrue(self.mod.send_desktop_notification("Claude Code", "body"))

        self.assertFalse(self.cache.exists())
        # It bailed at winrt rather than continuing down the chain.
        self.assertEqual(1, len(spawn.probes))

    def test_exhausted_budget_stops_the_chain_without_caching(self):
        # Each probe burns 5s of an 8s budget: winrt fails cleanly, then there
        # is no room left to give BurntToast its full timeout.
        with _LogCapture(self.mod), \
                mock.patch.object(self.mod.platform, "system", return_value="Windows"), \
                _SpawnRecorder(self.mod, run_result=self.mod.PROBE_FAILED,
                               duration=5.0) as spawn:
            self.assertTrue(self.mod.send_desktop_notification("Claude Code", "body"))

        self.assertFalse(self.cache.exists())
        self.assertEqual(1, len(spawn.probes))

    def test_probes_are_never_given_a_truncated_timeout(self):
        # A backend either gets its full generous timeout or is not run at all;
        # a squeezed timeout would condemn a working backend for a week.
        seen: List[float] = []

        def run(script, timeout):
            seen.append(timeout)
            return self.mod.PROBE_FAILED

        with _LogCapture(self.mod), \
                mock.patch.object(self.mod.platform, "system", return_value="Windows"), \
                _SpawnRecorder(self.mod, run_result=run, duration=1.0):
            self.mod.send_desktop_notification("Claude Code", "body")

        allowed = {
            self.mod._WINRT_PROBE_TIMEOUT_SEC,
            self.mod._MODULE_QUERY_TIMEOUT_SEC,
            self.mod._BURNTTOAST_PROBE_TIMEOUT_SEC,
        }
        self.assertTrue(seen)
        for timeout in seen:
            self.assertIn(timeout, allowed)


class TestTts(unittest.TestCase):
    """play_tts had the identical PowerShell escaping bug (fixed in v6.5.1)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_hook_runner()

    def test_sapi_script_uses_powershell_escaping(self):
        script = self.mod._build_sapi_script('Done: "fix" costs $5 `now`')
        self.assertIn('Done: `"fix`" costs `$5 ``now``', script)
        self.assertNotIn('\\"', script)

    def test_windows_tts_goes_through_the_shared_launcher(self):
        with _LogCapture(self.mod) as log, \
                mock.patch.object(self.mod.platform, "system", return_value="Windows"), \
                _SpawnRecorder(self.mod) as spawn:
            self.assertTrue(self.mod.play_tts('say "hi" $now'))
        self.assertEqual(1, len(spawn.calls))
        self.assertIn('say `"hi`" `$now', spawn.calls[0]["script"])
        event = log.find("tts_dispatch")
        self.assertEqual("info", event["level"])
        self.assertEqual("sapi", event["backend"])

    def test_wsl_tts_uses_powershell_escaping(self):
        with _LogCapture(self.mod), \
                mock.patch.object(self.mod.platform, "system", return_value="Linux"), \
                mock.patch.object(self.mod, "is_wsl", return_value=True), \
                _SpawnRecorder(self.mod) as spawn:
            self.assertTrue(self.mod.play_tts('say "hi"'))
        self.assertIn('say `"hi`"', spawn.calls[0]["script"])

    def test_macos_say_receives_the_raw_message_as_argv(self):
        with _LogCapture(self.mod), \
                mock.patch.object(self.mod.platform, "system", return_value="Darwin"), \
                mock.patch.object(self.mod.subprocess, "Popen") as popen:
            self.assertTrue(self.mod.play_tts('say "hi" $now'))
        cmd = popen.call_args[0][0]
        self.assertEqual(["say", 'say "hi" $now'], cmd)

    def test_failure_returns_false_and_emits_tts_failed(self):
        with _LogCapture(self.mod) as log, \
                mock.patch.object(self.mod.platform, "system", return_value="Windows"), \
                _SpawnRecorder(self.mod, spawn_result=False):
            self.assertFalse(self.mod.play_tts("body"))
        event = log.find("tts_dispatch")
        self.assertEqual("error", event["level"])
        self.assertEqual(self.mod.ErrorCode.TTS_FAILED, event["error"]["code"])

    def test_no_linux_engine_returns_false(self):
        with _LogCapture(self.mod), \
                mock.patch.object(self.mod.platform, "system", return_value="Linux"), \
                mock.patch.object(self.mod, "is_wsl", return_value=False), \
                mock.patch.object(self.mod.shutil, "which", return_value=None):
            self.assertFalse(self.mod.play_tts("body"))

    def test_subprocess_failure_returns_false(self):
        with _LogCapture(self.mod), \
                mock.patch.object(self.mod.platform, "system", return_value="Windows"), \
                mock.patch.object(self.mod.subprocess, "Popen",
                                  side_effect=OSError("no powershell")):
            self.assertFalse(self.mod.play_tts("body"))


class TestNotificationModeDefault(unittest.TestCase):
    """The code fallback must match the shipped config/default_preferences.json."""

    def test_mode_fallback_matches_template(self):
        src = HOOK_RUNNER.read_text(encoding="utf-8")
        self.assertIn('notification_settings.get("mode", "audio_and_notification")', src)
        template = json.loads(
            (REPO / "config" / "default_preferences.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            template["notification_settings"]["mode"],
            "audio_and_notification",
        )


if __name__ == "__main__":
    unittest.main()
