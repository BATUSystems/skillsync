"""Adapter of Playwright Test (180-A3): `playwright test` with the `list` reporter (the default outside CI).

Verified on @playwright/test 1.59.1 / Node 22.22.0 / chromium-headless-shell 147, with two projects and two workers (`tests/fixtures/test-runs/playwright-*.jsonl`,
the report `playwright-junit.xml`).

`live = 'completion'`: in a pipe the `list` reporter prints NO line when a test starts (a terminal gets one, rewritten after the end): a line
`  ✓   2 [project] › file:line:col › describe › title (512ms)` comes when the test ENDS, and with several workers and projects the lines of different tests
interleave (the number is the index of the test, not the order of the lines). `running` would need a reporter of our own (`onTestBegin`); it is not part of this adapter.
The `line` reporter prints `[i/N] ...` when a test ENDS without its result, and `dot` prints no names: nothing is emitted for them (the summary counters and the
report remain). `json` and `junit` reporters write one document at the end.

Mapping: `✓` passed, `✘` failed, `-` skipped. Every attempt of a retried test is a line (`(retry #N)`, the last one wins: a test that failed and passed on the retry is
`passed`). The id is `file::describe › title [project]`, the group is the file. It does NOT equal the id of the junit report (`classname` is the file name without the test
directory and the project sits only in the `hostname` of the suite, which `parse_junit` drops), so with an explicit `--junit` and an incomplete stream the same test would
appear twice; the report is skipped (`end` True) when the stream is complete. In CI (`CI=1`) the default reporter is `dot`: nothing is emitted then. The message of a failure is read from the detailed block that follows the run (`  1) [project] › file:l:c › title ──`): the
first lines of the error, emitted again. `end` returns True when the number of reported tests equals the total of the summary (`N failed`, `N flaky`, `N skipped`, `N interrupted`, `N passed`;
`N did not run` is not counted), False otherwise (an interrupted run has no summary; a test that never finished has no line).
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
SUMMARY = re.compile(r'^ {2}(?P<n>\d+) (?:(?P<kind>failed|flaky|skipped|did not run|interrupted)|passed(?: \([^()]*\))?)$')
RETRY = re.compile(r'^Retry #\d+\b')
STATES = {'✓': 'passed', '✘': 'failed', '-': 'skipped'}
UNITS = {'ms': 1, 's': 1000, 'm': 60000}


def _identity(found):
    project = found.group('project')
    name = found.group('name')
    return f"{found.group('file')}::{name}" + (f' [{project}]' if project else ''), name, found.group('file')


@register
class PlaywrightAdapter(Adapter):
    name = 'playwright'
    tools = ('playwright',)
    live = 'completion'

    def __init__(self):
        self.seen = {}        # id -> last state
        self.total = None
        self.parts = {}       # the summary: kind -> number (`passed` for the line `N passed (time)`)
        self.block = None     # [id, name, file, message lines, closed] of the failure block being read

    def accepts(self, argv):
        words = [re.sub(r'\.(exe|cmd|bat)$', '', str(word).replace('\\', '/').rsplit('/', 1)[-1].lower()) for word in argv]
        return 'playwright' in words[:4] and 'test' in words and '--list' not in words

    def _flush(self, ctx):
        if self.block is not None:
            test_id, name, file, lines, _ = self.block
            if lines and self.seen.get(test_id) == 'failed':   # a flaky test passed on a retry: its first failure must not turn it red again
                ctx.emit.test(test_id, name, 'failed', group=file, msg='\n'.join(lines))
            self.block = None

    def feed(self, stream, text, ctx):
        if stream == 'err' or len(text) > MAX_LINE:   # Node warnings go to stderr; a real result line is short
            return
        text = ANSI.sub('', text).rstrip()
        found = LINE.match(text)
        if found:
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
            test_id, name, file = _identity(header)
            self.block = [test_id, name, file, [], False]
            return
        summary = SUMMARY.match(text)
        if summary:
            self._flush(ctx)
            if summary.group('kind') != 'did not run':
                self.total = (self.total or 0) + int(summary.group('n'))
            kind = summary.group('kind') or 'passed'
            self.parts[kind] = self.parts.get(kind, 0) + int(summary.group('n'))
            return
        if self.block is not None and not self.block[4] and len(self.block[3]) < MAX_MESSAGE_LINES:
            line = text.strip()
            if RETRY.match(line):
                self.block[4] = True   # the next attempt of the same test starts: its error is another message
            elif line and not line.startswith('at ') and not line.startswith('Error Context'):
                self.block[3].append(line)

    def end(self, ctx, exit_code, output, reports):
        self._flush(ctx)
        complete = self.total is not None and self.total == len(self.seen)
        if self.parts and not complete and self.total:   # the `line` reporter names no test: the counters of the summary are the result (a flaky test passed)
            parts = self.parts
            ctx.emit.counts(passed=parts.get('passed', 0) + parts.get('flaky', 0), failed=parts.get('failed', 0) + parts.get('interrupted', 0),
                            skipped=parts.get('skipped', 0) + parts.get('did not run', 0), running=0,
                            total=sum(parts.get(k, 0) for k in ('passed', 'flaky', 'failed', 'interrupted', 'skipped', 'did not run')))
        return complete
