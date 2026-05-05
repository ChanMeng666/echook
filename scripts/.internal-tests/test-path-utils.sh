#!/bin/bash
# Test script for path utilities
# Verifies path conversion works correctly across different environments

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RESET='\033[0m'

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Source path utilities
if [ -f "$PROJECT_DIR/hooks/shared/path_utils.sh" ]; then
    source "$PROJECT_DIR/hooks/shared/path_utils.sh"
else
    echo -e "${RED}✗ Error: path_utils.sh not found${RESET}"
    echo "  Expected: $PROJECT_DIR/hooks/shared/path_utils.sh"
    exit 1
fi

echo ""
echo "================================================"
echo "  Path Utilities Test Suite"
echo "================================================"
echo ""

# Test counter
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Test function
run_test() {
    local test_name="$1"
    local test_cmd="$2"
    local expected="$3"

    ((TESTS_RUN++))

    echo -n "Testing $test_name... "

    result=$(eval "$test_cmd" 2>/dev/null)

    if [ "$result" = "$expected" ]; then
        echo -e "${GREEN}✓ PASS${RESET}"
        ((TESTS_PASSED++))
        return 0
    else
        echo -e "${RED}✗ FAIL${RESET}"
        echo "  Expected: $expected"
        echo "  Got:      $result"
        ((TESTS_FAILED++))
        return 1
    fi
}

# Test environment detection
echo "=== Environment Detection ==="
ENV_TYPE=$(detect_environment)
echo "Environment Type: $ENV_TYPE"
echo ""

# Test path conversion based on environment
echo "=== Path Conversion Tests ==="

case "$ENV_TYPE" in
    WSL)
        echo "Testing WSL environment path conversions..."
        echo ""

        # Test Unix to Windows conversion (WSL uses /mnt/c/ format)
        run_test "WSL: /c/ to Windows (forward)" \
                 "to_windows_path '/c/Users/test/file.txt' 'forward'" \
                 "C:/Users/test/file.txt"

        run_test "WSL: /c/ to Windows (backslash)" \
                 "to_windows_path '/c/Users/test/file.txt' 'backslash'" \
                 "C:\Users\test\file.txt"

        run_test "WSL: /d/ to Windows" \
                 "to_windows_path '/d/projects/file.txt' 'forward'" \
                 "D:/projects/file.txt"

        # Test Windows to Unix conversion (WSL uses /mnt/ format)
        run_test "WSL: Windows to /mnt/ (backslash)" \
                 "to_unix_path 'C:\Users\test\file.txt'" \
                 "/mnt/c/Users/test/file.txt"

        run_test "WSL: Windows to /mnt/ (forward)" \
                 "to_unix_path 'C:/Users/test/file.txt'" \
                 "/mnt/c/Users/test/file.txt"

        run_test "WSL: D: drive to /mnt/" \
                 "to_unix_path 'D:\projects\file.txt'" \
                 "/mnt/d/projects/file.txt"
        ;;

    GIT_BASH|CYGWIN)
        echo "Testing Git Bash/Cygwin environment path conversions..."
        echo ""

        # Test Unix to Windows conversion (Git Bash uses /c/ format)
        run_test "Unix to Windows (forward)" \
                 "to_windows_path '/c/Users/test/file.txt' 'forward'" \
                 "C:/Users/test/file.txt"

        run_test "Unix to Windows (backslash)" \
                 "to_windows_path '/c/Users/test/file.txt' 'backslash'" \
                 "C:\Users\test\file.txt"

        run_test "Unix to Windows (D: drive)" \
                 "to_windows_path '/d/projects/file.txt' 'forward'" \
                 "D:/projects/file.txt"

        # Test Windows to Unix conversion (Git Bash uses /c/ format)
        run_test "Windows to Unix (backslash)" \
                 "to_unix_path 'C:\Users\test\file.txt'" \
                 "/c/Users/test/file.txt"

        run_test "Windows to Unix (forward)" \
                 "to_unix_path 'C:/Users/test/file.txt'" \
                 "/c/Users/test/file.txt"

        run_test "Windows to Unix (D: drive)" \
                 "to_unix_path 'D:\projects\file.txt'" \
                 "/d/projects/file.txt"
        ;;

    LINUX|MACOS)
        echo "Testing Unix environment (no conversion needed)..."
        echo ""

        # Test that Unix paths stay as-is
        run_test "Unix path unchanged" \
                 "to_windows_path '/home/user/file.txt'" \
                 "/home/user/file.txt"

        run_test "Unix absolute path" \
                 "to_unix_path '/home/user/file.txt'" \
                 "/home/user/file.txt"
        ;;
esac

echo ""

# Test normalize_path
echo "=== Path Normalization Tests ==="

run_test "Normalize backslashes" \
         "normalize_path 'C:\Users\test\file.txt'" \
         "C:/Users/test/file.txt"

