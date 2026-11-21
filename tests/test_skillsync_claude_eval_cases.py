"""CLAUDE-4: the eval cases shipped in tools/claude-eval/evals pass the wrapper's own validation and keep the planned structure."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / 'tools' / 'claude-eval' / 'evals'
QUALIFYING = ('feature-with-key', 'bugfix-with-key', 'pre-implement-verdict')
CONTROL = 'feature-without-key'


def load():
    spec = importlib.util.spec_from_file_location('claude_eval_run', ROOT / 'tools' / 'claude-eval' / 'run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ShippedCases(unittest.TestCase):
    def test_every_case_passes_the_wrapper_validation(self):
        self.assertEqual(load().validate_cases(EVALS), [])

    def test_the_planned_cases_are_present_with_a_grader_each(self):
        for name in QUALIFYING + (CONTROL,):
            with self.subTest(case=name):
                self.assertTrue((EVALS / name / 'prompt.md').is_file())
                self.assertTrue(list((EVALS / name / 'graders').glob('*.md')))

    def test_the_control_forbids_the_read_that_the_qualifying_cases_require(self):
        control = (EVALS / CONTROL / 'graders' / 'no-ticket-intake.md').read_text(encoding='utf-8')
        self.assertIn('max: 0', control)
        for name in ('feature-with-key', 'bugfix-with-key'):
            text = (EVALS / name / 'graders' / 'reads-ticket-intake.md').read_text(encoding='utf-8')
            self.assertIn('ticket-intake', text)
            self.assertNotIn('max:', text)


if __name__ == '__main__':
    unittest.main()
