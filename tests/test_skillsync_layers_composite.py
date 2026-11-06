"""Acceptance tests of the 2.2a composition (step 2.2at): K4 (composite, `.orphans`, copy mode), K12 for `link`
and the layer selection of `link` (K3, rule and `--layer`).

Covered in tests/test_skillsync_layers_push.py (step 2.2bt): `files{owner, base}`, `push`, adoption, conversion of the old copy state.
All layers are synthetic, all machine state is isolated (`ComposeCase`), every behaviour runs in both link modes
(`mode` is chosen per `link` call; each mode has its own project)."""
import errno
import contextlib
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tomllib
try:
    import fcntl
except ImportError:  # Windows: the lock tests below are skipped (msvcrt locking is NOT_RUN there)
    fcntl = None
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from layers_helpers import (SKILL_TEXT, ComposeCase, LINK_MODES, composed_root, each_mode, generator_version, link_mode,  # noqa: E402
                            make_layer, tree_snapshot, write)
from layers_helpers import needs_os_symlinks, require_os_symlinks  # noqa: E402

CORE_FILES = {
    'instructions/_base.md': '# Core base\n<!-- SKILLSYNC:BASE-END -->\n',
    'instructions/proj.md': '# Project template\n',
    'codex.yaml': 'max_concurrent_threads_per_session: 2\n',
    'skills/feature/SKILL.md': SKILL_TEXT.format('feature'),
    'skills/feature/references/core.md': 'core reference\n',
    'skills/feature/references/shared.md': 'core shared\n',
    'skills/solo/SKILL.md': SKILL_TEXT.format('solo'),
    'skills/solo/notes.md': 'solo notes\n',
}
ORG_FILES = {'skills/feature/references/org.md': 'org reference\n'}
PERSONAL_FILES = {'skills/feature/references/shared.md': 'personal shared\n'}
SKILLS = ('feature', 'solo')


def expected_hash(files):
    """Independent re-statement of the spec definition: SHA-256 of the sorted (path, layer id, realpath) rows."""
    rows = sorted((rel, layer_id, os.path.realpath(path)) for rel, (layer_id, path) in files.items())
    return hashlib.sha256(''.join(f'{rel}\0{layer_id}\0{real}\n' for rel, layer_id, real in rows).encode()).hexdigest()


class CompositeCase(ComposeCase):
    def setUp(self):
        super().setUp()
        self.add_layers(core=CORE_FILES, org=ORG_FILES, personal=PERSONAL_FILES)

    def sources(self, skill='feature', layers=('core', 'org', 'personal')):
        """{rel: (layer id, source path)} of a skill: per file the later of `layers` wins."""
        result = {}
        for layer_id in layers:
            base = self.layer_paths[layer_id] / 'skills' / skill
            if base.is_dir():
                for path in sorted(base.rglob('*')):
                    if path.is_file():
                        result[path.relative_to(base).as_posix()] = (layer_id, path)
        return result

    def composite_of(self, project, skill='feature'):
        link = self.skill_dir(project, skill)
        self.assertTrue(link.is_symlink(), f'{link} is not a symlink')
        return Path(os.path.realpath(link))

    def only_composite(self, skill='feature'):
        found = self.composites(skill)
        self.assertEqual(len(found), 1, found)
        return composed_root() / skill / found[0]

    def set_rules(self, *pairs):
        """Config with all three layers and `(glob, [ids])` rules."""
        entries = [(i, i, self.layer_paths[i]) for i in ('core', 'org', 'personal')]
        self.write_config(layers=entries, rules=[([glob], use) for glob, use in pairs])


class SingleAndMultiProviderTests(CompositeCase):
    @each_mode
    def test_single_provider_skill_is_the_layer_directory_or_a_per_file_copy(self, mode):
        project = self.new_project(f'single-{mode}', skills=SKILLS)
        self.link_ok(project, mode=mode)
        solo = self.skill_dir(project, 'solo')
        if mode == 'symlink':
            self.assertTrue(solo.is_symlink())
            self.assertEqual(os.path.realpath(solo), os.path.realpath(self.layer_paths['core'] / 'skills' / 'solo'))
        else:
            self.assertTrue(solo.is_dir() and not solo.is_symlink())
            self.assertEqual([p for p in solo.rglob('*') if p.is_symlink()], [])
        self.assertEqual((solo / 'notes.md').read_text(), 'solo notes\n')
        self.assertEqual(self.composites('solo'), [], 'a skill with one provider needs no composite')

    @each_mode
    def test_skill_with_several_providers_is_a_composite_or_per_file_copies(self, mode):
        project = self.new_project(f'multi-{mode}', skills=SKILLS)
        self.link_ok(project, mode=mode)
        skill = self.skill_dir(project, 'feature')
        wanted = self.sources()
        self.assertEqual(sorted(wanted), ['SKILL.md', 'references/core.md', 'references/org.md',
                                          'references/shared.md'])
        self.assertEqual(wanted['references/shared.md'][0], 'personal')  # the later layer wins per file
        for rel, (_layer_id, path) in wanted.items():
            self.assertEqual((skill / rel).read_text(), path.read_text(), rel)
        if mode == 'symlink':
            composite = self.composite_of(project)
            self.assertEqual(composite.parent, composed_root() / 'feature')
            for rel, (_layer_id, path) in wanted.items():
                entry = composite / rel
                self.assertTrue(entry.is_symlink(), rel)
                self.assertEqual(os.readlink(entry), str(path), rel)  # straight to the source file
        else:
            self.assertTrue(skill.is_dir() and not skill.is_symlink())
            self.assertEqual([p for p in skill.rglob('*') if p.is_symlink()], [])

    def test_editing_through_the_composite_changes_the_owning_layer_file(self):
        project = self.new_project('edit', skills=SKILLS)
        self.link_ok(project)
        org_file = self.layer_paths['org'] / 'skills/feature/references/org.md'
        with open(self.skill_dir(project, 'feature') / 'references' / 'org.md', 'a', encoding='utf-8') as handle:
            handle.write('appended\n')
        self.assertEqual(org_file.read_text(), 'org reference\nappended\n')

    def test_copy_mode_creates_no_cache_state(self):
        project = self.new_project('copy-cache', skills=SKILLS)
        before = tree_snapshot(composed_root())
        self.link_ok(project, mode='copy')
        self.assertEqual(tree_snapshot(composed_root()), before)
        self.assertFalse((composed_root() / 'feature').exists())

    def test_copy_mode_refreshes_untouched_copies_and_never_overwrites_a_local_edit(self):
        project = self.new_project('copy-refresh', skills=SKILLS)
        self.link_ok(project, mode='copy')
        skill = self.skill_dir(project, 'feature')
        write(self.layer_paths['org'] / 'skills/feature/references/org.md', 'org v2\n')
        self.link_ok(project, mode='copy')
        self.assertEqual((skill / 'references/org.md').read_text(), 'org v2\n')
        (skill / 'references/core.md').write_text('local edit\n')
        write(self.layer_paths['org'] / 'skills/feature/references/org.md', 'org v3\n')
        self.link_ok(project, mode='copy')
        self.assertEqual((skill / 'references/core.md').read_text(), 'local edit\n')
        self.assertEqual((self.layer_paths['core'] / 'skills/feature/references/core.md').read_text(),
                         'core reference\n')


