"""Ban on attribution and advertising of AI tools in outgoing text.

One data file (`data/attribution-patterns.json`) holds the patterns; `enforce` names the classes in force (A: footer or advertising,
B: AI co-author trailer). Consumers: leakguard (builtin patterns, category `attribution`), `layers check` and `doctor`
(`scan_text`), and a copy of the file next to the guard hook (the hook is stdlib only, so the data is JSON).
"""
import json
import re
from pathlib import Path

DATA = Path(__file__).resolve().parent / 'data' / 'attribution-patterns.json'
SCHEMA = 1
CLASSES = ('A', 'B')
FLAGS = {'ignorecase': re.IGNORECASE, 'multiline': re.MULTILINE}
RULE_ID = re.compile(r'[a-z0-9-]+')


class AttributionError(ValueError):
    pass


def load(path=None):
    """The parsed and validated data file: `{'enforce': [...], 'patterns': [{id, class, tool, regex, flags, compiled}]}`."""
    path = Path(path or DATA)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise AttributionError(f'Attribution patterns are unreadable: {path} ({type(exc).__name__})') from None
    if not isinstance(data, dict) or data.get('schema') != SCHEMA:
        raise AttributionError(f'Attribution patterns {path}: schema {SCHEMA} required')
    enforce = data.get('enforce')
    if not isinstance(enforce, list) or any(item not in CLASSES for item in enforce):
        raise AttributionError(f'Attribution patterns {path}: `enforce` must be a list of {", ".join(CLASSES)}')
    patterns, seen = [], set()
    for entry in data.get('patterns') or []:
        if not isinstance(entry, dict) or not isinstance(entry.get('id'), str) or not RULE_ID.fullmatch(entry['id']):
            raise AttributionError(f'Attribution patterns {path}: every pattern needs an id matching [a-z0-9-]+')
        if entry['id'] in seen or entry.get('class') not in CLASSES or not isinstance(entry.get('regex'), str) or not entry['regex']:
            raise AttributionError(f'Attribution patterns {path}: invalid or duplicate pattern {entry["id"]}')
        flags = entry.get('flags', [])
        if not isinstance(flags, list) or any(flag not in FLAGS for flag in flags):
            raise AttributionError(f'Attribution patterns {path}: invalid flags in {entry["id"]}')
        compiled_flags = 0
        for flag in flags:
            compiled_flags |= FLAGS[flag]
        try:
            compiled = re.compile(entry['regex'], compiled_flags)
        except re.error as exc:
            raise AttributionError(f'Attribution patterns {path}: invalid regex in {entry["id"]}: {exc}') from None
        seen.add(entry['id'])
        patterns.append({'id': entry['id'], 'class': entry['class'], 'tool': entry.get('tool', ''), 'regex': entry['regex'],
                         'flags': list(flags), 'compiled': compiled})
    return {'enforce': list(enforce), 'patterns': patterns}


def enforced(path=None):
    """The patterns of the classes in force."""
    data = load(path)
    return [pattern for pattern in data['patterns'] if pattern['class'] in data['enforce']]


def scan_text(text, path=None):
    """`[(pattern id, class, line number)]` of the enforced patterns found in `text` (matched line by line)."""
    rules = enforced(path)
    found = []
    for number, line in enumerate(str(text).splitlines(), 1):
        for rule in rules:
            if rule['compiled'].search(line):
                found.append((rule['id'], rule['class'], number))
    return found


def leakguard_entries(path=None):
    """The enforced patterns in the `patterns.yaml` entry form of leakguard (category `attribution`, every surface and path class)."""
    return [{'id': rule['id'], 'category': 'attribution', 'regex': rule['regex'], 'flags': rule['flags'],
             'applies_to': ['content', 'commit']} for rule in enforced(path)]
