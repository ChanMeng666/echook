"""Safety tests for the v6.5 `terminalSequence` channel.

Claude Code 2.1.141+ reads a `terminalSequence` field from a hook's stdout JSON
and writes the escape to the terminal itself — a cross-platform desktop toast
that works without a controlling terminal.

It is also the only thing in `hook_runner` that writes to stdout, and stdout is
exactly what caused the v6.3.4 outage: `WorktreeCreate` is a *provider* hook
whose command form treats stdout as a return value, so a hook that printed
anything took over worktree creation and broke every worktree-isolated subagent.

These tests pin the containment that makes the feature safe:

  * silence on stdout everywhere except an explicit allowlist,
  * never a field that changes Claude Code's behaviour,
  * no terminal injection through the notification text,
  * off unless the user turned it on, and Claude Code only.

Run with::

    python -m unittest discover tests
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_RUNNER = REPO_ROOT / "hooks" / "hook_runner.py"

BEL = chr(7)
ESC = chr(27)


def _load_hook_runner():
    spec = importlib.util.spec_from_file_location("hook_runner_ts", HOOK_RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HR = _load_hook_runner()


def _config(enabled=True, style="osc9", hook_types=None):
    ts = {"enabled": enabled, "style": style}
    if hook_types is not None:
        ts["hook_types"] = hook_types
    return {"notification_settings": {"terminal_sequence": ts}}


class TestAllowlistContainment(unittest.TestCase):
    def test_safe_and_forbidden_sets_never_intersect(self) -> None:
        self.assertEqual(
            HR.TERMINAL_SEQUENCE_SAFE_EVENTS & HR.TERMINAL_SEQUENCE_FORBIDDEN_EVENTS,
            frozenset(),
        )

    def test_events_that_consume_stdout_are_forbidden(self) -> None:
        """Each of these acts on a hook's stdout JSON.

        `MessageDisplay.displayContent` replaces Claude's visible output;
        `Elicitation`/`ElicitationResult`.action answers an MCP prompt with
        accept/decline/cancel on the user's behalf. Emitting on them is how a
        notification feature turns into a behaviour change.
        """
        for event in ("message_display", "elicitation", "elicitation_result"):
            self.assertIn(event, HR.TERMINAL_SEQUENCE_FORBIDDEN_EVENTS)
            self.assertNotIn(event, HR.TERMINAL_SEQUENCE_SAFE_EVENTS)

    def test_worktree_events_are_never_safe(self) -> None:
        """`worktree_create` is not registered at all and `worktree_remove`
        must never gain stdout output, whatever else changes."""
        for event in ("worktree_create", "worktree_remove"):
            self.assertNotIn(event, HR.TERMINAL_SEQUENCE_SAFE_EVENTS)

    def test_forbidden_event_emits_nothing(self) -> None:
        for event in sorted(HR.TERMINAL_SEQUENCE_FORBIDDEN_EVENTS):
            buf = io.StringIO()
            with redirect_stdout(buf):
                emitted = HR.emit_terminal_sequence(event, "ctx", _config())
            self.assertFalse(emitted, event)
            self.assertEqual(buf.getvalue(), "", f"{event} wrote to stdout")

    def test_unknown_event_emits_nothing(self) -> None:
        """Default deny: an event absent from both sets stays silent."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            emitted = HR.emit_terminal_sequence("some_future_event", "ctx", _config())
        self.assertFalse(emitted)
        self.assertEqual(buf.getvalue(), "")


