"""metrics --classify: deterministyczna klasyfikacja przebiegów z zapisanych checkpointów."""
import itertools
import json
import os
import subprocess
import sys
import unittest

from test_skillsync_workflow import CLI, WorkflowFixture

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skillsync_workflow.runs import _classify_run, _write_warnings
from layers_helpers import needs_os_symlinks  # noqa: E402

needs_fifo = unittest.skipUnless(hasattr(os, 'mkfifo'), 'mkfifo unavailable')

DIGEST = 'a' * 64


def rec(step, status='PASS', duration=10, cause=None, cost=None, attempt=1, **extra):
    """Rekord checkpointu; pola opcjonalne R2 (final, cause_key, owner, denied, inherited) tylko przez **extra."""
    return {'step': step, 'status': status, 'next': 'n', 'recorded_at': '2026-10-29T10:00:00+00:00',
            'provenance': 'test', 'attempt': attempt, 'cause': cause, 'duration_seconds': duration, 'cost': cost,
            'cost_unit': 'USD', 'fingerprint': {'head': None, 'digest': DIGEST},
            'evidence': [{'path': 'e.log', 'sha256': DIGEST}] if status == 'PASS' else [], **extra}


class ClassifyBase(WorkflowFixture):
    def run_file(self, ident, *records, close=True):
        """close=True (N1): ostatni rekord PASS dostaje final=True, chyba że ma już własną wartość."""
        records = list(records)
        if close and records and records[-1]['status'] == 'PASS' and 'final' not in records[-1]:
            records[-1] = {**records[-1], 'final': True}
        self.write(f'.skillsync/runs/{ident}.json', json.dumps(
            {'schema_version': 1, 'run_id': ident, 'checkpoints': records}))

    def only(self, *records, close=True, ident='r'):
        self.run_file(ident, *records, close=close)
        return self.by_id(self.classify())[ident]

    def runs_dir(self, *parts):
        path = self.root.joinpath('.skillsync/runs', *parts)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def classify(self):
        return self.report('metrics', '--classify')['classification']

    def by_id(self, data):
        return {item['id']: item for item in data['runs']}


class CheckpointWarningMatchesClass(unittest.TestCase):
    """Ostrzeżenie zapisu i klasa `unexplained` to ten sam warunek, na wszystkich krótkich sekwencjach rekordów."""

    VARIANTS = [
        {'status': 'PASS'}, {'status': 'PASS', 'attempt': 2}, {'status': 'PASS', 'cause': 'x'},
        {'status': 'PASS', 'evidence': [{'path': 'o.log', 'sha256': 'b' * 64}]},
        {'status': 'FAIL'}, {'status': 'FAIL', 'cause': 'x'}, {'status': 'FAIL', 'cause': '  '},
        {'status': 'FAIL', 'cause_key': 'test-gap'}, {'status': 'BLOCKED'}, {'status': 'NOT_RUN'},
        {'status': 'FAIL', 'evidence': [{'path': 'o.log', 'sha256': 'b' * 64}], 'cause': 'x'},
    ]

    def sequences(self):
        """Rekordy kroku `a` (do 3), potem krok `b` z `--final`; wariant `b` kończy przebieg lub ponawia."""
        for size in (1, 2, 3):
            for variants in itertools.product(self.VARIANTS, repeat=size):
                records = [rec('a', **variant) for variant in variants]
                yield [*records, rec('b', final=True)]
                yield [*records, rec('a', final=True)]

    def test_warning_iff_unexplained_at_final_and_on_the_last_write_of_the_step(self):
        checked = {'unexplained': 0, 'other': 0}
        for records in self.sequences():
            with self.subTest(records=[(r['step'], r['status'], r.get('cause'), r.get('cause_key')) for r in records]):
                kind = _classify_run('r', records)[0]['class']
                at_final = _write_warnings(records, records[-1]['step'], True)
                self.assertEqual(kind == 'unexplained', bool(at_final), kind)
                checked['unexplained' if kind == 'unexplained' else 'other'] += 1
                if kind == 'unexplained':
                    last = max(i for i, r in enumerate(records) if r['step'] == 'a')
                    upto = records[:last + 1]
                    if not _classify_run('r', upto)[1]['a']['explained'] and upto[-1]['status'] == 'PASS':
                        self.assertTrue(_write_warnings(upto, 'a', False))
        self.assertGreater(checked['unexplained'], 0)
        self.assertGreater(checked['other'], 0)


