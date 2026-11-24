"""`skillsync_workflow/hostmetrics.py`: available memory and CPU busy fraction of the host, tested with fakes on every system."""
import ctypes
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow import hostmetrics  # noqa: E402

VM_STAT_INTEL = """Mach Virtual Memory Statistics: (page size of 4096 bytes)
Pages free:                              436530.
Pages active:                           1502259.
Pages inactive:                         1347887.
Pages speculative:                       218635.
Pages throttled:                              0.
Pages wired down:                        635631.
Pages purgeable:                         176329.
"Translation faults":                1295459462.
"""
VM_STAT_ARM = VM_STAT_INTEL.replace('page size of 4096 bytes', 'page size of 16384 bytes')


class FakeKernel32:
    """A stand-in for `kernel32`: fills the structures given by `ctypes.byref`, as the real API does."""

    def __init__(self, avail_bytes=8 * 1024 ** 3, memory_ok=1, times=None, times_ok=1):
        self.avail_bytes, self.memory_ok, self.times, self.times_ok = avail_bytes, memory_ok, list(times or []), times_ok
        self.seen_length = None

    def GlobalMemoryStatusEx(self, ref):
        status = ref._obj
        self.seen_length = status.dwLength
        status.ullAvailPhys = self.avail_bytes
        return self.memory_ok

    def GetSystemTimes(self, idle_ref, kernel_ref, user_ref):
        if not self.times_ok:
            return 0
        idle, kernel, user = self.times.pop(0)
        for ref, value in ((idle_ref, idle), (kernel_ref, kernel), (user_ref, user)):
            ref._obj.dwLowDateTime, ref._obj.dwHighDateTime = value & 0xFFFFFFFF, value >> 32
        return 1


