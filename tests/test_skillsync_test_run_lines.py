"""CORE-1140-T3 S2: integration of `skillsync test-run --format lines` (K2, K5, K6 of `docs/specs/2026-11-22-test-run-format-lines.md`).
The commands are synthetic `python3 -c` scripts that print what a shell `*_checks.sh` prints; the real CLI and recorder decide the status."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_replay as ar  # noqa: E402
from adapter_replay import replay  # noqa: E402
from test_skillsync_test_run import Base  # noqa: E402

from skillsync_workflow import test_adapters  # noqa: E402


def lines_script(*rows, code=0):
    body = '; '.join(f'print({r!r})' for r in rows)
    return f"import sys; {body}; sys.exit({code})"


class LinesRun(Base):
    def lines(self, script, fmt='lines', n=1):
        done = self.run_cli('--step', 'F1.1', '--format', fmt, '--', sys.executable, '-c', script)
        return done, self.attempt(n=n)

    @staticmethod
    def states(state):
        return {t['id']: t['state'] for t in state.tests.values()}

    def counts(self, state):
        return (state.counts['passed'], state.counts['failed'], state.counts['total'])

    def test_k6_checks_script_with_all_pass_summary_is_a_pass_without_pytest_summary(self):
        done, state = self.lines(lines_script('PASS config is valid', 'PASS schema matches', 'PASS names unique', 'ALL PASS (strict)'))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(state.end['status'], 'PASS', state.end)
        self.assertEqual(state.end['source'], 'adapter')
        self.assertEqual(self.counts(state), (3, 0, 3))
        self.assertEqual(self.states(state), {'config is valid': 'passed', 'schema matches': 'passed', 'names unique': 'passed'})
        self.assertTrue(any(p.name == '1.jsonl' for p in (self.root / '.skillsync/runs/R1/tests/F1.1').iterdir()))

    def test_k2_one_fail_line_with_exit_one_is_a_fail_with_the_message(self):
        done, state = self.lines(lines_script('PASS a', 'FAIL b', 'expected 1, got 2', 'PASS c', code=1))
        self.assertEqual(done.returncode, 1)
        self.assertEqual(state.end['status'], 'FAIL', state.end)
        self.assertEqual(self.counts(state), (2, 1, 3))
        self.assertEqual(self.states(state), {'a': 'passed', 'b': 'failed', 'c': 'passed'})
        self.assertIn('expected 1, got 2', state.tests['b'].get('msg') or '')

    def test_k2_exit_zero_without_any_result_line_is_blocked(self):
        done, state = self.lines(lines_script('ALL PASS (strict)'))
        self.assertEqual(done.returncode, 2)   # exit 0 without proof is exit code 2, as for the other adapters
        self.assertEqual(state.end['status'], 'BLOCKED', state.end)
        self.assertEqual(self.states(state), {})

    def test_k2_nonzero_exit_with_only_pass_lines_is_a_fail_with_the_counters(self):
        done, state = self.lines(lines_script('PASS a', 'PASS b', code=3))
        self.assertEqual(done.returncode, 3)
        self.assertEqual(state.end['status'], 'FAIL', state.end)
        self.assertEqual(state.counts['passed'], 2)
        self.assertEqual(self.states(state), {'a': 'passed', 'b': 'passed'})

    def test_k2_fail_line_with_exit_zero_is_settled_like_tap(self):
        """The same shape as `TapRecorder.test_k6_not_ok_with_exit_zero_is_not_a_pass`: BLOCKED, exit 2, reason mentions `failed`, the failed test is kept."""
        done, state = self.lines(lines_script('PASS a', 'FAIL b', 'PASS c', code=0))
        self.assertEqual(done.returncode, 2)
        self.assertEqual(state.end['status'], 'BLOCKED', state.end)
        self.assertIn('failed', state.end['reason'])
        self.assertEqual(self.states(state), {'a': 'passed', 'b': 'failed', 'c': 'passed'})
        self.assertEqual(self.counts(state), (2, 1, 3))

    def test_k5_lines_is_a_known_format_and_listed_in_the_error_for_an_unknown_one(self):
        test_adapters.discover()
        self.assertIn('lines', test_adapters.names())
        done = self.run_cli('--step', 'F1.1', '--format', 'nonexistent', '--', sys.executable, '-c', 'pass')
        self.assertNotEqual(done.returncode, 0)
        self.assertRegex(done.stderr + done.stdout, r'known:[^\n]*\blines\b')

    def test_k5_auto_never_picks_lines(self):
        script = lines_script('PASS a', 'PASS b', 'Ran 2 tests in 0.01s', 'OK')
        done, state = self.lines(script, fmt='auto')
        self.assertEqual(self.states(state), {})   # no adapter emitted tests
        self.assertNotIn('adapter lines', done.stderr)
        done, state = self.lines(lines_script('PASS a', 'ALL PASS (strict)'), fmt='auto', n=2)
        self.assertEqual(self.states(state), {})
        self.assertEqual(state.end['status'], 'BLOCKED', state.end)   # no summary to count from

    def test_replay_fixture_lines_basic(self):
        test_adapters.discover()
        rec, adapter, ctx, complete = ar.replay_fixture('lines', 'lines-basic.jsonl')
        self.assertTrue(complete)
        self.assertEqual({e[1]: e[3] for e in rec.emitted()}, {'config is valid': 'passed', 'schema matches': 'passed'})
        self.assertTrue(ar.load('lines-basic.jsonl')[0].get('format'))


if __name__ == '__main__':
    unittest.main()
