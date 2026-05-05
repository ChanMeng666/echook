#!/usr/bin/env bash
# =============================================================================
# echook - Quick Setup (Lite Tier)
# Zero-dependency, zero-Python notification setup for Claude Code
#
# Usage:
#   curl -sL https://raw.githubusercontent.com/ChanMeng666/echook/master/scripts/quick-setup.sh | bash
#   # or
#   bash scripts/quick-setup.sh
#
# This script patches ~/.claude/settings.json with platform-native notification
# commands. No Python, no MP3 files, no cloning required.
#
# Supports: macOS, Linux, WSL, Windows Git Bash
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
NC='\033[0m' # No Color

info()    { printf "${BLUE}[INFO]${NC} %s\n" "$1"; }
success() { printf "${GREEN}[OK]${NC} %s\n" "$1"; }
warn()    { printf "${YELLOW}[WARN]${NC} %s\n" "$1"; }
error()   { printf "${RED}[ERROR]${NC} %s\n" "$1"; }
header()  { printf "\n${BOLD}${CYAN}%s${NC}\n" "$1"; }

# =============================================================================
# PLATFORM DETECTION
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
# HOOK COMMAND GENERATORS
# =============================================================================

# Each function outputs the JSON command string for the hook on the given platform.
# We use distinct system sounds per event type for easy differentiation.

generate_macos_hooks() {
    # afplay plays system sounds directly (no permissions needed, works on all macOS versions)
    # osascript notification is best-effort (may require notification permissions on macOS 15+)
    # sound name is omitted from osascript to avoid double sound when notifications work
    cat <<'HOOKS_JSON'
{
  "Stop": [
    {
      "hooks": [
        {
          "type": "command",
          "command": "afplay /System/Library/Sounds/Glass.aiff 2>/dev/null & osascript -e 'display notification \"Task completed\" with title \"Claude Code\"' 2>/dev/null; true",
          "timeout": 10,
          "async": true
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
          "timeout": 10,
          "async": true
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
          "timeout": 10,
          "async": true
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
          "timeout": 10,
          "async": true
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
          "timeout": 10,
          "async": true
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
          "timeout": 10,
          "async": true
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
          "timeout": 10,
          "async": true
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
          "timeout": 10,
          "async": true
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
          "timeout": 10,
          "async": true
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
          "timeout": 10,
          "async": true
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
          "timeout": 10,
          "async": true
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
          "timeout": 10,
          "async": true
        }
      ]
    }
  ]
}
HOOKS_JSON
}

generate_gitbash_hooks() {
    # Same as WSL - uses powershell.exe available on Windows
    generate_wsl_hooks
}

# =============================================================================
# JSON MERGE UTILITY (pure bash, no jq/python dependency)
# =============================================================================

# Merges hook entries into an existing settings.json.
# Strategy: Read the existing file, parse the "hooks" object, add/replace our
# 3 hook keys, write back. We use Python if available, otherwise a simple
# sed-based approach for the common case.
merge_hooks_into_settings() {
    local settings_file="$1"
    local hooks_json="$2"

    # If python3/python is available, use it for reliable JSON merging
    local python_cmd=""
    if command -v python3 &>/dev/null; then
        python_cmd="python3"
    elif command -v python &>/dev/null; then
        python_cmd="python"
    fi

    if [ -n "$python_cmd" ]; then
        "$python_cmd" -c "
import json, sys

settings_file = sys.argv[1]
hooks_json = sys.argv[2]

# Load existing settings
try:
    with open(settings_file, 'r', encoding='utf-8') as f:
        settings = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    settings = {}

# Parse new hooks
new_hooks = json.loads(hooks_json)

# Merge: add/replace only our hook keys, preserve everything else
if 'hooks' not in settings:
    settings['hooks'] = {}

for key, value in new_hooks.items():
    settings['hooks'][key] = value

# Write back with nice formatting
with open(settings_file, 'w', encoding='utf-8') as f:
    json.dump(settings, f, indent=2)
    f.write('\n')
" "$settings_file" "$hooks_json"
        return $?
    fi

    # Fallback: if no python, try node
    if command -v node &>/dev/null; then
        node -e "
const fs = require('fs');
const settingsFile = process.argv[1];
const hooksJson = process.argv[2];

let settings = {};
try {
    settings = JSON.parse(fs.readFileSync(settingsFile, 'utf8'));
} catch(e) {}

const newHooks = JSON.parse(hooksJson);
if (!settings.hooks) settings.hooks = {};
Object.assign(settings.hooks, newHooks);

fs.writeFileSync(settingsFile, JSON.stringify(settings, null, 2) + '\n');
" "$settings_file" "$hooks_json"
        return $?
    fi

    # Last resort: write hooks-only settings (warn user about manual merge)
    warn "No Python or Node found - writing hooks directly."
    warn "If you had other settings in $settings_file, you may need to merge manually."
    printf '{\n  "hooks": %s\n}\n' "$hooks_json" > "$settings_file"
    return 0
}

# =============================================================================
# MAIN
# =============================================================================

