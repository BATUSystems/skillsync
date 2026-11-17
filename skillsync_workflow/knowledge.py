"""`skillsync knowledge init`: create the missing documentation skeleton (never overwrites)."""
import json
import os
from pathlib import Path
import subprocess
import yaml
from .common import add_project_arguments, load_layer_project, redact
from .pathfmt import posix
from .quality import knowledge_is_listed, knowledge_locations

README = """# Dokumentacja projektu

Mapa dla ludzi i agentów. Plik utworzony przez `skillsync knowledge init`; po utworzeniu jest własnością projektu (generator go nie nadpisuje).

## Wiedza projektu

- `domain/` — reguły biznesowe i wiedza domenowa zweryfikowana w kodzie lub danych (indeks: `domain/index.yaml`). Czytaj przed zmianą logiki biznesowej.
- `lessons/` — pułapki techniczne w formie Objaw → Przyczyna → Reguła → Jak wykryć (indeks: `lessons/index.yaml`). Czytają je przede wszystkim podumysły przed implementacją i review.
- Zasady zapisu i format rekordów: skill `workflow`, `references/knowledge.md` oraz `references/lessons.md`. Nowy rekord wymaga wpisu w indeksie.
- `skillsync route <projekt> --files ...` zwraca rekordy pasujące do zmienianych plików.

## Pliki dokumentacji (do głębokości 2)

{listing}
"""


def _listing(docs):
    items = []
    if docs.is_dir():
        for path in sorted(docs.rglob('*.md')):
            relative = path.relative_to(docs)
            if len(relative.parts) <= 2 and relative.as_posix() != 'README.md':
                items.append(f'- [{relative.as_posix()}]({relative.as_posix()})')
    return '\n'.join(items) if items else '_(brak innych plików; dopisz je tutaj, gdy powstaną)_'


OWNED_ELSEWHERE = 'owned by another repository; run knowledge init there'


def init_knowledge(root, manifest, library, dry_run=False):
    """Create the missing skeleton. The string form keeps writing to its one location (also a shared parent
    directory); the list form writes only inside the project root and reports the other directories as `skipped`."""
    project = Path(root).resolve()
    plan, skipped, readme_docs = [], [], None
    for kind, content in (('domain', {'version': 1, 'entries': []}), ('lessons', {'version': 1, 'lessons': []})):
        listed = knowledge_is_listed(root, manifest, library, kind)
        for directory, _ in knowledge_locations(root, manifest, library, kind):
            if listed and not (directory.is_relative_to(project) and directory.resolve().is_relative_to(project)):
                skipped.append({'path': posix(directory / 'index.yaml'), 'reason': OWNED_ELSEWHERE})
                continue
            plan.append((directory / 'index.yaml', yaml.safe_dump(content, sort_keys=False)))
            if kind == 'domain' and readme_docs is None:
                readme_docs = directory.parent
    if readme_docs is not None:
        plan.append((readme_docs / 'README.md', None))
    created, existing = [], []
    for path, content in plan:
        if path.exists() or path.is_symlink():
            existing.append(posix(path))
            continue
        created.append(posix(path))
        if dry_run:
            continue
        if content is None:
            content = README.format(listing=_listing(readme_docs))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8', newline='\n') as handle:
            handle.write(content)
    return {'status': 'PASS', 'dry_run': dry_run, 'created': created, 'existing': existing, 'skipped': skipped}


def _cmd(args):
    try:
        root, manifest, library = load_layer_project(args.path, args.layers)
        report, code = init_knowledge(root, manifest, library, args.dry_run), 0
    except (OSError, RuntimeError, ValueError, TypeError, yaml.YAMLError, subprocess.SubprocessError) as error:
        report, code = {'status': 'BLOCKED', 'reason': str(error)}, 2
    print(json.dumps(redact(report), ensure_ascii=False, indent=2))
    return code


def register(subparsers):
    parser = subparsers.add_parser('knowledge', help='Project documentation skeleton (docs/domain, docs/lessons, docs/README.md)')
    actions = parser.add_subparsers(dest='knowledge_command', required=True)
    init = actions.add_parser('init', help='Create missing index files and docs/README.md (never overwrites)')
    add_project_arguments(init)
    init.add_argument('--dry-run', action='store_true', help='Report what would be created without writing')
    init.set_defaults(func=_cmd)
