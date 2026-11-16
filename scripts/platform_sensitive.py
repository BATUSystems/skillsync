#!/usr/bin/env python3
"""Warning (never a gate): does the diff `<base>...<head>` touch code that depends on the operating system, and which systems
therefore need evidence before the push?

    python scripts/platform_sensitive.py --base release/1.8.0 --head HEAD            # text
    python scripts/platform_sensitive.py --base 6b6f8df --head HEAD --json

Added and removed lines of changed files are matched against patterns (category -> systems that need evidence):
  platform-branch  os.name, sys.platform, platform.system()                            windows, macos, linux
  signal           signal.SIG*, CTRL_*_EVENT, SetConsoleCtrlHandler, os.killpg/os.kill,
                   start_new_session, setsid                                           windows, macos, linux
  windows-api      ctypes, windll/WinDLL/WINFUNCTYPE, msvcrt, winreg, `.ps1` files     windows
  posix-api        fcntl, flock, os.getuid, os.fork, os.chmod, `.sh` files             macos, linux
  symlink          symlink/readlink/junction/mklink, core.symlinks, is_symlink         windows, macos, linux
  rmtree           shutil.rmtree, os.remove/unlink on trees (locked or read-only
                   files on Windows)                                                   windows
  line-ends        \\r, \\r\\n, CRLF, newline=, autocrlf, splitlines, file_lines        windows
  backslash-path   PureWindowsPath/ntpath, os.sep, drive letters, `'\\\\'` in paths    windows
  socket-option    SO_* options, AF_UNIX                                               windows, macos, linux
  tmp-path-data    `<tmp path> / <variable>` added in tests (name from data)           windows
The output lists the matches (file:line, category, text), the systems that need evidence and the test modules to run in each
tranche (`merge_modules.select`: the same set a merge runs). A match is a hint to look, not a finding: the matching is textual.
Exit codes: 0 always when the diff was read (a warning never blocks), 2 = unusable input (no git, unknown revision, checkout is
not `--head`). Standard library only."""
import argparse
import ast
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALL = ('windows', 'macos', 'linux')
PATTERNS = [
    ('platform-branch', ALL, re.compile(r'\bos\.name\b|\bsys\.platform\b|\bplatform\.(system|uname|platform)\s*\(')),
    ('signal', ALL, re.compile(r'\bsignal\.(SIG[A-Z0-9_]+|CTRL_\w+)|\bCTRL_\w*_EVENT\b|\bSetConsoleCtrlHandler\b|\bos\.(killpg|getpgrp|setsid)\b|\bstart_new_session\b|\bsetsid\b|\bSIGBREAK\b')),
    ('windows-api', ('windows',), re.compile(r'\bctypes\b|\bwindll\b|\bWinDLL\b|\bWINFUNCTYPE\b|\bmsvcrt\b|\bwinreg\b|\bCREATE_NEW_(PROCESS_GROUP|CONSOLE)\b')),
    ('posix-api', ('macos', 'linux'), re.compile(r'\bfcntl\b|\bflock\b|\bos\.(getuid|fork|geteuid|setuid|mkfifo)\b')),
    ('symlink', ALL, re.compile(r'symlink|readlink|\bjunction\b|\bmklink\b|core\.symlinks|is_symlink', re.IGNORECASE)),
    ('rmtree', ('windows',), re.compile(r'\brmtree\b|\bos\.(remove|unlink|rmdir)\b|\.unlink\(')),
    ('line-ends', ('windows',), re.compile(r'\\r\\n|\\r\b|\\r[\'"\])|]|\bCRLF\b|\bcrlf\b|\bnewline\s*=|\bautocrlf\b|\bsplitlines\s*\(|\bfile_lines\b')),
    ('backslash-path', ('windows',), re.compile(r'PureWindowsPath|\bntpath\b|\bos\.(sep|altsep)\b|\bos\.path\.(sep|altsep)\b|[\'"][A-Za-z]:\\\\|[\'"]\\\\\\\\[\'"]|replace\(\s*[\'"]\\\\\\\\[\'"]')),
    ('socket-option', ALL, re.compile(r'\bSO_[A-Z_]+\b|\bAF_UNIX\b|\bIPPROTO_\w+\b|\bTCP_NODELAY\b')),
]
FILE_RULES = [('windows-api', ('windows',), re.compile(r'\.ps1$')), ('posix-api', ('macos', 'linux'), re.compile(r'\.sh$'))]
HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


merge_modules = load('merge_modules', HERE / 'merge_modules.py')


