#!/bin/bash
# echook - Complete Installation Script
# Version: 4.2.0
# This script handles the complete installation process automatically
# Now with integrated environment detection, platform fixes, and validation
# Supports non-interactive mode for Claude Code and automation

set -eo pipefail  # Exit on errors (-e) and pipe failures (pipefail)

# =============================================================================
# CONFIGURATION
# =============================================================================

VERSION="4.2.0"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Cross-platform temp directory
if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "mingw"* ]] || [[ "$OSTYPE" == "cygwin" ]]; then
    # Windows (Git Bash, MSYS2, Cygwin) - use Windows TEMP
    LOG_DIR="${TEMP:-${TMP:-/tmp}}"
else
    # Unix (Linux, macOS, WSL)
    LOG_DIR="/tmp"
fi
LOG_FILE="$LOG_DIR/claude_hooks_install_$(date +%Y%m%d_%H%M%S).log"

# echook is AI-agent-first: this installer is always non-interactive. It never
# prompts and is safe to run unattended by an agent or CI. There is no human
# menu mode.
NON_INTERACTIVE=true

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
RESET='\033[0m'

# Counters
STEPS_TOTAL=11
STEPS_COMPLETED=0
ERRORS=0
WARNINGS=0

# =============================================================================
# LOGGING AND OUTPUT
# =============================================================================

# Dual output: terminal and log file
log() {
    echo -e "$1" | tee -a "$LOG_FILE"
}

log_silent() {
    echo -e "$1" >> "$LOG_FILE"
}

print_header() {
    log "${BLUE}${BOLD}$1${RESET}"
}

print_step() {
    ((STEPS_COMPLETED+=1))
    log "\n${MAGENTA}[$STEPS_COMPLETED/$STEPS_TOTAL]${RESET} ${BOLD}$1${RESET}"
}

print_success() {
    log "${GREEN}✓${RESET} $1"
}

print_warning() {
    log "${YELLOW}⚠${RESET} $1"
    ((WARNINGS+=1))
}

print_error() {
    log "${RED}✗${RESET} $1"
    ((ERRORS+=1))
}

print_info() {
    log "${CYAN}ℹ${RESET} $1"
}

# =============================================================================
# ERROR HANDLING
# =============================================================================

cleanup() {
    local exit_code=$?

    if [ $exit_code -ne 0 ]; then
        log ""
        print_error "Installation failed with exit code $exit_code"
        log ""
        log "Troubleshooting:"
        log "  1. Check the log file: $LOG_FILE"
        log "  2. Re-run the installer: bash scripts/install-complete.sh"
        log "  3. Report issue: https://github.com/ChanMeng666/echook/issues"
        log ""
    fi

    return $exit_code
}

trap cleanup EXIT

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

# Check if command exists
command_exists() {
    command -v "$1" &> /dev/null
}

# Get Python command (prefer py on Windows for reliability)
get_python_cmd() {
    local candidates

    # On Windows, prefer 'py' launcher which is more reliable
    if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "mingw"* ]] || [[ "$OSTYPE" == "cygwin" ]]; then
        candidates="py python3 python"
    else
        candidates="python3 python py"
    fi

    for cmd in $candidates; do
        if command_exists "$cmd"; then
            local version=$("$cmd" --version 2>&1)
            if [[ "$version" == *"Python 3"* ]]; then
                # Verify minimum version (3.6+)
                local major_minor=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
                if [[ -n "$major_minor" ]]; then
                    local major=$(echo "$major_minor" | cut -d. -f1)
                    local minor=$(echo "$major_minor" | cut -d. -f2)
                    if [[ "$major" -ge 3 ]] && [[ "$minor" -ge 6 ]]; then
                        echo "$cmd"
                        return 0
                    fi
                else
                    # Can't verify version, but it's Python 3, accept it
                    echo "$cmd"
                    return 0
                fi
            fi
        fi
    done
    return 1
}

# Check if running on Windows-like environment
is_windows_env() {
    [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "mingw"* ]] || [[ "$OSTYPE" == "cygwin" ]]
}

# Convert path to Windows format if needed
to_windows_path() {
    local path="$1"
    if is_windows_env; then
        # Try cygpath first (more reliable)
        if command_exists cygpath; then
            cygpath -m "$path" 2>/dev/null || echo "$path"
        else
            # Manual conversion: /d/path -> D:/path
            if [[ "$path" =~ ^/([a-zA-Z])/ ]]; then
                echo "${BASH_REMATCH[1]^}:${path:2}"
            else
                echo "$path"
            fi
        fi
    else
        echo "$path"
    fi
}

