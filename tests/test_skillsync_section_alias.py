"""CORE-1130 S4: English aliases of plan/spec sections and values (summary, Result, Acceptance step, risk sections, empty body).
Both languages are read, Polish is written; Polish documents behave bit for bit as before (golden K8)."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import protocol_tokens as pt  # noqa: E402
from skillsync_workflow import quality, status  # noqa: E402
from skillsync_workflow.common import plan_section  # noqa: E402


def plan(status_word='draft', summary_title='Streszczenie dla człowieka', summary='Co zmieniamy: a', steps='- [ ] F1.1 Krok\n'):
    text = f'# Plan: demo\n\nStatus: {status_word}\nPrzebieg: DEMO\n\n'
    if summary is not None:
        text += f'## {summary_title}\n{summary}\n\n'
    return text + '## Cel\nCel.\n\n## Progress\n' + steps


def spec(title, body='Treść.'):
    return f'# Spec\n\nStatus: draft\n\n## {title}\n{body}\n\n## Inne\nx\n'


class SummaryAlias(unittest.TestCase):
    def test_status_summary_pl_equals_en(self):
        pl = status.plan_summary(plan(summary='**Co zmieniamy:** a\n### Czego nie robimy\nb'))
        en = status.plan_summary(plan(summary_title='Summary for humans', summary='**Co zmieniamy:** a\n### Czego nie robimy\nb'))
        self.assertEqual(pl, '**Co zmieniamy:** a\nCzego nie robimy\nb')
        self.assertEqual(en, pl)

    def test_status_summary_en_case_insensitive_and_limits(self):
        lines = '\n'.join(f'l{n}' for n in range(1, 61))
        result = status.plan_summary(plan(summary_title='summary for HUMANS', summary=lines))
        self.assertEqual(len(result.splitlines()), 40)
        self.assertTrue(result.endswith('l40…'))

    def test_status_empty_en_summary_is_none(self):
        self.assertIsNone(status.plan_summary(plan(summary_title='Summary for humans', summary='  ')))
        self.assertIsNone(status.plan_summary(plan(summary=None)))

    def test_negative_similar_heading_is_not_the_summary(self):
        self.assertIsNone(status.plan_summary(plan(summary_title='Summary', summary='tekst')))
        self.assertIsNone(status.plan_summary(plan(summary_title='Summary for humans and others', summary='tekst')))

    def test_quality_missing_summary_pl_equals_en(self):
        for word in ('draft', 'approved'):
            self.assertFalse(quality.plan_summary_missing(plan(word)))
            self.assertFalse(quality.plan_summary_missing(plan(word, summary_title='Summary for humans')))
            self.assertTrue(quality.plan_summary_missing(plan(word, summary=None)))
            self.assertTrue(quality.plan_summary_missing(plan(word, summary_title='Summary for humans', summary='')))
        self.assertFalse(quality.plan_summary_missing(plan('in_progress', summary_title='Summary for humans', summary='')))

    def test_first_heading_wins_even_when_empty(self):
        text = '# P\n\n## Streszczenie dla człowieka\n\n## Summary for humans\nNiepuste.\n\n## Cel\nx\n'
        self.assertEqual(plan_section(text, pt.names('summary')), '')
        reverse = '# P\n\n## Summary for humans\n\n## Streszczenie dla człowieka\nNiepuste.\n\n## Cel\nx\n'
        self.assertEqual(plan_section(reverse, pt.names('summary')), '')
        self.assertEqual(plan_section(text, 'Streszczenie dla człowieka'), '')   # single string: as before


class PlanSectionNames(unittest.TestCase):
    def test_result_pl_equals_en(self):
        pl = '# P\n\n## Wynik\nGotowe.\n\n## Cel\nx\n'
        en = '# P\n\n## Result\nGotowe.\n\n## Cel\nx\n'
        self.assertEqual(plan_section(pl, pt.names('result')), 'Gotowe.')
        self.assertEqual(plan_section(en, pt.names('result')), 'Gotowe.')
        self.assertEqual(plan_section(en, ['result', 'Wynik']), 'Gotowe.')
        self.assertEqual(plan_section(en, ('Wynik', 'Result')), 'Gotowe.')

    def test_missing_and_first_wins(self):
        self.assertIsNone(plan_section('# P\n\n## Cel\nx\n', pt.names('result')))
        both = '# P\n\n## Wynik\nPL\n\n## Result\nEN\n'
        self.assertEqual(plan_section(both, pt.names('result')), 'PL')
        self.assertEqual(plan_section(both, ('Result', 'Wynik')), 'PL', 'order in the list does not matter, the top heading does')
        self.assertEqual(plan_section('# P\n\n## Wynik\n\n## Result\nEN\n', pt.names('result')), '')

    def test_fenced_heading_is_skipped_and_string_still_works(self):
        text = '# P\n\n```\n## Result\nnie\n```\n\n## Wynik\nOK\n'
        self.assertEqual(plan_section(text, pt.names('result')), 'OK')
        self.assertEqual(plan_section(text, 'wynik'), 'OK')
        self.assertIsNone(plan_section(text, 'Result'))


class AcceptanceAlias(unittest.TestCase):
    def accepted(self, step):
        return status.has_acceptance_step('done', f'# P\n\nStatus: done\n\n## Progress\n- [x] F1.1 Kod\n{step}\n- [ ] F9.9 Zamknięcie\n')

    def test_acceptance_title_function(self):
        for title in ('Acceptance', 'acceptance', 'S9 Acceptance: owner', 'Acceptance - owner', 'Acceptance (list)', 'F4.3 Acceptance – x',
                      'Odbiór', 'F4.3 Odbiór przez człowieka', 'Odbior'):
            self.assertTrue(pt.acceptance_title(title), title)
        for title in ('Acceptance tests', 'Acceptance criteria review', 'Kod', 'Review acceptance', 'Odbiorca'):
            self.assertFalse(pt.acceptance_title(title), title)

    def test_hyphen_without_space_does_not_end_the_title(self):
        for title in ('Acceptance-tests suite', 'Acceptance-driven x', 'Acceptance-x'):
            self.assertFalse(pt.acceptance_title(title), title)
            self.assertFalse(self.accepted(f'- [ ] {title}'), title)
        for title in ('Acceptance', 'Acceptance:', 'Acceptance – x', 'Acceptance — x', 'Acceptance(x)', 'Acceptance (x)', 'Acceptance - x',
                      'S9 Acceptance - x', 'Acceptance  - x'):
            self.assertTrue(pt.acceptance_title(title), title)

    def test_polish_golden_unchanged(self):
        for title in ('Odbiór', 'Odbior', 'F4.3 Odbiór przez człowieka', 'Odbiór-x', 'odbiór (x)'):
            self.assertTrue(pt.acceptance_title(title), title)
        for title in ('Odbiorca', 'Odbiorcy x'):
            self.assertFalse(pt.acceptance_title(title), title)

    def test_step_pl_equals_en(self):
        for step in ('- [ ] Acceptance', '- [x] S9 Acceptance: owner', '- [ ] F4.3 Acceptance (checklist)', '- [ ] Odbiór', '- [ ] F4.3 Odbiór przez człowieka'):
            self.assertTrue(self.accepted(step), step)

    def test_negative_steps(self):
        for step in ('- [ ] Acceptance tests', '- [ ] Acceptance criteria review', '- [ ] Kod', '- [ ] Odbiorca'):
            self.assertFalse(self.accepted(step), step)

    def test_only_done_plans(self):
        text = '# P\n\nStatus: in_progress\n\n## Progress\n- [ ] Acceptance\n'
        self.assertFalse(status.has_acceptance_step('in_progress', text))


class RiskSectionTitle(unittest.TestCase):
    def test_table_of_cases(self):
        yes = ['Data', 'Data and migration', '1. Contracts', '1) Contracts', '**Migration**', '`Contracts`', 'Contracts: API', 'Data - users',
               'Migration / rollback', 'Contracts, data', 'Data + schema', 'Data & x', 'Migration (db)', 'Contracts – x', 'migration',
               'Kontrakty', 'Migracja', 'Dane', 'Model Dane użytkownika', 'Zmiany: Kontrakty API', 'dane osobowe', '2. Migracja bazy']
        no = ['Data model', 'Migration of users', 'API contracts', 'Metadata', 'Contractsx', 'Goal', 'Cel', 'Tests', 'Database']
        for title in yes:
            self.assertTrue(pt.risk_section_title(title), title)
        for title in no:
            self.assertFalse(pt.risk_section_title(title), title)

    def test_and_is_case_insensitive(self):
        for title in ('Data And migration', 'Contracts AND x', 'DATA AND migration', 'Migration and rollback'):
            self.assertTrue(pt.risk_section_title(title), title)
        for title in ('Data android', 'Data andx'):
            self.assertFalse(pt.risk_section_title(title), title)

    def test_pre_implement_sections_en_equals_pl(self):
        pairs = (('Contracts', 'Kontrakty'), ('Migration', 'Migracja'), ('Data', 'Dane'))
        for en, pl in pairs:
            self.assertEqual(status.pre_implement_sections(spec(en)), [en])
            self.assertEqual(status.pre_implement_sections(spec(pl)), [pl])
        self.assertEqual(status.pre_implement_sections(spec('Data and migration')), ['Data and migration'])
        self.assertEqual(status.pre_implement_sections(spec('1. Contracts')), ['1. Contracts'])
        self.assertEqual(status.pre_implement_sections(spec('**Migration**')), ['**Migration**'])
        self.assertEqual(status.pre_implement_sections(spec('Model Dane użytkownika')), ['Model Dane użytkownika'])

    def test_pre_implement_sections_negative(self):
        for title in ('Data model', 'API contracts', 'Migration of users'):
            self.assertEqual(status.pre_implement_sections(spec(title)), [], title)


class EmptyBody(unittest.TestCase):
    def found(self, body):
        return status.pre_implement_sections(spec('Kontrakty', body))

    def test_not_applicable_is_empty(self):
        for body in ('not applicable', 'Not applicable.', 'not applicable - old', 'NOT APPLICABLE', '**not applicable**', 'not applicable: no API',
                     'Not  applicable', 'nie dotyczy', 'Nie dotyczy.', 'n/a', 'TODO', 'tbd', '-'):
            self.assertEqual(self.found(body), [], body)

    def test_not_applicable_to_old_ones_is_content(self):
        for body in ('Not applicable to old ones', 'Nie dotyczy starych', 'Todo-lista', 'Not applicable but changes'):
            self.assertEqual(self.found(body), ['Kontrakty'], body)

    def test_pl_heading_with_en_empty_value_and_back(self):
        self.assertEqual(status.pre_implement_sections(spec('Kontrakty', 'not applicable')), [])
        self.assertEqual(status.pre_implement_sections(spec('Contracts', 'nie dotyczy')), [])


# Copy of the pre-change regexes (K8 golden): the reference result is computed here, not stored.
_OLD_ACCEPTANCE = re.compile(r'^\s*-\s\[[ xX]\]\s+(?:[A-Za-z]+\d+(?:\.\d+)?\s+)?Odbi[oó]r\b', re.MULTILINE | re.IGNORECASE)
_OLD_WORD = re.compile(r'\b(?:Kontrakty|Migracja|Dane)\b', re.IGNORECASE)
_OLD_EMPTY = re.compile(r'(?:nie[ \t]+dotyczy(?:[ \t]*$|[ \t]*[.\-–—:,;])|(?:todo|tbd|n/a)(?:[ \t]*$|[ \t]*[.:])|[-–—]+[ \t]*$)', re.IGNORECASE)


class GoldenPolish(unittest.TestCase):
    TITLES = ['Kontrakty', 'Migracja', 'Dane', 'Model Dane użytkownika', 'Zmiana Kontrakty API', 'KONTRAKTY', 'dane', 'Danego', 'Metadane',
              'Migracje', 'Cel', 'Dane i migracja', '1. Kontrakty', 'Contracts', 'Data', 'Data model', 'Migration', 'Kod Dane']
    STEPS = ['- [ ] Odbiór', '- [x] F4.3 Odbiór przez człowieka', '- [ ] S9 odbior', '- [ ] Odbiorca', '- [ ] Odbiórka', '- [ ] Kod',
             '- [ ] F4.3.1 Odbiór', '- [ ] Review Odbiór']
    BODIES = ['nie dotyczy', 'Nie dotyczy.', 'Nie dotyczy starych', 'n/a', 'todo', 'Todo-lista', '-', '—', 'treść', '', 'tbd:', 'nie dotyczy - x']

    def test_risk_headings_keep_polish_behaviour(self):
        for title in self.TITLES:
            if _OLD_WORD.search(title):   # Polish words: identical; English-only titles are covered by RiskSectionTitle
                self.assertEqual(status.pre_implement_sections(spec(title)), [title], title)
            elif not re.search(r'(?i)\b(?:contracts|migration|data)\b', title):
                self.assertEqual(status.pre_implement_sections(spec(title)), [], title)

    def test_bodies_match_old_regex(self):
        for body in self.BODIES:
            lines = [re.sub(r'^[*_`>\s]+', '', line).rstrip(status._PRE_TAIL) for line in body.splitlines()]
            lines = [line for line in lines if line.strip()]
            expected = [] if (not lines or _OLD_EMPTY.match(lines[0])) else ['Kontrakty']
            self.assertEqual(status.pre_implement_sections(spec('Kontrakty', body)), expected, repr(body))

    def test_acceptance_steps_match_old_regex(self):
        for step in self.STEPS:
            text = f'# P\n\nStatus: done\n\n## Progress\n{step}\n'
            self.assertEqual(status.has_acceptance_step('done', text), bool(_OLD_ACCEPTANCE.search(f'{step}\n')), step)

    def test_plan_section_and_summary_pl_unchanged(self):
        text = plan(summary='Co zmieniamy: a')
        self.assertEqual(plan_section(text, 'Streszczenie dla człowieka'), 'Co zmieniamy: a')
        self.assertEqual(plan_section(text, 'cel'), 'Cel.')
        self.assertIsNone(plan_section(text, 'Result'))
        self.assertEqual(status.plan_summary(text), 'Co zmieniamy: a')
        self.assertTrue(quality.plan_summary_missing(plan(summary=None)))


if __name__ == '__main__':
    unittest.main()
