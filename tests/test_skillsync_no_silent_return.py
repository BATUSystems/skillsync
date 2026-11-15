"""Guard (GUARD-SILENT-RETURN): a test must not pass silently where it checks nothing.

A skipped test is counted: `tests/skip-baseline.json` lists every skip with its reason per system, and `scripts/check_skips.py` fails
a skip outside the list. A test that ends with a quiet `return` (for example `if os.name == 'nt': return`) is reported as PASS,
so the baseline never sees it and a test that stopped checking anything on a system looks green. The rule: a mechanism that the system
lacks gives `self.skipTest(reason)` / `@unittest.skipUnless(...)` with a reason from an existing category of the baseline, never a `return`.

Two forms are refused (AST of every `tests/**/*.py`, no run):
1. a `return` in a test method whose own block holds no assertion before it (a justified early return, after the checks of that branch,
   has them: `self.assertEqual(...)`, `self.assertTrue(...)`, ...);
2. a test method whose only assertions sit inside one conditional on the platform or on a capability, without an `else`
   (the test passes on every other system without checking).
Exceptions go to `ALLOWED` as `(file, method)` with the reason; the list is empty on purpose, and a stale entry fails the guard.

Known limits (a static guard, not a proof): an assertion counts when the call is named `assert*`, `verify*`, `check*`, `expect*`,
`ensure*`, `must*`, `fail` or `skipTest`; a check made by another name does not count, and a path put through a variable or a helper
that returns for the test is not followed; form 2 reads only the conditionals at the top level of the method body."""
import ast
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# (relative file, test method) -> reason. Empty: every quiet return is a skip with a reason.
ALLOWED = {}

CAPABILITY = re.compile(r'os\.name|sys\.platform|platform\.|hasattr\(|IS_WIN|IS_MAC|WINDOWS|MACOS|POSIX|geteuid|getuid|which\(|symlink|tzset|'
                        r'CHROME|NODE|shellcheck|junction|_available|supports|can_|\bNO_[A-Z_]+|\bHAS_[A-Z_]+|\bIS_[A-Z_]+', re.IGNORECASE)
CHECK_HELPERS = ('assert', 'verify', 'check', 'expect', 'ensure', 'must')   # a helper named so counts as an assertion (documented)


def _is_assertion(node):
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ''
    return name.startswith(CHECK_HELPERS) or name in ('fail', 'skipTest')   # skipTest raises: a return after it is unreachable


def _own_nodes(block):
    """Nodes of `block` without descending into nested functions, classes and lambdas."""
    stack = list(block)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _blocks(function):
    """Every statement list of the method (its body and the bodies of if / else / try / with / for / while / except)."""
    yield function.body
    for node in _own_nodes(function.body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue   # a nested helper is not the test: its returns are its own
        for field in ('body', 'orelse', 'finalbody'):
            value = getattr(node, field, None)
            if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
                yield value
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                yield handler.body


def silent_returns(function):
    """Lines of a `return` whose own block has no assertion before it."""
    lines = []
    for block in _blocks(function):
        for index, statement in enumerate(block):
            if isinstance(statement, ast.Return):
                if not any(_is_assertion(node) for earlier in block[:index] for node in _own_nodes([earlier])):
                    lines.append(statement.lineno)
    return sorted(set(lines))


def only_inside_conditional(function):
    """Line of a platform/capability `if` at the top of the method that holds every assertion of the method while one of its
    branches checks nothing (no `else`, or an `else`/`if` branch without an assertion, e.g. `if nt: pass / else: asserts`)."""
    body = [s for s in function.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    for statement in body:
        if isinstance(statement, ast.If) and CAPABILITY.search(ast.unparse(statement.test)):
            inside = any(_is_assertion(node) for node in _own_nodes([statement]))
            outside = any(_is_assertion(node) for other in body if other is not statement for node in _own_nodes([other]))
            then_checks = any(_is_assertion(node) for node in _own_nodes(statement.body))
            else_checks = any(_is_assertion(node) for node in _own_nodes(statement.orelse)) if statement.orelse else False
            if inside and not outside and not (then_checks and else_checks):
                return statement.lineno
    return None


def test_methods(tree):
    """The `test*` methods of the classes of a module: not helpers (module functions, nested functions), not `setUp`/`tearDown`/`setUpClass`."""
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef):
            for member in cls.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name.startswith('test'):
                    yield member


def findings(tests_dir, root=None, allowed=None):
    root = Path(root or tests_dir.parent)
    found = []
    for path in sorted(Path(tests_dir).rglob('*.py')):
        relative = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        except SyntaxError:
            continue
        for function in test_methods(tree):
            if (relative, function.name) in (ALLOWED if allowed is None else allowed):
                continue
            for line in silent_returns(function):
                found.append(f'{relative}:{line} {function.name}: a return with no assertion before it (use self.skipTest(reason))')
            line = only_inside_conditional(function)
            if line:
                found.append(f'{relative}:{line} {function.name}: every assertion sits in one platform/capability branch without else (skip instead)')
    return found


def stale_allowed(tests_dir=None):
    """`ALLOWED` entries that no longer name a test with a finding (the test was fixed or renamed): the list must not rot."""
    tests_dir = Path(tests_dir or ROOT / 'tests')
    current = {tuple(item.split(' ')[0].rsplit(':', 1)[0:1]) + (item.split(' ')[1].rstrip(':'),) for item in findings(tests_dir, tests_dir.parent, allowed={})}
    return sorted(entry for entry in ALLOWED if entry not in current)


def _first_function(source):
    return next(node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef))


