"""PROC-AB: `status` ostrzega o braku `pre-implement` (pole `preImplement`), `plan|spec approve` zwraca `warnings`,
`readiness --run-id` zapisuje `.skillsync/runs/<id>/readiness.md`."""
import os
import subprocess
import sys
import unittest

from tests.test_skillsync_workflow import CLI, WorkflowFixture
from tests.layers_helpers import needs_os_symlinks  # noqa: E402

RISKY = '# Spec\n\nStatus: draft\n\n## Kontrakty\n\n- `status` dostaje pole.\n\n## Migracja\n\nNie dotyczy.\n'
CALM = '# Spec\n\nStatus: draft\n\n## Kontrakty\n\nNie dotyczy.\n\n## Migracja\n\nNie dotyczy: brak.\n\n## Dane\n\nTODO\n'


class PreImplementFixture(WorkflowFixture):
    def doc(self, name, text):
        return self.write(name, text)

    def plan_with(self, status='approved', spec='docs/specs/s.md', run='R-1'):
        lines = ['# Plan', '', f'Status: {status}']
        if spec:
            lines.append(f'Spec: {spec}')
        if run:
            lines.append(f'Przebieg: {run}')
        lines += ['', '## Progress', '', '- [ ] F1 krok']
        return self.doc('docs/plans/p.md', '\n'.join(lines) + '\n')

    def pre(self):
        rows = {row['path']: row for row in self.report('status')['plans']}
        return rows['docs/plans/p.md']['preImplement']

    def report_file(self, run, name, text):
        return self.write(f'.skillsync/runs/{run}/{name}', text)


class StatusPreImplement(PreImplementFixture):
    def test_sections_without_report_warn(self):
        self.doc('docs/specs/s.md', RISKY)
        self.plan_with()
        pre = self.pre()
        self.assertTrue(pre['required'])
        self.assertEqual(pre['sections'], ['Kontrakty'])
        self.assertEqual((pre['run'], pre['report'], pre['verdict']), ('R-1', None, None))
        self.assertIn('brak pre-implement', pre['warning'])
        self.assertIn('Kontrakty', pre['warning'])

    def test_ready_report_clears_warning(self):
        self.doc('docs/specs/s.md', RISKY)
        self.plan_with()
        self.report_file('R-1', 'pre-implement.md', '\nWerdykt: READY\n\ntreść\n')
        pre = self.pre()
        self.assertEqual((pre['verdict'], pre['report'], pre['warning']), ('READY', '.skillsync/runs/R-1/pre-implement.md', None))

    def test_blocked_verdict_warns_and_is_worst(self):
        self.doc('docs/specs/s.md', RISKY)
        self.plan_with()
        self.report_file('R-1', 'pre-implement-1.md', 'Werdykt: BLOCKED\n')
        self.report_file('R-1', 'pre-implement-2.md', 'Werdykt: READY\n')
        pre = self.pre()
        self.assertEqual((pre['verdict'], pre['report']), ('BLOCKED', '.skillsync/runs/R-1/pre-implement-2.md'))
        self.assertIn('BLOCKED', pre['warning'])

    def test_invalid_report_does_not_count(self):
        self.doc('docs/specs/s.md', RISKY)
        self.plan_with()
        self.report_file('R-1', 'pre-implement.md', '# Raport\nWerdykt: READY\n')
        pre = self.pre()
        self.assertIsNone(pre['report'])
        self.assertIn('brak pre-implement', pre['warning'])

    def test_not_applicable_sections_are_not_required(self):
        self.doc('docs/specs/s.md', CALM)
        self.plan_with()
        pre = self.pre()
        self.assertEqual((pre['required'], pre['sections'], pre['warning']), (False, [], None))

    def test_unknown_run_warns_about_missing_przebieg_line(self):
        self.doc('docs/specs/s.md', RISKY)
        self.plan_with(run=None)
        pre = self.pre()
        self.assertIsNone(pre['run'])
        self.assertIn('Przebieg', pre['warning'])

    def test_plan_without_spec_has_no_pre_implement(self):
        self.plan_with(spec=None)
        self.assertIsNone(self.pre())

    def test_plan_with_missing_spec_file_has_none(self):
        self.plan_with()
        self.assertIsNone(self.pre())

    def test_done_plan_has_no_warning_but_keeps_required(self):
        self.doc('docs/specs/s.md', RISKY)
        self.plan_with(status='done')
        pre = self.pre()
        self.assertEqual((pre['required'], pre['warning']), (True, None))

    @needs_os_symlinks
    def test_symlinked_report_is_ignored(self):
        self.doc('docs/specs/s.md', RISKY)
        self.plan_with()
        outside = self.base / 'out.md'
        outside.write_text('Werdykt: READY\n')
        run = self.root / '.skillsync/runs/R-1'
        run.mkdir(parents=True)
        os.symlink(outside, run / 'pre-implement.md')
        self.assertIsNone(self.pre()['report'])


