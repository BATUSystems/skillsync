"""skillsync retro (R2): kontrakt wyjść, rubryka, K5 (wyciek), K6 (tylko odczyt), sygnały raportów,
pliki nieczytelne, kandydaci zmiany (K4), koniec do końca i worktree (K11)."""
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from test_skillsync_workflow import CLI, WorkflowFixture
from layers_helpers import make_layer
from layers_helpers import needs_os_symlinks, require_fifo, require_os_symlinks  # noqa: E402

needs_fifo = unittest.skipUnless(hasattr(os, 'mkfifo'), 'mkfifo unavailable')

DIGEST = 'a' * 64
TOP_KEYS = {'schema', 'status', 'runs', 'report_dirs', 'candidates', 'decisions', 'candidates_complete', 'coverage',
            'not_detected', 'notes', 'unknown_cause_keys', 'proposals', 'proposals_truncated'}
DECISIONS_KEYS = {'status', 'path', 'count'}
DECISION_KEYS = {'decision', 'date', 'records_after', 'fixed'}
RUN_KEYS = {'id', 'class', 'steps', 'repeated_steps', 'failed_records', 'not_run_records', 'final_status', 'rubric'}
UNREADABLE_KEYS = {'id', 'class', 'reason'}
RUBRIC_KEYS = {'evidence', 'repeats', 'denied', 'inherited', 'plan_coverage'}
EVIDENCE_KEYS = {'status', 'source', 'pass_records', 'evidence_files', 'verified', 'changed', 'missing', 'refused',
                 'unreadable', 'reused_from_problem'}
REPEATS_KEYS = {'status', 'source', 'repeated_steps', 'attempts_over_1'}
COUNTER_KEYS = {'status', 'source', 'value', 'records_with_value', 'records_total'}
UNAVAILABLE_KEYS = {'status', 'reason'}
DIR_KEYS = {'id', 'has_records', 'provenance', 'files', 'status_signals', 'truncated', 'unreadable_files',
            'unrecorded_signals'}
FILE_KEYS = {'md', 'log', 'diff', 'other'}
SIGNAL_KEYS = {'PASS', 'FAIL', 'BLOCKED', 'NOT_RUN', 'CHANGES_REQUIRED', 'DONE', 'unrecognized'}
COVERAGE_KEYS = {'runs', 'no_data', 'with_records', 'unreadable', 'unreadable_outside_scope', 'uncategorized_causes',
                 'symlinks_skipped', 'invalid_dirs', 'benchmark_runs_skipped'}
RETRO_COVERAGE_KEYS = {'runs', 'invalid_files', 'final_without_retro'}
PROPOSAL_ITEM_KEYS = {'id', 'owner', 'repo', 'target', 'title', 'state', 'diff', 'keys', 'signals', 'created_at'}
PROPOSAL_STATE_ORDER = ['deferred', 'open', 'approved', 'revising', 'stale', 'failed', 'handoff', 'rejected', 'applied']
BLOCKED_KEYS = {'schema', 'status', 'reason', 'run_id'}
CLASSES = {'clean', 'recovery', 'unexplained', 'incomplete', 'no_data', 'unreadable'}
REASONS = {'invalid_name', 'not_regular', 'symlink', 'malformed', 'io_error'}
TOKENS = ['sk-ant-api03abcDEF123', 'sk-proj-abcDEF123xyz', 'ghp_abcDEF123456', 'gho_abcDEF123456',
          'github_pat_11ABCDE_xyz789', 'glpat-abcDEF123456', 'xoxb-123456-abcdef', 'AKIAABCDEFGH12345678']
