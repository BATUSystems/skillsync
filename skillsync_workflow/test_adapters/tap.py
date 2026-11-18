"""Adapter of TAP (CORE-TAP): the lines `ok N - name` / `not ok N - name` of any command that prints TAP (`node --test --test-reporter=tap`, a bare script, `bats --tap`, `prove`).

Verified on Node 20.18.1, 22.22.0 and 24.11.1 (`tests/fixtures/test-runs/tap-*.jsonl`). The adapter is chosen only by `--format tap` (`explicit_only`, no `tools`); `node-test` (the
`node --test` adapter) reuses `TapParser` below, in the same way `angular` reuses `VitestCore`. The parser only reads lines and emits through `ctx.emit`.

`live = 'completion'`: TAP prints a result after the test ENDED. A result is emitted AT ONCE (the YAML block that follows never delays it); a FAILED result is emitted once more
when its block ends, with the message (`error:` / `message:`) and the time (`duration_ms:`). Groups come from `# Subtest: name` lines (Node): a line with a deeper indent than the
group's own line makes it a parent, and a parent reports nothing (its `ok` is a sum; its `not ok` is a failed test of its own only when no descendant failed).
Id = `group > subgroup::name`, a duplicate id gets ` (2)`, ` (3)` in the id and in the name. `ok # SKIP` and any `# TODO` are `skipped`. Names are decoded in ONE pass
(`\\\\` -> `\\`, `\\#` -> `#`, any other `\\x` stays). Without the header `TAP version N` only an explicit `--format tap` reads the unindented lines.
`end` is True (the counters come from the adapter) when the plan `1..N` equals the results of level 0, or, without a plan, when the exit code is 0; always False after
`Bail out!` or when nothing was seen.
"""
import re

from . import Adapter, register

MAX_LINE = 2000
MAX_SEEN = 50000
MAX_NAME = 300          # the same cut as the id of an emit (`test_events.MAX_NAME`): a longer key of `seen` would only cost memory
BLOCK_LIMIT = 200       # lines of a YAML block read without its `...`
COMMENTS_KEPT = 30      # comments before a result line (the error of a file that did not load is among them)
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
RESULT = re.compile(r'^(?P<indent> *)(?P<ok>not ok|ok)(?= |$)(?P<num> \d+(?= |$))?(?: - ?| )?(?P<rest>.*)$')
HEADER = re.compile(r'^TAP version \d+')
PLAN = re.compile(r'^1\.\.(\d+)(?:\s|$)')
SUBTEST = re.compile(r'^(?P<indent> *)# Subtest: ?(?P<name>.*)$')
BAIL = re.compile(r'^ *Bail out!')
DIRECTIVE = re.compile(r'^(?:(?P<skip>SKIP(?:PED)?)|(?P<todo>TODO))\b[ \t]*(?P<reason>.*)$', re.IGNORECASE)
ESCAPE = re.compile(r'\\([\\#])')
KEY = re.compile(r'^(?P<indent> *)(?P<key>[A-Za-z_]\w*):(?: +(?P<value>.*))?$')
ERROR_COMMENT = re.compile(r'^\w*Error\b')
NO_NAME = '(bez nazwy)'


def decode(text):
    """One left-to-right pass: `\\\\` -> `\\`, `\\#` -> `#`, any other backslash sequence stays (sequential `replace` would eat a backslash twice)."""
    return ESCAPE.sub(r'\1', text)


def split_directive(rest):
    """`(name, directive match or None)`: the FIRST unescaped ` # ` decides (or a `# ` that opens `rest`: `ok 1 # SKIP why` has no description); a text after it that is not
    SKIP/SKIPPED/TODO belongs to the name."""
    i = 0
    while i < len(rest):
        if rest[i] == '\\':
            i += 2
            continue
        if rest.startswith(' # ', i) or (i == 0 and rest.startswith('# ')):
            found = DIRECTIVE.match(rest[i + (3 if rest[i] == ' ' else 2):])
            return (rest[:i], found) if found else (rest, None)
        i += 1
    return rest, None


