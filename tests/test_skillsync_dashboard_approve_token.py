"""B1 -> CORE-2000-UNIFIED U-2: the consents of the status page have NO token (`X-Approve-Token`, `#zgoda=`, `tokenId` are gone); the right to consent is a
session with the role `approver`. The module keeps its name (other modules import `home_env` from it) and now covers:

- the legacy token: a client that still sends `X-Approve-Token` is ignored (the session decides), the token is nowhere (status, assets, log, state file);
- the state file (schema 2 without a token: `approvals: bool`, `tabs[]` without a token) and `skillsync dashboard url` (the address is `http://<host>:<port>/`,
  a tab `/?tab=<id>`, no fragment); records of servers of earlier versions (with a `token`) are still read to find and stop them;
- `/healthz` (`serverId`, no `tokenId`) and the console of `dashboard serve`.

The consent itself (roles, CSRF, audit, 409s) is in `test_skillsync_dashboard_consent_session`."""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from layers_helpers import home_env  # noqa: E402,F401  (re-exported: the other dashboard test modules import it from here)
from skillsync_workflow import dashboard_http as srv  # noqa: E402
from skillsync_workflow import dashboard_token  # noqa: E402
from tests.serve_start import bind_free, start_serve, stop_serve  # noqa: E402
from tests.test_skillsync_dashboard_server import (CLI, PLAN, SESSION_PW, TEST_TOKEN, HttpCase, ProjectCase, SessionKit, free_port,  # noqa: E402
                                                   install_fake_cli, make_project, raw_post, spy_core)

HEADERS = {"Content-Type": "application/json", "X-Status-Board": "1"}


class TokenCase(HttpCase):
    SPEC2 = "docs/specs/demo-etap-2.md"
    PLAN2 = "docs/plans/2026-01-20-demo-etap-2.md"

    def setUp(self) -> None:
        super().setUp()
        install_fake_cli(self)
        spy_core(self)
        self.ok = self.session.headers(self.port)   # contract U-2: the headers of a consent of the signed-in approver (cookie, CSRF, Origin, Sec-Fetch-Site)

    def offer(self) -> dict:
        (self.root / srv.RUNS_DIR / "2026-01-20-demo-etap-2").mkdir(parents=True, exist_ok=True)
        spec = self.root / self.SPEC2
        spec.write_text(f"# Spec: Demo 2\n\nStatus: draft (czeka na GO plan)\nPlan: {self.PLAN2}\n", encoding="utf-8")
        later = (self.root / PLAN).stat().st_mtime + 100
        os.utime(spec, (later, later))
        with self.get("/api/status") as r:
            action = next(a for a in json.load(r)["approvals"]["actions"] if a["kind"] == "spec")
        return {"kind": "spec", "path": self.SPEC2, "sha": action["sha"]}


class LegacyTokenIgnored(TokenCase):
    """Contract U-2: `X-Approve-Token` means nothing; the session decides. Every negative test has its positive control."""

    def test_the_old_token_without_a_session_is_a_401_and_writes_nothing(self) -> None:
        anonymous = {k: v for k, v in self.ok.items() if k not in ("Cookie", "X-Skillsync-CSRF")}
        code, body = self.post(self.offer(), {**anonymous, "X-Approve-Token": TEST_TOKEN}, session=False)
        self.assertEqual((code, body["code"]), (401, "login_required"))
        self.set_doc.assert_not_called()
        code, body = self.post(self.offer(), self.ok)   # control: the same consent with the session and without any token is accepted
        self.assertEqual(code, 200, body)

    def test_a_wrong_or_missing_legacy_token_changes_nothing_for_a_session(self) -> None:
        data = self.offer()
        answers = [self.post(data, {**self.ok, **extra})[0] for extra in ({"X-Approve-Token": "x"}, {"X-Approve-Token": ""}, {"X-Approve-Token": TEST_TOKEN})]
        self.assertEqual(answers[0], 200)   # the first wins the sha, the next ones are the same offer taken: 409 (the same for any token)
        self.assertEqual(answers[1:], [409, 409])
        self.assertEqual(self.set_doc.call_count, 1)

    def test_the_session_right_keeps_every_existing_behaviour(self) -> None:
        data = self.offer()
        code, body = self.post(data, self.ok)
        self.assertEqual((code, body["word"], body["file"]), (200, "GO plan", self.SPEC2), body)
        self.set_doc.assert_called_once()

    def test_the_csrf_is_compared_in_constant_time(self) -> None:
        with mock.patch.object(srv.hmac, "compare_digest", wraps=srv.hmac.compare_digest) as compare:
            code, body = self.post(self.offer(), {**self.ok, "X-Skillsync-CSRF": "wrong"})
        self.assertEqual((code, body["code"]), (403, "consent_refused"))
        csrf_calls = [call for call in compare.call_args_list if call.args[1] == self.session.csrf.encode("ascii")]
        self.assertEqual(len(csrf_calls), 1)
        self.set_doc.assert_not_called()

    def test_every_refusal_before_the_session_is_the_same_with_and_without_the_legacy_token(self) -> None:
        data = self.offer()
        cases = {
            "Host": ({"Host": "evil.example"}, {"error": srv.host_refused_message(srv.ports.ROLE_BOTH, "evil.example"),
                                                  "code": "host_refused"}),   # kontrakt U-3a
            "X-Status-Board": ({"X-Status-Board": "0"}, "consent_refused"),
            "Origin": ({"Origin": "https://evil.example"}, "consent_refused"),
            "Content-Type": ({"Content-Type": "text/plain"}, "consent_refused"),
        }
        for name, (change, expected) in cases.items():
            for token in (None, TEST_TOKEN, "wrong"):
                with self.subTest(refusal=name, token=token):
                    headers = {**self.ok, **change}
                    if token:
                        headers["X-Approve-Token"] = token
                    code, body = self.post(data, headers)
                    self.assertEqual(code, 403)
                    if isinstance(expected, dict):
                        self.assertEqual(body, expected)
                    else:
                        self.assertEqual(body["code"], expected)
        self.set_doc.assert_not_called()

    def test_the_session_is_checked_before_the_body_is_read(self) -> None:
        anonymous = {k: v for k, v in self.ok.items() if k not in ("Cookie", "X-Skillsync-CSRF")}
        code, body = raw_post(self.port, {**anonymous, "Content-Length": str(srv.MAX_BODY + 1)})
        self.assertEqual((code, body["code"]), (401, "login_required"))   # 401, not 400: the session before the length
        code, body = raw_post(self.port, {**self.ok, "Content-Length": str(srv.MAX_BODY + 1)})
        self.assertEqual(code, 400)   # control: with the session the length is judged as before


