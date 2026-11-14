"""Pytania do eksperta osobno dla każdego przebiegu (Q-PER-RUN, 1.2.1): API, tryb zgodności ze starym plikiem,
`questions.json` poza hashem ekranów i zamrożeniem iteracji.

Kryteria: spec zadania (poza rdzeniem) (K2, K4, K5, D2, D7, D9). Serwer startuje na porcie 0
(harness `ServerCase`), loginy to `*@example.com`, hasła-atrapy są składane w czasie wykonania.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_prototype_questions_runs.py`.
"""
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype_server as ps  # noqa: E402
from tests import test_skillsync_prototype_questions_api as Q  # noqa: E402
from tests import test_skillsync_prototype_server as S  # noqa: E402
from tests.test_skillsync_prototype_server import setUpModule, tearDownModule  # noqa: E402,F401  (strażnik katalogu)

SECOND = 'etap-2'
OLD_Q = 'qn-0000000000a1'      # tylko w starym pliku, dla wszystkich
OLD_Q_HIDDEN = 'qn-0000000000a2'   # stary plik, adresowane do EX2 (EX nie widzi)
NEW_Q = 'qn-0000000000b1'      # etap-2, dla wszystkich
NEW_Q_EX2 = 'qn-0000000000b2'  # etap-2, tylko EX2


def make_run(root, slug):
    S.write(os.path.join(root, slug, 'prototype.json'), json.dumps(
        {'slug': slug, 'title': slug, 'requirements': 'docs/%s.md' % slug, 'created': '2026-11-01T09:00:00Z',
         'generated_by': 'skillsync prototype'}))


class RunsCase(Q.QCase):
    """Świat `QCase` (przebieg `demo` z zasianymi pytaniami) plus drugi przebieg z własnym plikiem."""

    def setUp(self):
        super().setUp()
        make_run(self.root, SECOND)
        self.app = self.server.app
        self.app.questions_store(SECOND).append([
            Q.ask(NEW_Q, origin={'spec': 'docs/specs/b.md', 'q': 'Q1'}),
            Q.ask(NEW_Q_EX2, to=[Q.EX2], origin={'spec': 'docs/specs/b.md', 'q': 'Q2'})])

    def listing(self, client, path='/_api/v1/questions'):
        return self.check(client.get(path), 200).json


