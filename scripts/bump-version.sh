#!/usr/bin/env bash
# =============================================================================
# bump-version.sh — bump version across all canonical sources + plugin tree.
#
# Usage:
#   bash scripts/bump-version.sh [--skip-tests] <new_version>
#
# Updates 12 version locations in 9 canonical files, plus 4 documentation
# header stamps (listed after the locations):
#   bin/audio-hooks.py                                  PROJECT_VERSION
#   hooks/hook_runner.py                                HOOK_RUNNER_VERSION
#   .claude-plugin/marketplace.json                     metadata.version + plugins[0].version
#   plugins/audio-hooks/.claude-plugin/plugin.json      version
#   plugins/audio-hooks/.codex-plugin/plugin.json       version
#   cursor-hooks/hooks.json                             _audio_hooks_version
#   codex-hooks/hooks.json                              _audio_hooks_version
#   codex-hooks/plugin-hooks.json                       _audio_hooks_version
#   config/default_preferences.json                     _version + version + "(vX.Y.Z)" in _comment
#
# Header stamps (mechanical: a version number and, for the docs, a date):
#   AGENTS.md                                           "> vX.Y.Z · ..." first line of the guide
#   docs/ARCHITECTURE.md                                "> **Version:** X.Y.Z | **Last Updated:** YYYY-MM-DD"
#   docs/INSTALLATION_GUIDE.md                          (same header)
#   docs/TROUBLESHOOTING.md                             (same header)
# The date is rewritten to today only when the version actually changes, so a
# re-run with the same version stays a no-op.
#
# NOT stamped, because the sentence around the number is the content: the
# release entry in CHANGELOG.md and the "vX.Y.Z = ..." summary in llms.txt.
# When either lacks the new version the JSON output lists it under
# "needs_hand_written" so the release is not shipped with a stale pointer.
#
# Then runs scripts/build-plugin.sh to sync the generated plugin tree, and
# (unless --skip-tests is given) runs the unittest suite as a sanity check.
#
# Idempotent: re-running with the same version is a no-op (files_changed is []).
# Atomic in the sense that matters: every pattern in every target is checked in a
# dry pass before the first write, so a header or key that does not match exits 1
# with nothing changed and a JSON error on stderr naming the file and pattern.
# Outputs a single JSON line on stdout.
#
# Exit codes:
#   0   success
#   1   bad args / invalid version / IO error during edit
#   2   scripts/build-plugin.sh failed after the edits
#   3   unittest suite failed after the edits
# =============================================================================

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# Pick the first Python 3 interpreter that actually runs (not a Microsoft Store
# stub that exits 49). Tries python3 first for POSIX systems, then python.
PYTHON_BIN=""
for cand in python3 python python3.exe python.exe; do
    if "$cand" -c "import sys; sys.exit(0 if sys.version_info[0] >= 3 else 1)" >/dev/null 2>&1; then
        PYTHON_BIN="$cand"
        break
    fi
done
if [ -z "$PYTHON_BIN" ]; then
    printf '{"ok":false,"error":"no working Python 3 interpreter found in PATH","hint":"install Python 3.9+ or set PATH"}\n' >&2
    exit 1
fi

exec "$PYTHON_BIN" - "$@" <<'PY'
import datetime
import json
import pathlib
import re
import subprocess
import sys

argv = sys.argv[1:]
skip_tests = False
positional = []
for a in argv:
    if a == "--skip-tests":
        skip_tests = True
    elif a in ("-h", "--help"):
        print("usage: bash scripts/bump-version.sh [--skip-tests] <new_version>")
        sys.exit(0)
    elif a.startswith("-"):
        print(json.dumps({"ok": False, "error": f"unknown flag: {a}"}), file=sys.stderr)
        sys.exit(1)
    else:
        positional.append(a)

if len(positional) != 1:
    print(json.dumps({
        "ok": False,
        "error": "expected exactly one version argument",
        "usage": "bash scripts/bump-version.sh [--skip-tests] <new_version>",
    }), file=sys.stderr)
    sys.exit(1)

new_version = positional[0]
if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.+-]+)?", new_version):
    print(json.dumps({
        "ok": False,
        "error": f"invalid semver: {new_version}",
        "hint": "use e.g. 5.3.0 or 5.3.0-rc1",
    }), file=sys.stderr)
    sys.exit(1)

repo = pathlib.Path(".").resolve()
changes = []
old_versions = set()

# Every target is validated by a dry pass (DRY = True: read and match, never write)
# before the first byte is written, so a header or key that does not match fails
# with nothing changed instead of leaving the tree half-stamped.
DRY = True


def fail(rel, pattern, message):
    """Stop with a JSON error that names the file and the pattern; nothing was written."""
    print(json.dumps({"ok": False, "error": message, "file": rel, "pattern": pattern,
                      "files_changed": [c["file"] for c in changes]}), file=sys.stderr)
    sys.exit(1)