def _scalar(value):
    """The text of a YAML value written on the key's line (`'quoted'`, `"quoted"`, plain); None for a block scalar (`|-`, `>`) or nothing."""
    value = (value or '').strip()
    if not value or value[0] in '|>':
        return None
    if value[0] == "'":
        return (value[1:-1] if len(value) > 1 and value.endswith("'") else value[1:]).replace("''", "'")
    if value[0] == '"':
        return value[1:-1] if len(value) > 1 and value.endswith('"') else value[1:]
    return value


class _Group:
    __slots__ = ('indent', 'name', 'child', 'failed')

    def __init__(self, indent, name):
        self.indent = indent
        self.name = name
        self.child = False     # a line with a deeper indent came after it: it is a parent
        self.failed = False    # a descendant (a leaf) failed


class _Block:
    """The YAML block under a result line: only the top-level `error`, `message`, `duration_ms` and `exitCode` are read."""

    def __init__(self, key_indent, res_indent):
        self.key_indent = key_indent
        self.res_indent = res_indent
        self.lines = 0
        self.scalar_of = None   # `error` or `message` while its block scalar (`|-`) is read
        self.values = {}
        self.ms = None
        self.exit_code = False


class TapParser:
    """The TAP logic shared by `tap` and `node-test`: `feed_line(text, ctx)` per line of stdout, `finish(ctx, exit_code)` at the end (True = the adapter has the counters)."""

    def __init__(self, explicit=True):
        self.explicit = explicit
        self.header = False      # `TAP version N` seen: the stream is TAP (indented lines count too)
        self.bailed = False
        self.groups = []         # the stack of `# Subtest` lines
        self.subtests = False    # a `# Subtest` line was seen
        self.plan = None
        self.results = 0         # result lines read (any level)
        self.level0 = 0          # result lines with no indent (parents, leaves and files)
        self.collected_sent = False
        self.seen = {}           # id -> number of leaves with it
        self.comments = []       # `# ...` lines since the last result line
        self.pending = None      # the failed leaf that may still get a message: (id, name, group, error comment)
        self.after_result = False   # the previous line was a result line: `---` here opens a YAML block
        self.res_indent = 0
        self.block = None
        self.drain = None        # the indent of a result line whose block went over the limit: the deeper lines that follow are still its block

    @property
    def active(self):
        return self.header or self.explicit

    def feed_line(self, text, ctx):
        if len(text) > MAX_LINE:   # a line of that size is the output of a test, not TAP; inside a block it is just skipped
            return
        text = ANSI.sub('', text).rstrip()
        if self.bailed:
            return
        if self.drain is not None:
            if not text.strip() or len(text) - len(text.lstrip(' ')) > self.drain:
                return
            self.drain = None
        if self.block is not None:
            if self._in_block(text, ctx):
                return
        if HEADER.match(text):
            self.header = True
            self.after_result = False
            return
        if not self.active:
            return
        indent = len(text) - len(text.lstrip(' '))
        if indent and not self.header:   # without the header only the lines of level 0 are TAP
            self.after_result = False
            return
        if self.after_result and text.strip() == '---' and indent > self.res_indent:
            self.after_result = False
            self.block = _Block(indent, self.res_indent)
            return
        self.after_result = False
        if BAIL.match(text):
            self._flush(ctx)
            self.bailed = True
            return
        found = RESULT.match(text)
        if found:
            self._result(found, ctx)
            return
        found = SUBTEST.match(text)
        if found:
            self._subtest(len(found.group('indent')), decode(found.group('name').strip()))
            return
        if text.startswith('#') or (indent and text.lstrip().startswith('#')):
            self.comments = (self.comments + [text.lstrip()[1:].strip()])[-COMMENTS_KEPT:]
            return
        found = PLAN.match(text)
        if found:
            self.plan = int(found.group(1))

    def finish(self, ctx, exit_code):
        self._flush(ctx)
        if self.bailed or self.results == 0:
            return False
        if self.plan is not None:
            return self.level0 == self.plan
        return exit_code == 0

    def _subtest(self, indent, name):
        while self.groups and self.groups[-1].indent >= indent:
            self.groups.pop()
        for group in self.groups:
            group.child = True
        self.groups.append(_Group(indent, name))
        self.subtests = True

    def _result(self, found, ctx):
        indent = len(found.group('indent'))
        while self.groups and self.groups[-1].indent > indent:
            self.groups.pop()
        own = self.groups[-1] if self.groups and self.groups[-1].indent == indent else None
        parents = [g for g in self.groups if g.indent < indent]
        self.results += 1
        if indent == 0:
            self.level0 += 1
        if self.plan is not None and not self.collected_sent and self.results == 1:
            self.collected_sent = True
            if self.plan > 0 and not self.subtests:
                ctx.emit.collected(self.plan)
        comments, self.comments = self.comments, []
        self.after_result = True
        self.res_indent = indent
        self.pending = None
        failed = found.group('ok') == 'not ok'
        name_raw, directive = split_directive(found.group('rest'))
        if directive is not None:
            state, note = 'skipped', ('todo' if directive.group('todo') else (directive.group('reason').strip() or 'skipped'))
        else:
            state, note = ('failed' if failed else 'passed'), None
        if own is not None and own.child:   # a parent: a sum of its children
            if not (failed and directive is None and not own.failed):
                return
        name = decode(name_raw.strip()) or NO_NAME
        group = ' > '.join(g.name for g in parents) or None
        base = f'{group}::{name}' if group else name
        key = base[:MAX_NAME]
        if len(self.seen) >= MAX_SEEN and key not in self.seen:
            return
        count = self.seen[key] = self.seen.get(key, 0) + 1
        test_id = base if count == 1 else f'{base} ({count})'
        if count > 1:
            name = f'{name} ({count})'
        ctx.emit.test(test_id, name, state, group=group, msg=note)
        if state == 'failed':
            for g in parents:
                g.failed = True
            error = next((c for c in reversed(comments) if ERROR_COMMENT.match(c)), None)
            self.pending = (test_id, name, group, error)

    def _in_block(self, text, ctx):
        """True when the line belongs to the YAML block (consumed); False when the block ended before it (the line is then read as usual)."""
        block = self.block
        stripped = text.strip()
        if not stripped:
            block.lines += 1
            return self._within_limit(ctx)
        indent = len(text) - len(text.lstrip(' '))
        if indent <= block.res_indent:
            self._flush(ctx)
            return False
        if stripped == '...':
            self._flush(ctx)
            return True
        block.lines += 1
        self._within_limit(ctx)
        if self.block is None:   # over the limit: this line was the last one read for the block
            return True
        found = KEY.match(text)
        if indent == block.key_indent and found:
            self._key(block, found.group('key'), found.group('value'))
        elif block.scalar_of and indent > block.key_indent and block.scalar_of not in block.values:
            block.values[block.scalar_of] = stripped   # the first non-empty line of a block scalar
        return True

    def _within_limit(self, ctx):
        if self.block.lines > BLOCK_LIMIT:
            self.drain = self.block.res_indent   # the block ends here for the message, but its remaining (deeper) lines are not TAP
            self._flush(ctx)
        return True

    def _key(self, block, key, value):
        block.scalar_of = None
        if key in ('error', 'message'):
            text = _scalar(value)
            if text is not None:
                block.values.setdefault(key, text)
            elif (value or '').strip()[:1] in ('|', '>'):
                block.scalar_of = key
        elif key == 'duration_ms':
            try:
                block.ms = int(round(float((value or '').strip())))
            except ValueError:
                pass
        elif key == 'exitCode':
            block.exit_code = True

    def _flush(self, ctx):
        """Ends the YAML block (if any): a failed leaf is emitted again with the message and the time."""
        block, pending = self.block, self.pending
        self.block = None
        if block is None or pending is None:
            return
        self.pending = None
        test_id, name, group, error = pending
        msg = block.values.get('error') or block.values.get('message')
        if block.exit_code:   # a file that did not load: its YAML only says `test failed`, the reason is in the comments before the line
            msg = error or msg or 'test failed'
        if msg is not None or block.ms is not None:
            ctx.emit.test(test_id, name, 'failed', group=group, ms=block.ms, msg=msg)


@register
class TapAdapter(Adapter):
    name = 'tap'
    tools = ()
    explicit_only = True
    live = 'completion'

    def __init__(self):
        self.parser = None

    def begin(self, ctx):
        self.parser = TapParser(explicit=self.explicit)

    def feed(self, stream, text, ctx):
        if stream == 'out' and self.parser is not None:
            self.parser.feed_line(text, ctx)

    def end(self, ctx, exit_code, output, reports):
        return self.parser is not None and self.parser.finish(ctx, exit_code)
