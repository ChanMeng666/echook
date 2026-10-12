"""``config/user_preferences.schema.json`` is a copy of the schema the CLI prints.

The preferences template points editors at that file through ``$schema``, and
``audio-hooks manifest --schema`` builds the same schema in code
(``bin/echook_cli/manifest_schema.py``). Nothing tied the two together, so the
file could drift from what the CLI validates against. The code is the source;
this test fails when the file no longer matches it.
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMA_FILE = REPO / "config" / "user_preferences.schema.json"


class TestPreferencesSchemaFile(unittest.TestCase):
    def test_the_file_matches_the_schema_the_cli_builds(self) -> None:
        sys.path.insert(0, str(REPO / "bin"))
        try:
            from echook_cli.manifest_schema import _build_manifest_schema
        finally:
            sys.path.remove(str(REPO / "bin"))
        on_disk = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
        self.assertEqual(
            on_disk, _build_manifest_schema(),
            "regenerate with: audio-hooks manifest --schema > config/user_preferences.schema.json")

    def test_the_template_points_at_the_file(self) -> None:
        template = json.loads((REPO / "config" / "default_preferences.json").read_text(encoding="utf-8"))
        self.assertEqual((REPO / "config" / template["$schema"]).resolve(), SCHEMA_FILE.resolve())


if __name__ == "__main__":
    unittest.main()
