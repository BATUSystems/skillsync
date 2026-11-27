"""CORE-2000-U6-LIMITS: limits of the sign-in server (`prototype_server.py`, also behind the status page):
- N5: the rate-limit source behind a trusted proxy is read from EVERY `X-Forwarded-For` line;
- one time limit for the whole body of a request of the sign-in routes (a client that sends a byte at a time does not extend it);
- D2: `run/audit.jsonl` has a size limit and keeps a few older files.

A real `PrototypeHTTPServer` over a temporary directory; requests are bytes written to a socket, so that header lines and the pace of a body are
exactly what the test says."""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from skillsync_workflow import prototype_server as ps  # noqa: E402
from tmp_paths import descend  # noqa: E402

LOGIN = "dev.person@example.com"
PASSWORD = "-".join(("Haslo", "Testowe", "Limitow", "42"))


class ServerCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="limits-")
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name).resolve()
        self.root = descend(self.tmp, "prototypes")
        self.root.mkdir()
        self.run_dir = descend(self.tmp, "run")
        users_path = descend(self.tmp, "users.json")
        users = ps.UsersFile(str(users_path), iterations=1000)
        ps.add_user(users, LOGIN, roles=["developer"], password=PASSWORD)
        config = ps.ServerConfig(str(self.root), str(users_path), git_checks=False, recover=False, runtime_dir=str(self.run_dir),
                                 trust_proxy=True, trusted_proxies=["127.0.0.1"], log=None, pbkdf2_iterations=1000)
        self.app = ps.PrototypeApp(config)
        self.server = ps.PrototypeHTTPServer(("127.0.0.1", 0), ps.PrototypeRequestHandler, self.app)
        self.port = self.server.server_address[1]
        self.app.enable_login_link(self.server)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close()))

    def request(self, path: str, body: bytes, headers: list, declared: int | None = None, content: str = "application/json") -> socket.socket:
        lines = [f"POST {path} HTTP/1.1", f"Host: 127.0.0.1:{self.port}", f"Content-Type: {content}",
                 f"Origin: http://127.0.0.1:{self.port}", f"Content-Length: {len(body) if declared is None else declared}", "Connection: close",
                 *headers, "", ""]
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=20)
        self.addCleanup(sock.close)
        sock.sendall("\r\n".join(lines).encode("ascii") + body)
        return sock

    @staticmethod
    def reply(sock: socket.socket) -> tuple:
        data = b""
        while True:
            try:
                chunk = sock.recv(65536)
            except ConnectionResetError:
                break
            if not chunk:
                break
            data += chunk
        head, _, body = data.partition(b"\r\n\r\n")
        return int(head.split()[1]), head.decode("latin-1"), body

    def login(self, headers: list = (), password: str = PASSWORD) -> tuple:
        body = json.dumps({"login": LOGIN, "password": password}).encode("utf-8")
        return self.reply(self.request("/_api/v1/login", body, list(headers)))

    def audit_rows(self) -> list:
        path = self.run_dir / "audit.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="ascii").splitlines()] if path.exists() else []


class ForwardedFor(ServerCase):
    """N5. The proxy appends its hop after what the client sent; a client may send a header line of its own first."""

    def source_of_a_failed_login(self, *lines: str) -> str:
        before = len(self.audit_rows())
        with mock.patch.object(ps.PrototypeApp, "audit_account", lambda app, event, user, **fields: app.audit_event(event, **fields)):
            self.app.limiter = type(self.app.limiter)()
            self.login([f"X-Forwarded-For: {line}" for line in lines], password=PASSWORD + "x")
        added = [row for row in self.audit_rows()[before:] if "source" in row]
        self.assertTrue(added, self.audit_rows())
        return added[-1]["source"]

    def test_the_last_hop_counts_also_in_a_second_line(self) -> None:
        self.assertEqual(self.source_of_a_failed_login("203.0.113.5", "198.51.100.7"), "198.51.100.7")

    def test_a_trusted_proxy_in_the_last_line_is_skipped(self) -> None:
        self.assertEqual(self.source_of_a_failed_login("203.0.113.5", "198.51.100.7, 127.0.0.1"), "198.51.100.7")

    def test_a_single_line_is_read_as_before(self) -> None:
        self.assertEqual(self.source_of_a_failed_login("203.0.113.5, 198.51.100.7"), "198.51.100.7")


