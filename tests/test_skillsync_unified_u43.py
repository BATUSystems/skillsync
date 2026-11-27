"""CORE-2000-UNIFIED U-4.3: what is left of the page after U-4.2.
python3 -m unittest tests.test_skillsync_unified_u43

- ANON-4: no field the server composes for a request without a session (status, tabs, history, tests) carries an absolute path (POSIX, Windows,
  UNC), also outside the project and the home directory; the raw messages of the tests stay as printed (decision of 2026-11-27);
- the tab of the tests tells a reader without a session that the results are raw (not with `tests.mask`);
- CP / READY name `delivery.base` too (409 `delivery_changed`);
- the audit: `consent_intent` before the write, `consent` only after a write that happened; a short write of an audit line is not a success;
  a body that ends before its length is refused; what ends the session while a consent waits for a file lock refuses it;
- the git facts of `GET /api/history` are not asked again while the repository does not change;
- a running server of 1.20.1 (state file with a token) is an old server: nothing is added to it (`legacy_server`);
- the hint of the command that makes an approver parses with the real parser; the CSP of the page has no `'unsafe-inline'`.

Fake secrets and passwords are built at run time (secret scan)."""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import http.client
import io
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from skillsync_workflow import dashboard_history, dashboard_token, feedback, i18n, test_events  # noqa: E402
from skillsync_workflow import dashboard_http as srv  # noqa: E402
from skillsync_workflow import status as status_core  # noqa: E402
import tests.test_skillsync_dashboard_attach as attach  # noqa: E402  (the module, not its test classes: they must not run twice)
import tests.test_skillsync_dashboard_consent_session as consent  # noqa: E402
from tests.test_skillsync_dashboard_renderer import HARNESS, NODE_MAJOR, fixture_files  # noqa: E402
from tests.test_skillsync_dashboard_renderer_i18n import run_node  # noqa: E402
from tests.test_skillsync_dashboard_server import RUN  # noqa: E402
from tmp_paths import descend  # noqa: E402

OUTSIDE = ("/srv/ci/agent-7/work/suite.py", "/opt/tools/bin/runner", "/ho" "me/someone-else/secret/notes.txt", "/data/x/y.cfg", "/tmp/only.log")
WINDOWS = ("C:\\Us" "ers\\Jan Kowalski\\repo\\run.ps1", "D:/agent/_work/1/s/build.cmd", "\\\\fileserver\\share$\\team\\report.xml", "C:\\Program Files\\tool\\x.exe")
FORBIDDEN = ("/srv/ci", "/opt/tools", "someone-else", "/data/x", "/tmp/only", "Kowalski", "C:\\", "D:/agent", "fileserver", "Program Files\\")


class PathFilterTests(unittest.TestCase):
    """`public_text_filter`: the one filter of every text the server composes for a request without a session."""

    def setUp(self) -> None:
        self.clean = srv.public_text_filter([])

    def test_no_absolute_path_survives_and_the_name_of_the_file_stays(self) -> None:
        for path in OUTSIDE + WINDOWS:
            for text in (path, f"run {path} now", f"x --junit={path}", f"python3 {shlex.quote(path)} -q", f"<code>{path}</code>", f"[{path}]: boom",
                         f"PYTHONPATH={path}:{path}"):
                with self.subTest(text=text):
                    shown = self.clean(text)
                    for part in FORBIDDEN:
                        self.assertNotIn(part, shown)
                    self.assertIn(re.split(r"[\\/]", path)[-1], shown)

    def test_a_flag_glued_to_a_path_a_file_url_and_a_home_of_another_user(self) -> None:
        for text, gone in (("gcc -I/usr/local/include x.c", "/usr/local"), ("open file:///srv/ci/report.html", "/srv/ci"),
                           ("in /ho" "me/someone-else", "someone-else"), ("in C:\\Us" "ers\\Jan\\", "Jan"), ("in /Us" "ers/jan", "jan"),
                           ("dir '/srv/Jan Kowalski/work dir/a.py' gone", "Kowalski")):
            with self.subTest(text=text):
                self.assertNotIn(gone, self.clean(text))

    def test_what_is_not_an_absolute_path_stays_as_it_is(self) -> None:
        for text in ("docs/plans/a.md", "./run.sh tests/unit", "~/work/x", "https://example.org/a/b/c?d=/e/f", "http://127.0.0.1:5142/demo/",
                     "GET /api/status and POST /_api/v1/login", "<p>a</p><br/></code></li>", "type /feature or /bugfix", "and / or, 1/2, a/b/c",
                     "s/old/new/g", "${ROOT}/a/b", "**/node_modules/**", "// a comment", "#docs:docs/specs/x.md", "…/report.xml",
                     "C: is a drive, D:rive too", "feature/core-2000-unified"):
            with self.subTest(text=text):
                self.assertEqual(self.clean(text), text)

    def test_the_roots_of_the_project_and_the_home_keep_their_own_rule(self) -> None:
        clean = srv.public_text_filter(["/srv/work/project"])
        self.assertEqual(clean("/srv/work/project/docs/a.md"), "docs/a.md")
        self.assertEqual(clean("at /srv/work/project"), "at project")
        self.assertEqual(clean(os.path.expanduser("~") + "/notes.md"), "~/notes.md")


