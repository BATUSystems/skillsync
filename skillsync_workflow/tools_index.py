"""Tool index (`tools/index.yaml` of a layer, `docs/tools/index.yaml` of a project): schema 1, parsing and validation (CORE-1140-SUP-TOOLS).

A record points at a script an agent can reuse instead of writing a new one (`skillsync route` offers it, `skillsync doctor` checks the file). The texts
of a record come from a layer that may be org or personal, so they are DATA: this module only reads, validates and cuts them, nothing is run or
passed to a shell. The reader is tolerant: a bad record or file is skipped and reported (`tools-index-invalid`), it never raises for the caller,
because a broken index of tools must not block the lessons and domain knowledge of `route` or the whole `doctor`.

Record (the layer is not written: it follows from where the file is):

    schema: 1                               # `version: 1` is read as well; if both are given they must be equal
    tools:
      - id: ui-parity-measure                 # [a-z0-9][a-z0-9-]*, unique in the file
        path: skills/ui-verify/scripts/measure.mjs   # relative to the root of the layer (project: of the repository)
        purpose: one sentence                 # required
        topics: [ui, parity]                  # required, may be empty (then `files` or --tools-all)
        usage: "node measure.mjs --config <file> <outDir>"   # required, text; a relative path is relative to the script's directory (project index: to the repository root)
        files: ["**/*.css"]                   # optional: when `route` offers the tool for these files
        selftest: "node measure.mjs --selftest"       # optional, text (never run by the generator)
        limits: ["does not measure animations"]       # optional
        status: active                        # active | candidate | superseded | removed (default active)
        supersedes: []                        # ids of tools of lower layers this record replaces
        origin: "text"                        # optional
"""
import re
import unicodedata
from pathlib import Path

import yaml

SCHEMA = 1
MAX_FILE_BYTES = 256 * 1024   # 500 records of a realistic size fit; a bigger file costs seconds of pure-Python YAML parsing
MAX_RECORDS = 500
MAX_TEXT = 2000
MAX_TOPICS = 30
MAX_TOPIC = 60
MAX_FILES = 20
MAX_PATTERN = 200
MAX_PATH = 300   # a path or a pattern of a path: control, format (bidi, zero width) and separator characters are refused
MAX_LIMITS = 30
MAX_SUPERSEDES = 20
STATUSES = ('active', 'candidate', 'superseded', 'removed')
ID_RE = re.compile(r'[a-z0-9][a-z0-9-]*')
_DRIVE = re.compile(r'[A-Za-z]:')
INDEX_PATH = 'tools/index.yaml'
PROJECT_INDEX_PATH = 'docs/tools/index.yaml'


def clean_text(value, limit=300):
    """A text from a layer for display: control characters and ANSI escape sequences removed, cut to `limit` characters (an ellipsis marks the cut)."""
    text = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-Z\\-_]', '', str(value))
    text = ''.join(' ' if char in '\r\n\t' else char for char in text)
    text = ''.join(char for char in text if unicodedata.category(char)[0] != 'C')
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def safe_relative(value):
    """True for a relative path of the layer or project: no `..`, no leading separator, no drive, no control characters."""
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.replace('\\', '/')
    return not (len(value) > MAX_PATH or any(unicodedata.category(char)[0] == 'C' for char in value) or text.startswith('/')
                or _DRIVE.match(text) or '..' in text.split('/'))


def _text(record, key, label, *, required=False):
    value = record.get(key)
    if value is None:
        if required:
            raise ValueError(f'{label}: `{key}` is required')
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        raise ValueError(f'{label}: `{key}` must be a text of 1 to {MAX_TEXT} characters')
    return value


def _word_list(record, key, label, maximum, longest):
    value = record.get(key, [])
    if value is None:
        return []
    if (not isinstance(value, list) or len(value) > maximum
            or not all(isinstance(item, str) and item.strip() and len(item) <= longest for item in value)):
        raise ValueError(f'{label}: `{key}` must be a list of at most {maximum} texts of at most {longest} characters')
    return list(value)


