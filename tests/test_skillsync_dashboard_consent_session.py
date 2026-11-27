"""CORE-2000-UNIFIED U-2: the consents GO / CP / READY of the status page are given by a SESSION with the role `approver` (no token, no second password).

Contract (spec CORE-2000-UNIFIED, section 3 and 7; criteria K3, K4, K5, K12, K13, K14, K19):
- the order of the refusals of `POST /api/approve|feedback-approve|plan-status`: Host -> context (loopback peer with a loopback Host, or a trusted proxy
  that forwards HTTPS) -> the request itself (`X-Status-Board`, JSON, the exact `Origin`, `Sec-Fetch-Site: same-origin`) -> session (401 `login_required`,
  `session_expired`) -> CSRF -> the EFFECTIVE role `approver` (403 `forbidden_role`; a login-link and a handover session never have it) ->
  `password_change_required` -> `approvals_disabled`; every refusal is `{error, code}` with a text of the catalogue, audited, and writes nothing;
- the trail `approvals.jsonl` gets `by` (the pseudonym of the account), `role`, `session` (8 hex) and `source: dashboard`; `run/audit.jsonl` gets `consent` and
  `consent_refused` (`account`, `source`, `route`, `tab`, `session`), never a password or a token; a flood of equal refusals is folded into `suppressed`;
- `/api/status` `session` = `{url, account, roles, mode}` for a signed-in request only (an anonymous one: `{url}`), `/healthz` has no `tokenId`, the read socket
  has no consent route, `--allow-remote-approvals` is off by default.

Every negative test has its positive control (the same request with the right session is accepted). `MutationControlTests` shows that the key assertions can fail
(a mutant by monkeypatch, no edit of the code). Appendix: the pass of `/api/prototypes/<slug>/accept|restore|resolve` is U-3, not here.
Fake passwords are built at run time (secret scan)."""
from __future__ import annotations

import http.client
import json
import os
import select
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from skillsync_workflow import dashboard_http as srv  # noqa: E402
from skillsync_workflow import dashboard_ports as ports  # noqa: E402
from skillsync_workflow import dashboard_token  # noqa: E402
from skillsync_workflow import prototype_server as proto_server  # noqa: E402
from layers_helpers import home_env  # noqa: E402
from tmp_paths import child  # noqa: E402
from tests.serve_start import bind_free  # noqa: E402
from tests.test_skillsync_dashboard_server import (ACCEPTOR_LOGIN, APPROVER_LOGIN, ASSETS, COMMENTER_LOGIN, DESCRIPTIONS, PLAN, RUN,  # noqa: E402
                                                   SESSION_PW, SESSION_PW_COMMENTER, TEST_TOKEN, ProjectCase, SessionKit, free_port,
                                                   install_fake_cli, session_request, split_post, spy_core)

SPEC2 = "docs/specs/demo-etap-2.md"
PLAN2 = "docs/plans/2026-01-20-demo-etap-2.md"
RUN2 = "2026-01-20-demo-etap-2"
RUN1 = "2026-01-10-demo-etap-1"
ROUTE = "/api/approve"


def call(port: int, method: str, path: str, headers: dict | None = None, body: bytes = b"") -> tuple[int, dict, dict]:
    """One request to the loopback; `headers` values may be lists (a repeated header). Returns `(status, json, headers)`."""
    headers = headers or {}
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.putrequest(method, path, skip_host="Host" in headers, skip_accept_encoding=True)
        for key, value in headers.items():
            for item in (value if isinstance(value, list) else [value]):
                conn.putheader(key, item)
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        if body:
            conn.send(body)
        response = conn.getresponse()
        raw = response.read()
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = {"raw": raw[:80].decode("utf-8", "replace")}
        return response.status, data, dict(response.getheaders())
    finally:
        conn.close()


class PageCase(ProjectCase):
    """The `page` socket (the default mode of 1.19): everybody reads, a session with `approver` consents. `self.good` = the headers of a right consent."""
    ROLE = ports.ROLE_PAGE
    APPROVE_HOSTS: tuple = ()
    READ_HOSTS: tuple = ()
    KIT: dict = {}

    def setUp(self) -> None:
        super().setUp()
        install_fake_cli(self)
        spy_core(self)
        self.board = srv.Board(self.root, poll=0.2, heartbeat=0.5, prototype_port=1, assets=ASSETS, descriptions=DESCRIPTIONS, approvals=True,
                               approve_hosts=self.APPROVE_HOSTS, read_hosts=self.READ_HOSTS)
        self.kit = SessionKit(self, **self.KIT)
        self.server, self.port = bind_free(lambda port: srv.make_server(self.board, "127.0.0.1", port, role=self.ROLE, proto_app=self.kit.app), free_port)
        self.kit.attach(self.server)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        threading.Thread(target=self.board.watch_loop, daemon=True).start()
        self.addCleanup(self.stop)
        self.session = self.kit.login(self.port)
        self.good = self.session.headers(self.port)

    def stop(self) -> None:
        self.board.stop.set()
        with self.board.cond:
            self.board.cond.notify_all()
        self.server.shutdown()
        self.server.server_close()

    # --- helpers
    def post(self, headers: dict, data: dict | None = None, path: str = ROUTE, port: int | None = None) -> tuple[int, dict]:
        status, answer, _ = call(port or self.port, "POST", path, headers, json.dumps(data if data is not None else {}).encode("utf-8"))
        return status, answer

    def get(self, path: str, headers: dict | None = None) -> tuple[int, dict]:
        status, answer, _ = call(self.port, "GET", path, headers or {})
        return status, answer

    def spec_offer(self) -> dict:
        (self.root / srv.RUNS_DIR / RUN2).mkdir(parents=True, exist_ok=True)
        spec = self.root / SPEC2
        spec.write_text(f"# Spec: Demo 2\n\nStatus: draft (czeka na GO plan)\nPlan: {PLAN2}\n", encoding="utf-8")
        later = (self.root / PLAN).stat().st_mtime + 100
        os.utime(spec, (later, later))
        _status, data = self.get("/api/status")
        action = next(a for a in data["approvals"]["actions"] if a["kind"] == "spec")
        return {"kind": "spec", "path": SPEC2, "sha": action["sha"]}

    def cp_offer(self) -> dict:
        with (self.root / f"{RUN}/checkpoints.md").open("a", encoding="utf-8") as handle:
            handle.write("- 11:20 zrobione: S9; w toku: —; czeka: CP\n")
        _status, data = self.get(f"/api/status?plan={PLAN}")
        action = next(a for a in data["approvals"]["actions"] if a["kind"] == "cp")
        # kontrakt U-2-3 (D6): CP/READY carry the branch and the target the page showed
        return {"kind": "cp", "path": RUN1, "plan": PLAN, "sha": action["sha"], "branch": data["delivery"].get("branch"), "target": data["delivery"].get("target"),
                "mode": data["delivery"].get("mode")}

    def entries(self, run: str) -> list[dict]:
        path = child(self.root / srv.RUNS_DIR, run) / srv.RUN_APPROVALS
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def audit(self, event: str | None = None) -> list[dict]:
        return [line for line in self.kit.audit() if event is None or line.get("event") == event]

    def assert_no_write(self) -> None:
        self.set_doc.assert_not_called()
        self.record.assert_not_called()
        self.assertEqual(self.entries(RUN2) + self.entries(RUN1), [])

    def refused(self, headers: dict, status: int, code: str, data: dict | None = None, path: str = ROUTE) -> dict:
        """The request is refused with `status` and `code`, in the one shape `{error, code}` (a non-empty text of the catalogue), and nothing is written."""
        got, answer = self.post(headers, data if data is not None else self.offer_data, path)
        self.assertEqual((got, answer.get("code")), (status, code), answer)
        self.assertEqual(sorted(answer), ["code", "error"], answer)
        self.assertTrue(isinstance(answer["error"], str) and answer["error"])
        self.assert_no_write()
        return answer

    @property
    def offer_data(self) -> dict:
        if not hasattr(self, "_offer"):
            self._offer = self.spec_offer()
        return self._offer

    def consent_works(self) -> None:
        """The positive control: the right session with the right headers is accepted for the offer."""
        status, answer = self.post(self.good, self.offer_data)
        self.assertEqual(status, 200, answer)


