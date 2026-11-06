"""Step 2.4t: K1 equivalence of `link` artefacts (spec K1, K15 (a)).

The fixture `tests/fixtures/layers/baseline/*.json` was generated once on the generator before the layers work
(step 0.2, `make_baseline.py`): one library, the machine config with the old `library:` key, `skillsync link` of a
synthetic project. Here the same library and project go through the new generator: the old config is first
converted by `skillsync layers migrate` (the legacy variant, whose library has no `layer.yaml`, through
`--layer-source core=PATH` with a real core layer holding the same files; the variant with `layer.yaml` through the
plain migration and through `--layer-source core=<library>`), then `skillsync link` runs in the given mode.
The 0.9.x bridge (`--adopt-legacy`) no longer exists: it is BLOCKED (see `UnmigratedConfigTest`).

Equivalence (K1): the same list of files and symlinks in the project, the same content (paths of the temporary
directories replaced by tokens, as in the fixture), symlink targets equal relative to the library/layer root.
Exception documented in `COPY_STATE`: the copy-mode state file changed its format by contract K4
(`files{owner, base}`), so only its presence is compared, never its hash.
Each variant runs in both link modes (`SKILLSYNC_NO_SYMLINK=1` for copy); machine state is isolated.
"""
import hashlib
import json
import os
import re
import subprocess
import shutil
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS / 'fixtures' / 'layers'))
sys.path.insert(0, str(ROOT))

import make_baseline  # noqa: E402
from layers_helpers import LINK_MODES, LayersCase, generator_version, link_mode, make_layer, tree_snapshot, yaml_text  # noqa: E402

BASELINE = TESTS / 'fixtures' / 'layers' / 'baseline'
STATE_FILE = '.github/.skillsync-state.json'
# K4 changed the copy-mode state format (`files{owner, base}` per file); the file must exist, its hash differs.
COPY_STATE = {("copy", STATE_FILE)}
BRIDGE_WARNING = 'legacy-bridge'           # the warning of 0.9.x: never printed any more


def baseline(variant, mode):
    return json.loads((BASELINE / f'{variant}.{mode}.json').read_text(encoding='utf-8'))


class EquivalenceCase(LayersCase):
    def prepare(self, variant, mode, label):
        """Library of the fixture (with or without `layer.yaml`), project with the fixture manifest and the old
        machine config (`library:` key, the keys used by `make_baseline.py`)."""
        base = self.work / f'{label}-{mode}'
        library, project = base / 'library', base / 'project'
        make_baseline.build_library(library, with_layer=(variant == 'with-layer-yaml'))
        make_baseline.write(project / '.github' / '.skillsync.yaml', make_baseline.MANIFEST)
        self.config_file.write_text(f'library: {library}\ndiscover_paths: []\ntags: {{}}\npython_command: python3\n',
                                    encoding='utf-8')
        return library, project

    def describe(self, library, project):
        tokens = [(str(library), '<LIB>'), (str(project), '<PROJ>'), (str(ROOT), '<GEN>'), (self.env['HOME'], '<HOME>')]
        result = make_baseline.describe(project, tokens)
        result['symlinks'] = {key: (value[len('<LIB>/'):] if value.startswith('<LIB>/') else value)
                              for key, value in result['symlinks'].items()}
        self.normalize_task_command(project, tokens, result)
        return result

    def normalize_task_command(self, project, tokens, result):
        """The `command` of the `skillsync: link` task is the user's entry (`~/.local/bin/skillsync` or a hit on PATH), the
        checkout wrapper or an interpreter, whichever the machine has (I1, K6 revised: the task is independent of who ran
        `link`), so it is replaced by `<ENTRY>` before hashing; the rest of `.vscode/tasks.json` stays compared."""
        path = project / '.vscode' / 'tasks.json'
        if not path.is_file():
            return
        data = path.read_bytes().replace(b"\r\n", b"\n").decode('utf-8')
        for real, token in tokens:
            data = data.replace(real, token)
        data, count = re.subn(r'("command": )"[^"\n]*"', r'\1"<ENTRY>"', data)
        self.assertEqual(count, 1, 'exactly one task command in the generated tasks.json')
        result['files']['.vscode/tasks.json'] = hashlib.sha256(data.encode('utf-8')).hexdigest()

    def migrate(self, *flags):
        process = self.cli('layers', 'migrate', *flags, '--apply', '--json')
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        return json.loads(process.stdout)

    def assert_equivalent(self, got, expected, mode):
        self.assertEqual(sorted(got['symlinks']), sorted(expected['symlinks']), 'the same symlinks')
        self.assertEqual(got['symlinks'], expected['symlinks'], 'the same symlink targets relative to the layer root')
        self.assertEqual(sorted(got['files']), sorted(expected['files']), 'the same list of files')
        for rel, digest in expected['files'].items():
            if (mode, rel) in COPY_STATE:
                continue
            with self.subTest(file=rel):
                self.assertEqual(got['files'][rel], digest, f'content of {rel} differs from the baseline')


