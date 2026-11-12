"""`timing` of the dashboard: work time and waiting time per step and per plan phase (deterministic times, no clock reads)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillsync_workflow import dashboard  # noqa: E402

START = '2026-11-11T10:00:00+01:00'      # 09:00 UTC
PHASES = [{'name': 'F0: Fundament', 'label': 'F0', 'steps': [{'id': 'F0.1', 'done': True}, {'id': 'F0.2', 'done': True}]},
          {'name': 'F1: Ekrany', 'label': 'F1', 'steps': [{'id': 'F1.1', 'done': False}]}]


def cp(time, text):
    return {'time': time, 'text': text}


def approval(word, at):
    return {'schema': 1, 'at': at, 'word': word, 'sha': 'a' * 40, 'by': 'user'}


def timing(checkpoints, approvals=(), phases=PHASES, start=START):
    return dashboard.build_timing(start, checkpoints, list(approvals), phases)


def step(report, sid):
    return next(item for item in report['steps'] if item['id'] == sid)


class WaitingWindows(unittest.TestCase):
    def test_a_window_closes_at_the_named_consent_of_the_approvals_file(self):
        report = timing([cp('10:00', 'w toku: F0.1'), cp('10:30', 'zrobione: F0.1; w toku: F0.2; czeka: GO implement'),
                         cp('11:10', 'zrobione: F0.2')], [approval('GO implement', '2026-11-11T09:50:00Z')])
        (window,) = report['windows']
        self.assertEqual((window['startedAt'], window['endedAt'], window['seconds'], window['source']),
                         ('2026-11-11T10:30:00+01:00', '2026-11-11T10:50:00+01:00', 1200, 'approval'))
        self.assertFalse(window['unknown'])

    def test_without_a_matching_entry_the_next_checkpoint_closes_the_window(self):
        report = timing([cp('10:00', 'w toku: F0.1; czeka: GO plan'), cp('10:20', 'zrobione: F0.1')])
        (window,) = report['windows']
        self.assertEqual((window['seconds'], window['source']), (1200, 'checkpoint'))

    def test_a_consent_for_another_word_or_given_before_the_window_does_not_close_it(self):
        report = timing([cp('10:00', 'w toku: F0.1'), cp('10:10', 'czeka: GO plan'), cp('10:40', 'zrobione: F0.1')],
                        [approval('CP', '2026-11-11T09:20:00Z'), approval('GO plan', '2026-11-11T09:05:00Z')])
        (window,) = report['windows']
        self.assertEqual((window['seconds'], window['source']), (1800, 'checkpoint'))

    def test_a_wait_that_names_no_consent_runs_to_the_next_checkpoint(self):
        report = timing([cp('10:00', 'czeka: odpowiedź na Q3'), cp('10:05', 'w toku: F0.1')], [approval('GO plan', '2026-11-11T09:02:00Z')])
        self.assertEqual((report['windows'][0]['seconds'], report['windows'][0]['source']), (300, 'checkpoint'))

    def test_consecutive_waiting_checkpoints_are_one_window(self):
        report = timing([cp('10:00', 'czeka: GO plan'), cp('10:10', 'w toku: brak; czeka: GO plan'), cp('10:30', 'zrobione: F0.1')])
        self.assertEqual(len(report['windows']), 1)
        self.assertEqual(report['windows'][0]['seconds'], 1800)

    def test_a_new_wait_after_a_consent_opens_a_new_window(self):
        report = timing([cp('10:00', 'w toku: F0.1; czeka: GO implement'), cp('10:30', 'w toku: F0.1; czeka: odpowiedź Q3'),
                         cp('10:50', 'zrobione: F0.1')], [approval('GO implement', '2026-11-11T09:15:00Z')])
        self.assertEqual([(w['startedAt'][11:16], w['endedAt'][11:16], w['seconds'], w['source']) for w in report['windows']],
                         [('10:00', '10:15', 900, 'approval'), ('10:30', '10:50', 1200, 'checkpoint')])
        self.assertEqual((step(report, 'F0.1')['waitSeconds'], step(report, 'F0.1')['workSeconds']), (2100, 900))

    def test_a_wait_still_open_at_the_next_waiting_checkpoint_stays_one_window(self):
        report = timing([cp('10:00', 'czeka: GO implement'), cp('10:30', 'czeka: GO implement'), cp('10:40', 'w toku: F0.1')],
                        [approval('GO implement', '2026-11-11T09:35:00Z')])
        self.assertEqual([(w['seconds'], w['source']) for w in report['windows']], [(2100, 'approval')])

    def test_a_window_that_nothing_closes_is_unknown_never_estimated(self):
        report = timing([cp('10:00', 'w toku: F0.1'), cp('10:30', 'zrobione: F0.1; czeka: CP')])
        (window,) = report['windows']
        self.assertTrue(window['unknown'])
        self.assertIsNone(window['seconds'])
        self.assertIsNone(window['endedAt'])
        self.assertIsNone(window['source'])
        self.assertEqual(report['totals']['unknownWindows'], 1)
        self.assertFalse(report['totals']['complete'])
        self.assertEqual(step(report, 'F0.1')['workSeconds'], 1800)       # the finish mark is the start of the window

    def test_the_clock_that_goes_back_is_the_next_day(self):
        report = timing([cp('23:50', 'w toku: F0.1; czeka: GO plan'), cp('00:10', 'zrobione: F0.1')], start='2026-11-11T23:40:00+01:00')
        self.assertEqual(report['windows'][0]['seconds'], 1200)
        self.assertEqual(report['windows'][0]['endedAt'], '2026-11-12T00:10:00+01:00')


class StepsAndStages(unittest.TestCase):
    CHECKPOINTS = [cp('10:00', 'w toku: F0.1'), cp('10:30', 'zrobione: F0.1; w toku: F0.2; czeka: GO implement'),
                   cp('11:10', 'zrobione: F0.2; w toku: F1.1')]
    APPROVALS = [approval('GO implement', '2026-11-11T09:50:00Z')]

    def test_work_is_start_to_finish_minus_the_windows_inside(self):
        report = timing(self.CHECKPOINTS, self.APPROVALS)
        first, second = step(report, 'F0.1'), step(report, 'F0.2')
        self.assertEqual((first['workSeconds'], first['waitSeconds'], first['unknown']), (1800, 0, False))
        self.assertEqual((second['workSeconds'], second['waitSeconds'], second['unknown']), (1200, 1200, False))
        self.assertEqual((second['startedAt'], second['finishedAt']), ('2026-11-11T10:30:00+01:00', '2026-11-11T11:10:00+01:00'))

    def test_stage_sums_and_totals(self):
        report = timing(self.CHECKPOINTS, self.APPROVALS)
        stage = report['stages'][0]
        self.assertEqual((stage['phase'], stage['workSeconds'], stage['waitSeconds'], stage['unknownSteps'], stage['complete']),
                         ('F0', 3000, 1200, 0, True))
        self.assertEqual(report['totals']['waitSeconds'], 1200)
        self.assertEqual(report['totals']['windows'], 1)

    def test_a_started_step_without_a_finish_is_unknown_with_a_reason(self):
        report = timing(self.CHECKPOINTS, self.APPROVALS)
        open_step = step(report, 'F1.1')
        self.assertEqual((open_step['workSeconds'], open_step['unknown'], open_step['reason']), (None, True, 'no-finish'))
        self.assertEqual(report['totals']['unknownSteps'], 1)
        self.assertFalse(report['totals']['complete'])
        self.assertEqual(report['stages'][1]['unknownSteps'], 1)
        self.assertFalse(report['stages'][1]['complete'])

    def test_a_done_step_without_marks_is_unknown_and_a_planned_step_is_left_out(self):
        report = timing([cp('10:00', 'w toku: F0.1'), cp('10:10', 'zrobione: F0.1')])
        self.assertEqual((step(report, 'F0.2')['reason'], step(report, 'F0.2')['unknown']), ('no-start', True))
        self.assertNotIn('F1.1', [item['id'] for item in report['steps']])

    def test_parallel_steps_do_not_count_one_window_twice_in_the_stage(self):
        phases = [{'name': 'F0: A', 'label': 'F0', 'steps': [{'id': 'F0.1', 'done': True}, {'id': 'F0.2', 'done': True}]}]
        report = timing([cp('10:00', 'w toku: F0.1, F0.2; czeka: GO plan'), cp('10:30', 'w toku: F0.1, F0.2'),
                         cp('11:00', 'zrobione: F0.1, F0.2')], phases=phases)
        self.assertEqual((step(report, 'F0.1')['waitSeconds'], step(report, 'F0.2')['waitSeconds']), (1800, 1800))
        self.assertEqual(report['stages'][0]['waitSeconds'], 1800)
        self.assertEqual(report['totals']['waitSeconds'], 1800)

    def test_a_finish_before_the_start_is_unknown(self):
        report = timing([cp('10:00', 'zrobione: F0.1'), cp('10:10', 'w toku: F0.1')])
        self.assertEqual((step(report, 'F0.1')['reason'], step(report, 'F0.1')['workSeconds']), ('order', None))


class Availability(unittest.TestCase):
    def test_without_the_run_start_nothing_is_computed(self):
        report = timing([cp('10:00', 'w toku: F0.1')], start=None)
        self.assertFalse(report['available'])
        self.assertTrue(report['reason'])
        self.assertEqual((report['windows'], report['steps'], report['stages']), ([], [], []))
        self.assertFalse(report['totals']['complete'])

    def test_no_checkpoints_is_available_and_empty_for_a_plan_without_done_steps(self):
        report = timing([], phases=[PHASES[1]])
        self.assertTrue(report['available'])
        self.assertEqual((report['windows'], report['steps'], report['stages']), ([], [], []))
        self.assertIsNone(report['totals']['workSeconds'])
        self.assertTrue(report['totals']['complete'])

    def test_invalid_approval_entries_are_ignored(self):
        report = timing([cp('10:00', 'czeka: GO plan'), cp('10:20', 'w toku: F0.1')],
                        [{'word': 'GO plan', 'at': 'nie-data'}, {'word': 'GO plan'}])
        self.assertEqual(report['windows'][0]['source'], 'checkpoint')


if __name__ == '__main__':
    unittest.main()
