"""echook hook runner: fire-and-forget webhook delivery.

Formats the payload for Slack, Discord, Teams, ntfy or the raw
``audio-hooks.webhook.v1`` schema and posts it from a detached subprocess.
"""

import json
import os
import subprocess
import sys
import time
from typing import Any, Dict

import runner_core


def send_webhook(hook_type: str, context: str, stdin_data: dict, config: Dict[str, Any]) -> None:
    """Send hook event to a configured webhook URL (Slack, Discord, Teams, ntfy, or custom).

    v5.0: fire-and-forget via subprocess so the parent process can exit
    immediately even on slow webhooks. Raw payloads carry the
    `audio-hooks.webhook.v1` schema with all enriched stdin fields surfaced
    as top-level keys for downstream consumers to pin.
    """
    webhook = config.get("webhook_settings", {})
    if not webhook.get("enabled"):
        return

    url = webhook.get("url", "")
    if not url:
        return

    # Check if this hook type should trigger webhook
    allowed = webhook.get("hook_types", [])
    if allowed and hook_type not in allowed:
        return

    fmt = webhook.get("format", "raw")
    headers: Dict[str, str] = {str(k): str(v) for k, v in (webhook.get("headers") or {}).items()}
    display_name = runner_core._invoker_display_name()

    # Format payload based on target service
    if fmt == "slack":
        payload: Any = {"text": f"\U0001f514 {display_name}: {context}"}
    elif fmt == "discord":
        payload = {"content": f"\U0001f514 {display_name}: {context}"}
    elif fmt == "teams":
        payload = {"text": f"\U0001f514 {display_name}: {context}"}
    elif fmt == "ntfy":
        # ntfy.sh uses plain text body with header-based metadata
        headers.setdefault("Title", display_name)
        headers.setdefault("Priority", "default")
        headers.setdefault("Tags", "robot")
        payload = context  # plain text
    elif fmt == "raw":
        # v5.0 enriched schema: surface every new stdin field at the top level
        # and tag with a versioned schema string so consumers can pin.
        # v5.1.4: add invoker + cursor.* sub-object for cross-IDE consumers.
        # user_email is redacted from event_data by default for privacy; opt
        # in via webhook_settings.include_user_email = true if needed.
        include_email = bool(webhook.get("include_user_email"))
        cursor_specific = {
            k: stdin_data.get(k)
            for k in (
                "cursor_version", "conversation_id", "generation_id",
                "reason", "final_status", "duration_ms",
                "is_background_agent", "workspace_roots", "model",
                "error_message",
            )
            if stdin_data.get(k) is not None
        }
        if include_email and "user_email" in stdin_data:
            cursor_specific["user_email"] = stdin_data["user_email"]
        # Codex stdin (per developers.openai.com/codex/hooks) carries the same
        # snake_case shape as Claude Code, plus a few Codex-specifics. Surface
        # them in their own sub-object so downstream consumers can branch on
        # `invoker == "codex"` cleanly.
        codex_specific = {
            k: stdin_data.get(k)
            for k in (
                "turn_id", "tool_use_id", "permission_mode", "tool_response",
                "stop_hook_active",
            )
            if stdin_data.get(k) is not None
        }
        sanitized_event = {
            k: v for k, v in stdin_data.items()
            if k not in ("transcript_path",)
            and (include_email or k != "user_email")
        }
        payload = {
            "schema": "audio-hooks.webhook.v1",
            "version": runner_core.RUNNER_VERSION,
            "invoker": runner_core._get_invoker(),
            "hook_type": hook_type,
            "context": context,
            "timestamp": time.time(),
            "session_id": stdin_data.get("session_id"),
            "session_name": stdin_data.get("session_name"),
            "worktree": stdin_data.get("worktree"),
            "agent_id": stdin_data.get("agent_id"),
            "agent_type": stdin_data.get("agent_type"),
            "agent": stdin_data.get("agent"),
            "rate_limits": stdin_data.get("rate_limits"),
            "last_assistant_message": stdin_data.get("last_assistant_message"),
            "notification_type": stdin_data.get("notification_type"),
            "error_type": stdin_data.get("error_type"),
            "source": stdin_data.get("source"),
            "trigger": stdin_data.get("trigger"),
            "load_reason": stdin_data.get("load_reason"),
            "permission_suggestions": stdin_data.get("permission_suggestions"),
            "tool_name": stdin_data.get("tool_name"),
            "tool_input": stdin_data.get("tool_input"),
            "cursor": cursor_specific or None,
            "codex": codex_specific or None,
            "event_data": sanitized_event,
        }
    else:
        payload = {"text": f"{display_name}: {context}"}

    if isinstance(payload, str):
        body_bytes = payload.encode("utf-8")
        headers.setdefault("Content-Type", "text/plain; charset=utf-8")
    else:
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    # Fire-and-forget: spawn a tiny Python subprocess that does the urlopen
    # and exits. The parent can exit immediately. Survives main process exit
    # because the child is fully detached.
    sender = (
        "import sys, json, urllib.request, urllib.error\n"
        "url = sys.argv[1]\n"
        "headers = json.loads(sys.argv[2])\n"
        "timeout = float(sys.argv[3])\n"
        "data = sys.stdin.buffer.read()\n"
        "try:\n"
        "    req = urllib.request.Request(url, data=data, headers=headers, method='POST')\n"
        "    urllib.request.urlopen(req, timeout=timeout)\n"
        "except Exception:\n"
        "    pass\n"
    )
    try:
        creation_flags = 0
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            creation_flags = subprocess.CREATE_NO_WINDOW
        proc = subprocess.Popen(
            [sys.executable, "-c", sender, url, json.dumps(headers), "5"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        if proc.stdin is not None:
            proc.stdin.write(body_bytes)
            proc.stdin.close()
        runner_core.log_event("info", "webhook_dispatched", hook=hook_type, format=fmt)
    except Exception as e:
        runner_core.log_error_event("WEBHOOK_HTTP_ERROR", "webhook_dispatch", message=str(e), hook=hook_type)
