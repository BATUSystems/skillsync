"""CORE-2000-UNIFIED, finding A2: a server whose page listens on an address that is not the loopback (`--read-bind`, `--host`, `--approve-bind`)
listens on 127.0.0.1 as well, so signing in and consenting on this machine work; the start prints both addresses:
python3 -m unittest tests.test_skillsync_unified_loopback

Real `skillsync dashboard serve` processes (`unified_helpers`). The address is the LAN address of this machine when there is one the machine can
reach itself, else the stand-in `127.0.0.2`: another address of the loopback network, which the server does not accept as a local sign-in (only
127.0.0.1 and ::1 are), so it behaves as a LAN address in everything but the network. The real LAN address and the stand-in are both exercised
when both work."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unified_helpers import (  # noqa: E402
    APPROVER, APPROVER_ACCOUNT, APPROVER_PASSWORD, LAYER, PAGE_LAUNCHER, ROOT, SPEC2, Browser, UnifiedCase, http_request,
    lan_address, port_pool, self_reachable, write)
from layers_helpers import write_transitional_config  # noqa: E402
from test_skillsync_unified_origins import bindable, hold  # noqa: E402

from skillsync_workflow import dashboard_ports  # noqa: E402

STAND_IN = "127.0.0.2"
MODES = ("page", "port", "two")


def addresses() -> list:
    found = [lan_address(), STAND_IN if self_reachable(STAND_IN) else None]
    return [item for item in found if item]


def free_on_both(address: str, offsets=(0,), skip=()) -> int:
    """A port of the test pool that (with `offsets`) is free on `address` and on 127.0.0.1."""
    for port in port_pool():
        if port not in skip and all(port + step in port_pool() and bindable(port + step, host)
                                    for step in offsets for host in (address, "127.0.0.1")):
            return port
    raise RuntimeError("no port of the test pool is free on both addresses")


class CompanionRuleTests(unittest.TestCase):
    def test_only_an_address_without_a_local_sign_in_gets_a_loopback_socket(self) -> None:
        for bound in ("", "0.0.0.0", "::", "127.0.0.1", "::1", "localhost"):
            self.assertIsNone(dashboard_ports.loopback_companion(bound), bound)
        for bound in ("192.0.2.10", "10.8.0.3", STAND_IN, "fe80::1", "2001:db8::7", "fd00::2", "a-host-name"):
            self.assertEqual(dashboard_ports.loopback_companion(bound), "127.0.0.1", bound)


@unittest.skipUnless(addresses(), "no address of this machine besides 127.0.0.1 can be bound and reached (no LAN, no 127.0.0.2)")
class LanBindTests(UnifiedCase):
    ACCOUNTS = ((APPROVER, "approver", APPROVER_PASSWORD, APPROVER_ACCOUNT),)

    def start(self, address: str, mode: str, *extra: str):
        port = free_on_both(address, (0, 2) if mode != "two" else (0, 1, 2, 3))
        listed = ("--read-host", address) if mode == "port" else ()   # `--port N` answers only under the loopback and the listed names
        server = self.serve(*listed, *extra, mode=mode, address=address, port=port)
        self.addCleanup(server.stop)
        return server

    @staticmethod
    def printed(text: str) -> list:
        return [(host, int(port)) for host, port in re.findall(r"http://([0-9.]+):([0-9]+)/", text)]

    def server_id(self, address: str, port: int) -> str:
        reply = http_request(address, port, "GET", "/healthz")
        self.assertEqual(reply.status, 200, (address, port, reply.text))
        return reply.data["serverId"]

    def test_the_page_answers_and_signs_in_on_both_addresses_and_consents_work_locally(self) -> None:
        self.spec_waits_for_go_plan()
        for address in addresses():
            for mode in MODES:
                with self.subTest(address=address, mode=mode):
                    server = self.start(address, mode)
                    local = Browser(server.port)
                    self.assertEqual(self.server_id("127.0.0.1", server.port), self.server_id(address, server.port))
                    reply = local.login(APPROVER, APPROVER_PASSWORD)
                    self.assertEqual(reply.status, 200, reply.text)
                    remote = Browser(server.port, address=address)
                    refused = remote.login(APPROVER, APPROVER_PASSWORD)
                    self.assertEqual((refused.status, refused.data.get("error")), (403, "secure_connection_required"),
                                     "control: no password over plain HTTP on the other address")
                    seen = self.printed(server.log())
                    self.assertIn(("127.0.0.1", server.port), seen, server.log())
                    self.assertIn((address, server.port), seen, server.log())
                    for host, port in set(seen):
                        self.assertEqual(http_request(host, port, "GET", "/healthz").status in (200, 404), True, (host, port))
                    server.stop()
                    self.assertTrue(bindable(server.port) and bindable(server.port, address), "both sockets are closed")

    def test_a_consent_the_prototype_origin_and_the_handover_work_on_the_loopback(self) -> None:
        address = addresses()[0]
        for mode in MODES:
            with self.subTest(mode=mode):
                self.project = self.tmp / f"proj-{mode}"   # a fresh project: `GO plan` waits for each mode
                self.build_project(self.project, "direct")
                self.install_accounts(self.project)
                self.spec_waits_for_go_plan()
                write(self.project / "docs" / "prototypes" / "demo" / "prototype.json", json.dumps({"slug": "demo", "title": "Demo"}))
                write(self.project / "docs" / "prototypes" / "demo" / "index.html", '<html data-prototype-id="demo"></html>')
                server = self.start(address, mode)
                local = Browser(server.port)
                self.assertEqual(local.login(APPROVER, APPROVER_PASSWORD).status, 200)
                found = local.status()["prototype"]
                self.assertTrue(found["url"].startswith("http://127.0.0.1:"), found)
                proto_port = int(found["url"].rstrip("/").rsplit(":", 1)[1])
                for host in ("127.0.0.1", address):
                    self.assertEqual(http_request(host, proto_port, "GET", "/demo/index.html").status, 200, host)
                begun = http_request("127.0.0.1", proto_port, "GET", "/_handoff/begin")
                self.assertEqual(begun.status, 303, begun.text)
                challenge = re.search(r"challenge=([0-9a-f]{64})", begun.header("Location")).group(1)
                self.assertEqual(local.post("/_api/v1/handoff", {"challenge": challenge}).status, 200)
                offer = local.offer("spec")
                done = local.post("/api/approve", {"kind": "spec", "path": SPEC2, "sha": offer["sha"]})
                self.assertEqual(done.status, 200, done.text)
                server.stop()
                self.assertTrue(bindable(proto_port) and bindable(proto_port, address), "both sockets of the prototypes are closed")

    def test_a_login_link_is_issued_and_followed_on_the_loopback(self) -> None:
        address = addresses()[0]
        self.assertEqual(self.cli_with_stdin("-".join(("Komentuje", "Link", "778899")), "prototype", "user", "add", str(self.project),
                                             "link@example.com", "--roles", "commenter", "--password-stdin", *LAYER).returncode, 0)
        server = self.start(address, "page")
        issued = self.cli("prototype", "login-link", str(self.project), "--user", "link@example.com", "--port", str(server.port), *LAYER)
        self.assertEqual(issued.returncode, 0, issued.stdout + issued.stderr)
        url = json.loads(issued.stdout)["url"]
        self.assertTrue(url.startswith(f"http://127.0.0.1:{server.port}/"), url)
        self.assertEqual(http_request("127.0.0.1", server.port, "GET", url[url.index("/_login/link"):]).status, 302)

    def test_dashboard_url_and_a_detached_start_name_an_address_that_listens_and_stop_closes_both(self) -> None:
        address = addresses()[0]
        port = free_on_both(address, (0, 2))
        # the machine config of the test: an attach takes no `--layer` (a flag of settings)
        write_transitional_config(Path(self.env["SKILLSYNC_CONFIG"]), ROOT / "library", legacy_key=False)
        subprocess.run(["git", "init", "-q"], cwd=str(self.project), check=True, env=self.env, timeout=30)   # a project: a second start attaches
        self.addCleanup(self.cli, "dashboard", "stop", str(self.project), "--force")
        started = self.cli("dashboard", "serve", str(self.project), "--read-bind", address, "--page-port", str(port), "--detach")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        url = started.stdout.strip().splitlines()[-1]
        self.assertTrue(url.startswith(f"http://127.0.0.1:{port}/"), url)
        self.assertIn(f"http://{address}:{port}/", started.stderr)
        self.server_id("127.0.0.1", port)
        asked = self.cli("dashboard", "url", str(self.project))
        self.assertEqual(asked.returncode, 0, asked.stdout + asked.stderr)
        self.assertTrue(asked.stdout.strip().splitlines()[-1].startswith(f"http://127.0.0.1:{port}/"), asked.stdout)
        # the same directory again: attaches to the running server (the launcher keeps a start that would not attach away from port 5140)
        launcher = write(self.tmp / "page_launcher.py", PAGE_LAUNCHER.format(root=str(ROOT)))
        attached = subprocess.run([sys.executable, str(launcher), str(free_on_both("127.0.0.1", (0, 2), skip=(port, port + 2))), "dashboard",
                                   "serve", str(self.project)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=40, env=self.env, cwd=str(self.tmp), stdin=subprocess.DEVNULL, check=False)
        self.assertEqual(attached.returncode, 0, attached.stdout + attached.stderr)
        self.assertIn(f"http://127.0.0.1:{port}/", attached.stdout)
        listed = self.cli("servers", "list", "--kind", "dashboard", "--json")
        self.assertEqual(listed.returncode, 0, listed.stdout + listed.stderr)
        self.assertEqual(listed.stdout.count(f'"port": {port}'), 1, listed.stdout)
        stopped = self.cli("dashboard", "stop", str(self.project))
        self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
        for host in ("127.0.0.1", address):
            self.assertTrue(bindable(port, host) and bindable(port + 2, host), host)

    def test_a_loopback_port_held_by_another_program_blocks_the_start(self) -> None:
        address = addresses()[0]
        port = free_on_both(address, (0, 2))
        other = hold(port)
        self.addCleanup(other.close)
        done = self.cli("dashboard", "serve", str(self.project), "--read-bind", address, "--page-port", str(port), *LAYER, timeout=40)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("127.0.0.1", json.loads(done.stdout)["reason"])

    def test_control_a_wildcard_and_a_loopback_bind_open_one_page_socket(self) -> None:
        for bind in ("127.0.0.1", "0.0.0.0"):
            with self.subTest(bind=bind):
                port = free_on_both("127.0.0.1", (0, 2))
                self.addCleanup(self.cli, "dashboard", "stop", str(self.project), "--force")
                started = self.cli("dashboard", "serve", str(self.project), "--read-bind", bind, "--page-port", str(port), "--detach", *LAYER)
                self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
                self.assertNotIn("także na 127.0.0.1", started.stderr)
                self.server_id("127.0.0.1", port)
                self.assertEqual(self.cli("dashboard", "stop", str(self.project)).returncode, 0)


if __name__ == "__main__":
    unittest.main()
