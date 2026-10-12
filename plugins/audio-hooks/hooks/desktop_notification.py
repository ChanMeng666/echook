"""echook hook runner: desktop notifications (toasts).

macOS osascript, Linux notify-send, WSL tray balloon, and on Windows a probed
and cached backend chain (WinRT toast, BurntToast, tray balloon).
"""

import json
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple

import runner_core
import platform_shell


def _escape_notification_string(s: str) -> str:
    """Escape a string for safe use in an osascript literal.

    macOS only. PowerShell does not treat ``\\`` as an escape character, so
    using this on a PowerShell command produces a script that fails to parse
    the moment the body contains a quote — use platform_shell.escape_powershell_string()
    there instead (v6.5.1 fix).
    """
    # Remove characters that could cause shell/osascript injection
    return s.replace('"', '\\"').replace("'", "\\'").replace('`', '').replace('$', '')


# Windows toast backends, in preference order. Probed once and cached: the
# probe costs a synchronous PowerShell launch, which a hook must not pay on
# every event.
_WINDOWS_BACKENDS: Tuple[str, ...] = ("winrt", "burnttoast", "notifyicon")
_NOTIFY_BACKEND_FILE = "notify_backend"
_NOTIFY_BACKEND_TTL_SEC = 7 * 24 * 3600  # re-probe weekly; OS upgrades change what works

# Wall-clock ceiling for the whole probe chain. Every handler in
# plugins/audio-hooks/hooks/hooks.json is registered with "timeout": 10, and
# Windows kills an async hook's process tree (docs/EVENT_BEHAVIOR_NOTES.md), so
# a chain that overran would be reaped mid-flight. The budget stays under that
# so the probe always survives long enough to persist its verdict.
_NOTIFY_PROBE_BUDGET_SEC = 8.0

# Per-backend timeouts. Generous against measured cost (a warm WinRT toast and
# the module query each resolve in ~0.3 s here) but they must *sum* to less than
# the budget, or a backend late in the chain could never be reached and a host
# where WinRT fails would re-probe forever instead of settling on a verdict.
# A backend is either given its full timeout or not run at all — the budget is
# never spent by truncating one, because a squeezed timeout would condemn a
# working backend and pin the host to a worse one for a week. A genuinely slow
# host overruns instead, which is inconclusive rather than wrong: nothing is
# cached, the balloon still fires, and the retry finds PowerShell warm.
_WINRT_PROBE_TIMEOUT_SEC = 3.0
_MODULE_QUERY_TIMEOUT_SEC = 1.5
_BURNTTOAST_PROBE_TIMEOUT_SEC = 3.0


_BURNTTOAST_QUERY = "if (Get-Module -ListAvailable -Name BurntToast) { exit 0 } else { exit 1 }"

# Well-known shell AppUserModelID for Windows PowerShell. A WinRT toast must be
# addressed to a *registered* AUMID or Windows drops it silently; borrowing the
# shell's own PowerShell entry means nothing has to be installed or registered.
_POWERSHELL_AUMID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"


