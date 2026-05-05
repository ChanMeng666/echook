#!/bin/bash
# echook - Path Utilities
# Cross-platform path conversion and handling
# Version: 2.0.0

# =============================================================================
# PATH CONVERSION UTILITIES
# =============================================================================

# Detect the current environment type
detect_environment() {
    # Return cached value if available
    if [ -n "$CLAUDE_HOOKS_ENV_TYPE" ]; then
        echo "$CLAUDE_HOOKS_ENV_TYPE"
        return 0
    fi

    local env_type=""

    # Check for WSL (Windows Subsystem for Linux)
    if grep -qi microsoft /proc/version 2>/dev/null; then
        env_type="WSL"
    # Check for Git Bash / MSYS / MINGW
    elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "mingw"* ]]; then
        env_type="GIT_BASH"
    # Check for Cygwin
    elif [[ "$OSTYPE" == "cygwin" ]]; then
        env_type="CYGWIN"
    # Check for macOS
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        env_type="MACOS"
    # Check for native Linux
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        env_type="LINUX"
    else
        env_type="UNKNOWN"
    fi

    export CLAUDE_HOOKS_ENV_TYPE="$env_type"
    echo "$env_type"
}

# Convert Unix path to Windows path
# Usage: to_windows_path "/c/Users/name/file.txt"
# Output: "C:\Users\name\file.txt" or "C:/Users/name/file.txt"
to_windows_path() {
    local input_path="$1"
    local format="${2:-forward}"  # 'forward' (C:/...) or 'backslash' (C:\...)

    # Empty path
    if [ -z "$input_path" ]; then
        return 1
    fi

    # Already a Windows path (C:\ or D:\ format)
    if [[ "$input_path" =~ ^[A-Za-z]:[/\\] ]]; then
        if [ "$format" = "backslash" ]; then
            echo "$input_path" | tr '/' '\\'
        else
            echo "$input_path" | tr '\\' '/'
        fi
        return 0
    fi

    local converted_path=""
    local env_type=$(detect_environment)

    case "$env_type" in
        WSL)
            # WSL: Convert /c/ format or /mnt/c/ format to C:/ format
            # First normalize to /mnt/c/ format if needed
            local wsl_path="$input_path"

            # Convert /c/ to /mnt/c/ if needed
            if [[ "$wsl_path" =~ ^/([a-zA-Z])/ ]]; then
                local drive="${BASH_REMATCH[1]}"
                local rest="${wsl_path:3}"
                wsl_path="/mnt/${drive}/${rest}"
            fi

            # Now convert /mnt/c/ to C:/ using wslpath
            if command -v wslpath &> /dev/null && [ -e "$wsl_path" ]; then
                if [ "$format" = "backslash" ]; then
                    converted_path=$(wslpath -w "$wsl_path" 2>/dev/null)
                else
                    converted_path=$(wslpath -w "$wsl_path" 2>/dev/null | tr '\\' '/')
                fi

                if [ -n "$converted_path" ] && [[ "$converted_path" =~ ^[A-Za-z]: ]]; then
                    echo "$converted_path"
                    return 0
                fi
            fi

            # Manual conversion as fallback
            if [[ "$input_path" =~ ^/mnt/([a-zA-Z])/ ]] || [[ "$input_path" =~ ^/([a-zA-Z])/ ]]; then
                local drive="${BASH_REMATCH[1]}"
                local rest
                if [[ "$input_path" =~ ^/mnt/ ]]; then
                    rest="${input_path:7}"  # Remove /mnt/X/
                else
                    rest="${input_path:3}"  # Remove /X/
                fi

                # Convert drive letter to uppercase (bash 3.2 compatible)
                local drive_upper=$(echo "$drive" | tr '[:lower:]' '[:upper:]')
                if [ "$format" = "backslash" ]; then
                    echo "${drive_upper}:\\${rest//\//\\}"
                else
                    echo "${drive_upper}:/${rest}"
                fi
                return 0
            fi
            ;;

        GIT_BASH|MSYS|MINGW)
            # Git Bash path conversion: /c/Users/... -> C:/Users/...
            if [[ "$input_path" =~ ^/([a-zA-Z])/ ]]; then
                local drive="${BASH_REMATCH[1]}"
                local rest="${input_path:3}"

                # Convert drive letter to uppercase (bash 3.2 compatible)
                local drive_upper=$(echo "$drive" | tr '[:lower:]' '[:upper:]')
                if [ "$format" = "backslash" ]; then
                    converted_path="${drive_upper}:\\${rest//\//\\}"
                else
                    converted_path="${drive_upper}:/${rest}"
                fi

                echo "$converted_path"
                return 0
            fi
            ;;

        CYGWIN)
            # Cygwin has cygpath utility
            if command -v cygpath &> /dev/null; then
                if [ "$format" = "backslash" ]; then
                    converted_path=$(cygpath -w "$input_path" 2>/dev/null)
                else
                    converted_path=$(cygpath -w "$input_path" 2>/dev/null | tr '\\' '/')
                fi

                if [ -n "$converted_path" ]; then
                    echo "$converted_path"
                    return 0
                fi
            fi
            ;;

        MACOS|LINUX)
            # Native Unix systems - return as-is
            echo "$input_path"
            return 0
            ;;
    esac

    # Fallback: try manual conversion for /c/... format
    if [[ "$input_path" =~ ^/([a-zA-Z])/ ]]; then
        local drive="${BASH_REMATCH[1]}"
        local rest="${input_path:3}"

        # Convert drive letter to uppercase (bash 3.2 compatible)
        local drive_upper=$(echo "$drive" | tr '[:lower:]' '[:upper:]')
        if [ "$format" = "backslash" ]; then
            echo "${drive_upper}:\\${rest//\//\\}"
        else
            echo "${drive_upper}:/${rest}"
        fi
        return 0
    fi

    # Last resort: return as-is
    echo "$input_path"
    return 1
}

