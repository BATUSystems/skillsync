"""Strażnik treści reguły katalogu roboczego (CORE-WORKDIR, K8-K10): linia `Katalog:`, pytanie przy „GO plan”, warunki wejścia do `główny`.

Szczegóły niesie `skills/feature/references/workdir.md`; pozostałe pliki mają regułę i odesłanie.  Biblioteka z SKILLSYNC_TEST_LIBRARY.
"""
import os
import re
import unittest
from pathlib import Path

LIB = os.environ.get('SKILLSYNC_TEST_LIBRARY')
SKIP = unittest.skipUnless(LIB, 'set SKILLSYNC_TEST_LIBRARY to the library with the workdir rule')

BASE = 'instructions/_base.md'
SKILL = 'skills/feature/SKILL.md'
WORKDIR = 'skills/feature/references/workdir.md'
PLAN_DETAILS = 'skills/feature/references/plan-details.md'
PLAN_LARGE = 'skills/feature/references/plan-large.md'
SPEC = 'skills/feature/references/spec.md'
PR_AFTER = 'skills/feature/references/pr-after-approval.md'
RETRO = 'skills/feature/references/retro-quality.md'
WORKER = 'skills/feature/references/worker-mode.md'
PR_RUN = 'skills/pr-run/SKILL.md'
SKILLSYNC_SKILL = 'skills/skillsync/SKILL.md'

NEW_FILES = [WORKDIR]
VARIANT_FILES = [WORKDIR, PLAN_DETAILS, SKILL]
LINKING_FILES = [SKILL, PLAN_LARGE, PR_RUN]


def read(rel):
    return ' '.join((Path(LIB) / rel).read_text(encoding='utf-8').split())


# (id, plik, wyrażenie regularne po normalizacji białych znaków)
PHRASES = [
    # K9: warunki wejścia do `główny`
    ('k9-biezaca-galaz', WORKDIR, r'bieżąca gałąź głównego checkoutu, jakakolwiek, także `delivery\.base`, bez `git switch`'),
    ('k9-pytanie-nazwa-galezi', WORKDIR, r'Pytanie o katalog podaje nazwę tej gałęzi'),
    ('k9-ostrzezenie-baza', WORKDIR, r'przy `delivery\.base` ostrzega, że commity i `CP` pójdą na bazę'),
    ('k9-pr-baza-odmowa', WORKDIR, r'W trybie `pr` z bieżącą gałęzią równą `delivery\.base` „główny” nie jest przyjmowany'),
    ('k9-pr-tylko-worktree', WORKDIR, r'pytanie oferuje wtedy tylko własny worktree'),
    ('k9-odlaczony-head', WORKDIR, r'HEAD odłączony = STOP z propozycją worktree'),
    ('k9-brudne-drzewo-pytanie', WORKDIR, r'Zmiany w śledzonych plikach \(poza plikami generowanymi przez `link`\) = pytanie „kontynuować z tymi zmianami w drzewie\?”'),
    ('k9-brudne-drzewo-lista', WORKDIR, r'lista tych zmian trafia do baseline planu'),
    ('k9-brudne-drzewo-domyslnie', WORKDIR, r'domyślna odpowiedź to „użyj worktree”'),
    ('k9-bez-stash', WORKDIR, r'nie stash, nie `checkout --`'),
    ('k9-nieslezone-baseline', WORKDIR, r'Nieśledzone obce pliki: baseline bez pytania'),
    ('k9-dzierzawa-stop', WORKDIR, r'Żywa dzierżawa innego przebiegu w tym katalogu \(`\.skillsync/claims`\) = STOP i propozycja worktree'),
    # Q12: direct na bazie
    ('q12-direct-baza', WORKDIR, r'Tryb `direct` na bazie: bez pytania o `Oddanie:` i bez tej linii'),
    ('q12-cp-baza', WORKDIR, r'`CP` pushuje bieżącą gałąź będącą bazą'),
    # K8: pytanie i brak odpowiedzi
    ('k8-pytanie-domyslnie', WORKDIR, r'„własny worktree” \(domyślnie, zalecane: .*\) albo „główny checkout”'),
    ('k8-brak-odpowiedzi', WORKDIR, r'Brak odpowiedzi, odmowa albo anulowanie = brak „GO plan”'),
    ('k8-bez-go-plan-go-implement', WORKDIR, r'bez „GO plan”: przy „GO implement”'),
    ('k8-nie-pada-plan-ma-katalog', WORKDIR, r'gdy plan ma już linię `Katalog:`'),
    ('k8-nie-pada-karta', WORKDIR, r'w trybie karty \(`ASSIGN`/`dispatch:`\) katalog wynika ze zlecenia, domyślnie worktree paczki'),
    ('k8-nie-pada-klucz', WORKDIR, r'przy wejściu z klucza zgłoszenia \(`pr-after-approval`\)'),
    # Wznowienie, R7
    ('r7-zniknal-worktree', WORKDIR, r'zniknięty katalog worktree z linii planu = STOP i pytanie \(odtworzyć z gałęzi\?\), bez cichego przełączenia'),
    ('wznowienie-brak-linii', WORKDIR, r'brak linii `Katalog:` w planie = worktree jak dotąd'),
    ('katalog-nazwa-nie-sciezka', WORKDIR, r'sama nazwa katalogu, nie ścieżka bezwzględna; ścieżkę odnajdź przez `git worktree list`'),
    # wpływ na pr-run, CP, TAK, sprzątanie
    ('prrun-glowny', WORKDIR, r'`pr-run` krok 3 przy `Katalog: główny` używa głównego checkoutu i jego bieżącej gałęzi'),
    ('tak-direct-glowny', WORKDIR, r'W `direct` z `Katalog: główny` „TAK P<n>” działa'),
    ('tak-unieważnia-sha', WORKDIR, r'„TAK” po rekordzie końcowym zmienia HEAD i unieważnia `sha` zgody `CP`'),
    ('sprzatanie-tylko-worktree', WORKDIR, r'Kopia przebiegu poza worktree i sprzątanie worktree dotyczą tylko `Katalog: worktree`'),
    ('karta-zawsze-worktree', WORKDIR, r'karta \(worker-mode\) pracuje zawsze w worktree ze zlecenia'),
    ('ff-glowny', WORKDIR, r'główny checkout aktualizowany `--ff-only` tylko gdy czysty i na `base`'),
    ('odwrocenie', WORKDIR, r'po oddaniu na bazę w `direct` z gałęzi roboczej sesja proponuje `git switch <baza>`, nie robi tego sama'),
    # odwołania z pochodnych
    ('retro-tak-glowny', RETRO, r'z `Katalog: główny` w `direct` „TAK” działa'),
    ('worker-worktree', WORKER, r'karta zawsze pracuje w worktree ze zlecenia'),
    ('pr-after-bez-zmian', PR_AFTER, r'„rób PR” używa bieżącej gałęzi'),
]


