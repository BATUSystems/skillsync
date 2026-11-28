"""CORE-2000-UNIFIED U-7, K18 in a real browser: the handover of a session of the status page to the origin of the prototypes, its cookies
and its refusals (a used code, a code in a foreign link, the sign-out).

    python3 -m unittest discover -s tests/e2e/unified -p 'unified_handoff_e2e.py'
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import APPROVER, APPROVER_PASSWORD, COMMENTER, COMMENTER_PASSWORD, BrowserCase, prototype_origin  # noqa: E402

SLUG = "demo"


class HandoffTests(BrowserCase):
    # the account with every role a handed over session must lose
    ACCOUNTS = ((APPROVER, "approver,acceptor,developer,commenter", APPROVER_PASSWORD, "pseudonim-ap"),
                (COMMENTER, "commenter", COMMENTER_PASSWORD, None))

    def test_the_handover_in_a_browser(self) -> None:
        self.init_prototype(SLUG)
        server = self.serve()
        port = server.browser().status()["prototype"]["port"]
        found = self.run_script("handoff", {"page": f"http://127.0.0.1:{server.port}", "proto": prototype_origin(port), "slug": SLUG},
                                self.start_chrome())
        self.assert_all_pass(found, ("HO-1", "HO-2", "HO-3", "HO-4", "HO-5", "HO-6"))
        audit = self.audit()
        events = [row.get("event") for row in audit]
        # the audit keeps the accounts with `approver` only: the one handover of HO-1; the codes of the attacker (a commenter) are not in it
        self.assertEqual((events.count("handoff_issued"), events.count("handoff_redeemed")), (1, 1), events)
        # no code and no password in the audit or in the log of the server
        text = json.dumps(audit) + server.log()
        self.assertNotIn("code=", text)
        self.assertNotIn(APPROVER_PASSWORD, text)
        self.assertNotIn(COMMENTER_PASSWORD, text)


if __name__ == "__main__":
    unittest.main()
