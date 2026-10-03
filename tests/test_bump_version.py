"""``scripts/bump-version.sh`` owns every mechanical version stamp.

Through 6.6.0 it stamped the nine canonical files but not the version in the
agent guide's header or in the headers of the three docs, so
``docs/ARCHITECTURE.md`` still said 6.4.0 with a July date two releases later.
The script is exercised against a scratch copy of just the files it touches (a
stub ``build-plugin.sh`` stands in for the real sync), never the working tree.
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import datetime
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
    "codex-hooks/hooks.json",
    "codex-hooks/plugin-hooks.json",
    "config/default_preferences.json",
    "AGENTS.md",
    "docs/ARCHITECTURE.md",
    "docs/INSTALLATION_GUIDE.md",
    "docs/TROUBLESHOOTING.md",
    "llms.txt",
    "CHANGELOG.md",
]
STAMPED_DOCS = ["docs/ARCHITECTURE.md", "docs/INSTALLATION_GUIDE.md", "docs/TROUBLESHOOTING.md"]


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

    def test_a_mismatched_header_changes_nothing(self) -> None:
        """The four Markdown headers used to be checked last, after twelve files were rewritten."""
        for rel in ["AGENTS.md"] + STAMPED_DOCS:
            with self.subTest(file=rel):
                text = self._read(rel)
                if rel == "AGENTS.md":
                    broken = re.sub(r"(?m)^> v", "> version ", text, count=1)
                else:
                    broken = text.replace("**Version:**", "**Release:**", 1)
                self.assertNotEqual(broken, text)
                (self.root / rel).write_bytes(broken.encode("utf-8"))
                before = self._tree_bytes()
                err = self._bump_fails("9.8.7")
                self.assertFalse(err["ok"])
                self.assertEqual(err["file"], rel)
                self.assertTrue(err["pattern"])
                self.assertEqual(err["files_changed"], [])
                self.assertEqual(self._tree_bytes(), before, "a failed bump must leave every file untouched")
                (self.root / rel).write_bytes(text.encode("utf-8"))

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
        (self.root / "docs" / "TROUBLESHOOTING.md").unlink()
        before = self._tree_bytes()
        err = self._bump_fails("9.8.7")
        self.assertEqual(err["file"], "docs/TROUBLESHOOTING.md")
        self.assertEqual(self._tree_bytes(), before)

    def _read(self, rel: str) -> str:
        return (self.root / rel).read_bytes().decode("utf-8")

    def test_every_stamp_follows_the_version(self) -> None:
        out = self._bump("9.8.7")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["new_version"], "9.8.7")
        for rel in FILES[:9] + ["AGENTS.md"] + STAMPED_DOCS:
            self.assertIn(rel, out["files_changed"], f"{rel} was not stamped")
        self.assertRegex(self._read("AGENTS.md"), r"(?m)^> v9\.8\.7 ")
        today = datetime.date.today().isoformat()
        for rel in STAMPED_DOCS:
            self.assertRegex(self._read(rel), rf"(?m)^> \*\*Version:\*\* 9\.8\.7 \| \*\*Last Updated:\*\* {today}\r?$",
                             rel)

    def test_rerunning_the_same_version_changes_nothing(self) -> None:
        self._bump("9.8.7")
        before = {rel: self._read(rel) for rel in FILES}
        out = self._bump("9.8.7")
        self.assertEqual(out["files_changed"], [], out)
        self.assertEqual({rel: self._read(rel) for rel in FILES}, before)

    def test_the_date_moves_only_with_the_version(self) -> None:
        self._bump("9.8.7")
        path = self.root / "docs" / "ARCHITECTURE.md"
        text = self._read("docs/ARCHITECTURE.md")
        path.write_bytes(re.sub(r"Last Updated:\*\* \S+", "Last Updated:** 2001-01-01", text, count=1).encode("utf-8"))
        self._bump("9.8.7")
        self.assertIn("Last Updated:** 2001-01-01", self._read("docs/ARCHITECTURE.md"))
        self._bump("9.8.8")
        self.assertNotIn("2001-01-01", self._read("docs/ARCHITECTURE.md"))

    def test_the_hand_written_prose_is_reported_not_stamped(self) -> None:
        out = self._bump("9.8.7")
        self.assertEqual(sorted(out["needs_hand_written"]), ["CHANGELOG.md", "llms.txt"])
        self.assertNotIn("9.8.7", self._read("llms.txt"))
        self.assertTrue(any("by hand" in step for step in out["next_steps"]), out["next_steps"])

    def test_old_version_ignores_stale_doc_headers(self) -> None:
        """A doc header stuck on an old version must not be reported as the old release."""
        text = self._read("docs/ARCHITECTURE.md")
        (self.root / "docs" / "ARCHITECTURE.md").write_bytes(
            re.sub(r"\*\*Version:\*\* \S+", "**Version:** 1.0.0", text, count=1).encode("utf-8"))
        project = re.search(r'(?m)^PROJECT_VERSION\s*=\s*"([^"]+)"', self._read("bin/audio-hooks.py")).group(1)
        out = self._bump("9.8.7")
        self.assertEqual(out["old_version"], project)

    def test_the_header_comment_lists_what_the_script_does(self) -> None:
        head = SCRIPT.read_text(encoding="utf-8").split("set -euo pipefail")[0]
        self.assertNotIn("Updates 9 canonical files", head)
        for rel in ["AGENTS.md"] + STAMPED_DOCS:
            self.assertIn(rel, head)

    def test_the_repo_is_internally_consistent(self) -> None:
        """The stamps in the working tree agree with PROJECT_VERSION right now."""
        project = re.search(r'(?m)^PROJECT_VERSION\s*=\s*"([^"]+)"',
                            (REPO / "bin" / "audio-hooks.py").read_text(encoding="utf-8")).group(1)
        self.assertRegex((REPO / "AGENTS.md").read_text(encoding="utf-8"), rf"(?m)^> v{re.escape(project)} ")
        for rel in STAMPED_DOCS:
            self.assertRegex((REPO / rel).read_text(encoding="utf-8"),
                             rf"(?m)^> \*\*Version:\*\* {re.escape(project)} \|", rel)


if __name__ == "__main__":
    unittest.main()
