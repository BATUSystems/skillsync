"""N16R integration: the journal written by the commands of the orchestrator (`agent-run`, `review-round`) is the one the status page reads
(`dashboard_agents.build_agents`, `metrics_rows`). The two sides were written apart; this test keeps their format together."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import agent_runs, dashboard_agents, review_rounds  # noqa: E402

RUN = '2026-11-23-core-1150-n16r'
STAMPS = iter(range(10 ** 6))


class WrittenByTheCommandsReadByThePage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agents-integration-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.directory = self.root / '.skillsync' / 'runs' / RUN
        self.directory.mkdir(parents=True)

    def read(self):
        files = [item.name for item in self.directory.iterdir() if item.suffix == '.md']
        return dashboard_agents.build_agents(self.directory, RUN, None, files)

    def test_a_review_round_from_start_to_freeze_and_the_next_one(self):
        run, number, agent, name = review_rounds.start(self.root, 'spec', None, RUN, None, None, '14:35')
        self.assertEqual((number, name), (1, 'spec-review-1.md'))
        page = self.read()
        self.assertEqual(len(page['running']), 1)
        row = page['running'][0]
        self.assertEqual((row['role'], row['kind'], row['profile'], row['round'], row['eta'], row['etaFirst'], row['etaShifts']),
                         ('reviewer', 'review', 'spec', 1, '14:35', '14:35', 0))
        self.assertEqual(page['recent'], [])
        agent_runs.move_eta(self.root, RUN, agent, '15:05')
        self.assertEqual((self.read()['running'][0]['eta'], self.read()['running'][0]['etaShifts']), ('15:05', 1))
        review_rounds.freeze(self.root, 'spec', 'Wynik: CHANGES_REQUIRED\n\n- a\n', None, RUN)
        page = self.read()
        self.assertEqual(page['running'], [])
        done = page['recent'][0]
        self.assertEqual((done['profile'], done['round'], done['result'], done['report']), ('spec', 1, 'CHANGES_REQUIRED', 'spec-review-1.md'))
        # the second round gets 2, the first stays frozen
        _, second, _, _ = review_rounds.start(self.root, 'spec', None, RUN)
        self.assertEqual(second, 2)
        self.assertEqual(self.read()['running'][0]['round'], 2)

    def test_an_aborted_round_and_a_code_round_with_a_step(self):
        review_rounds.start(self.root, 'code', 'F2.1', RUN)
        review_rounds.start(self.root, 'code', 'F2.1', RUN)   # the first one is closed as ABORTED
        review_rounds.freeze(self.root, 'code', 'Wynik: PASS\n', 'F2.1', RUN)
        page = self.read()
        self.assertEqual(page['running'], [])
        results = sorted((item['result'], item['step'], item['profile']) for item in page['recent'])
        self.assertEqual(results, [('ABORTED', 'F2.1', 'code'), ('PASS', 'F2.1', 'code')])
        self.assertIn('code-review-F2.1-1.md', [item['report'] for item in page['recent'] if item['report']])

    def test_a_long_run_of_another_role_and_the_raw_metrics_rows(self):
        agent = agent_runs.start(self.root, RUN, 'implementer', 'run', None, 'F1.1', None, '14:00')
        agent_runs.move_eta(self.root, RUN, agent, '14:20')
        agent_runs.end(self.root, RUN, 'DONE', agent=agent)
        rows = dashboard_agents.metrics_rows(self.directory, None)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['role'], rows[0]['step'], rows[0]['etaFirst']), ('implementer', 'F1.1', '14:00'))

    def test_a_bad_line_among_the_written_ones_does_not_hide_the_rest(self):
        agent_runs.start(self.root, RUN, 'verifier', 'run', None, 'F1.1', None, '14:00')
        with open(self.directory / 'agents.jsonl', 'ab') as handle:
            handle.write(b'not json\n')
        agent_runs.start(self.root, RUN, 'implementer', 'run', None, 'F1.2', None, '14:05')
        self.assertEqual(len(self.read()['running']), 2)


if __name__ == '__main__':
    unittest.main()
