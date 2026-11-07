"""R2b E3 (K16, K17): zamknięcie planu nie do pominięcia.

K16: `doctor` ostrzega o planie `done` bez rekordu `final` w przebiegu z linii `Przebieg:`.
K17: Stop hook `.claude/hooks/check-closure.py` z biblioteki (blokada raz) oraz jego generowanie przez `link`.
Plik hooka pochodzi z biblioteki wspólnej (`$SKILLSYNC_TEST_LIBRARY/hooks/check-closure.py`; bez zmiennej testy hooka są pomijane)."""
import json
import os
import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import transitional_config  # noqa: E402
from test_skillsync_workflow import CLI, WorkflowFixture  # noqa: E402
from layers_helpers import needs_os_symlinks, needs_sh  # noqa: E402
from cli_module import skillsync as skillsync_cli  # noqa: E402

# The hook belongs to the shared library. The test reads it only from `SKILLSYNC_TEST_LIBRARY`: no fallback to the
# machine library in `~`, so a run does not depend on whatever happens to be installed there (K13: explicit skip).
_TEST_LIBRARY = os.environ.get('SKILLSYNC_TEST_LIBRARY')
HOOK_SOURCE = Path(_TEST_LIBRARY) / 'hooks' / 'check-closure.py' if _TEST_LIBRARY else None
needs_hook = unittest.skipUnless(HOOK_SOURCE is not None and HOOK_SOURCE.is_file(),
                                 'set SKILLSYNC_TEST_LIBRARY to a library with hooks/check-closure.py')
PLAN = 'docs/plans/2026-11-05-x.md'


def plan_text(status='done', run='abc', title='X'):
    lines = [f'# {title}', '', f'Status: {status}']
    if run is not None:
        lines.append(f'Przebieg: {run}')
    return '\n'.join(lines) + '\n'


def run_json(run_id='abc', final=True):
    records = [{'step': 'a', 'status': 'PASS', 'next': 'n'}, {'step': 'b', 'status': 'PASS', 'next': 'n'}]
    if final:
        records[-1]['final'] = True
    return json.dumps({'schema_version': 1, 'run_id': run_id, 'checkpoints': records})


