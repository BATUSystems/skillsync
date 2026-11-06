"""Step 3.0t: path case on macOS (spec "Dobór warstw": Windows and macOS compare paths without case, Linux with
case; URLs and scp-like addresses literally) for `layer_rules`, the leak guard and the D73 link ban.

Every case runs for three simulated hosts: `linux`, `darwin` and `win32`. The host is simulated by patching
`sys.platform` and `os.path.normcase` (`ntpath.normcase` for Windows), or by an explicit `system` for the
`layer_rules` functions. A path that differs only in case is one directory on macOS/Windows; this Linux host keeps
two directories then, so where the code needs an existing directory the test creates an identical copy under
the other spelling (what a case-insensitive file system shows under both names).
All names are synthetic; machine state is isolated (layers_helpers).
"""
import contextlib
import ntpath
import os
import shutil
import sys
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(ROOT))

from layers_helpers import LayersCase, make_layer, needs_os_symlinks, write, yaml_text  # noqa: E402
from skillsync_workflow import layers, leakguard  # noqa: E402

HOSTS = ('linux', 'darwin', 'win32')
FOLDS = {'linux': False, 'darwin': True, 'win32': True}          # case-insensitive file systems
SYSTEM = {'linux': 'Linux', 'darwin': 'Darwin', 'win32': 'Windows'}


@contextlib.contextmanager
def host(name):
    """Simulate the host `name` for code that reads `sys.platform` / `os.path.normcase`."""
    normcase = ntpath.normcase if name == 'win32' else (lambda text: text)
    with mock.patch.object(sys, 'platform', name), mock.patch.object(os.path, 'normcase', normcase):
        yield name


# ====================================================================== path_case_key / _key_within (unit)


class PathCaseKeyTest(unittest.TestCase):
    PAIRS = (('/w/Projekt/App', '/w/projekt/app'),
             ('/w/ŁÓDŹ/Żółw', '/w/łódź/żółw'),                         # Polish letters
             ('/w/Straße', '/w/STRASSE'),                               # `ß` folds to `ss` (casefold)
             ('/w/İzmir', '/w/i̇zmir'))                                  # `İ` folds to `i` + combining dot

    def test_the_host_key_folds_case_only_on_macos_and_windows(self):
        for name in HOSTS:
            with self.subTest(host=name), host(name):
                for upper, lower in self.PAIRS[:2]:
                    with self.subTest(path=upper):
                        self.assertEqual(layers.path_case_key(upper) == layers.path_case_key(lower), FOLDS[name])
                self.assertEqual(layers.path_case_key('/w/Repo'), '/w/repo' if FOLDS[name] else '/w/Repo')

    def test_macos_uses_casefold_not_lower(self):
        with host('darwin'):
            for upper, lower in self.PAIRS:
                with self.subTest(path=upper):
                    self.assertEqual(layers.path_case_key(upper), layers.path_case_key(lower))
        for upper, lower in self.PAIRS:
            with self.subTest(path=upper, system='Darwin'):
                self.assertEqual(layers.path_case_key(upper, 'Darwin'), layers.path_case_key(lower, 'Darwin'))

    def test_the_explicit_system_gives_the_same_answer_without_the_host(self):
        for name in HOSTS:                                                   # the real host stays unpatched
            for upper, lower in self.PAIRS[:2]:
                with self.subTest(system=SYSTEM[name], path=upper):
                    self.assertEqual(layers.path_case_key(upper, SYSTEM[name]) == layers.path_case_key(lower, SYSTEM[name]),
                                     FOLDS[name])

    def test_windows_separators_become_slashes(self):
        with host('win32'):
            self.assertEqual(layers.path_case_key(r'C:\Users\User\Repo'), 'c:/users/user/repo')
        self.assertEqual(layers.path_case_key(r'C:\Users\User\Repo', 'Windows'), 'c:/users/user/repo')

    def test_key_within_is_equality_or_a_child_below_a_separator(self):
        within = layers._key_within
        self.assertTrue(within('/a/b', '/a/b'))
        self.assertTrue(within('/a/b/c', '/a/b'))
        self.assertTrue(within('/a/b/c', '/a/b/'))
        self.assertTrue(within('/a', '/'))
        self.assertTrue(within('c:/users/user', 'c:/'))
        self.assertFalse(within('/a/bc', '/a/b'))                            # a sibling with a longer name
        self.assertFalse(within('/a', '/a/b'))
        for name in HOSTS:
            with self.subTest(host=name), host(name):
                key = layers.path_case_key
                self.assertEqual(within(key('/w/Layers/Core/skills'), key('/w/layers/core')), FOLDS[name])
                self.assertFalse(within(key('/w/Layers/Core-extra'), key('/w/layers/core')))


