"""PORTS-160-C: the two sockets of the status page (`dashboard serve --read-port N --approve-port M`): the role comes from the PORT.

Handler tests run two servers over one `Board` in-process (ports of the test slice only, never 5140-5142); command tests start
`dashboard serve` as a process (flags, listen address, lifecycle, state files, `dashboard url`). The single-port mode is guarded by
`test_skillsync_dashboard_single_port_golden`."""
from __future__ import annotations

import collections
import http.client
import inspect
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from skillsync_workflow import dashboard_http as srv  # noqa: E402
from skillsync_workflow import dashboard_ports as ports  # noqa: E402
from skillsync_workflow import dashboard_token  # noqa: E402
from skillsync_workflow import status as status_core  # noqa: E402
from layers_helpers import guard_real_state  # noqa: E402
from tests.test_skillsync_dashboard_approve_token import home_env  # noqa: E402
from tests.test_skillsync_dashboard_server import (ASSETS, CLI, DESCRIPTIONS, PLAN, TEST_TOKEN, ProjectCase, free_port,  # noqa: E402
                                                   install_fake_cli, make_project, spy_core)
import sys as _sys_tmp_paths  # noqa: E402
from pathlib import Path as _Path_tmp_paths  # noqa: E402
_sys_tmp_paths.path.insert(0, str(_Path_tmp_paths(__file__).resolve().parent))  # noqa: E402
from tmp_paths import descend  # noqa: E402

OK = {"Content-Type": "application/json", "X-Status-Board": "1", "X-Approve-Token": TEST_TOKEN}
LAN = None   # an address of this machine other than loopback, or None


def lan_candidates():
    """Non-loopback IPv4 addresses of this machine, the route source of a UDP probe first, then those of the host name, without repeats."""
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        found.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    result = []
    for ip in found:
        if ip and not ip.startswith("127.") and not ip.startswith("169.254.") and ip not in result:
            result.append(ip)
    return result


def reachable(ip):
    """True when a connection to `ip` reaches a listener of this machine (a point-to-point tunnel that takes over the default route does not
    deliver a connection to its own address, the address of the interface of the LAN does)."""
    try:
        with socket.socket() as listener:
            listener.bind(("0.0.0.0", 0))
            listener.listen(1)
            with socket.create_connection((ip, listener.getsockname()[1]), timeout=1):
                return True
    except OSError:
        return False


def lan_address():
    """An address of this machine other than loopback that a connection reaches, or None (the callers skip the test then)."""
    for ip in lan_candidates():
        if reachable(ip):
            return ip
    return None


def two_ports() -> tuple[int, int]:
    """Two different free ports of the test slice (`free_port` alone gives the same one twice: nothing listens on it yet)."""
    first = free_port()
    with socket.socket() as hold:
        if os.name != "nt":
            hold.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # the port may be in TIME_WAIT (free_port allows it)
        hold.bind(("127.0.0.1", first))
        hold.listen(1)
        second = free_port()
    return first, second