class MetricsClassify(ClassifyBase):
    def test_classes(self):
        self.run_file('clean', rec('a'), rec('b'))
        self.run_file('rec', rec('a', 'FAIL', 30, 'flaky'), rec('a', 'PASS', 30))
        self.run_file('unexp', rec('a', 'FAIL'), rec('a'))
        self.run_file('inc', rec('a'), rec('b', 'BLOCKED', cause='env'))
        data = self.classify()
        runs = self.by_id(data)
        self.assertEqual({k: v['class'] for k, v in runs.items()},
                         {'clean': 'clean', 'rec': 'recovery', 'unexp': 'unexplained', 'inc': 'incomplete'})
        self.assertEqual(runs['clean']['duration_seconds'], 20)
        self.assertEqual(runs['rec']['repeated_steps'], 1)
        self.assertEqual(runs['rec']['failed_records'], 1)
        self.assertEqual(runs['rec']['causes'], ['flaky'])
        self.assertEqual(runs['inc']['final_status'], 'BLOCKED')
        self.assertEqual(data['coverage']['runs'], 4)
        self.assertEqual(data['coverage']['with_causes'], 2)
        self.assertIn('nie ustala', data['notes'])

    def test_baseline_null_without_clean_runs(self):
        self.run_file('rec', rec('a', 'FAIL', 30, 'flaky'), rec('a', 'PASS', 30))
        data = self.classify()
        self.assertIsNone(data['baseline_seconds'])
        self.assertEqual(data['cause_ranking'], [{'cause': 'flaky', 'runs': 1, 'runs_with_excess': 0, 'excess_seconds': None}])

    def test_unknown_duration_is_null_and_not_baseline(self):
        self.run_file('clean', rec('a', duration=None), rec('b'))
        data = self.classify()
        self.assertIsNone(self.by_id(data)['clean']['duration_seconds'])
        self.assertIsNone(data['baseline_seconds'])
        self.assertEqual(data['coverage']['with_complete_durations'], 0)

    def test_baseline_median_and_deterministic_ranking(self):
        self.run_file('c1', rec('a', duration=10))
        self.run_file('c2', rec('a', duration=30))
        self.run_file('r1', rec('a', 'FAIL', 40, 'zeta'), rec('a', 'PASS', 40))      # 80, excess 60
        self.run_file('r2', rec('a', 'FAIL', 20, 'beta'), rec('a', 'PASS', 20))      # 40, excess 20
        self.run_file('r3', rec('a', 'FAIL', 20, 'alpha'), rec('a', 'PASS', 20))     # 40, excess 20
        self.run_file('r4', rec('a', 'FAIL', None, 'nodur'), rec('a', 'PASS', 5))
        data = self.classify()
        self.assertEqual(data['baseline_seconds'], 20)
        self.assertEqual(data['cause_ranking'], [
            {'cause': 'zeta', 'runs': 1, 'runs_with_excess': 1, 'excess_seconds': 60},
            {'cause': 'alpha', 'runs': 1, 'runs_with_excess': 1, 'excess_seconds': 20},
            {'cause': 'beta', 'runs': 1, 'runs_with_excess': 1, 'excess_seconds': 20},
            {'cause': 'nodur', 'runs': 1, 'runs_with_excess': 0, 'excess_seconds': None}])
        self.assertEqual(data, self.classify())

    def test_excess_split_between_causes(self):
        self.run_file('c1', rec('a', duration=10))
        self.run_file('r', rec('a', 'FAIL', 20, 'x'), rec('a', 'PASS', 20, 'y'))   # 40, excess 30
        ranking = self.classify()['cause_ranking']
        self.assertEqual([(e['cause'], e['excess_seconds']) for e in ranking], [('x', 15), ('y', 15)])

    def test_with_cost_requires_every_record(self):
        self.run_file('all', rec('a', cost=1), rec('b', cost=2))
        self.run_file('some', rec('a', cost=1), rec('b'))
        self.assertEqual(self.classify()['coverage']['with_cost'], 1)

    def test_median_odd_number_of_clean_runs(self):
        for ident, d in (('c1', 10), ('c2', 50), ('c3', 20)):
            self.run_file(ident, rec('a', duration=d))
        self.assertEqual(self.classify()['baseline_seconds'], 20)

    def test_empty_runs_directory(self):
        data = self.classify()
        self.assertEqual(data['runs'], [])
        self.assertIsNone(data['baseline_seconds'])
        self.assertEqual(data['cause_ranking'], [])
        self.assertEqual(data['coverage'], {'runs': 0, 'with_complete_durations': 0, 'with_cost': 0,
                                            'with_causes': 0, 'benchmark_runs_skipped': 0,
                                            'no_data': 0, 'symlinks_skipped': 0, 'invalid_dirs': 0})

    def test_run_without_records_is_no_data(self):
        self.run_file('empty')
        item = self.by_id(self.classify())['empty']
        self.assertEqual(item['class'], 'no_data')
        self.assertIsNone(item['final_status'])
        self.assertIsNone(item['duration_seconds'])

    def test_cause_on_passing_step_does_not_explain_failure(self):
        self.run_file('r', rec('a', 'FAIL'), rec('a'), rec('b', cause='happy'))
        data = self.classify()
        item = self.by_id(data)['r']
        self.assertEqual(item['class'], 'unexplained')
        self.assertEqual(item['causes'], [])
        self.assertEqual(data['coverage']['with_causes'], 0)

    def test_runs_with_excess_makes_incompleteness_explicit(self):
        self.run_file('c1', rec('a', duration=10))
        self.run_file('r1', rec('a', 'FAIL', 20, 'x'), rec('a', 'PASS', 20))
        self.run_file('r2', rec('a', 'FAIL', None, 'x'), rec('a', 'PASS', 5))
        entry = self.classify()['cause_ranking'][0]
        self.assertEqual((entry['runs'], entry['runs_with_excess'], entry['excess_seconds']), (2, 1, 30))

    def test_without_flag_result_unchanged(self):
        self.run_file('clean', rec('a'))
        plain = self.report('metrics')
        self.assertNotIn('classification', plain)
        with_flag = self.report('metrics', '--classify')
        with_flag.pop('classification')
        self.assertEqual(plain, with_flag)

    def test_benchmark_runs_skipped(self):
        self.run_file('clean', rec('a'))
        self.write('.skillsync/runs/benchmark-1.json', json.dumps(
            {'schema_version': 1, 'checkpoints': [], 'benchmark': {'cases': [{'status': 'PASS'}]}}))
        data = self.classify()
        self.assertEqual([i['id'] for i in data['runs']], ['clean'])
        self.assertEqual(data['coverage']['benchmark_runs_skipped'], 1)

    @needs_os_symlinks
    def test_symlinked_run_is_refused(self):
        self.run_file('clean', rec('a'))
        (self.root / '.skillsync/runs/link.json').symlink_to(self.root / '.skillsync/runs/clean.json')
        self.rejected('metrics', '--classify')