# ====================================================================== layer_rules (Projekt vs projekt)


class LayerRulesCaseTest(LayersCase):
    def test_path_matches_per_system(self):
        for name in HOSTS:
            system = SYSTEM[name]
            for path, pattern in (('/w/projekt/app', '/w/Projekt/**'), ('/w/PROJEKT/app', '/w/projekt/*'),
                                  ('/w/żółw/app', '/w/Żółw/**'), ('/w/x/Projekt', 'projekt')):
                with self.subTest(system=system, path=path, pattern=pattern):
                    self.assertEqual(layers.path_matches(path, pattern, system), FOLDS[name])
            self.assertTrue(layers.path_matches('/w/Projekt/app', '/w/Projekt/**', system))     # exact case: always
        self.assertTrue(layers.path_matches('/w/STRASSE/app', '/w/Straße/**', 'Darwin'))

    def test_a_rule_selects_layers_of_a_project_spelled_with_another_case(self):
        core = make_layer(self.work / 'layers' / 'core', 'core', 'core')
        org = make_layer(self.work / 'layers' / 'acme', 'acme', 'org')
        project = self.project('projekt/app')
        self.write_config(layers=[('core', 'core', core), ('acme', 'org', org)],
                          rules=[([f'{self.work}/Projekt/**'], ['core', 'acme'])])
        for name in HOSTS:
            with self.subTest(system=SYSTEM[name]), mock.patch.object(layers, 'current_system', return_value=SYSTEM[name]):
                if FOLDS[name]:
                    self.assertEqual(layers.resolve_layers(project).ids, ['core', 'acme'])
                else:
                    with self.assertRaises(layers.LayerError) as caught:
                        layers.resolve_layers(project)
                    self.assertEqual(caught.exception.code, 'no-rule')


# ====================================================================== leak guard: match_targets, select_target


class MatchTargetsCaseTest(unittest.TestCase):
    @staticmethod
    def targets(*globs):
        return {'acme': {'scope': 'org-private', 'kind': 'org', 'authors': [], 'remote_match': list(globs)}}

    def test_a_local_remote_match_folds_case_on_macos_and_windows_only(self):
        for name in HOSTS:
            with self.subTest(host=name), host(name):
                for pattern, remote in (('/Users/Dev/Repos/Acme', '/users/dev/repos/acme'),
                                        ('/Users/Dev/Repos/Acme*', '/users/dev/repos/acme-tools'),
                                        ('/w/Łódź/acme', '/w/łódź/ACME'), ('C:/Users/User/Acme', 'c:/users/user/acme')):
                    with self.subTest(pattern=pattern, remote=remote):
                        self.assertEqual(leakguard.match_targets(self.targets(pattern), [remote]),
                                         ['acme'] if FOLDS[name] else [])
                self.assertEqual(leakguard.match_targets(self.targets('/Users/Dev/Repos/Acme'), ['/Users/Dev/Repos/Acme']),
                                 ['acme'])                                                  # exact case: always
                self.assertEqual(leakguard.match_targets(self.targets('/Users/Dev/Repos/Acme'), ['/users/dev/repos/other']),
                                 [])

    def test_urls_and_scp_addresses_stay_literal_on_every_host(self):
        targets = self.targets('example.test/acme/skills*', 'example.test:acme/skills*')
        for _name in HOSTS:
            with self.subTest(host=_name), host(_name):
                self.assertEqual(leakguard.match_targets(targets, ['https://example.test/acme/skills.git']), ['acme'])
                self.assertEqual(leakguard.match_targets(targets, ['git@example.test:acme/skills.git']), ['acme'])
                for remote in ('https://example.test/Acme/skills.git', 'https://Example.test/acme/skills.git',
                               'git@example.test:Acme/skills.git', 'ssh://git@example.test/ACME/skills'):
                    with self.subTest(remote=remote):
                        self.assertEqual(leakguard.match_targets(targets, [remote]), [])
                upper = self.targets('https://example.test/Acme/Skills.git')
                self.assertEqual(leakguard.match_targets(upper, ['https://example.test/acme/skills.git']), [])


