"""Czas w `answerLine` (TECH-140, D3): lokalny z offsetem (`2026-11-13T13:30+01:00`), minuty; dziennik i kontrakt w UTC.
Stary wiersz z „Z” zostaje poprawny dla `record` (dopasowanie po `[EKSPERT]` i id).
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_prototype_answerline_time.py`.
"""
import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype as pt  # noqa: E402
from tests.test_skillsync_prototype_question_cli import (EWA, QA, QuestionCase,  # noqa: E402,F401
                                                         setUpModule, tearDownModule)


@unittest.skipIf(os.name == 'nt', 'TZ i tzset tylko na Unixie')
class LocalMomentTests(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get('TZ')
        self.addCleanup(self.restore)

    def restore(self):
        if self.old is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = self.old
        time.tzset()

    def at(self, zone, value):
        os.environ['TZ'] = zone
        time.tzset()
        return pt.answer_moment(value)

    def test_winter_and_summer_offsets(self):
        self.assertEqual(self.at('Europe/Warsaw', '2026-11-13T12:30:45.123Z'), '2026-11-13T13:30+01:00')
        self.assertEqual(self.at('Europe/Warsaw', '2026-07-01T10:00:00.000Z'), '2026-07-01T12:00+02:00')

    def test_utc_zone_has_a_zero_offset_and_a_day_can_change(self):
        self.assertEqual(self.at('UTC', '2026-11-13T12:30:45.123Z'), '2026-11-13T12:30+00:00')
        self.assertEqual(self.at('Asia/Kolkata', '2026-11-13T23:00:00.000Z'), '2026-11-14T04:30+05:30')

    def test_extreme_dates_and_naive_times_keep_the_utc_form_without_a_crash(self):
        self.assertEqual(self.at('America/New_York', '0001-01-01T00:00:00.000Z'), '0001-01-01T00:00Z')
        self.assertEqual(self.at('Europe/Warsaw', '2026-11-13T12:30:45'), '2026-11-13T12:30Z')

    def test_an_unreadable_time_keeps_the_utc_form(self):
        self.assertEqual(self.at('Europe/Warsaw', '2026-11-13T12:30:45Q'), '2026-11-13T12:30Z')
        self.assertEqual(self.at('Europe/Warsaw', ''), 'Z')


class AnswerLineCliTests(QuestionCase):
    def test_cli_line_uses_the_zone_of_the_process(self):
        self.users((EWA, ['expert'], None))
        self.add()
        self.answer()
        line = self.q('show', QA, env={'TZ': 'Asia/Kolkata'})['answerLine']
        self.assertRegex(line, r', \d{4}-\d\d-\d\dT\d\d:\d\d\+05:30$')

    def test_record_accepts_an_old_row_with_a_z_time(self):
        self.users((EWA, ['expert'], None))
        self.add()
        self.answer()
        self.rec(QA, 12)


if __name__ == '__main__':
    unittest.main()
