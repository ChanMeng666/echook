"""Runs last (alphabetical discovery order): reports any test that deleted or
redirected a variable the suite-level isolation depends on.

``tests/_isolation.py`` repairs such damage before the next test, so the suite
stays isolated -- but a test that does it is still a bug, and this names it.
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import unittest


class TestNoOneBrokeTheIsolation(unittest.TestCase):
    def test_no_test_deleted_or_redirected_a_protected_variable(self) -> None:
        self.assertEqual(
            _isolation.REPAIRS, [],
            "these (test, variable) pairs left the variable missing or outside the "
            "isolation tree; restore the old value in teardown instead of popping it",
        )


if __name__ == "__main__":
    unittest.main()
