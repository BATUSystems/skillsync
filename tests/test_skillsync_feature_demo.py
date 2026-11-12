"""Strażnik treści skilla `feature` i rdzenia: ceremonia `demo` (K1, K3), klasy pytań `Q` (K9), „GO plan” i FEAT-R6 (K10), bramka per faza `[UI]` (K12).

Frazy pochodzą ze specyfikacji zadania (poza rdzeniem), nie z obecnego tekstu.  Biblioteka z SKILLSYNC_TEST_LIBRARY.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

LIB = os.environ.get('SKILLSYNC_TEST_LIBRARY')
SKIP = unittest.skipUnless(LIB, 'set SKILLSYNC_TEST_LIBRARY to a library with the FEAT-DEMO skill text')

SKILL = 'skills/feature/SKILL.md'
CEREMONY = 'skills/feature/references/ceremony-demo.md'
SPEC = 'skills/feature/references/spec.md'
PLAN_DETAILS = 'skills/feature/references/plan-details.md'
PLAN_LARGE = 'skills/feature/references/plan-large.md'
REVIEW_LOOP = 'skills/feature/references/review-loop.md'
MEASUREMENT = 'skills/feature/references/measurement.md'
BASE = 'instructions/_base.md'

# (id, plik, wyrażenie regularne po normalizacji białych znaków)
REQUIRED = [
    # K1 (część skillowa) i K3
    ('k1-route-ceremony-value', CEREMONY, r'pole `ceremony\.value` z `skillsync route <projekt>`'),
    ('k1-brak-pola-full', CEREMONY, r'Brak pola `ceremony`.*= `full`'),
    ('k1-invalid-pytanie', CEREMONY, r'`source: invalid`.*= pytanie do użytkownika, jak przy `delivery`'),
    ('k1-skill-invalid', SKILL, r'`source: invalid` = pytanie do użytkownika, jak przy `delivery`'),
    ('k1-skill-brak-pola', SKILL, r'Brak pola `ceremony` = `full`'),
    ('k3-jedno-review', CEREMONY, r'jedno review `kod` na cały przebieg \(.*\) PLUS pętla pełna'),
    ('k3-trzy-kategorie', CEREMONY, r'strażnikiem wycieków, migracją albo bezpieczeństwem'),
    ('k3-d113', CEREMONY, r'Wszystkie trzy kategorie z tej sekcji zostają bez zmian'),
    ('k3-test-writer', CEREMONY, r'Test-writer\*\*: tylko w fazach z uprawnieniami lub izolacją'),
    ('k3-dokumentacja', CEREMONY, r'ograniczona do changeloga i „Wyniku”'),
    ('k3-dlug-retro-wiedza', CEREMONY, r'`retro-quality` i rekordy wiedzy.*podsekcji „Dług \(ceremony: demo\)”'),
    ('k3-plan-done-z-dlugiem', CEREMONY, r'Plan kończy się `done` z wypełnioną podsekcją „Dług \(ceremony: demo\)”'),
    ('k3-dlug-przed-czeka', CEREMONY, r'także w liście warunków przed stanem „czeka na READY/CP”'),
    ('dash-fix-demo-prototyp-po-specu', CEREMONY, r'## Po specu i review: następny krok to prototyp'),
    ('dash-fix-demo-nie-czekaj-biernie', CEREMONY, r'nie jest powodem, by bezczynnie czekać na „GO plan”'),
    ('dash-fix-demo-bez-ekranow-go-plan', CEREMONY, r'Bez ekranów w specu nie ma prototypu: poproś o „GO plan” od razu'),
    ('dash-fix-demo-prototyp-nie-go', CEREMONY, r'prototyp nie jest „GO plan”'),
    ('dash-fix-skill-demo-prototyp', SKILL, r'Przy `ceremony: demo` po specu i review `spec` następnym krokiem jest prototyp'),
    ('k3-linia-ceremonia', CEREMONY, r'linię `Ceremonia: demo`'),
    ('k3-rozbieznosc-pytanie', CEREMONY, r'Rozbieżność linii planu z `route` przy „GO implement” = pytanie'),
    ('k3-skill-wskaznik', SKILL, r'\(references/ceremony-demo\.md\)'),
    ('k3-plan-details-ceremonia', PLAN_DETAILS, r'Linia `Ceremonia: demo` \(opcjonalna przy `full`\)'),
    ('k3-plan-details-link', PLAN_DETAILS, r'\(ceremony-demo\.md\)'),
    ('k3-base-wyjatek-done', BASE, r'`ceremony: demo`: zob\. `feature/references/ceremony-demo\.md`'),
    ('k3-skill-done-demo', SKILL, r'Przy `ceremony: demo` `done` zachowuje wszystkie warunki poza dokumentacją ponad changelog i „Wynik”'),
    ('k3-skill-dokumentacja-demo', SKILL, r'Przy `ceremony: demo` dokumentacja to changelog i „Wynik”, a `retro-quality` i rekordy wiedzy to dług w „Wyniku”'),
    ('k3-final-ostrzezenie', CEREMONY, r'`retro-quality-missing`; jest oczekiwane i nie blokuje'),
    ('k3-liczba-petli', CEREMONY, r'`demo` zmienia liczbę pętli'),
    ('k3-petla-koncowa-d113', CEREMONY, r'Pętla końcowa pozostaje pełna, gdy cała zmiana należy do kategorii z sekcji „Kategoria pętli i przejścia”'),
    ('k3-review-loop-petle', REVIEW_LOOP, r'zmienia liczbę pętli: jedna pętla `kod` na przebieg zamiast pętli po każdej fazie.*pętla końcowa pozostaje pełna, gdy cała zmiana należy do kategorii pętli pełnej z tej sekcji'),
    ('k10-review-loop-tw-wyjatek', REVIEW_LOOP, r'test-writer pracujący równolegle z R1 nie wstrzymuje rundy, bo R1 dotyczy kodu produkcyjnego'),
    ('k10-measurement-wypelnia-format', MEASUREMENT, r'wypełnia istniejący format `- HH:MM zrobione: …`.*a nie zastępuje go'),
    ('k9-spec-zamrozony-z-zalozeniami', SPEC, r'Po „GO plan” spec jest zamrożony razem z nimi: nie przenoś ich do „Decyzji”'),
    ('k9-spec-bez-blokujacego-q', SPEC, r'`draft` bez blokującego `Q` \(także z samymi \[ZAŁOŻENIE\]\) \| `approved\*`'),
    ('k9-spec-zalozenia-takze-po-go-plan', SPEC, r'założenia `\[ZAŁOŻENIE\]` zostają w specu jako jawne założenia także po „GO plan”'),
    ('k10-skill-plan-bez-speca', SKILL, r'Plan bez speca \(`direct`, `bugfix`, wejście z klucza\) nie ma „GO plan”: pierwszy commit \(plan\) powstaje po „GO implement”'),
    ('k10-skill-sprawdzony-krok-planu', SKILL, r'a kolejne po każdym sprawdzonym kroku planu'),
    ('k3-plan-details-retro-dlug', PLAN_DETAILS, r'Przy `ceremony: demo` `retro-quality` jest odroczone do podsekcji „Dług \(ceremony: demo\)” w „Wyniku”'),
    ('k3-demo-max-2-rundy', CEREMONY, r'jednorundowe review `kod` ma \*\*najwyżej 2 rundy\*\*: druga tylko przy blockerze, przy co najmniej 3 majorach'),
    ('k3-demo-max-2-rundy-review-loop', REVIEW_LOOP, r'Tylko w `demo` jednorundowa pętla `kod` ma najwyżej 2 rundy'),
    ('k10-verifier-testy-tw', REVIEW_LOOP, r'czy testy pokrywają kryteria kroku i potrafią paść; dowód w rekordzie checkpointu kroku'),
    ('k9-plan-large-zalozenia-zostaja', PLAN_LARGE, r'założenia `\[ZAŁOŻENIE\]` zostają w specu'),
    ('k9-plan-large-bez-stop', PLAN_LARGE, r'przy samych założeniach `\[ZAŁOŻENIE\]` nie ma STOP'),
    ('k9-plan-large-odpowiedzi', PLAN_LARGE, r'Odpowiedzi użytkownika kasują pytania blokujące i uwagi C; założenia zostają'),
    ('k9-spec-przyjete-nowa-wartosc', SPEC, r'przechodzi do „Decyzji” albo dostaje nową wartość po „przyjęte:”'),
    # K9
    ('k9-spec-blokujace', SPEC, r'`- Q<n>: <pytanie>` jest \*\*blokujące\*\*: STOP'),
    ('k9-spec-zalozenie', SPEC, r'`- Q<n> \[ZAŁOŻENIE\]: <pytanie> — przyjęte: <wartość>` to \*\*założenie nieblokujące\*\*'),
    ('k9-spec-nie-zatrzymuje', SPEC, r'nie zatrzymuje „draft z Q”'),
    ('k9-spec-ostatni-podzial', SPEC, r'parser dzieli po \*\*ostatnim\*\* ` — przyjęte: `'),
    ('k9-spec-zakaz', SPEC, r'Nie oznaczaj jako \[ZAŁOŻENIE\] pytań o kontrakt, dane, migrację ani bezpieczeństwo'),
    ('k9-spec-tabela-wznowienia', SPEC, r'\| `draft` z blokującym `Q` \| brak \|'),
    ('k9-skill-klasy', SKILL, r'`- Q<n> \[ZAŁOŻENIE\]: <pytanie> — przyjęte: <wartość>`'),
    ('k9-skill-nie-zatrzymuje', SKILL, r'nie zatrzymuje „draft z Q”'),
    ('k9-skill-zakaz', SKILL, r'Nie oznaczaj jako \[ZAŁOŻENIE\] pytań o kontrakt, dane, migrację ani bezpieczeństwo'),
    # K10
    ('k10-skill-go-plan-commit', SKILL, r'„GO plan” = jeden lokalny commit\.\*\* Przy „GO plan” powstaje jeden commit lokalny: spec, plan-szkic i prototyp'),
    ('k10-skill-galaz-worktree', SKILL, r'na gałęzi roboczej w osobnym worktree; jeśli jej nie ma, zakłada ją „GO plan”'),
    ('k10-skill-bez-pusha', SKILL, r'Bez pusha: przed „GO implement” nic nie opuszcza maszyny'),
    ('k10-skill-wyjatek-klucz', SKILL, r'wejście z klucza zgłoszenia zostaje bez commita przy „GO plan”'),
    ('k10-skill-skan-sekretow', SKILL, r'`git add` tylko z jawnymi ścieżkami, skan sekretów przed commitem'),
    ('k10-plan-large-go-plan', PLAN_LARGE, r'„GO plan” to też jeden lokalny commit: spec, plan-szkic i prototyp'),
    ('k10-plan-large-bez-pusha', PLAN_LARGE, r'Bez pusha: przed „GO implement” nic nie opuszcza maszyny'),
    ('k10-plan-large-wyjatek', PLAN_LARGE, r'wejście z klucza zgłoszenia zostaje bez commita przy „GO plan”'),
    ('k10-base-go-plan', BASE, r'„GO plan” = lokalny commit speca, planu i prototypu \(bez niego: plan po „GO implement”\)'),
    ('k10-base-sprawdzony-krok', BASE, r'dalej commit po każdym sprawdzonym kroku planu'),
    ('k10-base-osobny-worktree', BASE, r'Od „GO plan” \(bez niego: „GO implement”\) osobny worktree i gałąź robocza'),
    ('k10-base-przed-go-implement', BASE, r'Przed „GO implement” nic nie opuszcza maszyny'),
    ('k10-kontrakt-najpierw', SKILL, r'Kontrakt najpierw: faza z FE i BE zaczyna od kroku kontraktu \(DTO, endpointy, migawka kontraktu w commicie\)'),
    ('k10-bez-tury-dopasowania', SKILL, r'bez tury „dopasowania”'),
    ('k10-kontrakt-plan-details', PLAN_DETAILS, r'Faza z FE i BE zaczyna od kroku kontraktu'),
    ('k10-tw-r1', SKILL, r'Test-writer pracuje równolegle z review R1 fazy \(jego testy integracyjne sprawdza runda 2 albo, w pętli jednorundowej, świeży `verifier` przed odhaczeniem kroku sprawdza testy test-writera względem kryteriów kroku \(czy je pokrywają i czy potrafią paść\)'),
    ('k10-tw-r1-review-loop', REVIEW_LOOP, r'Test-writer fazy pracuje równolegle z rundą R1 tej fazy.*tylko w fazach, które mają test-writera w planie'),
    ('k10-dwa-plany', SKILL, r'spec z co najmniej 5 zdolnościami to dwa plany'),
    ('k10-dwa-plany-plan-large', PLAN_LARGE, r'Spec z co najmniej 5 zdolnościami to dwa plany'),
    ('k10-szacunek', SKILL, r'ok\. 75–105 min pracy na zdolność z pełną pętlą review'),
    ('k10-jedna-linia-skill', SKILL, r'jedna linia na podkrok `Fn\.k` \(podkrok, wynik albo sha, czas\)'),
    ('k10-jedna-linia-measurement', MEASUREMENT, r'„jedna linia na podkrok”: po każdym podkroku `Fn\.k` dopisz jedną linię \(podkrok, wynik albo sha, czas\), albo co 30 minut, co nastąpi pierwsze'),
    ('k10-jedna-linia-plan-details', PLAN_DETAILS, r'jedna linia na podkrok: podkrok, wynik albo sha, czas'),
    # K12
    ('k12-znacznik-ui', PLAN_DETAILS, r'znacznik `\[UI\]` w komórce `Id` albo `Krok` tabeli kroków'),
    ('k12-faza-wymaga-akceptacji', PLAN_DETAILS, r'faza `\[UI\]` wymaga akceptacji prototypu przed startem'),
    ('k12-gate-phase', PLAN_DETAILS, r'`skillsync prototype gate <projekt> --plan <plan> --phase <Fn>`; FAIL albo BLOCKED = faza nie startuje'),
    ('k12-brak-json-blocked', PLAN_DETAILS, r'Brak JSON-a przy kodzie wyjścia 2 \(starszy `skillsync` bez `--phase`\) = BLOCKED'),
    ('k12-fazy-bez-znacznika', PLAN_DETAILS, r'fazy bez znacznika startują po „GO implement”'),
    ('k12-fail-nie-blokuje-faz', PLAN_DETAILS, r'FAIL bramki całego planu nie blokuje faz bez znacznika ani `readiness`; o starcie fazy decyduje `prototype gate --phase <Fn>`'),
    ('k12-skill-fail-nie-blokuje-faz', SKILL, r'FAIL bramki całego planu nie blokuje faz bez znacznika ani `readiness`; o starcie fazy decyduje `prototype gate --phase <Fn>`'),
    ('k12-ui-wymaga-prototypu', PLAN_DETAILS, r'Faza `\[UI\]` wymaga linii `Prototyp:`; bez niej `gate` i `readiness` dają BLOCKED `ui_phase_without_prototype`'),
    ('k12-prototyp-bez-ui', PLAN_DETAILS, r'Plan z linią `Prototyp:` bez żadnej fazy `\[UI\]` działa jak dotąd: cały plan za bramką'),
    ('k12-go-implement-podzial', PLAN_DETAILS, r'wymienia jawnie fazy `\[UI\]` i fazy bez znacznika \(pola `uiPhases` i `nonUiPhases` z `skillsync readiness`\)'),
    ('k12-skill-gate-phase', SKILL, r'`skillsync prototype gate <projekt> --plan <plan> --phase <Fn>` \(FAIL albo BLOCKED = faza nie startuje; brak JSON-a przy kodzie wyjścia 2 = BLOCKED\)'),
    ('k12-skill-go-implement-podzial', SKILL, r'prośba o „GO implement” wymienia jawnie fazy `\[UI\]` i fazy bez znacznika'),
]

# Pliki, które muszą zostać wskazane z SKILL.md (po normalizacji).
LINKED_FROM_SKILL = ['references/ceremony-demo.md', 'references/spec.md', 'references/plan-details.md#bramka-prototypu']
EDITED_SKILLS = ['skills/feature/SKILL.md']
# Wzorzec składany z fragmentów, żeby sam plik testu nie zawierał nazwy (grep w bibliotece i testach ma dać 0).
CLIENT_PATTERN = 'hac' + 'aton|hac' + 'kathon'
CLIENT_NAMES = re.compile(CLIENT_PATTERN + '|' + '/' + 'home/', re.I)


def norm(text):
    return re.sub(r'\s+', ' ', text)


def missing(lib):
    """Zwraca id wymaganych fraz, których brakuje w bibliotece `lib`."""
    cache, out = {}, []
    for pid, rel, rx in REQUIRED:
        if rel not in cache:
            p = Path(lib) / rel
            cache[rel] = norm(p.read_text(encoding='utf-8')) if p.is_file() else ''
        if not re.search(rx, cache[rel]):
            out.append(pid)
    return out


@SKIP
class FeatureDemoPhraseTests(unittest.TestCase):
    def test_required_phrases_present(self):
        self.assertEqual(missing(LIB), [], 'brakujące frazy FEAT-DEMO w skillu feature albo _base.md')

    def test_each_assertion_can_fail(self):
        """Dowód mutacyjny: usunięcie dopasowanej frazy z kopii biblioteki zgłasza dokładnie tę frazę."""
        with tempfile.TemporaryDirectory() as tmp:
            for pid, rel, rx in REQUIRED:
                with self.subTest(phrase=pid):
                    root = Path(tmp) / pid
                    for f in {rel for _, rel, _ in REQUIRED}:
                        dst = root / f
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy(Path(LIB) / f, dst)
                    target = root / rel
                    text = norm(target.read_text(encoding='utf-8'))
                    m = re.search(rx, text)
                    self.assertIsNotNone(m, 'fraza musi istnieć, by dało się ją usunąć')
                    target.write_text(re.sub(rx, '', text), encoding='utf-8')
                    self.assertIn(pid, missing(root))

    def test_missing_file_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(len(missing(tmp)), len(REQUIRED))

    def test_ceremony_demo_is_linked_from_skill(self):
        text = norm((Path(LIB) / SKILL).read_text(encoding='utf-8'))
        for target in LINKED_FROM_SKILL:
            with self.subTest(target=target):
                self.assertIn(f'({target})', text)
                self.assertTrue((Path(LIB) / 'skills/feature' / target.split('#')[0]).is_file())

    def test_go_plan_commit_stays_local_and_cp_rules_untouched(self):
        base = norm((Path(LIB) / BASE).read_text(encoding='utf-8'))
        self.assertLess(base.index('„GO plan” = lokalny commit speca'), base.index('`pr`: „GO implement” uruchamia `pr-run`'))
        for kept in ('`CP` — commit i push bieżącej gałęzi roboczej', '`READY` — tylko w `pr` i tylko od użytkownika',
                     'Zgody tylko od użytkownika: słowo zgody działa tylko wpisane przez użytkownika'):
            self.assertIn(kept, base)

    def test_no_client_names_in_public_core(self):
        hits = []
        for sub in ('skills/feature', 'instructions'):
            for path in sorted((Path(LIB) / sub).rglob('*.md')):
                for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                    if CLIENT_NAMES.search(line):
                        hits.append(f'{path.relative_to(LIB)}:{number}')
        self.assertEqual(hits, [])
        mutated = 'etapy hac' + 'kathonu'
        self.assertIsNotNone(CLIENT_NAMES.search(mutated), 'wzorzec musi łapać nazwę klienta')

    def test_grep_equivalent_returns_zero(self):
        if shutil.which('grep') is None:
            self.skipTest('grep not available')
        pattern = CLIENT_PATTERN + '|' + '/' + 'home/'
        targets = [str(Path(LIB) / 'skills/feature'), str(Path(LIB) / 'instructions'), str(Path(__file__).resolve())]
        proc = subprocess.run(['grep', '-rniE', pattern, *targets], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, f'grep: 0 trafień = rc 1, rc 2 = błąd; stdout={proc.stdout!r} stderr={proc.stderr!r}')
        self.assertEqual(proc.stdout, '')


@SKIP
class FeatureDemoFrontmatterTests(unittest.TestCase):
    """Frontmatter edytowanych SKILL.md musi parsować się jako YAML."""

    def test_edited_skill_frontmatter_is_valid_yaml(self):
        for rel in EDITED_SKILLS:
            with self.subTest(skill=rel):
                match = re.match(r'---\n(.*?)\n---\n', (Path(LIB) / rel).read_text(encoding='utf-8'), re.S)
                self.assertIsNotNone(match, 'missing frontmatter')
                data = yaml.safe_load(match.group(1))
                self.assertIsInstance(data, dict)
                self.assertTrue(data.get('name') and data.get('description'))

    def test_the_check_can_fail(self):
        with self.assertRaises(yaml.YAMLError):
            yaml.safe_load('name: x\ndescription: a (b: c)')


if __name__ == '__main__':
    unittest.main()
