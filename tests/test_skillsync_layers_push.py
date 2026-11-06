"""Acceptance tests of the 2.2b writes into layers (step 2.2bt): K4 `push` (owner rule, core only with
`--to-layer <core>`, read-only layers and a 0.9.x bridge config BLOCKED, detached HEAD BLOCKED, leakcheck
before any write, adoption of new files, atomic writes), the copy state `files{owner, base}` with the one-time
conversion of the legacy state (Q2) and the unknown-base conflict (Q3), `pull`/`watch`/`add` exit codes,
`import`/`skills add`/`init` on layers, `update_composites` without a lock for a project without composites,
and the K1 grep of the `skillsync` script.

All layers and names are synthetic; machine state is isolated (`ComposeCase`). Behaviour shared by both link
modes runs in both (`each_mode`); copy-state tests run in copy mode, composite adoption in symlink mode.
"""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

try:
    import fcntl
except ImportError:  # Windows: lock tests skipped (msvcrt locking is NOT_RUN there)
    fcntl = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from layers_helpers import (SKILL_TEXT, ComposeCase, composed_root, each_mode, generator_version, git,  # noqa: E402
                            link_mode, make_layer, tree_snapshot, write, yaml_text)
from layers_helpers import needs_os_symlinks  # noqa: E402

CLIENT = 'Brenk' + 'or'          # synthetic client term of a foreign identity layer (composed at runtime)
BASE_END = '<!-- SKILLSYNC:BASE-END -->'
CORE_FILES = {
    'instructions/_base.md': f'# Core base\n{BASE_END}\n',
    'skills/feature/SKILL.md': SKILL_TEXT.format('feature'),
    'skills/feature/references/core.md': 'core reference\n',
    'skills/feature/references/shared.md': 'core shared\n',
}
ORG_FILES = {
    'instructions/proj.md': '# Project template\n',
    'skills/feature/references/org.md': 'org reference\n',
    'skills/team/SKILL.md': SKILL_TEXT.format('team'),
}
PERSONAL_FILES = {
    'skills/feature/references/shared.md': 'personal shared\n',
    'skills/team/references/personal.md': 'personal note\n',
}
SKILLS = ('feature', 'team')
MIGRATE_HINT = 'layers migrate --layer-source core=PATH --apply'   # instruction for a 0.9.x bridge config


def load_generator():
    """A fresh copy of the generator CLI module (`skillsync_workflow.cli`) (in-process calls); loaded after the environment is isolated."""
    from cli_module import fresh_cli
    return fresh_cli()


class PushCase(ComposeCase):
    """core (read-only by default), org and personal layers; a foreign identity (`client`) in
    `leakguard.identity_dir`; leakguard targets for every layer."""

    def setUp(self):
        super().setUp()
        self.add_layers(core=CORE_FILES, org=ORG_FILES, personal=PERSONAL_FILES)
        self.identity_dir = self.work / 'identities'
        write(self.identity_dir / 'client.yaml', yaml_text({'schema': 1, 'layer': 'client',
                                                            'terms': [{'term': CLIENT, 'kind': 'org'}]}))
        self.configure()

    def configure(self, core_extra=None, rules=None, **extra):
        entries = [('core', 'core', self.layer_paths['core'], dict(core_extra or {})),
                   ('org', 'org', self.layer_paths['org']), ('personal', 'personal', self.layer_paths['personal'])]
        guard = {'identity_dir': str(self.identity_dir),
                 'targets': {'core': {'scope': 'public'}, 'org': {'scope': 'org-private'},
                             'personal': {'scope': 'private'}, 'client': {'scope': 'private'}}}
        self.write_config(layers=entries, rules=rules or [(['**'], ['core', 'org', 'personal'])], leakguard=guard,
                          **extra)

    def project(self, name, mode, skills=SKILLS):
        """Linked project; the layer files are reset first (`each_mode` runs both modes on the same layers)."""
        for layer_id, files in (('core', CORE_FILES), ('org', ORG_FILES), ('personal', PERSONAL_FILES)):
            for rel, text in files.items():
                path = self.layer_paths[layer_id] / rel
                if not path.is_file() or path.read_text(encoding='utf-8') != text:
                    write(path, text)
        project = self.new_project(f'{name}-{mode}', skills=skills)
        self.link_ok(project, mode=mode)
        return project

    def push(self, project, *args, mode='symlink'):
        with link_mode(mode):
            process = self.cli('push', str(project), *args)
        self.assertNotIn('Traceback', process.stderr)
        return process

    def layers_snapshot(self):
        return {layer_id: tree_snapshot(path) for layer_id, path in self.layer_paths.items()}

    def layer_file(self, layer_id, rel):
        return self.layer_paths[layer_id] / rel

    def instructions(self, project):
        return project / '.github' / 'copilot-instructions.md'

    def edit_instructions(self, project, old, new):
        path = self.instructions(project)
        text = path.read_text(encoding='utf-8')
        self.assertIn(old, text)
        path.write_text(text.replace(old, new), encoding='utf-8')

    def state(self, project):
        return json.loads((project / '.github' / '.skillsync-state.json').read_text(encoding='utf-8'))


# ====================================================================== K4: owner rule and core consent


