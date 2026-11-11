"""`skillsync dashboard show` against the contract fixtures (tests/fixtures/dashboard), compatibility with `skillsync status`,
read-only behaviour (K2), errors and the shared parsers of `status.py` (K3)."""
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from skillsync_workflow import dashboard, status  # noqa: E402

CLI = ROOT / 'skillsync'
FIXTURES = ROOT / 'tests' / 'fixtures' / 'dashboard'
CASES = sorted(path.name for path in FIXTURES.iterdir() if path.is_dir())
MTIME = datetime(2026, 1, 11, 10, 0, tzinfo=timezone.utc).timestamp()
# Cases whose input is committed into a fresh git repository (branch GIT_BRANCH) before `dashboard show`.
GIT_CASES = ('cp-ready',)
GIT_BRANCH = 'feat/demo'


def git_env(env):
    """Git without the user's configuration and with a neutral identity and fixed dates."""
    return {**env, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_AUTHOR_NAME': 'demo',
            'GIT_AUTHOR_EMAIL': 'demo@example.invalid', 'GIT_COMMITTER_NAME': 'demo',
            'GIT_COMMITTER_EMAIL': 'demo@example.invalid', 'GIT_AUTHOR_DATE': '2026-01-11T10:00:00Z',
            'GIT_COMMITTER_DATE': '2026-01-11T10:00:00Z'}


def git_commit_all(root, env):
    """`git init` + one commit of everything in `root`; returns the full HEAD sha."""
    env = git_env(env)
    for args in (('init', '-q', '-b', GIT_BRANCH), ('add', '-A'), ('-c', 'commit.gpgsign=false', 'commit', '-q', '-m', 'demo')):
        subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, env=env, timeout=60)
    return subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True,
                          env=env, timeout=60).stdout.strip()


def normalized(data, head=None):
    """The fixture normalization of docs/dashboard-contract.md ("Normalizacja w fixtures"); with `head` every form of the
    HEAD sha (full, 12 and 8 characters) becomes `<head>`."""
    data = json.loads(json.dumps(data))
    data['project']['path'] = '<root>'
    data['generatedAt'] = '2026-01-12T12:00:00+00:00'
    data['app']['version'] = '<version>'
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    if head:
        for form in (head, head[:12], head[:8]):
            text = text.replace(form, '<head>')
    return text


def tree_state(root):
    rows = []
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        rows.append((path.relative_to(root).as_posix(), info.st_mode, info.st_mtime_ns, digest))
    return rows


class DashboardFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skillsync-dashboard-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.env = {**os.environ, 'TZ': 'UTC', 'SKILLSYNC_CONFIG': str(self.base / 'machine-config.yaml'),
                    'XDG_CACHE_HOME': str(self.base / 'xdg-cache'), 'XDG_DATA_HOME': str(self.base / 'xdg-data')}

    def project(self, case):
        root = self.base / case / 'demo'
        shutil.copytree(FIXTURES / case / 'input', root)
        self.head = git_commit_all(root, self.env) if case in GIT_CASES else None
        for path in root.rglob('*'):
            if path.is_file() and '.git' not in path.relative_to(root).parts:
                os.utime(path, (MTIME, MTIME))
        return root

    def cli(self, *args, code=0):
        result = subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True, timeout=60,
                                env=self.env, cwd=self.base)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout)