# Convert Windows path to Unix path
# Usage: to_unix_path "C:\Users\name\file.txt"
# Output: "/c/Users/name/file.txt"
to_unix_path() {
    local input_path="$1"

    # Empty path
    if [ -z "$input_path" ]; then
        return 1
    fi

    # Already a Unix path
    if [[ ! "$input_path" =~ ^[A-Za-z]:[/\\] ]]; then
        echo "$input_path"
        return 0
    fi

    local converted_path=""
    local env_type=$(detect_environment)

    case "$env_type" in
        WSL)
            # WSL: Convert C:/ format to /mnt/c/ format
            if command -v wslpath &> /dev/null; then
                converted_path=$(wslpath -u "$input_path" 2>/dev/null)
                if [ -n "$converted_path" ]; then
                    echo "$converted_path"
                    return 0
                fi
            fi

            # Manual conversion as fallback
            if [[ "$input_path" =~ ^([A-Za-z]):[\\/](.*)$ ]]; then
                local drive="${BASH_REMATCH[1]}"
                local rest="${BASH_REMATCH[2]}"
                rest="${rest//\\//}"
                # Convert drive letter to lowercase (bash 3.2 compatible)
                local drive_lower=$(echo "$drive" | tr '[:upper:]' '[:lower:]')
                echo "/mnt/${drive_lower}/${rest}"
                return 0
            fi
            ;;

        GIT_BASH|MSYS|MINGW)
            # Convert C:\Users\... to /c/Users/...
            if [[ "$input_path" =~ ^([A-Za-z]):[\\/](.*)$ ]]; then
                local drive="${BASH_REMATCH[1]}"
                local rest="${BASH_REMATCH[2]}"
                # Replace backslashes with forward slashes
                rest="${rest//\\//}"
                # Convert drive letter to lowercase (bash 3.2 compatible)
                local drive_lower=$(echo "$drive" | tr '[:upper:]' '[:lower:]')
                converted_path="/${drive_lower}/${rest}"
                echo "$converted_path"
                return 0
            fi
            ;;

        CYGWIN)
            # Cygwin has cygpath utility
            if command -v cygpath &> /dev/null; then
                converted_path=$(cygpath -u "$input_path" 2>/dev/null)
                if [ -n "$converted_path" ]; then
                    echo "$converted_path"
                    return 0
                fi
            fi
            ;;

        MACOS|LINUX)
            # Native Unix - shouldn't have Windows paths
            echo "$input_path"
            return 1
            ;;
    esac

    # Fallback manual conversion
    if [[ "$input_path" =~ ^([A-Za-z]):[\\/](.*)$ ]]; then
        local drive="${BASH_REMATCH[1]}"
        local rest="${BASH_REMATCH[2]}"
        rest="${rest//\\//}"
        # Convert drive letter to lowercase (bash 3.2 compatible)
        local drive_lower=$(echo "$drive" | tr '[:upper:]' '[:lower:]')
        echo "/${drive_lower}/${rest}"
        return 0
    fi

    # Last resort: return as-is
    echo "$input_path"
    return 1
}