class NoTokenAnywhere(TokenCase):
    def test_status_says_no_token_is_required_and_has_no_token_field(self) -> None:
        with self.get("/api/status") as r:
            raw = r.read().decode("utf-8")
        data = json.loads(raw)
        self.assertIs(data["approvals"]["tokenRequired"], False)
        self.assertNotIn(TEST_TOKEN, raw)
        self.assertNotIn("tokenId", raw)

    def test_no_response_of_a_get_contains_a_token_or_the_session_secrets(self) -> None:
        paths = ["/", "/index.html", "/api/status", "/api/descriptions", "/healthz", "/assets/app.js", "/assets/dashboard-render.js",
                 "/assets/app.css", "/assets/theme.js"]
        for path in paths:
            with self.subTest(path=path):
                with self.get(path) as r:
                    body = r.read().decode("utf-8", "replace")
                    headers = str(r.headers)
                for secret in (TEST_TOKEN, self.session.csrf, self.session.pair.split("=", 1)[1], SESSION_PW):
                    self.assertNotIn(secret, body)
                    self.assertNotIn(secret, headers)

    def test_the_secrets_never_reach_the_log_or_an_error_answer(self) -> None:
        logged = []
        data = self.offer()
        secrets_ = (self.session.csrf, self.session.pair.split("=", 1)[1], SESSION_PW, TEST_TOKEN)
        with mock.patch.object(srv, "log", lambda text, *a, **k: logged.append(str(text))):
            for extra in ({"X-Skillsync-CSRF": "wrong"}, {}):
                code, body = self.post(data, {**self.ok, "X-Approve-Token": TEST_TOKEN, **extra})
                self.assertNotIn(self.session.csrf, json.dumps(body) if code != 200 else "")
            self.post({"kind": "rm"}, {**self.ok, "X-Approve-Token": TEST_TOKEN})
        self.assertFalse([line for line in logged if any(secret in line for secret in secrets_)])


class NoApprovals(HttpCase):
    APPROVALS = False

    def test_a_read_only_page_says_so_and_refuses_a_consent_of_an_approver(self) -> None:
        with self.get("/api/status") as r:
            self.assertIs(json.load(r)["approvals"]["tokenRequired"], False)
        code, body = self.post({"kind": "cp", "path": "x", "sha": "a" * 7}, self.session.headers(self.port))
        self.assertEqual((code, body["code"]), (403, "approvals_disabled"))
        self.assertIn("--no-approvals", body["error"])   # refused as before: the consents are off, whatever the session


class NoTokenPerProcess(ProjectCase):
    def test_the_board_has_no_token_and_refuses_the_old_argument(self) -> None:
        self.assertFalse(hasattr(self.board, "approve_token"))
        with self.assertRaises(TypeError):
            srv.Board(self.root, prototype_port=1, approve_token=TEST_TOKEN)   # type: ignore[call-arg]

    def test_show_knows_nothing_about_the_token(self) -> None:
        self.assertNotIn("tokenRequired", self.board.status()["approvals"])


