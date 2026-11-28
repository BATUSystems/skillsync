"""CORE-CLOSURE-TRACKED, krok B-4: jedna ocena zamknięcia planu dla haka Stop, `doctor` i `status` (spec Z1-Z2, K1, K5, K7, K10).

Kolejność werdyktu planu `done`: `none` (brak poprawnej linii przebiegu) -> `tracked` (linia `Zamknięcie:`) -> `final` (lokalny rekord) ->
`bad-line` (linia o złym kształcie) -> `foreign` (brak lokalnego śladu) / `open` (ślad bez `final`). Hak ma samodzielną kopię; jedyna zamierzona
różnica: magazyn przez dowiązanie albo nieczytelny to w generatorze `open`, a w haku `unknown` (hak nie blokuje tury).
"""
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import needs_mode_enforcement, needs_os_symlinks  # noqa: E402
from test_skillsync_workflow import WorkflowFixture  # noqa: E402
from tmp_paths import child  # noqa: E402

from skillsync_workflow import plan_closure, quality, test_gate  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
_LIBRARY = os.environ.get('SKILLSYNC_TEST_LIBRARY')
HOOK_SOURCE = Path(_LIBRARY) / 'hooks' / 'check-closure.py' if _LIBRARY else REPO / 'library' / 'hooks' / 'check-closure.py'
needs_hook = unittest.skipUnless(HOOK_SOURCE.is_file(), 'set SKILLSYNC_TEST_LIBRARY to a library with hooks/check-closure.py')

SHA = '0123456789abcdef0123456789abcdef01234567'
VALID = f'Zamknięcie: final 2026-11-25 {SHA}\n'
BAD = 'Zamknięcie: final 2026-11-25 0123456\n'
OPEN_STEPS = '\n## Progress\n\n- [x] A1 a\n- [ ] A2 b\n'
ALL_STEPS = '\n## Progress\n\n- [x] A1 a\n- [x] A2 b\n'


def plan(status='done', run='abc', line='', tail=''):
    head = f'# Plan\n\nStatus: {status}\n' + (f'Przebieg: {run}\n' if run is not None else '')
    return head + line + tail


def put_trace(root, run, kind):
    """Lokalny ślad przebiegu `run` w `.skillsync/runs`."""
    runs = Path(root) / '.skillsync' / 'runs'
    runs.mkdir(parents=True, exist_ok=True)

    def record(name, final):
        records = [{'step': 'a', 'status': 'PASS', 'next': 'n', **({'final': True} if final else {})}]
        child(runs, f'{name}.json').write_text(json.dumps({'schema_version': 1, 'run_id': name, 'checkpoints': records}), encoding='utf-8')

    if kind == 'directory':
        child(runs, run).mkdir()
    elif kind == 'approvals':
        child(runs, run).mkdir()
        (child(runs, run) / 'approvals.jsonl').write_text('', encoding='utf-8')
    elif kind == 'run-file':
        record(run, False)
    elif kind == 'attempt-file':
        record(f'{run}.2', False)
    elif kind == 'final':
        record(run, True)
    elif kind == 'final-attempt':
        record(run, False)
        record(f'{run}.2', True)
    elif kind == 'other-run':
        record(f'{run}x', True)
    elif kind == 'corrupt':
        child(runs, f'{run}.json').write_text('{ not json', encoding='utf-8')
    elif kind == 'corrupt-and-final':
        child(runs, f'{run}.json').write_text('{ not json', encoding='utf-8')
        record(f'{run}.2', True)
    else:
        assert kind == 'none', kind


