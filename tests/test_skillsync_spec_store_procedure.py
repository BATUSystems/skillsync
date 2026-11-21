"""Strażnik treści procedury trybu `specs.tracked: false` (CORE-SPECSTORE, F6.3).

Sprawdza frazy kluczowe, odsyłacze do `spec-store.md` i spójność trybu w skillach `feature`, `pr-run`, `bugfix`
oraz `docs/workflow.md`.  Biblioteka z SKILLSYNC_TEST_LIBRARY (bez niej testy są pomijane: skip to nie PASS);
`docs/workflow.md` czytane jest z korzenia repozytorium.
"""
import os
import re
import unittest
from pathlib import Path

LIB = os.environ.get('SKILLSYNC_TEST_LIBRARY')
SKIP = unittest.skipUnless(LIB, 'set SKILLSYNC_TEST_LIBRARY to a library with the spec-store procedure')
REPO = Path(__file__).resolve().parent.parent

FEATURE = 'skills/feature'
STORE = f'{FEATURE}/references/spec-store.md'
LINKING = [f'{FEATURE}/SKILL.md'] + [f'{FEATURE}/references/{n}.md' for n in (
    'plan-large', 'spec', 'plan-details', 'workdir', 'worker-mode', 'pr-after-approval', 'ticket-intake')]
STORE_KEYWORDS = ['specs.tracked', 'spec snapshot', '--check', 'planState', 'go-plan', 'check-ignore']
MODE_MARK = re.compile(r'spec-store|specs\.tracked: false')
# frazy twierdzące, że „GO plan” jest commitem lub ma commit (nie: listy zgód „GO plan, commit/push”, negacje „bez commita”)
GO_PLAN_COMMIT = re.compile(
    r'GO plan[^.]{0,100}(jeden lokalny commit|powstaje jeden commit|lokalny commit)'
    r'|commit(em)? „GO plan”|zacommitowane lokalnie już przy „GO plan”|commit powstaje przy „GO plan”', re.I)
WINDOW = 25  # linie w górę i w dół od wzmianki o „GO plan” jako commicie


def has_mode_link(text):
    return 'spec-store.md' in text or 'specs.tracked: false' in text


def missing_store_keywords(text):
    return [k for k in STORE_KEYWORDS if k not in text]


def missing_pr_run_phrases(raw):
    """Frazy kroku 4 pr-run (kotwica: cały plik, nie pierwszy `tracked: false`)."""
    text = norm(raw)
    return [p for p in (r'draft PR powstaje dopiero po pierwszym commicie kodu', r'`PR:` trafia tylko do lokalnego planu')
            if not re.search(p, text)]


def missing_step_phrases(raw):
    """Kroki 6 i 8 pr-run: „Wynik”/done/implemented lokalnie oraz `spec snapshot --check --to` przed sprzątaniem."""
    text = norm(raw)
    step6 = re.search(r'6\. \*\*Bramka końcowa\.\*\*(.*?)(?= 7\. \*\*)', text)
    step8 = re.search(r'8\. \*\*Przed gotowością(.*?)(?=## )', text)
    out = []
    if not (step6 and re.search(r'tracked: false.{0,120}„Wynik”, `done` i `implemented`.{0,300}nie commitujesz', step6.group(1))):
        out.append('krok 6')
    if not (step8 and re.search(r'tracked: false.{0,150}`skillsync spec snapshot --check --to <korzeń kopii poza worktree>`.{0,40}`PASS`', step8.group(1))):
        out.append('krok 8')
    return out


def norm(text):
    return re.sub(r'\s+', ' ', text)


def read(rel):
    if rel.startswith('docs/'):
        return (REPO / rel).read_text(encoding='utf-8')
    return (Path(LIB) / rel).read_text(encoding='utf-8')


def go_plan_commit_gaps(rel, text):
    """Wzmianki „GO plan” jako commitu bez odsyłacza do trybu w oknie ±WINDOW linii; zwraca `plik:linia`."""
    lines = text.splitlines()
    gaps = []
    for i, line in enumerate(lines):
        if not GO_PLAN_COMMIT.search(line):
            continue
        window = '\n'.join(lines[max(0, i - WINDOW):i + WINDOW + 1])
        if not MODE_MARK.search(window):
            gaps.append(f'{rel}:{i + 1}')
    return gaps


