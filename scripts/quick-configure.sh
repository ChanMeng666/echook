#!/usr/bin/env bash
# =============================================================================
# echook - Quick Configure (Lite Tier)
# Lightweight hook manager for Quick Setup users — no clone needed
#
# Usage (remote, no clone):
#   curl -sL https://raw.githubusercontent.com/ChanMeng666/echook/master/scripts/quick-configure.sh | bash -s -- --list
#   curl -sL ...quick-configure.sh | bash -s -- --disable SubagentStop
#   curl -sL ...quick-configure.sh | bash -s -- --enable SubagentStop
#   curl -sL ...quick-configure.sh | bash -s -- --only Stop Notification
#
# Usage (local):
#   bash scripts/quick-configure.sh --list
#   bash scripts/quick-configure.sh --disable SubagentStop PermissionRequest
#   bash scripts/quick-configure.sh --enable SubagentStop
#   bash scripts/quick-configure.sh --only Stop Notification
#
# Supported hooks: Stop, Notification, SubagentStop, PermissionRequest
# =============================================================================

set -euo pipefail

# =============================================================================
# COLORS AND OUTPUT
# =============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()    { printf "${BLUE}[INFO]${NC} %s\n" "$1"; }
success() { printf "${GREEN}[OK]${NC} %s\n" "$1"; }
warn()    { printf "${YELLOW}[WARN]${NC} %s\n" "$1"; }
error()   { printf "${RED}[ERROR]${NC} %s\n" "$1"; }

# All Quick Setup hooks
ALL_HOOKS=("Stop" "Notification" "SubagentStop" "PermissionRequest")

# Cross-platform temp/queue directory (must match hook_runner.py / hook_config.sh)
if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "mingw"* ]] || [[ "$OSTYPE" == "cygwin" ]]; then
    QUEUE_DIR="${TEMP:-${TMP:-/tmp}}/claude_audio_hooks_queue"
else
    QUEUE_DIR="/tmp/claude_audio_hooks_queue"
fi
SNOOZE_FILE="$QUEUE_DIR/snooze_until"

# =============================================================================
# PLATFORM DETECTION (same as quick-setup.sh)
# =============================================================================

detect_platform() {
    local uname_s
    uname_s="$(uname -s 2>/dev/null || echo "Unknown")"

    case "$uname_s" in
        Darwin)
            PLATFORM="macos"
            ;;
        Linux)
            if grep -qi "microsoft\|wsl" /proc/version 2>/dev/null; then
                PLATFORM="wsl"
            else
                PLATFORM="linux"
            fi
            ;;
        MINGW*|MSYS*|CYGWIN*)
            PLATFORM="gitbash"
            ;;
        *)
            PLATFORM="unknown"
            ;;
    esac
}

# =============================================================================
# PLATFORM HOOK GENERATORS (identical to quick-setup.sh)
# These output the full JSON for all 4 hooks. Python/Node filters later.
# =============================================================================

generate_macos_hooks() {
    cat <<'HOOKS_JSON'
{
  "Stop": [
    {
      "hooks": [
        {
          "type": "command",
          "command": "afplay /System/Library/Sounds/Glass.aiff 2>/dev/null & osascript -e 'display notification \"Task completed\" with title \"Claude Code\"' 2>/dev/null; true",
          "timeout": 10
        }
      ]
    }
  ],
  "Notification": [
    {
      "hooks": [
        {
          "type": "command",
          "command": "afplay /System/Library/Sounds/Sosumi.aiff 2>/dev/null & osascript -e 'display notification \"Authorization needed\" with title \"Claude Code\"' 2>/dev/null; true",
          "timeout": 10
        }
      ]
    }
  ],
  "SubagentStop": [
    {
      "hooks": [
        {
          "type": "command",
          "command": "afplay /System/Library/Sounds/Pop.aiff 2>/dev/null & osascript -e 'display notification \"Background task finished\" with title \"Claude Code\"' 2>/dev/null; true",
          "timeout": 10
        }
      ]
    }
  ],
  "PermissionRequest": [
    {
      "matcher": "",
      "hooks": [
        {
          "type": "command",
          "command": "afplay /System/Library/Sounds/Basso.aiff 2>/dev/null & osascript -e 'display notification \"Permission required\" with title \"Claude Code\"' 2>/dev/null; true",
          "timeout": 10
        }
      ]
    }
  ]
}
HOOKS_JSON
}

