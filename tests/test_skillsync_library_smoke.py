"""Smoke test with the real library: `library/` of this repository as the core layer (step 1.4).

The other tests build synthetic layers; this one proves that the shipped library is a valid core layer:
`layers check`, `link` and `doctor` pass on a throwaway project, in both link modes.
All machine state is isolated (`IsolatedTestCase`).
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from layers_helpers import OS_CAN_SYMLINK, IsolatedTestCase, write  # noqa: E402

from cli_module import SKILLSYNC_PATH  # noqa: E402

LIBRARY = ROOT / 'library'


class RealLibrarySmokeTest(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue((LIBRARY / 'layer.yaml').is_file(), f'library/layer.yaml is missing in {ROOT}')
        Path(self.env['SKILLSYNC_CONFIG']).write_text(
            f'layers:\n  - {{id: core, kind: core, path: {json.dumps(str(LIBRARY))}, source: {json.dumps(str(LIBRARY))}}}\n'
            'layer_rules:\n  - {match: ["**"], use: [core]}\n', encoding='utf-8')
        self.project = self.work / 'proj'
        write(self.project / '.github' / '.skillsync.yaml', '')
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True, capture_output=True)

    def run_cli(self, *args, mode):
        env = dict(os.environ)
        env.pop('SKILLSYNC_NO_SYMLINK', None)
        if mode == 'copy':
            env['SKILLSYNC_NO_SYMLINK'] = '1'
        return subprocess.run([sys.executable, str(SKILLSYNC_PATH), *args], capture_output=True, text=True,
                              timeout=180, env=env)

    def new_project(self, name):
        project = self.work / name
        write(project / '.github' / '.skillsync.yaml', '')
        subprocess.run(['git', 'init', '-q', str(project)], check=True, capture_output=True)
        return project

    def test_library_links_and_passes_doctor_in_both_modes(self):
        check = self.run_cli('layers', 'check', '--project', str(self.project), mode='symlink')
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        for mode in ('symlink', 'copy'):
            with self.subTest(mode=mode):
                project = self.new_project(f'proj-{mode}')
                link = self.run_cli('link', str(project), mode=mode)
                self.assertEqual(link.returncode, 0, link.stdout + link.stderr)
                skills = list((project / '.claude' / 'skills').iterdir())
                self.assertTrue(any((p / 'SKILL.md').is_file() for p in skills), 'no skill was linked')
                if mode == 'copy' or not OS_CAN_SYMLINK:
                    self.assertFalse(any(p.is_symlink() for p in skills), 'copy mode left symlinks')
                else:
                    self.assertTrue(all(p.is_symlink() for p in skills), 'symlink mode produced copies')
                doctor = self.run_cli('doctor', str(project), mode=mode)
                self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)


if __name__ == '__main__':
    unittest.main()
