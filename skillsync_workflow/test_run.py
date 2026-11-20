"""`skillsync test-run` and the observer of the shared runner (180-A, stage 1).

`test-run <project> [--run RUN] [--step ID] [--junit FILE|DIR ...] [--timeout S] -- <command...>` runs a test command of the project through the
shared runner (`quality.run_validation`) with an observer that writes the events of the run (`test_events`): the state of the command on the fly,
live counters where the output prints them (Karma "Executed N of M"), the list of the tests after the end from a JUnit XML or TRX report
(`--junit`, or the default places of dotnet, Maven and Gradle). The output of the command is passed through unchanged and the exit code of the
command is the exit code of `test-run` when the command failed (FAIL), and 0 when the run is a PASS. A command that exited 0 without the proof of
passing tests (status BLOCKED, or no recorded status at all) gives exit code 2, as `validate` does; 124 is a timeout and 127 a missing program.
An explicit `--run <id>` needs a trace of the run in `.skillsync/runs/` (a directory or a `<id>.json` record), else it is refused (BLOCKED, 2).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import socket
import sys
import threading
import time
from pathlib import Path

from .common import read_project
from .fsutil import isabs
from .dashboard import ids_in_progress, parse_checkpoints
from .quality import _test_counts, evidence_reason, karma_unexecuted_reason, run_validation
from .runs import _slug
from . import test_events as events
from . import test_gate
from . import test_adapters
from .test_adapters import karma_line, playwright as playwright_adapter, vitest as vitest_adapter
from . import test_reports

REPORT_SINCE_SLACK_S = 2.0
MAX_REPORT_FILES = 200

def _failure_count(output):
    """Failed tests named by the summary of the common tools (unittest `FAILED (failures=2, errors=1)`, pytest `3 failed`, Karma `(N FAILED)` of a valid `Executed N of M` line); 0 when the output names none. Callers read it only for a non-zero exit code."""
    total = 0
    unittest_summary = re.search(r'FAILED \(([^)]*)\)', output)
    if unittest_summary:
        total = sum(int(n) for kind, n in re.findall(r'(failures|errors)=(\d+)', unittest_summary.group(1)))
    found = re.search(r'(\d+) failed', output)
    total = max(total, int(found.group(1))) if found else total
    karma = karma_line.read_output(output)
    return max(total, karma['failed']) if karma else total


_UNITTEST_FAILED = re.compile(r'(?m)^FAILED \(([^)\n]*)\)')
_PYTEST_SUMMARY = re.compile(r'(?m)^(?:=+ )?(?:\d+ (?!failed)\w+(?: \w+)?, )*(\d+) failed\b[^\n]* in [\d.:]+s')


_JEST_TESTS = re.compile(r'^Tests:\s+(\d+) failed,')
_MAX_SUMMARY_LINE = 2000   # a longer line is no summary line (and a regex on a megabyte line must not stall the end of the run)


def _hidden_failures(output, source='regex'):
    """Failed tests that a clean exit code hides: the exact summaries of unittest (`FAILED (failures=2, errors=1)`) and pytest (`3 failed, 5 passed in 2s`,
    also inside the `=====` banner), not any `N failed` of a log line (`3 failed attempts`, `0 failed`, `failed=0`). With the counters read from the output itself
    (`source` is `regex`: no adapter and no report) also the anchored summary lines of Playwright (`  1 failed`, `  2 interrupted`), Vitest (the `Tests` line, never
    `Test Files`) and Jest (`Tests: 1 failed, ...`), read with the patterns of the adapters; for `junit` and `adapter` those lines are the business of the report
    or of the adapter. 0 when the output names none."""
    output = events._ANSI.sub('', output)   # a coloured summary (`--color=yes`) starts with an escape, not with the text
    total = 0
    for found in _UNITTEST_FAILED.finditer(output):
        total = max(total, sum(int(n) for kind, n in re.findall(r'(failures|errors)=(\d+)', found.group(1))))
    for found in _PYTEST_SUMMARY.finditer(output):
        total = max(total, int(found.group(1)))
    if source == 'regex':
        for line in output.splitlines():   # splitlines drops a trailing `\r` of a Windows console
            if len(line) > _MAX_SUMMARY_LINE:
                continue
            playwright_line = playwright_adapter.SUMMARY.match(line)
            if playwright_line and playwright_line.group('kind') in ('failed', 'interrupted'):
                total = max(total, int(playwright_line.group('n')))
            vitest_line = vitest_adapter.TESTS.match(line)
            if vitest_line:
                for count, kind in vitest_adapter.SUMMARY_PART.findall(vitest_line.group('body')):
                    if kind == 'failed':
                        total = max(total, int(count))
            jest_line = _JEST_TESTS.match(line)
            if jest_line:
                total = max(total, int(jest_line.group(1)))
    return total


def _ms(seconds):
    """Milliseconds of a duration, 0 for anything that is not a finite non-negative number (an `OverflowError` must not cost the `end` event)."""
    try:
        value = int(1000 * float(seconds or 0))
    except (TypeError, ValueError, OverflowError):
        return 0
    return value if 0 <= value <= events.MAX_NUMBER else 0


TOOLS_FILE = Path(__file__).resolve().parent / 'data' / 'test_tools.json'
_TABLE = None


def tool_table():
    """The table of the tools and of the wrappers (`data/test_tools.json`, schema 1), read once. A missing or broken file is a loud error (`ValueError`):
    there is no silent fallback, `test_skillsync_adapter_contract` and the packaging check keep the file in the package."""
    global _TABLE
    if _TABLE is None:
        try:
            table = json.loads(TOOLS_FILE.read_text(encoding='utf-8'))
            if table.get('schema') != 1:
                raise ValueError(f"schema {table.get('schema')!r}, expected 1")
            tools = tuple((str(item['word']), str(item['tool'])) for item in table['tools'])
            modules = {str(word): str(tool) for word, tool in table['module_words'].items()}
            wrappers = {str(item['word']): item for item in table['wrappers']}
            layers = int(table['max_layers'])
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise ValueError(f'the table of the test tools {TOOLS_FILE} is missing or broken: {error}') from error
        _TABLE = {'tools': tools, 'modules': modules, 'wrappers': wrappers, 'layers': layers}
    return _TABLE


def __getattr__(name):   # `test_run.TOOLS` and `MAX_WRAPPER_LAYERS` stay readable names; the data is loaded on first use
    if name == 'TOOLS':
        return tool_table()['tools']
    if name == 'MAX_WRAPPER_LAYERS':
        return tool_table()['layers']
    raise AttributeError(name)


def _base(word):
    """The lower-case name of a command word without directories (both separators, whatever the system), quotes, `.exe|.cmd|.bat` and `@version` (`jest@29`)."""
    name = str(word).strip('\'"').replace('\\', '/').rsplit('/', 1)[-1].lower()
    name = re.sub(r'\.(exe|cmd|bat)$', '', name.strip('\'"'))
    return re.sub(r'(?<=.)@[^@/]*$', '', name)


def _is_windows():
    return os.name == 'nt'


def _shell_words(text):
    """The words of a shell string with POSIX quoting rules on every system (`'A=1 cmd'`, `"a b"`, `-S'A=1 cmd'` are one word each), so a command is read the same on Windows
    and elsewhere. On Windows a backslash stays literal (`C:\\Program Files\\node.exe`): only quotes group words. Raises `ValueError` on an open quote."""
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ''
    if _is_windows():
        lexer.escape = ''
    return list(lexer)


def _split(command):
    """The words of a command given as an argv list, a tuple or a shell string; a string that cannot be split (an open quote) gives no words, never an exception."""
    if isinstance(command, (list, tuple)):
        return [str(word) for word in command]
    if isinstance(command, str):
        try:
            return _shell_words(command)
        except ValueError:
            return []
    return []


def _skip_flags(words, boolean, valued, end_at_double_dash=True, stop=None, numeric=False):
    """Index of the first word after the flags of a wrapper, or None when a flag is not known (nothing is guessed)."""
    i = 0
    while i < len(words):
        word = words[i]
        if end_at_double_dash and word == '--':
            return i + 1
        if word == stop:
            return i
        if not word.startswith('-') or word == '-':
            return i
        name = word.split('=', 1)[0]
        if numeric and re.fullmatch(r'-\d+', word):
            i += 1
            continue
        if len(word) > 2 and word[0] == '-' and word[1] != '-' and word[:2] in valued and '=' not in word:
            i += 1   # a short flag with its value attached: `-n10`
            continue
        if name in valued:
            i += 1 if '=' in word else 2
        elif name in boolean and '=' not in word:
            i += 1
        else:
            return None
    return i


def _split_string(rest, spec, boolean, valued):
    """`env -S 'A=1 cmd'` (the optional `split_flags` of a wrapper): the words of `rest` with the value of the flag split like a shell line and the arguments behind
    it appended. `False` when `rest` has no such flag; `None` when the value cannot be split safely (`$` or an open quote): nothing is guessed."""
    flags = set(spec['split_flags'])
    for j, word in enumerate(rest):
        if word == '--':
            return False
        if word in flags:
            value, tail = (rest[j + 1], rest[j + 2:]) if j + 1 < len(rest) else (None, None)
        elif word.startswith('--') and word.split('=', 1)[0] in flags and '=' in word:
            value, tail = word.split('=', 1)[1], rest[j + 1:]
        elif not word.startswith('--') and len(word) > 2 and word[:2] in flags:
            value, tail = word[2:], rest[j + 1:]
        else:
            continue
        skip = _skip_flags(rest[:j], boolean, valued, end_at_double_dash=False)   # the options of the wrapper stand before its assignments and its command
        if skip is None:
            return None   # a flag in front that is not known
        if skip != j:
            return False   # an assignment or the command stands in front: this is a flag of the command, not of the wrapper
        if value is None or '$' in value:
            return None
        try:
            return _shell_words(value) + tail
        except ValueError:
            return None
    return False


def _unwrap_once(argv):
    """The command behind ONE wrapper of the table, or None when argv[0] is not a known wrapper or the wrapper is not understood. The syntax is DATA
    (`data/test_tools.json`); this is its interpreter."""
    table = tool_table()
    spec = table['wrappers'].get(_base(argv[0]))
    if spec is None:
        return None
    rest = [str(w) for w in argv[1:]]
    boolean, valued = spec.get('boolean') or (), spec.get('valued') or ()
    if spec.get('subcommands'):
        if rest[:1] and rest[0] in spec['subcommands']:
            rest = rest[1:]
        elif spec.get('tool_words') and rest and _base(rest[0]) in {word for word, _ in table['tools']}:
            return rest   # `yarn playwright test`: the first word is a tool, not a script
        else:
            return None
    if spec.get('module_flag'):
        skip = _skip_flags(rest, boolean, valued, end_at_double_dash=False, stop=spec['module_flag'])
        if skip is None or rest[skip:skip + 1] != [spec['module_flag']] or skip + 1 >= len(rest):
            return None
        return rest[skip + 1:]
    if spec.get('split_flags'):
        split = _split_string(rest, spec, boolean, valued)
        if split is None:
            return None
        if split is not False:
            while split and spec.get('assignments') and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', split[0]):
                split = split[1:]
            return split or None
    skip = _skip_flags(rest, boolean, valued, numeric=bool(spec.get('numeric_flag')))
    if skip is None:
        return None
    rest = rest[skip:][int(spec.get('positional') or 0):]
    if spec.get('assignments'):
        while rest and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', rest[0]):
            rest = rest[1:]
    return rest or None


def unwrap_command(command):
    """The argv of the command behind the wrappers of the table (`npx`, `npm exec`, `pnpm exec|dlx`, `yarn`, `timeout`, `env` (also `env -S 'A=1 cmd'`), `time`, `nice`, `systemd-run`,
    `uv|poetry|pdm|pipenv run`, `python -m`), at most `max_layers` of them. `command` is an argv list or a shell string. A wrapper or a flag that is not in the table stops the
    unwrapping (the words so far are returned), and more layers than the limit return the original argv: nothing is guessed."""
    argv = _split(command)
    current = argv
    for _ in range(tool_table()['layers']):
        if not current:
            return argv
        inner = _unwrap_once(current)
        if inner is None:
            return current
        current = inner
    return argv if current and _unwrap_once(current) is not None else current


def _is_host(word):
    """A program that runs `-m MODULE`: `python*`, `py`, `pypy*`, `coverage` (a name as `_base` gives it)."""
    return word.startswith(('python', 'pypy')) or word in ('py', 'coverage')


def detect_tool(command):
    """Name of the tool of a command (`argv` list or shell string), in this order: (1) the first word behind the known wrappers, as a module word, then as a tool
    (`pytest`, `npx playwright test` -> `playwright`, `timeout -k 5 100 cargo test` -> `cargo`); (2) the word after `-m` when a Python host (`python*`, `py`, `pypy*`,
    `coverage`) stands before it (`python -W error -m unittest`); (3) a tool of the table in the first three words; (4) a module word that is also a tool
    (`pytest`) anywhere; else `unknown`. A module that is no tool (`unittest`) matches nowhere else. Public and stable: the one table of the tools
    (`data/test_tools.json`) for every consumer (the adapters, the hook of tests outside test-run)."""
    table = tool_table()
    argv = _split(command)
    words = [_base(word) for word in argv]
    core = unwrap_command(argv)
    first = _base(core[0]) if core else None
    if first is not None:
        if first in table['modules']:
            return table['modules'][first]
        for name, tool in table['tools']:
            if first == name:
                return tool
    for i, word in enumerate(words[:-1]):
        if word == '-m' and words[i + 1] in table['modules'] and any(_is_host(before) for before in words[:i]):
            return table['modules'][words[i + 1]]
    for word in words[:3]:
        for name, tool in table['tools']:
            if word == name:
                return tool
    tool_names = {name for name, _ in table['tools']}
    for word in words:
        if word in table['modules'] and word in tool_names:
            return table['modules'][word]
    return 'unknown'


def resolve_executable(argv, which=None, nt=None):
    """On Windows `npm`, `npx`, `ng`, `pnpm` and `yarn` are `.cmd` shims that CreateProcess does not start by the bare name: argv[0] becomes what
    `shutil.which` finds (it honours PATHEXT). On other systems and when nothing is found argv is returned unchanged."""
    argv = list(argv)
    if not argv or not (os.name == 'nt' if nt is None else nt):
        return argv
    found = (which or shutil.which)(str(argv[0]))
    return [found] + argv[1:] if found else argv


CMD_METACHARS = frozenset('&|<>^%"')


def cmd_metachar_notice(argv, nt=None):
    """Warning (stderr, before the start) for Windows only: cmd.exe re-parses the arguments of a `.cmd`/`.bat` target, so `& | < > ^ %` and `"` in them may
    split or alter the command. `argv` is the RESOLVED one. The text never repeats an argument (it may hold a secret). None when there is nothing to say."""
    if not (os.name == 'nt' if nt is None else nt):
        return None
    argv = [str(a) for a in argv or []]
    if not argv or not argv[0].lower().endswith(('.cmd', '.bat')):
        return None
    if not any(CMD_METACHARS & set(arg) for arg in argv[1:]):
        return None
    return ('test-run: warning: an argument of a .cmd/.bat command contains one of & | < > ^ % or a double quote, and cmd.exe interprets these characters '
            'in the arguments of batch files (for example --grep "a|b" of Playwright), so the command may be split or changed. Quote or escape the '
            'argument so that cmd.exe does not split it, or move the value to a configuration file of the tool. The command runs as given.')


def adapter_notice(fmt, argv, tool, adapter, junit=()):
    """The line of `test-run` about the choice of the adapter (stderr, before the command starts), only for `--format auto`: an explicit name or `none` is silent."""
    if fmt not in (None, 'auto'):
        return None
    if adapter is not None:
        hint = adapter.hint(list(argv or []), bool(junit))
        return f'test-run: adapter {adapter.name} (live {adapter.live})' + (f'; {hint}' if hint else '')
    test_adapters.discover()
    names = sorted(test_adapters.REGISTRY)
    same_tool = [name for name in names if tool in test_adapters.REGISTRY[name].tools]
    if tool == 'node' and 'node-test' in same_tool:
        core = unwrap_command(list(argv or []))
        from skillsync_workflow.test_adapters.node_test import scan
        has_test, reporters, destination = scan(core)
        if has_test:
            if destination or len(set(reporters)) > 1:
                return 'test-run: node-test reads a single stream on stdout: give a single --test-reporter=spec or tap, without --test-reporter-destination or other reporters'
            if reporters:
                return 'test-run: node-test reads only the reporters tap and spec: replace the reporter with --test-reporter=spec or tap'
            return 'test-run: node-test reads only the reporters tap and spec: add --test-reporter=spec or tap'
        return 'test-run: adapter node-test is not chosen automatically for this command: add --format tap (a bare script) or --format node-test (a file with node:test)'
    if same_tool:
        return f'test-run: adapter {", ".join(same_tool)} not used: it does not accept these arguments (see docs/test-adapters.md); counters and the report only'
    return f'test-run: no adapter for {tool} (known: {", ".join(names) or "none"}); pass --format <name>, or --format none to silence'


def _run_names(root):
    """`(directories, records)` of `.skillsync/runs/` as the listing of the directory gives them (never a symbolic link): the names are compared with
    the listing, so a file system that ignores case does not accept `r1` for `R1`."""
    base = Path(root) / '.skillsync' / 'runs'
    if not base.is_dir() or base.is_symlink():
        return [], []
    dirs, records = [], []
    try:
        entries = list(base.iterdir())
    except OSError:
        return [], []
    for entry in entries:
        if entry.is_symlink():
            continue
        if entry.is_dir():
            dirs.append(entry.name)
        elif entry.is_file() and entry.name.endswith('.json') and len(entry.name) > 5:
            records.append(entry.name[:-5])
    return sorted(dirs), sorted(records)


def resolve_run(root, explicit):
    """`(run id, error)`: the explicit id (validated, and with a trace in `.skillsync/runs/`: a directory or a `<id>.json` record, compared with the
    listing exactly), else the only directory of `.skillsync/runs/`."""
    dirs, records = _run_names(root)
    if explicit:
        run_id = _slug(explicit, '--run')
        if run_id in dirs or run_id in records:
            return run_id, None
        known = sorted(set(dirs) | set(records))
        if not known:
            return None, f'--run {run_id}: no run in .skillsync/runs/'
        return None, f'--run {run_id}: no such run in .skillsync/runs/ (runs: {", ".join(known[:8])})'
    names = dirs
    if len(names) == 1:
        return names[0], None
    return None, ('--run is required: ' + (f'{len(names)} runs in .skillsync/runs/ ({", ".join(names[:8])})' if names else 'no run in .skillsync/runs/'))


def resolve_step(root, run_id, explicit):
    """`(step, source)`: the explicit `--step`; else the ONLY step "w toku" in the newest checkpoint that has the phrase; `(None, 'ambiguous')` when several steps
    are in progress (the caller must pass `--step`: parallel sub-minds, no guessing); else `(None, 'none')`."""
    if explicit:
        return _slug(explicit, '--step'), 'flag'
    path = Path(root) / '.skillsync' / 'runs' / run_id / 'checkpoints.md'
    try:
        if path.is_symlink() or not path.is_file():
            return None, 'none'
        _, items = parse_checkpoints(path.read_text(encoding='utf-8', errors='replace'))
    except OSError:
        return None, 'none'
    for item in reversed(items):
        ids = ids_in_progress(item['text'])
        if ids is not None:
            return (ids[0], 'checkpoint') if len(ids) == 1 else (None, 'ambiguous' if ids else 'none')
    return None, 'none'


def find_reports(tool, cwd, explicit, since):
    """Report files of a run: the explicit ones (a file, or a directory searched for `*.xml` and `*.trx`), else the default places of dotnet,
    Maven and Gradle; only files written since the run started."""
    cwd = Path(cwd)
    candidates = []
    for item in explicit:
        path = Path(item) if isabs(item) else cwd / item
        if path.is_dir():
            candidates += [p for pattern in ('*.xml', '*.trx') for p in path.rglob(pattern)]
        elif path.is_file():
            candidates.append(path)
    if not explicit:
        karma = ['TESTS-*.xml', '*/TESTS-*.xml', 'junit*/*.xml', 'junit*/*/*.xml']   # karma-junit-reporter: `<outputDir>/<browser>/TESTS-*.xml`, outputDir often `junit...`
        defaults = {'karma': karma, 'angular': karma, 'dotnet': ['**/TestResults/*.trx'], 'maven': ['**/target/surefire-reports/*.xml', '**/target/failsafe-reports/*.xml'],
                    'gradle': ['**/build/test-results/**/*.xml']}.get(tool, [])
        for pattern in defaults:
            candidates += list(cwd.glob(pattern))
    found = []
    for path in sorted(set(candidates)):
        try:
            if path.is_file() and not path.is_symlink() and path.stat().st_mtime >= since - REPORT_SINCE_SLACK_S:
                found.append(path)
        except OSError:
            continue
        if len(found) >= MAX_REPORT_FILES:
            break
    return found


class TestRecorder:
    """Observer of `quality.run_validation` that records one attempt of a step (`started`, `line`, `finished`)."""
    __test__ = False   # not a test case for unittest/pytest discovery

    def __init__(self, root, run_id, step, step_source, mask=False, junit=(), passthrough=False, allow_no_count=None, allow_skips=False, adapter=None):
        self.root, self.run_id, self.step, self.step_source = Path(root), run_id, step, step_source
        self.mask, self.junit, self.passthrough = mask, list(junit), passthrough
        self.allow_no_count, self.allow_skips = allow_no_count, bool(allow_skips)
        self.adapter, self.ctx = adapter, None   # the adapter of the tool (stage 2); dropped for the rest of the run when it raises
        self.writer = None
        self.path = None
        self.number = None
        self.started_wall = None
        self.tree_start = None
        self.ended = False
        self.karma = karma_line.Tally()   # the live counters of Karma-style `Executed N of M` lines when no adapter is selected
        self.last = None

    def started(self, check):
        self.path, self.number = events.new_attempt(self.root, self.run_id, self.step)
        self.writer = events.EventWriter(self.path, mask=self.mask)   # a failure here (disk, permissions) reaches `run_validation`: the command does not start
        command = check.get('command') or ' '.join(shlex.quote(a) for a in check.get('display') or check.get('argv') or [])   # `display`: the command of the user, before `resolve_executable`
        self.started_wall = time.time()
        try:
            self.tree_start = test_gate.work_tree_id(self.root)   # the content the tests START on (the gate compares it with the end)
        except Exception:   # noqa: BLE001 - never lose the run over the fingerprint
            self.tree_start = None
        self.writer.start(treeStart=self.tree_start, run=self.run_id, step=self.step, stepSource=self.step_source, attempt=self.number, command=command,
                          tool=detect_tool(check.get('argv') or check.get('command')), pid=os.getpid(), levels='command',
                          **({'allowNoCount': events.clean_text(self.allow_no_count, 300, mask=True)} if self.allow_no_count else {}),
                          **({'allowSkips': True} if self.allow_skips else {}), **({'adapter': self.adapter.name, 'live': self.adapter.live} if self.adapter is not None else {}))
        if self.adapter is not None:
            argv = check.get('argv') or shlex.split(check.get('command') or '')
            self.ctx = test_adapters.AdapterContext(argv, check['cwd'], self.root, self.writer, tool=detect_tool(argv), argv_core=unwrap_command(argv))
            self._adapter_call(lambda: self.adapter.begin(self.ctx))

    def _adapter_call(self, fn):
        """Calls the adapter; any exception drops the adapter for the rest of the run (the counters of the summary and the report remain)."""
        if self.adapter is None:
            return None
        try:
            return fn()
        except Exception:
            self.adapter = None
            return None

    def partial(self, check, stream, text):
        if self.adapter is not None:
            self._adapter_call(lambda: self.adapter.partial(stream, text, self.ctx))

    def _echo(self, stream, text):
        """The line to the terminal of the caller, AFTER the adapter and the counters have seen it: a closed pipe or a console that cannot encode a character
        (cp1252, Polish letters) must not cost the record of the run. A closed pipe switches the echo off; an unencodable character becomes `?`."""
        target = sys.stderr if stream == 'err' else sys.stdout
        try:
            print(text, file=target, flush=True)
        except UnicodeEncodeError:
            try:
                print(text.encode(getattr(target, 'encoding', None) or 'ascii', 'replace').decode(getattr(target, 'encoding', None) or 'ascii', 'replace'), file=target, flush=True)
            except (OSError, ValueError):
                self.passthrough = False
        except (OSError, ValueError):   # BrokenPipeError, a closed stream
            self.passthrough = False

    def line(self, check, stream, text):
        try:
            self._line(check, stream, text)
        finally:
            if self.passthrough:
                self._echo(stream, text)

    def _line(self, check, stream, text):
        if self.adapter is not None:
            self._adapter_call(lambda: self.adapter.feed(stream, text, self.ctx))
        if self.adapter is None and self.writer is not None and self.karma.feed(text):   # a selected adapter owns the counters; the built-in reader only works without one
            totals = self.karma.totals()
            self.writer.counts(passed=totals['passed'], failed=totals['failed'], skipped=totals['skipped'], running=0, total=totals['total'])

    def finished(self, check, result, output):
        writer = self.writer
        if writer is None:
            return
        try:
            self._finish(check, result, output)
        except KeyboardInterrupt:   # SIGTERM while the end is being worked out: the attempt still gets its `end`, and the signal is not lost on the caller
            if not self.ended:
                writer.end('BLOCKED', result.get('exit_code'), reason='Interrupted while the end of the run was recorded', ms=_ms(result.get('duration_seconds')))
                self.ended = True
                self.last = {'status': 'BLOCKED', 'attempt': self.number, 'file': self.path, 'source': None}
        finally:
            writer.close()

    def _adapter_counts(self, complete):
        """The end counters from what the adapter saw, or None. A COMPLETE stream (`end` returned True: every test of the run was seen, none is truncated) gives
        the final state of every test; a counters-only adapter (Karma) gives its last counters when nothing runs any more and they add up to the total. Anything else
        (interrupted, truncated, nothing emitted) stays with the report and the summary of the output."""
        writer = self.writer
        if writer.truncated:
            return None
        if complete and writer.final:
            states = list(writer.final.values())
            if 'running' not in states:
                return {'total': len(states), 'passed': states.count('passed'), 'failed': states.count('failed'), 'skipped': states.count('skipped')}
            return None
        latest = writer.latest
        if latest and latest[4] and not latest[3] and latest[0] + latest[1] + latest[2] == latest[4]:
            return {'total': latest[4], 'passed': latest[0], 'failed': latest[1], 'skipped': latest[2]}
        return None

    def _finish(self, check, result, output):
        writer = self.writer
        code = result.get('exit_code')
        tool = detect_tool(check.get('argv') or check.get('command'))
        counts, source, ran, skipped, failed = None, 'regex', None, None, 0
        reports = find_reports(tool, check['cwd'], self.junit, self.started_wall or 0) if output is not None else []
        skip_reports = bool(self._adapter_call(lambda: self.adapter.end(self.ctx, code, output or '', list(reports)))) if self.adapter is not None else False
        listed = []
        for report in ([] if skip_reports else reports):
            try:
                parsed, info = test_reports.parse_any(report, with_info=True)
            except (OSError, ValueError):
                continue
            listed += parsed
            if info.get('truncated'):
                writer.truncated = True
        if listed:
            source = 'junit'
            for item in listed:
                writer.test(item['id'], item['name'], item['state'], group=item.get('group'), ms=item.get('ms'), msg=item.get('msg'))
            failed = sum(1 for i in listed if i['state'] == 'failed')
            skipped = sum(1 for i in listed if i['state'] == 'skipped')
            ran = len(listed) - skipped
            counts = {'total': len(listed), 'passed': sum(1 for i in listed if i['state'] == 'passed'), 'failed': failed, 'skipped': skipped}
        elif self.adapter is not None and (known := self._adapter_counts(bool(skip_reports))) is not None:
            counts, source = known, 'adapter'
            failed, skipped = counts['failed'], counts['skipped']
            ran = counts['total'] - skipped
        elif output is not None:
            ran, skipped = _test_counts(output)
            if ran is not None:
                failed = _failure_count(output) if code else 0   # a clean exit never reads "3 failed" out of a log line
                counts = {'total': ran + (skipped or 0), 'passed': max(ran - failed, 0), 'failed': failed, 'skipped': skipped or 0}
        if code is None:
            status, reason = 'BLOCKED', result.get('reason') or 'not run'
        elif result.get('reason', '').startswith('Timeout'):
            status, reason = 'BLOCKED', result['reason']
        elif code != 0:
            status, reason = 'FAIL', None
        else:
            hidden = _hidden_failures(output or '', source) if not failed else 0
            if hidden:
                failed = hidden
                if counts is not None:
                    counts = {**counts, 'failed': hidden, 'passed': max(counts['passed'] - hidden, 0)}
            reason = (f'the exit code is 0 but the report has {failed} failed tests' if listed and not hidden else
                      f'the exit code is 0 but the output reports {failed} failed tests (a pipe, `|| true` or a wrapper may hide the real exit code of the test command)') if failed \
                else evidence_reason('test', ran, skipped, False, self.allow_skips)
            if reason and self.allow_no_count and ran is None and not failed:
                reason = None
            if not reason and not failed and output:   # the count is known, so no allowance lifts it: specs of an interrupted Karma run never ran
                reason = karma_unexecuted_reason(output)
            status = 'BLOCKED' if reason else 'PASS'
        try:
            tree = test_gate.work_tree_id(self.root)
        except Exception:   # never lose the end of the run over the fingerprint
            tree = None
        writer.end(status, code, reason=reason, counts=counts, ms=_ms(result.get('duration_seconds')), source=source, tree=tree)
        self.ended = True
        self.last = {'status': status, 'attempt': self.number, 'file': self.path, 'source': source}


def import_events(root, run_id, step, source, source_sha):
    """`test-run --import`: validates an events file written on another machine (every line `v: 1`, a `start` of this run and step, an `end`, within
    the limits) and stores it, byte for byte, as the next attempt of the step, followed by one `import` event that names the commit it was made for
    (`--source-sha`: the copies made with `git archive` have no `.git`). Returns `(path, number)`."""
    source = Path(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError(f'not a file: {source.name}')
    if source.stat().st_size > events.MAX_BYTES:
        raise ValueError('the events file is over the limit (8 MiB)')
    data = source.read_bytes()
    if not data.endswith(b'\n'):
        raise ValueError('the events file ends in an incomplete line')
    parsed, _, skipped = events.read_events(source)
    if skipped:
        raise ValueError(f'{skipped} lines of the events file are not events of version {events.EVENT_VERSION}')
    start = next((e for e in parsed if e.get('t') == 'start'), None)
    if start is None or not any(e.get('t') == 'end' for e in parsed):
        raise ValueError('the events file needs a start and an end event')
    if not start.get('host') or not start.get('startedAt'):
        raise ValueError('the events file has no host and startedAt in its start event: it is not a run recorded by `test-run`')
    local = Path(root).resolve() / '.skillsync' / 'runs'
    if start.get('host') == socket.gethostname() or str(source.resolve()).startswith(str(local) + os.sep):
        raise ValueError('--import is for the runs of ANOTHER machine: this file was written here (or lies in the run files of this project); run the tests with `test-run` instead')
    if start.get('run') != run_id or start.get('step') != step:
        raise ValueError(f"the events file belongs to run {start.get('run')!r}, step {start.get('step')!r}, not to {run_id!r}, {step!r}")
    if sum(1 for e in parsed if e.get('t') == 'test') > events.MAX_TEST_EVENTS:
        raise ValueError('the events file has too many test events')
    if not re.fullmatch(r'[0-9a-f]{7,64}', source_sha or ''):
        raise ValueError('--source-sha must be the commit (hex, 7 to 64 characters) the run was made for')
    path, number = events.new_attempt(root, run_id, step)
    with open(path, 'ab') as handle:
        handle.write(data)
        handle.write((json.dumps({'v': events.EVENT_VERSION, 't': 'import', 'sourceSha': source_sha, 'at': events._now()}, separators=(',', ':')) + '\n').encode('utf-8'))
    return path, number


class _Interruptible:
    """While the tests run, SIGTERM (POSIX) or CTRL_BREAK (Windows) takes the path of Ctrl+C: `run_validation` ends the process tree of the command (its own
    session), writes `end` as BLOCKED and releases the step lock; without this the command, started in a session of its own, outlives `test-run`, the
    attempt file has no `end` and a second run starts beside the orphan. Only in the main thread (a handler cannot be installed elsewhere); a second
    signal while the first is being handled is ignored, so it cannot cut the cleanup half way. Windows: the handler of `stopsignal` (CTRL_BREAK); the
    limit there is that a console closed by the window button or `taskkill /F` gives no chance to clean up."""

    def __enter__(self):
        self.previous = None
        self.fired = False
        if threading.current_thread() is not threading.main_thread():
            return self
        if sys.platform == 'win32':
            from . import stopsignal
            self.previous = ('break', stopsignal.install_sigbreak())
            return self

        def handler(signum, frame):
            if self.fired:
                return
            self.fired = True
            raise KeyboardInterrupt
        try:
            self.previous = ('term', signal.signal(signal.SIGTERM, handler))
        except (ValueError, OSError):
            self.previous = None
        return self

    def __exit__(self, *exc):
        try:
            if self.previous and self.previous[0] == 'term':
                signal.signal(signal.SIGTERM, self.previous[1] if self.previous[1] is not None else signal.SIG_DFL)
            elif self.previous and self.previous[0] == 'break' and self.previous[1] is not None:
                signal.signal(signal.SIGBREAK, self.previous[1][0])
        except (ValueError, OSError):
            pass
        return False


def _command(args):
    if getattr(args, 'import_file', None):
        try:
            root, _ = read_project(args.path)
            run_id, problem = resolve_run(root, args.run)
            if problem or not args.step:
                if problem and args.run:
                    problem += f'; copy .skillsync/runs/{args.run}/ (or {args.run}.json) from the machine of the run, or record the run with skillsync checkpoint'
                print(f"BLOCKED: {problem or '--step is required with --import'}", file=sys.stderr)
                return 2
            path, number = import_events(root, run_id, _slug(args.step, '--step'), args.import_file, args.source_sha)
        except (OSError, ValueError) as error:
            print(f'BLOCKED: {error}', file=sys.stderr)
            return 2
        print(f'test-run: imported as attempt {number}, events {path}', file=sys.stderr)
        return 0
    try:
        root, manifest = read_project(args.path, getattr(args, 'manifest', None))
        command = list(args.command or [])
        if command[:1] == ['--']:
            command = command[1:]
        if not command:
            raise ValueError('test-run needs a command after `--`')
        run_id, problem = resolve_run(root, args.run)
        if problem:
            print(f'BLOCKED: {problem}', file=sys.stderr)
            return 2
        step, source = resolve_step(root, run_id, args.step)
        if source == 'ambiguous':
            print('BLOCKED: several steps are in progress in the checkpoints: pass --step <id> (and --run <id>)', file=sys.stderr)
            return 2
        adapter = test_adapters.resolve(args.format, command, detect_tool(command))
        notice = adapter_notice(args.format, command, detect_tool(command), adapter, args.junit or [])
        if notice:
            print(notice, file=sys.stderr)
        recorder = TestRecorder(root, run_id, step, source, mask=events.mask_setting(manifest), junit=args.junit or [], passthrough=True,
                                 allow_no_count=args.allow_no_count, allow_skips=args.allow_skips, adapter=adapter)
        resolved = resolve_executable(command)
        warning = cmd_metachar_notice(resolved)
        if warning:
            print(warning, file=sys.stderr)
        check = {'id': 'test-run', 'argv': resolved, 'display': list(command), 'cwd': str(root), 'kind': 'build', 'timeout': float(args.timeout), 'required': True, '_env': {'PYTHONUNBUFFERED': '1', **(adapter.env() if adapter is not None else {})},
                 'resources': ['test-run:' + str(Path(root).resolve()) + ':' + (step or events.NO_STEP)]}
        with _Interruptible():
            report = run_validation(root, [check], observer=recorder)
    except (OSError, ValueError) as error:
        print(f'BLOCKED: {error}', file=sys.stderr)
        return 2
    result = (report.get('results') or [{}])[0]
    code = result.get('exit_code')
    last = recorder.last or {}
    where = f", events {last['file']}" if last.get('file') else ''
    print(f"test-run: step {step or '-'} ({source}), attempt {last.get('attempt', '-')}: {last['status'] if last.get('status') else 'BLOCKED (no recorded status)'}{where}", file=sys.stderr)
    if code is None:
        print(f"BLOCKED: {result.get('reason') or report.get('reason') or 'the command did not run'}", file=sys.stderr)
        return 127 if 'Missing prerequisites' in (result.get('reason') or '') else 2
    if (result.get('reason') or '').startswith('Timeout'):
        return 124
    code = int(code)
    if code == 0 and last.get('status') != 'PASS':
        print(f"test-run: exit code 2: the command exited 0, but the run is {last.get('status') or 'without a recorded status'} (no proof of passing tests); see the line above", file=sys.stderr)
        return 2
    return code


def split_command(argv):
    """`(head, tail)` of the argument list of `skillsync`: for `test-run`, everything after the FIRST standalone `--` is the command (`tail`, as
    given, later `--` and option-like words included) and `head` is what the parser reads. `tail` is None when the list is not a `test-run` call or has no
    `--` (then `head` is `argv`). Python before 3.12 matches a positional `nargs='*'` EMPTY before the options, so `test-run <project> --step X -- cmd`
    failed there with `unrecognized arguments`; cutting the command off before argparse behaves the same on every version."""
    argv = list(argv)
    if not argv or argv[0] != 'test-run' or '--' not in argv:
        return argv, None
    cut = argv.index('--')
    return argv[:cut], argv[cut + 1:]


def register(subparsers):
    parser = subparsers.add_parser('test-run', help='Run a test command through skillsync and record its events for the status page (a step of a plan)')
    parser.add_argument('path', help='project directory (use . for the current one)')
    parser.add_argument('--run', help='run id (a directory .skillsync/runs/<id>/ or a record <id>.json must exist); default: the only run of the project')
    parser.add_argument('--step', help='plan step id (default: the only step "w toku" in the checkpoints)')
    parser.add_argument('--junit', action='append', help='JUnit XML or TRX report file or directory written by the command (default places: dotnet, Maven, Gradle)')
    parser.add_argument('--allow-no-count', metavar='REASON', help='accept a passing command whose number of executed tests is unknown (the reason is recorded and shown)')
    parser.add_argument('--allow-skips', action='store_true', help='accept skipped tests (recorded)')
    parser.add_argument('--format', default='auto', metavar='NAME', help='output adapter: auto (by the tool), none, or a name (live state of single tests, stage 2)')
    parser.add_argument('--import', dest='import_file', metavar='FILE', help='store an events file written on another machine (needs --step and --source-sha)')
    parser.add_argument('--source-sha', help='with --import: the commit the imported run was made for')
    parser.add_argument('--timeout', type=float, default=3600, help='seconds (default 3600)')
    parser.add_argument('command', nargs='*', help='-- <command...> (after the options, separated by --)')
    parser.set_defaults(func=_command)
