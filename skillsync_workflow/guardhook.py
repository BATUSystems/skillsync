"""Hook PreToolUse (Bash) odmawiający trwałego usunięcia: konfiguracja, wpis w ustawieniach hosta i raport `doctor`.

Test commands (180-J2): the same hook also WARNS (never blocks) about a wide test run outside `skillsync test-run`: `hooks.guard_tests: warn | off`
(default `warn` in agent-flow projects, `off` elsewhere), `hooks.test_wrappers` (command prefixes that count as a wrapper) and `hooks.wide_modules`. The classifier is
`library/hooks/guard-tests.py` and the table of the tools is `test-tools.json`, both copied next to the hook. `TESTS_HOOK_READY` keeps the whole part dormant until
`guard-commands.py` understands the arguments (it is on since 180-J2).

Skrypt hooka to `library/hooks/guard-commands.py` (kopiowany do `.claude/hooks/`). Wpis trafia do `.claude/settings.local.json`
obok hooka zamknięcia; generator zarządza wyłącznie własnym wpisem. Tryb: `hooks.guard_delete: deny | off`; brak klucza = `deny`
w projektach z przepływem agentowym (skill `feature` albo `dispatch`) i `off` w pozostałych. `hooks.delete_allow`: dodatkowe nazwy
katalogów odtwarzalnych albo ścieżki (bezwzględne = jak katalogi tymczasowe, względne = od korzenia projektu).
"""
import json
import re
import shlex
from pathlib import Path

GUARD_HOOK = '.claude/hooks/guard-commands.py'
TESTS_HOOK = '.claude/hooks/guard-tests.py'
TOOLS_HOOK = '.claude/hooks/test-tools.json'
ATTRIBUTION_HOOK = '.claude/hooks/attribution-patterns.json'   # copy of data/attribution-patterns.json
TEST_MODES = ('warn', 'off')
DEFAULT_WIDE_MODULES = 25
TESTS_HOOK_READY = True   # guard-commands.py reads --tests (180-J2); False would keep the whole test part dormant (nothing written, no argument)
SETTINGS = '.claude/settings.local.json'
SHARED_SETTINGS = '.claude/settings.json'
# Host setting that switches off the attribution of Claude Code: the object form with empty `commit` and `pr` (the single value `false` needs a newer
# host and is not used; `sessionUrl` concerns cloud sessions only and is left alone). Written by `link`, always the same value.
ATTRIBUTION_SETTING = {'commit': '', 'pr': ''}
FLOW_SKILLS = ('feature', 'agentflow', 'dispatch')
MODES = ('deny', 'off')


def hook_settings(manifest):
    """`(mode or None when invalid, allow list, problems)` read from the manifest `hooks:` block."""
    hooks = manifest.get('hooks') if isinstance(manifest.get('hooks'), dict) else {}
    problems = []
    mode = hooks.get('guard_delete')
    if mode is False:
        mode = 'off'
    elif mode is True:
        mode = 'deny'
    if mode is not None and mode not in MODES:
        problems.append(f'hooks.guard_delete must be one of {", ".join(MODES)}: {mode!r}')
        mode = None
    allow = hooks.get('delete_allow', [])
    if not isinstance(allow, list) or any(not isinstance(item, str) or not item.strip() or '\0' in item for item in allow):
        problems.append('hooks.delete_allow must be a list of nonempty strings')
        allow = []
    return mode, [item.strip() for item in allow], problems


