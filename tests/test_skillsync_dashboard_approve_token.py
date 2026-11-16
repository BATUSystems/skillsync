"""B1: the token of the consents of the status page (`X-Approve-Token`), the state file and `skillsync dashboard url`.

The token is made per process, is checked LAST (every refusal before it stays as it was), never appears in a status, an asset or a log, and
reaches the user in the fragment of an address (`#zgoda=<token>`) from the console of `dashboard serve` or from `dashboard url`."""
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

from skillsync_workflow import dashboard_http as srv  # noqa: E402
from skillsync_workflow import dashboard_token  # noqa: E402
from tests.serve_start import bind_free, start_serve  # noqa: E402
from tests.test_skillsync_dashboard_server import (CLI, PLAN, TEST_TOKEN, HttpCase, ProjectCase, free_port,  # noqa: E402
                                                   install_fake_cli, make_project, raw_post, spy_core)

HEADERS = {"Content-Type": "application/json", "X-Status-Board": "1"}


def home_env(base) -> dict:
    """The directories of the machine state under `base`: on macOS the data directory is `~/Library/Application Support` and on Windows
    `LOCALAPPDATA` (`Path.home()` follows `HOME`/`USERPROFILE`), so `XDG_DATA_HOME` alone leaves the real home to the tests (state files of
    the server pile up there, and two runs at once see each other's)."""
    base = Path(base)
    return {"XDG_DATA_HOME": str(base / "xdg-data"), "LOCALAPPDATA": str(base / "xdg-data"), "HOME": str(base / "home"),
            "USERPROFILE": str(base / "home")}


class TokenCase(HttpCase):
    SPEC2 = "docs/specs/demo-etap-2.md"
    PLAN2 = "docs/plans/2026-01-20-demo-etap-2.md"

    def setUp(self) -> None:
        super().setUp()
        install_fake_cli(self)
        spy_core(self)
        self.ok = {**HEADERS, "Origin": f"http://127.0.0.1:{self.port}"}

    def offer(self) -> dict:
        (self.root / srv.RUNS_DIR / "2026-01-20-demo-etap-2").mkdir(parents=True, exist_ok=True)
        spec = self.root / self.SPEC2
        spec.write_text(f"# Spec: Demo 2\n\nStatus: draft (czeka na GO plan)\nPlan: {self.PLAN2}\n", encoding="utf-8")
        later = (self.root / PLAN).stat().st_mtime + 100
        os.utime(spec, (later, later))
        with self.get("/api/status") as r:
            action = next(a for a in json.load(r)["approvals"]["actions"] if a["kind"] == "spec")
        return {"kind": "spec", "path": self.SPEC2, "sha": action["sha"]}