class ClassificationRules(ClassifyBase):
    """Reguły klas z planu R2: N1 (final), N2 (krok), F4a-F4e."""

    def test_f4a_not_run_in_the_middle_and_at_the_end(self):
        middle = self.only(rec('a'), rec('b', 'NOT_RUN'), rec('b'), ident='mid')
        self.assertEqual(middle['class'], 'unexplained')
        skipped = self.only(rec('a'), rec('b', 'NOT_RUN'), rec('c'), ident='skip')
        self.assertEqual(skipped['class'], 'incomplete')
        end = self.only(rec('a'), rec('b', 'NOT_RUN'), ident='end')
        self.assertEqual((end['class'], end['final_status'], end['not_run_records']), ('incomplete', 'NOT_RUN', 1))
        self.assertEqual((middle['not_run_records'], middle['failed_records']), (1, 0))

    def test_f4a_explained_not_run_that_was_later_run_is_recovery(self):
        item = self.only(rec('a', 'NOT_RUN', cause='no env'), rec('a'))
        self.assertEqual((item['class'], item['causes']), ('recovery', ['no env']))

    def test_f4b_attempt_above_one_is_a_problem(self):
        self.assertEqual(self.only(rec('a', attempt=2), ident='bare')['class'], 'unexplained')
        item = self.only(rec('a', attempt=2, cause='retry'), ident='why')
        self.assertEqual((item['class'], item['causes'], item['repeated_steps']), ('recovery', ['retry'], 0))

    def test_f4c_failed_step_without_retry_is_incomplete(self):
        self.assertEqual(self.only(rec('a', 'FAIL'), rec('b'), ident='bare')['class'], 'incomplete')
        item = self.only(rec('a', 'FAIL', cause='x'), rec('b'), ident='why')
        self.assertEqual((item['class'], item['final_status']), ('incomplete', 'PASS'))
        self.assertEqual(self.only(rec('a', 'BLOCKED', cause='x'), rec('b'), ident='blk')['class'], 'incomplete')

    def test_f4d_one_cause_does_not_explain_two_problem_steps(self):
        item = self.only(rec('a', 'FAIL', cause='x'), rec('a'), rec('b', 'FAIL'), rec('b'), ident='one')
        self.assertEqual(item['class'], 'unexplained')
        item = self.only(rec('a', 'FAIL', cause='x'), rec('a'), rec('b', attempt=2), ident='attempt')
        self.assertEqual(item['class'], 'unexplained')
        item = self.only(rec('a', 'FAIL', cause='x'), rec('a'), rec('b', 'FAIL', cause='y'), rec('b'), ident='both')
        self.assertEqual((item['class'], item['causes']), ('recovery', ['x', 'y']))

    def test_f4e_run_without_final_record_is_incomplete(self):
        item = self.only(rec('a'), rec('b'), close=False)
        self.assertEqual((item['class'], item['final_status']), ('incomplete', 'PASS'))
        self.assertEqual(self.only(rec('a'), close=False, ident='one')['class'], 'incomplete')

    def test_f4e_final_only_counts_on_the_last_record(self):
        reopened = self.only(rec('a', final=True), rec('b'), close=False, ident='reopened')
        self.assertEqual(reopened['class'], 'incomplete')
        closed = self.only(rec('a', final=True), rec('b', final=True), close=False, ident='closed')
        self.assertEqual(closed['class'], 'clean')

    def test_f4e_attempt_above_one_on_final_record_still_a_problem(self):
        self.assertEqual(self.only(rec('a', attempt=2, final=True), close=False, ident='bare')['class'], 'unexplained')
        item = self.only(rec('a', attempt=2, cause='retry', final=True), close=False, ident='why')
        self.assertEqual(item['class'], 'recovery')

    def test_final_requires_last_record_also_for_recovery_and_unexplained(self):
        self.assertEqual(self.only(rec('a', 'FAIL', cause='x'), rec('a'), close=False, ident='rec')['class'], 'incomplete')
        self.assertEqual(self.only(rec('a', 'FAIL'), rec('a'), close=False, ident='unexp')['class'], 'incomplete')

    def test_whitespace_only_cause_explains_nothing(self):
        item = self.only(rec('a', 'FAIL', cause='   '), rec('a'))
        self.assertEqual((item['class'], item['causes']), ('unexplained', []))

    def test_cause_key_without_cause_explains_and_is_the_label(self):
        self.run_file('c1', rec('a', duration=10))
        self.run_file('r', rec('a', 'FAIL', 30, cause_key='env-gap'), rec('a', 'PASS', 30))
        data = self.classify()
        item = self.by_id(data)['r']
        self.assertEqual((item['class'], item['causes']), ('recovery', ['env-gap']))
        self.assertEqual(data['baseline_seconds'], 10)
        self.assertEqual(data['cause_ranking'], [{'cause': 'env-gap', 'runs': 1, 'runs_with_excess': 1,
                                                  'excess_seconds': 50}])
        self.assertEqual(data['coverage']['with_causes'], 1)

    def test_text_cause_wins_over_cause_key_as_label(self):
        item = self.only(rec('a', 'FAIL', cause='  text  ', cause_key='k'), rec('a'))
        self.assertEqual(item['causes'], ['text'])

    def test_cause_key_on_plain_pass_stays_clean(self):
        item = self.only(rec('a', cause_key='noted', owner='test'))
        self.assertEqual((item['class'], item['causes']), ('clean', []))

    def test_step_level_cause_is_enough(self):
        item = self.only(rec('a', 'FAIL', cause='x'), rec('a', 'FAIL'), rec('a'))
        self.assertEqual((item['class'], item['causes'], item['failed_records']), ('recovery', ['x'], 2))

    def test_counters_failed_excludes_not_run_and_final_status_is_last_record(self):
        item = self.only(rec('a', 'BLOCKED', cause='e'), rec('a', 'NOT_RUN', cause='e'), rec('a'))
        self.assertEqual((item['failed_records'], item['not_run_records']), (1, 1))
        item = self.only(rec('a', 'NOT_RUN', cause='e'), rec('a'), ident='nr')
        self.assertEqual((item['failed_records'], item['not_run_records']), (0, 1))
        self.assertEqual(item['final_status'], 'PASS')

    def test_all_fields_of_an_ordinary_run(self):
        item = self.only(rec('a'), rec('b'))
        self.assertEqual(item, {'id': 'r', 'class': 'clean', 'steps': 2, 'repeated_steps': 0, 'failed_records': 0,
                                'not_run_records': 0, 'final_status': 'PASS', 'duration_seconds': 20, 'causes': []})


