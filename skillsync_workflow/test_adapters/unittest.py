"""Adapter of `python -m unittest -v` (180-A2).

`-v` prints `test_x (pkg.mod.Class.test_x) ... ` on STDERR when a test starts and the result (`ok`, `FAIL`, `ERROR`, `skipped 'why'`, `expected failure`,
`unexpected success`) on the same line when it ends. The unfinished line is what `partial` sees, so the state `running` exists. Verified on Python 3.14.10
(`tests/fixtures/test-runs/unittest-*.jsonl`). The old shape `test_x (pkg.mod.Class)` and `setUpClass (pkg.mod.Class)` are read too (the qualified part is then the
group); `subTest` lines carry `[msg]` and/or `(i=1)` after the qualified name.

Mapping: `ok` passed; `FAIL`/`ERROR` failed; `skipped '...'` skipped (the reason is the message); `expected failure` skipped with the message `expected failure`
(the run is green, like an XFAIL in a JUnit report); `unexpected success` failed with that message. A failing sub-test is reported as a test of its own
(`group::name (i=1)`) and fails its parent (unittest prints no result line for a parent whose sub-test failed). The message of a failure is the last unindented
line of the traceback that looks like an exception (`ValueError: kaput`), read from the `FAIL:`/`ERROR:` blocks after the run.

Safety against output that only looks like unittest: a result is taken only from the stream the start was seen on; a `FAIL:` block starts only after a `=====` rule;
a line `word (qualified) ... ` counts only when `qualified` ends with `.word` (or the word starts with `test`, or it is `setUpClass` and its relatives). A test whose start was
seen but whose result never came is `failed` with a note ONLY when the run did not print its `Ran N tests` summary and ended with a non-zero code (killed or
interrupted); with a summary nothing is invented. There is no report file for unittest: the counters of the summary line are the final source, so `end` returns False.
"""
import re

from . import Adapter, register

MAX_LINE = 2000   # longer lines are cut before matching (a regex on a megabyte line must not stall the reader)
MAX_SEEN = 50000
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
SUB = r'(?P<sub>(?: \[.*?\])?(?: \(.*?\))?)'   # lazy: the parameters may hold `)` and `]` (`(pair=(1, 2))`); the line is cut at MAX_LINE first
# `test_x (pkg.mod.Class.test_x)` + optional sub-test text + ` ... ` + the rest (the result, or nothing while the test runs)
HEAD = re.compile(r'^(?P<indent> *)(?P<name>[^\s(][^\s]*) \((?P<qual>[\w.]+)\)' + SUB + r' \.\.\. ?(?P<rest>.*)$')
PENDING = re.compile(r'^(?P<name>[^\s(][^\s]*) \((?P<qual>[\w.]+)\)$')   # a test with a docstring: the name is on its own line
DOC_RESULT = re.compile(r'^(?P<doc>.*?) \.\.\. (?P<rest>ok|FAIL|ERROR|skipped .*|expected failure|unexpected success)$')
BLOCK = re.compile(r'^(?P<kind>FAIL|ERROR): (?P<name>[^\s(][^\s]*) \((?P<qual>[\w.]+)\)' + SUB + r'$')
EXCEPTION = re.compile(r'^[A-Za-z_][\w.]*(?:: .*|:)?$')
RAN = re.compile(r'^Ran \d+ tests? in ')
SETUPS = ('setUpClass', 'tearDownClass', 'setUpModule', 'tearDownModule')
TOKENS = ('ok', 'FAIL', 'ERROR', 'expected failure', 'unexpected success')


def _state(rest):
    """`(state, msg)` of the text after ` ... `, or None when it is not a result (the test is still running or printed something)."""
    rest = rest.strip()
    if rest == 'ok':
        return 'passed', None
    if rest in ('FAIL', 'ERROR'):
        return 'failed', None
    if rest.startswith(("skipped '", 'skipped "')):
        reason = rest[7:].strip().strip('\'"')
        return 'skipped', reason or None
    if rest == 'expected failure':
        return 'skipped', 'expected failure'
    if rest == 'unexpected success':
        return 'failed', 'unexpected success'
    return None


def _plausible(name, qual):
    """The qualified part of a real unittest line ends with the test name (3.11+), or is the class of an old-style line."""
    return qual.endswith('.' + name) or name in SETUPS or name.startswith('test')


def _ident(name, qual, sub=None):
    if qual.endswith('.' + name):
        group = qual[:-len(name) - 1]
    else:
        group = qual   # old shape `test_x (pkg.mod.Class)` and `setUpClass (pkg.mod.Class)`
    label = name + (sub or '')
    return (f'{group}::{label}' if group else label), label, group or None


