"""Parser faz planu (K4): tabele z kolumnami `Id` i `Krok`, znacznik `[UI]`, fail closed."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow.plan_phases import parse_plan_phases  # noqa: E402


def table(header, *rows):
    sep = '|' + '|'.join('---' for _ in header.strip('|').split('|')) + '|'
    return '\n'.join([header, sep, *rows]) + '\n'


class ParsePlanPhasesTests(unittest.TestCase):
    def parse(self, text):
        return parse_plan_phases(text)

    def test_report_keys_and_empty_plan(self):
        self.assertEqual(self.parse(''), {'phases': [], 'ui_phases': [], 'stray_ui': 0})
        self.assertEqual(self.parse('# Plan\n\nProza z [UI] i bez tabeli.\n'), {'phases': [], 'ui_phases': [], 'stray_ui': 0})

    def test_standard_table_lists_phases_in_order(self):
        text = table('| Id | Krok | Rola | Exec | Ryzyko kroku | Pliki |', '| F1 | Kod | implementer | inline | low | `a.py` |',
                     '| F2 | Dok | scribe | inline | low | `b.md` |')
        self.assertEqual(self.parse(text), {'phases': ['F1', 'F2'], 'ui_phases': [], 'stray_ui': 0})

    def test_header_variants_and_column_order(self):
        for header in ('| Id | Krok | Rola |', '|Id|Krok|Rola|', '| Krok | Rola | Id |', '| Krok | Id |'):
            with self.subTest(header=header):
                cols = [c.strip() for c in header.strip('|').split('|')]
                row = '|' + '|'.join({'Id': ' F1 ', 'Krok': ' Praca '}.get(c, ' x ') for c in cols) + '|'
                self.assertEqual(self.parse(table(header, row))['phases'], ['F1'])

    def test_tables_without_both_columns_are_ignored(self):
        for header in ('| # | Krok | Rola |', '| Id | Decyzja | Uzasadnienie |', '| Nr | Krok |', '| id | krok |', '| Id | Kroki |'):
            with self.subTest(header=header):
                cols = len(header.strip('|').split('|'))
                self.assertEqual(self.parse(table(header, '| F1 |' + ' x |' * (cols - 1)))['phases'], [])

    def test_every_matching_table_counts(self):
        text = table('| Id | Krok |', '| F1 | a |') + '\nProza.\n\n' + table('| Krok | Id |', '| b | F2 |')
        self.assertEqual(self.parse(text)['phases'], ['F1', 'F2'])

    def test_code_blocks_are_ignored(self):
        text = '```\n' + table('| Id | Krok |', '| F9 [UI] | z przykładu |') + '```\n' + table('| Id | Krok |', '| F1 | a |')
        self.assertEqual(self.parse(text), {'phases': ['F1'], 'ui_phases': [], 'stray_ui': 0})
        tilde = '~~~md\n| F9 [UI] | x |\n~~~\n'
        self.assertEqual(self.parse(tilde)['stray_ui'], 0)
        # control: without the fence the same table is read
        self.assertEqual(self.parse(table('| Id | Krok |', '| F9 [UI] | z przykładu |'))['ui_phases'], ['F9'])

    def test_duplicated_ids_give_no_phases(self):
        text = table('| Id | Krok |', '| F1 [UI] | a |', '| F1 | b |')
        self.assertEqual(self.parse(text)['phases'], [])
        self.assertEqual(self.parse(text)['ui_phases'], [])
        across = table('| Id | Krok |', '| F1 | a |') + '\n' + table('| Id | Krok |', '| F1 [UI] | a |')
        self.assertEqual(self.parse(across)['ui_phases'], [])
        self.assertEqual(self.parse(table('| Id | Krok |', '| F1 | a |', '| F2 | b |'))['phases'], ['F1', 'F2'])  # control

    def test_ui_marker_in_the_id_cell_anywhere(self):
        for cell in ('F2 [UI]', '[UI] F2', 'F2 [UI] (wstęp)'):
            with self.subTest(cell=cell):
                result = self.parse(table('| Id | Krok |', '| %s | Praca |' % cell))
                self.assertEqual((result['phases'], result['ui_phases']), (['F2'], ['F2']))

    def test_ui_marker_in_the_step_cell_only_at_the_edges(self):
        cases = {'[UI] Ekran': True, 'Ekran [UI]': True, '  [UI]  Ekran  ': True, 'Ekran [UI] i dalej': False,
                 'Ekran [ui]': False, '[ui] Ekran': False, 'Ekran [UI ]': False}
        for cell, expected in cases.items():
            with self.subTest(cell=cell):
                result = self.parse(table('| Id | Krok |', '| F1 | %s |' % cell))
                self.assertEqual(result['ui_phases'], ['F1'] if expected else [])

    def test_ui_marker_is_case_sensitive_in_the_id_cell(self):
        self.assertEqual(self.parse(table('| Id | Krok |', '| F1 [ui] | a |'))['ui_phases'], [])

    def test_inline_code_is_left_out_before_looking_for_the_marker(self):
        cases = {'`[UI]` opis': False, 'opis `[UI]`': False, '`x` [UI]': True, '[UI] `y`': True}
        for cell, expected in cases.items():
            with self.subTest(step=cell):
                result = self.parse(table('| Id | Krok |', '| F1 | %s |' % cell))
                self.assertEqual(result['ui_phases'], ['F1'] if expected else [])
        self.assertEqual(self.parse(table('| Id | Krok |', '| F1 `[UI]` | a |'))['ui_phases'], [])

    def test_ui_marker_in_other_columns_of_a_recognised_table_does_not_mark(self):
        result = self.parse(table('| Id | Krok | Rola |', '| F1 | a | [UI] |'))
        self.assertEqual((result['ui_phases'], result['stray_ui']), ([], 0))

    def test_stray_ui_counts_table_rows_outside_recognised_tables(self):
        text = (table('| Id | Krok |', '| F1 | a |') + '\n' + table('| # | Krok |', '| 1 | [UI] ekran |', '| 2 | zwykły |')
                + '\n| luźny wiersz [UI] |\n')
        result = self.parse(text)
        self.assertEqual((result['phases'], result['ui_phases'], result['stray_ui']), (['F1'], [], 2))

    def test_stray_ui_ignores_prose_headings_code_and_inline_code(self):
        text = ('# Plan [UI]\n\nFaza [UI] w prozie.\n\n### Faza 2 [UI]\n- [ ] F2.1 [UI] krok\n\n```\n| a [UI] |\n```\n'
                + table('| # | Krok |', '| 1 | `[UI]` w kodzie |'))
        self.assertEqual(self.parse(text)['stray_ui'], 0)
        self.assertEqual(self.parse(table('| # | Krok |', '| 1 | [UI] w tabeli |'))['stray_ui'], 1)  # control

    def test_separator_rows_are_skipped(self):
        text = '| Id | Krok |\n|:---|---:|\n| F1 | a |\n'
        self.assertEqual(self.parse(text)['phases'], ['F1'])

    def test_rows_without_an_id_are_skipped(self):
        result = self.parse(table('| Id | Krok |', '|  | a |', '| F1 | b |'))
        self.assertEqual(result['phases'], ['F1'])

    def test_id_is_the_first_token_of_the_cell(self):
        self.assertEqual(self.parse(table('| Id | Krok |', '| F2 [UI] dodatek | a |'))['phases'], ['F2'])

    def test_crlf_lines_are_read(self):
        text = table('| Id | Krok |', '| F1 [UI] | a |').replace('\n', '\r\n')
        self.assertEqual(self.parse(text)['ui_phases'], ['F1'])

    def test_real_plans_of_the_repository_have_no_ui_phases(self):
        plans = sorted((ROOT / 'docs' / 'plans').glob('*.md'))
        self.assertTrue(plans, 'control: the repository has plans')
        for path in plans:
            with self.subTest(plan=path.name):
                result = parse_plan_phases(path.read_text(encoding='utf-8'))
                self.assertEqual(result['ui_phases'], [])
                self.assertEqual(result['stray_ui'], 0)


if __name__ == '__main__':
    unittest.main()
