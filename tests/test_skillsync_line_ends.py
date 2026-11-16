"""170-C: the readers of the status page cut the text of project files at line ends. `common.file_lines` splits at CRLF, LF and a lone CR and
NEVER at the other characters that `str.splitlines()` treats as line ends (VT, FF, FS, GS, RS, NEL, LS, PS): a title, a header value, a checkpoint
or a JSON string that holds one of them stays whole. Every test writes the bytes of the line ends itself, so the result does not depend on
`core.autocrlf` or the text mode of the platform."""
import ast
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow import dashboard as engine  # noqa: E402
from skillsync_workflow import status  # noqa: E402
from skillsync_workflow.common import decode_text, file_lines  # noqa: E402
from tests import test_skillsync_dashboard_server as base  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RARE = {'VT': '\x0b', 'FF': '\x0c', 'FS': '\x1c', 'GS': '\x1d', 'RS': '\x1e', 'NEL': '\x85', 'LS': ' ', 'PS': ' '}
ENDS = {'LF': b'\n', 'CRLF': b'\r\n', 'CR': b'\r'}


def with_ends(text, end):
    """The bytes of `text` (written with LF) with the given line end, decoded by the reader of the page (`decode_text`)."""
    return decode_text(text.encode('utf-8').replace(b'\n', end))


class FileLines(unittest.TestCase):
    def test_it_agrees_with_splitlines_for_cr_lf_and_crlf(self):
        for text in ('', 'a', 'a\n', 'a\n\n', '\n', 'a\nb', 'a\r\nb\r\n', 'a\rb\r', 'a\r\n\r\nb', 'a\n\rb', '\r\n\r\n', 'x\r\ny\nz\rw'):
            with self.subTest(text=text):
                self.assertEqual(file_lines(text), text.splitlines())

    def test_the_other_characters_stay_inside_the_line(self):
        for name, char in RARE.items():
            with self.subTest(char=name):
                self.assertEqual(file_lines(f'a{char}b\nc{char}'), [f'a{char}b', f'c{char}'])
                self.assertEqual(len(f'a{char}b'.splitlines()), 2)   # `splitlines()` would have cut here


PLAN = """# Plan: demo

Status: in_progress (czeka na GO plan)
Spec: docs/specs/demo.md
Przebieg: 2026-01-10-demo
Data: 2026-01-10
Oddanie: main
PR: #12 (link: https://example.test/pr/12)

## Progress

### Etap 1

- [x] S1 Pierwszy krok — abc1234 notatka
- [ ] S2 Drugi krok -- bez sha
- [ ] S3 Trzeci krok
"""
SPEC = """# Spec: demo

Status: approved

## Pytania otwarte

- Q1 [ZAŁOŻENIE]: Czy tak? — przyjęte: tak
- Q2: Blokujące pytanie
| Q3 | Pytanie w tabeli | więcej |

## Kontrakty

Treść kontraktu.

## Migracja

nie dotyczy
"""
CHECKPOINTS = """RUN=2026-01-10T09:00:00+01:00
- 09:10 zrobione: S1; w toku: S2 (a; b), S3; czeka: GO plan
  kontynuacja linii
- 09:20 eta: bieg do 17:30; trwa: etap 1
"""
READINESS = "Readiness: PASS\nData: 2026-01-10\nPlan-sha: `abcdef1234567`\n"


def read_lines(plan, spec, checkpoints):
    """The readers that go line by line (`file_lines`): they read a lone CR as a line end too."""
    fields = engine.parse_header(plan)[1]
    return {'header': engine.parse_header(plan), 'progress': engine.parse_progress(plan), 'checkpoints': engine.parse_checkpoints(checkpoints),
            'questions': engine.parse_questions(spec), 'oddanie': engine.delivery_target({'mode': 'direct'}, 'work', fields)}


def read_whole(plan, spec, readiness):
    """The readers that apply an anchored pattern to the whole text (`^...$`, MULTILINE): CRLF yes; a lone CR is not a line end for a pattern."""
    return {'status': engine.doc_status(plan), 'pr': (lambda m: m and m.groupdict())(engine.PLAN_PR_RE.search(plan)),
            'risky': engine.risky_sections(spec),
            'date': (lambda m: m and m.group('val'))(engine.READINESS_DATE_RE.search(readiness)),
            'sha': (lambda m: m and m.group('val'))(engine.READINESS_SHA_RE.search(readiness))}


