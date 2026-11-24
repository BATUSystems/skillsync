"""Are the skills of the manifest linked into the host directories of a project (CORE-1150-UNLINKED)?

One cheap answer for `doctor` (one summary error per host), `route`, `status` and the session start hook: only `exists` / `is_symlink` and one
`git ls-files -s` per host for the small regular files, never a hash and never a read of a skill. What counts as "not linked": a missing path, a
dangling symlink, and a regular file that git tracks as a link and did not recreate (a Windows checkout). A stale or foreign symlink, and a copy
(also one that differs from its source) count as linked: whether they are CURRENT is the business of `doctor`."""
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

from . import layers as _layers
from .common import LEGACY_WORKFLOW_SKILL, WORKFLOW_SKILL, checked_out_link, expand_project_skills, strings
from . import claude_manifest

SMALL_FILE = 4096   # as `checked_out_link`: a tracked link checked out as text is a tiny file
GIT_TIMEOUT = 10
SHOWN = 5           # names listed in a message


def _codex_enabled(manifest, agentflow):
    setting = manifest.get('codex', agentflow)
    return setting is not False and (not isinstance(setting, dict) or setting.get('enabled', True))


def skill_hosts(manifest, layerset, skills):
    """The host directories the skills of `skills` are installed into: [(host, prefix, excluded names)] in the order github, claude, codex.
    The single source for `doctor` and `linkage_status`: claude only when `claude_manifest.enabled`, codex when enabled (by default only with the
    workflow skill) and without `codex.exclude_skills` of the layers (`codex.yaml`) AND of the manifest."""
    agentflow = WORKFLOW_SKILL in skills or LEGACY_WORKFLOW_SKILL in skills
    codex_defaults = layerset.merged_file('codex.yaml').data
    codex_local = manifest.get('codex') if isinstance(manifest.get('codex'), dict) else {}
    excluded = set(strings(codex_defaults.get('exclude_skills', []), 'codex.exclude_skills')
                   + strings(codex_local.get('exclude_skills', []), 'codex.exclude_skills'))
    hosts = [('copilot', '.github/skills', set())]
    if claude_manifest.enabled(manifest):
        hosts.append(('claude', '.claude/skills', set()))
    if _codex_enabled(manifest, agentflow):
        hosts.append(('codex', '.agents/skills', excluded))
    return hosts


