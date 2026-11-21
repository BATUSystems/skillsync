"""180-A, review of stage 1: a bad file never costs the page, the command line is masked, a clean exit that hides failures is not a PASS, SIGTERM ends
the run, the echo cannot cost the record, and the gate compares the tree at the start and at the end."""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tmp_paths import child  # noqa: E402
from skillsync_workflow import dashboard_tests as dt, test_events as ev, test_gate, test_run  # noqa: E402
from test_skillsync_test_gate import PLAN  # noqa: E402
from test_skillsync_test_run import Base, PASSING  # noqa: E402
from test_skillsync_workflow import WorkflowFixture  # noqa: E402
import sys as _sys_tmp_paths  # noqa: E402
from pathlib import Path as _Path_tmp_paths  # noqa: E402
_sys_tmp_paths.path.insert(0, str(_Path_tmp_paths(__file__).resolve().parent))  # noqa: E402
from tmp_paths import child, descend  # noqa: E402

CLI = ROOT / 'skillsync'


def start_event(**extra):
    return {'v': 1, 't': 'start', 'at': '2026-11-16T10:00:00Z', 'step': 'F1.1', 'run': 'R1', 'command': 'x', 'pid': os.getpid(), **extra}


class ReaderHardening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='reader-')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = Path(self.tmp) / '1.jsonl'

    def write(self, *lines):
        self.path.write_bytes(b''.join((line if isinstance(line, bytes) else line.encode('utf-8')) + b'\n' for line in lines))

    def read(self):
        return ev.read_events(self.path)

    def test_a_deeply_nested_line_is_skipped_not_a_traceback(self):
        self.write('{"v":1,"t":"test","x":' + '[' * 100_000 + ']' * 100_000 + '}', json.dumps(start_event()))
        events, _, skipped = self.read()
        self.assertEqual((len(events), skipped), (1, 1))

    def test_a_shallow_but_too_deep_value_is_skipped(self):
        self.write(json.dumps({'v': 1, 't': 'test', 'x': [[[[[[[[1]]]]]]]]}), json.dumps(start_event()))
        self.assertEqual(self.read()[2], 1)

    def test_nan_infinity_and_huge_numbers_are_skipped(self):
        self.write('{"v":1,"t":"counts","passed":NaN}', '{"v":1,"t":"counts","passed":Infinity}', '{"v":1,"t":"counts","passed":1e999}',
                   '{"v":1,"t":"test","id":"a","state":"passed","ms":99999999999999999999999}', json.dumps(start_event()))
        events, _, skipped = self.read()
        self.assertEqual((len(events), skipped), (1, 4))

    def test_a_lone_surrogate_becomes_a_replacement_character_and_the_state_can_be_serialized(self):
        self.write('{"v":1,"t":"test","id":"a","name":"x\\ud800y","state":"passed"}')
        events, _, skipped = self.read()
        self.assertEqual(skipped, 0)
        self.assertEqual(events[0]['name'], 'x�y')
        json.dumps(events, ensure_ascii=False).encode('utf-8')   # what the HTTP layer does

    def test_an_overlong_line_is_skipped_unparsed(self):
        self.write('{"v":1,"t":"test","id":"a","state":"passed","msg":"' + 'x' * (ev.MAX_READ_LINE + 10) + '"}', json.dumps(start_event()))
        events, _, skipped = self.read()
        self.assertEqual((len(events), skipped), (1, 1))

    def test_counters_of_a_wrong_kind_never_reach_a_sum(self):
        self.write(json.dumps(start_event()), '{"v":1,"t":"counts","passed":"3","failed":[1],"skipped":null,"running":true,"total":"9"}',
                   '{"v":1,"t":"test","id":"a","state":"passed","ms":"fast"}',
                   '{"v":1,"t":"end","status":"PASS","exit":"0","ms":-5,"counts":{"passed":"x","failed":2.5,"total":{}}}')
        state = ev.read_attempt(self.path)
        self.assertEqual(state.counts['passed'] + state.counts['failed'] + state.counts['skipped'] + state.counts['running'], 1)   # test `a` only
        self.assertIsNone(state.tests['a'].get('ms'))
        self.assertNotIn('exit', state.end)
        self.assertNotIn('ms', state.end)

    def test_the_start_and_end_keep_only_known_fields_of_the_right_kind(self):
        self.write(json.dumps({**start_event(), 'host': ['x'], 'pid': 'abc', 'command': {'a': 1}, 'unknown': 'field'}))
        state = ev.read_attempt(self.path)
        self.assertNotIn('host', state.start)
        self.assertNotIn('pid', state.start)
        self.assertNotIn('unknown', state.start)
        self.assertEqual(state.start['step'], 'F1.1')

    def test_a_replaced_file_is_read_from_the_start(self):
        self.write(json.dumps(start_event()), '{"v":1,"t":"test","id":"old","state":"failed"}')
        first = ev.read_attempt(self.path)
        self.assertIn('old', first.tests)
        replacement = Path(self.tmp) / 'other.jsonl'
        replacement.write_bytes((json.dumps(start_event()) + '\n' + '{"v":1,"t":"test","id":"new","state":"passed"}\n' + 'x' * 400 + '\n').encode())
        os.replace(replacement, self.path)   # another file (another inode), longer than the old one
        second = ev.read_attempt(self.path, first)
        self.assertEqual(set(second.tests), {'new'})
        self.assertIsNot(second, first)

    def test_a_shorter_file_is_read_from_the_start(self):
        self.write(json.dumps(start_event()), '{"v":1,"t":"test","id":"a","state":"passed"}', '{"v":1,"t":"test","id":"b","state":"passed"}')
        first = ev.read_attempt(self.path)
        self.write(json.dumps(start_event()))
        self.assertEqual(ev.read_attempt(self.path, first).tests, {})

    def test_the_import_validates_what_the_reader_validates(self):
        root = Path(self.tmp) / 'proj'
        (root / '.skillsync/runs/R1').mkdir(parents=True)
        source = Path(self.tmp) / 'foreign.jsonl'
        source.write_bytes((json.dumps({**start_event(), 'host': 'other-machine', 'startedAt': 'x'}) + '\n' + '{"v":1,"t":"end","status":"PASS","exit":0,"x":' + '[' * 50_000 + ']' * 50_000 + '}\n').encode())
        with self.assertRaises(ValueError):
            test_run.import_events(root, 'R1', 'F1.1', source, 'abcdef1')


