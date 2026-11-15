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


class WaiterFollowsAnUnlinkedLockFileTests(unittest.TestCase):
    """A holder that unlinks the lock file (the orphan sweep of the questions store) must not leave waiters holding a lock
    on the unlinked inode: every `FileLock` re-checks, after `flock`, that the path still names the file it locked."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='filelock-sweep-')
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'questions.json.lock')

    @unittest.skipIf(os.name == 'nt', 'Windows nie pozwala usunąć ani podmienić otwartego pliku')
    def test_a_default_waiter_locks_the_recreated_file_after_the_holder_unlinked_it(self):
        import threading
        holder_in, waiter_started, result = threading.Event(), threading.Event(), {}

        def waiter():
            lock = ps.FileLock(self.path, 10)
            waiter_started.set()
            lock.__enter__()
            result['same'] = lock._same_file(lock._fd)
            result['exists'] = os.path.exists(self.path)
            lock.__exit__(None, None, None)

        with mock.patch.object(ps, '_process_lock', side_effect=lambda path: threading.Lock()):
            holder = ps.FileLock(self.path, 10)
            holder.__enter__()  # the sweeper holds the lock ...
            thread = threading.Thread(target=waiter)
            thread.start()
            waiter_started.wait(5)
            time.sleep(0.3)  # ... the waiter is blocked in flock on the same inode ...
            os.unlink(self.path)  # ... and the sweeper removes the orphan lock file
            holder.__exit__(None, None, None)
            thread.join(10)
        self.assertEqual(result, {'same': True, 'exists': True}, 'the waiter re-opened the path instead of keeping the unlinked inode')

    def test_the_orphan_sweep_removes_only_a_free_lock_without_its_data_file(self):
        with tempfile.TemporaryDirectory(prefix='filelock-orphan-') as directory:
            store = ps.QuestionsStore(directory)
            open(store.lock_path, 'w').close()
            self.assertTrue(store.sweep_orphan_lock())
            self.assertFalse(os.path.exists(store.lock_path))
            open(store.lock_path, 'w').close()
            with ps.FileLock(store.lock_path, 5):
                self.assertFalse(store.sweep_orphan_lock(), 'a held lock is left alone')
            self.assertTrue(os.path.exists(store.lock_path))

    def test_the_sweep_unlinks_under_the_lock_so_a_waiter_never_keeps_the_dead_inode(self):
        import threading
        with tempfile.TemporaryDirectory(prefix='filelock-orphan2-') as directory:
            store = ps.QuestionsStore(directory)
            open(store.lock_path, 'w').close()
            real = ps.FileLock.__exit__
            waiter_done, result = threading.Event(), {}

            def waiter():
                lock = ps.FileLock(store.lock_path, 10)
                lock.__enter__()
                result['same'] = lock._same_file(lock._fd)
                lock.__exit__(None, None, None)
                waiter_done.set()

            def exit_with_a_waiter(lock, *exc):
                if lock.unlink and 'thread' not in result:  # the sweeper is leaving: start a waiter that blocks on the old inode
                    result['thread'] = threading.Thread(target=waiter)
                    result['thread'].start()
                    time.sleep(0.3)
                return real(lock, *exc)

            with mock.patch.object(ps, '_process_lock', side_effect=lambda path: threading.Lock()), \
                    mock.patch.object(ps.FileLock, '__exit__', exit_with_a_waiter):
                self.assertTrue(store.sweep_orphan_lock())
            result['thread'].join(10)
            self.assertTrue(waiter_done.is_set())
            self.assertEqual(result['same'], True)


class WaiterEntersAfterReleaseTests(unittest.TestCase):
    """The property the unlink/replace tests protect, in a form every platform can run: a second writer blocks while the
    first holds the lock and enters as soon as the first releases it (with and without `unlink=True`)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='filelock-release-')
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'x.lock')

    def check(self, unlink):
        import threading
        entered, finished = threading.Event(), threading.Event()
        errors = []

        def waiter():
            try:
                with ps.FileLock(self.path, 20, unlink=unlink):
                    entered.set()
                finished.set()
            except BaseException as error:  # noqa: BLE001
                errors.append(repr(error))
                entered.set()
                finished.set()

        with mock.patch.object(ps, '_process_lock', side_effect=lambda path: threading.Lock()):
            holder = ps.FileLock(self.path, 20, unlink=unlink)
            holder.__enter__()
            thread = threading.Thread(target=waiter)
            thread.start()
            self.assertFalse(entered.wait(0.4), 'the waiter must block while the holder keeps the lock')
            holder.__exit__(None, None, None)
            self.assertTrue(finished.wait(15), 'the waiter enters after the holder released')
            thread.join(10)
        self.assertEqual(errors, [])
        self.assertTrue(entered.is_set())

    def test_the_waiter_enters_after_the_holder_releases(self):
        self.check(False)

    def test_the_waiter_enters_after_a_holder_that_unlinks_the_lock_file(self):
        self.check(True)


