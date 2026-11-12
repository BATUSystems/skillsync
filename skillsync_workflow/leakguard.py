"""Leak guard (`skillsync leakcheck`, `skillsync leakguard ...`).

Scans file content, file names and commit metadata for generic leak patterns and for
explicit identities of other layers. The API (`prepare`, `scan_texts`, `leakcheck`) takes
already resolved inputs (identity files, a targets mapping, the chosen target, the scan
mode and target pattern additions). Only the CLI without explicit inputs (and hooks, which
call it that way) takes them from the machine config via `layers.leakguard_inputs`.
Reports never contain the matched text or pattern values; a finding is only
`{path, category, location}`.
"""
import argparse
import fnmatch
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

from .common import exit_code, redact
from .selfcmd import self_argv

SCHEMA = 1
DATA_DIR = Path(__file__).resolve().parent / 'data'
PATTERNS_RESOURCE = DATA_DIR / 'leakguard-patterns.yaml'
SUFFIXES_RESOURCE = DATA_DIR / 'leakguard-suffixes-pl.txt'
# Exact paths (relative to the git root, or to the scanned directory without git) of the
# only two files exempt from generic patterns in content scans (both after validation).
RESOURCE_REL = 'skillsync_workflow/data/leakguard-patterns.yaml'
TARGET_PATTERNS_REL = '.leakguard/patterns.yaml'

CATEGORIES = ('d27', 'owner-path', 'email', 'machine', 'publisher', 'custom')
FLAGS = {'ignorecase': re.IGNORECASE, 'multiline': re.MULTILINE}
SURFACES = ('content', 'filename', 'commit')
PATH_CLASSES = ('instrukcje', 'atrybucje', 'inne')
TERM_KINDS = ('org', 'project', 'domain', 'person', 'domain-term', 'machine',
              'owner-nick', 'owner-name', 'persona', 'publisher')
CASES = ('insensitive', 'sensitive')
MATCHES = ('part', 'whole')   # D104: `part` also hits a segment of an identifier, `whole` only a whole token
LAYER_KINDS = ('core', 'org', 'personal')
SCOPES = ('public', 'org-private', 'private')
MODES = ('worktree', 'staged', 'range', 'history', 'text')
TEXT_LIMIT_BYTES = 1024 * 1024          # `--text`/`--stdin`: a PR or release description, not a repository
TEXT_CLASS_REL = 'skills/text'           # a text outside git is scanned in the strictest path class (instrukcje)
COVERAGE = ('full', 'partial', 'generic')
AUTHORS_SCOPES = ('all', 'committer')
# D86: default `authors_scope` per target `scope` (public repos accept commits of other people).
DEFAULT_AUTHORS_SCOPE = {'public': 'committer', 'org-private': 'all', 'private': 'all'}

HOOK_BEGIN = '# >>> skillsync leakguard (managed block, do not edit) >>>'
HOOK_END = '# <<< skillsync leakguard (managed block) <<<'
HOOK_NAMES = ('pre-commit', 'pre-push')

_ID = re.compile(r'[a-z0-9-]+')
_INLINE_FLAGS = re.compile(r'\(\?[aiLmsux-]+[:)]')
_TRAILER = re.compile(r'^Co-Authored-By:\s*[^<>]*<([^<>\s]+)>\s*$', re.IGNORECASE)
_GAP = re.compile(r'[\s\-_./]*')
# Lines used to reject target `allow` entries that are too broad (match the empty
# string or cover any whole line).
_ALLOW_PROBES = (
    'x',
    'lorem ipsum dolor sit amet',
    'Zażółć gęślą jaźń 123',
    'path/to/file.md:12: ok -_./:@<>()[]{}',
    'qzxv kwiatkowi stolik host-01.local',
    'Copyright (c) 1999 Nobody',
)

# Path classes (K8). Order of evaluation: instrukcje (any segment), inne (`tests/`, `.github/`), code files
# (`instrukcje`), atrybucje, inne (root files), default instrukcje (strictest). `inne` is equivalent to `instrukcje` in the table.
PATH_CLASS_RULES = {
    'instrukcje': {'segments': ['instructions', 'skills', 'agents', '_base.d']},
    # D238 (spec A, K8): code files outside `tests/` and `.github/` are `instrukcje` (also under
    # `docs/` and in the root); they are no attributions.
    'code': {
        'suffixes': ['.py', '.ts', '.js', '.mjs', '.cjs', '.ps1', '.sh'],
        'exact_names': ['skillsync', 'skillsync-ctl'],
    },
    'atrybucje': {
        'first_segments': ['docs'],
        'names': ['CHANGELOG*', 'README*', 'LICENSE*', 'NOTICE*'],
        'surfaces': ['commit'],
    },
    'inne': {'first_segments': ['tests', '.github'], 'root_files': True},
    'default': 'instrukcje',
    'filename_surface': 'instrukcje',
    # Evaluation order (spec A, K8): `tests/**` and `.github/**` are `inne` before code files and
    # `atrybucje`, so `tests/**/*.py` is `inne` (= `instrukcje` in the K8 table); code files come before
    # `atrybucje` and the root files, so `docs/site/build.py` and `skillsync` are `instrukcje`.
    'order': ['instrukcje.segments', 'inne.first_segments', 'code', 'atrybucje', 'inne.root_files', 'default'],
}

_FOLDED_SEGMENTS = {
    'instrukcje': frozenset(item.casefold() for item in PATH_CLASS_RULES['instrukcje']['segments']),
    'inne': frozenset(item.casefold() for item in PATH_CLASS_RULES['inne']['first_segments']),
}

# Allowed identities by target layer kind (K8): row -> kind -> (instrukcje, atrybucje).
_TABLE = {
    'owner-nick': {'core': (False, True), 'org': (False, True), 'personal': (True, True)},
    'persona': {'core': (False, False), 'org': (False, False), 'personal': (True, True)},
    'owner-name': {'core': (False, True), 'org': (True, True), 'personal': (True, True)},
    'publisher': {'core': (True, True), 'org': (False, False), 'personal': (False, False)},
    'client': {'core': (False, False), 'org': (False, False), 'personal': (False, False)},
}
_ROW = {'owner-nick': 'owner-nick', 'persona': 'persona', 'owner-name': 'owner-name', 'publisher': 'publisher'}

_FOLD_EXTRA = {'ł': 'l', 'Ł': 'L', 'đ': 'd', 'Đ': 'D', 'ø': 'o', 'Ø': 'O', 'ß': 'ss', 'ẞ': 'SS'}


class LeakguardError(ValueError):
    """Invalid input (schema, file, argument); reported as BLOCKED."""


# --------------------------------------------------------------------------- patterns

@dataclass
class Rule:
    id: str
    regex: str
    compiled: re.Pattern
    flags: list
    applies_to: tuple
    path_classes: tuple
    paths: tuple
    source: str
    category: str = ''

    def applies(self, surface, path_class, rel):
        if surface not in self.applies_to or path_class not in self.path_classes:
            return False
        return not self.paths or _path_matches(rel, self.paths)

    def as_dict(self, kind):
        data = {'id': self.id, 'regex': self.regex, 'flags': list(self.flags),
                'applies_to': list(self.applies_to), 'path_classes': list(self.path_classes),
                'paths': list(self.paths), 'source': self.source}
        if kind == 'pattern':
            data['category'] = self.category
        return data


@dataclass
class PatternSet:
    patterns: list = field(default_factory=list)
    allow: list = field(default_factory=list)

    def merged(self, other):
        ids = {rule.id for rule in self.patterns + self.allow}
        for rule in other.patterns + other.allow:
            if rule.id in ids:
                raise LeakguardError(f'Duplicate pattern id across pattern files: {rule.id}')
            ids.add(rule.id)
        return PatternSet(self.patterns + other.patterns, self.allow + other.allow)


def _path_matches(rel, globs):
    name = PurePosixPath(rel).name
    return any(fnmatch.fnmatchcase(rel, glob) or ('/' not in glob and fnmatch.fnmatchcase(name, glob))
               for glob in globs)


def _choice_list(entry, key, default, allowed, label):
    value = entry.get(key, default)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise LeakguardError(f'{label}.{key} must be a list of strings')
    unknown = [item for item in value if item not in allowed]
    if unknown:
        raise LeakguardError(f'{label}.{key} has unsupported values: {", ".join(unknown)}')
    return tuple(value)


def _rule(entry, label, source, kind):
    if not isinstance(entry, dict):
        raise LeakguardError(f'{label} must be a mapping')
    rule_id = entry.get('id')
    if not isinstance(rule_id, str) or not _ID.fullmatch(rule_id):
        raise LeakguardError(f'{label}.id must match [a-z0-9-]+')
    label = f'{label} ({rule_id})'
    regex = entry.get('regex')
    if not isinstance(regex, str) or not regex:
        raise LeakguardError(f'{label}.regex must be a nonempty string')
    if _INLINE_FLAGS.search(regex):
        raise LeakguardError(f'{label}.regex must not use inline flags; use `flags`')
    flags = _choice_list(entry, 'flags', [], FLAGS, label)
    compiled_flags = 0
    for flag in flags:
        compiled_flags |= FLAGS[flag]
    try:
        compiled = re.compile(regex, compiled_flags)
    except re.error as exc:
        raise LeakguardError(f'{label}.regex is invalid: {exc}') from None
    paths = entry.get('paths', [])
    if not isinstance(paths, list) or not all(isinstance(item, str) and item for item in paths):
        raise LeakguardError(f'{label}.paths must be a list of nonempty globs')
    category = ''
    if kind == 'pattern':
        category = entry.get('category')
        if category not in CATEGORIES:
            raise LeakguardError(f'{label}.category must be one of: {", ".join(CATEGORIES)}')
    return Rule(rule_id, regex, compiled, list(flags),
                _choice_list(entry, 'applies_to', list(SURFACES), SURFACES, label),
                _choice_list(entry, 'path_classes', list(PATH_CLASSES), PATH_CLASSES, label),
                tuple(paths), source, category)


