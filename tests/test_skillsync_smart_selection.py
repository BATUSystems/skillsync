"""Kontrakty addytywnych pól smart-test: uncovered, test_only, direct_files, summary."""
import contextlib
import io
import json
import sys
from types import SimpleNamespace
from unittest import mock
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_skillsync_workflow import WorkflowFixture  # noqa: E402
from layers_helpers import needs_os_symlinks  # noqa: E402


class SmartSelectionFields(WorkflowFixture):
    def setUp(self):
        super().setUp()
        self.checks(self.check('core'))
        self.manifest['workflow']['testing'] = {'modules': [
            {'id': 'core', 'paths': ['core/**'], 'checks': ['core']}]}
        self.save()
        self.write('core/base.py', 'old')
        self.init_git()

    def test_uncovered_lists_unmapped_files_sorted(self):
        report = self.report('smart-test', '--files', 'zz.py', 'aa.py', 'core/base.py')
        self.assertTrue(report['fallback'])
        self.assertEqual(report['uncovered'], ['aa.py', 'zz.py'])

    def test_uncovered_empty_when_mapped(self):
        report = self.report('smart-test', '--files', 'core/base.py')
        self.assertEqual(report['uncovered'], [])
        self.assertFalse(report['fallback'])

    def test_no_new_keys_keeps_defaults(self):
        report = self.report('smart-test', '--files', 'core/base.py')
        self.assertIsNone(report['test_only'])
        self.assertEqual(report['direct_files'], [])
        self.assertNotIn('summary', report)
        self.assertEqual([c['id'] for c in report['checks']], ['core'])

    def test_test_only_true_with_direct_files(self):
        self.manifest['workflow']['testing']['test_paths'] = ['tests/**']
        self.manifest['workflow']['testing']['rules'] = [{'paths': ['tests/**'], 'checks': ['core']}]
        self.save()
        report = self.report('smart-test', '--files', 'tests/b.py', 'tests/a.py')
        self.assertIs(report['test_only'], True)
        self.assertEqual(report['direct_files'], ['tests/a.py', 'tests/b.py'])
        self.assertEqual([c['id'] for c in report['checks']], ['core'])

    def test_test_only_false_when_mixed_or_wide(self):
        self.manifest['workflow']['testing']['test_paths'] = ['tests/**']
        self.manifest['workflow']['testing']['rules'] = [{'paths': ['tests/**'], 'checks': ['core']}]
        self.save()
        mixed = self.report('smart-test', '--files', 'tests/a.py', 'core/base.py')
        self.assertIs(mixed['test_only'], False)
        self.assertEqual(mixed['direct_files'], [])
        self.manifest['workflow']['testing']['test_paths'] = ['tests/**', 'package.json']
        self.save()
        wide = self.report('smart-test', '--files', 'tests/a.py', 'package.json')
        self.assertIs(wide['test_only'], False)
        self.assertEqual(wide['direct_files'], [])

    def test_bad_test_paths_type_blocks(self):
        for bad in ('tests/**', [1], {'a': 'b'}):
            with self.subTest(bad=bad):
                self.manifest['workflow']['testing']['test_paths'] = bad
                self.save()
                report = self.report('smart-test', '--files', 'core/base.py', code=2)
                self.assertEqual(report['status'], 'BLOCKED')
                self.assertTrue(any('test_paths' in r.get('reason', '') for r in report['reasons']))

    def test_summary_after_run_pass(self):
        report = self.report('smart-test', '--files', 'core/base.py', '--run')
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['summary'], {'planned': 1, 'ran': 1, 'passed': 1, 'failed': 0,
                                             'blocked': 0, 'not_run': 0, 'tests_counted': 1})

    def test_summary_after_run_fail(self):
        self.checks({'id': 'core', 'kind': 'test',
                     'argv': ['/usr/bin/python3', '-c', 'import sys; sys.exit(1)']})
        report = self.report('smart-test', '--files', 'core/base.py', '--run', code=1)
        self.assertEqual(report['status'], 'FAIL')
        summary = report['summary']
        self.assertEqual((summary['planned'], summary['ran'], summary['passed'], summary['failed']), (1, 1, 0, 1))
        self.assertIsNone(summary['tests_counted'])

    def test_tests_counted_known_sum(self):
        self.checks(self.check('core', output='3 passed'))
        report = self.report('smart-test', '--files', 'core/base.py', '--run')
        self.assertEqual(report['summary']['tests_counted'], 3)

    def test_tests_counted_unknown_is_none(self):
        self.checks({'id': 'core', 'kind': 'test', 'success_pattern': 'hello',
                     'argv': ['/usr/bin/python3', '-c', 'print("hello")']})
        report = self.report('smart-test', '--files', 'core/base.py', '--run')
        self.assertEqual(report['status'], 'PASS')
        self.assertIsNone(report['summary']['tests_counted'])

    @needs_os_symlinks
    def test_summary_blocked_series_without_results(self):
        (self.root / '.skillsync').mkdir(exist_ok=True)
        (self.root / 'elsewhere').mkdir()
        (self.root / '.skillsync' / 'logs').symlink_to(self.root / 'elsewhere')
        report = self.report('smart-test', '--files', 'core/base.py', '--run', code=2)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['summary'], {'planned': 1, 'ran': 0, 'passed': 0, 'failed': 0,
                                             'blocked': 1, 'not_run': 0, 'tests_counted': None})

    def test_summary_not_run_after_interrupted_series(self):
        execution = {'status': 'BLOCKED', 'interrupted': True, 'results': [
            {'id': 'a', 'status': 'PASS', 'exit_code': 0, 'tests_run': 2},
            {'id': 'b', 'status': 'NOT_RUN', 'exit_code': None, 'tests_run': None}]}
        args = SimpleNamespace(path=str(self.root), library=None, base=None,
                               files=['core/base.py'], profile='quick', run=True, json=True)
        self.checks(self.check('core'), self.check('svc'))
        self.manifest['workflow']['testing']['modules'][0]['checks'] = ['core', 'svc']
        self.save()
        from skillsync_workflow import selection
        out = io.StringIO()
        with mock.patch.object(selection, 'run_validation', return_value=execution), \
                contextlib.redirect_stdout(out):
            selection.command(args)
        summary = json.loads(out.getvalue())['summary']
        self.assertEqual(summary, {'planned': 2, 'ran': 1, 'passed': 1, 'failed': 0,
                                   'blocked': 0, 'not_run': 1, 'tests_counted': 2})


if __name__ == '__main__':
    unittest.main()