CHMOD_INEFFECTIVE = os.name != 'posix' or (hasattr(os, 'geteuid') and os.geteuid() == 0)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def write_exact(path, text):
    """Zapis tekstu bajt w bajt (UTF-8, bez tłumaczenia końców linii): na Windows `write_text` zamienia `\n` na
    `\r\n`, a `sha()` liczy z `\n`, więc sha dowodu musi pochodzić z tych samych bajtów, które produkt czyta."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode('utf-8'))
    return path


def rec(step, status='PASS', cause=None, attempt=1, evidence=None, nxt='n', **extra):
    """Rekord checkpointu. PASS bez `evidence` dostaje domyślny dowód e.log (fikstura tworzy ten plik)."""
    if evidence is None:
        evidence = [{'path': 'e.log', 'sha256': sha('evidence\n')}] if status == 'PASS' else []
    return {'step': step, 'status': status, 'next': nxt, 'recorded_at': '2026-10-29T10:00:00+00:00',
            'provenance': 'test', 'attempt': attempt, 'cause': cause, 'duration_seconds': 10, 'cost': None,
            'cost_unit': 'USD', 'fingerprint': {'head': None, 'digest': DIGEST}, 'evidence': evidence, **extra}


def snapshot(root):
    """Drzewo projektu: (rodzaj, rozmiar, mtime_ns) dla każdej pozycji, bez podążania za dowiązaniami."""
    result = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in [''] + dirnames + filenames:
            path = os.path.join(dirpath, name) if name else dirpath
            st = os.lstat(path)
            result[os.path.relpath(path, root)] = (stat.S_IFMT(st.st_mode), st.st_size, st.st_mtime_ns)
    return result


# Imię osoby składane z części, żeby sam plik testów go nie zawierał.
PERSON = 'Bar' + 'cis'


class RetroBase(WorkflowFixture):
    def setUp(self):
        super().setUp()
        self.write('e.log', 'evidence\n')
        self.text = ''

    # ---- zapis danych ----
    def write(self, name, content):
        return write_exact(self.root / name, content)

    def run_file(self, ident, *records, close=True, **top):
        records = list(records)
        if close and records and records[-1]['status'] == 'PASS' and 'final' not in records[-1]:
            records[-1] = {**records[-1], 'final': True}
        self.write(f'.skillsync/runs/{ident}.json', json.dumps(
            {'schema_version': 1, 'run_id': ident, 'checkpoints': records, **top}))

    def runs_dir(self, *parts):
        path = self.root.joinpath('.skillsync/runs', *parts)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def evidence(self, name, content='proof\n'):
        self.write(f'.skillsync/evidence/{name}', content)
        return {'path': f'.skillsync/evidence/{name}', 'sha256': sha(content)}

    def report_file(self, directory, rel, content):
        path = self.runs_dir(directory) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        return path

    # ---- uruchamianie ----
    def run_retro(self, *args, path=None, timeout=20):
        command = [sys.executable, str(CLI), 'retro', str(path or self.root), *map(str, args), '--json']
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                    timeout=timeout, cwd=self.root)
        except subprocess.TimeoutExpired:
            self.fail('retro zawisło (limit ' + str(timeout) + ' s): brak sprawdzenia typu pliku przed otwarciem?')
        self.text = result.stdout + result.stderr
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        return result

    def parsed(self, result, code):
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        try:
            return json.loads(result.stdout)
        except ValueError:
            self.fail('stdout nie jest JSON-em: ' + (result.stdout + result.stderr)[:300])

    def full(self, *args, code=0, **kw):
        """Pełny raport: sprawdza status/exit i zamkniętą listę pól na każdym poziomie."""
        data = self.parsed(self.run_retro(*args, **kw), code)
        self.check_shape(data)
        return data

    def refused(self, *args, path=None):
        result = self.run_retro(*args, path=path)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(result.stderr.startswith('skillsync:'), 'oczekiwano błędu skillsync:, jest: ' + result.stderr)
        self.assertEqual(result.stdout.strip(), '', 'błąd nie może mieć JSON-u na stdout')
        return result

    def by_id(self, data):
        return {item['id']: item for item in data['runs']}

    # ---- zamknięta lista pól ----
    def check_criterion(self, name, item):
        status = item.get('status')
        if status == 'unavailable':
            self.assertEqual(set(item), UNAVAILABLE_KEYS, name)
            self.assertTrue(isinstance(item['reason'], str) and item['reason'], name)
            return
        self.assertIn(status, ('available', 'partial'), name)
        expected = {'evidence': EVIDENCE_KEYS, 'repeats': REPEATS_KEYS, 'denied': COUNTER_KEYS,
                    'inherited': COUNTER_KEYS}[name]
        self.assertEqual(set(item), expected, name)

    def check_shape(self, data):
        self.assertEqual(set(data), TOP_KEYS)
        self.assertEqual(data['schema'], 1)
        self.assertIs(type(data['schema']), int)
        self.assertIn(data['status'], ('PASS', 'NOT_RUN', 'BLOCKED'))
        self.assertIsInstance(data['candidates'], list)
        self.assertEqual(set(data['decisions']), DECISIONS_KEYS)
        self.assertIn(data['decisions']['status'], ('absent', 'available', 'invalid'))
        self.assertIsInstance(data['candidates_complete'], bool)
        self.assertTrue(isinstance(data['not_detected'], str) and data['not_detected'])
        self.assertTrue(isinstance(data['notes'], str) and data['notes'])
        for item in data['runs']:
            self.assertIn(item['class'], CLASSES)
            if item['class'] == 'unreadable':
                self.assertEqual(set(item), UNREADABLE_KEYS)
                self.assertIn(item['reason'], REASONS)
                continue
            self.assertEqual(set(item), RUN_KEYS)
            self.assertEqual(set(item['rubric']), RUBRIC_KEYS)
            for name, criterion in item['rubric'].items():
                if name == 'plan_coverage':
                    self.assertEqual(set(criterion), UNAVAILABLE_KEYS)
                    self.assertEqual(criterion['status'], 'unavailable')
                else:
                    self.check_criterion(name, criterion)
        for item in data['report_dirs']:
            self.assertEqual(set(item), DIR_KEYS)
            self.assertEqual(set(item['files']), FILE_KEYS)
            self.assertEqual(set(item['status_signals']), SIGNAL_KEYS)
            self.assertEqual(item['provenance'], 'report-heuristic')
            self.assertIsInstance(item['has_records'], bool)
            self.assertIsInstance(item['truncated'], bool)
            self.assertIs(type(item['unrecorded_signals']), int)
            self.assertGreaterEqual(item['unrecorded_signals'], 0)
        unknown = data['unknown_cause_keys']
        self.assertTrue(unknown is None or (isinstance(unknown, list) and unknown == sorted(unknown)), unknown)
        cov = data['coverage']
        self.assertEqual(set(cov), COVERAGE_KEYS | {'retro'})  # K7: `retro` (nie `retro --all`) ma sekcję coverage.retro
        self.assertEqual(set(cov['retro']), RETRO_COVERAGE_KEYS)
        for name in RETRO_COVERAGE_KEYS:
            self.assertIs(type(cov['retro'][name]), int)
            self.assertGreaterEqual(cov['retro'][name], 0)
        self.assertIsInstance(data['proposals'], list)
        self.assertIs(type(data['proposals_truncated']), bool)
        self.assertLessEqual(len(data['proposals']), 50)
        for item in data['proposals']:
            self.assertEqual(set(item), PROPOSAL_ITEM_KEYS)
            self.assertIn(item['state'], PROPOSAL_STATE_ORDER)
            self.assertIs(type(item['signals']), int)
            self.assertIsInstance(item['keys'], list)
            self.assertIn(item['repo'], ('library', 'project'))
        cov = {k: v for k, v in cov.items() if k != 'retro'}
        self.assertEqual(cov['runs'], cov['with_records'] + cov['no_data'] + cov['unreadable'])
        self.assertEqual(cov['runs'], len(data['runs']))
        self.assertEqual(cov['unreadable'], sum(i['class'] == 'unreadable' for i in data['runs']))
        self.assertEqual(cov['no_data'], sum(i['class'] == 'no_data' for i in data['runs']))

    def assert_absent(self, *fragments):
        for fragment in fragments:
            self.assertNotIn(fragment, self.text)

    def make_fifo(self, *parts):
        require_fifo(self)
        path = self.root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.mkfifo(path)
        return path


class ExitContract(RetroBase):
    """Tabela wyjść: status, exit i kształt dla każdego wiersza."""

    def test_pass_when_a_run_has_records(self):
        self.run_file('good', rec('a'), rec('b'))
        data = self.full()
        self.assertEqual(data['status'], 'PASS')
        self.assertEqual([(i['id'], i['class']) for i in data['runs']], [('good', 'clean')])
        self.assertEqual(data['candidates'], [])
        self.assertIs(data['candidates_complete'], True)
        self.assertEqual(data['coverage']['with_records'], 1)

    def test_pass_with_one_run_and_one_no_data(self):
        self.run_file('good', rec('a'))
        self.runs_dir('lonely')
        data = self.full()
        self.assertEqual(data['status'], 'PASS')
        self.assertEqual({i['id']: i['class'] for i in data['runs']}, {'good': 'clean', 'lonely': 'no_data'})

    def test_pass_does_not_mean_the_runs_were_good(self):
        self.run_file('bad', rec('a', 'FAIL'))
        data = self.full()
        self.assertEqual((data['status'], data['runs'][0]['class']), ('PASS', 'incomplete'))

    def test_not_run_for_empty_store_variants(self):
        data = self.full()
        self.assertEqual((data['status'], data['runs'], data['report_dirs']), ('NOT_RUN', [], []))
        self.assertEqual(data['coverage']['runs'], 0)
        self.assertFalse((self.root / '.skillsync').exists())
        self.runs_dir()
        self.assertEqual(self.full()['status'], 'NOT_RUN')

    def test_not_run_when_only_no_data(self):
        self.runs_dir('lonely')
        self.run_file('empty')
        data = self.full()
        self.assertEqual(data['status'], 'NOT_RUN')
        self.assertEqual({i['class'] for i in data['runs']}, {'no_data'})
        self.assertEqual((data['coverage']['no_data'], data['coverage']['with_records']), (2, 0))

    def test_not_run_when_only_a_benchmark_exists(self):
        self.write('.skillsync/runs/benchmark-1.json', json.dumps(
            {'schema_version': 1, 'checkpoints': [], 'benchmark': {'cases': [{'status': 'PASS'}]}}))
        data = self.full()
        self.assertEqual((data['status'], data['runs']), ('NOT_RUN', []))
        self.assertEqual(data['coverage']['benchmark_runs_skipped'], 1)

    def test_benchmark_is_not_a_run(self):
        self.run_file('good', rec('a'))
        self.write('.skillsync/runs/benchmark-1.json', json.dumps(
            {'schema_version': 1, 'checkpoints': [], 'benchmark': {'cases': [{'status': 'PASS'}]}}))
        data = self.full()
        self.assertEqual([i['id'] for i in data['runs']], ['good'])
        self.assertEqual(data['coverage']['benchmark_runs_skipped'], 1)
        self.assertEqual(data['coverage']['runs'], 1)

    def test_blocked_when_a_run_in_scope_is_unreadable(self):
        self.run_file('good', rec('a'))
        self.write('.skillsync/runs/broken.json', '{not json')
        data = self.full(code=2)
        self.assertEqual(data['status'], 'BLOCKED')
        self.assertIn('good', self.by_id(data))
        self.assertEqual(self.by_id(data)['broken'], {'id': 'broken', 'class': 'unreadable', 'reason': 'malformed'})
        self.assertEqual(data['coverage']['unreadable'], 1)

    def test_blocked_shape_for_unknown_run_id(self):
        self.run_file('good', rec('a'))
        data = self.parsed(self.run_retro('--run-id', 'absent'), 2)
        self.assertEqual(set(data), BLOCKED_KEYS)
        self.assertEqual((data['status'], data['run_id']), ('BLOCKED', 'absent'))
        self.assertTrue(isinstance(data['reason'], str) and data['reason'])

    def test_blocked_for_run_id_that_only_matches_a_benchmark(self):
        self.write('.skillsync/runs/benchmark-1.json', json.dumps(
            {'schema_version': 1, 'checkpoints': [], 'benchmark': {'cases': [{'status': 'PASS'}]}}))
        data = self.parsed(self.run_retro('--run-id', 'benchmark-1'), 2)
        self.assertEqual((set(data), data['status']), (BLOCKED_KEYS, 'BLOCKED'))

    def test_blocked_for_run_id_on_empty_project(self):
        data = self.parsed(self.run_retro('--run-id', 'absent'), 2)
        self.assertEqual((set(data), data['run_id']), (BLOCKED_KEYS, 'absent'))

    def test_run_id_matches_file_or_dotted_file_or_directory_or_dotted_directory(self):
        creators = {'x.json': lambda: self.run_file('x', rec('a')),
                    'x.p.json': lambda: self.run_file('x.p', rec('a')),
                    'x/': lambda: self.runs_dir('x'),
                    'x.d/': lambda: self.runs_dir('x.d')}
        for name, create in creators.items():
            with self.subTest(name):
                shutil.rmtree(self.root / '.skillsync', ignore_errors=True)
                blocked = self.parsed(self.run_retro('--run-id', 'x'), 2)
                self.assertEqual((set(blocked), blocked['status']), (BLOCKED_KEYS, 'BLOCKED'))
                create()
                self.assertIn(self.full('--run-id', 'x')['status'], ('PASS', 'NOT_RUN'))

    def test_run_id_of_a_shorter_directory_name_than_the_dotted_one_is_not_found(self):
        self.runs_dir('x.y.z')
        data = self.parsed(self.run_retro('--run-id', 'x.y.z.w'), 2)
        self.assertEqual((set(data), data['status']), (BLOCKED_KEYS, 'BLOCKED'))

    def test_run_id_prefix_directory_with_dots_gives_not_run(self):
        self.runs_dir('x.y.z')
        data = self.full('--run-id', 'x.y')
        self.assertEqual(data['status'], 'NOT_RUN')
        self.assertEqual([(i['id'], i['class']) for i in data['runs']], [('x.y.z', 'no_data')])
        self.assertEqual([(d['id'], d['has_records']) for d in data['report_dirs']], [('x.y.z', False)])

    def test_invalid_run_id_is_a_skillsync_error(self):
        for value in ('bad id', 'a/b', '.hidden', 'x' * 81, ''):
            with self.subTest(value=value):
                self.refused('--run-id', value)

    def test_missing_manifest_is_a_skillsync_error(self):
        empty = self.base / 'no-manifest'
        empty.mkdir()
        self.refused(path=empty)

    @needs_os_symlinks
    def test_runs_directory_symlink_is_a_skillsync_error(self):
        target = self.base / 'elsewhere'
        target.mkdir()
        (self.root / '.skillsync').mkdir()
        (self.root / '.skillsync/runs').symlink_to(target, target_is_directory=True)
        self.refused()

    @needs_os_symlinks
    def test_skillsync_directory_symlink_is_a_skillsync_error(self):
        target = self.base / 'elsewhere'
        (target / 'runs').mkdir(parents=True)
        (self.root / '.skillsync').symlink_to(target, target_is_directory=True)
        self.refused()

    def test_layer_option_is_accepted_and_the_removed_library_option_is_not(self):
        # K1/K3: `--layer id=path` replaced `--library` (removed from every command).
        self.run_file('good', rec('a'))
        data = self.full('--layer', f'core={self.library}')
        self.assertEqual(data['status'], 'PASS')
        result = self.run_retro('--library', self.library)
        self.assertEqual(result.returncode, 2, self.text)
        self.assertEqual(result.stdout.strip(), '')

    def test_output_is_deterministic(self):
        self.run_file('b', rec('a'))
        self.run_file('a', rec('a', 'FAIL', cause='x'), rec('a'))
        self.runs_dir('lonely')
        first, second = self.run_retro(), self.run_retro()
        self.assertEqual(self.parsed(first, 0)['status'], 'PASS')
        self.assertEqual(first.stdout, second.stdout)


class ClosedFieldLists(RetroBase):
    """Lista pól wyjścia jest zamknięta dla każdego kształtu (porównanie kluczy na każdym poziomie)."""

    def test_full_report_with_every_kind_of_entry(self):
        self.run_file('good', rec('a'), rec('b', denied=1, inherited=0))
        self.run_file('empty')
        self.runs_dir('lonely')
        self.runs_dir('good')
        self.report_file('good', 'r.md', 'Status: PASS\n')
        self.write('.skillsync/runs/broken.json', '{')
        data = self.full(code=2)
        self.assertEqual({i['class'] for i in data['runs']}, {'clean', 'no_data', 'unreadable'})

    def test_full_report_closed_even_when_records_carry_extra_fields(self):
        extras = dict(cause='ZXQCAUSE', cause_key='k-one', owner='test', unknown_future='ZXQFUTURE')
        self.write('.skillsync/runs/good.json', json.dumps(
            {'schema_version': 1, 'run_id': 'good', 'project': '/ZXQPROJECT', 'extra': 'ZXQTOP',
             'checkpoints': [rec('a', 'FAIL', nxt='ZXQNEXT', **extras), rec('a', final=True)]}))
        data = self.full()
        self.assert_absent('ZXQCAUSE', 'ZXQNEXT', 'ZXQPROJECT', 'ZXQTOP', 'ZXQFUTURE')
        self.assertEqual(set(data['runs'][0]), RUN_KEYS)

    def test_no_data_entry_has_zeros_and_all_criteria_unavailable(self):
        self.runs_dir('lonely')
        item = self.full()['runs'][0]
        self.assertEqual({k: v for k, v in item.items() if k != 'rubric'},
                         {'id': 'lonely', 'class': 'no_data', 'steps': 0, 'repeated_steps': 0, 'failed_records': 0,
                          'not_run_records': 0, 'final_status': None})
        self.assertEqual(set(item['rubric']), RUBRIC_KEYS)
        for name, criterion in item['rubric'].items():
            self.assertEqual(criterion['status'], 'unavailable', name)
            self.assertTrue(criterion['reason'], name)

    def test_unreadable_entry_has_exactly_three_fields(self):
        self.write('.skillsync/runs/broken.json', 'x')
        data = self.full(code=2)
        self.assertEqual(data['runs'], [{'id': 'broken', 'class': 'unreadable', 'reason': 'malformed'}])

    def test_report_dir_entry_has_exactly_the_closed_fields(self):
        self.report_file('rd', 'a.md', 'Status: PASS\n')
        data = self.full()
        self.assertEqual(data['report_dirs'], [{
            'id': 'rd', 'has_records': False, 'provenance': 'report-heuristic', 'unrecorded_signals': 0,
            'files': {'md': 1, 'log': 0, 'diff': 0, 'other': 0},
            'status_signals': {'PASS': 1, 'FAIL': 0, 'BLOCKED': 0, 'NOT_RUN': 0, 'CHANGES_REQUIRED': 0, 'DONE': 0,
                               'unrecognized': 0},
            'unrecorded_signals': 0, 'truncated': False, 'unreadable_files': 0}])

    def test_blocked_shape_has_exactly_four_fields(self):
        data = self.parsed(self.run_retro('--run-id', 'absent'), 2)
        self.assertEqual(set(data), BLOCKED_KEYS)

    def test_coverage_is_closed_and_zero_for_nothing(self):
        self.assertEqual(self.full()['coverage'], {
            'runs': 0, 'no_data': 0, 'with_records': 0, 'unreadable': 0, 'unreadable_outside_scope': 0,
            'uncategorized_causes': 0, 'symlinks_skipped': 0, 'invalid_dirs': 0, 'benchmark_runs_skipped': 0,
            'retro': {'runs': 0, 'invalid_files': 0, 'final_without_retro': 0}})  # K7: nowa sekcja coverage.retro

    def test_exact_entry_of_a_recovery_run(self):
        self.run_file('r', rec('a', 'FAIL', cause='flaky'), rec('a'), rec('b'))
        item = self.full()['runs'][0]
        self.assertEqual(item['rubric']['evidence'], {
            'status': 'available', 'source': 'checkpoints+sha256', 'pass_records': 2, 'evidence_files': 2,
            'verified': 2, 'changed': 0, 'missing': 0, 'refused': 0, 'unreadable': 0, 'reused_from_problem': 0})
        self.assertEqual(item['rubric']['repeats'], {
            'status': 'available', 'source': 'checkpoints', 'repeated_steps': 1, 'attempts_over_1': 0})
        self.assertEqual({k: v for k, v in item.items() if k != 'rubric'},
                         {'id': 'r', 'class': 'recovery', 'steps': 2, 'repeated_steps': 1, 'failed_records': 1,
                          'not_run_records': 0, 'final_status': 'PASS'})
        for name in ('denied', 'inherited', 'plan_coverage'):
            self.assertEqual(item['rubric'][name]['status'], 'unavailable', name)


class Rubric(RetroBase):
    def rubric(self, *records, ident='r', close=True):
        self.run_file(ident, *records, close=close)
        return self.by_id(self.full())[ident]['rubric']

    def test_counters_available_partial_unavailable(self):
        for name in ('denied', 'inherited'):
            with self.subTest(name=name):
                everything = self.rubric(rec('a', **{name: 2}), rec('b', **{name: 3}), ident='all')[name]
                self.assertEqual(everything, {'status': 'available', 'source': 'checkpoints', 'value': 5,
                                              'records_with_value': 2, 'records_total': 2})
                some = self.rubric(rec('a', **{name: 2}), rec('b'), rec('c', **{name: 1}), ident='some')[name]
                self.assertEqual(some, {'status': 'partial', 'source': 'checkpoints', 'value': 3,
                                        'records_with_value': 2, 'records_total': 3})
                none = self.rubric(rec('a'), rec('b'), ident='none')[name]
                self.assertEqual(none['status'], 'unavailable')
                self.assertTrue(none['reason'])

    def test_explicit_zero_is_a_number_not_a_gap(self):
        for name in ('denied', 'inherited'):
            with self.subTest(name=name):
                zero = self.rubric(rec('a', **{name: 0}), rec('b', **{name: 0}), ident='zero')[name]
                self.assertEqual((zero['status'], zero['value'], zero['records_with_value']), ('available', 0, 2))
                mixed = self.rubric(rec('a', **{name: 0}), rec('b'), ident='mixed')[name]
                self.assertEqual((mixed['status'], mixed['value'], mixed['records_with_value'],
                                  mixed['records_total']), ('partial', 0, 1, 2))

    def test_counters_are_independent(self):
        rubric = self.rubric(rec('a', denied=4), rec('b', denied=1))
        self.assertEqual(rubric['denied']['value'], 5)
        self.assertEqual(rubric['inherited']['status'], 'unavailable')

    def test_repeats_count_repeated_steps_and_attempts(self):
        rubric = self.rubric(rec('a', 'FAIL', cause='x'), rec('a'), rec('b', attempt=3, cause='y'), rec('c'))
        self.assertEqual(rubric['repeats'], {'status': 'available', 'source': 'checkpoints',
                                             'repeated_steps': 1, 'attempts_over_1': 1})

    def test_plan_coverage_is_always_unavailable(self):
        for ident, records in {'clean': [rec('a')], 'rec': [rec('a', 'FAIL', cause='x'), rec('a')],
                               'inc': [rec('a', 'FAIL')]}.items():
            with self.subTest(ident):
                criterion = self.rubric(*records, ident=ident)['plan_coverage']
                self.assertEqual(criterion['status'], 'unavailable')
                self.assertTrue(criterion['reason'])

    def test_evidence_verified_available(self):
        proof = self.evidence('one.log')
        evidence = self.rubric(rec('a', evidence=[proof]))['evidence']
        self.assertEqual(evidence, {'status': 'available', 'source': 'checkpoints+sha256', 'pass_records': 1,
                                    'evidence_files': 1, 'verified': 1, 'changed': 0, 'missing': 0, 'refused': 0,
                                    'unreadable': 0, 'reused_from_problem': 0})

    def test_evidence_written_with_crlf_text_mode_is_changed_but_exact_write_is_verified(self):
        """Mechanizm G1 wymuszony na Linuxie: tryb tekstowy Windows (`\\r\\n`) rozjeżdża sha liczone z `\\n`,
        a `write_exact` (helper fikstury) zachowuje bajty, więc dowód jest zweryfikowany na każdym systemie."""
        content = 'proof line 1\nproof line 2\n'
        crlf = self.root / '.skillsync/evidence/crlf.log'
        crlf.parent.mkdir(parents=True, exist_ok=True)
        with open(crlf, 'wb') as raw, io.TextIOWrapper(raw, encoding='utf-8', newline='\r\n') as stream:
            stream.write(content)
        self.assertEqual(crlf.read_bytes(), content.replace('\n', '\r\n').encode())
        self.assertNotEqual(hashlib.sha256(crlf.read_bytes()).hexdigest(), sha(content))
        proof = {'path': '.skillsync/evidence/crlf.log', 'sha256': sha(content)}
        self.assertEqual(self.rubric(rec('a', evidence=[proof]))['evidence']['changed'], 1)
        exact = write_exact(self.root / '.skillsync/evidence/exact.log', content)
        self.assertEqual(exact.read_bytes(), content.encode())
        self.assertNotIn(b'\r', exact.read_bytes())
        fixed = {'path': '.skillsync/evidence/exact.log', 'sha256': sha(content)}
        evidence = self.rubric(rec('a', evidence=[fixed]), ident='exact')['evidence']
        self.assertEqual((evidence['verified'], evidence['changed']), (1, 0))

    def test_evidence_changed_is_partial_and_class_stays_from_records(self):
        proof = self.evidence('one.log')
        self.write(proof['path'], 'rewritten after the checkpoint\n')
        self.run_file('r', rec('a', 'FAIL', cause='x'), rec('a', evidence=[proof]))
        item = self.by_id(self.full())['r']
        self.assertEqual(item['class'], 'recovery')
        self.assertEqual((item['rubric']['evidence']['status'], item['rubric']['evidence']['changed'],
                          item['rubric']['evidence']['verified']), ('partial', 1, 0))

    def test_changed_evidence_does_not_downgrade_a_clean_class(self):
        proof = self.evidence('one.log')
        self.write(proof['path'], 'rewritten\n')
        self.run_file('r', rec('a', evidence=[proof]))
        item = self.full()['runs'][0]
        self.assertEqual((item['class'], item['rubric']['evidence']['status']), ('clean', 'partial'))

    def test_evidence_missing_file(self):
        proof = self.evidence('gone.log')
        os.unlink(self.root / proof['path'])
        evidence = self.rubric(rec('a', evidence=[proof]))['evidence']
        self.assertEqual((evidence['status'], evidence['missing'], evidence['verified']), ('partial', 1, 0))

    def test_evidence_directory_counts_as_missing(self):
        (self.root / '.skillsync/evidence/adir').mkdir(parents=True)
        proof = {'path': '.skillsync/evidence/adir', 'sha256': DIGEST}
        evidence = self.rubric(rec('a', evidence=[proof]))['evidence']
        self.assertEqual((evidence['status'], evidence['missing']), ('partial', 1))

    @needs_fifo
    def test_evidence_fifo_counts_as_missing_without_hanging(self):
        self.make_fifo('.skillsync/evidence/pipe.log')
        proof = {'path': '.skillsync/evidence/pipe.log', 'sha256': DIGEST}
        self.run_file('r', rec('a', evidence=[proof]))
        data = self.full(timeout=8)
        evidence = data['runs'][0]['rubric']['evidence']
        self.assertEqual((evidence['status'], evidence['missing']), ('partial', 1))

    @needs_os_symlinks
    def test_evidence_symlink_leaving_the_project_is_refused(self):
        outside = self.base / 'outside.log'
        write_exact(outside, 'outside\n')
        link = self.root / '.skillsync/evidence/out.log'
        link.parent.mkdir(parents=True)
        link.symlink_to(outside)
        proof = {'path': '.skillsync/evidence/out.log', 'sha256': sha('outside\n')}
        evidence = self.rubric(rec('a', evidence=[proof]))['evidence']
        self.assertEqual((evidence['status'], evidence['refused'], evidence['verified']), ('partial', 1, 0))

    @unittest.skipIf(CHMOD_INEFFECTIVE, 'chmod 000 nie blokuje odczytu (root lub nie-POSIX)')
    def test_evidence_unreadable_is_counted_not_raised(self):
        proof = self.evidence('ZXQEVIDENCE.log')
        path = self.root / proof['path']
        path.chmod(0)
        self.addCleanup(path.chmod, 0o644)
        self.run_file('r', rec('a', evidence=[proof]))
        data = self.full()
        evidence = data['runs'][0]['rubric']['evidence']
        self.assertEqual((data['status'], evidence['status'], evidence['unreadable']), ('PASS', 'partial', 1))
        self.assert_absent('ZXQEVIDENCE', str(self.root))

    def test_record_with_two_evidence_files(self):
        good = self.evidence('good.log')
        changed = self.evidence('changed.log')
        self.write(changed['path'], 'different\n')
        evidence = self.rubric(rec('a', evidence=[good, changed]))['evidence']
        self.assertEqual((evidence['pass_records'], evidence['evidence_files'], evidence['verified'],
                          evidence['changed'], evidence['status']), (1, 2, 1, 1, 'partial'))

    @needs_os_symlinks
    def test_every_state_in_one_run(self):
        good = self.evidence('good.log')
        changed = self.evidence('changed.log')
        self.write(changed['path'], 'different\n')
        gone = {'path': '.skillsync/evidence/gone.log', 'sha256': DIGEST}
        outside = self.base / 'outside.log'
        outside.write_text('o')
        (self.root / '.skillsync/evidence/out.log').symlink_to(outside)
        refused = {'path': '.skillsync/evidence/out.log', 'sha256': DIGEST}
        evidence = self.rubric(rec('a', evidence=[good, changed, gone, refused]), rec('b'))['evidence']
        self.assertEqual((evidence['pass_records'], evidence['evidence_files'], evidence['verified'],
                          evidence['changed'], evidence['missing'], evidence['refused'], evidence['unreadable']),
                         (2, 5, 2, 1, 1, 1, 0))
        self.assertEqual(evidence['status'], 'partial')

    def test_evidence_unavailable_without_pass_records(self):
        rubric = self.rubric(rec('a', 'FAIL', cause='x'), rec('b', 'BLOCKED', cause='y'))
        self.assertEqual(rubric['evidence']['status'], 'unavailable')
        self.assertTrue(rubric['evidence']['reason'])

    def test_every_evidence_file_of_every_pass_record_is_counted(self):
        a, b = self.evidence('a.log'), self.evidence('b.log')
        evidence = self.rubric(rec('a', evidence=[a]), rec('b', evidence=[b]), rec('c', evidence=[a, b]))['evidence']
        self.assertEqual((evidence['pass_records'], evidence['evidence_files'], evidence['verified']), (3, 4, 4))

    def test_evidence_path_is_not_printed(self):
        proof = self.evidence('ZXQSECRETNAME.log')
        self.write(proof['path'], 'changed\n')
        gone = {'path': '.skillsync/evidence/ZXQGONE.log', 'sha256': DIGEST}
        self.run_file('r', rec('a', evidence=[proof, gone]))
        self.full()
        self.assert_absent('ZXQSECRETNAME', 'ZXQGONE')


class SessionContentStaysOut(RetroBase):
    """K5: znaczniki tekstu nie mogą wystąpić w całym wyjściu (stdout i stderr)."""

    def test_cause_and_next_text_never_appear(self):
        self.run_file('r', rec('a', 'FAIL', cause='ZXQCAUSETEXT password=hunter2', nxt='ZXQNEXTTEXT'),
                      rec('a', cause='ZXQCAUSEPASS', nxt='ZXQNEXTPASS'))
        self.full()
        self.assert_absent('ZXQCAUSETEXT', 'ZXQNEXTTEXT', 'ZXQCAUSEPASS', 'ZXQNEXTPASS', 'hunter2')

    def test_file_content_fields_other_than_id_never_appear(self):
        self.write('.skillsync/runs/good.json', json.dumps({
            'schema_version': 1, 'run_id': 'ZXQRUNIDFIELD', 'project': '/ZXQPROJECTFIELD',
            'checkpoints': [rec('a', provenance='ZXQPROVENANCE', cause_key='k-one', cause='ZXQCAUSEKEYTEXT',
                                final=True)]}))
        data = self.full()
        self.assertEqual([i['id'] for i in data['runs']], ['good'])
        self.assert_absent('ZXQRUNIDFIELD', 'ZXQPROJECTFIELD', 'ZXQPROVENANCE', 'ZXQCAUSEKEYTEXT', str(self.root))

    def test_report_file_names_and_content_never_appear(self):
        self.run_file('rd.a', rec('a'))
        self.report_file('rd', 'ZXQFILENAME.md', 'Status: PASS\nZXQCONTENT line\nStatus: ZXQSTATUSTEXT\n'
                                                 'token ghp_ZXQLEAKEDTOKEN12345\n')
        self.report_file('rd', 'ZXQSUBDIR/ZXQNESTED.log', 'ZXQLOGCONTENT\n')
        self.report_file('rd', 'ZXQDIFF.diff', '+ZXQDIFFCONTENT\n')
        data = self.full()
        self.assertEqual(data['report_dirs'][0]['status_signals']['PASS'], 1)
        self.assertEqual(data['report_dirs'][0]['status_signals']['unrecognized'], 1)
        self.assert_absent('ZXQFILENAME', 'ZXQCONTENT', 'ZXQSTATUSTEXT', 'ZXQLEAKEDTOKEN', 'ZXQSUBDIR', 'ZXQNESTED',
                           'ZXQLOGCONTENT', 'ZXQDIFF')

    def test_run_file_name_outside_slug_never_appears(self):
        self.write('.skillsync/runs/ZXQ bad name (1).json', json.dumps(
            {'schema_version': 1, 'checkpoints': [rec('a', final=True)]}))
        data = self.full(code=2)
        item = data['runs'][0]
        self.assertEqual((item['class'], item['reason']), ('unreadable', 'invalid_name'))
        self.assertRegex(item['id'], r'^invalid-name:[0-9a-f]{12}$')
        self.assert_absent('ZXQ', 'bad name')

    def test_directory_name_outside_slug_is_only_counted(self):
        self.runs_dir('ZXQ bad dir!')
        self.run_file('real', rec('a'))
        data = self.full()
        self.assertEqual(data['coverage']['invalid_dirs'], 1)
        self.assertEqual([d['id'] for d in data['report_dirs']], [])
        self.assertEqual([i['id'] for i in data['runs']], ['real'])
        self.assert_absent('ZXQ', 'bad dir')

    def test_non_utf8_file_and_directory_names_do_not_break_the_report(self):
        runs = os.fsencode(self.runs_dir())
        try:
            with open(runs + b'/\xff\xfe-bad.json', 'wb') as stream:
                stream.write(b'{}')
            os.mkdir(runs + b'/\xff\xfe-dir')
            os.mkdir(runs + b'/rd')
            with open(runs + b'/rd/\xff\xfe-file.md', 'wb') as stream:
                stream.write(b'Status: PASS\n')
        except OSError:
            self.skipTest('system plików nie przyjmuje nazw spoza UTF-8')
        self.run_file('real', rec('a'))
        data = self.full(code=2)
        invalid = [i for i in data['runs'] if i['class'] == 'unreadable']
        self.assertEqual([(i['reason']) for i in invalid], ['invalid_name'])
        self.assertRegex(invalid[0]['id'], r'^invalid-name:[0-9a-f]{12}$')
        self.assertEqual(data['coverage']['invalid_dirs'], 1)
        self.assertEqual({d['id']: d['status_signals']['PASS'] for d in data['report_dirs']}, {'rd': 1})

    def test_non_utf8_name_outside_scope_does_not_break_scoped_report(self):
        runs = os.fsencode(self.runs_dir())
        try:
            with open(runs + b'/\xff-bad.json', 'wb') as stream:
                stream.write(b'{}')
        except OSError:
            self.skipTest('system plików nie przyjmuje nazw spoza UTF-8')
        self.run_file('real', rec('a'))
        data = self.full('--run-id', 'real')
        self.assertEqual(data['status'], 'PASS')
        self.assertEqual(data['coverage']['unreadable_outside_scope'], 1)

    def test_unreadable_run_does_not_print_path_or_exception_text(self):
        self.write('.skillsync/runs/broken.json', '{"schema_version": 1, "checkpoints": [ZXQJSONBODY')
        self.full(code=2)
        self.assert_absent(str(self.root), '.skillsync/runs', 'broken.json', 'ZXQJSONBODY', 'Expecting', 'line 1 column')

    @needs_os_symlinks
    def test_unreadable_variants_print_no_path(self):
        self.write('.skillsync/runs/bad-schema.json', json.dumps({'schema_version': 9, 'checkpoints': []}))
        (self.root / '.skillsync/runs/lnk.json').symlink_to(self.root / '.skillsync/runs/bad-schema.json')
        (self.root / '.skillsync/runs/adir.json').mkdir()
        self.full(code=2)
        self.assert_absent(str(self.root), '.skillsync/runs', '.json', 'ValueError', 'Errno')

    def test_unreadable_report_file_is_counted_without_path(self):
        if CHMOD_INEFFECTIVE:
            self.skipTest('chmod 000 nie blokuje odczytu (root lub nie-POSIX)')
        path = self.report_file('rd', 'ZXQHIDDEN.md', 'Status: PASS\n')
        path.chmod(0)
        self.addCleanup(path.chmod, 0o644)
        self.report_file('rd', 'ok.md', 'Status: FAIL\n')
        data = self.full()
        entry = data['report_dirs'][0]
        self.assertEqual(entry['unreadable_files'], 1)
        self.assertEqual(entry['status_signals']['FAIL'], 1)
        self.assert_absent('ZXQHIDDEN', str(self.root), 'Errno', 'Permission')


class TokenMasking(RetroBase):
    """K5: wzorce tokenów w id i run_id maskowane w całości, reszta segmentu zostaje; poprawne wartości czytelne."""

    def ids(self, *names):
        for name in names:
            self.run_file(name, rec('a'))
        return {i['id'] for i in self.full()['runs']}

    def test_every_token_pattern_is_masked_in_a_run_id(self):
        for token in TOKENS:
            with self.subTest(token=token):
                for entry in self.root.glob('.skillsync/runs/*'):
                    entry.unlink()
                ids = self.ids(f'pre-{token}.P1', f'{token}.P2')
                self.assertEqual(ids, {'pre-[REDACTED].P1', '[REDACTED].P2'})
                self.assert_absent(token)

    def test_long_alphanumeric_run_is_masked_at_32_but_not_31(self):
        ids = self.ids('run-' + 'a1b2c3d4' * 5, 'ok-' + 'a' * 31, 'bad-' + 'a' * 32)
        self.assertEqual(ids, {'run-[REDACTED]', 'ok-' + 'a' * 31, 'bad-[REDACTED]'})

    def test_valid_long_and_structured_ids_stay_readable(self):
        names = ['123e4567-e89b-12d3-a456-426614174000',
                 '-'.join(['abcdefgh'] * 9),
                 'this_is_a_long_snake_case_identifier_name',
                 'task-proj-1',
                 '2026-10-30-skillsync-r2.R2-self-improve',
                 '2026-10-30-r2-self-improve-long-plan-name-with-many-words.P1']
        for name in names:
            self.assertLessEqual(len(name), 80, name)
        self.assertEqual(self.ids(*names), set(names))

    def test_report_dir_id_is_masked_too(self):
        self.report_file('ghp_dirtoken123', 'a.md', 'Status: PASS\n')
        self.report_file('task-proj-1', 'a.md', 'Status: PASS\n')
        data = self.full()
        self.assertEqual({d['id'] for d in data['report_dirs']}, {'[REDACTED]', 'task-proj-1'})
        self.assert_absent('ghp_dirtoken123')

    def test_run_id_in_blocked_shape_is_masked(self):
        data = self.parsed(self.run_retro('--run-id', 'ghp_abc'), 2)
        self.assertEqual((set(data), data['run_id']), (BLOCKED_KEYS, '[REDACTED]'))
        self.assert_absent('ghp_abc')
        data = self.parsed(self.run_retro('--run-id', 'pre-' + TOKENS[0]), 2)
        self.assertEqual(data['run_id'], 'pre-[REDACTED]')
        self.assert_absent(TOKENS[0])

    def test_run_id_in_blocked_shape_stays_readable_when_valid(self):
        for value in ('task-proj-1', '123e4567-e89b-12d3-a456-426614174000', '-'.join(['abcdefgh'] * 9)):
            with self.subTest(value=value):
                self.assertEqual(self.parsed(self.run_retro('--run-id', value), 2)['run_id'], value)

    def test_scoped_report_masks_ids_but_scope_uses_the_raw_value(self):
        self.run_file('pre-ghp_secret99.P1', rec('a'))
        self.run_file('other', rec('a'))
        data = self.full('--run-id', 'pre-ghp_secret99')
        self.assertEqual([i['id'] for i in data['runs']], ['pre-[REDACTED].P1'])
        self.assert_absent('ghp_secret99')

    def test_token_in_invalid_name_is_not_printed(self):
        self.write('.skillsync/runs/ghp_secret99 bad.json', '{}')
        self.full(code=2)
        self.assert_absent('ghp_secret99')


class ReadOnly(RetroBase):
    """K6: retro niczego nie zapisuje (ścieżki, rozmiary, mtime przed i po)."""

    def populate(self):
        require_os_symlinks(self)
        require_fifo(self)
        proof = self.evidence('one.log')
        self.run_file('good.a', rec('a', 'FAIL', cause='x'), rec('a', evidence=[proof]))
        self.run_file('empty')
        self.runs_dir('lonely')
        self.report_file('good', 'r.md', 'Status: PASS\n')
        self.report_file('good', 'sub/r.log', 'log\n')
        self.report_file('good', 'big.md', b'#' * 1100000 + b'\nStatus: PASS\n')
        os.symlink(self.root / 'e.log', self.root / '.skillsync/runs/good/lnk.md')
        self.make_fifo('.skillsync/runs/good/pipe.md')
        self.write('.skillsync/runs/broken.json', '{')
        (self.root / '.skillsync/runs/linked-dir').symlink_to(self.base, target_is_directory=True)

    @needs_os_symlinks
    @needs_fifo
    def test_tree_is_unchanged_with_full_data(self):
        self.populate()
        before = snapshot(self.root)
        self.full(code=2, timeout=10)
        self.full('--run-id', 'good', timeout=10)
        self.parsed(self.run_retro('--run-id', 'absent', timeout=10), 2)
        self.assertEqual(snapshot(self.root), before)

    def test_tree_is_unchanged_without_runs_directory(self):
        self.write('plain.txt', 'x')
        before = snapshot(self.root)
        self.assertEqual(self.full()['status'], 'NOT_RUN')
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse((self.root / '.skillsync').exists())

    def test_tree_is_unchanged_with_skillsync_but_without_runs(self):
        self.write('.skillsync/other.txt', 'x')
        before = snapshot(self.root)
        self.full()
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse((self.root / '.skillsync/runs').exists())

    def test_tree_is_unchanged_after_error_exits(self):
        self.write('.skillsync/runs/x.json', '{}')
        before = snapshot(self.root)
        self.refused('--run-id', 'bad id')
        self.assertEqual(snapshot(self.root), before)

    def test_snapshot_would_notice_a_write(self):
        before = snapshot(self.root)
        self.write('.skillsync/runs/new.json', '{}')
        self.assertNotEqual(snapshot(self.root), before)


class ReportSignals(RetroBase):
    def signals(self, content, name='r.md', directory='rd'):
        self.report_file(directory, name, content)
        data = self.full()
        return {d['id']: d for d in data['report_dirs']}[directory]

    def test_status_forms_from_real_reports(self):
        entry = self.signals(
            '# Raport\n'
            'Status: **PASS**\n'
            'Status: **DONE z uwagami**\n'
            '**Werdykt: CHANGES_REQUIRED**\n'
            '**Status:** PASS\n'
            'Status: FAIL\n'
            'Werdykt: BLOCKED\n'
            '  Status: NOT_RUN\n'
            'Status: in_progress\n'
            'Werdykt: zatwierdzony\n'
            'Status: PASSED\n'
            'Nie status: PASS\n')
        self.assertEqual(entry['status_signals'], {'PASS': 2, 'FAIL': 1, 'BLOCKED': 1, 'NOT_RUN': 1,
                                                   'CHANGES_REQUIRED': 1, 'DONE': 1, 'unrecognized': 3})

    def test_signals_add_up_across_files_and_nested_directories(self):
        self.report_file('rd', 'a.md', 'Status: PASS\n')
        self.report_file('rd', 'deep/er/b.md', 'Status: PASS\nStatus: FAIL\n')
        entry = {d['id']: d for d in self.full()['report_dirs']}['rd']
        self.assertEqual((entry['status_signals']['PASS'], entry['status_signals']['FAIL']), (2, 1))

    def test_files_are_counted_by_extension(self):
        for name in ('a.md', 'b.md', 'x/c.log', 'd.diff', 'e.txt', 'f.json', 'noext'):
            self.report_file('rd', name, 'x\n')
        entry = {d['id']: d for d in self.full()['report_dirs']}['rd']
        self.assertEqual(entry['files'], {'md': 2, 'log': 1, 'diff': 1, 'other': 3})

    def test_only_markdown_is_scanned_for_signals(self):
        self.report_file('rd', 'a.log', 'Status: PASS\n')
        self.report_file('rd', 'b.txt', 'Status: FAIL\n')
        entry = {d['id']: d for d in self.full()['report_dirs']}['rd']
        self.assertEqual(sum(entry['status_signals'].values()), 0)

    @needs_os_symlinks
    def test_symlinks_are_not_followed(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'o.md').write_text('Status: PASS\nStatus: PASS\n')
        self.report_file('rd', 'a.md', 'Status: FAIL\n')
        (self.runs_dir('rd') / 'dirlink').symlink_to(outside, target_is_directory=True)
        (self.runs_dir('rd') / 'filelink.md').symlink_to(outside / 'o.md')
        entry = {d['id']: d for d in self.full()['report_dirs']}['rd']
        self.assertEqual((entry['status_signals']['PASS'], entry['status_signals']['FAIL']), (0, 1))

    @needs_os_symlinks
    def test_symlinked_report_directory_is_counted_not_read(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'o.md').write_text('Status: PASS\n')
        self.runs_dir()
        (self.root / '.skillsync/runs/lnk').symlink_to(outside, target_is_directory=True)
        data = self.full()
        self.assertEqual(data['report_dirs'], [])
        self.assertEqual((data['coverage']['symlinks_skipped'], data['coverage']['no_data']), (1, 0))

    @needs_fifo
    def test_fifo_in_report_directory_does_not_block(self):
        self.report_file('rd', 'ok.md', 'Status: PASS\n')
        self.make_fifo('.skillsync/runs/rd/pipe.md')
        data = self.full(timeout=8)
        self.assertEqual({d['id']: d for d in data['report_dirs']}['rd']['status_signals']['PASS'], 1)

    def test_binary_file_does_not_break_the_report(self):
        self.report_file('rd', 'bin.md', bytes(range(256)) * 50 + b'\xff\xfe\x00\nStatus: PASS\n')
        self.report_file('rd', 'ok.md', 'Status: FAIL\n')
        entry = {d['id']: d for d in self.full()['report_dirs']}['rd']
        self.assertEqual(entry['status_signals']['FAIL'], 1)
        self.assertFalse(entry['truncated'])

    def test_md_larger_than_one_mebibyte_is_read_only_up_to_the_limit(self):
        entry = self.signals(b'#' * 1048576 + b'\nStatus: PASS\n', name='big.md')
        self.assertEqual(entry['status_signals']['PASS'], 0)
        self.assertTrue(entry['truncated'])
        self.assertEqual(entry['files']['md'], 1)

    def test_small_md_is_not_truncated(self):
        entry = self.signals(b'Status: PASS\n' + b'#' * 500000)
        self.assertEqual((entry['status_signals']['PASS'], entry['truncated']), (1, False))

    def test_total_size_limit_gives_truncated(self):
        for index in range(65):
            with open(self.runs_dir('big') / f'f{index}.md', 'wb') as stream:
                stream.truncate(1048576)
        for index in range(10):
            with open(self.runs_dir('small') / f'f{index}.md', 'wb') as stream:
                stream.truncate(1048576)
        entries = {d['id']: d for d in self.full(timeout=30)['report_dirs']}
        self.assertTrue(entries['big']['truncated'])
        self.assertFalse(entries['small']['truncated'])

    def test_unopened_logs_do_not_count_toward_the_byte_limit(self):
        directory = self.runs_dir('logs')
        for index in range(65):
            with open(directory / f'a{index:02d}.log', 'wb') as stream:
                stream.truncate(1048576)
        (directory / 'z.md').write_text('Status: PASS\n')
        entry = {d['id']: d for d in self.full(timeout=30)['report_dirs']}['logs']
        self.assertFalse(entry['truncated'])
        self.assertEqual(entry['files']['log'], 65)
        self.assertEqual((entry['files']['md'], entry['status_signals']['PASS']), (1, 1))

    def test_file_count_limit_gives_truncated(self):
        directory = self.runs_dir('many')
        for index in range(5001):
            (directory / f'f{index}.txt').touch()
        few = self.runs_dir('few')
        for index in range(50):
            (few / f'f{index}.txt').touch()
        entries = {d['id']: d for d in self.full(timeout=30)['report_dirs']}
        self.assertTrue(entries['many']['truncated'])
        self.assertFalse(entries['few']['truncated'])

    def test_exactly_the_file_count_limit_is_not_truncated(self):
        directory = self.runs_dir('edge')
        for index in range(5000):
            (directory / f'f{index}.txt').touch()
        entry = {d['id']: d for d in self.full(timeout=30)['report_dirs']}['edge']
        self.assertFalse(entry['truncated'])

    def test_run_directory_with_dotted_run_files_is_visible_with_records(self):
        self.run_file('run.a', rec('a'))
        self.report_file('run', 'r.md', 'Status: PASS\n')
        data = self.full()
        self.assertEqual([(d['id'], d['has_records']) for d in data['report_dirs']], [('run', True)])
        self.assertEqual([i['id'] for i in data['runs']], ['run.a'])
        self.assertEqual(data['coverage']['no_data'], 0)

    def test_directory_without_run_file_has_no_records(self):
        self.report_file('lonely', 'r.md', 'Status: PASS\n')
        data = self.full()
        self.assertEqual([(d['id'], d['has_records']) for d in data['report_dirs']], [('lonely', False)])
        self.assertEqual([(i['id'], i['class']) for i in data['runs']], [('lonely', 'no_data')])

    def test_run_without_directory_has_no_entry_rather_than_zeros(self):
        self.run_file('solo', rec('a'))
        data = self.full()
        self.assertEqual(data['report_dirs'], [])
        self.assertEqual(data['status'], 'PASS')

    def test_dotted_directory_names_and_scope(self):
        self.runs_dir('v1')
        self.runs_dir('v1.2-x')
        self.run_file('v1.2-x.P1', rec('a'))
        data = self.full()
        self.assertEqual({d['id']: d['has_records'] for d in data['report_dirs']}, {'v1': False, 'v1.2-x': True})
        self.assertEqual({i['id']: i['class'] for i in data['runs']}, {'v1': 'no_data', 'v1.2-x.P1': 'clean'})

    def test_report_dirs_follow_run_id_scope_with_dots(self):
        for name in ('v1', 'v1.2-x', 'v1x', 'v2', 'v1.2-x.d'):
            self.runs_dir(name)
        self.report_file('v1.2', 'r.md', 'x')
        for name in ('v1.2-x.P1', 'v1', 'v1x', 'v2'):
            self.run_file(name, rec('a'))
        data = self.full('--run-id', 'v1.2-x')
        self.assertEqual({d['id'] for d in data['report_dirs']}, {'v1', 'v1.2-x', 'v1.2-x.d'})
        self.assertEqual({i['id'] for i in data['runs']}, {'v1.2-x.P1', 'v1.2-x.d'})

    def test_run_id_scope_selects_exact_and_dotted_runs_only(self):
        for name in ('v1', 'v1.2-x.P1', 'v1x', 'v2', 'v1.2'):
            self.run_file(name, rec('a'))
        self.assertEqual({i['id'] for i in self.full('--run-id', 'v1')['runs']}, {'v1', 'v1.2-x.P1', 'v1.2'})
        self.assertEqual({i['id'] for i in self.full('--run-id', 'v1x')['runs']}, {'v1x'})
        self.assertEqual({i['id'] for i in self.full('--run-id', 'v1.2-x.P1')['runs']}, {'v1.2-x.P1'})

    def test_scope_changes_coverage_to_the_scope(self):
        self.run_file('a', rec('a'))
        self.run_file('b', rec('a'))
        self.runs_dir('lonely-b')
        data = self.full('--run-id', 'a')
        self.assertEqual((data['coverage']['runs'], data['coverage']['with_records'], data['coverage']['no_data']),
                         (1, 1, 0))

    def test_directory_assignment_by_longest_name(self):
        self.report_file('x', 'a.md', 'Status: PASS\n')
        self.report_file('x.y', 'b.md', 'Status: FAIL\n')
        self.run_file('x.y.P1', rec('a'))
        data = self.full()
        self.assertEqual({d['id']: d['has_records'] for d in data['report_dirs']}, {'x': False, 'x.y': True})


class UnreadableFiles(RetroBase):
    def blocked(self, ident='good'):
        self.run_file(ident, rec('a'))
        data = self.full(code=2, timeout=10)
        self.assertEqual(data['status'], 'BLOCKED')
        self.assertIs(data['candidates_complete'], False)
        self.assertIn(ident, self.by_id(data))
        return data

    def test_malformed_file_in_scope(self):
        self.write('.skillsync/runs/broken.json', '{"schema_version": 1,')
        data = self.blocked()
        self.assertEqual(self.by_id(data)['broken'], {'id': 'broken', 'class': 'unreadable', 'reason': 'malformed'})
        self.assertEqual(data['coverage']['unreadable'], 1)
        self.assertEqual(data['coverage']['runs'], 2)

    def test_wrong_schema_is_malformed(self):
        self.write('.skillsync/runs/broken.json', json.dumps({'schema_version': 2, 'checkpoints': []}))
        self.assertEqual(self.by_id(self.blocked())['broken']['reason'], 'malformed')

    def test_invalid_record_is_malformed_and_never_clean(self):
        self.write('.skillsync/runs/broken.json', json.dumps(
            {'schema_version': 1, 'checkpoints': [{'step': 'a', 'status': 'PASS'}]}))
        item = self.by_id(self.blocked())['broken']
        self.assertEqual((item['class'], item['reason']), ('unreadable', 'malformed'))

    def test_absolute_evidence_path_in_file_is_malformed(self):
        self.write('.skillsync/runs/broken.json', json.dumps({'schema_version': 1, 'checkpoints': [
            rec('a', evidence=[{'path': '/etc/hostname', 'sha256': DIGEST}], final=True)]}))
        self.assertEqual(self.by_id(self.blocked())['broken']['reason'], 'malformed')

    def test_malformed_file_outside_scope(self):
        self.run_file('good', rec('a'))
        self.write('.skillsync/runs/broken.json', '{')
        data = self.full('--run-id', 'good')
        self.assertEqual(data['status'], 'PASS')
        self.assertEqual([i['id'] for i in data['runs']], ['good'])
        self.assertIs(data['candidates_complete'], False)
        self.assertEqual((data['coverage']['unreadable'], data['coverage']['unreadable_outside_scope']), (0, 1))
        self.assertEqual(data['coverage']['runs'], 1)

    def test_malformed_file_inside_dotted_scope_blocks(self):
        self.run_file('good', rec('a'))
        self.write('.skillsync/runs/good.bad.json', '{')
        data = self.full('--run-id', 'good', code=2)
        self.assertEqual(self.by_id(data)['good.bad']['class'], 'unreadable')
        self.assertEqual(data['coverage']['unreadable_outside_scope'], 0)

    def test_candidates_complete_is_true_when_everything_is_readable(self):
        self.run_file('good', rec('a'))
        self.assertIs(self.full()['candidates_complete'], True)

    def test_candidates_complete_does_not_follow_the_scope(self):
        self.run_file('good', rec('a'))
        self.run_file('other', rec('a'))
        self.assertIs(self.full('--run-id', 'good')['candidates_complete'], True)

    @needs_os_symlinks
    def test_symlinked_json_file(self):
        self.run_file('real', rec('a'))
        (self.root / '.skillsync/runs/lnk.json').symlink_to(self.root / '.skillsync/runs/real.json')
        data = self.full(code=2)
        self.assertEqual(self.by_id(data)['lnk'], {'id': 'lnk', 'class': 'unreadable', 'reason': 'symlink'})
        self.assertEqual(self.by_id(data)['real']['class'], 'clean')

    @needs_fifo
    def test_fifo_json_file_does_not_hang(self):
        self.make_fifo('.skillsync/runs/x.json')
        data = self.blocked_with_fifo()
        self.assertEqual(self.by_id(data)['x'], {'id': 'x', 'class': 'unreadable', 'reason': 'not_regular'})

    def blocked_with_fifo(self):
        self.run_file('good', rec('a'))
        data = self.full(code=2, timeout=8)
        self.assertIs(data['candidates_complete'], False)
        return data

    def test_directory_named_json(self):
        self.runs_dir('x.json')
        data = self.blocked()
        self.assertEqual(self.by_id(data)['x'], {'id': 'x', 'class': 'unreadable', 'reason': 'not_regular'})
        self.assertNotIn('no_data', {i['class'] for i in data['runs']})

    def test_json_nested_to_recursion_error(self):
        depth = 200000
        self.write('.skillsync/runs/x.json', '[' * depth + ']' * depth)
        item = self.by_id(self.blocked())['x']
        self.assertEqual((item['class'], set(item)), ('unreadable', UNREADABLE_KEYS))
        self.assertIn(item['reason'], REASONS)

    @unittest.skipIf(CHMOD_INEFFECTIVE, 'chmod 000 nie blokuje odczytu (root lub nie-POSIX)')
    def test_file_without_read_permission_is_io_error(self):
        self.write('.skillsync/runs/locked.json', '{}')
        path = self.root / '.skillsync/runs/locked.json'
        path.chmod(0)
        self.addCleanup(path.chmod, 0o644)
        self.assertEqual(self.by_id(self.blocked())['locked']['reason'], 'io_error')

    @needs_os_symlinks
    def test_reasons_come_from_the_fixed_list(self):
        self.write('.skillsync/runs/malformed.json', '{')
        self.runs_dir('dir.json')
        self.write('.skillsync/runs/bad name.json', '{}')
        (self.root / '.skillsync/runs/lnk.json').symlink_to(self.root / '.skillsync/runs/malformed.json')
        data = self.blocked()
        reasons = {i['reason'] for i in data['runs'] if i['class'] == 'unreadable'}
        self.assertEqual(reasons, {'malformed', 'not_regular', 'invalid_name', 'symlink'})

    def test_coverage_runs_is_the_sum_of_three_counters(self):
        self.run_file('good', rec('a'))
        self.run_file('empty')
        self.runs_dir('lonely')
        self.write('.skillsync/runs/broken.json', '{')
        data = self.blocked()
        cov = data['coverage']
        self.assertEqual((cov['with_records'], cov['no_data'], cov['unreadable'], cov['runs']), (1, 2, 1, 4))

    def test_unreadable_file_makes_no_data_directory_not_clean(self):
        self.runs_dir('x')
        self.write('.skillsync/runs/x.json', '{')
        data = self.full(code=2)
        self.assertEqual(self.by_id(data)['x']['class'], 'unreadable')


# ---------------------------------------------------------------------------------------------------------------
# Krok 3.1: kandydaci zmiany (K4, K5/D61), koniec do końca i worktree (K11)
# ---------------------------------------------------------------------------------------------------------------
CANDIDATE_KEYS = {'cause_key', 'status', 'decision', 'missing', 'runs', 'records', 'owner', 'owners_seen',
                  'failing_case', 'cause_sha256', 'proposal'}
CASE_KEYS = {'run', 'step', 'path', 'sha256', 'check'}
OWNER_VALUES = ('instruction', 'skill', 'hook', 'route', 'test')
LEAK = 'LEAKMARK'


class CandidateBase(RetroBase):
    """Fabryka rekordów z kluczem przyczyny i dowodem; wynik `cands()` ma sprawdzoną zamkniętą listę pól."""

    def case(self, ident, key, owner='test', step=None, cause='some cause text', state='verified', status='FAIL',
             rel=None, **fields):
        """Rekord problemu z `cause_key`. `state` opisuje dowód: verified, changed, missing, refused, none."""
        step = step or 'step-' + key
        rel = rel or f'.skillsync/evidence/{ident}-{key}.log'
        content = f'failure of {ident}/{key}\n'
        evidence = []
        if state != 'none':
            if state == 'missing':
                digest = sha(content)
            elif state == 'refused':
                require_os_symlinks(self)
                self.write(rel + '.real', content)
                link = self.root / rel
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(self.root / (rel + '.real'))
                digest = sha(content)
            else:
                self.write(rel, content)
                digest = sha(content) if state == 'verified' else sha('older content\n')
            evidence = [{'path': rel, 'sha256': digest}]
        extra = {'cause_key': key, **({'owner': owner} if owner else {}), **fields}
        return rec(step, status, cause=cause, evidence=evidence, nxt=LEAK + '-next', **extra)

    def pair(self, key='flaky', first='x', second='y', **kw):
        """Dwa przebiegi z jedną porażką tego samego klucza."""
        self.run_file(first, self.case(first, key, **kw), rec('done'))
        self.run_file(second, self.case(second, key, **kw), rec('done'))

    def cands(self, *args, **kw):
        data = self.full(*args, **kw)
        for item in data['candidates']:
            self.assertEqual(set(item), CANDIDATE_KEYS)
            if item['failing_case'] is not None:
                self.assertEqual(set(item['failing_case']), CASE_KEYS)
            if item['decision'] is not None:
                self.assertEqual(set(item['decision']), DECISION_KEYS)
        return data['candidates']

    def one(self, *args, **kw):
        found = self.cands(*args, **kw)
        self.assertEqual(len(found), 1, found)
        return found[0]


class CandidateThreshold(CandidateBase):
    def test_one_run_is_below_the_threshold(self):
        self.run_file('x', self.case('x', 'flaky'), rec('done'))
        self.assertEqual(self.cands(), [])

    def test_two_runs_make_a_candidate(self):
        self.pair()
        item = self.one()
        self.assertEqual((item['cause_key'], item['runs'], item['records']), ('flaky', 2, 2))

    def test_many_failures_in_one_run_do_not_cross_the_threshold(self):
        records = [self.case('x', 'flaky', step=f's{i}') for i in range(3)]
        self.run_file('x', *records, rec('done'))
        self.assertEqual(self.cands(), [])

    def test_attempt_suffix_is_the_same_logical_run(self):
        self.pair(first='x', second='x.2')
        data = self.full()
        self.assertEqual(sorted(self.by_id(data)), ['x', 'x.2'], 'oba pliki są przebiegami w runs[]')
        self.assertEqual(data['candidates'], [])

    def test_different_runs_x_and_y_count_twice(self):
        self.pair(first='x', second='y')
        self.assertEqual(self.one()['runs'], 2)

    def test_retry_suffix_does_not_inflate_the_count(self):
        for ident in ('x', 'x.2', 'y'):
            self.run_file(ident, self.case(ident, 'flaky'), rec('done'))
        item = self.one()
        self.assertEqual(item['runs'], 2)
        self.assertEqual(item['records'], 3, 'rekordy liczone wszystkie, przebiegi po tożsamości logicznej')

    def test_non_numeric_dotted_suffix_is_a_different_run(self):
        self.pair(first='r.P1', second='r.P2')
        self.assertEqual(self.one()['runs'], 2)

    def test_attempt_suffix_after_a_package_suffix_is_the_same_run(self):
        self.pair(first='r.P1', second='r.P1.2')
        self.assertEqual(self.cands(), [])

    def test_keys_are_grouped_separately(self):
        self.run_file('x', self.case('x', 'alpha'), rec('done'))
        self.run_file('y', self.case('y', 'beta'), rec('done'))
        self.assertEqual(self.cands(), [])

    def test_plain_pass_with_a_cause_key_is_ignored(self):
        self.run_file('x', self.case('x', 'flaky'), rec('done'))
        self.run_file('y', rec('a', cause_key='flaky', owner='test'))
        self.assertEqual(self.cands(), [])

    def test_key_on_a_passing_retry_counts(self):
        self.run_file('x', self.case('x', 'flaky'), rec('done'))
        self.run_file('y', rec('a', attempt=2, cause_key='flaky', owner='test'))
        item = self.one()
        self.assertEqual((item['runs'], item['records']), (2, 2))
        self.assertEqual(item['failing_case']['run'], 'x')

    def test_key_on_a_repeated_step_counts(self):
        self.run_file('x', self.case('x', 'flaky'), rec('done'))
        self.run_file('y', rec('a'), rec('a', cause_key='flaky'))
        self.assertEqual(self.one()['runs'], 2)

    def test_key_on_not_run_and_blocked_counts(self):
        for status in ('NOT_RUN', 'BLOCKED'):
            with self.subTest(status=status):
                shutil.rmtree(self.root / '.skillsync', ignore_errors=True)
                self.run_file('x', self.case('x', 'flaky'), rec('done'))
                self.run_file('y', self.case('y', 'flaky', status=status, state='none'), rec('done'))
                self.assertEqual(self.one()['runs'], 2)

    def test_run_id_does_not_narrow_candidates(self):
        self.pair()
        data = self.full('--run-id', 'x')
        self.assertEqual([i['id'] for i in data['runs']], ['x'])
        self.assertEqual([(c['cause_key'], c['runs']) for c in data['candidates']], [('flaky', 2)])

    def test_scope_without_the_key_still_sees_the_candidate(self):
        self.pair()
        self.run_file('z', rec('a'))
        data = self.full('--run-id', 'z')
        self.assertEqual([(c['cause_key'], c['runs']) for c in data['candidates']], [('flaky', 2)])


class CandidateOwnerAndCase(CandidateBase):
    def test_exactly_one_owner_gives_ready(self):
        self.pair(owner='skill')
        item = self.one()
        self.assertEqual((item['owner'], item['owners_seen'], item['status'], item['missing']),
                         ('skill', ['skill'], 'ready', []))

    def test_one_owner_in_a_group_of_records_is_enough(self):
        self.run_file('x', self.case('x', 'flaky', owner='hook'), rec('done'))
        self.run_file('y', self.case('y', 'flaky', owner=None), rec('done'))
        item = self.one()
        self.assertEqual((item['owner'], item['owners_seen'], item['status']), ('hook', ['hook'], 'ready'))

    def test_zero_owners_needs_owner(self):
        self.pair(owner=None)
        item = self.one()
        self.assertEqual((item['owner'], item['owners_seen'], item['status'], item['missing']),
                         (None, [], 'needs_owner', ['owner']))
        self.assertIsNotNone(item['failing_case'], 'dowód jest, brakuje tylko właściciela')

    def test_two_owners_are_not_guessed(self):
        self.run_file('x', self.case('x', 'flaky', owner='test'), rec('done'))
        self.run_file('y', self.case('y', 'flaky', owner='hook'), rec('done'))
        item = self.one()
        self.assertIsNone(item['owner'])
        self.assertEqual(sorted(item['owners_seen']), ['hook', 'test'])
        self.assertEqual((item['status'], item['missing']), ('needs_owner', ['owner']))

    def test_same_owner_repeated_is_one_owner(self):
        self.run_file('x', self.case('x', 'flaky', owner='test', step='s1'),
                      self.case('x', 'flaky', owner='test', step='s2'), rec('done'))
        self.run_file('y', self.case('y', 'flaky', owner='test'), rec('done'))
        item = self.one()
        self.assertEqual((item['owner'], item['owners_seen']), ('test', ['test']))

    def test_every_allowed_owner_is_accepted(self):
        for owner in OWNER_VALUES:
            with self.subTest(owner=owner):
                shutil.rmtree(self.root / '.skillsync', ignore_errors=True)
                self.pair(owner=owner)
                self.assertEqual(self.one()['owner'], owner)

    def test_fail_without_evidence_needs_case(self):
        self.pair(state='none')
        item = self.one()
        self.assertEqual((item['status'], item['missing'], item['failing_case']), ('needs_case', ['case'], None))
        self.assertEqual(item['owner'], 'test')

    @needs_os_symlinks
    def test_unverified_evidence_states_need_a_case(self):
        for state in ('changed', 'missing', 'refused'):
            with self.subTest(state=state):
                shutil.rmtree(self.root / '.skillsync', ignore_errors=True)
                self.pair(state=state)
                item = self.one()
                self.assertEqual((item['status'], item['missing'], item['failing_case']),
                                 ('needs_case', ['case'], None))

    @needs_os_symlinks
    def test_evidence_that_is_a_symlink_inside_the_project_is_refused_not_a_case(self):
        self.pair(state='refused')
        self.assertEqual(self.one()['failing_case'], None)
        # ten sam stan w rubryce: dowód PASS będący dowiązaniem w projekcie jest `refused`
        self.write('.skillsync/evidence/real.log', 'proof\n')
        (self.root / '.skillsync/evidence/lnk.log').symlink_to(self.root / '.skillsync/evidence/real.log')
        self.run_file('p', rec('a', evidence=[{'path': '.skillsync/evidence/lnk.log', 'sha256': sha('proof\n')}]))
        evidence = self.by_id(self.full())['p']['rubric']['evidence']
        self.assertEqual((evidence['status'], evidence['refused'], evidence['verified']), ('partial', 1, 0))

    def test_owner_and_case_both_missing(self):
        self.pair(owner=None, state='none')
        item = self.one()
        self.assertNotEqual(item['status'], 'ready')
        self.assertEqual(sorted(item['missing']), ['case', 'owner'])
        self.assertIsNone(item['owner'])
        self.assertIsNone(item['failing_case'])

    def test_second_verified_failure_is_enough_for_a_case(self):
        self.run_file('x', self.case('x', 'flaky', state='changed'), rec('done'))
        self.run_file('y', self.case('y', 'flaky'), rec('done'))
        item = self.one()
        self.assertEqual((item['status'], item['missing']), ('ready', []))
        self.assertEqual(item['failing_case']['run'], 'y')

    def test_evidence_of_a_pass_record_is_not_a_failing_case(self):
        self.run_file('x', self.case('x', 'flaky', state='none'), rec('done'))
        self.run_file('y', rec('a', attempt=2, cause_key='flaky', owner='test'))
        item = self.one()
        self.assertEqual((item['status'], item['failing_case']), ('needs_case', None))

    def test_ready_only_without_gaps(self):
        self.pair()
        item = self.one()
        self.assertEqual((item['status'], item['missing']), ('ready', []))
        self.assertIsNotNone(item['failing_case'])
        self.assertEqual(item['failing_case']['check'], 'verified')

    def test_missing_is_a_list(self):
        for kwargs in ({'owner': None}, {'state': 'none'}, {}):
            with self.subTest(**kwargs):
                shutil.rmtree(self.root / '.skillsync', ignore_errors=True)
                self.pair(**kwargs)
                self.assertIsInstance(self.one()['missing'], list)


class CandidateCaseSelection(CandidateBase):
    def test_failing_case_fields(self):
        self.pair()
        content = 'failure of x/flaky\n'
        self.assertEqual(self.one()['failing_case'],
                         {'run': 'x', 'step': 'step-flaky', 'path': '.skillsync/evidence/x-flaky.log',
                          'sha256': sha(content), 'check': 'verified'})

    def test_first_by_raw_run_id_not_by_file_creation(self):
        self.run_file('run-b', self.case('run-b', 'flaky'), rec('done'))
        self.run_file('run-a', self.case('run-a', 'flaky'), rec('done'))
        self.assertEqual(self.one()['failing_case']['run'], 'run-a')

    def test_first_by_record_order_within_a_run(self):
        self.run_file('x', self.case('x', 'flaky', step='z-step', rel='.skillsync/evidence/z.log'),
                      self.case('x', 'flaky', step='a-step', rel='.skillsync/evidence/a.log'), rec('done'))
        self.run_file('y', self.case('y', 'flaky'), rec('done'))
        case = self.one()['failing_case']
        self.assertEqual((case['run'], case['step']), ('x', 'z-step'))

    def test_unverified_record_is_skipped_in_a_run(self):
        self.run_file('x', self.case('x', 'flaky', step='first', state='changed', rel='.skillsync/evidence/c.log'),
                      self.case('x', 'flaky', step='second', rel='.skillsync/evidence/v.log'), rec('done'))
        self.run_file('y', self.case('y', 'flaky'), rec('done'))
        self.assertEqual(self.one()['failing_case']['step'], 'second')

    def test_second_evidence_file_of_a_record_can_be_the_case(self):
        content = 'second proof\n'
        self.write('.skillsync/evidence/second.log', content)
        self.write('.skillsync/evidence/first.log', 'new\n')
        evidence = [{'path': '.skillsync/evidence/first.log', 'sha256': sha('old\n')},
                    {'path': '.skillsync/evidence/second.log', 'sha256': sha(content)}]
        record = rec('s', 'FAIL', cause='c', evidence=evidence, cause_key='flaky', owner='test')
        self.run_file('x', record, rec('done'))
        self.run_file('y', self.case('y', 'flaky'), rec('done'))
        case = self.one()['failing_case']
        self.assertEqual((case['run'], case['path']), ('x', '.skillsync/evidence/second.log'))


class CandidateDecisions(CandidateBase):
    """Rejestr decyzji `docs/retro/decisions.yaml`: `decided`, `recurred`, kolejność i niepoprawny rejestr."""

    def registry(self, *items, raw=None):
        lines = ['version: 1', 'decisions:']
        for key, value, day, *extra in items:
            lines += [f'  - cause_key: {key}', f'    decision: {value}', f'    date: {day}', *extra]
        self.write('docs/retro/decisions.yaml', raw if raw is not None else '\n'.join(lines) + '\n')

    def test_without_registry_nothing_changes(self):
        self.pair()
        data = self.full()
        self.assertEqual(data['decisions'], {'status': 'absent', 'path': 'docs/retro/decisions.yaml', 'count': 0})
        self.assertEqual((data['candidates'][0]['status'], data['candidates'][0]['decision']), ('ready', None))

    def test_decision_without_later_records_is_decided(self):
        self.pair()  # rekordy z 2026-10-29
        self.registry(('flaky', 'approved', '2026-10-29', '    owner: skill', '    case: tests/x.py', '    ref: abc1234'))
        item = self.one()
        self.assertEqual(item['status'], 'decided')
        self.assertEqual(item['decision'], {'decision': 'approved', 'date': '2026-10-29', 'records_after': 0,
                                        'fixed': True})

    def test_recorded_at_at_the_edge_of_the_date_range_is_ignored_without_a_traceback(self):
        for label, value in (('min', '0001-01-01T00:00:00+01:00'), ('max', '9999-12-31T23:59:59-01:00')):
            with self.subTest(label):
                self.pair()
                self.run_file('z', self.case('z', 'flaky', recorded_at=value), rec('done'))
                self.registry(('flaky', 'approved', '2026-10-29'))
                self.assertEqual(self.one()['status'], 'decided')  # data poza zakresem nie liczy się jako późniejsza

    def test_decision_day_itself_does_not_count_as_recurrence(self):
        self.pair()
        self.run_file('z', self.case('z', 'flaky', recorded_at='2026-10-29T23:59:00+00:00'), rec('done'))
        self.registry(('flaky', 'approved', '2026-10-29'))
        self.assertEqual(self.one()['status'], 'decided')

    def test_problem_after_decision_day_is_recurred(self):
        self.pair()
        self.run_file('z', self.case('z', 'flaky', recorded_at='2026-10-30T00:30:00+02:00'), rec('done'))
        self.registry(('flaky', 'approved', '2026-10-28'))
        item = self.one()
        self.assertEqual(item['status'], 'recurred')
        self.assertEqual(item['decision']['records_after'], 3)

    def test_utc_date_decides_not_local_offset(self):
        self.pair()
        # 2026-10-30T00:30+02:00 to 2026-10-29T22:30Z: ten sam dzień co decyzja, więc bez nawrotu.
        self.run_file('z', self.case('z', 'flaky', recorded_at='2026-10-30T00:30:00+02:00'), rec('done'))
        self.registry(('flaky', 'rejected', '2026-10-29'))
        item = self.one()
        self.assertEqual((item['status'], item['decision']['decision']), ('decided', 'rejected'))

    def test_order_recurred_first_decided_last(self):
        self.pair(key='a-ready', first='a1', second='a2')
        self.pair(key='b-decided', first='b1', second='b2')
        self.pair(key='c-recurred', first='c1', second='c2')
        self.run_file('z', self.case('z', 'c-recurred', recorded_at='2026-11-02T10:00:00+00:00'), rec('done'))
        self.registry(('b-decided', 'approved', '2026-10-30'), ('c-recurred', 'approved', '2026-10-30'))
        self.assertEqual([c['cause_key'] for c in self.cands()], ['c-recurred', 'a-ready', 'b-decided'])
        self.assertEqual(self.full()['decisions']['count'], 2)

    def test_registry_details_stay_out_of_the_output(self):
        self.pair()
        self.registry(('flaky', 'approved', '2026-10-29', f'    ref: {LEAK}-ref', f'    case: {LEAK}-case'))
        self.full()
        self.assertNotIn(LEAK + '-ref', self.text)
        self.assertNotIn(LEAK + '-case', self.text)

    def test_unknown_key_in_registry_is_ignored_for_candidates_below_threshold(self):
        self.run_file('x', self.case('x', 'flaky'), rec('done'))
        self.registry(('other', 'approved', '2026-10-29'))
        data = self.full()
        self.assertEqual((data['candidates'], data['decisions']['status']), ([], 'available'))

    def assert_invalid(self, **kw):
        self.pair()
        self.registry(**kw)
        data = self.full(code=2)
        self.assertEqual((data['status'], data['decisions']['status'], data['decisions']['count']),
                         ('BLOCKED', 'invalid', 0))
        self.assertEqual([c['status'] for c in data['candidates']], ['ready'], 'raport powstaje bez decyzji')
        self.assertIsNone(data['candidates'][0]['decision'])

    def test_invalid_yaml_blocks(self):
        self.assert_invalid(raw='version: 1\ndecisions: [\n')

    def test_wrong_version_blocks(self):
        self.assert_invalid(raw='version: 2\ndecisions: []\n')

    def test_unknown_decision_value_blocks(self):
        self.assert_invalid(raw='version: 1\ndecisions:\n  - {cause_key: flaky, decision: maybe, date: 2026-10-29}\n')

    def test_bad_date_blocks(self):
        self.assert_invalid(raw='version: 1\ndecisions:\n  - {cause_key: flaky, decision: approved, date: soon}\n')

    def test_datetime_instead_of_date_blocks(self):
        self.assert_invalid(
            raw='version: 1\ndecisions:\n  - {cause_key: flaky, decision: approved, date: 2026-10-29T10:00:00Z}\n')

    def test_duplicate_key_blocks(self):
        self.assert_invalid(raw='version: 1\ndecisions:\n  - {cause_key: flaky, decision: approved, date: 2026-10-29}\n'
                                '  - {cause_key: flaky, decision: rejected, date: 2026-10-30}\n')

    def test_unknown_field_blocks(self):
        self.assert_invalid(
            raw='version: 1\ndecisions:\n  - {cause_key: flaky, decision: approved, date: 2026-10-29, why: x}\n')

    @needs_os_symlinks
    def test_symlinked_registry_blocks(self):
        self.write('elsewhere.yaml', 'version: 1\ndecisions: []\n')
        link = self.root / 'docs/retro/decisions.yaml'
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(self.root / 'elsewhere.yaml')
        self.pair()
        self.assertEqual(self.full(code=2)['decisions']['status'], 'invalid')

    def test_empty_or_null_decisions_are_available(self):
        for raw in ('version: 1\ndecisions: []\n', 'version: 1\ndecisions:\n', 'version: 1\n'):
            with self.subTest(raw=raw):
                self.pair()
                self.registry(raw=raw)
                data = self.full()
                self.assertEqual((data['decisions']['status'], data['candidates'][0]['status']), ('available', 'ready'))

    def test_quoted_date_is_accepted(self):
        self.pair()
        self.registry(raw='version: 1\ndecisions:\n  - {cause_key: flaky, decision: approved, date: "2026-10-29"}\n')
        self.assertEqual(self.one()['status'], 'decided')

    def test_compact_date_blocks(self):
        self.assert_invalid(raw='version: 1\ndecisions:\n  - {cause_key: flaky, decision: approved, date: "20261029"}\n')

    def test_boolean_version_blocks(self):
        self.assert_invalid(raw='version: true\ndecisions: []\n')

    def test_scalar_decisions_block(self):
        for value in ('0', 'false', 'text'):
            with self.subTest(value=value):
                self.assert_invalid(raw=f'version: 1\ndecisions: {value}\n')

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'FIFO wymaga POSIX')
    def test_fifo_instead_of_registry_blocks_without_opening(self):
        path = self.root / 'docs/retro/decisions.yaml'
        path.parent.mkdir(parents=True, exist_ok=True)
        os.mkfifo(path)
        self.pair()
        self.assertEqual(self.full(code=2)['decisions']['status'], 'invalid')

    def test_directory_instead_of_registry_blocks(self):
        (self.root / 'docs/retro/decisions.yaml').mkdir(parents=True)
        self.pair()
        self.assertEqual(self.full(code=2)['decisions']['status'], 'invalid')

    def test_oversized_registry_blocks(self):
        self.assert_invalid(raw='version: 1\ndecisions: []\n' + '#' * (1024 * 1024) + '\n')

    def test_registry_is_not_modified(self):
        self.pair()
        self.registry(('flaky', 'approved', '2026-10-29'))
        path = self.root / 'docs/retro/decisions.yaml'
        before = path.read_bytes()
        self.full()
        self.assertEqual(path.read_bytes(), before)


class CandidateOrderAndText(CandidateBase):
    def build(self):
        layout = {'ccc': (['r1', 'r2', 'r3'], 'test', 'verified'),
                  'bbb': (['r1', 'r2'], 'test', 'verified'),
                  'ddd': (['r1', 'r2'], 'test', 'verified'),
                  'aaa': (['r1', 'r2', 'r3'], None, 'verified'),
                  'yyy': (['r1', 'r2'], None, 'verified')}
        per_run = {}
        for key, (idents, owner, state) in layout.items():
            for ident in idents:
                per_run.setdefault(ident, []).append(self.case(ident, key, owner=owner, state=state))
        for ident, records in per_run.items():
            self.run_file(ident, *records, rec('done'))

    def test_ready_first_then_runs_descending_then_key(self):
        self.build()
        found = self.cands()
        self.assertEqual([(c['cause_key'], c['status'], c['runs']) for c in found],
                         [('ccc', 'ready', 3), ('bbb', 'ready', 2), ('ddd', 'ready', 2),
                          ('aaa', 'needs_owner', 3), ('yyy', 'needs_owner', 2)])

    def test_order_is_deterministic_across_runs(self):
        self.build()
        first = self.run_retro()
        second = self.run_retro()
        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stdout, second.stdout)

    def test_same_runs_and_status_are_sorted_by_key(self):
        for ident in ('x', 'y'):
            self.run_file(ident, *[self.case(ident, k) for k in ('mmm', 'bbb', 'zzz', 'aaa')], rec('done'))
        self.assertEqual([c['cause_key'] for c in self.cands()], ['aaa', 'bbb', 'mmm', 'zzz'])

    def test_cause_hash_is_twelve_sorted_hex_and_text_never_appears(self):
        self.run_file('x', self.case('x', 'flaky', cause=LEAK + ' zeta cause'), rec('done'))
        self.run_file('y', self.case('y', 'flaky', cause=LEAK + ' alpha cause'), rec('done'))
        item = self.one()
        expected = sorted(sha(LEAK + ' ' + word + ' cause')[:12] for word in ('zeta', 'alpha'))
        self.assertEqual(item['cause_sha256'], expected)
        for digest in item['cause_sha256']:
            self.assertRegex(digest, r'^[0-9a-f]{12}$')
        self.assert_absent(LEAK, 'zeta cause', 'alpha cause')

    def test_cause_hash_is_deterministic(self):
        self.run_file('x', self.case('x', 'flaky', cause='one cause'), rec('done'))
        self.run_file('y', self.case('y', 'flaky', cause='two cause'), rec('done'))
        first = self.one()['cause_sha256']
        self.assertEqual(first, self.one()['cause_sha256'])
        self.assertEqual(first, sorted(sha(c)[:12] for c in ('one cause', 'two cause')))

    def test_proposal_is_constant_and_names_the_approver_by_role(self):
        self.pair(key='alpha')
        for ident in ('x', 'y'):
            self.run_file(ident, self.case(ident, 'alpha'), self.case(ident, 'beta', owner=None), rec('done'))
        found = self.cands()
        self.assertEqual(len(found), 2)
        proposals = {c['proposal'] for c in found}
        self.assertEqual(len(proposals), 1)
        text = proposals.pop()
        self.assertIn('użytkownika', text)
        self.assertNotIn(PERSON, text)

    def test_free_text_never_reaches_the_output(self):
        self.pair(cause=LEAK + ' secret cause text')
        self.one()
        self.assert_absent(LEAK, 'secret cause text')

    def test_candidate_fields_are_closed_for_every_status(self):
        self.build()
        self.run_file('q1', self.case('q1', 'nocase', state='none'), rec('done'))
        self.run_file('q2', self.case('q2', 'nocase', state='none'), rec('done'))
        found = self.cands()
        self.assertEqual({c['status'] for c in found}, {'ready', 'needs_owner', 'needs_case'})

    def test_candidates_are_part_of_a_blocked_report_when_a_file_is_unreadable(self):
        self.pair()
        self.write('.skillsync/runs/broken.json', '{')
        data = self.full(code=2)
        self.assertEqual(len(data['candidates']), 1)
        self.assertIs(data['candidates_complete'], False)


class CandidateTokenMasking(CandidateBase):
    """K5 / D61: pola identyfikacyjne `failing_case` w kandydacie `ready` są maskowane, `sha256` nie."""

    def masked_pair(self, token, other):
        digest = sha(f'failure of {token}\n')
        for name in (token, other):
            self.write(f'.skillsync/evidence/{name}.log', f'failure of {name}\n')
        records = lambda ident: [rec(token, 'FAIL', cause='c', cause_key='flaky', owner='test', nxt='n',
                                     evidence=[{'path': f'.skillsync/evidence/{ident}.log',
                                                'sha256': sha(f'failure of {ident}\n')}]), rec('done')]
        self.run_file(token, *records(token))
        self.run_file(other, *records(other))
        return digest

    def test_ghp_token_in_run_step_and_path_is_masked(self):
        token, other = 'ghp_aaaa1111AAAA', 'ghp_bbbb2222BBBB'
        digest = self.masked_pair(token, other)
        item = self.one()
        self.assertEqual(item['status'], 'ready')
        self.assertEqual(item['runs'], 2, 'grupowanie przed maskowaniem: dwa różne identyfikatory to dwa przebiegi')
        case = item['failing_case']
        self.assertEqual(case['run'], '[REDACTED]')
        self.assertEqual(case['step'], '[REDACTED]')
        self.assertEqual(case['path'], '.skillsync/evidence/[REDACTED].log')
        self.assertEqual(case['sha256'], digest, 'skrót nie jest maskowany')
        self.assert_absent(token, other, 'aaaa1111AAAA', 'bbbb2222BBBB')

    def test_sk_ant_token_in_run_step_and_path_is_masked(self):
        token, other = 'sk-ant-api03aaaa1111', 'sk-ant-api03bbbb2222'
        digest = self.masked_pair(token, other)
        item = self.one()
        self.assertEqual(item['runs'], 2)
        case = item['failing_case']
        self.assertEqual((case['run'], case['step']), ('[REDACTED]', '[REDACTED]'))
        self.assertEqual(case['path'], '.skillsync/evidence/[REDACTED].log')
        self.assertEqual(case['sha256'], digest)
        self.assert_absent(token, other, 'api03aaaa1111', 'api03bbbb2222')

    def test_token_prefix_inside_a_longer_id_is_masked_but_the_rest_stays(self):
        self.masked_pair('run-ghp_aaaa1111AAAA', 'run-ghp_bbbb2222BBBB')
        case = self.one()['failing_case']
        self.assertEqual((case['run'], case['step']), ('run-[REDACTED]', 'run-[REDACTED]'))
        self.assert_absent('aaaa1111AAAA')

    def test_token_in_cause_key_is_masked_in_a_candidate(self):
        key = 'sk-ant-api03abc123'
        self.pair(key=key, state='verified')
        item = self.one()
        self.assertEqual(item['cause_key'], '[REDACTED]')
        self.assert_absent(key, 'api03abc123')

    def test_token_in_a_secret_evidence_path_directory_segment(self):
        token = 'ghp_dirTOKEN123456'
        rel = f'.skillsync/{token}/x.log'
        self.run_file('x', self.case('x', 'flaky', rel=rel), rec('done'))
        self.run_file('y', self.case('y', 'flaky'), rec('done'))
        self.assertEqual(self.one()['failing_case']['path'], '.skillsync/[REDACTED]/x.log')
        self.assert_absent(token)

    def test_valid_values_stay_readable(self):
        hexdir = 'a1b2c3d4' * 4
        run_a, run_b = 'a_long_snake_case_run_identifier_over_32', 'run-b'
        step = 'step_with_a_long_snake_case_name_here'
        log = f'.skillsync/logs/{hexdir}/x.log'
        long_rel = 'docs/path_with_long_snake_case_name_over_thirty_two_chars.log'
        self.assertGreaterEqual(len(step), 32)
        self.assertGreaterEqual(len(run_a), 32)
        for ident in (run_a, run_b):
            self.run_file(ident,
                          self.case(ident, 'logged', step=step, rel=log if ident == run_a else None),
                          self.case(ident, 'snaked', step=step + '_2', rel=long_rel if ident == run_a else None),
                          rec('done'))
        found = {c['cause_key']: c for c in self.cands()}
        self.assertEqual(found['logged']['failing_case']['run'], run_a)
        self.assertEqual(found['logged']['failing_case']['step'], step)
        self.assertEqual(found['logged']['failing_case']['path'], log)
        self.assertEqual(found['snaked']['failing_case']['step'], step + '_2')
        self.assertEqual(found['snaked']['failing_case']['path'], long_rel)
        self.assertNotIn('[REDACTED]', self.text)

    def test_long_hyphenated_ids_stay_readable(self):
        long36 = '-'.join(['abcdefg'] * 5)[:36].rstrip('-')
        long80 = '-'.join(['abcdefghi'] * 8)[:80].rstrip('-')
        self.assertGreaterEqual(len(long36), 35)
        for ident in (long36, long80):
            self.run_file(ident, self.case(ident, 'flaky', step='task-proj-1'), rec('done'))
        case = self.one()['failing_case']
        self.assertEqual((case['run'], case['step']), (min(long36, long80), 'task-proj-1'))
        self.assertNotIn('[REDACTED]', self.text)


# ---------------------------------------------------------------------------------------------------------------
# Koniec do końca: prawdziwe `checkpoint`, potem `retro`
# ---------------------------------------------------------------------------------------------------------------
class EndToEnd(RetroBase):
    def setUp(self):
        super().setUp()
        self.init_git()

    def checkpoint(self, run, step, status, *args):
        command = [sys.executable, str(CLI), 'checkpoint', str(self.root), '--run-id', run, '--step', step,
                   '--status', status, '--next', 'n', *map(str, args), '--json']
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30, cwd=self.root)
        except subprocess.TimeoutExpired:
            self.fail('checkpoint zawisł')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def failing_run(self, run, cause):
        self.write(f'fail-{run}.log', f'failure in {run}\n')
        self.checkpoint(run, 'build', 'FAIL', '--evidence', f'fail-{run}.log', '--cause', cause,
                        '--cause-key', 'smoke-flaky', '--owner', 'test')
        self.checkpoint(run, 'build', 'PASS', '--attempt', 2, '--evidence', 'e.log')
        self.checkpoint(run, 'wrap', 'PASS', '--evidence', 'e.log', '--final')

    def test_fail_then_retry_then_final_in_two_runs_gives_a_ready_candidate(self):
        self.failing_run('e2e-a', LEAK + ' first flaky cause')
        self.failing_run('e2e-b', LEAK + ' second flaky cause')
        data = self.full()
        self.assertEqual(data['status'], 'PASS')
        self.assertEqual({i['id']: i['class'] for i in data['runs']}, {'e2e-a': 'recovery', 'e2e-b': 'recovery'})
        item = self.one_candidate(data)
        self.assertEqual((item['cause_key'], item['status'], item['owner'], item['runs'], item['missing']),
                         ('smoke-flaky', 'ready', 'test', 2, []))
        self.assertEqual(item['owners_seen'], ['test'])
        self.assertEqual(item['failing_case'], {'run': 'e2e-a', 'step': 'build', 'path': 'fail-e2e-a.log',
                                                'sha256': sha('failure in e2e-a\n'), 'check': 'verified'})
        evidence = self.by_id(data)['e2e-a']['rubric']['evidence']
        self.assertEqual((evidence['status'], evidence['verified']), ('available', 2))
        self.assert_absent(LEAK)

    def test_only_final_pass_without_a_failure_is_clean_and_has_no_candidates(self):
        self.checkpoint('e2e-clean', 'build', 'PASS', '--evidence', 'e.log', '--final')
        data = self.full()
        self.assertEqual([(i['id'], i['class']) for i in data['runs']], [('e2e-clean', 'clean')])
        self.assertEqual(data['candidates'], [])
        self.assertEqual(data['status'], 'PASS')

    def test_one_failing_run_alone_is_recovery_without_a_candidate(self):
        self.failing_run('e2e-a', 'a cause')
        data = self.full()
        self.assertEqual(self.by_id(data)['e2e-a']['class'], 'recovery')
        self.assertEqual(data['candidates'], [])

    def test_run_without_the_final_marker_is_incomplete_even_after_a_pass(self):
        self.write('fail-e2e-a.log', 'x\n')
        self.checkpoint('e2e-a', 'build', 'FAIL', '--evidence', 'fail-e2e-a.log', '--cause-key', 'smoke-flaky')
        self.checkpoint('e2e-a', 'build', 'PASS', '--attempt', 2, '--evidence', 'e.log')
        self.assertEqual(self.by_id(self.full())['e2e-a']['class'], 'incomplete')

    def one_candidate(self, data):
        self.assertEqual(len(data['candidates']), 1, data['candidates'])
        item = data['candidates'][0]
        self.assertEqual(set(item), CANDIDATE_KEYS)
        return item


# ---------------------------------------------------------------------------------------------------------------
# Worktree (K11, D61): prawdziwy `git worktree add`
# ---------------------------------------------------------------------------------------------------------------
class WorktreeFlow(RetroBase):
    def setUp(self):
        super().setUp()
        # Manifest i .skillsync/ ignorowane PRZED init_git, bo ten robi `git add .`.
        self.write('.github/.gitignore', '.skillsync.yaml\n')
        self.write('.gitignore', '.skillsync/\n')
        self.init_git()
        self.tree = self.base / 'wt'
        self.sh(self.root, 'worktree', 'add', '-q', '-b', 'dispatch/test', str(self.tree))

    def sh(self, cwd, *args, code=0):
        result = subprocess.run(['git', '-C', str(cwd), *args], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result.stdout

    def cli_in_tree(self, command, *args):
        return subprocess.run([sys.executable, str(CLI), command, str(self.tree), *map(str, args), '--json'],
                              capture_output=True, text=True, timeout=30, cwd=self.tree)

    def copy_manifest(self):
        target = self.tree / '.github/.skillsync.yaml'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.root / '.github/.skillsync.yaml', target)

    def checkpoint(self, *args):
        return self.cli_in_tree('checkpoint', '--run-id', 'wave.P1', '--step', 'build', '--status', 'PASS',
                                '--next', 'n', *args)

    def test_manifest_is_ignored_and_absent_in_a_fresh_worktree(self):
        self.assertTrue((self.tree / '.git').exists(), 'worktree istnieje')
        self.assertFalse((self.tree / '.github/.skillsync.yaml').exists())
        tracked = self.sh(self.root, 'ls-files').split()
        self.assertNotIn('.github/.skillsync.yaml', tracked)
        self.assertIn('.github/.gitignore', tracked)

    def test_checkpoint_without_a_manifest_fails_with_exit_two(self):
        result = self.checkpoint('--evidence', 'e.log', '--final')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('Missing manifest', result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse((self.tree / '.skillsync/runs/wave.P1.json').exists())

    def test_checkpoint_works_after_copying_the_manifest_and_the_tree_stays_clean(self):
        self.copy_manifest()
        self.write('e.log', 'evidence\n')
        result = self.checkpoint('--evidence', 'e.log', '--final')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.tree / '.skillsync/runs/wave.P1.json').is_file())
        self.assertEqual(self.sh(self.tree, 'status', '--short').strip(), '')

    def test_add_all_and_commit_do_not_include_the_manifest(self):
        self.copy_manifest()
        (self.tree / 'change.txt').write_text('work\n')
        self.sh(self.tree, 'add', '-A')
        self.sh(self.tree, 'commit', '-qm', 'work in worktree')
        names = self.sh(self.tree, 'show', '--name-only', '--format=', 'HEAD').split()
        self.assertEqual(names, ['change.txt'])
        self.assertNotIn('.skillsync.yaml', ' '.join(names))
        self.assertNotIn('.github/.skillsync.yaml', self.sh(self.tree, 'ls-files').split())
        self.assertEqual(self.sh(self.tree, 'status', '--short').strip(), '')

    def test_moved_record_and_evidence_are_seen_by_retro_in_the_main_tree(self):
        self.copy_manifest()
        proof = self.tree / '.skillsync/evidence/proof.log'
        proof.parent.mkdir(parents=True, exist_ok=True)
        proof.write_text('worktree proof\n')
        result = self.checkpoint('--evidence', '.skillsync/evidence/proof.log', '--final')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for rel in ('.skillsync/runs/wave.P1.json', '.skillsync/evidence/proof.log'):
            target = self.root / rel
            self.assertFalse(target.exists(), 'przeniesienie nie nadpisuje, więc cel nie istnieje')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.tree / rel, target)
        data = self.full()
        self.assertEqual(data['status'], 'PASS')
        item = self.by_id(data)['wave.P1']
        self.assertEqual(item['class'], 'clean')
        evidence = item['rubric']['evidence']
        self.assertEqual((evidence['status'], evidence['verified'], evidence['evidence_files']),
                         ('available', 1, 1))
        self.assertEqual(self.sh(self.root, 'status', '--short').strip(), '')

    def test_retro_in_the_worktree_without_a_manifest_is_refused(self):
        result = self.cli_in_tree('retro')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(result.stderr.startswith('skillsync:'), result.stderr)
        self.assertEqual(result.stdout.strip(), '')


# ---------------------------------------------------------------------------------------------------------------
# Benchmark, magazyn, ponowne użycie dowodu, ścieżki dowodów, rekordy uszkodzone, tożsamość przebiegu
# ---------------------------------------------------------------------------------------------------------------
class BenchmarkAndStore(RetroBase):
    def test_benchmark_file_with_records_is_malformed(self):
        self.run_file('good', rec('a'))
        self.run_file('x', rec('s', 'FAIL', cause='c', cause_key='k'), close=False, benchmark={'cases': []})
        data = self.full(code=2)
        self.assertEqual(data['status'], 'BLOCKED')
        self.assertEqual(self.by_id(data)['x'], {'id': 'x', 'class': 'unreadable', 'reason': 'malformed'})
        self.assertIs(data['candidates_complete'], False)

    def test_directory_next_to_a_valid_benchmark_is_no_data_and_visible_in_scope(self):
        self.runs_dir('x')
        self.run_file('x', close=False, benchmark={'cases': []})
        data = self.full('--run-id', 'x')
        self.assertEqual([(i['id'], i['class']) for i in data['runs']], [('x', 'no_data')])
        self.assertEqual(data['status'], 'NOT_RUN')

    def test_runs_path_that_is_a_regular_file_is_an_error_not_not_run(self):
        self.write('.skillsync/runs', 'not a directory')
        self.refused()

    @unittest.skipIf(CHMOD_INEFFECTIVE, 'chmod 000 nie blokuje odczytu (root lub nie-POSIX)')
    def test_unreadable_skillsync_directory_is_an_error_not_not_run(self):
        self.write('.skillsync/runs/x.json', '{}')
        os.chmod(self.root / '.skillsync', 0)
        self.addCleanup(os.chmod, self.root / '.skillsync', 0o755)
        self.refused()


class EvidenceReuse(RetroBase):
    H = {'path': 'e.log', 'sha256': sha('evidence\n')}

    def evidence(self, data, ident='r'):
        return self.by_id(data)[ident]['rubric']['evidence']

    def test_same_evidence_on_final_pass_is_unexplained_even_with_a_cause(self):
        self.run_file('r', rec('a', 'FAIL', cause='x', evidence=[self.H]), rec('a'))
        data = self.full()
        self.assertEqual(self.by_id(data)['r']['class'], 'unexplained')
        self.assertEqual(self.evidence(data)['reused_from_problem'], 1)

    def test_different_evidence_is_recovery_with_zero_counter(self):
        self.write('g.log', 'other\n')
        self.run_file('r', rec('a', 'FAIL', cause='x', evidence=[self.H]),
                      rec('a', evidence=[{'path': 'g.log', 'sha256': sha('other\n')}]))
        data = self.full()
        self.assertEqual(self.by_id(data)['r']['class'], 'recovery')
        self.assertEqual(self.evidence(data)['reused_from_problem'], 0)

    def test_superset_of_evidence_is_not_a_reuse(self):
        self.write('g.log', 'other\n')
        both = [self.H, {'path': 'g.log', 'sha256': sha('other\n')}]
        self.run_file('r', rec('a', 'FAIL', cause='x', evidence=[self.H]), rec('a', evidence=both))
        data = self.full()
        self.assertEqual(self.by_id(data)['r']['class'], 'recovery')
        self.assertEqual(self.evidence(data)['reused_from_problem'], 0)

    def test_same_path_with_different_bytes_is_not_a_reuse(self):
        stale = {'path': 'e.log', 'sha256': sha('earlier bytes\n')}
        self.run_file('r', rec('a', 'FAIL', cause='x', evidence=[stale]), rec('a'))
        data = self.full()
        self.assertEqual(self.by_id(data)['r']['class'], 'recovery')
        self.assertEqual(self.evidence(data)['reused_from_problem'], 0)

    def reuse(self, *records):
        self.write('g.log', 'other\n')
        self.run_file('r', *records)
        data = self.full()
        return self.by_id(data)['r']['class'], self.evidence(data)['reused_from_problem']

    G = {'path': 'g.log', 'sha256': sha('other\n')}

    def test_evidence_of_a_later_non_final_pass_is_not_a_problem_evidence(self):
        got = self.reuse(rec('a', 'FAIL', cause='c', evidence=[self.H]), rec('a', evidence=[self.G]),
                         rec('a', evidence=[self.G]))
        self.assertEqual(got, ('recovery', 0))

    def test_passing_retry_with_its_own_evidence_is_not_a_problem_evidence(self):
        got = self.reuse(rec('a', attempt=2, cause='c', evidence=[self.G]), rec('a', evidence=[self.G]))
        self.assertEqual(got, ('recovery', 0))

    def test_final_evidence_equal_to_the_failure_evidence_after_a_pass_is_unexplained(self):
        got = self.reuse(rec('a', 'FAIL', cause='c', evidence=[self.H]), rec('a', evidence=[self.H]),
                         rec('a', evidence=[self.H]))
        self.assertEqual(got, ('unexplained', 1))

    def test_new_final_evidence_after_a_pass_with_the_failure_evidence_is_recovery(self):
        got = self.reuse(rec('a', 'FAIL', cause='c', evidence=[self.H]), rec('a', evidence=[self.H]),
                         rec('a', evidence=[self.G]))
        self.assertEqual(got, ('recovery', 0))

    def test_clean_run_has_zero_counter(self):
        self.run_file('r', rec('a'))
        self.assertEqual(self.evidence(self.full())['reused_from_problem'], 0)


class EvidencePathMasking(CandidateBase):
    """`failing_case.path` pochodzi od operatora, więc kształt segmentów jest ograniczany."""

    def pair_with_path(self, rel):
        self.run_file('x', self.case('x', 'flaky', rel=rel), rec('done'))
        self.run_file('y', self.case('y', 'flaky'), rec('done'))
        return self.one()['failing_case']

    def test_free_text_file_name_becomes_an_opaque_segment(self):
        case = self.pair_with_path('.skillsync/runs/r/Klient Jan Kowalski haslo hunter2.md')
        self.assertEqual(case['run'], 'x')
        self.assertRegex(case['path'], r'^\.skillsync/runs/r/invalid-name:[0-9a-f]{12}$')
        self.assert_absent('Kowalski', 'hunter2', 'Klient')

    def test_path_longer_than_240_characters_becomes_opaque(self):
        rel = '.skillsync/evidence/' + '/'.join(['ab-' * 20 + 'x'] * 4) + '/proof.log'
        self.assertGreater(len(rel), 240)
        case = self.pair_with_path(rel)
        self.assertIn('invalid-name:', case['path'])
        self.assertRegex(case['path'], r'invalid-name:[0-9a-f]{12}')
        self.assertLessEqual(len(case['path']), 240)
        self.assert_absent('ab-ab-ab-ab')

    def test_hex32_under_skillsync_logs_stays_readable(self):
        rel = '.skillsync/logs/' + 'a1b2c3d4' * 4 + '/x.log'
        self.assertEqual(self.pair_with_path(rel)['path'], rel)

    def test_hex32_in_any_other_directory_is_masked(self):
        for rel in ('.skillsync/evidence/' + 'a1b2c3d4' * 4 + '/x.log', 'docs/' + 'a1b2c3d4' * 4 + '/x.log',
                    'logs/' + 'a1b2c3d4' * 4 + '/x.log'):
            with self.subTest(rel=rel):
                shutil.rmtree(self.root / '.skillsync', ignore_errors=True)
                case = self.pair_with_path(rel)
                self.assertEqual(case['path'], rel.replace('a1b2c3d4' * 4, '[REDACTED]'))
                self.assert_absent('a1b2c3d4a1b2c3d4')

    def test_segment_with_a_leading_dot_stays_readable(self):
        for rel in ('.hidden-dir/x.log', '.github/notes.v2.log'):
            with self.subTest(rel=rel):
                shutil.rmtree(self.root / '.skillsync', ignore_errors=True)
                self.assertEqual(self.pair_with_path(rel)['path'], rel)


class CorruptRecords(RetroBase):
    def blocked(self, ident, text):
        self.run_file('good', rec('a'))
        self.write(f'.skillsync/runs/{ident}.json', text)
        data = self.full(code=2, timeout=20)
        self.assertEqual(data['status'], 'BLOCKED')
        self.assertIs(data['candidates_complete'], False)
        self.assert_absent('embedded null', 'OverflowError')
        return data

    def doc(self, *records):
        return json.dumps({'schema_version': 1, 'run_id': 'c', 'checkpoints': list(records)})

    def test_four_hundred_digit_duration_is_malformed(self):
        text = self.doc(rec('a', duration=10)).replace('"duration_seconds": 10', '"duration_seconds": 1' + '0' * 399)
        self.assertIn('0' * 399, text)
        data = self.blocked('big', text)
        self.assertEqual(self.by_id(data)['big'], {'id': 'big', 'class': 'unreadable', 'reason': 'malformed'})

    def test_nul_in_evidence_path_is_malformed(self):
        text = self.doc(rec('a', evidence=[{'path': 'a\x00b.log', 'sha256': sha('x')}]))
        self.assertIn('\\u0000', text)
        data = self.blocked('nul', text)
        self.assertEqual(self.by_id(data)['nul'], {'id': 'nul', 'class': 'unreadable', 'reason': 'malformed'})

    def test_lone_surrogate_in_evidence_path_does_not_break_the_report(self):
        self.run_file('good', rec('a'))
        self.write('.skillsync/runs/sur.json', self.doc(rec('a', evidence=[{'path': 'a\ud800b.log', 'sha256': sha('x')}])))
        result = self.run_retro(timeout=20)
        self.assertIn(result.returncode, (0, 2), result.stdout + result.stderr)
        data = self.parsed(result, result.returncode)
        self.check_shape(data)
        item = self.by_id(data)['sur']
        if item['class'] == 'unreadable':
            self.assertEqual(item['reason'], 'malformed')
        else:
            self.assertEqual(item['rubric']['evidence']['refused'], 1)
        self.assert_absent('embedded null', 'surrogate')

    def test_huge_duration_sum_does_not_break_the_report(self):
        records = [rec('a', duration=10 ** 308), rec('b', duration=10 ** 308), rec('c', duration=1.5)]
        self.run_file('huge', *records)
        result = self.run_retro(timeout=20)
        self.assertIn(result.returncode, (0, 2), result.stdout + result.stderr)
        if result.stdout.strip():
            self.check_shape(self.parsed(result, result.returncode))
        else:
            self.assertTrue(result.stderr.startswith('skillsync:'), result.stderr)

    def test_duplicate_checkpoints_key_is_malformed(self):
        text = ('{"schema_version": 1, "run_id": "d", "checkpoints": [], "checkpoints": ['
                + json.dumps(rec('a')) + ']}')
        data = self.blocked('dupkeys', text)
        self.assertEqual(self.by_id(data)['dupkeys']['reason'], 'malformed')

    def test_duplicate_key_inside_a_record_is_malformed(self):
        record = '{"status": "FAIL", ' + json.dumps(rec('a'))[1:]
        text = '{"schema_version": 1, "run_id": "d", "checkpoints": [' + record + ']}'
        data = self.blocked('dupstatus', text)
        self.assertEqual(self.by_id(data)['dupstatus']['reason'], 'malformed')


class ThresholdIdentity(CandidateBase):
    """Tożsamość logiczna z `run_id` w treści (gdy zgodny ze `_slug`), inaczej z nazwy pliku."""

    def put(self, name, record, **top):
        self.run_file(name, record, rec('done'), **top)

    def test_two_files_with_the_same_content_run_id_are_one_run(self):
        self.run_file('x', self.case('x', 'flaky'), rec('done'))
        self.run_file('x.bak', self.case('x.bak', 'flaky'), rec('done'), run_id='x')
        data = self.full()
        self.assertEqual(sorted(self.by_id(data)), ['x', 'x.bak'])
        self.assertEqual(data['candidates'], [])

    def test_differently_named_files_with_one_content_run_id_are_one_run(self):
        self.put('p', self.case('p', 'flaky'), run_id='q')
        self.put('r', self.case('r', 'flaky'), run_id='q')
        self.assertEqual(self.cands(), [])

    def test_invalid_run_id_falls_back_to_the_file_name(self):
        self.put('p', self.case('p', 'flaky'), run_id='bad id!')
        self.put('q', self.case('q', 'flaky'), run_id='bad id!')
        self.assertEqual(self.one()['runs'], 2)

    def test_non_string_run_id_falls_back_to_the_file_name(self):
        self.put('p', self.case('p', 'flaky'), run_id=123)
        self.put('q', self.case('q', 'flaky'), run_id=123)
        self.assertEqual(self.one()['runs'], 2)

    def test_missing_run_id_falls_back_to_the_file_name(self):
        for name in ('p', 'q'):
            records = [self.case(name, 'flaky'), {**rec('done'), 'final': True}]
            self.write(f'.skillsync/runs/{name}.json', json.dumps({'schema_version': 1, 'checkpoints': records}))
        self.assertEqual(self.one()['runs'], 2)


class FailingCaseSource(CandidateBase):
    def test_evidence_of_blocked_or_not_run_records_is_not_a_failing_case(self):
        for status in ('BLOCKED', 'NOT_RUN'):
            with self.subTest(status=status):
                shutil.rmtree(self.root / '.skillsync', ignore_errors=True)
                for ident in ('x', 'y'):
                    self.run_file(ident, self.case(ident, 'flaky', status=status), rec('done'))
                item = self.one()
                self.assertEqual((item['status'], item['failing_case'], item['missing']),
                                 ('needs_case', None, ['case']))



# ---------------------------------------------------------------------------------------------------------------
# R2b E1.1: `schema` w wyniku `retro`, `fixed` w decyzji, `skillsync retro-decide` (K1 do K5a)
# ---------------------------------------------------------------------------------------------------------------
class RetroSchema(RetroBase):
    def test_every_json_result_carries_schema_1(self):
        # NOT_RUN (pusty projekt), PASS i BLOCKED (nieznane id) mają to samo pole.
        self.assertEqual(self.parsed(self.run_retro(), 0)['schema'], 1)
        self.run_file('good', rec('a'), rec('b'))
        self.assertEqual(self.parsed(self.run_retro(), 0)['schema'], 1)
        blocked = self.parsed(self.run_retro('--run-id', 'nope'), 2)
        self.assertEqual((blocked['status'], blocked['schema']), ('BLOCKED', 1))

    def test_schema_is_an_integer_not_a_bool_or_string(self):
        self.run_file('good', rec('a'))
        value = self.parsed(self.run_retro(), 0)['schema']
        self.assertIs(type(value), int)


class RetroDecide(CandidateBase):
    PATH = 'docs/retro/decisions.yaml'

    # ---- pomoce ----
    def decide(self, *args, code=0, path=None):
        command = [sys.executable, str(CLI), 'retro-decide', str(path or self.root), *map(str, args), '--json']
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=20, cwd=self.root)
        self.text = result.stdout + result.stderr
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        self.assertEqual(result.returncode, code, self.text)
        try:
            data = json.loads(result.stdout)
        except ValueError:
            self.fail('stdout nie jest JSON-em: ' + self.text[:300])
        self.assertEqual(data['schema'], 1)
        return data

    def registry_path(self):
        return self.root / self.PATH

    def registry_data(self):
        import yaml
        return yaml.safe_load(self.registry_path().read_text(encoding='utf-8'))

    def entry(self, key):
        found = [item for item in (self.registry_data().get('decisions') or []) if item['cause_key'] == key]
        self.assertEqual(len(found), 1, found)
        return found[0]

    def seed(self, *items, raw=None):
        lines = ['version: 1', 'decisions:']
        for key, value, day, *extra in items:
            lines += [f'  - cause_key: {key}', f'    decision: {value}', f'    date: {day}', *extra]
        self.write(self.PATH, raw if raw is not None else '\n'.join(lines) + '\n')

    def tree(self):
        """Wszystkie pliki i katalogi projektu (bez .git), do wykrycia plików tymczasowych."""
        result = set()
        for dirpath, dirnames, filenames in os.walk(self.root):
            for name in dirnames + filenames:
                result.add(os.path.relpath(os.path.join(dirpath, name), self.root))
        return result

    def assert_refused(self, *args, reason_has=None, expect_unchanged=None):
        """Odmowa K3/K5: exit 2, BLOCKED z `reason`, plik rejestru bajtowo bez zmian."""
        path = self.registry_path()
        before = path.read_bytes() if path.is_file() and not path.is_symlink() else None
        tree_before = self.tree()
        data = self.decide(*args, code=2)
        self.assertEqual(data['status'], 'BLOCKED', data)
        self.assertEqual(set(data), {'schema', 'status', 'reason'})
        self.assertTrue(isinstance(data['reason'], str) and data['reason'])
        if reason_has:
            self.assertIn(reason_has, data['reason'])
        if before is not None:
            self.assertEqual(path.read_bytes(), before, 'plik rejestru zmieniony mimo odmowy')
        self.assertEqual(self.tree(), tree_before, 'odmowa zostawiła albo usunęła pliki')
        return data

    def good(self, key='flaky', **over):
        args = ['--cause-key', key, '--decision', 'approved', '--owner', 'skill', '--case', 'tests/x.py']
        return args

    # ---- K1 / K5: zapis ----
    def test_creates_missing_file_and_directory(self):
        self.pair()
        self.assertFalse((self.root / 'docs').exists())
        data = self.decide(*self.good())
        self.assertEqual(set(data), {'schema', 'status', 'path', 'decision', 'previous'})
        self.assertEqual(data['status'], 'PASS')
        self.assertEqual(data['path'], self.PATH)
        self.assertIsNone(data['previous'])
        self.assertEqual(set(data['decision']), {'cause_key', 'decision', 'date'})
        self.assertEqual((data['decision']['cause_key'], data['decision']['decision']), ('flaky', 'approved'))
        written = self.registry_data()
        self.assertEqual(written['version'], 1)
        self.assertEqual(self.entry('flaky')['owner'], 'skill')
        self.assertEqual(self.entry('flaky')['case'], 'tests/x.py')
        self.assertEqual(str(self.entry('flaky')['date']), data['decision']['date'])

    def test_creates_file_when_only_docs_directory_exists(self):
        (self.root / 'docs').mkdir()
        self.assertEqual(self.decide(*self.good())['status'], 'PASS')
        self.assertTrue(self.registry_path().is_file())

    def test_date_is_the_utc_day_of_the_write(self):
        from datetime import datetime, timezone
        low = datetime.now(timezone.utc).date()
        data = self.decide(*self.good())
        high = datetime.now(timezone.utc).date()
        self.assertIn(data['decision']['date'], {low.isoformat(), high.isoformat()})
        self.assertRegex(data['decision']['date'], r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
        self.assertEqual(str(self.entry('flaky')['date']), data['decision']['date'])

    def test_retro_reads_the_written_registry_as_available(self):
        self.pair()
        self.decide(*self.good())
        data = self.full()
        self.assertEqual(data['decisions'], {'status': 'available', 'path': self.PATH, 'count': 1})
        item = data['candidates'][0]
        self.assertEqual((item['status'], item['decision']['decision']), ('decided', 'approved'))

    def test_rejected_without_owner_case_ref(self):
        self.pair()
        data = self.decide('--cause-key', 'flaky', '--decision', 'rejected')
        self.assertEqual((data['status'], data['decision']['decision']), ('PASS', 'rejected'))
        entry = self.entry('flaky')
        self.assertEqual(set(entry), {'cause_key', 'decision', 'date'})
        self.assertEqual(self.full()['decisions']['status'], 'available')

    def test_approved_with_ref_and_other_entries_survive(self):
        self.seed(('old-one', 'rejected', '2026-10-01'))
        self.decide(*self.good(), '--ref', 'abc1234')
        self.assertEqual(self.entry('flaky')['ref'], 'abc1234')
        self.assertEqual(self.entry('old-one')['decision'], 'rejected')
        self.assertEqual(len(self.registry_data()['decisions']), 2)

    def test_write_leaves_only_the_registry_file(self):
        self.pair()
        before = self.tree()
        self.decide(*self.good())
        added = self.tree() - before
        self.assertEqual(added, {'docs', 'docs/retro', self.PATH})
        self.assertEqual(sorted(os.listdir(self.root / 'docs/retro')), ['decisions.yaml'])
        # też przy nadpisaniu istniejącego pliku
        before = self.tree()
        self.decide(*self.good(), '--replace')
        self.assertEqual(self.tree(), before)

    # ---- K2: odmowy wymagań pól ----
    def test_approved_needs_owner_and_case(self):
        self.assert_refused('--cause-key', 'flaky', '--decision', 'approved', '--case', 'tests/x.py')
        self.assert_refused('--cause-key', 'flaky', '--decision', 'approved', '--owner', 'skill')
        self.assert_refused('--cause-key', 'flaky', '--decision', 'approved')
        self.assertFalse((self.root / 'docs').exists(), 'odmowa nie może tworzyć katalogów')

    def test_approved_owner_outside_the_list_is_refused(self):
        for owner in ('plan', 'SKILL', '', 'skill ', 'x' * 50):
            with self.subTest(owner=owner):
                self.assert_refused('--cause-key', 'flaky', '--decision', 'approved', '--owner', owner,
                                    '--case', 'tests/x.py')

    def test_every_listed_owner_is_accepted(self):
        for index, owner in enumerate(OWNER_VALUES):
            with self.subTest(owner=owner):
                key = f'key-{index}'
                self.decide('--cause-key', key, '--decision', 'approved', '--owner', owner, '--case', 'c')
                self.assertEqual(self.entry(key)['owner'], owner)

    def test_rejected_refuses_owner_case_and_ref(self):
        for extra in (('--owner', 'skill'), ('--case', 'tests/x.py'), ('--ref', 'abc1234')):
            with self.subTest(extra=extra):
                self.assert_refused('--cause-key', 'flaky', '--decision', 'rejected', *extra)

    def test_unknown_decision_value_is_refused_without_traceback(self):
        result = subprocess.run([sys.executable, str(CLI), 'retro-decide', str(self.root), '--cause-key', 'flaky',
                                 '--decision', 'maybe', '--json'], capture_output=True, text=True, cwd=self.root)
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['status'], 'BLOCKED')
        self.assertFalse((self.root / 'docs').exists())

    # ---- K3: odmowy ----
    def test_invalid_registry_is_refused(self):
        for raw in ('version: 1\ndecisions: [\n', 'version: 2\ndecisions: []\n',
                    'version: 1\ndecisions:\n  - {cause_key: other, decision: maybe, date: 2026-10-29}\n',
                    'version: 1\ndecisions:\n  - {cause_key: other, decision: approved, date: soon}\n',
                    'version: 1\ndecisions:\n  - {cause_key: other, decision: approved, date: 2026-10-29, why: x}\n'):
            with self.subTest(raw=raw):
                self.seed(raw=raw)
                self.assert_refused(*self.good())
                self.assert_refused('--cause-key', 'flaky', '--decision', 'rejected', '--replace')

    def test_oversized_registry_is_refused(self):
        self.seed(raw='version: 1\ndecisions: []\n' + '#' * (1024 * 1024) + '\n')
        self.assert_refused(*self.good())

    def test_bad_cause_key_is_refused(self):
        for key in ('Flaky', 'UPPER', '-lead', 'has space', 'a/b', '../x', 'a_b', '', 'x' * 49, 'zażółć',
                    'a\nb', 'key.dot'):
            with self.subTest(key=key):
                self.assert_refused('--cause-key', key, '--decision', 'rejected')

    def test_key_that_looks_like_a_token_is_refused(self):
        for key in ('a' * 32, 'abc-' + 'b' * 33, 'c' * 40):
            with self.subTest(key=key):
                self.assert_refused('--cause-key', key, '--decision', 'rejected')

    def test_redacted_mask_as_key_is_refused(self):
        for key in ('[REDACTED]', 'redacted-[REDACTED]'):
            with self.subTest(key=key):
                self.assert_refused('--cause-key', key, '--decision', 'rejected')

    def test_token_in_case_or_ref_is_refused_and_not_echoed(self):
        for token in TOKENS:
            for flag in ('--case', '--ref'):
                with self.subTest(token=token, flag=flag):
                    extra = ['--owner', 'skill', '--case', 'tests/x.py']
                    if flag == '--case':
                        extra = ['--owner', 'skill', '--case', f'see {token} here']
                    else:
                        extra += ['--ref', f'{token}']
                    self.assert_refused('--cause-key', 'flaky', '--decision', 'approved', *extra)
                    self.assertNotIn(token, self.text)

    def test_control_characters_in_case_or_ref_are_refused(self):
        for char in ('\n', '\r', '\t', '\x00', '\x1b', '\x7f'):
            for flag in ('--case', '--ref'):
                with self.subTest(char=repr(char), flag=flag):
                    extra = ['--owner', 'skill', '--case', 'tests/x.py']
                    value = f'a{char}b'
                    if flag == '--case':
                        extra[3] = value
                    else:
                        extra += ['--ref', value]
                    try:
                        self.assert_refused('--cause-key', 'flaky', '--decision', 'approved', *extra)
                    except ValueError:  # NUL w argv odrzuca sam system przed uruchomieniem
                        self.assertEqual(char, '\x00')

    def test_case_and_ref_over_300_characters_are_refused_300_is_accepted(self):
        base = ['--cause-key', 'flaky', '--decision', 'approved', '--owner', 'skill']
        self.assert_refused(*base, '--case', 'x' * 301)
        self.assert_refused(*base, '--case', 'tests/x.py', '--ref', 'r' * 301)
        self.assert_refused(*base, '--case', 'é' * 301)
        self.assertEqual(self.decide(*base, '--case', 'x' * 300, '--ref', 'r' * 300)['status'], 'PASS')
        self.assertEqual(self.entry('flaky')['case'], 'x' * 300)

    def test_duplicate_key_without_replace_is_refused(self):
        self.seed(('flaky', 'rejected', '2026-10-01'))
        self.assert_refused(*self.good())
        self.assert_refused('--cause-key', 'flaky', '--decision', 'rejected')
        self.assertEqual(self.entry('flaky')['decision'], 'rejected')

    @needs_os_symlinks
    def test_symlinked_docs_is_refused(self):
        self.write('real/retro/decisions.yaml', 'version: 1\ndecisions: []\n')
        (self.root / 'docs').symlink_to(self.root / 'real')
        real = self.root / 'real/retro/decisions.yaml'
        before = real.read_bytes()
        self.assert_refused(*self.good())
        self.assertEqual(real.read_bytes(), before)

    @needs_os_symlinks
    def test_symlinked_docs_is_refused_even_when_target_is_empty(self):
        (self.root / 'real').mkdir()
        (self.root / 'docs').symlink_to(self.root / 'real')
        self.assert_refused(*self.good())
        self.assertEqual(os.listdir(self.root / 'real'), [])

    @needs_os_symlinks
    def test_symlinked_retro_directory_is_refused(self):
        self.write('elsewhere/decisions.yaml', 'version: 1\ndecisions: []\n')
        (self.root / 'docs').mkdir()
        (self.root / 'docs/retro').symlink_to(self.root / 'elsewhere')
        before = (self.root / 'elsewhere/decisions.yaml').read_bytes()
        self.assert_refused(*self.good())
        self.assertEqual((self.root / 'elsewhere/decisions.yaml').read_bytes(), before)

    @needs_os_symlinks
    def test_symlinked_registry_file_is_refused(self):
        self.write('elsewhere.yaml', 'version: 1\ndecisions: []\n')
        (self.root / 'docs/retro').mkdir(parents=True)
        self.registry_path().symlink_to(self.root / 'elsewhere.yaml')
        before = (self.root / 'elsewhere.yaml').read_bytes()
        self.assert_refused(*self.good())
        self.assertTrue(self.registry_path().is_symlink(), 'dowiązanie nie może zostać podmienione')
        self.assertEqual((self.root / 'elsewhere.yaml').read_bytes(), before)

    @needs_os_symlinks
    def test_dangling_symlink_as_registry_is_refused(self):
        (self.root / 'docs/retro').mkdir(parents=True)
        self.registry_path().symlink_to(self.root / 'nowhere.yaml')
        self.assert_refused(*self.good())
        self.assertFalse((self.root / 'nowhere.yaml').exists())

    def test_hard_linked_registry_is_refused(self):
        self.seed(('other', 'rejected', '2026-10-01'))
        twin = self.root / 'twin.yaml'
        try:
            os.link(self.registry_path(), twin)
        except OSError:
            self.skipTest('hard links unavailable')
        self.assertGreater(os.stat(self.registry_path()).st_nlink, 1)
        before = twin.read_bytes()
        self.assert_refused(*self.good())
        self.assertEqual(twin.read_bytes(), before)

    def test_directory_instead_of_registry_is_refused(self):
        self.registry_path().mkdir(parents=True)
        self.assert_refused(*self.good())
        self.assertTrue(self.registry_path().is_dir())

    @needs_fifo
    def test_fifo_instead_of_registry_is_refused_without_hanging(self):
        self.make_fifo('docs', 'retro', 'decisions.yaml')
        data = self.decide(*self.good(), code=2)
        self.assertEqual(data['status'], 'BLOCKED')

    def test_project_path_that_does_not_exist_is_not_a_traceback(self):
        result = subprocess.run([sys.executable, str(CLI), 'retro-decide', str(self.root / 'missing'),
                                 *self.good(), '--json'], capture_output=True, text=True, cwd=self.root)
        self.assertNotIn('Traceback', result.stderr)
        self.assertNotEqual(result.returncode, 0)

    # ---- --replace ----
    def test_replace_reports_previous_and_swaps_the_entry(self):
        self.seed(('flaky', 'rejected', '2026-10-01'), ('other', 'rejected', '2026-10-02'))
        data = self.decide(*self.good(), '--replace')
        self.assertEqual(data['status'], 'PASS')
        self.assertEqual(data['previous'], {'decision': 'rejected', 'date': '2026-10-01'})
        self.assertEqual(self.entry('flaky')['decision'], 'approved')
        self.assertEqual(len(self.registry_data()['decisions']), 2)
        self.assertEqual(str(self.entry('other')['date']), '2026-10-02')

    def test_replace_keeps_owner_and_case_when_not_given(self):
        self.seed(('flaky', 'approved', '2026-10-01', '    owner: hook', '    case: tests/keep.py'))
        data = self.decide('--cause-key', 'flaky', '--decision', 'approved', '--replace', '--ref', 'abc1234')
        self.assertEqual(data['status'], 'PASS')
        entry = self.entry('flaky')
        self.assertEqual((entry['owner'], entry['case'], entry['ref']), ('hook', 'tests/keep.py', 'abc1234'))
        self.assertNotEqual(str(entry['date']), '2026-10-01')

    def test_replace_overrides_owner_and_case_when_given(self):
        self.seed(('flaky', 'approved', '2026-10-01', '    owner: hook', '    case: tests/keep.py'))
        self.decide('--cause-key', 'flaky', '--decision', 'approved', '--replace', '--owner', 'test',
                    '--case', 'tests/new.py')
        entry = self.entry('flaky')
        self.assertEqual((entry['owner'], entry['case']), ('test', 'tests/new.py'))

    def test_replace_approved_with_rejected_drops_owner_case_and_ref(self):
        self.seed(('flaky', 'approved', '2026-10-01', '    owner: hook', '    case: tests/x.py', '    ref: abc1234'))
        data = self.decide('--cause-key', 'flaky', '--decision', 'rejected', '--replace')
        self.assertEqual(data['previous'], {'decision': 'approved', 'date': '2026-10-01'})
        self.assertEqual(set(self.entry('flaky')), {'cause_key', 'decision', 'date'})

    def test_replace_rejected_with_approved_still_needs_owner_and_case(self):
        self.seed(('flaky', 'rejected', '2026-10-01'))
        self.assert_refused('--cause-key', 'flaky', '--decision', 'approved', '--replace')
        self.assert_refused('--cause-key', 'flaky', '--decision', 'approved', '--replace', '--owner', 'skill')

    def test_replace_without_existing_entry_writes_with_no_previous(self):
        self.seed(('other', 'rejected', '2026-10-01'))
        data = self.decide(*self.good(), '--replace')
        self.assertEqual((data['status'], data['previous']), ('PASS', None))
        self.assertEqual(self.entry('other')['decision'], 'rejected')
        self.assertEqual(self.entry('flaky')['decision'], 'approved')

    def test_replace_result_is_readable_by_retro(self):
        self.pair()
        self.seed(('flaky', 'rejected', '2026-10-01'))
        self.decide(*self.good(), '--replace')
        self.assertEqual(self.full()['decisions'], {'status': 'available', 'path': self.PATH, 'count': 1})

    # ---- K2a: pole `fixed` i procedura po poprawce ----
    def test_fixed_is_true_only_when_ref_is_recorded(self):
        self.pair()
        self.seed(('flaky', 'approved', '2026-10-29', '    owner: skill', '    case: tests/x.py'))
        self.assertIs(self.one()['decision']['fixed'], False)
        self.seed(('flaky', 'approved', '2026-10-29', '    owner: skill', '    case: tests/x.py', '    ref: abc1234'))
        self.assertIs(self.one()['decision']['fixed'], True)
        self.seed(('flaky', 'rejected', '2026-10-29'))
        self.assertIs(self.one()['decision']['fixed'], False)

    def test_recurrence_before_the_fix_is_marked_not_fixed(self):
        self.pair()
        self.run_file('z', self.case('z', 'flaky', recorded_at='2026-10-30T10:00:00+00:00'), rec('done'))
        self.seed(('flaky', 'approved', '2026-10-28', '    owner: skill', '    case: tests/x.py'))
        item = self.one()
        self.assertEqual(item['status'], 'recurred')
        self.assertIs(item['decision']['fixed'], False)

    def test_recurrence_after_rejection_is_recurred_and_not_fixed(self):
        self.pair()
        self.run_file('z', self.case('z', 'flaky', recorded_at='2026-10-30T10:00:00+00:00'), rec('done'))
        self.seed(('flaky', 'rejected', '2026-10-28'))
        item = self.one()
        self.assertEqual((item['status'], item['decision']['decision']), ('recurred', 'rejected'))
        self.assertIs(item['decision']['fixed'], False)

    def test_docs_or_retro_being_a_file_is_refused(self):
        self.write('docs', 'plik')
        self.assert_refused(*self.good())
        (self.root / 'docs').unlink()
        self.write('docs/retro', 'plik')
        self.assert_refused(*self.good())

    def test_key_with_a_token_prefix_is_refused(self):
        self.assert_refused('--cause-key', 'sk-ant-abc', '--decision', 'rejected')

    def test_format_control_character_in_case_is_refused(self):
        self.assert_refused('--cause-key', 'flaky', '--decision', 'approved', '--owner', 'skill', '--case', 'a\u202eb')

    def test_written_date_is_a_plain_yaml_date(self):
        self.decide(*self.good())
        self.assertRegex(self.registry_path().read_text(encoding='utf-8'), r'(?m)^\s+date: \d{4}-\d{2}-\d{2}$')

    def test_replace_with_ref_after_the_fix_moves_candidate_to_decided(self):
        self.pair()
        self.run_file('z', self.case('z', 'flaky', recorded_at='2026-10-30T10:00:00+00:00'), rec('done'))
        self.seed(('flaky', 'approved', '2026-10-28', '    owner: skill', '    case: tests/x.py'))
        before = self.one()
        self.assertEqual((before['status'], before['decision']['fixed']), ('recurred', False))
        data = self.decide('--cause-key', 'flaky', '--decision', 'approved', '--replace', '--ref', 'abc1234')
        self.assertEqual(data['previous'], {'decision': 'approved', 'date': '2026-10-28'})
        after = self.one()
        self.assertEqual(after['status'], 'decided')
        self.assertEqual(after['decision']['fixed'], True)
        self.assertEqual(after['decision']['date'], data['decision']['date'])
        self.assertEqual(after['decision']['records_after'], 0)

    # ---- K19: aliasy w retro-decide ----
    def alias_list(self):
        (self.library / 'workflow').mkdir(parents=True, exist_ok=True)
        (self.library / 'workflow/cause-keys.yaml').write_text(yaml.safe_dump(
            {'version': 1, 'keys': {'flaky-tests': {'description': 'd', 'aliases': ['flaky-test']}}}),
            encoding='utf-8')

    def test_alias_is_stored_and_returned_as_the_canonical_key(self):
        self.alias_list()
        data = self.decide('--cause-key', 'flaky-test', '--decision', 'rejected')
        self.assertEqual(data['status'], 'PASS')
        self.assertEqual(data['decision']['cause_key'], 'flaky-tests')
        self.assertEqual(self.entry('flaky-tests')['decision'], 'rejected')
        self.assertEqual([item['cause_key'] for item in self.registry_data()['decisions']], ['flaky-tests'])
        self.assertNotIn('flaky-test\n', self.registry_path().read_text(encoding='utf-8'))

    def test_canonical_key_is_stored_as_given(self):
        self.alias_list()
        data = self.decide('--cause-key', 'flaky-tests', '--decision', 'rejected')
        self.assertEqual(data['decision']['cause_key'], 'flaky-tests')

    def test_approved_by_alias_records_owner_and_case_under_the_canonical_key(self):
        self.alias_list()
        data = self.decide('--cause-key', 'flaky-test', '--decision', 'approved', '--owner', 'skill',
                           '--case', 'tests/x.py')
        self.assertEqual(data['decision']['cause_key'], 'flaky-tests')
        self.assertEqual((self.entry('flaky-tests')['owner'], self.entry('flaky-tests')['case']),
                         ('skill', 'tests/x.py'))

    def test_alias_collides_with_an_existing_canonical_entry(self):
        self.alias_list()
        self.seed(('flaky-tests', 'rejected', '2026-10-01'))
        self.assert_refused('--cause-key', 'flaky-test', '--decision', 'rejected', reason_has='--replace')

    def test_replace_by_alias_swaps_the_canonical_entry(self):
        self.alias_list()
        self.seed(('flaky-tests', 'rejected', '2026-10-01'))
        data = self.decide('--cause-key', 'flaky-test', '--decision', 'rejected', '--replace')
        self.assertEqual(data['previous'], {'decision': 'rejected', 'date': '2026-10-01'})
        self.assertEqual(len(self.registry_data()['decisions']), 1)

    def test_replace_collapses_alias_and_canonical_entries_into_one(self):
        self.alias_list()
        self.seed(('flaky-test', 'rejected', '2026-10-01'), ('other', 'rejected', '2026-10-02'),
                  ('flaky-tests', 'rejected', '2026-10-05'))
        data = self.decide('--cause-key', 'flaky-tests', '--decision', 'rejected', '--replace')
        self.assertEqual(data['previous'], {'decision': 'rejected', 'date': '2026-10-05'})
        self.assertEqual([item['cause_key'] for item in self.registry_data()['decisions']],
                         ['flaky-tests', 'other'])

    def test_replace_group_tie_takes_the_first_entry_as_previous(self):
        self.alias_list()
        self.seed(('flaky-test', 'rejected', '2026-10-05'), ('flaky-tests', 'rejected', '2026-10-05'))
        data = self.decide('--cause-key', 'flaky-test', '--decision', 'rejected', '--replace')
        self.assertEqual(data['previous'], {'decision': 'rejected', 'date': '2026-10-05'})
        self.assertEqual(len(self.registry_data()['decisions']), 1)

    def test_key_outside_the_list_is_still_accepted(self):
        self.alias_list()
        data = self.decide('--cause-key', 'brand-new', '--decision', 'rejected')
        self.assertEqual(data['decision']['cause_key'], 'brand-new')

    def test_missing_list_keeps_the_key_as_given(self):
        self.alias_list()
        (self.library / 'workflow/cause-keys.yaml').unlink()
        data = self.decide('--cause-key', 'flaky-test', '--decision', 'rejected')
        self.assertEqual(data['decision']['cause_key'], 'flaky-test')



# ---------------------------------------------------------------------------------------------------------------
# R2b E3 (K18, K19): sygnały bez rekordu, lista kluczy przyczyn i aliasy
# ---------------------------------------------------------------------------------------------------------------
class UnrecordedSignals(RetroBase):
    """K18: `unrecorded_signals` = max(0, FAIL + CHANGES_REQUIRED w raportach - failed_records przebiegów katalogu)."""

    def entry(self, directory='rd'):
        data = self.full()
        return {d['id']: d for d in data['report_dirs']}[directory], data

    def test_changes_required_without_a_record_counts_one(self):
        self.report_file('rd', 'review.md', 'Status: CHANGES_REQUIRED\n')
        self.assertEqual(self.entry()[0]['unrecorded_signals'], 1)

    def test_fail_report_with_a_fail_record_counts_zero(self):
        self.report_file('rd', 'run.md', 'Status: FAIL\n')
        self.run_file('rd', rec('a', 'FAIL', cause='c', evidence=[]), rec('a', attempt=2))
        self.assertEqual(self.entry()[0]['unrecorded_signals'], 0)

    def test_changes_required_with_a_fail_record_counts_zero(self):
        self.report_file('rd', 'review.md', 'Werdykt: CHANGES_REQUIRED\n')
        self.run_file('rd', rec('a', 'FAIL', cause='c', evidence=[]), rec('a', attempt=2))
        self.assertEqual(self.entry()[0]['unrecorded_signals'], 0)

    def test_blocked_record_counts_as_a_failed_record(self):
        self.report_file('rd', 'run.md', 'Status: FAIL\n')
        self.run_file('rd', rec('a', 'BLOCKED', cause='c', evidence=[]), rec('a', attempt=2))
        self.assertEqual(self.entry()[0]['unrecorded_signals'], 0)

    def test_only_fail_and_changes_required_signals_count(self):
        self.report_file('rd', 'a.md', 'Status: PASS\nStatus: BLOCKED\nStatus: NOT_RUN\nStatus: DONE\n'
                                       'Status: in_progress\n')
        self.assertEqual(self.entry()[0]['unrecorded_signals'], 0)

    def test_surplus_signals_over_records_are_counted(self):
        self.report_file('rd', 'a.md', 'Status: FAIL\nStatus: FAIL\n')
        self.report_file('rd', 'b.md', 'Werdykt: CHANGES_REQUIRED\nStatus: PASS\n')
        self.run_file('rd', rec('a', 'FAIL', cause='c', evidence=[]), rec('a', attempt=2))
        self.assertEqual(self.entry()[0]['unrecorded_signals'], 2)

    def test_never_negative(self):
        self.report_file('rd', 'a.md', 'Status: FAIL\n')
        self.run_file('rd', rec('a', 'FAIL', cause='c', evidence=[]), rec('b', 'FAIL', cause='c', evidence=[]),
                      rec('a', attempt=2), rec('b', attempt=2))
        self.assertEqual(self.entry()[0]['unrecorded_signals'], 0)

    def test_attempt_files_of_the_directory_are_subtracted(self):
        self.report_file('rd', 'a.md', 'Status: FAIL\nStatus: FAIL\n')
        self.run_file('rd', rec('a', 'FAIL', cause='c', evidence=[]), rec('a', attempt=2))
        self.run_file('rd.2', rec('b', 'FAIL', cause='c', evidence=[]), rec('b', attempt=2))
        self.assertEqual(self.entry()[0]['unrecorded_signals'], 0)

    def test_records_of_another_directory_are_not_subtracted(self):
        self.report_file('rd', 'a.md', 'Status: FAIL\n')
        self.run_file('other', rec('a', 'FAIL', cause='c', evidence=[]), rec('a', attempt=2))
        self.assertEqual(self.entry()[0]['unrecorded_signals'], 1)

    def test_directory_without_signals_counts_zero(self):
        self.report_file('rd', 'a.md', 'bez statusu\n')
        self.assertEqual(self.entry()[0]['unrecorded_signals'], 0)

    def test_run_class_does_not_depend_on_report_signals(self):
        self.run_file('rd', rec('a', 'FAIL', cause='c', evidence=[]), rec('a', attempt=2))
        without = self.by_id(self.full())['rd']
        self.report_file('rd', 'a.md', 'Status: CHANGES_REQUIRED\nStatus: FAIL\nStatus: FAIL\n')
        data = self.full()
        self.assertEqual(self.by_id(data)['rd'], without)
        self.assertEqual({d['id']: d for d in data['report_dirs']}['rd']['unrecorded_signals'], 2)

    def test_signal_counts_stay_in_status_signals(self):
        self.report_file('rd', 'a.md', 'Status: FAIL\nWerdykt: CHANGES_REQUIRED\n')
        entry, _ = self.entry()
        self.assertEqual((entry['status_signals']['FAIL'], entry['status_signals']['CHANGES_REQUIRED']), (1, 1))
        self.assertEqual(entry['unrecorded_signals'], 2)


class CauseKeyList(CandidateBase):
    """K19: `workflow/cause-keys.yaml` w bibliotece (klucze kanoniczne i aliasy), `unknown_cause_keys`."""

    def pair(self, key='flaky', first=None, second=None, **kw):
        """Run ids zależne od klucza, żeby kilka kluczy w jednym projekcie nie nadpisywało plików."""
        super().pair(key, first or f'x-{key}', second or f'y-{key}', **kw)

    def keys_file(self, keys=None, library=None, raw=None):
        library = library or self.library
        (library / 'workflow').mkdir(parents=True, exist_ok=True)
        text = raw if raw is not None else yaml.safe_dump(
            {'version': 1, 'keys': {name: {'description': 'opis ' + name, 'aliases': list(aliases)}
                                    for name, aliases in (keys or {}).items()}})
        (library / 'workflow/cause-keys.yaml').write_text(text, encoding='utf-8')

    def alias_pair(self, alias='flaky-test', canonical='flaky-tests', **kw):
        self.run_file('x', self.case('x', alias, **kw), rec('done'))
        self.run_file('y', self.case('y', canonical, **kw), rec('done'))

    # ---- aliasy w kandydatach ----
    def test_alias_and_canonical_key_make_one_candidate(self):
        self.keys_file({'flaky-tests': ['flaky-test']})
        self.alias_pair()
        item = self.one()
        self.assertEqual((item['cause_key'], item['runs'], item['records']), ('flaky-tests', 2, 2))

    def test_two_runs_with_the_alias_only_report_the_canonical_key(self):
        self.keys_file({'flaky-tests': ['flaky-test']})
        self.pair(key='flaky-test')
        self.assertEqual(self.one()['cause_key'], 'flaky-tests')

    def test_without_the_list_alias_and_canonical_stay_separate(self):
        self.alias_pair()
        data = self.full()
        self.assertEqual(data['candidates'], [])
        self.assertIsNone(data['unknown_cause_keys'])

    def test_several_aliases_of_one_key_merge(self):
        self.keys_file({'flaky-tests': ['flaky-test', 'flake']})
        self.run_file('x', self.case('x', 'flaky-test'), rec('done'))
        self.run_file('y', self.case('y', 'flake'), rec('done'))
        self.run_file('z', self.case('z', 'flaky-tests'), rec('done'))
        item = self.one()
        self.assertEqual((item['cause_key'], item['runs'], item['records']), ('flaky-tests', 3, 3))

    def test_different_canonical_keys_do_not_merge(self):
        self.keys_file({'alpha': ['a1'], 'beta': ['b1']})
        self.run_file('x', self.case('x', 'a1'), rec('done'))
        self.run_file('y', self.case('y', 'b1'), rec('done'))
        self.assertEqual(self.cands(), [])

    def test_layer_option_selects_the_list(self):
        other = make_layer(self.base / 'other-library', 'core', 'core')
        self.keys_file({'flaky-tests': ['flaky-test']}, library=other)
        self.alias_pair()
        self.assertEqual(self.cands(), [])
        item = self.one('--layer', f'core={other}')
        self.assertEqual((item['cause_key'], item['runs']), ('flaky-tests', 2))

    # ---- decyzje pod kluczem kanonicznym ----
    def registry(self, key, value='approved', day='2026-10-29'):
        extra = '    owner: skill\n    case: tests/x.py\n' if value == 'approved' else ''
        self.write('docs/retro/decisions.yaml',
                   f'version: 1\ndecisions:\n  - cause_key: {key}\n    decision: {value}\n    date: {day}\n{extra}')

    def test_decision_under_the_canonical_key_covers_the_alias_record(self):
        self.keys_file({'flaky-tests': ['flaky-test']})
        self.alias_pair()
        self.registry('flaky-tests')
        item = self.one()
        self.assertEqual(item['status'], 'decided')
        self.assertEqual(item['decision']['decision'], 'approved')

    def test_alias_records_after_the_decision_count_as_recurrence(self):
        self.keys_file({'flaky-tests': ['flaky-test']})
        self.alias_pair()
        self.registry('flaky-tests', day='2026-10-01')
        item = self.one()
        self.assertEqual(item['status'], 'recurred')
        self.assertEqual(item['decision']['records_after'], 2)

    # ---- unknown_cause_keys ----
    def test_unknown_keys_are_sorted_and_exclude_canonical_and_alias_keys(self):
        self.keys_file({'flaky-tests': ['flaky-test']})
        for key in ('zeta-new', 'alpha-new', 'flaky-tests', 'flaky-test'):
            self.pair(key=key)
        data = self.full()
        self.assertEqual(data['unknown_cause_keys'], ['alpha-new', 'zeta-new'])

    def test_no_unknown_keys_gives_an_empty_list(self):
        self.keys_file({'flaky-tests': ['flaky-test']})
        self.pair(key='flaky-tests')
        self.assertEqual(self.full()['unknown_cause_keys'], [])

    def test_unknown_keys_are_masked(self):
        self.keys_file({'flaky-tests': []})
        token = 'sk-ant-api03aaaa1111'
        self.pair(key=token)
        data = self.full()
        self.assertEqual(data['unknown_cause_keys'], ['[REDACTED]'])
        self.assert_absent(token, 'api03aaaa1111')

    # ---- lista niedostępna albo niepoprawna ----
    def test_missing_list_gives_null_and_keeps_status(self):
        self.pair()
        data = self.full()
        self.assertIsNone(data['unknown_cause_keys'])
        self.assertEqual(data['status'], 'PASS')

    def test_invalid_lists_give_null_without_aliases_and_keep_status(self):
        cases = {
            'yaml': 'keys: [unclosed',
            'version': 'version: 2\nkeys:\n  flaky-tests:\n    description: d\n    aliases: [flaky-test]\n',
            'keys-not-a-mapping': 'version: 1\nkeys: [flaky-tests]\n',
            'aliases-not-a-list': 'version: 1\nkeys:\n  flaky-tests:\n    description: d\n    aliases: flaky-test\n',
            'not-a-mapping': '- just\n- a list\n',
            'empty': '',
        }
        for name, raw in cases.items():
            with self.subTest(name):
                shutil.rmtree(self.root / '.skillsync', ignore_errors=True)
                self.keys_file(raw=raw)
                self.alias_pair()
                data = self.full()
                self.assertIsNone(data['unknown_cause_keys'])
                self.assertEqual(data['candidates'], [], 'bez aliasów alias i klucz kanoniczny to różne klucze')
                self.assertEqual(data['status'], 'PASS')

    def test_directory_instead_of_the_list_gives_null(self):
        (self.library / 'workflow/cause-keys.yaml').mkdir(parents=True)
        self.pair()
        data = self.full()
        self.assertIsNone(data['unknown_cause_keys'])
        self.assertEqual(data['status'], 'PASS')

    def test_reading_the_list_does_not_change_it(self):
        self.keys_file({'flaky-tests': ['flaky-test']})
        path = self.library / 'workflow/cause-keys.yaml'
        before = path.read_bytes()
        self.alias_pair()
        self.full()
        self.assertEqual(path.read_bytes(), before)


# ---------------------------------------------------------------------------------------------------------------
# R2b E4 (K20 do K22): `skillsync retro --all`
# ---------------------------------------------------------------------------------------------------------------
class RetroAll(CandidateBase):
    """Projekty tymczasowe pod `$HOME/Repos`, lista z `discover_paths` configu w `SKILLSYNC_CONFIG`."""

    def setUp(self):
        super().setUp()
        self.home = self.base / 'home'
        self.repos = self.home / 'Repos'
        self.repos.mkdir(parents=True)
        # Fixture config (layers: `core` = `self.library`, rule for the fixture dir) plus the project list.
        self.config = self.use_layers(discover_paths=[str(self.repos)], tags={})

    def pair(self, key='flaky', first=None, second=None, **kw):
        """Run ids zależne od klucza, żeby kilka kluczy w jednym projekcie nie nadpisywało plików."""
        super().pair(key, first or f'x-{key}', second or f'y-{key}', **kw)

    def project(self, name, under=None):
        """Tworzy projekt (manifest, e.log) i przełącza na niego `self.root`, żeby działały `pair` i `run_file`."""
        self.root = (under or self.repos) / name
        self.root.mkdir(parents=True)
        self.save()
        self.write('e.log', 'evidence\n')
        return self.root

    def run_all(self, *args, code=0, expect_json=True):
        env = {**os.environ, 'HOME': str(self.home), 'USERPROFILE': str(self.home), 'SKILLSYNC_CONFIG': str(self.config)}
        result = subprocess.run([sys.executable, str(CLI), 'retro', '--all', *map(str, args), '--json'],
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=40,
                                cwd=self.base, env=env)
        self.text = result.stdout + result.stderr
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        self.assertEqual(result.returncode, code, self.text)
        if not expect_json:
            return result
        try:
            data = json.loads(result.stdout)
        except ValueError:
            self.fail('stdout nie jest JSON-em: ' + self.text[:300])
        self.assertEqual(data['schema'], 1)
        return data

    def entry(self, data, name):
        found = [item for item in data['projects'] if name in item['project']]
        self.assertEqual(len(found), 1, data['projects'])
        return found[0]

    def registry(self, *items):
        lines = ['version: 1', 'decisions:']
        for key, value, day in items:
            lines += [f'  - cause_key: {key}', f'    decision: {value}', f'    date: {day}']
            if value == 'approved':
                lines += ['    owner: skill', '    case: tests/x.py']
        self.write('docs/retro/decisions.yaml', '\n'.join(lines) + '\n')

    # ---- K20 ----
    def test_broken_project_does_not_stop_the_others(self):
        self.project('good-one')
        self.pair()
        self.project('broken-one')
        self.write('.github/.skillsync.yaml', 'version: 2\n')
        data = self.run_all()
        self.assertEqual(set(data), {'schema', 'status', 'projects', 'totals', 'library_pairs',
                                     'library_pairs_truncated'})
        self.assertEqual((data['library_pairs'], data['library_pairs_truncated']), ([], False))
        self.assertEqual(data['status'], 'PASS')
        good, bad = self.entry(data, 'good-one'), self.entry(data, 'broken-one')
        self.assertEqual(good['status'], 'PASS')
        self.assertEqual([c['cause_key'] for c in good['candidates']], ['flaky'])
        self.assertEqual(set(good['coverage']), COVERAGE_KEYS)
        self.assertEqual(bad['status'], 'BLOCKED')
        self.assertTrue(isinstance(bad['reason'], str) and bad['reason'])

    def test_all_has_neither_proposals_nor_retro_coverage(self):
        """K7: `retro --all` zostaje bez `proposals`, `proposals_truncated` i `coverage.retro`."""
        self.project('with-proposals')
        self.pair()
        ProposalBase.make_run(self, 'r1', states=(('P1', 'open'), ('P2', 'rejected')))
        data = self.run_all()
        self.assertNotIn('proposals', data)
        self.assertNotIn('proposals_truncated', data)
        project = self.entry(data, 'with-proposals')
        self.assertEqual(project['status'], 'PASS')
        self.assertNotIn('proposals', project)
        self.assertNotIn('proposals_truncated', project)
        self.assertEqual(set(project['coverage']), COVERAGE_KEYS)
        self.assertNotIn('retro', project['coverage'])

    def test_all_ignores_all_states_silently(self):
        """Obecne zachowanie: `--all-states` jest przyjęte przez parser, ale `retro --all` nie ma sekcji propozycji,
        więc wynik jest identyczny jak bez flagi."""
        self.project('with-proposals')
        self.pair()
        ProposalBase.make_run(self, 'r1', states=(('P1', 'open'), ('P2', 'rejected')))
        plain = self.run_all()
        flagged = self.run_all('--all-states')
        self.assertEqual(flagged, plain)
        self.assertNotIn('proposals', flagged)

    def test_unparsable_manifest_is_a_blocked_project_not_a_crash(self):
        self.project('good-one')
        self.pair()
        self.project('broken-one')
        self.write('.github/.skillsync.yaml', 'sync: [unclosed\n')
        data = self.run_all()
        self.assertEqual(data['status'], 'PASS')
        self.assertEqual(self.entry(data, 'broken-one')['status'], 'BLOCKED')

    def test_project_without_runs_is_not_run(self):
        self.project('good-one')
        self.pair()
        self.project('idle-one')
        data = self.run_all()
        self.assertEqual(self.entry(data, 'idle-one')['status'], 'NOT_RUN')
        self.assertEqual(data['status'], 'PASS')

    def test_zero_projects_is_not_run(self):
        data = self.run_all()
        self.assertEqual((data['status'], data['projects']), ('NOT_RUN', []))
        self.assertEqual(data['totals'], {'approved': 0, 'rejected': 0, 'recurred': 0, 'open': 0})

    def excluding(self, *paths):
        self.config = self.use_layers(discover_paths=[str(self.repos)], tags={}, discover_exclude=list(paths))

    def archive_fixture(self):
        self.project('live-one')
        self.pair()
        self.project('old-one', under=self.repos / 'archive' / 'org')
        self.pair()

    def test_nothing_is_excluded_by_default_even_under_repos_archive(self):
        self.archive_fixture()
        data = self.run_all()
        self.assertEqual(sorted(item['project'].rsplit('/', 1)[-1] for item in data['projects']),
                         ['live-one', 'old-one'])

    def test_empty_discover_exclude_excludes_nothing(self):
        self.archive_fixture()
        self.excluding()
        self.assertEqual(len(self.run_all()['projects']), 2)

    def test_discover_exclude_skips_the_path_and_its_subdirectories(self):
        self.archive_fixture()
        self.excluding(str(self.repos / 'archive'))
        data = self.run_all()
        self.assertEqual(len(data['projects']), 1, data['projects'])
        self.assertIn('live-one', data['projects'][0]['project'])
        self.assertNotIn('old-one', self.text)

    def test_discover_exclude_may_name_a_project_itself(self):
        self.archive_fixture()
        self.excluding(str(self.repos / 'archive' / 'org' / 'old-one'))
        data = self.run_all()
        self.assertEqual(len(data['projects']), 1, data['projects'])
        self.assertNotIn('old-one', self.text)

    def test_discover_exclude_expands_tilde(self):
        self.archive_fixture()
        self.excluding('~/Repos/archive')
        data = self.run_all()
        self.assertEqual(len(data['projects']), 1, data['projects'])
        self.assertNotIn('old-one', self.text)

    def test_discover_exclude_does_not_hide_a_sibling_with_a_common_prefix(self):
        self.archive_fixture()
        self.project('archive-extra')
        self.pair()
        self.excluding(str(self.repos / 'archive'))
        self.assertEqual(sorted(item['project'].rsplit('/', 1)[-1] for item in self.run_all()['projects']),
                         ['archive-extra', 'live-one'])

    def test_wrong_discover_exclude_type_is_blocked_with_a_fixed_reason(self):
        self.archive_fixture()
        for bad in ('archive', {'a': 1}, [1], ['']):
            self.config = self.use_layers(discover_paths=[str(self.repos)], tags={}, discover_exclude=bad)
            data = self.run_all(code=2)
            self.assertEqual(data['status'], 'BLOCKED')
            self.assertEqual(data['reason'], 'Konfiguracja maszyny: discover_exclude musi być listą niepustych ścieżek')
            self.assertNotIn(str(self.base), self.text)

    def test_exclusion_given_as_a_symlink_excludes_its_target(self):
        self.archive_fixture()
        link = self.base / 'link-to-archive'
        os.symlink(self.repos / 'archive', link)
        self.excluding(str(link))
        data = self.run_all()
        self.assertEqual([item['project'].rsplit('/', 1)[-1] for item in data['projects']], ['live-one'])

    def test_exclusion_by_target_applies_to_a_project_reached_through_a_symlink(self):
        self.archive_fixture()
        link = self.base / 'repos-link'
        os.symlink(self.repos, link)
        self.config = self.use_layers(discover_paths=[str(link)], tags={},
                                      discover_exclude=[str(self.repos / 'archive')])
        data = self.run_all()
        self.assertEqual([item['project'].rsplit('/', 1)[-1] for item in data['projects']], ['live-one'])

    def test_nonexistent_exclusion_excludes_nothing_without_error(self):
        self.archive_fixture()
        self.excluding(str(self.repos / 'no-such-dir'))
        self.assertEqual(len(self.run_all()['projects']), 2)

    def test_unresolvable_exclusion_is_blocked_with_a_fixed_reason(self):
        self.archive_fixture()
        loop_a, loop_b = self.base / 'loop-a', self.base / 'loop-b'
        os.symlink(loop_b, loop_a)
        os.symlink(loop_a, loop_b)
        for bad in ('~no-such-user-zz/x', str(loop_a / 'x')):
            self.excluding(bad)
            data = self.run_all(code=2)
            self.assertEqual(data['reason'],
                             'Konfiguracja maszyny: discover_exclude zawiera ścieżkę, której nie da się rozwiązać')
            self.assertNotIn(str(self.base), self.text)

    def test_unresolvable_project_path_counts_as_excluded_without_a_traceback(self):
        from skillsync_workflow import retro
        loop_a, loop_b = self.base / 'loop-a', self.base / 'loop-b'
        os.symlink(loop_b, loop_a)
        os.symlink(loop_a, loop_b)
        self.assertTrue(retro.is_excluded(loop_a / 'x', [self.base / 'elsewhere']))
        self.assertFalse(retro.is_excluded(self.base / 'fine', [self.base / 'elsewhere']))

    def test_user_texts_do_not_name_a_person_or_a_machine_path(self):
        from skillsync_workflow import retro
        self.assertNotIn(PERSON, retro.PROPOSAL)
        for command in ('retro', 'retro-decide'):
            result = subprocess.run([sys.executable, str(CLI), command, '--help'], capture_output=True, text=True,
                                    encoding='utf-8', timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(PERSON, result.stdout)
            self.assertNotIn('Repos/archive', result.stdout)
        from skillsync_workflow import retro_scan
        for module in (retro, retro_scan):
            source = Path(module.__file__).read_text(encoding='utf-8')
            self.assertNotIn(PERSON, source)
            self.assertNotIn('Repos/' + 'archive', source)

    def test_repo_and_its_worktree_are_one_entry_the_main_checkout(self):
        main = self.project('primary-repo')
        self.git('init', '-q')
        self.git('config', 'user.email', 'tests@example.invalid')
        self.git('config', 'user.name', 'Contract Tests')
        self.git('add', '.')
        self.git('commit', '-qm', 'baseline')
        linked = self.repos / 'zz-linked'
        self.git('worktree', 'add', '-q', str(linked))
        self.assertTrue((linked / '.github/.skillsync.yaml').is_file())
        data = self.run_all()
        self.assertEqual(len(data['projects']), 1, data['projects'])
        self.assertIn('primary-repo', data['projects'][0]['project'])
        self.assertNotIn('zz-linked', data['projects'][0]['project'])
        self.assertEqual(main, self.root)

    def test_worktree_dedup_is_not_confused_by_two_unrelated_repos(self):
        for name in ('repo-one', 'repo-two'):
            self.project(name)
            self.git('init', '-q')
            self.git('config', 'user.email', 'tests@example.invalid')
            self.git('config', 'user.name', 'Contract Tests')
            self.git('add', '.')
            self.git('commit', '-qm', 'baseline')
        data = self.run_all()
        self.assertEqual(sorted(p['project'].rsplit('/', 1)[-1] for p in data['projects']), ['repo-one', 'repo-two'])

    # ---- K21 ----
    def test_same_key_in_two_projects_suggests_library_scope(self):
        for name in ('alpha-one', 'beta-two'):
            self.project(name)
            self.pair()
        data = self.run_all()
        for name in ('alpha-one', 'beta-two'):
            candidates = self.entry(data, name)['candidates']
            self.assertEqual([c['cause_key'] for c in candidates], ['flaky'])
            self.assertEqual(candidates[0]['suggested_owner_scope'], 'library')

    def test_other_keys_do_not_get_the_suggestion(self):
        self.project('alpha-one')
        self.pair(key='flaky')
        self.pair(key='shared')
        self.project('beta-two')
        self.pair(key='shared')
        self.pair(key='local-only')
        data = self.run_all()
        scopes = {(name, c['cause_key']): c.get('suggested_owner_scope')
                  for name in ('alpha-one', 'beta-two') for c in self.entry(data, name)['candidates']}
        self.assertEqual(scopes, {('alpha-one', 'flaky'): None, ('alpha-one', 'shared'): 'library',
                                  ('beta-two', 'shared'): 'library', ('beta-two', 'local-only'): None})

    def test_single_project_has_no_suggestion_field(self):
        self.project('alpha-one')
        self.pair()
        candidate = self.entry(self.run_all(), 'alpha-one')['candidates'][0]
        self.assertNotIn('suggested_owner_scope', candidate)

    def test_repo_and_its_worktree_do_not_make_a_library_suggestion(self):
        self.project('primary-repo')
        self.git('init', '-q')
        self.git('config', 'user.email', 'tests@example.invalid')
        self.git('config', 'user.name', 'Contract Tests')
        self.git('add', '.')
        self.git('commit', '-qm', 'baseline')
        self.pair()
        linked = self.repos / 'zz-linked'
        self.git('worktree', 'add', '-q', str(linked))
        main, self.root = self.root, linked
        self.pair()  # ten sam klucz także w worktree
        self.root = main
        data = self.run_all()
        self.assertEqual(len(data['projects']), 1, data['projects'])
        candidate = data['projects'][0]['candidates'][0]
        self.assertEqual(candidate['cause_key'], 'flaky')
        self.assertNotIn('suggested_owner_scope', candidate)

    def test_aliases_count_as_the_same_key_across_projects(self):
        (self.library / 'workflow').mkdir(exist_ok=True)
        (self.library / 'workflow/cause-keys.yaml').write_text(yaml.safe_dump(
            {'version': 1, 'keys': {'flaky-tests': {'description': 'd', 'aliases': ['flaky-test']}}}), encoding='utf-8')
        self.project('alpha-one')
        self.pair(key='flaky-test')
        self.project('beta-two')
        self.pair(key='flaky-tests')
        data = self.run_all()
        for name in ('alpha-one', 'beta-two'):
            candidate = self.entry(data, name)['candidates'][0]
            self.assertEqual((candidate['cause_key'], candidate['suggested_owner_scope']), ('flaky-tests', 'library'))

    def test_project_without_a_layer_rule_is_blocked_without_a_path_leak(self):
        # 2.3 (K3): the library comes only from the machine config, so the unusable per-project setup is now
        # "no `layer_rules` entry matches" (was: a non-string manifest library); the reason must not leak paths.
        self.project('good-one')
        self.pair()
        broken = self.project('broken-one')
        config = yaml.safe_load(self.config.read_text(encoding='utf-8'))
        config['layer_rules'] = [{'match': [self.repos.resolve().as_posix() + '/good-*'], 'use': ['core']}]
        self.config.write_text(yaml.safe_dump(config), encoding='utf-8')
        data = self.run_all()
        self.assertEqual(data['status'], 'PASS')
        self.assertEqual(self.entry(data, 'good-one')['status'], 'PASS')
        entry = self.entry(data, 'broken-one')
        self.assertEqual(entry['status'], 'BLOCKED')
        self.assertIn('layer_rules', entry['reason'])
        self.assertNotIn(str(broken), entry['reason'])
        self.assertNotIn(str(broken.resolve()), entry['reason'])
        self.assertNotIn(str(self.home), entry['reason'])

    def test_manifest_library_setting_is_ignored_by_retro_all(self):
        # K3: `sync: {library: ...}` in a manifest is ignored (doctor warns); even a malformed value is no error.
        self.project('good-one')
        self.pair()
        self.project('odd-one')
        self.write('.github/.skillsync.yaml', 'sync: {library: 5}\n')
        data = self.run_all()
        self.assertEqual(data['status'], 'PASS')
        self.assertNotEqual(self.entry(data, 'odd-one')['status'], 'BLOCKED', data)

    def test_totals_count_aliased_decisions_once(self):
        (self.library / 'workflow').mkdir(exist_ok=True)
        (self.library / 'workflow/cause-keys.yaml').write_text(yaml.safe_dump(
            {'version': 1, 'keys': {'flaky-tests': {'description': 'd', 'aliases': ['flaky-test']}}}), encoding='utf-8')
        self.project('alpha-one')
        self.registry(('flaky-test', 'rejected', '2026-10-01'), ('flaky-tests', 'rejected', '2026-10-02'))
        data = self.run_all()
        self.assertEqual(data['totals']['rejected'], 1)

    # ---- K22 ----
    def test_totals_count_registry_entries_and_candidate_states(self):
        self.project('alpha-one')
        self.pair(key='k-ready')
        self.pair(key='k-needs-owner', owner=None)
        self.pair(key='k-needs-case', state='none')
        self.pair(key='k-decided')
        self.pair(key='k-recurred')
        self.registry(('k-decided', 'approved', '2026-10-29'), ('k-recurred', 'rejected', '2026-10-01'),
                      ('k-ghost', 'approved', '2026-10-01'))
        self.project('beta-two')
        self.pair(key='k-other')
        self.registry(('k-other', 'rejected', '2026-10-29'))
        data = self.run_all()
        statuses = {c['cause_key']: c['status'] for c in self.entry(data, 'alpha-one')['candidates']}
        self.assertEqual(statuses, {'k-ready': 'ready', 'k-needs-owner': 'needs_owner', 'k-needs-case': 'needs_case',
                                    'k-decided': 'decided', 'k-recurred': 'recurred'})
        self.assertEqual(data['totals'], {'approved': 2, 'rejected': 2, 'recurred': 1, 'open': 3})

    def test_totals_ignore_blocked_projects(self):
        self.project('alpha-one')
        self.pair()
        self.project('broken-one')
        self.write('.github/.skillsync.yaml', 'version: 2\n')
        self.assertEqual(self.run_all()['totals'], {'approved': 0, 'rejected': 0, 'recurred': 0, 'open': 1})

    # ---- odmowy i tylko odczyt ----
    def test_all_with_run_id_is_blocked(self):
        self.project('alpha-one')
        self.pair()
        data = self.run_all('--run-id', 'x', code=2)
        self.assertEqual(data['status'], 'BLOCKED')
        self.assertTrue(isinstance(data['reason'], str) and data['reason'])

    def test_all_with_explicit_path_is_blocked(self):
        project = self.project('alpha-one')
        self.pair()
        data = self.run_all(project, code=2)
        self.assertEqual(data['status'], 'BLOCKED')
        self.assertTrue(isinstance(data['reason'], str) and data['reason'])

    def test_all_writes_nothing(self):
        self.project('alpha-one')
        self.pair()
        self.registry(('flaky', 'rejected', '2026-10-01'))
        self.project('broken-one')
        self.write('.github/.skillsync.yaml', 'version: 2\n')
        before = snapshot(self.base)
        self.run_all()
        self.assertEqual(snapshot(self.base), before)

    def test_layer_option_applies_to_every_project(self):
        other = make_layer(self.base / 'other-library', 'core', 'core')
        (other / 'workflow').mkdir(parents=True)
        (other / 'workflow/cause-keys.yaml').write_text(yaml.safe_dump(
            {'version': 1, 'keys': {'flaky-tests': {'description': 'd', 'aliases': ['flaky-test']}}}), encoding='utf-8')
        self.project('alpha-one')
        self.run_file('x', self.case('x', 'flaky-test'), rec('done'))
        self.run_file('y', self.case('y', 'flaky-tests'), rec('done'))
        self.assertEqual(self.entry(self.run_all(), 'alpha-one')['candidates'], [])
        candidates = self.entry(self.run_all('--layer', f'core={other}'), 'alpha-one')['candidates']
        self.assertEqual([(c['cause_key'], c['runs']) for c in candidates], [('flaky-tests', 2)])


# ---------------------------------------------------------------------------------------------------------------
# R2 krok 1.2 (K8): `retro --all` pokazuje pary (klucz, cel) sygnałów `repo: library` z co najmniej dwóch projektów
# ---------------------------------------------------------------------------------------------------------------
LIB_TARGET = 'skills/feature/references/x.md'
PAIR_KEYS = {'key', 'target', 'projects'}


class LibraryPairs(CandidateBase):
    """Jak `RetroAll`: projekty pod `$HOME/Repos`, lista z `discover_paths`; przebiegi tylko z `retro-signals.json`."""

    def setUp(self):
        super().setUp()
        self.home = self.base / 'home'
        self.repos = self.home / 'Repos'
        self.repos.mkdir(parents=True)
        self.config = self.use_layers(discover_paths=[str(self.repos)], tags={})

    def project(self, name, *runs, under=None):
        """Projekt z manifestem; `runs`: (nazwa przebiegu, [(klucz, repo, cel), ...])."""
        self.root = (under or self.repos) / name
        self.root.mkdir(parents=True)
        self.save()
        for run, signals in runs:
            self.signals_run(run, signals)
        return self.root

    def signals_run(self, run, signals, created='2026-11-01T10:00:00Z'):
        listed = [{'id': f'S{n}', 'key': key, 'repo': repo, 'target': target}
                  for n, (key, repo, target) in enumerate(signals, 1)]
        self.write(f'.skillsync/runs/{run}/retro-signals.json', json.dumps(
            {'schema': 1, 'run_id': run, 'created_at': created, 'signals': listed, 'proposals': []}))

    def run_all(self, code=0):
        env = {**os.environ, 'HOME': str(self.home), 'USERPROFILE': str(self.home), 'SKILLSYNC_CONFIG': str(self.config)}
        result = subprocess.run([sys.executable, str(CLI), 'retro', '--all', '--json'], capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=40, cwd=self.base, env=env)
        self.text = result.stdout + result.stderr
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        self.assertEqual(result.returncode, code, self.text)
        data = json.loads(result.stdout)
        self.check_pairs(data)
        return data

    def run_in_process(self, *patches):
        """`retro --all` w tym procesie, z podmienionymi stałymi; stdout przechwycony."""
        import argparse
        import contextlib
        import io
        from unittest import mock
        from skillsync_workflow import retro
        parser = argparse.ArgumentParser()
        retro.register(parser.add_subparsers())
        args = parser.parse_args(['retro', '--all', '--json'])
        out = io.StringIO()
        env = {'HOME': str(self.home), 'USERPROFILE': str(self.home), 'SKILLSYNC_CONFIG': str(self.config)}
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, env))
            for patch in patches:
                stack.enter_context(patch)
            with contextlib.redirect_stdout(out):
                code = args.func(args)
        self.assertEqual(code, 0, out.getvalue())
        data = json.loads(out.getvalue())
        self.check_pairs(data)
        return data

    def check_pairs(self, data):
        self.assertIsInstance(data['library_pairs'], list)
        self.assertIs(type(data['library_pairs_truncated']), bool)
        for item in data['library_pairs']:
            self.assertEqual(set(item), PAIR_KEYS)
            self.assertIs(type(item['projects']), int)
            self.assertGreaterEqual(item['projects'], 2)

    def pairs(self, data):
        return [(item['key'], item['target'], item['projects']) for item in data['library_pairs']]

    def two_projects(self, signals=(('flaky', 'library', LIB_TARGET),)):
        self.project('alpha-one', ('r1', list(signals)))
        self.project('beta-two', ('r1', list(signals)))

    # ---- zawartość listy ----
    def test_pair_in_two_projects_is_listed_with_the_project_count(self):
        self.two_projects()
        data = self.run_all()
        self.assertEqual(self.pairs(data), [('flaky', LIB_TARGET, 2)])
        self.assertIs(data['library_pairs_truncated'], False)

    def test_count_grows_with_the_number_of_projects_not_runs_or_signals(self):
        self.project('alpha-one', ('r1', [('flaky', 'library', LIB_TARGET)] * 2), ('r2', [('flaky', 'library', LIB_TARGET)]))
        self.project('beta-two', ('r1', [('flaky', 'library', LIB_TARGET)]))
        self.project('gamma-three', ('r1', [('flaky', 'library', LIB_TARGET)]))
        self.assertEqual(self.pairs(self.run_all()), [('flaky', LIB_TARGET, 3)])

    def test_pair_in_a_single_project_is_not_listed(self):
        self.project('alpha-one', ('r1', [('flaky', 'library', LIB_TARGET)]), ('r2', [('flaky', 'library', LIB_TARGET)]))
        self.project('beta-two', ('r1', [('flaky', 'library', 'skills/other.md'), ('other-key', 'library', LIB_TARGET)]))
        self.assertEqual(self.run_all()['library_pairs'], [])

    def test_project_scope_signals_are_never_listed(self):
        self.project('alpha-one', ('r1', [('flaky', 'project', 'docs/workflow.md')]))
        self.project('beta-two', ('r1', [('flaky', 'project', 'docs/workflow.md')]))
        self.project('gamma-three', ('r1', [('flaky', 'library', 'docs/workflow.md')]))
        self.assertEqual(self.run_all()['library_pairs'], [])

    def test_signals_without_target_or_repo_are_never_listed(self):
        self.two_projects(signals=[('flaky', None, None)])
        self.assertEqual(self.run_all()['library_pairs'], [])

    def test_order_is_by_count_then_key_then_target(self):
        both = [('zeta', 'library', 'a.md'), ('alpha', 'library', 'b.md'), ('alpha', 'library', 'a.md')]
        self.project('alpha-one', ('r1', both))
        self.project('beta-two', ('r1', both))
        self.project('gamma-three', ('r1', [('zeta', 'library', 'a.md')]))
        self.assertEqual(self.pairs(self.run_all()),
                         [('zeta', 'a.md', 3), ('alpha', 'a.md', 2), ('alpha', 'b.md', 2)])

    def test_excluded_project_does_not_count(self):
        self.config = self.use_layers(discover_paths=[str(self.repos)], tags={},
                                      discover_exclude=[str(self.repos / 'archive')])
        self.project('alpha-one', ('r1', [('flaky', 'library', LIB_TARGET)]))
        self.project('old-one', ('r1', [('flaky', 'library', LIB_TARGET)]), under=self.repos / 'archive' / 'org')
        data = self.run_all()
        self.assertEqual(data['library_pairs'], [])
        self.assertNotIn('old-one', self.text)

    def test_repo_and_its_worktree_count_as_one_project(self):
        main = self.project('primary-repo', ('r1', [('flaky', 'library', LIB_TARGET)]))
        self.git('init', '-q')
        self.git('config', 'user.email', 'tests@example.invalid')
        self.git('config', 'user.name', 'Contract Tests')
        self.git('add', '.github')
        self.git('commit', '-qm', 'baseline')
        linked = self.repos / 'zz-linked'
        self.git('worktree', 'add', '-q', str(linked))
        self.root = linked
        self.signals_run('r2', [('flaky', 'library', LIB_TARGET)])
        self.assertEqual(self.run_all()['library_pairs'], [], 'repo i worktree to jeden projekt')
        self.project('beta-two', ('r1', [('flaky', 'library', LIB_TARGET)]))
        self.assertEqual(self.pairs(self.run_all()), [('flaky', LIB_TARGET, 2)])
        self.assertEqual(main, self.repos / 'primary-repo')

    def test_broken_project_does_not_hide_pairs_of_the_others(self):
        self.two_projects()
        self.project('broken-one')
        self.write('.github/.skillsync.yaml', 'version: 2\n')
        self.assertEqual(self.pairs(self.run_all()), [('flaky', LIB_TARGET, 2)])

    def test_unreadable_run_is_ignored(self):
        self.two_projects()
        self.write('.skillsync/runs/bad/retro-signals.json', '{"schema": 1,')
        self.assertEqual(self.pairs(self.run_all()), [('flaky', LIB_TARGET, 2)])

    # ---- prywatność ----
    def test_pairs_name_no_project_and_no_run_id(self):
        self.project('zebra-secret-name', ('secret-run-id', [('flaky', 'library', LIB_TARGET)]))
        self.project('yak-hidden-name', ('2026-11-04-hidden-run', [('flaky', 'library', LIB_TARGET)]))
        data = self.run_all()
        self.assertEqual(self.pairs(data), [('flaky', LIB_TARGET, 2)])
        text = json.dumps(data['library_pairs'])
        for secret in ('zebra-secret-name', 'yak-hidden-name', 'secret-run-id', 'hidden-run', str(self.base),
                       self.base.name, 'Repos'):
            self.assertNotIn(secret, text)

    def test_target_is_masked_like_evidence_paths(self):
        from skillsync_workflow.retro import _mask_path
        long_segment = 'docs/' + 'ab12' * 10 + '/x.md'
        odd_segment = 'docs/mój plik z spacją.md'
        for target in (long_segment, odd_segment, LIB_TARGET):
            self.assertNotEqual(_mask_path(target) == target, target in (long_segment, odd_segment),
                                'fixture musi mieć cel, który maskowanie zmienia')
        targets = [long_segment, odd_segment, LIB_TARGET]
        self.two_projects(signals=[('flaky', 'library', t) for t in targets])
        data = self.run_all()
        self.assertEqual(sorted(item['target'] for item in data['library_pairs']),
                         sorted(_mask_path(t) for t in targets))
        text = json.dumps(data['library_pairs'], ensure_ascii=False)
        self.assertNotIn('ab12' * 10, text)
        self.assertNotIn('mój plik', text)

    # ---- library_pairs_truncated ----
    def test_truncated_is_false_by_default_with_and_without_pairs(self):
        self.assertIs(self.run_all()['library_pairs_truncated'], False)
        self.two_projects()
        self.assertIs(self.run_all()['library_pairs_truncated'], False)

    def test_default_project_limit_is_200(self):
        from skillsync_workflow import retro_scan
        self.assertEqual(retro_scan.MAX_PROJECTS, 200)

    def test_project_limit_marks_the_pairs_truncated(self):
        from unittest import mock
        from skillsync_workflow import retro_scan
        self.two_projects()
        cut = self.run_in_process(mock.patch.object(retro_scan, 'MAX_PROJECTS', 1))
        self.assertIs(cut['library_pairs_truncated'], True)
        self.assertEqual(cut['library_pairs'], [], 'z jednego przeczytanego projektu nie ma pary')
        exact = self.run_in_process(mock.patch.object(retro_scan, 'MAX_PROJECTS', 2))
        self.assertIs(exact['library_pairs_truncated'], False)
        self.assertEqual(self.pairs(exact), [('flaky', LIB_TARGET, 2)])

    def test_run_limit_marks_the_pairs_truncated(self):
        from unittest import mock
        from skillsync_workflow import retro_scan
        self.project('alpha-one', ('r1', [('flaky', 'library', LIB_TARGET)]), ('r2', [('other', 'library', 'a.md')]))
        self.project('beta-two', ('r1', [('flaky', 'library', LIB_TARGET)]))
        cut = self.run_in_process(mock.patch.object(retro_scan, 'MAX_RUNS', 1))
        self.assertIs(cut['library_pairs_truncated'], True)
        self.assertEqual(cut['library_pairs'], [])
        exact = self.run_in_process(mock.patch.object(retro_scan, 'MAX_RUNS', 2))
        self.assertIs(exact['library_pairs_truncated'], False)
        self.assertEqual(self.pairs(exact), [('flaky', LIB_TARGET, 2)])

    def test_byte_budget_marks_the_pairs_truncated(self):
        from unittest import mock
        from skillsync_workflow import retro_scan
        self.two_projects()
        size = (self.repos / 'alpha-one/.skillsync/runs/r1/retro-signals.json').stat().st_size
        cut = self.run_in_process(mock.patch.object(retro_scan, 'MAX_SCAN_BYTES', size))
        self.assertIs(cut['library_pairs_truncated'], True)
        exact = self.run_in_process(mock.patch.object(retro_scan, 'MAX_SCAN_BYTES', 2 * size))
        self.assertIs(exact['library_pairs_truncated'], False)
        self.assertEqual(self.pairs(exact), [('flaky', LIB_TARGET, 2)])

    def test_read_error_of_one_project_marks_the_pairs_truncated_and_the_rest_is_read(self):
        from unittest import mock
        from skillsync_workflow import retro_scan
        original = retro_scan.library_signals

        def failing(root, aliases, budget):
            if root.name == 'gamma-three':
                raise ValueError('Nie można odczytać magazynu przebiegów')
            return original(root, aliases, budget)

        self.two_projects()
        self.project('gamma-three', ('r1', [('flaky', 'library', LIB_TARGET)]))
        data = self.run_in_process(mock.patch.object(retro_scan, 'library_signals', failing))
        self.assertIs(data['library_pairs_truncated'], True)
        self.assertEqual(self.pairs(data), [('flaky', LIB_TARGET, 2)], 'reszta projektów jest czytana')

    def test_exhausted_git_budget_marks_the_pairs_truncated(self):
        from unittest import mock
        from skillsync_workflow import retro
        self.two_projects()
        data = self.run_in_process(mock.patch.object(retro, 'GIT_BUDGET', 0))
        self.assertIs(data['library_pairs_truncated'], True)

    def test_alias_in_one_project_and_canonical_key_in_another_make_one_pair(self):
        (self.library / 'workflow').mkdir(parents=True, exist_ok=True)
        (self.library / 'workflow/cause-keys.yaml').write_text(yaml.safe_dump(
            {'version': 1, 'keys': {'flaky-tests': {'description': 'd', 'aliases': ['flaky-test']}}}), encoding='utf-8')
        self.project('alpha-one', ('r1', [('flaky-test', 'library', LIB_TARGET)]))
        self.project('beta-two', ('r1', [('flaky-tests', 'library', LIB_TARGET)]))
        data = self.run_all()
        self.assertEqual(self.pairs(data), [('flaky-tests', LIB_TARGET, 2)])

    def test_out_of_range_created_at_does_not_stop_the_others(self):
        for bad in ('0001-01-01T00:00:00+01:00', '9999-12-31T23:59:59-01:00'):
            with self.subTest(bad):
                shutil.rmtree(self.repos)
                self.repos.mkdir()
                self.two_projects()
                self.project('gamma-three', ('good', [('flaky', 'library', LIB_TARGET)]))
                self.signals_run('bad', [('flaky', 'library', LIB_TARGET)], created=bad)
                data = self.run_all()
                self.assertEqual(self.pairs(data), [('flaky', LIB_TARGET, 3)], 'projekt z błędną datą jest czytany dalej')
                self.assertIs(data['library_pairs_truncated'], False)
                self.assertEqual(len(data['projects']), 3)

    def test_keys_not_matching_the_cause_key_pattern_are_never_listed(self):
        bad_keys = ['Kowalski Jan fix', 'UPPER-case', 'a' * 300, 'x_y', '-lead']
        signals = [(key, 'library', LIB_TARGET) for key in bad_keys] + [('flaky', 'library', LIB_TARGET)]
        self.two_projects(signals=signals)
        data = self.run_all()
        self.assertEqual(self.pairs(data), [('flaky', LIB_TARGET, 2)])
        for key in bad_keys:
            self.assertNotIn(key, json.dumps(data['library_pairs']))

    def test_projects_with_the_same_directory_name_outside_repos_count_separately(self):
        elsewhere = self.base / 'elsewhere'
        elsewhere.mkdir()
        self.config = self.use_layers(discover_paths=[str(self.repos), str(elsewhere)], tags={})
        self.project('app', ('r1', [('flaky', 'library', LIB_TARGET)]))
        self.project('app', ('r1', [('flaky', 'library', LIB_TARGET)]), under=elsewhere)
        data = self.run_all()
        self.assertEqual(sorted(p['project'] for p in data['projects']), ['app', 'app'])
        self.assertEqual(self.pairs(data), [('flaky', LIB_TARGET, 2)])

    def test_unexpected_error_of_the_reader_marks_the_pairs_truncated_and_passes(self):
        from unittest import mock
        from skillsync_workflow import retro_scan
        self.two_projects()
        data = self.run_in_process(mock.patch.object(retro_scan, 'library_signals',
                                                     side_effect=RuntimeError('nieoczekiwany')))
        self.assertIs(data['library_pairs_truncated'], True)
        self.assertEqual(data['library_pairs'], [])
        self.assertEqual(len(data['projects']), 2)

    def test_unverifiable_project_directory_is_absent_and_the_rest_is_listed(self):
        from unittest import mock
        from pathlib import Path
        self.two_projects()
        self.project('zed-three', ('r1', [('flaky', 'library', LIB_TARGET)]))
        real = Path.is_file

        def is_file(path, *args, **kwargs):
            if 'zed-three' in path.parts and path.name == '.skillsync.yaml':
                raise PermissionError(13, 'Permission denied', str(path))
            return real(path, *args, **kwargs)

        data = self.run_in_process(mock.patch.object(Path, 'is_file', is_file))
        self.assertEqual(sorted(p['project'] for p in data['projects']), ['alpha-one', 'beta-two'])
        self.assertEqual(self.pairs(data), [('flaky', LIB_TARGET, 2)])

    def test_reading_pairs_writes_nothing(self):
        self.two_projects()
        before = snapshot(self.base)
        self.run_all()
        self.assertEqual(snapshot(self.base), before)



# ---------------------------------------------------------------------------------------------------------------
# R2 krok 1.3t (K5, K6): sekcja `proposals:` rejestru i `retro-decide --proposal`
# ---------------------------------------------------------------------------------------------------------------
PROPOSAL_TARGET = 'skills/feature/references/x.md'
SHA_ONE = '0123456789abcdef0123456789abcdef01234567'
SHA_TWO = 'f' * 40
UNRESOLVED_STATES = ('open', 'revising', 'approved', 'deferred', 'stale', 'failed', 'handoff')
BLOCKED_PROPOSAL_KEYS = {'schema', 'status', 'reason', 'writes'}
SKIPPED_WRITES = {'run': 'skipped', 'registry': 'skipped'}


class ProposalBase(CandidateBase):
    """Przebieg z `retro-signals.json` i `retro-decisions.json` oraz rejestr z sekcją `proposals:`."""
    PATH = 'docs/retro/decisions.yaml'

    # ---- uruchamianie ----
    def decide(self, *args, code=0, path=None):
        command = [sys.executable, str(CLI), 'retro-decide', str(path or self.root), *map(str, args), '--json']
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=30, cwd=self.root)
        self.text = result.stdout + result.stderr
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        self.assertEqual(result.returncode, code, self.text)
        try:
            data = json.loads(result.stdout)
        except ValueError:
            self.fail('stdout nie jest JSON-em: ' + self.text[:300])
        self.assertEqual(data['schema'], 1)
        return data

    def prop(self, ident, value, *extra, code=0, path=None):
        return self.decide('--proposal=' + ident, '--decision', value, *extra, code=code, path=path)

    def in_process(self, *args, patches=()):
        """`retro-decide` w tym procesie (podmiana datetime, zapisów, os.replace); zwraca (kod, wynik)."""
        import argparse
        import contextlib
        import io
        from skillsync_workflow import retro
        parser = argparse.ArgumentParser()
        retro.register(parser.add_subparsers())
        parsed = parser.parse_args(['retro-decide', str(self.root), *map(str, args), '--json'])
        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            with contextlib.redirect_stdout(out):
                code = parsed.func(parsed)
        self.text = out.getvalue()
        return code, json.loads(out.getvalue())

    # ---- fixture'y ----
    def make_run(self, run_id='r1', states=(('P1', 'open'),), extra=(), repo='library', target=PROPOSAL_TARGET,
                 decisions_file=True, root=None):
        """Przebieg z propozycjami z `states` (+ `extra`: numery bez wpisów stanu). `states`: (P<n>, stan[, ref[, diff]])."""
        root = Path(root or self.root)
        folder = root / '.skillsync/runs' / run_id
        folder.mkdir(parents=True, exist_ok=True)
        numbers = list(dict.fromkeys([item[0] for item in states] + list(extra)))
        proposals = [{'id': n, 'signals': [], 'owner': 'skill', 'repo': repo, 'target': target, 'title': 'Tytuł ' + n,
                      'rationale': 'uzasadnienie', 'diff': f'retro-{n}.diff', 'diff_by': 'reviewer',
                      'self_assessed': False, 'previously_rejected': None, 'recurrence_of': None} for n in numbers]
        (folder / 'retro-signals.json').write_text(json.dumps(
            {'schema': 1, 'run_id': run_id, 'created_at': '2026-11-05T10:00:00Z', 'assessor': 'reviewer',
             'signals': [], 'proposals': proposals}), encoding='utf-8')
        if decisions_file:
            entries = []
            for number, state, *more in states:
                entry = {'proposal': number, 'state': state, 'date': '2026-11-01',
                         'diff': (more[1] if len(more) > 1 else f'retro-{number}.diff')}
                if more and more[0] is not None:
                    entry['ref'] = more[0]
                entry['note'] = None
                entries.append(entry)
            (folder / 'retro-decisions.json').write_text(json.dumps(
                {'schema': 1, 'run_id': run_id, 'decisions': entries}, indent=2) + '\n', encoding='utf-8')
        return folder

    def run_decisions(self, run_id='r1', root=None):
        path = Path(root or self.root) / '.skillsync/runs' / run_id / 'retro-decisions.json'
        return json.loads(path.read_text(encoding='utf-8'))

    @staticmethod
    def pentry(proposal, decision='rejected', repo='library', target=PROPOSAL_TARGET, date='2026-11-05', ref=None):
        item = {'proposal': proposal, 'repo': repo, 'target': target, 'decision': decision, 'date': date}
        if ref is not None:
            item['ref'] = ref
        return item

    def put_registry(self, proposals=None, decisions=(), raw=None, root=None):
        data = {'version': 1, 'decisions': list(decisions)}
        if proposals is not None:
            data['proposals'] = proposals
        path = Path(root or self.root) / self.PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw if raw is not None else yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                        encoding='utf-8')
        return path

    def registry_doc(self, root=None):
        return yaml.safe_load((Path(root or self.root) / self.PATH).read_text(encoding='utf-8'))

    def disk(self, root=None):
        """Pełny stan drzewa (bez .git): rodzaj, mtime_ns i bajty każdej pozycji."""
        root = Path(root or self.root)
        result = {}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name != '.git']
            for name in [''] + dirnames + filenames:
                path = os.path.join(dirpath, name) if name else dirpath
                st = os.lstat(path)
                try:
                    data = Path(path).read_bytes() if stat.S_ISREG(st.st_mode) else None
                except PermissionError:
                    data = b'<nieczytelny>'
                result[os.path.relpath(path, root)] = (stat.S_IFMT(st.st_mode), st.st_mtime_ns, data)
        return result

    def tmp_files(self):
        return sorted(os.path.relpath(os.path.join(d, n), self.base) for d, _, names in os.walk(self.base)
                      for n in names if n.endswith('.tmp'))

    def blocked(self, *args, reason_has=None, roots=None):
        """Odmowa K5: kod 2, BLOCKED z `reason`, `writes` oba `skipped`, żadnej zmiany na dysku (migawka przed i po)."""
        roots = roots or [self.root]
        before = [self.disk(root) for root in roots]
        data = self.decide(*args, code=2)
        self.assertEqual(data['status'], 'BLOCKED', data)
        self.assertEqual(set(data), BLOCKED_PROPOSAL_KEYS, data)
        self.assertTrue(isinstance(data['reason'], str) and data['reason'])
        self.assertEqual(data['writes'], SKIPPED_WRITES)
        if reason_has:
            self.assertIn(reason_has, data['reason'])
        self.assertEqual([self.disk(root) for root in roots], before, 'odmowa zmieniła dysk')
        self.assertNotIn(str(self.base), self.text)
        return data

    def blocked_prop(self, ident, value, *extra, **kw):
        return self.blocked('--proposal=' + ident, '--decision', value, *extra, **kw)


class ProposalFlagsAndId(ProposalBase):
    """K5: wykluczenia flag, format `--proposal`, wartości `--decision`."""

    def setUp(self):
        super().setUp()
        self.make_run()

    def test_proposal_excludes_cause_key_owner_and_case(self):
        for flag, value in (('--cause-key', 'flaky'), ('--owner', 'skill'), ('--case', 'tests/x.py')):
            with self.subTest(flag=flag):
                data = self.blocked_prop('r1/P1', 'rejected', flag, value)
                self.assertIn('--proposal', data['reason'])
        self.assertFalse((self.root / self.PATH).exists())

    def test_cause_key_mode_without_proposal_still_works_next_to_a_run(self):
        self.pair()
        data = self.decide('--cause-key', 'flaky', '--decision', 'approved', '--owner', 'skill', '--case', 'x.py')
        self.assertEqual(set(data), {'schema', 'status', 'path', 'decision', 'previous'})
        self.assertEqual(self.run_decisions()['decisions'][0]['state'], 'open', 'tryb --cause-key rusza przebieg')

    def test_decision_must_be_rejected_or_applied(self):
        for value in ('approved', 'REJECTED', 'rejected ', 'open', 'deferred', ''):
            with self.subTest(value=value):
                self.blocked_prop('r1/P1', value)
        self.blocked('--proposal', 'r1/P1')

    def test_valid_id_boundaries(self):
        longest = 'a' * 80
        self.make_run(longest, states=(('P9999', 'open'), ('P1', 'open')))
        self.assertEqual(self.prop(longest + '/P9999', 'rejected')['decision']['proposal'], longest + '/P9999')
        self.assertEqual(self.prop(longest + '/P1', 'rejected')['decision']['proposal'], longest + '/P1')
        edge = 'A0_.-' + 'z' * 20
        self.make_run(edge)
        self.assertEqual(self.prop(edge + '/P1', 'rejected')['status'], 'PASS')

    def test_invalid_ids_are_refused_without_changes(self):
        self.make_run('a' * 81)
        self.make_run('-lead')
        self.make_run('.lead')
        self.make_run('r1', states=(('P1', 'open'),), extra=('P10000',))
        bad = ['r1/P0', 'r1/P10000', 'r1/P01', 'r1/p1', 'r1/P1x', 'r1/P', 'r1/', '/P1', 'r1', 'P1', '',
               'a' * 81 + '/P1', '-lead/P1', '.lead/P1', 'r 1/P1', 'r1/P1 ', ' r1/P1', 'r1\n/P1', 'rą/P1',
               'a/b/P1', '../r1/P1', 'r1/../r1/P1', 'r1\\P1', 'r1/P-1', 'r1/P1/']
        for ident in bad:
            with self.subTest(ident=ident):
                self.blocked_prop(ident, 'rejected')

    def test_id_with_run_id_that_has_a_dot_suffix_is_valid(self):
        self.make_run('r1.2', states=(('P3', 'approved'),))
        data = self.prop('r1.2/P3', 'applied', '--ref', SHA_ONE)
        self.assertEqual(data['decision']['proposal'], 'r1.2/P3')

    def test_missing_run_proposal_or_state_entry_is_refused_without_changes(self):
        self.blocked_prop('nope/P1', 'rejected')
        self.blocked_prop('r1/P2', 'rejected')
        self.make_run('nostate', states=(), extra=('P1',))
        data = self.blocked_prop('nostate/P1', 'rejected')
        self.assertTrue(data['reason'])
        self.make_run('nofile', states=(('P1', 'open'),), decisions_file=False)
        self.blocked_prop('nofile/P1', 'rejected')
        self.make_run('other', states=(('P2', 'open'),))
        self.blocked_prop('other/P1', 'rejected')
        shutil.rmtree(self.root / '.skillsync/runs/r1')
        (self.root / '.skillsync/runs/r1').mkdir()  # katalog bez plików retro
        self.blocked_prop('r1/P1', 'rejected')
        shutil.rmtree(self.root / '.skillsync/runs')
        self.blocked_prop('r1/P1', 'rejected')

    def test_unreadable_run_files_refuse_without_leaking_the_base_path(self):
        folder = self.make_run('mismatch')
        self.edit_signals(folder, run_id='other')
        self.assert_unreadable('mismatch')
        folder = self.make_run('baddec')
        (folder / 'retro-decisions.json').write_text('[]', encoding='utf-8')
        self.assert_unreadable('baddec')

    def test_malformed_json_gives_the_exact_reason(self):
        folder = self.make_run('broken')
        (folder / 'retro-signals.json').write_text('{not json', encoding='utf-8')
        self.assert_unreadable('broken')
        (folder / 'retro-signals.json').write_text('', encoding='utf-8')
        self.assert_unreadable('broken')

    UNREADABLE = 'Pliki retro przebiegu są nieczytelne albo niepoprawne'

    def edit_signals(self, folder, **top):
        path = folder / 'retro-signals.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        data.update(top)
        path.write_text(json.dumps(data), encoding='utf-8')

    def assert_unreadable(self, run):
        data = self.blocked_prop(run + '/P1', 'rejected')
        self.assertEqual(data['reason'], self.UNREADABLE)

    def test_invalid_signals_content_gives_the_exact_reason_and_no_changes(self):
        folder = self.make_run('badtime')
        self.edit_signals(folder, created_at='wczoraj')
        self.assert_unreadable('badtime')
        folder = self.make_run('norepo')
        self.edit_signals(folder, proposals=[{'id': 'P1', 'signals': [], 'target': PROPOSAL_TARGET}])
        self.assert_unreadable('norepo')
        folder = self.make_run('badrepo')
        self.edit_signals(folder, proposals=[{'id': 'P1', 'repo': 'other', 'target': PROPOSAL_TARGET}])
        self.assert_unreadable('badrepo')
        folder = self.make_run('badschema')
        self.edit_signals(folder, schema=2)
        self.assert_unreadable('badschema')

    def test_os_error_while_reading_the_run_does_not_leak_the_path(self):
        from unittest import mock
        from skillsync_workflow import retro_scan
        leaked = str(self.root / '.skillsync/runs/r1/retro-signals.json')
        failing = mock.patch.object(retro_scan, '_json', side_effect=OSError(13, 'denied', leaked))
        before = self.disk()
        code, data = self.in_process('--proposal', 'r1/P1', '--decision', 'rejected', patches=[failing])
        self.assertEqual((code, data['status'], data['writes']), (2, 'BLOCKED', SKIPPED_WRITES))
        self.assertNotIn(str(self.base), self.text)
        self.assertNotIn('denied', self.text)
        self.assertEqual(self.disk(), before)

    @unittest.skipIf(CHMOD_INEFFECTIVE, 'chmod nieskuteczny dla roota')
    def test_unreadable_signals_file_does_not_leak_the_path(self):
        folder = self.make_run('locked')
        (folder / 'retro-signals.json').chmod(0)
        try:
            self.blocked_prop('locked/P1', 'rejected')
        finally:
            (folder / 'retro-signals.json').chmod(0o644)

    def test_run_directory_symlink_is_not_followed(self):
        require_os_symlinks(self)
        outside = self.base / 'outside/.skillsync/runs/real'
        self.make_run('real', root=self.base / 'outside')
        link = self.root / '.skillsync/runs/linked'
        link.symlink_to(self.base / 'outside/.skillsync/runs/real')
        self.blocked_prop('linked/P1', 'rejected', roots=[self.root, self.base / 'outside'])
        self.assertTrue(outside.exists())


class ProposalTransitions(ProposalBase):
    """K5: dozwolone przejścia z ostatniego wpisu przebiegu; `rejected`, `applied`, `--ref`, `--replace`."""

    def test_rejected_from_every_unresolved_state(self):
        for state in UNRESOLVED_STATES:
            with self.subTest(state=state):
                run = 'from-' + state
                self.make_run(run, states=(('P1', 'open'), ('P1', state)))
                data = self.prop(run + '/P1', 'rejected')
                self.assertEqual(data['status'], 'PASS')
                self.assertEqual(self.run_decisions(run)['decisions'][-1]['state'], 'rejected')

    def test_applied_only_from_approved_or_handoff(self):
        for state in ('approved', 'handoff'):
            with self.subTest(state=state):
                run = 'ok-' + state
                self.make_run(run, states=(('P1', 'open'), ('P1', state)))
                data = self.prop(run + '/P1', 'applied', '--ref', SHA_ONE)
                self.assertEqual(data['decision']['decision'], 'applied')
                self.assertEqual(self.run_decisions(run)['decisions'][-1]['state'], 'applied')
        for state in ('open', 'revising', 'deferred', 'stale', 'failed'):
            with self.subTest(state=state):
                run = 'no-' + state
                self.make_run(run, states=(('P1', state),))
                data = self.blocked_prop(run + '/P1', 'applied', '--ref', SHA_ONE)
                self.assertIn(state, data['reason'])
                self.blocked_prop(run + '/P1', 'applied', '--ref', SHA_ONE, '--replace')

    def test_state_is_taken_from_the_last_run_entry(self):
        self.make_run('r1', states=(('P1', 'approved'), ('P1', 'open')))
        self.blocked_prop('r1/P1', 'applied', '--ref', SHA_ONE)
        self.make_run('r2', states=(('P1', 'open'), ('P1', 'approved')))
        self.assertEqual(self.prop('r2/P1', 'applied', '--ref', SHA_ONE)['status'], 'PASS')

    def test_applied_requires_a_full_lowercase_sha_without_whitespace(self):
        self.make_run(states=(('P1', 'approved'),))
        self.blocked_prop('r1/P1', 'applied')
        bad = [SHA_ONE[:7], SHA_ONE[:39], SHA_ONE + '0', SHA_ONE.upper(), 'g' * 40, ' ' + SHA_ONE, SHA_ONE + ' ',
               SHA_ONE + '\n', SHA_ONE[:20] + ' ' + SHA_ONE[20:], SHA_ONE[:20] + '\t' + SHA_ONE[21:], '', ' ', 'HEAD',
               SHA_ONE[:39] + 'G', 'ghp_abcDEF123456', 'sk-ant-api03abcDEF123' + 'a' * 19]
        for ref in bad:
            with self.subTest(ref=ref):
                data = self.blocked_prop('r1/P1', 'applied', '--ref', ref)
                self.assertIn('--ref', data['reason'], 'powód odmowy ma wskazać flagę')

    def test_oversized_or_token_like_ref_is_refused_and_not_echoed(self):
        self.make_run(states=(('P1', 'approved'),))
        data = self.blocked_prop('r1/P1', 'applied', '--ref', 'x' * 301)
        self.assertNotIn('xxxxxxxxxx', data['reason'])
        data = self.blocked_prop('r1/P1', 'applied', '--ref', 'ghp_abcDEF123456')
        self.assertNotIn('ghp_abcDEF123456', data['reason'])

    def test_rejected_does_not_accept_ref(self):
        self.make_run(states=(('P1', 'open'),))
        for ref in (SHA_ONE, 'abc', ''):
            with self.subTest(ref=ref):
                data = self.blocked_prop('r1/P1', 'rejected', '--ref', ref)
                self.assertIn('--ref', data['reason'])

    def test_final_state_requires_replace_and_replace_allows_both_directions(self):
        self.make_run('r1', states=(('P1', 'open'), ('P1', 'rejected')))
        self.blocked_prop('r1/P1', 'rejected')
        self.blocked_prop('r1/P1', 'applied', '--ref', SHA_ONE)
        data = self.prop('r1/P1', 'applied', '--ref', SHA_ONE, '--replace')
        self.assertEqual(data['status'], 'PASS')
        self.assertEqual([e['state'] for e in self.run_decisions()['decisions']], ['open', 'rejected', 'applied'])
        self.make_run('r2', states=(('P1', 'approved'), ('P1', 'applied', SHA_ONE)))
        self.blocked_prop('r2/P1', 'rejected')
        self.blocked_prop('r2/P1', 'applied', '--ref', SHA_TWO)
        self.assertEqual(self.prop('r2/P1', 'rejected', '--replace')['status'], 'PASS')
        self.assertEqual(self.run_decisions('r2')['decisions'][-1]['state'], 'rejected')

    def test_replace_does_not_waive_the_transition_rules(self):
        self.make_run(states=(('P1', 'open'),))
        self.blocked_prop('r1/P1', 'applied', '--ref', SHA_ONE, '--replace')
        self.blocked_prop('r1/P1', 'applied', '--replace')
        self.blocked_prop('r1/P1', 'rejected', '--ref', SHA_ONE, '--replace')

    def test_replace_on_a_fresh_proposal_is_harmless(self):
        self.make_run(states=(('P1', 'open'),))
        data = self.prop('r1/P1', 'rejected', '--replace')
        self.assertEqual((data['status'], data['previous']), ('PASS', None))

    def test_existing_registry_entry_requires_replace_even_when_the_run_is_not_final(self):
        self.make_run(states=(('P1', 'approved'),))
        self.put_registry([self.pentry('r1/P1', 'rejected', date='2026-10-01')])
        before = self.disk()
        self.blocked_prop('r1/P1', 'rejected')
        self.blocked_prop('r1/P1', 'applied', '--ref', SHA_ONE)
        self.assertEqual(self.disk(), before)
        data = self.prop('r1/P1', 'applied', '--ref', SHA_ONE, '--replace')
        self.assertEqual(data['previous'], {'decision': 'rejected', 'date': '2026-10-01'})
        self.assertEqual(data['writes'], {'run': 'written', 'registry': 'written'})

    def test_with_a_registry_entry_the_registry_is_the_effective_state(self):
        for state in ('open', 'failed'):
            with self.subTest(state=state):
                run = 'eff-' + state
                self.make_run(run, states=(('P1', state),))
                self.put_registry([self.pentry(run + '/P1', 'rejected', date='2026-10-01')])
                before = self.disk()
                self.blocked_prop(run + '/P1', 'applied', '--ref', SHA_ONE)
                self.assertEqual(self.disk(), before)
                data = self.prop(run + '/P1', 'applied', '--ref', SHA_ONE, '--replace')
                self.assertEqual(data['previous'], {'decision': 'rejected', 'date': '2026-10-01'})
                self.assertEqual(self.run_decisions(run)['decisions'][-1]['state'], 'applied')
                [item] = [i for i in self.registry_doc()['proposals'] if i['proposal'] == run + '/P1']
                self.assertEqual((item['decision'], item['ref']), ('applied', SHA_ONE))

    def test_registry_entry_of_another_proposal_does_not_trigger_the_replace_rule(self):
        self.make_run(states=(('P1', 'open'),), extra=('P2',))
        self.put_registry([self.pentry('r1/P2', 'rejected'), self.pentry('r2/P1', 'rejected')])
        data = self.prop('r1/P1', 'rejected')
        self.assertIsNone(data['previous'])
        self.assertEqual([item['proposal'] for item in self.registry_doc()['proposals']],
                         ['r1/P2', 'r2/P1', 'r1/P1'])


class ProposalWritesAndResult(ProposalBase):
    """K5: kształt wyniku, wpisy obu plików, kolejność zapisów, data UTC."""

    def test_result_shape_for_rejected(self):
        self.make_run()
        data = self.prop('r1/P1', 'rejected')
        self.assertEqual(set(data), {'schema', 'status', 'path', 'decision', 'previous', 'writes'})
        self.assertEqual((data['schema'], data['status'], data['path']), (1, 'PASS', self.PATH))
        self.assertEqual(set(data['decision']), {'proposal', 'decision', 'date'})
        self.assertEqual((data['decision']['proposal'], data['decision']['decision']), ('r1/P1', 'rejected'))
        self.assertRegex(data['decision']['date'], r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
        self.assertIsNone(data['previous'])
        self.assertEqual(data['writes'], {'run': 'written', 'registry': 'written'})

    def test_run_entry_follows_the_schema_exactly(self):
        self.make_run(states=(('P1', 'open'), ('P1', 'revising', None, 'retro-P1-2.diff'), ('P2', 'open')),
                      extra=())
        before = self.run_decisions()
        data = self.prop('r1/P1', 'rejected')
        after = self.run_decisions()
        self.assertEqual(set(after), {'schema', 'run_id', 'decisions'})
        self.assertEqual((after['schema'], after['run_id']), (1, 'r1'))
        self.assertEqual(after['decisions'][:-1], before['decisions'], 'wpisy tylko się dopisują')
        entry = after['decisions'][-1]
        self.assertEqual(entry, {'proposal': 'P1', 'state': 'rejected', 'date': data['decision']['date'],
                                 'diff': 'retro-P1-2.diff', 'note': None})
        self.assertEqual(len(after['decisions']), len(before['decisions']) + 1)

    def test_applied_run_entry_has_ref_and_registry_entry_follows_the_contract(self):
        self.make_run(states=(('P1', 'approved'),), repo='project', target='docs/workflow.md')
        data = self.prop('r1/P1', 'applied', '--ref', SHA_ONE)
        entry = self.run_decisions()['decisions'][-1]
        self.assertEqual(entry, {'proposal': 'P1', 'state': 'applied', 'date': data['decision']['date'],
                                 'diff': 'retro-P1.diff', 'ref': SHA_ONE, 'note': None})
        registry = self.registry_doc()
        self.assertEqual(set(registry), {'version', 'decisions', 'proposals'})
        self.assertEqual(registry['version'], 1)
        self.assertEqual(registry['decisions'], [])
        [item] = registry['proposals']
        self.assertEqual({k: (v if k != 'date' else str(v)) for k, v in item.items()},
                         {'proposal': 'r1/P1', 'repo': 'project', 'target': 'docs/workflow.md',
                          'decision': 'applied', 'date': data['decision']['date'], 'ref': SHA_ONE})

    def test_rejected_registry_entry_has_no_ref_title_or_key(self):
        self.make_run(states=(('P1', 'open'),))
        data = self.prop('r1/P1', 'rejected')
        [item] = self.registry_doc()['proposals']
        self.assertEqual(set(item), {'proposal', 'repo', 'target', 'decision', 'date'})
        self.assertEqual((item['repo'], item['target'], item['decision']), ('library', PROPOSAL_TARGET, 'rejected'))
        self.assertEqual(str(item['date']), data['decision']['date'])
        text = (self.root / self.PATH).read_text(encoding='utf-8')
        for fragment in ('Tytuł', 'uzasadnienie', 'user-correction', 'LEAKMARK'):
            self.assertNotIn(fragment, text)

    def test_repo_and_target_come_from_the_run_not_from_the_command_line(self):
        self.make_run(states=(('P1', 'open'),), repo='project', target='docs/other.md')
        self.prop('r1/P1', 'rejected')
        [item] = self.registry_doc()['proposals']
        self.assertEqual((item['repo'], item['target']), ('project', 'docs/other.md'))

    def test_registry_is_created_and_existing_entries_keep_order_and_values(self):
        self.make_run(states=(('P1', 'open'),))
        self.assertFalse((self.root / 'docs').exists())
        self.prop('r1/P1', 'rejected')
        self.assertTrue((self.root / self.PATH).is_file())
        self.put_registry([self.pentry('a/P1', 'rejected', date='2026-10-01'),
                           self.pentry('b/P2', 'applied', repo='project', target='docs/a.md', ref=SHA_TWO)],
                          decisions=[{'cause_key': 'k', 'decision': 'rejected', 'date': '2026-10-02'}])
        before = self.registry_doc()
        self.make_run('r2', states=(('P3', 'approved'),))
        self.prop('r2/P3', 'rejected')
        after = self.registry_doc()
        self.assertEqual(after['decisions'], before['decisions'])
        self.assertEqual(after['proposals'][:2], before['proposals'])
        self.assertEqual([i['proposal'] for i in after['proposals']], ['a/P1', 'b/P2', 'r2/P3'])

    def test_replace_swaps_the_entry_in_place(self):
        self.make_run(states=(('P1', 'approved'),))
        self.put_registry([self.pentry('a/P1', date='2026-10-01'),
                           self.pentry('r1/P1', 'rejected', date='2026-10-05'),
                           self.pentry('b/P2', 'applied', ref=SHA_TWO)])
        others = [self.registry_doc()['proposals'][i] for i in (0, 2)]
        data = self.prop('r1/P1', 'applied', '--ref', SHA_ONE, '--replace')
        self.assertEqual(data['previous'], {'decision': 'rejected', 'date': '2026-10-05'})
        items = self.registry_doc()['proposals']
        self.assertEqual([i['proposal'] for i in items], ['a/P1', 'r1/P1', 'b/P2'])
        self.assertEqual([items[0], items[2]], others)
        self.assertEqual((items[1]['decision'], items[1]['ref']), ('applied', SHA_ONE))

    def test_replace_to_rejected_drops_the_old_ref(self):
        self.make_run(states=(('P1', 'approved'), ('P1', 'applied', SHA_ONE)))
        self.put_registry([self.pentry('r1/P1', 'applied', ref=SHA_ONE, date='2026-10-05')])
        data = self.prop('r1/P1', 'rejected', '--replace')
        self.assertEqual(data['previous'], {'decision': 'applied', 'date': '2026-10-05'})
        [item] = self.registry_doc()['proposals']
        self.assertEqual(set(item), {'proposal', 'repo', 'target', 'decision', 'date'})

    def test_previous_comes_from_the_registry_with_either_date_style(self):
        for style, raw in (('text', '2026-10-01'), ('yaml', date(2026, 10, 1))):
            with self.subTest(style=style):
                run = 'r-' + style
                self.make_run(run, states=(('P1', 'open'),))
                self.put_registry([self.pentry(run + '/P1', 'applied', date=raw, ref=SHA_ONE)])
                data = self.prop(run + '/P1', 'rejected', '--replace')
                self.assertEqual(data['previous'], {'decision': 'applied', 'date': '2026-10-01'})

    def test_previous_is_null_for_a_final_run_state_without_registry_entry(self):
        self.make_run(states=(('P1', 'rejected'),))
        data = self.prop('r1/P1', 'rejected', '--replace')
        self.assertIsNone(data['previous'])

    def test_run_file_is_written_before_the_registry(self):
        from unittest import mock
        from skillsync_workflow import retro
        self.make_run()
        calls = []
        real = retro._write_atomic

        def recorder(path, text):
            calls.append(Path(path).name)
            return real(path, text)

        code, data = self.in_process('--proposal', 'r1/P1', '--decision', 'rejected',
                                     patches=[mock.patch.object(retro, '_write_atomic', side_effect=recorder)])
        self.assertEqual((code, data['status']), (0, 'PASS'))
        self.assertEqual(calls, ['retro-decisions.json', 'decisions.yaml'])

    def test_date_is_the_utc_day_even_when_local_time_is_a_day_ahead(self):
        from datetime import timedelta
        from unittest import mock
        from skillsync_workflow import retro
        real = retro.datetime

        class Fake(real):
            @classmethod
            def now(cls, tz=None):
                base = real(2026, 11, 5, 23, 30, tzinfo=timezone.utc)
                if tz is None:
                    return base.astimezone(timezone(timedelta(hours=14))).replace(tzinfo=None)
                return base.astimezone(tz)

        self.make_run()
        code, data = self.in_process('--proposal', 'r1/P1', '--decision', 'rejected',
                                     patches=[mock.patch.object(retro, 'datetime', Fake)])
        self.assertEqual(code, 0, data)
        self.assertEqual(data['decision']['date'], '2026-11-05')
        self.assertEqual(self.run_decisions()['decisions'][-1]['date'], '2026-11-05')
        self.assertEqual(str(self.registry_doc()['proposals'][0]['date']), '2026-11-05')

    def test_written_registry_is_read_by_retro_and_the_run_file_stays_valid_json(self):
        self.pair()
        self.make_run(states=(('P1', 'approved'),))
        self.prop('r1/P1', 'applied', '--ref', SHA_ONE)
        data = self.full()
        self.assertEqual(data['decisions']['status'], 'available')
        self.assertTrue((self.root / '.skillsync/runs/r1/retro-decisions.json').read_text().endswith('\n'))


class ProposalWorktree(ProposalBase):
    """K5: w powiązanym worktree (prawdziwe `git worktree add`) polecenie kończy się BLOCKED bez zmian."""

    def make_worktree(self, prefix=''):
        from layers_helpers import commit_file, git
        self.repo = self.base / 'repo'
        self.wt = self.base / 'wt'
        self.repo.mkdir()
        git(self.repo, 'init', '-q', '-b', 'main')
        for name, value in (('user.email', 'dev@example.test'), ('user.name', 'Dev'), ('commit.gpgsign', 'false')):
            git(self.repo, 'config', name, value)
        rel = (prefix + '/' if prefix else '') + '.github/.skillsync.yaml'
        commit_file(self.repo, rel, yaml.safe_dump(self.manifest), 'manifest')
        git(self.repo, 'worktree', 'add', '-q', '-b', 'feat', str(self.wt))
        self.main_root = self.repo / prefix if prefix else self.repo
        self.root = self.wt / prefix if prefix else self.wt
        self.assertTrue((self.root / '.github/.skillsync.yaml').is_file())

    def test_worktree_is_blocked_and_neither_tree_changes(self):
        self.make_worktree()
        self.make_run(states=(('P1', 'open'),), root=self.main_root)
        self.make_run(states=(('P1', 'open'),), root=self.root)
        self.put_registry([self.pentry('x/P1')], root=self.main_root)
        data = self.blocked_prop('r1/P1', 'rejected', roots=[self.root, self.main_root])
        self.assertIn('worktree', data['reason'])
        self.blocked_prop('r1/P1', 'rejected', '--replace', roots=[self.root, self.main_root])

    def test_worktree_with_the_project_in_a_subdirectory_is_blocked(self):
        self.make_worktree('services/api')
        self.make_run(states=(('P1', 'open'),), root=self.main_root)
        self.make_run(states=(('P1', 'open'),), root=self.root)
        self.blocked_prop('r1/P1', 'rejected', roots=[self.root, self.main_root])

    def test_main_checkout_of_the_same_repository_is_allowed(self):
        self.make_worktree()
        self.make_run(states=(('P1', 'open'),), root=self.main_root)
        data = self.prop('r1/P1', 'rejected', path=self.main_root)
        self.assertEqual(data['status'], 'PASS')
        self.assertTrue((self.main_root / self.PATH).is_file())
        self.assertFalse((self.wt / self.PATH).exists())

    def git_result(self, code, out=b''):
        import subprocess as sp
        return sp.CompletedProcess(['git'], code, stdout=out)

    def decide_with_git(self, outcome, *args):
        """`retro-decide --proposal` w tym procesie, z podmienionym wywołaniem git (`_Git._call`)."""
        from unittest import mock
        from skillsync_workflow import retro_scan
        patch = mock.patch.object(retro_scan._Git, '_call', return_value=outcome)
        return self.in_process('--proposal', 'r1/P1', '--decision', 'rejected', *args, patches=[patch])

    def assert_git_refusal(self, outcome):
        self.make_run()
        before = self.disk()
        code, data = self.decide_with_git(outcome)
        self.assertEqual((code, data['status'], data['writes']), (2, 'BLOCKED', SKIPPED_WRITES), data)
        self.assertTrue(data['reason'])
        self.assertEqual(self.disk(), before)
        self.assertNotIn(str(self.base), self.text)

    def test_git_not_a_repository_without_dot_git_lets_the_decision_through(self):
        self.make_run()
        code, data = self.decide_with_git(self.git_result(128))
        self.assertEqual((code, data['status']), (0, 'PASS'), data)

    def test_git_128_with_dot_git_directory_or_file_is_refused(self):
        self.make_run()
        (self.root / '.git').mkdir()
        before = self.disk()
        code, data = self.decide_with_git(self.git_result(128))
        self.assertEqual((code, data['status'], data['writes']), (2, 'BLOCKED', SKIPPED_WRITES), data)
        self.assertEqual(self.disk(), before)
        (self.root / '.git').rmdir()
        (self.root / '.git').write_text('gitdir: /nonexistent\n', encoding='utf-8')
        before = self.disk()
        code, data = self.decide_with_git(self.git_result(128))
        self.assertEqual((code, data['status']), (2, 'BLOCKED'), data)
        self.assertEqual(self.disk(), before)

    def test_git_128_with_dot_git_in_an_ancestor_is_refused(self):
        self.root = self.base / 'outer' / 'project'
        self.root.mkdir(parents=True)
        self.manifest_into_new_root()
        (self.base / 'outer' / '.git').mkdir()
        self.assert_git_refusal(self.git_result(128))

    def manifest_into_new_root(self):
        self.save()

    def test_git_timeout_or_failure_to_run_is_refused(self):
        self.assert_git_refusal(None)

    def test_git_other_error_codes_are_refused(self):
        for code in (1, 2, 129, 255):
            with self.subTest(code=code):
                self.assert_git_refusal(self.git_result(code, b'.git\n.git\n'))

    def test_git_output_with_too_few_lines_is_refused(self):
        for out in (b'', b'.git', b'/x/.git'):
            with self.subTest(out=out):
                self.assert_git_refusal(self.git_result(0, out))

    def test_git_equal_dirs_mean_not_linked_and_different_dirs_mean_linked(self):
        self.make_run()
        code, data = self.decide_with_git(self.git_result(0, b'.git\n.git\n'))
        self.assertEqual((code, data['status']), (0, 'PASS'), data)
        self.make_run('r2')
        from unittest import mock
        from skillsync_workflow import retro_scan
        patch = mock.patch.object(retro_scan._Git, '_call', return_value=self.git_result(0, b'.git/worktrees/x\n../.git\n'))
        code, data = self.in_process('--proposal', 'r2/P1', '--decision', 'rejected', patches=[patch])
        self.assertEqual((code, data['status']), (2, 'BLOCKED'), data)

    def test_plain_repository_checkout_is_allowed(self):
        from layers_helpers import commit_file, git
        git(self.root, 'init', '-q', '-b', 'main')
        for name, value in (('user.email', 'dev@example.test'), ('user.name', 'Dev'), ('commit.gpgsign', 'false')):
            git(self.root, 'config', name, value)
        commit_file(self.root, 'seed.txt', 'x\n', 'seed')
        self.make_run()
        self.assertEqual(self.prop('r1/P1', 'rejected')['status'], 'PASS')


class ProposalPartialWrites(ProposalBase):
    """K5: porażka jednego z zapisów, idempotentne ponowienie, atomowość, prywatność powodu."""

    def setUp(self):
        super().setUp()
        self.make_run(states=(('P1', 'open'),))

    def assert_failed_registry(self, data):
        self.assertEqual(data['status'], 'BLOCKED')
        self.assertEqual(set(data), BLOCKED_PROPOSAL_KEYS)
        self.assertEqual(data['writes'], {'run': 'written', 'registry': 'failed'})
        self.assertTrue(isinstance(data['reason'], str) and data['reason'])
        self.assertNotIn(str(self.base), self.text)

    def retry_and_check(self, run_entries_before, others=()):
        """Ponowienie bez `--replace` jest odmową, z `--replace` PASS bez duplikatu wpisu przebiegu."""
        self.blocked_prop('r1/P1', 'rejected')
        data = self.prop('r1/P1', 'rejected', '--replace')
        self.assertEqual(data['status'], 'PASS')
        self.assertEqual(data['writes'], {'run': 'skipped', 'registry': 'written'})
        self.assertEqual(len(self.run_decisions()['decisions']), run_entries_before)
        self.assertEqual([i['proposal'] for i in self.registry_doc()['proposals']], [*others, 'r1/P1'])
        self.assertEqual(self.tmp_files(), [])

    @unittest.skipIf(CHMOD_INEFFECTIVE, 'chmod nieskuteczny dla roota')
    def test_second_write_fails_on_unwritable_registry_directory(self):
        registry = self.put_registry([self.pentry('a/P1')])
        before = registry.read_bytes()
        registry.parent.chmod(0o555)
        try:
            data = self.prop('r1/P1', 'rejected', code=2)
        finally:
            registry.parent.chmod(0o755)
        self.assert_failed_registry(data)
        self.assertEqual(registry.read_bytes(), before)
        self.assertEqual(self.run_decisions()['decisions'][-1]['state'], 'rejected')
        self.assertEqual(self.tmp_files(), [])
        self.retry_and_check(2, others=['a/P1'])

    @unittest.skipIf(CHMOD_INEFFECTIVE, 'chmod nieskuteczny dla roota')
    def test_second_write_fails_when_docs_cannot_be_created(self):
        (self.root / 'docs').mkdir()
        (self.root / 'docs').chmod(0o555)
        try:
            data = self.prop('r1/P1', 'rejected', code=2)
        finally:
            (self.root / 'docs').chmod(0o755)
        self.assert_failed_registry(data)
        self.assertFalse((self.root / 'docs/retro').exists())
        self.assertEqual(len(self.run_decisions()['decisions']), 2)
        self.retry_and_check(2)

    def test_second_write_fails_by_patch(self):
        from unittest import mock
        from skillsync_workflow import retro
        failing = mock.patch.object(retro, '_write_registry',
                                    side_effect=OSError(13, 'denied', str(self.root / self.PATH)))
        code, data = self.in_process('--proposal', 'r1/P1', '--decision', 'rejected', patches=[failing])
        self.assertEqual(code, 2)
        self.assert_failed_registry(data)
        self.assertFalse((self.root / self.PATH).exists())
        self.assertEqual(self.run_decisions()['decisions'][-1]['state'], 'rejected')
        self.retry_and_check(2)

    @unittest.skipIf(CHMOD_INEFFECTIVE, 'chmod nieskuteczny dla roota')
    def test_first_write_fails_and_nothing_is_written(self):
        folder = self.root / '.skillsync/runs/r1'
        before = self.disk()
        folder.chmod(0o555)
        try:
            data = self.prop('r1/P1', 'rejected', code=2)
        finally:
            folder.chmod(0o755)
        self.assertEqual(data['status'], 'BLOCKED')
        self.assertEqual(data['writes'], {'run': 'failed', 'registry': 'skipped'})
        self.assertTrue(data['reason'])
        self.assertNotIn(str(self.base), self.text)
        self.assertFalse((self.root / self.PATH).exists())
        self.assertEqual(self.tmp_files(), [])
        after = self.disk()
        after[os.path.relpath(folder, self.root)] = before[os.path.relpath(folder, self.root)]  # mtime katalogu
        self.assertEqual({k: v[2] for k, v in after.items()}, {k: v[2] for k, v in before.items()})
        self.assertEqual(self.prop('r1/P1', 'rejected')['writes'], {'run': 'written', 'registry': 'written'})

    def test_first_write_fails_by_patch_and_the_registry_is_not_touched(self):
        from unittest import mock
        from skillsync_workflow import retro
        self.put_registry([self.pentry('a/P1')])
        before = self.disk()
        failing = mock.patch.object(retro, '_write_atomic',
                                    side_effect=OSError(13, 'denied', str(self.root / '.skillsync')))
        code, data = self.in_process('--proposal', 'r1/P1', '--decision', 'rejected', patches=[failing])
        self.assertEqual(code, 2)
        self.assertEqual(data['writes'], {'run': 'failed', 'registry': 'skipped'})
        self.assertNotIn(str(self.base), self.text)
        self.assertEqual(self.disk(), before)

    def test_replace_failure_leaves_the_target_file_whole_and_no_temp_files(self):
        from unittest import mock
        from skillsync_workflow import retro
        registry = self.put_registry([self.pentry('a/P1')])
        before = registry.read_bytes()
        real = os.replace

        def replace(src, dst, *args, **kwargs):
            if Path(dst).name == 'decisions.yaml':
                raise OSError(5, 'io error', str(dst))
            return real(src, dst, *args, **kwargs)

        code, data = self.in_process('--proposal', 'r1/P1', '--decision', 'rejected',
                                     patches=[mock.patch.object(retro.os, 'replace', replace)])
        self.assertEqual(code, 2)
        self.assert_failed_registry(data)
        self.assertEqual(registry.read_bytes(), before)
        self.assertEqual(self.tmp_files(), [])
        run_file = self.root / '.skillsync/runs/r1/retro-decisions.json'
        self.assertEqual(json.loads(run_file.read_text())['decisions'][-1]['state'], 'rejected')

    def test_replace_failure_on_the_run_file_leaves_it_whole(self):
        from unittest import mock
        from skillsync_workflow import retro
        run_file = self.root / '.skillsync/runs/r1/retro-decisions.json'
        before = run_file.read_bytes()
        real = os.replace

        def replace(src, dst, *args, **kwargs):
            if Path(dst).name == 'retro-decisions.json':
                raise OSError(5, 'io error', str(dst))
            return real(src, dst, *args, **kwargs)

        code, data = self.in_process('--proposal', 'r1/P1', '--decision', 'rejected',
                                     patches=[mock.patch.object(retro.os, 'replace', replace)])
        self.assertEqual((code, data['writes']), (2, {'run': 'failed', 'registry': 'skipped'}))
        self.assertEqual(run_file.read_bytes(), before)
        self.assertEqual(self.tmp_files(), [])
        self.assertFalse((self.root / self.PATH).exists())

    def test_registry_failure_for_applied_keeps_the_ref_in_the_run_entry(self):
        from unittest import mock
        from skillsync_workflow import retro
        self.make_run('r2', states=(('P1', 'approved'),))
        failing = mock.patch.object(retro, '_write_registry', side_effect=OSError(13, 'denied', 'x'))
        code, data = self.in_process('--proposal', 'r2/P1', '--decision', 'applied', '--ref', SHA_ONE,
                                     patches=[failing])
        self.assertEqual((code, data['writes']), (2, {'run': 'written', 'registry': 'failed'}))
        self.assertEqual(self.run_decisions('r2')['decisions'][-1]['ref'], SHA_ONE)
        retry = self.prop('r2/P1', 'applied', '--ref', SHA_ONE, '--replace')
        self.assertEqual(retry['writes'], {'run': 'skipped', 'registry': 'written'})
        self.assertEqual(len(self.run_decisions('r2')['decisions']), 2)

    def test_retry_with_a_different_ref_appends_a_new_run_entry(self):
        self.make_run('r2', states=(('P1', 'approved'),))
        self.prop('r2/P1', 'applied', '--ref', SHA_ONE)
        data = self.prop('r2/P1', 'applied', '--ref', SHA_TWO, '--replace')
        self.assertEqual(data['writes'], {'run': 'written', 'registry': 'written'})
        self.assertEqual([e.get('ref') for e in self.run_decisions('r2')['decisions']], [None, SHA_ONE, SHA_TWO])

    def test_reasons_never_contain_the_base_path(self):
        for args in (('--proposal', 'nope/P1', '--decision', 'rejected'),
                     ('--proposal', 'r1/P1', '--decision', 'applied', '--ref', SHA_ONE),
                     ('--proposal', 'r1/P1', '--decision', 'rejected', '--ref', SHA_ONE)):
            with self.subTest(args=args):
                data = self.decide(*args, code=2)
                self.assertNotIn(str(self.base), json.dumps(data))
                self.assertNotIn(str(self.base), self.text)


def _entry(**changes):
    base = {'proposal': 'r1/P1', 'repo': 'library', 'target': PROPOSAL_TARGET, 'decision': 'rejected',
            'date': '2026-11-05'}
    base.update(changes)
    return {k: v for k, v in base.items() if v is not _MISSING}


_MISSING = object()


def invalid_proposal_sections():
    """(etykieta, wartość sekcji `proposals:`), każda różni się od poprawnego wpisu jedną rzeczą."""
    good_applied = _entry(proposal='r2/P2', decision='applied', ref=SHA_ONE)
    cases = [
        ('duplicate id', [_entry(), _entry()]),
        ('duplicate id with another decision', [_entry(), _entry(decision='applied', ref=SHA_ONE)]),
        ('missing proposal', [_entry(proposal=_MISSING)]),
        ('missing repo', [_entry(repo=_MISSING)]),
        ('missing target', [_entry(target=_MISSING)]),
        ('missing decision', [_entry(decision=_MISSING)]),
        ('missing date', [_entry(date=_MISSING)]),
        ('applied without ref', [_entry(decision='applied')]),
        ('rejected with ref', [_entry(ref=SHA_ONE)]),
        ('applied with null ref', [_entry(decision='applied', ref=None)]),
        ('entry is a string', ['r1/P1']),
        ('entry is a list', [[1, 2]]),
        ('entry is null', [None]),
        ('second entry is bad', [good_applied, _entry(proposal='r3/P3', repo='other')]),
        ('section is a mapping', {'proposal': 'r1/P1'}),
        ('section is a string', 'r1/P1'),
        ('section is a number', 7),
        ('section is a bool', True),
    ]
    cases += [('bad id ' + repr(v), [_entry(proposal=v)]) for v in
              ('r1', 'r1/P0', 'r1/P10000', 'r1/P01', '/P1', '-r/P1', '.r/P1', 'r 1/P1', 'a' * 81 + '/P1', 'r1/p1',
               'r1/P1x', 'a/b/P1', 123, None, '')]
    cases += [('bad decision ' + repr(v), [_entry(decision=v)]) for v in
              ('approved', 'open', 'deferred', 'Rejected', '', None, 1)]
    cases += [('applied bad ref ' + repr(v), [_entry(decision='applied', ref=v)]) for v in
              (SHA_ONE[:7], SHA_ONE[:39], SHA_ONE + '0', SHA_ONE.upper(), 'g' * 40, ' ' + SHA_ONE, SHA_ONE + '\n',
               123, '')]
    cases += [('bad date ' + repr(v), [_entry(date=v)]) for v in
              ('2026-13-45', '2026-11-5', '5.11.2026', '2026/11/05', '2026-11-05T10:00:00', 20261105, None, '',
               datetime(2026, 11, 5, 10, 0, 0))]
    cases += [('bad repo ' + repr(v), [_entry(repo=v)]) for v in ('other', 'Library', '', None, 1)]
    cases += [('bad target ' + repr(v), [_entry(target=v)]) for v in
              ('../x.md', 'a/../b.md', '..', '/abs/x.md', '/', '', None, 5)]
    cases += [('unknown field ' + name, [_entry(**{name: 'x'})]) for name in
              ('title', 'key', 'by', 'note', 'cause_key', 'owner', 'extra')]
    return cases


class RegistryProposalsSection(ProposalBase):
    """K6: sekcja `proposals:` rejestru jest walidowana; niepoprawna daje `invalid`, który blokuje oba tryby `retro-decide`."""

    def seed_pair(self):
        self.pair()
        self.make_run(states=(('P1', 'open'),))

    def test_registry_without_the_section_is_read_as_before(self):
        self.pair()
        self.put_registry(None, decisions=[{'cause_key': 'flaky', 'decision': 'rejected', 'date': '2026-10-01'}])
        data = self.full()
        self.assertEqual(data['decisions'], {'status': 'available', 'path': self.PATH, 'count': 1})
        self.assertEqual(data['candidates'][0]['decision']['decision'], 'rejected')

    def test_valid_sections_are_available_for_retro(self):
        self.pair()
        variants = {
            'empty list': [],
            'rejected quoted date': [_entry()],
            'rejected yaml date': [_entry(date=date(2026, 11, 5))],
            'applied': [_entry(decision='applied', ref=SHA_ONE)],
            'both': [_entry(), _entry(proposal='r2/P2', decision='applied', ref=SHA_TWO, repo='project',
                                      target='docs/workflow.md')],
            'longest id': [_entry(proposal='a' * 80 + '/P9999')],
            'run id with dot suffix': [_entry(proposal='r1.2/P3')],
        }
        for label, section in variants.items():
            with self.subTest(label=label):
                self.put_registry(section, decisions=[{'cause_key': 'flaky', 'decision': 'rejected',
                                                       'date': '2026-10-01'}])
                data = self.full()
                self.assertEqual(data['decisions'], {'status': 'available', 'path': self.PATH, 'count': 1})
                self.assertEqual(data['candidates'][0]['decision']['decision'], 'rejected')

    def test_proposals_only_registry_with_empty_decisions_is_available(self):
        self.pair()
        self.put_registry([_entry()])
        self.assertEqual(self.full()['decisions']['status'], 'available')

    def test_invalid_sections_make_the_registry_invalid_for_retro(self):
        self.pair()
        for label, section in invalid_proposal_sections():
            with self.subTest(label=label):
                self.put_registry(section)
                data = self.full(code=2)
                self.assertEqual(data['status'], 'BLOCKED', label)
                self.assertEqual(data['decisions']['status'], 'invalid', label)

    def test_unknown_top_level_key_next_to_proposals_is_invalid(self):
        self.pair()
        self.put_registry(raw='version: 1\ndecisions: []\nproposals: []\nextra: 1\n')
        self.assertEqual(self.full(code=2)['decisions']['status'], 'invalid')

    def test_invalid_registry_blocks_both_modes_without_changes(self):
        self.seed_pair()
        for label, section in invalid_proposal_sections():
            with self.subTest(label=label):
                path = self.put_registry(section)
                before_bytes = path.read_bytes()
                before = self.disk()
                for args in (('--cause-key', 'flaky', '--decision', 'rejected'),
                             ('--cause-key', 'flaky', '--decision', 'rejected', '--replace'),
                             ('--proposal', 'r1/P1', '--decision', 'rejected'),
                             ('--proposal', 'r1/P1', '--decision', 'rejected', '--replace')):
                    data = self.decide(*args, code=2)
                    self.assertEqual(data['status'], 'BLOCKED', (label, args))
                    self.assertTrue(data['reason'])
                    self.assertNotIn(str(self.base), self.text)
                self.assertEqual(path.read_bytes(), before_bytes, label)
                self.assertEqual(self.disk(), before, label)

    def test_applied_decision_is_also_refused_on_an_invalid_registry(self):
        self.make_run(states=(('P1', 'approved'),))
        self.put_registry([_entry(decision='applied')])
        self.blocked_prop('r1/P1', 'applied', '--ref', SHA_ONE)

    def test_invalid_proposal_in_the_registry_is_detected_even_for_other_ids(self):
        self.make_run(states=(('P1', 'open'),))
        self.put_registry([_entry(proposal='zzz/P9', repo='bad')])
        self.blocked_prop('r1/P1', 'rejected')


class CauseKeyKeepsProposals(ProposalBase):
    """K6: `retro-decide --cause-key` zachowuje sekcję `proposals:` (wpisy, kolejność, wartości, `ref`)."""

    SECTION = [_entry(proposal='a/P1', date='2026-10-01'),
               _entry(proposal='b/P2', decision='applied', repo='project', target='docs/workflow.md',
                      date='2026-10-02', ref=SHA_ONE),
               _entry(proposal='c/P3', date='2026-10-03'),
               _entry(proposal='d/P4', decision='applied', date='2026-10-04', ref=SHA_TWO)]

    def args(self, key='flaky', *extra):
        return ('--cause-key', key, '--decision', 'approved', '--owner', 'skill', '--case', 'tests/x.py', *extra)

    def test_new_key_keeps_the_section_verbatim(self):
        self.put_registry(self.SECTION, decisions=[{'cause_key': 'old', 'decision': 'rejected', 'date': '2026-09-01'}])
        before = self.registry_doc()
        self.decide(*self.args())
        after = self.registry_doc()
        self.assertEqual(after['proposals'], before['proposals'])
        self.assertEqual([i['proposal'] for i in after['proposals']], ['a/P1', 'b/P2', 'c/P3', 'd/P4'])
        self.assertEqual(after['proposals'][1]['ref'], SHA_ONE)
        self.assertEqual(after['proposals'][3]['ref'], SHA_TWO)
        self.assertEqual([i['cause_key'] for i in after['decisions']], ['old', 'flaky'])
        self.assertEqual(list(after), ['version', 'decisions', 'proposals'])

    def test_replace_keeps_the_section(self):
        self.put_registry(self.SECTION, decisions=[{'cause_key': 'flaky', 'decision': 'rejected', 'date': '2026-09-01'},
                                                   {'cause_key': 'other', 'decision': 'rejected', 'date': '2026-09-02'}])
        before = self.registry_doc()['proposals']
        self.decide(*self.args('flaky', '--replace'))
        self.assertEqual(self.registry_doc()['proposals'], before)
        self.decide('--cause-key', 'other', '--decision', 'rejected', '--replace')
        self.assertEqual(self.registry_doc()['proposals'], before)

    def test_alias_collapse_of_several_entries_keeps_the_section(self):
        (self.library / 'workflow').mkdir(parents=True, exist_ok=True)
        (self.library / 'workflow/cause-keys.yaml').write_text(yaml.safe_dump(
            {'version': 1, 'keys': {'flaky-tests': {'description': 'd', 'aliases': ['flaky-test']}}}),
            encoding='utf-8')
        self.put_registry(self.SECTION, decisions=[
            {'cause_key': 'flaky-test', 'decision': 'rejected', 'date': '2026-10-05'},
            {'cause_key': 'other', 'decision': 'rejected', 'date': '2026-10-02'},
            {'cause_key': 'flaky-tests', 'decision': 'rejected', 'date': '2026-10-06'}])
        before = self.registry_doc()['proposals']
        data = self.decide('--cause-key', 'flaky-test', '--decision', 'rejected', '--replace')
        self.assertEqual(data['previous'], {'decision': 'rejected', 'date': '2026-10-06'})
        after = self.registry_doc()
        self.assertEqual([i['cause_key'] for i in after['decisions']], ['flaky-tests', 'other'])
        self.assertEqual(after['proposals'], before)

    def test_empty_section_stays_a_list_and_absent_section_stays_absent(self):
        self.put_registry([])
        self.decide(*self.args())
        self.assertEqual(self.registry_doc()['proposals'], [])
        self.put_registry(None)
        self.decide(*self.args('second'))
        self.assertNotIn('proposals', self.registry_doc())
        self.assertNotIn('proposals', (self.root / self.PATH).read_text(encoding='utf-8'))

    def test_repeated_writes_keep_the_section_and_retro_reads_it(self):
        self.pair()
        self.put_registry(self.SECTION)
        before = self.registry_doc()['proposals']
        self.decide(*self.args('flaky'))
        self.decide(*self.args('second'))
        self.decide('--cause-key', 'third', '--decision', 'rejected')
        self.assertEqual(self.registry_doc()['proposals'], before)
        self.assertEqual(self.full()['decisions']['status'], 'available')

    def test_proposal_write_keeps_decisions_and_cause_key_write_keeps_proposals_in_turn(self):
        self.make_run(states=(('P1', 'open'),))
        self.put_registry(self.SECTION, decisions=[{'cause_key': 'old', 'decision': 'rejected', 'date': '2026-09-01'}])
        self.prop('r1/P1', 'rejected')
        self.decide(*self.args('after'))
        data = self.registry_doc()
        self.assertEqual([i['proposal'] for i in data['proposals']], ['a/P1', 'b/P2', 'c/P3', 'd/P4', 'r1/P1'])
        self.assertEqual([i['cause_key'] for i in data['decisions']], ['old', 'after'])

    def test_cause_key_writes_registry_without_section_byte_for_byte_as_before(self):
        """Wartość bazowa z zachowania sprzed zmiany (generator fc73d32 daje te same bajty na tym wejściu)."""
        self.put_registry(raw='version: 1\ndecisions:\n  - {cause_key: old-one, decision: rejected, date: 2026-10-01}\n'
                              '  - cause_key: other\n    decision: approved\n    date: \'2026-10-02\'\n'
                              '    owner: skill\n    case: tests/x.py\n')
        data = self.decide('--cause-key', 'new-one', '--decision', 'rejected')
        expected = ('version: 1\n'
                    'decisions:\n'
                    '- cause_key: old-one\n'
                    '  decision: rejected\n'
                    '  date: 2026-10-01\n'
                    '- cause_key: other\n'
                    '  decision: approved\n'
                    '  date: \'2026-10-02\'\n'
                    '  owner: skill\n'
                    '  case: tests/x.py\n'
                    '- cause_key: new-one\n'
                    '  decision: rejected\n'
                    f'  date: {data["decision"]["date"]}\n')
        self.assertEqual((self.root / self.PATH).read_text(encoding='utf-8'), expected)



# ---------------------------------------------------------------------------------------------------------------
# R2 krok 1.4t (K7): `retro --json` sekcje `proposals[]`, `proposals_truncated` i `coverage.retro`
# ---------------------------------------------------------------------------------------------------------------
RETRO_SIGNAL = {'id': 'S1', 'key': 'user-correction', 'repo': 'library', 'target': PROPOSAL_TARGET,
                'from_failure': False}


class RetroListBase(ProposalBase):
    """Przebiegi retro z propozycjami; wynik `retro --json` z zamkniętą listą pól (`full`)."""

    def prun(self, run_id, proposals=(), created='2026-11-05T10:00:00Z', signals=None, files=(), root=None,
             registry_only=False):
        """`proposals`: słowniki {n, states, title, owner, repo, target, signals}. `states`: lista stanów (kolejne wpisy
        `retro-decisions.json`); element to nazwa stanu albo (stan, diff); domyślny diff to retro-P<n>.diff."""
        root = Path(root or self.root)
        folder = root / '.skillsync/runs' / run_id
        folder.mkdir(parents=True, exist_ok=True)
        listed, entries = [], []
        for spec in proposals:
            n = spec['n']
            listed.append({'id': f'P{n}', 'signals': spec.get('signals', ['S1']), 'owner': spec.get('owner', 'skill'),
                           'repo': spec.get('repo', 'library'), 'target': spec.get('target', PROPOSAL_TARGET),
                           'title': spec.get('title', f'Tytuł P{n}'), 'rationale': 'uzasadnienie',
                           'diff': f'retro-P{n}.diff', 'diff_by': 'reviewer', 'self_assessed': False,
                           'previously_rejected': None, 'recurrence_of': None})
            for state in spec.get('states', ['open']):
                state, diff = (state, f'retro-P{n}.diff') if isinstance(state, str) else state
                entries.append({'proposal': f'P{n}', 'state': state, 'date': '2026-11-05', 'diff': diff, 'note': None})
        (folder / 'retro-signals.json').write_text(json.dumps(
            {'schema': 1, 'run_id': run_id, 'created_at': created, 'assessor': 'reviewer',
             'signals': [RETRO_SIGNAL] if signals is None else signals, 'proposals': listed}), encoding='utf-8')
        (folder / 'retro-decisions.json').write_text(json.dumps(
            {'schema': 1, 'run_id': run_id, 'decisions': entries}), encoding='utf-8')
        for name in files:
            (folder / name).write_text('--- a\n+++ b\n', encoding='utf-8')
        return folder

    def listed(self, *args):
        return self.full(*args)

    def retro_in_process(self, *args, patches=()):
        """`retro --json` w tym procesie (podmiana limitów); zwraca zwalidowany kształtem wynik."""
        import argparse
        import contextlib
        import io
        from skillsync_workflow import retro
        parser = argparse.ArgumentParser()
        retro.register(parser.add_subparsers())
        parsed = parser.parse_args(['retro', str(self.root), *map(str, args), '--json'])
        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            with contextlib.redirect_stdout(out):
                code = parsed.func(parsed)
        self.text = out.getvalue()
        data = json.loads(out.getvalue())
        self.assertEqual(code, 0 if data['status'] != 'BLOCKED' else 2, self.text)
        self.check_shape(data)
        return data

    def ids(self, *args):
        return [item['id'] for item in self.full(*args)['proposals']]

    def states(self, *args):
        return [item['state'] for item in self.full(*args)['proposals']]


class RetroProposalList(RetroListBase):
    """K7: `retro --json`, lista propozycji."""

    def test_exact_entry_shape(self):
        self.run_file('cp', rec('a'))
        self.prun('r1', [{'n': 1, 'states': ['open'], 'title': 'krótki tytuł', 'owner': 'skill', 'signals': ['S1', 'S2']}],
                  signals=[RETRO_SIGNAL, {**RETRO_SIGNAL, 'id': 'S2', 'key': 'unneeded-question'},
                           {**RETRO_SIGNAL, 'id': 'S3', 'key': 'other-key'}], files=['retro-P1.diff'])
        data = self.full()
        self.assertEqual((data['schema'], data['status'], data['proposals_truncated']), (1, 'PASS', False))
        self.assertEqual(data['proposals'], [{
            'id': 'r1/P1', 'owner': 'skill', 'repo': 'library', 'target': PROPOSAL_TARGET, 'title': 'krótki tytuł',
            'state': 'open', 'diff': '.skillsync/runs/r1/retro-P1.diff', 'keys': ['unneeded-question', 'user-correction'],
            'signals': 2, 'created_at': '2026-11-05T10:00:00Z'}])

    def test_no_runs_gives_an_empty_list(self):
        data = self.full()
        self.assertEqual((data['proposals'], data['proposals_truncated']), ([], False))

    def test_default_lists_only_unresolved_states(self):
        specs = [{'n': i, 'states': [s]} for i, s in enumerate(PROPOSAL_STATE_ORDER, 1)]
        self.prun('r1', specs)
        self.assertEqual(sorted(self.states()), sorted(PROPOSAL_STATE_ORDER[:-2]))
        self.assertNotIn('rejected', self.states())
        self.assertNotIn('applied', self.states())

    def test_all_states_adds_rejected_and_applied_at_the_end(self):
        self.prun('r1', [{'n': i, 'states': [s]} for i, s in enumerate(PROPOSAL_STATE_ORDER, 1)])
        self.assertEqual(self.states('--all-states'), PROPOSAL_STATE_ORDER)

    def test_states_follow_the_contract_order_whatever_the_creation_order(self):
        for index, state in enumerate(reversed(PROPOSAL_STATE_ORDER)):
            self.prun(f'run-{index}', [{'n': 1, 'states': [state]}], created=f'2026-11-05T{10 + index:02d}:00:00Z')
        self.assertEqual(self.states('--all-states'), PROPOSAL_STATE_ORDER)

    def test_state_is_the_last_run_entry(self):
        self.prun('r1', [{'n': 1, 'states': ['open', 'revising', 'approved']}, {'n': 2, 'states': ['approved', 'open']}])
        self.assertEqual({i['id']: i['state'] for i in self.full()['proposals']},
                         {'r1/P1': 'approved', 'r1/P2': 'open'})

    def test_within_a_state_newest_first_then_run_id_and_number_descending(self):
        self.prun('r1', [{'n': 1}], created='2026-11-05T10:00:00Z')
        self.prun('r2', [{'n': 1}, {'n': 2}], created='2026-11-05T11:00:00Z')
        self.prun('r3', [{'n': 1}, {'n': 2}], created='2026-11-05T11:00:00Z')
        self.prun('r0', [{'n': 1}], created='2026-11-05T12:00:00Z')
        self.assertEqual(self.ids(), ['r0/P1', 'r3/P2', 'r3/P1', 'r2/P2', 'r2/P1', 'r1/P1'])

    def test_number_order_is_numeric_not_textual(self):
        self.prun('r1', [{'n': 2}, {'n': 10}, {'n': 9}])
        self.assertEqual(self.ids(), ['r1/P10', 'r1/P9', 'r1/P2'])

    def test_created_at_is_normalised_to_utc(self):
        self.prun('r1', [{'n': 1}], created='2026-11-05T13:00:00+03:00')
        self.assertEqual(self.full()['proposals'][0]['created_at'], '2026-11-05T10:00:00Z')

    def test_run_id_scope_includes_attempt_runs_only(self):
        for name in ('r1', 'r1.2', 'r10', 'xr1', 'r1-b'):
            self.prun(name, [{'n': 1}])
        self.assertEqual(sorted(self.ids('--run-id', 'r1')), ['r1.2/P1', 'r1/P1'])
        self.assertEqual(self.ids('--run-id', 'r10'), ['r10/P1'])
        self.assertEqual(sorted(self.ids('--run-id', 'r1.2')), ['r1.2/P1'])

    def test_run_id_scope_filters_registry_entries_too(self):
        self.prun('r1', [{'n': 1, 'states': ['approved']}])
        self.put_registry([self.pentry('gone/P1'), self.pentry('r1.3/P2'), self.pentry('r10/P1')])
        self.assertEqual(sorted(self.ids('--run-id', 'r1', '--all-states')), ['r1.3/P2', 'r1/P1'])

    def test_proposal_without_a_state_entry_is_absent(self):
        self.prun('r1', [{'n': 1, 'states': []}, {'n': 2}])
        self.assertEqual(self.ids('--all-states'), ['r1/P2'])

    # ---- pierwszeństwo rejestru ----
    def test_registry_state_wins_over_the_run_file(self):
        self.prun('r1', [{'n': 1, 'states': ['open']}, {'n': 2, 'states': ['approved']}, {'n': 3, 'states': ['open']}])
        self.put_registry([self.pentry('r1/P1', 'rejected'), self.pentry('r1/P2', 'applied', ref=SHA_ONE)])
        self.assertEqual(self.ids(), ['r1/P3'])
        data = self.full('--all-states')
        self.assertEqual({i['id']: i['state'] for i in data['proposals']},
                         {'r1/P3': 'open', 'r1/P1': 'rejected', 'r1/P2': 'applied'})

    def test_registry_applied_wins_over_a_run_file_that_says_rejected(self):
        self.prun('r1', [{'n': 1, 'states': ['rejected']}])
        self.put_registry([self.pentry('r1/P1', 'applied', ref=SHA_ONE)])
        self.assertEqual([i['state'] for i in self.full('--all-states')['proposals']], ['applied'])

    def test_registry_keeps_run_metadata_for_a_listed_proposal(self):
        self.prun('r1', [{'n': 1, 'states': ['open'], 'title': 'z przebiegu', 'owner': 'docs', 'signals': ['S1']}],
                  files=['retro-P1.diff'])
        self.put_registry([self.pentry('r1/P1', 'rejected')])
        [item] = self.full('--all-states')['proposals']
        self.assertEqual((item['title'], item['owner'], item['signals'], item['keys']),
                         ('z przebiegu', 'docs', 1, ['user-correction']))
        self.assertEqual(item['diff'], '.skillsync/runs/r1/retro-P1.diff')

    def test_registry_only_proposal(self):
        self.put_registry([self.pentry('gone/P3', 'rejected', repo='project', target='docs/workflow.md',
                                       date='2026-10-31'),
                           self.pentry('gone2/P1', 'applied', ref=SHA_ONE, date='2026-10-30')])
        self.assertEqual(self.ids(), [])
        data = self.full('--all-states')
        self.assertEqual(data['proposals'], [
            {'id': 'gone/P3', 'owner': None, 'repo': 'project', 'target': 'docs/workflow.md', 'title': None,
             'state': 'rejected', 'diff': None, 'keys': [], 'signals': 0, 'created_at': '2026-10-31T23:59:59Z'},
            {'id': 'gone2/P1', 'owner': None, 'repo': 'library', 'target': PROPOSAL_TARGET, 'title': None,
             'state': 'applied', 'diff': None, 'keys': [], 'signals': 0, 'created_at': '2026-10-30T23:59:59Z'}])

    def test_registry_with_invalid_section_blocks_the_report(self):
        self.prun('r1', [{'n': 1}])
        self.put_registry([self.pentry('r1/P1', 'applied')])
        data = self.full(code=2)
        self.assertEqual((data['status'], data['decisions']['status']), ('BLOCKED', 'invalid'))

    # ---- klucze (code-review-1.4, minor 1) ----
    def test_unsafe_keys_are_skipped_and_safe_keys_and_aliases_are_kept(self):
        (self.library / 'workflow').mkdir(parents=True, exist_ok=True)
        (self.library / 'workflow/cause-keys.yaml').write_text(yaml.safe_dump(
            {'version': 1, 'keys': {'flaky-tests': {'description': 'd', 'aliases': ['flaky-test']}}}), encoding='utf-8')
        keys = ['ok-key', 'flaky-test', 'bad\nkey', 'Upper', 'under_score', '-lead', 'a' * 49, 'a' * 40,
                'sk-ant-abc123', 'ghp_abcDEF123456', '', 'sp ace']
        signals = [{**RETRO_SIGNAL, 'id': f'S{i}', 'key': key} for i, key in enumerate(keys, 1)]
        self.prun('r1', [{'n': 1, 'signals': [s['id'] for s in signals]}], signals=signals)
        [item] = self.full()['proposals']
        self.assertEqual(item['keys'], ['flaky-tests', 'ok-key'])
        self.assertEqual(item['signals'], len(keys))
        for fragment in ('abcDEF123456', 'sk-ant-abc123', 'bad\\nkey', 'under_score'):
            self.assert_absent(fragment)

    def test_a_canonical_key_that_is_itself_unsafe_is_skipped_after_the_alias(self):
        self.prun('r1', [{'n': 1}], signals=[{**RETRO_SIGNAL, 'key': 'safe-key'}])
        self.assertEqual(self.full()['proposals'][0]['keys'], ['safe-key'])

    # ---- rejestr invalid, obcięcie czytnika, rok 1 ----
    def test_invalid_registry_gives_blocked_with_an_empty_list(self):
        self.prun('r1', [{'n': 1}, {'n': 2}])
        self.put_registry([self.pentry('r1/P1', 'applied')])  # applied bez ref
        data = self.full(code=2)
        self.assertEqual((data['status'], data['decisions']['status']), ('BLOCKED', 'invalid'))
        self.assertEqual((data['proposals'], data['proposals_truncated']), ([], False))
        self.assertEqual(data['coverage']['retro']['runs'], 1)
        self.assertEqual(self.full('--all-states', code=2)['proposals'], [])

    def test_created_at_of_year_one_is_zero_padded(self):
        self.prun('ancient', [{'n': 1}], created='0001-01-01T00:00:00Z')
        self.prun('recent', [{'n': 1}], created='2026-11-05T10:00:00Z')
        found = {i['id'].split('/')[0]: i['created_at'] for i in self.full()['proposals']}
        self.assertEqual(found, {'ancient': '0001-01-01T00:00:00Z', 'recent': '2026-11-05T10:00:00Z'})
        self.assertEqual(self.ids(), ['recent/P1', 'ancient/P1'])

    def test_reader_cut_by_the_run_limit_marks_the_list_truncated(self):
        from unittest import mock
        from skillsync_workflow import retro_scan
        for index in range(3):
            self.prun(f'run-{index}', [{'n': 1}], created=f'2026-11-0{index + 1}T10:00:00Z')
        self.assertIs(self.retro_in_process()['proposals_truncated'], False)
        data = self.retro_in_process(patches=[mock.patch.object(retro_scan, 'MAX_RUNS', 2)])
        self.assertIs(data['proposals_truncated'], True)
        self.assertEqual(len(data['proposals']), 2, 'najstarszy przebieg nie został przeczytany')

    def test_reader_cut_by_the_byte_budget_marks_the_list_truncated(self):
        from unittest import mock
        from skillsync_workflow import retro_scan
        for index in range(3):
            self.prun(f'run-{index}', [{'n': 1}], created=f'2026-11-0{index + 1}T10:00:00Z')
        data = self.retro_in_process(patches=[mock.patch.object(retro_scan, 'MAX_SCAN_BYTES', 1500)])
        self.assertIs(data['proposals_truncated'], True)
        self.assertLess(len(data['proposals']), 3)

    # ---- owner, signals (minor 5) ----
    def test_owner_outside_the_list_is_filtered(self):
        owners = ['skill', 'instruction', 'docs', 'route', 'test', 'hook', 'hacker', 'SKILL', '', 'skill ']
        self.prun('r1', [{'n': i, 'owner': owner} for i, owner in enumerate(owners, 1)])
        found = {i['id']: i['owner'] for i in self.full()['proposals']}
        self.assertEqual([found[f'r1/P{i}'] for i in range(1, len(owners) + 1)],
                         ['skill', 'instruction', 'docs', 'route', 'test', 'hook', None, None, None, None])

    def test_signals_with_a_repeated_or_unknown_id_are_counted_as_listed(self):
        """Obecne zachowanie: `signals` to długość listy z propozycji (bez deduplikacji i bez sprawdzania istnienia),
        a `keys` bierze tylko istniejące sygnały."""
        self.prun('r1', [{'n': 1, 'signals': ['S1', 'S1', 'S9']}])
        [item] = self.full()['proposals']
        self.assertEqual((item['signals'], item['keys']), (3, ['user-correction']))

    # ---- diff ----
    def test_diff_names_and_types(self):
        cases = {
            'ok-plain': (['retro-P1.diff'], 'retro-P1.diff', '.skillsync/runs/ok-plain/retro-P1.diff'),
            'ok-revision': (['retro-P1-2.diff'], 'retro-P1-2.diff', '.skillsync/runs/ok-revision/retro-P1-2.diff'),
            'ok-long': (['retro-P1-12.diff'], 'retro-P1-12.diff', '.skillsync/runs/ok-long/retro-P1-12.diff'),
            'missing': ([], 'retro-P1.diff', None),
            'bad-suffix': (['retro-P1-x.diff'], 'retro-P1-x.diff', None),
            'bad-ext': (['retro-P1.patch'], 'retro-P1.patch', None),
            'other-number': (['retro-P2.diff'], 'retro-P2.diff', None),
            'prefix': (['xretro-P1.diff'], 'xretro-P1.diff', None),
            'free-name': (['notes.diff'], 'notes.diff', None),
            'dotdot': ([], '../retro-P1.diff', None),
            'absolute': ([], '/etc/retro-P1.diff', None),
            'subdir': (['sub/retro-P1.diff'], 'sub/retro-P1.diff', None),
            'null-diff': (['retro-P1.diff'], None, None),
        }
        for run, (files, name, expected) in cases.items():
            folder = self.prun(run, [{'n': 1, 'states': [('open', name)]}])
            for rel in files:
                path = folder / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('x', encoding='utf-8')
        found = {i['id'].split('/')[0]: i['diff'] for i in self.full()['proposals']}
        self.assertEqual(found, {run: expected for run, (_, _, expected) in cases.items()})

    def test_diff_that_is_a_directory_is_null(self):
        folder = self.prun('dir', [{'n': 1}])
        (folder / 'retro-P1.diff').mkdir()
        self.assertIsNone(self.full()['proposals'][0]['diff'])

    def test_diff_that_is_a_symlink_is_null_inside_or_outside_the_run(self):
        require_os_symlinks(self)
        folder = self.prun('inside', [{'n': 1}], files=['real.diff'])
        (folder / 'retro-P1.diff').symlink_to(folder / 'real.diff')
        outside = self.base / 'outside.diff'
        outside.write_text('x', encoding='utf-8')
        folder = self.prun('outside', [{'n': 1}])
        (folder / 'retro-P1.diff').symlink_to(outside)
        found = {i['id'].split('/')[0]: i['diff'] for i in self.full()['proposals']}
        self.assertEqual(found, {'inside': None, 'outside': None})

    def test_diff_is_taken_from_the_last_decision_entry(self):
        self.prun('r1', [{'n': 1, 'states': [('open', 'retro-P1.diff'), ('revising', 'retro-P1.diff'),
                                             ('open', 'retro-P1-2.diff')]}], files=['retro-P1.diff', 'retro-P1-2.diff'])
        self.assertEqual(self.full()['proposals'][0]['diff'], '.skillsync/runs/r1/retro-P1-2.diff')

    # ---- maskowanie i tytuł ----
    def test_ids_targets_diffs_and_keys_are_masked(self):
        run = 'pre-ghp_abcDEF123456'
        long_name = 'a1b2c3d4' * 5
        self.prun(run, [{'n': 1, 'target': f'docs/{long_name}.md', 'title': 'tytuł'},
                        {'n': 2, 'target': 'docs/zażółć.md'}],
                  signals=[{**RETRO_SIGNAL, 'key': 'ghp_abcDEF123456'}], files=['retro-P1.diff'])  # gitleaks:allow (sztuczny token w teście maskowania)
        data = self.full()
        by_id = {i['id']: i for i in data['proposals']}
        first, second = by_id['pre-[REDACTED]/P1'], by_id['pre-[REDACTED]/P2']
        self.assertEqual(first['target'], 'docs/[REDACTED].md')
        self.assertEqual(first['diff'], '.skillsync/runs/pre-[REDACTED]/retro-P1.diff')
        self.assertEqual(first['keys'], [], 'klucz będący tokenem jest pomijany, nie maskowany')
        self.assertRegex(second['target'], r'^docs/invalid-name:[0-9a-f]{12}$')
        self.assert_absent('ghp_abcDEF123456', long_name, 'zażółć')

    def test_title_with_tokens_is_filtered(self):
        for index, token in enumerate(TOKENS):
            self.prun(f't{index}', [{'n': 1, 'title': f'Zmień {token} w opisie'}])
        titles = {i['id'].split('/')[0]: i['title'] for i in self.full()['proposals']}
        self.assertEqual(set(titles.values()), {'Zmień [REDACTED] w opisie'})
        for token in TOKENS:
            self.assert_absent(token)

    def test_title_long_alphanumeric_run_is_filtered(self):
        self.prun('r1', [{'n': 1, 'title': 'sekret ' + 'a1b2c3d4' * 5 + ' koniec'}])
        self.assertEqual(self.full()['proposals'][0]['title'], 'sekret [REDACTED] koniec')

    def test_title_is_cut_at_120_characters(self):
        self.prun('r120', [{'n': 1, 'title': 'ą' * 120}])
        self.prun('r121', [{'n': 1, 'title': 'ą' * 121}])
        self.prun('r500', [{'n': 1, 'title': 'ą ' * 250}])
        titles = {i['id'].split('/')[0]: i['title'] for i in self.full()['proposals']}
        self.assertEqual(titles['r120'], 'ą' * 120)
        self.assertEqual(titles['r121'], 'ą' * 120)
        self.assertLessEqual(len(titles['r500']), 120)

    def test_token_at_the_cut_boundary_never_leaks_a_fragment(self):
        self.prun('r1', [{'n': 1, 'title': 'x' * 115 + ' ghp_abcDEF123456'}])
        self.prun('r2', [{'n': 1, 'title': 'x ' * 55 + 'ghp_abcDEF123456'}])
        titles = [i['title'] for i in self.full()['proposals']]
        for title in titles:
            self.assertLessEqual(len(title), 120)
            self.assertNotIn('ghp_', title)
            self.assertNotIn('abcDEF', title)
        self.assert_absent('abcDEF123456')

    def test_missing_title_and_owner_are_null(self):
        folder = self.prun('r1', [{'n': 1}])
        data = json.loads((folder / 'retro-signals.json').read_text())
        del data['proposals'][0]['title'], data['proposals'][0]['owner']
        (folder / 'retro-signals.json').write_text(json.dumps(data))
        [item] = self.full()['proposals']
        self.assertEqual((item['title'], item['owner']), (None, None))

    def test_keys_and_signals_count_only_the_proposals_own_signals(self):
        self.prun('r1', [{'n': 1, 'signals': ['S2', 'S3']}, {'n': 2, 'signals': ['S1']}, {'n': 3, 'signals': []}],
                  signals=[RETRO_SIGNAL, {**RETRO_SIGNAL, 'id': 'S2', 'key': 'b-key'},
                           {**RETRO_SIGNAL, 'id': 'S3', 'key': 'a-key'}, {**RETRO_SIGNAL, 'id': 'S4', 'key': 'a-key'}])
        by_id = {i['id']: i for i in self.full()['proposals']}
        self.assertEqual((by_id['r1/P1']['keys'], by_id['r1/P1']['signals']), (['a-key', 'b-key'], 2))
        self.assertEqual((by_id['r1/P2']['keys'], by_id['r1/P2']['signals']), (['user-correction'], 1))
        self.assertEqual((by_id['r1/P3']['keys'], by_id['r1/P3']['signals']), ([], 0))

    def test_keys_are_deduplicated_and_sorted(self):
        self.prun('r1', [{'n': 1, 'signals': ['S1', 'S2', 'S3']}],
                  signals=[{**RETRO_SIGNAL, 'key': 'zz-key'}, {**RETRO_SIGNAL, 'id': 'S2', 'key': 'aa-key'},
                           {**RETRO_SIGNAL, 'id': 'S3', 'key': 'zz-key'}])
        [item] = self.full()['proposals']
        self.assertEqual((item['keys'], item['signals']), (['aa-key', 'zz-key'], 3))

    # ---- limit 50 ----
    def many(self, count):
        for index in range(count):
            self.prun(f'run-{index:03d}', [{'n': 1}], created=f'2026-10-{1 + index // 24:02d}T{index % 24:02d}:00:00Z')

    def test_exactly_fifty_proposals_are_not_truncated(self):
        self.many(50)
        data = self.full()
        self.assertEqual((len(data['proposals']), data['proposals_truncated']), (50, False))

    def test_fifty_one_proposals_are_truncated_and_the_oldest_is_dropped(self):
        self.many(51)
        data = self.full()
        self.assertEqual((len(data['proposals']), data['proposals_truncated']), (50, True))
        self.assertNotIn('run-000/P1', [i['id'] for i in data['proposals']])
        self.assertEqual(data['proposals'][0]['id'], 'run-050/P1')

    def test_limit_applies_after_the_state_filter(self):
        self.many(51)
        self.prun('final-run', [{'n': i, 'states': ['rejected']} for i in range(1, 6)])
        data = self.full()  # rozstrzygnięte nie liczą się do limitu bez --all-states
        self.assertEqual((len(data['proposals']), data['proposals_truncated']), (50, True))
        self.assertTrue(all(i['state'] == 'open' for i in data['proposals']))
        few = self.full('--run-id', 'final-run', '--all-states')
        self.assertEqual((len(few['proposals']), few['proposals_truncated']), (5, False))

    def test_all_states_counts_final_ones_toward_the_limit(self):
        self.many(48)
        self.prun('final-run', [{'n': i, 'states': ['rejected']} for i in range(1, 4)])
        self.assertEqual((len(self.full()['proposals']), self.full()['proposals_truncated']), (48, False))
        data = self.full('--all-states')
        self.assertEqual((len(data['proposals']), data['proposals_truncated']), (50, True))
        self.assertEqual(data['proposals'][-1]['state'], 'rejected')

    # ---- status, schema, tylko odczyt ----
    def test_invalid_retro_file_does_not_change_the_status(self):
        self.run_file('cp', rec('a'))
        self.prun('good', [{'n': 1}])
        self.assertEqual(self.full()['status'], 'PASS')
        bad = self.prun('bad', [{'n': 1}])
        (bad / 'retro-signals.json').write_text('{not json', encoding='utf-8')
        data = self.full()
        self.assertEqual((data['schema'], data['status']), (1, 'PASS'))
        self.assertEqual(self.ids(), ['good/P1'])
        self.assertEqual(data['coverage']['retro']['invalid_files'], 1)

    def test_reading_proposals_changes_nothing_on_disk(self):
        self.prun('r1', [{'n': 1}], files=['retro-P1.diff'])
        self.put_registry([self.pentry('r1/P1', 'rejected')])
        before = self.disk()
        self.full('--all-states')
        self.assertEqual(self.disk(), before)


class RetroCoverageSection(RetroListBase):
    """K7 i K9: `coverage.retro`."""

    def final_run(self, run_id, at, *records, retro=None):
        """Przebieg `<id>.json`: jeden krok i rekord `final` z `recorded_at` = `at`; `retro` = (status, czas) rekordu."""
        items = [rec('step', recorded_at='2026-10-01T09:00:00+00:00')]
        if retro is not None:
            items.append(rec('retro-quality', retro[0], recorded_at=retro[1]))
        items.append(rec('closing', recorded_at=at, final=True))
        self.run_file(run_id, *items, *records, close=False)

    def retro_cov(self, *args):
        return self.full(*args)['coverage']['retro']

    def test_runs_counts_valid_retro_runs_only_in_scope(self):
        self.prun('a', [{'n': 1}])
        self.prun('a.2', [])
        self.prun('b', [{'n': 1}], signals=[])
        self.assertEqual(self.retro_cov()['runs'], 3)
        self.assertEqual(self.retro_cov('--run-id', 'a')['runs'], 2)
        self.assertEqual(self.retro_cov('--run-id', 'b')['runs'], 1)

    def test_runs_ignores_directories_without_retro_files(self):
        self.runs_dir('plain').joinpath('note.md').write_text('x')
        self.run_file('cp', rec('a'))
        self.assertEqual(self.retro_cov(), {'runs': 0, 'invalid_files': 0, 'final_without_retro': 0})

    def test_invalid_files_counts_files_not_runs(self):
        two = self.prun('two', [{'n': 1}])
        (two / 'retro-signals.json').write_text('{', encoding='utf-8')
        (two / 'retro-decisions.json').write_text('[]', encoding='utf-8')
        one = self.prun('one', [{'n': 1}])
        (one / 'retro-decisions.json').write_text('{"schema": 2}', encoding='utf-8')
        self.prun('fine', [{'n': 1}])
        self.run_file('cp', rec('a'))
        data = self.full()
        self.assertEqual(data['coverage']['retro']['invalid_files'], 3)
        self.assertEqual(self.full('--run-id', 'two')['coverage']['retro']['invalid_files'], 2)
        self.assertEqual(self.full('--run-id', 'one')['coverage']['retro']['invalid_files'], 1)
        self.assertEqual(self.full('--run-id', 'fine')['coverage']['retro']['invalid_files'], 0)
        self.assertEqual((data['status'], data['schema']), ('PASS', 1))

    def test_invalid_signals_content_counts_too(self):
        bad = self.prun('bad', [{'n': 1}])
        data = json.loads((bad / 'retro-signals.json').read_text())
        data['created_at'] = 'wczoraj'
        (bad / 'retro-signals.json').write_text(json.dumps(data))
        self.assertEqual(self.retro_cov()['invalid_files'], 1)

    def test_final_without_retro_boundary_is_the_utc_day(self):
        self.final_run('before', '2026-11-04T23:59:59+00:00')
        self.final_run('start', '2026-11-05T00:00:00+00:00')
        self.final_run('later', '2026-12-01T12:00:00+00:00')
        self.final_run('offset-before', '2026-11-05T01:00:00+03:00')  # 2026-11-04T22:00Z
        self.final_run('offset-after', '2026-11-04T23:00:00-02:00')   # 2026-11-05T01:00Z
        self.assertEqual(self.retro_cov()['final_without_retro'], 3)
        self.assertEqual(self.retro_cov('--run-id', 'before')['final_without_retro'], 0)
        self.assertEqual(self.retro_cov('--run-id', 'start')['final_without_retro'], 1)

    def test_final_with_a_retrospective_before_it_is_not_counted(self):
        self.final_run('ok', '2026-11-05T12:00:00+00:00', retro=('PASS', '2026-11-05T11:59:59+00:00'))
        self.assertEqual(self.retro_cov()['final_without_retro'], 0)

    def test_retrospective_that_does_not_count(self):
        self.final_run('fail', '2026-11-05T12:00:00+00:00', retro=('FAIL', '2026-11-05T11:00:00+00:00'))
        self.final_run('after', '2026-11-05T12:00:00+00:00', retro=('PASS', '2026-11-05T12:00:01+00:00'))
        self.final_run('same', '2026-11-05T12:00:00+00:00', retro=('PASS', '2026-11-05T12:00:00+00:00'))
        self.final_run('blocked', '2026-11-05T12:00:00+00:00', retro=('BLOCKED', '2026-11-05T11:00:00+00:00'))
        self.assertEqual(self.retro_cov()['final_without_retro'], 4)

    def test_retrospective_in_an_attempt_file_counts_and_the_identity_is_counted_once(self):
        self.final_run('x', '2026-11-05T12:00:00+00:00')
        self.run_file('x.2', rec('retro-quality', recorded_at='2026-11-05T11:00:00+00:00'), close=False)
        self.assertEqual(self.retro_cov()['final_without_retro'], 0)
        self.final_run('y', '2026-11-05T12:00:00+00:00')
        self.final_run('y.2', '2026-11-05T13:00:00+00:00')
        self.final_run('y.3', '2026-11-05T14:00:00+00:00')
        self.assertEqual(self.retro_cov()['final_without_retro'], 1)

    def test_requirement_is_set_by_the_first_final_of_the_identity(self):
        self.final_run('z', '2026-11-05T12:00:00+00:00')
        self.run_file('z.2', rec('retro-quality', recorded_at='2026-11-05T13:00:00+00:00'),
                      rec('closing', recorded_at='2026-11-05T14:00:00+00:00', final=True), close=False)
        self.assertEqual(self.retro_cov()['final_without_retro'], 1)

    def test_attempt_scope_sees_the_retrospective_of_the_base_file(self):
        self.run_file('x', rec('step', recorded_at='2026-10-01T09:00:00+00:00'),
                      rec('retro-quality', recorded_at='2026-11-05T11:00:00+00:00'), close=False)
        self.final_run('x.2', '2026-11-05T12:00:00+00:00')
        self.assertEqual(self.retro_cov('--run-id', 'x.2')['final_without_retro'], 0)
        self.assertEqual(self.retro_cov()['final_without_retro'], 0)
        self.assertEqual(self.retro_cov('--run-id', 'x')['final_without_retro'], 0)

    def test_attempt_scope_counts_when_no_file_of_the_identity_has_a_retrospective(self):
        self.final_run('x', '2026-11-05T10:00:00+00:00')
        self.run_file('x.2', rec('step', recorded_at='2026-11-05T11:00:00+00:00'), close=False)
        self.assertEqual(self.retro_cov('--run-id', 'x.2')['final_without_retro'], 1)
        self.assertEqual(self.retro_cov('--run-id', 'x')['final_without_retro'], 1)
        self.assertEqual(self.retro_cov()['final_without_retro'], 1)

    def test_attempt_scope_does_not_pull_in_other_identities(self):
        self.final_run('x', '2026-11-05T10:00:00+00:00')
        self.final_run('y', '2026-11-05T10:00:00+00:00')
        self.run_file('x.2', rec('step', recorded_at='2026-11-05T11:00:00+00:00'), close=False)
        self.assertEqual(self.retro_cov('--run-id', 'x.2')['final_without_retro'], 1)
        self.assertEqual(self.retro_cov('--run-id', 'y')['final_without_retro'], 1)
        self.assertEqual(self.retro_cov()['final_without_retro'], 2)

    def test_run_without_final_is_not_counted(self):
        self.run_file('open', rec('step', recorded_at='2026-11-05T12:00:00+00:00'), close=False)
        self.assertEqual(self.retro_cov()['final_without_retro'], 0)

    def test_old_final_without_retro_is_not_counted(self):
        self.final_run('old', '2026-10-30T12:00:00+00:00')
        self.assertEqual(self.retro_cov()['final_without_retro'], 0)

    def test_coverage_is_scoped_by_run_id(self):
        self.final_run('s1', '2026-11-05T12:00:00+00:00')
        self.final_run('s2', '2026-11-05T12:00:00+00:00')
        self.assertEqual(self.retro_cov()['final_without_retro'], 2)
        self.assertEqual(self.retro_cov('--run-id', 's1')['final_without_retro'], 1)

    def test_retro_quality_step_is_enforced_by_name_and_status_only(self):
        self.final_run('named', '2026-11-05T12:00:00+00:00')
        self.run_file('named.2', rec('retro-quality-extra', recorded_at='2026-11-05T11:00:00+00:00'), close=False)
        self.assertEqual(self.retro_cov()['final_without_retro'], 1)



# ---------------------------------------------------------------------------------------------------------------
# R2 krok 1.4t: fixture kontraktu dla Skippstera (`tests/fixtures/retro-contract/`)
# ---------------------------------------------------------------------------------------------------------------
CONTRACT_DIR = Path(__file__).resolve().parent / 'fixtures' / 'retro-contract'
CONTRACT_ENV = 'SKILLSYNC_UPDATE_RETRO_CONTRACT'
CONTRACT_FILES = ('retro-json-proposals.json', 'retro-json-not-run.json', 'retro-json-truncated.json',
                  'retro-decide-proposal-rejected.json', 'retro-decide-proposal-applied.json')
CONTRACT_REFRESH = (CONTRACT_ENV + "=1 python3 -m unittest discover -s tests -p 'test_skillsync_retro.py' "
                    "-k RetroContractFixtures")
FROZEN_DAY = (2026, 11, 5, 12, 0, 0)
PERSON_NAMES = tuple(name[::-1] for name in ('sicrab', 'śicrab', 'łewap', 'lewap', 'ikcicrab', 'yppiks', 'ahtagan', 'ybliB'.lower(), 'aduram'))
ABSOLUTE_PATH = re.compile(r'(?:^|[\s"\'(=:,\[])(?:/(?:home|tmp|Users|var|root|usr|etc|opt|mnt|private)/|[A-Za-z]:[\\\\/])')


class RetroContractFixtures(RetroListBase):
    """Wyniki `retro --json` i `retro-decide --proposal` z prawdziwego kodu, porównywane z plikami, które Skippster
    kopiuje (z sha256). Odświeżanie: zmienna `SKILLSYNC_UPDATE_RETRO_CONTRACT=1`."""

    def serialise(self, data):
        return json.dumps(data, ensure_ascii=False, indent=2) + '\n'

    def check_fixture(self, name, data):
        text = self.serialise(data)
        path = CONTRACT_DIR / name
        if os.environ.get(CONTRACT_ENV) == '1':
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
            return
        self.assertTrue(path.is_file(), f'brak pliku {name}; wygeneruj go: {CONTRACT_REFRESH}')
        self.assertTrue(path.read_text(encoding='utf-8') == text,
                        f'Fixture {name} różni się od wyniku kodu. Jeśli zmiana kontraktu jest zamierzona, odśwież pliki '
                        f'i skopiuj je do Skippstera z nowym sha256: {CONTRACT_REFRESH}')

    # ---- generowanie ----
    def contract_project(self):
        """Deterministyczny projekt: propozycje we wszystkich stanach, z diffem i bez, rejestr, jeden zły plik retro."""
        self.run_file('2026-11-05-przyklad', rec('step', recorded_at='2026-11-05T09:00:00+00:00'),
                      rec('closing', recorded_at='2026-11-05T10:00:00+00:00', final=True), close=False)
        two = [RETRO_SIGNAL, {**RETRO_SIGNAL, 'id': 'S2', 'key': 'unneeded-question'}]
        self.prun('2026-11-03-alfa', [
            {'n': 1, 'states': ['open'], 'title': 'Doprecyzuj regułę pętli', 'owner': 'skill', 'signals': ['S1', 'S2']},
            {'n': 2, 'states': ['open', 'approved'], 'title': 'Dodaj test przypadku', 'owner': 'test', 'signals': ['S1']},
            {'n': 3, 'states': ['open', 'revising'], 'title': 'Skróć instrukcję', 'owner': 'instruction',
             'repo': 'project', 'target': 'docs/workflow.md', 'signals': ['S2']}],
            created='2026-11-03T09:00:00Z', signals=two, files=['retro-P1.diff', 'retro-P3.diff'])
        self.prun('2026-11-04-beta', [
            {'n': 1, 'states': ['open', 'deferred'], 'title': 'Poprawka w worktree', 'owner': 'docs', 'signals': ['S1']},
            {'n': 2, 'states': ['open', 'stale'], 'title': 'Diff przestał się stosować', 'owner': 'route',
             'signals': ['S2']},
            {'n': 3, 'states': ['open', 'approved', 'failed'], 'title': 'Walidacja nie przeszła', 'owner': 'hook',
             'signals': ['S1']},
            {'n': 4, 'states': ['open', 'approved', 'handoff'], 'title': 'Przekazana do bugfixu', 'owner': 'test',
             'signals': ['S1', 'S2']},
            {'n': 5, 'states': ['open', 'rejected'], 'title': 'Odrzucona przez użytkownika', 'owner': 'skill',
             'signals': ['S2']},
            {'n': 6, 'states': ['open', 'approved', 'applied'], 'title': 'Wprowadzona poprawka', 'owner': 'skill',
             'signals': ['S1']}],
            created='2026-11-04T09:00:00Z', signals=two, files=['retro-P1.diff', 'retro-P4.diff'])
        self.prun('2026-11-05-gamma', [
            {'n': 1, 'states': ['open'], 'title': 'Rejestr ma pierwszeństwo przed plikiem przebiegu', 'owner': 'skill',
             'signals': ['S1']},
            {'n': 2, 'states': ['open'], 'title': 'Tytuł z tokenem ghp_abcDEF123456 jest filtrowany', 'owner': 'docs',
             'signals': ['S1']}],
            created='2026-11-05T08:00:00Z', files=['retro-P1.diff'])
        broken = self.prun('2026-11-02-zepsuty', [{'n': 1}], created='2026-11-02T09:00:00Z')
        (broken / 'retro-signals.json').write_text('{', encoding='utf-8')
        self.put_registry([
            self.pentry('2026-11-05-gamma/P1', 'rejected', date='2026-11-05'),
            self.pentry('2026-10-20-stary/P1', 'applied', repo='project', target='docs/workflow.md',
                        date='2026-10-20', ref=SHA_ONE),
            self.pentry('2026-10-21-inny/P2', 'rejected', date='2026-10-21')])

    def not_run_project(self):
        """Projekt bez checkpointów: `retro` ma status NOT_RUN, a lista propozycji nie jest pusta."""
        self.prun('2026-11-04-beta', [
            {'n': 1, 'states': ['open', 'approved'], 'title': 'Propozycja z przebiegu', 'owner': 'skill',
             'signals': ['S1']}], created='2026-11-04T09:00:00Z', files=['retro-P1.diff'])
        self.put_registry([self.pentry('2026-10-20-stary/P1', 'rejected', date='2026-10-20')])

    def frozen(self):
        from unittest import mock
        from skillsync_workflow import retro
        real = retro.datetime

        class Frozen(real):
            @classmethod
            def now(cls, tz=None):
                moment = real(*FROZEN_DAY, tzinfo=timezone.utc)
                return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

        return mock.patch.object(retro, 'datetime', Frozen)

    def decide_frozen(self, *args):
        code, data = self.in_process(*args, patches=[self.frozen()])
        self.assertEqual(code, 0, data)
        return data

    # ---- porównania z plikami ----
    def test_retro_json_with_proposals_matches_the_fixture(self):
        self.contract_project()
        data = self.full('--all-states')
        self.check_fixture('retro-json-proposals.json', data)

    def test_retro_json_with_status_not_run_matches_the_fixture(self):
        self.not_run_project()
        data = self.full('--all-states')
        self.assertEqual(data['status'], 'NOT_RUN')
        self.check_fixture('retro-json-not-run.json', data)

    def test_retro_json_truncated_matches_the_fixture(self):
        from unittest import mock
        from skillsync_workflow import retro
        self.contract_project()
        data = self.retro_in_process('--all-states', patches=[mock.patch.object(retro, 'MAX_PROPOSALS', 3)])
        self.check_fixture('retro-json-truncated.json', data)

    def test_decide_rejected_matches_the_fixture(self):
        self.prun('2026-11-05-przyklad', [{'n': 1, 'states': ['open']}])
        data = self.decide_frozen('--proposal', '2026-11-05-przyklad/P1', '--decision', 'rejected')
        self.assertIsNone(data['previous'])
        self.check_fixture('retro-decide-proposal-rejected.json', data)

    def test_decide_applied_with_previous_matches_the_fixture(self):
        self.prun('2026-11-05-przyklad', [{'n': 1, 'states': ['open', 'approved']}])
        self.put_registry([self.pentry('2026-11-05-przyklad/P1', 'rejected', date='2026-11-01')])
        data = self.decide_frozen('--proposal', '2026-11-05-przyklad/P1', '--decision', 'applied', '--ref', SHA_ONE,
                                  '--replace')
        self.assertEqual(data['previous'], {'decision': 'rejected', 'date': '2026-11-01'})
        self.check_fixture('retro-decide-proposal-applied.json', data)

    # ---- zawartość fixture'ów ----
    def read_fixture(self, name):
        path = CONTRACT_DIR / name
        self.assertTrue(path.is_file(), f'brak pliku {name}: {CONTRACT_REFRESH}')
        return json.loads(path.read_text(encoding='utf-8'))

    def test_retro_fixture_covers_every_state_and_the_interesting_cases(self):
        data = self.read_fixture('retro-json-proposals.json')
        items = data['proposals']
        self.assertEqual({i['state'] for i in items}, set(PROPOSAL_STATE_ORDER))
        self.assertEqual([i['state'] for i in items], sorted((i['state'] for i in items), key=PROPOSAL_STATE_ORDER.index))
        self.assertTrue(any(i['diff'] for i in items) and any(i['diff'] is None for i in items))
        self.assertTrue(any(i['owner'] is None and i['title'] is None and i['signals'] == 0 for i in items), 'tylko rejestr')
        self.assertTrue(any('[REDACTED]' in (i['title'] or '') for i in items), 'tytuł z tokenem')
        self.assertFalse(data['proposals_truncated'])
        self.assertEqual(data['coverage']['retro']['invalid_files'], 1)
        self.assertEqual(data['coverage']['retro']['final_without_retro'], 1)
        self.assertEqual(data['schema'], 1)

    def test_not_run_and_truncated_fixtures_keep_their_purpose(self):
        not_run = self.read_fixture('retro-json-not-run.json')
        self.assertEqual(not_run['status'], 'NOT_RUN')
        self.assertTrue(all(item['class'] == 'no_data' for item in not_run['runs']), 'bez checkpointów')
        self.assertEqual([i['id'] for i in not_run['proposals']], ['2026-11-04-beta/P1', '2026-10-20-stary/P1'])
        self.assertTrue(any(i['owner'] is None for i in not_run['proposals']), 'propozycja tylko z rejestru')
        self.assertFalse(not_run['proposals_truncated'])
        truncated = self.read_fixture('retro-json-truncated.json')
        self.assertIs(truncated['proposals_truncated'], True)
        self.assertEqual(len(truncated['proposals']), 3)
        self.assertEqual(truncated['status'], 'PASS')

    def test_decide_fixtures_have_the_contract_shape(self):
        rejected = self.read_fixture('retro-decide-proposal-rejected.json')
        applied = self.read_fixture('retro-decide-proposal-applied.json')
        for data, kind in ((rejected, 'rejected'), (applied, 'applied')):
            self.assertEqual(set(data), {'schema', 'status', 'path', 'decision', 'previous', 'writes'})
            self.assertEqual((data['schema'], data['status']), (1, 'PASS'))
            self.assertEqual(data['decision'], {'proposal': '2026-11-05-przyklad/P1', 'decision': kind,
                                                'date': '2026-11-05'})
            self.assertEqual(data['writes'], {'run': 'written', 'registry': 'written'})
        self.assertIsNone(rejected['previous'])
        self.assertEqual(applied['previous'], {'decision': 'rejected', 'date': '2026-11-01'})

    def test_fixture_directory_has_exactly_the_agreed_files(self):
        self.assertEqual(sorted(p.name for p in CONTRACT_DIR.iterdir()), sorted([*CONTRACT_FILES, 'README.md']))

    def test_fixtures_have_no_absolute_paths_or_personal_names(self):
        for name in (*CONTRACT_FILES, 'README.md'):
            text = (CONTRACT_DIR / name).read_text(encoding='utf-8')
            self.assertIsNone(ABSOLUTE_PATH.search(text), f'{name}: ścieżka bezwzględna')
            self.assertNotIn(str(self.base), text)
            self.assertNotIn(str(Path.home()), text)
            lowered = text.lower()
            for person in PERSON_NAMES:
                self.assertNotIn(person, lowered, f'{name}: nazwa osoby')
            for token in TOKENS:
                self.assertNotIn(token, text)

    def test_generated_results_have_no_absolute_paths(self):
        self.contract_project()
        text = json.dumps(self.full('--all-states'), ensure_ascii=False)
        self.assertIsNone(ABSOLUTE_PATH.search(text))
        self.assertNotIn(str(self.base), text)

    def test_readme_explains_origin_sha256_and_refresh(self):
        text = (CONTRACT_DIR / 'README.md').read_text(encoding='utf-8')
        for fragment in ('sha256', CONTRACT_ENV, 'Skippster', 'retro --json', 'retro-decide --proposal',
                         *CONTRACT_FILES):
            self.assertIn(fragment, text)

    def test_update_mode_rewrites_the_file_and_the_default_mode_reports_the_difference(self):
        """Tryb odświeżania pracuje na katalogu tymczasowym (podmieniona stała katalogu), bez dotykania plików w repozytorium."""
        from unittest import mock
        name = 'retro-decide-proposal-rejected.json'
        tracked = (CONTRACT_DIR / name).read_bytes()
        scratch = self.base / 'contract-copy'
        scratch.mkdir()
        (scratch / name).write_bytes(tracked)
        with mock.patch.dict(globals(), {'CONTRACT_DIR': scratch}):
            with mock.patch.dict(os.environ, {CONTRACT_ENV: ''}):
                with self.assertRaises(AssertionError) as caught:
                    self.check_fixture(name, {'schema': 1, 'zmiana': True})
            self.assertIn(CONTRACT_ENV, str(caught.exception))
            self.assertEqual((scratch / name).read_bytes(), tracked, 'tryb domyślny nie zapisuje')
            with mock.patch.dict(os.environ, {CONTRACT_ENV: '1'}):
                self.check_fixture(name, {'schema': 1, 'zmiana': True})
            self.assertEqual(json.loads((scratch / name).read_text(encoding='utf-8')), {'schema': 1, 'zmiana': True})
            with mock.patch.dict(os.environ, {CONTRACT_ENV: ''}):
                self.check_fixture(name, {'schema': 1, 'zmiana': True})  # po odświeżeniu porównanie przechodzi
        self.assertEqual((CONTRACT_DIR / name).read_bytes(), tracked, 'plik w repozytorium nietknięty')

if __name__ == '__main__':
    unittest.main()