def parse_patterns(data, source='builtin'):
    """Validate a parsed `patterns.yaml` document (schema 1) and return a PatternSet."""
    if not isinstance(data, dict):
        raise LeakguardError(f'Patterns file ({source}) must be a mapping')
    schema = data.get('schema')
    if isinstance(schema, bool) or not isinstance(schema, int):
        raise LeakguardError(f'Patterns file ({source}) requires integer `schema`')
    if schema > SCHEMA:
        raise LeakguardError(f'Patterns file ({source}) uses schema {schema}; this generator reads schema {SCHEMA}')
    if schema != SCHEMA:
        raise LeakguardError(f'Patterns file ({source}) has unsupported schema {schema}')
    result = PatternSet()
    seen = set()
    for key, kind in (('patterns', 'pattern'), ('allow', 'allow')):
        entries = data.get(key, [])
        if entries is None:
            entries = []
        if not isinstance(entries, list):
            raise LeakguardError(f'Patterns file ({source}): `{key}` must be a list')
        for index, entry in enumerate(entries):
            rule = _rule(entry, f'{key}[{index}]', source, kind)
            if rule.id in seen:
                raise LeakguardError(f'Patterns file ({source}): duplicate id {rule.id}')
            seen.add(rule.id)
            (result.patterns if kind == 'pattern' else result.allow).append(rule)
    return result


def _read_yaml(path, what):
    path = Path(path)
    if not path.is_file():
        raise LeakguardError(f'{what} not found: {path}')
    try:
        return yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise LeakguardError(f'{what} is unreadable: {path} ({type(exc).__name__})') from None


def load_patterns(path=None, source='builtin'):
    """Load the generator resource (default) or another patterns file."""
    return parse_patterns(_read_yaml(path or PATTERNS_RESOURCE, 'Patterns file'), source)


def _allow_too_broad(rule):
    if rule.compiled.search('') is not None:
        return True
    for probe in _ALLOW_PROBES:
        covered = [False] * len(probe)
        for match in rule.compiled.finditer(probe):
            for index in range(match.start(), match.end()):
                covered[index] = True
        if all(covered[index] or char.isspace() for index, char in enumerate(probe)):
            return True
    return False


def validate_target_additions(patterns, text=None, matcher=None, label='Target patterns'):
    """Reject broad `allow` entries and explicit identity terms (D37) in target additions."""
    for rule in patterns.allow:
        if _allow_too_broad(rule):
            raise LeakguardError(f'{label}: allow entry {rule.id} matches the empty string or a whole line')
    if matcher is not None and text is not None:
        for line in text.splitlines():
            if matcher.find(line):
                raise LeakguardError(f'{label} contain an explicit identity term; value not shown')
    return patterns


def load_target_patterns(path, matcher=None):
    """Load and validate `.leakguard/patterns.yaml` of a target repository."""
    path = Path(path)
    data = _read_yaml(path, 'Target patterns file')
    patterns = parse_patterns(data, 'target')
    return validate_target_additions(patterns, path.read_text(encoding='utf-8'), matcher)


def _valid_patterns_text(text, broad_check):
    try:
        patterns = parse_patterns(yaml.safe_load(text), 'scan')
        if broad_check:
            validate_target_additions(patterns)
    except (LeakguardError, yaml.YAMLError):
        return False
    return True


def load_suffixes(path=None):
    path = Path(path or SUFFIXES_RESOURCE)
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeDecodeError):
        raise LeakguardError(f'Suffix resource is unreadable: {path}') from None
    suffixes = []
    for raw in lines:
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        if not line.isalpha():
            raise LeakguardError(f'Suffix resource has an invalid entry in {path}')
        suffixes.append(_fold(line).lower())
    if not suffixes:
        raise LeakguardError(f'Suffix resource is empty: {path}')
    return tuple(sorted(set(suffixes), key=len, reverse=True))


# --------------------------------------------------------------------------- identities

def _fold(text):
    out = []
    for char in text:
        if char in _FOLD_EXTRA:
            out.append(_FOLD_EXTRA[char])
            continue
        if unicodedata.combining(char):
            # A separate combining mark (NFD input) belongs to the preceding letter.
            continue
        decomposed = ''.join(c for c in unicodedata.normalize('NFKD', char) if not unicodedata.combining(c))
        out.append(decomposed or char)
    return ''.join(out)


def tokens(line):
    """Split a line into identifier tokens: non-alphanumerics, `_`, camelCase, acronyms.

    Combining marks (decomposed diacritics, NFD) stay inside the token of their base
    letter; spans always index the original line.
    """
    result = []
    size = len(line)

    def mark(position):
        return unicodedata.combining(line[position]) != 0

    def base_before(position):
        position -= 1
        while position > 0 and mark(position):
            position -= 1
        return line[position]

    def base_after(position):
        position += 1
        while position < size and mark(position):
            position += 1
        return line[position] if position < size else ''

    index = 0
    while index < size:
        if not line[index].isalnum():
            index += 1
            continue
        end = index + 1
        while end < size and (line[end].isalnum() or mark(end)):
            char = line[end]
            if mark(end):
                end += 1
                continue
            before = base_before(end)
            if char.isupper() and (before.islower() or before.isdigit()):
                break
            if char.isupper() and before.isupper() and base_after(end).islower():
                break
            end += 1
        result.append((index, end, line[index:end]))
        index = end
    return result


def _identifier_char(char):
    return char.isalnum() or char in '_-' or unicodedata.combining(char) != 0


def _whole_token(line, start, end):
    """D104 `match: whole`: the span `[start, end)` is a whole token, i.e. a whole snake_case/kebab-case/camelCase
    identifier or a separate word (no letter, digit, `_` or `-` right before or after it)."""
    return ((start == 0 or not _identifier_char(line[start - 1]))
            and (end >= len(line) or not _identifier_char(line[end])))


@dataclass
class Term:
    term: str
    kind: str
    case: str
    allow_in: tuple
    path_classes: tuple
    layer: str
    parts: tuple = ()
    parts_case: tuple = ()
    exact: bool = False
    glued: bool = False   # one-letter camelCase prefix (`xABC`): only adjacent tokens match (`_glued_hits`)
    match: str = 'part'   # D104: `whole` = the hit must cover a whole identifier/word (`_whole_token`)


@dataclass
class Identity:
    layer: str
    terms: list
    path: str
    empty: str = None   # D190: declared reason why the layer has no terms (counts toward `full` coverage)


def parse_identity(data, source='identity'):
    if not isinstance(data, dict):
        raise LeakguardError(f'Identity file {source} must be a mapping')
    schema = data.get('schema')
    if isinstance(schema, bool) or not isinstance(schema, int):
        raise LeakguardError(f'Identity file {source} requires integer `schema`')
    if schema > SCHEMA:
        raise LeakguardError(f'Identity file {source} uses schema {schema}; this generator reads schema {SCHEMA}')
    if schema != SCHEMA:
        raise LeakguardError(f'Identity file {source} has unsupported schema {schema}')
    layer = data.get('layer')
    if not isinstance(layer, str) or not layer:
        raise LeakguardError(f'Identity file {source} requires `layer`')
    entries = data.get('terms', [])
    if not isinstance(entries, list):
        raise LeakguardError(f'Identity file {source}: `terms` must be a list')
    empty = data.get('empty')
    if empty is not None:
        if not isinstance(empty, str) or not empty.strip():
            raise LeakguardError(f'Identity file {source}: `empty` must be a nonempty string (the reason the layer has no terms)')
        if entries:
            raise LeakguardError(f'Identity file {source}: `empty` declares no terms, but `terms` is not empty')
    terms = []
    for index, entry in enumerate(entries):
        label = f'Identity file {source}: terms[{index}]'
        if not isinstance(entry, dict):
            raise LeakguardError(f'{label} must be a mapping')
        value = entry.get('term')
        if not isinstance(value, str) or not value.strip():
            raise LeakguardError(f'{label}.term must be a nonempty string')
        kind = entry.get('kind')
        if kind not in TERM_KINDS:
            raise LeakguardError(f'{label}.kind must be one of: {", ".join(TERM_KINDS)}')
        case = entry.get('case', 'insensitive')
        if case not in CASES:
            raise LeakguardError(f'{label}.case must be insensitive or sensitive')
        allow_in = entry.get('allow_in', [layer])
        if not isinstance(allow_in, list) or not all(isinstance(item, str) and item for item in allow_in):
            raise LeakguardError(f'{label}.allow_in must be a list of target ids')
        path_classes = _choice_list(entry, 'path_classes', [], PATH_CLASSES, label)
        match = entry.get('match', 'part')
        if match not in MATCHES:
            raise LeakguardError(f'{label}.match must be part or whole')
        parts = tokens(value)
        if not parts:
            raise LeakguardError(f'{label}.term has no letters or digits')
        folded = tuple(_fold(text) for _, _, text in parts)
        lower = tuple(text.lower() for text in folded)
        # A term written as one identifier whose first token is a single letter (`xABC` -> x|ABC):
        # only its glued form matches - adjacent tokens (`xABC`, `xABCService`, `xABC_x`) or one token equal
        # to the glued text (`XABC`, `xabc`) - starting where a token starts (start of a run or a camelCase
        # boundary); never its tokens as separate words (`... x ABC`, `SVG i PNG`) nor `fooxABC`.
        glued = (len(parts) > 1 and len(parts[0][2]) == 1
                 and all(parts[index][1] == parts[index + 1][0] for index in range(len(parts) - 1)))
        terms.append(Term(value, kind, case, tuple(allow_in), path_classes, layer, lower, folded,
                          case == 'sensitive' or len(''.join(lower)) < 4, glued, match))
    return Identity(layer, terms, source, empty.strip() if empty is not None else None)


def load_identity(path):
    return parse_identity(_read_yaml(path, 'Identity file'), str(path))


