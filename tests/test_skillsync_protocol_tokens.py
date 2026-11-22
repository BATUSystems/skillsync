"""Tokeny protokołu (PL/EN): tabela `skillsync_workflow/protocol_tokens.py`, pomocniki regexów i linia przebiegu planu (CORE-1130, krok S1).

Testy opisują kontrakt modułu, a nie jego implementację; zachowanie polskie `Przebieg:` porównujemy z wzorcem referencyjnym sprzed zmiany.

Dowód zdolności do porażki (mutacje, które muszą zaczerwienić testy):
- porównanie tokenów zamienione na tekstowe (np. `RunLine` szukająca `'Run:'` przez `in`/`str.find` zamiast gramatyki linii) -> padną
  RunLineEnglish.test_two_words_value_not_matched_in_header, test_returns_re_match, test_cr_and_backticks oraz porównania start()/end();
- usunięty filtr nagłówka (`Run:` szukane w całym tekście) -> padną RunLineEnglish.test_run_in_body_not_matched,
  test_run_in_body_after_przebieg_in_header i test_run_outside_header_przebieg_in_header;
- usunięte `(?i:` w `ci` (zwracane samo `pattern` albo `(?i)pattern`) -> padną CiHelper.test_wraps_in_local_flag_group,
  test_case_insensitive_match, test_no_global_flag_prefix_anywhere i test_embeddable_mid_pattern;
- alternation bez sortowania po długości -> padnie AlternationHelper.test_longer_wins_in_both_argument_orders;
- alternation z grupą przechwytującą -> padnie test_non_capturing; bez re.escape -> test_escapes_special_chars."""
import ast
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from skillsync_workflow import protocol_tokens as pt  # noqa: E402

MODULE_PATH = ROOT / 'skillsync_workflow' / 'protocol_tokens.py'

# (klucz, pl, en, kind) dokładnie według kontraktu S1.
EXPECTED = [
    ('run', 'Przebieg:', 'Run:', 'parsed'),
    ('verdict', 'Werdykt:', 'Verdict:', 'parsed'),
    ('acceptance', 'Odbiór', 'Acceptance', 'parsed'),
    ('summary', 'Streszczenie dla człowieka', 'Summary for humans', 'parsed'),
    ('contracts', 'Kontrakty', 'Contracts', 'parsed'),
    ('migration', 'Migracja', 'Migration', 'parsed'),
    ('data', 'Dane', 'Data', 'parsed'),
    ('not_applicable', 'nie dotyczy', 'not applicable', 'parsed'),
    ('result', 'Wynik', 'Result', 'parsed'),
    ('step', 'Krok', 'Step', 'parsed'),
    ('expert', '[EKSPERT]', '[EXPERT]', 'parsed'),
    ('assumption', '[ZAŁOŻENIE]', '[ASSUMPTION]', 'skills'),
    ('handoff', 'Oddanie:', 'Handoff:', 'skills'),
    ('directory', 'Katalog:', 'Directory:', 'skills'),
    ('prototype', 'Prototyp:', 'Prototype:', 'parsed'),
    ('prototype_gate', 'Bramka prototypu: pominięta', 'Prototype gate: skipped', 'parsed'),
    ('plan_risk', 'Ryzyko planu:', 'Plan risk:', 'skills'),
    ('domain_terminology', 'Terminologia domenowa', 'Domain terminology', 'skills'),
    ('retro_signals', 'Retro: sygnały', 'Retro: signals', 'skills'),
    ('retro_proposals', 'propozycje', 'proposals', 'skills'),
    ('decision_apply', 'Wprowadź', 'Apply', 'skills'),
    ('decision_variant', 'Wariant', 'Variant', 'skills'),
    ('decision_defer', 'Odłóż', 'Defer', 'skills'),
    ('decision_keep', 'Zostaw', 'Keep', 'skills'),
    ('answer_marker', '— odpowiedź:', '— answer:', 'written'),
    ('expert_marker', '— ekspert:', '— expert:', 'written'),
    ('assumed_marker', 'przyjęte:', 'assumed:', 'written'),
    ('yes', 'TAK', 'YES', 'skills'),
    ('no', 'NIE', 'NO', 'skills'),
    ('waiting', 'czeka na', 'waiting for', 'parsed'),
]
# Gotowość: kontrakt nie podaje kluczy, więc sprawdzamy samą parę pl/en i rodzaj.
READINESS = [
    ('Cel', 'Goal'), ('Kryteria', 'Criteria'), ('TESTY', 'TESTS'), ('Wiedza', 'Knowledge'), ('Ryzyka', 'Risks'),
]

REFERENCE_RUN = re.compile(r'^Przebieg:[ \t]*`?([^\s`]*)`?[ \t\r]*$', re.MULTILINE)


