#!/bin/bash
# echook - Shared Configuration Library
# This library provides common functions for all hook scripts
# Version: 2.0.0

# =============================================================================
# CONFIGURATION PATHS
# =============================================================================

# Determine project directory (works from any hook script location)
get_project_dir() {
    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local hooks_parent="$(dirname "$script_dir")"

    # Strategy 1: Read from .project_path file (created during installation)
    # This is the most reliable method for installed hooks
    local project_path_file="$hooks_parent/.project_path"
    if [ -f "$project_path_file" ]; then
        local recorded_path=$(cat "$project_path_file" 2>/dev/null | tr -d '\n\r')
        if [ -d "$recorded_path" ] && [ -f "$recorded_path/config/user_preferences.json" ]; then
            echo "$recorded_path"
            return 0
        fi
    fi

    # Strategy 2: Check if we're in the project directory structure
    # (hooks are inside the project, e.g., echook/hooks/shared/)
    local candidate="$(dirname "$hooks_parent")"
    if [ -f "$candidate/config/user_preferences.json" ]; then
        echo "$candidate"
        return 0
    fi

    # Strategy 3: Search common installation locations
    for possible_dir in \
        "$HOME/echook" \
        "$HOME/projects/echook" \
        "$HOME/Documents/echook" \
        "$HOME/repos/echook" \
        "$HOME/git/echook" \
        "$HOME/src/echook"
    do
        if [ -d "$possible_dir" ] && [ -f "$possible_dir/config/user_preferences.json" ]; then
            echo "$possible_dir"
            return 0
        fi
    done

    # Fallback: return the calculated directory even if config doesn't exist
    echo "$candidate"
}

PROJECT_DIR="$(get_project_dir)"
AUDIO_DIR="$PROJECT_DIR/audio"
CONFIG_FILE="$PROJECT_DIR/config/user_preferences.json"

# Cross-platform temp directory
if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "mingw"* ]] || [[ "$OSTYPE" == "cygwin" ]]; then
    # Windows (Git Bash, MSYS2, Cygwin) - use Windows TEMP
    QUEUE_DIR="${TEMP:-${TMP:-/tmp}}/claude_audio_hooks_queue"
else
    # Unix (Linux, macOS, WSL)
    QUEUE_DIR="/tmp/claude_audio_hooks_queue"
fi
LOCK_FILE="$QUEUE_DIR/audio.lock"

# Debug mode (set CLAUDE_HOOKS_DEBUG=1 to enable)
CLAUDE_HOOKS_DEBUG="${CLAUDE_HOOKS_DEBUG:-}"

# Debug logging function
log_debug() {
    if [[ "$CLAUDE_HOOKS_DEBUG" == "1" ]] || [[ "$CLAUDE_HOOKS_DEBUG" == "true" ]]; then
        local log_dir="$QUEUE_DIR/logs"
        mkdir -p "$log_dir" 2>/dev/null
        local log_file="$log_dir/debug.log"
        local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
        echo "$timestamp | DEBUG | $1" >> "$log_file"
        # Keep only last 500 lines
        if [ -f "$log_file" ]; then
            tail -500 "$log_file" > "$log_file.tmp" 2>/dev/null && mv "$log_file.tmp" "$log_file" 2>/dev/null || true
        fi
    fi
}

# Error logging function (always logged)
log_error() {
    local log_dir="$QUEUE_DIR/logs"
    mkdir -p "$log_dir" 2>/dev/null
    local log_file="$log_dir/errors.log"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "$timestamp | ERROR | $1" >> "$log_file"
    # Keep only last 200 lines
    if [ -f "$log_file" ]; then
        tail -200 "$log_file" > "$log_file.tmp" 2>/dev/null && mv "$log_file.tmp" "$log_file" 2>/dev/null || true
    fi
}

# =============================================================================
# PYTHON COMMAND DETECTION (Windows Compatibility)
# =============================================================================

