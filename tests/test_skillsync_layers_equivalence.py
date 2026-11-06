"""Step 2.4t: K1 equivalence of `link` artefacts (spec K1, K15 (a)).

The fixture `tests/fixtures/layers/baseline/*.json` was generated once on the generator before the layers work
(step 0.2, `make_baseline.py`): one library, the machine config with the old `library:` key, `skillsync link` of a
synthetic project. Here the same library and project go through the new generator: the old config is first
converted by `skillsync layers migrate` (the legacy variant through the bridge `--adopt-legacy`, the variant with
`layer.yaml` through the plain migration and through `--adopt-legacy`), then `skillsync link` runs in the given mode.

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
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS / 'fixtures' / 'layers'))
sys.path.insert(0, str(ROOT))

import make_baseline  # noqa: E402
from layers_helpers import LINK_MODES, LayersCase, generator_version, link_mode, tree_snapshot  # noqa: E402

BASELINE = TESTS / 'fixtures' / 'layers' / 'baseline'
STATE_FILE = '.github/.skillsync-state.json'
# K4 changed the copy-mode state format (`files{owner, base}` per file); the file must exist, its hash differs.
COPY_STATE = {("copy", STATE_FILE)}
BRIDGE_WARNING = 'legacy-bridge'


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
        # label: (fixture variant, flags of `layers migrate`, bridge expected)
        'legacy-bridge': ('legacy', ('--adopt-legacy',), True),
        'with-layer-yaml': ('with-layer-yaml', (), False),
        'with-layer-yaml-adopt': ('with-layer-yaml', ('--adopt-legacy',), False),
    }

    def test_link_after_the_migration_gives_the_baseline_artefacts(self):
        for label, (variant, flags, bridge) in self.CASES.items():
            for mode in LINK_MODES:
                with self.subTest(case=label, link_mode=mode):
                    library, project = self.prepare(variant, mode, label)
                    before = tree_snapshot(library)
                    report = self.migrate(*flags)
                    self.assertEqual(report['status'], 'PASS', report)
                    config = self.config()
                    core = [entry for entry in config['layers'] if entry['kind'] == 'core']
                    self.assertEqual(len(core), 1, config)
                    self.assertEqual(Path(core[0]['path']), library)
                    self.assertEqual('synthetic' in core[0], bridge, core[0])
                    with link_mode(mode):
                        linked = self.cli('link', str(project))
                    self.assertEqual(linked.returncode, 0, linked.stdout + linked.stderr)
                    self.assertNotIn('Traceback', linked.stderr)
                    self.assertEqual(BRIDGE_WARNING in linked.stdout + linked.stderr, bridge,
                                     'link prints the legacy-bridge warning exactly when the bridge is used')
                    self.assert_equivalent(self.describe(library, project), baseline(variant, mode), mode)
                    self.assertEqual(tree_snapshot(library), before, 'neither migrate nor link writes to the library')
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
    """D41 and K15 (b): the old config without migration, and the bridge with `LEGACY_BRIDGE = False`, are BLOCKED
    (exit 2) with the `layers migrate` instruction and do not change the artefacts of the project."""

    def bridge_disabled(self, *args, cwd=None):
        """`skillsync <args>` in a child process with the constant `LEGACY_BRIDGE` switched off (as after v1.0)."""
        code = ('import runpy, sys; sys.path.insert(0, sys.argv[1]); from skillsync_workflow import layers; '
                'layers.LEGACY_BRIDGE = False; sys.argv = [sys.argv[2], *sys.argv[3:]]; '
                'runpy.run_path(sys.argv[0], run_name="__main__")')
        return subprocess.run([sys.executable, '-c', code, str(ROOT), str(ROOT / 'skillsync'), *args],
                              capture_output=True, text=True, cwd=str(cwd or self.work), env=dict(os.environ), timeout=300)

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

    def test_with_the_bridge_switched_off_the_bridge_config_is_blocked_and_nothing_changes(self):
        for mode in LINK_MODES:
            with self.subTest(link_mode=mode):
                library, project = self.prepare('legacy', mode, 'switched-off')
                self.migrate('--adopt-legacy')
                with link_mode(mode):
                    self.assertEqual(self.cli('link', str(project)).returncode, 0)
                    before = tree_snapshot(project)
                    process = self.bridge_disabled('link', str(project))
                self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
                self.assertNotIn('Traceback', process.stderr)
                self.assertIn('layers migrate', process.stdout + process.stderr)
                self.assertEqual(tree_snapshot(project), before, 'artefacts unchanged')
                control = self.cli('link', str(project), '--dry-run')                  # the same config with the bridge on
                self.assertEqual(control.returncode, 0, control.stdout + control.stderr)
        self.config_file.write_text(f'library: {self.work / "switched-off-copy" / "library"}\n', encoding='utf-8')
        config_before = self.config_file.read_bytes()
        backups = sorted(self.config_file.parent.glob('*.bak-*'))
        process = self.bridge_disabled('layers', 'migrate', '--adopt-legacy', '--apply', '--json')
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertEqual(json.loads(process.stdout)['status'], 'BLOCKED')
        self.assertEqual(self.config_file.read_bytes(), config_before, '--adopt-legacy without the bridge writes nothing')
        self.assertEqual(sorted(self.config_file.parent.glob('*.bak-*')), backups, 'and makes no backup')


if __name__ == '__main__':
    unittest.main()
