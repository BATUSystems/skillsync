"""`common.normalize_files`: the containment of a list of files is checked on the path itself (the parent is resolved, the last component is
not followed), so a link that lies in the project is not an escape; a link directory to the outside, `..` and an absolute path still are."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow.common import contained, normalize_files  # noqa: E402


@unittest.skipUnless(hasattr(os, 'symlink') and os.name != 'nt', 'POSIX: symlinks')
class NormalizeFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='normfiles-')).resolve()
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmp, ignore_errors=True))
        self.root = self.tmp / 'proj'
        self.outside = self.tmp / 'layer' / 'skills' / 'foo'
        self.outside.mkdir(parents=True)
        (self.outside / 'SKILL.md').write_text('x\n', encoding='utf-8')
        (self.root / '.github' / 'skills').mkdir(parents=True)
        (self.root / 'docs').mkdir()
        (self.root / 'docs' / 'a.md').write_text('a\n', encoding='utf-8')
        os.symlink(self.outside, self.root / '.github' / 'skills' / 'foo')        # a tracked skill link: points outside, lies inside
        os.symlink(self.tmp / 'layer', self.root / 'linkdir')                      # a directory link to the outside

    def test_a_link_that_lies_in_the_project_is_a_file_of_the_project(self):
        self.assertEqual(normalize_files(self.root, ['.github/skills/foo', 'docs/a.md']), ['.github/skills/foo', 'docs/a.md'])

    def test_the_old_check_still_follows_the_link(self):
        with self.assertRaises(ValueError):
            contained(self.root, '.github/skills/foo')

    def test_a_dangling_link_and_a_deleted_file_are_names_in_the_project(self):
        os.symlink(self.tmp / 'gone', self.root / 'dangling')
        self.assertEqual(normalize_files(self.root, ['dangling', 'docs/deleted.md']), ['dangling', 'docs/deleted.md'])

    def test_a_directory_link_to_the_outside_in_the_middle_is_an_escape(self):
        for path in ('linkdir/skills/foo/SKILL.md', 'linkdir/x.md'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                normalize_files(self.root, [path])

    def test_a_link_in_the_middle_that_points_back_into_the_project_is_followed(self):
        os.symlink(self.root / 'docs', self.root / 'alias')
        self.assertEqual(normalize_files(self.root, ['alias/a.md']), ['docs/a.md'])

    def test_dotdot_and_absolute_paths_outside_are_escapes(self):
        for path in ('../x.md', 'docs/../../x.md', str(self.tmp / 'other.md'), '.github/skills/foo/../../../../x'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                normalize_files(self.root, [path])

    def test_dotdot_inside_the_project_and_an_absolute_path_inside_are_normalized(self):
        self.assertEqual(normalize_files(self.root, ['docs/../docs/a.md', str(self.root / 'docs' / 'a.md')]), ['docs/a.md', 'docs/a.md'])

    def test_a_dotdot_through_a_link_follows_the_real_directory(self):
        # `.github/skills/foo/..` is the real parent of the layer skill (outside), not `.github/skills`
        with self.assertRaises(ValueError):
            normalize_files(self.root, ['.github/skills/foo/../x.md'])

    def test_the_root_and_a_directory_are_normalized(self):
        self.assertEqual(normalize_files(self.root, ['.', 'docs']), ['.', 'docs'])

    def test_a_file_through_a_project_symlink_to_a_project_file_keeps_its_own_name(self):
        os.symlink(self.root / 'docs' / 'a.md', self.root / 'b.md')
        self.assertEqual(normalize_files(self.root, ['b.md']), ['b.md'])


if __name__ == '__main__':
    unittest.main()
