"""CLAUDE-7 F3.1 (tests written first, RED until the code exists): installation of the status line (`statusLine`) by `link` and its `doctor` checks
(spec K4, K5, K6, K8, K9; plan step F3, risk HIGH: the generator writes into the settings of the user).

Four groups:
 1. `hooksync.merge_setting(settings_path, key, value, is_ours, want, dry_run, backup='earliest')` (unit): sets or removes one top-level key of
    `.claude/settings.local.json`; contract of `merge_entry` (True when no entry of ours stays after the call; a foreign value of the key is never
    replaced, only a `[warn]` with the file name; an unusable file is a `[warn]`, never an exception; a tracked file is not written; the earliest
    backup stays; foreign keys, hooks and permissions stay; a second identical call changes neither bytes nor mtime).
 2. `link` (black box, subprocess, isolated HOME/XDG/SKILLSYNC_CONFIG): the entry `{type, command, refreshInterval}` with an absolute path of the copied
    script and no `git` and no shell, `.claude/.gitignore`, idempotence, dry-run, no write when a foreign `statusLine` or `disableAllHooks` is in any of the
    three settings files, the way back (key off, key absent, `claude: false`), unusable and tracked files.
 3. `doctor`: warning for a foreign bar or `disableAllHooks` (names the file and the priority), for an outdated or missing copy of the script.
 4. K8 (nothing changes without the keys) and the collision with the hooks of CLAUDE-3 (the `hooks` section, the warnings and the guard of generated
    files are the same with and without the bar).

Decisions the spec leaves open (to be confirmed, see the report of F3.1): the exact wording of the `[warn]` lines and findings (the tests look for the file
name, `statusLine`/`disableAllHooks` and a word with `priorit`), `link` copies the script only when it writes the entry, `claude: false` removes our entry and
copy (the behaviour of `remove_ours` of CLAUDE-3), an entry of ours is recognised by the path `.claude/hooks/statusline.py` in the command.
Negative assertions have a proof that they can fail: `HarnessSelfChecks` plants the forbidden state by hand and expects the helper to refuse it.
Temporary paths come from `tmp_paths.child`/`descend`; the tests use the repository library; nothing touches the real `~/.claude`."""
import contextlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from pathlib import Path as _Path_tmp_paths
import sys as _sys_tmp_paths
from unittest import mock

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE / 'fixtures' / 'hooksync'))
from layers_helpers import IsolatedTestCase, git, needs_mode_enforcement  # noqa: E402
_sys_tmp_paths.path.insert(0, str(_Path_tmp_paths(__file__).resolve().parent))  # noqa: E402
from tmp_paths import child, descend  # noqa: E402
import cases  # noqa: E402

CLI = REPO / 'skillsync'
LOCAL = cases.LOCAL
BACKUP = cases.BACKUP
HOOKS_DIR = cases.HOOKS_DIR
NEEDLE = '.claude/hooks/statusline.py'
SOURCE = REPO / 'library' / 'hooks' / 'statusline.py'
REFRESH = 15
FOREIGN_BAR = {'type': 'command', 'command': 'echo moj-pasek-ż', 'padding': 2}
FOREIGN_SETTINGS = {
    'permissions': {'allow': ['Bash(ls:*)'], 'deny': ['Read(./.env)']},
    'env': {'ZMIENNA': 'wartość-ą'},
    'outputStyle': 'karta',
    'hooks': {'Stop': [{'matcher': '', 'hooks': [{'type': 'command', 'command': 'echo obcy-stop'}]}],
              'Notification': [{'matcher': '', 'hooks': [{'type': 'command', 'command': 'echo obcy-notify'}]}]},
}
needs_posix_shell = unittest.skipUnless(os.name == 'posix' and shutil.which('sh'), 'wymaga powłoki POSIX (sh -c)')
windows_only = unittest.skipUnless(os.name == 'nt', 'tylko na Windows')


def hooksync():
    """The module under test; a missing function fails the test that asks for it (AttributeError), not the import of this file."""
    from skillsync_workflow import hooksync as module
    return module


def posix(text):
    return str(text).replace('\\', '/')


def ours(value):
    """`is_ours` of the unit tests: the value points at the script `.claude/hooks/statusline.py`."""
    return isinstance(value, dict) and NEEDLE in posix(value.get('command', ''))


def our_value(command=None, refresh=REFRESH):
    return {'type': 'command', 'command': command or f'python3 /work/proj/{NEEDLE}', 'refreshInterval': refresh}


def dumped(data, eol='\n'):
    return cases.json_bytes(data, eol)


def split_command(command):
    """The argv of a command line as the host would split it (POSIX shell quoting; on Windows the double quotes)."""
    if os.name == 'nt':
        return [token[1:-1] if len(token) > 1 and token[0] == token[-1] == '"' else token for token in shlex.split(command, posix=False)]
    return shlex.split(command)


def mentions_bar(finding):
    return re.search(r'statusline|statusLine|disableAllHooks', f"{finding['path']} {finding['reason']}", re.IGNORECASE) is not None


