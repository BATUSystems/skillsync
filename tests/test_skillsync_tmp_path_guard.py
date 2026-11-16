"""Guard (report mode): `<temporary directory> / <variable or call>` outside `tests/tmp_paths.py`.

A name that comes from data and is joined to a temporary directory can leave it (on Windows `\\n` is a path from the drive root;
see `tests/test_skillsync_tmp_paths.py`). The AST scan reports every `<tmp path> / <expression>` whose right side is not a
constant, an UPPER_CASE constant or an f-string/concatenation of such constants. A "tmp path" is a name or attribute with `tmp` or
`temp` in it, or one assigned (transitively) from `tempfile`/`mkdtemp`/`TemporaryDirectory` or from another tmp path.

Mode: REPORT. Hits above the counts in BASELINE (per file) or in a file missing from it are printed to stderr; the test fails only
when STRICT is switched on. BASELINE is the state at the start (hits not reviewed one by one: most are fixture writers whose names
are literals of the test; the ones in the dashboard server test wait for the dashboard work to land). To fix a hit, use
`tmp_paths.child/make_dir/write_file` and lower the count; to keep it, add the reason next to the count.
"""
import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STRICT = False
EXEMPT = ('tmp_paths.py', 'test_skillsync_tmp_path_guard.py')
BASELINE = {
    'test_skillsync.py': 2,
    'test_skillsync_ci_workflows.py': 1,
    'test_skillsync_dashboard_plan_choice.py': 2,
    'test_skillsync_dashboard_ports.py': 1,
    'test_skillsync_dashboard_redaction.py': 2,
    'test_skillsync_dashboard_server.py': 2,
    'test_skillsync_layers_merge.py': 1,
    'test_skillsync_measurements.py': 1,
    'test_skillsync_merge_modules.py': 1,
    'test_skillsync_packaging.py': 1,
    'test_skillsync_platform_sensitive.py': 1,
    'test_skillsync_prototype_engine.py': 2,
    'test_skillsync_prototype_library.py': 2,
}
REASON = 'reviewed by reading (see the baseline review): names from the test itself, parameters of fixture writers, or the dashboard server test awaiting 180-A/B'

TMP = re.compile(r'(^|_)(tmp|temp)(dir|path|file|root|base)?[0-9]*($|_)', re.I)
SRC=('tempfile','mkdtemp','TemporaryDirectory','NamedTemporary')
def names_of(t):
    if isinstance(t,ast.Name): return [t.id]
    if isinstance(t,ast.Attribute): return [t.attr]
    if isinstance(t,(ast.Tuple,ast.List)): return [n for e in t.elts for n in names_of(e)]
    return []
def literal(n):
    """A constant, or a tuple/list/set/dict of constants (nested): data written in the test itself."""
    if isinstance(n, ast.Constant):
        return True
    if isinstance(n, (ast.Tuple, ast.List, ast.Set)):
        return all(literal(e) for e in n.elts)
    if isinstance(n, ast.Dict):
        return all(k is not None and literal(k) and literal(v) for k, v in zip(n.keys, n.values))
    return False