class ApproverConsentTests(PageCase):
    def test_a_go_consent_of_an_approver_session_writes_the_trail_with_the_account_the_role_and_the_session(self) -> None:
        offer = self.offer_data
        status, answer = self.post(self.good, offer)
        self.assertEqual((status, answer["word"], answer["file"]), (200, "GO plan", SPEC2), answer)
        self.set_doc.assert_called_once()
        entry = self.entries(RUN2)[-1]
        self.assertEqual((entry["word"], entry["sha"], entry["source"], entry["role"]), ("GO plan", offer["sha"], "dashboard", "approver"))
        self.assertEqual(entry["by"], self.kit.account(APPROVER_LOGIN))
        self.assertNotEqual(entry["by"], APPROVER_LOGIN)   # the pseudonym, not the e-mail
        self.assertRegex(entry["session"], r"^[0-9a-f]{8}$")
        token, csrf = self.session.pair.split("=", 1)[1], self.session.csrf
        self.assertNotIn(entry["session"], (token[:8], csrf[:8]))   # a digest of the session, not the beginning of a secret
        text = (self.root / srv.RUNS_DIR / RUN2 / srv.RUN_APPROVALS).read_text(encoding="utf-8")
        for secret in (SESSION_PW, csrf, token, TEST_TOKEN, APPROVER_LOGIN):
            self.assertNotIn(secret, text)   # no password, token or e-mail in the trail

    def test_a_cp_consent_has_the_head_as_its_sha_and_the_same_trail_fields(self) -> None:
        offer = self.cp_offer()
        status, answer = self.post(self.good, offer)
        self.assertEqual((status, answer["file"]), (200, f"{RUN}/approvals.jsonl"), answer)
        entry = self.entries(RUN1)[-1]
        self.assertEqual((entry["word"], entry["sha"], entry["source"], entry["role"], entry["by"]),
                         ("CP", offer["sha"], "dashboard", "approver", self.kit.account(APPROVER_LOGIN)))
        self.assertRegex(entry["session"], r"^[0-9a-f]{8}$")
        # the HEAD of the repository of the project is what the page offered (not a value of the request)
        head = (self.root / ".git/refs/heads/feature/demo").read_text(encoding="utf-8").strip()
        self.assertTrue(head.startswith(entry["sha"][:7]) or entry["sha"].startswith(head[:7]), (head, entry["sha"]))

    def test_two_sessions_of_one_account_leave_two_different_session_digests_and_the_same_account(self) -> None:
        first_offer = self.cp_offer()
        self.assertEqual(self.post(self.good, first_offer)[0], 200)   # the first session: CP at the HEAD shown
        other = self.kit.login(self.port)                             # a second sign-in of the same account: another session
        self.assertNotEqual(other.pair, self.session.pair)
        (self.root / ".git/refs/heads/feature/demo").write_text("fedcba9876543210\n", encoding="utf-8")   # a new commit: a new HEAD, a new offer
        time.sleep(0.3)
        _status, data = self.get("/api/status")
        second_offer = next(a for a in data["approvals"]["actions"] if a["kind"] == "cp")
        self.assertNotEqual(second_offer["sha"], first_offer["sha"])
        status, answer = self.post(other.headers(self.port), {**first_offer, "sha": second_offer["sha"]})
        self.assertEqual(status, 200, answer)
        first, second = self.entries(RUN1)
        self.assertNotEqual(first["session"], second["session"])
        self.assertEqual((first["by"], second["by"]), (self.kit.account(APPROVER_LOGIN),) * 2)

    def test_the_same_sha_a_second_time_is_409_and_there_is_one_trail_entry(self) -> None:
        offer = self.cp_offer()
        self.assertEqual(self.post(self.good, offer)[0], 200)
        status, answer = self.post(self.good, offer)
        self.assertEqual(status, 409, answer)
        self.assertEqual(len(self.entries(RUN1)), 1)
        self.assertEqual(self.record.call_count, 1)

    def test_a_changed_head_is_409_with_nothing_written_and_the_new_head_is_accepted(self) -> None:
        offer = self.cp_offer()
        status, answer = self.post(self.good, {**offer, "sha": "0" * 14})   # the HEAD of the page is not the HEAD of the repository any more
        self.assertEqual(status, 409, answer)
        self.record.assert_not_called()
        self.assertEqual(self.entries(RUN1), [])
        self.assertEqual(self.post(self.good, offer)[0], 200)   # control

    def test_a_tab_that_changed_since_the_page_was_drawn_is_409_tab_changed_and_writes_nothing(self) -> None:
        offer = self.offer_data
        with mock.patch.object(srv.TabRegistry, "revalidate_for_write", return_value=False):   # the directory of the tab no longer passes the re-validation
            status, answer = self.post(self.good, offer)
        self.assertEqual((status, answer.get("code")), (409, "tabChanged"), answer)
        self.assert_no_write()
        self.assertEqual(self.post(self.good, offer)[0], 200)   # control: the tab as it was

    def test_an_unknown_tab_is_404_only_after_the_session_gate(self) -> None:
        offer = {**self.offer_data, "tab": "abcdef012345"}
        status, answer = self.post(self.good, offer)
        self.assertEqual((status, answer.get("code")), (404, "unknownTab"))
        anonymous = {k: v for k, v in self.good.items() if k not in ("Cookie", "X-Skillsync-CSRF")}
        status, answer = self.post(anonymous, offer)   # an anonymous request learns nothing about the tabs: the gate answers first
        self.assertEqual((status, answer.get("code")), (401, "login_required"))
        self.assert_no_write()

    def test_the_body_may_not_name_the_consent_of_another_session(self) -> None:
        """The right to consent is the session, never a field of the body (`by`, `role`, `account`, `session` are ignored): the trail has the session's own."""
        status, answer = self.post(self.good, {**self.offer_data, "by": "someone-else", "role": "expert", "account": "x", "session": "deadbeef"})
        self.assertEqual(status, 200, answer)
        entry = self.entries(RUN2)[-1]
        self.assertEqual((entry["by"], entry["role"]), (self.kit.account(APPROVER_LOGIN), "approver"))
        self.assertNotEqual(entry["session"], "deadbeef")


class StatusAndHealthTests(PageCase):
    def test_status_session_is_url_account_roles_and_mode_for_a_signed_in_request_only(self) -> None:
        _status, anonymous = self.get("/api/status")
        self.assertEqual(sorted(anonymous["session"]), ["url"])
        for secret in (self.kit.account(APPROVER_LOGIN), APPROVER_LOGIN, "approver"):
            self.assertNotIn(secret, json.dumps(anonymous["session"]))
        _status, signed = self.get("/api/status", {"Cookie": self.session.pair})
        session = signed["session"]
        self.assertEqual(sorted(session), ["account", "mode", "roles", "url"])
        self.assertEqual((session["account"], session["mode"]), (self.kit.account(APPROVER_LOGIN), "password"))
        self.assertIn("approver", session["roles"])
        self.assertNotEqual(session["account"], APPROVER_LOGIN)
        # another account: only its own roles
        commenter = self.kit.login(self.port, COMMENTER_LOGIN, SESSION_PW_COMMENTER)
        _status, other = self.get("/api/status", {"Cookie": commenter.pair})
        self.assertEqual((other["session"]["account"], other["session"]["roles"]), (self.kit.account(COMMENTER_LOGIN), ["commenter"]))
        # a stale cookie is an anonymous request
        _status, stale = self.get("/api/status", {"Cookie": self.session.stale_pair})
        self.assertEqual(sorted(stale["session"]), ["url"])

    def test_status_of_a_link_and_a_handover_session_shows_their_mode_and_no_approver(self) -> None:
        for via in ("link", "handoff"):
            with self.subTest(via=via):
                made = self.kit.session_via(self.port, APPROVER_LOGIN, via)
                _status, data = self.get("/api/status", {"Cookie": made.pair})
                self.assertEqual(data["session"]["mode"], via)
                self.assertNotIn("approver", data["session"]["roles"])
                self.assertEqual(data["session"]["account"], self.kit.account(APPROVER_LOGIN))
        _status, data = self.get("/api/status", {"Cookie": self.session.pair})   # control: the password session of the same account has `approver`
        self.assertIn("approver", data["session"]["roles"])

    def test_healthz_and_the_state_have_no_token(self) -> None:
        status, data = self.get("/healthz")
        self.assertEqual((status, sorted(data)), (200, ["ok", "serverId", "version"]))
        self.assertNotIn("tokenId", data)
        _status, state = self.get("/api/status")
        self.assertIs(state["approvals"]["tokenRequired"], False)
        self.assertIs(state["approvals"]["here"], True)   # the consents are on and this request is local


