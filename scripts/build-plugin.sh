#!/usr/bin/env bash
# =============================================================================
# build-plugin.sh — sync canonical sources into the plugin directory
#
# Single source of truth: /hooks/hook_runner.py, /bin/audio-hooks*,
# /audio/, /config/default_preferences.json. The plugin layout under
# /plugins/audio-hooks/ holds COPIES that Claude Code's plugin caching
# can package and ship.
#
# This script is fully non-interactive. It exits 0 on success and emits a
# single JSON line summarising what was copied. Designed to be invoked by
# audio-hooks CLI, by CI, or by a developer after editing the canonical
# files. Re-run it any time the canonical files change.
#
# Usage:
#   bash scripts/build-plugin.sh
#   bash scripts/build-plugin.sh --check    # exit 1 if plugin out of sync
# =============================================================================

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PLUGIN="$REPO/plugins/audio-hooks"

CHECK_ONLY=0
if [ "${1:-}" = "--check" ]; then
    CHECK_ONLY=1
fi

mkdir -p "$PLUGIN/hooks" "$PLUGIN/bin" "$PLUGIN/audio/default" "$PLUGIN/audio/custom" "$PLUGIN/config" "$PLUGIN/cursor-hooks"

copied=0
checked=0
diffs=0

sync_file() {
    local src="$1"
    local dst="$2"
    checked=$((checked + 1))
    if [ ! -e "$src" ]; then
        return 0
    fi
    if [ "$CHECK_ONLY" -eq 1 ]; then
        if [ ! -e "$dst" ] || ! cmp -s "$src" "$dst"; then
            diffs=$((diffs + 1))
        fi
        return 0
    fi
    if [ ! -e "$dst" ] || ! cmp -s "$src" "$dst"; then
        cp "$src" "$dst"
        copied=$((copied + 1))
    fi
}

sync_dir() {
    local src="$1"
    local dst="$2"
    if [ ! -d "$src" ]; then
        return 0
    fi
    mkdir -p "$dst"
    for f in "$src"/*; do
        [ -f "$f" ] || continue
        sync_file "$f" "$dst/$(basename "$f")"
    done
}

# Canonical files
sync_file "$REPO/hooks/hook_runner.py"             "$PLUGIN/hooks/hook_runner.py"
sync_file "$REPO/bin/audio-hooks"                  "$PLUGIN/bin/audio-hooks"
sync_file "$REPO/bin/audio-hooks.py"               "$PLUGIN/bin/audio-hooks.py"
sync_file "$REPO/bin/audio-hooks.cmd"              "$PLUGIN/bin/audio-hooks.cmd"
sync_file "$REPO/bin/audio-hooks-statusline"       "$PLUGIN/bin/audio-hooks-statusline"
sync_file "$REPO/bin/audio-hooks-statusline.py"    "$PLUGIN/bin/audio-hooks-statusline.py"
sync_file "$REPO/bin/audio-hooks-statusline.cmd"   "$PLUGIN/bin/audio-hooks-statusline.cmd"
sync_file "$REPO/config/default_preferences.json"  "$PLUGIN/config/default_preferences.json"

# Cursor IDE hooks template (5.1.4+) — used by `audio-hooks install --cursor`
# for users who run Cursor without Claude Code.
sync_file "$REPO/cursor-hooks/hooks.json"          "$PLUGIN/cursor-hooks/hooks.json"

# Audio assets (both themes)
sync_dir "$REPO/audio/default" "$PLUGIN/audio/default"
sync_dir "$REPO/audio/custom"  "$PLUGIN/audio/custom"

if [ "$CHECK_ONLY" -eq 1 ]; then
    if [ "$diffs" -gt 0 ]; then
        printf '{"ok":false,"in_sync":false,"out_of_sync":%d,"checked":%d,"hint":"Run scripts/build-plugin.sh to sync."}\n' "$diffs" "$checked"
        exit 1
    fi
    printf '{"ok":true,"in_sync":true,"checked":%d}\n' "$checked"
    exit 0
fi

printf '{"ok":true,"copied":%d,"checked":%d,"plugin_dir":"%s"}\n' "$copied" "$checked" "$PLUGIN"
