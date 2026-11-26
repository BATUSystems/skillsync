"""Scripts of the library write UTF-8 to stderr whatever the code page of a redirected stream says (Windows: cp1250, cp852).

A real child process with PYTHONIOENCODING forcing the code page; the test reads the raw bytes. Covers the Polish messages of the hook
`guard-commands.py` (the boundary of a sub-agent) and of `prototype/scripts/check_tokens.py`. The exit code and stdout stay as they were.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / 'library' / 'hooks' / 'guard-commands.py'
CHECK_TOKENS = ROOT / 'library' / 'skills' / 'prototype' / 'scripts' / 'check_tokens.py'
CHECK_TESTS = ROOT / 'library' / 'hooks' / 'check-tests.py'
CODE_PAGES = ('cp1250', 'cp852', 'utf-8')


class EncodingBase(unittest.TestCase):
    def assert_utf8(self, data, code_page, expected):
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError as error:
            self.fail('stderr is not UTF-8 under %s: %s %r' % (code_page, error, data[:160]))
        self.assertIn(expected, text)


class GuardCommandsEncodingTests(EncodingBase):
    def run_guard(self, code_page):
        tmp = tempfile.TemporaryDirectory(prefix='guard-enc-')
        self.addCleanup(tmp.cleanup)
        root = os.path.realpath(tmp.name)
        os.mkdir(os.path.join(root, '.git'))
        payload = {'tool_name': 'Bash', 'tool_input': {'command': 'git commit -m x'}, 'cwd': root, 'session_id': 's1',
                   'agent_id': 'a1', 'agent_type': 'implementer'}
        env = {**os.environ, 'HOME': root, 'PYTHONIOENCODING': code_page}
        return subprocess.run([sys.executable, str(GUARD), '--subagent-commit', 'deny'], input=json.dumps(payload).encode('utf-8'),
                              capture_output=True, cwd=root, env=env, timeout=30)

    def check(self, code_page):
        done = self.run_guard(code_page)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertEqual(done.stdout, b'')
        self.assert_utf8(done.stderr, code_page, 'podumysły nie commitują')

    def test_cp1250(self):
        self.check('cp1250')

    def test_cp852(self):
        self.check('cp852')

    def test_utf8(self):
        self.check('utf-8')


class CheckTokensEncodingTests(EncodingBase):
    def run_check(self, code_page):
        tmp = tempfile.TemporaryDirectory(prefix='tokens-enc-')
        self.addCleanup(tmp.cleanup)
        assets = os.path.join(os.path.realpath(tmp.name), 'assets')
        os.mkdir(assets)
        env = {**os.environ, 'PYTHONIOENCODING': code_page}
        return subprocess.run([sys.executable, str(CHECK_TOKENS), '--assets', assets], capture_output=True, env=env, timeout=60)

    def check(self, code_page):
        done = self.run_check(code_page)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assert_utf8(done.stderr, code_page, 'check_tokens: BŁĄD')

    def test_cp1250(self):
        self.check('cp1250')

    def test_cp852(self):
        self.check('cp852')

    def test_utf8(self):
        self.check('utf-8')

    def test_argparse_error_with_a_polish_message(self):
        tmp = tempfile.TemporaryDirectory(prefix='tokens-enc-')
        self.addCleanup(tmp.cleanup)
        missing = os.path.join(os.path.realpath(tmp.name), 'brak')
        env = {**os.environ, 'PYTHONIOENCODING': 'cp1250'}
        done = subprocess.run([sys.executable, str(CHECK_TOKENS), '--print-used', '--assets', missing], capture_output=True, env=env, timeout=60)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assert_utf8(done.stderr, 'cp1250', 'katalog zasobów nie istnieje')


class CheckTestsEncodingTests(EncodingBase):
    """CORE-1190-ENC-B: the Stop hook `check-tests.py` passes the tail of a failed test run on stderr; it is UTF-8 whatever the stream's code page."""

    def run_hook(self, code_page):
        tmp = tempfile.TemporaryDirectory(prefix='check-tests-enc-')
        self.addCleanup(tmp.cleanup)
        root = os.path.realpath(tmp.name)
        subprocess.run(['git', 'init', '-q'], cwd=root, check=True, capture_output=True)
        with open(os.path.join(root, 'changed.txt'), 'w', encoding='utf-8') as handle:   # a pending change: the hook runs the tests only then
            handle.write('x\n')
        script = os.path.join(root, 'fail.py')
        with open(script, 'w', encoding='utf-8') as handle:
            handle.write("import sys\nsys.stdout.buffer.write('Nie przechodzi: zażółć gęślą jaźń\\n'.encode('utf-8'))\nsys.exit(1)\n")
        env = {**os.environ, 'HOME': root, 'PYTHONIOENCODING': code_page, 'PYTHONUTF8': '1'}   # the child (and the decoding of its output) is UTF-8
        command = '"%s" "%s"' % (sys.executable, script)
        return subprocess.run([sys.executable, str(CHECK_TESTS), command], input=json.dumps({'cwd': root}).encode('utf-8'),
                              capture_output=True, cwd=root, env=env, timeout=60)

    def check(self, code_page):
        done = self.run_hook(code_page)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertEqual(done.stdout, b'')
        self.assert_utf8(done.stderr, code_page, 'zażółć gęślą jaźń')

    def test_cp1250(self):
        self.check('cp1250')

    def test_cp852(self):
        self.check('cp852')

    def test_utf8(self):
        self.check('utf-8')


if __name__ == '__main__':
    unittest.main()