class SelectTargetCaseTest(LayersCase):
    def test_the_checkout_is_found_with_another_case_on_macos_and_windows(self):
        repo = self.make_repo('Repo')
        spelled = str(self.work / 'repo')                                  # the same directory on a case-insensitive FS
        for name in HOSTS:
            with self.subTest(host=name), host(name):
                self.assertEqual(leakguard.select_target(repo, None, {'acme': spelled}),
                                 ('acme', []) if FOLDS[name] else (None, []))
                self.assertEqual(leakguard.select_target(repo, None, {'acme': str(repo)}), ('acme', []))
                self.assertEqual(leakguard.select_target(repo, None, {'acme': str(self.work / 'repo-other')}), (None, []))


# ====================================================================== D73: links in a layer, scan path with another case


@needs_os_symlinks
class LinkBanCaseTest(LayersCase):
    def setUp(self):
        super().setUp()
        self.layer = make_layer(self.work / 'Layers' / 'Core', 'core', 'core', {'skills/x/SKILL.md': 'x\n'})
        outside = write(self.work / 'outside' / 'secret.md', 'outside the layer\n')
        os.symlink(outside, self.layer / 'skills' / 'x' / 'link.md')         # a link leaving the layer
        sibling = make_layer(self.work / 'Layers' / 'Core-extra', 'extra', 'org')
        self.write_config(layers=[('core', 'core', self.layer), ('extra', 'org', sibling)],
                          rules=[(['**'], ['core', 'extra'])])

    def assert_blocked(self, path):
        with self.assertRaises(layers.LayerError) as caught:
            layers.leakguard_inputs(path=path)
        self.assertEqual(caught.exception.code, 'symlink-in-layer')
        self.assertIn('skills/x/link.md', str(caught.exception))

    def test_the_exact_path_is_blocked_on_every_host(self):
        for _name in HOSTS:
            with self.subTest(host=_name), host(_name):
                self.assert_blocked(self.layer)
                self.assert_blocked(self.layer / 'skills')                       # scanned inside the layer
                self.assert_blocked(self.layer.parent)                           # the layer inside the scanned tree

    def test_another_case_cannot_bypass_the_ban_on_macos_and_windows(self):
        spellings = {'the layer': self.work / 'layers' / 'core',
                     'a directory inside': self.work / 'LAYERS' / 'CORE' / 'skills',
                     'a parent of the layer': self.work / 'layers'}
        for name in HOSTS:
            with self.subTest(host=name), host(name):
                for label, path in spellings.items():
                    with self.subTest(scan=label):
                        if FOLDS[name]:
                            self.assert_blocked(path)
                        else:                                                     # Linux: another directory (unchanged)
                            self.assertEqual(layers.leakguard_inputs(path=path)['target'], None)

    def test_a_sibling_with_a_longer_name_is_not_the_layer(self):
        for _name in HOSTS:
            with self.subTest(host=_name), host(_name):
                for path in (self.work / 'Layers' / 'Core-extra', self.work / 'layers' / 'core-extra'):
                    with self.subTest(path=path.name):
                        layers.leakguard_inputs(path=path)                         # no LayerError


# ====================================================================== macOS: NFC and NFD spellings of one name

