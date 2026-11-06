"""Checkpoint trust boundaries and reproducible offline benchmark contracts."""
import json
from pathlib import Path
import sys
import unittest
import yaml

from test_skillsync_workflow import WorkflowFixture
from layers_helpers import needs_os_symlinks, needs_posix  # noqa: E402


class CheckpointContracts(WorkflowFixture):
    def setUp(self):
        super().setUp()
        self.write('tracked.txt', 'baseline')
        self.init_git()
        self.write('.skillsync/evidence/result.log', 'Ran 1 test\nOK\n')

    def checkpoint(self, *extra, status='PASS'):
        return self.report('checkpoint', '--run-id', 'task', '--step', 'verify', '--status', status,
                           '--next', 'Independent review', '--evidence', '.skillsync/evidence/result.log', *extra)

    def test_retro_quality_sibling_dated_at_the_edge_of_the_range_does_not_count_and_does_not_crash(self):
        for label, value in (('min', '0001-01-01T00:00:00+01:00'), ('max', '9999-12-31T23:59:59-01:00')):
            with self.subTest(label):
                self.report('checkpoint', '--run-id', 'edge.2', '--step', 'retro-quality', '--status', 'PASS',
                            '--next', 'n', '--evidence', '.skillsync/evidence/result.log')
                path = self.root / '.skillsync/runs/edge.2.json'
                data = json.loads(path.read_text())
                data['checkpoints'][0]['recorded_at'] = value
                path.write_text(json.dumps(data))
                result = self.report('checkpoint', '--run-id', 'edge', '--step', 'verify', '--status', 'PASS',
                                     '--next', 'n', '--evidence', '.skillsync/evidence/result.log', '--final')
                self.assertTrue(any(w.startswith('retro-quality-missing:') for w in result['warnings']), result)
                path.unlink()
                (self.root / '.skillsync/runs/edge.json').unlink()

    def test_resume_trusts_matching_state_and_does_not_execute_next(self):
        self.checkpoint()
        report = self.report('resume', '--run-id', 'task')
        self.assertEqual(report['status'], 'PASS')
        self.assertFalse(report['stale'])
        self.assertEqual(report['next'], 'Independent review')

    def test_staged_unstaged_and_untracked_bytes_stale_checkpoint(self):
        for mode in ['staged', 'unstaged', 'untracked']:
            with self.subTest(mode=mode):
                self.checkpoint()
                if mode == 'untracked':
                    changed = self.write('untracked.txt', 'new content')
                else:
                    changed = self.write('tracked.txt', mode)
                    if mode == 'staged':
                        self.git('add', 'tracked.txt')
                report = self.report('resume', '--run-id', 'task', code=2)
                self.assertEqual(report['status'], 'BLOCKED')
                self.assertTrue(report['stale'])
                if mode == 'untracked':
                    changed.unlink()
                else:
                    self.git('restore', '--staged', '--worktree', 'tracked.txt')

    def test_modified_evidence_stales_even_when_git_state_unchanged(self):
        self.checkpoint()
        self.write('.skillsync/evidence/result.log', 'tampered evidence')
        report = self.report('resume', '--run-id', 'task', code=2)
        self.assertTrue(report['stale'])
        self.assertTrue(any('Dowód' in reason or 'evidence' in reason.lower() for reason in report['reasons']))

    def test_internal_artifacts_and_ignored_files_do_not_stale(self):
        self.write('.gitignore', 'ignored.txt\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'ignore rule')
        self.checkpoint()
        self.write('.skillsync/cache/internal.json', '{}')
        self.write('ignored.txt', 'ignored bytes')
        report = self.report('resume', '--run-id', 'task')
        self.assertFalse(report['stale'])

    def record(self, step, status, evidence, *extra):
        self.write(f'.skillsync/evidence/{evidence}.log', evidence)
        return self.report('checkpoint', '--run-id', 'task', '--step', step, '--status', status, '--next', 'n',
                           '--evidence', f'.skillsync/evidence/{evidence}.log', *extra)

    def run_class(self):
        runs = self.report('metrics', '--classify')['classification']['runs']
        return next(item['class'] for item in runs if item['id'] == 'task')

    def test_retry_without_cause_warns_at_write_time_and_stays_exit_zero(self):
        self.record('verify', 'PASS', 'a')
        report = self.record('verify', 'PASS', 'b', '--attempt', '2')
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(len(report['warnings']), 1)
        self.assertIn('verify', report['warnings'][0])
        self.assertIn('--cause', report['warnings'][0])
        stored = json.loads((self.root / '.skillsync/runs/task.json').read_text(encoding='utf-8'))
        self.assertEqual(len(stored['checkpoints']), 2)

    def test_warned_step_is_explained_by_a_later_record_with_a_cause(self):
        # Rekord retro-quality PASS przed `final` (K9) trzyma poza zestawem ostrzeżeń `retro-quality-missing:`,
        # więc test dalej sprawdza wyłącznie ostrzeżenia o niewyjaśnionych krokach.
        self.record('retro-quality', 'PASS', 'rq')
        self.record('verify', 'FAIL', 'a')
        first = self.record('verify', 'PASS', 'b', '--final')
        self.assertEqual(len(first['warnings']), 1)
        self.assertIn("'verify'", first['warnings'][0])
        self.assertEqual(self.run_class(), 'unexplained')
        report = self.record('verify', 'PASS', 'c', '--cause', 'zły próg', '--final')
        self.assertNotIn('warnings', report)
        self.assertEqual(self.run_class(), 'recovery')

    def test_no_warning_for_plain_pass_open_failure_or_step_level_cause(self):
        self.assertNotIn('warnings', self.record('plain', 'PASS', 'a'))
        self.assertNotIn('warnings', self.record('open', 'FAIL', 'b'))
        self.assertNotIn('warnings', self.record('open', 'FAIL', 'c'))
        self.assertNotIn('warnings', self.record('known', 'FAIL', 'd', '--cause', 'x'))
        self.assertNotIn('warnings', self.record('known', 'PASS', 'e'))
        self.assertNotIn('warnings', self.record('keyed', 'FAIL', 'f', '--cause-key', 'test-gap'))
        self.assertNotIn('warnings', self.record('keyed', 'PASS', 'g'))

    def test_pass_reusing_failure_evidence_warns_even_with_a_cause(self):
        self.record('verify', 'FAIL', 'a', '--cause', 'x')
        report = self.record('verify', 'PASS', 'a')
        self.assertEqual(len(report['warnings']), 1)
        self.assertIn('dowód', report['warnings'][0])
        self.assertEqual(self.record('verify', 'PASS', 'b').get('warnings'), None)

    def test_final_record_lists_every_unexplained_step(self):
        self.record('retro-quality', 'PASS', 'rq')  # K9: retrospektywa przed `final`, zestaw ostrzeżeń zostaje zamknięty
        self.record('one', 'FAIL', 'a')
        self.record('one', 'PASS', 'b')
        self.record('two', 'PASS', 'c', '--attempt', '2')
        self.record('fine', 'PASS', 'd')
        self.assertNotIn('warnings', self.record('later', 'PASS', 'e'))
        report = self.record('final', 'PASS', 'f', '--final')
        self.assertEqual(len(report['warnings']), 2)
        self.assertTrue(any("'one'" in item for item in report['warnings']))
        self.assertTrue(any("'two'" in item for item in report['warnings']))
        self.assertFalse(any(item.startswith('retro-quality-') for item in report['warnings']), report['warnings'])

    def test_pass_requires_evidence_and_ids_are_path_safe(self):
        self.rejected('checkpoint', '--run-id', 'task', '--step', 'verify', '--status', 'PASS', '--next', 'review')
        for ident in ['../escape', '/tmp/escape', 'a/b', 'a' * 81]:
            with self.subTest(ident=ident):
                self.rejected('checkpoint', '--run-id', ident, '--step', 'verify', '--status', 'NOT_RUN', '--next', 'test')
        self.assertFalse((self.base / 'escape.json').exists())

    @needs_os_symlinks
    def test_evidence_traversal_symlink_and_storage_symlink_are_refused(self):
        outside = self.base / 'outside.log'
        outside.write_text('external evidence')
        (self.root / 'escape.log').symlink_to(outside)
        for evidence in ['../outside.log', 'escape.log', str(outside)]:
            with self.subTest(evidence=evidence):
                self.rejected('checkpoint', '--run-id', 'task', '--step', 'verify', '--status', 'PASS',
                              '--next', 'review', '--evidence', evidence)
        runs = self.root / '.skillsync/runs'
        target = self.base / 'external-storage'
        target.mkdir()
        if runs.exists():
            self.assertEqual(list(runs.iterdir()), [])
            runs.rmdir()
        runs.symlink_to(target, target_is_directory=True)
        self.rejected('checkpoint', '--run-id', 'task', '--step', 'verify', '--status', 'NOT_RUN', '--next', 'test')
        self.assertEqual(list(target.iterdir()), [])

    def test_invalid_measurements_and_metadata_limits_are_rejected(self):
        for option, values in [('--duration', ['-1', 'nan', 'inf']), ('--cost', ['-1', 'nan', 'inf']),
                               ('--attempt', ['0', '-1']), ('--next', ['x' * 2001])]:
            for value in values:
                with self.subTest(option=option, value=value):
                    self.rejected('checkpoint', '--run-id', 'invalid', '--step', 'verify', '--status', 'NOT_RUN',
                                  '--next', 'test', option, value)
        self.assertFalse((self.root / '.skillsync/runs/invalid.json').exists())

    def test_unfinished_checkpoint_resume_is_blocked(self):
        for status in ['NOT_RUN', 'BLOCKED', 'FAIL']:
            with self.subTest(status=status):
                self.checkpoint(status=status)
                result = self.cli('resume', '--run-id', 'task')
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(json.loads(result.stdout)['status'], 'BLOCKED')
                self.assertNotIn('Traceback', result.stderr)

    def test_malformed_metadata_never_tracebacks_or_counts_invalid_measurements(self):
        for data in [[], 17, {'schema_version': 1, 'checkpoints': [17]},
                     {'schema_version': 1, 'checkpoints': [{'step': 'x', 'cost': -3, 'duration_seconds': float('nan')}]}]:
            with self.subTest(data=data):
                self.write('.skillsync/runs/tampered.json', json.dumps(data))
                self.rejected('metrics')

    def test_metrics_rejects_corrupted_measurements_in_otherwise_valid_records(self):
        self.checkpoint('--duration', '1', '--cost', '0.2')
        path = self.root / '.skillsync/runs/task.json'
        original = json.loads(path.read_text())
        for key, value in [('cost', -1), ('cost', float('nan')), ('duration_seconds', float('inf')),
                           ('attempt', True), ('attempt', 0)]:
            with self.subTest(key=key, value=value):
                data = json.loads(json.dumps(original))
                data['checkpoints'][0][key] = value
                path.write_text(json.dumps(data))
                self.rejected('metrics')

    def test_resume_rejects_tampered_evidence_paths(self):
        self.checkpoint()
        path = self.root / '.skillsync/runs/task.json'
        data = json.loads(path.read_text())
        data['checkpoints'][0]['evidence'][0]['path'] = '../external.log'
        path.write_text(json.dumps(data))
        self.rejected('resume', '--run-id', 'task')

    def test_metadata_size_and_record_count_are_bounded(self):
        self.write('.skillsync/runs/large.json', ' ' * (2 * 1024 * 1024 + 1))
        self.rejected('metrics')
        (self.root / '.skillsync/runs/large.json').unlink()
        self.checkpoint()
        path = self.root / '.skillsync/runs/task.json'
        data = json.loads(path.read_text())
        data['checkpoints'] = data['checkpoints'] * 1001
        path.write_text(json.dumps(data))
        self.rejected('metrics')

    @needs_os_symlinks
    def test_ignored_auto_companion_reference_change_invalidates_checkpoint(self):
        self.manifest['skills'] = ['feature']
        self.save()
        self.write('.gitignore', '.agents/\n')
        for name in ['feature', 'smart-test']:
            source = self.library / 'skills' / name
            (source / 'references').mkdir(parents=True)
            (source / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: Workflow\n---\nSkill')
            (source / 'references/rules.md').write_text('original rule')
            installed = self.root / '.agents/skills' / name
            installed.parent.mkdir(parents=True, exist_ok=True)
            installed.symlink_to(source, target_is_directory=True)
        self.git('add', '.')
        self.git('commit', '-qm', 'feature configuration')
        self.checkpoint()
        (self.library / 'skills/smart-test/references/rules.md').write_text('updated contract')
        report = self.report('resume', '--run-id', 'task', code=2)
        self.assertTrue(report['stale'])
        self.assertEqual(report['status'], 'BLOCKED')

    def test_metrics_keeps_currency_totals_separate_and_default_is_usd(self):
        self.checkpoint('--cost', '2')
        self.checkpoint('--cost', '3', '--cost-unit', 'PLN')
        report = self.report('metrics')
        self.assertEqual(report['cost_totals_by_unit'], {'USD': 2, 'PLN': 3})
        self.assertEqual(report['cost_known_total'], 2)
        self.assertEqual(report['cost_known_total_unit'], 'USD')
        self.assertNotEqual(report['cost_known_total'], 5)

    def test_metrics_unknown_values_are_separate_from_known_totals(self):
        self.checkpoint('--duration', '1.25', '--cost', '0.4')
        self.checkpoint(status='NOT_RUN')
        self.checkpoint('--duration', '2.75', '--cost', '0.6', '--attempt', '2', '--cause', 'regression')
        report = self.report('metrics')
        self.assertEqual(report['checkpoints'], 3)
        self.assertAlmostEqual(report['cost_known_total'], 1.0)
        self.assertEqual(report['cost_unknown_count'], 1)
        self.assertEqual(report['duration_known_seconds'], 4.0)
        self.assertEqual(report['duration_unknown_count'], 1)
        self.assertEqual(report['repeated_step_records'], 2)


class RetroQualityWarnings(WorkflowFixture):
    """K9: `checkpoint --final` ostrzega, gdy tożsamość przebiegu (`<id>.json` i `<id>.N.json`) nie ma rekordu
    `retro-quality` PASS wcześniejszego niż `final`. Kod wyjścia i zapis rekordu się nie zmieniają."""
    MISSING = 'retro-quality-missing:'
    UNVERIFIED = 'retro-quality-unverified:'

    def setUp(self):
        super().setUp()
        self.write('tracked.txt', 'baseline')
        self.init_git()

    def rec(self, run, step, status='PASS', *extra, code=0):
        name = f'{run}-{step}-{status}'
        self.write(f'.skillsync/evidence/{name}.log', name)
        return self.report('checkpoint', '--run-id', run, '--step', step, '--status', status, '--next', 'n',
                           '--evidence', f'.skillsync/evidence/{name}.log', *extra, code=code)

    def final(self, run='task', *extra):
        return self.rec(run, 'closing', 'PASS', '--final', *extra)

    def stored(self, run='task'):
        return json.loads((self.root / f'.skillsync/runs/{run}.json').read_text(encoding='utf-8'))

    def edit(self, run, step, **fields):
        path = self.root / f'.skillsync/runs/{run}.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        for record in data['checkpoints']:
            if record['step'] == step:
                record.update(fields)
        path.write_text(json.dumps(data), encoding='utf-8')

    def assert_missing(self, report):
        self.assertEqual(len(report['warnings']), 1, report.get('warnings'))
        self.assertTrue(report['warnings'][0].startswith(self.MISSING), report['warnings'])
        self.assertTrue(self.stored()['checkpoints'][-1].get('final'), 'rekord final ma być zapisany mimo ostrzeżenia')

    def test_retro_quality_pass_before_final_gives_no_warning(self):
        self.rec('task', 'retro-quality')
        report = self.final()
        self.assertNotIn('warnings', report)
        self.assertEqual(report['status'], 'PASS')

    def test_final_without_retrospective_warns_and_still_exits_zero(self):
        self.rec('task', 'verify')
        report = self.final()
        self.assert_missing(report)
        self.assertEqual(report['status'], 'PASS')

    def test_final_alone_is_a_missing_retrospective(self):
        self.assert_missing(self.final())

    def test_non_final_records_never_get_the_warning(self):
        self.assertNotIn('warnings', self.rec('task', 'verify'))
        self.assertNotIn('warnings', self.rec('task', 'retro-quality', 'FAIL'))

    def test_retro_quality_fail_does_not_count(self):
        self.rec('task', 'retro-quality', 'FAIL')
        self.assert_missing(self.final())

    def test_other_non_pass_statuses_do_not_count(self):
        for status in ('BLOCKED', 'NOT_RUN'):
            with self.subTest(status=status):
                run = 'task-' + status.lower().replace('_', '')
                self.rec(run, 'retro-quality', status)
                report = self.final(run)
                self.assertEqual(len(report['warnings']), 1, report)
                self.assertTrue(report['warnings'][0].startswith(self.MISSING))

    def test_a_pass_of_another_step_does_not_count(self):
        self.rec('task', 'retro-quality-notes')
        self.rec('task', 'retro')
        self.assert_missing(self.final())

    def test_retro_quality_recorded_after_final_does_not_count(self):
        self.rec('task', 'retro-quality')
        self.edit('task', 'retro-quality', recorded_at='2999-01-01T00:00:00+00:00')
        self.assert_missing(self.final())

    def test_retro_quality_in_the_same_instant_as_final_does_not_count(self):
        self.rec('task', 'retro-quality')
        report = self.final()
        self.assertNotIn('warnings', report)
        stamp = self.stored()['checkpoints'][-1]['recorded_at']
        self.edit('task', 'retro-quality', recorded_at=stamp)
        self.edit('task', 'closing', recorded_at=stamp)
        # drugi final (inny krok): wymóg wyznacza pierwszy final, a ten ma retro dokładnie w tym samym momencie
        report = self.rec('task', 'closing2', 'PASS', '--final')
        self.assertEqual(len(report['warnings']), 1, report)
        self.assertTrue(report['warnings'][0].startswith(self.MISSING))

    def test_requirement_is_set_by_the_first_final_of_the_identity(self):
        self.assert_missing(self.final())
        self.rec('task', 'retro-quality')  # po pierwszym final: spóźnione
        second = self.rec('task', 'closing2', 'PASS', '--final')
        self.assertEqual(len(second['warnings']), 1, second)
        self.assertTrue(second['warnings'][0].startswith(self.MISSING))

    def test_retro_quality_in_a_sibling_attempt_file_counts(self):
        self.rec('task.2', 'retro-quality')
        self.assertNotIn('warnings', self.final('task'))
        self.rec('other', 'retro-quality')
        self.assertNotIn('warnings', self.final('other.3'))

    def test_attempt_files_of_another_identity_do_not_count(self):
        self.rec('task2', 'retro-quality')
        self.rec('task-2.2', 'retro-quality')
        self.rec('xtask.2', 'retro-quality')
        self.assert_missing(self.final('task'))

    def test_unreadable_sibling_gives_unverified_and_the_record_is_still_written(self):
        self.rec('task', 'verify')
        (self.root / '.skillsync/runs/task.2.json').write_text('{not json', encoding='utf-8')
        report = self.final()
        self.assertEqual(len(report['warnings']), 1, report['warnings'])
        self.assertTrue(report['warnings'][0].startswith(self.UNVERIFIED), report['warnings'])
        self.assertEqual(report['status'], 'PASS')
        self.assertTrue(self.stored()['checkpoints'][-1]['final'])

    def test_unreadable_sibling_of_another_identity_is_ignored(self):
        self.rec('task', 'verify')
        (self.root / '.skillsync/runs/other.2.json').write_text('{not json', encoding='utf-8')
        self.assert_missing(self.final())

    BAD_FINAL_DATES = ('0001-01-01T00:00:00+01:00', '9999-12-31T23:59:59-01:00', 'to nie jest data', '')
    NOT_DATES = ('to nie jest data', '')  # plik z taką datą jest nieczytelny (walidacja `_read`), nie trafia do `retrospective`

    def sibling_with_unreadable_final_date(self, value):
        """`task.2.json` ma rekord `final` z `recorded_at` poza zakresem albo niebędącym datą."""
        self.rec('task.2', 'closing', 'PASS', '--final')
        self.edit('task.2', 'closing', recorded_at=value)

    def test_sibling_final_with_an_unusable_date_does_not_crash_and_the_valid_final_decides(self):
        for value in self.BAD_FINAL_DATES:
            with self.subTest(value=value):
                for path in (self.root / '.skillsync/runs').glob('task*.json'):
                    path.unlink()
                self.sibling_with_unreadable_final_date(value)
                report = self.final('task')  # kod 0, bez Tracebacku (report() sprawdza oba)
                if value in self.NOT_DATES:  # nieczytelny plik siostrzany: ostrzeżenie unverified, rekord zapisany
                    self.assertEqual(len(report['warnings']), 1, report['warnings'])
                    self.assertTrue(report['warnings'][0].startswith(self.UNVERIFIED), report['warnings'])
                    self.assertTrue(self.stored()['checkpoints'][-1]['final'])
                else:  # poprawny plik, data poza zakresem nie liczy się: nikt nie ma retro-quality PASS
                    self.assert_missing(report)

    def test_valid_retrospective_before_the_valid_final_gives_no_warning_next_to_an_unusable_sibling_final(self):
        for value in self.BAD_FINAL_DATES:
            with self.subTest(value=value):
                for path in (self.root / '.skillsync/runs').glob('task*.json'):
                    path.unlink()
                self.sibling_with_unreadable_final_date(value)
                self.rec('task', 'retro-quality')
                report = self.final('task')
                self.assertNotIn('warnings', report)
                self.assertTrue(self.stored()['checkpoints'][-1]['final'])

    def test_exit_code_is_zero_in_every_variant(self):
        for run, prepare in (('plain', lambda r: None),
                             ('failed', lambda r: self.rec(r, 'retro-quality', 'FAIL')),
                             ('sibling', lambda r: (self.rec(r + '.2', 'retro-quality'))),
                             ('broken', lambda r: (self.root / f'.skillsync/runs/{r}.2.json').write_text('{', encoding='utf-8'))):
            with self.subTest(run=run):
                prepare(run)
                self.final(run)  # report() sprawdza kod 0

    def test_warning_set_stays_closed_when_other_final_warnings_exist(self):
        self.rec('task', 'verify', 'FAIL')
        report = self.rec('task', 'verify', 'PASS', '--final')
        kinds = sorted(item.split(':', 1)[0] if item.startswith('retro-quality-') else 'step' for item in report['warnings'])
        self.assertEqual(kinds, ['retro-quality-missing', 'step'])


class BenchmarkContracts(WorkflowFixture):
    def suite(self, cases):
        return self.write('suite.yaml', yaml.safe_dump({'cases': cases}))

    def test_no_run_does_not_create_storage_or_execute_command(self):
        sentinel = self.base / 'executed'
        suite = self.suite([{'id': 'plan', 'argv': [sys.executable, '-c', f'from pathlib import Path; Path({str(sentinel)!r}).touch()']}])
        report = self.report('benchmark', '--suite', suite)
        self.assertEqual(report['status'], 'NOT_RUN')
        self.assertFalse(sentinel.exists())
        self.assertFalse((self.root / '.skillsync').exists())

    def test_fresh_fixture_repeatable_output_and_expected_exit(self):
        case = {'id': 'fresh', 'fixture': {'input.txt': 'fixture input'},
                'argv': ['{python}', '-c', 'from pathlib import Path; assert not Path("created").exists(); Path("created").touch(); print(Path("input.txt").read_text()); raise SystemExit(3)'],
                'expected_exit': 3, 'expected_output': 'fixture input'}
        suite = self.suite([case, {**case, 'id': 'fresh-again'}])
        first = self.report('benchmark', '--suite', suite, '--run')
        second = self.report('benchmark', '--suite', suite, '--run')
        self.assertEqual(first['status'], 'PASS')
        self.assertEqual(first['suite_sha256'], second['suite_sha256'])
        self.assertEqual([c['output_sha256'] for c in first['cases']], [c['output_sha256'] for c in second['cases']])
        self.assertTrue(all(c['exit_code'] == 3 and c['duration_seconds'] >= 0 for c in first['cases']))
        self.assertFalse((self.root / 'created').exists())
        self.assertIsNone(first['model_scores'])
        self.assertIsNone(first['cost'])

    def test_wrong_output_exit_and_timeout_fail(self):
        suite = self.suite([
            {'id': 'output', 'argv': ['{python}', '-c', 'print("actual")'], 'expected_output': 'expected'},
            {'id': 'exit', 'argv': ['{python}', '-c', 'raise SystemExit(9)'], 'expected_exit': 0},
            {'id': 'timeout', 'argv': ['{python}', '-c', 'import time; time.sleep(5)'], 'timeout': .02}])
        report = self.report('benchmark', '--suite', suite, '--run', code=2)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual([c['status'] for c in report['cases']], ['FAIL', 'FAIL', 'BLOCKED'])
        self.assertTrue(report['cases'][2]['timeout'])

    @needs_posix
    def test_interrupt_terminates_benchmark_tree(self):
        pid_file = self.root / 'benchmark-pids.json'
        suite = self.suite([{'id': 'interrupt', 'argv': ['{python}', '-c', self.sleeping_tree_script(pid_file)], 'timeout': 180}])
        self.cancel_cli_with_tree('benchmark', ['--suite', suite, '--run'], pid_file)

    def test_invalid_suites_and_fixture_traversal_are_refused_before_execution(self):
        cases = [
            [{'id': 'bad', 'argv': ['{python}', '-c', 'print(1)'], 'fixture': {'../escape': 'bad'}}],
            [{'id': 'bad', 'argv': ['{python}', '-c', 'print(1)'], 'timeout': float('nan')}],
            [{'id': 'bad', 'argv': 'echo unsafe'}],
            [{'id': 'same', 'argv': ['true']}, {'id': 'same', 'argv': ['true']}],
            []]
        for data in cases:
            with self.subTest(cases=data):
                self.rejected('benchmark', '--suite', self.suite(data), '--run')
                self.assertFalse((self.root / '.skillsync/runs').exists())
        self.assertFalse((self.base / 'escape').exists())


if __name__ == '__main__':
    unittest.main()