class TokenRequired(TokenCase):
    def test_post_without_a_token_is_refused_with_the_token_message_and_writes_nothing(self) -> None:
        code, body = self.post(self.offer(), self.ok)
        self.assertEqual((code, body), (403, {"error": srv.TOKEN_REFUSED, "code": "approve_token"}))
        self.set_doc.assert_not_called()

    def test_a_wrong_token_gets_the_same_answer_as_a_missing_one(self) -> None:
        data = self.offer()
        answers = {json.dumps(self.post(data, {**self.ok, **extra}))
                   for extra in ({}, {"X-Approve-Token": ""}, {"X-Approve-Token": "x"}, {"X-Approve-Token": TEST_TOKEN + "x"},
                                 {"X-Approve-Token": TEST_TOKEN[:-1]}, {"X-Approve-Token": TEST_TOKEN.upper()})}
        self.assertEqual(answers, {json.dumps([403, {"error": srv.TOKEN_REFUSED, "code": "approve_token"}])})
        self.set_doc.assert_not_called()

    def test_the_right_token_keeps_every_existing_behaviour(self) -> None:
        data = self.offer()
        code, body = self.post(data, {**self.ok, "X-Approve-Token": TEST_TOKEN})
        self.assertEqual((code, body["word"], body["file"]), (200, "GO plan", self.SPEC2), body)
        self.set_doc.assert_called_once()

    def test_the_token_is_compared_in_constant_time(self) -> None:
        with mock.patch.object(srv.hmac, "compare_digest", wraps=srv.hmac.compare_digest) as compare:
            self.post(self.offer(), {**self.ok, "X-Approve-Token": "wrong"})
        self.assertEqual(compare.call_count, 1)
        self.assertEqual(compare.call_args.args[1], TEST_TOKEN.encode("utf-8"))

    def test_every_refusal_before_the_token_is_the_same_with_and_without_it(self) -> None:
        data = self.offer()
        cases = {
            "Host": ({"Host": "evil.example"}, srv.HOST_REFUSED),
            "X-Status-Board": ({"X-Status-Board": "0"}, "Brak nagłówka X-Status-Board."),
            "Origin": ({"Origin": "https://evil.example"}, "Obcy Origin."),
            "Content-Type": ({"Content-Type": "text/plain"}, "Oczekiwano application/json."),
        }
        for name, (change, message) in cases.items():
            for token in (None, TEST_TOKEN, "wrong"):
                with self.subTest(refusal=name, token=token):
                    headers = {**self.ok, **change}
                    if token:
                        headers["X-Approve-Token"] = token
                    self.assertEqual(self.post(data, headers), (403, {"error": message}))
        self.set_doc.assert_not_called()

    def test_the_token_is_checked_before_the_body_is_read(self) -> None:
        code, body = raw_post(self.port, {**self.ok, "Content-Length": str(srv.MAX_BODY + 1)})
        self.assertEqual((code, body), (403, {"error": srv.TOKEN_REFUSED, "code": "approve_token"}))   # 403, not 400: the token before the length
        code, body = raw_post(self.port, {**self.ok, "X-Approve-Token": TEST_TOKEN, "Content-Length": str(srv.MAX_BODY + 1)})
        self.assertEqual(code, 400)   # with the token the length is judged as before


class TokenNeverLeaves(TokenCase):
    def test_status_says_a_token_is_required_but_never_shows_it(self) -> None:
        with self.get("/api/status") as r:
            raw = r.read().decode("utf-8")
        self.assertIs(json.loads(raw)["approvals"]["tokenRequired"], True)
        self.assertNotIn(TEST_TOKEN, raw)

    def test_no_response_of_a_get_contains_the_token(self) -> None:
        paths = ["/", "/index.html", "/api/status", "/api/descriptions", "/healthz", "/assets/app.js", "/assets/dashboard-render.js",
                 "/assets/app.css", "/assets/theme.js"]
        for path in paths:
            with self.subTest(path=path):
                with self.get(path) as r:
                    body = r.read().decode("utf-8", "replace")
                    headers = str(r.headers)
                self.assertNotIn(TEST_TOKEN, body)
                self.assertNotIn(TEST_TOKEN, headers)

    def test_the_token_never_reaches_the_log_or_an_error_answer(self) -> None:
        logged = []
        data = self.offer()
        with mock.patch.object(srv, "log", lambda text, *a, **k: logged.append(str(text))):
            for token in ("wrong", TEST_TOKEN):
                code, body = self.post(data, {**self.ok, "X-Approve-Token": token})
                self.assertNotIn(TEST_TOKEN, json.dumps(body) if code != 200 else "")
            self.post({"kind": "rm"}, {**self.ok, "X-Approve-Token": TEST_TOKEN})
        self.assertFalse([line for line in logged if TEST_TOKEN in line or "wrong" in line])


class NoApprovals(HttpCase):
    APPROVALS = False

    def test_a_read_only_page_has_no_token_and_says_so(self) -> None:
        self.assertIsNone(self.board.approve_token)
        with self.get("/api/status") as r:
            self.assertIs(json.load(r)["approvals"]["tokenRequired"], False)
        code, body = self.post({"kind": "cp", "path": "x", "sha": "a" * 7}, {**HEADERS, "X-Approve-Token": "anything"})
        self.assertEqual(code, 403)
        self.assertIn("--no-approvals", body["error"])   # refused as before, not as a token problem