def test_settings(manifest):
    """`(mode or None, wrappers, wide_modules, problems)` of the test part of the `hooks:` block: `guard_tests` (`warn`|`off`, a bool too), `test_wrappers` (a list of
    nonempty command prefixes) and `wide_modules` (an integer of at least 2)."""
    hooks = manifest.get('hooks') if isinstance(manifest.get('hooks'), dict) else {}
    problems = []
    mode = hooks.get('guard_tests')
    if mode is False:
        mode = 'off'
    elif mode is True:
        mode = 'warn'
    if mode is not None and mode not in TEST_MODES:
        problems.append(f'hooks.guard_tests must be one of {", ".join(TEST_MODES)}: {mode!r}')
        mode = None
    wrappers = hooks.get('test_wrappers', [])
    if not isinstance(wrappers, list) or any(not isinstance(item, str) or not item.strip() or '\0' in item for item in wrappers):
        problems.append('hooks.test_wrappers must be a list of nonempty strings (command prefixes)')
        wrappers = []
    else:
        for item in wrappers:
            try:
                shlex.split(item)
            except ValueError:
                problems.append(f'hooks.test_wrappers: not a valid command prefix (an open quote?): {item!r}')
                wrappers = [w for w in wrappers if w != item]
    wide = hooks.get('wide_modules', DEFAULT_WIDE_MODULES)
    if isinstance(wide, bool) or not isinstance(wide, int) or wide < 2:
        problems.append('hooks.wide_modules must be an integer of at least 2')
        wide = DEFAULT_WIDE_MODULES
    return mode, [item.strip() for item in wrappers], wide, problems


def effective_test_mode(manifest, skills):
    """`warn` or `off`: an explicit value wins; otherwise `warn` only for agent-flow projects. An unusable value never switches the hook on."""
    mode, _, _, problems = test_settings(manifest)
    if problems and mode is None and 'guard_tests' in (manifest.get('hooks') or {}):
        return 'off'
    if mode is not None:
        return mode
    return 'warn' if agent_flow(skills) else 'off'


def tests_active(manifest, skills):
    """The test part is written into the hook only when the script can read it (`TESTS_HOOK_READY`) and the mode is `warn`."""
    return TESTS_HOOK_READY and effective_test_mode(manifest, skills) == 'warn'


def agent_flow(skills):
    return any(name in skills for name in FLOW_SKILLS)


def effective_mode(manifest, skills):
    """`deny` or `off`: explicit value wins; otherwise `deny` only for agent-flow projects."""
    mode, _, problems = hook_settings(manifest)
    if problems and mode is None and 'guard_delete' in (manifest.get('hooks') or {}):
        return 'off'  # an unusable value never switches the hook on
    if mode is not None:
        return mode
    return 'deny' if agent_flow(skills) else 'off'


def hook_command(python_command, mode, allow, tests=None):
    # root from git: $CLAUDE_PROJECT_DIR is the start directory of the session, often a subdirectory; a missing script is exit 0
    args = ['--mode', mode, '--attribution', 'on']   # the ban on AI attribution is always on (no manifest switch)
    for item in allow:
        args += ['--allow', item]
    if tests:   # `{'wide': int, 'wrappers': [str]}`: the test part of the same hook (warns, never blocks)
        args += ['--tests', 'warn', '--wide-modules', str(tests['wide'])]
        for item in tests.get('wrappers', []):
            args += ['--wrapper', item]
    python = shlex.quote(python_command)
    return (f'f="$(git -C "${{CLAUDE_PROJECT_DIR:-.}}" rev-parse --show-toplevel 2>/dev/null)/{GUARD_HOOK}"; '
            f'if [ -f "$f" ]; then {python} "$f" {shlex.join(args)}; fi')


def hook_entry(python_command, mode, allow, tests=None):
    return {'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': hook_command(python_command, mode, allow, tests), 'timeout': 10}]}


def is_guard_hook(hook):
    return isinstance(hook, dict) and hook.get('type') == 'command' and GUARD_HOOK in str(hook.get('command', ''))


