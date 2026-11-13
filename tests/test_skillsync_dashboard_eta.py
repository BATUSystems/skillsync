"""`eta` of the dashboard: the `eta: <operation> do HH:MM` segment of the newest checkpoint (deterministic times, no clock reads)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillsync_workflow import dashboard  # noqa: E402

START = '2026-11-13T09:00:00+01:00'


def cps(*items):
    return [{'time': time, 'text': text} for time, text in items]


class ParseEta(unittest.TestCase):
    def test_reads_operation_and_time_of_the_segment(self):
        self.assertEqual(dashboard.parse_eta('w toku: F1.2; eta: rysowanie 12 ekranów prototypu do 15:10'),
                         ('rysowanie 12 ekranów prototypu', '15:10'))
        self.assertEqual(dashboard.parse_eta('eta: plan do 9:05.'), ('plan', '09:05'))
        self.assertEqual(dashboard.parse_eta('ETA: Spec DO 10:00'), ('Spec', '10:00'))

    def test_without_a_readable_estimate_there_is_none(self):
        for text in ('w toku: F1.1', 'eta: -', 'eta: brak', 'eta: do 10:00', 'eta: plan do 25:00', 'eta: plan do 10:75',
                     'eta: plan', 'eta: plan o 10:00', 'meta: plan do 10:00'):
            with self.subTest(text=text):
                self.assertIsNone(dashboard.parse_eta(text))


class BuildEta(unittest.TestCase):
    def test_the_newest_checkpoint_decides(self):
        chrono = cps(('09:00', 'start; eta: spec do 09:30'), ('09:20', 'w toku: S1; eta: prototyp do 10:15'))
        self.assertEqual(dashboard.build_eta(START, chrono, 'in_progress'),
                         {'operation': 'prototyp', 'until': '10:15', 'untilIso': '2026-11-13T10:15:00+01:00', 'since': '09:20'})

    def test_it_vanishes_with_a_later_checkpoint_without_eta(self):
        chrono = cps(('09:00', 'start; eta: spec do 09:30'), ('09:25', 'zrobione: S1'))
        self.assertIsNone(dashboard.build_eta(START, chrono, 'in_progress'))

    def test_an_update_replaces_the_estimate(self):
        chrono = cps(('09:00', 'eta: plan do 09:30'), ('09:35', 'eta: plan do 09:50'))
        self.assertEqual(dashboard.build_eta(START, chrono, 'in_progress')['until'], '09:50')

    def test_a_waiting_checkpoint_and_a_closed_plan_have_no_estimate(self):
        self.assertIsNone(dashboard.build_eta(START, cps(('09:00', 'eta: plan do 09:30; czeka: GO plan')), 'in_progress'))
        self.assertIsNone(dashboard.build_eta(START, cps(('09:00', 'eta: plan do 09:30')), 'done'))
        self.assertIsNone(dashboard.build_eta(START, [], 'in_progress'))

    def test_an_estimate_across_midnight_is_the_next_day(self):
        chrono = cps(('23:30', 'eta: iteracje do 00:40'))
        self.assertEqual(dashboard.build_eta('2026-11-13T22:00:00+01:00', chrono, 'in_progress')['untilIso'],
                         '2026-11-14T00:40:00+01:00')

    def test_an_estimate_a_few_minutes_in_the_past_stays_on_the_same_day(self):
        chrono = cps(('14:05', 'eta: krok do 14:00'))
        self.assertEqual(dashboard.build_eta('2026-11-13T09:00:00+01:00', chrono, None)['untilIso'], '2026-11-13T14:00:00+01:00')

    def test_without_a_run_start_only_the_clock_is_given(self):
        found = dashboard.build_eta(None, cps(('09:00', 'eta: plan do 09:30')), 'in_progress')
        self.assertEqual((found['until'], found['untilIso']), ('09:30', None))


if __name__ == '__main__':
    unittest.main()