class TokenPerProcess(ProjectCase):
    def test_every_board_gets_its_own_urlsafe_token(self) -> None:
        tokens = {srv.Board(self.root, prototype_port=1).approve_token for _ in range(5)}
        self.assertEqual(len(tokens), 5)
        for token in tokens:
            self.assertRegex(token, r"^[A-Za-z0-9_-]{32,}$")

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
        path = dashboard_token.write_state(self.project, 5140, "tok-1")
        self.assertIsNotNone(path)
        self.assertEqual(path.parent, dashboard_token.state_dir())
        for form in (str(self.project.resolve()), dashboard_token.root_text(self.project)):   # native and report form (`/` on Windows)
            self.assertNotIn(form, str(path))
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((record["schema"], record["port"], record["token"], record["root"], record["pid"]),
                         (1, 5140, "tok-1", dashboard_token.root_text(self.project), os.getpid()))   # the report form of the path
        self.assertEqual([p.name for p in path.parent.iterdir()], [path.name], "no temporary file is left")

    @unittest.skipIf(os.name == "nt", "POSIX: tryb 0600 pliku i 0700 katalogu stanu (na Windows ACL: test_skillsync_winacl)")
    def test_the_file_is_private_on_posix(self) -> None:
        path = dashboard_token.write_state(self.project, 5140, "tok-1")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_the_restriction_is_made_on_the_temporary_file_before_the_replace(self) -> None:
        events = []
        real_replace = os.replace
        with mock.patch.object(dashboard_token, "_restrict", lambda path: events.append(("restrict", Path(path).name)) or True), \
                mock.patch.object(dashboard_token.os, "replace", lambda a, b: events.append(("replace", Path(a).name)) or real_replace(a, b)):
            path = dashboard_token.write_state(self.project, 5141, "tok-2")
        kinds = [kind for kind, _ in events]
        self.assertEqual(kinds, ["restrict", "restrict", "replace"], events)   # the directory, the temporary file, then the replace
        self.assertTrue(events[1][1].startswith(".tmp-") and events[2][1] == events[1][1])
        self.assertTrue(path.is_file())

    def test_a_failed_restriction_warns_once_and_the_file_is_still_written(self) -> None:
        warnings = []
        with mock.patch.object(dashboard_token, "_restrict", lambda path: False):
            path = dashboard_token.write_state(self.project, 5142, "tok-3", warn=warnings.append)
        self.assertTrue(path.is_file())
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("tok-3", warnings[0])

    def test_an_unwritable_state_directory_does_not_stop_the_server(self) -> None:
        warnings = []
        with mock.patch.object(dashboard_token.tempfile, "mkstemp", side_effect=PermissionError(13, "denied")):
            self.assertIsNone(dashboard_token.write_state(self.project, 5143, "tok-4", warn=warnings.append))
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("tok-4", warnings[0])

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
                path = dashboard_token.write_state("ignored", 5190, "tok-win")
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
        path = dashboard_token.write_state(self.project, 5144, "tok-5")
        dashboard_token.remove_state(self.project, 5144)
        self.assertFalse(path.exists())
        dashboard_token.remove_state(self.project, 5144)

    def test_remove_state_with_a_pid_deletes_only_the_file_of_that_process(self) -> None:
        """An old server that stops late (or one that lost the port) must not delete the file of the server that holds the port now."""
        path = dashboard_token.write_state(self.project, 5145, "tok-new")
        dashboard_token.remove_state(self.project, 5145, os.getpid() + 1)   # a stopping server that is not the writer
        self.assertTrue(path.is_file(), "the file of another process stays")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["token"], "tok-new")
        dashboard_token.remove_state(self.project, 5145, os.getpid())
        self.assertFalse(path.exists(), "the writer removes its own file")
        dashboard_token.remove_state(self.project, 5145, os.getpid())   # gone: quiet

    def test_the_refusal_without_consents_does_not_speak_of_a_token(self) -> None:
        state = dashboard_token.state_dir()
        state.mkdir(parents=True)
        warnings = []
        with mock.patch.object(dashboard_token, "_own_directory", lambda directory: False):
            self.assertIsNone(dashboard_token.write_state(self.project, 5183, None, warn=warnings.append))
            self.assertIsNone(dashboard_token.write_state(self.project, 5184, "tok-x", warn=warnings.append))
        self.assertNotIn("token", warnings[0].lower())
        self.assertIn("tokenu", warnings[1])

    def test_servers_lists_only_healthy_servers_of_this_project(self) -> None:
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        dashboard_token.write_state(self.project, 5150, "tok-a")
        dashboard_token.write_state(self.project, 5151, "tok-b")
        dashboard_token.write_state(other, 5152, "tok-c")
        everything = dashboard_token.servers(self.project, healthy=lambda host, port, token_id=None: True)
        self.assertEqual([(s["port"], s["token"]) for s in everything], [(5150, "tok-a"), (5151, "tok-b")])
        only = dashboard_token.servers(self.project, 5151, healthy=lambda host, port, token_id=None: True)
        self.assertEqual([s["port"] for s in only], [5151])
        self.assertEqual(dashboard_token.servers(self.project, healthy=lambda host, port, token_id=None: port == 5150)[0]["port"], 5150,
                         "a leftover whose port answers no status page is ignored")
        self.assertEqual(dashboard_token.servers(self.project, healthy=lambda host, port, token_id=None: False), [])

    def test_a_dead_process_and_a_foreign_or_broken_file_are_ignored(self) -> None:
        path = dashboard_token.write_state(self.project, 5160, "tok-d")
        record = json.loads(path.read_text(encoding="utf-8"))
        if os.name != "nt":   # Windows decides by /healthz only
            record["pid"] = 2 ** 22 + 12345
            path.write_text(json.dumps(record), encoding="utf-8")
            self.assertEqual(dashboard_token.servers(self.project, healthy=lambda host, port, token_id=None: True), [])
        path.write_text("not json", encoding="utf-8")
        self.assertEqual(dashboard_token.servers(self.project, healthy=lambda host, port, token_id=None: True), [])
        path.write_text(json.dumps({**record, "schema": 2, "pid": os.getpid()}), encoding="utf-8")
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

    def test_a_state_file_without_a_token_is_kept_only_when_it_says_no_approvals(self) -> None:
        path = dashboard_token.write_state(self.project, 5180, None)
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((record["approvals"], "token" in record), (False, False))
        listed = dashboard_token.servers(self.project, healthy=lambda host, port, token_id=None: token_id is None)
        self.assertEqual([(item["port"], item["approvals"], item["token"]) for item in listed], [(5180, False, None)])
        del record["approvals"]                      # a record that is neither with a token nor "approvals: false" is broken
        path.write_text(json.dumps(record), encoding="utf-8")
        self.assertEqual(dashboard_token.servers(self.project, healthy=lambda host, port, token_id=None: True), [])

    @unittest.skipIf(os.name == "nt", "POSIX: dowiązania i właściciel katalogu")
    def test_a_state_directory_that_is_a_link_is_refused_and_nothing_is_written_through_it(self) -> None:
        elsewhere = Path(self.tmp.name) / "elsewhere"
        elsewhere.mkdir()
        state = dashboard_token.state_dir()
        state.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(elsewhere, state)
        warnings = []
        self.assertIsNone(dashboard_token.write_state(self.project, 5181, "tok-link", warn=warnings.append))
        self.assertEqual(list(elsewhere.iterdir()), [], "the token went nowhere")
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("tok-link", warnings[0])
        self.assertTrue(state.is_symlink(), "the link itself is left alone (not chmod-ed through, not removed)")

    @unittest.skipIf(os.name == "nt", "POSIX: dowiązania i właściciel katalogu")
    def test_a_state_directory_of_another_user_is_refused(self) -> None:
        dashboard_token.state_dir().mkdir(parents=True)
        warnings = []
        with mock.patch.object(dashboard_token.os, "geteuid", lambda: os.stat(dashboard_token.state_dir()).st_uid + 1):
            self.assertIsNone(dashboard_token.write_state(self.project, 5182, "tok-uid", warn=warnings.append))
        self.assertEqual(len(warnings), 1)
        self.assertEqual(list(dashboard_token.state_dir().iterdir()), [])

    def test_the_ordinary_case_is_not_refused(self) -> None:
        warnings = []
        self.assertTrue(dashboard_token.write_state(self.project, 5183, "tok-ok", warn=warnings.append).is_file())
        self.assertTrue(dashboard_token.write_state(self.project, 5183, "tok-ok2", warn=warnings.append).is_file(), "a second write over an existing directory")
        # a refusal says "nie zapisano ..."; the note about a missing ACL helper (this branch has none on Windows) is another thing
        self.assertEqual([text for text in warnings if "nie zapisano" in text], [])

    def test_a_start_removes_the_leftover_temporary_files_of_its_own_project_and_port_only(self) -> None:
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        dashboard_token.write_state(self.project, 5190, "tok-first")
        directory = dashboard_token.state_dir()
        digest = dashboard_token._digest(self.project)
        mine = directory / f".tmp-{digest}-5190-leftover1.json"
        mine.write_text('{"token": "dead"}', encoding="utf-8")
        same_project_other_port = directory / f".tmp-{digest}-5191-leftover2.json"
        same_project_other_port.write_text("x", encoding="utf-8")
        other_project = directory / f".tmp-{dashboard_token._digest(other)}-5190-leftover3.json"
        other_project.write_text("x", encoding="utf-8")
        dashboard_token.write_state(self.project, 5190, "tok-second")
        self.assertFalse(mine.exists(), "a temporary file of this project and port is swept")
        self.assertTrue(same_project_other_port.exists())
        self.assertTrue(other_project.exists())

    def test_the_address_has_the_token_in_the_fragment_only(self) -> None:
        self.assertEqual(dashboard_token.address(5140, "t0k"), "http://127.0.0.1:5140/#zgoda=t0k")
        self.assertEqual(dashboard_token.address(5140, "t0k", "demo.example"), "http://demo.example:5140/#zgoda=t0k")
        self.assertEqual(dashboard_token.address(5140, "t0k", "::1"), "http://[::1]:5140/#zgoda=t0k")


