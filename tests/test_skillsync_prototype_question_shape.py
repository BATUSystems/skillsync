"""Wspólny kształt `question show` i `question wait` (TECH-140, D4): oba mają `question`, `questions[]` i `answerLine`
na górze; addytywnie, żadne dotychczasowe pole nie znika.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_prototype_question_shape.py`.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_skillsync_prototype_question_cli import (EWA, QA, SPEC, QuestionCase,  # noqa: E402,F401
                                                         setUpModule, tearDownModule)


class ShapeTests(QuestionCase):
    def setUp(self):
        super().setUp()
        self.users((EWA, ['expert'], None))
        self.add()
        self.answer()

    def test_show_and_wait_share_the_top_level_fields(self):
        shown = self.q('show', QA)
        waited = self.q('wait', QA, '--timeout', '2')
        for report in (shown, waited):
            self.assertEqual(report['question']['id'], QA)
            self.assertEqual([item['id'] for item in report['questions']], [QA])
            self.assertIsNotNone(report['answerLine'])
        self.assertEqual(shown['answerLine'], waited['answerLine'])
        self.assertEqual(shown['question']['answer']['by'], waited['question']['answer']['by'])

    def test_existing_fields_stay(self):
        shown = self.q('show', QA)
        self.assertEqual(shown['schema'], 'expert-questions/1')
        self.assertEqual(shown['questions'][0], shown['question'])
        waited = self.q('wait', QA, '--timeout', '2')
        self.assertEqual((waited['event'], waited['questionId']), ('answer', QA))

    def test_wait_all_also_lists_the_question(self):
        waited = self.q('wait', '--all', '--timeout', '2')
        self.assertEqual([item['id'] for item in waited['questions']], [QA])
        self.assertEqual(waited['question']['id'], QA)

    def test_show_of_an_unanswered_question_has_a_null_line_and_both_fields(self):
        self.add('Druga?', 'qn-bbbbbbbbbbbb', origin=SPEC + ':Q2')
        shown = self.q('show', 'qn-bbbbbbbbbbbb')
        self.assertIsNone(shown['answerLine'])
        self.assertEqual(shown['question']['id'], 'qn-bbbbbbbbbbbb')


if __name__ == '__main__':
    unittest.main()


class FilesCreatedTests(QuestionCase):
    def test_created_is_files_to_commit_plus_ignored(self):
        report = self.add()
        self.assertTrue(report['created'], 'pierwszy add tworzy pliki')
        self.assertEqual(sorted(report['created']), sorted(report['files'] + report['ignored']), report)
        self.assertEqual([item for item in report['ignored'] if not item.endswith('.lock')], [])
        self.assertTrue(report['ignored'], 'lock jest utworzony i ignorowany przez git')

    def test_a_repeat_creates_and_ignores_nothing(self):
        self.add()
        again = self.add()
        self.assertEqual((again['created'], again['files'], again['ignored']), ([], [], []))
