"""Plans and specs with Windows line ends (CRLF): the header lines, sections and steps that `skillsync status` and the quality checks read give
the same answer as with LF, and no captured value keeps a `\\r` (161-F). The sample texts are bytes with explicit line ends, so the test does not
depend on what `Path.write_text` does on the system."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillsync_workflow import quality, status  # noqa: E402
from skillsync_workflow.common import plan_section  # noqa: E402

PLAN = """# Plan: demo
Status: done (nota)
Spec: docs/specs/demo.md
Przebieg: ACC-1
Ticket: DEMO-12 (link: https://example.test/t/12)
PR: #5 (link: https://example.test/pr/5)

## Streszczenie dla człowieka
Krótko o zmianie.

## Progress
- [x] F1.1 Kod
- [x] F4.3 Odbiór przez człowieka (lista kontrolna, pokrycie)
- [x] F4.4 Zamknięcie przebiegu

## Wynik
Zrobione, dowód w logu.
"""
SPEC = """# Spec: demo
Status: approved
Plan: docs/plans/2026-11-15-demo.md

## Kontrakty
Nowy endpoint.

## Migracja
nie dotyczy
"""


def both(text):
    return {'LF': text, 'CRLF': text.replace('\n', '\r\n')}


class PlanRunLine(unittest.TestCase):
    def test_przebieg_is_found_and_the_value_has_no_carriage_return(self) -> None:
        for name, text in both(PLAN).items():
            with self.subTest(name):
                match = quality._PLAN_RUN.search(text[:4000])
                self.assertIsNotNone(match)
                self.assertEqual(match.group(1), 'ACC-1')
                self.assertTrue(quality._RUN_ID.fullmatch(match.group(1)))

    def test_przebieg_with_backticks_and_trailing_blanks(self) -> None:
        for eol in ('\n', '\r\n'):
            for line in ('Przebieg: `ACC-1`', 'Przebieg:ACC-1  ', 'Przebieg: ACC-1\t'):
                with self.subTest(eol=repr(eol), line=line):
                    self.assertEqual(quality._PLAN_RUN.search(f'# P{eol}{line}{eol}x{eol}').group(1), 'ACC-1')

    def test_no_przebieg_and_a_value_with_spaces_are_still_not_a_match(self) -> None:
        for eol in ('\n', '\r\n'):
            self.assertIsNone(quality._PLAN_RUN.search(f'# P{eol}Opis: x{eol}'))
            self.assertIsNone(quality._PLAN_RUN.search(f'# P{eol}Przebieg: dwa słowa{eol}'))


class AcceptanceSignalWithCrlf(unittest.TestCase):
    def test_the_signal_is_the_same_for_lf_and_crlf(self) -> None:
        for name, text in both(PLAN).items():
            with self.subTest(name):
                context = {'mode': 'pr', 'approvals': [], 'runs': {'ACC-1'}, 'root': None}
                self.assertTrue(status.acceptance_pending('done', text, context, 'docs/plans/2026-11-15-demo.md'))
                self.assertFalse(status.acceptance_pending('done', text, {**context, 'runs': set()}, 'docs/plans/2026-11-15-demo.md'))   # no such run directory


class SameAnswerWithCrlf(unittest.TestCase):
    """Every reader of the header lines, sections and steps answers alike for LF and CRLF."""

    def answers(self, text):
        return json.dumps({
            'progress': status._progress(text), 'summary': status.plan_summary(text), 'wynik': plan_section(text, 'Wynik'),
            'needs': status.plan_text_needs_human('in_progress', text), 'spec': status._plan_spec(text),
            'status': status._status_fields(text, status.PLAN_STATUSES), 'acceptance_step': status.has_acceptance_step('done', text),
            'title': status._TITLE.search(text).group(1), 'ticket': status._TICKET.search(text).groups(), 'pr': status._PR.search(text).groups(),
        }, ensure_ascii=False, sort_keys=True)

    def test_plan_readers(self) -> None:
        texts = both(PLAN)
        self.assertEqual(self.answers(texts['LF']), self.answers(texts['CRLF']))
        self.assertNotIn('\\r', self.answers(texts['CRLF']))   # no `\r` in any captured value (json would show it as \r)

    def test_spec_readers(self) -> None:
        texts = both(SPEC)
        results = {name: json.dumps({'plan': status._spec_plan(text), 'pre': status.pre_implement_sections(text),
                                     'status': status._status_fields(text, status.SPEC_STATUSES)}, ensure_ascii=False, sort_keys=True)
                   for name, text in texts.items()}
        self.assertEqual(results['LF'], results['CRLF'])
        self.assertIn('docs/plans/2026-11-15-demo.md', results['CRLF'])
        self.assertIn('Kontrakty', results['CRLF'])


if __name__ == '__main__':
    unittest.main()
