"""CORE-2000-UNIFIED, findings of the preliminary review (S1, S2, S4b): the handover does not keep an `approver` session alive, a right password in a channel
an account may not sign in through counts as an attempt, and a trusted proxy counts as TLS only when it says `https`:
python3 -m unittest tests.test_skillsync_unified_login_channels

The servers run in this process (the harness of `test_skillsync_prototype_handoff`), with a clock of the test."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_skillsync_prototype_handoff as flow  # noqa: E402  (the module, so its test classes are not collected here)

ps = flow.ps
HOUR = 3600
WRONG = "-".join(("Zupelnie", "Bledne", "Haslo", "Proby", "31"))
APPROVER = "ap@example.com"
ACCEPTOR = "acc@example.com"
COMMENTER = "com@example.com"


class Case(unittest.TestCase):
    def env(self, **kw):
        return flow.Env(self, **kw)

    @staticmethod
    def sign_in(env, request, login: str, password: str):
        reply = request("POST", "/_api/v1/login", body={"login": login, "password": password})
        return reply.status, (reply.json().get("error") if reply.body else None)


class IdleTests(Case):
    """S1: the idle limit of an `approver` session counts from what the person writes, not from the handover the browser does by itself."""

    def signed_in(self, env, who) -> bool:
        reply = env.page_req("GET", "/_api/v1/session", headers={"Cookie": who.pair})
        return bool(reply.json().get("user"))

    def test_the_handover_does_not_extend_the_idle_time_of_an_approver(self) -> None:
        env = self.env()
        who = env.login(APPROVER, flow.PW_APPROVER)
        env.clock.advance(2 * HOUR - 60)
        self.assertEqual(env.issue(who).status, 200, "the handover itself works")
        env.clock.advance(120)
        self.assertFalse(self.signed_in(env, who), "two hours without a write of the person: the session ends")

    def test_control_without_the_wait_the_session_lives(self) -> None:
        env = self.env()
        who = env.login(APPROVER, flow.PW_APPROVER)
        env.clock.advance(2 * HOUR - 120)
        self.assertEqual(env.issue(who).status, 200)
        env.clock.advance(60)
        self.assertTrue(self.signed_in(env, who))


class RefusedChannelTests(Case):
    """S2: where an account may not sign in, a right password is an attempt like a wrong one: it counts, it stops at the limit and it raises
    the alert of an `approver`. The answers stay as the spec says (a right password: 403 with the way to the page; a wrong one: 401, the same
    as for an unknown address)."""

    def assert_limited(self, env, request, login: str, password: str, code: str) -> None:
        seen = [self.sign_in(env, request, login, password) for _ in range(ps.RATE_PAIR_LIMIT)]
        self.assertEqual(seen, [(403, code)] * ps.RATE_PAIR_LIMIT, "the answer of the spec, up to the limit")
        self.assertEqual(self.sign_in(env, request, login, password), (429, "rate_limited"), "a right password does not clear the count")
        self.assertEqual(self.sign_in(env, request, login, WRONG), (429, "rate_limited"))

    def alerts(self, env) -> list:
        return [(item["account"], item["failures"]) for item in env.app.alerts]

    def test_an_approver_on_a_server_that_is_not_the_page(self) -> None:
        env = self.env(page_url=False, prototype_url=False)
        self.assert_limited(env, env.proto_req, APPROVER, flow.PW_APPROVER, "approver_login_elsewhere")
        self.assertTrue(self.alerts(env), "a series of them alerts the approvers")
        self.assertEqual(self.sign_in(env, env.proto_req, COMMENTER, flow.PW_COMMENTER)[0], 200, "control: another account signs in there")
        self.assertEqual(self.sign_in(env, env.proto_req, "nobody@example.com", WRONG), (401, "invalid_credentials"))

    def test_a_right_password_after_wrong_ones_does_not_start_the_count_again(self) -> None:
        env = self.env(page_url=False, prototype_url=False)
        for _ in range(ps.RATE_PAIR_LIMIT - 1):
            self.assertEqual(self.sign_in(env, env.proto_req, APPROVER, WRONG), (401, "invalid_credentials"))
        self.assertEqual(self.sign_in(env, env.proto_req, APPROVER, flow.PW_APPROVER), (403, "approver_login_elsewhere"))
        self.assertEqual(self.sign_in(env, env.proto_req, APPROVER, WRONG), (429, "rate_limited"))

    def test_privileged_accounts_over_plain_http(self) -> None:
        for login, password in ((APPROVER, flow.PW_APPROVER), (ACCEPTOR, flow.PW_COMMENTER)):
            with self.subTest(login=login):
                env = self.env()
                env.app.config.allow_plain_login = True
                with mock.patch.object(ps.SessionRoutesMixin, "_context", return_value="plain"):
                    self.assert_limited(env, env.page_req, login, password, "secure_connection_required")

    def test_control_where_the_account_may_sign_in_a_right_password_clears_the_count(self) -> None:
        env = self.env()
        for _round in range(2):
            for _ in range(ps.RATE_PAIR_LIMIT - 1):
                self.assertEqual(self.sign_in(env, env.page_req, APPROVER, WRONG), (401, "invalid_credentials"))
            self.assertEqual(self.sign_in(env, env.page_req, APPROVER, flow.PW_APPROVER)[0], 200)


class ProxyTests(Case):
    """S4b: `--secure-cookies` alone does not turn a request of a trusted proxy into TLS: the proxy says `X-Forwarded-Proto: https`."""

    def through_proxy(self, headers: dict) -> tuple:
        env = self.env()
        env.app.config.secure_cookies = True
        with mock.patch.object(ps.SessionRoutesMixin, "_link_request_allowed", return_value=False), \
                mock.patch.object(ps.SessionRoutesMixin, "_from_trusted_proxy", return_value=True):
            reply = env.page_req("POST", "/_api/v1/login", body={"login": APPROVER, "password": flow.PW_APPROVER}, headers=headers)
        return reply.status, reply.json().get("error")

    def test_without_the_header_the_request_is_plain_http(self) -> None:
        self.assertEqual(self.through_proxy({}), (403, "secure_connection_required"))
        self.assertEqual(self.through_proxy({"X-Forwarded-Proto": "http"}), (403, "secure_connection_required"))

    def test_control_with_https_the_request_is_tls(self) -> None:
        self.assertEqual(self.through_proxy({"X-Forwarded-Proto": "https"})[0], 200)


if __name__ == "__main__":
    unittest.main()