# --------------------------------------------------------------------------------------------------------------
# 1. merge_setting (unit)
# --------------------------------------------------------------------------------------------------------------
class MergeSetting(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.project = child(self.work, 'proj')
        self.project.mkdir()
        self.path = descend(self.project, LOCAL)
        self.backup = descend(self.project, BACKUP)

    def put(self, data, eol='\n'):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(data if isinstance(data, bytes) else dumped(data, eol))

    def read(self):
        return json.loads(self.path.read_bytes().decode('utf-8'))

    def merge(self, value=None, want=True, dry_run=False, is_ours=ours, key='statusLine', **kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = hooksync().merge_setting(self.path, key, value if value is not None or not want else our_value(), is_ours, want, dry_run, **kwargs)
        return result, out.getvalue()

    def snapshot(self):
        return self.path.read_bytes(), self.path.stat().st_mtime_ns

    def warn_lines(self, out):
        return [line for line in out.splitlines() if line.startswith('[warn]')]

    # --- add / update / remove ------------------------------------------------------------------------------
    def test_add_creates_a_missing_file_with_only_the_key(self):
        result, out = self.merge()
        self.assertIs(result, False, 'want=True: no entry of ours is meant to be absent, so the result is False (as merge_entry)')
        self.assertEqual(self.read(), {'statusLine': our_value()})
        self.assertIn(str(self.path), out)

    def test_add_keeps_foreign_keys_hooks_permissions_and_output_style(self):
        self.put(FOREIGN_SETTINGS)
        self.merge()
        data = self.read()
        self.assertEqual(data.pop('statusLine'), our_value())
        self.assertEqual(data, FOREIGN_SETTINGS, 'every other key is untouched (outputStyle written by /output-style included)')
        self.assertEqual(list(self.read())[:-1], list(FOREIGN_SETTINGS), 'the key goes after the existing ones')

    def test_the_key_name_is_a_parameter(self):
        self.put({'a': 1})
        self.merge(value={'cmd': 'x /p/marker'}, key='mine', is_ours=lambda value: isinstance(value, dict) and 'marker' in str(value.get('cmd')))
        self.assertEqual(self.read(), {'a': 1, 'mine': {'cmd': 'x /p/marker'}})

    def test_the_hooks_section_is_not_rewritten(self):
        self.put(FOREIGN_SETTINGS)
        before = self.read()['hooks']
        self.merge()
        self.assertEqual(self.read()['hooks'], before)
        self.merge(want=False)
        self.assertEqual(self.read()['hooks'], before)

    def test_second_identical_merge_changes_neither_bytes_nor_mtime(self):
        self.put(FOREIGN_SETTINGS)
        self.merge()
        os.utime(self.path, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
        before = self.snapshot()
        result, out = self.merge()
        self.assertEqual(self.snapshot(), before)
        self.assertIs(result, False)
        self.assertNotIn('Updated', out)

    def test_same_input_gives_the_same_bytes(self):
        self.put(FOREIGN_SETTINGS)
        self.merge()
        first = self.path.read_bytes()
        self.path.unlink()
        self.put(FOREIGN_SETTINGS)
        self.merge()
        self.assertEqual(self.path.read_bytes(), first)

    def test_our_stale_value_is_replaced(self):
        self.put({'statusLine': our_value('python3 /old/path/' + NEEDLE, refresh=60), 'env': {'A': '1'}})
        self.merge(value=our_value('python3 /new/path/' + NEEDLE))
        self.assertEqual(self.read(), {'statusLine': our_value('python3 /new/path/' + NEEDLE), 'env': {'A': '1'}})

    def test_remove_deletes_ours_and_keeps_the_rest(self):
        self.put({**FOREIGN_SETTINGS, 'statusLine': our_value()})
        result, _ = self.merge(want=False)
        self.assertIs(result, True)
        self.assertEqual(self.read(), FOREIGN_SETTINGS)

    def test_remove_of_a_foreign_value_leaves_the_file_alone(self):
        self.put({**FOREIGN_SETTINGS, 'statusLine': FOREIGN_BAR})
        before = self.snapshot()
        result, out = self.merge(want=False)
        self.assertIs(result, True, 'no entry of ours stays')
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn('Updated', out)

    def test_remove_without_a_file_is_true_and_creates_nothing(self):
        result, out = self.merge(want=False)
        self.assertIs(result, True)
        self.assertFalse(self.path.parent.exists())
        self.assertEqual(out, '')

    def test_remove_when_nothing_of_ours_is_there_writes_nothing(self):
        self.put(FOREIGN_SETTINGS)
        before = self.snapshot()
        result, _ = self.merge(want=False)
        self.assertIs(result, True)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.backup.exists())

    # --- the foreign value is never replaced ----------------------------------------------------------------
    def test_a_foreign_value_is_not_replaced_and_warns_with_the_file_name(self):
        self.put({**FOREIGN_SETTINGS, 'statusLine': FOREIGN_BAR})
        before = self.snapshot()
        result, out = self.merge()
        self.assertIs(result, False)
        self.assertEqual(self.snapshot(), before, 'no write')
        self.assertFalse(self.backup.exists())
        warns = self.warn_lines(out)
        self.assertTrue(any('settings.local.json' in line for line in warns), out)
        self.assertNotIn('Updated', out)

    def test_a_foreign_value_that_is_not_a_mapping_is_not_replaced(self):
        for foreign in ('echo tekst', 7, ['a'], None):
            with self.subTest(foreign=foreign):
                self.put({'statusLine': foreign, 'env': {'A': '1'}})
                before = self.snapshot()
                result, out = self.merge()
                self.assertIs(result, False)
                self.assertEqual(self.snapshot(), before)
                self.assertTrue(self.warn_lines(out), out)

    def test_a_foreign_value_in_a_dry_run_is_also_a_warning_without_a_write(self):
        self.put({'statusLine': FOREIGN_BAR})
        before = self.snapshot()
        result, out = self.merge(dry_run=True)
        self.assertIs(result, False)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(self.warn_lines(out), out)

    # --- unusable files -------------------------------------------------------------------------------------
    def test_invalid_json_is_a_warning_not_an_exception(self):
        raw = b'{ to nie jest json\r\n'
        self.put(raw)
        for want in (True, False):
            with self.subTest(want=want):
                result, out = self.merge(want=want)
                self.assertIs(result, False)
                self.assertEqual(self.path.read_bytes(), raw)
                self.assertTrue(any('settings.local.json' in line for line in self.warn_lines(out)), out)
        self.assertFalse(self.backup.exists())

    def test_wrong_structures_are_warnings_and_leave_the_file(self):
        for data in ([1, 2], {'hooks': 'tekst'}, {'hooks': ['a']}):
            with self.subTest(data=data):
                self.put(data)
                before = self.snapshot()
                result, out = self.merge()
                self.assertIs(result, False)
                self.assertEqual(self.snapshot(), before)
                self.assertTrue(any('settings.local.json' in line for line in self.warn_lines(out)), out)

    def test_non_utf8_file_is_a_warning_not_an_exception(self):
        self.put(b'\xff\xfe{"a": 1}')
        result, out = self.merge()
        self.assertIs(result, False)
        self.assertEqual(self.path.read_bytes(), b'\xff\xfe{"a": 1}')
        self.assertTrue(self.warn_lines(out), out)

    def test_tracked_file_is_not_written_and_not_cleaned(self):
        self.put({'env': {'A': '1'}})
        git(self.project, 'init', '-q')
        git(self.project, 'add', '-f', LOCAL)
        before = self.snapshot()
        result, out = self.merge()
        self.assertIs(result, False)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(any('śledzony przez git' in line and line.startswith('[warn]') for line in out.splitlines()), out)
        self.put({'statusLine': our_value()})
        git(self.project, 'add', '-f', LOCAL)
        before = self.snapshot()
        result, out = self.merge(want=False)
        self.assertIs(result, False, 'a tracked file keeps our entry: the result says so')
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.backup.exists())

    def test_untracked_file_in_a_git_repository_is_written(self):
        git(self.project, 'init', '-q')
        self.put({'env': {'A': '1'}})
        self.merge()
        self.assertIn('statusLine', self.read())

    @needs_mode_enforcement
    def test_a_read_only_file_is_a_warning_not_an_exception(self):
        self.put({'env': {'A': '1'}})
        self.path.chmod(0o444)
        self.addCleanup(self.path.chmod, 0o644)
        before = self.path.read_bytes()
        result, out = self.merge()
        self.assertIs(result, False)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(any('settings.local.json' in line for line in self.warn_lines(out)), out)

    @needs_mode_enforcement
    def test_a_read_only_directory_is_a_warning_not_an_exception(self):
        self.put({'env': {'A': '1'}})
        self.path.parent.chmod(0o555)
        self.addCleanup(self.path.parent.chmod, 0o755)
        before = self.path.read_bytes()
        result, out = self.merge()
        self.assertIs(result, False)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(self.warn_lines(out), out)

    # --- dry run --------------------------------------------------------------------------------------------
    def test_dry_run_prints_and_writes_nothing(self):
        self.put({'env': {'A': '1'}})
        before = self.snapshot()
        result, out = self.merge(dry_run=True)
        self.assertIs(result, False)
        self.assertEqual(self.snapshot(), before)
        self.assertIn('[dry-run]', out)
        self.assertFalse(self.backup.exists())

    def test_dry_run_of_a_removal_returns_true_and_writes_nothing(self):
        self.put({'statusLine': our_value()})
        before = self.snapshot()
        result, out = self.merge(want=False, dry_run=True)
        self.assertIs(result, True)
        self.assertEqual(self.snapshot(), before)
        self.assertIn('[dry-run]', out)

    def test_dry_run_without_a_file_creates_neither_file_nor_directory(self):
        self.merge(dry_run=True)
        self.assertFalse(self.path.parent.exists())

    # --- backup ---------------------------------------------------------------------------------------------
    def test_earliest_is_the_default_policy_and_keeps_an_existing_backup_byte_for_byte(self):
        self.put({'env': {'A': '1'}})
        raw = b'{"stara-kopia": true}\r\n'
        self.backup.write_bytes(raw)
        self.merge()
        self.assertEqual(self.backup.read_bytes(), raw)
        self.merge(value=our_value('python3 /inny/' + NEEDLE))
        self.assertEqual(self.backup.read_bytes(), raw)

    def test_first_write_saves_the_original_and_a_later_write_does_not_replace_it(self):
        original = dumped({'env': {'A': '1'}})
        self.put(original)
        self.merge()
        self.assertEqual(self.backup.read_bytes(), original)
        self.merge(want=False)
        self.assertEqual(self.backup.read_bytes(), original, 'the earliest backup survives the removal as well')

    def test_no_file_no_backup(self):
        self.merge()
        self.assertFalse(self.backup.exists())

    def test_overwrite_policy_can_be_asked_for(self):
        self.put({'env': {'A': '1'}})
        self.backup.write_bytes(b'{"stara": 1}\n')
        self.merge(backup='overwrite')
        self.assertEqual(json.loads(self.backup.read_text(encoding='utf-8')), {'env': {'A': '1'}})

    # --- bytes ----------------------------------------------------------------------------------------------
    def test_crlf_file_stays_crlf(self):
        self.put({'env': {'A': '1'}}, eol='\r\n')
        self.merge()
        raw = self.path.read_bytes()
        self.assertIn(b'\r\n', raw)
        self.assertNotIn(b'\n', raw.replace(b'\r\n', b''))

    def test_non_ascii_is_kept_unescaped(self):
        self.put({'env': {'ZMIENNA': 'wartość-ą'}})
        self.merge()
        self.assertIn('wartość-ą'.encode('utf-8'), self.path.read_bytes())

    def test_a_string_path_is_accepted(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            hooksync().merge_setting(str(self.path), 'statusLine', our_value(), ours, True, False)
        self.assertEqual(self.read(), {'statusLine': our_value()})


# --------------------------------------------------------------------------------------------------------------
# 2. link (black box)
# --------------------------------------------------------------------------------------------------------------
class LinkBase(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {'SKILLSYNC_NO_COMPLETION': '1'})
        patcher.start()
        self.addCleanup(patcher.stop)
        cases.write_machine_config(self.env['SKILLSYNC_CONFIG'])
        self.count = 0
        self.home = Path(self.env['HOME'])

    # --- building and running -------------------------------------------------------------------------------
    def project(self, claude=None, skills=(), hooks=None, local=None, backup=None, name=None, bare=False):
        """A fresh project. Default: a plain project, the hooks of CLAUDE-3 switched off (only the guard of commands, which `link` always installs), the
        bar switched on. `claude`: the value of the manifest key (`None`: statusline on); `bare=True`: no `claude` key at all."""
        self.count += 1
        project = child(self.work, name or f'p{self.count}')
        project.mkdir()
        self.manifest = {'version': 1, 'project': {'name': 'Org/Bar', 'type': 'local'}, 'skills': list(skills)}
        self.manifest['hooks'] = dict(cases.NEW_HOOK_KEYS_OFF if hooks is None else hooks)
        if not bare:
            self.manifest['claude'] = {'statusline': True} if claude is None else claude
        self.write_manifest(project)
        for relative, data in ((LOCAL, local), (BACKUP, backup)):
            if data is not None:
                target = descend(project, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        return project

    def write_manifest(self, project):
        target = descend(project, '.github/.skillsync.yaml')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(self.manifest, sort_keys=False), encoding='utf-8')

    def set_claude(self, project, value):
        if value is None:
            self.manifest.pop('claude', None)
        else:
            self.manifest['claude'] = value
        self.write_manifest(project)

    def link(self, project, *args):
        return cases.run_link(project, *args)

    def lines(self, result):
        return result.stdout.splitlines()

    def warns(self, result):
        return [line for line in self.lines(result) if line.startswith('[warn]')]

    def settings(self, project):
        return json.loads(descend(project, LOCAL).read_bytes().decode('utf-8'))

    def write_settings(self, project, data, name='settings.local.json'):
        target = descend(project, f'.claude/{name}')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(dumped(data))
        return target

    def write_user_settings(self, data):
        target = child(self.home, '.claude')
        target.mkdir(exist_ok=True)
        path = child(target, 'settings.json')
        path.write_bytes(dumped(data))
        return path

    def script(self, project):
        return descend(project, f'{HOOKS_DIR}/statusline.py')

    def gitignore_lines(self, project):
        path = descend(project, '.claude/.gitignore')
        return path.read_text(encoding='utf-8').splitlines() if path.is_file() else []

    def doctor(self, project):
        done = subprocess.run([sys.executable, str(CLI), 'doctor', str(project), '--json'], capture_output=True, text=True, timeout=120,
                              cwd=str(project), env=dict(os.environ))
        self.assertNotIn('Traceback', done.stderr, done.stderr)
        return json.loads(done.stdout)['findings']

    def bar_findings(self, findings):
        return [finding for finding in findings if mentions_bar(finding)]

    # --- assertions -----------------------------------------------------------------------------------------
    def assert_no_statusline(self, project, message=''):
        """No entry, no copy of the script, no line of it in `.claude/.gitignore` (the negative assertion; proven to fail in `HarnessSelfChecks`)."""
        settings_path = descend(project, LOCAL)
        data = json.loads(settings_path.read_bytes().decode('utf-8')) if settings_path.is_file() else {}
        bar = data.get('statusLine')
        self.assertFalse(isinstance(bar, dict) and NEEDLE in posix(bar.get('command', '')), f'an entry of ours is there {message}')
        self.assertFalse(self.script(project).exists(), f'the script is there {message}')
        self.assertFalse(any('statusline.py' in line for line in self.gitignore_lines(project)), f'.claude/.gitignore names the script {message}')

    def entry(self, project):
        bar = self.settings(project).get('statusLine')
        self.assertIsInstance(bar, dict, 'no statusLine entry in settings.local.json')
        return bar

    def installed(self, project):
        """Link with the bar switched on and the entry in place; returns the entry."""
        self.link(project)
        return self.entry(project)

    def run_command(self, command, project, shell=False, session='proba-paska'):
        """Runs the command the way the host does and returns (exit code, stdout text); no colour, no width limit from the environment."""
        env = dict(os.environ, NO_COLOR='1')
        env.pop('COLUMNS', None)
        payload = {'session_name': session, 'cwd': str(project), 'workspace': {'project_dir': str(project)}}
        done = subprocess.run(command if shell else split_command(command), shell=shell, input=json.dumps(payload).encode('utf-8'),
                              capture_output=True, timeout=60, env=env, cwd=str(project))
        return done.returncode, done.stdout.decode('utf-8', 'replace')

    def assert_bar_output(self, code, out, session='proba-paska'):
        self.assertEqual(code, 0)
        self.assertEqual(len(out.splitlines()), 1, out)
        self.assertIn(f'sesja: {session}', out)


class Installation(LinkBase):
    def test_the_entry_has_the_documented_shape(self):
        bar = self.installed(self.project())
        self.assertEqual(sorted(bar), ['command', 'refreshInterval', 'type'], 'no padding and no other field')
        self.assertEqual(bar['type'], 'command')
        self.assertEqual(bar['refreshInterval'], REFRESH)
        self.assertIsInstance(bar['command'], str)

    def test_the_command_is_the_generator_python_and_the_absolute_path_of_the_copy_without_git_or_a_shell(self):
        project = self.project()
        command = self.installed(project)['command']
        argv = split_command(command)
        self.assertEqual(len(argv), 2, f'interpreter and script only: {command}')
        self.assertEqual(os.path.normcase(argv[0]), os.path.normcase(sys.executable), 'the interpreter of the generator that ran link')
        self.assertTrue(os.path.isabs(argv[1]), argv[1])
        self.assertEqual(os.path.normcase(os.path.realpath(argv[1])), os.path.normcase(os.path.realpath(self.script(project))))
        self.assertTrue(os.path.isfile(argv[1]))
        for token in argv:
            self.assertNotIn(os.path.basename(token).lower().removesuffix('.exe'), ('git', 'sh', 'bash', 'zsh', 'cmd', 'powershell', 'pwsh'), token)
        for fragment in ('CLAUDE_PROJECT_DIR', 'rev-parse', '$(', '`', '&&', '||', ';', '|', 'if [', '2>'):
            self.assertNotIn(fragment, command)

    def test_a_path_with_a_space_and_an_apostrophe_still_splits_into_two_tokens(self):
        project = self.project(name="moj projekt it's")
        command = self.installed(project)['command']
        argv = split_command(command)
        self.assertEqual(len(argv), 2, command)
        self.assertEqual(os.path.normcase(os.path.realpath(argv[1])), os.path.normcase(os.path.realpath(self.script(project))))

    @windows_only
    def test_on_windows_the_command_uses_double_quotes_only(self):
        command = self.installed(self.project(name='moj projekt'))['command']
        self.assertNotIn("'", command)
        self.assertIn('"', command)

    def test_the_command_runs_without_a_shell_and_prints_one_line(self):
        project = self.project()
        code, out = self.run_command(self.installed(project)['command'], project)
        self.assert_bar_output(code, out)

    @needs_posix_shell
    def test_the_command_runs_through_sh_c_like_the_host_does(self):
        project = self.project(name="powłoka it's")
        code, out = self.run_command(self.installed(project)['command'], project, shell=True)
        self.assert_bar_output(code, out)

    def test_the_script_is_a_byte_copy_of_the_library(self):
        project = self.project()
        self.link(project)
        self.assertEqual(self.script(project).read_bytes(), SOURCE.read_bytes())

    def test_claude_gitignore_lists_the_script_the_settings_and_their_backup(self):
        for hooks in (None, {}):
            with self.subTest(hooks='default' if hooks is None else 'no hook keys'):
                project = self.project(hooks=hooks)
                self.link(project)
                lines = self.gitignore_lines(project)
                for entry in ('hooks/statusline.py', 'settings.local.json', 'settings.local.json.skillsync-backup'):
                    self.assertIn(entry, lines)

    def test_the_gitignore_entries_are_written_once(self):
        project = self.project()
        self.link(project)
        self.link(project)
        lines = self.gitignore_lines(project)
        self.assertEqual(lines.count('hooks/statusline.py'), 1)
        self.assertEqual(lines.count('settings.local.json'), 1)

    def test_a_second_link_changes_nothing(self):
        project = self.project(local=dumped(FOREIGN_SETTINGS))
        self.link(project)
        os.utime(descend(project, LOCAL), ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
        os.utime(self.script(project), ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
        before = {name: (descend(project, name).read_bytes(), descend(project, name).stat().st_mtime_ns)
                  for name in (LOCAL, BACKUP, f'{HOOKS_DIR}/statusline.py', '.claude/.gitignore')}
        again = self.link(project)
        after = {name: (descend(project, name).read_bytes(), descend(project, name).stat().st_mtime_ns) for name in before}
        self.assertEqual(after, before)
        self.assertFalse([line for line in self.lines(again) if line.startswith('Updated:') and ('settings.local.json' in line or 'statusline' in line)], again.stdout)
        self.assertEqual([line for line in self.warns(again) if re.search('statusLine|statusline|settings.local', line)], [])

    def test_foreign_keys_hooks_permissions_and_the_output_style_stay(self):
        project = self.project(local=dumped(FOREIGN_SETTINGS))
        self.link(project)
        data = self.settings(project)
        for key, value in FOREIGN_SETTINGS.items():
            if key != 'hooks':
                self.assertEqual(data[key], value, key)
        self.assertEqual(data['hooks']['Notification'], FOREIGN_SETTINGS['hooks']['Notification'])
        self.assertIn({'type': 'command', 'command': 'echo obcy-stop'}, [hook for group in data['hooks']['Stop'] for hook in group['hooks']])
        self.assertIn('statusLine', data)

    def test_an_entry_with_another_path_is_foreign_it_is_kept_and_warned_about(self):
        stale = our_value(f'python3 /old/checkout/{NEEDLE}', refresh=60)
        project = self.project(local=dumped({'statusLine': stale}))
        result = self.link(project)
        self.assertEqual(self.entry(project), stale, 'not overwritten, not refreshed')
        self.assertFalse(self.script(project).exists(), 'no copy of the script without our entry')
        text = [posix(line) for line in self.warns(result) if 'statusLine' in line]
        self.assertTrue(any('.claude/settings.local.json' in line and re.search('priorit', line, re.IGNORECASE) and 'by hand' in line for line in text), result.stdout)

    def test_another_interpreter_in_our_entry_does_not_rewrite_the_file(self):
        project = self.project(local=dumped(FOREIGN_SETTINGS))
        bar = self.installed(project)
        other = {**bar, 'command': '/usr/bin/inny-python ' + bar['command'].split(' ', 1)[1]}
        data = self.settings(project)
        data['statusLine'] = other
        descend(project, LOCAL).write_bytes(dumped(data))
        os.utime(descend(project, LOCAL), ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
        before = (descend(project, LOCAL).read_bytes(), descend(project, LOCAL).stat().st_mtime_ns)
        again = self.link(project)
        self.assertEqual((descend(project, LOCAL).read_bytes(), descend(project, LOCAL).stat().st_mtime_ns), before)
        self.assertFalse([line for line in self.warns(again) if 'statusLine' in line], again.stdout)

    def test_another_refresh_interval_is_rewritten(self):
        project = self.project()
        bar = self.installed(project)
        data = self.settings(project)
        data['statusLine'] = {**bar, 'refreshInterval': 60}
        descend(project, LOCAL).write_bytes(dumped(data))
        self.link(project)
        self.assertEqual(self.entry(project)['refreshInterval'], REFRESH)

    def test_the_backup_of_the_settings_is_the_earliest_one(self):
        original = dumped(FOREIGN_SETTINGS)
        project = self.project(local=original)
        self.link(project)
        self.assertEqual(descend(project, BACKUP).read_bytes(), original, 'neither the guard nor the bar replaced the first backup')
        raw = b'{"stara-kopia": true}\r\n'
        other = self.project(local=original, backup=raw)
        self.link(other)
        self.assertEqual(descend(other, BACKUP).read_bytes(), raw)

    # --- dry run --------------------------------------------------------------------------------------------
    def test_dry_run_of_a_fresh_project_writes_nothing_and_names_the_script(self):
        project = self.project()
        result = self.link(project, '--dry-run')
        self.assertFalse(descend(project, LOCAL).exists())
        self.assertFalse(self.script(project).exists())
        self.assertTrue(any(line.startswith('[dry-run]') and 'statusline' in line for line in self.lines(result)), result.stdout)

    def test_dry_run_over_an_existing_file_keeps_its_bytes(self):
        project = self.project(local=dumped(FOREIGN_SETTINGS))
        before = descend(project, LOCAL).read_bytes()
        self.link(project, '--dry-run')
        self.assertEqual(descend(project, LOCAL).read_bytes(), before)
        self.assert_no_statusline(project, 'after a dry run')


class ForeignBar(LinkBase):
    """A foreign `statusLine` in any of the three files, or `disableAllHooks`, stops the write (the high risk of the step)."""

    def project_and_foreign_file(self, which, data):
        project = self.project()
        if which == 'local':
            path = self.write_settings(project, data)
        elif which == 'project':
            path = self.write_settings(project, data, 'settings.json')
        else:
            path = self.write_user_settings(data)
        return project, path

    def assert_warned_about(self, result, which, token):
        text = [posix(line) for line in self.warns(result) if token in line]
        self.assertTrue(text, f'no [warn] line names {token}: {result.stdout}')
        if which == 'local':
            self.assertTrue(any('settings.local.json' in line for line in text), text)
        elif which == 'project':
            self.assertTrue(any('.claude/settings.json' in line and '~/' not in line and posix(self.home) not in line for line in text), text)
        else:
            self.assertTrue(any('~/.claude/settings.json' in line or f'{posix(self.home)}/.claude/settings.json' in line for line in text), text)
        self.assertTrue(any(re.search(r'priorit|priorytet', line, re.IGNORECASE) for line in text), f'the priority is not named: {text}')

    def test_a_foreign_bar_in_each_file_is_not_overwritten_and_warns(self):
        for which in ('local', 'project', 'user'):
            with self.subTest(file=which):
                project, path = self.project_and_foreign_file(which, {'statusLine': FOREIGN_BAR, 'env': {'A': '1'}})
                result = self.link(project)
                self.assert_no_statusline(project, f'with a foreign bar in the {which} file')
                self.assert_warned_about(result, which, 'statusLine')
                self.assertEqual(json.loads(path.read_bytes().decode('utf-8'))['statusLine'], FOREIGN_BAR)
                self.assertEqual(json.loads(path.read_bytes().decode('utf-8'))['env'], {'A': '1'})

    def test_the_foreign_bar_of_the_local_file_stays_byte_exact_and_the_rest_of_link_still_works(self):
        project = self.project(skills=())
        self.write_settings(project, {'statusLine': FOREIGN_BAR})
        self.link(project)
        data = self.settings(project)
        self.assertEqual(data['statusLine'], FOREIGN_BAR)
        self.assertIn('PreToolUse', data['hooks'], 'the guard of commands is installed as always')

    def test_disable_all_hooks_in_each_file_stops_the_write_and_warns(self):
        for which in ('local', 'project', 'user'):
            with self.subTest(file=which):
                project, _ = self.project_and_foreign_file(which, {'disableAllHooks': True})
                result = self.link(project)
                self.assert_no_statusline(project, f'with disableAllHooks in the {which} file')
                self.assert_warned_about(result, which, 'disableAllHooks')

    def test_disable_all_hooks_false_does_not_block(self):
        for which in ('local', 'project', 'user'):
            with self.subTest(file=which):
                project, _ = self.project_and_foreign_file(which, {'disableAllHooks': False})
                result = self.link(project)
                self.assertIn('statusLine', self.settings(project))
                self.assertFalse([line for line in self.warns(result) if 'disableAllHooks' in line], result.stdout)

    def test_a_foreign_value_that_is_not_a_mapping_blocks_too(self):
        project, _ = self.project_and_foreign_file('project', {'statusLine': 'echo tekst'})
        result = self.link(project)
        self.assert_no_statusline(project)
        self.assertTrue([line for line in self.warns(result) if 'statusLine' in line], result.stdout)

    def test_the_warning_comes_back_on_every_link_and_nothing_is_written_later_either(self):
        project, _ = self.project_and_foreign_file('user', {'statusLine': FOREIGN_BAR})
        self.link(project)
        again = self.link(project)
        self.assert_no_statusline(project, 'after the second link')
        self.assertTrue([line for line in self.warns(again) if 'statusLine' in line], again.stdout)

    def test_a_foreign_bar_in_the_user_file_does_not_stop_the_other_projects_files(self):
        project, path = self.project_and_foreign_file('user', {'statusLine': FOREIGN_BAR})
        before = path.read_bytes()
        self.link(project)
        self.assertEqual(path.read_bytes(), before, 'the file of the user is never written')
        self.assertFalse(descend(project, '.claude/settings.json').exists())


class UnusableFiles(LinkBase):
    def test_invalid_json_is_a_warning_and_the_file_stays(self):
        raw = b'{ to nie jest json\r\n'
        project = self.project(local=raw)
        result = self.link(project)
        self.assertEqual(descend(project, LOCAL).read_bytes(), raw)
        self.assertTrue([line for line in self.warns(result) if 'settings.local.json' in line], result.stdout)

    def test_hooks_that_is_not_a_mapping_is_a_warning_and_the_file_stays(self):
        raw = dumped({'hooks': 'tekst', 'env': {'A': '1'}})
        project = self.project(local=raw)
        result = self.link(project)
        self.assertEqual(descend(project, LOCAL).read_bytes(), raw)
        self.assertTrue(self.warns(result), result.stdout)

    def test_an_unusable_settings_file_leaves_no_orphaned_copy_of_the_script(self):
        for raw in (dumped({'hooks': 'tekst'}), b'{ zepsute'):
            with self.subTest(raw=raw[:8]):
                project = self.project(local=raw)
                self.link(project)
                self.assertFalse(self.script(project).exists())
                self.assertFalse(any('statusline.py' in line for line in self.gitignore_lines(project)))

    def test_a_tracked_settings_file_leaves_no_orphaned_copy_of_the_script(self):
        project = self.project(local=dumped(FOREIGN_SETTINGS))
        git(project, 'init', '-q')
        git(project, 'add', '-f', LOCAL)
        self.link(project)
        self.assertFalse(self.script(project).exists())

    def test_a_tracked_settings_file_is_not_written(self):
        raw = dumped(FOREIGN_SETTINGS)
        project = self.project(local=raw)
        git(project, 'init', '-q')
        git(project, 'add', '-f', LOCAL)
        result = self.link(project)
        self.assertEqual(descend(project, LOCAL).read_bytes(), raw)
        self.assertTrue([line for line in self.warns(result) if 'settings.local.json' in line and 'śledzony przez git' in line], result.stdout)

    def test_a_symlinked_script_keeps_its_target_and_no_entry_points_at_it(self):
        project = self.project()
        target = child(self.work, 'moj-skrypt.py')
        target.write_bytes(b'# moj\n')
        folder = descend(project, HOOKS_DIR)
        folder.mkdir(parents=True)
        try:
            os.symlink(target, child(folder, 'statusline.py'))
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable')
        result = self.link(project)
        self.assertEqual(target.read_bytes(), b'# moj\n')
        self.assertTrue([line for line in self.warns(result) if 'statusline.py' in line], result.stdout)
        data = self.settings(project) if descend(project, LOCAL).is_file() else {}
        self.assertNotIn('statusLine', data, 'no entry without our script in place')


class SwitchingOff(LinkBase):
    def assert_gone(self, project):
        self.assertNotIn('statusLine', self.settings(project))
        self.assertFalse(self.script(project).exists())

    def test_key_false_removes_our_entry_and_the_copy_and_keeps_the_rest(self):
        project = self.project(local=dumped(FOREIGN_SETTINGS))
        self.installed(project)
        self.set_claude(project, {'statusline': False})
        self.link(project)
        self.assert_gone(project)
        data = self.settings(project)
        for key in ('permissions', 'env', 'outputStyle'):
            self.assertEqual(data[key], FOREIGN_SETTINGS[key])
        self.assertIn('PreToolUse', data['hooks'], 'the guard of commands stays')

    def test_a_missing_key_removes_it_too(self):
        for value in ({}, {'enabled': True}, None):
            with self.subTest(claude=value):
                project = self.project()
                self.installed(project)
                self.set_claude(project, value)
                self.link(project)
                self.assert_gone(project)

    def test_claude_false_removes_our_entry_and_the_copy(self):
        for value in (False, {'enabled': False}, {'enabled': False, 'statusline': True}):
            with self.subTest(claude=value):
                project = self.project()
                self.installed(project)
                self.set_claude(project, value)
                self.link(project)
                self.assert_gone(project)

    def test_claude_false_from_the_start_installs_nothing(self):
        for value in (False, {'enabled': False, 'statusline': True}):
            with self.subTest(claude=value):
                project = self.project(claude=value)
                self.link(project)
                self.assert_no_statusline(project)
                self.assertFalse(descend(project, LOCAL).exists())

    def test_a_foreign_bar_is_never_removed(self):
        for value in ({'statusline': False}, False, {'enabled': False}, None):
            with self.subTest(claude=value):
                project = self.project(local=dumped({'statusLine': FOREIGN_BAR, 'env': {'A': '1'}}))
                self.set_claude(project, value)
                self.link(project)
                data = self.settings(project) if descend(project, LOCAL).is_file() else {}
                self.assertEqual(data.get('statusLine'), FOREIGN_BAR)
                self.assertEqual(data.get('env'), {'A': '1'})

    def test_an_entry_of_another_script_that_merely_mentions_the_word_is_foreign(self):
        for command in ('python3 /usr/local/bin/mystatusline.py', 'echo statusline', 'python3 /opt/tools/statusline.py'):
            with self.subTest(command=command):
                project = self.project(local=dumped({'statusLine': {'type': 'command', 'command': command}}), claude={'statusline': False})
                self.link(project)
                self.assertEqual(self.settings(project)['statusLine']['command'], command)

    def test_a_tracked_script_stays_with_a_warning_and_no_entry_remains(self):
        project = self.project()
        self.installed(project)
        git(project, 'init', '-q')
        git(project, 'add', '-f', f'{HOOKS_DIR}/statusline.py')
        self.set_claude(project, {'statusline': False})
        result = self.link(project)
        self.assertTrue(self.script(project).exists())
        self.assertNotIn('statusLine', self.settings(project))
        self.assertTrue([line for line in self.warns(result) if 'statusline.py' in line and 'śledzony przez git' in line], result.stdout)

    def test_when_the_settings_are_unreadable_the_script_stays_so_no_entry_dangles(self):
        project = self.project()
        self.installed(project)
        descend(project, LOCAL).write_bytes(b'{ zepsute')
        self.set_claude(project, {'statusline': False})
        result = self.link(project)
        self.assertTrue(self.script(project).exists())
        self.assertEqual(descend(project, LOCAL).read_bytes(), b'{ zepsute')
        self.assertTrue(self.warns(result), result.stdout)

    FOREIGN_SCRIPT = b'# skrypt uzytkownika\n'

    def put_foreign_script(self, project):
        target = self.script(project)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.FOREIGN_SCRIPT)

    def test_a_foreign_script_under_the_name_stays_without_the_key_and_without_a_warning(self):
        for claude in (None, {'statusline': False}, {}, False, {'enabled': False}):
            with self.subTest(claude=claude):
                project = self.project(bare=claude is None, claude=claude)
                self.put_foreign_script(project)
                result = self.link(project)
                self.assertEqual(self.script(project).read_bytes(), self.FOREIGN_SCRIPT)
                self.assertFalse([line for line in self.lines(result) if 'statusline' in line.lower()], result.stdout)

    def test_a_foreign_script_stays_when_our_entry_is_already_gone(self):
        project = self.project()
        self.installed(project)
        data = self.settings(project)
        data.pop('statusLine')
        descend(project, LOCAL).write_bytes(dumped(data))
        self.script(project).write_bytes(self.FOREIGN_SCRIPT)
        self.set_claude(project, {'statusline': False})
        self.link(project)
        self.assertEqual(self.script(project).read_bytes(), self.FOREIGN_SCRIPT)

    def test_an_orphaned_copy_equal_to_the_library_is_removed(self):
        project = self.project()
        self.installed(project)
        data = self.settings(project)
        data.pop('statusLine')
        descend(project, LOCAL).write_bytes(dumped(data))
        self.set_claude(project, {'statusline': False})
        self.link(project)
        self.assertFalse(self.script(project).exists())

    def test_the_copy_goes_with_our_entry_even_when_edited(self):
        project = self.project()
        self.installed(project)
        self.script(project).write_bytes(self.FOREIGN_SCRIPT)
        self.set_claude(project, {'statusline': False})
        self.link(project)
        self.assert_gone(project)

    def test_an_entry_with_another_path_is_not_removed_without_the_key(self):
        stale = our_value(f'python3 /old/checkout/{NEEDLE}')
        for claude in (None, {'statusline': False}, False):
            with self.subTest(claude=claude):
                project = self.project(local=dumped({'statusLine': stale}), bare=claude is None, claude=claude)
                self.link(project)
                self.assertEqual(self.settings(project)['statusLine'], stale)

    def test_switching_on_again_installs_again(self):
        project = self.project()
        self.installed(project)
        self.set_claude(project, {'statusline': False})
        self.link(project)
        self.set_claude(project, {'statusline': True})
        self.link(project)
        self.assertEqual(self.entry(project)['refreshInterval'], REFRESH)
        self.assertTrue(self.script(project).is_file())


class ForeignAppearsLater(LinkBase):
    """After an installation a foreign status line or `disableAllHooks` appears: our entry and copy go, with a warning that says so."""

    def place(self, project, name, data, config_dir):
        if name == 'project':
            self.write_settings(project, data, 'settings.json')
        elif name == 'user':
            self.write_user_settings(data)
        else:
            config_dir.mkdir()
            (config_dir / 'settings.json').write_bytes(dumped(data))

    def test_our_entry_and_copy_are_removed_with_a_warning(self):
        tokens = {'project': '.claude/settings.json', 'user': '~/.claude/settings.json', 'config-dir': 'CLAUDE_CONFIG_DIR'}
        for name, token in tokens.items():
            for data in ({'statusLine': FOREIGN_BAR}, {'disableAllHooks': True}):
                with self.subTest(file=name, data=sorted(data)):
                    (self.home / '.claude' / 'settings.json').unlink(missing_ok=True)
                    project = self.project()
                    self.installed(project)
                    config_dir = descend(project, '.konto-claude')
                    self.place(project, name, data, config_dir)
                    with mock.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(config_dir)} if name == 'config-dir' else {}):
                        result = self.link(project)
                    self.assertNotIn('statusLine', self.settings(project))
                    self.assertFalse(self.script(project).exists())
                    text = [posix(line) for line in self.warns(result) if token in posix(line)]
                    self.assertTrue(any('removed the skillsync status line entry' in line for line in text), result.stdout)
                    self.assertFalse(any('not installed' in line for line in text), result.stdout)

    def test_only_not_installed_when_there_was_no_entry(self):
        project = self.project()
        self.write_user_settings({'statusLine': FOREIGN_BAR})
        result = self.link(project)
        text = [line for line in self.warns(result) if 'statusLine' in line]
        self.assertTrue(any('not installed' in line for line in text), result.stdout)
        self.assertFalse(any('removed' in line for line in text), result.stdout)


class UserConfigDirectory(LinkBase):
    def test_a_foreign_bar_in_the_claude_config_dir_blocks_and_names_the_file(self):
        project = self.project()
        config_dir = child(self.work, 'konto-claude')
        config_dir.mkdir()
        (config_dir / 'settings.json').write_bytes(dumped({'statusLine': FOREIGN_BAR}))
        with mock.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(config_dir)}):
            result = self.link(project)
            found = [f for f in self.bar_findings(self.doctor(project)) if f['level'] == 'warning']
        self.assert_no_statusline(project)
        text = [line for line in self.warns(result) if 'statusLine' in line]
        self.assertTrue(any('CLAUDE_CONFIG_DIR' in line and re.search('priorit', line, re.IGNORECASE) for line in text), result.stdout)
        self.assertTrue(any('CLAUDE_CONFIG_DIR' in f['reason'] or 'CLAUDE_CONFIG_DIR' in f['path'] for f in found), found)

    def test_a_relative_claude_config_dir_is_ignored(self):
        project = self.project()
        with mock.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': 'wzgledny'}):
            self.link(project)
        self.assertIn('statusLine', self.settings(project))