class ReadersAtEveryLineEnd(unittest.TestCase):
    def test_the_line_readers_give_the_same_answer_for_lf_crlf_and_cr_bytes(self):
        expected = read_lines(PLAN, SPEC, CHECKPOINTS)
        self.assertEqual(expected['oddanie'], 'main')
        self.assertEqual(len(expected['progress'][0]['steps']), 3)
        for name, end in ENDS.items():
            with self.subTest(line_end=name):
                self.assertEqual(read_lines(*(with_ends(text, end) for text in (PLAN, SPEC, CHECKPOINTS))), expected)

    def test_the_whole_text_readers_give_the_same_answer_for_lf_and_crlf_bytes(self):
        expected = read_whole(PLAN, SPEC, READINESS)
        self.assertEqual(expected['status'][0], 'in_progress')
        self.assertEqual(expected['pr']['num'], '12')
        for name, end in (('CRLF', b'\r\n'),):
            with self.subTest(line_end=name):
                self.assertEqual(read_whole(*(with_ends(text, end) for text in (PLAN, SPEC, READINESS))), expected)

    def test_the_first_line_of_the_readiness_report_is_read_at_every_line_end(self):
        for name, end in ENDS.items():
            with self.subTest(line_end=name):
                text = with_ends(READINESS, end)
                first = next((line.strip() for line in file_lines(text) if line.strip()), '')
                self.assertEqual(engine.READINESS_RE.match(first).group('status'), 'PASS')

    def test_a_rare_separator_inside_a_line_cuts_nothing(self):
        for name, char in RARE.items():
            with self.subTest(char=name):
                plan = PLAN.replace('S2 Drugi krok -- bez sha', f'S2 Drugi{char}krok -- notatka{char}druga')
                step = engine.parse_progress(plan)[0]['steps'][1]
                self.assertEqual((step['title'], step['note']), (f'Drugi{char}krok', f'notatka{char}druga'))
                self.assertEqual(engine.parse_header(PLAN.replace('Spec: docs/specs/demo.md', f'Spec: docs/specs/a{char}b.md'))[1]['spec'],
                                 f'docs/specs/a{char}b.md')
                text = engine.parse_checkpoints(f'- 09:10 w toku: S1{char}ciag; czeka: GO plan\n')[1][0]['text']
                self.assertEqual(text, f'w toku: S1{char}ciag; czeka: GO plan')
                blocking = engine.parse_questions(SPEC.replace('Blokujące pytanie', f'Blokujące{char}pytanie'))[1]
                self.assertEqual(blocking[0]['question'], f'Blokujące{char}pytanie')


class PlanSectionAndGit(unittest.TestCase):
    def test_plan_section_keeps_a_rare_separator_inside_its_line_and_ends_at_the_real_heading(self):
        from skillsync_workflow.common import plan_section
        for name, char in RARE.items():
            with self.subTest(char=name):
                text = f"# P\n\n## Wynik\n\nzrobione{char}## Uwagi\nkoniec\n\n## Dalej\nx\n"
                self.assertEqual(plan_section(text, 'Wynik'), f'zrobione{char}## Uwagi\nkoniec')
        self.assertEqual(plan_section('## Wynik\r\nA\r\n\r\n## Dalej\r\n', 'Wynik'), 'A')

    def test_the_readers_of_git_files_agree_for_lf_and_crlf(self):
        results = []
        for end in (b'\n', b'\r\n'):
            root = Path(tempfile.mkdtemp(prefix='git-ends-'))
            self.addCleanup(lambda root=root: shutil.rmtree(root, ignore_errors=True))
            git = root / '.git'
            (git / 'refs' / 'heads').mkdir(parents=True)
            (git / 'HEAD').write_bytes(b'ref: refs/heads/feature/demo' + end)
            (git / 'packed-refs').write_bytes(b'# pack-refs with: peeled' + end + b'abc1234def5678abc1234def5678abc1234def56 refs/heads/feature/demo' + end)
            results.append(engine.read_git(root))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0]['branch'], 'feature/demo')


class ProjectAtEveryLineEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='line-ends-'))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def project(self, end):
        root = base.make_project(self.tmp / end.decode('ascii').encode('unicode_escape').decode())
        if end != b'\n':
            for path in list(root.rglob('*')):
                if path.is_file() and '.git' not in path.parts and path.suffix in ('.md', '.yaml', '.yml', '.jsonl', '.txt'):
                    path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', end))
        return root

    @staticmethod
    def comparable(value):
        volatile = {'mtime', 'fileSha', 'sha', 'generatedAt', 'updated', 'size', 'since', 'path', 'root', 'lastUpdate', 'lastReportAt'}   # bytes, paths and file times differ by construction
        if isinstance(value, dict):
            return {key: ProjectAtEveryLineEnd.comparable(item) for key, item in value.items() if key not in volatile}
        if isinstance(value, list):
            return [ProjectAtEveryLineEnd.comparable(item) for item in value]
        return value

    def test_the_status_of_a_project_is_the_same_whatever_the_line_ends_of_its_files_are(self):
        expected = self.comparable(engine.build_status(self.project(b'\n'), base.PLAN, manifest={}))
        got = self.comparable(engine.build_status(self.project(b'\r\n'), base.PLAN, manifest={}))
        self.assertEqual(got, expected)

    def test_the_board_status_of_the_page_is_the_same_for_lf_and_crlf(self):
        from skillsync_workflow import dashboard_http
        answers = []
        for end in (b'\n', b'\r\n'):
            board = dashboard_http.Board(self.project(end), prototype_port=1)
            self.addCleanup(board.stop.set)
            board.prototype_acceptance = lambda slug: None
            answers.append(self.comparable(json.loads(json.dumps(dashboard_http.redact(board.status()), ensure_ascii=False))))
        self.assertEqual(answers[0], answers[1])

    def entry(self, **fields):
        return {'word': 'GO plan', 'at': '2026-11-16T09:00:00Z', 'sha': 'a' * 40, 'by': 'user', 'source': 'cli', **fields}

    def test_an_approval_with_a_rare_separator_in_its_json_string_is_read_by_both_readers(self):
        root = base.make_project(self.tmp / 'approvals')
        run = base.RUN.split('/')[-1]
        # Only NEL, LS and PS can stand raw inside a JSON string (`json.dumps` escapes the other control characters and a strict `json.loads` refuses
        # them raw), and only they were cut by `splitlines()` before.
        for name, char in {key: RARE[key] for key in ('NEL', 'LS', 'PS')}.items():
            with self.subTest(char=name):
                line = json.dumps(self.entry(by=f'a{char}b'), ensure_ascii=False)   # a raw separator inside the string, as a hand-written file has it
                self.assertIn(char, line)
                (root / base.RUN / 'approvals.jsonl').write_bytes(line.encode('utf-8') + b'\n')
                self.assertEqual((len(status.read_approvals(root)), len(engine.run_approvals(root, run))), (1, 1))

    def test_the_duplicate_check_of_record_approval_sees_such_an_entry(self):
        root = base.make_project(self.tmp / 'duplicate')
        run = base.RUN.split('/')[-1]
        (root / base.RUN / 'approvals.jsonl').write_bytes(json.dumps(self.entry(by='a b'), ensure_ascii=False).encode('utf-8') + b'\n')
        report = status.record_approval(root, 'GO plan', run, 'a' * 40, source='cli')
        self.assertEqual(report['status'], 'BLOCKED', report)
        self.assertIn('already recorded', report['reason'])

    def test_a_step_title_with_ls_stays_whole_and_the_page_and_the_json_show_it_as_text(self):
        root = base.make_project(self.tmp / 'ls')
        path = root / base.PLAN
        text = path.read_text(encoding='utf-8')
        path.write_text(text.replace('S3a API terminów', 'S3a API terminów <b>x</b>'), encoding='utf-8')
        data = engine.build_status(root, base.PLAN, manifest={})
        found = json.dumps(data, ensure_ascii=False)
        self.assertIn('API terminów <b>x</b>', found)                          # whole, no cut, and still plain text of the JSON
        self.assertEqual(json.loads(found)['plan']['path'], base.PLAN)                # the JSON written with ensure_ascii=False is valid
        from skillsync_workflow import dashboard_http
        html = dashboard_http.render_markdown('# T\n\nAPI terminów <b>x</b>\n', 'docs/plans/p.md')
        self.assertIn('API terminów', html)
        self.assertNotIn('<b>', html)                                                 # the markup of the text is escaped, a separator is just a character


