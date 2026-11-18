"""CORE-ODDANIE (red first): the plan line `Oddanie:` carries a branch name; any other text is not a hand-over target.

`delivery_target` takes the first word of the value and accepts it when it looks like a ref name, so a placeholder such as
"do ustalenia przy pierwszym CP" hands over to a branch called "do". The status page then shows `CP → origin/do` and the CP button records
`target: do` in `approvals.jsonl`.
"""
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import dashboard as engine  # noqa: E402

DIRECT = {'mode': 'direct'}
WORKING = 'feature/x'

NOT_A_BRANCH = (
    'do ustalenia przy pierwszym CP', 'do ustalenia', 'nie dotyczy', 'brak', 'TBD', 'a b', 'main (po CP)', 'main, potem develop',
    'a..b', 'x/', 'main.lock', 'a@{b}', 'x~1', 'x^', 'x:y', 'x?', 'x*y', 'x[y', '.hidden', 'a//b', '@', '--evil', '-x',
)
A_BRANCH = ('main', 'release/1.10.0', '`main`', '**main**', 'feat/x-1', 'develop', 'hotfix_2.1')
PLACEHOLDERS = ('brak', 'TBD', 'todo', 'none', 'n/a', 'nie', 'nic', '-')


class OddanieValue(unittest.TestCase):
    def test_a_value_that_is_not_a_branch_name_is_ignored(self):
        for value in NOT_A_BRANCH:
            with self.subTest(value=value):
                self.assertEqual(engine.delivery_target(DIRECT, WORKING, {'oddanie': value}), WORKING)

    def test_a_branch_name_is_still_the_target(self):
        for value in A_BRANCH:
            with self.subTest(value=value):
                self.assertEqual(engine.delivery_target(DIRECT, WORKING, {'oddanie': value}), value.strip('`*'))

    def test_one_word_placeholders_are_not_a_target(self):
        for value in PLACEHOLDERS:
            with self.subTest(value=value):
                self.assertEqual(engine.delivery_target(DIRECT, WORKING, {'oddanie': value}), WORKING)

    def test_an_absent_or_empty_line_is_no_target_and_no_note(self):
        for fields in ({}, {'oddanie': ''}, {'oddanie': '  '}):
            with self.subTest(fields=fields):
                self.assertEqual(engine.delivery_target(DIRECT, WORKING, fields), WORKING)
                self.assertIsNone(engine.handover_ignored(DIRECT, fields))

    def test_the_ignored_value_is_reported_for_the_tip(self):
        none = {"remote": None, "pushed": False, "ahead": 0}
        for value in ('do ustalenia przy pierwszym CP', 'main (po CP)', 'TBD'):
            with self.subTest(value=value):
                ignored = engine.handover_ignored(DIRECT, {'oddanie': value})
                self.assertEqual(ignored, value)
                cp = engine.delivery_words(DIRECT, none, {'status': 'done'}, [], WORKING, WORKING, ignored)[0]
                self.assertIn('Oddanie: wartość pominięta', cp['tip'])
                self.assertNotIn(value, cp['tip'])   # the text is not echoed
        self.assertIsNone(engine.handover_ignored(DIRECT, {'oddanie': 'main'}))
        self.assertIsNone(engine.handover_ignored({'mode': 'pr'}, {'oddanie': 'do ustalenia'}))
        cp = engine.delivery_words(DIRECT, none, {'status': 'done'}, [], WORKING, 'main', None)[0]
        self.assertNotIn('pominięta', cp['tip'])

    def test_the_branch_rule_is_the_one_of_git(self):
        from skillsync_workflow import status
        for value in NOT_A_BRANCH:
            if value.lower() in engine.HANDOVER_PLACEHOLDERS:
                continue   # valid ref names: the placeholders of one word are a separate list (`test_one_word_placeholders_are_not_a_target`)
            with self.subTest(value=value):
                self.assertFalse(status.valid_branch_name(value), value)
        for value in ('main', 'release/1.10.0', 'feat/x-1', 'a.b', 'x_y'):
            with self.subTest(value=value):
                self.assertTrue(status.valid_branch_name(value), value)
        self.assertFalse(status.valid_branch_name(None))
        self.assertFalse(status.valid_branch_name('a' * 121))

    @unittest.skipUnless(shutil.which('git'), 'git is needed to compare with `git check-ref-format`')
    def test_the_rule_agrees_with_git_check_ref_format(self):
        from skillsync_workflow import status
        values = ('main', 'a/b', 'a//b', 'a..b', 'a.lock', 'a/b.lock', 'x/.y', '.x', 'x.', 'x/', '/x', '-x', 'a@{b}', 'a@b', 'x y', 'x~1', 'x^', 'x:y', 'x?',
                  'x*y', 'x[y', 'x\\y', 'HEAD', 'a.b', 'a_b', 'a/b/c', 'a/./b', 'a/../b', 'refs/heads/x', '.lock', 'lock', 'release/1.10.0', 'feat/x-1')
        for value in values:
            with self.subTest(value=value):
                git = subprocess.run(['git', 'check-ref-format', '--branch', value], capture_output=True).returncode == 0
                self.assertEqual(status.valid_branch_name(value), git, value)

    def test_pr_mode_never_hands_over(self):
        self.assertEqual(engine.delivery_target({'mode': 'pr'}, WORKING, {'oddanie': 'main'}), WORKING)


if __name__ == '__main__':
    unittest.main()
