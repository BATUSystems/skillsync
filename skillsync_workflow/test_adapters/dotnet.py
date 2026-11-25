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
import shlex

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
# the end of the run closes the message of the last skipped test (it has no `Stack Trace:`): the results file, the total line and the `Test Run ...` line (no indent, so they are no part of a message)
MESSAGE_RUN_END = re.compile(r'^(Results File|Plik wynikowy):|^(Total tests|Łączna liczba testów):|^(Test Run|Przebieg testu) ', re.I)
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


# ---- `vstest.console.exe`: the ONE reader of the program and its arguments (the table of the tools, `accepts`, `find_reports`, the hook has its own mirror) ----
# It lives here, next to the adapter that reads the output of the program, because `test_run` imports the adapters (the adapter must not import `test_run`).
VSTEST_PROGRAM = 'vstest.console'
VSTEST_INFO = frozenset({'listtests', 'lt', 'listfullyqualifiedtests', 'lfqt', 'listdiscoverers', 'listexecutors', 'listloggers', 'listsettingsproviders', 'help', '?'})
# options whose value may stand in the NEXT word (`/Logger "trx;..."`, `/ResultsDirectory C:\out`); every other option has its value after `:` or `=`, or none
VSTEST_VALUED = frozenset({'logger', 'resultsdirectory', 'tests', 'testcasefilter', 'settings', 'platform', 'framework', 'testadapterpath', 'diag', 'collect', 'parallel'})
VSTEST_POINT = frozenset({'tests', 'testcasefilter'})
_OPTION_WORD = re.compile(r'^(?:/|--?)[A-Za-z?]+(?:[:=]|$)')   # `/Logger:...`, `-?`, `--Tests`; a path such as `/tmp/out` or `C:\\out` is a value


def program_name(word):
    """The lower-case name of a command word without directories (both separators), quotes and `.exe|.cmd|.bat` (the same reading as `test_run._base`, without the `@version`)."""
    name = str(word).strip('\'"').replace('\\', '/').rsplit('/', 1)[-1].lower()
    return re.sub(r'\.(exe|cmd|bat)$', '', name.strip('\'"'))


def expand_cmd(words):
    """`cmd /c "vstest.console.exe X.dll /Logger:trx"` (the whole command in ONE quoted word): the words of that third word are split in place, so the program is found as usual.
    Narrow on purpose: the first word is `cmd`, the second `/c` or `/k` (any case) and the third contains a space (and is not itself the path of `vstest.console`); anything else is returned unchanged. The inner text is split
    with POSIX quoting and a literal backslash (a Windows command); an open quote leaves the words as they were."""
    words = [str(w) for w in words]
    if len(words) >= 3 and program_name(words[0]) == 'cmd' and words[1].lower() in ('/c', '/k') and re.search(r'\s', words[2].strip()) and program_name(words[2]) != VSTEST_PROGRAM:
        lexer = shlex.shlex(words[2], posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ''
        lexer.escape = ''
        try:
            return words[:2] + list(lexer) + words[3:]
        except ValueError:
            return words
    return words


def vstest_options(args):
    """The arguments of `vstest.console` as `(name, value)` pairs: `name` is lower case without the prefix (`/`, `-` or `--`, any case: `/ResultsDirectory` = `--resultsdirectory`), `value` the text
    after `:` or `=` (or, for the options in `VSTEST_VALUED`, the next word when there is none) and `None` without one; a word that is no option (a test source) is `('', word)`.
    Stops at a lone `--` (what follows are run settings, not arguments of the program)."""
    out, i, args = [], 0, [str(a) for a in args]
    while i < len(args):
        word = args[i]
        if word == '--':
            break
        if word[:1] == '/' or (word[:1] == '-' and len(word) > 1):
            body = word[2:] if word.startswith('--') else word[1:]
            name, sep, value = re.split(r'([:=])', body, maxsplit=1) if re.search(r'[:=]', body) else (body, '', None)
            name = name.lower()
            if not sep and name in VSTEST_VALUED and i + 1 < len(args) and not _OPTION_WORD.match(args[i + 1]):
                value = args[i + 1]
                i += 1
            out.append((name, value))
        else:
            out.append(('', word))
        i += 1
    return out


def vstest_command(command):
    """`(program_word, options)` of a command that runs `vstest.console` (a list of words; the program may stand anywhere: behind a wrapper, `cmd /c`, with a full path, in any case and with
    or without `.exe`), `None` otherwise. `options` as in `vstest_options`. The single reader for `test_run.detect_tool`, `accepts` and `test_run.find_reports`."""
    words = expand_cmd(command)
    for index, word in enumerate(words):
        if program_name(word) == VSTEST_PROGRAM:
            return word, vstest_options(words[index + 1:])
    return None


def vstest_info(options):
    """`True` when the arguments only inform (`/ListTests`, `/lt`, `/Help`, `/?`, `/ListDiscoverers`...): nothing is run."""
    return any(name in VSTEST_INFO for name, _ in options)


@register
class DotnetAdapter(Adapter):
    name = 'dotnet'
    tools = ('dotnet', 'vstest')
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
        if not self._is_test_command(words):
            found = vstest_command(words)
            return found is not None and not vstest_info(found[1])
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
        out, i, build_off = [], next(k for k, w in enumerate(words) if re.sub(r'\.(exe|dll)$', '', w.replace('\\', '/').rsplit('/', 1)[-1].lower()) == 'dotnet') + 2, False
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
                    names.append(ident[0].replace('\\', '/'))   # the event writer normalises the ids of `test` events the same way
        if names:
            ctx.emit.collected(len(names), names[:MAX_COLLECTED])

    def _report(self, name, state, ctx, msg=None):
        ident = to_id(name)
        if ident is None:
            return None
        ident, label, group = ident
        if self.state.get(ident) == 'failed' and state in ('passed', 'skipped'):
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
            if MESSAGE_END.match(text) or not text.strip() or MESSAGE_RUN_END.match(text) or VSTEST.match(text):
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