class TokenTable(unittest.TestCase):
    def test_every_contract_entry_present_with_exact_values(self) -> None:
        for key, pl, en, kind in EXPECTED:
            with self.subTest(key=key):
                self.assertIn(key, pt.TOKENS)
                tok = pt.TOKENS[key]
                self.assertEqual((tok.pl, tok.en, tok.kind), (pl, en, kind))
                self.assertEqual(pt.names(key), (pl, en))

    def test_readiness_pairs_present_as_parsed(self) -> None:
        for pl, en in READINESS:
            with self.subTest(pl=pl):
                found = [t for t in pt.TOKENS.values() if t.pl == pl and t.en == en]
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0].kind, 'parsed')

    def test_keys_unique_and_strings(self) -> None:
        keys = list(pt.TOKENS)
        self.assertEqual(len(keys), len(set(keys)))
        for key in keys:
            self.assertIsInstance(key, str)
            self.assertTrue(key)

    def test_pl_differs_from_en_and_nonempty(self) -> None:
        for key, tok in pt.TOKENS.items():
            with self.subTest(key=key):
                self.assertTrue(tok.pl.strip())
                self.assertTrue(tok.en.strip())
                self.assertNotEqual(tok.pl, tok.en)

    def test_no_duplicate_pl_values(self) -> None:
        pls = [t.pl for t in pt.TOKENS.values()]
        self.assertEqual(len(pls), len(set(pls)))

    def test_kind_is_allowed(self) -> None:
        for key, tok in pt.TOKENS.items():
            with self.subTest(key=key):
                self.assertIn(tok.kind, {'parsed', 'written', 'skills'})

    def test_pl_to_en_is_exactly_all_pairs(self) -> None:
        expected = {t.pl: t.en for t in pt.TOKENS.values()}
        self.assertEqual(pt.PL_TO_EN, expected)
        self.assertEqual(len(pt.PL_TO_EN), len(pt.TOKENS))
        # także pozycje 'written' i 'skills'
        self.assertEqual(pt.PL_TO_EN['— odpowiedź:'], '— answer:')
        self.assertEqual(pt.PL_TO_EN['[ZAŁOŻENIE]'], '[ASSUMPTION]')

    def test_names_unknown_key_raises_keyerror(self) -> None:
        with self.assertRaises(KeyError):
            pt.names('no_such_token_key')


class AlternationHelper(unittest.TestCase):
    def test_non_capturing(self) -> None:
        rx = re.compile(pt.alternation('a', 'b'))
        self.assertEqual(rx.groups, 0)
        self.assertEqual(re.compile(pt.token_alternation('run')).groups, 0)

    def test_escapes_special_chars(self) -> None:
        for name in ('[EKSPERT]', 'Prototype gate: skipped', 'a.b', 'x+y', '(z)'):
            with self.subTest(name=name):
                rx = re.compile('^' + pt.alternation(name) + '$')
                self.assertIsNotNone(rx.match(name))
        # `[EKSPERT]` nie może działać jak klasa znaków
        self.assertIsNone(re.match('^' + pt.alternation('[EKSPERT]') + '$', 'E'))
        self.assertIsNone(re.match('^' + pt.alternation('a.b') + '$', 'axb'))

    def test_unique_names(self) -> None:
        pat = pt.alternation('Run:', 'Run:', 'Przebieg:')
        self.assertEqual(pat.count(re.escape('Run:')), 1)

    def test_longer_wins_in_both_argument_orders(self) -> None:
        for args in (('Run', 'Run:'), ('Run:', 'Run')):
            with self.subTest(args=args):
                m = re.match(pt.alternation(*args), 'Run: id')
                self.assertEqual(m.group(0), 'Run:')

    def test_empty_raises_valueerror(self) -> None:
        with self.assertRaises(ValueError):
            pt.alternation()

    def test_token_alternation_matches_both_languages(self) -> None:
        rx = re.compile('^' + pt.token_alternation('run') + '$')
        self.assertIsNotNone(rx.match('Przebieg:'))
        self.assertIsNotNone(rx.match('Run:'))
        self.assertIsNone(rx.match('Przebieg'))