class CompositeHashTests(CompositeCase):
    def test_hash_is_the_sha256_of_the_sorted_rows(self):
        project = self.new_project('hash', skills=SKILLS)
        self.link_ok(project)
        files = {rel: (layer_id, path) for rel, (layer_id, path) in self.sources().items()}
        self.assertEqual(self.composite_of(project).name, expected_hash(files))

    def test_hash_is_deterministic_across_projects_and_independent_of_file_content(self):
        first, second = self.new_project('det-a'), self.new_project('det-b')
        self.link_ok(first)
        name = self.composite_of(first).name
        self.link_ok(second)
        self.assertEqual(self.composite_of(second).name, name)
        self.assertEqual(self.composites('feature'), [name])
        write(self.layer_paths['core'] / 'skills/feature/SKILL.md', SKILL_TEXT.format('feature') + 'edited\n')
        self.link_ok(first)
        self.assertEqual(self.composite_of(first).name, name, 'content is read through the links, it is not hashed')
        self.assertIn('edited', (self.skill_dir(first, 'feature') / 'SKILL.md').read_text())

    def test_hash_changes_with_the_set_of_files_and_with_the_layer_set(self):
        first, second = self.new_project('set-a'), self.new_project('set-b')
        self.set_rules(('**/set-a', ['core', 'org']), ('**/set-b', ['core', 'org', 'personal']))
        self.link_ok(first)
        self.link_ok(second)
        names = {self.composite_of(first).name, self.composite_of(second).name}
        self.assertEqual(len(names), 2, 'the owner of references/shared.md differs, so does the hash')
        before = self.composite_of(first).name
        write(self.layer_paths['org'] / 'skills/feature/references/extra.md', 'extra\n')
        self.link_ok(first)
        self.assertNotEqual(self.composite_of(first).name, before)
        self.assertTrue((self.skill_dir(first, 'feature') / 'references/extra.md').is_file())


class CompositeDryRunTests(CompositeCase):
    @each_mode
    def test_dry_run_writes_neither_the_project_skills_nor_the_cache(self, mode):
        project = self.new_project(f'dry-{mode}', skills=SKILLS)
        before = tree_snapshot(composed_root())
        process = self.link_ok(project, '--dry-run', mode=mode)
        self.assertEqual(tree_snapshot(composed_root()), before)
        self.assertEqual(self.composites('feature'), [])
        self.assertEqual(list((project / '.github' / 'skills').glob('*')) if (project / '.github' / 'skills').exists() else [], [])
        self.assertNotIn('Traceback', process.stderr)


class CompositeHashRealpathTests(CompositeCase):
    @needs_os_symlinks
    def test_hash_uses_the_realpath_of_sources_so_an_alias_path_of_a_layer_changes_nothing(self):
        first, second = self.new_project('real-a'), self.new_project('real-b')
        self.link_ok(first)
        name = self.composite_of(first).name
        alias = self.work / 'alias-of-org'
        os.symlink(self.layer_paths['org'], alias)
        entries = [('core', 'core', self.layer_paths['core']), ('org', 'org', alias),
                   ('personal', 'personal', self.layer_paths['personal'])]
        self.write_config(layers=entries, rules=[(['**'], ['core', 'org', 'personal'])])
        self.link_ok(second)
        composite = self.composite_of(second)
        self.assertEqual(composite.name, name, 'the same files reached through an alias give the same hash')
        self.assertEqual(os.readlink(composite / 'references/org.md'), str(alias / 'skills/feature/references/org.md'))
        self.assertEqual(self.composites('feature'), [name])


class CompositeRebuildTests(CompositeCase):
    @needs_os_symlinks
    def test_link_rebuilds_a_deleted_or_damaged_composite(self):
        project = self.new_project('rebuild')
        self.link_ok(project)
        composite = self.composite_of(project)
        entries = {rel: os.readlink(composite / rel) for rel in ('SKILL.md', 'references/org.md')}
        # a deleted entry, an entry pointing at the wrong file
        (composite / 'references/org.md').unlink()
        (composite / 'SKILL.md').unlink()
        os.symlink(self.layer_paths['core'] / 'skills/feature/references/core.md', composite / 'SKILL.md')
        self.link_ok(project)
        for rel, target in entries.items():
            self.assertEqual(os.readlink(composite / rel), target, rel)
        self.assertIn('feature body', (composite / 'SKILL.md').read_text())
        # the whole composite directory removed
        shutil.rmtree(composite)
        self.link_ok(project)
        self.assertEqual(self.composite_of(project), composite)
        self.assertEqual((self.skill_dir(project, 'feature') / 'references/org.md').read_text(), 'org reference\n')

    @each_mode
    def test_second_link_is_idempotent_in_both_modes(self, mode):
        project = self.new_project(f'idem-{mode}', skills=SKILLS)
        self.link_ok(project, mode=mode)
        # copilot-instructions.md is rewritten with equal content on every link (baseline behaviour): no mtimes there
        project_before = tree_snapshot(project, mtimes=False)
        skills_before = tree_snapshot(project / '.github' / 'skills')
        cache_before = tree_snapshot(composed_root())
        second = self.link_ok(project, mode=mode)
        self.assertEqual(second.stdout, '', second.stdout)
        self.assertEqual(tree_snapshot(project, mtimes=False), project_before)
        self.assertEqual(tree_snapshot(project / '.github' / 'skills'), skills_before)
        self.assertEqual(tree_snapshot(composed_root()), cache_before)


class CompositeCollectionTests(CompositeCase):
    @each_mode
    def test_unused_hash_is_removed_but_never_the_hash_of_another_project(self, mode):
        first, second = self.new_project(f'a-{mode}'), self.new_project(f'b-{mode}')
        self.set_rules((f'**/a-{mode}', ['core', 'org']), (f'**/b-{mode}', ['core', 'org', 'personal']))
        cache_before = tree_snapshot(composed_root())
        self.link_ok(first, mode=mode)
        self.link_ok(second, mode=mode)
        if mode == 'copy':
            self.assertEqual(tree_snapshot(composed_root()), cache_before)
            return
        h_first, h_second = self.composite_of(first).name, self.composite_of(second).name
        self.assertNotEqual(h_first, h_second)
        self.assertEqual(self.composites('feature'), sorted([h_first, h_second]))
        registry = json.loads((composed_root() / '.projects.json').read_text())
        self.assertEqual(sorted(registry), sorted([str(first.resolve()), str(second.resolve())]))
        # relinking one project must keep the composite of the other one
        self.link_ok(first, mode=mode)
        self.assertEqual(self.composites('feature'), sorted([h_first, h_second]))
        # the org layer gets a file: both composites are stale, only the relinked project moves on
        write(self.layer_paths['org'] / 'skills/feature/references/more.md', 'more\n')
        self.link_ok(first, mode=mode)
        new_first = self.composite_of(first).name
        self.assertNotIn(h_first, self.composites('feature'), 'the unused hash of the relinked project goes')
        self.assertIn(h_second, self.composites('feature'), 'the hash of the other project stays')
        self.assertIn(new_first, self.composites('feature'))
        self.link_ok(second, mode=mode)
        self.assertNotIn(h_second, self.composites('feature'))
        self.assertEqual(self.composites('feature'), sorted([new_first, self.composite_of(second).name]))

    def test_hash_of_a_project_that_no_longer_exists_and_unknown_hashes_are_collected(self):
        gone, kept = self.new_project('gone'), self.new_project('kept')
        self.set_rules(('**/gone', ['core', 'org']), ('**/kept', ['core', 'org', 'personal']))
        self.link_ok(gone)
        self.link_ok(kept)
        h_gone = self.composite_of(gone).name
        (composed_root() / 'feature' / 'deadbeef').mkdir()
        shutil.rmtree(gone)
        self.link_ok(kept)
        self.assertEqual(self.composites('feature'), [self.composite_of(kept).name])
        self.assertNotIn(h_gone, self.composites('feature'))

    def test_unlisting_a_skill_removes_its_composite_hash(self):
        project = self.new_project('unlist')
        self.link_ok(project)
        self.assertEqual(len(self.composites('feature')), 1)
        manifest = project / '.github' / '.skillsync.yaml'
        manifest.write_text(manifest.read_text().replace('feature', 'solo'), encoding='utf-8')
        self.link_ok(project)
        self.assertEqual(self.composites('feature'), [])


