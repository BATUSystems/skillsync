"""K5: `selfcmd.self_argv()` in both forms (checkout, installation), the control grep and the launchers.

The installation form is produced by copying the package into an empty directory without the wrapper beside it
and asking a child process (cwd = another empty directory) for `self_argv()`.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.cli_module import ROOT, SKILLSYNC_PATH  # noqa: E402
from tests.layers_helpers import needs_os_symlinks, needs_sh  # noqa: E402
from skillsync_workflow import environment, selfcmd  # noqa: E402

PACKAGE = ROOT / 'skillsync_workflow'
INSTALLED = [sys.executable, '-P', '-m', 'skillsync_workflow']
CHILD = ('import json, sys\n'
         'from skillsync_workflow import selfcmd, leakguard\n'
         'print(json.dumps({"argv": selfcmd.self_argv(), "hook": leakguard.hook_block("pre-commit")}))\n')
# The forbidden expression of the control grep (K5), whitespace-tolerant.
FORBIDDEN = re.compile(r'''parent\s*\.\s*parent\s*/\s*['"]skillsync['"]''')


def child_env(path):
    env = {key: value for key, value in os.environ.items() if key not in ('PYTHONPATH', 'PYTHONSAFEPATH', 'PYTHONHOME')}
    env['PYTHONPATH'] = str(path)
    return env


class SelfArgvCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='selfcmd-')
        self.addCleanup(self.temporary.cleanup)
        self.tmp = Path(self.temporary.name).resolve()
        self.cwd = self.tmp / 'empty-cwd'
        self.cwd.mkdir()

    def install_form(self):
        """A copy of the package alone (no wrapper beside it) in an empty directory."""
        site = self.tmp / 'site-packages'
        site.mkdir()
        shutil.copytree(PACKAGE, site / 'skillsync_workflow', ignore=shutil.ignore_patterns('__pycache__'))
        self.assertFalse((site / 'skillsync').exists())
        return site

    def ask(self, path, cwd=None):
        process = subprocess.run([sys.executable, '-c', CHILD], cwd=str(cwd or self.cwd), env=child_env(path),
                                 capture_output=True, text=True, timeout=120)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        return json.loads(process.stdout)

    def run_self(self, argv, path, *args, cwd=None):
        return subprocess.run(argv + list(args), cwd=str(cwd or self.cwd), env=child_env(path),
                              capture_output=True, text=True, timeout=120)


class CheckoutForm(SelfArgvCase):
    def test_in_process_form_is_interpreter_and_the_wrapper(self):
        argv = selfcmd.self_argv()
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(len(argv), 2, argv)
        self.assertTrue(os.path.isabs(argv[1]))
        self.assertEqual(Path(argv[1]).resolve(), SKILLSYNC_PATH.resolve())
        self.assertEqual(Path(argv[1]).name, 'skillsync')

    def test_child_in_an_empty_directory_prints_the_checkout_form_and_it_runs(self):
        argv = self.ask(ROOT, self.cwd)['argv']
        self.assertEqual([argv[0], Path(argv[1]).resolve(), len(argv)], [sys.executable, SKILLSYNC_PATH.resolve(), 2])
        process = self.run_self(argv, ROOT, '--version')
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertRegex(process.stdout, r'^skillsync \d+\.\d+\.\d+')

    @needs_os_symlinks
    def test_paths_are_absolute_and_not_resolved_through_a_symlink(self):
        link = self.tmp / 'linked checkout'
        link.symlink_to(ROOT, target_is_directory=True)
        self.assertNotEqual(link, ROOT)
        answer = self.ask(link)
        argv = answer['argv']
        self.assertEqual(argv, [sys.executable, str(link / 'skillsync')])
        self.assertTrue(all(os.path.isabs(item) for item in argv))
        # the hook block carries the same unresolved path (quoted because of the space)
        self.assertIn("'" + (link / 'skillsync').as_posix() + "'", answer['hook'])


class InstalledForm(SelfArgvCase):
    def test_without_a_wrapper_beside_the_package_the_form_is_dash_p_dash_m(self):
        site = self.install_form()
        answer = self.ask(site)
        self.assertEqual(answer['argv'], INSTALLED)
        self.assertIn(' '.join(INSTALLED[1:]) + ' leakcheck', answer['hook'])

    def test_installed_argv_runs_in_an_empty_directory(self):
        site = self.install_form()
        argv = self.ask(site)['argv']
        process = self.run_self(argv, site, '--version')
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertRegex(process.stdout, r'^skillsync \d+\.\d+\.\d+')
        direct = self.run_self([sys.executable, '-P', '-m', 'skillsync_workflow'], site, '--version')
        self.assertEqual((direct.returncode, direct.stdout), (0, process.stdout), direct.stderr)

    def test_dash_p_ignores_a_package_planted_in_the_working_directory(self):
        site = self.install_form()
        planted = self.tmp / 'planted-cwd'
        (planted / 'skillsync_workflow').mkdir(parents=True)
        (planted / 'skillsync_workflow' / '__init__.py').write_text('')
        (planted / 'skillsync_workflow' / '__main__.py').write_text('print("PLANTED")\n')
        argv = self.ask(site)['argv']
        safe = self.run_self(argv, site, '--version', cwd=planted)
        self.assertEqual(safe.returncode, 0, safe.stderr)
        self.assertNotIn('PLANTED', safe.stdout)
        self.assertRegex(safe.stdout, r'^skillsync \d')
        # control: without `-P` the planted package wins, so the flag is what protects
        unsafe = self.run_self([sys.executable, '-m', 'skillsync_workflow'], site, '--version', cwd=planted)
        self.assertIn('PLANTED', unsafe.stdout)

    def test_a_wrapper_next_to_the_copied_package_switches_back_to_the_checkout_form(self):
        site = self.install_form()
        (site / 'skillsync').write_text('#!/usr/bin/env python3\n')
        argv = self.ask(site)['argv']
        self.assertEqual(argv, [sys.executable, str(site / 'skillsync')])


class HelpOutsideAGitCheckout(SelfArgvCase):
    """`<command> --help` ends with exit 0 for a copy of the files without `.git` (a `git archive` or an
    installed package), in the checkout form and in the installed form (-P -m)."""

    def top_level_commands(self, argv, path):
        process = self.run_self(argv, path, '--help')
        self.assertEqual(process.returncode, 0, process.stderr)
        found = re.search(r'\{([^}\n]+)\}', process.stdout.split('\n\n', 1)[0])
        self.assertIsNotNone(found, process.stdout)
        commands = found.group(1).split(',')
        for needed in ('doctor', 'retro', 'retro-decide', 'checkpoint', 'link', 'route'):
            self.assertIn(needed, commands)
        return commands

    def assert_every_help_exits_zero(self, argv, path):
        for command in self.top_level_commands(argv, path):
            with self.subTest(command=command):
                process = self.run_self(argv, path, command, '--help')
                self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
                self.assertTrue(process.stdout.startswith('usage: skillsync'), process.stdout[:80])
                self.assertNotIn('Traceback', process.stderr)

    def test_a_copy_without_git_in_the_checkout_form(self):
        site = self.tmp / 'archive'
        site.mkdir()
        shutil.copytree(PACKAGE, site / 'skillsync_workflow', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy2(SKILLSYNC_PATH, site / 'skillsync')
        self.assertFalse((site / '.git').exists())
        self.assert_every_help_exits_zero([sys.executable, str(site / 'skillsync')], site)

    def test_a_copy_of_the_package_alone_in_the_installed_form(self):
        site = self.install_form()
        self.assert_every_help_exits_zero([sys.executable, '-P', '-m', 'skillsync_workflow'], site)


class UserEntry(unittest.TestCase):
    """`_user_entry()` / `link_task_argv()`: the user's own command, never someone else's file."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='selfcmd-entry-')
        self.addCleanup(self.temporary.cleanup)
        self.tmp = Path(self.temporary.name).resolve()
        self.home = self.tmp / 'home'
        (self.home / '.local' / 'bin').mkdir(parents=True)
        self.path_dir = self.tmp / 'pathbin'
        self.path_dir.mkdir()

    def make(self, path, mode=0o755):
        path.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        path.chmod(mode)
        return path

    def entry(self, which=None):
        with mock.patch.object(Path, 'home', return_value=self.home), \
                mock.patch.object(selfcmd.shutil, 'which', return_value=which):
            return selfcmd._user_entry()

    @unittest.skipIf(os.name == 'nt', 'POSIX executable bits')
    def test_the_fixed_entry_wins_over_path(self):
        fixed = self.make(self.home / '.local' / 'bin' / 'skillsync')
        other = self.make(self.path_dir / 'skillsync')
        self.assertEqual(self.entry(which=str(other)), str(fixed))

    @unittest.skipIf(os.name == 'nt', 'POSIX executable bits')
    def test_a_file_without_the_execute_bit_a_directory_and_a_broken_link_are_not_entries(self):
        fixed = self.home / '.local' / 'bin' / 'skillsync'
        self.make(fixed, mode=0o644)
        self.assertIsNone(self.entry())
        fixed.unlink()
        fixed.mkdir()
        self.assertIsNone(self.entry())
        fixed.rmdir()
        os.symlink(self.tmp / 'gone', fixed)
        self.assertIsNone(self.entry())

    @unittest.skipIf(os.name == 'nt', 'POSIX executable bits')
    def test_path_is_used_when_the_fixed_entry_is_missing_but_only_as_an_absolute_hit(self):
        found = self.make(self.path_dir / 'skillsync')
        self.assertEqual(self.entry(which=str(found)), str(found))
        self.assertIsNone(self.entry(which='skillsync'))        # relative hit: `.` or an empty PATH entry
        self.assertIsNone(self.entry(which='./skillsync'))

    @needs_os_symlinks
    def test_a_symlink_entry_stays_unresolved_whatever_it_points_at(self):
        target = self.make(self.tmp / 'other-checkout-skillsync')
        link = self.home / '.local' / 'bin' / 'skillsync'
        os.symlink(target, link)
        self.assertEqual(self.entry(), str(link))
        with mock.patch.object(selfcmd, '_user_entry', return_value=str(link)):
            self.assertEqual(selfcmd.link_task_argv(), [str(link)])


