"""180-A: `skillsync test-run` and the observer of the shared runner (stage 1)."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from tmp_paths import child  # noqa: E402
from layers_helpers import guard_real_state, home_env  # noqa: E402
from skillsync_workflow import quality, test_events as ev, test_run  # noqa: E402

CLI = ROOT / 'skillsync'
PASSING = "print('Ran 2 tests in 0.01s'); print('OK')"
JUNIT = ('<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="s">'
         '<testcase classname="pkg.A" name="ok" time="0.010"/>'
         '<testcase classname="pkg.A" name="bad" time="0.020"><failure message="1 != 2">trace</failure></testcase>'
         '<testcase classname="pkg.B" name="skip" time="0"><skipped/></testcase></testsuite></testsuites>')


def project(tmp, checkpoint='- 10:00 start; w toku: F1.1\n'):
    root = Path(tmp) / 'p'
    (root / '.github').mkdir(parents=True)
    (root / '.github/.skillsync.yaml').write_text('schema: 1\n', encoding='utf-8')
    if checkpoint is not None:
        run = root / '.skillsync/runs/R1'
        run.mkdir(parents=True)
        (run / 'checkpoints.md').write_text(checkpoint, encoding='utf-8')
    return root


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='test-run-')
        guard_real_state(self)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = project(self.tmp)
        self.env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', **home_env(self.tmp),
                    'SKILLSYNC_CONFIG': f'{self.tmp}/cfg.yaml'}

    def run_cli(self, *args, root=None):
        return subprocess.run([sys.executable, str(CLI), 'test-run', str(root or self.root), *args], capture_output=True, text=True,
                              env=self.env, timeout=60)

    def attempt(self, step='F1.1', n=1):
        return ev.read_attempt(child(child(self.root / '.skillsync/runs/R1/tests', step), f'{n}.jsonl'))


class SplitCommand(unittest.TestCase):
    """180-W: the command after `--` is cut off BEFORE argparse, so Python 3.11 (a positional `nargs='*'` matched empty before the options) and later
    read `test-run <project> --step X -- cmd` the same way."""

    def test_table(self):
        split = test_run.split_command
        cases = [
            (['test-run', 'proj', '--step', 'F1', '--', 'cmd', 'a'], (['test-run', 'proj', '--step', 'F1'], ['cmd', 'a'])),
            (['test-run', 'proj', '--', 'cmd'], (['test-run', 'proj'], ['cmd'])),
            (['test-run', 'proj', '--step', 'F1', '--', 'python', '-c', 'x', '--', 'y', '--help'], (['test-run', 'proj', '--step', 'F1'], ['python', '-c', 'x', '--', 'y', '--help'])),
            (['test-run', 'proj', '--import', 'f.jsonl', '--step', 'F1', '--source-sha', 'abc'], (['test-run', 'proj', '--import', 'f.jsonl', '--step', 'F1', '--source-sha', 'abc'], None)),
            (['test-run', 'proj', '--'], (['test-run', 'proj'], [])),
            (['test-run', '--help'], (['test-run', '--help'], None)),
            (['test-run', 'proj', '--timeout', '5', '--format', 'none', '--', '--version'], (['test-run', 'proj', '--timeout', '5', '--format', 'none'], ['--version'])),
            (['status', 'proj', '--', 'x'], (['status', 'proj', '--', 'x'], None)),   # another command: untouched
            ([], ([], None)),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                self.assertEqual(split(argv), expected)

    def test_the_head_parses_without_the_command_on_every_version(self):
        parser = argparse.ArgumentParser(prog='skillsync')
        sub = parser.add_subparsers(dest='cmd', required=True)
        test_run.register(sub)
        head, tail = test_run.split_command(['test-run', 'proj', '--step', 'F1', '--allow-skips', '--', 'cmd', 'a'])
        args = parser.parse_args(head)
        self.assertEqual((args.path, args.step, args.allow_skips, args.command, tail), ('proj', 'F1', True, [], ['cmd', 'a']))


class Resolution(unittest.TestCase):
    def test_tool_detection(self):
        for command, tool in ((['dotnet', 'test'], 'dotnet'), (['python3', '-m', 'pytest', '-v'], 'pytest'), (['python3', '-m', 'unittest', '-v'], 'unittest'),
                              (['ng', 'test', '--no-watch'], 'angular'), ('mvn -q test', 'maven'), (['./gradlew', 'test'], 'gradle'),
                              (['cargo', 'nextest', 'run'], 'cargo'), (['make', 'test'], 'unknown')):
            self.assertEqual(test_run.detect_tool(command), tool, command)

    def test_run_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp, checkpoint=None)
            self.assertIn('no run', test_run.resolve_run(root, None)[1])
            (root / '.skillsync/runs/A').mkdir(parents=True)
            self.assertEqual(test_run.resolve_run(root, None), ('A', None))
            (root / '.skillsync/runs/B').mkdir()
            self.assertIn('2 runs', test_run.resolve_run(root, None)[1])
            self.assertEqual(test_run.resolve_run(root, 'B'), ('B', None))
            with self.assertRaises(ValueError):
                test_run.resolve_run(root, '../x')

    def test_step_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp)
            self.assertEqual(test_run.resolve_step(root, 'R1', 'F9.9'), ('F9.9', 'flag'))
            self.assertEqual(test_run.resolve_step(root, 'R1', None), ('F1.1', 'checkpoint'))
            cp = root / '.skillsync/runs/R1/checkpoints.md'
            cp.write_text('- 10:00 w toku: F1.1\n- 10:30 zrobione: F1.1; w toku: F1.2, F1.3\n', encoding='utf-8')
            self.assertEqual(test_run.resolve_step(root, 'R1', None), (None, 'ambiguous'))   # parallel steps: the caller must say which
            cp.write_text('- 10:00 start\n', encoding='utf-8')
            self.assertEqual(test_run.resolve_step(root, 'R1', None), (None, 'none'))
            with self.assertRaises(ValueError):
                test_run.resolve_step(root, 'R1', 'a b')


class Command(Base):
    def test_exit_code_output_and_events_of_a_failing_command(self):
        done = self.run_cli('--step', 'F2.3', '--', sys.executable, '-c', "import sys; print('out-line'); print('err-line', file=sys.stderr); sys.exit(3)")
        self.assertEqual(done.returncode, 3)
        self.assertIn('out-line', done.stdout)
        self.assertIn('err-line', done.stderr)
        self.assertIn('step F2.3 (flag), attempt 1: FAIL', done.stderr)
        state = self.attempt('F2.3')
        self.assertEqual((state.state, state.end['exit']), ('failed', 3))
        self.assertEqual(state.start['stepSource'], 'flag')

    def test_step_from_the_checkpoint_and_attempts_increase(self):
        for expected in (1, 2):
            done = self.run_cli('--', sys.executable, '-c', PASSING)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(self.attempt('F1.1', expected).state, 'passed')

    def test_no_step_goes_to_none(self):
        shutil.rmtree(self.root / '.skillsync/runs/R1')
        (self.root / '.skillsync/runs/R1').mkdir()
        done = self.run_cli('--', sys.executable, '-c', 'pass')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue((self.root / '.skillsync/runs/R1/tests/_none/1.jsonl').is_file())

    def test_junit_report_gives_the_list_of_tests(self):
        script = f"open('report.xml', 'w').write({JUNIT!r})"
        done = self.run_cli('--step', 'F1.1', '--junit', 'report.xml', '--', sys.executable, '-c', script)
        self.assertEqual(done.returncode, 0, done.stderr)
        state = self.attempt()
        self.assertEqual({t['id']: t['state'] for t in state.tests.values()}, {'pkg.A::ok': 'passed', 'pkg.A::bad': 'failed', 'pkg.B::skip': 'skipped'})
        self.assertEqual((state.counts['total'], state.counts['passed'], state.counts['failed'], state.counts['skipped']), (3, 1, 1, 1))
        self.assertEqual(state.end['source'], 'junit')
        self.assertIn('1 != 2', state.tests['pkg.A::bad']['msg'])

    def test_a_stale_report_is_ignored(self):
        report = self.root / 'old.xml'
        report.write_text(JUNIT, encoding='utf-8')
        os.utime(report, (1_000_000, 1_000_000))
        done = self.run_cli('--step', 'F1.1', '--junit', 'old.xml', '--', sys.executable, '-c', 'pass')
        self.assertEqual(done.returncode, 0)
        self.assertEqual(self.attempt().tests, {})

    def test_karma_counters_are_live_and_end_has_the_counts(self):
        script = "print('Chrome: Executed 3 of 10 (1 FAILED)'); print('Chrome: Executed 10 of 10 (2 FAILED)')"
        done = self.run_cli('--step', 'F1.1', '--', sys.executable, '-c', script)
        self.assertEqual(done.returncode, 0)
        events = ev.read_events(self.root / '.skillsync/runs/R1/tests/F1.1/1.jsonl')[0]
        counts = [e for e in events if e['t'] == 'counts']
        self.assertTrue(counts)
        self.assertEqual((counts[-1]['failed'], counts[-1]['total']), (2, 10))

    def test_missing_command_and_missing_run(self):
        done = self.run_cli('--step', 'F1.1', '--', 'definitely-not-a-command-xyz')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('BLOCKED', done.stderr)
        self.assertEqual(self.run_cli('--step', 'F1.1').returncode, 2)   # no command
        other = project(tempfile.mkdtemp(prefix='test-run-b-'), checkpoint=None)
        self.addCleanup(shutil.rmtree, other.parent, ignore_errors=True)
        done = self.run_cli('--', sys.executable, '-c', 'pass', root=other)
        self.assertEqual(done.returncode, 2)
        self.assertIn('--run is required', done.stderr)

    def test_timeout_is_124_and_the_attempt_is_closed(self):
        done = self.run_cli('--step', 'F1.1', '--timeout', '1', '--', sys.executable, '-c', 'import time; time.sleep(30)')
        self.assertEqual(done.returncode, 124)
        self.assertEqual(self.attempt().state, 'interrupted')

    def test_the_command_line_is_masked_in_the_events(self):
        secret = '-'.join(['zq9x', 'wv8y', 'ut7s', 'rp6q'])
        env = {**self.env, 'DEPLOY_TOKEN': secret}
        subprocess.run([sys.executable, str(CLI), 'test-run', str(self.root), '--step', 'F1.1', '--', sys.executable, '-c', f'pass  # {secret}'],
                       capture_output=True, text=True, env=env, timeout=60)
        text = (self.root / '.skillsync/runs/R1/tests/F1.1/1.jsonl').read_text(encoding='utf-8')
        self.assertNotIn(secret, text)

    def test_mask_setting_masks_the_results_in_the_events(self):
        secret = '-'.join(['zq9x', 'wv8y', 'ut7s', 'rp6q'])
        report = JUNIT.replace('1 != 2', 'token leaked ' + secret)
        script = f"open('r.xml', 'w').write({report!r})"
        env = {**self.env, 'DEPLOY_TOKEN': secret}
        run = lambda: subprocess.run([sys.executable, str(CLI), 'test-run', str(self.root), '--step', 'F1.1', '--junit', 'r.xml', '--', sys.executable, '-c', script],
                                     capture_output=True, text=True, env=env, timeout=60)
        run()
        self.assertIn(secret, (self.root / '.skillsync/runs/R1/tests/F1.1/1.jsonl').read_text(encoding='utf-8'))   # default: off, stored as it is
        (self.root / '.github/.skillsync.yaml').write_text('schema: 1\ntests:\n  mask: true\n', encoding='utf-8')
        run()
        self.assertNotIn(secret, (self.root / '.skillsync/runs/R1/tests/F1.1/2.jsonl').read_text(encoding='utf-8'))


class RunnerObserver(unittest.TestCase):
    def test_without_an_observer_nothing_changes_and_with_one_the_result_is_the_same(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp)
            check = {'id': 'c', 'argv': [sys.executable, '-c', "print('Ran 2 tests in 0.1s'); print('OK')"], 'cwd': str(root), 'kind': 'test', 'timeout': 30}
            plain = quality.run_validation(root, [dict(check)])
            seen = []

            class Obs:
                def started(self, c): seen.append('started')
                def line(self, c, stream, text): seen.append((stream, text))
                def finished(self, c, result, output): seen.append('finished')
            watched = quality.run_validation(root, [dict(check)], observer=Obs())
            for report in (plain, watched):
                for item in report['results']:
                    item.pop('duration_seconds', None)
                    item.pop('log', None)
            self.assertEqual(plain, watched)
            self.assertEqual(seen[0], 'started')
            self.assertEqual(seen[-1], 'finished')
            self.assertIn(('out', 'Ran 2 tests in 0.1s'), seen)

    def test_a_large_output_on_both_pipes_does_not_deadlock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp)
            # the child writes BYTES with an explicit `\n`: text mode would turn each `\n` into `\r\n` on Windows and the byte count below would not be the
            # same on every system (the point of the test is that nothing is lost and nothing blocks, with exact counts)
            script = ("import sys\nout, err = sys.stdout.buffer, sys.stderr.buffer\nfor i in range(20000):\n    out.write(b'o' * 100 + b'\\n'); err.write(b'e' * 100 + b'\\n')\n"
                      "out.write(b'Ran 1 test\\n')")
            check = {'id': 'big', 'argv': [sys.executable, '-c', script], 'cwd': str(root), 'kind': 'build', 'timeout': 60}
            lines = []

            class Obs:
                def started(self, c): pass
                def line(self, c, stream, text): lines.append(stream)
                def finished(self, c, result, output): self.out = output
            obs = Obs()
            report = quality.run_validation(root, [check], observer=obs)
            self.assertEqual(report['results'][0]['exit_code'], 0)
            self.assertEqual(len(lines), 40001)
            self.assertEqual(len(obs.out.encode('utf-8')), 20000 * 101 * 2 + len('Ran 1 test\n') + 1)

    def test_an_observer_that_fails_does_not_break_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp)
            check = {'id': 'x', 'argv': [sys.executable, '-c', "print('a')"], 'cwd': str(root), 'kind': 'build', 'timeout': 30}

            class Obs:
                def started(self, c): pass
                def line(self, c, stream, text): raise RuntimeError('boom')
                def finished(self, c, result, output): pass
            self.assertEqual(quality.run_validation(root, [check], observer=Obs())['results'][0]['exit_code'], 0)


    def test_the_unfinished_line_is_handed_over_while_the_command_runs(self):
        """The start of a test: `pytest -v` prints the id with no line end, the result comes later."""
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp)
            script = "import sys, time\nsys.stdout.write('test_a '); sys.stdout.flush(); time.sleep(1.0)\nsys.stdout.write('PASSED\\n'); sys.stdout.flush()\nsys.stderr.write('test_b ... '); sys.stderr.flush(); time.sleep(0.5)\nsys.stderr.write('ok\\r\\n')"
            check = {'id': 'p', 'argv': [sys.executable, '-c', script], 'cwd': str(root), 'kind': 'build', 'timeout': 30}
            seen = []

            class Obs:
                def started(self, c): pass
                def line(self, c, stream, text): seen.append(('line', stream, text, time.monotonic()))
                def partial(self, c, stream, text): seen.append(('partial', stream, text, time.monotonic()))
                def finished(self, c, result, output): self.out = output
            obs = Obs()
            quality.run_validation(root, [check], observer=obs)
            kinds = [(k, st, t) for k, st, t, _ in seen]
            self.assertIn(('partial', 'out', 'test_a '), kinds)
            self.assertIn(('line', 'out', 'test_a PASSED'), kinds)
            self.assertIn(('partial', 'err', 'test_b ... '), kinds)
            self.assertIn(('line', 'err', 'test_b ... ok'), kinds)   # a carriage return before the line feed is not part of the line
            start = next(t for k, st, x, t in seen if k == 'partial' and x == 'test_a ')
            end = next(t for k, st, x, t in seen if k == 'line' and x == 'test_a PASSED')
            self.assertGreater(end - start, 0.6)   # the start was seen about a second BEFORE the result
            self.assertEqual(obs.out.encode('utf-8').count(b'test_a PASSED'), 1)   # the bytes of the result are unchanged

    def test_multibyte_characters_split_between_chunks_and_a_huge_line_are_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp)
            script = "import sys, time\nb = 'zażółć'.encode('utf-8')\nsys.stdout.buffer.write(b[:3]); sys.stdout.buffer.flush(); time.sleep(0.3)\nsys.stdout.buffer.write(b[3:] + b'\\n'); sys.stdout.buffer.flush()\nsys.stdout.write('x' * 200000 + '\\n'); sys.stdout.flush()"
            check = {'id': 'p', 'argv': [sys.executable, '-c', script], 'cwd': str(root), 'kind': 'build', 'timeout': 30}
            lines = []

            class Obs:
                def started(self, c): pass
                def line(self, c, stream, text): lines.append(text)
                def finished(self, c, result, output): pass
            quality.run_validation(root, [check], observer=Obs())
            self.assertEqual(lines[0], 'zażółć')
            self.assertEqual(sum(len(x) for x in lines[1:]), 200000)   # cut into pieces, nothing lost


class Evidence(Base):
    """A2: `PASS` only with a known positive number of executed tests (the rule of `run_validation`)."""

    def status(self, *args):
        done = self.run_cli('--step', 'F1.1', *args)
        return done, self.attempt().end

    def test_a_command_without_tests_is_not_a_pass(self):
        for script, reason in (('pass', 'Test count unknown'), ("print('Ran 0 tests in 0.0s'); print('OK')", 'Zero executed tests'),
                               ("print('Ran 3 tests in 0.0s'); print('OK (skipped=1)')", 'Skipped tests require explicit disposition')):
            with self.subTest(script=script):
                done = self.run_cli('--step', 'F1.1', '--', sys.executable, '-c', script)
                self.assertEqual(done.returncode, 0)   # the exit code stays the one of the command
                self.assertIn('BLOCKED', done.stderr)
                end = ev.read_attempt(sorted((self.root / '.skillsync/runs/R1/tests/F1.1').glob('*.jsonl'), key=lambda p: int(p.stem))[-1]).end
                self.assertEqual(end['status'], 'BLOCKED')
                self.assertIn(reason, end['reason'])

    def test_true_is_not_a_pass(self):
        done = self.run_cli('--step', 'F1.1', '--', sys.executable, '-c', 'raise SystemExit(0)')
        self.assertIn('Test count unknown', ''.join(open(self.root / '.skillsync/runs/R1/tests/F1.1/1.jsonl', encoding='utf-8')))

    def test_explicit_exceptions_are_recorded(self):
        done = self.run_cli('--step', 'F1.1', '--allow-no-count', 'a tool without a summary', '--', sys.executable, '-c', 'pass')
        state = self.attempt()
        self.assertEqual(state.end['status'], 'PASS')
        self.assertEqual(state.start['allowNoCount'], 'a tool without a summary')
        self.run_cli('--step', 'F1.1', '--allow-skips', '--', sys.executable, '-c', "print('Ran 3 tests in 0.0s'); print('OK (skipped=1)')")
        state = self.attempt(n=2)
        self.assertEqual(state.end['status'], 'PASS')
        self.assertTrue(state.start['allowSkips'])

    def test_a_report_with_failures_and_exit_zero_is_not_a_pass(self):
        script = f"open('r.xml', 'w').write({JUNIT!r})"
        self.run_cli('--step', 'F1.1', '--junit', 'r.xml', '--', sys.executable, '-c', script)
        end = self.attempt().end
        self.assertEqual(end['status'], 'BLOCKED')
        self.assertIn('failed tests', end['reason'])

    def test_several_steps_in_progress_need_an_explicit_step(self):
        (self.root / '.skillsync/runs/R1/checkpoints.md').write_text('- 10:00 w toku: F1.1, F1.2\n', encoding='utf-8')
        done = self.run_cli('--', sys.executable, '-c', PASSING)
        self.assertEqual(done.returncode, 2)
        self.assertIn('pass --step', done.stderr)
        self.assertEqual(self.run_cli('--step', 'F1.2', '--', sys.executable, '-c', PASSING).returncode, 0)

    def test_the_command_runs_unbuffered(self):
        script = "import os; print('Ran 1 test in 0s'); print('OK'); open('flag', 'w').write(os.environ.get('PYTHONUNBUFFERED', ''))"
        self.run_cli('--step', 'F1.1', '--', sys.executable, '-c', script)
        self.assertEqual((self.root / 'flag').read_text(), '1')

class Hardening(Base):
    def test_counts_from_the_summary_fill_passed_and_failed(self):
        self.run_cli('--step', 'F1.1', '--', sys.executable, '-c', "print('Ran 5 tests in 0.1s'); print('OK')")
        self.assertEqual((self.attempt().counts['passed'], self.attempt().counts['failed']), (5, 0))
        self.run_cli('--step', 'F1.1', '--', sys.executable, '-c', "import sys; print('Ran 5 tests in 0.1s'); print('FAILED (failures=2, errors=1)'); sys.exit(1)")
        counts = self.attempt(n=2).counts
        self.assertEqual((counts['passed'], counts['failed'], counts['total']), (2, 3, 5))

    def test_the_recorder_survives_a_failing_fingerprint(self):
        from unittest import mock
        recorder = test_run.TestRecorder(self.root, 'R1', 'F1.1', 'flag')
        check = {'id': 'c', 'argv': [sys.executable, '-c', "print('Ran 2 tests in 0s'); print('OK')"], 'cwd': str(self.root), 'kind': 'build', 'timeout': 30}
        with mock.patch.object(test_run.test_gate, 'work_tree_id', side_effect=OSError('git is gone')):
            report = quality.run_validation(self.root, [check], observer=recorder)
        self.assertEqual(report['results'][0]['exit_code'], 0)
        self.assertEqual(self.attempt().end['status'], 'PASS')
        self.assertIsNone(self.attempt().end.get('tree'))

    def test_two_steps_may_run_at_the_same_time(self):
        import threading
        results = []
        def go(step):
            results.append(self.run_cli('--step', step, '--', sys.executable, '-c', "import time; print('Ran 1 test in 0s'); print('OK'); time.sleep(1.5)").returncode)
        threads = [threading.Thread(target=go, args=(s,)) for s in ('F1.1', 'F1.2')]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results, [0, 0])


if __name__ == '__main__':
    unittest.main()