class PerRunApiTests(RunsCase):
    def test_every_question_view_names_its_run(self):
        data = self.listing(self.who(Q.DE))
        by_id = {item['id']: item['slug'] for item in data['questions']}
        self.assertEqual(by_id[Q.Q1], 'demo')
        self.assertEqual(by_id[NEW_Q], SECOND)
        self.assertEqual(sorted(run['slug'] for run in data['runs']), ['demo', SECOND])

    def test_slug_filter_and_the_run_path_give_the_same_narrowed_list(self):
        client = self.who(Q.DE)
        for path in ('/_api/v1/questions?slug=' + SECOND, '/_api/v1/prototypes/%s/questions' % SECOND):
            with self.subTest(path=path):
                data = self.listing(client, path)
                self.assertEqual({item['slug'] for item in data['questions']}, {SECOND})
                self.assertEqual(sorted(item['id'] for item in data['questions']), sorted([NEW_Q, NEW_Q_EX2]))
                self.assertEqual(len(data['runs']), 2, 'lista przebiegów zawsze pełna (nawigacja strony)')

    def test_unknown_or_malformed_slug_is_404_not_an_empty_list(self):
        client = self.who(Q.DE)
        for path in ('/_api/v1/prototypes/nie-ma/questions', '/_api/v1/prototypes/X/questions',
                     '/_api/v1/questions?slug=nie-ma', '/_api/v1/questions?slug=..'):
            with self.subTest(path=path):
                self.check(client.get(path), 404)

    def test_expert_sees_a_run_only_when_it_has_a_visible_question(self):
        data = self.listing(self.who(Q.EX))   # EX: nie ma pytań do siebie w etap-2 poza adresowanymi do wszystkich
        self.assertEqual({item['slug'] for item in data['questions'] if item['id'] in (NEW_Q, NEW_Q_EX2)}, {SECOND})
        self.assertNotIn(NEW_Q_EX2, [item['id'] for item in data['questions']])
        ex3 = self.listing(self.who(Q.EX2))
        self.assertIn(NEW_Q_EX2, [item['id'] for item in ex3['questions']])

    def test_a_run_with_only_invisible_questions_is_absent_from_the_experts_runs(self):
        make_run(self.root, 'etap-3')
        self.app.questions_store('etap-3').append([Q.ask('qn-0000000000c1', to=[Q.EX2])])
        self.assertNotIn('etap-3', [run['slug'] for run in self.listing(self.who(Q.EX))['runs']])
        # a withdrawn question addressed to the expert is visible (as withdrawn), so its run is listed
        self.app.questions_store('etap-3').append([Q.ask('qn-0000000000c2'), {'type': 'withdraw', 'questionId': 'qn-0000000000c2',
                                                                           'by': 'agent', 'cid': 'wd-c2'}])
        self.assertIn('etap-3', [run['slug'] for run in self.listing(self.who(Q.EX))['runs']])
        self.assertIn('etap-3', [run['slug'] for run in self.listing(self.who(Q.EX2))['runs']])
        self.assertIn('etap-3', [run['slug'] for run in self.listing(self.who(Q.DE))['runs']])
        self.check(self.who(Q.EX).get('/_api/v1/questions?slug=etap-3'), 200)   # istnienie prototypu nie jest tajemnicą

    def test_runs_count_the_open_blocking_questions_the_viewer_sees(self):
        make_run(self.root, 'etap-3')
        self.app.questions_store('etap-3').append([
            Q.ask('qn-0000000000d1', blocking=True, assumed=None),
            Q.ask('qn-0000000000d2', to=[Q.EX2], blocking=True, assumed=None),
            Q.ask('qn-0000000000d3'),
            Q.ask('qn-0000000000d4', blocking=True, assumed=None),
            {'type': 'withdraw', 'questionId': 'qn-0000000000d4', 'by': 'agent', 'cid': 'wd-d4'}])
        def blocking(login):
            return {run['slug']: run['blocking'] for run in self.listing(self.who(login))['runs']}['etap-3']
        self.assertEqual(blocking(Q.EX), 1, 'ekspert: tylko widoczne, otwarte, blokujące')
        self.assertEqual(blocking(Q.EX2), 2)
        self.assertEqual(blocking(Q.DE), 2, 'administrator: bez wycofanych')
        runs = {run['slug']: run for run in self.listing(self.who(Q.EX))['runs']}
        self.assertEqual(runs['etap-3']['open'], 2)

    def test_a_commenter_gets_403_on_the_run_routes(self):
        commenter = [login for login, role in S.ROLES.items() if role == 'commenter'][0]
        client = self.client(commenter)
        for path in ('/_api/v1/prototypes/%s/questions' % SECOND, '/_api/v1/questions?slug=' + SECOND,
                     '/_api/v1/prototypes/%s/questions/%s' % (SECOND, NEW_Q)):
            with self.subTest(path=path):
                self.check(client.get(path), 403)
        self.check(S.Client(self.server).get('/_api/v1/prototypes/%s/questions' % SECOND), 401)

    def test_a_directory_without_prototype_json_does_not_break_the_list(self):
        S.write(os.path.join(self.root, 'luzem', 'questions.json'), '{"questionsFormat": 1, "operations": []}\n')
        data = self.listing(self.who(Q.DE))
        self.assertIn(Q.Q1, [item['id'] for item in data['questions']])
        self.assertEqual(self.app.locate_question(NEW_Q)[0], SECOND)

    def test_run_summary_counts_only_what_the_viewer_sees(self):
        by_slug = {run['slug']: run for run in self.listing(self.who(Q.EX))['runs']}
        by_slug_ex2 = {run['slug']: run for run in self.listing(self.who(Q.EX2))['runs']}
        self.assertEqual(by_slug_ex2[SECOND]['total'], by_slug[SECOND]['total'] + 1)

    def test_get_and_answer_find_the_question_in_its_own_file(self):
        ex = self.who(Q.EX)
        self.assertEqual(self.check(ex.get('/_api/v1/questions/' + NEW_Q), 200).json['question']['slug'], SECOND)
        self.check(ex.get('/_api/v1/prototypes/%s/questions/%s' % (SECOND, NEW_Q)), 200)
        self.check(ex.get('/_api/v1/prototypes/demo/questions/' + NEW_Q), 404)   # slug zawęża szukanie
        response = self.answer(ex, NEW_Q, {'cid': 'c-b1', 'text': 'tak'})
        self.check(response, 201)
        self.assertEqual(response.json['question']['slug'], SECOND)
        with open(os.path.join(self.root, SECOND, 'questions.json'), 'rb') as handle:
            second = json.loads(handle.read().decode('utf-8'))['operations']
        self.assertEqual([op['type'] for op in second if op['questionId'] == NEW_Q], ['ask', 'answer'])
        self.assertEqual(self.kinds(NEW_Q, 'answer'), [], 'plik przebiegu demo bez odpowiedzi na pytanie z etap-2')

    def test_comment_through_the_run_path_lands_in_that_run(self):
        ae = self.who(Q.AE)
        self.check(ae.post('/_api/v1/prototypes/%s/questions/%s/comment' % (SECOND, NEW_Q), {'cid': 'cm-1', 'text': 'uwaga'}), 201)
        self.assertEqual(len(self.app.questions_store(SECOND).snapshot().fold['byId'][NEW_Q]['comments']), 1)

    def test_a_damaged_file_of_one_run_does_not_hide_the_others(self):
        with open(os.path.join(self.root, SECOND, 'questions.json'), 'wb') as handle:
            handle.write(b'{"questionsFormat": 1, "operations": [')
        data = self.listing(self.who(Q.DE))
        self.assertIn(Q.Q1, [item['id'] for item in data['questions']])
        self.assertEqual(data['damaged'], [SECOND])
        self.assertNotIn('damaged', self.listing(self.who(Q.EX)), 'ekspert nie dostaje diagnostyki plików')
        self.check(self.who(Q.DE).get('/_api/v1/prototypes/%s/questions' % SECOND), 409)   # wskazany plik uszkodzony

    def test_only_the_two_top_levels_are_reserved_deeper_files_belong_to_the_prototype(self):
        S.write(os.path.join(self.demo, 'data', 'questions.json'), '{"quiz": []}')
        anon = S.Client(self.server)
        self.assertEqual(self.check(anon.get('/demo/data/questions.json'), 200).text, '{"quiz": []}')
        self.check(anon.get('/demo/questions.json'), 404)
        self.check(anon.get('/questions.json'), 404)

    def test_a_symlink_to_the_questions_file_inside_a_run_is_not_served(self):
        try:
            os.symlink('questions.json', os.path.join(self.demo, 'alias.txt'))
        except (OSError, NotImplementedError):
            self.skipTest('system plików nie tworzy dowiązań symbolicznych')
        self.check(S.Client(self.server).get('/demo/alias.txt'), 404)
        self.check(self.who(Q.DE).get('/demo/alias.txt'), 404)

    def test_the_renamed_old_file_is_not_served(self):
        S.write(os.path.join(self.root, '.questions.json.migrated'), '{"questionsFormat": 1, "operations": []}')
        self.check(S.Client(self.server).get('/.questions.json.migrated'), 404)

    def test_per_run_files_are_never_served_as_static_files(self):
        anon = S.Client(self.server)
        for path in ('/demo/questions.json', '/%s/questions.json' % SECOND, '/demo/QUESTIONS.JSON',
                     '/demo/questions.json.lock', '/%s/questions.json.lock' % SECOND, '/demo/./questions.json',
                     '//demo/questions.json', '/demo/%71uestions.json', '/demo/questions.json/', '/demo/x/../questions.json'):
            with self.subTest(path=path):
                self.check(anon.get(path), 404)
                self.check(self.who(Q.DE).get(path), 404)


