"""Adapter of Karma + Jasmine (180-A3): `karma start` with the `progress` reporter (and, through `angular`, the Karma of `ng test`).

Verified on Karma 6.4.4 / karma-jasmine 5.1.0 / jasmine-core 5.6.0 / Chrome Headless 157 (`tests/fixtures/test-runs/karma-*.jsonl`, the report `karma-junit.xml`
of karma-junit-reporter 2.0.1).

`live = 'counts'`: the `progress` reporter prints no test names, only `<browser>: Executed N of M [(X FAILED)] [(skipped S)] (...)`, and rewrites the line for
every test (`ESC[1A ESC[2K` before each line: in a pipe every change is a line of its own, arriving when the test ENDS). Executed does not count the skipped specs,
so `passed = N - X`, `skipped = S`, `total = M`. Several browsers print a line each (`<browser>: Executed ...`); the adapter sums the last line of each. The names of
the tests come only from the junit report (`karma-junit-reporter`, `karma-junit.xml`) after the run: the failure lines of the stream
(`<browser> describe it FAILED`) cannot be split into the `classname` and `name` the report uses, so they are not emitted (an id that does not match the report
would be a duplicate). The `dots` reporter prints no counters while it runs, only the final `Executed N of M` line: that one is emitted. Two browsers with the same label share one entry
(the last line wins); the total grows while the browsers print their first line. `end` returns False (the report, when there is one, gives the list).
"""
import re

from . import Adapter, register

MAX_LINE = 2000
MAX_BROWSERS = 64
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
# the label of a browser ends with the system in brackets (`Chrome Headless 157.0.0.0 (Linux 0.0.0)`) and the tail is only what Karma prints: a `console.log` line
# (`Chrome ... LOG: 'Job: Executed 5 of 8 steps'`) or a test name with `: Executed 1 of 2` does not match
EXECUTED = re.compile(r'^(?P<browser>[^\'"]+?\)): Executed (?P<n>\d+) of (?P<m>\d+)(?P<rest>(?: \(\d+ FAILED\))?(?: \(skipped \d+\))?(?: (?:SUCCESS|ERROR|DISCONNECTED|FAILED))?(?: \([^()]*secs[^()]*\))*)\s*$')
FAILED = re.compile(r'\((\d+) FAILED\)')
SKIPPED = re.compile(r'\(skipped (\d+)\)')


class KarmaCore:
    def __init__(self):
        self.browsers = {}   # browser -> (passed, failed, skipped, total)

    def feed(self, text, ctx):
        if len(text) > MAX_LINE:
            return
        text = ANSI.sub('', text).strip()
        found = EXECUTED.match(text)
        if not found:
            return
        browser = found.group('browser')
        if browser not in self.browsers and len(self.browsers) >= MAX_BROWSERS:
            return
        n, m, rest = int(found.group('n')), int(found.group('m')), found.group('rest')
        failed = FAILED.search(rest)
        skipped = SKIPPED.search(rest)
        failed = int(failed.group(1)) if failed else 0
        skipped = int(skipped.group(1)) if skipped else 0
        self.browsers[browser] = (max(0, n - failed), failed, skipped, m)
        passed, failed, skipped, total = (sum(v[i] for v in self.browsers.values()) for i in range(4))
        ctx.emit.counts(passed=passed, failed=failed, skipped=skipped, running=max(0, total - passed - failed - skipped), total=total)


@register
class KarmaAdapter(Adapter):
    name = 'karma'
    tools = ('karma',)
    live = 'counts'

    def __init__(self):
        self.core = KarmaCore()

    def accepts(self, argv):
        words = [str(word).replace('\\', '/').rsplit('/', 1)[-1].lower() for word in argv]
        return 'karma' in words[:4] and 'ng' not in words[:2]

    def feed(self, stream, text, ctx):
        self.core.feed(text, ctx)
