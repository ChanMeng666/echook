"""Where Codex keeps its files and whether its hooks feature is switched on.

``_codex_home`` resolves ``$CODEX_HOME``; the rest reads ``config.toml`` for
the ``[features].hooks`` state. Read-only: nothing here writes user TOML.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional


def _codex_home() -> Path:
    """Return the Codex home directory honoring ``CODEX_HOME``.

    Defaults to ``~/.codex`` on every platform (Codex's own default per
    developers.openai.com/codex/config-basic).
    """
    return Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"))


def _codex_feature_state_from_text(text: str) -> str:
    """Return Codex hooks feature state from config TOML text.

    Current Codex enables hooks by default. The canonical opt-out is
    ``[features].hooks = false``; the older ``codex_hooks`` alias is still
    recognized for compatibility with existing user configs.
    """
    try:
        import tomllib  # type: ignore
        try:
            data = tomllib.loads(text)
        except Exception:
            return "parse_error"
        features = data.get("features") if isinstance(data, dict) else None
        if not isinstance(features, dict):
            return "enabled_by_default"
        if "hooks" in features:
            if features.get("hooks") is True:
                return "explicitly_enabled"
            if features.get("hooks") is False:
                return "disabled"
            return "parse_error"
        if features.get("codex_hooks") is True:
            return "explicitly_enabled_legacy"
        if features.get("codex_hooks") is False:
            return "disabled_legacy"
        return "enabled_by_default"
    except ImportError:
        in_features = False
        saw_features = False
        hooks_value: Optional[bool] = None
        legacy_value: Optional[bool] = None
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if re.match(r"^\[[^\]]+\]\s*$", stripped):
                in_features = bool(re.match(r"^\[features\]\s*$", stripped, re.IGNORECASE))
                saw_features = saw_features or in_features
                continue
            if not in_features:
                continue
            m = re.match(r"^hooks\s*=\s*(true|false)\b", stripped, re.IGNORECASE)
            if m:
                hooks_value = m.group(1).lower() == "true"
                continue
            m = re.match(r"^codex_hooks\s*=\s*(true|false)\b", stripped, re.IGNORECASE)
            if m:
                legacy_value = m.group(1).lower() == "true"
        if hooks_value is True:
            return "explicitly_enabled"
        if hooks_value is False:
            return "disabled"
        if legacy_value is True:
            return "explicitly_enabled_legacy"
        if legacy_value is False:
            return "disabled_legacy"
        return "enabled_by_default" if saw_features or not saw_features else "enabled_by_default"


def _check_codex_feature_flag(config_path: Path) -> Dict[str, Any]:
    """Inspect ``~/.codex/config.toml`` for Codex's hooks feature state.

    Hooks are enabled by default in current Codex. This helper is read-only:
    it only reports a next step when the user's config explicitly disables
    hooks or cannot be parsed.
    """
    result: Dict[str, Any] = {"config_path": str(config_path)}
    if not config_path.exists():
        result["state"] = "enabled_by_default"
        result["next_step"] = None
        return result
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as e:
        result["state"] = "parse_error"
        result["error"] = str(e)
        result["next_step"] = (
            f"Could not read {config_path}: {e}. Ensure the file is readable; "
            "hooks are enabled by default unless `[features].hooks = false` is set."
        )
        return result

    state = _codex_feature_state_from_text(text)
    result["state"] = state
    if state in ("enabled_by_default", "explicitly_enabled", "explicitly_enabled_legacy"):
        result["next_step"] = None
    elif state in ("disabled", "disabled_legacy"):
        result["next_step"] = (
            f"In {config_path}, remove `[features].hooks = false` or set "
            "`hooks = true` under `[features]` so Codex invokes hooks."
        )
    else:
        result["next_step"] = (
            f"{config_path} has a TOML parse error. Fix it; hooks are enabled by "
            "default unless `[features].hooks = false` is set."
        )
    return result
