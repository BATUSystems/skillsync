"""CLI `skillsync prototype writing <projekt> <slug> start|done|status` (PROTO-READY, K1, K2, K12 część CLI).

Kontrakt: .skillsync/runs/PROTO-READY/spec.md (K1, „Kontrakty -> CLI"). Testy obserwują raporty CLI (kody 0/2),
plik `.writing.json` i `.gitignore` tymczasowego projektu; zegar i blokada wstrzykiwane w procesie testu
(CommentsStore z krótkim lock_timeout), bez długich procesów. Każdy test negatywny ma przypadek kontrolny.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_prototype_writing_cli.py`.
Zgodność ze składnią Pythona 3.8.
"""
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype as pt  # noqa: E402
from skillsync_workflow import prototype_server as ps  # noqa: E402
from tests.test_skillsync_prototype import PrototypeFixture  # noqa: E402
from tests.test_skillsync_prototype_server import setUpModule, tearDownModule  # noqa: E402,F401  (strażnik katalogu)

SECTION = ('<section class="screen" id="s3">\n  <div class="frame"><h2 data-proto-id="s3-h">Tydzień</h2></div>\n'
           '</section>\n')


class WritingCliFixture(PrototypeFixture):
    def setUp(self):
        super().setUp()
        self.init('demo')
        self.dir = self.proto_dir()
        index = self.dir / 'index.html'
        text = index.read_text(encoding='utf-8')
        index.write_text(text.replace('</body>', SECTION + '</body>', 1), encoding='utf-8', newline='')
        self.marker = self.dir / ps.WRITING_MARKER

    def writing(self, action, *args, code=0):
        return self.proto('writing', 'demo', action, *args, code=code)

    def stored(self):
        return json.loads(self.marker.read_text(encoding='utf-8'))

    def in_process(self, action, clock, **kwargs):
        """`writing_prototype` in this process with an injected clock (seconds) and a short lock timeout."""
        root, manifest = pt.read_project(str(self.root))
        real = pt._store

        def store(directory, slug):
            return ps.CommentsStore(str(directory), slug, clock=clock, lock_timeout=0.3)
        with mock.patch.object(pt, '_store', store):
            return pt.writing_prototype(root, manifest, 'demo', action, **kwargs), real