class StateFile(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dashboard-token-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir()
        patch = mock.patch.dict(os.environ, home_env(self.tmp.name))
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_file_is_outside_the_project_and_has_the_record(self) -> None:
        path = dashboard_token.write_state(self.project, 5140, True)
        self.assertIsNotNone(path)
        self.assertEqual(path.parent, dashboard_token.state_dir())
        for form in (str(self.project.resolve()), dashboard_token.root_text(self.project)):   # native and report form (`/` on Windows)
            self.assertNotIn(form, str(path))
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((record["schema"], record["port"], record["approvals"], record["root"], record["pid"]),
                         (1, 5140, True, dashboard_token.root_text(self.project), os.getpid()))   # the report form of the path
        self.assertNotIn("token", record)   # contract U-2: no token and no digest of it in the file
        self.assertNotIn("tokenId", record)
        self.assertEqual([p.name for p in path.parent.iterdir()], [path.name], "no temporary file is left")

    def test_the_temporary_file_is_never_a_json_name_while_the_state_is_written(self) -> None:
        """Regression (CI macOS): `Path.glob("*.json")` matches hidden files, so a `.tmp-*.json` (empty after `mkstemp`) was read as a state file."""
        seen: list[list[str]] = []
        real = os.replace

        def spy(source, target):
            seen.append(sorted(p.name for p in Path(target).parent.glob("*.json")))   # the state of the directory at the moment of the replace
            return real(source, target)

        with mock.patch.object(dashboard_token.os, "replace", spy):
            path = dashboard_token.write_state(self.project, 5140, True)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0], [], "the temporary file does not match `*.json` before the replace")
        self.assertEqual([p.name for p in path.parent.glob("*.json")], [path.name])

    @unittest.skipIf(os.name == "nt", "POSIX: tryb 0600 pliku i 0700 katalogu stanu (na Windows ACL: test_skillsync_winacl)")
    def test_the_file_is_private_on_posix(self) -> None:
        path = dashboard_token.write_state(self.project, 5140, True)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_the_restriction_is_made_on_the_temporary_file_before_the_replace(self) -> None:
        events = []
        real_replace = os.replace
        with mock.patch.object(dashboard_token, "_restrict", lambda path: events.append(("restrict", Path(path).name)) or True), \
                mock.patch.object(dashboard_token.os, "replace", lambda a, b: events.append(("replace", Path(a).name)) or real_replace(a, b)):
            path = dashboard_token.write_state(self.project, 5141, True)
        kinds = [kind for kind, _ in events]
        self.assertEqual(kinds, ["restrict", "restrict", "replace"], events)   # the directory, the temporary file, then the replace
        self.assertTrue(events[1][1].startswith(".tmp-") and events[2][1] == events[1][1])
        self.assertTrue(path.is_file())

    def test_a_failed_restriction_warns_once_and_the_file_is_still_written(self) -> None:
        warnings = []
        with mock.patch.object(dashboard_token, "_restrict", lambda path: False):
            path = dashboard_token.write_state(self.project, 5142, True, warn=warnings.append)
        self.assertTrue(path.is_file())
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("token", warnings[0].lower())

    def test_an_unwritable_state_directory_does_not_stop_the_server(self) -> None:
        warnings = []
        with mock.patch.object(dashboard_token.tempfile, "mkstemp", side_effect=PermissionError(13, "denied")):
            self.assertIsNone(dashboard_token.write_state(self.project, 5143, True, warn=warnings.append))
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("token", warnings[0].lower())

    def test_the_root_has_one_form_everywhere_and_the_file_is_the_same_on_windows(self) -> None:
        """PATHFMT: the `root` of the record is `pathfmt.posix` (`C:/work/proj`), and the digest that names the file comes from that
        same text, so the server and `dashboard url` find one file whatever spelling the system gives (`C:\\work\\proj` or `C:/work/proj`).
        Windows is imitated without a Path under a replaced `os.name`: a stand-in for the resolved path and `posix` run as on `nt`."""
        class Resolved:
            def __init__(self, text):
                self.text = text

            def __str__(self):
                return self.text

        real = dashboard_token.pathfmt.posix

        def as_on_windows(value):
            with mock.patch.object(os, "name", "nt"):   # only the pure string function runs under it
                return real(value)

        digests = set()
        for spelling in ("C:\\work\\proj", "C:/work/proj"):
            with mock.patch.object(dashboard_token, "_resolved", lambda root, spelling=spelling: Resolved(spelling)), \
                    mock.patch.object(dashboard_token.pathfmt, "posix", as_on_windows):
                self.assertEqual(dashboard_token.root_text("ignored"), "C:/work/proj")
                digests.add(dashboard_token._digest("ignored"))
                path = dashboard_token.write_state("ignored", 5190, True)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["root"], "C:/work/proj")
                found = dashboard_token.servers("ignored", healthy=lambda host, port, token_id=None: True)
                self.assertEqual([item["port"] for item in found], [5190], f"the record written for {spelling!r} is not found")
        self.assertEqual(len(digests), 1, "two spellings of one root name two different state files")
        self.assertEqual(len(list(dashboard_token.state_dir().glob("*-5190.json"))), 1)

    def test_a_posix_root_is_unchanged(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX: a backslash is a part of a name there")
        self.assertEqual(dashboard_token.root_text(self.project), str(Path(self.project).resolve()))

    def test_remove_state_deletes_the_file_and_is_quiet_when_it_is_gone(self) -> None:
        path = dashboard_token.write_state(self.project, 5144, True)
        dashboard_token.remove_state(self.project, 5144)
        self.assertFalse(path.exists())
        dashboard_token.remove_state(self.project, 5144)

    def test_remove_state_with_a_pid_deletes_only_the_file_of_that_process(self) -> None:
        """An old server that stops late (or one that lost the port) must not delete the file of the server that holds the port now."""
        path = dashboard_token.write_state(self.project, 5145, True)
        dashboard_token.remove_state(self.project, 5145, os.getpid() + 1)   # a stopping server that is not the writer
        self.assertTrue(path.is_file(), "the file of another process stays")
        self.assertIs(json.loads(path.read_text(encoding="utf-8"))["approvals"], True)
        dashboard_token.remove_state(self.project, 5145, os.getpid())
        self.assertFalse(path.exists(), "the writer removes its own file")
        dashboard_token.remove_state(self.project, 5145, os.getpid())   # gone: quiet

    def test_the_refusal_without_consents_does_not_speak_of_a_token(self) -> None:
        state = dashboard_token.state_dir()
        state.mkdir(parents=True)
        warnings = []
        with mock.patch.object(dashboard_token, "_own_directory", lambda directory: False):
            self.assertIsNone(dashboard_token.write_state(self.project, 5183, None, warn=warnings.append))
            self.assertIsNone(dashboard_token.write_state(self.project, 5184, True, warn=warnings.append))
        for text in warnings:   # contract U-2: there is no token file any more, the refusal speaks of the state file only
            self.assertNotIn("token", text.lower())
        self.assertEqual(len(warnings), 2)

    def test_servers_lists_only_healthy_servers_of_this_project(self) -> None:
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        dashboard_token.write_state(self.project, 5150, True)
        dashboard_token.write_state(self.project, 5151, True)
        dashboard_token.write_state(other, 5152, True)
        everything = dashboard_token.servers(self.project, healthy=lambda host, port, token_id=None: True)
        self.assertEqual([(s["port"], s["token"], s["approvals"]) for s in everything], [(5150, None, True), (5151, None, True)])
        only = dashboard_token.servers(self.project, 5151, healthy=lambda host, port, token_id=None: True)
        self.assertEqual([s["port"] for s in only], [5151])
        self.assertEqual(dashboard_token.servers(self.project, healthy=lambda host, port, token_id=None: port == 5150)[0]["port"], 5150,
                         "a leftover whose port answers no status page is ignored")
        self.assertEqual(dashboard_token.servers(self.project, healthy=lambda host, port, token_id=None: False), [])

    def test_a_dead_process_and_a_foreign_or_broken_file_are_ignored(self) -> None:
        path = dashboard_token.write_state(self.project, 5160, True)
        record = json.loads(path.read_text(encoding="utf-8"))
        if os.name != "nt":   # Windows decides by /healthz only
            record["pid"] = 2 ** 22 + 12345
            path.write_text(json.dumps(record), encoding="utf-8")
            self.assertEqual(dashboard_token.servers(self.project, healthy=lambda host, port, token_id=None: True), [])
        path.write_text("not json", encoding="utf-8")
        self.assertEqual(dashboard_token.servers(self.project, healthy=lambda host, port, token_id=None: True), [])
        path.write_text(json.dumps({**record, "schema": 3, "pid": os.getpid()}), encoding="utf-8")
        self.assertEqual(dashboard_token.servers(self.project, healthy=lambda host, port, token_id=None: True), [])
        path.write_text(json.dumps({**record, "root": "elsewhere", "pid": os.getpid()}), encoding="utf-8")
        self.assertEqual(dashboard_token.servers(self.project, healthy=lambda host, port, token_id=None: True), [])

    def test_address_host_is_a_name_the_page_accepts_and_bind_name_stays_the_probe_address(self) -> None:
        for host, approve, shown, probed in (("0.0.0.0", (), "127.0.0.1", "127.0.0.1"), ("", (), "127.0.0.1", "127.0.0.1"),
                                             ("::", (), "127.0.0.1", "127.0.0.1"), ("127.0.0.1", (), "127.0.0.1", "127.0.0.1"),
                                             ("localhost", (), "localhost", "localhost"),
                                             ("192.168.1.5", (), "127.0.0.1", "192.168.1.5"),
                                             ("192.168.1.5", ("192.168.1.5",), "192.168.1.5", "192.168.1.5"),
                                             ("192.168.1.5", ("DEMO.example",), "127.0.0.1", "192.168.1.5"),
                                             ("DEMO.example", ("demo.example",), "DEMO.example", "DEMO.example")):
            with self.subTest(host=host, approve=approve):
                self.assertEqual(dashboard_token.address_host(host, approve), shown)
                self.assertEqual(dashboard_token.bind_name(host), probed)

    def test_a_state_file_of_this_version_says_approvals_and_has_no_token(self) -> None:
        """Contract U-2: schema 2 without a token (`approvals: bool`); a record that says neither `approvals` nor has a token (of an earlier version) is broken."""
        for approvals in (True, False):
            with self.subTest(approvals=approvals):
                path = dashboard_token.write_state(self.project, 5180, approvals)
                record = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual((record["approvals"], "token" in record, "tokenId" in record), (approvals, False, False))
                listed = dashboard_token.servers(self.project, healthy=lambda host, port, token_id=None: True)
                self.assertEqual([(item["port"], item["approvals"], item["token"]) for item in listed], [(5180, approvals, None)])
        del record["approvals"]                      # neither with a token nor with `approvals`: broken
        path.write_text(json.dumps(record), encoding="utf-8")
        self.assertEqual(dashboard_token.servers(self.project, healthy=lambda host, port, token_id=None: True), [])

    def test_a_token_is_never_written_whatever_is_passed_and_a_file_of_an_old_server_is_still_read(self) -> None:
        path = dashboard_token.write_state(self.project, 5185, "tok-passed-by-mistake")
        self.assertNotIn("tok-passed-by-mistake", path.read_text(encoding="utf-8"))
        # a record of a server of an earlier version (a token, no `approvals`): still found, so that it can be stopped or told from a new one
        path.write_text(json.dumps({"schema": 1, "pid": os.getpid(), "port": 5185, "host": "127.0.0.1", "root": dashboard_token.root_text(self.project),
                                    "token": "old-" + "token", "tokenId": dashboard_token.token_id("old-" + "token"), "hosts": []}), encoding="utf-8")
        listed = dashboard_token.servers(self.project, healthy=lambda host, port, token_id=None: True)
        self.assertEqual([(item["port"], item["approvals"]) for item in listed], [(5185, True)])

    @unittest.skipIf(os.name == "nt", "POSIX: dowiązania i właściciel katalogu")
    def test_a_state_directory_that_is_a_link_is_refused_and_nothing_is_written_through_it(self) -> None:
        elsewhere = Path(self.tmp.name) / "elsewhere"
        elsewhere.mkdir()
        state = dashboard_token.state_dir()
        state.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(elsewhere, state)
        warnings = []
        self.assertIsNone(dashboard_token.write_state(self.project, 5181, True, warn=warnings.append))
        self.assertEqual(list(elsewhere.iterdir()), [], "the token went nowhere")
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("token", warnings[0].lower())
        self.assertTrue(state.is_symlink(), "the link itself is left alone (not chmod-ed through, not removed)")

    @unittest.skipIf(os.name == "nt", "POSIX: dowiązania i właściciel katalogu")
    def test_a_state_directory_of_another_user_is_refused(self) -> None:
        dashboard_token.state_dir().mkdir(parents=True)
        warnings = []
        with mock.patch.object(dashboard_token.os, "geteuid", lambda: os.stat(dashboard_token.state_dir()).st_uid + 1):
            self.assertIsNone(dashboard_token.write_state(self.project, 5182, True, warn=warnings.append))
        self.assertEqual(len(warnings), 1)
        self.assertEqual(list(dashboard_token.state_dir().iterdir()), [])

    def test_the_ordinary_case_is_not_refused(self) -> None:
        warnings = []
        self.assertTrue(dashboard_token.write_state(self.project, 5183, True, warn=warnings.append).is_file())
        self.assertTrue(dashboard_token.write_state(self.project, 5183, True, warn=warnings.append).is_file(), "a second write over an existing directory")
        # a refusal says "nie zapisano ..."; the note about a missing ACL helper (this branch has none on Windows) is another thing
        self.assertEqual([text for text in warnings if "nie zapisano" in text], [])

    def test_a_start_removes_the_leftover_temporary_files_of_its_own_project_and_port_only(self) -> None:
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        dashboard_token.write_state(self.project, 5190, True)
        directory = dashboard_token.state_dir()
        digest = dashboard_token._digest(self.project)
        mine = directory / f".tmp-{digest}-5190-leftover1.json"
        mine.write_text('{"token": "dead"}', encoding="utf-8")
        same_project_other_port = directory / f".tmp-{digest}-5191-leftover2.json"
        same_project_other_port.write_text("x", encoding="utf-8")
        other_project = directory / f".tmp-{dashboard_token._digest(other)}-5190-leftover3.json"
        other_project.write_text("x", encoding="utf-8")
        current = directory / f".tmp-{digest}-5190-leftover4.tmp"
        current.write_text('{"token": "dead"}', encoding="utf-8")
        dashboard_token.write_state(self.project, 5190, True)
        self.assertFalse(mine.exists(), "a temporary file of this project and port is swept")
        self.assertFalse(current.exists(), "also the current `.tmp` form")
        self.assertTrue(same_project_other_port.exists())
        self.assertTrue(other_project.exists())

    def test_readers_of_the_state_directory_skip_an_empty_hidden_temporary_file(self) -> None:
        """A write in progress leaves an empty `.tmp-*` file: `servers` and `port_owner` must not read it (no `JSONDecodeError`)."""
        dashboard_token.write_state(self.project, 5190, True)
        directory = dashboard_token.state_dir()
        digest = dashboard_token._digest(self.project)
        for name in (f".tmp-{digest}-5190-abc123.json", f".tmp-{digest}-5190-abc124.tmp"):
            (directory / name).write_text("", encoding="utf-8")
        self.assertEqual(len(dashboard_token.servers(self.project, 5190, healthy=lambda *a, **k: True)), 1)
        self.assertIsNotNone(dashboard_token.port_owner(5190, healthy=lambda *a, **k: True))

    def test_the_address_is_plain_with_no_fragment(self) -> None:
        self.assertEqual(dashboard_token.address(5140), "http://127.0.0.1:5140/")
        self.assertEqual(dashboard_token.address(5140, "demo.example"), "http://demo.example:5140/")
        self.assertEqual(dashboard_token.address(5140, "::1"), "http://[::1]:5140/")
        self.assertEqual(dashboard_token.tab_address(5140, "abc", "demo.example"), "http://demo.example:5140/?tab=abc")
        for text in (dashboard_token.address(5140), dashboard_token.tab_address(5140, "abc")):
            self.assertNotIn("#", text)
        self.assertFalse(hasattr(dashboard_token, "new_token"))
        self.assertFalse(hasattr(dashboard_token, "FRAGMENT"))


class UrlCommand(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="dashboard-url-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.root = make_project(self.tmp)
        __import__("shutil").rmtree(self.root / ".git")   # a directory outside a git project: `url` finds any server of the root (inside a git project only servers of kind `project`, CORE-1180)
        manifest = self.root / srv.MANIFEST
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("skills: [dashboard]\n", encoding="utf-8")
        patch = mock.patch.dict(os.environ, home_env(self.tmp))
        patch.start()
        self.addCleanup(patch.stop)
        self.layer = ["--layer", f"core={ROOT / 'library'}"]

    def args(self, **kwargs):
        values = {"path": str(self.root), "layers": [f"core={ROOT / 'library'}"], "port": None, "host": None, "json": True}
        return SimpleNamespace(**{**values, **kwargs})

    def run_url(self, **kwargs):
        out = []
        with mock.patch("builtins.print", lambda text="", *a, **k: out.append(str(text))), \
                mock.patch.object(dashboard_token, "_healthy", lambda host, port, token_id_expected=None, timeout=1.0: True):
            code = dashboard_token.url_command(self.args(**kwargs))
        return code, out

    def test_a_running_server_gives_its_address(self) -> None:
        dashboard_token.write_state(self.root, 5170, True)
        code, out = self.run_url()
        report = json.loads(out[0]) if out else {}
        self.assertEqual(code, 0, out)
        self.assertEqual((report["status"], report["url"], report["port"]), ("PASS", "http://127.0.0.1:5170/", 5170))

    def test_plain_output_is_just_the_address(self) -> None:
        dashboard_token.write_state(self.root, 5171, True)
        code, out = self.run_url(json=False)
        self.assertEqual((code, out), (0, ["http://127.0.0.1:5171/"]))

    def test_no_server_gives_blocked_server_not_running(self) -> None:
        code, out = self.run_url()
        report = json.loads(out[0])
        self.assertEqual((code, report["status"], report["code"]), (2, "BLOCKED", "server_not_running"))

    def test_two_servers_need_a_port(self) -> None:
        dashboard_token.write_state(self.root, 5172, True)
        dashboard_token.write_state(self.root, 5173, True)
        code, out = self.run_url()
        self.assertEqual((code, json.loads(out[0])["code"]), (2, "ambiguous_server"))
        code, out = self.run_url(port=5173)
        self.assertEqual((code, json.loads(out[0])["url"]), (0, "http://127.0.0.1:5173/"))

    def test_a_named_host_is_used_only_when_the_server_was_started_with_it(self) -> None:
        dashboard_token.write_state(self.root, 5174, True, hosts=["demo.example"])
        code, out = self.run_url(host="demo.example")
        self.assertEqual((code, json.loads(out[0])["url"]), (0, "http://demo.example:5174/"))
        code, out = self.run_url(host="DEMO.example")
        self.assertEqual(code, 0, "names compare without case")
        code, out = self.run_url(host="localhost")
        self.assertEqual((code, json.loads(out[0])["url"]), (0, "http://localhost:5174/"))
        code, out = self.run_url(host="evil.example")
        self.assertEqual((code, json.loads(out[0])["code"]), (2, "host_not_allowed"))
        self.assertNotIn("#", out[0])

    def test_a_server_without_consents_is_approvals_disabled_not_server_not_running(self) -> None:
        dashboard_token.write_state(self.root, 5175, False)
        code, out = self.run_url()
        report = json.loads(out[0])
        self.assertEqual((code, report["status"], report["code"]), (2, "BLOCKED", "approvals_disabled"))
        self.assertIn("5175", report["reason"])

    def test_with_a_server_with_and_one_without_consents_the_address_is_of_the_first(self) -> None:
        dashboard_token.write_state(self.root, 5176, False)
        dashboard_token.write_state(self.root, 5177, True)
        code, out = self.run_url()
        self.assertEqual((code, json.loads(out[0])["url"]), (0, "http://127.0.0.1:5177/"))
        code, out = self.run_url(port=5176)
        self.assertEqual((code, json.loads(out[0])["code"]), (2, "approvals_disabled"))

    def test_a_server_bound_to_an_address_the_page_refuses_is_offered_on_loopback(self) -> None:
        dashboard_token.write_state(self.root, 5178, True, host="192.168.1.5")    # the probe goes to its own address (the file)
        code, out = self.run_url()
        self.assertEqual((code, json.loads(out[0])["url"]), (0, "http://127.0.0.1:5178/"))

    def test_a_broken_project_is_blocked(self) -> None:
        code, out = self.run_url(path=str(self.tmp / "nope"))
        self.assertEqual((code, json.loads(out[0])["code"]), (2, "invalid_project"))


class HealthAndLeftovers(unittest.TestCase):
    """`/healthz` of a server of this version carries `serverId` (random per process, no token digest); servers of earlier versions answered `tokenId`, which
    `_healthy` still compares: a stranger on a port is told apart without sending anything secret."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="dashboard-health-")
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir()
        patch = mock.patch.dict(os.environ, home_env(self.tmp.name))
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_connection_goes_to_the_recorded_address_with_a_loopback_host_header(self) -> None:
        seen = []

        class Answer:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, *a):
                return b'{"ok": true, "tokenId": "abcd1234"}'

        def fake(request, timeout=None):
            seen.append((request.full_url, request.get_header("Host")))
            return Answer()

        with mock.patch.object(dashboard_token.urllib.request, "urlopen", fake):
            self.assertTrue(dashboard_token._healthy("192.0.2.7", 5200, "abcd1234"))
            self.assertFalse(dashboard_token._healthy("192.0.2.7", 5200, "other000"), "another token digest: a stranger on the port")
            self.assertTrue(dashboard_token._healthy("::1", 5201))
            self.assertTrue(dashboard_token._healthy("evil.example", 5202), "a name from the file is never connected to")
        self.assertEqual([url for url, _ in seen], ["http://192.0.2.7:5200/healthz", "http://192.0.2.7:5200/healthz", "http://[::1]:5201/healthz",
                                                     "http://127.0.0.1:5202/healthz"])
        self.assertEqual({host for _, host in seen}, {"localhost"})

    @unittest.skipIf(os.name == "nt", "POSIX: sprawdzenie PID (`os.kill(pid, 0)`); na Windows decyduje /healthz")
    def test_a_dead_process_leftover_is_removed(self) -> None:
        path = dashboard_token.write_state(self.project, 5210, True)
        record = json.loads(path.read_text(encoding="utf-8"))
        record["pid"] = 2 ** 22 + 4321
        path.write_text(json.dumps(record), encoding="utf-8")
        self.assertEqual(dashboard_token.servers(self.project, healthy=lambda host, port, token_id=None: True), [])
        self.assertFalse(path.exists())

    def test_the_state_records_the_bind_name_and_the_allowed_hosts_and_no_token_digest(self) -> None:
        path = dashboard_token.write_state(self.project, 5211, True, "192.0.2.7", hosts=["demo.example"])
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((record["host"], record["hosts"]), ("192.0.2.7", ["demo.example"]))
        self.assertNotIn("tokenId", record)   # contract U-2
        self.assertEqual([dashboard_token.bind_name(h) for h in ("", "0.0.0.0", "::", "127.0.0.1", "::1", "192.0.2.7")],
                         ["127.0.0.1", "127.0.0.1", "127.0.0.1", "127.0.0.1", "::1", "192.0.2.7"])


class HealthzOfTheServer(TokenCase):
    def test_healthz_has_the_server_id_and_no_token_digest(self) -> None:
        with self.get("/healthz") as r:
            body = json.load(r)
        self.assertRegex(body["serverId"], r"^[0-9a-f]{16}$")   # random per process (CORE-1180)
        self.assertNotIn("tokenId", body)   # contract U-2: no token, so no digest of it
        self.assertEqual(sorted(body), ["ok", "serverId", "version"])
        self.assertTrue(dashboard_token._healthy("127.0.0.1", self.port, body["serverId"]))
        self.assertFalse(dashboard_token._healthy("127.0.0.1", self.port, "0" * 16))


class HeaderEdgeCases(TokenCase):
    def raw(self, header_lines: bytes, body: bytes = b"{}", session: bool = True) -> int:
        import socket
        own = {"Cookie": self.session.pair, "X-Skillsync-CSRF": self.session.csrf} if session else {}
        fixed = f"Origin: http://127.0.0.1:{self.port}\r\nSec-Fetch-Site: same-origin\r\n" + "".join(f"{k}: {v}\r\n" for k, v in own.items())
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(f"POST /api/approve HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nContent-Type: application/json\r\nX-Status-Board: 1\r\n{fixed}".encode()
                         + header_lines + b"Content-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
            data = b""
            while chunk := sock.recv(4096):
                data += chunk
        return int(data.split(b" ", 2)[1])

    def test_a_header_outside_ascii_and_two_headers_never_pass(self) -> None:
        baseline = self.raw(b"")
        self.assertEqual(baseline, 400, "control: the right session reaches the body check")
        self.assertEqual(self.raw(b"X-Skillsync-CSRF: \xff\xfe\xfd\r\n"), baseline, "the first CSRF header counts: a second one outside ASCII changes nothing")
        self.assertEqual(self.raw(b"Origin: http://127.0.0.1:%d\r\n" % self.port), 403, "two Origin headers never pass")
        self.assertEqual(self.raw(b"", session=False), 401, "no cookie")


class VerboseLog(ProjectCase):
    def test_the_request_log_has_no_secrets_even_with_verbose(self) -> None:
        import threading
        logged = []
        kit = SessionKit(self)
        board = srv.Board(self.root, prototype_port=1)
        server, port = bind_free(lambda port: srv.make_server(board, "127.0.0.1", port, verbose=True, proto_app=kit.app), free_port)
        kit.attach(server)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        login = kit.login(port)
        secrets_ = (login.csrf, login.pair.split("=", 1)[1], SESSION_PW, TEST_TOKEN)
        with mock.patch.object(srv, "log", lambda text, *a, **k: logged.append(str(text))), mock.patch.object(sys.stderr, "write", lambda text: logged.append(text)):
            raw_post(port, {**login.headers(), "X-Approve-Token": TEST_TOKEN, "Content-Length": "2"}, b"{}")
            raw_post(port, {**login.headers(), "X-Skillsync-CSRF": "wrong", "Content-Length": "2"}, b"{}")
        self.assertTrue(logged, "verbose logs the requests")
        self.assertFalse([line for line in logged if any(secret in line for secret in secrets_) or "wrong" in line])


class ServeWritesAndRemovesTheState(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="dashboard-serve-token-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.root = make_project(self.tmp)
        __import__("shutil").rmtree(self.root / ".git")   # outside a git project: the server is one tab and `url` finds it by the root (CORE-1180)
        manifest = self.root / srv.MANIFEST
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("skills: [dashboard]\n", encoding="utf-8")
        self.data = self.tmp / "xdg-data"
        self.env = {**os.environ, "SKILLSYNC_CONFIG": str(self.tmp / "machine-config.yaml"),
                    "XDG_CACHE_HOME": str(self.tmp / "xdg-cache"), **home_env(self.tmp)}
        self.layer = ["--layer", f"core={ROOT / 'library'}"]

    def start(self, *extra, prepare=None, ready=None):
        """Start `dashboard serve` on a free port and wait until it is ready (`/healthz` answers, the state file of this very process exists, and `ready(port)` holds).

        `free_port()` only says that a port WAS free: another process of the machine (a second run of this module, a parallel module) can take it
        before the server binds. The server then exits at once, while the OTHER server on that port still answers `/healthz` and a test that
        trusts the answer reads state that is not its own (measured: the leftover test saw the stale file of the previous server). So the
        answer must be backed by the state file of our own pid (a server that lost the bind never writes one, and it takes longer than any fixed pause to import and exit), and a start that failed is retried on another port; `prepare(port)` runs before every try."""
        return start_serve(self, CLI, self.root, self.layer, self.env, free_port, *extra, prepare=prepare, ready=ready)

    def own_state(self, proc, port):
        """The state file of THIS server process: only the server that holds the port writes one with its pid, so an answer of `/healthz`
        from a foreign server on the same port (a lost race for the port) is not taken for ours."""
        for path in self.state_files():
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if record.get("pid") == proc.pid and record.get("port") == port:
                return True
        return False

    def state_files(self):
        with mock.patch.dict(os.environ, home_env(self.tmp)):   # where the server of this test writes (XDG, LOCALAPPDATA or ~/Library)
            directory = dashboard_token.state_dir()
        return sorted(directory.glob("*.json")) if directory.is_dir() else []

    def test_serve_prints_the_plain_address_writes_the_file_without_a_token_and_removes_it(self) -> None:
        proc, port = self.start()
        files = self.state_files()
        self.assertEqual(len(files), 1)
        record = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual((record["approvals"], "token" in record, "tokenId" in record), (True, False, False))
        env = {**self.env}
        url = subprocess.run([sys.executable, str(CLI), "dashboard", "url", str(self.root), *self.layer], capture_output=True, text=True,
                             env=env, timeout=30)
        self.assertEqual((url.returncode, url.stdout.strip()), (0, f"http://127.0.0.1:{port}/"), url.stderr)
        _out, err, _graceful = stop_serve(proc)
        self.assertIn(f"http://127.0.0.1:{port}/", err)
        self.assertNotIn("#zgoda=", err)   # contract U-2: the console asks for a login, it has no address with a token

    @unittest.skipIf(os.name == "nt", "SIGTERM kończy proces na Windows bez obsługi")
    def test_the_state_file_is_removed_when_the_server_stops(self) -> None:
        """A graceful stop (SIGTERM) removes the state file, with or without consents; Windows has no handler to test (see `stop_serve`)."""
        for extra in ((), ("--no-approvals",)):
            with self.subTest(extra=extra):
                proc, _port = self.start(*extra)
                self.assertEqual(len(self.state_files()), 1)
                _out, err, _graceful = stop_serve(proc)
                self.assertEqual(proc.returncode, 0, err)
                self.assertEqual(self.state_files(), [], "the state file is removed when the server stops")
                after = subprocess.run([sys.executable, str(CLI), "dashboard", "url", str(self.root), "--json", *self.layer], capture_output=True,
                                       text=True, env=self.env, timeout=30)
                self.assertEqual((after.returncode, json.loads(after.stdout)["code"]), (2, "server_not_running"))

    def test_an_approve_host_gets_its_own_address_and_the_state_lists_it(self) -> None:
        proc, port = self.start("--approve-host", "demo.example")
        files = self.state_files()
        record = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(record["hosts"], ["demo.example"])
        url = subprocess.run([sys.executable, str(CLI), "dashboard", "url", str(self.root), "--host", "demo.example", *self.layer],
                             capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual(url.stdout.strip(), f"http://demo.example:{port}/", url.stderr)
        _out, err, _graceful = stop_serve(proc)
        self.assertIn(f"http://demo.example:{port}/", err)
        self.assertNotIn("#zgoda=", err)

    def test_a_foreign_server_on_the_chosen_port_does_not_fool_the_start(self) -> None:
        """The race of the flake: `free_port()` returns a port, another process takes it before the bind, the server of the test exits, and
        the foreign `/healthz` answers. The start must notice and move on to another port."""
        class Foreign(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                body = b'{"ok": true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        foreign, taken = bind_free(lambda port: ThreadingHTTPServer(("127.0.0.1", port), Foreign), free_port)   # a port of the pool: the server accepts it
        threading.Thread(target=foreign.serve_forever, daemon=True).start()
        self.addCleanup(foreign.server_close)
        self.addCleanup(foreign.shutdown)
        first = [taken]
        real = free_port
        with mock.patch.object(sys.modules[__name__], "free_port", lambda: first.pop() if first else real()):
            proc, port = self.start("--no-approvals")
        self.assertNotEqual(port, taken, "the start trusted the answer of a foreign server")
        self.assertIsNone(proc.poll(), "the server of the test is the one that runs")
        self.assertEqual([json.loads(path.read_text(encoding="utf-8"))["port"] for path in self.state_files()], [port])
        stop_serve(proc)

    def test_a_leftover_of_a_killed_server_is_removed_by_a_server_without_consents(self) -> None:
        stale = []

        def leave_a_leftover(port):
            with mock.patch.dict(os.environ, home_env(self.tmp)):
                stale[:] = [dashboard_token.write_state(self.root, port, "dead-token")]
            self.assertTrue(stale[0].is_file())

        def replaced(_port):    # the new server has written its own record over the leftover
            return json.loads(stale[0].read_text(encoding="utf-8")).get("approvals") is False

        proc, port = self.start("--no-approvals", prepare=leave_a_leftover, ready=replaced)
        record = json.loads(stale[0].read_text(encoding="utf-8"))
        self.assertNotIn("token", record, "the token of the dead server is gone, not kept")
        self.assertNotIn("dead-token", json.dumps(record))
        self.assertIs(record.get("approvals"), False, "a server without consents says so")
        self.assertEqual((record["pid"] != os.getpid(), record["port"]), (True, port))
        stop_serve(proc)

    def test_no_approvals_prints_no_token_and_writes_a_file_without_one(self) -> None:
        proc, port = self.start("--no-approvals")
        files = self.state_files()
        self.assertEqual(len(files), 1)
        record = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual((record["approvals"], "token" in record, "tokenId" in record), (False, False, False))
        url = subprocess.run([sys.executable, str(CLI), "dashboard", "url", str(self.root), "--json", *self.layer], capture_output=True, text=True,
                             env=self.env, timeout=30)
        self.assertEqual((url.returncode, json.loads(url.stdout)["code"]), (2, "approvals_disabled"), url.stdout)
        _out, err, _graceful = stop_serve(proc)
        self.assertNotIn("#zgoda=", err)


if __name__ == "__main__":
    unittest.main()
