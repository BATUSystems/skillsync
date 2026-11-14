"""`FileLock` (MAC-141, M3): a file system without system locks is reported at once (`LockUnavailable`, 503
`lock_unsupported`) instead of waiting for the timeout as for contention; the questions store, comments and users files
share the primitive. `unlink=True` (approvals lock) removes the file on release.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_filelock_unsupported.py`.
"""
import errno
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype_server as ps  # noqa: E402


class FileLockClassificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='filelock-')
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'x.lock')

    def test_every_unsupported_errno_fails_at_once(self):
        for name in ('ENOTSUP', 'EOPNOTSUPP', 'ENOSYS'):
            code = getattr(errno, name, None)
            if code is None:
                continue
            with self.subTest(errno=name), mock.patch.object(ps, '_try_lock_fd', side_effect=OSError(code, name)):
                started = time.monotonic()
                with self.assertRaises(ps.LockUnavailable) as caught:
                    ps.FileLock(self.path, 5).__enter__()
                self.assertLess(time.monotonic() - started, 1)
                self.assertEqual((caught.exception.status, caught.exception.code), (503, 'lock_unsupported'))
        self.assertTrue(issubclass(ps.LockUnavailable, ps.LockTimeout), 'handlers of LockTimeout keep working')

    def test_enolck_is_retried_for_half_of_the_timeout_then_reported(self):
        with mock.patch.object(ps, '_try_lock_fd', side_effect=OSError(errno.ENOLCK, 'no locks')):
            started = time.monotonic()
            with self.assertRaises(ps.LockUnavailable):
                ps.FileLock(self.path, 1.0).__enter__()
            elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 0.45, 'a transient lack of a lock manager gets a chance')
        self.assertLess(elapsed, 0.95)

    def test_einval_stays_contention_like_before(self):
        with mock.patch.object(ps, '_try_lock_fd', side_effect=OSError(errno.EINVAL, 'bad argument')):
            with self.assertRaises(ps.LockTimeout) as caught:
                ps.FileLock(self.path, 0.15).__enter__()
            self.assertNotIsInstance(caught.exception, ps.LockUnavailable)

    def test_contention_errnos_wait_and_time_out(self):
        for name in ('EAGAIN', 'EACCES', 'EDEADLK', 'EWOULDBLOCK'):
            with self.subTest(errno=name), mock.patch.object(ps, '_try_lock_fd', side_effect=OSError(getattr(errno, name), name)):
                with self.assertRaises(ps.LockTimeout) as caught:
                    ps.FileLock(self.path, 0.15).__enter__()
                self.assertNotIsInstance(caught.exception, ps.LockUnavailable)
                self.assertEqual(caught.exception.code, 'lock_timeout')

    def test_the_thread_lock_is_released_after_a_failure(self):
        with mock.patch.object(ps, '_try_lock_fd', side_effect=OSError(errno.ENOTSUP, 'x')):
            with self.assertRaises(ps.LockUnavailable):
                ps.FileLock(self.path, 1).__enter__()
        with ps.FileLock(self.path, 1):  # the same path is usable right after
            pass

    def test_default_keeps_the_lock_file_and_unlink_removes_it(self):
        with ps.FileLock(self.path, 1):
            pass
        self.assertTrue(os.path.exists(self.path))
        with ps.FileLock(self.path, 1, unlink=True):
            self.assertTrue(os.path.exists(self.path))
        self.assertFalse(os.path.exists(self.path))


class QuestionsStoreSharesThePrimitiveTests(unittest.TestCase):
    def test_the_questions_store_reports_an_unsupported_lock_clearly(self):
        with tempfile.TemporaryDirectory(prefix='filelock-q-') as directory:
            store = ps.QuestionsStore(directory)
            with mock.patch.object(ps, '_try_lock_fd', side_effect=OSError(errno.ENOTSUP, 'x')):
                started = time.monotonic()
                with self.assertRaises(ps.LockUnavailable):
                    with store.lock():
                        pass
                self.assertLess(time.monotonic() - started, 1)

    def test_the_prototype_lock_of_comments_reports_it_too(self):
        with tempfile.TemporaryDirectory(prefix='filelock-c-') as directory:
            os.makedirs(os.path.join(directory, 'demo'))
            lock = ps.FileLock(os.path.join(directory, 'demo', 'comments.json.lock'), 1)
            with mock.patch.object(ps, '_try_lock_fd', side_effect=OSError(errno.ENOTSUP, 'x')):
                with self.assertRaises(ps.LockTimeout) as caught:
                    lock.__enter__()
                self.assertEqual(caught.exception.status, 503)


if __name__ == '__main__':
    unittest.main()