# --------------------------------------------------------------------------------------------------------------
# 3. doctor (K6)
# --------------------------------------------------------------------------------------------------------------
class Doctor(LinkBase):
    def test_a_healthy_installation_has_no_warning_and_no_error_about_the_bar(self):
        project = self.project()
        self.installed(project)
        bad = [finding for finding in self.bar_findings(self.doctor(project)) if finding['level'] in ('warning', 'error')]
        self.assertEqual(bad, [])

    def test_a_foreign_bar_is_a_warning_that_names_the_file_and_the_priority(self):
        cases_ = (('local', 'settings.local.json'), ('project', '.claude/settings.json'), ('user', '~/.claude/settings.json'))
        for which, token in cases_:
            with self.subTest(file=which):
                project = self.project()
                if which == 'local':
                    descend(project, LOCAL).parent.mkdir(parents=True)
                    descend(project, LOCAL).write_bytes(dumped({'statusLine': FOREIGN_BAR}))
                elif which == 'project':
                    descend(project, '.claude/settings.json').parent.mkdir(parents=True)
                    descend(project, '.claude/settings.json').write_bytes(dumped({'statusLine': FOREIGN_BAR}))
                else:
                    self.write_user_settings({'statusLine': FOREIGN_BAR})
                self.link(project)
                found = [f for f in self.bar_findings(self.doctor(project)) if f['level'] == 'warning']
                self.assertTrue(found, 'doctor is silent about the foreign bar')
                text = posix(' '.join(f"{f['path']} {f['reason']}" for f in found))
                if which == 'user':
                    self.assertTrue(token in text or f'{posix(self.home)}/.claude/settings.json' in text, text)
                else:
                    self.assertIn(token, text)
                self.assertRegex(text, r'(?i)priorit|priorytet')

    def test_disable_all_hooks_is_a_warning(self):
        for which in ('project', 'user'):
            with self.subTest(file=which):
                project = self.project()
                if which == 'project':
                    descend(project, '.claude').mkdir(parents=True)
                    descend(project, '.claude/settings.json').write_bytes(dumped({'disableAllHooks': True}))
                else:
                    self.write_user_settings({'disableAllHooks': True})
                self.link(project)
                found = [f for f in self.doctor(project) if f['level'] == 'warning' and re.search('disableAllHooks', f"{f['path']} {f['reason']}")]
                self.assertTrue(found)

    def test_an_outdated_copy_of_the_script_is_a_warning(self):
        project = self.project()
        self.installed(project)
        self.script(project).write_text('# edytowana kopia\n', encoding='utf-8')
        found = [f for f in self.doctor(project) if f['level'] == 'warning' and 'statusline.py' in f['path']]
        self.assertTrue(found, 'doctor does not compare the copy with the library')
        self.assertRegex(found[0]['reason'], r'(?i)librar|bibliotek')

    def test_an_info_names_the_version_and_the_unconfirmed_vscode_display_never_a_warning(self):
        project = self.project()
        self.installed(project)
        found = [f for f in self.doctor(project) if 'refreshInterval' in f['reason']]
        self.assertEqual([f['level'] for f in found], ['info'])
        self.assertIn('2.1.97', found[0]['reason'])
        self.assertRegex(found[0]['reason'], r'VS Code')

    def test_no_such_info_without_the_key(self):
        project = self.project(bare=True)
        self.link(project)
        self.assertEqual([f for f in self.doctor(project) if 'refreshInterval' in f['reason']], [])

    def test_a_missing_script_with_our_entry_is_a_warning(self):
        project = self.project()
        self.installed(project)
        self.script(project).unlink()
        found = [f for f in self.doctor(project) if f['level'] == 'warning' and 'statusline.py' in f['path']]
        self.assertTrue(found)


