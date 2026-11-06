#!/usr/bin/env python3
"""Check that __version__ (or the given version/tag) has a section in CHANGELOG.md.

Usage: check_release.py [--version X.Y.Z | --tag vX.Y.Z] [--root DIR]
Exit 0 = OK, 1 = missing section or version mismatch, 2 = bad argument.
"""
import argparse
import ast
import re
import sys
from pathlib import Path

SEMVER = re.compile(r'\d+\.\d+\.\d+')


class Usage(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f'check_release: {message}', file=sys.stderr)
        sys.exit(2)


def read_version(init_py):
    tree = ast.parse(init_py.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == '__version__' for t in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, str):
                return value
    raise ValueError('no __version__ in ' + str(init_py))


def main(argv):
    parser = Usage(prog='check_release.py')
    parser.add_argument('--version')
    parser.add_argument('--tag')
    parser.add_argument('--root', default=str(Path(__file__).resolve().parent.parent))
    args = parser.parse_args(argv)
    if args.version is not None and args.tag is not None:
        parser.error('--version and --tag are mutually exclusive')
    wanted = None
    if args.version is not None:
        wanted = args.version
    elif args.tag is not None:
        if not args.tag.startswith('v'):
            parser.error('--tag must look like vX.Y.Z')
        wanted = args.tag[1:]
    if wanted is not None and not SEMVER.fullmatch(wanted):
        parser.error(f'bad version format: {wanted!r} (expected X.Y.Z)')
    root = Path(args.root)
    try:
        current = read_version(root / 'skillsync_workflow' / '__init__.py')
        changelog = (root / 'CHANGELOG.md').read_text(encoding='utf-8')
    except (OSError, ValueError, SyntaxError) as exc:
        print(f'check_release: {exc}', file=sys.stderr)
        return 1
    if wanted is None:
        wanted = current
    elif wanted != current:
        print(f'check_release: version {wanted} differs from __version__ {current}')
        return 1
    heading = re.compile(r'^## \[' + re.escape(wanted) + r'\](\s|$)', re.MULTILINE)
    if not heading.search(changelog):
        print(f'check_release: CHANGELOG.md has no section "## [{wanted}]"')
        return 1
    print(f'check_release: OK {wanted}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
