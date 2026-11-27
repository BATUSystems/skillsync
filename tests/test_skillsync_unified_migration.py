"""CORE-2000-UNIFIED U-6 (migration): the password of an `approver` only on the page, the hint of `prototype serve`, the texts after the token
and the two ports, the help of the port flags: python3 -m unittest tests.test_skillsync_unified_migration

The servers of the password tests run in this process (the harness of `test_skillsync_prototype_handoff`)."""
from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

import test_skillsync_prototype_handoff as flow  # noqa: E402  (the module, so its test classes are not collected here)

from skillsync_workflow import dashboard, prototype  # noqa: E402

ps = flow.ps
APPROVER = "ap@example.com"
COMMENTER = "com@example.com"
NEW = "-".join(("Zupelnie", "Nowe", "Haslo", "Konta", "2027"))


class PasswordOffThePageTests(unittest.TestCase):
    """Spec sections 1 and 2: on a server that is not the page (`prototype serve`, standalone, `--single-origin`) nobody types the password of
    an account with `approver`, also not in a session of a login link, which may be opened there."""

    @staticmethod
    def session_of(env, request, login: str) -> dict:
        record = env.app.users.get(login)
        session = env.app.sessions.create(login, record["password"], via="link", kind="proto")
        cookie = "%s=%s" % (env.app.cookie_name_for(False, "proto"), session.token)
        return request("GET", "/_api/v1/session", headers={"Cookie": cookie}).json()

    def change(self, env, request, login: str, current: str):
        record = env.app.users.get(login)
        session = env.app.sessions.create(login, record["password"], via="link", kind="proto")
        cookie = "%s=%s" % (env.app.cookie_name_for(False, "proto"), session.token)
        csrf = request("GET", "/_api/v1/session", headers={"Cookie": cookie}).json()["csrf"]
        return request("POST", "/_api/v1/password", body={"current": current, "new": NEW}, headers={"Cookie": cookie, "X-Skillsync-CSRF": csrf})

    def test_an_approver_changes_no_password_there(self) -> None:
        env = flow.Env(self, page_url=False, prototype_url=False)
        before = env.app.users.get(APPROVER)["password"]
        reply = self.change(env, env.proto_req, APPROVER, flow.PW_APPROVER)
        self.assertEqual((reply.status, reply.json().get("error")), (403, "approver_login_elsewhere"), reply.body)
        self.assertEqual(env.app.users.get(APPROVER)["password"], before)
        lines = [json.loads(line) for line in (Path(env.run_dir) / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        refused = [line for line in lines if line["event"] == "password_refused"]
        self.assertEqual([(line["account"], line["reason"], bool(line["source"])) for line in refused],
                         [(ps.account_of(env.app.users.get(APPROVER)), "approver_elsewhere", True)], lines)
        self.assertNotIn(NEW, json.dumps(lines))

    def test_the_session_says_that_no_password_is_changed_there(self) -> None:
        env = flow.Env(self, page_url=False, prototype_url=False)
        self.assertIs(self.session_of(env, env.proto_req, APPROVER)["server"].get("passwordChange"), False)
        self.assertNotIn("passwordChange", self.session_of(env, env.proto_req, COMMENTER)["server"], "control")
        self.assertNotIn("passwordChange", env.proto_req("GET", "/_api/v1/session").json()["server"], "no account, no statement")
        page = env.proto_req("GET", "/_login").body.decode("utf-8")
        for part in ("passwordChange === false", "approver_login_elsewhere", "login_on_page", "skillsync dashboard serve"):
            self.assertIn(part, page)

    def test_the_status_page_in_single_origin_refuses_it_too(self) -> None:
        env = flow.Env(self, page_url=False, prototype_url=False)
        env.app.config.origin_kind, env.app.single_origin = "proto", True   # what `build_proto_app` sets for `--single-origin`
        before = env.app.users.get(APPROVER)["password"]
        self.assertIs(self.session_of(env, env.page_req, APPROVER)["server"].get("passwordChange"), False)
        reply = self.change(env, env.page_req, APPROVER, flow.PW_APPROVER)   # the socket and the handler of the dashboard
        self.assertEqual((reply.status, reply.json().get("error")), (403, "approver_login_elsewhere"), reply.body)
        self.assertEqual(env.app.users.get(APPROVER)["password"], before)
        self.assertEqual(self.change(env, env.page_req, COMMENTER, flow.PW_COMMENTER).status, 204, "control")

    def test_a_role_granted_while_the_password_is_hashed_is_seen_under_the_lock(self) -> None:
        env = flow.Env(self, page_url=False, prototype_url=False)
        real = ps.hash_password
        granted: list[str] = []

        def hashing(password, iterations=None, *rest, **kwargs):
            if password == NEW and not granted:   # between the check of the request and the write of the users file
                granted.append(COMMENTER)
                env.app.users.update(lambda users: [item.update({"roles": ["approver"]}) for item in users if item["login"] == COMMENTER])
            return real(password, iterations, *rest, **kwargs)

        before = env.app.users.get(COMMENTER)["password"]
        with mock.patch.object(ps, "hash_password", hashing):
            reply = self.change(env, env.proto_req, COMMENTER, flow.PW_COMMENTER)
        self.assertEqual(granted, [COMMENTER])
        self.assertIn("approver", ps.roles_of(env.app.users.get(COMMENTER)), "the race happened")
        self.assertEqual((reply.status, reply.json().get("error")), (403, "approver_login_elsewhere"), reply.body)
        self.assertEqual(env.app.users.get(COMMENTER)["password"], before)

    def test_control_another_account_changes_its_password_there(self) -> None:
        env = flow.Env(self, page_url=False, prototype_url=False)
        before = env.app.users.get(COMMENTER)["password"]
        reply = self.change(env, env.proto_req, COMMENTER, flow.PW_COMMENTER)
        self.assertEqual(reply.status, 204, reply.body)
        self.assertNotEqual(env.app.users.get(COMMENTER)["password"], before)


class PrototypeServeHintTests(unittest.TestCase):
    """Spec section 8: `prototype serve` stays and names `dashboard serve`."""

    def run_serve(self, demo: bool) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs" / "prototypes").mkdir(parents=True)
            args = SimpleNamespace(port=0, demo=demo, login_to_view=False, host=None, no_source_writes=True, users=None, secure_cookies=False,
                                   trust_proxy=False, proxy_address=None, allow_plain_login=False, local_only=True)
            instance = mock.MagicMock()
            instance.url = "http://127.0.0.1:1/"
            instance.app.warnings = []
            instance.serve_forever.side_effect = KeyboardInterrupt
            err = io.StringIO()
            with mock.patch.object(prototype.server, "make_server", return_value=instance):
                report = prototype.serve_prototypes(root, {}, args, err=err)
            self.assertEqual(report["status"], "PASS", report)
            return err.getvalue()

    def test_serve_names_the_status_page_and_the_demo_does_not(self) -> None:
        self.assertIn("skillsync dashboard serve", self.run_serve(demo=False))
        self.assertNotIn("skillsync dashboard serve", self.run_serve(demo=True))


class TextTests(unittest.TestCase):
    def test_the_tip_of_a_consent_that_cannot_be_given_here_names_the_account_not_a_host_flag(self) -> None:
        for lang in ("pl", "en"):
            with self.subTest(lang=lang):
                catalog = json.loads((ROOT / "skillsync_workflow" / "data" / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
                text = catalog["ui.approve.hereOnlyTip"]
                self.assertIn("approver", text)
                self.assertIn("TLS", text)
                self.assertNotIn("--approve-host", text)

    def test_the_help_of_the_port_flags_says_the_real_rules(self) -> None:
        parser = argparse.ArgumentParser()
        dashboard.register(parser.add_subparsers())
        top = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices["dashboard"]
        serve = next(a for a in top._actions if isinstance(a, argparse._SubParsersAction)).choices["serve"]
        helps = {flag: action.help for action in serve._actions for flag in action.option_strings}
        for part in ("+ 2", "5100-5199", "5141", "5140", "does not choose it"):
            self.assertIn(part, helps["--prototype-port"])
        self.assertNotIn("else 5130", helps["--prototype-port"])
        self.assertIn("only with --approve-port", helps["--approve-bind"])
        self.assertIn("plain HTTP", helps["--allow-remote-approvals"])


if __name__ == "__main__":
    unittest.main()
