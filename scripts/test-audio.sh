#!/bin/bash
# echook - Enhanced Audio Test Script v2.0
# Tests all audio files and provides diagnostics
# Compatible with bash 3.2+ (macOS default)
#
# v5.0: This is a human-only interactive menu. AI agents and CI should use
# `audio-hooks test all` instead, which returns structured JSON results.

# AI-first guard: redirect non-TTY callers to the audio-hooks CLI.
if [ ! -t 0 ] || [ -n "${CLAUDE_NONINTERACTIVE:-}" ]; then
    printf '{"ok":false,"error":{"code":"INTERACTIVE_SCRIPT","message":"test-audio.sh is a human-only menu. Use the audio-hooks CLI instead.","suggested_command":"audio-hooks test all"}}\n'
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
BOLD='\033[1m'
NC='\033[0m'

# Directories
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEST_AUDIO_DIR="$PROJECT_DIR/audio/default"  # Test script uses audio/default
HOOKS_DIR="$HOME/.claude/hooks"
CONFIG_FILE="$PROJECT_DIR/config/user_preferences.json"

echo -e "${BLUE}${BOLD}================================================${NC}"
echo -e "${BLUE}${BOLD}  echook - Audio Test v2.0${NC}"
echo -e "${BLUE}${BOLD}================================================${NC}\n"

#=============================================================================
# CHECK PREREQUISITES
#=============================================================================

echo -e "${CYAN}Checking prerequisites...${NC}\n"

# Check if shared library exists
if [ ! -f "$HOOKS_DIR/shared/hook_config.sh" ]; then
    echo -e "${RED}✗ Hook system not installed!${NC}"
    echo ""
    echo "Please run the installer first:"
    echo "  bash $PROJECT_DIR/scripts/install.sh"
    echo ""
    exit 1
fi

echo -e "${GREEN}✓${NC} Hook system is installed\n"

# Source the shared library for audio playback
source "$HOOKS_DIR/shared/hook_config.sh"

# Restore TEST_AUDIO_DIR for test script (hook_config.sh sets AUDIO_DIR differently)
AUDIO_DIR="$TEST_AUDIO_DIR"

#=============================================================================
# CONFIGURATION DATA - Using parallel arrays for bash 3.2 compatibility
#=============================================================================

# Hook names array (indexed)
HOOK_NAMES=("notification" "stop" "pretooluse" "posttooluse" "posttoolusefailure" "userpromptsubmit" "subagent_stop" "subagent_start" "precompact" "session_start" "session_end" "permission_request" "teammate_idle" "task_completed")

# Parallel arrays for configuration data
ENABLED_STATUS=()  # true/false for each hook
AUDIO_FILES=(
    "notification-urgent.mp3"
    "task-complete.mp3"
    "task-starting.mp3"
    "task-progress.mp3"
    "tool-failed.mp3"
    "prompt-received.mp3"
    "subagent-complete.mp3"
    "subagent-start.mp3"
    "notification-info.mp3"
    "session-start.mp3"
    "session-end.mp3"
    "permission-request.mp3"
    "teammate-idle.mp3"
    "team-task-done.mp3"
)
AUDIO_DESCRIPTIONS=(
    "Authorization/Confirmation Requests"
    "Task Completion"
    "Before Tool Execution"
    "After Tool Execution"
    "Tool Execution Failed"
    "User Prompt Submission"
    "Subagent Task Completion"
    "Subagent Spawned"
    "Before Conversation Compaction"
    "Session Start"
    "Session End"
    "Permission Dialog"
    "Teammate Idle (Agent Teams)"
    "Task Completed (Agent Teams)"
)

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
        echo "${ENABLED_STATUS[$index]}"
    else
        echo "false"
    fi
}

# Get audio file by hook name
get_audio_file() {
    local hook_name=$1
    local index=$(get_hook_index "$hook_name")
    if [ -n "$index" ]; then
        echo "${AUDIO_FILES[$index]}"
    fi
}

# Get description by hook name
get_description() {
    local hook_name=$1
    local index=$(get_hook_index "$hook_name")
    if [ -n "$index" ]; then
        echo "${AUDIO_DESCRIPTIONS[$index]}"
    fi
}

#=============================================================================
# LOAD CONFIGURATION
#=============================================================================

echo -e "${CYAN}Loading configuration...${NC}\n"

