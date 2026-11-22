"""Translation layers (`layer.yaml`: `kind: org`, `translation_of: core`, `language`): conformance with the core and the lock file (CORE-1130-I18N).

A translation layer replaces texts of the core per file (skills, roles, instructions). Because a missing file is taken from the core, a
careless translation would mix languages inside one skill, and a changed Polish source would silently outlive its translation. This module
keeps both in check: `conformance_findings` (used by `layers check`) and `update_lock` (`layers translations --update`).

The layer is a translation only with the consent of the machine config (`translation: true` in its `layers` entry, `layers add --translation`);
the declaration in its own `layer.yaml` is a claim, so a layer that declares itself without the consent is `translation-not-consented` and gets the
ordinary trust model, and the reverse is `translation-not-declared`.

Rules (every finding is an error, the layer is code that agents read):
- a skill present in the translation is complete: every `.md` file of the core skill is translated (`translation-incomplete`);
- nothing without a core counterpart among the files of skills, roles and instructions (`translation-orphan`), and a path that differs from
  the core one only in letter case is `translation-case-mismatch` (case-insensitive file systems would replace the core file);
- the translated `instructions/_base.md` stays within the budget of the core one (`context.base_max_bytes`, `translation-base-too-large`);
- a translated file has the structure of its source: the same sequence of heading levels outside code fences (ATX headings at column 0;
  setext headings are not recognised) and, for files with a frontmatter, the same keys and the same value of every key except the ones that
  carry words (`TRANSLATED_KEYS`): tools, models, hooks and the like are behaviour, not text (`translation-structure`). The body is not compared
  beyond the headings: a translation is code that agents read, which is why the consent exists;
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
TRANSLATED_KEYS = ('description', 'argument-hint', 'when_to_use', 'title')   # frontmatter keys whose value is text and may differ


def _fold(rel):
    return layers._fold_rel(rel)


def _is_translated_kind(rel):
    """Files the trust model lets a translation replace: Markdown of skills, roles and instructions (classified on the case-folded path)."""
    folded = _fold(rel)
    return folded.endswith('.md') and (folded == layers.BASE_FILE or layers.merge_rule(folded) in TRANSLATED_RULES)


def _is_language_file(rel):
    """Files of the kinds a translation covers, of any extension (a new script in a translated skill is an orphan)."""
    folded = _fold(rel)
    return folded == layers.BASE_FILE or layers.merge_rule(folded) in TRANSLATED_RULES


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
    if path.is_symlink() or not layers._clean(path, layer.root):
        return None, f'{LOCK_FILE} is a symlink or lies behind one; links in layers are never read'
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
    """`{rel: core_rel}` of the translated files of the layer that have a counterpart in the core (paths compared case-insensitively, as the
    trust model does)."""
    core_files = {_fold(rel): rel for rel in layers.layer_files(core)}
    return {rel: core_files[_fold(rel)] for rel in layers.layer_files(layer) if _is_translated_kind(rel) and _fold(rel) in core_files}


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


def _frontmatter_differs(source, target):
    """True when the keys differ or a key outside `TRANSLATED_KEYS` has another value (tools, models, hooks are behaviour)."""
    if set(source) != set(target):
        return True
    return any(source[key] != target[key] for key in source if key not in TRANSLATED_KEYS)


def layer_findings(layer, core):
    """Conformance findings of one translation layer against the core."""
    findings = []
    if not layer.language:
        findings.append(_finding('translation-language-missing', layer,
                                 f'Translation layer {layer.id}: layer.yaml has no `language`'))
    core_files = {_fold(rel): rel for rel in layers.layer_files(core)}
    own_files = layers.layer_files(layer)
    own = [rel for rel in own_files if _is_translated_kind(rel)]
    own_folded = {_fold(rel) for rel in own}
    for rel in sorted(own_files):
        if not _is_language_file(rel):
            continue
        core_rel = core_files.get(_fold(rel))
        if core_rel is None:
            findings.append(_finding('translation-orphan', layer, f'{rel} has no counterpart in the core layer'))
        elif core_rel != rel:
            findings.append(_finding('translation-case-mismatch', layer, f'{rel} differs from the core path {core_rel} only in letter case'))
    base = layer.root / layers.BASE_FILE
    if base.is_file() and base.stat().st_size > base_budget(core):
        findings.append(_finding('translation-base-too-large', layer, f'{layers.BASE_FILE} has {base.stat().st_size} bytes, '
                                 f'above the budget of the base instructions ({base_budget(core)})'))
    skills = sorted({_fold(rel).split('/')[1] for rel in own if _fold(rel).startswith('skills/') and _fold(rel).count('/') >= 2})
    for name in skills:
        prefix = f'skills/{name}/'
        for folded, core_rel in sorted(core_files.items()):
            if folded.startswith(prefix) and _is_translated_kind(core_rel) and folded not in own_folded:
                findings.append(_finding('translation-incomplete', layer,
                                         f'{core_rel} is not translated although the rest of skill {name} is'))
    pairs = translated_files(layer, core)
    for rel in sorted(pairs):
        source, target = _read(core.root / pairs[rel]), _read(layer.root / rel)
        if heading_levels(source) != heading_levels(target):
            findings.append(_finding('translation-structure', layer, f'{rel}: the headings differ from the core file '
                                     f'({len(heading_levels(target))} against {len(heading_levels(source))}, or another order of levels)'))
        source_front, target_front = frontmatter(source), frontmatter(target)
        if source_front is None or target_front is None:
            side = 'the core file' if source_front is None else 'the translation'
            findings.append(_finding('translation-structure', layer, f'{rel}: the frontmatter of {side} is not a valid mapping'))
        elif (source_front or target_front) and _frontmatter_differs(source_front, target_front):
            findings.append(_finding('translation-structure', layer, f'{rel}: the frontmatter keys, or the value of a key other than '
                                     f'{", ".join(TRANSLATED_KEYS)}, differ from the core file'))
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
        elif recorded[rel] != normalized_sha256(core.root / pairs[rel]):
            findings.append(_finding('translation-stale', layer,
                                     f'{rel}: the core file changed after the translation was made (review it, then run `skillsync layers translations --update`)'))
    for rel in sorted((set(recorded) | set(stale)) - set(pairs)):
        findings.append(_finding('translation-lock-extra', layer, f'{LOCK_FILE} lists {rel}, which is not translated'))
    return findings


def consent_findings(layerset):
    """The declaration in `layer.yaml` and the consent of the machine config must agree: a layer that declares `translation_of: core`
    without `translation: true` is treated as an ordinary org layer (and reported), a consented layer that does not declare is reported too."""
    findings = []
    for layer in layerset.layers:
        if layer.kind != 'org':
            continue
        declared = (layer.meta or {}).get('translation_of') == 'core'
        if declared and not layer.translation:
            findings.append(_finding('translation-not-consented', layer,
                                     f'Layer {layer.id} declares `translation_of: core` but the machine config does not consent '
                                     f'(`translation: true` in its `layers` entry, `layers add --translation`); the trust model applies as to any org layer'))
        elif layer.translation and layer.meta is not None and not declared:
            findings.append(_finding('translation-not-declared', layer,
                                     f'The machine config marks layer {layer.id} as a translation but its layer.yaml has no `translation_of: core`'))
    return findings


def conformance_findings(layerset):
    """Findings of every translation layer of a LayerSet (the consent findings need no core)."""
    findings = consent_findings(layerset)
    core = layerset.core
    if core is None:
        return findings
    for layer in layerset.layers:
        if layer.is_translation:
            findings.extend(layer_findings(layer, core))
    return findings


def update_lock(layer, core):
    """Write the lock of a translation layer from the current core files; returns `(path, changed relative paths)`.

    The sha256 of the CURRENT core file is recorded for every translated file and the `stale` marks are dropped: the command is what a
    reviewer runs after checking the translations, it does not check them. A lock that is a link (or behind one) is refused."""
    path = layer.root / LOCK_FILE
    if path.is_symlink() or not layers._clean(path, layer.root):
        raise layers.LayerError(f'{LOCK_FILE} of layer {layer.id} is a symlink or lies behind one; links in layers are never written', 'translation-lock-link')
    pairs = translated_files(layer, core)
    files = {rel: normalized_sha256(core.root / pairs[rel]) for rel in sorted(pairs)}
    old, _ = read_lock(layer)
    old_files = (old or {}).get('files') or {}
    changed = sorted(rel for rel in set(files) | set(old_files) if files.get(rel) != old_files.get(rel))
    text = yaml.safe_dump({'schema': LOCK_SCHEMA, 'files': files}, sort_keys=True, allow_unicode=True)
    if not path.is_file() or path.read_text(encoding='utf-8') != text:
        path.write_text(text, encoding='utf-8', newline='\n')
    return path, changed


DEFAULT_SKILL_LANGUAGE = 'pl'   # the language of the core library


def skill_language(layerset):
    """Language of the skills, roles and instructions the layers of a project provide: the language of the last translation layer, else the core's `pl`."""
    found = [layer.language for layer in layerset.layers if layer.is_translation and layer.language]
    return found[-1] if found else DEFAULT_SKILL_LANGUAGE


def language_findings(layerset, manifest_language):
    """`language-mismatch` (warning) when the interface language of the manifest (`i18n.resolve_language`) differs from the language of the layers.

    The manifest key steers the interface; the language of skills is chosen by `layer_rules` of the machine config (a manifest never selects layers)."""
    skills = skill_language(layerset)
    if manifest_language == skills:
        return []
    return [layers._finding('language-mismatch', 'warning',
                            f"manifest language is '{manifest_language}' but the skills, roles and instructions of this project are in '{skills}' "
                            "(choose the layers in `layer_rules` of the machine config, or set `language` in the manifest)", None)]
