"""`project.worktree` of the dashboard contract (K1-K3): the working directory kind read from `.git` without running git,
and the shared `gitdir`/`commondir` reader that `read_git` uses (its result stays the same)."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import sys as _sys_tmp_paths  # noqa: E402
from pathlib import Path as _Path_tmp_paths  # noqa: E402
_sys_tmp_paths.path.insert(0, str(_Path_tmp_paths(__file__).resolve().parent))  # noqa: E402
from tmp_paths import child  # noqa: E402
from skillsync_workflow import dashboard  # noqa: E402
from skillsync_workflow.pathfmt import posix  # noqa: E402
from layers_helpers import needs_mode_enforcement, needs_os_symlinks  # noqa: E402
from test_skillsync_dashboard import DashboardFixture, git_env, normalized  # noqa: E402

SHA = 'a' * 40


def git(cwd, *args, env=None):
    return subprocess.run(['git', '-C', str(cwd), '-c', 'commit.gpgsign=false', *args], check=True, capture_output=True,
                          text=True, timeout=60, env=git_env(env or os.environ)).stdout.strip()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


class Workdir(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skillsync-workdir-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()

    def repo(self, name='app'):
        root = child(self.base, name)
        root.mkdir()
        git(root, 'init', '-q', '-b', 'main')
        git(root, 'commit', '-q', '--allow-empty', '-m', 'init')
        return root

    def fake_worktree(self, name='F1', common_text=None, with_common=True):
        """A linked working directory made of files only: `.git` file -> `<main>/.git/worktrees/<name>` with `commondir`."""
        main = self.base / 'main-repo'
        admin = child(main / '.git' / 'worktrees', name)
        write(admin / 'HEAD', 'ref: refs/heads/feat/x\n')
        if with_common:
            write(admin / 'commondir', common_text if common_text is not None else '../..\n')
        root = child(self.base / 'wt', name)
        write(root / '.git', f'gitdir: {admin}\n')
        return root, main


class Recognition(Workdir):
    def test_directory_is_main(self):
        root = self.repo()
        self.assertEqual(dashboard.read_worktree(root), {'kind': 'main', 'name': 'app', 'path': posix(root), 'mainPath': None})

    def test_real_linked_worktree(self):
        main = self.repo()
        linked = self.base / 'wt' / 'F1'
        git(main, 'worktree', 'add', '-q', '-b', 'feat/x', str(linked))
        self.assertEqual(dashboard.read_worktree(linked),
                         {'kind': 'worktree', 'name': 'F1', 'path': posix(linked), 'mainPath': posix(main)})
        self.assertEqual(dashboard.read_worktree(main)['kind'], 'main')

    def test_gitdir_file_with_commondir_is_a_worktree(self):
        root, main = self.fake_worktree()
        self.assertEqual(dashboard.read_worktree(root),
                         {'kind': 'worktree', 'name': 'F1', 'path': posix(root), 'mainPath': posix(main)})

    def test_relative_gitdir_is_counted_from_the_root(self):
        root, main = self.fake_worktree()
        rel = os.path.relpath(main / '.git' / 'worktrees' / 'F1', root)
        write(root / '.git', f'gitdir: {rel}\n')
        self.assertEqual(dashboard.read_worktree(root)['mainPath'], posix(main))

    def test_common_dir_not_named_dot_git_gives_null_main_path(self):
        root, _ = self.fake_worktree(common_text='../../../bare.git\n')
        info = dashboard.read_worktree(root)
        self.assertEqual((info['kind'], info['mainPath']), ('worktree', None))

    def test_dot_git_directory_with_a_commondir_file_is_main(self):
        root = self.repo()
        write(root / '.git' / 'commondir', '../elsewhere\n')
        self.assertEqual(dashboard.read_worktree(root), {'kind': 'main', 'name': 'app', 'path': posix(root), 'mainPath': None})
        self.assertEqual(dashboard.read_git(root)['branch'], 'main')

    def test_commondir_to_a_missing_directory_is_a_worktree_with_a_missing_main_path(self):
        root, main = self.fake_worktree(common_text='../../../gone/.git\n')
        info = dashboard.read_worktree(root)
        self.assertEqual((info['kind'], info['mainPath']), ('worktree', posix((main / '.git' / 'worktrees' / 'F1' / '../../../gone').resolve())))
        self.assertFalse(Path(info['mainPath']).exists())

    def test_submodule_is_main(self):
        root = self.base / 'outer' / 'sub'
        write(root / '.git', 'gitdir: ../.git/modules/sub\n')
        write(self.base / 'outer' / '.git' / 'modules' / 'sub' / 'HEAD', 'ref: refs/heads/main\n')
        self.assertEqual(dashboard.read_worktree(root), {'kind': 'main', 'name': 'sub', 'path': posix(root), 'mainPath': None})

    def test_separate_git_dir_is_main(self):
        root = self.base / 'proj'
        root.mkdir()
        git(root, 'init', '-q', '--separate-git-dir', str(self.base / 'elsewhere'), '-b', 'main')
        self.assertTrue((root / '.git').is_file())
        self.assertEqual(dashboard.read_worktree(root)['kind'], 'main')
        self.assertIsNone(dashboard.read_worktree(root)['mainPath'])

    def test_missing_gitdir_directory_is_null_not_main(self):
        root, main = self.fake_worktree()
        shutil.rmtree(main / '.git' / 'worktrees' / 'F1')
        self.assertIsNone(dashboard.read_worktree(root))

    @needs_mode_enforcement
    def test_unreadable_commondir_is_null(self):
        root, main = self.fake_worktree(with_common=False)
        common = main / '.git' / 'worktrees' / 'F1' / 'commondir'
        write(common, '../..\n')
        common.chmod(0)
        self.addCleanup(common.chmod, 0o644)
        self.assertIsNone(dashboard.read_worktree(root))

    def test_garbage_dot_git_file_is_null(self):
        root = self.base / 'junk'
        write(root / '.git', 'not a gitdir line\n')
        self.assertIsNone(dashboard.read_worktree(root))

    def test_no_dot_git_is_null(self):
        root = self.base / 'plain'
        root.mkdir()
        self.assertIsNone(dashboard.read_worktree(root))

    def test_subdirectory_of_a_repository_is_null(self):
        sub = self.repo() / 'docs' / 'deep'
        sub.mkdir(parents=True)
        self.assertIsNone(dashboard.read_worktree(sub))

    @needs_os_symlinks
    def test_symlink_keeps_path_and_resolves_main_path(self):
        root, main = self.fake_worktree()
        real_main = self.base / 'real-main'
        main.rename(real_main)
        link = self.base / 'link-main'
        link.symlink_to(real_main, target_is_directory=True)
        write(root / '.git', f'gitdir: {link / ".git" / "worktrees" / "F1"}\n')
        shortcut = self.base / 'shortcut'
        shortcut.symlink_to(root, target_is_directory=True)
        info = dashboard.read_worktree(shortcut)
        self.assertEqual(info['path'], posix(shortcut))
        self.assertEqual(info['mainPath'], posix(real_main))
        self.assertEqual(info['name'], 'shortcut')

    def test_recognition_runs_no_process(self):
        main = self.repo()
        linked = self.base / 'wt' / 'F1'
        git(main, 'worktree', 'add', '-q', '-b', 'feat/x', str(linked))
        def boom(*args, **kwargs):
            raise AssertionError('a process was started')
        with mock.patch.dict(os.environ, {'PATH': ''}), mock.patch('subprocess.run', boom), mock.patch('subprocess.Popen', boom), \
                mock.patch('subprocess.check_output', boom), mock.patch('os.system', boom):
            self.assertEqual(dashboard.read_worktree(linked)['kind'], 'worktree')
            self.assertEqual(dashboard.read_worktree(main)['kind'], 'main')
            self.assertIsNotNone(dashboard.read_git(linked)['branch'])


class ReadGitUnchanged(Workdir):
    def test_read_git_result_of_a_linked_worktree_and_of_a_directory(self):
        main = self.repo()
        sha = git(main, 'rev-parse', 'HEAD')
        linked = self.base / 'wt' / 'F1'
        git(main, 'worktree', 'add', '-q', '-b', 'feat/x', str(linked))
        git(main, 'remote', 'add', 'origin', 'https://example.invalid/org/app.git')
        want = {'branch': 'main', 'head': sha[:12], 'headFull': sha, 'remote': None, 'branchUrl': None, 'branchPushed': None}
        self.assertEqual(dashboard.read_git(main), want)
        self.assertEqual(dashboard.read_git(linked), {**want, 'branch': 'feat/x'})

    def test_read_git_without_repository_and_with_bad_gitdir_keeps_the_empty_result(self):
        empty = {'branch': None, 'head': None, 'headFull': None, 'remote': None, 'branchUrl': None, 'branchPushed': None}
        plain = self.base / 'plain'
        plain.mkdir()
        junk = self.base / 'junk'
        write(junk / '.git', 'nonsense\n')
        self.assertEqual(dashboard.read_git(plain), empty)
        self.assertEqual(dashboard.read_git(junk), empty)

    def test_a_nul_byte_in_gitdir_keeps_the_empty_result_and_a_null_worktree(self):
        empty = {'branch': None, 'head': None, 'headFull': None, 'remote': None, 'branchUrl': None, 'branchPushed': None}
        root = self.base / 'nul-byte-gitdir'
        write(root / '.git', 'gitdir: a\x00b\n')
        self.assertEqual(dashboard.read_git(root), empty)
        self.assertIsNone(dashboard.read_worktree(root))

    def test_both_use_the_one_shared_reader(self):
        root, _ = self.fake_worktree()
        with mock.patch.object(dashboard, 'git_locations', wraps=dashboard.git_locations) as spy:
            dashboard.read_git(root)
            dashboard.read_worktree(root)
        self.assertEqual(spy.call_count, 2)


class StatusField(DashboardFixture):
    def test_status_of_a_linked_worktree_and_of_a_plain_directory(self):
        main = self.base / 'main-app'
        main.mkdir()
        write(main / 'README.md', 'demo\n')
        write(main / '.github' / '.skillsync.yaml', 'delivery:\n  mode: direct\n  base: main\n')
        git(main, 'init', '-q', '-b', 'main', env=self.env)
        git(main, 'add', '-A', env=self.env)
        git(main, 'commit', '-q', '-m', 'demo', env=self.env)
        linked = self.base / 'wt' / 'demo'
        git(main, 'worktree', 'add', '-q', '-b', 'feat/demo', str(linked), env=self.env)
        report = self.cli('dashboard', 'show', linked, '--json')
        data = json.loads(normalized(report))   # the fixture normalization: path -> <root>, mainPath -> <main>
        self.assertEqual(data['project'], {'name': 'demo', 'path': '<root>', 'worktree':
                                           {'kind': 'worktree', 'name': 'demo', 'path': '<root>', 'mainPath': '<main>'}})
        self.assertEqual(report['project']['worktree']['mainPath'], posix(main.resolve()))
        self.assertIsNone(self.cli('dashboard', 'show', self.project('empty'), '--json')['project']['worktree'])


if __name__ == '__main__':
    unittest.main()