class UrlCommand(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="dashboard-url-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.root = make_project(self.tmp)
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
        dashboard_token.write_state(self.root, 5170, "tok-url")
        code, out = self.run_url()
        report = json.loads(out[0]) if out else {}
        self.assertEqual(code, 0, out)
        self.assertEqual((report["status"], report["url"], report["port"]), ("PASS", "http://127.0.0.1:5170/#zgoda=tok-url", 5170))

    def test_plain_output_is_just_the_address(self) -> None:
        dashboard_token.write_state(self.root, 5171, "tok-plain")
        code, out = self.run_url(json=False)
        self.assertEqual((code, out), (0, ["http://127.0.0.1:5171/#zgoda=tok-plain"]))

    def test_no_server_gives_blocked_server_not_running(self) -> None:
        code, out = self.run_url()
        report = json.loads(out[0])
        self.assertEqual((code, report["status"], report["code"]), (2, "BLOCKED", "server_not_running"))

    def test_two_servers_need_a_port(self) -> None:
        dashboard_token.write_state(self.root, 5172, "tok-1")
        dashboard_token.write_state(self.root, 5173, "tok-2")
        code, out = self.run_url()
        self.assertEqual((code, json.loads(out[0])["code"]), (2, "ambiguous_server"))
        code, out = self.run_url(port=5173)
        self.assertEqual((code, json.loads(out[0])["url"]), (0, "http://127.0.0.1:5173/#zgoda=tok-2"))

    def test_a_named_host_is_used_only_when_the_server_was_started_with_it(self) -> None:
        dashboard_token.write_state(self.root, 5174, "tok-h", hosts=["demo.example"])
        code, out = self.run_url(host="demo.example")
        self.assertEqual((code, json.loads(out[0])["url"]), (0, "http://demo.example:5174/#zgoda=tok-h"))
        code, out = self.run_url(host="DEMO.example")
        self.assertEqual(code, 0, "names compare without case")
        code, out = self.run_url(host="localhost")
        self.assertEqual((code, json.loads(out[0])["url"]), (0, "http://localhost:5174/#zgoda=tok-h"))
        code, out = self.run_url(host="evil.example")
        self.assertEqual((code, json.loads(out[0])["code"]), (2, "host_not_allowed"))
        self.assertNotIn("tok-h", out[0])

    def test_a_server_without_consents_is_approvals_disabled_not_server_not_running(self) -> None:
        dashboard_token.write_state(self.root, 5175, None)
        code, out = self.run_url()
        report = json.loads(out[0])
        self.assertEqual((code, report["status"], report["code"]), (2, "BLOCKED", "approvals_disabled"))
        self.assertIn("5175", report["reason"])

    def test_with_a_server_with_and_one_without_consents_the_address_is_of_the_first(self) -> None:
        dashboard_token.write_state(self.root, 5176, None)
        dashboard_token.write_state(self.root, 5177, "tok-on")
        code, out = self.run_url()
        self.assertEqual((code, json.loads(out[0])["url"]), (0, "http://127.0.0.1:5177/#zgoda=tok-on"))
        code, out = self.run_url(port=5176)
        self.assertEqual((code, json.loads(out[0])["code"]), (2, "approvals_disabled"))

    def test_a_server_bound_to_an_address_the_page_refuses_is_offered_on_loopback(self) -> None:
        dashboard_token.write_state(self.root, 5178, "tok-ip", host="192.168.1.5")    # the probe goes to its own address (the file)
        code, out = self.run_url()
        self.assertEqual((code, json.loads(out[0])["url"]), (0, "http://127.0.0.1:5178/#zgoda=tok-ip"))

    def test_a_broken_project_is_blocked(self) -> None:
        code, out = self.run_url(path=str(self.tmp / "nope"))
        self.assertEqual((code, json.loads(out[0])["code"]), (2, "invalid_project"))


class HealthAndLeftovers(unittest.TestCase):
    """`/healthz` carries a digest of the token (never the token): a stranger on a port is told apart without sending anything secret."""

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
        path = dashboard_token.write_state(self.project, 5210, "tok-dead")
        record = json.loads(path.read_text(encoding="utf-8"))
        record["pid"] = 2 ** 22 + 4321
        path.write_text(json.dumps(record), encoding="utf-8")
        self.assertEqual(dashboard_token.servers(self.project, healthy=lambda host, port, token_id=None: True), [])
        self.assertFalse(path.exists())

    def test_the_state_records_the_token_digest_the_bind_name_and_the_allowed_hosts(self) -> None:
        path = dashboard_token.write_state(self.project, 5211, "tok-x", "192.0.2.7", hosts=["demo.example"])
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((record["host"], record["hosts"], record["tokenId"]), ("192.0.2.7", ["demo.example"], dashboard_token.token_id("tok-x")))
        self.assertEqual(len(record["tokenId"]), 8)
        self.assertNotIn("tok-x", record["tokenId"])
        self.assertEqual([dashboard_token.bind_name(h) for h in ("", "0.0.0.0", "::", "127.0.0.1", "::1", "192.0.2.7")],
                         ["127.0.0.1", "127.0.0.1", "127.0.0.1", "127.0.0.1", "::1", "192.0.2.7"])


class HealthzOfTheServer(TokenCase):
    def test_healthz_has_the_digest_and_never_the_token(self) -> None:
        with self.get("/healthz") as r:
            body = json.load(r)
        self.assertEqual(body["tokenId"], dashboard_token.token_id(TEST_TOKEN))
        self.assertNotIn(TEST_TOKEN, json.dumps(body))
        self.assertTrue(dashboard_token._healthy("127.0.0.1", self.port, dashboard_token.token_id(TEST_TOKEN)))
        self.assertFalse(dashboard_token._healthy("127.0.0.1", self.port, "00000000"))


class HeaderEdgeCases(TokenCase):
    def raw(self, header_lines: bytes, body: bytes = b"{}") -> int:
        import socket
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(b"POST /api/approve HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nX-Status-Board: 1\r\n"
                         + header_lines + b"Content-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
            data = b""
            while chunk := sock.recv(4096):
                data += chunk
        return int(data.split(b" ", 2)[1])

    def test_a_header_outside_ascii_and_two_headers_never_pass(self) -> None:
        self.assertEqual(self.raw(b"X-Approve-Token: \xff\xfe\xfd\r\n"), 403)
        self.assertEqual(self.raw(b"X-Approve-Token: wrong\r\nX-Approve-Token: " + TEST_TOKEN.encode() + b"\r\n"), 403, "the first header counts")
        self.assertEqual(self.raw(b"X-Approve-Token: " + TEST_TOKEN.encode() + b"\r\n"), 400, "the right token reaches the body check")


class VerboseLog(ProjectCase):
    def test_the_request_log_has_no_token_even_with_verbose(self) -> None:
        import threading
        logged = []
        board = srv.Board(self.root, prototype_port=1, approve_token=TEST_TOKEN)
        server, port = bind_free(lambda port: srv.make_server(board, "127.0.0.1", port, verbose=True), free_port)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        with mock.patch.object(srv, "log", lambda text, *a, **k: logged.append(str(text))), mock.patch.object(sys.stderr, "write", lambda text: logged.append(text)):
            raw_post(port, {**HEADERS, "X-Approve-Token": TEST_TOKEN, "Content-Length": "2"}, b"{}")
            raw_post(port, {**HEADERS, "X-Approve-Token": "wrong", "Content-Length": "2"}, b"{}")
        self.assertTrue(logged, "verbose logs the requests")
        self.assertFalse([line for line in logged if TEST_TOKEN in line or "wrong" in line])


@unittest.skipIf(os.name == "nt", "SIGTERM kończy proces na Windows bez obsługi")
class ServeWritesAndRemovesTheState(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="dashboard-serve-token-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.root = make_project(self.tmp)
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

    def test_serve_prints_the_address_with_the_token_writes_the_file_and_removes_it(self) -> None:
        proc, port = self.start()
        files = self.state_files()
        self.assertEqual(len(files), 1)
        token = json.loads(files[0].read_text(encoding="utf-8"))["token"]
        env = {**self.env}
        url = subprocess.run([sys.executable, str(CLI), "dashboard", "url", str(self.root), *self.layer], capture_output=True, text=True,
                             env=env, timeout=30)
        self.assertEqual((url.returncode, url.stdout.strip()), (0, f"http://127.0.0.1:{port}/#zgoda={token}"), url.stderr)
        proc.terminate()
        _out, err = proc.communicate(timeout=15)
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(err.count(f"http://127.0.0.1:{port}/#zgoda={token}"), 1, "the address with the token is printed once")
        self.assertEqual(self.state_files(), [], "the state file is removed when the server stops")
        after = subprocess.run([sys.executable, str(CLI), "dashboard", "url", str(self.root), "--json", *self.layer], capture_output=True,
                               text=True, env=env, timeout=30)
        self.assertEqual((after.returncode, json.loads(after.stdout)["code"]), (2, "server_not_running"))

    def test_an_approve_host_gets_its_own_address_and_the_state_lists_it(self) -> None:
        proc, port = self.start("--approve-host", "demo.example")
        files = self.state_files()
        record = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(record["hosts"], ["demo.example"])
        url = subprocess.run([sys.executable, str(CLI), "dashboard", "url", str(self.root), "--host", "demo.example", *self.layer],
                             capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual(url.stdout.strip(), f"http://demo.example:{port}/#zgoda={record['token']}", url.stderr)
        proc.terminate()
        _out, err = proc.communicate(timeout=15)
        self.assertIn(f"http://demo.example:{port}/#zgoda={record['token']}", err)

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
        proc.terminate()
        proc.communicate(timeout=15)

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
        self.assertIs(record.get("approvals"), False, "a server without consents says so")
        self.assertEqual((record["pid"] != os.getpid(), record["port"]), (True, port))
        proc.terminate()
        proc.communicate(timeout=15)

    def test_no_approvals_prints_no_token_and_writes_a_file_without_one(self) -> None:
        proc, port = self.start("--no-approvals")
        files = self.state_files()
        self.assertEqual(len(files), 1)
        record = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual((record["approvals"], "token" in record, "tokenId" in record), (False, False, False))
        url = subprocess.run([sys.executable, str(CLI), "dashboard", "url", str(self.root), "--json", *self.layer], capture_output=True, text=True,
                             env=self.env, timeout=30)
        self.assertEqual((url.returncode, json.loads(url.stdout)["code"]), (2, "approvals_disabled"), url.stdout)
        proc.terminate()
        _out, err = proc.communicate(timeout=15)
        self.assertNotIn("#zgoda=", err)
        self.assertEqual(self.state_files(), [], "removed when the server stops, with or without consents")


if __name__ == "__main__":
    unittest.main()
