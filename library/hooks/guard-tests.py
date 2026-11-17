"""Classifier of the test commands for the guard hook (180-J2): is a command a WIDE run of the tests of a project outside `skillsync test-run`?

A standalone, stdlib-only module that `guard-commands.py` loads from its own directory (the hook script is copied into `.claude/hooks/` and cannot import
the package). It has no table of its own: the tools, the module words and the SYNTAX OF THE WRAPPERS (`npx`, `timeout`, `env`, ...) are the data of
`test-tools.json` (a copy of `skillsync_workflow/data/test_tools.json`, written next to the hook by `skillsync link`), the same file `test_run.detect_tool`
reads; the interpreter below mirrors `test_run._unwrap_once`/`_skip_flags` (a test of the core compares the two on a table of commands). The RULES (wide, point,
exempt, the example) depend on the sub-command and the flags and live here, as code.

Every function takes the words of ONE command (an argv list) and never raises: a table that cannot be read, a command that is not understood, anything
unexpected gives `None` / `'none'`, i.e. no warning. The warning never blocks (see `guard-commands.py`).
"""
import json
import os
import re

SCHEMA = 1
DEFAULT_WIDE_MODULES = 25
BUILTIN_WRAPPERS = (('skillsync', 'test-run'),)   # words that start an opaque wrapper (`validate`/`smart-test` count only with `--run`, see `is_wrapper`)
_WORD = re.compile(r'[A-Za-z_][A-Za-z0-9_]*=.*')


def load_table(path):
    """The table of `test-tools.json` as `{tools, modules, wrappers, layers}`, or `None` when the file is missing, unreadable or not of schema 1."""
    try:
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
        if data.get('schema') != SCHEMA:
            return None
        return {'tools': tuple((str(item['word']), str(item['tool'])) for item in data['tools']),
                'modules': {str(word): str(tool) for word, tool in data['module_words'].items()},
                'wrappers': {str(item['word']): item for item in data['wrappers']},
                'layers': int(data['max_layers'])}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def base(word):
    """The lower-case name of a command word without directories, quotes, `.exe|.cmd|.bat` and `@version` (as `test_run._base`)."""
    name = str(word).strip('\'"').replace('\\', '/').rsplit('/', 1)[-1].lower()
    name = re.sub(r'\.(exe|cmd|bat)$', '', name.strip('\'"'))
    return re.sub(r'(?<=.)@[^@/]*$', '', name)


def _skip_flags(words, boolean, valued, end_at_double_dash=True, stop=None, numeric=False):
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
            i += 1
            continue
        if name in valued:
            i += 1 if '=' in word else 2
        elif name in boolean and '=' not in word:
            i += 1
        else:
            return None
    return i


def _unwrap_once(argv, table):
    spec = table['wrappers'].get(base(argv[0]))
    if spec is None:
        return None
    rest = [str(w) for w in argv[1:]]
    boolean, valued = spec.get('boolean') or (), spec.get('valued') or ()
    if spec.get('subcommands'):
        if rest[:1] and rest[0] in spec['subcommands']:
            rest = rest[1:]
        elif spec.get('tool_words') and rest and base(rest[0]) in {word for word, _ in table['tools']}:
            return rest
        else:
            return None
    if spec.get('module_flag'):
        skip = _skip_flags(rest, boolean, valued, end_at_double_dash=False, stop=spec['module_flag'])
        if skip is None or rest[skip:skip + 1] != [spec['module_flag']] or skip + 1 >= len(rest):
            return None
        return rest[skip + 1:]
    skip = _skip_flags(rest, boolean, valued, numeric=bool(spec.get('numeric_flag')))
    if skip is None:
        return None
    rest = rest[skip:][int(spec.get('positional') or 0):]
    if spec.get('assignments'):
        while rest and _WORD.fullmatch(rest[0]):
            rest = rest[1:]
    return rest or None


