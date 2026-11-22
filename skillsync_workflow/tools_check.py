"""Checks of the tool index against the files it points at (CORE-1140-SUP-TOOLS, TOOLS-3): `skillsync doctor` (project index) and `layers check` (layers).

Only warnings and only reading: a script of a record is never run (`selftest` is run by the project in its own checks), and a header is read as text from a
regular file (at most 64 KB, line by line, no backtracking patterns). A record whose `path` leaves the root, is a link or is not a regular file is reported
as `tools-index-invalid`; a script whose first comment block lacks labels is reported as `tool-header-incomplete`.
"""
import os
from pathlib import Path

from . import layers, tools_index
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


def _block(rest, index, extension):
    """`(lines, next index)` of the comment segment that starts at `rest[index]`, or `(None, index)` when it is not a comment."""
    line = rest[index].strip()
    if extension in HASH_EXTENSIONS:
        if extension == '.ps1' and line.startswith('<#'):
            block = []
            for number in range(index, len(rest)):
                text = rest[number].strip()
                block.append((text.removeprefix('<#') if number == index else text).removesuffix('#>').strip())
                if '#>' in text:
                    return block, number + 1
            return block, len(rest)
        if line.startswith('#'):
            block = []
            while index < len(rest) and rest[index].strip().startswith('#'):
                block.append(rest[index].strip().lstrip('#').strip())
                index += 1
            return block, index
        if extension == '.py':
            text = line.lstrip('rRuU') if line[:1] in 'rRuU' else line
            quote = text[:3]
            if quote in ('"""', "\'\'\'"):
                body = text[3:]
                if quote in body:
                    return [body.split(quote)[0].strip()], index + 1
                block = [body.strip()]
                for number in range(index + 1, len(rest)):
                    if quote in rest[number]:
                        block.append(rest[number].split(quote)[0].strip())
                        return block, number + 1
                    block.append(rest[number].strip())
                return block, len(rest)
        return None, index
    if line.startswith('/*'):
        block = []
        for number in range(index, len(rest)):
            text = rest[number].strip()
            block.append((text.removeprefix('/*') if number == index else text).lstrip('*').removesuffix('*/').rstrip('*').strip())
            if '*/' in text:
                return block, number + 1
        return block, len(rest)
    if line.startswith('//'):
        block = []
        while index < len(rest) and rest[index].strip().startswith('//'):
            block.append(rest[index].strip().lstrip('/').strip())
            index += 1
        return block, index
    return None, index


def _comment_block(lines, extension):
    """The comment lines at the start of a script (marks removed): the segments that follow each other from the top, after blank lines, a shebang and
    in js a `'use strict'` directive. So `# -*- coding` or `#Requires` before a docstring or a `<# #>` block do not hide the header."""
    rest = [line.lstrip('\ufeff') for line in lines]
    index, block = 0, []
    while index < len(rest):
        text = rest[index].strip()
        if not text or text.startswith('#!') or (extension in SLASH_EXTENSIONS and text.strip('\'";') == 'use strict'):
            index += 1
            continue
        segment, following = _block(rest, index, extension)
        if segment is None:
            break
        block.extend(segment)
        index = following
    return block


def missing_labels(path):
    """The header labels missing in the first comment block of the script at `path`; None for an extension that is not checked or an unreadable file."""
    extension = Path(path).suffix.lower()
    if extension not in HASH_EXTENSIONS + SLASH_EXTENSIONS:
        return None
    try:
        with open(path, 'rb') as handle:
            text = handle.read(HEADER_BYTES).decode('utf-8-sig', 'replace')
    except OSError:
        return None
    block = [line.lower() for line in _comment_block(text.splitlines(), extension)]
    return [label for label in LABELS if not any(line.startswith(label.lower() + ':') for line in block)]


def check_records(root, records, source, *, strict_links):
    """`[(code, message)]` for the records of one index (`records`: normalized, from `tools_index`); `source` names the file in the messages."""
    findings = []
    for record in records:
        if record['status'] in ('removed', 'superseded'):
            continue   # a tombstone may outlive its script
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
    probe = root
    for part in Path(tools_index.PROJECT_INDEX_PATH).parts[:-1]:
        probe = probe / part
        if layers.is_link(probe):
            return [('tools-index-invalid', f'{tools_index.PROJECT_INDEX_PATH}: {part} is a link; the index is not read')]
    records, errors = tools_index.read_index_file(path, tools_index.PROJECT_INDEX_PATH)
    return [('tools-index-invalid', message) for message in errors] + check_records(root, records, tools_index.PROJECT_INDEX_PATH, strict_links=False)


def layer_findings(layer):
    """`[(code, message)]` for `tools/index.yaml` of a layer (parse problems are reported by the merge; here the records against the files)."""
    root = Path(layer.root)
    path = root / tools_index.INDEX_PATH
    try:
        if not layer.root_clean or not layers._clean(path, root) or not path.is_file():   # a link anywhere on the way: the merge reports it, nothing is read
            return []
    except OSError:
        return []
    records, _ = tools_index.read_index_file(path, f'{layer.id}:{tools_index.INDEX_PATH}')
    return check_records(root, records, f'{layer.id}:{tools_index.INDEX_PATH}', strict_links=True)
