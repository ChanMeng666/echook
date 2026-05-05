#!/usr/bin/env bash
# =============================================================================
# bump-version.sh — bump version across all canonical sources + plugin tree.
#
# Usage:
#   bash scripts/bump-version.sh [--skip-tests] <new_version>
#
# Updates 6 canonical files:
#   bin/audio-hooks.py                                  PROJECT_VERSION
#   hooks/hook_runner.py                                HOOK_RUNNER_VERSION
#   .claude-plugin/marketplace.json                     metadata.version + plugins[0].version
#   plugins/audio-hooks/.claude-plugin/plugin.json      version
#   cursor-hooks/hooks.json                             _audio_hooks_version
#   codex-hooks/hooks.json                              _audio_hooks_version
#
# Then runs scripts/build-plugin.sh to sync the generated plugin tree, and
# (unless --skip-tests is given) runs the unittest suite as a sanity check.
#
# Idempotent: re-running with the same version is a no-op (files_changed is []).
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


def bump_py_const(rel, var):
    fp = repo / rel
    # Use binary I/O so we never re-translate CRLF↔LF on Windows.
    raw = fp.read_bytes()
    text = raw.decode("utf-8")
    pat = re.compile(rf'^({re.escape(var)}\s*=\s*)"([^"]*)"', re.M)
    m = pat.search(text)
    if not m:
        print(json.dumps({"ok": False, "error": f"could not find {var} in {rel}"}), file=sys.stderr)
        sys.exit(1)
    old = m.group(2)
    old_versions.add(old)
    if old != new_version:
        new_text, n = pat.subn(rf'\1"{new_version}"', text, count=1)
        if n != 1:
            print(json.dumps({"ok": False, "error": f"failed to substitute {var} in {rel}"}), file=sys.stderr)
            sys.exit(1)
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
    # Binary I/O so CRLF↔LF stays exactly as-is.
    raw = fp.read_bytes()
    text = raw.decode("utf-8")
    pat = re.compile(rf'({re.escape(key_quoted)}\s*:\s*)"([0-9][^"]*)"')
    matches = pat.findall(text)
    if len(matches) != expected_count:
        print(json.dumps({
            "ok": False,
            "error": f"expected {expected_count} matches of {key_quoted} in {rel}, found {len(matches)}",
        }), file=sys.stderr)
        sys.exit(1)
    olds = [m[1] for m in matches]
    for o in olds:
        old_versions.add(o)
    new_text = pat.sub(rf'\1"{new_version}"', text)
    if new_text != text:
        fp.write_bytes(new_text.encode("utf-8"))
        changes.append({"file": rel, "old": "/".join(sorted(set(olds))), "new": new_version})


# 1 + 2: Python constants
bump_py_const("bin/audio-hooks.py", "PROJECT_VERSION")
bump_py_const("hooks/hook_runner.py", "HOOK_RUNNER_VERSION")

# 3: marketplace.json — two `"version"` string fields (metadata.version + plugins[0].version).
#    The semver guard in bump_json_string skips Cursor-style `"version": 1` integers.
bump_json_string(".claude-plugin/marketplace.json", '"version"', expected_count=2)

# 4: plugin.json (separately canonical — build-plugin.sh does not regenerate it)
bump_json_string("plugins/audio-hooks/.claude-plugin/plugin.json", '"version"', expected_count=1)

# 5 + 6: cursor + codex hook templates use _audio_hooks_version (NOT version,
# which is Cursor's own schema-version integer).
bump_json_string("cursor-hooks/hooks.json", '"_audio_hooks_version"', expected_count=1)
bump_json_string("codex-hooks/hooks.json", '"_audio_hooks_version"', expected_count=1)

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

ok = (build_proc.returncode == 0) and (tests_rc in (None, 0))
out = {
    "ok": ok,
    "old_version": old_version,
    "new_version": new_version,
    "files_changed": [c["file"] for c in changes],
    "build_plugin": {"rc": build_proc.returncode, "stdout": build_out, "stderr": build_err},
    "tests": {"skipped": skip_tests, "rc": tests_rc, "summary": tests_summary},
}
out["next_steps"] = [
    s for s in [
        f"git diff -- {' '.join(c['file'] for c in changes)}" if changes else None,
        f"git add -A && git commit -m 'chore(release): v{new_version}'" if changes else None,
        f"git tag v{new_version}",
    ] if s
]
print(json.dumps(out))

if build_proc.returncode != 0:
    sys.exit(2)
if tests_rc not in (None, 0):
    sys.exit(3)
PY
