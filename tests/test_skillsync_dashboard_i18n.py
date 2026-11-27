"""I18N-1.2/1.3: język statusu (`lang`, `texts`), teksty serwera w katalogu (`srv.*`) i angielskie słowa kluczowe parsera.

Dane testowe są angielskie i własne (nie z fixtur z polskimi danymi projektu): wszystko polskie w wyniku `en` pochodzi z kodu."""
import json
import re
import shutil
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from layers_helpers import guard_real_state  # noqa: E402
from skillsync_workflow import dashboard, i18n, status  # noqa: E402
from skillsync_workflow import dashboard_http as srv  # noqa: E402
from tests.test_skillsync_dashboard_server import HttpCase  # noqa: E402

POLISH = re.compile('[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]')
SPEC_REL = 'docs/specs/2026-01-10-widget.md'
PLAN_REL = 'docs/plans/2026-01-10-widget.md'
RUN_ID = '2026-01-10-widget'
SPEC = """# Spec: Widget export

Status: draft (waiting for GO plan)
Date: 2026-01-10
Run: 2026-01-10-widget
Prototype: widget-demo

## Open questions

- Q1 [ASSUMPTION]: output format - accepted: CSV
- Q2: should the export be paged?
"""
PLAN = """# Plan: Widget export

Status: in_progress
Date: 2026-01-10
Run: 2026-01-10-widget
Spec: docs/specs/2026-01-10-widget.md
Handoff: main

## Progress

### Phase 1: Build
- [x] S1 Parser
- [ ] S2 Exporter
- [ ] S3 Closure
"""
NO_PROBES = {'ahead': lambda root, branch, head: {'ahead': 0, 'remote': None, 'pushed': False, 'commits': [], 'commitsMore': 0},
             'prototype_up': lambda: False, 'writing': lambda slug: None, 'overview': lambda slug: None,
             'acceptance': lambda slug: None}


