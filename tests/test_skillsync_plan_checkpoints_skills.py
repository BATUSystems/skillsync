"""Phrase guard of the library for the review checkpoints (1.6.0 package B): the human summary of a plan, the review profile
`prototyp` and the rule that names the review remarks, the `Wynik` template. Library from SKILLSYNC_TEST_LIBRARY (or ROOT/library).
Every required phrase must be able to fail: the second test removes each one from a copy of its file and expects exactly that id missing."""
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = Path(os.environ.get('SKILLSYNC_TEST_LIBRARY') or ROOT / 'library')

PLAN_DETAILS = 'skills/feature/references/plan-details.md'
REVIEW_LOOP = 'skills/feature/references/review-loop.md'
FEATURE = 'skills/feature/SKILL.md'

# (id, file, regular expression after whitespace normalisation)
REQUIRED = [
    ('summary-heading', PLAN_DETAILS, r'sekcją `## Streszczenie dla człowieka` \(nagłówek dosłowny, zaraz po liniach metadanych\)'),
    ('summary-limit', PLAN_DETAILS, r'do 40 linii i do 4000 znaków'),
    ('summary-blocks', PLAN_DETAILS, r'\*\*Co zmieniamy\*\*, \*\*Czego nie robimy\*\*, \*\*Ryzyka\*\*, \*\*Decyzje do podjęcia\*\*'),
    ('summary-warning', PLAN_DETAILS, r'ostrzegają \(warning, bez blokady\), gdy plan `draft` albo `approved` nie ma tej sekcji'),
    ('summary-status-field', PLAN_DETAILS, r'`skillsync status` podaje sekcję w `plans\[\]\.summary`'),
    ('result-template', PLAN_DETAILS, r'## Wynik: wzór'),
    ('result-remarks-block', PLAN_DETAILS, r'### Uwagi z review'),
    ('result-remarks-rule', PLAN_DETAILS, r'wszystkie uwagi wagi `blocker` i `major`.*z tytułem, wagą i tym, co zrobiono.*także gdy uwaga została naniesiona'),
    ('result-coverage-table', PLAN_DETAILS, r'tabela „kryterium → test\(y\) → wynik”.*bez testu.*nie twarda bramka'),
    ('review-profile-row', REVIEW_LOOP, r'\| `prototyp` \|'),
    ('review-profile-select', REVIEW_LOOP, r'Profil `prototyp` wybiera `skillsync review` \(pole `profile`, albo `--profile prototyp`\)'),
    ('review-report-name', REVIEW_LOOP, r'`prototype-review-N\.md`'),
    ('review-core-list', REVIEW_LOOP, r'`workflow\.review\.prototype`.*warstwy, projekt i manifest tylko dopisują pozycje'),
    ('review-naming-rule', REVIEW_LOOP, r'\*\*Reguła wymieniania uwag:\*\*.*`blocker` i `major`.*także gdy została naniesiona; `minor` tylko liczbą'),
    ('acceptance-step', PLAN_DETAILS, r'\*\*Krok „Odbiór”\*\*.*\*\*listę kontrolną\*\*.*tabelę pokrycia'),
    ('acceptance-is-closing-word', PLAN_DETAILS, r'\*\*Odbiorem jest końcowe słowo zgody trybu dostarczania\*\*.*`READY` w trybie `pr`, `CP` w trybie `direct` \(żadnego nowego słowa'),
    ('acceptance-needs-human', PLAN_DETAILS, r'`needsHuman: acceptance` do czasu wpisu tego słowa w `\.skillsync/runs/<run-id>/approvals\.jsonl` dla bieżącego HEAD'),
    ('acceptance-not-by-plan', PLAN_DETAILS, r'zaznaczenie kroku ani linia w planie niczego nie gaszą'),
    ('acceptance-vs-closure', PLAN_DETAILS, r'`closure` i `acceptance` nie występują naraz'),
    ('feature-acceptance', FEATURE, r'Końcowe słowo zgody trybu dostarczania \(`READY` w `pr`, `CP` w `direct`\) jest też odbiorem'),
    ('feature-pointer', FEATURE, r'plan zaczyna się sekcją `## Streszczenie dla człowieka`.*„Wynik” ma wzór z blokiem „Uwagi z review” i tabelą pokrycia'),
]


def norm(text):
    return re.sub(r'\s+', ' ', text)


def read(rel):
    path = LIB / rel
    return norm(path.read_text(encoding='utf-8')) if path.is_file() else ''


def missing(overrides=None):
    overrides = overrides or {}
    return [pid for pid, rel, rx in REQUIRED if not re.search(rx, overrides.get(rel, read(rel)))]


class PlanCheckpointsSkills(unittest.TestCase):
    def test_the_library_has_every_required_phrase(self):
        self.assertEqual(missing(), [])

    def test_each_phrase_can_fail(self):
        for pid, rel, rx in REQUIRED:
            with self.subTest(phrase=pid):
                text = read(rel)
                self.assertIsNotNone(re.search(rx, text), pid)
                broken = re.sub(rx, '', text)   # every occurrence: a phrase that stays elsewhere would hide the removal
                self.assertIn(pid, missing({rel: broken}))

    def test_the_template_names_no_client_or_event(self):
        for rel in (PLAN_DETAILS, REVIEW_LOOP):
            self.assertIsNone(re.search('hac' + 'aton|hac' + 'kathon|/' + 'home/', read(rel), re.I), rel)


if __name__ == '__main__':
    unittest.main()
