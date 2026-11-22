"""CLAUDE-3 F6 (tests written first, RED until the code exists): installation of the new Claude Code hooks by `link`, the manifest keys and `doctor`
(spec K7, K9, K10, A7; plan "Kontrakty wykonawcze").

Black box through `skillsync link` and `skillsync doctor --json` on a fresh temporary project, in the isolated environment of
`IsolatedTestCase` (temporary HOME, XDG and SKILLSYNC_CONFIG; the repository library is the only core layer). The same helpers as the golden
trees of `tests/test_skillsync_hooksync.py` (`tests/fixtures/hooksync/cases.py`) serve the isolation.

Pinned contract:
 * keys `hooks.session_context: true|false`, `hooks.guard_generated: warn|deny|off`, `hooks.subagent_report: warn|off`,
   `hooks.subagent_commit: warn|deny|off`; defaults of an agent-flow project (`feature`/`dispatch`): true, warn, warn, deny; plain project:
   false, off, off, off; an unknown value is `off` plus a `[warn]` line of `link` (without the value) and a finding of `doctor`;
 * events in the order Stop, PreToolUse, SessionStart, SubagentStop; two entries of ours under PreToolUse (guard of commands and guard of generated files);
 * scripts copied byte for byte into `.claude/hooks`, listed in `.claude/.gitignore`; `.skillsync/` in the root `.gitignore`;
 * a switch-off removes the entry and then the script; `claude: false` removes ALL our entries and scripts (K10, a NEW behaviour: the old `link`
   left them; the golden case `claude_false_after_install` of F1 is replaced by `ClaudeFalse` below on purpose);
 * a second `link` changes nothing; backup of the settings file: policy `earliest` for the new entries;
 * `doctor` compares the fixed part of the command (script, modes, matcher, timeout), not the argv of the CLI.
Temporary paths come from `tmp_paths.child`/`descend`."""
import datetime
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import sys as _sys_tmp_paths
import unittest
from pathlib import Path
from pathlib import Path as _Path_tmp_paths

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE / 'fixtures' / 'hooksync'))
from layers_helpers import IsolatedTestCase, git, needs_os_symlinks  # noqa: E402
_sys_tmp_paths.path.insert(0, str(_Path_tmp_paths(__file__).resolve().parent))  # noqa: E402
from tmp_paths import child, descend  # noqa: E402
import cases  # noqa: E402

CLI = REPO / 'skillsync'
LOCAL = cases.LOCAL
BACKUP = cases.BACKUP
NEW_SCRIPTS = ('session-context.py', 'guard-generated.py', 'check-subagent-report.py')
OUR_SCRIPTS = ('check-closure.py', 'guard-commands.py', 'guard-tests.py') + NEW_SCRIPTS
GENERATED_MATCHER = 'Write|Edit|NotebookEdit'
SESSION_MATCHER = 'startup|resume|clear|compact'
EVENT_ORDER = ['Stop', 'PreToolUse', 'SessionStart', 'SubagentStop']
FEATURE_DEFAULTS = {'session_context': True, 'guard_generated': 'warn', 'subagent_report': 'warn', 'subagent_commit': 'deny'}
ALL_OFF = {'session_context': False, 'guard_generated': 'off', 'subagent_report': 'off', 'subagent_commit': 'off'}
ALL_ON = {'session_context': True, 'guard_generated': 'warn', 'subagent_report': 'warn', 'subagent_commit': 'deny'}
COMMAND_FORM = re.compile(r'^f="\$\(git -C "\$\{CLAUDE_PROJECT_DIR:-\.\}" rev-parse --show-toplevel 2>/dev/null\)/(\.claude/hooks/[A-Za-z0-9_.-]+)"; '
                          r'if \[ -f "\$f" \]; then (.*); fi$', re.DOTALL)


def parse_command(command):
    """`(script path relative to the root, argv after "$f")` of a hook command in the form `f=...; if [ -f "$f" ]; then <python> "$f" <args>; fi`."""
    match = COMMAND_FORM.match(command)
    assert match, f'the hook command has not the established form: {command!r}'
    words = shlex.split(match.group(2))
    assert words[1] == '$f', words
    return match.group(1), words[2:]


def our_hooks(data, script):
    """`[(event, group, hook)]` of every hook whose command runs `.claude/hooks/<script>`."""
    found = []
    for event, groups in (data.get('hooks') or {}).items():
        for group in groups:
            for hook in group.get('hooks', []):
                if f'.claude/hooks/{script}' in str(hook.get('command', '')):
                    found.append((event, group, hook))
    return found


def fixed_part(command):
    """The command without the argv of the CLI (`--cli <json>` of session-context)."""
    script, args = parse_command(command)
    if '--cli' in args:
        at = args.index('--cli')
        args = args[:at] + args[at + 2:]
    return script, args


def golden(name):
    return json.loads(child(cases.GOLDEN, f'{name}.json').read_bytes().decode('utf-8'))


