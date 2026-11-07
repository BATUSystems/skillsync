"""Step 2.4t: `skillsync layers migrate` (K11, D41) and the 0.9.x legacy bridge that is no longer created (K15, D60, D63).

Contract under test (spec K11/K15, decisions of step 2.4 in the plan "Wynik"):
- dry-run by default (also `--dry-run`), writing only with `--apply`; a backup copy of the config before writing;
  idempotent (a second `--apply` changes nothing and makes no backup); every other config key is kept, the old
  `library` key becomes `legacy_library`;
- `--layer-source ID=PATH` only local paths (a URL = BLOCKED); with existing rules a new personal layer is not
  added to them and `isolation.allowed_layers` is not widened;
- `--manifests` removes the library setting from project manifests with backups in the data dir of skillsync
  (`<data dir>/backups/manifests/<stamp>/...`), never next to the manifest in the project repository;
- `--finish` removes `legacy_library` only after the old clone is gone and `layers check --isolation` = PASS;
- after the migration `link --dry-run` plans no removal of the user's own files;
- the core layer is a real layer: `--layer-source core=PATH` (or an old library with `layer.yaml`); `--adopt-legacy`
  (removed in 1.0, hidden from `--help`) is BLOCKED with the instruction and writes nothing;
- a config written by 0.9.x with the bridge (`synthetic` core entry, written here by `MigrateCase.bridge_config`) is
  still read: `link`/`layers` commands = BLOCKED `legacy-config`, `layers check --isolation` = `isolation-legacy-bridge`,
  and `layers migrate --layer-source core=PATH` replaces it with a real core (D162, D166).
All names are synthetic; machine state is isolated (layers_helpers).
"""
import json
import os
import shutil
import sys
import unittest
from pathlib import Path

import yaml

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(ROOT))

from layers_helpers import (LINK_MODES, SKILL_TEXT, LayersCase, commit, each_mode, generator_version, git, machine_data_dir,  # noqa: E402
                            link_mode, make_layer, tree_snapshot, write, yaml_text)
from skillsync_workflow import layers  # noqa: E402
from layers_helpers import needs_os_symlinks  # noqa: E402

OLD_LIBRARY_FILES = {
    'skills/feature/SKILL.md': SKILL_TEXT.format('feature'),
    'skills/feature/references/ref.md': 'reference\n',
    'agents/implementer.md': '---\nname: implementer\ndescription: role\n---\nImplement.\n',
    'instructions/_base.md': '# Base\n\nShared rules.\n\n<!-- SKILLSYNC:BASE-END -->\n',
    'instructions/proj.md': '# Template\n\nProject rules.\n',
}
KEPT_KEYS = {
    'discover_paths': [],                         # replaced per test by a real directory
    'tags': {'web': ['feature']},
    'variables': {'team': 'synthetic'},
    'python_command': 'python3',
    'entrypoint': 'agents',
    'claude_md_template': 'default',
    'custom_future_key': {'nested': [1, 2]},
    'memory_sync': {'files': ['~/notes/memory.md'], 'targets': [{'host': 'box.example.test', 'dir': '/srv/memory'}]},
    'remotes': {'laptop': 'user@laptop.example.test'},
}


class MigrateCase(LayersCase):
    def setUp(self):
        super().setUp()
        self.library = self.work / 'old-library'
        for rel, text in OLD_LIBRARY_FILES.items():
            write(self.library / rel, text)
        self.projects = self.work / 'repos'
        self.projects.mkdir()

    # ------------------------------------------------------------------ helpers

    def old_config(self, comment=True, **extra):
        """Machine config as used before layers: the `library:` key plus the keys used today."""
        lines = ['# machine config of the tester\n'] if comment else []
        lines.append(f'library: {self.library}   # the old single library\n')
        data = dict(KEPT_KEYS, discover_paths=[str(self.projects)])
        data.update(extra)
        text = ''.join(lines) + yaml_text(data)
        self.config_file.write_text(text, encoding='utf-8')
        return text

    def config(self):
        return yaml.safe_load(self.config_file.read_text(encoding='utf-8')) or {}

    def backups(self):
        return sorted(self.config_file.parent.glob(self.config_file.name + '.bak-*'))

    def machine_tree(self):
        """Snapshot of the whole isolated machine directory holding the config: the config, its backups, the
        isolated home and the XDG data/cache directories (so a stray write anywhere on the machine is visible)."""
        return tree_snapshot(self.config_file.parent, mtimes=True)

    def migrate(self, *args, code=0):
        process = self.cli('layers', 'migrate', *args, '--json')
        self.assertNotIn('Traceback', process.stderr)
        self.assertEqual(process.returncode, code, process.stdout + process.stderr)
        return json.loads(process.stdout)

    def new_project(self, name, manifest_text=None, *, with_git=True):
        path = self.projects / name
        text = manifest_text if manifest_text is not None else yaml_text(
            {'skills': ['feature'], 'instructions': {'merge': ['_base'], 'template': 'proj'}, 'claude': True,
             'codex': False, 'mcp': False})
        write(path / '.github' / '.skillsync.yaml', text)
        if with_git:
            git(path, 'init', '-q', '-b', 'main')
            git(path, 'config', 'user.email', 'dev@example.test')
            git(path, 'config', 'user.name', 'Dev')
            git(path, 'add', '-A')
            commit(path, 'init')
        return path

    def link(self, project, *args, mode='symlink'):
        with link_mode(mode):
            process = self.cli('link', str(project), *args)
        self.assertNotIn('Traceback', process.stderr)
        return process

    def bridge(self):
        return next(entry for entry in self.config()['layers'] if entry['id'] == 'core')

    def core_layer(self, name='core-layer'):
        """A real core layer (with `layer.yaml`) holding the files of the old library."""
        return make_layer(self.work / name, 'core', 'core', dict(OLD_LIBRARY_FILES), readme=False, changelog=False)

    def link_through_old_library(self, project, mode='symlink'):
        """Link `project` the way 0.9.x did, so that its managed links/copies come from the old library path, and
        leave the machine config as 0.9.x wrote it (the bridge; the generator cannot link through it any more)."""
        make_layer(self.library, 'core', 'core', readme=False, changelog=False)           # the old library as a core
        self.old_config()
        self.migrate('--apply')
        self.assertEqual(self.link(project, mode=mode).returncode, 0)
        self.bridge_config()

    def assert_bridge_needs_a_core(self, *args):
        """`layers migrate` on a bridge config without a core `--layer-source`: BLOCKED `no-core`, nothing written."""
        before, tree = self.config_file.read_bytes(), self.machine_tree()
        report = self.migrate(*args, '--apply', code=2)
        self.assertEqual((report['status'], report['code']), ('BLOCKED', 'no-core'), report)
        self.assertIn('--layer-source core=PATH', report['reason'])
        self.assertEqual(self.config_file.read_bytes(), before)
        self.assertEqual(self.machine_tree(), tree, 'no config change, no backup')
        self.assertEqual(self.backups(), [])
        return report

    def core_args(self, name='core-layer'):
        return ['--layer-source', f'core={self.core_layer(name)}']

    def bridge_config(self, comment=True, **extra):
        """The machine config exactly as 0.9.x `layers migrate --adopt-legacy --apply` wrote it for `old_config()`:
        a read-only `synthetic` core entry on the old library, `legacy_library`, the default rule and the isolation
        list. The generator no longer creates it, but still has to read, refuse and migrate it."""
        version = generator_version()
        data = dict(KEPT_KEYS, discover_paths=[str(self.projects)])
        data.update(legacy_library=str(self.library),
                    layers=[{'id': 'core', 'kind': 'core', 'path': str(self.library), 'writable': False,
                             'synthetic': {'version': version, 'core_min': version}}],
                    layer_rules=[{'match': ['**'], 'use': ['core']}], isolation={'allowed_layers': ['core']})
        data.update(extra)
        text = ('# machine config of the tester\n' if comment else '') + yaml_text(data)
        self.config_file.write_text(text, encoding='utf-8')
        return text


# ====================================================================== K11: dry-run, apply, backup, idempotence


