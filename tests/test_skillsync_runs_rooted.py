"""`runs._is_rooted`: a path in run metadata (evidence, benchmark fixture) must stay relative on every system."""
import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import runs  # noqa: E402


class IsRootedTests(unittest.TestCase):
    def test_relative_paths_stay_relative(self):
        for value in ('a.log', 'docs/a.log', 'docs\\a.log', 'build:log.txt', './a', 'a/b/c.txt'):
            with self.subTest(value=value):
                self.assertFalse(runs._is_rooted(value))

    def test_posix_rooted_paths_are_rooted_on_every_system(self):
        # `Path('/etc/hostname').is_absolute()` is False on Windows (no drive): the guard must not depend on it
        for value in ('/etc/hostname', '/', '//server/share/x'):
            with self.subTest(value=value):
                self.assertTrue(runs._is_rooted(value))

    def test_windows_rooted_and_drive_paths_are_rooted_on_windows(self):
        with unittest.mock.patch.object(runs.os, 'name', 'nt'):
            for value in ('\\etc\\hostname', 'C:\\x', 'C:x', '/etc/hostname', '\\\\server\\share\\x'):
                with self.subTest(value=value):
                    self.assertTrue(runs._is_rooted(value))
            self.assertFalse(runs._is_rooted('docs\\a.log'))
            self.assertFalse(runs._is_rooted('a.log'))


if __name__ == '__main__':
    unittest.main()
