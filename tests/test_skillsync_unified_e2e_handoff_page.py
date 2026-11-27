"""CORE-2000-UNIFIED U-7: the referrer policy of the page of the handover on the origin of the prototypes. That page posts the code with a form;
under `Referrer-Policy: no-referrer` a browser sends such a post with `Origin: null`, which the exchange refuses, so no handover worked in a
browser. The page says `same-origin` (the code is in the fragment, which no `Referer` carries); the refusal of `Origin: null` stays:
python3 -m unittest tests.test_skillsync_unified_e2e_handoff_page
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tmp_paths import descend  # noqa: E402
from unified_helpers import COMMENTER, COMMENTER_PASSWORD, Browser, UnifiedCase, http_request, write  # noqa: E402


class HandoffPageTests(UnifiedCase):
    ACCOUNTS = ((COMMENTER, "commenter", COMMENTER_PASSWORD, None),)

    def setUp(self) -> None:
        super().setUp()
        write(descend(self.project, "docs/prototypes/demo/prototype.json"), '{"slug": "demo", "title": "Demo"}')
        write(descend(self.project, "docs/prototypes/demo/index.html"), "<!doctype html><title>Demo</title>\n")
        self.server_ = self.serve()
        self.page = self.server_.browser()
        self.port = self.page.status()["prototype"]["port"]

    def test_the_page_that_posts_the_form_keeps_the_origin_of_its_post(self) -> None:
        reply = http_request("127.0.0.1", self.port, "GET", "/_handoff")
        self.assertEqual(reply.status, 200, reply.text)
        self.assertEqual(reply.header("Referrer-Policy"), "same-origin")
        self.assertIn('<meta name="referrer" content="same-origin">', reply.text)
        self.assertNotIn("no-referrer", reply.text, "a meta tag would override the header")

    def test_a_post_with_an_opaque_origin_is_still_refused_and_the_own_origin_passes(self) -> None:
        self.assertEqual(self.page.login(COMMENTER, COMMENTER_PASSWORD).status, 200)
        proto = Browser(self.port)
        begun = proto.get("/_handoff/begin")
        self.assertEqual(begun.header("Referrer-Policy"), "no-referrer")
        nonce = begun.set_cookie.split(";")[0]
        challenge = begun.header("Location").partition("challenge=")[2]
        code = self.page.post("/_api/v1/handoff", {"challenge": challenge}).data["code"]

        def exchange(origin: str):
            return http_request("127.0.0.1", self.port, "POST", "/_handoff",
                                {"Origin": origin, "Cookie": nonce, "Sec-Fetch-Site": "same-origin",
                                 "Content-Type": "application/x-www-form-urlencoded"}, ("code=" + quote(code, safe="")).encode("ascii"))

        refused = exchange("null")
        self.assertEqual((refused.status, refused.data.get("error")), (403, "invalid_origin"), refused.text)
        accepted = exchange(f"http://127.0.0.1:{self.port}")   # control: the refusal did not burn the code
        self.assertEqual(accepted.status, 303, accepted.text)
        self.assertEqual(accepted.header("Referrer-Policy"), "no-referrer")


if __name__ == "__main__":
    unittest.main()