def request(port: int, method: str, path: str, headers: dict, body: bytes = b"") -> tuple[int, dict, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
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


class TwoSockets(ProjectCase):
    """One `Board`, a read socket and an approve socket on two free test ports, loopback."""
    ALLOW_PROXY = False
    APPROVE_HOSTS = ()
    READ_HOSTS = ()

    def setUp(self) -> None:
        super().setUp()
        install_fake_cli(self)
        spy_core(self)
        self.board = srv.Board(self.root, poll=0.2, heartbeat=0.5, prototype_port=1, assets=ASSETS, descriptions=DESCRIPTIONS,
                               approve_token=TEST_TOKEN, approve_hosts=self.APPROVE_HOSTS, read_hosts=self.READ_HOSTS)
        self.read_port, self.approve_port = two_ports()
        self.read = srv.make_server(self.board, "127.0.0.1", self.read_port, role=ports.ROLE_READ, peer_port=self.approve_port)
        self.approve = srv.make_server(self.board, "127.0.0.1", self.approve_port, role=ports.ROLE_APPROVE, peer_port=self.read_port,
                                       allow_proxy_headers=self.ALLOW_PROXY)
        for server in (self.read, self.approve):
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        threading.Thread(target=self.board.watch_loop, daemon=True).start()
        self.addCleanup(self.stop)
        self.ok = {**OK, "Origin": f"http://127.0.0.1:{self.approve_port}"}

    def stop(self) -> None:
        self.board.stop.set()
        with self.board.cond:
            self.board.cond.notify_all()
        for server in (self.read, self.approve):
            server.shutdown()
            server.server_close()

    def post(self, port, headers, data=None):
        body = json.dumps(data if data is not None else {}).encode("utf-8")
        status, answer, _ = request(port, "POST", "/api/approve", headers, body)
        return status, answer

    def offer(self) -> dict:
        spec2, plan2 = "docs/specs/demo-etap-2.md", "docs/plans/2026-01-20-demo-etap-2.md"
        (self.root / srv.RUNS_DIR / "2026-01-20-demo-etap-2").mkdir(parents=True, exist_ok=True)
        spec = descend(self.root, spec2)
        spec.write_text(f"# Spec: Demo 2\n\nStatus: draft (czeka na GO plan)\nPlan: {plan2}\n", encoding="utf-8")
        later = (self.root / PLAN).stat().st_mtime + 100
        os.utime(spec, (later, later))
        status, data, _ = request(self.approve_port, "GET", "/api/status", {})
        action = next(a for a in data["approvals"]["actions"] if a["kind"] == "spec")
        return {"kind": "spec", "path": spec2, "sha": action["sha"]}


class ReadSocket(TwoSockets):
    def test_every_write_route_is_a_404_whatever_the_host_headers_and_token(self) -> None:
        wrong = {"error": "Nie ma takiej strony."}
        for host in (None, f"127.0.0.1:{self.read_port}", "evil.example", "localhost"):
            for name, headers in (("ok", OK), ("origin", {**OK, "Origin": "http://evil.example"}), ("bare", {}), ("proxy", {**OK, "Via": "1.1 x"})):
                for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
                    with self.subTest(host=host, headers=name, method=method):
                        merged = {**headers, **({"Host": host} if host else {})}
                        status, data, _ = request(self.read_port, method, "/api/approve", merged, b"{}")
                        self.assertEqual((status, data), (404, wrong))

    def test_unknown_and_exotic_methods_are_the_same_404(self) -> None:
        for method in ("TRACE", "CONNECT", "PROPFIND", "FOO"):
            with self.subTest(method=method):
                status, data, _ = request(self.read_port, method, "/api/approve", {}, b"")
                self.assertEqual((status, data), (404, {"error": "Nie ma takiej strony."}))

    def test_the_other_routes_of_a_write_are_also_404(self) -> None:
        for path in ("/api/approve", "/anything", "/"):
            status, data, _ = request(self.read_port, "POST", path, OK, b"{}")
            self.assertEqual((status, data), (404, {"error": "Nie ma takiej strony."}))

    def test_no_write_was_made_and_the_core_was_not_called(self) -> None:
        request(self.read_port, "POST", "/api/approve", OK, json.dumps(self.offer()).encode())
        self.set_doc.assert_not_called()
        self.record.assert_not_called()

    def test_the_status_says_read_only(self) -> None:
        status, data, _ = request(self.read_port, "GET", "/api/status", {})
        self.assertEqual(status, 200)
        approvals = data["approvals"]
        self.assertEqual((approvals["role"], approvals["viewer"], approvals["here"], approvals["tokenRequired"]), ("read", True, False, False))

    def test_healthz_of_the_read_socket_has_the_server_id_and_no_token_digest(self) -> None:
        status, data, _ = request(self.read_port, "GET", "/healthz", {})
        self.assertEqual((status, sorted(data)), (200, ["ok", "serverId", "version"]))   # the server id is random per process, not a secret (CORE-1180)
        self.assertNotIn("tokenId", data)

    def test_the_token_is_nowhere_on_the_read_socket(self) -> None:
        for path in ("/", "/api/status", "/healthz", "/api/descriptions", "/assets/app.js", "/assets/dashboard-render.js", "/events"):
            if path == "/events":
                continue   # a stream: the status already carries what it sends
            _status, _data, _ = request(self.read_port, "GET", path, {})
            conn = http.client.HTTPConnection("127.0.0.1", self.read_port, timeout=5)
            conn.request("GET", path)
            body = conn.getresponse().read().decode("utf-8", "replace")
            conn.close()
            self.assertNotIn(TEST_TOKEN, body, path)
            self.assertNotIn(dashboard_token.token_id(TEST_TOKEN), body, path)

    def test_the_read_handler_has_no_mention_of_the_write_route(self) -> None:
        source = inspect.getsource(srv.ReadHandler)
        for needle in ("/api/approve", "board.approve", "_approve_allowed", "TOKEN_HEADER"):
            self.assertNotIn(needle, source)
        self.assertEqual(srv.ReadHandler.role, ports.ROLE_READ)


class RoleFromTheSocket(TwoSockets):
    """The role of a request in the two-port mode is the SOCKET, never the `Host` (integration 1.6.0: A's viewer role meets C's sockets)."""
    APPROVE_HOSTS = ("demo.example",)
    READ_HOSTS = ("viewer.example",)

    def hosts(self, port):
        names = [f"127.0.0.1:{port}", "localhost", "demo.example", "viewer.example"]
        lan = lan_address()
        return names + ([lan] if lan else [])

    def test_the_read_socket_takes_no_write_whatever_the_host(self) -> None:
        offer = json.dumps(self.offer()).encode()
        for host in self.hosts(self.read_port):
            with self.subTest(host=host):
                status, data, _ = request(self.read_port, "POST", "/api/approve", {**OK, "Host": host}, offer)
                self.assertEqual((status, data), (404, {"error": "Nie ma takiej strony."}))
        self.set_doc.assert_not_called()
        self.record.assert_not_called()

    def test_the_read_socket_shows_no_token_digest_or_consent_link_whatever_the_host(self) -> None:
        digest = dashboard_token.token_id(TEST_TOKEN)
        served = 0
        for host in self.hosts(self.read_port):
            with self.subTest(host=host):
                status, data, _ = request(self.read_port, "GET", "/api/status", {"Host": host})
                if status == 403:   # a Host the read socket refuses to serve at all shows nothing either
                    continue
                self.assertEqual(status, 200)
                served += 1
                approvals = data["approvals"]
                self.assertEqual((approvals["here"], approvals["viewer"], approvals["role"], approvals["tokenRequired"]), (False, True, "read", False))
                body = json.dumps(data)
                self.assertEqual((TEST_TOKEN in body, digest in body, "#zgoda=" in body, "approveUrl" in body), (False, False, False, False))
                conn = http.client.HTTPConnection("127.0.0.1", self.read_port, timeout=5)
                try:
                    conn.putrequest("GET", "/events", skip_host=True, skip_accept_encoding=True)
                    conn.putheader("Host", host)
                    conn.endheaders()
                    response = conn.getresponse()
                    first = b"".join(response.fp.readline() for _ in range(4))
                finally:
                    conn.close()
                self.assertEqual((TEST_TOKEN.encode() in first, digest.encode() in first), (False, False))
        self.assertGreaterEqual(served, 1, "the read socket answered no Host at all: the checks above proved nothing")

    def test_here_and_viewer_follow_the_socket_not_the_host(self) -> None:
        served = 0
        for host in self.hosts(self.read_port):
            with self.subTest(socket="read", host=host):
                status, data, _ = request(self.read_port, "GET", "/api/status", {"Host": host})
                if status == 200:
                    served += 1
                    self.assertEqual((data["approvals"]["here"], data["approvals"]["viewer"]), (False, True))
        self.assertGreaterEqual(served, 1, "the read socket answered no Host at all")
        for host in (f"127.0.0.1:{self.approve_port}", "localhost", "demo.example"):
            with self.subTest(socket="approve", host=host):
                status, data, _ = request(self.approve_port, "GET", "/api/status", {"Host": host})
                self.assertEqual(status, 200)
                self.assertEqual((data["approvals"]["here"], data["approvals"]["viewer"]), (True, False))

    def test_the_viewer_sees_the_same_content_as_the_operator_no_filter(self) -> None:
        """DECISION OF THE OWNER (15.11.2026): the status page is internal, the read socket shows logins and question texts as the consent
        socket does. A filter added by accident must fail here as a change of the contract."""
        fixture = {"approvals": {"actions": []},
                   "waiting": [{"what": "GO plan", "who": {"role": "expert", "login": "ewa.test", "label": "ekspert"}}],
                   "run": {"checkpoints": [{"time": "10:00", "text": "czeka: GO plan (kto: ekspert ewa.test)"}]},
                   "expertQuestions": {"open": 1, "blocking": 0, "runs": [{"slug": "demo", "addressees": ["ewa.test"]}],
                                       "items": [{"id": "qn-1", "text": "Czy to ma tak działać?", "addressees": ["ewa.test"]}]}}
        with mock.patch.object(self.board, "status", side_effect=lambda plan=None: json.loads(json.dumps(fixture))):
            _s, read, _ = request(self.read_port, "GET", "/api/status", {})
            _s, approve, _ = request(self.approve_port, "GET", "/api/status", {})
        for key in ("waiting", "run", "expertQuestions"):
            self.assertEqual(read[key], approve[key], key)
        self.assertIn("ewa.test", json.dumps(read))
        self.assertEqual(read["expertQuestions"]["items"][0]["text"], "Czy to ma tak działać?")

    def test_the_role_is_not_counted_from_the_host_in_the_two_port_mode(self) -> None:
        """Control of the mutation: with `_approvals_role` counted from the Host this fails (`demo.example` would make the read socket `here`)."""
        status, data, _ = request(self.read_port, "GET", "/api/status", {"Host": "demo.example"})
        self.assertEqual(status, 200)
        self.assertEqual((data["approvals"]["here"], data["approvals"]["viewer"]), (False, True))


class StreamShares(TwoSockets):
    def test_a_full_read_share_leaves_the_streams_of_the_consent_socket(self) -> None:
        self.assertEqual(srv.SSE_ROLE_LIMITS, {"read": 48, "approve": 16})
        self.board.sse_by_role["read"] = srv.SSE_ROLE_LIMITS["read"]
        self.board.sse_clients = srv.SSE_ROLE_LIMITS["read"]
        status, _data, _ = request(self.read_port, "GET", "/events", {})
        self.assertEqual(status, 503)
        conn = http.client.HTTPConnection("127.0.0.1", self.approve_port, timeout=5)
        try:
            conn.request("GET", "/events")
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn(b"hello", response.fp.readline() + response.fp.readline())
        finally:
            conn.close()
        status, _data, _ = request(self.approve_port, "GET", "/healthz", {})
        self.assertEqual(status, 200)

    def wait_shares(self, role, expected, total=None):
        deadline = time.time() + 5
        while time.time() < deadline:
            with self.board.cond:
                if self.board.sse_by_role.get(role, 0) == expected and (total is None or self.board.sse_clients == total):
                    return True
            time.sleep(0.05)
        return False

    def open_streams(self, port, count):
        streams = []
        for _ in range(count):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/events")
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            response.fp.readline()
            streams.append(conn)
        return streams

    def test_the_share_is_released_when_streams_close_so_a_full_share_serves_again(self) -> None:
        limit = srv.SSE_ROLE_LIMITS["approve"]
        streams = self.open_streams(self.approve_port, limit)
        self.assertTrue(self.wait_shares("approve", limit, limit))
        self.assertEqual(request(self.approve_port, "GET", "/events", {})[0], 503)
        for conn in streams:
            conn.close()
        self.assertTrue(self.wait_shares("approve", 0, 0), dict(self.board.sse_by_role))
        again = self.open_streams(self.approve_port, 1)
        again[0].close()

    def test_the_share_is_released_when_the_connection_is_torn_down(self) -> None:
        import struct
        raw = []
        for _ in range(3):
            sock = socket.create_connection(("127.0.0.1", self.read_port), timeout=5)
            sock.sendall(b"GET /events HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            seen = b""
            while b"hello" not in seen:
                seen += sock.recv(4096)
            raw.append(sock)
        self.assertTrue(self.wait_shares("read", 3, 3))
        for sock in raw:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))   # close sends RST
            sock.close()
        self.assertTrue(self.wait_shares("read", 0, 0), dict(self.board.sse_by_role))

    def test_the_share_is_released_when_the_stream_raises(self) -> None:
        with mock.patch.object(srv.Handler, "_sse", side_effect=[None, RuntimeError("boom")]):   # hello passes, the first ping raises
            conn = http.client.HTTPConnection("127.0.0.1", self.read_port, timeout=5)
            try:
                conn.request("GET", "/events")
                try:
                    conn.getresponse().fp.read(1)
                except (OSError, ValueError, http.client.HTTPException):
                    pass
                self.assertTrue(self.wait_shares("read", 0, 0), dict(self.board.sse_by_role))
            finally:
                conn.close()

    def test_a_full_approve_share_does_not_block_the_read_socket(self) -> None:
        self.board.sse_by_role["approve"] = srv.SSE_ROLE_LIMITS["approve"]
        self.board.sse_clients = srv.SSE_ROLE_LIMITS["approve"]
        self.assertEqual(request(self.approve_port, "GET", "/events", {})[0], 503)
        conn = http.client.HTTPConnection("127.0.0.1", self.read_port, timeout=5)
        try:
            conn.request("GET", "/events")
            self.assertEqual(conn.getresponse().status, 200)
        finally:
            conn.close()

    def test_the_stream_of_the_read_socket_carries_no_token(self) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.read_port, timeout=5)
        try:
            conn.request("GET", "/events")
            response = conn.getresponse()
            first = b"".join(response.fp.readline() for _ in range(4))
        finally:
            conn.close()
        self.assertNotIn(TEST_TOKEN.encode(), first)
        self.assertNotIn(dashboard_token.token_id(TEST_TOKEN).encode(), first)


