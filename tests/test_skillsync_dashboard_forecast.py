"""`forecast` and `timing.acts` of the status page: the pace model (median and quartiles of the gaps between finished steps, waiting
windows subtracted), the minimum of data, the paused state, no absolute time, the measured time of acts I and II. Deterministic times,
synthetic data. Run: `python3 -m unittest tests.test_skillsync_dashboard_forecast`."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillsync_workflow import dashboard, dashboard_forecast as fc  # noqa: E402

START = '2026-11-13T09:00:00+01:00'


def cps(*items):
    return [{'time': time, 'text': text} for time, text in items]


def plan(done, total):
    return {'total': total, 'done': done, 'pending': 0}


def steps_timing(*marks, windows=()):
    """A `timing` dict: marks = (id, 'HH:MM start', 'HH:MM finish'); windows = ('HH:MM', 'HH:MM' or None)."""
    iso = lambda hm: f'2026-11-13T{hm}:00+01:00' if hm else None  # noqa: E731
    return {'available': True, 'reason': None,
            'steps': [{'id': i, 'startedAt': iso(a), 'finishedAt': iso(b)} for i, a, b in marks],
            'windows': [{'what': 'x', 'startedAt': iso(a), 'endedAt': iso(b), 'seconds': None if b is None else 1, 'unknown': b is None}
                        for a, b in windows]}


FOUR = (('S1', '09:00', '09:10'), ('S2', '09:10', '09:30'), ('S3', '09:30', '09:40'), ('S4', '09:40', '10:10'))   # gaps 10, 20, 10, 30 min


class ModelTests(unittest.TestCase):
    def test_the_range_is_remaining_times_quartiles_of_the_gaps(self):
        out = fc.build_forecast(steps_timing(*FOUR), plan(4, 9), 'in_progress', False)
        self.assertEqual((out['scope'], out['remaining'], out['samples'], out['basis'], out['paused']), ('plan', 5, 4, 'run', False))
        # gaps in minutes 10, 10, 20, 30: quartiles (inclusive) 10, 15, 22.5
        self.assertEqual((out['lowSeconds'], out['medianSeconds'], out['highSeconds']), (5 * 600, 5 * 900, round(5 * 1350)))

    def test_fewer_than_four_finished_steps_give_no_number_and_say_why(self):
        out = fc.build_forecast(steps_timing(*FOUR[:3]), plan(3, 9), 'in_progress', False)
        self.assertEqual(out, {'scope': 'plan', 'reason': 'not-enough-data', 'have': 3, 'need': 4})
        self.assertNotIn('lowSeconds', out)

    def test_a_step_without_a_start_mark_does_not_count(self):
        marks = FOUR[:3] + (('S4', None, '10:10'),)
        out = fc.build_forecast(steps_timing(*marks), plan(4, 9), 'in_progress', False)
        self.assertEqual((out['reason'], out['have']), ('not-enough-data', 3))

    def test_waiting_windows_inside_a_gap_are_not_work(self):
        # the same four steps, but 30 minutes of waiting inside the last gap (09:40 to 10:10): that gap is then 0 minutes of work
        out = fc.build_forecast(steps_timing(*FOUR, windows=[('09:40', '10:10')]), plan(4, 9), 'in_progress', False)
        self.assertEqual(out['lowSeconds'], round(5 * 450))     # gaps 0, 10, 10, 20 min: first quartile 7.5 min
        self.assertEqual(out['medianSeconds'], 5 * 600)

    def test_a_window_nothing_closes_leaves_its_gap_out_of_the_sample(self):
        out = fc.build_forecast(steps_timing(*FOUR, windows=[('09:35', None)]), plan(4, 9), 'in_progress', False)
        self.assertEqual((out['reason'], out['have'], out['need']), ('not-enough-data', 2, 4))   # the two gaps ending after 09:35 are unreadable

    def test_steps_finishing_together_share_their_gap(self):
        marks = (('S1', '09:00', '09:20'), ('S2', '09:00', '09:20'), ('S3', '09:20', '09:40'), ('S4', '09:40', '10:00'))
        out = fc.build_forecast(steps_timing(*marks), plan(4, 8), 'in_progress', False)
        self.assertEqual(sorted(fc.gap_samples(steps_timing(*marks))[0]), [600, 600, 1200, 1200])   # 20 min shared by the two steps that finished together
        self.assertEqual(out['samples'], 4)
        self.assertEqual(out['medianSeconds'], 4 * 900)

    def test_paused_while_the_newest_checkpoint_waits_for_a_person(self):
        out = fc.build_forecast(steps_timing(*FOUR), plan(4, 9), 'in_progress', True)
        self.assertTrue(out['paused'])
        self.assertIn('medianSeconds', out)

    def test_no_absolute_time_no_number(self):
        out = fc.build_forecast({'available': False, 'reason': 'x', 'steps': [], 'windows': []}, plan(4, 9), 'in_progress', False)
        self.assertEqual(out, {'scope': 'plan', 'reason': 'no-run-start'})

    def test_a_plan_that_does_not_run_or_has_nothing_left_has_no_forecast(self):
        self.assertEqual(fc.build_forecast(steps_timing(*FOUR), plan(4, 9), 'done', False)['reason'], 'plan-not-running')
        self.assertEqual(fc.build_forecast(steps_timing(*FOUR), plan(4, 9), None, False)['reason'], 'plan-not-running')
        self.assertEqual(fc.build_forecast(steps_timing(*FOUR), plan(9, 9), 'in_progress', False)['reason'], 'no-remaining')

    def test_reported_steps_count_as_done(self):
        out = fc.build_forecast(steps_timing(*FOUR), {'total': 9, 'done': 3, 'pending': 2}, 'in_progress', False)
        self.assertEqual(out['remaining'], 4)

    def test_the_minimum_is_one_constant(self):
        self.assertEqual(fc.MIN_FINISHED_STEPS, 4)


class FromCheckpoints(unittest.TestCase):
    """The same model over a real `build_timing` (checkpoint lines, not hand-made dicts)."""

    def test_end_to_end_with_a_waiting_window(self):
        lines = cps(('09:00', 'w toku: S1'), ('09:10', 'zrobione: S1; w toku: S2'), ('09:30', 'zrobione: S2; w toku: S3'),
                    ('09:40', 'zrobione: S3; w toku: S4; czeka: odpowiedź'), ('10:10', 'odpowiedź jest'), ('10:20', 'zrobione: S4'))
        phases = [{'name': 'Faza 1: x', 'steps': [{'id': f'S{n}', 'title': 't', 'done': n <= 4} for n in range(1, 8)]}]
        timing = dashboard.build_timing(START, lines, [], phases)
        out = fc.build_forecast(timing, plan(4, 7), 'in_progress', False)
        self.assertEqual((out['remaining'], out['samples']), (3, 4))
        self.assertEqual(out['lowSeconds'] <= out['medianSeconds'] <= out['highSeconds'], True)
        # the 30 minutes of waiting are not in the sample: S4 gap = 09:40 to 10:20 minus 30 = 10 minutes
        self.assertEqual(sorted(fc.gap_samples(timing)[0]), [600, 600, 600, 1200])


class ActTimes(unittest.TestCase):
    def approvals(self, plan_at=None, impl_at=None):
        out = []
        if plan_at:
            out.append({'word': 'GO plan', 'at': plan_at, 'sha': 'a' * 10})
        if impl_at:
            out.append({'word': 'GO implement', 'at': impl_at, 'sha': 'a' * 10})
        return out

    def acts(self, lines, approvals):
        return {a['key']: a for a in dashboard.build_timing(START, lines, approvals, [])['acts']}

    def test_act_one_and_two_are_measured_between_run_start_and_the_consents(self):
        acts = self.acts(cps(('09:05', 'start aktu I')), self.approvals('2026-11-13T10:00:00+01:00', '2026-11-13T10:45:00+01:00'))
        self.assertEqual((acts['spec']['workSeconds'], acts['spec']['waitSeconds'], acts['spec']['unknown']), (3600, 0, False))
        self.assertEqual((acts['plan']['workSeconds'], acts['plan']['endSource']), (2700, 'approval'))

    def test_waiting_inside_an_act_is_subtracted(self):
        lines = cps(('09:10', 'czeka: odpowiedź Q1'), ('09:40', 'odpowiedź jest'))
        acts = self.acts(lines, self.approvals('2026-11-13T10:00:00+01:00'))
        self.assertEqual((acts['spec']['workSeconds'], acts['spec']['waitSeconds']), (1800, 1800))

    def test_without_the_consent_in_the_file_the_end_is_unknown_not_guessed(self):
        acts = self.acts(cps(('09:05', 'start')), [])
        self.assertEqual((acts['spec']['unknown'], acts['spec']['reason'], acts['spec']['endedAt'], acts['spec']['workSeconds']),
                         (True, 'no-approval', None, None))
        self.assertNotIn('plan', acts, 'act II starts at GO plan: unknown start, no entry')

    def test_act_two_without_go_implement_is_unknown(self):
        acts = self.acts(cps(('09:05', 'start')), self.approvals('2026-11-13T10:00:00+01:00'))
        self.assertEqual((acts['plan']['unknown'], acts['plan']['reason']), (True, 'no-approval'))

    def test_an_open_window_inside_an_act_is_unknown(self):
        acts = self.acts(cps(('09:10', 'czeka: odpowiedź')), self.approvals('2026-11-13T10:00:00+01:00'))
        self.assertEqual((acts['spec']['unknown'], acts['spec']['reason']), (True, 'open-window'))

    def test_no_run_start_no_acts(self):
        self.assertEqual(dashboard.build_timing(None, cps(('09:05', 'x')), [], [])['acts'], [])


class InStatus(unittest.TestCase):
    """`forecast` and `timing.acts` come with `build_status` (so with `show` and `/api/status`); `metrics` carries `timing.acts` only."""

    def test_status_has_both_fields_and_the_forecast_says_why_it_has_no_number(self):
        import shutil
        import tempfile
        fixtures = Path(__file__).resolve().parent / 'fixtures' / 'dashboard'
        with tempfile.TemporaryDirectory(prefix='skillsync-forecast-') as base:
            root = Path(base) / 'demo'
            shutil.copytree(fixtures / 'timing' / 'input', root)
            status = dashboard.build_status(root, prototype_port=1, acceptance=lambda slug: None, writing=lambda slug: None, ahead=lambda *args: {})
        self.assertIn('acts', status['timing'])
        self.assertEqual(status['forecast']['scope'], 'plan')
        self.assertTrue('reason' in status['forecast'] or 'medianSeconds' in status['forecast'])
        self.assertEqual(status['eta'], status['eta'], 'eta is the agent declaration and is a separate field')


if __name__ == '__main__':
    unittest.main()