# Create backup of file
backup_file() {
    local file="$1"
    if [ -f "$file" ]; then
        cp "$file" "${file}.backup_$(date +%Y%m%d_%H%M%S)"
        log_silent "  Backed up: $file"
    fi
}

# =============================================================================
# INSTALLATION STEPS
# =============================================================================

# Step 1: Display welcome message
step_welcome() {
    print_step "Welcome"

    log ""
    log "================================================"
    print_header "  echook v$VERSION"
    print_header "  Complete Installation"
    log "================================================"
    log ""
    print_info "Installation log: $LOG_FILE"
    log ""
}

# Step 2: Check prerequisites
step_check_prerequisites() {
    print_step "Checking Prerequisites"

    local missing=0

    # Check Claude Code
    if command_exists claude; then
        CLAUDE_VERSION=$(claude --version 2>&1)
        print_success "Claude Code: $CLAUDE_VERSION"
    else
        print_error "Claude Code not found"
        print_info "  Install from: https://docs.anthropic.com/claude/docs/claude-code"
        ((missing+=1))
    fi

    # Check Git
    if command_exists git; then
        GIT_VERSION=$(git --version)
        print_success "Git: $GIT_VERSION"
    else
        print_error "Git not found"
        print_info "  Linux/WSL: sudo apt-get install git"
        print_info "  Windows: https://gitforwindows.org/"
        ((missing+=1))
    fi

    # Check Python (optional)
    PYTHON_CMD=$(get_python_cmd)
    if [ -n "$PYTHON_CMD" ]; then
        PYTHON_VERSION=$($PYTHON_CMD --version 2>&1)
        print_success "Python: $PYTHON_VERSION"
        export PYTHON_CMD
    else
        print_warning "Python 3 not found (optional)"
        print_info "  Some features will use defaults"
        print_info "  Linux/WSL: sudo apt-get install python3"
        print_info "  Windows: https://www.python.org/downloads/"
    fi

    # Check Bash version
    if [ -n "$BASH_VERSION" ]; then
        print_success "Bash: $BASH_VERSION"
    fi

    if [ $missing -gt 0 ]; then
        log ""
        print_error "Missing $missing required dependencies"
        print_info "Please install missing dependencies and try again"
        exit 1
    fi
}

# Step 3: Detect environment
step_detect_environment() {
    print_step "Detecting Environment"

    # Source path utilities for environment detection
    if [ -f "$PROJECT_DIR/hooks/shared/path_utils.sh" ]; then
        source "$PROJECT_DIR/hooks/shared/path_utils.sh"
        ENV_TYPE=$(detect_environment)
        print_success "Environment: $ENV_TYPE"

        case "$ENV_TYPE" in
            WSL)
                print_info "Windows Subsystem for Linux detected"
                print_info "Audio will use PowerShell + Windows Media Player"
                ;;
            GIT_BASH)
                print_info "Git Bash detected"
                print_info "Audio will use PowerShell + Windows Media Player"
                ;;
            CYGWIN)
                print_info "Cygwin detected"
                print_info "Audio will use PowerShell + Windows Media Player"
                ;;
            MACOS)
                print_info "macOS detected"
                print_info "Audio will use afplay"
                ;;
            LINUX)
                print_info "Native Linux detected"
                print_info "Audio will use mpg123, aplay, or ffplay"
                ;;
            *)
                print_warning "Unknown environment: $OSTYPE"
                ;;
        esac
    else
        print_warning "Path utilities not found, using basic detection"
        ENV_TYPE="UNKNOWN"
    fi

    export ENV_TYPE
}

