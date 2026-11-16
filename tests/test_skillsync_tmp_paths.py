"""`tests/tmp_paths.py`: names from data never leave the temporary directory.

The case that started it: a name `\\n` (backslash, n) taken from data is, on Windows, a path from the drive root, so
`tmp / name` wrote outside the temporary directory (shown here with `PureWindowsPath`, which behaves the same on every system).
"""
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tmp_paths  # noqa: E402
from tmp_paths import UnsafeName  # noqa: E402


class TmpPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='skillsync-tmppaths-'))
        self.addCleanup(__import__('shutil').rmtree, self.tmp, ignore_errors=True)

    def test_backslash_n_is_a_rooted_path_on_windows(self):
        joined = PureWindowsPath('C:/tmp/work') / '\\n'
        self.assertEqual(joined, PureWindowsPath('C:/n'))  # the temporary directory is gone
        with self.assertRaises(UnsafeName):
            tmp_paths.child(self.tmp, '\\n')

    def test_refused_names(self):
        for name in ('', '.', '..', 'a/b', 'a\\b', '\\n', '/abs', 'C:x', 'C:\\x', 'a\nb', 'a\x00b', 'x.', 'x ', 'CON', 'con.txt', 'Nul', 'COM1',
                     'lpt9.log', 'a<b', 'a|b', 'a?b', 'a*b', '../x', 'a\tb', None, 5, b'x'):
            with self.subTest(name=name):
                with self.assertRaises(UnsafeName):
                    tmp_paths.child(self.tmp, name)

    def test_accepted_names_stay_inside(self):
        for name in ('plan-1.md', 'ünïcode', 'a b', '.hidden', 'COMMON', 'console.txt', 'x.y.z'):
            with self.subTest(name=name):
                path = tmp_paths.child(self.tmp, name)
                self.assertEqual(path.resolve().relative_to(self.tmp.resolve()), Path(name))

    def test_make_dir_and_write_file(self):
        directory = tmp_paths.make_dir(self.tmp, 'sub')
        self.assertTrue(directory.is_dir())
        written = tmp_paths.write_file(directory, 'a.txt', 'line\r\n')
        self.assertEqual(written.read_bytes(), b'line\r\n')
        with self.assertRaises(UnsafeName):
            tmp_paths.write_file(directory, '..', 'x')


if __name__ == '__main__':
    unittest.main()
