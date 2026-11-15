"""Python 3.13 on Windows: `ntpath.isabs('/x')` is False (it was True). Checks that rely on a path rooted with `/` keep their meaning.

The 3.13 behaviour is simulated on every system (a mock of `os.path.isabs`, `os.name` and a `realpath` that puts a drive in front of a
rooted path as Windows does); nothing here instantiates a foreign `Path` class."""
import posixpath
import re
import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import fsutil, layers  # noqa: E402


def isabs_313(path):
    """`ntpath.isabs` of Python 3.13: only a drive with a root (`C:/x`) or a UNC path is absolute."""
    text = str(path).replace('\\', '/')
    return bool(re.match(r'^[A-Za-z]:/', text)) or text.startswith('//')


def realpath_windows(path):
    """What `os.path.realpath('/a/b')` gives on Windows: the drive of the current directory in front."""
    return 'C:' + path if path.startswith('/') else path


class Windows313(unittest.TestCase):
    def simulate(self):
        stack = unittest.mock.patch.multiple(fsutil.os, name='nt')
        stack.start()
        self.addCleanup(stack.stop)
        for target, value in (('isabs', isabs_313), ('realpath', realpath_windows)):
            patch = unittest.mock.patch.object(fsutil.os.path, target, value)
            patch.start()
            self.addCleanup(patch.stop)


class IsabsHelper(Windows313):
    def test_a_rooted_path_without_a_drive_is_absolute_on_windows_whatever_python_says(self):
        self.simulate()
        for value in ('/a/b', '\\a\\b', 'C:/a', 'C:\\a'):
            with self.subTest(value=value):
                self.assertTrue(fsutil.isabs(value))
        for value in ('a/b', 'C:a', '', '.'):
            with self.subTest(value=value):
                self.assertFalse(fsutil.isabs(value))

    def test_posix_is_unchanged(self):
        with unittest.mock.patch.object(fsutil.os, 'name', 'posix'), unittest.mock.patch.object(fsutil.os.path, 'isabs', posixpath.isabs):
            self.assertTrue(fsutil.isabs('/a/b'))
            self.assertFalse(fsutil.isabs('\\a'), 'a backslash is a name character there')
            self.assertFalse(fsutil.isabs('a/b'))


class LayerRules(Windows313):
    def test_a_rule_with_a_rooted_pattern_matches_on_windows_python_313(self):
        self.simulate()
        self.assertTrue(layers.path_matches('/a/b/c', '/a/**', 'Windows'))
        self.assertTrue(layers.path_matches('/a/b/c', '/a/b/*', 'Windows'))
        self.assertFalse(layers.path_matches('/x/b/c', '/a/**', 'Windows'))

    def test_the_config_keeps_a_rooted_path_as_written(self):
        self.simulate()
        self.assertEqual(layers._stored_path('/a/b'), '/a/b')


if __name__ == '__main__':
    unittest.main()
