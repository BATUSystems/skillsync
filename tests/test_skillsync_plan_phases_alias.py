"""Alias `Step` kolumny `Krok` w tabeli kroków planu (CORE-1130, S5): PL=EN, mieszane nagłówki, negatywy, fail closed."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow.plan_phases import parse_plan_phases, parse_table_phases  # noqa: E402


def table(header, *rows):
    sep = '|' + '|'.join('---' for _ in header.strip('|').split('|')) + '|'
    return '\n'.join([header, sep, *rows]) + '\n'


def marks(text):
    result = parse_table_phases(text)
    return result['phases'], result['ui_phases'], result['test_phases']


PL = '| Id | Krok | Rola | Ryzyko | Pliki |'
EN = '| Id | Step | Role | Risk | Files |'


def row(id_cell, step):
    return '| %s | %s | implementer | low | `a.py` |' % (id_cell, step)


class StepAliasTests(unittest.TestCase):
    def test_english_step_table_lists_phases(self):
        text = table(EN, row('F1', 'Kod'), row('F2', 'Dok'))
        self.assertEqual(parse_plan_phases(text)['phases'], ['F1', 'F2'])

    def test_pl_equals_en_for_markers_in_the_step_cell(self):
        cells = ['[UI] Ekran', 'Ekran [UI]', '  [UI]  Ekran  ', 'Ekran [UI] i dalej', 'Ekran [ui]', '`[UI]` opis', 'opis `[UI]`',
                 '`x` [UI]', '[T] Testy', 'Testy [T]', 'Testy [t]', 'Testy [T] dalej', '`[T]` opis', 'opis `[T]`', '[UI] [T] oba',
                 'zwykły']
        for cell in cells:
            with self.subTest(cell=cell):
                pl = marks(table(PL, row('F1', cell)))
                en = marks(table(EN, row('F1', cell)))
                self.assertEqual(en, pl)

    def test_english_markers_actually_mark(self):
        # kontrola, że porównanie PL=EN nie jest pustym 'oba nic'
        self.assertEqual(marks(table(EN, row('F1', '[UI] Ekran'))), (['F1'], ['F1'], []))
        self.assertEqual(marks(table(EN, row('F1', 'Testy [T]'))), (['F1'], [], ['F1']))
        self.assertEqual(marks(table(EN, row('F1', 'Testy [t]'))), (['F1'], [], []))
        self.assertEqual(marks(table(EN, row('F1', 'opis `[T]`'))), (['F1'], [], []))

    def test_markers_in_the_id_cell_work_in_english_table(self):
        self.assertEqual(marks(table(EN, row('F1 [UI]', 'x'), row('F2 [T]', 'y'))), (['F1', 'F2'], ['F1'], ['F2']))

    def test_polish_headers_per_column_name(self):
        for header in ('| Id | Step | Rola | Ryzyko | Pliki |', '| Id | Step | Role | Ryzyko |', '| Id | Step | Pliki |',
                       '| Id | Step | Risk |', '| Id | Step | Files |', '| Id | Step | Role |', '| Step | Id | Rola |'):
            with self.subTest(header=header):
                cols = [c.strip() for c in header.strip('|').split('|')]
                line = '|' + '|'.join({'Id': ' F1 ', 'Step': ' [UI] Praca '}.get(c, ' x ') for c in cols) + '|'
                self.assertEqual(marks(table(header, line)), (['F1'], ['F1'], []))

    def test_step_table_with_expected_column_is_not_a_step_table(self):
        # tabela scenariuszy testów: Id | Step | Expected
        text = table('| Id | Step | Expected |', '| T1 [UI] | klik | widać |')
        result = parse_table_phases(text)
        self.assertEqual((result['phases'], result['ui_phases'], result['test_phases']), ([], [], []))
        self.assertEqual(result['stray_ui'], 1)  # wiersz z [UI] poza rozpoznaną tabelą, jak dla innych nierozpoznanych tabel

    def test_bare_id_step_table_is_not_a_step_table(self):
        self.assertEqual(parse_plan_phases(table('| Id | Step |', '| F1 | a |'))['phases'], [])

    def test_krok_wins_over_step(self):
        text = table('| Id | Krok | Step | Risk |', '| F1 | [UI] Ekran | zwykły | low |', '| F2 | zwykły | [UI] Ekran | low |')
        self.assertEqual(marks(text), (['F1', 'F2'], ['F1'], []))
        text = table('| Id | Step | Krok | Risk |', '| F1 | zwykły | [T] Testy | low |', '| F2 | [T] Testy | zwykły | low |')
        self.assertEqual(marks(text), (['F1', 'F2'], [], ['F1']))

    def test_mixed_plan_with_polish_and_english_tables(self):
        text = table(PL, row('F1', 'a')) + '\nProza.\n\n' + table(EN, row('F2 [UI]', 'b'), row('F3', 'c [T]'))
        self.assertEqual(marks(text), (['F1', 'F2', 'F3'], ['F2'], ['F3']))

    def test_english_phrase_in_content_is_not_a_header_token(self):
        # negatyw: słowo Step poza nagłówkiem (w treści wiersza / w innej kolumnie) niczego nie rozpoznaje
        text = table('| Id | Krok | Rola |', '| F1 | Step 1: kod | implementer |')
        self.assertEqual(parse_plan_phases(text)['phases'], ['F1'])
        text = table('| # | Step | Risk |', '| 1 | [UI] ekran | low |')
        result = parse_plan_phases(text)
        self.assertEqual((result['phases'], result['ui_phases'], result['stray_ui']), ([], [], 1))
        text = table('| Id | Decyzja | Step |', '| D1 | wybór | x |')
        self.assertEqual(parse_plan_phases(text)['phases'], [])

    def test_header_is_case_sensitive(self):
        for header in ('| Id | step | Risk |', '| Id | STEP | Risk |', '| Id | Steps | Risk |', '| id | Step | Risk |'):
            with self.subTest(header=header):
                cols = len(header.strip('|').split('|'))
                self.assertEqual(parse_plan_phases(table(header, '| F1 |' + ' x |' * (cols - 1)))['phases'], [])

    def test_duplicated_id_fails_closed_in_english(self):
        text = table(EN, row('F1 [UI]', 'a'), row('F1 [T]', 'b'))
        self.assertEqual(marks(text), ([], [], []))
        across = table(PL, row('F1', 'a')) + '\n' + table(EN, row('F1 [UI]', 'a'))
        self.assertEqual(marks(across), ([], [], []))
        self.assertEqual(marks(table(EN, row('F1', 'a'), row('F2', 'b')))[0], ['F1', 'F2'])  # kontrola

    def test_code_block_with_english_table_is_ignored(self):
        text = '```\n' + table(EN, row('F9 [UI]', 'z przykładu')) + '```\n' + table(EN, row('F1', 'a'))
        self.assertEqual(marks(text), (['F1'], [], []))

    def test_polish_behaviour_unchanged(self):
        text = table('| Id | Krok | Rola |', '| F1 [UI] | a | x |', '| F2 | [T] b | x |')
        self.assertEqual(marks(text), (['F1', 'F2'], ['F1'], ['F2']))
        self.assertEqual(parse_plan_phases(table('| Id | Krok |', '| F1 | a |'))['phases'], ['F1'])
        self.assertEqual(parse_plan_phases(table('| Id | Decyzja | Źródło |', '| D1 | w | s |'))['phases'], [])


if __name__ == '__main__':
    unittest.main()
