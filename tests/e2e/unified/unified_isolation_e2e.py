"""CORE-2000-UNIFIED U-7, K17, K18 and K20 in a real browser: a script of a prototype cannot read the session of the status page, give a
consent, frame the page or read the password of its login; with `--single-origin` (negative control) the same script reads both.

    python3 -m unittest discover -s tests/e2e/unified -p 'unified_isolation_e2e.py'
"""
from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from harness import APPROVER, APPROVER_PASSWORD, COMMENTER, COMMENTER_PASSWORD, BrowserCase, prototype_origin, write  # noqa: E402
from unified_helpers import RUN_ID  # noqa: E402

SLUG = "demo"
ATTACK_PAGE = '<!doctype html><html lang="pl"><head><meta charset="utf-8"><title>atak</title></head><body><script src="attack.js"></script></body></html>\n'


class IsolationCase(BrowserCase):
    ACCOUNTS = ((APPROVER, "approver", APPROVER_PASSWORD, "pseudonim-ap"), (COMMENTER, "commenter", COMMENTER_PASSWORD, None))

    def setUp(self) -> None:
        super().setUp()
        self.session_waits_for("CP")
        directory = self.init_prototype(SLUG)
        write(directory / "attack.html", ATTACK_PAGE)
        shutil.copyfile(HERE / "attack.js", directory / "attack.js")


class TwoOriginTests(IsolationCase):
    def test_a_prototype_script_cannot_reach_the_session_of_the_page(self) -> None:
        server = self.serve()
        page = server.browser()
        port = page.status()["prototype"]["port"]
        self.assertEqual(page.login().status, 200)
        offer = page.offer("cp")
        self.assertEqual(offer["state"], "waiting", offer)
        delivery = page.status()["delivery"]
        consent = {"kind": "cp", "path": RUN_ID, "sha": offer["sha"], "branch": delivery["branch"], "target": delivery["target"],
                   "planState": offer.get("planState")}
        self.assertEqual(self.rows(), [])
        found = self.run_script("isolation", {"page": f"http://127.0.0.1:{server.port}", "proto": prototype_origin(port), "slug": SLUG,
                                              "consent": consent}, self.start_chrome())
        self.assert_all_pass(found, ("ISO-1", "ISO-2", "ISO-3", "ISO-4", "ISO-5", "ISO-6"))
        # exactly one consent: the one of the page itself (ISO-5), none from the attempts of the prototype
        rows = self.rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual((rows[0]["role"], rows[0]["sha"]), ("approver", consent["sha"]))
        events = [row.get("event") for row in self.audit()]
        self.assertNotIn("handoff_issued", events, "no code of the handover was issued")


class SingleOriginTests(IsolationCase):
    def test_negative_control_one_origin_lets_the_script_read_the_password_and_the_csrf_token(self) -> None:
        server = self.serve("--single-origin")
        found = self.run_script("single_origin", {"page": f"http://127.0.0.1:{server.port}", "slug": SLUG}, self.start_chrome())
        self.assert_all_pass(found, ("SO-1", "SO-2"))
        self.assertEqual(self.rows(), [], "consents from the page are off in this mode")


if __name__ == "__main__":
    unittest.main()