class RefusalTests(PageCase):
    """The order of the refusals and the one shape of them. Each line is a request that fails exactly one step."""

    def test_every_step_of_the_gate_refuses_in_the_one_shape_and_the_right_session_passes(self) -> None:
        good = self.good
        no = lambda *names: {k: v for k, v in good.items() if k not in names}   # noqa: E731
        cases = [
            ("proxy header", {**good, "X-Forwarded-For": "10.0.0.9"}, 403, "consent_context"),
            ("no X-Status-Board", no("X-Status-Board"), 403, "consent_refused"),
            ("text/plain", {**good, "Content-Type": "text/plain"}, 403, "consent_refused"),
            ("foreign Origin", {**good, "Origin": "http://evil.example"}, 403, "consent_refused"),
            ("Origin null", {**good, "Origin": "null"}, 403, "consent_refused"),
            ("no Origin", no("Origin"), 403, "consent_refused"),
            ("two Origins", {**good, "Origin": [good["Origin"], good["Origin"]]}, 403, "consent_refused"),
            ("no Sec-Fetch-Site", no("Sec-Fetch-Site"), 403, "consent_refused"),
            ("Sec-Fetch-Site same-site", {**good, "Sec-Fetch-Site": "same-site"}, 403, "consent_refused"),
            ("Sec-Fetch-Site cross-site", {**good, "Sec-Fetch-Site": "cross-site"}, 403, "consent_refused"),
            ("no cookie", no("Cookie"), 401, "login_required"),
            ("an empty cookie", {**good, "Cookie": ""}, 401, "login_required"),
            ("a cookie that opens no session", {**good, "Cookie": self.session.stale_pair}, 401, "session_expired"),
            ("no CSRF", no("X-Skillsync-CSRF"), 403, "consent_refused"),
            ("a wrong CSRF", {**good, "X-Skillsync-CSRF": "x" * len(self.session.csrf)}, 403, "consent_refused"),
        ]
        for name, headers, status, code in cases:
            with self.subTest(name=name):
                self.refused(headers, status, code)
            time.sleep(0.01)
        self.consent_works()   # control: the same offer with every header right is accepted

    def test_the_first_failing_step_hides_the_later_ones(self) -> None:
        broken = {"Origin": "http://evil.example", "Sec-Fetch-Site": "cross-site", "Cookie": self.session.stale_pair, "X-Skillsync-CSRF": "x"}
        self.refused({**self.good, "X-Forwarded-For": "10.0.0.9", **broken}, 403, "consent_context")     # the context before the request
        self.refused({**self.good, **broken}, 403, "consent_refused")                                       # the request before the session
        self.refused({**self.good, "Cookie": self.session.stale_pair, "X-Skillsync-CSRF": "x"}, 401, "session_expired")   # the session before the CSRF
        self.consent_works()

    def test_the_status_codes_and_texts_are_from_the_catalogue_not_from_the_session(self) -> None:
        answer = self.refused({k: v for k, v in self.good.items() if k != "Cookie"}, 401, "login_required")
        self.assertEqual(answer["error"], srv.i18n.t(self.board.lang(), "srv.http.loginRequired"))
        answer = self.refused({**self.good, "Cookie": self.session.stale_pair}, 401, "session_expired")
        self.assertEqual(answer["error"], srv.i18n.t(self.board.lang(), "srv.http.sessionExpired"))
        self.consent_works()

    def test_a_cross_origin_post_with_no_custom_header_is_refused_and_the_server_has_no_preflight(self) -> None:
        """K12: a script of the origin of the prototypes (another port of the same host) cannot consent: `text/plain` without `X-Status-Board` and with
        the cookie it would get from the browser is refused, and the server does not answer `OPTIONS` (no CORS)."""
        other_origin = f"http://127.0.0.1:{self.port + 1}"
        simple = {"Content-Type": "text/plain", "Origin": other_origin, "Cookie": self.session.pair}
        self.refused(simple, 403, "consent_refused")
        self.refused({**self.good, "Origin": other_origin}, 403, "consent_refused")   # even with every other header right
        status, _answer, headers = call(self.port, "OPTIONS", ROUTE, {"Origin": other_origin, "Access-Control-Request-Method": "POST",
                                                                       "Access-Control-Request-Headers": "x-status-board"})
        self.assertGreaterEqual(status, 400)
        self.assertFalse([name for name in headers if name.lower().startswith("access-control-")])
        self.consent_works()

    def test_the_route_of_a_consent_has_no_get(self) -> None:
        status, _data, _ = call(self.port, "GET", ROUTE, self.good)
        self.assertEqual(status, 404)
        self.assert_no_write()
        self.consent_works()


class RoleTests(PageCase):
    """The role is the EFFECTIVE one: a comment-only account, an acceptor and a developer, and the sessions of a login link or a handover never consent."""

    def test_only_the_effective_approver_role_consents(self) -> None:
        offer = self.offer_data
        commenter = self.kit.login(self.port, COMMENTER_LOGIN, SESSION_PW_COMMENTER)
        acceptor = self.kit.login(self.port, ACCEPTOR_LOGIN, SESSION_PW_COMMENTER)
        refused = {}
        for name, made in (("commenter", commenter), ("acceptor+developer", acceptor),
                           ("approver by link", self.kit.session_via(self.port, APPROVER_LOGIN, "link")),
                           ("approver by handover", self.kit.session_via(self.port, APPROVER_LOGIN, "handoff"))):
            with self.subTest(name=name):
                status, answer = self.post(made.headers(self.port), offer)
                self.assertEqual((status, answer.get("code"), sorted(answer)), (403, "forbidden_role", ["code", "error"]), answer)
                refused[name] = answer
        self.assertEqual(len({json.dumps(answer, sort_keys=True) for answer in refused.values()}), 1)   # the answer does not say WHICH role or account
        self.assert_no_write()
        self.consent_works()   # control: the password session of the approver

    def test_the_acceptor_and_developer_roles_are_not_a_consent_for_the_other_routes_either(self) -> None:
        acceptor = self.kit.login(self.port, ACCEPTOR_LOGIN, SESSION_PW_COMMENTER)
        for path, data in (("/api/feedback-approve", {"draft": "20260101-000000-demo", "sha": "a" * 7}),
                           ("/api/plan-status", {"plan": PLAN, "status": "done"})):
            with self.subTest(path=path):
                status, answer = self.post(acceptor.headers(self.port), data, path)
                self.assertEqual((status, answer.get("code")), (403, "forbidden_role"))
        self.assert_no_write()

    def test_an_account_that_must_change_its_password_does_not_consent(self) -> None:
        users = Path(self.kit.users_path)
        record = json.loads(users.read_text(encoding="utf-8"))
        original = users.read_text(encoding="utf-8")
        for item in record["users"]:
            if item["login"] == APPROVER_LOGIN:
                item["mustChange"] = True
        users.write_text(json.dumps(record), encoding="utf-8")
        self.refused(self.good, 403, "password_change_required")
        users.write_text(original, encoding="utf-8")   # control: the password changed, the same session consents
        self.consent_works()

    def test_the_consents_off_are_a_403_whatever_the_session(self) -> None:
        offer = self.offer_data   # the offers are read while the consents are on
        self.board.approvals = False
        self.refused(self.good, 403, "approvals_disabled", offer)
        self.board.approvals = True
        self.consent_works()

    def test_the_role_is_checked_before_the_must_change_and_the_consents_off(self) -> None:
        """A commenter gets `forbidden_role` even when the consents are off: the answer does not tell a non-approver anything else about the server."""
        commenter = self.kit.login(self.port, COMMENTER_LOGIN, SESSION_PW_COMMENTER)
        offer = self.offer_data
        self.board.approvals = False
        status, answer = self.post(commenter.headers(self.port), offer)
        self.assertEqual((status, answer.get("code")), (403, "forbidden_role"))
        self.board.approvals = True
        self.consent_works()