class CompositeOrphanTests(CompositeCase):
    def orphans_of(self, composite):
        return composite.with_name(composite.name + '.orphans')

    def atomic_save(self, composite, rel, text):
        temporary = composite / Path(rel).parent / f'.{Path(rel).name}.swp'
        temporary.write_text(text)
        os.replace(temporary, composite / rel)  # the editor's atomic save replaces the link
        self.assertFalse((composite / rel).is_symlink())

    def test_atomic_editor_save_over_a_link_is_adopted_by_the_writable_owner(self):
        """K4 (2.2b): a regular file where the composite has a link is a modification of the owner's file;
        the org owner is writable and leakcheck is clean, so `link` adopts it (2.2a kept it in `.orphans`)."""
        project = self.new_project('atomic')
        self.link_ok(project)
        composite = self.composite_of(project)
        org_file = self.layer_paths['org'] / 'skills/feature/references/org.md'
        self.atomic_save(composite, 'references/org.md', 'edited in the editor\n')
        result = self.link_ok(project)
        self.assertIn('Adopted', result.stdout)
        self.assertEqual(org_file.read_text(), 'edited in the editor\n')
        self.assertTrue((composite / 'references/org.md').is_symlink(), 'the composite entry is a link again')
        self.assertEqual(os.readlink(composite / 'references/org.md'), str(org_file))
        self.assertFalse((self.orphans_of(composite) / 'references/org.md').exists())

    def test_atomic_editor_save_over_a_core_file_is_never_overwritten_but_kept_as_an_orphan(self):
        """K4: the core owner takes no write without `--to-layer core`, so the rebuild moves the file to
        `.orphans` and never overwrites the owner's file."""
        project = self.new_project('atomic-core')
        self.link_ok(project)
        composite = self.composite_of(project)
        core_file = self.layer_paths['core'] / 'skills/feature/references/core.md'
        self.atomic_save(composite, 'references/core.md', 'edited in the editor\n')
        result = self.link_ok(project)
        self.assertIn('[warn]', result.stdout)
        self.assertEqual((self.orphans_of(composite) / 'references/core.md').read_text(), 'edited in the editor\n')
        self.assertTrue((composite / 'references/core.md').is_symlink(), 'the composite is rebuilt')
        self.assertEqual(core_file.read_text(), 'core reference\n', 'the owning layer file is not overwritten')

    def test_a_new_file_of_a_skill_whose_skill_md_is_core_goes_to_orphans(self):
        """K4: a new file goes to the last provider of `SKILL.md` (core here) - not writable without consent."""
        project = self.new_project('newfile')
        self.link_ok(project)
        composite = self.composite_of(project)
        write(composite / 'references/brand-new.md', 'new\n')
        self.link_ok(project)
        self.assertEqual((self.orphans_of(composite) / 'references/brand-new.md').read_text(), 'new\n')
        self.assertFalse((composite / 'references/brand-new.md').exists())
        for layer in ('core', 'org', 'personal'):
            self.assertFalse((self.layer_paths[layer] / 'skills/feature/references/brand-new.md').exists(), layer)

    def test_orphans_survive_collection_and_later_links(self):
        project = self.new_project('survive')
        self.link_ok(project)
        old = self.composite_of(project)
        write(old / 'references/draft.md', 'draft\n')
        write(self.layer_paths['org'] / 'skills/feature/references/extra.md', 'extra\n')  # new hash
        self.link_ok(project)
        self.assertNotEqual(self.composite_of(project), old)
        self.assertFalse(old.exists(), 'the unused hash is gone')
        self.assertEqual((self.orphans_of(old) / 'references/draft.md').read_text(), 'draft\n')
        for _ in range(3):
            self.link_ok(project)
        self.assertEqual((self.orphans_of(old) / 'references/draft.md').read_text(), 'draft\n')

    def test_copy_mode_has_no_composite_and_no_orphans(self):
        project = self.new_project('copy-orphans')
        self.link_ok(project, mode='copy')
        skill = self.skill_dir(project, 'feature')
        (skill / 'references/org.md').write_text('edited\n')
        self.link_ok(project, mode='copy')
        self.assertEqual((skill / 'references/org.md').read_text(), 'edited\n')
        self.assertFalse(composed_root().exists() and any(composed_root().rglob('*.orphans')))


class LayerLinksAreNeverReadTests(CompositeCase):
    """D73: a link inside a layer is not followed by the composite or by copy mode."""

    SECRET = 'SECRET-OUTSIDE-THE-LAYER'

    def plant_links(self):
        require_os_symlinks(self)
        outside = self.work / 'outside'
        write(outside / 'secret.md', self.SECRET + '\n')
        write(outside / 'dir' / 'inner.md', self.SECRET + ' inner\n')
        for layer_id, skill in (('org', 'feature'), ('core', 'solo')):
            base = self.layer_paths[layer_id] / 'skills' / skill
            if not (base / 'linked-file.md').is_symlink():
                os.symlink(outside / 'secret.md', base / 'linked-file.md')
                os.symlink(outside / 'dir', base / 'linked-dir')

    @each_mode
    def test_composite_and_copy_mode_skip_links_of_the_layers(self, mode):
        self.plant_links()
        project = self.new_project(f'links-{mode}', skills=SKILLS)
        result = self.link_ok(project, mode=mode)
        for skill in SKILLS:
            if mode == 'symlink' and skill == 'solo':
                continue  # one provider: the host reads the layer directory itself (known residual risk)
            directory = self.skill_dir(project, skill)
            names = sorted(path.relative_to(directory).as_posix() for path in directory.rglob('*'))
            self.assertNotIn('linked-file.md', names, (mode, skill))
            self.assertFalse([name for name in names if name.startswith('linked-dir')], (mode, skill))
            for path in directory.rglob('*'):
                if path.is_file():
                    self.assertNotIn(self.SECRET, path.read_text(), (mode, skill, path))
        self.assertNotIn('Traceback', result.stderr)
        for path in composed_root().rglob('*') if composed_root().exists() else ():
            self.assertNotIn('linked', path.name)