# --------------------------------------------------------------------------------------------------------------
# 4. K8 and the collision with the hooks of CLAUDE-3
# --------------------------------------------------------------------------------------------------------------
class NothingChangesWithoutTheKeys(LinkBase):
    def tree(self, project):
        names = sorted(entry.name for entry in descend(project, HOOKS_DIR).iterdir()) if descend(project, HOOKS_DIR).is_dir() else []
        return {'settings': descend(project, LOCAL).read_bytes(), 'hooks': names, 'gitignore': self.gitignore_lines(project)}

    def test_without_keys_link_writes_no_bar_file_and_no_entry(self):
        project = self.project(bare=True)
        self.link(project)
        self.assert_no_statusline(project)

    def test_the_same_tree_with_no_key_with_false_keys_and_with_an_empty_section(self):
        trees = []
        for claude in (None, {'statusline': False, 'output_styles': False}, {}):
            project = self.project(bare=claude is None, claude=claude)
            self.link(project)
            self.assert_no_statusline(project)
            trees.append(self.tree(project))
        self.assertEqual(trees[0], trees[1])
        self.assertEqual(trees[0], trees[2])

    def test_a_foreign_bar_and_disable_all_hooks_in_the_user_file_say_nothing_when_the_bar_is_off(self):
        self.write_user_settings({'statusLine': FOREIGN_BAR, 'disableAllHooks': True})
        project = self.project(bare=True)
        result = self.link(project)
        self.assertFalse([line for line in self.lines(result) if re.search('statusLine|disableAllHooks', line)], result.stdout)
        self.assertEqual(self.bar_findings(self.doctor(project)), [], 'doctor adds neither a warning nor an info')

    def test_doctor_adds_nothing_about_the_bar_without_the_keys(self):
        project = self.project(bare=True)
        self.link(project)
        self.assertEqual(self.bar_findings(self.doctor(project)), [])

    def test_an_existing_foreign_local_bar_is_untouched_without_the_keys(self):
        project = self.project(bare=True, local=dumped({'statusLine': FOREIGN_BAR, 'env': {'A': '1'}}))
        result = self.link(project)
        data = self.settings(project)
        self.assertEqual(data['statusLine'], FOREIGN_BAR)
        self.assertFalse([line for line in self.warns(result) if 'statusLine' in line])

    def test_a_new_worktree_needs_its_own_link(self):
        first = self.project()
        self.link(first)
        second = self.project(name='drugi-worktree')
        self.assert_no_statusline(second, 'before the link of the new worktree')
        self.assertFalse(descend(second, LOCAL).exists())


