#!/usr/bin/env python3
"""Refresh the generated catalog block of `skillsync_workflow/prototype_server.py` from `data/i18n/<lang>.proto.json`.

The prototype server is a standalone module (no imports from the package, copied alone to other hosts), so it cannot read the catalog
files at run time: the texts of the `proto.*` keys are embedded between the markers `# BEGIN GENERATED proto-i18n` and
`# END GENERATED proto-i18n`. Edit the JSON fragments and run this script; never edit the block by hand.
`--check` only compares (exit 1 when the block is stale), as the test `test_skillsync_proto_i18n` does.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG_DIR = ROOT / 'skillsync_workflow' / 'data' / 'i18n'
SERVER = ROOT / 'skillsync_workflow' / 'prototype_server.py'
LANGUAGES = ('pl', 'en')
BEGIN = '# BEGIN GENERATED proto-i18n'
END = '# END GENERATED proto-i18n'
HEADER = ('# Generated from skillsync_workflow/data/i18n/{pl,en}.proto.json by scripts/sync_proto_i18n.py.\n'
          '# Edit the JSON files and run the script; do not change this block by hand (a test compares it with the files).\n')


def fragments():
    return {lang: json.loads((CATALOG_DIR / f'{lang}.proto.json').read_text(encoding='utf-8')) for lang in LANGUAGES}


def block(data=None):
    """The text between the markers (markers included), ending with a newline."""
    payload = json.dumps(data if data is not None else fragments(), ensure_ascii=False, indent=1, sort_keys=True)
    if "'''" in payload:
        raise ValueError("a catalog text contains three single quotes; the block is a raw triple-quoted string")
    return f"{BEGIN}\n{HEADER}_PROTO_TEXTS_JSON = r'''{payload}'''\n{END}\n"


def current(text):
    start, end = text.index(BEGIN), text.index(END) + len(END)
    return text[start:end] + '\n'


def merge(path):
    """Add the keys of a file `{"pl": {key: text}, "en": {key: text}}` to the two fragments (an existing key with another text is an error)."""
    new = json.loads(Path(path).read_text(encoding='utf-8'))
    data = fragments()
    for lang in LANGUAGES:
        for key, value in new.get(lang, {}).items():
            if key in data[lang] and data[lang][key] != value:
                raise ValueError(f'{lang}: {key} already has another text')
            data[lang][key] = value
    if sorted(new.get('pl', {})) != sorted(new.get('en', {})):
        raise ValueError('the file has different keys in pl and en')
    texts = {lang: json.dumps(data[lang], ensure_ascii=False, indent=1, sort_keys=True) + '\n' for lang in LANGUAGES}
    temporary = {}
    try:
        for lang in LANGUAGES:   # both are written to temporary files first and renamed together: a reader sees the old pair or the new one
            temporary[lang] = CATALOG_DIR / f'.{lang}.proto.json.tmp'
            temporary[lang].write_text(texts[lang], encoding='utf-8', newline='\n')
        for lang in LANGUAGES:
            os.replace(str(temporary[lang]), str(CATALOG_DIR / f'{lang}.proto.json'))
    finally:
        for path in temporary.values():
            if path.exists():
                path.unlink()


class _Lock(object):
    """Several writers (the parallel assignments of CORE-1130-I18N-PROTO) take turns: an exclusive lock file next to the fragments."""
    path = CATALOG_DIR / '.proto.lock'

    STALE_SECONDS = 120   # a run that was killed leaves the file; a lock this old is not held by anyone

    def __enter__(self):
        deadline = time.time() + 60
        while True:
            try:
                if time.time() - self.path.stat().st_mtime > self.STALE_SECONDS:
                    self.path.unlink()
            except OSError:
                pass
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                if time.time() > deadline:
                    raise TimeoutError('the lock file %s stays; remove it if no other run is active' % self.path)
                time.sleep(0.2)

    def __exit__(self, *exc):
        os.close(self.fd)
        os.unlink(str(self.path))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--check', action='store_true', help='exit 1 when the block of the server is stale')
    parser.add_argument('--merge', metavar='FILE', help='first add the keys of FILE {"pl": {...}, "en": {...}} to the fragments')
    args = parser.parse_args(argv)
    if args.merge:
        with _Lock():
            try:
                merge(args.merge)
            except ValueError as error:
                print(error, file=sys.stderr)
                return 2
            return main([])
    text = SERVER.read_text(encoding='utf-8')
    try:
        wanted = block()
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    if args.check:
        if current(text) != wanted:
            print('prototype_server.py: the generated catalog block is stale; run scripts/sync_proto_i18n.py', file=sys.stderr)
            return 1
        return 0
    if current(text) != wanted:
        SERVER.write_text(text.replace(current(text).rstrip('\n'), wanted.rstrip('\n'), 1), encoding='utf-8', newline='\n')
        print('prototype_server.py: catalog block updated')
    return 0


if __name__ == '__main__':
    sys.exit(main())
