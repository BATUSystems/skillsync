"""D: FILELOCK-FD-ON-INTERRUPT: a descriptor opened by `FileLock.__enter__` is closed (and its system lock released) when anything,
`KeyboardInterrupt` included, interrupts the acquisition before it is handed to the lock object."""
import errno
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype_server as ps  # noqa: E402


class FileLockDescriptor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='filelock-fd-')
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'x.lock')
        self.opened, self.closed = [], []
        real_open, real_close = os.open, os.close

        def tracking_open(path, *args, **kwargs):
            fd = real_open(path, *args, **kwargs)
            if os.fspath(path) == self.path:
                self.opened.append(fd)
            return fd

        def tracking_close(fd):
            self.closed.append(fd)
            return real_close(fd)

        for name, value in (('open', tracking_open), ('close', tracking_close)):
            patch = mock.patch.object(ps.os, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def assert_discarded_once(self):
        self.assertEqual(len(self.opened), 1)
        self.assertEqual(self.closed.count(self.opened[0]), 1, 'the descriptor is closed exactly once')

    def assert_lock_is_free(self):
        """The thread lock and the system lock are free: a plain acquisition succeeds at once (a leaked descriptor would still hold `flock`)."""
        with ps.FileLock(self.path, 0.5, 0o600):
            pass

    def test_an_interrupt_in_the_system_lock_call_does_not_leak_the_descriptor(self):
        with mock.patch.object(ps, '_try_lock_fd', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                with ps.FileLock(self.path, 1, 0o600):
                    self.fail('the lock must not be taken')
        self.assert_discarded_once()
        self.opened.clear()
        self.closed.clear()
        self.assert_lock_is_free()

    def test_an_interrupt_after_the_lock_and_before_the_hand_over_does_not_leak_it(self):
        # `os.name` is 'posix' for the call: on Windows the same-file check is skipped, and this is the point between the lock and the hand over
        with mock.patch.object(ps.FileLock, '_same_file', side_effect=KeyboardInterrupt), mock.patch.object(ps.os, 'name', 'posix'):
            with self.assertRaises(KeyboardInterrupt):
                with ps.FileLock(self.path, 1, 0o600):
                    self.fail('the lock must not be taken')
        self.assert_discarded_once()
        self.opened.clear()
        self.closed.clear()
        self.assert_lock_is_free()

    def test_an_interrupt_while_waiting_does_not_leak_it(self):
        busy = BlockingIOError(errno.EWOULDBLOCK, 'busy')
        with mock.patch.object(ps, '_try_lock_fd', side_effect=busy), mock.patch.object(ps.time, 'sleep', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                with ps.FileLock(self.path, 5, 0o600):
                    self.fail('the lock must not be taken')
        self.assert_discarded_once()

    def test_a_timeout_and_an_unsupported_file_system_close_the_descriptor_once(self):
        busy = BlockingIOError(errno.EWOULDBLOCK, 'busy')
        with mock.patch.object(ps, '_try_lock_fd', side_effect=busy):
            with self.assertRaises(ps.LockTimeout):
                with ps.FileLock(self.path, 0.05, 0o600):
                    pass
        self.assert_discarded_once()
        self.opened.clear()
        self.closed.clear()
        unsupported = OSError(next(iter(ps._LOCK_UNSUPPORTED_ERRNOS)), 'unsupported')
        with mock.patch.object(ps, '_try_lock_fd', side_effect=unsupported):
            with self.assertRaises(ps.LockUnavailable):
                with ps.FileLock(self.path, 0.5, 0o600):
                    pass
        self.assert_discarded_once()

    def test_a_second_interrupt_inside_the_discard_still_closes_the_descriptor(self):
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        with mock.patch.object(ps, '_unlock_fd', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                ps._discard_fd(fd)
        self.assertEqual(self.closed.count(fd), 1)

    def test_a_file_unlinked_under_the_waiter_closes_the_old_descriptor_once_and_locks_the_new_file(self):
        if os.name == 'nt':
            self.skipTest('POSIX: the same-file check is skipped on Windows')
        real = ps.FileLock._same_file
        calls = []

        def unlink_once(lock, fd):
            calls.append(fd)
            if len(calls) == 1:
                os.unlink(self.path)   # the holder released and removed the file between our open and our check
                return False
            return real(lock, fd)

        with mock.patch.object(ps.FileLock, '_same_file', unlink_once):
            with ps.FileLock(self.path, 2, 0o600):
                self.assertEqual(len(self.opened), 2)
                self.assertEqual(self.closed, [self.opened[0]], 'only the stale descriptor is closed while the new file is locked')
        self.assertEqual(len(self.closed), 2, 'the new descriptor is closed once, on exit (a number may be reused by the system)')

    def test_a_normal_acquisition_hands_the_descriptor_over_and_closes_it_on_exit(self):
        with ps.FileLock(self.path, 1, 0o600):
            self.assertEqual(self.closed, [], 'held while the lock is held')
        self.assertEqual(self.closed, self.opened)


if __name__ == '__main__':
    unittest.main()