class LinkBlockedByLayerGateTests(ComposeCase):
    """K12 for `link`: BLOCKED (exit 2), artefacts untouched."""

    def setUp(self):
        super().setUp()
        self.add_layers(core=CORE_FILES, org=ORG_FILES)

    def rewrite_org(self, **kwargs):
        make_layer(self.layer_paths['org'], 'org', 'org', **kwargs)

    def assert_blocked_without_changes(self, project, mode, needle=None):
        before = tree_snapshot(project)
        cache = tree_snapshot(composed_root())
        process = self.link(project, mode=mode)
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertIn('[blocked]', process.stdout)
        if needle:
            self.assertIn(needle, process.stdout)
        self.assertNotIn('Traceback', process.stderr)
        self.assertEqual(tree_snapshot(project), before)
        self.assertEqual(tree_snapshot(composed_root()), cache)

    def test_each_blocking_layer_defect_refuses_link_on_a_fresh_and_a_linked_project(self):
        newer = '99.0.0'
        yaml_broken = lambda: write(self.layer_paths['org'] / 'layer.yaml', 'schema: [unclosed\n')  # noqa: E731
        defects = {
            'core-min-above-the-generator': (lambda: self.rewrite_org(core_min=newer), newer),
            'version-missing': (lambda: self.rewrite_org(version=None), 'version'),
            'core-min-missing': (lambda: self.rewrite_org(core_min=False), 'core_min'),
            'requires-unmet': (lambda: self.rewrite_org(requires=['absent-layer']), 'absent-layer'),
            'layer-yaml-invalid': (yaml_broken, None),
        }
        self.assertLess(generator_version(), newer)
        for mode in LINK_MODES:
            for name, (apply, needle) in defects.items():
                with self.subTest(defect=name, mode=mode):
                    make_layer(self.layer_paths['org'], 'org', 'org')  # healthy
                    fresh = self.new_project(f'fresh-{name}-{mode}', skills=SKILLS)
                    linked = self.new_project(f'linked-{name}-{mode}', skills=SKILLS)
                    self.link_ok(linked, mode=mode)
                    write(self.layer_paths['core'] / 'skills/feature/references/core.md', f'changed {name}\n')
                    apply()
                    self.assert_blocked_without_changes(fresh, mode, needle)
                    self.assertFalse((fresh / '.github' / 'skills').exists())
                    self.assert_blocked_without_changes(linked, mode, needle)
                    write(self.layer_paths['core'] / 'skills/feature/references/core.md', 'core reference\n')

    def test_healthy_layers_link_after_the_defect_is_fixed(self):
        project = self.new_project('fixed', skills=SKILLS)
        self.rewrite_org(core_min='99.0.0')
        self.assert_blocked_without_changes(project, 'symlink')
        self.rewrite_org()
        self.link_ok(project)
        self.assertTrue((self.skill_dir(project, 'feature') / 'references/org.md').is_file())

    def test_a_blocked_project_does_not_stop_the_others(self):
        good, bad = self.new_project('good', skills=SKILLS), self.new_project('bad', skills=SKILLS)
        self.write_config(
            layers=[('core', 'core', self.layer_paths['core']), ('org', 'org', self.layer_paths['org'])],
            rules=[([str(good)], ['core']), ([str(bad)], ['core', 'org'])],
            discover_paths=[str(self.work / 'projects')])
        self.rewrite_org(core_min='99.0.0')
        process = self.cli('link')
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertTrue(self.skill_dir(good, 'feature').exists(), 'the project not using the broken layer is linked')
        self.assertFalse(self.skill_dir(bad, 'feature').exists())
        self.assertIn(str(bad), process.stdout)

    @each_mode
    def test_config_with_only_the_legacy_library_key_is_blocked_with_instructions(self, mode):
        project = self.new_project(f'legacy-{mode}', skills=SKILLS)
        self.config_file.write_text(f'library: {self.layer_paths["core"]}\n', encoding='utf-8')
        self.assert_blocked_without_changes(project, mode, 'layers migrate')
        self.assertFalse((project / '.github' / 'skills').exists())

    def test_missing_config_is_blocked_too(self):
        project = self.new_project('noconfig', skills=SKILLS)
        self.config_file.unlink()
        process = self.link(project)
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertNotIn('Traceback', process.stderr)
        self.assertFalse((project / '.github' / 'skills').exists())


class LegacyBridgeIsBlockedTests(ComposeCase):
    """K15: a `synthetic` bridge entry (written by 0.9.x) is no longer linked: BLOCKED, nothing written."""

    @each_mode
    def test_link_on_a_bridge_config_is_blocked_and_leaves_everything_unchanged(self, mode):
        bridge = self.work / f'old-library-{mode}'
        for rel, text in CORE_FILES.items():
            write(bridge / rel, text)
        org = self.layer_dir(f'org-{mode}', 'org', ORG_FILES)
        make_layer(org, 'org', 'org')
        self.write_config(
            layers=[('core', 'core', bridge, {'synthetic': {'version': '1.0.0', 'core_min': generator_version()}}),
                    ('org', 'org', org)],
            rules=[(['**'], ['core', 'org'])])
        before, org_before = tree_snapshot(bridge), tree_snapshot(org)
        project = self.new_project(f'old-style-{mode}', skills=SKILLS)
        project_before = tree_snapshot(project)
        process = self.link(project, mode=mode)
        output = process.stdout + process.stderr
        self.assertNotIn('Traceback', process.stderr)
        self.assertEqual(process.returncode, 2, output)
        self.assertIn('legacy bridge', output)
        self.assertIn('layers migrate --layer-source core=PATH --apply', output)
        self.assertFalse((project / '.github' / 'skills').exists(), 'no project skills created')
        self.assertEqual(tree_snapshot(project), project_before)
        self.assertEqual(tree_snapshot(bridge), before)
        self.assertEqual(tree_snapshot(org), org_before)
        self.assertFalse((bridge / 'layer.yaml').exists())


class LayerSelectionTests(ComposeCase):
    """Layers of `link` come from the machine config rules or from `--layer`."""

    def setUp(self):
        super().setUp()
        self.add_layers(core=CORE_FILES, org=ORG_FILES, rules=False)

    @each_mode
    def test_no_matching_rule_is_blocked_with_the_layers_use_instruction(self, mode):
        project = self.new_project(f'norule-{mode}', skills=SKILLS)
        process = self.link(project, mode=mode)
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertIn('layers use', process.stdout)
        self.assertFalse((project / '.github' / 'skills').exists())
        self.assertFalse((project / '.github' / 'copilot-instructions.md').exists())

    def test_rule_for_other_paths_does_not_select_layers(self):
        self.write_config(layers=[('core', 'core', self.layer_paths['core'])],
                          rules=[([str(self.work / 'elsewhere')], ['core'])])
        project = self.new_project('unmatched', skills=SKILLS)
        process = self.link(project)
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertIn('layers use', process.stdout)

    @each_mode
    def test_layer_flag_replaces_the_configured_layers(self, mode):
        self.write_config(layers=[('core', 'core', self.layer_paths['core'])],
                          rules=[(['**'], ['core'])])
        plain = self.new_project(f'plain-{mode}', skills=SKILLS)
        self.link_ok(plain, mode=mode)
        self.assertFalse((self.skill_dir(plain, 'feature') / 'references/org.md').exists())
        flagged = self.new_project(f'flagged-{mode}', skills=SKILLS)
        self.link_ok(flagged, f'--layer=core={self.layer_paths["core"]}', f'--layer=org={self.layer_paths["org"]}',
                     mode=mode)
        self.assertEqual((self.skill_dir(flagged, 'feature') / 'references/org.md').read_text(), 'org reference\n')

    @each_mode
    def test_layer_flag_needs_no_rule(self, mode):
        project = self.new_project(f'flag-only-{mode}', skills=SKILLS)
        self.link_ok(project, f'--layer=core={self.layer_paths["core"]}', mode=mode)
        self.assertTrue((self.skill_dir(project, 'feature') / 'SKILL.md').is_file())

    def test_layer_flag_with_a_directory_that_is_not_a_layer_is_blocked(self):
        project = self.new_project('flag-bad', skills=SKILLS)
        bare = self.work / 'not-a-layer'
        bare.mkdir()
        process = self.link(project, f'--layer=core={bare}')
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertFalse((project / '.github' / 'skills').exists())


