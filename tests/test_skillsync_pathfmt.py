"""`pathfmt.posix`: `/` in the path fields of reports on Windows only."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow.pathfmt import posix  # noqa: E402


class PosixTests(unittest.TestCase):
    def test_windows_gets_slashes(self):
        with mock.patch.object(os, 'name', 'nt'):
            self.assertEqual(posix('D:\\work\\docs\\README.md'), 'D:/work/docs/README.md')
            self.assertEqual(posix('docs/a.md'), 'docs/a.md')

    def test_other_systems_keep_a_backslash_as_part_of_the_name(self):
        with mock.patch.object(os, 'name', 'posix'):
            self.assertEqual(posix('/w/a\\b'), '/w/a\\b')

    def test_a_path_object_gives_text(self):
        path = Path('/w/a')       # made before the patch: pathlib cannot make a PosixPath under a patched `os.name` on Windows
        expected = str(path)
        with mock.patch.object(os, 'name', 'posix'):
            self.assertEqual(posix(path), expected)


if __name__ == '__main__':
    unittest.main()
