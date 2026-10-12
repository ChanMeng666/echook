"""echook hook runner: the opt-in ``terminalSequence`` stdout channel.

The only code in the runner that writes hook output to stdout; see the
containment rules below before changing which events may emit.
"""

import json
import sys
from typing import Any, Dict, Optional

import runner_core


# ---------------------------------------------------------------------------
# terminalSequence (v6.5) — Claude Code emits an OSC escape on our behalf.
# ---------------------------------------------------------------------------
#
# Claude Code 2.1.141+ reads a `terminalSequence` field from a hook's stdout
# JSON and writes the sequence to the terminal itself. That is worth having:
# it produces a desktop toast / window title / bell on every platform with no
# dependencies, and it works even though a hook has no controlling terminal —
# which is precisely the away-from-screen case echook exists for.
#
# It is also the one feature that requires this runner to write to stdout, and
# writing to stdout is exactly what caused the v6.3.4 outage: `WorktreeCreate`
# is a *provider* hook whose command form treats stdout as a return value, so a
# hook that printed anything hijacked worktree creation. The containment rules
# below are not defensive padding, they are the reason this is safe to ship:
#
#   1. Only events on TERMINAL_SEQUENCE_SAFE_EVENTS may emit. Everything else
#      stays byte-for-byte silent on stdout.
#   2. Emit ONLY {"terminalSequence": ...}. Never `hookSpecificOutput`, never
#      `continue`, never `decision` — those fields change Claude Code's
#      behaviour, and several events we register would act on them:
#        * MessageDisplay.displayContent REPLACES Claude's visible output
#        * Elicitation / ElicitationResult .action answers an MCP prompt
#          (accept | decline | cancel) on the user's behalf
#        * TeammateIdle / TaskCompleted honour {"continue": false}
#   3. Claude Code only, by invoker. Cursor and Codex have no such contract and
#      would treat the JSON as ordinary output.
#   4. Opt-in. Default off.
#
# Upstream allows only OSC 0/1/2/9/99/777 and BEL; anything else is dropped.
TERMINAL_SEQUENCE_SAFE_EVENTS = frozenset({
    "notification",
    "stop",
    "stop_failure",
    "permission_request",
    "permission_denied",
    "subagent_stop",
    "task_completed",
    "teammate_idle",
    "session_end",
})

# Events that consume a hook's stdout JSON to change what Claude Code does.
# Kept as data so tests/test_terminal_sequence.py can assert the two sets never
# intersect, rather than trusting the comment above.
TERMINAL_SEQUENCE_FORBIDDEN_EVENTS = frozenset({
    "message_display",
    "elicitation",
    "elicitation_result",
    "pretooluse",
    "posttooluse",
    "post_tool_batch",
    "userpromptsubmit",
    "user_prompt_expansion",
    "session_start",
    "setup",
    "subagent_start",
    "cwd_changed",
    "file_changed",
})

_BEL = chr(7)   #  — terminates an OSC sequence
_ESC = chr(27)  # \e — introduces one


def _sanitise_osc_text(text: str, limit: int = 120) -> str:
    """Strip anything that could terminate or re-open an escape sequence.

    An unsanitised title is a terminal-injection vector: a BEL or ESC inside
    the body closes our sequence early and hands the remainder to the terminal
    as a fresh command. Semicolons delimit fields in OSC 777, so they go too.
    """
    unsafe = (_BEL, _ESC, ";")
    cleaned = []
    for ch in str(text):
        if ch in unsafe:
            cleaned.append(" ")
        elif ord(ch) < 32 or ord(ch) == 127:
            cleaned.append(" ")
        else:
            cleaned.append(ch)
    return " ".join("".join(cleaned).split())[:limit]


def build_terminal_sequence(style: str, title: str, body: str) -> Optional[str]:
    """Build one allowed OSC sequence, or None if it cannot be made safely."""
    title = _sanitise_osc_text(title, 60)
    body = _sanitise_osc_text(body, 120)
    if style == "bell":
        return _BEL
    if style == "title":
        # OSC 2 — window title.
        return f"{_ESC}]2;{title or 'Claude Code'}{_BEL}"
    if style == "osc777":
        # OSC 777 — rxvt/kitty/WezTerm desktop notification.
        return f"{_ESC}]777;notify;{title or 'Claude Code'};{body}{_BEL}"
    # Default: OSC 9 — iTerm2 / kitty / WezTerm / Ghostty.
    message = f"{title}: {body}".strip(": ") if body else title
    if not message:
        return None
    # Upstream rejects an OSC 9 body starting with a digit unless it is the
    # 9;4 progress form, so make sure we never generate one by accident.
    if message[0].isdigit():
        message = "Claude Code " + message
    return f"{_ESC}]9;{message}{_BEL}"


def emit_terminal_sequence(hook_type: str, context: str, config: Dict[str, Any]) -> bool:
    """Print {"terminalSequence": ...} for Claude Code to write out.

    Returns True if something was printed. Every failure path returns False
    silently: a notification channel must never be able to break the hook.
    """
    try:
        settings = config.get("notification_settings", {}) or {}
        ts = settings.get("terminal_sequence", {}) or {}
        if not isinstance(ts, dict) or not ts.get("enabled"):
            return False
        if runner_core._get_invoker() != "claude-code":
            return False
        if hook_type in TERMINAL_SEQUENCE_FORBIDDEN_EVENTS:
            return False
        if hook_type not in TERMINAL_SEQUENCE_SAFE_EVENTS:
            return False
        allowed = ts.get("hook_types")
        if isinstance(allowed, list) and allowed and hook_type not in allowed:
            return False

        style = str(ts.get("style", "osc9") or "osc9")
        sequence = build_terminal_sequence(style, "Claude Code", context or hook_type)
        if not sequence:
            return False
        sys.stdout.write(json.dumps({"terminalSequence": sequence}))
        sys.stdout.write(chr(10))
        sys.stdout.flush()
        return True
    except Exception:
        return False