class TermMatcher:
    """Find explicit identity terms in a line (inflections, identifiers, diacritics)."""

    def __init__(self, terms, suffixes=()):
        self.terms = list(terms)
        self.suffixes = tuple(suffixes)
        self.loose = {}
        self.exact = {}
        self.glued = {}   # glued terms by the first letter of their glued text (`_glued_hits`)
        for term in self.terms:
            if term.glued:
                self.glued.setdefault(term.parts[0][:1], []).append(term)
                continue
            if term.exact:
                self.exact.setdefault(term.parts_case[0], []).append(('seq', term))
                continue
            self.loose.setdefault(term.parts[0], []).append(('seq', term))
            if len(term.parts) > 1:
                self.loose.setdefault(''.join(term.parts), []).append(('glued', term))

    def _last_ok(self, token, part):
        return token == part or any(token == part + suffix for suffix in self.suffixes)

    def _sequence(self, line, toks, values, start, parts, inflect, glued=False):
        count = len(parts)
        if start + count > len(toks):
            return None
        for offset in range(count):
            index = start + offset
            gap = line[toks[index - 1][1]:toks[index][0]] if offset else ''
            if offset and (gap if glued else not _GAP.fullmatch(gap)):
                return None
            last = offset == count - 1
            if last and inflect:
                if not self._last_ok(values[index], parts[offset]):
                    return None
            elif values[index] != parts[offset]:
                return None
        return start + count - 1

    def _glued_hits(self, line, toks, folded, lower):
        """Glued terms (`term.glued`): starting where a token starts (start of a run or a camelCase
        boundary), adjacent tokens (no separator between them) concatenated must equal the glued text,
        optionally followed by an inflection suffix, and end where a token ends: `xABC`, `XABC`,
        `xABCService`, `xABC_x`, `xABCowi` hit; `x ABC`, `SVG i PNG`, `fooxABC` do not. Exact terms
        compare case-preserving and without suffixes."""
        hits = []
        for index, value in enumerate(lower):
            for term in self.glued.get(value[:1], ()):
                values = folded if term.exact else lower
                glued = ''.join(term.parts_case if term.exact else term.parts)
                text = ''
                for last in range(index, len(toks)):
                    if last > index and line[toks[last - 1][1]:toks[last][0]]:
                        break
                    text += values[last]
                    if not glued.startswith(text[:len(glued)]):
                        break
                    if text == glued or (not term.exact and len(text) > len(glued)
                                         and text[len(glued):] in self.suffixes):
                        hits.append((toks[index][0], toks[last][1], term))
                        break
                    if len(text) >= len(glued) + max((len(item) for item in self.suffixes), default=0):
                        break
        return hits

    def find(self, line):
        """Return `[(start, end, term)]`; never exposes anything but spans to callers."""
        if not self.terms:
            return []
        return [hit for hit in self._find(line) if hit[2].match != 'whole' or _whole_token(line, hit[0], hit[1])]

    def _find(self, line):
        toks = tokens(line)
        if not toks:
            return []
        folded = [_fold(text) for _, _, text in toks]
        lower = [text.lower() for text in folded]
        hits = self._glued_hits(line, toks, folded, lower) if self.glued else []
        for index, value in enumerate(lower):
            keys = {value}
            keys.update(value[:-len(suffix)] for suffix in self.suffixes
                        if value.endswith(suffix) and len(value) > len(suffix))
            for key in keys:
                for mode, term in self.loose.get(key, ()):
                    if mode == 'glued':
                        if self._last_ok(value, ''.join(term.parts)):
                            hits.append((toks[index][0], toks[index][1], term))
                        continue
                    if len(term.parts) > 1 and key != value:
                        continue
                    last = self._sequence(line, toks, lower, index, term.parts, True)
                    if last is not None:
                        hits.append((toks[index][0], toks[last][1], term))
            for _, term in self.exact.get(folded[index], ()):
                last = self._sequence(line, toks, folded, index, term.parts_case, False)
                if last is not None:
                    hits.append((toks[index][0], toks[last][1], term))
        return hits


def term_allowed(term, target, target_kind, path_class):
    """Is an explicit term allowed in the target for a path class (K8 table + allow_in)?"""
    effective = 'instrukcje' if path_class == 'inne' else path_class
    if target is not None and target in term.allow_in:
        classes = {'instrukcje' if item == 'inne' else item for item in term.path_classes}
        if not classes or effective in classes:
            return True
    row = _TABLE[_ROW.get(term.kind, 'client')]
    column = 1 if effective == 'atrybucje' else 0
    kinds = [target_kind] if target_kind in LAYER_KINDS else list(LAYER_KINDS)
    return all(row[kind][column] for kind in kinds)


# --------------------------------------------------------------------------- targets

def validate_targets(targets):
    """Validate a resolved targets mapping `{id: {scope, authors, remote_match, kind?}}`."""
    if targets is None:
        return None
    if not isinstance(targets, dict):
        raise LeakguardError('Targets must be a mapping {id: {scope, authors, remote_match}}')
    result = {}
    for target_id, entry in targets.items():
        if not isinstance(target_id, str) or not target_id:
            raise LeakguardError('Target ids must be nonempty strings')
        if not isinstance(entry, dict):
            raise LeakguardError(f'Target {target_id} must be a mapping')
        scope = entry.get('scope')
        if scope not in SCOPES:
            raise LeakguardError(f'Target {target_id}: scope must be one of: {", ".join(SCOPES)}')
        kind = entry.get('kind')
        if kind is not None and kind not in LAYER_KINDS:
            raise LeakguardError(f'Target {target_id}: kind must be one of: {", ".join(LAYER_KINDS)}')
        values = {}
        for key in ('authors', 'remote_match'):
            value = entry.get(key, [])
            if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
                raise LeakguardError(f'Target {target_id}: {key} must be a list of nonempty strings')
            values[key] = value
        authors_scope = entry.get('authors_scope')
        if authors_scope is None:
            authors_scope = DEFAULT_AUTHORS_SCOPE[scope]
        elif authors_scope not in AUTHORS_SCOPES:
            raise LeakguardError(f'Target {target_id}: authors_scope must be one of: {", ".join(AUTHORS_SCOPES)}')
        result[target_id] = {'scope': scope, 'kind': kind, **values, 'authors_scope': authors_scope}
    return result


def _strip_remote(url):
    url = re.sub(r'^[a-z][a-z0-9+.-]*://', '', url, flags=re.IGNORECASE)
    return re.sub(r'^[^@/]+@', '', url)


_LOCAL_PATH = re.compile(r'^(?:/|[A-Za-z]:[\\/]|\\\\)')


def _match_forms(text):
    """Forms of a remote or pattern: a local path also with `/` separators and case-folded
    (`layers.path_case_key`: Windows and macOS); URLs and scp-like addresses only literally."""
    if not _LOCAL_PATH.match(text):
        return {text}
    from .layers import path_case_key
    return {text, text.replace('\\', '/'), path_case_key(text)}  # the host: normcase (+ `/` on Windows; NFD + casefold on macOS)


def match_targets(targets, remotes):
    """Target ids whose `remote_match` globs match any of the remote URLs.

    Local paths (on both sides) are compared also after separator normalization
    (`\\` -> `/`) and case folding on Windows and macOS (`layers.path_case_key`), so a local
    source matches the origin of its clone; URLs are compared literally."""
    found = []
    for target_id, entry in (targets or {}).items():
        globs = {form for glob in entry.get('remote_match', []) for form in _match_forms(glob)}
        for remote in remotes:
            candidates = {form for item in (remote, _strip_remote(remote)) for form in _match_forms(item)}
            if any(fnmatch.fnmatchcase(candidate, glob) for glob in globs for candidate in candidates):
                found.append(target_id)
                break
    return sorted(found)


def select_target(path, targets, checkouts=None):
    """The single target selection (`leakcheck` API and `layers.leakguard_inputs`).

    `(target, warnings)`: a unique `remote_match` hit of the git root of `path`; several hits
    = None + warning; no hit = the id in `checkouts` ({id: path}) whose path is that git root.
    """
    top = git_root(Path(path)) if Path(path).is_dir() else None
    if top is None:
        return None, []
    matched = match_targets(targets, _remotes(top)) if targets else []
    if len(matched) == 1:
        return matched[0], []
    if matched:
        return None, ['Several leakguard targets match the remotes; pass --target']
    from .layers import path_case_key
    wanted = path_case_key(os.path.realpath(top))
    for target_id, checkout in (checkouts or {}).items():
        if path_case_key(os.path.realpath(checkout)) == wanted:
            return target_id, []
    return None, []


def compute_coverage(targets, target, identity_layers, empty_layers=()):
    """`full`/`partial`/`generic` and warnings (K7).

    `identity_layers`: layers whose loaded identity counts (at least one term or a declared `empty`);
    `empty_layers`: layers with loaded identity files but no terms and no `empty` (D190). Such a layer
    does not count toward `full`; when coverage needs it, a warning says so.
    """
    warnings = []
    if not targets or target is None or target not in targets:
        if target is None:
            warnings.append('No leakguard target selected; only generic patterns guarantee coverage')
        else:
            warnings.append(f'Target {target} is unknown in leakguard targets; coverage is generic')
        return 'generic', warnings
    needed = {target_id for target_id, entry in targets.items()
              if target_id != target and entry['scope'] != 'public'}
    have = set(identity_layers) - {target}
    for layer in sorted((set(empty_layers) - have) & needed):
        warnings.append(f'Identity of layer {layer} has no terms; it does not count toward full coverage '
                        '(declare empty: <reason> if intended)')
    if needed <= have:
        return 'full', warnings
    if have:
        return 'partial', warnings
    return 'generic', warnings


# --------------------------------------------------------------------------- path classes

def path_class(rel, surface='content'):
    """Path class of a POSIX path relative to the scanned root (K8)."""
    if surface == 'commit':
        return 'atrybucje'
    if surface == 'filename':
        return PATH_CLASS_RULES['filename_surface']
    parts = PurePosixPath(rel).parts
    rules = PATH_CLASS_RULES
    # The generator reads `Skills/` as `skills/` on case-insensitive systems (`_fold_rel`
    # in layers.py), so the segment checks are case-folded on every system. They only make
    # the class stricter (`instrukcje`/`inne`); the `atrybucje` checks stay exact, because
    # folding them would relax the class of e.g. `x/readme.md` or `Docs/x.md`.
    folded = [part.casefold() for part in parts]
    if any(part in _FOLDED_SEGMENTS['instrukcje'] for part in folded[:-1]):
        return 'instrukcje'
    if folded and folded[0] in _FOLDED_SEGMENTS['inne'] and len(parts) > 1:
        return 'inne'  # before `atrybucje`: `tests/**` (also `tests/README.md`) is `inne`
    name = parts[-1] if parts else rel
    code, folded_name = rules['code'], name.casefold()   # stricter class: `Build.PY` is code too
    if any(folded_name.endswith(suffix) for suffix in code['suffixes']) or folded_name in code['exact_names']:
        return 'instrukcje'  # D238: code is never an attribution
    attr = rules['atrybucje']
    if (parts and parts[0] in attr['first_segments'] and len(parts) > 1) \
            or any(fnmatch.fnmatchcase(name, glob) for glob in attr['names']):
        return 'atrybucje'
    if len(parts) == 1:
        return 'inne'
    return rules['default']