class ApproveSocket(TwoSockets):
    def test_the_right_request_is_accepted_and_records_where_it_came_from(self) -> None:
        data = self.offer()
        status, answer = self.post(self.approve_port, self.ok, data)
        self.assertEqual(status, 200, answer)
        entries = [json.loads(line) for line in (self.root / srv.RUNS_DIR / "2026-01-20-demo-etap-2" / srv.RUN_APPROVALS).read_text().splitlines()]
        via = entries[-1]["via"]
        self.assertEqual((via["port"], via["host"], via["peer"]), (self.approve_port, "127.0.0.1", "127.0.0.1"))

    def test_an_old_entry_without_via_is_still_read(self) -> None:
        run = self.root / srv.RUNS_DIR / "2026-01-10-demo-etap-1"
        old = run / srv.RUN_APPROVALS
        old.write_text(json.dumps({"schema": 1, "at": "2026-01-01T00:00:00Z", "word": "GO plan", "sha": "abcdef1", "by": "x", "source": "cli"}) + "\n")
        self.assertEqual(len(status_core._approval_entries(old)), 1)

    def test_the_forwarded_for_header_cannot_forge_the_peer(self) -> None:
        data = self.offer()
        status, _ = self.post(self.approve_port, {**self.ok, "X-Forwarded-For": "10.9.9.9"}, data)
        self.assertEqual(status, 403)
        self.set_doc.assert_not_called()

    def test_the_status_says_approve(self) -> None:
        _status, data, _ = request(self.approve_port, "GET", "/api/status", {})
        approvals = data["approvals"]
        self.assertEqual((approvals["role"], approvals["here"], approvals["viewer"], approvals["tokenRequired"]), ("approve", True, False, True))
        _status, healthz, _ = request(self.approve_port, "GET", "/healthz", {})
        self.assertRegex(healthz["serverId"], r"^[0-9a-f]{16}$")
        self.assertRegex(healthz["tokenId"], r"^[0-9a-f]{8}$")   # the consent socket, as in 1.17

    def test_the_order_of_refusals(self) -> None:
        good = self.ok
        cases = [
            ("host", {**good, "Host": "evil.example"}, 403, srv.host_refused_message(srv.ports.ROLE_APPROVE, "evil.example")),
            ("board header", {k: v for k, v in good.items() if k != "X-Status-Board"}, 403, "Brak nagłówka X-Status-Board."),
            ("origin", {**good, "Origin": "http://evil.example"}, 403, "Obcy Origin."),
            ("proxy", {**good, "Forwarded": "for=1.2.3.4"}, 403, srv.PROXY_REFUSED),
            ("content type", {**good, "Content-Type": "text/plain"}, 403, "Oczekiwano application/json."),
            ("token", {k: v for k, v in good.items() if k != "X-Approve-Token"}, 403, srv.TOKEN_REFUSED),
        ]
        # layered breakage: each earlier check hides every later one
        layered = [({**good, "Host": "evil.example", "Origin": "http://x", "Forwarded": "x", "Content-Type": "t", "X-Approve-Token": "z"}, srv.host_refused_message(srv.ports.ROLE_APPROVE, "evil.example")),
                   ({k: v for k, v in {**good, "Origin": "http://x", "Forwarded": "x", "Content-Type": "t", "X-Approve-Token": "z"}.items() if k != "X-Status-Board"}, "Brak nagłówka X-Status-Board."),
                   ({**good, "Origin": "http://x", "Forwarded": "x", "Content-Type": "t", "X-Approve-Token": "z"}, "Obcy Origin."),
                   ({**good, "Forwarded": "x", "Content-Type": "t", "X-Approve-Token": "z"}, srv.PROXY_REFUSED),
                   ({**good, "Content-Type": "t", "X-Approve-Token": "z"}, "Oczekiwano application/json.")]
        for headers, text in layered:
            with self.subTest(text=text):
                self.assertEqual(self.post(self.approve_port, headers)[1].get("error"), text)

    def test_the_token_refusals_have_one_answer_and_the_token_check_precedes_the_body(self) -> None:
        answers = {json.dumps(self.post(self.approve_port, {**self.ok, **extra}, {"kind": "spec"}))
                   for extra in ({"X-Approve-Token": ""}, {"X-Approve-Token": "x"}, {"X-Approve-Token": TEST_TOKEN + "x"}, {"X-Approve-Token": "\xe9"})}
        self.assertEqual(answers, {json.dumps([403, {"error": srv.TOKEN_REFUSED, "code": "approve_token"}])})
        headers = {k: v for k, v in self.ok.items() if k != "X-Approve-Token"}
        status, _, _ = request(self.approve_port, "POST", "/api/approve", headers, b"not json at all")
        self.assertEqual(status, 403)   # refused before the body is parsed

    def test_origin_table(self) -> None:
        own = f"127.0.0.1:{self.approve_port}"
        table = [
            (None, True), (f"http://{own}", True), (f"HTTP://{own.upper()}", True), (f"http://localhost:{self.approve_port}", False),
            ("null", False), ("", False), (f"https://{own}", False), (f"file://{own}", False), ("http://127.0.0.1", False),
            (f"http://127.0.0.1:{self.read_port}", False), (f"http://evil.example:{self.approve_port}", False),
            (f"http://{own}/", False), (f"http://{own}/x", False), (f"http://user@{own}", False),
        ]
        for origin, accepted in table:
            with self.subTest(origin=origin):
                headers = {k: v for k, v in self.ok.items() if k != "Origin"}
                if origin is not None:
                    headers["Origin"] = origin
                status, answer = self.post(self.approve_port, headers, {"kind": "spec"})
                if accepted:
                    self.assertNotEqual(answer.get("error"), "Obcy Origin.")
                else:
                    self.assertEqual((status, answer.get("error")), (403, "Obcy Origin."))
        with self.subTest(origin="two headers"):
            status, answer = self.post(self.approve_port, {**self.ok, "Origin": [f"http://{own}", f"http://{own}"]}, {"kind": "spec"})
            self.assertEqual((status, answer.get("error")), (403, "Obcy Origin."))

    def test_the_origin_of_the_read_port_is_refused_between_ports(self) -> None:
        status, answer = self.post(self.approve_port, {**self.ok, "Origin": f"http://127.0.0.1:{self.read_port}"}, self.offer())
        self.assertEqual((status, answer["error"]), (403, "Obcy Origin."))
        self.set_doc.assert_not_called()

    def test_proxy_headers_are_refused_in_every_spelling_and_the_list_is_closed(self) -> None:
        for name in ports.PROXY_HEADERS:
            for spelling in (name, name.lower(), name.upper()):
                for value in ("1.2.3.4", ""):
                    with self.subTest(header=spelling, value=value):
                        status, answer = self.post(self.approve_port, {**self.ok, spelling: value}, self.offer())
                        self.assertEqual((status, answer["error"]), (403, srv.PROXY_REFUSED))
        self.assertEqual(set(ports.PROXY_HEADERS), {"Forwarded", "Forwarded-For", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto",
                                                    "X-Forwarded-Port", "X-Forwarded-Server", "X-Forwarded-Scheme", "X-Forwarded-Prefix",
                                                    "X-Original-Forwarded-For", "X-Original-Host", "X-Real-IP", "X-Client-IP", "X-Cluster-Client-IP",
                                                    "True-Client-IP", "CF-Connecting-IP", "Fastly-Client-IP", "X-Envoy-External-Address", "Via"})
        self.set_doc.assert_not_called()

    def test_host_rule(self) -> None:
        for host, accepted in ((f"127.0.0.1:{self.approve_port}", True), (f"LOCALHOST:{self.approve_port}", True), (f"[::1]:{self.approve_port}", True),
                               (f"localhost.:{self.approve_port}", False), (f"evil.example:{self.approve_port}", False),
                               (f"0.0.0.0:{self.approve_port}", False), (f"[fe80::1%25lo]:{self.approve_port}", False)):
            with self.subTest(host=host):
                status, _answer = self.post(self.approve_port, {**self.ok, "Host": host, "Origin": f"http://{host}"}, {"kind": "spec"})
                if accepted:
                    self.assertNotEqual(_answer.get("error"), srv.host_refused_message(srv.ports.ROLE_APPROVE, host))
                else:
                    self.assertEqual(_answer.get("error"), srv.host_refused_message(srv.ports.ROLE_APPROVE, host))