class PushOwnerTests(PushCase):
    @each_mode
    def test_template_edit_goes_to_its_owner_even_with_another_to_layer(self, mode):
        """K4 + Q1: the owner rule always applies; `--to-layer` does not redirect a file that has an owner."""
        project = self.project('template', mode)
        self.edit_instructions(project, '# Project template', '# Project template edited')
        before = self.layers_snapshot()
        process = self.push(project, '--to-layer', 'personal', mode=mode)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(self.layer_file('org', 'instructions/proj.md').read_text(), '# Project template edited\n')
        after = self.layers_snapshot()
        self.assertEqual(after['core'], before['core'])
        self.assertEqual(after['personal'], before['personal'])
        self.assertFalse(self.layer_file('personal', 'instructions/proj.md').exists())
        again = self.push(project, mode=mode)
        self.assertEqual((again.returncode, self.layers_snapshot()), (0, after), 'a second push writes nothing')

    @each_mode
    def test_core_text_without_consent_is_info_and_skipped(self, mode):
        """K4: core is written only with `--to-layer <core>`; without it `[info]`, exit 0, other owners still written."""
        project = self.project('core-info', mode)
        self.edit_instructions(project, '# Core base', '# Core base edited')
        self.edit_instructions(project, '# Project template', '# Project template edited')
        process = self.push(project, mode=mode)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertRegex(process.stdout, r'(?m)^\[info\] .*--to-layer core.*skipped')
        self.assertEqual(self.layer_file('core', 'instructions/_base.md').read_text(), CORE_FILES['instructions/_base.md'])
        self.assertEqual(self.layer_file('org', 'instructions/proj.md').read_text(), '# Project template edited\n')

    @each_mode
    def test_core_with_consent_but_read_only_is_blocked_and_the_project_writes_nothing(self, mode):
        """K4: `writable: false` refuses every write (exit 2), also with `--to-layer core`; the project is one unit."""
        project = self.project('core-ro', mode)
        self.edit_instructions(project, '# Core base', '# Core base edited')
        self.edit_instructions(project, '# Project template', '# Project template edited')
        before = self.layers_snapshot()
        process = self.push(project, '--to-layer', 'core', mode=mode)
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertRegex(process.stdout, r'(?m)^\[blocked\] .*read-only')
        self.assertEqual(self.layers_snapshot(), before, 'a BLOCKED file stops every write of the project')

    @each_mode
    def test_core_with_consent_and_writable_is_written(self, mode):
        self.configure(core_extra={'writable': True})
        project = self.project('core-rw', mode)
        self.edit_instructions(project, '# Core base', '# Core base edited')
        process = self.push(project, '--to-layer', 'core', mode=mode)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        text = self.layer_file('core', 'instructions/_base.md').read_text()
        self.assertIn('# Core base edited', text)
        self.assertIn(BASE_END, text)

    def test_copy_edits_go_to_the_owner_of_each_file_and_become_the_new_base(self):
        project = self.project('copy-owner', 'copy')
        skill = self.skill_dir(project, 'feature')
        (skill / 'references/org.md').write_text('org edited\n')
        (skill / 'references/shared.md').write_text('shared edited\n')
        (skill / 'references/core.md').write_text('core edited\n')
        process = self.push(project, mode='copy')
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(self.layer_file('org', 'skills/feature/references/org.md').read_text(), 'org edited\n')
        self.assertEqual(self.layer_file('personal', 'skills/feature/references/shared.md').read_text(), 'shared edited\n')
        self.assertEqual(self.layer_file('core', 'skills/feature/references/shared.md').read_text(), 'core shared\n')
        self.assertEqual(self.layer_file('core', 'skills/feature/references/core.md').read_text(), 'core reference\n')
        self.assertRegex(process.stdout, r'(?m)^\[info\] .*core')
        files = self.state(project)['files']
        self.assertEqual(files['github-skill:feature/references/org.md']['owner'], 'org')
        self.assertEqual(files['github-skill:feature/references/shared.md']['owner'], 'personal')
        generator = load_generator()
        self.assertEqual(files['github-skill:feature/references/org.md']['base'],
                         generator.tree_hash(skill / 'references/org.md'))
        self.assertEqual(self.link(project, mode='copy').returncode, 0)
        self.assertEqual((skill / 'references/core.md').read_text(), 'core edited\n', 'unpushed edit kept')

    def test_dry_run_writes_nothing(self):
        project = self.project('dry', 'copy')
        (self.skill_dir(project, 'feature') / 'references/org.md').write_text('org edited\n')
        before = self.layers_snapshot()
        process = self.push(project, '--dry-run', mode='copy')
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertIn('[dry-run] Would write', process.stdout)
        self.assertEqual(self.layers_snapshot(), before)

    def test_unknown_to_layer_is_blocked(self):
        project = self.project('unknown-target', 'copy')
        (self.skill_dir(project, 'feature') / 'references/org.md').write_text('org edited\n')
        before = self.layers_snapshot()
        process = self.push(project, '--to-layer', 'nowhere', mode='copy')
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertEqual(self.layers_snapshot(), before)

    def test_legacy_library_only_config_is_blocked(self):
        project = self.project('legacy', 'symlink')
        self.config_file.write_text(yaml_text({'library': str(self.layer_paths['core'])}), encoding='utf-8')
        process = self.push(project)
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertIn('[blocked]', process.stdout)


# ====================================================================== K4: refusals (bridge, detached HEAD)


class PushRefusalTests(PushCase):
    def bridge_config(self, name):
        self.real_core = getattr(self, 'real_core', self.layer_paths['core'])
        bridge = self.work / f'old-library-{name}'
        for rel, text in CORE_FILES.items():
            write(bridge / rel, text)
        self.layer_paths['core'] = bridge
        self.configure(core_extra={'synthetic': {'version': '1.0.0', 'core_min': generator_version()}})
        return bridge

    @each_mode
    def test_push_on_a_bridge_config_is_blocked_with_the_migration(self, mode):
        """A project linked before; the machine config then carries the 0.9.x bridge (`synthetic` core): `push`
        (with and without `--to-layer core`) is BLOCKED (exit 2) with the migration instruction; nothing written."""
        if hasattr(self, 'real_core'):              # @each_mode: the previous mode left the bridge config behind
            self.layer_paths['core'] = self.real_core
            self.configure()
        project = self.project('bridge', mode)
        bridge = self.bridge_config(mode)
        self.edit_instructions(project, '# Core base', '# Core base edited')
        self.edit_instructions(project, '# Project template', '# Project template edited')
        if mode == 'copy':
            (self.skill_dir(project, 'feature') / 'references/core.md').write_text('edited\n')
        before = self.layers_snapshot()
        for extra in ((), ('--to-layer', 'core')):
            with self.subTest(args=extra):
                process = self.push(project, *extra, mode=mode)
                self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
                self.assertIn('legacy bridge', process.stdout + process.stderr)
                self.assertIn(MIGRATE_HINT, process.stdout + process.stderr)
                self.assertEqual(self.layers_snapshot(), before, 'neither the bridge nor the other layers change')
        self.assertFalse((bridge / 'layer.yaml').exists())

    def test_import_and_skills_add_on_a_bridge_config_are_blocked(self):
        bridge = self.bridge_config('import')
        before = tree_snapshot(bridge)
        source = self.work / 'to-import'
        write(source / '.github' / 'skills' / 'imported' / 'SKILL.md', SKILL_TEXT.format('imported'))
        for args in (('import', str(source), '--to-layer', 'core'), ('skills', 'add', 'brandnew', '--to-layer', 'core')):
            with self.subTest(args=args):
                process = self.cli(*args)
                self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
                self.assertIn(MIGRATE_HINT, process.stdout + process.stderr)
                self.assertEqual(tree_snapshot(bridge), before)
        self.assertFalse((source / '.github' / '.skillsync.yaml').exists())

    def test_detached_head_of_a_writable_layer_is_blocked_with_the_update_instruction(self):
        org = self.layer_paths['org']
        git(org, 'init', '-q', '-b', 'main')
        git(org, 'config', 'user.email', 'dev@example.test')
        git(org, 'config', 'user.name', 'Dev')
        git(org, 'add', '-A')
        git(org, 'commit', '-q', '--no-verify', '-m', 'init')
        git(org, 'checkout', '-q', '--detach')
        project = self.project('detached', 'copy')
        (self.skill_dir(project, 'feature') / 'references/org.md').write_text('org edited\n')
        (self.skill_dir(project, 'feature') / 'references/shared.md').write_text('shared edited\n')
        before = self.layers_snapshot()
        process = self.push(project, mode='copy')
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertRegex(process.stdout, r'(?m)^\[blocked\] .*detached HEAD.*layers update org --ref')
        self.assertEqual(self.layers_snapshot(), before)