# --------------------------------------------------------------------------- scanning

def _class_preserving_filler(text):
    """Replace every character by a different one of the same class: uppercase and
    lowercase letters, digits; other characters stay. A generic allow (e.g. a ticket key
    regex) keeps matching, an allow with the term as a fixed literal does not."""
    out = []
    for char in text:
        if char.isdigit():
            out.append('7' if char != '7' else '3')
        elif char.isalpha() and char.isupper():
            out.append('Q' if char != 'Q' else 'Z')
        elif char.isalpha():
            out.append('q' if char != 'q' else 'z')
        else:
            out.append(char)
    return ''.join(out)


class _Scanner:
    def __init__(self, patterns, matcher, target, target_kind, trailer_allow):
        self.patterns = patterns
        self.matcher = matcher
        self.target = target
        self.target_kind = target_kind
        self.trailer_allow = {address.lower() for address in trailer_allow}
        self.findings = set()

    def line(self, text, surface, cls, rel, path, location, exempt=False, header_email=False):
        hits = []
        if not exempt:
            for rule in self.patterns.patterns:
                if header_email and rule.category == 'email':
                    continue
                if rule.applies(surface, cls, rel):
                    hits.extend((m.start(), m.end(), rule.category) for m in rule.compiled.finditer(text)
                                if m.end() > m.start())
        for start, end, term in self.matcher.find(text):
            if not term_allowed(term, self.target, self.target_kind, cls):
                hits.append((start, end, 'identity-' + term.kind, True))
        if not hits:
            return
        # Each allow source yields spans for a given text, so identity hits can be rechecked.
        sources = [lambda value, rule=rule: [m.span() for m in rule.compiled.finditer(value)]
                   for rule in self.patterns.allow if rule.applies(surface, cls, rel)]
        if surface == 'commit' and self.trailer_allow:
            sources.append(self._trailer_spans)
        allowed = [(source, source(text)) for source in sources]
        for start, end, category, *identity in hits:
            if self._suppressed(text, start, end, bool(identity), allowed):
                continue
            column = f'{location}:{start + 1}' if location else str(start + 1)
            self.findings.add((path, category, column))

    def _trailer_spans(self, value):
        match = _TRAILER.match(value)
        return [match.span(1)] if match and match.group(1).lower() in self.trailer_allow else []

    @staticmethod
    def _suppressed(text, start, end, identity, allowed):
        """Pattern hits: contained in an allow match. Identity hits additionally: the
        allow stops covering the range once the term is replaced by a neutral filler,
        i.e. the term is part of the allow's fixed literal (publisher name), not a
        free part such as a path segment, an e-mail local part or a trailer name."""
        filled = None
        for source, spans in allowed:
            if not any(a <= start and end <= b for a, b in spans):
                continue
            if not identity:
                return True
            if filled is None:
                filled = text[:start] + _class_preserving_filler(text[start:end]) + text[end:]
            if not any(a <= start and end <= b for a, b in source(filled)):
                return True
        return False

    def text(self, content, rel, path, prefix, exempt):
        cls = path_class(rel)
        for number, line in enumerate(content.splitlines(), 1):
            self.line(line, 'content', cls, rel, path, f'{prefix}{number}', exempt)

    def filename(self, rel, path, prefix=''):
        self.line(rel, 'filename', path_class(rel, 'filename'), rel, path, f'{prefix}filename')

    def commit(self, sha, fields, message, label, kind='commit'):
        path = f'@{kind}:{sha[:12]}'
        for name, value, header_email in fields:
            self.line(value, 'commit', 'atrybucje', path, path, name, header_email=header_email)
        for number, line in enumerate(message.splitlines(), 1):
            self.line(line, 'commit', 'atrybucje', path, path, f'{label}:{number}')


def _decode(data):
    if b'\0' in data[:8192]:
        return None
    return data.decode('utf-8', errors='replace')