# ---------------------------------------------------------------------------------------------------------
# After code review 1 of 2.2a (M1-M4c, minors 7-8)
# ---------------------------------------------------------------------------------------------------------

def load_generator():
    """A fresh copy of the generator CLI module (`skillsync_workflow.cli`) (for in-process calls such as `build_composite`)."""
    from cli_module import fresh_cli
    return fresh_cli()


class ReviewCase(CompositeCase):
    def layerset(self):
        from skillsync_workflow import layers
        return layers.resolve_layers(None, config=layers.load_machine_config(), cwd=self.work)


class ConcurrentCompositeTests(ReviewCase):
    """M1: two `link` runs sharing a composite and its registry must not damage each other."""

    def setUp(self):
        super().setUp()
        self.generator = load_generator()
        self.addCleanup(self.generator.release_composed_lock)  # in-process build_composite takes the cache lock

    def race_symlink(self, fail_with):
        """os.symlink that behaves as if another process created the same link a moment earlier."""
        require_os_symlinks(self)
        real = os.symlink
        state = {'raised': False}

        def symlink(target, link, *args, **kwargs):
            if not state['raised'] and Path(link).parent.parent.parent.name == 'feature':
                state['raised'] = True
                real(target, link, *args, **kwargs)  # the "other process" wins the race
                raise fail_with
            return real(target, link, *args, **kwargs)
        return mock.patch('os.symlink', symlink)

    def test_file_exists_with_the_right_target_is_success_not_a_fallback(self):
        files = self.layerset().skill_files('feature')
        with self.race_symlink(FileExistsError(errno.EEXIST, 'File exists')):
            directory = self.generator.build_composite('feature', files)
        self.assertIsNotNone(directory, 'the link exists with the expected target: nothing to fall back from')
        for rel, (_layer, path) in files.items():
            self.assertEqual(os.readlink(directory / rel), str(path), rel)

    def test_fallback_without_symlinks_leaves_no_empty_hash_directory(self):
        files = self.layerset().skill_files('feature')
        with mock.patch('os.symlink', side_effect=OSError(errno.EPERM, 'no symlinks')), \
                mock.patch.object(self.generator, '_composite_warned', False):
            self.assertIsNone(self.generator.build_composite('feature', files))
        self.assertEqual(self.composites('feature'), [])
        self.assertFalse((composed_root() / 'feature').exists() and any((composed_root() / 'feature').iterdir()))

    def test_fallback_does_not_remove_the_valid_links_of_a_shared_composite(self):
        files = self.layerset().skill_files('feature')
        directory = self.generator.build_composite('feature', files)
        victim = directory / 'references' / 'org.md'
        victim.unlink()  # another project's composite is being (re)built: one link is missing
        kept = {rel: os.readlink(directory / rel) for rel in files if rel != 'references/org.md'}
        with mock.patch('os.symlink', side_effect=OSError(errno.EPERM, 'no symlinks')), \
                mock.patch.object(self.generator, '_composite_warned', False):
            self.assertIsNone(self.generator.build_composite('feature', files))
        self.assertEqual({rel: os.readlink(directory / rel) for rel in kept}, kept,
                         'the fallback must not tear down links other projects use')

    def test_two_processes_linking_projects_that_share_a_composite(self):
        for round_number in range(4):
            if composed_root().exists():
                shutil.rmtree(composed_root())
            projects = [self.new_project(f'race-{round_number}-{i}', skills=SKILLS) for i in range(4)]
            env = dict(os.environ)
            env.pop('SKILLSYNC_NO_SYMLINK', None)
            running = [subprocess.Popen([sys.executable, str(ROOT / 'skillsync'), 'link', str(project)],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                                        cwd=str(self.work)) for project in projects]
            outputs = [(process.communicate(timeout=300), process.returncode) for process in running]
            for (out, err), code in outputs:
                self.assertEqual(code, 0, out + err)
                self.assertNotIn('Traceback', err)
                self.assertNotIn('falling back to copies', out)
            registry = json.loads((composed_root() / '.projects.json').read_text())
            self.assertEqual(sorted(registry), sorted(str(p.resolve()) for p in projects), 'no registry entry lost')
            self.assertEqual(len(self.composites('feature')), 1)
            for project in projects:
                skill = self.skill_dir(project, 'feature')
                self.assertTrue(skill.is_symlink(), f'{project.name} degraded to a copy')
                self.assertEqual((skill / 'references/org.md').read_text(), 'org reference\n')


