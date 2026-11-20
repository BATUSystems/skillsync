"""Karma output with unexecuted specs and a clean exit code is not evidence of tests: `validate` (`quality.run_validation`) and `test-run` (`TestRecorder`) both BLOCK it."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skillsync_workflow import quality, test_events, test_run  # noqa: E402

LABEL = 'Chrome (Linux)'
REASON = 'Karma executed 3 of 11 specs'


def validate(line, code=0, **extra):
    with tempfile.TemporaryDirectory(prefix='karma-unexec-') as tmp:
        root = Path(tmp) / 'p'
        (root / '.github').mkdir(parents=True)
        (root / '.github/.skillsync.yaml').write_text('schema: 1\n', encoding='utf-8')
        script = f"print({line!r}); raise SystemExit({code})"
        check = {'id': 'k', 'argv': [sys.executable, '-c', script], 'cwd': str(root), 'kind': 'test', 'timeout': 30, **extra}
        return quality.run_validation(root, [check])['results'][0]


def recorded_end(line, code=0):
    with tempfile.TemporaryDirectory(prefix='karma-unexec-') as tmp:
        recorder = test_run.TestRecorder(tmp, 'R1', 'F1.1', 'flag', adapter=None)
        check = {'argv': ['karma', 'start'], 'command': 'karma start', 'cwd': tmp}
        recorder.started(check)
        recorder.line(check, 'out', line)
        recorder.finished(check, {'exit_code': code, 'duration_seconds': 1.0}, line + '\n')
        events = test_events.read_events(recorder.path)[0]
    return [e for e in events if e['t'] == 'end'][-1]


class Validate(unittest.TestCase):
    def test_unexecuted_specs_with_exit_0_are_blocked(self):
        for line, reason in ((f'{LABEL}: Executed 3 of 11 DISCONNECTED', REASON), (f'{LABEL}: Executed 5 of 11 ERROR', 'Karma executed 5 of 11 specs')):
            with self.subTest(line=line):
                result = validate(line)
                self.assertEqual((result['status'], result['reason']), ('BLOCKED', reason))

    def test_a_success_pattern_leaves_the_decision_to_the_user(self):
        result = validate(f'{LABEL}: Executed 3 of 11 DISCONNECTED', success_pattern='Executed')
        self.assertEqual(result['status'], 'PASS', result)

    def test_a_full_green_run_passes(self):
        self.assertEqual(validate(f'{LABEL}: Executed 11 of 11 SUCCESS')['status'], 'PASS')

    def test_skipped_ones_need_a_disposition_not_this_reason(self):
        result = validate(f'{LABEL}: Executed 9 of 11 (skipped 2)')
        self.assertEqual((result['status'], result['reason']), ('BLOCKED', 'Skipped tests require explicit disposition'))

    def test_a_non_zero_exit_stays_fail(self):
        self.assertEqual(validate(f'{LABEL}: Executed 3 of 11 DISCONNECTED', code=1)['status'], 'FAIL')


class TestRun(unittest.TestCase):
    def test_the_end_record_is_blocked_with_the_reason(self):
        end = recorded_end(f'{LABEL}: Executed 3 of 11 DISCONNECTED')
        self.assertEqual((end['status'], end['reason']), ('BLOCKED', REASON), end)

    def test_allowances_do_not_lift_it(self):
        with tempfile.TemporaryDirectory(prefix='karma-unexec-') as tmp:
            recorder = test_run.TestRecorder(tmp, 'R1', 'F1.1', 'flag', adapter=None, allow_no_count='x', allow_skips=True)
            check = {'argv': ['karma', 'start'], 'command': 'karma start', 'cwd': tmp}
            recorder.started(check)
            text = f'{LABEL}: Executed 3 of 11 DISCONNECTED'
            recorder.line(check, 'out', text)
            recorder.finished(check, {'exit_code': 0, 'duration_seconds': 1.0}, text + '\n')
            end = [e for e in test_events.read_events(recorder.path)[0] if e['t'] == 'end'][-1]
        self.assertEqual((end['status'], end['reason']), ('BLOCKED', REASON), end)

    def test_a_green_run_passes_and_a_non_zero_exit_fails(self):
        self.assertEqual(recorded_end(f'{LABEL}: Executed 11 of 11 SUCCESS')['status'], 'PASS')
        self.assertEqual(recorded_end(f'{LABEL}: Executed 3 of 11 DISCONNECTED', code=1)['status'], 'FAIL')


if __name__ == '__main__':
    unittest.main()