class CiHelper(unittest.TestCase):
    def test_wraps_in_local_flag_group(self) -> None:
        self.assertEqual(pt.ci('abc'), '(?i:abc)')

    def test_case_insensitive_match(self) -> None:
        rx = re.compile('^' + pt.ci(pt.alternation('Run:', 'Przebieg:')) + '$')
        self.assertIsNotNone(rx.match('RUN:'))
        self.assertIsNotNone(rx.match('przebieg:'))

    def test_embeddable_mid_pattern(self) -> None:
        # na Pythonie 3.11 globalna flaga w środku wzorca to błąd; lokalna grupa nie
        rx = re.compile('^x ' + pt.ci(pt.alternation('Run:', 'Przebieg:')) + r' \d+$')
        self.assertIsNotNone(rx.match('x run: 1'))
        self.assertIsNone(rx.match('X run: 1'))  # `x` poza grupą pozostaje wrażliwe na wielkość liter

    def test_no_global_flag_prefix_anywhere(self) -> None:
        bad = re.compile(r'^\(\?[a-z]+\)')
        outputs = [pt.ci('abc'), pt.alternation('a', 'b'), pt.ci(pt.alternation('a', 'b'))]
        outputs += [pt.token_alternation(k) for k in pt.TOKENS]
        outputs += [pt.ci(pt.token_alternation(k)) for k in pt.TOKENS]
        for out in outputs:
            with self.subTest(out=out):
                self.assertIsNone(bad.match(out))


RUN_CASES = {
    'plain': 'Przebieg: ACC-1\n',
    'backticks': 'Przebieg: `ACC-1`\n',
    'empty': 'Przebieg:\n',
    'empty_spaces': 'Przebieg:   \n',
    'crlf': '# Plan\r\nPrzebieg: ACC-1\r\n## Progress\r\n',
    'two_words': 'Przebieg: ACC 1\n',
    'leading_space': ' Przebieg: ACC-1\n',
    'in_body': '# Plan\nStatus: x\n\n## Progress\nPrzebieg: BODY-9\n',
    'several_first_wins': 'Przebieg: FIRST\nPrzebieg: SECOND\n',
    'tab_sep': 'Przebieg:\tACC-1\n',
    'no_trailing_newline': 'Przebieg: ACC-1',
    'none_at_all': '# Plan\nStatus: x\n',
}


class RunLinePolish(unittest.TestCase):
    def test_identical_to_reference(self) -> None:
        for name, text in RUN_CASES.items():
            with self.subTest(case=name):
                ref = REFERENCE_RUN.search(text)
                got = pt.RUN_LINE.search(text)
                if ref is None:
                    self.assertIsNone(got)
                else:
                    self.assertIsNotNone(got)
                    self.assertEqual(got.group(1), ref.group(1))
                    self.assertEqual((got.start(), got.end()), (ref.start(), ref.end()))

    def test_concrete_values(self) -> None:
        self.assertEqual(pt.RUN_LINE.search('Przebieg: ACC-1\n').group(1), 'ACC-1')
        self.assertEqual(pt.RUN_LINE.search('Przebieg: `ACC-1`\n').group(1), 'ACC-1')
        self.assertEqual(pt.RUN_LINE.search('Przebieg:\n').group(1), '')
        self.assertEqual(pt.RUN_LINE.search('x\r\nPrzebieg: A\r\n').group(1), 'A')
        self.assertIsNone(pt.RUN_LINE.search('Przebieg: A B\n'))
        self.assertIsNone(pt.RUN_LINE.search(' Przebieg: A\n'))
        self.assertEqual(pt.RUN_LINE.search('# P\n\n## S\nPrzebieg: B\n').group(1), 'B')
        self.assertEqual(pt.RUN_LINE.search('Przebieg: F\nPrzebieg: S\n').group(1), 'F')


