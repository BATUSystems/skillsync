"""`discover_exclude` of the machine config in the sync commands without a path (`discover_manifests`, `link`,
`instructions`): projects equal to or under an excluded path are skipped; no key or an empty list changes nothing;
a bad value is a fixed message without a path (exit 2, no traceback). All names are synthetic; machine state is
isolated (layers_helpers)."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(ROOT))

from layers_helpers import SKILL_TEXT, LayersCase, needs_os_symlinks, tree_snapshot, write, yaml_text  # noqa: E402
from cli_module import skillsync  # noqa: E402
from skillsync_workflow import common, retro  # noqa: E402

MANIFEST = '.github/.skillsync.yaml'
BAD_MESSAGE = 'Konfiguracja maszyny: discover_exclude musi być listą niepustych ścieżek'


def manifest_at(base, rel):
    path = Path(base) / rel / MANIFEST
    write(path, '')
    return path


class DiscoverManifestsTest(LayersCase):
    def setUp(self):
        super().setUp()
        self.repos = self.work / 'x'
        self.mine = manifest_at(self.repos, 'mine')
        self.arch = manifest_at(self.repos, 'arch')
        self.arch_deep = manifest_at(self.repos, 'arch/sub/deep')
        self.arch2 = manifest_at(self.repos, 'archive2')

    def found(self, **config):
        config.setdefault('discover_paths', [str(self.repos)])
        return skillsync.discover_manifests(config)

    def everything(self):
        return sorted([self.mine, self.arch, self.arch_deep, self.arch2])

    def test_no_key_and_empty_list_return_all_manifests(self):
        self.assertEqual(self.found(), self.everything())
        self.assertEqual(self.found(discover_exclude=[]), self.everything())
        self.assertEqual(self.found(discover_exclude=None), self.everything())

    def test_excluded_path_and_everything_under_it_are_skipped(self):
        self.assertEqual(self.found(discover_exclude=[str(self.repos / 'arch')]), sorted([self.mine, self.arch2]))

    def test_excluded_parent_directory_skips_all_projects_under_it(self):
        self.assertEqual(self.found(discover_exclude=[str(self.repos)]), [])

    def test_project_equal_to_the_exclusion_is_skipped(self):
        self.assertEqual(self.found(discover_exclude=[str(self.repos / 'mine')]),
                         sorted([self.arch, self.arch_deep, self.arch2]))

    def test_name_prefix_is_not_a_match(self):
        result = self.found(discover_exclude=[str(self.repos / 'arch')])
        self.assertIn(self.arch2, result)

    def test_tilde_is_expanded(self):
        with mock.patch.dict(os.environ, {'HOME': str(self.work)}):
            result = self.found(discover_exclude=['~/x/arch'])
        self.assertEqual(result, sorted([self.mine, self.arch2]))

    @needs_os_symlinks
    def test_exclusion_given_as_a_symlink_to_the_project_tree(self):
        alias = self.work / 'alias-to-arch'
        alias.symlink_to(self.repos / 'arch', target_is_directory=True)
        self.assertEqual(self.found(discover_exclude=[str(alias)]), sorted([self.mine, self.arch2]))

    @needs_os_symlinks
    def test_project_reached_through_a_symlinked_discover_root(self):
        alias = self.work / 'alias-root'
        alias.symlink_to(self.repos / 'arch', target_is_directory=True)
        result = skillsync.discover_manifests({'discover_paths': [str(alias)], 'discover_exclude': [str(self.repos / 'arch')]})
        self.assertEqual(result, [])
        self.assertTrue(skillsync.discover_manifests({'discover_paths': [str(alias)]}))

    def test_bad_type_is_a_fixed_message_without_the_value(self):
        secret = str(self.work / 'secret-name')
        for value in (secret, {'a': secret}, [secret, 5], [''], ['   ']):
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                self.found(discover_exclude=value)
            self.assertEqual(str(caught.exception), BAD_MESSAGE)
            self.assertNotIn('secret-name', str(caught.exception))


class HelperLocationTest(unittest.TestCase):
    def test_retro_uses_the_common_helper(self):
        self.assertIs(retro.discover_excludes, common.discover_excludes)
        self.assertIs(retro.is_excluded, common.is_excluded)
        self.assertIs(skillsync.discover_excludes, common.discover_excludes)


class SyncCommandsTest(LayersCase):
    """`link` / `instructions` without a path on an isolated machine config with two projects."""

    def setUp(self):
        super().setUp()
        self.core = self.layer_dir('core', 'core', files={'skills/feature/SKILL.md': SKILL_TEXT.format('feature')})
        self.repos = self.work / 'repos'
        self.kept = self.project_in('kept')
        self.skipped = self.project_in('skipped')

    def project_in(self, name):
        path = self.repos / name
        write(path / MANIFEST, yaml_text({'skills': ['feature'], 'mcp': False}))
        return path

    def configure(self, **extra):
        self.write_config([('core', 'core', self.core)], [(['**'], ['core'])],
                          discover_paths=[str(self.repos)], **extra)

    def generated(self, project):
        return sorted(str(p.relative_to(project)) for p in project.rglob('*')
                      if (p.is_file() or p.is_symlink()) and p.name != '.skillsync.yaml')

    def test_link_and_instructions_leave_the_excluded_project_untouched(self):
        for command in ('link', 'instructions'):
            with self.subTest(command):
                self.configure(discover_exclude=[str(self.skipped)])
                process = self.cli(command)
                self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
                self.assertTrue(self.generated(self.kept), process.stdout)
                self.assertEqual(self.generated(self.skipped), [], process.stdout)

    def test_without_the_key_both_projects_are_handled(self):
        for command in ('link', 'instructions'):
            with self.subTest(command):
                self.configure()
                process = self.cli(command)
                self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
                self.assertTrue(self.generated(self.kept), process.stdout)
                self.assertTrue(self.generated(self.skipped), process.stdout)

    def test_link_with_an_explicit_path_still_handles_an_excluded_project(self):
        self.configure(discover_exclude=[str(self.skipped)])
        process = self.cli('link', str(self.skipped))
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertTrue(self.generated(self.skipped), process.stdout)
        self.assertEqual(self.generated(self.kept), [])

    def push_dry_run(self, **config):
        self.configure(**config)
        for project in (self.kept, self.skipped):   # a local copy that differs from the layer: push has something to plan
            write(project / 'AGENTS.md', 'local edit\n')
        before = {name: tree_snapshot(path) for name, path in
                  (('core', self.core), ('kept', self.kept), ('skipped', self.skipped))}
        process = self.cli('push', '--dry-run')
        after = {name: tree_snapshot(path) for name, path in
                 (('core', self.core), ('kept', self.kept), ('skipped', self.skipped))}
        self.assertEqual(after, before, 'push --dry-run changed files')
        return process.returncode, process.stdout + process.stderr

    def test_push_dry_run_without_a_path_skips_the_excluded_project(self):
        _, text = self.push_dry_run(discover_exclude=[str(self.skipped)])
        self.assertIn(str(self.kept), text)
        self.assertNotIn(str(self.skipped), text)

    def test_push_dry_run_without_the_key_reports_both_projects(self):
        _, text = self.push_dry_run()
        self.assertIn(str(self.kept), text)
        self.assertIn(str(self.skipped), text)

    def test_bad_value_stops_the_command_with_a_fixed_message(self):
        secret = str(self.work / 'secret-name')
        for command in ('link', 'instructions'):
            with self.subTest(command):
                self.configure(discover_exclude=secret)
                process = self.cli(command)
                self.assertEqual(process.returncode, 2, process.stdout)
                self.assertNotIn('Traceback', process.stderr)
                self.assertIn(BAD_MESSAGE, process.stderr)
                self.assertNotIn('secret-name', process.stdout + process.stderr)
                self.assertEqual(self.generated(self.kept), [])


if __name__ == '__main__':
    unittest.main()
