"""`skillsync resources` (CORE-1150-AUTOKEEP, A-1): how many more sub-agents fit now.

The limits are checked on injected sources (load, memory, cpus, clock); the open runs come from fixtures made with the real
`agent-run` command; the command line goes through the real CLI.
"""
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from layers_helpers import ComposeCase  # noqa: E402
from skillsync_workflow import resources  # noqa: E402

RUN = '2026-11-23-core-1150-autokeep'


def at(minutes_ago=0):
    return datetime.now().astimezone() - timedelta(minutes=minutes_ago)


class ResourcesCase(ComposeCase):
    def project(self):
        path = self.new_project('p', skills=['alpha'])
        (path / '.skillsync' / 'runs' / RUN).mkdir(parents=True, exist_ok=True)
        return path

    def start(self, project, role='implementer', step='A-1', kind='run', run=RUN):
        (Path(project) / '.skillsync' / 'runs' / run).mkdir(parents=True, exist_ok=True)
        args = ['agent-run', 'start', str(project), '--run-id', run, '--role', role, '--kind', kind, '--json']
        if step:
            args += ['--step', step]
        done = self.cli(*args)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return json.loads(done.stdout)['agent']

    def report(self, project, load=0.5, mem=16384, cpus=8, max_agents=5, minutes_later=0):
        return resources.collect(project, max_agents, load=lambda: load, mem=lambda: mem, cpus=lambda: cpus,
                                 now=lambda: at(-minutes_later))


class Limits(ResourcesCase):
    def test_free_machine_is_limited_by_max(self):
        report = self.report(self.project())
        self.assertEqual(report['schema'], 1)
        self.assertEqual(report['limits'], {'byMax': 5, 'byLoad': 5, 'byMemory': 13})
        self.assertEqual(report['allowed'], 5)
        self.assertIn('byMax', report['reason'])

    def test_running_runs_lower_by_max(self):
        project = self.project()
        first = self.start(project)
        self.start(project, role='test-writer', step='A-2')
        report = self.report(project)
        self.assertEqual(report['limits']['byMax'], 3)
        self.assertEqual(report['allowed'], 3)
        self.assertEqual([item['agent'] for item in report['running']][0], first)
        self.assertEqual(set(report['running'][0]), {'agent', 'role', 'step', 'startedAt'})
        self.assertEqual(report['running'][1]['step'], 'A-2')

    def test_load_limits(self):
        report = self.report(self.project(), load=5.0, cpus=8)   # floor(0.75 * 8 - 5.0) = 1
        self.assertEqual((report['limits']['byLoad'], report['allowed']), (1, 1))
        self.assertIn('byLoad', report['reason'])

    def test_memory_limits(self):
        report = self.report(self.project(), mem=3072 + 2048 + 100)   # floor(2148 / 1024) = 2
        self.assertEqual((report['limits']['byMemory'], report['allowed']), (2, 2))
        self.assertIn('byMemory', report['reason'])

    def test_the_smallest_limit_wins(self):
        project = self.project()
        self.start(project)
        report = self.report(project, load=1.0, cpus=4, mem=9000)   # byMax 4, byLoad 2, byMemory 5
        self.assertEqual((report['limits'], report['allowed']), ({'byMax': 4, 'byLoad': 2, 'byMemory': 5}, 2))

    def test_null_sources_limit_nothing_and_the_reason_says_so(self):
        report = resources.collect(self.project(), 5, load=lambda: None, mem=lambda: None, cpus=lambda: 4, now=at)
        self.assertEqual(report['limits'], {'byMax': 5, 'byLoad': None, 'byMemory': None})
        self.assertEqual(report['allowed'], 5)
        self.assertIn('byLoad unknown', report['reason'])
        self.assertIn('byMemory unknown', report['reason'])
        self.assertIsNone(report['host']['load1'])
        self.assertIsNone(report['host']['memAvailableMiB'])

    def test_allowed_is_at_least_one_when_nothing_runs(self):
        report = self.report(self.project(), load=40.0, cpus=2, mem=100)   # both limits are negative
        self.assertLess(report['limits']['byLoad'], 0)
        self.assertEqual(report['allowed'], 1)
        self.assertIn('always allowed', report['reason'])

    def test_allowed_is_never_negative_when_something_runs(self):
        project = self.project()
        self.start(project)
        report = self.report(project, load=40.0, cpus=2, mem=100)
        self.assertEqual(report['allowed'], 0)

    def test_more_runs_than_the_limit_gives_zero(self):
        project = self.project()
        for number in range(3):
            self.start(project, step=f'A-{number + 1}')
        self.assertEqual(self.report(project, max_agents=2)['allowed'], 0)

    def test_max_must_be_one_to_five(self):
        project = self.project()
        for bad in (0, 6, -1, True):
            with self.assertRaises(ValueError):
                self.report(project, max_agents=bad)
        self.assertEqual(self.report(project, max_agents=1)['allowed'], 1)

    def test_cpu_count_none_counts_as_one(self):
        report = resources.collect(self.project(), 5, load=lambda: 0.0, mem=lambda: None, cpus=lambda: None, now=at)
        self.assertEqual((report['host']['cpus'], report['limits']['byLoad']), (1, 0))


