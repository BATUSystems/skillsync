"""Adapter of Vitest (180-A3): `vitest run --reporter=verbose` (and, through `angular`, the Vitest of `ng test`).

Verified on Vitest 4.1.12 / Node 22.22.0 (`tests/fixtures/test-runs/vitest-*.jsonl`, the report `vitest-junit.xml`).

`live = 'completion'`: no reporter prints a line when a test STARTS. `verbose` prints `✓ file > describe > name 404ms` when the test ENDS (the lines of parallel
files interleave, each carries the whole path, so nothing is lost); a run killed in the middle never shows the test that was hanging. The default reporter prints a
block per file after the file ended and does NOT print the describe path (`✓ adds 3ms` under `❯ file (9 tests ...)`), so an id built from it would not match the
id of the JUnit report (`file::describe > name`): the adapter emits nothing for it and the run falls back to the summary counters and the report.

Mapping: `✓` passed, `×` failed (the message is the next line `   → message`, emitted again with the message), `↓` skipped, `□` todo (skipped, message `todo`).
The id is `file::describe > name`, the same as the JUnit report of Vitest (`classname=file`, `name="describe > name"`). A retried test is ONE line with `(retry xN)`
and the elapsed time of all the attempts. `end` returns True (the report is not needed) only when the number of reported tests equals the total of the summary line.
"""
import re

from . import Adapter, register

MAX_LINE = 2000
MAX_SEEN = 50000
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
# ` ✓ tests/math.test.js > math > adds 404ms`, ` × file > name 411ms (retry x2)`, ` ↓ file > name`, ` □ file > name`
LINE = re.compile(r'^ (?P<mark>[✓×↓□]) (?P<file>[^\s>][^>]*?) > (?P<name>.+?)(?: (?P<time>\d+(?:\.\d+)?)(?P<unit>ms|s))?(?: \(retry x\d+\))?$')
MESSAGE = re.compile(r'^ {3}→ (?P<msg>.+)$')
TESTS = re.compile(r'^ +Tests +(?P<body>.+?) \((?P<total>\d+)\)$')
STATES = {'✓': 'passed', '×': 'failed', '↓': 'skipped', '□': 'skipped'}


def _ms(time, unit):
    if time is None:
        return None
    return int(round(float(time) * (1000 if unit == 's' else 1)))


class VitestCore:
    """The line logic shared by `vitest` and by `angular` (which hands over the lines of a Vitest run)."""

    def __init__(self):
        self.last = None       # (id, name, group, ms) of the last failed line: the next `→ message` line belongs to it
        self.seen = {}         # id -> last state
        self.total = None

    def feed(self, text, ctx):
        text = ANSI.sub('', text[:MAX_LINE]).rstrip()
        found = LINE.match(text)
        if found:
            file, name = found.group('file'), found.group('name')
            state = STATES[found.group('mark')]
            test_id = f'{file}::{name}'
            self.last = None
            if len(self.seen) < MAX_SEEN or test_id in self.seen:
                self.seen[test_id] = state
                ms = _ms(found.group('time'), found.group('unit'))
                ctx.emit.test(test_id, name, state, group=file, ms=ms, msg='todo' if found.group('mark') == '□' else None)
                if state == 'failed':
                    self.last = (test_id, name, file, ms)
            return
        message = MESSAGE.match(text)
        if message and self.last is not None:
            test_id, name, file, ms = self.last
            ctx.emit.test(test_id, name, 'failed', group=file, ms=ms, msg=message.group('msg'))
            self.last = None
            return
        if text.strip():
            self.last = None
        summary = TESTS.match(text)
        if summary:
            self.total = int(summary.group('total'))

    def complete(self):
        """True when every test of the summary was reported by a line."""
        return self.total is not None and len(self.seen) == self.total


@register
class VitestAdapter(Adapter):
    name = 'vitest'
    tools = ('vitest', 'npx', 'npm', 'node')
    live = 'completion'

    def __init__(self):
        self.core = VitestCore()

    def accepts(self, argv):
        words = [str(word).replace('\\', '/').rsplit('/', 1)[-1].lower() for word in argv]
        return 'vitest' in words[:4] and 'ng' not in words[:2]

    def feed(self, stream, text, ctx):
        self.core.feed(text, ctx)

    def end(self, ctx, exit_code, output, reports):
        return self.core.complete()