# ====================================================================== K4: leakcheck before writing


class PushLeakTests(PushCase):
    @each_mode
    def test_content_leak_fails_and_nothing_of_the_project_is_written(self, mode):
        project = self.project('leak', mode)
        self.edit_instructions(project, '# Project template', f'# Project template {CLIENT}')
        if mode == 'copy':
            (self.skill_dir(project, 'feature') / 'references/shared.md').write_text('clean edit\n')
        before = self.layers_snapshot()
        process = self.push(project, mode=mode)
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        self.assertRegex(process.stdout, r'(?m)^\[leak\] org:instructions/proj\.md ')
        self.assertNotIn(CLIENT.lower(), (process.stdout + process.stderr).lower())
        self.assertEqual(self.layers_snapshot(), before)

    def test_file_name_leak_of_a_new_file_fails(self):
        project = self.project('leak-name', 'copy')
        name = f'notes-{CLIENT.lower()}.md'
        write(self.skill_dir(project, 'team') / 'references' / name, 'clean\n')
        before = self.layers_snapshot()
        process = self.push(project, mode='copy')
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        self.assertIn('[leak] org:skills/team/references/', process.stdout)
        self.assertEqual(self.layers_snapshot(), before)

    def test_clean_push_after_the_leak_is_removed(self):
        project = self.project('leak-fixed', 'copy')
        target = self.skill_dir(project, 'feature') / 'references/org.md'
        target.write_text(f'with {CLIENT}\n')
        self.assertEqual(self.push(project, mode='copy').returncode, 1)
        target.write_text('without it\n')
        self.assertEqual(self.push(project, mode='copy').returncode, 0)
        self.assertEqual(self.layer_file('org', 'skills/feature/references/org.md').read_text(), 'without it\n')


# ====================================================================== K4: adoption of new files


class AdoptionTests(PushCase):
    def test_new_copy_file_goes_to_the_last_provider_of_skill_md(self):
        project = self.project('adopt-copy', 'copy')
        write(self.skill_dir(project, 'team') / 'references/new.md', 'new team file\n')
        process = self.push(project, mode='copy')
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(self.layer_file('org', 'skills/team/references/new.md').read_text(), 'new team file\n')
        self.assertFalse(self.layer_file('personal', 'skills/team/references/new.md').exists())
        self.assertEqual(self.state(project)['files']['github-skill:team/references/new.md']['owner'], 'org')

    def test_new_copy_file_of_a_core_skill_needs_consent_and_to_layer_chooses_the_target(self):
        project = self.project('adopt-core', 'copy')
        write(self.skill_dir(project, 'feature') / 'references/new.md', 'new feature file\n')
        process = self.push(project, mode='copy')
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertIn('[info]', process.stdout)
        for layer_id in ('core', 'org', 'personal'):
            self.assertFalse(self.layer_file(layer_id, 'skills/feature/references/new.md').exists(), layer_id)
        process = self.push(project, '--to-layer', 'personal', mode='copy')
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(self.layer_file('personal', 'skills/feature/references/new.md').read_text(), 'new feature file\n')

    def test_new_file_in_a_composite_is_adopted_by_push_and_becomes_a_link(self):
        project = self.project('adopt-composite', 'symlink')
        composite = Path(os.path.realpath(self.skill_dir(project, 'team')))
        self.assertEqual(composite.parent, composed_root() / 'team')
        write(composite / 'references/new.md', 'new team file\n')
        process = self.push(project)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        org_file = self.layer_file('org', 'skills/team/references/new.md')
        self.assertEqual(org_file.read_text(), 'new team file\n')
        self.assertTrue((composite / 'references/new.md').is_symlink())
        self.assertEqual(os.path.realpath(composite / 'references/new.md'), os.path.realpath(org_file))
        self.link_ok(project)
        self.assertEqual((self.skill_dir(project, 'team') / 'references/new.md').read_text(), 'new team file\n')
        self.assertFalse(any(composed_root().rglob('*.orphans')))

    def test_new_file_in_a_composite_of_a_core_skill_goes_to_orphans_at_the_next_link(self):
        project = self.project('orphan-core', 'symlink')
        composite = Path(os.path.realpath(self.skill_dir(project, 'feature')))
        write(composite / 'references/new.md', 'new feature file\n')
        process = self.push(project)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertIn('[info]', process.stdout)
        self.assertFalse(self.layer_file('core', 'skills/feature/references/new.md').exists())
        self.link_ok(project)
        orphans = composite.with_name(composite.name + '.orphans')
        self.assertEqual((orphans / 'references/new.md').read_text(), 'new feature file\n')

    def test_leaking_atomic_save_in_a_composite_is_kept_as_an_orphan(self):
        project = self.project('orphan-leak', 'symlink')
        composite = Path(os.path.realpath(self.skill_dir(project, 'feature')))
        temporary = composite / 'references/.org.md.swp'
        temporary.write_text(f'with {CLIENT}\n')
        os.replace(temporary, composite / 'references/org.md')
        result = self.link_ok(project)
        self.assertIn('not adopted', result.stdout)
        self.assertNotIn(CLIENT.lower(), result.stdout.lower())
        self.assertEqual(self.layer_file('org', 'skills/feature/references/org.md').read_text(), 'org reference\n')
        orphans = composite.with_name(composite.name + '.orphans')
        self.assertEqual((orphans / 'references/org.md').read_text(), f'with {CLIENT}\n')


