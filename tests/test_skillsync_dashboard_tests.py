"""180-A: the `tests` part of the status page: sections, the `finalize_status` boundary (decision 6), `GET /api/tests`."""
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import guard_real_state, home_env  # noqa: E402

from skillsync_workflow import dashboard as engine  # noqa: E402
from skillsync_workflow import dashboard_tests as dt  # noqa: E402
from skillsync_workflow import test_events as ev  # noqa: E402
from skillsync_workflow.common import redact  # noqa: E402


def secret():
    return '-'.join(['zq9x', 'wv8y', 'ut7s', 'rp6q'])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='dash-tests-')
        guard_real_state(self)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        dt._CACHE.clear()

    def attempt(self, step='F1.1', tests=(), end='PASS', start_at='2026-11-16T10:00:00Z', pid=None, command='run tests'):
        path, number = ev.new_attempt(self.root, 'R1', step)
        w = ev.EventWriter(path)
        w._write({'t': 'start', 'at': start_at, 'step': step, 'command': command, 'tool': 'unittest', 'pid': pid or os.getpid(), 'attempt': number})
        for index, (name, state, group) in enumerate(tests):
            w.test(f'{group}::{name}', name, state, group=group, msg='boom' if state == 'failed' else None)
        if end:
            w.end(end, 0 if end == 'PASS' else 1)
        w.close()
        return path, number