@unittest.skipUnless(fcntl is not None, 'fcntl.flock is unavailable on this host (Windows uses msvcrt, NOT_RUN)')
class CacheLockTests(ReviewCase):
    """N1/N3: another process holds the composite cache lock; `link` must give up cleanly, project by project."""

    def setUp(self):
        super().setUp()
        self.generator = load_generator()
        self.addCleanup(self.generator.release_composed_lock)
        self.write_config(layers=[(i, i, self.layer_paths[i]) for i in ('core', 'org', 'personal')],
                          rules=[(['**'], ['core', 'org', 'personal'])], discover_paths=[str(self.work / 'projects')])

    @contextlib.contextmanager
    def foreign_lock(self):
        """This test process plays the other skillsync process holding `<composed_root>/.lock`."""
        composed_root().mkdir(parents=True, exist_ok=True)
        with open(composed_root() / '.lock', 'a+b') as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield handle
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def lock_is_held_elsewhere(self):
        composed_root().mkdir(parents=True, exist_ok=True)
        with open(composed_root() / '.lock', 'a+b') as probe:
            try:
                fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
            return False

    def run_command(self, *args, mode='symlink'):
        env = {'SKILLSYNC_LOCK_TIMEOUT': '0.3'}
        with link_mode(mode):
            return self.cli(*args, env=env)

    def projects(self):
        # discovery order is by path: p1 (needs a composite: feature has several providers) comes before p2
        return (self.new_project('p1-composite', skills=SKILLS, claude=True),
                self.new_project('p2-plain', skills=('solo',)))

    def test_link_gives_up_on_the_project_that_needs_the_cache_and_goes_on(self):
        p1, p2 = self.projects()
        with self.foreign_lock():
            before = (tree_snapshot(p1), tree_snapshot(composed_root()))
            result = self.run_command('link')
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertNotIn('Traceback', result.stderr)
            self.assertRegex(result.stdout, r'(?m)^\[blocked\] .*p1-composite.*locked')
            self.assertEqual((tree_snapshot(p1), tree_snapshot(composed_root())), before,
                             'a lock timeout changes neither the project nor the cache')
            self.assertTrue((self.skill_dir(p2, 'solo')).exists(), 'a project without a composite does not need the lock')
            self.assertNotIn('p2-plain', ''.join(line for line in result.stdout.splitlines() if '[blocked]' in line))
        again = self.run_command('link')
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertTrue(self.skill_dir(p1, 'feature').is_symlink())
        self.assertEqual(len(self.composites('feature')), 1)
        self.assertIn(str(p1.resolve()), json.loads((composed_root() / '.projects.json').read_text()))

    def test_a_timeout_on_an_already_linked_project_leaves_every_artefact_as_it_was(self):
        p1, _p2 = self.projects()
        self.assertEqual(self.run_command('link').returncode, 0)
        write(self.layer_paths['org'] / 'skills/feature/references/new.md', 'new\n')  # a link would need a new <hash>
        with self.foreign_lock():
            before = (tree_snapshot(p1), tree_snapshot(composed_root()))
            result = self.run_command('link', str(p1))
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn('locked', result.stdout)
            self.assertEqual((tree_snapshot(p1), tree_snapshot(composed_root())), before)
        self.assertEqual(self.run_command('link', str(p1)).returncode, 0)
        self.assertTrue((self.skill_dir(p1, 'feature') / 'references/new.md').is_file())

    def test_instructions_command_obeys_the_same_lock(self):
        p1, p2 = self.projects()
        with self.foreign_lock():
            before = tree_snapshot(p1)
            result = self.run_command('instructions')
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertRegex(result.stdout, r'(?m)^\[blocked\] .*p1-composite.*locked')
            self.assertEqual(tree_snapshot(p1), before)
            self.assertTrue((p2 / '.github' / 'copilot-instructions.md').exists())
        self.assertEqual(self.run_command('instructions').returncode, 0)
        self.assertTrue((p1 / '.github' / 'copilot-instructions.md').exists())

    def test_copy_mode_never_waits_for_the_cache_lock(self):
        p1, _p2 = self.projects()
        with self.foreign_lock():
            result = self.run_command('link', mode='copy')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.skill_dir(p1, 'feature').is_symlink())

    @each_mode
    def test_lock_is_held_from_building_a_composite_until_the_registry_and_collection_are_done(self, mode):
        """The fresh <hash> must stay protected until the project is recorded; otherwise another process's
        collection (which only knows recorded projects) deletes it."""
        files = self.layerset().skill_files('feature')
        if mode == 'copy':
            shutil.rmtree(composed_root(), ignore_errors=True)  # a fresh machine: the symlink run left a registry
        if mode == 'symlink':
            self.generator.build_composite('feature', files)
            self.assertTrue(self.lock_is_held_elsewhere(), 'released right after building the composite')
            self.generator.release_composed_lock()
        self.assertFalse(self.lock_is_held_elsewhere())

        p1, _p2 = self.projects()
        p1 = self.new_project(f'p1-probe-{mode}', skills=SKILLS)
        seen, placed = [], []
        real = self.generator._update_registry
        real_place = self.generator.place

        def probe(*args, **kwargs):
            seen.append(self.lock_is_held_elsewhere())  # registry write and collection run under the lock
            return real(*args, **kwargs)

        def place_probe(src, dest, *args, **kwargs):
            if getattr(src, 'link', None) is not None and composed_root() in Path(src.link).parents:
                placed.append(self.lock_is_held_elsewhere())  # between the build and the registry write
            return real_place(src, dest, *args, **kwargs)
        manifest = p1 / '.github' / '.skillsync.yaml'
        with link_mode(mode), mock.patch.object(self.generator, '_update_registry', probe), \
                mock.patch.object(self.generator, 'place', place_probe):
            self.generator.link_project(manifest, self.generator.load_config())
        if mode == 'symlink':
            self.assertEqual(seen, [True])
            self.assertTrue(placed and all(placed), 'the fresh <hash> was unprotected before the project was recorded')
        else:  # copy mode never touches the cache: no registry, no composite, no lock
            self.assertEqual((seen, placed), ([], []))
            self.assertFalse((composed_root() / '.projects.json').exists())
        self.assertFalse(self.lock_is_held_elsewhere(), 'the lock is released when the link is done')


class UnsafeNamesDoNotStopTheRunTests(ReviewCase):
    """N2: a bad skill name blocks that project only; wildcards skip invalid directory names in both expanders."""

    def test_project_with_an_unsafe_name_is_blocked_and_the_next_one_is_linked(self):
        for mode in LINK_MODES:
            with self.subTest(mode=mode):
                self.write_config(layers=[(i, i, self.layer_paths[i]) for i in ('core', 'org', 'personal')],
                                  rules=[(['**'], ['core', 'org', 'personal'])],
                                  discover_paths=[str(self.work / 'projects')])
                bad = self.new_project(f'a-bad-{mode}', skills=('feature', '../x'))
                good = self.new_project(f'b-good-{mode}', skills=SKILLS)
                before = tree_snapshot(bad, mtimes=False)
                process = self.link_all(mode)
                self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
                self.assertNotIn('Traceback', process.stderr)
                self.assertRegex(process.stdout, rf'(?m)^\[blocked\] .*a-bad-{mode}.*Unsafe skill name')
                self.assertEqual(tree_snapshot(bad, mtimes=False), before)
                self.assertTrue((self.skill_dir(good, 'solo')).exists(), 'the next project is still linked')
                shutil.rmtree(bad)
                shutil.rmtree(good)

    def link_all(self, mode):
        with link_mode(mode):
            return self.cli('link')

    def test_wildcards_give_the_same_valid_set_in_both_implementations(self):
        from skillsync_workflow import common
        generator = load_generator()
        write(self.layer_paths['core'] / 'skills/.drafts/notes.md', 'draft without SKILL.md\n')
        write(self.layer_paths['org'] / 'skills/bad name/SKILL.md', SKILL_TEXT.format('bad name'))
        write(self.layer_paths['personal'] / 'skills/glob-ok/SKILL.md', SKILL_TEXT.format('glob-ok'))
        layerset = self.layerset()
        for spec in ('*', '?*', 'glob-*', '*raft*', 'bad*'):
            with self.subTest(spec=spec):
                script = generator.expand_skills([spec], layerset, {})
                package = common.expand_project_skills({'skills': [spec]}, layerset)
                self.assertEqual(script, package)
                self.assertNotIn('.drafts', script)
                self.assertNotIn('bad name', script)
        self.assertIn('glob-ok', generator.expand_skills(['*'], layerset, {}))
        self.assertIn('feature', generator.expand_skills(['*'], layerset, {}))


