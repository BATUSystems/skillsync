"""180-P: the command `prototype serve --demo` (flag, start without a users file, listening address, startup message)."""
import http.client
import os
import re
import subprocess
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_skillsync_prototype import CLI, PrototypeFixture  # noqa: E402


class ServeDemoCommandTests(PrototypeFixture):
    def setUp(self):
        super().setUp()
        self.init('demo')

    def start(self, *args):
        argv = [sys.executable, str(CLI), 'prototype', 'serve', str(self.root), '--port', '0', *args]
        environ = {key: value for key, value in os.environ.items()}
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=self.base, env=environ)
        self.addCleanup(self.stop, process)
        return process

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=20)
        for stream in (process.stdout, process.stderr):
            if stream:
                stream.close()

    def read_start(self, process, stop_after='Ctrl+C'):
        lines, box = [], {}

        def reader():
            for line in process.stderr:
                lines.append(line.rstrip('\n'))
                if stop_after in line and 'Serwer prototypów' in line:
                    box['url'] = re.search(r'http://[^\s]+', line).group(0)
                    break
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        thread.join(30)
        return box.get('url'), lines

    def test_starts_without_a_users_file_and_serves_read_only(self):
        self.assertFalse(os.path.exists(self.root / '.skillsync' / 'prototype' / 'users.json'))
        process = self.start('--demo', '--host', '127.0.0.1')
        url, lines = self.read_start(process)
        self.assertIsNotNone(url, lines)
        port = int(url.rsplit(':', 1)[1].strip('/'))
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
        connection.request('GET', '/demo/index.html')
        self.assertEqual(connection.getresponse().status, 200)
        connection.close()
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
        connection.request('POST', '/_api/v1/prototypes/demo/operations', b'{}')
        self.assertEqual(connection.getresponse().status, 405)
        connection.close()

    def test_the_startup_message_says_the_pages_are_public_and_read_only(self):
        process = self.start('--demo', '--host', '127.0.0.1')
        url, lines = self.read_start(process)
        self.assertIsNotNone(url, lines)
        self.read_more(process, lines, marker='POKAZ tylko do odczytu')
        text = '\n'.join(lines)
        process.terminate()
        self.assertIn('POKAZ tylko do odczytu', text)
        self.assertNotIn('przywracanie z UI', text)
        self.assertIn('jawne dla każdego', text)
        self.assertNotIn('Adresy w sieci lokalnej', text, 'a narrowed listening has no list of network addresses')

    def test_the_default_listens_on_every_interface_and_prints_the_addresses_and_the_way_to_narrow(self):
        process = self.start('--demo')
        url, lines = self.read_start(process, stop_after='Ctrl+C')
        self.assertIsNotNone(url, lines)
        self.read_more(process, lines)
        text = '\n'.join(lines)
        self.assertIn('Adresy w sieci lokalnej: http://', text)
        self.assertIn('--host 127.0.0.1', text)
        self.assertIn('0.0.0.0', url)

    def read_more(self, process, lines, marker='--host 127.0.0.1'):
        done = threading.Event()

        def reader():
            for line in process.stderr:
                lines.append(line.rstrip('\n'))
                if marker in line:
                    break
            done.set()
        threading.Thread(target=reader, daemon=True).start()
        done.wait(20)

    def test_demo_with_login_to_view_is_blocked(self):
        result = subprocess.run([sys.executable, str(CLI), 'prototype', 'serve', str(self.root), '--demo', '--login-to-view', '--port', '0',
                                 '--json'], capture_output=True, text=True, timeout=60, cwd=self.base)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('BLOCKED', result.stdout)

    def test_without_the_flag_the_missing_users_file_still_blocks(self):
        result = subprocess.run([sys.executable, str(CLI), 'prototype', 'serve', str(self.root), '--port', '0', '--json'],
                                capture_output=True, text=True, timeout=60, cwd=self.base)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('BLOCKED', result.stdout)


if __name__ == '__main__':
    unittest.main()