# ====================================================================== atomic layer writes


class AtomicWriteTests(PushCase):
    def test_failed_replace_leaves_the_old_file_and_no_partial_file(self):
        generator = load_generator()
        layerset = generator.resolve_project_layers(None, generator.load_config())
        org = layerset.get('org')
        target = self.layer_file('org', 'skills/feature/references/org.md')
        with mock.patch.object(generator.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                generator.write_layer_file(org, 'skills/feature/references/org.md', b'half written\n')
        self.assertEqual(target.read_text(), 'org reference\n')
        self.assertEqual(sorted(p.name for p in target.parent.iterdir()), ['org.md'], 'temporary file removed')
        generator.write_layer_file(org, 'skills/feature/references/org.md', b'whole\n')
        self.assertEqual(target.read_text(), 'whole\n')

    @needs_os_symlinks
    def test_paths_escaping_the_layer_or_through_a_link_are_refused(self):
        generator = load_generator()
        layerset = generator.resolve_project_layers(None, generator.load_config())
        org = layerset.get('org')
        outside = self.work / 'outside'
        outside.mkdir()
        os.symlink(outside, self.layer_paths['org'] / 'linked')
        for rel in ('../escape.md', 'linked/x.md'):
            with self.subTest(rel=rel):
                with self.assertRaises(ValueError):
                    generator.write_layer_file(org, rel, b'x\n')
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((self.work / 'layers' / 'escape.md').exists())


# ====================================================================== copy state files{owner, base}


class CopyStateConversionTests(PushCase):
    """K4 copy mode: legacy state `{"<kind>:<name>": <tree hash>}` converted once (Q2, Q3)."""

    def legacy_project(self, name):
        project = self.project(name, 'copy', skills=('feature',))
        generator = load_generator()
        skill = self.skill_dir(project, 'feature')
        state_path = project / '.github' / '.skillsync-state.json'
        return project, skill, state_path, generator

    def write_legacy(self, state_path, value, skill='feature'):
        """Replace the per-file entries of `github-skill:<skill>` by one legacy tree-hash entry (other copies kept)."""
        state = json.loads(state_path.read_text(encoding='utf-8'))
        files = {key: entry for key, entry in state.get('files', {}).items()
                 if not key.startswith(f'github-skill:{skill}/')}
        state = {key: item for key, item in state.items() if key != 'files'}
        if files:
            state['files'] = files
        state[f'github-skill:{skill}'] = value
        state_path.write_text(json.dumps(state), encoding='utf-8')

    def test_diverged_tree_does_not_bring_back_a_file_removed_from_the_layers(self):
        """Review 2.2b-1 (minor): in a diverged legacy tree a file that the layers no longer have is `[info]`, never
        adopted back by `push` (its owner would be the writable org layer: last provider of `SKILL.md`)."""
        project = self.project('legacy-removed', 'copy', skills=('team',))
        state_path = project / '.github' / '.skillsync-state.json'
        self.layer_file('personal', 'skills/team/references/personal.md').unlink()
        self.write_legacy(state_path, '0' * 64, skill='team')
        before = self.layers_snapshot()
        process = self.push(project, mode='copy')
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertRegex(process.stdout, r'(?m)^\[info\] .*personal\.md')
        self.assertEqual(self.layers_snapshot(), before)
        for layer_id in ('org', 'personal'):
            self.assertFalse(self.layer_file(layer_id, 'skills/team/references/personal.md').exists(), layer_id)

    def test_untouched_tree_gets_exact_bases_and_keeps_refreshing(self):
        project, skill, state_path, generator = self.legacy_project('legacy-clean')
        self.write_legacy(state_path, generator.tree_hash(skill))
        write(self.layer_file('org', 'skills/feature/references/org.md'), 'org v2\n')
        self.link_ok(project, mode='copy')
        self.assertEqual((skill / 'references/org.md').read_text(), 'org v2\n')
        files = self.state(project)['files']
        self.assertNotIn('github-skill:feature', self.state(project))
        for rel in ('SKILL.md', 'references/core.md', 'references/org.md', 'references/shared.md'):
            entry = files[f'github-skill:feature/{rel}']
            self.assertEqual(entry['base'], generator.tree_hash(skill / rel), rel)
        self.assertEqual(files['github-skill:feature/references/org.md']['owner'], 'org')
        self.assertEqual(files['github-skill:feature/references/shared.md']['owner'], 'personal')
        write(self.layer_file('core', 'skills/feature/references/core.md'), 'core v2\n')
        self.link_ok(project, mode='copy')
        self.assertEqual((skill / 'references/core.md').read_text(), 'core v2\n')

    def diverged(self, name):
        project, skill, state_path, generator = self.legacy_project(name)
        (skill / 'references/org.md').write_text('local org edit\n')
        self.write_legacy(state_path, '0' * 64)
        process = self.link_ok(project, mode='copy')
        return project, skill, generator, process

    def test_diverged_tree_marks_differing_files_as_local_edits_never_overwritten(self):
        project, skill, generator, process = self.diverged('legacy-diverged')
        self.assertRegex(process.stdout, r'(?m)^\[warn\] .*unknown merge base')
        files = self.state(project)['files']
        self.assertIsNone(files['github-skill:feature/references/org.md']['base'])
        self.assertEqual(files['github-skill:feature/references/core.md']['base'],
                         generator.tree_hash(self.layer_file('core', 'skills/feature/references/core.md')))
        write(self.layer_file('org', 'skills/feature/references/org.md'), 'org v2\n')
        write(self.layer_file('core', 'skills/feature/references/core.md'), 'core v2\n')
        self.link_ok(project, mode='copy')
        self.assertEqual((skill / 'references/org.md').read_text(), 'local org edit\n', 'never overwritten')
        self.assertEqual((skill / 'references/core.md').read_text(), 'core v2\n', 'known base: refreshed')

    def test_push_of_an_unknown_base_file_different_from_the_owner_is_a_conflict(self):
        """Q3: `[conflict]` with both ways out, the whole project stopped (exit 2, nothing written)."""
        project, skill, _generator, _process = self.diverged('legacy-q3')
        (skill / 'references/shared.md').write_text('shared edited\n')  # a clean edit of the same project
        before = self.layers_snapshot()
        process = self.push(project, mode='copy')
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertRegex(process.stdout, r'(?m)^\[conflict\] .*org\.md.*unknown merge base')
        self.assertIn('skillsync link', process.stdout)
        self.assertEqual(self.layers_snapshot(), before)

    def test_unknown_base_file_equal_to_the_owner_gets_its_base_without_a_conflict(self):
        project, skill, generator, _process = self.diverged('legacy-equal')
        (skill / 'references/org.md').write_text('org reference\n')
        before = self.layers_snapshot()
        process = self.push(project, mode='copy')
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertNotIn('[conflict]', process.stdout)
        self.assertEqual(self.layers_snapshot(), before)
        self.assertEqual(self.state(project)['files']['github-skill:feature/references/org.md']['base'],
                         generator.tree_hash(skill / 'references/org.md'))

    def test_push_converts_a_legacy_state_too(self):
        project, skill, state_path, generator = self.legacy_project('legacy-push')
        self.write_legacy(state_path, generator.tree_hash(skill))
        (skill / 'references/org.md').write_text('org edited\n')  # local edit after the legacy hash was taken
        self.write_legacy(state_path, '0' * 64)
        process = self.push(project, mode='copy')
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)  # unknown base, differs: Q3
        self.assertIn('files', self.state(project))
        self.assertNotIn('github-skill:feature', self.state(project))


