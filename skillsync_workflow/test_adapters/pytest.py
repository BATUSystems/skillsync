"""Adapter of `pytest -v` (180-A2).

`pytest -v` prints `nodeid ` (a space, no line end) when a test STARTS and `PASSED   [ 33%]` on the same line when it ends, so the state `running` exists
(`partial`). Verified on pytest 9.2.0 / Python 3.14.10 and pytest-xdist 3.8 (`tests/fixtures/test-runs/pytest-*.jsonl`). The id is the one of the JUnit report
(`classname::name`, classname = the path relative to pytest's rootdir without `.py`, with dots, plus the classes), so `--junit-xml` overwrites the entry instead of
doubling it; `-v` prints node ids relative to the DIRECTORY the command runs in, so the offset to the `rootdir:` of the header is added. `end` returns False: the
recorder still reads the report.

Where it stays silent instead of guessing: a start is `nodeid ` exactly (spaces only inside balanced `[...]`); results are read only in the run section (before the
first `=== FAILURES ===`-style header) and the `FAILED nodeid - msg` summary lines only after `short test summary info`; with xdist (`[gw0] ... PASSED nodeid`) or two
starts without a result the starts are no longer trusted for that run (results only, `note` says so). A test whose start was seen but whose result never came is
`failed` with a note only when the run ended with a non-zero code or none (killed, interrupted, timeout); pytest prints its closing line even when interrupted, so that line proves nothing.
Not read: `console_output_style=classic` (no per-test lines), `addopts = -v` in an ini file (the argument list has no `-v`), a skip reason broken over two lines.
"""
import re

from . import Adapter, register

MAX_LINE = 2000
MAX_COLLECTED = 5000
MAX_SEEN = 50000
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
STATES = {'PASSED': 'passed', 'FAILED': 'failed', 'ERROR': 'failed', 'SKIPPED': 'skipped', 'XFAIL': 'skipped', 'XPASS': 'passed'}
WORDS = '|'.join(STATES)
# a node id: `path.py::Class::test[param with spaces]`; spaces only inside balanced brackets (a parameter id), so `nodeid printed text` is not one node
NODE = r'[^\s:\[\]][^\s\[\]]*?\.(?:py|txt|rst|md)::(?:[^\s\[\]]|\[[^\[\]]*\])+'
RESULT = re.compile(r'^(?P<node>' + NODE + r')(?: <- \S+)? (?P<state>' + WORDS + r')(?: \((?P<why>.*)\))?(?:\s+\[\s*(?:\d+%|\d+/\d+)\])?$')
XDIST = re.compile(r'^\[gw\d+\](?:\s+\[\s*\d+%\])?\s+(?P<state>' + WORDS + r')\s+(?P<node>' + NODE + r')$')
STATE_ONLY = re.compile(r'^(?P<state>' + WORDS + r')(?: \((?P<why>.*)\))?\s*(?:\[\s*(?:\d+%|\d+/\d+)\])?$')
START = re.compile(r'^(?P<node>' + NODE + r')(?: <- \S+)? $')   # `nodeid ` exactly: a trailing space and nothing else
LEAD = re.compile(r'^(?P<node>' + NODE + r')(?=\s|$)')
SUMMARY = re.compile(r'^(?P<kind>FAILED|ERROR|SUBFAILED(?:\(.*?\))?) (?P<node>' + NODE + r')(?: - (?P<msg>.*))?$')
HEADER = re.compile(r'^={3,} (?P<title>.+?) ={3,}$')
FINAL = re.compile(r'^=+ (?P<title>.+? in [\d.]+s(?: \(.*\))?) =+$')   # `= 7 failed, 12 passed in 2.37s =` (a single `=` on a long line)
ROOTDIR = re.compile(r'^rootdir: (?P<root>.+?)(?:, (?:configfile|testpaths|inifile): .*)?$')
VERBOSE = re.compile(r'^-[vxsl]*v[vxsl]*$')


def to_id(node, prefix=''):
    """`dir/test_mod.py::Class::test_x[param]` -> (`dir.test_mod.Class::test_x[param]`, `test_x[param]`, `dir.test_mod.Class`) as the JUnit report writes it.
    `prefix` is the rootdir-relative directory the command ran in (dotted)."""
    cut = node.find('[')
    head, tail = (node[:cut], node[cut:]) if cut >= 0 else (node, '')
    parts = head.split('::')
    path = re.sub(r'\.py$', '', parts[0].replace('\\', '/')).strip('/').replace('/', '.')   # only the path part: a backslash in a parameter stays
    if prefix:
        path = prefix + '.' + path
    name = parts[-1] + tail if len(parts) > 1 else parts[0]
    classes = parts[1:-1]
    group = '.'.join([path, *classes]) if len(parts) > 1 else None
    ident = f'{group}::{name}' if group else name
    return ident, name, group


