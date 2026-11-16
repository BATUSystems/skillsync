"""`scripts/platform_sensitive.py`: a warning, never a gate, that names the systems needing evidence for a diff.

The cases are today's regressions rebuilt as small synthetic diffs: a stop signal (SIGBREAK), a symlink test that depends on
`core.symlinks`, a CRLF in a pattern, and a directory name taken from data joined to a temporary directory.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts' / 'platform_sensitive.py'


def run_git(repo, *args):
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', '-c', 'commit.gpgsign=false', *args],
                   check=True, capture_output=True, text=True)


class Synthetic(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='skillsync-platsens-'))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        run_git(self.dir, 'init', '-q')
        self.write({'skillsync_workflow/__init__.py': '', 'skillsync_workflow/stopsignal.py': 'import signal\n\n\ndef stop():\n    pass\n',
                    'skillsync_workflow/lines.py': 'import re\nPATTERN = re.compile(r"a")\n', 'tests/__init__.py': '',
                    'tests/test_skillsync_stop.py': 'from skillsync_workflow import stopsignal\n',
                    'tests/test_skillsync_plain.py': 'def test():\n    assert True\n'})
        shutil.copy(ROOT / 'tests' / 'test_skillsync_tmp_path_guard.py', self.dir / 'tests' / 'test_skillsync_tmp_path_guard.py')
        self.base = self.commit('base')

    def write(self, files):
        for name, text in files.items():
            path = self.dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8', newline='')

    def commit(self, message='c'):
        run_git(self.dir, 'add', '-A')
        run_git(self.dir, 'commit', '-q', '-m', message)
        return subprocess.run(['git', '-C', str(self.dir), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()

    def analyse(self, *extra):
        done = subprocess.run([sys.executable, str(SCRIPT), '--repo', str(self.dir), '--base', self.base, '--head', 'HEAD', *extra], capture_output=True, text=True)
        return done

    def result(self):
        done = self.analyse('--json')
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def categories(self, result):
        return {hit['category'] for hit in result['matches']}


class Cases(Synthetic):
    def test_sigbreak(self):
        self.write({'skillsync_workflow/stopsignal.py': 'import signal\n\n\ndef stop():\n    signal.signal(signal.SIGBREAK, handler)\n'})
        self.commit()
        result = self.result()
        self.assertIn('signal', self.categories(result))
        self.assertEqual(sorted(result['evidenceNeeded']), ['linux', 'macos', 'windows'])
        self.assertIn('tests.test_skillsync_stop', result['modules'])  # the tranche: the merge's module set
        self.assertNotIn('tests.test_skillsync_plain', result['modules'])

    def test_symlink_and_core_symlinks(self):
        self.write({'tests/test_skillsync_links.py': "import subprocess\n\n\ndef test(p):\n    subprocess.run(['git', 'config', 'core.symlinks', 'true'])\n    p.symlink_to('x')\n"})
        self.commit()
        result = self.result()
        self.assertIn('symlink', self.categories(result))
        self.assertIn('windows', result['evidenceNeeded'])

    def test_crlf_in_pattern(self):
        self.write({'skillsync_workflow/lines.py': 'import re\nPATTERN = re.compile(r"a\\r\\nb")\n'})
        self.commit()
        result = self.result()
        self.assertEqual(self.categories(result), {'line-ends'})
        self.assertEqual(list(result['evidenceNeeded']), ['windows'])

    def test_directory_name_from_data(self):
        self.write({'tests/test_skillsync_data_names.py': 'def test(self, data):\n    for name in data:\n        (self.tmp / name).mkdir()\n'})
        self.commit()
        result = self.result()
        hit = [h for h in result['matches'] if h['category'] == 'tmp-path-data']
        self.assertEqual([(h['file'], h['text']) for h in hit], [('tests/test_skillsync_data_names.py', 'self.tmp / name')])
        self.assertEqual(hit[0]['line'], 3)

    def test_removed_lines_count_and_backslash_paths_and_sockets(self):
        self.write({'skillsync_workflow/lines.py': 'import re\nimport socket\nS = socket.SO_REUSEADDR\nP = r"C:\\\\Users"\n'})
        self.commit()
        self.assertEqual(self.categories(self.result()), {'socket-option', 'backslash-path'})
        self.base = subprocess.run(['git', '-C', str(self.dir), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
        self.write({'skillsync_workflow/lines.py': 'import re\n'})
        self.commit()
        removed = [h for h in self.result()['matches'] if h['side'] == '-']
        self.assertTrue(removed)

    def test_deleted_file_is_scanned_on_the_removed_side(self):
        self.write({'skillsync_workflow/handler.py': 'import signal\nsignal.signal(signal.SIGBREAK, h)\n'})
        self.base = self.commit('add handler')
        run_git(self.dir, 'rm', '-q', 'skillsync_workflow/handler.py')
        self.commit('rm')
        result = self.result()
        self.assertEqual({(h['file'], h['side'], h['category']) for h in result['matches']}, {('skillsync_workflow/handler.py', '-', 'signal')})

    def test_noprefix_and_color_config_do_not_hide_matches(self):
        run_git(self.dir, 'config', 'diff.noprefix', 'true')
        run_git(self.dir, 'config', 'color.diff', 'always')
        run_git(self.dir, 'config', 'color.ui', 'always')
        self.write({'skillsync_workflow/stopsignal.py': 'import signal\n\n\ndef stop():\n    signal.signal(signal.SIGBREAK, handler)\n'})
        self.commit()
        self.assertIn('signal', self.categories(self.result()))

    def test_backslash_forms_and_os_calls(self):
        self.write({'skillsync_workflow/lines.py': "import os\nA = p.replace('\\\\', '/')\nB = r'C:\\Users'\nos.kill(1, 0)\nos.chmod(p, 0o600)\n"})
        self.commit()
        found = {(h['category']) for h in self.result()['matches']}
        self.assertTrue({'backslash-path', 'signal', 'posix-api'} <= found, found)
        lines = {h['line'] for h in self.result()['matches'] if h['category'] == 'backslash-path'}
        self.assertEqual(lines, {2, 3})

    def test_edited_docstring_on_removed_side_is_not_code(self):
        self.write({'skillsync_workflow/lines.py': 'import re\n"""Notes on os.name and SIGBREAK."""\nPATTERN = 1\n'})
        self.base = self.commit('doc')
        # the docstring moves down: old line 2 is prose in the merge-base file, new line 2 is code
        self.write({'skillsync_workflow/lines.py': 'import re\nA = 1\nB = 2\n"""Other notes."""\nPATTERN = 1\n'})
        self.commit()
        self.assertEqual(self.result()['matches'], [])

    def test_multiple_hunks_number_lines_correctly(self):
        body = ''.join(f'x{i} = 1\n' for i in range(30))
        self.write({'skillsync_workflow/lines.py': body})
        self.base = self.commit('many')
        lines = body.splitlines()
        lines[4] = 'import os; os.getuid()'
        lines[25] = 'import fcntl'
        self.write({'skillsync_workflow/lines.py': '\n'.join(lines) + '\n'})
        self.commit()
        self.assertEqual(sorted(h['line'] for h in self.result()['matches']), [5, 26])

    def test_checkout_must_be_head(self):
        run_git(self.dir, 'checkout', '-q', '-b', 'feature')
        self.write({'skillsync_workflow/lines.py': 'import fcntl\n'})
        self.commit()
        run_git(self.dir, 'checkout', '-q', self.base)
        done = subprocess.run([sys.executable, str(SCRIPT), '--repo', str(self.dir), '--base', self.base, '--head', 'feature'], capture_output=True, text=True)
        self.assertEqual(done.returncode, 2)

    def test_missing_guard_in_analysed_tree_is_not_fatal(self):
        run_git(self.dir, 'rm', '-q', 'tests/test_skillsync_tmp_path_guard.py')
        self.base = self.commit('no guard')
        self.write({'tests/test_skillsync_a.py': 'def t(self, n):\n    (self.tmp / n).mkdir()\n', 'tests/test_skillsync_b.py': 'def t(self, n):\n    (self.tmp / n).mkdir()\n'})
        self.commit()
        self.assertEqual(self.analyse().returncode, 0)

    def test_incompatible_guard_in_analysed_tree_is_not_fatal(self):
        (self.dir / 'tests' / 'test_skillsync_tmp_path_guard.py').write_text('raise SystemExit(3)\n', encoding='utf-8')
        self.base = self.commit('broken guard')
        self.write({'tests/test_skillsync_a.py': 'def t(self, n):\n    (self.tmp / n).mkdir()\n'})
        self.commit()
        self.assertEqual(self.analyse().returncode, 0)
        (self.dir / 'tests' / 'test_skillsync_tmp_path_guard.py').write_text('def scan(tree):\n    raise RuntimeError("x")\n', encoding='utf-8')
        self.base = self.commit('raising guard')
        self.write({'tests/test_skillsync_c.py': 'def t(self, n):\n    (self.tmp / n).mkdir()\n'})
        self.commit()
        self.assertEqual(self.analyse().returncode, 0)

    def test_docstrings_are_not_code(self):
        self.write({'skillsync_workflow/lines.py': 'import re\n"""Notes on os.name, SIGBREAK and symlink behaviour."""\nPATTERN = 1\n'})
        self.commit()
        self.assertEqual(self.result()['matches'], [])

    def test_plain_change_has_no_matches_and_exit_is_zero(self):
        self.write({'skillsync_workflow/lines.py': 'import re\nPATTERN = re.compile(r"b")\n# os.name in a comment\n'})
        self.commit()
        done = self.analyse()
        self.assertEqual(done.returncode, 0)
        self.assertIn('no system-dependent code touched', done.stdout)

    def test_text_output_is_a_warning_with_systems_and_modules(self):
        self.write({'skillsync_workflow/stopsignal.py': 'import signal\n\n\ndef stop():\n    signal.signal(signal.SIGBREAK, handler)\n'})
        self.commit()
        out = self.analyse().stdout
        self.assertIn('WARNING', out)
        self.assertIn('does not block anything', out)
        self.assertIn('Evidence needed from: ', out)
        self.assertIn('test_skillsync_stop', out)

    def test_powershell_file_and_unusable_input(self):
        self.write({'tools/run.ps1': 'Write-Host x\n'})
        self.commit()
        self.assertIn('windows-api', self.categories(self.result()))
        done = subprocess.run([sys.executable, str(SCRIPT), '--repo', str(self.dir), '--base', 'nope', '--head', 'HEAD'], capture_output=True, text=True)
        self.assertEqual(done.returncode, 2)


if __name__ == '__main__':
    unittest.main()