generate_linux_hooks() {
    cat <<'HOOKS_JSON'
{
  "Stop": [
    {
      "hooks": [
        {
          "type": "command",
          "command": "notify-send 'Claude Code' 'Task completed' -i dialog-information 2>/dev/null; paplay /usr/share/sounds/freedesktop/stereo/complete.oga 2>/dev/null || true",
          "timeout": 10
        }
      ]
    }
  ],
  "Notification": [
    {
      "hooks": [
        {
          "type": "command",
          "command": "notify-send -u critical 'Claude Code' 'Authorization needed' -i dialog-warning 2>/dev/null; paplay /usr/share/sounds/freedesktop/stereo/bell.oga 2>/dev/null || true",
          "timeout": 10
        }
      ]
    }
  ],
  "SubagentStop": [
    {
      "hooks": [
        {
          "type": "command",
          "command": "notify-send 'Claude Code' 'Background task finished' -i dialog-information 2>/dev/null; paplay /usr/share/sounds/freedesktop/stereo/message.oga 2>/dev/null || true",
          "timeout": 10
        }
      ]
    }
  ],
  "PermissionRequest": [
    {
      "matcher": "",
      "hooks": [
        {
          "type": "command",
          "command": "notify-send -u critical 'Claude Code' 'Permission required' -i dialog-warning 2>/dev/null; paplay /usr/share/sounds/freedesktop/stereo/dialog-warning.oga 2>/dev/null || true",
          "timeout": 10
        }
      ]
    }
  ]
}
HOOKS_JSON
}

generate_wsl_hooks() {
    cat <<'HOOKS_JSON'
{
  "Stop": [
    {
      "hooks": [
        {
          "type": "command",
          "command": "powershell.exe -WindowStyle Hidden -Command \"[System.Media.SystemSounds]::Exclamation.Play(); [void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms'); $n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true; $n.ShowBalloonTip(5000, 'Claude Code', 'Task completed', [System.Windows.Forms.ToolTipIcon]::Info); Start-Sleep -Seconds 6; $n.Dispose()\" &",
          "timeout": 10
        }
      ]
    }
  ],
  "Notification": [
    {
      "hooks": [
        {
          "type": "command",
          "command": "powershell.exe -WindowStyle Hidden -Command \"[System.Media.SystemSounds]::Hand.Play(); [void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms'); $n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Warning; $n.Visible = $true; $n.ShowBalloonTip(5000, 'Claude Code', 'Authorization needed', [System.Windows.Forms.ToolTipIcon]::Warning); Start-Sleep -Seconds 6; $n.Dispose()\" &",
          "timeout": 10
        }
      ]
    }
  ],
  "SubagentStop": [
    {
      "hooks": [
        {
          "type": "command",
          "command": "powershell.exe -WindowStyle Hidden -Command \"[System.Media.SystemSounds]::Asterisk.Play(); [void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms'); $n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true; $n.ShowBalloonTip(5000, 'Claude Code', 'Background task finished', [System.Windows.Forms.ToolTipIcon]::Info); Start-Sleep -Seconds 6; $n.Dispose()\" &",
          "timeout": 10
        }
      ]
    }
  ],
  "PermissionRequest": [
    {
      "matcher": "",
      "hooks": [
        {
          "type": "command",
          "command": "powershell.exe -WindowStyle Hidden -Command \"[System.Media.SystemSounds]::Question.Play(); [void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms'); $n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Warning; $n.Visible = $true; $n.ShowBalloonTip(5000, 'Claude Code', 'Permission required', [System.Windows.Forms.ToolTipIcon]::Warning); Start-Sleep -Seconds 6; $n.Dispose()\" &",
          "timeout": 10
        }
      ]
    }
  ]
}
HOOKS_JSON
}

generate_gitbash_hooks() {
    generate_wsl_hooks
}

# =============================================================================
# JSON TOOL DETECTION
# =============================================================================

find_json_tool() {
    if command -v python3 &>/dev/null; then
        echo "python3"
    elif command -v python &>/dev/null; then
        echo "python"
    elif command -v node &>/dev/null; then
        echo "node"
    else
        echo ""
    fi
}

# =============================================================================
# LIST HOOKS
# =============================================================================

