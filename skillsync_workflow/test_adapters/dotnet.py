"""Adapter of `dotnet test` (180-A2): VSTest (`dotnet test` classic) and Microsoft.Testing.Platform (MTP).

Verified on dotnet SDK 10.0.114 with VSTest 18.0.2 / xunit.runner.visualstudio 3.1.5 and Microsoft.Testing.Platform 2.5.1 / xunit.v3.mtp-v2 4.0.2
(`tests/fixtures/test-runs/dotnet-*.jsonl`, the reports `dotnet-vstest-all.trx` and `dotnet-mtp-all.trx`).

`live = 'completion'`: neither console prints a line when a single test STARTS (VSTest only `Starting: <assembly>`, MTP nothing); results of the VSTest console
arrive in batches, after the tests ended. So the adapter never emits `running`: a listed test is `waiting` until its result. What can arrive earlier:
the xUnit adapter of VSTest prints `... Name [FAIL]` / `[SKIP]` on STDERR in real time (before the console result), so a red or skipped test is shown as soon as
that line is read; the console result of the same test later changes nothing (the same state), only adds the message.

Language independence: the adapter asks for English output (`env()`: `DOTNET_CLI_UI_LANGUAGE=en`, `VSLANG=1033`; either variable is enough for VSTest, verified) and
reads the lines by STRUCTURE (`  <word> <Name> [<time>]`, `<word> <Name> (<time>)`), mapping a small table of known words (English and Polish); a word it does not know
is ignored, never guessed (the TRX report, read by the recorder after the run, is the final source and is language independent). Durations are never parsed (the
decimal separator follows the culture); the TRX has them. `[xUnit.net ...] Name [FAIL]` lines are English in every culture.
"""
import re

from . import Adapter, register

MAX_LINE = 2000
MAX_COLLECTED = 5000
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
# VSTest console: `  Passed Demo.Class.Name [7 ms]` (indent 2, word, name, `[time]`); the time text is culture dependent: only its shape `[...]` is used
VSTEST = re.compile(r'^ {2}(?P<word>[^\W\d_]+(?: [^\W\d_]+)?) (?P<name>[\w.`<>+,\[\]]+(?:\(.*\))?) \[[^\]]*\]$')
# MTP console: `failed Demo.Class.Name (30ms)` (no indent) and, in the detailed mode, `passed Demo.Class.Name (0ms)`
MTP = re.compile(r'^(?P<word>[^\W\d_]+(?: [^\W\d_]+)?) (?P<name>[\w.`<>+,\[\]]+(?:\(.*\))?) \([^()]*\)$')
# the xUnit adapter of VSTest: `[xUnit.net 00:00:00.27]     Demo.Class.Name [FAIL]`
XUNIT = re.compile(r'^\[xUnit\.net [\d:.]+\] {5}(?P<name>[\w.`<>+]+(?:\(.*\))?) \[(?P<tag>FAIL|SKIP)\]$')   # the test name is indented by 4 spaces, its messages by 6
WORDS = {
    'passed': 'passed', 'powodzenie': 'passed', 'zakończone powodzeniem': 'passed',
    'failed': 'failed', 'niepowodzenie': 'failed', 'zakończone niepowodzeniem': 'failed',
    'skipped': 'skipped', 'pominięto': 'skipped', 'pominięte': 'skipped',
}
MESSAGE_HEAD = re.compile(r'^ {2}(Error Message|Komunikat o błędzie):$')
MESSAGE_END = re.compile(r'^ {2}(Stack Trace|Ślad stosu|Standard Output Messages|Standardowe komunikaty wyjściowe):?$')
LIST_LINE = re.compile(r'^ {2,4}(?P<name>[\w.`<>+,\[\]]+(?:\(.*\))?)$')
MAX_STATE = 50000
MAX_MESSAGE_LINES = 5
STDOUT_SECTION = re.compile(r'^ {2}(Standard Output Messages|Standardowe komunikaty wyjściowe):?$')


def to_id(name):
    """`Demo.Class.Method(a: 1)` -> (`Demo.Class::Demo.Class.Method(a: 1)`, `Demo.Class.Method(a: 1)`, `Demo.Class`): the id of the TRX parser (`className::testName`)."""
    head = name.split('(', 1)[0]
    if '.' not in head:
        return None   # a bare method name (NUnit, MSTest, a DisplayName): it cannot be matched with the TRX id (`Ns.Class::Ns.Class.Method`), so it is not reported
    group = head.rsplit('.', 1)[0]
    return f'{group}::{name}', name, group


