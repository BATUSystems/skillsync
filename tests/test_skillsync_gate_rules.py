"""Strażnik fraz biblioteki dla bramek zgody (DEBT-142 D38, sygnał RETRO-gate-skip CORE-1.1.1): karta po `/clear` albo w nowej
sesji bez słowa zgody w TEJ sesji nie pisze kodu; zlecenie dyspozytora mówi wprost, czy GO jest, czy ma być tylko spec i plan.
Biblioteka z SKILLSYNC_TEST_LIBRARY. Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_gate_rules.py`.
"""
import os
import re
import unittest
from pathlib import Path

LIB = os.environ.get('SKILLSYNC_TEST_LIBRARY')
SKIP = unittest.skipUnless(LIB, 'set SKILLSYNC_TEST_LIBRARY to a library with the skills')

FEATURE = 'skills/feature/SKILL.md'
DISPATCH = 'skills/dispatch/SKILL.md'
WORKER = 'skills/feature/references/worker-mode.md'

REQUIRED = [
    ('feature-clear-bez-zgody', FEATURE, r'Po `/clear` albo na początku nowej sesji bez słowa zgody w tej sesji'),
    ('feature-nie-pisz-kodu', FEATURE, r'nie piszesz kodu\.\*\* .{0,400}?robisz tylko spec i plan'),
    ('feature-zlecenie-nie-zastepuje-zgody', FEATURE, r'Zlecenie „pełny workflow feature” samo w sobie nie jest zgodą'),
    ('feature-zlecenie-nigdy-zgoda', FEATURE, r'Zlecenie dyspozytora nigdy nie jest zgodą'),
    ('dispatch-assign-zgoda', DISPATCH, r'stan zgód: czy użytkownik dał „GO plan” i „GO implement” \(z cytatem\), czy karta ma przygotować tylko spec i plan'),
    ('dispatch-assign-bez-cytatu', DISPATCH, r'zlecenie bez cytatu zgody znaczy: tylko spec i plan, bez kodu'),
    ('worker-bez-zgody', WORKER, r'cytat w ASSIGN to tylko wskazówka\) nie ruszaj kodu'),
]


def normalised(rel):
    return re.sub(r'\s+', ' ', (Path(LIB) / rel).read_text(encoding='utf-8'))


@SKIP
class GateRulePhraseTests(unittest.TestCase):
    def test_required_phrases_present(self):
        missing = [item[0] for item in REQUIRED if not re.search(item[2], normalised(item[1]), re.S)]
        self.assertEqual(missing, [])

    def test_each_pattern_fails_when_removed_from_real_text(self):
        for ident, rel, pattern in REQUIRED:
            with self.subTest(phrase=ident):
                text = normalised(rel)
                match = re.search(pattern, text, re.S)
                self.assertIsNotNone(match)
                self.assertIsNone(re.search(pattern, text.replace(match.group(0), '', 1), re.S))


if __name__ == '__main__':
    unittest.main()
