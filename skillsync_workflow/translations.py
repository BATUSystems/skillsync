"""Translation layers (`layer.yaml`: `kind: org`, `translation_of: core`, `language`): conformance with the core and the lock file (CORE-1130-I18N).

A translation layer replaces texts of the core per file (skills, roles, instructions). Because a missing file is taken from the core, a
careless translation would mix languages inside one skill, and a changed Polish source would silently outlive its translation. This module
keeps both in check: `conformance_findings` (used by `layers check`) and `update_lock` (`layers translations --update`).

Rules (every finding is an error, the layer is code that agents read):
- a skill present in the translation is complete: every `.md` file of the core skill is translated (`translation-incomplete`);
- nothing without a core counterpart (`translation-orphan`);
- the translated `instructions/_base.md` stays within the budget of the core one (`context.base_max_bytes`, `translation-base-too-large`);
- a translated file has the structure of its source: the same sequence of heading levels outside code fences and, for files with a
  frontmatter, the same keys and the same `name` (`translation-structure`);
- the lock file `translations.lock.yaml` records the sha256 of every translated core file (line ends normalized to LF); a core file
  that changed after the translation was made, or has no entry, is `translation-stale` unless the lock marks it with a reason under `stale`.
"""
import hashlib
import re
from pathlib import Path

import yaml

from . import layers

LOCK_FILE = 'translations.lock.yaml'
LOCK_SCHEMA = 1
_HEADING = re.compile(r'(#{1,6})[ \t]+\S')
_FENCE = re.compile(r'[ \t]{0,3}(`{3,}|~{3,})')
TRANSLATED_RULES = ('skill', 'agent', 'instruction')


def _is_translated_kind(rel):
    return rel.endswith('.md') and (rel == layers.BASE_FILE or layers.merge_rule(rel) in TRANSLATED_RULES)


def normalized_sha256(path):
    """sha256 of the file with CRLF and CR turned into LF (a checkout with `autocrlf` hashes like one without)."""
    data = Path(path).read_bytes().replace(b'\r\n', b'\n').replace(b'\r', b'\n')
    return hashlib.sha256(data).hexdigest()