class ReadHostsOnTheReadSocket(TwoSockets):
    READ_HOSTS = ("viewer.example",)

    def test_a_read_host_opens_the_read_socket_only(self) -> None:
        self.assertEqual(request(self.read_port, "GET", "/healthz", {"Host": "viewer.example"})[0], 200)
        self.assertEqual(request(self.approve_port, "GET", "/healthz", {"Host": "viewer.example"})[0], 403)
        self.assertEqual(request(self.read_port, "GET", "/healthz", {"Host": "other.example"})[0], 403)


class StarReadHost(TwoSockets):
    READ_HOSTS = ("*",)

    def test_star_opens_any_name_on_the_read_socket_and_never_on_consents(self) -> None:
        self.assertEqual(request(self.read_port, "GET", "/healthz", {"Host": "tunnel.example"})[0], 200)
        self.assertEqual(request(self.approve_port, "GET", "/healthz", {"Host": "tunnel.example"})[0], 403)
        status, answer = self.post(self.approve_port, {**self.ok, "Host": "tunnel.example", "Origin": "http://tunnel.example"})
        self.assertEqual((status, answer["error"]), (403, srv.host_refused_message(srv.ports.ROLE_APPROVE, "tunnel.example")))


class AllowedProxy(TwoSockets):
    ALLOW_PROXY = True

    def test_allow_proxy_headers_only_lifts_that_one_check(self) -> None:
        with_proxy = {**self.ok, "X-Forwarded-For": "10.0.0.9"}
        status, answer = self.post(self.approve_port, with_proxy, self.offer())
        self.assertEqual(status, 200, answer)
        for name, headers, text in (("token", {**with_proxy, "X-Approve-Token": "x"}, srv.TOKEN_REFUSED),
                                    ("origin", {**with_proxy, "Origin": "http://evil.example"}, "Obcy Origin."),
                                    ("host", {**with_proxy, "Host": "evil.example"}, srv.host_refused_message(srv.ports.ROLE_APPROVE, "evil.example"))):
            with self.subTest(name=name):
                self.assertEqual(self.post(self.approve_port, headers)[1]["error"], text)
        entries = [json.loads(line) for line in (self.root / srv.RUNS_DIR / "2026-01-20-demo-etap-2" / srv.RUN_APPROVALS).read_text().splitlines()]
        self.assertEqual(entries[-1]["via"]["peer"], "127.0.0.1")   # the socket, never the header


class FailedTokens(unittest.TestCase):
    def test_counter_reports_at_once_then_once_a_minute_and_keeps_constant_memory(self) -> None:
        now = [100.0]
        counter = ports.TokenFailures(60.0, clock=lambda: now[0])
        self.assertIn("1 nieudanych", counter.add("10.0.0.5"))
        for _ in range(5000):
            now[0] += 0.001
            self.assertIsNone(counter.add("10.0.0.6"))
        now[0] += 61
        line = counter.add("10.0.0.7")
        self.assertIn("5001 nieudanych", line)
        self.assertIn("10.0.0.7", line)
        self.assertNotIn(TEST_TOKEN, line)
        self.assertEqual(sorted(vars(counter)), sorted(["_lock", "_count", "_reported_at", "_interval", "_clock"]))   # no per-address map


class DefaultInterval(TwoSockets):
    def test_the_board_counter_reports_at_most_once_a_minute_by_default(self) -> None:
        counter = self.board.token_failures
        self.assertEqual(counter._interval, 60.0)
        self.assertIsNotNone(counter.add("10.0.0.5"))   # the first one at once
        self.assertIsNone(counter.add("10.0.0.5"))      # the next ones within the minute are summed up


class LocalIp(unittest.TestCase):
    def test_loopback_unspecified_names_and_zones(self) -> None:
        for name, expected in (("127.0.0.1", True), ("::1", True), ("0.0.0.0", False), ("::", False), ("evil.example", False), ("localhost", False),
                               ("fe80::1%25eth0", False), ("203.0.113.9", False), ("", False)):
            with self.subTest(name=name):
                self.assertEqual(ports.is_local_ip(name), expected)

    def test_multicast_broadcast_reserved_and_subnet_broadcast_are_refused(self) -> None:
        for name in ("224.0.0.1", "239.255.255.250", "255.255.255.255", "240.0.0.1", "192.168.1.255", "10.255.255.255", "ff02::1", "ff05::2"):
            with self.subTest(name=name):
                self.assertFalse(ports.is_local_ip(name))
        self.assertTrue(ports.is_local_ip("::1"))   # lies in the reserved ::/8 block, still the loopback
        self.assertTrue(ports.is_local_ip("127.0.0.2"))

    def test_a_real_address_ending_in_255_is_accepted_only_when_enumerated(self) -> None:
        with mock.patch.object(ports, "_enumerated_addresses", return_value={"10.0.1.255"}):
            self.assertTrue(ports.is_local_ip("10.0.1.255"))    # a /23 or wider: the real address of this machine
            self.assertFalse(ports.is_local_ip("10.0.0.255"))   # a broadcast-looking address that is not ours
            self.assertFalse(ports.is_local_ip("255.255.255.255"))

    def test_where_the_bind_probe_proves_nothing_only_the_enumerated_addresses_count(self) -> None:
        with mock.patch.object(ports, "_nonlocal_bind_allowed", return_value=True), \
                mock.patch.object(ports, "_enumerated_addresses", return_value={"192.0.2.77"}):
            self.assertTrue(ports.is_local_ip("192.0.2.77"))
            self.assertFalse(ports.is_local_ip("198.51.100.9"))   # a bind would succeed here (non-local bind), the list says no
            self.assertTrue(ports.is_local_ip("127.0.0.1"))

    def test_an_address_of_this_machine_is_accepted(self) -> None:
        address = lan_address()
        if address is None:
            self.skipTest("no non-loopback address on this machine; deliberately not in skip-baseline, so on CI runners (which have one) a skip is an error")
        self.assertTrue(ports.is_local_ip(address))


