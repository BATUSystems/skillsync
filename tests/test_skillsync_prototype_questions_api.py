"""API HTTP pytań do eksperta (EXPERT-Q, E3): sesja, `mustChange`, role, widoczność, kolejność kontroli, statyka.

Kryteria: .skillsync/runs/EXPERT-Q/spec.md (K5, K11, K4 część API, D10, D13, D14, D19, „Kontrakty -> API",
„Widoczność i kolejność kontroli", „Bezpieczeństwo"). Serwer startuje na porcie 0 (harness `ServerCase`), loginy to
`*@example.com`, hasła-atrapy są składane w czasie wykonania, iteracje PBKDF2 niskie. Magazyn jest zasilany przez
`QuestionsStore`. Kody spoza specu (np. 403 vs 404 dla widocznego, ale nieadresowanego pytania) są sprawdzane
przedziałem. Każdy test negatywny ma przypadek kontrolny, który przechodzi.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_prototype_questions_api.py`.
Zgodność ze składnią Pythona 3.8.
"""
import json
import os
import shutil
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype_server as ps  # noqa: E402
from tests import test_skillsync_prototype_server as S  # noqa: E402
from tests.test_skillsync_prototype_server import setUpModule, tearDownModule  # noqa: E402,F401  (strażnik katalogu)

EX = 'ewa.ekspert@example.com'          # tylko expert
EX2 = 'ola.ekspert@example.com'         # tylko expert (drugi)
EX3 = 'iwo.ekspert@example.com'         # tylko expert (odpowiada na Q6)
AE = 'adam.akceptor@example.com'        # acceptor + expert
DE = 'dawid.dev@example.com'            # developer + expert
MX = 'maja.nowa@example.com'            # expert z hasłem domyślnym (mustChange)
PW = 'Pyt-' + 'Haslo-' + '5512'         # atrapa składana w czasie wykonania
NEWPW = 'Pyt-' + 'Nowe-' + '9981-xyz'

Q1 = 'qn-000000000001'   # all, assumed, opcje o1/o2, otwarte
Q2 = 'qn-000000000002'   # [EX, EX2], blokujące, opcje, otwarte
Q3 = 'qn-000000000003'   # [EX2], poufne
Q4 = 'qn-000000000004'   # all, refersTo Q3
Q5 = 'qn-000000000005'   # all, wycofane
Q6 = 'qn-000000000006'   # all, odpowiedziane przez EX3
Q7 = 'qn-000000000007'   # [DE]
Q8 = 'qn-000000000008'   # all, opcje
Q9 = 'qn-000000000009'   # all (wyścig)
QX = 'qn-0000000000ff'   # nieistnieje
SECRET = 'SEKRET-POUFNEGO-WYCIAGU'


def ask(qid, to='all', **over):
    op = {'type': 'ask', 'questionId': qid, 'by': 'agent', 'cid': 'ask-' + qid, 'text': 'Pytanie %s' % qid,
          'blocking': False, 'assumed': 'tak', 'addressees': to, 'refs': [], 'options': [],
          'origin': {'spec': 'docs/specs/a.md', 'q': 'Q1'}}
    op.update(over)
    return op


OPTIONS = [{'id': 'o1', 'label': 'Tak', 'recommended': True}, {'id': 'o2', 'label': 'Nie'}]


def seed_operations():
    return [
        ask(Q1, options=OPTIONS, refs=[{'title': 'Sekcja', 'href': 'docs/a.md#s', 'excerpt': 'wyciag publiczny'}]),
        ask(Q2, to=[EX, EX2], blocking=True, assumed=None, options=OPTIONS),
        ask(Q3, to=[EX2], text='Pytanie poufne ' + SECRET, refs=[{'title': 'T', 'href': 'docs/b.md', 'excerpt': SECRET}]),
        ask(Q4, refersTo=Q3),
        ask(Q5),
        {'type': 'withdraw', 'questionId': Q5, 'by': 'agent', 'cid': 'wd-5'},
        ask(Q6),
        {'type': 'answer', 'questionId': Q6, 'by': EX3, 'cid': 'ans-6', 'text': 'odpowiedz trzeciego'},
        ask(Q7, to=[DE]),
        ask(Q8, options=OPTIONS),
        ask(Q9),
    ]


class QCase(S.ServerCase):
    """Świat testowy plus konta ekspertów i zasilony `questions.json`."""

    def setUp(self):
        super().setUp()
        users = ps.UsersFile(self.users_path, iterations=S.ITER, clock=self.clock)
        for login in (EX, EX2, EX3):
            ps.add_user(users, login, roles=['expert'], password=PW)
        ps.add_user(users, AE, roles=['expert', 'acceptor'], password=PW)
        ps.add_user(users, DE, roles=['expert', 'developer'], password=PW)
        ps.add_user(users, MX, roles=['expert'], default_password=S.DEFAULT_PW)
        self.start_server()   # serwer startuje po dodaniu kont (ostrzeżenia startowe)
        self.seed_questions(seed_operations())
        self.qpath = os.path.join(self.root, 'questions.json')

    def seed_questions(self, partials):
        self.server.app.questions_store().append(partials)

    def who(self, login):
        client = S.Client(self.server)
        password = PW if login in (EX, EX2, EX3, AE, DE) else S.PASSWORDS[login]
        self.assertEqual(client.login(login, password).status, 200)
        return client

    def stored(self):
        with open(self.qpath, 'rb') as handle:
            return handle.read()

    def operations_on_disk(self):
        return json.loads(self.stored().decode('utf-8'))['operations']

    def kinds(self, qid, kind):
        return [op for op in self.operations_on_disk() if op['questionId'] == qid and op['type'] == kind]

    def answer(self, client, qid, body, **kwargs):
        return client.post('/_api/v1/questions/%s/answer' % qid, body, **kwargs)

    def comment(self, client, qid, body, **kwargs):
        return client.post('/_api/v1/questions/%s/comment' % qid, body, **kwargs)

    def assert_no_write(self, before, response, status, error=None):
        self.check(response, status, error)
        self.assertEqual(self.stored(), before, 'questions.json zmieniony mimo odmowy')

    @staticmethod
    def fingerprint(response):
        """Wszystko, co klient widzi, poza `Date` (czas odpowiedzi)."""
        headers = sorted((key.lower(), value) for key, value in response.headers.items() if key.lower() != 'date')
        return response.status, response.body, headers


# ═════════════════════════════════════════════════════════════════════════════
# (a) sesja wymagana zawsze
# ═════════════════════════════════════════════════════════════════════════════

class SessionRequiredTests(QCase):
    def routes(self, client):
        return [client.get('/_api/v1/questions'), client.get('/_api/v1/questions/%s' % Q1),
                self.answer(client, Q1, {'cid': 'c-1', 'confirm': True}),
                self.comment(client, Q1, {'cid': 'c-2', 'text': 'x'})]

    def test_without_login_to_view_every_route_is_401_unauthorized(self):
        before = self.stored()
        for response in self.routes(self.anon):
            self.check(response, 401, 'unauthorized')
            self.assertNotIn(SECRET, response.text)
        self.assertEqual(self.stored(), before)
        # kontrola: te same trasy z sesją działają
        expert = self.who(EX)
        self.check(expert.get('/_api/v1/questions'), 200)
        self.check(expert.get('/_api/v1/questions/%s' % Q1), 200)

    def test_with_login_to_view_routes_behave_like_the_existing_ones(self):
        self.start_server(login_to_view=True)
        reference = self.anon.get('/_api/v1/project')
        self.assertEqual(reference.status, 401)
        for response in self.routes(self.anon):
            self.assertEqual(response.status, 401, response.text)
            self.assertEqual(response.json['error'], reference.json['error'])
            self.assertNotIn(SECRET, response.text)
        client = S.Client(self.server)
        self.assertEqual(client.login(EX, PW).status, 200)
        self.check(client.get('/_api/v1/questions'), 200)   # kontrola

    def test_garbage_and_logged_out_cookies_are_401(self):
        expert = self.who(EX)
        stale = S.Client(self.server)
        stale.cookie = expert.cookie
        stale.csrf = expert.csrf
        self.check(expert.post('/_api/v1/logout'), 204)
        for response in self.routes(stale):
            self.check(response, 401)
        garbage = S.Client(self.server)
        garbage.cookie = 'skillsync_session=zmyslony; other=1'
        for response in self.routes(garbage):
            self.check(response, 401)

    def test_unauthenticated_post_with_a_huge_body_is_401_not_413(self):
        before = self.stored()
        huge = b'{"cid":"c-1","text":"' + b'x' * (80 * 1024) + b'"}'
        for kind in ('answer', 'comment'):
            self.assert_no_write(before, self.anon.post('/_api/v1/questions/%s/%s' % (Q1, kind), raw=huge), 401)