class DryRunAndApplyTest(MigrateCase):
    def test_the_default_is_a_dry_run_that_writes_nothing(self):
        original = self.old_config()
        before = self.machine_tree()
        for args in ((), ('--dry-run',)):
            with self.subTest(args=args):
                report = self.migrate(*self.core_args(), *args)
                self.assertEqual((report['status'], report['dry_run']), ('PASS', True), report)
                self.assertTrue(report['changes'], 'the plan lists the changes')
                self.assertIsNone(report['backup'])
                self.assertIn('--apply', report['next'])
                self.assertIn('legacy_library', report['preview'])                    # the would-be config
                self.assertEqual(self.machine_tree(), before, 'dry-run: config and its directory unchanged')
                self.assertEqual(self.config_file.read_text(encoding='utf-8'), original)
        process = self.cli('layers', 'migrate', *self.core_args())                      # text output says so too
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertIn('(dry-run)', process.stdout)
        self.assertIn('would change', process.stdout)
        self.assertEqual(self.machine_tree(), before)

    def test_apply_and_dry_run_exclude_each_other(self):
        self.old_config()
        before = self.machine_tree()
        process = self.cli('layers', 'migrate', *self.core_args(), '--apply', '--dry-run')
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertEqual(self.machine_tree(), before)

    def test_apply_writes_a_backup_with_the_original_and_converts_the_old_key(self):
        original = self.old_config()
        report = self.migrate(*self.core_args(), '--apply')
        self.assertEqual((report['status'], report['dry_run']), ('PASS', False), report)
        backup = Path(report['backup'])
        self.assertEqual(self.backups(), [backup])
        self.assertEqual(backup.read_text(encoding='utf-8'), original, 'the backup holds the original bytes')
        config = self.config()
        self.assertNotIn('library', config)
        self.assertEqual(config['legacy_library'], str(self.library))
        for key, value in dict(KEPT_KEYS, discover_paths=[str(self.projects)]).items():
            with self.subTest(key=key):
                self.assertEqual(config[key], value, f'{key} is kept unchanged')
        text = self.config_file.read_text(encoding='utf-8')
        self.assertIn('# machine config of the tester', text)                          # comments kept
        self.assertEqual([item['id'] for item in config['layers']], ['core'])
        self.assertNotIn('synthetic', self.bridge(), 'a real core layer, never the 0.9.x bridge')
        self.assertEqual(config['layer_rules'], [{'match': ['**'], 'use': ['core']}])
        self.assertEqual(config['isolation'], {'allowed_layers': ['core']})

    def test_a_second_apply_changes_nothing_and_makes_no_backup(self):
        self.old_config()
        self.migrate(*self.core_args(), '--apply')
        text, backups = self.config_file.read_bytes(), self.backups()
        for args in ((*self.core_args(), '--apply'), ('--apply',), ()):
            with self.subTest(args=args):
                report = self.migrate(*args)
                self.assertEqual((report['status'], report['changed'], report['changes']), ('PASS', False, []), report)
                self.assertEqual(self.config_file.read_bytes(), text)
                self.assertEqual(self.backups(), backups)

    def test_the_migrated_config_is_read_by_the_generator(self):
        self.old_config()
        project = self.new_project('app')
        before = tree_snapshot(project)
        self.assertEqual(self.link(project).returncode, 2, 'before the migration: BLOCKED (D41)')
        self.assertEqual(tree_snapshot(project), before)
        self.migrate(*self.core_args(), '--apply')
        code, report = self.cli_json('layers', 'list', '--project', str(project))
        self.assertEqual((code, [item['id'] for item in report['layers']]), (0, ['core']), report)

    def test_a_missing_config_is_blocked_without_writing(self):
        code_report = self.migrate('--apply', code=2)
        self.assertEqual((code_report['status'], code_report['code']), ('BLOCKED', 'missing-config'))
        self.assertIn('--layer-source core=PATH', code_report['reason'])
        self.assertNotIn('--adopt-legacy', code_report['reason'])
        self.assertFalse(self.config_file.exists())

    def test_a_missing_old_library_without_a_core_source_is_blocked_without_writing(self):
        self.old_config()
        shutil.rmtree(self.library)
        before = self.machine_tree()
        report = self.migrate('--apply', code=2)
        self.assertEqual((report['status'], report['code']), ('BLOCKED', 'no-core'), report)
        self.assertIn('--layer-source core=PATH', report['reason'])
        self.assertEqual(self.machine_tree(), before)

    def test_an_old_library_without_layer_yaml_is_blocked_and_points_at_layer_source(self):
        self.old_config()
        before = self.machine_tree()
        report = self.migrate('--apply', code=2)
        self.assertEqual((report['status'], report['code']), ('BLOCKED', 'no-core'))
        self.assertIn('--layer-source core=PATH', report['reason'])
        self.assertNotIn('--adopt-legacy', report['reason'], 'the removed bridge is not offered')
        self.assertEqual(self.machine_tree(), before)

    def test_adopt_legacy_is_blocked_with_the_instruction_and_writes_nothing(self):
        """The 0.9.x bridge flag was removed in 1.0: every form answers BLOCKED `bad-argument` and changes nothing."""
        original = self.old_config()
        before = self.machine_tree()
        forms = {'bare': ('--adopt-legacy',), 'with a path': ('--adopt-legacy', str(self.library)),
                 'with a core source': ('--adopt-legacy', *self.core_args()), 'with --apply': ('--adopt-legacy', '--apply'),
                 'with --manifests': ('--manifests', '--adopt-legacy', '--apply')}
        for label, args in forms.items():
            with self.subTest(label):
                report = self.migrate(*args, code=2)
                self.assertEqual((report['status'], report['code']), ('BLOCKED', 'bad-argument'), report)
                self.assertIn('--adopt-legacy', report['reason'])
                self.assertIn('removed in 1.0', report['reason'])
                self.assertIn('--layer-source core=PATH', report['reason'])
                self.assertEqual(self.machine_tree(), before, 'no config change, no backup')
                self.assertEqual(self.config_file.read_text(encoding='utf-8'), original)
        process = self.cli('layers', 'migrate', '--adopt-legacy', '--apply')
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertIn('layer-source core=PATH', process.stdout + process.stderr)
        self.assertEqual(self.machine_tree(), before)

    def test_adopt_legacy_without_any_config_is_blocked_and_creates_no_config(self):
        report = self.migrate('--adopt-legacy', str(self.library), '--apply', code=2)
        self.assertEqual((report['status'], report['code']), ('BLOCKED', 'bad-argument'), report)
        self.assertFalse(self.config_file.exists())

    def test_adopt_legacy_is_hidden_from_help(self):
        process = self.cli('layers', 'migrate', '--help')
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertNotIn('adopt-legacy', process.stdout + process.stderr)
        self.assertIn('--layer-source', process.stdout)

    def test_an_old_library_with_layer_yaml_becomes_a_real_core_layer(self):
        make_layer(self.library, 'core', 'core', readme=False, changelog=False)
        self.old_config()
        report = self.migrate('--apply')
        core = self.bridge()
        self.assertEqual((core['kind'], core['path']), ('core', str(self.library)), report)
        self.assertNotIn('synthetic', core)
        self.assertEqual(self.config()['legacy_library'], str(self.library))


# ====================================================================== K11: --layer-source, rules, isolation


