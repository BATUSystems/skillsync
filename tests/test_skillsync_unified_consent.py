"""CORE-2000-UNIFIED end to end (U-1 accounts and sessions, U-2 consents by session), criteria K1, K3, K4, K5, K8, K13:
python3 -m unittest tests.test_skillsync_unified_consent

A real `skillsync dashboard serve` process over a temporary project, accounts made by the real `skillsync prototype user add`, requests over HTTP,
consents read back by the real `skillsync approvals wait` / `skillsync status`. Every refusal is followed by the same request done right, which
succeeds: the proof that the negative assertion could fail. See `unified_helpers` for the one thing a test moves (the port of the default mode).
Not covered here: the origin of the prototypes, the interface of the page and the acceptance of screens (not built yet), the clock based expiry of
a session (unit tests with a fake clock), XSS in a browser."""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from unified_helpers import (  # noqa: E402
    ACCEPTOR, APPROVER, APPROVER_ACCOUNT, APPROVER_NEW_PASSWORD, APPROVER_PASSWORD, BRANCH, COMMENTER, COMMENTER_PASSWORD, DONE_PLAN, HEAD, HEAD_NEXT,
    LAYER, PLAN, PLAN2, RUN2_ID, RUN_ID, SPEC, SPEC2, UnifiedCase, descend, http_request)

FAKE_TOKEN = "-".join(("legacy", "consent", "token", "0123456789"))   # what a client of 1.x still sends in `X-Approve-Token`
ACCOUNTS = ((APPROVER, "approver", APPROVER_PASSWORD, APPROVER_ACCOUNT),
            (COMMENTER, "commenter", COMMENTER_PASSWORD, None),
            (ACCEPTOR, "acceptor,developer,expert", COMMENTER_PASSWORD, None))


class ConsentCase(UnifiedCase):
    ACCOUNTS = ACCOUNTS

    def signed_in(self, server, login: str = APPROVER, password: str = APPROVER_PASSWORD):
        browser = server.browser()
        reply = browser.login(login, password)
        self.assertEqual(reply.status, 200, reply.text)
        return browser

    def go_plan_body(self, browser) -> dict:
        offer = browser.offer("spec")
        self.assertEqual((offer["state"], offer["path"], offer["sha"]), ("waiting", SPEC2, self.blob_sha(SPEC2)))
        return {"kind": "spec", "path": SPEC2, "sha": offer["sha"]}

    def delivery_body(self, browser, kind: str = "cp") -> dict:
        offer = browser.offer(kind)
        self.assertEqual((offer["state"], offer["path"]), ("waiting", RUN_ID), offer)
        delivery = browser.status()["delivery"]
        return {"kind": kind, "path": RUN_ID, "sha": offer["sha"], "branch": delivery["branch"], "target": delivery["target"], "mode": delivery["mode"],
                "planState": offer.get("planState")}

    def wait(self, word: str, *extra: str):
        done = self.cli("approvals", "wait", str(self.project), "--word", word, "--timeout", "0", *extra, *LAYER)
        return done.returncode, json.loads(done.stdout)

    def assert_refused(self, reply, status: int, code: str) -> None:
        """The one shape of a refusal: `{error, code}` and nothing else, in particular no account and no role."""
        self.assertEqual((reply.status, reply.data.get("code")), (status, code), reply.text)
        self.assertEqual(sorted(reply.data), ["code", "error"])
        for secret in (APPROVER, APPROVER_ACCOUNT, APPROVER_PASSWORD, FAKE_TOKEN):
            self.assertNotIn(secret, reply.text)