class QuotedArgumentTests(unittest.TestCase):
    """The command of a test run is composed with `shlex.quote`: a quoted argument that holds a path is filtered, wherever the path stands in it."""

    ARGUMENTS = (("/ResultsDirectory:D:\\agent\\_work\\Jan\\out", ("agent", "_work", "Jan"), "/ResultsDirectory:"),
                 ("/p:OutDir=C:\\x\\Jan Kowalski\\y", ("C:", "Kowalski", "\\x"), "/p:OutDir="),
                 ("/c C:\\x\\f.bat", ("C:", "\\x"), "/c "),
                 ("/api/x log C:\\Us" "ers\\jan\\s.txt", ("C:", "Users", "jan"), "/api/x log "),
                 ("/p:Out=/srv/Jan Kowalski/work/y", ("/srv", "Kowalski"), "/p:Out="),
                 ("/api/x log /ho" "me/jan/s.txt", ("/home", "jan"), "/api/x log "),
                 ("/ResultsDirectory:\\\\fileserver\\team\\out", ("fileserver", "team"), "/ResultsDirectory:"),
                 ("/srv/Jan Kowalski/work dir/a.py", ("/srv", "Kowalski"), "\u2026/a.py"))

    def command(self, argument: str) -> str:
        tests = {"sections": [{"attempts": [{"command": " ".join(shlex.quote(word) for word in ("dotnet", "test", argument)), "groups": []}]}]}
        return srv.public_tests(tests, srv.public_text_filter([]))["sections"][0]["attempts"][0]["command"]

    def test_no_path_inside_a_quoted_argument_survives(self) -> None:
        for argument, gone, kept in self.ARGUMENTS:
            with self.subTest(argument=argument):
                self.assertTrue(shlex.quote(argument).startswith("'"), "control: shlex really quotes it")
                shown = self.command(argument)
                for part in gone:
                    self.assertNotIn(part, shown)
                self.assertIn(kept, shown)
                self.assertTrue(shown.startswith("dotnet test '") and shown.endswith("'"), shown)

    def test_a_quoted_route_or_single_word_stays(self) -> None:
        clean = srv.public_text_filter([])
        for text in ("'/api/status'", "`/_api/v1/login`", "'/feature'", '"/assets/app.js"', "'/healthz'", "'/c'", "'/p:Configuration=Release'"):
            with self.subTest(text=text):
                self.assertEqual(clean(text), text)

    def test_long_texts_stay_fast(self) -> None:
        clean = srv.public_text_filter([])
        for name, text in (("quoted slashes", "'" + " /a" * 40000 + "'"), ("quoted drive words", "'/c " + "C:\\a " * 20000 + "'"),
                           ("open quotes", "'/a:" * 30000), ("quoted switches", "'" + "/p:x=" * 30000 + "'"), ("home with spaces", "C:\\Us" "ers\\a " * 10000),
                           ("spaces at the end", "C:\\Us" "ers\\a b" + " " * 100000 + "x"), ("backslashes", "'/x " + "\\" * 60000 + "'")):
            with self.subTest(name=name):
                started = time.monotonic()
                clean(text)
                self.assertLess(time.monotonic() - started, 2.0)

    def test_a_windows_home_with_a_space_at_the_end_of_a_text(self) -> None:
        clean = srv.public_text_filter([])
        for text in ("in C:\\Us" "ers\\Jan Kowalski", "in C:\\Us" "ers\\Jan Maria Kowalski  ", "cd C:/Us" "ers/Jan Kowalski\nnext line"):
            with self.subTest(text=text):
                shown = clean(text)
                self.assertNotIn("Kowalski", shown)
                self.assertNotIn("Jan", shown)
        self.assertEqual(clean("in C:\\Us" "ers\\Jan Kowalski\nnext"), "in \u2026\nnext", "control: the next line stays")


class RenderedFieldTests(unittest.TestCase):
    """A path is never substituted inside rendered HTML: such a field is left out and the plain text, which is filtered, stays."""

    def shown(self, text: str, rendered: str) -> dict:
        return srv.public_tree({"text": text, "textHtml": rendered, "html": rendered, "moreHtml": "<p>x</p>", "moreLines": 1}, srv.public_text_filter([]))

    def test_a_field_with_a_path_is_left_out(self) -> None:
        for rendered in ("<code>/srv/ci/x/log.txt</code>", '<a href="/srv/ci/x/log.txt">log</a>', "<strong>/srv</strong>/ci/x/log.txt",
                         "<p>C:\\Us" "ers\\Jan\\log.txt</p>"):
            with self.subTest(rendered=rendered):
                shown = self.shown("see /srv/ci/x/log.txt", rendered)
                self.assertEqual(shown, {"text": "see \u2026/log.txt"})

    def test_a_field_without_a_path_stays_as_it_was_rendered(self) -> None:
        rendered = '<p><a href="#docs:docs/a.md" class="md-doc">docs/a.md</a> <code>/api/status</code><br/></p>'
        shown = self.shown("docs/a.md", rendered)
        self.assertEqual((shown["textHtml"], shown["html"], shown["moreHtml"]), (rendered, rendered, "<p>x</p>"))


class AnonymousPathCase(consent.PageCase):
    def attempt(self) -> str:
        """One finished attempt of a step with absolute paths in its command and in what the tests printed."""
        path = test_events.new_attempt(self.root, consent.RUN1, "S2")[0]
        writer = test_events.EventWriter(path)
        command = " ".join(shlex.quote(item) for item in ["/opt/tools/bin/runner", "--junit=/srv/ci/agent-7/work/out.xml", WINDOWS[0],
                                                          "/ResultsDirectory:D:\\agent\\_work\\Jan\\out", "/api/x log C:\\Us" "ers\\jan\\s.txt"])
        writer.start(run=consent.RUN1, step="S2", stepSource="flag", attempt=1, command=command, tool="runner", pid=os.getpid(), levels="command",
                     treeStart=None)
        writer.test("t1", "reads /srv/raw/name.txt", "failed", group="g /srv/raw/group", msg="boom at /srv/raw/message.py:3")
        writer.end("FAIL", 1, source="command", reason="cannot run /srv/ci/agent-7/bin/x", ms=5)
        return command