# ═════════════════════════════════════════════════════════════════════════════
# (b) mustChange
# ═════════════════════════════════════════════════════════════════════════════

class MustChangeTests(QCase):
    def setUp(self):
        super().setUp()
        self.must = S.Client(self.server)
        self.check(self.must.login(MX, S.DEFAULT_PW), 200)

    def test_every_question_route_is_403_password_change_required_and_leaks_nothing(self):
        before = self.stored()
        for response in (self.must.get('/_api/v1/questions'), self.must.get('/_api/v1/questions/%s' % Q1),
                         self.must.get('/_api/v1/questions/%s' % QX),
                         self.answer(self.must, Q1, {'cid': 'c-1', 'confirm': True}),
                         self.comment(self.must, Q1, {'cid': 'c-2', 'text': 'x'}),
                         self.answer(self.must, QX, {'cid': 'c-3', 'confirm': True})):
            self.assertEqual(response.status, 403, response.text)
            self.assertEqual(response.json['error'], 'password_change_required')
            self.assertNotIn('Pytanie', response.text)
        self.assertEqual(self.stored(), before)

    def test_csrf_is_checked_before_must_change(self):
        response = self.answer(self.must, Q1, {'cid': 'c-1', 'confirm': True}, headers={'X-Skillsync-CSRF': 'zly'})
        self.check(response, 403, 'invalid_csrf')

    def test_after_the_password_change_the_routes_work(self):
        self.check(self.must.post('/_api/v1/password', {'current': S.DEFAULT_PW, 'new': NEWPW}), 204)
        self.check(self.must.get('/_api/v1/questions'), 200)
        self.check(self.must.get('/_api/v1/questions/%s' % Q1), 200)
        self.check(self.comment(self.must, Q1, {'cid': 'c-9', 'text': 'po zmianie hasla'}), 201)
        self.check(self.answer(self.must, Q1, {'cid': 'c-8', 'confirm': True}), 201)
        self.assertEqual([op['by'] for op in self.kinds(Q1, 'answer')], [MX])


# ═════════════════════════════════════════════════════════════════════════════
# (c) role i widoczność (K5, D13)
# ═════════════════════════════════════════════════════════════════════════════

class RoleAndVisibilityTests(QCase):
    def ids(self, response):
        return [item['id'] for item in self.check(response, 200).json['questions']]

    def test_commenter_is_403_forbidden_on_every_route_also_for_unknown_ids(self):
        commenter = self.who(S.A)
        before = self.stored()
        for response in (commenter.get('/_api/v1/questions'), commenter.get('/_api/v1/questions/%s' % Q1),
                         commenter.get('/_api/v1/questions/%s' % QX),
                         self.answer(commenter, Q1, {'cid': 'c-1', 'confirm': True}),
                         self.comment(commenter, Q1, {'cid': 'c-2', 'text': 'x'}),
                         self.comment(commenter, QX, {'cid': 'c-3', 'text': 'x'})):
            self.assertEqual(response.status, 403, response.text)
            self.assertEqual(response.json['error'], 'forbidden')
            self.assertNotIn('Pytanie', response.text)
        self.assertEqual(self.stored(), before)

    def test_expert_only_sees_all_and_own_without_addressees_and_without_withdrawn(self):
        expert = self.who(EX)
        listing = self.check(expert.get('/_api/v1/questions'), 200).json['questions']
        self.assertEqual([item['id'] for item in listing], [Q1, Q2, Q4, Q6, Q8, Q9])
        for item in listing:
            self.assertNotIn('addressees', item)
            self.assertIn(item['forAll'], (True, False))
        self.assertEqual({item['id']: item['forAll'] for item in listing}[Q2], False)
        self.assertEqual({item['id']: item['forAll'] for item in listing}[Q1], True)
        body = expert.get('/_api/v1/questions').text
        for hidden in (SECRET, EX2, 'Pytanie %s' % Q3, 'Pytanie %s' % Q5, 'Pytanie %s' % Q7):
            self.assertNotIn(hidden, body)
        self.assertIn(EX3, body, 'kontrola: autor widocznej odpowiedzi jest w treści')

    def test_recorded_shows_the_expert_only_the_time_and_the_admin_everything(self):
        spec = 'docs/specs/zapisane-w-specu.md'
        self.seed_questions([{'type': 'recorded', 'questionId': Q6, 'by': 'agent', 'cid': 'rec-6', 'spec': spec, 'line': 41}])
        expert = self.who(EX)
        listing = self.check(expert.get('/_api/v1/questions'), 200)
        detail = self.check(expert.get('/_api/v1/questions/%s' % Q6), 200)
        for response, view in ((listing, {i['id']: i for i in listing.json['questions']}[Q6]), (detail, detail.json['question'])):
            self.assertEqual(set(view['recorded']), {'at'})
            self.assertTrue(view['recorded']['at'])
            self.assertNotIn(spec, response.text)
            self.assertNotIn('"line"', response.text)
        for login in (DE, AE):
            admin = self.who(login)
            view = self.check(admin.get('/_api/v1/questions/%s' % Q6), 200).json['question']
            self.assertEqual((view['recorded']['spec'], view['recorded']['line']), (spec, 41))

    def test_expert_gets_404_for_foreign_and_withdrawn_and_200_for_visible(self):
        expert = self.who(EX)
        self.check(expert.get('/_api/v1/questions/%s' % Q3), 404, 'not_found')
        self.check(expert.get('/_api/v1/questions/%s' % Q5), 404, 'not_found')
        self.check(expert.get('/_api/v1/questions/%s' % Q7), 404, 'not_found')
        for qid in (Q1, Q2, Q4, Q6):   # kontrola
            detail = self.check(expert.get('/_api/v1/questions/%s' % qid), 200).json['question']
            self.assertEqual(detail['id'], qid)
            self.assertNotIn('addressees', detail)

    def test_refers_to_an_invisible_question_is_omitted_for_the_expert_only(self):
        listing = {item['id']: item for item in self.check(self.who(EX).get('/_api/v1/questions'), 200).json['questions']}
        self.assertFalse(listing[Q4].get('refersTo'))
        detail = self.check(self.who(EX).get('/_api/v1/questions/%s' % Q4), 200).json['question']
        self.assertFalse(detail.get('refersTo'))
        self.assertNotIn(Q3, json.dumps(detail))
        # kontrola: adresat Q3 widzi odwołanie, administrator też
        for client in (self.who(EX2), self.who(S.D), self.who(S.C)):
            self.assertEqual(self.check(client.get('/_api/v1/questions/%s' % Q4), 200).json['question']['refersTo'], Q3)

    def test_developer_and_acceptor_see_everything_with_addressees(self):
        for login in (S.D, S.C, DE, AE):
            with self.subTest(login=login):
                client = self.who(login)
                listing = self.check(client.get('/_api/v1/questions'), 200).json['questions']
                self.assertEqual([item['id'] for item in listing], [Q1, Q2, Q3, Q4, Q5, Q6, Q7, Q8, Q9])
                by_id = {item['id']: item for item in listing}
                self.assertEqual(by_id[Q3]['addressees'], [EX2])
                self.assertEqual(by_id[Q1]['addressees'], 'all')
                self.assertEqual(by_id[Q5]['state'], 'withdrawn')
                self.check(client.get('/_api/v1/questions/%s' % Q5), 200)

    def test_developer_and_acceptor_without_expert_only_comment(self):
        for login in (S.D, S.C):
            with self.subTest(login=login):
                client = self.who(login)
                before = self.stored()
                response = self.answer(client, Q8, {'cid': 'c-a-%s' % login[:3], 'optionId': 'o1'})
                self.check(response, 403, 'forbidden')
                self.assertEqual(self.stored(), before)
                self.check(self.comment(client, Q3, {'cid': 'c-k-%s' % login[:3], 'text': 'uwaga programisty'}), 201)
        self.assertEqual(len(self.kinds(Q3, 'comment')), 2)

    def test_expert_with_developer_or_acceptor_answers_only_as_an_addressee(self):
        before = self.stored()
        dev_expert = self.who(DE)
        # Q3 jest adresowane do EX2: DE je widzi jako developer, ale nie odpowiada
        response = self.answer(dev_expert, Q3, {'cid': 'c-de-1', 'text': 'nie moja'})
        self.check(response, 403, 'forbidden')
        self.assertEqual(self.stored(), before)
        self.check(self.comment(dev_expert, Q3, {'cid': 'c-de-2', 'text': 'komentarz ok'}), 201)
        # kontrola: jako adresat Q7 i jako ekspert pytania "all" odpowiada
        self.check(self.answer(dev_expert, Q7, {'cid': 'c-de-3', 'text': 'moja odpowiedz'}), 201)
        self.check(self.answer(self.who(AE), Q8, {'cid': 'c-ae-1', 'optionId': 'o2'}), 201)
        self.assertEqual([op['by'] for op in self.kinds(Q7, 'answer')], [DE])

    def test_expert_cannot_answer_a_question_addressed_to_others(self):
        before = self.stored()
        expert = self.who(EX)
        self.assert_no_write(before, self.answer(expert, Q3, {'cid': 'c-1', 'text': 'x'}), 404, 'not_found')
        self.assert_no_write(before, self.comment(expert, Q3, {'cid': 'c-2', 'text': 'x'}), 404, 'not_found')
        self.assert_no_write(before, self.answer(expert, Q7, {'cid': 'c-3', 'text': 'x'}), 404, 'not_found')


