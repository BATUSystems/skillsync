"""Python 3.13 on Windows: `ntpath.isabs('/x')` is False (it was True). Checks that rely on a path rooted with `/` keep their meaning.

The 3.13 behaviour is simulated on every system (a mock of `os.path.isabs`, `os.name` and a `realpath` that puts a drive in front of a
rooted path as Windows does); nothing here instantiates a foreign `Path` class."""
import ast
import os
import posixpath
import re
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import cli, fsutil, layers  # noqa: E402


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


class Sites(unittest.TestCase):
    """The sites that decide on `fsutil.isabs` (D: ISABS-SITE-TESTS): each one is reached through the helper, so the Python 3.13 meaning
    cannot come back by an `os.path.isabs` written next to it."""

    def test_env_dir_takes_a_rooted_value_through_the_helper(self):
        with unittest.mock.patch.dict(os.environ, {'SKILLSYNC_TEST_DIR': '/a/b'}):
            with unittest.mock.patch.object(layers, 'isabs', lambda value: True):
                self.assertEqual(layers._env_dir('SKILLSYNC_TEST_DIR'), Path('/a/b'))
            with unittest.mock.patch.object(layers, 'isabs', lambda value: False):
                self.assertIsNone(layers._env_dir('SKILLSYNC_TEST_DIR'))
        self.assertIsNone(layers._env_dir('SKILLSYNC_TEST_DIR_UNSET'))

    def test_a_dangling_link_with_a_rooted_target_is_judged_by_that_target(self):
        with tempfile.TemporaryDirectory(prefix='isabs-') as tmp:
            library = Path(tmp) / 'library'
            (library / 'skills').mkdir(parents=True)
            link = Path(tmp) / 'link'
            try:
                link.symlink_to(library / 'skills' / 'gone')
            except OSError:
                self.skipTest('symlinks unavailable')
            self.assertTrue(cli.is_library_symlink(link, [library]))
            # the target of a dangling link is judged through the helper (joined to the link's directory only when it is not absolute)
            spy = unittest.mock.Mock(wraps=fsutil.isabs)
            with unittest.mock.patch.object(cli, 'isabs', spy):
                self.assertTrue(cli.is_library_symlink(link, [library]))
            spy.assert_called_once_with(os.readlink(link))

    def test_layers_and_cli_name_no_os_path_isabs_of_their_own(self):
        """`os.path.isabs` may stay only where the argument is a path with a drive or a self command (never `/x` on Windows):
        `fsutil.isabs` itself, the hook word helper (it adds `startswith('/')`), command lines of `self_argv` (environment, leakguard)
        and the `which` result (selfcmd),  (it adds its own `startswith(('/', '\\'))`). Anywhere else a new call would bring the Python 3.13 meaning back."""
        allowed = {'fsutil.py': 1, 'leakguard.py': 2, 'environment.py': 1, 'selfcmd.py': 1, 'prototype.py': 1}
        found = {}
        for path in sorted((ROOT / 'skillsync_workflow').glob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            count = sum(1 for node in ast.walk(tree)
                        if isinstance(node, ast.Attribute) and node.attr == 'isabs'
                        and isinstance(node.value, ast.Attribute) and node.value.attr == 'path')
            if count:
                found[path.name] = count
        self.assertEqual(found, allowed, 'use `fsutil.isabs` (Windows + Python 3.13: `os.path.isabs("/x")` is False)')


class GuardHook(Windows313):
    """The delete guard hook is a standalone script (it cannot import `fsutil`), so it carries its own `isabs`; here its three sites
    (project `allow` entries, `resolve`, the prefix of a glob) are driven under the simulated Python 3.13 meaning."""
    ENV = {'HOME': '/home/user', 'SCRATCH': '/tmp/session/scratchpad', 'TMPDIR': '/tmp', 'SKILLSYNC_GUARD_TMP_ROOTS': '/tmp'}

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location('guard_commands_isabs', ROOT / 'library' / 'hooks' / 'guard-commands.py')
        cls.guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.guard)

    def checker(self, allow=()):
        return self.guard.Guard('/home/user/Repos/proj', '/tmp/session/scratchpad', list(allow), dict(self.ENV))

    def refused(self, command, allow=()):
        return bool(self.checker(allow).walk(command))

    def test_a_rooted_allow_entry_without_a_drive_is_an_allowed_root_on_python_313(self):
        self.simulate()
        self.assertFalse(self.refused('rm -rf /data/scratch/x', allow=['/data/scratch']))
        self.assertFalse(self.refused('rm -rf /data/scratch', allow=['/data/scratch']), 'an absolute entry is deletable itself (exact)')
        self.assertTrue(self.refused('rm -rf /data/other/x', allow=['/data/scratch']), 'only the entry is allowed, not its neighbour')

    def test_resolve_and_the_glob_prefix_decide_through_the_hooks_own_isabs(self):
        """Both sites reach `isabs` of the hook (a spy sees the rooted word); a bare `os.path.isabs` there would not."""
        self.simulate()
        guard = self.checker()
        spy = unittest.mock.Mock(wraps=self.guard.isabs)
        with unittest.mock.patch.object(self.guard, 'isabs', spy):
            self.assertEqual(guard.resolve('/data/x'), 'C:/data/x')
            spy.assert_any_call('/data/x')
            spy.reset_mock()
            self.assertTrue(self.refused('rm -rf /data/foo-*'))
            spy.assert_any_call('/data/foo-')
        self.assertFalse(self.refused('rm -rf /tmp/foo-*'), 'under a temporary root')

    def test_posix_meaning_is_unchanged_in_the_hook(self):
        with unittest.mock.patch.object(self.guard.os, 'name', 'posix'), unittest.mock.patch.object(self.guard.os.path, 'isabs', posixpath.isabs):
            self.assertTrue(self.guard.isabs('/a/b'))
            self.assertFalse(self.guard.isabs('\\a'))
            self.assertFalse(self.guard.isabs(''))

    def test_doubt_stays_a_refusal(self):
        self.simulate()
        self.assertTrue(self.refused('rm -rf /'))
        self.assertTrue(self.refused('rm -rf src'))
        self.assertTrue(self.refused('rm -rf /home/user/other/data'), 'outside the project and every allowed root')
        self.assertTrue(self.refused('rm -rf /data/scratch/x'), 'without the allow entry')

    def test_the_hook_names_no_os_path_isabs_of_its_own(self):
        """The rooted reading lives in ONE helper `isabs` of guard-commands.py; elsewhere in library/hooks no `isabs`/`is_absolute`/`splitroot`
        attribute (any base: `os.path`, `ntpath`, `Path`), no `from os.path import isabs` and no second helper."""
        names = {'isabs', 'is_absolute', 'splitroot'}
        found, scanned, helpers = [], [], 0
        for path in sorted((ROOT / 'library' / 'hooks').glob('*.py')):
            scanned.append(path.name)
            tree = ast.parse(path.read_text(encoding='utf-8'))
            defs = [h for h in ast.walk(tree) if isinstance(h, ast.FunctionDef) and h.name == 'isabs']
            helpers += len(defs)
            inside = {id(c) for h in defs for c in ast.walk(h)}
            for node in ast.walk(tree):
                if id(node) in inside:
                    continue
                if isinstance(node, ast.Attribute) and node.attr in names:
                    found.append('%s:%d' % (path.name, node.lineno))
                elif isinstance(node, ast.ImportFrom) and any(a.name in names for a in node.names):
                    found.append('%s:%d' % (path.name, node.lineno))
                elif isinstance(node, ast.Constant) and node.value in names:  # getattr(os.path, 'isabs')
                    found.append('%s:%d' % (path.name, node.lineno))
        self.assertIn('guard-commands.py', scanned)
        self.assertEqual(helpers, 1, 'one helper, in guard-commands.py')
        self.assertEqual(found, [], "use the hook's own `isabs` (Windows + Python 3.13: `os.path.isabs('/x')` is False)")


if __name__ == '__main__':
    unittest.main()
