"""CORE-1190-STATUSPARSE: the status word of a plan. `Status: in progress (...)` (the way plans are written by hand) is `in_progress`, not `unknown`
(`status.py`) and not `in` (`dashboard.doc_status`); an unknown word stays `unknown` and `readiness` warns about it instead of keeping the first word."""
import importlib.util
import unittest
from pathlib import Path

from skillsync_workflow import dashboard, status
from tests.test_skillsync_workflow import WorkflowFixture

PLAN = ('# Plan\n## Cel\nc\n## Kryteria\nk\n## TESTY\nt\n## Wiedza\nw\n## Ryzyka\nr\n## Kontrakty\nk\n## Migracja\nm\n'
        '## Streszczenie dla człowieka\n\nKrótko.\n')


def line(value):
    return f'# Plan: x\n\nStatus: {value}\nData: 2026-11-26\n'


class StatusWord(unittest.TestCase):
    SPELLINGS = ('in progress (kod M-1 gotowy)', 'In Progress', 'IN PROGRESS', 'in-progress', 'in_progress', '`in progress`', '**in progress**',
                 'in progress', 'in  progress', 'in_progress. Ryzyko niskie')

    def test_every_spelling_of_in_progress_is_in_progress_in_both_parsers(self):
        for value in self.SPELLINGS:
            with self.subTest(value=value):
                self.assertEqual(status._status_fields(line(value), status.PLAN_STATUSES)[0], 'in_progress')
                self.assertEqual(dashboard.doc_status(line(value))[0], 'in_progress')

    def test_the_known_statuses_are_unchanged(self):
        for value in status.PLAN_STATUSES:
            with self.subTest(value=value):
                self.assertEqual(status._status_fields(line(value + ' (note)'), status.PLAN_STATUSES)[0], value)
                self.assertEqual(dashboard.doc_status(line(value.upper()))[0], value)

    def test_a_word_that_only_starts_like_in_progress_is_not_it(self):
        for value in ('in progressive', 'in', 'inprogress', 'in progress2'):
            with self.subTest(value=value):
                self.assertEqual(status._status_fields(line(value), status.PLAN_STATUSES)[0], 'unknown')

    def test_an_unknown_word_is_unknown_and_has_a_warning_with_the_text(self):
        for value in ('w toku', 'zrobione', 'wip'):
            with self.subTest(value=value):
                self.assertEqual(status._status_fields(line(value), status.PLAN_STATUSES)[0], 'unknown')
                warning = status.unknown_status_warning(line(value))
                self.assertIn(repr(value), warning)
                self.assertIn('in_progress', warning)

    def test_no_warning_for_known_statuses_and_for_a_missing_line(self):
        for value in self.SPELLINGS + ('draft', 'approved (GO plan)', 'done', 'archived'):
            with self.subTest(value=value):
                self.assertIsNone(status.unknown_status_warning(line(value)))
        self.assertIsNone(status.unknown_status_warning('# Plan bez statusu\n'))


def hook(name):
    path = Path(__file__).resolve().parent.parent / 'library' / 'hooks' / name
    spec = importlib.util.spec_from_file_location('hook_' + name.replace('-', '_').replace('.', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HookCopies(unittest.TestCase):
    """The hooks keep their own copy of the status pattern (a hook imports nothing from the package); the copies must read the same."""

    def test_the_copies_of_the_pattern_are_the_same_pattern(self):
        for name in ('check-closure.py', 'statusline.py'):
            with self.subTest(hook=name):
                copy = hook(name)._STATUS
                self.assertEqual((copy.pattern, copy.flags), (status._STATUS.pattern, status._STATUS.flags))

    def test_the_statusline_reads_every_spelling_like_the_generator(self):
        line_hook = hook('statusline.py')
        for value in StatusWord.SPELLINGS + ('draft', 'approved (GO plan)', 'done', 'archived', 'w toku', 'in progressive'):
            with self.subTest(value=value):
                self.assertEqual(line_hook.plan_status(line(value)), status._status_fields(line(value), dashboard.ANY_STATUS)[0])
        self.assertEqual(line_hook.plan_status(line('in progress (kod)')), 'in_progress')
        self.assertEqual(line_hook.plan_status(line('In-Progress')), 'in_progress')


class Consumers(WorkflowFixture):
    def configure(self):
        self.manifest['workflow'] = {'readiness': {'required_sections': ['Cel'], 'risk_sections': []}}
        self.save()

    def test_the_consent_poll_sees_an_in_progress_plan_as_approved_or_later(self):
        self.write('docs/plans/p.md', line('in progress (kod gotowy)'))
        self.assertEqual(status._doc_status_word(self.root, 'docs/plans/p.md')[0], 'in_progress')
        found = status.find_approval(self.root, 'GO plan', doc='docs/plans/p.md')
        self.assertEqual(found['status'], 'in_progress')

    def test_readiness_warns_about_an_unknown_status_word_only(self):
        self.configure()
        self.write('plan.md', 'Status: w toku\n' + PLAN)
        warnings = self.report('readiness', '--files', 'helper.py', '--plan', 'plan.md', code=0).get('warnings') or []
        self.assertTrue(any('Unknown plan status' in item and "'w toku'" in item for item in warnings), warnings)
        self.write('plan.md', 'Status: in progress (kod gotowy)\n' + PLAN)
        warnings = self.report('readiness', '--files', 'helper.py', '--plan', 'plan.md', code=0).get('warnings') or []
        self.assertFalse(any('Unknown plan status' in item for item in warnings), warnings)


if __name__ == '__main__':
    unittest.main()
