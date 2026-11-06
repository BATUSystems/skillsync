"""Repository map: `skillsync map` writes `.skillsync/map.md` (generated, never edited by hand)."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import yaml
from .common import add_project_arguments, emit, load_layer_project, read_project, redact, workflow_config
from .storage import state_path

MAP_LIMIT = 12 * 1024
MAX_DEPTH = 3
PRUNE = {'node_modules', '.git', 'bin', 'obj', '.venv', '.skillsync', '__pycache__'}
HEADER = re.compile(r'<!-- skillsync:map fingerprint=([0-9a-f]{64}) -->')
TEST_NAMES = {'test', 'tests', '__tests__', 'spec', 'specs', 'e2e'}
MIGRATION_NAMES = {'migration', 'migrations'}
ENTRY_NAMES = {'package.json', 'manage.py', 'pyproject.toml', 'Cargo.toml'}


def _git(root, *args):
    process = subprocess.run(['git', '-C', str(root), *args], capture_output=True, timeout=60)
    return process.stdout if process.returncode == 0 else None


def tracked_files(root):
    """Return (sorted relative POSIX paths, head) from git, or a filesystem walk outside of git."""
    output = _git(root, 'ls-files', '-z')
    if output is not None:
        head = (_git(root, 'rev-parse', 'HEAD') or b'').decode().strip() or 'no-commits'
        files = sorted(os.fsdecode(item) for item in output.split(b'\0') if item)
        return [f for f in files if (Path(root) / f).exists()], head
    files = []
    for directory, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in PRUNE)
        for name in names:
            files.append((Path(directory) / name).relative_to(root).as_posix())
    return sorted(files), 'nogit'


def fingerprint(files, head):
    digest = hashlib.sha256()
    digest.update(head.encode() + b'\n')
    for item in files:
        digest.update(item.encode() + b'\n')
    return digest.hexdigest()


def _purpose(root, directory, files):
    prefix = directory + '/' if directory else ''
    for name in sorted(f for f in files if f.startswith(prefix) and '/' not in f[len(prefix):]):
        base = name[len(prefix):]
        if base.lower().startswith('readme') and base.lower().endswith('.md'):
            try:
                with (Path(root) / name).open(encoding='utf-8', errors='replace') as handle:
                    for line in handle.read(4096).splitlines():
                        line = re.sub(r'^[#>\s*_-]+', '', line.strip()).strip()
                        if line and not line.startswith(('<', '[!', '![')):
                            return line[:100]
            except OSError:
                return ''
    return ''


def _dir_counts(files):
    counts = {}
    for item in files:
        parts = item.split('/')[:-1]
        for depth in range(1, len(parts) + 1):
            key = '/'.join(parts[:depth])
            counts[key] = counts.get(key, 0) + 1
    return counts


def _tree(root, files, counts, depth_limit):
    lines = [f'- ./ — {len(files)} plików' + (f' — {_purpose(root, "", files)}' if _purpose(root, '', files) else '')]
    for directory in sorted(counts, key=lambda path: tuple(path.split('/'))):
        depth = directory.count('/') + 1
        if depth > depth_limit:
            continue
        purpose = _purpose(root, directory, files)
        lines.append(f'{"  " * depth}- {directory.rsplit("/", 1)[-1]}/ — {counts[directory]} plików' + (f' — {purpose}' if purpose else ''))
    return lines


def _entry_points(root, files):
    lines = []
    for item in files:
        base = item.rsplit('/', 1)[-1]
        if base in ENTRY_NAMES or base.endswith(('.csproj', '.sln')):
            extra = ''
            if base == 'package.json':
                try:
                    scripts = json.loads((Path(root) / item).read_text(encoding='utf-8')).get('scripts', {})
                    if isinstance(scripts, dict) and scripts:
                        extra = ' — scripts: ' + ', '.join(list(scripts)[:12])
                except (OSError, ValueError, AttributeError):
                    pass
            lines.append(f'- `{item}`{extra}')
    return lines[:40]


def _commands(root, manifest, library):
    lines = []
    tests = manifest.get('tests', {})
    if isinstance(tests, dict) and tests.get('command'):
        lines.append(f'- tests: `{tests["command"]}`')
    try:
        checks = workflow_config(root, manifest, library).get('validation', {}).get('checks', [])
    except (ValueError, OSError, yaml.YAMLError):
        checks = []
    for check in checks:
        command = check.get('command') or ' '.join(str(x) for x in check.get('argv', []))
        if command:
            lines.append(f'- {check.get("id", "check")} ({check.get("kind", "check")}): `{command}`')
    return lines


def _named_dirs(counts, predicate):
    return [f'- `{d}/` — {n} plików' for d, n in sorted(counts.items()) if predicate(d.rsplit('/', 1)[-1].lower(), d)][:20]


def build_map(root, manifest, library):
    files, head = tracked_files(root)
    counts = _dir_counts(files)
    key = fingerprint(files, head)
    name = (manifest.get('project') or {}).get('name') if isinstance(manifest.get('project'), dict) else None
    sections = [
        ('Komendy build/test', _commands(root, manifest, library)),
        ('Entry pointy i manifesty', _entry_points(root, files)),
        ('Testy', _named_dirs(counts, lambda b, d: b in TEST_NAMES or b.endswith(('tests', '.tests')))),
        ('Migracje', _named_dirs(counts, lambda b, d: b in MIGRATION_NAMES)),
        ('Wiedza projektu', _named_dirs(counts, lambda b, d: d.endswith(('docs/domain', 'docs/lessons')))),
    ]
    head_lines = ['# Mapa repo', f'<!-- skillsync:map fingerprint={key} -->',
                  f'fingerprint: `{key}` (HEAD: `{head[:12]}`)',
                  'Plik generowany: `skillsync map`. Nie edytuj ręcznie; odtwarzaj po zmianie drzewa lub HEAD.', '',
                  f'- projekt: {name or Path(root).name}', '']
    body = []
    for title, lines in sections:
        if lines:
            body += [f'## {title}', ''] + lines + ['']
    truncated = False
    note = '\n\n_(obcięto do limitu 12 KB)_\n'
    for depth in range(MAX_DEPTH, 0, -1):
        tail = ['_(drzewo skrócone do głębokości %d: obcięto do limitu 12 KB)_' % depth, ''] if depth < MAX_DEPTH else []
        text = '\n'.join(head_lines + body + ['## Drzewo (głębokość ≤ %d, liczba plików śledzonych)' % depth, ''] + tail + _tree(root, files, counts, depth)) + '\n'
        truncated = depth < MAX_DEPTH
        if len(text.encode()) <= MAP_LIMIT:
            break
    if len(text.encode()) > MAP_LIMIT:
        truncated = True
        text = text.encode()[:MAP_LIMIT - len(note.encode()) - 4].decode('utf-8', errors='ignore').rstrip() + note
    return text, key, truncated


def _gitignored(root):
    process = subprocess.run(['git', '-C', str(root), 'check-ignore', '-q', '.skillsync/map.md'], capture_output=True)
    return process.returncode == 0


def _cmd(args):
    try:
        root, manifest = read_project(args.path)  # `--check` reads no layer
        path = state_path(root, 'map.md')
        if args.check:
            files, head = tracked_files(root)
            current = fingerprint(files, head)
            stored = HEADER.search(path.read_text(encoding='utf-8')) if path.is_file() else None
            if stored and stored.group(1) == current:
                report = {'status': 'PASS', 'path': str(path), 'fingerprint': current}
            else:
                report = {'status': 'FAIL', 'path': str(path), 'fingerprint': current,
                          'reason': 'Repository map is stale; run `skillsync map`' if stored else 'Repository map is missing; run `skillsync map`'}
        else:
            _, _, layerset = load_layer_project(args.path, args.layers)
            text, key, truncated = build_map(root, manifest, layerset)
            path = state_path(root, 'map.md', create=True)
            temporary = path.with_name('.map.md.tmp')
            temporary.write_text(text, encoding='utf-8', newline='\n')
            os.replace(temporary, path)
            report = {'status': 'PASS', 'path': str(path), 'fingerprint': key, 'bytes': len(text.encode()), 'truncated': truncated}
            if not _gitignored(root):
                report['note'] = 'Add `.skillsync/` to .gitignore: the map is generated'
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError, subprocess.SubprocessError) as error:
        report = {'status': 'BLOCKED', 'reason': str(error)}
    return emit(redact(report), args.json)


def register(subparsers):
    parser = subparsers.add_parser('map', help='Generate .skillsync/map.md (repository map)')
    add_project_arguments(parser)
    parser.add_argument('--check', action='store_true', help='Exit 1 when the map is missing or stale')
    parser.set_defaults(func=_cmd)