class DashboardShow(DashboardFixture):
    def test_show_matches_every_fixture(self):
        self.assertGreaterEqual(len(CASES), 7)
        for case in CASES:
            with self.subTest(case=case):
                report = self.cli('dashboard', 'show', self.project(case), '--json')
                expected = (FIXTURES / case / 'expected.json').read_text(encoding='utf-8')
                self.assertEqual(normalized(report, self.head), expected)

    def test_show_without_json_prints_the_same_contract(self):
        root = self.project('plan-in-progress')
        self.assertEqual(normalized(self.cli('dashboard', 'show', root)), normalized(self.cli('dashboard', 'show', root, '--json')))

    def test_plan_option_selects_a_plan_and_unknown_path_falls_back_to_auto(self):
        root = self.project('plan-in-progress')
        chosen = self.cli('dashboard', 'show', root, '--json', '--plan', './docs/plans/2026-01-10-demo-feature.md')
        self.assertEqual((chosen['planAuto'], chosen['plan']['path']), (False, 'docs/plans/2026-01-10-demo-feature.md'))
        unknown = self.cli('dashboard', 'show', root, '--json', '--plan', 'docs/plans/missing.md')
        self.assertEqual((unknown['planAuto'], unknown['mode']), (True, 'plan'))

    def test_show_writes_nothing_in_the_project(self):
        for case in ('plan-in-progress', 'readiness-blocked', 'run-mode'):
            with self.subTest(case=case):
                root = self.project(case)
                before = tree_state(root)
                self.cli('dashboard', 'show', root, '--json')
                self.assertEqual(tree_state(root), before)

    def test_missing_project_or_manifest_is_blocked_with_exit_2(self):
        missing = self.cli('dashboard', 'show', self.base / 'nope', '--json', code=2)
        self.assertEqual((missing['schema'], missing['status']), (1, 'BLOCKED'))
        self.assertIn('manifest', missing['reason'].lower())
        bare = self.base / 'bare'
        bare.mkdir()
        self.assertEqual(self.cli('dashboard', 'show', bare, code=2)['status'], 'BLOCKED')

    def test_subcommand_is_required(self):
        result = subprocess.run([sys.executable, str(CLI), 'dashboard', str(self.base)], capture_output=True, text=True,
                                timeout=60, env=self.env)
        self.assertEqual(result.returncode, 2)


class StatusCompatibility(DashboardFixture):
    def test_step_counters_equal_status_for_plans_with_progress(self):
        compared = 0
        for case in CASES:
            root = self.project(case)
            report = self.cli('status', root, '--json')
            for plan in report['plans']:
                if not plan['steps']:
                    continue
                with self.subTest(case=case, plan=plan['path']):
                    board = self.cli('dashboard', 'show', root, '--json', '--plan', plan['path'])
                    self.assertEqual({'total': board['progress']['total'], 'done': board['progress']['done']}, plan['steps'])
                    self.assertEqual(board['plans'][0]['status'], plan['status'])
                    compared += 1
        self.assertGreaterEqual(compared, 3)

    def test_documented_difference_steps_without_id_and_code_blocks(self):
        """`status._progress` counts every `- [x]` of `## Progress`; the dashboard counts only steps with an id outside code
        blocks (contract section "Zgodność z `skillsync status`")."""
        text = ('# Plan\n\nStatus: in_progress\n\n## Progress\n### Faza 1: A\n- [x] F1.1 Krok\n- [ ] bez identyfikatora\n'
                '```\n- [ ] F9.9 w bloku kodu\n```\n')
        self.assertEqual(status._progress(text), {'total': 3, 'done': 1})
        steps = [step for phase in dashboard.parse_progress(text) for step in phase['steps']]
        self.assertEqual([(step['id'], step['done']) for step in steps], [('F1.1', True)])

    def test_legacy_steps_section_is_status_only(self):
        text = '# Plan\n\nStatus: done\n\n## Kroki\n- [x] S1 Krok\n'
        self.assertEqual(status._progress(text), {'total': 1, 'done': 1})
        self.assertEqual(dashboard.parse_progress(text), [])