class CollectedChunks(unittest.TestCase):
    def test_hundreds_of_long_names_are_split_into_lines_within_the_limit_and_none_is_lost(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '1.jsonl'
            path.touch()
            w = ev.EventWriter(path)
            names = [f'ns.{i:04d}.' + 'x' * 250 for i in range(600)]
            self.assertTrue(w.collected(600, names=names))
            w.close()
            raw = path.read_bytes().split(b'\n')
            self.assertGreater(len([line for line in raw if line]), 5)
            self.assertTrue(all(len(line) <= ev.MAX_LINE for line in raw))
            state = ev.read_attempt(path)
            self.assertEqual((state.collected_total, len(state.tests)), (600, 600))
            self.assertTrue(all(t['state'] == 'waiting' for t in state.tests.values()))

    def test_more_than_5000_names_set_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '1.jsonl'
            path.touch()
            w = ev.EventWriter(path)
            w.collected(5001, names=[f't{i}' for i in range(5001)])
            self.assertTrue(w.truncated)
            w.close()


class CommandMask(unittest.TestCase):
    def test_values_after_secret_flags_are_masked(self):
        for line, secret in (('tool --token SUPERSECRET run', 'SUPERSECRET'), ('tool --api-key=SUPERSECRET', 'SUPERSECRET'), ("tool --password 'a b SUPERSECRET'", 'SUPERSECRET'),
                             ('tool -auth SUPERSECRET x', 'SUPERSECRET'), ('tool --client-secret SUPERSECRET', 'SUPERSECRET'), ('mysql -u root -pSUPERSECRET db', 'SUPERSECRET'),
                             ('mysql -p SUPERSECRET db', 'SUPERSECRET'), ('sshpass -p SUPERSECRET ssh h', 'SUPERSECRET'),
                             ('curl -H "Authorization: Bearer SUPERSECRET" https://h', 'SUPERSECRET'), ('curl https://user:SUPERSECRET@h/x', 'SUPERSECRET')):
            with self.subTest(line=line):
                self.assertNotIn(secret, ev.clean_text(ev.mask_command(line), ev.MAX_COMMAND, mask=True))

    def test_what_is_not_secret_stays(self):
        for line in ('python -m unittest -v tests.test_a', 'pytest -p no:cacheprovider -q', 'tool --no-color --keep-going', 'dotnet test --filter Name=x'):
            self.assertEqual(ev.clean_text(ev.mask_command(line), ev.MAX_COMMAND, mask=True), line)

    def test_the_known_limits_hold(self):
        # the documented boundaries: a secret as a positional argument or after a short flag of another kind is NOT masked
        self.assertIn('tajne', ev.mask_command('tool -p tajne x'))
        self.assertIn('tajne', ev.mask_command('tool deploy tajne'))

    def test_the_start_event_masks_a_cut_value_as_well(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '1.jsonl'
            path.touch()
            w = ev.EventWriter(path)
            w.start(command='tool ' + 'a ' * 480 + '--token ' + 'SECRET' * 40)
            w.close()
            self.assertNotIn('SECRET', path.read_text(encoding='utf-8'))


class HiddenFailures(Base):
    def run_code(self, code):
        return self.run_cli('--step', 'F1.1', '--', sys.executable, '-c', code)

    def test_a_clean_exit_with_a_unittest_failure_summary_is_blocked(self):
        result = self.run_code("print('Ran 3 tests in 0.1s'); print(); print('FAILED (failures=2, errors=1)')")
        self.assertEqual(result.returncode, 2)   # exit 0 without proof (status BLOCKED) is exit code 2 (CORE-TESTDEBT K3)
        end = self.attempt().end
        self.assertEqual(end['status'], 'BLOCKED')
        self.assertIn('3 failed', end['reason'])
        self.assertIn('hide', end['reason'])

    def test_a_clean_exit_with_a_pytest_summary_is_blocked(self):
        for line in ('=========== 2 failed, 5 passed in 0.31s ===========', '1 failed, 2 passed in 0.1s', '1 failed in 0.1s'):
            with self.subTest(line=line):
                result = self.run_code(f"print('collected 8 items'); print('{line}'); print('Ran 8 tests in 1s')")
                self.assertEqual(result.returncode, 2)   # exit 0 without proof (status BLOCKED) is exit code 2 (CORE-TESTDEBT K3)
                self.assertEqual(self.latest().end['status'], 'BLOCKED')

    def test_green_runs_with_the_word_failed_in_a_log_stay_green(self):
        for line in ('0 failed', 'failed=0', '3 failed attempts to connect, retried ok', 'retry: 2 failed requests', '0 failed, 10 passed in 1.0s'):
            with self.subTest(line=line):
                self.run_code(f"print({line!r}); print('Ran 2 tests in 0.01s'); print('OK')")
                self.assertEqual(self.latest().end['status'], 'PASS', line)

    def run_output(self, text):
        return self.run_code(f"import sys; sys.stdout.write({text!r})")

    def test_a_clean_exit_with_a_playwright_vitest_or_jest_summary_is_blocked(self):
        cases = {
            'playwright': '\n  1 failed\n  5 passed (3s)\n',
            'playwright interrupted': '\n  2 interrupted\n  3 passed (3s)\n',
            'playwright crlf': '\r\n  1 failed\r\n  5 passed (3s)\r\n',
            'playwright colour': '\x1b[31m  1 failed\x1b[39m\n  5 passed (3s)\n',
            'vitest': ' Test Files  1 failed | 1 passed (2)\n      Tests  1 failed | 4 passed (5)\n',
            'vitest only failed': '      Tests  2 failed (2)\n',
            'jest': 'Test Suites: 1 failed, 1 total\nTests:       1 failed, 4 passed, 5 total\n',
            'jest crlf': 'Tests:       1 failed, 4 passed, 5 total\r\n',
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                result = self.run_output(text + 'Ran 8 tests in 1s\n')   # the counters are known, so only a hidden failure can block
                self.assertEqual(result.returncode, 2, result.stderr)   # exit 0 without proof (status BLOCKED) is exit code 2 (CORE-TESTDEBT K3)
                end = self.latest().end
                self.assertEqual(end['status'], 'BLOCKED')
                self.assertIn('hide', end['reason'])

    def test_a_log_that_only_looks_like_a_summary_of_another_tool_stays_green(self):
        cases = {
            'vitest files only': ' Test Files  1 failed | 1 passed (2)\n      Tests  5 passed (5)\n',
            'zero failed': '  0 failed\n  5 passed (3s)\n',
            'failed=0': 'failed=0\n  5 passed (3s)\n',
            'failed attempts': 'retry: 3 failed attempts to connect, retried ok\n  5 passed (3s)\n',
            'flaky': '  1 flaky\n  5 passed (3s)\n',
            'did not run': '  1 did not run\n  5 passed (3s)\n',
            'indented deeper': '    1 failed\n  5 passed (3s)\n',
            'jest suites only': 'Test Suites: 1 failed, 1 total\nTests:       5 passed, 5 total\n',
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                self.run_output(text + "Ran 2 tests in 0.01s\nOK\n")
                self.assertEqual(self.latest().end['status'], 'PASS', name)

    def test_the_summary_of_another_tool_counts_only_when_the_counters_come_from_the_output(self):
        line = '\n  1 failed\n  5 passed (3s)\n'
        self.assertEqual(test_run._hidden_failures(line), 1)
        self.assertEqual(test_run._hidden_failures(line, source='regex'), 1)
        self.assertEqual(test_run._hidden_failures(line, source='junit'), 0)
        self.assertEqual(test_run._hidden_failures(line, source='adapter'), 0)
        self.assertEqual(test_run._hidden_failures('FAILED (failures=2)\n', source='junit'), 2)   # the generic part stays for every source
        self.assertEqual(test_run._hidden_failures('=== 3 failed, 5 passed in 2.00s ===\n', source='adapter'), 3)

    def test_a_line_of_another_tool_beside_a_clean_report_or_adapter_is_not_a_block(self):
        check = {'argv': [sys.executable, '-c', 'pass'], 'cwd': str(self.root)}
        report = self.root / 'r.xml'
        report.write_text('<testsuite><testcase classname="a" name="b"/></testsuite>', encoding='utf-8')
        recorder = test_run.TestRecorder(self.root, 'R1', 'F1.3', 'flag')
        recorder.started(check)
        recorder.junit = [str(report)]
        recorder.finished(check, {'exit_code': 0, 'duration_seconds': 0.1}, '  1 failed\n  5 passed (3s)\n')
        end = ev.read_attempt(recorder.path).end
        self.assertEqual((end['status'], end.get('source')), ('PASS', 'junit'))
        result = self.run_cli('--step', 'F1.4', '--format', 'tap', '--', sys.executable, '-c',
                              "print('  1 failed'); print('  5 passed (3s)'); print('ok - a'); print('ok - b')")
        end = self.latest_of('F1.4').end
        self.assertEqual((result.returncode, end['status'], end.get('source')), (0, 'PASS', 'adapter'), result.stderr)

    def test_a_pipe_that_hides_the_exit_code_is_blocked(self):
        result = subprocess.run([sys.executable, str(CLI), 'test-run', str(self.root), '--step', 'F1.1', '--', 'sh', '-c',
                                 f"{sys.executable} -c \"print('Ran 2 tests in 1s'); print('FAILED (failures=1)'); raise SystemExit(1)\" | cat; true"],
                                capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(result.returncode, 2)   # exit 0 without proof (status BLOCKED) is exit code 2 (CORE-TESTDEBT K3)
        self.assertEqual(self.latest().end['status'], 'BLOCKED')

    def latest(self):
        return self.latest_of('F1.1')

    def latest_of(self, step):
        directory = child(self.root / '.skillsync/runs/R1/tests', step)
        return ev.read_attempt(sorted(directory.glob('*.jsonl'), key=lambda p: int(p.stem))[-1])


class Echo(unittest.TestCase):
    def recorder(self, adapter=None):
        recorder = test_run.TestRecorder('.', 'R1', 'F1.1', 'flag', passthrough=True)
        recorder.writer = mock.Mock()
        return recorder

    def test_a_console_that_cannot_encode_a_character_does_not_lose_the_line(self):
        class Narrow:
            encoding = 'ascii'
            seen = []

            def write(self, text):
                text.encode('ascii')
                self.seen.append(text)

            def flush(self):
                pass
        stdout = Narrow()
        recorder = self.recorder()
        with mock.patch.object(sys, 'stdout', stdout):
            recorder.line({}, 'out', 'Chrome zażółć (Linux): Executed 3 of 5 (skipped 1)')
        self.assertTrue(recorder.passthrough)
        self.assertTrue(any('Executed' in text for text in stdout.seen))
        recorder.writer.counts.assert_called_once()   # the counters saw the line

    def test_a_closed_pipe_switches_the_echo_off_and_the_record_goes_on(self):
        class Broken:
            def write(self, text):
                raise BrokenPipeError

            def flush(self):
                raise BrokenPipeError
        recorder = self.recorder()
        with mock.patch.object(sys, 'stdout', Broken()):
            recorder.line({}, 'out', 'Executed 1 of 2')
            recorder.line({}, 'out', 'Executed 2 of 2')
        self.assertFalse(recorder.passthrough)
        self.assertEqual(recorder.writer.counts.call_count, 2)

    def test_the_adapter_sees_the_line_before_the_echo_is_tried(self):
        order = []
        adapter = mock.Mock()
        adapter.feed.side_effect = lambda *a: order.append('adapter')
        recorder = test_run.TestRecorder('.', 'R1', 'F1.1', 'flag', passthrough=True, adapter=adapter)
        recorder.writer = mock.Mock()
        recorder._echo = lambda stream, text: order.append('echo')
        recorder.line({}, 'out', 'x')
        self.assertEqual(order, ['adapter', 'echo'])


class Durations(unittest.TestCase):
    def test_a_duration_that_overflows_is_zero_not_an_exception(self):
        for value in (float('inf'), float('nan'), 1e308, -1, 'x', None):
            self.assertEqual(test_run._ms(value), 0, value)
        self.assertEqual(test_run._ms(1.5), 1500)


@unittest.skipIf(os.name == 'nt', 'SIGTERM is POSIX; Windows uses CTRL_BREAK (see the docstring of _Interruptible)')
class Sigterm(Base):
    def test_sigterm_ends_the_command_writes_end_and_frees_the_lock(self):
        pid_file = Path(self.tmp) / 'child.pid'
        code = f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(120)"
        process = subprocess.Popen([sys.executable, str(CLI), 'test-run', str(self.root), '--step', 'F1.1', '--', sys.executable, '-c', code],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env)
        try:
            for _ in range(200):
                if pid_file.exists() and pid_file.read_text():
                    break
                time.sleep(0.05)
            child_pid = int(pid_file.read_text())
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=30)
        finally:
            if process.poll() is None:
                process.kill()
            process.stdout.close()
            process.stderr.close()
        self.assertEqual(process.returncode, 2)
        for _ in range(100):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            os.kill(child_pid, signal.SIGKILL)
            self.fail('the command of the run outlived test-run')
        end = self.attempt().end
        self.assertEqual(end['status'], 'BLOCKED')
        self.assertIn('Interrupted', end['reason'])
        again = self.run_cli('--step', 'F1.1', '--', sys.executable, '-c', PASSING)   # the step lock is free: a second run is not blocked
        self.assertEqual(again.returncode, 0, again.stderr)


class TreeAtStartAndEnd(WorkflowFixture):
    def setUp(self):
        super().setUp()
        self.write('docs/plans/2026-11-16-demo.md', PLAN)
        self.write('.skillsync/runs/R1/checkpoints.md', '- 10:00 start\n')
        self.write('.gitignore', '.skillsync/\n')
        self.write('code.txt', 'v1\n')
        self.init_git()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True, text=True).stdout.strip()

    def run_tests(self, code=PASSING, step='F1.2'):
        return subprocess.run([sys.executable, str(CLI), 'test-run', str(self.root), '--run', 'R1', '--step', step, '--', sys.executable, '-c', code],
                              capture_output=True, text=True, timeout=120)

    def events(self, step='F1.2', n=1):
        return f'.skillsync/runs/R1/tests/{step}/{n}.jsonl'

    def problem(self, n=1):
        return test_gate.evidence_problem(self.root, 'R1', 'F1.2', [self.events(n=n)])

    def test_the_start_event_records_the_tree_and_a_stable_tree_is_a_proof(self):
        self.write('code.txt', 'v2\n')
        self.run_tests()
        state = ev.read_attempt(descend(self.root, self.events()))
        self.assertEqual(state.start['treeStart'], state.end['tree'])
        self.assertIsNone(self.problem())

    def test_a_file_changed_while_the_tests_ran_is_not_a_proof(self):
        code = f"import pathlib; pathlib.Path({str(self.root / 'generated.txt')!r}).write_text('made by the tests'); {PASSING}"
        self.run_tests(code)
        problem = self.problem()
        self.assertIn('WHILE the tests ran', problem)
        self.assertIn('generated.txt', problem)   # the path that changed is named
        self.assertIn('.gitignore', problem)

    def test_a_run_without_a_start_tree_is_not_a_proof(self):
        self.run_tests()
        path = descend(self.root, self.events())
        lines = path.read_text(encoding='utf-8').splitlines()
        first = json.loads(lines[0])
        del first['treeStart']
        path.write_text('\n'.join([json.dumps(first)] + lines[1:]) + '\n', encoding='utf-8')
        self.assertIn('no tree at its start', self.problem())

    def test_an_older_green_run_does_not_outweigh_a_newer_red_one_on_the_same_tree(self):
        self.write('code.txt', 'v2\n')
        self.run_tests()
        self.assertIsNone(self.problem(1))
        self.run_tests("print('Ran 2 tests in 1s'); print('FAILED (failures=1)'); raise SystemExit(1)")
        self.assertEqual(ev.read_attempt(descend(self.root, self.events(n=2))).end['status'], 'FAIL')
        problem = self.problem(1)
        self.assertIn('NEWER run', problem)
        self.assertIn('attempt 2', problem)
        self.run_tests()   # a newer green one: attempt 3 is a proof, attempt 1 is not outweighed by a red run any more only through attempt 3
        self.assertIsNone(self.problem(3))

    def test_an_older_green_run_is_fine_when_the_newer_red_one_ran_on_other_content(self):
        self.write('code.txt', 'v2\n')
        self.run_tests()
        self.write('code.txt', 'v3\n')
        self.run_tests("print('Ran 2 tests in 1s'); print('FAILED (failures=1)'); raise SystemExit(1)")
        self.write('code.txt', 'v2\n')   # back to the content of attempt 1
        self.assertIsNone(self.problem(1))

    def test_an_imported_run_needs_a_clean_tree(self):
        self.run_tests()
        path = descend(self.root, self.events())
        head = self.git('rev-parse', 'HEAD')
        with open(path, 'a', encoding='utf-8') as handle:
            handle.write(json.dumps({'v': 1, 't': 'import', 'sourceSha': head, 'at': '2026-11-16T10:00:00Z'}) + '\n')
        self.assertIsNone(self.problem())
        self.write('code.txt', 'dirty\n')
        self.assertIn('uncommitted', self.problem())

    def test_the_id_sees_a_change_of_the_same_size_made_right_after_the_commit(self):
        for index in range(25):   # a write in the same clock tick as the index entry (racy git): the copy of the index must not hide it
            self.write('code.txt', f'a{index % 10}\n')
            self.git('add', '-A')
            self.git('commit', '-qm', f'c{index}', '--allow-empty')
            self.write('code.txt', f'b{index % 10}\n')
            self.assertNotEqual(test_gate.work_tree_id(self.root), test_gate.work_tree_id(self.root, from_head=True), index)

    def test_the_run_of_a_plan_must_be_a_valid_run_id(self):
        self.assertEqual(test_gate.run_of_plan('Przebieg: 180-A\n'), '180-A')
        for bad in ('../x', '/etc/x', 'a/b', '..', 'CON', 'a b'):
            self.assertIsNone(test_gate.run_of_plan(f'Przebieg: {bad}\n'), bad)
        self.assertEqual(test_gate.plan_findings(self.root), [])
        self.write('docs/plans/evil.md', 'Przebieg: ../../x\n\n- [x] F1.2 [T] Testy\n')
        self.assertEqual(test_gate.plan_findings(self.root), [])


class StatusSurvivesBadFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='bad-files-')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = Path(self.tmp)
        self.base = self.root / '.skillsync/runs/R1/tests'
        dt._CACHE.clear()

    def good(self, step='F1.1'):
        directory = child(self.base, step)
        directory.mkdir(parents=True)
        path = directory / '1.jsonl'
        path.touch()
        w = ev.EventWriter(path)
        w._write({'t': 'start', 'at': '2026-11-16T10:00:00Z', 'step': step, 'command': 'x', 'pid': os.getpid()})
        w.test('g::a', 'a', 'passed', group='g')
        w.end('PASS', 0, counts={'total': 1, 'passed': 1, 'failed': 0, 'skipped': 0})
        w.close()

    def test_directories_with_names_outside_the_step_pattern_are_skipped_and_named(self):
        self.good()
        for name in ('.tmp', 'F1.1 (kopia)', 'CON', 'x' * 250, 'ä-ö'):
            (self.base / name).mkdir()
            (self.base / name / '1.jsonl').write_text('{}\n', encoding='utf-8')
        tests, steps = dt.build_tests(self.root, 'R1')
        self.assertEqual([s['step']['id'] for s in tests['sections']], ['F1.1'])
        self.assertGreaterEqual(len(tests['skipped']), 4)
        self.assertTrue(all(set(item) == {'path', 'reason'} for item in tests['skipped']))

    def test_an_unreadable_or_hostile_file_costs_only_itself(self):
        self.good('F1.1')
        directory = self.base / 'F1.2'
        directory.mkdir()
        (directory / '1.jsonl').write_bytes(b'{"v":1,"t":"start","x":' + b'[' * 200_000 + b'\n' + b'\xff\xfe\x00garbage\n')
        status = {'progress': {'phases': []}}
        dt.add_to_status(status, self.root, 'R1', [])
        self.assertIn('tests', status)
        self.assertEqual([s['step']['id'] for s in status['tests']['sections']], ['F1.1'])

    def test_any_exception_in_a_file_is_a_skip(self):
        self.good('F1.1')
        self.good('F1.2')
        original = dt._summary

        def boom(path, state):
            if Path(path).parent.name == 'F1.2':   # not a `/F1.2/` substring: the separator is `\\` on Windows
                raise TypeError('str + int')
            return original(path, state)
        with mock.patch.object(dt, '_summary', boom):
            tests, _ = dt.build_tests(self.root, 'R1')
        self.assertEqual([s['step']['id'] for s in tests['sections']], ['F1.1'])
        self.assertEqual(tests['skipped'][0]['reason'], 'unreadable')

    def test_a_failure_of_the_whole_collection_leaves_the_status_standing(self):
        status = {'progress': {'phases': []}}
        with mock.patch.object(dt, 'build_tests', side_effect=RuntimeError('boom')):
            dt.add_to_status(status, self.root, 'R1', [])
        self.assertEqual(status['tests']['skipped'], [{'path': 'tests', 'reason': 'unreadable'}])

    def test_the_answer_with_skipped_entries_passes_the_mask_and_is_valid_json(self):
        self.good()
        (self.base / 'CON').mkdir()
        (self.base / 'CON' / '1.jsonl').write_text('{}\n', encoding='utf-8')
        status = {'progress': {'phases': []}}
        dt.add_to_status(status, self.root, 'R1', [])
        json.dumps(dt.finalize_status(status), allow_nan=False)

    def test_a_directory_of_events_that_is_a_symlink_is_not_followed(self):
        self.good()
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / '1.jsonl').write_text('{}\n', encoding='utf-8')
        os.symlink(outside, self.base / 'F9.9')
        tests, _ = dt.build_tests(self.root, 'R1')
        self.assertEqual([s['step']['id'] for s in tests['sections']], ['F1.1'])

    def test_an_invalid_step_is_a_value_error_and_a_missing_attempt_is_none(self):
        self.good()
        with self.assertRaises(ValueError):
            dt.tests_response(self.root, 'R1', 'x' * 400, 1)   # an invalid step id: the caller answers 400
        self.assertIsNone(dt.tests_response(self.root, 'R1', 'F1.1', 999999))   # a missing attempt: 404

    def test_a_path_the_file_system_cannot_hold_is_not_found_instead_of_an_error(self):
        self.good()
        with mock.patch.object(Path, 'is_file', side_effect=OSError(36, 'File name too long')):
            self.assertIsNone(dt.tests_response(self.root, 'R1', 'F1.1', 1))

    def test_a_linked_step_directory_is_not_found(self):
        self.good()
        os.symlink(self.base / 'F1.1', self.base / 'F2.2')
        self.assertIsNone(dt.tests_response(self.root, 'R1', 'F2.2', 1))


class ReviewRound2(WorkflowFixture):
    """The second review of stage 1: ids from an events file never reach git, an interrupt cannot cost the `end`, the racy-git window, odd values."""

    def setUp(self):
        super().setUp()
        self.write('docs/plans/2026-11-16-demo.md', PLAN)
        self.write('.skillsync/runs/R1/checkpoints.md', '- 10:00 start\n')
        self.write('.gitignore', '.skillsync/\n')
        self.write('code.txt', 'v1\n')
        self.init_git()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True, text=True).stdout.strip()

    def test_a_tree_id_from_an_events_file_that_is_an_option_never_reaches_git(self):
        victim = self.root.parent / 'victim-output.txt'
        victim.write_text('keep me', encoding='utf-8')
        path = self.root / '.skillsync/runs/R1/tests/F1.2/1.jsonl'
        path.parent.mkdir(parents=True)
        lines = [{'v': 1, 't': 'start', 'at': '2026-11-16T10:00:00Z', 'run': 'R1', 'step': 'F1.2', 'stepSource': 'flag', 'treeStart': f'--output={victim}'},
                 {'v': 1, 't': 'end', 'at': '2026-11-16T10:00:01Z', 'status': 'PASS', 'exit': 0, 'counts': {'total': 2, 'passed': 2, 'failed': 0, 'skipped': 0},
                  'tree': 'abc'}]
        path.write_text('\n'.join(json.dumps(line) for line in lines) + '\n', encoding='utf-8')
        problem = test_gate.evidence_problem(self.root, 'R1', 'F1.2', ['.skillsync/runs/R1/tests/F1.2/1.jsonl'])
        self.assertIn('no tree at its start', problem)
        self.assertEqual(victim.read_text(encoding='utf-8'), 'keep me')
        with mock.patch.object(test_gate.subprocess, 'run') as run:   # CORE-TESTDEBT2: the guarantee stays, the hostile ids never reach any git command
            self.assertEqual(test_gate.changed_paths(self.root, f'--output={victim}', 'abc', self.root), [])
            self.assertEqual(test_gate.changed_paths(self.root, 'abc', f'--output={victim}', self.root), [])
            run.assert_not_called()
        self.assertEqual(victim.read_text(encoding='utf-8'), 'keep me')
        state = ev.read_attempt(path)
        self.assertNotIn('treeStart', state.start)
        self.assertNotIn('tree', state.end)

    def test_a_change_of_the_same_size_is_seen_even_when_the_index_is_older_than_two_seconds(self):
        self.write('code.txt', 'a1\n')
        self.git('add', '-A')
        self.git('commit', '-qm', 'a')
        self.git('status', '--porcelain')   # refreshes the real index
        self.write('code.txt', 'b1\n')   # the same size, written within the clock tick of the index entry
        time.sleep(3)
        self.assertNotEqual(test_gate.work_tree_id(self.root), test_gate.work_tree_id(self.root, from_head=True))

    def test_an_interrupt_during_started_or_finished_still_leaves_an_end(self):
        calls = []

        class Observer:
            def started(self, check):
                calls.append('started')
                raise KeyboardInterrupt

            def finished(self, check, result, output):
                calls.append('finished')
        check = {'id': 'c', 'argv': [sys.executable, '-c', 'pass'], 'cwd': str(self.root), 'kind': 'build', 'timeout': 30}
        quality_report = __import__('skillsync_workflow.quality', fromlist=['run_validation']).run_validation(self.root, [check], observer=Observer())
        self.assertEqual(calls, ['started', 'finished'])
        self.assertTrue(quality_report['interrupted'])

    def test_an_interrupt_while_the_end_is_recorded_still_writes_end(self):
        recorder = test_run.TestRecorder(self.root, 'R1', 'F1.2', 'flag')
        recorder.started({'argv': [sys.executable, '-c', 'pass'], 'cwd': str(self.root)})
        with mock.patch.object(recorder, '_finish', side_effect=KeyboardInterrupt):
            recorder.finished({}, {'exit_code': 0, 'duration_seconds': 0.1}, 'output')
        state = ev.read_attempt(recorder.path)
        self.assertEqual(state.end['status'], 'BLOCKED')
        self.assertIn('Interrupted', state.end['reason'])

    def test_a_pid_that_cannot_be_one_is_not_alive(self):
        self.assertFalse(ev.process_state(10 ** 12))
        self.assertFalse(ev.is_alive(2 ** 40))

    def test_a_live_pid_is_believed_for_a_day_of_silence_only(self):
        path = self.root / '.skillsync/runs/R1/tests/F1.1/1.jsonl'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'v': 1, 't': 'start', 'at': '2026-11-16T10:00:00Z', 'step': 'F1.1', 'pid': os.getpid(), 'host': socket_name()}) + '\n', encoding='utf-8')
        state = ev.read_attempt(path)
        self.assertEqual(dt._effective_state(state, path), 'running')
        old = time.time() - dt.STALE_ALIVE_S - 60
        os.utime(path, (old, old))
        self.assertEqual(dt._effective_state(state, path), 'interrupted')

    def test_a_negative_exit_code_of_a_killed_process_is_kept(self):
        path = self.root / 'x.jsonl'
        path.write_text(json.dumps({'v': 1, 't': 'start', 'at': 'x'}) + '\n' + json.dumps({'v': 1, 't': 'end', 'status': 'FAIL', 'exit': -9}) + '\n', encoding='utf-8')
        self.assertEqual(ev.read_attempt(path).end['exit'], -9)

    def test_a_report_with_a_huge_duration_does_not_lose_the_test(self):
        path = self.root / 'y.jsonl'
        path.touch()
        w = ev.EventWriter(path)
        w.test('a', 'a', 'failed', ms=1e300, msg='boom')
        w.test('b', 'b', 'passed', ms=float('nan'))
        w.close()
        state = ev.read_attempt(path)
        self.assertEqual((state.tests['a']['state'], state.tests['b']['state']), ('failed', 'passed'))
        self.assertIsNone(state.tests['a'].get('ms'))

    def test_the_mask_is_not_beaten_by_invisible_characters_exe_names_or_odd_arguments(self):
        w_path = self.root / 'z.jsonl'
        w_path.touch()
        w = ev.EventWriter(w_path)
        w.start(command='tool --token\u200b SUPERSECRET run mysql.exe')
        w.start(command='mysql.exe -pSUPERSECRET db')
        w.start(command='tool ' + '\udcff' + ' x')   # an argument with `surrogateescape`
        w.close()
        text = w_path.read_text(encoding='utf-8')
        self.assertNotIn('SUPERSECRET', text)
        self.assertEqual(len([line for line in text.splitlines() if line]), 3)

    def test_the_summary_in_colour_and_a_quadratic_looking_output(self):
        self.assertEqual(test_run._hidden_failures('\x1b[31m=========== 2 failed, 5 passed in 0.31s ===========\x1b[0m\n'), 2)
        started = time.time()
        test_run._hidden_failures('FAILED (' * 20000)
        self.assertLess(time.time() - started, 2.0)

    def test_the_message_names_the_output_when_a_clean_report_hides_nothing_but_the_output_does(self):
        # a JUnit report with no failure and a failure summary in the output: the reason names the OUTPUT, not the report
        recorder = test_run.TestRecorder(self.root, 'R1', 'F1.2', 'flag')
        check = {'argv': [sys.executable, '-c', 'pass'], 'cwd': str(self.root)}
        recorder.started(check)
        report = self.root / 'r.xml'
        report.write_text('<testsuite><testcase classname="a" name="b"/></testsuite>', encoding='utf-8')
        recorder.junit = [str(report)]
        recorder.finished(check, {'exit_code': 0, 'duration_seconds': 0.1}, 'FAILED (failures=1)\n')
        self.assertIn('the output reports', ev.read_attempt(recorder.path).end['reason'])

    def test_a_bool_or_a_float_is_not_the_event_version(self):
        path = self.root / 'v.jsonl'
        path.write_text('{"v":true,"t":"start"}\n{"v":1.0,"t":"start"}\n{"v":1,"t":"start"}\n', encoding='utf-8')
        events, _, skipped = ev.read_events(path)
        self.assertEqual((len(events), skipped), (1, 2))