class OpenRefusedForAnInstantTests(unittest.TestCase):
    """Windows: the holder that released the lock deletes the file (`unlink=True`); an open in that instant is refused with
    EACCES (measured: 43 of 24000 acquisitions by 6 processes, always at the `os.open` of the lock file). The open is retried
    briefly; a refusal that lasts is still raised. The Windows behaviour of `retry_transient` is switched on here, so the
    tests run on every system."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='filelock-')
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'x.lock')
        patch = mock.patch.object(ps, '_WINDOWS', True)
        patch.start()
        self.addCleanup(patch.stop)
        self.real_open = os.open

    def refuse_first(self, count):
        calls = []

        def fake(path, *args, **kwargs):
            if os.fspath(path) == self.path:
                calls.append(path)
                if len(calls) <= count:
                    raise PermissionError(errno.EACCES, 'Permission denied', path)
            return self.real_open(path, *args, **kwargs)
        return calls, mock.patch.object(ps.os, 'open', side_effect=fake)

    def test_a_refused_open_for_an_instant_is_retried_and_the_lock_is_taken(self):
        calls, patch = self.refuse_first(3)
        with patch:
            with ps.FileLock(self.path, 5, 0o600, unlink=True):
                pass
        self.assertEqual(len(calls), 4, 'three refusals, then the open succeeds')
        self.assertFalse(os.path.exists(self.path), 'unlink=True still removes the file')

    def test_a_lasting_refusal_is_raised_after_the_short_retry_only(self):
        calls, patch = self.refuse_first(10 ** 6)
        started = time.monotonic()
        with patch:
            with self.assertRaises(PermissionError):
                with ps.FileLock(self.path, 5, 0o600, unlink=True):
                    self.fail('the lock must not be taken')
        elapsed = time.monotonic() - started
        self.assertGreater(len(calls), 3, 'it was retried')
        self.assertLess(elapsed, ps.TRANSIENT_RETRY_SECONDS + 1.0, 'not for the whole lock timeout')

    def test_the_retry_never_outlasts_a_short_lock_timeout(self):
        calls, patch = self.refuse_first(10 ** 6)
        started = time.monotonic()
        with patch:
            with self.assertRaises(PermissionError):
                with ps.FileLock(self.path, 0.1, 0o600):
                    pass
        self.assertLess(time.monotonic() - started, 0.5)

    def test_the_thread_lock_is_released_after_a_refusal(self):
        _calls, patch = self.refuse_first(10 ** 6)
        with patch:
            with self.assertRaises(PermissionError):
                with ps.FileLock(self.path, 0.1, 0o600):
                    pass
        with ps.FileLock(self.path, 1, 0o600):   # the same path, the real open: not blocked by the failed attempt
            pass


if __name__ == '__main__':
    unittest.main()