class LayerSourceTest(MigrateCase):
    def test_local_layer_sources_are_registered_in_place_with_rules_and_stubs(self):
        self.old_config()
        org = make_layer(self.work / 'acme-skills', 'acme', 'org', {'skills/team/SKILL.md': SKILL_TEXT.format('team')})
        me = make_layer(self.work / 'my-skills', 'me', 'personal')
        glob = f'{self.projects}/acme/**'
        report = self.migrate(*self.core_args(), '--layer-source', f'acme={org}', '--layer-source', f'me={me}',
                              '--match', f'acme={glob}', '--apply')
        config = self.config()
        entries = {item['id']: item for item in config['layers']}
        self.assertEqual(sorted(entries), ['acme', 'core', 'me'], report)
        self.assertEqual((entries['acme']['kind'], entries['acme']['path']), ('org', str(org)))
        self.assertEqual((entries['me']['kind'], entries['me']['path']), ('personal', str(me)))
        self.assertEqual(config['layer_rules'], [{'match': [glob], 'use': ['core', 'acme', 'me']},
                                                 {'match': ['**'], 'use': ['core', 'me']}])
        self.assertEqual(config['isolation']['allowed_layers'], ['core', 'acme', 'me'])
        targets = config['leakguard']['targets']
        self.assertEqual(sorted(targets), ['acme', 'core', 'me'], 'stubs for every registered layer')
        self.assertEqual((targets['acme']['scope'], targets['me']['scope'], targets['core']['scope']),
                         ('org-private', 'private', 'public'))
        for path in (org, me):
            self.assertFalse((path / '.git').exists(), 'registered in place: nothing cloned or initialised')

    def test_a_directory_without_layer_yaml_needs_a_kind(self):
        self.old_config()
        plain = self.work / 'plain'
        plain.mkdir()
        before = self.machine_tree()
        report = self.migrate(*self.core_args(), '--layer-source', f'plain={plain}', '--apply', code=2)
        self.assertEqual((report['status'], report['code']), ('BLOCKED', 'bad-argument'))
        self.assertIn('--kind', report['reason'])
        self.assertEqual(self.machine_tree(), before)
        report = self.migrate(*self.core_args(), '--layer-source', f'plain={plain}', '--kind', 'plain=org', '--apply')
        self.assertEqual(next(item for item in self.config()['layers'] if item['id'] == 'plain')['kind'], 'org')
        self.assertTrue(any('layer.yaml' in item for item in report['warnings']), report['warnings'])

    def test_a_remote_source_is_blocked_with_the_layers_add_instruction(self):
        self.old_config()
        before = self.machine_tree()
        for url in ('https://example.test/acme/skills.git', 'ssh://git@example.test/acme/skills.git',
                    'git@example.test:acme/skills.git'):
            with self.subTest(url=url):
                report = self.migrate(*self.core_args(), '--layer-source', f'acme={url}', '--apply', code=2)
                self.assertEqual((report['status'], report['code']), ('BLOCKED', 'bad-argument'))
                self.assertIn('layers add acme', report['reason'])
                self.assertEqual(self.machine_tree(), before, 'nothing written, nothing cloned')
        self.assertFalse((machine_data_dir() / 'layers').exists())

    def test_bad_pairs_are_refused_without_writing(self):
        self.old_config()
        org = make_layer(self.work / 'acme', 'acme', 'org')
        before = self.machine_tree()
        cases = {
            'not ID=PATH': (('--layer-source', str(org)), 'bad-argument', 'expects ID=VALUE'),
            'id mismatch with layer.yaml': (('--layer-source', f'other={org}'), 'layer-yaml-invalid', 'declares id acme'),
            '--kind without a source': (('--kind', 'ghost=org'), 'bad-argument', 'needs --layer-source ghost=PATH'),
            '--kind outside the kinds': (('--layer-source', f'acme={org}', '--kind', 'acme=team'), 'bad-argument',
                                         'must be one of'),
            '--match for a non-org layer': (('--match', 'core=**'), 'bad-argument', 'org layers only'),
            'same source twice': (('--layer-source', f'acme={org}', '--layer-source', f'acme={org}'), 'bad-argument',
                                  'given twice'),
        }
        for label, (args, code, phrase) in cases.items():
            with self.subTest(label):
                report = self.migrate(*self.core_args(), *args, '--apply', code=2)
                self.assertEqual((report['status'], report['code']), ('BLOCKED', code), report)
                self.assertIn(phrase, report['reason'])
        self.assertEqual(self.machine_tree(), before)

    def test_existing_rules_get_no_personal_layer_and_the_isolation_list_is_not_widened(self):
        core = make_layer(self.work / 'core', 'core', 'core', readme=False, changelog=False)
        acme = make_layer(self.work / 'acme', 'acme', 'org')
        rules = [{'match': [f'{self.projects}/acme/**'], 'use': ['core', 'acme']}, {'match': ['**'], 'use': ['core']}]
        self.config_file.write_text(yaml_text({
            'layers': [{'id': 'core', 'kind': 'core', 'path': str(core)}, {'id': 'acme', 'kind': 'org', 'path': str(acme)}],
            'layer_rules': rules, 'isolation': {'allowed_layers': ['core', 'acme']}, 'tags': {'x': ['y']}}), encoding='utf-8')
        me = make_layer(self.work / 'me', 'me', 'personal')
        report = self.migrate('--layer-source', f'me={me}', '--apply')
        config = self.config()
        self.assertEqual(config['layer_rules'], rules, 'existing rules unchanged: me is not added')
        self.assertEqual(config['isolation'], {'allowed_layers': ['core', 'acme']}, 'allowed_layers not widened')
        self.assertIn('me', [item['id'] for item in config['layers']])
        joined = ' '.join(report['warnings'])
        self.assertIn('layers use me', joined)
        self.assertIn('allowed_layers lacks me', joined)
        self.assertEqual(config['tags'], {'x': ['y']})

    def test_a_layer_source_core_replaces_the_bridge(self):
        self.bridge_config()
        real = self.core_layer()
        report = self.migrate('--layer-source', f'core={real}', '--apply')
        core = self.bridge()
        self.assertEqual(core['path'], str(real), report)
        self.assertNotIn('synthetic', core)
        self.assertEqual(self.config()['legacy_library'], str(self.library), 'kept until --finish')
        again = self.migrate('--layer-source', f'core={real}', '--apply')
        self.assertFalse(again['changed'])


# ====================================================================== K11: --manifests


class ManifestsTest(MigrateCase):
    MANIFEST = ('# project manifest\nskills:\n  - feature\nsync:\n  library: /somewhere/old-library\n'
                '  push: true\nclaude: false\n')
    ONLY_LIBRARY = 'skills: [feature]\nsync:\n  library: /somewhere/old-library\nmcp: false\n'

    def setUp(self):
        super().setUp()
        self.old_config()
        self.migrate(*self.core_args(), '--apply')
        self.first = self.new_project('first', self.MANIFEST)
        self.second = self.new_project('second', self.ONLY_LIBRARY)
        self.clean = self.new_project('clean', 'skills: [feature]\n')

    def manifest(self, project):
        return project / '.github' / '.skillsync.yaml'

    def backup_root(self):
        return machine_data_dir() / 'backups' / 'manifests'

    def projects_tree(self):
        return {name: tree_snapshot(self.projects / name) for name in ('first', 'second', 'clean')}

    def test_dry_run_lists_the_manifests_and_writes_nothing(self):
        before = self.projects_tree()
        report = self.migrate('--manifests')
        self.assertEqual((report['status'], report['dry_run']), ('PASS', True))
        self.assertEqual(sorted(Path(item['manifest']) for item in report['manifests']),
                         sorted([self.manifest(self.first), self.manifest(self.second)]))
        self.assertTrue(all(item['backup'] is None for item in report['manifests']))
        self.assertEqual(self.projects_tree(), before)
        self.assertFalse(self.backup_root().exists(), 'dry-run creates no backup directory')

    def test_apply_removes_the_library_setting_and_keeps_the_rest(self):
        report = self.migrate('--manifests', '--apply')
        self.assertEqual(report['status'], 'PASS', report)
        first = yaml.safe_load(self.manifest(self.first).read_text(encoding='utf-8'))
        self.assertEqual(first, {'skills': ['feature'], 'sync': {'push': True}, 'claude': False})
        self.assertIn('# project manifest', self.manifest(self.first).read_text(encoding='utf-8'))
        second = yaml.safe_load(self.manifest(self.second).read_text(encoding='utf-8'))
        self.assertEqual(second, {'skills': ['feature'], 'mcp': False}, 'an empty sync block is removed')
        self.assertEqual(self.manifest(self.clean).read_text(encoding='utf-8'), 'skills: [feature]\n')
        stamps = sorted(self.backup_root().iterdir())
        again = self.migrate('--manifests', '--apply')
        self.assertEqual((again['changed'], again['manifests']), (False, []), 'idempotent')
        self.assertEqual(sorted(self.backup_root().iterdir()), stamps, 'a second --apply makes no backup directory')

    def test_backups_live_in_the_data_dir_and_never_in_the_project_repository(self):
        report = self.migrate('--manifests', '--apply')
        originals = {self.manifest(self.first): self.MANIFEST, self.manifest(self.second): self.ONLY_LIBRARY}
        root = self.backup_root()
        for item in report['manifests']:
            backup = Path(item['backup'])
            with self.subTest(manifest=item['manifest']):
                self.assertTrue(backup.is_relative_to(root), backup)
                self.assertEqual(backup.read_text(encoding='utf-8'), originals[Path(item['manifest'])])
                self.assertFalse(backup.is_relative_to(self.projects))
        self.assertEqual(len({Path(item['backup']).parent for item in report['manifests']}), 2,
                         'one backup directory per project')
        for project in (self.first, self.second, self.clean):
            with self.subTest(project=project.name):
                self.assertEqual(list(project.rglob('*.bak*')), [], 'no backup file in the project')
                status = git(project, 'status', '--porcelain', '--untracked-files=all').stdout
                self.assertNotIn('??', status, 'no untracked file in the project repository')

    def test_project_flag_limits_the_manifests(self):
        report = self.migrate('--manifests', '--project', str(self.second), '--apply')
        self.assertEqual([Path(item['manifest']) for item in report['manifests']], [self.manifest(self.second)])
        self.assertEqual(self.manifest(self.first).read_text(encoding='utf-8'), self.MANIFEST)

    def test_manifests_and_config_flags_are_separate_steps(self):
        before = self.projects_tree()
        for args in (('--manifests', *self.core_args()), ('--manifests', '--finish'), ('--project', str(self.first))):
            with self.subTest(args=args):
                report = self.migrate(*args, '--apply', code=2)
                self.assertEqual((report['status'], report['code']), ('BLOCKED', 'bad-argument'))
        self.assertEqual(self.projects_tree(), before)


