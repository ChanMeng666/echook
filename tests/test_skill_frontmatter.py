"""The skill's YAML frontmatter must parse.

A plain scalar containing ": " is a YAML error, and Claude Code silently drops
a skill whose frontmatter fails to parse, so the skill vanishes with no
warning. 7.1.0's shortened description shipped exactly that way.
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "plugins" / "audio-hooks" / "skills" / "audio-hooks" / "SKILL.md"


def frontmatter(text: str) -> str:
    lines = text.splitlines()
    assert lines[0] == "---", "SKILL.md must start with a --- frontmatter fence"
    end = lines.index("---", 1)
    return "\n".join(lines[1:end])


class SkillFrontmatterTests(unittest.TestCase):
    def test_parses_as_yaml_with_name_and_description(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        data = yaml.safe_load(frontmatter(SKILL.read_text(encoding="utf-8")))
        self.assertIsInstance(data, dict)
        self.assertEqual(data["name"], "audio-hooks")
        self.assertIsInstance(data["description"], str)
        self.assertLessEqual(len(data["description"]), 1024)

    def test_plain_scalars_have_no_colon_space(self):
        # Dependency-free guard for the same failure: an unquoted value
        # must not contain ": " (YAML reads it as a nested mapping).
        for line in frontmatter(SKILL.read_text(encoding="utf-8")).splitlines():
            key, sep, value = line.partition(": ")
            if not sep:
                continue
            value = value.strip()
            if value[:1] in ('"', "'", "|", ">"):
                continue
            self.assertNotIn(": ", value, f"unquoted ': ' in frontmatter key {key!r}")


if __name__ == "__main__":
    unittest.main()
