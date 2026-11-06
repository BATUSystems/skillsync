"""Smoke tests of the 2.2a composition (`link`/`instructions` on a LayerSet; implementer, step 2.2a).

Not the acceptance tests of K1/K4/K5/K12 (those belong to step 2.2at); they keep the main paths
alive in both link modes. All machine state is isolated (`IsolatedTestCase`).
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from layers_helpers import IsolatedTestCase, write  # noqa: E402

from cli_module import SKILLSYNC_PATH  # noqa: E402

SCRIPT = SKILLSYNC_PATH  # wrapper; processes run [sys.executable, SCRIPT]
SKILL = '---\nname: {0}\ndescription: {0} skill\n---\n'


def make_layer(root, layer_id, kind, files=None, core_min='0.9.0'):
    write(root / 'layer.yaml', f'schema: 1\nid: {layer_id}\nkind: {kind}\nversion: 1.0.0\ncore_min: {core_min}\n')
    write(root / 'README.md', f'# {layer_id}\n')
    write(root / 'CHANGELOG.md', '# Changelog\n')
    for rel, text in (files or {}).items():
        write(root / rel, text)
    return root


class ComposeSmokeTest(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.core = make_layer(self.work / 'core', 'core', 'core', {
            'instructions/_base.md': '# Base\n<!-- SKILLSYNC:BASE-END -->\n',
            'instructions/proj.md': '# Project\n',
            'codex.yaml': 'max_concurrent_threads_per_session: 2\n',
            'skills/feature/SKILL.md': SKILL.format('feature'),
            'skills/feature/references/core.md': 'core ref\n',
            'skills/dispatch/SKILL.md': SKILL.format('dispatch'),
        })
        self.org = make_layer(self.work / 'org', 'org', 'org', {
            'skills/feature/references/org.md': 'org ref\n',
        })
        self.personal = make_layer(self.work / 'personal', 'personal', 'personal', {
            'instructions/_base.d/10-persona.md': 'persona\n',
        })
        self.make_project('proj')
        self.config = Path(self.env['SKILLSYNC_CONFIG'])
        self.write_config()

    def make_project(self, name):
        self.project = self.work / name
        write(self.project / '.github' / '.skillsync.yaml',
              'version: 1\nskills: [feature, dispatch]\ninstructions: {merge: [_base], template: proj}\n'
              'claude: false\nmcp: false\n')

    def write_config(self, use=('core', 'org', 'personal')):
        layers = ''.join(f'  - {{id: {name}, kind: {name}, path: {getattr(self, name)}}}\n'
                         for name in ('core', 'org', 'personal'))
        self.config.write_text(f'layers:\n{layers}layer_rules:\n  - {{match: ["**"], use: [{", ".join(use)}]}}\n',
                               encoding='utf-8')

    def link(self, *extra, env=None):
        full = dict(os.environ)
        full.pop('SKILLSYNC_NO_SYMLINK', None)  # the mode is chosen per call, not inherited
        full.update(env or {})
        return subprocess.run([sys.executable, str(SCRIPT), 'link', str(self.project), *extra],
                              capture_output=True, text=True, timeout=120, env=full)

    def test_overlay_fragments_and_idempotence_in_both_modes(self):
        for mode, env in (('symlink', {}), ('copy', {'SKILLSYNC_NO_SYMLINK': '1'})):
            with self.subTest(mode=mode):
                self.make_project(f'proj-{mode}')
                first = self.link(env=env)
                self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
                skill = self.project / '.github' / 'skills' / 'feature'
                self.assertEqual((skill / 'references' / 'org.md').read_text(), 'org ref\n')
                self.assertEqual((skill / 'references' / 'core.md').read_text(), 'core ref\n')
                text = (self.project / '.github' / 'copilot-instructions.md').read_text()
                self.assertIn('<!-- SKILLSYNC:FRAGMENT begin personal/10-persona.md -->', text)
                self.assertLess(text.index('persona'), text.index('<!-- SKILLSYNC:BASE-END -->'))
                second = self.link(env=env)
                self.assertEqual((second.returncode, second.stdout), (0, ''), second.stderr)
                if mode == 'symlink':
                    composed = Path(self.env['XDG_CACHE_HOME']) / 'skillsync' / 'composed' / 'feature'
                    self.assertTrue(os.path.realpath(skill).startswith(str(composed)))
                    self.assertTrue(os.path.islink(skill / 'references' / 'org.md'))
                else:
                    self.assertFalse(os.path.islink(skill) or os.path.islink(skill / 'references' / 'org.md'))

    def test_core_min_above_the_generator_refuses_link(self):
        make_layer(self.org, 'org', 'org', core_min='99.0.0')
        result = self.link()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('[blocked]', result.stdout)
        self.assertFalse((self.project / '.github' / 'skills').exists())

    def test_legacy_library_only_config_is_blocked(self):
        self.config.write_text(f'library: {self.core}\n', encoding='utf-8')
        result = self.link()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('layers migrate', result.stdout)


if __name__ == '__main__':
    unittest.main()
