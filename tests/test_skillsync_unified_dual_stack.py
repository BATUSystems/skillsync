"""CORE-2000 U-8: a server listening on `::` also answers on 127.0.0.1 (Windows binds IPv6 only by default, so the printed local address
refused every connection and `serve --detach` timed out)."""
from __future__ import annotations

import socket
import sys
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from skillsync_workflow import dashboard_http as dh  # noqa: E402
from skillsync_workflow import prototype_server as ps  # noqa: E402


def has_ipv6() -> bool:
    try:
        with socket.socket(socket.AF_INET6) as probe:
            probe.bind(("::", 0))
        return hasattr(socket, "IPV6_V6ONLY")
    except OSError:
        return False


@unittest.skipUnless(has_ipv6(), "ta maszyna nie ma IPv6")
class DualStackTests(unittest.TestCase):
    def servers(self, address: str):
        app = SimpleNamespace(close_link=lambda: None)
        yield "page", lambda: dh.Server((address, 0), BaseHTTPRequestHandler)
        yield "prototypes", lambda: ps.PrototypeHTTPServer((address, 0), BaseHTTPRequestHandler, app)
        yield "prototype origin", lambda: dh.PrototypeOriginServer((address, 0), BaseHTTPRequestHandler, app)

    def test_the_wildcard_socket_asks_for_both_protocols(self) -> None:
        for name, make in self.servers("::"):
            with self.subTest(server=name):
                asked = []
                real = socket.socket.setsockopt

                def spy(sock, *args):
                    asked.append(args)
                    return real(sock, *args)

                with mock.patch.object(socket.socket, "setsockopt", spy):
                    server = make()
                try:
                    self.assertIn((socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0), asked)
                    port = server.server_address[1]
                    for target in ("127.0.0.1", "::1"):
                        with socket.create_connection((target, port), timeout=5):
                            pass
                finally:
                    server.server_close()

    def test_one_ipv6_address_is_left_alone(self) -> None:
        for name, make in self.servers("::1"):
            with self.subTest(server=name):
                asked = []
                real = socket.socket.setsockopt

                def spy(sock, *args):
                    asked.append(args)
                    return real(sock, *args)

                with mock.patch.object(socket.socket, "setsockopt", spy):
                    server = make()
                try:
                    self.assertNotIn((socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0), asked)
                finally:
                    server.server_close()


if __name__ == "__main__":
    unittest.main()