class NoSilentReturn(unittest.TestCase):
    def test_no_test_returns_silently_where_it_checks_nothing(self):
        self.assertEqual(findings(ROOT / 'tests'), [], 'a mechanism the system lacks is a skip with a reason (tests/skip-baseline.json), not a return')

    def test_the_guard_sees_the_quiet_forms(self):
        quiet = _first_function("def test_a(self):\n    if os.name == 'nt':\n        return\n    self.assertTrue(x)\n")
        self.assertEqual(silent_returns(quiet), [3])
        guarded = _first_function("def test_b(self):\n    if sys.platform == 'darwin':\n        self.assertTrue(y)\n")
        self.assertEqual(only_inside_conditional(guarded), 2)

    def test_an_allowed_entry_that_no_longer_has_a_finding_is_stale(self):
        self.assertEqual(stale_allowed(), [], 'remove the entry from ALLOWED: the test was fixed or renamed')
        with mock.patch.dict(ALLOWED, {('tests/test_skillsync_no_silent_return.py', 'test_the_guard_sees_the_quiet_forms'): 'x'}):
            self.assertEqual(stale_allowed(), [('tests/test_skillsync_no_silent_return.py', 'test_the_guard_sees_the_quiet_forms')])

    def test_the_guard_sees_a_pass_branch_with_the_checks_in_the_else_and_counts_skiptest(self):
        passing = _first_function("def test_f(self):\n    if os.name == 'nt':\n        pass\n    else:\n        self.assertTrue(a)\n")
        self.assertEqual(only_inside_conditional(passing), 2)
        flag = _first_function("def test_g(self):\n    if not NO_MODE_ENFORCEMENT:\n        self.verify_mode(a)\n")
        self.assertEqual(only_inside_conditional(flag), 2)
        skipped = _first_function("def test_h(self):\n    if os.name == 'nt':\n        self.skipTest('no')\n        return\n    self.assertTrue(a)\n")
        self.assertEqual(silent_returns(skipped), [])

    def test_the_guard_ignores_helpers_fixtures_and_subtests(self):
        source = (
            "def helper(x):\n    if os.name == 'nt':\n        return\n    return x\n"
            "def test_module_helper_named_like_a_test():\n    return\n"
            "class Case(unittest.TestCase):\n"
            "    def setUp(self):\n        if os.name == 'nt':\n            return\n"
            "    def tearDown(self):\n        return\n"
            "    def helper(self):\n        if os.name == 'nt':\n            return\n"
            "    def test_with_a_nested_helper(self):\n"
            "        def fake(*a):\n            if os.name == 'nt':\n                return\n"
            "        self.assertTrue(fake())\n"
            "    def test_subtests(self):\n"
            "        for value in (1, 2):\n            with self.subTest(value=value):\n"
            "                self.assertTrue(value)\n"
        )
        names = [method.name for method in test_methods(ast.parse(source))]
        self.assertEqual(names, ['test_with_a_nested_helper', 'test_subtests'])
        for method in test_methods(ast.parse(source)):
            self.assertEqual(silent_returns(method), [], method.name)
            self.assertIsNone(only_inside_conditional(method), method.name)

    def test_the_guard_refuses_a_return_inside_a_subtest_and_a_try(self):
        source = (
            "class Case(unittest.TestCase):\n"
            "    def test_a(self):\n        with self.subTest(x=1):\n            if os.name == 'nt':\n                return\n            self.assertTrue(1)\n"
            "    def test_b(self):\n        try:\n            import fcntl\n        except ImportError:\n            return\n        self.assertTrue(fcntl)\n"
        )
        lines = {method.name: silent_returns(method) for method in test_methods(ast.parse(source))}
        self.assertEqual(lines, {'test_a': [5], 'test_b': [11]})

    def test_the_guard_allows_an_early_return_after_assertions_and_a_branch_with_an_else(self):
        after_checks = _first_function("def test_c(self):\n    if mode == 'copy':\n        self.assertEqual(a, b)\n        return\n    self.assertTrue(z)\n")
        self.assertEqual(silent_returns(after_checks), [])
        with_else = _first_function("def test_d(self):\n    if os.name == 'nt':\n        self.assertTrue(a)\n    else:\n        self.assertTrue(b)\n")
        self.assertIsNone(only_inside_conditional(with_else))
        outside = _first_function("def test_e(self):\n    self.assertTrue(a)\n    if os.name == 'nt':\n        self.assertTrue(b)\n")
        self.assertIsNone(only_inside_conditional(outside))


if __name__ == '__main__':
    unittest.main()
