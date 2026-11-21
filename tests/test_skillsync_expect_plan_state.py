"""`approvals record --expect-plan-state` / `status.record_approval(expect_plan_state=...)`: the write is refused (BLOCKED, exit 2,
`code: plan_state_changed`, nothing written) unless the status of the plan of the run read under the file lock equals the expected one."""
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from skillsync_workflow import prototype_server as server  # noqa: E402
from skillsync_workflow import status  # noqa: E402
from tests.test_skillsync_approvals_record import HEAD, RUN, RecordFixture  # noqa: E402

REFUSED = 'approvals record: plan state changed (expected {}, current {}); consent not recorded'


class ExpectPlanState(RecordFixture):
    SHA2 = 'b' * 40

    def setUp(self):
        super().setUp()
        self.plan_path = self.root / 'docs' / 'plans' / 'p.md'
        self.plan_path.parent.mkdir(parents=True)

    def set_plan(self, state, run=RUN):
        self.plan_path.write_text(f'# Plan: demo\n\nStatus: {state}\nPrzebieg: {run}\n', encoding='utf-8')

    def refused(self, report, expected, current):
        self.assertEqual((report['status'], report['entry'], report.get('code')), ('BLOCKED', None, 'plan_state_changed'))
        self.assertEqual(report['reason'], REFUSED.format(expected, current))

    def test_matching_state_is_recorded_with_that_plan_state(self):
        self.set_plan('in_progress')
        report = self.record(expect_plan_state='in_progress')
        self.assertEqual((report['status'], report['entry']['planState']), ('PASS', 'in_progress'))
        self.assertNotIn('code', report)
        self.assertEqual(self.lines()[0]['planState'], 'in_progress')

    def test_changed_state_is_refused_and_nothing_is_written(self):
        self.set_plan('done')
        self.refused(self.record(expect_plan_state='in_progress'), 'in_progress', 'done')
        self.assertFalse(self.log.exists())

    def test_the_cli_exits_2_with_the_same_report(self):
        self.set_plan('done')
        report = self.record_cli('--sha', HEAD, '--expect-plan-state', 'in_progress', code=2)
        self.refused(report, 'in_progress', 'done')
        self.assertFalse(self.log.exists())
        self.assertEqual(self.record_cli('--sha', HEAD, '--expect-plan-state', 'done')['entry']['planState'], 'done')

    def test_unknown_current_state_is_refused(self):
        self.refused(self.record(expect_plan_state='done'), 'done', 'unknown')   # no plan of the run
        self.plan_path.write_text('# Plan: demo\n\nPrzebieg: r1\n', encoding='utf-8')   # no Status line
        self.refused(self.record(expect_plan_state='done'), 'done', 'unknown')
        self.set_plan('weird')   # status outside the list
        self.refused(self.record(expect_plan_state='done'), 'done', 'unknown')
        self.set_plan('done', run='other')   # plan of another run
        self.refused(self.record(expect_plan_state='done'), 'done', 'unknown')
        self.assertFalse(self.log.exists())

    def test_a_plan_over_the_size_limit_counts_as_unknown(self):
        self.plan_path.write_text('# Plan: demo\n\nStatus: done\nPrzebieg: r1\n' + 'x' * (512 * 1024), encoding='utf-8')
        self.refused(self.record(expect_plan_state='done'), 'done', 'unknown')

    def test_without_the_flag_nothing_changes(self):
        self.set_plan('done')
        self.assertEqual(self.record()['entry']['planState'], 'done')
        self.assertEqual(self.record(sha=self.SHA2)['status'], 'PASS')

    def test_value_outside_the_list_is_blocked_with_json_by_the_cli_and_the_api(self):
        self.set_plan('done')
        report = self.record_cli('--sha', HEAD, '--expect-plan-state', 'finished', code=2)
        self.assertEqual((report['status'], report['entry'], report.get('code')), ('BLOCKED', None, None))
        self.assertIn('draft, approved, in_progress, done, archived', report['reason'])
        api = self.record(expect_plan_state='x')
        self.assertEqual((api['status'], api.get('code')), ('BLOCKED', None))
        self.assertFalse(self.log.exists())

    def test_go_words_and_an_explicit_plan_state_exclude_the_flag(self):
        self.set_plan('done')
        doc = self.doc('specs', 's.md')
        report = self.record('GO plan', file=str(doc.relative_to(self.root)), sha=None, expect_plan_state='done')
        self.assertEqual((report['status'], report.get('code')), ('BLOCKED', None))
        self.assertIn('GO plan', report['reason'])
        both = self.record(expect_plan_state='done', plan_state='done')
        self.assertEqual((both['status'], both.get('code')), ('BLOCKED', None))
        self.assertFalse(self.log.exists())

    def test_the_state_is_compared_before_the_duplicate_check(self):
        self.set_plan('in_progress')
        self.assertEqual(self.record(expect_plan_state='in_progress')['status'], 'PASS')
        duplicate = self.record(expect_plan_state='in_progress')
        self.assertEqual((duplicate['status'], duplicate.get('code')), ('BLOCKED', None))   # same state: a duplicate
        self.set_plan('done')
        self.refused(self.record(expect_plan_state='in_progress'), 'in_progress', 'done')   # state first
        self.assertEqual(len(self.lines()), 1)

    def test_the_state_is_read_once_under_the_lock_and_the_entry_carries_it(self):
        self.set_plan('in_progress')
        seen = []
        real = status.plan_state_for_run

        def flip(root, run_id):   # the plan changes right after the read: the entry still carries the compared state
            seen.append((self.log.parent / 'approvals.jsonl.lock').exists())
            state = real(root, run_id)
            self.set_plan('done')
            return state
        with mock.patch.object(status, 'plan_state_for_run', flip):
            report = self.record(expect_plan_state='in_progress')
        self.assertEqual((report['status'], report['entry']['planState'], seen), ('PASS', 'in_progress', [True]))

    def test_a_refusal_leaves_neither_the_approvals_file_nor_the_lock_file(self):
        self.set_plan('done')
        self.refused(self.record(expect_plan_state='in_progress'), 'in_progress', 'done')
        self.assertEqual(sorted(path.name for path in self.log.parent.iterdir()), [])

    def test_a_second_writer_holding_the_lock_decides_on_the_state_after_it_releases(self):
        self.set_plan('in_progress')
        reports = []
        started = threading.Event()

        def writer():
            started.set()
            reports.append(self.record(expect_plan_state='in_progress'))
        with server.FileLock(str(self.log.parent / 'approvals.jsonl.lock'), 30, 0o600, unlink=True):
            thread = threading.Thread(target=writer)
            thread.start()
            started.wait(5)
            thread.join(0.5)
            self.assertTrue(thread.is_alive(), 'the writer waits for the lock')
            self.set_plan('done')   # the plan changes while the other writer holds the lock
        thread.join(30)
        self.refused(reports[0], 'in_progress', 'done')
        self.assertFalse(self.log.exists())

    def test_a_size_refusal_precedes_the_comparison_and_has_no_code(self):
        self.set_plan('done')
        self.log.write_bytes(b'\n' * status.MAX_APPROVALS_BYTES)
        report = self.record(expect_plan_state='in_progress')
        self.assertEqual((report['status'], report.get('code')), ('BLOCKED', None))
        self.assertIn('exceed', report['reason'])

    def test_the_entry_has_the_same_format_as_without_the_flag(self):
        self.set_plan('in_progress')
        with_flag = self.record(expect_plan_state='in_progress')['entry']
        self.log.unlink()
        self.assertEqual(self.record()['entry'], with_flag)


if __name__ == '__main__':
    unittest.main()