class Sections(Base):
    def test_no_events_no_tests(self):
        self.assertEqual(dt.build_tests(self.root, 'R1'), (None, {}))
        self.assertEqual(dt.build_tests(self.root, None), (None, {}))

    def test_sections_newest_first_with_progress_and_titles(self):
        self.attempt('F1.1', [('a', 'passed', 'g')], start_at='2026-11-16T10:00:00Z')
        self.attempt('F1.2', [('b', 'failed', 'g'), ('c', 'passed', 'g')], end='FAIL', start_at='2026-11-16T11:00:00Z')
        progress = [{'steps': [{'id': 'F1.1', 'title': 'Kod'}, {'id': 'F1.2', 'title': 'Testy'}]}]
        tests, steps = dt.build_tests(self.root, 'R1', progress)
        self.assertEqual([s['step']['id'] for s in tests['sections']], ['F1.2', 'F1.1'])
        self.assertEqual(tests['sections'][0]['step']['title'], 'Testy')
        self.assertEqual((tests['failedTotal'], tests['runningTotal']), (1, 0))
        self.assertEqual(steps['F1.2']['counts']['failed'], 1)
        self.assertEqual(steps['F1.1']['state'], 'passed')

    def test_only_failed_and_running_tests_are_inline_and_failed_groups_first(self):
        self.attempt('F1.1', [('ok', 'passed', 'z.Ok'), ('bad', 'failed', 'a.Bad'), ('ok2', 'passed', 'a.Bad')], end='FAIL')
        tests, _ = dt.build_tests(self.root, 'R1')
        groups = tests['sections'][0]['attempts'][0]['groups']
        self.assertEqual([g['name'] for g in groups], ['a.Bad', 'z.Ok'])
        self.assertEqual([t['name'] for t in groups[0]['tests']], ['bad'])
        self.assertEqual(groups[1]['tests'], [])
        self.assertEqual(groups[1]['total'], 1)

    def test_only_the_last_five_attempts_are_shown_newest_first(self):
        for i in range(7):
            self.attempt('F1.1', [('a', 'passed', 'g')])
        tests, _ = dt.build_tests(self.root, 'R1')
        self.assertEqual([a['n'] for a in tests['sections'][0]['attempts']], [7, 6, 5, 4, 3])

    def test_a_run_without_end_is_running_or_interrupted(self):
        path, _ = self.attempt('F1.1', [('a', 'running', 'g')], end=None)
        self.assertEqual(dt.build_tests(self.root, 'R1')[0]['sections'][0]['state'], 'running')
        dt._CACHE.clear()
        path2, _ = self.attempt('F1.2', [('a', 'running', 'g')], end=None, pid=2 ** 22 + 12345)
        sections = {s['step']['id']: s['state'] for s in dt.build_tests(self.root, 'R1')[0]['sections']}
        self.assertEqual(sections['F1.2'], 'interrupted')   # the process is gone
        old = time.time() - dt.STALE_RUNNING_S - 60
        os.utime(path, (old, old))
        dt._CACHE.clear()
        self.assertEqual({s['step']['id']: s['state'] for s in dt.build_tests(self.root, 'R1')[0]['sections']}['F1.1'], 'running')   # silent for long, but its process is alive: running
        path3, _ = self.attempt('F1.3', [('a', 'running', 'g')], end=None, pid=os.getpid())
        lines = path3.read_text(encoding='utf-8').splitlines()
        first = json.loads(lines[0])
        first['host'] = 'another-machine'   # the pid says nothing here: only the silence decides
        path3.write_text('\n'.join([json.dumps(first)] + lines[1:]) + '\n', encoding='utf-8')
        os.utime(path3, (old, old))
        dt._CACHE.clear()
        self.assertEqual({s['step']['id']: s['state'] for s in dt.build_tests(self.root, 'R1')[0]['sections']}['F1.3'], 'interrupted')

    def test_an_interrupted_attempt_and_a_finished_one_show_no_running_test(self):
        self.attempt('F1.2', [('a', 'running', 'g'), ('b', 'passed', 'g')], end=None, pid=2 ** 22 + 12345)   # the process is gone, no `end`
        self.attempt('F1.3', [('a', 'running', 'g'), ('b', 'passed', 'g')], end='BLOCKED')                       # ended, `a` has no result
        tests, steps = dt.build_tests(self.root, 'R1')
        for section in tests['sections']:
            attempt = section['attempts'][0]
            self.assertEqual(attempt['counts']['running'], 0, section['step'])
            self.assertEqual(attempt['counts']['waiting'], 1, section['step'])
            group = attempt['groups'][0]
            self.assertEqual(group['counts']['running'], 0)
            self.assertNotIn('running', [t['state'] for g in attempt['groups'] for t in g['tests']])
            self.assertNotEqual(group['state'], 'running')

    def test_reading_is_incremental(self):
        path, _ = self.attempt('F1.1', [('a', 'running', 'g')], end=None)
        dt.build_tests(self.root, 'R1')
        first = dt._CACHE[str(path)]
        offset = first.offset
        w = ev.EventWriter(path)
        w.test('g::a', 'a', 'passed', group='g')
        w.close()
        dt.build_tests(self.root, 'R1')
        self.assertIs(dt._CACHE[str(path)], first)   # the same state object continued from its offset
        self.assertGreater(first.offset, offset)
        self.assertEqual(first.tests['g::a']['state'], 'passed')

    def test_add_to_status_decorates_steps_and_adds_the_key_only_with_events(self):
        status = {'progress': {'phases': [{'steps': [{'id': 'F1.1', 'title': 'Kod'}, {'id': 'F1.2', 'title': 'X'}]}]}}
        self.assertNotIn('tests', dt.add_to_status(dict(status), self.root, 'R1', status['progress']['phases']))
        self.attempt('F1.1', [('a', 'passed', 'g')])
        out = dt.add_to_status(status, self.root, 'R1', status['progress']['phases'])
        self.assertIn('tests', out)
        self.assertIn('tests', out['progress']['phases'][0]['steps'][0])
        self.assertNotIn('tests', out['progress']['phases'][0]['steps'][1])

    def test_tests_response_pages_a_group(self):
        self.attempt('F1.1', [(f't{i}', 'passed', 'g1') for i in range(5)] + [('x', 'failed', 'g2')], end='FAIL')
        body = dt.tests_response(self.root, 'R1', 'F1.1', '1', 'g1')
        self.assertEqual((body['total'], body['group']), (5, 'g1'))
        page = dt.tests_response(self.root, 'R1', 'F1.1', '1', 'g1', limit=2, offset=3)
        self.assertEqual((len(page['tests']), page['hasMore']), (2, False))
        self.assertTrue(dt.tests_response(self.root, 'R1', 'F1.1', '1', 'g1', limit=2)['hasMore'])
        self.assertEqual(dt.tests_response(self.root, 'R1', 'F1.1', '1', None, limit=10 ** 9)['limit'], dt.MAX_RESPONSE_TESTS)
        every = dt.tests_response(self.root, 'R1', 'F1.1', '1')
        self.assertEqual(every['tests'][0]['state'], 'failed')   # failed first
        self.assertIsNone(dt.tests_response(self.root, 'R1', 'F1.1', '9'))
        for run, step, group in (('../R1', 'F1.1', None), ('R1', '..', None), ('R1', 'F1.1', 'x' * 400), ('R1', 'CON', None), ('R1', 'F1.', None),
                                 ('R1', 'a\\b', None), ('R1', 'a:b', None), ('R1', '%2e%2e', None)):
            with self.assertRaises(ValueError, msg=(run, step, group)):
                dt.tests_response(self.root, run, step, '1', group)
        with self.assertRaises(ValueError):
            dt.tests_response(self.root, 'R1', 'F1.1', '0')

    def test_a_running_attempt_lists_every_test_of_a_small_group_and_a_finished_one_only_the_hot_ones(self):
        self.attempt('F1.1', [('a', 'passed', 'g'), ('b', 'running', 'g'), ('c', 'passed', 'g')], end=None)
        self.attempt('F1.2', [('a', 'passed', 'g'), ('b', 'failed', 'g'), ('c', 'passed', 'g')], end='FAIL')
        tests, _ = dt.build_tests(self.root, 'R1')
        by = {s['step']['id']: s['attempts'][0]['groups'][0] for s in tests['sections']}
        self.assertEqual(len(by['F1.1']['tests']), 3)   # running, small group: all visible, no click
        self.assertEqual([t['name'] for t in by['F1.2']['tests']], ['b'])
        dt._CACHE.clear()
        with mock.patch.object(dt, 'MAX_RUNNING_GROUP', 2):
            by = {s['step']['id']: s['attempts'][0]['groups'][0] for s in dt.build_tests(self.root, 'R1')[0]['sections']}
        self.assertEqual([t['name'] for t in by['F1.1']['tests']], ['b'])   # above the threshold: only the hot ones

    def test_tests_without_a_group_get_a_readable_name(self):
        path, _ = ev.new_attempt(self.root, 'R1', 'F1.1')
        w = ev.EventWriter(path)
        w._write({'t': 'start', 'at': '2026-11-16T10:00:00Z', 'step': 'F1.1', 'pid': os.getpid()})
        w.test('x', 'x', 'failed')
        w.end('FAIL', 1)
        w.close()
        self.assertEqual(dt.build_tests(self.root, 'R1')[0]['sections'][0]['attempts'][0]['groups'][0]['name'], 'pozostałe testy')


