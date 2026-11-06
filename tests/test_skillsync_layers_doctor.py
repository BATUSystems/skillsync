"""`skillsync doctor` on layers (step 2.3t): K3 (manifest keys, `sync` library setting, `project.name`, no rule),
K12 (`core_min`, `requires`, `version`, README/CHANGELOG), the mapping of `check_layers` findings (decision 2.3 Q1:
`blocked` -> error, `error` -> warning, `info` omitted), installed skill symlinks (only provider / composite /
stale composite / foreign) and `layer:<id>:` instruction references.

All layers are synthetic and the machine state is isolated (`ComposeCase`). No mutation proofs (D113): the
negative assertions have a positive control in the same test instead (e.g. a foreign link is reported, so
"no error for a composite" is not vacuous)."""
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from layers_helpers import SKILL_TEXT, ComposeCase, commit, each_mode, generator_version, git, link_mode, write  # noqa: E402
from layers_helpers import needs_os_symlinks  # noqa: E402

MANIFEST = '.github/.skillsync.yaml'
CORE_FILES = {
    'instructions/_base.md': '# Core base\n<!-- SKILLSYNC:BASE-END -->\n',
    'instructions/proj.md': '# Project template\n',
    'instructions/x.md': 'core instruction\n',
    'skills/alpha/SKILL.md': SKILL_TEXT.format('alpha'),
    'skills/alpha/core.md': 'core part\n',
    'skills/beta/SKILL.md': SKILL_TEXT.format('beta'),
}
ORG_FILES = {
    'instructions/y.md': 'org instruction that is longer than ten bytes\n',
    'skills/alpha/org.md': 'org part\n',
    'skills/gamma/SKILL.md': SKILL_TEXT.format('gamma'),
}


class DoctorCase(ComposeCase):
    def setUp(self):
        super().setUp()
        self.version = generator_version()

    def layers(self, org=True, core_kwargs=None, org_kwargs=None, org_files=None):
        """core (version = generator version: no `core-version-mismatch`) and optionally org, rule `**`."""
        # (`ComposeCase.add_layers` takes per-layer kwargs keyed by the id, which collides with its `core`/`org`
        # parameters, so the layers are built here directly.)
        self.layer_paths['core'] = self.layer_dir('core', 'core', dict(CORE_FILES),
                                                  **{'version': self.version, **(core_kwargs or {})})
        entries = [('core', 'core', self.layer_paths['core'])]
        if org:
            self.layer_paths['org'] = self.layer_dir('org', 'org', dict(ORG_FILES if org_files is None else org_files),
                                                     **(org_kwargs or {}))
            entries.append(('org', 'org', self.layer_paths['org']))
        self.write_config(layers=entries, rules=[(['**'], [item[0] for item in entries])])
        return self.layer_paths

    def doctor(self, project, *args, mode='symlink', code=None):
        with link_mode(mode):
            process = self.cli('doctor', str(project), *args, '--json')
        self.assertNotIn('Traceback', process.stderr)
        try:
            report = json.loads(process.stdout)
        except ValueError:
            self.fail(f'doctor output is not JSON (exit {process.returncode}): {process.stdout!r} {process.stderr!r}')
        if code is not None:
            self.assertEqual(process.returncode, code, json.dumps(report, indent=1))
        return report

    @staticmethod
    def found(report, text, path=None):
        """Findings whose reason contains `text` (and with `path`, when given)."""
        return [item for item in report.get('findings', [])
                if text in item['reason'] and (path is None or item['path'] == path)]

    def levels(self, report, text, path=None):
        return [item['level'] for item in self.found(report, text, path)]