class LegacyFileTests(RunsCase):
    """D7: starsza instalacja ma `<root>/questions.json`; serwer 1.2.1 pokazuje go tylko do odczytu."""

    def setUp(self):
        super().setUp()
        legacy = ps.QuestionsStore(self.root, clock=self.clock)
        legacy.append([Q.ask(OLD_Q), Q.ask(OLD_Q_HIDDEN, to=[Q.EX2])])

    def test_legacy_questions_are_listed_read_only_with_a_null_slug(self):
        data = self.listing(self.who(Q.EX))
        by_id = {item['id']: item for item in data['questions']}
        self.assertIsNone(by_id[OLD_Q]['slug'])
        self.assertNotIn(OLD_Q_HIDDEN, by_id)
        self.assertEqual(data['legacy'], {'count': 1, 'readOnly': True})
        self.assertEqual(self.listing(self.who(Q.DE))['legacy']['count'], 2)

    def test_a_question_present_in_a_run_and_in_the_old_file_is_listed_once_from_the_run(self):
        legacy = ps.QuestionsStore(self.root, clock=self.clock)
        legacy.append([Q.ask(NEW_Q)])   # migracja przerwana po zapisie celu
        data = self.listing(self.who(Q.DE))
        self.assertEqual([item['slug'] for item in data['questions'] if item['id'] == NEW_Q], [SECOND])

    def test_a_slug_list_does_not_include_the_legacy_questions(self):
        data = self.listing(self.who(Q.DE), '/_api/v1/questions?slug=demo')
        self.assertNotIn(OLD_Q, [item['id'] for item in data['questions']])
        self.assertEqual(data['legacy']['count'], 2)

    def test_writing_to_a_legacy_question_is_409_migration_required_and_changes_nothing(self):
        before = (Path(self.root) / 'questions.json').read_bytes()
        ex = self.who(Q.EX)
        self.check(self.answer(ex, OLD_Q, {'cid': 'c-old', 'text': 'tak'}), 409, 'migration_required')
        self.check(self.comment(ex, OLD_Q, {'cid': 'c-old2', 'text': 'x'}), 409, 'migration_required')
        self.assertEqual((Path(self.root) / 'questions.json').read_bytes(), before)

    def test_an_invisible_legacy_question_is_404_not_409(self):
        self.check(self.answer(self.who(Q.EX), OLD_Q_HIDDEN, {'cid': 'c-h', 'text': 'x'}), 404)

    def test_legacy_file_is_still_not_served_and_the_server_warns_at_start(self):
        self.check(S.Client(self.server).get('/questions.json'), 404)
        _errors, warnings = ps.startup_checks(self.server.app.config)
        self.assertIn(ps.LEGACY_QUESTIONS_WARNING, warnings)
        self.assertIn('question migrate', ps.LEGACY_QUESTIONS_WARNING)
        os.unlink(os.path.join(self.root, 'questions.json'))
        self.assertNotIn(ps.LEGACY_QUESTIONS_WARNING, ps.startup_checks(self.server.app.config)[1])


class OutsideScreenHashTests(unittest.TestCase):
    """D9/K5: odpowiedź eksperta nie może unieważniać akceptacji ekranów ani wchodzić do zamrożonej iteracji."""

    def setUp(self):
        import shutil
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix='proto-qhash-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.directory = os.path.join(self.tmp, 'demo')
        S.write(os.path.join(self.directory, 'index.html'), '<section class="screen" data-proto-id="a">A</section>')
        S.write(os.path.join(self.directory, 'tokens.css'), ':root{--a:1}')

    def test_questions_json_is_outside_the_assets_hash(self):
        before = ps.assets_hash(self.directory)
        S.write(os.path.join(self.directory, 'questions.json'), '{"questionsFormat": 1, "operations": []}\n')
        self.assertEqual(ps.assets_hash(self.directory), before)
        S.write(os.path.join(self.directory, 'sub', 'questions.json'), 'x')   # tylko najwyższy poziom jest wyłączony
        self.assertNotEqual(ps.assets_hash(self.directory), before, 'kontrola: zwykły plik zmienia hash')

    def test_questions_json_is_not_frozen_into_an_iteration(self):
        self.assertIn('questions.json', ps._FREEZE_EXCLUDED_TOP)


if __name__ == '__main__':
    unittest.main()