# Smart Python command detector for cross-platform compatibility
get_python_cmd() {
    # Return cached command if already detected
    if [ -n "$CLAUDE_HOOKS_PYTHON_CMD" ]; then
        echo "$CLAUDE_HOOKS_PYTHON_CMD"
        return 0
    fi

    # Try different Python commands in order of preference
    for cmd in python3 python py; do
        if command -v "$cmd" &> /dev/null; then
            local version=$("$cmd" --version 2>&1)
            if [[ "$version" == *"Python 3"* ]]; then
                export CLAUDE_HOOKS_PYTHON_CMD="$cmd"
                echo "$cmd"
                return 0
            fi
        fi
    done

    # Windows specific: try common installation paths
    for py_pattern in \
        "/c/Python3*/python.exe" \
        "/c/Program Files/Python3*/python.exe" \
        "/d/Python/Python3*/python.exe" \
        "/c/Users/*/AppData/Local/Programs/Python/Python3*/python.exe"
    do
        for actual_path in $py_pattern; do
            if [ -f "$actual_path" ]; then
                local version=$("$actual_path" --version 2>&1)
                if [[ "$version" == *"Python 3"* ]]; then
                    export CLAUDE_HOOKS_PYTHON_CMD="$actual_path"
                    echo "$actual_path"
                    return 0
                fi
            fi
        done
    done

    return 1
}

# =============================================================================
# PATH CONVERSION FOR PYTHON (Git Bash Compatibility)
# =============================================================================

# Convert Unix-style path to Windows path for Python on Git Bash/MSYS/MINGW
# This fixes the issue where Git Bash uses /d/path but Windows Python expects D:/path
convert_path_for_python() {
    local path="$1"

    # Check if we're in Git Bash/MSYS/MINGW environment (Windows)
    if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "mingw"* ]]; then
        # Convert /c/Users/... to C:/Users/...
        # Convert /d/github/... to D:/github/...
        # Pattern: /[drive_letter]/path -> [DRIVE_LETTER]:/path
        echo "$path" | sed 's|^/\([a-zA-Z]\)/|\U\1:/|'
        return 0
    fi

    # Not Git Bash, return path as-is
    echo "$path"
}

# =============================================================================
# CONFIGURATION FUNCTIONS
# =============================================================================

# Check if a hook is enabled in configuration
is_hook_enabled() {
    local hook_type="$1"

    # If no config file exists, use defaults (notification, stop, subagent_stop enabled)
    if [ ! -f "$CONFIG_FILE" ]; then
        case "$hook_type" in
            notification|stop|subagent_stop)
                return 0  # Enabled by default
                ;;
            *)
                return 1  # Disabled by default
                ;;
        esac
    fi

    # Read enabled status from config using Python
    local python_cmd=$(get_python_cmd)
    if [ -z "$python_cmd" ]; then
        # If Python not found, use defaults (critical hooks enabled)
        case "$hook_type" in
            notification|stop|subagent_stop)
                return 0  # Enabled by default
                ;;
            *)
                return 1  # Disabled by default
                ;;
        esac
    fi

    # Convert path for Python compatibility in Git Bash
    local config_file_for_python=$(convert_path_for_python "$CONFIG_FILE")

    local enabled=$("$python_cmd" <<EOF 2>/dev/null
import json
import sys
try:
    with open("$config_file_for_python", "r", encoding="utf-8") as f:
        config = json.load(f)
    enabled = config.get("enabled_hooks", {}).get("$hook_type", False)
    print("true" if enabled else "false")
except:
    print("false")
EOF
)

    [ "$enabled" = "true" ]
}

# Check if hooks are temporarily snoozed via marker file
is_snoozed() {
    local snooze_file="$QUEUE_DIR/snooze_until"
    [ -f "$snooze_file" ] || return 1
    local snooze_until current_time
    snooze_until=$(cat "$snooze_file" 2>/dev/null) || return 1
    current_time=$(date +%s)
    [ "$current_time" -lt "${snooze_until%%.*}" ] 2>/dev/null
}

# Get audio file path for a hook type
get_audio_file() {
    local hook_type="$1"
    local default_file="$2"

    # Try to read from config
    if [ -f "$CONFIG_FILE" ]; then
        local python_cmd=$(get_python_cmd)
        if [ -z "$python_cmd" ]; then
            # If Python not found, use default
            echo "$AUDIO_DIR/default/$default_file"
            return 0
        fi

        # Convert path for Python compatibility in Git Bash
        local config_file_for_python=$(convert_path_for_python "$CONFIG_FILE")

        local audio_path=$("$python_cmd" <<EOF 2>/dev/null
import json
try:
    with open("$config_file_for_python", "r", encoding="utf-8") as f:
        config = json.load(f)
    audio_file = config.get("audio_files", {}).get("$hook_type", "$default_file")
    print(audio_file)
except:
    print("$default_file")
EOF
)
        echo "$AUDIO_DIR/$audio_path"
    else
        # Fallback to default
        echo "$AUDIO_DIR/default/$default_file"
    fi
}