# ====================================================================== K11: --finish


class FinishTest(MigrateCase):
    def setUp(self):
        super().setUp()
        self.bridge_config()

    def replace_bridge(self):
        real = self.core_layer()
        self.migrate('--layer-source', f'core={real}', '--apply')
        return real

    def test_with_the_bridge_finish_is_blocked(self):
        before = self.config_file.read_bytes()
        report = self.migrate('--finish', '--apply', code=2)
        self.assertEqual((report['status'], report['code']), ('BLOCKED', 'finish-blocked'), report)
        self.assertIn('--isolation', report['reason'])
        self.assertIn('isolation-legacy-bridge', {item['code'] for item in report['findings']})
        self.assertEqual(self.config_file.read_bytes(), before)

    def test_the_old_clone_must_be_deleted_first(self):
        self.replace_bridge()
        before = self.config_file.read_bytes()
        report = self.migrate('--finish', '--apply', code=2)
        self.assertIn('still exists', report['reason'])
        self.assertEqual(self.config_file.read_bytes(), before)

    def test_isolation_must_pass(self):
        self.replace_bridge()
        shutil.rmtree(self.library)
        foreign = make_layer(machine_data_dir() / 'layers' / 'foreign', 'foreign', 'org')
        before = self.config_file.read_bytes()
        report = self.migrate('--finish', '--apply', code=2)
        self.assertIn('isolation-foreign-library', {item['code'] for item in report['findings']}, report)
        self.assertEqual(self.config_file.read_bytes(), before)
        shutil.rmtree(foreign)
        config = self.config()
        del config['isolation']
        self.config_file.write_text(yaml_text(config), encoding='utf-8')
        report = self.migrate('--finish', '--apply', code=2)
        self.assertIn('allowed_layers is not configured', report['reason'])

    def test_after_the_clone_is_gone_and_isolation_passes_finish_removes_legacy_library(self):
        self.replace_bridge()
        shutil.rmtree(self.library)
        code, isolation = self.cli_json('layers', 'check', '--isolation')
        self.assertEqual((code, isolation['status']), (0, 'PASS'), isolation)
        before = self.config_file.read_bytes()
        dry = self.migrate('--finish')
        self.assertEqual((dry['status'], dry['dry_run']), ('PASS', True), dry)
        self.assertEqual(self.config_file.read_bytes(), before)
        report = self.migrate('--finish', '--apply')
        self.assertEqual(report['status'], 'PASS', report)
        self.assertNotIn('legacy_library', self.config())
        self.assertTrue(Path(report['backup']).is_file())
        again = self.migrate('--finish', '--apply')
        self.assertEqual((again['status'], again['changed']), ('PASS', False))


# ====================================================================== K11: link --dry-run after the migration


class LinkAfterMigrationTest(MigrateCase):
    # `.github/skills` is owned by the generator (an unwanted entry there is removed, as before layers), so the
    # user's own files are placed where `prune_managed` never removes hand-written entries.
    USER_FILES = {
        '.claude/agents/mine.md': '---\nname: mine\ndescription: my own role\n---\nmine\n',
        '.claude/skills/my-own/SKILL.md': SKILL_TEXT.format('my-own'),
        'notes/todo.md': 'my notes\n',
    }

    @each_mode
    def test_link_dry_run_plans_no_removal_of_user_files_and_old_links_are_repointed(self, mode):
        project = self.new_project(f'app-{mode}')
        self.link_through_old_library(project, mode)
        for rel, text in self.USER_FILES.items():
            write(project / rel, text)
        installed = project / '.claude' / 'skills' / 'feature'
        if mode == 'symlink':
            self.assertEqual(Path(os.path.realpath(installed)), (self.library / 'skills' / 'feature').resolve())
        real = self.core_layer(f'core-layer-{mode}')
        self.migrate('--layer-source', f'core={real}', '--apply')                       # the bridge -> real core
        dry = self.link(project, '--dry-run', mode=mode)
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        output = dry.stdout + dry.stderr
        for rel in self.USER_FILES:
            with self.subTest(user_file=rel):
                self.assertNotIn(str(project / rel), output.replace('Would replace', ''), 'user file not touched')
        self.assertNotIn('Would remove', output, 'no removal planned at all (any `Would remove...` line)')
        user_before = {rel: (project / rel).read_text(encoding='utf-8') for rel in self.USER_FILES}
        self.assertEqual(self.link(project, mode=mode).returncode, 0)
        self.assertEqual({rel: (project / rel).read_text(encoding='utf-8') for rel in self.USER_FILES}, user_before)
        if mode == 'symlink':
            self.assertEqual(Path(os.path.realpath(installed)), (real / 'skills' / 'feature').resolve(),
                             'the link to the old library is repointed to the layer')
        self.assertEqual((installed / 'SKILL.md').read_text(encoding='utf-8'), SKILL_TEXT.format('feature'))

    def test_old_links_to_the_legacy_library_are_managed_and_pruned_after_the_migration(self):
        """K11 + `prune_managed`: after the bridge is replaced, a symlink into `legacy_library` for a skill that is
        no longer wanted is recognised as generator-managed (removed), while a user's own entry stays."""
        write(self.library / 'skills' / 'extra' / 'SKILL.md', SKILL_TEXT.format('extra'))
        manifest = {'skills': ['feature', 'extra'], 'instructions': {'merge': ['_base'], 'template': 'proj'},
                    'claude': True, 'codex': False, 'mcp': False}
        project = self.new_project('old-links', yaml_text(manifest))
        self.link_through_old_library(project)
        stale = project / '.claude' / 'skills' / 'extra'
        self.assertEqual(Path(os.path.realpath(stale)), (self.library / 'skills' / 'extra').resolve())
        write(project / '.claude' / 'skills' / 'my-own' / 'SKILL.md', SKILL_TEXT.format('my-own'))
        real = self.core_layer()
        self.migrate('--layer-source', f'core={real}', '--apply')
        manifest['skills'] = ['feature']
        write(project / '.github' / '.skillsync.yaml', yaml_text(manifest))
        dry = self.link(project, '--dry-run')
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertIn('Would remove obsolete: extra', dry.stdout)
        self.assertNotIn('my-own', dry.stdout)
        self.assertTrue(stale.is_symlink(), 'dry-run removes nothing')
        self.assertEqual(self.link(project).returncode, 0)
        self.assertFalse(stale.exists() or stale.is_symlink(), 'the old managed link is removed')
        self.assertTrue((project / '.claude' / 'skills' / 'my-own' / 'SKILL.md').is_file())
        self.assertTrue((self.library / 'skills' / 'extra' / 'SKILL.md').is_file(), 'the old library is untouched')


# ====================================================================== K11: partial, broken and symlinked configs (review 2.4-1)


