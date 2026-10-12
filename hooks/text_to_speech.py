"""echook hook runner: speak a message with the platform's own TTS engine.

macOS say, Linux espeak / spd-say, Windows and WSL through SAPI.
"""

import platform
import shutil
import subprocess

import runner_core
import platform_shell


def _build_sapi_script(message: str) -> str:
    """PowerShell source for the SAPI speech path (Windows and WSL).

    Same escaping trap as the toast: this is interpolated into a PowerShell
    double-quoted string, so it needs backtick escaping. Until v6.5.1 it used
    the osascript escaper, and any spoken text containing a quote produced a
    script that failed to parse — silent TTS, with `$` and backticks dropped
    from the wording even when it did parse.
    """
    return (
        'Add-Type -AssemblyName System.Speech; '
        '(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('
        f'"{platform_shell.escape_powershell_string(message)}")'
    )


def play_tts(message: str) -> bool:
    """Speak a message using platform-native TTS.

    Args:
        message: Text to speak

    Returns:
        True when an engine accepted the message, False otherwise.
    """
    system = platform.system()
    backend = "unsupported"
    dispatched = False
    reason = f"no TTS engine for platform {system}"

    try:
        if system == "Darwin":
            # argv, not a shell string: `say` needs no escaping.
            subprocess.Popen(
                ["say", message],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            backend, dispatched = "say", True

        elif system == "Linux":
            if platform_shell.is_wsl():
                # WSL: speech is synthesised by the Windows host via SAPI.
                backend = "sapi_wsl"
                dispatched = platform_shell._spawn_powershell(_build_sapi_script(message))
                if not dispatched:
                    reason = "powershell.exe dispatch failed"
            else:
                # Native Linux: try espeak, then spd-say
                backend = "none"
                reason = "no Linux TTS engine found (espeak, spd-say)"
                for cmd_name in ["espeak", "spd-say"]:
                    if shutil.which(cmd_name):
                        subprocess.Popen(
                            [cmd_name, message],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                        backend, dispatched = cmd_name, True
                        break

        elif system == "Windows":
            backend = "sapi"
            dispatched = platform_shell._spawn_powershell(_build_sapi_script(message))
            if not dispatched:
                reason = "powershell.exe dispatch failed"

    except FileNotFoundError as e:
        reason = f"TTS command not found: {e}"
    except Exception as e:
        reason = f"TTS failed: {e}"
        runner_core.log_error(reason)

    if dispatched:
        runner_core.log_event("info", "tts_dispatch", backend=backend, status="DISPATCHED")
    else:
        runner_core.log_error_event("TTS_FAILED", "tts_dispatch",
                        message=reason, backend=backend)
    return dispatched