NFC_NAME = unicodedata.normalize('NFC', 'Zażółć gęślą')
NFD_NAME = unicodedata.normalize('NFD', NFC_NAME)
NORMALIZES = ('linux', 'darwin')          # Windows (NTFS) keeps the form: not part of the contract, not asserted


class UnicodeFormTest(LayersCase):
    """APFS/HFS+ treat NFC and NFD spellings as one name (review 3.0-1, major); Linux keeps them apart."""

    def test_the_forms_differ_as_text(self):
        self.assertNotEqual(NFC_NAME, NFD_NAME)                              # the test data really differ

    def test_path_case_key_and_path_key_unify_the_forms_on_macos_only(self):
        for name in NORMALIZES:
            with self.subTest(host=name), host(name):
                same = name == 'darwin'
                self.assertEqual(layers.path_case_key(f'/w/{NFC_NAME}') == layers.path_case_key(f'/w/{NFD_NAME}'), same)
                self.assertEqual(layers._path_key(self.work / NFC_NAME) == layers._path_key(self.work / NFD_NAME), same)
                upper = NFD_NAME.upper()                                    # case and form differ at once
                self.assertEqual(layers.path_case_key(f'/w/{NFC_NAME}') == layers.path_case_key(f'/w/{upper}'), same)
        self.assertEqual(layers.path_case_key(f'/w/{NFC_NAME}', 'Darwin'), layers.path_case_key(f'/w/{NFD_NAME}', 'Darwin'))
        self.assertNotEqual(layers.path_case_key(f'/w/{NFC_NAME}', 'Linux'), layers.path_case_key(f'/w/{NFD_NAME}', 'Linux'))

    def test_match_targets_and_select_target_unify_the_forms_on_macos_only(self):
        targets = {'acme': {'scope': 'org-private', 'kind': 'org', 'authors': [],
                            'remote_match': [f'/w/{NFC_NAME}/acme']}}
        repo = self.make_repo(NFC_NAME)
        for name in NORMALIZES:
            with self.subTest(host=name), host(name):
                same = name == 'darwin'
                self.assertEqual(leakguard.match_targets(targets, [f'/w/{NFD_NAME}/acme']), ['acme'] if same else [])
                self.assertEqual(leakguard.select_target(repo, None, {'acme': str(self.work / NFD_NAME)}),
                                 ('acme', []) if same else (None, []))
                self.assertEqual(leakguard.match_targets(targets, [f'/w/{NFC_NAME}/acme']), ['acme'])

    def test_a_layer_rule_in_nfc_matches_an_nfd_project_path_on_macos_only(self):
        for name in NORMALIZES:
            with self.subTest(host=name), host(name):
                same = name == 'darwin'
                system = SYSTEM[name]
                self.assertEqual(layers.path_matches(f'/w/{NFD_NAME}/app', f'/w/{NFC_NAME}/**', system), same)
                self.assertEqual(layers.path_matches(f'/w/{NFD_NAME}/app', f'/w/{NFC_NAME}/app', system), same)
                self.assertTrue(layers.path_matches(f'/w/{NFC_NAME}/app', f'/w/{NFC_NAME}/**', system))

    def test_project_name_in_another_unicode_form_warns_on_linux_only(self):
        core = make_layer(self.work / 'layers' / 'core', 'core', 'core')
        project = self.project(f'{NFD_NAME}/app', name=f'{NFC_NAME}/app')       # path NFD, project.name NFC
        self.write_config(layers=[('core', 'core', core)], rules=[(['**'], ['core'])])
        for name in NORMALIZES:
            with self.subTest(host=name), mock.patch.object(layers, 'current_system', return_value=SYSTEM[name]):
                found = [item for item in layers.resolve_layers(project).notes if item['code'] == 'project-name-mismatch']
                self.assertEqual(len(found), 0 if name == 'darwin' else 1)

    @needs_os_symlinks
    def test_an_nfd_scan_path_cannot_bypass_the_link_ban_on_macos(self):
        layer = make_layer(self.work / NFC_NAME, 'core', 'core', {'skills/x/SKILL.md': 'x\n'})
        os.symlink(write(self.work / 'outside' / 'secret.md', 'outside\n'), layer / 'skills' / 'x' / 'link.md')
        self.write_config(layers=[('core', 'core', layer)], rules=[(['**'], ['core'])])
        for name in NORMALIZES:
            with self.subTest(host=name), host(name):
                for path in (self.work / NFD_NAME, self.work / NFD_NAME / 'skills'):
                    with self.subTest(path=str(path.relative_to(self.work))):
                        if name == 'darwin':
                            with self.assertRaises(layers.LayerError) as caught:
                                layers.leakguard_inputs(path=path)
                            self.assertEqual(caught.exception.code, 'symlink-in-layer')
                        else:                                                 # Linux: another directory (unchanged)
                            self.assertIsNone(layers.leakguard_inputs(path=path)['target'])