if [ -f "$CONFIG_FILE" ]; then
    # Load enabled hooks from config
    for i in "${!HOOK_NAMES[@]}"; do
        local hook="${HOOK_NAMES[$i]}"
        local enabled=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('enabled_hooks', {}).get('$hook', False))" 2>/dev/null)
        ENABLED_STATUS[$i]=$([[ "$enabled" == "True" ]] && echo "true" || echo "false")
    done

    echo -e "${GREEN}✓${NC} Configuration loaded from user_preferences.json\n"
else
    # Use defaults
    ENABLED_STATUS[0]="true"   # notification
    ENABLED_STATUS[1]="true"   # stop
    ENABLED_STATUS[2]="false"  # pretooluse
    ENABLED_STATUS[3]="false"  # posttooluse
    ENABLED_STATUS[4]="false"  # posttoolusefailure
    ENABLED_STATUS[5]="false"  # userpromptsubmit
    ENABLED_STATUS[6]="true"   # subagent_stop
    ENABLED_STATUS[7]="false"  # subagent_start
    ENABLED_STATUS[8]="false"  # precompact
    ENABLED_STATUS[9]="false"  # session_start
    ENABLED_STATUS[10]="false" # session_end
    ENABLED_STATUS[11]="true"  # permission_request
    ENABLED_STATUS[12]="false" # teammate_idle
    ENABLED_STATUS[13]="false" # task_completed

    echo -e "${YELLOW}⚠${NC} No config found, using defaults\n"
fi

#=============================================================================
# TEST OPTION
#=============================================================================

echo -e "${CYAN}${BOLD}What would you like to test?${NC}"
echo ""
echo -e "  ${BOLD}1.${NC} Test all enabled hooks (recommended)"
echo -e "  ${BOLD}2.${NC} Test ALL audio files (including disabled hooks)"
echo -e "  ${BOLD}3.${NC} Test specific hook"
echo -e "  ${BOLD}4.${NC} Quick test (task-complete audio only)"
echo ""
read -p "Enter option (1-4): " -n 1 -r TEST_OPTION
echo ""
echo ""

#=============================================================================
# TEST FUNCTIONS
#=============================================================================

test_audio_file() {
    local hook=$1
    local audio_file=$(get_audio_file "$hook")
    local description=$(get_description "$hook")
    local full_path="$AUDIO_DIR/$audio_file"

    echo -e "${CYAN}Testing:${NC} ${BOLD}$description${NC}"
    echo -e "  Hook: $hook"
    echo -e "  File: $audio_file"

    if [ -f "$full_path" ]; then
        local size=$(du -h "$full_path" | cut -f1)
        echo -e "  Size: $size"

        echo -e "  ${BLUE}▶ Playing...${NC}"
        play_audio_internal "$full_path" 2>/dev/null

        sleep 3

        echo -e "  ${GREEN}✓${NC} Playback complete"
    else
        echo -e "  ${RED}✗${NC} File not found!"
    fi

    echo ""
}

#=============================================================================
# TEST EXECUTION
#=============================================================================