# ====================================================================== after code review 2.2b, round 1


class ReviewRound1Tests(PushCase):
    """Findings of `R/code-review-2.2b-1.md` and the decisions in `R/code-review-2.2b-1-decyzje.md`."""

    def quiet(self):
        return contextlib.redirect_stdout(io.StringIO())

    def test_new_file_then_one_watch_cycle_with_the_real_link_then_push_writes_nothing(self):
        for mode in ('symlink', 'copy'):
            with self.subTest(link_mode=mode):
                self.check_new_file_watch_cycle(mode)

    def check_new_file_watch_cycle(self, mode):
        """Major 2 (spec K4 test "new file + watch + push"): the real `link` adopts the file, the snapshot taken after
        `link` keeps its own rebuild from starting another cycle, and the following `push` has nothing to write."""
        project = self.project(f'watch-real-{mode}', mode)
        composite = Path(os.path.realpath(self.skill_dir(project, 'team')))
        generator = load_generator()
        sleeps = {'n': 0}
        real_sleep = generator.time.sleep

        def sleep(seconds):
            # Only the watch loop (interval 0 below) counts; other sleeps (the composite cache lock retry, 0.05 s,
            # under load) really wait and are not cycles of the watch.
            if seconds != 0:
                return real_sleep(seconds)
            sleeps['n'] += 1
            if sleeps['n'] == 1:
                write(composite / 'references/new.md', 'new team file\n')
                if mode == 'copy':      # the watch roots are the layers and the composites, never the project copies:
                    write(self.layer_paths['core'] / 'skills/team/references/touch.md', 'unrelated\n')  # a layer change starts the cycle
            elif sleeps['n'] >= 3:
                raise KeyboardInterrupt

        out = io.StringIO()
        with link_mode(mode), mock.patch.object(generator.time, 'sleep', sleep), \
                mock.patch.object(generator, 'cmd_link', wraps=generator.cmd_link) as linked, \
                contextlib.redirect_stdout(out):
            code = generator.cmd_watch(argparse.Namespace(path=str(project), interval=0))
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(sleeps['n'], 3)
        self.assertEqual(linked.call_count, 1, 'the cycle after the link must not link again\n' + out.getvalue())
        org_file = self.layer_file('org', 'skills/team/references/new.md')
        self.assertEqual(org_file.read_text(), 'new team file\n')
        entry = Path(os.path.realpath(self.skill_dir(project, 'team'))) / 'references/new.md'
        if mode == 'symlink':
            self.assertTrue(entry.is_symlink())
            self.assertEqual(os.path.realpath(entry), os.path.realpath(org_file))
        else:                                                         # copy mode: a plain copy of the adopted owner file
            self.assertFalse(entry.is_symlink())
            self.assertEqual(entry.read_text(), 'new team file\n')
        before = self.layers_snapshot()
        process = self.push(project)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(self.layers_snapshot(), before, 'push after the adoption writes nothing')

    def test_duplicate_file_planned_from_two_copies_with_different_content_is_a_conflict(self):
        """Minor (plan): `.github` and `.claude` copies of the same layer file differ: exit 2, nothing written."""
        project = self.new_project('duplicate-copy', skills=SKILLS, claude=True)
        self.link_ok(project, mode='copy')
        github = self.skill_dir(project, 'team') / 'references/personal.md'
        claude = project / '.claude' / 'skills' / 'team' / 'references' / 'personal.md'
        self.assertTrue(claude.is_file() and not claude.is_symlink(), 'the fixture needs a .claude copy')
        github.write_text('edit A\n')
        claude.write_text('edit B\n')
        before = self.layers_snapshot()
        process = self.push(project, mode='copy')
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertIn('[conflict]', process.stdout)
        self.assertEqual(self.layers_snapshot(), before)

    @needs_os_symlinks
    def test_bad_path_in_the_second_file_of_a_unit_is_refused_at_plan_time(self):
        """Minor (D73/`..` validated in `plan`): BLOCKED before the first write, the first file is not written."""
        generator = load_generator()
        layerset = generator.resolve_project_layers(None, generator.load_config())
        org = layerset.get('org')
        outside = self.work / 'outside'
        outside.mkdir()
        for bad, prepare in (('z/../../escape.md', None),
                             ('linked/x.md', lambda: os.symlink(outside, self.layer_paths['org'] / 'linked'))):
            with self.subTest(path=bad):
                if prepare:
                    prepare()
                writer = generator.LayerWriter(layerset)
                with self.quiet():
                    self.assertTrue(writer.plan(org, 'a/ok.md', b'ok\n', 'first file'))
                    self.assertFalse(writer.plan(org, bad, b'bad\n', 'second file'))
                    self.assertEqual(writer.commit(), 2)
                self.assertFalse((self.layer_paths['org'] / 'a' / 'ok.md').exists())
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((self.work / 'escape.md').exists())

    def test_owner_file_changed_between_plan_and_commit_is_a_conflict(self):
        """Minor (parallel pushes): the owner's file no longer has the expected base at `commit`: `[conflict]`,
        exit 2, the other change is not overwritten."""
        project = self.project('race', 'copy')
        (self.skill_dir(project, 'feature') / 'references/org.md').write_text('mine\n')
        generator = load_generator()
        config = generator.load_config()
        writer = generator.LayerWriter(generator.resolve_project_layers(project, config))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            generator.sync_copies_to_library(project / '.github' / '.skillsync.yaml', config, writer=writer)
            write(self.layer_file('org', 'skills/feature/references/org.md'), 'theirs\n')  # the other push won
            code = writer.commit()
        self.assertEqual(code, 2, out.getvalue())
        self.assertIn('[conflict]', out.getvalue())
        self.assertEqual(self.layer_file('org', 'skills/feature/references/org.md').read_text(), 'theirs\n')

    def test_source_with_variables_is_skipped_with_a_warning(self):
        """Minor: `push` never writes rendered values into a `_base`, fragment or template that has `{{ var }}`."""
        cases = {
            'template': ('org', 'instructions/proj.md', '# Project {{ who }}\n', '# Project World', ()),
            'base': ('core', 'instructions/_base.md', f'# Core {{{{ who }}}}\n{BASE_END}\n', '# Core World',
                     ('--to-layer', 'core')),
            'fragment': ('personal', 'instructions/_base.d/note.md', 'Hello {{ who }}\n', 'Hello World', ()),
        }
        for name, (layer_id, rel, text, rendered, args) in cases.items():
            with self.subTest(source=name):
                write(self.layer_file(layer_id, rel), text)
                self.configure(core_extra={'writable': True}, variables={'who': 'World'})
                project = self.new_project(f'vars-{name}', skills=SKILLS)
                self.link_ok(project)
                self.edit_instructions(project, rendered, rendered + ' edited')
                process = self.push(project, *args)
                self.assertIn('[warn]', process.stdout)
                self.assertEqual(self.layer_file(layer_id, rel).read_text(), text)
                self.assertNotIn('World', self.layer_file(layer_id, rel).read_text())
                for layer, files in (('org', ORG_FILES), ('core', CORE_FILES)):
                    for item, value in files.items():
                        write(self.layer_file(layer, item), value)

    def test_an_edited_fragment_block_goes_to_its_fragment_file_and_nothing_else_is_touched(self):
        """K4.17: the edit of a `SKILLSYNC:FRAGMENT` block in the generated instructions is written (after leakcheck)
        to `instructions/_base.d/<file>` of the fragment's layer; the other layers' files stay as they were."""
        rel = 'instructions/_base.d/30-habit.md'
        write(self.layer_file('personal', rel), 'Habit one\n')
        project = self.new_project('fragment-push', skills=SKILLS)
        self.link_ok(project)
        self.edit_instructions(project, 'Habit one', 'Habit one, edited')
        before = self.layers_snapshot()
        process = self.push(project)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(self.layer_file('personal', rel).read_text(), 'Habit one, edited\n')
        after = self.layers_snapshot()
        self.assertEqual(after['core'], before['core'])
        self.assertEqual(after['org'], before['org'])
        self.assertEqual(self.layer_file('core', 'instructions/_base.md').read_text(), CORE_FILES['instructions/_base.md'])

    def test_a_leaking_fragment_edit_is_refused_and_the_report_names_the_full_path(self):
        """K4.17 + K6.4 (D71): leakcheck runs before the write; the `[leak]` line holds `layer:path` of the fragment."""
        rel = 'instructions/_base.d/30-habit.md'
        write(self.layer_file('personal', rel), 'Habit one\n')
        project = self.new_project('fragment-leak', skills=SKILLS)
        self.link_ok(project)
        self.edit_instructions(project, 'Habit one', f'Habit one for {CLIENT}')
        before = self.layers_snapshot()
        process = self.push(project)
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        self.assertRegex(process.stdout, rf'(?m)^\[leak\] personal:{re.escape(rel)} ')
        self.assertNotIn(CLIENT.lower(), (process.stdout + process.stderr).lower())
        self.assertEqual(self.layers_snapshot(), before)

    def test_leakcheck_report_names_the_full_path_of_a_hit_in_a_path_with_the_client_term(self):
        """K6.4 (D71): a hit in a file name is reported with the whole relative path, term included, never masked."""
        rel = f'skills/team/references/notes-{CLIENT.lower()}/plan.md'
        write(self.layer_file('org', rel), 'clean text\n')
        process = self.cli('leakcheck', str(self.layer_paths['org']), '--identity', str(self.identity_dir / 'client.yaml'),
                           '--no-config', '--json')
        self.assertNotIn('Traceback', process.stderr)
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        report = json.loads(process.stdout)
        self.assertIn(rel, {item['path'] for item in report['findings']}, report)

    def test_every_conflict_of_push_exits_2(self):
        """Decision: each `[conflict]` in `push` = exit 2, also a plain three-way conflict (both sides changed)."""
        project = self.project('three-way', 'copy')
        (self.skill_dir(project, 'feature') / 'references/org.md').write_text('local\n')
        write(self.layer_file('org', 'skills/feature/references/org.md'), 'layer\n')
        before = self.layers_snapshot()
        process = self.push(project, mode='copy')
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertIn('[conflict]', process.stdout)
        self.assertEqual(self.layers_snapshot(), before)

    def test_untracked_file_gets_its_own_message_not_the_unknown_base_one(self):
        """Nit: a copy file without a state entry is not described as a legacy conversion leftover."""
        project = self.project('untracked', 'copy')
        target = self.skill_dir(project, 'feature') / 'references/org.md'
        target.write_text('local\n')
        state_path = project / '.github' / '.skillsync-state.json'
        state = json.loads(state_path.read_text(encoding='utf-8'))
        del state['files']['github-skill:feature/references/org.md']
        state_path.write_text(json.dumps(state), encoding='utf-8')
        process = self.link_ok(project, mode='copy')
        self.assertNotIn('unknown merge base', process.stdout)
        self.assertEqual(target.read_text(), 'local\n')
        pushed = self.push(project, mode='copy')
        self.assertNotIn('unknown merge base', pushed.stdout)

    def test_dry_run_shows_the_planned_composite_adoption(self):
        """Nit: `push --dry-run` lists the adoption of a new composite file without writing anything."""
        project = self.project('dry-adopt', 'symlink')
        composite = Path(os.path.realpath(self.skill_dir(project, 'team')))
        write(composite / 'references/new.md', 'new\n')
        before = self.layers_snapshot()
        process = self.push(project, '--dry-run')
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertRegex(process.stdout, r'\[dry-run\] Would write: .*skills/team/references/new\.md')
        self.assertEqual(self.layers_snapshot(), before)
        self.assertFalse((composite / 'references/new.md').is_symlink())


