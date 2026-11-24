"""CLAUDE-3 F1: the shared merge of hook entries into `.claude/settings.local.json` (spec K1, K2; plan "Kontrakty wykonawcze").

Two groups of tests:

1. GOLDEN TREES (black box, K2). `link` is run on a fresh temporary project for every case of `tests/fixtures/hooksync/cases.py` and compared with the
   pattern in `tests/fixtures/hooksync/golden/` that the code BEFORE the refactor wrote (`generate_golden.py`): the bytes of settings.local.json and of
   its backup, the names and bytes of the files in `.claude/hooks`, `.claude/.gitignore`, the order of the events and of the top-level keys, and the
   normalised `Updated:`/`[warn]`/`[dry-run]` lines. They are GREEN on the old code and have to stay green after the merge moves to `hooksync`.
2. API of the new module `skillsync_workflow/hooksync.py` (K1):
   `merge_entry(settings_path, event, entry, is_ours, want, dry_run, backup='overwrite'|'earliest', extra=None)`; RED (ImportError) until it exists.
   The contract is that of the merge functions today (`merge_closure_settings`, `merge_guard_settings`): `entry` is one group
   `{"matcher": ..., "hooks": [...]}`; `is_ours(hook)` recognises our hook in any group; the result is True when no entry of ours remains after the
   call (so `want=True` gives False and a successful removal True); a tracked file is never written (`[warn] ... jest śledzony przez git, pomijam zapis`);
   an unusable file is a `[warn]` line, never an exception; the EOL of the file is kept; foreign hooks, permissions and other keys stay; `extra` is a
   top-level key written in the same read and write as the entry (the guard's `attribution`).
Temporary paths come from `tmp_paths.child`/`descend`; the tests use the repository library (no `SKILLSYNC_TEST_LIBRARY`)."""
import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from pathlib import Path as _Path_tmp_paths
import sys as _sys_tmp_paths

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE / 'fixtures' / 'hooksync'))
from layers_helpers import IsolatedTestCase, git  # noqa: E402
_sys_tmp_paths.path.insert(0, str(_Path_tmp_paths(__file__).resolve().parent))  # noqa: E402
from tmp_paths import child, descend  # noqa: E402
import cases  # noqa: E402


def hooksync():
    """The module under test; ImportError (the module does not exist yet) fails the test that asks for it."""
    from skillsync_workflow import hooksync as module
    return module