class ApproveWarnings(PreImplementFixture):
    def test_plan_approve_returns_warning_and_still_passes(self):
        self.doc('docs/specs/s.md', RISKY)
        self.plan_with(status='draft')
        report = self.approve('plan', 'docs/plans/p.md')
        self.assertEqual((report['status'], report['to']), ('PASS', 'approved'))
        self.assertEqual(len(report['warnings']), 1)
        self.assertIn('brak pre-implement', report['warnings'][0])
        self.assertIn('Status: approved', (self.root / 'docs/plans/p.md').read_text())

    def test_plan_approve_without_risk_has_empty_warnings(self):
        self.doc('docs/specs/s.md', CALM)
        self.plan_with(status='draft')
        self.assertEqual(self.approve('plan', 'docs/plans/p.md')['warnings'], [])

    def test_spec_approve_uses_linked_plan(self):
        self.doc('docs/specs/s.md', RISKY.replace('Status: draft\n', 'Status: draft\nPlan: docs/plans/p.md\n'))
        self.plan_with(status='draft')
        report = self.approve('spec', 'docs/specs/s.md')
        self.assertEqual(report['to'], 'approved')
        self.assertIn('brak pre-implement', report['warnings'][0])
        self.assertIn('R-1', report['warnings'][0])

    def test_spec_approve_without_plan_warns_about_run(self):
        self.doc('docs/specs/s.md', RISKY)
        report = self.approve('spec', 'docs/specs/s.md')
        self.assertIn('Przebieg', report['warnings'][0])

    def test_ready_report_gives_no_warnings(self):
        self.doc('docs/specs/s.md', RISKY)
        self.plan_with(status='draft')
        self.report_file('R-1', 'pre-implement.md', 'Werdykt: READY\n')
        self.assertEqual(self.approve('plan', 'docs/plans/p.md')['warnings'], [])

    def approve(self, kind, doc):
        result = subprocess.run([sys.executable, str(CLI), kind, 'approve', str(self.root), doc, '--json'],
                                capture_output=True, text=True, timeout=30, cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        import json
        return json.loads(result.stdout)


class ReadinessRunId(WorkflowFixture):
    def setUp(self):
        super().setUp()
        self.write('src/api/orders.py', 'def orders(): return []')

    def test_pass_writes_report(self):
        self.write('plan.md', '# Contracts\nKeeps list.\n# Risks\nEmpty orders ok.\n# Evidence\nRegression tests.\n')
        report = self.report('readiness', '--files', 'src/api/orders.py', '--plan', 'plan.md', '--run-id', 'RUN-1')
        self.assertEqual(report['reportFile'], '.skillsync/runs/RUN-1/readiness.md')
        text = (self.root / report['reportFile']).read_text(encoding='utf-8')
        self.assertEqual(text.splitlines()[0], 'Readiness: PASS')
        self.assertIn('src/api/orders.py (istnieje)', text)
        self.assertIn('plan.md', text)

    def test_blocked_writes_report_with_issues(self):
        report = self.report('readiness', '--files', 'src/api/orders.py', '--run-id', 'RUN-2', code=2)
        text = (self.root / report['reportFile']).read_text(encoding='utf-8')
        self.assertEqual(text.splitlines()[0], 'Readiness: BLOCKED')
        self.assertIn('Risky change requires a plan', text)

    def test_invalid_run_ids_are_rejected(self):
        for run_id in ('../x', 'a/b', '..', ''):
            with self.subTest(run_id):
                report = self.report('readiness', '--files', 'src/api/orders.py', '--run-id', run_id, code=2)
                self.assertEqual(report['status'], 'BLOCKED')
        self.assertFalse((self.root / '.skillsync').exists())
        self.assertFalse((self.base / 'x').exists())

    def test_without_flag_nothing_is_written(self):
        report = self.report('readiness', '--files', 'src/api/orders.py', code=2)
        self.assertNotIn('reportFile', report)
        self.assertFalse((self.root / '.skillsync').exists())

    @needs_os_symlinks
    def test_symlinked_run_directory_is_refused(self):
        runs = self.root / '.skillsync/runs'
        runs.mkdir(parents=True)
        target = self.base / 'elsewhere'
        target.mkdir()
        os.symlink(target, runs / 'RUN-3')
        report = self.report('readiness', '--files', 'src/api/orders.py', '--run-id', 'RUN-3', code=2)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(list(target.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
