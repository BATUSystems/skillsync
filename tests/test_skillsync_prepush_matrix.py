"""PROCESS-PREPUSH-MATRIX: the matrix before a push is written down and linked from the feature and dispatch skills."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / 'library' / 'skills'


def read(path):
    return (SKILLS / path).read_text(encoding='utf-8')


class PrePushMatrix(unittest.TestCase):
    def test_the_reference_names_every_required_combination(self):
        text = read('feature/references/prepush-matrix.md')
        for needle in ('3.11', '3.13', 'Windows', 'macOS', 'check_skips', 'NOT_RUN', 'os.name', 'wszystkich systemów', 'PUSH SLOT',
                       'kodem 2', 'prototype_e2e', '.github/workflows/ci.yml', 'gh pr checks', '--json', 'while read', 'zsh', 'flock', 'zniknęły'):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_the_dispatch_assignment_and_the_feature_baseline_point_to_it(self):
        self.assertIn('../feature/references/prepush-matrix.md', read('dispatch/SKILL.md'))
        self.assertIn('prepush-matrix.md', read('feature/references/tests-baseline.md'))
        self.assertIn('kodem 2', read('feature/references/tests-baseline.md'))

    def test_the_linked_file_exists(self):
        self.assertTrue((SKILLS / 'feature' / 'references' / 'prepush-matrix.md').is_file())


if __name__ == '__main__':
    unittest.main()