# ====================================================================== the host simulation itself


class HostSimulationTest(unittest.TestCase):
    """Review 3.0-1 (minor): a red assertion inside a simulated host must not leave the patch behind."""

    def test_a_failing_assertion_inside_a_simulated_host_restores_the_platform(self):
        platform, normcase = sys.platform, os.path.normcase

        class Planted(unittest.TestCase):
            def test_red(self):
                for name in HOSTS:
                    with self.subTest(host=name), host(name):
                        self.assertEqual(sys.platform, 'never')             # planted red assertion

            def test_error(self):
                with host('darwin'):
                    raise RuntimeError('planted')

        result = unittest.TestResult()
        unittest.defaultTestLoader.loadTestsFromTestCase(Planted).run(result)
        self.assertEqual((len(result.failures), len(result.errors)), (3, 1), (result.failures, result.errors))
        self.assertEqual(sys.platform, platform)
        self.assertIs(os.path.normcase, normcase)


# ====================================================================== _path_key: isolation, migrate, --finish


class PathKeyCase(LayersCase):
    def twin(self, path, other):
        """`other` = the same directory as `path` under another spelling (a copy on this case-sensitive host)."""
        shutil.copytree(path, other)
        return other

    def config(self, data):
        self.config_file.write_text(yaml_text(data), encoding='utf-8')


