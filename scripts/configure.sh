#!/bin/bash
# echook - Dual-Mode Configuration Tool
# Interactive Mode (no args): Menu-driven interface for humans
# Programmatic Mode (with args): CLI interface for Claude Code and scripts
# Compatible with bash 3.2+ (macOS default)
#
# v5.0: When invoked with no arguments AND stdin is not a TTY (or
# CLAUDE_NONINTERACTIVE=1), this script emits a JSON pointer to the
# audio-hooks CLI instead of opening the human menu. Programmatic mode
# (calling with flags like --theme, --enable, --snooze) is unchanged.

set -e

# AI-first guard: if invoked with no args by a non-human caller, redirect to
# the audio-hooks CLI which is the canonical machine interface.
if [ $# -eq 0 ] && { [ ! -t 0 ] || [ -n "${CLAUDE_NONINTERACTIVE:-}" ]; }; then
    printf '{"ok":false,"error":{"code":"INTERACTIVE_SCRIPT","message":"configure.sh interactive menu is human-only. Use the audio-hooks CLI instead.","suggested_command":"audio-hooks manifest","hint":"Pass flags like --theme, --enable, --disable, --snooze to use programmatic mode."}}\n'
    exit 0
fi

# Bash version compatibility notice
if [ "${BASH_VERSION%%.*}" -eq 3 ]; then
    # Running on bash 3.x (likely macOS)
    # Script has been adapted for bash 3.2 compatibility
    :  # No-op, script will work fine
fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
NC='\033[0m'

# Directories
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_FILE="$PROJECT_DIR/config/user_preferences.json"
AUDIO_DIR="$PROJECT_DIR/audio"

# Source hook config for audio playback testing
source "$PROJECT_DIR/hooks/shared/hook_config.sh" 2>/dev/null || true

#=============================================================================
# CONFIGURATION STATE - Using parallel arrays for bash 3.2 compatibility
#=============================================================================

# Hook names array (indexed)
HOOK_NAMES=("notification" "stop" "pretooluse" "posttooluse" "posttoolusefailure" "userpromptsubmit" "subagent_stop" "subagent_start" "precompact" "session_start" "session_end" "permission_request" "teammate_idle" "task_completed" "stop_failure" "postcompact" "config_change" "instructions_loaded" "worktree_create" "worktree_remove" "elicitation" "elicitation_result")

# Parallel arrays for enabled status and descriptions
HOOK_ENABLED=()
HOOK_DESCRIPTIONS=()


# Initialize descriptions
init_descriptions() {
    HOOK_DESCRIPTIONS[0]="⚠️  Authorization/confirmation requests (CRITICAL)"
    HOOK_DESCRIPTIONS[1]="✅ Task completion"
    HOOK_DESCRIPTIONS[2]="🔨 Before tool execution (can be noisy)"
    HOOK_DESCRIPTIONS[3]="📊 After tool execution (very noisy)"
    HOOK_DESCRIPTIONS[4]="❌ Tool execution failed"
    HOOK_DESCRIPTIONS[5]="💬 User prompt submission"
    HOOK_DESCRIPTIONS[6]="🤖 Subagent task completion"
    HOOK_DESCRIPTIONS[7]="🚀 Subagent spawned"
    HOOK_DESCRIPTIONS[8]="🗜️  Before conversation compaction"
    HOOK_DESCRIPTIONS[9]="👋 Session start"
    HOOK_DESCRIPTIONS[10]="👋 Session end"
    HOOK_DESCRIPTIONS[11]="🔐 Permission dialog (CRITICAL)"
    HOOK_DESCRIPTIONS[12]="💤 Teammate idle (Agent Teams)"
    HOOK_DESCRIPTIONS[13]="🏁 Task completed (Agent Teams)"
    HOOK_DESCRIPTIONS[14]="⛔ API error / stop failure"
    HOOK_DESCRIPTIONS[15]="📦 After context compaction"
    HOOK_DESCRIPTIONS[16]="⚙️  Configuration file changed"
    HOOK_DESCRIPTIONS[17]="📄 Instructions/rules file loaded"
    HOOK_DESCRIPTIONS[18]="🌳 Worktree created (isolation)"
    HOOK_DESCRIPTIONS[19]="🧹 Worktree removed (cleanup)"
    HOOK_DESCRIPTIONS[20]="📝 MCP elicitation (input needed)"
    HOOK_DESCRIPTIONS[21]="✉️  Elicitation response submitted"
}

# Get index of hook by name
get_hook_index() {
    local hook_name=$1
    for i in "${!HOOK_NAMES[@]}"; do
        if [[ "${HOOK_NAMES[$i]}" == "$hook_name" ]]; then
            echo "$i"
            return 0
        fi
    done
    return 1
}

# Get enabled status by hook name
is_hook_enabled() {
    local hook_name=$1
    local index=$(get_hook_index "$hook_name")
    if [ -n "$index" ]; then
        echo "${HOOK_ENABLED[$index]}"
    else
        echo "false"
    fi
}

# Set enabled status by hook name
set_hook_enabled() {
    local hook_name=$1
    local enabled=$2
    local index=$(get_hook_index "$hook_name")
    if [ -n "$index" ]; then
        HOOK_ENABLED[$index]="$enabled"
    fi
}

# Initialize hook data
init_hooks() {
    # Initialize descriptions
    init_descriptions

    # Load current configuration
    if [ -f "$CONFIG_FILE" ]; then
        load_configuration
    else
        # Use defaults
        HOOK_ENABLED[0]="true"   # notification
        HOOK_ENABLED[1]="true"   # stop
        HOOK_ENABLED[2]="false"  # pretooluse
        HOOK_ENABLED[3]="false"  # posttooluse
        HOOK_ENABLED[4]="false"  # posttoolusefailure
        HOOK_ENABLED[5]="false"  # userpromptsubmit
        HOOK_ENABLED[6]="true"   # subagent_stop
        HOOK_ENABLED[7]="false"  # subagent_start
        HOOK_ENABLED[8]="false"  # precompact
        HOOK_ENABLED[9]="false"  # session_start
        HOOK_ENABLED[10]="false" # session_end
        HOOK_ENABLED[11]="true"  # permission_request
        HOOK_ENABLED[12]="false" # teammate_idle
        HOOK_ENABLED[13]="false" # task_completed
        HOOK_ENABLED[14]="false" # stop_failure
        HOOK_ENABLED[15]="false" # postcompact
        HOOK_ENABLED[16]="false" # config_change
        HOOK_ENABLED[17]="false" # instructions_loaded
        HOOK_ENABLED[18]="false" # worktree_create
        HOOK_ENABLED[19]="false" # worktree_remove
        HOOK_ENABLED[20]="false" # elicitation
        HOOK_ENABLED[21]="false" # elicitation_result
    fi
}

load_configuration() {
    for i in "${!HOOK_NAMES[@]}"; do
        local hook="${HOOK_NAMES[$i]}"
        local enabled=$(python3 -c "import json; config=json.load(open('$CONFIG_FILE')); print(str(config.get('enabled_hooks', {}).get('$hook', False)).lower())")
        HOOK_ENABLED[$i]=$([[ "$enabled" == "true" ]] && echo "true" || echo "false")
    done

}

save_configuration() {
    local config_file="$1"
    python3 << PYTHON_SCRIPT
import json
import sys
import os

config_file = "$config_file"

# Load existing config or create new
try:
    with open(config_file, 'r') as f:
        config = json.load(f)
except:
    config = {
        "version": "2.0.0",
        "playback_settings": {
            "queue_enabled": True,
            "max_queue_size": 5,
            "debounce_ms": 500
        }
    }

# Update enabled_hooks from environment variables
import os
enabled_hooks = {}
hooks = ["notification", "stop", "pretooluse", "posttooluse", "posttoolusefailure", "userpromptsubmit", "subagent_stop", "subagent_start", "precompact", "session_start", "session_end", "permission_request", "teammate_idle", "task_completed"]

for hook in hooks:
    env_var = f"HOOK_{hook.upper()}"
    enabled_hooks[hook] = os.environ.get(env_var, "false") == "true"

config['enabled_hooks'] = enabled_hooks

# Save configuration
with open(config_file, 'w') as f:
    json.dump(config, f, indent=2)

print("Configuration saved successfully!")
PYTHON_SCRIPT

    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓${NC} Configuration saved to $CONFIG_FILE"
        return 0
    else
        echo -e "${RED}✗${NC} Failed to save configuration"
        return 1
    fi
}

#=============================================================================
# UI FUNCTIONS
#=============================================================================

clear_screen() {
    clear
}

print_header() {
    clear_screen
    echo -e "${BLUE}${BOLD}╔════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}${BOLD}║   echook Configuration       ║${NC}"
    echo -e "${BLUE}${BOLD}╚════════════════════════════════════════════════╝${NC}"
    echo ""
}

print_hook_status() {
    local index=$1
    local enabled="${HOOK_ENABLED[$index]}"

    if [[ "$enabled" == "true" ]]; then
        echo -e "${GREEN}[✓]${NC}"
    else
        echo -e "${RED}[ ]${NC}"
    fi
}

display_main_menu() {
    print_header

    echo -e "${CYAN}${BOLD}Current Configuration:${NC}\n"

    # Display all hooks with status
    for i in "${!HOOK_NAMES[@]}"; do
        local status=$(print_hook_status "$i")
        local desc="${HOOK_DESCRIPTIONS[$i]}"
        printf "${BOLD}%d.${NC} %s ${desc}\n" $((i + 1)) "$status"
    done

    echo ""
    echo -e "${CYAN}${BOLD}Options:${NC}"
    echo -e "  ${BOLD}[1-14]${NC} Toggle hook on/off"
    echo -e "  ${BOLD}[R]${NC}   Reset to recommended defaults"
    echo -e "  ${BOLD}[T]${NC}   Test audio files"
    echo -e "  ${BOLD}[S]${NC}   Save and exit"
    echo -e "  ${BOLD}[Q]${NC}   Quit without saving"
    echo ""
}

get_hook_index_by_number() {
    local num=$1
    if [ "$num" -ge 1 ] && [ "$num" -le 14 ]; then
        echo $((num - 1))
    else
        echo ""
    fi
}

toggle_hook() {
    local index=$1
    local hook_name="${HOOK_NAMES[$index]}"

    if [[ "${HOOK_ENABLED[$index]}" == "true" ]]; then
        HOOK_ENABLED[$index]="false"
        echo -e "${YELLOW}Disabled${NC} $hook_name"
    else
        HOOK_ENABLED[$index]="true"
        echo -e "${GREEN}Enabled${NC} $hook_name"
    fi

    sleep 0.5
}

reset_to_defaults() {
    print_header
    echo -e "${YELLOW}Reset to recommended defaults?${NC}\n"
    echo -e "Recommended configuration:"
    echo -e "  ${GREEN}✓${NC} Notification (authorization/confirmation)"
    echo -e "  ${GREEN}✓${NC} Stop (task completion)"
    echo -e "  ${GREEN}✓${NC} SubagentStop (background tasks)"
    echo -e "  ${GREEN}✓${NC} PermissionRequest (permission dialog)"
    echo -e "  ${RED}✗${NC} All others (disabled)"
    echo ""
    read -p "Confirm reset? (y/N): " -n 1 -r
    echo ""

    if [[ $REPLY =~ ^[Yy]$ ]]; then
        HOOK_ENABLED[0]="true"   # notification
        HOOK_ENABLED[1]="true"   # stop
        HOOK_ENABLED[2]="false"  # pretooluse
        HOOK_ENABLED[3]="false"  # posttooluse
        HOOK_ENABLED[4]="false"  # posttoolusefailure
        HOOK_ENABLED[5]="false"  # userpromptsubmit
        HOOK_ENABLED[6]="true"   # subagent_stop
        HOOK_ENABLED[7]="false"  # subagent_start
        HOOK_ENABLED[8]="false"  # precompact
        HOOK_ENABLED[9]="false"  # session_start
        HOOK_ENABLED[10]="false" # session_end
        HOOK_ENABLED[11]="true"  # permission_request
        HOOK_ENABLED[12]="false" # teammate_idle
        HOOK_ENABLED[13]="false" # task_completed

        echo -e "${GREEN}✓${NC} Reset to defaults"
        sleep 1
    fi
}

test_audio_files() {
    print_header
    echo -e "${CYAN}${BOLD}Audio File Testing${NC}\n"

    local audio_files=(
        "default/notification-urgent.mp3"
        "default/task-complete.mp3"
        "default/task-starting.mp3"
        "default/task-progress.mp3"
        "default/tool-failed.mp3"
        "default/prompt-received.mp3"
        "default/subagent-complete.mp3"
        "default/subagent-start.mp3"
        "default/notification-info.mp3"
        "default/session-start.mp3"
        "default/session-end.mp3"
        "default/permission-request.mp3"
        "default/teammate-idle.mp3"
        "default/team-task-done.mp3"
    )

    echo -e "Testing enabled hooks only...\n"

    local tested=0
    for i in "${!HOOK_NAMES[@]}"; do
        local hook="${HOOK_NAMES[$i]}"
        local audio_file="$AUDIO_DIR/${audio_files[$i]}"

        if [[ "${HOOK_ENABLED[$i]}" == "true" ]]; then
            if [ -f "$audio_file" ]; then
                echo -e "${CYAN}Playing:${NC} $hook (${audio_files[$i]})"
                play_audio_internal "$audio_file" 2>/dev/null
                sleep 3
                ((tested++))
            else
                echo -e "${YELLOW}⚠${NC} $hook: Audio file not found"
            fi
        fi
    done

    if [ $tested -eq 0 ]; then
        echo -e "${YELLOW}No enabled hooks to test!${NC}"
    else
        echo -e "\n${GREEN}✓${NC} Tested $tested audio file(s)"
    fi

    echo ""
    read -p "Press Enter to continue..."
}

#=============================================================================
# AUDIO THEME SWITCHING
#=============================================================================

apply_theme() {
    local theme=$1
    python3 << PYTHON_SCRIPT
import json

config_file = "$CONFIG_FILE"
theme = "$theme"

try:
    with open(config_file, 'r') as f:
        config = json.load(f)
except:
    config = {}

config['audio_theme'] = theme

with open(config_file, 'w') as f:
    json.dump(config, f, indent=2)

print("OK")
PYTHON_SCRIPT
}

# Programmatic per-hook notification mode setting
cmd_hook_mode() {
    local assignments=("$@")
    if [ ${#assignments[@]} -eq 0 ]; then
        echo -e "${RED}Error: At least one hook=mode assignment required${NC}" >&2
        echo "Usage: $0 --hook-mode <hook>=<mode> [hook2=mode2 ...]" >&2
        echo "Valid modes: audio_only, notification_only, audio_and_notification, disabled" >&2
        echo "Example: $0 --hook-mode pretooluse=audio_only posttooluse=disabled" >&2
        exit 1
    fi

    local valid_modes="audio_only notification_only audio_and_notification disabled"
    local changed=0

    for assignment in "${assignments[@]}"; do
        if [[ ! "$assignment" =~ ^([a-z_]+)=(.+)$ ]]; then
            echo -e "${YELLOW}Warning: Invalid format '$assignment', use hook=mode${NC}" >&2
            continue
        fi

        local hook_name="${BASH_REMATCH[1]}"
        local mode="${BASH_REMATCH[2]}"

        # Validate hook name
        local index=$(get_hook_index "$hook_name")
        if [ -z "$index" ]; then
            echo -e "${YELLOW}Warning: Unknown hook '$hook_name', skipping${NC}" >&2
            continue
        fi

        # Validate mode
        local mode_valid=false
        for vm in $valid_modes; do
            if [[ "$mode" == "$vm" ]]; then
                mode_valid=true
                break
            fi
        done
        if [[ "$mode_valid" != "true" ]]; then
            echo -e "${YELLOW}Warning: Invalid mode '$mode' for $hook_name, skipping${NC}" >&2
            echo "  Valid modes: $valid_modes" >&2
            continue
        fi

        echo -e "${GREEN}✓${NC} Set $hook_name mode = $mode"
        ((changed++))
    done

    if [ $changed -eq 0 ]; then
        echo -e "${RED}No valid assignments to apply${NC}" >&2
        exit 1
    fi

    # Apply all changes via Python
    python3 << PYTHON_SCRIPT
import json

config_file = "$CONFIG_FILE"
assignments = """$@""".split()

try:
    with open(config_file, 'r') as f:
        config = json.load(f)
except:
    config = {}

notification_settings = config.setdefault("notification_settings", {})
per_hook = notification_settings.setdefault("per_hook", {})

for assignment in assignments:
    if '=' in assignment:
        hook_name, mode = assignment.split('=', 1)
        per_hook[hook_name] = mode

# Remove _comment keys from per_hook if present (clean save)
per_hook_clean = {k: v for k, v in per_hook.items() if not k.startswith('_')}
notification_settings["per_hook"] = per_hook_clean

with open(config_file, 'w') as f:
    json.dump(config, f, indent=2)

print("OK")
PYTHON_SCRIPT

    if [ $? -eq 0 ]; then
        echo -e "\n${GREEN}${BOLD}✓ Per-hook notification modes saved${NC}"
        echo -e "${YELLOW}Remember to restart Claude Code to apply changes${NC}"
    else
        echo -e "${RED}✗${NC} Failed to save per-hook modes" >&2
        exit 1
    fi
}

# Programmatic theme switching
cmd_theme() {
    local theme=$1
    if [ -z "$theme" ]; then
        echo -e "${RED}Error: Theme name required${NC}" >&2
        echo "Usage: $0 --theme <default|custom>" >&2
        echo "  default - Professional ElevenLabs voice recordings (audio/default/)" >&2
        echo "  custom  - Modern UI sound effects, no voice (audio/custom/)" >&2
        exit 1
    fi

    if [[ "$theme" != "default" && "$theme" != "custom" ]]; then
        echo -e "${RED}Error: Unknown theme '$theme'${NC}" >&2
        echo "Available themes: default, custom" >&2
        echo "  default - Voice recordings (audio/default/)" >&2
        echo "  custom  - Non-voice chimes (audio/custom/)" >&2
        exit 1
    fi

    if apply_theme "$theme"; then
        echo -e "${GREEN}✓${NC} Audio theme set to: ${BOLD}$theme${NC}"
        if [[ "$theme" == "default" ]]; then
            echo -e "  Using voice recordings from audio/default/"
        else
            echo -e "  Using non-voice chimes from audio/custom/"
        fi

        # Sync hook_runner.py to ~/.claude/hooks/ so the installed copy is up-to-date
        local installed_runner="$HOME/.claude/hooks/hook_runner.py"
        local source_runner="$PROJECT_DIR/hooks/hook_runner.py"
        if [ -f "$source_runner" ] && [ -d "$HOME/.claude/hooks" ]; then
            cp "$source_runner" "$installed_runner"
            echo -e "${GREEN}✓${NC} Updated hook_runner.py in ~/.claude/hooks/"
        fi

        echo -e "${YELLOW}Remember to restart Claude Code to apply changes${NC}"
    else
        echo -e "${RED}✗${NC} Failed to switch theme" >&2
        exit 1
    fi
}

#=============================================================================
# MAIN LOOP
#=============================================================================

main() {
    # Initialize
    init_hooks

    # Main loop
    while true; do
        display_main_menu

        read -p "Enter option: " -r option
        echo ""

        case $option in
            [1-9]|1[0-4])
                local index=$(get_hook_index_by_number $option)
                if [ -n "$index" ]; then
                    toggle_hook "$index"
                fi
                ;;
            [Rr])
                reset_to_defaults
                ;;
            [Tt])
                test_audio_files
                ;;
            [Ss])
                print_header
                echo -e "${CYAN}Saving configuration...${NC}\n"

                # Export hook states as environment variables for Python script
                for i in "${!HOOK_NAMES[@]}"; do
                    local hook="${HOOK_NAMES[$i]}"
                    # Use tr for uppercase conversion (bash 3.2 compatible)
                    local hook_upper=$(echo "$hook" | tr '[:lower:]' '[:upper:]')
                    export HOOK_${hook_upper}="${HOOK_ENABLED[$i]}"
                done

                if save_configuration "$CONFIG_FILE"; then
                    echo ""
                    echo -e "${GREEN}${BOLD}Configuration saved successfully!${NC}"
                    echo -e "${YELLOW}${BOLD}Remember to restart Claude Code to apply changes.${NC}"
                    echo ""
                    exit 0
                else
                    echo -e "${RED}Failed to save configuration${NC}"
                    read -p "Press Enter to continue..."
                fi
                ;;
            [Qq])
                print_header
                echo -e "${YELLOW}Quit without saving?${NC}\n"
                read -p "Confirm (y/N): " -n 1 -r
                echo ""
                if [[ $REPLY =~ ^[Yy]$ ]]; then
                    echo -e "${CYAN}Configuration not saved. Exiting...${NC}"
                    exit 0
                fi
                ;;
            *)
                ;;
        esac
    done
}