def parse_record(raw, label):
    """One normalized record; ValueError (the reason) for a bad one."""
    if not isinstance(raw, dict):
        raise ValueError(f'{label} must be a mapping')
    record_id = raw.get('id')
    if not isinstance(record_id, str) or not ID_RE.fullmatch(record_id):
        raise ValueError(f'{label}: `id` must match [a-z0-9][a-z0-9-]*')
    label = f'{label} ({record_id})'
    path = raw.get('path')
    if not safe_relative(path):
        raise ValueError(f'{label}: `path` must be a path relative to the root, without `..`, a drive or a leading separator')
    if 'topics' not in raw:
        raise ValueError(f'{label}: `topics` is required (it may be an empty list)')
    status = raw.get('status', 'active')
    if status not in STATUSES:
        raise ValueError(f'{label}: `status` must be one of: {", ".join(STATUSES)}')
    files = _word_list(raw, 'files', label, MAX_FILES, MAX_PATTERN)
    if not all(safe_relative(pattern) for pattern in files):
        raise ValueError(f'{label}: `files` must be relative patterns')
    supersedes = _word_list(raw, 'supersedes', label, MAX_SUPERSEDES, MAX_PATTERN)
    if not all(ID_RE.fullmatch(item) for item in supersedes):
        raise ValueError(f'{label}: `supersedes` must be a list of tool ids')
    return {'id': record_id, 'path': path.replace('\\', '/'), 'purpose': _text(raw, 'purpose', label, required=True),
            'topics': _word_list(raw, 'topics', label, MAX_TOPICS, MAX_TOPIC), 'files': files,
            'usage': _text(raw, 'usage', label, required=True), 'selftest': _text(raw, 'selftest', label),
            'limits': _word_list(raw, 'limits', label, MAX_LIMITS, MAX_TEXT), 'status': status, 'supersedes': supersedes,
            'origin': _text(raw, 'origin', label)}


def parse_index(text, source='tools/index.yaml'):
    """`(records, errors)` of the text of an index file: the valid records in file order and one message per problem (a bad record is skipped; a
    duplicate id keeps the first; a file that is not a schema 1 mapping gives no records and one message). Never raises."""
    try:
        data = yaml.safe_load(text)
    except Exception:   # not only YAMLError: a bad date, a bad tag or a deep nesting (RecursionError) raise other types; the reader never raises
        return [], [f'{source}: not valid YAML']
    if data is None:
        return [], []
    if not isinstance(data, dict):
        return [], [f'{source}: must be a mapping with `schema` and `tools`']
    schema = data.get('schema', data.get('version'))   # `version` is read as an alias: the other indexes of the generator (lessons, domain) use it
    if 'schema' in data and 'version' in data and data['schema'] != data['version']:
        return [], [f'{source}: `schema` and `version` differ; give one of them']
    if isinstance(schema, bool) or not isinstance(schema, int):
        return [], [f'{source}: requires integer `schema` (or `version`)']
    if schema > SCHEMA:
        return [], [f'{source}: uses a newer `schema` ({schema}); this generator reads schema {SCHEMA}; the file is ignored']
    if schema != SCHEMA:
        return [], [f'{source}: unsupported `schema` {schema}']
    items = data.get('tools')
    if items is None:
        return [], []
    if not isinstance(items, list):
        return [], [f'{source}: `tools` must be a list']
    records, errors, seen = [], [], set()
    for index, raw in enumerate(items):
        label = f'{source} tools[{index}]'
        if index >= MAX_RECORDS:
            errors.append(f'{label}: more than {MAX_RECORDS} records; the rest is ignored')
            break
        try:
            record = parse_record(raw, label)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if record['id'] in seen:
            errors.append(f'{label} ({record["id"]}): the id is already used earlier in the file; this record is ignored')
            continue
        seen.add(record['id'])
        records.append(record)
    return records, errors


def read_index_file(path, source=None):
    """`(records, errors)` of an index file on disk (a regular file of at most `MAX_FILE_BYTES`); a missing file is `([], [])`. Never raises."""
    path = Path(path)
    source = source or str(path)
    try:
        if path.is_symlink():
            return [], [f'{source}: is a symlink; the index is not read']
        if not path.is_file():
            return [], []
        if path.stat().st_size > MAX_FILE_BYTES:
            return [], [f'{source}: larger than {MAX_FILE_BYTES} bytes; the file is ignored']
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError, ValueError):
        return [], [f'{source}: unreadable']
    return parse_index(text, source)
