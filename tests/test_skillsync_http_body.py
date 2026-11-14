"""Lingering close of the local HTTP servers: an error answered before the request body is read must reach the client
as a response, not as a connection reset (WinError 10054 on Windows). The mixin is exercised on a minimal server that
answers every POST with 403 without reading the body, which is exactly what the real handlers do on their early refusals."""
import http.server
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillsync_workflow import prototype_server as http_body  # noqa: E402
import test_skillsync_dashboard_server as dashboard_tests  # noqa: E402
import test_skillsync_prototype_server as prototype_tests  # noqa: E402

BODY = b'{"x": "' + b'a' * (60 * 1024) + b'"}'
BIG = b'{"pad": "' + b'a' * (240 * 1024) + b'"}'   # over the handler's own read buffer, under the lingering limit


class _Refusing(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    timeout = 5

    def do_POST(self):
        self.close_connection = True     # like the real handlers: refuse first, never read the body
        payload = b'{"error": "forbidden"}'
        self.send_response(403)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class _Lingering(http_body.LingeringCloseMixin, _Refusing):
    pass


class _Server:
    def __init__(self, handler):
        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def post_and_read(port, body=BODY, pause=0.3, chunked=False):
    """Send a POST whole (the server never reads it), wait, then read the response; returns the raw bytes read."""
    head = (b'POST /x HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n'
            + (b'Transfer-Encoding: chunked\r\n' if chunked else b'Content-Length: ' + str(len(body)).encode() + b'\r\n')
            + b'\r\n')
    with socket.create_connection(('127.0.0.1', port), timeout=5) as sock:
        sock.sendall(head + body)
        time.sleep(pause)   # the server has answered and (without the cure) closed with the body unread
        data = b''
        while True:
            try:
                chunk = sock.recv(65536)
            except ConnectionResetError:
                return data, True
            if not chunk:
                return data, False
            data += chunk


class LingeringClose(unittest.TestCase):
    def serve(self, handler):
        server = _Server(handler)
        self.addCleanup(server.close)
        return server.port

    def test_the_cure_delivers_the_refusal_after_a_whole_body_was_sent(self):
        data, reset = post_and_read(self.serve(_Lingering))
        self.assertFalse(reset)
        self.assertTrue(data.startswith(b'HTTP/1.1 403'), data[:60])
        self.assertTrue(data.endswith(b'{"error": "forbidden"}'), data[-40:])

    def test_a_chunked_body_is_drained_within_the_limit_too(self):
        chunked = b'%x\r\n' % len(BODY) + BODY + b'\r\n0\r\n\r\n'
        data, reset = post_and_read(self.serve(_Lingering), body=chunked, chunked=True)
        self.assertFalse(reset)
        self.assertTrue(data.startswith(b'HTTP/1.1 403'), data[:60])

    def test_a_body_over_the_limit_is_not_read_to_the_end_and_does_not_hold_the_server(self):
        started = time.monotonic()
        discarded = []
        original = http_body.linger_close

        class Spy(http_body.LingeringCloseMixin, _Refusing):
            def finish(self):
                discarded.append(original(self.connection, limit=1024, seconds=0.5))
                _Refusing.finish(self)

        port = self.serve(Spy)
        post_and_read(port, body=b'a' * (512 * 1024), pause=0.1)
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(discarded and discarded[0] <= 1024, discarded)

    def test_linger_close_is_bounded_by_bytes_and_time_and_never_raises(self):
        server, client = socket.socketpair()
        self.addCleanup(server.close)
        self.addCleanup(client.close)
        client.sendall(b'x' * 5000)
        started = time.monotonic()
        self.assertEqual(http_body.linger_close(server, limit=1000, seconds=0.3), 1000)
        client.sendall(b'y' * 100)
        silent_a, silent_b = socket.socketpair()
        self.addCleanup(silent_a.close)
        self.addCleanup(silent_b.close)
        self.assertEqual(http_body.linger_close(silent_a, limit=1000, seconds=0.3), 0)   # a silent client: the time limit
        self.assertLess(time.monotonic() - started, 3)
        gone = socket.socket()
        gone.close()
        self.assertEqual(http_body.linger_close(gone), 0)   # a connection that is already closed

    def test_only_requests_that_declare_a_body_linger(self):
        class Headers(dict):
            get = dict.get
        self.assertFalse(http_body.declares_body('GET', Headers({'Content-Length': '5'})))
        self.assertFalse(http_body.declares_body('POST', Headers()))
        self.assertFalse(http_body.declares_body('POST', Headers({'Content-Length': '0'})))
        self.assertTrue(http_body.declares_body('POST', Headers({'Content-Length': '5'})))
        self.assertTrue(http_body.declares_body('POST', Headers({'Transfer-Encoding': 'chunked'})))
        self.assertTrue(http_body.declares_body('POST', Headers({'Content-Length': 'abc'})))


def raw_post(port, path, body, headers=b''):
    """POST `body` whole to a real handler without any cookie or header the handler wants; returns the raw response."""
    head = (b'POST ' + path.encode() + b' HTTP/1.1\r\nHost: 127.0.0.1:' + str(port).encode() + b'\r\nContent-Type: application/json\r\n'
            + headers + b'Content-Length: ' + str(len(body)).encode() + b'\r\n\r\n')
    with socket.create_connection(('127.0.0.1', port), timeout=5) as sock:
        sock.sendall(head + body)
        data = b''
        while True:
            try:
                chunk = sock.recv(65536)
            except ConnectionResetError:
                return data
            if not chunk:
                return data
            data += chunk


class SpyLinger:
    """Records how many bytes the lingering close discarded; the real function still runs."""

    def __init__(self, test):
        self.calls = []
        original = http_body.linger_close

        def spy(connection, *args, **kwargs):
            discarded = original(connection, *args, **kwargs)
            self.calls.append(discarded)
            return discarded

        patcher = mock.patch.object(http_body, 'linger_close', spy)
        patcher.start()
        test.addCleanup(patcher.stop)

    def wait(self, seconds=3.0):
        deadline = time.monotonic() + seconds
        while not self.calls and time.monotonic() < deadline:
            time.sleep(0.02)
        return self.calls[0] if self.calls else None


class PrototypeServerLingers(prototype_tests.ServerCase):
    """The refusals of `_post_checks` (here 401: no session) come before the body is read; the body is then drained."""

    def test_a_refused_post_with_a_big_body_is_drained_not_reset(self):
        spy = SpyLinger(self)
        body = BIG
        data = raw_post(self.server.port, '/_api/v1/prototypes/demo/operations', body)
        self.assertTrue(data.startswith(b'HTTP/1.'), data[:40])
        self.assertIn(b' 401 ', data.split(b'\r\n', 1)[0])
        discarded = spy.wait()
        self.assertIsNotNone(discarded, 'the connection was closed without the lingering close')
        self.assertGreaterEqual(discarded, 32 * 1024)   # what the handler's own read buffer (8 KiB, 128 KiB on newer Pythons) did not take

    def test_a_refused_post_over_the_body_limit_is_413_and_still_drained(self):
        spy = SpyLinger(self)
        login = self.client(prototype_tests.A)
        response = login.post('/_api/v1/prototypes/demo/operations', {'pad': 'a' * (http_body.MAX_BODY + 4096)})
        self.assertEqual(response.status, 413)
        self.assertIsNotNone(spy.wait())


class DashboardServerLingers(dashboard_tests.HttpCase):
    def test_a_refused_approval_with_a_big_body_is_drained_not_reset(self):
        spy = SpyLinger(self)
        body = BIG
        data = raw_post(self.port, '/api/approve', body)
        self.assertTrue(data.startswith(b'HTTP/1.'), data[:40])
        self.assertIn(b' 403 ', data.split(b'\r\n', 1)[0])
        discarded = spy.wait()
        self.assertIsNotNone(discarded, 'the connection was closed without the lingering close')
        self.assertGreaterEqual(discarded, 32 * 1024)


if __name__ == '__main__':
    unittest.main()
