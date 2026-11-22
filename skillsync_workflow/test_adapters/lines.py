"""Adapter of result lines (CORE-1140-T3): one line `PASS name` / `FAIL name` per check, as printed by a shell `*_checks.sh` (any command that prints such lines).

The adapter is chosen only by `--format lines` (`explicit_only`, no `tools`): `auto` never picks it. It only reads lines of stdout (stderr is not read) and emits through `ctx.emit`.

Grammar: a stdout line, after stripping ANSI sequences and trailing white space (`\\r` included), is a result when it matches
`^(PASS|FAIL)(?:[ \\t]*[:\\-–—][ \\t]*|[ \\t]+)(?P<name>\\S.*)$` or is a bare `PASS` / `FAIL` (the name is then `(bez nazwy)`). The anchor is column 0 and the case is
significant: indented lines, `ALL PASS`, `SUMMARY`, `PASSED`, `Pass`, `pass:` are not results. `PASS name` is `passed`, `FAIL name` is `failed`.

`live = 'completion'`: a result is emitted AT ONCE. A failure is emitted once more when its block ends (the next result line or `end`), with the message: the non-empty lines after
the `FAIL` line up to the next result line (at most 10 lines, 500 characters in total; a line that would pass the limit and any line longer than `MAX_LINE` is left out; non-result
lines such as `ALL PASS (x)` belong to the block; the lines are joined with `\\n`). No details means no second emit. The recorder masks the message.
Id = name (cut to `MAX_NAME` as in `tap`); a duplicate gets ` (2)`, ` (3)` in the id and in the name; there are no groups; lines longer than `MAX_LINE` are skipped; at most `MAX_SEEN` names are remembered (a check past the limit is still emitted).
`end` is True (the counters come from the adapter) when at least one result was read, for any exit code (an exit code 0 with a `FAIL` is settled by the recorder, not guessed here);
otherwise False and nothing was emitted.
"""
import re

from . import Adapter, register

MAX_LINE = 2000
MAX_SEEN = 50000
MAX_NAME = 300          # the same cut as the id of an emit (`test_events.MAX_NAME`)
MAX_DETAIL_LINES = 10
MAX_DETAIL_CHARS = 500
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
RESULT = re.compile(r'^(?P<word>PASS|FAIL)(?:[ \t]*[:\-–—][ \t]*|[ \t]+)(?P<name>\S.*)$')
BARE = re.compile(r'^(?P<word>PASS|FAIL)$')
NO_NAME = '(bez nazwy)'
SEPARATORS = (':', '-', '\u2013', '\u2014')   # a name that is only a separator (`PASS -`) is no name: the line is not a result


@register
class LinesAdapter(Adapter):
    name = 'lines'
    tools = ()
    explicit_only = True
    live = 'completion'

    def __init__(self):
        self.results = 0
        self.seen = {}
        self.pending = None     # (id, name) of the failure whose block is open
        self.detail = []
        self.chars = 0
        self.full = False       # the character limit was reached: the rest of the block is not read

    def feed(self, stream, text, ctx):
        if stream != 'out' or len(text) > MAX_LINE:
            return
        text = ANSI.sub('', text).rstrip()
        found = RESULT.match(text) or BARE.match(text)
        if found is None or (found.groupdict().get('name') or '') in SEPARATORS:
            self._detail(text.strip())
            return
        self._flush(ctx)
        self.results += 1
        name = (found.groupdict().get('name') or '').strip()[:MAX_NAME] or NO_NAME
        if len(self.seen) >= MAX_SEEN and name not in self.seen:
            count = 1   # past the memory limit a check is still a check (its duplicates are not told apart)
        else:
            count = self.seen[name] = self.seen.get(name, 0) + 1
        test_id = name if count == 1 else f'{name} ({count})'
        if count > 1:
            name = f'{name} ({count})'
        failed = found.group('word') == 'FAIL'
        ctx.emit.test(test_id, name, 'failed' if failed else 'passed')
        if failed:
            self.pending = (test_id, name)

    def _detail(self, text):
        if self.pending is None or not text.strip() or self.full or len(self.detail) >= MAX_DETAIL_LINES:
            return
        if self.chars + len(text) > MAX_DETAIL_CHARS:
            self.full = True
            return
        self.detail.append(text)
        self.chars += len(text)

    def _flush(self, ctx):
        pending, detail = self.pending, self.detail
        self.pending, self.detail, self.chars, self.full = None, [], 0, False
        if pending is not None and detail:
            ctx.emit.test(pending[0], pending[1], 'failed', msg='\n'.join(detail))

    def end(self, ctx, exit_code, output, reports):
        self._flush(ctx)
        return self.results > 0