# --------------------------------------------------------------------------------------------------------------
# 1. golden trees
# --------------------------------------------------------------------------------------------------------------
class GoldenTrees(IsolatedTestCase):
    """`link` of the current code against the patterns of the code before the refactor (K2)."""

    def pattern(self, name):
        path = child(cases.GOLDEN, f'{name}.json')
        raw = path.read_bytes()
        self.assertNotIn(b'\r', raw, f'{name}: a raw CR byte in the fixture file (.gitattributes would turn it into LF)')
        return json.loads(raw.decode('utf-8'))

    def run_and_compare(self, name):
        expected = self.pattern(name)
        cases.write_machine_config(self.env['SKILLSYNC_CONFIG'])
        actual = cases.run_case(name, cases.CASES[name], self.work)
        for key in ('settings_local', 'settings_backup', 'hook_files', 'claude_gitignore', 'root_gitignore_skillsync',
                    'top_level_keys', 'event_order', 'stdout', 'second_stdout', 'second_settings_local', 'second_settings_backup',
                    'second_hook_files'):
            self.assertEqual(actual.get(key), expected.get(key), f'{name}: {key}')
        self.assertEqual(sorted(actual), sorted(expected), f'{name}: the set of captured fields')
        project = child(self.work, name)
        for hook_name in actual['hook_files']:   # every copied hook file is byte-equal to its source in the repository
            source = cases.HOOK_SOURCES[hook_name]
            self.assertEqual(descend(project, f'.claude/hooks/{hook_name}').read_bytes(), descend(REPO, source).read_bytes(),
                             f'{name}: .claude/hooks/{hook_name} differs from {source}')
        return actual, expected

    def test_pattern_files_match_the_case_matrix(self):
        on_disk = sorted(path.stem for path in cases.GOLDEN.glob('*.json'))
        self.assertEqual(on_disk, sorted(cases.CASES))

    def test_feature_only(self):
        actual, _ = self.run_and_compare('feature_only')
        # the order of the events and keys is the contract of K2 (checked here independently of the pattern)
        self.assertEqual(actual['event_order'], ['Stop', 'PostToolUse', 'PreToolUse'])
        self.assertEqual(actual['top_level_keys'], ['hooks', 'attribution'])
        self.assertEqual(sorted(actual['hook_files']), ['attribution-patterns.json', 'check-closure.py', 'guard-commands.py',
                                                       'guard-tests.py', 'test-tools.json'])

    def test_plain_project_with_only_the_command_guard(self):
        actual, _ = self.run_and_compare('plain_guard_only')
        self.assertEqual(actual['event_order'], ['PreToolUse'])
        self.assertNotIn('check-closure.py', actual['hook_files'])

    def test_guard_tests_warn(self):
        actual, _ = self.run_and_compare('guard_tests_warn')
        self.assertIn('guard-tests.py', actual['hook_files'])
        self.assertIn('test-tools.json', actual['hook_files'])

    def test_foreign_entries_survive(self):
        actual, _ = self.run_and_compare('foreign_entries')
        data = json.loads(actual['settings_local'])
        self.assertEqual(data['permissions'], cases.FOREIGN_SETTINGS['permissions'])
        self.assertEqual(data['env'], cases.FOREIGN_SETTINGS['env'])
        self.assertIn(cases.FOREIGN_STOP, data['hooks']['Stop'])
        self.assertIn(cases.FOREIGN_PRE, data['hooks']['PreToolUse'])
        self.assertEqual(data['hooks']['Notification'], cases.FOREIGN_SETTINGS['hooks']['Notification'])
        self.assertEqual(list(data['hooks'])[:3], ['Stop', 'PreToolUse', 'Notification'], 'existing events keep their place, ours are not moved')

    def test_stale_entries_in_shared_groups_are_replaced_and_foreign_hooks_stay(self):
        actual, _ = self.run_and_compare('shared_group_stale')
        data = json.loads(actual['settings_local'])
        stop_commands = [hook['command'] for group in data['hooks']['Stop'] for hook in group['hooks']]
        self.assertIn('echo obcy-w-grupie', stop_commands)
        self.assertNotIn('--stary-argument', ''.join(stop_commands))
        pre_commands = [hook['command'] for group in data['hooks']['PreToolUse'] for hook in group['hooks']]
        self.assertIn('echo obcy-bash', pre_commands)
        self.assertNotIn('--mode stary', ''.join(pre_commands))

    def test_attribution_switched_on_is_replaced_with_a_warning(self):
        actual, _ = self.run_and_compare('attribution_on')
        self.assertEqual(json.loads(actual['settings_local'])['attribution'], {'commit': '', 'pr': ''})
        self.assertTrue(any(line.startswith('[warn]') and 'attribution' in line for line in actual['stdout']), actual['stdout'])

    def test_crlf_settings_keep_their_line_ending(self):
        actual, expected = self.run_and_compare('crlf_settings')
        written = descend(child(self.work, 'crlf_settings'), cases.LOCAL).read_bytes()
        self.assertIn(b'\r\n', written)
        self.assertNotIn(b'\n', written.replace(b'\r\n', b''), 'a bare LF next to CRLF')
        self.assertIn('\r\n', expected['settings_local'], 'the pattern holds explicit CRLF (as an escape inside the JSON string)')
        backup = descend(child(self.work, 'crlf_settings'), cases.BACKUP).read_bytes()
        self.assertEqual(backup, cases.json_bytes(cases.FOREIGN_SETTINGS, eol='\r\n'), 'the backup is the CRLF input byte for byte')

    def test_tracked_settings_are_not_written(self):
        actual, _ = self.run_and_compare('tracked_settings')
        self.assertEqual(descend(child(self.work, 'tracked_settings'), cases.LOCAL).read_bytes(), cases.json_bytes(cases.FOREIGN_SETTINGS))
        self.assertIsNone(actual['settings_backup'])
        self.assertTrue(any('śledzony przez git' in line for line in actual['stdout']), actual['stdout'])

    def test_invalid_json_is_a_warning_and_the_file_stays(self):
        actual, _ = self.run_and_compare('invalid_json')
        self.assertEqual(descend(child(self.work, 'invalid_json'), cases.LOCAL).read_bytes(), b'{ to nie jest json\r\n')
        self.assertTrue(any(line.startswith('[warn]') for line in actual['stdout']), actual['stdout'])
        self.assertIsNone(actual['settings_backup'])

    def test_backup_policy_closure_overwrites_guard_keeps_the_earliest(self):
        actual, _ = self.run_and_compare('backup_existing_feature')
        # closure writes first and replaces the old backup with the file it saw; the guard then finds a backup and keeps it
        self.assertEqual(descend(child(self.work, 'backup_existing_feature'), cases.BACKUP).read_bytes(), cases.json_bytes(cases.FOREIGN_SETTINGS))
        self.assertNotEqual(actual['settings_backup'], cases.PRE_EXISTING_BACKUP.decode('utf-8'))

    def test_backup_policy_guard_alone_keeps_the_earliest_backup_bytes(self):
        actual, _ = self.run_and_compare('backup_existing_plain')
        self.assertEqual(descend(child(self.work, 'backup_existing_plain'), cases.BACKUP).read_bytes(), cases.PRE_EXISTING_BACKUP)

    def test_claude_false_from_the_start_installs_nothing(self):
        actual, _ = self.run_and_compare('claude_false_fresh')
        self.assertIsNone(actual['settings_local'])
        self.assertEqual(actual['hook_files'], [])

    # K10 (F6): the golden case `claude_false_after_install` (the old `link` left our entries and scripts after `claude: false`) is removed ON PURPOSE:
    # the new behaviour (everything of ours is removed) is tested in tests/test_skillsync_hook_link.py, class ClaudeFalse.

    def test_dry_run_writes_nothing(self):
        actual, _ = self.run_and_compare('feature_dry_run')
        self.assertIsNone(actual['settings_local'])
        self.assertTrue(any(line.startswith('[dry-run]') and 'settings.local.json' in line for line in actual['stdout']), actual['stdout'])

    def test_dry_run_over_a_foreign_file_keeps_its_bytes(self):
        actual, _ = self.run_and_compare('foreign_dry_run')
        self.assertEqual(descend(child(self.work, 'foreign_dry_run'), cases.LOCAL).read_bytes(), cases.json_bytes(cases.FOREIGN_SETTINGS))
        self.assertIsNone(actual['settings_backup'])

    def test_second_link_is_idempotent_in_every_case(self):
        for name, spec in cases.CASES.items():
            if spec['dry_run']:
                continue
            with self.subTest(case=name):
                expected = self.pattern(name)
                self.assertEqual(expected['second_settings_local'], expected['settings_local'])
                self.assertEqual(expected['second_settings_backup'], expected['settings_backup'])
                self.assertFalse([line for line in expected['second_stdout'] if line.startswith('Updated:') and 'settings.local.json' in line],
                                 'the second link rewrote settings.local.json')