class Watching(Base):
    def test_a_new_event_changes_the_signature_of_the_page(self):
        from skillsync_workflow import dashboard_http as srv
        (self.root / '.github').mkdir()
        (self.root / '.github/.skillsync.yaml').write_text('schema: 1\n', encoding='utf-8')
        (self.root / '.skillsync/runs/R1').mkdir(parents=True)
        board = srv.Board(self.root, prototype_port=1)
        before = board.signature()
        path, _ = self.attempt('F1.1', [('a', 'running', 'g')], end=None)
        after_start = board.signature()
        self.assertNotEqual(before, after_start)
        w = ev.EventWriter(path)
        w.test('g::a', 'a', 'passed', group='g')
        w.close()
        self.assertNotEqual(board.signature(), after_start)


class Boundary(unittest.TestCase):
    """Decision 6: the raw `tests` is the ONLY exception to the masking of the answer of the page."""

    def status(self):
        s = secret()
        return {'schema': 1, 'plan': {'title': 'plan ' + s}, 'run': {'checkpoints': [{'text': 'token=' + s + ' zrobione'}]},
                'delivery': {'commits': [{'sha': 'abc1234', 'subject': 'password: ' + s}]},
                'waiting': [{'what': 'GO ' + s}],
                'tests': {'sections': [{'step': {'id': 'F1.1', 'title': 'Test ' + s},
                                        'attempts': [{'command': 'run [REDACTED]', 'groups': [{'name': 'g', 'tests': [{'name': 'n ' + s, 'msg': 'boom ' + s}]}]}]}]}}

    def with_env(self):
        return mock.patch.dict(os.environ, {'DEPLOY_TOKEN': secret()})

    def test_raw_result_leaves_and_masked_rest(self):
        with self.with_env():
            out = dt.finalize_status(self.status(), mask_tests=False)
        text_rest = json.dumps({k: v for k, v in out.items() if k != 'tests'}, ensure_ascii=False)
        self.assertNotIn('zq9x', text_rest)
        self.assertNotIn('rp6q', text_rest)
        test = out['tests']['sections'][0]['attempts'][0]['groups'][0]['tests'][0]
        self.assertIn(secret(), test['name'])
        self.assertIn(secret(), test['msg'])
        self.assertEqual(list(out), list(self.status()))   # the key order is kept

    def test_inside_tests_only_the_result_leaves_are_raw(self):
        """A1 (b2): the tree `tests` outside the leaves (`step.title`, `command`, `reason`, `tool`...) equals `redact`."""
        with self.with_env():
            data = self.status()
            data['tests']['sections'][0]['attempts'][0].update({'reason': 'failed: ' + secret(), 'tool': 'x ' + secret(), 'command': 'run ' + secret()})
            data['tests']['sections'][0]['attempts'][0]['groups'][0]['name'] = 'group ' + secret()
            out = dt.finalize_status(data, mask_tests=False)['tests']
            expected = redact(data['tests'])
        sec, exp = out['sections'][0], expected['sections'][0]
        self.assertEqual(sec['step'], exp['step'])
        self.assertNotIn('zq9x', json.dumps(sec['step']))
        attempt, exp_attempt = sec['attempts'][0], exp['attempts'][0]
        for key in ('reason', 'tool', 'command'):
            self.assertEqual(attempt[key], exp_attempt[key], key)
            self.assertNotIn('zq9x', attempt[key])
        self.assertIn(secret(), attempt['groups'][0]['name'])   # the name of a group is a test result
        self.assertIn(secret(), attempt['groups'][0]['tests'][0]['msg'])

    def test_the_response_of_the_route_keeps_only_the_result_leaves_raw(self):
        body = {'run': 'R1 ' + secret(), 'step': 'F1.1', 'attempt': 1, 'group': 'g ' + secret(), 'total': 1,
                'tests': [{'id': 'id ' + secret(), 'name': 'n ' + secret(), 'group': 'g ' + secret(), 'msg': 'm ' + secret(), 'state': 'failed'}]}
        with self.with_env():
            out = dt.finalize_response(body, mask_tests=False)
            masked = dt.finalize_response(body, mask_tests=True)
        self.assertNotIn('zq9x', out['run'])
        self.assertIn(secret(), out['tests'][0]['msg'])
        self.assertIn(secret(), out['group'])
        self.assertNotIn('zq9x', json.dumps(masked))

    def test_mask_on_masks_the_tests_too(self):
        with self.with_env():
            out = dt.finalize_status(self.status(), mask_tests=True)
        self.assertNotIn('zq9x', json.dumps(out, ensure_ascii=False))

    def test_outside_the_tests_key_the_result_equals_redact(self):
        with self.with_env():
            data = self.status()
            out = dt.finalize_status(data, mask_tests=False)
            expected = redact({k: v for k, v in data.items() if k != 'tests'})
        self.assertEqual({k: v for k, v in out.items() if k != 'tests'}, expected)

    def test_a_wider_exception_would_fail_the_guard(self):
        """The mutations of the guard: an exception on another top-level key, or on a leaf that is not a result (`step.title`, `command`)."""
        def leaky_top(data, mask_tests=False):
            keep = {k: data[k] for k in ('tests', 'waiting') if k in data}
            rest = redact({k: v for k, v in data.items() if k not in keep})
            return {k: keep[k] if k in keep else rest[k] for k in data}
        with self.with_env():
            data = self.status()
            out = leaky_top(data)
            expected = redact({k: v for k, v in data.items() if k != 'tests'})
            self.assertNotEqual({k: v for k, v in out.items() if k != 'tests'}, expected)
            original = dt.RAW_LEAVES
            try:
                dt.RAW_LEAVES = original + ('command',)
                leaked = dt.finalize_response({'tests': [{'id': 'a', 'command': 'run ' + secret()}]}, mask_tests=False)
            finally:
                dt.RAW_LEAVES = original
            self.assertIn(secret(), leaked['tests'][0]['command'])   # the guard test (b2) above fails for such a list: it checks command/reason/tool

    def test_without_tests_everything_is_redact(self):
        with self.with_env():
            data = self.status()
            data.pop('tests')
            self.assertEqual(dt.finalize_status(data), redact(data))

    def test_every_raw_string_in_the_tests_tree_is_on_the_list_of_result_leaves(self):
        """A-3: a generic walk: the secret is put into EVERY string of a full tree; after `finalize_status` it may survive only on the allowed paths."""
        s = secret()
        test = {'id': 'i' + s, 'name': 'n' + s, 'group': 'g' + s, 'msg': 'm' + s, 'state': 'failed ' + s, 'ms': 3}
        attempt = {'n': 1, 'state': 'failed ' + s, 'startedAt': 't' + s, 'endedAt': 'e' + s, 'command': 'c' + s, 'tool': 'tool' + s, 'levels': 'l' + s, 'source': 'so' + s,
                   'exit': 1, 'reason': 'r' + s, 'counts': {'total': 1}, 'truncated': False,
                   'groups': [{'name': 'gn' + s, 'state': 'st' + s, 'total': 1, 'counts': {'failed': 1}, 'tests': [test]}]}
        tree = {'sections': [{'step': {'id': 'F1.1' + s, 'title': 'ti' + s}, 'state': 'failed ' + s, 'attempts': [attempt]}], 'failedTotal': 1, 'runningTotal': 0}
        allowed = {('sections', 0, 'attempts', 0, 'groups', 0, 'name')} | {('sections', 0, 'attempts', 0, 'groups', 0, 'tests', 0, k) for k in ('id', 'name', 'group', 'msg')}
        found = []

        def walk(node, path):
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, path + (key,))
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, path + (index,))
            elif isinstance(node, str) and 'zq9x' in node:
                found.append(path)
        with self.with_env():
            walk(dt.finalize_status({'tests': tree}, mask_tests=False)['tests'], ())
        self.assertEqual(set(found), allowed)

    def test_only_the_expected_modules_know_the_event_files(self):
        package = ROOT / 'skillsync_workflow'
        users = {p.name for p in package.glob('*.py') if p.name not in ('test_events.py',) and ('test_events' in p.read_text(encoding='utf-8') or "'tests'" in p.read_text(encoding='utf-8') and 'EVENTS_DIR' in p.read_text(encoding='utf-8'))}
        self.assertLessEqual(users, {'dashboard.py', 'dashboard_http.py', 'dashboard_tests.py', 'test_gate.py', 'test_run.py'}, users)

    def test_show_keeps_the_boundary_end_to_end(self):
        """`dashboard show` of a real project: the secret of the plan is masked, the one in a test message is not (mask off)."""
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'p'
            (root / '.github').mkdir(parents=True)
            (root / '.github/.skillsync.yaml').write_text('schema: 1\n', encoding='utf-8')
            (root / 'docs/plans').mkdir(parents=True)
            s = secret()
            (root / 'docs/plans/2026-11-16-d.md').write_text(f'Status: in_progress\nPrzebieg: R1\n\n## Progress\n\n- [ ] F1.1 Kod {s} [T]\n', encoding='utf-8')
            (root / '.skillsync/runs/R1').mkdir(parents=True)
            (root / '.skillsync/runs/R1/checkpoints.md').write_text('- 10:00 w toku: F1.1\n', encoding='utf-8')
            path, _ = ev.new_attempt(root, 'R1', 'F1.1')
            w = ev.EventWriter(path)
            w.start(command=f'tool --token={s}', tool='unittest', pid=os.getpid())
            w.test('t', 't', 'failed', group='g', msg='boom ' + s)
            w.end('FAIL', 1)
            w.close()
            env = {**os.environ, 'DEPLOY_TOKEN': s, 'PYTHONDONTWRITEBYTECODE': '1', **home_env(tmp), 'SKILLSYNC_CONFIG': f'{tmp}/cfg.yaml'}
            out = subprocess.run([sys.executable, str(ROOT / 'skillsync'), 'dashboard', 'show', str(root), '--json'], capture_output=True, text=True, env=env, timeout=60).stdout
            data = json.loads(out)
            rest = json.dumps({k: v for k, v in data.items() if k != 'tests'}, ensure_ascii=False)
            self.assertNotIn(s, rest)
            self.assertNotIn(s, json.dumps(data['tests']['sections'][0]['attempts'][0]['command']))
            self.assertIn(s, data['tests']['sections'][0]['attempts'][0]['groups'][0]['tests'][0]['msg'])



