"""Step 2.1t: machine config, layer selection (K3), `layer.yaml` and versions (K12),
leak guard sources taken from the layers (K7/K8, N5-2).

The tests follow spec A (spec zadania (poza rdzeniem)) and the decisions of
the orchestrator in the plan ("Wynik"): `severity` has a `blocked` level, `allow_override`
format, glob rules. All layer, client and project names are synthetic; terms the guard must
catch are composed at runtime. Every test runs in an isolated environment (layers_helpers).
"""
import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from layers_helpers import (LINK_MODES, LayersCase, ROOT, commit, git, link_mode, machine_data_dir, make_layer,  # noqa: E402
                            write, yaml_text)
from skillsync_workflow import layers, leakguard  # noqa: E402
from layers_helpers import needs_os_symlinks, require_os_symlinks  # noqa: E402

CLIENT_BETA = 'Brenk' + 'or'          # synthetic client term (identity of layer `beta`)
CLIENT_GAMMA = 'Zulv' + 'ek'


def codes(findings):
    return {item['code'] for item in findings}


def by_code(findings, code):
    return [item for item in findings if item['code'] == code]


class ConfigCase(LayersCase):
    """Config + selection helpers."""

    def setup_three(self, with_rules=True):
        """core + acme (org) + me (personal) layers, rules: Acme projects get all three, the rest core+me."""
        self.core = self.layer_dir('core', 'core')
        self.acme = self.layer_dir('acme', 'org')
        self.me = self.layer_dir('me', 'personal')
        self.repos = self.work / 'Repos'
        rules = [([f'{self.repos}/Acme/**'], ['core', 'acme', 'me']), ([f'{self.repos}/**'], ['core', 'me'])]
        self.write_config([('core', 'core', self.core), ('acme', 'org', self.acme), ('me', 'personal', self.me)],
                          rules if with_rules else ())
        return self.project('Repos/Acme/app')

    def ids_for(self, project, **kwargs):
        return layers.resolve_layers(project, **kwargs).ids


# ====================================================================== config parsing and validation


class ConfigParsingTest(ConfigCase):
    def full_raw(self):
        return {
            'layers': [
                {'id': 'core', 'kind': 'core', 'source': 'https://example.test/owner/skillsync', 'subdir': 'library',
                 'ref': 'v1.0.0', 'path': str(self.work / 'c')},
                {'id': 'acme', 'kind': 'org', 'source': 'git@example.test:acme/skills.git'},
                {'id': 'me', 'kind': 'personal', 'source': str(self.work / 'mine'), 'writable': False},
            ],
            'layer_rules': [{'match': ['~/Repos/Acme/**'], 'use': ['core', 'acme', 'me']},
                            {'match': ['**'], 'use': ['core', 'me']}],
            'isolation': {'allowed_layers': ['core', 'acme', 'me']},
            'leakguard': {'identity_dir': str(self.work / 'ops'), 'trailer_allow': ['bot@example.test'],
                          'targets': {'acme': {'scope': 'org-private', 'remote_match': ['example.test:acme/*'],
                                               'authors': ['jan@acme.example']},
                                      'core': {'scope': 'public', 'authors': ['owner@example.com']}}},
            'allow_override': ['validation.checks:unit'],
            'legacy_library': str(self.work / 'old-library'),
            'tags': ['kept'], 'discover_paths': ['~/Repos'],
        }

    def test_all_keys_are_parsed(self):
        config = layers.parse_machine_config(self.full_raw(), self.config_file)
        self.assertEqual(list(config.layers), ['core', 'acme', 'me'])
        core = config.layers['core']
        self.assertEqual((core.kind, core.subdir, core.ref, core.source), ('core', 'library', 'v1.0.0',
                                                                          'https://example.test/owner/skillsync'))
        self.assertEqual(core.root, self.work / 'c' / 'library')
        self.assertEqual([rule['use'] for rule in config.rules], [['core', 'acme', 'me'], ['core', 'me']])
        self.assertEqual(config.allowed_layers, ['core', 'acme', 'me'])
        self.assertEqual(config.identity_dir, self.work / 'ops')
        self.assertEqual(config.trailer_allow, ['bot@example.test'])
        self.assertEqual(config.allow_override, ['validation.checks:unit'])
        self.assertEqual(config.legacy_library, self.work / 'old-library')
        self.assertEqual(config.targets['acme']['authors'], ['jan@acme.example'])
        self.assertEqual(config.targets_with_kind()['acme']['kind'], 'org')
        self.assertEqual(config.warnings, [])

    def test_writable_defaults_follow_the_kind(self):
        config = layers.parse_machine_config(self.full_raw(), self.config_file)
        self.assertEqual({lid: layer.writable for lid, layer in config.layers.items()},
                         {'core': False, 'acme': True, 'me': False})   # `me` sets writable: false explicitly
        defaults = layers.parse_machine_config({'layers': [{'id': 'a', 'kind': 'personal', 'source': 's'},
                                                           {'id': 'c', 'kind': 'core', 'source': 's'}]}, None)
        self.assertEqual({lid: layer.writable for lid, layer in defaults.layers.items()}, {'a': True, 'c': False})

    def test_missing_path_uses_the_default_checkout_dir(self):
        config = layers.parse_machine_config({'layers': [{'id': 'acme', 'kind': 'org', 'source': 's'}]}, None)
        expected = machine_data_dir() / 'layers' / 'acme'
        self.assertEqual(config.layers['acme'].path, expected)

    def test_tilde_in_path_is_expanded(self):
        config = layers.parse_machine_config({'layers': [{'id': 'a', 'kind': 'org', 'path': '~/lay/a'}]}, None)
        self.assertEqual(config.layers['a'].path, Path(self.env['HOME']) / 'lay' / 'a')

    def test_invalid_configs_are_rejected(self):
        absolute = str(self.work / 'p')
        good = {'id': 'a', 'kind': 'org', 'source': 's'}
        cases = {
            'bad id (upper case)': {'layers': [dict(good, id='Acme')]},
            'bad id (leading dash)': {'layers': [dict(good, id='-a')]},
            'unknown kind': {'layers': [dict(good, kind='team')]},
            'no source and no path': {'layers': [{'id': 'a', 'kind': 'org'}]},
            'subdir escapes': {'layers': [dict(good, subdir='../x')]},
            'absolute subdir': {'layers': [dict(good, subdir='/x')]},
            'ref starting with a dash': {'layers': [dict(good, ref='--upload-pack=x')]},
            'relative path': {'layers': [dict(good, path='relative/dir')]},
            'writable not boolean': {'layers': [dict(good, writable='yes')]},
            'synthetic on a non-core layer': {'layers': [dict(good, synthetic={'version': '1.0.0', 'core_min': '1.0.0'})]},
            'synthetic and writable': {'layers': [{'id': 'core', 'kind': 'core', 'path': absolute, 'writable': True,
                                                   'synthetic': {'version': '1.0.0', 'core_min': '1.0.0'}}]},
            'synthetic without a version': {'layers': [{'id': 'core', 'kind': 'core', 'path': absolute,
                                                        'synthetic': {'core_min': '1.0.0'}}]},
            'duplicate id': {'layers': [good, dict(good)]},
            'two core layers': {'layers': [{'id': 'c1', 'kind': 'core', 'source': 's'},
                                           {'id': 'c2', 'kind': 'core', 'source': 's'}]},
            'layers is not a list': {'layers': {'a': 1}},
            'rule use names an unknown layer': {'layers': [good], 'layer_rules': [{'match': ['**'], 'use': ['zzz']}]},
            'rule lists a layer twice': {'layers': [good], 'layer_rules': [{'match': ['**'], 'use': ['a', 'a']}]},
            'rule has two personal layers': {'layers': [{'id': 'p1', 'kind': 'personal', 'source': 's'},
                                                        {'id': 'p2', 'kind': 'personal', 'source': 's'}],
                                             'layer_rules': [{'match': ['**'], 'use': ['p1', 'p2']}]},
            'rule is not a mapping': {'layers': [good], 'layer_rules': ['**']},
            'isolation is not a mapping': {'isolation': ['core']},
            'target with a bad scope': {'leakguard': {'targets': {'a': {'scope': 'secret'}}}},
            'allow_override is not a list': {'allow_override': {'a': 1}},
            'identity_dir is relative': {'leakguard': {'identity_dir': 'ops'}},
        }
        for label, raw in cases.items():
            with self.subTest(label):
                with self.assertRaises(layers.LayerError) as caught:
                    layers.parse_machine_config(raw, None)
                self.assertEqual(caught.exception.status, 'BLOCKED')

    def test_unknown_leakguard_key_is_a_warning_not_an_error(self):
        config = layers.parse_machine_config({'leakguard': {'targets': {}, 'surprise': 1}}, None)
        self.assertTrue(any('surprise' in item for item in config.warnings))

    def test_load_missing_empty_and_invalid_files(self):
        missing = layers.load_machine_config(self.work / 'nothing.yaml')
        self.assertFalse(missing.exists)
        self.assertEqual(missing.layers, {})
        self.config_file.write_text('layers: [unclosed\n', encoding='utf-8')
        with self.assertRaises(layers.LayerError):
            layers.load_machine_config()
        self.config_file.write_text('- just\n- a list\n', encoding='utf-8')
        with self.assertRaises(layers.LayerError):
            layers.load_machine_config()

    def test_config_path_reads_the_environment_each_time(self):
        self.assertEqual(layers.config_path(), self.config_file)
        with mock.patch.dict(os.environ, {'SKILLSYNC_CONFIG': ''}):
            self.assertEqual(layers.config_path(), Path(self.env['HOME']) / '.config' / 'skillsync' / 'config.yaml')

    def test_other_keys_are_not_touched(self):
        config = layers.parse_machine_config(self.full_raw(), self.config_file)
        self.assertEqual(config.raw['tags'], ['kept'])
        self.assertEqual(config.raw['discover_paths'], ['~/Repos'])