class PartialConfigTest(MigrateCase):
    """Major 2 of review 2.4-1: configs that are partly migrated or broken."""

    def core_config(self, **extra):
        core = make_layer(self.work / 'core', 'core', 'core', readme=False, changelog=False)
        data = {'layers': [{'id': 'core', 'kind': 'core', 'path': str(core)}],
                'layer_rules': [{'match': ['**'], 'use': ['core']}], 'isolation': {'allowed_layers': ['core']},
                'tags': {'t': ['x']}}
        data.update(extra)
        self.config_file.write_text(yaml_text(data), encoding='utf-8')
        return core

    def test_a_library_key_equal_to_legacy_library_is_removed_and_the_run_is_idempotent(self):
        self.core_config(library=str(self.library), legacy_library=str(self.library))
        report = self.migrate('--apply')
        self.assertEqual(report['status'], 'PASS', report)
        config = self.config()
        self.assertNotIn('library', config)
        self.assertEqual(config['legacy_library'], str(self.library))
        self.assertEqual(([item['id'] for item in config['layers']], config['tags']), (['core'], {'t': ['x']}))
        self.assertEqual(len(self.backups()), 1)
        text, before = self.config_file.read_bytes(), self.machine_tree()
        again = self.migrate('--apply')
        self.assertEqual((again['status'], again['changed']), ('PASS', False), again)
        self.assertEqual((self.config_file.read_bytes(), self.machine_tree()), (text, before))

    def test_a_library_key_different_from_legacy_library_is_a_conflict(self):
        other = self.work / 'another-library'
        other.mkdir()
        self.core_config(library=str(self.library), legacy_library=str(other))
        text, before = self.config_file.read_bytes(), self.machine_tree()
        for args in ((), ('--apply',)):
            with self.subTest(args=args):
                report = self.migrate(*args, code=2)
                self.assertEqual((report['status'], report['code']), ('BLOCKED', 'conflict'), report)
                self.assertIn('legacy_library', report['reason'])
                self.assertEqual((self.config_file.read_bytes(), self.machine_tree()), (text, before))

    def test_a_library_key_next_to_layers_with_a_core_becomes_legacy_library_only(self):
        core = self.core_config(library=str(self.library))
        layers_before = self.config()['layers']
        report = self.migrate('--apply')
        self.assertEqual(report['status'], 'PASS', report)
        config = self.config()
        self.assertNotIn('library', config)
        self.assertEqual(config['legacy_library'], str(self.library))
        self.assertEqual(config['layers'], layers_before, 'no bridge added next to a real core')
        self.assertEqual(config['layer_rules'], [{'match': ['**'], 'use': ['core']}])
        report = self.migrate('--layer-source', f'core={core}', '--apply')                 # the same core again
        self.assertEqual((report['status'], report['changed']), ('PASS', False), report)
        self.assertEqual(Path(self.config()['layers'][0]['path']), core)

    def test_invalid_yaml_or_a_non_mapping_config_is_blocked_without_writing(self):
        cases = {'invalid YAML': ('library: [unclosed\n', 'not valid YAML'),
                 'a list': ('- library\n- other\n', 'must be a mapping'),
                 'a scalar': ('just text\n', 'must be a mapping')}
        for label, (text, phrase) in cases.items():
            for args in (('--layer-source', f'core={self.library}', '--apply'), ('--apply',), ('--manifests', '--apply')):
                with self.subTest(label, args=args):
                    self.config_file.write_text(text, encoding='utf-8')
                    before = self.machine_tree()
                    report = self.migrate(*args, code=2)
                    self.assertEqual((report['status'], report['code']), ('BLOCKED', 'layer-error'), report)
                    self.assertIn(phrase, report['reason'])
                    self.assertEqual(self.config_file.read_text(encoding='utf-8'), text)
                    self.assertEqual(self.machine_tree(), before)

    def test_an_isolation_block_without_allowed_layers_gets_a_warning(self):
        self.old_config(isolation={})
        report = self.migrate(*self.core_args(), '--apply')
        self.assertEqual(report['status'], 'PASS', report)
        self.assertEqual(self.config()['isolation'], {}, 'the list is not added (no widening)')
        self.assertEqual(len([item for item in report['warnings'] if 'has no `allowed_layers`' in item]), 1,
                         report['warnings'])
        self.assertFalse([item for item in report['warnings'] if 'lacks' in item], report['warnings'])


class WriteSafetyTest(MigrateCase):
    def test_an_interrupted_write_leaves_the_config_unchanged_and_no_temporary_file(self):
        from unittest import mock
        original = self.old_config()
        names_before = sorted(path.name for path in self.config_file.parent.iterdir())
        with mock.patch.object(layers.os, 'replace', side_effect=OSError('disk full (simulated)')):
            report = layers.layers_migrate(sources=[f'core={self.core_layer()}'], apply=True)
        self.assertEqual(report['status'], 'BLOCKED', report)
        self.assertEqual(self.config_file.read_text(encoding='utf-8'), original)
        leftovers = [path.name for path in self.config_file.parent.iterdir()
                     if path.name not in names_before and '.bak-' not in path.name]
        self.assertEqual(leftovers, [], 'no temporary file is left behind')
        for backup in self.backups():
            self.assertEqual(backup.read_text(encoding='utf-8'), original, 'a backup made before the write is whole')
        self.assertEqual(self.migrate(*self.core_args(), '--apply')['status'], 'PASS', 'a retry works')

    def test_the_backup_is_made_before_the_write(self):
        from unittest import mock
        original = self.old_config()
        seen = []
        real_replace = os.replace

        def replace(src, dst):
            seen.append([path.read_text(encoding='utf-8') for path in self.backups()])
            return real_replace(src, dst)
        with mock.patch.object(layers.os, 'replace', side_effect=replace):
            report = layers.layers_migrate(sources=[f'core={self.core_layer()}'], apply=True)
        self.assertEqual(report['status'], 'PASS', report)
        self.assertEqual(seen, [[original]], 'when the new config replaces the old one, the backup already holds it')

    def test_an_unchanged_result_is_not_written_and_makes_no_backup(self):
        """`edit_config`: a result equal to the current text writes nothing (idempotence of every config edit)."""
        self.old_config()
        self.migrate(*self.core_args(), '--apply')
        text, backups, mtime = self.config_file.read_bytes(), self.backups(), self.config_file.stat().st_mtime_ns
        for apply in (True, False):
            with self.subTest(apply=apply):
                result = layers.edit_config(self.config_file, [], apply=apply)
                self.assertEqual((result['changed'], result['backup']), (False, None), result)
                self.assertEqual(self.config_file.read_bytes(), text)
                self.assertEqual(self.config_file.stat().st_mtime_ns, mtime)
                self.assertEqual(self.backups(), backups)

    @needs_os_symlinks
    def test_a_symlinked_config_is_written_through_to_its_target(self):
        dotfiles = self.work / 'dotfiles'
        dotfiles.mkdir()
        target = dotfiles / 'skillsync.yaml'
        original = self.old_config()
        self.config_file.rename(target)
        self.config_file.symlink_to(target)
        report = self.migrate(*self.core_args(), '--apply')
        self.assertEqual(report['status'], 'PASS', report)
        self.assertTrue(self.config_file.is_symlink(), 'the symlink stays')
        self.assertEqual(Path(os.path.realpath(self.config_file)), target.resolve())
        self.assertIn('legacy_library', target.read_text(encoding='utf-8'))
        self.assertTrue(report['backup'] and Path(report['backup']).read_text(encoding='utf-8') == original, report)
        self.assertEqual(Path(report['backup']).parent, self.config_file.parent, 'the backup lies next to the link')
        self.assertEqual(sorted(path.name for path in dotfiles.iterdir()), ['skillsync.yaml'],
                         'nothing added to the dotfiles repository')

    @needs_os_symlinks
    def test_a_relative_symlinked_config_is_written_through_to_its_target(self):
        target = self.work / 'dotfiles-rel' / 'skillsync.yaml'
        target.parent.mkdir()
        original = self.old_config()
        self.config_file.rename(target)
        relative = os.path.relpath(target, self.config_file.parent)
        self.config_file.symlink_to(relative)
        report = self.migrate(*self.core_args(), '--apply')
        self.assertEqual(report['status'], 'PASS', report)
        self.assertEqual(os.readlink(self.config_file), relative, 'the relative link stays as it was')
        self.assertIn('legacy_library', target.read_text(encoding='utf-8'))
        self.assertEqual(Path(report['backup']).read_text(encoding='utf-8'), original)
        self.assertEqual(sorted(path.name for path in target.parent.iterdir()), ['skillsync.yaml'])

    def test_an_io_error_in_manifests_keeps_the_backups_written_so_far(self):
        from unittest import mock
        from skillsync_workflow.common import exit_code
        self.old_config()
        self.migrate(*self.core_args(), '--apply')
        texts = {name: f'skills: [feature]\nsync:\n  library: /old/{name}\n  push: true\n' for name in ('m1', 'm2')}
        projects = {name: self.new_project(name, text, with_git=False) for name, text in texts.items()}
        manifests = {name: path / '.github' / '.skillsync.yaml' for name, path in projects.items()}
        real_write = layers._write_atomic
        calls = []

        def failing(path, text):
            calls.append(Path(path))
            if len(calls) == 2:
                raise OSError('disk full (simulated)')
            return real_write(path, text)
        with mock.patch.object(layers, '_write_atomic', side_effect=failing):
            report = layers.layers_migrate(manifests=True, projects=[str(projects['m1']), str(projects['m2'])], apply=True)
        self.assertEqual((report['status'], report['code'], exit_code(report)), ('BLOCKED', 'io-error', 2), report)
        items = {Path(item['manifest']): item for item in report['manifests']}
        self.assertEqual(sorted(items), sorted(manifests.values()), report)
        first, second = items[manifests['m1']], items[manifests['m2']]
        self.assertNotIn('error', first)
        self.assertIn('OSError', second['error'])
        for name, item in (('m1', first), ('m2', second)):
            self.assertEqual(Path(item['backup']).read_text(encoding='utf-8'), texts[name], name)
        self.assertEqual(manifests['m2'].read_text(encoding='utf-8'), texts['m2'], 'the failed manifest is unchanged')
        self.assertNotIn('library', yaml.safe_load(manifests['m1'].read_text(encoding='utf-8'))['sync'])

    @needs_os_symlinks
    def test_a_symlinked_manifest_is_written_through_to_its_target(self):
        self.old_config()
        self.migrate(*self.core_args(), '--apply')
        project = self.new_project('linked', with_git=False)
        shared = self.work / 'shared-manifests' / 'linked.yaml'
        original = 'skills: [feature]\nsync:\n  library: /somewhere/old\n  push: true\n'
        write(shared, original)
        manifest = project / '.github' / '.skillsync.yaml'
        manifest.unlink()
        manifest.symlink_to(shared)
        report = self.migrate('--manifests', '--project', str(project), '--apply')
        self.assertEqual(report['status'], 'PASS', report)
        self.assertTrue(manifest.is_symlink(), 'the symlink stays')
        self.assertEqual(yaml.safe_load(shared.read_text(encoding='utf-8')), {'skills': ['feature'], 'sync': {'push': True}})
        self.assertEqual(Path(report['manifests'][0]['backup']).read_text(encoding='utf-8'), original)