# Step 4: Validate project structure
step_validate_project() {
    print_step "Validating Project Structure"

    local valid=0

    # Check required directories
    if [ -d "$PROJECT_DIR/hooks" ]; then
        print_success "hooks/ directory found"
        ((valid+=1))
    else
        print_error "hooks/ directory not found"
    fi

    if [ -d "$PROJECT_DIR/audio/default" ]; then
        local audio_count=$(ls -1 "$PROJECT_DIR/audio/default"/*.mp3 2>/dev/null | wc -l)
        print_success "audio/ directory found with $audio_count MP3 files"
        ((valid+=1))
    else
        print_error "audio/ directory not found"
    fi

    if [ -d "$PROJECT_DIR/config" ]; then
        print_success "config/ directory found"
        ((valid+=1))
    else
        print_error "config/ directory not found"
    fi

    if [ -f "$PROJECT_DIR/hooks/hook_runner.py" ]; then
        print_success "hook_runner.py found"
        ((valid+=1))
    else
        print_error "hook_runner.py not found"
    fi

    if [ $valid -lt 4 ]; then
        log ""
        print_error "Project structure incomplete"
        print_info "Make sure you're in the project root directory"
        print_info "Expected structure:"
        print_info "  echook/"
        print_info "    ├── hooks/"
        print_info "    ├── audio/default/"
        print_info "    ├── config/"
        print_info "    └── scripts/"
        exit 1
    fi
}

# Step 5: Install hook scripts
step_install_hooks() {
    print_step "Installing Hook Scripts"

    # Create hooks directory
    mkdir -p ~/.claude/hooks
    print_success "Created hooks directory"

    # Record project path (use Windows format on Windows for Python compatibility)
    local project_path_to_save
    if is_windows_env; then
        project_path_to_save=$(to_windows_path "$PROJECT_DIR")
        print_info "Converting path for Windows: $project_path_to_save"
    else
        project_path_to_save="$PROJECT_DIR"
    fi
    echo "$project_path_to_save" > ~/.claude/hooks/.project_path
    print_success "Recorded project path: $project_path_to_save"


    # Install the Python hook runner — this is the registered runtime for the
    # full tier (settings.json invokes hook_runner.py). Its sibling modules must
    # ship alongside it (hook_runner.py imports both).
    if [ -f "$PROJECT_DIR/hooks/hook_runner.py" ]; then
        cp "$PROJECT_DIR/hooks/hook_runner.py" ~/.claude/hooks/
        cp "$PROJECT_DIR/hooks/user_preferences.py" ~/.claude/hooks/ 2>/dev/null || true
        cp "$PROJECT_DIR/hooks/invoker.py" ~/.claude/hooks/ 2>/dev/null || true
        print_success "Python hook runner installed"
    fi
}

# Step 6: Configure Claude settings
step_configure_settings() {
    print_step "Configuring Claude Settings"

    # Check if Python is available for JSON manipulation
    if [ -z "$PYTHON_CMD" ]; then
        print_warning "Python not available, skipping automatic settings configuration"
        print_info "Please manually add hooks to ~/.claude/settings.json"
        print_info "See AI_INSTALL.md for manual configuration steps"
        return 0
    fi

    # Backup existing settings
    backup_file ~/.claude/settings.json

    # Update settings.json
    print_info "Updating settings.json..."
    PROJECT_DIR="$PROJECT_DIR" $PYTHON_CMD << 'EOF'
import json
import os
import sys
import platform

settings_file = os.path.expanduser('~/.claude/settings.json')
home_dir = os.path.expanduser('~')

try:
    # Load default preferences to determine which hooks to register
    project_dir = os.environ.get('PROJECT_DIR', '.')
    prefs_file = os.path.join(project_dir, 'config', 'default_preferences.json')
    if os.path.exists(prefs_file):
        with open(prefs_file, 'r', encoding='utf-8') as f:
            prefs = json.load(f)
        enabled = {k for k, v in prefs.get('enabled_hooks', {}).items()
                   if not k.startswith('_') and v is True}
    else:
        enabled = {'notification', 'stop', 'subagent_stop', 'permission_request'}  # hardcoded fallback

    # Read existing settings or create new
    if os.path.exists(settings_file):
        with open(settings_file, 'r', encoding='utf-8') as f:
            settings = json.load(f)
    else:
        settings = {}

    # Add hooks configuration for Claude Code
    if 'hooks' not in settings:
        settings['hooks'] = {}

    # Determine if we're on Windows
    is_windows = platform.system() == 'Windows' or os.name == 'nt'

    # All hook types: Claude Code name -> hook_runner.py argument
    all_hook_types = {
        'PreToolUse': 'pretooluse',
        'PostToolUse': 'posttooluse',
        'Notification': 'notification',
        'Stop': 'stop',
        'UserPromptSubmit': 'userpromptsubmit',
        'SubagentStop': 'subagent_stop',
        'PreCompact': 'precompact',
        'SessionStart': 'session_start',
        'SessionEnd': 'session_end',
        'PermissionRequest': 'permission_request',
        'PostToolUseFailure': 'posttoolusefailure',
        'SubagentStart': 'subagent_start',
        'TeammateIdle': 'teammate_idle',
        'TaskCompleted': 'task_completed',
        'StopFailure': 'stop_failure',
        'PostCompact': 'postcompact',
        'ConfigChange': 'config_change',
        'InstructionsLoaded': 'instructions_loaded',
        'Elicitation': 'elicitation',
        'ElicitationResult': 'elicitation_result'
    }

    hooks_with_matcher = ['PreToolUse', 'PostToolUse', 'PostToolUseFailure', 'PermissionRequest', 'SubagentStart', 'SubagentStop', 'Notification', 'ConfigChange', 'InstructionsLoaded', 'PreCompact', 'PostCompact', 'SessionStart', 'SessionEnd', 'StopFailure', 'Elicitation', 'ElicitationResult']

    # Smart matchers: filter high-noise hooks to only fire for relevant tools
    smart_matchers = {
        'PreToolUse': 'Bash',
        'PostToolUseFailure': 'Bash|Write|Edit',
    }

    registered = 0

    if is_windows:
        # Windows: Use Python hook_runner.py for reliable execution
        hooks_dir = home_dir.replace(os.sep, '/') + '/.claude/hooks'
        hook_runner = f'{hooks_dir}/hook_runner.py'

        for hook_name, hook_type in all_hook_types.items():
            if hook_type not in enabled:
                continue
            # Use 'py' command which is reliable on Windows
            command = f'py "{hook_runner}" {hook_type} || true'

            entry = {
                'hooks': [{'type': 'command', 'command': command, 'timeout': 10, 'async': True}]
            }
            if hook_name in hooks_with_matcher:
                entry['matcher'] = smart_matchers.get(hook_name, '')
            settings['hooks'][hook_name] = [entry]
            registered += 1

        env_note = "(Windows - Python hooks, async)"
    else:
        # Unix: Use absolute path and defensive wrapping so a missing
        # hook_runner.py returns success (0) instead of blocking the user
        hook_runner = f'{home_dir}/.claude/hooks/hook_runner.py'
        python_cmd = "python3"

        for hook_name, hook_type in all_hook_types.items():
            if hook_type not in enabled:
                continue
            command = f'test -f {hook_runner} && {python_cmd} {hook_runner} {hook_type} || true'
            entry = {
                'hooks': [{'type': 'command', 'command': command, 'timeout': 10, 'async': True}]
            }
            if hook_name in hooks_with_matcher:
                entry['matcher'] = smart_matchers.get(hook_name, '')
            settings['hooks'][hook_name] = [entry]
            registered += 1

        env_note = "(Unix - Python hooks, async)"

    # Save settings
    with open(settings_file, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)

    print(f"Configured {registered} hooks in settings.json {env_note}")
    sys.exit(0)

except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF

    if [ $? -eq 0 ]; then
        print_success "Settings configured successfully"
    else
        print_error "Failed to configure settings"
        print_info "You may need to configure manually"
        ((ERRORS+=1))
    fi
}

# Step 7: Configure permissions
step_configure_permissions() {
    print_step "Configuring Permissions"

    # Hook commands in settings.json are executed directly by Claude Code's
    # hook system, not via the Bash tool. Therefore, allowedTools entries for
    # hook scripts serve no purpose and would only clutter settings.local.json.
    print_success "No additional permissions needed (hooks run via Claude Code's hook system)"
}

# Step 8: Initialize configuration
step_initialize_config() {
    print_step "Initializing Configuration"

    # Copy default configuration if user preferences don't exist
    if [ ! -f "$PROJECT_DIR/config/user_preferences.json" ]; then
        if [ -f "$PROJECT_DIR/config/default_preferences.json" ]; then
            cp "$PROJECT_DIR/config/default_preferences.json" "$PROJECT_DIR/config/user_preferences.json"
            print_success "Created user_preferences.json from defaults"
        else
            print_warning "default_preferences.json not found"
        fi
    else
        print_success "user_preferences.json already exists"
    fi

    # Validate configuration
    if [ -n "$PYTHON_CMD" ] && [ -f "$PROJECT_DIR/config/user_preferences.json" ]; then
        $PYTHON_CMD << 'EOF'
import json
import sys

try:
    with open('config/user_preferences.json', 'r', encoding='utf-8') as f:
        config = json.load(f)

    # Filter out comment keys and sum only boolean values
    enabled_hooks = config.get('enabled_hooks', {})
    enabled_count = sum(1 for k, v in enabled_hooks.items() if not k.startswith('_') and v is True)
    print(f"Configuration valid: {enabled_count} hooks enabled by default")
    sys.exit(0)

except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF

        if [ $? -eq 0 ]; then
            print_success "Configuration validated"
        else
            print_warning "Configuration validation failed"
        fi
    fi
}

# Step 9: Run tests
step_run_tests() {
    print_step "Running Installation Tests"

    local tests_passed=0
    local tests_failed=0

    # Test 1: Check the Python hook runner is installed (the full-tier runtime)
    print_info "Test 1: Checking hook runner..."
    if [ -f ~/.claude/hooks/hook_runner.py ]; then
        print_success "Hook runner: hook_runner.py installed"
        ((tests_passed+=1))
    else
        print_error "Hook runner not found at ~/.claude/hooks/hook_runner.py"
        ((tests_failed+=1))
    fi

    # Test 2: Check settings
    print_info "Test 2: Checking settings configuration..."
    if [ -f ~/.claude/settings.json ]; then
        if grep -q "hook_runner.py" ~/.claude/settings.json 2>/dev/null; then
            print_success "Settings: Hooks configured"
            ((tests_passed+=1))
        else
            print_warning "Settings: Hooks may not be configured"
            ((tests_failed+=1))
        fi
    else
        print_warning "Settings file not found"
        ((tests_failed+=1))
    fi

    # Test 3: Check permissions
    print_info "Test 3: Checking permissions..."
    if [ -f ~/.claude/settings.local.json ]; then
        if grep -q "allowedTools" ~/.claude/settings.local.json 2>/dev/null; then
            print_success "Permissions: allowedTools configured"
            ((tests_passed+=1))
        else
            print_warning "Permissions: allowedTools not found"
            ((tests_failed+=1))
        fi
    else
        print_warning "settings.local.json not found"
        ((tests_failed+=1))
    fi

    # Test 4: Check audio files
    print_info "Test 4: Checking audio files..."
    if [ -f ~/.claude/hooks/.project_path ]; then
        PROJECT_PATH=$(cat ~/.claude/hooks/.project_path)
        if [ -d "$PROJECT_PATH/audio/default" ]; then
            local audio_count=$(ls -1 "$PROJECT_PATH/audio/default"/*.mp3 2>/dev/null | wc -l)
            if [ $audio_count -ge 14 ]; then
                print_success "Audio files: $audio_count files found"
                ((tests_passed+=1))
            else
                print_warning "Audio files: Only $audio_count/14 files found"
                ((tests_failed+=1))
            fi
        else
            print_error "Audio directory not found"
            ((tests_failed+=1))
        fi
    else
        print_error "Project path not recorded"
        ((tests_failed+=1))
    fi

    # Test 5: Test path utilities (if available)
    if [ -f "$PROJECT_DIR/scripts/.internal-tests/test-path-utils.sh" ]; then
        print_info "Test 5: Testing path utilities..."
        if bash "$PROJECT_DIR/scripts/.internal-tests/test-path-utils.sh" >> "$LOG_FILE" 2>&1; then
            print_success "Path utilities: All tests passed"
            ((tests_passed+=1))
        else
            print_warning "Path utilities: Some tests failed (see log)"
            ((tests_failed+=1))
        fi
    fi

    log ""
    print_info "Tests: $tests_passed passed, $tests_failed failed"

    if [ $tests_failed -gt 2 ]; then
        print_warning "Multiple tests failed. Installation may be incomplete."
        print_info "Please re-run the installer or check the log file for details."
    fi
}

# Step 10: Audio testing pointer (non-interactive — verification is the agent's job)
step_offer_audio_test() {
    print_step "Audio Testing"
    log ""
    print_info "Verify playback with the CLI: audio-hooks test all"
    return 0
}

# Step 11: Display next steps
step_next_steps() {
    print_step "Installation Complete!"

    log ""
    log "================================================"
    print_header "  Installation Summary"
    log "================================================"
    log ""

    # Show statistics
    print_info "Steps completed: $STEPS_COMPLETED/$STEPS_TOTAL"
    if [ $ERRORS -eq 0 ]; then
        print_success "Errors: 0"
    else
        print_warning "Errors: $ERRORS"
    fi
    if [ $WARNINGS -eq 0 ]; then
        print_success "Warnings: 0"
    else
        print_warning "Warnings: $WARNINGS"
    fi

    log ""

    if [ $ERRORS -eq 0 ]; then
        print_success "Installation completed successfully!"
        log ""
        log "Next Steps:"
        log ""
        log "  1. ${BOLD}Restart Claude Code${RESET} (settings require restart)"
        log ""
        log "  2. Verify and operate everything via the CLI (machine-readable JSON):"
        log "     ${CYAN}audio-hooks status${RESET}      # current config snapshot"
        log "     ${CYAN}audio-hooks diagnose${RESET}    # system check"
        log "     ${CYAN}audio-hooks test all${RESET}    # play every enabled sound"
        log "     ${CYAN}audio-hooks manifest${RESET}    # full capability list for agents"
        log ""
    else
        print_warning "Installation completed with errors"
        log ""
        log "Troubleshooting (all machine-readable JSON):"
        log ""
        log "  ${CYAN}audio-hooks diagnose${RESET}    # structured system check"
        log "  ${CYAN}audio-hooks logs tail${RESET}   # recent hook events"
        log "  Install log:     ${CYAN}$LOG_FILE${RESET}"
        log ""
    fi

    # Show useful commands — all via the AI-agent-first CLI
    log "Useful Commands:"
    log ""
    log "  Status:          ${CYAN}audio-hooks status${RESET}"
    log "  Configure:       ${CYAN}audio-hooks set <dotted.key> <value>${RESET}"
    log "  Test audio:      ${CYAN}audio-hooks test all${RESET}"
    log "  Uninstall:       ${CYAN}audio-hooks uninstall${RESET}"
    log ""

    log "Documentation:"
    log ""
    log "  README:          ${CYAN}README.md${RESET}"
    log "  Changelog:       ${CYAN}CHANGELOG.md${RESET}"
    log "  License:         ${CYAN}LICENSE${RESET}"
    log ""

    log "Installation log saved to:"
    log "  ${CYAN}$LOG_FILE${RESET}"
    log ""
}

# =============================================================================
# MAIN EXECUTION
# =============================================================================

main() {
    # Create log file
    touch "$LOG_FILE"

    # Execute installation steps
    step_welcome
    step_check_prerequisites
    step_detect_environment
    step_validate_project
    step_install_hooks
    step_configure_settings
    step_configure_permissions
    step_initialize_config
    step_run_tests
    step_offer_audio_test
    step_next_steps

    # Return success if no critical errors
    if [ $ERRORS -eq 0 ]; then
        return 0
    else
        return 1
    fi
}

# =============================================================================
# ARGUMENT PROCESSING
# =============================================================================

# Parse command line arguments
parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --yes|-y|--non-interactive)
                NON_INTERACTIVE=true
                shift
                ;;
            --help|-h)
                cat << EOF
${BOLD}echook - Installation Script${RESET}

${CYAN}USAGE:${RESET}
  $0 [OPTIONS]

${CYAN}OPTIONS:${RESET}
  ${BOLD}--yes, -y, --non-interactive${RESET}
    Run in non-interactive mode (skip all prompts)
    Perfect for Claude Code and automation scripts

  ${BOLD}--help, -h${RESET}
    Show this help message

${CYAN}INTERACTIVE MODE${RESET} (default):
  Will prompt for audio testing after installation

${CYAN}NON-INTERACTIVE MODE${RESET} (--yes):
  Skips all prompts, auto-accepts defaults
  Installation completes without user input

${CYAN}EXAMPLES:${RESET}
  # Interactive installation (prompts for audio test)
  bash scripts/install-complete.sh

  # Non-interactive installation (for Claude Code/automation)
  bash scripts/install-complete.sh --yes

  # Short form
  bash scripts/install-complete.sh -y

${YELLOW}Note:${RESET} Installation log saved to: /tmp/claude_hooks_install_*.log
EOF
                exit 0
                ;;
            *)
                echo -e "${RED}Error: Unknown option '$1'${RESET}" >&2
                echo "Run '$0 --help' for usage information" >&2
                exit 1
                ;;
        esac
    done
}

# Parse arguments before running main
parse_args "$@"

# Run main function
main
