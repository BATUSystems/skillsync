"""CORE-2000-UNIFIED end to end: where a password and a consent may come from (K19, on the origin of the page), cookies behind TLS and the frame
headers (K13, K15), the login limits (K14): python3 -m unittest tests.test_skillsync_unified_network

A real `skillsync dashboard serve` process. TLS is what 2.0.0 offers: a trusted proxy; the test is that proxy (it connects from the loopback and
sends the forwarded headers). A peer outside the loopback is real too: the server listens on a non-loopback address of this machine and the test
connects to it (skipped without one). Every refusal has its control: the same request from an allowed place succeeds."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from serve_start import state_files  # noqa: E402
from unified_helpers import (  # noqa: E402
    APPROVER, APPROVER_ACCOUNT, APPROVER_PASSWORD, COMMENTER, COMMENTER_PASSWORD, LAYER, RUN_ID, SPEC2, Browser, UnifiedCase, free_port,
    lan_address, self_reachable)

PUBLIC_HOST = "status.example.test"
PROXY = ("--trust-proxy", "--proxy-address", "127.0.0.1", "--public-url", f"https://{PUBLIC_HOST}")
WRONG = "-".join(("zle", "haslo", "do", "proby", "123456"))
ACCOUNTS = ((APPROVER, "approver", APPROVER_PASSWORD, APPROVER_ACCOUNT), (COMMENTER, "commenter", COMMENTER_PASSWORD, None))
NO_LAN = "brak adresu IPv4 spoza loopbacku, z którym ta maszyna połączy się sama (nie da się wystąpić jako peer z sieci)"


class NetworkCase(UnifiedCase):
    ACCOUNTS = ACCOUNTS

    def setUp(self) -> None:
        super().setUp()
        self.session_waits_for("CP")

    def cp_body(self, browser) -> dict:
        offer = browser.offer("cp")
        self.assertEqual(offer["state"], "waiting", offer)
        delivery = browser.status()["delivery"]
        return {"kind": "cp", "path": RUN_ID, "sha": offer["sha"], "branch": delivery["branch"], "target": delivery["target"], "mode": delivery["mode"],
                "planState": offer.get("planState")}

    def assert_code(self, reply, status: int, code: str) -> None:
        """A refusal of a session route says it in `error`, a refusal of a consent in `code`."""
        self.assertEqual((reply.status, reply.data.get("code") or reply.data.get("error")), (status, code), reply.text)


class StartRefusalTests(NetworkCase):
    def test_trust_proxy_without_the_list_of_proxies_does_not_start(self) -> None:
        port = free_port()
        done = self.cli("dashboard", "serve", str(self.project), "--port", str(port), "--trust-proxy", *LAYER, timeout=60)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertIn("proxy", report["reason"].lower())
        self.assertEqual(state_files(self.env), [])
        server = self.serve("--trust-proxy", "--proxy-address", "127.0.0.1", mode="port")   # the control: with the list it starts
        self.assertEqual(server.browser().get("/healthz").status, 200)


class TrustedProxyTests(NetworkCase):
    """TLS through a trusted proxy."""

    def through_tls(self, server, source: str = "203.0.113.7", proto: str | None = "https") -> Browser:
        forwarded = {"X-Forwarded-For": source}
        if proto is not None:
            forwarded["X-Forwarded-Proto"] = proto
        return Browser(server.port, host=PUBLIC_HOST, scheme="https", extra=forwarded)

    def test_login_and_consent_through_tls_with_a_secure_host_cookie(self) -> None:
        server = self.serve(*PROXY)
        browser = self.through_tls(server)
        reply = browser.login()
        self.assertEqual(reply.status, 200, reply.text)
        attributes = [item.strip() for item in reply.set_cookie.split(";")]
        self.assertRegex(attributes[0], r"^__Host-skillsync_page_[0-9a-f]{8}=")
        self.assertEqual(attributes[1:], ["HttpOnly", "SameSite=Strict", "Path=/", f"Max-Age={12 * 3600}", "Secure"])
        body = self.cp_body(browser)
        before = self.snapshot()
        plain_origin = browser.post("/api/approve", body, browser.post_headers(Origin=f"http://{PUBLIC_HOST}"))
        self.assert_code(plain_origin, 403, "consent_refused")   # through TLS only the `https` origin counts
        downgraded = browser.clone()
        downgraded.extra["X-Forwarded-Proto"] = "http"
        self.assert_code(downgraded.post("/api/approve", body), 403, "consent_context")   # the same cookie, forwarded as plain HTTP
        self.assertEqual(self.snapshot(), before)
        done = browser.post("/api/approve", body)
        self.assertEqual(done.status, 200, done.text)
        row, = self.rows()
        self.assertEqual((row["by"], row["role"], row["via"]["host"]), (APPROVER_ACCOUNT, "approver", PUBLIC_HOST))
        self.assertEqual(row["via"]["peer"], "127.0.0.1")   # the peer of the socket, never the forwarded header
        self.assertEqual({item["source"] for item in self.audit() if item["event"] in ("login", "consent")}, {"203.0.113.7"})

    def test_forwarded_plain_http_signs_nobody_in(self) -> None:
        server = self.serve(*PROXY)
        browser = self.through_tls(server, proto="http")
        browser.scheme = "http"
        self.assert_code(browser.login(), 403, "secure_connection_required")
        self.assertEqual(browser.get("/_login").status, 403)
        self.assertEqual(browser.get("/api/status").status, 200)   # reading stays open
        self.assertEqual(self.through_tls(server).login().status, 200)

    def test_failures_of_one_source_do_not_lock_the_approver_out_elsewhere(self) -> None:
        server = self.serve(*PROXY, "--verbose")
        attacker = self.through_tls(server, "198.51.100.66")
        for attempt in range(5):
            self.assert_code(attacker.login(APPROVER, WRONG), 401, "invalid_credentials")
        limited = attacker.login(APPROVER, WRONG)
        self.assert_code(limited, 429, "rate_limited")
        self.assertTrue(0 < int(limited.header("Retry-After")) <= 15 * 60)
        self.assert_code(attacker.login(APPROVER, APPROVER_PASSWORD), 429, "rate_limited")   # the right password does not open a limited pair
        spoofed = self.through_tls(server, "192.0.2.200, 198.51.100.66")   # the client's own hop on the left changes nothing
        self.assert_code(spoofed.login(APPROVER, APPROVER_PASSWORD), 429, "rate_limited")

        elsewhere = self.through_tls(server, "203.0.113.7")
        self.assertEqual(elsewhere.login().status, 200)
        done = elsewhere.post("/api/approve", self.cp_body(elsewhere))
        self.assertEqual(done.status, 200, done.text)

        events = [item["event"] for item in self.audit()]
        self.assertIn("approver_alert", events)
        self.assertIn("login_limited", events)
        alerts = elsewhere.get("/_api/v1/session").data["server"]["alerts"]
        self.assertEqual([item["account"] for item in alerts][:1], [APPROVER_ACCOUNT])
        self.assertNotIn("alerts", self.through_tls(server, "203.0.113.8").get("/_api/v1/session").data["server"])
        everything = server.log() + json.dumps(self.audit()) + json.dumps(self.rows())
        self.assertIn("/_api/v1/login", server.log(), "the verbose log has no request lines: the next assertions would be empty")
        for secret in (WRONG, APPROVER_PASSWORD, elsewhere.token, elsewhere.csrf):
            self.assertNotIn(secret, everything)

    def test_ipv6_sources_are_limited_per_64_and_unknown_logins_look_alike(self) -> None:
        server = self.serve(*PROXY)
        first = self.through_tls(server, "2001:db8:1:2::1")
        known = first.login(COMMENTER, WRONG)
        unknown = first.login("nobody@example.com", WRONG)
        self.assertEqual((known.status, known.data), (unknown.status, unknown.data))
        self.assertEqual(known.status, 401)
        for attempt in range(4):
            self.assertEqual(first.login(COMMENTER, WRONG).status, 401)
        self.assert_code(self.through_tls(server, "2001:db8:1:2:ffff::9").login(COMMENTER, COMMENTER_PASSWORD), 429, "rate_limited")
        self.assertEqual(self.through_tls(server, "2001:db8:1:3::1").login(COMMENTER, COMMENTER_PASSWORD).status, 200)


class UntrustedForwardingTests(NetworkCase):
    """Forwarded headers of a peer that is not a trusted proxy buy nothing; a `Host` that is not the loopback is not local."""

    def check_refused(self, server) -> None:
        local = server.browser()
        self.assertEqual(local.login().status, 200)
        body = self.cp_body(local)
        before = self.snapshot()
        claimed = local.clone()
        claimed.extra = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.7"}
        self.assert_code(claimed.post("/api/approve", body), 403, "consent_context")
        self.assertEqual(self.snapshot(), before)
        stranger = server.browser()
        stranger.extra = dict(claimed.extra)
        self.assert_code(stranger.login(), 403, "secure_connection_required")
        for header in ({"X-Forwarded-Proto": "https"}, {"Forwarded": "proto=https"}, {"X-Real-IP": "203.0.113.7"}):
            other = server.browser()
            other.extra = header
            with self.subTest(header=header):
                self.assert_code(other.login(), 403, "secure_connection_required")
        self.assertEqual(local.post("/api/approve", body).status, 200)   # the control: the same session without the headers

    def test_without_trust_proxy(self) -> None:
        self.check_refused(self.serve())

    def test_with_a_proxy_list_that_does_not_name_the_peer(self) -> None:
        self.check_refused(self.serve("--trust-proxy", "--proxy-address", "192.0.2.10"))

    def test_a_name_that_is_not_the_loopback_is_not_local(self) -> None:
        server = self.serve("--read-host", "viewer.example.test", mode="port")
        local = server.browser()
        self.assertEqual(local.login().status, 200)
        body = self.cp_body(local)
        named = local.clone()
        named.host = f"viewer.example.test:{server.port}"
        self.assertEqual(named.get("/api/status").status, 200)
        before = self.snapshot()
        self.assert_code(named.post("/api/approve", body), 403, "consent_context")
        self.assert_code(server.browser(host=named.host).login(), 403, "secure_connection_required")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(local.post("/api/approve", body).status, 200)


class FrameHeaderTests(NetworkCase):
    def test_the_page_and_the_login_form_cannot_be_framed(self) -> None:
        server = self.serve()
        browser = server.browser()
        for path in ("/", "/_login"):
            with self.subTest(path=path):
                reply = browser.get(path)
                self.assertEqual(reply.status, 200)
                policy = reply.header("Content-Security-Policy")
                directives = {part.split()[0]: part.split()[1:] for part in policy.split(";") if part.strip()}
                self.assertEqual(directives["frame-ancestors"], ["'none'"])
                self.assertEqual(reply.header("X-Frame-Options"), "DENY")
                self.assertNotIn("'unsafe-inline'", directives["script-src"])
                self.assertIn("no-store", reply.header("Cache-Control"))


LAN = lan_address()   # once: the default route can move (a VPN coming up) while the tests run


@unittest.skipUnless(LAN, NO_LAN)
class NetworkPeerTests(NetworkCase):
    """A peer that is not the loopback, over plain HTTP."""

    def serve_lan(self, *extra: str):
        if not self_reachable(LAN):
            self.skipTest(NO_LAN)
        return self.serve(*extra, address=LAN)

    def test_the_network_reads_but_neither_signs_in_nor_consents(self) -> None:
        server = self.serve_lan()
        remote = server.browser()
        self.assertEqual(remote.get("/api/status").status, 200)
        session = remote.get("/_api/v1/session").data["server"]
        self.assertEqual((session["connection"], session["loginAllowed"]), ("plain", False))
        self.assertEqual(remote.get("/_login").status, 403)
        for login, password in ((APPROVER, APPROVER_PASSWORD), (COMMENTER, COMMENTER_PASSWORD)):
            self.assert_code(remote.login(login, password), 403, "secure_connection_required")
        claimed = server.browser(host=f"127.0.0.1:{server.port}")   # a loopback `Host` from a peer of the network
        self.assert_code(claimed.login(), 403, "secure_connection_required")
        before = self.snapshot()
        self.assert_code(remote.post("/api/approve", self.cp_body(remote)), 403, "consent_context")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.rows(), [])

    def test_allow_plain_login_admits_commenters_only(self) -> None:
        server = self.serve_lan("--allow-plain-login")
        approver = server.browser()
        self.assert_code(approver.login(), 403, "secure_connection_required")
        self.assertIsNone(approver.cookie)
        commenter = server.browser()
        self.assertEqual(commenter.login(COMMENTER, COMMENTER_PASSWORD).status, 200)
        self.assert_code(commenter.post("/api/approve", self.cp_body(commenter)), 403, "consent_context")
        self.assertEqual(self.rows(), [])

    def test_local_only_refuses_the_network_peer_on_every_route(self) -> None:
        server = self.serve_lan("--local-only")
        remote = server.browser()
        for path in ("/", "/api/status", "/healthz", "/_login", "/_api/v1/session", "/events"):
            with self.subTest(path=path):
                self.assertEqual(remote.get(path).status, 403)
        self.assert_code(remote.login(), 403, "local_only")


if __name__ == "__main__":
    unittest.main()
