#!/bin/bash
# echook - Uninstallation Script (AI-agent-first, always non-interactive)
#
# Thin wrapper around `audio-hooks uninstall --scripts`. v6.6 moved the removal
# of the legacy script install into the CLI (bin/audio-hooks.py): the plugin
# layout does not ship this script, so the logic could not live here, and two
# implementations of one matching rule drift. This wrapper therefore contains no
# rule of its own -- it only finds a working Python, runs the CLI, and (with
# --purge) removes the project's own config and audio, which the CLI never
# touches.

set -e

PURGE=false
CLI_EXTRA=()

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

CLAUDE_DIR="$HOME/.claude"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# =============================================================================
# ARGUMENT PROCESSING
# =============================================================================

while [ $# -gt 0 ]; do
    case "$1" in
        --yes|-y|--non-interactive)
            # Accepted as a no-op for backward compatibility — this script is
            # always non-interactive.
            shift
            ;;
        --purge)
            PURGE=true
            shift
            ;;
        --remove-unmatched)
            CLI_EXTRA+=(--remove-unmatched)
            shift
            ;;
        --help|-h)
            cat << EOF
${BOLD}echook - Uninstallation Script (AI-agent-first, always non-interactive)${NC}

${CYAN}USAGE:${NC}
  $0 [OPTIONS]

${CYAN}OPTIONS:${NC}
  ${BOLD}--purge${NC}
    Also remove this project's config (config/user_preferences.json) and
    audio files (audio/default/). Without it those are PRESERVED.
    (The CLI form, \`audio-hooks uninstall\`, takes no --purge for the script
    install: its --purge applies to --cursor / --codex only.)

  ${BOLD}--remove-unmatched${NC}
    Passed to the CLI: also strip registrations that spell the home directory in
    a form uninstall does not recognise, and the scripts they point at. Read the
    unmatched_references in the (incomplete) result before using it.

  ${BOLD}--yes, -y, --non-interactive${NC}
    No-op, accepted for backward compatibility. This script never prompts.

  ${BOLD}--help, -h${NC}
    Show this help message

${CYAN}BEHAVIOR:${NC}
  Runs \`audio-hooks uninstall --scripts\` from this checkout: it backs up, then
  removes echook's hook registrations from ~/.claude/settings.json and
  settings.local.json and echook's own files from ~/.claude/hooks. Files are
  judged by content, not by name alone, so your own stop_hook.sh or shared/
  directory is left alone and reported.

${CYAN}EXAMPLES:${NC}
  bash scripts/uninstall.sh            # remove the script install, keep config + audio
  bash scripts/uninstall.sh --purge    # ... and also this project's config + audio
  # Prefer the CLI:  audio-hooks uninstall

${CYAN}Note:${NC} Backups are created in: $CLAUDE_DIR/backups/audio-hooks-uninstall-<timestamp>/
EOF
            exit 0
            ;;
        *)
            echo -e "${RED}Error: Unknown option '$1'${NC}" >&2
            echo "Run '$0 --help' for usage information" >&2
            exit 1
            ;;
    esac
done

# =============================================================================
# PYTHON SELECTION
# =============================================================================

# Pick the first Python 3 that actually runs, the way scripts/bump-version.sh
# does (a Microsoft Store `python3` stub prints nothing and exits 49). Nothing is
# touched until one has been found.
PYTHON_BIN=""
for cand in python3 python python3.exe python.exe; do
    if "$cand" -c "import sys; sys.exit(0 if sys.version_info[0] >= 3 else 1)" >/dev/null 2>&1; then
        PYTHON_BIN="$cand"
        break
    fi
done
if [ -z "$PYTHON_BIN" ]; then
    echo -e "${RED}Error: no working Python 3 interpreter found in PATH (a Microsoft Store python3 stub does not count).${NC}" >&2
    echo "Nothing was changed. Install Python 3 or fix PATH, then re-run." >&2
    exit 1
fi

# =============================================================================
# REMOVE THE SCRIPT INSTALL (the CLI does the work and prints a JSON result)
# =============================================================================

cd "$PROJECT_DIR"
cli_rc=0
"$PYTHON_BIN" bin/audio-hooks.py uninstall --scripts "${CLI_EXTRA[@]}" || cli_rc=$?
if [ "$cli_rc" -ne 0 ]; then
    printf '%s\n' "audio-hooks uninstall --scripts exited with status $cli_rc; see its JSON output above. --purge was NOT applied." >&2
    exit "$cli_rc"
fi

# =============================================================================
# OPTIONAL: REMOVE THIS PROJECT'S CONFIGURATION AND AUDIO FILES
# =============================================================================

if [ "$PURGE" = true ]; then
    PURGE_BACKUP="$CLAUDE_DIR/backups/audio-hooks-purge-$(date +%Y%m%d_%H%M%S)"
    if [ -f "$PROJECT_DIR/config/user_preferences.json" ]; then
        mkdir -p "$PURGE_BACKUP"
        cp "$PROJECT_DIR/config/user_preferences.json" "$PURGE_BACKUP/user_preferences.json.backup"
        rm "$PROJECT_DIR/config/user_preferences.json"
        printf '%s\n' "Removed configuration file (backed up to $PURGE_BACKUP)"
    fi
    if [ -d "$PROJECT_DIR/audio/default" ] && [ -n "$(ls -A "$PROJECT_DIR/audio/default" 2>/dev/null)" ]; then
        mkdir -p "$PURGE_BACKUP/audio"
        cp -r "$PROJECT_DIR/audio/default" "$PURGE_BACKUP/audio/"
        rm -rf "$PROJECT_DIR/audio/default"/*
        printf '%s\n' "Removed audio files (backed up to $PURGE_BACKUP)"
    fi
else
    printf '%s\n' "Preserved config/user_preferences.json and audio/ (use --purge to remove)"
fi

# The temp lock file and queue directory are NOT deleted: with the plugin
# installed (the dual-install case this exists for) they hold its live state.
_tmp_dir="${TEMP:-${TMP:-/tmp}}"
if [ -e "$_tmp_dir/claude_audio_hooks_queue" ] || [ -e "$_tmp_dir/claude_audio_hooks.lock" ]; then
    printf '%s\n' "Left in place: $_tmp_dir/claude_audio_hooks_queue and claude_audio_hooks.lock (may hold the plugin's live state)"
fi

printf '%s\n' "Restart Claude Code to apply the change. The project directory ($PROJECT_DIR) can be deleted if you no longer need it."