class TestOptInAndInvoker(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            emitted = HR.emit_terminal_sequence("notification", "ctx", {})
        self.assertFalse(emitted)
        self.assertEqual(buf.getvalue(), "")

    def test_explicitly_disabled_emits_nothing(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            emitted = HR.emit_terminal_sequence("notification", "ctx", _config(enabled=False))
        self.assertFalse(emitted)
        self.assertEqual(buf.getvalue(), "")

    def test_hook_types_narrows_further(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            emitted = HR.emit_terminal_sequence(
                "stop", "ctx", _config(hook_types=["notification"])
            )
        self.assertFalse(emitted)
        self.assertEqual(buf.getvalue(), "")

    def test_emits_only_the_terminal_sequence_key(self) -> None:
        """The payload must carry nothing that changes Claude Code's behaviour
        — no hookSpecificOutput, no continue, no decision."""
        buf = io.StringIO()
        original = HR._get_invoker
        HR._get_invoker = lambda: "claude-code"
        try:
            with redirect_stdout(buf):
                emitted = HR.emit_terminal_sequence("notification", "Waiting for you", _config())
        finally:
            HR._get_invoker = original
        self.assertTrue(emitted)
        payload = json.loads(buf.getvalue().strip())
        self.assertEqual(list(payload.keys()), ["terminalSequence"])
        for forbidden in ("hookSpecificOutput", "continue", "decision", "stopReason"):
            self.assertNotIn(forbidden, payload)


    def test_cursor_and_codex_never_emit(self) -> None:
        """Only Claude Code reads `terminalSequence`. On the other two editors
        this JSON would be ordinary output on a hook that must stay silent."""
        original = HR._get_invoker
        try:
            for invoker in ("cursor", "codex"):
                HR._get_invoker = lambda inv=invoker: inv
                buf = io.StringIO()
                with redirect_stdout(buf):
                    emitted = HR.emit_terminal_sequence("notification", "ctx", _config())
                self.assertFalse(emitted, invoker)
                self.assertEqual(buf.getvalue(), "", f"{invoker} wrote to stdout")
        finally:
            HR._get_invoker = original


class TestSequenceConstruction(unittest.TestCase):
    def test_only_allowed_osc_codes(self) -> None:
        """Upstream drops anything outside OSC 0/1/2/9/99/777 and BEL."""
        allowed_prefixes = tuple(f"{ESC}]{code};" for code in ("0", "1", "2", "9", "99", "777"))
        for style in ("osc9", "osc777", "title"):
            seq = HR.build_terminal_sequence(style, "Claude Code", "body")
            self.assertTrue(
                seq.startswith(allowed_prefixes), f"{style} produced {seq!r}"
            )
            self.assertTrue(seq.endswith(BEL))

    def test_bell_style_is_bare_bel(self) -> None:
        self.assertEqual(HR.build_terminal_sequence("bell", "t", "b"), BEL)

    def test_injected_escapes_are_neutralised(self) -> None:
        """A BEL or ESC in the body would close our sequence early and hand the
        rest to the terminal as a new command."""
        hostile = "evil" + BEL + ESC + "]2;pwned"
        seq = HR.build_terminal_sequence("osc9", "T", hostile)
        self.assertEqual(seq.count(BEL), 1, "BEL must appear only as terminator")
        self.assertEqual(seq.count(ESC), 1, "ESC must appear only as introducer")
        self.assertNotIn("]2;", seq[2:])

    def test_semicolons_stripped_so_osc777_fields_cannot_be_forged(self) -> None:
        """OSC 777 is `777;notify;<title>;<body>` — exactly three delimiters.
        A semicolon surviving from user text would forge a fourth field."""
        seq = HR.build_terminal_sequence("osc777", "Claude Code", "a;b;c")
        self.assertEqual(seq.count(";"), 3, f"unexpected field count in {seq!r}")
        self.assertTrue(seq.endswith("a b c" + BEL))

    def test_body_never_starts_with_a_digit(self) -> None:
        """Upstream rejects an OSC 9 body starting with a digit unless it is the
        9;4 progress form, so we must not generate one by accident."""
        seq = HR.build_terminal_sequence("osc9", "", "42 files changed")
        body = seq[len(f"{ESC}]9;"):-1]
        self.assertFalse(body[0].isdigit(), f"body {body!r} starts with a digit")

    def test_control_characters_are_removed(self) -> None:
        seq = HR.build_terminal_sequence("osc9", "T", "line1\nline2\ttab\r")
        body = seq[len(f"{ESC}]9;"):-1]
        for ch in body:
            self.assertGreaterEqual(ord(ch), 32, f"control char {ch!r} survived")

    def test_empty_input_produces_nothing(self) -> None:
        self.assertIsNone(HR.build_terminal_sequence("osc9", "", ""))


if __name__ == "__main__":
    unittest.main()
