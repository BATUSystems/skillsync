"""Kontrakt JSON dashboardu (`schema: 1`): struktura `expected.json` w tests/fixtures/dashboard, dokument kontraktu, neutralność wejść."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures' / 'dashboard'
CONTRACT_DOC = ROOT / 'docs' / 'dashboard-contract.md'
CASES = ('empty', 'plan-in-progress', 'spec-act', 'run-mode', 'silence', 'readiness-blocked', 'questions', 'cp-ready', 'timing', 'eta')
GENERATED_BY_LINK = {'.mcp.json', '.claude', '.github', '.gitignore', 'AGENTS.md', 'CLAUDE.md'}
FORBIDDEN_IN_INPUT = ('/home/', '@')  # ogólne wzorce; nazwy prywatne pilnuje leakcheck (strażnik), nie źródło testu
MODES = {'plan', 'spec', 'run', 'none'}
STATUSES = {'draft', 'approved', 'in_progress', 'done', 'implemented', 'archived', None}
STATE_ACT = {'planned', 'active', 'done'}
STEP_STATE = {'done', 'active', 'reported', 'planned'}
APPROVAL_STATE = {'waiting', 'running', 'after'}
APPROVAL_KIND = {'spec', 'plan', 'cp', 'ready'}
APPROVAL_WORD = {'GO plan', 'GO implement', 'CP', 'READY'}
DELIVERY_MODES = {'pr', 'direct', 'invalid'}

NUM = (int, float)
STR_OR_NONE = (str, type(None))
# klucz -> (dozwolone typy, czy wymagany). Wszystkie wymienione klucze są wymagane w oczekiwanym JSON.
TOP = {
    'schema': int, 'lang': str, 'app': dict, 'project': dict, 'generatedAt': str, 'version': int, 'lastUpdate': STR_OR_NONE,
    'git': dict, 'plans': list, 'planAuto': bool, 'mode': str, 'plan': (dict, type(None)), 'spec': dict, 'run': dict,
    'progress': dict, 'acts': list, 'result': (dict, type(None)), 'waiting': list, 'silence': (dict, type(None)), 'approvals': dict, 'delivery': dict,
    'session': dict, 'prototype': dict, 'docs': list, 'readiness': dict, 'assumptions': dict, 'blockingQuestions': dict,
    'expertQuestions': dict, 'prototypes': list, 'substeps': list, 'timing': dict, 'forecast': dict, 'feedback': dict, 'eta': (dict, type(None)),
}
ETA = ({'operation': str, 'until': str, 'untilIso': STR_OR_NONE, 'since': str}, {})
# Pola obiektów zagnieżdżonych: wymagane (typ) i opcjonalne (opcjonalne tylko gdy opisane w kontrakcie jako opcjonalne).
NESTED = {
    'app': ({'name': str, 'version': str}, {}),
    'project': ({'name': str, 'path': str, 'worktree': (dict, type(None))}, {}),
    'git': ({'branch': STR_OR_NONE, 'head': STR_OR_NONE, 'headFull': STR_OR_NONE, 'remote': (dict, type(None)),
             'branchUrl': STR_OR_NONE, 'branchPushed': (bool, type(None))}, {}),
    'run': ({'id': STR_OR_NONE, 'exists': bool, 'start': STR_OR_NONE, 'startIso': STR_OR_NONE, 'checkpoints': list,
             'files': list}, {'lastReportAt': str, 'inProgressFrom': str, 'parseWarnings': list}),
    'spec': ({'path': STR_OR_NONE, 'exists': bool, 'preparing': bool, 'sha': STR_OR_NONE, 'prototype': STR_OR_NONE},
             {'title': STR_OR_NONE, 'status': STR_OR_NONE, 'statusText': STR_OR_NONE, 'mtime': STR_OR_NONE,
              'fileSha': STR_OR_NONE, 'prototypeAcceptance': (dict, type(None))}),
    'progress': ({'total': int, 'done': int, 'percent': int, 'pending': int, 'pendingPercent': int, 'phases': list,
                  'active': list, 'activeSource': STR_OR_NONE, 'current': (dict, type(None)), 'parallel': int,
                  'reported': list}, {'activePercent': int}),
    'approvals': ({'enabled': bool, 'actions': list, 'last': (dict, type(None))}, {}),
    'delivery': ({'mode': str, 'base': STR_OR_NONE, 'source': str, 'branch': STR_OR_NONE, 'target': STR_OR_NONE,
                  'remote': STR_OR_NONE, 'pushed': (bool, type(None)), 'ahead': (int, type(None)), 'commits': (list, type(None)), 'commitsMore': int,
                  'words': list}, {}),
    'session': ({'url': STR_OR_NONE}, {}),
    'prototype': ({'slug': STR_OR_NONE, 'port': int, 'up': bool, 'writing': (dict, type(None))}, {'commentsAfterAcceptance': dict}),
    'readiness': ({'status': STR_OR_NONE, 'date': STR_OR_NONE, 'planSha': STR_OR_NONE, 'source': STR_OR_NONE}, {}),
    'assumptions': ({'count': int, 'items': list}, {}),
    'blockingQuestions': ({'count': int, 'items': list}, {}),
    'expertQuestions': ({'open': int, 'blocking': int, 'runs': list, 'current': (dict, type(None))}, {'items': list}),
}
WORKTREE = ({'kind': str, 'name': str, 'path': str, 'mainPath': STR_OR_NONE}, {})
WORKTREE_KINDS = {'main', 'worktree'}
PLAN = ({'path': str, 'title': str, 'status': STR_OR_NONE, 'statusText': STR_OR_NONE, 'date': STR_OR_NONE, 'mtime': str,
         'fileSha': STR_OR_NONE, 'pr': (dict, type(None))}, {})
PLAN_ROW = ({'path': str, 'title': str, 'status': STR_OR_NONE, 'spec': STR_OR_NONE, 'run': STR_OR_NONE}, {})
ACT = ({'key': str, 'roman': str, 'name': str, 'state': str, 'status': STR_OR_NONE, 'detail': STR_OR_NONE,
        'stages': list}, {'warning': STR_OR_NONE, 'percent': int, 'linkWarning': STR_OR_NONE})
STAGE = ({'label': str, 'done': bool, 'tip': str, 'key': STR_OR_NONE}, {'state': str, 'since': str})
PHASE = ({'name': str, 'label': str, 'short': str, 'total': int, 'done': int, 'percent': int, 'pending': int,
          'pendingPercent': int, 'state': str, 'steps': list}, {})
STEP = ({'id': str, 'title': str, 'done': bool, 'note': str, 'sha': STR_OR_NONE, 'index': int, 'state': str}, {})
WAITING = ({'what': str, 'source': str, 'since': STR_OR_NONE, 'action': str, 'link': (dict, type(None))},
           {'word': str, 'approve': STR_OR_NONE, 'who': dict})
ACTION = ({'kind': str, 'word': str, 'state': str, 'reason': STR_OR_NONE, 'blocked': STR_OR_NONE, 'path': STR_OR_NONE,
           'sha': str, 'given': (dict, type(None)), 'run': STR_OR_NONE}, {'target': STR_OR_NONE, 'planState': STR_OR_NONE})
DOC_GROUP = ({'key': str, 'label': str, 'items': list}, {})
DOC_ITEM = ({'path': str, 'title': str, 'status': STR_OR_NONE, 'mtime': str}, {})
TIMING = ({'available': bool, 'reason': STR_OR_NONE, 'windows': list, 'steps': list, 'stages': list, 'acts': list, 'totals': dict}, {})
TIMING_ACT = ({'key': str, 'startedAt': str, 'endedAt': STR_OR_NONE, 'workSeconds': (int, type(None)), 'waitSeconds': (int, type(None)),
               'unknown': bool, 'reason': STR_OR_NONE, 'endSource': STR_OR_NONE}, {})
FORECAST = ({'scope': str}, {'reason': str, 'have': int, 'need': int, 'remaining': int, 'samples': int, 'basis': str, 'paused': bool,
            'lowSeconds': int, 'medianSeconds': int, 'highSeconds': int})
TIMING_WINDOW = ({'what': str, 'startedAt': str, 'endedAt': STR_OR_NONE, 'seconds': (int, type(None)), 'source': STR_OR_NONE,
                  'unknown': bool}, {})
TIMING_STEP = ({'id': str, 'phase': str, 'startedAt': STR_OR_NONE, 'finishedAt': STR_OR_NONE, 'workSeconds': (int, type(None)),
                'waitSeconds': (int, type(None)), 'unknown': bool, 'reason': STR_OR_NONE}, {})
TIMING_STAGE = ({'phase': str, 'name': str, 'workSeconds': (int, type(None)), 'waitSeconds': (int, type(None)),
                 'unknownSteps': int, 'complete': bool}, {})
TIMING_TOTALS = ({'workSeconds': (int, type(None)), 'waitSeconds': (int, type(None)), 'windows': int, 'unknownWindows': int,
                  'unknownSteps': int, 'complete': bool}, {})
SUBSTEP = ({'id': str, 'title': str, 'phase': str, 'state': str, 'done': bool, 'startedAt': STR_OR_NONE,
            'finishedAt': STR_OR_NONE, 'seconds': (int, type(None)), 'missingData': bool, 'noData': bool}, {})
CHECKPOINT = ({'time': str, 'text': str}, {})
DELIVERY_WORD = ({'word': str, 'active': bool, 'tip': str, 'target': STR_OR_NONE}, {})
ASSUMPTION = ({'id': str, 'question': str, 'accepted': str}, {})
BLOCKING = ({'id': str, 'question': str}, {})
EXPERT_CURRENT = ({'spec': str, 'prototypes': list, 'open': int, 'blocking': int, 'items': list}, {})
EXPERT_ITEM = ({'id': str, 'runSlug': str, 'text': str, 'blocking': bool, 'addressees': list, 'state': str, 'at': str, 'url': str}, {})
CURRENT_PROTOTYPE = ({'slug': str, 'iteration': (int, type(None)), 'screens': int, 'accepted': int, 'stale': int, 'threads': dict,
                      'commentsAfterAcceptance': dict, 'writing': (dict, type(None))}, {})
EXPERT_RUN = ({'slug': str, 'open': int, 'blocking': int, 'url': str}, {'addressees': (str, list)})


def load(case):
    path = FIXTURES / case / 'expected.json'
    return path, path.read_text(encoding='utf-8')


class DashboardContract(unittest.TestCase):
    def check_shape(self, value, shape, where):
        required, optional = shape
        self.assertIsInstance(value, dict, where)
        for key, types in required.items():
            self.assertIn(key, value, f'{where}: brak klucza {key}')
            self.assertIsInstance(value[key], types, f'{where}.{key}: zły typ {type(value[key]).__name__}')
            if types is int or types == (int, type(None)):
                self.assertNotIsInstance(value[key], bool, f'{where}.{key}: bool zamiast liczby')
        for key, types in optional.items():
            if key in value:
                self.assertIsInstance(value[key], types, f'{where}.{key}: zły typ')
        extra = set(value) - set(required) - set(optional)
        self.assertFalse(extra, f'{where}: klucze spoza kontraktu {sorted(extra)}')

    def each(self, items, shape, where):
        for index, item in enumerate(items):
            self.check_shape(item, shape, f'{where}[{index}]')

    def test_cases_exist_with_input_and_expected(self):
        found = sorted(path.name for path in FIXTURES.iterdir() if path.is_dir())
        self.assertEqual(found, sorted(CASES))
        for case in CASES:
            self.assertTrue((FIXTURES / case / 'expected.json').is_file(), case)
            inputs = [p for p in (FIXTURES / case / 'input').rglob('*') if p.is_file()]
            self.assertTrue(inputs, f'{case}: pusty input/')

    def test_top_level_matches_schema(self):
        for case in CASES:
            with self.subTest(case=case):
                _, text = load(case)
                data = json.loads(text)
                self.assertEqual(set(data), set(TOP), 'klucze najwyższego poziomu różnią się od kontraktu')
                for key, types in TOP.items():
                    self.assertIsInstance(data[key], types, key)
                self.assertEqual(data['schema'], 1)
                self.assertIn(data['mode'], MODES)

    def test_nested_objects_and_arrays(self):
        for case in CASES:
            with self.subTest(case=case):
                _, text = load(case)
                data = json.loads(text)
                for key, shape in NESTED.items():
                    self.check_shape(data[key], shape, key)
                worktree = data['project']['worktree']
                if worktree is not None:
                    self.check_shape(worktree, WORKTREE, 'project.worktree')
                    self.assertIn(worktree['kind'], WORKTREE_KINDS)
                    self.assertEqual((worktree['name'], worktree['path']), ('demo', '<root>'))
                    self.assertIsNone(worktree['mainPath'], 'fixtures: kind main has no mainPath')
                self.assertEqual(worktree is not None, case == 'cp-ready', f'{case}: project.worktree null only without a repository')
                self.each(data['plans'], PLAN_ROW, 'plans')
                if data['plan'] is not None:
                    self.check_shape(data['plan'], PLAN, 'plan')
                    self.assertIn(data['plan']['status'], STATUSES)
                self.each(data['run']['checkpoints'], CHECKPOINT, 'run.checkpoints')
                self.each(data['acts'], ACT, 'acts')
                self.assertEqual([a['key'] for a in data['acts']], ['spec', 'plan', 'real'])
                for act in data['acts']:
                    self.assertIn(act['state'], STATE_ACT)
                    self.each(act['stages'], STAGE, f"acts.{act['key']}.stages")
                self.each(data['progress']['phases'], PHASE, 'progress.phases')
                for phase in data['progress']['phases']:
                    self.assertIn(phase['state'], STATE_ACT)
                    self.each(phase['steps'], STEP, f"progress.phases.{phase['name']}.steps")
                    for step in phase['steps']:
                        self.assertIn(step['state'], STEP_STATE)
                self.each(data['waiting'], WAITING, 'waiting')
                self.each(data['approvals']['actions'], ACTION, 'approvals.actions')
                for action in data['approvals']['actions']:
                    self.assertIn(action['kind'], APPROVAL_KIND)
                    self.assertIn(action['word'], APPROVAL_WORD)
                    self.assertIn(action['state'], APPROVAL_STATE)
                self.assertIn(data['delivery']['mode'], DELIVERY_MODES)
                self.each(data['delivery']['words'], DELIVERY_WORD, 'delivery.words')
                self.each(data['docs'], DOC_GROUP, 'docs')
                for group in data['docs']:
                    self.each(group['items'], DOC_ITEM, f"docs.{group['key']}.items")
                self.each(data['substeps'], SUBSTEP, 'substeps')
                self.check_shape(data['timing'], TIMING, 'timing')
                self.each(data['timing']['windows'], TIMING_WINDOW, 'timing.windows')
                self.each(data['timing']['steps'], TIMING_STEP, 'timing.steps')
                self.each(data['timing']['stages'], TIMING_STAGE, 'timing.stages')
                self.each(data['timing']['acts'], TIMING_ACT, 'timing.acts')
                self.check_shape(data['forecast'], FORECAST, 'forecast')
                self.check_shape(data['timing']['totals'], TIMING_TOTALS, 'timing.totals')
                self.each(data['assumptions']['items'], ASSUMPTION, 'assumptions.items')
                self.each(data['blockingQuestions']['items'], BLOCKING, 'blockingQuestions.items')
                self.each(data['expertQuestions']['runs'], EXPERT_RUN, 'expertQuestions.runs')
                self.each(data['expertQuestions'].get('items', []), EXPERT_ITEM, 'expertQuestions.items')
                current = data['expertQuestions']['current']
                if current is not None:
                    self.check_shape(current, EXPERT_CURRENT, 'expertQuestions.current')
                    self.each(current['items'], EXPERT_ITEM, 'expertQuestions.current.items')
                    self.each(current['prototypes'], CURRENT_PROTOTYPE, 'expertQuestions.current.prototypes')
                if data['eta'] is not None:
                    self.check_shape(data['eta'], ETA, 'eta')

    def test_new_fields_are_consistent(self):
        for case in CASES:
            with self.subTest(case=case):
                _, text = load(case)
                data = json.loads(text)
                self.assertIn(data['readiness']['status'], {'PASS', 'BLOCKED', None})
                if data['readiness']['status'] is None:
                    self.assertIsNone(data['readiness']['source'])
                for key in ('assumptions', 'blockingQuestions'):
                    self.assertEqual(data[key]['count'], len(data[key]['items']), key)
                eq = data['expertQuestions']
                self.assertEqual((eq['open'], eq['blocking']), (sum(r['open'] for r in eq['runs']), sum(r['blocking'] for r in eq['runs'])))
                current = eq['current']
                if current is not None:
                    self.assertTrue(current['prototypes'] or current['items'], 'current is null when nothing is bound to the run')
                    self.assertEqual(current['spec'], data['spec']['path'])
                    self.assertTrue(data['spec']['exists'])
                    self.assertLessEqual(len(current['items']), 50)
                    self.assertEqual(current['open'], sum(1 for item in current['items'] if item['state'] == 'open'))
                    self.assertEqual(current['blocking'], sum(1 for item in current['items'] if item['state'] == 'open' and item['blocking']))
                    self.assertEqual([item['at'] for item in current['items']], sorted((item['at'] for item in current['items']), reverse=True))
                    known = {item['id']: item for item in eq.get('items', [])}
                    for item in current['items']:
                        self.assertEqual(known.get(item['id'], item), item, 'an item of the run is the project item')
                    self.assertNotIn('origin', json.dumps(current))
                for run in eq['runs']:
                    self.assertGreater(run['open'], 0, run['slug'])
                    self.assertLessEqual(run['blocking'], run['open'], run['slug'])
                    self.assertEqual(run['url'], '/_experts/' + run['slug'])
                for sub in data['substeps']:
                    self.assertRegex(sub['id'], r'^[A-Z]{1,3}-?\d+[a-z]?\.\d+[a-z]?$')
                    self.assertEqual(sub['noData'], sub['seconds'] is None, sub['id'])   # same meaning as `noData` in `metrics`
                    if sub['seconds'] is not None:
                        self.assertFalse(sub['missingData'], sub['id'])
                    self.assertTrue(not sub['missingData'] or sub['done'] or sub['state'] == 'reported', sub['id'])
                self.assertEqual(data['progress']['total'], sum(len(p['steps']) for p in data['progress']['phases']))

    def test_timing_is_consistent(self):
        for case in CASES:
            with self.subTest(case=case):
                timing = json.loads(load(case)[1])['timing']
                for window in timing['windows']:
                    self.assertEqual(window['unknown'], window['seconds'] is None, window)
                    self.assertEqual(window['source'] is None, window['unknown'], window)
                for item in timing['steps']:
                    self.assertEqual(item['unknown'], item['workSeconds'] is None, item)
                    self.assertEqual(item['reason'] is None, not item['unknown'], item)
                totals = timing['totals']
                self.assertEqual(totals['windows'], len(timing['windows']))
                self.assertEqual(totals['unknownWindows'], sum(1 for w in timing['windows'] if w['unknown']))
                self.assertEqual(totals['unknownSteps'], sum(1 for s in timing['steps'] if s['unknown']))
                self.assertEqual(totals['complete'], not totals['unknownWindows'] and not totals['unknownSteps'] and timing['available'])
                if not timing['available']:
                    self.assertTrue(timing['reason'])

    def test_the_contract_documents_plan_state_of_consents(self):
        text = CONTRACT_DOC.read_text(encoding='utf-8')
        for needle in ('`planState`', 'tracked: false', 'ostatni wpis'):
            self.assertIn(needle, text)
        flat = ' '.join(text.split())
        for pattern in (r'`planState` równa się\s+bieżącemu statusowi planu przebiegu',
                        r'zmiana statusu planu po zgodzie ponownie otwiera przycisk',
                        r'odsyła go w `POST /api/approve` jako `planState`, a serwer odpowiada `409`',
                        r'porównanie dotyczy OSTATNIEGO wpisu tego słowa w przebiegu',
                        r'brak pola w żądaniu = zachowanie bez tej kontroli'):
            self.assertRegex(flat, pattern)

    def test_cases_cover_the_contract(self):
        plan = json.loads(load('plan-in-progress')[1])
        self.assertEqual(plan['mode'], 'plan')
        self.assertEqual(plan['readiness']['status'], 'PASS')
        self.assertTrue(plan['assumptions']['items'] and plan['blockingQuestions']['items'])
        self.assertTrue(any(s['seconds'] is not None for s in plan['substeps']))
        self.assertTrue(any(s['missingData'] for s in plan['substeps']))
        self.assertTrue(any(a['state'] == 'after' for a in plan['approvals']['actions']))
        self.assertEqual(json.loads(load('empty')[1])['mode'], 'none')
        spec = json.loads(load('spec-act')[1])
        self.assertEqual(spec['mode'], 'spec')
        self.assertEqual([a['state'] for a in spec['approvals']['actions']], ['waiting'])
        self.assertEqual(json.loads(load('run-mode')[1])['mode'], 'run')
        self.assertIsNotNone(json.loads(load('silence')[1])['silence'])
        self.assertEqual(json.loads(load('readiness-blocked')[1])['readiness']['status'], 'BLOCKED')
        questions = json.loads(load('questions')[1])
        self.assertTrue(questions['assumptions']['items'] and questions['blockingQuestions']['items'])
        self.assertFalse(any('pytania' in w['action'] for w in questions['waiting']))
        self.assertEqual([(r['slug'], r['open'], r['blocking']) for r in questions['expertQuestions']['runs']],
                         [('run-alpha', 2, 1), ('run-beta', 1, 1)])  # dwa przebiegi, zamknięty run-gamma pominięty
        self.assertEqual({k: v for k, v in json.loads(load('empty')[1])['expertQuestions'].items()},
                         {'open': 0, 'blocking': 0, 'runs': [], 'items': [], 'current': None})
        current = questions['expertQuestions']['current']   # K8: the spec names run-alpha; run-delta is bound by `requirements`
        self.assertEqual((current['spec'], [row['slug'] for row in current['prototypes']], current['open'], current['blocking']),
                         (questions['spec']['path'], ['run-alpha', 'run-delta'], 2, 2))
        self.assertEqual([(item['id'], item['runSlug'], item['state']) for item in current['items']],
                         [('qn-b00001', 'run-beta', 'open'), ('qn-a00001', 'run-alpha', 'open')])   # not the foreign qn-a00002/3
        for case in CASES:
            if case != 'questions':
                self.assertIsNone(json.loads(load(case)[1])['expertQuestions']['current'], case)
        cp_ready = json.loads(load('cp-ready')[1])
        self.assertEqual([(a['kind'], a['state']) for a in cp_ready['approvals']['actions'][2:]],
                         [('cp', 'waiting'), ('ready', 'waiting')])
        self.assertEqual(cp_ready['git']['headFull'], '<head>')
        self.assertEqual(cp_ready['project']['worktree'], {'kind': 'main', 'mainPath': None, 'name': 'demo', 'path': '<root>'})
        after = json.loads((FIXTURES / 'cp-ready' / 'expected-after.json').read_text(encoding='utf-8'))
        cp = next(a for a in after['approvals']['actions'] if a['kind'] == 'cp')
        self.assertEqual((cp['state'], cp['given']['word'], cp['given']['sha'], after['approvals']['last']),
                         ('after', 'CP', '<head>', cp['given']))
        self.assertIsNotNone(cp['given']['at'])
        self.each(after['approvals']['actions'], ACTION, 'cp-ready after: approvals.actions')
        self.assertEqual(after['project']['worktree'], cp_ready['project']['worktree'])

    def test_top_level_keys_documented(self):
        doc = CONTRACT_DOC.read_text(encoding='utf-8')
        for key in TOP:
            self.assertIn(f'`{key}`', doc, f'docs/dashboard-contract.md nie opisuje klucza {key}')
        for word in ('PASS', 'BLOCKED', 'approvals record', '--word', '--source', 'addytywnie'):
            self.assertIn(word, doc)

    def test_expected_is_normalized_and_stable(self):
        for case in CASES:
            with self.subTest(case=case):
                path, text = load(case)
                data = json.loads(text)
                self.assertEqual(text, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
                                 f'{path.name}: JSON nie jest posortowany/ustabilizowany')
                self.assertEqual(data['project']['path'], '<root>')
                self.assertEqual(data['project']['name'], 'demo')
                self.assertEqual(data['generatedAt'], '2026-01-12T12:00:00+00:00')
                self.assertEqual(data['app']['version'], '<version>')
                for forbidden in FORBIDDEN_IN_INPUT:
                    self.assertNotIn(forbidden, text.lower(), f'{case}: expected.json zawiera {forbidden!r}')

    def test_input_is_neutral_text(self):
        for case in CASES:
            for path in sorted((FIXTURES / case / 'input').rglob('*')):
                if not path.is_file():
                    continue
                if GENERATED_BY_LINK.intersection(path.relative_to(FIXTURES / case / 'input').parts):
                    continue  # pliki generowane przez `skillsync link`/usługę watch, nie część fixture
                with self.subTest(file=str(path.relative_to(FIXTURES))):
                    text = path.read_text(encoding='utf-8')
                    for forbidden in FORBIDDEN_IN_INPUT:
                        self.assertNotIn(forbidden, text.lower(), f'{path.name} zawiera {forbidden!r}')
                    self.assertIsNone(re.search(r'[A-Za-z]:\\\\|~/', text), f'{path.name}: ścieżka domowa')


if __name__ == '__main__':
    unittest.main()
