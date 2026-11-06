"""`status` knows `archived`; `plan archive|unarchive` rewrites only the Status line."""
import json
import os
import subprocess
import sys
import unittest

from tests.test_skillsync_workflow import CLI, WorkflowFixture
from tests.layers_helpers import needs_os_symlinks, needs_posix  # noqa: E402


class PlanArchive(WorkflowFixture):
    def plan(self, name, data):
        path = self.root / 'docs/plans' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def cli(self, *args, code=0):
        result = subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True, timeout=30,
                                cwd=self.root, env={**os.environ, 'SKILLSYNC_CONFIG': str(self.base / 'none.yaml')})
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def status_rows(self):
        return {row['path']: row for row in self.cli('status', self.root, '--json')['plans']}

    def test_archived_is_recognized_without_needs_human(self):
        self.plan('a.md', b'# A\n\nStatus: archived\n')
        self.plan('b.md', b'# B\n\nStatus: draft\n')
        rows = self.status_rows()
        self.assertEqual((rows['docs/plans/a.md']['status'], rows['docs/plans/a.md']['needsHuman']), ('archived', []))
        self.assertEqual(rows['docs/plans/b.md']['needsHuman'], ['approval'])

    def test_archive_and_unarchive_change_only_the_status_word(self):
        variants = {
            'plain': b'# T\r\n\r\nStatus: done\r\nrest  \r\n',
            'bold': b'# T\n\n**Status:** **done**\nrest\n',
            'bullet': b'# T\n\n- Status: done (merged, PR #1)\nrest',
            'front': b'---\nstatus: Done\ntitle: x\n---\n# T\n',
        }
        for name, data in variants.items():
            with self.subTest(name):
                path = self.plan(f'{name}.md', data)
                report = self.cli('plan', 'archive', self.root, f'docs/plans/{name}.md', '--json')
                self.assertEqual((report['status'], report['from'], report['to']), ('PASS', 'done', 'archived'))
                self.assertEqual(self.status_rows()[f'docs/plans/{name}.md']['status'], 'archived')
                changed = path.read_bytes()
                self.assertEqual(len(changed.splitlines(True)), len(data.splitlines(True)))
                self.assertEqual([a for a, b in zip(data.splitlines(True), changed.splitlines(True)) if a != b].__len__(), 1)
                self.cli('plan', 'unarchive', self.root, str(path))
                restored = path.read_bytes()
                self.assertEqual(restored.lower().replace(b'done', b'x'), data.lower().replace(b'done', b'x'))
                self.assertEqual(self.status_rows()[f'docs/plans/{name}.md']['status'], 'done')

    def test_rest_of_file_is_byte_identical(self):
        path = self.plan('b.md', 'Status: done\n\n# Zażółć\r\n\tx  tail'.encode())
        self.cli('plan', 'archive', self.root, 'docs/plans/b.md')
        self.assertEqual(path.read_bytes(), 'Status: archived\n\n# Zażółć\r\n\tx  tail'.encode())
        self.assertEqual([p.name for p in path.parent.iterdir()], ['b.md'])

    def test_approve_changes_only_draft_to_approved(self):
        path = self.plan('e.md', b'# E\r\n\r\nStatus: draft (szkic)\r\nrest')
        report = self.cli('plan', 'approve', self.root, 'docs/plans/e.md', '--json')
        self.assertEqual((report['status'], report['from'], report['to']), ('PASS', 'draft', 'approved'))
        self.assertEqual(path.read_bytes(), b'# E\r\n\r\nStatus: approved (szkic)\r\nrest')
        self.assertEqual(self.status_rows()['docs/plans/e.md']['status'], 'approved')
        for status in ('approved', 'in_progress', 'done', 'archived'):
            path.write_bytes(f'Status: {status}\n'.encode())
            self.assertEqual(self.cli('plan', 'approve', self.root, 'docs/plans/e.md', code=2)['status'], 'BLOCKED')
            self.assertEqual(path.read_bytes(), f'Status: {status}\n'.encode())

    def test_refuses_wrong_source_status(self):
        for status in ('draft', 'in_progress', 'archived', 'unknown-thing'):
            path = self.plan('c.md', f'Status: {status}\n'.encode())
            report = self.cli('plan', 'archive', self.root, 'docs/plans/c.md', code=2)
            self.assertEqual(report['status'], 'BLOCKED')
            self.assertEqual(path.read_bytes(), f'Status: {status}\n'.encode())
        report = self.cli('plan', 'unarchive', self.root, 'docs/plans/c.md', code=2)
        self.assertEqual(report['status'], 'BLOCKED')

    def test_missing_status_line_is_blocked(self):
        path = self.plan('d.md', b'# D\n\nbrak\n')
        report = self.cli('plan', 'archive', self.root, 'docs/plans/d.md', code=2)
        self.assertEqual((report['status'], report['root']), ('BLOCKED', str(self.root.resolve())))
        self.assertIn('Status', report['reason'])
        self.assertEqual(path.read_bytes(), b'# D\n\nbrak\n')

    @needs_os_symlinks
    def test_path_validation(self):
        self.plan('ok.md', b'Status: done\n')
        (self.root / 'docs/plans/README.md').write_bytes(b'Status: done\n')
        (self.root / 'docs/other.md').write_bytes(b'Status: done\n')
        outside = self.base / 'outside.md'
        outside.write_bytes(b'Status: done\n')
        (self.root / 'docs/plans/link.md').symlink_to(outside)
        for bad in ('../plans/ok.md', 'docs/plans/../other.md', 'docs/other.md', 'docs/plans/README.md',
                    'docs/plans/link.md', str(outside), 'docs/plans/missing.md', 'docs/plans/ok.txt', 'docs/plans'):
            with self.subTest(bad):
                self.assertEqual(self.cli('plan', 'archive', self.root, bad, code=2)['status'], 'BLOCKED')
        self.assertEqual(outside.read_bytes(), b'Status: done\n')
        self.assertEqual((self.root / 'docs/other.md').read_bytes(), b'Status: done\n')

    @needs_os_symlinks
    def test_symlinked_docs_or_plans_are_refused(self):
        real = self.base / 'real'
        (real / 'plans').mkdir(parents=True)
        (real / 'plans/x.md').write_bytes(b'Status: done\n')
        (self.root / 'docs').symlink_to(real)
        self.assertEqual(self.cli('plan', 'archive', self.root, 'docs/plans/x.md', code=2)['status'], 'BLOCKED')
        (self.root / 'docs').unlink()
        (self.root / 'docs').mkdir()
        (self.root / 'docs/plans').symlink_to(real / 'plans')
        self.assertEqual(self.cli('plan', 'archive', self.root, 'docs/plans/x.md', code=2)['status'], 'BLOCKED')
        self.assertEqual((real / 'plans/x.md').read_bytes(), b'Status: done\n')

    @needs_os_symlinks
    @needs_posix
    def test_planted_tmp_symlink_is_not_followed_and_mode_is_kept(self):
        path = self.plan('m.md', b'Status: done\n')
        os.chmod(path, 0o640)
        victim = self.base / 'victim.txt'
        victim.write_bytes(b'keep')
        for pid in range(1, 3):
            (path.parent / f'.m.md.{pid}.tmp').symlink_to(victim)
        self.cli('plan', 'archive', self.root, 'docs/plans/m.md')
        self.assertEqual(victim.read_bytes(), b'keep')
        self.assertEqual(path.stat().st_mode & 0o7777, 0o640)
        self.assertEqual(path.read_bytes(), b'Status: archived\n')
        self.assertEqual(sorted(p.name for p in path.parent.iterdir() if p.name.endswith('.tmp')), ['.m.md.1.tmp', '.m.md.2.tmp'])


if __name__ == '__main__':
    unittest.main()