class UnsafeSkillNameTests(ReviewCase):
    """M2: skill names that climb out of `skills/`."""

    NAMES = ('../..', '..', '/etc/passwd', 'a/../../b', '../x', 'a/b', '.hidden/..')

    def test_both_expanders_reject_them_with_the_same_message(self):
        from skillsync_workflow import common
        generator = load_generator()
        layerset = self.layerset()
        for name in self.NAMES:
            for spec in (name, '@bad'):
                tags = {'bad': [name]}
                with self.subTest(name=name, via=spec):
                    with self.assertRaises(ValueError) as script:
                        generator.expand_skills([spec], layerset, tags)
                    with mock.patch.object(common, 'read_yaml', return_value={'tags': tags}):
                        with self.assertRaises(ValueError) as package:
                            common.expand_project_skills({'skills': [spec]}, layerset)
                    self.assertEqual(str(script.exception), str(package.exception))
                    self.assertIn('Unsafe skill name', str(package.exception))

    def test_layer_readers_reject_parent_segments(self):
        from skillsync_workflow import layers
        layerset = self.layerset()
        for name in ('..', '../..', 'a/../../b'):
            with self.subTest(name=name):
                with self.assertRaises(layers.LayerError):
                    layerset.skill_files(name)
                with self.assertRaises(layers.LayerError):
                    layers.layer_files(layerset.get('core'), f'skills/{name}')
        for name in ('/etc/passwd', 'a/b', '.hidden', ''):  # not a single skill-name segment
            with self.subTest(name=name):
                with self.assertRaises(layers.LayerError):
                    layerset.skill_files(name)
        self.assertTrue(layerset.skill_files('feature'), 'ordinary names still work')

    def test_link_with_such_a_name_writes_nothing_anywhere(self):
        for name in ('../..', 'a/../../b'):
            for mode in LINK_MODES:
                with self.subTest(name=name, mode=mode):
                    project = self.new_project(f'unsafe-{mode}-{abs(hash(name)) % 1000}', skills=('feature', name))
                    before = tree_snapshot(self.root, mtimes=False)
                    process = self.link(project, mode=mode)
                    self.assertNotEqual(process.returncode, 0, process.stdout + process.stderr)
                    self.assertIn('Unsafe skill name', process.stdout + process.stderr)
                    self.assertEqual(tree_snapshot(self.root, mtimes=False), before,
                                     'nothing may appear in the project, the cache or next to the layers')


class LegacyLoaderRemovedTests(ComposeCase):
    """5.1: the old single-library loader is gone; K1 no longer allowlists `prototype*` or `common.py:load_project`."""

    def test_common_has_no_load_project(self):
        from skillsync_workflow import common
        self.assertFalse(hasattr(common, 'load_project'))
        self.assertFalse(hasattr(common, '_legacy_library_fallback'))
        self.assertTrue(callable(common.load_layer_project), 'the layer loader stays')

    def test_k1_grep_passes_without_a_prototype_allowlist(self):
        grep = Path(__file__).resolve().parent / 'k1_grep.py'
        process = subprocess.run([sys.executable, str(grep)], capture_output=True, text=True, timeout=60)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        spec = importlib.util.spec_from_file_location('k1_grep_allowlist', grep)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for entry in module.ALLOWLIST:
            self.assertTrue(entry.startswith('skillsync_workflow/layers.py:'), entry)


class HostPruneTests(ComposeCase):
    """M4b: managed symlinks (layer, composite, legacy_library) are pruned in `.claude/skills` and `.agents/skills`."""

    def setUp(self):
        super().setUp()
        self.old = self.work / 'old-lib'
        write(self.old / 'skills/oldskill/SKILL.md', SKILL_TEXT.format('oldskill'))
        self.outside = self.work / 'outside-skill'
        write(self.outside / 'SKILL.md', SKILL_TEXT.format('foreign'))
        files = dict(CORE_FILES)
        files['skills/other/SKILL.md'] = SKILL_TEXT.format('other')
        files['skills/extra/SKILL.md'] = SKILL_TEXT.format('extra')
        self.add_layers(core=files, org={**ORG_FILES, 'skills/extra/references/org.md': 'extra org\n'})

    def set_manifest(self, project, skills):
        manifest = project / '.github' / '.skillsync.yaml'
        text = manifest.read_text(encoding='utf-8')
        import re
        manifest.write_text(re.sub(r'(?s)skills:.*?(?=\n\w)', 'skills: [' + ', '.join(skills) + ']', text, 1), encoding='utf-8')

    @needs_os_symlinks
    @each_mode
    def test_removed_skills_vanish_from_every_host_and_hand_written_entries_stay(self, mode):
        project = self.new_project(f'prune-{mode}', skills=('feature', 'solo', 'other', 'extra'), claude=True)
        self.link_ok(project, mode=mode)
        hosts = ('.claude/skills', '.agents/skills', '.github/skills')
        for host in hosts:
            self.assertTrue((project / host / 'extra').exists(), (mode, host))
        if mode == 'symlink':
            self.assertTrue((project / '.claude/skills/extra').is_symlink())
            self.assertEqual((project / '.claude/skills/extra').resolve().parent.parent, composed_root())
        for host in ('.claude/skills', '.agents/skills'):
            write(project / host / 'manual' / 'SKILL.md', 'hand written\n')
            os.symlink(self.outside, project / host / 'foreign')
        self.set_manifest(project, ['feature', 'solo'])
        self.assertIn('skills: [feature, solo]', (project / '.github/.skillsync.yaml').read_text())
        self.link_ok(project, mode=mode)
        for host in hosts:
            for gone in ('extra', 'other'):
                self.assertFalse((project / host / gone).exists() or (project / host / gone).is_symlink(), (host, gone))
            for kept in ('feature', 'solo'):
                self.assertTrue((project / host / kept).exists(), (host, kept))
        for host in ('.claude/skills', '.agents/skills'):
            self.assertEqual((project / host / 'manual' / 'SKILL.md').read_text(), 'hand written\n')
            self.assertTrue((project / host / 'foreign').is_symlink(), 'a foreign symlink is not ours to remove')

    def test_edited_copy_of_a_removed_skill_is_kept_in_copy_mode(self):
        project = self.new_project('prune-edited', skills=('feature', 'other'), claude=True)
        self.link_ok(project, mode='copy')
        edited = project / '.claude/skills/other/SKILL.md'
        edited.write_text('my own edit\n')
        self.set_manifest(project, ['feature'])
        self.link_ok(project, mode='copy')
        self.assertEqual(edited.read_text(), 'my own edit\n')
        self.assertFalse((project / '.github/skills/other').exists())

    @needs_os_symlinks
    @each_mode
    def test_symlinks_into_legacy_library_are_pruned_only_when_it_is_configured(self, mode):
        entries = [(i, i, self.layer_paths[i]) for i in ('core', 'org')]
        for configured in (False, True):
            extra = {'legacy_library': str(self.old)} if configured else {}
            self.write_config(layers=entries, rules=[(['**'], ['core', 'org'])], **extra)
            project = self.new_project(f'legacy-{mode}-{configured}', skills=('feature',), claude=True)
            self.link_ok(project, mode=mode)
            for host in ('.claude/skills', '.agents/skills'):
                os.symlink(self.old / 'skills/oldskill', project / host / 'oldskill')
            self.link_ok(project, mode=mode)
            for host in ('.claude/skills', '.agents/skills'):
                present = (project / host / 'oldskill').is_symlink()
                self.assertEqual(present, not configured, (host, configured))