def unwrap(words, table):
    """The words behind the wrappers of the table (as `test_run.unwrap_command`)."""
    argv = [str(w) for w in words]
    current = argv
    for _ in range(table['layers']):
        if not current:
            return argv
        inner = _unwrap_once(current, table)
        if inner is None:
            return current
        current = inner
    return argv if current and _unwrap_once(current, table) is not None else current


def tool_of(words, table):
    """The tool id of a command (as `test_run.detect_tool`), `'unknown'` when none."""
    argv = [str(w) for w in words]
    names = [base(word) for word in argv]
    for word, tool in table['modules'].items():
        if word in names:
            return tool
    core = unwrap(argv, table)
    for word in ([base(core[0])] if core else []) + names[:3]:
        for name, tool in table['tools']:
            if word == name:
                return tool
    return 'unknown'


# ---- the rules of the tools -------------------------------------------------------------------------------------------------------------------------

VALUED = {
    'pytest': {'-k', '-m', '-p', '-c', '-o', '-W', '-n', '-r', '--rootdir', '--ignore', '--ignore-glob', '--deselect', '--maxfail', '--tb', '--junitxml',
               '--log-level', '--basetemp', '--durations', '--import-mode', '--timeout', '--color', '--confcutdir'},
    'unittest': {'-s', '-t', '-p', '-k', '--start-directory', '--top-level-directory', '--pattern'},
    'dotnet': {'--filter', '-c', '--configuration', '-f', '--framework', '-r', '--runtime', '--logger', '-l', '--results-directory', '-o', '--output', '--settings', '-s',
               '--blame-hang-timeout', '--diag', '-v', '--verbosity'},
    'jest': {'-t', '--testNamePattern', '--config', '-c', '--maxWorkers', '-w', '--testPathPattern', '--findRelatedTests', '--reporters', '--shard'},
    'vitest': {'-t', '--testNamePattern', '--config', '-c', '--reporter', '--shard', '--root', '-r', '--project', '--outputFile'},
    'angular': {'--include', '--browsers', '--karma-config', '--watch'},
    'cargo': {'--message-format', '-p', '--package', '--test', '--bin', '--example', '--features', '-F', '--target', '--manifest-path', '-j', '--jobs', '--profile', '-Z', '--color', '--exclude'},
    'playwright': {'-g', '--grep', '--grep-invert', '--project', '--reporter', '--config', '-c', '--workers', '-j', '--retries', '--shard', '--timeout'},
    'cypress': {'--spec', '-s', '--browser', '-b', '--config', '-c', '--env', '-e', '--reporter', '-r', '--project', '-P', '--record', '--key', '--group', '--tag'},
    'go': {'-coverpkg', '-run', '-bench', '-count', '-timeout', '-p', '-parallel', '-tags', '-cpu', '-v', '-coverprofile', '-covermode', '-skip', '-shuffle'},
}
GO_BOOLEAN_AFTER = {'-v', '-race', '-short', '-json', '-cover', '-failfast', '-x', '-n'}


def _positionals(args, valued):
    """The words of `args` that are neither flags nor the values of the flags in `valued`; stops at `--` (what follows is for the tool under test)."""
    out, i = [], 0
    while i < len(args):
        word = args[i]
        if word == '--':
            break
        if word.startswith('-'):
            name = word.split('=', 1)[0]
            i += 1 if '=' in word or name not in valued else 2
            continue
        out.append(word)
        i += 1
    return out


def _has(args, names):
    return any(word.split('=', 1)[0] in names or any(word.startswith(n + '=') for n in names) for word in args if word.startswith('-'))


def _unittest(words, wide_modules, table):
    names = [base(w) for w in words]
    index = names.index('unittest') if 'unittest' in names else -1
    if index < 0:
        return None
    args = [str(w) for w in words[index + 1:]]
    if _has(args, {'-h', '--help'}):
        return 'exempt', None
    if _has(args, {'-k'}):
        return 'point', None
    if args[:1] == ['discover']:
        return 'wide', None
    modules = _positionals(args, VALUED['unittest'])
    if not modules or len(modules) >= max(int(wide_modules), 2):
        return 'wide', None
    return 'point', None