def heading_levels(text):
    """Levels of the ATX headings of a Markdown text outside fenced code blocks, in order."""
    levels, fence = [], None
    for line in text.splitlines():
        marker = _FENCE.match(line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token[0] * len(token)
            elif token[0] == fence[0] and len(token) >= len(fence) and not line.strip()[len(token):].strip():
                fence = None
            continue
        if fence is None:
            heading = _HEADING.match(line)
            if heading:
                levels.append(len(heading.group(1)))
    return levels


def frontmatter(text):
    """The frontmatter mapping of a Markdown text, `{}` when there is none, `None` when it is not a valid mapping."""
    lines = text.lstrip('﻿').splitlines()
    if not lines or lines[0].strip() != '---':
        return {}
    for index in range(1, len(lines)):
        if lines[index].strip() == '---':
            try:
                data = yaml.safe_load('\n'.join(lines[1:index]))
            except yaml.YAMLError:
                return None
            return data if isinstance(data, dict) else None
    return None


def _read(path):
    return Path(path).read_text(encoding='utf-8', errors='replace')


def read_lock(layer):
    """`(lock dict, error)` of the lock file of a translation layer; `(None, None)` when absent."""
    path = layer.root / LOCK_FILE
    if not path.is_file():
        return None, None
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return None, f'{LOCK_FILE} cannot be read: {exc}'
    if not isinstance(data, dict) or data.get('schema') != LOCK_SCHEMA or not isinstance(data.get('files'), dict):
        return None, f'{LOCK_FILE} must be a mapping with `schema: {LOCK_SCHEMA}` and a `files` mapping'
    stale = data.get('stale') or {}
    if not isinstance(stale, dict) or not all(isinstance(key, str) and isinstance(value, str) and value.strip() for key, value in stale.items()):
        return None, f'{LOCK_FILE}: `stale` must map a path to a reason'
    return data, None


def translated_files(layer, core):
    """`{rel: core_rel}` of the files of the translation layer that have a counterpart in the core (path compared as is)."""
    core_files = set(layers.layer_files(core))
    return {rel: rel for rel in layers.layer_files(layer) if _is_translated_kind(rel) and rel in core_files}


def base_budget(core):
    """`context.base_max_bytes` of the core defaults (8192 when unreadable): the same budget as for the core `_base.md`."""
    try:
        data = yaml.safe_load((core.root / 'workflow/defaults.yaml').read_text(encoding='utf-8')) or {}
        value = data['context']['base_max_bytes']
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 8192
    except (OSError, UnicodeDecodeError, yaml.YAMLError, KeyError, TypeError):
        return 8192


def _finding(code, layer, message):
    return layers._finding(code, 'error', message, layer.id)


def layer_findings(layer, core):
    """Conformance findings of one translation layer against the core."""
    findings = []
    if not layer.language:
        findings.append(_finding('translation-language-missing', layer,
                                 f'Translation layer {layer.id}: layer.yaml has no `language`'))
    own = [rel for rel in layers.layer_files(layer) if _is_translated_kind(rel)]
    core_set = set(layers.layer_files(core))
    for rel in sorted(own):
        if rel not in core_set:
            findings.append(_finding('translation-orphan', layer, f'{rel} has no counterpart in the core layer'))
    base = layer.root / layers.BASE_FILE
    if base.is_file() and base.stat().st_size > base_budget(core):
        findings.append(_finding('translation-base-too-large', layer, f'{layers.BASE_FILE} has {base.stat().st_size} bytes, '
                                 f'above the budget of the base instructions ({base_budget(core)})'))
    skills = sorted({rel.split('/')[1] for rel in own if rel.startswith('skills/') and rel.count('/') >= 2})
    for name in skills:
        prefix = f'skills/{name}/'
        for rel in sorted(item for item in core_set if item.startswith(prefix) and _is_translated_kind(item)):
            if rel not in own:
                findings.append(_finding('translation-incomplete', layer,
                                         f'{rel} is not translated although the rest of skill {name} is'))
    pairs = translated_files(layer, core)
    for rel in sorted(pairs):
        source, target = _read(core.root / rel), _read(layer.root / rel)
        if heading_levels(source) != heading_levels(target):
            findings.append(_finding('translation-structure', layer, f'{rel}: the headings differ from the core file '
                                     f'({len(heading_levels(target))} against {len(heading_levels(source))}, or another order of levels)'))
        source_front, target_front = frontmatter(source), frontmatter(target)
        if source_front is None or target_front is None:
            findings.append(_finding('translation-structure', layer, f'{rel}: the frontmatter is not a valid mapping'))
        elif source_front or target_front:
            if set(source_front) != set(target_front) or source_front.get('name') != target_front.get('name'):
                findings.append(_finding('translation-structure', layer,
                                         f'{rel}: the frontmatter keys or `name` differ from the core file'))
    lock, error = read_lock(layer)
    if error:
        findings.append(_finding('translation-lock-invalid', layer, error))
        return findings
    if lock is None:
        if pairs:
            findings.append(_finding('translation-lock-missing', layer, f'{LOCK_FILE} is missing; run `skillsync layers translations --update`'))
        return findings
    recorded, stale = lock['files'], lock.get('stale') or {}
    for rel in sorted(pairs):
        if rel in stale:
            continue
        if rel not in recorded:
            findings.append(_finding('translation-stale', layer, f'{rel} has no entry in {LOCK_FILE}'))
        elif recorded[rel] != normalized_sha256(core.root / rel):
            findings.append(_finding('translation-stale', layer,
                                     f'{rel}: the core file changed after the translation was made (review it, then run `skillsync layers translations --update`)'))
    for rel in sorted(set(recorded) - set(pairs)):
        findings.append(_finding('translation-lock-extra', layer, f'{LOCK_FILE} lists {rel}, which is not translated'))
    return findings


def conformance_findings(layerset):
    """Findings of every translation layer of a LayerSet (none without one)."""
    core = layerset.core
    if core is None:
        return []
    findings = []
    for layer in layerset.layers:
        if layer.is_translation:
            findings.extend(layer_findings(layer, core))
    return findings


def update_lock(layer, core):
    """Write the lock of a translation layer from the current core files; returns `(path, changed relative paths)`.

    The `stale` marks are dropped: the command is what a reviewer runs after checking the translations."""
    pairs = translated_files(layer, core)
    files = {rel: normalized_sha256(core.root / rel) for rel in sorted(pairs)}
    old, _ = read_lock(layer)
    old_files = (old or {}).get('files') or {}
    changed = sorted(rel for rel in set(files) | set(old_files) if files.get(rel) != old_files.get(rel))
    path = layer.root / LOCK_FILE
    text = yaml.safe_dump({'schema': LOCK_SCHEMA, 'files': files}, sort_keys=True, allow_unicode=True)
    if not path.is_file() or path.read_text(encoding='utf-8') != text:
        path.write_text(text, encoding='utf-8', newline='\n')
    return path, changed
