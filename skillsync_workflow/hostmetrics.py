"""Host metrics for `skillsync resources`: available memory and CPU busy fraction on Linux, macOS and Windows (read only, no dependencies).

Sources: Linux `MemAvailable` of `/proc/meminfo`; macOS `vm_stat` (`free_count + inactive`, the pages the system gives back at once); Windows
`GlobalMemoryStatusEx` (`ullAvailPhys`) and, for the load, two samples of `GetSystemTimes` (a busy fraction of the CPU, not a load average).
Every system call is injectable, so the parsers and the sampling are tested on any system with fakes. A failing source returns `None`
(never an exception): the limit built from it is then `null` and `resources` says so.

Known limits: the busy fraction is the mean of one short window (`SAMPLE_SECONDS`), more jumpy than a one-minute load average, and it does not
see the depth of the run queue; machines with more than 64 logical processors (Windows processor groups) were not checked; a container or
cgroup limit is not seen on any system.
"""
import ctypes
import os
import platform
import re
import subprocess
import time

SAMPLE = object()          # `collect(busy=SAMPLE)`: "use the default CPU sample"; `None` means "no sample"
SAMPLE_SECONDS = 0.5       # distance of the two `GetSystemTimes` samples (the counter has a grain of about 15.6 ms)
VM_STAT_TIMEOUT = 3        # seconds

MEM_SOURCES = {'Linux': 'proc-meminfo', 'Darwin': 'vm_stat', 'Windows': 'global-memory-status'}


class MemoryStatus(ctypes.Structure):
    """`MEMORYSTATUSEX` of the Windows API (DWORD = 32 bits, DWORDLONG = 64 bits); a plain structure, usable on every system."""
    _fields_ = [('dwLength', ctypes.c_uint32), ('dwMemoryLoad', ctypes.c_uint32),
                ('ullTotalPhys', ctypes.c_uint64), ('ullAvailPhys', ctypes.c_uint64),
                ('ullTotalPageFile', ctypes.c_uint64), ('ullAvailPageFile', ctypes.c_uint64),
                ('ullTotalVirtual', ctypes.c_uint64), ('ullAvailVirtual', ctypes.c_uint64),
                ('ullAvailExtendedVirtual', ctypes.c_uint64)]


class FileTime(ctypes.Structure):
    """`FILETIME`: 100 ns ticks as two 32-bit halves."""
    _fields_ = [('dwLowDateTime', ctypes.c_uint32), ('dwHighDateTime', ctypes.c_uint32)]


def system_name():
    return platform.system()


def kernel32():
    """The `kernel32` library with typed prototypes, or None when it does not exist (not Windows) or cannot be loaded."""
    try:
        library = ctypes.WinDLL('kernel32', use_last_error=True)
        library.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
        library.GlobalMemoryStatusEx.restype = ctypes.c_int
        library.GetSystemTimes.argtypes = [ctypes.POINTER(FileTime)] * 3
        library.GetSystemTimes.restype = ctypes.c_int
        return library
    except (AttributeError, OSError):
        return None


def read_mem_proc(path='/proc/meminfo'):
    """`MemAvailable` of `/proc/meminfo` in MiB, or None (no such file, no access, no such line)."""
    try:
        with open(path, encoding='ascii', errors='replace') as handle:
            for line in handle:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1]) // 1024   # kB -> MiB
    except (OSError, ValueError, IndexError):
        pass
    return None


def parse_vm_stat(text):
    """Available MiB of the output of `vm_stat`: (`Pages free` + `Pages speculative` + `Pages inactive`) * page size, or None.
    `Pages free` already excludes the speculative pages, so the sum is `free_count + inactive`. The page size is read from the header
    (4096 bytes on Intel, 16384 on Apple silicon); a missing header, a missing line or a value that is not a number gives None."""
    match = re.search(r'page size of (\d+) bytes', text or '')
    if not match or int(match.group(1)) <= 0:
        return None
    pages = 0
    for name in ('Pages free', 'Pages speculative', 'Pages inactive'):
        found = re.search(rf'^{name}:\s+(\d+)\.?\s*$', text, re.MULTILINE)
        if not found:
            return None
        pages += int(found.group(1))
    return pages * int(match.group(1)) // (1024 * 1024)


def read_mem_macos(run=subprocess.run):
    """Available MiB from `vm_stat`, or None (no command, exit code other than 0, timeout, unreadable text)."""
    try:
        done = run(['vm_stat'], capture_output=True, text=True, timeout=VM_STAT_TIMEOUT, check=False, env={**os.environ, 'LC_ALL': 'C'})
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if getattr(done, 'returncode', 1) != 0:
        return None
    return parse_vm_stat(getattr(done, 'stdout', '') or '')


def read_mem_windows(library=None):
    """Available physical memory in MiB from `GlobalMemoryStatusEx` (`ullAvailPhys`), or None (no `kernel32`, API error)."""
    library = library if library is not None else kernel32()
    if library is None:
        return None
    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)   # the API needs the size before the call
    try:
        if not library.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
    except (OSError, AttributeError, ValueError, ctypes.ArgumentError):
        return None
    return int(status.ullAvailPhys) // (1024 * 1024)


def read_mem_available_mib(system=None):
    """Available memory in MiB on this system (`MemAvailable`, `vm_stat`, `GlobalMemoryStatusEx`), or None."""
    system = system or system_name()
    if system == 'Linux':
        return read_mem_proc()
    if system == 'Darwin':
        return read_mem_macos()
    if system == 'Windows':
        return read_mem_windows()
    return None


def mem_source(system=None):
    """Name of the memory source of the system (`sources.mem`), or None."""
    return MEM_SOURCES.get(system or system_name())


def sample_system_times(library=None):
    """`(idle, kernel, user)` of `GetSystemTimes` in 100 ns ticks (`kernel` includes `idle`), or None."""
    library = library if library is not None else kernel32()
    if library is None:
        return None
    idle, kernel, user = FileTime(), FileTime(), FileTime()
    try:
        if not library.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            return None
    except (OSError, AttributeError, ValueError, ctypes.ArgumentError):
        return None
    return tuple(int(item.dwLowDateTime) | (int(item.dwHighDateTime) << 32) for item in (idle, kernel, user))


def busy_fraction(first, second):
    """Busy fraction 0..1 of the CPU between two `(idle, kernel, user)` samples: 1 - d(idle) / (d(kernel) + d(user)); None when the
    window is empty or a counter went back."""
    if not first or not second:
        return None
    idle, kernel, user = (b - a for a, b in zip(first, second))
    total = kernel + user
    if idle < 0 or kernel < 0 or user < 0 or total <= 0:
        return None
    return min(1.0, max(0.0, 1.0 - idle / total))


def read_cpu_busy(interval=SAMPLE_SECONDS, sample=None, sleep=time.sleep):
    """Busy fraction 0..1 of the CPU over `interval` seconds on Windows (two samples of `GetSystemTimes`), or None (other system,
    API error). `sample` and `sleep` are injectable."""
    if sample is None:
        if system_name() != 'Windows':
            return None
        sample = sample_system_times
    first = sample()
    if first is None:
        return None
    sleep(interval)
    second = sample()
    return busy_fraction(first, second) if second is not None else None
