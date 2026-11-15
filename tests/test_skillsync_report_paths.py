"""Contract guard (DEBT-150 PATHFMT): a path field of a CLI report is written through `pathfmt.posix`, never as `str(path)`.

Since 1.4.1 a path in a report has `/` on every system (`D:/work/docs/README.md`); the fields `root`, `project`, `config`,
`directory`, `budgets[].path` and the rest were left native. The guard reads the sources of the modules (AST, no run) and
fails for a dict entry or keyword argument whose key names a path and whose value is `str(...)` / `os.fspath(...)`, so a new
report field cannot bring the native form back. A field that must stay native (a command to execute, a value read from the
configuration) goes to `NATIVE_ALLOWED` with the reason."""
import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PATH_KEY = re.compile(r'^(path|paths|root|project|config|manifest|directory|dir|file|log|report|index|recorded|scenario|'
                      r'launchers|composed_root|checkout|backup|library|repo|checkpoint|targets|worktree)$')
NATIVE_FUNCTIONS = ('str', 'fspath')
# (module, key) -> reason; empty on purpose: every report path goes through `posix`.
NATIVE_ALLOWED = {}


REPORT_CALLS = ('_report', 'update', 'dict')   # keyword arguments of these calls become report fields


def _callee(node):
    func = node.func
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None


def _is_native_call(node):
    """`str(x)`, `os.fspath(x)`, either arm of `str(x) if x else None`, and a list or tuple of them (`[str(p) for p in ...]`)."""
    if isinstance(node, ast.IfExp):
        return _is_native_call(node.body) or _is_native_call(node.orelse)
    if isinstance(node, (ast.ListComp, ast.GeneratorExp, ast.SetComp)):
        return _is_native_call(node.elt)
    if isinstance(node, (ast.List, ast.Tuple)):
        return any(_is_native_call(item) for item in node.elts)
    return isinstance(node, ast.Call) and _callee(node) in NATIVE_FUNCTIONS


def _values(tree):
    """`(key, value node, line)` of dict entries with a string key, of `x['key'] = value` assignments and of keyword
    arguments of the report calls. Scope: values written directly (also under a conditional or in a list); a path put in a
    variable first and then into the report is not seen (the review of a new report field covers it)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    yield key.value, value, value.lineno
        elif isinstance(node, ast.Assign):   # report['backup'] = str(path)
            for target in node.targets:
                if (isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
                        and isinstance(target.slice.value, str)):
                    yield target.slice.value, node.value, node.value.lineno
        elif isinstance(node, ast.Call) and _callee(node) in REPORT_CALLS:
            for keyword in node.keywords:
                if keyword.arg:
                    yield keyword.arg, keyword.value, keyword.value.lineno


class ReportPathFields(unittest.TestCase):
    def test_no_report_path_field_is_a_native_str(self):
        found = []
        for source in sorted((ROOT / 'skillsync_workflow').glob('*.py')):
            tree = ast.parse(source.read_text(encoding='utf-8'), filename=str(source))
            for key, value, line in _values(tree):
                if not PATH_KEY.match(key) or not _is_native_call(value):
                    continue
                if (source.name, key) in NATIVE_ALLOWED:
                    continue
                found.append(f'{source.name}:{line} {key}')
        self.assertEqual(found, [], 'path fields of reports must use pathfmt.posix (see NATIVE_ALLOWED for exceptions)')

    def test_the_guard_sees_a_native_field(self):
        tree = ast.parse("report = {'root': str(root), 'ok': True}\n_report(config=str(p))\nf(path=str(p))\ng(path=posix(p))\n"
                         "r['backup'] = str(b) if b else None\nr['project'] = posix(p) if p else None\nd = {'targets': [str(x) for x in xs]}\n")
        seen = sorted((line, key) for key, value, line in _values(tree) if PATH_KEY.match(key) and _is_native_call(value))
        self.assertEqual(seen, [(1, 'root'), (2, 'config'), (5, 'backup'), (7, 'targets')])


if __name__ == '__main__':
    unittest.main()
