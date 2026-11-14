"""`common.resolve_loop_checked`: a symlink loop is reported on every Python version.

`Path.resolve()` stopped raising on a loop in newer Pythons (POSIX 3.13, Windows 3.12); the loop then shows only when the result is
used. The checks inject the `os.stat` failures, so they run the same on every system without needing the right to make symlinks."""
import errno
import os
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

    def test_a_loop_reported_by_stat_raises_on_posix_and_on_windows(self):
        for error in (OSError(errno.ELOOP, 'Too many levels of symbolic links'), self.windows_error(1921)):
            with self.subTest(error=repr(error)), mock.patch.object(os, 'stat', side_effect=error):
                with self.assertRaises((OSError, RuntimeError)):   # older pathlib turns ELOOP into RuntimeError itself
                    common.resolve_loop_checked(self.base / 'loop' / 'x')

    def test_a_path_that_cannot_be_read_or_does_not_exist_is_not_a_loop(self):
        for error in (PermissionError(errno.EACCES, 'denied'), FileNotFoundError(errno.ENOENT, 'missing'),
                      NotADirectoryError(errno.ENOTDIR, 'not a directory')):
            with self.subTest(error=type(error).__name__), mock.patch.object(os, 'stat', side_effect=error):
                self.assertEqual(common.resolve_loop_checked(self.base / 'x'), (self.base / 'x').resolve())

    def test_the_users_of_the_helper_treat_a_loop_as_unresolvable(self):
        loop = OSError(errno.ELOOP, 'Too many levels of symbolic links')
        with mock.patch.object(os, 'stat', side_effect=loop):
            self.assertTrue(common.is_excluded(self.base / 'x', [self.base / 'elsewhere']))
            with self.assertRaises(ValueError):
                common.discover_excludes({'discover_exclude': [str(self.base / 'x')]})

    @staticmethod
    def windows_error(code):
        error = OSError(errno.EINVAL, 'The name of the file cannot be resolved by the system')
        error.winerror = code   # the attribute exists on Windows only; set by hand so the check runs everywhere
        return error


if __name__ == '__main__':
    unittest.main()