#=============================================================================
# PROGRAMMATIC MODE - CLI Interface for Scripts and Claude Code
#=============================================================================

print_usage() {
    cat << EOF
${BOLD}echook - Configuration Tool${NC}

${CYAN}INTERACTIVE MODE${NC} (no arguments):
  ${BOLD}$0${NC}
    Launch interactive menu for configuration

${CYAN}PROGRAMMATIC MODE${NC} (with arguments):
  ${BOLD}--help, -h${NC}
    Show this help message

  ${BOLD}--list, -l${NC}
    List all hooks and their current status

  ${BOLD}--get <hook>${NC}
    Get status of a specific hook (returns 'true' or 'false')
    Example: $0 --get notification

  ${BOLD}--enable <hook> [hook2 ...]${NC}
    Enable one or more hooks
    Example: $0 --enable notification stop subagent_stop

  ${BOLD}--disable <hook> [hook2 ...]${NC}
    Disable one or more hooks
    Example: $0 --disable pretooluse posttooluse

  ${BOLD}--set <hook>=<value>${NC}
    Set hook to specific value (true/false)
    Example: $0 --set notification=true --set pretooluse=false

  ${BOLD}--theme <default|custom>${NC}
    Switch audio theme for all hooks at once
    default - Professional ElevenLabs voice recordings (audio/default/)
    custom  - Modern UI sound effects, no voice (audio/custom/)
    Example: $0 --theme custom

  ${BOLD}--hook-mode <hook>=<mode> [hook2=mode2 ...]${NC}
    Set per-hook notification mode overrides
    Valid modes: audio_only, notification_only, audio_and_notification, disabled
    Hooks not listed fall back to the global notification_settings.mode
    Example: $0 --hook-mode pretooluse=audio_only posttooluse=disabled

  ${BOLD}--reset${NC}
    Reset to recommended defaults
    (Enables: notification, stop, subagent_stop, permission_request; Disables: all others)

  ${BOLD}--snooze [DURATION]${NC}
    Temporarily mute all audio hooks (default: 30m)
    Examples: --snooze 30m, --snooze 1h, --snooze 2h

  ${BOLD}--resume${NC}
    Cancel snooze early, resume all hooks

  ${BOLD}--snooze-status${NC}
    Show current snooze status

  ${BOLD}--apply${NC}
    Save configuration without prompting
    (Auto-applied after --enable, --disable, --set, --reset)

${CYAN}AVAILABLE HOOKS${NC}:
  notification, stop, pretooluse, posttooluse, posttoolusefailure,
  userpromptsubmit, subagent_stop, subagent_start, precompact,
  session_start, session_end, permission_request,
  teammate_idle, task_completed

${CYAN}EXAMPLES${NC}:
  # Switch to non-voice chime sounds
  $0 --theme custom

  # Switch back to voice recordings
  $0 --theme default

  # Enable multiple hooks at once
  $0 --enable notification stop subagent_stop

  # Disable noisy hooks
  $0 --disable pretooluse posttooluse

  # Mixed operations
  $0 --enable notification --disable pretooluse --set stop=true

  # Set per-hook notification modes (audio only for noisy hooks)
  $0 --hook-mode pretooluse=audio_only posttooluse=disabled

  # Check if notification hook is enabled
  $0 --get notification

  # List all current settings
  $0 --list

${YELLOW}Note:${NC} Changes are automatically saved in programmatic mode.
      Remember to restart Claude Code to apply changes.
EOF
}

