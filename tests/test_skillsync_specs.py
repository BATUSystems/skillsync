"""Klucz `workflow.specs` i wykrywanie reguły ignorowania: `specs_info`, walidacja, `doctor` (K1, K2, K3, K9)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow.specs import specs_info, specs_findings, validate_specs  # noqa: E402

GIT = shutil.which('git')
BOTH = '/docs/specs/\n/docs/plans/\n'


def git(repo, *args):
    subprocess.run([GIT, '-C', str(repo), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   env={**os.environ, 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'})


@unittest.skipUnless(GIT, 'brak gita')
class SpecsInfoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='specs-')
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init', '-q')

    def ignore(self, text, name='.gitignore'):
        (self.repo / name).write_text(text, encoding='utf-8')

    def info(self, **specs):
        return specs_info(self.repo, {'workflow': {'specs': specs}} if specs else {})

    def test_no_rule_no_key_is_tracked_default(self):
        self.assertEqual(self.info(), {'tracked': True, 'archive': None, 'source': 'default'})
        self.assertEqual(specs_info(self.repo, None)['source'], 'default')

    def test_rule_for_both_directories_means_untracked(self):
        self.ignore(BOTH)
        self.assertEqual(self.info(), {'tracked': False, 'archive': None, 'source': 'gitignore'})

    def test_rule_for_one_directory_is_default(self):
        for text in ('/docs/specs/\n', '/docs/plans/\n'):
            self.ignore(text)
            self.assertEqual(self.info()['source'], 'default', text)
            self.assertTrue(self.info()['tracked'])

    def test_negation_is_not_a_rule(self):
        self.ignore('/docs/specs/*\n!/docs/specs/x.md\n/docs/plans/\n')
        self.assertEqual(self.info()['source'], 'default')

    def test_info_exclude_does_not_count(self):
        (self.repo / '.git' / 'info').mkdir(exist_ok=True)
        (self.repo / '.git' / 'info' / 'exclude').write_text(BOTH, encoding='utf-8')
        self.assertEqual(self.info()['source'], 'default')

    def test_global_excludes_do_not_count(self):
        glob = Path(self.tmp.name) / 'global'
        glob.mkdir()
        (glob / '.gitignore').write_text(BOTH, encoding='utf-8')
        (glob / 'gitconfig').write_text(f'[core]\n\texcludesFile = {glob / ".gitignore"}\n', encoding='utf-8')
        xdg = Path(self.tmp.name) / 'xdg' / 'git'
        xdg.mkdir(parents=True)
        (xdg / 'ignore').write_text(BOTH, encoding='utf-8')
        env = {'GIT_CONFIG_GLOBAL': str(glob / 'gitconfig'), 'HOME': str(glob), 'XDG_CONFIG_HOME': str(xdg.parent)}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(self.info()['source'], 'default')

    def test_local_config_excludes_file_does_not_count(self):
        glob = Path(self.tmp.name) / 'local'
        glob.mkdir()
        (glob / '.gitignore').write_text(BOTH, encoding='utf-8')
        git(self.repo, 'config', 'core.excludesFile', str(glob / '.gitignore'))
        self.assertEqual(self.info()['source'], 'default')

    def test_nested_gitignore_counts(self):
        (self.repo / 'docs').mkdir()
        self.ignore('/specs/\n/plans/\n', name='docs/.gitignore')
        self.assertEqual(self.info()['source'], 'gitignore')

    def test_manifest_false_wins_without_rule(self):
        self.assertEqual(self.info(tracked=False), {'tracked': False, 'archive': None, 'source': 'manifest'})

    def test_manifest_true_without_rule(self):
        self.assertEqual(self.info(tracked=True), {'tracked': True, 'archive': None, 'source': 'manifest'})

    def test_manifest_false_with_rule(self):
        self.ignore(BOTH)
        self.assertEqual(self.info(tracked=False)['source'], 'manifest')
        self.assertFalse(self.info(tracked=False)['tracked'])

    def test_true_with_rule_is_invalid(self):
        self.ignore(BOTH)
        result = self.info(tracked=True)
        self.assertEqual((result['tracked'], result['source']), (None, 'invalid'))
        self.assertIn('error', result)
        self.assertEqual(self.info(tracked=True), result)

    def test_true_with_one_rule_is_valid(self):
        self.ignore('/docs/specs/\n')
        self.assertEqual(self.info(tracked=True)['source'], 'manifest')

    def test_archive_is_expanded_and_validated(self):
        with mock.patch.dict(os.environ, {'HOME': '/opt/fakehome'}):
            self.assertEqual(self.info(archive='~/store')['archive'], '/opt/fakehome/store')
        self.assertEqual(self.info(archive='/srv/store')['archive'], '/srv/store')

    def test_invalid_types(self):
        bad = [{'tracked': 'yes'}, {'tracked': 1}, {'tracked': None}, {'archive': ''}, {'archive': 5}, {'archive': 'relative/x'},
               {'archive': '~/a/../b'}, {'archive': '/a/../b'}, {'archive': ['x']}]
        for specs in bad:
            result = self.info(**specs)
            self.assertEqual((result['tracked'], result['archive'], result['source']), (None, None, 'invalid'), specs)
            self.assertIn('error', result)
            self.assertNotIn('relative', result['error'])
        for manifest in ({'workflow': {'specs': 'x'}}, {'workflow': {'specs': ['tracked']}}):
            self.assertEqual(specs_info(self.repo, manifest)['source'], 'invalid')

    def test_workflow_not_a_mapping_is_default(self):
        for manifest in ({'workflow': 'x'}, {'workflow': None}, {'workflow': {}}):
            self.assertEqual(specs_info(self.repo, manifest)['source'], 'default')

    def test_missing_git_does_not_raise(self):
        self.ignore(BOTH)
        with mock.patch('skillsync_workflow.specs.subprocess.run', side_effect=FileNotFoundError):
            self.assertEqual(self.info(), {'tracked': True, 'archive': None, 'source': 'default'})
            self.assertEqual(self.info(tracked=False)['source'], 'manifest')

    def test_not_a_repository_is_default(self):
        plain = Path(self.tmp.name) / 'plain'
        plain.mkdir()
        self.assertEqual(specs_info(plain, {})['source'], 'default')


@unittest.skipUnless(GIT, 'brak gita')
class SpecsFindingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='specs-')
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init', '-q')

    def levels(self, **specs):
        manifest = {'workflow': {'specs': specs}} if specs else {}
        return [(level, text) for level, _path, text in specs_findings(self.repo, manifest)]

    def test_silent_by_default_and_with_clean_rule(self):
        self.assertEqual(self.levels(), [])
        (self.repo / '.gitignore').write_text(BOTH, encoding='utf-8')
        self.assertEqual(self.levels(), [])

    def test_type_errors(self):
        for specs in ({'tracked': 'x'}, {'archive': ''}, {'archive': 'rel'}, {'archive': '~/../x'}):
            self.assertEqual([level for level, _ in self.levels(**specs)], ['error'], specs)
        for level, text in self.levels(tracked='secret-value'):
            self.assertNotIn('secret-value', text)

    def test_unknown_key_is_a_warning(self):
        self.assertEqual([level for level, _ in self.levels(other=1)], ['warning'])

    def test_absolute_archive_and_archive_with_tracked(self):
        self.assertEqual([level for level, _ in self.levels(archive='/srv/x')], ['warning', 'warning'])
        self.assertEqual([level for level, _ in self.levels(archive='~/x')], ['warning'])
        (self.repo / '.gitignore').write_text(BOTH, encoding='utf-8')
        self.assertEqual(self.levels(archive='~/x'), [])

    def test_false_without_rule_warns_with_gitignore_entries(self):
        found = self.levels(tracked=False)
        self.assertEqual([level for level, _ in found], ['warning'])
        self.assertIn('/docs/specs/', found[0][1])
        self.assertIn('/docs/plans/', found[0][1])

    def test_rule_for_one_directory_warns(self):
        (self.repo / '.gitignore').write_text('/docs/specs/\n', encoding='utf-8')
        self.assertEqual([level for level, _ in self.levels()], ['warning'])

    def test_true_with_both_rules_is_an_error(self):
        (self.repo / '.gitignore').write_text(BOTH, encoding='utf-8')
        self.assertEqual([level for level, _ in self.levels(tracked=True)], ['error'])

    def test_untracked_mode_with_tracked_files_warns_with_count_and_command(self):
        (self.repo / '.gitignore').write_text(BOTH, encoding='utf-8')
        for name in ('specs/a.md', 'plans/b.md'):
            path = self.repo / 'docs' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('x\n', encoding='utf-8')
        git(self.repo, 'add', '-f', '--', 'docs/specs/a.md', 'docs/plans/b.md')
        found = self.levels()
        self.assertEqual([level for level, _ in found], ['warning'])
        self.assertIn('2', found[0][1])
        self.assertIn('git', found[0][1])
        self.assertIn('rm --cached', found[0][1])
        self.assertIn('docs/specs/a.md', found[0][1])

    def test_tracked_mode_with_tracked_files_is_silent(self):
        path = self.repo / 'docs' / 'specs' / 'a.md'
        path.parent.mkdir(parents=True)
        path.write_text('x\n', encoding='utf-8')
        git(self.repo, 'add', '--', 'docs/specs/a.md')
        self.assertEqual(self.levels(), [])


class ValidateSpecsTests(unittest.TestCase):
    def test_valid_and_missing(self):
        for manifest in ({}, None, {'workflow': {}}, {'workflow': {'specs': {'tracked': False, 'archive': '~/x'}}}):
            self.assertEqual(validate_specs(manifest)[0], [], manifest)


if __name__ == '__main__':
    unittest.main()
