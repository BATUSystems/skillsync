"""`skillsync leakcheck --text FILE` / `--stdin`: the publication check for text outside git (PR and release descriptions).

All names that the guard must catch are SYNTHETIC and composed at runtime (no real names, no token literals).
Every test runs in a temporary directory; nothing outside it is touched.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cli_module import SKILLSYNC_PATH  # noqa: E402
from skillsync_workflow import leakguard  # noqa: E402

CLIENT = 'Brenk' + 'or'              # client organisation term (synthetic)
NICK = 'Skrz' + 'at'                 # owner nick (synthetic)
EVENT = 'Ha' + 'ckfest'              # event name (synthetic)
TARGETS = {'mine': {'scope': 'public', 'kind': 'core'}, 'other': {'scope': 'private'}}


class TextCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='leakcheck-text-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        terms = [{'term': CLIENT, 'kind': 'org'}, {'term': NICK, 'kind': 'owner-nick'}, {'term': EVENT, 'kind': 'project'}]
        self.identity = self.root / 'other.yaml'
        self.identity.write_text(yaml.safe_dump({'schema': 1, 'layer': 'other', 'terms': terms}), encoding='utf-8')
        self.targets = self.root / 'targets.json'
        self.targets.write_text(json.dumps(TARGETS), encoding='utf-8')

    def write(self, name, text):
        path = self.root / name
        path.write_text(text, encoding='utf-8')
        return path

    def api(self, texts, **kwargs):
        return leakguard.leakcheck(str(self.root), mode='text', texts=texts, target='mine', targets=TARGETS,
                                   identity_files=[self.identity], **kwargs)

    def cli(self, *args, stdin=None):
        argv = [sys.executable, str(SKILLSYNC_PATH), 'leakcheck', *args, '--identity', str(self.identity),
                '--targets-json', str(self.targets), '--target', 'mine']
        return subprocess.run(argv, input=stdin, capture_output=True, text=True, cwd=str(self.root), timeout=60)


class TextApiTests(TextCase):
    def test_clean_text_passes_without_a_git_repository(self):
        report = self.api([('opis.md', 'Zmiana dotyczy walidacji; testy przechodzą.\n')])
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['findings'], [])

    def test_identity_terms_are_found_with_label_category_and_line(self):
        report = self.api([('opis.md', 'pierwsza linia\nklient %s prosił o zmianę\n' % CLIENT)])
        self.assertEqual(report['status'], 'FAIL')
        (finding,) = report['findings']
        self.assertEqual((finding['path'], finding['category']), ('opis.md', 'identity-org'))
        self.assertTrue(finding['location'].startswith('2:'), finding)
        self.assertNotIn(CLIENT, json.dumps(report), 'raport nie powtarza trafionego tekstu')

    def test_a_text_is_scanned_in_the_strictest_class(self):
        body = 'dziękuję, %s\n' % NICK
        # w CHANGELOG (atrybucje) nick właściciela w rdzeniu bywa dozwolony, w opisie publikacji nie
        self.assertEqual(leakguard.scan_texts([('CHANGELOG.md', body)], leakguard.prepare(
            target='mine', targets=TARGETS, identity_files=[self.identity]))['status'], 'PASS')
        self.assertEqual(self.api([('opis.md', body)])['status'], 'FAIL')

    def test_same_hits_as_scanning_the_text_as_an_instruction_file(self):
        body = 'a %s b\nc %s d\n' % (CLIENT, EVENT)
        prepared = leakguard.prepare(target='mine', targets=TARGETS, identity_files=[self.identity])
        expected = leakguard.scan_texts([('skills/x.md', body)], prepared)['findings']
        got = self.api([('opis.md', body)])['findings']
        self.assertEqual([(f['category'], f['location']) for f in got], [(f['category'], f['location']) for f in expected])

    def test_require_full_blocks_without_coverage(self):
        report = leakguard.leakcheck(str(self.root), mode='text', texts=[('a.md', 'x')], target='mine', targets=TARGETS,
                                     require='full')
        self.assertEqual(report['status'], 'BLOCKED')

    def test_text_mode_without_a_text_is_blocked(self):
        self.assertEqual(self.api([])['status'], 'BLOCKED')

    def test_empty_text_is_blocked_not_passed(self):
        for body in ('', '  \n\t\n'):
            with self.subTest(body=body):
                self.assertEqual(self.api([('a.md', body)])['status'], 'BLOCKED')

    def test_oversized_text_is_blocked(self):
        report = self.api([('big.md', 'a' * (leakguard.TEXT_LIMIT_BYTES + 1))])
        self.assertEqual(report['status'], 'BLOCKED')


class TextCliTests(TextCase):
    def test_file_with_a_leak_fails_with_exit_1_and_never_prints_the_text(self):
        path = self.write('pr.md', 'Opis\n\nzgłosił %s\n' % CLIENT)
        done = self.cli('--text', str(path))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('pr.md 3:', done.stderr)
        self.assertIn('[identity-org]', done.stderr)
        self.assertNotIn(CLIENT, done.stdout + done.stderr)
        as_json = self.cli('--text', str(path), '--json')
        self.assertEqual(as_json.returncode, 1)
        report = json.loads(as_json.stdout)
        self.assertEqual(report['findings'][0]['path'], 'pr.md')
        self.assertNotIn(CLIENT, as_json.stdout + as_json.stderr)

    def test_clean_file_passes_with_exit_0(self):
        path = self.write('pr.md', 'Czysty opis zmiany.\n')
        done = self.cli('--text', str(path))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('leakcheck: PASS', done.stdout)

    def test_stdin_is_scanned_and_labelled(self):
        done = self.cli('--stdin', stdin='wydarzenie %s w opisie\n' % EVENT)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('<stdin> 1:', done.stderr)
        self.assertNotIn(EVENT, done.stdout + done.stderr)
        self.assertEqual(self.cli('--stdin', stdin='nic szczególnego\n').returncode, 0)

    def test_missing_file_and_binary_are_blocked_with_exit_2(self):
        missing = self.cli('--text', str(self.root / 'nie-ma.md'))
        self.assertEqual(missing.returncode, 2)
        self.assertIn('BLOCKED', missing.stderr)
        binary = self.root / 'bin.md'
        binary.write_bytes(b'abc\x00def')
        self.assertEqual(self.cli('--text', str(binary)).returncode, 2)

    def test_text_excludes_other_modes(self):
        path = self.write('pr.md', 'x\n')
        for extra in ('--stdin', '--staged', '--worktree', '--history'):
            with self.subTest(extra):
                done = self.cli('--text', str(path), extra, stdin='')
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)

    def test_empty_path_argument_is_the_text_mode_and_never_a_worktree_scan(self):
        self.write('leak.md', 'klient %s\n' % CLIENT)           # katalog roboczy ma wyciek: skan worktree dałby FAIL
        done = self.cli('--text', '')
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn('BLOCKED', done.stderr)
        self.assertNotIn('leak.md', done.stdout + done.stderr)

    def test_empty_whitespace_directory_and_bad_encoding_are_blocked(self):
        cases = {'pusty': b'', 'białe znaki': b' \n\t\n', 'nie-UTF-8': 'klient Żuraw\n'.encode('cp1250'), 'NUL': b'a\x00b'}
        for name, data in cases.items():
            with self.subTest(name):
                path = self.root / 'case.md'
                path.write_bytes(data)
                done = self.cli('--text', str(path))
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(self.cli('--text', str(self.root)).returncode, 2)         # katalog
        self.assertEqual(self.cli('--stdin', stdin='  \n').returncode, 2)

    def test_utf8_bom_is_stripped_and_the_text_is_still_scanned(self):
        path = self.root / 'bom.md'
        path.write_bytes(b'\xef\xbb\xbf' + ('klient %s\n' % CLIENT).encode('utf-8'))
        self.assertEqual(self.cli('--text', str(path)).returncode, 1)

    def test_oversized_text_is_blocked_for_a_file_and_for_stdin(self):
        big = 'a' * (leakguard.TEXT_LIMIT_BYTES + 1)
        path = self.write('big.md', big)
        self.assertEqual(self.cli('--text', str(path)).returncode, 2)
        self.assertEqual(self.cli('--stdin', stdin=big).returncode, 2)
        self.assertEqual(self.cli('--text', str(self.write('edge.md', 'b' * leakguard.TEXT_LIMIT_BYTES))).returncode, 0)

    def test_require_full_applies_to_the_text_mode(self):
        partial = dict(TARGETS, third={'scope': 'private'})        # cel bez pliku tożsamości: pokrycie częściowe
        targets = self.root / 'targets-partial.json'
        targets.write_text(json.dumps(partial), encoding='utf-8')
        path = self.write('pr.md', 'czysty opis\n')
        argv = [sys.executable, str(SKILLSYNC_PATH), 'leakcheck', '--text', str(path), '--identity', str(self.identity),
                '--targets-json', str(targets), '--target', 'mine', '--require', 'full']
        done = subprocess.run(argv, capture_output=True, text=True, cwd=str(self.root), timeout=60)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(self.cli('--text', str(path), '--require', 'full').returncode, 0)   # pełne pokrycie: PASS

    def test_the_text_is_never_an_argument_of_the_command(self):
        done = subprocess.run([sys.executable, str(SKILLSYNC_PATH), 'leakcheck', '--help'], capture_output=True, text=True, timeout=60)
        self.assertIn('--text FILE', done.stdout)
        self.assertIn('--stdin', done.stdout)


if __name__ == '__main__':
    unittest.main()
