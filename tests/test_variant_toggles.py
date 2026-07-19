"""Tests for v6.4 per-variant hook toggles.

Before 6.4, ``is_hook_enabled`` only ever saw the canonical hook name, so every
synthetic variant of an event shared one on/off switch: a user could not ask for
permission-prompt audio without also getting idle-prompt audio. 6.4 adds an
optional ``variant`` argument with a five-tier precedence chain.

The load-bearing test here is
``TestBackwardCompatibility::test_existing_variants_unchanged_without_variant_keys``:
a config written by any pre-6.4 install contains no variant keys at all, and the
new code path must produce exactly the answer the old one did for every variant
that shipped before this release. Everything else in this file is about the new
behaviour; that one is about not breaking anyone.

Run with::

    python -m unittest discover tests
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, Optional

REPO = Path(__file__).resolve().parent.parent
HOOK_RUNNER = REPO / "hooks" / "hook_runner.py"


def _load_hook_runner():
    spec = importlib.util.spec_from_file_location("hook_runner", HOOK_RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _ConfigPatch:
    """Force ``load_config`` to return a fixture for the duration of a block."""

    def __init__(self, module, enabled_hooks: Dict[str, Any]):
        self.module = module
        self.config = {"enabled_hooks": enabled_hooks}
        self._original = None

    def __enter__(self):
        self._original = self.module.load_config
        self.module.load_config = lambda: self.config
        return self

    def __exit__(self, *exc):
        self.module.load_config = self._original
        return False


def _legacy_is_hook_enabled(config: Dict[str, Any], hook_type: str) -> bool:
    """The pre-6.4 implementation, verbatim, as the comparison oracle."""
    default_enabled = {"notification", "stop", "permission_request"}
    enabled_hooks = config.get("enabled_hooks", {})
    if hook_type in enabled_hooks:
        return enabled_hooks[hook_type] is True
    return hook_type in default_enabled


class TestVariantPrecedence(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hr = _load_hook_runner()

    def _enabled(self, enabled_hooks: Dict[str, Any], hook: str,
                 variant: Optional[str] = None) -> bool:
        with _ConfigPatch(self.hr, enabled_hooks):
            return self.hr.is_hook_enabled(hook, variant)

    def test_rule1_explicit_variant_beats_enabled_parent(self) -> None:
        self.assertFalse(self._enabled(
            {"notification": True, "notification_idle_prompt": False},
            "notification", "notification_idle_prompt"))

    def test_rule1_explicit_variant_is_not_consulted_for_siblings(self) -> None:
        # Silencing idle_prompt must leave permission_prompt alone.
        self.assertTrue(self._enabled(
            {"notification": True, "notification_idle_prompt": False},
            "notification", "notification_permission_prompt"))

    def test_rule2_disabled_parent_silences_variants(self) -> None:
        """`hooks disable notification` must actually produce silence."""
        self.assertFalse(self._enabled(
            {"notification": False}, "notification", "notification_idle_prompt"))

    def test_rule1_beats_rule2_disabled_parent(self) -> None:
        # Explicitly re-enabling one variant of a muted parent is the documented
        # escape hatch, so rule 1 has to outrank the kill switch.
        self.assertTrue(self._enabled(
            {"notification": False, "notification_idle_prompt": True},
            "notification", "notification_idle_prompt"))

    def test_rule3_variant_default_applies_under_enabled_parent(self) -> None:
        self.hr.SYNTHETIC_VARIANT_DEFAULTS["notification_probe"] = False
        try:
            self.assertFalse(self._enabled(
                {"notification": True}, "notification", "notification_probe"))
        finally:
            del self.hr.SYNTHETIC_VARIANT_DEFAULTS["notification_probe"]

    def test_rule3_is_overridden_by_explicit_variant_key(self) -> None:
        self.hr.SYNTHETIC_VARIANT_DEFAULTS["notification_probe"] = False
        try:
            self.assertTrue(self._enabled(
                {"notification": True, "notification_probe": True},
                "notification", "notification_probe"))
        finally:
            del self.hr.SYNTHETIC_VARIANT_DEFAULTS["notification_probe"]

    def test_rule4_explicit_parent_true(self) -> None:
        self.assertTrue(self._enabled(
            {"subagent_stop": True}, "subagent_stop", "subagent_stop_x"))

    def test_rule5_builtin_default_set(self) -> None:
        self.assertTrue(self._enabled({}, "stop"))
        self.assertFalse(self._enabled({}, "subagent_stop"))

    def test_variant_none_matches_single_arg_behaviour(self) -> None:
        for hooks in ({}, {"stop": False}, {"subagent_stop": True}):
            for name in ("stop", "notification", "subagent_stop", "session_end"):
                with self.subTest(hooks=hooks, hook=name):
                    self.assertIs(
                        self._enabled(hooks, name, None),
                        _legacy_is_hook_enabled({"enabled_hooks": hooks}, name),
                    )


class TestBackwardCompatibility(unittest.TestCase):
    """A pre-6.4 config has no variant keys. Prove the new chain is a no-op."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.hr = _load_hook_runner()
        # Variants that shipped before 6.4 must not appear in the new defaults
        # table, or their behaviour would change under existing users.
        cls.pre_64_variants = [
            key for key in cls.hr.SYNTHETIC_EVENT_MAP
            if key not in cls.hr.SYNTHETIC_VARIANT_DEFAULTS
        ]

    def test_there_are_pre_64_variants_to_check(self) -> None:
        self.assertGreater(len(self.pre_64_variants), 15,
                           "fixture lost its variants — the comparison below "
                           "would pass vacuously")

    def test_existing_variants_unchanged_without_variant_keys(self) -> None:
        """For every legacy variant, across a spread of realistic configs, the
        6.4 answer must equal the pre-6.4 answer."""
        fixtures = [
            {},                                             # fresh install
            {"notification": True, "stop": True,
             "permission_request": True},                   # shipped default
            {"notification": True, "stop": False,
             "permission_request": True},                   # stop muted
            {"notification": False},                        # category muted
            {"session_start": True, "stop_failure": True,
             "setup": True, "session_end": True},           # opt-ins enabled
        ]
        for enabled_hooks in fixtures:
            config = {"enabled_hooks": enabled_hooks}
            for variant in self.pre_64_variants:
                parent = self.hr.SYNTHETIC_EVENT_MAP[variant][0]
                with self.subTest(config=enabled_hooks, variant=variant):
                    with _ConfigPatch(self.hr, enabled_hooks):
                        new = self.hr.is_hook_enabled(parent, variant)
                    self.assertIs(
                        new, _legacy_is_hook_enabled(config, parent),
                        f"{variant!r} (parent {parent!r}) changed behaviour "
                        f"under config {enabled_hooks!r}",
                    )

    def test_new_variant_defaults_are_all_opt_in(self) -> None:
        """Anything added to the defaults table must be False. A True entry
        would start making noise on every existing install."""
        for variant, default in self.hr.SYNTHETIC_VARIANT_DEFAULTS.items():
            self.assertIs(default, False,
                          f"{variant!r} defaults to True — new variants ship "
                          f"opt-in, like new events do")

    def test_variant_defaults_keys_are_known_variants(self) -> None:
        for variant in self.hr.SYNTHETIC_VARIANT_DEFAULTS:
            self.assertIn(variant, self.hr.SYNTHETIC_EVENT_MAP,
                          f"{variant!r} has a default but is not in "
                          f"SYNTHETIC_EVENT_MAP")


