"""Reference reader of `patterns.yaml` (spec K7, "Zasady dla czytelników", D52).

The reader below is a minimal, independent implementation written from the spec text only
(standard library + PyYAML, like `docs/site/build.py`). It does not import the guard. The
test checks that it and `skillsync leakcheck` produce the same content hits on a shared
fixture, both when reading the YAML files and when reading `leakguard patterns --json`.
"""
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import PurePosixPath
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from layers_helpers import IsolatedTestCase, write  # noqa: E402
from skillsync_workflow import leakguard  # noqa: E402

HOME = '/ho' + 'me'
AT = '@'
USERS = 'Us' + 'ers'
ROOT = Path(leakguard.__file__).resolve().parent.parent
from cli_module import SKILLSYNC_PATH  # noqa: E402

SCRIPT = SKILLSYNC_PATH  # wrapper; processes run [sys.executable, SCRIPT]
RESOURCE = ROOT / 'skillsync_workflow' / 'data' / 'leakguard-patterns.yaml'

D27 = 'Open' + ' ' + 'Mer' + 'cato'
D27_ICON = 'Lu' + 'cide'
FLAG_VALUES = {'ignorecase': re.IGNORECASE, 'multiline': re.MULTILINE}
ALL_SURFACES = ['content', 'filename', 'commit']
ALL_CLASSES = ['instrukcje', 'atrybucje', 'inne']


# ----------------------------------------------------------------- the reference reader

class ReaderError(Exception):
    pass


def read_document(data, supported=1):
    """Entries of one `patterns.yaml` document with defaults applied (spec: reader rules)."""
    if not isinstance(data, dict) or not isinstance(data.get('schema'), int):
        raise ReaderError('schema missing')
    if data['schema'] > supported:
        raise ReaderError('schema too new')
    entries = {'patterns': [], 'allow': []}
    for key in entries:
        for raw in data.get(key) or []:
            flags = 0
            for name in raw.get('flags', []):
                flags |= FLAG_VALUES[name]          # anything else is an error (KeyError)
            entries[key].append({
                'id': raw['id'],
                'category': raw.get('category'),
                'regex': re.compile(raw['regex'], flags),
                'applies_to': raw.get('applies_to', ALL_SURFACES),
                'path_classes': raw.get('path_classes', ALL_CLASSES),
                'paths': raw.get('paths', []),          # empty = all paths
            })
    return entries


def read_files(*paths):
    merged = {'patterns': [], 'allow': []}
    for path in paths:
        document = read_document(yaml.safe_load(Path(path).read_text(encoding='utf-8')))
        for key in merged:
            merged[key].extend(document[key])
    return merged


def read_json_report(report):
    """Same structure from `leakguard patterns --json` (regex strings already in the report)."""
    data = {'schema': report['schema'], 'patterns': report['patterns'], 'allow': report['allow']}
    return read_document(data)


def klass(rel):
    """Path class from the spec table (K8), content surface."""
    parts = PurePosixPath(rel).parts
    name = parts[-1]
    if any(part in ('instructions', 'skills', 'agents', '_base.d') for part in parts[:-1]):
        return 'instrukcje'
    if parts[0] in ('tests', '.github') and len(parts) > 1:
        return 'inne'
    if name.endswith(('.py', '.ts', '.js', '.mjs', '.cjs', '.ps1', '.sh')) or name in ('skillsync', 'skillsync-ctl'):
        return 'instrukcje'     # code outside tests/ and .github/ (D238)
    if parts[0] == 'docs' and len(parts) > 1:
        return 'atrybucje'
    if any(fnmatch.fnmatchcase(name, glob) for glob in ('CHANGELOG*', 'README*', 'LICENSE*', 'NOTICE*')):
        return 'atrybucje'
    if len(parts) == 1:
        return 'inne'
    return 'instrukcje'     # unclassified = strictest


def applies(entry, rel):
    if 'content' not in entry['applies_to'] or klass(rel) not in entry['path_classes']:
        return False
    return not entry['paths'] or any(fnmatch.fnmatchcase(rel, g) or fnmatch.fnmatchcase(PurePosixPath(rel).name, g)
                                     for g in entry['paths'])


def reader_hits(files, entries):
    """{(path, 'line:column', category)} for content hits, `allow` by containment."""
    found = set()
    for rel, text in files.items():
        for number, line in enumerate(text.splitlines(), 1):
            spans = [m.span() for a in entries['allow'] if applies(a, rel) for m in a['regex'].finditer(line)]
            for pattern in entries['patterns']:
                if not applies(pattern, rel):
                    continue
                for match in pattern['regex'].finditer(line):
                    if match.end() == match.start():
                        continue
                    if any(a <= match.start() and match.end() <= b for a, b in spans):
                        continue
                    found.add((rel, f'{number}:{match.start() + 1}', pattern['category']))
    return found


# ----------------------------------------------------------------- shared fixture

