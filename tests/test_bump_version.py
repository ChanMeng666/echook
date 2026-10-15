"""``scripts/bump-version.sh`` owns every mechanical version stamp.

It stamps the nine canonical files and nothing else. The agent guide and the docs
carried version headers through 6.8.0; they were retired because a stamp on a
document says nothing about whether its content is current, and each one was a
release-time chore (``docs/ARCHITECTURE.md`` once sat two releases behind).
The script is exercised against a scratch copy of just the files it touches (a
stub ``build-plugin.sh`` stands in for the real sync), never the working tree.
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "bump-version.sh"

# Every file the script reads or rewrites.
FILES = [
    "bin/audio-hooks.py",
    "hooks/hook_runner.py",
    ".claude-plugin/marketplace.json",
    "plugins/audio-hooks/.claude-plugin/plugin.json",
    "plugins/audio-hooks/.codex-plugin/plugin.json",
    "cursor-hooks/hooks.json",
    "config/default_preferences.json",
    "CHANGELOG.md",
]
# Documents that used to carry a stamp and must stay untouched by a bump.
UNSTAMPED_DOCS = ["AGENTS.md", "docs/ARCHITECTURE.md", "docs/INSTALLATION_GUIDE.md", "docs/TROUBLESHOOTING.md",
                  "llms.txt"]


def _find_bash():
    """A bash that really runs (not the WSL relay stub that exits 49)."""
    candidates = [os.environ.get("BASH"), shutil.which("bash"),
                  r"C:\Program Files\Git\bin\bash.exe", "/bin/bash", "/usr/bin/bash"]
    for cand in candidates:
        if not cand or not Path(cand).exists():
            continue
        try:
            r = subprocess.run([cand, "-c", "echo ok"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0 and r.stdout.strip() == "ok":
            return cand
    return None


BASH = _find_bash()


@unittest.skipIf(BASH is None, "no working bash on this machine")
class TestBumpVersion(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        for rel in FILES:
            dst = self.root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / rel, dst)
        (self.root / "scripts").mkdir()
        shutil.copyfile(SCRIPT, self.root / "scripts" / "bump-version.sh")
        (self.root / "scripts" / "build-plugin.sh").write_text('echo \'{"ok":true}\'\n', encoding="utf-8")

    def _bump(self, version: str):
        env = {k: v for k, v in os.environ.items() if k in
               ("PATH", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "PATHEXT", "COMSPEC", "HOME", "USERPROFILE")}
        env["BASH"] = BASH
        proc = subprocess.run([BASH, "scripts/bump-version.sh", "--skip-tests", version],
                              cwd=str(self.root), capture_output=True, text=True, env=env, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stderr[-600:])
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def _bump_fails(self, version: str):
        env = {k: v for k, v in os.environ.items() if k in
               ("PATH", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "PATHEXT", "COMSPEC", "HOME", "USERPROFILE")}
        env["BASH"] = BASH
        proc = subprocess.run([BASH, "scripts/bump-version.sh", "--skip-tests", version],
                              cwd=str(self.root), capture_output=True, text=True, env=env, timeout=180)
        self.assertEqual(proc.returncode, 1, proc.stdout[-400:] + proc.stderr[-400:])
        return json.loads(proc.stderr.strip().splitlines()[-1])

    def _tree_bytes(self):
        # Only the files the script owns: git-bash with a trimmed environment can drop
        # unrelated directories (a literal "%SystemDrive%") into its cwd.
        return {rel: (self.root / rel).read_bytes() for rel in FILES if (self.root / rel).exists()}

    def test_a_mismatched_canonical_stamp_changes_nothing(self) -> None:
        for rel, old, new in (("bin/audio-hooks.py", "PROJECT_VERSION", "PROJECT_VER"),
                              ("plugins/audio-hooks/.claude-plugin/plugin.json", '"version"', '"vers"'),
                              ("config/default_preferences.json", '"_comment"', '"_note"')):
            with self.subTest(file=rel):
                text = self._read(rel)
                (self.root / rel).write_bytes(text.replace(old, new).encode("utf-8"))
                before = self._tree_bytes()
                err = self._bump_fails("9.8.7")
                self.assertEqual(err["file"], rel)
                self.assertEqual(self._tree_bytes(), before)
                (self.root / rel).write_bytes(text.encode("utf-8"))

    def test_a_missing_target_changes_nothing(self) -> None:
        (self.root / "cursor-hooks" / "hooks.json").unlink()
        before = self._tree_bytes()
        err = self._bump_fails("9.8.7")
        self.assertEqual(err["file"], "cursor-hooks/hooks.json")
        self.assertEqual(self._tree_bytes(), before)

    def _read(self, rel: str) -> str:
        return (self.root / rel).read_bytes().decode("utf-8")

    def test_every_stamp_follows_the_version(self) -> None:
        out = self._bump("9.8.7")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["new_version"], "9.8.7")
        for rel in FILES[:7]:
            self.assertIn(rel, out["files_changed"], f"{rel} was not stamped")

    def test_no_document_is_stamped(self) -> None:
        for rel in UNSTAMPED_DOCS:
            dst = self.root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / rel, dst)
        before = {rel: (self.root / rel).read_bytes() for rel in UNSTAMPED_DOCS}
        out = self._bump("9.8.7")
        for rel in UNSTAMPED_DOCS:
            self.assertNotIn(rel, out["files_changed"])
            self.assertEqual((self.root / rel).read_bytes(), before[rel], rel)

    def test_rerunning_the_same_version_changes_nothing(self) -> None:
        self._bump("9.8.7")
        before = {rel: self._read(rel) for rel in FILES}
        out = self._bump("9.8.7")
        self.assertEqual(out["files_changed"], [], out)
        self.assertEqual({rel: self._read(rel) for rel in FILES}, before)

    def test_the_hand_written_prose_is_reported_not_stamped(self) -> None:
        out = self._bump("9.8.7")
        self.assertEqual(out["needs_hand_written"], ["CHANGELOG.md"])
        self.assertNotIn("9.8.7", self._read("CHANGELOG.md"))
        self.assertTrue(any("by hand" in step for step in out["next_steps"]), out["next_steps"])

    def test_the_header_comment_lists_what_the_script_does(self) -> None:
        head = SCRIPT.read_text(encoding="utf-8").split("set -euo pipefail")[0]
        self.assertNotIn("Updates 9 canonical files", head)
        for rel in FILES[:7]:
            self.assertIn(rel, head)
        for rel in UNSTAMPED_DOCS:
            self.assertNotIn(rel, head)

    def test_the_repo_is_internally_consistent(self) -> None:
        """Every canonical stamp in the working tree agrees with PROJECT_VERSION right now."""
        project = re.search(r'(?m)^PROJECT_VERSION\s*=\s*"([^"]+)"',
                            (REPO / "bin" / "audio-hooks.py").read_text(encoding="utf-8")).group(1)
        for rel in FILES[1:7]:
            self.assertIn(f'"{project}"', (REPO / rel).read_text(encoding="utf-8"), rel)


if __name__ == "__main__":
    unittest.main()