# ====================================================================== pull, watch, add, import, skills add, init


class CommandExitCodeTests(PushCase):
    def break_org(self):
        make_layer(self.layer_paths['org'], 'org', 'org', ORG_FILES, core_min='99.0.0')

    def test_pull_updates_the_layers_and_links(self):
        project = self.new_project('pull', skills=SKILLS)
        generator = load_generator()
        layers = generator._layers_module()
        with mock.patch.object(layers, 'layers_update', wraps=layers.layers_update) as update, \
                contextlib.redirect_stdout(open(os.devnull, 'w')):
            code = generator.cmd_pull(mock.Mock(path=str(project)))
        self.assertEqual(code, 0)
        update.assert_called_once_with()
        self.assertTrue(self.skill_dir(project, 'feature').exists())

    def test_pull_add_and_watch_carry_the_link_status(self):
        generator = load_generator()
        project = self.new_project('codes', skills=('feature',))
        for code in (0, 1, 2):
            with self.subTest(code=code), mock.patch.object(generator, 'cmd_link', return_value=code), \
                    contextlib.redirect_stdout(open(os.devnull, 'w')):
                self.assertEqual(generator.cmd_pull(mock.Mock(path=str(project))), code, 'pull')
                manifest = project / '.github' / '.skillsync.yaml'
                manifest.write_text(yaml_text({'skills': ['feature']}), encoding='utf-8')
                self.assertEqual(generator.cmd_add(mock.Mock(project=str(project), skill='team', layers=[])), code, 'add')
                self.assertEqual(self.watch_once(generator), code, 'watch')

    def watch_once(self, generator):
        """One `watch` cycle with a change in a layer, then Ctrl+C; returns the exit code."""
        calls = {'n': 0}
        real_sleep = generator.time.sleep

        def sleep(seconds):
            if seconds != 0:            # only the watch loop (interval 0) counts; e.g. the cache lock retry really waits
                return real_sleep(seconds)
            calls['n'] += 1
            if calls['n'] == 1:
                write(self.layer_file('personal', 'skills/team/references/watch.md'), f'change {id(calls)}\n')
                return
            raise KeyboardInterrupt
        with mock.patch.object(generator.time, 'sleep', sleep):
            return generator.cmd_watch(mock.Mock(path=None, interval=0))

    def test_cli_pull_and_add_exit_codes(self):
        project = self.new_project('cli', skills=('feature',))
        process = self.cli('pull', str(project))
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        process = self.cli('add', str(project), 'team')
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertTrue(self.skill_dir(project, 'team').exists())
        self.assertEqual(self.cli('add', str(project), 'no-such-skill').returncode, 1)
        self.break_org()
        process = self.cli('pull', str(project))
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        manifest = project / '.github' / '.skillsync.yaml'
        manifest.write_text(manifest.read_text().replace('- team\n', ''), encoding='utf-8')
        self.assertEqual(self.cli('add', str(project), 'team').returncode, 2)

    def test_watch_watches_every_present_layer_and_the_composite_cache(self):
        absent = self.work / 'absent-layer'
        entries = [('core', 'core', self.layer_paths['core']), ('org', 'org', self.layer_paths['org']),
                   ('personal', 'personal', self.layer_paths['personal']), ('gone', 'org', absent)]
        self.write_config(layers=entries, rules=[(['**'], ['core', 'org', 'personal'])])
        self.link_ok(self.new_project('watched', skills=SKILLS))
        generator = load_generator()
        machine = generator.machine_config(generator.load_config())
        roots = [os.path.realpath(path) for path in generator.watch_roots(machine)]
        expected = [os.path.realpath(self.layer_paths[i]) for i in ('core', 'org', 'personal')]
        self.assertEqual(roots, expected + [os.path.realpath(composed_root())])
        before = generator.watch_snapshot(machine)
        composite = next(path for path in (composed_root() / 'team').iterdir() if not path.name.endswith('.orphans'))
        write(composite.with_name(composite.name + '.orphans') / 'x.md', 'orphan\n')
        self.assertEqual(generator.watch_snapshot(machine), before, '.orphans are not watched')
        for path in (composite / 'references/new.md', self.layer_file('org', 'skills/team/x.md')):
            with self.subTest(path=path.name):
                write(path, 'new\n')
                self.assertNotEqual(generator.watch_snapshot(machine), before)
                path.unlink()

    def test_watch_with_a_legacy_config_is_blocked(self):
        self.config_file.write_text(yaml_text({'library': str(self.layer_paths['core'])}), encoding='utf-8')
        generator = load_generator()
        with contextlib.redirect_stdout(open(os.devnull, 'w')):
            self.assertEqual(generator.cmd_watch(mock.Mock(path=None, interval=0)), 2)

    @needs_os_symlinks
    def test_import_writes_into_personal_or_to_layer_and_creates_the_manifest(self):
        source = self.work / 'import-src'
        write(source / '.github' / 'skills' / 'imported' / 'SKILL.md', SKILL_TEXT.format('imported'))
        write(source / '.github' / 'skills' / 'imported' / 'references' / 'r.md', 'ref\n')
        write(source / '.github' / 'copilot-instructions.md', '# Imported rules\n')
        os.symlink(self.layer_paths['core'] / 'skills' / 'feature', source / '.github' / 'skills' / 'feature')
        process = self.cli('import', str(source))
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertIn('[info]', process.stdout)  # the linked skill is skipped
        self.assertEqual(self.layer_file('personal', 'skills/imported/references/r.md').read_text(), 'ref\n')
        self.assertFalse(self.layer_file('personal', 'skills/feature/SKILL.md').exists(), 'linked skill not imported')
        manifest = (source / '.github' / '.skillsync.yaml').read_text()
        self.assertIn('imported', manifest)
        other = self.work / 'import-org'
        write(other / '.github' / 'skills' / 'orgimp' / 'SKILL.md', SKILL_TEXT.format('orgimp'))
        self.assertEqual(self.cli('import', str(other), '--to-layer', 'org').returncode, 0)
        self.assertTrue(self.layer_file('org', 'skills/orgimp/SKILL.md').is_file())

    def test_import_refusals_write_nothing_and_create_no_manifest(self):
        cases = {'core-read-only': ((), '--to-layer', 'core', 2), 'leak': ((f'with {CLIENT}\n',), None, None, 1)}
        for name, (leak, flag, value, code) in cases.items():
            with self.subTest(case=name):
                source = self.work / f'import-{name}'
                write(source / '.github' / 'skills' / f'imp-{name}' / 'SKILL.md', SKILL_TEXT.format(name))
                write(source / '.github' / 'skills' / f'imp-{name}' / 'clean.md', 'clean\n')
                if leak:
                    write(source / '.github' / 'skills' / f'imp-{name}' / 'notes.md', leak[0])
                before = self.layers_snapshot()
                process = self.cli('import', str(source), *((flag, value) if flag else ()))
                self.assertEqual(process.returncode, code, process.stdout + process.stderr)
                self.assertEqual(self.layers_snapshot(), before)
                self.assertFalse((source / '.github' / '.skillsync.yaml').exists())

    def test_skills_add_on_layers(self):
        process = self.cli('skills', 'add', 'fresh')
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertTrue(self.layer_file('personal', 'skills/fresh/SKILL.md').is_file())
        self.assertEqual(self.cli('skills', 'add', 'orgfresh', '--to-layer', 'org').returncode, 0)
        self.assertTrue(self.layer_file('org', 'skills/orgfresh/SKILL.md').is_file())
        before = self.layers_snapshot()
        for args, code in ((('team',), 1), (('fresh',), 1), (('../bad',), 1), (('ro', '--to-layer', 'core'), 2),
                           (('elsewhere', '--to-layer', 'nowhere'), 2), ((f'{CLIENT.lower()}-notes',), 1)):
            with self.subTest(args=args):
                process = self.cli('skills', 'add', *args)
                self.assertEqual(process.returncode, code, process.stdout + process.stderr)
                self.assertEqual(self.layers_snapshot(), before)

    def test_init_creates_a_personal_layer(self):
        target = self.work / 'my-personal'
        process = self.cli('init', str(target), '--id', 'mine')
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        import yaml
        meta = yaml.safe_load((target / 'layer.yaml').read_text())
        self.assertEqual((meta['id'], meta['kind']), ('mine', 'personal'))
        self.assertTrue((target / '.git').is_dir())
        default = self.cli('init')
        self.assertEqual(default.returncode, 0, default.stdout + default.stderr)
        generator = load_generator()
        self.assertTrue((generator._layers_module().default_layer_path('personal') / 'layer.yaml').is_file())


