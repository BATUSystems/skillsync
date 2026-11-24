"""`skillsync plan-fresh` (CORE-1150-AUTOKEEP, A-2): a plan is as fresh as the checkpoints and the log of runs.

The open runs come from the real `agent-run` command; the checkpoint lines have the format of `measurement.md`
(`- HH:MM zrobione: ...; w toku: ...`); the times of the files are set with `os.utime` (the window rule compares them).
"""
import json
import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from layers_helpers import ComposeCase  # noqa: E402
from skillsync_workflow import plan_fresh  # noqa: E402

RUN = '2026-11-23-core-1150-autokeep'
PLAN = 'docs/plans/2026-11-23-core-1150-autokeep.md'
SHA = 'abc1234def0'
HOUR = 3600


def plan_text(steps=('[ ] A-1 jeden', '[ ] A-2 dwa', '[ ] A-3 trzy'), status='in_progress', run=RUN, heading='Kroki', phases=None):
    lines = ['# Plan', '', f'Status: {status}']
    if run:
        lines.append(f'Przebieg: {run}')
    lines += ['', f'## {heading}', '']
    if phases:
        for name, items in phases:
            lines += [f'### {name}', ''] + [f'- {item}' for item in items] + ['']
    else:
        lines += [f'- {item}' for item in steps]
    return '\n'.join(lines) + '\n'


class FreshCase(ComposeCase):
    def setUp(self):
        super().setUp()
        self.project = self.new_project('p', skills=['alpha'])
        self.now = time.time()

    def put_plan(self, text=None, age=2 * HOUR):
        path = self.project / PLAN
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text or plan_text(), encoding='utf-8')
        os.utime(path, (self.now - age, self.now - age))
        return path

    def run_dir(self, name=RUN):
        directory = self.project / '.skillsync' / 'runs' / name
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def checkpoint(self, *lines, age=60, name=RUN, header='RUN=2026-11-23T09:00:00+01:00'):
        path = self.run_dir(name) / 'checkpoints.md'
        path.write_text('\n'.join([header, *lines]) + '\n', encoding='utf-8')
        os.utime(path, (self.now - age, self.now - age))

    def agent(self, *args, name=RUN):
        self.run_dir(name)
        done = self.cli('agent-run', args[0], str(self.project), '--run-id', name, *args[1:], '--json')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return json.loads(done.stdout)

    def start(self, step='A-1', role='implementer', kind='run', name=RUN):
        args = ['start', '--role', role, '--kind', kind]
        if step:
            args += ['--step', step]
        return self.agent(*args, name=name)['agent']

    def analyze(self, allowed=0, now=None):
        return plan_fresh.analyze(self.project, now=self.now if now is None else now, allowed=allowed)

    def codes(self, report=None, blocking=True):
        report = report or self.analyze()
        return sorted((item['code'], item['step']) for item in report['findings'] if item['blocking'] or not blocking)


class ReportedUnchecked(FreshCase):
    def test_zrobione_with_a_sha_and_an_open_step_blocks(self):
        self.put_plan()
        self.checkpoint(f'- 10:00 zrobione: A-1 (impl {SHA}); w toku: A-2')
        report = self.analyze()
        self.assertEqual(self.codes(report), [('step-reported-unchecked', 'A-1')])
        self.assertTrue(report['blocking'])
        self.assertEqual(report['findings'][0]['plan'], PLAN)

    def test_a_checked_step_does_not_block(self):
        self.put_plan(plan_text(('[x] A-1 jeden', '[ ] A-2 dwa')))
        self.checkpoint(f'- 10:00 zrobione: A-1 ({SHA})')
        self.assertEqual(self.codes(), [])

    def test_zrobione_without_a_sha_is_waiting_for_the_review(self):
        self.put_plan()
        self.checkpoint('- 10:00 zrobione: A-1 (implementer DONE, review trwa); w toku: A-1 (review)')
        self.assertEqual(self.codes(), [])

    def test_a_plain_seven_digit_number_is_not_a_sha(self):
        self.put_plan()
        self.checkpoint('- 10:00 zrobione: A-1 (1234567 testow)')
        self.assertEqual(self.codes(), [])

    def test_a_sha_without_word_boundaries_is_not_a_sha(self):
        self.put_plan()
        self.checkpoint('- 10:00 zrobione: A-1 (xabcdef1234x)')
        self.assertEqual(self.codes(), [])

    def test_a_sha_in_another_element_does_not_belong_to_the_step(self):
        self.put_plan()
        self.checkpoint(f'- 10:00 zrobione: A-1 (bez sha), A-2 ({SHA})')
        self.assertEqual(self.codes(), [('step-reported-unchecked', 'A-2')])

    def test_a_step_outside_the_plan_is_ignored(self):
        self.put_plan()
        self.checkpoint(f'- 10:00 zrobione: Z-9 ({SHA})')
        self.assertEqual(self.codes(), [])

    def test_only_the_newest_line_counts(self):
        self.put_plan()
        self.checkpoint(f'- 10:00 zrobione: A-1 ({SHA})', '- 10:20 w toku: A-2')
        self.assertEqual(self.codes(), [])

    def test_english_keyword_works_too(self):
        self.put_plan()
        self.checkpoint(f'- 10:00 done: A-1 ({SHA})')
        self.assertEqual(self.codes(), [('step-reported-unchecked', 'A-1')])

    def test_phase_plan_with_progress_section(self):
        self.put_plan(plan_text(heading='Progress', phases=[('F1', ['[ ] F1.1 a', '[ ] F1.2 b'])]))
        self.checkpoint(f'- 10:00 zrobione: F1.2 ({SHA})')
        self.assertEqual(self.codes(), [('step-reported-unchecked', 'F1.2')])