# =============================================================================
# AUDIO PLAYBACK FUNCTIONS
# =============================================================================

# Play audio file (platform-specific)
play_audio_internal() {
    local audio_file="$1"

    # Verify file exists
    if [ ! -f "$audio_file" ]; then
        return 1
    fi

    # Detect platform and play audio

    # Windows environments (WSL, Git Bash, MSYS, MINGW, Cygwin, or PowerShell)
    # Check for WSL first (has /proc/version with "microsoft")
    if grep -qi microsoft /proc/version 2>/dev/null; then
        # WSL environment - Windows MediaPlayer cannot access WSL UNC paths
        # Solution: Copy audio to Windows temp directory first
        local win_temp_dir="C:/Windows/Temp"
        local temp_filename="claude_audio_$(date +%s)_$$.mp3"
        local win_temp_file="$win_temp_dir/$temp_filename"
        local win_temp_unix=$(wslpath "$win_temp_file" 2>/dev/null)

        if [ -n "$win_temp_unix" ]; then
            # Copy audio file to Windows temp directory
            cp "$audio_file" "$win_temp_unix" 2>/dev/null

            # Play audio from Windows temp directory and clean up in background
            (
                powershell.exe -Command "
                    Add-Type -AssemblyName presentationCore
                    \$mediaPlayer = New-Object System.Windows.Media.MediaPlayer
                    \$mediaPlayer.Open('$win_temp_file')
                    \$mediaPlayer.Play()
                    Start-Sleep -Seconds 4
                    \$mediaPlayer.Stop()
                    \$mediaPlayer.Close()
                " 2>/dev/null
                # Clean up temp file after playback
                rm -f "$win_temp_unix" 2>/dev/null
            ) &
            return 0
        fi
    # Git Bash / MSYS / MINGW (Windows Git Bash)
    elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "mingw"* ]]; then
        # Convert Unix-style path to Windows path using cygpath (most reliable)
        local win_path
        if command -v cygpath &> /dev/null; then
            win_path=$(cygpath -w "$audio_file" 2>/dev/null)
        else
            # Fallback: manual conversion for /c/Users/... style paths
            win_path=$(echo "$audio_file" | sed 's|^/\([a-zA-Z]\)/|\U\1:/|')
        fi

        if [ -n "$win_path" ]; then
            # Create temporary PowerShell script to avoid escaping issues
            local temp_ps1="/tmp/claude_audio_play_$$.ps1"
            local temp_ps1_win
            if command -v cygpath &> /dev/null; then
                temp_ps1_win=$(cygpath -w "$temp_ps1" 2>/dev/null)
            else
                # Fallback for /tmp -> use TEMP environment variable
                temp_ps1_win="$TEMP/claude_audio_play_$$.ps1"
            fi

            # Create PowerShell script - use direct path instead of file:// URI
            cat > "$temp_ps1" << PSEOF
