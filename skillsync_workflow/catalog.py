"""Skill catalog: INDEX.md generation, organization/technology checks and `suggest`."""
import fnmatch
import json
import os
from pathlib import Path
import subprocess
import sys
import yaml
from . import layers as _layers
from .common import (LEGACY_WORKFLOW_SKILL, WORKFLOW_SKILL, add_project_arguments, config_file,
                     expand_project_skills, load_layer_project, read_yaml, redact, require_layerset, strings)

SCOPES = ('org', 'client', 'project', 'tech', 'workflow', 'tool', 'personal')
SCOPE_TITLES = {
    'org': 'Organizacje', 'client': 'Klienci', 'project': 'Projektowe', 'tech': 'Technologiczne',
    'workflow': 'Workflow', 'tool': 'Narzędzia', 'personal': 'Osobiste',
}
UNCATALOGUED = 'Nieskatalogowane'
CATALOG_PATH = 'workflow/skill-catalog.yaml'
INDEX_PATH = 'skills/INDEX.md'
PRUNE = {'node_modules', '.git', 'bin', 'obj', '.venv'}
MAX_DEPTH = 4
DETECTABLE = ('dotnet', 'angular', 'django', 'capacitor', 'hugo', 'rust', 'python', 'node')
DESCRIPTION_LIMIT = 160


def load_catalog(library):
    """Return the validated catalog mapping (merged across layers: `skill-catalog.yaml` + `.d/`), or None
    when no layer has a catalog. `library`: `LayerSet`."""
    merged = require_layerset(library, 'load_catalog').merged_file(CATALOG_PATH)
    if not merged.sources:
        return None
    data = merged.data
    if data.get('version') != 1:
        raise ValueError('Unsupported skill catalog version')
    orgs = data.get('orgs', {})
    skills = data.get('skills', {})
    if not isinstance(orgs, dict) or not isinstance(skills, dict):
        raise ValueError('Skill catalog orgs and skills must be mappings')
    for name, org in orgs.items():
        if not isinstance(org, dict) or not isinstance(org.get('skill'), str):
            raise ValueError(f'Catalog org {name} requires a skill')
        patterns = org.get('projects', [])
        if not isinstance(patterns, list) or not all(isinstance(item, str) for item in patterns):
            raise ValueError(f'Catalog org {name} projects must be a list of patterns')
    for name, entry in skills.items():
        if not isinstance(entry, dict) or entry.get('scope') not in SCOPES:
            raise ValueError(f'Catalog skill {name} requires scope: ' + '|'.join(SCOPES))
        if 'org' in entry and not isinstance(entry['org'], str):
            raise ValueError(f'Catalog skill {name} org must be a string')
        tech = entry.get('tech', [])
        if not isinstance(tech, list) or not all(isinstance(item, str) for item in tech):
            raise ValueError(f'Catalog skill {name} tech must be a list of strings')
    return {'orgs': orgs, 'skills': skills}


def skill_description(library, name):
    found = require_layerset(library, 'skill_description').resolve(f'skills/{name}/SKILL.md')
    if found is None:
        return ''
    path = found[1]
    try:
        text = path.read_text(encoding='utf-8')
        metadata = yaml.safe_load(text.split('---', 2)[1]) if text.startswith('---\n') else {}
    except (OSError, yaml.YAMLError, IndexError):
        return ''
    description = metadata.get('description') if isinstance(metadata, dict) else ''
    return ' '.join(str(description or '').split())


def library_skill_names(library):
    return require_layerset(library, 'library_skill_names').skill_names(require_skill_md=True)


def _short(text):
    return text if len(text) <= DESCRIPTION_LIMIT else text[:DESCRIPTION_LIMIT - 1].rstrip() + '…'


