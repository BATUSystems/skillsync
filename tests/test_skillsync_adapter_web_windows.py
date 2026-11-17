"""180-A3: what Windows changes in the stream of the web tools: line ends `\\r\\n` (also cut anywhere by the pipe, a chunk may end in `\\r`) and paths with `\\` in the
names of files. Synthetic: the recorded streams are re-written, nothing here was recorded on Windows (NOT_VERIFIED there)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_replay as ar  # noqa: E402

from skillsync_workflow import test_adapters  # noqa: E402


def setUpModule():
    test_adapters.discover()


FIXTURES = (('vitest', 'vitest-verbose.jsonl'), ('vitest', 'vitest-default.jsonl'), ('karma', 'karma-progress.jsonl'), ('playwright', 'playwright-list.jsonl'),
            ('playwright', 'playwright-line.jsonl'), ('cypress', 'cypress-spec.jsonl'), ('angular', 'angular-vitest-verbose.jsonl'), ('angular', 'angular-karma-mixed.jsonl'))


def crlf(chunks):
    """The same bytes with every line end `\\r\\n` (a lone `\\r` of the Karma progress is not a line end and stays)."""
    out = []
    for chunk in chunks:
        out.append({**chunk, 'data': chunk['data'].replace('\r\n', '\n').replace('\n', '\r\n')})
    return out


class Crlf(unittest.TestCase):
    def test_crlf_gives_the_result_of_lf_for_any_cut_of_the_pipe(self):
        for adapter, name in FIXTURES:
            meta, chunks = ar.load(name)
            want = ar.replay_fixture(adapter, name)
            for size in (1, 2, 7, 4096):
                with self.subTest(fixture=name, size=size):
                    got = ar.replay(adapter, ar.rechunk(crlf(chunks), size), argv=meta['command'], tool=meta['tool'], exit_code=meta['exit'])
                    self.assertEqual((got[0].calls, got[3]), (want[0].calls, want[3]))

    def test_a_line_that_keeps_its_cr_is_read_like_the_same_line_without_it(self):
        """The runner strips the `\\r` of a complete line; an adapter must not depend on that for the patterns that end with `$`."""
        for adapter, name in FIXTURES:
            meta, chunks = ar.load(name)
            want = ar.replay_fixture(adapter, name)[0].calls
            instance = test_adapters.resolve(adapter, meta['command'], meta['tool'])
            recorder = ar.Recorder()
            ctx = test_adapters.AdapterContext(list(meta['command']), ar.ROOT, ar.ROOT, recorder)
            instance.begin(ctx)
            tails = {'out': '', 'err': ''}
            for chunk in chunks:
                lines = (tails[chunk['fd']] + chunk['data']).split('\n')
                tails[chunk['fd']] = lines.pop()
                for line in lines:
                    instance.feed(chunk['fd'], line.rstrip('\r') + '\r', ctx)
            for fd in ('out', 'err'):
                if tails[fd]:
                    instance.feed(fd, tails[fd].rstrip('\r') + '\r', ctx)
            instance.end(ctx, meta['exit'], '', [])
            with self.subTest(fixture=name):
                self.assertEqual(recorder.calls, want)


class Backslashes(unittest.TestCase):
    def ids(self, adapter, text, argv):
        events, _ = ar.replay_to_files(adapter, ar.chunks_from_text(text), argv=argv, tool=adapter)
        return [e['id'] for e in events if e['t'] == 'test']

    def test_playwright_on_windows_gives_ids_with_slashes(self):
        text = '  ✓  1 [chromium] › e2e\\a.spec.ts:3:1 › first (5ms)\n  1 passed (1s)\n'
        reference = '  ✓  1 [chromium] › e2e/a.spec.ts:3:1 › first (5ms)\n  1 passed (1s)\n'
        ids = self.ids('playwright', text, ['playwright', 'test'])
        self.assertTrue(ids and all('\\' not in i for i in ids), ids)
        self.assertEqual(ids, self.ids('playwright', reference, ['playwright', 'test']))
        events = ar.replay_to_files('playwright', ar.chunks_from_text(text), argv=['playwright', 'test'], tool='playwright')[0]
        self.assertEqual({e['group'] for e in events if e['t'] == 'test'}, {'e2e/a.spec.ts'})   # the group of the page is the same file for a report and a stream

    def test_cypress_on_windows_gives_ids_with_slashes(self):
        text = '  Running:  cypress\\e2e\\a.cy.js  (1 of 1)\n\n\n  s\n    ✓ ok (1ms)\n\n\n  1 passing (1s)\n'
        ids = self.ids('cypress', text, ['cypress', 'run'])
        self.assertTrue(ids and all('\\' not in i for i in ids), ids)
        self.assertEqual(ids, self.ids('cypress', text.replace('\\', '/'), ['cypress', 'run']))

    def test_vitest_on_windows_gives_ids_with_slashes(self):
        text = ' ✓ tests\\a.test.js > d > works 3ms\n Tests  1 passed (1)\n'
        ids = self.ids('vitest', text, ['vitest', 'run', '--reporter=verbose'])
        self.assertEqual(ids, ['tests/a.test.js::d > works'])

    def test_a_junit_report_with_backslashes_gives_the_same_ids_and_groups_as_the_slashes(self):
        import tempfile
        from skillsync_workflow import test_events, test_reports
        xml = '<testsuites><testsuite name="s"><testcase classname="e2e%sa.spec.ts" name="first"/></testsuite></testsuites>'
        seen = []
        for sep in ('\\', '/'):
            with tempfile.TemporaryDirectory(prefix='report-') as tmp:
                report = Path(tmp) / 'r.xml'
                report.write_text(xml % sep, encoding='utf-8')
                path, _ = test_events.new_attempt(Path(tmp), 'R1', 'F1.1')
                writer = test_events.EventWriter(path)
                try:
                    for item in test_reports.parse_any(report):
                        writer.test(item['id'], item['name'], item['state'], group=item.get('group'))
                finally:
                    writer.close()
                seen.append([(e['id'], e.get('group')) for e in test_events.read_events(path)[0] if e['t'] == 'test'])
        self.assertEqual(seen[0], seen[1])
        self.assertTrue(seen[0] and all('\\' not in (i + (g or '')) for i, g in seen[0]), seen)


if __name__ == '__main__':
    unittest.main()