def scan(tree):
    tmpish = set()

    def is_tmp(n):
        if isinstance(n, ast.Name): return bool(TMP.search(n.id)) or n.id in tmpish
        if isinstance(n, ast.Attribute): return bool(TMP.search(n.attr)) or n.attr in tmpish
        if isinstance(n, ast.Call):
            f = n.func; fn = f.id if isinstance(f, ast.Name) else getattr(f, 'attr', '')
            if fn in ('Path', 'PurePath', 'resolve', 'realpath') or isinstance(f, ast.Attribute) and fn in ('resolve',):
                return any(is_tmp(a) for a in n.args) or (isinstance(f, ast.Attribute) and is_tmp(f.value))
            return False
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div): return is_tmp(n.left)
        return False
    for _ in range(3):
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign):
                if any(s in ast.unparse(n.value) for s in SRC) or is_tmp(n.value):
                    for t in n.targets: tmpish.update(names_of(t))
            elif isinstance(n, ast.With):
                for it in n.items:
                    if it.optional_vars is not None and any(s in ast.unparse(it.context_expr) for s in SRC): tmpish.update(names_of(it.optional_vars))

    # module-level names bound to a literal table (REQUIRED = [(...), ...], VARIANTS = (...), a dict of constants)
    # (an UPPER_CASE name bound to a display - list, tuple, set, dict - is a table of the test, even if its rows hold calls such as re.compile)
    displays = (ast.Tuple, ast.List, ast.Set, ast.Dict)
    tables = {t.id for n in tree.body if isinstance(n, ast.Assign) and (literal(n.value) or isinstance(n.value, displays))
              for t in n.targets if isinstance(t, ast.Name) and (literal(n.value) or t.id.isupper())}

    def safe(n, ok):
        if isinstance(n, ast.Constant): return True
        if isinstance(n, ast.Name): return n.id.isupper() or n.id in ok
        if isinstance(n, ast.Attribute): return n.attr.isupper()
        if isinstance(n, ast.JoinedStr): return all(safe(v.value, ok) if isinstance(v, ast.FormattedValue) else True for v in n.values)
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Div)): return safe(n.left, ok) and safe(n.right, ok)
        if isinstance(n, ast.IfExp): return safe(n.body, ok) and safe(n.orelse, ok)  # the condition picks, it does not build the name
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ('hex', 'hexdigest') and not n.args:
            return True  # a digest or hex string: characters 0-9a-f only
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'str' and len(n.args) == 1:
            return isinstance(n.args[0], ast.Name) and n.args[0].id in ok
        return False

    def display_safe(n, ok):
        """An inline display whose every element is a constant, an UPPER_CASE name or a safe expression (recursively)."""
        if isinstance(n, (ast.Tuple, ast.List, ast.Set)): return all(display_safe(e, ok) for e in n.elts)
        if isinstance(n, ast.Dict): return all(k is not None and display_safe(k, ok) and display_safe(v, ok) for k, v in zip(n.keys, n.values))
        return safe(n, ok)

    def literal_iter(n, ok):
        """True when iterating `n` yields only values written in the test (a display, a table, range, enumerate/items of those)."""
        if isinstance(n, displays) and display_safe(n, ok): return True
        if isinstance(n, ast.Name) and (n.id in tables or '@' + n.id in ok): return True
        if isinstance(n, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):  # {rel for _, rel, _ in TABLE}: a comprehension over literal sources
            inner = ok
            for gen in n.generators:
                if not literal_iter(gen.iter, inner): return False
                inner = bind(gen.target, gen.iter, inner)
            return display_safe(n.elt, inner)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            if n.func.id == 'range': return True
            if n.func.id in ('enumerate', 'sorted', 'list', 'tuple', 'reversed') and n.args: return literal_iter(n.args[0], ok)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ('items', 'keys', 'values') and not n.args:
            return literal_iter(n.func.value, ok)
        return False

    out = []

    def bind(target_node, iter_node, ok):
        names = set(names_of(target_node)) if literal_iter(iter_node, ok) else set()
        if (isinstance(iter_node, ast.Call) and isinstance(iter_node.func, ast.Name) and iter_node.func.id == 'enumerate'
                and isinstance(target_node, ast.Tuple) and target_node.elts):
            names |= set(names_of(target_node.elts[0]))  # the index of enumerate is a number whatever is enumerated
        return ok | {n for n in names if n not in tmpish}

    def visit(node, ok):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and is_tmp(node.left) and not safe(node.right, ok):
            out.append((node.lineno, ast.unparse(node)))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef, ast.Module)):
            ok = set() if not isinstance(node, ast.Module) else ok  # a function body starts without the loop names of the caller
        if isinstance(node, (ast.For, ast.AsyncFor)):
            inner = bind(node.target, node.iter, ok)
            visit(node.iter, ok)
            for stmt in node.body: inner = visit_stmt(stmt, inner)
            for stmt in node.orelse: visit_stmt(stmt, ok)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            inner = ok
            for gen in node.generators:
                visit(gen.iter, inner)
                inner = bind(gen.target, gen.iter, inner)
                for cond in gen.ifs: visit(cond, inner)
            for part in ([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]): visit(part, inner)
            return
        body_attrs = [f for f in ('body', 'orelse', 'finalbody', 'handlers') if isinstance(getattr(node, f, None), list)]
        scope = set(ok)
        for field, value in ast.iter_fields(node):
            if field in body_attrs:
                for stmt in value: scope = visit_stmt(stmt, scope) if isinstance(stmt, ast.stmt) else (visit(stmt, scope) or scope)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, ast.AST): visit(item, scope)
            elif isinstance(value, ast.AST):
                visit(value, scope)

    def visit_stmt(stmt, ok):
        """Visit one statement; returns the safe names after it (an assignment from a safe expression adds its target)."""
        visit(stmt, ok)
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            name = stmt.targets[0].id
            ok = ok - {name, '@' + name}
            if isinstance(stmt.value, displays) and display_safe(stmt.value, ok):
                return ok | {'@' + name}  # a local table written in the test
            if name not in tmpish and safe(stmt.value, ok):
                return ok | {name}
            return ok
        return ok

    visit(tree, set())
    return out


def repository_hits():
    found = {}
    for file in sorted((ROOT / 'tests').rglob('*.py')):
        if file.name in EXEMPT:
            continue
        try:
            tree = ast.parse(file.read_text(encoding='utf-8-sig'), filename=str(file))
        except (SyntaxError, ValueError, OSError):
            continue
        hits = scan(tree)
        if hits:
            found[file.relative_to(ROOT / 'tests').as_posix()] = hits
    return found