# ═════════════════════════════════════════════════════════════════════════════
# (d) 404 nieodróżnialne
# ═════════════════════════════════════════════════════════════════════════════

class IndistinguishableNotFoundTests(QCase):
    def test_get_detail_is_byte_identical_for_missing_foreign_withdrawn_and_malformed(self):
        expert = self.who(EX)
        prints = {qid: self.fingerprint(expert.get('/_api/v1/questions/%s' % qid)) for qid in (QX, Q3, Q5, Q7, 'zly-id')}
        self.assertEqual(prints[QX][0], 404)
        for qid, value in prints.items():
            with self.subTest(qid=qid):
                self.assertEqual(value, prints[QX])
        self.assertNotEqual(prints[QX], self.fingerprint(expert.get('/_api/v1/questions/%s' % Q1)), 'kontrola')

    def test_head_is_404_identical_to_get_for_missing_and_invisible(self):
        expert = self.who(EX)
        for qid in (QX, Q3, Q5, Q7):
            with self.subTest(qid=qid):
                head = expert.request('HEAD', '/_api/v1/questions/%s' % qid)
                get = expert.get('/_api/v1/questions/%s' % qid)
                self.assertEqual(head.status, 404)
                self.assertEqual(get.status, 404)
                self.assertEqual(self.fingerprint(head)[0], self.fingerprint(get)[0])
        heads = {qid: self.fingerprint(expert.request('HEAD', '/_api/v1/questions/%s' % qid)) for qid in (QX, Q3, Q5, Q7)}
        for value in heads.values():
            self.assertEqual(value, heads[QX])

    def test_odd_ids_get_the_same_404_on_get_and_post(self):
        expert = self.who(EX)
        before = self.stored()
        odd = (Q1.upper(), Q1[:-1] + '%2f', Q1 + '%00', '%00', Q1[:-1] + '%2F')
        reference_get = self.fingerprint(expert.get('/_api/v1/questions/%s' % QX))
        reference_post = self.fingerprint(expert.post('/_api/v1/questions/%s/comment' % QX, {'cid': 'c-1', 'text': 'x'}))
        for qid in odd:
            with self.subTest(qid=qid):
                self.assertEqual(self.fingerprint(expert.get('/_api/v1/questions/%s' % qid)), reference_get)
                self.assertEqual(self.fingerprint(expert.post('/_api/v1/questions/%s/comment' % qid,
                                                              {'cid': 'c-1', 'text': 'x'})), reference_post)
                self.assertEqual(self.fingerprint(expert.post('/_api/v1/questions/%s/answer' % qid,
                                                              {'cid': 'c-1', 'text': 'x'}))[0], 404)
        self.assertEqual(self.stored(), before)

    def test_listing_with_a_trailing_slash(self):
        expert = self.who(EX)
        response = expert.get('/_api/v1/questions/')
        self.assertEqual(response.status, 404, response.text)
        self.assertNotIn(SECRET, response.text)

    def test_post_answer_and_comment_are_byte_identical_for_missing_foreign_and_withdrawn(self):
        expert = self.who(EX)
        before = self.stored()
        for kind, body in (('answer', {'cid': 'c-1', 'text': 'x'}), ('comment', {'cid': 'c-2', 'text': 'x'})):
            prints = {qid: self.fingerprint(expert.post('/_api/v1/questions/%s/%s' % (qid, kind), body))
                      for qid in (QX, Q3, Q5, Q7)}
            self.assertEqual(prints[QX][0], 404)
            for qid, value in prints.items():
                with self.subTest(kind=kind, qid=qid):
                    self.assertEqual(value, prints[QX])
        self.assertEqual(self.stored(), before)

    def test_invisible_with_bad_body_or_cid_is_still_the_same_404(self):
        expert = self.who(EX)
        before = self.stored()
        reference = {}
        for kind in ('answer', 'comment'):
            reference[kind] = self.fingerprint(expert.post('/_api/v1/questions/%s/%s' % (QX, kind), {'cid': 'c-1', 'text': 'x'}))
        bodies = (dict(raw=b'{nie json'), dict(raw=b'[]'), dict(raw=b''), dict(data={'text': 'x'}),
                  dict(data={'cid': 'zly cid!', 'text': 'x'}), dict(data={'cid': 'c-1', 'optionId': 1, 'text': 'x'}),
                  dict(data={'cid': 'c-1', 'bogus': True}))
        for kind in ('answer', 'comment'):
            for qid in (QX, Q3, Q5):
                for body in bodies:
                    with self.subTest(kind=kind, qid=qid, body=sorted(body)):
                        response = expert.post('/_api/v1/questions/%s/%s' % (qid, kind), **body)
                        self.assertEqual(self.fingerprint(response), reference[kind])
        self.assertEqual(self.stored(), before)
        # kontrola: widoczne pytanie z tą samą złą treścią to 400, nie 404
        self.check(expert.post('/_api/v1/questions/%s/comment' % Q1, raw=b'{nie json'), 400)


# ═════════════════════════════════════════════════════════════════════════════
# (e) kolejność kontroli POST
# ═════════════════════════════════════════════════════════════════════════════

class CheckOrderTests(QCase):
    def test_csrf_comes_before_everything_else(self):
        expert, commenter = self.who(EX), self.who(S.A)
        before = self.stored()
        for client, qid in ((expert, QX), (expert, Q3), (expert, Q1), (commenter, Q1)):
            for kind in ('answer', 'comment'):
                with self.subTest(login=client.login_name, qid=qid, kind=kind):
                    for headers in ({'X-Skillsync-CSRF': None}, {'X-Skillsync-CSRF': 'zly'}):
                        response = client.post('/_api/v1/questions/%s/%s' % (qid, kind), raw=b'{zepsute', headers=headers)
                        self.assert_no_write(before, response, 403, 'invalid_csrf')

    def test_wrong_content_type_and_foreign_origin_are_403(self):
        expert = self.who(EX)
        before = self.stored()
        for headers in ({'Content-Type': 'text/plain'}, {'Content-Type': 'application/x-www-form-urlencoded'},
                        {'Origin': 'http://evil.example'}, {'Origin': 'null'}):
            with self.subTest(headers=headers):
                self.assert_no_write(before, self.comment(expert, Q1, {'cid': 'c-1', 'text': 'x'}, headers=headers), 403)
        good = 'http://127.0.0.1:%d' % self.server.port
        self.check(self.comment(expert, Q1, {'cid': 'c-1', 'text': 'x'}, headers={'Origin': good}), 201)   # kontrola

    def test_role_comes_before_visibility_and_visibility_before_body(self):
        commenter, expert = self.who(S.A), self.who(EX)
        before = self.stored()
        self.assert_no_write(before, self.comment(commenter, QX, {'cid': 'c-1', 'text': 'x'}), 403, 'forbidden')
        self.assert_no_write(before, self.answer(commenter, Q3, {'cid': 'c-1', 'text': 'x'}), 403, 'forbidden')
        # niewidoczne + zła treść / zły cid = 404, nie 400
        for body in (dict(raw=b'{zepsute'), dict(data={'cid': 'zly cid!', 'text': 'x'}), dict(data={'text': 'x'}),
                     dict(data={'cid': 'c-1', 'optionId': 'a', 'text': 'b', 'confirm': True})):
            self.assert_no_write(before, expert.post('/_api/v1/questions/%s/answer' % Q3, **body), 404, 'not_found')
            self.assert_no_write(before, expert.post('/_api/v1/questions/%s/comment' % QX, **body), 404, 'not_found')

    def test_visible_closed_question_is_409_before_a_schema_400(self):
        expert = self.who(EX)
        before = self.stored()
        for body in ({'cid': 'c-1', 'optionId': 'o1', 'text': 'oba naraz'}, {'cid': 'c-2'},
                     {'cid': 'c-3', 'text': ' '}, {'cid': 'c-4', 'confirm': True, 'bogus': 1}):
            with self.subTest(body=body):
                self.assert_no_write(before, self.answer(expert, Q6, body), 409, 'already_answered')
        # kontrola: to samo ciało na otwartym pytaniu to 400
        self.check(self.answer(expert, Q1, {'cid': 'c-1', 'optionId': 'o1', 'text': 'oba naraz'}), 400)
        self.assertEqual(self.stored(), before)

    def test_visible_open_question_with_a_bad_body_is_400_and_writes_nothing(self):
        expert = self.who(EX)
        before = self.stored()
        for raw in (b'{zepsute', b'[]', b'null', b''):
            self.assert_no_write(before, expert.post('/_api/v1/questions/%s/answer' % Q1, raw=raw), 400)
        self.assert_no_write(before, self.answer(expert, Q1, {'text': 'bez cid'}), 400, 'cid_required')
        self.assert_no_write(before, self.answer(expert, Q1, {'cid': 'zly cid!', 'text': 'x'}), 400, 'cid_required')
        self.check(self.answer(expert, Q1, {'cid': 'c-ok', 'text': 'x'}), 201)   # kontrola


