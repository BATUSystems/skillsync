#!/usr/bin/env python3
"""Check that a built wheel contains only what K1 allows.

Usage: check_wheel.py <wheel>
Exit 0 = contents match, 1 = extra or missing files (listed), 2 = bad argument.
"""
import re
import sys
import zipfile
from pathlib import PurePosixPath

REQUIRED = [
    'skillsync_workflow/__init__.py',
    'skillsync_workflow/cli.py',
    'skillsync_workflow/__main__.py',
    'skillsync_workflow/selfcmd.py',
]
DATA_PREFIX = 'skillsync_workflow/data/'
DIST_INFO = re.compile(r'^[^/]+\.dist-info/(?:licenses/)?[^/]+$')


def allowed(name):
    if name.endswith('/'):
        return True  # directory entry
    if DIST_INFO.match(name):
        return True
    path = PurePosixPath(name)
    if path.parts[:1] != ('skillsync_workflow',) or '__pycache__' in path.parts:
        return False
    if name.startswith(DATA_PREFIX):
        return len(path.parts) == 3
    return path.suffix == '.py'


def main(argv):
    if len(argv) != 2 or argv[1] in ('-h', '--help'):
        print('usage: check_wheel.py <wheel>', file=sys.stderr)
        return 2
    try:
        with zipfile.ZipFile(argv[1]) as wheel:
            names = wheel.namelist()
    except (OSError, zipfile.BadZipFile) as exc:
        print(f'check_wheel: cannot read {argv[1]}: {exc}', file=sys.stderr)
        return 2
    extra = sorted(n for n in names if not allowed(n))
    missing = [r for r in REQUIRED if r not in names]
    if not any(n.startswith(DATA_PREFIX) and not n.endswith('/') for n in names):
        missing.append(DATA_PREFIX + '*')
    for name in extra:
        print(f'extra: {name}')
    for name in missing:
        print(f'missing: {name}')
    if extra or missing:
        return 1
    print(f'check_wheel: OK ({len(names)} entries)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
