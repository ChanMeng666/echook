"""Every error code the CLI can emit must be in ``manifest.error_codes``.

CLAUDE.md tells agents the manifest is the source of truth for error codes, but
it was built from ``hook_runner.ErrorCode`` alone -- the codes a *hook* reports.
INVALID_USAGE, DUPLICATE_BRIDGE, UNINSTALL_INCOMPLETE and a dozen more are
emitted only by ``bin/audio-hooks.py``, as string literals, so they were absent.
The CLI now carries its own table (``CLI_ERROR_CODES``) and this test scans the
CLI's source for every emit site, so a new code cannot ship uncatalogued.
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
CODE_SHAPE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")


def _load_cli():
    sys.modules.pop("audio_hooks_cli", None)
    spec = importlib.util.spec_from_file_location("audio_hooks_cli", CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["audio_hooks_cli"] = module
    spec.loader.exec_module(module)
    return module


def emitted_codes(source: str):
    """(codes, unscannable) found in the CLI source.

    A code is emitted by ``emit_error("CODE", ...)`` / ``emit_error(code="CODE", ...)``
    or by a dict literal ``{"code": "CODE", ...}`` (the shape ``diagnose`` and
    the install-state warnings use). ``unscannable`` lists emit sites whose code
    is not a literal; those hide a code from this test, so they fail it.
    """
    tree = ast.parse(source)
    codes, unscannable = {}, []
    # emit_error itself builds {"code": code}; that one variable is the sink, not a site.
    sink_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "emit_error":
            sink_lines.update(range(node.lineno, node.end_lineno + 1))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "emit_error":
            arg = node.args[0] if node.args else next(
                (k.value for k in node.keywords if k.arg == "code"), None)
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                codes.setdefault(arg.value, node.lineno)
            else:
                unscannable.append(node.lineno)
        elif isinstance(node, ast.Dict) and node.lineno not in sink_lines:
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "code":
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        codes.setdefault(value.value, node.lineno)
                    else:
                        unscannable.append(node.lineno)
    return codes, unscannable


class TestCliErrorCodes(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = CLI.read_text(encoding="utf-8")
        cls.cli = _load_cli()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cls.cli.cmd_manifest([])
        cls.manifest_codes = json.loads(out.getvalue())["error_codes"]

    def test_scanner_finds_the_known_codes(self) -> None:
        """Guards the scanner itself: a refactor that makes it find nothing must not pass."""
        codes, _ = emitted_codes(self.source)
        for known in ("INVALID_USAGE", "DUPLICATE_BRIDGE", "UNINSTALL_INCOMPLETE",
                      "DUAL_INSTALL_DETECTED", "UNKNOWN_HOOK_TYPE", "CONFIG_READ_ERROR",
                      "PROJECT_DIR_NOT_FOUND", "NO_COMPLETION_SIGNAL"):
            self.assertIn(known, codes)
        self.assertGreater(len(codes), 25)

    def test_scanner_notices_a_new_code_and_a_non_literal_one(self) -> None:
        codes, unscannable = emitted_codes(
            'emit_error("BRAND_NEW_CODE", "x")\n'
            'x = {"code": "ANOTHER_ONE"}\n'
            'emit_error(some_variable, "y")\n'
        )
        self.assertEqual(set(codes), {"BRAND_NEW_CODE", "ANOTHER_ONE"})
        self.assertEqual(unscannable, [3])

    def test_every_emitted_code_is_in_the_manifest(self) -> None:
        codes, unscannable = emitted_codes(self.source)
        self.assertEqual(unscannable, [],
                         "emit sites whose code is not a string literal cannot be catalogued; "
                         f"lines {unscannable}")
        missing = {c: line for c, line in codes.items() if c not in self.manifest_codes}
        self.assertEqual(missing, {},
                         "the CLI emits these codes but manifest.error_codes lacks them; add each to "
                         "CLI_ERROR_CODES in bin/audio-hooks.py (code: first emit line)")

    def test_every_cli_table_entry_is_well_formed(self) -> None:
        for code, meta in self.cli.CLI_ERROR_CODES.items():
            with self.subTest(code=code):
                self.assertRegex(code, CODE_SHAPE)
                self.assertTrue(meta.get("hint"), "needs a one-line meaning")
                self.assertTrue(meta.get("suggested_command"), "needs a suggested remedy")
                self.assertNotIn("\n", meta["hint"])
                self.assertTrue(meta["suggested_command"].startswith("audio-hooks "))
                self.assertIn(meta.get("appears_in"), ("error", "diagnose", "error, diagnose"))

    def test_cli_table_has_no_dead_entries(self) -> None:
        """A catalogued code the CLI no longer emits is documentation that lies."""
        codes, _ = emitted_codes(self.source)
        dead = sorted(set(self.cli.CLI_ERROR_CODES) - set(codes))
        self.assertEqual(dead, [], "in CLI_ERROR_CODES but never emitted")

    def test_manifest_entries_all_carry_hint_and_command_keys(self) -> None:
        for code, meta in self.manifest_codes.items():
            with self.subTest(code=code):
                self.assertIn("hint", meta)
                self.assertIn("suggested_command", meta)

    def test_the_cli_table_does_not_redefine_runner_codes(self) -> None:
        """hook_runner.ErrorCode stays the owner of the codes it defines."""
        runner = {getattr(self.cli.HR.ErrorCode, n) for n in dir(self.cli.HR.ErrorCode) if not n.startswith("_")}
        self.assertEqual(sorted(set(self.cli.CLI_ERROR_CODES) & runner), [])


if __name__ == "__main__":
    unittest.main()
