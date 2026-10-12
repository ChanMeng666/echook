"""echook hook runner: the one-line text a notification shows or speaks.

Builds the wording for each event from its stdin payload, and sanitises model
or tool text (code blocks, markdown, secrets) before it reaches a toast, a
voice or a webhook. Pure: no I/O, no logging, no sibling imports.
"""

import re
from pathlib import Path
from typing import Dict, List

# Wording per Notification subtype. The table is defined in hook_runner.py
# (with the other catalog tables) and bound here at import; same object.
NOTIFICATION_TYPE_LABELS: Dict[str, str] = {}


def _truncate(s: str, max_len: int = 60) -> str:
    """Truncate a string with ellipsis if too long."""
    return (s[:max_len - 3] + "...") if len(s) > max_len else s


# Secret/token patterns redacted before any text is spoken aloud or shown in a
# desktop toast. Order matters: structured tokens first, then key=value pairs,
# then long opaque blobs (last, so it doesn't swallow ordinary prose).
_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),                       # OpenAI-style
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),    # GitHub tokens
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                          # AWS access key id
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),  # JWT
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),    # bearer header
    re.compile(r"(?i)\b(password|passwd|token|api[_-]?key|secret|access[_-]?key)\b\s*[=:]\s*\S+"),
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),                          # long hex blob
]


def _redact_secrets(s: str) -> str:
    """Replace anything that looks like a credential with [redacted]."""
    for pat in _SECRET_PATTERNS:
        s = pat.sub("[redacted]", s)
    return s


def _clean_for_output(text: str, max_len: int, *, for_speech: bool = False) -> str:
    """Turn raw model/tool text into a safe, readable one-liner.

    Used by both TTS reply-reading and verbose desktop toasts so neither speaks
    nor displays code blocks, markdown syntax, or secrets. Deterministic and
    offline (no LLM/network) — a hook must stay instant.

    Steps: strip fenced/inline code, strip markdown, redact secrets, collapse
    whitespace, then truncate on a sentence/word boundary (not mid-word).
    """
    if not text:
        return ""
    s = str(text)

    # 1. Fenced code blocks -> a short marker (do NOT read code aloud / leak it).
    code_marker = "[code omitted]" if for_speech else "[code]"
    s = re.sub(r"```.*?```", f" {code_marker} ", s, flags=re.DOTALL)
    s = re.sub(r"~~~.*?~~~", f" {code_marker} ", s, flags=re.DOTALL)
    # Inline code -> keep the inner text, drop the backticks.
    s = re.sub(r"`([^`]*)`", r"\1", s)

    # 2. Redact credentials BEFORE markdown stripping — otherwise the bold/italic
    #    pass would eat the underscores in tokens like ghp_… and break the match.
    s = _redact_secrets(s)

    # 3. Markdown syntax.
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", s)          # [text](url) -> text
    if for_speech:
        s = re.sub(r"https?://\S+", "a link", s)             # bare URLs unspeakable
    s = re.sub(r"^\s{0,3}#{1,6}\s*", "", s, flags=re.MULTILINE)  # headers
    s = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", s, flags=re.MULTILINE)  # list markers
    s = re.sub(r"(\*\*|__|\*|_|~~)", "", s)                   # bold/italic/strike

    # 4. Collapse whitespace.
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return ""

    # 5. Boundary truncation.
    if len(s) <= max_len:
        return s
    window = s[:max_len]
    # Prefer the last sentence end within the window.
    m = list(re.finditer(r"[.!?](?:\s|$)", window))
    if m and m[-1].end() >= max_len * 0.5:
        return window[:m[-1].end()].strip()
    # Else cut at the last word boundary.
    cut = window.rsplit(" ", 1)[0].strip() if " " in window else window.strip()
    return cut + "…"


