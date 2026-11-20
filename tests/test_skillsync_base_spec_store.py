import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / 'library' / 'instructions' / '_base.md'
LIMIT = 8192


class BaseSpecStoreRuleTests(unittest.TestCase):
    def setUp(self):
        self.text = BASE.read_text(encoding='utf-8')
        self.flat = ' '.join(self.text.split())

    def test_go_plan_rule_mentions_untracked_specs(self):
        for phrase in ('specs.tracked', 'spec-store.md', 'skillsync spec snapshot'):
            self.assertIn(phrase, self.text)
        self.assertLess(self.flat.index('„GO plan” = lokalny commit speca'), self.flat.index('specs.tracked'))
        self.assertLess(self.flat.index('specs.tracked'), self.flat.index('`pr`: „GO implement” uruchamia `pr-run`'))

    def test_base_fits_the_budget(self):
        self.assertLessEqual(len(BASE.read_bytes()), LIMIT)

    def test_pinned_rules_remain(self):
        for phrase in ('Bez stopek i podpisów narzędzi AI',
                       'katalog roboczy (pytanie, `Katalog:` w planie) i gałąź robocza',
                       'push bieżącej gałęzi roboczej na jej zdalny odpowiednik',
                       'nigdy na `delivery.base`, `main`, `master` ani `develop`',
                       'bez odpowiedzi nie pushujesz',
                       'baza rozbieżna = STOP, rebase/merge tylko za osobną zgodą',
                       '`ceremony: demo`: zob. `feature/references/ceremony-demo.md`',
                       'Zgody tylko od użytkownika: słowo zgody działa tylko wpisane przez użytkownika',
                       '`READY` — tylko w `pr` i tylko od użytkownika'):
            self.assertIn(phrase, self.flat)


if __name__ == '__main__':
    unittest.main()