def render_index(library):
    library = require_layerset(library, 'render_index')
    catalog = load_catalog(library)
    if catalog is None:
        raise ValueError(f'Missing skill catalog: {CATALOG_PATH} in layers {", ".join(library.ids)}')
    names = library_skill_names(library)
    entries = catalog['skills']
    groups = {}
    orphans = []
    for name in names:
        entry = entries.get(name)
        if entry is None:
            orphans.append(name)
            continue
        scope = entry['scope']
        if scope in ('org', 'client', 'project'):
            subgroup = entry.get('org') or '(bez organizacji)'
        elif scope == 'tech':
            subgroup = ', '.join(entry.get('tech', [])) or '(bez technologii)'
        else:
            subgroup = ''
        groups.setdefault(scope, {}).setdefault(subgroup, []).append(name)
    lines = ['# Indeks skilli', '',
             'Plik generowany: `skillsync skills index` z `workflow/skill-catalog.yaml`. Nie edytuj ręcznie.', '']
    for scope in SCOPES:
        if scope not in groups:
            continue
        lines += [f'## {SCOPE_TITLES[scope]} (`{scope}`)', '']
        for subgroup in sorted(groups[scope]):
            if subgroup:
                lines += [f'### {subgroup}', '']
            for name in groups[scope][subgroup]:
                lines.append(f'- `{name}` — {_short(skill_description(library, name))}')
            lines.append('')
    if orphans:
        lines += [f'## {UNCATALOGUED}', '']
        lines += [f'- `{name}` — {_short(skill_description(library, name))}' for name in orphans]
        lines.append('')
    missing = sorted(set(entries) - set(names))
    if missing:
        lines += ['## Wpisy katalogu bez skilla', ''] + [f'- `{name}`' for name in missing] + ['']
    return '\n'.join(lines).rstrip('\n') + '\n'


def _library_from_args(args):
    """LayerSet for commands without a project (`skills index`): `--layer id=path` overrides, else the
    machine config (rule for the current directory; outside any project all layers)."""
    return _layers.resolve_layers(layers=list(getattr(args, 'layers', None) or []) or None)


def index_cache_path(layerset):
    """Cache file of the generated index for this layer set (`skills index` never writes to a layer)."""
    return _layers.cache_home() / 'skills-index' / ('INDEX-' + '+'.join(layerset.ids or ['none']) + '.md')


def cmd_index(args):
    library = _library_from_args(args)
    text = render_index(library)
    if getattr(args, 'stdout', False):
        sys.stdout.write(text)
        return 0
    target = index_cache_path(library)
    if args.check:
        current = target.read_text(encoding='utf-8') if target.is_file() else None
        if current != text:
            print(f'{target} is stale; run `skillsync skills index`')
            return 1
        print(f'{target} is up to date')
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('w', encoding='utf-8', newline='\n') as handle:
        handle.write(text)
    print(f'Wrote {target}')
    return 0


def detect_technologies(root):
    """Detect technologies from marker files; local only, depth-limited."""
    root = Path(root)
    found = set()
    for directory, dirs, files in os.walk(root):
        depth = len(Path(directory).relative_to(root).parts)
        dirs[:] = [d for d in dirs if d not in PRUNE] if depth < MAX_DEPTH else []
        names = set(files)
        if any(f.endswith(('.csproj', '.sln')) for f in names):
            found.add('dotnet')
        if 'angular.json' in names:
            found.add('angular')
        if 'manage.py' in names:
            found.add('django')
        if any(f.startswith('capacitor.config.') for f in names):
            found.add('capacitor')
        if names & {'hugo.toml', 'hugo.yaml', 'config.toml'} and ({'content', 'themes'} & (set(dirs) | _siblings(directory))):
            found.add('hugo')
        if 'Cargo.toml' in names:
            found.add('rust')
        if 'pyproject.toml' in names or any(f.startswith('requirements') and f.endswith('.txt') for f in names):
            found.add('python')
        if 'package.json' in names:
            found.add('node')
    return found


def _siblings(directory):
    try:
        return {item.name for item in Path(directory).iterdir() if item.is_dir()}
    except OSError:
        return set()


def project_name(root, manifest):
    project = manifest.get('project', {})
    name = project.get('name') if isinstance(project, dict) else None
    return name if isinstance(name, str) and name else None


def project_orgs(root, manifest, catalog):
    """Organizations whose patterns match the manifest project.name (fallback: <parent>/<dir> of the root)."""
    root = Path(root)
    candidates = [project_name(root, manifest)] if project_name(root, manifest) else [f'{root.parent.name}/{root.name}']
    result = []
    for org, spec in catalog['orgs'].items():
        if any(fnmatch.fnmatch(candidate, pattern) for candidate in candidates for pattern in spec.get('projects', [])):
            result.append(org)
    return result