# ═════════════════════════════════════════════════════════════════════════════
# (f) odpowiedzi i komentarze (K11)
# ═════════════════════════════════════════════════════════════════════════════

class AnswerAndCommentTests(QCase):
    def test_invalid_answer_bodies_are_400_and_write_nothing(self):
        expert = self.who(EX)
        before = self.stored()
        cases = {
            'brak wariantu': {'cid': 'c-1'},
            'optionId i text': {'cid': 'c-2', 'optionId': 'o1', 'text': 'x'},
            'optionId i confirm': {'cid': 'c-3', 'optionId': 'o1', 'confirm': True},
            'text i confirm': {'cid': 'c-4', 'text': 'x', 'confirm': True},
            'nieznana opcja': {'cid': 'c-5', 'optionId': 'o9'},
            'confirm false': {'cid': 'c-6', 'confirm': False},
            'confirm nie-bool': {'cid': 'c-7', 'confirm': 'true'},
            'pusty tekst': {'cid': 'c-8', 'text': '   '},
            'tekst za dlugi': {'cid': 'c-9', 'text': 'x' * 4001},
            'komentarz za dlugi': {'cid': 'c-10', 'text': 'x', 'comment': 'y' * 2001},
            'nieznane pole': {'cid': 'c-11', 'text': 'x', 'bogus': 1},
        }
        for name, body in cases.items():
            with self.subTest(name):
                self.assert_no_write(before, self.answer(expert, Q1, body), 400)
        self.assertEqual(self.answer(expert, Q1, {'cid': 'c-12', 'optionId': 'o9'}).json['error'], 'unknown_option')

    def test_confirm_needs_an_assumed_value(self):
        expert = self.who(EX)
        before = self.stored()
        self.assert_no_write(before, self.answer(expert, Q2, {'cid': 'c-1', 'confirm': True}), 400)   # Q2: blokujące
        self.check(self.answer(expert, Q1, {'cid': 'c-2', 'confirm': True}), 201)                     # kontrola: Q1 ma assumed

    def test_boundary_lengths_are_accepted(self):
        expert = self.who(EX)
        self.check(self.answer(expert, Q1, {'cid': 'c-1', 'text': 'x' * 4000, 'comment': 'y' * 2000}), 201)
        self.check(self.comment(expert, Q1, {'cid': 'c-2', 'text': 'z' * 2000}), 201)
        self.check(self.comment(expert, Q1, {'cid': 'c-3', 'text': 'z' * 2001}), 400)

    def test_answer_variants_and_the_response_shape(self):
        for login, qid, body, key in ((EX, Q1, {'cid': 'c-1', 'confirm': True}, 'confirm'),
                                      (EX, Q8, {'cid': 'c-2', 'optionId': 'o2', 'comment': 'bo tak'}, 'optionId'),
                                      (EX2, Q2, {'cid': 'c-3', 'text': 'wlasna odpowiedz'}, 'text')):
            with self.subTest(key=key):
                response = self.check(self.answer(self.who(login), qid, body), 201)
                view = response.json['question']
                self.assertEqual((view['id'], view['state']), (qid, 'answered'))
                self.assertEqual(view['answer']['by'], login)
                self.assertIn(key, view['answer'])
                self.assertEqual(set(response.json['operation']), {'id', 'type', 'at', 'cid'})
                self.assertEqual(response.json['operation']['cid'], body['cid'])
                self.assertNotIn('addressees', view, 'widok eksperta nie ma adresatów')
                self.assertEqual([op['by'] for op in self.kinds(qid, 'answer')], [login])
        self.assertEqual(self.check(self.who(EX).get('/_api/v1/questions/%s' % Q8), 200).json['question']['answer']['optionId'], 'o2')

    def test_admin_view_of_the_comment_response_has_addressees(self):
        response = self.check(self.comment(self.who(S.D), Q3, {'cid': 'c-1', 'text': 'x'}), 201)
        self.assertEqual(response.json['question']['addressees'], [EX2])

    def test_first_answer_closes_repeat_is_idempotent_others_get_409(self):
        first, second = self.who(EX), self.who(EX2)
        body = {'cid': 'c-first', 'optionId': 'o1'}
        self.check(self.answer(first, Q2, body), 201)
        before = self.stored()
        repeat = self.check(self.answer(first, Q2, body), 200)
        self.assertEqual(repeat.json['question']['answer']['by'], EX)
        self.assertEqual(self.stored(), before, 'powtórzenie nie dopisuje operacji')
        self.assertEqual(len(self.kinds(Q2, 'answer')), 1)
        self.assert_no_write(before, self.answer(second, Q2, {'cid': 'c-other', 'optionId': 'o2'}), 409, 'already_answered')
        self.assert_no_write(before, self.answer(first, Q2, {'cid': 'c-new', 'optionId': 'o2'}), 409, 'already_answered')
        self.assert_no_write(before, self.answer(second, Q2, {'cid': 'c-first', 'optionId': 'o1'}), 409, 'already_answered')   # cid cudzy

    def test_the_same_cid_on_another_question_is_409_cid_conflict(self):
        expert = self.who(EX)
        self.check(self.answer(expert, Q1, {'cid': 'c-shared', 'text': 'x'}), 201)
        before = self.stored()
        self.assert_no_write(before, self.answer(expert, Q8, {'cid': 'c-shared', 'text': 'x'}), 409, 'cid_conflict')
        self.assert_no_write(before, self.comment(expert, Q8, {'cid': 'c-shared', 'text': 'x'}), 409, 'cid_conflict')
        # kontrola: inny cid na Q8 przechodzi, a ten sam cid u innego autora nie koliduje
        self.check(self.answer(expert, Q8, {'cid': 'c-other', 'text': 'x'}), 201)
        self.check(self.answer(self.who(EX2), Q2, {'cid': 'c-shared', 'text': 'x'}), 201)

    def test_comment_after_closure_is_201_and_idempotent(self):
        expert = self.who(EX)
        self.check(self.answer(expert, Q1, {'cid': 'c-1', 'text': 'x'}), 201)
        other = self.who(EX2)
        response = self.check(self.comment(other, Q1, {'cid': 'c-2', 'text': 'po zamknieciu'}), 201)
        self.assertEqual([(c['by'], c['text']) for c in response.json['question']['comments']], [(EX2, 'po zamknieciu')])
        before = self.stored()
        self.check(self.comment(other, Q1, {'cid': 'c-2', 'text': 'po zamknieciu'}), 200)
        self.assertEqual(self.stored(), before)
        self.assert_no_write(before, self.comment(other, Q1, {'cid': 'c-3', 'text': ''}), 400)
        self.assertEqual(self.check(expert.get('/_api/v1/questions/%s' % Q1), 200).json['question']['state'], 'answered')

    def test_comment_on_a_question_withdrawn_for_admin_is_a_known_outcome_not_a_server_error(self):
        response = self.comment(self.who(S.D), Q5, {'cid': 'c-1', 'text': 'x'})
        self.check(response, 201)

    def test_author_comes_from_the_session_not_from_the_body(self):
        """Nieznane/zastrzeżone pola ciała (`by`, `type`, `id`, `at`, `questionId`) nie zmieniają operacji:
        pierwszeństwo ma sesja i adres, operacja powstaje z `partial` (klucze ciała tylko dopełniają)."""
        expert = self.who(EX)
        body = {'cid': 'c-1', 'text': 'x', 'by': EX2, 'type': 'withdraw', 'id': 'op-x',
                'at': '2020-01-01T00:00:00.000Z', 'questionId': Q9}
        self.check(self.comment(expert, Q1, body), 201)
        written = self.kinds(Q1, 'comment')
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0]['by'], EX)
        self.assertNotEqual(written[0]['id'], 'op-x')
        self.assertTrue(written[0]['at'].startswith('2026-11-05'))
        self.assertEqual(self.kinds(Q9, 'comment'), [])
        self.assertEqual([op for op in self.operations_on_disk() if op['type'] == 'withdraw' and op['questionId'] != Q5], [])
        answer = dict(body, cid='c-2')
        self.check(self.answer(expert, Q8, {k: v for k, v in dict(answer, optionId='o1').items() if k != 'text'}), 201)
        self.assertEqual([op['by'] for op in self.kinds(Q8, 'answer')], [EX])

    def test_sixteen_parallel_answers_from_different_experts_exactly_one_wins(self):
        users = ps.UsersFile(self.users_path, iterations=S.ITER, clock=self.clock)
        logins = ['wyscig%02d@example.com' % number for number in range(16)]
        for login in logins:
            ps.add_user(users, login, roles=['expert'], password=PW)
        clients = []
        for login in logins:
            client = S.Client(self.server)
            self.assertEqual(client.login(login, PW).status, 200)
            clients.append(client)
        results = [None] * 16
        gate = threading.Barrier(16)

        def run(index):
            gate.wait(10)
            results[index] = self.answer(clients[index], Q9, {'cid': 'wyscig-%d' % index, 'text': 'odpowiedz %d' % index})

        threads = [threading.Thread(target=run, args=(index,)) for index in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        statuses = sorted(response.status for response in results)
        self.assertEqual(statuses, [201] + [409] * 15, [response.text for response in results])
        for response in results:
            if response.status == 409:
                self.assertEqual(response.json['error'], 'already_answered')
        winner = [index for index, response in enumerate(results) if response.status == 201][0]
        stored = self.kinds(Q9, 'answer')
        self.assertEqual([op['by'] for op in stored], [logins[winner]])
        document = ps.parse_questions_document(self.stored().decode('utf-8'))   # plik poprawny
        self.assertEqual(ps.fold_questions(document)['byId'][Q9]['answer']['by'], logins[winner])
        self.assertEqual(len({op['id'] for op in document}), len(document), 'identyfikatory operacji są unikalne')
        leftovers = [name for name in os.listdir(self.root) if name.endswith('.tmp')]
        self.assertEqual(leftovers, [])


# ═════════════════════════════════════════════════════════════════════════════
# (g) limit ciała
# ═════════════════════════════════════════════════════════════════════════════

class BodyLimitTests(QCase):
    URL = '/_api/v1/questions/%s/comment' % Q1

    def padded(self, size):
        payload = json.dumps({'cid': 'c-pad', 'text': 'x'}).encode('utf-8')
        return payload + b' ' * (size - len(payload))

    def test_over_64_kib_is_413_and_exactly_64_kib_is_accepted(self):
        expert = self.who(EX)
        before = self.stored()
        self.assert_no_write(before, expert.post(self.URL, raw=self.padded(64 * 1024 + 1)), 413, 'payload_too_large')
        self.check(expert.post(self.URL, raw=self.padded(64 * 1024)), 201)   # kontrola na granicy

    def test_the_body_is_not_judged_before_the_session_and_csrf(self):
        before = self.stored()
        huge = self.padded(64 * 1024 + 1)
        self.assert_no_write(before, self.anon.post(self.URL, raw=huge), 401)
        expert = self.who(EX)
        self.assert_no_write(before, expert.post(self.URL, raw=huge, headers={'X-Skillsync-CSRF': None}), 403, 'invalid_csrf')
        must = S.Client(self.server)
        self.check(must.login(MX, S.DEFAULT_PW), 200)
        self.assert_no_write(before, must.post(self.URL, raw=huge), 403, 'password_change_required')

    def test_commenter_with_a_huge_body_is_refused_and_nothing_is_written(self):
        before = self.stored()
        response = self.who(S.A).post(self.URL, raw=self.padded(64 * 1024 + 1))
        self.check(response, 413, 'payload_too_large')   # rozmiar przed rolą
        self.assertEqual(self.stored(), before)


# ═════════════════════════════════════════════════════════════════════════════
# (h) statyka
# ═════════════════════════════════════════════════════════════════════════════

STATIC_VARIANTS = ('/questions.json', '/QUESTIONS.JSON', '/Questions.Json', '/./questions.json', '/%71uestions.json',
                   '/questions.json/', '/questions.json.lock', '//questions.json', '/x/../questions.json',
                   '/demo/../questions.json', '/questions.json?x=1', '/questions.json%2f', '/%2e/questions.json')


class StaticDeniedTests(QCase):
    def setUp(self):
        super().setUp()
        self.assertIn(SECRET.encode(), self.stored())   # kontrola: plik istnieje i ma treść
        with open(self.qpath + '.lock', 'ab'):
            pass

    def assert_denied(self, client, path, anonymous_with_login_to_view=False):
        response = client.get(path)
        if anonymous_with_login_to_view:
            self.assertIn(response.status, (401, 302, 404), path)
        else:
            self.assertEqual(response.status, 404, '%s: %s' % (path, response.text[:200]))
        self.assertNotIn(SECRET, response.text, path)
        self.assertNotIn('questionsFormat', response.text, path)

    def test_questions_file_is_never_served_without_login_to_view(self):
        expert, developer = self.who(EX), self.who(S.D)
        for path in STATIC_VARIANTS:
            for client in (self.anon, expert, developer):
                with self.subTest(path=path, user=client.login_name):
                    self.assert_denied(client, path)
        for client in (self.anon, expert):   # HEAD też
            self.check(client.request('HEAD', '/questions.json'), 404)

    def test_questions_file_is_never_served_with_login_to_view(self):
        self.start_server(login_to_view=True)
        anon = self.anon
        expert, developer = S.Client(self.server), S.Client(self.server)
        self.check(expert.login(EX, PW), 200)
        self.check(developer.login(S.D, S.PASSWORDS[S.D]), 200)
        for path in STATIC_VARIANTS:
            with self.subTest(path=path, user='anon'):
                self.assert_denied(anon, path, anonymous_with_login_to_view=True)
            for client in (expert, developer):
                with self.subTest(path=path, user=client.login_name):
                    self.assert_denied(client, path)

    def test_slug_named_questions_and_questions_json_x_are_served(self):
        os.makedirs(os.path.join(self.root, 'questions'))
        S.write(os.path.join(self.root, 'questions', 'index.html'), 'QSLUG-INDEX')
        S.write(os.path.join(self.demo, 'questions.json-x'), 'QJX')
        self.assertIn('QSLUG-INDEX', self.check(self.anon.get('/questions/index.html'), 200).text)
        self.assertEqual(self.check(self.anon.get('/demo/questions.json-x'), 200).text, 'QJX')
        self.check(self.anon.get('/questions.json'), 404)

    def test_ordinary_files_still_work(self):
        author = self.who(S.A)
        self.seed(author, 'th-1', 'm-1')
        self.assertIn('DEMO-INDEX', self.check(self.anon.get('/demo/index.html'), 200).text)
        self.check(self.anon.get('/demo/prototype.json'), 200)
        served = self.check(self.anon.get('/demo/comments.json'), 200)
        self.assertEqual(served.body, self.journal())
        self.check(self.anon.get('/demo/questions.json'), 404)   # brak takiego pliku w prototypie (kontrola 404 zwykłe)
        self.start_server(login_to_view=True)
        client = S.Client(self.server)
        self.check(client.login(EX, PW), 200)
        self.check(client.get('/demo/index.html'), 200)
        self.check(client.get('/demo/comments.json'), 200)


# ═════════════════════════════════════════════════════════════════════════════
# (i) session features, (j) ostrzeżenie startowe
# ═════════════════════════════════════════════════════════════════════════════

class FeaturesAndWarningTests(QCase):
    def test_session_lists_the_questions_feature_and_the_server_version_is_unchanged(self):
        self.assertEqual(ps.SERVER_VERSION, '1')
        for client in (self.anon, self.who(EX)):
            server = self.check(client.get('/_api/v1/session'), 200).json['server']
            self.assertIn('questions', server['features'])
            self.assertIn('views', server['features'], 'istniejąca funkcja zostaje')
            self.assertEqual(server['version'], '1')
        self.assertIn('questions', ps.SERVER_FEATURES)

    def test_expert_account_without_login_to_view_warns_but_does_not_refuse(self):
        errors, warnings = ps.startup_checks(self.config())
        self.assertEqual(errors, [])
        self.assertIn(ps.EXPERT_PUBLIC_WARNING, warnings)
        self.assertIn('--login-to-view', ps.EXPERT_PUBLIC_WARNING)
        self.assertIn(ps.EXPERT_PUBLIC_WARNING, self.server.app.warnings)
        self.assertIn(ps.PUBLIC_READ_WARNING, warnings)

    def test_login_to_view_has_no_expert_warning(self):
        errors, warnings = ps.startup_checks(self.config(login_to_view=True))
        self.assertEqual(errors, [])
        self.assertNotIn(ps.EXPERT_PUBLIC_WARNING, warnings)
        self.start_server(login_to_view=True)
        self.assertNotIn(ps.EXPERT_PUBLIC_WARNING, self.server.app.warnings)

    def test_no_expert_accounts_no_expert_warning(self):
        os.unlink(self.users_path)
        users = ps.UsersFile(self.users_path, iterations=S.ITER, clock=self.clock)
        ps.add_user(users, S.A, role='commenter', password=S.PASSWORDS[S.A])
        ps.add_user(users, S.C, role='acceptor', password=S.PASSWORDS[S.C])
        _errors, warnings = ps.startup_checks(self.config())
        self.assertNotIn(ps.EXPERT_PUBLIC_WARNING, warnings)
        self.assertIn(ps.PUBLIC_READ_WARNING, warnings, 'kontrola: zwykłe ostrzeżenie zostaje')


# ═════════════════════════════════════════════════════════════════════════════
# (k) brak efektów ubocznych odczytu
# ═════════════════════════════════════════════════════════════════════════════

class ReadSideEffectsTests(QCase):
    def tree(self):
        found = []
        for current, dirs, files in os.walk(self.tmp):
            for name in dirs + files:
                found.append(os.path.relpath(os.path.join(current, name), self.tmp))
        return sorted(found)

    def test_get_does_not_create_root_or_any_file_when_root_is_missing(self):
        shutil.rmtree(self.root)
        before = self.tree()
        expert = self.who(EX)
        listing = self.check(expert.get('/_api/v1/questions'), 200).json
        self.assertEqual(listing['questions'], [])
        self.check(expert.get('/_api/v1/questions/%s' % Q1), 404, 'not_found')
        self.check(self.who(S.D).get('/_api/v1/questions'), 200)
        self.assertFalse(os.path.exists(self.root), 'GET nie tworzy katalogu root')
        self.assertEqual(self.tree(), before)

    def test_reads_leave_no_tmp_or_lock_files_and_do_not_touch_the_file(self):
        before_bytes = self.stored()
        before_tree = self.tree()
        expert = self.who(EX)
        for _ in range(3):
            expert.get('/_api/v1/questions')
            expert.get('/_api/v1/questions/%s' % Q1)
            expert.get('/_api/v1/questions/%s' % Q3)
        self.assertEqual(self.stored(), before_bytes)
        self.assertEqual(self.tree(), before_tree)

    def test_writes_leave_no_tmp_files(self):
        expert = self.who(EX)
        self.check(self.comment(expert, Q1, {'cid': 'c-1', 'text': 'x'}), 201)
        self.check(self.answer(expert, Q1, {'cid': 'c-2', 'text': 'x'}), 201)
        self.assertEqual([name for name in self.tree() if name.endswith('.tmp') or '.tmp' in name], [])
        self.assertEqual(sorted(name for name in os.listdir(self.root) if 'questions' in name and name != 'questions.json'
                                and not name.endswith('.lock')), [])
        self.check(expert.get('/_api/v1/questions'), 200)


# ═════════════════════════════════════════════════════════════════════════════
# (l) odpowiedzi: brak sekretów, nagłówki
# ═════════════════════════════════════════════════════════════════════════════

class ResponseHygieneTests(QCase):
    def test_no_password_hashes_or_foreign_addressees_in_any_expert_response(self):
        expert = self.who(EX)
        responses = [expert.get('/_api/v1/questions'), self.comment(expert, Q1, {'cid': 'c-1', 'text': 'x'}),
                     self.answer(expert, Q2, {'cid': 'c-2', 'optionId': 'o1'}),
                     expert.get('/_api/v1/questions/%s' % Q2), expert.get('/_api/v1/questions/%s' % Q3)]
        stored_hash = ps.UsersFile(self.users_path).get(EX)['password']
        for response in responses:
            text = response.text
            for forbidden in ('pbkdf2', PW, stored_hash, 'passwordChanged', '"password"', EX2, SECRET):
                self.assertNotIn(forbidden, text, forbidden)
        self.assertEqual(responses[-1].status, 404)
        # kontrola: administrator widzi adresatów (czyli wykrywacz potrafi znaleźć login w treści)
        self.assertIn(EX2, self.who(S.D).get('/_api/v1/questions').text)

    def test_security_headers_like_the_other_api_responses(self):
        expert, commenter = self.who(EX), self.who(S.A)
        for response in (expert.get('/_api/v1/questions'), expert.get('/_api/v1/questions/%s' % Q1),
                         expert.get('/_api/v1/questions/%s' % Q3), self.comment(expert, Q1, {'cid': 'c-1', 'text': 'x'}),
                         self.comment(expert, QX, {'cid': 'c-2', 'text': 'x'}), commenter.get('/_api/v1/questions'),
                         self.anon.get('/_api/v1/questions'), self.anon.get('/questions.json')):
            with self.subTest(status=response.status):
                self.assertEqual(response.header('X-Content-Type-Options'), 'nosniff')
                self.assertEqual(response.header('Referrer-Policy'), 'same-origin')
                self.assertIn("frame-ancestors 'self'", response.header('Content-Security-Policy'))
        for response in (expert.get('/_api/v1/questions'), expert.get('/_api/v1/questions/%s' % Q1),
                         expert.get('/_api/v1/questions/%s' % Q3)):
            self.assertEqual(response.header('Cache-Control'), 'no-store')
            self.assertIn('application/json', response.header('Content-Type'))


# ═════════════════════════════════════════════════════════════════════════════
# (m) regresja: komentarze i /project dla kont expert+acceptor, expert nie rozszerza dostępu
# ═════════════════════════════════════════════════════════════════════════════

class RegressionTests(QCase):
    def test_comment_operations_work_for_expert_accounts_as_before(self):
        for index, login in enumerate((EX, AE, DE)):
            client = self.who(login)
            response = client.op('demo', 'create', 'th-%d' % index, S.create_payload('m-%d' % index))
            self.check(response, 201)
            self.assertEqual(response.json['operation']['by'], login)
        self.check(self.who(EX).operations('demo'), 200)

    def test_project_listing_follows_the_privileged_role_only(self):
        self.check(self.who(AE).get('/_api/v1/project'), 200)
        self.check(self.who(DE).get('/_api/v1/project'), 200)
        self.check(self.who(EX).get('/_api/v1/project'), 403, 'forbidden')
        self.check(self.who(S.A).get('/_api/v1/project'), 403, 'forbidden')

    def test_expert_only_cannot_accept_or_restore(self):
        expert = self.who(EX)
        self.check(expert.post('/_api/v1/project/accept', {'cid': 'c-1', 'versions': {}}), 403, 'forbidden')
        response = expert.post('/_api/v1/prototypes/demo/restore', {'cid': 'c-2', 'screen': 's1', 'from': 1})
        self.check(response, 403, 'forbidden')
        self.check(expert.get('/_api/v1/prototypes/demo/restore-preview?screen=s1&from=1'), 403, 'forbidden')

    def test_question_routes_have_their_own_method_rules(self):
        expert = self.who(EX)
        self.check(expert.post('/_api/v1/questions', {'cid': 'c-1'}), 405)
        self.check(expert.get('/_api/v1/questions/%s/answer' % Q1), 405)
        self.check(expert.request('DELETE', '/_api/v1/questions/%s' % Q1), 405)
        self.check(expert.get('/_api/v1/questions'), 200)   # kontrola


# ═════════════════════════════════════════════════════════════════════════════
# (k) ochrona przed zalewem (K18, D21): limit zapisów, limit komentarzy, rezerwa pliku
# ═════════════════════════════════════════════════════════════════════════════

class FloodProtectionTests(QCase):
    def burst(self, client, count, qid=Q1, prefix='b'):
        return [self.comment(client, qid, {'cid': '%s-%d' % (prefix, n), 'text': 'x'}) for n in range(count)]

    def fill_file(self, target_bytes):
        """Dopisuje wprost (omijając progi) poprawne pytania, aż plik ma co najmniej `target_bytes`."""
        store = self.server.app.questions_store()
        operations = list(store.snapshot().operations)
        index = 0
        while len(ps.serialize_questions(operations).encode('utf-8')) < target_bytes:
            index += 1
            op = ask('qn-%012x' % (0x500 + index), refs=[{'title': 't', 'href': 'docs/a.md', 'excerpt': 'x' * 4000}] * 5)
            op.update(id='op-fill%05d' % index, at='2026-01-01T00:00:00.%03dZ' % (index % 900 + 1))
            operations.append(op)
        ps.atomic_write_bytes(self.qpath, ps.serialize_questions(operations).encode('utf-8'))

    def set_file_size(self, target):
        """Ustawia plik na dokładnie `target` bajtów (poprawne pytania; ostatnie dopełnia długość jednego excerpt)."""
        store = self.server.app.questions_store()
        operations = [op for op in store.snapshot().operations if not op['id'].startswith('op-pad')]   # poprzednie dopełnienie
        index = 0

        def size():
            return len(ps.serialize_questions(operations).encode('utf-8'))

        def make(excerpt):
            op = ask('qn-%012x' % (0x700 + index), refs=[{'title': 't', 'href': 'docs/a.md', 'excerpt': excerpt}])
            op.update(id='op-pad%05d' % index, at='2026-01-01T00:00:00.%03dZ' % (index % 900 + 1))
            return op

        while target - size() > 6000:
            index += 1
            operations.append(make('x' * 4000))
        index += 1
        operations.append(make(''))
        missing = target - size()
        self.assertTrue(0 <= missing <= 4000, missing)
        operations[-1] = make('x' * missing)
        data = ps.serialize_questions(operations).encode('utf-8')
        self.assertEqual(len(data), target)
        ps.atomic_write_bytes(self.qpath, data)

    # -- limit 60 zapisów / 60 s per login --

    def test_60th_write_ok_61st_is_429_with_retry_after_then_window_passes(self):
        expert = self.who(EX)
        for n in range(30):
            self.check(self.comment(expert, Q1, {'cid': 'a-%d' % n, 'text': 'x'}), 201)
        for n in range(30):
            self.check(self.comment(expert, Q4, {'cid': 'b-%d' % n, 'text': 'x'}), 201)
        before = self.stored()
        refused = self.check(self.comment(expert, Q8, {'cid': 'z-1', 'text': 'x'}), 429, 'rate_limited')
        self.assertTrue(1 <= refused.json['retryAfter'] <= 60, refused.json)
        self.assertEqual(refused.headers.get('Retry-After'), str(refused.json['retryAfter']))
        self.assertEqual(self.stored(), before)
        self.check(self.answer(expert, Q8, {'cid': 'z-2', 'optionId': 'o1'}), 429, 'rate_limited')
        self.clock.advance(61)
        self.check(self.comment(expert, Q8, {'cid': 'z-3', 'text': 'x'}), 201)

    def test_limit_is_per_login(self):
        first, second = self.who(EX), self.who(EX2)
        for response in self.burst(first, 60, QX, 'n'):
            self.check(response, 404)
        self.check(self.comment(first, QX, {'cid': 'n-61', 'text': 'x'}), 429, 'rate_limited')
        self.check(self.comment(second, Q1, {'cid': 'o-1', 'text': 'x'}), 201)

    def test_limit_does_not_depend_on_visibility_or_data(self):
        # każda próba po bramce roli się liczy: 404 (nieistniejące i niewidoczne), 409, 400 — i 429 jest ten sam
        admin = self.who(S.D)
        outcomes = []
        for n in range(60):
            qid = (QX, Q3, Q1)[n % 3]
            body = {'cid': 'v-%d' % n, 'text': 'x'} if n % 7 else {'cid': 'v-%d' % n}
            outcomes.append(self.comment(admin, qid, body).status)
        self.assertTrue({404, 400, 201} <= set(outcomes), outcomes)
        responses = [self.comment(admin, qid, {'cid': 'w-%d' % n, 'text': 'x'}) for n, qid in enumerate((QX, Q3, Q1, Q5))]
        for response in responses:
            self.check(response, 429, 'rate_limited')
        self.assertEqual(len({(r.status, r.json['error']) for r in responses}), 1)

    def test_blocked_and_unprivileged_requests_are_not_counted(self):
        expert = self.who(EX)
        commenter = self.who(S.A)
        for n in range(70):
            self.check(self.answer(commenter, Q1, {'cid': 'r-%d' % n, 'text': 'x'}), 403, 'forbidden')   # rola przed limitem
        self.check(self.comment(expert, Q1, {'cid': 'r-ok', 'text': 'x'}), 201)

    # -- 50 komentarzy na pytanie --

    def test_50th_comment_ok_51st_is_409_comment_limit(self):
        expert, admin = self.who(EX), self.who(S.D)
        for n in range(25):
            self.check(self.comment(expert, Q1, {'cid': 'k-%d' % n, 'text': 'x'}), 201)
        for n in range(25):
            self.check(self.comment(admin, Q1, {'cid': 'k-%d' % n, 'text': 'x'}), 201)
        before = self.stored()
        self.assert_no_write(before, self.comment(expert, Q1, {'cid': 'k-last', 'text': 'x'}), 409, 'comment_limit')
        self.assert_no_write(before, self.comment(admin, Q1, {'cid': 'k-last2', 'text': 'x'}), 409, 'comment_limit')
        self.check(self.comment(expert, Q4, {'cid': 'k-other', 'text': 'x'}), 201)          # inne pytanie niezależne
        self.check(self.comment(expert, Q1, {'cid': 'k-0', 'text': 'x'}), 200)              # powtórzenie to nie nowy komentarz
        self.check(self.answer(expert, Q1, {'cid': 'k-ans', 'text': 'odpowiedź'}), 201)     # odpowiedź nadal wchodzi

    def test_late_answers_count_as_comments(self):
        partials = [{'type': 'answer', 'questionId': Q1, 'by': EX3, 'cid': 'first', 'text': 'pierwsza'}]
        partials += [{'type': 'comment', 'questionId': Q1, 'by': 'agent', 'cid': 'agc-%d' % n, 'text': 'x'} for n in range(24)]
        self.seed_questions(partials)
        # późne odpowiedzi (np. po scaleniu) magazyn przez API odrzuca, więc dopisujemy je wprost do pliku
        operations = list(self.server.app.questions_store().snapshot().operations)
        for n in range(25):
            operations.append({'id': 'op-late%03d' % n, 'type': 'answer', 'questionId': Q1, 'at': '2026-12-01T00:00:00.%03dZ' % (n + 1),
                               'by': EX2, 'cid': 'late-%d' % n, 'text': 'x'})
        ps.atomic_write_bytes(self.qpath, ps.serialize_questions(operations).encode('utf-8'))
        self.assertEqual(len(self.server.app.questions_store().snapshot().fold['byId'][Q1]['comments']), 49)
        expert = self.who(EX)
        self.check(self.comment(expert, Q1, {'cid': 'c-50', 'text': 'x'}), 201)
        self.check(self.comment(expert, Q1, {'cid': 'c-51', 'text': 'x'}), 409, 'comment_limit')

    # -- rezerwa pliku --

    def test_comment_507_above_896_kib_answer_still_201(self):
        self.fill_file(897 * 1024)
        expert = self.who(EX)
        before = self.stored()
        self.assert_no_write(before, self.comment(expert, Q1, {'cid': 'f-1', 'text': 'x'}), 507, 'file_too_large')
        self.check(self.answer(expert, Q1, {'cid': 'f-2', 'text': 'odpowiedź w rezerwie'}), 201)
        self.assertLessEqual(len(self.stored()), 1024 * 1024)

    def test_comment_ok_just_below_the_threshold(self):
        self.fill_file(860 * 1024)
        self.assertLess(len(self.stored()), 896 * 1024)
        self.check(self.comment(self.who(EX), Q1, {'cid': 'f-1', 'text': 'x'}), 201)

    def test_answer_507_only_above_1_mib(self):
        self.fill_file(1024 * 1024 + 1)
        before = self.stored()
        self.assert_no_write(before, self.answer(self.who(EX), Q1, {'cid': 'f-1', 'text': 'x'}), 507, 'file_too_large')

    def test_new_session_of_the_same_login_does_not_reset_the_limit(self):
        first = self.who(EX)
        for n in range(60):
            self.check(self.comment(first, Q1 if n < 40 else Q4, {'cid': 's-%d' % n, 'text': 'x'}), 201)
        second = self.who(EX)   # świeża sesja tego samego loginu
        self.check(self.comment(second, Q8, {'cid': 's-new', 'text': 'x'}), 429, 'rate_limited')
        self.check(self.who(EX2).post('/_api/v1/questions/%s/comment' % Q8, {'cid': 's-other', 'text': 'x'}), 201)

    def test_parallel_comments_at_the_limit_exactly_one_wins(self):
        self.seed_questions([{'type': 'comment', 'questionId': Q1, 'by': 'agent', 'cid': 'seed-%d' % n, 'text': 'x'}
                             for n in range(49)])
        clients = [self.who(EX) for _ in range(8)]   # jeden login, osobne sesje; 8 zapisów < 60/60 s
        results = [None] * 8
        gate = threading.Barrier(8)

        def run(index):
            gate.wait(10)
            results[index] = self.comment(clients[index], Q1, {'cid': 'par-%d' % index, 'text': 'x'})

        threads = [threading.Thread(target=run, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(sorted(r.status for r in results), [201] + [409] * 7, [r.text for r in results])
        for response in results:
            if response.status == 409:
                self.assertEqual(response.json['error'], 'comment_limit')
        operations = self.operations_on_disk()   # plik poprawny (parsuje się) i dokładnie 50 komentarzy
        self.assertEqual(len([op for op in operations if op['questionId'] == Q1 and op['type'] == 'comment']), 50)
        self.assertEqual(len(self.server.app.questions_store().snapshot().fold['byId'][Q1]['comments']), 50)

    def test_replay_of_a_stored_comment_is_200_even_when_the_file_is_over_the_threshold(self):
        expert = self.who(EX)
        self.check(self.comment(expert, Q1, {'cid': 'd-1', 'text': 'x'}), 201)
        self.fill_file(900 * 1024)
        before = self.stored()
        self.assertGreater(len(before), 896 * 1024)
        self.assert_no_write(before, self.comment(expert, Q1, {'cid': 'd-1', 'text': 'x'}), 200)
        self.assert_no_write(before, self.comment(expert, Q1, {'cid': 'd-2', 'text': 'x'}), 507, 'file_too_large')

    def test_comment_threshold_boundary_is_exact(self):
        expert = self.who(EX)
        self.set_file_size(896 * 1024)
        self.check(self.comment(expert, Q1, {'cid': 'e-1', 'text': 'x'}), 201)
        self.set_file_size(896 * 1024 + 1)
        before = self.stored()
        self.assert_no_write(before, self.comment(expert, Q1, {'cid': 'e-2', 'text': 'x'}), 507, 'file_too_large')

    def test_answer_boundary_at_1_mib(self):
        expert = self.who(EX)
        self.set_file_size(1024 * 1024 - 1)
        self.check(self.answer(expert, Q1, {'cid': 'g-1', 'text': 'x'}), 201)
        self.set_file_size(1024 * 1024)
        self.check(self.answer(expert, Q4, {'cid': 'g-2', 'text': 'x'}), 201)
        self.set_file_size(1024 * 1024 + 1)
        before = self.stored()
        self.assert_no_write(before, self.answer(expert, Q8, {'cid': 'g-3', 'text': 'x'}), 507, 'file_too_large')

    def test_ask_above_768_kib_is_refused_by_the_store(self):
        self.fill_file(769 * 1024)
        partial = ask('qn-0000000000aa', cid='ask-aa')
        with self.assertRaises(ps.PrototypeServerError) as caught:
            self.server.app.questions_store().append([partial])
        self.assertEqual((caught.exception.status, caught.exception.code), (507, 'file_too_large'))
        self.check(self.answer(self.who(EX), Q1, {'cid': 'f-1', 'text': 'x'}), 201)


QM = 'qn-0000000000a1'   # all, multi


class MultiChoiceApiTests(QCase):
    """Pytanie wielokrotnego wyboru: formularz wysyła `optionIds`, pusty zestaw i zły rodzaj odpowiedzi to 400 z kodem."""

    def setUp(self):
        super().setUp()
        options = [{'id': 'o1', 'label': 'CSV', 'recommended': True}, {'id': 'o2', 'label': 'XLSX', 'recommended': True},
                   {'id': 'o3', 'label': 'PDF'}]
        self.seed_questions([ask(QM, multi=True, options=options, text='Formaty eksportu?')])

    def test_view_shows_multi_and_the_answer_keeps_every_chosen_option(self):
        expert = self.who(EX)
        listing = expert.get('/_api/v1/questions').json['questions']
        self.assertTrue([q for q in listing if q['id'] == QM][0]['multi'])
        self.assertFalse([q for q in listing if q['id'] == Q8][0]['multi'])
        response = self.answer(expert, QM, {'cid': 'c-1', 'optionIds': ['o3', 'o1']})
        self.check(response, 201)
        stored = self.kinds(QM, 'answer')
        self.assertEqual(stored[0]['optionIds'], ['o3', 'o1'])
        self.assertNotIn('optionId', stored[0])
        self.assertEqual(response.json['question']['answer']['optionIds'], ['o3', 'o1'])

    def test_empty_selection_is_400_with_its_code_and_writes_nothing(self):
        expert = self.who(EX)
        before = self.stored()
        response = self.answer(expert, QM, {'cid': 'c-1', 'optionIds': []})
        self.assert_no_write(before, response, 400)
        self.assertEqual(response.json['error'], 'empty_selection')

    def test_wrong_kind_unknown_and_duplicate_options_are_400(self):
        expert = self.who(EX)
        before = self.stored()
        cases = {
            'optionId na multi': ({'cid': 'c-1', 'optionId': 'o1'}, QM, 'wrong_answer_kind'),
            'optionIds na pojedynczym': ({'cid': 'c-2', 'optionIds': ['o1']}, Q8, 'wrong_answer_kind'),
            'nieznana opcja': ({'cid': 'c-3', 'optionIds': ['o1', 'o9']}, QM, 'unknown_option'),
            'duplikat': ({'cid': 'c-4', 'optionIds': ['o1', 'o1']}, QM, None),
            'nie lista': ({'cid': 'c-5', 'optionIds': 'o1'}, QM, None),
            'zestaw i tekst': ({'cid': 'c-6', 'optionIds': ['o1'], 'text': 'x'}, QM, None),
        }
        for name, (body, qid, code) in cases.items():
            with self.subTest(name):
                response = self.answer(expert, qid, body)
                self.assert_no_write(before, response, 400)
                if code:
                    self.assertEqual(response.json['error'], code)

    def test_free_text_and_comment_stay_available_on_a_multi_question(self):
        expert = self.who(EX)
        self.check(self.answer(expert, QM, {'cid': 'c-1', 'text': 'Żaden: JSON', 'comment': 'potrzebny też JSON'}), 201)
        self.check(self.comment(expert, QM, {'cid': 'c-2', 'text': 'dopisek'}), 201)

    def test_retry_of_the_same_multi_answer_is_idempotent(self):
        expert = self.who(EX)
        body = {'cid': 'c-1', 'optionIds': ['o2']}
        self.check(self.answer(expert, QM, body), 201)
        before = self.stored()
        self.check(self.answer(expert, QM, body), 200)
        self.assertEqual(self.stored(), before)


if __name__ == '__main__':
    unittest.main()
