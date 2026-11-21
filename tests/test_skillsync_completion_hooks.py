"""Automatic completion hooks (CORE-COMPLETION, K12-K13): `install.sh` calls `skillsync completion install --auto`
and the `setup` wizard has a `completion` step. Everything runs in a temporary HOME; nothing touches the real one."""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from layers_helpers import SKILLSYNC, make_layer  # noqa: E402

AUTO = 'skillsync <completion> <install> <--auto>'
WIN = sys.platform == 'win32'


def _tree(root):
    return sorted(str(path.relative_to(root)) for path in Path(root).rglob('*'))


@unittest.skipIf(sys.platform == 'win32', 'install.sh is a POSIX sh script')
class InstallShHookTest(unittest.TestCase):
    ENV = {'SKILLSYNC_TAG': 'v9.9.9', 'SS_VERSION': '9.9.9'}

    def setUp(self):
        import test_skillsync_install_sh as base
        self.base = base
        self.env = base.Env()
        self.addCleanup(self.env.cleanup)
        # a fake whose `completion` exit code is separate from the one of `setup`
        base.write_exec(self.env.fakes / 'skillsync.template', base.FAKE_SKILLSYNC.replace(
            'log skillsync "$@"\n', 'log skillsync "$@"\n[ "$1" = completion ] && exit "${SS_COMPLETION_RC:-0}"\n'))

    def test_completion_runs_after_install_and_before_setup(self):
        r = self.env.run(extra=self.ENV)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = self.env.calls()
        install = next(i for i, c in enumerate(calls) if c.startswith('uv <tool> <install>'))
        self.assertEqual(self.env.calls_starting('skillsync'), [AUTO, 'skillsync <setup>'])
        self.assertLess(install, calls.index(AUTO))
        self.assertLess(calls.index(AUTO), calls.index('skillsync <setup>'))

    def test_completion_runs_even_without_setup(self):
        r = self.env.run(extra={**self.ENV, 'SKILLSYNC_NO_SETUP': '1'})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.env.calls_starting('skillsync'), [AUTO])

    def test_failure_is_a_warning_and_the_exit_code_is_unchanged(self):
        r = self.env.run(extra={**self.ENV, 'SS_COMPLETION_RC': '2'})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('install.sh: warning:', r.stderr)
        self.assertEqual(r.stderr.count('install.sh: warning:'), 1)
        self.assertEqual(self.env.calls_starting('skillsync'), [AUTO, 'skillsync <setup>'])
        self.assertIn('install.sh: done.', r.stdout)

    def test_failure_does_not_mask_the_setup_exit_code(self):
        r = self.env.run(extra={**self.ENV, 'SS_COMPLETION_RC': '2', 'SS_SETUP_RC': '1'})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_success_prints_no_warning(self):
        r = self.env.run(extra=self.ENV)
        self.assertNotIn('warning', r.stderr)