FIXTURE = {
    'skills/a.md': '\n'.join([
        f'uses {D27} in a sentence',
        f'path {HOME}/alice/x and placeholder {HOME}/user/y',
        f'partial {HOME}/user/a then {HOME}/alice/b on one line',
        f'mail jan{AT}corp.test or jan{AT}example.com',
        f'icon {D27_ICON}, word {D27_ICON}ity is fine',
        f'C:\\{USERS}\\alice\\proj',
        'host-77 appears here',
        '',
    ]),
    'docs/b.md': f'host-88 in docs\nsee {HOME}/<user>/proj and {HOME}/bob/proj\nx{AT}ci.invalid y{AT}host.example z{AT}acme.test\n',
    'LICENSE': 'Copyright (c) 2026 BATUSystems\nCopyright (c) 2026 Somebody Else\n',
    'tests/c.txt': f'host-99 in tests\n{HOME}/runner/work and {HOME}/carol/work\n',
    'src/d.py': f'# {D27.upper()} twice {D27.lower()}\nx = "noreply{AT}anthropic.com"\nhost-55 in code\n',
    'misc/e.dat': f'host-11 {HOME}/dave/x\n',
}

ADDITIONS = {
    'schema': 1,
    'future-key': {'ignored': True},
    'patterns': [
        {'id': 'host-instr', 'category': 'custom', 'regex': r'host-\d+', 'path_classes': ['instrukcje']},
        {'id': 'host-attr-ci', 'category': 'machine', 'regex': r'HOST-\d+', 'flags': ['ignorecase'],
         'path_classes': ['atrybucje'], 'paths': ['docs/*', '*.py']},
    ],
    'allow': [
        {'id': 'host-77-ok', 'regex': r'host-77', 'paths': ['skills/*']},
    ],
}


class ReferenceReaderTest(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.additions = self.root / 'additions.yaml'
        self.additions.write_text(yaml.safe_dump(ADDITIONS), encoding='utf-8')
        self.tree = self.work / 'tree'
        for rel, text in FIXTURE.items():
            write(self.tree / rel, text)

    def leakcheck_hits(self):
        report = leakguard.leakcheck(self.tree, target_patterns=self.additions)
        self.assertIn(report['status'], ('PASS', 'FAIL'), report)
        return {(item['path'], item['location'], item['category'])
                for item in report['findings'] if not item['location'].startswith('filename')}

    def test_fixture_is_discriminating(self):
        hits = reader_hits(FIXTURE, read_files(RESOURCE, self.additions))
        categories = {item[2] for item in hits}
        self.assertEqual(categories, {'d27', 'owner-path', 'email', 'custom', 'machine'})
        paths = {item[0] for item in hits}
        self.assertEqual(paths, {'skills/a.md', 'docs/b.md', 'tests/c.txt', 'src/d.py', 'misc/e.dat'})
        self.assertEqual(len(hits), 16)

    def test_reader_and_leakcheck_agree_on_the_yaml_files(self):
        expected = reader_hits(FIXTURE, read_files(RESOURCE, self.additions))
        self.assertEqual(self.leakcheck_hits(), expected)

    def test_reader_on_patterns_json_agrees_too(self):
        process = subprocess.run([sys.executable, str(SCRIPT), 'leakguard', 'patterns', '--json',
                                  '--target-patterns', str(self.additions)], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        from_json = reader_hits(FIXTURE, read_json_report(json.loads(process.stdout)))
        self.assertEqual(from_json, reader_hits(FIXTURE, read_files(RESOURCE, self.additions)))
        self.assertEqual(from_json, self.leakcheck_hits())

    def test_specific_decisions_of_the_shared_rules(self):
        hits = self.leakcheck_hits()
        cell = {(path, line.split(':')[0]) for path, line, _ in hits}
        self.assertNotIn(('skills/a.md', '7'), cell)         # allow `host-77` limited to skills/*
        self.assertIn(('skills/a.md', '3'), cell)            # allow covers only part of the line
        self.assertIn(('skills/a.md', '5'), cell)            # word-bounded: `...ity` is fine, the icon name is not
        self.assertNotIn(('LICENSE', '1'), cell)             # publisher line, LICENSE only
        self.assertNotIn(('tests/c.txt', '1'), cell)         # pattern limited to instrukcje/atrybucje
        self.assertIn(('misc/e.dat', '1'), cell)             # unknown path = instrukcje
        self.assertIn(('src/d.py', '2'), cell)               # the noreply allow is commit-scoped, not for content
        # D238: code is `instrukcje` - the `instrukcje` pattern hits it, the `atrybucje` one (paths `*.py`) does not
        self.assertIn(('src/d.py', '3:1', 'custom'), hits)
        self.assertNotIn(('src/d.py', '3:1', 'machine'), hits)

    def test_both_readers_refuse_a_newer_schema(self):
        newer = self.root / 'newer.yaml'
        newer.write_text('schema: 2\npatterns: []\n', encoding='utf-8')
        with self.assertRaises(ReaderError):
            read_files(newer)
        self.assertEqual(leakguard.patterns_report(newer.as_posix())['status'], 'BLOCKED')
        blocked = leakguard.leakcheck(self.tree, target_patterns=newer)
        self.assertEqual(blocked['status'], 'BLOCKED')

    def test_reader_would_notice_a_divergence(self):
        # the comparison is not vacuous: dropping one allow entry changes the reader's result
        entries = read_files(RESOURCE, self.additions)
        full = reader_hits(FIXTURE, entries)
        reduced = dict(entries, allow=[a for a in entries['allow'] if a['id'] != 'home-placeholders'])
        self.assertNotEqual(reader_hits(FIXTURE, reduced), full)


if __name__ == '__main__':
    import unittest
    unittest.main()
