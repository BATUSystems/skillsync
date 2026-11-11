"""DASH-FIX: automatic plan choice prefers a plan with a live run over a finished, unclosed plan without a run."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from skillsync_workflow import dashboard, quality, status  # noqa: E402

ORPHAN = 'docs/plans/2026-11-07-base.md'
CURRENT = 'docs/plans/2026-11-11-current.md'


def plan_text(status_word, steps, run=None):
    head = f'# Plan: demo\n\nStatus: {status_word}\n' + (f'Przebieg: {run}\n' if run else '')
    body = '\n## Progress\n\n### F1\n' + ''.join(f'- [{"x" if done else " "}] S{i} step {i}\n' for i, done in enumerate(steps, 1))
    return head + body


class PlanChoice(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='plan-choice-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / 'docs/plans').mkdir(parents=True)

    def plan(self, rel, text, mtime=None):
        path = self.root / rel
        path.write_text(text, encoding='utf-8')
        if mtime:
            os.utime(path, (mtime, mtime))

    def run_dir(self, name, checkpoints=True, approvals=False, mtime=None):
        directory = self.root / '.skillsync/runs' / name
        directory.mkdir(parents=True)
        files = []
        if checkpoints:
            (directory / 'checkpoints.md').write_text('RUN=2026-11-11T10:00:00+01:00\n- 10:05 start\n', encoding='utf-8')
            files.append(directory / 'checkpoints.md')
        if approvals:
            (directory / 'approvals.jsonl').write_text('{}\n', encoding='utf-8')
            files.append(directory / 'approvals.jsonl')
        for path in files:
            if mtime:
                os.utime(path, (mtime, mtime))

    def chosen(self):
        return dashboard.choose_plan(dashboard.list_plans(self.root))['path']

    def test_orphan_in_progress_without_run_loses_to_draft_with_live_run(self):
        self.plan(ORPHAN, plan_text('in_progress', [True, True, True], run='base-20261107'))
        self.plan(CURRENT, plan_text('draft', [False, False], run='current-20261111'))
        self.run_dir('current-20261111', approvals=True)
        self.assertEqual(self.chosen(), CURRENT)
        # the dashboard shows the live plan, not "Act III 3/3"
        status_json = dashboard.build_status(self.root, manifest={})
        self.assertEqual(status_json['plan']['path'], CURRENT)
        self.assertTrue(status_json['planAuto'])

    def test_complete_in_progress_with_an_existing_run_still_wins_by_rank(self):
        self.plan(ORPHAN, plan_text('in_progress', [True, True], run='base-20261107'))
        self.plan(CURRENT, plan_text('draft', [False], run='current-20261111'))
        self.run_dir('base-20261107')
        self.run_dir('current-20261111')
        self.assertEqual(self.chosen(), ORPHAN)       # not an orphan: its run exists (open closure of a live run)

    def test_incomplete_in_progress_without_run_keeps_rank(self):
        self.plan(ORPHAN, plan_text('in_progress', [True, False]))
        self.plan(CURRENT, plan_text('draft', [False], run='current-20261111'))
        self.run_dir('current-20261111')
        self.assertEqual(self.chosen(), ORPHAN)

    def test_without_any_live_run_the_previous_order_holds(self):
        self.plan(ORPHAN, plan_text('in_progress', [False]))
        self.plan(CURRENT, plan_text('in_progress', [False]))
        self.assertEqual(self.chosen(), CURRENT)       # dated name, newest first
        self.plan('docs/plans/2026-11-12-draft.md', plan_text('draft', [False]))
        self.assertEqual(self.chosen(), CURRENT)       # rank beats the name

    def test_the_only_orphan_is_still_shown(self):
        self.plan(ORPHAN, plan_text('in_progress', [True, True]))
        self.assertEqual(self.chosen(), ORPHAN)

    def test_freshest_run_activity_wins_within_a_rank(self):
        self.plan('docs/plans/2026-11-09-a.md', plan_text('in_progress', [False], run='a-run'))
        self.plan('docs/plans/2026-11-10-b.md', plan_text('in_progress', [False], run='b-run'))
        self.run_dir('a-run', mtime=2_000_000_000)
        self.run_dir('b-run', mtime=1_900_000_000)
        self.assertEqual(self.chosen(), 'docs/plans/2026-11-09-a.md')   # fresher checkpoints beat the later date
        self.run_dir('c-run', checkpoints=False, approvals=True, mtime=2_100_000_000)
        self.plan('docs/plans/2026-11-08-c.md', plan_text('in_progress', [False], run='c-run'))
        self.assertEqual(self.chosen(), 'docs/plans/2026-11-08-c.md')   # approvals.jsonl counts too

    def test_done_plans_keep_the_dated_name_order_whatever_the_runs(self):
        self.plan('docs/plans/2026-11-05-old.md', plan_text('done', [True], run='old-run'))
        self.plan('docs/plans/2026-11-10-new.md', plan_text('done', [True], run='new-run'))
        self.run_dir('old-run', mtime=2_100_000_000)          # only the older plan still has a local run directory
        self.assertEqual(self.chosen(), 'docs/plans/2026-11-10-new.md')

    def test_approved_orphan_ranks_like_done(self):
        self.plan(ORPHAN, plan_text('approved', [True], run='base-20261107'))
        self.plan(CURRENT, plan_text('draft', [False], run='current-20261111'))
        self.run_dir('current-20261111')
        self.assertEqual(self.chosen(), CURRENT)

    def test_symlinked_run_directory_makes_the_plan_an_orphan(self):
        target = self.root / 'elsewhere'
        target.mkdir()
        (self.root / '.skillsync/runs').mkdir(parents=True)
        os.symlink(target, self.root / '.skillsync/runs/base-20261107')
        self.plan(ORPHAN, plan_text('in_progress', [True], run='base-20261107'))
        self.plan(CURRENT, plan_text('draft', [False], run='current-20261111'))
        self.run_dir('current-20261111')
        self.assertEqual(self.chosen(), CURRENT)

    def test_plan_mtime_decides_a_tie_of_run_activity(self):
        self.plan('docs/plans/2026-11-09-a.md', plan_text('in_progress', [False], run='a-run'), mtime=1_950_000_000)
        self.plan('docs/plans/2026-11-10-b.md', plan_text('in_progress', [False], run='b-run'), mtime=2_050_000_000)
        self.run_dir('a-run', mtime=1_900_000_000)
        self.run_dir('b-run', mtime=1_900_000_000)
        self.assertEqual(self.chosen(), 'docs/plans/2026-11-10-b.md')    # plan mtime is part of the activity

    def test_internal_fields_are_not_in_the_json_contract(self):
        self.plan(CURRENT, plan_text('draft', [False], run='current-20261111'))
        self.run_dir('current-20261111')
        row = dashboard.build_status(self.root, manifest={})['plans'][0]
        self.assertEqual(sorted(row), ['path', 'run', 'spec', 'status', 'title'])


class ClosureReason(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='closure-reason-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / 'docs/plans').mkdir(parents=True)

    def reasons(self, text):
        (self.root / ORPHAN).write_text(text, encoding='utf-8')
        return [reason for rel, reason in quality.plan_closure_findings(self.root) if rel == ORPHAN]

    def test_status_reports_closure_for_a_complete_in_progress_plan(self):
        text = plan_text('in_progress', [True, True], run='base-20261107')
        self.assertEqual(status.plan_text_needs_human('in_progress', text), ['closure'])
        (self.root / ORPHAN).write_text(text, encoding='utf-8')
        self.assertEqual(status.plan_record(self.root, self.root / ORPHAN)['needsHuman'], ['closure'])

    def test_reason_names_the_run_state(self):
        no_line = self.reasons(plan_text('in_progress', [True]))
        self.assertEqual(len(no_line), 1)
        self.assertIn('no `Przebieg:` line', no_line[0])
        missing = self.reasons(plan_text('in_progress', [True], run='base-20261107'))
        self.assertIn('run `base-20261107` does not exist', missing[0])
        (self.root / '.skillsync/runs/base-20261107').mkdir(parents=True)
        open_run = self.reasons(plan_text('in_progress', [True], run='base-20261107'))
        self.assertIn('has no `final` record', open_run[0])
        (self.root / '.skillsync/runs/base-20261107.json').write_text('{"checkpoints": [{"final": true}]}', encoding='utf-8')
        closed = self.reasons(plan_text('in_progress', [True], run='base-20261107'))
        self.assertIn('has a `final` record', closed[0])
        for reason in (no_line + missing + open_run + closed):
            self.assertIn('close it', reason)

    def test_reason_for_an_invalid_run_id(self):
        self.assertIn('invalid run id', self.reasons(plan_text('in_progress', [True], run='.bad'))[0])

    def test_incomplete_plan_is_not_reported(self):
        self.assertEqual(self.reasons(plan_text('in_progress', [True, False])), [])


if __name__ == '__main__':
    unittest.main()
