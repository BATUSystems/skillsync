"""K12: `skillsync status` and `doctor` remark (`info`) that a plan of act I has no saved spec review.

A plan counts when it is `approved` or `in_progress` and has `Spec:` and `Spec-sha:`; the trace is a report `spec-review-N.md`, an
ended spec round in `agents.jsonl` or a checkpoint record of a step `spec-review`; a run directory that is gone is no signal unless the plan
is the current one. Exit codes and `needsHuman` do not change.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_skillsync_layers_doctor as base  # noqa: E402

RUN = '2026-11-23-core-1150-n16r'
PLAN = 'docs/plans/2026-11-23-x.md'
SPEC = 'docs/specs/2026-11-23-x.md'
SHA = 'a' * 40


def plan_text(status='in_progress', spec=True, sha=True, run=RUN):
    lines = ['# Plan: x', '', f'Status: {status}', 'Data: 2026-11-23']
    if spec:
        lines.append(f'Spec: {SPEC}')
    if sha:
        lines.append(f'Spec-sha: {SHA}')
    if run:
        lines.append(f'Przebieg: {run}')
    return '\n'.join(lines) + '\n\n## Progress\n\n- [ ] S1 krok\n'


class GapCase(base.DoctorCase):
    def project(self, name='p', plan=None, run_dir=True, files=(), log=None, record=None, extra_plans=()):
        self.layers()
        project = self.new_project(name, skills=['alpha'])
        self.link_ok(project)
        for rel, text in ((PLAN, plan if plan is not None else plan_text()), (SPEC, '# Spec: x\n\nStatus: approved\n')):
            path = Path(project) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        for rel, text in extra_plans:
            (Path(project) / rel).write_text(text, encoding='utf-8')
        runs = Path(project) / '.skillsync' / 'runs'
        if run_dir:
            (runs / RUN).mkdir(parents=True)
            for name in files:
                (runs / RUN / name).write_text('x', encoding='utf-8')
            if log:
                (runs / RUN / 'agents.jsonl').write_text('\n'.join(json.dumps(item) for item in log) + '\n', encoding='utf-8')
        if record:
            runs.mkdir(parents=True, exist_ok=True)
            (runs / f'{RUN}.json').write_text(json.dumps({'schema_version': 1, 'checkpoints': [{'step': record}]}), encoding='utf-8')
        return project

    def plans(self, project):
        done = self.cli('status', str(project), '--json')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return {item['path']: item for item in json.loads(done.stdout)['plans']}

    def doctor_infos(self, project):
        report = self.doctor(project)
        return [item for item in report['findings'] if 'saved review round' in item['reason']], report['status']


class Remark(GapCase):
    def test_a_plan_of_act_one_without_a_spec_review_is_remarked_in_status_and_doctor(self):
        project = self.project()
        plan = self.plans(project)[PLAN]
        self.assertEqual(plan['reviewRounds'], {'missing': ['spec']})
        infos, code = self.doctor_infos(project)
        self.assertEqual([(item['level'], item['path']) for item in infos], [('info', PLAN)])
        self.assertIn('review-round', infos[0]['reason'])
        # the same project with a saved report: no remark, and nothing else about the plan differs
        saved = self.project('q', files=('spec-review-1.md',))
        other = self.plans(saved)[PLAN]
        self.assertNotIn('reviewRounds', other)
        self.assertEqual((plan['needsHuman'], plan['status']), (other['needsHuman'], other['status']))
        self.assertEqual(self.doctor_infos(saved)[0], [])
        self.assertEqual(code, self.doctor_infos(saved)[1])   # the remark changes no status of `doctor`

    def test_every_kind_of_trace_silences_it(self):
        started = {'v': 1, 'ev': 'start', 'agent': 'a-1', 'role': 'reviewer', 'kind': 'review', 'profile': 'spec', 'round': 1, 't': 'x'}
        ended = {'v': 1, 'ev': 'end', 'agent': 'a-1', 'result': 'PASS', 't': 'x'}
        cases = {
            'file': dict(files=('spec-review-2.md',)),
            'file with a step-like name': dict(files=('spec-review-r1.md',)),
            'ended round in the log': dict(log=[started, ended]),
            'checkpoint record': dict(record='spec-review'),
        }
        for name, kwargs in cases.items():
            with self.subTest(name):
                self.assertNotIn('reviewRounds', self.plans(self.project(name.replace(' ', '-'), **kwargs))[PLAN])
        # these are NOT a trace: an open round, a decisions report, a report of another kind
        for name, kwargs in {'open round': dict(log=[started]), 'decisions only': dict(files=('spec-review-1-decyzje.md',)),
                             'plan review': dict(files=('plan-review-1.md',))}.items():
            with self.subTest(name):
                self.assertIn('reviewRounds', self.plans(self.project(name.replace(' ', '-'), **kwargs))[PLAN])

    def test_only_approved_and_in_progress_plans_with_spec_and_spec_sha_count(self):
        for name, text in {'done': plan_text('done'), 'draft': plan_text('draft'), 'no spec line': plan_text(spec=False),
                           'no spec-sha': plan_text(sha=False), 'no run': plan_text(run=None)}.items():
            with self.subTest(name):
                self.assertNotIn('reviewRounds', self.plans(self.project(name.replace(' ', '-'), plan=text))[PLAN])
        self.assertIn('reviewRounds', self.plans(self.project('approved', plan=plan_text('approved')))[PLAN])

    def test_a_missing_run_directory_is_a_signal_only_for_the_current_plan(self):
        # the only plan is the current one: remarked even without a directory
        project = self.project(run_dir=False)
        self.assertIn('reviewRounds', self.plans(project)[PLAN])
        # an older plan next to a current one, no directory: history, no remark
        older = plan_text('approved', run='2026-11-01-old')
        current = plan_text('in_progress')
        project = self.project('two', plan=current, run_dir=False, extra_plans=[('docs/plans/2026-11-01-old.md', older)])
        rows = self.plans(project)
        self.assertNotIn('reviewRounds', rows['docs/plans/2026-11-01-old.md'])
        self.assertIn('reviewRounds', rows[PLAN])

    def test_a_plan_without_the_run_id_pattern_does_not_break_status(self):
        project = self.project(plan=plan_text(run='../x'))
        self.assertNotIn('reviewRounds', self.plans(project)[PLAN])


if __name__ == '__main__':
    unittest.main()
