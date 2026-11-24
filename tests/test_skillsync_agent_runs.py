"""`skillsync agent-run`: the log of the runs of sub-agents (`.skillsync/runs/<run-id>/agents.jsonl`), K1, K6 and the cases of K8.

The commands go through the real CLI in an isolated project (`ComposeCase`); the concurrency test starts 20 processes that import the
module (no machine state is touched). The clock of the module is replaced where a time matters.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from layers_helpers import ComposeCase, needs_os_symlinks  # noqa: E402
from skillsync_workflow import agent_runs  # noqa: E402

RUN = '2026-11-23-core-1150-n16r'


class AgentRunCase(ComposeCase):
    def project(self, name='p'):
        return self.new_project(name, skills=['alpha'])

    def run_ok(self, project, *args):
        if args[0] == 'start':   # `agent-run start` does not create the run directory (`review-round` does)
            run_id = args[args.index('--run-id') + 1]
            (Path(project) / '.skillsync' / 'runs' / run_id).mkdir(parents=True, exist_ok=True)
        done = self.cli('agent-run', *args[:1], str(project), *args[1:], '--json')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn('Traceback', done.stderr)
        return json.loads(done.stdout)

    def run_refused(self, project, *args):
        done = self.cli('agent-run', *args[:1], str(project), *args[1:])
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertNotIn('Traceback', done.stderr)
        return done.stderr

    def log(self, project, run_id=RUN):
        path = Path(project) / '.skillsync' / 'runs' / run_id / 'agents.jsonl'
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.is_file() else []


class StartEtaEnd(AgentRunCase):
    def test_start_eta_end_write_the_lines_of_the_contract(self):
        project = self.project()
        agent = self.run_ok(project, 'start', '--run-id', RUN, '--role', 'reviewer', '--kind', 'review', '--profile', 'spec',
                            '--round', '2', '--eta', '14:35')['agent']
        self.assertEqual(agent, 'a-1')
        self.run_ok(project, 'eta', '--run-id', RUN, '--agent', agent, '--eta', '15:05')
        ended = self.run_ok(project, 'end', '--run-id', RUN, '--agent', agent, '--result', 'CHANGES_REQUIRED', '--report', 'spec-review-2.md')
        self.assertEqual(ended, {'agent': 'a-1', 'result': 'CHANGES_REQUIRED'})
        start, eta, end = self.log(project)
        self.assertEqual({key: start[key] for key in ('v', 'ev', 'agent', 'role', 'kind', 'profile', 'round', 'eta')},
                         {'v': 1, 'ev': 'start', 'agent': 'a-1', 'role': 'reviewer', 'kind': 'review', 'profile': 'spec', 'round': 2, 'eta': '14:35'})
        self.assertNotIn('step', start)   # absent, not null
        self.assertEqual((eta['ev'], eta['agent'], eta['eta']), ('eta', 'a-1', '15:05'))
        self.assertEqual((end['ev'], end['result'], end['report']), ('end', 'CHANGES_REQUIRED', 'spec-review-2.md'))
        for item in (start, eta, end):
            self.assertRegex(item['t'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$')

    def test_start_needs_the_run_directory_and_ids_grow_over_the_whole_file(self):
        project = self.project()
        self.run_refused(project, 'start', '--run-id', RUN, '--role', 'implementer', '--kind', 'run')   # a typo must not leave a directory
        self.assertFalse((Path(project) / '.skillsync' / 'runs' / RUN).exists())
        (Path(project) / '.skillsync' / 'runs' / RUN).mkdir(parents=True)
        first = self.run_ok(project, 'start', '--run-id', RUN, '--role', 'implementer', '--kind', 'run', '--step', 'F1.1')['agent']
        second = self.run_ok(project, 'start', '--run-id', RUN, '--role', 'test-runner', '--kind', 'run', '--step', 'F1.2')['agent']
        self.run_ok(project, 'end', '--run-id', RUN, '--agent', second, '--result', 'PASS')
        third = self.run_ok(project, 'start', '--run-id', RUN, '--role', 'scribe', '--kind', 'run')['agent']
        self.assertEqual((first, second, third), ('a-1', 'a-2', 'a-3'))

    def test_a_new_start_of_the_same_work_closes_the_open_run_as_aborted(self):
        project = self.project()
        a = self.run_ok(project, 'start', '--run-id', RUN, '--role', 'implementer', '--kind', 'run', '--step', 'F1.1', '--round', '1')['agent']
        b = self.run_ok(project, 'start', '--run-id', RUN, '--role', 'implementer', '--kind', 'run', '--step', 'F1.1', '--round', '1')['agent']
        other = self.run_ok(project, 'start', '--run-id', RUN, '--role', 'implementer', '--kind', 'run', '--step', 'F1.2')['agent']
        events = self.log(project)
        ends = [(item['agent'], item['result']) for item in events if item['ev'] == 'end']
        self.assertEqual(ends, [(a, 'ABORTED')])   # the other step is a different work: still open
        self.assertEqual((b, other), ('a-2', 'a-3'))

    def test_review_rounds_of_one_profile_and_step_replace_each_other_in_any_round(self):
        project = self.project()
        a = self.run_ok(project, 'start', '--run-id', RUN, '--role', 'reviewer', '--kind', 'review', '--profile', 'code', '--step', 'F2.1', '--round', '1')['agent']
        self.run_ok(project, 'start', '--run-id', RUN, '--role', 'reviewer', '--kind', 'review', '--profile', 'code', '--step', 'F2.1', '--round', '2')
        self.run_ok(project, 'start', '--run-id', RUN, '--role', 'reviewer', '--kind', 'review', '--profile', 'spec', '--round', '1')
        ends = [(item['agent'], item['result']) for item in self.log(project) if item['ev'] == 'end']
        self.assertEqual(ends, [(a, 'ABORTED')])   # another profile is untouched

    def test_end_without_an_id_picks_the_one_open_run_after_a_session_restart(self):
        project = self.project()
        self.run_ok(project, 'start', '--run-id', RUN, '--role', 'reviewer', '--kind', 'review', '--profile', 'plan', '--round', '1')
        ended = self.run_ok(project, 'end', '--run-id', RUN, '--role', 'reviewer', '--result', 'PASS')
        self.assertEqual(ended['agent'], 'a-1')
        # two open runs of the same role: ambiguous
        self.run_ok(project, 'start', '--run-id', RUN, '--role', 'implementer', '--kind', 'run', '--step', 'F1.1')
        self.run_ok(project, 'start', '--run-id', RUN, '--role', 'implementer', '--kind', 'run', '--step', 'F1.2')
        self.assertIn('--agent', self.run_refused(project, 'end', '--run-id', RUN, '--role', 'implementer', '--result', 'DONE'))
        self.run_ok(project, 'end', '--run-id', RUN, '--role', 'implementer', '--step', 'F1.2', '--result', 'DONE')

    def test_the_run_id_of_a_dispatch_package_is_accepted(self):
        project = self.project()
        run_id = '2026-11-23-core-1150-n16r.pack-2'
        self.run_ok(project, 'start', '--run-id', run_id, '--role', 'explore', '--kind', 'run')
        self.assertEqual(len(self.log(project, run_id)), 1)

    def test_two_runs_of_one_step_in_parallel_are_both_kept(self):
        project = self.project()
        self.run_ok(project, 'start', '--run-id', RUN, '--role', 'reviewer', '--kind', 'review', '--profile', 'code', '--step', 'F1.1')
        self.run_ok(project, 'start', '--run-id', RUN, '--role', 'verifier', '--kind', 'run', '--step', 'F1.1')
        opened = agent_runs.open_runs(agent_runs.read_events(Path(project) / '.skillsync' / 'runs' / RUN))
        self.assertEqual(sorted(opened), ['a-1', 'a-2'])


class Refusals(AgentRunCase):
    def test_every_bad_value_is_a_refusal_with_code_2_and_writes_nothing(self):
        project = self.project()
        good = ['start', '--run-id', RUN, '--role', 'implementer', '--kind', 'run']
        cases = [
            ('role', ['start', '--run-id', RUN, '--role', 'wizard', '--kind', 'run']),
            ('kind', ['start', '--run-id', RUN, '--role', 'implementer', '--kind', 'job']),
            ('review kind needs a reviewer', ['start', '--run-id', RUN, '--role', 'implementer', '--kind', 'review']),
            ('profile on a run', good + ['--profile', 'spec']),
            ('unknown profile', ['start', '--run-id', RUN, '--role', 'reviewer', '--kind', 'review', '--profile', 'x']),
            ('step', good + ['--step', 'a b']),
            ('review step with a double dash', ['start', '--run-id', RUN, '--role', 'reviewer', '--kind', 'review', '--step', 'a--b']),
            ('review step with an underscore', ['start', '--run-id', RUN, '--role', 'reviewer', '--kind', 'review', '--step', 'F2_1']),
            ('round 0', good + ['--round', '0']),
            ('round 100', good + ['--round', '100']),
            ('eta', good + ['--eta', '25:00']),
            ('eta text', good + ['--eta', 'soon']),
            ('run id', ['start', '--run-id', '../x', '--role', 'implementer', '--kind', 'run']),
            ('run id with a space', ['start', '--run-id', 'a b', '--role', 'implementer', '--kind', 'run']),
        ]
        for name, args in cases:
            with self.subTest(name):
                done = self.cli('agent-run', args[0], str(project), *args[1:])
                self.assertNotEqual(done.returncode, 0, done.stdout)
                self.assertNotIn('Traceback', done.stderr)
        self.assertEqual(self.log(project), [])

    def test_end_and_eta_refusals(self):
        project = self.project()
        self.run_refused(project, 'end', '--run-id', RUN, '--agent', 'a-1', '--result', 'PASS')   # no directory at all
        agent = self.run_ok(project, 'start', '--run-id', RUN, '--role', 'reviewer', '--kind', 'review', '--profile', 'spec')['agent']
        self.run_refused(project, 'end', '--run-id', RUN, '--agent', 'a-9', '--result', 'PASS')    # no such run
        self.run_refused(project, 'end', '--run-id', RUN, '--agent', agent, '--result', 'DONE')    # a result of another role
        self.run_refused(project, 'end', '--run-id', RUN, '--agent', agent, '--result', 'PASS', '--report', '../x.md')
        self.run_refused(project, 'end', '--run-id', RUN, '--agent', agent, '--result', 'PASS', '--report', 'x.txt')
        self.run_refused(project, 'eta', '--run-id', RUN, '--agent', agent, '--eta', '9:5')
        self.run_ok(project, 'end', '--run-id', RUN, '--agent', agent, '--result', 'PASS')
        self.run_refused(project, 'end', '--run-id', RUN, '--agent', agent, '--result', 'PASS')    # a second end
        self.run_refused(project, 'eta', '--run-id', RUN, '--agent', agent, '--eta', '16:00')      # a closed run
        self.assertEqual([item['ev'] for item in self.log(project)], ['start', 'end'])

    def test_verifier_has_verdicts_not_a_status(self):
        project = self.project()
        agent = self.run_ok(project, 'start', '--run-id', RUN, '--role', 'verifier', '--kind', 'run')['agent']
        self.run_refused(project, 'end', '--run-id', RUN, '--agent', agent, '--result', 'PASS')
        self.run_ok(project, 'end', '--run-id', RUN, '--agent', agent, '--result', 'CONFIRMED')

    @needs_os_symlinks
    def test_a_symlink_in_the_place_of_the_run_directory_or_the_log_is_refused(self):
        project = self.project()
        runs = Path(project) / '.skillsync' / 'runs'
        runs.mkdir(parents=True)
        target = Path(project) / 'elsewhere'
        target.mkdir()
        (runs / RUN).symlink_to(target)
        self.run_refused(project, 'start', '--run-id', RUN, '--role', 'implementer', '--kind', 'run')
        (runs / RUN).unlink()
        (runs / RUN).mkdir()
        (runs / RUN / 'agents.jsonl').symlink_to(target / 'log')
        self.run_refused(project, 'start', '--run-id', RUN, '--role', 'implementer', '--kind', 'run')
        self.assertEqual(list(target.iterdir()), [])


class LogContent(AgentRunCase):
    def test_the_log_holds_no_free_text(self):
        project = self.project()
        agent = self.run_ok(project, 'start', '--run-id', RUN, '--role', 'reviewer', '--kind', 'review', '--profile', 'spec', '--eta', '14:35')['agent']
        self.run_ok(project, 'end', '--run-id', RUN, '--agent', agent, '--result', 'PASS', '--report', 'spec-review-1.md')
        allowed = {'v', 't', 'ev', 'agent', 'role', 'kind', 'profile', 'step', 'round', 'eta', 'result', 'report'}
        for item in self.log(project):
            self.assertLessEqual(set(item), allowed)
            for value in item.values():
                if isinstance(value, str):
                    self.assertLess(len(value), 100)

    def test_bad_lines_are_skipped_and_the_ids_come_from_the_whole_file(self):
        project = self.project()
        directory = Path(project) / '.skillsync' / 'runs' / RUN
        directory.mkdir(parents=True)
        (directory / 'agents.jsonl').write_text('not json\n{"v":2,"ev":"start","agent":"a-9"}\n'
                                                '{"v":1,"ev":"start","agent":"a-7","t":"x","role":"scribe","kind":"run"}\n[1]\n', encoding='utf-8')
        events = agent_runs.read_events(directory)
        self.assertEqual([item['agent'] for item in events], ['a-7'])
        self.assertEqual(self.run_ok(project, 'start', '--run-id', RUN, '--role', 'explore', '--kind', 'run')['agent'], 'a-8')


class Concurrency(unittest.TestCase):
    def test_twenty_processes_lose_no_line_and_share_no_id(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix='agent-runs-') as tmp:
            base = os.path.realpath(tmp)
            (Path(base) / '.skillsync' / 'runs' / RUN).mkdir(parents=True)
            code = ("import sys; sys.path.insert(0, %r); from skillsync_workflow import agent_runs; "
                    "print(agent_runs.start(sys.argv[1], %r, 'implementer', 'run', None, 'F' + sys.argv[2]))" % (str(ROOT), RUN))
            env = {key: value for key, value in os.environ.items()}
            procs = [subprocess.Popen([sys.executable, '-c', code, base, str(number)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      env=env, text=True) for number in range(20)]
            outputs = []
            for proc in procs:
                out, err = proc.communicate(timeout=120)
                self.assertEqual(proc.returncode, 0, err)
                outputs.append(out.strip())
            self.assertEqual(len(set(outputs)), 20, outputs)
            events = agent_runs.read_events(Path(base) / '.skillsync' / 'runs' / RUN)
            self.assertEqual(len(events), 20)
            self.assertEqual(sorted(item['agent'] for item in events), sorted(f'a-{n}' for n in range(1, 21)))


if __name__ == '__main__':
    unittest.main()