def socket_name():
    import socket
    return socket.gethostname()


if __name__ == '__main__':
    unittest.main()


class ChangedPathsField(WorkflowFixture):
    """CORE-TESTDEBT2 Q6: the paths that changed while the tests ran come from `end.changed` (written at the end of the run), not from a git call of the gate."""

    def setUp(self):
        super().setUp()
        self.write('docs/plans/2026-11-16-demo.md', PLAN)
        self.write('.skillsync/runs/R1/checkpoints.md', '- 10:00 start\n')
        self.write('.gitignore', '.skillsync/\n')
        self.write('code.txt', 'v1\n')
        self.init_git()

    def run_tests(self, code=PASSING, step='F1.2'):
        return subprocess.run([sys.executable, str(CLI), 'test-run', str(self.root), '--run', 'R1', '--step', step, '--', sys.executable, '-c', code],
                              capture_output=True, text=True, timeout=120)

    def events(self, n=1):
        return f'.skillsync/runs/R1/tests/F1.2/{n}.jsonl'

    def count(self):
        out = subprocess.run(['git', '-C', str(self.root), 'count-objects', '-v'], check=True, capture_output=True, text=True).stdout
        return int(out.split('count:')[1].split()[0])

    def test_a_run_that_changed_the_tree_records_the_paths_and_the_gate_names_them(self):
        code = f"import pathlib; pathlib.Path({str(self.root / 'generated.txt')!r}).write_text('made by the tests'); {PASSING}"
        self.run_tests(code)
        state = ev.read_attempt(descend(self.root, self.events()))
        self.assertEqual(state.end['changed'], ['generated.txt'])
        problem = test_gate.evidence_problem(self.root, 'R1', 'F1.2', [self.events()])
        self.assertIn('changed: generated.txt', problem)

    def test_a_stable_run_records_no_field_and_a_run_leaves_no_object_in_the_repository(self):
        self.write('code.txt', 'fresh content for the run 1\n')
        before = self.count()
        self.run_tests()
        state = ev.read_attempt(descend(self.root, self.events()))
        self.assertNotIn('changed', state.end)
        self.assertEqual(self.count(), before, 'a run wrote loose objects into the repository')
        self.assertIsNone(test_gate.evidence_problem(self.root, 'R1', 'F1.2', [self.events()]))

    def test_the_gate_takes_the_paths_from_the_field_and_runs_no_git_for_them(self):
        path = self.root / '.skillsync/runs/R1/tests/F1.2/1.jsonl'
        path.parent.mkdir(parents=True)
        hostile = '--output=/tmp/should-never-exist.txt'
        lines = [{'v': 1, 't': 'start', 'at': '2026-11-21T10:00:00Z', 'run': 'R1', 'step': 'F1.2', 'stepSource': 'flag', 'treeStart': 'a' * 40},
                 {'v': 1, 't': 'end', 'at': '2026-11-21T10:00:01Z', 'status': 'PASS', 'exit': 0, 'counts': {'total': 2, 'passed': 2, 'failed': 0, 'skipped': 0},
                  'tree': 'b' * 40, 'changed': [hostile, 'plain.txt']}]
        path.write_text('\n'.join(json.dumps(line) for line in lines) + '\n', encoding='utf-8')
        calls = []
        real = subprocess.run
        with mock.patch.object(test_gate.subprocess, 'run', side_effect=lambda *a, **k: (calls.append(a[0]), real(*a, **k))[1]):
            problem = test_gate.evidence_problem(self.root, 'R1', 'F1.2', [self.events()])
        self.assertIn('WHILE the tests ran', problem)
        self.assertIn(hostile, problem)   # text of the message only
        self.assertFalse([c for c in calls if any(hostile in str(part) for part in c)], 'the paths of the field never reach a git command line')

    def test_an_old_events_file_without_the_field_gives_the_message_without_paths(self):
        path = self.root / '.skillsync/runs/R1/tests/F1.2/1.jsonl'
        path.parent.mkdir(parents=True)
        lines = [{'v': 1, 't': 'start', 'at': '2026-11-21T10:00:00Z', 'run': 'R1', 'step': 'F1.2', 'stepSource': 'flag', 'treeStart': 'a' * 40},
                 {'v': 1, 't': 'end', 'at': '2026-11-21T10:00:01Z', 'status': 'PASS', 'exit': 0, 'counts': {'total': 2, 'passed': 2, 'failed': 0, 'skipped': 0}, 'tree': 'b' * 40}]
        path.write_text('\n'.join(json.dumps(line) for line in lines) + '\n', encoding='utf-8')
        problem = test_gate.evidence_problem(self.root, 'R1', 'F1.2', [self.events()])
        self.assertIn('WHILE the tests ran', problem)
        self.assertNotIn('changed:', problem)

    def test_the_object_directory_of_a_run_is_removed_also_when_the_start_fails(self):
        import gc
        from skillsync_workflow import quality
        recorder = test_run.TestRecorder(self.root, 'R1', 'F1.2', 'flag')
        check = {'id': 'c', 'argv': [sys.executable, '-c', PASSING], 'cwd': str(self.root), 'kind': 'build', 'timeout': 30}
        quality.run_validation(self.root, [check], observer=recorder)
        self.assertTrue(recorder.objects_dir)
        self.assertFalse(Path(recorder.objects_dir).exists(), 'removed after the run')
        broken = test_run.TestRecorder(self.root, 'R1', 'F1.2', 'flag')
        with mock.patch.object(ev.EventWriter, 'start', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                broken.started(check)
        leftover = Path(broken.objects_dir)
        self.assertTrue(leftover.exists())
        del broken
        gc.collect()
        self.assertFalse(leftover.exists(), 'removed when the recorder is dropped')