def _build_windows_toast_script(backend: str, title: str, message: str,
                                urgency: str = "normal") -> str:
    """Build the PowerShell source for one Windows notification backend.

    Split out from the dispatcher so the generated script can be parsed in a
    test without spawning a toast (see tests/test_desktop_notification.py).
    """
    safe_title = platform_shell.escape_powershell_string(title)
    safe_message = platform_shell.escape_powershell_string(message)

    if backend == "winrt":
        # Text is inserted as XML *text nodes*, not interpolated into the XML
        # source, so XML metacharacters in a tool command cannot break the
        # document. Escaping still applies to the PowerShell literal itself.
        # silent="true" because the audio track already played the sound.
        duration = 'long' if urgency == "critical" else 'short'
        return (
            '$ErrorActionPreference = "Stop"; '
            '$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]; '
            '$null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]; '
            '$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent('
            '[Windows.UI.Notifications.ToastTemplateType]::ToastText02); '
            '$nodes = $xml.GetElementsByTagName("text"); '
            f'$null = $nodes.Item(0).AppendChild($xml.CreateTextNode("{safe_title}")); '
            f'$null = $nodes.Item(1).AppendChild($xml.CreateTextNode("{safe_message}")); '
            '$audio = $xml.CreateElement("audio"); '
            '$audio.SetAttribute("silent", "true"); '
            '$null = $xml.DocumentElement.AppendChild($audio); '
            f'$xml.DocumentElement.SetAttribute("duration", "{duration}"); '
            '$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); '
            '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('
            f'"{_POWERSHELL_AUMID}").Show($toast)'
        )

    if backend == "burnttoast":
        return (
            '$ErrorActionPreference = "Stop"; '
            'Import-Module BurntToast -ErrorAction Stop; '
            f'New-BurntToastNotification -Text "{safe_title}", "{safe_message}" -Silent'
        )

    # notifyicon: pre-Win10 tray balloon. Windows 11 drops these unpredictably,
    # hence its place at the end of the chain, but it is still correct on 7/8
    # and on hosts where the WinRT projection is unavailable.
    icon = "Warning" if urgency == "critical" else "Info"
    return (
        '[void][System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms"); '
        '$n = New-Object System.Windows.Forms.NotifyIcon; '
        '$n.Icon = [System.Drawing.SystemIcons]::Information; '
        '$n.Visible = $true; '
        f'$n.ShowBalloonTip(5000, "{safe_title}", "{safe_message}", '
        f'[System.Windows.Forms.ToolTipIcon]::{icon}); '
        'Start-Sleep -Seconds 6; '
        '$n.Dispose()'
    )


def _notify_backend_cache_path() -> Optional[Path]:
    """Marker file recording which Windows toast backend works on this host."""
    try:
        runner_core.ensure_queue_dir()
        return runner_core._prefs().queue_dir / _NOTIFY_BACKEND_FILE
    except Exception:
        return None


def _read_cached_notify_backend() -> Optional[str]:
    """Return the cached backend, or None when missing, stale or unreadable."""
    path = _notify_backend_cache_path()
    if path is None:
        return None
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        backend = data.get("backend")
        if backend not in _WINDOWS_BACKENDS:
            return None
        if data.get("release") != platform.release():
            return None
        if time.time() - float(data.get("ts", 0)) > _NOTIFY_BACKEND_TTL_SEC:
            return None
        return str(backend)
    except Exception as e:
        runner_core.log_debug(f"Could not read notify backend cache: {e}")
        return None


def _write_cached_notify_backend(backend: str) -> None:
    path = _notify_backend_cache_path()
    if path is None:
        return
    try:
        path.write_text(
            json.dumps({"backend": backend, "ts": time.time(), "release": platform.release()}),
            encoding="utf-8",
        )
    except OSError as e:
        runner_core.log_debug(f"Could not write notify backend cache: {e}")


def _clear_cached_notify_backend() -> None:
    path = _notify_backend_cache_path()
    if path is None:
        return
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _probe_windows_backend(title: str, message: str, urgency: str) -> Tuple[Optional[str], bool]:
    """Walk the chain until a backend is confirmed, showing *this* notification
    through it. Returns (backend, cacheable).

    The whole chain shares one wall-clock budget (_NOTIFY_PROBE_BUDGET_SEC): a
    probe that overran the hook's registered timeout would be reaped by the
    harness, and a reaped probe could leave behind a cache entry naming a
    backend it never finished validating. So a backend runs only when its full
    timeout still fits in the budget, and anything inconclusive — a timeout, a
    launch failure, or a budget too small to finish the chain — makes the whole
    probe non-cacheable. The notification still goes out best-effort through the
    tray balloon; only the week-long verdict is withheld, and the next
    notification probes again. First-of-the-week may therefore be best-effort
    with the cache written on the second one.
    """
    deadline = time.monotonic() + _NOTIFY_PROBE_BUDGET_SEC
    inconclusive = False

    for backend in _WINDOWS_BACKENDS:
        if backend == "notifyicon":
            break  # last resort, handled below — it can never be validated

        if backend == "burnttoast":
            if deadline - time.monotonic() < _MODULE_QUERY_TIMEOUT_SEC + _BURNTTOAST_PROBE_TIMEOUT_SEC:
                inconclusive = True
                break
            available = platform_shell._run_powershell(_BURNTTOAST_QUERY, _MODULE_QUERY_TIMEOUT_SEC)
            if available == platform_shell.PROBE_INCONCLUSIVE:
                inconclusive = True
                break
            if available != platform_shell.PROBE_OK:
                continue  # not installed; never install it
            timeout = _BURNTTOAST_PROBE_TIMEOUT_SEC
        else:
            timeout = _WINRT_PROBE_TIMEOUT_SEC

        if deadline - time.monotonic() < timeout:
            inconclusive = True
            break

        result = platform_shell._run_powershell(
            _build_windows_toast_script(backend, title, message, urgency), timeout
        )
        if result == platform_shell.PROBE_OK:
            return backend, True
        if result == platform_shell.PROBE_INCONCLUSIVE:
            inconclusive = True
            break

    # The balloon only lives as long as its host process, so it can never be run
    # synchronously; a successful spawn is all the signal there is.
    if platform_shell._spawn_powershell(_build_windows_toast_script("notifyicon", title, message, urgency)):
        return "notifyicon", not inconclusive
    return None, False


