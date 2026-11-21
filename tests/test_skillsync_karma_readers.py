"""The readers of the Karma `Executed N of M` line (the adapter, the live reader of `TestRecorder` without an adapter, `quality._test_counts` with
`test_run._failure_count`) agree (four readers: the adapter, the live reader, the summary reader and the failure counter) on every recorded Karma stream, and the shared line rule rejects what only looks like a counter."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_replay as ar  # noqa: E402

from skillsync_workflow import quality, test_adapters, test_events, test_run  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = (
    'karma-progress.jsonl', 'karma-junit-progress.jsonl', 'karma-dots.jsonl', 'karma-interrupted-progress.jsonl',
    'angular-karma-mixed.jsonl', 'angular-karma-pass.jsonl',
)
LABEL = 'Chrome Headless 157.0.0.0 (Linux 0.0.0)'
KEYS = ('passed', 'failed', 'skipped', 'total')


def setUpModule():
    test_adapters.discover()


def stream_lines(fixture):
    """The lines of the recorded output the way a reader of the pipe sees them, and the exit code of the recorded run."""
    meta, chunks = ar.load(fixture)
    text = ''.join(c['data'] for c in chunks)
    return [line.rstrip('\r') for line in text.split('\n')], meta['exit']


def pick(counts):
    return tuple(counts[k] for k in KEYS)


def adapter_counts(fixture):
    recorder = ar.replay_fixture('karma', fixture)[0]
    series = [c[1] for c in recorder.calls if c[0] == 'counts']
    return pick(series[-1])


def live_counts(lines):
    recorder = test_run.TestRecorder('.', 'R1', 'F1.1', 'flag', adapter=None)
    recorder.writer = mock.Mock()
    for line in lines:
        recorder.line({}, 'out', line)
    if not recorder.writer.counts.called:
        return None
    return pick(recorder.writer.counts.call_args.kwargs)


def quality_counts(lines):
    output = '\n'.join(lines)
    run, skipped = quality._test_counts(output)
    failed = test_run._failure_count(output)
    return (run - failed, failed, skipped, run + skipped), (run, skipped), failed


class Parity(unittest.TestCase):
    def test_every_karma_fixture_gives_the_same_numbers_in_all_readers(self):
        for fixture in FIXTURES:
            with self.subTest(fixture=fixture):
                lines, code = stream_lines(fixture)
                expected = adapter_counts(fixture)
                self.assertEqual(live_counts(lines), expected, 'live reader')
                summary = quality_counts(lines)[0]
                if 'interrupted' in fixture:   # the summary of an unfinished run cannot know the unexecuted: its total is executed + skipped
                    self.assertEqual(summary, expected[:3] + (expected[2] + 4,), 'quality._test_counts + _failure_count')
                else:
                    self.assertEqual(summary, expected, 'quality._test_counts + _failure_count')

    def test_the_progress_stream_is_7_2_2_11(self):
        lines, code = stream_lines('karma-progress.jsonl')
        self.assertEqual(adapter_counts('karma-progress.jsonl'), (7, 2, 2, 11))
        self.assertEqual(live_counts(lines), (7, 2, 2, 11))
        counts, (run, skipped), failed = quality_counts(lines)
        self.assertEqual((counts, run, skipped, failed), ((7, 2, 2, 11), 9, 2, 2))

    def test_reversed_parentheses_read_the_same(self):
        for text in ('%s: Executed 9 of 11 (2 FAILED) (skipped 2)' % LABEL, '%s: Executed 9 of 11 (skipped 2) (2 FAILED)' % LABEL):
            with self.subTest(text=text):
                self.assertEqual(live_counts([text]), (7, 2, 2, 11))
                self.assertEqual(quality_counts([text])[0], (7, 2, 2, 11))


def end_record(fixture):
    """The `end` event of a run whose output is the fixture, through the real recorder WITHOUT an adapter."""
    meta, chunks = ar.load(fixture)
    argv = list(meta['command'])
    with tempfile.TemporaryDirectory(prefix='karma-readers-') as tmp:
        recorder = test_run.TestRecorder(tmp, 'R1', 'F1.1', 'flag', adapter=None)
        check = {'argv': argv, 'command': ' '.join(argv), 'cwd': tmp}
        recorder.started(check)
        text = ''.join(c['data'] for c in chunks)
        for line in text.split('\n'):
            recorder.line(check, 'out', line.rstrip('\r'))
        recorder.finished(check, {'exit_code': meta['exit'], 'duration_seconds': 1.0}, text)
        events = test_events.read_events(recorder.path)[0]
    return [e for e in events if e['t'] == 'end'][-1]


class Interrupted(unittest.TestCase):
    LINE = LABEL + ': Executed 4 of 11 (1 FAILED) (skipped 2) (0 secs / 1.5 secs)'

    def test_skipped_is_the_parenthesis_not_the_unexecuted(self):
        lines, _ = stream_lines('karma-interrupted-progress.jsonl')
        self.assertEqual(quality._test_counts('\n'.join(lines)), (4, 2))
        self.assertEqual(live_counts(lines)[2], 2)

    def test_a_line_without_the_parenthesis_has_no_skipped(self):
        self.assertEqual(quality._test_counts(LABEL + ': Executed 4 of 11 SUCCESS'), (4, 0))
        self.assertEqual(live_counts([LABEL + ': Executed 4 of 11 SUCCESS']), (4, 0, 0, 11))

    def test_the_end_total_without_an_adapter_is_executed_plus_skipped(self):
        end = end_record('karma-interrupted-progress.jsonl')
        self.assertEqual(end['counts']['total'], 6, end)
        self.assertEqual(end['counts']['skipped'], 2, end)
        # the recorded exit code is 0: the product never reads failures out of a log line then, so the end record shows 4 passed and 0 failed
        # although the last Karma line says `(1 FAILED)`
        self.assertEqual((end['counts']['passed'], end['counts']['failed']), (4, 0), end)


class LineRule(unittest.TestCase):
    def read(self, *lines):
        return live_counts(list(lines)), quality._test_counts('\n'.join(lines))

    def test_what_only_looks_like_a_counter_is_ignored(self):
        for line in ("Chrome Headless 157.0.0.0 (Linux 0.0.0) LOG: 'Job: Executed 5 of 8 steps'",
                     "LOG: 'Job: Executed 5 of 8 steps'",
                     'Executed 5 of 8 steps',
                     LABEL + ': Executed 5 of 8 steps done',
                     'Chrome 1 (Linux 0): Executed 5 of 8 steps done',
                     'math suite: Executed 1 of 2',
                     'the test: Executed 1 of 2 asserts passed',
                     'Chrome: Executed 3 of 10'):
            with self.subTest(line=line):
                live, (run, skipped) = self.read(line)
                self.assertIsNone(live)
                self.assertIsNone(run)

    def test_what_karma_prints_is_read(self):
        for line, expected in (('Executed 5 of 8', (5, 0, 0, 8)),
                               ('Executed 5 of 8 SUCCESS', (5, 0, 0, 8)),
                               ('Executed 5 of 8 (1 FAILED)', (4, 1, 0, 8)),
                               ('Executed 5 of 8 ERROR', (5, 0, 0, 8)),
                               ('Executed 5 of 8 DISCONNECTED', (5, 0, 0, 8)),
                               ('Executed 5 of 8 FAILED', (5, 0, 0, 8)),
                               (LABEL + ': Executed 5 of 8 (skipped 2) (0 secs / 1 secs)', (5, 0, 2, 8)),
                               (LABEL + ': Executed 5 of 8 (skipped 2) (1 FAILED)', (4, 1, 2, 8))):
            with self.subTest(line=line):
                self.assertEqual(self.read(line)[0], expected)

    def test_known_difference_the_shared_rule_does_not_judge_the_tail_after_the_status(self):
        # deliberately wider than the adapter: after `of M` a status word or a parenthesis is enough, the rest of the line is not judged
        for line in ('Chrome (Linux): Executed 5 of 8 SUCCESS anything', 'Chrome (Linux): Executed 5 of 8 (in 3 files)'):
            with self.subTest(line=line):
                self.assertEqual(self.read(line)[0], (5, 0, 0, 8))
                recorder = ar.Recorder()
                adapter = test_adapters.resolve('karma', ['karma', 'start'], 'karma')
                ctx = test_adapters.AdapterContext(['karma', 'start'], ROOT, ROOT, recorder)
                adapter.begin(ctx)
                adapter.feed('out', line, ctx)
                self.assertEqual([c for c in recorder.calls if c[0] == 'counts'], [])

    def test_a_line_over_the_limit_is_ignored_fast(self):
        line = LABEL + ': Executed 1 of 2' + ' (' * 50000
        self.assertEqual(self.read('x' * 100000, line, 'Executed ' + '1' * 100000 + ' of 2')[0], None)

    def test_ansi_before_the_label_does_not_double_the_sum(self):
        lines = [LABEL + ': Executed 1 of 3 SUCCESS',
                 '\x1b[1A\x1b[2K' + LABEL + ': Executed 2 of 3 SUCCESS',
                 '\x1b[1A\x1b[2K\x1b[32m' + LABEL + ': Executed 3 of 3 SUCCESS\x1b[39m']
        self.assertEqual(self.read(*lines)[0], (3, 0, 0, 3))
        self.assertEqual(self.read(*lines)[1], (3, 0))

    def test_two_browsers_sum_their_last_lines(self):
        a, b = 'Chrome 1 (Linux 0)', 'Firefox 2 (Linux 0)'
        lines = [f'{a}: Executed 1 of 4 SUCCESS', f'{b}: Executed 1 of 4 SUCCESS', f'{a}: Executed 4 of 4 SUCCESS',
                 f'{b}: Executed 3 of 4 (1 FAILED) (skipped 1)']
        self.assertEqual(self.read(*lines)[0], (4 + 2, 1, 1, 8))
        self.assertEqual(self.read(*lines)[1], (7, 1))
        self.assertEqual(quality_counts(lines)[1:], ((7, 1), 1))

    def test_the_shared_reader_agrees_with_the_adapter_on_synthetic_lines(self):
        lines = ['A (Linux 0): Executed 1 of 4 SUCCESS', 'B (Linux 0): Executed 2 of 4 (1 FAILED)', 'A (Linux 0): Executed 3 of 4 (skipped 1) SUCCESS']
        recorder = ar.Recorder()
        adapter = test_adapters.resolve('karma', ['karma', 'start'], 'karma')
        ctx = test_adapters.AdapterContext(['karma', 'start'], ROOT, ROOT, recorder)
        adapter.begin(ctx)
        for line in lines:
            adapter.feed('out', line, ctx)
        series = [c[1] for c in recorder.calls if c[0] == 'counts']
        self.assertEqual(pick(series[-1]), live_counts(lines))


class Purity(unittest.TestCase):
    def test_the_shared_module_imports_alone(self):
        code = ('import sys; sys.path.insert(0, %r); import skillsync_workflow.test_adapters.karma_line as m; '
                'bad = [n for n in ("skillsync_workflow.quality", "skillsync_workflow.test_run", "skillsync_workflow.test_adapters.karma") if n in sys.modules]; '
                'sys.exit(1 if bad else 0)') % str(ROOT)
        done = subprocess.run([sys.executable, '-I', '-c', code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == '__main__':
    unittest.main()