@register
class UnittestAdapter(Adapter):
    name = 'unittest'
    tools = ('unittest',)
    live = 'running'

    def __init__(self):
        self.running = None      # (id, label, group) of the test whose start was seen and whose result is still missing
        self.running_stream = None
        self.pending = None      # a test name waiting for its docstring line
        self.block = None        # (id, label, group, kind) of the FAIL:/ERROR: block being read
        self.block_last = None
        self.dashes = 0
        self.after_rule = False  # the previous line was a `=====` rule
        self.ran = False         # the `Ran N tests in ...` summary was printed
        self.seen = {}           # id -> last state
        self.open = {}           # id -> (label, group) of tests started and not finished

    def accepts(self, argv):
        words = [str(a) for a in argv]
        if 'unittest' not in words:
            return False
        for word in words[words.index('unittest') + 1:]:   # the flags of unittest itself, not the `-v` of the interpreter
            if word == '--verbose' or re.fullmatch(r'-[vqfcb]*v[vqfcb]*', word):
                return True
        return False   # without -v unittest prints dots: no names, nothing to adapt

    @staticmethod
    def _clean(text):
        return ANSI.sub('', text[:MAX_LINE])

    def _start(self, ident, label, group, stream, ctx):
        self.running, self.running_stream = (ident, label, group), stream
        if self.seen.get(ident) is None and len(self.seen) < MAX_SEEN:
            self.seen[ident] = 'running'
            self.open[ident] = (label, group)
            ctx.emit.test(ident, label, 'running', group=group)

    def partial(self, stream, text, ctx):
        found = HEAD.match(self._clean(text))
        if not found or found.group('rest') or found.group('indent') or found.group('sub') or not _plausible(found.group('name'), found.group('qual')):
            return   # not a bare start (a result or foreign text follows the dots, a sub-test line, or an unlikely name)
        self._start(*_ident(found.group('name'), found.group('qual')), stream, ctx)

    def _result(self, ident, label, group, state, msg, ctx):
        if self.seen.get(ident) == state and not msg:
            return
        if ident not in self.seen and len(self.seen) >= MAX_SEEN:
            return
        self.seen[ident] = state
        self.open.pop(ident, None)
        ctx.emit.test(ident, label, state, group=group, msg=msg)

    def feed(self, stream, text, ctx):
        text = self._clean(text)
        if RAN.match(text):
            self.ran = True
        if self.block is not None:
            self._feed_block(text, ctx)
            if self.block is not None:
                return
        rule = self.after_rule
        self.after_rule = text.startswith('=' * 20)
        if rule:
            block = BLOCK.match(text)
            if block and _plausible(block.group('name'), block.group('qual')):
                ident, label, group = _ident(block.group('name'), block.group('qual'), block.group('sub') or None)
                self.block, self.block_last, self.dashes = (ident, label, group, block.group('kind')), None, 0
                return
        found = HEAD.match(text)
        if found and _plausible(found.group('name'), found.group('qual')):
            sub = found.group('sub') or None
            ident, label, group = _ident(found.group('name'), found.group('qual'), sub)
            state = _state(found.group('rest'))
            if state:
                parent = _ident(found.group('name'), found.group('qual'))
                self._result(ident, label, group, state[0], state[1], ctx)
                if sub and state[0] == 'failed':   # the parent test fails with its sub-test (unittest prints no result line for it)
                    self._result(parent[0], parent[1], parent[2], 'failed', 'sub-test failed: ' + label, ctx)
                if self.running and self.running[0] in (ident, parent[0]) and not (sub and state[0] != 'failed'):
                    self.running = None
                self.pending = None
            elif not sub and not found.group('indent'):
                self._start(ident, label, group, stream, ctx)   # the line ended before the result (the test printed first): it was started
            return
        pending = PENDING.match(text)
        if pending and _plausible(pending.group('name'), pending.group('qual')):
            self.pending = _ident(pending.group('name'), pending.group('qual'))
            return
        doc = DOC_RESULT.match(text)
        if doc and self.pending is not None:
            state = _state(doc.group('rest'))
            if state:
                self._result(*self.pending, state[0], state[1], ctx)
            self.pending = None
            return
        # a lone result token on a later line, on the stream the start was seen on, after text the test printed
        token = text.strip()
        if self.running is not None and stream == self.running_stream and (token in TOKENS or token.startswith(("skipped '", 'skipped "'))):
            state = _state(token)
            if state:
                self._result(*self.running, state[0], state[1], ctx)
                self.running = None

    def _feed_block(self, text, ctx):
        """Inside `FAIL: test (qual)`: skips the rule under the header, reads the traceback, ends at the next rule; the last exception-looking line is the message."""
        if text.startswith('-' * 10) and self.dashes == 0:
            self.dashes = 1   # the rule under the header
            return
        if text.startswith('=' * 6) or text.startswith('-' * 6):
            self._close_block(ctx)
            self.after_rule = text.startswith('=' * 20)
            return
        if EXCEPTION.match(text) and not text.startswith('Traceback'):
            self.block_last = text.strip()

    def _close_block(self, ctx):
        ident, label, group, kind = self.block
        if self.block_last:
            self._result(ident, label, group, 'failed', self.block_last[:300], ctx)
        self.block = None

    def end(self, ctx, exit_code, output, reports):
        if self.block is not None:   # the output ended inside a block: report what was read
            self._close_block(ctx)
        if exit_code != 0 and not self.ran:   # None (interrupted before an exit code) counts as not successful
            # killed or interrupted: a test started and never finished did not pass (with a summary printed nothing is invented)
            for ident, (label, group) in list(self.open.items()):
                if self.seen.get(ident) == 'running':
                    self._result(ident, label, group, 'failed', 'no result: the run ended while the test was running', ctx)
        return False
