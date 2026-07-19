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
      → ("notification", "notification-info.mp3")

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
    # StopFailure collapses its five low-signal error types onto a single
    # handler (matcher "billing_error|invalid_request|server_error|
    # max_output_tokens|unknown" → arg "stop_failure_other"). The per-type keys
    # stay in the map so the audio table can distinguish them if the collapse is
    # ever unwound.
    "stop_failure_billing_error": "collapsed into stop_failure_other",
    "stop_failure_invalid_request": "collapsed into stop_failure_other",
    "stop_failure_server_error": "collapsed into stop_failure_other",
    "stop_failure_max_output_tokens": "collapsed into stop_failure_other",
    "stop_failure_unknown": "collapsed into stop_failure_other",
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