def _git(cwd, *args, input=None, check=True):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
    try:
        process = subprocess.run(['git', '-c', 'core.quotepath=off', *args], cwd=str(cwd), input=input,
                                 capture_output=True, env=env, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LeakguardError(f'git is unavailable: {type(exc).__name__}') from None
    if check and process.returncode:
        message = process.stderr.decode('utf-8', errors='replace').strip().splitlines()
        raise LeakguardError(f'git {args[0]} failed: {message[-1] if message else process.returncode}')
    return process


def git_root(path):
    process = _git(path, 'rev-parse', '--show-toplevel', check=False)
    if process.returncode:
        return None
    return Path(os.fsdecode(process.stdout.strip())).resolve()


def _git_prefix(path):
    return os.fsdecode(_git(path, 'rev-parse', '--show-prefix').stdout.strip())


def _remotes(root):
    process = _git(root, 'remote', '-v', check=False)
    if process.returncode:
        return []
    urls = set()
    for line in process.stdout.decode('utf-8', errors='replace').splitlines():
        parts = line.split()
        if len(parts) >= 2:
            urls.add(parts[1])
    return sorted(urls)


def _blobs(root, shas):
    """Read blobs with one `git cat-file --batch`; returns {sha: bytes}."""
    unique = list(dict.fromkeys(shas))
    if not unique:
        return {}
    output = _git(root, 'cat-file', '--batch', input=('\n'.join(unique) + '\n').encode()).stdout
    result = {}
    offset = 0
    for sha in unique:
        newline = output.index(b'\n', offset)
        header = output[offset:newline].split()
        offset = newline + 1
        if len(header) < 3 or header[1] == b'missing':
            continue
        size = int(header[2])
        result[sha] = output[offset:offset + size]
        offset += size + 1
    return result


def _raw_entries(output, with_commits=False):
    """Parse `git diff --raw -z` (or `diff-tree --stdin -z`) output.

    Returns `[(mode, blob, status, path)]`, or `[(commit, (mode, blob, status, path))]`
    when `with_commits` (commit ids are the non-metadata records between entries).
    """
    parts = output.split(b'\0')
    entries = []
    commit = None
    index = 0
    while index < len(parts):
        meta = parts[index].decode('ascii', errors='replace').strip()
        if not meta.startswith(':'):
            if meta:
                commit = meta
            index += 1
            continue
        fields = meta[1:].split()
        status = fields[4][:1] if len(fields) > 4 else ''
        count = 2 if status in ('R', 'C') else 1
        if index + count >= len(parts):
            break
        entry = (fields[1], fields[3], status, os.fsdecode(parts[index + count]))
        entries.append((commit, entry) if with_commits else entry)
        index += 1 + count
    return entries


def _local_email(root):
    process = _git(root, 'config', 'user.email', check=False)
    return process.stdout.decode('utf-8', errors='replace').strip().lower() if not process.returncode else ''


def _ident_email(root, var):
    process = _git(root, 'var', var, check=False)
    if process.returncode:
        return None
    match = re.search(r'<([^<>]*)>', process.stdout.decode('utf-8', errors='replace'))
    return match.group(1).strip().lower() if match else None


def _check_rev(value):
    if not isinstance(value, str) or not value or any(part.startswith('-') for part in value.split('..')):
        raise LeakguardError('Revision range must look like A..B or a single revision')
    return value


def _generator_repo(git_top):
    """Is the scanned repository the checkout this module runs from?"""
    if git_top is None:
        return False
    try:
        return Path(__file__).resolve().is_relative_to(Path(git_top).resolve())
    except OSError:
        return False


def _exempt_rel(rel_git, text, git_top):
    """Exemption by exact path: the generator resource only inside the generator repo,
    the target additions file in any repo; both after schema validation."""
    if rel_git == RESOURCE_REL and _generator_repo(git_top):
        return _valid_patterns_text(text, broad_check=False)
    if rel_git == TARGET_PATTERNS_REL:
        return _valid_patterns_text(text, broad_check=True)
    return False


class _Context:
    def __init__(self, root, git_top, prefix, scanner, authors, authors_scope='committer'):
        self.root = root
        self.git_top = git_top
        self.prefix = prefix
        self.scanner = scanner
        self.authors = authors
        self.authors_scope = authors_scope
        self.notices = set()
        self.warnings = []

    def rel_git(self, rel):
        return self.prefix + rel

    def exempt(self, rel_git, text, abs_path=None):
        if abs_path is not None:
            try:
                if Path(abs_path).resolve() == PATTERNS_RESOURCE.resolve():
                    return True
            except OSError:
                pass
        return _exempt_rel(rel_git, text, self.git_top)

    def under_root(self, rel_top):
        if not self.prefix:
            return rel_top
        return rel_top[len(self.prefix):] if rel_top.startswith(self.prefix) else None


def _scan_worktree(ctx):
    scanner = ctx.scanner
    if ctx.git_top is not None:
        output = _git(ctx.root, 'ls-files', '-z', '-co', '--exclude-standard').stdout
        files = sorted({os.fsdecode(item) for item in output.split(b'\0') if item})
    else:
        files = []
        for directory, dirs, names in os.walk(ctx.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d != '.git' and not os.path.islink(os.path.join(directory, d)))
            linked = [d for d in os.listdir(directory) if d != '.git' and os.path.islink(os.path.join(directory, d))
                      and os.path.isdir(os.path.join(directory, d))]
            for name in sorted(set(names) | set(linked)):
                files.append(Path(os.path.relpath(os.path.join(directory, name), ctx.root)).as_posix())
    for rel in files:
        absolute = ctx.root / rel
        if not os.path.lexists(absolute):
            continue
        scanner.filename(rel, rel)
        if absolute.is_symlink():
            text = os.readlink(absolute)
        elif absolute.is_file():
            try:
                text = _decode(absolute.read_bytes())
            except OSError:
                ctx.warnings.append(f'Unreadable file skipped: {rel}')
                continue
        else:
            continue
        if text is None:
            continue
        exempt = ctx.exempt(ctx.rel_git(rel), text, None if absolute.is_symlink() else absolute)
        scanner.text(text, rel, rel, '', exempt)


def _scan_entries(ctx, entries, label):
    """Scan `[(mode, blob, status, rel_top)]` entries; label prefixes locations."""
    wanted = []
    for mode, blob, status, rel_top in entries:
        rel = ctx.under_root(rel_top)
        if rel is None or status == 'D' or mode == '160000' or set(blob) == {'0'}:
            continue
        wanted.append((mode, blob, rel_top, rel))
    contents = _blobs(ctx.git_top, [blob for _, blob, _, _ in wanted])
    for mode, blob, rel_top, rel in wanted:
        ctx.scanner.filename(rel, rel, label)
        data = contents.get(blob)
        text = None if data is None else _decode(data)
        if text is None:
            continue
        ctx.scanner.text(text, rel, rel, label, ctx.exempt(rel_top, text))


def _scan_staged(ctx):
    output = _git(ctx.git_top, 'diff', '--cached', '--raw', '-z', '--no-renames', '--no-abbrev',
                  '--diff-filter=ACMRT').stdout
    _scan_entries(ctx, _raw_entries(output), 'staged:')
    local = _local_email(ctx.git_top)
    if ctx.authors is None:
        return
    for name, var in (('author', 'GIT_AUTHOR_IDENT'), ('committer', 'GIT_COMMITTER_IDENT')):
        email = _ident_email(ctx.git_top, var)
        if email is None:
            ctx.warnings.append(f'Cannot read the {name} identity; authors not checked')
        elif email not in ctx.authors:
            ctx.scanner.findings.add(('@staged', 'author', name))
    if not local:
        ctx.warnings.append('git user.email is not set')


def _scan_commits(ctx, revs):
    # Topological order: reversed, every parent precedes its children.
    process = _git(ctx.git_top, 'rev-list', '--topo-order', '--parents', '--boundary', *revs, check=False)
    if process.returncode:
        if revs == ['HEAD']:
            ctx.warnings.append('Repository has no commits')
            return
        raise LeakguardError('Cannot resolve the revision range')
    rows, boundary = [], []
    for line in process.stdout.decode('ascii').splitlines():
        if line.startswith('-'):
            boundary.append(line[1:].split()[0])
        elif line.strip():
            rows.append(line.split())
    shas = [row[0] for row in rows]
    if not shas:
        return
    # Oldest first, so a blob is attributed to the commit that introduced it. Every
    # commit is fed as `<sha> <first parent>` (root commits alone, with `--root`), so a
    # merge is diffed only against its first parent: a conflict resolution is scanned,
    # while blobs coming from the merged side (possibly outside the range) are not.
    # Portable alternative to `--diff-merges=first-parent` (git >= 2.31).
    # `seen` starts with the trees of the range boundary (commits excluded by `A..B`):
    # a blob already present there is outside the range even when a merge (or a
    # fast-forward to a merge) makes it look new against the first parent.
    pairs = [' '.join(row[:2]) for row in reversed(rows)]
    output = _git(ctx.git_top, 'diff-tree', '--stdin', '-r', '-z', '--root', '--no-renames', '--no-abbrev',
                  input=('\n'.join(pairs) + '\n').encode()).stdout
    seen = set()
    for sha in boundary:
        listing = _git(ctx.git_top, 'ls-tree', '-r', '-z', '--full-tree', sha).stdout
        for item in listing.split(b'\0'):
            meta, _, name = item.partition(b'\t')
            fields = meta.split()
            if len(fields) == 3:
                seen.add((fields[2].decode('ascii'), os.fsdecode(name)))
    by_commit = {}
    for sha, (mode, blob, status, rel_top) in _raw_entries(output, with_commits=True):
        key = (blob, rel_top)
        if sha is None or key in seen:
            continue
        seen.add(key)
        by_commit.setdefault(sha, []).append((mode, blob, status, rel_top))
    for sha in reversed(shas):
        if sha in by_commit:
            _scan_entries(ctx, by_commit[sha], f'{sha[:12]}:')
    local = _local_email(ctx.git_top)
    every = ctx.authors_scope == 'all'
    if not local and not every:
        ctx.warnings.append('git user.email is not set; authors not checked')
    log = _git(ctx.git_top, 'log', '--format=%x1e%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B',
               *revs).stdout.decode('utf-8', errors='replace')
    for record in log.split('\x1e'):
        if not record.strip():
            continue
        fields = record.split('\x00', 5)
        if len(fields) < 6:
            continue
        sha, author_name, author_email, committer_name, committer_email, message = fields
        sha = sha.strip()
        ctx.scanner.commit(sha, [('author-name', author_name, False), ('author-email', author_email, True),
                                 ('committer-name', committer_name, False),
                                 ('committer-email', committer_email, True)], message, 'message')
        path = f'@commit:{sha[:12]}'
        if ctx.authors is None:
            continue
        # D86: `all` = every commit needs author and committer from `authors`; `committer` = only
        # commits committed by the local user, commits of other people are reported as notices.
        # D239: a commit authored by the local user but committed by someone else (cherry-pick, rebase,
        # a merge button) has its author checked; the foreign committer stays a notice.
        if every or (local and committer_email.strip().lower() == local):
            for name, email in (('author', author_email), ('committer', committer_email)):
                if email.strip().lower() not in ctx.authors:
                    ctx.scanner.findings.add((path, 'author', name))
        else:
            if local and author_email.strip().lower() == local and local not in ctx.authors:
                ctx.scanner.findings.add((path, 'author', 'author'))
            ctx.notices.add((path, 'foreign-commit', 'committer'))


_TAG_SIGNATURES = ('-----BEGIN PGP SIGNATURE-----', '-----BEGIN PGP MESSAGE-----',
                   '-----BEGIN SSH SIGNATURE-----', '-----BEGIN SIGNED MESSAGE-----')
_TAGGER = re.compile(r'^(.*?) <([^<>]*)>')


def _range_tips(rev):
    """Positive endpoints of a `--range` value: `A..B` -> B, `A...B` -> A and B, `B` -> B
    (an empty side is `HEAD`, as in git)."""
    if '...' in rev:
        left, _, right = rev.partition('...')
        return [left or 'HEAD', right or 'HEAD']
    if '..' in rev:
        return [rev.partition('..')[2] or 'HEAD']
    return [rev]


def _tag_message(body):
    """Tag message without its signature. Like git `parse_signed_buffer`, the signature
    starts at the LAST line beginning with a signature marker, so text between markers
    (a marker quoted in the message) is still scanned."""
    lines = body.splitlines()
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].startswith(_TAG_SIGNATURES):
            return '\n'.join(lines[:index])
    return '\n'.join(lines)


def _scan_tags(ctx, rev):
    """D173: annotated tags pushed as range tips. The tag message and the tagger go
    through the commit surface (class `atrybucje`, path `@tag:<sha>`); the tagger e-mail
    is checked against `authors` like a committer (D86); the tag name (header `tag`) goes
    through the filename surface (D174). A tag chain (tag of a tag) is
    followed; the tagged commit itself is not a reason to scan more history (the commit
    range is resolved separately). Lightweight tags are plain commits: nothing to add."""
    local = _local_email(ctx.git_top)
    every = ctx.authors_scope == 'all'
    seen = set()
    for tip in _range_tips(rev):
        # The range is already resolved by `_scan_commits`; a tip that does not resolve
        # here (e.g. a git without `rev-parse --end-of-options`) blocks instead of
        # silently skipping the tag.
        process = _git(ctx.git_top, 'rev-parse', '--verify', '--quiet', '--end-of-options', tip, check=False)
        sha = process.stdout.decode('ascii', errors='replace').strip()
        if process.returncode or not sha:
            raise LeakguardError(f'Cannot resolve the range tip {tip} for the tag scan')
        while sha and sha not in seen and len(seen) < 64:
            kind = _git(ctx.git_top, 'cat-file', '-t', sha).stdout.decode('ascii', errors='replace').strip()
            if kind != 'tag':
                break
            seen.add(sha)
            raw = _git(ctx.git_top, 'cat-file', 'tag', sha).stdout.decode('utf-8', errors='replace')
            header, _, body = raw.partition('\n\n')
            target, tagger, name = '', None, None
            for line in header.splitlines():
                if line.startswith('object '):
                    target = line[len('object '):].strip()
                elif line.startswith('tag '):
                    name = line[len('tag '):]
                elif line.startswith('tagger '):
                    tagger = _TAGGER.match(line[len('tagger '):])
            fields = []
            if tagger:
                fields = [('tagger-name', tagger.group(1), False), ('tagger-email', tagger.group(2), True)]
            ctx.scanner.commit(sha, fields, _tag_message(body), 'message', kind='tag')
            if name is not None:
                # D174: the tag name is a name like a file name: filename surface, its
                # strictest class (`filename_surface`), location `tag-name:<col>`.
                path = f'@tag:{sha[:12]}'
                ctx.scanner.line(name, 'filename', PATH_CLASS_RULES['filename_surface'], path, path, 'tag-name')
            if ctx.authors is not None:
                path = f'@tag:{sha[:12]}'
                email = tagger.group(2).strip().lower() if tagger else ''
                if every or (local and email == local):
                    if email not in ctx.authors:
                        ctx.scanner.findings.add((path, 'author', 'tagger'))
                else:
                    ctx.notices.add((path, 'foreign-tag', 'tagger'))
            sha = target


def _finding_key(finding):
    path, category, location = finding
    parts = tuple((0, int(part), '') if part.isdigit() else (1, 0, part) for part in location.split(':'))
    return path, parts, category


@dataclass
class Prepared:
    """Loaded and validated guard inputs for one target (see `prepare`)."""
    patterns: PatternSet
    matcher: TermMatcher
    target: str
    target_kind: str
    coverage: str
    warnings: list
    authors: set
    trailer_allow: tuple
    authors_scope: str = None

    def scanner(self):
        return _Scanner(self.patterns, self.matcher, self.target, self.target_kind, self.trailer_allow)


