"""D48: `list_plans` parses a plan once per (path, mtime_ns, size); activity of a run is never cached."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from skillsync_workflow import dashboard  # noqa: E402

PLAN = 'docs/plans/2026-11-15-demo.md'


def plan_text(status_word='draft', steps=(False, False), run=None, title='demo'):
    head = f'# Plan: {title}\n\nStatus: {status_word}\n' + (f'Przebieg: {run}\n' if run else '')
    body = '\n## Progress\n\n### F1\n' + ''.join(f'- [{"x" if done else " "}] S{i} step {i}\n' for i, done in enumerate(steps, 1))
    return head + body


class PlanCache(unittest.TestCase):
    def setUp(self):
        dashboard._PLAN_CACHE.clear()
        self.addCleanup(dashboard._PLAN_CACHE.clear)
        self.root = Path(tempfile.mkdtemp(prefix='plan-cache-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / 'docs/plans').mkdir(parents=True)
        self.path = self.root / PLAN

    def write(self, text, ns=None):
        self.path.write_text(text, encoding='utf-8')
        if ns is not None:
            os.utime(self.path, ns=(ns, ns))

    def reads(self):
        """Number of plan reads (`read_text` of the plan) made by one `list_plans`."""
        real, calls = dashboard.read_text, []

        def counting(path, *args, **kwargs):
            calls.append(Path(path).name)
            return real(path, *args, **kwargs)

        with mock.patch.object(dashboard, 'read_text', counting):
            dashboard.list_plans(self.root)
        return len(calls)

    def test_second_call_does_not_read_the_plan_again_and_gives_the_same_answer(self):
        self.write(plan_text(steps=(True, False)))
        self.assertGreater(self.reads(), 0)
        first = dashboard.list_plans(self.root)
        self.assertEqual(self.reads(), 0, 'unchanged plan: no read')
        self.assertEqual(dashboard.list_plans(self.root), first)
        dashboard._PLAN_CACHE.clear()
        self.assertEqual(dashboard.list_plans(self.root), first, 'the cache never changes the answer')

    def test_a_changed_size_or_mtime_is_parsed_again(self):
        self.write(plan_text(steps=(True, False)), ns=1_700_000_000_000_000_000)
        self.assertFalse(dashboard.list_plans(self.root)[0]['complete'])
        self.write(plan_text(steps=(True,)), ns=1_700_000_000_000_000_000)           # another size, the same mtime
        self.assertTrue(dashboard.list_plans(self.root)[0]['complete'])
        self.write(plan_text(status_word='approved', steps=(True, True)), ns=1_700_000_000_000_000_000)   # again another size
        self.assertEqual(dashboard.list_plans(self.root)[0]['status'], 'approved')
        text = plan_text(status_word='approved', steps=(True, False))                # the same size, another mtime
        self.assertEqual(len(text), len(plan_text(status_word='approved', steps=(True, True))))
        self.write(text, ns=1_700_000_100_000_000_000)
        self.assertFalse(dashboard.list_plans(self.root)[0]['complete'])

    def test_run_activity_is_not_cached(self):
        run = self.root / '.skillsync/runs/demo-run'
        run.mkdir(parents=True)
        (run / 'checkpoints.md').write_text('RUN=2026-11-15T10:00:00+01:00\n- 10:05 start\n', encoding='utf-8')
        os.utime(run / 'checkpoints.md', ns=(1_700_000_000_000_000_000,) * 2)
        self.write(plan_text(run='demo-run'), ns=1_600_000_000_000_000_000)
        before = dashboard.list_plans(self.root)[0]
        self.assertTrue(before['runAlive'])
        os.utime(run / 'checkpoints.md', ns=(1_700_000_500_000_000_000,) * 2)       # the plan itself is untouched
        self.assertEqual(self.reads(), 0)
        after = dashboard.list_plans(self.root)[0]
        self.assertGreater(after['activity'], before['activity'])
        shutil.rmtree(run)
        self.assertFalse(dashboard.list_plans(self.root)[0]['runAlive'], 'a removed run directory is seen at once')

    def test_removed_plan_disappears_and_cache_is_bounded(self):
        self.write(plan_text())
        self.assertEqual(len(dashboard.list_plans(self.root)), 1)
        self.path.unlink()
        self.assertEqual(dashboard.list_plans(self.root), [])
        with mock.patch.object(dashboard, 'PLAN_CACHE_MAX', 3):
            for index in range(8):
                (self.root / f'docs/plans/p{index}.md').write_text(plan_text(title=f'p{index}'), encoding='utf-8')
            self.assertEqual(len(dashboard.list_plans(self.root)), 8)
        self.assertLessEqual(len(dashboard._PLAN_CACHE), 3)


if __name__ == '__main__':
    unittest.main()
