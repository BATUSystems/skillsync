"""Additive skill profiles, confined workflow upgrades and project guards."""
import base64
import hashlib
import os
from pathlib import Path
import re
import tempfile
import uuid
import yaml
from .common import (add_project_arguments, changed_files, emit, layer_file, load_layer_project,
                     matches, read_yaml, strings, workflow_config)
from .quality import _locks, plan_validation, run_validation
from .storage import read_json, state_path, write_json


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', value) or '..' in value:
        raise ValueError('Expected a safe profile/rule/revision identifier')
    return value


def _local(root, name):
    if not isinstance(name, str) or not name or Path(name).is_absolute() or '..' in Path(name).parts or '\\' in name:
        raise ValueError('Expected a relative workflow path')
    root = Path(root).resolve()
    path = root
    for part in Path(name).parts:
        path /= part
        if path.is_symlink():
            raise ValueError('Workflow artifact symlinks are forbidden')
    return path


def resolve_profile_skills(manifest, library, root=None):
    """Skills of the selected profiles (merged from `workflow/skill-profiles.yaml` + `.d/` of all layers).
    `library`: `LayerSet` (`skillsync:expand_skills` wraps its `Path` form with `as_layerset` first)."""
    from .common import require_layerset
    layerset = require_layerset(library, 'resolve_profile_skills')
    config = workflow_config(Path(root or '.'), manifest, layerset).get('skill_profiles', {})
    selected = strings(config.get('selected', []), 'skill_profiles.selected')
    if not selected:
        return []
    catalog = layerset.merged_file('workflow/skill-profiles.yaml').data
    if catalog.get('version') != 1 or not isinstance(catalog.get('profiles'), dict):
        raise ValueError('Missing or invalid skill profile catalog')
    result = []
    for name in selected:
        _name(name)
        if name not in catalog['profiles']:
            raise ValueError('Unknown skill profile: ' + name)
        for skill in strings(catalog['profiles'][name], 'profile skills'):
            _name(skill)
            if not layerset.providers(f'skills/{skill}/SKILL.md'):
                raise ValueError('Profile references missing skill: ' + skill)
            if skill not in result:
                result.append(skill)
    return result


def profiles(root, manifest, library):
    from .common import expand_project_skills
    config = workflow_config(root, manifest, library).get('skill_profiles', {})
    maximum = config.get('catalog_budget_bytes', 32768)
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
        raise ValueError('Catalog budget must be a nonnegative integer')
    names = expand_project_skills(manifest, library)
    catalog = []
    for name in names:
        found = library.resolve(f'skills/{name}/SKILL.md')
        path = found[1] if found else None
        if path is None:
            raise ValueError('Missing skill: ' + name)
        text = path.read_text(encoding='utf-8')
        if not text.startswith('---\n') or len(text.split('---', 2)) != 3:
            raise ValueError('Skill requires YAML frontmatter: ' + name)
        metadata = yaml.safe_load(text.split('---', 2)[1])
        description = metadata.get('description') if isinstance(metadata, dict) else None
        if not isinstance(description, str) or not description.strip():
            raise ValueError('Skill requires description: ' + name)
        catalog.append({'name': name, 'description': description})
    size = sum(len((item['name'] + ': ' + item['description'] + '\n').encode()) for item in catalog)
    return {'status': 'PASS' if size <= maximum else 'FAIL', 'selected': config.get('selected', []),
            'skills': names, 'catalog': catalog, 'bytes': size, 'limit': maximum,
            'note': 'Budget measures names and descriptions; skill bodies are loaded on demand'}


def _bytes(path):
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 512 * 1024:
        raise ValueError('Workflow artifact must be a bounded regular file')
    return path.read_bytes()