# ---------------------------------------------------------------------------------------------------------------
# K16: doctor
# ---------------------------------------------------------------------------------------------------------------
class DoctorClosure(WorkflowFixture):
    def setUp(self):
        super().setUp()
        self.manifest['skills'] = ['feature']  # ostrzeżenie o braku linii `Przebieg:` wymaga skillu workflow
        skill = self.library / 'skills/feature'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: feature\ndescription: Fixture\n---\nSkill', encoding='utf-8')
        installed = self.root / '.github/skills/feature'
        installed.mkdir(parents=True)
        (installed / 'SKILL.md').write_bytes((skill / 'SKILL.md').read_bytes())
        self.manifest['agents'] = []
        self.manifest['claude'] = False
        self.manifest['codex'] = False
        self.save()

    def plan_warnings(self, name):
        report = self.report('doctor')
        self.assertEqual(report['status'], 'PASS', report)
        return [f for f in report['findings'] if f['level'] == 'warning' and name in f['path']]

    def put_plan(self, name='2026-11-05-x.md', **kw):
        self.write(f'docs/plans/{name}', plan_text(**kw))

    def test_done_plan_without_final_record_warns_and_keeps_status(self):
        self.put_plan()
        self.write('.skillsync/runs/abc.json', run_json(final=False))
        self.assertEqual(len(self.plan_warnings('2026-11-05-x.md')), 1)

    def test_done_plan_with_missing_run_file_warns(self):
        self.put_plan()
        self.assertEqual(len(self.plan_warnings('2026-11-05-x.md')), 1)

    def test_final_record_in_attempt_file_clears_the_warning(self):
        self.put_plan()
        self.write('.skillsync/runs/abc.json', run_json(final=False))
        self.write('.skillsync/runs/abc.2.json', run_json(run_id='abc.2', final=True))
        self.assertEqual(self.plan_warnings('2026-11-05-x.md'), [])

    def test_final_record_in_the_main_file_clears_the_warning(self):
        self.put_plan()
        self.write('.skillsync/runs/abc.json', run_json())
        self.assertEqual(self.plan_warnings('2026-11-05-x.md'), [])

    def test_unrelated_run_with_final_does_not_clear_the_warning(self):
        self.put_plan()
        self.write('.skillsync/runs/abcd.json', run_json(run_id='abcd'))
        self.assertEqual(len(self.plan_warnings('2026-11-05-x.md')), 1)

    def test_in_progress_plan_is_not_checked(self):
        self.put_plan(status='in_progress')
        self.assertEqual(self.plan_warnings('2026-11-05-x.md'), [])

    def test_done_without_run_line_warns_from_2026_11_01(self):
        self.put_plan(name='2026-11-01-x.md', run=None)
        self.assertEqual(len(self.plan_warnings('2026-11-01-x.md')), 1)

    def test_done_without_run_line_is_not_judged_without_the_workflow_skill(self):
        self.manifest['skills'] = []
        self.save()
        self.put_plan(name='2026-11-01-x.md', run=None)
        self.assertEqual(self.plan_warnings('2026-11-01-x.md'), [])

    def test_missing_final_is_judged_without_the_workflow_skill(self):
        self.manifest['skills'] = []
        self.save()
        self.put_plan()
        self.assertEqual(len(self.plan_warnings('2026-11-05-x.md')), 1)

    @needs_os_symlinks
    def test_skillsync_symlink_is_treated_as_a_missing_run(self):
        self.put_plan()
        other = self.base / 'elsewhere'
        (other / 'runs').mkdir(parents=True)
        (other / 'runs/abc.json').write_text(run_json(), encoding='utf-8')
        (self.root / '.skillsync').symlink_to(other)
        self.assertEqual(len(self.plan_warnings('2026-11-05-x.md')), 1)

    def test_done_without_run_line_is_not_judged_before_2026_11_01(self):
        self.put_plan(name='2026-10-31-x.md', run=None)
        self.assertEqual(self.plan_warnings('2026-10-31-x.md'), [])

    def test_done_without_run_line_and_date_in_name_is_not_judged(self):
        self.put_plan(name='x.md', run=None)
        self.assertEqual(self.plan_warnings('x.md'), [])

    def test_backticked_run_id_is_read(self):
        self.put_plan(run='`abc`')
        self.write('.skillsync/runs/abc.json', run_json())
        self.assertEqual(self.plan_warnings('2026-11-05-x.md'), [])
        self.write('.skillsync/runs/abc.json', run_json(final=False))
        self.assertEqual(len(self.plan_warnings('2026-11-05-x.md')), 1)

    def test_traversal_run_id_warns_about_invalid_id_without_reading_outside(self):
        self.put_plan(run='../x')
        # Gdyby doctor wyszedł poza magazyn, ten plik z rekordem final zdjąłby ostrzeżenie.
        self.write('.skillsync/x.json', run_json(run_id='x'))
        found = self.plan_warnings('2026-11-05-x.md')
        self.assertEqual(len(found), 1, found)
        self.assertIn('id', found[0]['reason'].lower())

    def test_corrupt_run_file_does_not_crash_doctor(self):
        self.put_plan()
        self.write('.skillsync/runs/abc.json', '{ not json')
        report = self.report('doctor')
        self.assertEqual(report['status'], 'PASS', report)