# List all hooks (programmatic mode)
cmd_list() {
    init_hooks
    echo -e "${CYAN}${BOLD}Hook Configuration:${NC}\n"
    for i in "${!HOOK_NAMES[@]}"; do
        local hook="${HOOK_NAMES[$i]}"
        local enabled="${HOOK_ENABLED[$i]}"
        local desc="${HOOK_DESCRIPTIONS[$i]}"
        if [[ "$enabled" == "true" ]]; then
            printf "  ${GREEN}✓${NC} %-20s ${BOLD}enabled${NC}  %s\n" "$hook" "$desc"
        else
            printf "  ${RED}✗${NC} %-20s ${BOLD}disabled${NC} %s\n" "$hook" "$desc"
        fi
    done
    echo ""
}

# Get status of specific hook (programmatic mode)
cmd_get() {
    local hook_name=$1
    if [ -z "$hook_name" ]; then
        echo -e "${RED}Error: Hook name required${NC}" >&2
        echo "Usage: $0 --get <hook>" >&2
        exit 1
    fi

    init_hooks
    local index=$(get_hook_index "$hook_name")
    if [ -z "$index" ]; then
        echo -e "${RED}Error: Unknown hook '$hook_name'${NC}" >&2
        exit 1
    fi

    echo "${HOOK_ENABLED[$index]}"
}