class TestRunHookThreadsVariant(unittest.TestCase):
    """``run_hook`` must take the variant as a parameter, not read module state,
    so direct callers such as ``audio-hooks test`` gate correctly."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.hr = _load_hook_runner()

    def test_run_hook_accepts_variant_kwarg(self) -> None:
        import inspect
        sig = inspect.signature(self.hr.run_hook)
        self.assertIn("variant", sig.parameters)
        self.assertIsNone(sig.parameters["variant"].default,
                          "variant must default to None so bare canonical "
                          "invocations keep working")

    def test_disabled_variant_short_circuits(self) -> None:
        with _ConfigPatch(self.hr, {"notification": True,
                                    "notification_idle_prompt": False}):
            rc = self.hr.run_hook("notification",
                                  {"session_id": "t",
                                   "hook_event_name": "Notification"},
                                  "notification_idle_prompt")
        self.assertEqual(rc, 0)

    def test_stale_module_global_does_not_gate(self) -> None:
        """The bug this guards: _run_one_test loops over hooks calling run_hook
        directly, so a leftover _current_synthetic_variant from a previous
        iteration must not decide the next one."""
        self.hr._current_synthetic_variant = "notification_idle_prompt"
        try:
            with _ConfigPatch(self.hr, {"notification": True,
                                        "notification_idle_prompt": False}):
                # No variant passed => the disabled variant key must be ignored.
                self.assertTrue(self.hr.is_hook_enabled("notification", None))
        finally:
            self.hr._current_synthetic_variant = None


class TestMachineReadableSurface(unittest.TestCase):
    """``audio-hooks manifest`` is documented as the canonical description of
    every capability, and the bundled SKILL tells agents to consult it when
    unsure. A capability the manifest omits is, for an AI operator, a
    capability that does not exist — so variant support has to be discoverable
    there, not only in prose."""

    @classmethod
    def setUpClass(cls) -> None:
        import json
        import subprocess
        cli = str(REPO / "bin" / "audio-hooks.py")

        def run(*args):
            proc = subprocess.run(
                [sys.executable, cli, *args],
                capture_output=True, text=True, cwd=str(REPO),
            )
            return json.loads(proc.stdout)

        cls.manifest = run("manifest")
        cls.status = run("status")

    def test_manifest_lists_variants(self) -> None:
        self.assertIn("variants", self.manifest)
        self.assertGreater(len(self.manifest["variants"]), 25)

    def test_manifest_variant_entries_are_self_describing(self) -> None:
        for entry in self.manifest["variants"]:
            for field in ("name", "variant_of", "audio_file", "default"):
                self.assertIn(field, entry, f"variant entry missing {field!r}")

    def test_manifest_documents_gating_precedence(self) -> None:
        """Without this, an agent cannot predict what happens when a variant
        and its parent disagree — the one genuinely surprising rule."""
        self.assertIn("variant_gating", self.manifest)
        self.assertGreaterEqual(
            len(self.manifest["variant_gating"].get("precedence", [])), 5)

    def test_manifest_advertises_new_config_keys(self) -> None:
        keys = self.manifest["config_keys"]
        for expected in ("enabled_hooks.<variant_name>",
                         "filters.stop.skip_if_background_tasks_running"):
            self.assertIn(expected, keys, f"{expected!r} missing from config_keys")

    def test_status_surfaces_variant_overrides(self) -> None:
        self.assertIn("variants", self.status)
        for field in ("total", "overridden_count", "overridden"):
            self.assertIn(field, self.status["variants"])


if __name__ == "__main__":
    unittest.main()