class AnonymousPathTests(AnonymousPathCase):
    def test_the_composed_fields_of_the_tests_have_no_absolute_path_and_the_messages_stay_raw(self) -> None:
        self.attempt()
        _status, data = self.get(f"/api/status?plan={consent.PLAN}")
        attempts = [item for section in data["tests"]["sections"] for item in section["attempts"]]
        composed = json.dumps([{key: value for key, value in item.items() if key != "groups"} for item in attempts])
        for part in ("/opt/tools", "/srv/ci", "Kowalski", "_work", "Users"):
            self.assertNotIn(part, composed)
        self.assertIn("…/runner", attempts[0]["command"])
        raw = json.dumps([item["groups"] for item in attempts])
        for part in ("/srv/raw/name.txt", "/srv/raw/group", "/srv/raw/message.py"):
            self.assertIn(part, raw, "the results are what the tests printed")
        _status, body = self.get(f"/api/tests?run={consent.RUN1}&step=S2&attempt=1")
        self.assertIn("/srv/raw/message.py", body["tests"][0]["msg"])
        self.assertIn("/srv/raw/name.txt", body["tests"][0]["name"])

    def test_control_a_signed_in_session_sees_the_command_as_it_was(self) -> None:
        self.attempt()
        _status, data = self.get(f"/api/status?plan={consent.PLAN}", {"Cookie": self.session.pair})
        self.assertIn("/opt/tools/bin/runner", data["tests"]["sections"][0]["attempts"][0]["command"])

    def test_the_tabs_the_history_and_the_status_have_no_absolute_path(self) -> None:
        plan = self.root / consent.PLAN
        plan.write_text(plan.read_text(encoding="utf-8") + "\n- 12:00 see /srv/ci/agent-7/log.txt\n", encoding="utf-8")
        with (self.root / f"{RUN}/checkpoints.md").open("a", encoding="utf-8") as handle:
            handle.write("- 12:10 zrobione: log w /srv/ci/agent-7/log.txt; w toku: —; czeka: —\n")
        time.sleep(0.3)
        _status, data = self.get(f"/api/status?plan={consent.PLAN}")
        self.assertIn("log.txt", json.dumps(data, ensure_ascii=False), "control: the text of the checkpoint is shown")
        for path in (f"/api/status?plan={consent.PLAN}", "/api/tabs", "/api/history"):
            with self.subTest(path=path):
                _status, answer = self.get(path)
                self.assertNotIn("/srv/ci", json.dumps(answer, ensure_ascii=False))
                self.assertNotIn(str(self.tmp), json.dumps(answer, ensure_ascii=False))

    def test_the_history_hides_a_path_in_a_title_and_a_signed_in_session_reads_it(self) -> None:
        plan = self.root / "docs" / "plans" / "2020-01-01-done.md"
        plan.write_text("# Plan: log w /srv/ci/agent-7/done.txt\n\nStatus: done\n", encoding="utf-8")
        title = lambda answer: next(item["title"] for item in answer["items"] if item["path"].endswith("2020-01-01-done.md"))
        self.assertEqual(title(self.get("/api/history")[1]), "Plan: log w \u2026/done.txt")
        self.assertIn("/srv/ci/agent-7/done.txt", title(self.get("/api/history", {"Cookie": self.session.pair})[1]), "control")

    def test_the_status_tells_an_anonymous_reader_that_the_results_are_raw(self) -> None:
        self.attempt()
        route = f"/api/status?plan={consent.PLAN}"
        self.assertIs(self.get(route)[1]["tests"].get("publicRaw"), True)
        self.assertNotIn("publicRaw", self.get(route, {"Cookie": self.session.pair})[1]["tests"])
        manifest = self.root / srv.MANIFEST
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("tests:\n  mask: on\n", encoding="utf-8")
        self.assertTrue(test_events.mask_setting(self.board.manifest()), "control: the mask is on")
        self.assertNotIn("publicRaw", self.get(route)[1]["tests"])


class DeliveryBaseTests(consent.PageCase):
    def offer(self) -> dict:
        offer = self.cp_offer()
        _status, data = self.get("/api/status")
        return {**offer, "base": data["delivery"].get("base")}

    def test_another_base_than_the_page_showed_is_409_and_writes_nothing(self) -> None:
        offer = self.offer()
        status, answer = self.post(self.good, {**offer, "base": "release/9"})
        self.assertEqual((status, answer.get("code")), (409, "delivery_changed"), answer)
        self.assert_no_write()
        self.assertEqual(self.post(self.good, offer)[0], 200, "control")

    def test_a_cp_without_the_base_is_a_bad_request(self) -> None:
        offer = self.offer()
        without = {key: value for key, value in offer.items() if key != "base"}
        for name, body in (("missing", without), ("a number", {**offer, "base": 5})):
            with self.subTest(name=name):
                status, answer = self.post(self.good, body)
                self.assertEqual((status, answer.get("code")), (400, "badRequest"), answer)
        self.assert_no_write()


