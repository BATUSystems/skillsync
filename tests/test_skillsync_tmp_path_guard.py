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
    'test_skillsync_agents_md.py': 1,
    'test_skillsync_ci_workflows.py': 1,
    'test_skillsync_dashboard_plan_cache.py': 1,
    'test_skillsync_dashboard_plan_choice.py': 2,
    'test_skillsync_dashboard_ports.py': 1,
    'test_skillsync_dashboard_redaction.py': 2,
    'test_skillsync_dashboard_server.py': 7,
    'test_skillsync_feature_demo.py': 3,
    'test_skillsync_install_sh.py': 2,
    'test_skillsync_layers_merge.py': 1,
    'test_skillsync_line_ends.py': 1,
    'test_skillsync_measurements.py': 1,
    'test_skillsync_merge_modules.py': 1,
    'test_skillsync_packaging.py': 1,
    'test_skillsync_platform_sensitive.py': 1,
    'test_skillsync_prototype_engine.py': 2,
    'test_skillsync_prototype_library.py': 2,
    'test_skillsync_prototype_skills.py': 3,
    'test_skillsync_quick_skills.py': 2,
    'test_skillsync_selfcmd.py': 1,
    'test_skillsync_workflow.py': 1,
}
REASON = 'start: not reviewed one by one; names written by the test itself or loop indices (a data-derived name needs tmp_paths)'

TMP=re.compile(r'tmp|temp',re.I)
SRC=('tempfile','mkdtemp','TemporaryDirectory','NamedTemporary')
def names_of(t):
    if isinstance(t,ast.Name): return [t.id]
    if isinstance(t,ast.Attribute): return [t.attr]
    if isinstance(t,(ast.Tuple,ast.List)): return [n for e in t.elts for n in names_of(e)]
    return []
def scan(tree):
    tmpish=set()
    def is_tmp(n):
        if isinstance(n,ast.Name): return bool(TMP.search(n.id)) or n.id in tmpish
        if isinstance(n,ast.Attribute): return bool(TMP.search(n.attr)) or n.attr in tmpish
        if isinstance(n,ast.Call):
            f=n.func; fn=f.id if isinstance(f,ast.Name) else getattr(f,'attr','')
            if fn in('Path','PurePath','resolve','realpath') or isinstance(f,ast.Attribute) and fn in('resolve',):
                return any(is_tmp(a) for a in n.args) or (isinstance(f,ast.Attribute) and is_tmp(f.value))
            return False
        if isinstance(n,ast.BinOp) and isinstance(n.op,ast.Div): return is_tmp(n.left)
        return False
    for _ in range(3):
        for n in ast.walk(tree):
            if isinstance(n,ast.Assign):
                if any(s in ast.unparse(n.value) for s in SRC) or is_tmp(n.value):
                    for t in n.targets: tmpish.update(names_of(t))
            elif isinstance(n,ast.With):
                for it in n.items:
                    if it.optional_vars is not None and any(s in ast.unparse(it.context_expr) for s in SRC): tmpish.update(names_of(it.optional_vars))
    def safe(n):
        if isinstance(n,ast.Constant): return True
        if isinstance(n,ast.Name): return n.id.isupper()
        if isinstance(n,ast.Attribute): return n.attr.isupper()
        if isinstance(n,ast.JoinedStr): return all(safe(v.value) if isinstance(v,ast.FormattedValue) else True for v in n.values)
        if isinstance(n,ast.BinOp) and isinstance(n.op,(ast.Add,ast.Div)): return safe(n.left) and safe(n.right)
        return False
    out=[]
    for n in ast.walk(tree):
        if isinstance(n,ast.BinOp) and isinstance(n.op,ast.Div) and is_tmp(n.left) and not safe(n.right):
            out.append((n.lineno,ast.unparse(n)))
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

    def test_ignores_constants_and_unrelated_paths(self):
        self.assertEqual(self.hits('x = self.tmp / "a" / "b.txt"\ny = self.tmp / PLAN\nz = self.tmp / f"{RUN}/x.md"\nw = self.tmp / (A + "x")\n'), [])
        self.assertEqual(self.hits('x = self.library_dir / name\n'), [])


class Repository(unittest.TestCase):
    def test_report(self):
        found = repository_hits()
        over = []
        for name, hits in found.items():
            allowed = BASELINE.get(name, 0)
            if len(hits) > allowed:
                over.append(f'{name}: {len(hits)} hits, baseline {allowed}: ' + '; '.join(f'{line}: {text[:60]}' for line, text in hits))
        total = sum(len(h) for h in found.values())
        print(f'tmp-path guard (report): {total} hits in {len(found)} files; baseline {sum(BASELINE.values())}', file=sys.stderr)
        for line in over:
            print('  NEW ' + line, file=sys.stderr)
        if STRICT:
            self.assertEqual(over, [])


if __name__ == '__main__':
    unittest.main()
