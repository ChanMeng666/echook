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

    Same trap as the toast: the spoken text is untrusted (it can be the
    agent's last message), so it reaches PowerShell base64-encoded through
    platform_shell.powershell_text_expr, never as source. Until v6.5.1 it used
    the osascript escaper (any quote broke the script); until 7.2.4 a backtick
    escaper that missed the typographic quotes PowerShell also accepts, which
    let a message close the literal and run its own statements.
    """
    return (
        'Add-Type -AssemblyName System.Speech; '
        '(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('
        f'{platform_shell.powershell_text_expr(message)})'
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
