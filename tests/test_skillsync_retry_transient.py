"""`prototype_server.retry_transient`: the short, bounded retry of a refused open/rename/replace on Windows.

On Windows a file that another thread has open (or is replacing) is refused with `PermissionError` for an instant: a one-time
login token was then lost by every caller (nobody got the session) and a read of questions.json ended as a 500. The helper retries
only there, only that error, only for a bounded time. The cases inject the refusals, so they run the same on every system."""
import errno
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillsync_workflow import prototype_server as ps  # noqa: E402
from tests.test_skillsync_prototype_questions import NOON, ask  # noqa: E402
from tests.test_skillsync_prototype_server import FakeClock  # noqa: E402


def refusal():
    error = PermissionError(errno.EACCES, 'Access is denied')
    error.winerror = 5   # the attribute exists on Windows only
    return error


class RetryTransient(unittest.TestCase):
    def test_a_transient_refusal_is_retried_on_windows_and_the_result_returned(self):
        calls = []

        def operation():
            calls.append(1)
            if len(calls) < 4:
                raise refusal()
            return 'ok'

        with mock.patch.object(ps, '_WINDOWS', True):
            self.assertEqual(ps.retry_transient(operation), 'ok')
        self.assertEqual(len(calls), 4)

    def test_other_systems_run_the_operation_once(self):
        calls = []

        def operation():
            calls.append(1)
            raise refusal()

        with mock.patch.object(ps, '_WINDOWS', False):
            with self.assertRaises(PermissionError):
                ps.retry_transient(operation)
        self.assertEqual(len(calls), 1)

    def test_a_refusal_that_lasts_is_raised_within_the_bound_with_its_errno(self):
        started = time.monotonic()
        with mock.patch.object(ps, '_WINDOWS', True):
            with self.assertRaises(PermissionError) as caught:   # a directory without the right: a real error stays an error
                ps.retry_transient(lambda: (_ for _ in ()).throw(refusal()), seconds=0.1)
        elapsed = time.monotonic() - started
        self.assertEqual(caught.exception.errno, errno.EACCES)
        self.assertGreaterEqual(elapsed, 0.1)
        self.assertLess(elapsed, 1.5)

    def test_other_errors_are_not_retried(self):
        for error in (FileNotFoundError(errno.ENOENT, 'missing'), FileExistsError(errno.EEXIST, 'exists'),
                      OSError(errno.EIO, 'I/O error'), ValueError('x')):
            calls = []

            def operation(error=error):
                calls.append(1)
                raise error

            with self.subTest(error=type(error).__name__), mock.patch.object(ps, '_WINDOWS', True):
                with self.assertRaises(type(error)):
                    ps.retry_transient(operation)
                self.assertEqual(len(calls), 1)


class FlakyOs:
    """`os.rename`/`os.replace`/`os.open` that refuse (PermissionError) a share of the calls, then do the real work (`os.open` and `os.unlink` too)."""

    def __init__(self, every=3):
        self.counter = 0
        self.every = every
        self.lock = threading.Lock()

    def wrap(self, real):
        def call(*args, **kwargs):
            with self.lock:
                self.counter += 1
                refuse = self.counter % self.every != 0
            if refuse:
                raise refusal()
            return real(*args, **kwargs)
        return call


class LinkConsumeKeepsExclusivity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='skillsync-retry-')
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = os.path.join(self.tmp.name, 'run')
        self.assertIsNone(ps.link_prepare_run_dir(self.run_dir))

    def race(self, workers=6, instance='i1', flaky=None):
        token, _expires = ps.link_issue(self.run_dir, 'ewa@example.test', '/demo/', instance=instance)   # issued with the real os.rename
        barrier = threading.Barrier(workers)
        results = []

        def work():
            barrier.wait(10)
            results.append(ps.link_consume(self.run_dir, token, time.time(), instance=instance))

        threads = [threading.Thread(target=work) for _ in range(workers)]
        # Without `flaky` nothing is patched: on Windows the real retry meets the real refusals of the system.
        with mock.patch.object(ps, '_WINDOWS', True if flaky is not None else ps._WINDOWS), \
                mock.patch.object(os, 'rename', flaky.wrap(os.rename) if flaky is not None else os.rename):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(20)
        return [item for item in results if item is not None], len(results)

    def test_exactly_one_caller_gets_the_token_in_500_races(self):
        for _ in range(500):
            winners, answered = self.race()
            self.assertEqual((len(winners), answered), (1, 6))

    def test_with_transient_refusals_injected_never_two_winners_and_one_wins(self):
        flaky = FlakyOs(every=3)
        for _ in range(200):
            winners, answered = self.race(flaky=flaky)
            self.assertEqual((len(winners), answered), (1, 6))

    def test_a_refusal_that_lasts_gives_no_session_and_keeps_the_token(self):
        token, _expires = ps.link_issue(self.run_dir, 'ewa@example.test', '/demo/', instance='i1')
        with mock.patch.object(ps, '_WINDOWS', True), mock.patch.object(ps, 'TRANSIENT_RETRY_SECONDS', 0.05), \
                mock.patch.object(os, 'rename', side_effect=refusal()):
            self.assertIsNone(ps.link_consume(self.run_dir, token, time.time(), instance='i1'))
        self.assertIsNotNone(ps.link_consume(self.run_dir, token, time.time(), instance='i1'))   # nothing was used up