def catalog_findings(root, manifest, library, skills=None):
    """Return (level, reason) warnings for org/technology mismatches; empty when the library has no catalog."""
    try:
        catalog = load_catalog(library)
    except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError) as error:
        return [f'Invalid skill catalog: {" ".join(str(error).split())[:300]}']
    if catalog is None:
        return []
    skills = expand_project_skills(manifest, library) if skills is None else skills
    orgs = project_orgs(root, manifest, catalog)
    detected = detect_technologies(root)
    findings = []
    if orgs:
        for name in skills:
            entry = catalog['skills'].get(name, {})
            if entry.get('org') and entry['org'] not in orgs:
                findings.append(f'Skill of another organization: {name} ({entry["org"]}; project organizations: {", ".join(orgs)})')
        for org in orgs:
            required = catalog['orgs'][org]['skill']
            if required not in skills:
                findings.append(f'Missing organization skill: {required} (organization {org})')
    for name in skills:
        techs = [t for t in catalog['skills'].get(name, {}).get('tech', []) if t in DETECTABLE]
        if catalog['skills'].get(name, {}).get('scope') == 'tech' and techs and not detected.intersection(techs):
            findings.append(f'Technology not detected: {name} requires {", ".join(techs)}')
    return findings


def suggest(root, manifest, library):
    catalog = load_catalog(library)
    if catalog is None:
        raise ValueError(f'Missing skill catalog: {CATALOG_PATH}')
    skills = expand_project_skills(manifest, library)
    orgs = project_orgs(root, manifest, catalog)
    detected = detect_technologies(root)
    add, remove, reasons = [], [], {}
    for name in skills:
        entry = catalog['skills'].get(name, {})
        if orgs and entry.get('org') and entry['org'] not in orgs:
            remove.append(name)
            reasons[name] = f'skill of organization {entry["org"]}; project organizations: {", ".join(orgs)}'
        techs = [t for t in entry.get('tech', []) if t in DETECTABLE]
        if entry.get('scope') == 'tech' and techs and not detected.intersection(techs):
            remove.append(name)
            reasons[name] = f'technology not detected: {", ".join(techs)}'
    for org in orgs:
        required = catalog['orgs'][org]['skill']
        if required not in skills:
            add.append(required)
            reasons[required] = f'organization skill for {org}'
    covered = {t for name in skills for t in catalog['skills'].get(name, {}).get('tech', [])}
    for name, entry in sorted(catalog['skills'].items()):
        techs = [t for t in entry.get('tech', []) if t in DETECTABLE]
        if (entry.get('scope') == 'tech' and name not in skills and detected.intersection(techs) - covered
                and library.providers(f'skills/{name}/SKILL.md')):
            add.append(name)
            reasons[name] = f'technology detected, no project skill covers it: {", ".join(sorted(detected.intersection(techs) - covered))}'
    return {'project': project_name(root, manifest) or f'{Path(root).parent.name}/{Path(root).name}',
            'orgs': orgs, 'tech': sorted(detected), 'add': add, 'remove': remove, 'reasons': reasons}


def _cmd_suggest(args):
    try:
        root, manifest, library = load_layer_project(args.path, args.layers)
        report = suggest(root, manifest, library)
        code = 0
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError, subprocess.SubprocessError) as error:
        report, code = {'status': 'BLOCKED', 'reason': str(error)}, 2
    print(json.dumps(redact(report), ensure_ascii=False, indent=2))
    return code


SKILL_STATUSES = ('active', 'companion', 'suggested', 'available')
LIST_SCHEMA = 1


def _tags():
    tags = read_yaml(config_file()).get('tags', {})
    return tags if isinstance(tags, dict) else {}


