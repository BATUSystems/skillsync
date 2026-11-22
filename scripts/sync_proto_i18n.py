#!/usr/bin/env python3
"""Refresh the generated catalog block of `skillsync_workflow/prototype_server.py` from `data/i18n/<lang>.proto.json`.

The prototype server is a standalone module (no imports from the package, copied alone to other hosts), so it cannot read the catalog
files at run time: the texts of the `proto.*` keys are embedded between the markers `# BEGIN GENERATED proto-i18n` and
`# END GENERATED proto-i18n`. Edit the JSON fragments and run this script; never edit the block by hand.
`--check` only compares (exit 1 when the block is stale), as the test `test_skillsync_proto_i18n` does.
"""
import argparse
import json
import sys
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--check', action='store_true', help='exit 1 when the block of the server is stale')
    args = parser.parse_args(argv)
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
