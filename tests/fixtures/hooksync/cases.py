"""Case matrix of the golden trees of the hook settings merge (plan CLAUDE-3, step F1; spec K2).

One module serves two callers: `generate_golden.py` (writes the patterns with the code that exists TODAY, before the shared merge
`skillsync_workflow/hooksync.py` appears) and `tests/test_skillsync_hooksync.py` (rebuilds the same project and compares). The patterns are
JSON files in `golden/` (no raw CR byte inside: `.gitattributes` has `* text=auto eol=lf`; CRLF of a settings file is written as `\\r\\n`
inside a JSON string and the CRLF input of a case is built here, from bytes, at run time).

What a pattern holds, per case (`golden/<case>.json`):
  settings_local     text of `.claude/settings.local.json` after the last `link` (null: no such file)
  settings_backup    text of `.claude/settings.local.json.skillsync-backup` (null: none)
  hook_files         names in `.claude/hooks` (sorted); each hook file must equal its source in the repository (`hook_sources`)
  hook_sha256        informational only (the hash of the source at generation time; not asserted, the sources legitimately change later)
  claude_gitignore   text of `.claude/.gitignore` (null: none)
  root_gitignore_skillsync   whether the root `.gitignore` lists `.skillsync/`
  top_level_keys     order of the top-level keys of settings.local.json
  event_order        order of the events under `hooks`
  stdout             normalised lines of the last `link` (see `normalise`)
  second_*           the same after a SECOND identical `link` (idempotence; absent for dry-run cases)
Text fields are the UTF-8 decoding of the file bytes (a lossless form of the bytes: CR and LF stay visible as `\\r`, `\\n`).
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import sys as _sys_tmp_paths
from pathlib import Path
from pathlib import Path as _Path_tmp_paths

REPO = _Path_tmp_paths(__file__).resolve().parents[3]
TESTS = REPO / 'tests'
_sys_tmp_paths.path.insert(0, str(TESTS))
from tmp_paths import child, descend  # noqa: E402

import yaml  # noqa: E402

CLI = REPO / 'skillsync'
LIBRARY = REPO / 'library'
GOLDEN = Path(__file__).resolve().parent / 'golden'
LOCAL = '.claude/settings.local.json'
BACKUP = '.claude/settings.local.json.skillsync-backup'
CLAUDE_GITIGNORE = '.claude/.gitignore'
HOOKS_DIR = '.claude/hooks'
PLACEHOLDER = '<PROJECT>'

# name in `.claude/hooks` -> its source in the repository (the copies must be byte-equal to them)
HOOK_SOURCES = {
    'check-closure.py': 'library/hooks/check-closure.py',
    'guard-commands.py': 'library/hooks/guard-commands.py',
    'guard-tests.py': 'library/hooks/guard-tests.py',
    'attribution-patterns.json': 'skillsync_workflow/data/attribution-patterns.json',
    'test-tools.json': 'skillsync_workflow/data/test_tools.json',
}

# Keys of the new hook families (spec K7), set off in every golden case: with them off `link` has to give the bytes of the old code (K2).
NEW_HOOK_KEYS_OFF = {'session_context': False, 'guard_generated': 'off', 'subagent_report': 'off', 'subagent_commit': 'off'}


def json_bytes(data, eol='\n'):
    return (json.dumps(data, indent=2, ensure_ascii=False) + '\n').replace('\n', eol).encode('utf-8')


FOREIGN_STOP = {'matcher': '', 'hooks': [{'type': 'command', 'command': 'echo obcy-stop-ż'}]}
FOREIGN_PRE = {'matcher': 'Write', 'hooks': [{'type': 'command', 'command': 'echo obcy-pre'}]}
FOREIGN_SETTINGS = {
    'permissions': {'allow': ['Bash(ls:*)'], 'deny': ['Read(./.env)']},
    'env': {'ZMIENNA': 'wartość-ą'},
    'hooks': {'Stop': [FOREIGN_STOP], 'PreToolUse': [FOREIGN_PRE],
              'Notification': [{'matcher': '', 'hooks': [{'type': 'command', 'command': 'echo obcy-notify'}]}]},
}
# Our entries of an older generation in a group shared with a foreign hook (the merge replaces ours and keeps the foreign one in place).
SHARED_GROUP_SETTINGS = {
    'hooks': {
        'Stop': [{'matcher': '', 'hooks': [{'type': 'command', 'command': 'echo obcy-w-grupie'},
                                           {'type': 'command', 'command': 'python3 .claude/hooks/check-closure.py --stary-argument', 'timeout': 99}]}],
        'PreToolUse': [{'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': 'python3 .claude/hooks/guard-commands.py --mode stary'},
                                                      {'type': 'command', 'command': 'echo obcy-bash'}]}],
    },
}
ATTRIBUTION_ON_SETTINGS = {'attribution': {'commit': 'Co-Authored-By: Narzędzie', 'pr': 'wygenerowano'}, 'permissions': {'allow': ['Bash(git status)']}}
PRE_EXISTING_BACKUP = '{"stara-kopia": true}\r\n'.encode('utf-8')   # CRLF on purpose: the backup bytes are compared exactly

FEATURE = {'skills': ['feature']}
PLAIN = {'skills': []}


def case(manifest, local=None, backup=None, tracked=False, runs=None, dry_run=False):
    """`manifest`: manifest keys on top of the common ones; `local`/`backup`: bytes written before the first `link`; `tracked`: the settings file is
    added to the git index; `runs`: manifest patches applied one by one with a `link` after each (the last one is captured); `dry_run`: the captured
    run is `link --dry-run`."""
    return {'manifest': manifest, 'local': local, 'backup': backup, 'tracked': tracked, 'runs': runs or [{}], 'dry_run': dry_run}


CASES = {
    # only `feature`: closure (Stop) and the guard (PreToolUse + attribution), default modes
    'feature_only': case(FEATURE),
    # plain project: only the guard of commands (always wanted: the ban on AI attribution), no closure
    'plain_guard_only': case(PLAIN),
    # `hooks.guard_tests: warn` in a plain project: the guard entry gets the test part and two more files
    'guard_tests_warn': case({**PLAIN, 'hooks': {'guard_tests': 'warn'}}),
    # foreign entries, permissions and other keys of settings.local.json stay (a feature project)
    'foreign_entries': case(FEATURE, local=json_bytes(FOREIGN_SETTINGS)),
    # our older entries share groups with foreign hooks: ours are replaced, the foreign ones stay
    'shared_group_stale': case(FEATURE, local=json_bytes(SHARED_GROUP_SETTINGS)),
    # `attribution` switched on by the user: the guard writes the empty form and warns
    'attribution_on': case(PLAIN, local=json_bytes(ATTRIBUTION_ON_SETTINGS)),
    # CRLF settings file: the line ending is kept (the bytes of CR are part of the pattern)
    'crlf_settings': case(FEATURE, local=json_bytes(FOREIGN_SETTINGS, eol='\r\n')),
    # settings.local.json tracked by git: `link` skips the write with a warning (the bytes stay)
    'tracked_settings': case(FEATURE, local=json_bytes(FOREIGN_SETTINGS), tracked=True),
    # invalid JSON: a warning, no exception, the file stays untouched
    'invalid_json': case(FEATURE, local=b'{ to nie jest json\r\n'),
    # a backup already exists, two writers (closure first, then the guard): the closure OVERWRITES the backup, the guard keeps the EARLIEST one
    'backup_existing_feature': case(FEATURE, local=json_bytes(FOREIGN_SETTINGS), backup=PRE_EXISTING_BACKUP),
    # a backup already exists, the guard alone (a plain project): the earliest backup stays byte for byte
    'backup_existing_plain': case(PLAIN, local=json_bytes(FOREIGN_SETTINGS), backup=PRE_EXISTING_BACKUP),
    # `claude: false` from the start: nothing in `.claude/`
    'claude_false_fresh': case({**FEATURE, 'claude': False}),
    # `claude: false` AFTER an installation is not a golden case: K10 (F6) changed it on purpose (our entries and scripts are removed);
    # the new behaviour is tested in tests/test_skillsync_hook_link.py
    # dry-run of a fresh feature project: only `[dry-run]` lines, no file written
    'feature_dry_run': case(FEATURE, dry_run=True),
    # dry-run over an existing foreign file: `[dry-run] Would update`, the bytes stay
    'foreign_dry_run': case(FEATURE, local=json_bytes(FOREIGN_SETTINGS), dry_run=True),
}


def manifest_for(spec, patch=None):
    manifest = {'version': 1, 'project': {'name': 'Org/Hooksync', 'type': 'local'}, **spec['manifest']}
    hooks = dict(manifest.get('hooks') or {})
    for key, value in NEW_HOOK_KEYS_OFF.items():
        hooks.setdefault(key, value)
    manifest['hooks'] = hooks
    manifest.update(patch or {})
    return manifest


def write_machine_config(path):
    """Machine config: the repository library as the only core layer, a rule for everything; `python_command` stays default (`python3`)."""
    from layers_helpers import transitional_config
    Path(path).write_text(yaml.safe_dump(transitional_config(LIBRARY, legacy_key=False, tags={}), sort_keys=False), encoding='utf-8')


def run_link(project, *args, timeout=180):
    """`skillsync link <project>` as a subprocess in the CURRENT (isolated) environment; returns the CompletedProcess."""
    result = subprocess.run([sys.executable, str(CLI), 'link', str(project), *args], capture_output=True, text=True, timeout=timeout,
                            cwd=str(project), env=dict(os.environ))
    if result.returncode != 0 or 'Traceback' in result.stderr:
        raise RuntimeError(f'link failed ({result.returncode}): {result.stdout}\n{result.stderr}')
    return result


KEEP_LINE = re.compile(r'^(Updated:|\[warn\]|\[dry-run\])')
TOPIC = ('settings.local.json', '.claude/hooks', '.claude/.gitignore')


def normalise(stdout, project):
    """Lines of `link` that concern the hook settings and files, with the project path replaced by a symbol and `/` as the separator."""
    lines = []
    prefixes = {str(project), Path(project).as_posix()}
    for line in stdout.splitlines():
        if not KEEP_LINE.match(line):
            continue
        for prefix in sorted(prefixes, key=len, reverse=True):
            line = line.replace(prefix, PLACEHOLDER)
        line = line.replace('\\', '/')
        if any(topic in line for topic in TOPIC):
            lines.append(line)
    return lines


CLI_ARGUMENT = re.compile(r"--cli '[^']*'")


def scrub_cli(text):
    """The argv of the CLI in our Stop entry (`--cli '<json>'`: interpreter and checkout of THIS machine) is replaced by a symbol."""
    return None if text is None else CLI_ARGUMENT.sub('--cli <CLI>', text)


def text_of(path):
    return Path(path).read_bytes().decode('utf-8') if Path(path).is_file() else None


def capture_state(project):
    project = Path(project)
    local = scrub_cli(text_of(descend(project, LOCAL)))
    try:
        parsed = json.loads(local) if local else {}
    except ValueError:   # the invalid-JSON case: the file stays as it was, there is no order to record
        parsed = {}
    hooks_dir = descend(project, HOOKS_DIR)
    names = sorted(entry.name for entry in hooks_dir.iterdir()) if hooks_dir.is_dir() else []
    root_gitignore = text_of(child(project, '.gitignore')) or ''
    return {
        'settings_local': local,
        'settings_backup': scrub_cli(text_of(descend(project, BACKUP))),
        'hook_files': names,
        'claude_gitignore': text_of(descend(project, CLAUDE_GITIGNORE)),
        'root_gitignore_skillsync': '.skillsync/' in root_gitignore.splitlines(),
        'top_level_keys': list(parsed.keys()),
        'event_order': list((parsed.get('hooks') or {}).keys()),
    }


def hook_hashes(project):
    hooks_dir = descend(project, HOOKS_DIR)
    names = sorted(entry.name for entry in hooks_dir.iterdir()) if hooks_dir.is_dir() else []
    return {name: hashlib.sha256(child(hooks_dir, name).read_bytes()).hexdigest() for name in names}


def build_project(name, spec, workdir):
    """A fresh project directory `workdir/<name>` with the manifest, the initial settings/backup bytes and (when asked) a git index entry."""
    project = child(workdir, name)
    project.mkdir()
    manifest_path = descend(project, '.github/.skillsync.yaml')
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(yaml.safe_dump(manifest_for(spec), sort_keys=False), encoding='utf-8')
    for relative, data in ((LOCAL, spec['local']), (BACKUP, spec['backup'])):
        if data is not None:
            target = descend(project, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    if spec['tracked']:
        subprocess.run(['git', 'init', '-q'], cwd=str(project), check=True)
        subprocess.run(['git', 'add', '-f', LOCAL], cwd=str(project), check=True)
    return project


def run_case(name, spec, workdir):
    """Runs the case and returns the pattern dict. The caller provides an ISOLATED environment (temporary HOME, XDG, SKILLSYNC_CONFIG)."""
    project = build_project(name, spec, workdir)
    result = None
    last = len(spec['runs']) - 1
    cumulative = {}
    for index, patch in enumerate(spec['runs']):
        if patch:
            cumulative.update(patch)
            descend(project, '.github/.skillsync.yaml').write_text(yaml.safe_dump(manifest_for(spec, cumulative), sort_keys=False), encoding='utf-8')
        args = ['--dry-run'] if spec['dry_run'] and index == last else []
        result = run_link(project, *args)
    pattern = capture_state(project)
    pattern['stdout'] = normalise(result.stdout, project)
    pattern['hook_sha256'] = hook_hashes(project)
    if not spec['dry_run']:
        again = run_link(project)
        second = capture_state(project)
        pattern['second_stdout'] = normalise(again.stdout, project)
        pattern.update({f'second_{key}': value for key, value in second.items() if key in ('settings_local', 'settings_backup', 'hook_files')})
    return pattern