class AuditTests(consent.PageCase):
    def events(self) -> list[str]:
        return [line["event"] for line in self.audit() if line["event"] != "login"]

    def test_the_intent_comes_before_the_write_and_the_consent_after_it(self) -> None:
        seen = []
        real = srv.status_core.set_doc_status

        def watched(*args, **kwargs):
            seen.append(self.events())
            return real(*args, **kwargs)

        with mock.patch.object(srv.status_core, "set_doc_status", watched):
            status, answer = self.post(self.good, self.offer_data)
        self.assertEqual(status, 200, answer)
        self.assertEqual(seen, [["consent_intent"]])
        self.assertEqual(self.events(), ["consent_intent", "consent"])
        intent, done = self.audit("consent_intent")[0], self.audit("consent")[0]
        for key in ("account", "source", "route", "tab", "session", "word", "path", "sha"):
            self.assertEqual(done[key], intent[key], key)

    def test_a_write_that_failed_leaves_no_consent_line(self) -> None:
        offer = self.cp_offer()
        offer["base"] = self.get("/api/status")[1]["delivery"].get("base")
        failed = {"status": "BLOCKED", "reason": "disk", "entry": None}
        with mock.patch.object(srv.status_core, "record_approval", return_value=failed):
            status, answer = self.post(self.good, offer)
        self.assertEqual((status, answer.get("code")), (500, "saveFailed"), answer)
        self.assertEqual(self.events(), ["consent_intent", "consent_refused"])

    def test_a_body_that_ends_before_its_length_is_refused(self) -> None:
        offer = self.offer_data
        raw = json.dumps(offer).encode("utf-8")
        sent = {**self.good, "Host": f"127.0.0.1:{self.port}", "Content-Length": str(len(raw) + 40), "Connection": "close"}
        head = f"POST {consent.ROUTE} HTTP/1.1\r\n" + "".join(f"{key}: {value}\r\n" for key, value in sent.items()) + "\r\n"
        answer = b""
        with socket.create_connection(("127.0.0.1", self.port), timeout=15) as sock:
            sock.sendall(head.encode("latin-1") + raw)   # a whole JSON object, but not the whole body that was announced
            sock.shutdown(socket.SHUT_WR)
            while chunk := sock.recv(65536):
                answer += chunk
        self.assertIn(b" 400 ", answer.split(b"\r\n", 1)[0] + b" ")
        self.assertIn(b'"badLength"', answer)
        self.assert_no_write()
        self.assertEqual(self.post(self.good, offer)[0], 200, "control")


class LockWindowTests(consent.PageCase):
    """The session ends while the consent waits for the file lock of what it writes: nothing is written."""

    def logout_while_waiting(self, route: str, body: dict, lock) -> tuple[int, dict]:
        outcome: list = []
        with lock:
            thread = threading.Thread(target=lambda: outcome.append(self.post(self.good, body, route)))
            thread.start()
            time.sleep(0.8)
            self.assertEqual(outcome, [], "control: the consent really waits for the lock")
            status, data, _ = consent.session_request(self.port, "POST", "/_api/v1/logout", {}, self.good)
            self.assertLess(status, 300, data)
        thread.join(timeout=20)
        return outcome[0]

    def file_lock(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        return status_core._lock_module().FileLock(str(path), 5.0, 0o600, unlink=True)

    def test_a_go_consent_that_waits_for_the_lock_of_the_plan_status(self) -> None:
        offer = self.offer_data
        document = descend(self.root, offer["path"])
        before = document.read_bytes()
        status, answer = self.logout_while_waiting(consent.ROUTE, offer, status_core.plan_status_lock(self.root.resolve()))
        self.assertEqual((status, answer.get("code")), (401, "session_expired"), answer)
        self.assertEqual(document.read_bytes(), before)
        self.assertEqual(self.entries(consent.RUN2), [])
        self.assertEqual(self.audit("consent"), [])

    def test_a_cp_consent_that_waits_for_the_lock_of_the_approvals(self) -> None:
        offer = self.cp_offer()
        offer["base"] = self.get("/api/status")[1]["delivery"].get("base")
        lock = self.file_lock(self.root / srv.RUNS_DIR / consent.RUN1 / (srv.RUN_APPROVALS + ".lock"))
        status, answer = self.logout_while_waiting(consent.ROUTE, offer, lock)
        self.assertEqual((status, answer.get("code")), (401, "session_expired"), answer)
        self.assertEqual(self.entries(consent.RUN1), [])
        self.assertEqual(self.audit("consent"), [])


class FeedbackWindowTests(unittest.TestCase):
    """`approve_from_preview` asks `before_write` under the lock of the store, right before the row is added."""

    def test_the_callback_runs_under_the_lock_and_a_refusal_adds_no_row(self) -> None:
        order: list[str] = []

        @contextlib.contextmanager
        def lock(_store):
            order.append("locked")
            yield
            order.append("released")

        def refused() -> bool:
            order.append("asked")
            return False

        sha = "a" * 64
        preview = {"sha": sha, "at": feedback._now().isoformat(), "view": {}}
        session = argparse.Namespace(root=Path("."), store=Path("."), trusted_section=lambda layer: {})
        with mock.patch.multiple(feedback, card_alias=lambda root: None, read_json_file=lambda path: preview, preview_path=lambda *a: Path("x"),
                                 load_draft=lambda *a: {"layer": "org"}, render=lambda *a, **k: {}, content_sha=lambda rendered: sha,
                                 _view_matches=lambda *a: True, store_lock=lock, consumed_approvals=lambda store: set(), _results=lambda store: [],
                                 read_approvals=lambda store: [], find_approval=lambda *a: None, _utc=lambda value: feedback._now(),
                                 _append_approval=lambda *a, **k: order.append("appended")):
            with self.assertRaises(feedback.WriteRefused):
                feedback.approve_from_preview(session, "F1", sha=sha, source="dashboard", before_write=refused)
        self.assertEqual(order, ["locked", "asked"])


class ShortWriteTests(consent.PageCase):
    """A write that takes only a part of the line is finished; one that cannot be finished is a failure that leaves no torn line."""

    def writer(self, marker: bytes, fail: bool):
        """`os.write` that takes 10 bytes of the line that holds `marker` at a time; `fail`: after the first piece the disk is full."""
        real = os.write
        started: set = set()

        def write(fd, data):
            data = bytes(data)
            if fd in started:
                if fail:
                    raise OSError(28, "No space left on device")
                return real(fd, data[:10])
            if marker in data:
                started.add(fd)
                return real(fd, data[:10])
            return real(fd, data)
        return write

    def test_the_trail_of_a_consent(self) -> None:
        (self.root / srv.RUNS_DIR / consent.RUN2).mkdir(parents=True, exist_ok=True)
        path = self.root / srv.RUNS_DIR / consent.RUN2 / srv.RUN_APPROVALS
        with mock.patch.object(status_core.os, "write", self.writer(b'"word"', True)):
            report = status_core.record_approval(self.root, "CP", consent.RUN2, sha="a" * 40)
        self.assertEqual(report["status"], "BLOCKED", report)
        self.assertEqual(path.read_bytes() if path.exists() else b"", b"", "no torn line")
        with mock.patch.object(status_core.os, "write", self.writer(b'"word"', False)):
            report = status_core.record_approval(self.root, "CP", consent.RUN2, sha="a" * 40)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["sha"], "a" * 40)

    def test_the_audit_of_a_change_of_a_plan(self) -> None:
        path = self.root / ".skillsync" / "audit" / dashboard_history.AUDIT_NAME
        with mock.patch.object(dashboard_history.os, "write", self.writer(b'"phase"', True)):
            with self.assertRaises(OSError):
                dashboard_history._audit_append(self.root, {"phase": "intent", "path": "docs/plans/a.md"})
        self.assertEqual(path.read_bytes() if path.exists() else b"", b"")
        with mock.patch.object(dashboard_history.os, "write", self.writer(b'"phase"', False)):
            dashboard_history._audit_append(self.root, {"phase": "intent", "path": "docs/plans/a.md"})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["phase"], "intent")