class AtomicWriteUnderTransientRefusals(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='skillsync-retry-')
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'file.json')

    def test_the_write_side_retries_a_refused_replace(self):
        flaky = FlakyOs(every=4)
        with mock.patch.object(ps, '_WINDOWS', True), mock.patch.object(os, 'replace', flaky.wrap(os.replace)):
            ps.atomic_write_bytes(self.path, b'{"v": 1}')
        self.assertEqual(Path(self.path).read_bytes(), b'{"v": 1}')

    def test_a_write_whose_replace_is_refused_for_good_fails_and_leaves_no_temporary(self):
        with mock.patch.object(ps, '_WINDOWS', True), mock.patch.object(ps, 'TRANSIENT_RETRY_SECONDS', 0.05), \
                mock.patch.object(os, 'replace', side_effect=refusal()):
            with self.assertRaises(PermissionError):
                ps.atomic_write_bytes(self.path, b'x')
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_writes_and_reads_in_parallel_never_lose_a_write_or_fail_a_read(self):
        ps.atomic_write_bytes(self.path, b'{"n": 0}')
        errors = []

        def writer(seed):
            for n in range(60):
                try:
                    ps.atomic_write_bytes(self.path, ('{"n": %d}' % (seed * 1000 + n)).encode())
                except BaseException as error:   # a lost write would show here
                    errors.append(('write', repr(error)))

        def reader():
            for _ in range(120):
                try:
                    with ps.retry_transient(lambda: open(self.path, 'rb')) as handle:
                        data = handle.read()
                    if not data.startswith(b'{"n": ') or not data.endswith(b'}'):
                        errors.append(('torn', data[:40]))
                except BaseException as error:
                    errors.append(('read', repr(error)))

        threads = [threading.Thread(target=writer, args=(seed,)) for seed in (1, 2)] + [threading.Thread(target=reader) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(errors, [])


class StoresRetryTheRead(unittest.TestCase):
    """The 500 of the defect: a store reading its file in the instant of another thread's replace."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='skillsync-retry-')
        self.addCleanup(self.tmp.cleanup)

    def test_the_questions_store_reads_through_a_refused_open(self):
        directory = os.path.join(self.tmp.name, 'root')
        clock = FakeClock(NOON)
        ps.QuestionsStore(directory, clock=clock).append([ask()])
        fresh = ps.QuestionsStore(directory, clock=clock)   # no cache: the first snapshot opens the file
        flaky = FlakyOs(every=3)
        with mock.patch.object(ps, '_WINDOWS', True), mock.patch.object(os, 'open', flaky.wrap(os.open)):
            snapshot = fresh.snapshot()
        self.assertTrue(snapshot.exists)
        self.assertGreaterEqual(flaky.counter, 3)   # the open was refused twice and went through

    def test_a_questions_store_reports_a_refusal_that_lasts(self):
        directory = os.path.join(self.tmp.name, 'root')
        clock = FakeClock(NOON)
        ps.QuestionsStore(directory, clock=clock).append([ask()])
        fresh = ps.QuestionsStore(directory, clock=clock)
        with mock.patch.object(ps, '_WINDOWS', True), mock.patch.object(ps, 'TRANSIENT_RETRY_SECONDS', 0.05), \
                mock.patch.object(os, 'open', side_effect=refusal()):
            with self.assertRaises(PermissionError):
                fresh.snapshot()

    def test_the_writing_marker_is_read_and_removed_through_refusals(self):
        directory = self.tmp.name
        payload = ps.write_writing(directory, int(time.time() * 1000), 600, 'agent')
        flaky = FlakyOs(every=3)
        with mock.patch.object(ps, '_WINDOWS', True), mock.patch.object(os, 'open', flaky.wrap(os.open)):
            marker = ps.read_writing(directory, int(time.time() * 1000))
        self.assertEqual((marker['by'], marker['expiresAt']), ('agent', payload['expiresAt']))
        with mock.patch.object(ps, '_WINDOWS', True), mock.patch.object(os, 'unlink', flaky.wrap(os.unlink)):
            self.assertTrue(ps.remove_writing(directory))


if __name__ == '__main__':
    unittest.main()
