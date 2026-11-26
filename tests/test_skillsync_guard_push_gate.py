"""CORE-1190-PUSHHOOK: the PreToolUse hook `guard-commands.py` refuses a `git push` that follows a secret scan (`gitleaks`, `leakcheck`) of the SAME
command text with `|`, `;` or a newline in between (a pipe hides the status of the scan, `;` ignores it). The gate is `&&` or a separate command whose
result was read. Two layers: the classifier `push_gate_violation` on a table, and the hook protocol (stdin JSON -> exit code and stderr).
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / 'library' / 'hooks' / 'guard-commands.py'
spec = importlib.util.spec_from_file_location('guard_commands_push_gate', HOOK)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

# (command, refused)
TABLE = [
    # refused: the scan result does not gate the push
    ('gitleaks detect --no-git | tail -3; git commit -m x; git push', True),   # the S5c case
    ('gitleaks detect --no-git | tail; git push origin main', True),
    ('skillsync leakcheck --range a..b; git push', True),
    ('gitleaks detect | tail && git push', True),   # the pipe hides the status of gitleaks, `&&` only looks at `tail`
    ('gitleaks detect && git commit -m x; git push', True),   # `;` after the gate: the push runs whatever the scan found
    ('gitleaks detect\ngit push', True),   # a newline is `;`
    ('gitleaks detect & git push', True),   # in the background
    ('timeout 60 gitleaks detect | tail -1; git -C repo push', True),
    ("bash -c 'gitleaks detect | tail; git push'", True),
    ('git add x; gitleaks detect | tail; git push', True),
    ('gitleaks detect --no-git || exit 1; git push', True),   # deliberate: `;` after the scan; the way out is `&&` or the exception
    ('if gitleaks detect; then git push; fi', True),   # deliberate: the same, spelled with `if`
    # allowed
    ('gitleaks detect && git push', False),
    ('skillsync leakcheck && git push origin main', False),
    ('gitleaks detect --no-git && git commit -m x && git push', False),
    ('git push', False),   # a separate command: the scan result was read before
    ('gitleaks detect | tail -3', False),
    ('gitleaks detect; echo done', False),
    ('echo gitleaks | cat; git push', False),   # not a scan
    ('command -v gitleaks; git push', False),   # asking where the tool is scans nothing
    ('gitleaks --version; git push', False),
    ('skillsync leakcheck --help; git push', False),
    ('git push && gitleaks detect | tail', False),   # the scan comes after
    ('git push origin main', False),
    ("SKILLSYNC_GUARD_EXCEPT='scan read in the previous call' git push", False),
]
# the exception only counts on the push itself (here: after a scan with `;`)
EXCEPTED = "gitleaks detect | tail; SKILLSYNC_GUARD_EXCEPT='scan read in the previous call' git push"


class Classifier(unittest.TestCase):
    def test_table(self):
        for command, refused in TABLE:
            with self.subTest(command=command):
                self.assertIs(guard.push_gate_violation(command), refused)

    def test_the_exception_on_the_push_lets_it_through(self):
        self.assertFalse(guard.push_gate_violation(EXCEPTED))
        self.assertTrue(guard.push_gate_violation(EXCEPTED.replace("SKILLSYNC_GUARD_EXCEPT='scan read in the previous call' ", '')))
        self.assertTrue(guard.push_gate_violation("gitleaks detect | tail; SKILLSYNC_GUARD_EXCEPT='' git push"))   # an empty reason is no exception

    def test_parse_records_the_operator_that_ends_a_command(self):
        commands = guard.parse('a | b; c && d || e & f\ng')
        self.assertEqual([c.sep for c in commands], ['|', ';', '&&', '||', '&', '\n', ''])

    def test_odd_input_never_raises(self):
        for text in ('', ' ', 'gitleaks', 'gitleaks |', '| git push', ';;', 'gitleaks;', "bash -c", "bash -c 'gitleaks | x; git push", 'gitleaks (' * 50):
            with self.subTest(text=text[:30]):
                self.assertIn(guard.push_gate_violation(text), (True, False))


class Protocol(unittest.TestCase):
    def run_hook(self, command, *args, tool='Bash'):
        temp = tempfile.TemporaryDirectory(prefix='skillsync-pushgate-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve() / 'proj'
        (root / '.git').mkdir(parents=True)
        payload = {'tool_name': tool, 'tool_input': {'command': command}, 'cwd': str(root), 'session_id': 's1'}
        env = {**os.environ, 'HOME': str(root)}
        return subprocess.run([sys.executable, str(HOOK), *args], input=json.dumps(payload), capture_output=True, text=True, timeout=30, cwd=root, env=env)

    def test_a_refused_push_is_exit_2_with_the_way_out(self):
        done = self.run_hook('gitleaks detect --no-git | tail -3; git commit -m x; git push')
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn('bramka push', done.stderr)
        self.assertIn('&&', done.stderr)
        self.assertIn('SKILLSYNC_GUARD_EXCEPT', done.stderr)
        self.assertEqual(done.stdout, '')

    def test_the_gate_does_not_depend_on_mode(self):
        done = self.run_hook('gitleaks detect | tail; git push', '--mode', 'off')
        self.assertEqual(done.returncode, 2, done.stderr)

    def test_a_gated_push_and_other_tools_pass(self):
        self.assertEqual(self.run_hook('gitleaks detect --no-git && git push').returncode, 0)
        self.assertEqual(self.run_hook('git push').returncode, 0)
        self.assertEqual(self.run_hook('gitleaks detect | tail; git push', tool='Read').returncode, 0)

    def test_the_exception_passes_and_says_nothing(self):
        done = self.run_hook(EXCEPTED)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn('bramka push', done.stderr)


if __name__ == '__main__':
    unittest.main()
