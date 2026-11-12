"""Kontrakt JSON dashboardu (`schema: 1`): struktura `expected.json` w tests/fixtures/dashboard, dokument kontraktu, neutralność wejść."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures' / 'dashboard'
CONTRACT_DOC = ROOT / 'docs' / 'dashboard-contract.md'
CASES = ('empty', 'plan-in-progress', 'spec-act', 'run-mode', 'silence', 'readiness-blocked', 'questions', 'cp-ready', 'timing')
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
    'schema': int, 'app': dict, 'project': dict, 'generatedAt': str, 'version': int, 'lastUpdate': STR_OR_NONE,
    'git': dict, 'plans': list, 'planAuto': bool, 'mode': str, 'plan': (dict, type(None)), 'spec': dict, 'run': dict,
    'progress': dict, 'acts': list, 'waiting': list, 'silence': (dict, type(None)), 'approvals': dict, 'delivery': dict,
    'session': dict, 'prototype': dict, 'docs': list, 'readiness': dict, 'assumptions': dict, 'blockingQuestions': dict,
    'substeps': list, 'timing': dict,
}
# Pola obiektów zagnieżdżonych: wymagane (typ) i opcjonalne (opcjonalne tylko gdy opisane w kontrakcie jako opcjonalne).
NESTED = {
    'app': ({'name': str, 'version': str}, {}),
    'project': ({'name': str, 'path': str}, {}),
    'git': ({'branch': STR_OR_NONE, 'head': STR_OR_NONE, 'headFull': STR_OR_NONE, 'remote': (dict, type(None)),
             'branchUrl': STR_OR_NONE, 'branchPushed': (bool, type(None))}, {}),
    'run': ({'id': STR_OR_NONE, 'exists': bool, 'start': STR_OR_NONE, 'startIso': STR_OR_NONE, 'checkpoints': list,
             'files': list}, {'lastReportAt': str, 'inProgressFrom': str}),
    'spec': ({'path': STR_OR_NONE, 'exists': bool, 'preparing': bool, 'sha': STR_OR_NONE, 'prototype': STR_OR_NONE},
             {'title': STR_OR_NONE, 'status': STR_OR_NONE, 'statusText': STR_OR_NONE, 'mtime': STR_OR_NONE,
              'fileSha': STR_OR_NONE, 'prototypeAcceptance': (dict, type(None))}),
    'progress': ({'total': int, 'done': int, 'percent': int, 'pending': int, 'pendingPercent': int, 'phases': list,
                  'active': list, 'activeSource': STR_OR_NONE, 'current': (dict, type(None)), 'parallel': int,
                  'reported': list}, {}),
    'approvals': ({'enabled': bool, 'actions': list, 'last': (dict, type(None))}, {}),
    'delivery': ({'mode': str, 'base': STR_OR_NONE, 'source': str, 'branch': STR_OR_NONE, 'target': STR_OR_NONE,
                  'remote': STR_OR_NONE, 'pushed': (bool, type(None)), 'ahead': (int, type(None)), 'words': list}, {}),
    'session': ({'url': STR_OR_NONE}, {}),
    'prototype': ({'slug': STR_OR_NONE, 'port': int, 'up': bool}, {}),
    'readiness': ({'status': STR_OR_NONE, 'date': STR_OR_NONE, 'planSha': STR_OR_NONE, 'source': STR_OR_NONE}, {}),
    'assumptions': ({'count': int, 'items': list}, {}),
    'blockingQuestions': ({'count': int, 'items': list}, {}),
}
PLAN = ({'path': str, 'title': str, 'status': STR_OR_NONE, 'statusText': STR_OR_NONE, 'date': STR_OR_NONE, 'mtime': str,
         'fileSha': STR_OR_NONE, 'pr': (dict, type(None))}, {})
PLAN_ROW = ({'path': str, 'title': str, 'status': STR_OR_NONE, 'spec': STR_OR_NONE, 'run': STR_OR_NONE}, {})
ACT = ({'key': str, 'roman': str, 'name': str, 'state': str, 'status': STR_OR_NONE, 'detail': STR_OR_NONE,
        'stages': list}, {'warning': STR_OR_NONE, 'percent': int})
STAGE = ({'label': str, 'done': bool, 'tip': str, 'key': STR_OR_NONE}, {})
PHASE = ({'name': str, 'label': str, 'short': str, 'total': int, 'done': int, 'percent': int, 'pending': int,
          'pendingPercent': int, 'state': str, 'steps': list}, {})
STEP = ({'id': str, 'title': str, 'done': bool, 'note': str, 'sha': STR_OR_NONE, 'index': int, 'state': str}, {})
WAITING = ({'what': str, 'source': str, 'since': STR_OR_NONE, 'action': str, 'link': (dict, type(None))},
           {'word': str, 'approve': STR_OR_NONE})
ACTION = ({'kind': str, 'word': str, 'state': str, 'reason': STR_OR_NONE, 'blocked': STR_OR_NONE, 'path': STR_OR_NONE,
           'sha': str, 'given': (dict, type(None)), 'run': STR_OR_NONE}, {'target': STR_OR_NONE})
DOC_GROUP = ({'key': str, 'label': str, 'items': list}, {})
DOC_ITEM = ({'path': str, 'title': str, 'status': STR_OR_NONE, 'mtime': str}, {})
TIMING = ({'available': bool, 'reason': STR_OR_NONE, 'windows': list, 'steps': list, 'stages': list, 'totals': dict}, {})
TIMING_WINDOW = ({'what': str, 'startedAt': str, 'endedAt': STR_OR_NONE, 'seconds': (int, type(None)), 'source': STR_OR_NONE,
                  'unknown': bool}, {})
TIMING_STEP = ({'id': str, 'phase': str, 'startedAt': STR_OR_NONE, 'finishedAt': STR_OR_NONE, 'workSeconds': (int, type(None)),
                'waitSeconds': (int, type(None)), 'unknown': bool, 'reason': STR_OR_NONE}, {})
TIMING_STAGE = ({'phase': str, 'name': str, 'workSeconds': (int, type(None)), 'waitSeconds': (int, type(None)),
                 'unknownSteps': int, 'complete': bool}, {})
TIMING_TOTALS = ({'workSeconds': (int, type(None)), 'waitSeconds': (int, type(None)), 'windows': int, 'unknownWindows': int,
                  'unknownSteps': int, 'complete': bool}, {})
SUBSTEP = ({'id': str, 'title': str, 'phase': str, 'state': str, 'done': bool, 'startedAt': STR_OR_NONE,
            'finishedAt': STR_OR_NONE, 'seconds': (int, type(None)), 'missingData': bool}, {})
CHECKPOINT = ({'time': str, 'text': str}, {})
DELIVERY_WORD = ({'word': str, 'active': bool, 'tip': str, 'target': STR_OR_NONE}, {})
ASSUMPTION = ({'id': str, 'question': str, 'accepted': str}, {})
BLOCKING = ({'id': str, 'question': str}, {})


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
                self.check_shape(data['timing']['totals'], TIMING_TOTALS, 'timing.totals')
                self.each(data['assumptions']['items'], ASSUMPTION, 'assumptions.items')
                self.each(data['blockingQuestions']['items'], BLOCKING, 'blockingQuestions.items')

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
                for sub in data['substeps']:
                    self.assertRegex(sub['id'], r'^[A-Z]{1,3}-?\d+[a-z]?\.\d+[a-z]?$')
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
        cp_ready = json.loads(load('cp-ready')[1])
        self.assertEqual([(a['kind'], a['state']) for a in cp_ready['approvals']['actions'][2:]],
                         [('cp', 'waiting'), ('ready', 'waiting')])
        self.assertEqual(cp_ready['git']['headFull'], '<head>')
        after = json.loads((FIXTURES / 'cp-ready' / 'expected-after.json').read_text(encoding='utf-8'))
        cp = next(a for a in after['approvals']['actions'] if a['kind'] == 'cp')
        self.assertEqual((cp['state'], cp['given']['word'], cp['given']['sha'], after['approvals']['last']),
                         ('after', 'CP', '<head>', cp['given']))
        self.assertIsNotNone(cp['given']['at'])
        self.each(after['approvals']['actions'], ACTION, 'cp-ready after: approvals.actions')

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