class WritingCliTests(WritingCliFixture):
    def test_start_status_done_cycle(self):
        before = time.time() * 1000
        started = self.writing('start')
        after = time.time() * 1000
        self.assertEqual(started['status'], 'PASS')
        window = started['writing']
        self.assertEqual(set(window), {'since', 'expiresAt', 'by'})
        self.assertEqual(window['by'], 'agent')
        self.assertEqual(window['expiresAt'] - window['since'], 900_000)
        self.assertTrue(before - 1000 <= window['since'] <= after + 1000)
        self.assertEqual(self.stored()['version'], 1)
        status = self.writing('status')
        self.assertEqual(status['status'], 'PASS')
        self.assertEqual(status['writing'], window)
        done = self.writing('done')
        self.assertEqual((done['status'], done['writing']), ('PASS', None))
        self.assertFalse(self.marker.exists())
        self.assertIsNone(self.writing('status')['writing'])
        self.assertEqual(self.writing('done')['status'], 'PASS', 'done is idempotent')

    def test_status_without_marker_is_null(self):
        report = self.writing('status')
        self.assertEqual(report['status'], 'PASS')
        self.assertIsNone(report['writing'])

    def test_ttl_and_by_are_stored(self):
        report = self.writing('start', '--ttl', '1800', '--by', 'ala.kot@example.com')
        self.assertEqual(report['writing']['expiresAt'] - report['writing']['since'], 1_800_000)
        self.assertEqual(report['writing']['by'], 'ala.kot@example.com')
        self.assertEqual(self.stored()['by'], 'ala.kot@example.com')

    def test_ttl_limits_are_inclusive(self):
        for ttl in ('60', '3600'):
            with self.subTest(ttl=ttl):
                report = self.writing('start', '--ttl', ttl)
                self.assertEqual(report['writing']['expiresAt'] - report['writing']['since'], int(ttl) * 1000)

    def test_invalid_args_are_blocked_and_write_nothing(self):
        for args in (('--ttl', '59'), ('--ttl', '3601'), ('--ttl', '0'), ('--ttl', '-5'), ('--ttl', 'abc'),
                     ('--ttl', '1.5'), ('--ttl', ''), ('--by', ''), ('--by', 'a b'), ('--by', 'x' * 65),
                     ('--by', 'zażółć')):
            with self.subTest(args=args):
                report = self.writing('start', *args, code=2)
                self.assertEqual(report['error'], 'invalid_args', report)
                self.assertFalse(self.marker.exists())
        self.assertEqual(self.writing('start', '--ttl', '60', '--by', 'x' * 64)['status'], 'PASS', 'control')

    def test_invalid_args_are_checked_for_done_and_status_too(self):
        self.assertEqual(self.writing('status', '--ttl', '5', code=2)['error'], 'invalid_args')
        self.assertEqual(self.writing('done', '--by', 'a b', code=2)['error'], 'invalid_args')

    def test_repeated_start_replaces_both_fields_also_shortening(self):
        first = self.writing('start', '--ttl', '3600')['writing']
        time.sleep(0.01)
        second = self.writing('start', '--ttl', '60')['writing']
        self.assertGreater(second['since'], first['since'])
        self.assertEqual(second['expiresAt'] - second['since'], 60_000)
        self.assertEqual(self.writing('status')['writing'], second)

    def test_unknown_action_and_unknown_slug(self):
        report = self.proto('writing', 'nieistnieje', 'start', code=2)
        self.assertEqual(report['status'], 'BLOCKED')
        result = self.run_argv_unknown_action()
        self.assertNotEqual(result, 0)

    def run_argv_unknown_action(self):
        import subprocess
        from tests.test_skillsync_prototype import CLI
        return subprocess.run([sys.executable, str(CLI), 'prototype', 'writing', str(self.root), 'demo', 'stop',
                               '--json'], capture_output=True, text=True, timeout=60).returncode

    def test_v1_prototype_needs_migration(self):
        (self.dir / 'comments.json').unlink()
        (self.dir / 'comments.js').write_text('window.PROTOTYPE_COMMENTS = {};\n', encoding='utf-8')
        for action in ('start', 'done', 'status'):
            with self.subTest(action=action):
                report = self.writing(action, code=2)
                self.assertIn('needs_migration', report['reason'])
                self.assertEqual(report.get('error'), 'needs_migration')
        self.assertFalse(self.marker.exists())

    def test_gitignore_gets_the_line_once(self):
        gitignore = self.dir / '.gitignore'
        gitignore.write_text('comments.json.lock\n*.tmp\n', encoding='utf-8')
        report = self.writing('start')
        self.assertNotIn('warnings', report)
        self.assertEqual(gitignore.read_text(encoding='utf-8').splitlines(),
                         ['comments.json.lock', '*.tmp', '.writing.json'])
        self.writing('start')
        self.assertEqual(gitignore.read_text(encoding='utf-8').count('.writing.json'), 1)

    def test_missing_gitignore_is_created_and_unreadable_one_only_warns(self):
        (self.dir / '.gitignore').unlink()
        self.writing('start')
        self.assertEqual((self.dir / '.gitignore').read_text(encoding='utf-8').strip(), '.writing.json')
        target = self.dir / '.gitignore'
        target.unlink()
        target.mkdir()  # not a regular file: `_ensure_lines` refuses
        report = self.writing('start')
        self.assertEqual(report['status'], 'PASS')
        self.assertTrue(any('.gitignore' in warning for warning in report['warnings']), report)
        self.assertTrue(self.marker.exists())

    def test_template_and_migrate_lines_carry_the_marker(self):
        self.assertIn('.writing.json', pt.GITIGNORE_LINES)
        template = (ROOT / 'library' / 'skills' / 'prototype' / 'assets' / 'repo' / '.gitignore')
        self.assertIn('.writing.json', template.read_text(encoding='utf-8').splitlines())

    def test_screen_hashes_do_not_change_after_start(self):
        before = ps.screen_hashes(str(self.dir))
        self.writing('start')
        after = ps.screen_hashes(str(self.dir))
        self.assertEqual(after['screens'], before['screens'])
        self.assertEqual((after['version'], after['assets']), (before['version'], before['assets']))
        self.assertEqual(sorted(after['screens']), ['s2', 's3'], 'control: the hashed screens exist')

    def test_list_and_ids_check_still_pass_with_the_marker(self):
        self.writing('start')
        self.assertEqual(self.list()['status'], 'PASS')
        self.assertEqual(self.proto('ids', 'demo', '--check')['status'], 'PASS')


class WritingClockAndLockTests(WritingCliFixture):
    def test_injected_store_clock_sets_both_times(self):
        report, _ = self.in_process('start', lambda: 1_800_000_000.5, ttl='120', by='ala')
        self.assertEqual(report['writing'], {'since': 1_800_000_000_500, 'expiresAt': 1_800_000_120_500, 'by': 'ala'})
        status, _ = self.in_process('status', lambda: 1_800_000_119.0)
        self.assertEqual(status['writing'], report['writing'])
        expired, _ = self.in_process('status', lambda: 1_800_000_121.0)
        self.assertIsNone(expired['writing'])
        self.assertTrue(self.marker.exists(), 'expiry removes nothing: a read never writes')

    def test_start_blocked_by_a_held_lock_but_done_and_status_are_not(self):
        self.writing('start')
        stored = self.marker.read_bytes()
        store = ps.CommentsStore(str(self.dir), 'demo')
        with store.lock():
            blocked, _ = self.in_process('start', time.time, ttl='60')
            self.assertEqual((blocked['status'], blocked['error']), ('BLOCKED', 'lock_timeout'), blocked)
            self.assertEqual(self.marker.read_bytes(), stored, 'the failed start changed nothing')
            status, _ = self.in_process('status', time.time)
            self.assertEqual(status['status'], 'PASS')
            self.assertIsNotNone(status['writing'])
            done, _ = self.in_process('done', time.time)
            self.assertEqual(done['status'], 'PASS')
        self.assertFalse(self.marker.exists())
        ok, _ = self.in_process('start', time.time)  # control: free lock
        self.assertEqual(ok['status'], 'PASS')


if __name__ == '__main__':
    unittest.main()
