#!/usr/bin/env python3
"""Plugin entry point for hook_runner.py.

This file lives at ${CLAUDE_PLUGIN_ROOT}/runner/run.py and is invoked by every
hook entry in hooks/hooks.json. It imports the canonical hook_runner.py from
the project source bundled inside the plugin and dispatches.

Why a wrapper instead of a symlink: symlinks don't survive file copy on
Windows and can confuse some plugin caching paths. A wrapper is portable and
Claude Code's plugin caching copies the source files alongside this wrapper,
so the import resolves cleanly inside the plugin cache.

Path resolution:
  - The plugin layout under .claude/plugins/cache/<id>/ contains both this
    runner/ directory and a sibling source/ tree from the marketplace clone.
  - We walk up looking for a directory containing hooks/hook_runner.py, then
    insert it into sys.path.

Fast path: before importing hook_runner, event_is_disabled_fast() answers "has
the user switched this event off?" from user_preferences.json with json and os
alone, and exits 0 without loading the runner. See its docstring for the exact
conditions; anything doubtful runs the full path.
"""

from __future__ import annotations

import json
import os
import sys


def _find_hook_runner():
    """Locate hooks/hook_runner.py in or above this script's directory.

    Returns the hooks directory as a string, or None. Uses os.path only:
    pathlib costs ~20 ms of import time on a path that runs for every event.
    """
    explicit = os.environ.get("CLAUDE_AUDIO_HOOKS_PROJECT")
    if explicit:
        candidate = os.path.join(explicit, "hooks")
        if os.path.exists(os.path.join(candidate, "hook_runner.py")):
            return candidate

    ancestor = os.path.dirname(os.path.realpath(__file__))
    while True:
        # Plugin layout: ${CLAUDE_PLUGIN_ROOT}/hooks/hook_runner.py is bundled
        candidate = os.path.join(ancestor, "hooks")
        if os.path.exists(os.path.join(candidate, "hook_runner.py")):
            return candidate
        parent = os.path.dirname(ancestor)
        if parent == ancestor:
            return None
        ancestor = parent


def _event_key(argv):
    """The event name as hook_runner.main() derives it, or None.

    Mirrors main(): ``--invoker <name>`` / ``--invoker=<name>`` are removed
    first, then argv[1] is lower-cased with "-" turned into "_".
    """
    args = []
    skip = False
    for a in argv:
        if skip:
            skip = False
            continue
        if a == "--invoker":
            skip = True
            continue
        if a.startswith("--invoker="):
            continue
        args.append(a)
    if len(args) < 2:
        return None
    return args[1].lower().replace("-", "_")


def _invoker_is_cursor(argv, environ):
    """True when hook_runner's invoker detection would answer "cursor"."""
    for i, a in enumerate(argv):
        if a == "--invoker" and i + 1 < len(argv):
            name = argv[i + 1]
            if name in ("codex", "cursor", "claude-code"):
                return name == "cursor"
            break
        if a.startswith("--invoker="):
            name = a.split("=", 1)[1]
            if name in ("codex", "cursor", "claude-code"):
                return name == "cursor"
            break
    return bool(environ.get("CURSOR_VERSION"))


def _read_json_object(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else None


def event_is_disabled_fast(hooks_dir, argv=None, environ=None):
    """True only when the full runner would do nothing at all for this event.

    A stdlib-only pre-check that lets a disabled event exit without importing
    hook_runner and its dozen modules. Deliberately narrow: every doubt answers
    False and the caller runs the full path, which decides as it always did.

    Short-circuits only when ALL of these hold:
      * CLAUDE_HOOKS_DEBUG is off (with it on, the full path writes its debug
        and DISABLED log lines);
      * the data directory comes from CLAUDE_PLUGIN_DATA, PLUGIN_DATA or
        CLAUDE_AUDIO_HOOKS_DATA -- the first three levels of
        UserPreferences._resolve_data_dir, in the same order. Anything later in
        that chain needs the full resolver, so it falls through;
      * user_preferences.json exists, parses as a JSON object, and carries the
        same ``_version`` as config/default_preferences.json -- otherwise the
        full path auto-inits, repairs or migrates it;
      * ``enabled_hooks`` says False for the event itself (rule 1/2 of
        hook_runner.resolve_hook_enabled), or, for a matcher variant that has no
        key of its own, False for its parent (rule 2). Absent keys are never
        guessed from defaults;
      * it is not a case that acts on an event that is switched off: Cursor's
        ``session_start`` (emits the CLAUDE_PLUGIN_DATA env override) and
        Cursor's ``stop`` (can be rerouted to ``stop_failure``, which has its
        own switch).

    Nothing else in run_hook happens before the enabled gate (rate-limit alerts,
    webhooks, TTS, toasts, terminal sequences and self-update all sit behind it),
    so the only thing skipped is the DISABLED log line.
    """
    try:
        argv = sys.argv if argv is None else argv
        environ = os.environ if environ is None else environ
        if environ.get("CLAUDE_HOOKS_DEBUG", "").lower() in ("1", "true", "yes"):
            return False
        key = _event_key(argv)
        if not key:
            return False
        if _invoker_is_cursor(argv, environ) and (key == "stop" or key.startswith("session_start")):
            return False
        data_dir = (environ.get("CLAUDE_PLUGIN_DATA") or environ.get("PLUGIN_DATA")
                    or environ.get("CLAUDE_AUDIO_HOOKS_DATA"))
        if not data_dir:
            return False
        prefs = _read_json_object(os.path.join(data_dir, "user_preferences.json"))
        template = _read_json_object(os.path.join(
            os.path.dirname(os.path.abspath(hooks_dir)), "config", "default_preferences.json"))
        if not prefs or not template:
            return False
        if prefs.get("_version") != template.get("_version"):
            return False
        enabled = prefs.get("enabled_hooks")
        if not isinstance(enabled, dict):
            return False
        if key in enabled:
            return enabled[key] is False
        # A matcher variant without its own key follows its parent: the longest
        # existing key that the name extends ("stop_failure_unknown" ->
        # "stop_failure"). tests/test_run_fast_path.py pins this against
        # SYNTHETIC_EVENT_MAP.
        parent = None
        for name in enabled:
            if (not name.startswith("_") and key.startswith(name + "_")
                    and (parent is None or len(name) > len(parent))):
                parent = name
        return parent is not None and enabled[parent] is False
    except Exception:
        return False


def main() -> int:
    hooks_dir = _find_hook_runner()
    if hooks_dir is None:
        # Structured error to stderr is acceptable here because Claude Code's
        # hook system swallows hook output by default; this branch only fires
        # if the plugin cache is corrupted.
        sys.stderr.write('{"error":"hook_runner.py not found in plugin"}\n')
        return 1

    if event_is_disabled_fast(hooks_dir):
        # Drain the payload like the full path does, so the editor never sees
        # a closed pipe it was still writing to.
        try:
            sys.stdin.read()
        except Exception:
            pass
        return 0

    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)

    import hook_runner  # type: ignore
    return hook_runner.main()


if __name__ == "__main__":
    sys.exit(main())
