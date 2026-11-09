"""Smoke tests of `skillsync_workflow.layers` (implementer, step 2.1).

Not the acceptance tests of K2/K3/K9/K12 (those belong to step 2.1t); they only keep the
module importable and its main paths alive. All machine state is isolated.
"""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from skillsync_workflow import layers  # noqa: E402
from layers_helpers import IsolatedTestCase, write  # noqa: E402

from cli_module import SKILLSYNC_PATH  # noqa: E402

SCRIPT = SKILLSYNC_PATH  # wrapper; processes run [sys.executable, SCRIPT]


def make_layer(root, layer_id, kind, files=None, core_min='0.9.0', version='1.0.0'):
    write(root / 'layer.yaml', f'schema: 1\nid: {layer_id}\nkind: {kind}\nversion: {version}\ncore_min: {core_min}\n')
    write(root / 'README.md', f'# {layer_id}\n')
    write(root / 'CHANGELOG.md', '# Changelog\n')
    for rel, text in (files or {}).items():
        write(root / rel, text)
    return root


class LayersSmokeTest(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.core = make_layer(self.work / 'core', 'core', 'core', {
            'instructions/_base.md': 'base\n',
            'instructions/_base.d/20-core.md': 'core fragment\n',
            'skills/feature/SKILL.md': 'core skill\n',
            'skills/feature/ref.md': 'core ref\n',
            'codex.yaml': 'exclude_skills: [a]\nagents:\n  reviewer: {sandbox_mode: read-only}\n',
            'workflow/defaults.yaml': ('validation:\n  checks:\n  - {id: unit, command: x}\n'
                                       'routing:\n  rules:\n  - {paths: ["a"]}\n'),
            'workflow/cause-keys.yaml': 'version: 1\nkeys:\n  test-gap: {description: d, aliases: [gap]}\n',
        })
        self.org = make_layer(self.work / 'acme', 'acme', 'org', {
            'instructions/_base.d/05-org.md': 'org early\n',
            'skills/feature/extra.md': 'org extra\n',
            'codex.yaml': 'exclude_skills: [b]\nagents:\n  reviewer: {sandbox_mode: workspace-write}\n',
            'workflow/defaults.yaml': ('validation:\n  checks:\n  - {id: unit, command: y}\n  - {id: lint, command: z}\n'
                                       'routing:\n  rules:\n  - {paths: ["a"]}\n  - {paths: ["b"]}\n'),
            'workflow/cause-keys.d/acme.yaml': 'version: 1\nkeys:\n  other: {description: o, aliases: [gap]}\n',
        })
        self.personal = make_layer(self.work / 'me', 'me', 'personal', {'instructions/_base.d/10-persona.md': 'p\n'})
        self.config_file = Path(self.env['SKILLSYNC_CONFIG'])
        projects = self.work / 'Repos'
        write(projects / 'Acme' / 'app' / '.github' / '.skillsync.yaml', 'project: {name: Acme/app}\n')
        write(self.work / 'loose' / 'x' / '.github' / '.skillsync.yaml', '')
        write(self.config_file, (
            'layers:\n'
            f'  - {{id: core, kind: core, path: {self.core}, source: {self.core}}}\n'
            f'  - {{id: acme, kind: org, path: {self.org}, source: {self.org}}}\n'
            f'  - {{id: me, kind: personal, path: {self.personal}, source: {self.personal}}}\n'
            'layer_rules:\n'
            f'  - {{match: ["{projects}/Acme/**"], use: [core, acme, me]}}\n'
            f'  - {{match: ["{projects}/**"], use: [core, me]}}\n'))
        self.project = projects / 'Acme' / 'app'

    def test_rule_selection_and_blocked_without_rule(self):
        layerset = layers.resolve_layers(self.project)
        self.assertEqual(layerset.ids, ['core', 'acme', 'me'])
        with self.assertRaises(layers.LayerError) as caught:
            layers.resolve_layers(self.work / 'loose' / 'x')
        self.assertEqual(caught.exception.code, 'no-rule')

    def test_glob_and_case_folding(self):
        self.assertTrue(layers.path_matches('/a/b/c', '/a/**', 'Linux'))
        self.assertFalse(layers.path_matches('/a', '/a/**', 'Linux'))
        self.assertTrue(layers.path_matches('/x/Acme/p', 'Acme/*', 'Linux'))
        self.assertFalse(layers.path_matches('/A/B', '/a/b', 'Linux'))
        self.assertTrue(layers.path_matches('/A/B', '/a/b', 'Darwin'))

    def test_layer_flag_and_child_argv(self):
        layerset = layers.resolve_layers(layers=[f'core={self.core}', f'me={self.personal}'])
        self.assertEqual(layerset.origin, 'override')
        self.assertEqual(layers.layer_argv(layerset), ['--layer', f'core={self.core}', '--layer', f'me={self.personal}'])
        with self.assertRaises(layers.LayerError):
            layers.parse_layer_flag(f'core={self.core},synthetic')

    def test_merges_and_fragments(self):
        layerset = layers.resolve_layers(self.project)
        defaults = layerset.merged_file('workflow/defaults.yaml')
        self.assertEqual([item['command'] for item in defaults.data['validation']['checks']], ['y', 'z'])
        self.assertEqual(len(defaults.data['routing']['rules']), 2)
        codex = layerset.merged_file('codex.yaml')
        self.assertEqual(codex.data['exclude_skills'], ['a', 'b'])
        files = layerset.skill_files('feature')
        self.assertEqual({rel: owner.id for rel, (owner, _) in files.items()}, {'SKILL.md': 'core', 'extra.md': 'acme', 'ref.md': 'core'})
        order = [(item['layer'], item['position']) for item in layerset.base_fragments()]
        self.assertEqual(order, [('acme', 'pre'), ('core', 'post'), ('me', 'post')])

    def test_check_layers_trust_and_core_min(self):
        layerset = layers.resolve_layers(self.project)
        findings = layers.check_layers(layerset, hooks=False)
        overrides = sorted(item['message'].split()[0] for item in findings if item['code'] == 'override-without-consent')
        self.assertEqual(overrides, ['codex.yaml:agents.reviewer.sandbox_mode', 'validation.checks:unit'])
        self.assertIn('alias-collision', {item['code'] for item in findings})
        self.assertEqual(layers.findings_status(findings), 'FAIL')
        old = layers.check_layers(layerset, hooks=False, version='0.8.0')
        self.assertIn('core-min-unmet', {item['code'] for item in old})
        self.assertEqual(layers.findings_status(old), 'BLOCKED')
        self.assertEqual(layers.findings_status([]), 'PASS')

    def test_allow_override_accepts_listed_elements(self):
        with self.config_file.open('a', encoding='utf-8') as handle:
            handle.write('allow_override: ["validation.checks:unit", "codex.yaml:agents.*"]\n')
        findings = layers.check_layers(layers.resolve_layers(self.project), hooks=False)
        self.assertNotIn('override-without-consent', {item['code'] for item in findings})

    def test_list_json_cli(self):
        process = subprocess.run([sys.executable, str(SCRIPT), 'layers', 'list', '--project', str(self.project), '--json'],
                                 capture_output=True, text=True, timeout=60)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn(f'"generator_version": "{layers.generator_version()}"', process.stdout)


if __name__ == '__main__':
    unittest.main()