class PathKeyTest(PathKeyCase):
    def test_the_key_folds_case_on_macos_and_windows(self):
        for name in HOSTS:
            with self.subTest(host=name), host(name):
                self.assertEqual(layers._path_key(self.work / 'Lib') == layers._path_key(self.work / 'lib'), FOLDS[name])
                self.assertEqual(layers._path_key(self.work / 'Lib'), layers._path_key(self.work / 'Lib'))

    def test_linux_keeps_a_backslash_in_a_name(self):
        # Linux behaviour unchanged: `\` is an ordinary character of a file name there, so `a\b` and `a/b` are two
        # different directories (before 3.0 `_path_key` was `os.path.normcase(realpath)` = no change on POSIX).
        with host('linux'):
            self.assertNotEqual(layers._path_key('/w/a\\b'), layers._path_key('/w/a/b'))

    def test_isolation_treats_another_spelling_of_a_layer_as_the_same_tree(self):
        core = make_layer(self.work / 'Lib', 'core', 'core')
        write(core / 'leakguard' / 'identity.yaml', yaml_text({'schema': 1, 'layer': 'foreign', 'terms': []}))
        twin = self.twin(core, self.work / 'lib')
        self.config({'layers': [{'id': 'core', 'kind': 'core', 'path': str(core)}],
                     'layer_rules': [{'match': ['**'], 'use': ['core']}],
                     'isolation': {'allowed_layers': ['core']}, 'legacy_library': str(twin)})
        for name in HOSTS:
            with self.subTest(host=name), host(name):
                findings = layers.isolation_findings(layers.load_machine_config(), 'Linux')
                foreign = [item for item in findings if item['code'] == 'isolation-foreign-identity']
                self.assertEqual(len(foreign), 1 if FOLDS[name] else 2, findings)   # Linux: two trees, two files

    def test_legacy_library_and_the_old_key_spelled_differently_are_not_a_conflict(self):
        core = make_layer(self.work / 'core', 'core', 'core')
        self.config({'layers': [{'id': 'core', 'kind': 'core', 'path': str(core)}],
                     'layer_rules': [{'match': ['**'], 'use': ['core']}], 'isolation': {'allowed_layers': ['core']},
                     'library': str(self.work / 'Old-Library'), 'legacy_library': str(self.work / 'old-library')})
        before = self.config_file.read_bytes()
        for name in HOSTS:
            with self.subTest(host=name), host(name):
                report = layers.layers_migrate()
                if FOLDS[name]:
                    self.assertEqual(report['status'], 'PASS', report)
                else:
                    self.assertEqual((report['status'], report['code']), ('BLOCKED', 'conflict'), report)
                self.assertEqual(self.config_file.read_bytes(), before)            # dry-run

    def test_a_layer_source_spelled_differently_adopts_the_configured_layer(self):
        core = make_layer(self.work / 'Core', 'core', 'core')
        org = make_layer(self.work / 'Acme', 'acme', 'org')
        self.config({'layers': [{'id': 'core', 'kind': 'core', 'path': str(core)},
                                {'id': 'acme', 'kind': 'org', 'path': str(org)}],
                     'layer_rules': [{'match': ['**'], 'use': ['core', 'acme']}],
                     'isolation': {'allowed_layers': ['core', 'acme']},
                     'leakguard': {'targets': {'core': {'scope': 'public'}, 'acme': {'scope': 'org-private'}}}})
        sources = {'org layer': f'acme={self.twin(org, self.work / "acme")}',
                   'core layer': f'core={self.twin(core, self.work / "core")}'}
        before = self.config_file.read_bytes()
        for name in HOSTS:
            with self.subTest(host=name), host(name):
                for label, source in sources.items():
                    with self.subTest(source=label):
                        report = layers.layers_migrate(sources=[source])
                        if FOLDS[name]:
                            self.assertEqual((report['status'], report['changed']), ('PASS', False), report)
                        else:
                            self.assertEqual((report['status'], report['code']), ('BLOCKED', 'conflict'), report)
                        self.assertEqual(self.config_file.read_bytes(), before)

    def test_adopt_legacy_spelled_differently_is_the_configured_core(self):
        core = make_layer(self.work / 'Lib', 'core', 'core')
        twin = self.twin(core, self.work / 'lib')
        self.config({'layers': [{'id': 'core', 'kind': 'core', 'path': str(core)}],
                     'layer_rules': [{'match': ['**'], 'use': ['core']}], 'isolation': {'allowed_layers': ['core']},
                     'leakguard': {'targets': {'core': {'scope': 'public'}}}})
        for name in HOSTS:
            with self.subTest(host=name), host(name):
                report = layers.layers_migrate(adopt_legacy=str(twin))
                if FOLDS[name]:
                    self.assertEqual(report['status'], 'PASS', report)
                else:
                    self.assertEqual((report['status'], report['code']), ('BLOCKED', 'conflict'), report)

    def test_finish_treats_legacy_library_spelled_like_a_layer_as_in_use(self):
        core = make_layer(self.work / 'Lib', 'core', 'core')
        twin = self.twin(core, self.work / 'lib')
        self.config({'layers': [{'id': 'core', 'kind': 'core', 'path': str(core)}],
                     'layer_rules': [{'match': ['**'], 'use': ['core']}], 'isolation': {'allowed_layers': ['core']},
                     'legacy_library': str(twin)})
        before = self.config_file.read_bytes()
        for name in HOSTS:
            with self.subTest(host=name), host(name):
                report = layers.layers_migrate(finish=True)
                if FOLDS[name]:
                    self.assertEqual((report['status'], report['dry_run']), ('PASS', True), report)
                else:                                                             # Linux: a separate old clone
                    self.assertEqual((report['status'], report['code']), ('BLOCKED', 'finish-blocked'), report)
                    self.assertIn('still exists', report['reason'])
                self.assertEqual(self.config_file.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
