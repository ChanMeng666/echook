"""Contract tests for what is left of the legacy script-install path.

Until 7.0.0 the repository shipped a script installer (a POSIX shell script and
a PowerShell one) and a shell uninstaller. Each carried a hand-written list of
Claude Code event names that nothing validated, and both had drifted: the
installer registered 20 of the 28 events the plugin template registered, and
the uninstaller knew only 9, so an uninstall left 19 orphaned registrations in
``~/.claude/settings.json`` pointing at scripts that no longer existed.

7.0.0 removed the installer, the shell uninstaller and the shell library they
shared. What remains is the removal rule in ``bin/echook_cli/legacy_uninstall.py``
(``audio-hooks uninstall``), which machines that still carry a script install
need. These tests pin that rule's lists and markers, and keep the removed files
removed and unreferenced.

The authority for event names is ``plugins/audio-hooks/hooks/hooks.json``:
whatever the plugin registers, the uninstaller must be able to remove.

Run with::

    python -m unittest discover tests
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import json
import subprocess
import unittest
from pathlib import Path
from typing import List, Set

REPO_ROOT = Path(__file__).resolve().parent.parent
CC_TEMPLATE = REPO_ROOT / "plugins" / "audio-hooks" / "hooks" / "hooks.json"

# Spelled in pieces so this module does not itself contain the names it bans.
_INSTALLER_SH = "install-" + "complete.sh"
_INSTALLER_PS1 = "install-" + "windows.ps1"
_UNINSTALLER_SH = "uninstall" + ".sh"

REMOVED_PATHS = (
    "scripts/" + _INSTALLER_SH,
    "scripts/" + _INSTALLER_PS1,
    "scripts/" + _UNINSTALLER_SH,
    "scripts/.internal-tests",
    "hooks/shared",
    "plugins/audio-hooks/hooks/shared",
)
BANNED_REFERENCES = (_INSTALLER_SH, _INSTALLER_PS1, "scripts/" + _UNINSTALLER_SH)
# The release history and the dated project record may name what was removed.
REFERENCE_ALLOWED_IN = {"CHANGELOG.md", "docs/PROJECT_STATUS.md"}


def _authoritative_events() -> Set[str]:
    """Claude Code event names the plugin template registers."""
    data = json.loads(CC_TEMPLATE.read_text(encoding="utf-8"))
    return set(data["hooks"].keys())


def _load_cli():
    import importlib.util
    import sys
    sys.modules.pop("audio_hooks_cli", None)
    spec = importlib.util.spec_from_file_location("audio_hooks_cli", REPO_ROOT / "bin" / "audio-hooks.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["audio_hooks_cli"] = module
    spec.loader.exec_module(module)
    return module


def _load_legacy():
    """The module that owns the removal rule; loading the CLI puts it on sys.path."""
    import importlib
    _load_cli()
    return importlib.import_module("echook_cli.legacy_uninstall")


def _repository_files() -> List[str]:
    """Tracked files plus untracked ones git would not ignore, as posix paths.

    Raises ``unittest.SkipTest`` outside a git checkout (a source tarball).
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise unittest.SkipTest(f"git is not available: {exc}")
    if proc.returncode != 0:
        raise unittest.SkipTest("not a git checkout")
    names = [n for n in proc.stdout.decode("utf-8", "replace").split("\0") if n]
    # A file deleted in the working tree is still in the index until committed.
    return [n for n in names if (REPO_ROOT / n).is_file()]


class TestUninstallCoversEveryEvent(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected = _authoritative_events()

    def test_authority_is_non_trivial(self) -> None:
        """Guard the guard: if the template ever fails to parse, the other
        assertion would pass vacuously against an empty set."""
        self.assertGreaterEqual(len(self.expected), 25)

    def test_uninstall_covers_every_event(self) -> None:
        """The removal lives in the CLI, so the CLI's event list is the one that
        must cover the plugin's events."""
        cli = _load_legacy()
        self.assertEqual(
            self.expected - set(cli.LEGACY_HOOK_EVENTS),
            set(),
            "audio-hooks uninstall would leave orphaned hook registrations behind",
        )


class TestRemovalRuleLivesInTheCli(unittest.TestCase):
    """There is one implementation of the removal rule. These tests pin its
    lists, markers and matching rule."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cli = _load_legacy()

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


class TestScriptInstallerStaysRemoved(unittest.TestCase):
    """7.0.0 removed the script installer. It double-registered every hook on a
    machine that had the plugin (2026-10-03), and no supported install path
    needed it."""

    def test_the_removed_files_are_gone(self) -> None:
        for rel in REMOVED_PATHS:
            with self.subTest(path=rel):
                self.assertFalse((REPO_ROOT / rel).exists(), f"{rel} is back")

    def test_nothing_references_the_removed_scripts(self) -> None:
        files = _repository_files()
        self.assertGreater(len(files), 100, "file listing looks empty; the scan would pass vacuously")
        self.assertIn("bin/audio-hooks.py", files)
        needles = [n.encode("ascii") for n in BANNED_REFERENCES]
        offenders = []
        for rel in files:
            if rel in REFERENCE_ALLOWED_IN:
                continue
            try:
                blob = (REPO_ROOT / rel).read_bytes()
            except OSError:
                continue
            for needle in needles:
                if needle in blob:
                    offenders.append(f"{rel}: {needle.decode('ascii')}")
        self.assertEqual(offenders, [])

    def test_the_cli_spawns_no_installer(self) -> None:
        """`install --scripts` used to run a script from the checkout."""
        src = (REPO_ROOT / "bin" / "audio-hooks.py").read_text(encoding="utf-8")
        start = src.index("def cmd_install(")
        body = src[start:src.index("\ndef ", start + 1)]
        self.assertNotIn("subprocess", body)
        self.assertIn('"SCRIPT_INSTALL_REMOVED"', body)


if __name__ == "__main__":
    unittest.main()
