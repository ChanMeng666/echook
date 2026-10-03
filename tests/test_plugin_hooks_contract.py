"""Contract tests for the Claude Code hook registration template.

``plugins/audio-hooks/hooks/hooks.json`` is the one hand-maintained file that
lives inside an otherwise generated directory: ``scripts/build-plugin.sh``
mirrors ``/hooks/``, ``/bin/``, ``/config/``, ``/audio/``, ``/cursor-hooks/``
and ``/codex-hooks/`` into ``plugins/audio-hooks/``, but it does **not** touch
``plugins/audio-hooks/hooks/hooks.json`` and there is no repo-root counterpart.
It therefore has to be edited by hand and, until this module existed, had zero
test coverage.

That mattered because the registration → runtime linkage is by naming
convention alone, with nothing validating it end to end::

    hooks.json matcher "idle_prompt"
      → command arg "notification_idle_prompt"
      → SYNTHETIC_EVENT_MAP["notification_idle_prompt"]
      → ("notification", "notif-idle-prompt.mp3")

A typo anywhere in that chain fails silently: ``_resolve_synthetic_event``
passes an unknown arg straight through, ``run_hook`` receives a hook type
nothing recognises, and the event is a no-op. No crash, no log line anyone
reads — just a hook that never fires again.

This module pins the invariants that make such a break loud:

  1. Every command arg in the template resolves to a synthetic variant or a
     canonical ``HOOK_CATALOG`` entry.
  2. Every ``SYNTHETIC_EVENT_MAP`` key is either registered in the template or
     listed in ``INTENTIONALLY_UNREGISTERED`` with a stated reason.
  3. Every audio override names a file that exists in *both* themes.
  4. Every ``HOOK_CATALOG`` entry has a matching ``enabled_hooks`` default in
     ``config/default_preferences.json`` (complements
     ``tests/test_defaults_stability.py``, which only guards flips of keys that
     already exist).

Sibling contract tests for the other two editors live in
``tests/test_cursor_bridge.py::TestCursorTemplateValidity`` and
``tests/test_codex_hooks.py::TestCodexTemplateValidity``.

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
import re
import unittest
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

REPO = Path(__file__).resolve().parent.parent
CC_TEMPLATE = REPO / "plugins" / "audio-hooks" / "hooks" / "hooks.json"
HOOK_RUNNER = REPO / "hooks" / "hook_runner.py"
AUDIO_HOOKS_CLI = REPO / "bin" / "audio-hooks.py"
DEFAULT_PREFS = REPO / "config" / "default_preferences.json"
AUDIO_DIR = REPO / "audio"

# Synthetic variants that exist in SYNTHETIC_EVENT_MAP but are deliberately not
# registered as their own matcher in hooks.json. Each needs a reason, because
# the default assumption for an unregistered key is "this is a bug".
INTENTIONALLY_UNREGISTERED: Dict[str, str] = {
    # Empty since v6.4.1. Until then StopFailure collapsed five of its error
    # types onto one "stop_failure_other" handler, so their per-variant toggles
    # silently did nothing while `hooks list --variants` advertised them as
    # switchable. v6.4.1 registers one handler per real upstream error_type and
    # drops "other", which was never a Claude Code value in the first place.
    #
    # Keep this dict as the escape hatch it is: anything added here must state
    # why the variant exists in SYNTHETIC_EVENT_MAP but is deliberately not
    # reachable from the template.
}

# The command string format is:
#   python "${CLAUDE_PLUGIN_ROOT}/runner/run.py" <arg>
_ARG_RE = re.compile(r'run\.py"?\s+([a-z_]+)')


def _load_hook_runner():
    spec = importlib.util.spec_from_file_location("hook_runner", HOOK_RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hook_catalog() -> List[Dict[str, Any]]:
    """Read HOOK_CATALOG without importing the CLI.

    ``bin/audio-hooks.py`` runs argparse and touches the filesystem at import
    time, so parsing the literal is both cheaper and less brittle here.
    """
    src = AUDIO_HOOKS_CLI.read_text(encoding="utf-8")
    entries = re.findall(
        r'\{"name":\s*"([a-z_]+)",\s*"default":\s*(True|False),\s*"audio":\s*"([^"]+)"',
        src,
    )
    return [{"name": n, "default": d == "True", "audio": a} for n, d, a in entries]


class TestClaudeCodeTemplateContract(unittest.TestCase):
    """``plugins/audio-hooks/hooks/hooks.json`` is a contract between the
    matcher strings Claude Code fires on and the handlers hook_runner exposes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.template: Dict[str, Any] = json.loads(CC_TEMPLATE.read_text(encoding="utf-8"))
        cls.runner = _load_hook_runner()
        cls.catalog = _hook_catalog()
        cls.canonical: Set[str] = {h["name"] for h in cls.catalog}
        cls.synthetic: Dict[str, Tuple[str, Any]] = dict(cls.runner.SYNTHETIC_EVENT_MAP)
        cls.registered_args: Set[str] = set()
        for groups in cls.template.get("hooks", {}).values():
            for group in groups:
                for handler in group.get("hooks", []):
                    match = _ARG_RE.search(handler.get("command", ""))
                    if match:
                        cls.registered_args.add(match.group(1))

    def test_template_is_valid_json_with_hooks_block(self) -> None:
        self.assertIn("hooks", self.template)
        self.assertIsInstance(self.template["hooks"], dict)
        self.assertTrue(self.template["hooks"], "hooks block must not be empty")

    def test_every_handler_command_parses_to_an_arg(self) -> None:
        for event, groups in self.template["hooks"].items():
            for group in groups:
                for handler in group.get("hooks", []):
                    command = handler.get("command", "")
                    self.assertRegex(
                        command,
                        _ARG_RE,
                        f"{event}: command does not pass a hook arg to run.py: {command!r}",
                    )

    def test_every_command_arg_resolves(self) -> None:
        """An unresolvable arg is the silent-death case: hook_runner accepts it,
        logs nothing a user reads, and the event never fires again."""
        for arg in sorted(self.registered_args):
            self.assertTrue(
                arg in self.synthetic or arg in self.canonical,
                f"hooks.json registers {arg!r}, which is neither a "
                f"SYNTHETIC_EVENT_MAP key nor a HOOK_CATALOG name — it will "
                f"fall through _resolve_synthetic_event and no-op",
            )

    def test_every_synthetic_key_is_registered_or_allowlisted(self) -> None:
        """A synthetic variant nothing invokes is dead code."""
        for key in sorted(self.synthetic):
            if key in self.registered_args or key in INTENTIONALLY_UNREGISTERED:
                continue
            self.fail(
                f"SYNTHETIC_EVENT_MAP defines {key!r} but no hooks.json handler "
                f"passes that arg — it is unreachable. Either register a matcher "
                f"for it or add it to INTENTIONALLY_UNREGISTERED with a reason."
            )

    def test_allowlist_has_no_stale_entries(self) -> None:
        """Keep the allowlist honest: an entry that is registered, or that no
        longer exists in the map, is a leftover."""
        for key in sorted(INTENTIONALLY_UNREGISTERED):
            self.assertIn(
                key, self.synthetic,
                f"INTENTIONALLY_UNREGISTERED lists {key!r}, which is no longer "
                f"in SYNTHETIC_EVENT_MAP — drop it",
            )
            self.assertNotIn(
                key, self.registered_args,
                f"INTENTIONALLY_UNREGISTERED lists {key!r}, but hooks.json does "
                f"register it — drop it from the allowlist",
            )

    def test_every_audio_override_exists_in_both_themes(self) -> None:
        """``get_audio_file`` derives the custom-theme path by prefixing
        ``chime-``. A missing sibling silently degrades to the parent hook's
        sound, so theme fidelity is only guaranteed if both files exist."""
        for key, (_canonical, override) in sorted(self.synthetic.items()):
            if not override:
                continue
            default_path = AUDIO_DIR / "default" / override
            custom_path = AUDIO_DIR / "custom" / f"chime-{override}"
            self.assertTrue(
                default_path.is_file(),
                f"{key!r} overrides audio with {override!r} but "
                f"audio/default/{override} does not exist",
            )
            self.assertTrue(
                custom_path.is_file(),
                f"{key!r} overrides audio with {override!r} but "
                f"audio/custom/chime-{override} does not exist",
            )

    def test_every_notification_subtype_has_its_own_wording(self) -> None:
        """Each registered notification_type needs a label. Without one it
        falls into the generic branch and the user is told something vague
        about an event we do in fact recognise."""
        labels = self.runner.NOTIFICATION_TYPE_LABELS
        for group in self.template["hooks"].get("Notification", []):
            matcher = group.get("matcher", "")
            if not matcher:
                continue  # catch-all handler, no single type to word
            for notification_type in matcher.split("|"):
                self.assertIn(
                    notification_type, labels,
                    f"hooks.json registers Notification matcher "
                    f"{notification_type!r} but NOTIFICATION_TYPE_LABELS has no "
                    f"entry for it",
                )

    def test_notification_labels_match_registered_matchers(self) -> None:
        """The reverse direction: a label for a type we never register is
        either a typo or a matcher someone forgot to add."""
        registered = set()
        for group in self.template["hooks"].get("Notification", []):
            matcher = group.get("matcher", "")
            registered.update(t for t in matcher.split("|") if t)
        for notification_type in self.runner.NOTIFICATION_TYPE_LABELS:
            self.assertIn(
                notification_type, registered,
                f"NOTIFICATION_TYPE_LABELS words {notification_type!r} but "
                f"hooks.json never registers that matcher",
            )

    def test_every_synthetic_parent_is_a_canonical_hook(self) -> None:
        for key, (canonical, _override) in sorted(self.synthetic.items()):
            self.assertIn(
                canonical, self.canonical,
                f"{key!r} maps to parent hook {canonical!r}, which is not in "
                f"HOOK_CATALOG",
            )