def _pytest(words, wide_modules, table):
    names = [base(w) for w in words]
    key = 'pytest' if 'pytest' in names else 'py.test' if 'py.test' in names else None
    if key is None:
        return None
    args = [str(w) for w in words[names.index(key) + 1:]]
    if _has(args, {'--collect-only', '--co', '--help', '-h', '--version', '--fixtures', '--markers'}):
        return 'exempt', None
    if _has(args, {'-k'}):
        return 'point', None
    targets = _positionals(args, VALUED['pytest'])
    if any('::' in t or t.endswith('.py') for t in targets):
        return 'point', None
    return 'wide', None


def _dotnet(words, wide_modules, table):
    names = [base(w) for w in words]
    if 'dotnet' not in names:
        return None
    args = [str(w) for w in words[names.index('dotnet') + 1:]]
    sub = next((w for w in args if not w.startswith('-')), None)
    if sub not in ('test', 'vstest'):
        return None
    rest = args[args.index(sub) + 1:]
    if _has(rest, {'--list-tests', '-t', '--no-run'}):
        return 'exempt', None
    if _has(rest, {'--filter'}):
        return 'point', None
    return 'wide', None


def _angular(words, wide_modules, table):
    names = [base(w) for w in words]
    if 'ng' not in names:
        return None
    args = [str(w) for w in words[names.index('ng') + 1:]]
    if args[:1] != ['test']:
        return None
    return ('point', None) if _has(args[1:], {'--include'}) else ('wide', None)


def _runner(tool):
    """`jest` and `vitest`: a file or a name pattern is a point run; no argument at all is wide; `--listTests`/`--list` list only."""
    def rule(words, wide_modules, table):
        names = [base(w) for w in words]
        if tool not in names:
            return None
        args = [str(w) for w in words[names.index(tool) + 1:]]
        if tool == 'vitest' and args[:1] in (['run'], ['watch']):
            args = args[1:]
        if _has(args, {'--listTests', '--list', '--help', '-h', '--version'}):
            return 'exempt', None
        if _has(args, {'-t', '--testNamePattern', '--testPathPattern', '--findRelatedTests', '--onlyChanged', '-o'}):
            return 'point', None
        return ('point', None) if _positionals(args, VALUED[tool]) else ('wide', None)
    return rule


def _cargo(words, wide_modules, table):
    names = [base(w) for w in words]
    if 'cargo' not in names:
        return None
    args = [str(w) for w in words[names.index('cargo') + 1:]]
    args = [a for a in args if not a.startswith('+')]   # a toolchain: `cargo +nightly test`
    if args[:1] == ['test'] or args[:1] == ['t']:
        rest = args[1:]
        if '--' in rest and '--list' in rest[rest.index('--') + 1:]:
            return 'exempt', None
        if _has(rest, {'--no-run'}):
            return 'exempt', None
        if _has(rest, {'-p', '--package', '--test', '--lib', '--bin', '--example', '--doc'}) or _positionals(rest, VALUED['cargo']):
            return 'point', None
        return 'wide', None
    if args[:2] in (['nextest', 'run'], ['nextest', 'r']):
        rest = args[2:]
        return ('point', None) if _has(rest, {'-p', '--package'}) or _positionals(rest, VALUED['cargo']) else ('wide', None)
    if args[:2] == ['nextest', 'list']:
        return 'exempt', None
    return None