def strings(value):
    """Every string of a JSON value (keys too), depth first."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


class ProjectCase(unittest.TestCase):
    def setUp(self):
        guard_real_state(self)
        self.tmp = tempfile.TemporaryDirectory(prefix='skillsync-i18n-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'proj'
        self.root.mkdir()

    def put(self, rel, text):
        path = self.root.joinpath(*rel.split('/'))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def checkpoints(self, *lines):
        self.put(f'.skillsync/runs/{RUN_ID}/checkpoints.md', '\n'.join(['RUN=2026-01-10T09:00:00Z', *lines]) + '\n')

    def build(self, manifest, **probes):
        return dashboard.build_status(self.root, manifest=manifest, **{**NO_PROBES, **probes})

    def assertNoPolish(self, data, label=''):
        leaks = [text for text in strings(data) if POLISH.search(text)]
        self.assertEqual(leaks, [], f'polskie znaki w wyniku en {label}')


class LanguageFields(ProjectCase):
    def test_missing_key_is_polish_without_texts(self):
        self.put(SPEC_REL, SPEC)
        data = self.build({})
        self.assertEqual(data['lang'], 'pl')
        self.assertNotIn('texts', data)

    def test_polish_explicit_and_unknown_values_have_no_texts(self):
        self.put(SPEC_REL, SPEC)
        for manifest in ({'language': 'pl'}, {'language': 'de'}, {'language': 5}, {'language': ['en']}):
            data = self.build(manifest)
            self.assertEqual((data['lang'], 'texts' in data), ('pl', False), manifest)

    def test_english_has_lang_and_the_client_catalog(self):
        self.put(SPEC_REL, SPEC)
        data = self.build({'language': 'en'})
        self.assertEqual(data['lang'], 'en')
        self.assertEqual(data['texts'], i18n.catalog_for_client('en'))
        self.assertTrue(all(key.startswith('ui.') for key in data['texts']))

    def test_client_catalog_never_carries_server_keys(self):
        for lang in ('pl', 'en'):
            self.assertFalse([key for key in i18n.catalog_for_client(lang) if key.startswith('srv.')])

    def test_polish_status_is_the_same_with_or_without_the_key(self):
        self.put(SPEC_REL, SPEC)
        self.put(PLAN_REL, PLAN)
        self.checkpoints('- 09:05 start; waiting: CP (who: acceptance)')
        plain, explicit = self.build({}), self.build({'language': 'pl'})
        for data in (plain, explicit):
            data['generatedAt'] = 'x'
        self.assertEqual(plain, explicit)


class EnglishServerTexts(ProjectCase):
    def test_spec_act_with_prototype_and_waiting_marker(self):
        self.put(SPEC_REL, SPEC)
        self.checkpoints('- 09:05 spec ready; waiting: GO plan')
        data = self.build({'language': 'en'}, acceptance=lambda slug: {'screens': 3, 'accepted': 1, 'stale': 1})
        self.assertNoPolish(data)
        item = data['waiting'][0]
        self.assertEqual((item['word'], item['approve']), ('GO plan', 'spec'))
        self.assertIn('Read the spec', item['action'])
        self.assertEqual([stage['label'] for stage in data['acts'][0]['stages']], ['Draft', 'Review', 'Prototype 1/3', 'Approved'])
        self.assertEqual(data['acts'][0]['stages'][2]['tip'], 'widget-demo: accepted screens 1 of 3, stale 1')
        self.assertEqual([act['name'] for act in data['acts']], ['Spec', 'Plan', 'Implementation'])
        self.assertEqual(data['acts'][1]['detail'], 'no plan (waiting for GO plan)')
        self.assertEqual(data['assumptions']['items'][0]['accepted'], 'CSV')
        self.assertEqual([q['id'] for q in data['blockingQuestions']['items']], ['Q2'])

    def test_prototype_acceptance_item_and_button_reason(self):
        self.put(SPEC_REL, SPEC)
        self.put(PLAN_REL.replace('widget', 'other'), PLAN.replace('in_progress', 'draft').replace('Run: 2026-01-10-widget', 'Run: x'))
        data = self.build({'language': 'en'}, acceptance=lambda slug: {'screens': 3, 'accepted': 1, 'stale': 0})
        self.assertNoPolish(data)
        self.assertTrue(any('prototype' in item['what'] for item in data['waiting']), data['waiting'])

    def test_link_warnings(self):
        self.put(SPEC_REL, SPEC.replace('Run: 2026-01-10-widget\n', ''))
        self.assertEqual(self.build({'language': 'en'})['acts'][0]['linkWarning'],
                         'The spec does not name a run (no "Run:" line), so the page cannot see checkpoints or reports.')
        self.put(SPEC_REL, SPEC)
        self.put(PLAN_REL, PLAN.replace('Run: 2026-01-10-widget', 'Run: missing-run'))
        self.assertEqual(self.build({'language': 'en'})['acts'][0]['linkWarning'],
                         'Run directory missing-run does not exist in .skillsync/runs/, so the page cannot see checkpoints or reports.')

    def test_prototype_missing_warning(self):
        self.put(SPEC_REL, SPEC)
        self.checkpoints('- 09:05 spec ready')
        warning = self.build({'language': 'en'})['acts'][0]['linkWarning']
        self.assertEqual(warning, 'The spec points to prototype widget-demo, which is not in docs/prototypes/.')

    def test_plan_in_progress_delivery_consents_and_eta(self):
        self.put(SPEC_REL, SPEC.replace('draft (waiting for GO plan)', 'approved'))
        self.put(PLAN_REL, PLAN)
        self.checkpoints('- 09:05 start; in progress: S1', '- 09:20 done: S1; in progress: S2; eta: exporter until 23:59')
        ahead = {'ahead': 3, 'remote': 'origin/main', 'pushed': True, 'commits': [], 'commitsMore': 0}
        data = self.build({'language': 'en', 'delivery': {'mode': 'direct', 'base': 'main'}}, ahead=lambda *args: ahead)
        self.assertNoPolish(data)
        self.assertEqual(data['progress']['active'], ['S2'])
        self.assertEqual(data['eta']['operation'], 'exporter')
        words = {word['word']: word['tip'] for word in data['delivery']['words']}
        self.assertEqual(words['CP'], 'CP → origin/main; 3 commits not pushed to origin/main')
        self.assertEqual(data['acts'][2]['detail'], 'step 2 of 3: S2 Exporter')
        self.assertEqual(data['acts'][1]['detail'], '3 steps in 1 phase')
        cp = next(action for action in data['approvals']['actions'] if action['kind'] == 'cp')
        self.assertEqual(cp['reason'], 'no repository HEAD')

    def test_unpushed_commits_plural_per_language(self):
        for lang, expected in (('en', {1: '1 commit not pushed to origin/x', 2: '2 commits not pushed to origin/x'}),
                               ('pl', {1: '1 commit niewypchniętych względem origin/x', 2: '2 commity niewypchniętych względem origin/x',
                                       5: '5 commitów niewypchniętych względem origin/x'})):
            for count, text in expected.items():
                ahead = {'ahead': count, 'remote': 'origin/x', 'pushed': True}
                tips = dashboard.delivery_words({'mode': 'direct'}, ahead, None, [], lang=lang)
                self.assertTrue(tips[0]['tip'].endswith(text), (lang, count, tips[0]['tip']))

    def test_pending_checkpoint_waiting_items_with_who(self):
        self.put(SPEC_REL, SPEC.replace('draft (waiting for GO plan)', 'approved'))
        self.put(PLAN_REL, PLAN)
        for text, role, label in (('waiting: CP (who: acceptance)', 'acceptor', 'acceptance'),
                                  ('waiting: S2 review (who: developer ann)', 'developer', 'developer'),
                                  ('waiting: answer Q3 (who: expert)', 'expert', 'expert')):
            self.checkpoints(f'- 09:05 {text}')
            data = self.build({'language': 'en'})
            self.assertNoPolish(data, text)
            self.assertEqual((data['waiting'][0]['who']['role'], data['waiting'][0]['who']['label']), (role, label), text)

    def test_writing_and_expert_items(self):
        writing = dashboard.writing_item('widget-demo', {'since': 1_700_000_000_000, 'expiresAt': 1_700_000_600_000,
                                                         'screensPlanned': 5, 'screensDone': 2}, lang='en')
        self.assertTrue(writing['what'].startswith('agent is writing screens (2 of 5, until '), writing)
        self.assertNoPolish(writing)
        expert = dashboard.expert_item({'blocking': 2, 'runs': []}, lang='en')
        self.assertEqual(expert['what'], 'expert answer (2 blocking)')
        self.assertNoPolish(expert)
        one = dashboard.expert_item({'blocking': 1, 'runs': []}, lang='en')
        self.assertEqual(one['what'], 'expert answer (1 blocking)')

    def test_every_wait_item_action_in_english(self):
        for what in ('GO plan', 'GO implement', 'CP', 'READY', 'prototype widget-demo', 'answers Q3', 'retro P1 YES NO', 'something else'):
            item = dashboard.wait_item(what, 'checkpoint', None, {'spec': SPEC_REL, 'plan': PLAN_REL}, lang='en')
            self.assertNoPolish(item, what)
        self.assertEqual(dashboard.wait_item('something else', 'checkpoint', lang='en')['action'],
                         'The session is waiting: something else. Reply in the session.')

    def test_doc_group_labels(self):
        self.put(SPEC_REL, SPEC.replace('draft (waiting for GO plan)', 'approved'))
        self.put(PLAN_REL, PLAN)
        self.put('docs/specs/2025-01-01-older.md', '# Spec: Older\n\nStatus: implemented\n')
        self.put('docs/plans/2025-01-01-older.md', '# Plan: Older\n\nStatus: done\n')
        self.checkpoints('- 09:05 start')
        labels = {group['key']: group['label'] for group in self.build({'language': 'en'})['docs']}
        self.assertEqual(labels['task'], 'Current task')
        self.assertEqual(labels['run'], f'Run {RUN_ID}')
        self.assertEqual({k: v for k, v in labels.items() if k in ('specs', 'plans')}, {'specs': 'Specs', 'plans': 'Plans'})

    def test_polish_labels_are_unchanged(self):
        self.put(SPEC_REL, SPEC.replace('draft (waiting for GO plan)', 'approved'))
        self.put(PLAN_REL, PLAN)
        self.put('docs/specs/2025-01-01-older.md', '# Spec: Older\n\nStatus: implemented\n')
        self.checkpoints('- 09:05 start')
        data = self.build({})
        labels = {group['key']: group['label'] for group in data['docs']}
        self.assertEqual((labels['task'], labels['run'], labels['specs']), ('Bieżące zadanie', f'Przebieg {RUN_ID}', 'Speki'))
        self.assertEqual([act['name'] for act in data['acts']], ['Spec', 'Plan', 'Realizacja'])


class EnglishHttpTexts(ProjectCase):
    def setUp(self):
        super().setUp()
        self.put('.github/.skillsync.yaml', 'language: en\n')
        self.put(SPEC_REL, SPEC.replace('draft (waiting for GO plan)', 'approved'))
        self.put(PLAN_REL, PLAN)
        self.board = srv.Board(self.root, prototype_port=1)
        self.board.prototype_acceptance = lambda slug: None

    def test_board_approve_errors(self):
        for kind, rel, sha in (('bogus', PLAN_REL, 'x'), ('plan', 'docs/plans/none.md', 'x'), ('plan', PLAN_REL, 'x'), ('cp', 'r', 'x')):
            code, body = self.board.approve(kind, rel, sha)
            self.assertGreaterEqual(code, 400, (kind, rel))
            self.assertNoPolish(body, f'{kind} {rel}')
        self.assertEqual(self.board.approve('bogus', PLAN_REL, 'x'), (400, {'error': 'Invalid request.', 'code': 'badRequest'}))   # kontrakt U-2-3: kazda odmowa ma `code`

    def test_approvals_disabled(self):
        board = srv.Board(self.root, prototype_port=1, approvals=False)
        self.assertEqual(board.approve('plan', PLAN_REL, 'x'), (403, {'error': 'Approvals from this page are disabled (--no-approvals).',
                                                                       'code': 'approvals_disabled'}))   # kontrakt U-2-3: kazda odmowa ma `code`

    def test_host_refusals_are_translated(self):
        for role in (srv.ports.ROLE_READ, srv.ports.ROLE_APPROVE, srv.ports.ROLE_BOTH):
            text = srv.host_refused_message(role, 'evil.example', 'en')
            self.assertNotRegex(text, POLISH)
            self.assertIn('evil.example', text)
            self.assertNotEqual(text, srv.host_refused_message(role, 'evil.example'))
        self.assertIn('(none)', srv.host_refused_message(srv.ports.ROLE_READ, None, 'en'))
        self.assertIn('(brak)', srv.host_refused_message(srv.ports.ROLE_READ, None))

    def test_polish_constants_are_the_catalog_text(self):
        # kontrakt U-2: `HOST_REFUSED` (one text for every socket) and `TOKEN_REFUSED` are gone: the Host text names the socket (`host_refused_message`),
        # the consent has no token, and its refusals are the codes of the catalogue (`srv.http.loginRequired` ...)
        self.assertFalse(hasattr(srv, 'HOST_REFUSED'))
        self.assertFalse(hasattr(srv, 'TOKEN_REFUSED'))
        self.assertTrue(srv.PROXY_REFUSED)
        self.assertTrue(i18n.t('pl', 'srv.http.loginRequired').startswith('Zgoda wymaga zalogowania'))
        self.assertIn('„dalej”', srv.NEXT_HINT)
        self.assertNotRegex(i18n.t('en', 'srv.http.nextHint'), POLISH)


class EnglishHttp(HttpCase):
    def setUp(self):
        super().setUp()
        manifest = self.root / '.github' / '.skillsync.yaml'
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text('language: en\n', encoding='utf-8')
        self.board.manifest()   # the board has read the project before the manifest existed

    def test_unknown_page_and_the_refusals_of_a_consent_are_in_the_language_of_the_project(self):
        """Kontrakt U-2: the refusals of a consent are `{error, code}` with a text of the catalogue (no token refusal any more)."""
        try:
            self.get('/nope')
        except urllib.error.HTTPError as error:
            self.assertEqual((error.code, json.load(error)), (404, {'error': 'There is no such page.'}))
        else:
            self.fail('404 expected')
        offer = {'kind': 'plan', 'path': PLAN_REL, 'sha': 'x'}
        origin = {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{self.port}'}
        code, body = self.post(offer, origin)   # the session headers are added, `X-Status-Board` is missing
        self.assertEqual((code, body['code']), (403, 'consent_refused'))
        self.assertEqual(body['error'], i18n.t('en', 'srv.http.consentRefused'))
        anonymous = {**origin, 'X-Status-Board': '1', 'Sec-Fetch-Site': 'same-origin'}
        code, body = self.post(offer, anonymous, session=False)
        self.assertEqual((code, body['code']), (401, 'login_required'))
        self.assertEqual(body['error'], i18n.t('en', 'srv.http.loginRequired'))
        code, body = self.post(offer, {**anonymous, 'Cookie': self.session.stale_pair}, session=False)
        self.assertEqual((code, body['code']), (401, 'session_expired'))
        self.assertEqual(body['error'], i18n.t('en', 'srv.http.sessionExpired'))
        for text in (i18n.t('en', key) for key in ('srv.http.consentRefused', 'srv.http.loginRequired', 'srv.http.sessionExpired', 'srv.http.forbiddenRole',
                                                   'srv.http.consentContext', 'srv.http.passwordChangeRequired', 'srv.http.hostPage')):
            self.assertNotRegex(text, POLISH)
        for key in ('srv.http.consentRefused', 'srv.http.loginRequired', 'srv.http.sessionExpired', 'srv.http.forbiddenRole', 'srv.http.consentContext',
                    'srv.http.passwordChangeRequired', 'srv.http.hostPage'):
            self.assertNotEqual(i18n.t('en', key), i18n.t('pl', key), key)   # translated, not copied
        self.assertNotRegex(body['error'], POLISH)


class Keywords(unittest.TestCase):
    def test_risky_sections_stay_polish_until_aliases(self):
        self.assertEqual(dashboard.risky_sections('# S\n\n## Kontrakty\n\nThe export API changes.\n'), ['Kontrakty'])
        self.assertEqual(dashboard.risky_sections('# S\n\n## Dane\n\nNot applicable.\n'), [])
        self.assertEqual(dashboard.risky_sections('# S\n\n## Migracja\n\nnie dotyczy\n'), [])

    def test_pre_implement_sections_stay_polish_until_aliases(self):
        found = status.pre_implement_sections('# S\n\n## Kontrakty\n\nNew endpoint.\n\n## Migracja\n\nNot applicable.\n')
        self.assertEqual(len(found), 1, found)

    def test_waiting_marker(self):
        self.assertEqual(dashboard.wait_marker('09:05 spec ready; waiting: GO plan'), 'GO plan')
        self.assertEqual(dashboard.wait_marker('Waiting : CP.'), 'CP')
        self.assertIsNone(dashboard.wait_marker('waiting: none'))
        self.assertIsNone(dashboard.wait_marker('waiting: n/a'))
        self.assertEqual(dashboard.wait_marker('czeka: GO plan'), 'GO plan')   # Polish unchanged
        self.assertIsNone(dashboard.wait_marker('czeka: nic'))

    def test_done_and_in_progress_lists(self):
        self.assertEqual(dashboard.ids_reported_done('done: S1, S2; in progress: S3'), ['S1', 'S2'])
        self.assertEqual(dashboard.ids_in_progress('done: S1; in progress: S3 / S4'), ['S3', 'S4'])
        self.assertIsNone(dashboard.ids_in_progress('the work is in progress somewhere'))
        self.assertEqual(dashboard.ids_reported_done('zrobione: S1; w toku: S2'), ['S1'])
        self.assertEqual(dashboard.ids_in_progress('zrobione: S1; w toku: S2'), ['S2'])

    def test_english_report_warnings_use_the_same_reasons(self):
        found = dashboard.report_warnings('done: S1, some prose; in progress: none')
        self.assertEqual([(w['segment'], w['reason']) for w in found], [('zrobione', 'no-step-id')])

    def test_eta_until(self):
        self.assertEqual(dashboard.parse_eta('eta: exporter until 14:30'), ('exporter', '14:30'))
        self.assertEqual(dashboard.parse_eta('eta: parsery do 14:30'), ('parsery', '14:30'))
        self.assertIsNone(dashboard.parse_eta('eta: exporter until later'))

    def test_ongoing_stage_marker(self):
        self.assertEqual(dashboard.trwa_marker('ongoing: review, prototype'), ['review', 'prototype'])
        self.assertEqual(dashboard.trwa_marker('trwa: review'), ['review'])
        self.assertIsNone(dashboard.trwa_marker('ongoing: none'))

    def test_who_suffix(self):
        self.assertEqual(dashboard.split_who('CP (who: acceptance)', lang='en')[1],
                         {'role': 'acceptor', 'login': None, 'label': 'acceptance'})
        self.assertEqual(dashboard.split_who('CP (who: acceptance)')[1]['label'], 'odbiór')
        self.assertEqual(dashboard.split_who('CP (who: approver ann)')[1]['login'], 'ann')
        self.assertEqual(dashboard.split_who('CP (who: acceptor)', lang='en')[1]['label'], 'acceptor')
        self.assertEqual(dashboard.split_who('CP (who: acceptor)')[1]['label'], 'akceptujący')
        self.assertEqual(dashboard.split_who('CP (kto: odbiór)')[1]['label'], 'odbiór')
        self.assertEqual(dashboard.split_who('CP (kto: odbiór)', lang='en')[1]['label'], 'acceptance')

    def test_status_text_waiting_for(self):
        self.assertEqual(dashboard.STATUS_WAIT_RE.search('draft (waiting for GO plan)').group('what'), 'GO plan')
        self.assertEqual(dashboard.STATUS_WAIT_RE.search('draft (czeka na GO plan)').group('what'), 'GO plan')

    def test_questions_section_tags_and_accepted(self):
        text = ('# Spec\n\n## Open questions\n\n- Q1 [assumption]: format — accepted: CSV\n'
                '- Q2 [RESOLVED 2026-01-10, owner]: language\n- Q3: paged?\n\n## Scope\n\n- Q4: outside\n')
        assumptions, blocking = dashboard.parse_questions(text)
        self.assertEqual(assumptions, [{'id': 'Q1', 'question': 'format', 'accepted': 'CSV'}])
        self.assertEqual([q['id'] for q in blocking], ['Q3'])
        polish = '# Spec\n\n## Pytania otwarte\n\n- Q1 [ZAŁOŻENIE]: format — przyjęte: CSV\n- Q2: strony?\n'
        self.assertEqual(dashboard.parse_questions(polish)[0][0]['accepted'], 'CSV')

    def test_header_aliases(self):
        title, fields = dashboard.parse_header('# Plan: x\n\nRun: r1\nPrototype: p1\nDate: 2026-01-01\nHandoff: dev\n\n## A\n')
        self.assertEqual((fields['przebieg'], fields['prototyp'], fields['data'], fields['oddanie']), ('r1', 'p1', '2026-01-01', 'dev'))
        _, polish = dashboard.parse_header('# Plan: x\n\nPrzebieg: r2\nPrototyp: p2\n')
        self.assertEqual((polish['przebieg'], polish['prototyp']), ('r2', 'p2'))
        _, both = dashboard.parse_header('# Plan: x\n\nPrzebieg: first\nRun: second\n')
        self.assertEqual(both['przebieg'], 'first')

    def test_result_section_in_english(self):
        info = {'status': 'done', 'path': 'docs/plans/x.md'}
        self.assertEqual(dashboard.build_result('# P\n\n## Result\n\nAll good\n', info)['text'], 'All good')
        self.assertEqual(dashboard.build_result('# P\n\n## Wynik\n\nOk\n', info)['text'], 'Ok')


class StatusKeywordsAndTexts(ProjectCase):
    def test_acceptance_step_in_english(self):
        plan = '# P\n\nStatus: done\n\n## Progress\n\n- [x] S1 Build\n- [ ] S2 Acceptance\n'
        self.assertTrue(status.has_acceptance_step('done', plan))
        self.assertTrue(status.has_acceptance_step('done', plan.replace('Acceptance', 'Odbiór')))
        self.assertFalse(status.has_acceptance_step('done', plan.replace('Acceptance', 'Review')))

    def test_human_summary_section(self):
        self.assertEqual(status.plan_summary('# P\n\n## Summary for humans\n\nShort text.\n'), 'Short text.')
        self.assertEqual(status.plan_summary('# P\n\n## Streszczenie dla człowieka\n\nKrótko.\n'), 'Krótko.')

    def test_pre_implement_warnings_are_translated(self):
        self.put(SPEC_REL, '# Spec\n\n## Kontrakty\n\nThe export API.\n')
        plan = '# Plan\n\nStatus: draft\nPrzebieg: r1\nSpec: docs/specs/2026-01-10-widget.md\n'
        self.put('.skillsync/runs/r1/pre-implement.md', 'Verdict: NEEDS_CHANGES\n')
        english = status.pre_implement_state(self.root, SPEC_REL, plan, 'draft', lang='en')
        self.assertEqual(english['verdict'], 'NEEDS_CHANGES')
        self.assertEqual(english['warning'], 'pre-implement: verdict NEEDS_CHANGES (.skillsync/runs/r1/pre-implement.md); '
                                             'resolve the findings before "GO implement"')
        polish = status.pre_implement_state(self.root, SPEC_REL, plan, 'draft')
        self.assertTrue(polish['warning'].startswith('pre-implement: werdykt NEEDS_CHANGES'), polish)
        shutil.rmtree(self.root / '.skillsync')
        self.assertTrue(status.pre_implement_state(self.root, SPEC_REL, plan, 'draft', lang='en')['warning'].startswith('no pre-implement:'))
        for lang in ('pl', 'en'):
            state = status.pre_implement_state(self.root, SPEC_REL, plan.replace('Przebieg: r1\n', ''), 'draft', lang=lang)
            self.assertIn('Kontrakty', state['warning'])
        self.assertNotRegex(status.pre_implement_state(self.root, SPEC_REL, plan.replace('Przebieg: r1\n', ''), 'draft', lang='en')['warning'], POLISH)

    def test_project_status_warns_about_an_unknown_language(self):
        report = status.project_status(self.root, {'language': 'de'})
        self.assertTrue(any('language' in finding for finding in report['findings']), report['findings'])
        self.assertEqual(status.project_status(self.root, {})['findings'], [])
        self.assertEqual(status.project_status(self.root, {'language': 'en'})['findings'], [])


class CatalogUse(unittest.TestCase):
    SOURCES = ('dashboard.py', 'dashboard_detach.py', 'dashboard_http.py', 'dashboard_ports.py', 'dashboard_token.py', 'status.py')   # kontrakt U-3a: dashboard_ports czyta katalog

    @staticmethod
    def used():
        keys = set()
        for name in CatalogUse.SOURCES:
            text = (ROOT / 'skillsync_workflow' / name).read_text(encoding='utf-8')
            keys |= set(re.findall(r"""['"](srv\.[A-Za-z0-9._]+)['"]""", text))
        return keys

    def test_every_key_used_in_code_is_in_both_catalogs(self):
        for lang in ('pl', 'en'):
            missing = sorted(key for key in self.used() if key not in i18n.catalog(lang))
            self.assertEqual(missing, [], lang)

    def test_no_unused_server_keys(self):
        used = self.used()
        for lang in ('pl', 'en'):
            unused = sorted(key for key in i18n.catalog(lang) if key.startswith('srv.') and key not in used)
            self.assertEqual(unused, [], lang)

    def test_catalog_has_server_keys_in_both_languages(self):
        for lang in ('pl', 'en'):
            self.assertGreaterEqual(len([key for key in i18n.catalog(lang) if key.startswith('srv.')]), 60)


if __name__ == '__main__':
    unittest.main()