# ---------------------------------------------------------------------------------------------------------------
# K10: pole `closure` w `skillsync status`
# ---------------------------------------------------------------------------------------------------------------
class StatusClosureField(WorkflowFixture):
    # (linia zamknięcia, ślad lokalny) -> werdykt według kolejności z kontraktu
    MATRIX = [
        ('valid', 'none', 'tracked'), ('valid', 'run-file', 'tracked'), ('valid', 'final', 'tracked'), ('valid', 'corrupt', 'tracked'),
        ('bad', 'final', 'final'), ('bad', 'final-attempt', 'final'), ('absent', 'final', 'final'), ('absent', 'final-attempt', 'final'),
        ('bad', 'none', 'bad-line'), ('bad', 'run-file', 'bad-line'), ('bad', 'directory', 'bad-line'),
        ('absent', 'none', 'foreign'), ('absent', 'other-run', 'foreign'),
        ('absent', 'directory', 'open'), ('absent', 'approvals', 'open'), ('absent', 'run-file', 'open'), ('absent', 'attempt-file', 'open'),
        ('absent', 'corrupt', 'open'),
    ]
    LINES = {'valid': VALID, 'bad': BAD, 'absent': ''}

    def status(self):
        return self.report('status')

    def rows(self, report=None):
        return {row['path']: row for row in (report or self.status())['plans']}

    def test_every_state_of_a_done_plan_and_the_order_between_them(self):
        names = {}
        for index, (line, trace, expected) in enumerate(self.MATRIX):
            run = f'run{index:02d}'
            names[f'docs/plans/2026-11-26-{run}.md'] = (line, trace, expected)
            self.write(f'docs/plans/2026-11-26-{run}.md', plan(run=run, line=self.LINES[line]))
            put_trace(self.root, run, trace)
        report = self.status()
        self.assertEqual((report['schema'], report['status']), (1, 'PASS'))
        rows = self.rows(report)
        for path, (line, trace, expected) in names.items():
            with self.subTest(line=line, trace=trace):
                self.assertEqual(rows[path]['closure'], {'state': expected, 'sha': SHA if expected == 'tracked' else None})
                self.assertEqual(rows[path]['needsHuman'], [])
        self.assertEqual({row['closure']['state'] for row in rows.values()}, {'tracked', 'final', 'bad-line', 'foreign', 'open'})

    def test_a_plan_without_a_usable_run_line_is_none_even_with_a_closure_line(self):
        cases = {'no-run': plan(run=None), 'no-run-with-line': plan(run=None, line=VALID), 'bad-id': plan(run='../x', line=VALID),
                 'run-en-in-body': plan(run=None, line=VALID, tail='\n## S\n\nRun: abc\n')}
        for name, text in cases.items():
            self.write(f'docs/plans/2026-11-26-{name}.md', text)
        put_trace(self.root, 'abc', 'final')
        rows = self.rows()
        for name in cases:
            with self.subTest(name):
                self.assertEqual(rows[f'docs/plans/2026-11-26-{name}.md']['closure'], {'state': 'none', 'sha': None})

    def test_only_done_plans_have_the_field(self):
        statuses = ('draft', 'approved', 'in_progress', 'archived', 'done')
        for status in statuses:
            self.write(f'docs/plans/2026-11-26-{status}.md', plan(status=status, line=VALID, tail=OPEN_STEPS))
        rows = self.rows()
        self.assertEqual({status: 'closure' in rows[f'docs/plans/2026-11-26-{status}.md'] for status in statuses},
                         {status: status == 'done' for status in statuses})

    def test_the_field_carries_no_host_path_or_run_id(self):
        """Odczyt bez logowania (U-4): tylko werdykt i sha z linii, także gdy w magazynie są ścieżki."""
        self.write('docs/plans/2026-11-26-a.md', plan(run='secret-run', line=VALID))
        self.write('docs/plans/2026-11-26-b.md', plan(run='other-run-id'))
        put_trace(self.root, 'other-run-id', 'run-file')
        for row in self.rows().values():
            field = row['closure']
            self.assertEqual(sorted(field), ['sha', 'state'])
            dumped = json.dumps(field)
            for forbidden in (str(self.root), self.root.name, socket.gethostname(), 'secret-run', 'other-run-id', '.skillsync', '/'):
                self.assertNotIn(forbidden, dumped)

    def test_windows_endings_do_not_count_against_the_header_limit(self):
        """D1: generator liczy limit nagłówka po ujednoliceniu końców linii, jak hak czytający plan w trybie tekstowym."""
        padding = 'a\n' * 1900   # po ujednoliceniu poniżej 4000 znaków, w bajtach CRLF powyżej
        text = f'# P\n\nStatus: done\nPrzebieg: abc\n{padding}{VALID}'
        self.assertLess(len(text), 4000)
        self.assertGreater(len(text.replace('\n', '\r\n')), 4200)
        for label, ending in (('lf', '\n'), ('crlf', '\r\n')):
            self.write(f'docs/plans/2026-11-26-{label}.md', text.replace('\n', ending))
        rows = self.rows()
        for label in ('lf', 'crlf'):
            with self.subTest(label):
                self.assertEqual(rows[f'docs/plans/2026-11-26-{label}.md']['closure'], {'state': 'tracked', 'sha': SHA})
        for ending in ('\r\n', '\r'):
            with self.subTest(direct=repr(ending)):
                self.assertEqual(plan_closure.closure_verdict(self.root, text.replace('\n', ending)), {'state': 'tracked', 'sha': SHA})

    def test_a_long_windows_plan_in_progress_gets_the_status_finding_too(self):
        """Ta sama linia w planie `in_progress`: skoro `doctor` każe ustawić `Status: done`, `status` ma o tym przypomnieć."""
        text = '# P\n\nStatus: in_progress\nPrzebieg: abc\n' + 'a\n' * 1900 + VALID + ALL_STEPS
        self.write('docs/plans/2026-11-26-a.md', text.replace('\n', '\r\n'))
        found = [reason for _path, reason in quality.plan_closure_findings(self.root)]
        self.assertTrue(len(found) == 1 and 'set `Status: done`' in found[0], found)
        self.assertEqual(len(self.reminders(self.status())), 1)

    def test_a_line_in_the_body_a_quote_or_a_code_block_does_not_make_the_plan_tracked(self):
        cases = {'body': plan(tail=f'\n## Notatki\n\n{VALID}'), 'quote': plan(line=f'> {VALID}'),
                 'fence-header': plan(line=f'\n```\n{VALID}```\n'), 'tilde-header': plan(line=f'\n~~~\n{VALID}~~~\n'),
                 'fence-bad': plan(line=f'\n```\n{BAD}```\n')}
        for name, text in cases.items():
            self.write(f'docs/plans/2026-11-26-{name}.md', text)
        rows = self.rows()
        for name in cases:
            with self.subTest(name):
                self.assertEqual(rows[f'docs/plans/2026-11-26-{name}.md']['closure'], {'state': 'foreign', 'sha': None})

    def test_english_tokens_and_windows_endings(self):
        text = f'# P\n\nStatus: done\nRun: abc\nClosure: final 2026-11-25 {SHA}\n'
        self.write('docs/plans/2026-11-26-lf.md', text)
        self.write('docs/plans/2026-11-26-crlf.md', text.replace('\n', '\r\n'))
        for row in self.rows().values():
            self.assertEqual(row['closure'], {'state': 'tracked', 'sha': SHA}, row['path'])

    @needs_os_symlinks
    def test_a_symlinked_store_is_open_and_is_not_read(self):
        other = self.base / 'elsewhere'
        put_trace(other, 'abc', 'final')
        (self.root / '.skillsync').symlink_to(other / '.skillsync', target_is_directory=True)
        self.write('docs/plans/2026-11-26-a.md', plan())
        self.assertEqual(self.rows()['docs/plans/2026-11-26-a.md']['closure'], {'state': 'open', 'sha': None})

    @needs_mode_enforcement
    def test_an_unreadable_store_does_not_break_status(self):
        self.write('docs/plans/2026-11-26-a.md', plan())
        runs = self.root / '.skillsync' / 'runs'
        runs.mkdir(parents=True)
        os.chmod(runs, 0)
        self.addCleanup(os.chmod, runs, 0o700)
        report = self.status()
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(self.rows(report)['docs/plans/2026-11-26-a.md']['closure'], {'state': 'open', 'sha': None})

    def reminders(self, report):
        return [item for item in report['findings'] if 'Status: done' in item]

    def test_a_closed_plan_still_in_progress_with_every_step_checked_gets_a_status_finding(self):
        self.write('docs/plans/2026-11-26-all.md', plan('in_progress', line=VALID, tail=ALL_STEPS))
        report = self.status()
        found = self.reminders(report)
        self.assertEqual(len(found), 1, report['findings'])
        self.assertIn('2026-11-26-all.md', found[0])
        self.assertEqual(self.rows(report)['docs/plans/2026-11-26-all.md']['needsHuman'], ['closure'], 'needsHuman bez zmian')

    def test_no_status_finding_without_a_valid_line_or_with_open_steps(self):
        for name, text in (('open-steps', plan('in_progress', line=VALID, tail=OPEN_STEPS)),
                           ('no-line', plan('in_progress', tail=ALL_STEPS)), ('bad-line', plan('in_progress', line=BAD, tail=ALL_STEPS)),
                           ('done', plan(line=VALID, tail=ALL_STEPS))):
            self.write(f'docs/plans/2026-11-26-{name}.md', text)
        self.assertEqual(self.reminders(self.status()), [])

    def test_a_fresh_clone_with_the_line_is_tracked_and_doctor_is_silent(self):
        """K1, regresja z pkt 5 zgłoszenia Agady: świeży klon bez `.skillsync/runs`."""
        self.write('.gitignore', '.skillsync/\n')
        self.write('docs/plans/2026-11-26-a.md', plan(line=VALID))
        self.init_git()
        clone = self.base / 'clone'
        subprocess.run(['git', 'clone', '-q', str(self.root), str(clone)], check=True, capture_output=True, timeout=30)
        self.assertFalse((clone / '.skillsync').exists())
        self.root = clone
        self.assertEqual(self.rows()['docs/plans/2026-11-26-a.md']['closure'], {'state': 'tracked', 'sha': SHA})
        report = self.report('doctor')
        self.assertEqual([f for f in report['findings'] if 'docs/plans' in f.get('path', '')], [])


