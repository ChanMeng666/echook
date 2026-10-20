"""JSON Schema for user_preferences.json, published as `audio-hooks manifest` → `config_schema`.
"""

from __future__ import annotations

from typing import Any, Dict


def _build_manifest_schema() -> Dict[str, Any]:
    """JSON Schema for user_preferences.json."""
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "echook user preferences",
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "audio_theme": {"type": "string", "enum": ["default", "custom"], "default": "default"},
            "enabled_hooks": {
                "type": "object",
                "additionalProperties": {"type": "boolean"},
                "description": "Per-hook enable flags. Keys are hook names from `audio-hooks hooks list`.",
            },
            "playback_settings": {
                "type": "object",
                "properties": {
                    "debounce_ms": {"type": "integer", "minimum": 0},
                },
            },
            "notification_settings": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["audio_only", "notification_only", "audio_and_notification", "disabled"]},
                    "detail_level": {"type": "string", "enum": ["minimal", "standard", "verbose"]},
                    "per_hook": {"type": "object"},
                    "terminal_sequence": {
                        "type": "object",
                        "description": "Claude Code only; inert for async hooks (see `audio-hooks diagnose`).",
                        "properties": {
                            "enabled": {"type": "boolean"},
                            "style": {"type": "string", "enum": ["osc9", "osc777", "title", "bell"]},
                            "hook_types": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
            },
            "filters": {"type": "object", "description": "Per-hook regex filters on stdin fields."},
            "audio_files": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Per-hook sound file override, a path under audio/. Keys are hook names.",
            },
            "webhook_settings": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "url": {"type": "string"},
                    "format": {"type": "string", "enum": ["slack", "discord", "teams", "ntfy", "raw"]},
                    "hook_types": {"type": "array", "items": {"type": "string"}},
                    "headers": {"type": "object"},
                    "include_user_email": {"type": "boolean"},
                },
            },
            "tts_settings": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "speak_assistant_message": {"type": "boolean"},
                    "assistant_message_max_chars": {"type": "integer", "minimum": 10, "maximum": 1000},
                    "messages": {"type": "object"},
                },
            },
            "rate_limit_alerts": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "five_hour_thresholds": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 100}},
                    "seven_day_thresholds": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 100}},
                    "audio": {"type": "string"},
                },
            },
            "statusline_settings": {
                "type": "object",
                "properties": {
                    "visible_segments": {"type": "array", "items": {"type": "string"}},
                    "hidden_segments": {"type": "array", "items": {"type": "string"}},
                    "extra_segments": {"type": "array", "items": {"type": "string"}},
                    "max_width": {"type": "integer", "minimum": 0},
                },
            },
        },
    }
