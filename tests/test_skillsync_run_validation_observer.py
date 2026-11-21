"""CORE-SMALLFIX (A): an unexpected exception of `observer.started` ends the attempt as `BLOCKED`, `finished` still runs."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tmp_paths import child  # noqa: E402
from skillsync_workflow import quality, test_events as ev, test_run  # noqa: E402
from test_skillsync_test_run import project  # noqa: E402

PASSING = "print('Ran 2 tests in 0.01s'); print('OK')"


class Observer:
    def __init__(self, error):
        self.error, self.finished_calls, self.lines = error, 0, 0

    def started(self, check):
        raise self.error

    def line(self, check, stream, text):
        self.lines += 1

    def finished(self, check, result, output):
        self.finished_calls += 1


class StartedRaises(unittest.TestCase):
    def check(self, root, marker):
        script = f"open({str(marker)!r}, 'w').close(); {PASSING}"
        return {'id': 'c', 'argv': [sys.executable, '-c', script], 'cwd': str(root), 'kind': 'test', 'timeout': 30}

    def test_an_unexpected_exception_is_blocked_and_finished_runs(self):
        for error in (RuntimeError('boom'), KeyError('k'), TypeError('t'), AttributeError('a')):
            with self.subTest(type(error).__name__), tempfile.TemporaryDirectory() as tmp:
                root = project(tmp)
                marker = child(tmp, 'started.flag')
                observer = Observer(error)
                report = quality.run_validation(root, [self.check(root, marker)], observer=observer)
                result = report['results'][0]
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertIn(type(error).__name__, result['reason'])
                self.assertIn('test_skillsync_run_validation_observer.py', result['reason'])   # file:line of the place that failed
                self.assertEqual(observer.finished_calls, 1)
                self.assertFalse(marker.exists(), 'the command must not start after the exception')

    def test_the_known_exceptions_keep_their_result(self):
        for error in (OSError('disk'), ValueError('bad')):
            with self.subTest(type(error).__name__), tempfile.TemporaryDirectory() as tmp:
                root = project(tmp)
                observer = Observer(error)
                result = quality.run_validation(root, [self.check(root, child(tmp, 'x.flag'))], observer=observer)['results'][0]
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertEqual(observer.finished_calls, 1)

    def test_the_series_goes_on_after_the_blocked_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp)
            marker = child(tmp, 'second.flag')
            first = {**self.check(root, child(tmp, 'first.flag')), 'id': 'a'}
            second = {**self.check(root, marker), 'id': 'b'}

            class Once(Observer):
                def started(self, check):
                    if check['id'] == 'a':
                        raise RuntimeError('only the first')
            observer = Once(None)
            report = quality.run_validation(root, [first, second], observer=observer)
            self.assertEqual([r['status'] for r in report['results']], ['BLOCKED', 'PASS'])
            self.assertTrue(marker.exists())
            self.assertEqual(observer.finished_calls, 2)

    def test_the_text_of_the_exception_is_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp)
            secret = 'hunter2' + 'hunter2'
            result = quality.run_validation(root, [self.check(root, child(tmp, 'x.flag'))], observer=Observer(RuntimeError(f'connect failed password={secret}')))['results'][0]
            self.assertNotIn(secret, result['reason'])
            self.assertIn('RuntimeError', result['reason'])


class RecorderStartFails(unittest.TestCase):
    def test_the_event_file_is_closed_and_ends_blocked_through_run_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp)
            recorder = test_run.TestRecorder(root, 'R1', 'F1.2', 'flag')
            check = {'id': 'c', 'argv': [sys.executable, '-c', PASSING], 'cwd': str(root), 'kind': 'test', 'timeout': 30}
            with mock.patch.object(ev.EventWriter, 'start', side_effect=RuntimeError('programmer error')):
                report = quality.run_validation(root, [check], observer=recorder)
            self.assertEqual(report['results'][0]['status'], 'BLOCKED')
            self.assertIsNone(recorder.writer.fd, 'the event file is closed')
            self.assertFalse(Path(recorder.objects_dir).exists(), 'the object directory is removed')
            self.assertEqual(ev.read_attempt(recorder.path).end['status'], 'BLOCKED')


if __name__ == '__main__':
    unittest.main()