class CollisionWithTheHooksOfClaude3(LinkBase):
    def feature_pair(self):
        with_bar = self.project(skills=('feature',), hooks={})
        without = self.project(skills=('feature',), hooks={}, bare=True)
        return with_bar, without

    def test_the_hooks_section_and_the_other_keys_are_the_same_with_and_without_the_bar(self):
        with_bar, without = self.feature_pair()
        self.link(with_bar)
        self.link(without)
        mine, other = self.settings(with_bar), self.settings(without)
        self.assertIn('statusLine', mine)
        self.assertNotIn('statusLine', other)
        mine.pop('statusLine')
        self.assertEqual(mine, other)
        self.assertIn('SessionStart', other['hooks'], 'the premise: the hooks of CLAUDE-3 are on in a feature project')

    def test_link_prints_the_same_warnings_with_and_without_the_bar(self):
        with_bar, without = self.feature_pair()
        first, second = self.link(with_bar), self.link(without)
        normalise = lambda result, project: sorted(line.replace(str(project), '<P>') for line in self.warns(result))
        self.assertEqual(normalise(first, with_bar), normalise(second, without))

    def test_doctor_reports_the_same_about_the_hooks_with_and_without_the_bar(self):
        with_bar, without = self.feature_pair()
        self.link(with_bar)
        self.link(without)
        pick = lambda project: sorted((f['level'], f['path'], f['reason']) for f in self.doctor(project)
                                      if not mentions_bar(f) and ('hooks/' in f['path'] or 'settings' in f['path'] or 'hooks' in f['reason']))
        self.assertEqual(pick(with_bar), pick(without))

    def run_guard(self, project, relative):
        guard = descend(project, f'{HOOKS_DIR}/guard-generated.py')
        payload = {'tool_name': 'Write', 'tool_input': {'file_path': str(descend(project, relative))}, 'cwd': str(project)}
        done = subprocess.run([sys.executable, str(guard), '--mode', 'warn'], input=json.dumps(payload).encode('utf-8'), capture_output=True,
                              timeout=60, env=dict(os.environ), cwd=str(project))
        return done.returncode, done.stdout.decode('utf-8', 'replace')

    def test_the_guard_of_generated_files_treats_the_copy_of_the_bar_like_the_other_hook_scripts(self):
        project = self.project(skills=('feature',), hooks={})
        git(project, 'init', '-q')
        self.link(project)
        code_control, out_control = self.run_guard(project, '.claude/agents/rola.md')
        self.assertEqual(code_control, 0)
        self.assertIn('additionalContext', out_control, 'the control: a generated file is warned about (the harness can fail)')
        for relative in (f'{HOOKS_DIR}/statusline.py', f'{HOOKS_DIR}/session-context.py'):
            code, out = self.run_guard(project, relative)
            self.assertEqual((code, out), (0, ''), relative)