def read_target(rel):
    try:
        # Binary I/O so CRLF<->LF stays exactly as-is.
        return (repo / rel).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        fail(rel, "(file)", f"cannot read {rel}: {e}")


def bump_py_const(rel, var):
    fp = repo / rel
    text = read_target(rel)
    pat = re.compile(rf'^({re.escape(var)}\s*=\s*)"([^"]*)"', re.M)
    m = pat.search(text)
    if not m:
        fail(rel, f"{var} = \"...\"", f"could not find {var} in {rel}")
    old = m.group(2)
    old_versions.add(old)
    if old != new_version:
        new_text, n = pat.subn(rf'\1"{new_version}"', text, count=1)
        if n != 1:
            fail(rel, f"{var} = \"...\"", f"failed to substitute {var} in {rel}")
        if not DRY:
            fp.write_bytes(new_text.encode("utf-8"))
            changes.append({"file": rel, "old": old, "new": new_version})


def bump_json_string(rel, key_quoted, expected_count):
    """Regex-substitute `<key>: "<semver>"` directly in the file text.

    Avoids json.dumps() round-tripping, which would re-format inline arrays like
    `"keywords": ["a", "b"]` into multi-line form. The semver guard `[0-9][^"]*`
    keeps us from matching integer values like Cursor's schema `"version": 1`.

    expected_count must match exactly to catch missing/extra occurrences.
    """
    fp = repo / rel
    text = read_target(rel)
    pat = re.compile(rf'({re.escape(key_quoted)}\s*:\s*)"([0-9][^"]*)"')
    matches = pat.findall(text)
    if len(matches) != expected_count:
        fail(rel, key_quoted, f"expected {expected_count} matches of {key_quoted} in {rel}, found {len(matches)}")
    olds = [m[1] for m in matches]
    for o in olds:
        old_versions.add(o)
    new_text = pat.sub(rf'\1"{new_version}"', text)
    if new_text != text and not DRY:
        fp.write_bytes(new_text.encode("utf-8"))
        changes.append({"file": rel, "old": "/".join(sorted(set(olds))), "new": new_version})


def bump_embedded_paren_version(rel, key_quoted, expected_count):
    """Rewrite a `(vX.Y.Z)` that is embedded inside a JSON string value.

    bump_json_string() can't do this one: the value is prose ("echook - Default
    Configuration Template (v6.5.0)"), not a bare semver, so its `"[0-9][^"]*"`
    guard never matches.
    """
    fp = repo / rel
    text = read_target(rel)
    pat = re.compile(rf'({re.escape(key_quoted)}\s*:\s*"[^"]*\(v)([0-9][^)"]*)(\))')
    matches = pat.findall(text)
    if len(matches) != expected_count:
        fail(rel, f"{key_quoted}: \"...(vX.Y.Z)\"",
             f"expected {expected_count} embedded (vX.Y.Z) after {key_quoted} in {rel}, found {len(matches)}")
    olds = [m[1] for m in matches]
    for o in olds:
        old_versions.add(o)
    new_text = pat.sub(rf'\g<1>{new_version}\g<3>', text)
    if new_text != text and not DRY:
        fp.write_bytes(new_text.encode("utf-8"))
        changes.append({"file": rel, "old": "/".join(sorted(set(olds))), "new": new_version})


def bump_header_stamp(rel, pat, with_date):
    """Stamp a documentation header: `> **Version:** X | **Last Updated:** DATE`
    or `> vX · ...`. The date moves to today only when the version changes, so
    re-running with the same version is a no-op (the file is not even rewritten).
    """
    fp = repo / rel
    text = read_target(rel)
    m = pat.search(text)
    if not m:
        fail(rel, pat.pattern, f"could not find the version header in {rel}")
    old = m.group("ver")
    if old == new_version:
        return
    if with_date:
        repl = lambda mm: f'{mm.group("pre")}{new_version}{mm.group("mid")}{datetime.date.today().isoformat()}'
    else:
        repl = lambda mm: f'{mm.group("pre")}{new_version}{mm.group("mid")}'
    new_text = pat.sub(repl, text, count=1)
    if not DRY:
        fp.write_bytes(new_text.encode("utf-8"))
        changes.append({"file": rel, "old": old, "new": new_version})


# Header stamps. AGENTS.md is the single full agent guide (CLAUDE.md only imports
# it); the three docs carry `Version | Last Updated`.
DOC_HEADER = re.compile(
    r"(?P<pre>^> \*\*Version:\*\* )(?P<ver>[0-9][^ |]*)(?P<mid> \| \*\*Last Updated:\*\* )\d{4}-\d{2}-\d{2}", re.M)