def prepare(*, target=None, targets=None, identity_files=(), target_patterns=None, trailer_allow=(),
            patterns_file=None, suffixes_file=None):
    """Load patterns, identities and the term matcher for a resolved target.

    `target_patterns` is used only when given (no git root lookup here). Raises
    `LeakguardError` for invalid input. `authors` is `None` when the target is unknown or
    has no authors (a warning is added in the latter case).
    """
    targets = validate_targets(targets)
    identities = [load_identity(item) for item in identity_files]
    foreign = [identity for identity in identities if identity.layer != target]
    matcher = TermMatcher([term for identity in foreign for term in identity.terms], load_suffixes(suffixes_file))
    patterns = load_patterns(patterns_file)
    if target_patterns:
        patterns = patterns.merged(load_target_patterns(target_patterns, matcher))
    counted = {identity.layer for identity in foreign if identity.terms or identity.empty}
    loaded = {identity.layer for identity in foreign}
    coverage, warnings = compute_coverage(targets, target, counted, loaded - counted)
    entry = (targets or {}).get(target) if target else None
    authors = None
    if entry is not None:
        if entry['authors']:
            authors = {item.lower() for item in entry['authors']}
        else:
            warnings.append(f'Target {target} has no authors; commit authors are not checked')
    return Prepared(patterns, matcher, target, entry['kind'] if entry else None, coverage, warnings,
                    authors, tuple(trailer_allow), entry['authors_scope'] if entry else None)


def scan_texts(items, prepared, surface='content'):
    """Scan in-memory texts `[(path relative to the target root, text)]`.

    `surface` is `content` (text per line, path class from the path) or `filename`
    (the path itself; text ignored). Returns a report `{schema, status, coverage,
    target, findings, warnings}`; FAIL when any finding.
    """
    if surface not in ('content', 'filename'):
        raise LeakguardError('scan_texts surface must be content or filename')
    scanner = prepared.scanner()
    for rel, text in items:
        rel = PurePosixPath(str(rel).replace('\\', '/')).as_posix()
        if surface == 'filename':
            scanner.filename(rel, rel)
        else:
            scanner.text(text, rel, rel, '', _exempt_rel(rel, text, None))
    findings = [{'path': p, 'category': c, 'location': l} for p, c, l in sorted(scanner.findings, key=_finding_key)]
    return {'schema': SCHEMA, 'status': 'FAIL' if findings else 'PASS', 'coverage': prepared.coverage,
            'target': prepared.target, 'findings': findings, 'warnings': list(prepared.warnings)}


def leakcheck(path='.', *, mode='worktree', rev_range=None, target=None, targets=None,
              identity_files=(), target_patterns=None, require=None, trailer_allow=(),
              patterns_file=None, suffixes_file=None, select=True, texts=None):
    """Run the leak guard on resolved inputs and return a report (schema 1).

    `targets`: `{id: {scope, authors, remote_match, kind?}}`; `identity_files`: paths of
    `identity.yaml` files; `target_patterns`: path of target additions (default:
    `<git root>/.leakguard/patterns.yaml` when present). Never raises for bad input:
    invalid input is `BLOCKED` with `reason`. `select=False`: the caller already chose the
    target (`select_target` via `layers.leakguard_inputs`), so no second selection here.
    `mode='text'` scans `texts` (`[(label, text)]`, e.g. a PR description written to a file) with the same target,
    identities and patterns as the other modes, in the strictest path class; it needs no git repository and the
    findings carry only the label, never the text.
    """
    report = {'schema': SCHEMA, 'status': 'BLOCKED', 'coverage': 'generic', 'target': target,
              'findings': [], 'warnings': [], 'notices': []}
    try:
        if mode not in MODES:
            raise LeakguardError(f'Unknown mode: {mode}')
        if require not in (None, 'full'):
            raise LeakguardError('--require accepts only: full')
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise LeakguardError(f'Not a directory: {path}')
        targets = validate_targets(targets)
        top = git_root(root)
        if mode not in ('worktree', 'text') and top is None:
            raise LeakguardError(f'Mode {mode} requires a git repository')
        if select and target is None and targets and top is not None:
            target, chosen = select_target(top, targets)
            report['warnings'].extend(chosen)
        if not target_patterns and top is not None:
            candidate = top / TARGET_PATTERNS_REL
            from .layers import _clean, is_link
            if (candidate.exists() or is_link(candidate)) and not _clean(candidate, top):
                # D73: never read target additions through a link (tracked or not, any repo)
                raise LeakguardError(f'{TARGET_PATTERNS_REL} is (or lies under) a symlink/junction; not read')
            if candidate.is_file():
                target_patterns = candidate
        prepared = prepare(target=target, targets=targets, identity_files=identity_files,
                           target_patterns=target_patterns, trailer_allow=trailer_allow,
                           patterns_file=patterns_file, suffixes_file=suffixes_file)
        report['target'] = target
        report['coverage'] = prepared.coverage
        report['warnings'].extend(prepared.warnings)
        if require == 'full' and prepared.coverage != 'full':
            report['reason'] = (f'Coverage is {prepared.coverage}; --require full needs identity files '
                                'for every non-public target')
            return report
        scanner = prepared.scanner()
        prefix = _git_prefix(root) if top is not None else ''
        ctx = _Context(root, top, prefix, scanner, prepared.authors, prepared.authors_scope or 'committer')
        report['authors_scope'] = prepared.authors_scope
        if mode == 'text':
            if not texts:
                raise LeakguardError('Mode text needs a text to scan')
            for label, content in texts:
                if not content.strip():
                    raise LeakguardError('The text is empty: nothing to check')
                if len(content.encode('utf-8', 'surrogatepass')) > TEXT_LIMIT_BYTES:
                    raise LeakguardError(f'Text is larger than {TEXT_LIMIT_BYTES} bytes')
                scanner.text(content, TEXT_CLASS_REL, label, '', False)
        elif mode == 'worktree':
            _scan_worktree(ctx)
        elif mode == 'staged':
            _scan_staged(ctx)
        elif mode == 'range':
            rev = _check_rev(rev_range)
            _scan_commits(ctx, [rev])
            _scan_tags(ctx, rev)
        else:
            _scan_commits(ctx, ['HEAD'])
        report['warnings'].extend(ctx.warnings)
        report['findings'] = [{'path': p, 'category': c, 'location': l}
                              for p, c, l in sorted(scanner.findings, key=_finding_key)]
        report['notices'] = [{'path': p, 'category': c, 'location': l}
                             for p, c, l in sorted(ctx.notices, key=_finding_key)]
        report['status'] = 'FAIL' if report['findings'] else 'PASS'
    except LeakguardError as exc:
        report['status'] = 'BLOCKED'
        report['reason'] = str(exc)
        report['findings'] = []
    return report


def patterns_report(target_patterns=None, patterns_file=None, matcher=None):
    """Merged pattern list (builtin + target additions) for external readers (`build.py`).

    `matcher` (a TermMatcher of foreign identities) rejects additions naming them (D37)."""
    report = {'schema': SCHEMA, 'status': 'BLOCKED', 'patterns': [], 'allow': [],
              'path_class_rules': PATH_CLASS_RULES}
    try:
        patterns = load_patterns(patterns_file)
        if target_patterns:
            patterns = patterns.merged(load_target_patterns(target_patterns, matcher))
        report['patterns'] = [rule.as_dict('pattern') for rule in patterns.patterns]
        report['allow'] = [rule.as_dict('allow') for rule in patterns.allow]
        report['status'] = 'PASS'
    except LeakguardError as exc:
        report['reason'] = str(exc)
    return report


# --------------------------------------------------------------------------- hooks

def _hook_args(identity_files, targets_json, target, trailer_allow):
    args = []
    for item in identity_files:
        args += ['--identity', Path(item).expanduser().resolve().as_posix()]
    if targets_json:
        args += ['--targets-json', Path(targets_json).expanduser().resolve().as_posix()]
    if target:
        args += ['--target', target]
    for address in trailer_allow:
        args += ['--trailer-allow', address]
    return ' '.join(shlex.quote(arg) for arg in args)


def hook_block(name, identity_files=(), targets_json=None, target=None, trailer_allow=()):
    """Managed shell block for `pre-commit` (`--staged`) or `pre-push` (`--range`)."""
    command = ' '.join(shlex.quote(Path(item).as_posix() if os.path.isabs(item) else item) for item in self_argv()) + ' leakcheck'
    extra = _hook_args(identity_files, targets_json, target, trailer_allow)
    extra = f' {extra}' if extra else ''
    if name == 'pre-commit':
        body = (f'{command} "$(git rev-parse --show-toplevel)" --staged{extra} || exit $?\n')
    else:
        body = (
            # Fail closed: any error preparing the ref list (temp file, buffering stdin,
            # reading it back) stops the push; the block also runs under bash/zsh shebangs.
            '_sl_tmp=$(mktemp 2>/dev/null) || _sl_tmp="${TMPDIR:-/tmp}/skillsync-pre-push.$$"\n'
            'if ! cat > "$_sl_tmp"; then\n'
            '  echo "skillsync leakguard: cannot buffer pre-push input in $_sl_tmp" >&2\n'
            '  rm -f "$_sl_tmp" 2>/dev/null\n'
            '  exit 1\n'
            'fi\n'
            'if [ ! -f "$_sl_tmp" ] || [ ! -r "$_sl_tmp" ]; then\n'
            '  echo "skillsync leakguard: cannot read buffered pre-push input $_sl_tmp" >&2\n'
            '  rm -f "$_sl_tmp" 2>/dev/null\n'
            '  exit 1\n'
            'fi\n'
            '_sl_rc=0\n'
            'while read -r _sl_lref _sl_lsha _sl_rref _sl_rsha; do\n'
            '  [ -n "$_sl_lsha" ] || continue\n'
            '  case "$_sl_lsha" in *[!0]*) ;; *) continue ;; esac\n'
            '  _sl_range=\n'
            '  case "$_sl_rsha" in *[!0]*) git cat-file -e "$_sl_rsha^{commit}" 2>/dev/null'
            ' && _sl_range="$_sl_rsha..$_sl_lsha" ;; esac\n'
            '  if [ -z "$_sl_range" ]; then\n'
            '    _sl_def=$(git symbolic-ref -q "refs/remotes/$1/HEAD" 2>/dev/null) || _sl_def=\n'
            '    _sl_base=\n'
            '    if [ -n "$_sl_def" ]; then\n'
            '      _sl_base=$(git merge-base "$_sl_def" "$_sl_lsha" 2>/dev/null) || _sl_base=\n'
            '    fi\n'
            '    if [ -n "$_sl_base" ]; then _sl_range="$_sl_base..$_sl_lsha"; else _sl_range="$_sl_lsha"; fi\n'
            '  fi\n'
            f'  {command} "$(git rev-parse --show-toplevel)" --range "$_sl_range"{extra} || _sl_rc=$?\n'
            'done < "$_sl_tmp" || {\n'
            '  echo "skillsync leakguard: cannot read buffered pre-push input $_sl_tmp" >&2\n'
            '  rm -f "$_sl_tmp" 2>/dev/null\n'
            '  exit 1\n'
            '}\n'
            'exec < "$_sl_tmp" || {\n'
            '  echo "skillsync leakguard: cannot restore pre-push input from $_sl_tmp" >&2\n'
            '  rm -f "$_sl_tmp" 2>/dev/null\n'
            '  exit 1\n'
            '}\n'
            'rm -f "$_sl_tmp" 2>/dev/null\n'
            '[ "$_sl_rc" -eq 0 ] || exit "$_sl_rc"\n'
        )
    return f'{HOOK_BEGIN}\n{body}{HOOK_END}\n'