# Enable hooks (programmatic mode)
cmd_enable() {
    local hooks=("$@")
    if [ ${#hooks[@]} -eq 0 ]; then
        echo -e "${RED}Error: At least one hook name required${NC}" >&2
        echo "Usage: $0 --enable <hook> [hook2 ...]" >&2
        exit 1
    fi

    init_hooks
    local changed=0

    for hook_name in "${hooks[@]}"; do
        local index=$(get_hook_index "$hook_name")
        if [ -z "$index" ]; then
            echo -e "${YELLOW}Warning: Unknown hook '$hook_name', skipping${NC}" >&2
            continue
        fi

        if [[ "${HOOK_ENABLED[$index]}" != "true" ]]; then
            HOOK_ENABLED[$index]="true"
            echo -e "${GREEN}✓${NC} Enabled $hook_name"
            ((changed++))
        else
            echo -e "${CYAN}→${NC} $hook_name already enabled"
        fi
    done

    if [ $changed -gt 0 ]; then
        cmd_save
    fi
}

# Disable hooks (programmatic mode)
cmd_disable() {
    local hooks=("$@")
    if [ ${#hooks[@]} -eq 0 ]; then
        echo -e "${RED}Error: At least one hook name required${NC}" >&2
        echo "Usage: $0 --disable <hook> [hook2 ...]" >&2
        exit 1
    fi

    init_hooks
    local changed=0

    for hook_name in "${hooks[@]}"; do
        local index=$(get_hook_index "$hook_name")
        if [ -z "$index" ]; then
            echo -e "${YELLOW}Warning: Unknown hook '$hook_name', skipping${NC}" >&2
            continue
        fi

        if [[ "${HOOK_ENABLED[$index]}" != "false" ]]; then
            HOOK_ENABLED[$index]="false"
            echo -e "${RED}✗${NC} Disabled $hook_name"
            ((changed++))
        else
            echo -e "${CYAN}→${NC} $hook_name already disabled"
        fi
    done

    if [ $changed -gt 0 ]; then
        cmd_save
    fi
}

# Set hook value (programmatic mode)
cmd_set() {
    local assignments=("$@")
    if [ ${#assignments[@]} -eq 0 ]; then
        echo -e "${RED}Error: At least one assignment required${NC}" >&2
        echo "Usage: $0 --set <hook>=<value>" >&2
        exit 1
    fi

    init_hooks
    local changed=0

    for assignment in "${assignments[@]}"; do
        if [[ ! "$assignment" =~ ^([a-z_]+)=(true|false)$ ]]; then
            echo -e "${YELLOW}Warning: Invalid format '$assignment', use hook=true or hook=false${NC}" >&2
            continue
        fi

        local hook_name="${BASH_REMATCH[1]}"
        local value="${BASH_REMATCH[2]}"

        local index=$(get_hook_index "$hook_name")
        if [ -z "$index" ]; then
            echo -e "${YELLOW}Warning: Unknown hook '$hook_name', skipping${NC}" >&2
            continue
        fi

        if [[ "${HOOK_ENABLED[$index]}" != "$value" ]]; then
            HOOK_ENABLED[$index]="$value"
            if [[ "$value" == "true" ]]; then
                echo -e "${GREEN}✓${NC} Set $hook_name = true"
            else
                echo -e "${RED}✗${NC} Set $hook_name = false"
            fi
            ((changed++))
        else
            echo -e "${CYAN}→${NC} $hook_name already set to $value"
        fi
    done

    if [ $changed -gt 0 ]; then
        cmd_save
    fi
}

# Reset to defaults (programmatic mode)
cmd_reset() {
    init_hooks

    HOOK_ENABLED[0]="true"   # notification
    HOOK_ENABLED[1]="true"   # stop
    HOOK_ENABLED[2]="false"  # pretooluse
    HOOK_ENABLED[3]="false"  # posttooluse
    HOOK_ENABLED[4]="false"  # posttoolusefailure
    HOOK_ENABLED[5]="false"  # userpromptsubmit
    HOOK_ENABLED[6]="true"   # subagent_stop
    HOOK_ENABLED[7]="false"  # subagent_start
    HOOK_ENABLED[8]="false"  # precompact
    HOOK_ENABLED[9]="false"  # session_start
    HOOK_ENABLED[10]="false" # session_end
    HOOK_ENABLED[11]="true"  # permission_request
    HOOK_ENABLED[12]="false" # teammate_idle
    HOOK_ENABLED[13]="false" # task_completed

    echo -e "${GREEN}✓${NC} Reset to recommended defaults:"
    echo -e "  ${GREEN}✓${NC} Enabled: notification, stop, subagent_stop, permission_request"
    echo -e "  ${RED}✗${NC} Disabled: all others"

    cmd_save
}

# Save configuration (programmatic mode)
cmd_save() {
    # Export hook states to environment variables (required by save_configuration)
    for i in "${!HOOK_NAMES[@]}"; do
        local hook="${HOOK_NAMES[$i]}"
        # Use tr for uppercase conversion (bash 3.2 compatible)
        local hook_upper=$(echo "$hook" | tr '[:lower:]' '[:upper:]')
        export HOOK_${hook_upper}="${HOOK_ENABLED[$i]}"
    done

    if save_configuration "$CONFIG_FILE"; then
        echo -e "\n${GREEN}${BOLD}✓ Configuration saved successfully${NC}"
        echo -e "${YELLOW}Remember to restart Claude Code to apply changes${NC}"
    else
        echo -e "\n${RED}${BOLD}✗ Failed to save configuration${NC}" >&2
        exit 1
    fi
}

#=============================================================================
# ARGUMENT PROCESSING
#=============================================================================

process_arguments() {
    # No arguments = interactive mode
    if [ $# -eq 0 ]; then
        return 1  # Signal to run interactive mode
    fi

    # Check if configuration file directory exists
    if [ ! -d "$(dirname "$CONFIG_FILE")" ]; then
        echo -e "${RED}Error: Configuration directory not found${NC}" >&2
        echo -e "${YELLOW}Please run install-complete.sh first${NC}" >&2
        exit 1
    fi

    # Process arguments
    while [ $# -gt 0 ]; do
        case "$1" in
            --help|-h)
                print_usage
                exit 0
                ;;
            --list|-l)
                cmd_list
                exit 0
                ;;
            --get)
                shift
                cmd_get "$1"
                exit 0
                ;;
            --enable)
                shift
                local hooks=()
                while [ $# -gt 0 ] && [[ ! "$1" =~ ^-- ]]; do
                    hooks+=("$1")
                    shift
                done
                cmd_enable "${hooks[@]}"
                continue  # Continue processing other args
                ;;
            --disable)
                shift
                local hooks=()
                while [ $# -gt 0 ] && [[ ! "$1" =~ ^-- ]]; do
                    hooks+=("$1")
                    shift
                done
                cmd_disable "${hooks[@]}"
                continue
                ;;
            --set)
                shift
                local assignments=()
                while [ $# -gt 0 ] && [[ ! "$1" =~ ^-- ]]; do
                    assignments+=("$1")
                    shift
                done
                cmd_set "${assignments[@]}"
                continue
                ;;
            --theme)
                shift
                cmd_theme "$1"
                exit 0
                ;;
            --hook-mode)
                shift
                local hook_modes=()
                while [ $# -gt 0 ] && [[ ! "$1" =~ ^-- ]]; do
                    hook_modes+=("$1")
                    shift
                done
                cmd_hook_mode "${hook_modes[@]}"
                exit 0
                ;;
            --snooze)
                shift
                bash "$PROJECT_DIR/scripts/snooze.sh" "${1:-30m}"
                exit 0
                ;;
            --resume|--unsnooze)
                bash "$PROJECT_DIR/scripts/snooze.sh" resume
                exit 0
                ;;
            --snooze-status)
                bash "$PROJECT_DIR/scripts/snooze.sh" status
                exit 0
                ;;
            --reset)
                cmd_reset
                exit 0
                ;;
            --apply)
                cmd_save
                exit 0
                ;;
            *)
                echo -e "${RED}Error: Unknown option '$1'${NC}" >&2
                echo "Run '$0 --help' for usage information" >&2
                exit 1
                ;;
        esac
        shift
    done

    # If we got here, all programmatic commands executed
    exit 0
}

#=============================================================================
# MAIN ENTRY POINT
#=============================================================================

# Check if configuration file directory exists (for interactive mode)
if [ ! -d "$(dirname "$CONFIG_FILE")" ]; then
    echo -e "${RED}Error: Configuration directory not found${NC}"
    echo -e "${YELLOW}Please run install-complete.sh first${NC}"
    exit 1
fi

# Process arguments first
if ! process_arguments "$@"; then
    # No arguments or process_arguments returned 1 = run interactive mode
    main
fi