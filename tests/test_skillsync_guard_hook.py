"""180-J1: the PreToolUse hook that refuses permanent deletion (`library/hooks/guard-commands.py`) and its generation.

Three layers: the command classifier on a table of commands (today's work included, with the expected verdict of each), the hook
protocol (stdin JSON -> exit code, stderr, the exception trail), and `link`/`doctor` (entry in the host settings, backup,
idempotence, removal, projects without the agent flow untouched). Every project here is a temporary one with isolated state.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import needs_sh, transitional_config  # noqa: E402
from test_skillsync_workflow import CLI, WorkflowFixture  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / 'library' / 'hooks' / 'guard-commands.py'
spec = importlib.util.spec_from_file_location('guard_commands', HOOK)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

WORK = '/home/user/Repos/proj'
SCRATCH = '/tmp/session/scratchpad'
ENV = {'HOME': '/home/user', 'SCRATCH': SCRATCH, 'TMPDIR': '/tmp'}

# (command, verdict): True = refused, False = allowed, 'EXC' = refused but carries an approved one-time exception.
TABLE = [
    # today's work (the table of the spec)
    ('python3 -m unittest tests.test_ci_verdict tests.test_alert_sh tests.test_linux_run', False),
    ('cd tools && python3 -m unittest tests.a tests.b tests.c', False),
    ('python3 -m unittest discover -s tools/tests -p "test_*.py"', False),  # a wide test run is part (a), not this hook
    ('flock /run/user/1000/heavy-run.lock systemd-run --user --scope -p MemoryMax=4G python3 -m unittest discover -s tests', False),
    ("ssh winhost 'python -m unittest discover'", False),
    ("ssh winhost 'rm -rf C:/x'", False),
    ('command rm -f /tmp/mm.bak', False),
    ('rm -f $SCRATCH/x.py', False),
    ('command rm -rf ~/work/wt/FULL-copy-1', True),
    ('command rm -rf /home/user/work/wt/E2E-copy-2', True),
    ('git worktree remove ~/work/wt/F1', False),
    ('git worktree remove --force ~/work/wt/F1', True),
    ('git worktree remove -f ~/x/F', True),
    ('git clean -fd', True),
    ('git clean -n', False),
    ('git clean -fdx -e keep', True),
    ('git -C /tmp/worktree clean -fd', False),
    ('rm -rf node_modules', False),
    ('rm -rf src/__pycache__ dist build', False),
    ('rm -rf src', True),
    ('rm -rf *', True),
    ('rm -rf /tmp/a /srv/data/b', True),
    ('grep -rn "rm -rf" docs', False),
    ('echo "git clean -fd"', False),
    ("echo 'rm -rf /'", False),
    ('git commit -m "docs: git worktree remove --force and rm -rf x"', False),
    ("cat <<'EOF'\nrm -rf /\nEOF\ncat x", False),
    ('cat <<EOF > f.txt\nrm -rf /\nEOF', False),
    ('cat <<-EOF\n\trm -rf /\n\tEOF\nls', False),
    ('# rm -rf /\nls', False),
    ('python3 scripts/check_skips.py run.log', False),
    ('python3 -m py_compile skillsync_workflow/*.py', False),
    # forms that must be seen
    ("bash -c 'cd x && rm -rf y'", True),
    ('sh -c "rm -rf y"', True),
    ('eval "rm -rf y"', True),
    ('echo $(rm -rf zzz)', True),
    ('timeout 5 rm -rf src', True),
    ('timeout 5 rm -rf build', False),
    ('sudo rm -rf /var/lib/x', True),
    ('env X=1 rm -rf src', True),
    ('\\rm -rf src', True),
    ('/bin/rm -rf src', True),
    ('rm -R src', True),
    ('rm --recursive --force src', True),
    ('rm -rf -- src', True),
    ('ls && rm -rf src', True),
    ('ls | xargs rm -rf', True),
    ('find . -name "*.pyc" -delete', True),
    ('find /tmp/q -delete', False),
    ('find . -type d -name x -exec rm -rf {} +', True),
    ('python3 -c \'import shutil; shutil.rmtree("x")\'', True),
    ('Remove-Item -Recurse -Force src', True),
    ('rd /s /q src', True),
    ('rm -rf src 2>&1 > /dev/null', True),
    # temporary paths and variables
    ('rm -rf /tmp/x', False),
    ('rm -rf $TMPDIR/x', False),
    ('rm -rf ' + SCRATCH + '/run', False),
    ('rm -rf $SCRATCH/run', False),
    ('d=$(mktemp -d); touch $d/a; rm -rf "$d"', False),
    ('rm -rf $(mktemp -d)', False),
    ('rm -rf $UNKNOWN_VARIABLE/x', True),
    ('rm -rf /tmp/../srv/data', True),
    # no recursion, other tools
    ('rm file.txt', False),
    ('rm -f a.txt b.txt', False),
    ('trash -- src', False),
    ('make test && echo done', False),
    # one-time exception approved by the user
    ("SKILLSYNC_GUARD_EXCEPT='zgoda w oknie wyboru' rm -rf src", 'EXC'),
    ("SKILLSYNC_GUARD_EXCEPT='' rm -rf src", True),
]


def verdict(command, allow=()):
    findings = guard.Guard(WORK, SCRATCH, list(allow), ENV).walk(command)
    if findings and all(item[4] for item in findings):
        return 'EXC'
    return bool(findings)


class Classifier(unittest.TestCase):
    def test_table(self):
        for command, expected in TABLE:
            with self.subTest(command=command):
                self.assertEqual(verdict(command), expected)

    def test_project_allow_entries(self):
        self.assertEqual(verdict('rm -rf coverage'), True)
        self.assertEqual(verdict('rm -rf coverage', allow=['coverage']), False)
        self.assertEqual(verdict('rm -rf out/cache', allow=['out/cache']), False)
        self.assertEqual(verdict('rm -rf out/other', allow=['out/cache']), True)
        self.assertEqual(verdict('rm -rf /data/scratch/x', allow=['/data/scratch']), False)

    def test_allowed_names_only_count_inside_the_project(self):
        self.assertEqual(verdict('rm -rf /home/user/other/build'), True)
        self.assertEqual(verdict('rm -rf /home/user/Repos/proj/app/build'), False)

    def test_wrapped_remote_and_substituted_text_are_not_commands(self):
        self.assertEqual(verdict('echo "$(echo rm -rf x)"'), False)
        self.assertEqual(verdict('printf "%s" "rm -rf x" | cat'), False)

    def test_findings_name_the_kind(self):
        found = guard.Guard(WORK, SCRATCH, [], ENV).walk('git worktree remove --force /x/F; rm -rf src')
        self.assertEqual([item[0] for item in found], ['worktree-force', 'rm'])


class Protocol(unittest.TestCase):
    def run_hook(self, payload, *args, raw=None, cwd=None):
        text = raw if raw is not None else json.dumps(payload)
        env = {**os.environ, 'HOME': str(cwd or '/'), 'SKILLSYNC_GUARD_TMP_ROOTS': str(self.tmp_root)}
        return subprocess.run([sys.executable, str(HOOK), *args], input=text, capture_output=True, text=True, timeout=30, cwd=cwd, env=env)

    def project(self):
        # the project is under the system temp dir, so the temporary roots are narrowed to <base>/tmproot
        temp = tempfile.TemporaryDirectory(prefix='skillsync-guard-')
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.tmp_root = base / 'tmproot'
        self.tmp_root.mkdir()
        root = base / 'proj'
        (root / '.git').mkdir(parents=True)
        return root

    def bash(self, command, root, **extra):
        return {'tool_name': 'Bash', 'tool_input': {'command': command}, 'cwd': str(root), 'session_id': 's1', **extra}

    def test_refusal_is_exit_2_with_a_ready_replacement(self):
        root = self.project()
        done = self.run_hook(self.bash('rm -rf src docs', root), cwd=root)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn('trash -- src docs', done.stderr)
        self.assertIn('SKILLSYNC_GUARD_EXCEPT', done.stderr)
        self.assertIn('AskUserQuestion', done.stderr)
        self.assertEqual(done.stdout, '')
        self.assertNotIn('podumyslem', done.stderr)

    def test_subagent_is_told_to_report_to_the_main_session(self):
        root = self.project()
        done = self.run_hook(self.bash('rm -rf src', root, agent_id='a1', agent_type='implementer'), cwd=root)
        self.assertEqual(done.returncode, 2)
        self.assertIn('podumyslem', done.stderr)

    def test_worktree_and_clean_messages(self):
        root = self.project()
        done = self.run_hook(self.bash('git worktree remove --force ../F1', root), cwd=root)
        self.assertEqual(done.returncode, 2)
        self.assertIn('git worktree remove', done.stderr)
        self.assertIn('bez --force', done.stderr)
        done = self.run_hook(self.bash('git clean -fd', root), cwd=root)
        self.assertIn('git clean -n', done.stderr)

    def test_allowed_command_is_silent_exit_0(self):
        root = self.project()
        done = self.run_hook(self.bash(f'rm -rf {self.tmp_root}/x && ls', root), cwd=root)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, '', ''))

    def test_exception_is_allowed_and_leaves_a_trail(self):
        root = self.project()
        done = self.run_hook(self.bash("SKILLSYNC_GUARD_EXCEPT='zgoda uzytkownika' rm -rf src", root, agent_id='a9'), cwd=root)
        self.assertEqual(done.returncode, 0, done.stderr)
        trail = (root / '.skillsync' / 'hook-exceptions.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(trail), 1)
        record = json.loads(trail[0])
        self.assertEqual(record['reason'], 'zgoda uzytkownika')
        self.assertIn('rm -rf src', record['command'])
        self.assertEqual((record['agent_id'], record['session_id'], record['kind']), ('a9', 's1', ['rm']))
        self.assertRegex(record['time'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$')
        done = self.run_hook(self.bash("SKILLSYNC_GUARD_EXCEPT='drugi' rm -rf src", root), cwd=root)
        self.assertEqual(len((root / '.skillsync' / 'hook-exceptions.jsonl').read_text(encoding='utf-8').splitlines()), 2)

    def test_exception_only_covers_its_own_command(self):
        root = self.project()
        done = self.run_hook(self.bash("SKILLSYNC_GUARD_EXCEPT='x' rm -rf a; rm -rf b", root), cwd=root)
        self.assertEqual(done.returncode, 2)

    def test_fail_open_on_garbage_and_other_tools(self):
        root = self.project()
        for raw in ('', 'not json', '[]', '{"tool_name": "Bash"}', '{"tool_name": "Bash", "tool_input": {"command": 5}}',
                    json.dumps({'tool_name': 'Edit', 'tool_input': {'command': 'rm -rf x'}})):
            with self.subTest(raw=raw):
                done = self.run_hook(None, raw=raw, cwd=root)
                self.assertEqual(done.returncode, 0, done.stderr)

    def test_internal_failure_is_fail_open_with_a_note(self):
        root = self.project()
        payload = self.bash('rm -rf src', root)
        payload['cwd'] = 5  # a type the guard cannot use: the hook must not block because of it
        done = self.run_hook(payload, cwd=root)
        self.assertEqual(done.returncode, 0)
        self.assertIn('blad hooka', done.stderr)

    def test_mode_off_does_nothing(self):
        root = self.project()
        self.assertEqual(self.run_hook(self.bash('rm -rf src', root), '--mode', 'off', cwd=root).returncode, 0)

    def test_allow_arguments(self):
        root = self.project()
        self.assertEqual(self.run_hook(self.bash('rm -rf coverage', root), cwd=root).returncode, 2)
        self.assertEqual(self.run_hook(self.bash('rm -rf coverage', root), '--allow', 'coverage', cwd=root).returncode, 0)

    def test_scratchpad_from_the_payload_is_allowed(self):
        root = self.project()
        scratch = root.parent / 'scratch'
        scratch.mkdir()
        self.assertEqual(self.run_hook(self.bash(f'rm -rf {scratch}/x', root), cwd=root).returncode, 2)
        self.assertEqual(self.run_hook(self.bash(f'rm -rf {scratch}/x', root, scratchpad_dir=str(scratch)), cwd=root).returncode, 0)

    def test_runs_fast(self):
        import time
        root = self.project()
        start = time.monotonic()
        self.run_hook(self.bash('ls && rm -rf src', root), cwd=root)
        self.assertLess(time.monotonic() - start, 5)


# ---------------------------------------------------------------------------------------------------------------
# link / doctor
# ---------------------------------------------------------------------------------------------------------------
class GuardBase(WorkflowFixture):
    HOOK_PATH = '.claude/hooks/guard-commands.py'
    LOCAL = '.claude/settings.local.json'

    def setUp(self):
        super().setUp()
        (self.library / 'hooks').mkdir()
        (self.library / 'hooks/guard-commands.py').write_bytes(HOOK.read_bytes())
        for name in ('feature', 'dispatch', 'plain'):
            skill = self.library / 'skills' / name
            skill.mkdir(parents=True)
            (skill / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: Fixture\n---\nSkill', encoding='utf-8')
        (self.library / 'instructions').mkdir()
        (self.library / 'instructions/_base.md').write_text('# Base\n', encoding='utf-8')
        self.home = self.base / 'home'
        self.home.mkdir()
        self.config = self.base / 'config.yaml'
        self.config.write_text(yaml.safe_dump(transitional_config(self.library, legacy_key=False, tags={})), encoding='utf-8')
        self.manifest['skills'] = ['feature']
        self.save()

    def link(self, *args, code=0):
        result = subprocess.run([sys.executable, str(CLI), 'link', str(self.root), *args], capture_output=True, text=True, timeout=30,
                                cwd=self.root, env={**os.environ, 'SKILLSYNC_CONFIG': str(self.config), 'HOME': str(self.home)})
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def local(self):
        return json.loads((self.root / self.LOCAL).read_text(encoding='utf-8'))

    def guard_entries(self, data):
        return [hook for group in (data.get('hooks') or {}).get('PreToolUse', []) for hook in group.get('hooks', []) if 'guard-commands.py' in json.dumps(hook)]

    def tree(self):
        result = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            for name in dirnames:
                result[os.path.relpath(os.path.join(dirpath, name), self.root)] = None
            for name in filenames:
                path = Path(dirpath, name)
                result[os.path.relpath(path, self.root)] = path.read_bytes() if not path.is_symlink() else 'link'
        return result

    def set_hooks(self, **values):
        self.manifest['hooks'] = values
        self.save()



class GuardLink(GuardBase):
    def test_feature_project_gets_script_and_entry(self):
        self.link()
        self.assertEqual((self.root / self.HOOK_PATH).read_bytes(), HOOK.read_bytes())
        entries = self.guard_entries(self.local())
        self.assertEqual(len(entries), 1)
        self.assertIn('--mode deny', entries[0]['command'])
        group = [g for g in self.local()['hooks']['PreToolUse'] if self.guard_entries({'hooks': {'PreToolUse': [g]}})][0]
        self.assertEqual(group['matcher'], 'Bash')
        self.assertEqual(entries[0]['timeout'], 10)
        self.assertIn('hooks/guard-commands.py', (self.root / '.claude/.gitignore').read_text(encoding='utf-8'))

    def test_dispatch_only_project_gets_it(self):
        self.manifest['skills'] = ['dispatch']
        self.save()
        self.link()
        self.assertEqual(len(self.guard_entries(self.local())), 1)

    def test_project_without_the_agent_flow_is_untouched(self):
        self.manifest['skills'] = ['plain']
        self.save()
        self.link()
        self.assertFalse((self.root / self.HOOK_PATH).exists())
        if (self.root / self.LOCAL).exists():
            self.assertEqual(self.guard_entries(self.local()), [])
        gitignore = self.root / '.claude/.gitignore'
        if gitignore.exists():
            self.assertNotIn('guard-commands', gitignore.read_text(encoding='utf-8'))

    def test_explicit_deny_installs_in_a_plain_project(self):
        self.manifest['skills'] = ['plain']
        self.set_hooks(guard_delete='deny')
        self.link()
        self.assertEqual(len(self.guard_entries(self.local())), 1)

    def test_foreign_entries_and_permissions_survive_and_backup_is_kept(self):
        existing = {'permissions': {'allow': ['Bash(git fetch *)']},
                    'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': 'foreign-pre'}]},
                                             {'matcher': 'Edit', 'hooks': [{'type': 'command', 'command': 'other'}]}],
                              'Stop': [{'matcher': '', 'hooks': [{'type': 'command', 'command': 'echo foreign'}]}]}}
        self.write(self.LOCAL, json.dumps(existing))
        self.link()
        data = self.local()
        self.assertEqual(data['permissions'], existing['permissions'])
        commands = json.dumps(data['hooks']['PreToolUse'])
        self.assertIn('foreign-pre', commands)
        self.assertIn('other', commands)
        self.assertEqual(len(self.guard_entries(data)), 1)
        self.assertIn('echo foreign', json.dumps(data['hooks']['Stop']))
        backup = self.root / (self.LOCAL + '.skillsync-backup')
        self.assertTrue(backup.is_file())

    def test_second_link_changes_nothing(self):
        self.link()
        before = self.tree()
        self.link()
        self.assertEqual(self.tree(), before)
        self.assertEqual(len(self.guard_entries(self.local())), 1)

    def test_off_removes_entry_and_script_but_keeps_foreign(self):
        self.write(self.LOCAL, json.dumps({'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': 'foreign-pre'}]}]}}))
        self.link()
        self.assertTrue((self.root / self.HOOK_PATH).exists())
        self.set_hooks(guard_delete='off')
        self.link()
        self.assertFalse((self.root / self.HOOK_PATH).exists())
        data = self.local()
        self.assertEqual(self.guard_entries(data), [])
        self.assertIn('foreign-pre', json.dumps(data['hooks']['PreToolUse']))

    def test_off_in_a_never_linked_project_creates_nothing(self):
        self.set_hooks(guard_delete='off')
        self.link()
        self.assertFalse((self.root / self.HOOK_PATH).exists())
        if (self.root / self.LOCAL).exists():
            self.assertEqual(self.guard_entries(self.local()), [])

    def test_delete_allow_goes_into_the_entry_and_changes_update_it(self):
        self.set_hooks(delete_allow=['coverage', 'out/cache'])
        self.link()
        command = self.guard_entries(self.local())[0]['command']
        self.assertIn('--allow coverage', command)
        self.assertIn('--allow out/cache', command)
        self.set_hooks(delete_allow=['coverage'])
        self.link()
        entries = self.guard_entries(self.local())
        self.assertEqual(len(entries), 1)
        self.assertNotIn('out/cache', entries[0]['command'])

    def test_invalid_settings_warn_and_do_not_switch_the_hook_on(self):
        self.manifest['skills'] = ['plain']
        self.set_hooks(guard_delete='sometimes', delete_allow='x')
        result = self.link()
        self.assertIn('[warn] hooks.guard_delete', result.stdout)
        self.assertIn('[warn] hooks.delete_allow', result.stdout)
        self.assertFalse((self.root / self.HOOK_PATH).exists())

    def test_dry_run_shows_what_it_would_add_and_writes_nothing(self):
        before = self.tree()
        result = self.link('--dry-run')
        self.assertIn('Would write', result.stdout)
        self.assertIn('guard-commands.py', result.stdout)
        self.assertIn('Would update', result.stdout)
        self.assertEqual(self.tree(), before)

    def test_invalid_json_in_settings_is_left_alone_with_a_warning(self):
        self.write(self.LOCAL, '{broken')
        result = self.link()
        self.assertIn('[warn]', result.stdout)
        self.assertEqual((self.root / self.LOCAL).read_text(encoding='utf-8'), '{broken')

    def test_tracked_settings_file_is_not_written(self):
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        self.write(self.LOCAL, '{}')
        subprocess.run(['git', 'add', '-f', self.LOCAL], cwd=self.root, check=True)
        result = self.link()
        self.assertIn('śledzony', result.stdout)
        self.assertEqual(self.guard_entries(self.local()), [])

    def test_python_command_with_spaces_is_quoted(self):
        from skillsync_workflow import guardhook
        command = guardhook.hook_command('C:/Program Files/py.exe', 'deny', ['a b'])
        self.assertIn("'C:/Program Files/py.exe'", command)
        self.assertIn("--allow 'a b'", command)

    @needs_sh
    def test_generated_command_blocks_in_the_linked_project(self):
        self.link()
        command = self.guard_entries(self.local())[0]['command']
        payload = json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 'rm -rf src'}, 'cwd': str(self.root)})
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        env = {**os.environ, 'CLAUDE_PROJECT_DIR': str(self.root), 'SKILLSYNC_GUARD_TMP_ROOTS': str(self.base / 'tmproot')}
        done = subprocess.run(['sh', '-c', command], input=payload, capture_output=True, text=True, cwd=self.root, env=env, timeout=30)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn('trash --', done.stderr)
        done = subprocess.run(['sh', '-c', command], input=json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 'ls'}, 'cwd': str(self.root)}),
                              capture_output=True, text=True, cwd=self.root, env=env, timeout=30)
        self.assertEqual(done.returncode, 0)

    @needs_sh
    def test_missing_script_means_exit_0(self):
        self.link()
        command = self.guard_entries(self.local())[0]['command']
        (self.root / self.HOOK_PATH).unlink()
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        env = {**os.environ, 'CLAUDE_PROJECT_DIR': str(self.root)}
        done = subprocess.run(['sh', '-c', command], input='{}', capture_output=True, text=True, cwd=self.root, env=env, timeout=30)
        self.assertEqual(done.returncode, 0)


class GuardDoctor(GuardBase):
    def doctor(self):
        # the fixture project has unrelated doctor errors (no adapters); only the guard findings matter here
        result = self.cli('doctor')
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        return json.loads(result.stdout)

    def guard_findings(self):
        report = self.doctor()
        return [f for f in report['findings'] if f['path'] == self.LOCAL], report

    def test_not_installed_warns_installed_reports_info(self):
        found, report = self.guard_findings()
        self.assertEqual([f['level'] for f in found], ['warning'])
        self.assertIn('not installed', found[0]['reason'])
        self.link()
        found, report = self.guard_findings()
        self.assertEqual([f['level'] for f in found], ['info'])
        self.assertIn('mode deny', found[0]['reason'])

    def test_drift_and_switched_off_but_installed(self):
        self.link()
        self.set_hooks(delete_allow=['coverage'])
        found, _ = self.guard_findings()
        self.assertEqual([f['level'] for f in found], ['warning'])
        self.assertIn('differs', found[0]['reason'])
        self.set_hooks(guard_delete='off')
        found, _ = self.guard_findings()
        self.assertIn('switched off', found[0]['reason'])

    def test_project_without_the_flow_has_no_guard_finding(self):
        self.manifest['skills'] = ['plain']
        self.save()
        found, _ = self.guard_findings()
        self.assertEqual(found, [])

    def test_invalid_manifest_values_are_reported_never_an_error(self):
        self.set_hooks(guard_delete='sometimes')
        report = self.doctor()
        self.assertTrue(any('hooks.guard_delete' in f['reason'] for f in report['findings']))


if __name__ == '__main__':
    unittest.main()