@SKIP
class WorkdirRuleText(unittest.TestCase):
    def test_new_reference_exists(self):
        for rel in NEW_FILES:
            self.assertTrue((Path(LIB) / rel).is_file(), rel)

    def test_phrases(self):
        failures = []
        for key, rel, pattern in PHRASES:
            path = Path(LIB) / rel
            if not path.is_file() or not re.search(pattern, read(rel)):
                failures.append(f'{key}: {rel} !~ {pattern}')
        self.assertEqual(failures, [])

    def test_base_names_the_line_without_details(self):
        base = read(BASE)
        self.assertIn('`Katalog:`', base)
        self.assertIn('katalog roboczy (pytanie, `Katalog:` w planie) i gałąź robocza', base)
        self.assertIn('sprzątanie worktree (tylko `Katalog: worktree`)', base)
        self.assertNotIn('osobny worktree i gałąź robocza', base)
        for detail in ('git switch', 'odłączony', 'checkout --'):
            self.assertNotIn(detail, base)

    def test_base_keeps_push_to_base_safety(self):
        base = read(BASE)
        self.assertIn('baza rozbieżna = STOP, rebase/merge tylko za osobną zgodą', base)
        self.assertIn('w `pr` nigdy bazy', base)
        self.assertIn('szczegóły w `feature`', base)
        self.assertNotIn('--ff-only', base)

    def test_both_variants_in_rule_files(self):
        failures = []
        for rel in VARIANT_FILES:
            text = read(rel) if (Path(LIB) / rel).is_file() else ''
            for variant in ('Katalog: worktree <nazwa-katalogu>', 'Katalog: główny'):
                if variant not in text:
                    failures.append(f'{rel}: brak „{variant}”')
        self.assertEqual(failures, [])

    def test_derived_files_point_to_workdir_reference(self):
        failures = []
        for rel in LINKING_FILES:
            if 'workdir.md' not in read(rel):
                failures.append(rel)
        self.assertEqual(failures, [])

    def test_no_stale_unconditional_worktree_rule(self):
        stale = [
            (SKILL, 'na gałęzi roboczej w osobnym worktree; jeśli jej nie ma'),
            (PLAN_LARGE, 'na gałęzi roboczej w osobnym worktree, którą'),
        ]
        for rel, phrase in stale:
            self.assertNotIn(phrase, read(rel), rel)

    def test_skill_and_docs_mention_the_choice(self):
        for rel in (SKILL, PLAN_LARGE, SPEC, PR_RUN, SKILLSYNC_SKILL):
            self.assertIn('Katalog:', read(rel), rel)

    def test_new_files_are_public_safe(self):
        text = (Path(LIB) / WORKDIR).read_text(encoding='utf-8') if (Path(LIB) / WORKDIR).is_file() else ''
        self.assertTrue(text)
        self.assertNotRegex(text, r'/home/\w')
        self.assertNotRegex(text, r'/Users/\w')
        self.assertNotRegex(text, r'(?i)claude code|generated with|co-authored-by')


if __name__ == '__main__':
    unittest.main()
