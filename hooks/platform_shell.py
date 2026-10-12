"""echook hook runner: host-platform primitives shared by the dispatch modules.

PowerShell string escaping and hidden PowerShell launch (fire-and-forget and
run-to-completion), WSL detection, and a writable temp directory.
"""

import os
import platform
import subprocess
from pathlib import Path
from typing import List

import runner_core


def escape_powershell_string(s: str) -> str:
    """Escape a string for safe use in PowerShell double-quoted strings."""
    # Escape backticks, double quotes, and dollar signs
    s = s.replace('`', '``')
    s = s.replace('"', '`"')
    s = s.replace('$', '`$')
    return s


def get_safe_temp_dir() -> Path:
    """Get a safe temporary directory that exists and is writable."""
    candidates: List[Path] = []

    if platform.system() == "Windows":
        # Windows: prefer TEMP, then TMP, then USERPROFILE/Temp, then fallback
        for env_var in ["TEMP", "TMP"]:
            val = os.environ.get(env_var)
            if val:
                candidates.append(Path(val))

        userprofile = os.environ.get("USERPROFILE")
        if userprofile:
            candidates.append(Path(userprofile) / "AppData" / "Local" / "Temp")

        # Windows fallback
        windir = os.environ.get("WINDIR", "C:/Windows")
        candidates.append(Path(windir) / "Temp")
        candidates.append(Path("C:/Windows/Temp"))
    else:
        # Unix: prefer TMPDIR, then standard locations
        tmpdir = os.environ.get("TMPDIR")
        if tmpdir:
            candidates.append(Path(tmpdir))
        candidates.extend([
            Path("/tmp"),
            Path("/var/tmp"),
            Path.home() / ".cache" / "claude_hooks_temp",
        ])

    # Find first existing and writable directory
    for candidate in candidates:
        try:
            if candidate.exists() and os.access(str(candidate), os.W_OK):
                runner_core.log_debug(f"Using temp dir: {candidate}")
                return candidate
        except Exception:
            continue

    # Last resort: create in home directory
    fallback = Path.home() / ".cache" / "claude_hooks_temp"
    fallback.mkdir(parents=True, exist_ok=True)
    runner_core.log_debug(f"Using fallback temp dir: {fallback}")
    return fallback


def is_wsl() -> bool:
    """Check if running in WSL."""
    try:
        with open("/proc/version", "r") as f:
            content = f.read().lower()
            return "microsoft" in content or "wsl" in content
    except (FileNotFoundError, PermissionError):
        return False


# _run_powershell outcomes. The failed/inconclusive split is load-bearing: a
# *failed* backend is genuinely broken here and the next one down may be cached
# as the winner, but an *inconclusive* one may merely be slow, so caching past
# it would be a guess with a week-long lifetime.
PROBE_OK = "ok"
PROBE_FAILED = "failed"
PROBE_INCONCLUSIVE = "inconclusive"


def _powershell_command(script: str) -> List[str]:
    """Always ``powershell.exe`` (Windows PowerShell 5.1), never ``pwsh`` — the
    WinRT projection the toast backend relies on needs extra assembly loading
    under PowerShell 7. ``-NoProfile`` keeps a hook from paying for the user's
    profile on every notification or spoken message.
    """
    return [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-WindowStyle", "Hidden", "-Command", script,
    ]


def _creation_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def _spawn_powershell(script: str) -> bool:
    """Fire-and-forget a hidden PowerShell script. True means it was spawned.

    Never raises.
    """
    try:
        subprocess.Popen(
            _powershell_command(script),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_creation_flags(),
        )
        return True
    except Exception as e:
        runner_core.log_debug(f"PowerShell dispatch failed: {e}")
        return False


def _run_powershell(script: str, timeout: float) -> str:
    """Run a hidden PowerShell script to completion. Never raises.

    Returns PROBE_OK (exit 0), PROBE_FAILED (non-zero exit — broken here), or
    PROBE_INCONCLUSIVE (timed out or could not be launched, so this host has
    told us nothing we may act on).
    """
    try:
        proc = subprocess.run(
            _powershell_command(script),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            creationflags=_creation_flags(),
        )
        return PROBE_OK if proc.returncode == 0 else PROBE_FAILED
    except subprocess.TimeoutExpired:
        runner_core.log_debug(f"PowerShell probe timed out after {timeout}s")
        return PROBE_INCONCLUSIVE
    except Exception as e:
        runner_core.log_debug(f"PowerShell probe could not run: {e}")
        return PROBE_INCONCLUSIVE