_BLOCK_RE = re.compile(re.escape(HOOK_BEGIN) + r'\n.*?' + re.escape(HOOK_END) + r'\n?', re.DOTALL)


def _write_atomic(path, text, mode):
    fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix='.' + path.name + '.')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
        os.chmod(temp, mode)
        os.replace(temp, path)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise


def hooks_dir(repo):
    """Local hooks directory, or `(None, reason)` when hooks must not be written there."""
    repo = Path(repo).expanduser().resolve()
    top = git_root(repo)
    if top is None:
        raise LeakguardError(f'Not a git repository: {repo}')
    raw = os.fsdecode(_git(top, 'rev-parse', '--git-path', 'hooks').stdout.strip())
    directory = (top / raw).resolve()
    configured = _git(top, 'config', '--get', 'core.hooksPath', check=False)
    if configured.returncode == 0 and configured.stdout.strip():
        common = (top / os.fsdecode(_git(top, 'rev-parse', '--git-common-dir').stdout.strip())).resolve()
        if not directory.is_relative_to(common):
            tracked = False
            if directory.is_relative_to(top):
                listed = _git(top, 'ls-files', '--', directory.relative_to(top).as_posix(), check=False)
                tracked = bool(listed.stdout.strip())
            where = 'a tracked directory in the repository' if tracked else 'a directory outside the local git dir'
            return top, None, (f'core.hooksPath points to {where}; leakguard does not change shared hooks. '
                               'Call `skillsync leakcheck --staged` (pre-commit) and `--range` (pre-push) manually.')
    return top, directory, None


_LEGACY_INPUTS = ('--identity ', '--targets-json ', '--trailer-allow ')
# A command line calling `skillsync … leakcheck` (manual call in a shared hook); comments do not count.
_LEAKCHECK_CALL = re.compile(r'(?:^|[\s/\\;&|(\'"])skillsync(?:\.exe|\.py|_workflow)?[\'"]?(?:\s+\S+)*?\s+leakcheck(?:\s|$)')


def hook_state(repo):
    """State of the leakguard hooks of `repo` (read-only; follows `core.hooksPath`).

    `{state, hooks: {name: managed|legacy|call|missing|unreadable}, missing: [names], reason?}`;
    `state`: `ok` (managed block or a manual `leakcheck` call in both hooks), `legacy` (a
    managed block with explicit inputs from step 1.1, rewritten by `install-hook`),
    `missing`, `not-git`.
    """
    repo = Path(repo).expanduser()
    top = git_root(repo) if repo.is_dir() else None
    if top is None:
        return {'state': 'not-git', 'hooks': {}, 'missing': list(HOOK_NAMES)}
    raw = _git(top, 'rev-parse', '--git-path', 'hooks', check=False)
    directory = (top / os.fsdecode(raw.stdout.strip())).resolve() if raw.returncode == 0 else top / '.git' / 'hooks'
    _, _, refusal = hooks_dir(top)
    hooks = {}
    for name in HOOK_NAMES:
        path = directory / name
        try:
            text = path.read_text(encoding='utf-8') if path.is_file() else None
        except (OSError, UnicodeDecodeError):
            hooks[name] = 'unreadable'
            continue
        match = _BLOCK_RE.search(text) if text else None
        if match:
            hooks[name] = 'legacy' if any(item in match.group(0) for item in _LEGACY_INPUTS) else 'managed'
        elif text and any(_LEAKCHECK_CALL.search(line) for line in text.splitlines()
                          if line.strip() and not line.lstrip().startswith('#')):
            hooks[name] = 'call'
        else:
            hooks[name] = 'missing'
    missing = [name for name, value in hooks.items() if value in ('missing', 'unreadable')]
    state = 'missing' if missing else ('legacy' if 'legacy' in hooks.values() else 'ok')
    result = {'state': state, 'hooks': hooks, 'missing': missing, 'directory': str(directory)}
    if refusal and missing:
        result['reason'] = refusal
    return result


def _leakcheck_prefix(line):
    """Tokens before `leakcheck` of a hook line that calls skillsync (the self command), else None."""
    if not _LEAKCHECK_CALL.search(line):
        return None
    try:
        tokens = shlex.split(line, comments=False, posix=True)
    except ValueError:
        return None
    if 'leakcheck' not in tokens:
        return None
    return tokens[:tokens.index('leakcheck')]


def hook_command_problems(repo):
    """Hook lines of `repo` whose command cannot run: `[{hook, kind, path}]` (read-only).

    `kind` is `interpreter` (first word) or `file` (an absolute path among the other words, e.g. the
    wrapper). A bare first word counts as missing when it is not on PATH. Typical cause: the hook was
    written for an installation that was removed (`uv tool uninstall`) or a checkout that moved (K8).
    """
    repo = Path(repo).expanduser()
    top = git_root(repo) if repo.is_dir() else None
    if top is None:
        return []
    raw = _git(top, 'rev-parse', '--git-path', 'hooks', check=False)
    directory = (top / os.fsdecode(raw.stdout.strip())).resolve() if raw.returncode == 0 else top / '.git' / 'hooks'
    problems = []
    for name in HOOK_NAMES:
        try:
            text = (directory / name).read_text(encoding='utf-8') if (directory / name).is_file() else ''
        except (OSError, UnicodeDecodeError):
            continue
        seen = set()
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            prefix = _leakcheck_prefix(line)
            if not prefix:
                continue
            for index, word in enumerate(prefix):
                if index == 0:
                    absolute = os.path.isabs(word) or bool(re.match(r'^[A-Za-z]:[\\/]', word))
                    missing = (not Path(word).exists()) if absolute else (shutil.which(word) is None)
                    kind = 'interpreter'
                elif os.path.isabs(word) or re.match(r'^[A-Za-z]:[\\/]', word):
                    missing, kind = not Path(word).exists(), 'file'
                else:
                    continue
                if missing and (name, kind, word) not in seen:
                    seen.add((name, kind, word))
                    problems.append({'hook': name, 'kind': kind, 'path': word})
    return problems


def install_hook(repo, *, remove=False, identity_files=(), targets_json=None, target=None, trailer_allow=()):
    """Install (or remove) the managed block in local `pre-commit` and `pre-push` hooks."""
    report = {'schema': SCHEMA, 'status': 'BLOCKED', 'hooks': []}
    try:
        for item in identity_files:
            load_identity(item)
        if targets_json:
            validate_targets(_read_yaml(targets_json, 'Targets file'))
        top, directory, refusal = hooks_dir(repo)
        report['repo'] = str(top)
        if refusal:
            report.update(status='NOT_RUN', reason=refusal, info=True)
            return report
        directory.mkdir(parents=True, exist_ok=True)
        for name in HOOK_NAMES:
            path = directory / name
            entry = {'name': name, 'path': str(path)}
            if path.is_symlink():
                entry['action'] = 'skipped'
                entry['reason'] = 'existing hook is a symlink (shared hook); add the call manually'
                report['hooks'].append(entry)
                continue
            try:
                existing = path.read_text(encoding='utf-8') if path.is_file() else None
            except UnicodeDecodeError:
                raise LeakguardError(f'Existing {name} hook is not readable as UTF-8; not modified: {path}') from None
            if remove:
                if existing is None or not _BLOCK_RE.search(existing):
                    entry['action'] = 'absent'
                else:
                    rest = _BLOCK_RE.sub('', existing, count=1)
                    if rest.strip() in ('', '#!/bin/sh'):
                        path.unlink()
                        entry['action'] = 'deleted'
                    else:
                        _write_atomic(path, rest, stat.S_IMODE(path.stat().st_mode))
                        entry['action'] = 'removed'
                report['hooks'].append(entry)
                continue
            block = hook_block(name, identity_files, targets_json, target, trailer_allow)
            if existing is None:
                _write_atomic(path, '#!/bin/sh\n' + block, 0o755)
                entry['action'] = 'created'
            elif _BLOCK_RE.search(existing):
                updated = _BLOCK_RE.sub(lambda _: block, existing, count=1)
                if updated != existing:
                    _write_atomic(path, updated, stat.S_IMODE(path.stat().st_mode))
                entry['action'] = 'updated' if updated != existing else 'unchanged'
            else:
                first, _, rest = existing.partition('\n')
                if not first.startswith('#!') or not re.search(r'\b(ba|da|k|z)?sh\b', first):
                    entry['action'] = 'skipped'
                    entry['reason'] = 'existing hook is not a POSIX shell script; add the call manually'
                    report['hooks'].append(entry)
                    continue
                _write_atomic(path, f'{first}\n{block}{rest}', stat.S_IMODE(path.stat().st_mode))
                entry['action'] = 'inserted'
            report['hooks'].append(entry)
        skipped = [entry for entry in report['hooks'] if entry['action'] == 'skipped']
        report['status'] = 'NOT_RUN' if skipped and len(skipped) == len(report['hooks']) else 'PASS'
        if skipped:
            report['info'] = True
    except (LeakguardError, OSError) as exc:
        report['status'] = 'BLOCKED'
        report['reason'] = str(exc)
    return report