# ---------------------------------------------------------------------------------------------------------------
# K17: hook
# ---------------------------------------------------------------------------------------------------------------
@needs_hook
class ClosureHook(WorkflowFixture):
    def setUp(self):
        super().setUp()
        self.hook = self.root / '.claude/hooks/check-closure.py'
        self.hook.parent.mkdir(parents=True)
        self.hook.write_bytes(HOOK_SOURCE.read_bytes())
        self.write('README.md', 'fixture\n')
        self.write('.gitignore', '.claude/\n.skillsync/\n')

    def fire(self, payload=None, cwd=None, raw=None, root=None):
        root = root or self.root
        if raw is None:
            raw = json.dumps({'cwd': str(root), 'stop_hook_active': False, **(payload or {})})
        result = subprocess.run([sys.executable, str(self.hook)], input=raw, capture_output=True, text=True,
                                timeout=20, cwd=cwd or root)
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        return result

    def blocked(self, **kw):
        result = self.fire(**kw)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        return result.stderr

    def allowed(self, **kw):
        result = self.fire(**kw)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def commit_plan(self, **kw):
        self.write(PLAN, plan_text(**kw))
        self.init_git()

    def test_untracked_done_plan_without_final_blocks_with_actionable_message(self):
        self.init_git()
        self.write(PLAN, plan_text())
        self.write('.skillsync/runs/abc.json', run_json(final=False))
        message = self.blocked()
        for fragment in ('2026-11-05-x.md', 'abc', '--final', 'retro'):
            self.assertIn(fragment, message)

    def test_plan_changed_against_head_blocks(self):
        self.commit_plan(status='in_progress')
        self.write(PLAN, plan_text(status='done'))
        self.write('.skillsync/runs/abc.json', run_json(final=False))
        self.assertIn('abc', self.blocked())

    def test_missing_run_file_blocks(self):
        self.init_git()
        self.write(PLAN, plan_text())
        self.assertIn('--final', self.blocked())

    def test_final_record_allows(self):
        self.init_git()
        self.write(PLAN, plan_text())
        self.write('.skillsync/runs/abc.json', run_json(final=True))
        self.allowed()

    def test_final_record_in_attempt_file_allows(self):
        self.init_git()
        self.write(PLAN, plan_text())
        self.write('.skillsync/runs/abc.json', run_json(final=False))
        self.write('.skillsync/runs/abc.2.json', run_json(run_id='abc.2', final=True))
        self.allowed()

    def test_backticked_run_id_is_read(self):
        self.init_git()
        self.write(PLAN, plan_text(run='`abc`'))
        self.assertIn('abc', self.blocked())
        self.write('.skillsync/runs/abc.json', run_json())
        self.allowed()

    def test_stop_hook_active_never_blocks(self):
        self.init_git()
        self.write(PLAN, plan_text())
        self.allowed(payload={'stop_hook_active': True})

    def test_done_plan_unchanged_against_head_is_ignored(self):
        self.commit_plan(status='done')
        self.allowed()

    def test_in_progress_plan_is_ignored(self):
        self.init_git()
        self.write(PLAN, plan_text(status='in_progress'))
        self.allowed()

    def test_plan_without_run_line_is_ignored(self):
        self.init_git()
        self.write(PLAN, plan_text(run=None))
        self.allowed()

    def test_directory_without_git_is_allowed(self):
        self.write(PLAN, plan_text())
        self.allowed()

    def test_broken_stdin_json_is_allowed(self):
        self.init_git()
        self.write(PLAN, plan_text())
        self.allowed(raw='{ not json')

    def test_empty_stdin_is_allowed(self):
        self.allowed(raw='')

    def test_broken_run_json_is_allowed(self):
        self.init_git()
        self.write(PLAN, plan_text())
        self.write('.skillsync/runs/abc.json', '{ not json')
        self.allowed()

    def test_traversal_run_id_is_not_read_and_does_not_block(self):
        self.init_git()
        self.write(PLAN, plan_text(run='../x'))
        # Plik poza magazynem bez rekordu final: odczyt zablokowałby turę.
        self.write('.skillsync/x.json', run_json(run_id='x', final=False))
        self.allowed()

    def test_payload_cwd_decides_where_the_hook_looks(self):
        self.init_git()
        self.write(PLAN, plan_text())
        with tempfile.TemporaryDirectory(prefix='skillsync-closure-elsewhere-') as elsewhere:
            self.assertIn('2026-11-05-x.md', self.blocked(cwd=elsewhere))

    @needs_os_symlinks
    def test_skillsync_symlink_is_not_read_and_allows(self):
        self.init_git()
        self.write(PLAN, plan_text())
        other = self.base / 'elsewhere'
        (other / 'runs').mkdir(parents=True)
        (other / 'runs/abc.json').write_text(run_json(final=False), encoding='utf-8')
        (self.root / '.skillsync').symlink_to(other)
        self.allowed()

    def test_only_flat_plans_without_readme_are_read(self):
        self.init_git()
        self.write('docs/plans/README.md', plan_text())
        self.write('docs/plans/sub/2026-11-05-y.md', plan_text())
        self.allowed()

    def test_hook_is_read_only(self):
        self.init_git()
        self.write(PLAN, plan_text())
        before = self.git('status', '--porcelain', '--ignored')
        self.blocked()
        self.assertEqual(self.git('status', '--porcelain', '--ignored'), before)

    def test_hook_uses_only_the_standard_library(self):
        source = HOOK_SOURCE.read_text(encoding='utf-8')
        self.assertNotIn('import yaml', source)
        self.assertNotIn('skillsync_workflow', source)


