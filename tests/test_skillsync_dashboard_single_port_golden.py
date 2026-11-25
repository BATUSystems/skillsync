"""K7 (PORTS-160-C): the single-port mode (`serve --port`, no new flags) stays byte for byte what 1.5.0 was.

`tests/fixtures/dashboard_ports/single-port-v1.5.0.json` was written from the UNCHANGED code of v1.5.0 (1b6cc95) by
`SKILLSYNC_GOLDEN_WRITE=1 python3 -m unittest tests.test_skillsync_dashboard_single_port_golden`; this test compares the answers of the server
(refusals of a write for a matrix of `Host`/header sets, the `approvals` flags of `/api/status`, `/healthz`, the effective listen address
of the command) with that record. A change of the single-port behaviour means a new record AND a deliberate entry in the CHANGELOG."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from skillsync_workflow import dashboard_http as srv  # noqa: E402
from tests.test_skillsync_dashboard_server import (ASSETS, CLI, DESCRIPTIONS, TEST_TOKEN, ProjectCase, free_port,  # noqa: E402
                                                   raw_post)

GOLDEN = ROOT / "tests" / "fixtures" / "dashboard_ports" / "single-port-v1.5.0.json"
WRITE = os.environ.get("SKILLSYNC_GOLDEN_WRITE") == "1"
OK = {"Content-Type": "application/json", "X-Status-Board": "1", "X-Approve-Token": TEST_TOKEN}
HOSTS = ("127.0.0.1", "localhost", "[::1]", "phone.example", "viewer.example", "evil.example", "")
HEADER_SETS = {
    "ok": OK,
    "no-board-header": {k: v for k, v in OK.items() if k != "X-Status-Board"},
    "no-token": {k: v for k, v in OK.items() if k != "X-Approve-Token"},
    "wrong-token": {**OK, "X-Approve-Token": "x"},
    "wrong-type": {**OK, "Content-Type": "text/plain"},
    "origin-own": {**OK, "Origin": "http://localhost:{port}"},
    "origin-viewer": {**OK, "Origin": "http://viewer.example:{port}"},
    "origin-evil": {**OK, "Origin": "http://evil.example"},
    "origin-null": {**OK, "Origin": "null"},
    "forwarded": {**OK, "X-Forwarded-For": "10.0.0.9", "Forwarded": "for=10.0.0.9"},
}


def compute(port: int, board_hosts: dict) -> dict:
    out: dict = {"post": {}, "status": {}, "healthz": {}}
    for host in HOSTS:
        for name, headers in HEADER_SETS.items():
            headers = {**{k: v.format(port=port) for k, v in headers.items()}, "Content-Length": "2"}
            if host:
                headers["Host"] = host if host.startswith("[") or ":" in host else f"{host}:{port}"
            try:
                result = raw_post(port, headers, b"{}")
            except OSError as error:   # a refusal that closes the connection is part of the record
                result = ["os-error", type(error).__name__]
            out["post"][f"{host or '(none)'}|{name}"] = list(result) if not isinstance(result, list) else result
    for host in HOSTS:
        if not host:
            continue
        label = host
        req_host = host if host.startswith("[") else f"{host}:{port}"
        for route, key in (("/api/status", "status"), ("/healthz", "healthz")):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{route}", headers={"Host": req_host})
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    data = json.load(r)
                    if route == "/api/status":
                        approvals = data["approvals"]
                        data = {k: approvals.get(k) for k in ("enabled", "here", "viewer", "tokenRequired") if k in approvals}
                        data["approvalKeys"] = sorted(approvals)
                    else:
                        data = {"keys": sorted(data), "ok": data.get("ok"), "serverIdLen": len(data.get("serverId", ""))}
                    out[key][label] = [r.status, data]
            except urllib.error.HTTPError as exc:
                out[key][label] = [exc.code, json.load(exc)]
    return out


class SinglePortGolden(ProjectCase):
    def test_answers_match_the_record_of_v1_5_0(self) -> None:
        port = free_port()
        board = srv.Board(self.root, poll=0.2, heartbeat=0.5, prototype_port=1, assets=ASSETS, descriptions=DESCRIPTIONS,
                          approve_token=TEST_TOKEN, approve_hosts=("phone.example",), read_hosts=("viewer.example",))
        server = srv.make_server(board, "127.0.0.1", port)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        try:
            got = compute(port, {})
        finally:
            board.stop.set()
            server.shutdown()
            server.server_close()
        got = json.loads(json.dumps(got))
        if WRITE:
            GOLDEN.write_text(json.dumps(got, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
            self.skipTest("record written")
        self.assertEqual(got, json.loads(GOLDEN.read_text(encoding="utf-8")))


class SinglePortListensOnLoopbackOnly(ProjectCase):
    """The command without `--host` listens on 127.0.0.1 only (the default of `--port` mode never changes): a connection to another
    address of this machine is refused."""

    def test_default_bind_is_loopback(self) -> None:
        probe = None
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                udp.connect(("192.0.2.1", 9))
                probe = udp.getsockname()[0]
        except OSError:
            pass
        if not probe or probe.startswith("127."):
            self.skipTest("no non-loopback address on this machine")
        manifest = self.root / srv.MANIFEST
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("skills: [dashboard]\n", encoding="utf-8")
        port = free_port()
        env = {**os.environ, "SKILLSYNC_CONFIG": str(self.tmp / "machine-config.yaml"), "XDG_CACHE_HOME": str(self.tmp / "xdg-cache"), "XDG_DATA_HOME": str(self.tmp / "xdg"), "LOCALAPPDATA": str(self.tmp / "xdg"), "HOME": str(self.tmp / "home"),
               "USERPROFILE": str(self.tmp / "home")}
        proc = subprocess.Popen([sys.executable, str(CLI), "dashboard", "serve", str(self.root), "--port", str(port),
                                 "--layer", f"core={ROOT / 'library'}"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            deadline = time.time() + 20
            up = False
            while time.time() < deadline and not up:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                        up = True
                except OSError:
                    time.sleep(0.2)
            self.assertTrue(up, "the server did not start")
            with self.assertRaises(OSError):
                socket.create_connection((probe, port), timeout=1).close()
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            for stream in (proc.stdout, proc.stderr):
                if stream:
                    stream.close()


if __name__ == "__main__":
    unittest.main()