main() {
    header "echook - Quick Setup"
    printf "%s\n" "Zero-dependency notifications for Claude Code"
    printf "%s\n\n" "============================================="

    # Step 1: Detect platform
    detect_platform
    info "Detected platform: $PLATFORM"

    if [ "$PLATFORM" = "unknown" ]; then
        error "Unsupported platform. Supported: macOS, Linux, WSL, Windows Git Bash"
        exit 1
    fi

    # Step 2: Ensure ~/.claude directory exists
    local claude_dir="$HOME/.claude"
    local settings_file="$claude_dir/settings.json"

    if [ ! -d "$claude_dir" ]; then
        info "Creating $claude_dir directory..."
        mkdir -p "$claude_dir"
    fi

    # Step 3: Backup existing settings.json
    if [ -f "$settings_file" ]; then
        local backup_file="${settings_file}.backup.$(date +%Y%m%d_%H%M%S)"
        info "Backing up existing settings to $(basename "$backup_file")"
        cp "$settings_file" "$backup_file"
        success "Backup saved: $backup_file"
    else
        info "No existing settings.json found, creating new one"
        echo '{}' > "$settings_file"
    fi

    # Step 4: Generate platform-specific hooks
    local hooks_json=""
    case "$PLATFORM" in
        macos)
            hooks_json="$(generate_macos_hooks)"
            ;;
        linux)
            hooks_json="$(generate_linux_hooks)"
            # Check for notify-send
            if ! command -v notify-send &>/dev/null; then
                warn "notify-send not found. Install it for desktop notifications:"
                warn "  sudo apt install libnotify-bin"
            fi
            ;;
        wsl)
            hooks_json="$(generate_wsl_hooks)"
            # Check for powershell.exe
            if ! command -v powershell.exe &>/dev/null; then
                warn "powershell.exe not accessible from WSL"
                warn "Notifications may not work. Check WSL interop settings."
            fi
            ;;
        gitbash)
            hooks_json="$(generate_gitbash_hooks)"
            ;;
    esac

    # Step 5: Merge hooks into settings.json
    info "Adding notification hooks to settings.json..."
    if merge_hooks_into_settings "$settings_file" "$hooks_json"; then
        success "Hooks added to $settings_file"
    else
        error "Failed to merge hooks into settings.json"
        exit 1
    fi

    # Step 6: Mark as lite installation (for quick-unsetup to find)
    echo "lite" > "$claude_dir/.hooks_mode"

    # Step 7: Play confirmation sound so user gets immediate feedback
    case "$PLATFORM" in
        macos)
            afplay /System/Library/Sounds/Glass.aiff 2>/dev/null &
            ;;
    esac

    # Step 8: Print summary
    header "Setup Complete!"
    printf "\n"
    success "4 notification hooks installed:"
    printf "  ${GREEN}*${NC} Stop              - Sound + notification when tasks complete\n"
    printf "  ${GREEN}*${NC} Notification      - Alert when authorization is needed\n"
    printf "  ${GREEN}*${NC} SubagentStop      - Alert when background tasks finish\n"
    printf "  ${GREEN}*${NC} PermissionRequest - Alert when permission dialog appears\n"
    printf "\n"
    printf "${BOLD}Next steps:${NC}\n"
    printf "  1. Restart Claude Code (close and reopen terminal)\n"
    printf "  2. Run: ${CYAN}claude \"What is 2+2?\"${NC}\n"
    printf "  3. You should see a notification when the task completes\n"
    printf "\n"

    case "$PLATFORM" in
        macos)
            printf "${BOLD}Platform notes (macOS):${NC}\n"
            printf "  - Audio plays via afplay (works on all macOS versions, no permissions needed)\n"
            printf "  - System sounds: Glass (done), Sosumi (attention), Pop (background)\n"
            printf "  - Desktop notifications via osascript (best-effort)\n"
            printf "  - macOS 15+ (Sequoia): notifications may need permission in\n"
            printf "    System Settings > Notifications > Script Editor\n"
            ;;
        linux)
            printf "${BOLD}Platform notes (Linux):${NC}\n"
            printf "  - Desktop notifications via notify-send\n"
            printf "  - System sounds via paplay (PulseAudio)\n"
            if ! command -v notify-send &>/dev/null; then
                printf "  ${YELLOW}! Install libnotify-bin for notifications: sudo apt install libnotify-bin${NC}\n"
            fi
            ;;
        wsl)
            printf "${BOLD}Platform notes (WSL):${NC}\n"
            printf "  - Notifications play through Windows via PowerShell\n"
            printf "  - System sounds from Windows audio\n"
            ;;
        gitbash)
            printf "${BOLD}Platform notes (Git Bash):${NC}\n"
            printf "  - Notifications play through Windows via PowerShell\n"
            printf "  - System sounds from Windows audio\n"
            ;;
    esac

    printf "\n"
    printf "${BOLD}Want custom audio, TTS, or advanced features?${NC}\n"
    printf "  See: https://github.com/ChanMeng666/echook#full-installation\n"
    printf "\n"
    printf "${BOLD}To remove:${NC}\n"
    printf "  curl -sL https://raw.githubusercontent.com/ChanMeng666/echook/master/scripts/quick-unsetup.sh | bash\n"
    printf "\n"
}

main "$@"
