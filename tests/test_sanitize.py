"""Unit tests for the output sanitizer in ``hooks/hook_runner.py``.

Run with::

    python -m unittest discover tests

Stdlib-only (unittest + importlib) so they run on the same matrix as the smoke
workflow without new dependencies.

Purpose: ``_clean_for_output`` is the shared gate that both spoken TTS replies
and verbose desktop toasts pass through. These tests pin its contract — code
blocks and secrets must never reach speech/toast, and truncation must land on a
word/sentence boundary rather than mid-token.

NOTE ON FIXTURES: the "secret"-shaped strings below are SYNTHESISED at runtime
try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

from inert fragments (see ``_fake_*`` helpers). They are deliberately fake —
there is nothing to leak — and are assembled this way so secret scanners never
see a literal credential in this source file. They only exist to exercise the
redaction regexes in ``_redact_secrets``.
"""

from __future__ import annotations

import base64
import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK_RUNNER = REPO / "hooks" / "hook_runner.py"


def _load_module():
    sys.modules.pop("hook_runner", None)
    sys.modules.pop("invoker", None)
    sys.modules.pop("user_preferences", None)
    spec = importlib.util.spec_from_file_location("hook_runner", HOOK_RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- synthesised, NON-real credential fixtures (assembled at runtime) --------
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")

# sk-<22 chars>  -> matches the OpenAI-style pattern, contains no real key.
FAKE_OPENAI = "sk-" + ("A" * 22)
# ghp_<20 chars> -> matches the GitHub-token pattern.
FAKE_GITHUB = "ghp_" + "0123456789" + "abcdef" + "ABCD"
# AKIA<16 upper/digit> -> matches the AWS access-key-id pattern.
FAKE_AWS = "AKIA" + "1234567890ABCDEF"
# A structurally valid (but meaningless) JWT built from a textbook header/payload.
FAKE_JWT = ".".join(_b64url(p) for p in (b'{"alg":"HS256"}', b'{"sub":"x"}', b"sig"))


class CleanForOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hr = _load_module()

    def clean(self, text, max_len=200, **kw):
        return self.hr._clean_for_output(text, max_len, **kw)

    # ---- code stripping -------------------------------------------------
    def test_fenced_code_block_omitted(self):
        out = self.clean("Done. ```python\nprint('hi')\n``` All set.")
        self.assertNotIn("print", out)
        self.assertIn("[code]", out)

    def test_fenced_code_block_speech_marker(self):
        out = self.clean("Result: ```rm -rf /tmp/x```", for_speech=True)
        self.assertNotIn("rm -rf", out)
        self.assertIn("[code omitted]", out)

    def test_inline_code_keeps_inner_text_drops_backticks(self):
        out = self.clean("Run `npm test` now")
        self.assertNotIn("`", out)
        self.assertIn("npm test", out)

    # ---- markdown stripping --------------------------------------------
    def test_markdown_header_and_bullets_stripped(self):
        out = self.clean("# Title\n- one\n- two")
        self.assertNotIn("#", out)
        self.assertNotRegex(out, r"(?m)^- ")

    def test_link_keeps_text_drops_url(self):
        out = self.clean("See [the docs](https://example.com/secret-path)")
        self.assertIn("the docs", out)
        self.assertNotIn("example.com", out)

    def test_bare_url_unspeakable(self):
        out = self.clean("Visit https://example.com/x now", for_speech=True)
        self.assertNotIn("https://", out)
        self.assertIn("a link", out)

    def test_bold_italic_markers_removed(self):
        out = self.clean("This is **bold** and _italic_")
        self.assertNotIn("*", out)
        self.assertNotIn("_", out)
        self.assertIn("bold", out)

    # ---- secret redaction (fixtures synthesised at runtime; see top) -----
    def test_openai_key_redacted(self):
        out = self.clean(f"key is {FAKE_OPENAI} done")
        self.assertNotIn(FAKE_OPENAI, out)
        self.assertIn("[redacted]", out)

    def test_github_token_redacted(self):
        out = self.clean(f"token {FAKE_GITHUB}")
        self.assertNotIn(FAKE_GITHUB, out)
        self.assertIn("[redacted]", out)

    def test_aws_key_redacted(self):
        out = self.clean(f"{FAKE_AWS} is the id")
        self.assertNotIn(FAKE_AWS, out)

    def test_jwt_redacted(self):
        out = self.clean(f"auth {FAKE_JWT} ok")
        self.assertNotIn(FAKE_JWT, out)
        self.assertNotIn("eyJ", out)

    def test_key_value_secret_redacted(self):
        pw = "password" + "=" + "s3cr3t" + "val"
        key = "api_key" + ": " + "abc123" + "XYZ"
        out = self.clean(f"{pw} and {key}")
        self.assertNotIn("s3cr3tval", out)
        self.assertNotIn("abc123XYZ", out)

    # ---- truncation -----------------------------------------------------
    def test_short_text_unchanged(self):
        self.assertEqual(self.clean("All good.", 200), "All good.")

    def test_truncates_on_sentence_boundary(self):
        text = "First sentence here. Second sentence runs well past the limit boundary."
        out = self.clean(text, 30)
        self.assertTrue(out.endswith("here."), f"got: {out!r}")

    def test_truncates_on_word_boundary_no_midword(self):
        out = self.clean("alpha beta gamma delta epsilon zeta", 18)
        self.assertNotIn("gam…", out)            # not chopped mid-word
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(len(out), 19)

    def test_empty_input(self):
        self.assertEqual(self.clean("", 100), "")
        self.assertEqual(self.clean(None, 100), "")

    def test_whitespace_collapsed(self):
        out = self.clean("line one\n\n   line two\t\tdone")
        self.assertNotIn("\n", out)
        self.assertNotIn("\t", out)
        self.assertIn("line one line two done", out)


if __name__ == "__main__":
    unittest.main()