def _hash(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _target(root, name):
    path = _local(root, name)
    if not any(name.startswith(prefix) for prefix in ('.skillsync/providers/', '.skillsync/scripts/', '.scripts/skillsync/', 'docs/workflow/')):
        raise ValueError('Upgrade target is outside workflow artifact allowlist')
    return path


def _replace(path, data, mode=0o600):
    if path.is_symlink():
        raise ValueError('Workflow artifact symlink is forbidden')
    if data is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.skillsync-', dir=path.parent)
    try:
        os.fchmod(fd, mode) if hasattr(os, 'fchmod') else None
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def upgrade(root, manifest, library, action='plan', run=False, backup=None):
    if action not in ('plan', 'apply', 'rollback'):
        raise ValueError('Unknown upgrade action')
    with _locks(['upgrade:' + str(Path(root).resolve())]):
        state_file = state_path(root, 'upgrade/state.json', create=run)
        state = read_json(state_file, {'version': 1, 'sources': {}})
        if not isinstance(state, dict) or state.get('version') != 1 or not isinstance(state.get('sources'), dict):
            raise ValueError('Invalid workflow upgrade state')
        if action == 'rollback':
            identity = _name(backup or state.get('backup'))
            saved = read_json(state_path(root, 'upgrade/backups/' + identity + '.json'))
            if not isinstance(saved, dict) or not isinstance(saved.get('files'), list) or saved.get('version') != 1:
                raise ValueError('Missing or invalid workflow backup')
            restore = []
            for item in saved['files']:
                target = _target(root, item['target'])
                before = base64.b64decode(item['before'], validate=True) if item['before'] is not None else None
                if _hash(before) != item['before_hash']:
                    raise ValueError('Workflow backup digest does not match')
                current = _bytes(target)
                if _hash(current) == item['before_hash']:
                    continue
                if _hash(current) != item['after_hash']:
                    return {'status': 'BLOCKED', 'reason': 'Later local edits prevent rollback', 'target': item['target']}
                restore.append((target, before, item.get('mode', 0o600)))
            if not run:
                return {'status': 'NOT_RUN', 'backup': identity, 'targets': [str(x[0]) for x in restore]}
            write_json(state_file, dict(state, pending=True, backup=identity))
            for target, before, mode in restore:
                _replace(target, before, mode)
            write_json(state_file, saved['previous_state'])
            return {'status': 'PASS', 'rolled_back': identity}
        if state.get('pending'):
            return {'status': 'BLOCKED', 'reason': 'Interrupted upgrade requires rollback', 'backup': state.get('backup')}
        config = workflow_config(root, manifest, library).get('upgrade', {})
        if not config:
            return {'status': 'NOT_RUN', 'reason': 'No workflow upgrade artifacts configured'}
        revision = _name(config.get('revision'))
        artifacts = config.get('artifacts')
        if not isinstance(artifacts, list) or not artifacts or len(artifacts) > 100:
            raise ValueError('Upgrade requires bounded nonempty artifacts')
        rows, updates, seen = [], [], set()
        total = 0
        for item in artifacts:
            if not isinstance(item, dict):
                raise ValueError('Upgrade artifact must be a mapping')
            name = item.get('target')
            target = _target(root, name)
            if name in seen:
                raise ValueError('Duplicate upgrade target')
            seen.add(name)
            source = layer_file(library, item.get('source'))
            incoming = _bytes(source) if source is not None else None
            if incoming is None:
                raise ValueError('Missing upgrade source')
            current = _bytes(target)
            total += len(incoming) + len(current or b'')
            if total > 512 * 1024:
                raise ValueError('Upgrade transaction exceeds backup size budget')
            old = state['sources'].get(name)
            local_hash, new_hash = _hash(current), _hash(incoming)
            if local_hash == new_hash:
                decision = 'unchanged'
            elif old is None:
                decision = 'create' if current is None else 'conflict'
            elif local_hash == old:
                decision = 'update'
            elif new_hash == old:
                decision = 'preserve-local'
            else:
                decision = 'conflict'
            rows.append({'target': name, 'decision': decision, 'base': old, 'local': local_hash, 'incoming': new_hash})
            updates.append((name, target, current, incoming, decision))
        if any(row['decision'] == 'conflict' for row in rows):
            return {'status': 'BLOCKED', 'revision': revision, 'artifacts': rows, 'reason': 'Three-way conflict; local files preserved'}
        if action == 'plan' or not run:
            return {'status': 'NOT_RUN', 'revision': revision, 'artifacts': rows}
        identity = uuid.uuid4().hex
        saved = {'version': 1, 'previous_state': state, 'files': []}
        for name, target, current, incoming, decision in updates:
            if decision in ('create', 'update'):
                saved['files'].append({'target': name, 'before': base64.b64encode(current).decode() if current is not None else None,
                                      'before_hash': _hash(current), 'after_hash': _hash(incoming),
                                      'mode': target.stat().st_mode & 0o777 if current is not None else 0o600})
        write_json(state_path(root, 'upgrade/backups/' + identity + '.json', create=True), saved)
        write_json(state_file, dict(state, pending=True, backup=identity))
        for name, target, current, incoming, decision in updates:
            if decision in ('create', 'update'):
                if _hash(_bytes(target)) != _hash(current):
                    raise ValueError('Local edit during upgrade; pending transaction requires rollback')
                _replace(target, incoming, target.stat().st_mode & 0o777 if current is not None else 0o600)
        sources = dict(state['sources'])
        sources.update({row['target']: row['incoming'] for row in rows})
        write_json(state_file, {'version': 1, 'revision': revision, 'sources': sources, 'backup': identity})
        return {'status': 'PASS', 'revision': revision, 'artifacts': rows, 'backup': identity}


def guard(root, manifest, library, files=None, run=False):
    config = workflow_config(root, manifest, library).get('guardrails', {})
    rules, checks = config.get('rules', []), config.get('checks', [])
    if not isinstance(rules, list) or not isinstance(checks, list):
        raise ValueError('Guard rules/checks must be lists')
    if not rules and not checks:
        return {'status': 'NOT_RUN', 'reason': 'No project convention guards configured'}
    compiled, ids = [], set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError('Guard rule must be a mapping')
        name = _name(rule.get('id'))
        if name in ids or rule.get('severity', 'error') not in ('error', 'warning'):
            raise ValueError('Guard rule ids must be unique and severity valid')
        ids.add(name)
        strings(rule.get('paths'), 'guard.paths')
        terms = {}
        for key in ('forbidden', 'required'):
            values = rule.get(key, [])
            values = [values] if isinstance(values, str) else strings(values, 'guard.' + key)
            if any(len(value) > 1024 for value in values):
                raise ValueError('Guard pattern exceeds budget')
            terms[key] = [re.compile(value, re.MULTILINE) for value in values]
        if not any(terms.values()):
            raise ValueError('Guard rule requires a forbidden/required pattern')
        compiled.append((rule, terms))
    names = files if files is not None else changed_files(root)
    paths = [(name, _local(root, name)) for name in names]
    if not run:
        return {'status': 'NOT_RUN', 'rules': list(ids), 'files': names, 'checks': checks}
    findings, scanned = [], []
    for name, path in paths:
        applicable = [(rule, terms) for rule, terms in compiled if matches(name, rule['paths'])]
        if not applicable or not path.exists():
            continue
        data = _bytes(path)
        text = data.decode('utf-8')
        scanned.append(name)
        for rule, terms in applicable:
            for pattern in terms['forbidden']:
                for match in pattern.finditer(text):
                    findings.append({'rule': rule['id'], 'file': name, 'line': text.count('\n', 0, match.start()) + 1, 'severity': rule.get('severity', 'error'), 'reason': 'Forbidden convention pattern'})
                    if len(findings) >= 1000:
                        return {'status': 'FAIL', 'findings': findings, 'reason': 'Finding limit reached'}
            for pattern in terms['required']:
                if not pattern.search(text):
                    findings.append({'rule': rule['id'], 'file': name, 'line': 1, 'severity': rule.get('severity', 'error'), 'reason': 'Required convention pattern missing'})
    result = {'status': 'FAIL' if any(item['severity'] == 'error' for item in findings) else 'PASS' if scanned else 'NOT_RUN', 'findings': findings, 'scanned': scanned}
    if checks:
        local = dict(manifest)
        local['workflow'] = dict(manifest.get('workflow', {}))
        local['workflow']['validation'] = {'checks': checks}
        local['workflow']['guardrails'] = {'rules': [], 'checks': []}
        planned = plan_validation(root, local, library, profile='all')
        executed = run_validation(root, planned['checks']) if planned['status'] != 'BLOCKED' else planned
        result['checks'] = executed
        if executed['status'] != 'PASS' or result['status'] != 'FAIL':
            result['status'] = executed['status']
    return result


def _cmd(args):
    try:
        root, manifest, library = load_layer_project(args.path, args.layers)
        if args.command == 'profiles':
            result = profiles(root, manifest, library)
        elif args.command == 'upgrade':
            result = upgrade(root, manifest, library, args.action, args.run, args.backup)
        else:
            result = guard(root, manifest, library, args.files, args.run)
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError, re.error, KeyboardInterrupt) as error:
        result = {'status': 'BLOCKED', 'reason': str(error)}
    return emit(result, args.json)


def register(subparsers):
    for name in ('profiles', 'upgrade', 'guard'):
        parser = subparsers.add_parser(name, help='Workflow ' + name)
        add_project_arguments(parser)
        if name == 'upgrade':
            parser.add_argument('--action', choices=['plan', 'apply', 'rollback'], default='plan')
            parser.add_argument('--backup')
        if name == 'guard':
            parser.add_argument('--files', nargs='*')
        if name != 'profiles':
            parser.add_argument('--run', action='store_true')
        parser.set_defaults(func=_cmd)