class NoLeakOfAccountsTests(PageCase):
    def test_a_refusal_does_not_tell_whether_an_account_exists(self) -> None:
        offer = self.offer_data
        anonymous = {k: v for k, v in self.good.items() if k not in ("Cookie", "X-Skillsync-CSRF")}
        # the body names an existing and a non-existing account: the answer is the same
        names = [self.post(anonymous, {**offer, "login": login})[1] for login in (APPROVER_LOGIN, "nobody@example.com")]
        self.assertEqual(names[0], names[1])
        # a session of an account that was removed gets the answer of a cookie that never was a session
        victim = self.kit.login(self.port, COMMENTER_LOGIN, SESSION_PW_COMMENTER)
        users = Path(self.kit.users_path)
        original = users.read_text(encoding="utf-8")
        record = json.loads(original)
        record["users"] = [item for item in record["users"] if item["login"] != COMMENTER_LOGIN]
        users.write_text(json.dumps(record), encoding="utf-8")
        gone = self.post(victim.headers(self.port), offer)
        never = self.post({**self.good, "Cookie": self.session.stale_pair}, offer)
        self.assertEqual(gone, never)
        self.assertEqual(gone[0], 401)
        users.write_text(original, encoding="utf-8")
        self.assert_no_write()
        self.consent_works()


class AuditTests(PageCase):
    SECRETS_KEYS = ("password", "token", "csrf", "cookie", "body", "login")

    def test_a_consent_is_audited_with_the_account_the_source_the_route_the_tab_and_the_session(self) -> None:
        offer = self.offer_data
        self.assertEqual(self.post(self.good, offer)[0], 200)
        lines = self.audit("consent")
        self.assertEqual(len(lines), 1, self.kit.audit())
        line = lines[0]
        self.assertEqual((line["account"], line["route"], line["tab"]), (self.kit.account(APPROVER_LOGIN), ROUTE, self.tabs_default_id()))
        self.assertTrue(line["source"])
        self.assertRegex(line["session"], r"^[0-9a-f]{8}$")
        self.assertEqual(line["session"], self.entries(RUN2)[-1]["session"])   # the audit and the trail name the same session
        for key in self.SECRETS_KEYS:
            self.assertNotIn(key, line)
        text = (Path(self.kit.run_dir) / "audit.jsonl").read_text(encoding="utf-8")
        for secret in (SESSION_PW, self.session.csrf, self.session.pair.split("=", 1)[1], TEST_TOKEN, APPROVER_LOGIN):
            self.assertNotIn(secret, text)

    def tabs_default_id(self) -> str:
        return self.server.RequestHandlerClass.tabs.default_id

    def test_a_refusal_is_audited_with_its_code_and_the_known_account_only(self) -> None:
        commenter = self.kit.login(self.port, COMMENTER_LOGIN, SESSION_PW_COMMENTER)
        self.post(commenter.headers(self.port), self.offer_data)
        anonymous = {k: v for k, v in self.good.items() if k not in ("Cookie", "X-Skillsync-CSRF")}
        self.post(anonymous, self.offer_data)
        lines = self.audit("consent_refused")
        by_code = {line["code"]: line for line in lines}
        self.assertEqual(sorted(by_code), ["forbidden_role", "login_required"], lines)
        self.assertEqual(by_code["forbidden_role"]["account"], self.kit.account(COMMENTER_LOGIN))
        self.assertEqual((by_code["forbidden_role"]["http"], by_code["forbidden_role"]["route"]), (403, ROUTE))
        self.assertNotIn("account", by_code["login_required"])   # an anonymous source: no account, only the source
        for line in lines:
            self.assertTrue(line["source"])
            for key in self.SECRETS_KEYS:
                self.assertNotIn(key, line)
        self.assertEqual(self.audit("consent"), [])

    def test_a_flood_of_equal_refusals_is_folded_into_suppressed(self) -> None:
        anonymous = {k: v for k, v in self.good.items() if k not in ("Cookie", "X-Skillsync-CSRF")}
        for _ in range(5):
            self.post(anonymous, self.offer_data)
        first = self.audit("consent_refused")
        self.assertEqual(len(first), 1, first)   # one line, not five fsyncs
        time.sleep(1.1)
        self.post(anonymous, self.offer_data)
        lines = self.audit("consent_refused")
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1].get("suppressed"), 4)   # the four folded ones are counted on the next line
        self.assertNotIn("suppressed", lines[0])
        self.post({**anonymous, "X-Status-Board": "0"}, self.offer_data)   # another code is another refusal: written at once
        self.assertEqual(len(self.audit("consent_refused")), 3)


class ContextTests(PageCase):
    """K19: a consent only locally or through TLS; `--allow-remote-approvals` is off by default."""
    APPROVE_HOSTS = ("lan-host",)

    def lan(self, headers: dict | None = None) -> dict:
        return {**self.good, "Host": f"lan-host:{self.port}", "Origin": f"http://lan-host:{self.port}", **(headers or {})}

    def test_a_consent_from_the_network_over_http_is_refused_by_default(self) -> None:
        self.assertIs(getattr(self.kit.app, "allow_remote_approvals", False), False)
        self.refused(self.lan(), 403, "consent_context")
        status, state = self.get("/api/status", {"Host": f"lan-host:{self.port}"})
        self.assertEqual(status, 200)
        self.assertEqual((state["approvals"]["here"], state["approvals"]["viewer"]), (False, True))   # the page shows the buttons only where a consent can work
        self.consent_works()   # control: the same session, locally

    def test_allow_remote_approvals_is_the_only_thing_that_lets_a_network_consent_through(self) -> None:
        self.kit.app.allow_remote_approvals = True
        status, answer = self.post(self.lan(), self.offer_data)
        self.assertEqual(status, 200, answer)

    def test_the_default_of_the_command_is_off(self) -> None:
        from skillsync_workflow import prototype as proto_cli  # noqa: F401  (the command module is imported lazily by `build_proto_app`)
        args = SimpleNamespace()
        manifest: dict = {}
        app = srv.build_proto_app(self.root, manifest, self.board, args)
        self.assertIs(app.allow_remote_approvals, False)
        app = srv.build_proto_app(self.root, manifest, self.board, SimpleNamespace(allow_remote_approvals=True))
        self.assertIs(app.allow_remote_approvals, True)