def _playwright(words, wide_modules, table):
    names = [base(w) for w in words]
    if 'playwright' not in names:
        return None
    args = [str(w) for w in words[names.index('playwright') + 1:]]
    if args[:1] != ['test']:
        return None
    rest = args[1:]
    if _has(rest, {'--list'}):
        return 'exempt', None
    if _has(rest, {'-g', '--grep', '--project', '--last-failed', '--only-changed'}) or _positionals(rest, VALUED['playwright']):
        return 'point', None
    return 'wide', None


def _cypress(words, wide_modules, table):
    names = [base(w) for w in words]
    if 'cypress' not in names:
        return None
    args = [str(w) for w in words[names.index('cypress') + 1:]]
    if args[:1] != ['run']:
        return None
    return ('point', None) if _has(args[1:], {'--spec', '-s'}) else ('wide', None)


def _go(words, wide_modules, table):
    names = [base(w) for w in words]
    if 'go' not in names:
        return None
    args = [str(w) for w in words[names.index('go') + 1:]]
    if args[:1] != ['test']:
        return None
    rest = args[1:]
    if _has(rest, {'-run', '-skip', '-list'}):
        return 'point', None
    packages = _positionals_go(rest)
    return ('wide', None) if any(p.endswith('...') for p in packages) else ('point', None)


def _positionals_go(args):
    out, i = [], 0
    while i < len(args):
        word = args[i]
        if word == '--':
            break
        if word.startswith('-'):
            name = word.split('=', 1)[0]
            i += 1 if '=' in word or name in GO_BOOLEAN_AFTER or name not in VALUED['go'] else 2
            continue
        out.append(word)
        i += 1
    return out


def _npm(words, wide_modules, table):
    """`npm|pnpm|yarn test`, `npm run test`: probably a wide run (the hook does not read package.json)."""
    names = [base(w) for w in words]
    if not names or names[0] not in ('npm', 'pnpm', 'yarn', 'npx'):
        return None
    raw = [str(w) for w in words[1:]]
    valued = {'--prefix', '--workspace', '-w', '--cwd', '--dir', '-C', '--filter', '--registry'}
    args, i = [], 0
    while i < len(raw):
        if raw[i] == '--':
            break
        if raw[i].startswith('-'):
            i += 1 if '=' in raw[i] or raw[i] not in valued else 2
            continue
        args.append(raw[i])
        i += 1
    tail = raw[i + 1:] if i < len(raw) else []   # what follows `--` goes to the script: a file or a pattern there narrows the run
    if args[:1] == ['test'] or args[:2] == ['run', 'test'] or args[:1] == ['t']:
        return ('point', None) if tail else ('wide', 'probably')
    return None


def _make(words, wide_modules, table):
    names = [base(w) for w in words]
    if not names or names[0] != 'make':
        return None
    valued, targets, i = {'-C', '--directory', '-j', '--jobs', '-f', '--file', '--makefile', '-I', '-o', '-W', '-l', '--max-load'}, [], 1
    while i < len(words):
        word = words[i]
        if word.startswith('-'):
            i += 1 if '=' in word or word not in valued else 2
            continue
        if '=' not in word:
            targets.append(word)
        i += 1
    return ('wide', 'probably') if targets and targets[0] in ('test', 'tests', 'check') else None


RULES = {'unittest': _unittest, 'pytest': _pytest, 'dotnet': _dotnet, 'angular': _angular, 'jest': _runner('jest'), 'vitest': _runner('vitest'), 'cargo': _cargo,
         'playwright': _playwright, 'cypress': _cypress, 'go': _go, 'npm': _npm, 'npx': _npm}
EXAMPLES = {
    'unittest': 'python3 -m unittest tests.test_a tests.test_b',
    'pytest': 'python3 -m pytest tests/test_a.py::TestX or -k <expression>',
    'dotnet': 'dotnet test --filter <expression>',
    'angular': 'ng test --include <file>',
    'jest': 'npx jest <file> or -t <name>',
    'vitest': 'npx vitest run <file>',
    'cargo': 'cargo test <name> or -p <crate>',
    'playwright': 'npx playwright test <file> or -g <pattern>',
    'cypress': 'npx cypress run --spec <file>',
    'go': 'go test -run <pattern> ./<package>',
    'npm': 'a single test file through the script of the project',
    'make': 'a single target that runs one test file',
}


