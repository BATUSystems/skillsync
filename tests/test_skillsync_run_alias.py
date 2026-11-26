"""CORE-1130-I18N-ALIASES, S2: angielski alias `Run:` linii `Przebieg:` planu.

Czytamy oba języki, zapisujemy po polsku. Źródło prawdy gramatyki to `protocol_tokens.RUN_LINE` (`Przebieg:` w całym tekście,
`Run:` tylko w nagłówku przed pierwszym `## `, wcześniejsza linia wygrywa). Miejsca czytające: `quality._PLAN_RUN`, `test_gate._RUN_LINE`
(`plan_for_run`, `run_of_plan`), `status` (`pre_implement_state`, `_plan_text_for_spec`) i samodzielny hook `check-closure.py`.

Założenie spoza kontraktu (do ustalenia z implementerem): hook wystawia funkcję `find_run(text)` zwracającą `re.Match` albo None
(`.group(1)` = id) i używa jej w `main` zamiast `_RUN.search(head)`; test różnicowy porównuje ją z `RUN_LINE.search`.

Dowód zdolności do porażki (mutacje, wykonane na kopii kodu w głowie/lokalnie, nie w repozytorium):
- `RUN_LINE`/`_PLAN_RUN` bez ograniczenia do nagłówka (`Run:` w całym tekście) -> padają `test_run_in_body_does_not_bind_*`,
  `test_hook_matches_run_line_on_corpus`, `test_hook_ignores_run_in_body`;
- hook bez wyszukiwania `Run:` -> padają `test_hook_blocks_done_plan_with_run_line` i różnicowy;
- `Run:` ignorowany w `quality`/`test_gate`/`status` -> padają testy PL=EN odpowiednich klas.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillsync_workflow import protocol_tokens, quality, status, test_gate  # noqa: E402
from skillsync_workflow.protocol_tokens import RUN_LINE  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
_LIBRARY = os.environ.get('SKILLSYNC_TEST_LIBRARY')
HOOK_SOURCE = Path(_LIBRARY) / 'hooks' / 'check-closure.py' if _LIBRARY else REPO / 'library' / 'hooks' / 'check-closure.py'
needs_hook = unittest.skipUnless(HOOK_SOURCE.is_file(), 'set SKILLSYNC_TEST_LIBRARY to a library with hooks/check-closure.py')

TOKENS = {'PL': 'Przebieg', 'EN': 'Run'}
PLAN_NAME = '2026-11-22-demo.md'
SPEC = '# Spec\n\nStatus: approved\n\n## Kontrakty\n\n- `status` dostaje pole.\n\n## Migracja\n\nNie dotyczy.\n'


def plan(token='Przebieg', run='R-1', status_word='done', body='', spec=None):
    lines = ['# Plan: demo', '', f'Status: {status_word}']
    if spec:
        lines.append(f'Spec: {spec}')
    if run is not None:
        lines.append(f'{token}: {run}')
    return '\n'.join(lines) + '\n\n## Progress\n\n- [x] F1 krok\n' + body


import sys as _sys_tmp_paths  # noqa: E402
from pathlib import Path as _Path_tmp_paths  # noqa: E402
_sys_tmp_paths.path.insert(0, str(_Path_tmp_paths(__file__).resolve().parent))  # noqa: E402
from tmp_paths import child, descend  # noqa: E402
class TmpRoot(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='run-alias-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / 'docs/plans').mkdir(parents=True)

    def put(self, rel, text):
        path = descend(self.root, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='')   # exact bytes: Windows would write \r\n and the reader returns the file as it is
        return path

    def run_record(self, run_id, final):
        records = [{'step': 'a', 'status': 'PASS', 'next': 'n'}]
        if final:
            records[-1]['final'] = True
        self.put(f'.skillsync/runs/{run_id}.json', json.dumps({'schema_version': 1, 'run_id': run_id, 'checkpoints': records}))


# ---------------------------------------------------------------------------------------------------------------
# quality: zamknięcie planu
# ---------------------------------------------------------------------------------------------------------------
class QualityRunLine(TmpRoot):
    def reasons(self, text):
        self.put(f'docs/plans/{PLAN_NAME}', text)
        return [reason for rel, reason in quality.plan_closure_findings(self.root) if rel == f'docs/plans/{PLAN_NAME}']

    def test_plan_run_regex_reads_both_tokens(self) -> None:
        for name, token in TOKENS.items():
            with self.subTest(name):
                match = quality._PLAN_RUN.search(plan(token, 'ACC-1')[:4000])
                self.assertIsNotNone(match)
                self.assertEqual(match.group(1), 'ACC-1')

    def test_plan_run_regex_tolerates_crlf_and_backticks_for_run(self) -> None:
        for eol in ('\n', '\r\n'):
            for line in ('Run: `ACC-1`', 'Run:ACC-1  ', 'Run: ACC-1\t'):
                with self.subTest(eol=repr(eol), line=line):
                    match = quality._PLAN_RUN.search(f'# P\n{line}{eol}Status: done{eol}')
                    self.assertIsNotNone(match)
                    self.assertEqual(match.group(1), 'ACC-1')

    def test_run_in_body_does_not_bind_plan(self) -> None:
        text = plan('Przebieg', None) + '\nRun: R-BODY\n'
        self.assertIsNone(quality._PLAN_RUN.search(text))
        self.assertEqual(self.reasons(text), [])   # bez agentflow brak linii nie jest ustaleniem; `Run:` w treści nie wiąże planu

    def test_done_plan_without_final_gives_the_same_finding_for_run_and_przebieg(self) -> None:
        self.run_record('R-1', final=False)
        pl = self.reasons(plan('Przebieg', 'R-1'))
        en = self.reasons(plan('Run', 'R-1'))
        self.assertEqual(len(pl), 1)
        self.assertEqual(pl, en)
        self.assertIn('run `R-1` has no `final` record', en[0])

    def test_done_plan_with_final_has_no_closure_finding_for_both(self) -> None:
        self.run_record('R-1', final=True)
        self.assertEqual(self.reasons(plan('Przebieg', 'R-1')), [])
        self.assertEqual(self.reasons(plan('Run', 'R-1')), [])

    def test_invalid_run_id_is_flagged_for_both_tokens(self) -> None:
        for run in ('../x', '-bad'):
            with self.subTest(run=run):
                self.assertEqual(len(self.reasons(plan('Przebieg', run))), 1)
                self.assertEqual(self.reasons(plan('Przebieg', run)), self.reasons(plan('Run', run)))
        # id z białym znakiem nie pasuje do gramatyki linii (jak dawny regex): brak linii, a nie „Invalid run id”; PL = EN
        with self.subTest(run='a b/c'):
            self.assertEqual(self.reasons(plan('Przebieg', 'a b/c')), self.reasons(plan('Run', 'a b/c')))

    def test_missing_line_is_flagged_for_agentflow_and_run_line_silences_it(self) -> None:
        text = plan(None, None)
        self.assertEqual(len(quality.plan_closure_findings(self._with(text), agentflow=True)), 1)
        # `Run:` w nagłówku = plan ma linię, więc nie ma ustalenia „brak linii”; zostaje stan przebiegu (nie istnieje).
        found = quality.plan_closure_findings(self._with(plan('Run', 'R-9')), agentflow=True)
        # assertions changed on purpose (CORE-1170-STOPHOOK): a run with no trace on this machine (another machine or checkout) is not reported:
        # this session cannot close it, whether or not the plan has a Result section
        self.assertEqual(found, [])

    def test_a_done_plan_of_another_machine_is_not_reported_but_a_local_unclosed_run_is(self) -> None:
        """CORE-1170-STOPHOOK: `.skillsync/runs` is outside git, so a plan merged from another machine has no local run."""
        for text in (plan('Run', 'R-9') + '\n## Wynik\n\nGotowe, szczegoly w PR.\n', plan('Run', 'R-9')):
            with self.subTest(result='## Wynik' in text):
                self.assertEqual(quality.plan_closure_findings(self._with(text), agentflow=True), [])
        # a local trace without a `final` record is still an unclosed run of this machine
        (self.root / '.skillsync' / 'runs' / 'R-9').mkdir(parents=True)
        found = quality.plan_closure_findings(self._with(plan('Run', 'R-9')), agentflow=True)
        self.assertEqual(len(found), 1)
        self.assertIn('run `R-9` has no `final` record', found[0][1])

    def _with(self, text):
        self.put(f'docs/plans/{PLAN_NAME}', text)
        return self.root

    def test_closure_run_state_for_run_equals_przebieg(self) -> None:
        for run, prepare in (('R-1', None), ('R-2', 'dir'), ('R-3', 'final'), ('../x', None)):
            with self.subTest(run=run):
                if prepare == 'dir':
                    (self.root / '.skillsync/runs/R-2').mkdir(parents=True)
                elif prepare == 'final':
                    self.run_record('R-3', final=True)
                pl = quality._closure_run_state(self.root, plan('Przebieg', run)[:4000])
                en = quality._closure_run_state(self.root, plan('Run', run)[:4000])
                self.assertEqual(pl, en)
                self.assertNotIn('no `Przebieg:` line', en)
                self.assertNotIn('no `Run:` line', en)

    def test_closure_run_state_names_the_directory_state(self) -> None:
        self.assertIn('does not exist', quality._closure_run_state(self.root, plan('Run', 'R-1')[:4000]))
        (self.root / '.skillsync/runs/R-1').mkdir(parents=True)
        self.assertIn('has no `final` record', quality._closure_run_state(self.root, plan('Run', 'R-1')[:4000]))

    def test_in_progress_plan_all_checked_reports_run_state_for_run(self) -> None:
        text = plan('Run', 'R-1', status_word='in_progress')
        reasons = self.reasons(text)
        self.assertEqual(len(reasons), 1)
        self.assertNotIn('no `Przebieg:` line', reasons[0])
        self.assertNotIn('no `Run:` line', reasons[0])
        self.assertIn('run `R-1` does not exist', reasons[0])

    def test_polish_wording_unchanged_for_missing_line(self) -> None:
        reasons = self.reasons(plan(None, None, status_word='in_progress'))
        self.assertEqual(len(reasons), 1)
        self.assertIn('no `Przebieg:` line', reasons[0])


# ---------------------------------------------------------------------------------------------------------------
# test_gate
# ---------------------------------------------------------------------------------------------------------------
class TestGateRunLine(TmpRoot):
    def test_run_line_is_the_shared_object(self) -> None:
        self.assertIs(test_gate._RUN_LINE, RUN_LINE)
        self.assertIs(quality._PLAN_RUN, RUN_LINE)

    def test_run_of_plan_pl_equals_en(self) -> None:
        for run in ('R-1', 'ACC-2026.1'):
            with self.subTest(run=run):
                self.assertEqual(test_gate.run_of_plan(plan('Przebieg', run)), run)
                self.assertEqual(test_gate.run_of_plan(plan('Run', run)), run)

    def test_run_of_plan_rejects_unsafe_ids_for_both_tokens(self) -> None:
        for token in TOKENS.values():
            for run in ('../x', 'a/b', '', 'CON'):
                with self.subTest(token=token, run=run):
                    self.assertIsNone(test_gate.run_of_plan(plan(token, run)))

    def test_run_in_body_is_not_a_run_of_plan(self) -> None:
        self.assertIsNone(test_gate.run_of_plan(plan(None, None) + '\nRun: R-BODY\n'))

    def test_polish_in_body_is_still_a_run_of_plan(self) -> None:
        # Polskie `Przebieg:` czytane w całym tekście (bit w bit jak dotąd); czytane jest tylko pierwsze 4000 znaków.
        self.assertEqual(test_gate.run_of_plan('# P\n\n## Sekcja\nPrzebieg: R-9\n'), 'R-9')

    def test_both_tokens_earlier_line_wins(self) -> None:
        self.assertEqual(test_gate.run_of_plan('# P\nRun: R-EN\nPrzebieg: R-PL\n'), 'R-EN')
        self.assertEqual(test_gate.run_of_plan('# P\nPrzebieg: R-PL\nRun: R-EN\n'), 'R-PL')

    def test_plan_for_run_finds_plan_by_both_tokens(self) -> None:
        self.put('docs/plans/2026-11-22-pl.md', plan('Przebieg', 'R-PL'))
        self.put('docs/plans/2026-11-22-en.md', plan('Run', 'R-EN'))
        self.put('docs/plans/2026-11-22-body.md', plan(None, None) + '\nRun: R-BODY\n')
        for run, rel in (('R-PL', 'docs/plans/2026-11-22-pl.md'), ('R-EN', 'docs/plans/2026-11-22-en.md')):
            found = test_gate.plan_for_run(self.root, run)
            self.assertIsNotNone(found, run)
            self.assertEqual(found[0], rel)
        self.assertIsNone(test_gate.plan_for_run(self.root, 'R-BODY'))
        self.assertIsNone(test_gate.plan_for_run(self.root, 'R-NONE'))


# ---------------------------------------------------------------------------------------------------------------
# status: pre_implement_state, _plan_text_for_spec
# ---------------------------------------------------------------------------------------------------------------
class StatusRunLine(TmpRoot):
    def setUp(self):
        super().setUp()
        self.put('docs/specs/s.md', SPEC)

    def state(self, text):
        return status.pre_implement_state(self.root, 'docs/specs/s.md', text, 'approved')

    def test_pre_implement_state_binds_run_like_przebieg(self) -> None:
        self.put('.skillsync/runs/R-1/pre-implement.md', '\nWerdykt: READY\n\ntreść\n')
        pl = self.state(plan('Przebieg', 'R-1', 'approved', spec='docs/specs/s.md'))
        en = self.state(plan('Run', 'R-1', 'approved', spec='docs/specs/s.md'))
        self.assertEqual(pl['run'], 'R-1')
        self.assertEqual((pl['report'], pl['verdict'], pl['warning']), ('.skillsync/runs/R-1/pre-implement.md', 'READY', None))
        self.assertEqual(pl, en)

    def test_plan_state_for_run_finds_the_plan_by_run_line(self) -> None:
        """Cała ścieżka CP/READY (ochrona 409), `acceptance_pending` i bramka testów: `status.plan_state_for_run` → `test_gate.plan_for_run`.
        Plan z `Run:` w nagłówku daje ten sam stan co z `Przebieg:` (nie None); `Run:` w treści po `## ` nie wiąże planu."""
        for state in ('draft', 'approved', 'in_progress', 'done'):
            with self.subTest(state=state):
                self.put('docs/plans/p.md', plan('Przebieg', 'R-1', state))
                polish = status.plan_state_for_run(self.root, 'R-1')
                self.put('docs/plans/p.md', plan('Run', 'R-1', state))
                english = status.plan_state_for_run(self.root, 'R-1')
                self.assertEqual(polish, state)
                self.assertEqual(english, state)
        self.put('docs/plans/p.md', plan(None, None, 'approved') + '\n## Notes\nRun: R-1\n')
        self.assertIsNone(status.plan_state_for_run(self.root, 'R-1'))

    def test_pre_implement_state_without_report_warns_for_run_too(self) -> None:
        en = self.state(plan('Run', 'R-1', 'approved'))
        self.assertEqual(en['run'], 'R-1')
        self.assertIn('brak pre-implement', en['warning'])
        self.assertEqual(en, self.state(plan('Przebieg', 'R-1', 'approved')))

    def test_pre_implement_state_run_in_body_does_not_bind(self) -> None:
        self.put('.skillsync/runs/R-1/pre-implement.md', 'Werdykt: READY\n')
        state = self.state(plan(None, None, 'approved') + '\nRun: R-1\n')
        self.assertIsNone(state['run'])
        self.assertIsNone(state['report'])
        self.assertIn('brak pre-implement', state['warning'])

    def test_pre_implement_state_unsafe_run_id_is_no_run(self) -> None:
        for token in TOKENS.values():
            with self.subTest(token):
                self.assertIsNone(self.state(plan(token, '../x', 'approved'))['run'])

    def test_plan_text_for_spec_finds_plan_by_run_header(self) -> None:
        for name, token in TOKENS.items():
            with self.subTest(name):
                shutil.rmtree(self.root / 'docs/plans')
                text = plan(token, 'R-1', 'approved', spec='docs/specs/s.md')
                self.put('docs/plans/2026-11-22-p.md', text)
                self.assertEqual(status._plan_text_for_spec(self.root, 'docs/specs/s.md', SPEC), text)

    def test_plan_text_for_spec_run_in_body_does_not_bind(self) -> None:
        self.put('docs/plans/2026-11-22-p.md', plan(None, None, 'approved', spec='docs/specs/s.md') + '\nRun: R-1\n')
        self.assertIsNone(status._plan_text_for_spec(self.root, 'docs/specs/s.md', SPEC))

    def test_plan_text_for_spec_polish_unchanged(self) -> None:
        text = plan('Przebieg', 'R-1', 'approved', spec='docs/specs/s.md')
        self.put('docs/plans/2026-11-22-p.md', text)
        self.assertEqual(status._plan_text_for_spec(self.root, 'docs/specs/s.md', SPEC), text)


# ---------------------------------------------------------------------------------------------------------------
# hook: samodzielna kopia logiki RUN_LINE
# ---------------------------------------------------------------------------------------------------------------
CORPUS = [
    ('pl', '# P\nPrzebieg: R-1\n'),
    ('en', '# P\nRun: R-1\n'),
    ('pl-crlf', '# P\r\nPrzebieg: R-1\r\nStatus: done\r\n'),
    ('en-crlf', '# P\r\nRun: R-1\r\nStatus: done\r\n'),
    ('pl-backticks', '# P\nPrzebieg: `R-1`\n'),
    ('en-backticks', '# P\nRun: `R-1`\n'),
    ('pl-nospace', '# P\nPrzebieg:R-1\n'),
    ('en-nospace', '# P\nRun:R-1\n'),
    ('pl-trailing', '# P\nPrzebieg: R-1  \t\n'),
    ('en-trailing', '# P\nRun: R-1 \t\n'),
    ('pl-two-words', '# P\nPrzebieg: R-1 extra\n'),
    ('en-two-words', '# P\nRun: R-1 extra\n'),
    ('en-pytest-in-body', '# P\n\n## Kroki\nRun: pytest\n'),
    ('en-pytest-in-header', '# P\nRun: pytest -q\n'),
    ('both-pl-first', '# P\nPrzebieg: R-PL\nRun: R-EN\n'),
    ('both-en-first', '# P\nRun: R-EN\nPrzebieg: R-PL\n'),
    ('both-en-header-pl-body', '# P\nRun: R-EN\n\n## S\nPrzebieg: R-PL\n'),
    ('pl-body-en-header-later', '# P\n\n## S\nPrzebieg: R-PL\n'),
    ('en-body-only', '# P\n\n## S\nRun: R-BODY\n'),
    ('en-after-section-with-pl-header', '# P\nPrzebieg: R-PL\n\n## S\nRun: R-EN\n'),
    ('no-header-no-section-en', 'Run: R-1\n'),
    ('no-header-no-section-pl', 'Przebieg: R-1\n'),
    ('section-first-line-en', '## S\nRun: R-1\n'),
    ('empty-id-pl', '# P\nPrzebieg:\n'),
    ('empty-id-en', '# P\nRun:\n'),
    ('empty-id-en-backticks', '# P\nRun: ``\n'),
    ('lowercase-en', '# P\nrun: R-1\n'),
    ('lowercase-pl', '# P\nprzebieg: R-1\n'),
    ('indented-en', '# P\n  Run: R-1\n'),
    ('bullet-en', '# P\n- Run: R-1\n'),
    ('bold-en', '# P\n**Run:** R-1\n'),
    ('space-before-colon-en', '# P\nRun : R-1\n'),
    ('inline-en', '# P\nto jest Run: R-1\n'),
    ('empty-text', ''),
    ('h3-is-not-section', '# P\n### S\nRun: R-1\n'),
    ('section-no-space', '# P\n##S\nRun: R-1\n'),
    ('second-en-in-header', '# P\nRun: R-1\nRun: R-2\n'),
    ('unicode-pl', '# P\nPrzebieg: R-Ł\n'),
    ('fenced-pl-then-pl', '# P\n```\nPrzebieg: R-EX\n```\nPrzebieg: R-1\n'),
    ('fenced-en-then-en', '# P\n~~~\nRun: R-EX\n~~~\nRun: R-1\n'),
    ('fenced-crlf', '# P\r\n```text\r\nPrzebieg: R-EX\r\n```\r\nPrzebieg: R-1\r\n'),
    ('fenced-only', '# P\n```\nPrzebieg: R-EX\n```\n'),
    ('fenced-unclosed', '# P\n```\nPrzebieg: R-EX\n'),
    ('fenced-in-body', '# P\n\n## S\n```\nPrzebieg: R-EX\n```\nPrzebieg: R-1\n'),
    ('fenced-longer', '# P\n````\n```\nPrzebieg: R-EX\n````\nPrzebieg: R-1\n'),
    ('fenced-then-body-en', '# P\n```\nx\n```\n\n## S\nRun: R-BODY\n'),
]


@needs_hook
class HookLoaded(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('check_closure_under_test', HOOK_SOURCE)
        cls.hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.hook)

    def hook_find(self, text):
        find = getattr(self.hook, 'find_run', None)
        self.assertIsNotNone(find, 'hook musi wystawiać find_run(text) -> re.Match | None (lokalna kopia protocol_tokens.RUN_LINE)')
        return find(text)

    def test_corpus_is_large_enough(self) -> None:
        self.assertGreaterEqual(len(CORPUS), 25)

    def test_hook_matches_run_line_on_corpus(self) -> None:
        for name, text in CORPUS:
            with self.subTest(name):
                expected = RUN_LINE.search(text)
                actual = self.hook_find(text)
                self.assertEqual(None if actual is None else actual.group(1), None if expected is None else expected.group(1))
                self.assertEqual(actual is None, expected is None)

    def test_lines_in_code_blocks_are_skipped_by_the_source_and_the_hook(self) -> None:
        expected = {'fenced-pl-then-pl': 'R-1', 'fenced-en-then-en': 'R-1', 'fenced-crlf': 'R-1', 'fenced-only': None,
                    'fenced-unclosed': None, 'fenced-in-body': 'R-1', 'fenced-longer': 'R-1', 'fenced-then-body-en': None}
        texts = dict(CORPUS)
        for name, run in expected.items():
            with self.subTest(name):
                for found in (RUN_LINE.search(texts[name]), self.hook_find(texts[name])):
                    self.assertEqual(None if found is None else found.group(1), run)

    def test_corpus_has_positive_and_negative_cases(self) -> None:
        # Korpus nie może być zdegenerowany: są dopasowania w obu językach i brak dopasowania.
        results = {name: RUN_LINE.search(text) for name, text in CORPUS}
        self.assertTrue(results['en'] and results['pl'] and results['both-en-first'])
        self.assertEqual(results['both-en-first'].group(1), 'R-EN')
        self.assertEqual(results['both-pl-first'].group(1), 'R-PL')
        self.assertIsNone(results['en-body-only'])
        self.assertIsNone(results['inline-en'])


# ---------------------------------------------------------------------------------------------------------------
# CORE-CLOSURE-TRACKED (K5, K7): linia zamknięcia; kopia gramatyki w haku = `protocol_tokens.closure_line`
# ---------------------------------------------------------------------------------------------------------------
_SHA = '0123456789abcdef0123456789abcdef01234567'
_OK = ('2026-11-25', _SHA)
_LINE = f'Zamknięcie: final 2026-11-25 {_SHA}'
_EN = f'Closure: final 2026-11-25 {_SHA}'


def _padded(end):
    """Nagłówek, w którym linia zamknięcia kończy się dokładnie na znaku `end`."""
    return 'x' * (end - len(_LINE) - 1) + '\n' + _LINE + '\n'


# (nazwa, tekst, oczekiwany wynik według spec Z1)
CLOSURE_SPEC_CORPUS = [
    ('pl', f'# P\nPrzebieg: R\n{_LINE}\n', _OK),
    ('en', f'# P\nRun: R\n{_EN}\n', _OK),
    ('crlf-pl', f'# P\r\n{_LINE}\r\nStatus: done\r\n', _OK),
    ('crlf-en', f'# P\r\n{_EN}\r\n', _OK),
    ('trailing', f'# P\n{_LINE} \t\n', _OK),
    ('no-newline', _LINE, _OK),
    ('before-section', f'{_LINE}\n## S\n', _OK),
    ('ends-at-limit', _padded(4000), _OK),
    ('past-limit', _padded(4001), None),
    ('after-limit', 'x' * 4000 + '\n' + _LINE + '\n', None),
    ('ends-at-limit-crlf', _padded(4000)[:-1] + '\r\n', _OK),
    ('ends-at-limit-eof', _padded(4000)[:-1], _OK),
    ('cut-with-tail', _padded(4000)[:-1] + 'abc\n', None),   # po obcięciu do 4000 wyglądałaby na poprawną
    ('cut-with-words', _padded(4000)[:-1] + ' extra\n', None),
    ('cut-sha41', _padded(4000)[:-1] + 'a\n', None),
    ('valid-before-a-cut-line', _LINE + '\n' + 'x' * 4000, _OK),
    ('in-body-pl', f'# P\n\n## S\n{_LINE}\n', None),
    ('in-body-en', f'# P\n\n## S\n{_EN}\n', None),
    ('code-block-in-body', f'# P\n\n## S\n```\n{_LINE}\n```\n', None),
    ('section-first', f'## S\n{_LINE}\n', None),
    ('quoted', f'# P\n> {_LINE}\n', None),
    ('indented', f'# P\n  {_LINE}\n', None),
    ('inline', f'# P\nlinia {_LINE}\n', None),
    ('no-final', f'Zamknięcie: 2026-11-25 {_SHA}\n', None),
    ('other-word', f'Zamknięcie: done 2026-11-25 {_SHA}\n', None),
    ('no-date', f'Zamknięcie: final {_SHA}\n', None),
    ('no-sha', 'Zamknięcie: final 2026-11-25\n', None),
    ('short-date', f'Zamknięcie: final 2026-1-5 {_SHA}\n', None),
    ('sha39', f'Zamknięcie: final 2026-11-25 {_SHA[:39]}\n', None),
    ('sha41', f'Zamknięcie: final 2026-11-25 {_SHA}a\n', None),
    ('sha-not-hex', f'Zamknięcie: final 2026-11-25 {"z" * 40}\n', None),
    ('tail', f'{_LINE} extra\n', None),
    ('split-lines', f'Zamknięcie: final 2026-11-25\n{_SHA}\n', None),
    ('lowercase', f'zamknięcie: final 2026-11-25 {_SHA}\n', None),
    # bloki kodu w nagłówku: przykład niczego nie zamyka (K10)
    ('fence', f'# P\n```\n{_LINE}\n```\n', None),
    ('fence-info', f'# P\n```markdown\n{_EN}\n```\n', None),
    ('fence-tilde', f'# P\n~~~\n{_LINE}\n~~~\n', None),
    ('fence-indented', f'# P\n  ```\n{_LINE}\n  ```\n', None),
    ('fence-crlf', f'# P\r\n```\r\n{_LINE}\r\n```\r\n', None),
    ('fence-unclosed', f'# P\n```\n{_LINE}\n', None),
    ('fence-longer-keeps-inner', f'# P\n````\n```\n{_LINE}\n```\n````\n', None),
    ('fence-other-mark-does-not-close', f'# P\n```\n~~~\n{_LINE}\n```\n', None),
    ('fence-info-does-not-close', f'# P\n```\n```text\n{_LINE}\n```\n', None),
    ('after-closed-fence', f'# P\n```\nx\n```\n{_LINE}\n', _OK),
    ('after-closed-fence-crlf', f'# P\r\n~~~\r\nx\r\n~~~\r\n{_LINE}\r\n', _OK),
    ('after-longer-closing', f'# P\n```\nx\n`````\n{_LINE}\n', _OK),
    ('example-then-real', f'# P\n```\n{_LINE.replace("11-25", "01-01")}\n```\n{_LINE}\n', _OK),
    ('inline-triple-backtick-is-no-fence', f'# P\n``` kod ``` w tekscie\n{_LINE}\n', _OK),
    ('empty', '', None),
]
# Przypadki bez rozstrzygnięcia w specu: liczy się tylko zgodność kopii.
CLOSURE_PARITY_ONLY = [
    f'Zamknięcie:final 2026-11-25 {_SHA}\n', f'Zamknięcie: final  2026-11-25\t{_SHA}\n', f'Zamknięcie: final 2026-11-25 {_SHA.upper()}\n',
    f'Zamknięcie: final 2026-13-45 {_SHA}\n', f'Zamknięcie: zle\n{_LINE}\n', f'{_LINE}\nClosure: final 2027-01-01 {"f" * 40}\n',
    f'{_EN}\n{_LINE.replace("11-25", "11-26")}\n', f'##S\n{_LINE}\n', f'### S\n{_LINE}\n', f'Zamknięcie: `final` 2026-11-25 {_SHA}\n',
    f'\ufeff{_LINE}\n', f'{_LINE}\r', _padded(4000) + '## S\n', 'x' * 3999 + '\n## S\n' + _LINE, _LINE + 'a' * 3 + '\n',
    'x' * (4000 - len(_LINE) - 1) + '\n' + _LINE + 'abc\n',
]


@needs_hook
class ClosureLineCopy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('check_closure_grammar', HOOK_SOURCE)
        cls.hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.hook)

    def test_the_source_of_truth_follows_the_spec(self) -> None:
        for name, text, expected in CLOSURE_SPEC_CORPUS:
            with self.subTest(name):
                self.assertEqual(protocol_tokens.closure_line(text), expected)

    def test_the_hook_copy_matches_the_source_of_truth(self) -> None:
        for text in [case[1] for case in CLOSURE_SPEC_CORPUS] + CLOSURE_PARITY_ONLY:
            with self.subTest(text=text[-120:]):
                self.assertEqual(self.hook.find_closure(text), protocol_tokens.closure_line(text))

    def test_the_token_names_are_the_ones_of_the_contract_and_the_hook_reads_each(self) -> None:
        self.assertEqual(tuple(protocol_tokens.names('closure')), ('Zamknięcie:', 'Closure:'))
        for token in protocol_tokens.names('closure'):
            with self.subTest(token):
                self.assertEqual(self.hook.find_closure(f'# P\n{token} final 2026-11-25 {_SHA}\n'), _OK)

    def test_a_bad_line_is_told_apart_from_a_missing_one(self) -> None:
        self.assertTrue(protocol_tokens.closure_line_present('# P\nZamknięcie: kiedys\n'))
        self.assertTrue(protocol_tokens.closure_line_present('# P\nClosure: final 2026-11-25 abc\n'))
        self.assertFalse(protocol_tokens.closure_line_present('# P\n\n## S\nZamknięcie: kiedys\n'))
        self.assertFalse(protocol_tokens.closure_line_present('# P\n'))
        self.assertFalse(protocol_tokens.closure_line_present('# P\n```\nZamknięcie: kiedys\n```\n'))
        self.assertTrue(protocol_tokens.closure_line_present('# P\n```\nx\n```\nZamknięcie: kiedys\n'))


@needs_hook
class HookBehaviour(TmpRoot):
    def setUp(self):
        super().setUp()
        self.hook = self.root / '.claude/hooks/check-closure.py'
        self.hook.parent.mkdir(parents=True)
        self.hook.write_bytes(HOOK_SOURCE.read_bytes())
        self.put('README.md', 'fixture\n')
        self.put('.gitignore', '.claude/\n.skillsync/\n')
        env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@e', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@e')
        for args in (['init', '-q'], ['add', 'README.md', '.gitignore'], ['commit', '-q', '-m', 'init']):
            subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True, env=env)

    def fire(self, session='s1'):
        payload = json.dumps({'cwd': str(self.root), 'stop_hook_active': False, 'session_id': f'{session}-{self.root.name}'})
        result = subprocess.run([sys.executable, str(self.hook)], input=payload, capture_output=True, text=True, timeout=20, cwd=self.root)
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        return result

    def test_hook_blocks_done_plan_with_run_line(self) -> None:
        for name, token in TOKENS.items():
            with self.subTest(name):
                run = f'abc-{name.lower()}'
                self.put(f'docs/plans/2026-11-22-{name.lower()}.md', plan(token, run))
                self.run_record(run, final=False)
                result = self.fire(session=name)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn(run, result.stderr)
                self.assertIn('--final', result.stderr)

    def test_hook_final_record_allows_for_run_line(self) -> None:
        self.put('docs/plans/2026-11-22-en.md', plan('Run', 'abc-en'))
        self.run_record('abc-en', final=True)
        self.assertEqual(self.fire().returncode, 0)

    def test_hook_message_is_the_same_for_both_tokens(self) -> None:
        self.put('docs/plans/2026-11-22-x.md', plan('Przebieg', 'abc'))
        self.run_record('abc', final=False)
        pl = self.fire(session='a').stderr
        self.put('docs/plans/2026-11-22-x.md', plan('Run', 'abc'))
        en = self.fire(session='b').stderr
        self.assertEqual(pl, en)

    def test_hook_ignores_run_in_body(self) -> None:
        # Negatyw: `Run:` po `## ` nie wiąże planu, więc hook milczy (dowód zdolności do porażki: hook czytający `Run:` w całym tekście
        # zablokowałby ten plan; pozytywny odpowiednik z nagłówkiem blokuje w test_hook_blocks_done_plan_with_run_line).
        self.put('docs/plans/2026-11-22-body.md', plan(None, None) + '\nRun: body-run\n')
        self.run_record('body-run', final=False)
        self.assertEqual(self.fire().returncode, 0)

    def test_hook_polish_przebieg_unchanged(self) -> None:
        self.put('docs/plans/2026-11-22-pl.md', plan('Przebieg', 'abc'))
        self.run_record('abc', final=False)
        result = self.fire()
        self.assertEqual(result.returncode, 2)
        self.assertIn('abc', result.stderr)


if __name__ == '__main__':
    unittest.main()