class ManifestKeysK3(DoctorCase):
    def test_layers_and_layer_rules_in_the_manifest_are_errors(self):
        self.layers(org=False)
        for key in ('layers', 'layer_rules'):
            with self.subTest(key=key):
                project = self.new_project(f'p-{key}', skills=['beta'], **{key: [{'id': 'x'}]})
                self.link_ok(project)
                report = self.doctor(project, code=1)
                self.assertEqual(report['status'], 'FAIL')
                self.assertEqual(self.levels(report, f'`{key}` is not a manifest key', MANIFEST), ['error'], report)

    def test_a_clean_project_has_no_layer_findings(self):
        """Control for the tests below: the same layers and project give no K3/K12 finding."""
        self.layers()
        project = self.new_project('clean', skills=['alpha', 'beta', 'gamma'])
        self.link_ok(project)
        report = self.doctor(project, code=0)
        self.assertEqual(report['status'], 'PASS')
        unrelated = ('No checks selected', 'delivery.mode')  # validation plan without checks; delivery mode warning (not K3/K12)
        self.assertEqual([f for f in report['findings'] if f['path'].startswith('layer')
                          or (f['path'] == MANIFEST and not any(text in f['reason'] for text in unrelated))], [])

    def test_sync_library_is_ignored_with_a_warning(self):
        self.layers(org=False)
        legacy = self.work / 'legacy-library'
        write(legacy / 'skills' / 'only-legacy' / 'SKILL.md', SKILL_TEXT.format('only-legacy'))
        write(legacy / 'instructions' / 'proj.md', 'legacy template\n')
        project = self.new_project('legacy', skills=['beta'], sync={'library': str(legacy)})
        self.link_ok(project)
        report = self.doctor(project, code=0)
        self.assertEqual(self.levels(report, 'Manifest `sync: {library: ...}` is ignored', MANIFEST), ['warning'], report)
        # Ignored: a skill that exists only in the manifest's library is missing (layers come from the config).
        manifest = project / MANIFEST
        manifest.write_text(manifest.read_text(encoding='utf-8').replace('- beta', '- only-legacy'), encoding='utf-8')
        report = self.doctor(project, code=1)
        self.assertEqual(self.levels(report, 'Missing skill', 'skills/only-legacy/SKILL.md'), ['error'], report)

    def test_project_name_that_does_not_match_the_path_is_a_warning(self):
        self.layers(org=False)
        good = self.new_project('named', skills=['beta'], project={'name': 'named'})
        bad = self.new_project('elsewhere', skills=['beta'], project={'name': 'Team/other-name'})
        for project in (good, bad):
            self.link_ok(project)
        self.assertEqual(self.found(self.doctor(good, code=0), 'project.name'), [])
        report = self.doctor(bad, code=0)
        self.assertEqual(self.levels(report, 'does not match the project path', MANIFEST), ['warning'], report)
        # The same check when the layers come from `--layer` (no rule involved).
        report = self.doctor(bad, '--layer', f'core={self.layer_paths["core"]}', code=0)
        self.assertEqual(self.levels(report, 'does not match the project path', MANIFEST), ['warning'], report)
        self.assertEqual(self.found(self.doctor(good, '--layer', f'core={self.layer_paths["core"]}', code=0),
                                    'project.name'), [])

    def test_a_worktree_with_layer_flags_has_no_false_project_name_warning(self):
        """Review 2.3 (minor): `project.name` names the main checkout; a linked worktree elsewhere, with layers
        from `--layer` (no rule involved), must not get the mismatch warning (by rule it matches by the main
        repository first)."""
        self.layers(org=False)
        main = self.new_project('app', skills=['beta'], project={'name': 'app'})
        git(main, 'init', '-q', '-b', 'main')
        git(main, 'config', 'user.email', 'dev@example.test')
        git(main, 'config', 'user.name', 'Dev')
        git(main, 'add', '-A')
        commit(main, 'init')
        worktree = self.work / 'trees' / 'topic-branch'
        git(main, 'worktree', 'add', '-q', '-b', 'topic', str(worktree))
        flag = f'core={self.layer_paths["core"]}'
        for args in (('--layer', flag), ()):
            with self.subTest(args=args):
                report = self.doctor(worktree, *args)
                self.assertNotEqual(report.get('status'), 'BLOCKED', report)
                self.assertEqual(self.found(report, 'project.name'), [], report)

    def test_project_without_a_matching_rule_is_blocked(self):
        self.layers(org=False)
        project = self.new_project('ruled', skills=['beta'])
        self.write_config(layers=[('core', 'core', self.layer_paths['core'])],
                          rules=[([str(self.work / 'nowhere') + '/**'], ['core'])])
        with link_mode('symlink'):
            process = self.cli('doctor', str(project), '--json')
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertIn('layers use', report['reason'])
        # Decision 2.3 Q2: a command that never reads a layer keeps its exit code without a rule.
        with link_mode('symlink'):
            status = self.cli('status', str(project), '--json')
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)