class RunLineEnglish(unittest.TestCase):
    def test_run_in_header_same_group(self) -> None:
        for text, expected in (
            ('# Plan\nRun: ACC-1\n\n## Progress\n', 'ACC-1'),
            ('# Plan\r\nRun: ACC-1\r\n\r\n## Progress\r\n', 'ACC-1'),
            ('# Plan\nRun: `ACC-1`\n## Progress\n', 'ACC-1'),
            ('# Plan\nRun:\n## Progress\n', ''),
        ):
            with self.subTest(text=text):
                m = pt.RUN_LINE.search(text)
                self.assertIsNotNone(m)
                self.assertEqual(m.group(1), expected)
                self.assertEqual(text[m.start():m.end()].rstrip('\r').split(':')[0], 'Run')

    def test_cr_and_backticks(self) -> None:
        m = pt.RUN_LINE.search('# P\r\nRun: `X-2`\r\n## S\r\n')
        self.assertEqual(m.group(1), 'X-2')

    def test_run_in_body_not_matched(self) -> None:
        self.assertIsNone(pt.RUN_LINE.search('# Plan\nStatus: x\n\n## Progress\nRun: pytest\n'))
        self.assertIsNone(pt.RUN_LINE.search('## Progress\nRun: ACC-1\n'))

    def test_run_without_section_lines_whole_text_is_header(self) -> None:
        m = pt.RUN_LINE.search('# Plan\nStatus: x\nRun: ACC-1\nreszta\n')
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), 'ACC-1')

    def test_two_words_value_not_matched_in_header(self) -> None:
        self.assertIsNone(pt.RUN_LINE.search('# Plan\nRun: ACC 1\n## S\n'))
        self.assertIsNone(pt.RUN_LINE.search('# Plan\n Run: ACC-1\n## S\n'))

    def test_both_in_header_earlier_wins(self) -> None:
        for first, second in (('Przebieg: PL-1', 'Run: EN-1'), ('Run: EN-1', 'Przebieg: PL-1')):
            with self.subTest(first=first):
                text = '# Plan\n' + first + '\n' + second + '\n\n## Progress\n'
                m = pt.RUN_LINE.search(text)
                self.assertIsNotNone(m)
                self.assertEqual(m.group(1), first.split(': ')[1])
                self.assertEqual(m.start(), text.index(first))
                self.assertEqual(m.end(), text.index(first) + len(first))

    def test_przebieg_in_body_and_run_in_header_run_wins(self) -> None:
        text = '# Plan\nRun: EN-1\n\n## Progress\nPrzebieg: PL-9\n'
        m = pt.RUN_LINE.search(text)
        self.assertEqual(m.group(1), 'EN-1')
        self.assertEqual(m.start(), text.index('Run:'))

    def test_run_in_body_after_przebieg_in_header(self) -> None:
        text = '# Plan\nPrzebieg: PL-1\n\n## Progress\nRun: EN-9\n'
        m = pt.RUN_LINE.search(text)
        self.assertEqual(m.group(1), 'PL-1')
        self.assertEqual(m.start(), text.index('Przebieg:'))

    def test_run_outside_header_przebieg_in_header(self) -> None:
        text = '# Plan\nPrzebieg: PL-1\n## S\nRun: EN-9\n'
        self.assertEqual(pt.RUN_LINE.search(text).group(1), 'PL-1')

    def test_run_outside_header_przebieg_later_in_body(self) -> None:
        text = '# Plan\n## S\nRun: EN-9\nPrzebieg: PL-2\n'
        m = pt.RUN_LINE.search(text)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), 'PL-2')

    def test_returns_re_match(self) -> None:
        self.assertIsInstance(pt.RUN_LINE.search('# P\nRun: A\n'), re.Match)
        self.assertIsInstance(pt.RUN_LINE.search('Przebieg: A\n'), re.Match)

    def test_no_match_returns_none(self) -> None:
        self.assertIsNone(pt.RUN_LINE.search(''))
        self.assertIsNone(pt.RUN_LINE.search('# Plan\nStatus: x\n'))

    def test_truncated_text_does_not_raise(self) -> None:
        text = '# Plan\nRun: ACC-1\n' + 'x' * 5000 + '\n## S\nPrzebieg: B\n'
        for cut in (0, 1, 9, 10, 12, 17, 4000, len(text)):
            with self.subTest(cut=cut):
                pt.RUN_LINE.search(text[:cut])
        # obcięcie w środku linii `Run:` nadal daje poprawny wynik albo None
        m = pt.RUN_LINE.search('# Plan\nRun: ACC')
        self.assertEqual(m.group(1), 'ACC')


class LeafModule(unittest.TestCase):
    ALLOWED = {'re', 'typing', 'dataclasses', 'collections', 'collections.abc', '__future__'}

    def test_imports_only_stdlib_leaf(self) -> None:
        self.assertTrue(MODULE_PATH.is_file(), f'brak {MODULE_PATH}')
        tree = ast.parse(MODULE_PATH.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name, self.ALLOWED, alias.name)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, 'import względny w module-liściu')
                self.assertIn(node.module, self.ALLOWED, str(node.module))
                self.assertNotIn('skillsync_workflow', node.module or '')

    def test_import_has_no_side_effects(self) -> None:
        code = f'import sys; sys.path.insert(0, {str(ROOT)!r}); from skillsync_workflow import protocol_tokens'
        res = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout, '')
        self.assertEqual(res.stderr, '')


class WrittenMarkers(unittest.TestCase):
    """Znaczniki zapisywane po angielsku przy `language: en` (strona zapisu: inna karta) są w tabeli."""

    def test_expert_ref_and_markers(self):
        self.assertEqual(pt.PL_TO_EN['(ekspert:'], '(expert:')
        self.assertEqual(pt.names('expert_ref'), ('(ekspert:', '(expert:'))
        self.assertEqual(pt.TOKENS['expert_ref'].kind, 'written')
        for key in ('answer_marker', 'expert_marker', 'assumed_marker'):
            self.assertEqual(pt.TOKENS[key].kind, 'written')


if __name__ == '__main__':
    unittest.main()
