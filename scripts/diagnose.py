#!/usr/bin/env python3
"""
echook - Diagnostic Tool

This tool helps diagnose issues with the audio hooks installation.
It checks the environment, configuration, and tests audio playback.

Usage:
    python diagnose.py [--verbose] [--test-audio]

Options:
    --verbose       Show detailed debug information
    --test-audio    Test audio playback
    --help          Show this help message
"""

import json
import os
import sys
import platform
import subprocess
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

# =============================================================================
# CONFIGURATION
# =============================================================================

VERSION = "1.0.0"

class Colors:
    """ANSI color codes for terminal output."""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    RESET = '\033[0m'

    @staticmethod
    def disable():
        """Disable colors (for non-TTY output)."""
        Colors.GREEN = ''
        Colors.RED = ''
        Colors.YELLOW = ''
        Colors.BLUE = ''
        Colors.CYAN = ''
        Colors.BOLD = ''
        Colors.RESET = ''


# Disable colors if not a TTY
if not sys.stdout.isatty():
    Colors.disable()

# =============================================================================
# OUTPUT HELPERS
# =============================================================================

def print_header(text: str) -> None:
    print(f"\n{Colors.BLUE}{Colors.BOLD}{'=' * 60}{Colors.RESET}")
    print(f"{Colors.BLUE}{Colors.BOLD}  {text}{Colors.RESET}")
    print(f"{Colors.BLUE}{Colors.BOLD}{'=' * 60}{Colors.RESET}\n")


def print_section(text: str) -> None:
    print(f"\n{Colors.CYAN}{Colors.BOLD}--- {text} ---{Colors.RESET}\n")


def print_ok(text: str) -> None:
    print(f"  {Colors.GREEN}[OK]{Colors.RESET} {text}")


def print_fail(text: str) -> None:
    print(f"  {Colors.RED}[FAIL]{Colors.RESET} {text}")


def print_warn(text: str) -> None:
    print(f"  {Colors.YELLOW}[WARN]{Colors.RESET} {text}")


def print_info(text: str) -> None:
    print(f"  {Colors.CYAN}[INFO]{Colors.RESET} {text}")


# =============================================================================
# DIAGNOSTIC CHECKS
# =============================================================================

def check_python_version() -> Tuple[bool, str]:
    """Check Python version."""
    version = sys.version_info
    version_str = f"{version.major}.{version.minor}.{version.micro}"

    if version.major >= 3 and version.minor >= 6:
        return True, f"Python {version_str}"
    else:
        return False, f"Python {version_str} (requires 3.6+)"


def check_platform() -> Dict[str, str]:
    """Detect the current platform."""
    system = platform.system()
    release = platform.release()

    result = {
        "system": system,
        "release": release,
        "is_wsl": False,
        "is_git_bash": False,
        "detected": system
    }

    # Check for WSL
    if system == "Linux":
        try:
            with open("/proc/version", "r") as f:
                content = f.read().lower()
                if "microsoft" in content or "wsl" in content:
                    result["is_wsl"] = True
                    result["detected"] = "WSL"
        except:
            pass

    # Check for Git Bash/MSYS2
    if system == "Windows":
        ostype = os.environ.get("OSTYPE", "")
        if "msys" in ostype or "mingw" in ostype:
            result["is_git_bash"] = True
            result["detected"] = "Git Bash"

    return result


def check_hooks_directory() -> Tuple[bool, str, Optional[Path]]:
    """Check if hooks directory exists and contains hook_runner.py."""
    hooks_dir = Path.home() / ".claude" / "hooks"

    if not hooks_dir.exists():
        return False, "Hooks directory not found", None

    hook_runner = hooks_dir / "hook_runner.py"
    if not hook_runner.exists():
        return False, "hook_runner.py not found", hooks_dir

    return True, f"Found at {hooks_dir}", hooks_dir


def check_project_path(hooks_dir: Path) -> Tuple[bool, str, Optional[Path]]:
    """Check if project path is configured and valid."""
    project_path_file = hooks_dir / ".project_path"

    if not project_path_file.exists():
        return False, ".project_path file not found", None

    try:
        recorded_path = project_path_file.read_text(encoding="utf-8").strip()

        # Normalize path for Windows
        if platform.system() == "Windows":
            # Handle Git Bash paths
            if recorded_path.startswith("/") and len(recorded_path) >= 2:
                if recorded_path[1].isalpha():
                    if len(recorded_path) == 2 or recorded_path[2] == '/':
                        drive = recorded_path[1].upper()
                        rest = recorded_path[2:] if len(recorded_path) > 2 else "/"
                        recorded_path = f"{drive}:{rest}"

        project_dir = Path(recorded_path)

        if not project_dir.exists():
            return False, f"Project directory does not exist: {recorded_path}", None

        config_file = project_dir / "config" / "user_preferences.json"
        if not config_file.exists():
            return False, f"Config file not found in project", project_dir

        return True, f"Project: {project_dir}", project_dir

    except Exception as e:
        return False, f"Error reading project path: {e}", None


