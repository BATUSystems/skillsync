"""CORE-2000-UNIFIED U-7, K2, K17 and K20 in a real browser: the tab "Prototypes" of the status page (ANON-6A…F). Without a session: the rows
with the counts of accepted screens and open threads, no button of an action, no e-mail address and no absolute path; "open" in the frame and
in a new tab; the frame is sandboxed, cannot reach the page and cannot navigate it (control: the same frame without the sandbox can).
Signed in: the acceptances and the threads on the page, and "sign in to the prototypes", which ends in the gallery of their origin.

    python3 -m unittest discover -s tests/e2e/unified -p 'unified_prototype_tab_e2e.py'
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import APPROVER, APPROVER_PASSWORD, COMMENTER, COMMENTER_PASSWORD, BrowserCase, prototype_origin  # noqa: E402

SLUG = "demo"
APPROVER_NAME = "pseudonim-kanarek-akceptujacy"
TITLE_MAIL = "kanarek.tytul@example.com"
TITLE_PATH = "/srv/kanarek/z-tytulu.txt"
SCREENS = "".join(f'''
  <section class="screen" id="s{n}" aria-labelledby="s{n}-h">
    <header class="screen-meta"><h2 id="s{n}-h">{n}. Ekran</h2><p>Opis</p></header>
    <div class="frame"><button type="button" data-proto-id="s{n}-ok">Dalej</button></div>
  </section>''' for n in (2, 3))


class PrototypeTabTests(BrowserCase):
    ACCOUNTS = ((APPROVER, "approver,acceptor", APPROVER_PASSWORD, APPROVER_NAME), (COMMENTER, "commenter", COMMENTER_PASSWORD, None))

    def test_the_tab_of_the_prototypes_and_its_frame(self) -> None:
        self.session_waits_for("CP")
        directory = self.init_prototype(SLUG, title=f"KAN-title- {TITLE_MAIL} {TITLE_PATH}")
        self.add_screens(directory, SCREENS)
        server = self.serve()
        page = server.browser()
        port = page.status()["prototype"]["port"]
        author = server.browser()
        self.assertEqual(author.login(COMMENTER, COMMENTER_PASSWORD).status, 200)
        proto = self.handed_over(author, port)
        route = f"/_api/v1/prototypes/{SLUG}/operations"
        operations = proto.get(route + "?after=0")
        self.assertEqual(operations.status, 200, operations.text)
        iteration = operations.data.get("iteration")
        iteration = iteration.get("current", 1) if isinstance(iteration, dict) else iteration or 1
        headers = {**proto.base_headers(), "Content-Type": "application/json", "Origin": prototype_origin(port), "X-Skillsync-CSRF": proto.csrf}
        created = proto.post(route, {"cid": "c-tab-create-1", "type": "create", "threadId": "th-tab-thread-1", "payload": {
            "screen": "s3", "anchor": {"kind": "element", "protoId": "s3-ok", "x": 0.5, "y": 0.5, "iteration": iteration},
            "message": {"id": "m-tab-message-1", "text": "KAN-comment- uwaga do ekranu"}}}, headers)
        self.assertEqual(created.status, 201, created.text)
        # one screen of the two accepted, on the page, with the hash and the version the page shows
        self.assertEqual(page.login().status, 200)
        detail = page.get(f"/api/prototypes/{SLUG}")
        self.assertEqual(detail.status, 200, detail.text)
        hashes = {row["id"]: row["hash"] for row in detail.data["screens"]}
        accepted = page.post(f"/api/prototypes/{SLUG}/accept", {"cid": "c-tab-accept-1", "screens": {"s2": hashes["s2"]},
                                                              "version": detail.data["version"]})
        self.assertEqual(accepted.status, 200, accepted.text)
        row, = server.browser().status()["prototypes"]
        self.assertEqual((row["screens"], row["accepted"], row["threads"].get("open", 0) + row["threads"].get("awaiting", 0)), (2, 1, 1), row)
        comments = (directory / "comments.json").read_bytes()
        events_before = [item.get("event") for item in self.audit()]

        people = {"author-login": COMMENTER, "approver-login": APPROVER, "project-path": str(self.project), "temporary": str(self.tmp)}
        found = self.run_script("prototype_tab", {
            "page": f"http://127.0.0.1:{server.port}", "proto": prototype_origin(port), "slug": SLUG, "people": people,
            "title": {"title-mail": TITLE_MAIL, "title-path": TITLE_PATH}, "pseudonym": APPROVER_NAME,
            "facts": ["1 z 2 ekranów zaakceptowanych", "1 wątek otwarty"]}, self.start_chrome())
        self.assert_all_pass(found, tuple(f"ANON-6{letter}" for letter in "ABCDEF"))

        # nothing was written by the page, the frame or the new tabs: no consent, no operation; one handover, the one of ANON-6F
        self.assertEqual(self.rows(), [])
        self.assertEqual((directory / "comments.json").read_bytes(), comments)
        events = [item.get("event") for item in self.audit()][len(events_before):]
        self.assertEqual((events.count("handoff_issued"), events.count("handoff_redeemed")), (1, 1), events)


if __name__ == "__main__":
    unittest.main()