class SlowResolver(unittest.TestCase):
    """The name of this machine may not resolve for tens of seconds: nothing on the start, the stop or the request path may wait for it."""

    def setUp(self) -> None:
        ports._reset_host_lookup()
        self.addCleanup(ports._reset_host_lookup)
        patch = mock.patch.object(ports, "HOST_LOOKUP_TIMEOUT", 0.3)
        patch.start()
        self.addCleanup(patch.stop)
        self.release = threading.Event()
        self.addCleanup(self.release.set)   # the lookup thread of a test never outlives it
        self.calls = 0
        real = socket.getaddrinfo

        def slow(host, *args, **kwargs):
            if host == socket.gethostname():
                self.calls += 1
                self.release.wait(30)
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.77", 0))]
            return real(host, *args, **kwargs)
        resolver = mock.patch.object(socket, "getaddrinfo", slow)
        resolver.start()
        self.addCleanup(resolver.stop)

    def test_lan_addresses_does_not_wait_for_the_resolver(self) -> None:
        started = time.monotonic()
        found = srv.lan_addresses()
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertNotIn("192.0.2.77", found)   # not resolved yet: only the route address (or the placeholder)

    def test_a_slow_resolver_refuses_a_255_address_that_no_other_way_found_and_does_not_multiply_lookups(self) -> None:
        started = time.monotonic()
        with mock.patch.object(ports.socket, "socket", side_effect=OSError("no route")):   # the route probe finds nothing either
            answers = [ports.is_local_ip("10.0.1.255") for _ in range(10)]
        self.assertEqual(answers, [False] * 10)   # fail closed
        self.assertLess(time.monotonic() - started, 3.0)   # the first call waits once, the negative answer serves the rest
        self.assertEqual(self.calls, 1)   # N requests, at most one resolution

    def test_a_late_answer_fills_the_cache(self) -> None:
        with mock.patch.object(ports, "HOST_LOOKUP_NEGATIVE_TTL", 0.0):
            self.assertEqual(ports.host_addresses(), set())   # too slow
            self.release.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and ports.host_addresses() != {"192.0.2.77"}:
                time.sleep(0.05)
        self.assertEqual(ports.host_addresses(), {"192.0.2.77"})
        self.assertEqual(self.calls, 1)

    def test_an_old_good_answer_serves_a_slow_resolver_only_for_a_limited_time(self) -> None:
        base = time.monotonic()
        ports._lookup.update(value={"192.0.2.55"}, until=0.0, good_at=base)   # a good answer whose validity has run out; the resolver is slow now
        with mock.patch.object(ports, "time") as clock:
            clock.monotonic.return_value = base + 1
            self.assertEqual(ports.host_addresses(), {"192.0.2.55"})   # too slow, the old answer is still young enough
            clock.monotonic.return_value = base + 2 * ports.HOST_LOOKUP_NEGATIVE_TTL   # the negative memory ran out and was renewed, the answer is still not too old
            self.assertEqual(ports.host_addresses(), {"192.0.2.55"})
            clock.monotonic.return_value = base + ports.HOST_LOOKUP_STALE_MAX - 1
            self.assertEqual(ports.host_addresses(), {"192.0.2.55"})   # renewed just before the limit ...
            clock.monotonic.return_value = base + ports.HOST_LOOKUP_STALE_MAX + 1
            self.assertEqual(ports.host_addresses(), set())   # ... but the renewal did not carry it beyond the limit: too old: fail closed, no longer renewed for ever
            clock.monotonic.return_value = base + ports.HOST_LOOKUP_STALE_MAX + 2
            self.assertEqual(ports.host_addresses(), set())   # and the empty answer is what is remembered
        self.assertEqual(self.calls, 1)   # one lookup throughout

    def test_a_failed_lookup_is_not_a_good_answer_to_fall_back_on(self) -> None:
        ports._reset_host_lookup()
        with mock.patch.object(socket, "getaddrinfo", side_effect=socket.gaierror("no name")):
            ports.host_addresses()
        self.assertIsNone(ports._lookup["good_at"])
        self.assertEqual(ports._lookup["value"], set())

    def test_an_exception_of_the_resolver_is_an_empty_answer_and_nothing_stays_pending(self) -> None:
        for error in (UnicodeError("bad name"), ValueError("bad"), socket.gaierror("no name")):
            with self.subTest(error=type(error).__name__):
                ports._reset_host_lookup()
                with mock.patch.object(socket, "getaddrinfo", side_effect=error):
                    started = time.monotonic()
                    self.assertEqual(ports.host_addresses(), set())
                    self.assertLess(time.monotonic() - started, 1.0)   # the waiter is released at once, not after the limit
                    self.assertIsNone(ports._lookup["pending"])
        # after the negative memory a new resolution starts and can succeed
        ports._reset_host_lookup()
        with mock.patch.object(socket, "getaddrinfo", side_effect=UnicodeError("x")):
            ports.host_addresses()
        with mock.patch.object(ports, "time") as clock:
            clock.monotonic.return_value = time.monotonic() + ports.HOST_LOOKUP_NEGATIVE_TTL + 1
            self.release.set()
            self.assertEqual(ports.host_addresses(), {"192.0.2.77"})

    def test_a_failed_lookup_is_remembered_briefly_not_like_a_success(self) -> None:
        ports._reset_host_lookup()
        with mock.patch.object(socket, "getaddrinfo", side_effect=socket.gaierror("no name")):
            ports.host_addresses()
        self.assertLessEqual(ports._lookup["until"] - time.monotonic(), ports.HOST_LOOKUP_NEGATIVE_TTL + 0.5)

    def test_a_slow_answer_after_the_memory_expired_keeps_the_older_good_answer(self) -> None:
        self.release.set()
        self.assertEqual(ports.host_addresses(), {"192.0.2.77"})
        self.release.clear()
        ports._lookup["until"] = 0.0   # the good answer is old now
        self.assertEqual(ports.host_addresses(), {"192.0.2.77"})   # the new lookup is too slow: the old answer stays

    def test_a_stale_lookup_never_writes_into_the_cache_after_a_reset(self) -> None:
        old = threading.Thread(target=lambda: ports.host_addresses(), daemon=True)
        old.start()
        deadline = time.monotonic() + 3
        while ports._lookup["pending"] is None and time.monotonic() < deadline:
            time.sleep(0.01)
        stale = ports._lookup["pending"]
        self.assertIsNotNone(stale)
        ports._reset_host_lookup()
        self.release.set()   # the old lookup returns now
        self.assertTrue(stale.wait(3))
        old.join(3)
        self.assertIsNone(ports._lookup["value"])   # the new (empty) cache was not written by it

    def test_a_resolved_answer_is_remembered(self) -> None:
        self.release.set()
        self.assertEqual(ports.host_addresses(), {"192.0.2.77"})
        self.assertEqual(ports.host_addresses(), {"192.0.2.77"})
        self.assertEqual(self.calls, 1)


class Flags(unittest.TestCase):
    def resolve(self, **kwargs):
        values = dict(port=None, read_port=None, approve_port=None, read_bind=None, approve_bind=None, host=None, no_approvals=False,
                      allow_proxy_headers=False, approve_host=[], read_host=[])
        return ports.resolve(SimpleNamespace(**{**values, **kwargs}), srv.port_problem)

    def test_single_port_mode_is_only_an_explicit_port(self) -> None:
        self.assertIsNone(self.resolve(port=5155))
        self.assertIsNone(self.resolve(port=5155, host="0.0.0.0", no_approvals=True))

    def test_without_port_flags_the_default_two_port_mode(self) -> None:
        mode = self.resolve()
        self.assertEqual((mode["read"], mode["approve"]), ({"port": 5140, "bind": "0.0.0.0"}, {"port": 5141, "bind": "127.0.0.1"}))
        self.assertTrue(mode["read_default"])
        mode = self.resolve(no_approvals=True)
        self.assertEqual((mode["read"], mode["approve"]), ({"port": 5140, "bind": "0.0.0.0"}, None))

    def test_explicit_ports_keep_consents_on_this_machine_only(self) -> None:
        mode = self.resolve(read_port=5151, approve_port=5152)
        self.assertEqual((mode["read"], mode["approve"]), ({"port": 5151, "bind": "0.0.0.0"}, {"port": 5152, "bind": "127.0.0.1"}))

    def test_an_explicit_host_moves_only_the_read_socket(self) -> None:
        mode = self.resolve(read_port=5151, approve_port=5152, host="192.168.1.2")
        self.assertEqual((mode["read"]["bind"], mode["approve"]["bind"]), ("192.168.1.2", "127.0.0.1"))
        self.assertFalse(mode["read_default"])
        mode = self.resolve(read_port=5151, approve_port=5152, host="")   # an explicit empty host keeps meaning every interface
        self.assertEqual((mode["read"]["bind"], mode["approve"]["bind"]), ("0.0.0.0", "127.0.0.1"))
        with self.assertRaises(ValueError):   # no read socket: nothing for --host to move
            self.resolve(approve_port=5152, host="127.0.0.1")

    def test_calls_that_meant_one_port_with_a_host_gate_are_refused_with_a_hint(self) -> None:
        for kwargs, hint in ((dict(host="0.0.0.0"), "--port N --host"), (dict(approve_host=["tunnel.example"]), "--port N --approve-host")):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError) as caught:
                self.resolve(**kwargs)
            self.assertIn(hint, str(caught.exception))
        self.assertIsNone(self.resolve(port=5155, host="0.0.0.0", approve_host=["tunnel.example"]))   # the explicit old mode
        self.assertIsNotNone(self.resolve(read_port=5151, approve_port=5152, approve_host=["phone.example"]))

    def test_narrowing_flags(self) -> None:
        mode = self.resolve(read_port=5151, approve_port=5152, approve_bind="0.0.0.0")
        self.assertEqual((mode["read"]["bind"], mode["approve"]["bind"]), ("0.0.0.0", "0.0.0.0"))
        mode = self.resolve(read_port=5151, approve_port=5152, host="10.0.0.5", read_bind="0.0.0.0")
        self.assertEqual((mode["read"]["bind"], mode["approve"]["bind"]), ("0.0.0.0", "127.0.0.1"))

    def test_one_socket_alone(self) -> None:
        self.assertEqual(self.resolve(read_port=5151)["approve"], None)
        self.assertEqual(self.resolve(approve_port=5152)["read"], None)

    def test_refused_combinations(self) -> None:
        for kwargs in (dict(port=5155, read_port=5151), dict(port=5155, approve_port=5152), dict(read_port=5151, approve_port=5152, no_approvals=True),
                       dict(approve_port=5152, no_approvals=True), dict(read_port=5151, approve_port=5151), dict(read_port=5040),
                       dict(read_port=5151, approve_port=5200), dict(port=5155, read_bind="127.0.0.1"), dict(port=5155, approve_bind="127.0.0.1"),
                       dict(read_port=5151, approve_bind="127.0.0.1"), dict(approve_port=5152, read_bind="127.0.0.1"),
                       dict(port=5155, allow_proxy_headers=True), dict(read_port=5151, allow_proxy_headers=True),
                       dict(no_approvals=True, approve_bind="127.0.0.1"), dict(no_approvals=True, allow_proxy_headers=True),
                       dict(approve_port=5152, approve_host=["*"]), dict(approve_port=5152, read_host=["*"])):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.resolve(**kwargs)

    def test_allowed_combinations(self) -> None:
        self.assertIsNotNone(self.resolve(read_port=5151, no_approvals=True))
        self.assertIsNotNone(self.resolve(read_port=5151, approve_host=["phone.example"]))
        self.assertIsNotNone(self.resolve(approve_port=5152, allow_proxy_headers=True))
        self.assertIsNotNone(self.resolve(read_port=5151, read_host=["*"]))