def diff_lines(repo, base, head):
    """`{path: {'+': [(line, text)], '-': [(line, text)]}}` of changed text files (added lines with their new line numbers)."""
    out = {}
    path = None
    new_line = old_line = 0
    text = merge_modules.git(repo, 'diff', '-U0', '--no-renames', f'{base}...{head}', '--')
    for raw in text.split('\n'):
        if raw.startswith('diff --git '):
            path = None
        elif raw.startswith('+++ '):
            path = raw[6:] if raw.startswith('+++ b/') else None
            if path is not None:
                out.setdefault(path, {'+': [], '-': []})
        elif raw.startswith('--- '):
            if raw.startswith('--- a/'):
                path = raw[6:]
                out.setdefault(path, {'+': [], '-': []})
        elif raw.startswith('@@'):
            match = HUNK.match(raw)
            if match:
                new_line = int(match.group(1))
            old_line = 0
        elif path and raw.startswith('+') and not raw.startswith('+++'):
            out[path]['+'].append((new_line, raw[1:]))
            new_line += 1
        elif path and raw.startswith('-') and not raw.startswith('---'):
            out[path]['-'].append((0, raw[1:]))
    return out


def prose_lines(repo, path):
    """Line numbers of the working-tree file covered by bare string statements (docstrings, prose blocks): not code."""
    try:
        tree = ast.parse((Path(repo) / path).read_text(encoding='utf-8-sig', errors='replace'))
    except (OSError, SyntaxError, ValueError):
        return set()
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            lines.update(range(node.lineno, node.end_lineno + 1))
    return lines


def tmp_path_hits(repo, path, added):
    """Added `<tmp path> / <variable>` expressions of a test file, by the AST scan of the tmp-path guard."""
    try:
        guard = load('tmp_path_guard', Path(repo) / 'tests' / 'test_skillsync_tmp_path_guard.py') if 'tmp_path_guard' not in sys.modules else sys.modules['tmp_path_guard']
        tree = ast.parse((Path(repo) / path).read_text(encoding='utf-8-sig', errors='replace'))
    except (OSError, SyntaxError, ValueError, ImportError, AttributeError):
        return []
    lines = {number for number, _ in added}
    return [(number, text) for number, text in guard.scan(tree) if number in lines]


def matches(repo, base, head):
    found = []
    for path, sides in diff_lines(repo, base, head).items():
        for category, systems, pattern in FILE_RULES:
            if pattern.search(path):
                found.append({'file': path, 'line': 0, 'side': '+', 'category': category, 'systems': list(systems), 'text': 'file type'})
        if not path.endswith('.py'):
            continue
        prose = prose_lines(repo, path)
        for side in ('+', '-'):
            for number, text in sides[side]:
                if text.strip().startswith('#') or (side == '+' and number in prose):
                    continue
                for category, systems, pattern in PATTERNS:
                    if pattern.search(text):
                        found.append({'file': path, 'line': number, 'side': side, 'category': category, 'systems': list(systems), 'text': text.strip()[:100]})
        if path.startswith('tests/'):
            for number, text in tmp_path_hits(repo, path, sides['+']):
                found.append({'file': path, 'line': number, 'side': '+', 'category': 'tmp-path-data', 'systems': ['windows'], 'text': text[:100]})
    return found


def analyse(repo, base, head):
    hits = matches(repo, base, head)
    systems = {name: sorted({hit['category'] for hit in hits if name in hit['systems']}) for name in ALL}
    needed = {name: categories for name, categories in systems.items() if categories}
    selection = merge_modules.select(repo, base, head)
    return {'base': base, 'head': head, 'matches': hits, 'evidenceNeeded': needed,
            'modules': [item['module'] for item in selection['modules']], 'moduleReasons': {item['module']: item['reasons'] for item in selection['modules']}}


def render(result):
    hits = result['matches']
    if not hits:
        return f"platform_sensitive: no system-dependent code touched in {result['base']}...{result['head']}"
    lines = [f"WARNING: the diff {result['base']}...{result['head']} touches system-dependent code ({len(hits)} matches); this does not block anything."]
    shown = {}
    for hit in hits:
        key = (hit['file'], hit['category'])
        shown.setdefault(key, []).append(hit)
    for (file, category), group in sorted(shown.items()):
        first = group[0]
        where = f"{file}:{first['line']}" if first['line'] else file
        lines.append(f"  {category:16} {where}  {first['side']} {first['text']}" + (f'  (+{len(group) - 1} more)' if len(group) > 1 else ''))
    lines.append('Evidence needed from: ' + '; '.join(f"{name} ({', '.join(cats)})" for name, cats in result['evidenceNeeded'].items()))
    lines.append(f"Test modules for the tranche ({len(result['modules'])}, as for the merge): " + ' '.join(m.split('.')[-1] for m in result['modules']))
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Warn when a diff touches system-dependent code and name the systems that need evidence.')
    parser.add_argument('--base', required=True)
    parser.add_argument('--head', required=True)
    parser.add_argument('--repo', default='.')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        head = merge_modules.git(args.repo, 'rev-parse', '--verify', '--end-of-options', args.head + '^{commit}').strip()
        current = merge_modules.git(args.repo, 'rev-parse', 'HEAD').strip()
        if head != current:
            raise merge_modules.GitError(f'the checkout is {current[:10]}, not --head {args.head} ({head[:10]}): check out the merged tree first')
        result = analyse(args.repo, args.base, args.head)
    except merge_modules.GitError as error:
        print(f'platform_sensitive: {error}', file=sys.stderr)
        return 2
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', newline='\n')
    print(json.dumps(result, ensure_ascii=False, indent=1) if args.json else render(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