@register
class DotnetAdapter(Adapter):
    name = 'dotnet'
    tools = ('dotnet',)
    live = 'completion'

    def __init__(self):
        self.state = {}          # id -> last emitted state
        self.msg_of = None       # (id, name, group, state) of the VSTest result whose message is being read
        self.msg_lines = []
        self.in_stdout = False   # inside `Standard Output Messages:` of a result: text printed by the test

    @staticmethod
    def _is_test_command(argv):
        words = [str(a) for a in argv]
        for index, word in enumerate(words):
            if re.sub(r'\.(exe|dll)$', '', word.replace('\\', '/').rsplit('/', 1)[-1].lower()) == 'dotnet':
                return words[index + 1:index + 2] == ['test']
        return False

    def accepts(self, argv):
        words = [str(a) for a in argv]
        return self._is_test_command(words) and '--list-tests' not in words and not any(w.startswith('--list-tests') for w in words)

    def env(self):
        return {'DOTNET_CLI_UI_LANGUAGE': 'en', 'VSLANG': '1033'}

    COLLECTOR_VALUE = ('--filter', '-c', '--configuration', '-f', '--framework', '-r', '--runtime', '--arch', '--os')
    DROPPED_VALUE = ('--logger', '--results-directory', '--collect', '--diag', '-l', '-d', '-s', '--settings', '--test-adapter-path', '-a', '-v', '--verbosity')
    COLLECTOR_FLAG = ('--no-build', '--no-restore', '--nologo')
    DROPPED_FLAG = ('--blame', '--blame-crash', '--blame-hang', '--no-ansi', '--no-progress', '-nologo')

    def _collector_argv(self, argv):
        """The `dotnet test --list-tests` command for the same tests, or None when the arguments cannot be read unambiguously (then there is no list: nothing is guessed).
        Only a run with `--no-build` is listed (a listing that builds the project would run a second build and can be killed half way)."""
        words = [str(a) for a in argv]
        if not self._is_test_command(words):
            return None
        out, i, build_off = [], words.index('test') + 1, False
        out = words[:i]
        while i < len(words):
            word = words[i]
            if word == '--':
                break   # arguments for the test run settings: not for a listing
            name = word.split('=', 1)[0]
            if name in self.COLLECTOR_FLAG and '=' not in word:
                build_off = build_off or name == '--no-build'
                out.append(word)
            elif name in self.DROPPED_FLAG:
                pass
            elif name in self.COLLECTOR_VALUE:
                if '=' in word:
                    out.append(word)
                elif i + 1 < len(words):
                    out += [word, words[i + 1]]
                    i += 1
                else:
                    return None
            elif name in self.DROPPED_VALUE:
                i += 0 if '=' in word else 1
            elif not word.startswith('-'):
                out.append(word)   # the project or the solution
            else:
                return None   # a flag the adapter does not know (MTP selection, MSBuild properties...): the listing could differ from the run
            i += 1
        return out + ['--list-tests'] if build_off else None

    def begin(self, ctx):
        """The list of the tests to run (`dotnet test --list-tests` for a `--no-build` run), read by structure (the header is localized, the names are not)."""
        argv = self._collector_argv(ctx.argv)
        if argv is None:
            return
        text = ctx.collect(argv)
        if not text:
            return
        names = []
        for line in text.splitlines():
            found = LIST_LINE.match(ANSI.sub('', line[:MAX_LINE]).rstrip())
            if found:
                ident = to_id(found.group('name'))
                if ident:
                    names.append(ident[0])
        if names:
            ctx.emit.collected(len(names), names[:MAX_COLLECTED])

    def _report(self, name, state, ctx, msg=None):
        ident = to_id(name)
        if ident is None:
            return None
        ident, label, group = ident
        if self.state.get(ident) == 'failed' and state == 'passed':
            return ident   # the same test twice (several target frameworks): a failure is not taken back by a later pass; the TRX settles it
        if self.state.get(ident) == state and not msg:
            return ident
        if ident not in self.state and len(self.state) >= MAX_STATE:
            return None
        self.state[ident] = state
        ctx.emit.test(ident, label, state, group=group, msg=msg)
        return ident

    def _flush_message(self, ctx):
        if self.msg_of is not None and self.msg_lines:
            ident, name, group, state = self.msg_of
            text = ' '.join(line.strip() for line in self.msg_lines[:3])[:500]
            if text:
                self.state[ident] = state
                ctx.emit.test(ident, name, state, group=group, msg=text)
        self.msg_of, self.msg_lines = None, []

    def feed(self, stream, text, ctx):
        text = ANSI.sub('', text[:MAX_LINE]).rstrip()
        # the message of a failed or skipped VSTest result: `  Error Message:` then lines up to `  Stack Trace:`, a blank line or the next result
        if self.in_stdout:
            if not text.strip():
                self.in_stdout = False   # the output of the test ends with an empty line: what it printed is not a result
            return
        if STDOUT_SECTION.match(text):
            self._flush_message(ctx)
            self.in_stdout = True
            return
        if self.msg_of is not None and not text.startswith('[xUnit.net'):
            if MESSAGE_HEAD.match(text):
                return
            if MESSAGE_END.match(text) or not text.strip() or VSTEST.match(text):
                self._flush_message(ctx)
            else:
                if len(self.msg_lines) < MAX_MESSAGE_LINES:
                    self.msg_lines.append(text)
                return
        found = XUNIT.match(text)
        if found:
            state = 'failed' if found.group('tag') == 'FAIL' else 'skipped'
            self._report(found.group('name'), state, ctx)
            return
        if MESSAGE_HEAD.match(text):
            return   # the lines that follow are collected by the branch above (msg_of is set by the result line)
        for pattern in (VSTEST, MTP):
            found = pattern.match(text)
            if found:
                word = found.group('word').lower()
                state = WORDS.get(word)
                if state is None:
                    return   # a word the adapter does not know (another language): ignored, the TRX has the truth
                name = found.group('name')
                ident = self._report(name, state, ctx)
                if ident is not None and state != 'passed' and pattern is VSTEST:
                    self.msg_of, self.msg_lines = (ident, name, to_id(name)[2], state), []
                return

    def end(self, ctx, exit_code, output, reports):
        self._flush_message(ctx)
        return False
