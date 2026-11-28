"""CORE-2000-UNIFIED U-7, K21 in a real browser: a link nested inside an autolink must not give a link of the content a `style` that covers
the page of the consents; the buttons of a consent stay on top.

    python3 -m unittest discover -s tests/e2e/unified -p 'unified_markdown_inject_e2e.py'

The evidence directory (`SKILLSYNC_E2E_EVIDENCE`) also gets the screenshots.
"""
from __future__ import annotations

import os
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import APPROVER, APPROVER_PASSWORD, BrowserCase, descend  # noqa: E402
from unified_helpers import PLAN  # noqa: E402

HOST = "e.example"
PAYLOAD = f"<http://{HOST}/[x](https://b.example/style=position:fixed;inset:0;z-index:99999;)>"
# the second way: the mask of the value after `token=` took the end of an attribute with it
MASKED = ('[A](token=y ) B"style=position:fixed;inset:0;z-index:99999;font-size:90vw;opacity:0 **z** [WWWW](https://evil.example/)')


class MarkdownInjectionTests(BrowserCase):
    ACCOUNTS = ((APPROVER, "approver", APPROVER_PASSWORD, "pseudonim-ap"),)

    CONTENT = PAYLOAD

    def test_content_cannot_cover_the_page_of_the_consents(self) -> None:
        self.session_waits_for("CP")
        plan = descend(self.project, PLAN)
        text = plan.read_text(encoding="utf-8")
        self.assertIn("S2a Reguły", text)
        content = self.CONTENT
        plan.write_text(text.replace("S2a Reguły", f"S2a **x** {content} Reguły") + f"\n## Uwagi\n\n{content}\n", encoding="utf-8")
        server = self.serve()
        address = f"http://127.0.0.1:{server.port}"
        found = self.run_script("markdown_inject", {"page": address, "url": address + "/", "signIn": True, "host": HOST, "doc": PLAN,
                                                    "consentWindow": True}, self.start_chrome())
        evidence = os.environ.get("SKILLSYNC_E2E_EVIDENCE")
        if evidence:
            shutil.copytree(descend(self.tmp, f"node-{self.runs}/shots"), Path(evidence) / "shots", dirs_exist_ok=True)
        self.assertEqual(self.rows(), [], "no consent was given")
        self.assert_all_pass(found, ("MD-1", "MD-2", "MD-3"))


class MaskedValueInjectionTests(MarkdownInjectionTests):
    """The same checks with the content that the mask of secrets turned into markup."""

    CONTENT = MASKED


if __name__ == "__main__":
    unittest.main()