class DirectoryRules(ClassifyBase):
    """Katalogi w .skillsync/runs, przypisanie plików, id z nazwy pliku (N5)."""

    NO_DATA = {'class': 'no_data', 'steps': 0, 'repeated_steps': 0, 'failed_records': 0, 'not_run_records': 0,
               'final_status': None, 'duration_seconds': None, 'causes': []}

    def test_directory_without_file_is_no_data(self):
        self.runs_dir('lonely')
        data = self.classify()
        self.assertEqual(data['runs'], [{'id': 'lonely', **self.NO_DATA}])
        self.assertEqual(data['coverage']['no_data'], 1)

    def test_empty_run_file_has_all_no_data_fields(self):
        self.run_file('empty')
        self.assertEqual(self.by_id(self.classify())['empty'], {'id': 'empty', **self.NO_DATA})

    def test_directory_next_to_its_json_is_not_no_data(self):
        self.runs_dir('x')
        self.run_file('x', rec('a'))
        data = self.classify()
        self.assertEqual([(i['id'], i['class']) for i in data['runs']], [('x', 'clean')])
        self.assertEqual(data['coverage']['no_data'], 0)

    def test_directory_with_dotted_files_is_not_no_data(self):
        self.runs_dir('r')
        self.run_file('r.a', rec('a'))
        self.run_file('r.b', rec('a'))
        data = self.classify()
        self.assertEqual(sorted(i['id'] for i in data['runs']), ['r.a', 'r.b'])
        self.assertEqual(data['coverage']['no_data'], 0)

    def test_longest_directory_name_owns_the_file(self):
        self.runs_dir('v1')
        self.runs_dir('v1.2-x')
        self.run_file('v1.2-x.P1', rec('a'))
        data = self.classify()
        classes = {i['id']: i['class'] for i in data['runs']}
        self.assertEqual(classes, {'v1': 'no_data', 'v1.2-x.P1': 'clean'})
        self.assertEqual(data['coverage']['no_data'], 1)

    def test_lock_directory_is_skipped_by_directory_rule(self):
        self.runs_dir('x.lock')
        self.run_file('real', rec('a'))
        data = self.classify()
        self.assertEqual([i['id'] for i in data['runs']], ['real'])
        self.assertEqual((data['coverage']['no_data'], data['coverage']['invalid_dirs']), (0, 0))

    def test_json_named_directory_is_not_a_run_directory(self):
        self.runs_dir('x.json')
        result = self.rejected('metrics', '--classify')
        self.assertTrue(result.stderr.startswith('skillsync:'), result.stderr)
        self.assertNotIn('no_data', result.stdout)

    @needs_os_symlinks
    def test_symlinked_directory_is_counted_not_read(self):
        target = self.base / 'elsewhere'
        target.mkdir()
        (target / 'inside.json').write_text('{}')
        self.runs_dir()
        (self.root / '.skillsync/runs/lnk').symlink_to(target, target_is_directory=True)
        self.run_file('real', rec('a'))
        data = self.classify()
        self.assertEqual([i['id'] for i in data['runs']], ['real'])
        self.assertEqual(data['coverage']['symlinks_skipped'], 1)
        self.assertEqual(data['coverage']['no_data'], 0)

    def test_directory_with_name_outside_slug_is_only_counted(self):
        self.runs_dir('bad name!')
        self.run_file('real', rec('a'))
        result = self.cli('metrics', '--classify')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)['classification']
        self.assertEqual([i['id'] for i in data['runs']], ['real'])
        self.assertEqual((data['coverage']['invalid_dirs'], data['coverage']['no_data']), (1, 0))
        self.assertNotIn('bad name', result.stdout + result.stderr)

    def test_directories_do_not_change_plain_metrics(self):
        self.run_file('real', rec('a'), rec('b'))
        before = self.report('metrics')
        self.runs_dir('lonely')
        self.runs_dir('real')
        self.runs_dir('x.lock')
        plain = self.report('metrics')
        self.assertEqual(plain, before)
        self.assertEqual((plain['runs'], plain['checkpoints']), (1, 2))
        with_flag = self.report('metrics', '--classify')
        with_flag.pop('classification')
        self.assertEqual(with_flag, before)

    def test_id_comes_from_file_name_not_from_run_id(self):
        base = {'schema_version': 1, 'checkpoints': [rec('a', final=True)]}
        self.write('.skillsync/runs/missing.json', json.dumps(base))
        self.write('.skillsync/runs/number.json', json.dumps({**base, 'run_id': 123}))
        self.write('.skillsync/runs/first.json', json.dumps({**base, 'run_id': 'same'}))
        self.write('.skillsync/runs/second.json', json.dumps({**base, 'run_id': 'same'}))
        data = self.classify()
        self.assertEqual(sorted(i['id'] for i in data['runs']), ['first', 'missing', 'number', 'second'])
        self.assertEqual(data['coverage']['runs'], 4)

    def test_file_name_outside_slug_gets_opaque_id(self):
        name = 'secret marker (1)'
        self.write(f'.skillsync/runs/{name}.json', json.dumps(
            {'schema_version': 1, 'run_id': 'leaky-run-id', 'checkpoints': [rec('a', final=True)]}))
        result = self.cli('metrics', '--classify')
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout + result.stderr
        ids = [i['id'] for i in json.loads(result.stdout)['classification']['runs']]
        self.assertEqual(len(ids), 1)
        self.assertRegex(ids[0], r'^invalid-name:[0-9a-f]{12}$')
        for fragment in ('secret', 'marker', 'leaky-run-id'):
            self.assertNotIn(fragment, output)


