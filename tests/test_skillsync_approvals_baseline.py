"""Baseline characterization of the consent receipt BEFORE `planState` (CORE-SPECSTORE F4): `status.read_approvals` rules,
the duplicate key of `record_approval`, `acceptance_pending` with several entries per (run, word) and the states of
`dashboard.consent_buttons`. Only gaps next to test_skillsync_approvals_record / _plan_acceptance / _dashboard_server;
asserts existing fields and behaviour, never the absence of a field that F4 adds."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import dashboard, status  # noqa: E402
import sys as _sys_tmp_paths  # noqa: E402
from pathlib import Path as _Path_tmp_paths  # noqa: E402
_sys_tmp_paths.path.insert(0, str(_Path_tmp_paths(__file__).resolve().parent))  # noqa: E402
from tmp_paths import child  # noqa: E402

GIT_ENV = {'GIT_AUTHOR_NAME': 'T', 'GIT_AUTHOR_EMAIL': 't' + '@' + 'example.test', 'GIT_COMMITTER_NAME': 'T',
           'GIT_COMMITTER_EMAIL': 't' + '@' + 'example.test', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull}
SHA_A, SHA_B = 'a' * 40, 'b' * 40
NOW = datetime(2026, 1, 12, 12, 0, tzinfo=timezone.utc)


class Tree(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_dir(self, run):
        directory = child(self.root / '.skillsync' / 'runs', run)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def write(self, run, *items, raw=None):
        lines = [item if isinstance(item, str) else json.dumps(item) for item in items]
        (self.run_dir(run) / 'approvals.jsonl').write_text('\n'.join(lines) + '\n' + (raw or ''), encoding='utf-8')


def entry(word='CP', sha=SHA_A, at='2026-01-12T10:00:00Z', **extra):
    return {'schema': 1, 'at': at, 'word': word, 'sha': sha, 'by': 'u', 'source': 'cli', **extra}


class ReadApprovalsRules(Tree):
    def test_the_latest_entry_by_at_wins_per_run_and_word_whatever_the_line_order(self):
        self.write('r1', entry(sha=SHA_B, at='2026-01-12T11:00:00Z'), entry(sha=SHA_A, at='2026-01-12T10:00:00Z'))
        (row,) = status.read_approvals(self.root)
        self.assertEqual((row['sha'], row['at']), (SHA_B, '2026-01-12T11:00:00Z'))

    def test_equal_at_goes_to_the_later_line(self):
        self.write('r1', entry(sha=SHA_A), entry(sha=SHA_B))
        self.assertEqual([row['sha'] for row in status.read_approvals(self.root)], [SHA_B])

    def test_one_row_per_run_and_word_sorted_by_at_then_run_then_word(self):
        self.write('r2', entry('READY', at='2026-01-12T09:00:00Z'), entry('CP', at='2026-01-12T12:00:00Z'))
        self.write('r1', entry('CP', at='2026-01-12T12:00:00Z'))
        rows = status.read_approvals(self.root)
        self.assertEqual([(row['run'], row['word']) for row in rows], [('r2', 'READY'), ('r1', 'CP'), ('r2', 'CP')])

    def test_a_row_carries_run_word_at_sha_source_and_only_a_string_file(self):
        self.write('r1', entry('GO plan', file='docs/specs/x.md', plan='docs/plans/p.md', target='main', shaAfter=SHA_B),
                   entry('CP', at='2026-01-12T10:00:00+02:00', file=5, source='s' * 60))
        rows = {row['word']: row for row in status.read_approvals(self.root)}
        self.assertEqual({key: rows['GO plan'][key] for key in ('run', 'word', 'sha', 'source', 'file')},
                         {'run': 'r1', 'word': 'GO plan', 'sha': SHA_A, 'source': 'cli', 'file': 'docs/specs/x.md'})
        self.assertNotIn('target', rows['GO plan'], 'entry extras other than `file` are not exposed')
        self.assertEqual(rows['CP']['at'], '2026-01-12T08:00:00Z', 'at is normalised to UTC with Z')
        self.assertNotIn('file', rows['CP'])
        self.assertEqual(len(rows['CP']['source']), 40)

    def test_junk_entries_are_skipped(self):
        at = '2026-01-12T10:00:00Z'
        self.write('r1', 'not json', '[1]', json.dumps({'word': 'RM', 'sha': SHA_A, 'at': at}),
                   json.dumps({'word': 'CP', 'sha': 'abc123', 'at': at}), json.dumps({'word': 'CP', 'sha': 'A' * 40, 'at': at}),
                   json.dumps({'word': 'CP', 'sha': SHA_A, 'at': 'yesterday'}), json.dumps({'word': 'CP', 'sha': SHA_A}),
                   json.dumps({'word': 'READY', 'sha': 'abcdef1', 'at': at}))
        self.assertEqual([(row['word'], row['sha']) for row in status.read_approvals(self.root)], [('READY', 'abcdef1')])

    def test_an_oversized_file_is_a_finding_and_a_symlinked_file_is_ignored(self):
        self.write('big', entry(), raw='x' * status.MAX_APPROVALS_BYTES)
        findings = []
        self.assertEqual(status.read_approvals(self.root, findings), [])
        self.assertEqual(len(findings), 1)
        self.assertIn('big', findings[0])
        if hasattr(os, 'symlink'):
            real = self.root / 'real.jsonl'
            real.write_text(json.dumps(entry()) + '\n', encoding='utf-8')
            try:
                os.symlink(real, self.run_dir('link') / 'approvals.jsonl')
            except OSError:
                self.skipTest('no symlinks')
            self.assertEqual(status.read_approvals(self.root), [])

    def test_no_runs_directory_means_no_rows(self):
        self.assertEqual(status.read_approvals(self.root), [])


class DuplicateKey(Tree):
    def record(self, word='CP', sha=SHA_A, **kwargs):
        return status.record_approval(self.root, word, 'r1', sha=sha, by='u', now=NOW, **kwargs)

    def setUp(self):
        super().setUp()
        self.run_dir('r1')

    def test_the_key_is_word_and_exact_sha_not_source_plan_or_mode(self):
        self.assertEqual(self.record(source='cli')['status'], 'PASS')
        for kwargs in ({'source': 'ide'}, {'source': 'dashboard', 'plan': 'docs/plans/p.md', 'mode': 'direct'}):
            with self.subTest(kwargs=kwargs):
                refused = self.record(**kwargs)
                self.assertEqual(refused['status'], 'BLOCKED')
                self.assertIn('already recorded', refused['reason'])

    def test_a_sha_prefix_is_a_different_key_than_the_full_sha(self):
        self.assertEqual(self.record(sha=SHA_A)['status'], 'PASS')
        self.assertEqual(self.record(sha=SHA_A[:12])['status'], 'PASS')
        self.assertEqual(self.record(sha=SHA_A[:12])['status'], 'BLOCKED')

    def test_junk_lines_do_not_hide_an_existing_entry_from_the_duplicate_check(self):
        path = self.root / '.skillsync/runs/r1/approvals.jsonl'
        path.write_text('junk\n[1]\n' + json.dumps(entry()) + '\n', encoding='utf-8')
        self.assertEqual(self.record()['status'], 'BLOCKED')
        self.assertEqual(self.record(sha=SHA_B)['status'], 'PASS')

    def test_the_entry_has_the_contract_fields_and_go_words_key_on_the_blob_sha(self):
        written = self.record()['entry']
        self.assertEqual({key: written[key] for key in ('schema', 'at', 'word', 'sha', 'by', 'source', 'run')},
                         {'schema': status.SCHEMA, 'at': '2026-01-12T12:00:00Z', 'word': 'CP', 'sha': SHA_A, 'by': 'u',
                          'source': 'cli', 'run': 'r1'})
        self.assertEqual(self.record('GO plan', sha=SHA_A)['status'], 'PASS', 'another word with the same sha is another key')


class AcceptanceWithSeveralEntries(Tree):
    PLAN = ('# Plan: demo\n\nStatus: done\nPrzebieg: ACC-1\n\n## Cel\nx\n\n## Progress\n'
            '- [x] F4.3 Odbiór przez człowieka (lista kontrolna, pokrycie)\n')

    def git(self, *args):
        result = subprocess.run(['git', *args], cwd=self.root, capture_output=True, text=True, env={**os.environ, **GIT_ENV})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def setUp(self):
        super().setUp()
        (self.root / 'docs/plans').mkdir(parents=True)
        (self.root / 'docs/plans/2026-11-15-demo.md').write_text(self.PLAN, encoding='utf-8')
        self.git('init', '-q', '-b', 'work')
        self.git('add', '.')
        self.git('commit', '-q', '-m', 'plan done')
        self.head = self.git('rev-parse', 'HEAD')
        self.run_dir('ACC-1')

    def needs(self, mode='direct'):
        (record,) = status.project_status(self.root, {'delivery': {'mode': mode, 'base': 'main'}})['plans']
        return record['needsHuman']

    def test_no_entry_waits_and_a_closing_entry_on_the_done_commit_closes(self):
        self.assertEqual(self.needs(), ['acceptance'])
        self.write('ACC-1', entry('CP', self.head))
        self.assertEqual(self.needs(), [])

    def test_an_entry_without_at_or_with_junk_sha_is_no_entry(self):
        self.write('ACC-1', json.dumps({'word': 'CP', 'sha': self.head}), json.dumps({'word': 'CP', 'sha': 'zz', 'at': '2026-01-12T10:00:00Z'}))
        self.assertEqual(self.needs(), ['acceptance'])

    def test_the_later_entry_of_the_same_word_replaces_the_earlier_one(self):
        # `read_approvals` keeps ONE entry per (run, word): a later CP on a sha outside the history reopens the wait.
        self.write('ACC-1', entry('CP', self.head, at='2026-01-12T10:00:00Z'), entry('CP', SHA_B, at='2026-01-12T11:00:00Z'))
        self.assertEqual(self.needs(), ['acceptance'])
        self.write('ACC-1', entry('CP', SHA_B, at='2026-01-12T10:00:00Z'), entry('CP', self.head, at='2026-01-12T11:00:00Z'))
        self.assertEqual(self.needs(), [])

    def test_the_other_word_does_not_replace_the_closing_one(self):
        self.write('ACC-1', entry('CP', self.head, at='2026-01-12T10:00:00Z'), entry('READY', SHA_B, at='2026-01-12T11:00:00Z'),
                   entry('GO implement', SHA_B, at='2026-01-12T12:00:00Z'))
        self.assertEqual(self.needs('direct'), [])
        self.assertEqual(self.needs('pr'), ['acceptance'], 'READY on a foreign sha does not close in pr')

    def test_an_open_plan_has_no_acceptance_signal_even_without_entries(self):
        path = self.root / 'docs/plans/2026-11-15-demo.md'
        path.write_text(self.PLAN.replace('Status: done', 'Status: in_progress'), encoding='utf-8')
        self.assertNotIn('acceptance', self.needs())

    def test_the_project_status_lists_the_rows_of_read_approvals(self):
        self.write('ACC-1', entry('CP', self.head))
        report = status.project_status(self.root, {'delivery': {'mode': 'direct', 'base': 'main'}})
        self.assertEqual([(row['run'], row['word'], row['sha']) for row in report['approvals']], [('ACC-1', 'CP', self.head)])


class ConsentButtonStates(unittest.TestCase):
    HEAD = 'c' * 40
    WORDS = [{'word': 'CP', 'active': True, 'tip': 'wypchnij', 'target': 'feature/x'},
             {'word': 'READY', 'active': False, 'tip': 'czeka na CP', 'target': None}]

    def buttons(self, spec=None, plan=None, waiting=(), words=None, log=(), head=None, run_ok=True):
        spec = {'exists': False} if spec is None else spec
        return dashboard.consent_buttons(spec, plan, list(waiting), self.WORDS if words is None else words, list(log),
                                         self.HEAD if head is None else head, run_ok)

    def states(self, **kwargs):
        return [(b['kind'], b['state']) for b in self.buttons(**kwargs)]

    def test_without_spec_and_plan_there_are_no_buttons(self):
        self.assertEqual(self.buttons(), [])

    def test_a_draft_spec_is_running_until_the_session_signals_go_plan(self):
        spec = {'exists': True, 'status': 'draft', 'path': 'docs/specs/s.md', 'fileSha': SHA_A, 'statusText': 'draft'}
        (button,) = self.buttons(spec=spec)
        self.assertEqual((button['kind'], button['word'], button['state']), ('spec', 'GO plan', 'running'))
        self.assertIn('czeka na koniec pętli spec', button['reason'])
        self.assertEqual(button['blocked'], button['reason'])
        (waiting,) = self.buttons(spec=spec, waiting=[{'word': 'GO plan!'}])
        self.assertEqual((waiting['state'], waiting['reason'], waiting['blocked'], waiting['sha'], waiting['path']),
                         ('waiting', None, None, SHA_A, 'docs/specs/s.md'))

    def test_an_approved_spec_is_after_with_the_given_entry_or_an_empty_given(self):
        spec = {'exists': True, 'status': 'approved', 'path': 'docs/specs/s.md', 'fileSha': SHA_A}
        (button,) = self.buttons(spec=spec)
        self.assertEqual((button['state'], button['given'], button['reason']), ('after', {'at': None}, 'zgoda „GO plan” już dana'))
        given = {'word': 'GO plan', 'at': '2026-01-12T10:00:00Z', 'sha': SHA_A}
        (button,) = self.buttons(spec=spec, log=[given])
        self.assertEqual((button['state'], button['given']), ('after', given))
        self.assertRegex(button['reason'], r'^zgoda „GO plan” dana \d\d:\d\d$')

    def test_a_draft_plan_waits_for_go_plan_while_the_spec_is_not_approved(self):
        spec = {'exists': True, 'status': 'draft', 'path': 's', 'statusText': ''}
        plan = {'status': 'draft', 'statusText': 'draft, czeka na GO implement', 'path': 'docs/plans/p.md', 'fileSha': SHA_B}
        by_kind = {b['kind']: b for b in self.buttons(spec=spec, plan=plan)}
        self.assertEqual(by_kind['plan']['state'], 'running')
        self.assertIn('GO plan', by_kind['plan']['reason'])
        approved = {'exists': True, 'status': 'approved', 'path': 's', 'fileSha': SHA_A}
        by_kind = {b['kind']: b for b in self.buttons(spec=approved, plan=plan)}
        self.assertEqual((by_kind['spec']['state'], by_kind['plan']['state']), ('after', 'waiting'))
        self.assertEqual(by_kind['plan']['sha'], SHA_B)

    def test_an_approved_or_running_plan_shows_go_implement_as_after(self):
        for plan_status in ('approved', 'in_progress', 'done'):
            with self.subTest(plan_status=plan_status):
                plan = {'status': plan_status, 'statusText': plan_status, 'path': 'p', 'fileSha': SHA_B}
                self.assertEqual(self.buttons(plan=plan)[0]['state'], 'after')
                self.assertEqual(self.buttons(plan=plan)[0]['word'], 'GO implement')

    def test_cp_and_ready_start_only_with_the_implementation(self):
        self.assertEqual(self.states(plan={'status': 'draft', 'statusText': '', 'path': 'p'}), [('plan', 'running')])
        for plan_status in ('in_progress', 'done'):
            plan = {'status': plan_status, 'statusText': '', 'path': 'p'}
            self.assertEqual(self.states(plan=plan)[-2:], [('cp', 'waiting'), ('ready', 'running')])

    def test_cp_reasons_without_head_or_run_directory(self):
        plan = {'status': 'in_progress', 'statusText': '', 'path': 'p'}
        cp = next(b for b in self.buttons(plan=plan, run_ok=False) if b['kind'] == 'cp')
        self.assertEqual((cp['state'], cp['reason']), ('running', 'brak katalogu przebiegu (.skillsync/runs/<run-id>) dla wpisu zgody'))
        cp = next(b for b in dashboard.consent_buttons({'exists': False}, plan, [], self.WORDS, [], '', True) if b['kind'] == 'cp')
        self.assertEqual((cp['state'], cp['reason'], cp['sha']), ('running', 'brak HEAD repozytorium', ''))
        ready = next(b for b in self.buttons(plan=plan) if b['kind'] == 'ready')
        self.assertEqual((ready['state'], ready['reason'], ready['blocked']), ('running', 'czeka na koniec realizacji (czeka na CP)', ready['reason']))

    def test_after_needs_an_entry_whose_sha_is_the_current_head(self):
        plan = {'status': 'in_progress', 'statusText': '', 'path': 'p'}
        old = {'word': 'CP', 'at': '2026-01-12T10:00:00Z', 'sha': 'd' * 40, 'target': 'main'}
        self.assertEqual(self.states(plan=plan, log=[old])[-2:], [('cp', 'waiting'), ('ready', 'running')], 'an old HEAD is a new consent')
        now = {**old, 'sha': self.HEAD}
        cp = next(b for b in self.buttons(plan=plan, log=[old, now]) if b['kind'] == 'cp')
        self.assertEqual((cp['state'], cp['given'], cp['sha'], cp['path'], cp['target']), ('after', now, self.HEAD, None, 'main'))
        self.assertRegex(cp['reason'], r'^zgoda „CP” dana \d\d:\d\d \(HEAD cccccccc\)$')

    def test_the_last_entry_per_word_decides_and_the_target_falls_back_to_the_word(self):
        plan = {'status': 'in_progress', 'statusText': '', 'path': 'p'}
        first = {'word': 'CP', 'at': '2026-01-12T10:00:00Z', 'sha': self.HEAD}
        second = {'word': 'CP', 'at': '2026-01-12T11:00:00Z', 'sha': 'd' * 40}
        cp = next(b for b in self.buttons(plan=plan, log=[first, second]) if b['kind'] == 'cp')
        self.assertEqual(cp['state'], 'waiting', 'the log is read in order: the last CP wins, here for another HEAD')
        cp = next(b for b in self.buttons(plan=plan, log=[second, first]) if b['kind'] == 'cp')
        self.assertEqual((cp['state'], cp['target']), ('after', 'feature/x'))

    def test_a_plan_sketch_has_no_go_implement_button(self):
        plan = {'status': 'draft', 'statusText': 'szkic, czeka na spec', 'path': 'p'}
        self.assertEqual(self.buttons(plan=plan), [])

    def test_approvals_state_adds_run_and_the_run_id_as_cp_path_and_keeps_the_last_entry(self):
        plan = {'status': 'in_progress', 'statusText': '', 'path': 'p'}
        log = [{'word': 'CP', 'at': '2026-01-12T10:00:00Z', 'sha': 'd' * 40}]
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / '.skillsync' / 'runs' / 'r1').mkdir(parents=True)
            state = dashboard.approvals_state(Path(temp), {'exists': False}, plan, {'words': self.WORDS}, 'r1', self.HEAD, log, [])
            missing = dashboard.approvals_state(Path(temp), {'exists': False}, plan, {'words': self.WORDS}, 'r9', self.HEAD, log, [])
        self.assertEqual([(a['kind'], a['path'], a['run']) for a in state['actions'] if a['kind'] != 'plan'], [('cp', 'r1', 'r1'), ('ready', 'r1', 'r1')])
        self.assertEqual((state['enabled'], state['last']), (True, log[-1]))
        self.assertEqual(next(a for a in state['actions'] if a['kind'] == 'plan')['path'], 'p', 'only cp/ready take the run id as path')
        self.assertEqual([(a['state'], a['run']) for a in missing['actions'] if a['kind'] != 'plan'], [('running', None), ('running', None)])
        off = dashboard.approvals_state(Path('.'), {}, plan, {}, 'r1', self.HEAD, log, [], enabled=False)
        self.assertEqual(off, {'enabled': False, 'actions': [], 'last': log[-1]})


if __name__ == '__main__':
    unittest.main()
