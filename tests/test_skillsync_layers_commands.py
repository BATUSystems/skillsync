"""Step 2.1t: commands `layers init|add|update|list`, the findings engine for hooks and
`.orphans` (K9, K4 hooks, K12 part) with exit codes and JSON `schema: 1`.

Step 2.4t appends `use`, `check`, the isolation tests (K10) and the target stub fill (`migrate`: test_skillsync_layers_migrate.py). Sources of
layers are local bare repositories in a temporary directory (no network); every run is
isolated (layers_helpers). Client names are synthetic and composed at runtime.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from layers_helpers import (LINK_MODES, LayersCase, commit, generator_version, git, link_mode,  # noqa: E402
                            machine_cache_dir, machine_data_dir, make_layer, write, yaml_text)
from skillsync_workflow import layers, leakguard  # noqa: E402
from skillsync_workflow.selfcmd import self_argv  # noqa: E402

CLIENT_BETA = 'Brenk' + 'or'


def codes(findings):
    return {item['code'] for item in findings}


def by_code(findings, code):
    return [item for item in findings if item['code'] == code]


class CommandsCase(LayersCase):
    def config(self):
        return yaml.safe_load(self.config_file.read_text(encoding='utf-8')) or {}

    def default_checkout(self, layer_id):
        return machine_data_dir() / 'layers' / layer_id

    def head(self, path):
        return git(path, 'rev-parse', 'HEAD').stdout.strip()

    def detached(self, path):
        return git(path, 'symbolic-ref', '-q', 'HEAD', check=False).returncode != 0

    def add(self, layer_id, kind, source, *extra):
        return self.cli_json('layers', 'add', layer_id, '--kind', kind, '--source', str(source), *extra)

    def layer_findings(self, project_layers, **kwargs):
        self.write_config([(lid, kind, path) for lid, kind, path in project_layers],
                          [(['**'], [item[0] for item in project_layers])])
        return layers.check_layers(layers.resolve_layers(self.project('p')), **kwargs)


# ====================================================================== hooks and orphans in the findings engine


class HookCommandMissingTest(CommandsCase):
    """K8: a hook whose interpreter or wrapper no longer exists is `hook-command-missing` (error, FAIL)."""

    def setUp(self):
        super().setUp()
        self.core = self.layer_dir('core', 'core', version=generator_version())
        self.path = self.git_layer('acme', 'org')
        self.assertEqual(leakguard.install_hook(self.path, target='acme')['status'], 'PASS')
        self.hooks = self.path / '.git' / 'hooks'

    def findings(self, **kwargs):
        return self.layer_findings([('core', 'core', self.core), ('acme', 'org', self.path)], **kwargs)

    def broken(self, findings):
        return by_code(findings, 'hook-command-missing')

    def rewrite(self, name, old, new):
        text = (self.hooks / name).read_text(encoding='utf-8')
        self.assertIn(old, text)
        (self.hooks / name).write_text(text.replace(old, new), encoding='utf-8')

    def test_a_working_hook_has_no_command_finding(self):
        findings = self.findings()
        self.assertEqual(self.broken(findings), [])
        self.assertEqual(layers.findings_status(findings), 'PASS')

    def test_missing_interpreter_is_an_error_with_the_repair_command(self):
        self.rewrite('pre-commit', shlex.quote(Path(sys.executable).as_posix()), '/nonexistent/venv/bin/python')
        found = self.broken(self.findings())
        self.assertEqual([(item['severity'], item['layer']) for item in found], [('error', 'acme')])
        self.assertIn('pre-commit', found[0]['message'])
        self.assertIn('interpreter', found[0]['message'])
        self.assertIn('skillsync leakguard install-hook', found[0]['message'])
        self.assertIn('skillsync layers add', found[0]['message'])
        self.assertEqual(layers.findings_status(self.findings()), 'FAIL')

    def test_missing_wrapper_file_is_reported_for_each_hook(self):
        wrapper = self_argv()[1]
        for name in ('pre-commit', 'pre-push'):
            self.rewrite(name, shlex.quote(Path(wrapper).as_posix()), '/nonexistent/checkout/skillsync')
        found = self.broken(self.findings())
        self.assertEqual(sorted((item['message'].split(' hook')[0]) for item in found),
                         ['The pre-commit', 'The pre-push'])
        self.assertTrue(all('file' in item['message'] for item in found))

    def test_installation_form_with_a_missing_interpreter_is_found(self):
        for name in ('pre-commit', 'pre-push'):
            text = (self.hooks / name).read_text(encoding='utf-8')
            wrapper = shlex.quote(Path(self_argv()[1]).as_posix())
            (self.hooks / name).write_text(text.replace(wrapper, '-P -m skillsync_workflow'), encoding='utf-8')
        self.assertEqual(self.broken(self.findings()), [])             # installation form, interpreter exists
        for name in ('pre-commit', 'pre-push'):
            self.rewrite(name, shlex.quote(Path(sys.executable).as_posix()), '/gone/uv/tools/skillsync/bin/python')
        self.assertEqual(len(self.broken(self.findings())), 2)

    def test_a_bare_command_missing_from_path_is_found_and_a_present_one_is_not(self):
        bin_dir = self.path.parent / 'path-bin'
        bin_dir.mkdir()
        full_path = os.pathsep.join([str(bin_dir), os.environ.get('PATH', '')])      # git stays reachable
        write(self.hooks / 'pre-commit', '#!/bin/sh\nskillsync.py leakcheck "$PWD" --staged || exit $?\n', executable=True)
        with unittest.mock.patch.dict(os.environ, {'PATH': full_path}):
            found = self.broken(self.findings())
        self.assertEqual(len(found), 1)
        self.assertIn('pre-commit', found[0]['message'])
        write(bin_dir / 'skillsync.py', '#!/bin/sh\nexit 0\n', executable=True)
        with unittest.mock.patch.dict(os.environ, {'PATH': full_path}):
            self.assertEqual(self.broken(self.findings()), [])

    def test_a_hand_written_call_is_checked_too(self):
        write(self.hooks / 'pre-push', f'#!/bin/sh\n{shlex.quote(sys.executable)} /nonexistent/dev/skillsync leakcheck "$PWD" --range HEAD\n',
              executable=True)
        found = self.broken(self.findings())
        self.assertEqual(len(found), 1)
        self.assertIn('pre-push', found[0]['message'])

    def test_comments_and_foreign_commands_are_not_checked(self):
        write(self.hooks / 'pre-commit', '#!/bin/sh\n# /nonexistent/skillsync leakcheck\n/usr/bin/env true\n'
              + (self.hooks / 'pre-push').read_text(encoding='utf-8').split('\n', 1)[1], executable=True)
        self.assertEqual(self.broken(self.findings()), [])

    def test_legacy_blocks_with_a_missing_command_report_both(self):
        self.rewrite('pre-push', shlex.quote(Path(sys.executable).as_posix()), '/nonexistent/venv/bin/python')
        text = (self.hooks / 'pre-push').read_text(encoding='utf-8')
        (self.hooks / 'pre-push').write_text(text.replace('leakcheck', 'leakcheck --identity /x/id.yaml', 1), encoding='utf-8')
        codes_found = sorted(item['code'] for item in self.findings() if item['code'].startswith('hook-'))
        self.assertEqual(codes_found, ['hook-command-missing', 'hook-legacy'])

    def test_the_check_follows_the_hooks_switch(self):
        self.rewrite('pre-commit', shlex.quote(Path(sys.executable).as_posix()), '/nonexistent/venv/bin/python')
        self.assertEqual(self.broken(self.findings(hooks=False)), [])

    def test_problems_are_listed_without_values_of_other_words(self):
        self.rewrite('pre-commit', shlex.quote(Path(sys.executable).as_posix()), '/nonexistent/venv/bin/python')
        problems = leakguard.hook_command_problems(self.path)
        self.assertEqual(problems, [{'hook': 'pre-commit', 'kind': 'interpreter', 'path': '/nonexistent/venv/bin/python'}])
        self.assertEqual(leakguard.hook_command_problems(self.path / 'not-there'), [])


class HookMissingTest(CommandsCase):
    def setUp(self):
        super().setUp()
        self.core = self.layer_dir('core', 'core', version=generator_version())

    def findings(self, *layer_specs, **kwargs):
        return self.layer_findings([('core', 'core', self.core), *layer_specs], **kwargs)

    def test_a_writable_layer_without_hooks_is_an_error(self):
        for kind in ('org', 'personal'):
            with self.subTest(kind):
                path = self.git_layer(f'w-{kind}', kind)
                found = by_code(self.findings((f'w-{kind}', kind, path)), 'hook-missing')
                self.assertEqual([(item['severity'], item['layer']) for item in found], [('error', f'w-{kind}')])
                self.assertEqual(layers.findings_status(self.findings((f'w-{kind}', kind, path))), 'FAIL')

    def test_installed_hooks_clear_the_finding(self):
        path = self.git_layer('acme', 'org')
        self.assertEqual(leakguard.install_hook(path, target='acme')['status'], 'PASS')
        findings = self.findings(('acme', 'org', path))
        self.assertFalse([item for item in findings if item['code'].startswith('hook-')])
        self.assertEqual(layers.findings_status(findings), 'PASS')

    def test_read_only_layers_need_no_hooks(self):
        self.assertFalse(by_code(self.findings(), 'hook-missing'))                  # core is read-only by default
        path = self.git_layer('acme', 'org')
        self.write_config([('core', 'core', self.core), ('acme', 'org', path, {'writable': False})],
                          [(['**'], ['core', 'acme'])])
        findings = layers.check_layers(layers.resolve_layers(self.project('p')))
        self.assertFalse(by_code(findings, 'hook-missing'))

    def test_a_writable_layer_outside_git_is_an_error(self):
        path = self.layer_dir('acme', 'org')
        found = by_code(self.findings(('acme', 'org', path)), 'hook-missing')
        self.assertEqual([item['severity'] for item in found], [('error')])

    def test_one_missing_hook_is_enough(self):
        path = self.git_layer('acme', 'org')
        leakguard.install_hook(path, target='acme')
        (path / '.git' / 'hooks' / 'pre-push').unlink()
        found = by_code(self.findings(('acme', 'org', path)), 'hook-missing')
        self.assertEqual(len(found), 1)
        self.assertIn('(pre-push)', found[0]['message'])             # names exactly the missing hook

    def test_a_foreign_hook_without_leakcheck_does_not_count(self):
        path = self.git_layer('acme', 'org')
        for name in ('pre-commit', 'pre-push'):
            write(path / '.git' / 'hooks' / name, '#!/bin/sh\necho hello\n', executable=True)
        self.assertEqual(len(by_code(self.findings(('acme', 'org', path)), 'hook-missing')), 1)

    def test_hooks_check_can_be_switched_off(self):
        path = self.git_layer('acme', 'org')
        self.assertFalse(by_code(self.findings(('acme', 'org', path), hooks=False), 'hook-missing'))

    def test_shared_hooks_path_with_a_manual_leakcheck_call_is_accepted(self):
        path = self.git_layer('acme', 'org')
        shared = self.work / 'shared-hooks'
        shared.mkdir()
        git(path, 'config', 'core.hooksPath', str(shared))
        missing = by_code(self.findings(('acme', 'org', path)), 'hook-missing')
        self.assertEqual(len(missing), 1)
        self.assertIn('core.hooksPath', missing[0]['message'])                         # instruction, not silence
        for name in ('pre-commit', 'pre-push'):
            write(shared / name, '#!/bin/sh\nskillsync leakcheck "$(git rev-parse --show-toplevel)" --staged\n', executable=True)
        self.assertFalse(by_code(self.findings(('acme', 'org', path)), 'hook-missing'))

    def test_the_word_leakcheck_in_a_comment_or_an_echo_is_not_a_call(self):
        path = self.git_layer('acme', 'org')
        shared = self.work / 'shared-hooks'
        shared.mkdir()
        git(path, 'config', 'core.hooksPath', str(shared))
        fakes = ['#!/bin/sh\n# TODO: run skillsync leakcheck here\n',
                 '#!/bin/sh\necho leakcheck\n',
                 '#!/bin/sh\necho nothing\n   # skillsync leakcheck --staged\n']
        for fake in fakes:
            with self.subTest(fake=fake):
                for name in ('pre-commit', 'pre-push'):
                    write(shared / name, fake, executable=True)
                self.assertEqual(len(by_code(self.findings(('acme', 'org', path)), 'hook-missing')), 1)
        for name, call in (('pre-commit', 'skillsync leakcheck "$(git rev-parse --show-toplevel)" --staged'),
                           ('pre-push', '"/usr/bin/python3" "/opt/x/skillsync" leakcheck . --range "$r"')):
            write(shared / name, f'#!/bin/sh\n# comment\n{call} || exit $?\n', executable=True)
        self.assertFalse(by_code(self.findings(('acme', 'org', path)), 'hook-missing'))

    def test_tracked_hooks_directory_is_treated_the_same(self):
        path = self.git_layer('acme', 'org')
        write(path / '.githooks' / 'pre-commit', '#!/bin/sh\ntrue\n', executable=True)
        git(path, 'add', '-A')
        commit(path, 'team hooks')
        git(path, 'config', 'core.hooksPath', '.githooks')
        missing = by_code(self.findings(('acme', 'org', path)), 'hook-missing')
        self.assertEqual(len(missing), 1)
        for name in ('pre-commit', 'pre-push'):
            write(path / '.githooks' / name, '#!/bin/sh\nskillsync leakcheck . --staged\n', executable=True)
        self.assertFalse(by_code(self.findings(('acme', 'org', path)), 'hook-missing'))

    def test_a_legacy_bridge_entry_is_blocked_before_any_hook_check(self):
        old = self.work / 'old-library'
        old.mkdir()
        self.config_file.write_text(yaml_text({
            'layers': [{'id': 'core', 'kind': 'core', 'path': str(old), 'synthetic': {'version': '0.9.0', 'core_min': '0.9.0'}}],
            'layer_rules': [{'match': ['**'], 'use': ['core']}]}), encoding='utf-8')
        with self.assertRaises(layers.LayerError) as caught:
            layers.resolve_layers(self.project('p'))
        self.assertEqual(caught.exception.code, 'legacy-config')


class OrphansTest(CommandsCase):
    def test_unadopted_files_are_listed_and_empty_directories_are_not(self):
        core = self.layer_dir('core', 'core', version=generator_version())
        cache = self.work / 'composed'
        write(cache / 'feature' / 'abc123.orphans' / 'stray.md', 'x\n')
        (cache / 'feature' / 'def456.orphans').mkdir(parents=True)                      # empty: nothing to report
        write(cache / 'feature' / 'abc123' / 'SKILL.md', 'regular composite entry\n')   # not an orphan
        findings = self.layer_findings([('core', 'core', core)], cache_dir=cache)
        found = by_code(findings, 'orphans')
        self.assertEqual([item['severity'] for item in found], ['warning'])
        self.assertIn('abc123.orphans', found[0]['message'])
        self.assertNotIn('def456', found[0]['message'])
        self.assertEqual(layers.findings_status(findings), 'PASS')                      # a warning: listed, never silent
        self.assertFalse(by_code(self.layer_findings([('core', 'core', core)], cache_dir=cache, orphans=False), 'orphans'))
        self.assertFalse(by_code(self.layer_findings([('core', 'core', core)], cache_dir=self.work / 'none'), 'orphans'))

    def test_default_cache_is_the_composed_root_of_the_system(self):
        core = self.layer_dir('core', 'core', version=generator_version())
        write(layers.composed_root() / 'feature' / 'h.orphans' / 'stray.md', 'x\n')
        self.assertEqual(len(by_code(self.layer_findings([('core', 'core', core)]), 'orphans')), 1)


# ====================================================================== layers init (K9)


class LayersInitTest(CommandsCase):
    def test_next_hint_commits_before_add_and_runs_as_given(self):
        """UX-1 (D199): `next` commits the skeleton before `layers add`, so `add` never clones an empty repository."""
        self.set_git_identity('jan@acme.example')
        directory = self.work / 'my notes'                          # a space: the hint quotes the path
        code, report = self.cli_json('layers', 'init', str(directory), '--kind', 'personal', '--id', 'notes')
        self.assertEqual((code, report['status']), (0, 'PASS'), report)
        hint = report['next']
        self.assertLess(hint.index('commit'), hint.index('skillsync layers add notes'), hint)
        git_part = hint.split(' && skillsync ')[0]
        done = subprocess.run(git_part, shell=True, capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        code, added = self.add('notes', 'personal', directory)
        self.assertEqual((code, added['status']), (0, 'PASS'), added)
        self.assertFalse([w for w in added['warnings'] if 'no commits' in w or 'layer.yaml' in w], added['warnings'])

    def test_skeleton_git_repository_hooks_and_target_stub(self):
        self.set_git_identity('jan@acme.example')
        self.config_file.write_text('# my config\ntags: [x]\n', encoding='utf-8')
        target = self.work / 'my-notes'
        for mode in LINK_MODES:
            with self.subTest(mode), link_mode(mode):
                directory = self.work / f'my-notes-{mode}'
                code, report = self.cli_json('layers', 'init', str(directory), '--kind', 'personal', '--id', f'notes-{mode}')
                self.assertEqual((code, report['schema'], report['status']), (0, 1, 'PASS'), report)
                self.assertEqual(report['layer'], {'id': f'notes-{mode}', 'kind': 'personal', 'path': str(directory)})
                for rel in ('layer.yaml', 'README.md', 'CHANGELOG.md', 'instructions/_base.d/10-persona.md', 'leakguard/identity.yaml'):
                    self.assertIn(rel, report['created'])
                    self.assertTrue((directory / rel).is_file(), rel)
                self.assertTrue((directory / '.git').is_dir())
                meta, error = layers.read_layer_yaml(directory)
                self.assertIsNone(error)
                self.assertEqual((meta['id'], meta['kind'], meta['core_min']), (f'notes-{mode}', 'personal', generator_version()))
                self.assertTrue(meta['version'])
                self.assertEqual(sorted(item['name'] for item in report['hooks']), ['pre-commit', 'pre-push'])
                self.assertEqual(leakguard.hook_state(directory)['state'], 'ok')
                stub = self.config()['leakguard']['targets'][f'notes-{mode}']
                self.assertEqual(stub, {'scope': 'private', 'remote_match': [], 'authors': ['jan@acme.example']})
        self.assertEqual(self.config()['tags'], ['x'])                                     # foreign keys untouched
        self.assertIn('# my config', self.config_file.read_text(encoding='utf-8'))        # comment kept
        self.assertTrue(list(self.config_file.parent.glob('skillsync-config.yaml.bak-*')))
        self.assertNotIn('layers', self.config())                                          # registration is `layers add`

    def test_org_kind_uses_the_org_private_scope_and_the_directory_name_as_default_id(self):
        directory = self.work / 'Acme_Skills'
        code, report = self.cli_json('layers', 'init', str(directory), '--kind', 'org')
        self.assertEqual((code, report['layer']['id']), (0, 'acme-skills'))
        self.assertEqual(self.config()['leakguard']['targets']['acme-skills']['scope'], 'org-private')
        self.assertIn('layers add acme-skills', report['next'])
        self.assertFalse(list(self.config_file.parent.glob('*.bak-*')))                   # no config existed: nothing to back up

    def test_refusals_do_not_touch_anything(self):
        busy = self.work / 'busy'
        write(busy / 'file.txt', 'mine\n')
        existing = make_layer(self.work / 'already', 'already', 'personal')
        self.config_file.write_text('tags: [x]\n', encoding='utf-8')
        before = self.config_file.read_text(encoding='utf-8')
        cases = {
            'non-empty directory': (['layers', 'init', str(busy), '--kind', 'personal'], 'exists'),
            'already a layer': (['layers', 'init', str(existing), '--kind', 'personal'], 'exists'),
            'invalid id': (['layers', 'init', str(self.work / 'ok'), '--kind', 'org', '--id', 'Bad Id'], 'bad-argument'),
            'id from an unusable directory name': (['layers', 'init', str(self.work / '___'), '--kind', 'org'], 'bad-argument'),
        }
        for label, (args, code_expected) in cases.items():
            with self.subTest(label):
                code, report = self.cli_json(*args)
                self.assertEqual((code, report['status'], report['code']), (2, 'BLOCKED', code_expected))
        self.assertEqual((busy / 'file.txt').read_text(encoding='utf-8'), 'mine\n')
        self.assertEqual(self.config_file.read_text(encoding='utf-8'), before)
        self.assertFalse((self.work / 'ok').exists())

    def test_a_failure_after_the_skeleton_removes_what_init_created_and_a_retry_works(self):
        self.config_file.mkdir()                                              # the target stub cannot be written
        fresh = self.work / 'fresh'
        code, report = self.cli_json('layers', 'init', str(fresh), '--kind', 'personal')
        self.assertEqual((code, report['status']), (2, 'BLOCKED'))
        self.assertFalse(fresh.exists())                                      # a directory created by init is removed
        empty = self.work / 'empty-existing'
        empty.mkdir()
        keep = self.work / 'sibling.txt'
        write(keep, 'mine\n')
        code, report = self.cli_json('layers', 'init', str(empty), '--kind', 'personal')
        self.assertEqual(code, 2)
        self.assertEqual((empty.is_dir(), list(empty.iterdir())), (True, []))     # a pre-existing directory stays, empty
        self.assertEqual(keep.read_text(encoding='utf-8'), 'mine\n')
        self.config_file.rmdir()
        for directory in (fresh, empty):
            code, report = self.cli_json('layers', 'init', str(directory), '--kind', 'personal')
            self.assertEqual((code, report['status']), (0, 'PASS'), report)
            self.assertTrue((directory / 'layer.yaml').is_file())

    def test_argument_errors_exit_2(self):
        for args in (['layers', 'init', str(self.work / 'a')], ['layers', 'init', str(self.work / 'a'), '--kind', 'core'],
                     ['layers', 'init', '--kind', 'org']):
            with self.subTest(args):
                self.assertEqual(self.cli(*args).returncode, 2)
        self.assertFalse((self.work / 'a').exists())

    def test_an_existing_target_is_left_unchanged_with_a_warning(self):
        self.config_file.write_text(yaml_text({'leakguard': {'targets': {'mine': {'scope': 'org-private', 'authors': ['x@example.test']}}}}),
                                    encoding='utf-8')
        code, report = self.cli_json('layers', 'init', str(self.work / 'mine'), '--kind', 'personal')
        self.assertEqual(code, 0)
        self.assertTrue(any('already exists' in item for item in report['warnings']))
        self.assertEqual(self.config()['leakguard']['targets']['mine'], {'scope': 'org-private', 'authors': ['x@example.test']})

    def test_shared_hooks_path_is_never_written_and_init_still_succeeds(self):
        shared = self.work / 'shared-hooks'
        shared.mkdir()
        Path(self.env['GIT_CONFIG_GLOBAL']).write_text(f'[core]\n\thooksPath = {shared}\n', encoding='utf-8')
        code, report = self.cli_json('layers', 'init', str(self.work / 'mine'), '--kind', 'personal')
        self.assertEqual((code, report['status']), (0, 'PASS'))
        self.assertTrue(any(item.startswith('hooks:') and 'core.hooksPath' in item for item in report['warnings']), report['warnings'])
        self.assertEqual(list(shared.iterdir()), [])
        for name in ('pre-commit', 'pre-push'):
            self.assertFalse((self.work / 'mine' / '.git' / 'hooks' / name).exists())

    def test_the_installed_hook_blocks_a_foreign_identity_and_lets_clean_commits_pass(self):
        other = self.git_layer('beta', 'org', identity_terms=[{'term': CLIENT_BETA, 'kind': 'org'}])
        self.write_config([('beta', 'org', other)])
        self.set_git_identity('me@example.test')
        directory = self.work / 'mine'
        code, _ = self.cli_json('layers', 'init', str(directory), '--kind', 'personal')
        self.assertEqual(code, 0)
        write(directory / 'docs' / 'a.md', f'about {CLIENT_BETA}\n')
        git(directory, 'add', '-A')
        blocked = git(directory, 'commit', '-q', '-m', 'leak', check=False)
        self.assertNotEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
        write(directory / 'docs' / 'a.md', 'about nothing\n')
        git(directory, 'add', '-A')
        self.assertEqual(git(directory, 'commit', '-q', '-m', 'clean', check=False).returncode, 0)


# ====================================================================== layers add (K9)


class LayersAddTest(CommandsCase):
    def setUp(self):
        super().setUp()
        self.set_git_identity('jan@acme.example')
        self.bare, self.work_src = self.source_repo('acme', 'acme', 'org', {'skills/s/SKILL.md': 'skill\n'})

    def test_clone_config_entry_target_stub_and_hooks(self):
        for mode in LINK_MODES:
            with self.subTest(mode), link_mode(mode):
                layer_id = f'acme-{mode}'
                bare, _ = self.source_repo(layer_id, layer_id, 'org', {'skills/s/SKILL.md': 'skill\n'})
                code, report = self.add(layer_id, 'org', bare)
                self.assertEqual((code, report['schema'], report['status']), (0, 1, 'PASS'), report)
                checkout = self.default_checkout(layer_id)
                self.assertEqual(Path(report['layer']['path']), checkout)
                self.assertTrue((checkout / '.git').is_dir() and (checkout / 'layer.yaml').is_file())
                self.assertTrue((checkout / 'skills/s/SKILL.md').is_file())
                entry = next(item for item in self.config()['layers'] if item['id'] == layer_id)
                self.assertEqual((entry['kind'], entry['source']), ('org', str(bare)))
                stub = self.config()['leakguard']['targets'][layer_id]
                self.assertEqual((stub['scope'], stub['authors'], stub['remote_match']), ('org-private', ['jan@acme.example'], [str(bare)]))
                self.assertEqual(leakguard.hook_state(checkout)['state'], 'ok')
                self.assertEqual(sorted(item['name'] for item in report['hooks']), ['pre-commit', 'pre-push'])
                self.assertEqual(report['layer']['writable'], True)
                self.assertEqual(report['layer']['version'], '1.0.0')

    def test_user_comments_are_kept_and_a_backup_holds_the_original(self):
        original = ('# machine config of the tester\nlayers:\n  # the core layer\n  - id: core\n    kind: core\n'
                    f'    path: {self.default_checkout("core")}\n\ntags: [x]   # keep me\n')
        self.config_file.write_text(original, encoding='utf-8')
        code, report = self.add('acme', 'org', self.bare)
        self.assertEqual((code, report['rewritten']), (0, False))
        new_lines = self.config_file.read_text(encoding='utf-8').splitlines()
        position = -1
        for line in original.splitlines():
            self.assertIn(line, new_lines, line)
            self.assertGreater(new_lines.index(line), position, f'order changed at {line!r}')
            position = new_lines.index(line)
        self.assertEqual(Path(report['backup']).read_text(encoding='utf-8'), original)
        self.assertEqual([item['id'] for item in self.config()['layers']], ['core', 'acme'])

    def test_uncertain_layout_rewrites_the_yaml_but_keeps_the_original_in_the_backup(self):
        original = '# precious comment\nlayers: []\ntags: [x]\n'
        self.config_file.write_text(original, encoding='utf-8')
        code, report = self.add('acme', 'org', self.bare)
        self.assertEqual((code, report['rewritten']), (0, True))
        self.assertEqual(Path(report['backup']).read_text(encoding='utf-8'), original)
        self.assertEqual(([item['id'] for item in self.config()['layers']], self.config()['tags']), (['acme'], ['x']))

    def test_read_only_layers_get_no_hooks(self):
        core_bare, _ = self.source_repo('core', 'core', 'core', version=generator_version())
        code, report = self.add('core', 'core', core_bare)
        self.assertEqual((code, report['layer']['writable'], report['hooks']), (0, False, []))
        self.assertEqual(leakguard.hook_state(self.default_checkout('core'))['state'], 'missing')
        code, report = self.add('acme', 'org', self.bare, '--read-only')
        self.assertEqual((code, report['hooks']), (0, []))
        self.assertEqual(leakguard.hook_state(self.default_checkout('acme'))['state'], 'missing')
        other, _ = self.source_repo('rw', 'rw', 'org')
        code, report = self.add('rw', 'org', other, '--writable')
        self.assertEqual(leakguard.hook_state(self.default_checkout('rw'))['state'], 'ok')

    def test_custom_path_ref_and_subdir_are_recorded(self):
        tagged = self.make_repo('tagged')
        make_layer(tagged / 'library', 'lib', 'org')
        git(tagged, 'add', '-A')
        commit(tagged, 'one')
        git(tagged, 'tag', 'v1')
        write(tagged / 'library' / 'extra.md', 'later\n')
        git(tagged, 'add', '-A')
        commit(tagged, 'two')
        target = self.work / 'my-checkout'
        code, report = self.add('lib', 'org', tagged, '--subdir', 'library', '--ref', 'v1', '--path', str(target))
        self.assertEqual((code, report['status']), (0, 'PASS'), report)
        self.assertTrue(self.detached(target))                                           # a tag is a detached checkout
        self.assertFalse((target / 'library' / 'extra.md').exists())
        entry = self.config()['layers'][0]
        self.assertEqual((entry['subdir'], entry['ref'], entry['path']), ('library', 'v1', str(target)))
        layer = layers.load_machine_config().layers['lib']
        self.assertEqual(layer.root, target / 'library')

    def test_source_without_commits_adds_the_layer_with_a_warning(self):
        empty = self.work / 'empty.git'
        git(self.work, 'init', '-q', '--bare', str(empty))
        code, report = self.add('fresh', 'personal', empty)
        self.assertEqual((code, report['status']), (0, 'PASS'))
        self.assertTrue(any('layer.yaml' in item for item in report['warnings']), report['warnings'])
        self.assertTrue(any('has no commits' in item for item in report['warnings']), report['warnings'])
        self.assertIn('fresh', [item['id'] for item in self.config()['layers']])

    def test_clone_failure_shows_the_whole_short_git_message(self):
        """UX-1 (D199): not only the last line of git (`... and the repository exists.`)."""
        not_repo = self.work / 'not-a-repo'
        not_repo.mkdir()
        code, report = self.add('ghost', 'org', not_repo.as_uri())        # file:// = a transport: git prints several lines
        self.assertEqual((code, report['status']), (2, 'BLOCKED'), report)
        self.assertIn('git clone failed:', report['reason'])
        self.assertIn('Could not read from remote repository', report['reason'])
        self.assertIn('repository exists', report['reason'])
        self.assertIn(' | ', report['reason'], 'several git lines joined')

    def test_git_message_hides_url_credentials_and_is_short(self):
        process = subprocess.CompletedProcess(['git'], 128, '', "Cloning into 'x'...\n"
                                              "fatal: unable to access 'https://jan:s3cret-token@git.example.test/a.git/': refused\n" + 'x\n' * 20)
        message = layers._git_message(process)
        self.assertNotIn('s3cret-token', message)
        self.assertNotIn('jan:', message)
        self.assertIn('https://***@git.example.test/a.git/', message)
        self.assertTrue(message.endswith('| …'))
        self.assertLessEqual(len(layers._git_message(subprocess.CompletedProcess(['git'], 1, '', 'y' * 2000))), 600)
        self.assertEqual(layers._git_message(subprocess.CompletedProcess(['git'], 5, '', '')), 'exit 5')
        for raw, hidden in (('https://jan:p@ss@git.example.test/a.git', 'ss@git'), ('https://git.example.test/a.git?private_token=abc123&x=1', 'abc123'),
                            ('ssh://deploy:k3y@git.example.test/r', 'k3y')):
            with self.subTest(raw=raw):
                masked = layers._git_message(subprocess.CompletedProcess(['git'], 128, '', f"fatal: unable to access '{raw}'"))
                self.assertNotIn(hidden, masked)
                tail = layers._git_tail(subprocess.CompletedProcess(['git'], 128, '', f"x\nfatal: unable to access '{raw}'"))
                self.assertNotIn(hidden, tail)

    def test_refusals_leave_config_and_disk_unchanged(self):
        self.assertEqual(self.add('acme', 'org', self.bare)[0], 0)
        before = self.config_file.read_text(encoding='utf-8')
        other, _ = self.source_repo('other', 'other', 'org')
        core_one, _ = self.source_repo('c1', 'c1', 'core')
        mismatch, _ = self.source_repo('mismatch', 'someone-else', 'org')
        busy = self.work / 'busy'
        write(busy / 'precious.txt', 'x\n')
        cases = {
            'duplicate id': (('acme', 'org', other), (), 'conflict', None),
            'duplicate id with another checkout path': (('acme', 'org', other), ('--path', str(self.work / 'dup-checkout')),
                                                        'conflict', self.work / 'dup-checkout'),
            'second core layer': (('c1', 'core', core_one), (), 'conflict', self.default_checkout('c1')),
            'checkout path in use': (('busy', 'org', other), ('--path', str(busy)), 'conflict', None),
            'unreachable source': (('ghost', 'org', self.work / 'no-such-repo'), (), 'git-error', self.default_checkout('ghost')),
            'layer.yaml of another id': (('mismatch', 'org', mismatch), (), 'layer-yaml-invalid', self.default_checkout('mismatch')),
            'unknown ref': (('refd', 'org', other), ('--ref', 'v-nope'), None, self.default_checkout('refd')),
            'missing subdir': (('sub', 'org', other), ('--subdir', 'nope'), 'bad-argument', self.default_checkout('sub')),
            'invalid id': (('Bad_Id', 'org', other), (), 'bad-argument', None),
        }
        # the second core case needs an existing core to conflict with
        self.assertEqual(self.add('core', 'core', self.source_repo('c0', 'core', 'core')[0])[0], 0)
        before = self.config_file.read_text(encoding='utf-8')
        for label, ((layer_id, kind, source), extra, code_expected, leftover) in cases.items():
            with self.subTest(label):
                code, report = self.add(layer_id, kind, source, *extra)
                self.assertEqual((code, report['status']), (2, 'BLOCKED'), report)
                if code_expected:
                    self.assertEqual(report['code'], code_expected)
                self.assertEqual(self.config_file.read_text(encoding='utf-8'), before)
                if leftover is not None:
                    self.assertFalse(leftover.exists(), f'{leftover} left behind')
        self.assertEqual((busy / 'precious.txt').read_text(encoding='utf-8'), 'x\n')

    def test_argument_errors_exit_2(self):
        for args in (['layers', 'add', 'x', '--source', str(self.bare)], ['layers', 'add', 'x', '--kind', 'org'],
                     ['layers', 'add', 'x', '--kind', 'team', '--source', str(self.bare)],
                     ['layers', 'add', 'x', '--kind', 'org', '--source', str(self.bare), '--writable', '--read-only']):
            with self.subTest(args):
                self.assertEqual(self.cli(*args).returncode, 2)

    def test_an_existing_target_stub_is_not_overwritten(self):
        self.config_file.write_text(yaml_text({'leakguard': {'targets': {'acme': {'scope': 'private', 'authors': ['keep@example.test']}}}}),
                                    encoding='utf-8')
        code, report = self.add('acme', 'org', self.bare)
        self.assertEqual(code, 0)
        self.assertTrue(any('already exists' in item for item in report['warnings']))
        self.assertIsNone(report['target'])
        self.assertEqual(self.config()['leakguard']['targets']['acme'], {'scope': 'private', 'authors': ['keep@example.test']})

    def test_a_warning_points_to_layers_use_when_no_rule_uses_the_layer(self):
        code, report = self.add('acme', 'org', self.bare)
        self.assertTrue(any('layers use acme' in item for item in report['warnings']))
        self.write_config([('acme', 'org', self.default_checkout('acme'))], [(['**'], ['acme'])])
        other, _ = self.source_repo('x', 'x', 'org')
        code, report = self.add('x', 'org', other)
        self.assertTrue(any('layers use x' in item for item in report['warnings']))              # only for `x`
        self.assertFalse(any('layers use acme' in item for item in report['warnings']))

    def test_tracked_hooks_directory_is_not_modified(self):
        shared, _ = self.source_repo('shared', 'shared', 'org', {'.githooks/pre-commit': '#!/bin/sh\ntrue\n'})
        Path(self.env['GIT_CONFIG_GLOBAL']).write_text('[user]\n\temail = jan@acme.example\n[core]\n\thooksPath = .githooks\n',
                                                       encoding='utf-8')
        code, report = self.add('shared', 'org', shared)
        self.assertEqual((code, report['status']), (0, 'PASS'))
        self.assertTrue(any(item.startswith('hooks:') for item in report['warnings']))
        checkout = self.default_checkout('shared')
        self.assertEqual((checkout / '.githooks' / 'pre-commit').read_text(encoding='utf-8'), '#!/bin/sh\ntrue\n')
        self.assertFalse((checkout / '.githooks' / 'pre-push').exists())

    def test_remote_patterns_are_exact_and_match_the_source_in_every_url_form(self):
        table = {
            'https://example.test/acme/skills.git': (['example.test/acme/skills', 'example.test/acme/skills.git'],
                                                     ['https://example.test/acme/skills.git', 'https://example.test/acme/skills',
                                                      'ssh://git@example.test/acme/skills.git']),
            'git@example.test:acme/skills.git': (['example.test:acme/skills', 'example.test:acme/skills.git'],
                                                 ['git@example.test:acme/skills.git', 'git@example.test:acme/skills']),
        }
        for source, (expected, remotes) in table.items():
            patterns = layers.remote_patterns(source)
            self.assertEqual(patterns, expected)
            targets = {'acme': {'scope': 'org-private', 'kind': 'org', 'authors': [], 'remote_match': patterns}}
            for remote in remotes:
                with self.subTest(source=source, remote=remote):
                    self.assertEqual(leakguard.match_targets(targets, [remote]), ['acme'])
            for other in ('skills-other', 'skills-ops', 'skills2'):          # a shared name prefix is not a match
                for remote in remotes:
                    with self.subTest(source=source, other=other, remote=remote):
                        sibling = remote.replace('/skills', f'/{other}').replace(':acme/skills', f':acme/{other}')
                        self.assertEqual(leakguard.match_targets(targets, [sibling]), [])
            self.assertEqual(leakguard.match_targets(targets, ['git@example.test:other/skills.git']), [])
        local = layers.remote_patterns(str(self.bare))
        self.assertEqual(local, [str(self.bare)])
        targets = {'acme': {'scope': 'org-private', 'kind': 'org', 'authors': [], 'remote_match': local}}
        self.assertEqual(leakguard.match_targets(targets, [str(self.bare)]), ['acme'])
        self.assertEqual(leakguard.match_targets(targets, [str(self.bare) + '-other']), [])
        # glob characters of a source are literal characters of the pattern
        targets['acme']['remote_match'] = layers.remote_patterns('https://example.test/ac*me/skills')
        self.assertEqual(leakguard.match_targets(targets, ['https://example.test/ac*me/skills']), ['acme'])
        self.assertEqual(leakguard.match_targets(targets, ['https://example.test/ac-me/skills']), [])

    def test_a_failure_after_the_clone_removes_the_clone_and_a_retry_works(self):
        self.config_file.mkdir()                                             # the config cannot be written
        code, report = self.add('acme', 'org', self.bare)
        self.assertEqual((code, report['status']), (2, 'BLOCKED'))
        self.assertFalse(self.default_checkout('acme').exists())
        self.config_file.rmdir()
        code, report = self.add('acme', 'org', self.bare)
        self.assertEqual((code, report['status']), (0, 'PASS'), report)
        # a bad layer.yaml in the source: fixed in the source, the retry succeeds
        bare, src = self.source_repo('wrong', 'someone-else', 'org')
        self.assertEqual(self.add('wrong', 'org', bare)[0], 2)
        self.assertFalse(self.default_checkout('wrong').exists())
        fixed = yaml_text({'schema': 1, 'id': 'wrong', 'kind': 'org', 'version': '1.0.0', 'core_min': generator_version()})
        self.push_commit(src, 'layer.yaml', fixed, 'fix id')
        self.assertEqual(self.add('wrong', 'org', bare)[0], 0)

    def test_a_bad_ref_is_rejected_before_any_clone(self):
        ghost = self.work / 'no-such-repo'                                    # a clone attempt would fail with git-error
        for ref in ('--oops', ' ', ''):
            with self.subTest(ref=ref):
                code, report = self.add('refd', 'org', ghost, f'--ref={ref}')
                self.assertEqual((code, report['code']), (2, 'bad-argument'))
        self.assertFalse(self.default_checkout('refd').exists())
        self.assertFalse(self.config_file.exists())

    def test_git_messages_do_not_depend_on_the_locale(self):
        ghost = self.work / 'no-such-repo'
        reasons = []
        for env in ({'LC_ALL': 'pl_PL.UTF-8', 'LANG': 'pl_PL.UTF-8', 'LANGUAGE': 'pl'}, {'LC_ALL': 'C', 'LANG': 'C'},
                    {'LC_ALL': 'de_DE.UTF-8', 'LANG': 'de_DE.UTF-8', 'LANGUAGE': 'de'}):
            code, report = self.cli_json('layers', 'add', 'ghost', '--kind', 'org', '--source', str(ghost), env=env)
            self.assertEqual((code, report['code']), (2, 'git-error'))
            reasons.append(report['reason'])
        self.assertEqual(len(set(reasons)), 1, reasons)
        self.assertTrue(reasons[0].isascii(), reasons[0])
        self.assertRegex(reasons[0], r'(?i)does not exist|not found|repository')

    def test_stub_authors_are_empty_without_a_git_email(self):
        Path(self.env['GIT_CONFIG_GLOBAL']).write_text('', encoding='utf-8')
        code, report = self.add('acme', 'org', self.bare)
        self.assertEqual((code, self.config()['leakguard']['targets']['acme']['authors']), (0, []))


# ====================================================================== layers update (K9, "Aktualizacja warstw")


class LayersUpdateTest(CommandsCase):
    def setUp(self):
        super().setUp()
        self.set_git_identity('jan@acme.example')
        self.bare, self.src = self.source_repo('acme', 'acme', 'org', {'skills/s/SKILL.md': 'v1\n'})
        self.checkout = self.default_checkout('acme')

    def update(self, *args):
        return self.cli_json('layers', 'update', *args)

    def entry(self, layer_id='acme'):
        return next(item for item in self.config()['layers'] if item['id'] == layer_id)

    def test_without_a_ref_the_current_branch_is_pulled_fast_forward_only(self):
        self.assertEqual(self.add('acme', 'org', self.bare)[0], 0)
        old = self.head(self.checkout)
        new = self.push_commit(self.src, 'skills/s/SKILL.md', 'v2\n')
        for mode in LINK_MODES:
            with self.subTest(mode), link_mode(mode):
                code, report = self.update('acme')
                self.assertEqual((code, report['schema'], report['status']), (0, 1, 'PASS'), report)
                item = report['layers'][0]
                self.assertEqual((item['id'], item['status'], item['action']), ('acme', 'PASS', 'pulled'))
                self.assertEqual(self.head(self.checkout), new)
                if mode == 'symlink':
                    self.assertEqual((item['from'], item['to']), (old, new))
        self.assertEqual((self.checkout / 'skills/s/SKILL.md').read_text(encoding='utf-8'), 'v2\n')

    def test_a_diverged_branch_is_blocked_without_moving_head(self):
        self.assertEqual(self.add('acme', 'org', self.bare)[0], 0)
        commit_file_local = self.checkout / 'local.md'
        write(commit_file_local, 'local\n')
        git(self.checkout, 'add', '-A')
        local_head = commit(self.checkout, 'local commit')
        self.push_commit(self.src, 'skills/s/SKILL.md', 'v2\n')
        code, report = self.update('acme')
        self.assertEqual((code, report['status']), (2, 'BLOCKED'))
        self.assertEqual(report['layers'][0]['action'], 'no-fast-forward')
        self.assertEqual(self.head(self.checkout), local_head)

    def test_a_branch_ref_fast_forwards_to_the_remote_branch(self):
        self.assertEqual(self.add('acme', 'org', self.bare, '--ref', 'main')[0], 0)
        new = self.push_commit(self.src, 'skills/s/SKILL.md', 'v2\n')
        code, report = self.update('acme')
        self.assertEqual((code, report['layers'][0]['action']), (0, 'fast-forward'))
        self.assertEqual(self.head(self.checkout), new)
        self.assertFalse(self.detached(self.checkout))                                   # stays on the branch
        local = self.checkout / 'local.md'
        write(local, 'local\n')
        git(self.checkout, 'add', '-A')
        commit(self.checkout, 'local commit')
        self.push_commit(self.src, 'skills/s/SKILL.md', 'v3\n')
        code, report = self.update('acme')
        self.assertEqual((code, report['layers'][0]['action']), (2, 'no-fast-forward'))

    def test_a_tag_ref_checks_out_the_tag_and_ref_changes_are_stored_in_the_config(self):
        git(self.src, 'tag', 'v1')
        git(self.src, 'push', '-q', 'origin', 'v1')
        self.assertEqual(self.add('acme', 'org', self.bare, '--ref', 'v1')[0], 0)
        v1 = self.head(self.checkout)
        v2 = self.push_commit(self.src, 'skills/s/SKILL.md', 'v2\n')
        git(self.src, 'tag', 'v2')
        git(self.src, 'push', '-q', 'origin', 'v2')
        code, report = self.update('acme')                                               # pinned: stays on v1
        self.assertEqual((code, report['layers'][0]['action'], self.head(self.checkout)), (0, 'checkout-tag', v1))
        code, report = self.update('acme', '--ref', 'v2')
        self.assertEqual((code, report['layers'][0]['action'], report['status']), (0, 'checkout-tag', 'PASS'))
        self.assertEqual(self.head(self.checkout), v2)
        self.assertTrue(self.detached(self.checkout))
        self.assertEqual(self.entry()['ref'], 'v2')
        self.assertTrue(Path(report['backup']).is_file())

    def test_a_commit_ref_gives_a_detached_head(self):
        self.assertEqual(self.add('acme', 'org', self.bare)[0], 0)
        first = self.head(self.checkout)
        self.push_commit(self.src, 'skills/s/SKILL.md', 'v2\n')
        code, report = self.update('acme', '--ref', first)
        self.assertEqual((code, report['layers'][0]['action']), (0, 'checkout-commit'))
        self.assertEqual(self.head(self.checkout), first)
        self.assertTrue(self.detached(self.checkout))
        self.assertEqual(self.entry()['ref'], first)

    def test_a_dirty_tree_blocks_that_layer_and_the_others_continue(self):
        beta_bare, beta_src = self.source_repo('beta', 'beta', 'org')
        self.assertEqual(self.add('acme', 'org', self.bare)[0], 0)
        self.assertEqual(self.add('beta', 'org', beta_bare)[0], 0)
        acme_head, beta_head = self.head(self.checkout), self.head(self.default_checkout('beta'))
        write(self.checkout / 'skills/s/SKILL.md', 'edited locally\n')
        self.push_commit(self.src, 'skills/s/SKILL.md', 'v2\n')
        beta_new = self.push_commit(beta_src, 'note.md', 'n\n')
        code, report = self.update()
        results = {item['id']: item for item in report['layers']}
        self.assertEqual((code, report['status']), (2, 'BLOCKED'))
        self.assertEqual((results['acme']['status'], results['acme']['action']), ('BLOCKED', 'dirty'))
        self.assertEqual(results['beta']['status'], 'PASS')
        self.assertEqual(self.head(self.checkout), acme_head)                           # untouched ...
        self.assertEqual((self.checkout / 'skills/s/SKILL.md').read_text(encoding='utf-8'), 'edited locally\n')   # ... with local edits
        self.assertEqual(self.head(self.default_checkout('beta')), beta_new)
        self.assertNotEqual(beta_new, beta_head)

    def test_a_missing_checkout_is_blocked_and_unknown_arguments_are_refused(self):
        self.assertEqual(self.add('acme', 'org', self.bare)[0], 0)
        shutil.rmtree(self.checkout)
        code, report = self.update('acme')
        self.assertEqual((code, report['layers'][0]['status'], report['layers'][0]['action']), (2, 'BLOCKED', 'missing'))
        self.assertEqual(self.update('nope')[0], 2)
        self.assertEqual(self.update('--ref', 'v1')[0], 2)                               # --ref needs a layer id
        self.assertEqual(self.update('acme', '--ref=--oops')[0], 2)                      # a ref cannot look like an option
        self.assertEqual(self.cli('layers', 'update', 'acme', '--ref').returncode, 2)       # argparse error

    def test_ref_option_needs_a_layer_id_and_changes_nothing(self):
        self.assertEqual(self.add('acme', 'org', self.bare)[0], 0)
        first = self.head(self.checkout)
        new = self.push_commit(self.src, 'skills/s/SKILL.md', 'v2\n')
        self.assertEqual(self.update()[0], 0)
        before = self.config_file.read_text(encoding='utf-8')
        code, report = self.update('--ref', first)
        self.assertEqual((code, report['status'], report['code']), (2, 'BLOCKED', 'bad-argument'))
        self.assertEqual((self.head(self.checkout), self.config_file.read_text(encoding='utf-8')), (new, before))

    def test_an_unknown_ref_blocks_and_does_not_change_the_config(self):
        self.assertEqual(self.add('acme', 'org', self.bare)[0], 0)
        before = self.config_file.read_text(encoding='utf-8')
        head = self.head(self.checkout)
        code, report = self.update('acme', '--ref', 'v-nope')
        self.assertEqual((code, report['status']), (2, 'BLOCKED'))
        self.assertEqual(self.config_file.read_text(encoding='utf-8'), before)
        self.assertEqual(self.head(self.checkout), head)

    def test_update_rewrites_a_step_1_1_hook_block_and_is_idempotent(self):
        self.assertEqual(self.add('acme', 'org', self.bare)[0], 0)
        identity = self.work / 'ident.yaml'
        write(identity, yaml_text({'schema': 1, 'layer': 'x', 'terms': []}))
        self.assertEqual(leakguard.install_hook(self.checkout, remove=True)['status'], 'PASS')
        self.assertEqual(leakguard.install_hook(self.checkout, identity_files=[str(identity)])['status'], 'PASS')
        self.assertEqual(leakguard.hook_state(self.checkout)['state'], 'legacy')
        code, report = self.update('acme')
        self.assertEqual((code, sorted(item['action'] for item in report['layers'][0]['hooks'])), (0, ['updated', 'updated']))
        self.assertEqual(leakguard.hook_state(self.checkout)['state'], 'ok')
        code, report = self.update('acme')
        self.assertEqual([item['action'] for item in report['layers'][0]['hooks']], ['unchanged', 'unchanged'])

    def test_nothing_to_update_is_not_run_and_a_non_git_layer_does_not_hide_a_pass(self):
        plain = self.layer_dir('plain', 'org')
        self.write_config([('plain', 'org', plain)])
        code, report = self.update()
        self.assertEqual((code, report['status'], report['layers'][0]['action']), (0, 'NOT_RUN', 'not-git'))
        self.assertEqual(self.update('plain')[1]['status'], 'NOT_RUN')
        self.config_file.unlink()
        self.assertEqual(self.add('acme', 'org', self.bare)[0], 0)
        config = self.config()
        config['layers'].append({'id': 'plain', 'kind': 'org', 'path': str(plain), 'source': str(plain)})
        self.config_file.write_text(yaml_text(config), encoding='utf-8')
        code, report = self.update()
        self.assertEqual((code, report['status']), (0, 'PASS'))
        self.assertEqual({item['id']: item['status'] for item in report['layers']}, {'acme': 'PASS', 'plain': 'NOT_RUN'})
        shutil.rmtree(self.checkout)                                       # a BLOCKED layer beats NOT_RUN
        self.assertEqual(self.update()[1]['status'], 'BLOCKED')

    def test_update_checks_the_bridge_like_every_resolver(self):
        old = self.work / 'old-library'
        old.mkdir()
        self.config_file.write_text(yaml_text({'layers': [{'id': 'core', 'kind': 'core', 'path': str(old),
                                                           'synthetic': {'version': '0.9.0', 'core_min': '0.9.0'}}]}), encoding='utf-8')
        for args in ((), ('core',)):
            report = layers.layers_update(*args)
            self.assertEqual((report['status'], report['code']), ('BLOCKED', 'legacy-config'))
            self.assertIn('layers migrate --layer-source core=PATH --apply', report['reason'])

    def test_update_covers_layers_in_kind_order(self):
        core_bare, _ = self.source_repo('core', 'core', 'core', version=generator_version())
        self.assertEqual(self.add('acme', 'org', self.bare)[0], 0)
        self.assertEqual(self.add('core', 'core', core_bare)[0], 0)
        code, report = self.update()
        self.assertEqual((code, [item['id'] for item in report['layers']]), (0, ['core', 'acme']))


# ====================================================================== layers list --json (K9, K14)


class LayersListTest(CommandsCase):
    def setUp(self):
        super().setUp()
        self.core = self.layer_dir('core', 'core', version=generator_version())
        self.acme = self.layer_dir('acme', 'org', version='2.1.0')
        self.me = self.layer_dir('me', 'personal')
        self.project_dir = self.project('Repos/Acme/app')
        self.write_config([('core', 'core', self.core), ('acme', 'org', self.acme, {'subdir': ''}), ('me', 'personal', self.me)],
                          [([f'{self.work}/Repos/Acme/**'], ['core', 'acme', 'me']), (['**'], ['core', 'me'])])

    def test_json_schema_of_the_k14_contract_plus_status(self):
        for mode in LINK_MODES:
            with self.subTest(mode), link_mode(mode):
                code, report = self.cli_json('layers', 'list', '--project', str(self.project_dir))
                self.assertEqual(code, 0)
                for key in ('schema', 'generator_version', 'layers', 'composed_root', 'status'):
                    self.assertIn(key, report)
                self.assertEqual((report['schema'], report['status'], report['generator_version']), (1, 'PASS', generator_version()))
                self.assertEqual(report['composed_root'], str(machine_cache_dir() / 'composed'))
                self.assertEqual([item['id'] for item in report['layers']], ['core', 'acme', 'me'])
                acme = report['layers'][1]
                for key in ('id', 'kind', 'path', 'subdir', 'version', 'writable'):
                    self.assertIn(key, acme)
                self.assertEqual((acme['kind'], acme['path'], acme['subdir'], acme['version'], acme['writable']),
                                 ('org', str(self.acme), '', '2.1.0', True))
                self.assertEqual([item['writable'] for item in report['layers']], [False, True, True])

    def test_the_effective_list_depends_on_the_project(self):
        other = self.project('Repos/Other/app')
        code, report = self.cli_json('layers', 'list', '--project', str(other))
        self.assertEqual((code, [item['id'] for item in report['layers']]), (0, ['core', 'me']))

    def test_blocked_without_a_rule_keeps_the_schema(self):
        stray = self.project('loose/x')
        self.write_config([('core', 'core', self.core)], [([f'{self.work}/Repos/**'], ['core'])])
        code, report = self.cli_json('layers', 'list', '--project', str(stray))
        self.assertEqual((code, report['schema'], report['status'], report['code']), (2, 1, 'BLOCKED', 'no-rule'))
        self.assertEqual(report['generator_version'], generator_version())

    def test_text_output_lists_layers_and_exit_code(self):
        process = self.cli('layers', 'list', '--project', str(self.project_dir))
        self.assertEqual(process.returncode, 0)
        self.assertIn('layers list: PASS', process.stdout)
        for layer_id in ('core', 'acme', 'me'):
            self.assertIn(layer_id, process.stdout)
        blocked = self.cli('layers', 'list', '--project', str(self.project('loose/x')), '--layer', 'zzz')
        self.assertEqual(blocked.returncode, 2)

    def test_layer_flag_replaces_the_configured_layers(self):
        code, report = self.cli_json('layers', 'list', '--layer', f'core={self.core}', '--layer', f'me={self.me}',
                                     cwd=self.project('loose/x'))
        self.assertEqual((code, [item['id'] for item in report['layers']], report['origin']), (0, ['core', 'me'], 'override'))

    def test_missing_layer_directories_are_reported_as_absent_notes_not_listed(self):
        shutil.rmtree(self.acme)
        code, report = self.cli_json('layers', 'list', '--project', str(self.project_dir))
        self.assertEqual((code, [item['id'] for item in report['layers']]), (0, ['core', 'me']))
        self.assertIn('layer-absent', codes(report['notes']))


# ====================================================================== K9 scenario: two organisations and a personal layer


class OnboardingScenarioTest(CommandsCase):
    def commit_all(self, path, message='content'):
        git(path, 'add', '-A')
        return commit(path, message)

    ORDERS = {
        'organisations first': ('org-one', 'org-two', 'mine'),
        'personal default first': ('mine', 'org-one', 'org-two'),
    }

    def test_a_new_user_wires_two_organisations_and_a_personal_layer_organisations_first(self):
        self.scenario(self.ORDERS['organisations first'])

    def test_a_new_user_wires_two_organisations_and_a_personal_layer_personal_default_first(self):
        self.scenario(self.ORDERS['personal default first'])

    def test_core_added_after_the_rules_joins_every_rule(self):
        """UX-1 (D199): the README steps in the wrong order (personal and org rules before `layers add core`)
        still end in a working setup: adding the core layer puts it first in every existing rule."""
        self.set_git_identity('new.user@example.test')
        for layer_id, kind in (('org-one', 'org'), ('mine', 'personal')):
            directory = self.work / 'repos' / layer_id
            self.assertEqual(self.cli_json('layers', 'init', str(directory), '--kind', kind, '--id', layer_id)[0], 0)
            self.commit_all(directory)
            self.assertEqual(self.add(layer_id, kind, directory)[0], 0)
        org_glob = f'{self.work}/clients/one/**'
        self.assertEqual(self.cli_json('layers', 'use', 'mine', '--match', '**')[0], 0)
        self.assertEqual(self.cli_json('layers', 'use', 'org-one', '--match', org_glob)[0], 0)
        self.assertEqual(self.config()['layer_rules'], [{'match': [org_glob], 'use': ['org-one', 'mine']},
                                                        {'match': ['**'], 'use': ['mine']}])
        core_bare, _ = self.source_repo('core', 'core', 'core', {'skills/base/SKILL.md': 'base\n'}, version=generator_version())
        code, report = self.add('core', 'core', core_bare)
        self.assertEqual((code, report['status']), (0, 'PASS'), report)
        self.assertEqual(report['rules_updated'], [{'match': [org_glob], 'use': ['core', 'org-one', 'mine']},
                                                   {'match': ['**'], 'use': ['core', 'mine']}])
        self.assertTrue(any('added to 2 existing layer_rules entries' in w for w in report['warnings']), report['warnings'])
        self.assertEqual(self.config()['layer_rules'], [{'match': [org_glob], 'use': ['core', 'org-one', 'mine']},
                                                        {'match': ['**'], 'use': ['core', 'mine']}])
        for rel, ids in {'clients/one/app': ['core', 'org-one', 'mine'], 'private/app': ['core', 'mine']}.items():
            with self.subTest(rel):
                code, listed = self.cli_json('layers', 'list', '--project', str(self.project(rel)))
                self.assertEqual((code, [item['id'] for item in listed['layers']]), (0, ids))
                code, checked = self.cli_json('layers', 'check', '--project', str(self.project(rel)))
                self.assertEqual((code, checked['status']), (0, 'PASS'), checked)

    def test_core_added_without_rules_changes_no_rule(self):
        self.set_git_identity('new.user@example.test')
        core_bare, _ = self.source_repo('core', 'core', 'core', {'skills/base/SKILL.md': 'base\n'}, version=generator_version())
        code, report = self.add('core', 'core', core_bare)
        self.assertEqual((code, report['rules_updated']), (0, []), report)
        self.assertFalse([w for w in report['warnings'] if 'existing layer_rules' in w])

    def scenario(self, order):
        """K9 (review 2.4-1 major 1): only commands, no hand-written config: `layers add` core, `layers init` +
        `layers add` for two organisations and a personal layer, `layers use` for both organisations and
        `layers use mine --match '**'`; the result does not depend on the order of the `use` commands."""
        self.set_git_identity('new.user@example.test')
        core_bare, _ = self.source_repo('core', 'core', 'core', {'skills/base/SKILL.md': 'base\n'}, version=generator_version())
        self.assertEqual(self.add('core', 'core', core_bare)[0], 0)
        names = {'org-one': 'org', 'org-two': 'org', 'mine': 'personal'}
        for layer_id, kind in names.items():
            directory = self.work / 'repos' / layer_id
            code, report = self.cli_json('layers', 'init', str(directory), '--kind', kind, '--id', layer_id)
            self.assertEqual((code, report['status']), (0, 'PASS'), report)
            write(directory / 'skills' / f'{layer_id}-skill' / 'SKILL.md', f'{layer_id}\n')
            self.commit_all(directory)                                                   # `add` clones commits, as documented
            code, report = self.add(layer_id, kind, directory)
            self.assertEqual((code, report['status']), (0, 'PASS'), report)
            self.assertFalse(any('layer.yaml' in item for item in report['warnings']), report['warnings'])
        config = self.config()
        self.assertEqual([item['id'] for item in config['layers']], ['core', 'org-one', 'org-two', 'mine'])
        self.assertEqual(sorted(config['leakguard']['targets']), ['core', 'mine', 'org-one', 'org-two'])
        globs = {'org-one': f'{self.work}/clients/one/**', 'org-two': f'{self.work}/clients/two/**', 'mine': '**'}
        for layer_id in order:
            code, report = self.cli_json('layers', 'use', layer_id, '--match', globs[layer_id])
            self.assertEqual((code, report['status']), (0, 'PASS'), report)
            self.assertFalse([item for item in report['warnings'] if 'never matches' in item], report['warnings'])
        self.assertEqual(self.config()['layer_rules'],
                         [{'match': [globs['org-one']], 'use': ['core', 'org-one', 'mine']},
                          {'match': [globs['org-two']], 'use': ['core', 'org-two', 'mine']},
                          {'match': ['**'], 'use': ['core', 'mine']}])
        expected = {'clients/one/app': ['core', 'org-one', 'mine'], 'clients/two/app': ['core', 'org-two', 'mine'],
                    'private/app': ['core', 'mine']}
        for rel, ids in expected.items():
            with self.subTest(rel):
                code, report = self.cli_json('layers', 'list', '--project', str(self.project(rel)))
                self.assertEqual((code, [item['id'] for item in report['layers']]), (0, ids))
                code, report = self.cli_json('layers', 'check', '--project', str(self.project(rel)))
                self.assertEqual((code, report['status']), (0, 'PASS'), report)
                self.assertFalse([item for item in report['findings'] if item['severity'] in ('error', 'blocked')])
        # everything the commands created lives in the isolated machine, nothing under the real home
        for layer_id in ('core', 'org-one', 'org-two', 'mine'):
            self.assertTrue(str(self.default_checkout(layer_id)).startswith(str(self.root)))
        # the stubs allow the first commit of a new layer: nothing blocks it, coverage is generic
        mine = self.default_checkout('mine')
        write(mine / 'skills' / 'x' / 'SKILL.md', 'more\n')
        git(mine, 'add', '-A')
        self.assertEqual(git(mine, 'commit', '-q', '-m', 'second', check=False).returncode, 0)
        code, report = self.cli_json('leakcheck', str(mine))
        self.assertEqual((code, report['status'], report['target']), (0, 'PASS', 'mine'))


# ====================================================================== step 2.4t: layers use (K9)


class LayersUseTest(CommandsCase):
    def setUp(self):
        super().setUp()
        self.core = self.layer_dir('core', 'core', version=generator_version())
        self.acme = self.layer_dir('acme', 'org')
        self.beta = self.layer_dir('beta', 'org')
        self.me = self.layer_dir('me', 'personal')
        self.other_me = self.layer_dir('other-me', 'personal')
        self.layers = [('core', 'core', self.core), ('acme', 'org', self.acme), ('beta', 'org', self.beta),
                       ('me', 'personal', self.me), ('other-me', 'personal', self.other_me)]
        self.glob = f'{self.work}/clients/acme/**'

    def use(self, *args):
        return self.cli_json('layers', 'use', *args)

    def test_the_new_rule_takes_the_default_use_with_the_id_before_personal_and_goes_before_the_default(self):
        self.write_config(self.layers, [([f'{self.work}/x/**'], ['core']), (['**'], ['core', 'me'])])
        for mode in LINK_MODES:
            with self.subTest(mode), link_mode(mode):
                self.write_config(self.layers, [([f'{self.work}/x/**'], ['core']), (['**'], ['core', 'me'])])
                code, report = self.use('acme', '--match', self.glob)
                self.assertEqual((code, report['schema'], report['status'], report['index']), (0, 1, 'PASS', 1), report)
                self.assertEqual(report['rule'], {'match': [self.glob], 'use': ['core', 'acme', 'me']})
                rules = self.config()['layer_rules']
                self.assertEqual([rule['match'] for rule in rules], [[f'{self.work}/x/**'], [self.glob], ['**']])
                self.assertTrue(Path(report['backup']).is_file())
                code, listing = self.cli_json('layers', 'list', '--project', str(self.project('clients/acme/app')))
                self.assertEqual((code, [item['id'] for item in listing['layers']]), (0, ['core', 'acme', 'me']))

    def test_an_identical_rule_changes_nothing(self):
        self.write_config(self.layers, [(['**'], ['core', 'me'])])
        self.assertEqual(self.use('acme', '--match', self.glob)[0], 0)
        before, backups = self.config_file.read_bytes(), sorted(self.config_file.parent.glob('*.bak-*'))
        code, report = self.use('acme', '--match', self.glob)
        self.assertEqual((code, report['status'], report['changed']), (0, 'PASS', False), report)
        self.assertEqual((self.config_file.read_bytes(), sorted(self.config_file.parent.glob('*.bak-*'))), (before, backups))

    def test_position_is_a_zero_based_index(self):
        self.write_config(self.layers, [([f'{self.work}/x/**'], ['core']), (['**'], ['core', 'me'])])
        code, report = self.use('acme', '--match', self.glob, '--position', '0')
        self.assertEqual((code, report['index']), (0, 0), report)
        self.assertEqual(self.config()['layer_rules'][0]['match'], [self.glob])
        self.assertFalse(report['warnings'])
        code, report = self.use('beta', '--match', f'{self.work}/late/**', '--position', '3')    # after `**`
        self.assertEqual((code, report['index']), (0, 3), report)
        self.assertTrue(any('never matches' in item for item in report['warnings']), report['warnings'])

    def test_refusals_are_blocked_and_leave_the_config_unchanged(self):
        self.write_config(self.layers, [(['**'], ['core', 'me'])])
        before = self.config_file.read_bytes()
        for label, args in {'core': ('core', '--match', self.glob), 'unknown id': ('ghost', '--match', self.glob),
                            'position too large': ('acme', '--match', self.glob, '--position', '2'),
                            'negative position': ('acme', '--match', self.glob, '--position', '-1')}.items():
            with self.subTest(label):
                code, report = self.use(*args)
                self.assertEqual((code, report['status'], report['code']), (2, 'BLOCKED', 'bad-argument'), report)
        self.assertEqual(self.config_file.read_bytes(), before)
        self.assertEqual(self.cli('layers', 'use', 'acme').returncode, 2)                    # --match is required

    def test_a_personal_id_replaces_the_personal_layer(self):
        self.write_config(self.layers, [(['**'], ['core', 'acme', 'me'])])
        code, report = self.use('other-me', '--match', self.glob)
        self.assertEqual((code, report['rule']['use']), (0, ['core', 'acme', 'other-me']), report)

    def test_without_a_default_rule_the_base_is_core_plus_the_only_personal_layer(self):
        single = [item for item in self.layers if item[0] != 'other-me']
        self.write_config(single, [([f'{self.work}/x/**'], ['core'])])
        code, report = self.use('acme', '--match', self.glob)
        self.assertEqual((code, report['rule']['use'], report['index']), (0, ['core', 'acme', 'me'], 1), report)
        self.assertFalse([item for item in report['warnings'] if 'personal' in item], report['warnings'])
        self.write_config(self.layers, [([f'{self.work}/x/**'], ['core'])])            # two personal layers
        code, report = self.use('acme', '--match', self.glob)
        self.assertEqual((code, report['rule']['use']), (0, ['core', 'acme']), report)
        warning = [item for item in report['warnings'] if 'several personal layers' in item]
        self.assertEqual(len(warning), 1, report['warnings'])
        self.assertIn('layers use', warning[0])
        code, report = self.use('me', '--match', f'{self.work}/mine/**')               # a personal id: no warning
        self.assertEqual((code, report['rule']['use']), (0, ['core', 'me']), report)
        self.assertFalse([item for item in report['warnings'] if 'personal' in item], report['warnings'])

    def test_a_config_without_layers_is_blocked(self):
        self.config_file.write_text(yaml_text({'library': str(self.core)}), encoding='utf-8')
        code, report = self.use('acme', '--match', self.glob)
        self.assertEqual((code, report['status'], report['code']), (2, 'BLOCKED', 'legacy-config'))
        self.assertIn('layers migrate', report['reason'])


# ====================================================================== step 2.4t: layers check (K2, K4, K9, K12)


class LayersCheckTest(CommandsCase):
    def setUp(self):
        super().setUp()
        self.core = self.layer_dir('core', 'core', version=generator_version())
        self.acme = self.git_layer('acme', 'org')
        leakguard.install_hook(self.acme, target='acme')
        self.app = self.project('Repos/app')

    def configure(self, *extra, rules=None):
        entries = [('core', 'core', self.core), ('acme', 'org', self.acme), *extra]
        self.write_config(entries, rules or [(['**'], [item[0] for item in entries])])

    def check(self, *args, project=None):
        return self.cli_json('layers', 'check', '--project', str(project or self.app), *args)

    def test_pass_with_the_json_schema_and_a_status_per_layer(self):
        self.configure()
        for mode in LINK_MODES:
            with self.subTest(mode), link_mode(mode):
                code, report = self.check()
                self.assertEqual((code, report['schema'], report['status']), (0, 1, 'PASS'), report)
                for item in report['findings']:
                    self.assertEqual(sorted(item), ['code', 'layer', 'message', 'severity'])
                self.assertEqual([(item['id'], item['status']) for item in report['layers']],
                                 [('core', 'PASS'), ('acme', 'PASS')])
                self.assertEqual(report['rule'], 0)
        process = self.cli('layers', 'check', '--project', str(self.app))
        self.assertEqual(process.returncode, 0)
        self.assertIn('layers check: PASS', process.stdout)

    def test_an_error_finding_is_fail_exit_1_and_marks_its_layer(self):
        bare = self.git_layer('beta', 'org')                                              # writable, no hooks
        self.configure(('beta', 'org', bare))
        code, report = self.check()
        self.assertEqual((code, report['status']), (1, 'FAIL'), report)
        self.assertIn('hook-missing', codes(report['findings']))
        self.assertEqual({item['id']: item['status'] for item in report['layers']}['beta'], 'FAIL')
        self.assertEqual({item['id']: item['status'] for item in report['layers']}['acme'], 'PASS')

    def test_core_min_above_the_generator_is_blocked(self):
        self.core = self.layer_dir('core-new', 'core', version='99.0.0', core_min='99.0.0')
        self.configure()
        code, report = self.check()
        self.assertEqual((code, report['status']), (2, 'BLOCKED'), report)

    def test_no_rule_and_no_layers_are_blocked_with_the_finding(self):
        self.configure(rules=[([f'{self.work}/elsewhere/**'], ['core'])])
        code, report = self.check()
        self.assertEqual((code, report['status'], report['code']), (2, 'BLOCKED', 'no-rule'), report)
        self.assertEqual([(item['code'], item['severity']) for item in report['findings']], [('no-rule', 'blocked')])
        self.assertIn('layers use', report['reason'])
        self.config_file.unlink()
        code, report = self.check()
        self.assertEqual((code, report['code']), (2, 'missing-config'))

    def test_an_absent_layer_is_not_run_and_does_not_change_the_status(self):
        ghost = self.work / 'layers' / 'ghost'
        self.configure(('ghost', 'org', ghost))
        code, report = self.check()
        self.assertEqual((code, report['status']), (0, 'PASS'), report)
        self.assertEqual({item['id']: item['status'] for item in report['layers']}['ghost'], 'NOT_RUN')

    def test_layer_flag_replaces_the_configured_layers(self):
        self.configure()
        code, report = self.cli_json('layers', 'check', '--layer', f'core={self.core}', cwd=self.project('loose/x'))
        self.assertEqual((code, [item['id'] for item in report['layers']]), (0, ['core']), report)


# ====================================================================== step 2.4t: --isolation (K10)


class IsolationTest(CommandsCase):
    def setUp(self):
        super().setUp()
        self.core = self.layer_dir('core', 'core', version=generator_version())
        self.acme = self.layer_dir('acme', 'org')
        self.repos = self.work / 'Repos'
        self.repos.mkdir()

    def configure(self, allowed=('core', 'acme'), extra_layers=(), **extra):
        data = {'isolation': {'allowed_layers': list(allowed)}} if allowed is not None else {}
        data.update(extra)
        self.write_config([('core', 'core', self.core), ('acme', 'org', self.acme), *extra_layers],
                          [(['**'], ['core', 'acme'])], discover_paths=[str(self.repos)], **data)

    def isolation(self, code=None):
        exit_code, report = self.cli_json('layers', 'check', '--isolation')
        if code is not None:
            self.assertEqual(exit_code, code, report)
        return exit_code, report

    def assert_fail(self, expected_code):
        code, report = self.isolation()
        self.assertEqual((code, report['status']), (1, 'FAIL'), report)
        self.assertIn(expected_code, codes(report['findings']), report)
        return report

    def test_pass_when_only_allowed_layers_are_on_the_machine(self):
        self.configure()
        for mode in LINK_MODES:
            with self.subTest(mode), link_mode(mode):
                code, report = self.isolation()
                self.assertEqual((code, report['schema'], report['status'], report['isolation']), (0, 1, 'PASS', True), report)
                self.assertEqual(report['allowed_layers'], ['core', 'acme'])
                self.assertEqual(report['findings'], [])

    def test_not_run_without_an_allowed_list(self):
        self.configure(allowed=None)
        code, report = self.isolation()
        self.assertEqual((code, report['status']), (0, 'NOT_RUN'), report)
        self.assertIn('allowed_layers', report['reason'])

    def test_a_configured_layer_outside_the_list_fails(self):
        self.configure(allowed=('core',))
        report = self.assert_fail('isolation-layer-not-allowed')
        self.assertEqual(by_code(report['findings'], 'isolation-layer-not-allowed')[0]['layer'], 'acme')

    def test_a_layer_whose_layer_yaml_declares_a_foreign_id_fails(self):
        disguised = make_layer(self.work / 'disguised', 'foreign', 'org')
        self.configure(allowed=('core', 'acme', 'disguised'), extra_layers=[('disguised', 'org', disguised)])
        self.assert_fail('isolation-foreign-library')

    def test_a_layer_without_layer_yaml_and_the_bridge_fail(self):
        bare = self.work / 'no-meta'
        bare.mkdir()
        self.configure(allowed=('core', 'acme', 'nometa'), extra_layers=[('nometa', 'org', bare)])
        self.assert_fail('isolation-no-layer-yaml')
        old = self.work / 'old-library'
        old.mkdir()
        self.config_file.write_text(yaml_text({
            'layers': [{'id': 'core', 'kind': 'core', 'path': str(old), 'writable': False,
                        'synthetic': {'version': generator_version(), 'core_min': generator_version()}}],
            'layer_rules': [{'match': ['**'], 'use': ['core']}], 'isolation': {'allowed_layers': ['core']}}),
            encoding='utf-8')
        self.assert_fail('isolation-legacy-bridge')

    def test_the_bridge_fails_even_when_its_directory_is_absent(self):
        """Review 2.4-1: the bridge in the config is FAIL on purpose (K15), also without its directory on disk."""
        self.config_file.write_text(yaml_text({
            'layers': [{'id': 'core', 'kind': 'core', 'path': str(self.work / 'gone-old-library'), 'writable': False,
                        'synthetic': {'version': generator_version(), 'core_min': generator_version()}}],
            'layer_rules': [{'match': ['**'], 'use': ['core']}], 'isolation': {'allowed_layers': ['core']}}),
            encoding='utf-8')
        report = self.assert_fail('isolation-legacy-bridge')
        self.assertEqual(by_code(report['findings'], 'isolation-legacy-bridge')[0]['layer'], 'core')

    def test_project_and_layer_flags_are_ignored_with_a_warning(self):
        self.configure()
        code, report = self.cli_json('layers', 'check', '--isolation', '--project', str(self.project('Repos/app')))
        self.assertEqual((code, report['status']), (0, 'PASS'), report)
        self.assertTrue([item for item in report['warnings'] if '--project' in item], report)
        code, report = self.cli_json('layers', 'check', '--isolation', '--layer', f'core={self.core}')
        self.assertTrue([item for item in report['warnings'] if '--layer' in item], report)
        code, report = self.cli_json('layers', 'check', '--isolation')
        self.assertFalse(report.get('warnings'), report)

    def test_foreign_checkouts_in_the_layers_directory_fail(self):
        self.configure()
        layers_dir = machine_data_dir() / 'layers'
        make_layer(layers_dir / 'foreign', 'foreign', 'org')
        self.assert_fail('isolation-foreign-library')
        shutil.rmtree(layers_dir / 'foreign')
        (layers_dir / 'stray').mkdir()
        self.assert_fail('isolation-unknown-checkout')
        shutil.rmtree(layers_dir / 'stray')
        make_layer(layers_dir / 'acme-copy', 'acme', 'org')                     # an allowed id elsewhere is fine
        self.isolation(code=0)

    def test_old_library_clones_fail(self):
        cases = {
            'legacy_library': lambda old: self.configure(legacy_library=str(old)),
            'old library key': lambda old: self.configure(library=str(old)),
        }
        for label, setup in cases.items():
            with self.subTest(label):
                old = self.work / f'old-{label.replace(" ", "-")}'
                write(old / 'skills' / 'x' / 'SKILL.md', 'x\n')
                setup(old)
                self.assert_fail('isolation-legacy-library')
                shutil.rmtree(old)
                self.isolation(code=0)
        self.configure()
        known = Path(self.env['HOME']) / '.local' / 'copilot-library'
        write(known / 'skills' / 'x' / 'SKILL.md', 'x\n')
        self.assert_fail('isolation-legacy-library')
        shutil.rmtree(known)
        write(self.repos / 'some-org' / 'CopilotSkills' / 'skills' / 'x' / 'SKILL.md', 'x\n')
        self.assert_fail('isolation-legacy-library')
        shutil.rmtree(self.repos / 'some-org')
        self.isolation(code=0)

    def test_the_copilotskills_search_has_a_depth_limit_and_skips_hidden_directories(self):
        self.configure()
        for rel in ('a/b/c/d/CopilotSkills', '.hidden/CopilotSkills', 'a/.cache/CopilotSkills'):
            write(self.repos / rel / 'skills' / 'x' / 'SKILL.md', 'x\n')
        self.isolation(code=0)                                     # five levels down and hidden: not searched
        write(self.repos / 'a' / 'b' / 'c' / 'copilotskills' / 'skills' / 'x' / 'SKILL.md', 'x\n')
        report = self.assert_fail('isolation-legacy-library')       # four levels down: found (any case)
        self.assertEqual(len(by_code(report['findings'], 'isolation-legacy-library')), 1, report)

    def test_an_invalid_layer_yaml_fails(self):
        self.configure()
        layers_dir = machine_data_dir() / 'layers'
        write(layers_dir / 'broken' / 'layer.yaml', 'schema: 1\nid: Bad Id\nkind: org\n')
        self.assert_fail('isolation-layer-yaml-invalid')
        shutil.rmtree(layers_dir / 'broken')
        write(self.acme / 'layer.yaml', 'schema: [\n')
        self.assert_fail('isolation-layer-yaml-invalid')

    def test_a_foreign_identity_file_fails(self):
        identities = self.work / 'identities'
        write(identities / 'client.yaml', yaml_text({'schema': 1, 'layer': 'client', 'terms': [{'term': CLIENT_BETA}]}))
        self.configure(leakguard={'identity_dir': str(identities)})
        self.assert_fail('isolation-foreign-identity')
        write(identities / 'client.yaml', yaml_text({'schema': 1, 'layer': 'acme', 'terms': []}))
        self.isolation(code=0)
        write(self.acme / 'leakguard' / 'other.yaml', yaml_text({'schema': 1, 'layer': 'client', 'terms': []}))
        self.assert_fail('isolation-foreign-identity')
        # D190: an identity file declaring only `empty` (no `terms` key) is an identity file too
        write(self.acme / 'leakguard' / 'other.yaml', yaml_text({'schema': 1, 'layer': 'client', 'empty': 'celowo pusta'}))
        self.assert_fail('isolation-foreign-identity')

    def test_the_layers_directory_follows_the_system(self):
        """K10: Linux for real, macOS and Windows by passing the platform (in-process, isolated HOME/LOCALAPPDATA)."""
        self.configure()
        home = Path(self.env['HOME'])
        dirs = {'Linux': Path(self.env['XDG_DATA_HOME']) / 'skillsync' / 'layers',
                'Darwin': home / 'Library' / 'Application Support' / 'skillsync' / 'layers',
                'Windows': Path(self.env['LOCALAPPDATA']) / 'skillsync' / 'layers'}
        for system, directory in dirs.items():
            with self.subTest(system=system):
                self.assertEqual(layers.default_layers_dir(system), directory)
                make_layer(directory / 'foreign', 'foreign', 'org')
                for other in dirs:
                    report = layers.layers_check(isolation=True, system=other)
                    expected = 'FAIL' if other == system else 'PASS'
                    self.assertEqual(report['status'], expected, (system, other, report))
                shutil.rmtree(directory / 'foreign')

    def test_layers_list_isolation_adds_the_field_and_keeps_its_own_status(self):
        self.configure(allowed=('core',))
        code, report = self.cli_json('layers', 'list', '--isolation', '--project', str(self.project('Repos/app')))
        self.assertEqual((code, report['status']), (0, 'PASS'), report)
        self.assertEqual(report['isolation']['status'], 'FAIL')
        self.assertIn('isolation-layer-not-allowed', codes(report['isolation']['findings']))
        code, plain = self.cli_json('layers', 'list', '--project', str(self.project('Repos/app2')))
        self.assertNotIn('isolation', plain)


# ====================================================================== step 2.4t: target stub of `layers init` filled by `layers add`


class TargetStubFillTest(CommandsCase):
    def test_add_fills_the_empty_remote_match_of_an_init_stub(self):
        self.set_git_identity('jan@acme.example')
        directory = self.work / 'repos' / 'mine'
        self.assertEqual(self.cli_json('layers', 'init', str(directory), '--kind', 'personal', '--id', 'mine')[0], 0)
        self.assertEqual(self.config()['leakguard']['targets']['mine']['remote_match'], [])
        git(directory, 'add', '-A')
        commit(directory, 'content')
        code, report = self.add('mine', 'personal', directory)
        self.assertEqual(code, 0, report)
        stub = self.config()['leakguard']['targets']['mine']
        self.assertEqual(stub, {'scope': 'private', 'remote_match': [str(directory)], 'authors': ['jan@acme.example']})
        self.assertTrue(any('remote_match filled' in item for item in report['warnings']), report['warnings'])
        code, report = self.cli_json('leakcheck', str(self.default_checkout('mine')))
        self.assertEqual((code, report['target']), (0, 'mine'), report)

    def test_a_stub_with_a_value_or_without_the_key_is_left_unchanged(self):
        bare, _ = self.source_repo('acme', 'acme', 'org')
        cases = {'with a value': {'scope': 'org-private', 'remote_match': ['example.test/keep'], 'authors': []},
                 'without the key': {'scope': 'org-private', 'authors': ['keep@example.test']}}
        for label, stub in cases.items():
            with self.subTest(label):
                self.config_file.write_text(yaml_text({'leakguard': {'targets': {'acme': stub}}}), encoding='utf-8')
                shutil.rmtree(self.default_checkout('acme'), ignore_errors=True)
                code, report = self.add('acme', 'org', bare)
                self.assertEqual(code, 0, report)
                self.assertEqual(self.config()['leakguard']['targets']['acme'], stub)
                self.assertTrue(any('already exists' in item for item in report['warnings']), report['warnings'])


if __name__ == '__main__':
    unittest.main()