# ====================================================================== K15: a 0.9.x bridge config is read, refused, migrated


class LegacyBridgeConfigTest(MigrateCase):
    """The generator no longer creates the bridge (a `synthetic` core entry on the old library), but a machine
    config written by 0.9.x may still hold it: it is parsed, every command that needs layers is BLOCKED
    `legacy-config` with the migration instruction, and `layers migrate --layer-source core=PATH` replaces it."""

    INSTRUCTION = 'layers migrate --layer-source core=PATH --apply'

    def setUp(self):
        super().setUp()
        self.bridge_config()

    def test_the_bridge_config_is_the_one_0_9_x_wrote(self):
        entry = self.bridge()
        version = generator_version()
        self.assertEqual((entry['kind'], entry['writable'], entry['path']), ('core', False, str(self.library)))
        self.assertEqual(entry['synthetic'], {'version': version, 'core_min': version})
        self.assertEqual(self.config()['legacy_library'], str(self.library))
        parsed = layers.load_machine_config(self.config_file)
        self.assertEqual(parsed.layers['core'].synthetic, {'version': version, 'core_min': version},
                         'a config with the bridge is still parsed')

    def test_the_bridge_with_a_personal_layer_gives_the_rule_core_personal(self):
        me = make_layer(self.work / 'me', 'me', 'personal')
        self.migrate(*self.core_args(), '--layer-source', f'me={me}', '--apply')
        self.assertEqual(self.config()['layer_rules'], [{'match': ['**'], 'use': ['core', 'me']}])
        self.assertNotIn('synthetic', self.bridge())

    def test_without_a_core_source_nothing_is_migrated_whatever_else_is_given(self):
        """There is no "bridge kept" state: org/personal sources alone are BLOCKED `no-core`, nothing is written."""
        org = make_layer(self.work / 'acme', 'acme', 'org')
        me = make_layer(self.work / 'me', 'me', 'personal')
        cases = {'no flags': (), 'org only': ('--layer-source', f'acme={org}'),
                 'personal only': ('--layer-source', f'me={me}'),
                 'org and personal': ('--layer-source', f'acme={org}', '--layer-source', f'me={me}',
                                      '--match', f'acme={self.projects}/acme/**')}
        for label, args in cases.items():
            with self.subTest(label):
                self.assert_bridge_needs_a_core(*args)

    def test_two_core_sources_replacing_the_bridge_are_a_conflict_and_nothing_is_written(self):
        second = make_layer(self.work / 'core2', 'core2', 'core', dict(OLD_LIBRARY_FILES), readme=False, changelog=False)
        before, tree = self.config_file.read_bytes(), self.machine_tree()
        report = self.migrate(*self.core_args(), '--layer-source', f'core2={second}', '--apply', code=2)
        self.assertEqual((report['status'], report['code']), ('BLOCKED', 'conflict'), report)
        self.assertIn('Two core layers', report['reason'])
        self.assertEqual((self.config_file.read_bytes(), self.machine_tree()), (before, tree))

    def test_apply_replacing_the_bridge_keeps_a_backup_equal_to_the_original_config(self):
        original = self.config_file.read_bytes()
        report = self.migrate(*self.core_args(), '--apply')
        self.assertEqual(report['status'], 'PASS', report)
        backups = self.backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(Path(report['backup']), backups[0])
        self.assertEqual(backups[0].read_bytes(), original, 'the backup holds the original bytes')
        self.assertNotEqual(self.config_file.read_bytes(), original)

    def test_other_commands_on_a_bridge_config_report_the_problem_and_write_nothing(self):
        project = self.new_project('app')
        before, project_before = self.machine_tree(), tree_snapshot(project)
        for args in (('instructions', str(project)), ('skills', 'list'), ('doctor', str(project)), ('list',)):
            with self.subTest(args=args):
                process = self.cli(*args, cwd=project)
                output = process.stdout + process.stderr
                self.assertNotIn('Traceback', process.stderr)
                self.assertEqual(process.returncode, 2, output)
                self.assertTrue('legacy bridge' in output or 'legacy-config' in output, output)
                self.assertIn('layers migrate --layer-source core=PATH --apply', output)
                self.assertEqual((self.machine_tree(), tree_snapshot(project)), (before, project_before))

    def test_layers_add_core_on_a_bridge_config_points_to_the_migration(self):
        self.bridge_config()
        before = self.config_file.read_bytes()
        process = self.cli('layers', 'add', 'core2', '--kind', 'core', '--source', str(self.core_layer('core-src')), '--json')
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual((report['status'], report['code']), ('BLOCKED', 'conflict'), report)
        self.assertIn('legacy bridge', report['reason'])
        self.assertIn('layers migrate --layer-source core=PATH --apply', report['reason'])
        self.assertEqual(self.config_file.read_bytes(), before)

    def test_layers_commands_are_blocked_with_the_migration_instruction_and_write_nothing(self):
        project = self.new_project('app')
        before, project_before = self.machine_tree(), tree_snapshot(project)
        for args in (('layers', 'list', '--project', str(project)), ('layers', 'check', '--project', str(project)),
                     ('layers', 'update')):
            with self.subTest(args=args):
                code, report = self.cli_json(*args)
                self.assertEqual((code, report['status'], report['code']), (2, 'BLOCKED', 'legacy-config'), report)
                self.assertIn(self.INSTRUCTION, report['reason'])
                self.assertNotIn('--adopt-legacy', report['reason'])
        self.assertEqual((self.machine_tree(), tree_snapshot(project)), (before, project_before))

    @each_mode
    def test_link_is_blocked_with_the_migration_instruction_and_changes_nothing(self, mode):
        project = self.new_project(f'blocked-{mode}')
        before = tree_snapshot(project)
        for extra in ((), ('--dry-run',)):
            with self.subTest(args=extra):
                process = self.link(project, *extra, mode=mode)
                self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
                text = process.stdout + process.stderr
                self.assertIn('the legacy bridge', text)
                self.assertIn(self.INSTRUCTION, text)
                self.assertNotIn('legacy-bridge', text, 'the bridge warning is gone')
                self.assertEqual(tree_snapshot(project), before)

    def test_isolation_check_still_reports_the_bridge_and_fails_on_purpose(self):
        code, isolation = self.cli_json('layers', 'check', '--isolation')
        self.assertEqual((code, isolation['status']), (1, 'FAIL'), isolation)
        # exactly one finding: the bridge path is also `legacy_library`, which is not reported a second time
        self.assertEqual([item['code'] for item in isolation['findings']], ['isolation-legacy-bridge'], isolation)
        self.assertEqual(isolation['findings'][0]['layer'], 'core')

    def test_the_synthetic_layer_flag_is_refused_whatever_the_config_holds(self):
        stray = self.work / 'elsewhere'
        stray.mkdir()
        before = self.machine_tree()
        for args in (('layers', 'check', '--layer', f'core={self.library},synthetic'),
                     ('layers', 'list', '--layer', f'core={self.library},synthetic')):
            with self.subTest(args=args):
                code, report = self.cli_json(*args, cwd=stray)
                self.assertEqual((code, report['status'], report['code']), (2, 'BLOCKED', 'synthetic-refused'), report)
        self.assertEqual(self.machine_tree(), before)
        code, report = self.cli_json('layers', 'check', '--layer', f'core={self.library}', cwd=stray)
        self.assertNotIn('legacy-bridge', [item['code'] for item in report['findings']], report)

    @each_mode
    def test_push_to_the_bridge_is_blocked_and_writes_nothing(self, mode):
        project = self.new_project(f'push-{mode}')
        before_project, before = tree_snapshot(project), tree_snapshot(self.library)
        for extra in ((), ('--to-layer', 'core')):
            with self.subTest(args=extra), link_mode(mode):
                process = self.cli('push', str(project), *extra)
                self.assertNotIn('Traceback', process.stderr)
                self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
                self.assertIn('the legacy bridge', process.stdout + process.stderr)
                self.assertIn(self.INSTRUCTION, process.stdout + process.stderr)
                self.assertEqual(tree_snapshot(self.library), before, 'the old library tree is unchanged')
                self.assertEqual(tree_snapshot(project), before_project)

    def test_import_and_skills_add_into_the_bridge_are_blocked(self):
        before = tree_snapshot(self.library)
        source = self.work / 'to-import'
        write(source / '.github' / 'skills' / 'imported' / 'SKILL.md', SKILL_TEXT.format('imported'))
        for args in (('import', str(source), '--to-layer', 'core'), ('skills', 'add', 'brandnew', '--to-layer', 'core')):
            with self.subTest(args=args):
                process = self.cli(*args)
                self.assertNotIn('Traceback', process.stderr)
                self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
                self.assertIn(self.INSTRUCTION, process.stdout + process.stderr)
                self.assertEqual(tree_snapshot(self.library), before)

    def test_in_process_the_bridge_and_adopt_legacy_are_refused(self):
        project = self.new_project('off')
        with self.assertRaises(layers.LayerError) as caught:
            layers.resolve_layers(project)
        self.assertEqual(caught.exception.code, 'legacy-config')
        self.assertIn('layers migrate', str(caught.exception))
        before = self.config_file.read_bytes()
        report = layers.layers_migrate(adopt_legacy=True, apply=True)
        self.assertEqual((report['status'], report['code']), ('BLOCKED', 'bad-argument'))
        self.assertEqual(self.config_file.read_bytes(), before)
        check = layers.layers_check(project)
        self.assertEqual(check['status'], 'BLOCKED')

    def test_the_migration_replaces_the_bridge_and_the_config_works_again(self):
        project = self.new_project('app')
        real = self.core_layer()
        report = self.migrate('--layer-source', f'core={real}', '--apply')
        self.assertTrue(any('the bridge core replaced by the core layer core' in item for item in report['changes']), report)
        self.assertNotIn('synthetic', self.bridge())
        self.assertEqual((self.bridge()['path'], self.config()['legacy_library']), (str(real), str(self.library)))
        code, listing = self.cli_json('layers', 'list', '--project', str(project))
        self.assertEqual((code, [item['id'] for item in listing['layers']]), (0, ['core']), listing)
        self.assertEqual(self.link(project).returncode, 0)