# Normalize path separators (always use forward slashes)
# Usage: normalize_path "/c/Users\name\file.txt"
# Output: "/c/Users/name/file.txt"
normalize_path() {
    local input_path="$1"
    echo "$input_path" | tr '\\' '/'
}

# Check if a path exists (works with both Unix and Windows paths)
# Usage: path_exists "/c/Users/name/file.txt"
# Returns: 0 if exists, 1 if not
path_exists() {
    local input_path="$1"

    # Try as-is first
    if [ -e "$input_path" ]; then
        return 0
    fi

    # Try converting to Unix path
    local unix_path=$(to_unix_path "$input_path")
    if [ -e "$unix_path" ]; then
        return 0
    fi

    return 1
}

# Get absolute path (cross-platform)
# Usage: get_absolute_path "./relative/path"
# Output: "/full/absolute/path"
get_absolute_path() {
    local input_path="$1"

    # If path doesn't exist, return as-is
    if [ ! -e "$input_path" ]; then
        # Try to resolve relative to current directory
        if [[ ! "$input_path" =~ ^/ ]]; then
            input_path="$(pwd)/$input_path"
        fi
        echo "$input_path"
        return 0
    fi

    # Use realpath if available
    if command -v realpath &> /dev/null; then
        realpath "$input_path" 2>/dev/null && return 0
    fi

    # Use readlink if available
    if command -v readlink &> /dev/null; then
        readlink -f "$input_path" 2>/dev/null && return 0
    fi

    # Fallback to cd and pwd
    if [ -d "$input_path" ]; then
        (cd "$input_path" && pwd)
    else
        local dir=$(dirname "$input_path")
        local file=$(basename "$input_path")
        echo "$(cd "$dir" && pwd)/$file"
    fi
}

# Smart path converter - automatically detect format and convert as needed
# Usage: smart_path_convert "/c/Users/name/file.txt" "audio_playback"
# Context: "audio_playback" (needs Windows path), "file_operation" (needs Unix path)
smart_path_convert() {
    local input_path="$1"
    local context="${2:-auto}"

    local env_type=$(detect_environment)

    case "$context" in
        audio_playback)
            # For audio playback on Windows, we need Windows paths
            case "$env_type" in
                WSL|GIT_BASH|CYGWIN)
                    to_windows_path "$input_path" "forward"
                    ;;
                *)
                    echo "$input_path"
                    ;;
            esac
            ;;

        file_operation)
            # For file operations, we need Unix paths in Unix-like environments
            case "$env_type" in
                WSL|GIT_BASH|CYGWIN)
                    to_unix_path "$input_path"
                    ;;
                *)
                    echo "$input_path"
                    ;;
            esac
            ;;

        powershell)
            # For PowerShell, we need Windows paths with forward slashes
            case "$env_type" in
                WSL|GIT_BASH|CYGWIN)
                    to_windows_path "$input_path" "forward"
                    ;;
                *)
                    echo "$input_path"
                    ;;
            esac
            ;;

        auto|*)
            # Auto-detect based on environment
            # Windows environments get Windows paths for external tools
            case "$env_type" in
                WSL|GIT_BASH|CYGWIN)
                    # If it's already a Windows path, keep it
                    if [[ "$input_path" =~ ^[A-Za-z]:[/\\] ]]; then
                        to_windows_path "$input_path" "forward"
                    else
                        # Convert Unix to Windows for external tools
                        to_windows_path "$input_path" "forward"
                    fi
                    ;;
                *)
                    # Unix systems use Unix paths
                    echo "$input_path"
                    ;;
            esac
            ;;
    esac
}