# --------------------------------------------------------------------------------------------------------------
# 2. API of skillsync_workflow/hooksync.py
# --------------------------------------------------------------------------------------------------------------
def ours(hook):
    return isinstance(hook, dict) and hook.get('type') == 'command' and 'ours-hook.py' in str(hook.get('command', ''))


def entry(command='python3 .claude/hooks/ours-hook.py --mode a', matcher='', timeout=10):
    return {'matcher': matcher, 'hooks': [{'type': 'command', 'command': command, 'timeout': timeout}]}


FOREIGN = {'matcher': '', 'hooks': [{'type': 'command', 'command': 'echo obcy'}]}


def dumped(data, eol='\n'):
    return (json.dumps(data, indent=2, ensure_ascii=False) + '\n').replace('\n', eol).encode('utf-8')


class MergeEntry(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.project = child(self.work, 'proj')
        self.project.mkdir()
        self.path = descend(self.project, '.claude/settings.local.json')
        self.backup = descend(self.project, '.claude/settings.local.json.skillsync-backup')

    def put(self, data, eol='\n'):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(data if isinstance(data, bytes) else dumped(data, eol))

    def read(self):
        return json.loads(self.path.read_bytes().decode('utf-8'))

    def merge(self, event='Stop', hook_entry=None, want=True, dry_run=False, **kwargs):
        module = hooksync()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = module.merge_entry(self.path, event, hook_entry if hook_entry is not None else entry(), ours, want, dry_run, **kwargs)
        return result, out.getvalue()

    def snapshot(self):
        return self.path.read_bytes(), self.path.stat().st_mtime_ns

    # --- add / update / remove ------------------------------------------------------------------------------
    def test_add_creates_a_missing_file_with_the_entry(self):
        result, out = self.merge()
        self.assertIs(result, False)   # an entry of ours is there: "no entry of ours remains" is False
        self.assertEqual(self.read(), {'hooks': {'Stop': [entry()]}})
        self.assertEqual(self.path.read_bytes(), dumped({'hooks': {'Stop': [entry()]}}), 'indent 2, no ASCII escaping, LF, a final newline')
        self.assertEqual(out.splitlines(), [f'Updated: {self.path}'])
        self.assertFalse(self.backup.exists(), 'a file that did not exist has no backup')

    def test_add_keeps_foreign_groups_permissions_and_other_keys(self):
        original = {'permissions': {'allow': ['Bash(ls:*)']}, 'env': {'X': 'ż'}, 'hooks': {'Stop': [FOREIGN], 'Notification': [FOREIGN]}}
        self.put(original)
        self.merge()
        data = self.read()
        self.assertEqual(data['permissions'], original['permissions'])
        self.assertEqual(data['env'], original['env'])
        self.assertEqual(data['hooks']['Stop'], [FOREIGN, entry()], 'ours is appended after the foreign groups')
        self.assertEqual(data['hooks']['Notification'], [FOREIGN])
        self.assertEqual(list(data), ['permissions', 'env', 'hooks'])

    def test_current_entry_is_not_written_again(self):
        self.put({'hooks': {'Stop': [FOREIGN, entry()]}})
        before = self.snapshot()
        result, out = self.merge()
        self.assertEqual(out, '')
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.backup.exists())
        self.assertIs(result, False)

    def test_second_identical_merge_changes_nothing(self):
        self.merge()
        before = self.snapshot()
        _, out = self.merge()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(out, '')

    def test_update_replaces_our_stale_hook_and_keeps_the_foreign_one_of_the_same_group(self):
        stale = {'type': 'command', 'command': 'python3 .claude/hooks/ours-hook.py --mode stary', 'timeout': 99}
        self.put({'hooks': {'Stop': [{'matcher': '', 'hooks': [FOREIGN['hooks'][0], stale]}, FOREIGN]}})
        self.merge()
        stop = self.read()['hooks']['Stop']
        self.assertEqual(stop, [{'matcher': '', 'hooks': [FOREIGN['hooks'][0]]}, FOREIGN, entry()])

    def test_group_without_hooks_key_is_left_alone(self):
        odd = {'matcher': 'Bash', 'comment': 'bez klucza hooks'}
        self.put({'hooks': {'Stop': [odd]}})
        self.merge()
        self.assertEqual(self.read()['hooks']['Stop'], [odd, entry()])

    def test_remove_deletes_ours_and_keeps_the_rest(self):
        self.put({'permissions': {'allow': ['A']}, 'hooks': {'Stop': [FOREIGN, entry()], 'PreToolUse': [FOREIGN]}})
        result, out = self.merge(want=False)
        self.assertIs(result, True)
        self.assertEqual(self.read(), {'permissions': {'allow': ['A']}, 'hooks': {'Stop': [FOREIGN], 'PreToolUse': [FOREIGN]}})
        self.assertEqual(out.splitlines(), [f'Updated: {self.path}'])

    def test_remove_of_the_only_entry_drops_the_event_and_keeps_the_hooks_object(self):
        self.put({'permissions': {'allow': ['A']}, 'hooks': {'Stop': [entry()]}})
        result, _ = self.merge(want=False)
        self.assertIs(result, True)
        self.assertEqual(self.read(), {'permissions': {'allow': ['A']}, 'hooks': {}})

    def test_remove_from_a_group_shared_with_a_foreign_hook(self):
        self.put({'hooks': {'Stop': [{'matcher': '', 'hooks': [FOREIGN['hooks'][0], entry()['hooks'][0]]}]}})
        self.merge(want=False)
        self.assertEqual(self.read()['hooks']['Stop'], [FOREIGN])

    def test_remove_without_a_file_is_true_and_creates_nothing(self):
        result, out = self.merge(want=False)
        self.assertIs(result, True)
        self.assertEqual(out, '')
        self.assertFalse(self.path.exists())
        self.assertFalse(self.path.parent.exists(), 'no directory is created for a removal')

    def test_remove_when_nothing_of_ours_is_there_writes_nothing(self):
        self.put({'hooks': {'Stop': [FOREIGN]}})
        before = self.snapshot()
        result, out = self.merge(want=False)
        self.assertIs(result, True)
        self.assertEqual((out, self.snapshot()), ('', before))
        self.assertFalse(self.backup.exists())

    def test_events_are_added_in_call_order_after_the_existing_ones(self):
        self.put({'hooks': {'Notification': [FOREIGN]}})
        for event in ('Stop', 'PreToolUse', 'SessionStart', 'SubagentStop'):
            self.merge(event=event)
        self.assertEqual(list(self.read()['hooks']), ['Notification', 'Stop', 'PreToolUse', 'SessionStart', 'SubagentStop'])

    def test_same_input_gives_the_same_bytes(self):
        results = []
        for name in ('one', 'two'):
            self.path = descend(child(self.work, name), '.claude/settings.local.json')
            self.put({'permissions': {'allow': ['A']}})
            for event in ('Stop', 'PreToolUse'):
                self.merge(event=event)
            results.append(self.path.read_bytes())
        self.assertEqual(results[0], results[1])

    # --- the safety net: tracked file, unusable file, dry run -----------------------------------------------------
    def test_tracked_file_is_skipped_with_a_warning(self):
        self.put({'permissions': {'allow': ['A']}})
        git(self.project, 'init', '-q')
        git(self.project, 'add', '-f', '.claude/settings.local.json')
        before = self.snapshot()
        result, out = self.merge()
        self.assertIs(result, False)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.backup.exists())
        self.assertEqual(out.splitlines(), [f'[warn] {self.path} jest śledzony przez git, pomijam zapis'])

    def test_tracked_file_with_our_entry_is_not_cleaned_by_a_removal(self):
        self.put({'hooks': {'Stop': [entry()]}})
        git(self.project, 'init', '-q')
        git(self.project, 'add', '-f', '.claude/settings.local.json')
        before = self.snapshot()
        result, out = self.merge(want=False)
        self.assertIs(result, False)   # our entry remains in the file
        self.assertEqual(self.snapshot(), before)
        self.assertIn('śledzony przez git', out)

    def test_untracked_file_in_a_git_repository_is_written(self):
        self.put({'permissions': {'allow': ['A']}})
        git(self.project, 'init', '-q')
        self.merge()
        self.assertEqual(self.read()['hooks']['Stop'], [entry()])

    def test_invalid_json_is_a_warning_not_an_exception(self):
        self.put(b'{ nie json')
        before = self.snapshot()
        result, out = self.merge()
        self.assertIs(result, False)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.backup.exists())
        self.assertEqual(len(out.splitlines()), 1)
        self.assertTrue(out.startswith('[warn]') and str(self.path) in out, out)

    def test_wrong_structures_are_warnings_and_leave_the_file(self):
        shapes = {'root is a list': [], 'hooks is a list': {'hooks': []}, 'the event is not a list': {'hooks': {'Stop': {}}},
                  'a group is not an object': {'hooks': {'Stop': ['x']}}, 'group hooks is not a list': {'hooks': {'Stop': [{'hooks': 'x'}]}}}
        for label, data in shapes.items():
            with self.subTest(label):
                self.put(data)
                before = self.snapshot()
                result, out = self.merge()
                self.assertIs(result, False)
                self.assertEqual(self.snapshot(), before)
                self.assertTrue(out.startswith('[warn]') and str(self.path) in out, out)
                self.assertEqual(len(out.splitlines()), 1)

    def test_non_utf8_file_is_a_warning_not_an_exception(self):
        self.put(b'\xff\xfe{"hooks": {}}')
        before = self.snapshot()
        result, out = self.merge()
        self.assertIs(result, False)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(out.startswith('[warn]'), out)

    def test_dry_run_prints_and_writes_nothing(self):
        self.put({'permissions': {'allow': ['A']}})
        before = self.snapshot()
        result, out = self.merge(dry_run=True)
        self.assertIs(result, False)   # `not want`: with want the entry would be there
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.backup.exists())
        self.assertEqual(out.splitlines(), [f'[dry-run] Would update: {self.path}'])

    def test_dry_run_of_a_removal_returns_true_and_writes_nothing(self):
        self.put({'hooks': {'Stop': [entry()]}})
        before = self.snapshot()
        result, out = self.merge(want=False, dry_run=True)
        self.assertIs(result, True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(out.splitlines(), [f'[dry-run] Would update: {self.path}'])

    def test_dry_run_without_a_file_creates_neither_file_nor_directory(self):
        _, out = self.merge(dry_run=True)
        self.assertFalse(self.path.parent.exists())
        self.assertIn('[dry-run]', out)

    # --- backup policy ---------------------------------------------------------------------------------------
    def test_overwrite_policy_replaces_an_existing_backup(self):
        original = {'permissions': {'allow': ['A']}}
        self.put(original)
        self.backup.write_bytes(b'stara kopia\r\n')
        self.merge(backup='overwrite')
        self.assertEqual(self.backup.read_bytes(), dumped(original))

    def test_overwrite_is_the_default_policy(self):
        original = {'permissions': {'allow': ['A']}}
        self.put(original)
        self.backup.write_bytes(b'stara kopia\r\n')
        self.merge()
        self.assertEqual(self.backup.read_bytes(), dumped(original))

    def test_earliest_policy_keeps_an_existing_backup_byte_for_byte(self):
        self.put({'permissions': {'allow': ['A']}})
        self.backup.write_bytes(b'stara kopia\r\n\xc5\xbc')
        self.merge(backup='earliest')
        self.assertEqual(self.backup.read_bytes(), b'stara kopia\r\n\xc5\xbc')

    def test_earliest_policy_creates_the_backup_when_there_is_none(self):
        original = {'permissions': {'allow': ['A']}}
        self.put(original)
        self.merge(backup='earliest')
        self.assertEqual(self.backup.read_bytes(), dumped(original))

    def test_two_writes_earliest_keeps_the_first_overwrite_keeps_the_last_state(self):
        original = {'permissions': {'allow': ['A']}}
        for policy in ('earliest', 'overwrite'):
            self.path = descend(child(self.work, policy), '.claude/settings.local.json')
            self.backup = descend(child(self.work, policy), '.claude/settings.local.json.skillsync-backup')
            self.put(original)
            self.merge(event='Stop', backup=policy)
            after_first = self.path.read_bytes()
            self.merge(event='PreToolUse', backup=policy)
            expected = dumped(original) if policy == 'earliest' else after_first
            self.assertEqual(self.backup.read_bytes(), expected, policy)

    def test_backup_of_a_crlf_file_is_the_crlf_bytes(self):
        original = {'permissions': {'allow': ['A']}}
        self.put(original, eol='\r\n')
        self.merge(backup='overwrite')
        self.assertEqual(self.backup.read_bytes(), dumped(original, '\r\n'))

    # --- extra: a top-level key in the same read and write -------------------------------------------------------
    def test_extra_is_written_in_the_same_write_as_the_entry(self):
        original = {'permissions': {'allow': ['A']}}
        self.put(original)
        _, out = self.merge(extra={'attribution': {'commit': '', 'pr': ''}})
        data = self.read()
        self.assertEqual(data['attribution'], {'commit': '', 'pr': ''})
        self.assertEqual(data['hooks']['Stop'], [entry()])
        self.assertEqual(out.splitlines(), [f'Updated: {self.path}'], 'one write, one Updated line')
        self.assertEqual(self.backup.read_bytes(), dumped(original), 'one backup of the file as it was before')

    def test_extra_replaces_a_different_value(self):
        self.put({'attribution': {'commit': 'x', 'pr': 'y'}, 'hooks': {'Stop': [entry()]}})
        self.merge(extra={'attribution': {'commit': '', 'pr': ''}})
        self.assertEqual(self.read()['attribution'], {'commit': '', 'pr': ''})

    def test_a_different_extra_alone_triggers_a_write_even_when_the_entry_is_current(self):
        self.put({'attribution': {'commit': 'x', 'pr': 'y'}, 'hooks': {'Stop': [entry()]}})
        _, out = self.merge(extra={'attribution': {'commit': '', 'pr': ''}})
        self.assertEqual(out.splitlines(), [f'Updated: {self.path}'])
        self.assertEqual(self.read()['attribution'], {'commit': '', 'pr': ''})

    def test_current_entry_and_current_extra_write_nothing(self):
        self.put({'hooks': {'Stop': [entry()]}, 'attribution': {'commit': '', 'pr': ''}})
        before = self.snapshot()
        _, out = self.merge(extra={'attribution': {'commit': '', 'pr': ''}})
        self.assertEqual((out, self.snapshot()), ('', before))

    def test_extra_is_not_applied_by_a_removal(self):
        self.put({'attribution': {'commit': 'x', 'pr': 'y'}, 'hooks': {'Stop': [entry()]}})
        self.merge(want=False, extra={'attribution': {'commit': '', 'pr': ''}})
        data = self.read()
        self.assertEqual(data['attribution'], {'commit': 'x', 'pr': 'y'})
        self.assertEqual(data['hooks'], {})

    def test_extra_is_not_written_into_a_tracked_file(self):
        self.put({'permissions': {'allow': ['A']}})
        git(self.project, 'init', '-q')
        git(self.project, 'add', '-f', '.claude/settings.local.json')
        before = self.snapshot()
        self.merge(extra={'attribution': {'commit': '', 'pr': ''}})
        self.assertEqual(self.snapshot(), before)

    # --- EOL --------------------------------------------------------------------------------------------------------
    def test_crlf_file_stays_crlf(self):
        self.put({'permissions': {'allow': ['A']}}, eol='\r\n')
        self.merge()
        raw = self.path.read_bytes()
        self.assertIn(b'\r\n', raw)
        self.assertEqual(raw.count(b'\n'), raw.count(b'\r\n'), 'every LF is part of CRLF')
        self.assertEqual(self.read()['hooks']['Stop'], [entry()])

    def test_lf_file_stays_lf(self):
        self.put({'permissions': {'allow': ['A']}})
        self.merge()
        self.assertNotIn(b'\r', self.path.read_bytes())

    def test_non_ascii_is_kept_unescaped(self):
        self.put({'env': {'KLUCZ': 'zażółć gęślą'}})
        self.merge()
        self.assertIn('zażółć gęślą'.encode('utf-8'), self.path.read_bytes())

    def test_merge_accepts_a_string_path(self):
        module = hooksync()
        with contextlib.redirect_stdout(io.StringIO()):
            module.merge_entry(str(self.path), 'Stop', entry(), ours, True, False)
        self.assertEqual(self.read(), {'hooks': {'Stop': [entry()]}})


class FixtureSelfChecks(unittest.TestCase):
    """The fixtures themselves: a CRLF case keeps its 0x0D bytes where the repository would normalise a checked-in file."""

    def test_crlf_input_is_built_with_cr_bytes(self):
        data = cases.CASES['crlf_settings']['local']
        self.assertIn(b'\r\n', data)
        self.assertEqual(data.count(b'\n'), data.count(b'\r\n'))

    def test_pre_existing_backup_input_has_cr_bytes(self):
        self.assertIn(b'\r', cases.PRE_EXISTING_BACKUP)

    def test_crlf_pattern_has_the_escape_not_the_byte(self):
        raw = child(cases.GOLDEN, 'crlf_settings.json').read_bytes()
        self.assertNotIn(b'\r', raw)
        self.assertIn(b'\\r\\n', raw)


if __name__ == '__main__':
    unittest.main()
