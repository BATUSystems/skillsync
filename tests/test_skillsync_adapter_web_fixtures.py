"""180-A3: the recorded streams of Vitest, Karma, Playwright and Cypress carry no path of a home or session directory, no user or machine name, no port, and fit the budget.

The patterns are generic (no literal name in this file); the name of the user and of the machine the test runs on are compared at run time (they are the names a
recording made on this machine would carry)."""
import getpass
import json
import os
import re
import socket
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures' / 'test-runs'
TOOLS = ('vitest', 'karma', 'playwright', 'cypress')
BUDGET = 300 * 1024
FILES = sorted(p for tool in TOOLS for p in FIXTURES.glob(f'{tool}-*'))
FORBIDDEN = (re.compile(r'/home/[^\s"\\]+'), re.compile(r'/Users/[^\s"\\]+'), re.compile(r'[A-Za-z]:\\Users\\'), re.compile(r'/tmp/(?!ROOT\b)[^\s"\\]+'),
             re.compile(r'/run/user/'), re.compile(r'\.cache/skillsync'), re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+'), re.compile(r'(?<!PORT)localhost:\d+'),
             re.compile(r'hostname="(?!HOST")'), re.compile(r'"hostname":\s*"(?!HOST")'), re.compile(r'\(node:\d+\)'))


class WebFixtures(unittest.TestCase):
    def test_the_fixtures_exist_for_every_tool(self):
        for tool in TOOLS:
            with self.subTest(tool=tool):
                self.assertGreaterEqual(len(list(FIXTURES.glob(f'{tool}-*.jsonl'))), 3)
                self.assertTrue(list(FIXTURES.glob(f'{tool}-*.xml')))

    def test_no_forbidden_pattern(self):
        for path in FILES:
            text = path.read_text(encoding='utf-8')
            for pattern in FORBIDDEN:
                with self.subTest(file=path.name, pattern=pattern.pattern):
                    self.assertIsNone(pattern.search(text), pattern.search(text) and pattern.search(text).group(0)[:60])

    def test_the_name_of_this_user_and_this_machine_do_not_occur(self):
        names = {name for name in (getpass.getuser(), socket.gethostname().split('.')[0], os.path.basename(os.path.expanduser('~'))) if name and len(name) > 3}
        for path in FILES:
            text = path.read_text(encoding='utf-8').lower()
            for name in names:
                with self.subTest(file=path.name):
                    self.assertNotIn(name.lower(), text)

    def test_every_recording_has_its_meta_and_chunks(self):
        for path in FIXTURES.glob('*.jsonl'):
            if not path.name.split('-')[0] in TOOLS:
                continue
            rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
            with self.subTest(file=path.name):
                meta = rows[0]['meta']
                for key in ('tool', 'version', 'command', 'exit', 'note', 'scrubbed', 'format'):
                    self.assertIn(key, meta)
                self.assertTrue(rows[1:] and all({'t', 'fd', 'data'} <= set(r) and r['fd'] in ('out', 'err') for r in rows[1:]))
                self.assertEqual([r['t'] for r in rows[1:]], sorted(r['t'] for r in rows[1:]))

    def test_the_total_size_fits_the_budget(self):
        self.assertLess(sum(p.stat().st_size for p in FILES), BUDGET)


if __name__ == '__main__':
    unittest.main()
