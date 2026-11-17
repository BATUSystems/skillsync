"""180-J2: the generator side of the test commands part of the guard hook: the manifest keys, the entry arguments, the files next to the hook, `doctor`, and the
dormant state (`TESTS_HOOK_READY` False changes nothing for any project until the hook script reads the arguments)."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import transitional_config  # noqa: E402
from test_skillsync_guard_hook import GuardBase, HOOK  # noqa: E402
from skillsync_workflow import guardhook  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CLASSIFIER = ROOT / 'library' / 'hooks' / 'guard-tests.py'
TABLE = ROOT / 'skillsync_workflow' / 'data' / 'test_tools.json'


class Settings(unittest.TestCase):
    def test_defaults_and_values(self):
        self.assertEqual(guardhook.test_settings({}), (None, [], 25, []))
        self.assertEqual(guardhook.test_settings({'hooks': {'guard_tests': 'warn', 'test_wrappers': ['my-runner run '], 'wide_modules': 10}}), ('warn', ['my-runner run'], 10, []))
        self.assertEqual(guardhook.test_settings({'hooks': {'guard_tests': False}})[0], 'off')
        self.assertEqual(guardhook.test_settings({'hooks': {'guard_tests': True}})[0], 'warn')

    def test_invalid_values_are_problems_and_never_switch_it_on(self):
        self.assertTrue(guardhook.test_settings({'hooks': {'test_wrappers': ["it's open"]}})[3], 'an open quote in a prefix is a problem, not a silent failure')
        for hooks in ({'guard_tests': 'maybe'}, {'guard_tests': 3}, {'test_wrappers': 'x'}, {'test_wrappers': ['']}, {'test_wrappers': [3]}, {'wide_modules': 1}, {'wide_modules': 'many'},
                      {'wide_modules': True}):
            with self.subTest(hooks=hooks):
                self.assertTrue(guardhook.test_settings({'hooks': hooks})[3])
        self.assertEqual(guardhook.effective_test_mode({'hooks': {'guard_tests': 'maybe'}}, ['feature']), 'off')

    def test_the_default_depends_on_the_agent_flow(self):
        self.assertEqual(guardhook.effective_test_mode({}, ['feature']), 'warn')
        self.assertEqual(guardhook.effective_test_mode({}, ['dispatch']), 'warn')
        self.assertEqual(guardhook.effective_test_mode({}, ['plain']), 'off')
        self.assertEqual(guardhook.effective_test_mode({'hooks': {'guard_tests': 'off'}}, ['feature']), 'off')
        self.assertEqual(guardhook.effective_test_mode({'hooks': {'guard_tests': 'warn'}}, ['plain']), 'warn')

    def test_the_part_is_on_and_can_be_switched_off_by_the_constant(self):
        self.assertTrue(guardhook.TESTS_HOOK_READY)
        self.assertEqual(guardhook.tests_config({}, ['feature']), {'wide': 25, 'wrappers': []})
        self.assertIsNone(guardhook.tests_config({'hooks': {'guard_tests': 'off'}}, ['feature']))
        with mock.patch.object(guardhook, 'TESTS_HOOK_READY', False):
            self.assertIsNone(guardhook.tests_config({}, ['feature']))

    def test_the_command_round_trip(self):
        tests = {'wide': 12, 'wrappers': ['my-runner run', 'other']}
        command = guardhook.hook_command('python3', 'deny', ['dist'], tests)
        self.assertIn('--tests warn --wide-modules 12', command)
        self.assertEqual(guardhook.command_tests(command), tests)
        self.assertEqual(guardhook.command_args(command), ('deny', ['dist']))
        self.assertIsNone(guardhook.command_tests(guardhook.hook_command('python3', 'deny', [])))

    def test_without_the_test_part_the_command_is_byte_for_byte_the_old_one(self):
        self.assertEqual(guardhook.hook_command('python3', 'deny', ['x']), guardhook.hook_command('python3', 'deny', ['x'], None))
        self.assertNotIn('--tests', guardhook.hook_command('python3', 'deny', ['x']))


class DefaultLink(GuardBase):
    """`link` of a project with the agent flow: the test part is part of the default hook."""

    def setUp(self):
        super().setUp()
        (self.library / 'hooks/guard-tests.py').write_bytes(CLASSIFIER.read_bytes())

    def test_a_feature_project_gets_the_test_part(self):
        self.link()
        entry = self.guard_entries(self.local())[0]['command']
        self.assertIn('--tests warn --wide-modules 25', entry)
        self.assertTrue((self.root / guardhook.TESTS_HOOK).is_file())
        self.assertTrue((self.root / guardhook.TOOLS_HOOK).is_file())

    def test_hooks_guard_tests_off_keeps_only_the_delete_guard(self):
        self.set_hooks(guard_tests='off')
        self.link()
        self.assertNotIn('--tests', self.guard_entries(self.local())[0]['command'])
        self.assertFalse((self.root / guardhook.TESTS_HOOK).exists())

    def test_a_bad_value_is_reported_by_link(self):
        self.set_hooks(guard_tests='maybe')
        result = self.link()
        self.assertIn('hooks.guard_tests', result.stdout + result.stderr)
        self.assertNotIn('--tests', self.guard_entries(self.local())[0]['command'])


class Active(GuardBase):
    """`link` of a project with the agent flow and the classifier in the library."""

    def setUp(self):
        super().setUp()
        (self.library / 'hooks/guard-tests.py').write_bytes(CLASSIFIER.read_bytes())

    def sync(self, dry_run=False):
        result = GuardBase.link(self, *(['--dry-run'] if dry_run else []))
        manifest = yaml.safe_load((self.root / '.github/.skillsync.yaml').read_text(encoding='utf-8'))
        return result, manifest

    def command(self):
        return self.guard_entries(self.local())[0]['command']

    def test_a_feature_project_gets_the_classifier_the_table_and_the_arguments(self):
        self.sync()
        self.assertEqual((self.root / guardhook.TESTS_HOOK).read_bytes(), CLASSIFIER.read_bytes())
        self.assertEqual((self.root / guardhook.TOOLS_HOOK).read_bytes(), TABLE.read_bytes())
        self.assertEqual(guardhook.command_tests(self.command()), {'wide': 25, 'wrappers': []})
        self.assertEqual(guardhook.command_args(self.command()), ('deny', []))

    def test_the_manifest_settings_reach_the_entry(self):
        self.set_hooks(wide_modules=10, test_wrappers=['my-runner run'])
        self.sync()
        self.assertEqual(guardhook.command_tests(self.command()), {'wide': 10, 'wrappers': ['my-runner run']})

    def test_it_is_idempotent_and_the_second_sync_writes_nothing(self):
        self.sync()
        first = self.tree()
        self.sync()
        self.assertEqual(self.tree(), first)

    def test_switching_the_test_part_off_removes_its_files_and_arguments_but_keeps_the_delete_guard(self):
        self.sync()
        self.set_hooks(guard_tests='off')
        self.sync()
        self.assertFalse((self.root / guardhook.TESTS_HOOK).exists())
        self.assertFalse((self.root / guardhook.TOOLS_HOOK).exists())
        self.assertTrue((self.root / guardhook.GUARD_HOOK).exists())
        self.assertNotIn('--tests', self.command())
        self.assertIn('--mode deny', self.command())

    def test_the_tests_part_alone_keeps_the_entry_when_the_delete_guard_is_off(self):
        self.set_hooks(guard_delete='off', guard_tests='warn')
        self.sync()
        self.assertEqual(guardhook.command_args(self.command())[0], 'off')
        self.assertEqual(guardhook.command_tests(self.command()), {'wide': 25, 'wrappers': []})
        self.assertTrue((self.root / guardhook.GUARD_HOOK).exists())

    def test_both_off_removes_everything(self):
        self.sync()
        self.set_hooks(guard_delete='off', guard_tests='off')
        self.sync()
        for name in (guardhook.GUARD_HOOK, guardhook.TESTS_HOOK, guardhook.TOOLS_HOOK):
            self.assertFalse((self.root / name).exists(), name)
        self.assertEqual(self.guard_entries(self.local()), [])

    def test_a_missing_classifier_in_the_library_leaves_the_delete_guard_working(self):
        (self.library / 'hooks/guard-tests.py').unlink()
        self.sync()
        self.assertTrue((self.root / guardhook.GUARD_HOOK).exists())
        self.assertFalse((self.root / guardhook.TESTS_HOOK).exists())

    def test_dry_run_writes_nothing_and_says_what_it_would_write(self):
        before = self.tree()
        result, _ = self.sync(dry_run=True)
        self.assertEqual(self.tree(), before)
        self.assertIn('guard-tests.py', result.stdout)
        self.assertIn('test-tools.json', result.stdout)

    def test_the_gitignore_names_the_new_files(self):
        self.sync()
        text = (self.root / '.claude/.gitignore').read_text(encoding='utf-8')
        self.assertIn('hooks/guard-tests.py', text)
        self.assertIn('hooks/test-tools.json', text)

    def test_a_plain_project_is_untouched(self):
        self.manifest['skills'] = ['plain']
        self.save()
        self.sync()
        for name in (guardhook.GUARD_HOOK, guardhook.TESTS_HOOK, guardhook.TOOLS_HOOK):
            self.assertFalse((self.root / name).exists(), name)

    def test_doctor_reports_the_state(self):
        _, manifest = self.sync()
        skills = ['feature']
        info = [item for item in guardhook.doctor_findings(self.root, manifest, skills) if item[0] == 'info']
        self.assertTrue(info and 'test commands: warn' in info[0][2], info)
        (self.root / guardhook.TOOLS_HOOK).unlink()
        warned = [item[2] for item in guardhook.doctor_findings(self.root, manifest, skills) if item[0] == 'warning']
        self.assertTrue(any('missing' in text for text in warned), warned)
        manifest['hooks'] = {'guard_tests': 'off'}
        warned = [item[2] for item in guardhook.doctor_findings(self.root, manifest, skills) if item[0] == 'warning']
        self.assertTrue(any('differs' in text for text in warned), warned)


class PlainProjectUnchanged(GuardBase):
    """The promise: a project without the agent flow does not change by a byte. Checked on the generated artefacts of a real `link`, not on a function."""

    def setUp(self):
        super().setUp()
        (self.library / 'hooks/guard-tests.py').write_bytes(CLASSIFIER.read_bytes())
        self.manifest['skills'] = ['plain']
        self.save()

    def test_default_and_explicitly_off_give_the_same_tree_and_nothing_of_the_hook(self):
        self.link()
        default = self.tree()
        self.set_hooks(guard_tests='off')
        self.link()
        self.assertEqual(self.tree(), {**default, '.github/.skillsync.yaml': self.tree()['.github/.skillsync.yaml']})
        for rel in default:
            self.assertNotIn('test-tools', rel)
            self.assertNotIn('guard-tests', rel)
            self.assertNotIn('guard-commands', rel)
        for name in (guardhook.GUARD_HOOK, guardhook.TESTS_HOOK, guardhook.TOOLS_HOOK):
            self.assertFalse((self.root / name).exists(), name)
        gitignore = self.root / '.claude/.gitignore'
        if gitignore.exists():
            text = gitignore.read_text(encoding='utf-8')
            for word in ('guard-commands', 'guard-tests', 'test-tools'):
                self.assertNotIn(word, text)
        if (self.root / '.claude/settings.local.json').exists():
            self.assertNotIn('PreToolUse', (self.root / '.claude/settings.local.json').read_text(encoding='utf-8'))

    def test_a_flow_project_with_the_test_part_off_has_none_of_its_artefacts(self):
        self.manifest['skills'] = ['feature']
        self.save()
        self.set_hooks(guard_tests='off')
        self.link()
        for name in (guardhook.TESTS_HOOK, guardhook.TOOLS_HOOK):
            self.assertFalse((self.root / name).exists(), name)
        text = (self.root / '.claude/.gitignore').read_text(encoding='utf-8')
        self.assertIn('hooks/guard-commands.py', text)
        self.assertNotIn('guard-tests', text)
        self.assertNotIn('test-tools', text)
        self.assertNotIn('--tests', self.guard_entries(self.local())[0]['command'])


if __name__ == '__main__':
    unittest.main()
