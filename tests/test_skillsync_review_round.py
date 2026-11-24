"""`skillsync review-round start|freeze` (K9, K10, K14): the number of a review round, the run directory, the report file and the log.

Real CLI in an isolated project (`ComposeCase`). The report names are checked against the regexes of the status page, because the
label `Review ×N` is counted from the files.
"""
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from layers_helpers import ComposeCase, needs_os_symlinks  # noqa: E402
from skillsync_workflow import agent_runs, dashboard, review_rounds  # noqa: E402

RUN = '2026-11-23-core-1150-n16r'
SPEC = 'docs/specs/2026-11-23-feature-x.md'
PLAN = 'docs/plans/2026-11-23-feature-x.md'
REPORT = 'Wynik: CHANGES_REQUIRED\n\n- uwaga 1\n'


def spec_text(run=RUN, status='draft', plan=None):
    lines = ['# Spec: x', '', f'Status: {status}', 'Data: 2026-11-23']
    if run:
        lines.append(f'Przebieg: {run}')
    if plan:
        lines.append(f'Plan: {plan}')
    return '\n'.join(lines) + '\n\n## Problem\n\ntext\n'


def plan_text(run=RUN, status='in_progress'):
    return f'# Plan: x\n\nStatus: {status}\nData: 2026-11-23\nSpec: {SPEC}\nPrzebieg: {run}\n\n## Progress\n\n- [ ] S1 krok\n'


class RoundCase(ComposeCase):
    def project(self, name='p', spec=None, plan=None):
        project = self.new_project(name, skills=['alpha'])
        for rel, text in ((SPEC, spec), (PLAN, plan)):
            if text is not None:
                path = Path(project) / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding='utf-8')
        return project

    def go(self, project, action, *args, stdin=None, code=0):
        done = self.cli('review-round', action, str(project), *args, '--json')
        self.assertEqual(done.returncode, code, done.stdout + done.stderr)
        self.assertNotIn('Traceback', done.stderr)
        return json.loads(done.stdout) if code == 0 else done.stderr

    def freeze_text(self, project, text, *args, code=0):
        report = Path(project) / 'report.in'
        report.write_text(text, encoding='utf-8')
        return self.go(project, 'freeze', '--file', str(report), *args, code=code)

    def directory(self, project, run=RUN):
        return Path(project) / '.skillsync' / 'runs' / run

    def log(self, project, run=RUN):
        return agent_runs.read_events(self.directory(project, run))


