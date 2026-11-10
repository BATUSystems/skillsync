"""`status` reads consents from `.skillsync/runs/<run-id>/approvals.jsonl`; `approvals wait` waits for one."""
import json
import os
import subprocess
import sys
import threading
import time
import unittest

from tests.test_skillsync_workflow import CLI, WorkflowFixture

HEAD = 'f27ef059c38902e6beef347221a4c0b7aaf8837a'


class Approvals(WorkflowFixture):
    def cli(self, *args, code=0):
        result = subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True, timeout=60,
                                cwd=self.root, env={**os.environ, 'SKILLSYNC_CONFIG': str(self.base / 'none.yaml')})
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def log(self, run, *entries, raw=''):
        path = self.root / '.skillsync/runs' / run / 'approvals.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as handle:
            handle.write(raw)
            for entry in entries:
                handle.write(json.dumps(entry) + '\n')
        return path

    def test_status_reports_the_latest_entry_per_run_and_word(self):
        self.log('2026-11-10-a',
                 {'word': 'CP', 'at': '2026-11-10T10:00:00Z', 'sha': 'a' * 40, 'source': 'status-board'},
                 {'word': 'CP', 'at': '2026-11-10T11:00:00Z', 'sha': HEAD, 'source': 'status-board'},
                 {'word': 'GO plan', 'at': '2026-11-10T09:00:00Z', 'sha': 'b' * 40, 'file': 'docs/specs/s.md'},
                 raw='nie json\n[1]\n{"word": "rm -rf", "at": "2026-11-10T09:00:00Z", "sha": "abc1234"}\n'
                     '{"word": "CP", "at": "zła data", "sha": "abc1234"}\n{"word": "CP", "at": "2026-11-10T12:00:00Z", "sha": "XYZ"}\n')
        rows = self.cli('status', self.root, '--json')['approvals']
        self.assertEqual([(r['run'], r['word'], r['sha'], r['at']) for r in rows],
                         [('2026-11-10-a', 'GO plan', 'b' * 40, '2026-11-10T09:00:00Z'),
                          ('2026-11-10-a', 'CP', HEAD, '2026-11-10T11:00:00Z')])
        self.assertEqual(rows[0]['file'], 'docs/specs/s.md')

    def test_status_without_runs_has_an_empty_list(self):
        self.assertEqual(self.cli('status', self.root, '--json')['approvals'], [])

    def test_wait_returns_a_matching_entry_and_honours_run_sha_and_since(self):
        self.log('r1', {'word': 'CP', 'at': '2026-11-10T11:00:00Z', 'sha': HEAD})
        found = self.cli('approvals', 'wait', self.root, '--word', 'CP', '--sha', HEAD, '--timeout', '0')
        self.assertEqual((found['status'], found['approval']['run'], found['approval']['kind']), ('PASS', 'r1', 'entry'))
        for extra in (('--sha', 'c' * 40), ('--run', 'r2'), ('--since', '2026-11-10T11:00:00Z')):
            with self.subTest(extra=extra):
                report = self.cli('approvals', 'wait', self.root, '--word', 'CP', *extra, '--timeout', '0', code=3)
                self.assertEqual(report['status'], 'TIMEOUT')
        self.assertEqual(self.cli('approvals', 'wait', self.root, '--word', 'READY', '--timeout', '0', code=3)['status'],
                         'TIMEOUT')
        self.assertEqual(self.cli('approvals', 'wait', self.root, '--word', 'CP', '--sha', 'NOT-HEX', '--timeout', '0',
                                  code=2)['status'], 'BLOCKED')

    def test_wait_wakes_up_when_the_entry_appears(self):
        writer = threading.Timer(1.0, lambda: self.log('r1', {'word': 'READY', 'at': '2026-11-10T12:00:00Z', 'sha': HEAD}))
        writer.start()
        started = time.monotonic()
        try:
            report = self.cli('approvals', 'wait', self.root, '--word', 'READY', '--timeout', '20', '--interval', '0.2')
        finally:
            writer.cancel()
        self.assertEqual(report['approval']['word'], 'READY')
        self.assertLess(time.monotonic() - started, 15)

    def test_go_wait_also_ends_on_an_approved_status(self):
        plan = self.root / 'docs/plans/p.md'
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text('# P\n\nStatus: draft (czeka na GO implement)\n', encoding='utf-8')
        self.cli('approvals', 'wait', self.root, '--word', 'GO implement', '--file', 'docs/plans/p.md', '--timeout', '0',
                 code=3)
        self.cli('plan', 'approve', self.root, 'docs/plans/p.md')
        report = self.cli('approvals', 'wait', self.root, '--word', 'GO implement', '--file', 'docs/plans/p.md',
                          '--timeout', '0')
        self.assertEqual((report['approval']['kind'], report['approval']['status']), ('status', 'approved'))
        self.assertEqual(self.cli('approvals', 'wait', self.root, '--word', 'GO plan', '--file', '../x.md', '--timeout', '0',
                                  code=2)['status'], 'BLOCKED')


if __name__ == '__main__':
    unittest.main()