class HistoryGitCacheTests(unittest.TestCase):
    """D5: an anonymous `GET /api/history` does not start git again while the repository and the plans are as they were."""

    def setUp(self) -> None:
        import tempfile
        import shutil
        self.root = Path(tempfile.mkdtemp(prefix="history-cache-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "docs" / "plans").mkdir(parents=True)
        self.plan = self.root / "docs" / "plans" / "2020-01-01-old.md"
        self.plan.write_text("# Plan: old\n\nStatus: approved\n\n## Progress\n\n- [ ] S1 x\n", encoding="utf-8")
        old = time.time() - 400 * 86400
        os.utime(self.plan, (old, old))
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.git("add", "docs")
        self.git("commit", "-qm", "plan", date="2020-01-01T00:00:00Z")

    def git(self, *args, date: str | None = None) -> None:
        env = {**os.environ, **({"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date} if date else {})}
        subprocess.run(["git", "-C", str(self.root), "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, env=env, timeout=30)

    def collect(self) -> tuple[list, int]:
        calls: list = []
        real = status_core.PlanChangeTimes._git

        def counted(times, *args):
            calls.append(args[0])
            return real(times, *args)

        with mock.patch.object(status_core.PlanChangeTimes, "_git", counted):
            body = dashboard_history.collect([dashboard_history.TabRoot("a", "a", self.root)])
        return [(item["path"], item["abandoned"]) for item in body["items"]], len(calls)

    def test_git_is_asked_once_until_the_repository_or_a_plan_changes(self) -> None:
        first, asked = self.collect()
        self.assertEqual(first, [("docs/plans/2020-01-01-old.md", True)], "control: an old approved plan is abandoned")
        self.assertGreater(asked, 0)
        self.assertEqual(self.collect(), (first, 0))
        self.plan.write_text(self.plan.read_text(encoding="utf-8") + "\nmore\n", encoding="utf-8")   # a change of a plan: asked again
        changed, asked = self.collect()
        self.assertGreater(asked, 0)
        self.assertEqual(changed, [], "the plan changed now: not abandoned")
        self.git("commit", "-qam", "edit", date="2020-02-01T00:00:00Z")   # a commit alone (no file of a plan changes): asked again
        committed, asked = self.collect()
        self.assertGreater(asked, 0)
        self.assertEqual(committed, [("docs/plans/2020-01-01-old.md", True)])


class UntrackedDocumentsTests(unittest.TestCase):
    """`workflow.specs.tracked: false`: the dates of the plans never come from the history of git; an old file is still old."""

    def test_git_history_of_the_plans_is_not_asked(self) -> None:
        import shutil
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="untracked-docs-"))
        self.addCleanup(shutil.rmtree, root, True)
        (root / ".github").mkdir()
        (root / ".github" / ".skillsync.yaml").write_text("skills: [dashboard]\nworkflow:\n  specs:\n    tracked: false\n", encoding="utf-8")
        calls: list = []
        with mock.patch.object(status_core.PlanChangeTimes, "_git", lambda times, *args: calls.append(args[0]) or ""):
            self.assertEqual(status_core.PlanChangeTimes(root).changed("docs/plans/a.md", 123.0), 123.0)
            self.assertEqual(calls, [])
            (root / ".github" / ".skillsync.yaml").write_text("skills: [dashboard]\n", encoding="utf-8")
            status_core.PlanChangeTimes(root).changed("docs/plans/a.md", 123.0)
        self.assertEqual(calls, ["log"], "control: tracked documents are dated by git")


class OldServerTests(attach.Base):
    """D4: a server of 1.20.1 (schema 2 with a token, `git show 7d2f02e5:skillsync_workflow/dashboard_token.py`, `write_state`) is an old one."""

    def old_server(self) -> int:
        health = ThreadingHTTPServer(("127.0.0.1", 0), attach.HealthOnly)
        health.body = {"ok": True, "serverId": "0123456789abcdef", "version": 1}
        threading.Thread(target=health.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        self.addCleanup(lambda: (health.shutdown(), health.server_close()))
        return health.server_address[1]

    def write_records(self, port: int, two_sockets: bool = False) -> None:
        project = attach.dw.project_identity(self.main)
        token = "-".join(("old", "consent", "token", "012" + "345" + "678" + "9ab" + "cde" + "f"))
        records = [(port, {"approvals": True, "token": token, "tokenId": hashlib.sha256(token.encode()).hexdigest()[:8], "tabs": [
            {"id": self.tab_of(self.main), "path": str(self.main), "token": token, "current": True}]})]
        if two_sockets:   # the read socket of 1.20.1 holds no token: it is the same old server
            records[0][1]["role"] = "approve"
            records.append((port + 0, {"approvals": False, "role": "read", "tabs": [{"id": self.tab_of(self.main), "path": str(self.main)}]}))
        directory = dashboard_token.state_dir()
        directory.mkdir(parents=True, exist_ok=True)
        for index, (number, extra) in enumerate(records):
            path = dashboard_token.state_path(self.main, number)
            if index:
                path = path.with_name(path.stem.rsplit("-", 1)[0] + f"-read-{number}.json")
            record = {"schema": 2, "pid": os.getpid(), "port": number, "host": "127.0.0.1", "root": dashboard_token.root_text(self.main),
                      "startedAt": "2026-11-27T10:00:00+00:00", "hosts": [], "kind": "project", "serverId": "0123456789abcdef",
                      "commonDir": str(project.common_dir), "terminal": {}, **extra}
            path.write_text(json.dumps(record), encoding="utf-8")

    def assert_old(self, plan) -> None:
        self.assertEqual((plan.mode, [item["port"] for item in plan.legacy][:1], plan.live, plan.race), ("start", [self.port], [], False))
        self.assertEqual(list(dashboard_token.state_dir().glob("*-pins")), [], "nothing is added to the old server")

    def test_serve_does_not_add_a_tab_to_it_and_says_why(self) -> None:
        self.port = self.old_server()
        self.write_records(self.port)
        worktree = self.worktree("wt-old", runs=True)
        for path in (self.main, worktree):
            with self.subTest(path=path.name):
                self.assert_old(dashboard_token.plan_serve(attach.serve_args(path, layers=None)))
        done = self.cli("serve", str(worktree), "--json", timeout=60)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("legacy_server", done.stdout)
        self.assertIn(str(self.port), done.stdout)
        detached = self.cli("serve", str(worktree), "--detach", "--json", timeout=60)
        self.assertEqual(detached.returncode, 2, detached.stdout + detached.stderr)
        self.assertEqual(json.loads(detached.stdout)["code"], "legacy_server", detached.stdout)
        self.assertEqual(list(dashboard_token.state_dir().glob("*-pins")), [])

    def test_the_read_socket_of_an_old_server_is_old_too(self) -> None:
        self.port = self.old_server()
        self.write_records(self.port, two_sockets=True)
        self.assert_old(dashboard_token.plan_serve(attach.serve_args(self.main)))
        self.assertIsNone(dashboard_token.append_command(attach.serve_args(self.main), self.project_plan()))

    def project_plan(self):
        plan = dashboard_token.ServePlan()
        plan.project, plan.root = attach.dw.project_identity(self.main), self.main
        return plan

    def test_control_a_server_of_this_version_gets_the_tab(self) -> None:
        self.port = self.old_server()
        project = attach.dw.project_identity(self.main)
        dashboard_token.write_state(self.main, self.port, True, server_id="0123456789abcdef", kind="project", common_dir=project.common_dir,
                                    tabs=[{"id": self.tab_of(self.main), "path": str(self.main)}])
        plan = dashboard_token.plan_serve(attach.serve_args(self.main))
        self.assertEqual((plan.mode, plan.legacy), ("append", []))


class AccountHintTests(unittest.TestCase):
    """D2: the command the console of `dashboard serve` names for the first approver parses with the real parser."""

    def parse(self, argv: list[str]) -> argparse.Namespace:
        from skillsync_workflow import cli
        caught: list = []
        real = argparse.ArgumentParser.parse_args

        def capture(parser, args=None, namespace=None):
            caught.append(real(parser, args, namespace))
            raise SystemExit(0)

        with mock.patch.object(sys, "argv", ["skillsync", *argv]), mock.patch.object(argparse.ArgumentParser, "parse_args", capture):
            with contextlib.suppress(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                cli.main()
        self.assertTrue(caught, f"the parser refused {argv}")
        return caught[0]

    def test_the_hint_is_a_command_that_parses(self) -> None:
        hint = srv.approver_account_command(Path("/srv/my project"))
        words = shlex.split(hint)
        self.assertEqual(words[:3], ["skillsync", "prototype", "user"], hint)
        parsed = self.parse([word.replace("<e-mail>", "ola@example.com") for word in words[1:]])
        self.assertEqual((parsed.path, parsed.login, parsed.password_prompt, parsed.confirm_mixed_fleet), ("/srv/my project", "ola@example.com", True, True))
        self.assertIn("approver", str(parsed.role))

    def test_the_console_names_this_command(self) -> None:
        source = (ROOT / "skillsync_workflow" / "dashboard_http.py").read_text(encoding="utf-8")
        self.assertNotIn("prototype user add <login> --roles approver", source)
        self.assertIn("approver_account_command(board.root)", source)


class ContentPolicyTests(consent.PageCase):
    def fetch(self, path: str) -> tuple[dict, str]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return dict(response.getheaders()), response.read().decode("utf-8", "replace")
        finally:
            conn.close()

    def assert_styles(self, path: str, single: bool = False) -> None:
        headers, body = self.fetch(path)
        policy = headers["Content-Security-Policy"]
        self.assertNotIn("unsafe-inline", policy)
        style = next(part.strip() for part in policy.split(";") if part.strip().startswith("style-src"))
        for block in re.findall(r"<style>(.*?)</style>", body, re.S):
            digest = base64.b64encode(hashlib.sha256(block.encode("utf-8")).digest()).decode("ascii")
            self.assertIn(f"'sha256-{digest}'", style, f"the style block of {path} is allowed by its hash")
        self.assertNotRegex(body, r"<[a-z][^>]*\sstyle=", "a style attribute would be blocked")
        self.assertEqual("data-single-origin" in body, single)

    def test_the_page_and_the_sign_in_have_no_unsafe_inline(self) -> None:
        for path in ("/", "/_login"):
            with self.subTest(path=path):
                self.assert_styles(path)
        self.assertIn("<style>", self.fetch("/_login")[1], "control: the sign-in page has a style block")

    def test_the_banner_of_a_single_origin_is_styled_by_a_hashed_block(self) -> None:
        self.kit.app.single_origin = True
        for path in ("/", "/_login"):
            with self.subTest(path=path):
                self.assert_styles(path, single=True)
                self.assertIn("<style>", self.fetch(path)[1])


WINDOWS_JS = r"""
(async () => {
  const out = { violations };
  const settle = async () => { await tick(); await tick(); await tick(); };
  let native = false, shown = [];
  Object.defineProperty(E.prototype, 'showModal', { configurable: true, get() { if (!native || this.localName !== 'dialog') return undefined;
    return () => { if (!this.isConnected) throw new Error('not connected'); this.open = true; shown.push(this.getAttribute('class')); }; } });
  const status = (change) => { const j = JSON.parse(JSON.stringify(input.fixture)); j.approvals.here = true; j.approvals.viewer = false;
    j.delivery = Object.assign({}, j.delivery, { mode: 'direct', base: 'main', branch: 'feat/x', target: 'feat/x' }); if (change) change(j); return j; };
  const account = { state: 'user', login: 'a@x', name: 'Ola', roles: ['approver'], approver: true, mustChange: false, loginAllowed: true };
  const open = (kind) => {
    const sent = []; let release = null;
    const actions = { approve: (r) => { sent.push(r); return new Promise((done) => { release = () => done({ ok: true, word: 'CP', text: 'ok' }); }); }, changePassword: () => Promise.resolve({ ok: true }), login: () => {} };
    const m = mount(status(), { now: Date.parse(input.fixture.generatedAt), actions, deps: { session: () => account } });
    const q = (s) => m.root.querySelector(s);
    if (kind === 'password') q('#account button[data-session="password"]').click(); else q('button[data-approve="cp"]').click();
    return { m, q, sent, release: () => release && release() };
  };
  for (const mode of ['native', 'plain']) {
    native = mode === 'native';
    const r = out[mode] = {};
    for (const kind of ['consent', 'password']) {
      shown = [];
      const p = open(kind);
      const win = () => p.q(`[data-dialog="${kind}"]`);
      r[kind] = { tag: win() && win().localName, shown: shown.length, roles: [win().getAttribute('role'), win().querySelector('.fb-dialog').getAttribute('role')],
        labelled: Boolean(win().getAttribute('aria-labelledby') || win().querySelector('.fb-dialog').getAttribute('aria-labelledby')) };
      const cancel = new Ev('cancel'); win().dispatchEvent(cancel);
      r[kind].cancelled = { gone: native ? !win() : null, prevented: cancel.defaultPrevented };
      p.m.board.destroy();
    }
    if (native) {
      const p = open('consent');
      p.q('button[data-consent-confirm]').click(); shown = [];
      p.q('[data-dialog="consent"]').dispatchEvent(new Ev('cancel'));
      const afterCancel = Boolean(p.q('[data-dialog="consent"]'));
      p.q('[data-dialog="consent"]').open = false; p.q('[data-dialog="consent"]').dispatchEvent(new Ev('close'));
      r.sending = { afterCancel, reshown: shown.length, sent: p.sent.length };
      p.release(); await settle(); p.m.board.destroy();
      const f = open('consent'); const node = f.q('[data-dialog="consent"]'); node.open = false; node.dispatchEvent(new Ev('close'));
      r.forced = { gone: !f.q('[data-dialog="consent"]'), sent: f.sent.length }; f.m.board.destroy();
    }
    // the confirmation of the History
    shown = [];
    const doc = makeDoc(), root = doc.createElement('div'); doc.root = root; let cancelled = 0;
    const view = R.createArchiveView(root, { t: (k) => k, onCancel: () => { cancelled++; } });
    const item = { key: 'a', title: 'A', path: 'docs/plans/a.md', status: 'done', action: 'archive', canAct: true, copies: 1, ownerName: 'main' };
    const model = { lang: 'pl', state: 'ready', groups: [{ id: 'a', name: 'main', items: [item] }], count: 1, more: 0, preview: { state: 'ready', item, html: '' },
      confirm: { action: 'archive', title: 'A', tab: 'main', sha: 'abc', busy: false }, notice: null };
    view.update(model);
    const box = root.querySelector('.wh-confirm');
    const wrap = root.querySelector('dialog.wh-dialog');
    r.history = { box: Boolean(box), tag: wrap ? 'dialog' : null, shown: shown.slice() };
    if (wrap) { const ev = new Ev('cancel'); wrap.dispatchEvent(ev); r.history.cancelled = [cancelled, ev.defaultPrevented]; }
    view.destroy();
  }
  native = false;
  // the base of the delivery travels with CP, and a change of it closes the window
  {
    const p = open('consent');
    p.m.board.update(status((j) => { j.delivery.base = 'release/9'; }));
    out.baseChange = { open: Boolean(p.q('[data-dialog="consent"]')), sent: p.sent.length };
    p.m.board.destroy();
    const c = open('consent'); c.q('button[data-consent-confirm]').click(); out.baseSent = c.sent.map((x) => x.base); c.release(); await settle(); c.m.board.destroy();
  }
  // `closeDialogs` (the host hides the page) closes every window of a decision, sending nothing
  for (const mode of ['native', 'plain']) {
    native = mode === 'native';
    const got = out['close_' + mode] = {};
    const sent = [];
    const detail = { schema: 1, slug: 'run-alpha', title: 'A', format: 2, version: 'a'.repeat(64), iteration: 1, writing: null, sourceWrites: true, iterations: [], problems: [], threads: [],
      can: { accept: true }, screens: [{ id: 's1', title: 'One', hash: 'b'.repeat(64), state: 'pending' }] };
    const actions = { approve: (r) => { sent.push(r); return Promise.resolve({ ok: true }); }, feedbackApprove: (r) => { sent.push(r); return Promise.resolve({ ok: true }); },
      changePassword: () => Promise.resolve({ ok: true }), login: () => {}, loadPrototype: () => Promise.resolve({ ok: true, data: detail }), prototypeAction: (r) => { sent.push(r); return Promise.resolve({ ok: true }); } };
    const prepared = status((j) => { j.prototype = { slug: 'run-alpha', port: 5142, up: true, writing: null, url: 'http://127.0.0.1:5142', served: true, frame: true };
      j.prototypes = [{ slug: 'run-alpha', iteration: 1, screens: 1, accepted: 0, stale: 0, threads: {}, commentsAfterAcceptance: 0, gate: 'fail', writing: false }];
      j.feedback = { enabled: true, total: 1, drafts: [{ draft: 'F1', state: 'waiting', sha: 'd'.repeat(64), sha12: 'd'.repeat(12), view: { title: 'T', body: 'B', mode: 'create', tags: [], target: {}, scan: { status: 'PASS' } } }] }; });
    const kinds = { consent: ['button[data-approve="cp"]'], password: ['#account button[data-session="password"]'], feedback: ['button.fb-approve'],
      proto: ['#tab-prototypes', 'button[data-proto-manage="run-alpha"]', '[data-screen="s1"] button[data-proto-act="accept"]'] };
    for (const [kind, presses] of Object.entries(kinds)) {
      const m = mount(prepared, { now: Date.parse(input.fixture.generatedAt), actions, deps: { session: () => Object.assign({}, account, { roles: ['approver', 'acceptor'] }), prototypeFrame: true } });
      const missing = [];
      for (const sel of presses) { const b = m.root.querySelector(sel); if (!b) { missing.push(sel); break; } b.click(); await settle(); }
      const open = m.root.querySelectorAll('.fb-modal').length;
      m.board.closeDialogs();
      got[kind] = { missing, open, after: m.root.querySelectorAll('.fb-modal').length, sent: sent.length };
      m.board.destroy();
    }
  }
  // the warning of the tab of the tests
  for (const [name, flag] of [['raw', true], ['plain', undefined]]) {
    out['warning_' + name] = {};
    for (const [lang, empty] of [['full', false], ['empty', true]]) {
      const j = status((s) => { s.tests = { sections: [], failedTotal: 0, runningTotal: 0, skipped: [{ path: 'tests/x', reason: 'unreadable' }] };
        if (!empty) { s.tests.sections = [{ step: null, state: 'passed', attempts: [] }]; delete s.tests.skipped; } if (flag) s.tests.publicRaw = true; });
      const m = mount(j, { now: Date.parse(input.fixture.generatedAt) });
      out['warning_' + name][lang] = text(m.root.querySelector('#view-tests [data-tests-raw]'));
      m.board.destroy();
    }
  }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e) })); });
"""


@unittest.skipUnless(NODE_MAJOR >= 18, "set SKILLSYNC_TEST_NODE (or have node on PATH), node >= 18")
class RendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tail = HARNESS[:HARNESS.index("(async () => {\n  const out = { fixtures: {}")]
        cls.out = run_node(tail + WINDOWS_JS, {"fixtures": {}, "now": {}, "fixture": fixture_files()["cp-ready"]})
        assert "fatal" not in cls.out, cls.out["fatal"]

    def test_no_violation_of_the_dom_rules(self):
        self.assertEqual(self.out["violations"], [])

    def test_the_windows_are_dialogs_in_the_top_layer(self):
        for kind in ("consent", "password"):
            with self.subTest(kind=kind):
                got = self.out["native"][kind]
                self.assertEqual((got["tag"], got["shown"], got["roles"], got["labelled"]), ("dialog", 1, [None, None], True))
                self.assertEqual(got["cancelled"], {"gone": True, "prevented": True})
        history = self.out["native"]["history"]
        self.assertEqual((history["box"], history["tag"], history["shown"], history["cancelled"]), (True, "dialog", ["wh-dialog"], [1, True]))

    def test_a_window_that_sends_stays_and_one_the_browser_closes_is_cancelled(self):
        self.assertEqual(self.out["native"]["sending"], {"afterCancel": True, "reshown": 1, "sent": 1})
        self.assertEqual(self.out["native"]["forced"], {"gone": True, "sent": 0})

    def test_a_host_without_show_modal_keeps_the_window_as_it_was(self):
        for kind in ("consent", "password"):
            with self.subTest(kind=kind):
                got = self.out["plain"][kind]
                self.assertEqual((got["tag"], got["shown"], got["roles"], got["labelled"]), ("div", 0, [None, "dialog"], True))
        history = self.out["plain"]["history"]
        self.assertEqual((history["box"], history["tag"], history["shown"]), (True, None, []))

    def test_a_hidden_page_keeps_no_window_of_a_decision(self):
        for mode in ("native", "plain"):
            for kind in ("consent", "password", "feedback", "proto"):
                with self.subTest(mode=mode, kind=kind):
                    self.assertEqual(self.out["close_" + mode][kind], {"missing": [], "open": 1, "after": 0, "sent": 0})

    def test_the_base_of_the_delivery_is_sent_and_its_change_closes_the_window(self):
        self.assertEqual(self.out["baseChange"], {"open": False, "sent": 0})
        self.assertEqual(self.out["baseSent"], ["main"])

    def test_the_tab_of_the_tests_warns_that_the_results_are_raw(self):
        for view in ("full", "empty"):
            with self.subTest(view=view):
                self.assertIn("tests.mask", self.out["warning_raw"][view] or "")
                self.assertIsNone(self.out["warning_plain"][view])

    def test_the_catalogues_have_the_warning(self):
        for lang in ("pl", "en"):
            catalogue = json.loads((ROOT / "skillsync_workflow" / "data" / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
            self.assertIn("tests.mask", catalogue["ui.tests.publicRaw"])


if __name__ == "__main__":
    unittest.main()
