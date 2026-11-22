"""CORE-1130 S5b: the reading side of the prototype gate and of the expert rows accepts English aliases next to the Polish tokens.

`Prototype: <path>` reads like `Prototyp: <path>`; `Prototype gate: skipped — <reason> — <person>, <YYYY-MM-DD>` reads like
`Bramka prototypu: pominięta — <powód> — <osoba>, <RRRR-MM-DD>`; a spec row with `[EXPERT]` is accepted like `[EKSPERT]` by `question record`.
Real typos stay refused (fail closed) with the same error class as their Polish counterparts."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow import prototype as pt  # noqa: E402

QID = 'qn-0a1b2c3d4e'
BYPASS_PL = 'Bramka prototypu: pominięta — pilna poprawka — Ola, 2026-11-05'
BYPASS_EN = 'Prototype gate: skipped — pilna poprawka — Ola, 2026-11-05'
PROTO_PL = 'Prototyp: docs/prototypes/alpha'
PROTO_EN = 'Prototype: docs/prototypes/alpha'


class GateAliasCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='gate-alias-')).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        (self.root / 'docs' / 'plans').mkdir(parents=True)

    def gate(self, *header_lines):
        text = '# Plan\n\nSpec: docs/specs/x.md\n' + ''.join(line + '\n' for line in header_lines) + '\n## Etap 1\nTreść.\n'
        (self.root / 'docs' / 'plans' / 'p.md').write_text(text, encoding='utf-8')
        return pt.prototype_gate(self.root, {}, plan='docs/plans/p.md')

    def make_prototype(self, slug='alpha'):
        directory = self.root / 'docs' / 'prototypes' / slug
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'prototype.json').write_text('{}', encoding='utf-8')

    def outcome(self, report):
        return (report['status'], report.get('error'))


class PrototypeLineAlias(GateAliasCase):
    def test_polish_line_keeps_its_behaviour(self):
        self.assertEqual(self.outcome(self.gate(PROTO_PL)), ('BLOCKED', 'prototype_not_found'))

    def test_english_line_is_read_as_a_path_like_the_polish_one(self):
        pl, en = self.gate(PROTO_PL), self.gate(PROTO_EN)
        self.assertEqual(self.outcome(en), ('BLOCKED', 'prototype_not_found'), en)
        self.assertEqual(en.get('path'), pl.get('path'))

    def test_english_line_names_the_same_target_when_the_prototype_exists(self):
        self.make_prototype()
        pl, en = self.gate(PROTO_PL), self.gate(PROTO_EN)
        self.assertEqual(en, pl)
        self.assertNotIn(en.get('error'), ('invalid_prototype_line', 'prototype_not_found'), en)

    def test_mixed_plan_reads_both_lines(self):
        self.make_prototype('alpha')
        self.make_prototype('beta')
        mixed = self.gate(PROTO_PL, 'Prototype: docs/prototypes/beta')
        polish = self.gate(PROTO_PL, 'Prototyp: docs/prototypes/beta')
        self.assertEqual(mixed, polish)
        self.assertNotIn(mixed.get('error'), ('invalid_prototype_line', 'prototype_not_found'), mixed)

    def test_backticked_english_path_and_two_paths(self):
        self.make_prototype()
        self.assertEqual(self.gate('Prototype: `docs/prototypes/alpha`'), self.gate('Prototyp: `docs/prototypes/alpha`'))
        self.assertNotEqual(self.gate('Prototype: `docs/prototypes/alpha`').get('error'), 'invalid_prototype_line')
        self.assertEqual(self.outcome(self.gate('Prototype: docs/prototypes/alpha docs/prototypes/beta')), ('BLOCKED', 'invalid_prototype_line'))
        self.assertEqual(self.outcome(self.gate('Prototype:')), ('BLOCKED', 'invalid_prototype_line'))

    def test_english_line_marks_the_plan_as_having_gate_lines(self):
        for line in (PROTO_PL, PROTO_EN, BYPASS_PL, BYPASS_EN):
            with self.subTest(line=line):
                self.assertTrue(pt.plan_has_gate_lines('# Plan\n\n' + line + '\n\n## Etap 1\n'))

    def test_english_prototype_word_in_text_is_not_a_token(self):
        for line in ('Note: the prototype is ready', 'Prototypes done.', 'Status: prototype gate passes', 'Author: Ola'):
            with self.subTest(line=line):
                report = self.gate(line)
                self.assertEqual((report['status'], report['prototypes']), ('PASS', []), report)
                self.assertFalse(pt.plan_has_gate_lines('# Plan\n\n' + line + '\n\n## Etap 1\n'))

    def test_typos_keep_the_error_class_of_the_polish_ones(self):
        cases = {'space before the colon': 'Prototype : x', 'list item': '- Prototype: x', 'bold': '**Prototype:** x',
                 'quote': '> Prototype: x', 'numbered': '1. Prototype: x', 'italic': '_Prototype:_ x'}
        for name, line in cases.items():
            with self.subTest(case=name):
                polish = self.gate(line.replace('Prototype', 'Prototyp'))
                english = self.gate(line)
                self.assertEqual(self.outcome(english), ('BLOCKED', 'invalid_prototype_line'), english)
                self.assertEqual(self.outcome(english), self.outcome(polish))
        polish = self.gate('Prototyp :')
        self.assertEqual(self.outcome(polish), ('BLOCKED', 'invalid_prototype_line'))


class BypassAlias(GateAliasCase):
    def test_polish_bypass_keeps_its_behaviour(self):
        report = self.gate(BYPASS_PL)
        self.assertEqual(report['status'], 'PASS', report)
        self.assertEqual(report['bypassed'], {'reason': 'pilna poprawka', 'by': 'Ola', 'date': '2026-11-05'})

    def test_english_bypass_equals_the_polish_one(self):
        pl, en = self.gate(BYPASS_PL), self.gate(BYPASS_EN)
        self.assertEqual(en['status'], 'PASS', en)
        self.assertEqual(en['bypassed'], pl['bypassed'])

    def test_english_bypass_unblocks_a_ui_phase_like_the_polish_one(self):
        table = '\n## Kroki\n\n| Id | Krok |\n|---|---|\n| F1 | Ekran logowania [UI] |\n'

        def gate(*header_lines):
            text = '# Plan\n\nSpec: docs/specs/x.md\n' + ''.join(line + '\n' for line in header_lines) + table
            (self.root / 'docs' / 'plans' / 'p.md').write_text(text, encoding='utf-8')
            return pt.prototype_gate(self.root, {}, plan='docs/plans/p.md')

        self.assertEqual(self.outcome(gate()), ('BLOCKED', 'ui_phase_without_prototype'))   # control: no bypass
        polish = gate(BYPASS_PL)
        self.assertEqual(polish['status'], 'PASS', polish)
        english = gate(BYPASS_EN)
        self.assertEqual(english['status'], 'PASS', english)
        self.assertEqual(english.get('bypassed'), polish.get('bypassed'))

    def test_english_bypass_validation_matches_the_polish_grammar(self):
        bad = ['Prototype gate: skipped - x - Ola, 2026-11-05', 'Prototype gate: skipped — x — Ola', 'Prototype gate: skipped — x — Ola, 05.11.2026',
               'Prototype gate: skipped — x — Ola, 2026-13-45', 'Prototype gate: skipped — x — Kowalski, Jan, 2026-11-05',
               'Prototype gate: skipped —  — Ola, 2026-11-05', 'Prototype gate: waived — x — Ola, 2026-11-05']
        for line in bad:
            with self.subTest(line=line):
                self.assertEqual(self.outcome(self.gate(line)), ('BLOCKED', 'invalid_bypass_line'))
        self.assertEqual(self.outcome(self.gate(BYPASS_EN, BYPASS_PL)), ('BLOCKED', 'invalid_bypass_line'), 'two bypass lines, mixed languages')

    def test_bypass_typos_keep_the_error_class_of_the_polish_ones(self):
        cases = {
            'no colon': ('Prototype gate skipped — x — Ola, 2026-11-05', 'Bramka prototypu pominięta — x — Ola, 2026-11-05'),
            'list item': ('- Prototype gate: skipped — x — Ola, 2026-11-05', '- Bramka prototypu: pominięta — x — Ola, 2026-11-05'),
            'bold': ('**Prototype gate:** skipped — x — Ola, 2026-11-05', '**Bramka prototypu:** pominięta — x — Ola, 2026-11-05'),
        }
        for name, (english, polish) in cases.items():
            with self.subTest(case=name):
                got = self.outcome(self.gate(english))
                self.assertEqual(got, self.outcome(self.gate(polish)))
                self.assertIn(got[1], ('invalid_prototype_line', 'invalid_bypass_line'))
        typo = self.gate('Prototype gate: skiped — x — Ola, 2026-11-05')
        self.assertEqual(self.outcome(typo), ('BLOCKED', 'invalid_bypass_line'), typo)

    def test_headings_are_not_gate_lines(self):
        self.assertEqual(self.gate('# Prototype gate: skipped — x — Ola, 2026-11-05')['status'], 'PASS')
        self.assertEqual(self.gate('# Prototype: x')['status'], 'PASS')


class ExpertRowAlias(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='record-alias-')).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        (self.root / 'docs').mkdir()

    def check(self, row, qid=QID):
        (self.root / 'docs' / 'spec.md').write_text('# Spec\n' + row + '\n', encoding='utf-8')
        return pt._check_record_line(self.root, 'docs/spec.md', 2, qid)

    def row(self, token):
        return f'- Q1 {token}: pytanie (ekspert: {QID}) — odpowiedź: tak — ekspert: Ola'

    def test_polish_token_is_accepted(self):
        self.assertIsNone(self.check(self.row('[EKSPERT]')))

    def test_english_token_is_accepted(self):
        self.assertIsNone(self.check(self.row('[EXPERT]')))

    def test_row_without_a_token_or_with_another_id_is_refused_as_before(self):
        for row, qid in ((self.row('[EXPERTS]'), QID), (self.row('EXPERT'), QID), (self.row('[EXPERT]'), 'qn-ffffffff'), (self.row('[EKSPERT]'), 'qn-ffffffff')):
            with self.subTest(row=row, qid=qid):
                report = self.check(row, qid)
                self.assertEqual(report['status'], 'FAIL')
                self.assertEqual(report['error'], 'record_line_mismatch')

    def test_the_refusal_and_the_hint_name_both_tokens(self):
        report = self.check('- Q1: nic')
        self.assertEqual(report['error'], 'record_line_mismatch')
        text = report['reason'] + ' ' + report['hint']
        self.assertIn('[EKSPERT]', text)
        self.assertIn('[EXPERT]', text)
        self.assertIn('[EXPERT]', pt.RECORD_HINT)
        self.assertIn('[EKSPERT]', pt.RECORD_HINT)


if __name__ == '__main__':
    unittest.main()