class LinkEquivalenceTest(EquivalenceCase):
    """K1: artefacts of `link` after `layers migrate` equal the baseline of the generator before layers."""

    CASES = {
        # label: (fixture variant, how the core layer is given to `layers migrate`)
        'legacy-core-source': ('legacy', 'core-copy'),      # a real core: the library files plus `layer.yaml`
        'with-layer-yaml': ('with-layer-yaml', 'old-library'),
        'with-layer-yaml-source': ('with-layer-yaml', 'library-as-source'),
    }

    def test_link_after_the_migration_gives_the_baseline_artefacts(self):
        for label, (variant, how) in self.CASES.items():
            for mode in LINK_MODES:
                with self.subTest(case=label, link_mode=mode):
                    library, project = self.prepare(variant, mode, label)
                    root, flags = library, ()
                    if how == 'core-copy':
                        root = library.parent / 'core'
                        shutil.copytree(library, root)
                        make_layer(root, 'core', 'core', readme=False, changelog=False)
                    if how != 'old-library':
                        flags = ('--layer-source', f'core={root}')
                    before = tree_snapshot(root)
                    report = self.migrate(*flags)
                    self.assertEqual(report['status'], 'PASS', report)
                    config = self.config()
                    core = [entry for entry in config['layers'] if entry['kind'] == 'core']
                    self.assertEqual(len(core), 1, config)
                    self.assertEqual(Path(core[0]['path']), root)
                    self.assertNotIn('synthetic', core[0], 'a real core layer, never the 0.9.x bridge')
                    with link_mode(mode):
                        linked = self.cli('link', str(project))
                    self.assertEqual(linked.returncode, 0, linked.stdout + linked.stderr)
                    self.assertNotIn('Traceback', linked.stderr)
                    self.assertNotIn(BRIDGE_WARNING, linked.stdout + linked.stderr, 'no legacy-bridge warning any more')
                    self.assert_equivalent(self.describe(root, project), baseline(variant, mode), mode)
                    self.assertEqual(tree_snapshot(root), before, 'neither migrate nor link writes to the core layer')
                    if mode == 'copy':
                        self.assert_copy_state(project, core[0]['id'])

    def assert_copy_state(self, project, core_id):
        """The exception of `COPY_STATE`: instead of the old hash, the K4 format names the owner of every copied
        skill file (here the single core layer)."""
        files = json.loads((project / STATE_FILE).read_text(encoding='utf-8'))['files']
        copied = sorted(path.relative_to(project / '.github' / 'skills').as_posix()
                        for path in (project / '.github' / 'skills').rglob('*') if path.is_file())
        self.assertTrue(copied)
        for rel in copied:
            self.assertEqual(files[f'github-skill:{rel}']['owner'], core_id, rel)

    def test_the_fixture_itself_was_not_regenerated_on_the_new_generator(self):
        """The baseline is meaningful only as the state before layers: its config had the `library:` key, which
        the new generator refuses (D41), and its link output has no layer warning."""
        for variant in ('legacy', 'with-layer-yaml'):
            for mode in LINK_MODES:
                data = baseline(variant, mode)
                self.assertEqual((data['variant'], data['mode']), (variant, mode))
                self.assertNotIn(BRIDGE_WARNING, data['link_stdout'])
                self.assertTrue(data['files'])
        readme = (BASELINE / 'README.md').read_text(encoding='utf-8')
        self.assertIn('sprzed zmian warstw', readme)

    def config(self):
        import yaml
        return yaml.safe_load(self.config_file.read_text(encoding='utf-8'))


