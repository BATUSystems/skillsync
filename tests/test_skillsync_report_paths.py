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
                      r'launchers|composed_root|checkout|backup|library)$')
NATIVE_FUNCTIONS = ('str', 'fspath')
# (module, key) -> reason; empty on purpose: every report path goes through `posix`.
NATIVE_ALLOWED = {}


REPORT_CALLS = ('_report', 'update', 'dict')   # keyword arguments of these calls become report fields


def _callee(node):
    func = node.func
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None


def _is_native_call(node):
    return isinstance(node, ast.Call) and _callee(node) in NATIVE_FUNCTIONS


def _values(tree):
    """`(key, value node, line)` of dict entries with a string key and of keyword arguments."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    yield key.value, value, value.lineno
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
        tree = ast.parse("report = {'root': str(root), 'ok': True}\n_report(config=str(p))\nf(path=str(p))\ng(path=posix(p))\n")
        seen = [(key, line) for key, value, line in _values(tree) if PATH_KEY.match(key) and _is_native_call(value)]
        self.assertEqual(seen, [('root', 1), ('config', 2)])


if __name__ == '__main__':
    unittest.main()
