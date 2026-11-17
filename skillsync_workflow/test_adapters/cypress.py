"""Adapter of Cypress (180-A3): `cypress run` with the default `spec` reporter (Mocha), Electron or Chrome.

Verified on Cypress 16.3.0 (Electron 150 headless and Chrome 157 headless), Node 22.22.0 (`tests/fixtures/test-runs/cypress-*.jsonl`, the report `cypress-junit.xml`).

`live = 'completion'`: the `spec` reporter prints a line when a test ENDS (`✓ title (472ms)`, `N) title` for a failure, `- title` for a skipped or pending test), the
spec files run one after another (no interleaving), and nothing is printed when a test starts. The titles of the suites are lines of their own (`  math`,
`    nested`): the full name is rebuilt from the indentation. A retried test prints `(Attempt i of n) title` lines before its final line (ignored). The detailed
failure listing after `N passing` repeats `1) suite` / `title:`: the adapter stops reading tests at the summary of a file (`N passing|pending|failing`) until
the next `Running:` header, so those lines are never taken for tests.

The id is `file::suite title` (the spec file and the titles joined with ` > `); the Mocha junit report uses `classname=<test title>` and `name="<suite> <title>"`, which do
not carry the file, so `end` returns True (the report is not needed) when the number of result lines equals the total of the summaries of the files and every file has one, and False
otherwise (interrupted: no summary; an empty file). With an explicit `--junit` and an incomplete stream the same test would appear twice. `√` is what Mocha prints for
a pass on Windows (NOT_VERIFIED on a recording). The message of a failure is the first line of the listing.
"""
import re

from . import Adapter, register

MAX_LINE = 2000
MAX_SEEN = 50000
MAX_DEPTH = 32
MAX_FILES = 1000
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
RUNNING = re.compile(r'^\s+Running:\s+(?P<file>\S.*?)\s+\((?P<i>\d+) of (?P<n>\d+)\)\s*$')
SUMMARY = re.compile(r'^\s+(?P<n>\d+) (?:passing(?: \(\d+(?:\.\d+)?(?:ms|s|m|h)(?: \d+(?:\.\d+)?(?:ms|s|m))*\))?|pending|failing)$')
TEST = re.compile(r'^(?P<indent> +)(?P<mark>[✓√]|-|\d+\))\s+(?P<title>.+?)(?: \((?P<ms>\d+)ms\))?$')   # `√` is what Mocha prints on Windows
SUITE = re.compile(r'^(?P<indent> +)(?P<title>\S[^\n]*)$')
ATTEMPT = re.compile(r'^\s+\(Attempt \d+ of \d+\)')
FAILURE = re.compile(r'^ {2}(?P<n>\d+)\) (?P<head>.+)$')   # the detailed listing after the summary: `  1) suite`, `       title:`, `      Error: message`


@register
class CypressAdapter(Adapter):
    name = 'cypress'
    tools = ('cypress',)
    live = 'completion'

    def __init__(self):
        self.file = None
        self.in_tests = False
        self.stack = []          # [(indent, title)] of the suites around the current line
        self.seen = {}           # id -> state
        self.lines = 0           # result lines read (a title repeated in a suite is one id but two lines)
        self.totals = {}         # file -> tests counted by its summary
        self.failure = None      # [file, name parts, message wanted] while a failure of the listing after a summary is read

    def accepts(self, argv):
        words = [str(word).replace('\\', '/').rsplit('/', 1)[-1].lower() for word in argv]
        return 'cypress' in words[:4] and 'run' in words

    def feed(self, stream, text, ctx):
        if stream == 'err' or len(text) > MAX_LINE:   # the noise of Electron and Chrome goes to stderr; a real line is short
            return
        text = ANSI.sub('', text).rstrip()
        found = RUNNING.match(text)
        if found:
            self.file, self.in_tests, self.stack, self.failure = found.group('file'), True, [], None
            if len(self.totals) < MAX_FILES or self.file in self.totals:
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
            state = 'passed' if mark in '✓√' else 'skipped' if mark == '-' else 'failed'
            name = ' > '.join([title for _, title in self.stack] + [test.group('title')])
            test_id = f'{self.file}::{name}'
            if len(self.seen) < MAX_SEEN or test_id in self.seen:
                if self.seen.get(test_id) == 'failed':
                    state = 'failed'   # the same title twice (a loop of `it`): a failure is not undone by a green twin
                self.seen[test_id] = state
                self.lines += 1
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
            head = found.group('head').strip()
            # a test outside any `describe` is `  1) title:` in one line; inside one the title is on a line of its own
            self.failure = [[head[:-1]], True] if head.endswith(':') else [[head], False]
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
        return bool(self.totals) and all(self.totals.values()) and sum(self.totals.values()) == self.lines == len(self.seen)
