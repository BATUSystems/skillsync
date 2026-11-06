"""Smoke tests of the leak guard module (implementer, step 1.1).

Criteria tests (K6-K8) live in test_skillsync_leakguard.py (test-writer). Names that the
guard must catch are composed at runtime so this file carries no literal trace.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from layers_helpers import IsolatedTestCase  # noqa: E402
from skillsync_workflow import leakguard  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
from cli_module import SKILLSYNC_PATH  # noqa: E402

SCRIPT = SKILLSYNC_PATH  # wrapper; processes run [sys.executable, SCRIPT]


class LeakguardSmokeTest(IsolatedTestCase):
    def test_builtin_resources_load(self):
        patterns = leakguard.load_patterns()
        self.assertTrue(any(rule.category == 'd27' for rule in patterns.patterns))
        self.assertIn('publisher-urls', [rule.id for rule in patterns.allow])
        self.assertIn('ow', leakguard.load_suffixes())

    def test_worktree_fail_and_pass_without_git(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'skills').mkdir()
            (root / 'skills' / 'a.md').write_text('uses ' + 'Open' + 'Mer' + 'cato' + '\n', encoding='utf-8')
            report = leakguard.leakcheck(root)
            self.assertEqual(report['status'], 'FAIL')
            self.assertEqual(report['findings'], [{'path': 'skills/a.md', 'category': 'd27', 'location': '1:6'}])
            self.assertNotIn('Merc' + 'ato', json.dumps(report))
            (root / 'skills' / 'a.md').write_text('clean /home/user/x\n', encoding='utf-8')
            self.assertEqual(leakguard.leakcheck(root)['status'], 'PASS')

    def test_identity_inflection_and_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            term = 'Glob' + 'ex'
            identity = root / 'identity.yaml'
            identity.write_text(f'schema: 1\nlayer: other\nterms:\n  - {{term: {term}, kind: org}}\n', encoding='utf-8')
            scan = root / 'scan'
            scan.mkdir()
            (scan / 'note.md').write_text(f'we met {term.lower()}owi\n', encoding='utf-8')
            targets = {'mine': {'scope': 'org-private', 'kind': 'org'}, 'other': {'scope': 'private'}}
            report = leakguard.leakcheck(scan, target='mine', targets=targets, identity_files=[identity])
            self.assertEqual((report['status'], report['coverage']), ('FAIL', 'full'))
            self.assertEqual(report['findings'][0]['category'], 'identity-org')
            blocked = leakguard.leakcheck(scan, target='mine', targets=targets, require='full')
            self.assertEqual((blocked['status'], blocked['coverage']), ('BLOCKED', 'generic'))

    def test_cli_patterns_and_exit_codes(self):
        process = subprocess.run([sys.executable, str(SCRIPT), 'leakguard', 'patterns', '--json'],
                                 capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)['status'], 'PASS')
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, 'x.md').write_text('see /home/' + 'someone' + '/x\n', encoding='utf-8')
            process = subprocess.run([sys.executable, str(SCRIPT), 'leakcheck', temp, '--json'],
                                     capture_output=True, text=True)
            self.assertEqual(process.returncode, 1, process.stderr)
            process = subprocess.run([sys.executable, str(SCRIPT), 'leakcheck', temp, '--staged', '--json'],
                                     capture_output=True, text=True)
            self.assertEqual(process.returncode, 2, process.stderr)


if __name__ == '__main__':
    unittest.main()
