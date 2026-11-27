"""CORE-2000-UNIFIED U-8, review B (m4, m5, m6, m8): the window of a consent shows hidden characters as their codes, belongs to the account that
opened it, and the second click of a double click never confirms:
python3 -m unittest tests.test_skillsync_unified_consent_window

The REAL page (`app.js` + `dashboard-render.js`) on the shadow DOM of the renderer tests, over real HTTP (the harness of
`test_skillsync_unified_page_session`)."""
from __future__ import annotations

import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_skillsync_unified_page_prototypes as proto  # noqa: E402
import test_skillsync_unified_page_session as page  # noqa: E402  (the modules, so their test classes are not collected here)
from unified_helpers import PLAN  # noqa: E402

CONFIRM = "[data-consent-confirm]"
OPEN = 'button[data-approve="cp"]'
# a bidi override, a zero width space and characters that show nothing outside those categories (a joiner, a filler, two variation selectors)
CODES = (0x202E, 0x200B, 0x034F, 0x3164, 0xFE0F, 0xE0100)
HIDDEN = chr(CODES[0]) + "gnp.exe" + "".join(chr(code) for code in CODES[1:])
SESSION = "/_api/v1/session"
# one more step of the harness: a click event as the browser sends the second click of a double click
SECOND = """    else if (op.op === 'second') { const el = find(op); if (!el) { out.failed = { op, shot: snapshot() }; break; } el.dispatchEvent(new Ev('click', { detail: 2 })); }
    else if (op.op === 'sleep')"""


def second(selector):
    return {"op": "second", "selector": selector}


class ConsentWindowTests(page.PageCase):
    def setUp(self) -> None:
        super().setUp()
        step, refusal = "    else if (op.op === 'sleep')", "ok: false, status: s.status"
        self.assertEqual((page.PAGE.count(step), page.PAGE.count(refusal)), (1, 1))
        # a stubbed answer may also be a success: here the session of another account
        patcher = mock.patch.object(page, "PAGE", page.PAGE.replace(step, SECOND).replace(refusal, "ok: s.status < 300, status: s.status"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def cp(self) -> object:
        self.session_waits_for("CP")
        return self.serve()

    def test_a_hidden_character_is_shown_as_its_code(self) -> None:
        self.session_waits_for("CP")
        plan = page.descend(self.project, PLAN)
        text = plan.read_text(encoding="utf-8")
        title = next(line for line in text.splitlines() if line.startswith("# "))
        plan.write_text(text.replace(title, title + " " + HIDDEN, 1), encoding="utf-8")
        server = self.serve()
        browser = self.signed_in(server)
        out = self.page(server, [page.wait(OPEN), page.click(OPEN), page.wait(".dlg-modal"), page.shot("open")], browser.cookie)
        shown = out["shots"]["open"]["dialog"]["rows"]["doc"]
        self.assertIn("\\u{202E}gnp.exe" + "".join(f"\\u{{{code:X}}}" for code in CODES[1:]), shown)
        for code in CODES:
            self.assertNotIn(chr(code), out["shots"]["open"]["dialog"]["text"])

    def test_the_second_click_of_a_double_click_does_not_confirm(self) -> None:
        server = self.cp()
        browser = self.signed_in(server)
        out = self.page(server, [page.wait(OPEN), page.click(OPEN), page.wait(CONFIRM), second(CONFIRM), {"op": "sleep", "ms": 800}, page.shot("after")],
                        browser.cookie)
        self.assertEqual(self.writes(out, "/api/approve"), [])
        self.assertEqual(out["shots"]["after"]["dialog"]["name"], "consent", "the window stays")
        self.assertEqual(self.rows(), [])
        done = self.page(server, [page.wait(OPEN), page.click(OPEN), page.wait(CONFIRM), page.click(CONFIRM), page.answered("/api/approve", 200)],
                         browser.cookie)
        self.assertEqual(len(self.writes(done, "/api/approve")), 1, "control: one click confirms")

    def test_a_window_does_not_survive_another_account(self) -> None:
        server = self.cp()
        browser = self.signed_in(server)
        reply = browser.get(SESSION)
        self.assertEqual(reply.status, 200, reply.text)
        other = dict(reply.data, user=dict(reply.data["user"], login="other@example.com", name="Other"))
        out = self.page(server, [page.wait(OPEN), page.click(OPEN), page.wait(CONFIRM), page.shot("open"),
                                 {"op": "stub", "path": SESSION, "status_": 200, "body": other}, {"op": "tick", "ms": 30000},
                                 {"op": "gone", "selector": ".dlg-modal"}, page.shot("after")], browser.cookie)
        self.assertEqual(out["shots"]["open"]["dialog"]["name"], "consent")
        self.assertIsNone(out["shots"]["after"]["dialog"])
        self.assertEqual(self.writes(out, "/api/approve"), [])
        self.assertEqual(self.rows(), [])

@unittest.skipUnless(proto.NODE_MAJOR >= 18, "set SKILLSYNC_TEST_NODE (or have node on PATH), node >= 18")
class DeleteThreadWindowTests(proto.ViewCase):
    page = proto.PrototypePageTests.page

    def test_the_window_that_deletes_a_thread_says_how_many_messages_go(self) -> None:
        self.start()
        browser = self.signed(proto.DEVELOPER)
        out = self.page(proto.TAB + proto.manage() + [*proto.act('[data-thread="th-dev"]', "delete"), page.shot("window")], cookie=browser.cookie)
        thread = next(item for item in browser.get("/api/prototypes/demo").data["threads"] if item["id"] == "th-dev")
        self.assertGreaterEqual(thread["messages"], 1)
        self.assertEqual(out["shots"]["window"]["window"]["messages"], str(thread["messages"]))
        self.assertEqual([item for item in out["requests"] if item["method"] == "POST"], [])


if __name__ == "__main__":
    unittest.main()
