"""AGENTS.md is the one full agent guide; CLAUDE.md only imports it.

Claude Code ignores ``AGENTS.md`` whenever a ``CLAUDE.md`` exists, and the
documented way to share one guide is an ``@AGENTS.md`` import inside
``CLAUDE.md`` (a symlink is unreliable on Windows, this project's primary
platform). The two files used to be byte-identical copies kept in sync by hand.
That the import is really loaded was confirmed once with ``claude -p`` (a canary
sentence added to AGENTS.md alone was quoted back); this test keeps the shape
that makes it work.
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import contextlib
import importlib.util
import io
import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


class TestAgentGuides(unittest.TestCase):
    def test_claude_md_imports_agents_md_on_its_first_line(self) -> None:
        first = _read("CLAUDE.md").splitlines()[0].strip()
        self.assertEqual(first, "@AGENTS.md")

    def test_claude_md_is_a_shim_not_a_copy(self) -> None:
        shim, guide = _read("CLAUDE.md"), _read("AGENTS.md")
        self.assertLess(len(shim), 2000, "CLAUDE.md should hold the import and Claude-only notes, not the guide")
        self.assertGreater(len(guide), 10000)
        self.assertNotEqual(shim, guide)

    def test_the_critical_rules_live_only_in_agents_md(self) -> None:
        guide, shim = _read("AGENTS.md"), _read("CLAUDE.md")
        self.assertIn("<critical>", guide)
        self.assertIn("Scope guard", guide)
        self.assertNotIn("Scope guard", shim)

    def test_claude_md_is_not_a_symlink(self) -> None:
        self.assertFalse((REPO / "CLAUDE.md").is_symlink())
        self.assertFalse((REPO / "AGENTS.md").is_symlink())

    def test_agents_md_does_not_import_back(self) -> None:
        self.assertNotRegex(_read("AGENTS.md"), r"(?m)^@CLAUDE\.md")

    def test_manifest_pointers_resolve(self) -> None:
        sys.modules.pop("audio_hooks_cli", None)
        spec = importlib.util.spec_from_file_location("audio_hooks_cli", REPO / "bin" / "audio-hooks.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["audio_hooks_cli"] = module
        spec.loader.exec_module(module)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            module.cmd_manifest([])
        pointers = json.loads(out.getvalue())["pointers"]
        self.assertEqual(pointers["agents_md"], "AGENTS.md")
        self.assertEqual(pointers["claude_md"], "CLAUDE.md")
        for key, value in pointers.items():
            if key.startswith("_") or key == "canonical_sources":
                continue
            with self.subTest(pointer=key):
                self.assertTrue((REPO / value).exists(), f"{key} -> {value}")
        for rel in pointers["canonical_sources"]:
            self.assertTrue((REPO / rel).exists(), rel)

    def test_docs_do_not_call_the_shim_the_canonical_guide(self) -> None:
        for rel in ("docs/ARCHITECTURE.md", "docs/INSTALLATION_GUIDE.md", "docs/TROUBLESHOOTING.md"):
            with self.subTest(doc=rel):
                self.assertNotRegex(_read(rel), r"\[CLAUDE\.md\]\(\.\./CLAUDE\.md\)\s+—\s+canonical")

    def test_agents_md_header_carries_the_current_version(self) -> None:
        project = re.search(r'(?m)^PROJECT_VERSION\s*=\s*"([^"]+)"', _read("bin/audio-hooks.py")).group(1)
        self.assertRegex(_read("AGENTS.md"), rf"(?m)^> v{re.escape(project)} ")


if __name__ == "__main__":
    unittest.main()