# ====================================================================== update_composites without the lock


@unittest.skipUnless(fcntl is not None, 'fcntl.flock is unavailable on this host (Windows uses msvcrt, NOT_RUN)')
class NoLockWithoutCompositeTests(PushCase):
    """Runda 3 2.2a: a project without composites and without a registry entry never waits for the cache lock,
    even when another project's registry exists and another process holds the lock."""

    @contextlib.contextmanager
    def foreign_lock(self):
        composed_root().mkdir(parents=True, exist_ok=True)
        with open(composed_root() / '.lock', 'a+b') as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @each_mode
    def test_plain_project_links_under_a_foreign_lock(self, mode):
        self.link_ok(self.new_project(f'with-composite-{mode}', skills=SKILLS))  # symlink: writes the registry
        self.assertTrue((composed_root() / '.projects.json').is_file())
        plain = self.new_project(f'plain-{mode}', skills=('feature',))
        self.configure(rules=[([f'**/plain-{mode}'], ['core']), (['**'], ['core', 'org', 'personal'])])
        with self.foreign_lock():
            for command in ('link', 'instructions'):
                with self.subTest(command=command), link_mode(mode):
                    process = self.cli(command, str(plain), env={'SKILLSYNC_LOCK_TIMEOUT': '0.3'})
                    self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
                    self.assertNotIn('[blocked]', process.stdout)
        self.assertTrue(self.skill_dir(plain, 'feature').exists())
        self.assertNotIn(str(plain.resolve()), json.loads((composed_root() / '.projects.json').read_text()))