def _get_tool_detail(stdin_data: dict, max_len: int = 60) -> str:
    """Extract a brief detail string from tool_input (command, file_path, etc.)."""
    tool_input = stdin_data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return ""
    # Try common fields in priority order
    for key in ("command", "file_path", "pattern", "query", "url", "prompt"):
        val = tool_input.get(key, "")
        if val:
            # Path-like fields collapse to a basename; everything else is
            # sanitized (secrets redacted, code/markdown stripped) so a verbose
            # toast never dumps a raw command or credential.
            if key == "file_path":
                return Path(str(val)).name
            return _clean_for_output(str(val), max_len)
    return ""


def _format_context_suffix(stdin_data: dict, detail_level: str) -> str:
    """Build a session/worktree/agent suffix appended to every notification.

    Returns ' [session: foo, worktree: bar]' style string when detail_level
    allows. Empty string for 'minimal'.
    """
    if detail_level == "minimal":
        return ""
    parts: List[str] = []
    sn = stdin_data.get("session_name")
    if sn:
        parts.append(f"session: {sn}")
    wt = stdin_data.get("worktree")
    if isinstance(wt, dict):
        wt_label = wt.get("name") or wt.get("branch")
        if wt_label:
            parts.append(f"worktree: {wt_label}")
    agent_name = None
    agent_obj = stdin_data.get("agent")
    if isinstance(agent_obj, dict):
        agent_name = agent_obj.get("name")
    if agent_name:
        parts.append(f"agent: {agent_name}")
    return f" [{', '.join(parts)}]" if parts else ""