list_hooks() {
    local settings_file="$1"
    local json_tool="$2"

    if [ ! -f "$settings_file" ]; then
        warn "No settings.json found at $settings_file"
        warn "Run Quick Setup first to install hooks."
        exit 0
    fi

    if [ "$json_tool" = "node" ]; then
        node -e "
const fs = require('fs');
let s = {};
try { s = JSON.parse(fs.readFileSync(process.argv[1], 'utf8')); } catch(e) { process.exit(1); }
const hooks = s.hooks || {};
const all = ['Stop', 'Notification', 'SubagentStop', 'PermissionRequest'];
all.forEach(h => {
    const status = hooks[h] ? '\x1b[32menabled\x1b[0m' : '\x1b[90mdisabled\x1b[0m';
    console.log('  ' + h.padEnd(20) + status);
});
" "$settings_file"
    else
        "$json_tool" -c "
import json, sys

settings_file = sys.argv[1]
try:
    with open(settings_file, 'r', encoding='utf-8') as f:
        settings = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    settings = {}

hooks = settings.get('hooks', {})
all_hooks = ['Stop', 'Notification', 'SubagentStop', 'PermissionRequest']
for h in all_hooks:
    if h in hooks:
        print(f'  {h:<20}\033[32menabled\033[0m')
    else:
        print(f'  {h:<20}\033[90mdisabled\033[0m')
" "$settings_file"
    fi
}

# =============================================================================
# DISABLE HOOKS (remove from settings.json)
# =============================================================================

remove_hooks() {
    local settings_file="$1"
    local json_tool="$2"
    shift 2
    local hooks_to_remove=("$@")

    local hooks_str
    hooks_str=$(printf '%s,' "${hooks_to_remove[@]}")
    hooks_str="${hooks_str%,}"

    if [ "$json_tool" = "node" ]; then
        node -e "
const fs = require('fs');
let s = {};
try { s = JSON.parse(fs.readFileSync(process.argv[1], 'utf8')); } catch(e) { process.exit(1); }
const hooks = s.hooks || {};
const toRemove = process.argv[2].split(',');
toRemove.forEach(k => {
    if (hooks[k]) { delete hooks[k]; console.log('Disabled: ' + k); }
    else { console.log('Already disabled: ' + k); }
});
if (Object.keys(hooks).length === 0) delete s.hooks;
else s.hooks = hooks;
fs.writeFileSync(process.argv[1], JSON.stringify(s, null, 2) + '\n');
" "$settings_file" "$hooks_str"
    else
        "$json_tool" -c "
import json, sys

settings_file = sys.argv[1]
to_remove = sys.argv[2].split(',')

try:
    with open(settings_file, 'r', encoding='utf-8') as f:
        settings = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    settings = {}

hooks = settings.get('hooks', {})
for key in to_remove:
    if key in hooks:
        del hooks[key]
        print(f'Disabled: {key}')
    else:
        print(f'Already disabled: {key}')

if not hooks and 'hooks' in settings:
    del settings['hooks']

with open(settings_file, 'w', encoding='utf-8') as f:
    json.dump(settings, f, indent=2)
    f.write('\n')
" "$settings_file" "$hooks_str"
    fi
}

# =============================================================================
# ENABLE HOOKS (add to settings.json with platform-specific commands)
# =============================================================================

add_hooks() {
    local settings_file="$1"
    local json_tool="$2"
    local platform="$3"
    shift 3
    local hooks_to_add=("$@")

    # Generate all platform hooks as JSON (from heredoc — safe, no escaping issues)
    local all_hooks_json=""
    case "$platform" in
        macos)   all_hooks_json="$(generate_macos_hooks)" ;;
        linux)   all_hooks_json="$(generate_linux_hooks)" ;;
        wsl)     all_hooks_json="$(generate_wsl_hooks)" ;;
        gitbash) all_hooks_json="$(generate_gitbash_hooks)" ;;
    esac

    local hooks_str
    hooks_str=$(printf '%s,' "${hooks_to_add[@]}")
    hooks_str="${hooks_str%,}"

    if [ "$json_tool" = "node" ]; then
        node -e "
const fs = require('fs');
const settingsFile = process.argv[1];
const allHooksJson = process.argv[2];
const toAdd = process.argv[3].split(',');

const allHooks = JSON.parse(allHooksJson);
let s = {};
try { s = JSON.parse(fs.readFileSync(settingsFile, 'utf8')); } catch(e) {}
if (!s.hooks) s.hooks = {};

