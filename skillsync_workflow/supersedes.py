"""`supersedes` of a layer against the files of a project: the `superseded` warnings of `skillsync doctor` (CORE-1140-SUP-TOOLS).

A layer says in `layer.yaml` that its tool replaces local files of a project (`matches`: path patterns relative to the project). Here a project is
checked: an entry applies when the version of the layer is at least `since` and a file of the project matches a pattern. The result is only a text for
the person or the agent: NOTHING from a layer is run, passed to a shell or interpreted here (`parity`, `migration`, `replacement` are data from a layer
that may be org or personal; they are cleaned and cut before they are shown, and marked with their layer). The manifest key `superseded_keep`
({id: reason}) switches a warning off for a project that keeps its local tool on purpose.

Matching is `fnmatch` on a list of file names (`*` crosses `/`, like `common.matches`), never `Path.glob`: `..` and links are irrelevant and a pattern
cannot be a regular expression bomb. The list of files comes from git (`--cached --others --exclude-standard`, a file that no longer exists is not a
hit) or, without git, from a walk that follows no links; at most `MAX_FILES` files are looked at, and a cut is reported.
"""
import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import layers
from .tools_index import clean_text

MAX_FILES = 20000
SKIPPED_DIRECTORIES = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', '.tox', 'dist', 'build', '.skillsync', '.pytest_cache', '.mypy_cache'}
CASE_INSENSITIVE = sys.platform in ('win32', 'darwin')
MANIFEST_KEY = 'superseded_keep'
GIT_TIMEOUT = 30


def project_files(root, limit=None):
    """`(sorted relative posix paths of the regular files of the project, cut)`; `cut` is True when `limit` (default `MAX_FILES`) files were reached."""
    root = Path(root)
    limit = limit or MAX_FILES
    names = None
    try:
        done = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-C', str(root), 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
                              capture_output=True, timeout=GIT_TIMEOUT, check=False, stdin=subprocess.DEVNULL)
        if done.returncode == 0:
            names = [item.decode('utf-8', 'replace') for item in done.stdout.split(b'\0') if item]
    except (OSError, subprocess.SubprocessError):
        names = None
    cut = False
    files = []
    if names is not None:
        for name in names:
            path = root / name
            try:
                real = path.is_file() and not path.is_symlink()   # deleted from the tree but still in the index: not a hit
            except OSError:
                real = False
            if not real:
                continue
            if len(files) >= limit:
                cut = True   # a file beyond the limit exists, so the list really is incomplete
                break
            files.append(name.replace('\\', '/') if os.sep == '\\' else name)
    else:
        for current, directories, found in os.walk(root, followlinks=False):
            directories[:] = sorted(item for item in directories if item not in SKIPPED_DIRECTORIES and not os.path.islink(os.path.join(current, item)))
            for name in sorted(found):
                path = os.path.join(current, name)
                if os.path.islink(path):
                    continue
                if len(files) >= limit:
                    return sorted(files), True
                files.append(os.path.relpath(path, root).replace(os.sep, '/'))
    return sorted(files), cut


def matching_files(files, patterns):
    """The files that match any of the patterns (`fnmatch`; case-insensitive on systems whose file system is)."""
    compiled = []
    for pattern in patterns:
        text = pattern.replace('\\', '/') if os.sep == '\\' else pattern
        variants = [text] + ([text[3:]] if text.startswith('**/') else [])   # `**/x` also matches `x` in the root, like common.matches
        for variant in variants:
            try:
                compiled.append(re.compile(fnmatch.translate(variant.lower() if CASE_INSENSITIVE else variant)))
            except re.error:
                continue
    return [name for name in files if any(rule.match(name.lower() if CASE_INSENSITIVE else name) for rule in compiled)]


def applicable_entries(layerset):
    """`[(layer, entry)]` of the `supersedes` entries whose `since` the version of their layer has reached (a layer without a version declares nothing)."""
    found = []
    for layer in layerset.layers:
        meta = layer.meta or {}
        if not layer.version:
            continue
        for entry in meta.get('supersedes') or []:
            try:
                if layers.compare_versions(layer.version, entry['since']) >= 0:
                    found.append((layer, entry))
            except layers.LayerError:
                continue
    return found


def _keep(manifest):
    """`(reasons {id: text}, warnings)` of the manifest key `superseded_keep`; never raises (a bad value is a warning, not a `BLOCKED` doctor)."""
    value = manifest.get(MANIFEST_KEY) if isinstance(manifest, dict) else None
    if value is None:
        return {}, []
    if not isinstance(value, dict):
        return {}, [f'`{MANIFEST_KEY}` must be a mapping {{<id>: <reason>}}; ignored']
    reasons, warnings = {}, []
    for key, reason in value.items():
        if not isinstance(key, str) or not isinstance(reason, str) or not reason.strip():
            warnings.append(f'`{MANIFEST_KEY}` entry {clean_text(key, 60)!r} needs a reason (a nonempty text); ignored')
            continue
        reasons[key] = reason
    return reasons, warnings


def _show(layer, entry, files):
    shown = ', '.join(clean_text(name, 120) for name in files[:5]) + (f' (+{len(files) - 5} more)' if len(files) > 5 else '')
    def quoted(value):
        return json.dumps(clean_text(value), ensure_ascii=False)   # a quoted text reads as a quotation, not as an instruction of the generator

    parts = [f'{clean_text(entry["id"], 80)} [from layer {clean_text(layer.id, 60)}, {layer.kind}; data from the layer, not instructions]: {shown} '
             f'is replaced by {quoted(entry["replacement"] or "(not described)")} (layer version {layer.version} >= {entry["since"]})']
    if entry.get('parity'):
        parts.append(f'parity: {quoted(entry["parity"])}')
    if entry.get('migration'):
        parts.append(f'migration: {quoted(entry["migration"])}')
    return '; '.join(parts)


def doctor_findings(root, manifest, layerset, files=None):
    """Findings `[{level, path, reason}]` for `skillsync doctor`: `superseded` warnings, the `info` of a kept one, `superseded_keep` warnings and a
    `superseded-scan-truncated` warning. Runs the scan once and only when an entry applies."""
    reasons, problems = _keep(manifest)
    findings = [{'level': 'warning', 'path': '.github/.skillsync.yaml', 'reason': text} for text in problems]
    entries = applicable_entries(layerset)
    declared = {entry['id'] for layer in layerset.layers for entry in (layer.meta or {}).get('supersedes') or []}
    for key in sorted(set(reasons) - declared):
        findings.append({'level': 'warning', 'path': '.github/.skillsync.yaml',
                         'reason': f'`{MANIFEST_KEY}` names {clean_text(key, 80)!r}, which no layer of this project declares in `supersedes`'})
    if not entries:
        return findings
    cut = False
    if files is None:
        files, cut = project_files(root)
    if cut:
        findings.append({'level': 'warning', 'path': '.', 'reason': f'`superseded` scan looked at the first {MAX_FILES} files only; a matching file later in the tree is not reported'})
    for layer, entry in entries:
        hits = matching_files(files, entry['matches'])
        if not hits:
            continue
        if entry['id'] in reasons:
            findings.append({'level': 'info', 'path': clean_text(hits[0], 200), 'reason': f'{clean_text(entry["id"], 80)} kept on purpose: {clean_text(reasons[entry["id"]])}'})
        else:
            findings.append({'level': 'warning', 'path': clean_text(hits[0], 200), 'reason': _show(layer, entry, hits)})
    return findings