class SignInBodyDeadline(ServerCase):
    LIMIT = 1.0

    def setUp(self) -> None:
        super().setUp()
        patch = mock.patch.object(ps, "LOGIN_BODY_SECONDS", self.LIMIT, create=True)
        patch.start()
        self.addCleanup(patch.stop)

    def drip(self, path: str, headers: list = (), seconds: float = 6.0, content: str = "application/json") -> tuple:
        """A body of 100 declared bytes, one byte every 0.2 s: every single read is quick, the whole body never ends in time."""
        sock = self.request(path, b"{", list(headers), declared=100, content=content)
        started = time.monotonic()
        sock.settimeout(0.2)
        answer = b""
        while time.monotonic() - started < seconds:
            try:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                answer += chunk
                break
            except socket.timeout:
                try:
                    sock.sendall(b" ")
                except OSError:
                    break
        took = time.monotonic() - started
        sock.settimeout(5)
        try:
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                answer += chunk
        except OSError:
            pass
        return answer, took

    def assert_timed_out(self, answer: bytes, took: float) -> None:
        self.assertTrue(answer.startswith(b"HTTP/1.1 408") or answer.startswith(b"HTTP/1.0 408"), (took, answer[:200]))
        self.assertIn(b"body_timeout", answer)
        self.assertLess(took, self.LIMIT + 3.0)

    def test_a_dripping_login_body_is_cut(self) -> None:
        self.assert_timed_out(*self.drip("/_api/v1/login"))

    def test_a_dripping_body_of_a_signed_in_route_is_cut(self) -> None:
        status, head, body = self.login()
        self.assertEqual(status, 200, body)
        cookie = next(line.split(":", 1)[1].split(";")[0].strip() for line in head.splitlines() if line.lower().startswith("set-cookie:"))
        signed = [f"Cookie: {cookie}", f"X-Skillsync-CSRF: {json.loads(body)['csrf']}"]
        for path in ("/_api/v1/password", "/_api/v1/logout"):
            with self.subTest(path=path):
                self.assert_timed_out(*self.drip(path, signed))

    def test_a_dripping_body_of_both_handoff_posts_is_cut(self) -> None:
        page = ps.PrototypeHTTPServer(("127.0.0.1", 0), ps.PrototypeRequestHandler, self.app, origin_kind="page")
        threading.Thread(target=page.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        self.addCleanup(lambda: (page.shutdown(), page.server_close()))
        prototypes, self.port = self.port, page.server_address[1]
        status, head, body = self.login()   # a session of the origin of the page
        self.assertEqual(status, 200, body)
        cookie = next(line.split(":", 1)[1].split(";")[0].strip() for line in head.splitlines() if line.lower().startswith("set-cookie:"))
        with self.subTest(path="/_api/v1/handoff"):
            self.assert_timed_out(*self.drip("/_api/v1/handoff", [f"Cookie: {cookie}", f"X-Skillsync-CSRF: {json.loads(body)['csrf']}"]))
        self.port = prototypes
        with self.subTest(path="/_handoff"), mock.patch.object(self.app.config, "page_url", "http://127.0.0.1:1"):
            self.assert_timed_out(*self.drip("/_handoff", ["Sec-Fetch-Site: same-origin"], content="application/x-www-form-urlencoded"))

    def test_a_body_in_two_quick_pieces_still_signs_in(self) -> None:
        body = json.dumps({"login": LOGIN, "password": PASSWORD}).encode("utf-8")
        sock = self.request("/_api/v1/login", body[:5], [], declared=len(body))
        time.sleep(0.3)
        sock.sendall(body[5:])
        status, _head, answer = self.reply(sock)
        self.assertEqual(status, 200, answer)


class AnonymousRefusals(ServerCase):
    """A flood of refused requests must not push the consents out of the bounded audit."""

    def rows_of(self, event: str) -> list:
        return [row for row in self.audit_rows() if row.get("event") == event]

    def test_refusals_of_one_source_are_limited_per_minute_and_counted(self) -> None:
        now = [1000.0]
        with mock.patch.object(ps, "AUDIT_REFUSALS_PER_MINUTE", 5, create=True), mock.patch.object(self.app, "clock", lambda: now[0]):
            for number in range(12):
                self.app.audit_event("consent_refused", source="198.51.100.7", route=f"/r{number}")
            self.app.audit_event("consent_refused", source="203.0.113.5", route="/other")
            self.app.audit_event("consent", source="198.51.100.7", word="CP")   # a consent is never suppressed
            self.assertEqual(len(self.rows_of("consent_refused")), 6)
            self.assertEqual(len(self.rows_of("consent")), 1)
            now[0] += 61
            self.app.audit_event("consent_refused", source="198.51.100.7", route="/later")
        self.assertEqual(self.rows_of("consent_refused")[-1]["suppressed"], 7)

    def test_a_long_field_is_cut(self) -> None:
        self.app.audit_event("consent_refused", source="198.51.100.7", route="/api/prototypes/" + "a" * 60000 + "/accept",
                             via={"host": "h" * 5000})
        row = self.rows_of("consent_refused")[-1]
        self.assertLessEqual(len(row["route"]), 301)
        self.assertLessEqual(len(row["via"]["host"]), 301)
        self.assertTrue(row["route"].startswith("/api/prototypes/aaa"))
        self.assertLess((self.run_dir / "audit.jsonl").stat().st_size, 2000)

    def test_an_audit_that_stays_full_refuses_the_audit_check_of_an_approver(self) -> None:
        self.assertIsNone(self.app.ensure_audit())
        (self.run_dir / "audit.jsonl").write_text("x" * 900, encoding="ascii")

        def refuse(source, target):
            raise PermissionError("held open by another program")

        with mock.patch.object(ps, "LINK_AUDIT_MAX_BYTES", 400), mock.patch.object(ps.os, "replace", refuse):
            self.assertIsNotNone(self.app.ensure_audit())
        with mock.patch.object(ps, "LINK_AUDIT_MAX_BYTES", 400):
            self.assertIsNone(self.app.ensure_audit())   # the control: once it can be set aside, the audit works again


class AuditRotation(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="audit-")
        self.addCleanup(temporary.cleanup)
        self.run_dir = descend(Path(temporary.name).resolve(), "run")
        self.assertIsNone(ps.link_prepare_run_dir(str(self.run_dir)))
        for name, value in (("LINK_AUDIT_MAX_BYTES", 400), ("LINK_AUDIT_KEPT", 2)):
            patch = mock.patch.object(ps, name, value, create=True)
            patch.start()
            self.addCleanup(patch.stop)

    def append(self, number: int) -> None:
        ps.link_audit_append(str(self.run_dir), {"n": number, "pad": "x" * 80})

    def files(self) -> dict:
        found = {}
        for path in sorted(self.run_dir.glob("audit.jsonl*")):
            if not path.name.endswith(".lock"):
                found[path.name] = [json.loads(line)["n"] for line in path.read_text(encoding="ascii").splitlines()]
        return found

    def test_the_files_are_bounded_and_the_newest_lines_stay(self) -> None:
        for number in range(60):
            self.append(number)
        files = self.files()
        self.assertEqual(sorted(files), ["audit.jsonl", "audit.jsonl.1", "audit.jsonl.2"])
        kept = files["audit.jsonl.2"] + files["audit.jsonl.1"] + files["audit.jsonl"]
        self.assertEqual(kept, list(range(60 - len(kept), 60)), "the kept lines are the newest ones, in order, none lost between files")
        for name in files:
            self.assertLessEqual((self.run_dir / name).stat().st_size, 400 + 120, name)
            if os.name != "nt":
                self.assertEqual((self.run_dir / name).stat().st_mode & 0o777, 0o600, name)

    def test_under_the_limit_nothing_is_rotated(self) -> None:
        for number in range(3):
            self.append(number)
        self.assertEqual(self.files(), {"audit.jsonl": [0, 1, 2]})

    def test_a_failed_rotation_keeps_writing_up_to_twice_the_limit_then_refuses(self) -> None:
        real = os.replace

        def refuse(source, target):
            if "audit.jsonl" in str(source):
                raise PermissionError("held open by another program")
            return real(source, target)

        with mock.patch.object(ps.os, "replace", refuse):
            written = 0
            with self.assertRaises(OSError):
                for number in range(60):
                    self.append(number)
                    written += 1
        size = (self.run_dir / "audit.jsonl").stat().st_size
        self.assertGreater(written, 4)
        self.assertGreaterEqual(size, 800)
        self.assertLess(size, 800 + 120)

    def test_a_held_live_file_costs_none_of_the_older_ones(self) -> None:
        for number in range(30):
            self.append(number)
        before = self.files()
        self.assertEqual(len(before), 3)
        real = os.replace

        def refuse_the_live_file(source, target):
            if str(source).endswith("audit.jsonl"):
                raise PermissionError("held open by another program")
            return real(source, target)

        with mock.patch.object(ps.os, "replace", refuse_the_live_file):
            for number in range(30, 36):
                try:
                    self.append(number)
                except OSError:
                    break
        after = self.files()
        for name in ("audit.jsonl.1", "audit.jsonl.2"):
            self.assertEqual(after[name], before[name], name)
        for number in range(36, 50):   # released: the rotation goes on and keeps the newest lines in order
            self.append(number)
        kept = [n for name in ("audit.jsonl.2", "audit.jsonl.1", "audit.jsonl") for n in self.files()[name]]
        self.assertEqual(kept, sorted(kept))
        self.assertEqual(kept[-1], 49)

    @unittest.skipIf(os.name == "nt", "symbolic links need privileges")
    def test_a_link_at_an_old_name_is_replaced_not_followed(self) -> None:
        outside = self.run_dir.parent / "outside.txt"
        outside.write_text("keep", encoding="ascii")
        self.append(0)
        (self.run_dir / "audit.jsonl.1").symlink_to(outside)
        for number in range(1, 20):
            self.append(number)
        self.assertEqual(outside.read_text(encoding="ascii"), "keep")
        self.assertFalse((self.run_dir / "audit.jsonl.1").is_symlink())


if __name__ == "__main__":
    unittest.main()
