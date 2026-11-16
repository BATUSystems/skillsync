"""Guard of the checkpoint syntax in the docs (STATUS-160-A, K17): the skill `feature` (measurement) and the contract name the same rules."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MEASUREMENT = ROOT / 'library' / 'skills' / 'feature' / 'references' / 'measurement.md'
CONTRACT = ROOT / 'docs' / 'dashboard-contract.md'
PROTOTYPE_SKILL = ROOT / 'library' / 'skills' / 'prototype' / 'SKILL.md'


class SyntaxDocs(unittest.TestCase):
    def test_measurement_states_the_segment_rules(self):
        text = MEASUREMENT.read_text(encoding='utf-8')
        for phrase in ('`;` wewnątrz nawiasów', 'zaczyna się od id kroku', '`‖`', 'trwa: <etap>', '(kto: <rola>[ <login>])',
                       'ostrzeżenie o pominiętych fragmentach'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_contract_describes_the_same_syntax(self):
        text = CONTRACT.read_text(encoding='utf-8')
        for phrase in ('`;` kończy segment tylko poza nawiasami', '`parseWarnings`', '`no-step-id`', '`later-id`', '`unbalanced-bracket`', '`trwa:`', '(kto: <rola>'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_prototype_skill_tells_about_screens_and_the_fallback(self):
        text = PROTOTYPE_SKILL.read_text(encoding='utf-8')
        self.assertIn('writing <projekt> <slug> start --screens <N>', text)
        self.assertIn('starszy rdzeń nie zna `--screens`', text)
        self.assertIn('trwa: prototyp', text)


if __name__ == '__main__':
    unittest.main()