# ---------------------------------------------------------------------------------------------------------------
# K5, K10: ostrzeżenia `doctor`
# ---------------------------------------------------------------------------------------------------------------
class DoctorClosureWarnings(WorkflowFixture):
    NAME = 'docs/plans/2026-11-26-a.md'

    def findings(self, text, trace='none'):
        shutil.rmtree(self.root / '.skillsync', ignore_errors=True)
        put_trace(self.root, 'abc', trace)
        self.write(self.NAME, text)
        return [reason for path, reason in quality.plan_closure_findings(self.root) if path == self.NAME]

    def kinds(self, reasons):
        """Rodzaj ostrzeżenia po jego treści kontraktowej; nieznana treść wychodzi dosłownie."""
        def kind(reason):
            if reason.startswith('Invalid `Zamknięcie:` line'):
                return 'bad-line'
            if 'waiting for the pull request' in reason:
                return 'waiting-pr'
            if reason.startswith('Stale `Zamknięcie:` line'):
                return 'stale'
            if 'Status: done' in reason and '`Zamknięcie:` line is present' in reason:
                return 'set-done'
            if 'has no `final` record' in reason and reason.startswith('Plan is done'):
                return 'open'
            if reason.startswith('All Progress steps are checked but the plan is still in_progress'):
                return 'unclosed'
            return reason
        return sorted(kind(reason) for reason in reasons)

    def test_done_plans(self):
        table = [
            (VALID, 'none', []), (VALID, 'run-file', []), (VALID, 'directory', []),                  # K4: linia wygrywa
            ('', 'none', []), ('', 'other-run', []), ('', 'final', []),                              # foreign i final milczą
            ('', 'run-file', ['open']), ('', 'directory', ['open']),                                 # K3
            (BAD, 'none', ['bad-line']), (BAD, 'final', ['bad-line']),                               # K5
            (BAD, 'run-file', ['bad-line', 'open']),
        ]
        for line, trace, expected in table:
            with self.subTest(line=line, trace=trace):
                self.assertEqual(self.kinds(self.findings(plan(line=line), trace)), expected)

    def test_in_progress_plans(self):
        ticket = OPEN_STEPS.replace('## Progress\n', '## Progress\n\nTicket: ABC-1 (link: https://example/ABC-1)\n')
        waiting = ticket.replace('- [ ] A2 b', '- [x] A2 b')
        table = [
            ('stale-open-steps', VALID, OPEN_STEPS, ['stale']), ('set-done', VALID, ALL_STEPS, ['set-done']),
            ('ticket-waiting', VALID, waiting, ['waiting-pr']), ('ticket-open-steps', VALID, ticket, ['stale']), ('no-line-open', '', OPEN_STEPS, []),
            ('no-line-all', '', ALL_STEPS, ['unclosed']), ('bad-open', BAD, OPEN_STEPS, ['bad-line']),
            ('bad-all', BAD, ALL_STEPS, ['bad-line', 'unclosed']),
        ]
        for label, line, tail, expected in table:
            with self.subTest(label):
                self.assertEqual(self.kinds(self.findings(plan('in_progress', line=line, tail=tail))), expected)

    def test_the_stale_and_set_done_messages_say_what_to_do(self):
        stale = self.findings(plan('in_progress', line=VALID, tail=OPEN_STEPS))[0]
        for fragment in ('remove it', 'skillsync checkpoint --final'):
            self.assertIn(fragment, stale)
        done = self.findings(plan('in_progress', line=VALID, tail=ALL_STEPS))[0]
        self.assertIn('set `Status: done`', done)

    def test_a_closed_plan_that_waits_for_its_pull_request_says_so(self):
        """D4: linia zamknięcia i wszystkie `[x]`, `Ticket:` bez `PR:`: ani „usuń linię”, ani samo „ustaw done”; status bez findingu."""
        waiting = ALL_STEPS.replace('## Progress\n', '## Progress\n\nTicket: ABC-1 (link: https://example/ABC-1)\n')
        reason = self.findings(plan('in_progress', line=VALID, tail=waiting))[0]
        for fragment in ('`Ticket:` without `PR:`', 'add the `PR:` line', 'set `Status: done`'):
            self.assertIn(fragment, reason)
        for forbidden in ('Stale', 'remove'):
            self.assertNotIn(forbidden, reason)
        self.assertEqual([item for item in self.report('status')['findings'] if 'Status: done' in item], [])
        with self.subTest(control='with the PR line the plan only needs the status'):
            merged = waiting.replace('Ticket:', 'PR: #5 (link: https://example/pr/5)\nTicket:')
            self.assertEqual(self.kinds(self.findings(plan('in_progress', line=VALID, tail=merged))), ['set-done'])

    def test_the_english_bad_line_warns(self):
        self.assertEqual(self.kinds(self.findings(plan(line='Closure: yesterday\n'))), ['bad-line'])

    def test_lines_in_the_body_or_in_a_code_block_are_not_judged(self):
        for label, text in (('body', plan(tail=f'\n## S\n\n{BAD}')), ('fence', plan(line=f'\n```\n{BAD}```\n')),
                            ('quote', plan(line=f'> {BAD}'))):
            with self.subTest(label):
                self.assertEqual(self.findings(text), [])
        with self.subTest('an example does not close an open run'):
            self.assertEqual(self.kinds(self.findings(plan(line=f'\n```\n{VALID}```\n'), 'directory')), ['open'])

    def test_the_cli_reports_warnings_and_stays_pass(self):
        self.write(self.NAME, plan(line=BAD))
        report = self.report('doctor')
        self.assertEqual(report['status'], 'PASS', report)
        found = [item for item in report['findings'] if item.get('path') == self.NAME]
        self.assertEqual([(item['level'], item['reason'].split(' (')[0]) for item in found], [('warning', 'Invalid `Zamknięcie:` line')])


