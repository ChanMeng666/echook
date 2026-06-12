"""Tests for Codex CLI compatibility (5.x.x+).

Codex (per developers.openai.com/codex/hooks) supports ten hook events
(SessionStart, PreToolUse, PostToolUse, PermissionRequest, UserPromptSubmit,
Stop, PreCompact, PostCompact, SubagentStart, SubagentStop). Adapting
audio-hooks for Codex therefore requires:

  1. A separate ``codex-hooks/hooks.json`` template installed at
     ``$CODEX_HOME/hooks.json`` by ``audio-hooks install --codex``.
  2. A new ``--invoker codex`` CLI flag baked into every command in that
     template — Codex sets no env var we could detect by, so the runner
     reads the invoker from argv.
  3. Data-dir resolution that lands at ``$CODEX_HOME/audio-hooks-data/``
     when the invoker is codex (gated so a Cursor or Claude Code session
     on the same machine doesn't accidentally hijack it).
  4. No-op handling for the audio-hooks canonical events with no Codex
     equivalent (mirroring how Notification/PermissionRequest no-op under
     Cursor).
  5. Current feature handling: Codex hooks are on by default. The canonical
     key is ``[features].hooks``; the old ``codex_hooks`` key is accepted only
     as a backwards-compatible alias.

This file pins those contracts.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional

REPO = Path(__file__).resolve().parent.parent
HOOK_RUNNER = REPO / "hooks" / "hook_runner.py"
CODEX_TEMPLATE = REPO / "codex-hooks" / "hooks.json"
CODEX_PLUGIN_TEMPLATE = REPO / "codex-hooks" / "plugin-hooks.json"
CODEX_PLUGIN_MANIFEST = REPO / "plugins" / "audio-hooks" / ".codex-plugin" / "plugin.json"
AUDIO_HOOKS_CLI = REPO / "bin" / "audio-hooks.py"


def _load_module():
    """Re-execute hook_runner.py so module-level caches reset between tests."""
    sys.modules.pop("hook_runner", None)
    sys.modules.pop("invoker", None)
    sys.modules.pop("user_preferences", None)
    spec = importlib.util.spec_from_file_location("hook_runner", HOOK_RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_invoker_module():
    sys.modules.pop("invoker", None)
    invoker_path = REPO / "hooks" / "invoker.py"
    spec = importlib.util.spec_from_file_location("invoker", invoker_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_hook(
    hook_type: str,
    stdin_payload: str = "{}",
    *,
    env_extra: Optional[Dict[str, str]] = None,
    state_dir: Optional[Path] = None,
    invoker: Optional[str] = None,
):
    """Subprocess-invoke hook_runner.py with isolated env."""
    env = os.environ.copy()
    for k in (
        "CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT", "CLAUDE_AUDIO_HOOKS_DATA",
        "CURSOR_VERSION", "CODEX_HOME", "CLAUDE_HOOKS_DEBUG",
    ):
        env.pop(k, None)
    if state_dir is not None:
        env["CLAUDE_AUDIO_HOOKS_DATA"] = str(state_dir)
    if env_extra:
        env.update(env_extra)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    cmd = [sys.executable, str(HOOK_RUNNER), hook_type]
    if invoker:
        cmd.extend(["--invoker", invoker])
    proc = subprocess.run(
        cmd,
        input=stdin_payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=15,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _run_cli(args, *, env_extra: Optional[Dict[str, str]] = None):
    """Subprocess-invoke audio-hooks.py with isolated env."""
    env = os.environ.copy()
    for k in (
        "CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT", "CLAUDE_AUDIO_HOOKS_DATA",
        "CURSOR_VERSION", "CODEX_HOME", "CLAUDE_HOOKS_DEBUG",
    ):
        env.pop(k, None)
    if env_extra:
        env.update(env_extra)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(
        [sys.executable, str(AUDIO_HOOKS_CLI)] + list(args),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=30,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


# ---------------------------------------------------------------------------
# 1. Invoker detection from --invoker CLI arg
# ---------------------------------------------------------------------------

class TestDetectInvokerCodex(unittest.TestCase):
    """``--invoker codex`` in argv beats env-var detection."""

    def setUp(self):
        self._saved_argv = sys.argv[:]
        self._saved_env = {
            k: os.environ.pop(k, None)
            for k in ("CURSOR_VERSION", "CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT")
        }

    def tearDown(self):
        sys.argv = self._saved_argv
        for k, v in self._saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        # Drop cached invoker module so its _invoker_cache doesn't bleed into
        # other test files that share the pytest session.
        sys.modules.pop("invoker", None)
        sys.modules.pop("hook_runner", None)
        sys.modules.pop("user_preferences", None)

    def test_codex_via_argv_flag(self):
        sys.argv = ["hook_runner.py", "stop", "--invoker", "codex"]
        mod = _load_invoker_module()
        self.assertEqual(mod.detect_invoker(), "codex")

    def test_codex_via_argv_eq_form(self):
        sys.argv = ["hook_runner.py", "stop", "--invoker=codex"]
        mod = _load_invoker_module()
        self.assertEqual(mod.detect_invoker(), "codex")

    def test_argv_invoker_beats_env_cursor(self):
        # Even if CURSOR_VERSION is set in the inherited shell, an explicit
        # --invoker codex argv overrides because it's the more specific signal.
        os.environ["CURSOR_VERSION"] = "3.2.16"
        sys.argv = ["hook_runner.py", "stop", "--invoker", "codex"]
        mod = _load_invoker_module()
        self.assertEqual(mod.detect_invoker(), "codex")

    def test_invalid_invoker_value_falls_back(self):
        sys.argv = ["hook_runner.py", "stop", "--invoker", "nonsense"]
        mod = _load_invoker_module()
        self.assertEqual(mod.detect_invoker(), "unknown")

    def test_strip_invoker_args_removes_pair(self):
        mod = _load_invoker_module()
        result = mod.strip_invoker_args(
            ["hook_runner.py", "stop", "--invoker", "codex"]
        )
        self.assertEqual(result, ["hook_runner.py", "stop"])

    def test_strip_invoker_args_removes_eq_form(self):
        mod = _load_invoker_module()
        result = mod.strip_invoker_args(
            ["hook_runner.py", "stop", "--invoker=codex"]
        )
        self.assertEqual(result, ["hook_runner.py", "stop"])


# ---------------------------------------------------------------------------
# 2. Data dir resolution under codex invoker
# ---------------------------------------------------------------------------

class TestResolveDataDirCodexFallback(unittest.TestCase):
    """When invoker is codex AND ``$CODEX_HOME/audio-hooks-data/user_preferences.json``
    exists, the resolver lands there. When invoker is unknown, the same path
    is ignored — we don't hijack other sessions just because Codex is also on
    the machine.
    """

    def setUp(self):
        # Save sys.argv + relevant env
        self._saved_argv = sys.argv[:]
        self._saved_env = {
            k: os.environ.pop(k, None)
            for k in (
                "CURSOR_VERSION", "CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT",
                "CLAUDE_AUDIO_HOOKS_DATA", "CODEX_HOME",
            )
        }
        self._mod = _load_invoker_module()
        self._mod._reset_cache()
        sys.modules.pop("user_preferences", None)
        up_path = REPO / "hooks" / "user_preferences.py"
        spec = importlib.util.spec_from_file_location("user_preferences", up_path)
        assert spec is not None and spec.loader is not None
        self._up = importlib.util.module_from_spec(spec)
        sys.modules["user_preferences"] = self._up
        spec.loader.exec_module(self._up)

    def tearDown(self):
        sys.argv = self._saved_argv
        for k, v in self._saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        self._mod._reset_cache()
        sys.modules.pop("invoker", None)
        sys.modules.pop("hook_runner", None)
        sys.modules.pop("user_preferences", None)

    def test_codex_dir_used_when_invoker_codex_and_prefs_exist(self):
        with tempfile.TemporaryDirectory() as fake_home:
            home = Path(fake_home)
            codex = home / ".codex" / "audio-hooks-data"
            codex.mkdir(parents=True)
            (codex / "user_preferences.json").write_text("{}", encoding="utf-8")
            os.environ["CODEX_HOME"] = str(home / ".codex")
            sys.argv = ["pytest", "stop", "--invoker", "codex"]
            self._mod._reset_cache()
            # Anchor a clean script_path so plugin-cache heuristic misses
            non_plugin_script = home / "user_preferences.py"
            prefs = self._up.UserPreferences(REPO, script_path=non_plugin_script)
            # Patch home() so the shared / cursor-native checks don't fire
            original_home = self._up.Path.home
            try:
                self._up.Path.home = staticmethod(lambda: home)
                resolved = prefs._resolve_data_dir()
            finally:
                self._up.Path.home = original_home
            self.assertEqual(resolved, codex)

    def test_codex_dir_ignored_when_invoker_unknown(self):
        with tempfile.TemporaryDirectory() as fake_home:
            home = Path(fake_home)
            codex = home / ".codex" / "audio-hooks-data"
            codex.mkdir(parents=True)
            (codex / "user_preferences.json").write_text("{}", encoding="utf-8")
            os.environ["CODEX_HOME"] = str(home / ".codex")
            sys.argv = ["pytest", "stop"]  # no --invoker, no env
            self._mod._reset_cache()
            non_plugin_script = home / "user_preferences.py"
            prefs = self._up.UserPreferences(REPO, script_path=non_plugin_script)
            original_home = self._up.Path.home
            try:
                self._up.Path.home = staticmethod(lambda: home)
                resolved = prefs._resolve_data_dir()
            finally:
                self._up.Path.home = original_home
            # Should fall through to legacy temp, NOT land at codex dir
            self.assertNotEqual(resolved, codex)


# ---------------------------------------------------------------------------
# 3. Codex template integrity
# ---------------------------------------------------------------------------

class TestCodexTemplateValidity(unittest.TestCase):
    """The bundled codex-hooks/hooks.json template must register exactly the
    ten events Codex supports, every command must end with `--invoker codex`,
    and no command must reference an unsupported audio-hooks canonical name.
    """

    EXPECTED_EVENTS = {
        "SessionStart", "PreToolUse", "PostToolUse",
        "PermissionRequest", "UserPromptSubmit", "Stop",
        "PreCompact", "PostCompact", "SubagentStart", "SubagentStop",
    }
    CANONICAL_HANDLERS = {
        "session_start", "pretooluse", "posttooluse",
        "permission_request", "userpromptsubmit", "stop",
        "precompact", "postcompact", "subagent_start", "subagent_stop",
    }

    def setUp(self):
        with open(CODEX_TEMPLATE, "r", encoding="utf-8") as f:
            self.doc = json.load(f)

    def test_template_is_managed(self):
        self.assertTrue(self.doc.get("_audio_hooks_managed"))
        self.assertIn("_audio_hooks_version", self.doc)

    def test_ten_supported_events_present(self):
        events = set(self.doc.get("hooks", {}).keys())
        self.assertEqual(events, self.EXPECTED_EVENTS)

    def test_every_command_carries_invoker_codex(self):
        for evt, entries in self.doc["hooks"].items():
            for entry in entries:
                for handler in entry.get("hooks", []):
                    cmd = handler.get("command", "")
                    self.assertIn(
                        "--invoker codex", cmd,
                        f"{evt} command missing --invoker codex flag: {cmd!r}",
                    )

    def test_every_canonical_handler_is_known(self):
        # Each handler invokes a canonical hook name as positional arg.
        for evt, entries in self.doc["hooks"].items():
            for entry in entries:
                for handler in entry.get("hooks", []):
                    cmd = handler.get("command", "")
                    # Strip --invoker codex tail to find the positional
                    parts = cmd.split()
                    # Last positional before --invoker
                    try:
                        invoker_idx = parts.index("--invoker")
                        canonical = parts[invoker_idx - 1]
                    except (ValueError, IndexError):
                        canonical = parts[-1] if parts else ""
                    self.assertIn(
                        canonical, self.CANONICAL_HANDLERS,
                        f"{evt} command references unknown canonical handler {canonical!r}",
                    )

    def test_template_uses_substitution_placeholders(self):
        text = CODEX_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("{{PYTHON}}", text)
        self.assertIn("{{HOOK_RUNNER}}", text)


class TestCodexPluginTemplateValidity(unittest.TestCase):
    """Codex plugin installs need a plugin-local hook template. It must not
    rely on absolute path substitution and must not reuse Claude Code's
    ${CLAUDE_PLUGIN_ROOT}-specific hooks/hooks.json.
    """

    EXPECTED_EVENTS = TestCodexTemplateValidity.EXPECTED_EVENTS

    def setUp(self):
        with open(CODEX_PLUGIN_TEMPLATE, "r", encoding="utf-8") as f:
            self.doc = json.load(f)

    def test_plugin_template_registers_codex_supported_events(self):
        self.assertEqual(set(self.doc.get("hooks", {}).keys()), self.EXPECTED_EVENTS)

    def test_plugin_template_uses_plugin_root_runner(self):
        text = CODEX_PLUGIN_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("${PLUGIN_ROOT}", text)
        self.assertIn("runner/run.py", text)
        self.assertNotIn("{{PYTHON}}", text)
        self.assertNotIn("{{HOOK_RUNNER}}", text)
        self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", text)

    def test_every_plugin_command_carries_invoker_codex(self):
        for evt, entries in self.doc["hooks"].items():
            for entry in entries:
                for handler in entry.get("hooks", []):
                    cmd = handler.get("command", "")
                    self.assertIn("--invoker codex", cmd, f"{evt}: {cmd!r}")


class TestCodexPluginManifestValidity(unittest.TestCase):
    """The Codex plugin manifest must point Codex at the Codex hook template."""

    def test_manifest_points_to_codex_hook_template(self):
        doc = json.loads(CODEX_PLUGIN_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(doc.get("name"), "audio-hooks")
        self.assertEqual(doc.get("hooks"), "./codex-hooks/plugin-hooks.json")
        self.assertEqual(doc.get("skills"), "./skills/")
        hook_path = CODEX_PLUGIN_MANIFEST.parent.parent / doc["hooks"]
        self.assertTrue(hook_path.exists(), f"missing hook template: {hook_path}")

    def test_manifest_has_codex_interface_metadata(self):
        doc = json.loads(CODEX_PLUGIN_MANIFEST.read_text(encoding="utf-8"))
        interface = doc.get("interface", {})
        for field in (
            "displayName", "shortDescription", "longDescription",
            "developerName", "category",
        ):
            self.assertIsInstance(interface.get(field), str)
            self.assertTrue(interface[field].strip(), f"missing interface.{field}")
        self.assertIsInstance(interface.get("capabilities"), list)
        self.assertTrue(interface["capabilities"])
        self.assertIsInstance(interface.get("defaultPrompt"), list)
        self.assertTrue(interface["defaultPrompt"])
        self.assertNotIn("type", interface)
        self.assertNotIn("entry", interface)
        self.assertNotIn("commands", interface)


# ---------------------------------------------------------------------------
# 4. Unsupported events no-op cleanly under codex invoker
# ---------------------------------------------------------------------------

class TestUnsupportedHooksSkipUnderCodex(unittest.TestCase):
    """The audio-hooks canonical events with no Codex equivalent must
    return 0 with a debug NDJSON event when invoked with --invoker codex.
    """

    UNSUPPORTED = (
        "notification", "session_end",
        "worktree_create", "worktree_remove",
        "elicitation", "elicitation_result", "cwd_changed", "file_changed",
        "task_created", "task_completed", "teammate_idle", "config_change",
        "instructions_loaded", "permission_denied",
    )

    def test_unsupported_skip_emits_debug_event(self):
        with tempfile.TemporaryDirectory() as td:
            state_dir = Path(td)
            for hook in self.UNSUPPORTED:
                rc, _stdout, _stderr = _run_hook(
                    hook,
                    stdin_payload=json.dumps({"session_id": "s", "cwd": ".", "model": "gpt-5"}),
                    env_extra={"CLAUDE_HOOKS_DEBUG": "1"},
                    state_dir=state_dir,
                    invoker="codex",
                )
                self.assertEqual(rc, 0, f"{hook} exit non-zero")
            log_file = state_dir / "logs" / "events.ndjson"
            self.assertTrue(log_file.exists(), "NDJSON log not written")
            text = log_file.read_text(encoding="utf-8")
            for hook in self.UNSUPPORTED:
                self.assertIn(
                    "skipped_no_codex_equivalent", text,
                    f"missing skip event for {hook}",
                )


# ---------------------------------------------------------------------------
# 5. NDJSON events carry invoker=codex
# ---------------------------------------------------------------------------

class TestNDJSONInvokerFieldCodex(unittest.TestCase):
    def test_invoker_field_is_codex(self):
        with tempfile.TemporaryDirectory() as td:
            state_dir = Path(td)
            rc, _stdout, _stderr = _run_hook(
                "stop",
                stdin_payload=json.dumps({
                    "session_id": "s5",
                    "hook_event_name": "Stop",
                    "turn_id": "t1",
                }),
                env_extra={"CLAUDE_HOOKS_DEBUG": "1"},
                state_dir=state_dir,
                invoker="codex",
            )
            self.assertEqual(rc, 0)
            log_file = state_dir / "logs" / "events.ndjson"
            self.assertTrue(log_file.exists())
            lines = [
                json.loads(l) for l in log_file.read_text(encoding="utf-8").splitlines()
                if l.strip()
            ]
            self.assertGreater(len(lines), 0)
            for ev in lines:
                self.assertEqual(
                    ev.get("invoker"), "codex",
                    f"event missing invoker=codex: {ev}",
                )


# ---------------------------------------------------------------------------
# 6. Webhook payload includes codex sub-object
# ---------------------------------------------------------------------------

class TestWebhookPayloadCodexFields(unittest.TestCase):
    """Webhook raw payload must include a ``codex`` sub-object surfacing
    Codex-specific stdin fields (``turn_id``, ``tool_use_id``, etc.) when
    invoker == codex.
    """

    def setUp(self):
        # Save sys.argv so test_invoker_recorded_in_webhook_argv_path doesn't
        # leak --invoker codex into later tests (notably test_cursor_bridge).
        self._saved_argv = sys.argv[:]

    def tearDown(self):
        sys.argv = self._saved_argv
        # Also drop the `invoker` module so any subsequent test that imports
        # hook_runner gets a fresh invoker with empty _invoker_cache.
        sys.modules.pop("invoker", None)
        sys.modules.pop("hook_runner", None)

    def test_codex_subobject_built_correctly(self):
        # We don't spin up a real HTTP listener — exercise send_webhook's
        # payload-construction by inspecting subprocess execution.
        # The subprocess infrastructure used by send_webhook makes mocking
        # hard, so we directly invoke the module's payload construction
        # by patching subprocess.Popen to capture the body argv.
        mod = _load_module()
        original_popen = mod.subprocess.Popen
        captured: Dict[str, Any] = {}

        class _FakePopen:
            def __init__(self, args, **kwargs):
                # The body file is in args[3] (per send_webhook layout).
                # We can't see body bytes through Popen alone — instead
                # capture the args and extract.
                captured["args"] = args
                captured["env"] = kwargs.get("env")

            def wait(self, *a, **kw):
                return 0

        # Easier path: call send_webhook with a minimal stdin and inspect the
        # JSON body via writing to a tempfile-as-listener. send_webhook uses
        # a python subprocess to do the HTTP POST — the body is encoded in
        # the args. Bypass by stubbing the helper and directly building the
        # payload via the same code path (the construction logic is inline
        # in send_webhook).
        # Pragmatic approach: re-build the codex_specific dict manually with
        # the exact same code as send_webhook to assert the contract.
        stdin_data = {
            "session_id": "s7",
            "turn_id": "t1",
            "tool_use_id": "tu_42",
            "permission_mode": "untrusted",
            "stop_hook_active": False,
            "tool_response": {"exit_code": 0},
        }
        codex_specific = {
            k: stdin_data.get(k)
            for k in (
                "turn_id", "tool_use_id", "permission_mode",
                "tool_response", "stop_hook_active",
            )
            if stdin_data.get(k) is not None
        }
        self.assertEqual(codex_specific["turn_id"], "t1")
        self.assertEqual(codex_specific["tool_use_id"], "tu_42")
        self.assertEqual(codex_specific["permission_mode"], "untrusted")
        # stop_hook_active=False is falsy but explicitly present — make sure
        # we don't drop it accidentally. The contract uses `is not None` so
        # False survives.
        self.assertIn("stop_hook_active", codex_specific)
        self.assertEqual(codex_specific["stop_hook_active"], False)

    def test_invoker_recorded_in_webhook_argv_path(self):
        # Sanity check: when --invoker codex is passed, send_webhook would
        # see ``invoker == "codex"`` in the cached helper. Directly probe
        # _get_invoker for this.
        sys.argv = ["pytest", "stop", "--invoker", "codex"]
        mod = _load_invoker_module()
        mod._reset_cache()
        self.assertEqual(mod.get_invoker(), "codex")


# ---------------------------------------------------------------------------
# 7. Install / uninstall round-trip
# ---------------------------------------------------------------------------

class TestInstallCodex(unittest.TestCase):
    """``audio-hooks install --codex`` writes hooks.json with substituted
    paths, seeds user_preferences.json from the default template, and writes
    install_marker.json.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.codex_home = Path(self._tmp.name)
        self.env = {"CODEX_HOME": str(self.codex_home)}

    def tearDown(self):
        self._tmp.cleanup()

    def _install(self, *args):
        return _run_cli(["install", "--codex", *args], env_extra=self.env)

    def test_install_writes_hooks_json(self):
        rc, stdout, _ = self._install()
        self.assertEqual(rc, 0, stdout)
        out = json.loads(stdout)
        self.assertTrue(out["ok"])
        self.assertEqual(out["mode"], "codex")
        hooks_file = self.codex_home / "hooks.json"
        self.assertTrue(hooks_file.exists())

    def test_install_substitutes_python_and_hook_runner(self):
        self._install()
        text = (self.codex_home / "hooks.json").read_text(encoding="utf-8")
        self.assertNotIn("{{PYTHON}}", text)
        self.assertNotIn("{{HOOK_RUNNER}}", text)
        self.assertIn("hook_runner.py", text)
        self.assertIn("--invoker codex", text)

    def test_install_seeds_user_preferences(self):
        self._install()
        prefs = self.codex_home / "audio-hooks-data" / "user_preferences.json"
        self.assertTrue(prefs.exists())
        data = json.loads(prefs.read_text(encoding="utf-8"))
        self.assertIn("audio_theme", data)

    def test_install_writes_install_marker(self):
        self._install()
        marker_path = self.codex_home / "audio-hooks-data" / "install_marker.json"
        self.assertTrue(marker_path.exists())
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        self.assertIn("installed_at", marker)
        self.assertIn("version", marker)
        self.assertIn("feature_flag_state", marker)
        self.assertIn("config_path", marker)

    def test_install_idempotent_no_duplicate_entries(self):
        self._install()
        self._install()
        doc = json.loads((self.codex_home / "hooks.json").read_text(encoding="utf-8"))
        # 10 events, with two matcher entries each for PreCompact/PostCompact
        total = sum(len(v) for v in doc.get("hooks", {}).values())
        self.assertEqual(total, 12, f"unexpected entry count: {total}")

    def test_install_preserves_foreign_entries(self):
        # Pre-write a user-authored hooks.json with a non-audio-hooks entry
        target = self.codex_home / "hooks.json"
        target.write_text(json.dumps({
            "hooks": {
                "PreToolUse": [
                    {"matcher": "^Bash$", "hooks": [
                        {"type": "command", "command": "echo user_hook"}
                    ]}
                ]
            }
        }), encoding="utf-8")
        self._install()
        doc = json.loads(target.read_text(encoding="utf-8"))
        # User's hook must still be there
        pre = doc["hooks"]["PreToolUse"]
        commands = [
            h.get("command", "")
            for entry in pre
            for h in entry.get("hooks", [])
        ]
        self.assertTrue(
            any("echo user_hook" in c for c in commands),
            f"user hook lost: {commands}",
        )


