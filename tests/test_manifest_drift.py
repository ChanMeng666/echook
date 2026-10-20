"""``audio-hooks manifest`` must list every flag and config key that really exists.

AGENTS.md calls the manifest the live truth for agents, yet through 7.1.0 it
left out ``hooks list --variants``, four ``tts set`` / ``rate-limits set`` /
``webhook set`` flags and seven live config keys (``filters.<hook>.min_duration_ms``,
``notification_settings.terminal_sequence.*``, ``webhook_settings.headers``,
``tts_settings.messages.<hook>``, ``rate_limit_alerts.audio``,
``audio_files.<hook>``). An agent that trusts the manifest could not find them.

Two directions are checked:

* flags: the flags each subcommand's parser accepts (the named constants it
  hands to ``_check_args`` or tests against) equal the flags in its manifest
  ``args``, and no ``_check_args`` call takes an inline flag tuple this test
  cannot see;
* config keys: every leaf of ``config/default_preferences.json`` is covered by a
  ``manifest.config_keys`` entry, and every entry is either in the template or
  on the short, reasoned list of runner-read keys the template does not ship.

Run with::

    python -m unittest discover tests
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import ast
import contextlib
import importlib.util
import io
import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "bin" / "audio-hooks.py"
CLI_SOURCES = [CLI] + sorted((REPO / "bin" / "echook_cli").glob("*.py"))
TEMPLATE = REPO / "config" / "default_preferences.json"
FLAG = re.compile(r"--[a-z][a-z0-9-]*")

# Template keys that are deliberately not in manifest.config_keys.
# Empty: the one such key, ``notification_settings.show_context``, was read by
# no code and was dropped from the template after 7.1.0 (``DROPPED_KEYS``).
NOT_LIVE: dict = {}

# manifest.config_keys entries with no template leaf. Each is read by the runner.
NOT_IN_TEMPLATE = {
    "filters.<hook_name>.<field_name>": "hook_runner.should_filter, regex on a stdin field",
    "filters.<hook_name>.<field_name>_exclude": "hook_runner.should_filter, inverted regex",
    "filters.<hook_name>.min_duration_ms": "hook_runner.should_filter",
    "filters.stop.skip_if_background_tasks_running": "hook_runner.should_filter",
    "filters.stop.skip_if_session_crons_scheduled": "hook_runner.should_filter",
    "filters.stop.skip_if_aborted": "hook_runner.should_filter",
    "filters.stop.error_as_stop_failure": "hook_runner._stop_error_reroute_enabled",
    "audio_files.<hook_name>": "hook_runner.get_audio_file, a path under audio/",
    "enabled_hooks.<variant_name>": "hook_runner.is_hook_enabled; variants ship as SYNTHETIC_VARIANT_DEFAULTS",
}

# Flags a parser accepts but the manifest does not list in ``args``, or the reverse.
FLAG_EXCEPTIONS = {
    # Accepted only to answer SCRIPT_INSTALL_REMOVED; the manifest description says so.
    ("install", "--scripts"): "removed in 7.0.0, described in the entry's description",
    # cmd_update ignores every argument (a reporting command; see PROJECT_STATUS "Known limits").
    ("update", "--check"): "listed for compatibility; update only reports and reads no flag",
}


def _load_cli():
    sys.modules.pop("audio_hooks_cli", None)
    spec = importlib.util.spec_from_file_location("audio_hooks_cli", CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["audio_hooks_cli"] = module
    spec.loader.exec_module(module)
    return module


def _hyphen_spellings(flags):
    """``tts set`` / ``rate-limits set`` also accept ``--five_hour_thresholds``; the manifest lists the hyphen form."""
    return {f for f in flags if "_" not in f}


def _template_leaves(node, prefix=""):
    """Dotted paths of the template's settings, skipping metadata and comments.

    An object with no settings of its own (``per_hook: {}``, ``filters`` with only
    a comment) is a leaf: a free-form map the manifest covers with a placeholder.
    """
    out = []
    for key, value in node.items():
        if key.startswith(("_", "$")) or key == "version":
            continue
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and any(not k.startswith("_") for k in value):
            out.extend(_template_leaves(value, path))
        else:
            out.append(path)
    return out


def _pattern(key: str) -> "re.Pattern[str]":
    parts = [r"[^.]+" if p.startswith("<") and p.endswith(">") else re.escape(p)
             for p in key.split(".")]
    return re.compile(r"\.".join(parts))


def _covered(leaf: str, config_keys) -> bool:
    for key in config_keys:
        pat = _pattern(key)
        if pat.fullmatch(leaf):
            return True
        # A free-form object such as notification_settings.per_hook is covered by
        # notification_settings.per_hook.<hook_name>.
        if key.startswith(leaf + ".<"):
            return True
    return False


class TestManifestDrift(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cli = _load_cli()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cls.cli.cmd_manifest([])
        cls.manifest = json.loads(out.getvalue())
        cls.template = json.loads(TEMPLATE.read_text(encoding="utf-8"))

    # -- flags --------------------------------------------------------------

    def _accepted(self):
        cli = self.cli
        return {
            "manifest": set(cli._MANIFEST_FLAGS),
            "hooks list": set(cli._HOOKS_LIST_FLAGS),
            "webhook set": set(cli._WEBHOOK_SET_FLAGS),
            "tts set": _hyphen_spellings(cli._TTS_SET_FLAGS),
            "rate-limits set": _hyphen_spellings(cli._RATE_LIMITS_SET_FLAGS),
            "logs tail": set(cli._LOGS_TAIL_FLAGS),
            "statusline codex preview": set(cli.statusline.CODEX_STATUSLINE_FLAGS),
            "statusline codex apply": set(cli.statusline.CODEX_STATUSLINE_FLAGS),
            "upgrade": set(cli.plugin_upgrade.UPGRADE_FLAGS),
            "install": set(cli._INSTALL_MODE_FLAGS) | set(cli._INSTALL_EXTRA_FLAGS) | {"--help"},
            "uninstall": set(cli._INSTALL_MODE_FLAGS) | set(cli._UNINSTALL_EXTRA_FLAGS) | {"--help"},
            "update": set(),
        }

    def test_every_subcommand_lists_exactly_the_flags_it_accepts(self) -> None:
        accepted = self._accepted()
        listed = {e["name"]: set(FLAG.findall(" ".join(e["args"]))) for e in self.manifest["subcommands"]}
        for name, flags in listed.items():
            with self.subTest(subcommand=name):
                if name not in accepted:
                    self.assertEqual(flags, set(),
                                     f"`{name}` lists flags; add its parser's flag constant to this test")
                    continue
                code = accepted[name]
                missing = {f for f in code - flags if (name, f) not in FLAG_EXCEPTIONS}
                extra = {f for f in flags - code if (name, f) not in FLAG_EXCEPTIONS}
                self.assertFalse(missing, f"`{name}` accepts {sorted(missing)} but the manifest omits them")
                self.assertFalse(extra, f"the manifest lists {sorted(extra)} for `{name}`, which its parser does not accept")
        for name in accepted:
            self.assertIn(name, listed, f"`{name}` is missing from manifest.subcommands")

    def test_flag_exceptions_are_still_true(self) -> None:
        accepted = self._accepted()
        listed = {e["name"]: e for e in self.manifest["subcommands"]}
        install = listed["install"]
        self.assertIn("--scripts", accepted["install"])
        self.assertIn("--scripts", install["description"])
        self.assertIn("--check", " ".join(listed["update"]["args"]))

    def test_no_check_args_call_takes_an_inline_flag_tuple(self) -> None:
        """A literal tuple in a ``_check_args`` call is a flag list this test cannot see."""
        for path in CLI_SOURCES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_check_args"):
                    continue
                for kw in node.keywords:
                    if kw.arg not in ("valued", "flags"):
                        continue
                    for sub in ast.walk(kw.value):
                        if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and sub.value.startswith("-"):
                            self.fail(f"{path.name}:{node.lineno}: `_check_args(..., {kw.arg}=...)` names a flag "
                                      "inline; use a named constant and add it to this test")

    # -- config keys --------------------------------------------------------

    def test_every_template_key_is_in_the_manifest(self) -> None:
        keys = self.manifest["config_keys"]
        for leaf in _template_leaves(self.template):
            with self.subTest(key=leaf):
                if leaf in NOT_LIVE:
                    self.assertFalse(_covered(leaf, keys), f"{leaf} is listed but no code reads it")
                    continue
                self.assertTrue(_covered(leaf, keys), f"{leaf} is in default_preferences.json but not in manifest.config_keys")

    def test_every_manifest_key_is_in_the_template_or_explained(self) -> None:
        leaves = _template_leaves(self.template)
        for key in self.manifest["config_keys"]:
            with self.subTest(key=key):
                if key in NOT_IN_TEMPLATE:
                    continue
                pat = _pattern(key)
                hit = any(pat.fullmatch(leaf) for leaf in leaves) or any(
                    key.startswith(leaf + ".<") for leaf in leaves)
                self.assertTrue(hit, f"manifest.config_keys has {key}, which the template does not ship; "
                                     "add it to NOT_IN_TEMPLATE with the code that reads it, or drop it")

    def test_every_template_key_is_in_the_schema(self) -> None:
        """``manifest --schema`` describes every template setting, down to the
        level where the schema stops listing ``properties`` (a free-form map)."""
        schema = self.cli._build_manifest_schema()
        for leaf in _template_leaves(self.template):
            if leaf in NOT_LIVE:
                continue
            with self.subTest(key=leaf):
                node = schema
                for part in leaf.split("."):
                    props = node.get("properties")
                    if props is None:
                        break  # a free-form object: anything below it is allowed
                    self.assertIn(part, props, f"{leaf} is in default_preferences.json but not in "
                                               "bin/echook_cli/manifest_schema.py")
                    node = props[part]

    def test_schema_lists_no_key_the_template_dropped(self) -> None:
        schema = self.cli._build_manifest_schema()
        for dotted in self.cli.UserPreferences.DROPPED_KEYS:
            with self.subTest(key=dotted):
                node = schema
                for part in dotted.split("."):
                    node = (node.get("properties") or {}).get(part)
                    if node is None:
                        break
                self.assertIsNone(node, f"{dotted} is a dropped key but the schema still describes it")

    def test_not_live_keys_are_really_unread(self) -> None:
        sources = [p for p in (REPO / "hooks").glob("*.py")] + list((REPO / "bin").rglob("*.py"))
        for key in NOT_LIVE:
            leaf = key.rsplit(".", 1)[-1]
            readers = [p.name for p in sources
                       if p.name != "manifest_schema.py" and f'"{leaf}"' in p.read_text(encoding="utf-8")]
            self.assertEqual(readers, [], f"{key} is read in {readers}; move it into manifest.config_keys")


if __name__ == "__main__":
    unittest.main()