def _skill_origin(name, specs, tags, profile_skills, available):
    """Explain why an already expanded skill is part of the project (membership comes from expand_project_skills)."""
    if name in specs:
        return 'manifest'
    if name == WORKFLOW_SKILL and LEGACY_WORKFLOW_SKILL in specs:
        return 'alias'
    for spec in specs:
        if spec.startswith('@') and name in tags.get(spec[1:], []):
            return 'tag'
        if ('*' in spec or '?' in spec) and fnmatch.fnmatch(name, spec):
            return 'wildcard'
    if name in profile_skills:
        return 'profile'
    return 'companion'


def _skill_references(library, name):
    """`references/**` of the skill across layers (per file, later wins; links skipped, D73)."""
    try:
        files = require_layerset(library, 'skill references').skill_files(name)
    except _layers.LayerError:  # a directory name that is not a valid skill name
        return []
    return sorted(str(Path(rel)) for rel in files if rel.startswith('references/'))[:100]


def skills_list(root, manifest, library, statuses=SKILL_STATUSES):
    """Read-only per-project skill view: membership from expand_project_skills, metadata from the library catalog."""
    from .maintenance import resolve_profile_skills
    wanted = tuple(statuses)
    if not wanted or any(item not in SKILL_STATUSES for item in wanted):
        raise ValueError('status must be a comma list of: ' + ', '.join(SKILL_STATUSES))
    catalog = load_catalog(library) or {'orgs': {}, 'skills': {}}
    expanded = expand_project_skills(manifest, library)
    specs = strings(manifest.get('skills', []), 'skills')
    profile_skills = resolve_profile_skills(manifest, library)
    tags = _tags()
    names = library_skill_names(library)
    profiles = library.merged_file('workflow/skill-profiles.yaml').data.get('profiles', {})
    hint = suggest(root, manifest, library) if catalog['skills'] else {'add': [], 'remove': [], 'reasons': {}}
    suggested = [name for name in hint['add'] if name in names and name not in expanded]
    rows = []
    for name in sorted(set(names) | set(expanded)):
        if name in expanded:
            origin = _skill_origin(name, specs, tags, profile_skills, names)
            status = 'companion' if origin == 'companion' else 'active'
        elif name in suggested:
            origin, status = None, 'suggested'
        else:
            origin, status = None, 'available'
        if status not in wanted:
            continue
        entry = catalog['skills'].get(name, {})
        description = skill_description(library, name)
        findings = []
        if name not in names:
            findings.append('Skill is missing from the library')
        if len(description.encode('utf-8')) > 200:
            findings.append('Description exceeds the 200 B catalog budget')
        if name in hint['remove']:
            findings.append(hint['reasons'].get(name, 'Catalog suggests removing this skill'))
        if status == 'suggested':
            findings.append('Suggested: ' + hint['reasons'].get(name, 'catalog match'))
        rows.append({
            'name': name, 'status': status, 'origin': origin, 'scope': entry.get('scope'), 'org': entry.get('org'),
            'tech': list(entry.get('tech', [])),
            'profiles': sorted(profile for profile, members in profiles.items() if isinstance(members, list) and name in members),
            'description': description, 'descriptionBytes': len(description.encode('utf-8')),
            'path': f'skills/{name}/SKILL.md', 'references': _skill_references(library, name), 'findings': findings})
    return {'schema': LIST_SCHEMA, 'status': 'PASS', 'project': project_name(root, manifest) or f'{Path(root).parent.name}/{Path(root).name}',
            'root': str(Path(root).resolve()), 'skills': rows}


def _cmd_skills_list(args):
    try:
        root, manifest, library = load_layer_project(args.path, getattr(args, 'layers', None))
        statuses = [item for item in args.status.split(',') if item] if args.status else SKILL_STATUSES
        report, code = skills_list(root, manifest, library, statuses), 0
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError, subprocess.SubprocessError) as error:
        report, code = {'schema': LIST_SCHEMA, 'status': 'BLOCKED', 'reason': ' '.join(str(error).split())[:500]}, 2
    print(json.dumps(redact(report), ensure_ascii=False, indent=2))
    return code


def register(subparsers):
    parser = subparsers.add_parser('suggest', help='Suggest skills to add/remove from the skill catalog (read-only)')
    add_project_arguments(parser)
    parser.set_defaults(func=_cmd_suggest)