LINE_END_LITERALS = ('\n', '\r\n', '\r')
SPLIT_METHODS = ('split', 'rsplit', 'partition', 'rpartition')


def line_split_hits(source):
    """`(function, line)` of every place that splits text at a line end the raw way: any use of the name `.splitlines` (a call, `str.splitlines(t)`,
    an alias `f = t.splitlines`), `getattr(x, 'splitlines')`, and `.split/.rsplit/.partition/.rpartition` whose separator (first argument or `sep=`)
    is a literal line end."""
    found = []

    def separator(node):
        if node.args and isinstance(node.args[0], ast.Constant):
            return node.args[0].value
        for keyword in node.keywords:
            if keyword.arg == 'sep' and isinstance(keyword.value, ast.Constant):
                return keyword.value.value
        return None

    def visit(node, function):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if isinstance(node, ast.Attribute) and node.attr == 'splitlines':
            found.append((function, node.lineno))
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == 'getattr' and len(node.args) > 1 \
                    and isinstance(node.args[1], ast.Constant) and node.args[1].value == 'splitlines':
                found.append((function, node.lineno))
            elif isinstance(node.func, ast.Attribute) and node.func.attr in SPLIT_METHODS and separator(node) in LINE_END_LITERALS:
                found.append((function, node.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child, function)
    visit(ast.parse(source), '<module>')
    return found


class NoRawLineSplitting(unittest.TestCase):
    """The text of project files in the readers of the page is split only by `file_lines`: the raw ways above are refused. An exception is a (file,
    function) pair with the reason (text that does not come from a project file, or is normalized first); the list is checked for stale entries.
    Not caught (no such code in these files, listed so that nobody takes the guard for more than it is): a split by a separator held in a variable,
    `re.split` with a pattern, `io.StringIO(text)` and iteration over a file handle."""
    FILES = ('skillsync_workflow/dashboard.py', 'skillsync_workflow/dashboard_http.py', 'skillsync_workflow/common.py',
             'skillsync_workflow/plan_phases.py', 'skillsync_workflow/quality.py')   # the files whose readers of project text are all converted
    EXCEPTIONS = {
        ('skillsync_workflow/dashboard_http.py', 'render_markdown'):
            'the text is normalized first (`\\r\\n` and a lone `\\r` become `\\n`) and the other separators are ordinary characters of the line',
    }

    def hits(self):
        return [(rel, function, line) for rel in self.FILES for function, line in line_split_hits((ROOT / rel).read_text(encoding='utf-8'))]

    def test_no_reader_splits_project_text_with_splitlines_or_split_on_a_line_end(self):
        refused = [f'{rel}:{line} in {function}' for rel, function, line in self.hits() if (rel, function) not in self.EXCEPTIONS]
        self.assertEqual(refused, [], 'use common.file_lines (see the docstring of this guard)')

    def test_the_exceptions_are_alive(self):
        used = {(rel, function) for rel, function, _line in self.hits()}
        self.assertEqual(sorted(set(self.EXCEPTIONS) - used), [], 'a stale exception: remove it')

    def test_the_guard_catches_the_raw_ways_and_leaves_the_clean_code_alone(self):
        caught = {
            'method call': 'def f(t):\n    return t.splitlines()\n',
            'unbound call': 'def f(t):\n    return str.splitlines(t)\n',
            'alias': 'def f(t):\n    g = t.splitlines\n    return g()\n',
            'getattr': "def f(t):\n    return getattr(t, 'splitlines')()\n",
            'split LF': "def f(t):\n    return t.split('\\n')\n",
            'split CRLF with a limit': 'def f(t):\n    return t.split("\\r\\n", 1)\n',
            'split sep=': "def f(t):\n    return t.split(sep='\\n')\n",
            'rsplit': "def f(t):\n    return t.rsplit('\\n')\n",
            'partition': "def f(t):\n    return t.partition('\\n')\n",
        }
        for label, source in caught.items():
            with self.subTest(label=label):
                self.assertEqual([function for function, _ in line_split_hits(source)], ['f'])
        clean = "def f(t):\n    return t.split(','), t.partition('='), file_lines(t), t.split()\n"
        self.assertEqual(line_split_hits(clean), [])


if __name__ == '__main__':
    unittest.main()