class ControlGrep(unittest.TestCase):
    def test_no_module_but_selfcmd_builds_the_wrapper_path_by_hand(self):
        found = []
        for path in sorted(PACKAGE.rglob('*.py')):
            if path.name == 'selfcmd.py' and path.parent == PACKAGE:
                continue
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                if FORBIDDEN.search(line):
                    found.append(f'{path.relative_to(ROOT)}:{number}: {line.strip()}')
        self.assertEqual(found, [])

    def test_the_pattern_can_fail(self):
        for sample in ("Path(__file__).resolve().parent.parent / 'skillsync'",
                       'Path(__file__).parent.parent/"skillsync"',
                       "x = here.parent.parent  /  'skillsync'"):
            with self.subTest(sample=sample):
                self.assertTrue(FORBIDDEN.search(sample))
        self.assertFalse(FORBIDDEN.search("package.parent.parent / 'skillsync_workflow'"))
        # the one allowed place really does contain it, so the exclusion above is not vacuous
        self.assertTrue(FORBIDDEN.search((PACKAGE / 'selfcmd.py').read_text(encoding='utf-8')))


TRICKY_PYTHON = "/opt/my py's/bin/python"
TRICKY_WRAPPER = "/srv/o'brien/my tools/skillsync"


class Launchers(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='selfcmd-launch-')
        self.addCleanup(self.temporary.cleanup)
        self.tmp = Path(self.temporary.name).resolve()

    def generate(self, argv, root=None):
        root = root or (self.tmp / "it's a project")
        root.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(environment, 'self_argv', return_value=argv):
            result = environment.generate(root)
        sh, ps = (Path(item) for item in result['launchers'])
        self.assertEqual((sh.name, ps.name), ('start.sh', 'start.ps1'))
        return root, sh, ps

    def test_start_sh_quotes_every_element_and_the_root(self):
        root, sh, _ = self.generate([TRICKY_PYTHON, TRICKY_WRAPPER])
        lines = sh.read_text(encoding='utf-8').splitlines()
        self.assertEqual(lines[0], '#!/bin/sh')
        import shlex
        self.assertEqual(shlex.split(lines[1]), ['exec', TRICKY_PYTHON, TRICKY_WRAPPER, 'env', root.as_posix(),
                                                 '--action', 'up', '--run', '$@'])
        self.assertTrue(lines[1].endswith('"$@"'), lines[1])
        self.assertEqual(len(lines), 2)

    @needs_sh
    def test_start_sh_is_valid_shell_and_passes_the_arguments_through(self):
        launcher_dir = self.tmp / "it's a tool dir"
        launcher_dir.mkdir()
        fake = launcher_dir / 'fake skillsync.py'
        fake.write_text('import json, sys\nprint(json.dumps(sys.argv[1:]))\n')
        root, sh, _ = self.generate([sys.executable, fake.as_posix()])
        self.assertEqual(subprocess.run(['sh', '-n', str(sh)], capture_output=True, text=True).returncode, 0)
        process = subprocess.run(['sh', str(sh), 'one', 'two words'], capture_output=True, text=True, timeout=60)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout),
                         ['env', root.as_posix(), '--action', 'up', '--run', 'one', 'two words'])

    def test_start_ps1_doubles_apostrophes_and_quotes_every_element(self):
        root, _, ps = self.generate([TRICKY_PYTHON, TRICKY_WRAPPER])
        text = ps.read_text(encoding='utf-8')
        first = text.splitlines()[0]
        self.assertTrue(first.startswith("& '/opt/my py''s/bin/python' '/srv/o''brien/my tools/skillsync' env '"), first)
        self.assertIn("it''s a project", first)
        tokens = [token.replace("''", "'") for token in re.findall(r"'((?:[^']|'')*)'", first)]
        self.assertEqual(tokens, [TRICKY_PYTHON, TRICKY_WRAPPER, str(root)])
        self.assertTrue(first.endswith(" --action up --run @args"), first)
        self.assertEqual(text.splitlines()[1], 'exit $LASTEXITCODE')

    def test_start_ps1_doubles_typographic_single_quotes_too(self):
        # PowerShell ends a single-quoted string at U+2018..U+201B as well as at the ASCII apostrophe
        wrapper = "/srv/o\u2019brien/\u2018x\u201a\u201b/skillsync"
        _, _, ps = self.generate([TRICKY_PYTHON, wrapper])
        first = ps.read_text(encoding='utf-8').splitlines()[0]
        self.assertIn("o\u2019\u2019brien/\u2018\u2018x\u201a\u201a\u201b\u201b/skillsync", first)

    def test_installed_form_gives_separate_quoted_elements(self):
        root, sh, ps = self.generate(list(INSTALLED))
        import shlex
        self.assertEqual(shlex.split(sh.read_text(encoding='utf-8').splitlines()[1])[1:5],
                         [sys.executable, '-P', '-m', 'skillsync_workflow'])
        first = ps.read_text(encoding='utf-8').splitlines()[0]
        self.assertIn("'-P' '-m' 'skillsync_workflow' env '", first)

    def test_paths_are_absolute_and_use_forward_slashes_in_the_shell_script(self):
        argv = [Path(sys.executable).as_posix(), SKILLSYNC_PATH.as_posix()]
        _, sh, _ = self.generate(argv)
        import shlex
        parts = shlex.split(sh.read_text(encoding='utf-8').splitlines()[1])
        self.assertTrue(all(os.path.isabs(item) and '\\' not in item for item in parts[1:3]), parts)

    def test_real_self_argv_reaches_the_launchers(self):
        root = self.tmp / 'real'
        root.mkdir()
        result = environment.generate(root)
        import shlex
        parts = shlex.split(Path(result['launchers'][0]).read_text(encoding='utf-8').splitlines()[1])
        expected = [Path(item).as_posix() if os.path.isabs(item) else item for item in selfcmd.self_argv()]
        self.assertEqual(parts[1:1 + len(expected)], expected)


if __name__ == '__main__':
    unittest.main()