class SharedParsers(unittest.TestCase):
    """K3: the dashboard uses the status.py parsers instead of its own copies."""

    def test_functions_come_from_status(self):
        self.assertIs(dashboard.git_blob_sha, status.git_blob_sha)
        self.assertIs(dashboard._status_fields, status._status_fields)
        self.assertIs(dashboard._approval_at, status._approval_at)
        self.assertIs(dashboard._APPROVAL_SHA, status._APPROVAL_SHA)
        self.assertIs(dashboard.APPROVAL_WORDS, status.APPROVAL_WORDS)

    def test_no_duplicate_definitions_and_no_skill_script_import(self):
        source = (ROOT / 'skillsync_workflow' / 'dashboard.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        defined = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
        self.assertFalse(defined & {'git_blob_sha', '_status_fields', '_progress', 'read_approvals', '_approval_at',
                                    'normalize_status'})
        imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertIn('status', imported)
        self.assertNotIn('dashboard_server', source)

    def test_status_word_follows_status_parser(self):
        self.assertEqual(dashboard.doc_status('# T\n\n**Status:** `done`\n'), ('done', 'done'))
        self.assertEqual(dashboard.doc_status('# T\n\nStatus: draft (czeka na GO plan)\n'), ('draft', 'draft (czeka na GO plan)'))
        self.assertEqual(dashboard.doc_status('# T\n'), (None, None))


class NewFields(unittest.TestCase):
    def test_questions_variants(self):
        text = ('# Spec\n\n## Pytania otwarte\n\n- Q1 [ZAŁOŻENIE]: a — przyjęte: tak\n- Q2 [założenie]: b -- przyjęte: nie\n'
                '- Q3 [ROZSTRZYGNIĘTE]: c\n- **Q4**: d?\n- Q5 [ZAŁOŻENIE]: bez wartości\n| Q6 | e? | x |\n| Q1 | duplikat | |\n'
                '```\n- Q8: w bloku kodu\n```\n\n## Dalej\n\n- Q7: poza sekcją\n')
        assumptions, blocking = dashboard.parse_questions(text)
        self.assertEqual(assumptions, [{'id': 'Q1', 'question': 'a', 'accepted': 'tak'},
                                       {'id': 'Q2', 'question': 'b', 'accepted': 'nie'},
                                       {'id': 'Q5', 'question': 'bez wartości', 'accepted': ''}])
        self.assertEqual(blocking, [{'id': 'Q4', 'question': 'd?'}, {'id': 'Q6', 'question': 'e?'}])

    def test_readiness_unrecognized_first_line_keeps_source(self):
        with tempfile.TemporaryDirectory(prefix='skillsync-dashboard-') as temp:
            root = Path(temp)
            run = root / '.skillsync/runs/r1'
            run.mkdir(parents=True)
            (run / 'readiness.md').write_text('# Brief\nReadiness: PASS\nData: 2026-01-11\n', encoding='utf-8')
            self.assertEqual(dashboard.read_readiness(root, 'r1', True),
                             {'status': None, 'date': '2026-01-11', 'planSha': None,
                              'source': '.skillsync/runs/r1/readiness.md'})
            self.assertEqual(dashboard.read_readiness(root, 'r2', False)['source'], None)

    def test_substep_seconds_cross_midnight_and_missing_data(self):
        progress = dashboard.build_progress(
            [{'name': 'Faza 1: A', 'steps': [{'id': 'F1.1', 'title': 'x', 'done': True, 'note': '', 'sha': None},
                                             {'id': 'F1.2', 'title': 'y', 'done': False, 'note': '', 'sha': None},
                                             {'id': 'S1', 'title': 'z', 'done': False, 'note': '', 'sha': None}]}],
            None, None, ['F1.2'])
        checkpoints = [{'time': '23:50', 'text': 'w toku: F1.1'}, {'time': '00:10', 'text': 'zrobione: F1.1, F1.2'}]
        rows = {row['id']: row for row in dashboard.build_substeps(progress, checkpoints)}
        self.assertEqual(set(rows), {'F1.1', 'F1.2'})
        self.assertEqual((rows['F1.1']['seconds'], rows['F1.1']['missingData']), (1200, False))
        self.assertEqual((rows['F1.2']['state'], rows['F1.2']['seconds'], rows['F1.2']['missingData']), ('reported', None, True))

    def test_approvals_rows_follow_status_rules(self):
        with tempfile.TemporaryDirectory(prefix='skillsync-dashboard-') as temp:
            root = Path(temp)
            run = root / '.skillsync/runs/r1'
            run.mkdir(parents=True)
            good = {'schema': 1, 'at': '2026-01-10T08:30:00Z', 'word': 'GO plan', 'sha': 'abcdef0', 'by': 'user', 'source': 'cli'}
            lines = [good, {**good, 'word': 'MAYBE'}, {**good, 'sha': 'not-hex'}, {**good, 'at': 'yesterday'}]
            (run / 'approvals.jsonl').write_text('\n'.join(json.dumps(row) for row in lines) + '\nnot json\n', encoding='utf-8')
            self.assertEqual(dashboard.run_approvals(root, 'r1'), [good])
            self.assertEqual([row['word'] for row in status.read_approvals(root)], ['GO plan'])


if __name__ == '__main__':
    unittest.main()