class LayerMetadataK12(DoctorCase):
    def doctor_with_org(self, **org_kwargs):
        self.layers(org_kwargs=org_kwargs)
        project = self.new_project('k12', skills=['beta'])
        with link_mode('symlink'):
            self.cli('link', str(project))  # may refuse (BLOCKED) for these layers; doctor still reports
        return self.doctor(project)

    def assert_layer_error(self, report, text):
        self.assertEqual(report['status'], 'FAIL', report)
        self.assertEqual(self.levels(report, text, 'layer:org'), ['error'], report)
        self.assertTrue(all('(details: `skillsync layers check`)' in f['reason'] for f in self.found(report, text)))

    def test_core_min_above_the_generator_version_is_an_error(self):
        major = int(self.version.split('.')[0]) + 1
        self.assert_layer_error(self.doctor_with_org(core_min=f'{major}.0.0'), f'needs generator >= {major}.0.0')

    def test_core_min_equal_to_the_generator_version_is_fine(self):
        report = self.doctor_with_org(core_min=self.version)
        self.assertEqual(self.found(report, 'needs generator'), [])

    def test_unmet_requires_is_an_error(self):
        self.assert_layer_error(self.doctor_with_org(requires=['absent-layer']), 'requires layer absent-layer')

    def test_met_requires_is_fine(self):
        self.assertEqual(self.found(self.doctor_with_org(requires=['core']), 'requires layer'), [])

    def test_missing_version_is_an_error(self):
        self.assert_layer_error(self.doctor_with_org(version=None), 'has no version')

    def test_missing_core_min_is_an_error(self):
        self.assert_layer_error(self.doctor_with_org(core_min=False), 'has no core_min')

    def test_missing_readme_and_changelog_are_warnings(self):
        report = self.doctor_with_org(readme=False, changelog=False)
        self.assertEqual(self.levels(report, 'has no README', 'layer:org'), ['warning'], report)
        self.assertEqual(self.levels(report, 'has no CHANGELOG', 'layer:org'), ['warning'], report)
        self.assertEqual(report['status'], 'PASS', report)

    def test_core_version_other_than_the_generator_is_a_warning(self):
        self.layers(core_kwargs={'version': '0.0.1'})
        project = self.new_project('core-version', skills=['beta'])
        self.link_ok(project)
        report = self.doctor(project, code=0)
        self.assertEqual(self.levels(report, 'differs from the generator version', 'layer:core'), ['warning'], report)


class FindingMappingQ1(DoctorCase):
    @needs_os_symlinks
    def test_error_findings_of_the_layers_become_warnings(self):
        """`check_layers` severity `error` (here: a link inside a layer, D73) -> doctor warning, not FAIL."""
        self.layers()
        project = self.new_project('mapped', skills=['beta'])
        self.link_ok(project)
        os.symlink(self.layer_paths['core'] / 'instructions' / 'x.md', self.layer_paths['org'] / 'instructions' / 'link.md')
        report = self.doctor(project, code=0)
        self.assertEqual(self.levels(report, 'instructions/link.md is a symlink', 'layer:org'), ['warning'], report)

    def test_info_findings_are_omitted(self):
        """An absent layer (`layer-absent`, info) gives no doctor finding."""
        self.layers(org=False)
        missing = self.work / 'layers' / 'not-cloned'
        self.write_config(layers=[('core', 'core', self.layer_paths['core']), ('org', 'org', missing)],
                          rules=[(['**'], ['core', 'org'])])
        project = self.new_project('absent', skills=['beta'])
        self.link_ok(project)
        report = self.doctor(project, code=0)
        self.assertEqual([f for f in report['findings'] if 'not-cloned' in f['reason'] or f['path'] == 'layer:org'], [])


