"""Removal of the legacy script install: `audio-hooks uninstall [--scripts]`.

Owns the single removal rule: which hook events and script names the old
installer registered, the content markers that decide whether a file in
``~/.claude/hooks`` is echook's, and the strict / loose matching of a
registration's command. The installer itself was removed in 7.0.0; this stays
because machines that installed with it still need a way to remove it.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .output import emit, emit_error

# ---------------------------------------------------------------------------
# Native removal of the legacy script install (v6.6)
# ---------------------------------------------------------------------------
#
# `audio-hooks uninstall` used to shell out to a shell uninstaller, which the
# plugin layout did not ship (PROJECT_ROOT is the plugin directory there, the
# exact situation in which DUAL_INSTALL_DETECTED is reported) and which had no
# Windows implementation. The removal therefore lives here. 7.0.0 removed that
# shell script and the installer, so this is the only implementation and rule.

# Every hook event the removed script installer could register (and so uninstall must clear).
LEGACY_HOOK_EVENTS: Tuple[str, ...] = (
    "Notification", "Stop", "StopFailure", "SessionStart", "SessionEnd",
    "SubagentStart", "SubagentStop", "PermissionRequest",
    "PermissionDenied", "TaskCreated", "TaskCompleted", "TeammateIdle",
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "UserPromptSubmit",
    "PreCompact", "PostCompact", "ConfigChange", "InstructionsLoaded",
    "Elicitation", "ElicitationResult", "CwdChanged", "DirectoryAdded",
    "WorktreeRemove", "FileChanged",
    "Setup", "UserPromptExpansion", "PostToolBatch", "MessageDisplay",
)

# Scripts the installers register as hook *commands* (the names a settings.json
# command or a settings.local.json permission may reference).
LEGACY_COMMAND_SCRIPTS: Tuple[str, ...] = (
    "notification_hook.sh", "stop_hook.sh", "pretooluse_hook.sh",
    "posttooluse_hook.sh", "userprompt_hook.sh", "subagent_hook.sh",
    "precompact_hook.sh", "session_start_hook.sh", "session_end_hook.sh",
    "play_audio.sh",   # legacy v1.0
    "hook_runner.py",  # v3.0+ Python runner
)

# Files the installers place in ~/.claude/hooks/ besides the command scripts:
# hook_runner.py imports the two modules at runtime; .project_path records the checkout.
LEGACY_EXTRA_HOOK_FILES: Tuple[str, ...] = ("invoker.py", "user_preferences.py", ".project_path")

# Bytecode of the modules we installed; nothing else in __pycache__ is ours.
LEGACY_INSTALLED_MODULES: Tuple[str, ...] = ("hook_runner", "invoker", "user_preferences")

# --- Ownership by content --------------------------------------------------
# A file name alone proves nothing: `~/.claude/hooks/stop_hook.sh` is an ordinary
# name for a user's own hook, and `shared/` an ordinary directory. A file is
# echook's only when its first 8 KiB also carry a marker taken from every
# historical revision of that file (checked with `git log --all` / `git show`):
#   * the nine wrappers (*_hook.sh): all 9 files, in every revision, contain
#     `source "$SCRIPT_DIR/shared/hook_config.sh"`;
#   * play_audio.sh (v1.0): every revision has the header line
#     `# Claude Code Stop Hook - Play notification audio`;
#   * hook_runner.py: all 44 revisions open with the docstring title
#     `Claude Code Audio Hooks - Python Hook Runner` (30) or `echook - Python Hook Runner` (14);
#   * invoker.py / user_preferences.py: the module docstrings below (one and five revisions);
#   * shared/*.sh: the header comment of each library, across both product names.
_LEGACY_WRAPPER_MARKER = r'source "\$SCRIPT_DIR/shared/hook_config\.sh"'
LEGACY_FILE_MARKERS: Dict[str, str] = {
    **{n: _LEGACY_WRAPPER_MARKER for n in LEGACY_COMMAND_SCRIPTS if n.endswith("_hook.sh")},
    "play_audio.sh": r"^# Claude Code Stop Hook - Play notification audio$",
    "hook_runner.py": r"(?:echook|Claude Code Audio Hooks) - Python Hook Runner",
    "invoker.py": r"Invoker detection for the audio-hooks runner",
    "user_preferences.py": r"single source of truth for user_preferences\.json access",
}
LEGACY_SHARED_MARKERS: Dict[str, str] = {
    "hook_config.sh": r"^# (?:echook|Claude Code Audio Hooks) - Shared Configuration Library$",
    "hook_config_with_path_utils.sh": r"^# (?:echook|Claude Code Audio Hooks) - Shared Configuration Library$",
    "path_utils.sh": r"^# (?:echook|Claude Code Audio Hooks) - Path Utilities$",
    "hook_logger.sh": r"^# Hook Logger - Records all hook triggers for debugging$",
}

# A command or permission is ours only when, after backslashes become forward
# slashes, it references `<home>/.claude/hooks/<known script>`: <home> is one of
# _LEGACY_HOME_SPELLINGS or the actual home directory (case-insensitive on
# Windows); it starts at a word boundary (start of string, whitespace, a quote,
# `=`, `:` or an opening parenthesis); and the script name is not followed by
# more path characters. Another directory's `.claude/hooks` (`node
# D:/proj/.claude/hooks/hook_runner.py`) and a bare substring (`bash
# ~/bin/my_stop_hook.sh`) are NOT ours. Whether the *file* it names is echook's
# is a separate question, decided by content (above).
_LEGACY_HOME_SPELLINGS: Tuple[str, ...] = (r"~", r"\$HOME", r"\$\{HOME\}", r"%USERPROFILE%")
_LEGACY_REF_TEMPLATE = r"""(?:^|[\s"'=:(])(?:%s)/\.claude/hooks/(?:%s)(?![\w.\-/])"""


def _legacy_names_pattern() -> str:
    return "(?P<name>" + "|".join(re.escape(n) for n in LEGACY_COMMAND_SCRIPTS) + ")"


def _legacy_ref_regex() -> "re.Pattern[str]":
    home = str(Path.home()).replace("\\", "/").rstrip("/")
    homes = list(_LEGACY_HOME_SPELLINGS) + ([re.escape(home)] if home else [])
    flags = re.IGNORECASE if platform.system() == "Windows" else 0
    return re.compile(_LEGACY_REF_TEMPLATE % ("|".join(homes), _legacy_names_pattern()), flags)


def _legacy_ref_name(text: Any, rx: Optional["re.Pattern[str]"] = None) -> Optional[str]:
    """The known script a command/permission references (home-anchored), else None."""
    if not isinstance(text, str):
        return None
    m = (rx or _legacy_ref_regex()).search(text.replace("\\", "/"))
    return m.group("name") if m else None


def _is_legacy_script_ref(text: Any, _rx: Optional["re.Pattern[str]"] = None) -> bool:
    return _legacy_ref_name(text, _rx) is not None


def _strip_legacy_hooks(settings: Dict[str, Any],
                        owned: Optional[Callable[[str], bool]] = None,
                        extra_ref: Optional[Callable[[str], Optional[str]]] = None) -> int:
    """Remove only our hook entries from ``settings["hooks"]`` (in place); return the count.

    Per entry, not per group: a group is dropped only when every hook in it was
    ours, an event key only when it had removals and no groups remain, and the
    ``hooks`` object only when removals emptied it. ``owned(name)`` says whether
    the script file an entry names is echook's (or gone); an entry that points at
    a user's own file of the same name is kept.

    ``extra_ref(command)`` (used by ``--remove-unmatched``) names the script of a
    command whose home is spelled in a form the strict rule does not recognise.
    When given, every event key is examined, not only the 30 echook registers.
    """
    removed = 0
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    rx = _legacy_ref_regex()

    def is_ours_entry(h: Any) -> bool:
        if not isinstance(h, dict):
            return False
        cmd = h.get("command")
        name = _legacy_ref_name(cmd, rx)
        if name is None and extra_ref is not None and isinstance(cmd, str):
            name = extra_ref(cmd)
        return name is not None and (owned is None or owned(name))

    events = list(LEGACY_HOOK_EVENTS)
    if extra_ref is not None:
        events += [e for e in hooks if e not in LEGACY_HOOK_EVENTS]
    for event_name in events:
        groups = hooks.get(event_name)
        if not isinstance(groups, list):
            continue
        kept_groups: List[Any] = []
        event_removed = 0
        for group in groups:
            entries = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(entries, list):
                kept_groups.append(group)
                continue
            kept = [h for h in entries if not is_ours_entry(h)]
            gone = len(entries) - len(kept)
            event_removed += gone
            if gone == 0:
                kept_groups.append(group)
            elif kept:
                group = dict(group)  # a copy keeps key order
                group["hooks"] = kept
                kept_groups.append(group)
        if event_removed:
            removed += event_removed
            if kept_groups:
                hooks[event_name] = kept_groups
            else:
                del hooks[event_name]
    if removed and not hooks:
        del settings["hooks"]
    return removed


def _strip_legacy_permissions(settings: Dict[str, Any],
                              owned: Optional[Callable[[str], bool]] = None,
                              extra_ref: Optional[Callable[[str], Optional[str]]] = None) -> int:
    """Remove our entries from ``settings["permissions"]["allow"]`` (in place)."""
    perms = settings.get("permissions")
    if not isinstance(perms, dict) or not isinstance(perms.get("allow"), list):
        return 0
    before = len(perms["allow"])
    rx = _legacy_ref_regex()

    def is_ours_perm(p: Any) -> bool:
        name = _legacy_ref_name(p, rx)
        if name is None and extra_ref is not None and isinstance(p, str):
            name = extra_ref(p)
        return name is not None and (owned is None or owned(name))

    perms["allow"] = [p for p in perms["allow"] if not is_ours_perm(p)]
    return before - len(perms["allow"])


def _read_head(path: Path, limit: int = 8192) -> str:
    with open(path, "rb") as f:
        data = f.read(limit)
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n").lstrip("﻿")


def _has_marker(path: Path, pattern: str) -> bool:
    try:
        return re.search(pattern, _read_head(path), re.MULTILINE) is not None
    except OSError:
        return False


def _marker_state(path: Path, pattern: str) -> Tuple[str, str]:
    """('yes'|'no'|'unreadable', detail) -- an unreadable file is not 'no marker'."""
    try:
        head = _read_head(path)
    except OSError as e:
        return "unreadable", str(e)
    return ("yes" if re.search(pattern, head, re.MULTILINE) else "no"), ""


def _classify_legacy_files(hooks_dir: Path) -> Dict[str, Any]:
    """Split the name-matching files in ~/.claude/hooks into ours and not-ours.

    Returns ``ours`` (paths), ``ours_names`` (set), ``not_ours`` ({path, reason}
    dicts), ``not_ours_names`` (set), ``shared_ours`` (paths in shared/) and
    ``shared_present`` (the shared dir exists).
    """
    ours: List[Path] = []
    not_ours: List[Dict[str, str]] = []
    ours_names: set = set()
    not_ours_names: set = set()

    for name in LEGACY_COMMAND_SCRIPTS + LEGACY_EXTRA_HOOK_FILES:
        p = hooks_dir / name
        if not p.is_file() or name == ".project_path":
            continue
        state, detail = _marker_state(p, LEGACY_FILE_MARKERS[name])
        if state == "yes":
            ours.append(p)
            ours_names.add(name)
        else:
            reason = ("name matches an echook file but its content has no echook marker" if state == "no"
                      else f"name matches an echook file but it could not be read ({detail}); left alone")
            not_ours.append({"path": str(p), "reason": reason})
            not_ours_names.add(name)

    # .project_path holds the checkout path the installers record. It is ours when
    # it is a single path line and sits beside echook's runner, or names a checkout.
    pp = hooks_dir / ".project_path"
    if pp.is_file():
        head = ""
        unreadable = ""
        try:
            head = _read_head(pp, 4096).strip()
        except OSError as e:
            unreadable = str(e)
        single_line = bool(head) and "\n" not in head and "\0" not in head
        points_at_checkout = False
        if single_line:
            try:
                points_at_checkout = (Path(head) / "hooks" / "hook_runner.py").is_file()
            except (OSError, ValueError):
                pass
        if single_line and ("hook_runner.py" in ours_names or points_at_checkout):
            ours.append(pp)
            ours_names.add(".project_path")
        else:
            not_ours.append({"path": str(pp),
                             "reason": (f"could not be read ({unreadable}); left alone" if unreadable else
                                        "not a single-line path beside echook's hook_runner.py or naming an echook checkout")})
            not_ours_names.add(".project_path")

    shared = hooks_dir / "shared"
    shared_ours: List[Path] = []
    if shared.is_dir():
        for name, marker in LEGACY_SHARED_MARKERS.items():
            p = shared / name
            if not p.is_file():
                continue
            state, detail = _marker_state(p, marker)
            if state == "yes":
                shared_ours.append(p)
            else:
                reason = ("name matches an echook library but its content has no echook marker" if state == "no"
                          else f"name matches an echook library but it could not be read ({detail}); left alone")
                not_ours.append({"path": str(p), "reason": reason})
    return {"ours": ours, "ours_names": ours_names, "not_ours": not_ours,
            "not_ours_names": not_ours_names, "shared_ours": shared_ours,
            "shared_present": shared.is_dir()}


_LEGACY_LOOSE_REF_RE = re.compile(
    r"""/\.claude/hooks/%s(?![\w.\-/])""" % _legacy_names_pattern())


def _prefix_segment(norm: str, start: int) -> str:
    """The path-ish word that ends at ``norm[start]`` (the '/' of '/.claude/hooks/…').

    Runs back to the previous whitespace or '(' and drops quotes, then keeps what
    follows the last shell separator, so `"$HOME"`, `true;~`, `true&&~` and
    backtick-~ all reduce to the home spelling and `./` or `$PROJECT/` to a
    relative or foreign one.
    """
    j = start
    while j > 0 and not norm[j - 1].isspace() and norm[j - 1] != "(":
        j -= 1
    segment = norm[j:start].replace('"', "").replace("'", "")
    return re.split(r"[;&|`<>=]", segment)[-1]


def _prefix_denotes_home(prefix: str, name: str, hooks_dir: Path) -> bool:
    """Could ``prefix`` be a spelling of the user's home that the strict rule missed?

    True for a lone `~` (however it is attached to a shell separator),
    environment-variable spellings (`$env:USERPROFILE`, `%HOMEDRIVE%%HOMEPATH%`,
    anything like `$…HOME…`), and an ABSOLUTE path (also an MSYS `/c/Users/…` path)
    that resolves to the very same file as the one in the real hooks directory
    (8.3 short names included). Relative prefixes (`./`, `$PROJECT_DIR/`) and other
    projects' directories are not: they would otherwise be judged against the
    current working directory.
    """
    if prefix == "~":
        return True
    if re.search(r"(?i)\$env:|\$\{?\w*(?:HOME|USERPROFILE)|%\w*(?:HOME|USERPROFILE)", prefix):
        return True
    candidates = []
    if os.path.isabs(prefix) or re.match(r"^[A-Za-z]:[/\\]", prefix):
        candidates.append(prefix)
    m = re.match(r"^/([A-Za-z])/(.*)$", prefix)
    if m:
        candidates.append(f"{m.group(1)}:/{m.group(2)}")
    target = hooks_dir / name
    for cand in candidates:
        try:
            other = Path(cand) / ".claude" / "hooks" / name
            if other.exists() and target.exists() and os.path.samefile(other, target):
                return True
        except (OSError, ValueError):
            continue
    return False


def _unmatched_names(text: str, hooks_dir: Path, not_ours_names: set) -> List[str]:
    """Known scripts that ``text`` references with a home spelling the strict rule
    missed (excluding scripts that are a user's own files)."""
    norm = text.replace("\\", "/")
    names: List[str] = []
    for m in _LEGACY_LOOSE_REF_RE.finditer(norm):
        name = m.group("name")
        if name in not_ours_names:
            continue
        if _prefix_denotes_home(_prefix_segment(norm, m.start()), name, hooks_dir) and name not in names:
            names.append(name)
    return names


def _candidate_strings(doc: Dict[str, Any]):
    """The strings uninstall edits: hook commands (any event) and permissions.allow."""
    hooks = doc.get("hooks")
    if isinstance(hooks, dict):
        for groups in hooks.values():
            for group in groups if isinstance(groups, list) else []:
                entries = group.get("hooks") if isinstance(group, dict) else None
                for h in entries if isinstance(entries, list) else []:
                    if isinstance(h, dict) and isinstance(h.get("command"), str):
                        yield h["command"]
    perms = doc.get("permissions")
    if isinstance(perms, dict) and isinstance(perms.get("allow"), list):
        for p in perms["allow"]:
            if isinstance(p, str):
                yield p


def _unmatched_legacy_references(label: str, doc: Dict[str, Any], hooks_dir: Path,
                                 not_ours_names: set) -> List[Dict[str, Any]]:
    """Registrations that survive stripping because the home is spelled in a form
    the strict rule does not recognise. ``scripts`` carries the full set of script
    names (taken from the untruncated text); ``value`` is only for display."""
    out: List[Dict[str, Any]] = []
    for text in _candidate_strings(doc):
        names = _unmatched_names(text, hooks_dir, not_ours_names)
        if names:
            out.append({"file": label, "value": text[:200], "scripts": names})
    return out


def _encode_json(data: Any) -> bytes:
    """UTF-8, non-ASCII preserved, trailing newline. May raise (RecursionError,
    UnicodeEncodeError for a lone surrogate) -- callers do this BEFORE any write."""
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` atomically, keeping its identity.

    Writes through a symlink to the resolved target (a dotfiles-managed
    settings.json stays a link to the managed file) and keeps the original
    permission bits. The temp file is created 0600 from the start, so a secret-
    bearing file is never briefly readable more widely than the original.
    """
    import stat
    target = Path(os.path.realpath(path))
    tmp = target.with_name(target.name + ".uninstall-tmp")
    try:
        mode = None
        try:
            mode = stat.S_IMODE(os.stat(target).st_mode)
        except OSError:
            pass
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if mode is not None and os.name == "posix":
            os.chmod(tmp, mode)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _uninstall_scripts(*, remove_unmatched: bool = False) -> int:
    """Remove the legacy script install (``~/.claude/hooks`` + its settings entries).

    Order matters: nothing is deleted until the settings edits succeeded, so an
    abort leaves a working install. Both settings files are parsed, edited and
    serialised to bytes before either is written; each write is atomic and keeps
    symlinks and permissions; a file with nothing to remove is not rewritten.
    Only files whose *content* identifies them as echook's are removed, and a
    registration is stripped only if the file it names is echook's or gone. The
    temp lock/queue directory is deliberately left alone: with the plugin also
    installed (the dual-install case this exists for) it holds live state.

    Result: ``ok:true, incomplete:false`` when everything echook put there is gone.
    If something could not be removed, or a registration spells the home in a form
    the strict rule does not recognise (so its script is kept rather than broken),
    the result is ``ok:false`` with error ``UNINSTALL_INCOMPLETE`` and the same
    fields. ``remove_unmatched`` (``--remove-unmatched``) additionally strips those
    registrations and then removes the scripts they pointed at.
    """
    import shutil

    claude_dir = Path.home() / ".claude"
    hooks_dir = claude_dir / "hooks"
    targets = (
        (claude_dir / "settings.json", _strip_legacy_hooks),
        (claude_dir / "settings.local.json", _strip_legacy_permissions),
    )

    # 1. What in ~/.claude/hooks is ours (by content).
    cls = _classify_legacy_files(hooks_dir)
    not_ours_names = cls["not_ours_names"]

    def owned(name: str) -> bool:
        return name not in not_ours_names  # ours, or the file is gone

    def extra_ref(text: str) -> Optional[str]:
        names = _unmatched_names(text, hooks_dir, not_ours_names)
        return names[0] if names else None

    # 2. Parse both settings files first; compute the new contents.
    pending: List[Tuple[Path, Dict[str, Any], int]] = []
    for path, strip in targets:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("top-level JSON value is not an object")
        except Exception as e:  # OSError, ValueError, RecursionError on absurd nesting
            return emit_error(
                "CONFIG_READ_ERROR",
                f"Cannot read {path}: {e}. Nothing was changed.",
                hint="Fix or move the file, then re-run `audio-hooks uninstall`.",
            )
        pending.append((path, data, strip(data, owned, extra_ref if remove_unmatched else None)))
    entries_removed = {p.name: n for p, _d, n in pending}

    # 3. Registrations that survived because the home was spelled in an
    #    unrecognised form. The names come from the untruncated text. Their
    #    script files are kept: deleting a script a registration still points at
    #    would break that hook.
    unmatched: List[Dict[str, Any]] = []
    for path, data, _n in pending:
        unmatched.extend(_unmatched_legacy_references(path.name, data, hooks_dir, not_ours_names))
    blocked_names = {n for u in unmatched for n in u["scripts"]}

    # 4. What gets deleted, and what has to stay so a kept script still runs.
    kept_reason: Dict[Path, str] = {}
    runner_kept = "hook_runner.py" in blocked_names or "hook_runner.py" in not_ours_names
    wrapper_kept = any(n.endswith("_hook.sh") and (n in blocked_names or n in not_ours_names)
                       for n in LEGACY_COMMAND_SCRIPTS)
    files: List[Path] = []
    for f in cls["ours"]:
        if f.name in blocked_names:
            kept_reason[f] = "a registration still points at it"
        elif runner_kept and f.name in ("invoker.py", "user_preferences.py", ".project_path"):
            kept_reason[f] = "needed by the kept hook_runner.py (it imports it / reads it at runtime)"
        else:
            files.append(f)
    shared_ours: List[Path] = cls["shared_ours"]
    if wrapper_kept and shared_ours:
        for f in shared_ours:
            kept_reason[f] = "sourced by a kept *_hook.sh wrapper"
        shared_ours = []
    shared = hooks_dir / "shared"
    pycache = hooks_dir / "__pycache__"
    pyc: List[Path] = []
    if pycache.is_dir():
        removed_or_absent = {f.stem for f in files if f.suffix == ".py"} | {
            m for m in LEGACY_INSTALLED_MODULES if not (hooks_dir / (m + ".py")).exists()}
        for mod in LEGACY_INSTALLED_MODULES:
            if mod in removed_or_absent:
                pyc.extend(sorted(pycache.glob(mod + ".*.pyc")))

    tmp_base = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp")
    left_in_place = [str(p) for p in (tmp_base / "claude_audio_hooks_queue", tmp_base / "claude_audio_hooks.lock")
                     if p.exists()]
    left_in_place.append("config/user_preferences.json and audio/ (preserved)")
    left_in_place.extend(f"{f} (kept: {why})" for f, why in kept_reason.items())
    skipped = list(cls["not_ours"])

    def result(**extra: Any) -> Dict[str, Any]:
        base: Dict[str, Any] = {
            "mode": "scripts",
            "removed_hook_entries": entries_removed.get("settings.json", 0),
            "removed_permissions": entries_removed.get("settings.local.json", 0),
            "removed_files": [], "backup_dir": None,
            "skipped_not_ours": skipped, "unmatched_references": unmatched,
            "left_in_place": left_in_place,
        }
        base.update(extra)
        return base

    def finish(res: Dict[str, Any], failed: List[str]) -> int:
        incomplete = bool(unmatched or failed)
        res["incomplete"] = incomplete
        if not incomplete:
            res["ok"] = True
            emit(res)
            return 0
        problems = []
        steps = []
        if unmatched:
            problems.append(f"{len(unmatched)} registration(s) spell the home directory in a form this command does not "
                            "recognise, so they were left and the script files they point at were kept")
            steps.append("Read unmatched_references (a variable such as $XDG_CONFIG_HOME or %ANDROID_HOME% may belong to "
                         "another tool), then run: audio-hooks uninstall --remove-unmatched")
        if failed:
            problems.append(f"{len(failed)} file(s) could not be deleted")
            steps.append("Close whatever holds the files listed in left_in_place, then run: audio-hooks uninstall")
        res["next_steps"] = steps
        res.pop("ok", None)
        return emit_error("UNINSTALL_INCOMPLETE", "Uninstall is INCOMPLETE: " + "; ".join(problems) + ".",
                          hint="Everything else that was echook's has been removed. Check unmatched_references before using "
                               "--remove-unmatched: it removes every entry listed there and then the scripts they point at.",
                          **{k: v for k, v in res.items() if k != "error"})

    nothing = (not any(n for _p, _d, n in pending)) and not (files or pyc or shared_ours)
    if nothing:
        res = result(nothing_to_remove=not unmatched)
        return finish(res, [])

    # 5. Serialise BEFORE writing anything: encoding failures (a lone surrogate,
    #    absurd nesting) must surface while the install is still untouched.
    payloads: List[Tuple[Path, bytes]] = []
    for path, data, count in pending:
        if count:  # nothing removed -> leave the file byte-for-byte alone
            try:
                payloads.append((path, _encode_json(data)))
            except Exception as e:
                return emit_error("INTERNAL_ERROR",
                                  f"Cannot serialise the edited {path.name}: {e}. Nothing was changed.")

    # 6. Backup: everything that will be changed or deleted (bytecode excepted).
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = claude_dir / "backups" / f"audio-hooks-uninstall-{stamp}"
    n = 1
    while backup_dir.exists():
        n += 1
        backup_dir = claude_dir / "backups" / f"audio-hooks-uninstall-{stamp}-{n}"
    try:
        backup_dir.mkdir(parents=True)
        for path, _d, _n in pending:
            shutil.copy2(path, backup_dir / (path.name + ".backup"))
        if files or shared_ours:
            (backup_dir / "hooks").mkdir()
            for f in files:
                shutil.copy2(f, backup_dir / "hooks" / f.name)
            if shared_ours:
                (backup_dir / "hooks" / "shared").mkdir()
                for f in shared_ours:
                    shutil.copy2(f, backup_dir / "hooks" / "shared" / f.name)
    except OSError as e:
        return emit_error("INTERNAL_ERROR", f"Cannot create backup in {backup_dir}: {e}. Nothing was changed.")

    # 7. Settings edits; on ANY failure restore what was written from the backup.
    written: List[Path] = []
    try:
        for path, payload in payloads:
            _write_bytes_atomic(path, payload)
            written.append(path)
    except Exception as e:
        not_restored: List[str] = []
        for path in written:
            try:
                _write_bytes_atomic(path, (backup_dir / (path.name + ".backup")).read_bytes())
            except Exception:
                not_restored.append(str(path))
        if not_restored:
            return emit_error(
                "INTERNAL_ERROR",
                f"Cannot update settings: {e}. These file(s) were already rewritten and could NOT be restored automatically: "
                f"{', '.join(not_restored)}. The originals are in {backup_dir}; copy them back. No script files were deleted.",
                backup_dir=str(backup_dir))
        return emit_error("INTERNAL_ERROR",
                          f"Cannot update settings: {e}. Settings restored from {backup_dir}; no files were deleted.",
                          backup_dir=str(backup_dir))

    # 8. Only now delete what we installed.
    removed_files: List[str] = []
    failed: List[str] = []
    for f in files + pyc + shared_ours:
        try:
            f.unlink()
            removed_files.append(str(f))
        except OSError:
            failed.append(str(f))
            left_in_place.append(f"{f} (could not be deleted)")
    if shared_ours and shared.is_dir():
        # Only a directory we emptied is removed; a user's shared/ keeps living.
        try:
            shared.rmdir()
            removed_files.append(str(shared))
        except OSError:
            left_in_place.append(f"{shared} (still holds files echook did not install)")
    if pycache.is_dir():
        try:
            pycache.rmdir()  # only succeeds if our bytecode was all that was in it
        except OSError:
            pass
    left_in_place.append(f"{hooks_dir} (the directory itself, and any file echook did not install)")

    res = result(removed_files=removed_files, backup_dir=str(backup_dir),
                 next_steps=["Restart Claude Code so it stops invoking the removed hook scripts"])
    return finish(res, failed)