class TestCatalogPreferencesContract(unittest.TestCase):
    """``HOOK_CATALOG`` and ``config/default_preferences.json`` must agree.

    ``tests/test_defaults_stability.py`` guards *flips* of existing keys; this
    guards *absence* and *disagreement*, which that test cannot see.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = _hook_catalog()
        prefs = json.loads(DEFAULT_PREFS.read_text(encoding="utf-8"))
        cls.enabled: Dict[str, Any] = prefs["enabled_hooks"]

    def test_catalog_is_not_empty(self) -> None:
        self.assertGreater(len(self.catalog), 30, "HOOK_CATALOG failed to parse")

    def test_every_catalog_hook_has_a_template_default(self) -> None:
        for hook in self.catalog:
            self.assertIn(
                hook["name"], self.enabled,
                f"HOOK_CATALOG lists {hook['name']!r} but "
                f"config/default_preferences.json has no enabled_hooks entry — "
                f"new installs would fall back to the built-in default set",
            )

    def test_catalog_defaults_match_template_defaults(self) -> None:
        for hook in self.catalog:
            if hook["name"] not in self.enabled:
                continue  # reported by the test above
            self.assertIs(
                self.enabled[hook["name"]], hook["default"],
                f"{hook['name']!r}: HOOK_CATALOG default is {hook['default']} "
                f"but default_preferences.json says "
                f"{self.enabled[hook['name']]} — `hooks list` would report a "
                f"state new installs do not actually get",
            )

    def test_template_has_no_hooks_missing_from_catalog(self) -> None:
        names = {h["name"] for h in self.catalog}
        for key, value in self.enabled.items():
            if key.startswith("_"):
                continue  # _comment_* documentation keys
            self.assertIn(
                key, names,
                f"default_preferences.json enables {key!r}, which is not in "
                f"HOOK_CATALOG — `hooks enable/disable` would reject it",
            )


if __name__ == "__main__":
    unittest.main()



class TestManifestEditorSurface(unittest.TestCase):
    """``supported_editors["claude-code"].events`` must equal what we register.

    CLAUDE.md tells operators the manifest is the live source of truth, so a
    wrong list here is not cosmetic — it is what an agent reads before deciding
    what echook can do. Until v6.4.1 this field was derived from
    ``HOOK_CATALOG`` and therefore claimed all 37 canonical events, including
    the nine Cursor-only ones the Claude Code template never registers.

    This also anchors drift detection: if a later edit adds an event to the
    catalog but forgets the template (or the reverse), the sets diverge here.
    """

    CURSOR_ONLY = {
        "shell_before", "shell_after", "mcp_before", "mcp_after",
        "file_read", "agent_response", "agent_thinking",
        "workspace_open", "tab_file_edit",
    }

    @classmethod
    def setUpClass(cls) -> None:
        template: Dict[str, Any] = json.loads(CC_TEMPLATE.read_text(encoding="utf-8"))
        runner = _load_hook_runner()
        synthetic = dict(runner.SYNTHETIC_EVENT_MAP)
        cls.registered: Set[str] = set()
        for groups in template.get("hooks", {}).values():
            for group in groups:
                for handler in group.get("hooks", []):
                    match = _ARG_RE.search(handler.get("command", ""))
                    if not match:
                        continue
                    arg = match.group(1)
                    canonical, _audio = synthetic.get(arg, (arg, None))
                    cls.registered.add(canonical)

    def test_cursor_only_events_are_not_registered_with_claude_code(self) -> None:
        """The 37-vs-28 bug in one assertion.

        These nine events exist only in Cursor's hook surface. Claude Code has
        no equivalent, so registering — or advertising — them here is wrong.
        """
        self.assertEqual(
            self.CURSOR_ONLY & self.registered,
            set(),
            "Cursor-only events must not appear in the Claude Code template",
        )

    def test_registered_events_are_all_canonical(self) -> None:
        canonical = {h["name"] for h in _hook_catalog()}
        self.assertTrue(
            self.registered <= canonical,
            "template registers events absent from HOOK_CATALOG: "
            f"{sorted(self.registered - canonical)}",
        )

    def test_session_start_fork_is_registered(self) -> None:
        """Claude Code 2.1.213 reports ``fork`` where it used to report
        ``resume``. Missing this matcher makes forked sessions silent, which is
        exactly the class of upstream drift nothing else in CI would catch."""
        template: Dict[str, Any] = json.loads(CC_TEMPLATE.read_text(encoding="utf-8"))
        matchers = {
            group.get("matcher")
            for group in template["hooks"]["SessionStart"]
        }
        self.assertIn("fork", matchers)

    def test_stop_failure_registers_only_real_upstream_types(self) -> None:
        """Every StopFailure matcher must be a real Claude Code ``error_type``.

        ``other`` was never one — it was echook's own invention for a collapsed
        handler, so it could never fire.
        """
        upstream = {
            "authentication_failed", "oauth_org_not_allowed", "account_on_hold",
            "billing_error", "rate_limit", "overloaded", "invalid_request",
            "model_not_found", "server_error", "unknown", "max_output_tokens",
            # v6.7: cloud_credential_error is documented (v2.1.267+);
            # verification_required is in the 2.1.288 binary's error union.
            "cloud_credential_error", "verification_required",
        }
        template: Dict[str, Any] = json.loads(CC_TEMPLATE.read_text(encoding="utf-8"))
        registered = set()
        for group in template["hooks"]["StopFailure"]:
            registered.update(str(group.get("matcher", "")).split("|"))
        self.assertEqual(
            registered - upstream,
            set(),
            "StopFailure registers matcher values Claude Code never emits",
        )


class TestV670Variants(unittest.TestCase):
    """The three matcher values Claude Code sent that had no handler.

    ``hooks.json`` has no catch-all under ``Notification`` or ``StopFailure``, so
    a matcher value with no entry is a permanently silent event. ``cloud_credential_error``
    is documented (Claude Code v2.1.267+); ``verification_required`` and
    ``auth_storage_failure`` come from the 2.1.288 binary.
    """

    NEW = {
        ("StopFailure", "cloud_credential_error"):
            ("stop_failure_cloud_credential_error", "stop_failure", "fail-cloud-credential.mp3"),
        ("StopFailure", "verification_required"):
            ("stop_failure_verification_required", "stop_failure", "fail-verification.mp3"),
        ("Notification", "auth_storage_failure"):
            ("notification_auth_storage_failure", "notification", "notif-auth-storage.mp3"),
    }

    @classmethod
    def setUpClass(cls) -> None:
        cls.template: Dict[str, Any] = json.loads(CC_TEMPLATE.read_text(encoding="utf-8"))
        cls.runner = _load_hook_runner()

    def test_each_matcher_is_registered_and_resolves(self) -> None:
        for (event, matcher), (arg, parent, audio) in self.NEW.items():
            with self.subTest(event=event, matcher=matcher):
                groups = [g for g in self.template["hooks"][event] if g.get("matcher") == matcher]
                self.assertEqual(len(groups), 1, f"{event} matcher {matcher!r} not registered once")
                commands = [h["command"] for h in groups[0]["hooks"]]
                self.assertEqual(len(commands), 1)
                self.assertEqual(_ARG_RE.search(commands[0]).group(1), arg)
                self.assertEqual(
                    self.runner._resolve_synthetic_event(arg), (parent, audio, arg))

    def test_handler_shape_matches_siblings(self) -> None:
        for (event, matcher) in self.NEW:
            sibling = next(
                g for g in self.template["hooks"][event]
                if g.get("matcher") not in (matcher, "", None)
            )
            group = next(g for g in self.template["hooks"][event] if g.get("matcher") == matcher)
            mine, theirs = group["hooks"][0], sibling["hooks"][0]
            for key in ("type", "async", "timeout"):
                self.assertEqual(mine[key], theirs[key], f"{matcher}: {key} differs from siblings")

    def test_notification_variant_defaults_off_and_stop_failure_inherits(self) -> None:
        defaults = self.runner.SYNTHETIC_VARIANT_DEFAULTS
        # `notification` is on by default, so its new variant must be opt-in.
        self.assertIs(defaults.get("notification_auth_storage_failure"), False)
        # `stop_failure` is off by default; its eleven existing variants carry no
        # entry and inherit it, so the new ones must not either.
        self.assertNotIn("stop_failure_cloud_credential_error", defaults)
        self.assertNotIn("stop_failure_verification_required", defaults)

    def test_notification_label(self) -> None:
        self.assertEqual(
            self.runner.NOTIFICATION_TYPE_LABELS["auth_storage_failure"],
            "Login needs attention")

    def test_model_refusal_fallback_is_not_registered(self) -> None:
        """Declared in the 2.1.288 type list, but no emitter was found."""
        self.assertNotIn("stop_failure_model_refusal_fallback", self.runner.SYNTHETIC_EVENT_MAP)
        self.assertNotIn("model_refusal_fallback", json.dumps(self.template))


class TestAudioUniqueness(unittest.TestCase):
    """No two events or variants may share a sound.

    The point of 39 events and 47 independently switchable variants is that you
    can tell them apart by ear. Before v6.5.1 eleven files were shared by up to
    seven slots each — `notification-urgent.mp3` covered `notification` plus six
    variants, so four different rate-limit and auth failures were audibly the
    same event. Every slot now owns exactly one file.

    This also guards the cheap regression: adding an event by copying a
    neighbouring line and forgetting to change the audio filename.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = _load_hook_runner()
        cls.catalog = _hook_catalog()
        cls.users: Dict[str, List[str]] = {}
        for entry in cls.catalog:
            cls.users.setdefault(entry["audio"], []).append(f"event:{entry['name']}")
        for variant, (_parent, override) in cls.runner.SYNTHETIC_EVENT_MAP.items():
            if override:
                cls.users.setdefault(override, []).append(f"variant:{variant}")

    def test_every_variant_has_its_own_audio_override(self) -> None:
        """A variant with no override inherits its parent's sound, which makes
        its independent toggle audibly indistinguishable from the parent."""
        inheriting = [
            v for v, (_p, o) in self.runner.SYNTHETIC_EVENT_MAP.items() if not o
        ]
        self.assertEqual(inheriting, [], "variants without their own sound")

    def test_no_audio_file_is_shared(self) -> None:
        shared = {f: u for f, u in self.users.items() if len(u) > 1}
        self.assertEqual(shared, {}, f"audio shared by multiple slots: {shared}")

    def test_every_referenced_file_exists_in_both_themes(self) -> None:
        for filename in self.users:
            self.assertTrue(
                (AUDIO_DIR / "default" / filename).exists(),
                f"missing audio/default/{filename}",
            )
            self.assertTrue(
                (AUDIO_DIR / "custom" / f"chime-{filename}").exists(),
                f"missing audio/custom/chime-{filename}",
            )

    def test_manifest_can_regenerate_every_referenced_file(self) -> None:
        """`scripts/generate-audio.py` only knows what is in the manifest.
        A file it cannot regenerate is one a voice or theme refresh would
        silently leave behind at the old voice."""
        manifest = json.loads(
            (REPO / "config" / "audio_manifest.json").read_text(encoding="utf-8")
        )
        known = {entry["filename"] for entry in manifest["files"]}
        missing = sorted(
            f for f in self.users
            if f not in known or f"chime-{f}" not in known
        )
        self.assertEqual(missing, [], "files absent from audio_manifest.json")

    def test_manifest_entries_are_individually_distinct(self) -> None:
        """Two entries with the same prompt produce the same audio, which
        defeats the whole exercise even though the filenames differ."""
        manifest = json.loads(
            (REPO / "config" / "audio_manifest.json").read_text(encoding="utf-8")
        )
        for kind in ("voice", "sound_effect"):
            texts = [e["text"] for e in manifest["files"] if e["type"] == kind]
            duplicates = {t for t in texts if texts.count(t) > 1}
            self.assertEqual(duplicates, set(), f"duplicate {kind} prompts")
