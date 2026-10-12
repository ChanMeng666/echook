"""JSON output and argument checking shared by every `audio-hooks` subcommand.

``emit`` / ``emit_error`` are the only way the CLI writes to stdout;
``_check_args`` is how a state-changing subcommand rejects what it does not
define. Nothing here reads or writes project state.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional, Tuple


def emit(payload: Dict[str, Any]) -> None:
    """Print a JSON document to stdout. Compact, no trailing newline noise."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def emit_error(code: str, message: str, hint: str = "", suggested_command: str = "", **extra: Any) -> int:
    """Emit a JSON error to stdout and return exit code 1."""
    err: Dict[str, Any] = {"ok": False, "error": {"code": code, "message": message}}
    if hint:
        err["error"]["hint"] = hint
    if suggested_command:
        err["error"]["suggested_command"] = suggested_command
    for k, v in extra.items():
        err[k] = v
    emit(err)
    return 1


def _is_help_flag(tok: str) -> bool:
    """``--help`` / ``-h`` plus the spellings agents and shells reach for next.

    v6.6: ``/?`` (Windows), ``-?`` and ``--help=<anything>`` used to be ordinary
    unknown arguments, so on a state-changing subcommand they were ignored and
    the command ran.
    """
    return tok in ("-h", "--help", "-?", "/?") or tok.startswith("--help=")


def _looks_like_flag(tok: str) -> bool:
    return (tok.startswith("-") and tok != "-") or tok == "/?"


def _check_args(cmd: str, args: List[str], *, flags: Tuple[str, ...] = (),
                valued: Tuple[str, ...] = (), max_positionals: Optional[int] = None) -> Optional[int]:
    """Reject arguments a state-changing subcommand does not define.

    v6.6: these subcommands used to skip anything they did not recognise and
    then act -- ``statusline install --dry-run`` rewrote settings.json,
    ``tts set --bogus x`` wrote a ``bogus`` key. Returns None when the
    arguments are fine, else emits ``INVALID_USAGE`` (nothing was changed) and
    returns the exit code.

    ``valued`` flags consume the next token whatever it looks like. Anything
    else that starts with ``-`` (or is ``/?``) is an unknown flag; a bare token
    is a positional, counted against ``max_positionals`` (None = unlimited).
    The decision is per call site, from that subcommand's real grammar: a
    positional that may legitimately start with ``-`` (the value of
    ``set <key> <value>``) is simply never passed through here.
    """
    accepted = list(flags) + list(valued)
    hint = ("Accepted flags: " + ", ".join(accepted)) if accepted else "This subcommand takes no flags."
    usage_cmd = f"audio-hooks {cmd.split(' ')[0]} --help"
    positionals = 0
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in valued:
            if i + 1 >= len(args):
                return emit_error("INVALID_USAGE", f"{tok} requires a value. Nothing was changed.",
                                  hint=hint, suggested_command=usage_cmd)
            i += 2
            continue
        if tok in flags:
            i += 1
            continue
        if _looks_like_flag(tok):
            return emit_error("INVALID_USAGE", f"Unknown argument for `audio-hooks {cmd}`: {tok}. Nothing was changed.",
                              hint=hint, suggested_command=usage_cmd, unknown_args=[tok])
        positionals += 1
        if max_positionals is not None and positionals > max_positionals:
            return emit_error("INVALID_USAGE", f"Unexpected argument for `audio-hooks {cmd}`: {tok}. Nothing was changed.",
                              hint=hint, suggested_command=usage_cmd, unknown_args=[tok])
        i += 1
    return None
