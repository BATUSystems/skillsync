"""`plans[].summary` of `skillsync status` (the `## Streszczenie dla człowieka` section of a plan) and the warning for a plan being
prepared without it (`quality.plan_summary_missing`, `plan_summary_findings`)."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import quality, status  # noqa: E402


def plan(status_word='draft', summary=None, extra=''):
    text = f'# Plan: demo\n\nStatus: {status_word}\nPrzebieg: DEMO\n\n'
    if summary is not None:
        text += f'## Streszczenie dla człowieka\n{summary}\n\n'
    return text + '## Cel\nCel.\n' + extra + '\n## Progress\n- [ ] F1.1 Krok\n'


class PlanSummary(unittest.TestCase):
    def test_the_section_is_returned_as_text_without_heading_markers(self):
        text = plan(summary='**Co zmieniamy:** a\n### Czego nie robimy\nb\n\n**Ryzyka:** c')
        self.assertEqual(status.plan_summary(text), '**Co zmieniamy:** a\nCzego nie robimy\nb\n\n**Ryzyka:** c')

    def test_a_missing_or_empty_section_is_none(self):
        self.assertIsNone(status.plan_summary(plan()))
        self.assertIsNone(status.plan_summary(plan(summary='')))
        self.assertIsNone(status.plan_summary(plan(summary='   \n\n')))

    def test_a_section_with_only_lower_level_headings_has_their_text_only(self):
        self.assertEqual(status.plan_summary(plan(summary='### Tylko nagłówek')), 'Tylko nagłówek')

    def test_a_code_block_with_a_level_two_heading_does_not_end_the_section(self):
        text = plan(summary='przed\n```\n## nie koniec\n```\npo')
        self.assertEqual(status.plan_summary(text), 'przed\n```\n## nie koniec\n```\npo')

    def test_both_limits_bind_and_a_cut_text_ends_with_an_ellipsis(self):
        lines = '\n'.join(f'linia {n}' for n in range(1, 61))
        result = status.plan_summary(plan(summary=lines))
        self.assertEqual(result.splitlines()[-1], 'linia 40…')
        self.assertEqual(len(result.splitlines()), 40)
        wide = status.plan_summary(plan(summary='x' * 6000))
        self.assertEqual(len(wide), 4001)
        self.assertTrue(wide.endswith('…'))
        exact = '\n'.join(f'l{n}' for n in range(1, 41))
        self.assertEqual(status.plan_summary(plan(summary=exact)), exact)   # at the limit: not cut, no mark

    def test_plan_record_carries_the_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'docs/plans').mkdir(parents=True)
            with_summary, without = root / 'docs/plans/a.md', root / 'docs/plans/b.md'
            with_summary.write_text(plan(summary='Krótko.'), encoding='utf-8')
            without.write_text(plan(), encoding='utf-8')
            self.assertEqual(status.plan_record(root, with_summary)['summary'], 'Krótko.')
            self.assertIsNone(status.plan_record(root, without)['summary'])


class PlanSummaryWarning(unittest.TestCase):
    def test_only_plans_being_prepared_are_asked_for_the_summary(self):
        for word, expected in (('draft', True), ('approved', True), ('in_progress', False), ('done', False), ('archived', False)):
            with self.subTest(status=word):
                self.assertEqual(quality.plan_summary_missing(plan(word)), expected)
                self.assertFalse(quality.plan_summary_missing(plan(word, summary='jest')))

    def test_an_empty_section_counts_as_missing(self):
        self.assertTrue(quality.plan_summary_missing(plan('draft', summary='  ')))

    def test_findings_list_the_plans_without_a_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'docs/plans').mkdir(parents=True)
            (root / 'docs/plans/a.md').write_text(plan('draft'), encoding='utf-8')
            (root / 'docs/plans/b.md').write_text(plan('draft', summary='jest'), encoding='utf-8')
            (root / 'docs/plans/c.md').write_text(plan('done'), encoding='utf-8')
            (root / 'docs/plans/README.md').write_text('# plans\n', encoding='utf-8')
            self.assertEqual(quality.plan_summary_findings(root), ['docs/plans/a.md'])


if __name__ == '__main__':
    unittest.main()