class Scanner(unittest.TestCase):
    def hits(self, source):
        return [text for _, text in scan(ast.parse(source))]

    def test_flags_data_names_joined_to_tmp(self):
        self.assertEqual(self.hits('def f(self, name):\n    (self.tmp / name).write_text("x")\n'), ['self.tmp / name'])
        self.assertEqual(self.hits('def f(tmp, rel):\n    return Path(tmp) / rel\n'), ['Path(tmp) / rel'])
        self.assertEqual(self.hits('def f(i):\n    return self.tmp / f"case-{i}"\n'), ["self.tmp / f'case-{i}'"])

    def test_follows_tempfile_origin_and_derived_paths(self):
        source = 'import tempfile\nbase = Path(tempfile.mkdtemp())\nwork = base / "w"\nx = work / part\n'
        self.assertEqual(self.hits(source), ['work / part'])
        self.assertEqual(self.hits('with tempfile.TemporaryDirectory() as d:\n    p = Path(d) / key\n'), ['Path(d) / key'])

    def test_literal_loops_digests_and_conditionals_are_safe(self):
        safe_sources = [
            'for name in ("a", "b"):\n    x = self.tmp / name\n',
            'for i in range(3):\n    x = self.tmp / f"case-{i}"\n',
            'for i, name in enumerate(("a", "b")):\n    x = self.tmp / f"{i}-{name}"\n',
            'TABLE = [("a", 1), ("b", 2)]\nfor name, n in TABLE:\n    x = self.tmp / name\n',
            'TABLE = {"a": 1}\nfor name, n in TABLE.items():\n    x = self.tmp / name\n',
            'names = {rel for _, rel, _ in REQUIRED}\nREQUIRED = [("a", "b", re.compile("x"))]\nfor f in {rel for _, rel, _ in REQUIRED}:\n    x = self.tmp / f\n',
            'def f(self):\n    local = {"a": 1}\n    for k in local:\n        x = self.tmp / k\n',
            'x = self.tmp / digest.hexdigest()\ny = self.tmp / data.hex()\n',
            'x = self.tmp / ("a.exe" if os.name == "nt" else "a")\n',
            'name = end.hex()\nx = self.tmp / name\n',
            'for pair in ((".a", RUN), (".b", RUN)):\n    x = self.tmp / pair[0]\n' if False else 'for part in (".a", ".b", RUN):\n    x = self.tmp / part\n',
            'for i, job in enumerate(jobs(data)):\n    x = self.tmp / f"job-{i}"\n',
        ]
        for source in safe_sources:
            with self.subTest(source=source):
                self.assertEqual(self.hits(source), [])

    def test_loops_over_data_and_leaks_of_the_safe_names_are_still_reported(self):
        unsafe_sources = [
            'for name in data:\n    x = self.tmp / name\n',
            'for name in names_from(path):\n    x = self.tmp / name\n',
            'for n, name in enumerate(rows(data)):\n    x = self.tmp / name\n',
            'for name in ("a", "b"):\n    pass\nx = self.tmp / name\n',
            'def f(self, name):\n    x = self.tmp / name\n',
            'for name in ("a",):\n    def g(self):\n        return self.tmp / name\n',
            'name = "a"\nname = data\nx = self.tmp / name\n',
            'x = self.tmp / (a if c else data)\n',
            'x = self.tmp / digest.hexdigest(data)\n',
            'for name in sorted(os.listdir(".")):\n    x = self.tmp / name\n',
        ]
        for source in unsafe_sources:
            with self.subTest(source=source):
                self.assertTrue(self.hits(source), source)

    def test_ignores_constants_and_unrelated_paths(self):
        self.assertEqual(self.hits('x = self.tmp / "a" / "b.txt"\ny = self.tmp / PLAN\nz = self.tmp / f"{RUN}/x.md"\nw = self.tmp / (A + "x")\n'), [])
        self.assertEqual(self.hits('x = self.library_dir / name\n'), [])
        self.assertEqual(self.hits('x = attempts / total\ny = template / part\nz = temperature / n\n'), [])
        self.assertEqual(self.hits('x = tmp_dir / n\ny = TMP / n\nz = temp / n\n'), ['tmp_dir / n', 'TMP / n', 'temp / n'])


class Repository(unittest.TestCase):
    def test_report(self):
        found = repository_hits()
        over = []
        for name, hits in found.items():
            allowed = BASELINE.get(name, 0)
            if len(hits) > allowed:
                over.append(f'{name}: {len(hits)} hits, baseline {allowed}: ' + '; '.join(f'{line}: {text[:60]}' for line, text in hits))
        total = sum(len(h) for h in found.values())
        self.assertGreater(total, 0, 'the scan found nothing: the scanner is broken')
        print(f'tmp-path guard (report): {total} hits in {len(found)} files; baseline {sum(BASELINE.values())}', file=sys.stderr)
        for line in over:
            print('  NEW ' + line, file=sys.stderr)
        if STRICT:
            self.assertEqual(over, [])


if __name__ == '__main__':
    unittest.main()