class OpenRuns(ResourcesCase):
    def test_a_run_older_than_90_minutes_is_abandoned(self):
        project = self.project()
        self.start(project)
        self.assertEqual(len(self.report(project, minutes_later=89)['running']), 1)
        self.assertEqual(self.report(project, minutes_later=91)['running'], [])

    def test_ended_runs_do_not_count(self):
        project = self.project()
        agent = self.start(project)
        done = self.cli('agent-run', 'end', str(project), '--run-id', RUN, '--agent', agent, '--result', 'DONE')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.report(project)['running'], [])

    def test_runs_of_every_run_directory_count(self):
        project = self.project()
        self.start(project)
        self.start(project, run='2026-11-22-other', role='reviewer', kind='review', step=None)
        report = self.report(project)
        self.assertEqual(len(report['running']), 2)
        self.assertIsNone([item for item in report['running'] if item['role'] == 'reviewer'][0]['step'])

    def test_a_run_without_a_zone_in_the_time_is_ignored(self):
        project = self.project()
        log = project / '.skillsync' / 'runs' / RUN / 'agents.jsonl'
        log.write_text(json.dumps({'v': 1, 't': 'not-a-time', 'ev': 'start', 'agent': 'a-1', 'role': 'implementer', 'kind': 'run'}) + '\n',
                       encoding='utf-8')
        self.assertEqual(self.report(project)['running'], [])

    def test_utc_time_is_read_with_its_zone(self):
        project = self.project()
        stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
        log = project / '.skillsync' / 'runs' / RUN / 'agents.jsonl'
        log.write_text(json.dumps({'v': 1, 't': stamp, 'ev': 'start', 'agent': 'a-1', 'role': 'implementer', 'kind': 'run'}) + '\n', encoding='utf-8')
        self.assertEqual(len(self.report(project)['running']), 1)

    def test_a_project_without_runs_has_none(self):
        project = self.new_project('empty', skills=['alpha'])
        self.assertEqual(self.report(project)['running'], [])


class Sources(unittest.TestCase):
    def test_read_functions_return_numbers_or_none(self):
        load, memory = resources.read_load1(), resources.read_mem_available_mib()
        self.assertTrue(load is None or load >= 0)
        self.assertTrue(memory is None or memory > 0)


class CommandLine(ResourcesCase):
    def test_json_output_has_the_contract_fields(self):
        project = self.project()
        done = self.cli('resources', str(project), '--json')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        data = json.loads(done.stdout)
        self.assertEqual(set(data), {'schema', 'host', 'running', 'max', 'allowed', 'limits', 'reason'})
        self.assertEqual(set(data['host']), {'os', 'cpus', 'load1', 'memAvailableMiB'})
        self.assertGreaterEqual(data['allowed'], 1)

    def test_text_output_and_max(self):
        done = self.cli('resources', str(self.project()), '--max', '2')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('allowed:', done.stdout)
        self.assertIn('max 2', done.stdout)

    def test_bad_max_is_a_usage_error(self):
        for bad in ('0', '6', 'x'):
            done = self.cli('resources', str(self.project()), '--max', bad)
            self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
            self.assertNotIn('Traceback', done.stderr)

    def test_a_path_without_a_manifest_still_reports(self):
        done = self.cli('resources', str(self.work), '--json')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(json.loads(done.stdout)['running'], [])


if __name__ == '__main__':
    unittest.main()
