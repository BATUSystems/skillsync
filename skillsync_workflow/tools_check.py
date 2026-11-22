"""Checks of the tool index against the files it points at (CORE-1140-SUP-TOOLS, TOOLS-3): `skillsync doctor` (project index) and `layers check` (layers).

Only warnings and only reading: a script of a record is never run (`selftest` is run by the project in its own checks), and a header is read as text from a
regular file (at most 64 KB, line by line, no backtracking patterns). A record whose `path` leaves the root, is a link or is not a regular file is reported
as `tools-index-invalid`; a script whose first comment block lacks labels is reported as `tool-header-incomplete`.
"""
import os
from pathlib import Path

from . import tools_index
from .tools_index import clean_text

HEADER_BYTES = 64 * 1024
LABELS = ('Purpose', 'Usage', 'Inputs', 'Outputs', 'Exit codes', 'Dependencies', 'Limits', 'Security')
HASH_EXTENSIONS = ('.py', '.sh', '.ps1')
SLASH_EXTENSIONS = ('.js', '.mjs', '.cjs')


def _is_link(path):
    try:
        return path.is_symlink() or bool(os.name == 'nt' and getattr(path, 'is_junction', lambda: False)())
    except OSError:
        return True


def _plain_path(root, rel, *, strict_links):
    """`(path, problem)`: the file `rel` under `root`. `strict_links`: no entry on the way may be a link (layers, as D73); otherwise the resolved
    path must stay under the root (projects). A problem is a short text."""
    root = Path(root)
    if not tools_index.safe_relative(rel):
        return None, 'the path leaves the root or is not relative'
    current = root
    for part in rel.replace('\\', '/').split('/'):
        current = current / part
        if strict_links and _is_link(current):
            return None, 'a link on the way is never followed'
    try:
        if not strict_links and not current.resolve().is_relative_to(root.resolve()):
            return None, 'the path resolves outside the root'
        if not current.exists():
            return None, 'the file does not exist'
        if not current.is_file():
            return None, 'not a regular file'
    except (OSError, RuntimeError):
        return None, 'the file cannot be read'
    return current, None


def _comment_block(lines, extension):
    """The first comment block of a script as a list of text lines (comment marks removed)."""
    index = 0
    while index < len(lines) and (not lines[index].strip() or lines[index].startswith('#!') or lines[index].lstrip('﻿').startswith('#!')):
        index += 1
    block = []
    rest = lines[index:]
    if not rest:
        return block
    first = rest[0].strip()
    if extension in HASH_EXTENSIONS:
        if extension == '.ps1' and first.startswith('<#'):
            for line in rest:
                block.append(line.strip().lstrip('<#').rstrip('#>').strip())
                if '#>' in line:
                    break
            return block
        if extension == '.py' and first[:3] in ('"""', "'''"):
            quote = first[:3]
            body = rest[0].strip()[3:]
            if quote in body:
                return [body.split(quote)[0]]
            block.append(body)
            for line in rest[1:]:
                if quote in line:
                    block.append(line.split(quote)[0])
                    break
                block.append(line.strip())
            return block
        for line in rest:
            if not line.lstrip().startswith('#'):
                break
            block.append(line.strip().lstrip('#').strip())
        return block
    if first.startswith('/*'):
        for line in rest:
            block.append(line.strip().lstrip('/').lstrip('*').strip().rstrip('/').rstrip('*').strip())
            if '*/' in line:
                break
        return block
    for line in rest:
        if not line.lstrip().startswith('//'):
            break
        block.append(line.strip().lstrip('/').strip())
    return block


def missing_labels(path):
    """The header labels missing in the first comment block of the script at `path`; None for an extension that is not checked or an unreadable file."""
    extension = Path(path).suffix.lower()
    if extension not in HASH_EXTENSIONS + SLASH_EXTENSIONS:
        return None
    try:
        with open(path, 'rb') as handle:
            text = handle.read(HEADER_BYTES).decode('utf-8', 'replace')
    except OSError:
        return None
    block = [line.lower() for line in _comment_block(text.splitlines(), extension)]
    return [label for label in LABELS if not any(line.startswith(label.lower() + ':') for line in block)]


def check_records(root, records, source, *, strict_links):
    """`[(code, message)]` for the records of one index (`records`: normalized, from `tools_index`); `source` names the file in the messages."""
    findings = []
    for record in records:
        path, problem = _plain_path(root, record['path'], strict_links=strict_links)
        label = f'{source}: tool {clean_text(record["id"], 80)}'
        if problem:
            findings.append(('tools-index-invalid', f'{label}: `path` {clean_text(record["path"], 120)}: {problem}'))
            continue
        missing = missing_labels(path)
        if missing:
            findings.append(('tool-header-incomplete', f'{label}: the header of {clean_text(record["path"], 120)} lacks {", ".join(missing)}'))
    return findings


def project_findings(root):
    """`[(code, message)]` for `docs/tools/index.yaml` of a project (none when the file is absent)."""
    root = Path(root)
    path = root / tools_index.PROJECT_INDEX_PATH
    try:
        if not (path.is_symlink() or path.exists()):
            return []
    except OSError:
        return []
    records, errors = tools_index.read_index_file(path, tools_index.PROJECT_INDEX_PATH)
    return [('tools-index-invalid', message) for message in errors] + check_records(root, records, tools_index.PROJECT_INDEX_PATH, strict_links=False)


def layer_findings(layer):
    """`[(code, message)]` for `tools/index.yaml` of a layer (parse problems are reported by the merge; here the records against the files)."""
    root = Path(layer.root)
    path = root / tools_index.INDEX_PATH
    try:
        if _is_link(path) or not path.is_file():
            return []
    except OSError:
        return []
    records, _ = tools_index.read_index_file(path, f'{layer.id}:{tools_index.INDEX_PATH}')
    return check_records(root, records, f'{layer.id}:{tools_index.INDEX_PATH}', strict_links=True)
