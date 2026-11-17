"""Adapters of `cargo test` (Rust, libtest in its default format) (180-A4).

Verified on cargo / rustc 1.97.1 (`tests/fixtures/test-runs/rust-*.jsonl`): libtest prints `test <name> ... ok|FAILED|ignored[, why]` on STDOUT, the headers of
the test binaries (`Running unittests src/lib.rs (.../deps/demo-<hash>)`, `Running tests/green.rs (...)`, `Doc-tests demo`) on STDERR.

`live`: with several test threads (the default) libtest prints a test only AFTER it ended (`test x ... ok` in one piece): no `running` state exists, adapter
`cargo-test`, `live = 'completion'`. With ONE thread libtest prints `test x ... ` BEFORE the test runs and appends the result to the same line: the start is the
unfinished line, adapter `cargo-test-serial`, `live = 'running'`, chosen by `--test-threads=1` in the arguments. The number of threads can also come from the
environment (`RUST_TEST_THREADS`, one CPU): that is not visible in the arguments, so such a run is read as `completion` and its unfinished lines are ignored.

Id of a test = its libtest name (`tests::nested::deep`, a doc-test `src/lib.rs - add (line 5)`); group = the test binary of the last `Running` header (the executable
name without its hash, `doc-tests <crate>` for doc-tests). A name that comes back from another binary after it already has a result gets the group as a prefix
(`green::works`). libtest has no report file, so nothing overwrites these entries; `cargo nextest` (not read) would write JUnit. No collector: cargo has no
equivalent of `--no-build`, and `ctx.collect` returns no stderr (so no binary headers).

Failure messages come from the `---- name stdout ----` blocks after the run (the line after `panicked at ...:` or the quoted text of the older single-line form);
the names of failures also come from the `failures:` list and the terse `name --- FAILED` lines (`-- -q`), so a quiet run still names its failed tests.
A test started and never finished (killed, interrupted, timeout; non-zero or no exit code) is `failed` with a note. `bench:` lines are not tests and are ignored.
"""
import re

from . import Adapter, register

MAX_LINE = 2000
MAX_SEEN = 50000
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
RESULT = re.compile(r'^test (?P<name>.+?) \.\.\. (?P<res>ok|FAILED|ignored(?:, (?P<why>.*?))?)(?: <[\d.]+s>)?$')   # `<0.001s>`: --report-time (nightly)
START = re.compile(r'^test (?P<name>.+?) \.\.\. $')           # `test name ... ` exactly: the unfinished line of a test that is running (one thread)
LONE = re.compile(r'^(?P<res>ok|FAILED|ignored(?:, (?P<why>.*))?)$')   # the result on a later line, after text the test printed (`--nocapture`)
START_TEXT = re.compile(r'^test (?P<name>.+?) \.\.\. (?P<rest>\S.*)$')               # `test name ... text`: the test started and printed before its result (`--nocapture`)
BENCH = re.compile(r'^test (?P<name>.+?) \.\.\. bench: ')
TERSE_FAILED = re.compile(r'^(?P<name>\S.*?) --- FAILED$')
RUNNING_TESTS = re.compile(r'^\s+Running (?:unittests )?(?P<src>.+?) \((?P<exe>.*)\)$')
DOC_TESTS = re.compile(r'^\s+Doc-tests (?P<crate>\S+)$')
BLOCK = re.compile(r'^---- (?P<name>.+) stdout ----$')
PANIC_NEW = re.compile(r"panicked at [^\n]*:\d+(?::\d+)?:$")
PANIC_OLD = re.compile(r"panicked at '(?P<msg>.*)', [^\s]+:\d+:\d+$")
STATES = {'ok': 'passed', 'FAILED': 'failed'}
SHOULD_PANIC = re.compile(r' - should panic$')   # libtest decorates the name of a `#[should_panic]` test on its result line only


def _group(exe):
    """`.../deps/demo-5d464ed6742d1409` -> `demo`; `green-05497fcba309b506.exe` -> `green`."""
    base = exe.replace('\\', '/').rsplit('/', 1)[-1]
    base = re.sub(r'\.exe$', '', base)
    return re.sub(r'-[0-9a-f]{12,}$', '', base) or None


