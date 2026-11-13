"""`checkpoint` i `resume` z `--manifest PLIK` (TECH-140, D7c): projekt bez `.github/.skillsync.yaml` (publiczny rdzeń)
używa manifestu spoza repozytorium; bez flagi zachowanie bez zmian.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_checkpoint_manifest.py`.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_skillsync_workflow import CLI, WorkflowFixture  # noqa: E402


class ManifestFlagTests(WorkflowFixture):
    def setUp(self):
        super().setUp()
        self.write('tracked.txt', 'baseline')
        self.init_git()
        self.copy = self.base / 'outside-manifest' / '.skillsync.yaml'
        self.copy.parent.mkdir()
        self.copy.write_text((self.root / '.github/.skillsync.yaml').read_text(encoding='utf-8'), encoding='utf-8')
        (self.root / '.github/.skillsync.yaml').unlink()

    def run_cli(self, command, *args):
        return subprocess.run([sys.executable, str(CLI), command, str(self.root), *map(str, args), '--json'],
                              capture_output=True, text=True, timeout=30, cwd=self.root)

    def checkpoint(self, *extra):
        return self.run_cli('checkpoint', '--run-id', 'task', '--step', 'verify', '--status', 'NOT_RUN', '--next', 'review', *extra)

    def test_without_the_flag_a_missing_manifest_is_still_refused(self):
        result = self.checkpoint()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing manifest', result.stdout + result.stderr)

    def test_checkpoint_and_resume_work_with_a_manifest_outside_the_repository(self):
        result = self.checkpoint('--manifest', self.copy)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / '.skillsync' / 'runs' / 'task.json').is_file(), 'zapis w korzeniu projektu')
        resumed = self.run_cli('resume', '--run-id', 'task', '--manifest', self.copy)
        report = json.loads(resumed.stdout)  # NOT_RUN poprzedniego kroku to BLOCKED (2): wynik biznesowy, nie błąd manifestu
        self.assertEqual((report['run_id'], report['step']), ('task', 'verify'), resumed.stdout + resumed.stderr)
        self.assertNotIn('Missing manifest', resumed.stdout + resumed.stderr)

    def test_a_bad_manifest_path_is_refused_with_the_flag_named(self):
        for value in (self.base / 'nie-ma.yaml', self.base):
            with self.subTest(value=str(value)):
                result = self.checkpoint('--manifest', value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('--manifest', result.stdout + result.stderr)
                self.assertNotIn('Traceback', result.stderr)

    def test_a_manifest_that_is_not_a_mapping_is_refused(self):
        bad = self.base / 'bad.yaml'
        bad.write_text('- lista\n', encoding='utf-8')
        result = self.checkpoint('--manifest', bad)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('Traceback', result.stderr)

    def test_the_flag_wins_over_a_manifest_in_the_project(self):
        (self.root / '.github/.skillsync.yaml').write_text('version: 2\n', encoding='utf-8')  # nieobsługiwana wersja
        self.assertNotEqual(self.checkpoint().returncode, 0)
        self.assertEqual(self.checkpoint('--manifest', self.copy).returncode, 0)


if __name__ == '__main__':
    unittest.main()