run_test "Normalize mixed" \
         "normalize_path '/c/Users\test/file.txt'" \
         "/c/Users/test/file.txt"

run_test "Already normalized" \
         "normalize_path '/c/Users/test/file.txt'" \
         "/c/Users/test/file.txt"

echo ""

# Test smart_path_convert
echo "=== Smart Path Conversion Tests ==="

case "$ENV_TYPE" in
    WSL)
        run_test "WSL: Smart convert for audio playback" \
                 "smart_path_convert '/c/Users/test/audio.mp3' 'audio_playback'" \
                 "C:/Users/test/audio.mp3"

        run_test "WSL: Smart convert for PowerShell" \
                 "smart_path_convert '/c/Users/test/file.txt' 'powershell'" \
                 "C:/Users/test/file.txt"
        ;;

    GIT_BASH|CYGWIN)
        run_test "Smart convert for audio playback" \
                 "smart_path_convert '/c/Users/test/audio.mp3' 'audio_playback'" \
                 "C:/Users/test/audio.mp3"

        run_test "Smart convert for PowerShell" \
                 "smart_path_convert '/c/Users/test/file.txt' 'powershell'" \
                 "C:/Users/test/file.txt"
        ;;

    LINUX|MACOS)
        run_test "Smart convert (no change needed)" \
                 "smart_path_convert '/home/user/audio.mp3' 'audio_playback'" \
                 "/home/user/audio.mp3"
        ;;
esac

echo ""

# Test actual project paths
echo "=== Real Project Path Tests ==="

if [ -f "$HOME/.claude/hooks/.project_path" ]; then
    PROJECT_PATH=$(cat "$HOME/.claude/hooks/.project_path")
    echo "Recorded project path: $PROJECT_PATH"

    # Test if we can find audio files
    AUDIO_DIR="$PROJECT_PATH/audio/default"

    echo -n "Testing audio directory access... "
    if [ -d "$AUDIO_DIR" ]; then
        echo -e "${GREEN}✓ PASS${RESET}"
        ((TESTS_PASSED++))

        # Test path conversion for actual audio file
        AUDIO_FILE="$AUDIO_DIR/task-complete.mp3"
        if [ -f "$AUDIO_FILE" ]; then
            echo -n "Testing actual audio file path... "
            CONVERTED=$(smart_path_convert "$AUDIO_FILE" "audio_playback")
            if [ -n "$CONVERTED" ]; then
                echo -e "${GREEN}✓ PASS${RESET}"
                echo "  Original:  $AUDIO_FILE"
                echo "  Converted: $CONVERTED"
                ((TESTS_PASSED++))
            else
                echo -e "${RED}✗ FAIL${RESET}"
                ((TESTS_FAILED++))
            fi
            ((TESTS_RUN++))
        fi
    else
        echo -e "${RED}✗ FAIL${RESET}"
        echo "  Audio directory not found: $AUDIO_DIR"
        ((TESTS_FAILED++))
    fi
    ((TESTS_RUN++))
else
    echo -e "${YELLOW}⚠ Skipped: Project not installed${RESET}"
fi

echo ""

# Performance test
echo "=== Performance Test ==="
echo -n "Running 100 path conversions... "

START_TIME=$(date +%s%N 2>/dev/null || date +%s)

for i in {1..100}; do
    to_windows_path "/c/Users/test/file$i.txt" "forward" > /dev/null 2>&1
done

END_TIME=$(date +%s%N 2>/dev/null || date +%s)
DURATION=$((END_TIME - START_TIME))

if command -v bc &> /dev/null; then
    DURATION_MS=$(echo "scale=2; $DURATION / 1000000" | bc)
    echo -e "${GREEN}✓ Complete${RESET}"
    echo "  Total time: ${DURATION_MS}ms"
    echo "  Average: $(echo "scale=2; $DURATION_MS / 100" | bc)ms per conversion"
else
    echo -e "${GREEN}✓ Complete${RESET}"
fi

echo ""

# Summary
echo "================================================"
echo "  Test Results"
echo "================================================"
echo ""
echo "Environment: $ENV_TYPE"
echo "Tests Run:   $TESTS_RUN"
echo "Passed:      $TESTS_PASSED"
echo "Failed:      $TESTS_FAILED"
echo ""

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ All tests passed!${RESET}"
    echo ""
    echo "Path utilities are working correctly on your system."
    exit 0
else
    echo -e "${RED}✗ Some tests failed${RESET}"
    echo ""
    echo "This may indicate compatibility issues with your environment."
    echo "Please report this at:"
    echo "  https://github.com/ChanMeng666/echook/issues"
    echo ""
    echo "Include the following information:"
    echo "  - Environment: $ENV_TYPE"
    echo "  - OS: $OSTYPE"
    echo "  - Failed tests: $TESTS_FAILED"
    exit 1
fi