class InstalledSkillTargets(DoctorCase):
    """K4 in doctor: the target of an installed skill is the composite or the skill of its only provider."""

    def skill_errors(self, report):
        return [f for f in report['findings'] if f['level'] == 'error' and '/skills/' in f['path'].replace('\\', '/')]

    @each_mode
    def test_only_provider_and_composite_targets_are_not_errors(self, mode):
        self.layers()
        project = self.new_project(f'targets-{mode}', skills=['alpha', 'beta', 'gamma'])
        self.link_ok(project, mode=mode)
        installed = self.skill_dir(project, 'alpha')
        if mode == 'symlink':
            self.assertTrue(installed.is_symlink())
            self.assertIn(self.composites('alpha')[0], os.readlink(installed))
            self.assertEqual(Path(os.path.realpath(self.skill_dir(project, 'gamma'))),
                             Path(os.path.realpath(self.layer_paths['org'] / 'skills' / 'gamma')))
        report = self.doctor(project, mode=mode, code=0)
        self.assertEqual(self.skill_errors(report), [], report)

    @needs_os_symlinks
    def test_a_foreign_symlink_is_still_an_error(self):
        """Control: the state check is not vacuous."""
        self.layers()
        project = self.new_project('foreign', skills=['beta'])
        self.link_ok(project)
        installed = self.skill_dir(project, 'beta')
        other = self.work / 'other-beta'
        write(other / 'SKILL.md', SKILL_TEXT.format('beta'))
        installed.unlink()
        installed.symlink_to(other, target_is_directory=True)
        report = self.doctor(project, code=1)
        self.assertEqual(self.levels(report, 'Foreign installed skill symlink'), ['error'], report)

    def test_a_stale_composite_is_reported_with_its_own_message(self):
        self.layers()
        project = self.new_project('stale', skills=['alpha'])
        self.link_ok(project)
        write(self.layer_paths['org'] / 'skills' / 'alpha' / 'later.md', 'added after link\n')
        report = self.doctor(project, code=1)
        installed = str(self.skill_dir(project, 'alpha'))
        self.assertEqual(self.levels(report, 'points to a stale composite', installed), ['error'], report)
        self.assertIn('skillsync link', self.found(report, 'stale composite')[0]['reason'])
        self.assertEqual(self.found(report, 'Foreign installed skill symlink'), [])
        self.link_ok(project)
        self.assertEqual(self.found(self.doctor(project, code=0), 'stale composite'), [])

    @each_mode
    def test_a_stale_skill_with_a_routed_instruction_inside_it_is_a_fail_not_blocked(self, mode):
        """Review 2.3 (M1): a routing rule naming a file inside the installed skill must not turn the stale
        composite (symlink) / the differing copy (`_files_hash`, copy) into BLOCKED, and the other findings stay."""
        self.layers()
        project = self.new_project(f'routed-{mode}', skills=['alpha'], workflow={
            'routing': {'rules': [{'paths': ['**'], 'instructions': ['.github/skills/alpha/core.md']}]},
            'guides': ['docs/missing-guide.md']})
        self.link_ok(project, mode=mode)
        clean = self.doctor(project, mode=mode)
        self.assertEqual(self.found(clean, 'Missing routed instruction'), [], clean)
        self.assertEqual(self.levels(clean, 'Missing guide'), ['error'], clean)
        if mode == 'symlink':
            write(self.layer_paths['org'] / 'skills' / 'alpha' / 'later.md', 'added after link\n')
            expected = 'points to a stale composite'
        else:
            write(self.layer_paths['org'] / 'skills' / 'alpha' / 'org.md', 'org part changed after link\n')
            expected = 'Installed skill copy differs from source'
        report = self.doctor(project, mode=mode, code=1)
        self.assertEqual(report['status'], 'FAIL', report)
        self.assertEqual(self.levels(report, expected, str(self.skill_dir(project, 'alpha'))), ['error'], report)
        self.assertEqual(self.levels(report, 'Missing guide'), ['error'], report)

    def test_a_copy_that_differs_from_the_layers_is_reported(self):
        self.layers()
        project = self.new_project('copy-diff', skills=['alpha'])
        self.link_ok(project, mode='copy')
        self.assertEqual(self.found(self.doctor(project, mode='copy', code=0), 'differs from source'), [])
        write(self.layer_paths['org'] / 'skills' / 'alpha' / 'org.md', 'org part changed\n')
        report = self.doctor(project, mode='copy', code=1)
        self.assertEqual(self.levels(report, 'Installed skill copy differs from source'), ['error'], report)


class LayerReferences(DoctorCase):
    """`layer:<id>:<path>` (and `library:` = core) in instruction paths of the workflow config."""

    def project_with_budgets(self, budgets, name='refs'):
        self.layers()
        project = self.new_project(name, skills=['beta'], workflow={'context': {'budgets': budgets}})
        self.link_ok(project)
        return project

    def test_budgets_resolve_to_the_owning_layer(self):
        project = self.project_with_budgets({'layer:org:instructions/y.md': 10, 'library:instructions/x.md': 1000})
        report = self.doctor(project, code=1)
        sizes = {Path(item['path']).resolve(): item for item in report['budgets']}
        org_file = (self.layer_paths['org'] / 'instructions' / 'y.md').resolve()
        core_file = (self.layer_paths['core'] / 'instructions' / 'x.md').resolve()
        self.assertEqual(sizes[org_file]['bytes'], org_file.stat().st_size)
        self.assertEqual(sizes[core_file]['bytes'], core_file.stat().st_size)
        exceeded = [Path(f['path']).resolve() for f in self.found(report, 'Instruction budget exceeded')]
        self.assertEqual(exceeded, [org_file])

    def test_a_reference_to_an_inactive_layer_is_blocked(self):
        project = self.project_with_budgets({'layer:ghost:instructions/y.md': 10}, name='ghost')
        report = self.doctor(project, code=2)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertIn('ghost', report['reason'])

    def test_a_reference_escaping_its_layer_is_blocked(self):
        project = self.project_with_budgets({'layer:org:../core/instructions/x.md': 10}, name='escape')
        report = self.doctor(project, code=2)
        self.assertIn('escapes', report['reason'])


if __name__ == '__main__':
    unittest.main()