Add-Type -AssemblyName presentationCore
\$mediaPlayer = New-Object System.Windows.Media.MediaPlayer
\$mediaPlayer.Open("$win_path")
\$mediaPlayer.Play()
Start-Sleep -Seconds 3
\$mediaPlayer.Stop()
\$mediaPlayer.Close()
PSEOF

            # Execute PowerShell script and clean up in background
            (powershell.exe -ExecutionPolicy Bypass -File "$temp_ps1_win" 2>/dev/null; rm -f "$temp_ps1" 2>/dev/null) &
            return 0
        fi
    # Cygwin (another Windows compatibility layer)
    elif [[ "$OSTYPE" == "cygwin" ]]; then
        # Cygwin has its own path converter
        if command -v cygpath &> /dev/null; then
            local win_path=$(cygpath -w "$audio_file" 2>/dev/null)
            if [ -n "$win_path" ]; then
                powershell.exe -Command "
                    Add-Type -AssemblyName presentationCore
                    \$mediaPlayer = New-Object System.Windows.Media.MediaPlayer
                    \$mediaPlayer.Open('$win_path')
                    \$mediaPlayer.Play()
                    Start-Sleep -Seconds 3
                    \$mediaPlayer.Stop()
                    \$mediaPlayer.Close()
                " 2>/dev/null &
                return 0
            fi
        fi
    fi

    # macOS
    if [[ "$OSTYPE" == "darwin"* ]]; then
        if command -v afplay &> /dev/null; then
            afplay "$audio_file" 2>/dev/null &
            return 0
        fi
    fi

    # Linux (native) - not WSL
    if [[ "$OSTYPE" == "linux-gnu"* ]] && ! grep -qi microsoft /proc/version 2>/dev/null; then
        # Try mpg123 first (best for MP3)
        if command -v mpg123 &> /dev/null; then
            mpg123 -q "$audio_file" 2>/dev/null &
            return 0
        fi
        # Try aplay (ALSA)
        if command -v aplay &> /dev/null; then
            aplay "$audio_file" 2>/dev/null &
            return 0
        fi
        # Try ffplay (from ffmpeg)
        if command -v ffplay &> /dev/null; then
            ffplay -nodisp -autoexit -hide_banner -loglevel quiet "$audio_file" 2>/dev/null &
            return 0
        fi
        # Try paplay (PulseAudio)
        if command -v paplay &> /dev/null; then
            paplay "$audio_file" 2>/dev/null &
            return 0
        fi
    fi

    # No suitable player found
    return 1
}

# =============================================================================
# AUDIO QUEUE SYSTEM
# =============================================================================

# Initialize queue directory
init_queue() {
    mkdir -p "$QUEUE_DIR" 2>/dev/null
}

# Check if queue is enabled
is_queue_enabled() {
    if [ ! -f "$CONFIG_FILE" ]; then
        return 0  # Queue enabled by default
    fi

    local python_cmd=$(get_python_cmd)
    if [ -z "$python_cmd" ]; then
        # If Python not found, assume queue enabled
        return 0
    fi

    # Convert path for Python compatibility in Git Bash
    local config_file_for_python=$(convert_path_for_python "$CONFIG_FILE")

    local queue_enabled=$("$python_cmd" <<EOF 2>/dev/null
import json
try:
    with open("$config_file_for_python", "r", encoding="utf-8") as f:
        config = json.load(f)
    enabled = config.get("playback_settings", {}).get("queue_enabled", True)
    print("true" if enabled else "false")
except:
    print("true")
EOF
)

    [ "$queue_enabled" = "true" ]
}

# Get debounce milliseconds
get_debounce_ms() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "500"  # Default 500ms
        return
    fi

    local python_cmd=$(get_python_cmd)
    if [ -z "$python_cmd" ]; then
        # If Python not found, use default
        echo "500"
        return
    fi

    # Convert path for Python compatibility in Git Bash
    local config_file_for_python=$(convert_path_for_python "$CONFIG_FILE")

    "$python_cmd" <<EOF 2>/dev/null
import json
try:
    with open("$config_file_for_python", "r", encoding="utf-8") as f:
        config = json.load(f)
    debounce = config.get("playback_settings", {}).get("debounce_ms", 500)
    print(debounce)
except:
    print("500")
EOF
}

# Play audio with queue management (prevents overlapping sounds)
play_audio_queued() {
    local audio_file="$1"

    # Initialize queue directory
    init_queue

    # Check if queue is enabled
    if ! is_queue_enabled; then
        # Queue disabled, play directly
        play_audio_internal "$audio_file"
        return $?
    fi

    # Implement simple lock-based queue
    local max_wait=10  # Maximum wait time in seconds
    local waited=0

    while [ -f "$LOCK_FILE" ] && [ $waited -lt $max_wait ]; do
        sleep 0.1
        waited=$((waited + 1))
    done

    # If still locked after max wait, play anyway (avoid hanging)
    if [ $waited -ge $max_wait ]; then
        play_audio_internal "$audio_file" &
        return 0
    fi

    # Acquire lock
    touch "$LOCK_FILE"

    # Play audio
    play_audio_internal "$audio_file"
    local result=$?

    # Wait for playback to complete (estimated based on typical audio length)
    sleep 3

    # Release lock
    rm -f "$LOCK_FILE"

    return $result
}

