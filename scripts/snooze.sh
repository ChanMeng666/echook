#!/usr/bin/env bash
# =============================================================================
# Claude Code Audio Hooks - Snooze / Temporary Mute
# Temporarily silence all audio hooks for a specified duration.
# Hooks auto-resume when the snooze expires — no cleanup needed.
#
# Usage:
#   bash scripts/snooze.sh          # Snooze 30 minutes (default)
#   bash scripts/snooze.sh 30m      # Snooze 30 minutes
#   bash scripts/snooze.sh 1h       # Snooze 1 hour
#   bash scripts/snooze.sh 2h       # Snooze 2 hours
#   bash scripts/snooze.sh 90m      # Snooze 90 minutes
#   bash scripts/snooze.sh status   # Show snooze status
#   bash scripts/snooze.sh off      # Cancel snooze early
#   bash scripts/snooze.sh resume   # Cancel snooze early (alias)
#
# The snooze works by writing a Unix epoch timestamp to a marker file.
# All hook execution paths check this file; if current_time < timestamp,
# hooks are silently skipped. No daemon or cleanup needed.
# =============================================================================

set -euo pipefail

# =============================================================================
# COLORS
# =============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# =============================================================================
# QUEUE DIRECTORY (must match hook_config.sh and hook_runner.py)
# =============================================================================

if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "mingw"* ]] || [[ "$OSTYPE" == "cygwin" ]]; then
    QUEUE_DIR="${TEMP:-${TMP:-/tmp}}/claude_audio_hooks_queue"
else
    # Use $TMPDIR if set (macOS sets this to /var/folders/..., not /tmp)
    # Must match the logic in hook_runner.py's get_safe_temp_dir()
    _tmpbase="${TMPDIR%/}"
    QUEUE_DIR="${_tmpbase:-/tmp}/claude_audio_hooks_queue"
fi

SNOOZE_FILE="$QUEUE_DIR/snooze_until"

# =============================================================================
# DURATION PARSER
# =============================================================================

# Parse a human-readable duration string into seconds.
# Accepts: 30m, 1h, 2h, 90m, 45, etc. Bare numbers = minutes.
parse_duration() {
    local input="$1"
    local seconds=0

    if [[ "$input" =~ ^([0-9]+)h$ ]]; then
        seconds=$(( ${BASH_REMATCH[1]} * 3600 ))
    elif [[ "$input" =~ ^([0-9]+)m$ ]]; then
        seconds=$(( ${BASH_REMATCH[1]} * 60 ))
    elif [[ "$input" =~ ^([0-9]+)s$ ]]; then
        seconds=$(( ${BASH_REMATCH[1]} ))
    elif [[ "$input" =~ ^([0-9]+)$ ]]; then
        # Bare number = minutes
        seconds=$(( ${BASH_REMATCH[1]} * 60 ))
    else
        printf "${RED}Error:${NC} Invalid duration format '%s'\n" "$input" >&2
        printf "Examples: 30m, 1h, 2h, 90m, 45 (bare number = minutes)\n" >&2
        return 1
    fi

    if [ "$seconds" -le 0 ]; then
        printf "${RED}Error:${NC} Duration must be greater than 0\n" >&2
        return 1
    fi

    # Warn if > 24h but allow it
    if [ "$seconds" -gt 86400 ]; then
        printf "${YELLOW}Warning:${NC} Snoozing for more than 24 hours.\n" >&2
        printf "Consider using 'bash scripts/configure.sh --disable <hook>' for longer silencing.\n" >&2
    fi

    echo "$seconds"
}

# Format seconds into a human-readable string
format_duration() {
    local total_seconds="$1"
    local hours=$(( total_seconds / 3600 ))
    local minutes=$(( (total_seconds % 3600) / 60 ))
    local seconds=$(( total_seconds % 60 ))

    if [ "$hours" -gt 0 ] && [ "$minutes" -gt 0 ]; then
        printf "%dh %dm" "$hours" "$minutes"
    elif [ "$hours" -gt 0 ]; then
        printf "%dh" "$hours"
    elif [ "$minutes" -gt 0 ] && [ "$seconds" -gt 0 ]; then
        printf "%dm %ds" "$minutes" "$seconds"
    elif [ "$minutes" -gt 0 ]; then
        printf "%dm" "$minutes"
    else
        printf "%ds" "$seconds"
    fi
}

# =============================================================================
# COMMANDS
# =============================================================================