def is_wrapper(words, extra=()):
    """`True` for a command that already runs through a wrapper: `skillsync test-run|validate|smart-test`, `flock ... systemd-run ...`, or a prefix of
    `hooks.test_wrappers` (each a list of words)."""
    names = [base(w) for w in words]
    for prefix in list(BUILTIN_WRAPPERS) + [tuple(base(w) for w in p) for p in extra]:   # the same normalisation as the words of the command
        if tuple(names[:len(prefix)]) == tuple(prefix):
            return True
    if names[:1] == ['skillsync'] and names[1:2] in (['validate'], ['smart-test']) and any(w == '--run' or w.startswith('--run=') for w in words):
        return True   # the shared runner records the run only with `--run`
    if names[:1] == ['systemd-run']:
        return True   # the station wrapper with a memory limit
    return names[:1] == ['flock'] and 'systemd-run' in names   # `flock <lock> systemd-run ...`: the lock AND the limit; a bare flock is not the wrapper


def program_tool(core, table):
    """The tool of the PROGRAM that a command runs: its first word after the wrappers (`pytest`, `dotnet`, `ng`, or the module behind `python -m`), not any word
    of the line (`echo pytest` and `grep unittest docs` run no test). `None` when the program is not a tool of the table."""
    if not core:
        return None
    first = base(core[0])
    if first in table['modules']:
        return table['modules'][first]
    for name, tool in table['tools']:
        if first == name:
            return tool
    return None


def classify(words, table, wide_modules=DEFAULT_WIDE_MODULES, extra_wrappers=()):
    """`{'verdict': 'wide'|'point'|'exempt'|'wrapper'|'none', 'tool': id, 'probably': bool}` for ONE command. `wide` is the only verdict that warns."""
    try:
        words = [str(w) for w in words]
        if not words:
            return {'verdict': 'none', 'tool': None, 'probably': False}
        if is_wrapper(words, extra_wrappers):
            return {'verdict': 'wrapper', 'tool': None, 'probably': False}
        if not table:
            return {'verdict': 'none', 'tool': None, 'probably': False}
        core = unwrap(words, table)
        tool = program_tool(core, table)
        found = RULES[tool](core, wide_modules, table) if tool in RULES else None
        if found is None and core:
            first = base(core[0])
            if first == 'make':
                found, tool = _make(core, wide_modules, table), 'make'
            elif first in ('pnpm', 'yarn'):
                found, tool = _npm(core, wide_modules, table), 'npm'
        if found is None:
            return {'verdict': 'none', 'tool': tool, 'probably': False}
        return {'verdict': found[0], 'tool': tool, 'probably': found[1] == 'probably'}
    except Exception:   # noqa: BLE001 - a classifier never takes the hook down
        return {'verdict': 'none', 'tool': None, 'probably': False}


def warning_text(command, verdict, root='<project>'):
    """The text of the warning for a wide run: plain sentences of facts (it reaches the model AFTER the command ran, next to its result)."""
    tool = verdict.get('tool')
    example = EXAMPLES.get(tool, 'a single test file or a name filter')
    maybe = ' This looks like a wide run (the hook does not read the scripts of the project).' if verdict.get('probably') else ''
    return (f'This command ran a wide test run outside `skillsync test-run`: `{command}`.{maybe} The run is not recorded in the plan run and is not evidence of a test step. '
            f'Evidence comes from `skillsync test-run {root} -- <the same command>` (add `--run <id> --step <id>` when several steps are in progress). '
            f'A narrow run needs no wrapper, for example `{example}`. A one-time exception, after the user agrees: `SKILLSYNC_GUARD_EXCEPT=\'<reason>\'` before the command.')