class VmStat(unittest.TestCase):
    def test_the_sum_is_free_speculative_and_inactive_in_pages(self):
        pages = 436530 + 218635 + 1347887
        self.assertEqual(hostmetrics.parse_vm_stat(VM_STAT_INTEL), pages * 4096 // (1024 * 1024))

    def test_the_page_size_comes_from_the_header(self):
        pages = 436530 + 218635 + 1347887
        self.assertEqual(hostmetrics.parse_vm_stat(VM_STAT_ARM), pages * 16384 // (1024 * 1024))

    def test_a_missing_header_line_or_number_gives_none(self):
        self.assertIsNone(hostmetrics.parse_vm_stat(VM_STAT_INTEL.replace('(page size of 4096 bytes)', '')))
        self.assertIsNone(hostmetrics.parse_vm_stat(VM_STAT_INTEL.replace('Pages inactive:', 'Pages idle:')))
        self.assertIsNone(hostmetrics.parse_vm_stat(VM_STAT_INTEL.replace('1347887.', 'many.')))
        self.assertIsNone(hostmetrics.parse_vm_stat('page size of 0 bytes\nPages free: 1.\nPages speculative: 1.\nPages inactive: 1.'))
        self.assertIsNone(hostmetrics.parse_vm_stat(''))
        self.assertIsNone(hostmetrics.parse_vm_stat(None))

    def test_read_runs_vm_stat_in_the_c_locale_with_a_timeout(self):
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, stdout=VM_STAT_INTEL, stderr='')
        self.assertGreater(hostmetrics.read_mem_macos(run=run), 0)
        command, kwargs = calls[0]
        self.assertEqual(command, ['vm_stat'])
        self.assertEqual(kwargs['env']['LC_ALL'], 'C')
        self.assertEqual(kwargs['timeout'], hostmetrics.VM_STAT_TIMEOUT)

    def test_every_failure_of_the_command_gives_none(self):
        def missing(command, **kwargs):
            raise FileNotFoundError('vm_stat')

        def slow(command, **kwargs):
            raise subprocess.TimeoutExpired(command, 3)

        def failing(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, stdout=VM_STAT_INTEL, stderr='')

        def garbage(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout='nothing useful', stderr='')
        for run in (missing, slow, failing, garbage):
            self.assertIsNone(hostmetrics.read_mem_macos(run=run))


class WindowsMemory(unittest.TestCase):
    def test_available_physical_memory_in_mib_and_the_size_is_set_first(self):
        fake = FakeKernel32(avail_bytes=12867 * 1024 * 1024)
        self.assertEqual(hostmetrics.read_mem_windows(fake), 12867)
        self.assertEqual(fake.seen_length, ctypes.sizeof(hostmetrics.MemoryStatus))

    def test_an_api_error_or_an_exception_gives_none(self):
        self.assertIsNone(hostmetrics.read_mem_windows(FakeKernel32(memory_ok=0)))

        class Raising:
            def GlobalMemoryStatusEx(self, ref):
                raise OSError('boom')
        self.assertIsNone(hostmetrics.read_mem_windows(Raising()))

    def test_no_kernel32_gives_none(self):
        if sys.platform != 'win32':
            self.assertIsNone(hostmetrics.kernel32())
            self.assertIsNone(hostmetrics.read_mem_windows())
            self.assertIsNone(hostmetrics.sample_system_times())


class CpuBusy(unittest.TestCase):
    def test_busy_fraction_of_two_samples(self):
        # kernel includes idle: window = 1000 kernel + 500 user, idle 750 -> busy = 1 - 750/1500
        self.assertAlmostEqual(hostmetrics.busy_fraction((0, 0, 0), (750, 1000, 500)), 0.5)
        self.assertEqual(hostmetrics.busy_fraction((10, 20, 5), (10, 20, 5)), None)               # empty window
        self.assertEqual(hostmetrics.busy_fraction((0, 0, 0), (1000, 1000, 0)), 0.0)              # idle machine
        self.assertEqual(hostmetrics.busy_fraction((0, 0, 0), (0, 1000, 0)), 1.0)                 # saturated machine
        self.assertIsNone(hostmetrics.busy_fraction((100, 100, 100), (50, 200, 200)))            # a counter went back
        self.assertIsNone(hostmetrics.busy_fraction(None, (1, 1, 1)))

    def test_busy_fraction_stays_between_zero_and_one(self):
        self.assertEqual(hostmetrics.busy_fraction((0, 0, 0), (2000, 1000, 0)), 0.0)

    def test_system_times_joins_the_two_halves_of_filetime(self):
        big = (3 << 32) | 7
        fake = FakeKernel32(times=[(big, big + 5, 9)])
        self.assertEqual(hostmetrics.sample_system_times(fake), (big, big + 5, 9))

    def test_a_failing_api_gives_none(self):
        self.assertIsNone(hostmetrics.sample_system_times(FakeKernel32(times_ok=0)))

    def test_read_cpu_busy_samples_twice_with_the_interval(self):
        samples = [(0, 0, 0), (750, 1000, 500)]
        slept = []
        busy = hostmetrics.read_cpu_busy(interval=0.5, sample=lambda: samples.pop(0), sleep=slept.append)
        self.assertAlmostEqual(busy, 0.5)
        self.assertEqual(slept, [0.5])

    def test_a_failing_first_or_second_sample_gives_none(self):
        slept = []
        self.assertIsNone(hostmetrics.read_cpu_busy(sample=lambda: None, sleep=slept.append))
        self.assertEqual(slept, [])
        samples = [(0, 0, 0), None]
        self.assertIsNone(hostmetrics.read_cpu_busy(sample=lambda: samples.pop(0), sleep=lambda seconds: None))

    def test_other_systems_do_not_sample(self):
        original = hostmetrics.system_name
        try:
            hostmetrics.system_name = lambda: 'Linux'
            self.assertIsNone(hostmetrics.read_cpu_busy(sleep=lambda seconds: self.fail('must not sleep')))
        finally:
            hostmetrics.system_name = original


class Dispatch(unittest.TestCase):
    def test_the_source_name_follows_the_system(self):
        self.assertEqual([hostmetrics.mem_source(name) for name in ('Linux', 'Darwin', 'Windows', 'Plan9')],
                         ['proc-meminfo', 'vm_stat', 'global-memory-status', None])

    def test_an_unknown_system_has_no_memory_reading(self):
        self.assertIsNone(hostmetrics.read_mem_available_mib('Plan9'))

    def test_proc_meminfo_reader_handles_a_missing_file_and_a_bad_number(self):
        self.assertIsNone(hostmetrics.read_mem_proc('/nonexistent/meminfo'))
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            good, bad = Path(folder, 'good'), Path(folder, 'bad')
            good.write_text('MemTotal: 100 kB\nMemAvailable: 2048 kB\n', encoding='ascii')
            bad.write_text('MemAvailable: lots kB\n', encoding='ascii')
            self.assertEqual(hostmetrics.read_mem_proc(str(good)), 2)
            self.assertIsNone(hostmetrics.read_mem_proc(str(bad)))


class ThisSystem(unittest.TestCase):
    """The real call of the system this test runs on (no skips: each system checks its own source)."""

    def test_the_memory_reading_of_this_system_is_a_positive_number(self):
        memory = hostmetrics.read_mem_available_mib()
        self.assertIsNotNone(memory, hostmetrics.system_name())
        self.assertGreater(memory, 0)

    def test_the_cpu_sample_of_windows_is_a_fraction_and_other_systems_give_none(self):
        busy = hostmetrics.read_cpu_busy(interval=0.05)
        if hostmetrics.system_name() == 'Windows':
            self.assertIsNotNone(busy)
            self.assertTrue(0.0 <= busy <= 1.0)
        else:
            self.assertIsNone(busy)


if __name__ == '__main__':
    unittest.main()