class RunningUnmarked(FreshCase):
    def test_open_run_without_w_toku_blocks(self):
        self.put_plan()
        agent = self.start('A-2')
        self.checkpoint('- 10:00 w toku: A-1')
        report = self.analyze()
        self.assertEqual(self.codes(report), [('step-running-unmarked', 'A-2')])
        self.assertEqual(report['findings'][0]['agent'], agent)

    def test_marked_step_does_not_block(self):
        self.put_plan()
        self.start('A-2')
        self.checkpoint('- 10:00 w toku: A-1, A-2 (review)')
        self.assertEqual(self.codes(), [])

    def test_the_phase_covers_its_substeps(self):
        self.put_plan(plan_text(heading='Progress', phases=[('F1', ['[ ] F1.1 a', '[ ] F1.2 b'])]))
        self.start('F1.2')
        self.checkpoint('- 10:00 w toku: F1')
        self.assertEqual(self.codes(), [])

    def test_a_substep_does_not_cover_the_phase_or_a_prefix_lookalike(self):
        self.put_plan(plan_text(steps=('[ ] A-1 a', '[ ] A-10 b')))
        self.start('A-10')
        self.checkpoint('- 10:00 w toku: A-1')
        self.assertEqual(self.codes(), [('step-running-unmarked', 'A-10')])

    def test_a_hand_edited_journal_with_an_unhashable_step_does_not_crash(self):
        self.put_plan()
        self.start('A-1')
        journal = self.run_dir() / 'agents.jsonl'
        stamp = time.strftime('%Y-%m-%dT%H:%M:%S%z', time.localtime(self.now - 60))
        with open(journal, 'a', encoding='utf-8') as handle:
            handle.write(json.dumps({'v': 1, 't': stamp, 'ev': 'start', 'agent': 'a-99', 'role': 'implementer', 'kind': 'run', 'step': ['A-2']}) + '\n')
        self.assertEqual(self.codes(), [('step-running-unmarked', 'A-1')])

    def test_missing_checkpoints_block(self):
        self.put_plan()
        self.start('A-1')
        self.assertEqual(self.codes(), [('step-running-unmarked', 'A-1')])

    def test_ended_run_does_not_block(self):
        self.put_plan()
        agent = self.start('A-1')
        self.agent('end', '--agent', agent, '--result', 'DONE')
        self.assertEqual(self.codes(), [])

    def test_a_review_run_is_not_a_step_run(self):
        self.put_plan()
        self.start('A-1', role='reviewer', kind='review')
        self.assertEqual(self.codes(), [])

    def test_a_run_of_a_step_outside_the_plan_or_without_a_step_is_ignored(self):
        self.put_plan()
        self.start('Z-9')
        self.start(None, role='explore')
        self.assertEqual(self.codes(), [])

    def test_implementer_done_with_review_running_and_no_zrobione_does_not_block(self):
        self.put_plan()
        implementer = self.start('A-1')
        self.agent('end', '--agent', implementer, '--result', 'DONE')
        self.start('A-1', role='reviewer', kind='review')
        self.checkpoint('- 10:00 w toku: A-1 (review)')
        self.assertEqual(self.codes(), [])

    def test_two_runs_of_one_step_give_one_finding(self):
        self.put_plan()
        self.start('A-1')
        self.start('A-1', role='test-writer')
        self.assertEqual(self.codes(), [('step-running-unmarked', 'A-1')])


