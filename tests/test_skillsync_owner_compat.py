"""Odczyt rekordu przebiegu z właścicielem, którego ta wersja nie zna (CORE-1130-OWNER-COMPAT).

Nowszy generator może zapisać właściciela spoza listy tej wersji (w tym teście `wizard`: nazwa spoza `OWNERS` każdej wersji; `tool` jest znanym właścicielem od 1.14.0). Maszyna ze starszym generatorem ma taki przebieg przeczytać: rekord zostaje,
właściciel jest pomijany z ostrzeżeniem na stderr, a nie wyjątkiem. Zapis nowego rekordu i `retro-decide` zostają ścisłe.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skillsync_workflow import runs  # noqa: E402
from test_skillsync_retro import CandidateBase  # noqa: E402


class UnknownOwnerRead(CandidateBase):
    def test_a_run_with_an_unknown_owner_is_read_and_the_owner_is_skipped(self):
        self.pair('flaky', owner='wizard')
        item = self.one()
        self.assertEqual((item['owner'], item['owners_seen'], item['status']), (None, [], 'needs_owner'))

    def test_an_unknown_owner_next_to_a_known_one_is_ambiguous(self):
        self.run_file('x', self.case('x', 'flaky', owner='wizard'))
        self.run_file('y', self.case('y', 'flaky', owner='test'))
        item = self.one()
        self.assertEqual((item['owner'], item['owners_seen'], item['status']), (None, ['test'], 'needs_owner'))

    def test_a_checkpoint_appended_to_such_a_run_keeps_the_foreign_owner_and_stdout_stays_json(self):
        import json
        self.init_git()
        self.run_file('x', self.case('x', 'flaky', owner='wizard'))
        self.write('.skillsync/evidence/more.log', 'more\n')
        result = self.cli('checkpoint', '--run-id', 'x', '--step', 'more', '--status', 'PASS', '--next', 'n', '--evidence', '.skillsync/evidence/more.log')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("'wizard'", result.stderr)
        json.loads(result.stdout)   # the warning is on stderr only
        data = json.loads((self.root / '.skillsync/runs/x.json').read_text(encoding='utf-8'))
        self.assertEqual([record.get('owner') for record in data['checkpoints']][0], 'wizard')
        self.assertEqual(len(data['checkpoints']), 2)

    def test_the_warning_is_printed_once_and_a_malformed_owner_is_still_an_error(self):
        runs._WARNED_OWNERS.discard('wizard')
        record = {'cause_key': 'k', 'owner': 'wizard'}
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            runs._extra_fields(record, reading=True)
            runs._extra_fields(record, reading=True)
        self.assertEqual(err.getvalue().count("'wizard'"), 1)
        for bad in ('Tool', '', 'x' * 40, 'a b', 5):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    runs._extra_fields({'cause_key': 'k', 'owner': bad}, reading=True)

    def test_writing_stays_strict(self):
        with self.assertRaises(ValueError):
            runs._extra_fields({'cause_key': 'k', 'owner': 'wizard'})


if __name__ == '__main__':
    unittest.main()
