#!/usr/bin/env python3
"""Which test modules must run when merging `<base>..<head>`: a set computed from the diff, with a reason for every module.

    python scripts/merge_modules.py --base release/1.8.0 --head fix/x            # one module per line + reason
    python scripts/merge_modules.py --base 6b6f8df --head HEAD --json            # machine-readable
    python -m unittest $(python scripts/merge_modules.py --base B --head H --names)

Rules (a module is selected when at least one holds; every hit is listed as its reason):
  changed      the test file itself is added or modified;
  import       the test imports (statically, `import`/`from`, absolute or relative, also inside functions) a changed module
               of the product (`skillsync_workflow`), of `scripts/` or a changed helper in `tests/`, directly or through
               other modules (reverse closure over the import graph);
  path         a string in the test names a changed file (`skillsync_workflow/status.py`, or `status.py` next to
               `skillsync_workflow`, or `scripts/x.py`) - for tests that run the code through a subprocess;
  literal      a string literal on an added or removed line of a changed product file (messages: at least 12 characters and
               a space, so identifiers, paths and module prefixes do not count; f-string placeholders are cut out) occurs in a string of the test (either contains the
               other) or in a fixture the test reads;
  fixture      a changed non-Python file under `tests/` (fixtures) is named by a string in the test (basename, stem prefix
               such as `expected`, or its directory);
  expected     any file of `skillsync_workflow/` changed and the test reads `expected*` fixtures (their content mirrors the product output);
  unparsable   the test file does not parse - selected for safety;
  artefact     a file that feeds the artefacts `skillsync link` generates changed (ARTEFACT_SOURCES: the generator `cli.py`/`layers.py`/`guardhook.py` and the helpers
               it uses for file bytes and the task command, the library hooks, instructions, skills, agents): the tests that run `link` through
               a subprocess and compare its output (ARTEFACT_TESTS: a baseline, tracked links) name no product file, so no other rule sees them;
  guard        the fixed guard set (GUARDS below), always.
The analysis reads the working tree, so the checkout must be `--head` (otherwise exit 2); the diff is `base...head` (from the merge base).
Deleted or renamed product modules select the tests that import or name them. Limits: dynamic imports and commands named only by a string (`skillsync status`) are not seen except through the `path`,
`literal` and `expected` rules; a deleted test is never selected. The set is a floor for the merge run, not a replacement for
the full suite before a release.
Exit codes: 0 = computed, 2 = unusable input (no git, unknown revision). Standard library only."""
import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

GUARDS = ('test_skillsync_repo_hygiene', 'test_skillsync_packaging', 'test_skillsync_check_skips',
          'test_skillsync_dashboard_contract', 'test_skillsync_line_ends', 'test_skillsync_line_ends_rest',
          'test_skillsync_line_ends_checkers', 'test_skillsync_measurements', 'test_skillsync_ci_workflows',
          'test_skillsync_duplicate_defs', 'test_skillsync_no_silent_return')
# Sources of the generated `link` artefacts and the tests that run `link` through a subprocess and compare its output (a baseline, tracked links):
# they name no product file, so the import/path/literal rules see them only by accident.
ARTEFACT_SOURCES = ('skillsync_workflow/cli.py', 'skillsync_workflow/layers.py', 'skillsync_workflow/guardhook.py', 'skillsync_workflow/common.py',
                    'skillsync_workflow/selfcmd.py', 'skillsync_workflow/fsutil.py', 'skillsync_workflow/pathfmt.py', 'library/hooks/',
                    'library/instructions/', 'library/skills/', 'library/agents/')
ARTEFACT_TESTS = ('test_skillsync_layers_equivalence', 'test_skillsync_worktree_links')
PRODUCT_DIRS = ('skillsync_workflow', 'scripts', 'tests')
MIN_LITERAL = 12
STRING = re.compile(r"""(?P<q>'|")(?P<s>(?:\\.|(?!(?P=q)).)*)(?P=q)""")
PLACEHOLDER = re.compile(r'\{[^{}]*\}')


class GitError(RuntimeError):
    pass


