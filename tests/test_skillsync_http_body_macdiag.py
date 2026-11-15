"""DEBUG (not to be merged): phase timings of the lingering-close scenario that took 35 s on the macOS runner."""
import faulthandler
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillsync_workflow import prototype_server as ps  # noqa: E402
import test_skillsync_http_body as T  # noqa: E402


def say(*args):
    print('MACDIAG', *args, file=sys.stderr, flush=True)


class MacDiag(unittest.TestCase):
    def test_phases_of_the_old_scenario(self):
        faulthandler.dump_traceback_later(25, repeat=False, file=sys.stderr)
        self.addCleanup(faulthandler.cancel_dump_traceback_later)
        t0 = time.monotonic()
        now = lambda: round(time.monotonic() - t0, 3)  # noqa: E731
        say('start', sys.platform, sys.version.split()[0])
        a = now(); socket.getfqdn('127.0.0.1'); say('socket.getfqdn(127.0.0.1) took', round(now() - a, 3))
        server_side = []
        original = ps.linger_close

        class Spy(ps.LingeringCloseMixin, T._Refusing):
            def handle(self):
                a = now()
                super().handle()
                server_side.append(('handle() (request until before finish)', a, now()))

            def finish(self):
                a = now()
                n = original(self.connection, limit=1024, seconds=0.5)
                server_side.append(('linger_close', a, now(), n))
                T._Refusing.finish(self)
                server_side.append(('finish done', now()))

        a = now(); srv = T._Server(Spy); self.addCleanup(srv.close); say('T._Server(...) start took', round(now() - a, 3))
        body = b'a' * (512 * 1024)
        head = b'POST /x HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n'
        a = now()
        sock = socket.create_connection(('127.0.0.1', srv.port), timeout=5)
        say('connect took', round(now() - a, 3))
        self.addCleanup(sock.close)
        a = now(); sent = 0
        try:
            view = memoryview(head + body)
            sock.settimeout(1.0)
            while sent < len(view):
                try:
                    sent += sock.send(view[sent:sent + 65536])
                except socket.timeout:
                    say('send timeout at', sent, 'bytes after', round(now() - a, 3)); break
        except OSError as error:
            say('send error', type(error).__name__, error, 'at', sent, 'bytes after', round(now() - a, 3))
        say('send phase ended: sent', sent, 'of', len(head) + len(body), 'in', round(now() - a, 3))
        time.sleep(0.1)
        a = now(); data = b''; sock.settimeout(5)
        while True:
            try:
                chunk = sock.recv(65536)
            except OSError as error:
                say('recv error', type(error).__name__, 'after', round(now() - a, 3), 'with', len(data), 'bytes'); break
            if not chunk:
                say('recv EOF after', round(now() - a, 3), 'with', len(data), 'bytes'); break
            data += chunk
        time.sleep(0.5)
        for row in server_side:
            say('server', row)
        say('total', now())