def _tracked_as_link(directory, names):
    """Names (of `names`) that git tracks in `directory` as a symlink (mode 120000): ONE git call. Empty outside git and on any error."""
    try:
        process = subprocess.run(['git', '-C', str(directory), 'ls-files', '-s'], capture_output=True, timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return set()
    if process.returncode:
        return set()
    found = set()
    for line in process.stdout.decode('utf-8', 'replace').splitlines():
        meta, _tab, name = line.partition('\t')
        if meta.startswith('120000 ') and name in names:
            found.add(name)
    return found


def _missing(directory, names):
    """Names of `names` that are not linked in `directory`."""
    missing, candidates = [], {}
    for name in names:
        path = directory / name
        if path.is_symlink():
            if not path.exists():
                missing.append(name)
        elif not path.exists():
            missing.append(name)
        elif path.is_file():
            try:
                if path.stat().st_size <= SMALL_FILE:
                    candidates[name] = path
            except OSError:
                pass
    if candidates:
        for name in sorted(_tracked_as_link(directory, set(candidates))):
            if checked_out_link(candidates[name]):
                missing.append(name)
    return sorted(missing, key=names.index)


def linkage_status(root, manifest, layerset):
    """None when it cannot be told (no manifest, a layer error, a malformed key): never an alarm. Otherwise
    {'state': 'linked' | 'partial' | 'unlinked', 'hosts': {host: {'dir', 'expected', 'missing': [...]}}, 'fix': 'skillsync link <dir>'};
    `unlinked` when every expected skill of every host is missing, `partial` when some are."""
    if not isinstance(manifest, dict) or not manifest:
        return None
    try:
        skills = expand_project_skills(manifest, layerset)
        hosts = skill_hosts(manifest, layerset, skills)
        names = [name for name in skills if layerset.resolve(f'skills/{name}/SKILL.md')]   # a name without a source is `doctor`'s "Missing skill"
    except (OSError, ValueError, TypeError, KeyError, AttributeError, _layers.LayerError, yaml.YAMLError):
        return None
    base = Path(root)
    result, expected_total, missing_total = {}, 0, 0
    for host, prefix, excluded in hosts:
        wanted = [name for name in names if name not in excluded]
        try:
            missing = _missing(base / prefix, wanted)
        except OSError:
            return None
        result[host] = {'dir': prefix, 'expected': len(wanted), 'missing': missing}
        expected_total += len(wanted)
        missing_total += len(missing)
    state = 'linked' if not missing_total else ('unlinked' if missing_total == expected_total else 'partial')
    return {'state': state, 'hosts': result, 'fix': f'skillsync link {_layers._quote_arg(str(root))}'}


def summary(status):
    """[(host directory, line for people)]: one per host with missing skills, the line ends with the command to run. [] when all is linked or unknown."""
    if not status or status.get('state') == 'linked':
        return []
    lines = []
    for host, info in status['hosts'].items():
        if info['missing']:
            names = ', '.join(info['missing'][:SHOWN]) + (', …' if len(info['missing']) > SHOWN else '')
            lines.append((info['dir'], f"Host {host}: {len(info['missing'])} of {info['expected']} skills from the manifest are not linked ({names}); run `{status['fix']}`"))
    return lines


def note(status):
    """One sentence for `route` and the session start: which hosts miss skills and what to run. '' when all is linked or unknown."""
    if not status or status.get('state') == 'linked':
        return ''
    parts = [f"{host} {len(info['missing'])}/{info['expected']}" for host, info in status['hosts'].items() if info['missing']]
    return f"Skills from the manifest are not linked ({', '.join(parts)}). Run: {status['fix']}"


def report_fields(root, manifest, layerset):
    """{'linkage': ..., 'linkageNote': ...} for `route` and `status`: only when something is not linked (the key is absent otherwise)."""
    status = linkage_status(root, manifest, layerset)
    text = note(status)
    return {'linkage': status, 'linkageNote': text} if text else {}


# --- one line on stderr for any other command run in a project whose skills are not linked (CORE-1150-UNLINKED, U-3b) ---------------------------
NOTICE_OFF = 'SKILLSYNC_NO_LINK_NOTICE'
NOTICE_SKIP = frozenset({'link', 'pull', 'watch', 'doctor', 'route', 'status'})   # these say it themselves, or install the skills
HOST_DIRS = ('.github/skills', '.claude/skills')


def _project_root(path_argument):
    """The project directory of a command: its `path` argument (a directory, or the manifest file), else the working directory and its parents up
    to the first `.git`. None when there is no manifest."""
    candidates = []
    if path_argument:
        given = Path(path_argument)
        candidates.append(given.parent.parent if given.name == '.skillsync.yaml' and given.parent.name == '.github' else given)
    else:
        here = Path.cwd()
        for folder in (here, *here.parents):
            candidates.append(folder)
            if (folder / '.git').exists():
                break
    for folder in candidates:
        try:
            if (folder / '.github' / '.skillsync.yaml').is_file():
                return folder
        except OSError:
            continue
    return None


_PLAIN_NAME = re.compile(r'[a-z0-9][a-z0-9._-]*')


def _declared_skills_present(root):
    """None when the manifest cannot be read or declares no skills; else whether every plainly named declared skill is in some host directory. Parses
    only the manifest: a linked project pays a few `stat` calls and nothing else. Only `exists()` counts here (a dangling link or a text file
    left by a checkout falls through to the full check); a name that is not a plain directory name (an `@tag`, a glob, an old alias
    such as `agentflow` or `go-jira`) is assumed present, so such a project never pays for the full check and can only be warned by `doctor`, `route`,
    `status` and the hook."""
    try:
        data = yaml.safe_load((root / '.github' / '.skillsync.yaml').read_text(encoding='utf-8'))
    except (OSError, ValueError, yaml.YAMLError):
        return None
    skills = data.get('skills') if isinstance(data, dict) else None
    names = [name for name in skills if isinstance(name, str) and _PLAIN_NAME.fullmatch(name) and name not in (LEGACY_WORKFLOW_SKILL, 'go-jira')] \
        if isinstance(skills, list) else []
    if not names:
        return None
    return all(any((root / host / name).exists() for host in HOST_DIRS) for name in names)


def notice(command, path_argument, argv, environ=None):
    """The line for stderr (`skillsync: Skills from the manifest are not linked ...`) or None. Never raises, never writes: the caller prints it.
    Off for `--json` (machines read the streams), for the commands of NOTICE_SKIP, and with SKILLSYNC_NO_LINK_NOTICE set (not 0)."""
    environ = os.environ if environ is None else environ
    if command in NOTICE_SKIP or '--json' in argv or environ.get(NOTICE_OFF, '') not in ('', '0'):
        return None
    try:
        root = _project_root(path_argument)
        if root is None or _declared_skills_present(root) is not False:
            return None
        from .common import load_layer_project
        _root, manifest, layerset = load_layer_project(root)
        text = note(linkage_status(root, manifest, layerset))
    except Exception:   # noqa: BLE001 - a hint on stderr must never break or slow a command that works
        return None
    return f'skillsync: {text}' if text else None


def print_notice(command, path_argument, argv):
    try:
        text = notice(command, path_argument, argv)
        if text and sys.stderr is not None:
            print(text, file=sys.stderr)
    except Exception:   # noqa: BLE001 - a closed or broken stderr must not stop the command
        pass