# ---------------------------------------------------------------------------------------------------------------
# Linia przebiegu w bloku kodu to przykład autora: `status`, hak i `quality` czytają własną linię planu
# ---------------------------------------------------------------------------------------------------------------
@needs_hook
class RunLineInACodeBlock(WorkflowFixture):
    NAME = 'docs/plans/2026-11-26-a.md'

    def setUp(self):
        super().setUp()
        self.write('.gitignore', '.skillsync/\n')
        self.init_git()
        self.sessions = 0

    def judge(self, text):
        """(werdykt statusu, ostrzeżenia quality, wynik haka) dla jednego planu."""
        self.write(self.NAME, text)
        row = {item['path']: item for item in self.report('status')['plans']}[self.NAME]
        warnings = [reason for path, reason in quality.plan_closure_findings(self.root) if path == self.NAME]
        self.sessions += 1
        done = subprocess.run([sys.executable, str(HOOK_SOURCE)],
                              input=json.dumps({'cwd': str(self.root), 'session_id': f'{self.root}-{self.sessions}'}),
                              capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20, cwd=self.root)
        self.assertNotIn('Traceback', done.stderr)
        return row['closure']['state'], warnings, done

    def assert_open(self, text, run, other):
        state, warnings, hook = self.judge(text)
        self.assertEqual(state, 'open')
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn(f'run `{run}` has no `final` record', warnings[0])
        self.assertEqual(hook.returncode, 2, hook.stderr)
        self.assertIn(f'przebieg {run}', hook.stderr)
        self.assertNotIn(other, hook.stderr)

    def assert_quiet(self, text, expected):
        state, warnings, hook = self.judge(text)
        self.assertEqual((state, warnings, hook.returncode, hook.stderr), (expected, [], 0, ''))

    def variants(self, line, fence):
        block = f'\n{fence}\n{line}\n{fence}\n' if fence else f'\n{line}\n'
        text = f'# Plan\n\nStatus: done\n{block}Przebieg: abc\n'
        return (('lf', text), ('crlf', text.replace('\n', '\r\n')))

    def test_the_own_run_line_wins_over_an_example_in_a_code_block(self):
        put_trace(self.root, 'abc', 'final')
        put_trace(self.root, 'example', 'run-file')
        for token in ('Przebieg', 'Run'):
            for fence in ('```', '~~~'):
                for ending, text in self.variants(f'{token}: example', fence):
                    with self.subTest(token=token, fence=fence, ending=ending):
                        self.assert_quiet(text, 'final')
            with self.subTest(token=token, control='the same line outside a block binds the plan'):
                self.assert_open(self.variants(f'{token}: example', None)[0][1], 'example', 'abc')

    def test_an_example_of_a_closed_run_does_not_close_the_plan(self):
        put_trace(self.root, 'example', 'final')
        put_trace(self.root, 'abc', 'run-file')
        for ending, text in self.variants('Przebieg: example', '```'):
            with self.subTest(ending):
                self.assert_open(text, 'abc', 'example')
        with self.subTest(control='outside a block the example decides'):
            self.assert_quiet(self.variants('Przebieg: example', None)[0][1], 'final')

    def test_a_plan_with_the_run_line_only_in_a_code_block_has_no_run(self):
        put_trace(self.root, 'abc', 'run-file')
        for label, where in (('header', '\n```\nPrzebieg: abc\n```\n'), ('body', '\n## Notatki\n\n```\nPrzebieg: abc\n```\n'),
                             ('unclosed', '\n```\nPrzebieg: abc\n')):
            with self.subTest(label):
                self.assert_quiet('# Plan\n\nStatus: done\n' + where, 'none')
        with self.subTest(control='the polish line in the body outside a block is read'):
            self.assert_open('# Plan\n\nStatus: done\n\n## Notatki\n\nPrzebieg: abc\n', 'abc', 'example')

    def test_the_test_gate_reads_the_same_line(self):
        text = '# P\n```\nPrzebieg: example\n```\nPrzebieg: abc\n'
        self.assertEqual(test_gate.run_of_plan(text), 'abc')
        self.assertEqual(test_gate.run_of_plan(text.replace('```\n', '')), 'example')
        self.assertIsNone(test_gate.run_of_plan('# P\n```\nPrzebieg: abc\n```\n'))