toAdd.forEach(hook => {
    if (allHooks[hook]) {
        s.hooks[hook] = allHooks[hook];
        console.log('Enabled: ' + hook);
    } else {
        console.log('Skipped (unknown): ' + hook);
    }
});

fs.writeFileSync(settingsFile, JSON.stringify(s, null, 2) + '\n');
" "$settings_file" "$all_hooks_json" "$hooks_str"
    else
        "$json_tool" -c "
import json, sys

settings_file = sys.argv[1]
all_hooks_json = sys.argv[2]
to_add = sys.argv[3].split(',')

all_hooks = json.loads(all_hooks_json)

try:
    with open(settings_file, 'r', encoding='utf-8') as f:
        settings = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    settings = {}

if 'hooks' not in settings:
    settings['hooks'] = {}

for hook in to_add:
    if hook in all_hooks:
        settings['hooks'][hook] = all_hooks[hook]
        print(f'Enabled: {hook}')
    else:
        print(f'Skipped (unknown): {hook}')

with open(settings_file, 'w', encoding='utf-8') as f:
    json.dump(settings, f, indent=2)
    f.write('\n')
" "$settings_file" "$all_hooks_json" "$hooks_str"
    fi
}

# =============================================================================
# RESOLVE HOOK NAME (case-insensitive)
# =============================================================================

resolve_hook_name() {
    local name="$1"
    for h in "${ALL_HOOKS[@]}"; do
        if [ "$h" = "$name" ]; then
            echo "$h"
            return 0
        fi
    done
    local lower_name
    lower_name="$(echo "$name" | tr '[:upper:]' '[:lower:]')"
    for h in "${ALL_HOOKS[@]}"; do
        local lower_h
        lower_h="$(echo "$h" | tr '[:upper:]' '[:lower:]')"
        if [ "$lower_h" = "$lower_name" ]; then
            echo "$h"
            return 0
        fi
    done
    return 1
}

# =============================================================================
# USAGE
# =============================================================================

show_usage() {
    printf "${BOLD}${CYAN}echook - Quick Configure${NC}\n"
    printf "Lightweight hook manager for Quick Setup (Lite tier) users\n\n"
    printf "${BOLD}Usage:${NC}\n"
    printf "  quick-configure.sh --list                          Show hook status\n"
    printf "  quick-configure.sh --enable <Hook> [Hook...]       Enable hooks\n"
    printf "  quick-configure.sh --disable <Hook> [Hook...]      Disable hooks\n"
    printf "  quick-configure.sh --only <Hook> [Hook...]         Keep only these hooks\n"
    printf "  quick-configure.sh --snooze [DURATION]             Temporarily mute all hooks (default: 30m)\n"
    printf "  quick-configure.sh --resume                        Cancel snooze, resume hooks\n"
    printf "  quick-configure.sh --snooze-status                 Show snooze status\n"
    printf "  quick-configure.sh --help                          Show this help\n"
    printf "\n${BOLD}Available hooks:${NC}\n"
    printf "  Stop              - Sound + notification when tasks complete\n"
    printf "  Notification      - Alert when authorization is needed\n"
    printf "  SubagentStop      - Alert when background tasks finish\n"
    printf "  PermissionRequest - Alert when permission dialog appears\n"
    printf "\n${BOLD}Examples:${NC}\n"
    printf "  # Disable SubagentStop (too noisy):\n"
    printf "  quick-configure.sh --disable SubagentStop\n\n"
    printf "  # Keep only Stop and Notification:\n"
    printf "  quick-configure.sh --only Stop Notification\n\n"
    printf "  # Re-enable everything:\n"
    printf "  quick-configure.sh --enable Stop Notification SubagentStop PermissionRequest\n\n"
    printf "  # Snooze all hooks for 1 hour:\n"
    printf "  quick-configure.sh --snooze 1h\n\n"
    printf "  # Check snooze status:\n"
    printf "  quick-configure.sh --snooze-status\n\n"
    printf "${BOLD}Remote usage (no clone needed):${NC}\n"
    printf "  curl -sL https://raw.githubusercontent.com/ChanMeng666/echook/master/scripts/quick-configure.sh | bash -s -- --list\n"
    printf "\n"
}

# =============================================================================
# MAIN
# =============================================================================