def get_notification_context(hook_type: str, stdin_data: dict, detail_level: str = "standard") -> str:
    """Generate human-readable context from hook data.

    detail_level: 'minimal' (hook name only), 'standard' (tool + brief context), 'verbose' (full detail)
    """
    max_len = 40 if detail_level == "standard" else 120 if detail_level == "verbose" else 0

    if hook_type == "stop":
        last_msg = stdin_data.get("last_assistant_message", "")
        if last_msg and detail_level != "minimal":
            summary = _clean_for_output(str(last_msg), max(max_len * 2, 80))
            return f"Task completed: {summary}" if summary else "Task completed"
        return "Task completed"
    elif hook_type == "notification":
        msg = stdin_data.get("message", "")
        nt = stdin_data.get("notification_type", "")
        # The notification_type matcher (v5.0) lets us word the alert correctly
        # without re-parsing the free-text message.
        base = NOTIFICATION_TYPE_LABELS.get(nt)
        if base is None:
            # An unknown subtype used to be worded "Authorization needed",
            # which is a confident lie about an event we do not recognise.
            # Degrade to the type itself instead.
            base = nt.replace("_", " ").capitalize() if nt else "Authorization needed"
        return base + (f": {_truncate(msg, 80)}" if msg else "")
    elif hook_type == "pretooluse":
        tool = stdin_data.get("tool_name", "unknown")
        if detail_level == "minimal":
            return f"Running: {tool}"
        detail = _get_tool_detail(stdin_data, max_len)
        return f"Running {tool}" + (f": {detail}" if detail else "")
    elif hook_type == "posttooluse":
        tool = stdin_data.get("tool_name", "unknown")
        if detail_level == "minimal":
            return f"Completed: {tool}"
        detail = _get_tool_detail(stdin_data, max_len)
        return f"Completed {tool}" + (f": {detail}" if detail else "")
    elif hook_type == "subagent_stop":
        agent = stdin_data.get("agent_type", "")
        last_msg = stdin_data.get("last_assistant_message", "")
        base = "Background task finished" + (f" ({agent})" if agent else "")
        if last_msg and detail_level == "verbose":
            summary = _clean_for_output(str(last_msg), max(max_len * 2, 80))
            if summary:
                base += f": {summary}"
        return base
    elif hook_type == "session_start":
        source = stdin_data.get("source", "")
        return "Session started" + (f" ({source})" if source and detail_level != "minimal" else "")
    elif hook_type == "session_end":
        reason = stdin_data.get("reason", "")
        return "Session ended" + (f" ({reason})" if reason and detail_level != "minimal" else "")
    elif hook_type == "precompact":
        trigger = stdin_data.get("trigger", "")
        return "Compacting context" + (f" ({trigger})" if trigger and detail_level != "minimal" else "")
    elif hook_type == "userpromptsubmit":
        return "Prompt received"
    elif hook_type == "permission_request":
        tool = stdin_data.get("tool_name", "unknown")
        if detail_level == "minimal":
            return f"Permission needed: {tool}"
        detail = _get_tool_detail(stdin_data, max_len)
        base = f"Permission needed: {tool}" + (f" — {detail}" if detail else "")
        suggestions = stdin_data.get("permission_suggestions")
        if isinstance(suggestions, list) and suggestions and detail_level == "verbose":
            base += f" ({len(suggestions)} suggestions)"
        return base
    elif hook_type == "posttoolusefailure":
        tool = stdin_data.get("tool_name", "unknown")
        error = stdin_data.get("error", "")
        if detail_level == "minimal":
            return f"Tool failed: {tool}"
        detail = _get_tool_detail(stdin_data, max_len)
        base = f"{tool} failed"
        if detail:
            base += f": {detail}"
        if error:
            base += f" — {_truncate(error, max_len)}"
        return base
    elif hook_type == "subagent_start":
        agent_type = stdin_data.get("agent_type", "")
        return "Subagent starting" + (f": {agent_type}" if agent_type else "")
    elif hook_type == "teammate_idle":
        teammate = stdin_data.get("teammate_name", "unknown")
        team = stdin_data.get("team_name", "")
        return f"Teammate idle: {teammate}" + (f" ({team})" if team else "")
    elif hook_type == "task_completed":
        subject = stdin_data.get("task_subject", "")
        return "Task completed" + (f": {_truncate(subject, 60)}" if subject else "")
    elif hook_type == "stop_failure":
        # error_type is the v5.0 field; fall back to legacy `error` for older payloads.
        error = stdin_data.get("error_type") or stdin_data.get("error", "unknown")
        details = stdin_data.get("error_message") or stdin_data.get("error_details", "")
        if (
            stdin_data.get("status") == "error"
            and not stdin_data.get("error_type")
            and "error" not in stdin_data
        ):
            # A Cursor ``stop`` re-routed here by _is_cursor_stop_error(): its
            # payload says only that the agent loop ended in an error, so
            # "API error: unknown" would claim more than is known.
            return "Agent stopped with an error" + (f" — {_truncate(details, max_len)}" if details else "")
        return f"API error: {error}" + (f" — {_truncate(details, max_len)}" if details else "")
    elif hook_type == "postcompact":
        trigger = stdin_data.get("trigger", "")
        return "Context compaction complete" + (f" ({trigger})" if trigger else "")
    elif hook_type == "config_change":
        source = stdin_data.get("source", "unknown")
        file_path = stdin_data.get("file_path", "")
        name = Path(file_path).name if file_path and detail_level != "minimal" else ""
        return f"Configuration changed: {source}" + (f" ({name})" if name else "")
    elif hook_type == "instructions_loaded":
        file_path = stdin_data.get("file_path", "")
        reason = stdin_data.get("load_reason", "")
        name = Path(file_path).name if file_path else "unknown"
        return f"Instructions loaded: {name}" + (f" ({reason})" if reason else "")
    elif hook_type == "elicitation":
        server = stdin_data.get("mcp_server_name", "unknown")
        msg = stdin_data.get("message", "")
        return f"Input requested by {server}" + (f": {_truncate(msg, 60)}" if msg else "")
    elif hook_type == "elicitation_result":
        server = stdin_data.get("mcp_server_name", "unknown")
        action = stdin_data.get("action", "")
        return f"Elicitation response: {action}" + (f" ({server})" if server else "")
    # ---- v5.0 hooks ----
    elif hook_type == "permission_denied":
        tool = stdin_data.get("tool_name", "unknown")
        reason = stdin_data.get("reason", "")
        base = f"Permission denied: {tool}"
        if reason and detail_level != "minimal":
            base += f" — {_truncate(str(reason), max_len)}"
        return base
    elif hook_type == "cwd_changed":
        new_cwd = stdin_data.get("new_cwd", "")
        if not new_cwd:
            return "Working directory changed"
        if detail_level == "minimal":
            return "Working directory changed"
        return f"cd {Path(str(new_cwd)).name}"

    elif hook_type == "directory_added":
        # v6.5. Sibling of cwd_changed: /add-dir or the SDK's register_repo_root
        # bringing another root into the session.
        directory = stdin_data.get("directory", "")
        if not directory or detail_level == "minimal":
            return "Directory added"
        return f"Added {Path(str(directory)).name}"

    elif hook_type == "worktree_remove":
        # v6.5. Safe to sound on: unlike WorktreeCreate this is NOT a provider
        # hook — it has no hookSpecificOutput variant and its stdout is
        # discarded. See docs/EVENT_BEHAVIOR_NOTES.md.
        path = stdin_data.get("worktree_path", "")
        if not path or detail_level == "minimal":
            return "Worktree removed"
        return f"Worktree removed: {Path(str(path)).name}"
    elif hook_type == "file_changed":
        fp = stdin_data.get("file_path", "")
        if not fp:
            return "Watched file changed"
        return f"File changed: {Path(str(fp)).name}"
    elif hook_type == "task_created":
        subj = stdin_data.get("task_subject", "")
        teammate = stdin_data.get("teammate_name", "")
        base = "Task created" + (f": {_truncate(str(subj), 60)}" if subj else "")
        if teammate and detail_level != "minimal":
            base += f" → {teammate}"
        return base
    # ---- v6.2 hooks ----
    elif hook_type == "setup":
        # Claude Code Setup hook: trigger is "init" or "maintenance".
        trigger = stdin_data.get("trigger", "")
        return "Environment ready" + (f" ({trigger})" if trigger and detail_level != "minimal" else "")
    elif hook_type == "user_prompt_expansion":
        cmd = stdin_data.get("original_command", "")
        return "Command expanded" + (f": {_truncate(str(cmd), 60)}" if cmd else "")
    elif hook_type == "post_tool_batch":
        tools = stdin_data.get("tools")
        n = len(tools) if isinstance(tools, list) else 0
        return f"Tool batch finished ({n} tools)" if n else "Tool batch finished"
    elif hook_type == "message_display":
        return "Message displayed"
    elif hook_type == "shell_before":
        cmd = stdin_data.get("command", "")
        if detail_level == "minimal" or not cmd:
            return "Shell command starting"
        return f"Shell: {_truncate(str(cmd), max_len)}"
    elif hook_type == "shell_after":
        cmd = stdin_data.get("command", "")
        if detail_level == "minimal" or not cmd:
            return "Shell command finished"
        return f"Shell done: {_truncate(str(cmd), max_len)}"
    elif hook_type == "mcp_before":
        tool = stdin_data.get("tool_name", "")
        return "MCP tool starting" + (f": {tool}" if tool else "")
    elif hook_type == "mcp_after":
        tool = stdin_data.get("tool_name", "")
        return "MCP tool finished" + (f": {tool}" if tool else "")
    elif hook_type == "file_read":
        fp = stdin_data.get("file_path", "")
        if not fp:
            return "Reading file"
        return f"Reading {Path(str(fp)).name}"
    elif hook_type == "agent_response":
        text = stdin_data.get("text", "")
        if text and detail_level != "minimal":
            return f"Response ready: {_clean_for_output(str(text), max(max_len * 2, 80))}"
        return "Response ready"
    elif hook_type == "agent_thinking":
        dur = stdin_data.get("duration_ms", "")
        if dur and detail_level != "minimal":
            try:
                return f"Finished thinking ({int(dur) / 1000:.1f}s)"
            except (TypeError, ValueError):
                pass
        return "Finished thinking"
    elif hook_type == "workspace_open":
        roots = stdin_data.get("workspace_roots")
        if isinstance(roots, list) and roots:
            return f"Workspace opened: {Path(str(roots[0])).name}"
        return "Workspace opened"
    elif hook_type == "tab_file_edit":
        fp = stdin_data.get("file_path", "")
        if not fp:
            return "Inline edit applied"
        return f"Inline edit: {Path(str(fp)).name}"
    return hook_type.replace("_", " ").title()