# ---------------------------------------------------------------------------------------------------------------
# Nielistowalny magazyn: bez oceny tylko planów zależnych od magazynu
# ---------------------------------------------------------------------------------------------------------------
@needs_hook
class UnlistableStoreHook(WorkflowFixture):
    def fire(self, session):
        done = subprocess.run([sys.executable, str(HOOK_SOURCE)],
                              input=json.dumps({'cwd': str(self.root), 'session_id': f'{self.root}-{session}'}),
                              capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20, cwd=self.root)
        self.assertNotIn('Traceback', done.stderr)
        return done

    @needs_mode_enforcement
    def test_other_plans_still_block_whatever_their_order(self):
        self.write('.gitignore', '.skillsync/\n')
        self.init_git()
        put_trace(self.root, 'abc', 'run-file')
        runs = self.root / '.skillsync' / 'runs'
        self.addCleanup(os.chmod, runs, 0o700)
        for order, done_name, steps_name in (('done-first', 'a-done', 'b-steps'), ('steps-first', 'b-done', 'a-steps')):
            shutil.rmtree(self.root / 'docs', ignore_errors=True)
            self.write(f'docs/plans/{done_name}.md', plan())
            self.write(f'docs/plans/{steps_name}.md', plan('in_progress', run='xyz', tail=ALL_STEPS))
            for mode in (0o000, 0o300):
                with self.subTest(order=order, mode=oct(mode)):
                    os.chmod(runs, 0o700)
                    control = self.fire(f'control-{order}-{mode}')
                    self.assertEqual(control.returncode, 2, control.stderr)
                    self.assertIn(f'{done_name}.md', control.stderr)
                    os.chmod(runs, mode)
                    done = self.fire(f'locked-{order}-{mode}')
                    self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                    self.assertIn('in_progress ze wszystkimi krokami', done.stderr)
                    self.assertIn(f'{steps_name}.md', done.stderr)
                    self.assertNotIn(f'{done_name}.md', done.stderr, 'planu bez oceny hak nie zgłasza')