class GeneratorWiringTests(ComposeCase):
    """M4c: hooks come from the core only, roles from the latest layer, codex.yaml is merged."""

    AGENT = '---\nname: {0}\ndescription: {1}\n---\n{1} body\n'

    def setUp(self):
        super().setUp()
        core = {**CORE_FILES,
                'hooks/check-closure.py': '#!/usr/bin/env python3\n# core closure hook\n',
                'hooks/check-tests.py': '#!/usr/bin/env python3\n# core tests hook\n',
                'agents/reviewer.md': self.AGENT.format('reviewer', 'core reviewer'),
                'codex.yaml': ('max_concurrent_threads_per_session: 2\nexclude_skills: [solo]\nagents:\n'
                               '  reviewer: {name: reviewer, model: m-core, sandbox_mode: read-only}\n')}
        org = {'hooks/check-closure.py': '# org closure hook (must be ignored)\n',
               'hooks/check-tests.py': '# org tests hook (must be ignored)\n',
               'agents/reviewer.md': self.AGENT.format('reviewer', 'org reviewer'),
               'agents/helper.md': self.AGENT.format('helper', 'org helper'),
               'codex.yaml': ('exclude_skills: [other]\nagents:\n'
                              '  helper: {name: helper, model: m-org, sandbox_mode: read-only}\n'),
               'skills/other/SKILL.md': SKILL_TEXT.format('other')}
        personal = {'agents/helper.md': self.AGENT.format('helper', 'personal helper')}
        self.add_layers(core=core, org=org, personal=personal)

    @each_mode
    def test_hooks_are_taken_from_the_core_layer_only(self, mode):
        feature = self.new_project(f'hook-feature-{mode}', skills=('feature',), claude=True)
        self.link_ok(feature, mode=mode)
        self.assertEqual((feature / '.claude/hooks/check-closure.py').read_text(), '#!/usr/bin/env python3\n# core closure hook\n')
        plain = self.new_project(f'hook-plain-{mode}', skills=('solo',), claude=True, hooks={'stop_tests': True},
                                 tests={'command': './run-tests.sh'})
        self.link_ok(plain, mode=mode)
        self.assertEqual((plain / '.claude/hooks/check-tests.py').read_text(), '#!/usr/bin/env python3\n# core tests hook\n')

    @each_mode
    def test_a_hook_that_only_a_non_core_layer_has_is_not_used(self, mode):
        (self.layer_paths['core'] / 'hooks' / 'check-tests.py').unlink(missing_ok=True)
        plain = self.new_project(f'hook-missing-{mode}', skills=('solo',), claude=True, hooks={'stop_tests': True},
                                 tests={'command': './run-tests.sh'})
        result = self.link_ok(plain, mode=mode)
        self.assertFalse((plain / '.claude/hooks/check-tests.py').exists())
        self.assertIn('Missing hook script', result.stdout)

    @each_mode
    def test_the_latest_layer_wins_for_roles(self, mode):
        project = self.new_project(f'roles-{mode}', skills=('feature',), claude=True, agents=['reviewer', 'helper'])
        self.link_ok(project, mode=mode)
        self.assertIn('org reviewer body', (project / '.claude/agents/reviewer.md').read_text())
        self.assertIn('personal helper body', (project / '.claude/agents/helper.md').read_text())
        reviewer = tomllib.loads((project / '.codex/agents/reviewer.toml').read_text())
        helper = tomllib.loads((project / '.codex/agents/helper.toml').read_text())
        self.assertEqual(reviewer['description'], 'org reviewer')
        self.assertEqual(helper['description'], 'personal helper')

    @each_mode
    def test_codex_yaml_is_merged_across_layers(self, mode):
        project = self.new_project(f'codex-{mode}', skills=('feature', 'solo', 'other'), claude=True)
        self.link_ok(project, mode=mode)
        # no explicit `agents:` -> the roster is the merged codex.yaml (core reviewer + org helper)
        roles = sorted(path.name for path in (project / '.codex/agents').iterdir() if path.suffix == '.toml')
        self.assertEqual(roles, ['helper.toml', 'reviewer.toml'])
        self.assertEqual(tomllib.loads((project / '.codex/agents/reviewer.toml').read_text())['model'], 'm-core')
        self.assertEqual(tomllib.loads((project / '.codex/agents/helper.toml').read_text())['model'], 'm-org')
        self.assertEqual(tomllib.loads((project / '.codex/config.toml').read_text())['agents']['max_concurrent_threads_per_session'], 2)
        # exclude_skills is the union of core ([solo]) and org ([other]) for Codex, not for the other hosts
        codex = sorted(path.name for path in (project / '.agents/skills').iterdir())
        self.assertEqual(codex, ['feature'])
        self.assertEqual(sorted(path.name for path in (project / '.github/skills').iterdir()), ['feature', 'other', 'solo'])


class SingleProviderLinkTests(CompositeCase):
    """Minor: a skill with one provider whose directory contains a link (D73) must not expose it to the host."""

    SECRET = 'SECRET-BEHIND-THE-LINK'

    @needs_os_symlinks
    @each_mode
    def test_the_host_does_not_see_a_link_inside_a_single_provider_skill(self, mode):
        outside = self.work / 'outside'
        write(outside / 'secret.md', self.SECRET + '\n')
        skill = self.layer_paths['core'] / 'skills' / 'solo'
        if not (skill / 'leak.md').is_symlink():
            os.symlink(outside / 'secret.md', skill / 'leak.md')
            os.symlink(outside, skill / 'leak-dir')
        project = self.new_project(f'single-link-{mode}', skills=SKILLS, claude=True)
        self.link_ok(project, mode=mode)
        for host in ('.github/skills', '.claude/skills', '.agents/skills'):
            directory = project / host / 'solo'
            if not directory.exists():
                continue
            names = {path.relative_to(directory).as_posix() for path in directory.rglob('*')}
            self.assertEqual(names & {'leak.md', 'leak-dir', 'leak-dir/secret.md'}, set(), (mode, host))
            self.assertEqual((directory / 'notes.md').read_text(), 'solo notes\n')
        self.assertTrue((project / '.github/skills/solo').exists())


class TrustWarningTests(ComposeCase):
    """Minor: `link` reports a trust violation (org overrides core without allow_override) as [warn] and links on."""

    @each_mode
    def test_override_of_a_core_file_is_a_warning_not_a_block(self, mode):
        self.add_layers(core=CORE_FILES, org={'skills/feature/SKILL.md': SKILL_TEXT.format('feature') + 'org edit\n'})
        project = self.new_project(f'trust-{mode}', skills=SKILLS)
        result = self.link_ok(project, mode=mode)
        self.assertRegex(result.stdout, r'(?m)^\[warn\] layers: .*skills/feature/SKILL\.md.*allow_override')
        self.assertNotIn('[blocked]', result.stdout)
        self.assertIn('org edit', (self.skill_dir(project, 'feature') / 'SKILL.md').read_text())

    @each_mode
    def test_an_allowed_override_and_a_clean_set_print_no_trust_warning(self, mode):
        self.add_layers(core=CORE_FILES, org={'skills/feature/SKILL.md': SKILL_TEXT.format('feature') + 'org edit\n'})
        self.write_config(layers=[('core', 'core', self.layer_paths['core']), ('org', 'org', self.layer_paths['org'])],
                          rules=[(['**'], ['core', 'org'])], allow_override=['skills/feature/SKILL.md'])
        allowed = self.new_project(f'trust-ok-{mode}', skills=SKILLS)
        self.assertNotIn('allow_override', self.link_ok(allowed, mode=mode).stdout)
        (self.layer_paths['org'] / 'skills/feature/SKILL.md').unlink()
        self.add_layers(core=CORE_FILES, org=ORG_FILES)
        clean = self.new_project(f'trust-clean-{mode}', skills=SKILLS)
        self.assertNotIn('allow_override', self.link_ok(clean, mode=mode).stdout)


if __name__ == '__main__':
    unittest.main()