GUIDE_HEADER = re.compile(r"(?P<pre>^> v)(?P<ver>[0-9][^ \u00b7]*)(?P<mid> \u00b7)", re.M)
def apply_all():
    # 1 + 2: Python constants
    bump_py_const("bin/audio-hooks.py", "PROJECT_VERSION")
    bump_py_const("hooks/hook_runner.py", "HOOK_RUNNER_VERSION")

    # 3: marketplace.json — two `"version"` string fields (metadata.version + plugins[0].version).
    #    The semver guard in bump_json_string skips Cursor-style `"version": 1` integers.
    bump_json_string(".claude-plugin/marketplace.json", '"version"', expected_count=2)

    # 4 + 5: plugin manifests (separately canonical — build-plugin.sh does not regenerate them)
    bump_json_string("plugins/audio-hooks/.claude-plugin/plugin.json", '"version"', expected_count=1)
    bump_json_string("plugins/audio-hooks/.codex-plugin/plugin.json", '"version"', expected_count=1)

    # 6 + 7 + 8: cursor + codex hook templates use _audio_hooks_version (NOT version,
    # which is Cursor's own schema-version integer).
    bump_json_string("cursor-hooks/hooks.json", '"_audio_hooks_version"', expected_count=1)
    bump_json_string("codex-hooks/hooks.json", '"_audio_hooks_version"', expected_count=1)
    bump_json_string("codex-hooks/plugin-hooks.json", '"_audio_hooks_version"', expected_count=1)

    # 9: the preferences template stamps the version three ways. It is canonical
    #    because UserPreferences._migrate_if_needed writes _version into every
    #    user's config; leaving it stale (it sat at 5.1.5 through 6.5.0) makes every
    #    install claim a version it isn't. `"version"` cannot match inside
    #    `"_version"` — the leading quote is part of the literal.
    bump_json_string("config/default_preferences.json", '"_version"', expected_count=1)
    bump_json_string("config/default_preferences.json", '"version"', expected_count=1)
    bump_embedded_paren_version("config/default_preferences.json", '"_comment"', expected_count=1)

    bump_header_stamp("AGENTS.md", GUIDE_HEADER, with_date=False)
    for _doc in ("docs/ARCHITECTURE.md", "docs/INSTALLATION_GUIDE.md", "docs/TROUBLESHOOTING.md"):
        bump_header_stamp(_doc, DOC_HEADER, with_date=True)


# Dry pass: validate every pattern in every target first. Only then write.
DRY = True
apply_all()
DRY = False
apply_all()

# Prose that names the latest release but cannot be stamped (see the header).
needs_hand_written = []
for _rel, _needle in (("CHANGELOG.md", new_version), ("llms.txt", "v" + new_version)):
    _fp = repo / _rel
    if _fp.exists() and _needle not in _fp.read_text(encoding="utf-8"):
        needs_hand_written.append(_rel)

# Sync the generated plugin tree (copies bin/, hooks/, cursor-hooks/, codex-hooks/, etc).
# Use $BASH (set by the parent bash) so we get the same bash binary that ran us
# instead of accidentally hitting Windows' WSL bash relay (which exits 49 on systems
# without a real WSL distro).
import os, shutil
bash_bin = os.environ.get("BASH") or shutil.which("bash") or "bash"
build_proc = subprocess.run(
    [bash_bin, "scripts/build-plugin.sh"],
    capture_output=True, text=True,
)
build_out = (build_proc.stdout or "").strip()
build_err = (build_proc.stderr or "").strip()

# Optional sanity-check: run the unittest suite.
tests_rc = None
tests_summary = None
if not skip_tests:
    test_proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-v", "tests"],
        capture_output=True, text=True,
    )
    tests_rc = test_proc.returncode
    # Grab the last "Ran N tests in ..." line as a compact summary.
    last_lines = (test_proc.stderr or test_proc.stdout or "").strip().splitlines()
    tests_summary = next((line for line in reversed(last_lines)
                          if line.startswith("Ran ") or line in ("OK", "FAILED")
                          or line.startswith("FAILED")), None)

old_version = next(iter(old_versions)) if len(old_versions) == 1 else (
    sorted(old_versions)[-1] if old_versions else None
)

# Some files carry more than one canonical location (marketplace.json,
# default_preferences.json), so dedupe while preserving first-touch order.
files_changed = list(dict.fromkeys(c["file"] for c in changes))

ok = (build_proc.returncode == 0) and (tests_rc in (None, 0))
out = {
    "ok": ok,
    "old_version": old_version,
    "new_version": new_version,
    "files_changed": files_changed,
    "needs_hand_written": needs_hand_written,
    "build_plugin": {"rc": build_proc.returncode, "stdout": build_out, "stderr": build_err},
    "tests": {"skipped": skip_tests, "rc": tests_rc, "summary": tests_summary},
}
out["next_steps"] = [
    s for s in [
        (f"write the v{new_version} entry by hand in: {', '.join(needs_hand_written)}"
         if needs_hand_written else None),
        f"git diff -- {' '.join(files_changed)}" if files_changed else None,
        f"git add -A && git commit -m 'chore(release): v{new_version}'" if files_changed else None,
        f"git tag v{new_version}",
    ] if s
]
print(json.dumps(out))

if build_proc.returncode != 0:
    sys.exit(2)
if tests_rc not in (None, 0):
    sys.exit(3)
PY