# ---------------------------------------------------------------------------------------------------------------
# K7: kopia haka i generator dają ten sam werdykt
# ---------------------------------------------------------------------------------------------------------------
@needs_hook
class HookGeneratorParity(unittest.TestCase):
    TRACES = ('none', 'directory', 'approvals', 'run-file', 'attempt-file', 'final', 'final-attempt', 'other-run', 'corrupt',
              'corrupt-and-final')
    TEXTS = {
        'plain': plan(), 'valid': plan(line=VALID), 'bad': plan(line=BAD),
        'en': f'# P\nStatus: done\nRun: abc\nClosure: final 2026-11-25 {SHA}\n',
        'crlf-valid': plan(line=VALID).replace('\n', '\r\n'), 'crlf-bad': plan(line=BAD).replace('\n', '\r\n'),
        'body': plan(tail=f'\n## S\n\n{VALID}'), 'fence-valid': plan(line=f'```\n{VALID}```\n'), 'fence-bad': plan(line=f'~~~\n{BAD}~~~\n'),
        'fence-then-valid': plan(line=f'```\n{BAD}```\n{VALID}'), 'result': plan(tail='\n## Wynik\n\nGotowe.\n'),
    }

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('check_closure_parity_b4', HOOK_SOURCE)
        cls.hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.hook)

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='skillsync-parity-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.count = 0

    def project(self):
        self.count += 1
        root = child(self.base, f'p{self.count}')
        root.mkdir()
        return root

    def verdicts(self, root, text):
        return self.hook.closure_state(str(root), text, 'abc'), plan_closure.closure_verdict(root, text)['state']

    def test_the_same_verdict_for_every_line_and_readable_store(self):
        seen = set()
        for trace in self.TRACES:
            root = self.project()
            put_trace(root, 'abc', trace)
            for label, text in self.TEXTS.items():
                with self.subTest(trace=trace, text=label):
                    hook, generator = self.verdicts(root, text)
                    self.assertEqual('open' if hook == 'unknown' else hook, generator)
                    if trace != 'corrupt':
                        self.assertNotEqual(hook, 'unknown', 'czytelny magazyn nie może być „unknown”')
                    seen.add(generator)
        self.assertEqual(seen, {'tracked', 'final', 'bad-line', 'foreign', 'open'})

    def assert_unknown_versus_open(self, root):
        self.assertEqual(self.verdicts(root, plan()), ('unknown', 'open'))
        self.assertEqual(self.verdicts(root, plan(line=VALID)), ('tracked', 'tracked'))
        self.assertEqual(self.verdicts(root, plan(line=BAD)), ('bad-line', 'bad-line'))

    @needs_os_symlinks
    def test_a_symlinked_store_directory_or_record(self):
        other = self.base / 'elsewhere'
        put_trace(other, 'abc', 'final')
        for label in ('skillsync', 'runs', 'record'):
            with self.subTest(label):
                root = self.project()
                if label == 'skillsync':
                    (root / '.skillsync').symlink_to(other / '.skillsync', target_is_directory=True)
                elif label == 'runs':
                    (root / '.skillsync').mkdir()
                    (root / '.skillsync/runs').symlink_to(other / '.skillsync/runs', target_is_directory=True)
                else:
                    (root / '.skillsync/runs').mkdir(parents=True)
                    (root / '.skillsync/runs/abc.json').symlink_to(other / '.skillsync/runs/abc.json')
                self.assert_unknown_versus_open(root)

    @needs_mode_enforcement
    def test_an_unlistable_store_is_open_for_the_generator_and_never_blocks_in_the_hook(self):
        root = self.project()
        runs = root / '.skillsync' / 'runs'
        runs.mkdir(parents=True)
        os.chmod(runs, 0)
        self.addCleanup(os.chmod, runs, 0o700)
        self.assert_unknown_versus_open(root)
        (root / 'docs/plans').mkdir(parents=True)
        (root / 'docs/plans/2026-11-26-a.md').write_text(plan(), encoding='utf-8')
        subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True, timeout=30)
        done = subprocess.run([sys.executable, str(HOOK_SOURCE)], input=json.dumps({'cwd': str(root), 'session_id': str(root)}),
                              capture_output=True, text=True, timeout=20, cwd=root)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn('Traceback', done.stderr)

    RESULT_CORPUS = [
        ('# X\n\n## Wynik\n\nGotowe.\n', True), ('# X\n\n## Result\n\nDone.\n', True), ('# X\n\n## Wynik (etap 1)\n\ncos\n', True),
        ('# X\r\n\r\n## Wynik\r\n\r\nGotowe.\r\n', True), ('# X\n\n## Wynik\n\n### Testy\n\nPASS.\n', True),
        ('# X\n\n## Wynik\n\n(po wykonaniu)\n', False), ('# X\n\n## Wynik\n\npo wykonaniu.\n', False),
        ('# X\n\n## Result\n\n(after the run)\n', False), ('# X\n\n## Result\n\nafter delivery\n', False),
        ('# X\n\n## Wynik\n\nTBD\n', False), ('# X\n\n## Wynik\n\n(todo).\n', False), ('# X\r\n\r\n## Wynik\r\n\r\n(po wykonaniu)\r\n', False),
        ('# X\n\n## Wynik\n\n## Inna\n\ntresc\n', False), ('# X\n\n## Wynik\n\n### Testy\n\n### Ryzyka\n', False),
        ('# X\n\n### Wynik\n\nGotowe.\n', False), ('# X\n\n## Wyniki pomiarow\n\ncos\n', False), ('# X\n\n## Results\n\nx\n', False),
        ('# X\n\n## wynik\n\nGotowe.\n', False), ('# X\n\n## Wynik\n   \n', False), ('', False),
    ]

    def test_the_result_section_rule_is_the_same_in_both_copies(self):
        for text, expected in self.RESULT_CORPUS:
            with self.subTest(text=text):
                self.assertEqual((self.hook.plan_has_result(text), plan_closure.plan_has_result(text)), (expected, expected))


if __name__ == '__main__':
    unittest.main()
