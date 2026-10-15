"""Error codes only the CLI emits, merged into `audio-hooks manifest` → `error_codes`. Pure data.
"""

from __future__ import annotations

from typing import Dict

# v6.7: codes the CLI emits that hook_runner.ErrorCode does not define -- the
# runner's catalogue covers what a *hook* can report, so the manifest used to
# omit INVALID_USAGE, DUPLICATE_BRIDGE, UNINSTALL_INCOMPLETE and the rest, which
# CLAUDE.md names as the place an agent looks codes up. _build_manifest() merges
# this over HR.ErrorCode. "appears_in" says where the code shows up: "error"
# (the {"ok": false, "error": {"code": ...}} a command returns) or "diagnose"
# (an entry in `audio-hooks diagnose` errors/warnings).
# tests/test_cli_error_codes.py scans the entry file and every module of this
# package for each code the CLI can emit and
# fails when one is in neither this table nor HR.ErrorCode.
CLI_ERROR_CODES: Dict[str, Dict[str, str]] = {
    "INVALID_USAGE": {
        "appears_in": "error",
        "hint": "The command was called with an unknown flag, a missing value, a stray argument or a help-like value. Nothing was changed.",
        "suggested_command": "audio-hooks manifest",
    },
    "DUAL_INSTALL_DETECTED": {
        "appears_in": "diagnose",
        "hint": "A script install made before 7.0.0 and the Claude Code plugin are both active, so every hook fires twice. Reported by diagnose and as status.install.warning; `audio-hooks uninstall` removes the script install.",
        "suggested_command": "audio-hooks uninstall",
    },
    "SCRIPT_INSTALL_REMOVED": {
        "appears_in": "error",
        "hint": "`install --scripts` installs nothing: the script installer was removed in 7.0.0. Use the plugin. An existing script install keeps working and `audio-hooks uninstall` removes it.",
        "suggested_command": "audio-hooks install --plugin",
    },
    "DUPLICATE_BRIDGE": {
        "appears_in": "error, diagnose",
        "hint": "Cursor receives echook both through Claude Code's auto-bridge and through a native ~/.cursor/hooks.json, so events fire twice. `install --cursor` refuses unless --force.",
        "suggested_command": "audio-hooks uninstall --cursor",
    },
    "CURSOR_NOT_FOUND": {
        "appears_in": "error",
        "hint": "~/.cursor/ does not exist, so there is nothing to install the native Cursor hooks into. Install Cursor first.",
        "suggested_command": "audio-hooks status",
    },
    "UNINSTALL_INCOMPLETE": {
        "appears_in": "error",
        "hint": "Uninstall removed what it could but left entries it did not recognise or could not delete; the response lists them. Read unmatched_references before --remove-unmatched.",
        "suggested_command": "audio-hooks uninstall",
    },
    "BACKUP_NOT_FOUND": {
        "appears_in": "error",
        "hint": "No preferences backup has that id.",
        "suggested_command": "audio-hooks backup list",
    },
    "RESTORE_FAILED": {
        "appears_in": "error",
        "hint": "The chosen backup could not be read or restored.",
        "suggested_command": "audio-hooks backup list",
    },
    "NOT_INSTALLED": {
        "appears_in": "error",
        "hint": "upgrade found the audio-hooks plugin in no scope; there is nothing to upgrade.",
        "suggested_command": "audio-hooks install --plugin",
    },
    "PRIOR_UPGRADE_INCOMPLETE": {
        "appears_in": "error",
        "hint": "A previous `upgrade` left its in-progress marker behind; the response carries it. Check state, then retry with --force.",
        "suggested_command": "audio-hooks status",
    },
    "UPGRADE_UNINSTALL_FAILED": {
        "appears_in": "error",
        "hint": "The `claude plugin uninstall` step of upgrade failed; the installed plugin was not replaced.",
        "suggested_command": "audio-hooks upgrade --check-only",
    },
    "UPGRADE_REINSTALL_FAILED": {
        "appears_in": "error",
        "hint": "The `claude plugin install` step of upgrade failed after the uninstall, so the plugin may be absent now. Run the suggested install command.",
        "suggested_command": "audio-hooks install --plugin",
    },
    "UPGRADE_VERIFY_FAILED": {
        "appears_in": "error",
        "hint": "The upgrade ran but the final `claude plugin list` check failed; it may well have completed.",
        "suggested_command": "audio-hooks upgrade --check-only",
    },
    "HOOKS_NOT_REGISTERED": {
        "appears_in": "diagnose",
        "hint": "No hooks block in ~/.claude/settings.json and no plugin install found, so nothing triggers echook.",
        "suggested_command": "audio-hooks install --plugin",
    },
    "NATIVE_NOTIFICATIONS_ACTIVE": {
        "appears_in": "diagnose",
        "hint": "Claude Code's own notification channel is on and signals the same events, so expect a doubled bell or toast. Turn one of the two off.",
        "suggested_command": "audio-hooks diagnose",
    },
    "NO_COMPLETION_SIGNAL": {
        "appears_in": "diagnose",
        "hint": "None of stop, subagent_stop or notification is enabled, so no hook can say a turn finished. echook is healthy but silent for that.",
        "suggested_command": "audio-hooks hooks enable stop",
    },
    "TERMINAL_SEQUENCE_INERT": {
        "appears_in": "diagnose",
        "hint": "notification_settings.terminal_sequence is enabled but Claude Code never emits it from an async hook. Use notification_settings.mode audio_and_notification for a desktop toast.",
        "suggested_command": "audio-hooks set notification_settings.mode audio_and_notification",
    },
    "PREFS_SCHEMA_STALE": {
        "appears_in": "diagnose",
        "hint": "user_preferences.json is stamped with an older version or carries keys this version removed. The next hook event or any state-changing command migrates it; read-only commands (status, diagnose, get) do not. `audio-hooks migrate` does it on demand.",
        "suggested_command": "audio-hooks migrate",
    },
    "STALE_PLUGIN_CACHE": {
        "appears_in": "diagnose",
        "hint": "installed_plugins.json records a version or install path that is not the code now running.",
        "suggested_command": "audio-hooks upgrade",
    },
    "WINDOWS_NO_GIT_BASH": {
        "appears_in": "diagnose",
        "hint": "Windows without Git Bash on PATH: Claude Code runs command hooks through bash and refuses them when it is missing, so every handler fails.",
        "suggested_command": "audio-hooks diagnose",
    },
    "CODEX_HOOKS_DISABLED": {
        "appears_in": "diagnose",
        "hint": "Codex hooks are installed but [features].hooks is false in config.toml, so Codex invokes none of them.",
        "suggested_command": "audio-hooks install --codex",
    },
    "CODEX_CONFIG_PARSE_ERROR": {
        "appears_in": "diagnose",
        "hint": "Codex hooks are installed but config.toml could not be read or parsed. Fix the TOML; hooks are on unless [features].hooks = false.",
        "suggested_command": "audio-hooks diagnose",
    },
    "CODEX_MANAGED_HOOKS_ONLY": {
        "appears_in": "diagnose",
        "hint": "Codex allows managed hooks only, so $CODEX_HOME/hooks.json is ignored and a native --codex install never fires. Ask whoever owns the Codex policy.",
        "suggested_command": "audio-hooks diagnose",
    },
    "CODEX_HOOKS_FILE_REJECTED": {
        "appears_in": "diagnose",
        "hint": "$CODEX_HOME/hooks.json has a top-level key other than description and hooks, so Codex rejects the whole file and none of its hooks run. install --codex up to 7.0.0 wrote such keys; re-running it removes them.",
        "suggested_command": "audio-hooks install --codex",
    },
}