class TlsContextTests(PageCase):
    """A trusted proxy that forwards HTTPS is the other legal context (`--trust-proxy` with the list of proxies); a peer outside the list is not trusted."""
    KIT = {"trust_proxy": True, "trusted_proxies": ["127.0.0.1"]}
    TLS = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.9"}

    def test_a_session_signed_in_through_tls_consents_through_tls_and_not_over_plain_http(self) -> None:
        status, data, headers = call(self.port, "POST", "/_api/v1/login", {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}", **self.TLS},
                                     json.dumps({"login": APPROVER_LOGIN, "password": SESSION_PW}).encode())
        self.assertEqual(status, 200, data)
        cookie = [v for k, v in headers.items() if k.lower() == "set-cookie"][0].split(";")[0]
        # kontrakt U-2-3 (D4): through TLS only an `https` Origin counts
        tls = {**self.good, **self.TLS, "Cookie": cookie, "X-Skillsync-CSRF": data["csrf"], "Origin": f"https://127.0.0.1:{self.port}"}
        self.refused({**tls, "Origin": f"http://127.0.0.1:{self.port}"}, 403, "consent_refused")
        plain = {**{k: v for k, v in tls.items() if k not in self.TLS}, "Origin": f"http://127.0.0.1:{self.port}"}   # the cookie of a TLS session (`__Host-`) is not the cookie of a plain local request
        self.refused(plain, 401, "login_required")
        self.assertEqual(self.post(tls, self.offer_data)[0], 200)   # control: through the trusted TLS proxy the same session consents

    def test_only_the_last_forwarded_proto_counts(self) -> None:
        """Kontrakt U-2-3 (D4): the trusted proxy appends its own value; what the client wrote before it does not make the request TLS."""
        status, data, headers = call(self.port, "POST", "/_api/v1/login", {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}", **self.TLS},
                                     json.dumps({"login": APPROVER_LOGIN, "password": SESSION_PW}).encode())
        self.assertEqual(status, 200, data)
        cookie = [v for k, v in headers.items() if k.lower() == "set-cookie"][0].split(";")[0]
        tls = {**self.good, **self.TLS, "Cookie": cookie, "X-Skillsync-CSRF": data["csrf"], "Origin": f"https://127.0.0.1:{self.port}"}
        for value in ("https, http", ["https", "http"], "https,http"):
            with self.subTest(value=value):
                self.refused({**tls, "X-Forwarded-Proto": value}, 403, "consent_context")
        self.assertEqual(self.post({**tls, "X-Forwarded-Proto": ["http", "https"]}, self.offer_data)[0], 200)   # control: the last value is `https`

    def test_forwarded_https_from_a_peer_that_is_not_a_trusted_proxy_is_not_tls(self) -> None:
        self.kit.app.config.proxy_networks = []   # nobody is a trusted proxy
        self.refused({**self.good, **self.TLS}, 403, "consent_context")
        self.consent_works()


class LoginContextProxyHeaderTests(PageCase):
    """The `local` context of the page origin (login and login page, not only the consent) refuses every one of the 19 headers an intermediary adds:
    a password never travels as `local` behind a proxy or a tunnel (K19)."""

    def login_with(self, extra: dict) -> tuple[int, dict, list]:
        headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}", **extra}
        status, data, answered = call(self.port, "POST", "/_api/v1/login", headers, json.dumps({"login": APPROVER_LOGIN, "password": SESSION_PW}).encode())
        return status, data, [value for key, value in answered.items() if key.lower() == "set-cookie"]

    def test_the_list_has_nineteen_headers_and_it_is_one_list(self) -> None:
        self.assertEqual(len(ports.PROXY_HEADERS), 19)
        self.assertEqual(len({name.lower() for name in ports.PROXY_HEADERS}), 19)
        self.assertIs(ports.PROXY_HEADERS, proto_server.PROXY_HEADERS)

    def test_a_login_with_any_of_the_headers_is_refused_and_gets_no_cookie(self) -> None:
        for name in ports.PROXY_HEADERS:
            for spelling in (name, name.lower(), name.upper()):
                for value in ("203.0.113.9", ""):
                    with self.subTest(header=spelling, value=value):
                        status, data, cookies = self.login_with({spelling: value})
                        self.assertEqual((status, cookies), (403, []), data)
                        self.assertIn("secure_connection_required", json.dumps(data))
        status, data, cookies = self.login_with({})   # control: the same login without the header opens a session
        self.assertEqual((status, len(cookies)), (200, 1), data)

    def test_the_login_page_is_refused_with_any_of_the_headers(self) -> None:
        for name in ports.PROXY_HEADERS:
            with self.subTest(header=name):
                self.assertEqual(call(self.port, "GET", "/_login", {name: "203.0.113.9"})[0], 403)
        self.assertEqual(call(self.port, "GET", "/_login")[0], 200)   # control

    def test_mutant_the_short_list_of_headers_lets_a_tunnel_header_sign_in(self) -> None:
        """Mutant: the list the engine had before U-2-2 (`Forwarded`, `X-Real-IP`, `Via`, `X-Forwarded-*`): a login behind a tunnel passes again."""
        short = frozenset(("forwarded", "x-real-ip", "via"))
        with mock.patch.object(proto_server, "_PROXY_HEADERS_LOWER", short):
            status, data, cookies = self.login_with({"CF-Connecting-IP": "203.0.113.9"})
        self.assertEqual((status, len(cookies)), (200, 1), data)
        self.assertEqual(self.login_with({"CF-Connecting-IP": "203.0.113.9"})[0], 403)   # control: the real list refuses it