# --------------------------------------------------------------------------------------------------------------
# harness self-checks: the negative assertions can fail
# --------------------------------------------------------------------------------------------------------------
class HarnessSelfChecks(LinkBase):
    def plant(self, project):
        target = self.script(project)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(SOURCE.read_bytes())
        descend(project, LOCAL).parent.mkdir(parents=True, exist_ok=True)
        descend(project, LOCAL).write_bytes(dumped({'statusLine': our_value(f'python3 {target}')}))

    def test_assert_no_statusline_refuses_an_installed_bar(self):
        project = self.project()
        self.plant(project)
        with self.assertRaises(AssertionError):
            self.assert_no_statusline(project)

    def test_assert_no_statusline_refuses_a_lone_script_and_a_lone_gitignore_line(self):
        project = self.project()
        self.script(project).parent.mkdir(parents=True)
        self.script(project).write_bytes(b'#\n')
        with self.assertRaises(AssertionError):
            self.assert_no_statusline(project)
        self.script(project).unlink()
        descend(project, '.claude/.gitignore').write_text('hooks/statusline.py\n', encoding='utf-8')
        with self.assertRaises(AssertionError):
            self.assert_no_statusline(project)

    def test_assert_no_statusline_accepts_a_clean_project_and_a_foreign_bar(self):
        project = self.project()
        self.assert_no_statusline(project)
        descend(project, LOCAL).parent.mkdir(parents=True)
        descend(project, LOCAL).write_bytes(dumped({'statusLine': FOREIGN_BAR}))
        self.assert_no_statusline(project)

    def test_the_bar_finding_filter_sees_a_planted_finding(self):
        planted = [{'level': 'warning', 'path': '.claude/settings.local.json', 'reason': 'A foreign statusLine wins'},
                   {'level': 'info', 'path': 'x', 'reason': 'nic'}]
        self.assertEqual(len(self.bar_findings(planted)), 1)

    def test_the_command_check_refuses_a_command_that_runs_no_script(self):
        project = self.project()
        missing = child(self.work, 'nie-ma.py')
        command = f'{shlex.quote(sys.executable)} {shlex.quote(str(missing))}' if os.name != 'nt' else f'"{sys.executable}" "{missing}"'
        code, out = self.run_command(command, project)
        with self.assertRaises(AssertionError):
            self.assert_bar_output(code, out)

    def test_the_command_check_accepts_the_real_script(self):
        project = self.project()
        target = self.script(project)
        target.parent.mkdir(parents=True)
        target.write_bytes(SOURCE.read_bytes())
        command = f'{shlex.quote(sys.executable)} {shlex.quote(str(target))}' if os.name != 'nt' else f'"{sys.executable}" "{target}"'
        code, out = self.run_command(command, project)
        self.assert_bar_output(code, out)

    def test_the_split_of_a_quoted_command_keeps_a_path_with_spaces_together(self):
        quoted = f'{shlex.quote(sys.executable)} {shlex.quote("/a b/it\'s/x.py")}' if os.name != 'nt' else f'"{sys.executable}" "C:\\a b\\x.py"'
        self.assertEqual(len(split_command(quoted)), 2)


if __name__ == '__main__':
    unittest.main()
