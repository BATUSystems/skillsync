"""Are the skills of the manifest linked into the host directories of a project (CORE-1150-UNLINKED)?

One cheap answer for `doctor` (one summary error per host), `route`, `status` and the session start hook: only `exists` / `is_symlink` and one
`git ls-files -s` per host for the small regular files, never a hash and never a read of a skill. What counts as "not linked": a missing path, a
dangling symlink, and a regular file that git tracks as a link and did not recreate (a Windows checkout). A stale or foreign symlink, and a copy
(also one that differs from its source) count as linked: whether they are CURRENT is the business of `doctor`."""
import subprocess
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
