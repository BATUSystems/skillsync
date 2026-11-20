"""K13 (CORE-SPECSTORE, F4.4): the consent button carries `planState`, the server refuses a click made on a stale plan state (409),
and the status build reads the `tracked` flag once. Uses the demo project of the dashboard server tests."""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from skillsync_workflow import dashboard as engine  # noqa: E402
from test_skillsync_dashboard_server import PLAN, RUN, ProjectCase  # noqa: E402

RUN_ID = '2026-01-10-demo-etap-1'
HEAD = 'abc1234def5678'


class ButtonGuard(ProjectCase):
    def offers(self):
        return {a['kind']: a for a in self.board.status(PLAN)['approvals']['actions'] if a['kind'] in ('cp', 'ready')}

    def set_plan_status(self, state):
        plan = self.root / PLAN
        plan.write_text(re.sub(r'(?m)^Status:.*$', f'Status: {state}', plan.read_text(encoding='utf-8'), count=1), encoding='utf-8')

    def entries(self):
        path = self.root / RUN / 'approvals.jsonl'
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []

    def test_the_offer_carries_the_plan_state_of_the_run(self):
        self.assertEqual(self.offers()['cp']['planState'], 'in_progress')
        self.set_plan_status('done')
        self.assertEqual(self.offers()['cp']['planState'], 'done')

    def test_a_click_after_the_plan_was_closed_at_the_same_head_is_409_and_writes_nothing(self):
        self.set_plan_status('in_progress')
        seen = self.offers()['cp']['planState']
        self.set_plan_status('done')                                            # HEAD did not move
        code, body = self.board.approve('cp', RUN_ID, HEAD, PLAN, None, seen)
        self.assertEqual(code, 409, body)
        self.assertIn('Status planu zmienił się', body['error'])
        self.assertEqual(self.entries(), [], 'nothing recorded: the receipt stays open')

    def test_a_matching_click_is_200_and_records_the_state_the_user_saw(self):
        self.set_plan_status('done')
        code, body = self.board.approve('cp', RUN_ID, HEAD, PLAN, None, 'done')
        self.assertEqual(code, 200, body)
        self.assertEqual([(e['word'], e['planState']) for e in self.entries()], [('CP', 'done')])

    def test_a_request_without_the_field_behaves_as_before(self):
        with (self.root / RUN / 'checkpoints.md').open('a', encoding='utf-8') as stream:
            stream.write('- 11:20 zrobione: S9; w toku: —; czeka: CP\n')
        code, body = self.board.approve('cp', RUN_ID, HEAD, PLAN)
        self.assertEqual(code, 200, body)
        self.assertEqual([e['planState'] for e in self.entries()], ['in_progress'])

    def test_an_unknown_plan_state_in_the_request_is_400(self):
        self.assertEqual(self.board.approve('cp', RUN_ID, HEAD, PLAN, None, 'weird')[0], 400)
        self.assertEqual(self.entries(), [])

    def test_build_status_passes_the_tracked_flag_of_the_manifest_to_the_buttons(self):
        self.set_plan_status('done')
        entry = {'schema': 1, 'at': '2026-01-12T10:00:00Z', 'word': 'CP', 'sha': HEAD, 'by': 'u', 'source': 'cli', 'run': RUN_ID}
        (self.root / RUN / 'approvals.jsonl').write_text(json.dumps(entry) + '\n', encoding='utf-8')
        self.assertEqual(self.offers()['cp']['state'], 'after', 'no manifest: an entry without the field counts by sha')
        manifest = self.root / '.github/.skillsync.yaml'
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text('workflow:\n  specs:\n    tracked: false\n', encoding='utf-8')
        self.assertEqual(self.offers()['cp']['state'], 'waiting', 'tracked: false + done plan: the old entry gives no `after`')

    def test_the_client_sends_the_offered_plan_state(self):
        assets = ROOT / 'library/skills/dashboard/assets'
        self.assertIn('planState: a.planState', (assets / 'dashboard-render.js').read_text(encoding='utf-8'))
        self.assertIn('planState', (assets / 'app.js').read_text(encoding='utf-8').split('async function postApprove', 1)[1][:900])


class TrackedMemo(unittest.TestCase):
    @staticmethod
    def build(root, *args):
        return engine.approvals_state(root, *args)

    def test_tracked_is_probed_once_for_both_words(self):
        calls = []
        spec = {'exists': False}
        plan = {'status': 'done', 'statusText': '', 'path': 'p'}
        words = [{'word': 'CP', 'active': True, 'tip': 't', 'target': 'main'}, {'word': 'READY', 'active': True, 'tip': 't', 'target': None}]
        head = 'c' * 40
        log = [{'word': 'CP', 'sha': head, 'at': 'x'}, {'word': 'READY', 'sha': head, 'at': 'x'}]
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / '.skillsync/runs/r1').mkdir(parents=True)
            state = self.build(Path(temp), spec, plan, {'words': words}, 'r1', head, log, [], True, lambda: calls.append(1) or False)
        self.assertEqual(len(calls), 1)
        self.assertEqual([a['state'] for a in state['actions'] if a['kind'] in ('cp', 'ready')], ['waiting', 'waiting'], 'untracked + done + entry without the field')


if __name__ == '__main__':
    unittest.main()