class HookLinkBase(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        cases.write_machine_config(self.env['SKILLSYNC_CONFIG'])
        self.count = 0

    # --- building and running -------------------------------------------------------------------------------
    def project(self, skills=('feature',), hooks=None, claude=None, local=None, backup=None, name=None):
        self.count += 1
        project = child(self.work, name or f'p{self.count}')
        project.mkdir()
        self.manifest = {'version': 1, 'project': {'name': 'Org/HookLink', 'type': 'local'}, 'skills': list(skills)}
        if hooks is not None:
            self.manifest['hooks'] = dict(hooks)
        if claude is not None:
            self.manifest['claude'] = claude
        self.write_manifest(project)
        for relative, data in ((LOCAL, local), (BACKUP, backup)):
            if data is not None:
                target = descend(project, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        return project

    def write_manifest(self, project):
        descend(project, '.github/.skillsync.yaml').parent.mkdir(parents=True, exist_ok=True)
        descend(project, '.github/.skillsync.yaml').write_text(yaml.safe_dump(self.manifest, sort_keys=False), encoding='utf-8')

    def set_hooks(self, project, **hooks):
        self.manifest.setdefault('hooks', {}).update(hooks)
        self.write_manifest(project)

    def link(self, project, *args):
        return cases.run_link(project, *args)

    def lines(self, result):
        return result.stdout.splitlines()

    def settings(self, project):
        return json.loads(descend(project, LOCAL).read_bytes().decode('utf-8'))

    def write_settings(self, project, data):
        descend(project, LOCAL).write_bytes(cases.json_bytes(data))

    def hook_names(self, project):
        folder = descend(project, cases.HOOKS_DIR)
        return sorted(entry.name for entry in folder.iterdir()) if folder.is_dir() else []

    def state(self, project):
        """Everything `link` writes that these tests look at, as bytes."""
        files = {name: descend(project, f'{cases.HOOKS_DIR}/{name}').read_bytes() for name in self.hook_names(project)}
        return {'settings': descend(project, LOCAL).read_bytes() if descend(project, LOCAL).is_file() else None,
                'backup': descend(project, BACKUP).read_bytes() if descend(project, BACKUP).is_file() else None, 'files': files}

    def source(self, script):
        return descend(REPO, f'library/hooks/{script}').read_bytes()

    def events(self, project):
        return list(self.settings(project).get('hooks', {}))

    def guard_command(self, project):
        (event, _, hook), = our_hooks(self.settings(project), 'guard-commands.py')
        self.assertEqual(event, 'PreToolUse')
        return hook['command']

    def assert_nothing_new(self, project):
        """No entry and no file of the new hook families (the negative assertion of the plain project; proven to fail in `HookLinkMeta`)."""
        data = self.settings(project) if descend(project, LOCAL).is_file() else {}
        for script in NEW_SCRIPTS:
            self.assertEqual(our_hooks(data, script), [], script)
            self.assertNotIn(script, self.hook_names(project))
        self.assertNotIn('SessionStart', data.get('hooks', {}))
        self.assertNotIn('SubagentStop', data.get('hooks', {}))
        gitignore = descend(project, '.claude/.gitignore')
        text = gitignore.read_text(encoding='utf-8') if gitignore.is_file() else ''
        for script in NEW_SCRIPTS:
            self.assertNotIn(f'hooks/{script}', text)

    # --- doctor -------------------------------------------------------------------------------------------------
    def doctor(self, project):
        done = subprocess.run([sys.executable, str(CLI), 'doctor', str(project), '--json'], capture_output=True, text=True, timeout=120,
                              cwd=str(project))
        self.assertNotIn('Traceback', done.stderr, done.stderr)
        return json.loads(done.stdout)['findings']

    @staticmethod
    def about(findings, *tokens):
        return [f for f in findings if any(token in f['path'] or token in f['reason'] for token in tokens)]


# --------------------------------------------------------------------------------------------------------------
# installation: defaults, shape of the entries, files
# --------------------------------------------------------------------------------------------------------------
class DefaultsFeature(HookLinkBase):
    def test_events_in_the_agreed_order_and_attribution_after_the_hooks(self):
        project = self.project()
        self.link(project)
        self.assertEqual(self.events(project), EVENT_ORDER)
        self.assertEqual(list(self.settings(project)), ['hooks', 'attribution'])
        self.assertEqual(self.settings(project)['attribution'], {'commit': '', 'pr': ''})

    def test_pre_tool_use_holds_two_groups_of_ours_the_guard_of_commands_first(self):
        project = self.project()
        self.link(project)
        groups = self.settings(project)['hooks']['PreToolUse']
        self.assertEqual(len(groups), 2, groups)
        commands, generated = groups
        self.assertEqual(commands['matcher'], 'Bash')
        script, args = parse_command(commands['hooks'][0]['command'])
        self.assertEqual(script, '.claude/hooks/guard-commands.py')
        at = args.index('--subagent-commit')
        self.assertEqual(args[at + 1], 'deny')
        self.assertEqual(generated['matcher'], GENERATED_MATCHER)
        self.assertEqual(len(generated['hooks']), 1)
        script, args = parse_command(generated['hooks'][0]['command'])
        self.assertEqual(script, '.claude/hooks/guard-generated.py')
        self.assertEqual(args, ['--mode', 'warn'])
        self.assertLessEqual(generated['hooks'][0]['timeout'], 10)
        self.assertEqual(generated['hooks'][0]['type'], 'command')

    def test_guard_of_commands_keeps_its_old_arguments_and_gets_only_the_new_one(self):
        project = self.project()
        self.link(project)
        old_command = json.loads(golden('feature_only')['settings_local'])['hooks']['PreToolUse'][0]['hooks'][0]['command']
        _, old_args = parse_command(old_command)
        _, args = parse_command(self.guard_command(project))
        at = args.index('--subagent-commit')
        self.assertEqual(args[:at] + args[at + 2:], old_args)

    def test_session_start_entry(self):
        project = self.project()
        self.link(project)
        (event, group, hook), = our_hooks(self.settings(project), 'session-context.py')
        self.assertEqual(event, 'SessionStart')
        self.assertEqual(group['matcher'], SESSION_MATCHER)
        self.assertEqual(hook['type'], 'command')
        self.assertEqual(hook['timeout'], 3)
        script, args = parse_command(hook['command'])
        self.assertEqual(script, '.claude/hooks/session-context.py')
        self.assertEqual(args[0], '--cli')
        argv = json.loads(args[1])    # the shape only: the value depends on the checkout
        self.assertIsInstance(argv, list)
        self.assertTrue(argv and all(isinstance(item, str) and item for item in argv), argv)
        self.assertEqual(len(args), 2)
        self.assertTrue(hook['command'].count('python3 "$f"') == 1, hook['command'])

    def test_subagent_stop_entry(self):
        project = self.project()
        self.link(project)
        (event, group, hook), = our_hooks(self.settings(project), 'check-subagent-report.py')
        self.assertEqual(event, 'SubagentStop')
        script, args = parse_command(hook['command'])
        self.assertEqual((script, args), ('.claude/hooks/check-subagent-report.py', ['--mode', 'warn']))
        self.assertEqual(hook['type'], 'command')
        self.assertIsInstance(hook['timeout'], int)

    def test_scripts_are_copied_byte_for_byte(self):
        project = self.project()
        self.link(project)
        for script in OUR_SCRIPTS:
            with self.subTest(script=script):
                self.assertEqual(descend(project, f'{cases.HOOKS_DIR}/{script}').read_bytes(), self.source(script))
        self.assertEqual(self.hook_names(project), sorted(['attribution-patterns.json', 'test-tools.json', *OUR_SCRIPTS]))

    def test_gitignore_lists_the_new_scripts_and_skillsync_state_dir(self):
        project = self.project()
        self.link(project)
        lines = descend(project, '.claude/.gitignore').read_text(encoding='utf-8').splitlines()
        for script in NEW_SCRIPTS:
            self.assertIn(f'hooks/{script}', lines)
        self.assertIn('.skillsync/', child(project, '.gitignore').read_text(encoding='utf-8').splitlines())

    def test_dispatch_skill_alone_gets_the_flow_defaults(self):
        project = self.project(skills=('dispatch',))
        self.link(project)
        self.assertEqual(self.events(project), EVENT_ORDER[1:], 'dispatch has no closure: PreToolUse, SessionStart, SubagentStop')
        _, args = parse_command(self.guard_command(project))
        self.assertEqual(args[args.index('--subagent-commit') + 1], 'deny')

    def test_the_shape_is_the_same_on_a_project_with_foreign_hooks(self):
        project = self.project(local=cases.json_bytes(cases.FOREIGN_SETTINGS))
        self.link(project)
        data = self.settings(project)
        self.assertIn(cases.FOREIGN_PRE, data['hooks']['PreToolUse'])
        self.assertEqual(data['hooks']['Notification'], cases.FOREIGN_SETTINGS['hooks']['Notification'])
        self.assertEqual(list(data['hooks'])[:3], ['Stop', 'PreToolUse', 'Notification'], 'existing events keep their place')
        self.assertEqual(list(data['hooks'])[3:], ['SessionStart', 'SubagentStop'])
        self.assertEqual(data['permissions'], cases.FOREIGN_SETTINGS['permissions'])


class DefaultsPlain(HookLinkBase):
    def test_plain_project_without_keys_is_byte_equal_to_the_golden_tree(self):
        project = self.project(skills=())
        self.link(project)
        self.assertEqual(descend(project, LOCAL).read_bytes().decode('utf-8'), golden('plain_guard_only')['settings_local'])
        self.assertEqual(self.hook_names(project), golden('plain_guard_only')['hook_files'])
        self.assert_nothing_new(project)
        self.assertNotIn('--subagent-commit', self.guard_command(project), 'with the key off the argument is left out, the bytes stay those of the old code')
        self.assertEqual(self.events(project), ['PreToolUse'])
        self.assertEqual(len(self.settings(project)['hooks']['PreToolUse']), 1)

    def test_explicit_off_everywhere_gives_the_bytes_of_the_old_code_in_a_feature_project(self):
        project = self.project(hooks=ALL_OFF)
        self.link(project)
        self.assertEqual(descend(project, LOCAL).read_bytes().decode('utf-8'), golden('feature_only')['settings_local'])
        self.assertEqual(self.hook_names(project), golden('feature_only')['hook_files'])
        self.assert_nothing_new(project)

    def test_plain_project_can_switch_every_family_on(self):
        project = self.project(skills=(), hooks=ALL_ON)
        self.link(project)
        self.assertEqual(self.events(project), ['PreToolUse', 'SessionStart', 'SubagentStop'])
        for script in NEW_SCRIPTS:
            self.assertEqual(descend(project, f'{cases.HOOKS_DIR}/{script}').read_bytes(), self.source(script))
        _, args = parse_command(self.guard_command(project))
        self.assertEqual(args[args.index('--subagent-commit') + 1], 'deny')

    def test_modes_are_passed_through(self):
        for key, value, script, flag in (('guard_generated', 'deny', 'guard-generated.py', '--mode'),
                                         ('subagent_commit', 'warn', 'guard-commands.py', '--subagent-commit')):
            with self.subTest(key=key, value=value):
                project = self.project(skills=(), hooks={**ALL_OFF, key: value})
                self.link(project)
                (_, _, hook), = our_hooks(self.settings(project), script)
                _, args = parse_command(hook['command'])
                self.assertEqual(args[args.index(flag) + 1], value)
        project = self.project(skills=(), hooks={**ALL_OFF, 'subagent_commit': 'warn'})
        self.link(project)
        self.assertEqual(our_hooks(self.settings(project), 'guard-generated.py'), [], 'the other families stay off')

    def test_off_guard_of_commands_in_a_feature_project_keeps_the_rest(self):
        project = self.project(hooks={**FEATURE_DEFAULTS, 'subagent_commit': 'off'})
        self.link(project)
        _, args = parse_command(self.guard_command(project))
        self.assertNotIn('--subagent-commit', args)
        _, old_args = parse_command(json.loads(golden('feature_only')['settings_local'])['hooks']['PreToolUse'][0]['hooks'][0]['command'])
        self.assertEqual(args, old_args)
        self.assertEqual(self.events(project), EVENT_ORDER)


class ProjectsWithoutClaude(HookLinkBase):
    def test_claude_false_from_the_start_installs_nothing_even_with_every_family_on(self):
        for claude in (False, {'enabled': False}):
            with self.subTest(claude=claude):
                project = self.project(hooks=ALL_ON, claude=claude)
                self.link(project)
                self.assertFalse(descend(project, LOCAL).exists())
                self.assertEqual(self.hook_names(project), [])


# --------------------------------------------------------------------------------------------------------------
# invalid values
# --------------------------------------------------------------------------------------------------------------
class InvalidValues(HookLinkBase):
    BAD = {'session_context': ['sometimes', 7, 'warn'], 'guard_generated': ['sometimes', 7, True], 'subagent_report': ['deny', 'sometimes', 7],
           'subagent_commit': ['sometimes', 7, True]}
    PRESENT = {'session_context': ('session-context.py', 'SessionStart'), 'guard_generated': ('guard-generated.py', 'PreToolUse'),
               'subagent_report': ('check-subagent-report.py', 'SubagentStop'), 'subagent_commit': ('guard-commands.py', 'PreToolUse')}

    def test_an_unknown_value_is_off_with_a_warning_that_does_not_echo_the_value(self):
        for key, values in self.BAD.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    project = self.project(hooks={**FEATURE_DEFAULTS, key: value})
                    result = self.link(project)
                    warnings = [line for line in self.lines(result) if line.startswith('[warn]') and f'hooks.{key}' in line]
                    self.assertEqual(len(warnings), 1, result.stdout)
                    self.assertNotIn(str(value), warnings[0].split(']', 1)[1], 'the value of the manifest is not echoed')  # the line starts with the tag `[warn]`
                    script, event = self.PRESENT[key]
                    if key == 'subagent_commit':
                        _, args = parse_command(self.guard_command(project))
                        self.assertNotIn('--subagent-commit', args)
                    else:
                        self.assertEqual(our_hooks(self.settings(project), script), [])
                        self.assertNotIn(script, self.hook_names(project))
                    self.assertEqual(self.events(project)[0], 'Stop', 'the other families are not affected')

    def test_a_bad_value_removes_an_installed_hook(self):
        project = self.project()
        self.link(project)
        self.assertEqual(len(our_hooks(self.settings(project), 'guard-generated.py')), 1)
        self.set_hooks(project, guard_generated='bogus')
        self.link(project)
        self.assertEqual(our_hooks(self.settings(project), 'guard-generated.py'), [])
        self.assertNotIn('guard-generated.py', self.hook_names(project))

    def test_valid_values_give_no_hooks_warning(self):
        project = self.project()
        result = self.link(project)
        self.assertEqual([line for line in self.lines(result) if line.startswith('[warn]') and 'hooks.' in line], [])


# --------------------------------------------------------------------------------------------------------------
# switching off and on, ordering of the removal
# --------------------------------------------------------------------------------------------------------------
class SwitchOffAndOn(HookLinkBase):
    def test_off_removes_the_entry_and_the_script_on_is_written_back(self):
        families = {'session_context': ('session-context.py', False), 'guard_generated': ('guard-generated.py', 'off'),
                    'subagent_report': ('check-subagent-report.py', 'off')}
        for key, (script, off) in families.items():
            with self.subTest(key=key):
                project = self.project()
                self.link(project)
                self.set_hooks(project, **{key: off})
                self.link(project)
                self.assertEqual(our_hooks(self.settings(project), script), [])
                self.assertNotIn(script, self.hook_names(project))
                others = [other for other in NEW_SCRIPTS if other != script]
                for other in others:
                    self.assertEqual(len(our_hooks(self.settings(project), other)), 1, f'{other} untouched')
                self.set_hooks(project, **{key: FEATURE_DEFAULTS[key]})
                self.link(project)
                self.assertEqual(len(our_hooks(self.settings(project), script)), 1)
                self.assertEqual(descend(project, f'{cases.HOOKS_DIR}/{script}').read_bytes(), self.source(script))

    def test_switching_off_does_not_touch_foreign_hooks_of_the_same_events(self):
        foreign = {'hooks': {'SessionStart': [{'matcher': 'startup', 'hooks': [{'type': 'command', 'command': 'echo obcy-start'}]}],
                             'SubagentStop': [{'matcher': '', 'hooks': [{'type': 'command', 'command': 'echo obcy-stop'}]}]}}
        project = self.project(local=cases.json_bytes(foreign))
        self.link(project)
        self.set_hooks(project, session_context=False, subagent_report='off')
        self.link(project)
        data = self.settings(project)
        self.assertEqual(data['hooks']['SessionStart'], foreign['hooks']['SessionStart'])
        self.assertEqual(data['hooks']['SubagentStop'], foreign['hooks']['SubagentStop'])

    def test_the_script_stays_while_the_entry_cannot_be_removed(self):
        project = self.project()
        self.link(project)
        git(project, 'init', '-q')
        git(project, 'add', '-f', LOCAL)
        before = self.state(project)
        self.set_hooks(project, guard_generated='off')
        result = self.link(project)
        self.assertTrue(any(line.startswith('[warn]') and 'śledzony przez git' in line for line in self.lines(result)), result.stdout)
        self.assertEqual(self.state(project)['settings'], before['settings'], 'a tracked file is not written')
        self.assertIn('guard-generated.py', self.hook_names(project), 'the script goes only after its entry: a dangling entry would call a missing file')

    def test_disjoint_predicates_removing_one_pre_tool_use_group_keeps_the_other(self):
        project = self.project(local=cases.json_bytes({'hooks': {'PreToolUse': [cases.FOREIGN_PRE]}}))
        self.link(project)
        self.set_hooks(project, guard_generated='off')
        self.link(project)
        groups = self.settings(project)['hooks']['PreToolUse']
        self.assertEqual(groups[0], cases.FOREIGN_PRE)
        self.assertEqual([group['matcher'] for group in groups], ['Write', 'Bash'])
        self.set_hooks(project, guard_generated='warn')
        self.link(project)
        self.assertEqual([group['matcher'] for group in self.settings(project)['hooks']['PreToolUse']], ['Write', 'Bash', GENERATED_MATCHER])


# --------------------------------------------------------------------------------------------------------------
# claude: false (K10: NEW behaviour)
# --------------------------------------------------------------------------------------------------------------
class ClaudeFalse(HookLinkBase):
    """K10: `claude: false` after an installation removes everything of ours. The OLD `link` left the entries and the scripts in place; this is a
    deliberate change of that behaviour (it replaces `test_claude_false_after_installation_today_leaves_our_entries` and the golden case
    `claude_false_after_install` of F1)."""

    def installed(self, local=None):
        project = self.project(local=local)
        self.link(project)
        return project

    def test_every_entry_of_ours_and_every_script_are_removed(self):
        for claude in (False, {'enabled': False}):
            with self.subTest(claude=claude):
                project = self.installed()
                self.assertEqual(self.events(project), EVENT_ORDER)
                self.manifest['claude'] = claude
                self.write_manifest(project)
                self.link(project)
                data = self.settings(project)
                for script in OUR_SCRIPTS:
                    self.assertEqual(our_hooks(data, script), [], script)
                    self.assertNotIn(script, self.hook_names(project), script)
                for event in EVENT_ORDER:
                    self.assertNotIn(event, data.get('hooks', {}), event)

    def test_foreign_entries_keys_and_files_stay(self):
        project = self.installed(local=cases.json_bytes(cases.FOREIGN_SETTINGS))
        foreign_script = descend(project, f'{cases.HOOKS_DIR}/moj-skrypt.sh')
        foreign_script.write_text('#!/bin/sh\n', encoding='utf-8')
        self.manifest['claude'] = False
        self.write_manifest(project)
        self.link(project)
        data = self.settings(project)
        self.assertEqual(data['hooks']['Stop'], [cases.FOREIGN_STOP])
        self.assertEqual(data['hooks']['PreToolUse'], [cases.FOREIGN_PRE])
        self.assertEqual(data['hooks']['Notification'], cases.FOREIGN_SETTINGS['hooks']['Notification'])
        self.assertEqual(data['permissions'], cases.FOREIGN_SETTINGS['permissions'])
        self.assertEqual(data['env'], cases.FOREIGN_SETTINGS['env'])
        self.assertTrue(foreign_script.is_file())

    def test_attribution_is_removed_only_when_it_is_ours(self):
        project = self.installed()
        self.assertEqual(self.settings(project)['attribution'], {'commit': '', 'pr': ''})
        self.manifest['claude'] = False
        self.write_manifest(project)
        self.link(project)
        self.assertNotIn('attribution', self.settings(project))
        mine = self.installed()
        data = self.settings(mine)
        data['attribution'] = {'commit': 'Moj podpis', 'pr': ''}
        self.write_settings(mine, data)
        self.manifest['claude'] = False
        self.write_manifest(mine)
        self.link(mine)
        self.assertEqual(self.settings(mine)['attribution'], {'commit': 'Moj podpis', 'pr': ''})

    def test_the_second_link_changes_nothing(self):
        project = self.installed(local=cases.json_bytes(cases.FOREIGN_SETTINGS))
        self.manifest['claude'] = False
        self.write_manifest(project)
        self.link(project)
        after = self.state(project)
        again = self.link(project)
        self.assertEqual(self.state(project), after)
        self.assertEqual([line for line in self.lines(again) if line.startswith('Updated:') and ('settings.local.json' in line or '.claude/hooks' in line)], [])

    def test_dry_run_changes_nothing(self):
        project = self.installed()
        before = self.state(project)
        self.manifest['claude'] = False
        self.write_manifest(project)
        result = self.link(project, '--dry-run')
        self.assertEqual(self.state(project), before)
        self.assertTrue(any(line.startswith('[dry-run]') for line in self.lines(result)), result.stdout)

    def test_a_tracked_settings_file_is_not_cleaned(self):
        project = self.installed(local=cases.json_bytes(cases.FOREIGN_SETTINGS))
        git(project, 'init', '-q')
        git(project, 'add', '-f', LOCAL)
        before = self.state(project)['settings']
        self.manifest['claude'] = False
        self.write_manifest(project)
        result = self.link(project)
        self.assertEqual(self.state(project)['settings'], before)
        self.assertTrue(any(line.startswith('[warn]') and 'śledzony przez git' in line for line in self.lines(result)), result.stdout)
        self.assertIn('guard-generated.py', self.hook_names(project), 'the scripts stay while their entries stay')

    def test_claude_back_on_installs_again(self):
        project = self.installed()
        self.manifest['claude'] = False
        self.write_manifest(project)
        self.link(project)
        del self.manifest['claude']
        self.write_manifest(project)
        self.link(project)
        self.assertEqual(self.events(project), EVENT_ORDER)
        for script in OUR_SCRIPTS:
            self.assertIn(script, self.hook_names(project))


# --------------------------------------------------------------------------------------------------------------
# idempotence, backup, shape-sensitive detection
# --------------------------------------------------------------------------------------------------------------
class Idempotence(HookLinkBase):
    def test_second_and_third_link_change_nothing(self):
        for local in (None, cases.json_bytes(cases.FOREIGN_SETTINGS), cases.json_bytes(cases.FOREIGN_SETTINGS, eol='\r\n')):
            with self.subTest(foreign=local is not None):
                project = self.project(local=local)
                self.link(project)
                first = self.state(project)
                mtimes = {name: descend(project, f'{cases.HOOKS_DIR}/{name}').stat().st_mtime_ns for name in self.hook_names(project)}
                settings_mtime = descend(project, LOCAL).stat().st_mtime_ns
                for _ in range(2):
                    again = self.link(project)
                    self.assertEqual(self.state(project), first)
                    self.assertEqual(descend(project, LOCAL).stat().st_mtime_ns, settings_mtime, 'the settings file was rewritten')
                    self.assertEqual({name: descend(project, f'{cases.HOOKS_DIR}/{name}').stat().st_mtime_ns for name in self.hook_names(project)}, mtimes)
                    self.assertEqual([line for line in self.lines(again) if line.startswith('Updated:') and ('settings.local.json' in line or '.claude/hooks' in line)], [])

    def test_a_different_cli_argument_alone_is_not_drift(self):
        project = self.project()
        self.link(project)
        data = self.settings(project)
        (_, _, hook), = our_hooks(data, 'session-context.py')
        script, args = parse_command(hook['command'])
        elsewhere = json.dumps(['python3', 'inny-checkout/skillsync'])
        hook['command'] = hook['command'].replace(shlex.quote(args[1]), shlex.quote(elsewhere))
        self.assertNotEqual(parse_command(hook['command'])[1], args)
        self.write_settings(project, data)
        before = self.state(project)
        result = self.link(project)
        self.assertEqual(self.state(project)['settings'], before['settings'], 'the argv of the CLI is not part of the comparison')
        self.assertEqual([line for line in self.lines(result) if line.startswith('Updated:') and 'settings.local.json' in line], [])

    def test_stale_fixed_part_is_rewritten(self):
        project = self.project()
        self.link(project)
        good = self.state(project)['settings']
        data = self.settings(project)
        (_, _, hook), = our_hooks(data, 'check-subagent-report.py')
        hook['command'] = hook['command'].replace('--mode warn', '--mode off')
        (_, _, hook), = our_hooks(data, 'session-context.py')
        hook['timeout'] = 99
        self.write_settings(project, data)
        result = self.link(project)
        self.assertEqual(self.state(project)['settings'], good)
        self.assertTrue(any(line.startswith('Updated:') and 'settings.local.json' in line for line in self.lines(result)), result.stdout)


class BackupPolicy(HookLinkBase):
    def test_backup_after_many_writes_in_one_link_is_the_state_before_the_first(self):
        original = cases.json_bytes(cases.FOREIGN_SETTINGS)
        project = self.project(skills=(), hooks=ALL_ON, local=original)   # plain: the guard and the three new entries, no closure (which would overwrite)
        self.link(project)
        self.assertEqual(self.state(project)['backup'], original)

    def test_a_pre_existing_backup_is_kept_byte_for_byte_by_the_new_entries(self):
        project = self.project(skills=(), hooks=ALL_ON, local=cases.json_bytes(cases.FOREIGN_SETTINGS), backup=cases.PRE_EXISTING_BACKUP)
        self.link(project)
        self.assertEqual(self.state(project)['backup'], cases.PRE_EXISTING_BACKUP)

    def test_enabling_the_new_hooks_later_keeps_the_backup_of_the_first_installation(self):
        original = cases.json_bytes(cases.FOREIGN_SETTINGS)
        project = self.project(hooks=ALL_OFF, local=original)
        self.link(project)
        self.assertEqual(self.state(project)['backup'], original)
        self.set_hooks(project, **FEATURE_DEFAULTS)
        self.link(project)
        self.assertEqual([event for event in self.events(project) if event != 'Notification'], EVENT_ORDER)
        self.assertEqual(self.state(project)['backup'], original, 'earliest: the state before the first write stays')

    def test_the_backup_of_a_file_that_did_not_exist_is_as_the_golden_tree(self):
        """The earlier `link` leaves no backup for a fresh plain project, and for a fresh `feature` project the one of the state after the closure
        hook (the guard hook keeps the earliest state it saw); the new hooks do not change either."""
        plain = self.project(skills=())
        self.link(plain)
        self.assertIsNone(self.state(plain)['backup'])
        self.assertEqual(golden('plain_guard_only')['settings_backup'], None)
        project = self.project()
        self.link(project)
        expected = golden('feature_only')['settings_backup']
        self.assertEqual(self.state(project)['backup'].decode('utf-8'), expected)


class MatcherDrift(HookLinkBase):
    def test_a_changed_matcher_alone_makes_our_entry_stale_and_it_is_rewritten(self):
        for script, event, matcher_now, wanted in (('guard-generated.py', 'PreToolUse', 'Write', GENERATED_MATCHER),
                                                   ('session-context.py', 'SessionStart', 'startup', SESSION_MATCHER),
                                                   ('guard-commands.py', 'PreToolUse', '*', 'Bash')):
            with self.subTest(script=script):
                project = self.project(local=cases.json_bytes(cases.FOREIGN_SETTINGS))
                self.link(project)
                good = self.state(project)['settings']
                data = self.settings(project)
                (_, group, _), = our_hooks(data, script)
                group['matcher'] = matcher_now
                self.write_settings(project, data)
                result = self.link(project)
                self.assertEqual(self.state(project)['settings'], good, 'rewritten to the wanted matcher, the same bytes as a fresh install')
                self.assertTrue(any(line.startswith('Updated:') and 'settings.local.json' in line for line in self.lines(result)), result.stdout)
                (_, group, _), = our_hooks(self.settings(project), script)
                self.assertEqual(group['matcher'], wanted)

    def test_the_two_pre_tool_use_entries_do_not_recognise_each_other(self):
        project = self.project(local=cases.json_bytes(cases.FOREIGN_SETTINGS))
        self.link(project)
        first = self.state(project)['settings']
        again = self.link(project)
        self.assertEqual(self.state(project)['settings'], first)
        self.assertEqual([line for line in self.lines(again) if line.startswith('Updated:') and 'settings.local.json' in line], [])
        groups = self.settings(project)['hooks']['PreToolUse']
        self.assertEqual([group['matcher'] for group in groups], ['Write', 'Bash', GENERATED_MATCHER])
        self.assertEqual(groups[0], cases.FOREIGN_PRE, 'a foreign hook of PreToolUse is untouched')

    def test_a_stale_mode_of_the_guard_of_generated_files_is_rewritten(self):
        project = self.project()
        self.link(project)
        good = self.state(project)['settings']
        data = self.settings(project)
        (_, _, hook), = our_hooks(data, 'guard-generated.py')
        hook['command'] = hook['command'].replace('--mode warn', '--mode deny')
        self.write_settings(project, data)
        self.link(project)
        self.assertEqual(self.state(project)['settings'], good)


# --------------------------------------------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------------------------------------------
class Doctor(HookLinkBase):
    def test_installed_hooks_report_their_state_and_mode_as_info(self):
        project = self.project()
        self.link(project)
        findings = self.doctor(project)
        for script, mode in (('guard-generated.py', 'warn'), ('check-subagent-report.py', 'warn'), ('session-context.py', None)):
            with self.subTest(script=script):
                found = self.about(findings, script)
                self.assertTrue(found, f'doctor says nothing about {script}')
                self.assertEqual({f['level'] for f in found}, {'info'}, found)
                if mode:
                    self.assertTrue(any(mode in f['reason'] for f in found), found)
        commit = [f for f in self.about(findings, 'subagent-commit', 'subagent_commit')]
        self.assertTrue(commit, 'the mode of the commit boundary is reported')
        self.assertTrue(any('deny' in f['reason'] for f in commit), commit)

    def test_a_stale_copy_of_a_script_is_a_warning(self):
        project = self.project()
        self.link(project)
        target = descend(project, f'{cases.HOOKS_DIR}/guard-generated.py')
        target.write_bytes(target.read_bytes() + b'\n# local change\n')
        found = self.about(self.doctor(project), 'guard-generated.py')
        self.assertIn('warning', {f['level'] for f in found}, found)
        self.assertNotIn('error', {f['level'] for f in found}, found)

    def test_a_missing_script_with_its_entry_is_a_warning(self):
        project = self.project()
        self.link(project)
        descend(project, f'{cases.HOOKS_DIR}/check-subagent-report.py').unlink()
        found = self.about(self.doctor(project), 'check-subagent-report.py')
        self.assertIn('warning', {f['level'] for f in found}, found)
        self.assertNotIn('error', {f['level'] for f in found}, found)

    def test_an_unknown_value_is_a_finding_without_the_value(self):
        for key in ('session_context', 'guard_generated', 'subagent_report', 'subagent_commit'):
            with self.subTest(key=key):
                project = self.project(hooks={**FEATURE_DEFAULTS, key: 'zupelnie-bledna-wartosc'})
                found = self.about(self.doctor(project), f'hooks.{key}')
                self.assertTrue(found, f'no finding for hooks.{key}')
                self.assertNotIn('error', {f['level'] for f in found}, 'an unusable value never switches a hook on and is not an error')
                for finding in found:
                    self.assertNotIn('zupelnie-bledna-wartosc', finding['reason'])
                    self.assertNotIn('zupelnie-bledna-wartosc', finding['path'])

    def test_recent_no_report_events_are_counted_over_the_last_14_days(self):
        project = self.project()
        self.link(project)
        now = datetime.datetime.now().astimezone()
        records = []
        for hours in (1, 30, 200):                              # inside the window
            records.append((now - datetime.timedelta(hours=hours)).isoformat())
        for days in (15, 20, 40, 90):                           # outside the window
            records.append((now - datetime.timedelta(days=days)).isoformat())
        lines = [json.dumps({'time': stamp, 'hook': 'subagent-report', 'event': 'SubagentStop', 'agent_type': 'implementer',
                             'reason': 'no-report', 'session_id': 's1'}) for stamp in records]
        lines.insert(2, 'to nie jest json')                     # a broken line is skipped
        descend(project, '.skillsync').mkdir(exist_ok=True)
        target = child(descend(project, '.skillsync'), 'hook-events.jsonl')
        target.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        found = [f for f in self.doctor(project) if re.search(r'subagent', f['path'] + f['reason'], re.IGNORECASE) and re.search(r'\b3\b', f['reason'])]
        self.assertTrue(found, 'doctor reports the number of no-report warnings of the last 14 days')
        self.assertIn(found[0]['level'], ('info',))
        for finding in self.about(self.doctor(project), 'hook-events', 'no-report', 'check-subagent-report'):
            self.assertIsNone(re.search(r'\b(4|7)\b', finding['reason']), f'old events must not be counted: {finding}')

    def test_no_events_file_is_no_warning(self):
        project = self.project()
        self.link(project)
        found = self.about(self.doctor(project), 'hook-events', 'no-report')
        self.assertNotIn('warning', {f['level'] for f in found})
        self.assertNotIn('error', {f['level'] for f in found})

    def test_the_cli_argument_is_not_compared(self):
        project = self.project()
        self.link(project)
        data = self.settings(project)
        (_, _, hook), = our_hooks(data, 'session-context.py')
        _, args = parse_command(hook['command'])
        hook['command'] = hook['command'].replace(shlex.quote(args[1]), shlex.quote(json.dumps(['python3', 'inny-checkout/skillsync'])))
        self.write_settings(project, data)
        found = self.about(self.doctor(project), 'session-context')
        self.assertTrue(found)
        self.assertEqual({f['level'] for f in found}, {'info'}, found)

    def test_a_stale_fixed_part_is_reported(self):
        project = self.project()
        self.link(project)
        data = self.settings(project)
        (_, _, hook), = our_hooks(data, 'session-context.py')
        hook['timeout'] = 99
        self.write_settings(project, data)
        found = self.about(self.doctor(project), 'session-context')
        self.assertIn('warning', {f['level'] for f in found}, found)

    def test_a_plain_project_has_no_findings_about_the_new_hooks(self):
        project = self.project(skills=())
        self.link(project)
        findings = self.doctor(project)
        self.assertEqual(self.about(findings, *NEW_SCRIPTS, 'hooks.session_context', 'hooks.guard_generated', 'hooks.subagent_report',
                                    'hooks.subagent_commit'), [])

    def test_a_project_with_claude_off_has_no_findings_about_the_new_hooks(self):
        project = self.project(claude=False)
        self.link(project)
        findings = self.doctor(project)
        self.assertEqual(self.about(findings, *NEW_SCRIPTS), [])


# --------------------------------------------------------------------------------------------------------------
# review F6: stale argv of the CLI, tracked files, symlinks, interpreter, remaining gaps
# --------------------------------------------------------------------------------------------------------------
class StaleCliArgv(HookLinkBase):
    def with_cli(self, project, argv):
        data = self.settings(project)
        (_, _, hook), = our_hooks(data, 'session-context.py')
        _, args = parse_command(hook['command'])
        hook['command'] = hook['command'].replace(shlex.quote(args[1]), shlex.quote(json.dumps(argv)))
        self.write_settings(project, data)

    def cli_of(self, project):
        (_, _, hook), = our_hooks(self.settings(project), 'session-context.py')
        return json.loads(parse_command(hook['command'])[1][1])

    def test_an_argv_naming_a_missing_path_is_drift_link_rewrites_it_and_doctor_warns(self):
        project = self.project()
        self.link(project)
        good = self.state(project)['settings']
        self.with_cli(project, ['python3', str(child(self.work, 'usuniety-worktree'))])
        found = self.about(self.doctor(project), 'session-context')
        self.assertIn('warning', {f['level'] for f in found}, found)
        result = self.link(project)
        self.assertEqual(self.state(project)['settings'], good)
        self.assertTrue(any(line.startswith('Updated:') and 'settings.local.json' in line for line in self.lines(result)), result.stdout)
        self.assertEqual({f['level'] for f in self.about(self.doctor(project), 'session-context')}, {'info'})

    def test_an_argv_naming_an_existing_path_of_another_checkout_is_not_rewritten(self):
        project = self.project()
        self.link(project)
        other = child(self.work, 'inny-checkout')
        other.write_text('#', encoding='utf-8')
        self.with_cli(project, ['python3', str(other)])
        before = self.state(project)['settings']
        result = self.link(project)
        self.assertEqual(self.state(project)['settings'], before)
        self.assertEqual(self.cli_of(project), ['python3', str(other)])
        self.assertEqual([line for line in self.lines(result) if line.startswith('Updated:') and 'settings.local.json' in line], [])
        self.assertEqual({f['level'] for f in self.about(self.doctor(project), 'session-context')}, {'info'})


class TrackedAndSymlinkedFiles(HookLinkBase):
    def test_tracked_scripts_and_data_files_are_not_removed_by_claude_false(self):
        project = self.project()
        self.link(project)
        git(project, 'init', '-q')
        tracked = ('guard-generated.py', 'attribution-patterns.json')
        for name in tracked:
            git(project, 'add', '-f', f'{cases.HOOKS_DIR}/{name}')
        self.manifest['claude'] = False
        self.write_manifest(project)
        result = self.link(project)
        for name in tracked:
            self.assertIn(name, self.hook_names(project), name)
            self.assertTrue(any(line.startswith('[warn]') and name in line and 'śledzony przez git' in line for line in self.lines(result)), result.stdout)
        for name in set(self.hook_names(project)) - set(tracked):
            self.fail(f'an untracked file of ours stayed: {name}')

    def test_a_tracked_script_of_a_switched_off_hook_stays(self):
        project = self.project()
        self.link(project)
        git(project, 'init', '-q')
        git(project, 'add', '-f', f'{cases.HOOKS_DIR}/check-subagent-report.py')
        self.set_hooks(project, subagent_report='off')
        result = self.link(project)
        self.assertIn('check-subagent-report.py', self.hook_names(project))
        self.assertEqual(our_hooks(self.settings(project), 'check-subagent-report.py'), [])
        self.assertTrue(any(line.startswith('[warn]') and 'check-subagent-report.py' in line for line in self.lines(result)), result.stdout)

    @needs_os_symlinks
    def test_a_symlinked_script_keeps_its_target_and_gets_a_warning(self):
        project = self.project()
        target = child(self.work, 'moj-skrypt.py')
        target.write_bytes(b'# moj\n')
        folder = descend(project, cases.HOOKS_DIR)
        folder.mkdir(parents=True)
        link = child(folder, 'session-context.py')
        os.symlink(target, link)
        result = self.link(project)
        self.assertEqual(target.read_bytes(), b'# moj\n', 'the target of the symlink is never overwritten')
        self.assertTrue(link.is_symlink())
        self.assertTrue(any(line.startswith('[warn]') and 'session-context.py' in line for line in self.lines(result)), result.stdout)
        self.assertEqual(our_hooks(self.settings(project), 'session-context.py'), [], 'no entry without our script')


class ClaudeFalseBrokenJson(HookLinkBase):
    def test_scripts_stay_and_one_warning_when_the_settings_are_not_json(self):
        project = self.project()
        self.link(project)
        names = self.hook_names(project)
        descend(project, LOCAL).write_bytes(b'{to nie jest json')
        self.manifest['claude'] = False
        self.write_manifest(project)
        result = self.link(project)
        self.assertEqual(self.hook_names(project), names)
        self.assertEqual(descend(project, LOCAL).read_bytes(), b'{to nie jest json')
        warns = [line for line in self.lines(result) if line.startswith('[warn]') and 'settings.local.json' in line]
        self.assertEqual(len(warns), 1, result.stdout)


class FreshDryRun(HookLinkBase):
    def test_nothing_is_written_with_the_new_hooks_on_by_default(self):
        project = self.project()
        result = self.link(project, '--dry-run')
        self.assertEqual(self.hook_names(project), [])
        self.assertFalse(descend(project, LOCAL).exists())
        self.assertFalse(descend(project, BACKUP).exists())
        lines = self.lines(result)
        for script in NEW_SCRIPTS:
            self.assertTrue(any(line.startswith('[dry-run]') and script in line for line in lines), (script, result.stdout))
        self.assertFalse([line for line in lines if line.startswith('Updated:') and ('settings.local.json' in line or '.claude/hooks' in line)], result.stdout)

    def test_dry_run_does_not_announce_a_write_of_an_identical_script(self):
        project = self.project()
        self.link(project)
        result = self.link(project, '--dry-run')
        self.assertEqual([line for line in self.lines(result) if line.startswith('[dry-run]') and any(script in line for script in NEW_SCRIPTS)], [], result.stdout)


class InterpreterDrift(HookLinkBase):
    def set_python(self, command):
        path = Path(self.env['SKILLSYNC_CONFIG'])
        config = yaml.safe_load(path.read_text(encoding='utf-8'))
        config['python_command'] = command
        path.write_text(yaml.safe_dump(config, sort_keys=False), encoding='utf-8')

    def test_a_changed_python_command_is_rewritten_by_link_and_reported_by_doctor(self):
        project = self.project()
        self.link(project)
        self.set_python('python3.99')
        for script in NEW_SCRIPTS[1:]:
            self.assertIn('warning', {f['level'] for f in self.about(self.doctor(project), script)}, script)
        result = self.link(project)
        self.assertTrue(any(line.startswith('Updated:') and 'settings.local.json' in line for line in self.lines(result)), result.stdout)
        for script in NEW_SCRIPTS:
            (_, _, hook), = our_hooks(self.settings(project), script)
            self.assertIn(' python3.99 ', hook['command'])
            self.assertEqual({f['level'] for f in self.about(self.doctor(project), script)}, {'info'}, script)


class DoctorStates(HookLinkBase):
    def test_off_in_the_manifest_but_installed_is_a_warning(self):
        project = self.project()
        self.link(project)
        self.set_hooks(project, guard_generated='off')
        found = self.about(self.doctor(project), 'guard-generated.py')
        self.assertEqual({f['level'] for f in found}, {'warning'}, found)
        self.assertTrue(any('off' in f['reason'] for f in found), found)

    def test_on_but_not_installed_is_a_warning(self):
        project = self.project(skills=())
        self.link(project)
        self.set_hooks(project, session_context=True)
        found = self.about(self.doctor(project), 'session-context.py')
        self.assertEqual({f['level'] for f in found}, {'warning'}, found)
        self.assertTrue(any('not installed' in f['reason'] for f in found), found)

    def test_a_drift_of_the_commit_boundary_is_a_warning(self):
        project = self.project()
        self.link(project)
        self.set_hooks(project, subagent_commit='warn')
        found = self.about(self.doctor(project), 'subagent_commit')
        self.assertIn('warning', {f['level'] for f in found}, found)
        self.link(project)
        self.assertNotIn('warning', {f['level'] for f in self.about(self.doctor(project), 'subagent_commit')})

    def test_a_never_linked_project_has_no_findings_about_the_new_hooks(self):
        project = self.project()
        self.assertEqual(self.about(self.doctor(project), *NEW_SCRIPTS), [])


class EventCounting(HookLinkBase):
    def load_session_context(self):
        spec = importlib.util.spec_from_file_location('session_context_under_test', str(descend(REPO, 'library/hooks/session-context.py')))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def write_events(self, root, lines):
        folder = descend(root, '.skillsync')
        folder.mkdir(parents=True, exist_ok=True)
        child(folder, 'hook-events.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    @staticmethod
    def event(stamp, hook='subagent-report'):
        return json.dumps({'time': stamp, 'hook': hook, 'event': 'SubagentStop', 'reason': 'no-report'})

    def test_the_window_edge_and_a_timestamp_without_a_zone_count_as_in_session_context(self):
        from skillsync_workflow import hooksync
        project = self.project()
        now = datetime.datetime.now().astimezone()
        day = datetime.timedelta(days=1)
        lines = [self.event((now - 14 * day + datetime.timedelta(minutes=5)).isoformat()),      # just inside
                 self.event((now - 14 * day - datetime.timedelta(minutes=5)).isoformat()),      # just outside
                 self.event((now + datetime.timedelta(hours=1)).isoformat()),                   # in the future
                 self.event((datetime.datetime.now() - day).isoformat()),                       # no zone, inside (local time)
                 self.event((datetime.datetime.now() - 20 * day).isoformat()),                  # no zone, outside
                 self.event((now - day).isoformat(), hook='inny'),                              # another hook
                 'to nie jest json', '[1, 2]', json.dumps({'hook': 'subagent-report', 'time': 12})]
        self.write_events(project, lines)
        self.assertEqual(hooksync.count_recent_events(project), 2)
        self.assertEqual(self.load_session_context().count_warnings(str(project)), hooksync.count_recent_events(project))

    def test_the_parser_gives_the_same_count_as_session_context_on_a_long_file(self):
        from skillsync_workflow import hooksync
        project = self.project()
        now = datetime.datetime.now().astimezone()
        # one minute off the hour grid: no event sits exactly on the 14-day edge, where two parsers reading the clock
        # in different ticks (Windows: ~15 ms resolution) could disagree by one
        lines = [self.event((now - datetime.timedelta(hours=index, minutes=1)).isoformat()) for index in range(0, 900, 3)]
        self.write_events(project, lines)
        self.assertEqual(hooksync.count_recent_events(project), self.load_session_context().count_warnings(str(project)))
        self.assertGreater(hooksync.count_recent_events(project), 0)


# --------------------------------------------------------------------------------------------------------------
# the negative assertions can fail (evidence for the rule of tests-baseline)
# --------------------------------------------------------------------------------------------------------------
class HookLinkMeta(HookLinkBase):
    def test_nothing_new_helper_fails_when_a_new_script_or_entry_is_present(self):
        project = self.project(skills=())
        self.link(project)
        self.assert_nothing_new(project)
        planted = descend(project, f'{cases.HOOKS_DIR}/session-context.py')
        planted.write_bytes(b'x')
        with self.assertRaises(AssertionError):
            self.assert_nothing_new(project)
        planted.unlink()
        data = self.settings(project)
        data['hooks']['SubagentStop'] = [{'matcher': '', 'hooks': [{'type': 'command', 'command': 'python3 .claude/hooks/check-subagent-report.py'}]}]
        self.write_settings(project, data)
        with self.assertRaises(AssertionError):
            self.assert_nothing_new(project)

    def test_parse_command_refuses_a_foreign_shape(self):
        with self.assertRaises(AssertionError):
            parse_command('echo obcy')


if __name__ == '__main__':
    unittest.main()