# ====================================================================== K1 grep (simple form; full script in 2.3)


class K1GrepTests(unittest.TestCase):
    PATTERN = re.compile(r"\[[\"']library[\"']\]|get\(\s*[\"']library|sync\.library|--library")

    CLI_SOURCE = ROOT / 'skillsync_workflow' / 'cli.py'

    def hits(self, text):
        return [f'{number}: {line.strip()}' for number, line in enumerate(text.splitlines(), 1)
                if self.PATTERN.search(line)]

    def test_skillsync_cli_module_never_reads_the_library_key(self):
        text = self.CLI_SOURCE.read_text(encoding='utf-8')
        self.assertGreater(len(text.splitlines()), 1000, 'the generator body lives in skillsync_workflow/cli.py')
        self.assertEqual(self.hits(text), [])

    def test_the_wrapper_has_no_generator_logic_to_scan(self):
        self.assertLess(len((ROOT / 'skillsync').read_text(encoding='utf-8').splitlines()), 21)  # K2: at most 20 lines

    def test_a_planted_bypass_in_a_copy_of_the_cli_module_is_found(self):
        text = self.CLI_SOURCE.read_text(encoding='utf-8') + "\nSNEAK = config.get('library')\n"
        found = self.hits(text)
        self.assertEqual(len(found), 1, found)
        self.assertIn("config.get('library')", found[0])

    def test_the_pattern_catches_each_form(self):
        for line in ('config["library"]', "x.get('library')", 'manifest sync.library', 'parser --library'):
            self.assertRegex(line, self.PATTERN)


if __name__ == '__main__':
    unittest.main()