class Window(FreshCase):
    def test_checkpoint_older_than_the_plan_is_not_counted(self):
        self.put_plan(age=10)   # the plan was edited (10 s ago) after the checkpoint (300 s ago)
        self.checkpoint(f'- 10:00 zrobione: A-1 ({SHA})', age=300)
        self.assertEqual(self.codes(), [])

    def test_checkpoint_older_than_six_hours_is_not_counted(self):
        self.put_plan(age=9 * HOUR)
        self.checkpoint(f'- 10:00 zrobione: A-1 ({SHA})', age=7 * HOUR)
        self.assertEqual(self.codes(), [])

    def test_checkpoint_inside_the_window_counts(self):
        self.put_plan(age=9 * HOUR)
        self.checkpoint(f'- 10:00 zrobione: A-1 ({SHA})', age=5 * HOUR)
        self.assertEqual(self.codes(), [('step-reported-unchecked', 'A-1')])

    def test_run_older_than_the_plan_edit_is_not_counted(self):
        self.put_plan(age=0)
        os.utime(self.project / PLAN, (self.now + 120, self.now + 120))
        self.start('A-1')
        self.assertEqual(self.codes(), [])

    def test_run_older_than_six_hours_is_not_counted(self):
        self.put_plan(age=9 * HOUR)
        self.start('A-1')
        self.assertEqual(self.codes(self.analyze(now=self.now + 7 * HOUR)), [])

    def test_open_run_inside_the_window_counts_after_a_plan_edit_before_it(self):
        self.put_plan(age=HOUR)
        self.start('A-1')
        self.assertEqual(self.codes(), [('step-running-unmarked', 'A-1')])


class Selection(FreshCase):
    def test_package_directory_of_the_run_is_read(self):
        self.put_plan()
        self.checkpoint(f'- 10:00 zrobione: A-1 ({SHA})', name=RUN + '.autokeep')
        self.assertEqual(self.codes(), [('step-reported-unchecked', 'A-1')])

    def test_package_directory_of_the_run_gets_the_open_runs_too(self):
        self.put_plan()
        self.start('A-1', name=RUN + '.autokeep')
        self.assertEqual(self.codes(), [('step-running-unmarked', 'A-1')])

    def test_a_directory_of_another_run_with_the_same_prefix_is_not_read(self):
        self.put_plan()
        self.checkpoint(f'- 10:00 zrobione: A-1 ({SHA})', name=RUN + '-other')
        self.assertEqual(self.codes(), [])

    def test_only_in_progress_plans_count(self):
        for status in ('done', 'approved', 'draft'):
            self.put_plan(plan_text(status=status))
            self.checkpoint(f'- 10:00 zrobione: A-1 ({SHA})')
            self.assertEqual(self.codes(), [], status)

    def test_plan_without_a_run_line_is_ignored(self):
        self.put_plan(plan_text(run=None))
        self.checkpoint(f'- 10:00 zrobione: A-1 ({SHA})')
        self.assertEqual(self.codes(), [])

    def test_no_plans_means_no_findings(self):
        report = self.analyze()
        self.assertEqual((report['findings'], report['blocking'], report['plans']), ([], False, []))

    def test_readme_in_plans_is_ignored(self):
        path = self.project / 'docs' / 'plans' / 'README.md'
        path.parent.mkdir(parents=True)
        path.write_text(plan_text(), encoding='utf-8')
        self.checkpoint(f'- 10:00 zrobione: A-1 ({SHA})')
        self.assertEqual(self.analyze()['plans'], [])


