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
        self.assertEqual((pre['verdict'], pre['report']), ('BLOCKED', '.skillsync/runs/R-1/pre-implement-1.md'))
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

    def test_needs_changes_warns(self):
        self.doc('docs/specs/s.md', RISKY)
        self.plan_with()
        self.report_file('R-1', 'pre-implement.md', 'Werdykt: NEEDS_CHANGES\n')
        pre = self.pre()
        self.assertEqual(pre['verdict'], 'NEEDS_CHANGES')
        self.assertIn('NEEDS_CHANGES', pre['warning'])

    def test_markdown_bold_verdict_is_valid(self):
        self.doc('docs/specs/s.md', RISKY)
        self.plan_with()
        self.report_file('R-1', 'pre-implement.md', '**Werdykt:** READY\n')
        self.assertEqual(self.pre()['verdict'], 'READY')

    def test_tie_prefers_natural_highest_name(self):
        self.doc('docs/specs/s.md', RISKY)
        self.plan_with()
        for name in ('pre-implement-2.md', 'pre-implement-10.md'):
            self.report_file('R-1', name, 'Werdykt: BLOCKED\n')
        self.assertEqual(self.pre()['report'], '.skillsync/runs/R-1/pre-implement-10.md')

    def test_subsections_belong_to_the_section_body(self):
        self.doc('docs/specs/s.md', '# S\n\nStatus: draft\n\n## Kontrakty\n\n### API\n\nPole `x` w JSON.\n\n## Dane\n\n### Pusta\n')
        self.plan_with()
        pre = self.pre()
        self.assertEqual((pre['required'], pre['sections']), (True, ['Kontrakty']))

    def test_empty_heading_with_empty_subsection_is_empty(self):
        self.doc('docs/specs/s.md', '# S\n\nStatus: draft\n\n## Kontrakty\n\n### API\n\n## Inne\n\ntekst\n')
        self.plan_with()
        self.assertFalse(self.pre()['required'])

    def test_not_applicable_reason_variants(self):
        from skillsync_workflow.status import pre_implement_sections
        empty = ['Nie dotyczy.', 'nie dotyczy', 'Nie dotyczy: brak zmian', 'Nie dotyczy - brak', 'Nie dotyczy, bo X', '**Nie dotyczy**',
                 'TODO', 'TBD.', 'n/a', '-', '—']
        content = ['Nie dotyczy starych rekordów; nowa kolumna X', 'Todo-lista zadań', '- punkt', 'Pole X']
        for body in empty:
            with self.subTest(empty=body):
                self.assertEqual(pre_implement_sections(f'## Kontrakty\n\n{body}\n'), [])
        for body in content:
            with self.subTest(content=body):
                self.assertEqual(pre_implement_sections(f'## Kontrakty\n\n{body}\n'), ['Kontrakty'])

    @needs_os_symlinks
    def test_symlinked_specs_directory_is_refused(self):
        real = self.base / 'real-specs'
        real.mkdir()
        (real / 's.md').write_text(RISKY, encoding='utf-8')
        (self.root / 'docs').mkdir()
        os.symlink(real, self.root / 'docs/specs')
        self.plan_with()
        self.assertIsNone(self.pre())

    def test_library_spec_template_with_filled_sections(self):
        import re
        from pathlib import Path
        from skillsync_workflow.status import pre_implement_sections
        reference = Path(__file__).resolve().parent.parent / 'library/skills/feature/references/spec.md'
        template = re.search(r'````markdown\n(.*?)\n````', reference.read_text(encoding='utf-8'), re.DOTALL).group(1)
        for title in ('Kontrakty', 'Migracja', 'Dane'):
            self.assertNotIn(title, pre_implement_sections(template.replace(f'## {title}', f'## {title}\n\nNie dotyczy: powód.\n\n<!--')) )
        filled = re.sub(r'(## (?:Kontrakty|Migracja|Dane)\n\n)[^\n]*', r'\1Konsument X zachowuje zgodność.', template)
        self.assertEqual(sorted(pre_implement_sections(filled)), ['Dane', 'Kontrakty', 'Migracja'])


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

    def test_spec_approve_without_any_plan_has_no_warnings(self):
        self.doc('docs/specs/s.md', RISKY)
        self.assertEqual(self.approve('spec', 'docs/specs/s.md')['warnings'], [])

    def test_ready_report_gives_no_warnings(self):
        self.doc('docs/specs/s.md', RISKY)
        self.plan_with(status='draft')
        self.report_file('R-1', 'pre-implement.md', 'Werdykt: READY\n')
        self.assertEqual(self.approve('plan', 'docs/plans/p.md')['warnings'], [])

    def test_spec_approve_finds_plan_by_scan_ready_report(self):
        self.doc('docs/specs/s.md', RISKY)
        self.plan_with(status='draft')
        self.report_file('R-1', 'pre-implement.md', 'Werdykt: READY\n')
        self.assertEqual(self.approve('spec', 'docs/specs/s.md')['warnings'], [])

    def test_spec_approve_finds_plan_by_scan_without_report(self):
        self.doc('docs/specs/s.md', RISKY)
        self.plan_with(status='draft')
        warnings = self.approve('spec', 'docs/specs/s.md')['warnings']
        self.assertEqual(len(warnings), 1)
        self.assertIn('R-1', warnings[0])

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

    def test_report_has_date_and_plan_sha(self):
        from skillsync_workflow.status import git_blob_sha
        plan = self.write('plan.md', '# Contracts\nKeeps list.\n# Risks\nEmpty orders ok.\n# Evidence\nRegression tests.\n')
        report = self.report('readiness', '--files', 'src/api/orders.py', '--plan', 'plan.md', '--run-id', 'RUN-1')
        lines = (self.root / report['reportFile']).read_text(encoding='utf-8').splitlines()
        self.assertEqual(lines[0], 'Readiness: PASS')
        self.assertRegex(lines[1], r'^Data: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
        self.assertEqual(lines[2], 'Plan-sha: ' + git_blob_sha(plan.read_bytes()))

    def test_existing_report_is_overwritten(self):
        self.write('.skillsync/runs/RUN-1/readiness.md', 'Readiness: PASS\nstary\n')
        self.report('readiness', '--files', 'src/api/orders.py', '--run-id', 'RUN-1', code=2)
        text = (self.root / '.skillsync/runs/RUN-1/readiness.md').read_text(encoding='utf-8')
        self.assertTrue(text.startswith('Readiness: BLOCKED'))
        self.assertNotIn('stary', text)

    def test_error_after_valid_id_writes_blocked_with_reason(self):
        self.write('.skillsync/runs/RUN-1/readiness.md', 'Readiness: PASS\n')
        report = self.report('readiness', '--files', 'src/api/orders.py', '--plan', 'missing.md', '--run-id', 'RUN-1', code=2)
        self.assertEqual(report['status'], 'BLOCKED')
        text = (self.root / '.skillsync/runs/RUN-1/readiness.md').read_text(encoding='utf-8')
        self.assertTrue(text.startswith('Readiness: BLOCKED'))
        self.assertIn('## Powód', text)
        self.assertNotIn('Plan-sha', text)

    def test_without_flag_result_has_only_the_old_fields(self):
        report = self.report('readiness', '--files', 'src/api/orders.py', code=2)
        self.assertEqual(sorted(report), ['areas', 'files', 'issues', 'note', 'risk', 'risks', 'sections', 'spec', 'status'])

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