class SetupStepTest(unittest.TestCase):
    """Runs `skillsync setup` (CLI, in a subprocess, so that `completion.register` has captured the tree)."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='compl-hooks-'))
        self.addCleanup(lambda: __import__('shutil').rmtree(self.root, ignore_errors=True))
        self.home = self.root / 'home'
        self.home.mkdir()
        self.bin = self.root / 'bin'   # the ONLY directory on PATH: fake shells and a wrapper of the real git, never the real bash/zsh/pwsh
        self.bin.mkdir()
        self.profile = self.root / 'ps' / 'Microsoft.PowerShell_profile.ps1'
        self.fake_log = self.root / 'ps-host.log'
        self.fake('bash', 'exit 0\n', '')
        for host in ('pwsh', 'powershell'):
            self.fake(host, f'[ -n "$FAKE_LOG" ] && printf \'%s\\n\' "$*" >> "$FAKE_LOG"\nprintf \'%s\\n\' "$FAKE_PROFILE"\n',
                      '@echo %FAKE_PROFILE%\r\n')
        self.git_dir = None
        git = shutil.which('git')
        if git and not WIN:
            self.fake('git', f'exec {shlex.quote(git)} "$@"\n', '')
            for tool in ('sh',):   # git runs local transports through `sh`
                real = shutil.which(tool)
                if real:
                    os.symlink(real, self.bin / tool)
        elif git:
            self.git_dir = os.path.dirname(git)   # Windows: git.exe cannot be wrapped; its directory holds no shell
        self.core = self.root / 'core-origin'
        self.make_core()
        self.gitconfig = self.root / 'gitconfig'
        self.gitconfig.write_text('[user]\n\temail = dev@example.test\n\tname = Dev\n')

    def fake(self, name, posix, windows):
        if WIN:
            (self.bin / f'{name}.bat').write_text('@echo off\r\n' + windows, encoding='ascii')
        else:
            path = self.bin / name
            path.write_text('#!/bin/sh\n' + posix, encoding='utf-8')
            path.chmod(0o755)

    def make_core(self):
        self.core.mkdir()
        make_layer(self.core / 'library', 'core', 'core')
        env = {**os.environ, 'GIT_CONFIG_GLOBAL': str(self.root / 'gc0'), 'GIT_CONFIG_NOSYSTEM': '1'}
        for args in (['init', '-q'], ['add', '-A'], ['-c', 'user.name=T', '-c', 'user.email=t@example.test', 'commit', '-qm', 'core'],
                     ['tag', 'vtest-1']):
            subprocess.run(['git', *args], cwd=self.core, env=env, check=True, capture_output=True)

    def env(self, extra=None):
        data = self.root / 'xdg-data'
        env = {k: os.environ[k] for k in ('SYSTEMROOT', 'TEMP', 'TMP') if k in os.environ}   # nothing else is inherited
        env.update({
            'HOME': str(self.home), 'USERPROFILE': str(self.home), 'ZDOTDIR': str(self.home / 'zdot'),
            'XDG_DATA_HOME': str(data), 'XDG_CACHE_HOME': str(self.root / 'xdg-cache'), 'XDG_CONFIG_HOME': str(self.root / 'xdg-config'),
            'LOCALAPPDATA': str(self.root / 'localappdata'), 'APPDATA': str(self.root / 'appdata'),
            'GIT_CONFIG_GLOBAL': str(self.gitconfig), 'GIT_CONFIG_NOSYSTEM': '1',
            'SKILLSYNC_CONFIG': str(self.root / 'skillsync-config.yaml'),
            'PATH': str(self.bin) + (os.pathsep + self.git_dir if self.git_dir else ''),
            'FAKE_PROFILE': str(self.profile), 'FAKE_LOG': str(self.fake_log), 'PYTHONUTF8': '1',
        })
        env.update(extra or {})
        return env

    def setup(self, *extra, env=None):
        argv = [sys.executable, str(SKILLSYNC), 'setup', '--yes', '--core-source', str(self.core), '--core-ref', 'vtest-1', '--json', *extra]
        process = subprocess.run(argv, capture_output=True, text=True, env=self.env(env), stdin=subprocess.DEVNULL,
                                 cwd=str(self.root), timeout=120)
        try:
            report = json.loads(process.stdout)
        except ValueError:
            self.fail(f'not JSON (exit {process.returncode}): {process.stdout!r} {process.stderr!r}')
        return process, report

    def completion_files(self):
        data = self.root / 'xdg-data'
        return sorted(p for p in _tree(data) if p.split(os.sep)[0] in ('bash-completion', 'zsh')) if data.exists() else []

    @staticmethod
    def step(report, name='completion'):
        return next(item for item in report['steps'] if item['step'] == name)

    def test_normal_run_installs_the_bash_script_in_the_temporary_home(self):
        process, report = self.setup()
        self.assertEqual(report['status'], 'PASS', report)
        self.assertEqual(self.step(report)['result'], 'done', report)
        names = [item['step'] for item in report['steps']]
        self.assertLess(names.index('rule'), names.index('completion'))
        self.assertLess(names.index('completion'), names.index('check'))
        self.assertTrue((self.root / 'xdg-data' / 'bash-completion' / 'completions' / 'skillsync').is_file())
        _, again = self.setup()
        self.assertEqual(self.step(again)['result'], 'unchanged', again)

    def test_only_the_fake_tools_are_on_path(self):
        path = self.env()['PATH']
        for name in ('bash', 'pwsh', 'powershell'):
            found = shutil.which(name, path=path)
            self.assertIsNotNone(found, name)
            self.assertEqual(Path(found).parent, self.bin, name)
        self.assertIsNone(shutil.which('zsh', path=path), 'the real zsh must not be reachable')

    def test_the_powershell_profile_is_written_where_the_fake_host_says(self):
        process, report = self.setup()
        self.assertEqual(report['status'], 'PASS', report)
        self.assertEqual(self.profile.read_bytes().count(b'# >>> skillsync completion >>>'), 1)
        self.assertIn('-NoProfile', self.fake_log.read_text(encoding='utf-8'))
        base = self.root / ('localappdata' if WIN else 'xdg-data')
        self.assertTrue((base / 'skillsync' / 'completion' / 'skillsync-completion.ps1').is_file())

    def test_switch_skips_and_writes_nothing(self):
        for value in ('1', 'true', 'yes', 'on'):
            with self.subTest(value=value):
                _, report = self.setup(env={'SKILLSYNC_NO_COMPLETION': value})
                self.assertEqual(report['status'], 'PASS', report)
                self.assertEqual(self.step(report)['result'], 'skipped')
        self.assertFalse((self.root / 'xdg-data' / 'bash-completion').exists())
        self.assertFalse((self.home / 'zdot').exists())

    def test_dry_run_is_planned_and_leaves_home_unchanged(self):
        before = _tree(self.home)
        data_before = self.completion_files()
        _, report = self.setup('--dry-run')
        self.assertEqual(self.step(report)['result'], 'planned', report)
        self.assertEqual(_tree(self.home), before)
        self.assertEqual(self.completion_files(), data_before)
        self.assertFalse((self.root / 'xdg-data' / 'bash-completion').exists())

    def test_failure_is_skipped_with_a_warning_and_not_blocked(self):
        (self.root / 'xdg-data').mkdir()
        (self.root / 'xdg-data' / 'bash-completion').write_text('a file where the completion directory should be\n')
        process, report = self.setup()
        self.assertNotEqual(report['status'], 'BLOCKED', report)
        self.assertEqual(process.returncode, 0, process.stderr)
        step = self.step(report)
        self.assertEqual(step['result'], 'skipped', report)
        self.assertTrue(any('completion' in warning for warning in report['warnings']), report['warnings'])
        self.assertNotIn('blocked', [item['result'] for item in report['steps']])

    def test_without_a_parser_the_step_is_skipped(self):
        from unittest import mock
        from skillsync_workflow import completion, setup_wizard
        run = setup_wizard._Run(mock.Mock(dry_run=False, yes=True, json=False))
        with mock.patch.object(completion, '_SUBPARSERS', None):
            setup_wizard._completion_step(run)
        self.assertEqual(run.steps[0]['result'], 'skipped')
        self.assertIn('no parser', run.steps[0]['detail'])


class InstallForSetupTest(unittest.TestCase):
    """`completion_install.install_for_setup(subparsers, dry)` -> (result, detail, warnings); nothing here reaches a startup file."""

    def call(self, subparsers=object(), dry=False, **env):
        from skillsync_workflow import completion_install
        with mock.patch.dict(os.environ, env):
            return completion_install.install_for_setup(subparsers, dry)

    def test_without_a_parser_it_is_skipped(self):
        result, detail, warnings = self.call(None)
        self.assertEqual((result, warnings), ('skipped', []))
        self.assertIn('no parser', detail)

    def test_the_switch_skips(self):
        result, detail, warnings = self.call(SKILLSYNC_NO_COMPLETION='1')
        self.assertEqual((result, warnings), ('skipped', []))
        self.assertIn('SKILLSYNC_NO_COMPLETION', detail)

    def test_an_unknown_switch_value_warns_and_skips(self):
        result, _, warnings = self.call(SKILLSYNC_NO_COMPLETION='maybe')
        self.assertEqual(result, 'skipped')
        self.assertTrue(any('SKILLSYNC_NO_COMPLETION' in item for item in warnings), warnings)

    def test_an_internal_error_is_skipped_with_a_warning(self):
        from skillsync_workflow import completion_install
        with mock.patch.object(completion_install, '_detect', side_effect=RuntimeError('boom')):
            result, detail, warnings = self.call(SKILLSYNC_NO_COMPLETION='0')
        self.assertEqual(result, 'skipped')
        self.assertIn('boom', ' '.join(warnings))


if __name__ == '__main__':
    unittest.main()