class Idle(FreshCase):
    PHASES = [('F1', ['[x] F1.1 a', '[ ] F1.2 b', '[ ] F1.3 c']), ('F2', ['[ ] F2.1 d', '[ ] F2.2 e'])]

    def test_two_open_steps_of_the_first_open_phase_and_a_free_place_inform(self):
        self.put_plan(plan_text(heading='Progress', phases=self.PHASES))
        self.checkpoint('- 10:00 w toku: F1.1')
        report = self.analyze(allowed=3)
        self.assertFalse(report['blocking'])
        self.assertEqual(report['idle'], {'allowed': 3, 'steps': ['F1.2', 'F1.3'], 'plan': PLAN})
        self.assertEqual(self.codes(report, blocking=False), [('idle-capacity', 'F1.2, F1.3')])

    def test_no_free_place_no_idle(self):
        self.put_plan(plan_text(heading='Progress', phases=self.PHASES))
        report = self.analyze(allowed=0)
        self.assertEqual((report['findings'], report['idle']['steps']), ([], []))

    def test_steps_in_w_toku_are_not_idle(self):
        self.put_plan(plan_text(heading='Progress', phases=self.PHASES))
        self.checkpoint('- 10:00 w toku: F1.2')
        self.assertEqual(self.analyze(allowed=3)['idle']['steps'], [])

    def test_the_phase_in_w_toku_covers_its_steps(self):
        self.put_plan(plan_text(heading='Progress', phases=self.PHASES))
        self.checkpoint('- 10:00 w toku: F1')
        self.assertEqual(self.analyze(allowed=3)['idle']['steps'], [])

    def test_only_the_first_phase_with_open_steps_counts(self):
        self.put_plan(plan_text(heading='Progress', phases=[('F1', ['[x] F1.1 a', '[ ] F1.2 b']), ('F2', ['[ ] F2.1 c', '[ ] F2.2 d'])]))
        self.assertEqual(self.analyze(allowed=3)['idle']['steps'], [])

    def test_flat_plan_is_one_phase(self):
        self.put_plan()
        self.assertEqual(self.analyze(allowed=2)['idle']['steps'], ['A-1', 'A-2', 'A-3'])

    def test_resources_are_read_when_allowed_is_not_injected(self):
        self.put_plan()
        report = plan_fresh.analyze(self.project, now=self.now)
        self.assertGreaterEqual(report['idle']['allowed'], 1)   # nothing runs here: at least one place


class DoctorHelper(FreshCase):
    def test_plan_findings_lists_only_blocking_ones(self):
        self.put_plan()
        self.checkpoint(f'- 10:00 zrobione: A-1 ({SHA})')
        found = plan_fresh.plan_findings(self.project)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], PLAN)
        self.assertIn('step-reported-unchecked A-1', found[0][1])

    def test_plan_findings_without_plans_is_empty(self):
        self.assertEqual(plan_fresh.plan_findings(self.project), [])

    def test_plan_findings_never_raises(self):
        self.assertEqual(plan_fresh.plan_findings(self.project / 'no-such-dir'), [])


BAD = ('[ ] FT.1 pierwszy', '[ ] FT.2 drugi')
GOOD = ('[ ] F1.1 a', '[ ] F1.2 b')


