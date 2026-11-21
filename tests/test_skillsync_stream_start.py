"""CORE-STREAMFIX: a reader thread that cannot start must not leave the child process of the check alive."""
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from skillsync_workflow import quality  # noqa: E402
from test_skillsync_test_run import project  # noqa: E402

SLEEPING = "import time; print('child', flush=True); time.sleep(120)"


def failing_thread(fail_on):
    """A `Thread` class whose `fail_on`-th instance (1-based) raises in `start`; the others start as usual."""
    created = []

    class Thread(threading.Thread):
        def start(self):
            created.append(self)
            if len(created) == fail_on:
                raise RuntimeError("can't start new thread")
            super().start()
    return Thread


class SpyPopen:
    """Records the processes of `subprocess.Popen` started while it is installed."""

    def __init__(self):
        self.processes, self.real = [], subprocess.Popen

    def __call__(self, *args, **kwargs):
        process = self.real(*args, **kwargs)
        self.processes.append(process)
        return process


class StreamStart(unittest.TestCase):
    def check(self, root):
        return {'id': 'c', 'argv': [sys.executable, '-c', SLEEPING], 'cwd': str(root), 'kind': 'build', 'timeout': 60}

    def run_execute(self, fail_on):
        spy = SpyPopen()
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp)
            with mock.patch.object(quality.threading, 'Thread', failing_thread(fail_on)), mock.patch.object(quality.subprocess, 'Popen', spy):
                with self.assertRaises(RuntimeError):
                    quality._execute(self.check(root), lambda stream, text: None)
        self.assertEqual(len(spy.processes), 1)
        return spy.processes[0]

    def test_the_child_is_stopped_when_the_second_reader_thread_cannot_start(self):
        process = self.run_execute(2)
        self.assertIsNotNone(process.poll(), 'the child process is still running')

    def test_the_child_is_stopped_when_the_first_reader_thread_cannot_start(self):
        process = self.run_execute(1)
        self.assertIsNotNone(process.poll(), 'the child process is still running')

    def test_run_validation_ends_the_check_as_blocked_and_calls_finished_once(self):
        calls = []

        class Observer:
            def started(self, check): calls.append('started')
            def line(self, check, stream, text): pass
            def finished(self, check, result, output): calls.append('finished')
        spy = SpyPopen()
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp)
            with mock.patch.object(quality.threading, 'Thread', failing_thread(2)), mock.patch.object(quality.subprocess, 'Popen', spy):
                result = quality.run_validation(root, [self.check(root)], observer=Observer())['results'][0]
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn('RuntimeError', result['reason'])
        self.assertEqual(calls, ['started', 'finished'])
        self.assertIsNotNone(spy.processes[0].poll(), 'the child process is still running')

    def test_a_normal_run_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp)
            seen = []
            check = {'id': 'c', 'argv': [sys.executable, '-c', "print('out line'); import sys; print('err line', file=sys.stderr)"], 'cwd': str(root),
                     'kind': 'build', 'timeout': 30}
            code, raw, timed_out = quality._execute(check, lambda stream, text: seen.append((stream, text)))
        self.assertEqual((code, timed_out), (0, False))
        self.assertIn(('out', 'out line'), seen)
        self.assertIn(('err', 'err line'), seen)


if __name__ == '__main__':
    unittest.main()
