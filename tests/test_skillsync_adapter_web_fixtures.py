"""180-A3: the recorded streams of Vitest, Karma, Playwright and Cypress (and, CORE-TAP, of Node `--test` as TAP and `spec`) carry no path of a home or session directory, no user or machine name, no port, and fit the budget.

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
# CORE-TAP: the prefixes of the Node fixtures. They are matched with `startswith(prefix + '-')`: `split('-')[0]` of `node-test-...` is `node` and would skip the
# files silently. `MIN_JSONL` is a count of the recordings only (no JUnit/XML requirement); `node-test` (the `spec` stream) is filled by a later step of the plan,
# which raises its minimum together with the fixtures.
NODE_PREFIXES = ('tap', 'node-test')
MIN_JSONL = {'tap': 11, 'node-test': 0}
SYNTHETIC = {'tap-bare-green.jsonl', 'tap-bare-not-ok-exit0.jsonl', 'tap-bail-out.jsonl'}   # hand written, marked `"synthetic": true` in the meta
BUDGET = 300 * 1024
FILES = sorted({p for tool in TOOLS for p in FIXTURES.glob(f'{tool}-*')} | {p for prefix in NODE_PREFIXES for p in FIXTURES.glob(f'{prefix}-*')})


def is_recording(name):
    """A chunk fixture of a guarded family: the first segment is one of `TOOLS`, or the name starts with a Node prefix followed by `-`."""
    return name.split('-')[0] in TOOLS or any(name.startswith(prefix + '-') for prefix in NODE_PREFIXES)
GENERIC = {'user', 'users', 'node', 'runner', 'localhost', 'root', 'desktop', 'mobile', 'admin', 'test', 'tests', 'build', 'host', 'linux', 'ubuntu', 'debian', 'github', 'docker', 'chrome',
           'cypress', 'karma', 'vitest', 'playwright'}   # a user or a host with such a name would match plain words of the recordings (`cypress_runner.js`, `Node 22`)
FORBIDDEN = (re.compile(r'/home/[^\s"\\]+'), re.compile(r'/Users/[^\s"\\]+'), re.compile(r'[A-Za-z]:\\{1,2}Users\\{1,2}'), re.compile(r'/tmp/(?!ROOT\b)[^\s"\\]+'),
             re.compile(r'/(?:root|var|opt|mnt|private|Volumes|srv|run/user)/'), re.compile(r'\.cache/skillsync'), re.compile(r'~/'), re.compile(r'file://(?!/NODE_MODULES)'),
             re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+'), re.compile(r'(?<!PORT)localhost:\d+'), re.compile(r'://[^/\s"]+:(?!PORT)\d+'), re.compile(r'(?<![\d.])(?!157\.0\.0\.0)(?!\d+\.0\.0\.0)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?![\d.])'),
             re.compile(r'\[::1\]'), re.compile(r'hostname="(?!HOST")'), re.compile(r'"hostname":\s*"(?!HOST")'), re.compile(r'\(node:\d+\)'), re.compile(r'\[\d+:\d+:\d+/'),
             re.compile(r'ghp_[A-Za-z0-9]{20,}|Bearer [A-Za-z0-9._-]{20,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}'))
SAMPLES = ('/ho' + 'me/someone/p', '/Us' + 'ers/someone/p', 'C:\\Us' + 'ers\\someone\\p', 'C:\\\\Us' + 'ers\\\\someone', '/tmp/sess/x', '/root/x', '/var/x', '~/x', 'file:///etc/x', 'a@b.example',
           'localhost:5181', 'http://h:8080/', '10.1.2.3', '[::1]', 'hostname="box"', '"hostname": "box"', '(node:123)', '[1234:5678:0101/', 'ghp_' + 'a' * 24)


def names_of_this_machine():
    found = set()
    for getter in (getpass.getuser, lambda: socket.gethostname().split('.')[0], lambda: os.path.basename(os.path.expanduser('~'))):
        try:
            found.add(getter())
        except (OSError, KeyError, ImportError):   # no passwd entry (a container with a random uid)
            continue
    return {name for name in found if name and len(name) > 3 and name.lower() not in GENERIC}


class WebFixtures(unittest.TestCase):
    def test_the_fixtures_exist_for_every_tool(self):
        for tool in TOOLS:
            with self.subTest(tool=tool):
                self.assertGreaterEqual(len(list(FIXTURES.glob(f'{tool}-*.jsonl'))), 3)
                self.assertTrue(list(FIXTURES.glob(f'{tool}-*.xml')))

    def test_the_node_fixtures_exist_and_are_guarded(self):
        for prefix, minimum in MIN_JSONL.items():
            with self.subTest(prefix=prefix):
                found = list(FIXTURES.glob(f'{prefix}-*.jsonl'))
                self.assertGreaterEqual(len(found), minimum)
                self.assertTrue(all(p in FILES for p in found))   # the forbidden patterns, the names and the budget cover them
        for name in ('tap-node22-describe.jsonl', 'node-test-spec-node22-describe.jsonl'):
            with self.subTest(name=name):
                self.assertTrue(is_recording(name))   # `node-test-...` is not skipped by a split on `-`
        self.assertFalse(is_recording('node-tap.txt'))   # the older `node-*` files are not recordings of a guarded family
        self.assertFalse(is_recording('tapas-x.jsonl'))   # the prefix needs its `-`

    def test_no_forbidden_pattern(self):
        for path in FILES:
            text = path.read_text(encoding='utf-8')
            for pattern in FORBIDDEN:
                with self.subTest(file=path.name, pattern=pattern.pattern):
                    self.assertIsNone(pattern.search(text), pattern.search(text) and pattern.search(text).group(0)[:60])

    def test_the_patterns_can_fail(self):
        for sample in SAMPLES:
            with self.subTest(sample=sample):
                self.assertTrue(any(p.search(sample) for p in FORBIDDEN), sample)
        for clean in ('Chrome 157.0.0.0', '/ROOT/e2e/a.js', '/TOOLS/tools/x', 'localhost:PORT', 'hostname="HOST"', '/NODE_MODULES', 'http://localhost:PORT/__cypress/runner.js:1776:23'):
            with self.subTest(clean=clean):
                self.assertFalse(any(p.search(clean) for p in FORBIDDEN), clean)

    def test_the_name_of_this_user_and_this_machine_do_not_occur(self):
        for path in FILES:
            text = path.read_text(encoding='utf-8')
            for name in names_of_this_machine():
                with self.subTest(file=path.name):
                    self.assertIsNone(re.search(r'(?<![\w-])' + re.escape(name) + r'(?![\w-])', text, re.IGNORECASE))

    def test_every_recording_has_its_meta_and_chunks(self):
        for path in FIXTURES.glob('*.jsonl'):
            if not is_recording(path.name):
                continue
            rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
            with self.subTest(file=path.name):
                meta = rows[0]['meta']
                for key in ('tool', 'version', 'command', 'exit', 'note', 'scrubbed', 'format'):
                    self.assertIn(key, meta)
                self.assertTrue(rows[1:] and all({'t', 'fd', 'data'} <= set(r) and r['fd'] in ('out', 'err') for r in rows[1:]))
                self.assertEqual([r['t'] for r in rows[1:]], sorted(r['t'] for r in rows[1:]))

    def test_only_the_hand_written_node_fixtures_are_marked_synthetic(self):
        for prefix in NODE_PREFIXES:
            for path in FIXTURES.glob(f'{prefix}-*.jsonl'):
                meta = json.loads(path.read_text(encoding='utf-8').splitlines()[0])['meta']
                with self.subTest(file=path.name):
                    self.assertEqual(bool(meta.get('synthetic')), path.name in SYNTHETIC)
        self.assertTrue(SYNTHETIC <= {p.name for p in FIXTURES.glob('tap-*.jsonl')})   # none of them was forgotten

    def test_the_total_size_fits_the_budget(self):
        self.assertLess(sum(p.stat().st_size for p in FILES), BUDGET)


if __name__ == '__main__':
    unittest.main()
