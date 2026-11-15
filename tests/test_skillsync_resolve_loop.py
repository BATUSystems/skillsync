"""`common.resolve_loop_checked`: a symlink loop is reported on every Python version.

`Path.resolve()` stopped raising on a loop in newer Pythons (POSIX 3.13, Windows 3.12): it returns the path it got stuck at, which
still has a symlink among its components. The injected `is_symlink` answers make the checks run the same on every system; one more
tests that make real loops are in test_skillsync_retro (RetroAll)."""
import errno
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillsync_workflow import common  # noqa: E402


class ResolveLoopChecked(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='skillsync-loop-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_an_existing_and_a_missing_path_resolve(self):
        self.assertEqual(common.resolve_loop_checked(self.base), self.base.resolve())
        missing = self.base / 'nie-ma' / 'x'
        self.assertEqual(common.resolve_loop_checked(missing), missing.resolve())

    def test_a_resolved_path_that_still_has_a_link_is_a_loop(self):
        stuck = self.base.resolve() / 'loop-b'
        with mock.patch.object(Path, 'resolve', lambda self, strict=False: stuck / 'x'), \
                mock.patch.object(Path, 'is_symlink', lambda self: self == stuck):
            with self.assertRaises(OSError) as caught:
                common.resolve_loop_checked(self.base / 'loop-a' / 'x')
        self.assertEqual(caught.exception.errno, errno.ELOOP)

    def test_a_resolved_path_that_still_has_a_junction_is_a_loop(self):
        # Windows, Python 3.12+: a loop of junctions comes back as the stuck path; a junction is not a symlink but `os.path.isjunction`
        stuck = self.base.resolve() / 'loop-b'
        with mock.patch.object(Path, 'resolve', lambda self, strict=False: stuck / 'x'), \
                mock.patch.object(Path, 'is_symlink', lambda self: False), \
                mock.patch.object(common.os.path, 'isjunction', lambda part: Path(part) == stuck, create=True):
            with self.assertRaises(OSError) as caught:
                common.resolve_loop_checked(self.base / 'loop-a' / 'x')
        self.assertEqual(caught.exception.errno, errno.ELOOP)

    def test_a_real_loop_of_junctions_is_reported_on_windows(self):
        if os.name != 'nt':   # no junctions elsewhere; not a skip, so that no baseline entry is needed
            return
        a, b = self.base / 'a', self.base / 'b'
        b.mkdir()
        for link, target in ((a, b),):
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], check=True, capture_output=True)
        b.rmdir()
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(b), str(a)], check=True, capture_output=True)
        with self.assertRaises((OSError, RuntimeError)):   # Python 3.11: `resolve()` itself raises RuntimeError on the loop
            common.resolve_loop_checked(a / 'x')

    def test_a_python_without_isjunction_keeps_working(self):
        with mock.patch.object(Path, 'is_symlink', lambda self: False), mock.patch.object(common.os.path, 'isjunction', None, create=True):
            self.assertEqual(common.resolve_loop_checked(self.base / 'x'), (self.base / 'x').resolve())

    def test_a_path_without_links_is_not_a_loop(self):
        with mock.patch.object(Path, 'is_symlink', lambda self: False):
            self.assertEqual(common.resolve_loop_checked(self.base / 'x'), (self.base / 'x').resolve())

    def test_the_users_of_the_helper_treat_a_loop_as_unresolvable(self):
        with mock.patch.object(common, 'resolve_loop_checked', side_effect=OSError(errno.ELOOP, 'loop')):
            self.assertTrue(common.is_excluded(self.base / 'x', [self.base / 'elsewhere']))
            with self.assertRaises(ValueError):
                common.discover_excludes({'discover_exclude': [str(self.base / 'x')]})


if __name__ == '__main__':
    unittest.main()
