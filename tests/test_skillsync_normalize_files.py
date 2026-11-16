"""`common.normalize_files`: the containment of a list of files is checked on the path itself (the parent is resolved, the last component is
not followed), so a link that lies in the project is not an escape; a link directory to the outside, `..` and an absolute path still are.
`NormalizeFilesPlain` has no links and runs on every system; `NormalizeFilesLinks` plants links and needs a system that can create them."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from layers_helpers import needs_os_symlinks  # noqa: E402
from skillsync_workflow.common import contained, normalize_files  # noqa: E402


class Base(unittest.TestCase):
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

    def refused(self, *paths):
        for path in paths:
            with self.subTest(path=path), self.assertRaises(ValueError):
                normalize_files(self.root, [path])


class NormalizeFilesPlain(Base):
    """No links: the same answers on every system."""

    def test_dotdot_and_absolute_paths_outside_are_escapes(self):
        self.refused('../x.md', 'docs/../../x.md', '..', 'docs/../..', str(self.tmp / 'other.md'), str(self.tmp))

    def test_dotdot_inside_the_project_and_an_absolute_path_inside_are_normalized(self):
        self.assertEqual(normalize_files(self.root, ['docs/../docs/a.md', str(self.root / 'docs' / 'a.md'), 'docs/..']),
                         ['docs/a.md', 'docs/a.md', '.'])

    def test_the_root_a_dot_an_empty_name_and_a_trailing_slash_are_normalized(self):
        self.assertEqual(normalize_files(self.root, ['.', './docs', 'docs/', 'docs/./a.md', str(self.root)]), ['.', 'docs', 'docs', 'docs/a.md', '.'])

    def test_an_empty_name_is_the_project_root(self):
        self.assertEqual(normalize_files(self.root, ['']), ['.'])

    def test_a_missing_file_and_a_missing_parent_are_names_in_the_project(self):
        self.assertEqual(normalize_files(self.root, ['docs/deleted.md', 'gone/dir/file.md']), ['docs/deleted.md', 'gone/dir/file.md'])

    def test_a_backslash_follows_the_rules_of_the_system(self):
        if os.name == 'nt':   # a separator
            self.assertEqual(normalize_files(self.root, ['docs\\a.md']), ['docs/a.md'])
            self.refused('..\\x.md')
        else:                 # an ordinary character of the name: a file of the project, never an escape
            self.assertEqual(normalize_files(self.root, ['docs\\a.md', '..\\x.md']), ['docs\\a.md', '..\\x.md'])

    def test_the_old_check_agrees_on_plain_paths(self):
        for path in ('docs/a.md', 'docs', '.'):
            self.assertEqual(contained(self.root, path).relative_to(self.root.resolve()).as_posix(), normalize_files(self.root, [path])[0])


@needs_os_symlinks
class NormalizeFilesLinks(Base):
    def setUp(self):
        super().setUp()
        os.symlink(self.outside, self.root / '.github' / 'skills' / 'foo', target_is_directory=True)   # a tracked skill link: points outside, lies inside
        os.symlink(self.tmp / 'layer', self.root / 'linkdir', target_is_directory=True)                  # a directory link to the outside

    def test_a_link_that_lies_in_the_project_is_a_file_of_the_project(self):
        self.assertEqual(normalize_files(self.root, ['.github/skills/foo', 'docs/a.md']), ['.github/skills/foo', 'docs/a.md'])

    def test_a_link_to_a_directory_outside_as_the_last_component_passes_with_and_without_a_trailing_slash(self):
        self.assertEqual(normalize_files(self.root, ['linkdir', 'linkdir/', '.github/skills/foo/']), ['linkdir', 'linkdir', '.github/skills/foo'])

    def test_the_old_check_still_follows_the_link(self):
        with self.assertRaises(ValueError):
            contained(self.root, '.github/skills/foo')

    def test_a_dangling_link_is_a_name_in_the_project(self):
        os.symlink(self.tmp / 'gone', self.root / 'dangling')
        self.assertEqual(normalize_files(self.root, ['dangling']), ['dangling'])

    def test_a_directory_link_to_the_outside_in_the_middle_is_an_escape(self):
        self.refused('linkdir/skills/foo/SKILL.md', 'linkdir/x.md', 'linkdir/new/deep.md')

    def test_a_link_in_the_middle_that_points_back_into_the_project_is_followed(self):
        os.symlink(self.root / 'docs', self.root / 'alias', target_is_directory=True)
        self.assertEqual(normalize_files(self.root, ['alias/a.md']), ['docs/a.md'])

    def test_dotdot_through_a_link_follows_the_real_directory(self):
        # `.github/skills/foo/..` is the real parent of the layer skill (outside), not `.github/skills`: in the middle and as the last component
        self.refused('.github/skills/foo/../x.md', '.github/skills/foo/..', 'linkdir/..')

    def test_a_file_through_a_project_symlink_to_a_project_file_keeps_its_own_name(self):
        os.symlink(self.root / 'docs' / 'a.md', self.root / 'b.md')
        self.assertEqual(normalize_files(self.root, ['b.md']), ['b.md'])


if __name__ == '__main__':
    unittest.main()