class CliAccountTests(UnifiedCase):
    """K16 on the way to every other scenario: how the first account with `approver` comes to be."""

    def users_file(self) -> Path:
        return self.project / ".skillsync" / "prototype" / "users.json"

    def test_approver_needs_confirmation_terminal_and_a_long_password(self) -> None:
        base = ["prototype", "user", "add", str(self.project), APPROVER, "--roles", "approver"]
        piped = self.cli_with_stdin(APPROVER_PASSWORD, *base, "--password-stdin", "--confirm-mixed-fleet", *LAYER)
        self.assertEqual(piped.returncode, 2, piped.stdout + piped.stderr)
        code, out, shown = self.cli_on_terminal(APPROVER_PASSWORD, *base, "--password-prompt", *LAYER)
        self.assertEqual(code, 2, out + shown)
        self.assertIn("confirm_required", out)
        short = APPROVER_PASSWORD[:13]
        code, out, shown = self.user_add_approver(self.project, APPROVER, short)
        self.assertEqual(code, 2, out + shown)
        self.assertIn("14", out)
        self.assertFalse(self.users_file().exists(), "a refused command created the users file")
        # the control of the three refusals: 14 characters, a terminal and the confirmation
        code, out, shown = self.user_add_approver(self.project, APPROVER, APPROVER_PASSWORD[:14], account=APPROVER_ACCOUNT)
        self.assertEqual(code, 0, out + shown)
        stored = self.users_file().read_text(encoding="utf-8")
        self.assertIn('"approver"', stored)
        self.assertIn(APPROVER_ACCOUNT, stored)
        for text in (stored, out, shown):
            self.assertNotIn(APPROVER_PASSWORD[:14], text)   # neither stored, nor reported, nor echoed on the terminal
        if os.name != "nt":   # Windows has no file modes: the access is limited by the ACL of the file
            self.assertEqual(self.users_file().stat().st_mode & 0o777, 0o600)


class AnonymousReadTests(ConsentCase):
    """K1 and K5: everybody reads, nobody writes without a session."""

    READS = ("/", "/index.html", "/assets/app.js", "/api/status", "/api/tabs", "/api/history", f"/api/doc?path={SPEC}", f"/api/doc?path={PLAN}",
             "/api/descriptions", "/healthz", "/_api/v1/session", "/_login")

    def setUp(self) -> None:
        super().setUp()
        self.spec_waits_for_go_plan()

    def test_every_read_of_the_one_socket_works_without_a_session(self) -> None:
        server = self.serve()
        anonymous = server.browser()
        for path in self.READS:
            with self.subTest(path=path):
                reply = anonymous.get(path)
                self.assertEqual(reply.status, 200, reply.text[:300])
                self.assertEqual(reply.set_cookie, "", "an anonymous read must not start a session")
        session = anonymous.get("/_api/v1/session").data
        self.assertEqual((session["user"], session["csrf"]), (None, None))
        self.assertEqual((session["server"]["connection"], session["server"]["loginAllowed"], session["server"]["approverLogin"]),
                         ("local", True, True))
        status = anonymous.status()
        self.assertNotIn("account", status.get("session") or {})
        self.assertFalse(status["approvals"]["tokenRequired"])
        self.assertEqual(status["delivery"]["mode"], "direct")
        events = anonymous.get("/events")
        self.assertEqual((events.status, events.header("Content-Type")), (200, "text/event-stream; charset=utf-8"))

    def test_no_post_without_a_session_writes_anything(self) -> None:
        server = self.serve()
        anonymous = server.browser()
        approver = self.signed_in(server)
        tab = anonymous.get("/api/tabs").data["current"]
        bodies = {
            "/api/approve": self.go_plan_body(anonymous),
            "/api/plan-status": {"tab": tab, "path": DONE_PLAN, "sha": self.blob_sha(DONE_PLAN), "action": "archive"},
            "/api/feedback-approve": {"draft": "2026-01-10-kanarek", "sha": "a" * 64},
        }
        before = self.snapshot()
        stale = approver.cookie.split("=", 1)[0] + "=" + "0" * 43
        attempts = (("no credentials", {}, "login_required"),
                    ("the consent token of 1.x", {"X_Approve_Token": FAKE_TOKEN}, "login_required"),
                    ("a forged session cookie", {"Cookie": stale, "X_Skillsync_CSRF": "x" * 43}, "session_expired"),
                    ("the CSRF token of somebody without the cookie", {"X_Skillsync_CSRF": approver.csrf}, "login_required"))
        for path, body in bodies.items():
            for label, override, code in attempts:
                with self.subTest(path=path, attempt=label):
                    self.assert_refused(anonymous.post(path, body, anonymous.post_headers(**override)), 401, code)
        for method in ("PUT", "PATCH", "DELETE", "OPTIONS"):
            with self.subTest(method=method):
                reply = http_request("127.0.0.1", server.port, method, "/api/approve", anonymous.post_headers(), bodies["/api/approve"])
                self.assertGreaterEqual(reply.status, 400, reply.text)
                self.assertIsNone(reply.header("Access-Control-Allow-Origin"))
        for path in ("/api/status", "/api/doc", "/healthz", "/nowhere"):
            self.assertEqual(anonymous.post(path, {}).status, 404, path)
        self.assertEqual(self.snapshot(), before)
        refused = [row for row in self.audit() if row["event"] == "consent_refused"]
        self.assertTrue(refused)
        self.assertNotIn(FAKE_TOKEN, json.dumps(self.audit()))
        # the control: the same two bodies from a signed-in approver DO write, so the snapshot above could have changed
        self.assertEqual(approver.post("/api/approve", bodies["/api/approve"]).status, 200)
        archived = approver.post("/api/plan-status", bodies["/api/plan-status"])
        self.assertEqual(archived.status, 200, archived.text)
        self.assertNotEqual(self.snapshot(), before)

    def test_read_socket_of_the_two_socket_mode_has_no_session_and_no_write(self) -> None:
        server = self.serve(mode="two")
        read_port, approve_port = server.ports
        approver = self.signed_in(server)
        body = self.go_plan_body(approver)
        on_read = approver.clone()
        on_read.port, on_read.host = read_port, f"127.0.0.1:{read_port}"
        self.assertEqual(on_read.get("/api/status").status, 200)
        self.assertNotIn("account", on_read.status().get("session") or {})
        for path in ("/_login", "/_api/v1/session"):
            self.assertEqual(on_read.get(path).status, 404, path)
        before = self.snapshot()
        for path in ("/_api/v1/login", "/api/approve", "/api/plan-status", "/api/feedback-approve"):
            self.assertEqual(on_read.post(path, body).status, 404, path)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(approver.post("/api/approve", body).status, 200)   # the control, on the socket of the consents
        self.assertEqual([row["word"] for row in self.rows(RUN2_ID)], ["GO plan"])