class StepIdWarnings(FreshCase):
    """CORE-1160-STEPID K2: non-blocking warnings about step ids in the plan and in the newest checkpoint."""

    def put_second(self, text, name='2026-11-23-second.md'):
        path = self.project / 'docs' / 'plans' / name
        path.write_text(text, encoding='utf-8')
        os.utime(path, (self.now - 2 * HOUR, self.now - 2 * HOUR))

    def found(self, code):
        return [item for item in self.analyze()['findings'] if item['code'] == code]

    def test_plan_with_only_unparsed_steps_is_reported(self):
        self.put_plan(plan_text(steps=BAD))
        found = self.found('step-id-unparsed')
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0]['blocking'])
        self.assertEqual(found[0]['count'], 2)

    def test_aggregation_shows_three_fragments_and_the_rest(self):
        self.put_plan(plan_text(steps=('[ ] FT.1 aa', '[ ] FT.2 bb', '[ ] FT.3 cc')))
        detail = self.found('step-id-unparsed')[0]['detail']
        self.assertNotIn('more', detail)
        self.put_plan(plan_text(steps=tuple(f'[ ] FT.{n} item{n}' for n in range(1, 6))))
        found = self.found('step-id-unparsed')
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['count'], 5)
        self.assertIn('and 2 more', found[0]['detail'])
        self.assertNotIn('item4', found[0]['detail'])

    def test_skipped_checkpoint_elements(self):
        self.put_plan(plan_text(steps=GOOD))
        self.checkpoint('- 09:00 w toku: F1.1', '- 10:00 w toku: review R1, F1.2')
        found = self.found('checkpoint-id-skipped')
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0]['blocking'])

    def test_older_checkpoint_lines_are_not_read(self):
        self.put_plan(plan_text(steps=GOOD))
        self.checkpoint('- 09:00 w toku: review R1, F1.2', '- 10:00 w toku: F1.2')
        self.assertEqual(self.found('checkpoint-id-skipped'), [])

    def test_unknown_checkpoint_id(self):
        self.put_plan(plan_text(steps=GOOD))
        self.checkpoint('- 10:00 zrobione: K2, O113')
        found = self.found('checkpoint-id-unknown')
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0]['blocking'])
        self.assertIn('K2', found[0]['detail'])

    def test_known_step_and_phase_are_not_unknown(self):
        self.put_plan(plan_text(steps=GOOD))
        self.checkpoint('- 10:00 zrobione: F1.1; w toku: F1')
        self.assertEqual(self.found('checkpoint-id-unknown'), [])

    def test_no_unknown_without_recognised_steps(self):
        self.put_plan(plan_text(steps=BAD))
        self.checkpoint('- 10:00 zrobione: K2, O113')
        self.assertEqual(self.found('checkpoint-id-unknown'), [])

    def test_id_of_the_second_plan_of_the_run_is_known_and_reported_once(self):
        self.put_plan(plan_text(steps=GOOD))
        self.put_second(plan_text(steps=('[ ] G1.1 x',)))
        self.checkpoint('- 10:00 zrobione: G1.1, K2; w toku: review R1')
        unknown = self.found('checkpoint-id-unknown')
        self.assertEqual(len(unknown), 1)
        self.assertIn('K2', unknown[0]['detail'])
        self.assertNotIn('G1.1', unknown[0]['detail'])
        self.assertEqual(len(self.found('checkpoint-id-skipped')), 1)

    def test_done_plan_has_no_such_findings(self):
        self.put_plan(plan_text(steps=BAD, status='done'))
        self.checkpoint('- 10:00 zrobione: K2; w toku: review R1')
        self.assertEqual([i for i in self.analyze()['findings'] if i['code'] in plan_fresh.STEP_ID_CODES], [])

    def test_clean_plan_has_no_new_codes(self):
        self.put_plan(plan_text(steps=GOOD))
        self.checkpoint('- 10:00 w toku: F1.1; zrobione: F1.2')
        self.assertEqual([i for i in self.analyze()['findings'] if i['code'] in plan_fresh.STEP_ID_CODES], [])

    def test_new_codes_are_not_blocking_and_render_as_info(self):
        self.put_plan(plan_text(steps=BAD))
        report = self.analyze()
        self.assertFalse(report['blocking'])
        lines = [line for line in plan_fresh.render(report).splitlines() if 'step-id-unparsed' in line]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith('info '))

    def test_step_id_findings_pairs(self):
        self.put_plan(plan_text(steps=BAD))
        found = plan_fresh.step_id_findings(self.project)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], PLAN)
        self.assertIn('step-id-unparsed', found[0][1])

    def test_step_id_findings_ignores_blocking_codes(self):
        self.put_plan()
        self.checkpoint(f'- 10:00 zrobione: A-1 ({SHA})')
        self.assertEqual(plan_fresh.step_id_findings(self.project), [])

    def test_step_id_findings_never_raises(self):
        self.assertEqual(plan_fresh.step_id_findings(self.project / 'no-such-dir'), [])


class CommandLine(FreshCase):
    def test_json_and_exit_zero_with_findings(self):
        path = self.put_plan(age=3600)
        self.checkpoint(f'- 10:00 zrobione: A-1 ({SHA})', age=0)
        self.assertTrue(path.is_file())
        done = self.cli('plan-fresh', str(self.project), '--json')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        data = json.loads(done.stdout)
        self.assertTrue(data['blocking'])
        self.assertEqual(set(data), {'schema', 'plans', 'findings', 'blocking', 'idle'})
        self.assertEqual(data['findings'][0]['code'], 'step-reported-unchecked')

    def test_text_output(self):
        self.put_plan(age=3600)
        self.checkpoint(f'- 10:00 zrobione: A-1 ({SHA})', age=0)
        done = self.cli('plan-fresh', str(self.project))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('step-reported-unchecked A-1', done.stdout)

    def test_no_plan_exit_zero(self):
        done = self.cli('plan-fresh', str(self.project), '--json')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(json.loads(done.stdout)['blocking'])

    def test_bad_usage_exit_two(self):
        done = self.cli('plan-fresh', str(self.project), '--nope')
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)


if __name__ == '__main__':
    unittest.main()