def _send_windows_notification(title: str, message: str, urgency: str) -> Tuple[bool, str]:
    """Dispatch on Windows via the cached backend, probing the chain if needed.

    Returns (dispatched, backend_name).
    """
    backend = _read_cached_notify_backend()
    if backend:
        if platform_shell._spawn_powershell(_build_windows_toast_script(backend, title, message, urgency)):
            return True, backend
        # The cached choice can no longer even be spawned: re-probe the chain.
        _clear_cached_notify_backend()

    backend, cacheable = _probe_windows_backend(title, message, urgency)
    if backend is None:
        return False, "none"
    if cacheable:
        _write_cached_notify_backend(backend)
    return True, backend


def send_desktop_notification(title: str, message: str, urgency: str = "normal") -> bool:
    """Send a desktop notification using platform-native methods.

    Args:
        title: Notification title
        message: Notification body text
        urgency: 'normal' or 'critical'

    Returns:
        True when a backend accepted the notification, False otherwise. For a
        fire-and-forget dispatch that means the backend is known-good on this
        host and the process spawned.
    """
    system = platform.system()
    backend = "unsupported"
    dispatched = False
    reason = f"no desktop notification backend for platform {system}"

    try:
        if system == "Darwin":
            # Audio is handled separately by play_audio_macos() via afplay.
            # Omit "sound name" to avoid double sound and to work on macOS 15+
            # where osascript notifications may be silently blocked.
            safe_title = _escape_notification_string(title)
            safe_message = _escape_notification_string(message)
            script = f'display notification "{safe_message}" with title "{safe_title}"'
            subprocess.Popen(
                ["osascript", "-e", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            backend, dispatched = "osascript", True

        elif system == "Linux":
            if platform_shell.is_wsl():
                # WSL: the toast is drawn by the Windows host, so it goes
                # through powershell.exe with PowerShell escaping.
                backend = "notifyicon_wsl"
                dispatched = platform_shell._spawn_powershell(
                    _build_windows_toast_script("notifyicon", title, message, urgency)
                )
                if not dispatched:
                    reason = "powershell.exe dispatch failed"
            else:
                # Native Linux: use notify-send
                if shutil.which("notify-send"):
                    cmd = ["notify-send"]
                    if urgency == "critical":
                        cmd.extend(["-u", "critical"])
                    cmd.extend([title, message])
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    backend, dispatched = "notify-send", True
                else:
                    backend = "none"
                    reason = "notify-send not found"

        elif system == "Windows":
            dispatched, backend = _send_windows_notification(title, message, urgency)
            if not dispatched:
                reason = "every Windows toast backend failed (winrt, burnttoast, notifyicon)"

    except FileNotFoundError as e:
        reason = f"notification command not found: {e}"
    except Exception as e:
        reason = f"desktop notification failed: {e}"
        runner_core.log_error(reason)

    if dispatched:
        runner_core.log_event("info", "desktop_notification", backend=backend,
                  status="DISPATCHED", urgency=urgency)
    else:
        runner_core.log_error_event("NOTIFICATION_FAILED", "desktop_notification",
                        message=reason, backend=backend, urgency=urgency)
    return dispatched
