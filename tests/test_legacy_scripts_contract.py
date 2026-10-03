"""Contract tests for the legacy script-install path.

``scripts/install-complete.sh`` and ``scripts/uninstall.sh`` each carry a
hand-written list of Claude Code event names, and nothing validated either one
until this module existed. Both had silently drifted:

  * ``install-complete.sh`` registered 20 of the 28 events the plugin template
    registers — missing the four v5.0 events (``PermissionDenied``,
    ``CwdChanged``, ``FileChanged``, ``TaskCreated``) and all four v6.2 ones
    (``Setup``, ``UserPromptExpansion``, ``PostToolBatch``, ``MessageDisplay``).
    A script install was quietly less capable than a plugin install.

  * ``uninstall.sh`` knew only 9 events, in two separate lists that disagreed
    with each other. Uninstalling therefore left 19 orphaned registrations in
    ``~/.claude/settings.json`` pointing at scripts that no longer existed —
    the worse of the two bugs, because it outlives the uninstall.

The authority is ``plugins/audio-hooks/hooks/hooks.json``: whatever the plugin
registers, the script installer must be able to register and the uninstaller
must be able to remove.

Run with::

    python -m unittest discover tests
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import json
import re
import unittest
from pathlib import Path
from typing import Set

REPO_ROOT = Path(__file__).resolve().parent.parent
CC_TEMPLATE = REPO_ROOT / "plugins" / "audio-hooks" / "hooks" / "hooks.json"
INSTALL_SH = REPO_ROOT / "scripts" / "install-complete.sh"
UNINSTALL_SH = REPO_ROOT / "scripts" / "uninstall.sh"


def _authoritative_events() -> Set[str]:
    """Claude Code event names the plugin template registers."""
    data = json.loads(CC_TEMPLATE.read_text(encoding="utf-8"))
    return set(data["hooks"].keys())


class TestLegacyScriptEventCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected = _authoritative_events()
        cls.install_src = INSTALL_SH.read_text(encoding="utf-8")
        cls.uninstall_src = UNINSTALL_SH.read_text(encoding="utf-8")

    def test_authority_is_non_trivial(self) -> None:
        """Guard the guard: if the template ever fails to parse, the other
        assertions would pass vacuously against an empty set."""
        self.assertGreaterEqual(len(self.expected), 25)

    def test_install_script_registers_every_event(self) -> None:
        block = re.search(
            r"all_hook_types\s*=\s*\{(.*?)\n    \}", self.install_src, re.DOTALL
        )
        self.assertIsNotNone(block, "all_hook_types dict not found")
        found = set(re.findall(r"'([A-Za-z]+)':\s*'[a-z_]+'", block.group(1)))
        self.assertEqual(
            self.expected - found,
            set(),
            "install-complete.sh cannot register events the plugin registers",
        )

    def test_uninstall_covers_every_event(self) -> None:
        """The removal lives in the CLI (scripts/uninstall.sh delegates to it), so
        the CLI's event list is the one that must cover the plugin's events."""
        cli = _load_cli()
        self.assertEqual(
            self.expected - set(cli.LEGACY_HOOK_EVENTS),
            set(),
            "audio-hooks uninstall would leave orphaned hook registrations behind",
        )


def _load_cli():
    import importlib.util
    import sys
    sys.modules.pop("audio_hooks_cli", None)
    spec = importlib.util.spec_from_file_location("audio_hooks_cli", REPO_ROOT / "bin" / "audio-hooks.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["audio_hooks_cli"] = module
    spec.loader.exec_module(module)
    return module


class TestUninstallScriptIsAThinWrapper(unittest.TestCase):
    """``scripts/uninstall.sh`` used to carry its own copy of the matching rule,
    the name lists and the deletion logic, and the copies drifted. It is now a
    wrapper around ``audio-hooks uninstall --scripts``: there is one
    implementation, so equivalence holds by construction. These tests keep it
    that way -- no embedded rule, no deletion of files other than the project's
    own --purge targets, no deletion of the live temp queue -- and pin the CLI's
    own lists, markers and rule."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.src = UNINSTALL_SH.read_text(encoding="utf-8")
        cls.cli = _load_cli()

    def test_it_delegates_to_the_cli(self) -> None:
        self.assertIn('"$PYTHON_BIN" bin/audio-hooks.py uninstall --scripts', self.src)

    def test_it_carries_no_rule_of_its_own(self) -> None:
        for forbidden in ("PYTHON_SCRIPT", "hook_events", "HOOK_EVENTS", "HOOK_SCRIPTS",
                          "REF_TEMPLATE", "HOME_SPELLINGS", "json.load", "json.dump",
                          'rm "$HOOKS_DIR', "rm -rf \"$HOOKS_DIR"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.src)

    def test_it_does_not_delete_the_temp_queue(self) -> None:
        self.assertNotIn("rm -rf \"$_tmp_dir/claude_audio_hooks_queue\"", self.src)
        self.assertNotIn("rm -f \"$_tmp_dir/claude_audio_hooks.lock\"", self.src)

    def test_paths_are_printed_with_printf_not_echo_e(self) -> None:
        """`echo -e` interprets \\c, \\a, \\0 inside a Windows temp path and truncates it."""
        for line in self.src.splitlines():
            if line.lstrip().startswith("echo -e"):
                self.assertNotRegex(line, r"\$\{?(?:BACKUP|PURGE|_tmp|PROJECT|CLAUDE|HOME|TEMP)", line)

    def test_it_does_not_advertise_a_purge_form_the_cli_rejects(self) -> None:
        self.assertNotIn("audio-hooks uninstall [--purge]", self.src)
        self.assertNotIn("audio-hooks uninstall --purge", self.src)

    def test_help_names_the_real_backup_location(self) -> None:
        self.assertIn("backups/audio-hooks-uninstall-<timestamp>", self.src)
        self.assertNotIn("claude_hooks_backup", self.src)

    def test_cli_names_files_and_markers_line_up(self) -> None:
        names = set(self.cli.LEGACY_COMMAND_SCRIPTS) | set(self.cli.LEGACY_EXTRA_HOOK_FILES)
        self.assertEqual(set(self.cli.LEGACY_FILE_MARKERS) | {".project_path"}, names)
        self.assertEqual(set(self.cli.LEGACY_SHARED_MARKERS),
                         {"hook_config.sh", "hook_config_with_path_utils.sh", "path_utils.sh", "hook_logger.sh"})

    def test_home_anchored_rule_pieces(self) -> None:
        self.assertEqual(self.cli._LEGACY_HOME_SPELLINGS, (r"~", r"\$HOME", r"\$\{HOME\}", r"%USERPROFILE%"))
        self.assertEqual(
            self.cli._LEGACY_REF_TEMPLATE,
            r"""(?:^|[\s"'=:(])(?:%s)/\.claude/hooks/(?:%s)(?![\w.\-/])""")
        self.assertEqual(len(self.cli.LEGACY_HOOK_EVENTS), 30)


if __name__ == "__main__":
    unittest.main()