class ConsentFlowTests(ConsentCase):
    """K3 (sequence), K4 (the trail and its readers), K13 (the cookie, the race)."""

    def test_go_plan_then_go_implement_are_recorded_and_awaited_by_the_cli(self) -> None:
        self.spec_waits_for_go_plan()
        server = self.serve()
        browser = server.browser()
        reply = browser.login()
        self.assertEqual(reply.status, 200, reply.text)
        attributes = [item.strip() for item in reply.set_cookie.split(";")]
        self.assertRegex(attributes[0], r"^skillsync_page_[0-9a-f]{8}=[0-9a-f]{8}\.[A-Za-z0-9_-]{43}$")
        self.assertEqual(attributes[1:], ["HttpOnly", "SameSite=Strict", "Path=/", f"Max-Age={12 * 3600}"])   # local HTTP: no `Secure`
        self.assertEqual(reply.header("Cache-Control").split(",")[0], "no-cache")
        self.assertNotIn(browser.token, reply.text)
        self.assertIn("approver", browser.status()["session"]["roles"])

        body = self.go_plan_body(browser)
        shown_sha = body["sha"]
        self.assertEqual(self.wait("GO plan", "--run", RUN2_ID)[0], 3)   # nothing to read yet: the later PASS is the consent's doing
        given = browser.post("/api/approve", body)
        self.assertEqual((given.status, given.data.get("word")), (200, "GO plan"), given.text)
        self.assertIn("Status: approved", descend(self.project, SPEC2).read_text(encoding="utf-8"))
        rows = self.rows(RUN2_ID)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual({key: row[key] for key in ("schema", "word", "sha", "source", "by", "role", "session", "file", "shaAfter", "run")},
                         {"schema": 1, "word": "GO plan", "sha": shown_sha, "source": "dashboard", "by": APPROVER_ACCOUNT, "role": "approver",
                          "session": browser.digest, "file": SPEC2, "shaAfter": self.blob_sha(SPEC2), "run": RUN2_ID})
        self.assertEqual((row["via"]["port"], row["via"]["peer"]), (server.port, "127.0.0.1"))
        for secret in (APPROVER, APPROVER_PASSWORD, browser.token, browser.csrf):
            self.assertNotIn(secret, json.dumps(row))

        events = [(item["event"], item.get("account")) for item in self.audit()]
        self.assertEqual(events, [("login", APPROVER_ACCOUNT), ("consent", APPROVER_ACCOUNT)])
        consent = self.audit()[1]
        self.assertEqual((consent["route"], consent["word"], consent["sha"], consent["session"], consent["path"]),
                         ("/api/approve", "GO plan", shown_sha, browser.digest, SPEC2))

        code, report = self.wait("GO plan", "--run", RUN2_ID, "--sha", shown_sha)
        self.assertEqual((code, report["status"]), (0, "PASS"), report)
        self.assertEqual({key: report["approval"][key] for key in ("kind", "run", "word", "sha", "source", "file")},
                         {"kind": "entry", "run": RUN2_ID, "word": "GO plan", "sha": shown_sha, "source": "dashboard", "file": SPEC2})
        self.assertEqual(self.wait("GO plan", "--run", RUN2_ID, "--sha", "f" * 40)[0], 3)   # the sha check of the reader is unchanged
        self.assertEqual(self.wait("GO implement", "--run", RUN2_ID)[0], 3)                  # one word never stands for another
        overall = self.cli("status", str(self.project), *LAYER)
        self.assertEqual(overall.returncode, 0, overall.stderr)
        self.assertIn(("GO plan", shown_sha, "dashboard"),
                      [(item["word"], item["sha"], item["source"]) for item in json.loads(overall.stdout)["approvals"]])

        replay = browser.post("/api/approve", body)
        self.assertEqual(replay.status, 409, replay.text)
        self.assertEqual(len(self.rows(RUN2_ID)), 1)

        # the next act: the plan appears and waits for GO implement; the same session consents, no second password
        self.plan_waits_for_go_implement()
        offer = browser.offer("plan", PLAN2)   # the page showing that plan (`?plan=`); the plan in progress stays the automatic choice
        self.assertEqual((offer["state"], offer["path"]), ("waiting", PLAN2), offer)
        second = browser.post("/api/approve", {"kind": "plan", "path": PLAN2, "sha": offer["sha"]})
        self.assertEqual((second.status, second.data.get("word")), (200, "GO implement"), second.text)
        self.assertEqual([(item["word"], item["by"], item["session"]) for item in self.rows(RUN2_ID)],
                         [("GO plan", APPROVER_ACCOUNT, browser.digest), ("GO implement", APPROVER_ACCOUNT, browser.digest)])
        self.assertEqual(self.wait("GO implement", "--run", RUN2_ID, "--sha", offer["sha"])[0], 0)

        # K4/K2: who consented stays with the approver
        view = f"/api/status?plan={PLAN2}"   # the page of the plan whose run holds the trail
        anonymous, mine = server.browser().get(view), browser.get(view)
        for canary in (APPROVER_ACCOUNT, browser.digest):
            self.assertIn(canary, mine.text)
            self.assertNotIn(canary, anonymous.text)
        self.assertNotIn(APPROVER, anonymous.text)
        public, private = anonymous.data["approvals"]["last"], mine.data["approvals"]["last"]
        self.assertEqual((public["word"], public["sha"], private["word"]), ("GO implement", offer["sha"], "GO implement"))
        self.assertTrue({"by", "role", "session", "via"} <= set(private), private)
        self.assertEqual({"by", "role", "session", "via"} & set(public), set(), public)

    def test_cp_binds_the_shown_head_and_a_new_head_needs_a_new_consent(self) -> None:
        self.session_waits_for("CP")
        server = self.serve()
        browser = self.signed_in(server)
        body = self.delivery_body(browser)
        self.assertEqual((body["sha"], body["branch"], body["target"], body["planState"]), (HEAD, BRANCH, BRANCH, "in_progress"))   # no `Oddanie:` in the plan: CP goes to the working branch
        self.set_head(HEAD_NEXT)
        self.assert_refused(browser.post("/api/approve", body), 409, "headChanged")
        self.set_head(HEAD)
        self.assert_refused(browser.post("/api/approve", {**body, "target": "develop"}), 409, "delivery_changed")
        without = {key: value for key, value in body.items() if key not in ("branch", "target", "mode")}
        self.assert_refused(browser.post("/api/approve", without), 400, "badRequest")
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.wait("CP", "--run", RUN_ID)[0], 3)

        given = browser.post("/api/approve", body)
        self.assertEqual((given.status, given.data.get("word")), (200, "CP"), given.text)
        row, = self.rows()
        self.assertEqual({key: row[key] for key in ("word", "sha", "source", "by", "role", "session", "mode", "target", "branch", "planState")},
                         {"word": "CP", "sha": HEAD, "source": "dashboard", "by": APPROVER_ACCOUNT, "role": "approver", "session": browser.digest,
                          "mode": "direct", "target": BRANCH, "branch": BRANCH, "planState": "in_progress"})
        self.assertEqual(self.wait("CP", "--run", RUN_ID, "--sha", HEAD)[0], 0)
        self.set_head(HEAD_NEXT)
        self.assertEqual(self.wait("CP", "--run", RUN_ID, "--sha", HEAD_NEXT)[0], 3)   # the old consent does not cover the new HEAD
        fresh = self.delivery_body(browser)
        self.assertEqual(browser.post("/api/approve", fresh).status, 200)
        self.assertEqual([item["sha"] for item in self.rows()], [HEAD, HEAD_NEXT])

    def test_ready_in_pr_mode(self) -> None:
        self.set_delivery(self.project, "pr")
        self.session_waits_for("READY")
        server = self.serve()
        browser = self.signed_in(server)
        given = browser.post("/api/approve", self.delivery_body(browser, "ready"))
        self.assertEqual((given.status, given.data.get("word")), (200, "READY"), given.text)
        row, = self.rows()
        self.assertEqual((row["word"], row["sha"], row["mode"], row["role"]), ("READY", HEAD, "pr", "approver"))
        self.assertEqual(self.wait("READY", "--run", RUN_ID, "--sha", HEAD)[0], 0)
        self.assertEqual(self.wait("CP", "--run", RUN_ID)[0], 3)

    def test_two_consents_at_once_one_wins(self) -> None:
        self.session_waits_for("CP")
        server = self.serve()
        first, second = self.signed_in(server), self.signed_in(server)
        body = self.delivery_body(first)
        start, replies = threading.Barrier(2), []

        def press(browser) -> None:
            start.wait(10)
            replies.append(browser.post("/api/approve", body))

        threads = [threading.Thread(target=press, args=(browser,)) for browser in (first, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(sorted(reply.status for reply in replies), [200, 409], [reply.text for reply in replies])
        self.assertEqual([item["word"] for item in self.rows()], ["CP"])


class RefusalTests(ConsentCase):
    """K3 and K12: every missing condition of a consent refuses it with one shape and writes nothing."""

    def setUp(self) -> None:
        super().setUp()
        self.spec_waits_for_go_plan()

    def test_each_missing_condition_refuses_and_the_complete_request_succeeds(self) -> None:
        server = self.serve()
        browser = self.signed_in(server)
        body = self.go_plan_body(browser)
        port = server.port
        before = self.snapshot()
        context = (403, "consent_refused")
        cases = (
            ("no CSRF", {"X_Skillsync_CSRF": None}, context),
            ("wrong CSRF", {"X_Skillsync_CSRF": "x" * 43}, context),
            ("no Origin", {"Origin": None}, context),
            ("foreign Origin", {"Origin": "http://evil.example"}, context),
            ("Origin null", {"Origin": "null"}, context),
            ("Origin of another port of the host", {"Origin": f"http://127.0.0.1:{port + 2}"}, context),
            ("Origin of another name of the host", {"Origin": f"http://localhost:{port}"}, context),
            ("no Sec-Fetch-Site", {"Sec_Fetch_Site": None}, context),
            ("Sec-Fetch-Site same-site", {"Sec_Fetch_Site": "same-site"}, context),
            ("Sec-Fetch-Site cross-site", {"Sec_Fetch_Site": "cross-site"}, context),
            ("neither Origin nor Sec-Fetch-Site", {"Origin": None, "Sec_Fetch_Site": None}, context),
            ("no X-Status-Board", {"X_Status_Board": None}, context),
            ("text/plain", {"Content_Type": "text/plain"}, context),
            ("a simple cross-origin form", {"Content_Type": "text/plain", "Origin": f"http://127.0.0.1:{port + 2}", "X_Status_Board": None,
                                            "X_Skillsync_CSRF": None, "Sec_Fetch_Site": "same-site"}, context),
            ("no cookie, old token header", {"Cookie": None, "X_Approve_Token": FAKE_TOKEN}, (401, "login_required")),
            ("a proxy header from the loopback", {"X_Forwarded_For": "203.0.113.5"}, (403, "consent_context")),
        )
        for label, override, expected in cases:
            with self.subTest(case=label):
                self.assert_refused(browser.post("/api/approve", body, browser.post_headers(**override)), *expected)
        for host in ("evil.example", f"127.0.0.1.evil.example:{port}"):
            with self.subTest(host=host):
                reply = browser.post("/api/approve", body, browser.post_headers(Host=host, Origin=f"http://{host}"))
                self.assertEqual(reply.status, 403, reply.text)
                self.assertNotIn("word", reply.data)
                for path in ("/api/status", "/_api/v1/session", "/", f"/api/doc?path={SPEC}", "/healthz"):
                    read = browser.get(path, {"Host": host})
                    self.assertEqual(read.status, 403, path)
                    self.assertNotIn("Demo", read.text)
        self.assertEqual(self.snapshot(), before)
        refused = [row for row in self.audit() if row["event"] == "consent_refused"]
        self.assertTrue({"consent_refused", "consent_context", "login_required"} <= {row.get("code") for row in refused}, refused)
        done = browser.post("/api/approve", body)   # the control: the same session and body with every condition met
        self.assertEqual(done.status, 200, done.text)

    def test_sessions_without_the_role_approver_are_refused(self) -> None:
        server = self.serve()
        approver = self.signed_in(server)
        body = self.go_plan_body(approver)
        before = self.snapshot()
        for login in (COMMENTER, ACCEPTOR):
            with self.subTest(login=login):
                other = self.signed_in(server, login, COMMENTER_PASSWORD)
                self.assertNotIn("approver", other.status()["session"]["roles"])
                self.assert_refused(other.post("/api/approve", body), 403, "forbidden_role")
                self.assert_refused(other.post("/api/approve", body, other.post_headers(X_Approve_Token=FAKE_TOKEN)), 403, "forbidden_role")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(approver.post("/api/approve", body).status, 200)

    def test_a_login_link_session_never_consents(self) -> None:
        server = self.serve()
        linked = server.browser()
        issued = self.cli("prototype", "login-link", str(self.project), "--user", APPROVER, "--port", str(server.port), *LAYER)
        self.assertEqual(issued.returncode, 0, issued.stdout + issued.stderr)
        url = json.loads(issued.stdout)["url"]
        followed = linked.get(url[url.index("/_login/link"):])
        self.assertEqual(followed.status, 302, followed.text)
        linked.cookie = followed.set_cookie.split(";")[0]
        linked.csrf = linked.get("/_api/v1/session").data["csrf"]
        self.assertTrue(linked.csrf, "the link did not open a session: the refusal below would prove nothing")
        summary = linked.status()["session"]
        self.assertEqual(summary["mode"], "link")
        self.assertNotIn("approver", summary["roles"])
        body = self.go_plan_body(linked)
        before = self.snapshot()
        self.assert_refused(linked.post("/api/approve", body), 403, "forbidden_role")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.signed_in(server).post("/api/approve", body).status, 200)

    def test_no_approvals_refuses_even_an_approver(self) -> None:
        server = self.serve("--no-approvals")
        browser = self.signed_in(server)
        before = self.snapshot()
        self.assert_refused(browser.post("/api/approve", {"kind": "spec", "path": SPEC2, "sha": self.blob_sha(SPEC2)}), 403, "approvals_disabled")
        self.assertEqual(self.snapshot(), before)


class SessionLifecycleTests(ConsentCase):
    """K8 and K13: rotation, logout, password change, changes of the account from the CLI, restart."""

    def setUp(self) -> None:
        super().setUp()
        self.spec_waits_for_go_plan()

    def assert_session_dead(self, browser, body: dict) -> None:
        before = self.snapshot()
        self.assert_refused(browser.post("/api/approve", body), 401, "session_expired")
        self.assertIsNone(browser.get("/_api/v1/session").data["user"])
        self.assertNotIn("account", browser.status().get("session") or {})
        self.assertEqual(self.snapshot(), before)

    def test_a_new_login_rotates_the_token(self) -> None:
        server = self.serve()
        browser = self.signed_in(server)
        old = browser.clone()
        self.assertEqual(browser.login().status, 200)
        self.assertNotEqual(browser.token, old.token)
        self.assertNotEqual(browser.csrf, old.csrf)
        body = self.go_plan_body(browser)
        self.assert_session_dead(old, body)
        self.assertEqual(browser.post("/api/approve", body).status, 200)

    def test_logout_needs_csrf_and_ends_the_session(self) -> None:
        server = self.serve()
        browser, other = self.signed_in(server), self.signed_in(server)
        copy = browser.clone()
        body = self.go_plan_body(browser)
        for override in ({"X_Skillsync_CSRF": None}, {"Origin": "http://evil.example"}):
            refused = browser.post("/_api/v1/logout", {}, browser.post_headers(**override))
            self.assertEqual(refused.status, 403, refused.text)
        self.assertEqual(browser.get("/_api/v1/session").data["user"]["login"], APPROVER)   # still signed in: the refusals did nothing
        done = browser.post("/_api/v1/logout", {})
        self.assertEqual(done.status, 204, done.text)
        self.assertIn("Max-Age=0", done.set_cookie)
        self.assert_session_dead(copy, body)                       # a kept copy of the cookie is worth nothing
        self.assertEqual(other.post("/api/approve", body).status, 200)   # the logout ended one session, not the account

    def test_password_change_on_the_page_ends_every_other_session(self) -> None:
        server = self.serve()
        browser, other = self.signed_in(server), self.signed_in(server)
        old = browser.clone()
        body = self.go_plan_body(browser)
        short = browser.change_password(APPROVER_PASSWORD, APPROVER_NEW_PASSWORD[:13])
        self.assertEqual((short.status, short.data.get("error")), (400, "weak_password"), short.text)
        wrong = browser.change_password(APPROVER_NEW_PASSWORD, APPROVER_NEW_PASSWORD)
        self.assertEqual((wrong.status, wrong.data.get("error")), (403, "invalid_current"), wrong.text)
        self.assertEqual(other.get("/_api/v1/session").data["user"]["login"], APPROVER)
        changed = browser.change_password(APPROVER_PASSWORD, APPROVER_NEW_PASSWORD)
        self.assertEqual(changed.status, 204, changed.text)
        self.assertNotEqual(browser.token, old.token)
        self.assert_session_dead(old, body)
        self.assert_session_dead(other, body)
        self.assertEqual(server.browser().login(APPROVER, APPROVER_PASSWORD).status, 401)
        self.assertEqual(server.browser().login(APPROVER, APPROVER_NEW_PASSWORD).status, 200)
        self.assertEqual(browser.post("/api/approve", body).status, 200)   # the session that changed it goes on with its new token
        everything = server.log() + json.dumps(self.audit())
        self.assertIn("password_changed", everything)
        for secret in (APPROVER_PASSWORD, APPROVER_NEW_PASSWORD, browser.token, old.token):
            self.assertNotIn(secret, everything)

    def test_password_reset_from_the_cli_ends_the_running_session(self) -> None:
        server = self.serve()
        browser = self.signed_in(server)
        body = self.go_plan_body(browser)
        code, out, shown = self.cli_on_terminal(APPROVER_NEW_PASSWORD, "prototype", "user", "passwd", str(self.project), APPROVER,
                                                "--password-prompt", *LAYER)
        self.assertEqual(code, 0, out + shown)
        self.assert_session_dead(browser, body)
        self.assertEqual(self.signed_in(server, APPROVER, APPROVER_NEW_PASSWORD).post("/api/approve", body).status, 200)

    def test_role_withdrawn_from_the_cli_takes_effect_on_the_running_session(self) -> None:
        server = self.serve()
        browser = self.signed_in(server)
        body = self.go_plan_body(browser)
        done = self.cli("prototype", "user", "roles", str(self.project), APPROVER, "--roles", "commenter", *LAYER)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        before = self.snapshot()
        self.assert_refused(browser.post("/api/approve", body), 403, "forbidden_role")
        self.assertEqual(self.snapshot(), before)

    def test_account_removed_from_the_cli_ends_the_running_session(self) -> None:
        server = self.serve()
        browser = self.signed_in(server)
        body = self.go_plan_body(browser)
        done = self.cli("prototype", "user", "remove", str(self.project), APPROVER, *LAYER)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assert_session_dead(browser, body)

    def test_restart_forgets_every_session_and_keeps_the_trail(self) -> None:
        self.session_waits_for("CP")
        server = self.serve()
        browser = self.signed_in(server)
        first_instance = browser.get("/healthz").data["serverId"]
        body = self.go_plan_body(browser)
        self.assertEqual(browser.post("/api/approve", body).status, 200)
        trail = self.rows(RUN2_ID)
        server.stop()
        self.assertEqual(server.proc.returncode, 0, server.log())

        again = self.serve(port=server.port)
        self.assertNotEqual(again.browser().get("/healthz").data["serverId"], first_instance)
        self.assertEqual(self.rows(RUN2_ID), trail)
        self.assertEqual(self.wait("GO plan", "--run", RUN2_ID)[0], 0)
        self.plan_waits_for_go_implement()
        offer = again.browser().offer("plan", PLAN2)
        self.assertEqual((offer["state"], offer["path"]), ("waiting", PLAN2), offer)
        next_body = {"kind": "plan", "path": PLAN2, "sha": offer["sha"]}
        before = self.snapshot()
        # the old cookie carries the name of the old instance: for the new server it is no cookie at all
        self.assert_refused(browser.post("/api/approve", next_body), 401, "login_required")
        fresh = self.signed_in(again)
        old_name, new_name = browser.cookie.split("=")[0], fresh.cookie.split("=")[0]
        self.assertNotEqual(old_name, new_name)
        renamed = browser.clone()
        renamed.cookie = f"{new_name}={browser.token}"   # the old token under the new name: refused by the instance of the token
        self.assert_refused(renamed.post("/api/approve", next_body), 401, "session_expired")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(fresh.post("/api/approve", next_body).status, 200)


class NoTokenTests(ConsentCase):
    """K5: no consent token anywhere: not in the address, not in `/healthz`, not in the state file, not in the output; an old header changes nothing."""

    def test_url_healthz_state_and_output_carry_no_token(self) -> None:
        self.spec_waits_for_go_plan()
        server = self.serve()
        plain = self.cli("dashboard", "url", str(self.project), "--port", str(server.port), *LAYER)
        self.assertEqual(plain.returncode, 0, plain.stdout + plain.stderr)
        default = self.cli("dashboard", "url", str(self.project), *LAYER)
        self.assertEqual(default.returncode, 0, default.stdout + default.stderr)
        machine = self.cli("dashboard", "url", str(self.project), "--port", str(server.port), "--json", *LAYER)
        self.assertEqual(machine.returncode, 0, machine.stdout + machine.stderr)
        report = json.loads(machine.stdout)
        self.assertRegex(report["url"], rf"^http://127\.0\.0\.1:{server.port}/(\?tab=[A-Za-z0-9_-]+)?$")
        self.assertRegex(plain.stdout.strip().splitlines()[-1], rf"^http://127\.0\.0\.1:{server.port}/")
        self.assertNotIn("#", plain.stdout + default.stdout + report["url"])
        health = server.browser().get("/healthz").data
        self.assertEqual(sorted(health), ["ok", "serverId", "version"])
        states = self.state_texts()
        self.assertTrue(states, "no state file: the assertions about it would be empty")
        for text in states:
            keys = set()

            def collect(value) -> None:
                if isinstance(value, dict):
                    keys.update(value)
                    for item in value.values():
                        collect(item)
                elif isinstance(value, list):
                    for item in value:
                        collect(item)

            collect(json.loads(text))
            self.assertIn("port", keys)
            self.assertEqual([key for key in keys if "token" in key.lower()], [])
        for label, text in (("url", plain.stdout + plain.stderr + default.stdout), ("url --json", machine.stdout), ("server output", server.log())):
            with self.subTest(source=label):
                self.assertNotIn("#zgoda", text)
                self.assertNotIn("zgoda=", text)
                self.assertNotIn("token=", text.lower())


if __name__ == "__main__":
    unittest.main()
