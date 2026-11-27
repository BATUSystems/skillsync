"""CORE-2000-UNIFIED U-8, review B (m6) in a real browser: the second click of a double click never confirms a consent.

    python3 -m unittest discover -s tests/e2e/unified -p 'unified_consent_window_e2e.py'
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import APPROVER, APPROVER_PASSWORD, BrowserCase, descend  # noqa: E402
from unified_helpers import RUN_ID  # noqa: E402


class ConsentDoubleClickTests(BrowserCase):
    ACCOUNTS = ((APPROVER, "approver", APPROVER_PASSWORD, "pseudonim-ap"),)

    def test_a_double_click_gives_no_consent(self) -> None:
        self.session_waits_for("CP")
        server = self.serve()
        approvals = descend(self.project, f".skillsync/runs/{RUN_ID}/approvals.jsonl")
        found = self.run_script("consent_dblclick", {"page": f"http://127.0.0.1:{server.port}", "approvals": str(approvals)}, self.start_chrome())
        self.assert_all_pass(found, ("DBL-1", "DBL-2"))
        self.assertEqual(len(self.rows()), 1, "the one row is the control click")


if __name__ == "__main__":
    unittest.main()