class UnmigratedConfigTest(EquivalenceCase):
    """D41 and K15: the old config without migration, and a 0.9.x bridge config (`synthetic` core), are BLOCKED
    (exit 2) with the `layers migrate` instruction and do not change the artefacts of the project;
    `layers migrate --adopt-legacy` (removed in 1.0) is BLOCKED and writes nothing."""

    def test_the_old_library_key_is_blocked_with_the_migration_instruction(self):
        for mode in LINK_MODES:
            with self.subTest(link_mode=mode):
                library, project = self.prepare('legacy', mode, 'unmigrated')
                before = tree_snapshot(project)
                with link_mode(mode):
                    process = self.cli('link', str(project))
                self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
                self.assertIn('layers migrate', process.stdout + process.stderr)
                self.assertNotIn('Traceback', process.stderr)
                self.assertEqual(tree_snapshot(project), before, 'a BLOCKED link creates no artefact')
                code, report = self.cli_json('layers', 'check', '--project', str(project))
                self.assertEqual((code, report['status']), (2, 'BLOCKED'), report)
                self.assertIn('layers migrate', report['reason'])

    def test_a_bridge_config_written_by_0_9_x_is_blocked_and_nothing_changes(self):
        for mode in LINK_MODES:
            with self.subTest(link_mode=mode):
                library, project = self.prepare('legacy', mode, 'bridge-config')
                version = generator_version()
                self.config_file.write_text(yaml_text({
                    'discover_paths': [], 'tags': {}, 'python_command': 'python3', 'legacy_library': str(library),
                    'layers': [{'id': 'core', 'kind': 'core', 'path': str(library), 'writable': False,
                                'synthetic': {'version': version, 'core_min': version}}],
                    'layer_rules': [{'match': ['**'], 'use': ['core']}], 'isolation': {'allowed_layers': ['core']}}),
                    encoding='utf-8')
                before = tree_snapshot(project)
                for extra in ((), ('--dry-run',)):
                    with link_mode(mode):
                        process = self.cli('link', str(project), *extra)
                    self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
                    self.assertNotIn('Traceback', process.stderr)
                    self.assertIn('layers migrate --layer-source core=PATH --apply', process.stdout + process.stderr)
                    self.assertNotIn(BRIDGE_WARNING, process.stdout + process.stderr)
                    self.assertEqual(tree_snapshot(project), before, 'artefacts unchanged')

    def test_adopt_legacy_is_blocked_and_writes_nothing(self):
        library, project = self.prepare('legacy', 'symlink', 'adopt')
        config_before = self.config_file.read_bytes()
        backups = sorted(self.config_file.parent.glob('*.bak-*'))
        library_before = tree_snapshot(library)
        process = self.cli('layers', 'migrate', '--adopt-legacy', '--apply', '--json')
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual((report['status'], report['code']), ('BLOCKED', 'bad-argument'))
        self.assertIn('--layer-source core=PATH', report['reason'])
        self.assertEqual(self.config_file.read_bytes(), config_before, '--adopt-legacy writes nothing')
        self.assertEqual(sorted(self.config_file.parent.glob('*.bak-*')), backups, 'and makes no backup')
        self.assertEqual(tree_snapshot(library), library_before)


if __name__ == '__main__':
    unittest.main()