def git(repo, *args):
    try:
        done = subprocess.run(['git', '-C', str(repo), '-c', 'core.quotePath=false', '-c', 'diff.noprefix=false', '-c', 'color.ui=false', *args], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        raise GitError(str(error))
    if done.returncode != 0:
        raise GitError(done.stderr.strip()[:300] or f'git {args[0]} failed')
    return done.stdout


def changed_files(repo, base, head):
    """`{path: status}` with status A/M/D (renames count as delete + add)."""
    out = git(repo, 'diff', '--name-status', '--no-renames', '-z', f'{base}...{head}', '--').split('\0')
    result = {}
    for status, path in zip(out[0::2], out[1::2]):
        if status:
            result[path] = status[0]
    return result


def diff_literals(repo, base, head, paths):
    """Static parts of string literals on added or removed lines of the given Python files."""
    found = set()
    for path in paths:
        text = git(repo, 'diff', '-U0', '--no-renames', '--no-color', '--no-ext-diff', '--src-prefix=a/', '--dst-prefix=b/', f'{base}...{head}', '--', path)
        for line in text.split('\n'):
            if not line or line[0] not in '+-' or line.startswith(('+++', '---')) or line[1:].lstrip().startswith('#'):
                continue
            for match in STRING.finditer(line[1:]):
                for part in PLACEHOLDER.split(match.group('s')):
                    part = part.strip()
                    # a message has words; an identifier, a path or a module prefix ('skillsync_workflow') matches half of the suite
                    if len(part) >= MIN_LITERAL and re.search(r'\s', part):
                        found.add(part)
    return found


def dotted(path):
    parts = Path(path).with_suffix('').parts
    return '.'.join(parts[:-1] if parts[-1] == '__init__' else parts)


def internal_modules(repo):
    """`{dotted name: relative path}` of every Python file in the product, scripts and tests."""
    found = {}
    for top in PRODUCT_DIRS:
        for file in sorted((Path(repo) / top).rglob('*.py')):
            relative = file.relative_to(repo).as_posix()
            found[dotted(relative)] = relative
    return found


def imports_of(tree, own, modules, is_package=False):
    """`(resolved, raw)`: internal modules imported by a parsed file and every dotted name it mentions
    (own = its dotted name, for relative imports; is_package = the file is an `__init__.py`)."""
    names = set()
    package = own if is_package else (own.rsplit('.', 1)[0] if '.' in own else '')
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = package.split('.') if package else []
                if node.level > 1:
                    base_parts = base_parts[:len(base_parts) - (node.level - 1)]
                base = '.'.join(base_parts + ([node.module] if node.module else []))
            else:
                base = node.module or ''
            if base:
                names.add(base)
            names.update((base + '.' + alias.name) if base else alias.name for alias in node.names)
    expanded = set()
    for name in names:  # `import a.b.c` also runs a and a.b
        parts = name.split('.')
        expanded.update('.'.join(parts[:i]) for i in range(1, len(parts) + 1))
    by_last = {}
    for dotted_name in modules:
        by_last.setdefault(dotted_name.rsplit('.', 1)[-1], []).append(dotted_name)
    resolved = set()
    for name in expanded:
        if name in modules:
            resolved.add(name)
        elif '.' not in name and name not in ('tests', 'scripts', 'skillsync_workflow'):
            # a bare `import helper`: unittest discovery and sys.path.insert put tests/ (or a fixture directory) on the path
            resolved.update(candidate for candidate in by_last.get(name, []) if candidate.startswith('tests.'))
    return resolved, expanded


def string_constants(tree):
    values = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            values.append(node.value)
    return values


def select(repo, base, head):
    repo = Path(repo)
    changed = changed_files(repo, base, head)
    modules = internal_modules(repo)
    by_path = {path: name for name, path in modules.items()}
    tests = {name: path for name, path in modules.items() if Path(path).name.startswith('test_skillsync') and path.startswith('tests/')}
    reasons = {}

    def add(test, text):
        reasons.setdefault(test, [])
        if text not in reasons[test]:
            reasons[test].append(text)

    changed_py = {path for path, status in changed.items() if path.endswith('.py') and status != 'D'}
    changed_modules = {by_path[path] for path in changed_py if path in by_path}
    deleted_py = {path for path, status in changed.items() if path.endswith('.py') and status == 'D'}
    deleted_modules = {dotted(path) for path in deleted_py} | {dotted(path).rsplit('.', 1)[-1] for path in deleted_py if path.startswith('tests/')}  # bare `import helper` too
    # deleted product files count too: tests that named or imported them break at the merge
    changed_product = sorted(path for path in changed_py | deleted_py if path.startswith(('skillsync_workflow/', 'scripts/')))
    changed_fixtures = sorted(path for path in changed if path.startswith('tests/') and not path.endswith('.py'))
    literals = diff_literals(repo, base, head, changed_product)

    parsed, constants, imports, raw_imports = {}, {}, {}, {}
    for name, path in tests.items():
        try:
            tree = ast.parse((repo / path).read_text(encoding='utf-8-sig', errors='replace'), filename=path)
        except (SyntaxError, ValueError, OSError):
            parsed[name] = None
            continue
        parsed[name] = tree
        constants[name] = string_constants(tree)
    for name, path in modules.items():
        tree = parsed.get(name)
        if tree is None and name not in parsed:
            try:
                tree = ast.parse((repo / path).read_text(encoding='utf-8-sig', errors='replace'), filename=path)
            except (SyntaxError, ValueError, OSError):
                tree = None
        if tree is not None:
            imports[name], raw_imports[name] = imports_of(tree, name, modules, path.endswith('__init__.py'))

    # reverse closure: module -> the changed module it depends on (first found), with the chain for the reason
    def chain_to_changed(start):
        seen, queue = {start}, [(start, [start])]
        while queue:
            current, trail = queue.pop(0)
            for target in sorted(imports.get(current, ())):
                if Path(modules[target]).name.startswith('test_skillsync') and target not in changed_modules:
                    continue  # an imported test module is selected on its own merits
                if target in changed_modules:
                    return trail + [target]
                if target not in seen:
                    seen.add(target)
                    queue.append((target, trail + [target]))
        return None

    all_fixtures = sorted((repo / 'tests').rglob('*.json')) if (repo / 'tests').is_dir() else []
    def reachable(start):
        """`(module, trail)` for the module itself and every non-test module it imports, directly or not."""
        seen, queue = {start}, [(start, [start])]
        while queue:
            current, trail = queue.pop(0)
            yield current, trail
            for target in sorted(imports.get(current, ())):
                if target not in seen and not Path(modules[target]).name.startswith('test_skillsync'):
                    seen.add(target)
                    queue.append((target, trail + [target]))

    fixture_by_name = {}
    for file in all_fixtures:
        fixture_by_name.setdefault(file.name, []).append(file)

    for name, path in tests.items():
        if changed.get(path) in ('A', 'M'):
            add(name, 'changed: test file changed')
        if parsed[name] is None:
            add(name, 'unparsable: test file does not parse')
            continue
        if name not in changed_modules or changed.get(path) not in ('A', 'M'):
            trail = chain_to_changed(name)
            if trail:
                add(name, 'import: ' + ' -> '.join(trail))
        for reached, trail in reachable(name):
            gone = sorted(raw_imports.get(reached, set()) & deleted_modules)
            if gone:
                add(name, 'import: ' + ' -> '.join(trail) + ' imports deleted ' + ', '.join(gone))
        values = constants[name]
        for product in changed_product:
            stem = Path(product).name
            parent = Path(product).parent.name
            if any(product in value for value in values) or (stem in values and parent in values):
                add(name, f'path: names {product}')
        for literal in sorted(literals):
            if any(len(value) >= MIN_LITERAL and re.search(r'\s', value) and (literal in value or value in literal) for value in values):
                add(name, f'literal: "{literal[:60]}"')
                break
            # fixtures the test reads by name
            for value in values:
                for file in fixture_by_name.get(Path(value).name, []):
                    if literal in file.read_text(encoding='utf-8', errors='replace'):
                        add(name, f'literal: "{literal[:60]}" in fixture {file.relative_to(repo).as_posix()}')
                        break
        for fixture in changed_fixtures:
            file = Path(fixture)
            keys = {file.name}
            if file.stem.startswith('expected'):
                keys.add('expected')
            case = file.parent.name if file.parent.name not in ('tests', 'fixtures') and len(file.parent.name) >= 4 else None
            if any(key in value for key in keys for value in values) or (case and any(case in value for value in values)):
                add(name, f'fixture: {fixture}')
        if any(path.startswith('skillsync_workflow/') for path in changed_product) and any('expected' in value for value in values):
            add(name, 'expected: reads expected* fixtures and the product changed')

    artefact_hits = sorted(path for path, status in changed.items() if status != 'D' and path.startswith(ARTEFACT_SOURCES))
    if artefact_hits:
        for name in ARTEFACT_TESTS:
            if 'tests.' + name in tests:
                add('tests.' + name, f'artefact: {artefact_hits[0]} feeds the generated link artefacts (baseline comparison)')
    for guard in GUARDS:
        if 'tests.' + guard in tests:
            add('tests.' + guard, 'guard: fixed guard set')
    return {'base': base, 'head': head, 'changedFiles': len(changed),
            'modules': [{'module': name, 'reasons': reasons[name]} for name in sorted(reasons)]}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Test modules to run when merging <base>..<head>, with a reason per module.')
    parser.add_argument('--base', required=True)
    parser.add_argument('--head', required=True)
    parser.add_argument('--repo', default='.', help='repository root (default: current directory)')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--names', action='store_true', help='only dotted module names (for `python -m unittest`), one per line')
    args = parser.parse_args(argv)
    try:
        head = git(args.repo, 'rev-parse', '--verify', '--end-of-options', args.head + '^{commit}').strip()
        current = git(args.repo, 'rev-parse', 'HEAD').strip()
        if head != current:
            raise GitError(f'the checkout is {current[:10]}, not --head {args.head} ({head[:10]}): the analysis reads the working tree, check out the merged tree first')
        result = select(args.repo, args.base, args.head)
    except GitError as error:
        print(f'merge_modules: {error}', file=sys.stderr)
        return 2
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', newline='\n')
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    elif args.names:
        print('\n'.join(item['module'] for item in result['modules']))
    else:
        for item in result['modules']:
            print(item['module'])
            for reason in item['reasons']:
                print(f'    {reason}')
        print(f"{len(result['modules'])} modules from {result['changedFiles']} changed files ({args.base}..{args.head})")
    return 0


if __name__ == '__main__':
    sys.exit(main())