# =============================================================================
# PATH VALIDATION AND TESTING
# =============================================================================

# Test if path conversion is working correctly
test_path_conversion() {
    echo "Testing path conversion utilities..."
    echo ""

    local env_type=$(detect_environment)
    echo "Environment: $env_type"
    echo ""

    # Test cases
    local test_paths=(
        "/c/Users/test/file.txt"
        "C:\\Users\\test\\file.txt"
        "C:/Users/test/file.txt"
        "/home/user/file.txt"
        "./relative/path.txt"
    )

    echo "Test Results:"
    echo "-------------"

    for test_path in "${test_paths[@]}"; do
        echo "Input: $test_path"
        echo "  → to_windows_path: $(to_windows_path "$test_path" 2>/dev/null || echo 'FAILED')"
        echo "  → to_unix_path: $(to_unix_path "$test_path" 2>/dev/null || echo 'FAILED')"
        echo "  → normalize_path: $(normalize_path "$test_path")"
        echo "  → smart_path_convert (audio): $(smart_path_convert "$test_path" "audio_playback")"
        echo ""
    done
}

# Print path conversion help
print_path_help() {
    cat << 'EOF'
Path Utilities for echook
==========================================

Available Functions:
-------------------

1. detect_environment
   - Detects the current runtime environment
   - Returns: WSL, GIT_BASH, CYGWIN, MACOS, LINUX, UNKNOWN

2. to_windows_path <path> [format]
   - Converts Unix path to Windows path
   - Format: 'forward' (C:/...) or 'backslash' (C:\...)
   - Example: to_windows_path "/c/Users/name/file.txt" "forward"

3. to_unix_path <path>
   - Converts Windows path to Unix path
   - Example: to_unix_path "C:\Users\name\file.txt"

4. normalize_path <path>
   - Normalizes path separators (always forward slashes)
   - Example: normalize_path "C:\Users\name\file.txt"

5. path_exists <path>
   - Check if path exists (cross-platform)
   - Returns: 0 if exists, 1 if not

6. get_absolute_path <path>
   - Get absolute path from relative path
   - Example: get_absolute_path "./relative/file.txt"

7. smart_path_convert <path> [context]
   - Automatically convert path based on context
   - Context: audio_playback, file_operation, powershell, auto
   - Example: smart_path_convert "/c/file.txt" "audio_playback"

8. test_path_conversion
   - Run tests on path conversion functions
   - Usage: test_path_conversion

Usage Example:
--------------

    source hooks/shared/path_utils.sh

    # Detect environment
    ENV=$(detect_environment)
    echo "Running on: $ENV"

    # Convert path for audio playback
    AUDIO_FILE="/c/Users/name/audio.mp3"
    WIN_PATH=$(smart_path_convert "$AUDIO_FILE" "audio_playback")
    echo "Windows path: $WIN_PATH"

    # Check if file exists
    if path_exists "$AUDIO_FILE"; then
        echo "File exists!"
    fi

EOF
}

# =============================================================================
# INITIALIZATION
# =============================================================================

# Detect environment on load
detect_environment > /dev/null 2>&1

# Export functions for use in other scripts
export -f detect_environment
export -f to_windows_path
export -f to_unix_path
export -f normalize_path
export -f path_exists
export -f get_absolute_path
export -f smart_path_convert
export -f test_path_conversion
export -f print_path_help
