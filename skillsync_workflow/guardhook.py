"""Hook PreToolUse (Bash) odmawiający trwałego usunięcia: konfiguracja, wpis w ustawieniach hosta i raport `doctor`.

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
SETTINGS = '.claude/settings.local.json'
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


def hook_command(python_command, mode, allow):
    # root from git: $CLAUDE_PROJECT_DIR is the start directory of the session, often a subdirectory; a missing script is exit 0
    args = ['--mode', mode]
    for item in allow:
        args += ['--allow', item]
    python = shlex.quote(python_command)
    return (f'f="$(git -C "${{CLAUDE_PROJECT_DIR:-.}}" rev-parse --show-toplevel 2>/dev/null)/{GUARD_HOOK}"; '
            f'if [ -f "$f" ]; then {python} "$f" {shlex.join(args)}; fi')


def hook_entry(python_command, mode, allow):
    return {'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': hook_command(python_command, mode, allow), 'timeout': 10}]}


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


def doctor_findings(root, manifest, skills):
    """`[(level, path, reason)]`: the state of the hook against what `link` would write (never an error: a warning at most)."""
    _, allow, problems = hook_settings(manifest)
    found = [('warning', '.github/.skillsync.yaml', reason) for reason in problems]
    claude = manifest.get('claude')
    if claude is False or (isinstance(claude, dict) and claude.get('enabled') is False):
        return found
    mode = effective_mode(manifest, skills)
    root = Path(root)
    script = root / GUARD_HOOK
    command = installed(root / SETTINGS)
    if command is False:
        return found + [('warning', SETTINGS, 'Unreadable settings: the delete guard hook state is unknown')]
    if mode == 'off':
        if command or script.is_file():
            found.append(('warning', SETTINGS, 'Delete guard hook is installed but switched off in the manifest; run `skillsync link` to remove it'))
        return found
    if not script.is_file() or command is None:
        found.append(('warning', SETTINGS, 'Delete guard hook (PreToolUse) is wanted but not installed; run `skillsync link`'))
        return found
    installed_mode, installed_allow = command_args(command)
    if installed_mode != mode or installed_allow != allow:
        found.append(('warning', SETTINGS, 'Delete guard hook differs from the manifest (mode or hooks.delete_allow); run `skillsync link`'))
    else:
        found.append(('info', SETTINGS, f'Delete guard hook active: mode {mode}, {len(allow)} extra allowed path(s)'))
    return found
