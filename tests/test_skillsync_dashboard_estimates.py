"""CORE-1150-ETA, the data part: `build_eta` (steps, shift for the waiting of the user, pause, end rules) and `build_estimates` (series, the error
without the time the user was waited for, states) over checkpoints with deterministic times. One table ends a series (spec `Jedna tabela końca serii`),
so the tests walk its rows for series with steps and without."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillsync_workflow import dashboard, dashboard_estimates  # noqa: E402

START = '2026-11-23T09:00:00+01:00'


def cps(*items):
    return [{'time': time, 'text': text} for time, text in items]


def timing_of(chrono, start=START):
    return dashboard.build_timing(start, chrono, [], [])


def eta_of(chrono, status='in_progress', start=START):
    return dashboard.build_eta(start, chrono, status, timing_of(chrono, start))


def rows_of(chrono, status='in_progress', start=START):
    return dashboard.build_estimates(start, chrono, status, timing_of(chrono, start))


STEP = 'w toku: F1.2; eta: implementacja do 10:00'


class EtaWithSteps(unittest.TestCase):
    def test_the_fields_of_a_plain_estimate(self):
        eta = eta_of(cps(('09:05', STEP)))
        self.assertEqual(eta, {'operation': 'implementacja', 'until': '10:00', 'untilIso': '2026-11-23T10:00:00+01:00',
                               'untilOriginalIso': '2026-11-23T10:00:00+01:00', 'since': '09:05', 'steps': ['F1.2'], 'paused': False, 'shiftSeconds': 0})

    def test_it_goes_through_a_waiting_line_and_the_term_moves_by_the_closed_window(self):
        chrono = cps(('09:05', STEP), ('09:30', STEP + '; czeka: odpowiedź Q1 (kto: ekspert)'), ('10:00', 'w toku: F1.2'))
        eta = eta_of(chrono)   # the line after the reaction has no `eta:` but is still "w toku" of the step: goes on once
        self.assertEqual((eta['shiftSeconds'], eta['untilIso'], eta['untilOriginalIso'], eta['paused']),
                         (1800, '2026-11-23T10:30:00+01:00', '2026-11-23T10:00:00+01:00', False))
        self.assertEqual(eta['until'], '10:00')   # the original clock of the checkpoint

    def test_the_second_line_after_the_reaction_without_eta_ends_it(self):
        chrono = cps(('09:05', STEP), ('09:30', 'w toku: F1.2; czeka: Q1'), ('10:00', 'w toku: F1.2'), ('10:05', 'w toku: F1.2'))
        self.assertIsNone(eta_of(chrono))

    def test_an_open_window_of_the_user_pauses_it_and_shifts_nothing_yet(self):
        eta = eta_of(cps(('09:05', STEP), ('09:30', 'w toku: F1.2; czeka: odpowiedź Q1')))
        self.assertTrue(eta['paused'])
        self.assertEqual((eta['shiftSeconds'], eta['untilIso']), (0, '2026-11-23T10:00:00+01:00'))

    def test_a_window_of_the_agent_neither_pauses_nor_shifts(self):
        chrono = cps(('09:05', STEP), ('09:30', 'w toku: F1.2; czeka: CI (kto: agent)'))
        eta = eta_of(chrono)
        self.assertEqual((eta['paused'], eta['shiftSeconds']), (False, 0))
        closed = eta_of(cps(('09:05', STEP), ('09:30', 'w toku: F1.2; czeka: CI (kto: agent)'), ('09:50', 'w toku: F1.2')))
        self.assertEqual((closed['paused'], closed['shiftSeconds'], closed['untilIso']), (False, 0, '2026-11-23T10:00:00+01:00'))

    def test_a_merged_window_with_a_part_of_the_user_counts_as_waiting_for_the_user(self):
        chrono = cps(('09:05', STEP), ('09:30', 'w toku: F1.2; czeka: CI (kto: agent)'), ('09:35', 'w toku: F1.2; czeka: odpowiedź Q1'))
        self.assertTrue(eta_of(chrono)['paused'])

    def test_an_estimate_in_a_line_with_czeka_and_steps_goes_on_paused(self):
        eta = eta_of(cps(('09:05', STEP + '; czeka: odpowiedź Q1')))
        self.assertTrue(eta['paused'])
        self.assertEqual(eta['steps'], ['F1.2'])

    def test_every_other_ending(self):
        for name, chrono in {
            'eta dash': cps(('09:05', STEP), ('09:20', 'w toku: F1.2; eta: -')),
            'done with czeka in the same line': cps(('09:05', STEP), ('09:40', 'zrobione: F1.2; czeka: CP')),
            'done': cps(('09:05', STEP), ('09:40', 'zrobione: F1.2')),
            'another step without eta': cps(('09:05', STEP), ('09:20', 'w toku: F1.3')),
            'no w toku': cps(('09:05', STEP), ('09:20', 'testy lecą')),
        }.items():
            with self.subTest(name):
                self.assertIsNone(eta_of(chrono))
        self.assertIsNone(eta_of(cps(('09:05', STEP)), status='done'))
        self.assertIsNone(eta_of(cps(('09:05', STEP)), status='implemented'))

    def test_a_repeated_same_term_keeps_the_shift_a_different_term_resets_it(self):
        base = [('09:05', STEP), ('09:30', 'w toku: F1.2; czeka: Q1'), ('10:00', 'w toku: F1.2')]
        same = eta_of(cps(*base, ('10:05', STEP)))
        self.assertEqual((same['shiftSeconds'], same['untilIso']), (1800, '2026-11-23T10:30:00+01:00'))
        other = eta_of(cps(*base, ('10:05', 'w toku: F1.2; eta: implementacja do 10:45')))
        self.assertEqual((other['shiftSeconds'], other['until'], other['untilIso']), (0, '10:45', '2026-11-23T10:45:00+01:00'))

    def test_the_shift_counts_only_the_waiting_after_the_term_was_set(self):
        chrono = cps(('09:00', 'w toku: F1.2; czeka: Q0'), ('09:20', 'w toku: F1.2; eta: implementacja do 10:00'), ('09:40', 'w toku: F1.2; czeka: Q1'),
                     ('10:00', 'w toku: F1.2'))
        self.assertEqual(eta_of(chrono)['shiftSeconds'], 1200)   # the window of 09:00-09:20 is before the estimate

    def test_across_midnight_and_a_long_window(self):
        chrono = cps(('23:30', 'w toku: F1.2; eta: iteracje do 00:40'), ('23:45', 'w toku: F1.2; czeka: Q1'), ('00:10', 'w toku: F1.2'))
        eta = eta_of(chrono, start='2026-11-23T22:00:00+01:00')
        self.assertEqual((eta['untilOriginalIso'], eta['shiftSeconds'], eta['untilIso']),
                         ('2026-11-24T00:40:00+01:00', 1500, '2026-11-24T01:05:00+01:00'))

    def test_without_a_run_start_nothing_is_shifted(self):
        eta = dashboard.build_eta(None, cps(('09:05', STEP)), 'in_progress', None)
        self.assertEqual((eta['untilIso'], eta['untilOriginalIso'], eta['shiftSeconds'], eta['until']), (None, None, 0, '10:00'))


class EtaWithoutSteps(unittest.TestCase):
    def test_an_estimate_of_an_operation_ends_at_a_waiting_line(self):
        chrono = cps(('09:00', 'eta: spec do 09:30'), ('09:30', 'spec gotowy; czeka: GO plan'))
        self.assertIsNone(eta_of(chrono))
        self.assertIsNone(eta_of(cps(('09:00', 'eta: plan do 09:30; czeka: GO plan'))))   # a line with both: czeka wins, as before

    def test_after_the_consent_only_a_new_estimate_shows(self):
        chrono = cps(('09:00', 'eta: spec do 09:30'), ('09:30', 'czeka: GO plan'), ('09:40', 'plan; eta: plan do 10:10'))
        eta = eta_of(chrono)
        self.assertEqual((eta['operation'], eta['until'], eta['steps'], eta['shiftSeconds']), ('plan', '10:10', [], 0))

    def test_a_line_without_eta_ends_it(self):
        self.assertIsNone(eta_of(cps(('09:00', 'eta: spec do 09:30'), ('09:10', 'dalej piszę spec'))))


class CardReportExample(unittest.TestCase):
    """CORE-1200-N17: the lines that `worker-mode.md` tells a card to write give a series, without a parser warning."""

    def test_the_english_example_of_the_card_rule_gives_the_same_series(self):
        rule = (Path(__file__).resolve().parent.parent / 'layers' / 'core-en' / 'skills' / 'feature' / 'references' / 'worker-mode.md').read_text(encoding='utf-8')
        self.assertIn('`- HH:MM in progress: F1; eta: <operation> until HH:MM`', rule)
        self.assertIn('`- HH:MM done: F1 (<result or sha>); in progress: F2; eta: <operation> until HH:MM`', rule)
        text = '\n'.join((f'RUN={START}', '- 09:05 in progress: F1; eta: implementation until 09:45',
                          '- 09:20 in progress: F1; eta: implementation until 09:45; waiting: a lease (who: agent)',
                          '- 09:50 done: F1 (abc1234); in progress: F2; eta: e2e run until 10:10', '- 10:05 done: F2 (PASS)'))
        start, chrono = dashboard.parse_checkpoints(text)
        rows = dashboard.build_estimates(start, chrono, 'in_progress', dashboard.build_timing(start, chrono, [], []))
        self.assertEqual([(row['steps'], row['estimatedSeconds'], row['actualSeconds']) for row in rows], [(['F1'], 2400, 2700), (['F2'], 1200, 900)])

    def test_the_example_of_the_card_rule_gives_a_series(self):
        rule = (Path(__file__).resolve().parent.parent / 'library' / 'skills' / 'feature' / 'references' / 'worker-mode.md').read_text(encoding='utf-8')
        self.assertIn('`- HH:MM w toku: F1; eta: <operacja> do HH:MM`', rule)
        self.assertIn('`- HH:MM zrobione: F1 (<wynik albo sha>); w toku: F2; eta: <operacja> do HH:MM`', rule)
        text = '\n'.join((f'RUN={START}', '- 09:05 w toku: F1; eta: implementacja do 09:45',
                          '- 09:20 w toku: F1; eta: implementacja do 09:45; czeka: dzierżawa (kto: agent)',
                          '- 09:50 zrobione: F1 (abc1234); w toku: F2; eta: bieg e2e do 10:10', '- 10:05 zrobione: F2 (PASS)'))
        start, chrono = dashboard.parse_checkpoints(text)
        rows = dashboard.build_estimates(start, chrono, 'in_progress', dashboard.build_timing(start, chrono, [], []))
        self.assertEqual([(row['steps'], row['estimatedSeconds'], row['actualSeconds'], row['userWaitSeconds']) for row in rows],
                         [(['F1'], 2400, 2700, 0), (['F2'], 1200, 900, 0)])
        self.assertEqual([warning for item in chrono for warning in dashboard.report_warnings(item['text'])], [])


class Estimates(unittest.TestCase):
    def test_the_example_of_the_note(self):
        chrono = cps(('14:05', 'w toku: F2.1; eta: implementacja do 14:45'), ('14:30', 'w toku: F2.1; eta: implementacja do 14:45; czeka: Q1 (kto: ekspert)'),
                     ('14:45', 'w toku: F2.1; eta: implementacja do 14:45'), ('15:10', 'zrobione: F2.1'))
        row = rows_of(chrono, start='2026-11-23T14:00:00+01:00')[0]
        self.assertEqual((row['steps'], row['estimatedSeconds'], row['actualSeconds'], row['userWaitSeconds'], row['errorSeconds'], row['adjustedErrorSeconds'],
                          row['state']), (['F2.1'], 2400, 3900, 900, 1500, 600, 'late'))
        self.assertEqual((row['estimatedAt'], row['etaFirst'], row['endedAt']),
                         ('2026-11-23T14:05:00+01:00', '2026-11-23T14:45:00+01:00', '2026-11-23T15:10:00+01:00'))

    def test_a_too_big_estimate_counts_without_any_correction(self):
        chrono = cps(('09:00', 'w toku: F1.1; eta: krok do 10:00'), ('09:40', 'zrobione: F1.1'))
        row = rows_of(chrono)[0]
        self.assertEqual((row['errorSeconds'], row['adjustedErrorSeconds'], row['state']), (-1200, -1200, 'early'))
        waited = cps(('09:00', 'w toku: F1.1; eta: krok do 10:00'), ('09:10', 'w toku: F1.1; czeka: Q1'), ('09:20', 'w toku: F1.1'), ('09:40', 'zrobione: F1.1'))
        row = rows_of(waited)[0]
        self.assertEqual((row['userWaitSeconds'], row['adjustedErrorSeconds'], row['state']), (600, -1200, 'early'))   # waiting does not reduce an early finish

    def test_the_wait_of_the_user_never_turns_a_delay_into_an_early_finish(self):
        chrono = cps(('09:00', 'w toku: F1.1; eta: krok do 09:40'), ('09:10', 'w toku: F1.1; czeka: Q1'), ('09:40', 'w toku: F1.1'), ('09:50', 'zrobione: F1.1'))
        row = rows_of(chrono)[0]
        self.assertEqual((row['errorSeconds'], row['userWaitSeconds'], row['adjustedErrorSeconds'], row['state']), (600, 1800, 0, 'ontime'))

    def test_work_shorter_than_estimated_but_waiting_makes_it_late_is_ontime_by_the_rule(self):
        chrono = cps(('09:00', 'w toku: F1.1; eta: krok do 09:40'), ('09:10', 'w toku: F1.1; czeka: Q1'), ('09:30', 'w toku: F1.1'), ('09:50', 'zrobione: F1.1'))
        row = rows_of(chrono)[0]   # work 30 min, wait 20 min, the end 10 min late: the error of the estimate is 0 (A5)
        self.assertEqual((row['errorSeconds'], row['userWaitSeconds'], row['adjustedErrorSeconds']), (600, 1200, 0))

    def test_the_wait_for_the_agent_is_not_taken_out_of_the_error(self):
        chrono = cps(('09:00', 'w toku: F1.1; eta: krok do 09:40'), ('09:10', 'w toku: F1.1; czeka: CI (kto: agent)'), ('09:30', 'w toku: F1.1'), ('09:50', 'zrobione: F1.1'))
        row = rows_of(chrono)[0]
        self.assertEqual((row['userWaitSeconds'], row['adjustedErrorSeconds'], row['state']), (0, 600, 'late'))

    def test_the_wait_before_the_estimate_is_clipped_and_an_open_window_makes_it_unknown(self):
        chrono = cps(('09:00', 'w toku: F1.1; czeka: Q0'), ('09:20', 'w toku: F1.1; eta: krok do 09:50'), ('10:00', 'zrobione: F1.1'))
        row = rows_of(chrono)[0]
        self.assertEqual(row['userWaitSeconds'], 0)   # the window 09:00-09:20 ended before the estimate
        opened = cps(('09:00', 'w toku: F1.1; eta: krok do 09:50'), ('09:30', 'w toku: F1.1; czeka: Q1'), ('10:00', 'zrobione: F1.1; czeka: CP'))
        row = rows_of(opened)[0]
        self.assertEqual((row['state'], row['adjustedErrorSeconds']), ('unknown', None))

    def test_two_operations_of_a_step_are_two_series_and_a_group_is_one_row(self):
        chrono = cps(('09:00', 'w toku: F1.1; eta: implementacja do 09:45'), ('09:45', 'w toku: F1.1; eta: testy do 10:20'), ('10:25', 'zrobione: F1.1'))
        rows = rows_of(chrono)
        self.assertEqual([(row['operation'], row['errorSeconds']) for row in rows], [('implementacja', 0), ('testy', 300)])
        group = rows_of(cps(('09:00', 'w toku: F1.1, F1.2; eta: oba kroki do 10:00'), ('09:40', 'zrobione: F1.1, F1.2')))
        self.assertEqual((len(group), group[0]['steps']), (1, ['F1.1', 'F1.2']))

    def test_one_skipped_repetition_does_not_split_a_series(self):
        chrono = cps(('09:00', 'w toku: F1.1; eta: krok do 10:00'), ('09:20', 'w toku: F1.1'), ('09:40', 'w toku: F1.1; eta: krok do 10:00'), ('09:55', 'zrobione: F1.1'))
        self.assertEqual(len(rows_of(chrono)), 1)
        two = cps(('09:00', 'w toku: F1.1; eta: krok do 10:00'), ('09:20', 'w toku: F1.1'), ('09:30', 'w toku: F1.1'), ('09:40', 'w toku: F1.1; eta: krok do 10:00'))
        self.assertEqual(len(rows_of(two)), 2)

    def test_the_estimate_that_came_late_is_measured_from_its_own_line(self):
        chrono = cps(('09:00', 'w toku: F1.1'), ('09:30', 'w toku: F1.1; eta: krok do 10:00'), ('10:00', 'zrobione: F1.1'))
        row = rows_of(chrono)[0]
        self.assertEqual((row['estimatedAt'], row['estimatedSeconds'], row['state']), ('2026-11-23T09:30:00+01:00', 1800, 'ontime'))

    def test_open_series_closed_by_the_plan_and_series_without_steps(self):
        chrono = cps(('09:00', 'w toku: F1.1; eta: krok do 10:00'))
        row = rows_of(chrono)[0]
        self.assertEqual((row['state'], 'estimatedSeconds' in row), ('open', False))
        closed = rows_of(chrono, status='done')[0]
        self.assertEqual((closed['state'], closed['endedAt']), ('early', '2026-11-23T09:00:00+01:00'))   # closed by the plan on its last line (table, row 1)
        spec = rows_of(cps(('09:00', 'eta: spec do 09:30'), ('09:40', 'spec gotowy; czeka: GO plan')))[0]
        self.assertEqual((spec['steps'], spec['state'], spec['adjustedErrorSeconds']), ([], 'late', 600))

    def test_no_run_start_no_rows_and_no_eta_no_field(self):
        self.assertIsNone(dashboard.build_estimates(None, cps(('09:00', STEP)), 'in_progress', None))
        self.assertIsNone(rows_of(cps(('09:00', 'w toku: F1.1'), ('09:30', 'zrobione: F1.1'))))

    def test_totals(self):
        chrono = cps(('09:00', 'w toku: F1.1; eta: a do 09:30'), ('09:50', 'zrobione: F1.1'),
                     ('10:00', 'w toku: F1.2; eta: b do 11:00'), ('10:30', 'zrobione: F1.2'),
                     ('11:00', 'w toku: F1.3; eta: c do 12:00'))
        rows = rows_of(chrono)
        totals = dashboard_estimates.totals(rows)
        self.assertEqual((totals['series'], totals['late'], totals['early'], totals['open'], totals['lateSeconds'], totals['earlySeconds'],
                          totals['medianAbsoluteErrorSeconds']), (3, 1, 1, 1, 1200, 1800, 1500))


FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'dashboard' / 'plan-in-progress' / 'input'
RUN_ID = '2026-01-10-demo-feature'


class ThroughShowAndMetrics(unittest.TestCase):
    """`show` (`build_status`) and `dashboard metrics` (`build_metrics`) carry the same rows; a run without `eta:` lines has no field."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='dash-estimates-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'demo'
        shutil.copytree(FIXTURE, self.root, symlinks=True)

    def write_log(self, *lines):
        log = self.root / '.skillsync' / 'runs' / RUN_ID / 'checkpoints.md'
        log.write_text('RUN=2026-01-10T09:00:00Z\n' + '\n'.join(lines) + '\n', encoding='utf-8')

    def test_the_rows_are_in_show_and_in_metrics_with_the_totals(self):
        self.write_log('- 09:05 start planu; w toku: F1.1; eta: implementacja do 09:30', '- 09:50 zrobione: F1.1; w toku: F1.2; eta: testy do 10:30')
        status = dashboard.build_status(self.root, None)
        self.assertEqual([(row['steps'], row['state']) for row in status['estimates']], [(['F1.1'], 'late'), (['F1.2'], 'open')])
        self.assertEqual(status['eta']['steps'], ['F1.2'])
        report = dashboard.build_metrics(self.root, RUN_ID)
        self.assertEqual(report['estimates'], status['estimates'])
        self.assertEqual((report['estimateTotals']['series'], report['estimateTotals']['late'], report['estimateTotals']['lateSeconds']), (2, 1, 1200))
        again = json.loads(json.dumps(dashboard.build_status(self.root, None)))
        self.assertEqual(again['estimates'], json.loads(json.dumps(status['estimates'])))

    def test_a_run_without_eta_lines_has_no_new_fields(self):
        status = dashboard.build_status(self.root, None)
        report = dashboard.build_metrics(self.root, RUN_ID)
        self.assertNotIn('estimates', status)
        self.assertNotIn('estimates', report)
        self.assertNotIn('estimateTotals', report)
        self.assertIsNone(status['eta'])


class ReviewFixes(unittest.TestCase):
    def test_a_step_reopened_after_done_does_not_end_its_new_series(self):
        chrono = cps(('09:00', STEP), ('09:40', 'zrobione: F1.2'), ('10:00', 'w toku: F1.2; eta: poprawki do 11:00'),
                     ('10:20', 'w toku: F1.2; eta: poprawki do 11:00'))
        rows = rows_of(chrono)
        self.assertEqual([r['operation'] for r in rows], ['implementacja', 'poprawki'])
        self.assertEqual(rows[1]['state'], 'open')   # one series, not one per repeated line
        self.assertEqual(eta_of(chrono)['operation'], 'poprawki')

    def test_every_waiting_cycle_of_a_series_gets_one_line_without_eta(self):
        chrono = cps(('09:05', STEP), ('09:10', STEP + '; czeka: Q1 (kto: ekspert)'), ('09:30', 'w toku: F1.2'),
                     ('09:40', STEP + '; czeka: Q2 (kto: ekspert)'), ('10:00', 'w toku: F1.2'))
        self.assertEqual(eta_of(chrono)['operation'], 'implementacja')
        self.assertEqual(len(rows_of(chrono)), 1)


if __name__ == '__main__':
    unittest.main()
