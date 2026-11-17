"""`scripts/merge_modules.py`: the test set for a merge, computed from the diff, with a reason per module.

The three regressions that motivated it are rebuilt as small synthetic repos (not from history): a changed doctor message
asserted literally by a test that never imports the module, a changed `status.py` reached by a test only through other modules,
and a new output field mirrored by `expected*.json` fixtures read by a test without any import of the product.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import sys as _sys_tmp_paths  # noqa: E402
from pathlib import Path as _Path_tmp_paths  # noqa: E402
_sys_tmp_paths.path.insert(0, str(_Path_tmp_paths(__file__).resolve().parent))  # noqa: E402
from tmp_paths import descend  # noqa: E402
SCRIPT = ROOT / 'scripts' / 'merge_modules.py'


def run_git(repo, *args):
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', '-c', 'commit.gpgsign=false', *args],
                   check=True, capture_output=True, text=True)


class Repo:
    def __init__(self, test):
        self.dir = Path(tempfile.mkdtemp(prefix='skillsync-mergemods-'))
        test.addCleanup(__import__('shutil').rmtree, self.dir, ignore_errors=True)
        run_git(self.dir, 'init', '-q')

    def write(self, files):
        for name, text in files.items():
            path = descend(self.dir, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')

    def commit(self, message='c'):
        run_git(self.dir, 'add', '-A')
        run_git(self.dir, 'commit', '-q', '-m', message)
        return subprocess.run(['git', '-C', str(self.dir), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()

    def select(self, base, head='HEAD', *extra):
        done = subprocess.run([sys.executable, str(SCRIPT), '--repo', str(self.dir), '--base', base, '--head', head, '--json', *extra],
                              capture_output=True, text=True)
        return done.returncode, (json.loads(done.stdout) if done.stdout.strip() else None), done.stderr


def modules(result):
    return {item['module']: item['reasons'] for item in result['modules']}


BASE = {
    'skillsync_workflow/__init__.py': '',
    'skillsync_workflow/common.py': 'X = 1\n',
    'skillsync_workflow/doctor.py': "MESSAGE = 'Missing installed skill'\n",
    'skillsync_workflow/status.py': 'VALUE = 1\n',
    'skillsync_workflow/cli.py': 'from . import status\n',
    'skillsync_workflow/delivery.py': "def out():\n    return {'mode': 'pr'}\n",
    'tests/__init__.py': '',
    'tests/test_skillsync_dispatch.py': "import subprocess\n\n\ndef test():\n    out = subprocess.run(['skillsync', 'doctor'])\n    assert 'Missing installed skill' in out\n",
    'tests/test_skillsync_ide_contract.py': 'from skillsync_workflow import cli\n',
    'tests/test_skillsync_status_fixtures.py': "import json\nEXPECTED = 'fixtures/status/expected.json'\n",
    'tests/fixtures/status/expected.json': '{"mode": "pr"}\n',
    'tests/test_skillsync_unrelated.py': "def test():\n    assert 1 + 1 == 2\n",
    'tests/test_skillsync_repo_hygiene.py': '',
    'tests/test_skillsync_duplicate_defs.py': '',
    'tests/test_skillsync_no_silent_return.py': '',
    'tests/test_skillsync_packaging.py': '',
}


class MergeModules(unittest.TestCase):
    def setUp(self):
        self.repo = Repo(self)
        self.repo.write(BASE)
        self.base = self.repo.commit('base')

    def test_changed_message_found_by_literal_without_import(self):
        self.repo.write({'skillsync_workflow/doctor.py': "MESSAGE = 'Missing installed skill: run link'\n"})
        self.repo.commit()
        code, result, _ = self.repo.select(self.base)
        found = modules(result)
        self.assertEqual(code, 0)
        self.assertIn('tests.test_skillsync_dispatch', found)
        self.assertTrue(any(reason.startswith('literal:') for reason in found['tests.test_skillsync_dispatch']))
        self.assertNotIn('tests.test_skillsync_unrelated', found)

    def test_generator_or_library_change_selects_the_tests_that_compare_the_link_artefacts(self):
        """180-O: these tests run `skillsync link` through a subprocess and name no product file, so only this rule selects them."""
        for changed in ('library/hooks/guard-commands.py', 'skillsync_workflow/cli.py', 'skillsync_workflow/guardhook.py', 'skillsync_workflow/selfcmd.py',
                        'library/skills/x/SKILL.md', 'library/instructions/_base.md'):
            with self.subTest(changed=changed):
                repo = Repo(self)
                repo.write(BASE)
                repo.write({'tests/test_skillsync_layers_equivalence.py': "def test():\n    pass\n", 'tests/test_skillsync_worktree_links.py': "def test():\n    pass\n",
                            changed: 'A = 1\n'})
                base = repo.commit('with the artefact tests')
                repo.write({changed: 'A = 2\n'})
                repo.commit()
                code, result, _ = repo.select(base)
                found = modules(result)
                self.assertEqual(code, 0)
                for name in ('tests.test_skillsync_layers_equivalence', 'tests.test_skillsync_worktree_links'):
                    self.assertTrue(any(reason.startswith('artefact:') for reason in found.get(name, [])), found)
                self.assertNotIn('tests.test_skillsync_unrelated', found)

    def test_a_deleted_artefact_source_selects_nothing_by_this_rule(self):
        self.repo.write({'tests/test_skillsync_layers_equivalence.py': "def test():\n    pass\n", 'library/hooks/old.py': 'A = 1\n'})
        base = self.repo.commit('with the artefact test and a hook')
        (self.repo.dir / 'library/hooks/old.py').unlink()
        self.repo.commit('delete the hook')
        _, result, _ = self.repo.select(base)
        found = modules(result)
        self.assertFalse(any(reason.startswith('artefact:') for reason in found.get('tests.test_skillsync_layers_equivalence', [])), found)

    def test_a_change_outside_the_artefact_sources_leaves_the_artefact_tests_out(self):
        self.repo.write({'tests/test_skillsync_layers_equivalence.py': "def test():\n    pass\n"})
        base = self.repo.commit('with the artefact test')
        self.repo.write({'skillsync_workflow/status.py': 'VALUE = 2\n'})
        self.repo.commit()
        _, result, _ = self.repo.select(base)
        self.assertNotIn('tests.test_skillsync_layers_equivalence', modules(result))

    def engine_run(self, base, env_value):
        env = {key: value for key, value in os.environ.items() if key != 'SKILLSYNC_PROTOTYPE_ENGINE_V2_SOURCE'}
        if env_value:
            env['SKILLSYNC_PROTOTYPE_ENGINE_V2_SOURCE'] = env_value
        done = subprocess.run([sys.executable, str(SCRIPT), '--repo', str(self.repo.dir), '--base', base, '--head', 'HEAD', '--json'], capture_output=True, text=True, env=env)
        return done.returncode, modules(json.loads(done.stdout)), done.stderr

    def test_a_change_of_the_engine_selects_the_freshness_test_and_warns_without_the_variable(self):
        """180-AA: the library source and the copy of the engine are not named by the test, so only this rule selects it."""
        for changed in ('library/skills/prototype/assets/repo/prototype.js', 'tests/fixtures/prototype/engine-v2/prototype.js'):
            with self.subTest(changed=changed):
                repo = Repo(self)
                repo.write(BASE)
                repo.write({'tests/test_skillsync_prototype_engine.py': "def test():\n    pass\n", changed: 'A = 1\n'})
                base = repo.commit('with the engine test')
                repo.write({changed: 'A = 2\n'})
                repo.commit()
                self.repo = repo
                code, found, stderr = self.engine_run(base, '')
                self.assertEqual(code, 0)
                self.assertTrue(any(reason.startswith('engine:') for reason in found.get('tests.test_skillsync_prototype_engine', [])), found)
                self.assertIn('WARNING', stderr)
                self.assertIn('SKILLSYNC_PROTOTYPE_ENGINE_V2_SOURCE', stderr)
                _, _, quiet = self.engine_run(base, '/x/prototype.js')
                self.assertNotIn('WARNING', quiet)

    def test_a_change_elsewhere_leaves_the_engine_test_and_the_warning_out(self):
        self.repo.write({'tests/test_skillsync_prototype_engine.py': "def test():\n    pass\n"})
        base = self.repo.commit('with the engine test')
        self.repo.write({'skillsync_workflow/status.py': 'VALUE = 3\n'})
        self.repo.commit()
        code, found, stderr = self.engine_run(base, '')
        self.assertEqual(code, 0)
        self.assertFalse(any(reason.startswith('engine:') for reason in found.get('tests.test_skillsync_prototype_engine', [])), found)
        self.assertNotIn('WARNING', stderr)

    def test_a_deleted_engine_source_selects_nothing_by_the_engine_rule(self):
        self.repo.write({'tests/test_skillsync_prototype_engine.py': "def test():\n    pass\n", 'library/skills/prototype/assets/repo/prototype.js': 'A = 1\n'})
        base = self.repo.commit('with the engine')
        (self.repo.dir / 'library/skills/prototype/assets/repo/prototype.js').unlink()
        self.repo.commit('delete')
        _, found, _ = self.engine_run(base, '')
        self.assertFalse(any(reason.startswith('engine:') for reason in found.get('tests.test_skillsync_prototype_engine', [])), found)

    def test_changed_status_reaches_test_through_another_module(self):
        self.repo.write({'skillsync_workflow/status.py': 'VALUE = 2\n'})
        self.repo.commit()
        _, result, _ = self.repo.select(self.base)
        found = modules(result)
        self.assertIn('tests.test_skillsync_ide_contract', found)
        reason = found['tests.test_skillsync_ide_contract'][0]
        self.assertTrue(reason.startswith('import:') and 'skillsync_workflow.cli' in reason and reason.endswith('skillsync_workflow.status'), reason)
        self.assertNotIn('tests.test_skillsync_unrelated', found)

    def test_new_output_field_selects_test_reading_expected_fixtures(self):
        self.repo.write({'skillsync_workflow/delivery.py': "def out():\n    return {'mode': 'pr', 'commits': []}\n"})
        self.repo.commit()
        _, result, _ = self.repo.select(self.base)
        found = modules(result)
        self.assertIn('tests.test_skillsync_status_fixtures', found)
        self.assertTrue(any(reason.startswith('expected:') for reason in found['tests.test_skillsync_status_fixtures']))
        self.assertNotIn('tests.test_skillsync_unrelated', found)

    def test_changed_fixture_selects_its_reader(self):
        self.repo.write({'tests/fixtures/status/expected.json': '{"mode": "direct"}\n'})
        self.repo.commit()
        _, result, _ = self.repo.select(self.base)
        found = modules(result)
        self.assertTrue(any(reason.startswith('fixture:') for reason in found['tests.test_skillsync_status_fixtures']))
        self.assertNotIn('tests.test_skillsync_unrelated', found)

    def test_changed_test_and_guards_always(self):
        self.repo.write({'tests/test_skillsync_unrelated.py': "def test():\n    assert 2 + 2 == 4\n"})
        self.repo.commit()
        _, result, _ = self.repo.select(self.base)
        found = modules(result)
        self.assertEqual(found['tests.test_skillsync_unrelated'], ['changed: test file changed'])
        self.assertEqual(found['tests.test_skillsync_repo_hygiene'], ['guard: fixed guard set'])
        self.assertEqual(found['tests.test_skillsync_packaging'], ['guard: fixed guard set'])
        for guard in ('duplicate_defs', 'no_silent_return'):   # the guards of the test files themselves (180-A5)
            self.assertEqual(found['tests.test_skillsync_' + guard], ['guard: fixed guard set'])

    def test_helper_change_selects_importers_and_path_rule(self):
        self.repo.write({'tests/helpers.py': 'X = 1\n', 'tests/test_skillsync_uses_helper.py': 'import helpers\n',
                         'tests/test_skillsync_runs_script.py': "P = ['skillsync_workflow', 'common.py']\n"})
        base = self.repo.commit('add')
        self.repo.write({'tests/helpers.py': 'X = 2\n', 'skillsync_workflow/common.py': 'X = 2\n'})
        self.repo.commit()
        _, result, _ = self.repo.select(base)
        found = modules(result)
        self.assertIn('tests.test_skillsync_uses_helper', found)
        self.assertTrue(any(reason.startswith('path:') for reason in found['tests.test_skillsync_runs_script']))

    def test_unparsable_test_is_selected_and_deleted_is_not(self):
        self.repo.write({'tests/test_skillsync_broken.py': 'def (:\n'})
        self.repo.commit()
        run_git(self.repo.dir, 'rm', '-q', 'tests/test_skillsync_unrelated.py')
        self.repo.commit('rm')
        _, result, _ = self.repo.select(self.base)
        found = modules(result)
        self.assertIn('unparsable: test file does not parse', found['tests.test_skillsync_broken'])
        self.assertNotIn('tests.test_skillsync_unrelated', found)

    def test_deleted_product_module_selects_its_importer(self):
        run_git(self.repo.dir, 'mv', 'skillsync_workflow/status.py', 'skillsync_workflow/state.py')
        self.repo.commit('rename')
        self.repo.write({'tests/test_skillsync_old_import.py': 'from skillsync_workflow import status\n'})
        base = self.base
        self.repo.commit('x')
        _, result, _ = self.repo.select(base)
        found = modules(result)
        self.assertTrue(any('imports deleted skillsync_workflow.status' in r for r in found['tests.test_skillsync_old_import']), found)
        self.assertNotIn('tests.test_skillsync_unrelated', found)

    def test_identifier_literals_and_scripts_do_not_select_the_suite(self):
        # a new script holding 'skillsync_workflow' and 'test_skillsync_x' strings (as merge_modules itself does) must not pull in
        # every test that mentions those words, nor the tests that read expected* fixtures
        self.repo.write({'scripts/tool.py': "DIRS = ('skillsync_workflow', 'test_skillsync_something')\nMSG = 'one two three'\n",
                         'tests/test_skillsync_mentions.py': "A = 'skillsync_workflow'\nB = 'test_skillsync_something'\nC = 'expected'\n"})
        self.repo.commit()
        _, result, _ = self.repo.select(self.base)
        found = modules(result)
        self.assertEqual(found['tests.test_skillsync_mentions'], ['changed: test file changed'])
        self.assertNotIn('tests.test_skillsync_status_fixtures', found)
        self.assertNotIn('tests.test_skillsync_dispatch', found)

    def test_deleted_module_reaches_untouched_test_through_another_module(self):
        run_git(self.repo.dir, 'rm', '-q', 'skillsync_workflow/status.py')
        self.repo.commit('rm status')
        _, result, _ = self.repo.select(self.base)
        found = modules(result)
        self.assertTrue(any('imports deleted skillsync_workflow.status' in r for r in found['tests.test_skillsync_ide_contract']), found)
        self.assertNotIn('tests.test_skillsync_unrelated', found)

    def test_deleted_bare_helper_selects_untouched_importer(self):
        self.repo.write({'tests/helpers.py': 'X = 1\n', 'tests/test_skillsync_uses_helper.py': 'import helpers\n'})
        base = self.repo.commit('add')
        run_git(self.repo.dir, 'rm', '-q', 'tests/helpers.py')
        self.repo.commit('rm')
        _, result, _ = self.repo.select(base)
        self.assertTrue(any('imports deleted' in r for r in modules(result)['tests.test_skillsync_uses_helper']))

    def test_checkout_must_be_head(self):
        run_git(self.repo.dir, 'checkout', '-q', '-b', 'feature')
        self.repo.write({'skillsync_workflow/status.py': 'VALUE = 3\n'})
        self.repo.commit('f')
        run_git(self.repo.dir, 'checkout', '-q', self.base)
        code, _, error = self.repo.select(self.base, 'feature')
        self.assertEqual(code, 2)
        self.assertIn('check out the merged tree', error)

    def test_package_init_relative_import_and_bare_fixture_helper(self):
        self.repo.write({'skillsync_workflow/__init__.py': 'from .status import VALUE\n',
                         'tests/fixtures/layers/make_baseline.py': 'X = 1\n',
                         'tests/test_skillsync_baseline.py': 'import make_baseline\n',
                         'tests/test_skillsync_sub_only.py': 'import skillsync_workflow.cli\n'})
        base = self.repo.commit('add')
        self.repo.write({'tests/fixtures/layers/make_baseline.py': 'X = 2\n', 'skillsync_workflow/status.py': 'VALUE = 9\n'})
        self.repo.commit()
        _, result, _ = self.repo.select(base)
        found = modules(result)
        self.assertIn('tests.test_skillsync_baseline', found)
        self.assertIn('tests.test_skillsync_sub_only', found)  # runs the package __init__, which imports status

    def test_unknown_revision_is_exit_2_and_text_output(self):
        code, _, error = self.repo.select('nonexistent')
        self.assertEqual(code, 2)
        self.assertIn('merge_modules:', error)
        done = subprocess.run([sys.executable, str(SCRIPT), '--repo', str(self.repo.dir), '--base', self.base, '--head', 'HEAD', '--names'], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0)
        self.assertIn('tests.test_skillsync_repo_hygiene', done.stdout.split())


if __name__ == '__main__':
    unittest.main()
