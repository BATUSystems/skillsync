#!/usr/bin/env python3
"""Control grep of K1 (spec A): no module reads the old single library outside the allowlist.

Regex of the spec: `\\[["']library["']\\]|get\\(\\s*["']library|sync\\.library|--library`, over the generator
`skillsync_workflow/*.py` (the body of the CLI is `skillsync_workflow/cli.py`; the root `skillsync` is a thin wrapper). Every hit `file:line` is mapped (with `ast`) to the
innermost enclosing function/class (`Class.method`; `<module>` outside any definition) and checked against
the allowlist `file:qualname-glob` from the plan (TESTY, "Grep K1"). Comments and strings count: a message
naming the old key must avoid the literal or have its function allowlisted.

Exit code: 0 = no hit outside the allowlist, 1 = violations (listed), 2 = cannot scan (missing/unparsable file).
Usage: python3 tests/k1_grep.py [--root DIR] [--json]
"""
import argparse
import ast
import fnmatch
import json
import re
import sys
from pathlib import Path

PATTERN = re.compile(r"""\[["']library["']\]|get\(\s*["']library|sync\.library|--library""")

# file (relative, posix) : qualname glob. Source: plan 2026-11-05-warstwy-generator, TESTY "Grep K1".
ALLOWLIST = (
    'skillsync_workflow/layers.py:migrate*',              # migration module (2.4)
    'skillsync_workflow/layers.py:*.migrate*',
    'skillsync_workflow/layers.py:legacy_library_key',     # D41: detection of the old config key in the loader
    'skillsync_workflow/layers.py:*.legacy_library_key',
)

ROOT = Path(__file__).resolve().parents[1]


def sources(root):
    root = Path(root)
    files = sorted((root / 'skillsync_workflow').glob('*.py'))
    return [path for path in files if path.is_file()]


def _definitions(tree):
    """[(first line, last line, qualname)] of every function/class, outer before inner."""
    result = []

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f'{prefix}.{child.name}' if prefix else child.name
                first = min([child.lineno] + [item.lineno for item in child.decorator_list])
                result.append((first, child.end_lineno, name))
                visit(child, name)
            else:
                visit(child, prefix)
    visit(tree, '')
    return result


def qualname_at(definitions, line):
    """Innermost definition containing `line` (`<module>` when none)."""
    best = None
    for first, last, name in definitions:
        if first <= line <= last and (best is None or first >= best[0]):
            best = (first, last, name)
    return best[2] if best else '<module>'


def allowed(rel, qualname, allowlist=ALLOWLIST):
    for entry in allowlist:
        file_part, _, name_glob = entry.partition(':')
        if rel == file_part and fnmatch.fnmatchcase(qualname, name_glob):
            return True
    return False


def scan(root=ROOT, allowlist=ALLOWLIST):
    """[{file, line, function, text, allowed}] for every regex hit (raises SyntaxError/OSError)."""
    root = Path(root)
    hits = []
    for path in sources(root):
        text = path.read_text(encoding='utf-8')
        definitions = _definitions(ast.parse(text, filename=str(path)))
        rel = path.relative_to(root).as_posix()
        for number, line in enumerate(text.splitlines(), 1):
            if PATTERN.search(line):
                name = qualname_at(definitions, number)
                hits.append({'file': rel, 'line': number, 'function': name, 'text': line.strip(),
                             'allowed': allowed(rel, name, allowlist)})
    return hits


def main(argv=None):
    parser = argparse.ArgumentParser(description='K1 control grep with a file:function allowlist')
    parser.add_argument('--root', default=str(ROOT))
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        if not any(path.name == 'cli.py' for path in sources(args.root)):
            raise OSError(f'no skillsync_workflow/cli.py under {args.root}')
        hits = scan(args.root)
    except (OSError, SyntaxError, UnicodeDecodeError) as error:
        print(f'k1_grep: cannot scan: {error}', file=sys.stderr)
        return 2
    violations = [hit for hit in hits if not hit['allowed']]
    if args.json:
        print(json.dumps({'status': 'FAIL' if violations else 'PASS', 'hits': hits}, indent=2))
    else:
        for hit in hits:
            mark = 'allowed' if hit['allowed'] else 'VIOLATION'
            print(f"{hit['file']}:{hit['line']} [{hit['function']}] {mark}: {hit['text']}")
        print(f'K1 grep: {len(hits)} hit(s), {len(violations)} outside the allowlist')
    return 1 if violations else 0


if __name__ == '__main__':
    sys.exit(main())