def installed(settings_path):
    """The guard hook command found in a settings file, `None` when absent, `False` when the file is unusable."""
    try:
        data = json.loads(Path(settings_path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return False
    pre = (data.get('hooks') or {}).get('PreToolUse') if isinstance(data, dict) and isinstance(data.get('hooks'), dict) else None
    if not isinstance(pre, list):
        return None
    for group in pre:
        for hook in group.get('hooks', []) if isinstance(group, dict) and isinstance(group.get('hooks'), list) else []:
            if is_guard_hook(hook):
                return str(hook.get('command', ''))
    return None


def command_args(command):
    """`(mode, allow)` read back from an installed command string (`... "$f" <args>; fi`)."""
    match = re.search(r'"\$f" (.*); fi\s*$', command)
    try:
        args = shlex.split(match.group(1)) if match else []
    except ValueError:
        args = []
    mode, allow = None, []
    for index, item in enumerate(args):
        if item == '--mode' and index + 1 < len(args):
            mode = args[index + 1]
        elif item == '--allow' and index + 1 < len(args):
            allow.append(args[index + 1])
    return mode, allow


def command_tests(command):
    """`{'wide': int, 'wrappers': [...]}` of the test part read back from an installed command, `None` when the command has none."""
    match = re.search(r'"\$f" (.*); fi\s*$', command)
    try:
        args = shlex.split(match.group(1)) if match else []
    except ValueError:
        args = []
    if '--tests' not in args:
        return None
    wide, wrappers = DEFAULT_WIDE_MODULES, []
    for index, item in enumerate(args):
        if item == '--wide-modules' and index + 1 < len(args) and args[index + 1].isdigit():
            wide = int(args[index + 1])
        elif item == '--wrapper' and index + 1 < len(args):
            wrappers.append(args[index + 1])
    return {'wide': wide, 'wrappers': wrappers}


def tests_config(manifest, skills):
    """The `tests` argument of `hook_entry`: `None` when the test part is not active."""
    if not tests_active(manifest, skills):
        return None
    _, wrappers, wide, _ = test_settings(manifest)
    return {'wide': wide, 'wrappers': wrappers}


def _read_settings(path):
    """`(data or None when absent, readable)` of a settings file."""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None, True
    except (OSError, ValueError):
        return None, False
    return (data if isinstance(data, dict) else None), isinstance(data, dict)


def attribution_state(value):
    """`True` when the value of a settings `attribution` key hides all attribution (commit and pr empty, or `false`), else `False`."""
    if value is False:
        return True
    return isinstance(value, dict) and value.get('commit') == '' and value.get('pr') == ''


LINK_TRACES = ('.claude/hooks', '.claude/settings.local.json')


def never_linked(root):
    """True when the project shows no trace of `skillsync link` for Claude Code: a missing hook is then only a hint, not a FAIL."""
    return not any((Path(root) / rel).exists() for rel in LINK_TRACES)


def attribution_findings(root, home=None):
    """`[(level, path, reason)]`: the host setting that switches the attribution off. The project-local file (the highest file the generator writes)
    must carry it (`error`); a shared or user file with attribution on is a `warning` (the local file wins over them, a clone without `link` does not)."""
    root = Path(root)
    found = []
    data, readable = _read_settings(root / SETTINGS)
    if not readable:
        return [('error', SETTINGS, 'Unreadable settings: the host setting that switches AI attribution off cannot be checked')]
    if data is None or not attribution_state(data.get('attribution')):
        found.append(('warning' if never_linked(root) else 'error', SETTINGS, 'The host setting `attribution` (empty commit and pr) is missing or switched on: Claude Code would add its footer and trailer; run `skillsync link`'))
    shared, ok = _read_settings(root / SHARED_SETTINGS)
    if ok and shared is not None:
        value = shared.get('attribution')
        if (value is not None and not attribution_state(value)) or shared.get('includeCoAuthoredBy') is True:
            found.append(('warning', SHARED_SETTINGS, 'Shared project settings switch AI attribution on; the local settings written by `link` win over them'))
    user, ok = _read_settings(Path(home or Path.home()) / '.claude' / 'settings.json')
    if ok and user is not None:
        value = user.get('attribution')
        if (value is not None and not attribution_state(value)) or user.get('includeCoAuthoredBy') is True:
            found.append(('warning', '~/.claude/settings.json', 'User settings switch AI attribution on; project settings win, but other projects are not covered: run `skillsync attribution --user`'))
    return found


INSTRUCTION_FILES = ('AGENTS.md', 'CLAUDE.md', '.github/copilot-instructions.md', '.codex/skillsync-instructions.md')


def instruction_text_findings(root):
    """`error` for a generated instruction file of the project that contains text of the banned AI attribution (a layer or the project
    instructions tell the agent to add it): the file, the line and the pattern id, never the text."""
    from . import attribution
    found = []
    try:
        rules = attribution.enforced()
    except attribution.AttributionError:
        return found
    for rel in INSTRUCTION_FILES:
        path = Path(root) / rel
        try:
            text = path.read_text(encoding='utf-8', errors='replace') if path.is_file() else ''
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for rule in rules:
                if rule['compiled'].search(line):
                    found.append(('error', rel, f'Line {number} contains text of the banned AI attribution (pattern {rule["id"]}): a layer or the project instructions '
                                                'tell the agent to add a footer or signature of an AI tool; remove it at the source and run `skillsync link`'))
                    break   # one finding per line
    return found


def doctor_findings(root, manifest, skills):
    """`[(level, path, reason)]`: the state of the hook against what `link` would write. A missing hook or attribution data is an `error` (the ban on AI
    attribution is not optional); drift of the delete or test settings stays a warning."""
    _, allow, problems = hook_settings(manifest)
    problems = problems + test_settings(manifest)[3]
    found = [('warning', '.github/.skillsync.yaml', reason) for reason in problems]
    found.extend(instruction_text_findings(root))
    claude = manifest.get('claude')
    if claude is False or (isinstance(claude, dict) and claude.get('enabled') is False):
        return found
    mode = effective_mode(manifest, skills)
    tests = tests_config(manifest, skills)
    root = Path(root)
    found.extend(attribution_findings(root))
    script = root / GUARD_HOOK
    command = installed(root / SETTINGS)
    if command is False:
        return found + [('warning', SETTINGS, 'Unreadable settings: the delete guard hook state is unknown')]
    if not script.is_file() or command is None:
        found.append(('warning' if never_linked(root) else 'error', SETTINGS, 'The guard hook (PreToolUse: ban on AI attribution in commit and PR text, delete guard) is not installed; run `skillsync link`'))
        return found
    copy = root / ATTRIBUTION_HOOK
    if not copy.is_file():
        found.append(('error', ATTRIBUTION_HOOK, 'The attribution patterns of the guard hook are missing: the ban stays silent; run `skillsync link`'))
        return found
    from . import attribution
    try:
        attribution.load(copy)
    except attribution.AttributionError:
        found.append(('error', ATTRIBUTION_HOOK, 'The attribution patterns of the guard hook are unreadable or invalid: the hook ignores them and the ban stays silent; run `skillsync link`'))
        return found
    try:
        if copy.read_bytes() != attribution.DATA.read_bytes():
            have = {rule['id'] for rule in attribution.enforced(copy)}
            weaker = {rule['id'] for rule in attribution.enforced()} - have   # patterns in force at the generator that the copy lacks
            found.append(('error' if weaker else 'warning', ATTRIBUTION_HOOK, 'The attribution patterns of the guard hook ' + (
                'miss patterns that are in force (weaker ban)' if weaker else 'differ from the generator data (outdated copy)') + '; run `skillsync link`'))
    except OSError:
        pass
    if '--attribution' not in command:
        found.append(('error', SETTINGS, 'The installed guard hook entry predates the ban on AI attribution; run `skillsync link`'))
        return found
    installed_mode, installed_allow = command_args(command)
    if installed_mode != mode or installed_allow != allow or command_tests(command) != tests:
        found.append(('warning', SETTINGS, 'Guard hook differs from the manifest (mode, hooks.delete_allow or the hooks.guard_tests settings); run `skillsync link`'))
        return found
    if tests is not None and not ((root / TESTS_HOOK).is_file() and (root / TOOLS_HOOK).is_file()):
        found.append(('warning', TOOLS_HOOK, 'The test commands part of the guard hook is on but its files (guard-tests.py, test-tools.json) are missing: the part stays silent; run `skillsync link`'))
        return found
    parts = f'attribution ban on, delete guard mode {mode}, {len(allow)} extra allowed path(s)' + (f'; test commands: warn (wide run from {tests["wide"]} modules, {len(tests["wrappers"])} extra wrapper(s))' if tests else '')
    found.append(('info', SETTINGS, f'Guard hook active: {parts}'))
    return found