def _cargo_test(argv):
    """True for `cargo [+toolchain] [flags] test|t ...` and not for `cargo nextest ...` (the first word after the global flags decides)."""
    words = [str(a) for a in argv]
    for index, word in enumerate(words):
        if re.sub(r'\.exe$', '', word.replace('\\', '/').rsplit('/', 1)[-1].lower()) == 'cargo':
            skip = False
            for item in words[index + 1:]:
                if skip:
                    skip = False
                elif item in ('-Z', '-C', '--color', '--config'):
                    skip = True   # a global flag with a separate value
                elif item.startswith('+') or (item.startswith('-') and item != '--'):
                    continue
                else:
                    return item in ('test', 't')
            return False
    return False


def _serial(argv):
    words = [str(a) for a in argv]
    for index, word in enumerate(words):
        if re.fullmatch(r'--test-threads=0*1|RUST_TEST_THREADS=0*1', word) or (word == '--test-threads' and words[index + 1:index + 2] == ['1']):
            return True
    return False


class _CargoTest(Adapter):
    tools = ('cargo',)

    def __init__(self):
        self.group = None
        self.binary = None        # the full executable (with its hash) or `doc-tests <crate>`: the identity of a test binary
        self.successes = False    # inside the `successes:` section of `--show-output` (captured output of tests that passed)
        self.seen = {}            # id -> state
        self.names = {}           # name -> (id, group, state) of the first binary that reported it
        self.open = None          # (id, name, group) of a test whose start was seen, no result yet
        self.block = None         # the test name of the `---- name stdout ----` block being read
        self.msg_lines = []       # the message of that block: the lines after `panicked at ...:`
        self.collecting = False
        self.failures_marks = 0   # `failures:` lines of the current binary: the first opens the blocks, the second the list of names

    # -- selection --------------------------------------------------------------------------------------------------------------------------------
    def env(self):
        return {}

    @staticmethod
    def _clean(text):
        return ANSI.sub('', text[:MAX_LINE])

    # -- ids --------------------------------------------------------------------------------------------------------------------------------------
    def _ident(self, name):
        known = self.names.get(name)
        if known is not None and known[1] != self.binary and known[2] is not None:
            return f'{self.group}::{name}' if self.group else (f'{self.binary}::{name}' if self.binary else name)   # the same name from another binary, after its result
        return name

    def _emit(self, name, state, ctx, msg=None):
        ident = self._ident(name)
        if self.seen.get(ident) == state and not msg:
            return ident
        if ident not in self.seen and len(self.seen) >= MAX_SEEN:
            return ident
        self.seen[ident] = state
        if name not in self.names:
            self.names[name] = (ident, self.binary, state)
        elif state != 'running' and self.names[name][1] == self.binary:
            self.names[name] = (self.names[name][0], self.binary, state)
        ctx.emit.test(ident, name, state, group=self.group, msg=msg)
        return ident

    # -- the stream -------------------------------------------------------------------------------------------------------------------------------
    def partial(self, stream, text, ctx):
        return None   # `completion`: an unfinished line is not a start (the serial adapter overrides this)

    def _header(self, text, ctx):
        found = RUNNING_TESTS.match(text)
        doc = DOC_TESTS.match(text) if not found else None
        if not found and not doc:
            return False
        if self.block is not None:
            self._close_block(ctx)
        if self.open is not None:   # a new binary starts while a test of the previous one never finished: that binary died without a `test result:`
            ident, name, group = self.open
            if self.seen.get(ident) == 'running':
                self._emit(name, 'failed', ctx, msg='no result: the test binary ended without a result for this test')
            self.open = None
        self.group = _group(found.group('exe')) if found else f"doc-tests {doc.group('crate')}"
        self.binary = found.group('exe').strip() if found else self.group
        self.block, self.failures_marks, self.successes = None, 0, False
        return True

    def _result(self, name, res, why, ctx):
        name = SHOULD_PANIC.sub('', name)
        if res.startswith('ignored'):
            self._emit(name, 'skipped', ctx, msg=(why or '').strip() or None)
        else:
            self._emit(name, STATES[res], ctx)
        if self.open and self.open[1] == name:
            self.open = None

    def feed(self, stream, text, ctx):
        raw = self._clean(text)
        text = raw.rstrip()
        if self._header(text, ctx):
            return
        if text == 'successes:':   # `--show-output`: the captured output of tests that PASSED follows; it is never a result
            if self.block is not None:
                self._close_block(ctx)
            self.successes = True
            return
        if self.successes:
            if text == 'failures:':
                self.successes = False
                self.failures_marks += 1
            elif text.startswith('test result:'):
                self.successes, self.failures_marks, self.open = False, 0, None
            return
        if self.block is not None:
            if BLOCK.match(text) or text.startswith('failures:') or text.startswith('test result:'):
                self._close_block(ctx)
            else:
                self._read_block(text)
                return
        found = BLOCK.match(text)
        if found:
            self.block, self.msg_lines, self.collecting = found.group('name'), [], False
            return
        if text == 'failures:':
            self.failures_marks += 1
            return
        if text.startswith('test result:'):
            self.failures_marks = 0
            self.open = None   # the binary finished
            return
        found = RESULT.match(text)
        if found:
            self._result(found.group('name'), found.group('res'), found.group('why'), ctx)
            return
        found = START.match(raw)
        if found:
            self._started(found.group('name'), ctx)   # a start line that ended (the test printed output first)
            return
        bench = BENCH.match(text)
        if bench:
            if self.open and self.open[1] == SHOULD_PANIC.sub('', bench.group('name')):
                self._emit(self.open[1], 'passed', ctx, msg='bench')   # a benchmark is not a test; it finished
                self.open = None
            return
        found = START_TEXT.match(text)
        if found:
            self._started(found.group('name'), ctx)   # `test name ... text printed by the test`: it started; its result comes on a later line
            return
        terse = TERSE_FAILED.match(text)
        if terse:
            self._emit(SHOULD_PANIC.sub('', terse.group('name')), 'failed', ctx)
            return
        if self.open is not None:
            lone = LONE.match(text)
            if lone:
                self._result(self.open[1], lone.group('res'), lone.group('why'), ctx)
                return
        if self.failures_marks >= 2 and text.startswith('    ') and text.strip():
            name = SHOULD_PANIC.sub('', text.strip())
            if self.seen.get(self._ident(name)) != 'passed':   # the list names a failure of a quiet run; it never takes back a pass
                self._emit(name, 'failed', ctx)

    def _started(self, name, ctx):
        return None

    def _read_block(self, text):
        if self.collecting:
            if not text.strip() or text.startswith('note:') or len(self.msg_lines) >= 3:
                self.collecting = False
            else:
                self.msg_lines.append(text.strip())
            return
        if self.msg_lines:
            return
        old = PANIC_OLD.search(text)
        if old:
            self.msg_lines = [old.group('msg').strip()]
        elif PANIC_NEW.search(text):
            self.collecting = True
        elif text.startswith('note: test did not panic as expected'):
            self.msg_lines = [text[6:].strip()]

    def _close_block(self, ctx):
        name, msg = SHOULD_PANIC.sub('', self.block), ' '.join(self.msg_lines)
        self.block, self.msg_lines, self.collecting = None, [], False
        if name and msg and self.seen.get(self._ident(name)) != 'passed':   # a block never takes a pass back
            self._emit(name, 'failed', ctx, msg=msg[:300])

    def end(self, ctx, exit_code, output, reports):
        if self.block is not None:
            self._close_block(ctx)
        if self.open is not None and exit_code != 0:
            ident, name, group = self.open
            if self.seen.get(ident) == 'running':
                self._emit(name, 'failed', ctx, msg='no result: the run ended while the test was running')
        return False


@register
class CargoTest(_CargoTest):
    """Several test threads (the default): a test appears after its end."""
    name = 'cargo-test'
    live = 'completion'

    def accepts(self, argv):
        return _cargo_test(argv) and not _serial(argv)


@register
class CargoTestSerial(_CargoTest):
    """`--test-threads=1`: libtest prints `test name ... ` when the test starts."""
    name = 'cargo-test-serial'
    live = 'running'

    def accepts(self, argv):
        return _cargo_test(argv) and _serial(argv)

    def partial(self, stream, text, ctx):
        found = START.match(self._clean(text))
        if found:
            self._started(found.group('name'), ctx)

    def _started(self, name, ctx):
        name = SHOULD_PANIC.sub('', name)
        ident = self._ident(name)
        self.open = (ident, name, self.group)
        if self.seen.get(ident) is None and len(self.seen) < MAX_SEEN:
            self._emit(name, 'running', ctx)