class HelperControlTest(LayersCase):
    def test_link_modes_toggle_the_environment_variable_children_inherit(self):
        for mode in LINK_MODES:
            with self.subTest(mode), link_mode(mode):
                expected = '1' if mode == 'copy' else None
                self.assertEqual(os.environ.get('SKILLSYNC_NO_SYMLINK'), expected)
                import subprocess
                seen = subprocess.run([sys.executable, '-c', 'import os; print(os.environ.get("SKILLSYNC_NO_SYMLINK"))'],
                                      capture_output=True, text=True, env=dict(os.environ)).stdout.strip()
                self.assertEqual(seen, expected or 'None')

    def test_machine_state_is_redirected_into_the_temporary_directory(self):
        for name in ('HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'LOCALAPPDATA', 'GIT_CONFIG_GLOBAL', 'SKILLSYNC_CONFIG'):
            self.assertTrue(os.environ[name].startswith(str(self.root)), name)
        self.assertEqual(os.environ['GIT_CONFIG_NOSYSTEM'], '1')


class LegacyConfigTest(ConfigCase):
    """D41: no `library:` mode; the old key is only detected."""

    def test_library_only_config_is_blocked_with_a_migration_hint(self):
        self.config_file.write_text(f'library: {self.work / "old"}\ntags: [x]\n', encoding='utf-8')
        with self.assertRaises(layers.LayerError) as caught:
            layers.resolve_layers(self.project('p'))
        self.assertEqual((caught.exception.code, caught.exception.status), ('legacy-config', 'BLOCKED'))
        self.assertIn('layers migrate', str(caught.exception))

    def test_key_is_detected_even_next_to_layers_and_never_used_as_a_layer(self):
        core = self.layer_dir('core', 'core')
        self.write_config([('core', 'core', core)], [(['**'], ['core'])], library=str(self.work / 'old'))
        config = layers.load_machine_config()
        self.assertEqual(config.legacy_library_key, str(self.work / 'old'))
        self.assertEqual(layers.resolve_layers(self.project('p')).ids, ['core'])
        self.assertEqual(layers.legacy_library_key({'library': '  '}), None)
        self.assertEqual(layers.legacy_library_key({}), None)

    def test_missing_config_and_empty_layers_are_blocked(self):
        with self.assertRaises(layers.LayerError) as missing:
            layers.resolve_layers(self.project('p'))
        self.assertEqual(missing.exception.code, 'missing-config')
        self.config_file.write_text('tags: [x]\n', encoding='utf-8')
        with self.assertRaises(layers.LayerError) as empty:
            layers.resolve_layers(self.project('p'))
        self.assertEqual(empty.exception.code, 'no-layers')

    def test_cli_reports_blocked_with_exit_2(self):
        self.config_file.write_text(f'library: {self.work / "old"}\n', encoding='utf-8')
        project = self.project('p')
        for mode in LINK_MODES:
            with self.subTest(mode), link_mode(mode):
                code, report = self.cli_json('layers', 'list', '--project', str(project))
                self.assertEqual((code, report['status'], report['code']), (2, 'BLOCKED', 'legacy-config'))


# ====================================================================== default paths per system


class DefaultPathsTest(ConfigCase):
    def test_linux_paths(self):
        data, cache = Path(self.env['XDG_DATA_HOME']), Path(self.env['XDG_CACHE_HOME'])
        self.assertEqual(layers.default_layer_path('acme', 'Linux'), data / 'skillsync' / 'layers' / 'acme')
        self.assertEqual(layers.composed_root('Linux'), cache / 'skillsync' / 'composed')

    def test_linux_falls_back_to_home_when_xdg_is_unset(self):
        home = Path(self.env['HOME'])
        with mock.patch.dict(os.environ):
            os.environ.pop('XDG_DATA_HOME')
            os.environ.pop('XDG_CACHE_HOME')
            self.assertEqual(layers.default_layer_path('a', 'Linux'), home / '.local/share/skillsync/layers/a')
            self.assertEqual(layers.composed_root('Linux'), home / '.cache/skillsync/composed')

    def test_relative_xdg_value_is_ignored(self):
        home = Path(self.env['HOME'])
        with mock.patch.dict(os.environ, {'XDG_DATA_HOME': 'relative/dir'}):
            self.assertEqual(layers.default_layer_path('a', 'Linux'), home / '.local/share/skillsync/layers/a')

    def test_macos_paths(self):
        home = Path(self.env['HOME'])
        self.assertEqual(layers.default_layer_path('acme', 'Darwin'),
                         home / 'Library/Application Support/skillsync/layers/acme')
        self.assertEqual(layers.composed_root('Darwin'), home / 'Library/Caches/skillsync/composed')

    def test_windows_paths(self):
        local = Path(self.env['LOCALAPPDATA'])
        self.assertEqual(layers.default_layer_path('acme', 'Windows'), local / 'skillsync' / 'layers' / 'acme')
        self.assertEqual(layers.composed_root('Windows'), local / 'skillsync' / 'cache' / 'composed')

    def test_current_system_decides_the_default_path_of_a_config_entry(self):
        raw = {'layers': [{'id': 'acme', 'kind': 'org', 'source': 's'}]}
        for system, tail in (('Darwin', 'Library/Application Support/skillsync/layers/acme'),
                             ('Windows', 'skillsync/layers/acme')):
            with self.subTest(system), mock.patch.object(layers, 'current_system', return_value=system):
                path = layers.parse_machine_config(raw, None).layers['acme'].path
                self.assertTrue(path.as_posix().endswith(tail), path)


# ====================================================================== selection by project path (K3)


class GlobMatchingTest(ConfigCase):
    def match(self, path, pattern, system):
        """`path_matches` for the given system without the host file system: `realpath` is the identity,
        so the tables give the same result on Linux, macOS and Windows hosts."""
        with mock.patch.object(os.path, 'realpath', side_effect=lambda value, **kwargs: str(value)):
            return layers.path_matches(path, pattern, system)

    def test_glob_table(self):
        table = [
            ('/r/Acme/app', '/r/Acme/*', True),
            ('/r/Acme/app/sub', '/r/Acme/*', False),          # `*` stays inside one segment
            ('/r/Acme/app/sub', '/r/Acme/**', True),
            ('/r/Acme/a/b/c', '/r/Acme/**', True),
            ('/r/Acme', '/r/Acme/**', False),                  # `a/**` does not include `a`
            ('/r/Acme', '/r/Acme', True),
            ('/r/Acme/app', '/r/Ac?e/app', True),
            ('/r/Acme/app', '/r/Ac??e/app', False),
            ('/r/AcmeX/app', '/r/Acme/*', False),              # no partial segment match
            ('/r/Acme-1/app', '/r/Acme*/app', True),
            ('/r/Acme/x/y/app', '/r/**/app', True),
            ('/r/app', '/r/**/app', True),                     # `**` also matches zero segments in the middle
            ('/anything/at/all', '**', True),
            ('/x/Acme/p', 'Acme/*', True),                     # a relative pattern matches anywhere
            ('/x/Acme/p/q', 'Acme/*', False),
            ('/r/a.b/x', '/r/a.b/*', True),                    # regex characters are literal
            ('/r/axb/x', '/r/a.b/*', False),
            ('/r/a+b/x', '/r/a+b/*', True),
            ('/r/Acme/app', '/r/acme/*', False),               # Linux is case sensitive
        ]
        leading = [
            ('/r/Acme/app', '**/Acme/*', True),                # a leading `**/` also covers the root
            ('/Acme/app', '**/Acme/*', True),
            ('/x/Acme/a/b', '**/Acme/**', True),
            ('/Acme/a', '**/Acme/**', True),
            ('/x/Acme', '**/Acme/**', False),
            ('/x/Other/app', '**/Acme/*', False),
            ('/x/Acme/a/b', '**/Acme/*', False),
        ]
        for system in ('Linux', 'Darwin', 'Windows'):
            for path, pattern, expected in leading:
                with self.subTest(system=system, path=path, pattern=pattern):
                    self.assertEqual(self.match(path, pattern, system), expected)
        for path, pattern, expected in table:
            with self.subTest(path=path, pattern=pattern):
                self.assertEqual(self.match(path, pattern, 'Linux'), expected)

    def test_case_is_folded_on_windows_and_macos_only(self):
        for system, expected in (('Linux', False), ('Darwin', True), ('Windows', True)):
            with self.subTest(system):
                self.assertEqual(self.match('/R/Acme/App', '/r/acme/*', system), expected)

    def test_windows_accepts_backslash_patterns_other_systems_do_not(self):
        self.assertTrue(self.match('/r/Acme/app', '\\r\\acme\\*', 'Windows'))
        self.assertFalse(self.match('/x/r/Acme/app', '\\r\\acme\\*', 'Windows'))      # still anchored at the root
        self.assertFalse(self.match('/r/Acme/app', '\\r\\acme\\*', 'Linux'))

    def test_tilde_is_expanded(self):
        def expand(value):
            return '/home/user' + value[1:] if value.startswith('~') else value
        with mock.patch.object(os.path, 'expanduser', expand):
            self.assertTrue(self.match('/home/user/Repos/Acme/app', '~/Repos/Acme/**', 'Linux'))
            self.assertFalse(self.match('/home/user/Repos/Acme/app', '~/Repos/Other/**', 'Linux'))
            self.assertFalse(self.match('/home/runner/Repos/Acme/app', '~/Repos/Acme/**', 'Linux'))

    @unittest.skipUnless(hasattr(os, 'symlink') and os.name == 'posix', 'symlink creation needs POSIX')
    def test_match_uses_the_resolved_path(self):
        real = self.work / 'real' / 'Acme' / 'app'
        real.mkdir(parents=True)
        (self.work / 'links').symlink_to(self.work / 'real', target_is_directory=True)
        through_link = self.work / 'links' / 'Acme' / 'app'
        self.assertTrue(layers.path_matches(through_link, f'{self.work}/real/Acme/**', 'Linux'))
        # a symlinked prefix inside the pattern (e.g. `~/Repos` -> elsewhere) resolves too
        self.assertTrue(layers.path_matches(real, f'{self.work}/links/Acme/**', 'Linux'))
        self.assertFalse(layers.path_matches(real, f'{self.work}/links/Other/**', 'Linux'))


class SelectionTest(ConfigCase):
    def test_first_matching_rule_wins_and_use_order_is_the_precedence(self):
        project = self.setup_three()
        layerset = layers.resolve_layers(project)
        self.assertEqual(layerset.ids, ['core', 'acme', 'me'])
        self.assertEqual((layerset.origin, layerset.rule['index']), ('rule', 0))
        self.assertEqual(layers.resolve_layers(self.project('Repos/Other/app')).ids, ['core', 'me'])
        # swapping the rules makes the broad rule win (first match, not best match)
        rules = [([f'{self.repos}/**'], ['core', 'me']), ([f'{self.repos}/Acme/**'], ['core', 'acme', 'me'])]
        self.write_config([('core', 'core', self.core), ('acme', 'org', self.acme), ('me', 'personal', self.me)], rules)
        self.assertEqual(layers.resolve_layers(project).ids, ['core', 'me'])

    def test_use_order_decides_not_the_order_of_the_layers_list(self):
        project = self.setup_three(with_rules=False)
        beta = self.layer_dir('beta', 'org')
        self.write_config([('core', 'core', self.core), ('acme', 'org', self.acme), ('beta', 'org', beta),
                           ('me', 'personal', self.me)], [(['**'], ['core', 'beta', 'acme', 'me'])])
        self.assertEqual(layers.resolve_layers(project).ids, ['core', 'beta', 'acme', 'me'])
        self.write_config([('core', 'core', self.core), ('acme', 'org', self.acme), ('beta', 'org', beta),
                           ('me', 'personal', self.me)], [(['**'], ['core', 'acme', 'beta', 'me'])])
        self.assertEqual(layers.resolve_layers(project).ids, ['core', 'acme', 'beta', 'me'])

    def test_no_matching_rule_is_blocked_with_instructions(self):
        self.setup_three()
        stray = self.project('loose/x')
        with self.assertRaises(layers.LayerError) as caught:
            layers.resolve_layers(stray)
        self.assertEqual((caught.exception.code, caught.exception.status), ('no-rule', 'BLOCKED'))
        self.assertIn('layers use', str(caught.exception))
        for mode in LINK_MODES:
            with self.subTest(mode), link_mode(mode):
                code, report = self.cli_json('layers', 'list', '--project', str(stray))
                self.assertEqual((code, report['status'], report['code']), (2, 'BLOCKED', 'no-rule'))

    def test_project_name_never_selects_layers(self):
        # Negative test: the manifest claims the name of an Acme project, but the path is outside Acme.
        self.setup_three()
        impostor = self.project('Repos/Other/app', name='Acme/app')
        self.assertEqual(layers.resolve_layers(impostor).ids, ['core', 'me'])
        # and a project in the Acme tree with a foreign name still gets the Acme layers
        renamed = self.project('Repos/Acme/zzz', name='Other/qqq')
        layerset = layers.resolve_layers(renamed)
        self.assertEqual(layerset.ids, ['core', 'acme', 'me'])
        self.assertIn('project-name-mismatch', codes(layerset.notes))

    def test_name_path_mismatch_is_only_a_warning_and_a_matching_name_is_silent(self):
        self.setup_three()
        mismatch = layers.resolve_layers(self.project('Repos/Acme/a1', name='Acme/other'))
        notes = by_code(mismatch.notes, 'project-name-mismatch')
        self.assertEqual([item['severity'] for item in notes], ['warning'])
        self.assertEqual(layers.findings_status(layers.check_layers(mismatch, hooks=False)), 'PASS')
        self.assertNotIn('project-name-mismatch', codes(layers.resolve_layers(self.project('Repos/Acme/a2', name='Acme/a2')).notes))
        self.assertNotIn('project-name-mismatch', codes(layers.resolve_layers(self.project('Repos/Acme/a3')).notes))

    def test_order_warnings_for_core_and_personal(self):
        project = self.setup_three(with_rules=False)
        spec = [('core', 'core', self.core), ('acme', 'org', self.acme), ('me', 'personal', self.me)]
        cases = {'core-not-first': ['acme', 'core', 'me'], 'personal-not-last': ['core', 'me', 'acme']}
        for code, use in cases.items():
            with self.subTest(code):
                self.write_config(spec, [(['**'], use)])
                layerset = layers.resolve_layers(project)
                self.assertEqual(layerset.ids, use)               # still honoured as written
                self.assertEqual([item['severity'] for item in by_code(layerset.notes, code)], ['warning'])
        self.write_config(spec, [(['**'], ['core', 'acme', 'me'])])
        self.assertFalse({'core-not-first', 'personal-not-last'} & codes(layers.resolve_layers(project).notes))

    def test_absent_layer_directory_is_skipped_with_an_info_note(self):
        project = self.setup_three()
        import shutil
        shutil.rmtree(self.acme)
        layerset = layers.resolve_layers(project)
        self.assertEqual(layerset.ids, ['core', 'me'])
        note = by_code(layerset.notes, 'layer-absent')[0]
        self.assertEqual((note['severity'], note['layer']), ('info', 'acme'))

    def test_project_may_be_given_as_the_manifest_file(self):
        project = self.setup_three()
        self.assertEqual(layers.resolve_layers(project / '.github' / '.skillsync.yaml').ids, ['core', 'acme', 'me'])
        with self.assertRaises(layers.LayerError) as caught:
            layers.resolve_layers(self.work / 'does-not-exist')
        self.assertEqual(caught.exception.code, 'bad-project')

    def test_case_folding_changes_the_selection_on_windows_and_macos(self):
        core = self.layer_dir('core', 'core')
        project = self.project('Repos/acme/app')
        self.write_config([('core', 'core', core)], [([f'{self.work}/Repos/ACME/**'], ['core'])])
        # the file system of the host must not decide: realpath keeps the spelling given
        with mock.patch.object(os.path, 'realpath', side_effect=lambda value, **kwargs: str(value)), \
                mock.patch.object(Path, 'resolve', lambda self, strict=False: self):
            with self.assertRaises(layers.LayerError):
                layers.resolve_layers(project, system='Linux')
            for system in ('Windows', 'Darwin'):
                with self.subTest(system):
                    self.assertEqual(layers.resolve_layers(project, system=system).ids, ['core'])


class WorktreeSelectionTest(ConfigCase):
    def make_worktree(self, main_dir, worktree_dir):
        main = self.work / main_dir
        main.mkdir(parents=True)
        git(main, 'init', '-q', '-b', 'main')
        git(main, 'config', 'user.email', 'dev@example.test')
        git(main, 'config', 'user.name', 'Dev')
        write(main / '.github' / '.skillsync.yaml', '')
        git(main, 'add', '-A')
        commit(main, 'init')
        worktree = self.work / worktree_dir
        worktree.parent.mkdir(parents=True, exist_ok=True)
        git(main, 'worktree', 'add', '-q', str(worktree), '-b', 'wt')
        return main, worktree

    def test_worktree_matches_by_the_main_repository_first(self):
        core, acme = self.layer_dir('core', 'core'), self.layer_dir('acme', 'org')
        self.write_config([('core', 'core', core), ('acme', 'org', acme)],
                          [([f'{self.work}/Repos/Acme/**'], ['core', 'acme']), (['**'], ['core'])])
        main, worktree = self.make_worktree('Repos/Acme/app', 'elsewhere/wt')
        self.assertEqual(layers.resolve_layers(worktree).ids, ['core', 'acme'])
        self.assertEqual(layers.resolve_layers(main).ids, ['core', 'acme'])

    def test_main_repository_wins_over_the_worktree_own_path(self):
        core, acme = self.layer_dir('core', 'core'), self.layer_dir('acme', 'org')
        self.write_config([('core', 'core', core), ('acme', 'org', acme)],
                          [([f'{self.work}/Repos/Acme/**'], ['core', 'acme']), (['**'], ['core'])])
        _, worktree = self.make_worktree('outside/app', 'Repos/Acme/wt')
        self.assertEqual(layers.resolve_layers(worktree).ids, ['core'])

    def test_worktree_falls_back_to_its_own_path_without_a_rule_for_the_main_repository(self):
        core, acme = self.layer_dir('core', 'core'), self.layer_dir('acme', 'org')
        self.write_config([('core', 'core', core), ('acme', 'org', acme)],
                          [([f'{self.work}/Repos/Acme/**'], ['core', 'acme'])])
        _, worktree = self.make_worktree('outside/app', 'Repos/Acme/wt')
        self.assertEqual(layers.resolve_layers(worktree).ids, ['core', 'acme'])
        _, stray = self.make_worktree('outside/app2', 'nowhere/wt')
        with self.assertRaises(layers.LayerError) as caught:
            layers.resolve_layers(stray)
        self.assertEqual(caught.exception.code, 'no-rule')


class CommandsWithoutProjectTest(ConfigCase):
    def test_cwd_inside_a_project_uses_its_rule(self):
        project = self.setup_three()
        layerset = layers.resolve_layers(None, cwd=project / '.github')
        self.assertEqual((layerset.ids, layerset.origin), (['core', 'acme', 'me'], 'rule'))
        self.assertEqual(layerset.project, project.resolve())

    def test_cwd_inside_a_project_without_a_rule_is_blocked(self):
        self.setup_three()
        stray = self.project('loose/x')
        with self.assertRaises(layers.LayerError) as caught:
            layers.resolve_layers(None, cwd=stray)
        self.assertEqual(caught.exception.code, 'no-rule')

    def test_cwd_outside_any_project_gets_all_layers_in_kind_order(self):
        core, beta, acme, me = (self.layer_dir('core', 'core'), self.layer_dir('beta', 'org'),
                                self.layer_dir('acme', 'org'), self.layer_dir('me', 'personal'))
        # config order deliberately scrambled: personal first, org order beta then acme
        self.write_config([('me', 'personal', me), ('beta', 'org', beta), ('core', 'core', core),
                           ('acme', 'org', acme)], [([f'{self.work}/nowhere/**'], ['core'])])
        outside = self.work / 'plain'
        outside.mkdir()
        layerset = layers.resolve_layers(None, cwd=outside)
        self.assertEqual((layerset.ids, layerset.origin), (['core', 'beta', 'acme', 'me'], 'all'))

    def test_cwd_outside_a_project_uses_a_rule_that_matches_the_directory(self):
        core, acme = self.layer_dir('core', 'core'), self.layer_dir('acme', 'org')
        self.write_config([('core', 'core', core), ('acme', 'org', acme)], [(['**'], ['core'])])
        outside = self.work / 'plain'
        outside.mkdir()
        layerset = layers.resolve_layers(None, cwd=outside)
        self.assertEqual((layerset.ids, layerset.origin), (['core'], 'rule'))

    def test_cli_list_without_project_uses_the_cwd(self):
        project = self.setup_three()
        code, report = self.cli_json('layers', 'list', cwd=project)
        self.assertEqual((code, [item['id'] for item in report['layers']]), (0, ['core', 'acme', 'me']))


# ====================================================================== --layer flag (K3)


class LayerFlagTest(ConfigCase):
    def test_valid_forms(self):
        cwd = str(self.work)
        absolute = layers.parse_layer_flag('core=/x/y')
        self.assertEqual((absolute.id, absolute.path, absolute.synthetic), ('core', Path('/x/y'), False))
        relative = layers.parse_layer_flag('acme-2=rel/dir', cwd=cwd)
        self.assertEqual(relative.path, self.work / 'rel' / 'dir')
        home = layers.parse_layer_flag('me=~/lay')
        self.assertEqual(home.path, Path(self.env['HOME']) / 'lay')

    def test_bad_forms_are_rejected(self):
        for value in ('core', '=/x', 'core=', 'Core=/x', '-a=/x', 'a b=/x', 5, None, ''):
            with self.subTest(value=value):
                with self.assertRaises(layers.LayerError) as caught:
                    layers.parse_layer_flag(value)
                self.assertEqual((caught.exception.code, caught.exception.status), ('bad-layer-flag', 'BLOCKED'))

    def test_synthetic_marker_is_always_refused(self):
        """The 0.9.x bridge form `id=path,synthetic` has no accepting API any more."""
        with self.assertRaises(TypeError):
            layers.parse_layer_flag('core=/x,synthetic', allow_synthetic=True)
        for value in ('core=/x,synthetic', layers.LayerOverride('core', Path('/x'), True)):
            with self.subTest(value=repr(value)):
                with self.assertRaises(layers.LayerError) as caught:
                    layers.parse_layer_flag(value)
                self.assertEqual(caught.exception.code, 'synthetic-refused')
        # through the resolver the marker is refused when the config has no such bridge layer ...
        core = self.layer_dir('core', 'core')
        self.write_config([('core', 'core', core)], [(['**'], ['core'])])
        with self.assertRaises(layers.LayerError) as caught:
            layers.resolve_layers(layers=[f'core={core},synthetic'])
        self.assertEqual(caught.exception.code, 'synthetic-refused')
        # ... and also when the config still holds a 0.9.x bridge entry for that id
        old = self.work / 'old-library'
        old.mkdir()
        self.config_file.write_text(yaml_text({
            'layers': [{'id': 'core', 'kind': 'core', 'path': str(old),
                        'synthetic': {'version': '0.9.0', 'core_min': '0.9.0'}}],
            'layer_rules': [{'match': ['**'], 'use': ['core']}]}), encoding='utf-8')
        with self.assertRaises(layers.LayerError) as caught:
            layers.resolve_layers(layers=[f'core={old},synthetic'])
        self.assertEqual(caught.exception.code, 'synthetic-refused')

    def test_override_replaces_the_configured_layers_for_one_command(self):
        project = self.setup_three()
        other = self.layer_dir('other', 'org')
        layerset = layers.resolve_layers(project, layers=[f'core={self.core}', f'other={other}'])
        self.assertEqual((layerset.ids, layerset.origin), (['core', 'other'], 'override'))
        self.assertEqual(layerset.roots, [self.core, other])
        # no rule needed, no config needed
        self.config_file.unlink()
        self.assertEqual(layers.resolve_layers(layers=[f'core={self.core}']).ids, ['core'])

    def test_invalid_override_sets(self):
        core, me = self.layer_dir('core', 'core'), self.layer_dir('me', 'personal')
        self.write_config([('core', 'core', core)], [(['**'], ['core'])])
        make_layer(self.work / 'p2', 'b', 'personal')
        make_layer(self.work / 'o1', 'dup', 'org')
        make_layer(self.work / 'o2', 'dup', 'org')
        cases = {
            'duplicate id': [f'core={core}', f'core={core}'],
            'duplicate non-core id': [f'core={core}', f'dup={self.work / "o1"}', f'dup={self.work / "o2"}'],
            'two personal layers': [f'a={me}', f'b={self.work / "p2"}'],
            'unknown kind': [f'zzz={self.work}'],
        }
        for label, values in cases.items():
            with self.subTest(label):
                with self.assertRaises(layers.LayerError) as caught:
                    layers.resolve_layers(layers=values)
                self.assertEqual(caught.exception.code, 'bad-layer-flag')

    def test_child_processes_receive_the_same_layers_in_argv(self):
        project = self.setup_three()
        layerset = layers.resolve_layers(project)
        argv = layers.layer_argv(layerset)
        self.assertEqual(argv, ['--layer', f'core={self.core}', '--layer', f'acme={self.acme}', '--layer', f'me={self.me}'])
        self.assertEqual(layerset.argv(), argv)
        again = layers.resolve_layers(layers=argv[1::2])
        self.assertEqual((again.ids, again.roots), (layerset.ids, layerset.roots))
        # a real child: the cwd has no rule at all, the flags alone decide
        stray = self.project('loose/x')
        for mode in LINK_MODES:
            with self.subTest(mode), link_mode(mode):
                code, report = self.cli_json('layers', 'list', *argv, cwd=stray)
                self.assertEqual(code, 0, report)
                self.assertEqual([item['id'] for item in report['layers']], layerset.ids)
                self.assertEqual(report['origin'], 'override')

    def test_kind_of_an_override_comes_from_config_then_layer_yaml_then_core_name(self):
        lay = self.layer_dir('thing', 'org')
        no_meta = make_layer(self.work / 'nometa', 'nometa', 'org', meta=False)
        self.write_config()
        self.assertEqual(layers.resolve_layers(layers=[f'thing={lay}']).layers[0].kind, 'org')      # from layer.yaml
        self.assertEqual(layers.resolve_layers(layers=[f'core={no_meta}']).layers[0].kind, 'core')   # by the name core
        with self.assertRaises(layers.LayerError):
            layers.resolve_layers(layers=[f'nometa={no_meta}'])

    def test_a_synthetic_bridge_entry_in_the_config_is_parsed_but_blocked_when_layers_are_resolved(self):
        old = self.work / 'old-library'
        old.mkdir()
        self.config_file.write_text(yaml_text({
            'layers': [{'id': 'core', 'kind': 'core', 'path': str(old),
                        'synthetic': {'version': '0.9.0', 'core_min': '0.9.0'}}],
            'layer_rules': [{'match': ['**'], 'use': ['core']}]}), encoding='utf-8')
        project = self.project('p')
        core = layers.load_machine_config().layers['core']
        self.assertEqual((core.synthetic, core.writable), ({'version': '0.9.0', 'core_min': '0.9.0'}, False))
        self.assertEqual(core.argv_value(), f'core={old}', 'the bridge form `,synthetic` is never produced')
        with self.assertRaises(layers.LayerError) as config_case:
            layers.resolve_layers(project)
        self.assertEqual((config_case.exception.code, config_case.exception.status), ('legacy-config', 'BLOCKED'))
        self.assertIn('layers migrate --layer-source core=PATH --apply', str(config_case.exception))
        # a plain override of the same id does not resurrect the bridge: it is a real layer or nothing
        child = layers.resolve_layers(layers=[f'core={old}'])
        self.assertNotIn('legacy-bridge', codes(child.notes))
        self.assertEqual((child.core.synthetic, child.core.writable), (None, False))
        with self.assertRaises(layers.LayerError) as flag_case:
            layers.resolve_layers(layers=[f'core={old},synthetic'])
        self.assertEqual(flag_case.exception.code, 'synthetic-refused')


# ====================================================================== layer.yaml and versions (K12)


class LayerYamlTest(ConfigCase):
    def test_generator_version_matches_the_package_version(self):
        import skillsync_workflow
        self.assertRegex(skillsync_workflow.__version__, r'^\d+\.\d+\.\d+$')
        self.assertEqual(layers.generator_version(), skillsync_workflow.__version__)

    def test_semver_parsing_and_ordering(self):
        self.assertEqual(layers.parse_semver('1.20.3'), (1, 20, 3))
        for bad in ('1.0', 'v1.0.0', '1.0.0-rc1', '01.0.0', '1.0.x', '', None, 1):
            with self.subTest(bad):
                with self.assertRaises(layers.LayerError):
                    layers.parse_semver(bad)
        self.assertEqual(layers.compare_versions('0.10.0', '0.9.0'), 1)      # numeric, not lexical
        self.assertEqual(layers.compare_versions('0.9.0', '0.9.0'), 0)
        self.assertEqual(layers.compare_versions('0.9.1', '1.0.0'), -1)

    def test_parse_layer_yaml(self):
        meta = layers.parse_layer_yaml({'schema': 1, 'id': 'acme', 'kind': 'org', 'version': '1.2.3',
                                        'core_min': '0.9.0', 'requires': ['core'], 'identity': 'leakguard/identity.yaml'})
        self.assertEqual((meta['id'], meta['kind'], meta['version'], meta['core_min'], meta['requires'], meta['identity']),
                         ('acme', 'org', '1.2.3', '0.9.0', ['core'], 'leakguard/identity.yaml'))
        minimal = layers.parse_layer_yaml({'schema': 1, 'id': 'a', 'kind': 'personal'})
        self.assertEqual((minimal['version'], minimal['core_min'], minimal['requires']), (None, None, []))

    def test_invalid_layer_yaml_is_rejected(self):
        base = {'schema': 1, 'id': 'a', 'kind': 'org', 'version': '1.0.0', 'core_min': '0.9.0'}
        cases = {
            'not a mapping': ['x'],
            'no schema': {k: v for k, v in base.items() if k != 'schema'},
            'schema from the future': dict(base, schema=2),
            'schema zero': dict(base, schema=0),
            'bad id': dict(base, id='A b'),
            'unknown kind': dict(base, kind='team'),
            'version not semver': dict(base, version='1.0'),
            'core_min not semver': dict(base, core_min='latest'),
            'requires not a list': dict(base, requires='core'),
            'requires with a bad id': dict(base, requires=['Core']),
            'identity escapes the layer': dict(base, identity='../outside.yaml'),
            'identity is absolute': dict(base, identity='/etc/passwd'),
        }
        for label, data in cases.items():
            with self.subTest(label):
                with self.assertRaises(layers.LayerError):
                    layers.parse_layer_yaml(data)

    def test_read_layer_yaml_reports_missing_and_broken_files(self):
        empty = self.work / 'empty'
        empty.mkdir()
        self.assertEqual(layers.read_layer_yaml(empty), (None, None))
        write(empty / 'layer.yaml', 'schema: [unclosed\n')
        meta, error = layers.read_layer_yaml(empty)
        self.assertIsNone(meta)
        self.assertTrue(error)

    # ---- check_layers: BLOCKED / warning classes

    def layerset(self, *specs, **kwargs):
        """specs: (id, kind, kwargs of make_layer); one rule `**` using all of them in order."""
        spec = []
        for layer_id, kind, options in specs:
            spec.append((layer_id, kind, self.layer_dir(layer_id, kind, **options)))
        self.write_config(spec, [(['**'], [item[0] for item in specs])])
        return layers.resolve_layers(self.project('proj'), **kwargs)

    def check(self, layerset, **kwargs):
        kwargs.setdefault('hooks', False)
        return layers.check_layers(layerset, **kwargs)

    def test_valid_layers_pass_without_findings(self):
        version = layers.generator_version()
        layerset = self.layerset(('core', 'core', {'version': version}), ('acme', 'org', {}), ('me', 'personal', {}))
        findings = self.check(layerset)
        self.assertEqual(findings, [])
        self.assertEqual(layers.findings_status(findings), 'PASS')

    def test_missing_version_or_core_min_is_blocked(self):
        for label, options, code in (('version', {'version': None}, 'version-missing'),
                                     ('core_min', {'core_min': False}, 'core-min-missing')):
            with self.subTest(label):
                layerset = self.layerset(('core', 'core', {'version': layers.generator_version()}), ('acme', 'org', options))
                findings = self.check(layerset)
                self.assertEqual([(item['code'], item['severity'], item['layer']) for item in findings if item['code'] == code],
                                 [(code, 'blocked', 'acme')])
                self.assertEqual(layers.findings_status(findings), 'BLOCKED')

    def test_core_min_above_the_generator_version_is_blocked(self):
        # Versions are derived from the generator version so the test does not expire at a release bump.
        major = int(layers.generator_version().split('.')[0])
        above, newer = f'{major + 1}.0.0', f'{major + 2}.0.0'
        layerset = self.layerset(('core', 'core', {'version': layers.generator_version()}),
                                 ('acme', 'org', {'core_min': above}))
        findings = self.check(layerset)
        self.assertEqual([item['layer'] for item in by_code(findings, 'core-min-unmet')], ['acme'])
        self.assertEqual(layers.findings_status(findings), 'BLOCKED')
        # the comparison is against the generator version: the same layer is fine for a newer generator ...
        self.assertNotIn('core-min-unmet', codes(self.check(layerset, version=newer)))
        # ... and equal versions are fine, lower ones are not
        current = layers.generator_version()  # core_min of a layer defaults to the generator version (make_layer)
        equal = self.layerset(('core', 'core', {'version': current}), ('acme', 'org', {'core_min': current}))
        self.assertNotIn('core-min-unmet', codes(self.check(equal, version=current)))
        old = self.check(layerset, version='0.9.9')
        self.assertIn('core-min-unmet', codes(old))

    def test_core_min_is_checked_for_numeric_not_lexical_order(self):
        layerset = self.layerset(('core', 'core', {'version': '0.9.0', 'core_min': '0.9.0'}), ('acme', 'org', {'core_min': '0.9.0'}))
        self.assertNotIn('core-min-unmet', codes(self.check(layerset, version='1.1.0')))
        layerset = self.layerset(('core', 'core', {'version': '0.9.0', 'core_min': '0.9.0'}), ('acme', 'org', {'core_min': '0.10.0'}))
        self.assertIn('core-min-unmet', codes(self.check(layerset, version='0.9.0')))

    def test_unmet_requires_is_blocked_and_met_requires_passes(self):
        version = layers.generator_version()
        unmet = self.layerset(('core', 'core', {'version': version}), ('acme', 'org', {'requires': ['beta']}))
        findings = self.check(unmet)
        self.assertEqual([(item['code'], item['layer']) for item in findings if item['code'] == 'requires-unmet'],
                         [('requires-unmet', 'acme')])
        self.assertEqual(layers.findings_status(findings), 'BLOCKED')
        met = self.layerset(('core', 'core', {'version': version}), ('beta', 'org', {}), ('acme', 'org', {'requires': ['beta']}))
        self.assertNotIn('requires-unmet', codes(self.check(met)))

    def test_requires_cycle_is_blocked(self):
        version = layers.generator_version()
        layerset = self.layerset(('core', 'core', {'version': version}), ('a', 'org', {'requires': ['b']}),
                                 ('b', 'org', {'requires': ['a']}))
        findings = self.check(layerset)
        self.assertIn('requires-cycle', codes(findings))
        self.assertEqual(layers.findings_status(findings), 'BLOCKED')
        acyclic = self.layerset(('core', 'core', {'version': version}), ('a', 'org', {'requires': ['b']}), ('b', 'org', {}))
        self.assertNotIn('requires-cycle', codes(self.check(acyclic)))
        self_cycle = self.layerset(('core', 'core', {'version': version}), ('a', 'org', {'requires': ['a']}))
        self.assertIn('requires-cycle', codes(self.check(self_cycle)))

    def test_missing_or_broken_layer_yaml_and_mismatches_are_blocked(self):
        version = layers.generator_version()
        cases = {
            'layer-yaml-missing': {'meta': False},
            'id-mismatch': {'extra_meta': {'id': 'other'}},
            'kind-mismatch': {'extra_meta': {'kind': 'personal'}},
        }
        for code, options in cases.items():
            with self.subTest(code):
                layerset = self.layerset(('core', 'core', {'version': version}), ('acme', 'org', options))
                findings = self.check(layerset)
                self.assertEqual([item['layer'] for item in by_code(findings, code)], ['acme'])
                self.assertEqual(layers.findings_status(findings), 'BLOCKED')
        self.layerset(('core', 'core', {'version': version}), ('acme', 'org', {}))
        write(self.work / 'layers' / 'acme' / 'layer.yaml', 'schema: [unclosed\n')
        layerset = layers.resolve_layers(self.project('proj'))
        self.assertIn('layer-yaml-invalid', codes(self.check(layerset)))

    def test_missing_core_layer_is_blocked(self):
        layerset = self.layerset(('acme', 'org', {}), ('me', 'personal', {}))
        findings = self.check(layerset)
        self.assertEqual([item['severity'] for item in by_code(findings, 'core-missing')], ['blocked'])
        self.assertEqual(layers.findings_status(findings), 'BLOCKED')

    def test_core_version_mismatch_is_only_a_warning(self):
        major, minor, patch = map(int, layers.generator_version().split('.'))
        newer_core = f'{major}.{minor}.{patch + 1}'  # a core newer than the generator, whatever the release
        layerset = self.layerset(('core', 'core', {'version': newer_core}), ('acme', 'org', {}))
        findings = self.check(layerset)
        mismatch = by_code(findings, 'core-version-mismatch')
        self.assertEqual([(item['severity'], item['layer']) for item in mismatch], [('warning', 'core')])
        self.assertEqual(layers.findings_status(findings), 'PASS')
        equal = self.layerset(('core', 'core', {'version': layers.generator_version()}))
        self.assertNotIn('core-version-mismatch', codes(self.check(equal)))
        # an org layer with another version is not a "core" mismatch
        org_only = self.layerset(('core', 'core', {'version': layers.generator_version()}), ('acme', 'org', {'version': '7.0.0'}))
        self.assertNotIn('core-version-mismatch', codes(self.check(org_only)))

    def test_missing_readme_or_changelog_are_warnings(self):
        version = layers.generator_version()
        layerset = self.layerset(('core', 'core', {'version': version}),
                                 ('acme', 'org', {'readme': False}), ('me', 'personal', {'changelog': False}))
        findings = self.check(layerset)
        self.assertEqual([(item['code'], item['layer'], item['severity']) for item in findings],
                         [('readme-missing', 'acme', 'warning'), ('changelog-missing', 'me', 'warning')])
        self.assertEqual(layers.findings_status(findings), 'PASS')

    def test_severity_levels_and_status_mapping(self):
        self.assertEqual(layers.SEVERITIES, ('blocked', 'error', 'warning', 'info'))
        mixed = [{'code': 'a', 'layer': None, 'message': '', 'severity': severity} for severity in ('info', 'warning')]
        self.assertEqual(layers.findings_status(mixed), 'PASS')
        self.assertEqual(layers.findings_status(mixed + [dict(mixed[0], severity='error')]), 'FAIL')
        self.assertEqual(layers.findings_status(mixed + [dict(mixed[0], severity='error'), dict(mixed[0], severity='blocked')]),
                         'BLOCKED')

    def test_synthetic_metadata_is_parsed_from_the_config_but_the_layer_is_blocked(self):
        old = self.work / 'old-library'
        old.mkdir()
        self.config_file.write_text(yaml_text({
            'layers': [{'id': 'core', 'kind': 'core', 'path': str(old), 'synthetic': {'version': '0.9.0', 'core_min': '0.9.0'}}],
            'layer_rules': [{'match': ['**'], 'use': ['core']}]}), encoding='utf-8')
        bridge = layers.load_machine_config().layers['core']
        self.assertEqual(bridge.synthetic, {'version': '0.9.0', 'core_min': '0.9.0'}, 'parsed from the config as written')
        with self.assertRaises(layers.LayerError) as caught:
            layers.resolve_layers(self.project('p'))
        self.assertEqual(caught.exception.code, 'legacy-config')


# ====================================================================== leak guard sources from the layers (K7/K8, N5-2)


class GuardSourcesCase(ConfigCase):
    def guard_config(self, layer_specs, targets, rules=None, **extra):
        self.write_config(layer_specs, rules or [(['**'], [item[0] for item in layer_specs])],
                          leakguard=dict(targets=targets, **extra.pop('guard', {})), **extra)

    def leakcheck(self, path, *args, cwd=None):
        return self.cli_json('leakcheck', str(path), *args, cwd=cwd)


class GuardSourcesTest(GuardSourcesCase):
    def setUp(self):
        super().setUp()
        beta_term = {'term': CLIENT_BETA, 'kind': 'org'}
        # the core checkout carries a client term of layer `beta` in its instructions
        self.core = self.git_layer('core', 'core', {'skills/x/SKILL.md': f'see {CLIENT_BETA} here\n'})
        self.acme = self.git_layer('acme', 'org', identity_terms=[{'term': CLIENT_GAMMA, 'kind': 'org'}])
        self.beta = self.git_layer('beta', 'org', identity_terms=[beta_term])
        self.specs = [('core', 'core', self.core), ('acme', 'org', self.acme), ('beta', 'org', self.beta)]
        self.targets = {'core': {'scope': 'public', 'authors': ['owner@example.com']},
                        'acme': {'scope': 'org-private', 'authors': ['dev@example.test']},
                        'beta': {'scope': 'org-private', 'authors': ['dev@example.test']}}
        self.guard_config(self.specs, self.targets)

    def test_identities_of_local_layers_are_taken_from_the_config(self):
        for mode in LINK_MODES:
            with self.subTest(mode), link_mode(mode):
                code, report = self.leakcheck(self.core)
                self.assertEqual((code, report['status']), (1, 'FAIL'))
                self.assertEqual(report['target'], 'core')                 # checkout is the git root of the scan
                self.assertEqual([(item['path'], item['category']) for item in report['findings']],
                                 [('skills/x/SKILL.md', 'identity-org')])
                self.assertNotIn(CLIENT_BETA.lower(), json.dumps(report).lower())      # never a value

    def test_a_broken_layer_yaml_blocks_leakcheck_from_the_config_instead_of_falling_back(self):
        (self.acme / 'layer.yaml').write_text('schema: 7\nid: acme\nkind: org\n', encoding='utf-8')
        for mode in LINK_MODES:
            with self.subTest(mode), link_mode(mode):
                code, report = self.leakcheck(self.core)
                self.assertEqual((code, report['status']), (2, 'BLOCKED'), report)
                self.assertIn('acme', report.get('reason', ''))                    # names the layer
        with self.assertRaises(layers.LayerError) as caught:
            layers.leakguard_inputs(path=self.core)
        self.assertEqual(caught.exception.code, 'layer-yaml-invalid')
        self.assertIn('acme', str(caught.exception))
        # explicit inputs and --no-config do not read the layers
        self.assertEqual(self.leakcheck(self.core, '--no-config')[0], 0)
        # `layers check` reports it as a finding instead of raising
        self.write_config([('core', 'core', self.core), ('acme', 'org', self.acme)], [(['**'], ['core', 'acme'])])
        findings = layers.check_layers(layers.resolve_layers(self.project('p')), hooks=False)
        self.assertIn('layer-yaml-invalid', codes(findings))

    @needs_os_symlinks
    def test_a_link_in_a_layer_blocks_leakcheck_from_the_config(self):
        os.symlink(self.beta / 'README.md', self.core / 'skills' / 'x' / 'link.md')            # a link inside the scanned layer
        for mode in LINK_MODES:
            with self.subTest(mode), link_mode(mode):
                code, report = self.leakcheck(self.core)
                self.assertEqual((code, report['status']), (2, 'BLOCKED'), report)           # structure error, not a leak (FAIL)
                self.assertIn('core', report.get('reason', ''))
                self.assertIn('skills/x/link.md', report.get('reason', ''))
                self.assertNotIn(str(self.beta), json.dumps(report))                         # the target is not disclosed
                self.assertEqual(report.get('findings'), [])
        with self.assertRaises(layers.LayerError) as caught:
            layers.leakguard_inputs(path=self.core)
        self.assertEqual(caught.exception.code, 'symlink-in-layer')
        self.assertEqual(self.leakcheck(self.core, '--no-config')[0], 0)                      # explicit/generic mode does not read layers
        (self.core / 'skills' / 'x' / 'link.md').unlink()
        self.assertEqual(self.leakcheck(self.core)[0], 1)                                    # back to the real leak finding

    @needs_os_symlinks
    def test_an_identity_file_that_is_a_link_blocks_leakcheck_of_any_repository(self):
        real = self.work / 'real-identity.yaml'
        real.write_text((self.beta / 'leakguard' / 'identity.yaml').read_text(encoding='utf-8'), encoding='utf-8')
        (self.beta / 'leakguard' / 'identity.yaml').unlink()
        os.symlink(real, self.beta / 'leakguard' / 'identity.yaml')
        other = self.make_repo('other-repo')
        for target in (self.core, other):
            with self.subTest(target=target.name):
                code, report = self.leakcheck(target)
                self.assertEqual((code, report['status']), (2, 'BLOCKED'), report)
                self.assertIn('beta', report.get('reason', ''))
                self.assertNotIn(str(real), json.dumps(report))

    def subdir_repo(self, *, link_subdir=False):
        """A git checkout holding a layer in `library/` (optionally `library` is a link)."""
        repo = self.work / 'subrepo'
        make_layer(repo / ('real' if link_subdir else 'library'), 'sub', 'org')
        git(repo, 'init', '-q', '-b', 'main')
        git(repo, 'config', 'user.email', 'dev@example.test')
        git(repo, 'config', 'user.name', 'Dev')
        if link_subdir:
            require_os_symlinks(self)
            os.symlink(repo / 'real', repo / 'library', target_is_directory=True)
        git(repo, 'add', '-A')
        git(repo, 'commit', '-q', '--no-verify', '-m', 'content')
        self.guard_config(self.specs + [('sub', 'org', repo, {'subdir': 'library'})], dict(self.targets, sub={'scope': 'org-private'}))
        return repo

    @needs_os_symlinks
    def test_a_link_on_the_subdir_path_blocks_leakcheck_of_that_layer(self):
        repo = self.subdir_repo(link_subdir=True)
        for mode in LINK_MODES:
            with self.subTest(mode), link_mode(mode):
                code, report = self.leakcheck(repo)
                self.assertEqual((code, report['status']), (2, 'BLOCKED'), report)
                self.assertIn('sub', report.get('reason', ''))
                self.assertNotIn('real', json.dumps(report).replace('subrepo', ''))      # the target is not disclosed

    @needs_os_symlinks
    def test_untracked_links_outside_subdir_do_not_block_leakcheck_but_tracked_ones_do(self):
        repo = self.subdir_repo()
        (repo / '.claude' / 'skills').mkdir(parents=True)
        os.symlink(repo / 'library', repo / '.claude' / 'skills' / 'z')                     # what `skillsync link` leaves behind
        for mode in LINK_MODES:
            with self.subTest(mode), link_mode(mode):
                self.assertEqual(self.leakcheck(repo)[0], 0, 'untracked artefact links are not violations')
        os.symlink(repo / 'library', repo / 'tracked-link')
        git(repo, 'add', 'tracked-link')
        code, report = self.leakcheck(repo)
        self.assertEqual((code, report['status']), (2, 'BLOCKED'))
        self.assertIn('tracked-link', report['reason'])

    @needs_os_symlinks
    def test_a_tracked_link_blocks_leakcheck_even_when_the_layout_root_is_missing(self):
        repo = self.subdir_repo()
        shutil.rmtree(repo / 'library')                                                     # the checkout exists, its layout root does not
        (repo / 'tools').mkdir()
        os.symlink(repo / 'tools', repo / 'tools' / 'self')
        git(repo, 'add', '-A')
        code, report = self.leakcheck(repo)
        self.assertEqual((code, report['status']), (2, 'BLOCKED'), report)
        self.assertIn('tools/self', report['reason'])

    @needs_os_symlinks
    def test_layer_yaml_that_is_a_link_blocks_leakcheck_without_disclosing_its_content(self):
        real = self.work / 'real-meta.yaml'
        real.write_text('schema: 1\nid: acme\nkind: org\nversion: UNIQUEVER\n', encoding='utf-8')
        (self.acme / 'layer.yaml').unlink()
        os.symlink(real, self.acme / 'layer.yaml')
        for mode in LINK_MODES:
            with self.subTest(mode), link_mode(mode):
                code, report = self.leakcheck(self.core)
                self.assertEqual((code, report['status']), (2, 'BLOCKED'), report)
                self.assertIn('acme', report['reason'])
                self.assertNotIn('UNIQUE', json.dumps(report))
                self.assertNotIn('real-meta', json.dumps(report))

    def test_no_config_flag_and_missing_identities_leave_only_generic_patterns(self):
        code, report = self.leakcheck(self.core, '--no-config')
        self.assertEqual((code, report['status'], report['coverage']), (0, 'PASS', 'generic'))
        self.beta.joinpath('leakguard/identity.yaml').unlink()
        code, report = self.leakcheck(self.core)
        self.assertEqual((code, report['status']), (0, 'PASS'))           # nothing to recognise the term by

    def test_coverage_is_counted_from_the_config(self):
        # needed identities when scanning core: every non-public target other than core (acme, beta)
        code, report = self.leakcheck(self.core)
        self.assertEqual(report['coverage'], 'full')
        self.beta.joinpath('leakguard/identity.yaml').unlink()
        self.assertEqual(self.leakcheck(self.core)[1]['coverage'], 'partial')
        self.acme.joinpath('leakguard/identity.yaml').unlink()
        self.assertEqual(self.leakcheck(self.core)[1]['coverage'], 'generic')

    def test_public_targets_do_not_count_towards_full(self):
        # scanning acme: the only other target is public `core`, so no identity is needed
        self.guard_config(self.specs[:2], {key: self.targets[key] for key in ('core', 'acme')})
        self.acme.joinpath('leakguard/identity.yaml').unlink()
        code, report = self.leakcheck(self.acme)
        self.assertEqual((report['target'], report['coverage']), ('acme', 'full'))
        # a non-public second target without an identity makes it partial/generic
        self.guard_config(self.specs, self.targets)
        self.assertEqual(self.leakcheck(self.acme)[1]['coverage'], 'full')     # beta has its identity
        self.beta.joinpath('leakguard/identity.yaml').unlink()
        self.assertEqual(self.leakcheck(self.acme)[1]['coverage'], 'generic')

    def test_identity_dir_supplies_identities_of_layers_that_are_not_checked_out(self):
        ops = self.work / 'ops'
        gamma_term = 'Quen' + 'tal'
        write(ops / 'delta.yaml', yaml_text({'schema': 1, 'layer': 'delta', 'terms': [{'term': gamma_term, 'kind': 'org'}]}))
        targets = {key: self.targets[key] for key in ('core', 'acme')}
        targets['delta'] = {'scope': 'org-private'}
        self.guard_config(self.specs[:2], targets, guard={'identity_dir': str(ops)})
        write(self.core / 'docs' / 'a.md', f'about {gamma_term}\n')
        git(self.core, 'add', '-A')
        commit(self.core, 'doc')
        code, report = self.leakcheck(self.core)
        self.assertEqual(code, 1)
        self.assertIn(('docs/a.md', 'identity-org'), [(item['path'], item['category']) for item in report['findings']])
        # delta has an identity only in identity_dir, acme in its checkout: both are needed and present
        self.assertEqual((report['coverage'], report['target']), ('full', 'core'))

    def test_target_is_chosen_by_remote_match_and_fallbacks(self):
        project = self.make_repo('app')
        git(project, 'remote', 'add', 'origin', 'git@example.test:acme/app.git')
        targets = dict(self.targets)
        targets['acme'] = dict(targets['acme'], remote_match=['example.test:acme/*'])
        self.guard_config(self.specs, targets)
        self.assertEqual(self.leakcheck(project)[1]['target'], 'acme')           # remote_match
        self.assertEqual(layers.leakguard_inputs(path=project)['target'], 'acme')     # chosen by the layers module for the hooks
        self.assertEqual(self.leakcheck(project, '--target', 'beta')[1]['target'], 'beta')     # explicit wins
        git(project, 'remote', 'set-url', 'origin', 'git@example.test:unrelated/app.git')
        code, report = self.leakcheck(project)                                    # no match, not a layer checkout
        self.assertIsNone(report['target'])
        self.assertEqual(report['coverage'], 'generic')
        self.assertTrue(report['warnings'])
        self.assertEqual(self.leakcheck(self.beta)[1]['target'], 'beta')         # checkout of a layer = git root

    def test_ambiguous_remote_match_selects_no_target(self):
        project = self.make_repo('app2')
        git(project, 'remote', 'add', 'origin', 'git@example.test:acme/app.git')
        targets = {key: dict(value, remote_match=['example.test:acme/*']) for key, value in self.targets.items()}
        self.guard_config(self.specs, targets)
        inputs = layers.leakguard_inputs(path=project)
        self.assertIsNone(inputs['target'])

    def test_foreign_identity_in_a_project_of_another_target_fails_unless_allowed(self):
        project = self.make_repo('app3')
        git(project, 'remote', 'add', 'origin', 'git@example.test:acme/app.git')
        write(project / 'docs' / 'n.md', f'{CLIENT_BETA} was here\n')
        targets = dict(self.targets)
        targets['acme'] = dict(targets['acme'], remote_match=['example.test:acme/*'])
        self.guard_config(self.specs, targets)
        code, report = self.leakcheck(project)
        self.assertEqual((code, report['target']), (1, 'acme'))
        # own identity of the target is never a leak
        write(project / 'docs' / 'n.md', f'{CLIENT_GAMMA} was here\n')
        self.assertEqual(self.leakcheck(project)[0], 0)
        # allow_in on the term (identity file of beta) opens exactly that target
        write(self.beta / 'leakguard/identity.yaml', yaml_text({'schema': 1, 'layer': 'beta', 'terms': [
            {'term': CLIENT_BETA, 'kind': 'org', 'allow_in': ['acme']}]}))
        write(project / 'docs' / 'n.md', f'{CLIENT_BETA} was here\n')
        self.assertEqual(self.leakcheck(project)[0], 0)

    def test_inputs_function_collects_files_targets_and_trailer_allow(self):
        self.guard_config(self.specs, self.targets, guard={'trailer_allow': ['bot@example.test']})
        inputs = layers.leakguard_inputs(path=self.core)
        names = sorted(Path(item).parent.parent.name for item in inputs['identity_files'])
        self.assertEqual(names, ['acme', 'beta'])
        self.assertEqual(inputs['trailer_allow'], ['bot@example.test'])
        self.assertEqual(inputs['targets']['acme']['kind'], 'org')
        self.assertEqual(inputs['targets']['core']['kind'], 'core')
        self.assertEqual(inputs['target'], 'core')

    def test_config_warnings_reach_the_report(self):
        self.guard_config(self.specs, self.targets, guard={'surprise': 1})
        code, report = self.leakcheck(self.core)
        self.assertTrue(any('surprise' in item for item in report['warnings']), report['warnings'])

    def test_every_call_path_selects_the_target_through_one_function(self):
        project = self.make_repo('app4')
        git(project, 'remote', 'add', 'origin', 'git@example.test:acme/app.git')
        targets = dict(self.targets)
        targets['acme'] = dict(targets['acme'], remote_match=['example.test:acme/*'])
        self.guard_config(self.specs, targets)
        validated = leakguard.validate_targets(layers.load_machine_config().targets_with_kind())
        self.assertEqual(leakguard.select_target(project, validated), ('acme', []))
        self.assertEqual(layers.leakguard_inputs(path=project)['target'], 'acme')
        self.assertEqual(leakguard.leakcheck(project, targets=validated)['target'], 'acme')      # API without a target
        self.assertEqual(self.leakcheck(project)[1]['target'], 'acme')                          # CLI from the config
        # a layer checkout is a target only where the layer map is known (config), not for the bare API
        self.assertEqual(leakguard.select_target(self.beta, validated), (None, []))
        self.assertEqual(leakguard.select_target(self.beta, validated, {'beta': self.beta}), ('beta', []))
        self.assertEqual(layers.leakguard_inputs(path=self.beta)['target'], 'beta')
        self.assertEqual(self.leakcheck(self.beta)[1]['target'], 'beta')
        # several hits: no target and a warning, the same everywhere
        both = {key: dict(value, remote_match=['example.test:acme/*']) for key, value in self.targets.items()}
        self.guard_config(self.specs, both)
        validated = leakguard.validate_targets(layers.load_machine_config().targets_with_kind())
        chosen, warnings = leakguard.select_target(project, validated)
        self.assertEqual((chosen, bool(warnings)), (None, True))
        self.assertIsNone(layers.leakguard_inputs(path=project)['target'])
        self.assertIsNone(leakguard.leakcheck(project, targets=validated)['target'])
        self.assertIsNone(self.leakcheck(project)[1]['target'])
        # outside a git repository nothing is selected
        self.assertEqual(leakguard.select_target(self.work / 'nowhere', validated), (None, []))

    def test_missing_config_gives_generic_inputs_and_unknown_target_a_warning(self):
        self.config_file.unlink()
        inputs = layers.leakguard_inputs(path=self.core)
        self.assertEqual((inputs['identity_files'], inputs['targets'], inputs['target']), ([], None, None))
        self.guard_config(self.specs, self.targets)
        code, report = self.leakcheck(self.core, '--target', 'nope')
        self.assertEqual((report['coverage'], bool(report['warnings'])), ('generic', True))

    def test_require_full_blocks_when_coverage_is_not_full(self):
        self.assertEqual(self.leakcheck(self.core, '--require', 'full')[1]['coverage'], 'full')
        self.beta.joinpath('leakguard/identity.yaml').unlink()
        self.acme.joinpath('leakguard/identity.yaml').unlink()
        code, report = self.leakcheck(self.core, '--require', 'full')
        self.assertEqual((code, report['status']), (2, 'BLOCKED'))


class PatternsForTargetTest(GuardSourcesCase):
    def setUp(self):
        super().setUp()
        self.acme = self.git_layer('acme', 'org')
        self.beta = self.git_layer('beta', 'org', identity_terms=[{'term': CLIENT_BETA, 'kind': 'org'}])
        self.core = self.git_layer('core', 'core')
        self.guard_config([('core', 'core', self.core), ('acme', 'org', self.acme), ('beta', 'org', self.beta)],
                          {'core': {'scope': 'public'}, 'acme': {'scope': 'org-private'},
                           'beta': {'scope': 'org-private'}})

    def patterns(self, *args):
        process = self.cli('leakguard', 'patterns', '--json', *args)
        return process.returncode, json.loads(process.stdout)

    def add_additions(self, text):
        write(self.acme / '.leakguard' / 'patterns.yaml', text)

    def test_target_additions_are_merged_into_the_pattern_list(self):
        base = self.patterns()[1]
        self.add_additions("schema: 1\npatterns:\n  - id: acme-extra\n    category: custom\n    regex: 'zz-secret-[0-9]+'\n")
        for mode in LINK_MODES:
            with self.subTest(mode), link_mode(mode):
                code, report = self.patterns('--target', 'acme')
                self.assertEqual((code, report['status'], report['target']), (0, 'PASS', 'acme'))
                self.assertIn('acme-extra', [item['id'] for item in report['patterns']])
                self.assertEqual(len(report['patterns']), len(base['patterns']) + 1)
        self.assertNotIn('acme-extra', [item['id'] for item in self.patterns()[1]['patterns']])      # no target, no additions
        self.assertNotIn('acme-extra', [item['id'] for item in self.patterns('--target', 'beta')[1]['patterns']])

    def test_additions_naming_a_foreign_identity_are_blocked(self):
        self.add_additions(f"schema: 1\npatterns:\n  - id: leak\n    category: custom\n    regex: '{CLIENT_BETA}'\n")
        code, report = self.patterns('--target', 'acme')
        self.assertEqual((code, report['status']), (2, 'BLOCKED'))
        self.assertNotIn(CLIENT_BETA, json.dumps(report))

    @needs_os_symlinks
    def test_a_patterns_file_that_is_a_link_or_lies_under_one_is_blocked(self):
        real = self.work / 'real-patterns.yaml'
        real.write_text("schema: 1\npatterns:\n  - id: acme-extra\n    category: custom\n    regex: 'UNIQUEPATTERN'\n", encoding='utf-8')
        directory = self.acme / '.leakguard'
        directory.mkdir()
        os.symlink(real, directory / 'patterns.yaml')                                      # the file is a link
        for mode in LINK_MODES:
            with self.subTest(mode, shape='file'), link_mode(mode):
                code, report = self.patterns('--target', 'acme')
                self.assertEqual((code, report['status']), (2, 'BLOCKED'), report)
                self.assertNotIn('UNIQUEPATTERN', json.dumps(report))
                self.assertNotIn('acme-extra', [item['id'] for item in report['patterns']])
        (directory / 'patterns.yaml').unlink()
        directory.rmdir()
        store = self.work / 'real-leakguard'
        store.mkdir()
        (store / 'patterns.yaml').write_text(real.read_text(encoding='utf-8'), encoding='utf-8')
        os.symlink(store, self.acme / '.leakguard', target_is_directory=True)             # the directory is a link
        code, report = self.patterns('--target', 'acme')
        self.assertEqual((code, report['status']), (2, 'BLOCKED'), report)
        self.assertNotIn('UNIQUEPATTERN', json.dumps(report))

    def test_broken_additions_are_blocked_and_unknown_target_is_a_warning(self):
        self.add_additions('schema: 7\npatterns: []\n')
        self.assertEqual(self.patterns('--target', 'acme')[0], 2)
        code, report = self.patterns('--target', 'nope')
        self.assertEqual((code, report['status']), (0, 'PASS'))
        self.assertTrue(any('nope' in item for item in report['warnings']))


class MatchTargetsSeparatorsTest(unittest.TestCase):
    """Local sources on Windows: separators and case differ between `source` and the `origin` of the clone."""

    @staticmethod
    def targets(*globs):
        return {'acme': {'scope': 'org-private', 'kind': 'org', 'authors': [], 'remote_match': list(globs)}}

    @staticmethod
    def windows_normcase(text):
        return text.replace('/', '\\').lower()

    def test_separators_are_equivalent_on_every_system(self):
        for pattern, remote in ((r'C:\Users\User\Repo', 'C:/Users/User/Repo'), ('C:/Users/User/Repo', r'C:\Users\User\Repo'),
                                (r'C:\Users\User\Repo', r'C:\Users\User\Repo')):
            with self.subTest(pattern=pattern, remote=remote):
                self.assertEqual(leakguard.match_targets(self.targets(pattern), [remote]), ['acme'])
        for remote in (r'C:\Users\User\Repo-other', 'C:/Users/User/Repo-ops', 'C:/Users/User'):
            with self.subTest(remote=remote):
                self.assertEqual(leakguard.match_targets(self.targets(r'C:\Users\User\Repo'), [remote]), [])

    @staticmethod
    def posix_normcase(text):
        return text

    def test_case_is_folded_only_where_normcase_folds_it(self):
        pattern, remote = r'C:\Users\User\Repo', 'c:/users/user/repo'
        with mock.patch.object(os.path, 'normcase', self.posix_normcase):                      # POSIX: case matters
            self.assertEqual(leakguard.match_targets(self.targets(pattern), [remote]), [])
        with mock.patch.object(os.path, 'normcase', self.windows_normcase):
            self.assertEqual(leakguard.match_targets(self.targets(pattern), [remote]), ['acme'])
            self.assertEqual(leakguard.match_targets(self.targets('C:/USERS/user/repo'), [r'c:\Users\USER\repo']), ['acme'])
            self.assertEqual(leakguard.match_targets(self.targets(pattern), ['c:/users/user/repo-other']), [])
            self.assertEqual(leakguard.match_targets(self.targets(r'\\Server\Share\Repo'), ['//server/share/repo']), ['acme'])

    def test_urls_are_compared_literally_on_every_host(self):
        targets = self.targets('example.test/acme/skills', 'example.test/acme/skills.git', 'example.test:acme/skills')
        for normcase in (self.posix_normcase, self.windows_normcase):
            with self.subTest(normcase=normcase.__name__), mock.patch.object(os.path, 'normcase', normcase):
                self.assertEqual(leakguard.match_targets(targets, ['https://example.test/acme/skills.git']), ['acme'])
                self.assertEqual(leakguard.match_targets(targets, ['https://example.test/Acme/skills.git']), [])
                self.assertEqual(leakguard.match_targets(targets, ['git@example.test:Acme/skills.git']), [])
                self.assertEqual(leakguard.match_targets(targets, ['git@example.test:acme/skills']), ['acme'])
                # a backslash in a URL is not a separator
                self.assertEqual(leakguard.match_targets(self.targets('example.test/acme/a\\b'), ['https://example.test/acme/a/b']), [])


class HookRewriteTest(GuardSourcesCase):
    def test_a_step_1_1_block_with_explicit_inputs_is_rewritten_idempotently_to_the_config_call(self):
        acme = self.git_layer('acme', 'org')
        identity = self.work / 'ident.yaml'
        write(identity, yaml_text({'schema': 1, 'layer': 'x', 'terms': []}))
        self.guard_config([('acme', 'org', acme)], {'acme': {'scope': 'org-private'}})
        first = leakguard.install_hook(acme, identity_files=[str(identity)])
        self.assertEqual(first['status'], 'PASS')
        self.assertEqual(leakguard.hook_state(acme)['state'], 'legacy')
        self.assertIn('--identity', (acme / '.git/hooks/pre-commit').read_text(encoding='utf-8'))
        rewritten = leakguard.install_hook(acme, target='acme')
        self.assertEqual([item['action'] for item in rewritten['hooks']], ['updated', 'updated'])
        self.assertEqual(leakguard.hook_state(acme)['state'], 'ok')
        for name in ('pre-commit', 'pre-push'):
            text = (acme / '.git/hooks' / name).read_text(encoding='utf-8')
            self.assertNotIn('--identity', text)
            self.assertIn('--target acme', text)
        again = leakguard.install_hook(acme, target='acme')
        self.assertEqual([item['action'] for item in again['hooks']], ['unchanged', 'unchanged'])
        self.assertEqual((acme / '.git/hooks/pre-commit').read_text(encoding='utf-8').count(leakguard.HOOK_BEGIN), 1)

    def test_check_layers_flags_the_old_block_as_a_warning(self):
        acme = self.git_layer('acme', 'org')
        identity = self.work / 'ident.yaml'
        write(identity, yaml_text({'schema': 1, 'layer': 'x', 'terms': []}))
        leakguard.install_hook(acme, identity_files=[str(identity)])
        self.write_config([('core', 'core', self.layer_dir('core', 'core')), ('acme', 'org', acme)], [(['**'], ['core', 'acme'])])
        findings = layers.check_layers(layers.resolve_layers(self.project('p')))
        legacy = [item for item in findings if item['code'].startswith('hook-')]
        self.assertEqual([(item['code'], item['severity'], item['layer']) for item in legacy], [('hook-legacy', 'warning', 'acme')])


if __name__ == '__main__':
    unittest.main()