class SinglePortHost(unittest.TestCase):
    def listen_host(self, host):
        """The `--host` that `serve_command` gives to `make_server` in the single-port mode (the server itself is replaced)."""
        args = SimpleNamespace(path=".", layers=None, plan=None, session_url=None, approve_host=[], read_host=[], poll=1.0, verbose=False,
                               prototype_port=None, read_port=None, approve_port=None, read_bind=None, approve_bind=None, allow_proxy_headers=False,
                               host=host, port=5155, no_approvals=False)
        seen = {}

        def fake(board, listen, port, *rest, **kwargs):
            seen["host"] = listen
            raise OSError(98, "busy")

        root = Path(tempfile.mkdtemp(prefix="dashboard-host-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        project = make_project(root)
        manifest = project / srv.MANIFEST
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("skills: [dashboard]\n", encoding="utf-8")
        args.path = str(project)
        args.layers = [f"core={ROOT / 'library'}"]
        with mock.patch.object(srv, "make_server", fake), mock.patch("sys.stdout", new=__import__("io").StringIO()):
            srv.serve_command(args)
        return seen.get("host")

    def test_not_given_is_loopback_and_an_explicit_empty_host_is_every_interface_as_before(self) -> None:
        self.assertEqual(self.listen_host(None), "127.0.0.1")
        self.assertEqual(self.listen_host(""), "")
        self.assertEqual(self.listen_host("0.0.0.0"), "0.0.0.0")


class Process(unittest.TestCase):
    """`dashboard serve` as a process: sockets, listen address, console, state files, `dashboard url`, stopping."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="dashboard-serve-ports-"))
        guard_real_state(self)
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.root = make_project(self.tmp)
        manifest = self.root / srv.MANIFEST
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("skills: [dashboard]\n", encoding="utf-8")
        self.env = {**os.environ, "SKILLSYNC_CONFIG": str(self.tmp / "machine-config.yaml"), "XDG_CACHE_HOME": str(self.tmp / "xdg-cache"), **home_env(self.tmp)}
        self.layer = ["--layer", f"core={ROOT / 'library'}"]
        self.procs: list[subprocess.Popen] = []
        self.addCleanup(self.kill)

    def kill(self) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
            for stream in (proc.stdout, proc.stderr):
                if stream:
                    stream.close()

    def serve(self, *flags):
        proc = subprocess.Popen([sys.executable, str(CLI), "dashboard", "serve", str(self.root), *flags, *self.layer], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=self.env, text=True)
        self.procs.append(proc)
        return proc

    def wait_up(self, *ports_) -> None:
        deadline = time.time() + 25
        pending = set(ports_)
        while pending and time.time() < deadline:
            for port in list(pending):
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                        pending.discard(port)
                except OSError:
                    time.sleep(0.1)
        self.assertFalse(pending, f"not listening: {pending}")

    CONSOLE_LINES = 5000   # the most lines of one stream that `console_until` keeps (the oldest are dropped): the output of a process must not grow the test without bound

    def console_until(self, proc, *needles, timeout=30):
        """Reads the console (stderr) of `proc` until every needle was printed (the start logs after the sockets are up, and a slow name
        lookup delays the later lines); returns `(lines, finish)`: `finish()` stops the process and returns the whole console text.
        stdout is read too (a full pipe would block the process) and kept in `self.stdout_lines`. When a needle is still missing after
        `timeout` seconds the test FAILS here, naming the missing lines and showing the console, not later in an unrelated assertion.
        The process and the pipes are closed also when the test raises before `finish()`."""
        lines: collections.deque = collections.deque(maxlen=self.CONSOLE_LINES)
        self.stdout_lines = collections.deque(maxlen=self.CONSOLE_LINES)
        readers = [threading.Thread(target=lambda stream=stream, sink=sink: [sink.append(line) for line in iter(stream.readline, "")], daemon=True)
                   for stream, sink in ((proc.stderr, lines), (proc.stdout, self.stdout_lines)) if stream]
        for reader in readers:
            reader.start()

        def finish() -> str:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
            for reader in readers:
                reader.join(10)
            return "".join(lines)
        self.addCleanup(finish)   # runs before `kill` (registered in setUp): a failing test still leaves no process and no reader behind

        def missing() -> list[str]:
            printed = list(lines)
            return [needle for needle in needles if not any(needle in line for line in printed)]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and missing():
            time.sleep(0.05)
        absent = missing()
        if absent:
            console = finish()
            self.fail(f"console line(s) not printed within {timeout} s: {absent!r}\nstderr:\n{console}\nstdout:\n{''.join(self.stdout_lines)}")
        return lines, finish

    def script(self, code: str):
        proc = subprocess.Popen([sys.executable, "-u", "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.procs.append(proc)
        return proc

    def test_console_until_fails_naming_the_missing_line_and_stops_the_process(self) -> None:
        proc = self.script("import sys, time; print('hello', file=sys.stderr); time.sleep(60)")
        with self.assertRaises(AssertionError) as caught:
            self.console_until(proc, "hello", "never printed", timeout=5)
        message = str(caught.exception)
        self.assertIn("'never printed'", message)
        self.assertNotIn("'hello'", message)   # only what is missing is named
        self.assertIn("stderr:\nhello\n", message)   # the console that was read is shown
        self.assertIsNotNone(proc.poll())   # the process was stopped, not left running

    def test_console_until_reads_stdout_so_that_a_full_pipe_does_not_block_the_process(self) -> None:
        proc = self.script("import sys; sys.stdout.write('x' * 400000 + '\\n'); sys.stdout.flush(); print('ready', file=sys.stderr); sys.stdout.write('y' * 400000 + '\\n')")
        lines, finish = self.console_until(proc, "ready", timeout=20)
        self.assertEqual(proc.wait(timeout=20), 0)   # it ends by itself: the second write did not block on a full pipe
        finish()
        self.assertEqual(sum(len(line.strip()) for line in self.stdout_lines), 800000)
        self.assertEqual(list(lines), ["ready\n"])

    def test_console_until_keeps_a_bounded_number_of_lines(self) -> None:
        proc = self.script(f"import sys\nfor n in range({self.CONSOLE_LINES + 500}): print(n, file=sys.stderr)\nprint('END', file=sys.stderr)")
        lines, finish = self.console_until(proc, "END", timeout=20)
        text = finish()
        self.assertLessEqual(len(lines), self.CONSOLE_LINES)
        self.assertTrue(text.endswith("END\n"))   # the newest lines are kept

    def blocked(self, *flags) -> dict:
        done = subprocess.run([sys.executable, str(CLI), "dashboard", "serve", str(self.root), *flags, *self.layer], capture_output=True, text=True,
                              env=self.env, timeout=60)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        return json.loads(done.stdout)

    def state_files(self) -> list[dict]:
        directory = dashboard_token.state_dir()
        with mock.patch.dict(os.environ, home_env(self.tmp)):
            directory = dashboard_token.state_dir()
            return [json.loads(path.read_text()) for path in sorted(directory.glob("*.json")) if not path.name.startswith(".")] if directory.is_dir() else []

    def test_two_sockets_state_files_and_url(self) -> None:
        read_port, approve_port = two_ports()
        proc = self.serve("--read-port", str(read_port), "--approve-port", str(approve_port), "--host", "127.0.0.1")
        self.wait_up(read_port, approve_port)
        status, data, _ = request(read_port, "GET", "/api/status", {})
        self.assertEqual(data["approvals"]["role"], "read")
        status, data, _ = request(approve_port, "GET", "/api/status", {})
        self.assertEqual(data["approvals"]["role"], "approve")
        deadline = time.time() + 10
        while len(self.state_files()) < 2 and time.time() < deadline:
            time.sleep(0.1)
        records = {record["role"]: record for record in self.state_files()}
        self.assertEqual({key: records["read"][key] for key in ("port", "peer", "approvals")}, {"port": read_port, "peer": approve_port, "approvals": False})
        self.assertNotIn("token", records["read"])
        self.assertEqual((records["approve"]["port"], records["approve"]["peer"]), (approve_port, read_port))
        self.assertTrue(records["approve"]["token"])
        with mock.patch.dict(os.environ, home_env(self.tmp)):
            url = subprocess.run([sys.executable, str(CLI), "dashboard", "url", str(self.root), "--json", "--port", str(approve_port), *self.layer], capture_output=True, text=True,
                                 env=self.env, timeout=60)   # a server started with flags is standalone: `url` names it by `--port` (CORE-1180)
            reading = subprocess.run([sys.executable, str(CLI), "dashboard", "url", str(self.root), "--read", "--port", str(read_port), *self.layer], capture_output=True, text=True,
                                     env=self.env, timeout=60)
        answer = json.loads(url.stdout)
        self.assertEqual((answer["status"], answer["port"], answer["role"]), ("PASS", approve_port, "approve"))
        self.assertTrue(answer["url"].startswith(f"http://127.0.0.1:{approve_port}/#zgoda="))
        self.assertEqual(answer["readUrl"], f"http://127.0.0.1:{read_port}/")
        self.assertEqual(reading.stdout.strip(), f"http://127.0.0.1:{read_port}/")
        self.assertNotIn("#", reading.stdout)

    @unittest.skipIf(os.name == "nt", "SIGTERM kończy proces na Windows bez obsługi")
    def test_a_signal_stops_both_sockets_and_removes_both_state_files(self) -> None:
        read_port, approve_port = two_ports()
        proc = self.serve("--read-port", str(read_port), "--approve-port", str(approve_port), "--host", "127.0.0.1")
        self.wait_up(read_port, approve_port)
        deadline = time.time() + 10
        while len(self.state_files()) < 2 and time.time() < deadline:
            time.sleep(0.1)
        self.assertEqual(len(self.state_files()), 2)
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(json.loads(out)["status"], "PASS")
        self.assertEqual(self.state_files(), [])
        for port in (read_port, approve_port):   # both sockets are free again
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # a stopped server leaves TIME_WAIT behind
                probe.bind(("127.0.0.1", port))

    def test_an_interrupt_in_process_stops_both_sockets_and_removes_both_state_files(self) -> None:
        """The same stop on every system: the interrupt of the main thread (what Ctrl+C does) instead of a signal to a process."""
        import _thread
        read_port, approve_port = two_ports()
        args = SimpleNamespace(path=str(self.root), layers=[f"core={ROOT / 'library'}"], plan=None, session_url=None, approve_host=[], read_host=[],
                               poll=1.0, verbose=False, prototype_port=None, read_port=read_port, approve_port=approve_port, read_bind=None,
                               approve_bind=None, allow_proxy_headers=False, host="127.0.0.1", port=None, no_approvals=False)
        seen: dict = {}

        def interrupt_when_up() -> None:
            deadline = time.time() + 20
            while time.time() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", read_port), timeout=0.3), socket.create_connection(("127.0.0.1", approve_port), timeout=0.3):
                        break
                except OSError:
                    time.sleep(0.1)
            while len(self.state_files()) < 2 and time.time() < deadline:
                time.sleep(0.1)
            seen["states"] = len(self.state_files())
            _thread.interrupt_main()

        previous = signal.signal(signal.SIGINT, signal.default_int_handler)   # a runner started with `&` ignores SIGINT, and then interrupt_main does nothing
        self.addCleanup(signal.signal, signal.SIGINT, previous)
        with mock.patch.dict(os.environ, home_env(self.tmp)):
            threading.Thread(target=interrupt_when_up, daemon=True).start()
            with mock.patch("sys.stdout", new=__import__("io").StringIO()) as out, mock.patch("sys.stderr", new=__import__("io").StringIO()):
                code = srv.serve_command(args)
            self.assertEqual(self.state_files(), [])
        self.assertEqual((code, seen.get("states")), (0, 2))
        self.assertEqual(json.loads(out.getvalue())["status"], "PASS")
        for port in (read_port, approve_port):
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) if os.name != "nt" else None
                probe.bind(("127.0.0.1", port))

    def test_console_has_a_line_per_socket_and_no_token_on_the_read_line(self) -> None:
        read_port, approve_port = two_ports()
        proc = self.serve("--read-port", str(read_port), "--approve-port", str(approve_port), "--approve-bind", "127.0.0.1", "--read-bind", "127.0.0.1")
        self.wait_up(read_port, approve_port)
        _lines, finish = self.console_until(proc, "odczyt (do tunelu", "zgody (przyciski")
        err = finish()
        lines = err.splitlines()
        read_lines = [line for line in lines if "odczyt (do tunelu" in line]
        approve_lines = [line for line in lines if line.count("zgody (przyciski") and "#zgoda=" in line]
        self.assertEqual((len(read_lines), len(approve_lines)), (1, 1))
        self.assertNotIn("#zgoda=", read_lines[0])
        self.assertNotIn("UWAGA: port zgód", err)   # both narrowed to loopback: no network warning

    def test_listening_beyond_loopback_warns_for_the_read_socket_only_and_keeps_consents_local(self) -> None:
        read_port, approve_port = two_ports()
        proc = self.serve("--read-port", str(read_port), "--approve-port", str(approve_port))
        self.wait_up(read_port, approve_port)
        address = lan_address()
        if address is None:
            self.skipTest("no non-loopback address on this machine; deliberately not in skip-baseline, so on CI runners (which have one) a skip is an error")
        with socket.create_connection((address, read_port), timeout=2):   # the default of the read socket: every interface
            pass
        with self.assertRaises(OSError):   # the consent socket: this machine only
            socket.create_connection((address, approve_port), timeout=2).close()
        _lines, finish = self.console_until(proc, f"UWAGA: port odczytu {read_port}")
        err = finish()
        self.assertIn(f"UWAGA: port odczytu {read_port} słucha na 0.0.0.0", err)
        self.assertNotIn("UWAGA: port zgód", err)

    def test_console_of_the_default_listen_address(self) -> None:
        """K5/K8/K12: one address line per LAN address, the warning, the sentence about the changed default, the consents-are-local line."""
        read_port, approve_port = two_ports()
        proc = self.serve("--read-port", str(read_port), "--approve-port", str(approve_port))
        self.wait_up(read_port, approve_port)
        _lines, finish = self.console_until(proc, f"UWAGA: port odczytu {read_port}", "zgody są tylko na tej maszynie")
        err = finish()
        if srv._version_tuple(srv.__version__) < ports.LISTEN_NOTE_UNTIL:   # the sentence ends with 1.10
            self.assertIn("od 1.8.0 odczyt słucha domyślnie na wszystkich interfejsach", err)
        else:
            self.assertNotIn("od 1.8.0", err)
        self.assertIn("--read-bind 127.0.0.1", err)
        self.assertIn("dashboard.local_only: true", err)
        self.assertIn("zgody z telefonu: --approve-bind 0.0.0.0", err)
        self.assertNotIn("strona tylko na tej maszynie", err)
        names = [line for line in err.splitlines() if "albo po nazwie tej maszyny" in line]
        if ports.own_names():   # a machine without a usable host name prints no such line
            self.assertEqual(len(names), 1)
            self.assertIn(f":{read_port}/", names[0])
        address = lan_address()
        if address is not None:
            self.assertEqual(len([line for line in err.splitlines() if line.rstrip().endswith(f"z innego urządzenia: http://{address}:{read_port}/")]), 1)

    def test_console_of_a_narrowed_listen_address_has_no_warning_and_no_sentence(self) -> None:
        read_port, approve_port = two_ports()
        proc = self.serve("--read-port", str(read_port), "--approve-port", str(approve_port), "--read-bind", "127.0.0.1")
        self.wait_up(read_port, approve_port)
        _lines, finish = self.console_until(proc, "strona tylko na tej maszynie")
        err = finish()
        self.assertIn("strona tylko na tej maszynie", err)
        for text in ("UWAGA: port odczytu", "od 1.8.0", "zgody z telefonu"):
            self.assertNotIn(text, err)

    def test_the_consent_socket_listens_beyond_loopback_only_by_an_explicit_flag(self) -> None:
        read_port, approve_port = two_ports()
        proc = self.serve("--read-port", str(read_port), "--approve-port", str(approve_port), "--approve-bind", "0.0.0.0")
        self.wait_up(read_port, approve_port)
        address = lan_address()
        if address is None:
            self.skipTest("no non-loopback address on this machine; deliberately not in skip-baseline, so on CI runners (which have one) a skip is an error")
        with socket.create_connection((address, approve_port), timeout=2):
            pass
        _lines, finish = self.console_until(proc, f"UWAGA: port zgód {approve_port}")
        self.assertIn(f"UWAGA: port zgód {approve_port} słucha na 0.0.0.0", finish())

    def slow_resolver_env(self) -> dict:
        directory = self.tmp / "slow-dns"
        directory.mkdir()
        (directory / "sitecustomize.py").write_text(
            "import signal, socket, threading, time\n_real = socket.getaddrinfo\n_host = socket.gethostname()\n"
            "def slow(host, *a, **k):\n    if host == _host:\n"
            # like a blocking C resolver: a signal is seen only after the call returns
            "        main = threading.current_thread() is threading.main_thread()\n"
            "        if main:\n            signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT})\n"
            "        try:\n            time.sleep(25)\n"
            "        finally:\n            if main:\n                signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM, signal.SIGINT})\n"
            "    return _real(host, *a, **k)\n"
            "socket.getaddrinfo = slow\n", encoding="utf-8")
        return {**self.env, "PYTHONPATH": str(directory)}

    @unittest.skipIf(os.name == "nt", "SIGTERM kończy proces na Windows bez obsługi")
    def test_a_slow_resolver_does_not_delay_the_stop_with_two_ports(self) -> None:
        self.env = self.slow_resolver_env()
        read_port, approve_port = two_ports()
        proc = self.serve("--read-port", str(read_port), "--approve-port", str(approve_port))
        self.wait_up(read_port, approve_port)
        time.sleep(0.5)
        started = time.monotonic()
        proc.terminate()
        proc.communicate(timeout=20)
        self.assertLess(time.monotonic() - started, 5.0)

    @unittest.skipIf(os.name == "nt", "SIGTERM kończy proces na Windows bez obsługi")
    def test_a_slow_resolver_does_not_delay_the_stop_with_one_port_on_every_interface(self) -> None:
        self.env = self.slow_resolver_env()
        port, _unused = two_ports()
        proc = self.serve("--port", str(port), "--host", "0.0.0.0")
        self.wait_up(port)
        time.sleep(0.5)
        started = time.monotonic()
        proc.terminate()
        proc.communicate(timeout=20)
        self.assertLess(time.monotonic() - started, 5.0)

    def test_approve_bind_loopback_leaves_only_the_read_socket_in_the_network(self) -> None:
        address = lan_address()
        if address is None:
            self.skipTest("no non-loopback address on this machine; deliberately not in skip-baseline, so on CI runners (which have one) a skip is an error")
        read_port, approve_port = two_ports()
        self.serve("--read-port", str(read_port), "--approve-port", str(approve_port), "--approve-bind", "127.0.0.1")
        self.wait_up(read_port, approve_port)
        with socket.create_connection((address, read_port), timeout=2):
            pass
        with self.assertRaises(OSError):
            socket.create_connection((address, approve_port), timeout=2).close()

    def test_a_busy_second_port_leaves_nothing_behind(self) -> None:
        read_port, approve_port = two_ports()
        with socket.socket() as busy:
            if os.name != "nt":
                busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            busy.bind(("127.0.0.1", approve_port))
            busy.listen(1)
            answer = self.blocked("--read-port", str(read_port), "--approve-port", str(approve_port), "--host", "127.0.0.1")
            self.assertEqual(answer["status"], "BLOCKED")
        with socket.socket() as probe:   # the first socket was closed again
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", read_port))
        self.assertEqual(self.state_files(), [])

    def test_refused_flags_are_blocked_before_any_bind(self) -> None:
        port = free_port()
        for flags in (["--port", str(port), "--read-port", str(port + 1)], ["--read-port", str(port), "--approve-port", str(port)],
                      ["--approve-port", str(port), "--no-approvals"], ["--host", "127.0.0.1"], ["--approve-host", "tunnel.example"],
                      ["--port", str(port), "--read-bind", "127.0.0.1"], ["--read-bind", ""]):   # none of these binds anything, none may start a default server
            with self.subTest(flags=flags):
                self.assertEqual(self.blocked(*flags)["status"], "BLOCKED")

    def test_a_state_file_of_1_5_0_is_a_both_server(self) -> None:
        with mock.patch.dict(os.environ, home_env(self.tmp)):
            dashboard_token.write_state(self.root, 5155, "tok", "127.0.0.1")
            record = json.loads(dashboard_token.state_path(self.root, 5155).read_text())
            self.assertNotIn("role", record)
            self.assertNotIn("peer", record)
            found = dashboard_token.servers(self.root, healthy=lambda *a: True)
        self.assertEqual([item["role"] for item in found], ["both"])



class LanAddressChoice(unittest.TestCase):
    """The address of the LAN tests is a reachable one: with a VPN taking the default route the route source of the probe is not (it does not
    deliver a connection to itself), and the address of the host name is."""

    def test_a_candidate_that_is_not_reachable_is_passed_over(self):
        with mock.patch(__name__ + ".lan_candidates", return_value=["10.0.0.9", "192.168.1.6"]), \
                mock.patch(__name__ + ".reachable", side_effect=lambda ip: ip == "192.168.1.6"):
            self.assertEqual(lan_address(), "192.168.1.6")

    def test_no_reachable_candidate_gives_none(self):
        with mock.patch(__name__ + ".lan_candidates", return_value=["10.0.0.9"]), mock.patch(__name__ + ".reachable", return_value=False):
            self.assertIsNone(lan_address())
        with mock.patch(__name__ + ".lan_candidates", return_value=[]):
            self.assertIsNone(lan_address())

    def test_candidates_leave_out_loopback_link_local_and_repeats(self):
        with mock.patch("socket.gethostbyname_ex", return_value=("h", [], ["127.0.0.1", "169.254.3.4", "192.168.1.6", "192.168.1.6"])):
            found = lan_candidates()
        self.assertNotIn("127.0.0.1", found)
        self.assertNotIn("169.254.3.4", found)
        self.assertEqual(found.count("192.168.1.6"), 1)

    def test_loopback_is_reachable_and_a_closed_address_is_not(self):
        self.assertTrue(reachable("127.0.0.1"))
        self.assertFalse(reachable("192.0.2.1"))   # TEST-NET-1: nothing answers


if __name__ == "__main__":
    unittest.main()
