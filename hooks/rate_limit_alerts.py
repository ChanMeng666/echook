"""echook hook runner: one-shot warning sound when a rate-limit threshold is crossed.

Reads ``rate_limits`` from the stdin payload; one alert per (window,
threshold, resets_at), remembered by a marker file in the queue directory.
"""

import time
from pathlib import Path
from typing import Any, Dict, List

import runner_core
import audio_playback


def _threshold_values(raw: Any) -> List[Any]:
    """Coerce a configured thresholds value to a list without raising.

    v6.6: ``rate-limits set --five-hour-thresholds 90`` used to store the integer
    90, and ``sorted(90)`` then raised before any audio for every event whose
    payload carried ``rate_limits``. The setter now stores a list; this keeps a
    config written by an older version (or by hand) from crashing the hook.
    """
    if raw is None or isinstance(raw, bool):
        return []
    if isinstance(raw, (int, float)):
        return [raw]
    if isinstance(raw, str):
        return [x.strip() for x in raw.split(",") if x.strip()]
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return []


def check_rate_limits(stdin_data: Dict[str, Any], config: Dict[str, Any]) -> None:
    """Inspect stdin `rate_limits` and play a warning audio when crossing thresholds.

    Side effect only: plays one audio cue per (window, threshold, resets_at)
    tuple, debounced via marker file in the queue dir. Snooze and the user's
    `rate_limit_alerts.enabled` flag both gate this.

    Stdin schema (Claude Code v2.1.80+, Claude.ai subscribers only):
        {
          "rate_limits": {
            "five_hour": {"used_percentage": 78, "resets_at": 1738425600},
            "seven_day": {"used_percentage": 41, "resets_at": 1738857600}
          }
        }
    """
    rl_cfg = config.get("rate_limit_alerts", {}) or {}
    if rl_cfg.get("enabled", True) is False:
        return
    rate_limits = stdin_data.get("rate_limits") if isinstance(stdin_data, dict) else None
    if not isinstance(rate_limits, dict):
        return

    five_thresholds = _threshold_values(rl_cfg.get("five_hour_thresholds", [80, 95]))
    seven_thresholds = _threshold_values(rl_cfg.get("seven_day_thresholds", [80, 95]))
    audio_file_name = rl_cfg.get("audio", "notification-urgent.mp3")

    windows = (("five_hour", five_thresholds), ("seven_day", seven_thresholds))
    for window_name, thresholds in windows:
        window = rate_limits.get(window_name)
        if not isinstance(window, dict):
            continue
        used = window.get("used_percentage")
        resets_at = window.get("resets_at")
        if used is None or resets_at is None:
            continue
        try:
            used_int = int(used)
            resets_int = int(resets_at)
        except (TypeError, ValueError, OverflowError):
            continue
        # Fire only the highest crossed threshold per call. Each marker is
        # keyed on resets_at so a new reset window can re-fire.
        parsed: List[int] = []
        for threshold in thresholds:
            try:
                parsed.append(int(threshold))
            except (TypeError, ValueError, OverflowError):  # OverflowError: 1e999 / inf
                continue
        for t_int in sorted(parsed, reverse=True):
            if used_int < t_int:
                continue
            runner_core.ensure_queue_dir()
            marker = runner_core._prefs().queue_dir / f"rate_limit_{window_name}_{t_int}_{resets_int}"
            if marker.exists():
                break
            try:
                marker.write_text(str(time.time()), encoding="utf-8")
            except OSError:
                pass
            # Resolve audio file across both themes
            theme_cfg = config.get("audio_theme", "default")
            candidates: List[Path] = []
            if theme_cfg == "custom":
                candidates.append(runner_core.AUDIO_DIR / "custom" / ("chime-" + audio_file_name))
                candidates.append(runner_core.AUDIO_DIR / "default" / audio_file_name)
            else:
                candidates.append(runner_core.AUDIO_DIR / "default" / audio_file_name)
                candidates.append(runner_core.AUDIO_DIR / "custom" / ("chime-" + audio_file_name))
            audio_path = next((p for p in candidates if p.exists()), None)
            if audio_path is None:
                runner_core.log_error_event("AUDIO_FILE_MISSING", "rate_limit_alert",
                                message=f"rate-limit alert audio not found: {audio_file_name}")
                break
            try:
                audio_playback.play_audio(audio_path)
            except Exception as e:
                runner_core.log_error_event("AUDIO_PLAY_FAILED", "rate_limit_alert", message=str(e))
                break
            runner_core.log_event("warn", "rate_limit_alert",
                      window=window_name,
                      threshold=t_int,
                      used_percentage=used_int,
                      resets_at=resets_int,
                      audio_file=audio_file_name)
            break  # one alert per call
