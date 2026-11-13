"""Komunikat błędu identyfikatora w `checkpoint` (TECH-140, D5): nazywa pole (`--step`, `--run-id`) i wzorzec.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_workflow_id_message.py`.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_skillsync_workflow import WorkflowFixture  # noqa: E402


class IdMessageTests(WorkflowFixture):
    def setUp(self):
        super().setUp()
        self.write('tracked.txt', 'baseline')
        self.init_git()

    def refused(self, run_id, step):
        result = self.cli('checkpoint', '--run-id', run_id, '--step', step, '--status', 'NOT_RUN', '--next', 'test')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('Traceback', result.stderr)
        return result.stdout + result.stderr

    def test_bad_step_names_the_field_and_the_pattern(self):
        text = self.refused('task', 'zły krok')
        self.assertIn('--step', text)
        self.assertIn('[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', text)

    def test_bad_run_id_names_its_own_field(self):
        text = self.refused('a/b', 'verify')
        self.assertIn('--run-id', text)
        self.assertNotIn('--step', text)

    def test_the_pattern_accepts_what_it_names(self):
        result = self.cli('checkpoint', '--run-id', 'task', '--step', 'P1.2-verify', '--status', 'NOT_RUN', '--next', 'test')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