# =============================================================================
# DEBOUNCE SYSTEM
# =============================================================================

# Check if we should debounce (skip) this notification
should_debounce() {
    local hook_type="$1"
    local debounce_file="$QUEUE_DIR/${hook_type}_last_played"
    local debounce_ms=$(get_debounce_ms)
    local debounce_sec=$(echo "scale=3; $debounce_ms / 1000" | bc 2>/dev/null || echo "0.5")

    # If debounce file exists and is recent, skip
    if [ -f "$debounce_file" ]; then
        local current_time=$(date +%s.%N)
        local last_time=$(cat "$debounce_file" 2>/dev/null || echo "0")
        local time_diff=$(echo "$current_time - $last_time" | bc 2>/dev/null || echo "999")

        if (( $(echo "$time_diff < $debounce_sec" | bc -l 2>/dev/null || echo "0") )); then
            return 0  # Should debounce (skip)
        fi
    fi

    # Update debounce timestamp
    date +%s.%N > "$debounce_file"
    return 1  # Should not debounce (play)
}

# =============================================================================
# MAIN HOOK EXECUTION FUNCTION
# =============================================================================

# Main function to get and play audio for a hook type
# This is the primary entry point called by individual hook scripts
get_and_play_audio() {
    local hook_type="$1"
    local default_audio_file="$2"

    # Log directory
    local log_dir="/tmp/claude_hooks_log"
    local log_file="$log_dir/hook_triggers.log"
    mkdir -p "$log_dir" 2>/dev/null

    # Check if hook is enabled
    if ! is_hook_enabled "$hook_type"; then
        exit 0  # Hook disabled, exit silently
    fi

    # Check if snoozed
    if is_snoozed; then
        log_debug "Hook $hook_type snoozed, skipping"
        exit 0
    fi

    # Check debounce (prevent rapid-fire notifications)
    if should_debounce "$hook_type"; then
        exit 0  # Debounced, exit silently
    fi

    # Get audio file path
    local audio_file=$(get_audio_file "$hook_type" "$default_audio_file")

    # Verify audio file exists
    if [ ! -f "$audio_file" ]; then
        # Try legacy location for backward compatibility
        local legacy_audio="$AUDIO_DIR/legacy/hey-chan-please-help-me.mp3"
        if [ -f "$legacy_audio" ]; then
            audio_file="$legacy_audio"
        else
            exit 0  # No audio file found, exit silently
        fi
    fi

    # Log the trigger
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "$timestamp | $hook_type | $(basename "$audio_file")" >> "$log_file"

    # Keep only last 200 entries
    if [ -f "$log_file" ]; then
        tail -n 200 "$log_file" > "$log_file.tmp" && mv "$log_file.tmp" "$log_file"
    fi

    # Play audio with queue management
    play_audio_queued "$audio_file"

    exit 0
}

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

# Test audio playback (for diagnostic purposes)
test_audio_playback() {
    local audio_file="$1"

    echo "Testing audio playback: $audio_file"

    if [ ! -f "$audio_file" ]; then
        echo "Error: Audio file not found"
        return 1
    fi

    echo "File size: $(du -h "$audio_file" | cut -f1)"
    echo "Playing audio..."

    play_audio_internal "$audio_file"
    local result=$?

    if [ $result -eq 0 ]; then
        echo "Audio playback initiated successfully"
    else
        echo "Audio playback failed"
    fi

    return $result
}

# Cleanup function (remove lock files, queue directory)
cleanup_hooks() {
    rm -f "$LOCK_FILE"
    rm -rf "$QUEUE_DIR"
    echo "echook: Cleanup complete"
}

# =============================================================================
# EXPORTS
# =============================================================================

# Export functions for use in hook scripts
export -f is_snoozed
export -f is_hook_enabled
export -f get_audio_file
export -f play_audio_internal
export -f play_audio_queued
export -f get_and_play_audio
export -f test_audio_playback
export -f cleanup_hooks

# Export variables
export PROJECT_DIR
export AUDIO_DIR
export CONFIG_FILE
