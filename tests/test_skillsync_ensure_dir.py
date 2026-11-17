"""`fsutil.ensure_dir`: a directory shared by parallel steps is created without an error even when the platform answers the loser of the race with
`PermissionError` (Windows, WinError 5), also when the `is_dir()` that follows fails the same way."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import fsutil, test_events  # noqa: E402


class EnsureDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ensure-dir-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_creates_nested_and_is_idempotent(self):
        target = self.base / 'a' / 'b' / 'c'
        self.assertEqual(fsutil.ensure_dir(target), target)
        self.assertTrue(target.is_dir())
        fsutil.ensure_dir(target)

    def test_without_parents_a_missing_parent_is_an_error(self):
        with self.assertRaises(FileNotFoundError):
            fsutil.ensure_dir(self.base / 'x' / 'y', parents=False)

    def test_a_file_in_the_way_is_raised_at_once(self):
        (self.base / 'f').write_text('x')
        with mock.patch.object(fsutil.time, 'sleep') as sleep, self.assertRaises(OSError):
            fsutil.ensure_dir(self.base / 'f')
        sleep.assert_not_called()

    def _loser(self, fail_is_dir):
        """`os.mkdir` that creates the directory and then answers 'access denied' (the loser of the race); `is_dir` fails `fail_is_dir` times."""
        real_mkdir, real_is_dir, state = os.mkdir, Path.is_dir, {'denied': 0, 'stat': 0}

        def mkdir(path, *args, **kwargs):
            real_mkdir(path, *args, **kwargs)
            if not state['denied']:
                state['denied'] += 1
                raise PermissionError(13, 'Access is denied (simulated WinError 5)', str(path))

        def is_dir(self, *args, **kwargs):
            if state['stat'] < fail_is_dir:
                state['stat'] += 1
                raise PermissionError(13, 'Access is denied on stat (simulated)', str(self))
            return real_is_dir(self, *args, **kwargs)
        return mkdir, is_dir

    def test_pathlib_alone_fails_where_the_helper_survives(self):
        target = self.base / 'shared'
        mkdir, is_dir = self._loser(fail_is_dir=1)
        with mock.patch('os.mkdir', mkdir), mock.patch.object(Path, 'is_dir', is_dir):
            with self.assertRaises(PermissionError):
                target.mkdir(parents=True, exist_ok=True)   # the reproduction of the 1.9.0 Windows flake
        target2 = self.base / 'shared2'
        mkdir, is_dir = self._loser(fail_is_dir=1)
        with mock.patch('os.mkdir', mkdir), mock.patch.object(Path, 'is_dir', is_dir), mock.patch.object(fsutil.time, 'sleep'):
            self.assertEqual(fsutil.ensure_dir(target2), target2)
        self.assertTrue(target2.is_dir())

    def test_a_directory_that_stays_denied_is_raised_after_the_attempts(self):
        def mkdir(path, *args, **kwargs):
            raise PermissionError(13, 'denied', str(path))
        with mock.patch('os.mkdir', mkdir), mock.patch.object(fsutil.time, 'sleep') as sleep:
            with self.assertRaises(PermissionError):
                fsutil.ensure_dir(self.base / 'never', attempts=4)
        self.assertEqual(sleep.call_count, 3)

    def test_new_attempt_of_two_steps_in_a_fresh_run(self):
        mkdir, is_dir = self._loser(fail_is_dir=1)
        with mock.patch('os.mkdir', mkdir), mock.patch.object(Path, 'is_dir', is_dir), mock.patch.object(fsutil.time, 'sleep'):
            path, number = test_events.new_attempt(self.base, 'R1', 'F1.1')
        self.assertEqual((path.name, number), ('1.jsonl', 1))
        self.assertTrue(path.is_file())

    def test_a_race_of_many_threads_on_a_fresh_shared_parent(self):
        threads_count, rounds, errors = 8, 40, []
        for round_ in range(rounds):
            shared = self.base / f'r{round_}' / 'logs'
            barrier = threading.Barrier(threads_count)

            def go(index):
                try:
                    barrier.wait(timeout=10)
                    fsutil.ensure_dir(shared / f'u{index}')
                except BaseException as error:   # noqa: BLE001 - collected for the assertion
                    errors.append(repr(error))
            threads = [threading.Thread(target=go, args=(i,)) for i in range(threads_count)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)
            self.assertEqual(len([p for p in shared.iterdir() if p.is_dir()]), threads_count)
        self.assertEqual(errors, [])


if __name__ == '__main__':
    unittest.main()