# ---------------------------------------------------------------------------------------------------------------
# K17: generowanie przez link
# ---------------------------------------------------------------------------------------------------------------
@needs_hook
class ClosureLink(WorkflowFixture):
    HOOK_PATH = '.claude/hooks/check-closure.py'
    LOCAL = '.claude/settings.local.json'

    def setUp(self):
        super().setUp()
        (self.library / 'hooks').mkdir()
        (self.library / 'hooks/check-closure.py').write_bytes(HOOK_SOURCE.read_bytes())
        skill = self.library / 'skills/feature'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: feature\ndescription: Fixture\n---\nSkill', encoding='utf-8')
        (self.library / 'instructions').mkdir()
        (self.library / 'instructions/_base.md').write_text('# Base\n', encoding='utf-8')
        self.home = self.base / 'home'
        self.home.mkdir()
        self.config = self.base / 'config.yaml'
        self.config.write_text(yaml.safe_dump(transitional_config(self.library, legacy_key=False, tags={})), encoding='utf-8')
        self.manifest['skills'] = ['feature']
        self.save()

    def link(self, *args, code=0):
        result = subprocess.run([sys.executable, str(CLI), 'link', str(self.root), *args], capture_output=True,
                                text=True, timeout=30, cwd=self.root,
                                env={**os.environ, 'SKILLSYNC_CONFIG': str(self.config), 'HOME': str(self.home)})
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def local(self):
        return json.loads((self.root / self.LOCAL).read_text(encoding='utf-8'))

    def closure_commands(self, data):
        found = []
        for group in (data.get('hooks') or {}).get('Stop', []):
            for hook in group.get('hooks', []):
                if 'check-closure.py' in json.dumps(hook):
                    found.append(hook)
        return found

    def tree(self):
        result = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            for name in dirnames:
                result[os.path.relpath(os.path.join(dirpath, name), self.root)] = None
            for name in filenames:
                path = Path(dirpath, name)
                result[os.path.relpath(path, self.root)] = path.read_bytes() if not path.is_symlink() else 'link'
        return result

    def test_feature_project_gets_hook_and_local_stop_entry(self):
        self.link()
        self.assertTrue((self.root / self.HOOK_PATH).is_file())
        self.assertEqual((self.root / self.HOOK_PATH).read_bytes(), HOOK_SOURCE.read_bytes())
        self.assertEqual(len(self.closure_commands(self.local())), 1)

    def test_shared_settings_json_never_gets_the_entry(self):
        self.link()
        shared = self.root / '.claude/settings.json'
        if shared.exists():
            self.assertEqual(self.closure_commands(json.loads(shared.read_text(encoding='utf-8'))), [])

    def test_second_link_does_not_duplicate_the_entry(self):
        self.link()
        self.link()
        self.assertEqual(len(self.closure_commands(self.local())), 1)

    def test_existing_permissions_and_foreign_hooks_survive(self):
        existing = {'permissions': {'allow': ['Bash(git fetch *)']},
                    'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': 'pre'}]}],
                              'Stop': [{'matcher': '', 'hooks': [{'type': 'command', 'command': 'echo foreign'}]}]}}
        self.write(self.LOCAL, json.dumps(existing))
        self.link()
        data = self.local()
        self.assertEqual(data['permissions'], existing['permissions'])
        self.assertEqual(data['hooks']['PreToolUse'], existing['hooks']['PreToolUse'])
        self.assertIn('echo foreign', json.dumps(data['hooks']['Stop']))
        self.assertEqual(len(self.closure_commands(data)), 1)

    def test_stop_closure_false_removes_only_our_entry(self):
        foreign = {'hooks': {'Stop': [{'matcher': '', 'hooks': [{'type': 'command', 'command': 'echo foreign'}]}]},
                   'permissions': {'allow': ['Read(x)']}}
        self.write(self.LOCAL, json.dumps(foreign))
        self.link()
        self.assertEqual(len(self.closure_commands(self.local())), 1)
        self.manifest['hooks'] = {'stop_closure': False}
        self.save()
        self.link()
        data = self.local()
        self.assertEqual(self.closure_commands(data), [])
        self.assertIn('echo foreign', json.dumps(data['hooks']['Stop']))
        self.assertEqual(data['permissions'], foreign['permissions'])

    def test_project_without_feature_gets_no_hook(self):
        self.manifest['skills'] = []
        self.save()
        self.link()
        self.assertFalse((self.root / self.HOOK_PATH).exists())
        local = self.root / self.LOCAL
        if local.exists():
            self.assertEqual(self.closure_commands(self.local()), [])

    def test_claude_gitignore_covers_local_settings_backup_and_hook(self):
        self.link()
        lines = (self.root / '.claude/.gitignore').read_text(encoding='utf-8').splitlines()
        for entry in ('settings.local.json', 'settings.local.json.skillsync-backup', 'hooks/check-closure.py'):
            self.assertIn(entry, lines)

    def test_invalid_local_settings_json_is_left_byte_identical(self):
        raw = b'{ "permissions": [broken'
        path = self.root / self.LOCAL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        self.link()
        self.assertEqual(path.read_bytes(), raw)

    def test_dry_run_writes_nothing(self):
        before = self.tree()
        self.link('--dry-run')
        self.assertEqual(self.tree(), before)
        self.assertFalse((self.root / self.HOOK_PATH).exists())

    def entry_command(self):
        return self.closure_commands(self.local())[0]['command']

    def run_entry(self, cwd, env_dir):
        env = {k: v for k, v in os.environ.items() if k != 'CLAUDE_PROJECT_DIR'}
        if env_dir is not None:
            env['CLAUDE_PROJECT_DIR'] = str(env_dir)
        return subprocess.run(['sh', '-c', self.entry_command()], input=json.dumps({'cwd': str(cwd)}),
                              capture_output=True, text=True, timeout=20, cwd=cwd, env=env)

    @needs_sh
    def test_entry_command_works_from_a_subdirectory_and_blocks(self):
        self.link()
        self.init_git()
        self.write(PLAN, plan_text())
        (self.root / 'sub').mkdir()
        result = self.run_entry(self.root / 'sub', self.root)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def run_session(self, session_id, tmpdir):
        env = dict(os.environ, TMPDIR=str(tmpdir), CLAUDE_PROJECT_DIR=str(self.root))
        payload = {'cwd': str(self.root), 'session_id': session_id, 'stop_hook_active': False}
        return subprocess.run(['sh', '-c', self.entry_command()], input=json.dumps(payload), capture_output=True,
                              text=True, timeout=20, cwd=self.root, env=env)

    def session_fixture(self):
        self.link()
        self.init_git()
        self.write(PLAN, plan_text())
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        return tmpdir

    @needs_sh
    def test_one_session_is_blocked_once_per_plan(self):
        tmpdir = self.session_fixture()
        self.assertEqual(self.run_session('s1', tmpdir).returncode, 2)
        # stop_hook_active po użyciu narzędzia jest znów false; blokada się nie powtarza.
        self.assertEqual(self.run_session('s1', tmpdir).returncode, 0)

    @needs_sh
    def test_without_session_id_the_block_is_not_repeated(self):
        tmpdir = self.session_fixture()
        env = dict(os.environ, TMPDIR=str(tmpdir), CLAUDE_PROJECT_DIR=str(self.root))
        codes = [subprocess.run(['sh', '-c', self.entry_command()], input=json.dumps({'cwd': str(self.root)}),
                                capture_output=True, text=True, timeout=20, cwd=self.root, env=env).returncode
                 for _ in range(2)]
        self.assertEqual(codes, [2, 0])

    @needs_sh
    def test_another_session_is_blocked_again(self):
        tmpdir = self.session_fixture()
        self.assertEqual(self.run_session('s1', tmpdir).returncode, 2)
        self.assertEqual(self.run_session('s2', tmpdir).returncode, 2)

    @needs_sh
    def test_another_plan_in_the_same_session_is_blocked(self):
        tmpdir = self.session_fixture()
        self.assertEqual(self.run_session('s1', tmpdir).returncode, 2)
        self.write('docs/plans/2026-11-06-y.md', plan_text())
        result = self.run_session('s1', tmpdir)
        self.assertEqual(result.returncode, 2)
        self.assertIn('2026-11-06-y.md', result.stderr)
        self.assertNotIn(PLAN, result.stderr)

    @needs_sh
    def test_marker_write_failure_lets_the_turn_end(self):
        tmpdir = self.session_fixture()
        uid = getattr(os, 'getuid', lambda: 'user')()
        with open(os.path.join(tmpdir, f'skillsync-closure-{uid}'), 'w', encoding='utf-8') as handle:
            handle.write('plik zamiast katalogu')
        result = self.run_session('s1', tmpdir)
        self.assertEqual((result.returncode, result.stderr), (0, ''))

    @needs_sh
    def test_entry_command_blocks_when_the_session_started_in_a_subdirectory(self):
        # Prawdziwy Claude Code ustawia CLAUDE_PROJECT_DIR na katalog startu sesji, a settings czyta z katalogu wyżej.
        self.link()
        self.init_git()
        self.write(PLAN, plan_text())
        (self.root / 'sub').mkdir()
        self.assertEqual(self.run_entry(self.root / 'sub', self.root / 'sub').returncode, 2)

    @needs_sh
    def test_entry_command_without_variable_uses_the_current_directory(self):
        self.link()
        self.init_git()
        self.write(PLAN, plan_text())
        self.assertEqual(self.run_entry(self.root, None).returncode, 2)

    @needs_sh
    def test_entry_command_without_script_or_outside_git_exits_zero(self):
        self.link()
        self.init_git()
        self.write(PLAN, plan_text())
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        self.assertEqual(self.run_entry(outside, outside).returncode, 0)
        (self.root / self.HOOK_PATH).unlink()
        self.assertEqual(self.run_entry(self.root, self.root).returncode, 0)

    def test_old_relative_entry_is_replaced_by_one_current_entry(self):
        path = self.root / self.LOCAL
        path.parent.mkdir(parents=True, exist_ok=True)
        old = {'matcher': '', 'hooks': [{'type': 'command', 'command': 'python3 .claude/hooks/check-closure.py'}]}
        path.write_text(json.dumps({'hooks': {'Stop': [old]}}), encoding='utf-8')
        self.link()
        commands = self.closure_commands(self.local())
        self.assertEqual(len(commands), 1)
        self.assertIn('rev-parse --show-toplevel', commands[0]['command'])

    def test_python_command_with_a_space_is_quoted(self):
        entry = skillsync_cli.build_closure_hook_entry({"python_command": "C:/Program Files/py.exe"})
        self.assertIn("'C:/Program Files/py.exe' \"$f\"", entry["hooks"][0]["command"])

    def test_missing_library_script_removes_the_entry_with_a_warning(self):
        self.link()
        (self.library / 'hooks/check-closure.py').unlink()
        result = self.link()
        self.assertIn('[warn]', result.stdout)
        self.assertEqual(self.closure_commands(self.local()), [])

    def test_disable_removes_entry_then_script(self):
        self.link()
        self.manifest['hooks'] = {'stop_closure': False}
        self.save()
        self.link()
        self.assertEqual(self.closure_commands(self.local()), [])
        self.assertFalse((self.root / self.HOOK_PATH).exists())

    def test_disable_keeps_the_script_when_the_entry_cannot_be_removed(self):
        self.link()
        self.manifest['hooks'] = {'stop_closure': False}
        self.save()
        path = self.root / self.LOCAL
        os.chmod(path, 0o444)
        try:
            if os.access(path, os.W_OK):
                self.skipTest('uprawnienia nie są egzekwowane (root)')
            result = self.link()
        finally:
            os.chmod(path, 0o644)
        self.assertIn('[warn]', result.stdout)
        self.assertEqual(len(self.closure_commands(self.local())), 1)
        self.assertTrue((self.root / self.HOOK_PATH).exists())

    def test_read_only_local_settings_warn_and_link_continues(self):
        path = self.write(self.LOCAL, '{"permissions": {}}\n')
        os.chmod(path, 0o444)
        try:
            if os.access(path, os.W_OK):
                self.skipTest('uprawnienia nie są egzekwowane (root)')
            result = self.link()
        finally:
            os.chmod(path, 0o644)
        self.assertIn('[warn]', result.stdout)
        self.assertEqual(self.closure_commands(self.local()), [])

    def test_tracked_local_settings_are_not_written(self):
        self.write(self.LOCAL, '{"permissions": {}}\n')
        self.init_git()
        self.git('add', '-f', self.LOCAL)
        # `init_git` already tracks the file when no global ignore hides it; --allow-empty keeps the commit independent of that
        self.git('commit', '-qm', 'track local settings', '--allow-empty')
        before = (self.root / self.LOCAL).read_bytes()
        result = self.link()
        self.assertIn('[warn]', result.stdout)
        self.assertEqual((self.root / self.LOCAL).read_bytes(), before)

    def test_stop_group_without_hooks_key_is_left_alone(self):
        existing = {'hooks': {'Stop': [{'matcher': 'x'}]}}
        self.write(self.LOCAL, json.dumps(existing))
        self.link()
        self.assertIn({'matcher': 'x'}, self.local()['hooks']['Stop'])

    def test_generated_hook_works_in_the_linked_project(self):
        self.link()
        self.init_git()
        self.write(PLAN, plan_text())
        result = subprocess.run([sys.executable, str(self.root / self.HOOK_PATH)],
                                input=json.dumps({'cwd': str(self.root), 'stop_hook_active': False}),
                                capture_output=True, text=True, timeout=20, cwd=self.root)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