@register
class PytestAdapter(Adapter):
    name = 'pytest'
    tools = ('pytest',)
    live = 'running'

    def __init__(self):
        self.open = None         # id of the test whose start line was seen
        self.open_node = None
        self.last_node = None    # a node id line without a state (the test printed output): its state may arrive on a later line
        self.seen = {}
        self.parallel = False    # xdist lines seen or overlapping starts: starts are not trustworthy
        self.note = ''
        self.section = 'run'     # run | summary | other: where the lines are read
        self.finished = False    # the closing `=== 3 passed in 1.2s ===` line was printed
        self.prefix = ''         # rootdir-relative directory of the command, dotted

    def accepts(self, argv):
        words = [str(a) for a in argv]
        if '--collect-only' in words or '--collectonly' in words or '--co' in words:
            return False
        return any(word == '--verbose' or VERBOSE.match(word) for word in words)

    @staticmethod
    def _clean(text):
        return ANSI.sub('', text[:MAX_LINE])

    # -- the list of the tests to come ------------------------------------------------------------------------------------------------------------------
    DROP_VALUE = ('-n', '--numprocesses', '--maxfail', '--junit-xml', '--junitxml', '-p', '--tb', '-W', '--durations', '--basetemp', '--html', '--alluredir',
                  '--json-report-file', '--report-log', '--log-file', '--cov', '--cov-report', '--timeout', '--color', '--rootdir-ignored')
    KEEP_VALUE = ('-k', '-m', '--ignore', '--ignore-glob', '--deselect', '-c', '--rootdir', '--confcutdir', '-o', '--override-ini')
    DROP_FLAG = re.compile(r'^(-[vqxslr]\w*|--verbose|--quiet|--exitfirst|--lf|--ff|--nf|--sw|--no-header|--no-summary|--capture=\S+|--tb=\S+|--color=\S+|--durations=\S+|'
                           r'-n\S+|--maxfail=\S+|--junit-xml=\S+|--junitxml=\S+|-p\S+|--timeout=\S+|--cov\S*|-W\S+|--basetemp=\S+|--html=\S+|--alluredir=\S+|--log-file=\S+|'
                           r'--report-log=\S+|--json-report\S*|--reruns\S*|-rA|-ra)$')

    def _collector_argv(self, argv):
        """`pytest --collect-only -q` for the same selection, or None when the arguments cannot be read unambiguously (no list then: nothing is guessed).
        Only flags that select tests are kept (an allow list); anything unknown, or a runner that is not python/pytest/uv/poetry, gives no list."""
        words = [str(a) for a in argv]
        index = next((i for i, w in enumerate(words) if w.replace('\\', '/').rsplit('/', 1)[-1] in ('pytest', 'py.test')), None)
        if index is None:
            return None
        head = words[:index + 1]
        for w in head[:-1]:
            base = w.replace('\\', '/').rsplit('/', 1)[-1].lower()
            if not (base.startswith('python') or w in ('-m', 'run', '--') or base in ('uv', 'poetry', 'pipenv', 'hatch', 'py')):
                return None   # `coverage run -m pytest`, `docker run ... pytest`, `timeout 5 pytest`: not rebuilt
        out, i = list(head), index + 1
        while i < len(words):
            word = words[i]
            name = word.split('=', 1)[0]
            if name in self.KEEP_VALUE:
                if '=' in word:
                    out.append(word)
                elif i + 1 < len(words):
                    out += [word, words[i + 1]]
                    i += 1
                else:
                    return None
            elif name in self.DROP_VALUE and '=' not in word:
                i += 1
            elif self.DROP_FLAG.match(word):
                pass
            elif word.startswith('-'):
                return None   # a flag the adapter does not know: the listing could differ from the run
            else:
                out.append(word)   # a path, a node id
            i += 1
        return out + ['--collect-only', '-q']

    def begin(self, ctx):
        """The list of the tests to come: node ids become `waiting` entries (the collector prints rootdir-relative ids, like the report)."""
        argv = self._collector_argv(ctx.argv)
        if argv is None:
            return
        text = ctx.collect(argv)
        if not text:
            return
        names = []
        for line in text.splitlines():
            line = ANSI.sub('', line[:MAX_LINE]).strip()
            if re.fullmatch(NODE, line):
                names.append(to_id(line)[0].replace('\\', '/'))   # the event writer normalises the ids of `test` events the same way
        if names:
            ctx.emit.collected(len(names), names[:MAX_COLLECTED])

    # -- the stream -----------------------------------------------------------------------------------------------------------------------------------
    def _id(self, node):
        return to_id(node, self.prefix)

    def partial(self, stream, text, ctx):
        if self.parallel or self.section != 'run':
            return
        found = START.match(self._clean(text))
        if not found:
            return
        ident, name, group = self._id(found.group('node'))
        if self.open is not None and self.open != ident:
            # another test is open: two tests at once (xdist, or output interleaved): starts are not reliable for this run
            self.parallel = True
            self.note = 'starts dropped: tests overlap'
            return
        if self.seen.get(ident) is None and len(self.seen) < MAX_SEEN:
            self.open, self.open_node = ident, found.group('node')
            self.last_node = found.group('node')   # the state may arrive alone on a later line (the test printed output in between)
            self.seen[ident] = 'running'
            ctx.emit.test(ident, name, 'running', group=group)

    def _result(self, node, state, why, ctx):
        ident, name, group = self._id(node)
        new = STATES[state]
        msg = (why or '').strip() or None
        if state == 'XFAIL':
            msg = ('xfail: ' + msg) if msg else 'xfail'
        elif state == 'XPASS':
            msg = ('xpass: ' + msg) if msg else 'xpass'
        if self.seen.get(ident) == new and not msg:
            return
        if ident not in self.seen and len(self.seen) >= MAX_SEEN:
            return
        self.seen[ident] = new
        if self.open == ident:
            self.open = None
        ctx.emit.test(ident, name, new, group=group, msg=msg)

    def _header(self, text, ctx):
        final = FINAL.match(text)
        if final:
            self.finished, self.section = True, 'other'
            return True
        found = HEADER.match(text)
        if not found:
            return False
        title = found.group('title').lower()
        if title == 'test session starts':
            self.section = 'run'
        elif title == 'short test summary info':
            self.section = 'summary'
        elif re.match(r'^[\d\w ,]*(passed|failed|error|skipped|deselected|xfailed|xpassed|warnings?|no tests ran).* in [\d.]+s', title):
            self.finished = True
            self.section = 'other'
        else:
            self.section = 'other'   # FAILURES, ERRORS, warnings summary, captured output...: text of tests, never results
        return True

    def feed(self, stream, text, ctx):
        text = self._clean(text).rstrip()   # xdist prints `[gw1] [ 7%] PASSED nodeid ` with a trailing space
        if self._header(text, ctx):
            return
        root = ROOTDIR.match(text)
        if root and self.section == 'run' and not self.seen:
            base = str(ctx.cwd).replace('\\', '/').rstrip('/')   # text only: the adapter touches no file system
            top = root.group('root').strip().replace('\\', '/').rstrip('/')
            self.prefix = base[len(top) + 1:].strip('/').replace('/', '.') if base.startswith(top + '/') else ''
            return
        if self.section == 'summary':
            summary = SUMMARY.match(text)
            if summary:
                kind = summary.group('kind')
                ident, name, group = self._id(summary.group('node'))
                msg = (summary.group('msg') or '').strip() or None
                if kind.startswith('SUBFAILED'):
                    self.seen[ident] = 'failed'
                    ctx.emit.test(ident, name, 'failed', group=group, msg=('sub-test ' + kind[9:] + ': ' + msg) if msg else 'sub-test failed')
                elif msg and self.seen.get(ident) in ('failed', 'running', None) and (ident in self.seen or len(self.seen) < MAX_SEEN):
                    self.seen[ident] = 'failed'
                    ctx.emit.test(ident, name, 'failed', group=group, msg=msg)
            return
        if self.section != 'run':
            return
        x = XDIST.match(text)
        if x:
            self.parallel = True   # a worker line: results only
            self._result(x.group('node'), x.group('state'), None, ctx)
            return
        found = RESULT.match(text)
        if found:
            self._result(found.group('node'), found.group('state'), found.group('why'), ctx)
            self.last_node = None
            return
        only = STATE_ONLY.match(text)
        if only and self.last_node:
            self._result(self.last_node, only.group('state'), only.group('why'), ctx)
            self.last_node = None
            return
        node = LEAD.match(text)
        if node:
            self.last_node = node.group('node')   # a node id line without a state: the test printed output, the state comes on a later line
            ident, name, group = self._id(node.group('node'))
            if not self.parallel and self.seen.get(ident) is None and self.open in (None, ident) and len(self.seen) < MAX_SEEN:
                self.open, self.open_node = ident, node.group('node')
                self.seen[ident] = 'running'
                ctx.emit.test(ident, name, 'running', group=group)

    def end(self, ctx, exit_code, output, reports):
        # a test whose start was seen but whose result never came (the run was killed, interrupted or timed out, and printed no closing summary): it did not pass
        if self.open is not None and exit_code != 0 and self.seen.get(self.open) == 'running':   # pytest prints its closing line even when interrupted
            ident, name, group = self._id(self.open_node) if self.open_node else (self.open, self.open, None)
            ctx.emit.test(ident, name, 'failed', group=group, msg='no result: the run ended while the test was running')
            self.open = None
        return False