main() {
    if [ $# -eq 0 ]; then
        show_usage
        exit 0
    fi

    local settings_file="$HOME/.claude/settings.json"

    # Find JSON tool
    local json_tool
    json_tool="$(find_json_tool)"
    if [ -z "$json_tool" ]; then
        error "No Python or Node.js found. One is required for JSON editing."
        exit 1
    fi

    # Detect platform (needed for --enable/--only)
    detect_platform

    local action=""
    local hook_args=()

    while [ $# -gt 0 ]; do
        case "$1" in
            --list|-l)
                action="list"
                shift
                ;;
            --enable|-e)
                action="enable"
                shift
                while [ $# -gt 0 ] && [[ ! "$1" =~ ^-- ]]; do
                    hook_args+=("$1")
                    shift
                done
                ;;
            --disable|-d)
                action="disable"
                shift
                while [ $# -gt 0 ] && [[ ! "$1" =~ ^-- ]]; do
                    hook_args+=("$1")
                    shift
                done
                ;;
            --only|-o)
                action="only"
                shift
                while [ $# -gt 0 ] && [[ ! "$1" =~ ^-- ]]; do
                    hook_args+=("$1")
                    shift
                done
                ;;
            --snooze|-s)
                action="snooze"
                shift
                if [ $# -gt 0 ] && [[ ! "$1" =~ ^-- ]]; then
                    hook_args+=("$1")
                    shift
                fi
                ;;
            --resume|--unsnooze)
                action="resume"
                shift
                ;;
            --snooze-status)
                action="snooze-status"
                shift
                ;;
            --help|-h)
                show_usage
                exit 0
                ;;
            *)
                error "Unknown option: $1"
                printf "Run with --help for usage.\n"
                exit 1
                ;;
        esac
    done

    case "$action" in
        list)
            printf "${BOLD}Quick Setup hooks in %s:${NC}\n" "$settings_file"
            list_hooks "$settings_file" "$json_tool"
            ;;

        disable)
            if [ ${#hook_args[@]} -eq 0 ]; then
                error "No hooks specified. Usage: --disable <Hook> [Hook...]"
                exit 1
            fi
            local resolved=()
            for name in "${hook_args[@]}"; do
                local canonical
                if canonical="$(resolve_hook_name "$name")"; then
                    resolved+=("$canonical")
                else
                    error "Unknown hook: $name (available: ${ALL_HOOKS[*]})"
                    exit 1
                fi
            done
            remove_hooks "$settings_file" "$json_tool" "${resolved[@]}"
            printf "\n${BOLD}Restart Claude Code for changes to take effect.${NC}\n"
            ;;

        enable)
            if [ ${#hook_args[@]} -eq 0 ]; then
                error "No hooks specified. Usage: --enable <Hook> [Hook...]"
                exit 1
            fi
            if [ "$PLATFORM" = "unknown" ]; then
                error "Could not detect platform. Supported: macOS, Linux, WSL, Git Bash"
                exit 1
            fi
            if [ ! -f "$settings_file" ]; then
                mkdir -p "$(dirname "$settings_file")"
                echo '{}' > "$settings_file"
            fi
            local resolved=()
            for name in "${hook_args[@]}"; do
                local canonical
                if canonical="$(resolve_hook_name "$name")"; then
                    resolved+=("$canonical")
                else
                    error "Unknown hook: $name (available: ${ALL_HOOKS[*]})"
                    exit 1
                fi
            done
            add_hooks "$settings_file" "$json_tool" "$PLATFORM" "${resolved[@]}"
            printf "\n${BOLD}Restart Claude Code for changes to take effect.${NC}\n"
            ;;

        only)
            if [ ${#hook_args[@]} -eq 0 ]; then
                error "No hooks specified. Usage: --only <Hook> [Hook...]"
                exit 1
            fi
            if [ "$PLATFORM" = "unknown" ]; then
                error "Could not detect platform. Supported: macOS, Linux, WSL, Git Bash"
                exit 1
            fi
            if [ ! -f "$settings_file" ]; then
                mkdir -p "$(dirname "$settings_file")"
                echo '{}' > "$settings_file"
            fi
            local keep=()
            for name in "${hook_args[@]}"; do
                local canonical
                if canonical="$(resolve_hook_name "$name")"; then
                    keep+=("$canonical")
                else
                    error "Unknown hook: $name (available: ${ALL_HOOKS[*]})"
                    exit 1
                fi
            done
            # Remove hooks not in the keep list
            local to_remove=()
            for h in "${ALL_HOOKS[@]}"; do
                local found=false
                for k in "${keep[@]}"; do
                    if [ "$h" = "$k" ]; then
                        found=true
                        break
                    fi
                done
                if [ "$found" = false ]; then
                    to_remove+=("$h")
                fi
            done
            if [ ${#to_remove[@]} -gt 0 ]; then
                remove_hooks "$settings_file" "$json_tool" "${to_remove[@]}"
            fi
            add_hooks "$settings_file" "$json_tool" "$PLATFORM" "${keep[@]}"
            printf "\n${BOLD}Restart Claude Code for changes to take effect.${NC}\n"
            ;;

        snooze)
            # Inline snooze (self-contained, no external script needed)
            local duration_str="${hook_args[0]:-30m}"
            local duration_seconds=0

            if [[ "$duration_str" =~ ^([0-9]+)h$ ]]; then
                duration_seconds=$(( ${BASH_REMATCH[1]} * 3600 ))
            elif [[ "$duration_str" =~ ^([0-9]+)m$ ]]; then
                duration_seconds=$(( ${BASH_REMATCH[1]} * 60 ))
            elif [[ "$duration_str" =~ ^([0-9]+)s$ ]]; then
                duration_seconds=$(( ${BASH_REMATCH[1]} ))
            elif [[ "$duration_str" =~ ^([0-9]+)$ ]]; then
                duration_seconds=$(( ${BASH_REMATCH[1]} * 60 ))
            else
                error "Invalid duration format '$duration_str'. Examples: 30m, 1h, 2h, 90m"
                exit 1
            fi

            if [ "$duration_seconds" -le 0 ]; then
                error "Duration must be greater than 0"
                exit 1
            fi

            if [ "$duration_seconds" -gt 86400 ]; then
                warn "Snoozing for more than 24 hours. Consider disabling hooks instead."
            fi

            mkdir -p "$QUEUE_DIR" 2>/dev/null
            local snooze_until=$(( $(date +%s) + duration_seconds ))
            echo "$snooze_until" > "$SNOOZE_FILE"

            success "Snoozed! All audio hooks muted for $duration_str"
            printf "Hooks will auto-resume at %s\n" "$(date -d "@$snooze_until" 2>/dev/null || date -r "$snooze_until" 2>/dev/null || echo "epoch $snooze_until")"
            printf "\nTo cancel: quick-configure.sh --resume\n"
            ;;

        resume)
            if [ -f "$SNOOZE_FILE" ]; then
                rm -f "$SNOOZE_FILE"
                success "Resumed! Audio hooks are active again."
            else
                info "Not snoozed. Audio hooks are already active."
            fi
            ;;

        snooze-status)
            if [ ! -f "$SNOOZE_FILE" ]; then
                info "Not snoozed — audio hooks are active."
            else
                local snooze_until
                snooze_until="$(cat "$SNOOZE_FILE" 2>/dev/null)" || {
                    info "Not snoozed — audio hooks are active."
                    exit 0
                }
                snooze_until="${snooze_until%%.*}"
                local current_time
                current_time="$(date +%s)"
                if [ "$current_time" -lt "$snooze_until" ] 2>/dev/null; then
                    local remaining=$(( snooze_until - current_time ))
                    local hours=$(( remaining / 3600 ))
                    local minutes=$(( (remaining % 3600) / 60 ))
                    local friendly=""
                    if [ "$hours" -gt 0 ]; then friendly="${hours}h "; fi
                    if [ "$minutes" -gt 0 ]; then friendly="${friendly}${minutes}m"; fi
                    if [ -z "$friendly" ]; then friendly="${remaining}s"; fi
                    warn "Snoozed — ~${friendly} remaining"
                    printf "Resumes at %s\n" "$(date -d "@$snooze_until" 2>/dev/null || date -r "$snooze_until" 2>/dev/null || echo "epoch $snooze_until")"
                    printf "\nTo cancel: quick-configure.sh --resume\n"
                else
                    info "Snooze expired — audio hooks are active."
                    rm -f "$SNOOZE_FILE" 2>/dev/null
                fi
            fi
            ;;

        *)
            show_usage
            exit 0
            ;;
    esac
}

main "$@"