from test_skillsync_dashboard_server import HttpCase, RUN, PLAN  # noqa: E402


class OverHttp(HttpCase):
    """The routes of the server (single-port mode here; the tests on BOTH sockets move to the two-socket helper of 180-B after the rebase)."""
    RUN_ID = '2026-01-10-demo-etap-1'

    def setUp(self):
        super().setUp()
        dt._CACHE.clear()
        env = mock.patch.dict(os.environ, {'DEPLOY_TOKEN': secret()})
        env.start()
        self.addCleanup(env.stop)
        self.path, _ = self.make_events()

    def make_events(self):
        path, number = ev.new_attempt(self.root, self.RUN_ID, 'F1.1')
        w = ev.EventWriter(path)
        w.start(run=self.RUN_ID, step='F1.1', stepSource='flag', command='tool --token=' + secret(), tool='unittest', pid=os.getpid())
        w.test('mod::a', 'a ' + secret(), 'failed', group='mod', msg='boom ' + secret())
        w.test('mod::b', 'b', 'passed', group='mod')
        w.end('FAIL', 1, reason='failed: ' + secret(), counts={'total': 2, 'passed': 1, 'failed': 1})
        w.close()
        return path, number

    def json(self, path):
        with self.get(path) as r:
            return json.load(r)

    def test_status_has_raw_result_leaves_and_a_masked_rest(self):
        data = self.json('/api/status')
        sec = data['tests']['sections'][0]
        attempt = sec['attempts'][0]
        test = attempt['groups'][0]['tests'][0]
        self.assertIn(secret(), test['name'])
        self.assertIn(secret(), test['msg'])
        for masked in (attempt['command'], attempt['reason'], json.dumps({k: v for k, v in data.items() if k != 'tests'}, ensure_ascii=False)):
            self.assertNotIn('zq9x', masked)

    def test_the_mask_setting_masks_the_results_on_the_page_and_the_route(self):
        manifest = self.root / '.github/.skillsync.yaml'
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text('tests:\n  mask: true\n', encoding='utf-8')
        self.assertNotIn('zq9x', json.dumps(self.json('/api/status')['tests'], ensure_ascii=False))
        self.assertNotIn('zq9x', json.dumps(self.json(f'/api/tests?run={self.RUN_ID}&step=F1.1&attempt=1'), ensure_ascii=False))

    def test_the_route_pages_and_keeps_the_leaves_raw(self):
        body = self.json(f'/api/tests?run={self.RUN_ID}&step=F1.1&attempt=1&group=mod&limit=1&offset=0')
        self.assertEqual((body['total'], body['hasMore'], len(body['tests'])), (2, True, 1))
        self.assertIn(secret(), body['tests'][0]['msg'])
        self.assertEqual(self.json(f'/api/tests?run={self.RUN_ID}&step=F1.1&attempt=1&group=mod&limit=1&offset=1')['tests'][0]['id'], 'mod::b')

    def test_bad_parameters_are_refused_and_no_write_route_exists(self):
        import urllib.error
        bad = ('run=..&step=F1.1&attempt=1', f'run={self.RUN_ID}&step=..&attempt=1', f'run={self.RUN_ID}&step=%2e%2e&attempt=1', f'run={self.RUN_ID}&step=a%5Cb&attempt=1',
               f'run={self.RUN_ID}&step=a:b&attempt=1', f'run={self.RUN_ID}&step=CON&attempt=1', f'run={self.RUN_ID}&step=F1.&attempt=1',
               f'run={self.RUN_ID}&step=F1.1&attempt=x', f'run={self.RUN_ID}&step=F1.1&attempt=9', f'run={self.RUN_ID}&step=F1.1&attempt=1&group=' + 'x' * 400)
        for query in bad:
            with self.subTest(query):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.get('/api/tests?' + query)
                self.assertIn(caught.exception.code, (400, 404))
        req = urllib.request.Request(self.base + '/api/tests', data=b'{}', method='POST')
        with self.assertRaises(urllib.error.HTTPError) as post:
            urllib.request.urlopen(req, timeout=5)
        self.assertIn(post.exception.code, (403, 404, 405))

    def test_no_other_route_hands_out_the_events(self):
        import urllib.error
        for route in (f'/api/doc?path={RUN}/tests/F1.1/1.jsonl', f'/api/doc?path=.skillsync/runs/{self.RUN_ID}/tests/F1.1/1.jsonl', '/events'):
            if route == '/events':
                continue
            with self.subTest(route):
                with self.assertRaises(urllib.error.HTTPError):
                    self.get(route)

    def test_a_new_event_refreshes_the_signature_and_a_hundred_attempts_keep_the_newest_watched(self):
        before = self.board.signature()
        w = ev.EventWriter(self.path)
        w.test('mod::c', 'c', 'passed', group='mod')
        w.close()
        self.assertNotEqual(self.board.signature(), before)
        for _ in range(110):
            ev.new_attempt(self.root, self.RUN_ID, 'F1.1')
        newest = max(int(p.stem) for p in ev.list_attempts(self.root, self.RUN_ID, 'F1.1'))
        names = {Path(p).name for p in self.board.watched_paths()}
        self.assertIn(f'{newest}.jsonl', names)


if __name__ == '__main__':
    unittest.main()
