"""`skillsync dashboard metrics` (K8): general run metrics against the contract fixtures (`expected-metrics.json` in
tests/fixtures/dashboard/<case>/), explicit missing data, the same sub-step times as `dashboard show`, BLOCKED inputs and
read-only behaviour."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from skillsync_workflow import dashboard  # noqa: E402

CLI = ROOT / 'skillsync'
FIXTURES = ROOT / 'tests' / 'fixtures' / 'dashboard'
MTIME = datetime(2026, 1, 11, 10, 0, tzinfo=timezone.utc).timestamp()
# case -> run id; every case has `expected-metrics.json`
CASES = {'plan-in-progress': '2026-01-10-demo-feature', 'silence': '2026-01-11-demo-report',
         'run-mode': '2026-01-11-demo-research', 'timing': '2026-01-11-timing-demo'}
SUBSTEP_KEYS = {'id', 'title', 'seconds', 'startedAt', 'finishedAt', 'noData', 'source'}
TOP_KEYS = {'schema', 'status', 'run', 'plan', 'generatedAt', 'substeps', 'phases', 'totals', 'rounds', 'failures',
            'interventions', 'timing'}


def normalized(data):
    """Fixture normalization of the metrics output (docs/dashboard-contract.md, `dashboard metrics`)."""
    data = json.loads(json.dumps(data))
    data['generatedAt'] = '2026-01-12T12:00:00+00:00'
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n'


def tree_state(root):
    rows = []
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() and not path.is_symlink() else None
        rows.append((path.relative_to(root).as_posix(), info.st_mode, info.st_mtime_ns, digest))
    return rows


class MetricsFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skillsync-dashboard-metrics-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.env = {**os.environ, 'TZ': 'UTC', 'SKILLSYNC_CONFIG': str(self.base / 'machine-config.yaml'),
                    'XDG_CACHE_HOME': str(self.base / 'xdg-cache'), 'XDG_DATA_HOME': str(self.base / 'xdg-data')}

    def project(self, case):
        root = self.base / case / 'demo'
        shutil.copytree(FIXTURES / case / 'input', root, symlinks=True)
        for path in root.rglob('*'):
            if path.is_file():
                os.utime(path, (MTIME, MTIME))
        return root

    def cli(self, *args, code=0):
        result = subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True, timeout=60,
                                env=self.env, cwd=self.base)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def metrics(self, root, run_id, *extra, code=0):
        return self.cli('dashboard', 'metrics', root, run_id, '--json', *extra, code=code)

    def blocked(self, root, run_id, *extra):
        report = self.metrics(root, run_id, *extra, code=2)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['schema'], 1)
        self.assertTrue(report['reason'])
        return report


class MetricsContract(MetricsFixture):
    def test_metrics_match_every_fixture(self):
        for case, run_id in CASES.items():
            with self.subTest(case=case):
                report = self.metrics(self.project(case), run_id)
                expected = (FIXTURES / case / 'expected-metrics.json').read_text(encoding='utf-8')
                self.assertEqual(normalized(report), expected)

    def test_shape_of_the_result(self):
        for case, run_id in CASES.items():
            with self.subTest(case=case):
                report = self.metrics(self.project(case), run_id)
                self.assertEqual(set(report), TOP_KEYS)
                self.assertEqual((report['schema'], report['status'], report['run']), (1, 'PASS', run_id))
                self.assertIsNone(report['interventions'])
                for item in report['substeps']:
                    self.assertEqual(set(item), SUBSTEP_KEYS)
                    self.assertIn(item['source'], ('checkpoints.md', 'records', None))
                    self.assertEqual(item['noData'], item['seconds'] is None)
                    self.assertEqual(item['source'] is None, item['noData'])
                    if item['seconds'] is not None:
                        self.assertIsInstance(item['seconds'], int)
                        self.assertNotIsInstance(item['seconds'], bool)
                totals = report['totals']
                self.assertEqual(totals['substeps'], len(report['substeps']))
                self.assertEqual(totals['withData'] + totals['withoutData'], totals['substeps'])
                self.assertEqual(set(report['rounds']), {'records', 'failed', 'notRun', 'repeatedSteps'})

    def test_without_json_prints_the_same_json(self):
        root = self.project('plan-in-progress')
        plain = self.cli('dashboard', 'metrics', root, CASES['plan-in-progress'])
        self.assertEqual(normalized(plain), normalized(self.metrics(root, CASES['plan-in-progress'])))

    def test_substep_without_data_is_explicit(self):
        report = self.metrics(self.project('plan-in-progress'), CASES['plan-in-progress'])
        steps = {item['id']: item for item in report['substeps']}
        # started, never finished: the start mark stays visible, the time is not guessed
        self.assertEqual((steps['F1.3']['startedAt'], steps['F1.3']['finishedAt']), ('09:20', None))
        self.assertEqual((steps['F1.3']['seconds'], steps['F1.3']['noData'], steps['F1.3']['source']), (None, True, None))
        for sid in ('F2.1', 'F2.2'):
            self.assertEqual((steps[sid]['seconds'], steps[sid]['noData'], steps[sid]['source']), (None, True, None))
        self.assertEqual(report['phases'], [{'id': 'F1', 'seconds': None}, {'id': 'F2', 'seconds': None}])
        self.assertEqual((report['totals']['withData'], report['totals']['withoutData']), (2, 3))
        self.assertIsNone(report['totals']['seconds'])
        self.assertEqual(report['totals']['measuredSeconds'], 1800)

    def test_records_fill_a_substep_only_when_the_log_has_no_time(self):
        root = self.project('plan-in-progress')
        report = self.metrics(root, CASES['plan-in-progress'])
        steps = {item['id']: item for item in report['substeps']}
        self.assertEqual((steps['F1.1']['seconds'], steps['F1.1']['source']), (900, 'checkpoints.md'))  # log wins over 840
        self.assertEqual((steps['F1.2']['seconds'], steps['F1.2']['source']), (900, 'records'))  # 300 FAIL + 600 PASS
        self.assertEqual(report['rounds'], {'records': 4, 'failed': 1, 'notRun': 1, 'repeatedSteps': 1})
        self.assertEqual(report['failures'], [{'step': 'F1.2', 'status': 'FAIL', 'causeKey': 'fixture-drift'}])
        (root / '.skillsync' / 'runs' / f"{CASES['plan-in-progress']}.json").unlink()
        report = self.metrics(root, CASES['plan-in-progress'])
        steps = {item['id']: item for item in report['substeps']}
        self.assertEqual((steps['F1.2']['seconds'], steps['F1.2']['noData'], steps['F1.2']['source']), (None, True, None))
        self.assertEqual(report['rounds'], {'records': None, 'failed': None, 'notRun': None, 'repeatedSteps': None})
        self.assertEqual(report['failures'], [])

    def test_complete_phase_and_totals_are_summed(self):
        root = self.project('silence')
        log = root / '.skillsync' / 'runs' / CASES['silence'] / 'checkpoints.md'
        log.write_text(log.read_text(encoding='utf-8') + '- 09:10 zrobione: F1.2\n', encoding='utf-8')
        report = self.metrics(root, CASES['silence'])
        self.assertEqual([item['seconds'] for item in report['substeps']], [2100, 1800])
        self.assertEqual(report['phases'], [{'id': 'F1', 'seconds': 3900}])
        self.assertEqual((report['totals']['seconds'], report['totals']['measuredSeconds']), (3900, 3900))
        self.assertEqual(report['totals']['withoutData'], 0)

    def test_without_a_plan_substeps_come_from_the_log(self):
        root = self.project('run-mode')
        log = root / '.skillsync' / 'runs' / CASES['run-mode'] / 'checkpoints.md'
        log.write_text(log.read_text(encoding='utf-8')
                       + '- 23:50 w toku: F2.1\n- 23:55 w toku: F1.1, F1\n- 00:10 zrobione: F2.1\n', encoding='utf-8')
        report = self.metrics(root, CASES['run-mode'])
        self.assertIsNone(report['plan'])
        self.assertEqual([(item['id'], item['title'], item['seconds']) for item in report['substeps']],
                         [('F2.1', None, 1200), ('F1.1', None, None)])  # midnight crossing; phase id F1 is not a sub-step
        self.assertEqual(report['phases'], [{'id': 'F2', 'seconds': 1200}, {'id': 'F1', 'seconds': None}])

    def test_plan_option(self):
        root = self.project('plan-in-progress')
        rel = 'docs/plans/2026-01-10-demo-feature.md'
        self.assertEqual(self.metrics(root, CASES['plan-in-progress'], '--plan', rel)['plan'], rel)
        self.blocked(root, CASES['plan-in-progress'], '--plan', 'docs/plans/missing.md')
        self.blocked(root, CASES['plan-in-progress'], '--plan', '../outside.md')


class MetricsAgreeWithShow(MetricsFixture):
    def test_same_substep_times_as_show(self):
        for case in ('plan-in-progress', 'silence'):
            with self.subTest(case=case):
                root = self.project(case)
                shown = self.cli('dashboard', 'show', root, '--json')
                metrics = self.metrics(root, shown['run']['id'])
                self.assertEqual(metrics['plan'], shown['plan']['path'])
                self.assertEqual([item['id'] for item in metrics['substeps']], [item['id'] for item in shown['substeps']])
                for mine, theirs in zip(metrics['substeps'], shown['substeps']):
                    self.assertEqual((mine['title'], mine['startedAt'], mine['finishedAt']),
                                     (theirs['title'], theirs['startedAt'], theirs['finishedAt']))
                    if theirs['seconds'] is not None:
                        self.assertEqual((mine['seconds'], mine['source']), (theirs['seconds'], 'checkpoints.md'))
                    elif mine['source'] != 'records':
                        self.assertIsNone(mine['seconds'])

    def test_one_shared_time_function(self):
        self.assertEqual(dashboard.substep_seconds('23:50', '00:10'), 1200)
        self.assertEqual(dashboard.substep_seconds('09:05', '09:20'), 900)
        self.assertIsNone(dashboard.substep_seconds('09:05', None))
        self.assertIsNone(dashboard.substep_seconds(None, '09:20'))
        _, items = dashboard.parse_checkpoints('- 09:05 w toku: F1.1\n- 09:20 zrobione: F1.1; w toku: F1.2\n')
        started, finished, order = dashboard.substep_marks(items)
        self.assertEqual((started, finished, order), ({'F1.1': '09:05', 'F1.2': '09:20'}, {'F1.1': '09:20'}, ['F1.1', 'F1.2']))


class MetricsBlocked(MetricsFixture):
    def test_invalid_run_ids(self):
        root = self.project('plan-in-progress')
        for run_id in ('../x', '..', '.', 'a/b', '.x', 'x' * 121):
            with self.subTest(run_id=run_id):
                self.blocked(root, run_id)

    def test_missing_run_and_missing_project(self):
        root = self.project('plan-in-progress')
        self.assertIn('no run directory', self.blocked(root, '2026-01-10-other')['reason'])
        self.blocked(self.base / 'nowhere', CASES['plan-in-progress'])

    def test_symlinked_run_directory(self):
        root = self.project('plan-in-progress')
        runs = root / '.skillsync' / 'runs'
        outside = self.base / 'outside-run'
        shutil.copytree(runs / CASES['plan-in-progress'], outside)
        (runs / 'linked').symlink_to(outside, target_is_directory=True)
        self.assertIn('symlink', self.blocked(root, 'linked')['reason'])

    def test_symlinked_checkpoints_and_records(self):
        root = self.project('plan-in-progress')
        run_dir = root / '.skillsync' / 'runs' / CASES['plan-in-progress']
        log = run_dir / 'checkpoints.md'
        target = self.base / 'outside-checkpoints.md'
        shutil.copy(log, target)
        log.unlink()
        log.symlink_to(target)
        self.assertIn('symlink', self.blocked(root, CASES['plan-in-progress'])['reason'])

        root = self.project('silence')
        records = root / '.skillsync' / 'runs' / f"{CASES['silence']}.json"
        target = self.base / 'outside-records.json'
        shutil.copy(FIXTURES / 'plan-in-progress' / 'input' / '.skillsync' / 'runs' / f"{CASES['plan-in-progress']}.json",
                    target)
        records.symlink_to(target)
        self.blocked(root, CASES['silence'])

    def test_symlinked_runs_store(self):
        root = self.project('silence')
        runs = root / '.skillsync' / 'runs'
        outside = self.base / 'outside-runs'
        shutil.move(str(runs), outside)
        runs.symlink_to(outside, target_is_directory=True)
        self.assertIn('symlink', self.blocked(root, CASES['silence'])['reason'])

    def test_unreadable_records_are_blocked(self):
        root = self.project('silence')
        (root / '.skillsync' / 'runs' / f"{CASES['silence']}.json").write_text('{"schema_version": 2}', encoding='utf-8')
        self.assertIn('run records', self.blocked(root, CASES['silence'])['reason'])


class MetricsReadOnly(MetricsFixture):
    def test_metrics_write_nothing(self):
        for case, run_id in CASES.items():
            with self.subTest(case=case):
                root = self.project(case)
                before = tree_state(root)
                self.metrics(root, run_id)
                self.metrics(root, '../x', code=2)
                self.metrics(root, 'missing-run', code=2)
                self.assertEqual(tree_state(root), before)


if __name__ == '__main__':
    unittest.main()