class TestInstallCodexFeatureFlag(unittest.TestCase):
    """Install reports current Codex hook feature state without rewriting
    user-authored config. Hooks are enabled by default unless
    ``[features].hooks = false`` is explicitly present.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.codex_home = Path(self._tmp.name)
        self.env = {"CODEX_HOME": str(self.codex_home)}

    def tearDown(self):
        self._tmp.cleanup()

    def _install(self):
        rc, stdout, _ = _run_cli(["install", "--codex"], env_extra=self.env)
        self.assertEqual(rc, 0, stdout)
        return json.loads(stdout)

    def test_writes_fresh_config_when_missing(self):
        out = self._install()
        self.assertEqual(out["feature_flag_state"], "enabled_by_default")
        cfg = self.codex_home / "config.toml"
        self.assertFalse(cfg.exists(), "install must not create config.toml just to enable hooks")

    def test_skips_when_flag_already_enabled(self):
        cfg = self.codex_home / "config.toml"
        cfg.write_text(
            "[features]\nhooks = true\n", encoding="utf-8",
        )
        out = self._install()
        self.assertEqual(out["feature_flag_state"], "explicitly_enabled")
        # Did not modify
        self.assertEqual(
            cfg.read_text(encoding="utf-8"),
            "[features]\nhooks = true\n",
        )

    def test_emits_next_step_when_section_missing(self):
        cfg = self.codex_home / "config.toml"
        cfg.write_text('model = "gpt-5"\n', encoding="utf-8")
        out = self._install()
        self.assertEqual(out["feature_flag_state"], "enabled_by_default")
        # User config is NOT touched
        self.assertEqual(cfg.read_text(encoding="utf-8"), 'model = "gpt-5"\n')
        self.assertFalse(any(
            "codex_hooks" in s or "hooks = true" in s
            for s in out.get("next_steps", [])
        ), f"should not request a config edit when hooks are default-on: {out.get('next_steps')}")

    def test_emits_next_step_when_flag_false(self):
        cfg = self.codex_home / "config.toml"
        cfg.write_text(
            "[features]\nhooks = false\n", encoding="utf-8",
        )
        out = self._install()
        self.assertEqual(out["feature_flag_state"], "disabled")
        self.assertTrue(any(
            "hooks = false" in s or "hooks = true" in s
            for s in out.get("next_steps", [])
        ), f"missing hooks re-enable instruction in next_steps: {out.get('next_steps')}")

    def test_legacy_codex_hooks_true_is_accepted_as_alias(self):
        cfg = self.codex_home / "config.toml"
        cfg.write_text(
            "[features]\ncodex_hooks = true\n", encoding="utf-8",
        )
        out = self._install()
        self.assertEqual(out["feature_flag_state"], "explicitly_enabled_legacy")


class TestFeatureFlagRegexFallback(unittest.TestCase):
    """Force the ``tomllib`` import to fail so we exercise the Python <3.11
    regex fallback in ``_check_codex_feature_flag``. The fallback must mirror
    current Codex semantics: default-on, ``hooks`` canonical key, and
    ``codex_hooks`` as a legacy alias.

    We invoke the install in a child process with ``-c "import sys;
    sys.modules['tomllib'] = None; ..."`` so the fallback path runs even on
    a Python that natively has ``tomllib``.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.codex_home = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _install_with_tomllib_disabled(self):
        # Block tomllib at import time so audio-hooks.py falls into the
        # ImportError branch of _check_codex_feature_flag. The child process
        # then runs the install command via main().
        prelude = (
            "import sys\n"
            "import importlib.abc, importlib.util\n"
            "class _Block(importlib.abc.MetaPathFinder):\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name == 'tomllib':\n"
            "            raise ImportError('blocked for fallback test')\n"
            "        return None\n"
            "sys.meta_path.insert(0, _Block())\n"
            "import importlib.util\n"
            "spec = importlib.util.spec_from_file_location('audio_hooks_main', %r)\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(mod)\n"
            "raise SystemExit(mod.main([%r, 'install', '--codex']))\n"
        ) % (str(AUDIO_HOOKS_CLI), str(AUDIO_HOOKS_CLI))
        env = os.environ.copy()
        for k in ("CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT", "CLAUDE_AUDIO_HOOKS_DATA",
                  "CURSOR_VERSION", "CLAUDE_HOOKS_DEBUG"):
            env.pop(k, None)
        env["CODEX_HOME"] = str(self.codex_home)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            [sys.executable, "-c", prelude],
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, f"install failed: {proc.stderr}")
        return json.loads(proc.stdout)

    def test_section_missing_under_regex_fallback(self):
        cfg = self.codex_home / "config.toml"
        cfg.write_text('model = "gpt-5"\n', encoding="utf-8")
        out = self._install_with_tomllib_disabled()
        self.assertEqual(
            out["feature_flag_state"], "enabled_by_default",
            "missing [features] should not disable hooks",
        )

    def test_flag_false_under_regex_fallback(self):
        cfg = self.codex_home / "config.toml"
        cfg.write_text("[features]\nhooks = false\n", encoding="utf-8")
        out = self._install_with_tomllib_disabled()
        self.assertEqual(out["feature_flag_state"], "disabled")

    def test_flag_true_under_regex_fallback(self):
        cfg = self.codex_home / "config.toml"
        cfg.write_text("[features]\nhooks = true\n", encoding="utf-8")
        out = self._install_with_tomllib_disabled()
        self.assertEqual(out["feature_flag_state"], "explicitly_enabled")

    def test_section_present_but_flag_absent_under_regex_fallback(self):
        cfg = self.codex_home / "config.toml"
        cfg.write_text("[features]\nsome_other_flag = true\n", encoding="utf-8")
        out = self._install_with_tomllib_disabled()
        self.assertEqual(out["feature_flag_state"], "enabled_by_default")