@SKIP
class SpecStoreReferenceTests(unittest.TestCase):
    def test_spec_store_exists_with_key_phrases(self):
        path = Path(LIB) / STORE
        self.assertTrue(path.is_file(), STORE)
        text = path.read_text(encoding='utf-8')
        absent = missing_store_keywords(text)
        self.assertEqual(absent, [], f'spec-store.md bez fraz: {absent}')

    def test_procedure_files_link_to_spec_store(self):
        gaps = []
        for rel in LINKING:
            text = read(rel)
            if not has_mode_link(text):
                gaps.append(rel)
        self.assertEqual(gaps, [], f'brak odsyłacza do spec-store.md lub trybu: {gaps}')

    def test_each_link_check_can_fail(self):
        # asercje działają na zmodyfikowanej treści: usunięcie odsyłacza / dopisanie niezabezpieczonego „GO plan” = wykryta luka
        self.assertEqual(go_plan_commit_gaps('x.md', '„GO plan” to jeden lokalny commit.\n'), ['x.md:1'])
        self.assertEqual(go_plan_commit_gaps('x.md', 'Pierwszy commit powstaje przy „GO plan”.\n'), ['x.md:1'])
        self.assertEqual(go_plan_commit_gaps('x.md', '„GO plan” to jeden lokalny commit.\nZob. spec-store.\n'), [])
        stripped = read(f'{FEATURE}/references/plan-large.md').replace('spec-store', 'x').replace('specs.tracked: false', 'x')
        self.assertTrue(has_mode_link(read(f'{FEATURE}/references/plan-large.md')))
        self.assertFalse(has_mode_link(stripped))
        store = read(STORE).replace('go-plan', 'xx')
        self.assertTrue(missing_store_keywords(store))
        self.assertTrue(missing_pr_run_phrases(read('skills/pr-run/SKILL.md').replace('draft PR powstaje dopiero', 'x')))
        self.assertTrue(missing_step_phrases(read('skills/pr-run/SKILL.md').replace('--check --to', '--zz')))

    def test_pr_run_defers_draft_pr_until_first_code_commit(self):
        raw = read('skills/pr-run/SKILL.md')
        self.assertEqual(missing_pr_run_phrases(raw), [])

    def test_pr_run_steps_6_and_8_have_untracked_mode(self):
        self.assertEqual(missing_step_phrases(read('skills/pr-run/SKILL.md')), [])

    def test_worker_mode_exempts_snapshot_docs_from_copy_conflict(self):
        text = norm(read(f'{FEATURE}/references/worker-mode.md'))
        self.assertRegex(text, r'poddrzewo `docs/` migawki jest nadpisywane i nie jest dowodem konfliktu kopii')
        self.assertRegex(text, r'spec snapshot --check --to <główne repo>')

    def test_templates_bugfix_and_workflow_doc_mention_mode(self):
        for rel in ('skills/pr-run/references/templates.md', 'skills/bugfix/SKILL.md', 'docs/workflow.md'):
            self.assertIn('specs.tracked: false', read(rel), rel)

    def test_go_plan_as_commit_has_mode_reference_nearby(self):
        gaps = []
        files = [f'{FEATURE}/SKILL.md', 'skills/pr-run/SKILL.md', 'skills/bugfix/SKILL.md', 'docs/workflow.md']
        files += [f'{FEATURE}/references/{p.name}' for p in sorted((Path(LIB) / FEATURE / 'references').glob('*.md'))
                  if p.name != 'spec-store.md']
        for rel in files:
            p = Path(LIB) / rel if not rel.startswith('docs/') else REPO / rel
            if p.is_file():
                gaps += go_plan_commit_gaps(rel, p.read_text(encoding='utf-8'))
        self.assertEqual(gaps, [], f'„GO plan” jako commit bez odsyłacza do trybu tracked: false (±{WINDOW} linii): {gaps}')

    def test_go_plan_step_name_is_shared_beyond_spec_store(self):
        # `go-plan` ma występować w spec-store.md ORAZ w co najmniej jednym pliku procedury (checkpointy)
        roots = [Path(LIB) / 'skills/workflow', Path(LIB) / FEATURE, REPO / 'docs/workflow.md']
        others = []
        for root in roots:
            for p in ([root] if root.is_file() else sorted(root.rglob('*.md'))):
                if p.name == 'spec-store.md':
                    continue
                if 'go-plan' in p.read_text(encoding='utf-8'):
                    others.append(str(p))
        self.assertTrue(others, 'nazwa kroku `go-plan` istnieje tylko w spec-store.md (ryzyko: brak w procedurze checkpointów)')
        # sprawdzenie, że to krok checkpointu, a nie przypadkowy dopisek
        store = norm(read(STORE))
        self.assertRegex(store, r'`skillsync checkpoint [^`]*--step go-plan --status PASS --evidence <plik migawki> --next [^`]+`')


if __name__ == '__main__':
    unittest.main()
