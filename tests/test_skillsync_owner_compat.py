"""Odczyt rekordu przebiegu z właścicielem, którego ta wersja nie zna (CORE-1130-OWNER-COMPAT).

Nowszy generator może zapisać właściciela spoza listy tej wersji (np. `tool` w 1.14.0). Maszyna ze starszym generatorem ma taki przebieg przeczytać: rekord zostaje,
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
        self.pair('flaky', owner='tool')
        item = self.one()
        self.assertEqual((item['owner'], item['owners_seen'], item['status']), (None, [], 'needs_owner'))

    def test_a_known_owner_from_another_run_still_decides(self):
        self.run_file('x', self.case('x', 'flaky', owner='tool'), self.case('x2', 'flaky2', owner='test'))
        self.run_file('y', self.case('y', 'flaky', owner='test'))
        item = [found for found in self.cands() if found['cause_key'] == 'flaky'][0]
        self.assertEqual((item['owner'], item['owners_seen']), ('test', ['test']))

    def test_the_warning_is_printed_once_and_a_malformed_owner_is_still_an_error(self):
        runs._WARNED_OWNERS.discard('tool')
        record = {'cause_key': 'k', 'owner': 'tool'}
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            runs._extra_fields(record, reading=True)
            runs._extra_fields(record, reading=True)
        self.assertEqual(err.getvalue().count("'tool'"), 1)
        for bad in ('Tool', '', 'x' * 40, 'a b', 5):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    runs._extra_fields({'cause_key': 'k', 'owner': bad}, reading=True)

    def test_writing_stays_strict(self):
        with self.assertRaises(ValueError):
            runs._extra_fields({'cause_key': 'k', 'owner': 'tool'})


if __name__ == '__main__':
    unittest.main()