def check_audio_files(project_dir: Path) -> Tuple[bool, str]:
    """Check if audio files exist."""
    audio_dir = project_dir / "audio" / "default"

    if not audio_dir.exists():
        return False, "Audio directory not found"

    mp3_files = list(audio_dir.glob("*.mp3"))

    if len(mp3_files) >= 9:
        return True, f"Found {len(mp3_files)} audio files"
    elif len(mp3_files) > 0:
        return True, f"Found {len(mp3_files)} audio files (expected 9)"
    else:
        return False, "No MP3 files found"


def check_settings_json() -> Tuple[bool, str]:
    """Check Claude settings.json configuration."""
    settings_file = Path.home() / ".claude" / "settings.json"

    if not settings_file.exists():
        return False, "settings.json not found"

    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))

        if "hooks" not in settings:
            return False, "No hooks configured in settings.json"

        hooks = settings["hooks"]
        configured_hooks = list(hooks.keys())

        if len(configured_hooks) >= 9:
            return True, f"Configured hooks: {', '.join(configured_hooks)}"
        else:
            return True, f"Configured hooks ({len(configured_hooks)}): {', '.join(configured_hooks)}"

    except json.JSONDecodeError as e:
        return False, f"Invalid JSON in settings.json: {e}"
    except Exception as e:
        return False, f"Error reading settings.json: {e}"


def check_config(project_dir: Path) -> Tuple[bool, str]:
    """Check user_preferences.json configuration."""
    config_file = project_dir / "config" / "user_preferences.json"

    if not config_file.exists():
        return False, "user_preferences.json not found"

    try:
        config = json.loads(config_file.read_text(encoding="utf-8"))

        enabled_hooks = config.get("enabled_hooks", {})
        enabled_count = sum(1 for k, v in enabled_hooks.items() if not k.startswith("_") and v is True)

        return True, f"{enabled_count} hooks enabled"

    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}"
    except Exception as e:
        return False, f"Error reading config: {e}"


def check_logs() -> Tuple[bool, str, List[str]]:
    """Check hook trigger logs."""
    if platform.system() == "Windows":
        temp_dir = Path(os.environ.get("TEMP", os.environ.get("TMP", "C:/Windows/Temp")))
    else:
        temp_dir = Path("/tmp")

    log_dir = temp_dir / "claude_audio_hooks_queue" / "logs"
    log_file = log_dir / "hook_triggers.log"

    recent_logs = []

    if not log_file.exists():
        return False, "No trigger logs found (hooks may not have been triggered yet)", recent_logs

    try:
        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        recent_logs = lines[-10:] if len(lines) > 10 else lines

        if len(lines) > 0:
            return True, f"Found {len(lines)} log entries", recent_logs
        else:
            return False, "Log file is empty", recent_logs

    except Exception as e:
        return False, f"Error reading logs: {e}", recent_logs


