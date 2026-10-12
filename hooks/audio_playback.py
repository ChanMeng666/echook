"""echook hook runner: play one audio file with the platform's own player.

Windows (PowerShell MediaPlayer, three fallbacks), macOS (afplay), Linux
(mpg123 / ffplay / paplay / aplay) and WSL (copy to the Windows side).
"""

import os
import platform
import subprocess
import time
from pathlib import Path

import runner_core
import platform_shell


def play_audio_windows(audio_file: Path) -> bool:
    """Play audio on Windows using multiple fallback methods."""
    # Escape path for PowerShell
    win_path = str(audio_file).replace("\\", "/")
    win_path_escaped = platform_shell.escape_powershell_string(win_path)

    runner_core.log_debug(f"Windows audio playback: {win_path}")

    # Method 1: Direct PowerShell command with MediaPlayer.
    # MediaPlayer.Open() is async: we poll NaturalDuration.HasTimeSpan with a
    # short ceiling, then sleep for the real clip length + a tail buffer so
    # Stop()/Close() don't truncate playback (issue #14). Fallback to a
    # generous-but-bounded sleep if Open() never resolves.
    try:
        ps_cmd = (
            'Add-Type -AssemblyName presentationCore; '
            '$p = New-Object System.Windows.Media.MediaPlayer; '
            f'$p.Open("{win_path_escaped}"); '
            '$deadline = (Get-Date).AddMilliseconds(1500); '
            'while (-not $p.NaturalDuration.HasTimeSpan -and (Get-Date) -lt $deadline) '
            '{ Start-Sleep -Milliseconds 50 }; '
            '$p.Play(); '
            'if ($p.NaturalDuration.HasTimeSpan) '
            '{ Start-Sleep -Milliseconds ([int]($p.NaturalDuration.TimeSpan.TotalMilliseconds + 500)) } '
            'else { Start-Sleep -Seconds 10 }; '
            '$p.Stop(); $p.Close()'
        )
        proc = subprocess.Popen(
            ["powershell.exe", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", ps_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        runner_core.log_debug(f"Started PowerShell MediaPlayer (PID: {proc.pid})")
        return True
    except FileNotFoundError:
        runner_core.log_debug("PowerShell not found, trying fallback")
    except Exception as e:
        runner_core.log_error(f"PowerShell MediaPlayer failed: {e}")

    # Method 2: Use PowerShell script file
    try:
        temp_dir = platform_shell.get_safe_temp_dir()
        script_file = temp_dir / f"claude_audio_{os.getpid()}_{int(time.time())}.ps1"

        ps_script = f'''
Add-Type -AssemblyName presentationCore
$player = New-Object System.Windows.Media.MediaPlayer
$player.Open("{win_path_escaped}")
$deadline = (Get-Date).AddMilliseconds(1500)
while (-not $player.NaturalDuration.HasTimeSpan -and (Get-Date) -lt $deadline) {{
    Start-Sleep -Milliseconds 50
}}
$player.Play()
if ($player.NaturalDuration.HasTimeSpan) {{
    Start-Sleep -Milliseconds ([int]($player.NaturalDuration.TimeSpan.TotalMilliseconds + 500))
}} else {{
    Start-Sleep -Seconds 10
}}
$player.Stop()
$player.Close()
Remove-Item -Path $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
'''
        script_file.write_text(ps_script, encoding="utf-8")
        runner_core.log_debug(f"Created PowerShell script: {script_file}")

        proc = subprocess.Popen(
            ["powershell.exe", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(script_file)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        runner_core.log_debug(f"Started PowerShell script (PID: {proc.pid})")
        return True
    except Exception as e:
        runner_core.log_error(f"PowerShell script method failed: {e}")

    # Method 3: Use WMPlayer.OCX COM object
    try:
        ps_cmd = (
            f'$w = New-Object -ComObject WMPlayer.OCX; $w.URL = "{win_path_escaped}"; '
            '$deadline = (Get-Date).AddMilliseconds(1500); '
            'while (($w.currentMedia -eq $null -or $w.currentMedia.duration -eq 0) '
            '-and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 50 }; '
            'if ($w.currentMedia -ne $null -and $w.currentMedia.duration -gt 0) '
            '{ Start-Sleep -Milliseconds ([int]($w.currentMedia.duration * 1000 + 500)) } '
            'else { Start-Sleep -Seconds 10 }'
        )
        proc = subprocess.Popen(
            ["powershell.exe", "-Command", ps_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        runner_core.log_debug(f"Started WMPlayer.OCX (PID: {proc.pid})")
        return True
    except Exception as e:
        runner_core.log_error(f"WMPlayer.OCX method failed: {e}")
        return False


def play_audio_macos(audio_file: Path) -> bool:
    """Play audio on macOS using afplay."""
    runner_core.log_debug(f"macOS audio playback: {audio_file}")
    try:
        proc = subprocess.Popen(
            ["afplay", str(audio_file)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        runner_core.log_debug(f"Started afplay (PID: {proc.pid})")
        return True
    except FileNotFoundError:
        runner_core.log_error("afplay not found")
        return False
    except Exception as e:
        runner_core.log_error(f"afplay failed: {e}")
        return False


def play_audio_linux(audio_file: Path) -> bool:
    """Play audio on Linux using available players."""
    runner_core.log_debug(f"Linux audio playback: {audio_file}")

    players = [
        (["mpg123", "-q"], "mpg123"),
        (["ffplay", "-nodisp", "-autoexit", "-hide_banner", "-loglevel", "quiet"], "ffplay"),
        (["paplay"], "paplay"),
        (["aplay"], "aplay"),
    ]

    for player_cmd, player_name in players:
        try:
            cmd = player_cmd + [str(audio_file)]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            runner_core.log_debug(f"Started {player_name} (PID: {proc.pid})")
            return True
        except FileNotFoundError:
            runner_core.log_debug(f"{player_name} not found, trying next")
            continue
        except Exception as e:
            runner_core.log_debug(f"{player_name} failed: {e}")
            continue

    runner_core.log_error("No audio player found on Linux")
    return False


def play_audio_wsl(audio_file: Path) -> bool:
    """Play audio in WSL by copying to Windows temp and using PowerShell."""
    runner_core.log_debug(f"WSL audio playback: {audio_file}")

    try:
        import shutil

        # Get Windows temp directory
        # Try multiple methods to find a writable Windows temp
        win_temp_candidates = []

        # Method 1: Use WSLENV or inherited Windows env vars
        for env_var in ["TEMP", "TMP", "USERPROFILE"]:
            val = os.environ.get(env_var)
            if val and val.startswith("/mnt/"):
                win_temp_candidates.append(Path(val))

        # Method 2: Use wslvar to get Windows TEMP
        try:
            win_temp_path = subprocess.check_output(
                ["wslvar", "TEMP"],
                text=True,
                stderr=subprocess.DEVNULL
            ).strip()
            if win_temp_path:
                # Convert Windows path to WSL path
                wsl_path = subprocess.check_output(
                    ["wslpath", "-u", win_temp_path],
                    text=True,
                    stderr=subprocess.DEVNULL
                ).strip()
                win_temp_candidates.append(Path(wsl_path))
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        # Method 3: Standard Windows temp locations via /mnt
        windir = os.environ.get("WINDIR", "")
        if windir and windir.startswith("/mnt/"):
            win_temp_candidates.append(Path(windir) / "Temp")

        win_temp_candidates.extend([
            Path("/mnt/c/Windows/Temp"),
            Path("/mnt/c/Users") / os.environ.get("USER", "Public") / "AppData/Local/Temp",
        ])

        # Find first writable temp directory
        win_temp = None
        for candidate in win_temp_candidates:
            try:
                if candidate.exists() and os.access(str(candidate), os.W_OK):
                    win_temp = candidate
                    break
            except Exception:
                continue

        if not win_temp:
            runner_core.log_error("Could not find writable Windows temp directory from WSL")
            # Fallback to native Linux playback
            return play_audio_linux(audio_file)

        runner_core.log_debug(f"Using Windows temp: {win_temp}")

        # Copy audio file to Windows temp
        temp_filename = f"claude_audio_{int(time.time())}_{os.getpid()}.mp3"
        wsl_temp_file = win_temp / temp_filename
        shutil.copy(str(audio_file), str(wsl_temp_file))
        runner_core.log_debug(f"Copied audio to: {wsl_temp_file}")

        # Convert to Windows path for PowerShell
        try:
            win_path = subprocess.check_output(
                ["wslpath", "-w", str(wsl_temp_file)],
                text=True,
                stderr=subprocess.DEVNULL
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Manual conversion
            path_str = str(wsl_temp_file)
            if path_str.startswith("/mnt/"):
                drive = path_str[5].upper()
                win_path = f"{drive}:{path_str[6:]}".replace("/", "\\")
            else:
                runner_core.log_error("Could not convert WSL path to Windows path")
                return play_audio_linux(audio_file)

        runner_core.log_debug(f"Windows path: {win_path}")
        win_path_escaped = platform_shell.escape_powershell_string(win_path.replace("\\", "/"))

        # Play using PowerShell
        ps_command = f'''
Add-Type -AssemblyName presentationCore
$player = New-Object System.Windows.Media.MediaPlayer
$player.Open("{win_path_escaped}")
$deadline = (Get-Date).AddMilliseconds(1500)
while (-not $player.NaturalDuration.HasTimeSpan -and (Get-Date) -lt $deadline) {{
    Start-Sleep -Milliseconds 50
}}
$player.Play()
if ($player.NaturalDuration.HasTimeSpan) {{
    Start-Sleep -Milliseconds ([int]($player.NaturalDuration.TimeSpan.TotalMilliseconds + 500))
}} else {{
    Start-Sleep -Seconds 10
}}
$player.Stop()
$player.Close()
Remove-Item -Path "{win_path_escaped}" -ErrorAction SilentlyContinue
'''

        proc = subprocess.Popen(
            ["powershell.exe", "-Command", ps_command],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        runner_core.log_debug(f"Started WSL PowerShell playback (PID: {proc.pid})")
        return True

    except Exception as e:
        runner_core.log_error(f"WSL audio playback failed: {e}")
        # Fallback to native Linux playback
        runner_core.log_debug("Falling back to native Linux playback")
        return play_audio_linux(audio_file)


def play_audio(audio_file: Path) -> bool:
    """Play audio file using platform-specific method."""
    system = platform.system()
    runner_core.log_debug(f"Platform: {system}")

    if system == "Windows":
        return play_audio_windows(audio_file)
    elif system == "Darwin":
        return play_audio_macos(audio_file)
    elif system == "Linux":
        if platform_shell.is_wsl():
            runner_core.log_debug("Detected WSL environment")
            return play_audio_wsl(audio_file)
        return play_audio_linux(audio_file)
    else:
        runner_core.log_error(f"Unsupported platform: {system}")
        return False
