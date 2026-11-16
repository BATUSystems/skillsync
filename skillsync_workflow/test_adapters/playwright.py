"""Adapter of Playwright Test (180-A3): `playwright test` with the `list` reporter (the default outside CI).

Verified on @playwright/test 1.59.1 / Node 22.22.0 / chromium-headless-shell 147, with two projects and two workers (`tests/fixtures/test-runs/playwright-*.jsonl`,
the report `playwright-junit.xml`).

`live = 'completion'`: in a pipe the `list` reporter prints NO line when a test starts (a terminal gets one, rewritten after the end): a line
`  ✓   2 [project] › file:line:col › describe › title (512ms)` comes when the test ENDS, and with several workers and projects the lines of different tests
interleave (the number is the index of the test, not the order of the lines). `running` would need a reporter of our own (`onTestBegin`); it is not part of this adapter.
The `line` reporter prints `[i/N] ...` when a test ENDS without its result, and `dot` prints no names: nothing is emitted for them (the summary counters and the
report remain). `json` and `junit` reporters write one document at the end.

Mapping: `✓` passed, `✘` failed, `-` skipped. Every attempt of a retried test is a line (`(retry #N)`, the last one wins: a test that failed and passed on the retry is
`passed`). The id is `file::describe › title [project]` (a project is a group of its own in the report, the junit report loses it: two projects would collapse into
one test there), the group is the file. The message of a failure is read from the detailed block that follows the run (`  1) [project] › file:l:c › title ──`): the
first lines of the error, emitted again. `end` returns True when the number of reported tests equals the total of the summary (`N failed`, `N flaky`, `N skipped`,
`N passed`), False otherwise (an interrupted run has no summary).
"""
import re

from . import Adapter, register

MAX_LINE = 2000
MAX_SEEN = 50000
MAX_MESSAGE_LINES = 3
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
WHERE = r'(?:\[(?P<project>[^\]]*)\] › )?(?P<file>[^\s›]+?):(?P<line>\d+):(?P<col>\d+) › (?P<name>.+?)'
LINE = re.compile(r'^ {2}(?P<mark>[✓✘-]) +(?P<n>\d+) ' + WHERE + r'(?: \(retry #\d+\))?(?: \((?P<time>\d+(?:\.\d+)?)(?P<unit>ms|s|m)\))?$')
HEADER = re.compile(r'^ {2}\d+\) ' + WHERE + r'(?: ─+)?$')
SUMMARY = re.compile(r'^ {2}(?P<n>\d+) (?P<kind>failed|flaky|skipped|passed|did not run|interrupted)\b')
STATES = {'✓': 'passed', '✘': 'failed', '-': 'skipped'}
UNITS = {'ms': 1, 's': 1000, 'm': 60000}


def _identity(found):
    project = found.group('project')
    name = found.group('name')
    return f"{found.group('file')}::{name}" + (f' [{project}]' if project else ''), name, found.group('file')


@register
class PlaywrightAdapter(Adapter):
    name = 'playwright'
    tools = ('playwright', 'npx', 'npm', 'node')
    live = 'completion'

    def __init__(self):
        self.seen = {}        # id -> last state
        self.total = None
        self.block = None     # [id, name, file, message lines] of the failure block being read
        self.reading = False  # the detailed failure blocks have started (after the lines of the run)

    def accepts(self, argv):
        words = [str(word).replace('\\', '/').rsplit('/', 1)[-1].lower() for word in argv]
        return 'playwright' in words[:4] and 'test' in words

    def _flush(self, ctx):
        if self.block is not None:
            test_id, name, file, lines = self.block
            if lines and self.seen.get(test_id) == 'failed':   # a flaky test passed on a retry: its first failure must not turn it red again
                ctx.emit.test(test_id, name, 'failed', group=file, msg='\n'.join(lines))
            self.block = None

    def feed(self, stream, text, ctx):
        text = ANSI.sub('', text[:MAX_LINE]).rstrip()
        found = LINE.match(text)
        if found and not self.reading:
            test_id, name, file = _identity(found)
            state = STATES[found.group('mark')]
            if len(self.seen) < MAX_SEEN or test_id in self.seen:
                self.seen[test_id] = state
                ms = int(round(float(found.group('time')) * UNITS[found.group('unit')])) if found.group('time') else None
                ctx.emit.test(test_id, name, state, group=file, ms=ms)
            return
        header = HEADER.match(text)
        if header:
            self._flush(ctx)
            self.reading = True
            test_id, name, file = _identity(header)
            self.block = [test_id, name, file, []]
            return
        summary = SUMMARY.match(text)
        if summary:
            self._flush(ctx)
            self.total = (self.total or 0) + int(summary.group('n')) if summary.group('kind') != 'did not run' else self.total
            return
        if self.block is not None and len(self.block[3]) < MAX_MESSAGE_LINES:
            line = text.strip()
            if line and not line.startswith('at ') and not line.startswith('Error Context'):
                self.block[3].append(line)

    def end(self, ctx, exit_code, output, reports):
        self._flush(ctx)
        return self.total is not None and self.total == len(self.seen)
