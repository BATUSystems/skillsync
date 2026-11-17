"""CORE-NOATTR: ban on attribution and advertising of AI tools (data file, scanner, leakguard integration).

All sample texts are composed at run time from fragments, so neither this file nor the data file matches its own patterns."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from layers_helpers import commit_file  # noqa: E402
from skillsync_workflow import attribution, leakguard  # noqa: E402
from test_skillsync_leakguard import LeakTestCase  # noqa: E402

ADDR = 'noreply' + '@' + 'anthro' + 'pic.com'
FOOTER = 'Gener' + 'ated with [Cla' + 'ude Code](https://cla' + 'ude.com/cla' + 'ude-code)'
ROBOT = '\U0001F916 ' + FOOTER
TRAILER = 'Co-Auth' + 'ored-By: Cla' + 'ude Sonnet 5.5 <' + ADDR + '>'
CURSOR = 'Made ' + 'with Cur' + 'sor'
SESSION = 'Cla' + 'ude-Ses' + 'sion: https://example.invalid/s/1'
HUMAN = 'Co-Authored-By: Jan Kowalski <jan' + '@' + 'example.com>'
SAMPLES = {
    'attr-claude-footer': FOOTER,
    'attr-ai-tool-footer': 'Gener' + 'ated by Cop' + 'ilot',
    'attr-made-with': CURSOR,
    'attr-tool-link': 'https://cla' + 'ude.com/cla' + 'ude-code',
    'attr-session-trailer': SESSION,
    'attr-coauthor-name': 'Co-Auth' + 'ored-By: Co' + 'dex <someone' + '@' + 'example.com>',
    'attr-coauthor-address': 'Co-Auth' + 'ored-By: Bot <x@open' + 'ai.com>',
}


class DataTests(unittest.TestCase):
    def test_every_pattern_has_a_sample_and_matches_it(self):
        data = attribution.load()
        self.assertEqual({p['id'] for p in data['patterns']}, set(SAMPLES))
        for pattern in data['patterns']:
            with self.subTest(pattern['id']):
                self.assertTrue(pattern['compiled'].search(SAMPLES[pattern['id']]))

    def test_data_file_does_not_match_its_own_patterns(self):
        self.assertEqual(attribution.scan_text(attribution.DATA.read_text(encoding='utf-8')), [])

    def test_neutral_text_and_human_trailer_pass(self):
        for text in ('fix: parser\n\nplain body', HUMAN, 'the model generated a list of options', 'Made with care'):
            with self.subTest(text):
                self.assertEqual(attribution.scan_text(text), [])

    def test_scan_reports_class_and_line(self):
        found = attribution.scan_text('subject\n\n' + ROBOT + '\n' + TRAILER)
        self.assertEqual({(c, n) for _, c, n in found if c == 'A'}, {('A', 3)})
        self.assertIn(('B', 4), [(c, n) for _, c, n in found])

    def test_enforce_switch_is_one_data_entry(self):
        data = json.loads(attribution.DATA.read_text(encoding='utf-8'))
        data['enforce'] = ['A']
        path = Path(self._testMethodName + '.json')
        try:
            path.write_text(json.dumps(data), encoding='utf-8')
            self.assertEqual(attribution.scan_text(TRAILER, path), [])
            self.assertTrue(attribution.scan_text(FOOTER, path))
            self.assertFalse(any(e['id'].startswith('attr-coauthor') for e in attribution.leakguard_entries(path)))
        finally:
            path.unlink()

    def test_invalid_data_is_refused(self):
        for bad in ({'schema': 2}, {'schema': 1, 'enforce': ['C'], 'patterns': []},
                    {'schema': 1, 'enforce': ['A'], 'patterns': [{'id': 'X', 'class': 'A', 'regex': 'a'}]},
                    {'schema': 1, 'enforce': ['A'], 'patterns': [{'id': 'x', 'class': 'A', 'regex': '('}]}):
            path = Path(self._testMethodName + '.json')
            try:
                path.write_text(json.dumps(bad), encoding='utf-8')
                with self.assertRaises(attribution.AttributionError):
                    attribution.load(path)
            finally:
                path.unlink()


class LeakguardTests(LeakTestCase):
    def test_text_mode_finds_footer_and_trailer(self):
        repo = self.make_repo()
        for text in (FOOTER, ROBOT, TRAILER, CURSOR, SESSION):
            with self.subTest(text[:12]):
                report = self.check(repo, texts=[('pr-body', 'Opis zmiany\n\n' + text)], mode='text')
                self.assertEqual(report['status'], 'FAIL')
                self.assertEqual({f['category'] for f in report['findings']} & {'attribution'}, {'attribution'})

    def test_text_mode_passes_clean_text_and_human_trailer(self):
        repo = self.make_repo()
        self.assertEqual(self.check(repo, texts=[('pr-body', 'Opis\n\n' + HUMAN)], mode='text')['status'], 'PASS')

    def test_new_commit_is_refused_in_range_and_staged_files_in_every_class(self):
        repo = self.make_repo()
        base = commit_file(repo, 'a.md', 'x\n', 'base')
        head = commit_file(repo, 'b.md', 'y\n', 'feat: b\n\n' + TRAILER)
        report = self.check(repo, mode='range', rev_range=f'{base}..{head}')
        self.assertEqual(report['status'], 'FAIL')
        for target_kind in ('core', 'org', 'personal'):
            with self.subTest(target_kind):
                self.assertEqual(self.check(repo, mode='range', rev_range=f'{base}..{head}', target_kind=target_kind)['status'], 'FAIL')
        for rel in ('docs/x.md', 'skills/x/SKILL.md', 'CHANGELOG.md', 'tests/t.py'):
            with self.subTest(rel):
                tree = self.tree({rel: 'note\n' + FOOTER + '\n'})
                self.assertEqual(self.check(tree)['status'], 'FAIL')

    def test_history_ignores_old_trailers_but_range_does_not_hide_a_new_commit(self):
        repo = self.make_repo()
        old = commit_file(repo, 'a.md', 'x\n', 'old\n\n' + TRAILER)
        self.assertEqual(self.check(repo, mode='history')['status'], 'PASS')
        new = commit_file(repo, 'b.md', 'y\n', 'new\n\n' + TRAILER)
        self.assertEqual(self.check(repo, mode='history')['status'], 'PASS')
        self.assertEqual(self.check(repo, mode='range', rev_range=f'{old}..{new}')['status'], 'FAIL')
        self.assertEqual(self.check(repo, mode='range', rev_range=f'{old}~0')['status'], 'FAIL')

    def test_history_still_finds_other_leaks_in_a_trailer(self):
        repo = self.make_repo()
        commit_file(repo, 'a.md', 'x\n', 'old\n\nCo-Auth' + 'ored-By: Cla' + 'ude ' + 'Brenk' + 'or <' + ADDR + '>')
        report = self.check(repo, [{'term': 'Brenk' + 'or', 'kind': 'org'}], mode='history')
        self.assertEqual(report['status'], 'FAIL')

    def test_builtin_patterns_carry_the_category_and_the_old_allow_is_gone(self):
        patterns = leakguard.load_patterns()
        self.assertEqual({r.category for r in patterns.patterns if r.id.startswith('attr-')}, {'attribution'})
        self.assertNotIn('trailer-noreply', {r.id for r in patterns.allow})


if __name__ == '__main__':
    unittest.main()