def test_audio_playback(project_dir: Path) -> Tuple[bool, str]:
    """Test audio playback."""
    audio_file = project_dir / "audio" / "default" / "task-complete.mp3"

    if not audio_file.exists():
        return False, "Test audio file not found"

    system = platform.system()
    print_info(f"Testing audio playback on {system}...")

    try:
        if system == "Windows":
            # Use PowerShell
            ps_cmd = f'''
Add-Type -AssemblyName presentationCore
$p = New-Object System.Windows.Media.MediaPlayer
$p.Open("{str(audio_file).replace(chr(92), '/')}")
Start-Sleep -Milliseconds 500
$p.Play()
Start-Sleep -Seconds 2
$p.Stop()
$p.Close()
'''
            result = subprocess.run(
                ["powershell.exe", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return True, "Audio playback test completed"
            else:
                return False, f"PowerShell error: {result.stderr[:100]}"

        elif system == "Darwin":
            # macOS
            result = subprocess.run(
                ["afplay", str(audio_file)],
                capture_output=True,
                timeout=10
            )
            return result.returncode == 0, "Audio playback test completed" if result.returncode == 0 else "afplay failed"

        elif system == "Linux":
            # Linux - try multiple players
            players = ["mpg123", "ffplay", "paplay", "aplay"]
            for player in players:
                if shutil.which(player):
                    try:
                        if player == "ffplay":
                            cmd = [player, "-nodisp", "-autoexit", "-hide_banner", "-loglevel", "quiet", str(audio_file)]
                        elif player == "mpg123":
                            cmd = [player, "-q", str(audio_file)]
                        else:
                            cmd = [player, str(audio_file)]

                        result = subprocess.run(cmd, capture_output=True, timeout=10)
                        if result.returncode == 0:
                            return True, f"Audio playback test completed (using {player})"
                    except:
                        continue

            return False, "No working audio player found"

        else:
            return False, f"Unsupported platform: {system}"

    except subprocess.TimeoutExpired:
        return False, "Audio playback timed out"
    except Exception as e:
        return False, f"Error during playback: {e}"


# =============================================================================
# MAIN DIAGNOSTIC
# =============================================================================

def run_diagnostics(verbose: bool = False, test_audio: bool = False) -> int:
    """Run all diagnostic checks."""
    print_header(f"echook Diagnostic Tool v{VERSION}")

    issues = 0
    project_dir = None

    # Section 1: Environment
    print_section("Environment")

    # Python version
    ok, msg = check_python_version()
    if ok:
        print_ok(msg)
    else:
        print_fail(msg)
        issues += 1

    # Platform detection
    platform_info = check_platform()
    print_ok(f"Platform: {platform_info['detected']} ({platform_info['system']} {platform_info['release']})")

    if platform_info['is_wsl']:
        print_info("WSL detected - audio will use Windows PowerShell")
    if platform_info['is_git_bash']:
        print_info("Git Bash detected - audio will use Windows PowerShell")

    # macOS version check for Sequoia notification permissions
    if platform_info['system'] == "Darwin":
        try:
            mac_ver = platform.mac_ver()[0]  # e.g. "15.6"
            if mac_ver:
                major = int(mac_ver.split(".")[0])
                print_info(f"macOS version: {mac_ver}")
                if major >= 15:
                    print_warn(
                        f"macOS {major} (Sequoia+) restricts notification permissions. "
                        "osascript notifications may be silently blocked."
                    )
                    print_info(
                        "Audio via afplay works without permissions. "
                        "To enable notifications: System Settings > Notifications > Script Editor"
                    )
        except (ValueError, IndexError):
            pass

    # Section 2: Installation
    print_section("Installation")

    # Hooks directory
    ok, msg, hooks_dir = check_hooks_directory()
    if ok:
        print_ok(msg)
    else:
        print_fail(msg)
        issues += 1

    # Project path
    if hooks_dir:
        ok, msg, project_dir = check_project_path(hooks_dir)
        if ok:
            print_ok(msg)
        else:
            print_fail(msg)
            issues += 1

    # Audio files
    if project_dir:
        ok, msg = check_audio_files(project_dir)
        if ok:
            print_ok(msg)
        else:
            print_fail(msg)
            issues += 1

    # Section 3: Configuration
    print_section("Configuration")

    # settings.json
    ok, msg = check_settings_json()
    if ok:
        print_ok(msg)
    else:
        print_fail(msg)
        issues += 1

    # user_preferences.json
    if project_dir:
        ok, msg = check_config(project_dir)
        if ok:
            print_ok(msg)
        else:
            print_warn(msg)

    # Section 4: Logs
    print_section("Recent Activity")

    ok, msg, recent_logs = check_logs()
    if ok:
        print_ok(msg)
        if verbose and recent_logs:
            print_info("Recent log entries:")
            for log in recent_logs[-5:]:
                print(f"      {log}")
    else:
        print_warn(msg)

    # Section 5: Audio Test (optional)
    if test_audio and project_dir:
        print_section("Audio Playback Test")
        ok, msg = test_audio_playback(project_dir)
        if ok:
            print_ok(msg)
        else:
            print_fail(msg)
            issues += 1

    # Summary
    print_section("Summary")

    if issues == 0:
        print_ok(f"{Colors.GREEN}All checks passed!{Colors.RESET}")
        print_info("If audio is not playing, try setting CLAUDE_HOOKS_DEBUG=1 and check the debug logs")
    else:
        print_fail(f"{issues} issue(s) found")
        print_info("Fix the issues above and run the diagnostic again")

    print()
    return 0 if issues == 0 else 1


def main() -> int:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="echook Diagnostic Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python diagnose.py                  # Basic diagnostic
  python diagnose.py --verbose        # Show detailed information
  python diagnose.py --test-audio     # Include audio playback test
  python diagnose.py -v --test-audio  # Full diagnostic with audio test
"""
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed debug information")
    parser.add_argument("--test-audio", action="store_true", help="Test audio playback")

    args = parser.parse_args()

    return run_diagnostics(verbose=args.verbose, test_audio=args.test_audio)


if __name__ == "__main__":
    sys.exit(main())
