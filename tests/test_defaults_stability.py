"""Defaults stability test: every existing default value pinned in
config/_defaults_baseline.json must survive in config/default_preferences.json.

New keys in default_preferences.json are allowed.
Removed keys are allowed (deprecation).
Flipped scalar values are FORBIDDEN.
Reordered arrays are allowed (set-equality compare).
Element changes in arrays are FORBIDDEN.
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import json
import unittest
from pathlib import Path
from typing import Any, List, Tuple

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "config" / "default_preferences.json"
BASELINE = REPO / "config" / "_defaults_baseline.json"

METADATA_KEYS = ("_version", "version", "$schema")
COMMENT_PREFIX = "_"


def _walk_for_diff(
    template: Any,
    baseline: Any,
    path: str = "",
    flips: List[Tuple[str, Any, Any]] = None,
) -> List[Tuple[str, Any, Any]]:
    if flips is None:
        flips = []
    if isinstance(baseline, dict):
        if not isinstance(template, dict):
            flips.append((path, baseline, template))
            return flips
        for k, b_val in baseline.items():
            if k in METADATA_KEYS or k.startswith(COMMENT_PREFIX):
                continue
            if k not in template:
                continue  # removed key, allowed
            full = f"{path}.{k}" if path else k
            _walk_for_diff(template[k], b_val, full, flips)
    elif isinstance(baseline, list):
        # Set-equality with scalars; FAIL if elements differ
        if not isinstance(template, list):
            flips.append((path, baseline, template))
            return flips
        if set(map(_hashable, baseline)) != set(map(_hashable, template)):
            flips.append((path, baseline, template))
    else:
        if baseline != template:
            flips.append((path, baseline, template))
    return flips


def _hashable(v):
    if isinstance(v, (list, dict)):
        return json.dumps(v, sort_keys=True)
    return v


class TestDefaultsStability(unittest.TestCase):
    def test_no_existing_default_was_flipped(self):
        template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        flips = _walk_for_diff(template, baseline)
        if flips:
            msg = "Default value flip(s) detected — update _defaults_baseline.json AND CHANGELOG if intentional:\n"
            for path, old, new in flips:
                msg += f"  {path}: {old!r} -> {new!r}\n"
            self.fail(msg)


if __name__ == "__main__":
    unittest.main()