# ====================================================================== D166: the bridge config + layers in one call


class BridgeWithLayersTest(MigrateCase):
    """D166 (scenarios R1-R3 of the release runbook): a config with the legacy bridge and org/personal layers added
    in the same `layers migrate` call ends like one migrated from the old `library:` key. Boundary of decision
    2.4: a config already on layers (no bridge, no `library:`) is not widened."""

    def layers_on_disk(self):
        core = make_layer(self.work / 'core-layer', 'core', 'core', dict(OLD_LIBRARY_FILES), readme=False,
                          changelog=False)
        org = make_layer(self.work / 'acme', 'acme', 'org', {'skills/team/SKILL.md': SKILL_TEXT.format('team')})
        me = make_layer(self.work / 'me', 'me', 'personal', {'skills/mine/SKILL.md': SKILL_TEXT.format('mine')})
        return core, org, me

    def flags(self, core, org, me, with_core=True):
        flags = ['--layer-source', f'acme={org}', '--layer-source', f'me={me}',
                 '--match', f'acme={self.projects}/acme/**']
        return (['--layer-source', f'core={core}'] if with_core else []) + flags

    def expected_rules(self):
        return [{'match': [f'{self.projects}/acme/**'], 'use': ['core', 'acme', 'me']},
                {'match': ['**'], 'use': ['core', 'me']}]

    def test_r1_the_old_library_key_gives_complete_rules_and_isolation(self):
        self.old_config()
        core, org, me = self.layers_on_disk()
        self.migrate(*self.flags(core, org, me), '--apply')
        config = self.config()
        self.assertEqual(config['layer_rules'], self.expected_rules())
        self.assertEqual(config['isolation'], {'allowed_layers': ['core', 'acme', 'me']})

    def test_r2_the_bridge_with_layers_in_one_call_equals_the_library_key_result(self):
        self.old_config()
        core, org, me = self.layers_on_disk()
        self.migrate(*self.flags(core, org, me), '--apply')
        from_library = self.config()
        self.config_file.unlink()
        self.bridge_config()
        self.assertEqual(self.config()['layer_rules'], [{'match': ['**'], 'use': ['core']}], 'the bridge before')
        report = self.migrate(*self.flags(core, org, me), '--apply')
        config = self.config()
        self.assertEqual(config['layer_rules'], from_library['layer_rules'], 'same rules as from `library:`')
        self.assertEqual(config['layer_rules'], self.expected_rules())
        self.assertEqual(sum(1 for rule in config['layer_rules'] if rule['match'] == ['**']), 1,
                         'the default rule is replaced, not duplicated')
        self.assertEqual(config['isolation']['allowed_layers'], ['core', 'acme', 'me'])
        self.assertEqual(sorted(item['id'] for item in config['layers']), ['acme', 'core', 'me'])
        self.assertNotIn('synthetic', self.bridge())
        joined = ' '.join(report['warnings'])
        self.assertNotIn('No layer_rules entry uses', joined)
        self.assertNotIn('allowed_layers lacks', joined)
        again = self.migrate(*self.flags(core, org, me), '--apply')
        self.assertFalse(again['changed'], 'idempotent')

    def test_r3_the_migrated_bridge_config_links_without_missing_skills_and_isolation_passes(self):
        self.bridge_config()
        core, org, me = self.layers_on_disk()
        self.migrate(*self.flags(core, org, me), '--apply')
        manifest = lambda skills: yaml_text({'skills': skills, 'instructions': {'merge': ['_base'], 'template': 'proj'},  # noqa: E731
                                             'claude': True, 'codex': False, 'mcp': False})
        in_org = self.new_project('acme/app', manifest(['feature', 'team', 'mine']))
        other = self.new_project('other', manifest(['feature', 'mine']))
        for project, wanted in ((in_org, ['team', 'mine']), (other, ['mine'])):
            with self.subTest(project=project.name):
                dry = self.link(project, '--dry-run')
                output = dry.stdout + dry.stderr
                self.assertEqual(dry.returncode, 0, output)
                self.assertNotIn('Missing skill', output)
                for skill in wanted:
                    self.assertIn(f'skills/{skill}', output)
        code, listing = self.cli_json('layers', 'list', cwd=in_org)
        self.assertEqual([item['id'] for item in listing['layers']], ['core', 'acme', 'me'])
        code, listing = self.cli_json('layers', 'list', cwd=other)
        self.assertEqual([item['id'] for item in listing['layers']], ['core', 'me'])
        shutil.rmtree(self.library)
        code, isolation = self.cli_json('layers', 'check', '--isolation')
        self.assertEqual((code, isolation['status']), (0, 'PASS'), isolation)

    def test_the_layers_alone_do_not_keep_the_bridge_the_call_is_blocked(self):
        """Was "the bridge kept gets the layers added in the same call"; the same result needs the core source (R2)."""
        self.bridge_config()
        core, org, me = self.layers_on_disk()
        self.assert_bridge_needs_a_core(*self.flags(core, org, me, with_core=False))

    def test_an_org_layer_first_and_the_personal_layer_later_is_no_longer_a_bridge_flow(self):
        """Was a two-step flow with the bridge kept after the org call; now the org call alone is BLOCKED and the
        complete result (org rule with core, acme, me; default rule core, me) comes from one call with the core (R2)."""
        self.bridge_config()
        core, org, me = self.layers_on_disk()
        glob = f'{self.projects}/acme/**'
        self.assert_bridge_needs_a_core('--layer-source', f'acme={org}', '--match', f'acme={glob}')
        self.migrate('--layer-source', f'core={core}', '--layer-source', f'acme={org}', '--layer-source', f'me={me}',
                     '--match', f'acme={glob}', '--apply')
        self.assertEqual(self.config()['layer_rules'], [{'match': [glob], 'use': ['core', 'acme', 'me']},
                                                        {'match': ['**'], 'use': ['core', 'me']}])
        self.assertEqual(self.config()['isolation']['allowed_layers'], ['core', 'acme', 'me'])

    def test_the_isolation_list_follows_the_kind_order_whatever_the_order_of_the_flags(self):
        self.bridge_config()
        core, org, me = self.layers_on_disk()
        self.migrate('--layer-source', f'me={me}', '--layer-source', f'acme={org}', '--layer-source', f'core={core}',
                     '--match', f'acme={self.projects}/acme/**', '--apply')
        self.assertEqual(self.config()['isolation']['allowed_layers'], ['core', 'acme', 'me'])

    def test_the_bridge_with_an_org_layer_only_keeps_the_default_rule_and_widens_the_isolation_list(self):
        self.bridge_config()
        core, org, me = self.layers_on_disk()
        self.migrate('--layer-source', f'core={core}', '--layer-source', f'acme={org}',
                     '--match', f'acme={self.projects}/acme/**', '--apply')
        config = self.config()
        self.assertEqual(config['layer_rules'], [{'match': [f'{self.projects}/acme/**'], 'use': ['core', 'acme']},
                                                 {'match': ['**'], 'use': ['core']}])
        self.assertEqual(config['isolation']['allowed_layers'], ['core', 'acme'])

    def test_a_renamed_core_replaces_the_bridge_id_in_rules_and_isolation(self):
        self.bridge_config()
        core2 = make_layer(self.work / 'core2', 'core2', 'core', dict(OLD_LIBRARY_FILES), readme=False, changelog=False)
        _, org, me = self.layers_on_disk()
        report = self.migrate('--layer-source', f'core2={core2}', '--layer-source', f'acme={org}',
                              '--layer-source', f'me={me}', '--match', f'acme={self.projects}/acme/**', '--apply')
        self.assertNotIn('allowed_layers lacks', ' '.join(report['warnings']), 'the renamed core is in the list')
        config = self.config()
        self.assertEqual(config['layer_rules'], [{'match': [f'{self.projects}/acme/**'], 'use': ['core2', 'acme', 'me']},
                                                 {'match': ['**'], 'use': ['core2', 'me']}])
        self.assertEqual(config['isolation']['allowed_layers'], ['core2', 'acme', 'me'])

    def test_a_dry_run_on_the_bridge_config_writes_nothing_and_lists_the_changes(self):
        self.bridge_config()
        core, org, me = self.layers_on_disk()
        before = self.machine_tree()
        report = self.migrate(*self.flags(core, org, me))
        self.assertTrue(report['dry_run'])
        self.assertEqual(self.machine_tree(), before)
        self.assertIn("layer_rules: ['**'] -> ['core', 'me'] (personal layer added)", report['changes'])
        self.assertIn('isolation.allowed_layers: + acme, me', report['changes'])

    def test_repeating_the_same_match_with_a_new_personal_layer_does_not_duplicate_the_org_rule(self):
        self.bridge_config()
        core, org, me = self.layers_on_disk()
        glob = f'{self.projects}/acme/**'
        flags = ['--layer-source', f'core={core}', '--layer-source', f'acme={org}', '--layer-source', f'me={me}',
                 '--match', f'acme={glob}']
        self.migrate(*flags, '--apply')
        again = self.migrate(*flags, '--apply')
        self.assertFalse(again['changed'])
        rules = self.config()['layer_rules']
        self.assertEqual(rules, [{'match': [glob], 'use': ['core', 'acme', 'me']},
                                 {'match': ['**'], 'use': ['core', 'me']}], 'one org rule, one default rule')

    def test_without_a_default_rule_the_personal_layer_joins_every_rule_and_none_is_created(self):
        self.bridge_config()
        config = self.config()
        config['layer_rules'] = [{'match': [f'{self.projects}/x/**'], 'use': ['core']}]
        self.config_file.write_text(yaml_text(config), encoding='utf-8')
        core, org, me = self.layers_on_disk()
        self.migrate('--layer-source', f'core={core}', '--layer-source', f'me={me}', '--apply')
        self.assertEqual(self.config()['layer_rules'], [{'match': [f'{self.projects}/x/**'], 'use': ['core', 'me']}])

    def test_a_personal_layer_alone_on_the_bridge_config_is_blocked_and_with_the_core_joins_the_default_rule(self):
        """Was a two-step flow (personal first, org later) with the bridge kept; the combined result is R2."""
        self.bridge_config()
        core, org, me = self.layers_on_disk()
        self.assert_bridge_needs_a_core('--layer-source', f'me={me}')
        self.migrate('--layer-source', f'core={core}', '--layer-source', f'me={me}', '--apply')
        self.assertEqual(self.config()['layer_rules'], [{'match': ['**'], 'use': ['core', 'me']}])

    def test_the_replaced_bridge_is_idempotent_on_a_second_call(self):
        self.bridge_config()
        core, org, me = self.layers_on_disk()
        self.migrate(*self.flags(core, org, me), '--apply')
        before = self.config_file.read_bytes()
        again = self.migrate(*self.flags(core, org, me), '--apply')
        self.assertFalse(again['changed'])
        self.assertEqual(self.config_file.read_bytes(), before)

    def test_two_personal_layers_in_the_bridge_config_are_a_conflict_and_nothing_is_written(self):
        self.bridge_config()
        core, org, me = self.layers_on_disk()
        other = make_layer(self.work / 'me-other', 'me2', 'personal')
        before = self.config_file.read_bytes()
        report = self.migrate(*self.core_args(), '--layer-source', f'me={me}', '--layer-source', f'me2={other}',
                              '--apply', code=2)
        self.assertEqual((report['status'], report['code']), ('BLOCKED', 'conflict'), report)
        self.assertEqual(self.config_file.read_bytes(), before)

    def test_a_config_already_on_layers_is_not_widened_boundary_of_decision_2_4(self):
        core, org, _ = self.layers_on_disk()
        rules = [{'match': ['**'], 'use': ['core']}]
        self.config_file.write_text(yaml_text({
            'layers': [{'id': 'core', 'kind': 'core', 'path': str(core)}], 'layer_rules': rules,
            'isolation': {'allowed_layers': ['core']}}), encoding='utf-8')
        me = make_layer(self.work / 'me2', 'me', 'personal')
        report = self.migrate('--layer-source', f'acme={org}', '--layer-source', f'me={me}',
                              '--match', f'acme={self.projects}/acme/**', '--apply')
        config = self.config()
        self.assertEqual(config['layer_rules'], [{'match': [f'{self.projects}/acme/**'], 'use': ['core', 'acme']},
                                                 {'match': ['**'], 'use': ['core']}],
                         'no personal in the existing or the new rule, the default rule not rewritten')
        self.assertEqual(config['isolation'], {'allowed_layers': ['core']}, 'allowed_layers not widened')
        joined = ' '.join(report['warnings'])
        self.assertIn('layers use me', joined)
        self.assertIn('allowed_layers lacks', joined)


if __name__ == '__main__':
    unittest.main()
