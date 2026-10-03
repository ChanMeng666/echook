"""Tests for `audio-hooks upgrade` subcommand.

Mocks `claude plugin` invocations via PATH-shim — a fake `claude.cmd`
(Windows) or `claude` (POSIX) bash script in a tempdir prepended to PATH.
This is more portable than mocking subprocess.
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import json
import os
import platform
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "audio-hooks.py"


def _make_fake_claude(shim_dir: Path, behaviors: dict):
    """Create a fake `claude` shim that responds to `claude plugin <verb>`
    based on the `behaviors` dict, e.g.:
        {"list": '{"plugins": [{"id": "audio-hooks@chanmeng-audio-hooks", ...}]}',
         "update": "OK", "install": "OK", "uninstall": "OK"}
    """
    if platform.system() == "Windows":
        shim_path = shim_dir / "claude.cmd"
        # Cmd-batch is fragile with JSON's many " characters. Stash the JSON
        # in a sibling file and `type` it to stdout to avoid escaping hell.
        list_payload = behaviors.get("list", "[]")
        list_file = shim_dir / "_list_response.json"
        list_file.write_text(list_payload, encoding="utf-8")
        body = "@echo off\r\n"
        body += f'if "%2"=="list" (\r\n'
        body += f'  type "{list_file}"\r\n'
        body += f'  exit /b 0\r\n'
        body += f')\r\n'
        for verb in ("update", "install", "uninstall"):
            ok = behaviors.get(verb, "OK")
            rc = 0 if ok != "FAIL" else 1
            body += f'if "%2"=="{verb}" (\r\n'
            body += f'  echo {ok}\r\n'
            body += f'  exit /b {rc}\r\n'
            body += f')\r\n'
        body += "exit /b 1\r\n"
        shim_path.write_text(body, encoding="utf-8")
    else:
        shim_path = shim_dir / "claude"
        body = "#!/usr/bin/env bash\n"
        body += "case \"$2\" in\n"
        body += f"  list) cat <<'EOF_LIST'\n{behaviors.get('list', '[]')}\nEOF_LIST\n  ;;\n"
        for verb in ("update", "install", "uninstall"):
            ok = behaviors.get(verb, "OK")
            body += f"  {verb}) echo '{ok}'; exit {0 if ok != 'FAIL' else 1};;\n"
        body += "  *) exit 1;;\n"
        body += "esac\n"
        shim_path.write_text(body)
        shim_path.chmod(stat.S_IRWXU)


class TestUpgradeCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.shim_dir = Path(self.tmp.name) / "shim"
        self.shim_dir.mkdir()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _run_with_shim(self, args, list_response):
        env = os.environ.copy()
        env["PATH"] = str(self.shim_dir) + os.pathsep + env.get("PATH", "")
        env["CLAUDE_PLUGIN_DATA"] = str(self.data_dir)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        _make_fake_claude(self.shim_dir, {"list": list_response, "update": "OK"})
        return subprocess.run(
            [sys.executable, str(SCRIPT)] + list(args),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=15,
        )

    def test_check_only_emits_versions_and_exits(self):
        list_response = json.dumps([
            {"id": "audio-hooks@chanmeng-audio-hooks", "scope": "local", "version": "5.1.4"}
        ])
        proc = self._run_with_shim(["upgrade", "--check-only"], list_response)
        self.assertEqual(proc.returncode, 0)
        doc = json.loads(proc.stdout.strip())
        self.assertTrue(doc["ok"])
        self.assertEqual(doc["current_version"], "5.1.4")
        self.assertIn("would_upgrade", doc)

    def test_upgrade_when_not_installed_emits_NOT_INSTALLED(self):
        list_response = "[]"
        proc = self._run_with_shim(["upgrade"], list_response)
        self.assertNotEqual(proc.returncode, 0)
        doc = json.loads(proc.stdout.strip())
        self.assertEqual(doc["error"]["code"], "NOT_INSTALLED")


if __name__ == "__main__":
    unittest.main()