case $TEST_OPTION in
    1)
        # Test enabled hooks only
        echo -e "${BLUE}${BOLD}Testing Enabled Hooks${NC}\n"
        echo -e "This will test only the hooks you have enabled.\n"

        tested=0
        for i in "${!HOOK_NAMES[@]}"; do
            hook="${HOOK_NAMES[$i]}"
            if [[ "${ENABLED_STATUS[$i]}" == "true" ]]; then
                test_audio_file "$hook"
                ((tested++))

                if [ $tested -lt 3 ]; then
                    echo -e "${CYAN}Next audio in 2 seconds...${NC}\n"
                    sleep 2
                fi
            fi
        done

        if [ $tested -eq 0 ]; then
            echo -e "${YELLOW}⚠${NC} No enabled hooks found!"
            echo "Run ./scripts/configure.sh to enable hooks."
        else
            echo -e "${GREEN}${BOLD}✓ Tested $tested enabled hook(s)${NC}"
        fi
        ;;

    2)
        # Test all audio files
        echo -e "${BLUE}${BOLD}Testing All Audio Files${NC}\n"
        echo -e "This will play all 14 audio files, including disabled hooks.\n"

        count=0
        for hook in "${HOOK_NAMES[@]}"; do
            test_audio_file "$hook"
            ((count++))

            if [ $count -lt 14 ]; then
                echo -e "${CYAN}Next audio in 2 seconds...${NC}\n"
                sleep 2
            fi
        done

        echo -e "${GREEN}${BOLD}✓ Tested all 14 audio files${NC}"
        ;;

    3)
        # Test specific hook
        echo -e "${BLUE}${BOLD}Test Specific Hook${NC}\n"
        echo "Select a hook to test:"
        echo ""
        echo "   1. Notification (authorization/confirmation)"
        echo "   2. Stop (task completion)"
        echo "   3. PreToolUse (before tool execution)"
        echo "   4. PostToolUse (after tool execution)"
        echo "   5. PostToolUseFailure (tool execution failed)"
        echo "   6. UserPromptSubmit (prompt submission)"
        echo "   7. SubagentStop (subagent completion)"
        echo "   8. SubagentStart (subagent spawned)"
        echo "   9. PreCompact (before compaction)"
        echo "  10. SessionStart (session start)"
        echo "  11. SessionEnd (session end)"
        echo "  12. PermissionRequest (permission dialog)"
        echo "  13. TeammateIdle (teammate idle)"
        echo "  14. TaskCompleted (task completed)"
        echo ""
        read -p "Enter number (1-14): " -r HOOK_NUM
        echo ""

        case $HOOK_NUM in
            1) test_audio_file "notification" ;;
            2) test_audio_file "stop" ;;
            3) test_audio_file "pretooluse" ;;
            4) test_audio_file "posttooluse" ;;
            5) test_audio_file "posttoolusefailure" ;;
            6) test_audio_file "userpromptsubmit" ;;
            7) test_audio_file "subagent_stop" ;;
            8) test_audio_file "subagent_start" ;;
            9) test_audio_file "precompact" ;;
            10) test_audio_file "session_start" ;;
            11) test_audio_file "session_end" ;;
            12) test_audio_file "permission_request" ;;
            13) test_audio_file "teammate_idle" ;;
            14) test_audio_file "task_completed" ;;
            *) echo -e "${RED}Invalid selection${NC}" ;;
        esac
        ;;

    4)
        # Quick test
        echo -e "${BLUE}${BOLD}Quick Test${NC}\n"
        echo -e "Testing task-complete audio (most commonly used)...\n"

        test_audio_file "stop"

        echo -e "${GREEN}${BOLD}✓ Quick test complete${NC}"
        ;;

    *)
        echo -e "${RED}Invalid option${NC}"
        exit 1
        ;;
esac

#=============================================================================
# TROUBLESHOOTING
#=============================================================================

echo ""
echo -e "${CYAN}${BOLD}Did you hear the audio?${NC}\n"

read -p "Did all audio files play correctly? (y/N): " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    echo -e "${YELLOW}${BOLD}Troubleshooting Tips:${NC}\n"

    echo -e "${BOLD}1. Check System Volume${NC}"
    echo "   • Make sure your system volume is not muted"
    echo "   • Try playing audio from another application"
    echo ""

    echo -e "${BOLD}2. Platform-Specific Issues${NC}"

    if grep -qi microsoft /proc/version 2>/dev/null; then
        echo "   • WSL detected: Ensure PowerShell is available"
        echo "   • Windows audio services should be running"
        echo "   • Try: powershell.exe -Command 'Get-Command Out-Host'"
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        echo "   • macOS: afplay should be available (built-in)"
        echo "   • Check System Preferences > Sound > Output"
    else
        echo "   • Linux: Install audio player:"
        echo "     sudo apt-get install mpg123"
        echo "   • Or: sudo apt-get install alsa-utils"
    fi
    echo ""

    echo -e "${BOLD}3. File Permissions${NC}"
    echo "   • Check audio files exist in: $AUDIO_DIR"
    echo "   • Run: ls -la $AUDIO_DIR"
    echo ""

    echo -e "${BOLD}4. Hook Configuration${NC}"
    echo "   • Re-run the installer to verify: ./scripts/install-complete.sh"
    echo "   • Check the installation log for details"
    echo ""

    echo -e "${BOLD}5. Manual Test${NC}"
    echo "   • Try playing an audio file directly:"
    if grep -qi microsoft /proc/version 2>/dev/null; then
        echo "     powershell.exe -Command \"(New-Object Media.SoundPlayer '$AUDIO_DIR/task-complete.mp3').PlaySync()\""
    else
        echo "     mpg123 $AUDIO_DIR/task-complete.mp3"
    fi
    echo ""

    echo "For more help, see README.md"
else
    echo ""
    echo -e "${GREEN}${BOLD}🎉 Great! Audio playback is working correctly!${NC}"
    echo ""
    echo "Your echook are ready to use."
    echo "You'll hear these notifications when using Claude Code!"
fi

echo ""
echo -e "${CYAN}${BOLD}📚 Additional Options:${NC}"
echo "  • Run ${BOLD}./scripts/configure.sh${NC} to enable/disable hooks"
echo "  • See ${BOLD}README.md${NC} for configuration and customization"
echo ""