class StartRound(RoundCase):
    def test_start_creates_the_directory_numbers_the_round_and_opens_a_run(self):
        project = self.project(spec=spec_text())
        done = self.go(project, 'start', '--kind', 'spec', '--eta', '14:35')
        self.assertEqual((done['round'], done['agent'], done['run']), (1, 'a-1', RUN))
        self.assertEqual(done['report'], f'.skillsync/runs/{RUN}/spec-review-1.md')
        self.assertTrue(self.directory(project).is_dir())
        start = self.log(project)[0]
        self.assertEqual((start['role'], start['kind'], start['profile'], start['round'], start['eta']), ('reviewer', 'review', 'spec', 1, '14:35'))

    def test_the_number_comes_from_the_files_and_ignores_decisions_and_verifier_reports(self):
        project = self.project(spec=spec_text())
        directory = self.directory(project)
        directory.mkdir(parents=True)
        for name in ('spec-review-1.md', 'spec-review-2.md', 'spec-review-1-decyzje.md', 'spec-review-9-verifier.md', 'spec-review-R3.md',
                     'plan-review-7.md', 'spec-review-4.log', 'notes.md'):
            (directory / name).write_text('x', encoding='utf-8')
        self.assertEqual(self.go(project, 'start', '--kind', 'spec')['round'], 4)   # R3 counts: the page counts `rN` too

    def test_a_second_start_closes_the_open_round_and_gets_the_same_number(self):
        project = self.project(spec=spec_text())
        first = self.go(project, 'start', '--kind', 'spec')
        second = self.go(project, 'start', '--kind', 'spec')
        self.assertEqual((first['round'], second['round']), (1, 1))
        self.assertEqual([(item['ev'], item['agent'], item.get('result')) for item in self.log(project)],
                         [('start', 'a-1', None), ('end', 'a-1', 'ABORTED'), ('start', 'a-2', None)])

    def test_the_code_rounds_are_numbered_per_step_and_a_step_is_refused_for_spec_and_plan(self):
        project = self.project(plan=plan_text())
        self.go(project, 'start', '--kind', 'code', '--step', 'F1.1')
        self.freeze_text(project, REPORT, '--kind', 'code', '--step', 'F1.1')
        self.assertEqual(self.go(project, 'start', '--kind', 'code', '--step', 'F1.1')['round'], 2)
        self.assertEqual(self.go(project, 'start', '--kind', 'code', '--step', 'F2.1')['round'], 1)
        self.assertEqual(self.go(project, 'start', '--kind', 'code')['report'], f'.skillsync/runs/{RUN}/code-review-1.md')
        self.assertIn('--step', self.go(project, 'start', '--kind', 'spec', '--step', 'F1.1', code=2))
        self.assertIn('--step', self.go(project, 'start', '--kind', 'plan', '--step', 'F1.1', code=2))
        for bad in ('a--b', '-a', 'a-', 'F2_1', 'a b'):
            with self.subTest(step=bad):
                self.go(project, 'start', '--kind', 'code', '--step', bad, code=2)

    def test_twenty_parallel_starts_of_different_steps_share_no_id(self):
        import subprocess
        project = self.project(plan=plan_text())
        code = ("import sys; sys.path.insert(0, %r); from skillsync_workflow import review_rounds; "
                "print(review_rounds.start(sys.argv[1], 'code', 'F' + sys.argv[2], %r)[2])" % (str(ROOT), RUN))
        procs = [subprocess.Popen([sys.executable, '-c', code, str(project), str(n)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for n in range(20)]
        ids = []
        for proc in procs:
            out, err = proc.communicate(timeout=120)
            self.assertEqual(proc.returncode, 0, err)
            ids.append(out.strip())
        self.assertEqual(len(set(ids)), 20, ids)
        self.assertEqual(len([item for item in self.log(project) if item['ev'] == 'start']), 20)


class FreezeRound(RoundCase):
    def test_freeze_writes_the_report_and_closes_the_run_with_the_result_of_its_first_line(self):
        project = self.project(spec=spec_text())
        self.go(project, 'start', '--kind', 'spec')
        done = self.freeze_text(project, REPORT, '--kind', 'spec')
        self.assertEqual((done['round'], done['result'], done['agent']), (1, 'CHANGES_REQUIRED', 'a-1'))
        self.assertEqual((self.directory(project) / 'spec-review-1.md').read_text(encoding='utf-8'), REPORT)
        end = self.log(project)[-1]
        self.assertEqual((end['ev'], end['agent'], end['result'], end['report']), ('end', 'a-1', 'CHANGES_REQUIRED', 'spec-review-1.md'))
        self.assertEqual(self.go(project, 'start', '--kind', 'spec')['round'], 2)   # the next round after a frozen report

    def test_freeze_reads_stdin_and_the_result_option_wins_over_the_text(self):
        import subprocess
        project = self.project(spec=spec_text())
        done = subprocess.run([sys.executable, str(ROOT / 'skillsync'), 'review-round', 'freeze', str(project), '--kind', 'spec',
                               '--result', 'PASS', '--json'], input='Wynik: CHANGES_REQUIRED\n'.encode(), capture_output=True,
                              env=dict(os.environ), timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)['result'], 'PASS')

    def test_freeze_without_start_works_and_leaves_a_closed_run(self):
        project = self.project(spec=spec_text())
        done = self.freeze_text(project, 'Wynik: PASS\n', '--kind', 'spec')
        self.assertEqual((done['round'], done['result']), (1, 'PASS'))
        self.assertEqual([(item['ev'], item.get('result')) for item in self.log(project)], [('start', None), ('end', 'PASS')])

    def test_an_existing_file_is_never_overwritten_the_next_free_number_is_taken(self):
        project = self.project(spec=spec_text())
        self.go(project, 'start', '--kind', 'spec')     # round 1 open
        existing = self.directory(project) / 'spec-review-1.md'
        existing.write_text('written by hand\n', encoding='utf-8')
        done = self.freeze_text(project, REPORT, '--kind', 'spec')
        self.assertEqual(done['round'], 2)
        self.assertEqual(existing.read_text(encoding='utf-8'), 'written by hand\n')
        self.assertEqual((self.directory(project) / 'spec-review-2.md').read_text(encoding='utf-8'), REPORT)

    def test_refusals_write_nothing(self):
        project = self.project(spec=spec_text())
        self.go(project, 'start', '--kind', 'spec')
        before = sorted(item.name for item in self.directory(project).iterdir())
        self.assertIn('Wynik', self.freeze_text(project, 'no result line here\n', '--kind', 'spec', code=2))
        self.assertIn('pusty', self.freeze_text(project, '   \n', '--kind', 'spec', '--result', 'PASS', code=2))
        self.assertIn('1 MiB', self.freeze_text(project, 'x' * (1024 * 1024 + 1), '--kind', 'spec', '--result', 'PASS', code=2))
        self.freeze_text(project, 'Wynik: DONE\n', '--kind', 'spec', code=2)   # not a result of a reviewer
        self.assertEqual(sorted(item.name for item in self.directory(project).iterdir()), before)
        self.assertEqual([item['ev'] for item in self.log(project)], ['start'])   # the round is still open

    def test_the_names_of_the_reports_are_counted_by_the_status_page(self):
        project = self.project(spec=spec_text(), plan=plan_text())
        for kind, step in (('spec', None), ('plan', None), ('code', None), ('code', 'F1.1'), ('code', 'S2-a.3')):
            args = ['--kind', kind] + (['--step', step] if step else [])
            self.go(project, 'start', *args)
            done = self.freeze_text(project, REPORT, *args)
            name = done['report'].rsplit('/', 1)[-1].lower()
            self.assertEqual(dashboard.review_rounds([name], kind), 1, name)


class RunChoice(RoundCase):
    def test_a_spec_without_a_plan_takes_the_run_of_its_przebieg_line(self):
        project = self.project(spec=spec_text(run='2026-11-23-the-run'))
        self.assertEqual(self.go(project, 'start', '--kind', 'spec')['run'], '2026-11-23-the-run')
        self.assertTrue(self.directory(project, '2026-11-23-the-run').is_dir())

    def test_the_first_existing_directory_wins_and_a_new_one_goes_under_the_first_name(self):
        project = self.project(spec=spec_text(run='2026-11-23-named', plan='docs/plans/2026-11-23-feature-x.md'))
        self.directory(project, '2026-11-23-feature-x').mkdir(parents=True)      # only the directory of the `Plan:` line exists
        self.assertEqual(self.go(project, 'start', '--kind', 'spec', '--spec', SPEC)['run'], '2026-11-23-feature-x')
        other = self.project('q', spec=spec_text(run='2026-11-23-named'))
        self.assertEqual(self.go(other, 'start', '--kind', 'spec', '--spec', SPEC)['run'], '2026-11-23-named')

    def test_a_spec_without_a_przebieg_line_uses_the_file_name(self):
        project = self.project(spec=spec_text(run=None))
        self.assertEqual(self.go(project, 'start', '--kind', 'spec')['run'], '2026-11-23-feature-x')

    def test_plan_and_code_rounds_take_the_run_of_the_current_plan_or_of_plan_option(self):
        project = self.project(plan=plan_text(run='2026-11-23-plan-run'))
        self.assertEqual(self.go(project, 'start', '--kind', 'plan')['run'], '2026-11-23-plan-run')
        self.assertEqual(self.go(project, 'start', '--kind', 'code', '--plan', PLAN)['run'], '2026-11-23-plan-run')

    def test_nothing_to_choose_from_is_a_refusal_that_names_run_id(self):
        project = self.project()
        for kind in ('spec', 'plan', 'code'):
            self.assertIn('podaj', self.go(project, 'start', '--kind', kind, code=2))
        self.assertEqual(self.go(project, 'start', '--kind', 'plan', '--run-id', RUN)['run'], RUN)

    def test_bad_run_ids_and_files_outside_the_project_are_refused(self):
        project = self.project(spec=spec_text())
        for bad in ('../x', 'a b', ''):
            self.go(project, 'start', '--kind', 'spec', '--run-id', bad, code=2)
        self.go(project, 'start', '--kind', 'spec', '--spec', '../outside.md', code=2)
        self.go(project, 'start', '--kind', 'spec', '--spec', 'docs/specs/missing.md', code=2)

    @needs_os_symlinks
    def test_a_symlink_in_the_place_of_the_run_directory_is_refused(self):
        project = self.project(spec=spec_text())
        runs = Path(project) / '.skillsync' / 'runs'
        runs.mkdir(parents=True)
        target = Path(project) / 'elsewhere'
        target.mkdir()
        (runs / RUN).symlink_to(target)
        self.go(project, 'start', '--kind', 'spec', '--run-id', RUN, code=2)
        self.assertEqual(list(target.iterdir()), [])


class Hardening(RoundCase):
    """The cases of the review of the code: the choice of the spec, the result line, a verifier among the runs, the files."""

    def two_specs(self, name='two', older_than_plan=False):
        project = self.project(name, spec=spec_text())
        other = Path(project) / 'docs/specs/2026-11-24-second.md'
        other.write_text(spec_text(run='2026-11-24-second'), encoding='utf-8')
        if older_than_plan:
            plan = Path(project) / PLAN
            plan.parent.mkdir(parents=True, exist_ok=True)
            plan.write_text(plan_text(), encoding='utf-8')
            for path in (Path(project) / SPEC, other):
                os.utime(path, (1, 1))   # both specs older than the plan
        return project

    def test_several_open_specs_without_a_plan_need_the_spec_option(self):
        project = self.two_specs()
        self.assertIn('--spec', self.go(project, 'start', '--kind', 'spec', code=2))
        self.assertEqual(self.go(project, 'start', '--kind', 'spec', '--spec', 'docs/specs/2026-11-24-second.md')['run'], '2026-11-24-second')

    def test_a_spec_older_than_the_current_plan_is_not_taken_for_act_one(self):
        # the specs are old, the plan is current and points to its own spec: the round goes to the run of that spec's plan, not to a stray spec
        project = self.two_specs('older', older_than_plan=True)
        done = self.go(project, 'start', '--kind', 'spec')
        self.assertEqual(done['run'], RUN)   # `Przebieg:` of the spec of the current plan

    def test_a_spec_directory_named_like_the_spec_is_found_as_the_page_finds_it(self):
        project = self.project(spec=spec_text(run=None))
        self.directory(project, 'older-2026-11-23-feature-x').mkdir(parents=True)   # `<prefix>-<spec slug>`
        self.assertEqual(self.go(project, 'start', '--kind', 'spec')['run'], 'older-2026-11-23-feature-x')

    def test_the_result_line_may_carry_backticks_and_a_second_different_line_is_a_refusal(self):
        project = self.project(spec=spec_text())
        done = self.freeze_text(project, 'Wynik: `PASS`\n\n- ok\n', '--kind', 'spec')
        self.assertEqual(done['result'], 'PASS')
        self.assertIn('Wynik', self.freeze_text(project, 'Wynik: PASS\n\nWynik: CHANGES_REQUIRED\n', '--kind', 'spec', code=2))
        self.assertEqual(self.freeze_text(project, 'Wynik: PASS\nStatus: BLOCKED\n', '--kind', 'spec')['result'], 'PASS')   # `Status:` is not a result line
        self.assertEqual(self.freeze_text(project, 'Result: **BLOCKED**\n', '--kind', 'spec')['result'], 'BLOCKED')

    def test_an_open_verifier_run_is_neither_taken_nor_closed_by_a_review_round(self):
        project = self.project(spec=spec_text())
        directory = self.directory(project)
        directory.mkdir(parents=True)
        agent = agent_runs.start(project, RUN, 'verifier', 'review', 'spec', None, 1)
        done = self.freeze_text(project, 'Wynik: PASS\n', '--kind', 'spec')
        self.assertNotEqual(done['agent'], agent)
        again = self.go(project, 'start', '--kind', 'spec')
        self.assertNotIn((agent, 'ABORTED'), [(item['agent'], item.get('result')) for item in self.log(project) if item['ev'] == 'end'])
        self.assertNotEqual(again['agent'], agent)

    def test_a_report_given_as_a_symlink_or_missing_is_refused(self):
        project = self.project(spec=spec_text())
        done = self.cli('review-round', 'freeze', str(project), '--kind', 'spec', '--file', str(Path(project) / 'nope.md'), '--result', 'PASS')
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        if os.name != 'nt':
            real = Path(project) / 'real.md'
            real.write_text('Wynik: PASS\n', encoding='utf-8')
            link = Path(project) / 'link.md'
            link.symlink_to(real)
            done = self.cli('review-round', 'freeze', str(project), '--kind', 'spec', '--file', str(link))
            self.assertEqual(done.returncode, 2, done.stdout + done.stderr)

    def test_a_bad_eta_leaves_no_directory(self):
        project = self.project(spec=spec_text(run='2026-11-23-eta-run'))
        self.go(project, 'start', '--kind', 'spec', '--eta', '25:99', code=2)
        self.assertFalse(self.directory(project, '2026-11-23-eta-run').exists())

    def test_twenty_parallel_starts_of_one_profile_and_step_keep_unique_ids_and_one_open_run(self):
        import subprocess
        project = self.project(plan=plan_text())
        code = ("import sys; sys.path.insert(0, %r); from skillsync_workflow import review_rounds; "
                "print(review_rounds.start(sys.argv[1], 'code', 'F1.1', %r)[2])" % (str(ROOT), RUN))
        procs = [subprocess.Popen([sys.executable, '-c', code, str(project)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(20)]
        ids = []
        for proc in procs:
            out, err = proc.communicate(timeout=120)
            self.assertEqual(proc.returncode, 0, err)
            ids.append(out.strip())
        self.assertEqual(len(set(ids)), 20, ids)
        events = self.log(project)
        self.assertEqual(len(agent_runs.open_runs(events)), 1)   # every earlier start was closed as ABORTED
        self.assertEqual({item['round'] for item in events if item['ev'] == 'start'}, {1})


class CommandNames(RoundCase):
    def test_review_round_does_not_collide_with_review(self):
        project = self.project()
        done = self.cli('review', str(project), '--files', 'README.md')
        self.assertNotIn('invalid choice', done.stderr)
        self.assertEqual(self.cli('review-round', '--help').returncode, 0)


if __name__ == '__main__':
    unittest.main()
