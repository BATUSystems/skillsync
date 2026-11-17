"""180-A: the events of a test run (`skillsync_workflow/test_events.py`): files, limits, mask, partial lines, parallel attempts."""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import test_events as ev  # noqa: E402


def fake_secret():
    return '-'.join(['zq9x', 'wv8y', 'ut7s', 'rp6q'])


class WindowsProcessState(unittest.TestCase):
    """180-W: the liveness probe of Windows (`OpenProcess` + `GetExitCodeProcess`); the logic with a fake `kernel32` everywhere, the real calls on Windows."""

    class Kernel:
        def __init__(self, handle=1, error=0, code=ev.STILL_ACTIVE, ok=True):
            self.handle, self.error, self.code, self.ok, self.closed, self.opened = handle, error, code, ok, [], []

        def get_last_error(self):
            return self.error

        def new_dword(self):
            return mock.Mock(value=0)

        @staticmethod
        def byref(value):
            return value

        def OpenProcess(self, access, inherit, pid):
            self.opened.append((access, inherit, pid))
            return self.handle

        def GetExitCodeProcess(self, handle, reference):
            reference.value = self.code
            return self.ok

        def CloseHandle(self, handle):
            self.closed.append(handle)

    def state(self, **kwargs):
        kernel = self.Kernel(**kwargs)
        return ev.windows_process_state(4242, kernel), kernel

    def test_a_running_process_is_alive_and_the_handle_is_closed(self):
        result, kernel = self.state()
        self.assertIs(result, True)
        self.assertEqual((kernel.opened, kernel.closed), ([(ev.PROCESS_QUERY_LIMITED_INFORMATION, False, 4242)], [1]))

    def test_an_exited_process_is_not_alive_even_when_a_handle_is_still_open(self):
        result, kernel = self.state(code=0)
        self.assertIs(result, False)
        self.assertEqual(kernel.closed, [1])
        self.assertIs(self.state(code=-1073741510 & 0xFFFFFFFF)[0], False)   # killed by Ctrl+C

    def test_no_such_process_is_false_access_denied_is_alive_anything_else_is_unknown(self):
        self.assertIs(self.state(handle=0, error=ev.ERROR_INVALID_PARAMETER)[0], False)
        self.assertIs(self.state(handle=0, error=ev.ERROR_ACCESS_DENIED)[0], True)
        self.assertIsNone(self.state(handle=0, error=1234)[0])
        result, kernel = self.state(ok=False)
        self.assertIsNone(result)
        self.assertEqual(kernel.closed, [1])   # the handle is closed on every path

    def test_a_number_that_no_pid_can_be_is_false_without_a_call(self):
        kernel = self.Kernel()
        for pid in (0, -5, 2 ** 32, 10 ** 12, True.__class__, None, 1.5):
            with self.subTest(pid=pid):
                self.assertIs(ev.windows_process_state(pid, kernel), False)
        self.assertEqual(kernel.opened, [])

    def test_the_real_calls_on_windows(self):
        if sys.platform != 'win32':
            return   # the logic above is the test elsewhere; this branch needs the real kernel32
        import subprocess
        self.assertIs(ev.windows_process_state(os.getpid()), True)
        self.assertIs(ev.windows_process_state(10 ** 12), False)
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        child.wait()
        self.assertIs(ev.windows_process_state(child.pid), False)   # exited, its handle may still be open here
        self.assertIs(ev.process_state(os.getpid()), True)


class EventFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='test-events-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def attempt(self, step='F1.1', mask=False):
        path, number = ev.new_attempt(self.root, 'run-1', step)
        return path, number, ev.EventWriter(path, mask=mask)

    def test_path_layout_and_validation(self):
        path, number, writer = self.attempt('F5.4')
        self.addCleanup(writer.close)
        self.assertEqual(path, self.root / '.skillsync/runs/run-1/tests/F5.4/1.jsonl')
        self.assertEqual(number, 1)
        self.assertEqual(ev.new_attempt(self.root, 'run-1', None)[0].parent.name, '_none')
        for bad in ('../x', 'a b', 'a/b', ''):
            with self.assertRaises(ValueError):
                ev.step_directory(self.root, 'run-1', bad)
        with self.assertRaises(ValueError):
            ev.step_directory(self.root, '../run', 'F1.1')

    def test_symlink_on_the_way_is_refused(self):
        real = self.root / 'elsewhere'
        real.mkdir()
        (self.root / '.skillsync').mkdir()
        os.symlink(real, self.root / '.skillsync/runs')
        with self.assertRaises(ValueError):
            ev.step_directory(self.root, 'run-1', 'F1.1')

    def test_parallel_attempts_get_different_numbers(self):
        numbers, errors = [], []

        def take():
            try:
                numbers.append(ev.new_attempt(self.root, 'run-1', 'F1.1')[1])
            except Exception as error:  # pragma: no cover
                errors.append(error)
        threads = [threading.Thread(target=take) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(numbers), list(range(1, 13)))
        self.assertEqual(len(ev.list_attempts(self.root, 'run-1', 'F1.1')), 12)

    def test_roundtrip_and_state(self):
        path, _, w = self.attempt()
        w.start(run='run-1', step='F1.1', command='python3 -m unittest', tool='unittest', pid=os.getpid())
        w.collected(3, ['t.a', 't.b', 't.c'])
        w.test('t.a', 't.a', 'passed', group='t', ms=3)
        w.test('t.b', 't.b', 'failed', group='t', msg='AssertionError: 1 != 2')
        w.test('t.c', 't.c', 'running')
        state = ev.read_attempt(path)
        self.assertEqual(state.state, 'running')
        self.assertEqual((state.counts['passed'], state.counts['failed'], state.counts['running'], state.counts['total']), (1, 1, 1, 3))
        w.test('t.c', 't.c', 'skipped')
        w.end('FAIL', 1, counts={'total': 3, 'passed': 1, 'failed': 1, 'skipped': 1})
        w.close()
        state = ev.read_attempt(path, state)   # continues from the offset
        self.assertEqual(state.state, 'failed')
        self.assertEqual(state.counts['skipped'], 1)
        self.assertEqual(state.tests['t.b']['msg'], 'AssertionError: 1 != 2')

    def test_partial_last_line_is_left_for_later_and_junk_is_skipped(self):
        path, _, w = self.attempt()
        w.test('a', 'a', 'passed')
        w.close()
        with open(path, 'ab') as handle:
            handle.write(b'not json\n{"v":1,"t":"test","id":"b","state":"pa')   # a write in progress
        events, offset, skipped = ev.read_events(path)
        self.assertEqual([e['id'] for e in events], ['a'])
        self.assertEqual(skipped, 1)
        with open(path, 'ab') as handle:
            handle.write(b'ssed","name":"b"}\n')
        events, offset2, _ = ev.read_events(path, offset)
        self.assertEqual([e['id'] for e in events], ['b'])
        self.assertGreater(offset2, offset)

    def test_a_line_separator_inside_a_message_does_not_cut_the_event(self):
        path, _, w = self.attempt()
        w.test('a', 'a', 'failed', msg='before after\x0bend')
        w.close()
        events, _, skipped = ev.read_events(path)
        self.assertEqual(skipped, 0)
        self.assertEqual(len(events), 1)
        self.assertIn('before', events[0]['msg'])

    def test_limits(self):
        path, _, w = self.attempt()
        with mock.patch.object(ev, 'MAX_TEST_EVENTS', 5):
            results = [w.test(f't{i}', f't{i}', 'passed') for i in range(8)]
        self.assertEqual(results, [True] * 5 + [False] * 3)
        self.assertTrue(w.truncated)
        w.end('PASS', 0, counts={'total': 8, 'passed': 8})
        w.close()
        state = ev.read_attempt(path)
        self.assertTrue(state.truncated)
        self.assertEqual(state.counts['passed'], 8)   # the counters of `end` stay true

    def test_byte_limit_keeps_the_end_event(self):
        path, _, w = self.attempt()
        with mock.patch.object(ev, 'MAX_BYTES', 400):
            for i in range(50):
                w.test(f't{i}', 'n' * 50, 'passed')
            w.end('PASS', 0, counts={'total': 50, 'passed': 50})
        w.close()
        self.assertTrue(path.stat().st_size < 1200)
        self.assertEqual(ev.read_attempt(path).state, 'passed')

    def test_text_is_cut_and_cleaned(self):
        path, _, w = self.attempt()
        w.test('a', 'n' * 500, 'failed', msg='x' * 5000 + '\x1b[31mred\x1b[0m')
        w.close()
        event = ev.read_events(path)[0][0]
        self.assertEqual(len(event['name']), ev.MAX_NAME)
        self.assertTrue(event['name'].endswith('…'))
        self.assertEqual(len(event['msg']), ev.MAX_MSG)

    def test_ansi_bidi_and_zero_width_are_removed_and_lines_of_a_message_stay(self):
        text = ev.mask_text('a\x1b[31mred\x1b[0m ‮bidi​ ok\nsecond\n\n\n\nthird', False, keep_newlines=True)
        self.assertEqual(text, 'ared bidi ok\nsecond\n\nthird')

    def test_mask_off_keeps_the_text_and_on_masks_before_cutting(self):
        secret = fake_secret()
        with mock.patch.dict(os.environ, {'DEPLOY_TOKEN': secret}):
            self.assertIn(secret, ev.mask_text('boom ' + secret, False))
            masked = ev.mask_text('b' * 1990 + ' ' + secret, True)
            self.assertNotIn('zq9x', masked)
            self.assertNotIn('rp6q', masked)
            self.assertNotIn('hunter2', ev.mask_text('password: ' + 'hunter2' * 3 + ' tail', True))

    def test_the_command_line_is_always_masked(self):
        secret = fake_secret()
        path, _, w = self.attempt(mask=False)
        with mock.patch.dict(os.environ, {'DEPLOY_TOKEN': secret}):
            w.start(command=f'tool --token={secret} password: hunter2hunter2')
        w.test('t', 'name ' + secret, 'failed', msg='msg ' + secret)
        w.close()
        events = ev.read_events(path)[0]
        self.assertNotIn(secret, events[0]['command'])
        self.assertNotIn('hunter2', events[0]['command'])
        self.assertIn(secret, events[1]['name'] + events[1]['msg'])   # mask off: the result is stored as it is

    def test_the_setting(self):
        self.assertFalse(ev.mask_setting({}))
        self.assertFalse(ev.mask_setting({'tests': {'command': 'x'}}))
        self.assertTrue(ev.mask_setting({'tests': {'mask': True}}))

    def test_unknown_state_is_refused(self):
        _, _, w = self.attempt()
        self.addCleanup(w.close)
        with self.assertRaises(ValueError):
            w.test('a', 'a', 'maybe')

    def test_interrupted_attempt_and_stream_counts(self):
        path, _, w = self.attempt()
        w.start(command='ng test', pid=os.getpid())
        w.counts(passed=3, failed=1, running=0, total=10, force=True)
        w.end('BLOCKED', None, reason='interrupted')
        w.close()
        state = ev.read_attempt(path)
        self.assertEqual(state.state, 'interrupted')
        self.assertEqual((state.counts['passed'], state.counts['failed'], state.counts['total']), (3, 1, 10))

    def test_counts_are_rate_limited_and_the_last_one_is_flushed_by_end(self):
        path, _, w = self.attempt()
        w.counts(passed=1, force=True)
        self.assertFalse(w.counts(passed=2))
        self.assertFalse(w.counts(passed=3))
        w.end('PASS', 0)
        w.close()
        events = ev.read_events(path)[0]
        self.assertEqual([e['passed'] for e in events if e['t'] == 'counts'], [1, 3])

    def test_windows_unsafe_names_are_refused(self):
        for bad in ('CON', 'nul', 'COM1', 'lpt9', 'AUX.txt', 'F1.', 'F1.2.'):
            with self.assertRaises(ValueError, msg=bad):
                ev.step_directory(self.root, 'run-1', bad)
            with self.assertRaises(ValueError, msg=bad):
                ev.step_directory(self.root, bad, 'F1.1')
        ev.step_directory(self.root, 'run-1', 'F1.2')   # a normal id stays valid

    def test_a_backslash_in_a_test_id_never_becomes_a_path_and_is_the_same_on_both_systems(self):
        path, _, w = self.attempt()
        w.test('C:\\proj\\tests\\a.py::t', 'a', 'passed')
        w.close()
        self.assertEqual(ev.read_events(path)[0][0]['id'], 'C:/proj/tests/a.py::t')

    def test_collected_names_use_the_same_slash_as_the_ids(self):
        path, _, w = self.attempt()
        w.collected(2, names=['C:\\proj\\tests\\a.py::t', 'tests/b.py::u'])
        w.close()
        names = [n for event in ev.read_events(path)[0] for n in event.get('names', [])]
        self.assertEqual(names, ['C:/proj/tests/a.py::t', 'tests/b.py::u'])

    def test_a_test_without_a_result_is_not_running_after_the_end_of_the_run(self):
        path, _, w = self.attempt()
        w.start(run='run-1', step='F1.1', command='x')
        w.test('t1', 't1', 'running')
        w.test('t2', 't2', 'passed')
        w.close()
        running = ev.read_attempt(path)
        self.assertEqual((running.tests['t1']['state'], running.counts['running']), ('running', 1))
        w = ev.EventWriter(path)
        w.end('BLOCKED', 1)
        w.close()
        done = ev.read_attempt(path)
        self.assertEqual(done.tests['t1']['state'], 'waiting')
        self.assertEqual((done.counts['running'], done.counts['waiting'], done.counts['passed']), (0, 1, 1))

    def test_the_file_is_binary_with_explicit_line_feeds(self):
        path, _, w = self.attempt()
        w.test('a', 'a', 'failed', msg='x\ry\r\nz')
        w.close()
        data = path.read_bytes()
        self.assertNotIn(b'\r', data)   # a carriage return inside a message is escaped by JSON
        self.assertTrue(data.endswith(b'\n'))

    def test_writes_from_threads_do_not_interleave(self):
        path, _, w = self.attempt()
        def burst(prefix):
            for i in range(200):
                w.test(f'{prefix}{i}', f'{prefix}{i}', 'passed')
        threads = [threading.Thread(target=burst, args=(c,)) for c in 'abcd']
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        w.close()
        events, _, skipped = ev.read_events(path)
        self.assertEqual((len(events), skipped), (800, 0))

    def test_the_start_event_has_the_host_and_the_pid_of_another_host_says_nothing(self):
        path, _, w = self.attempt()
        w.start(command='x', pid=os.getpid())
        w.close()
        start = ev.read_events(path)[0][0]
        self.assertTrue(start['host'])
        self.assertTrue(ev.is_alive(2 ** 22 + 777, 'another-machine'))   # unknown: not declared dead
        if sys.platform != 'win32':
            self.assertFalse(ev.is_alive(2 ** 22 + 777, start['host']))

    def test_the_setting_fails_closed(self):
        for value in (True, 'on', 'yes', 'true', 1, ['x'], 'maybe'):
            self.assertTrue(ev.mask_setting({'tests': {'mask': value}}), value)
        for value in (False, None, 'off', 'false', 'no', 0, ''):
            self.assertFalse(ev.mask_setting({'tests': {'mask': value}}), value)
        self.assertFalse(ev.mask_setting({'tests': {}}))

    def test_a_malformed_event_does_not_break_the_fold(self):
        state = ev.AttemptState(1)
        state.feed([{'v': 1, 't': 'start', 'at': 'x'}, {'v': 1, 't': 'test', 'id': 'a', 'state': 'passed', 'group': ['not', 'a', 'string']},
                    {'v': 1, 't': 'test', 'id': 'b', 'state': 'failed', 'msg': {'x': 1}}, {'v': 1, 't': 'test', 'id': 'c', 'state': 'passed', 'name': 'c'},
                    {'v': 1, 't': 'end', 'status': ['PASS'], 'counts': 'nope'}])
        self.assertEqual(list(state.tests), ['c'])
        self.assertIn(state.state, ('failed', 'interrupted'))

    def test_a_trace_keeps_its_indentation(self):
        self.assertEqual(ev.mask_text('Traceback:\n  File "a.py", line 1\n    x = 1', False, keep_newlines=True), 'Traceback:\n  File "a.py", line 1\n    x = 1')


if __name__ == '__main__':
    unittest.main()
