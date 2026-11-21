"""CORE-EXPECTPLAN F2: the consent button passes the state it showed to the writer as `expect_plan_state`; the writer decides under the file lock,
the server maps its refusal (`code: plan_state_changed`) to 409 with the same `code` in the body. Uses the demo project of the dashboard server tests."""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from skillsync_workflow import dashboard_http, status  # noqa: E402
from test_skillsync_dashboard_server import PLAN, RUN, ProjectCase  # noqa: E402

RUN_ID = '2026-01-10-demo-etap-1'
HEAD = 'abc1234def5678'


class ExpectPlanButton(ProjectCase):
    def set_plan_status(self, state):
        plan = self.root / PLAN
        plan.write_text(re.sub(r'(?m)^Status:.*$', f'Status: {state}', plan.read_text(encoding='utf-8'), count=1), encoding='utf-8')

    def entries(self):
        path = self.root / RUN / 'approvals.jsonl'
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []

    def test_a_stale_click_is_409_with_the_code_and_the_unchanged_message(self):
        self.set_plan_status('done')
        code, body = self.board.approve('cp', RUN_ID, HEAD, PLAN, None, 'in_progress')
        self.assertEqual(code, 409, body)
        self.assertEqual(body, {'error': 'Status planu zmienił się od wyświetlenia; odśwież stronę i sprawdź plan.',
                                'code': 'plan_state_changed'})
        self.assertEqual(self.entries(), [])

    def test_the_server_passes_the_state_as_the_expectation_not_as_the_value_to_write(self):
        self.set_plan_status('done')
        real = status.record_approval
        calls = []

        def spy(*args, **kwargs):
            calls.append({key: kwargs.get(key) for key in ('plan_state', 'expect_plan_state')})
            return real(*args, **kwargs)
        with mock.patch.object(status, 'record_approval', spy):
            code, body = self.board.approve('cp', RUN_ID, HEAD, PLAN, None, 'done')
        self.assertEqual(code, 200, body)
        self.assertEqual(calls, [{'plan_state': None, 'expect_plan_state': 'done'}])
        self.assertEqual([entry['planState'] for entry in self.entries()], ['done'])

    def test_a_plan_that_changes_after_the_status_was_built_is_refused_by_the_writer(self):
        self.set_plan_status('done')
        real = status.plan_state_for_run

        def reopen_before_the_read(root, run_id):   # reopened after the server read its offer, before the writer reads it
            self.set_plan_status('in_progress')
            return real(root, run_id)
        with mock.patch.object(status, 'plan_state_for_run', reopen_before_the_read):
            code, body = self.board.approve('cp', RUN_ID, HEAD, PLAN, None, 'done')
        self.assertEqual((code, body.get('code')), (409, 'plan_state_changed'), body)
        self.assertEqual(self.entries(), [])

    def test_a_plan_that_disappeared_is_409_as_well(self):
        self.set_plan_status('done')
        with mock.patch.object(status, 'plan_state_for_run', lambda root, run_id: None):
            code, body = self.board.approve('cp', RUN_ID, HEAD, PLAN, None, 'done')
        self.assertEqual((code, body.get('code')), (409, 'plan_state_changed'), body)
        self.assertEqual(self.entries(), [])

    def test_another_refusal_of_the_writer_stays_500_without_a_code(self):
        self.set_plan_status('done')
        refusal = {'schema': 1, 'status': 'BLOCKED', 'reason': 'Approvals file is locked', 'entry': None}
        with mock.patch.object(status, 'record_approval', lambda *args, **kwargs: refusal):
            code, body = self.board.approve('cp', RUN_ID, HEAD, PLAN, None, 'done')
        self.assertEqual(code, 500, body)
        self.assertNotIn('code', body)

    def test_the_server_keeps_no_comparison_of_its_own(self):
        source = Path(dashboard_http.__file__).read_text(encoding='utf-8')
        self.assertNotIn('offer.get("planState")', source)


if __name__ == '__main__':
    unittest.main()