# --------------------------------------------------------------------------- CLI

def _add_inputs(parser):
    parser.add_argument('--target', help='Target id (default: the remote matched against leakguard.targets)')
    # D200: inputs for ops tools and diagnostics (explicit identity files, targets file, trailer addresses):
    # they work as before but are not shown in --help; normal use reads them from the machine config.
    parser.add_argument('--targets-json', help=argparse.SUPPRESS)          # JSON/YAML {id: {scope, authors, remote_match, kind}}
    parser.add_argument('--identity', action='append', default=[], help=argparse.SUPPRESS)       # identity.yaml (repeatable)
    parser.add_argument('--trailer-allow', action='append', default=[], help=argparse.SUPPRESS)  # Co-Authored-By address


def _print(report, json_output, title):
    if json_output:
        print(json.dumps(redact(report), ensure_ascii=False, indent=2))
        return exit_code(report)
    stream = sys.stdout if report['status'] in ('PASS', 'NOT_RUN') else sys.stderr
    line = f'{title}: {report["status"]}'
    if 'coverage' in report:
        line += f' (coverage: {report["coverage"]}, target: {report.get("target") or "-"})'
    print(line, file=stream)
    if report.get('reason'):
        prefix = '[info] ' if report.get('info') else ''
        print(f'  {prefix}{report["reason"]}', file=stream)
    for finding in report.get('findings', []):
        print(f'  {finding["path"]} {finding["location"]} [{finding["category"]}]', file=stream)
    for hook in report.get('hooks', []):
        print(f'  {hook["name"]}: {hook["action"]} ({hook["path"]})', file=stream)
        if hook.get('reason'):
            print(f'    [info] {hook["reason"]}', file=stream)
    for warning in report.get('warnings', []):
        print(f'  [warn] {warning}', file=stream)
    for notice in report.get('notices', []):
        print(f'  [info] {notice["path"]} {notice["category"]}', file=stream)
    return exit_code(report)


def _targets_from(args):
    if not args.targets_json:
        return None
    return _read_yaml(args.targets_json, 'Targets file')


def _explicit_inputs(args):
    return bool(args.identity or args.targets_json or args.trailer_allow)


def config_inputs(path=None, target=None):
    """Guard inputs from the machine config (`layers.leakguard_inputs`); raises LeakguardError."""
    from .layers import LayerError, leakguard_inputs
    try:
        return leakguard_inputs(path=path, target=target)
    except LayerError as exc:
        raise LeakguardError(f'Machine config: {exc}') from None


def _read_text_input(args):
    """`[(label, text)]` for `--text FILE` / `--stdin`; raises LeakguardError. The label is the file name, never the text."""
    if args.from_stdin:
        label = '<stdin>'
        try:
            data = sys.stdin.buffer.read(TEXT_LIMIT_BYTES + 1)
        except (AttributeError, OSError, ValueError):
            raise LeakguardError('Cannot read the text from standard input') from None
    else:
        try:
            source = Path(args.text_file).expanduser()
            label = source.name or 'text'
            if not source.is_file():
                raise LeakguardError(f'Not a file: {label}')
            with open(source, 'rb') as handle:
                data = handle.read(TEXT_LIMIT_BYTES + 1)
        except (OSError, RuntimeError) as exc:
            raise LeakguardError(f'Cannot read the text file: {getattr(exc, "strerror", None) or type(exc).__name__}') from None
    if len(data) > TEXT_LIMIT_BYTES:
        raise LeakguardError(f'Text is larger than {TEXT_LIMIT_BYTES} bytes')
    if b'\0' in data[:8192]:
        raise LeakguardError('The text looks binary; leakcheck --text expects a text file')
    try:
        text = data.decode('utf-8-sig')   # strict: a text in another encoding would hide identities with diacritics
    except UnicodeDecodeError:
        raise LeakguardError('The text is not valid UTF-8; convert it before the check') from None
    if not text.strip():
        raise LeakguardError('The text is empty: nothing to check (a failed producer must not pass the gate)')
    return [(label, text)]


def _cmd_leakcheck(args):
    text_mode = args.text_file is not None or args.from_stdin   # `--text ''` is still the text mode (then BLOCKED), never a worktree scan
    mode = 'text' if text_mode else ('range' if args.rev_range else (args.mode or 'worktree'))
    target, warnings, select = args.target, [], True
    texts = None
    try:
        if text_mode:
            texts = _read_text_input(args)
        if _explicit_inputs(args) or args.no_config:
            targets, identity_files, trailer_allow = _targets_from(args), args.identity, args.trailer_allow
        else:
            # No explicit inputs: identities, targets and trailer allowlist from the machine config.
            inputs = config_inputs(args.path, args.target)
            targets, identity_files = inputs['targets'], inputs['identity_files']
            trailer_allow, target, warnings = inputs['trailer_allow'], inputs['target'], inputs['warnings']
            select = False  # the target was chosen by `layers.leakguard_inputs` (single source)
    except LeakguardError as exc:
        report = {'schema': SCHEMA, 'status': 'BLOCKED', 'coverage': 'generic', 'target': args.target,
                  'findings': [], 'reason': str(exc)}
        return _print(report, args.json, 'leakcheck')
    report = leakcheck(args.path, mode=mode, rev_range=args.rev_range, target=target, targets=targets,
                       identity_files=identity_files, target_patterns=args.target_patterns,
                       require=args.require, trailer_allow=trailer_allow, select=select, texts=texts)
    report['warnings'] = warnings + report.get('warnings', [])
    return _print(report, args.json, 'leakcheck')


def target_patterns_report(target, target_patterns=None, patterns_file=None):
    """`leakguard patterns --target ID`: builtin patterns merged with the target's additions.

    Additions: `target_patterns` when given, else `.leakguard/patterns.yaml` in the git root
    of the checkout of layer `target`. They are validated against the identities of the
    other local layers (D37). Values of identities are never part of the report.
    """
    report = {'schema': SCHEMA, 'status': 'BLOCKED', 'patterns': [], 'allow': [],
              'path_class_rules': PATH_CLASS_RULES, 'target': target, 'warnings': []}
    try:
        from .layers import LayerError, load_machine_config, target_patterns_file
        try:
            config = load_machine_config()
        except LayerError as exc:
            raise LeakguardError(f'Machine config: {exc}') from None
        inputs = config_inputs(None, target)
        targets = inputs['targets'] or {}
        if target not in targets:
            report['warnings'].append(f'Target {target} is unknown in leakguard targets; no target additions')
        prepared = prepare(target=target, targets=targets or None, identity_files=inputs['identity_files'],
                           patterns_file=patterns_file)
        try:
            additions = target_patterns or target_patterns_file(config, target)
        except LayerError as exc:  # D73: a linked target additions file is BLOCKED
            raise LeakguardError(str(exc)) from None
        merged = patterns_report(additions, patterns_file, prepared.matcher)
        report.update({key: merged[key] for key in ('status', 'patterns', 'allow')})
        if merged.get('reason'):
            report['reason'] = merged['reason']
        report['target_patterns'] = bool(additions)
    except LeakguardError as exc:
        report['status'] = 'BLOCKED'
        report['reason'] = str(exc)
    return report


def _cmd_patterns(args):
    if args.target:
        return _print(target_patterns_report(args.target, args.target_patterns), True, 'leakguard patterns')
    return _print(patterns_report(args.target_patterns), True, 'leakguard patterns')


def _cmd_install_hook(args):
    report = install_hook(args.repo, remove=args.remove, identity_files=args.identity,
                          targets_json=args.targets_json, target=args.target, trailer_allow=args.trailer_allow)
    return _print(report, args.json, 'leakguard install-hook')


def register(subparsers):
    check = subparsers.add_parser('leakcheck', help='Scan content, file names and commits for leaks')
    check.add_argument('path', nargs='?', default='.')
    modes = check.add_mutually_exclusive_group()
    modes.add_argument('--worktree', dest='mode', action='store_const', const='worktree',
                       help='Files in the working tree (default)')
    modes.add_argument('--staged', dest='mode', action='store_const', const='staged', help='Staged files')
    modes.add_argument('--range', dest='rev_range', metavar='A..B',
                       help='Commits in a range (a single revision = its whole history)')
    modes.add_argument('--history', dest='mode', action='store_const', const='history',
                       help='Whole history of HEAD')
    modes.add_argument('--text', dest='text_file', metavar='FILE',
                       help='A text outside git (a PR or release description written to a file); never pass the text itself')
    modes.add_argument('--stdin', dest='from_stdin', action='store_true',
                       help='Scan the text read from standard input (a PR or release description)')
    _add_inputs(check)
    check.add_argument('--target-patterns', help=argparse.SUPPRESS)   # D200; default: <git root>/.leakguard/patterns.yaml
    check.add_argument('--require', choices=['full'], help='BLOCKED unless coverage is full')
    check.add_argument('--no-config', action='store_true',
                       help='Do not read identities/targets from the machine config (generic patterns only)')
    check.add_argument('--json', action='store_true')
    check.set_defaults(func=_cmd_leakcheck, mode=None, text_file=None, from_stdin=False)

    guard = subparsers.add_parser('leakguard', help='Leak guard patterns and git hooks')
    actions = guard.add_subparsers(dest='leakguard_command', required=True)
    patterns = actions.add_parser('patterns', help='Merged builtin and target patterns as JSON')
    patterns.add_argument('--target-patterns', help=argparse.SUPPRESS)   # D200: target additions file to merge
    patterns.add_argument('--target', help='Target id: merge its additions (.leakguard/patterns.yaml of the layer checkout)')
    patterns.add_argument('--json', action='store_true', help='JSON output (the only format)')
    patterns.set_defaults(func=_cmd_patterns)
    hook = actions.add_parser('install-hook', help='Managed block in local pre-commit and pre-push hooks')
    hook.add_argument('repo')
    hook.add_argument('--remove', action='store_true', help='Remove the managed block')
    _add_inputs(hook)
    hook.add_argument('--json', action='store_true')
    hook.set_defaults(func=_cmd_install_hook)
