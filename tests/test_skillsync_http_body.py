"""Lingering close of the local HTTP servers: an error answered before the request body is read must reach the client
as a response, not as a connection reset (WinError 10054 on Windows). The mixin is exercised on a minimal server that
answers every POST with 403 without reading the body, which is exactly what the real handlers do on their early refusals."""
import http.server
import socket
import socketserver
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


def connect(port, timeout=5):
    """A client socket to 127.0.0.1:<port>, connected by the numeric address alone: no `getaddrinfo`, so no name resolution (on the
    macOS runner a resolver stall of about 35 s hit random tests); `create_connection` always goes through the resolver."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(('127.0.0.1', port))
    except BaseException:
        sock.close()
        raise
    return sock


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


class _QuickBindServer(http.server.ThreadingHTTPServer):
    def server_bind(self):
        # like the real servers: no `socket.getfqdn` of HTTPServer.server_bind (a reverse DNS lookup of 127.0.0.1 took about
        # 30 s on the macOS runner, once per server, which also made the old client-side time measurement meaningless)
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


class _Server:
    def __init__(self, handler):
        self.httpd = _QuickBindServer(('127.0.0.1', 0), handler)
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
    with connect(port) as sock:
        try:
            sock.sendall(head + body)
        except OSError:   # the server answered and closed before the whole body was sent (it is allowed to)
            pass
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
        data, reset = post_and_read(self.serve(_Lingering), body=BIG)   # over the handler's own read buffer on every Python
        self.assertFalse(reset)
        self.assertTrue(data.startswith(b'HTTP/1.1 403'), data[:60])
        self.assertTrue(data.endswith(b'{"error": "forbidden"}'), data[-40:])

    def test_a_chunked_body_is_drained_within_the_limit_too(self):
        chunked = b'%x\r\n' % len(BIG) + BIG + b'\r\n0\r\n\r\n'
        data, reset = post_and_read(self.serve(_Lingering), body=chunked, chunked=True)
        self.assertFalse(reset)
        self.assertTrue(data.startswith(b'HTTP/1.1 403'), data[:60])

    def hold_times(self, sender, limit=None, seconds=None, wait=12.0):
        """(Calls `linger_close` itself, not the mixin's `finish`: the wiring of the mixin is tested by `SpyLinger` and the keep-alive tests.)
        Run `sender(port)` against a refusing server whose `finish` times the lingering close; returns `(discarded, held)`
        measured ON THE SERVER SIDE (how long the handler thread stayed in the lingering close), whatever the client does."""
        record = []
        original = http_body.linger_close
        arguments = {} if limit is None else {'limit': limit, 'seconds': seconds}

        class Spy(http_body.LingeringCloseMixin, _Refusing):
            def finish(self):
                started = time.monotonic()
                discarded = original(self.connection, **arguments)
                record.append((discarded, time.monotonic() - started))
                _Refusing.finish(self)

        port = self.serve(Spy)
        thread = threading.Thread(target=sender, args=(port,), daemon=True)
        thread.start()
        deadline = time.monotonic() + wait
        while not record and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(record, 'the handler never reached the lingering close')
        return record[0]

    def test_a_body_over_the_limit_is_not_read_to_the_end_and_does_not_hold_the_server(self):
        def sender(port):
            post_and_read(port, body=b'a' * (512 * 1024), pause=0.1)   # the client may block in sendall: it is the server that is measured

        discarded, held = self.hold_times(sender, limit=1024, seconds=0.5)
        self.assertLessEqual(discarded, 1024, (discarded, held))   # it stopped at the limit it was given
        self.assertLess(held, 0.5 + 1.0, (discarded, held))        # and within the time it was given (plus scheduling slack)

    def test_a_client_that_keeps_sending_does_not_hold_the_thread_longer_than_the_limits(self):
        stop = threading.Event()

        def sender(port):
            head = (b'POST /x HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 100000000\r\n\r\n')
            try:
                with connect(port) as sock:
                    sock.sendall(head)
                    while not stop.is_set():   # never stops sending until the test is over
                        sock.sendall(b'a' * 4096)
                        time.sleep(0.001)
            except OSError:
                pass

        self.addCleanup(stop.set)
        discarded, held = self.hold_times(sender)   # the default limits: 256 KiB and one second
        self.assertLessEqual(discarded, http_body.LINGER_BYTES, (discarded, held))
        self.assertLess(held, http_body.LINGER_SECONDS + 1.0, (discarded, held))

    def test_a_client_that_dribbles_slowly_is_cut_off_by_the_time_limit_not_the_byte_limit(self):
        stop = threading.Event()

        def sender(port):
            head = (b'POST /x HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 100000000\r\n\r\n')
            try:
                with connect(port) as sock:
                    sock.sendall(head)
                    while not stop.is_set():   # about 2 KB/s: the byte limit (256 KiB) can never be what ends the lingering
                        sock.sendall(b'a' * 100)
                        time.sleep(0.05)
            except OSError:
                pass

        self.addCleanup(stop.set)
        discarded, held = self.hold_times(sender)
        self.assertLess(discarded, http_body.LINGER_BYTES // 8, (discarded, held))
        self.assertGreaterEqual(held, http_body.LINGER_SECONDS * 0.8, (discarded, held))   # it waited for the clock, not for the bytes
        self.assertLess(held, http_body.LINGER_SECONDS + 1.0, (discarded, held))

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

class _Handlers:
    """Minimal handlers that refuse or accept a POST, with and without reading the body."""

    class NotFoundUnread(http_body.LingeringCloseMixin, http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        timeout = 5

        def do_POST(self):   # refuses WITHOUT close_connection and without reading: the old keep-alive desync
            payload = b'{"error": "not_found"}'
            self.send_response(404)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST

        def log_message(self, *args):
            pass

    class ReadsBody(NotFoundUnread):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length') or 0))
            payload = b'{"ok": true}'
            self.send_response(200)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)


def keep_alive_exchange(port, body, second=True, path='/x'):
    """POST `body` on a keep-alive connection, read one response, then see what the server does with the connection."""
    head = (b'POST ' + path.encode() + b' HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n')
    with connect(port) as sock:
        sock.sendall(head + body)
        data = b''
        while b'\r\n\r\n' not in data:
            data += sock.recv(65536)
        header, _, rest = data.partition(b'\r\n\r\n')
        length = int(next(line.split(b':')[1] for line in header.split(b'\r\n') if line.lower().startswith(b'content-length')))
        while len(rest) < length:
            rest += sock.recv(65536)
        time.sleep(0.2)
        if second:
            sock.sendall(b'GET /y HTTP/1.1\r\nHost: x\r\n\r\n')
        sock.settimeout(2)
        try:
            extra = sock.recv(65536)
        except socket.timeout:
            return header, 'open'
        except ConnectionResetError:
            return header, 'closed'
        return header, ('closed' if not extra else 'answered:' + extra[:20].decode('latin-1'))


class UnreadBodyClosesTheConnection(unittest.TestCase):
    def serve(self, handler):
        server = _Server(handler)
        self.addCleanup(server.close)
        return server.port

    def test_an_unread_body_is_not_parsed_as_the_next_request_on_a_kept_alive_connection(self):
        header, after = keep_alive_exchange(self.serve(_Handlers.NotFoundUnread), BIG[:100 * 1024])
        self.assertTrue(header.startswith(b'HTTP/1.1 404'), header[:30])
        self.assertEqual(after, 'closed')   # not 'answered:' (the body bytes taken for a request) and not 'open'

    def test_a_body_the_handler_read_keeps_the_connection_and_does_not_linger(self):
        spy = []
        original = http_body.linger_close
        with mock.patch.object(http_body, 'linger_close', lambda *a, **k: spy.append(1) or original(*a, **k)):
            header, after = keep_alive_exchange(self.serve(_Handlers.ReadsBody), b'{"a": 1}')
        self.assertTrue(header.startswith(b'HTTP/1.1 200'), header[:30])
        self.assertTrue(after.startswith('answered:HTTP/1.1'), after)   # the connection stayed for the second request
        self.assertEqual(spy, [])

    def test_a_get_that_declares_a_body_is_closed_too(self):
        port = self.serve(_Handlers.NotFoundUnread)
        head = b'GET /x HTTP/1.1\r\nHost: x\r\nContent-Length: 5000\r\n\r\n'
        with connect(port) as sock:
            sock.sendall(head + b'a' * 5000)
            data = b''
            sock.settimeout(3)
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        self.assertTrue(data.startswith(b'HTTP/1.1 404'), data[:30])   # the server closed it: recv returned the EOF



def raw_post(port, path, body, headers=b''):
    """POST `body` whole to a real handler without any cookie or header the handler wants; returns the raw response."""
    head = (b'POST ' + path.encode() + b' HTTP/1.1\r\nHost: 127.0.0.1:' + str(port).encode() + b'\r\nContent-Type: application/json\r\n'
            + headers + b'Content-Length: ' + str(len(body)).encode() + b'\r\n\r\n')
    with connect(port) as sock:
        try:
            sock.sendall(head + body)
        except OSError:   # a refusal may close before the whole body is sent
            pass
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


class PrototypeServerOtherRefusals(prototype_tests.ServerCase):
    def test_chunked_body_is_refused_and_drained_on_the_real_server(self):   # no session: the refusal is 401, before the 411
        spy = SpyLinger(self)
        head = (b'POST /_api/v1/prototypes/demo/operations HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n'
                b'Transfer-Encoding: chunked\r\n\r\n')
        chunked = b'%x\r\n' % len(BODY) + BODY + b'\r\n0\r\n\r\n'
        with connect(self.server.port) as sock:
            sock.sendall(head + chunked)
            data = b''
            while True:
                try:
                    chunk = sock.recv(65536)
                except ConnectionResetError:
                    break
                if not chunk:
                    break
                data += chunk
        self.assertTrue(data.startswith(b'HTTP/1.'), data[:40])
        self.assertIsNotNone(spy.wait())

    def test_an_unknown_endpoint_with_a_body_is_404_and_closed_not_kept_alive(self):
        header, after = keep_alive_exchange(self.server.port, BIG[:100 * 1024], second=True, path='/_api/v1/nope')
        self.assertTrue(header.startswith(b'HTTP/1.'), header[:30])
        self.assertEqual(after, 'closed')


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

    def test_an_unknown_route_and_a_bad_length_with_a_body_are_answered_and_drained_too(self):
        spy = SpyLinger(self)
        data = raw_post(self.port, '/nie-ma', BIG)
        self.assertIn(b' 404 ', data.split(b'\r\n', 1)[0])
        self.assertGreaterEqual(spy.wait() or 0, 32 * 1024)


if __name__ == '__main__':
    unittest.main()