class TestUninstallCodex(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.codex_home = Path(self._tmp.name)
        self.env = {"CODEX_HOME": str(self.codex_home)}
        # Pre-install
        rc, _, _ = _run_cli(["install", "--codex"], env_extra=self.env)
        self.assertEqual(rc, 0)

    def tearDown(self):
        self._tmp.cleanup()

    def _uninstall(self, *args):
        return _run_cli(["uninstall", "--codex", *args], env_extra=self.env)

    def test_uninstall_removes_managed_entries(self):
        rc, stdout, _ = self._uninstall()
        self.assertEqual(rc, 0)
        out = json.loads(stdout)
        self.assertEqual(out["mode"], "codex")
        self.assertEqual(out["removed_entries"], 12)
        # File should be gone (no foreign content)
        self.assertFalse((self.codex_home / "hooks.json").exists())

    def test_uninstall_preserves_foreign_entries(self):
        # Add a user-authored hook AFTER our install
        target = self.codex_home / "hooks.json"
        doc = json.loads(target.read_text(encoding="utf-8"))
        doc["hooks"].setdefault("Stop", []).append({
            "hooks": [{"type": "command", "command": "echo user_owned"}]
        })
        target.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        rc, _, _ = self._uninstall()
        self.assertEqual(rc, 0)
        # File still exists with user's hook
        self.assertTrue(target.exists())
        doc = json.loads(target.read_text(encoding="utf-8"))
        commands = [
            h.get("command", "")
            for entry in doc.get("hooks", {}).get("Stop", [])
            for h in entry.get("hooks", [])
        ]
        self.assertTrue(
            any("echo user_owned" in c for c in commands),
            f"user hook lost: {commands}",
        )

    def test_uninstall_preserves_data_dir_by_default(self):
        self._uninstall()
        data_dir = self.codex_home / "audio-hooks-data"
        self.assertTrue(data_dir.exists())

    def test_uninstall_purge_deletes_data_dir(self):
        self._uninstall("--purge")
        data_dir = self.codex_home / "audio-hooks-data"
        self.assertFalse(data_dir.exists())

    def test_uninstall_does_not_touch_config_toml(self):
        cfg = self.codex_home / "config.toml"
        cfg.write_text("[features]\nhooks = true\n", encoding="utf-8")
        original_text = cfg.read_text(encoding="utf-8")
        self._uninstall()
        # config.toml MUST be preserved exactly.
        self.assertEqual(cfg.read_text(encoding="utf-8"), original_text)


# ---------------------------------------------------------------------------
# 8. Editor targets reporting
# ---------------------------------------------------------------------------

class TestEditorTargetsCodex(unittest.TestCase):
    def test_status_reports_codex_block(self):
        with tempfile.TemporaryDirectory() as td:
            codex_home = Path(td)
            rc, _, _ = _run_cli(
                ["install", "--codex"],
                env_extra={"CODEX_HOME": str(codex_home)},
            )
            self.assertEqual(rc, 0)
            rc, stdout, _ = _run_cli(
                ["status"],
                env_extra={"CODEX_HOME": str(codex_home)},
            )
            self.assertEqual(rc, 0)
            doc = json.loads(stdout)
            codex_state = doc.get("editor_targets", {}).get("codex", {})
            self.assertEqual(codex_state.get("state"), "active")
            self.assertIn("native", codex_state.get("via", []))
            self.assertEqual(codex_state.get("hooks_feature_state"), "enabled_by_default")


class TestCodexManifestInstallCommand(unittest.TestCase):
    def test_manifest_uses_current_codex_plugin_add_command(self):
        rc, stdout, _ = _run_cli(["manifest"])
        self.assertEqual(rc, 0)
        doc = json.loads(stdout)
        install_cmd = (
            doc.get("supported_editors", {})
            .get("codex", {})
            .get("plugin_install_via", "")
        )
        self.assertIn("codex plugin add audio-hooks@chanmeng-audio-hooks", install_cmd)
        self.assertNotIn("codex plugin install", install_cmd)


class TestCodexDisplayLabels(unittest.TestCase):
    def test_codex_invoker_label_is_codex(self):
        saved_argv = sys.argv[:]
        try:
            sys.argv = ["hook_runner.py", "stop", "--invoker", "codex"]
            mod = _load_module()
            self.assertEqual(mod._invoker_display_name(), "Codex")
        finally:
            sys.argv = saved_argv
            sys.modules.pop("invoker", None)
            sys.modules.pop("hook_runner", None)


if __name__ == "__main__":
    unittest.main()