class ReadSocketAndLegacyClientTests(PageCase):
    def test_the_read_socket_has_no_consent_and_no_login_route(self) -> None:
        reader = srv.make_server(self.board, "127.0.0.1", free_port(), role=ports.ROLE_READ, proto_app=self.kit.app)
        threading.Thread(target=reader.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        self.addCleanup(lambda: (reader.shutdown(), reader.server_close()))
        port = reader.server_address[1]
        offer = self.offer_data
        for path in (ROUTE, "/api/feedback-approve", "/api/plan-status", "/_api/v1/login"):
            with self.subTest(path=path):
                status, answer = self.post({**self.session.headers(port)}, offer, path, port)
                self.assertEqual((status, answer), (404, {"error": "Nie ma takiej strony."}))
        self.assert_no_write()
        self.assertEqual(self.audit("consent_refused"), [])   # a route that does not exist is not a refused consent
        self.consent_works()   # control: the page socket takes the same consent
        _status, data, _ = call(port, "GET", "/api/status", {"Cookie": self.session.pair})
        self.assertEqual(sorted(data["session"]), ["url"])   # and the read socket shows no session either

    def test_an_old_client_with_a_token_gets_a_readable_refusal_at_once_not_a_hang(self) -> None:
        """A client of 1.18 sends `X-Approve-Token` and no session: the old page of a browser (it sends `Sec-Fetch-Site`) gets 401 `login_required`, a script
        without that header gets 403 `consent_refused` (the request is judged before the session); both are `{error, code}` and quick."""
        old = {"Content-Type": "application/json", "X-Status-Board": "1", "X-Approve-Token": TEST_TOKEN, "Origin": f"http://127.0.0.1:{self.port}"}
        for name, headers, status, code in (("old page", {**old, "Sec-Fetch-Site": "same-origin"}, 401, "login_required"),
                                            ("old script", old, 403, "consent_refused")):
            with self.subTest(name=name):
                started = time.monotonic()
                got, answer = self.post(headers, self.offer_data)
                self.assertLess(time.monotonic() - started, 5)
                self.assertEqual((got, answer["code"], sorted(answer)), (status, code, ["code", "error"]))
                self.assertTrue(answer["error"])
                self.assertNotIn(TEST_TOKEN, json.dumps(answer))
                self.assert_no_write()
        self.consent_works()

    def test_a_new_state_file_is_not_a_server_of_1_18_and_has_no_token(self) -> None:
        """An old `dashboard url` takes a record for a server only with a token or with `approvals: false` (the rule of 1.18); a record of this version has neither,
        so the old client says `server_not_running` (a readable BLOCKED) instead of printing a dead address with a token."""
        from skillsync_workflow.dashboard_worktrees import ID_PATTERN
        project = self.tmp / "state-project"
        project.mkdir()
        with mock.patch.dict(os.environ, home_env(self.tmp)):
            path = dashboard_token.write_state(project, 5199, True, server_id="a" * 16, kind="standalone",
                                               tabs=[{"id": "abcdef012345", "path": str(project), "token": TEST_TOKEN, "current": True}])
            record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((record["schema"], record["approvals"]), (2, True))
        self.assertTrue(all(ID_PATTERN.fullmatch(tab["id"]) for tab in record["tabs"]))
        self.assertNotIn("token", record)
        self.assertNotIn("tokenId", record)
        self.assertEqual(record["tabs"], [{"id": "abcdef012345", "path": str(project), "current": True}])   # a token given for a tab is dropped
        self.assertNotIn(TEST_TOKEN, json.dumps(record))
        old_reader_takes = isinstance(record.get("token"), str) and bool(record.get("token")) or record.get("approvals") is False
        self.assertFalse(old_reader_takes)
        legacy = {"schema": 1, "token": "x" * 32, "approvals": True}   # control of the rule itself: a record of 1.18 with a token is taken
        self.assertTrue(isinstance(legacy.get("token"), str) and bool(legacy.get("token")))


class SingleSocketTests(unittest.TestCase):
    """The default mode is one `page` socket on 5140; no consents socket and no token."""

    def test_the_default_is_one_page_socket_and_the_name_of_the_socket_is_known(self) -> None:
        self.assertEqual(ports.DEFAULT_READ_PORT, 5140)
        self.assertFalse(hasattr(ports, "DEFAULT_APPROVE_PORT"))
        self.assertEqual(ports.ROLE_PAGE, "page")
        self.assertIn("page", srv.SSE_ROLE_LIMITS)


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------
# U-2-3: the session between the headers and the write (B1), the time limit of the body, the delivery shown (D6), the audit before the write (D1, D2), what an
# anonymous request sees of the consents (W2), the Host of the page socket (W4)

NEW_PASSWORD = "Calkiem-" + "Nowe-" + "Haslo-" + "2027"


class TwoPartCase(PageCase):
    """The helpers of a consent sent in two parts."""

    def split(self, between, data: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
        return split_post(self.port, ROUTE, headers or self.good, json.dumps(data or self.offer_data).encode("utf-8"), between)

    def logout(self) -> None:
        status, data, _ = session_request(self.port, "POST", "/_api/v1/logout", {}, self.good)
        self.assertLess(status, 300, data)


class WhileTheBodyArrives(TwoPartCase):
    """B1: the consent is checked again right before the write: what ends the session or the right while the body arrives (or while the consent waits for its
    lock) refuses it with the code of that step, and nothing is written. Control: the same two-part request with nothing happening is accepted."""

    def assert_ended(self, between, status: int, code: str) -> None:
        offer = self.offer_data
        got, answer = self.split(between, offer)
        self.assertEqual((got, answer.get("code"), sorted(answer)), (status, code, ["code", "error"]), answer)
        self.assert_no_write()
        self.assertEqual(self.audit("consent"), [])
        self.assertEqual([line["code"] for line in self.audit("consent_refused")], [code])

    def edit_account(self, **changes) -> None:
        users = Path(self.kit.users_path)
        record = json.loads(users.read_text(encoding="utf-8"))
        for item in record["users"]:
            if item["login"] == APPROVER_LOGIN:
                item.update(changes)
        users.write_text(json.dumps(record, indent=1), encoding="utf-8")

    def test_control_the_two_part_request_with_nothing_in_between_is_accepted(self) -> None:
        status, answer = self.split(None)
        self.assertEqual(status, 200, answer)
        self.assertEqual(len(self.entries(RUN2)), 1)

    def test_a_logout_while_the_body_arrives_is_401_and_writes_nothing(self) -> None:
        self.assert_ended(self.logout, 401, "session_expired")

    def test_a_password_change_while_the_body_arrives_is_401_and_writes_nothing(self) -> None:
        def change() -> None:
            status, data, _ = session_request(self.port, "POST", "/_api/v1/password", {"current": SESSION_PW, "new": NEW_PASSWORD}, self.good)
            self.assertLess(status, 300, data)
        self.assert_ended(change, 401, "session_expired")

    def test_an_expiry_while_the_body_arrives_is_401_and_writes_nothing(self) -> None:
        real = self.kit.app.sessions.clock

        def later() -> None:
            self.kit.app.sessions.clock = lambda: real() + 13 * 3600   # past the maximum of an approver session
        self.assert_ended(later, 401, "session_expired")

    def test_a_removed_role_while_the_body_arrives_is_403_and_writes_nothing(self) -> None:
        self.assert_ended(lambda: self.edit_account(role="commenter", roles=["commenter"]), 403, "forbidden_role")

    def test_a_password_to_change_while_the_body_arrives_is_403_and_writes_nothing(self) -> None:
        self.assert_ended(lambda: self.edit_account(mustChange=True), 403, "password_change_required")

    def test_consents_switched_off_while_the_body_arrives_is_403_and_writes_nothing(self) -> None:
        self.assert_ended(lambda: setattr(self.board, "approvals", False), 403, "approvals_disabled")

    def test_a_logout_while_the_consent_waits_for_its_lock_is_401_and_writes_nothing(self) -> None:
        offer, outcome = self.offer_data, []
        with self.board._approve_lock:
            thread = threading.Thread(target=lambda: outcome.append(self.post(self.good, offer)))
            thread.start()
            time.sleep(0.5)   # the request passed the gate and read its body; it waits for the lock
            self.assertEqual(outcome, [], "control: the consent really waits")
            self.logout()
        thread.join(timeout=15)
        self.assertEqual((outcome[0][0], outcome[0][1].get("code")), (401, "session_expired"), outcome)
        self.assert_no_write()


class BodyTimeLimitTests(PageCase):
    """B1: ONE time limit for the whole body (`BODY_DEADLINE_S`); a client that sends a byte at a time does not extend it."""

    def trickle(self, pieces: list[bytes], pause: float, length: int) -> tuple[int, dict, float]:
        sent = {**self.good, "Host": f"127.0.0.1:{self.port}", "Content-Length": str(length), "Connection": "close"}
        head = f"POST {ROUTE} HTTP/1.1\r\n" + "".join(f"{key}: {value}\r\n" for key, value in sent.items()) + "\r\n"
        started, raw = time.monotonic(), b""
        with socket.create_connection(("127.0.0.1", self.port), timeout=15) as sock:
            sock.sendall(head.encode("latin-1"))
            for piece in pieces:
                if select.select([sock], [], [], pause)[0]:   # the answer came before the body was complete
                    break
                try:
                    sock.sendall(piece)
                except OSError:
                    break
            while chunk := sock.recv(65536):
                raw += chunk
        elapsed = time.monotonic() - started
        status_line, _, rest = raw.partition(b"\r\n")
        return int(status_line.split()[1]), json.loads(rest.partition(b"\r\n\r\n")[2] or b"{}"), elapsed

    def test_a_body_that_trickles_past_the_limit_is_408_and_writes_nothing(self) -> None:
        self.assertEqual(srv.BODY_DEADLINE_S, 10.0)
        offer = self.offer_data
        body = json.dumps(offer).encode("utf-8")
        with mock.patch.object(srv, "BODY_DEADLINE_S", 1.0):
            status, answer, elapsed = self.trickle([body[i:i + 1] for i in range(len(body))], 0.25, len(body))   # every byte well within the limit
        self.assertEqual((status, answer.get("code")), (408, "body_timeout"), answer)
        self.assertLess(elapsed, 4.0, "the limit is for the whole body, not for one read")
        self.assert_no_write()
        with mock.patch.object(srv, "BODY_DEADLINE_S", 3.0):   # control: the same body in pieces within the limit
            status, answer, _ = self.trickle([body[:10], body[10:20], body[20:]], 0.2, len(body))
        self.assertEqual(status, 200, answer)


class DeliveryShownTests(PageCase):
    """D6: a CP carries the branch and the target the page showed."""

    def test_a_cp_without_the_branch_and_the_target_is_400_and_writes_nothing(self) -> None:
        offer = self.cp_offer()
        cases = {"no branch": {k: v for k, v in offer.items() if k != "branch"}, "no target": {k: v for k, v in offer.items() if k != "target"},
                 "neither": {k: v for k, v in offer.items() if k not in ("branch", "target")},
                 "no mode": {k: v for k, v in offer.items() if k != "mode"}, "a mode of another type": {**offer, "mode": 1},
                 "a number": {**offer, "branch": 5}, "a list": {**offer, "target": ["main"]}}
        for name, data in cases.items():
            with self.subTest(name=name):
                status, answer = self.post(self.good, data)
                self.assertEqual((status, answer.get("code")), (400, "badRequest"), answer)
        self.record.assert_not_called()
        self.assertEqual(self.post(self.good, offer)[0], 200)   # control

    def test_another_branch_or_target_than_the_current_one_is_409_delivery_changed(self) -> None:
        offer = self.cp_offer()
        for name, data in (("branch", {**offer, "branch": "feature/other"}), ("target", {**offer, "target": "release/9"}),
                           ("null branch", {**offer, "branch": None}), ("mode", {**offer, "mode": "pr" if offer["mode"] != "pr" else "direct"})):
            with self.subTest(name=name):
                status, answer = self.post(self.good, data)
                self.assertEqual((status, answer.get("code")), (409, "delivery_changed"), answer)
        self.record.assert_not_called()
        self.assertEqual(self.entries(RUN1), [])
        self.assertEqual(self.post(self.good, offer)[0], 200)   # control: what the page showed


class AuditBeforeTheWriteTests(PageCase):
    """D1: the `consent` line is written before the consent and a consent without it does not happen; D2: every refusal after the gate has a `code` and a line."""

    def test_the_line_has_the_word_path_run_sha_tab_and_session_and_exists_before_the_write(self) -> None:
        offer = self.cp_offer()
        seen: list = []
        real = srv.status_core.record_approval

        def spying(*args, **kwargs):
            seen.extend(self.audit("consent"))
            return real(*args, **kwargs)
        with mock.patch.object(srv.status_core, "record_approval", spying):
            self.assertEqual(self.post(self.good, offer)[0], 200)
        self.assertEqual(len(seen), 1, "the audit line was there when the writer was called")
        line = seen[0]
        self.assertEqual((line["word"], line["path"], line["run"], line["sha"], line["route"]), ("CP", PLAN, RUN1, offer["sha"], ROUTE))
        self.assertEqual((line["account"], line["tab"], line["session"]),
                         (self.kit.account(APPROVER_LOGIN), self.server.RequestHandlerClass.tabs.default_id, self.entries(RUN1)[-1]["session"]))
        self.assertTrue(line["at"] and line["source"])

    def test_the_line_of_a_go_names_the_document(self) -> None:
        offer = self.offer_data
        self.assertEqual(self.post(self.good, offer)[0], 200)
        line = self.audit("consent")[-1]
        self.assertEqual((line["word"], line["path"], line["run"], line["sha"]), ("GO plan", SPEC2, RUN2, offer["sha"]))

    def test_an_audit_that_cannot_be_written_refuses_the_consent_with_503_and_nothing_is_written(self) -> None:
        for name, patch in (("append", lambda: mock.patch.object(proto_server, "link_audit_append", side_effect=OSError("disk"))),
                            ("directory", lambda: mock.patch.object(proto_server.PrototypeApp, "ensure_audit", return_value="no run directory"))):
            offer = self.offer_data
            with self.subTest(name=name), patch():
                status, answer = self.post(self.good, offer)
                self.assertEqual((status, answer.get("code"), sorted(answer)), (503, "audit_unavailable", ["code", "error"]), answer)
                self.assert_no_write()
        self.consent_works()   # control

    def test_a_cp_without_its_audit_line_is_not_written(self) -> None:
        offer = self.cp_offer()
        with mock.patch.object(proto_server, "link_audit_append", side_effect=OSError("disk")):
            status, answer = self.post(self.good, offer)
        self.assertEqual((status, answer.get("code")), (503, "audit_unavailable"), answer)
        self.record.assert_not_called()
        self.assertEqual(self.entries(RUN1), [])
        self.assertEqual(self.post(self.good, offer)[0], 200)

    def test_every_refusal_after_the_gate_has_a_code_and_an_audit_line_with_route_and_tab(self) -> None:
        offer = self.cp_offer()
        tab = self.server.RequestHandlerClass.tabs.default_id
        cases = [({**offer, "sha": "0" * 14}, 409, "headChanged"), ({**offer, "tab": "abcdef012345"}, 404, "unknownTab"),
                 ({**offer, "branch": "x/y"}, 409, "delivery_changed"), ({**offer, "kind": "rm"}, 400, "badRequest")]
        for data, status, code in cases:
            with self.subTest(code=code):
                got, answer = self.post(self.good, data)
                self.assertEqual((got, answer.get("code")), (status, code), answer)
                line = [line for line in self.audit("consent_refused") if line.get("code") == code][-1]
                self.assertEqual((line["http"], line["route"], line["account"]), (status, ROUTE, self.kit.account(APPROVER_LOGIN)))
                self.assertEqual(line["tab"], data.get("tab", tab))

    def test_a_refusal_is_folded_only_with_the_same_route_and_tab(self) -> None:
        anonymous = {k: v for k, v in self.good.items() if k not in ("Cookie", "X-Skillsync-CSRF")}
        for path in (ROUTE, "/api/feedback-approve", "/api/plan-status", ROUTE):
            self.post(anonymous, {}, path)
        self.assertEqual(sorted(line["route"] for line in self.audit("consent_refused")), ["/api/approve", "/api/feedback-approve", "/api/plan-status"])
        offer = self.offer_data
        for tab in ("abcdef012345", "abcdef012346", "abcdef012345"):
            self.post(self.good, {**offer, "tab": tab})
        self.assertEqual(sorted(line["tab"] for line in self.audit("consent_refused") if line.get("code") == "unknownTab"), ["abcdef012345", "abcdef012346"])


CANARY = {name: f"canary-{name}-7f3a" for name in ("by", "session", "role", "worktree", "peer", "host", "note")}


class ConsentedRunCase(PageCase):
    """A CP already given for the HEAD, with a canary in every field that must not be public."""
    ALLOWED = {"word", "at", "sha", "source", "target", "planState"}

    def setUp(self) -> None:
        super().setUp()
        offer = self.cp_offer()
        entry = {"schema": 1, "at": "2026-01-10T11:30:00Z", "word": "CP", "sha": offer["sha"], "source": "dashboard", "planState": "in_progress",
                 "by": CANARY["by"], "session": CANARY["session"], "role": CANARY["role"], "worktree": CANARY["worktree"],
                 "via": {"port": 1, "peer": CANARY["peer"], "host": CANARY["host"]}, "note": CANARY["note"]}
        (self.root / RUN / "approvals.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")
        self.sha = offer["sha"]

    def status_of(self, headers: dict | None = None, port: int | None = None) -> dict:
        status, data, _ = call(port or self.port, "GET", f"/api/status?plan={PLAN}", headers or {})
        self.assertEqual(status, 200, data)
        return data

    def given(self, data: dict) -> dict:
        return next(action["given"] for action in data["approvals"]["actions"] if action["kind"] == "cp")

    def assert_redacted(self, data: dict) -> None:
        for entry in (data["approvals"]["last"], self.given(data)):
            self.assertEqual((entry["word"], entry["sha"], entry["source"]), ("CP", self.sha, "dashboard"))   # the allowed fields are there
            self.assertLessEqual(set(entry), self.ALLOWED, entry)
        for action in data["approvals"]["actions"]:
            self.assertLessEqual(set(action.get("given") or {}), self.ALLOWED, action)
        text = json.dumps(data, ensure_ascii=False)
        for value in CANARY.values():
            self.assertNotIn(value, text)



class AnonymousApprovalViewTests(ConsentedRunCase):
    """W2: of a consent already given, a request without an `approver` session sees only word, at, sha, source, target and planState."""

    def test_an_anonymous_request_sees_only_the_allowed_fields(self) -> None:
        self.assert_redacted(self.status_of())
        _status, stale = self.get(f"/api/status?plan={PLAN}", {"Cookie": self.session.stale_pair})
        self.assert_redacted(stale)

    def test_a_session_without_the_approver_role_sees_only_the_allowed_fields(self) -> None:
        for name, made in (("commenter", self.kit.login(self.port, COMMENTER_LOGIN, SESSION_PW_COMMENTER)),
                           ("acceptor+developer", self.kit.login(self.port, ACCEPTOR_LOGIN, SESSION_PW_COMMENTER)),
                           ("link", self.kit.session_via(self.port, APPROVER_LOGIN, "link")),
                           ("handover", self.kit.session_via(self.port, APPROVER_LOGIN, "handoff"))):
            with self.subTest(name=name):
                self.assert_redacted(self.status_of({"Cookie": made.pair}))

    def test_control_an_approver_session_sees_who_consented(self) -> None:
        data = self.status_of({"Cookie": self.session.pair})
        for entry in (data["approvals"]["last"], self.given(data)):
            self.assertEqual((entry["by"], entry["session"], entry["via"]["peer"], entry["worktree"]),
                             (CANARY["by"], CANARY["session"], CANARY["peer"], CANARY["worktree"]))

    def test_the_read_socket_sees_only_the_allowed_fields_even_with_an_approver_cookie(self) -> None:
        reader = srv.make_server(self.board, "127.0.0.1", free_port(), role=ports.ROLE_READ, proto_app=self.kit.app)
        threading.Thread(target=reader.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        self.addCleanup(lambda: (reader.shutdown(), reader.server_close()))
        self.assert_redacted(self.status_of({"Cookie": self.session.pair}, reader.server_address[1]))

    def test_the_tabs_and_the_history_carry_no_consent_entry(self) -> None:
        for path in ("/api/tabs", "/api/history"):
            with self.subTest(path=path):
                status, data = self.get(path)
                self.assertEqual(status, 200, data)
                for value in CANARY.values():
                    self.assertNotIn(value, json.dumps(data))


class StarReadHostOnThePageSocketTests(PageCase):
    """W4: `--read-host '*'` opens the read socket only; the one `page` socket (it consents) keeps judging the Host."""
    READ_HOSTS = ("*", "viewer.example")

    def test_a_foreign_host_is_refused_on_the_page_socket_and_open_on_the_read_socket(self) -> None:
        for path in ("/", "/api/status", "/_api/v1/session"):
            with self.subTest(path=path):
                self.assertEqual(call(self.port, "GET", path, {"Host": "evil.example"})[0], 403)
        self.assertEqual(call(self.port, "GET", "/api/status", {"Host": "viewer.example"})[0], 200)   # control: a listed name is served
        reader = srv.make_server(self.board, "127.0.0.1", free_port(), role=ports.ROLE_READ, proto_app=self.kit.app)
        threading.Thread(target=reader.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        self.addCleanup(lambda: (reader.shutdown(), reader.server_close()))
        self.assertEqual(call(reader.server_address[1], "GET", "/api/status", {"Host": "evil.example"})[0], 200)   # control: `*` on the read socket
        status, _answer = self.post({**self.good, "Host": "evil.example", "Origin": "http://evil.example"}, self.offer_data)
        self.assertEqual(status, 403)
        self.assert_no_write()


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------
# Mutants (monkeypatch, no edit of the code): with the guard broken the refusal turns into an accepted consent, so the assertions above can fail.

class MutationControlTests(PageCase):
    APPROVE_HOSTS = ("lan-host",)

    def accepted(self, headers: dict, data: dict | None = None) -> int:
        return self.post(headers, data if data is not None else self.offer_data)[0]

    def fresh_offer(self) -> dict:
        """A new offer for every probe (a consent that went through used the sha of the previous one)."""
        return self.spec_offer()

    def case_wrong_role(self) -> tuple[dict, int]:
        commenter = self.kit.login(self.port, COMMENTER_LOGIN, SESSION_PW_COMMENTER)
        return commenter.headers(self.port), 403

    def test_control_every_probe_is_refused_by_the_unmutated_code(self) -> None:
        link = self.kit.session_via(self.port, APPROVER_LOGIN, "link").headers(self.port)
        commenter = self.case_wrong_role()[0]
        no_csrf = {**self.good, "X-Skillsync-CSRF": "x" * len(self.session.csrf)}
        proxy = {**self.good, "X-Forwarded-For": "10.0.0.9"}
        lan = {**self.good, "Host": f"lan-host:{self.port}", "Origin": f"http://lan-host:{self.port}"}
        for name, headers in (("link", link), ("commenter", commenter), ("csrf", no_csrf), ("proxy", proxy), ("network", lan)):
            with self.subTest(name=name):
                self.assertIn(self.accepted(headers), (401, 403))
        self.assert_no_write()
        self.assertEqual(self.accepted(self.good), 200)

    def test_mutant_the_role_check_is_skipped(self) -> None:
        headers = self.case_wrong_role()[0]
        offer = self.offer_data
        with mock.patch.object(proto_server.PrototypeApp, "effective_roles", lambda app, user, session=None: [proto_server.APPROVER]):
            status, answer = self.post(headers, offer)
        self.assertEqual(status, 200, "the mutant must turn the refusal `forbidden_role` into a consent")
        self.assertEqual(self.entries(RUN2)[-1]["role"], "approver")

    def test_mutant_the_csrf_is_not_compared(self) -> None:
        wrong = {**self.good, "X-Skillsync-CSRF": "x" * len(self.session.csrf)}
        offer = self.offer_data
        with mock.patch.object(srv.hmac, "compare_digest", lambda left, right: True):
            status, _answer = self.post(wrong, offer)
        self.assertEqual(status, 200, "the mutant must turn the refusal of a wrong CSRF into a consent")

    def test_mutant_the_local_or_tls_context_is_not_checked(self) -> None:
        proxy = {**self.good, "X-Forwarded-For": "10.0.0.9"}
        offer = self.offer_data
        with mock.patch.object(srv.Handler, "_consent_context_ok", lambda handler: True):
            status, _answer = self.post(proxy, offer)
        self.assertEqual(status, 200, "the mutant must turn `consent_context` into a consent")

    def test_mutant_a_login_link_session_counts_as_an_approver(self) -> None:
        link = self.kit.session_via(self.port, APPROVER_LOGIN, "link").headers(self.port)
        offer = self.offer_data
        real = proto_server.PrototypeApp.effective_roles
        with mock.patch.object(proto_server.PrototypeApp, "effective_roles", lambda app, user, session=None: real(app, user, None)):   # ignores how the session began
            status, _answer = self.post(link, offer)
        self.assertEqual(status, 200, "the mutant must turn the refusal of a link session into a consent")

    def test_mutant_a_refusal_is_not_audited(self) -> None:
        anonymous = {k: v for k, v in self.good.items() if k not in ("Cookie", "X-Skillsync-CSRF")}
        real = srv.Handler._consent_audit

        def silent(handler, event, user=None, **fields):
            if event != "consent_refused":
                real(handler, event, user, **fields)

        with mock.patch.object(srv.Handler, "_consent_audit", silent):
            self.post(anonymous, self.offer_data)
        self.assertEqual(self.audit("consent_refused"), [], "the mutant leaves no audit line of the refusal")
        self.post({**anonymous, "X-Status-Board": "0"}, self.offer_data)   # control: the unmutated code writes it
        self.assertEqual(len(self.audit("consent_refused")), 1)


class RecheckMutationTests(TwoPartCase):
    """Mutant: the session is not checked again before the write: the logout in between no longer stops the consent."""

    def test_mutant_no_second_check_of_the_session(self) -> None:
        with mock.patch.object(srv.Handler, "_consent_recheck", lambda handler, gate: None):
            status, answer = self.split(self.logout)
        self.assertEqual(status, 200, "the mutant must let the consent of a logged-out session through")
        self.assertEqual(len(self.entries(RUN2)), 1)


class RedactionMutationTests(ConsentedRunCase):
    """Mutant: the anonymous view is not redacted: the canaries show (the assertions above can fail)."""

    def test_mutant_the_anonymous_view_is_not_redacted(self) -> None:
        with mock.patch.object(srv, "public_approvals", lambda approvals: dict(approvals)):
            data = self.status_of()
        with self.assertRaises(AssertionError):
            self.assert_redacted(data)
        self.assertIn(CANARY["by"], json.dumps(data))
        self.assert_redacted(self.status_of())   # control: the real code


if __name__ == "__main__":
    unittest.main()
