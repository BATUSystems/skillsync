"""Adapter of Cypress (180-A3): `cypress run` with the default `spec` reporter (Mocha), Electron or Chrome.

Verified on Cypress 16.3.0 (Electron 150 headless and Chrome 157 headless), Node 22.22.0 (`tests/fixtures/test-runs/cypress-*.jsonl`, the report `cypress-junit.xml`).

`live = 'completion'`: the `spec` reporter prints a line when a test ENDS (`✓ title (472ms)`, `N) title` for a failure, `- title` for a skipped or pending test), the
spec files run one after another (no interleaving), and nothing is printed when a test starts. The titles of the suites are lines of their own (`  math`,
`    nested`): the full name is rebuilt from the indentation. A retried test prints `(Attempt i of n) title` lines before its final line (ignored). The detailed
failure listing after `N passing` repeats `1) suite` / `title:`: the adapter stops reading tests at the summary of a file (`N passing|pending|failing`) until
the next `Running:` header, so those lines are never taken for tests.

The id is `file::suite title` (the spec file and the titles joined with ` > `); the Mocha junit report uses `classname=<test title>` and `name="<suite> <title>"`, which do
not carry the file, so `end` returns True (the report is not needed) when the number of reported tests equals the total of the summaries of the files, and False
otherwise (a run without the summary: interrupted).
"""
import re

from . import Adapter, register

MAX_LINE = 2000
MAX_SEEN = 50000
MAX_DEPTH = 32
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
RUNNING = re.compile(r'^\s+Running:\s+(?P<file>\S.*?)\s+\((?P<i>\d+) of (?P<n>\d+)\)\s*$')
SUMMARY = re.compile(r'^\s+(?P<n>\d+) (?P<kind>passing|pending|failing)\b')
TEST = re.compile(r'^(?P<indent> +)(?P<mark>✓|-|\d+\))\s+(?P<title>.+?)(?: \((?P<ms>\d+)ms\))?$')
SUITE = re.compile(r'^(?P<indent> +)(?P<title>[^\s(✓\-\d][^\n]*)$')
ATTEMPT = re.compile(r'^\s+\(Attempt \d+ of \d+\)')
FAILURE = re.compile(r'^ {2}(?P<n>\d+)\) (?P<head>.+)$')   # the detailed listing after the summary: `  1) suite`, `       title:`, `      Error: message`


@register
class CypressAdapter(Adapter):
    name = 'cypress'
    tools = ('cypress', 'npx', 'npm', 'node')
    live = 'completion'

    def __init__(self):
        self.file = None
        self.in_tests = False
        self.stack = []          # [(indent, title)] of the suites around the current line
        self.seen = {}           # id -> state
        self.totals = {}         # file -> tests counted by its summary
        self.failure = None      # [file, name parts, message wanted] while a failure of the listing after a summary is read

    def accepts(self, argv):
        words = [str(word).replace('\\', '/').rsplit('/', 1)[-1].lower() for word in argv]
        return 'cypress' in words[:4] and 'run' in words

    def feed(self, stream, text, ctx):
        text = ANSI.sub('', text[:MAX_LINE]).rstrip()
        found = RUNNING.match(text)
        if found:
            self.file, self.in_tests, self.stack = found.group('file'), True, []
            self.totals.setdefault(self.file, 0)
            return
        if not self.file:
            return
        summary = SUMMARY.match(text)
        if summary:
            self.in_tests = False
            self.totals[self.file] = self.totals.get(self.file, 0) + int(summary.group('n'))
            return
        if not self.in_tests:
            self.listing(text, ctx)
            return
        if not text.strip() or ATTEMPT.match(text):
            return
        test = TEST.match(text)
        if test:
            indent = len(test.group('indent'))
            while self.stack and self.stack[-1][0] >= indent:
                self.stack.pop()
            mark = test.group('mark')
            state = 'passed' if mark == '✓' else 'skipped' if mark == '-' else 'failed'
            name = ' > '.join([title for _, title in self.stack] + [test.group('title')])
            test_id = f'{self.file}::{name}'
            if len(self.seen) < MAX_SEEN or test_id in self.seen:
                self.seen[test_id] = state
                ctx.emit.test(test_id, name, state, group=self.file, ms=int(test.group('ms')) if test.group('ms') else None)
            return
        suite = SUITE.match(text)
        if suite:
            indent = len(suite.group('indent'))
            while self.stack and self.stack[-1][0] >= indent:
                self.stack.pop()
            if len(self.stack) < MAX_DEPTH:
                self.stack.append((indent, suite.group('title')))

    def listing(self, text, ctx):
        """Reads `  1) suite` / `       title:` / `      AssertionError: message` after a summary: the message of a failure already reported by its line."""
        found = FAILURE.match(text)
        if found:
            self.failure = [[found.group('head').strip()], False]
            return
        if self.failure is None or not text.strip():
            return
        parts, named = self.failure
        if not named:
            line = text.strip()
            if line.endswith(':'):
                parts.append(line[:-1])
                self.failure[1] = True
            elif len(parts) < MAX_DEPTH:
                parts.append(line)
            return
        name = ' > '.join(parts)
        test_id = f'{self.file}::{name}'
        if test_id in self.seen:
            ctx.emit.test(test_id, name, 'failed', group=self.file, msg=text.strip())
        self.failure = None

    def end(self, ctx, exit_code, output, reports):
        return bool(self.totals) and all(self.totals.values()) and sum(self.totals.values()) == len(self.seen)