class UnreadableRuns(ClassifyBase):
    @needs_fifo
    def test_fifo_run_is_refused_without_hanging(self):
        self.runs_dir()
        os.mkfifo(self.root / '.skillsync/runs/x.json')
        try:
            result = subprocess.run([sys.executable, str(CLI), 'metrics', str(self.root), '--classify', '--json'],
                                    capture_output=True, text=True, timeout=8, cwd=self.root)
        except subprocess.TimeoutExpired:
            self.fail('metrics zawisł na FIFO x.json (brak sprawdzenia S_ISREG przed otwarciem)')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(result.stderr.startswith('skillsync:'), result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_json_nested_to_recursion_error_is_refused(self):
        depth = 200000
        self.write('.skillsync/runs/x.json', '[' * depth + ']' * depth)
        result = self.rejected('metrics', '--classify')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(result.stderr.startswith('skillsync:'), result.stderr)


class NewFieldValidation(ClassifyBase):
    """Pola final, cause_key, owner, denied, inherited: zapis przez CLI i odczyt z pliku."""

    def setUp(self):
        super().setUp()
        self.write('tracked.txt', 'baseline')
        self.init_git()
        self.write('.skillsync/evidence/result.log', 'Ran 1 test\nOK\n')

    def store(self):
        return self.root / '.skillsync/runs/task.json'

    def checkpoint(self, status='PASS', *extra):
        args = ['--run-id', 'task', '--step', 'verify', '--status', status, '--next', 'n', *extra]
        if status == 'PASS':
            args += ['--evidence', '.skillsync/evidence/result.log']
        return self.cli('checkpoint', *args)

    def saved(self, status='PASS', *extra):
        result = self.checkpoint(status, *extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(self.store().read_text())['checkpoints'][-1]

    def refused(self, status, *extra, choice_ok=False):
        result = self.checkpoint(status, *extra)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertTrue(result.stderr.startswith('skillsync:') or (choice_ok and 'invalid choice' in result.stderr),
                        'odmowa ma pochodzić z walidacji, nie z nieznanej flagi: ' + result.stderr)
        self.assertFalse(self.store().exists(), 'odmowa nie może zostawić zapisu')

    def test_plain_checkpoint_has_none_of_the_new_fields(self):
        record = self.saved('PASS')
        for key in ('final', 'denied', 'inherited', 'cause_key', 'owner'):
            self.assertNotIn(key, record)

    def test_valid_fields_are_saved_only_when_given(self):
        record = self.saved('PASS', '--final')
        self.assertIs(record['final'], True)
        self.store().unlink()
        record = self.saved('FAIL', '--cause-key', 'evidence-gap', '--owner', 'test', '--denied', '0', '--inherited', '3')
        self.assertEqual((record['cause_key'], record['owner'], record['denied'], record['inherited']),
                         ('evidence-gap', 'test', 0, 3))
        self.assertNotIn('final', record)

    def test_long_key_made_of_short_segments_is_accepted(self):
        key = '-'.join(['abcdefghij'] * 4) + '-abcde'
        key = key[:-1]
        self.assertEqual(len(key), 48)
        self.assertEqual(self.saved('FAIL', '--cause-key', key)['cause_key'], key)

    def test_final_is_refused_on_non_pass(self):
        for status in ('FAIL', 'BLOCKED', 'NOT_RUN'):
            with self.subTest(status=status):
                self.refused(status, '--final')

    def test_owner_needs_cause_key(self):
        self.refused('FAIL', '--owner', 'test')

    def test_owner_must_be_known(self):
        self.refused('FAIL', '--cause-key', 'k', '--owner', 'wizard', choice_ok=True)
        for owner in ('instruction', 'skill', 'hook', 'route', 'test'):
            with self.subTest(owner=owner):
                self.store().unlink(missing_ok=True)
                self.assertEqual(self.saved('FAIL', '--cause-key', 'k', '--owner', owner)['owner'], owner)

    def test_token_like_or_malformed_cause_key_is_refused(self):
        for key in ('a' * 32, 'x-' + 'b' * 40, 'ghp' + '0' * 40, 'Upper', '-lead', 'has space', 'a' * 49,
                    '-'.join(['abcdefghij'] * 5)):
            with self.subTest(key=key):
                self.refused('FAIL', '--cause-key=' + key)

    def test_negative_counters_are_refused(self):
        self.refused('FAIL', '--denied', '-1')
        self.refused('FAIL', '--inherited', '-1')

    def write_record(self, **fields):
        record = rec('a')
        record.update(fields)
        self.write('.skillsync/runs/bad.json', json.dumps(
            {'schema_version': 1, 'run_id': 'bad', 'checkpoints': [record]}))

    def test_invalid_values_in_file_are_refused_on_read(self):
        cases = {'final on FAIL': {'status': 'FAIL', 'evidence': [], 'final': True},
                 'final text': {'final': 'yes'}, 'final number': {'final': 1},
                 'owner without key': {'owner': 'test'}, 'owner unknown': {'cause_key': 'k', 'owner': 'wizard'},
                 'owner not text': {'cause_key': 'k', 'owner': 5},
                 'token key': {'cause_key': 'a' * 32}, 'key not text': {'cause_key': 123},
                 'key uppercase': {'cause_key': 'Upper'},
                 'denied negative': {'denied': -1}, 'denied bool': {'denied': True},
                 'denied float': {'denied': 1.5}, 'denied text': {'denied': '2'},
                 'inherited negative': {'inherited': -1}, 'inherited bool': {'inherited': False}}
        for name, fields in cases.items():
            with self.subTest(name):
                self.write_record(**fields)
                result = self.rejected('metrics')
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertTrue(result.stderr.startswith('skillsync:'), result.stderr)

    def test_valid_new_fields_in_file_are_read(self):
        self.write_record(final=True, denied=0, inherited=2, cause_key='ok-key', owner='hook')
        self.assertEqual(self.report('metrics')['runs'], 1)

class ReadRobustnessAndReuse(ClassifyBase):
    """N7 i odporność odczytu: benchmark z rekordami, ponowne użycie dowodu, liczby spoza zakresu float."""

    def test_benchmark_file_with_records_is_refused_by_metrics(self):
        self.run_file('good', rec('a'))
        self.write('.skillsync/runs/x.json', json.dumps({
            'schema_version': 1, 'checkpoints': [rec('s', 'FAIL', cause='c', cause_key='k')],
            'benchmark': {'cases': []}}))
        for args in (('metrics',), ('metrics', '--classify')):
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertTrue(result.stderr.startswith('skillsync:'), result.stderr)
                self.assertNotIn('Traceback', result.stderr)

    def test_four_hundred_digit_duration_is_refused(self):
        text = json.dumps({'schema_version': 1, 'checkpoints': [rec('a', duration=10)]}).replace(
            '"duration_seconds": 10', '"duration_seconds": 1' + '0' * 399)
        self.write('.skillsync/runs/big.json', text)
        result = self.rejected('metrics', '--classify')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(result.stderr.startswith('skillsync:'), result.stderr)

    def test_huge_duration_sum_does_not_break_classification(self):
        self.run_file('huge', rec('a', duration=10 ** 308), rec('b', duration=10 ** 308), rec('c', duration=1.5))
        for args in (('metrics',), ('metrics', '--classify')):
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertTrue(result.stderr.startswith('skillsync:'), result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                self.assertEqual(result.stdout.strip(), '', 'bez niepoprawnego JSON (Infinity)')

    def test_huge_benchmark_durations_do_not_break_metrics(self):
        cases = [{'status': 'PASS', 'duration_seconds': value} for value in (10 ** 308, 10 ** 308, 1.5)]
        self.write('.skillsync/runs/benchmark-1.json', json.dumps(
            {'schema_version': 1, 'checkpoints': [], 'benchmark': {'cases': cases}}))
        result = self.cli('metrics')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(result.stderr.startswith('skillsync:'), result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_runs_path_that_is_a_regular_file_is_refused(self):
        self.write('.skillsync/runs', 'not a directory')
        result = self.rejected('metrics', '--classify')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(result.stderr.startswith('skillsync:'), result.stderr)
        self.assertEqual(result.stdout.strip(), '')

    def reuse(self, *records):
        return self.only(*records)['class']

    def test_later_non_final_pass_evidence_is_not_a_problem_evidence(self):
        x, y = {'path': 'e.log', 'sha256': DIGEST}, {'path': 'g.log', 'sha256': 'b' * 64}
        self.assertEqual(self.reuse(rec('a', 'FAIL', cause='c', evidence=[x]), rec('a', evidence=[y]),
                                    rec('a', evidence=[y])), 'recovery')

    def test_passing_retry_with_its_own_evidence_is_recovery(self):
        y = {'path': 'g.log', 'sha256': 'b' * 64}
        self.assertEqual(self.reuse(rec('a', attempt=2, cause='c', evidence=[y]), rec('a', evidence=[y])), 'recovery')

    def test_failure_evidence_reused_after_a_pass_is_unexplained(self):
        x = {'path': 'e.log', 'sha256': DIGEST}
        self.assertEqual(self.reuse(rec('a', 'FAIL', cause='c', evidence=[x]), rec('a', evidence=[x]),
                                    rec('a', evidence=[x])), 'unexplained')

    def test_new_final_evidence_after_a_pass_with_failure_evidence_is_recovery(self):
        x, y = {'path': 'e.log', 'sha256': DIGEST}, {'path': 'g.log', 'sha256': 'b' * 64}
        self.assertEqual(self.reuse(rec('a', 'FAIL', cause='c', evidence=[x]), rec('a', evidence=[x]),
                                    rec('a', evidence=[y])), 'recovery')

    def test_same_evidence_on_the_final_pass_is_unexplained(self):
        same = [{'path': 'e.log', 'sha256': DIGEST}]
        item = self.only(rec('a', 'FAIL', cause='x', evidence=same), rec('a'))
        self.assertEqual(item['class'], 'unexplained')

    def test_different_evidence_on_the_final_pass_is_recovery(self):
        other = [{'path': 'g.log', 'sha256': 'b' * 64}]
        item = self.only(rec('a', 'FAIL', cause='x', evidence=[{'path': 'e.log', 'sha256': DIGEST}]),
                         rec('a', evidence=other))
        self.assertEqual(item['class'], 'recovery')

    def test_superset_of_evidence_is_recovery(self):
        both = [{'path': 'e.log', 'sha256': DIGEST}, {'path': 'g.log', 'sha256': 'b' * 64}]
        item = self.only(rec('a', 'FAIL', cause='x', evidence=[{'path': 'e.log', 'sha256': DIGEST}]),
                         rec('a', evidence=both))
        self.assertEqual(item['class'], 'recovery')


class CheckpointRefusesBenchmark(ClassifyBase):
    def setUp(self):
        super().setUp()
        self.write('tracked.txt', 'baseline')
        self.init_git()
        self.write('.skillsync/evidence/result.log', 'Ran 1 test\nOK\n')

    def test_checkpoint_does_not_append_to_a_benchmark_file(self):
        path = self.root / '.skillsync/runs/benchmark-1.json'
        self.write('.skillsync/runs/benchmark-1.json', json.dumps({
            'schema_version': 1, 'checkpoints': [], 'benchmark': {'cases': [{'status': 'PASS'}]}}))
        before = path.read_bytes()
        result = self.cli('checkpoint', '--run-id', 'benchmark-1', '--step', 'verify', '--status', 'PASS',
                          '--next', 'n', '--evidence', '.skillsync/evidence/result.log')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(result.stderr.startswith('skillsync:'), result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