cmd_snooze() {
    local duration_str="${1:-30m}"
    local duration_seconds
    duration_seconds="$(parse_duration "$duration_str")" || exit 1

    mkdir -p "$QUEUE_DIR" 2>/dev/null

    local current_time
    current_time="$(date +%s)"
    local snooze_until=$(( current_time + duration_seconds ))

    echo "$snooze_until" > "$SNOOZE_FILE"

    local friendly
    friendly="$(format_duration "$duration_seconds")"
    printf "${GREEN}${BOLD}Snoozed!${NC} All audio hooks muted for ${BOLD}%s${NC}\n" "$friendly"
    printf "Hooks will auto-resume at %s\n" "$(date -d "@$snooze_until" 2>/dev/null || date -r "$snooze_until" 2>/dev/null || echo "epoch $snooze_until")"
    printf "\nTo cancel early: ${CYAN}bash scripts/snooze.sh off${NC}\n"
    printf "To check status: ${CYAN}bash scripts/snooze.sh status${NC}\n"
}

cmd_resume() {
    if [ -f "$SNOOZE_FILE" ]; then
        rm -f "$SNOOZE_FILE"
        printf "${GREEN}${BOLD}Resumed!${NC} Audio hooks are active again.\n"
    else
        printf "${CYAN}Not snoozed.${NC} Audio hooks are already active.\n"
    fi
}

cmd_status() {
    if [ ! -f "$SNOOZE_FILE" ]; then
        printf "${GREEN}Status:${NC} Not snoozed — audio hooks are active.\n"
        return 0
    fi

    local snooze_until
    snooze_until="$(cat "$SNOOZE_FILE" 2>/dev/null)" || {
        printf "${GREEN}Status:${NC} Not snoozed — audio hooks are active.\n"
        return 0
    }

    # Remove any decimal portion for integer comparison
    snooze_until="${snooze_until%%.*}"

    local current_time
    current_time="$(date +%s)"

    if [ "$current_time" -lt "$snooze_until" ] 2>/dev/null; then
        local remaining=$(( snooze_until - current_time ))
        local friendly
        friendly="$(format_duration "$remaining")"
        printf "${YELLOW}${BOLD}Snoozed${NC} — ~%s remaining\n" "$friendly"
        printf "Resumes at %s\n" "$(date -d "@$snooze_until" 2>/dev/null || date -r "$snooze_until" 2>/dev/null || echo "epoch $snooze_until")"
        printf "\nTo cancel: ${CYAN}bash scripts/snooze.sh off${NC}\n"
    else
        printf "${GREEN}Status:${NC} Snooze expired — audio hooks are active.\n"
        # Clean up expired marker
        rm -f "$SNOOZE_FILE" 2>/dev/null
    fi
}

# =============================================================================
# USAGE
# =============================================================================

show_usage() {
    cat <<EOF
${BOLD}${CYAN}Claude Code Audio Hooks - Snooze / Temporary Mute${NC}

${BOLD}Usage:${NC}
  bash scripts/snooze.sh [DURATION]    Snooze for DURATION (default: 30m)
  bash scripts/snooze.sh status        Show current snooze status
  bash scripts/snooze.sh off           Cancel snooze, resume hooks
  bash scripts/snooze.sh resume        Cancel snooze (alias for off)
  bash scripts/snooze.sh --help        Show this help

${BOLD}Duration formats:${NC}
  30m       30 minutes
  1h        1 hour
  2h        2 hours
  90m       90 minutes
  45        45 minutes (bare number = minutes)
  30s       30 seconds

${BOLD}Examples:${NC}
  bash scripts/snooze.sh              # Snooze 30 minutes (default)
  bash scripts/snooze.sh 1h           # Snooze 1 hour
  bash scripts/snooze.sh status       # Check remaining time
  bash scripts/snooze.sh off          # Resume immediately

${BOLD}How it works:${NC}
  Writes an expiry timestamp to a marker file. All hook runners check
  this file before playing audio. When the timestamp is in the past,
  hooks automatically resume — no cleanup needed.

${BOLD}Also available via configure.sh:${NC}
  bash scripts/configure.sh --snooze 1h
  bash scripts/configure.sh --resume
  bash scripts/configure.sh --snooze-status
EOF
}

# =============================================================================
# MAIN
# =============================================================================

main() {
    local arg="${1:-}"

    case "$arg" in
        --help|-h)
            show_usage
            exit 0
            ;;
        status)
            cmd_status
            ;;
        off|resume)
            cmd_resume
            ;;
        "")
            cmd_snooze "30m"
            ;;
        *)
            cmd_snooze "$arg"
            ;;
    esac
}

main "$@"
