"""CORE-1130 S3: `Verdict:` obok `Werdykt:` w raporcie pre-implement (`pre_implement_state`) i w raporcie retro
(sygnały statusu). Czytamy oba języki; zachowanie dla polskich dokumentów bez zmian."""
import tempfile
import unittest
from pathlib import Path

import sys as _sys_tmp_paths  # noqa: E402
from pathlib import Path as _Path_tmp_paths  # noqa: E402
_sys_tmp_paths.path.insert(0, str(_Path_tmp_paths(__file__).resolve().parent))  # noqa: E402
from tmp_paths import child  # noqa: E402

import test_skillsync_retro as retro_tests
from skillsync_workflow.status import pre_implement_state

SPEC = '# Spec\n\nStatus: draft\n\n## Kontrakty\n\n- `status` dostaje pole.\n'
PLAN = '# Plan\n\nStatus: approved\nSpec: docs/specs/s.md\nPrzebieg: R-1\n'


class PreImplementVerdict(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skillsync-verdict-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'docs/specs').mkdir(parents=True)
        (self.root / 'docs/specs/s.md').write_text(SPEC, encoding='utf-8')

    def state(self, *reports):
        run = self.root / '.skillsync/runs/R-1'
        run.mkdir(parents=True, exist_ok=True)
        for name, text in reports:
            child(run, name).write_text(text, encoding='utf-8')
        return pre_implement_state(self.root, 'docs/specs/s.md', PLAN, 'approved')

    # ---- PL = EN ----
    def test_each_word_gives_the_same_result_in_both_languages(self):
        for word in ('READY', 'NEEDS_CHANGES', 'BLOCKED'):
            with self.subTest(word=word):
                pl = self.state(('pre-implement.md', f'Werdykt: {word}\n'))
                en = self.state(('pre-implement.md', f'Verdict: {word}\n'))
                self.assertEqual(pl['verdict'], word)
                self.assertEqual(en, pl)

    def test_ready_english_clears_warning(self):
        st = self.state(('pre-implement.md', '\nVerdict: READY\n\ntreść\n'))
        self.assertEqual((st['verdict'], st['report'], st['warning']),
                         ('READY', '.skillsync/runs/R-1/pre-implement.md', None))

    def test_blocked_english_warns(self):
        st = self.state(('pre-implement.md', 'Verdict: BLOCKED\n'))
        self.assertEqual(st['verdict'], 'BLOCKED')
        self.assertIn('BLOCKED', st['warning'])

    def test_formatting_variants(self):
        for text in ('**Verdict:** READY\n', '`Verdict:` READY\n', 'Verdict: **READY**\n', 'Verdict: `READY`.\n',
                     'Verdict: READY.\n', '  \n**Verdict: READY**\n'):
            with self.subTest(text=text):
                self.assertEqual(self.state(('pre-implement.md', text))['verdict'], 'READY')

    # ---- mieszane ----
    def test_mixed_reports_pick_the_worst_across_languages(self):
        st = self.state(('pre-implement-1.md', 'Werdykt: READY\n'), ('pre-implement-2.md', 'Verdict: BLOCKED\n'))
        self.assertEqual((st['verdict'], st['report']), ('BLOCKED', '.skillsync/runs/R-1/pre-implement-2.md'))
        st = self.state(('pre-implement-1.md', 'Verdict: NEEDS_CHANGES\n'), ('pre-implement-2.md', 'Werdykt: READY\n'))
        self.assertEqual((st['verdict'], st['report']), ('NEEDS_CHANGES', '.skillsync/runs/R-1/pre-implement-1.md'))

    def test_polish_still_works(self):
        self.assertEqual(self.state(('pre-implement.md', 'Werdykt: READY\n'))['verdict'], 'READY')

    # ---- negatywne ----
    def test_unknown_word_is_not_a_verdict(self):
        st = self.state(('pre-implement.md', 'Verdict: MAYBE\n'))
        self.assertIsNone(st['verdict'])
        self.assertIn('brak pre-implement', st['warning'])

    def test_lowercase_label_is_not_a_verdict_like_polish(self):
        self.assertIsNone(self.state(('pre-implement.md', 'werdykt: READY\n'))['verdict'])
        self.assertIsNone(self.state(('pre-implement.md', 'verdict: READY\n'))['verdict'])

    def test_english_phrase_in_content_is_not_a_token(self):
        for text in ('# Raport\nVerdict: READY\n', 'The Verdict: READY was given\n', 'Verdict: READY and more\n',
                     'No Verdict: READY\n'):
            with self.subTest(text=text):
                self.assertIsNone(self.state(('pre-implement.md', text))['verdict'])


class RetroVerdictSignals(retro_tests.RetroBase):
    def signals(self, content):
        self.report_file('rd', 'r.md', content)
        return {d['id']: d for d in self.full()['report_dirs']}['rd']['status_signals']

    def test_english_equals_polish_for_every_signal(self):
        for word in ('PASS', 'FAIL', 'BLOCKED', 'NOT_RUN', 'CHANGES_REQUIRED', 'DONE'):
            with self.subTest(word=word):
                pl = self.signals(f'Werdykt: {word}\n')
                en = self.signals(f'Verdict: {word}\n')
                self.assertEqual(pl[word], 1)
                self.assertEqual(en, pl)

    def test_formatting_variants(self):
        entry = self.signals('**Verdict: CHANGES_REQUIRED**\n**Verdict:** PASS\nVerdict: **FAIL**\n  Verdict: BLOCKED\n')
        self.assertEqual((entry['CHANGES_REQUIRED'], entry['PASS'], entry['FAIL'], entry['BLOCKED']), (1, 1, 1, 1))
        self.assertEqual(entry['unrecognized'], 0)

    def test_mixed_languages_add_up(self):
        entry = self.signals('Status: PASS\nWerdykt: PASS\nVerdict: PASS\nVerdict: FAIL\nWerdykt: FAIL\n')
        self.assertEqual((entry['PASS'], entry['FAIL'], entry['unrecognized']), (3, 2, 0))

    def test_verdict_with_a_pre_implement_word_is_unrecognized_like_polish(self):
        entry = self.signals('Verdict: READY\nVerdict: NEEDS_CHANGES\nVerdict: approved\nVerdict: PASSED\n')
        self.assertEqual(entry['unrecognized'], 4)
        self.assertEqual(sum(v for k, v in entry.items() if k != 'unrecognized'), 0)
        pl = self.signals('Werdykt: READY\nWerdykt: NEEDS_CHANGES\nWerdykt: approved\nWerdykt: PASSED\n')
        self.assertEqual(pl, entry)

    def test_lowercase_label_is_not_a_signal(self):
        entry = self.signals('verdict: PASS\nwerdykt: PASS\n')
        self.assertEqual(sum(entry.values()), 0)

    def test_english_phrase_in_content_is_not_a_signal(self):
        entry = self.signals('The Verdict: PASS\nNo Verdict: FAIL\nMy Verdict: DONE\n')
        self.assertEqual(sum(entry.values()), 0)

    def test_verdict_signals_count_as_unrecorded_like_polish(self):
        self.report_file('rd', 'review.md', 'Verdict: CHANGES_REQUIRED\n')
        data = self.full()
        self.assertEqual({d['id']: d for d in data['report_dirs']}['rd']['unrecorded_signals'], 1)


if __name__ == '__main__':
    unittest.main()
