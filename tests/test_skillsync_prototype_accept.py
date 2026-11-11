"""Akceptacja prototypu (F3): fold, API `accept`/`unaccept`, `GET /project`, `POST /project/accept`, CLI `accept`
(TTY), `list`/`index` oraz bramka `gate` (K26).

Kontrakt: docs/specs/2026-11-04-prototype-komentarze-wspolne.md (K14, K16, K20, K24-K26, K32, K42, K43) i
.skillsync/runs/2026-11-04-prototype-komentarze.karta-2/F3-kontrakt.md (A, A1, B-E, G).  Testy obserwują odpowiedzi
HTTP, pliki `comments.json` i raporty CLI.  Serwer działa na porcie 0, użytkownicy mają niską liczbę iteracji PBKDF2.
Każdy test negatywny ma przypadek kontrolny, który przechodzi (to samo żądanie z poprawnymi danymi).  Różnicowy fold
Node<->Python z `accept`/`unaccept` jest w `tests/test_skillsync_prototype_engine.py`.
"""
import errno
import io
import json
import os
import re
import select
import shutil
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype_server as ps  # noqa: E402
from tests.test_skillsync_prototype import CLI, PrototypeFixture  # noqa: E402
from tests.test_skillsync_prototype_server import setUpModule, tearDownModule  # noqa: E402,F401  (strażnik katalogu tymczasowego)
from tests.test_skillsync_prototype_server import (  # noqa: E402
    A, ACC2, C, D, DEFAULT_PW, ITER, PASSWORDS, R_HEAD, R_S1, R_S2_V1, R_S2_V2, R_S3, R_S3_V2, R_TAIL,
    Client, RestoreServerCase, ServerCase, anchor, assert_canonical_text, create_payload, element_anchor,
    file_operations, op_lines, r_page, read_file_bytes, uid, write, write_journal)

POSIX = os.name != 'nt'
C2 = 'celina.druga@example.com'      # drugi akceptujący
PASSWORDS = dict(PASSWORDS, **{C2: 'Haslo-Drugiej-1234'})
HASH = 'a' * 64


def page_for(slug, s2=R_S2_V1, s3=R_S3):
    return r_page(s2, s3).replace('data-prototype-id="demo"', 'data-prototype-id="%s"' % slug)


def op(number, kind, payload, by='anna@example.com', at=None):
    return {'id': 'op-%02d' % number, 'type': kind, 'at': at or '2026-11-05T10:00:%02d.000Z' % number, 'by': by,
            'payload': payload}


def acc_payload(screens, **extra):
    return dict({'scope': 'screen', 'screens': screens, 'iteration': 1, 'via': 'ui'}, **extra)


# ═════════════════════════════════════════════════════════════════════════════
# 1. Fold akceptacji w Pythonie (kontrakt A i A1)
# ═════════════════════════════════════════════════════════════════════════════

class FoldAcceptanceTests(unittest.TestCase):
    def acceptance(self, *operations):
        return ps.fold_operations(list(operations))['acceptance']

    def test_no_operations_means_empty_acceptance(self):
        self.assertEqual(self.acceptance(), {})
        self.assertEqual(ps.fold_operations([])['threads'], [], 'accept is a global operation, not a thread')

    def test_last_operation_per_screen_wins_and_unaccept_removes(self):
        result = self.acceptance(
            op(1, 'accept', acc_payload({'s1': 'h1', 's2': 'h2'})),
            op(2, 'unaccept', {'screens': ['s1']}),
            op(3, 'accept', acc_payload({'s3': 'h3'}), by='jan@example.com'),
            op(4, 'accept', acc_payload({'s2': 'h2b'}, iteration=2), by='jan@example.com'),
            op(5, 'unaccept', {'screens': ['s3', 'nieistnieje']}),
            op(6, 'accept', acc_payload({'s1': 'h1c'})))
        self.assertEqual(list(result), ['s1', 's2'])
        self.assertEqual((result['s1']['hash'], result['s1']['opId']), ('h1c', 'op-06'), 're-accepted after unaccept')
        self.assertEqual((result['s2']['hash'], result['s2']['by'], result['s2']['iteration']), ('h2b', 'jan@example.com', 2))
        # kontrola: ten sam dziennik bez operacji `unaccept` zachowuje ekran s3 i starszą akceptację s1
        kept = self.acceptance(op(1, 'accept', acc_payload({'s1': 'h1'})), op(3, 'accept', acc_payload({'s3': 'h3'})))
        self.assertEqual(list(kept), ['s1', 's3'])

    def test_order_is_by_at_then_id_not_by_array_position(self):
        accept = op(1, 'accept', acc_payload({'s1': 'h'}))
        unaccept = op(2, 'unaccept', {'screens': ['s1']})
        self.assertEqual(self.acceptance(unaccept, accept), {}, 'unaccept is later in time')
        later_accept = dict(accept, at='2026-11-05T10:00:09.000Z')
        self.assertEqual(list(self.acceptance(later_accept, unaccept)), ['s1'], 'control: accept is later in time')

    def test_who_is_the_login_for_ui_and_the_payload_who_for_cli(self):
        ui = self.acceptance(op(1, 'accept', acc_payload({'s1': 'h'}, who='ktos@example.com'), by='anna@example.com'))['s1']
        self.assertEqual((ui['who'], ui['by'], ui['via']), ('anna@example.com', 'anna@example.com', 'ui'), 'ui ignores payload.who')
        cli = self.acceptance(op(1, 'accept', acc_payload({'s1': 'h'}, via='cli', who='celina@example.com'), by='agent'))['s1']
        self.assertEqual((cli['who'], cli['by'], cli['via']), ('celina@example.com', 'agent', 'cli'))
        self.assertEqual(set(cli), {'hash', 'by', 'who', 'at', 'iteration', 'via', 'scope', 'opId'})
        for bad in ('', None, 5, ['x']):
            entry = self.acceptance(op(1, 'accept', acc_payload({'s1': 'h'}, via='cli', who=bad), by='agent'))['s1']
            self.assertEqual(entry['who'], 'agent', 'cli without a usable who falls back to by: %r' % (bad,))

    def test_a1_malformed_accept_is_skipped_or_normalised(self):
        good = acc_payload({'s1': 'h'})
        skipped = {'screens is a list': dict(good, screens=['s1']), 'screens is null': dict(good, screens=None),
                   'screens is a string': dict(good, screens='s1'), 'empty hash': dict(good, screens={'s1': ''}),
                   'hash is a number': dict(good, screens={'s1': 5}), 'hash is a list': dict(good, screens={'s1': ['h']})}
        for name, payload in skipped.items():
            with self.subTest(name):
                self.assertEqual(self.acceptance(op(1, 'accept', payload)), {})
        self.assertEqual(list(self.acceptance(op(1, 'accept', good))), ['s1'], 'control: well-formed payload counts')
        # jedna zła wartość nie unieważnia całej operacji: poprawne ekrany zostają
        mixed = self.acceptance(op(1, 'accept', dict(good, screens={'s1': 'h', 's2': '', 's3': 7, 's4': 'k'})))
        self.assertEqual(list(mixed), ['s1', 's4'])
        iterations = [(2, 2), (2.0, 2), (2.5, None), (True, None), (False, None), ('2', None), (None, None), (-1, -1), (0, 0)]
        for value, expected in iterations:
            with self.subTest(iteration=value):
                entry = self.acceptance(op(1, 'accept', dict(good, iteration=value)))['s1']
                self.assertEqual(entry['iteration'], expected)
                self.assertIsNot(entry['iteration'], True)
                if expected is not None:
                    self.assertIs(type(entry['iteration']), int, 'a whole float becomes an int')
        for key, value, expected in (('via', 5, None), ('scope', 5, None), ('via', 'x', 'x'), ('scope', 'all', 'all')):
            entry = self.acceptance(op(1, 'accept', dict(good, **{key: value})))['s1']
            self.assertEqual(entry[key], expected)
        self.assertEqual(self.acceptance(op(1, 'accept', 'tekst')), {}, 'payload that is not an object')

    def test_a1_malformed_unaccept_removes_nothing(self):
        accepted = op(1, 'accept', acc_payload({'s1': 'h', 's2': 'h'}))
        for name, screens in {'object': {'s1': 'h'}, 'null': None, 'string': 's1', 'number': 3}.items():
            with self.subTest(name):
                self.assertEqual(list(self.acceptance(accepted, op(2, 'unaccept', {'screens': screens}))), ['s1', 's2'])
        junk = self.acceptance(accepted, op(2, 'unaccept', {'screens': ['', None, 5, ['s1'], 's2']}))
        self.assertEqual(list(junk), ['s1'], 'only non-empty strings count')
        self.assertEqual(self.acceptance(accepted, op(2, 'unaccept', {'screens': ['s1', 's2']})), {}, 'control')

    def test_keys_are_sorted_in_utf16_order(self):
        result = self.acceptance(op(1, 'accept', acc_payload({'s2': 'h', 's10': 'h', 'a': 'h', 'ż': 'h', '__proto__': 'h'})))
        self.assertEqual(list(result), sorted(result))
        self.assertIn('__proto__', result)
        self.assertIsNone(ps.fold_operations([])['acceptance'].get('__proto__'))

    def test_state_valid_means_the_hash_equals_the_current_one(self):
        fold = ps.fold_operations([op(1, 'accept', acc_payload({'s1': 'old', 's2': 'cur', 's9': 'gone'}), by='anna@example.com'),
                                   op(2, 'accept', acc_payload({'s3': 'cur3'}, via='cli', who='c@example.com', iteration=3), by='agent')])
        state = ps.acceptance_state(fold, {'s1': 'new', 's2': 'cur', 's3': 'cur3', 's4': 'nobody'})
        self.assertEqual(state['s1'], {'valid': False, 'who': 'anna@example.com', 'at': '2026-11-05T10:00:01.000Z', 'iteration': 1, 'via': 'ui'})
        self.assertTrue(state['s2']['valid'])
        self.assertEqual((state['s3']['valid'], state['s3']['who'], state['s3']['via'], state['s3']['iteration']),
                         (True, 'c@example.com', 'cli', 3))
        self.assertNotIn('s9', state, 'screens missing from the current list (removed) are left out')
        self.assertNotIn('s4', state, 'a screen without an entry in the fold is left out')
        self.assertEqual(ps.acceptance_state(ps.fold_operations([]), {'s1': 'x'}), {})


# ═════════════════════════════════════════════════════════════════════════════
# 4. API POST /operations: accept i unaccept
# ═════════════════════════════════════════════════════════════════════════════

class AcceptCase(ServerCase):
    """`demo` ma ekrany s1 (przykład), s2, s3 i dziennik v2; klientów: com (komentujący), acc, acc2 (akceptujący), dev."""

    def setUp(self):
        super().setUp()
        write(os.path.join(self.demo, 'index.html'), page_for('demo'))
        write(os.path.join(self.demo, 'screens.css'), 'body { margin: 0 }\n')
        write(os.path.join(self.demo, 'README.md'), 'Opis\n')
        write_journal(self.demo, 'demo', [])
        users = ps.UsersFile(self.users_path, iterations=ITER, clock=self.clock)
        ps.add_user(users, C2, role='acceptor', password=PASSWORDS[C2])
        ps.add_user(users, ACC2, role='acceptor', default_password=DEFAULT_PW)
        self.com, self.acc, self.acc2, self.dev = self.client(A), self.client(C), self.client(C2), self.client(D)

    def client(self, login=None, password=None, ip=None):
        return super().client(login, password or PASSWORDS.get(login), ip)

    def listing(self, slug='demo'):
        return self.check(self.anon.operations(slug), 200).json

    def screen_payload(self, screen='s2', slug='demo', **extra):
        listing = self.listing(slug)
        payload = {'scope': 'screen', 'screens': {screen: listing['screens'][screen]}, 'version': listing['version']}
        payload.update(extra)
        return payload

    def accept(self, client, screen='s2', slug='demo', cid=None, **extra):
        return client.op(slug, 'accept', None, self.screen_payload(screen, slug, **extra), cid=cid)

    def unaccept(self, client, *screens, slug='demo', cid=None):
        return client.op(slug, 'unaccept', None, {'screens': list(screens)}, cid=cid)

    def acceptance(self, slug='demo'):
        return self.listing(slug)['acceptance']

    def count(self):
        return len(file_operations(self.demo))

    def index_text(self):
        return Path(self.demo, 'index.html').read_text(encoding='utf-8')


class AcceptRoleAndStoredPayloadTests(AcceptCase):
    def test_role_matrix_for_accept(self):
        before = self.journal()
        self.check(self.accept(self.com), 403, 'forbidden')
        self.assertEqual(self.journal(), before, 'commenter writes nothing')
        self.check(self.accept(self.acc), 201)     # control: the same request from an acceptor
        self.check(self.accept(self.dev, 's3'), 201)
        self.assertEqual(set(self.acceptance()), {'s2', 's3'})

    def test_role_matrix_for_unaccept(self):
        self.check(self.accept(self.acc), 201)
        before = self.journal()
        self.check(self.unaccept(self.com, 's2'), 403, 'forbidden')
        self.assertEqual(self.journal(), before)
        self.check(self.unaccept(self.acc, 's2'), 201)  # control: own acceptance by an acceptor
        self.check(self.accept(self.acc), 201)
        self.check(self.unaccept(self.dev, 's2'), 201)  # developer may undo any
        self.assertEqual(self.acceptance(), {})

    def test_session_csrf_and_origin_are_required(self):
        body = {'cid': uid('c'), 'type': 'accept', 'payload': self.screen_payload()}
        self.check(self.anon.post('/_api/v1/prototypes/demo/operations', body), 401)
        before = self.journal()
        self.check(self.acc.post('/_api/v1/prototypes/demo/operations', body, headers={'X-Skillsync-CSRF': None}), 403)
        self.check(self.acc.post('/_api/v1/prototypes/demo/operations', body, headers={'Origin': 'http://evil.example'}), 403)
        self.assertEqual(self.journal(), before)
        self.check(self.acc.post('/_api/v1/prototypes/demo/operations', body), 201)  # control

    def test_stored_payload_has_via_ui_current_iteration_and_no_version(self):
        response = self.check(self.accept(self.acc), 201).json
        stored = response['operation']
        self.assertEqual((stored['type'], stored['by']), ('accept', C))
        self.assertNotIn('threadId', stored)
        self.assertEqual(stored['payload'], {'scope': 'screen', 'screens': {'s2': self.listing()['screens']['s2']},
                                             'iteration': 1, 'via': 'ui'})
        self.assertNotIn('version', stored['payload'])
        self.assertNotIn('who', stored['payload'])
        assert_canonical_text(self, self.journal())
        entry = self.acceptance()['s2']
        self.assertEqual((entry['who'], entry['via'], entry['iteration'], entry['valid']), (C, 'ui', 1, True))

    def test_iteration_follows_the_current_iteration(self):
        ps.perform_iterate(ps.CommentsStore(self.demo, 'demo', clock=self.clock), self.demo)
        self.assertEqual(self.listing()['iteration'], {'current': 2})
        stored = self.check(self.accept(self.acc), 201).json['operation']
        self.assertEqual(stored['payload']['iteration'], 2)
        self.assertEqual(self.acceptance()['s2']['iteration'], 2)

    def test_repeated_cid_is_200_and_writes_nothing(self):
        cid = uid('rep')
        first = self.check(self.accept(self.acc, cid=cid), 201).json
        after_first = self.journal()
        again = self.check(self.accept(self.acc, cid=cid), 200).json
        self.assertEqual(again['operation']['id'], first['operation']['id'])
        self.assertEqual(self.journal(), after_first)
        self.check(self.accept(self.acc, cid=uid('rep')), 201)  # control: a new cid is a new operation
        self.assertEqual(self.count(), 2)
        cid2 = uid('un')
        self.check(self.unaccept(self.acc, 's2', cid=cid2), 201)
        self.check(self.unaccept(self.acc, 's2', cid=cid2), 200)  # unaccept is idempotent by cid, not 409 not_accepted
        self.assertEqual(self.count(), 3)

    def test_must_change_is_403_password_change_required(self):
        must = Client(self.server)
        self.check(must.login(ACC2, DEFAULT_PW), 200)
        before = self.journal()
        self.check(self.accept(must), 403, 'password_change_required')
        self.check(self.unaccept(must, 's2'), 403, 'password_change_required')
        self.assertEqual(self.journal(), before)
        self.check(self.accept(self.acc), 201)  # control: the same payload from an account that may write

    def test_accept_and_unaccept_are_browser_operations_not_cli_only(self):
        """Typy `accept`/`unaccept` należą do operacji przeglądarki (kontrakt B). Że nigdy nie trafiają do kolejki offline (K16),
        sprawdza silnik: `EngineV2AcceptancePureTests.test_accept_types_never_queueable`."""
        self.assertIn('accept', ps.BROWSER_TYPES)
        self.assertIn('unaccept', ps.BROWSER_TYPES)
        for kind in ('triage', 'decision', 'iteration', 'restore'):
            self.assertNotIn(kind, ps.BROWSER_TYPES, 'control: the CLI-only types stay out of the list')
        self.check(self.accept(self.acc), 201)  # and the server really takes one from a browser session


class AcceptRefusalTests(AcceptCase):
    def post_accept(self, payload, client=None):
        return (client or self.acc).op('demo', 'accept', None, payload)

    def assert_refused(self, payload, status, error):
        before = self.journal()
        response = self.post_accept(payload)
        self.check(response, status, error)
        self.assertEqual(self.journal(), before, 'no write on a refusal')

    def test_stale_hash_is_409_and_the_same_hash_after_reload_is_201(self):
        payload = self.screen_payload()
        self.assert_refused(dict(payload, screens={'s2': 'f' * 64}), 409, 'stale_view')
        self.check(self.post_accept(payload), 201)  # control: the current hash

    def test_stale_version_is_409_and_the_current_version_is_201(self):
        payload = self.screen_payload()
        self.assert_refused(dict(payload, version='f' * 64), 409, 'stale_view')
        self.check(self.post_accept(payload), 201)

    def test_real_change_of_the_file_between_load_and_post_is_stale_view(self):
        payload = self.screen_payload()  # as loaded by the page
        write(os.path.join(self.demo, 'index.html'), self.index_text().replace('Tydzień', 'Tydzień zmieniony'))
        self.assert_refused(payload, 409, 'stale_view')
        self.check(self.post_accept(self.screen_payload()), 201)  # reloaded page: control

    def test_change_of_another_screen_alone_still_changes_the_version(self):
        payload = self.screen_payload('s2')
        write(os.path.join(self.demo, 'index.html'), self.index_text().replace('Inny', 'Inny zmieniony'))
        self.assertEqual(self.listing()['screens']['s2'], payload['screens']['s2'], 'the hash of s2 is unchanged')
        self.assert_refused(payload, 409, 'stale_view')  # but the version moved: the page is outdated
        self.check(self.post_accept(self.screen_payload('s2')), 201)

    def test_unknown_screen_including_the_example_is_400(self):
        base = self.screen_payload()
        for screen in ('s9', 's1'):  # s1 is the example screen (data-prototype-example)
            with self.subTest(screen=screen):
                self.assert_refused(dict(base, screens={screen: HASH}), 400, 'unknown_screen')
        self.check(self.post_accept(base), 201)

    def test_unknown_screen_message_names_the_ambiguity(self):
        write(os.path.join(self.demo, 'index.html'), page_for('demo', R_S2_V1 + R_S2_V1, R_S3))
        listing = self.listing()
        payload = {'scope': 'screen', 'screens': {'s2': HASH}, 'version': listing['version']}
        response = self.check(self.post_accept(payload), 400, 'unknown_screen')
        self.assertIn('duplicate_id', response.json['message'])
        self.check(self.unaccept(self.acc, 's2'), 400, 'unknown_screen')
        self.assertIn('duplicate_id', self.unaccept(self.acc, 's2').json['message'])
        control = self.post_accept(dict(payload, screens={'s9': HASH}))
        self.check(control, 400, 'unknown_screen')
        self.assertNotIn('duplicate_id', control.json['message'], 'control: a screen that never existed')

    def test_scopes_all_and_project_are_refused_in_the_browser(self):
        base = self.screen_payload()
        for scope in ('all', 'project', 'cokolwiek'):
            with self.subTest(scope=scope):
                self.assert_refused(dict(base, scope=scope), 400, 'invalid_scope')
        self.check(self.post_accept(base), 201)  # control: scope screen

    def test_fields_set_by_the_server_are_refused(self):
        base = self.screen_payload()
        for extra in ({'via': 'cli'}, {'via': 'ui'}, {'who': A}, {'iteration': 99}, {'iteration': 1}):
            with self.subTest(extra=extra):
                self.assert_refused(dict(base, **extra), 400, 'field_not_allowed')
        self.check(self.post_accept(base), 201)

    def test_shape_errors_are_400_without_a_write(self):
        base = self.screen_payload()
        two = dict(base, screens={'s2': base['screens']['s2'], 's3': self.listing()['screens']['s3']})
        self.assert_refused(two, 400, 'invalid_request')
        for broken in ({k: v for k, v in base.items() if k != 'scope'}, {k: v for k, v in base.items() if k != 'version'},
                       dict(base, screens={}), dict(base, screens=['s2']), dict(base, screens={'s2': 'krótki'}),
                       dict(base, version='krótki'), dict(base, extra=1)):
            with self.subTest(broken=sorted(broken)):
                before = self.journal()
                response = self.post_accept(broken)
                self.assertEqual(response.status, 400, response.text)
                self.assertEqual(self.journal(), before)
        self.check(self.post_accept(base), 201)

    def test_unaccept_never_accepted_is_409_not_accepted(self):
        before = self.journal()
        response = self.check(self.unaccept(self.acc, 's2'), 409, 'not_accepted')
        self.assertEqual(response.json['screens'], ['s2'])
        self.assertEqual(self.journal(), before)
        self.check(self.accept(self.acc), 201)
        self.check(self.unaccept(self.acc, 's2'), 201)  # control: after an acceptance it works
        self.check(self.unaccept(self.acc, 's2'), 409, 'not_accepted')  # and a second time it does not

    def test_unaccept_of_several_screens_is_all_or_nothing(self):
        self.check(self.accept(self.acc, 's2'), 201)
        before = self.journal()
        self.check(self.unaccept(self.acc, 's2', 's3'), 409, 'not_accepted')  # s3 was never accepted
        self.assertEqual(self.journal(), before)
        self.assertEqual(set(self.acceptance()), {'s2'})
        self.check(self.accept(self.acc, 's3'), 201)
        self.check(self.unaccept(self.acc, 's2', 's3'), 201)
        self.assertEqual(self.acceptance(), {})

    def test_unaccept_unknown_screen_is_400(self):
        before = self.journal()
        for screen in ('s9', 's1'):
            self.check(self.unaccept(self.acc, screen), 400, 'unknown_screen')
        self.assertEqual(self.journal(), before)
        self.check(self.accept(self.acc), 201)
        self.check(self.unaccept(self.acc, 's2'), 201)

    def test_unaccept_rejects_empty_and_foreign_fields(self):
        self.check(self.accept(self.acc), 201)
        before = self.journal()
        for payload in ({'screens': []}, {'screens': 's2'}, {'screens': ['s2'], 'via': 'ui'}, {}):
            response = self.acc.op('demo', 'unaccept', None, payload)
            self.assertEqual(response.status, 400, response.text)
        self.assertEqual(self.journal(), before)
        self.check(self.unaccept(self.acc, 's2'), 201)

    def test_acceptor_cannot_undo_somebody_elses_acceptance(self):
        self.check(self.accept(self.acc, 's2'), 201)
        self.check(self.accept(self.acc2, 's3'), 201)
        before = self.journal()
        self.check(self.unaccept(self.acc2, 's2'), 403, 'forbidden')   # somebody else's
        self.check(self.unaccept(self.acc2, 's2', 's3'), 403, 'forbidden')  # one foreign screen spoils the whole request
        self.assertEqual(self.journal(), before)
        self.check(self.unaccept(self.acc2, 's3'), 201)   # control: own
        self.check(self.unaccept(self.dev, 's2'), 201)    # developer: any
        self.assertEqual(self.acceptance(), {})

    def test_the_acceptance_of_the_cli_belongs_to_the_who_login(self):
        listing = self.listing()
        ps.CommentsStore(self.demo, 'demo', clock=self.clock).append([{
            'type': 'accept', 'by': 'agent',
            'payload': {'scope': 'screen', 'screens': {'s2': listing['screens']['s2']}, 'iteration': 1, 'via': 'cli', 'who': C}}])
        self.assertEqual(self.acceptance()['s2']['who'], C)
        self.check(self.unaccept(self.acc2, 's2'), 403, 'forbidden')
        self.check(self.unaccept(self.acc, 's2'), 201)  # the acceptor named in `who` may undo it


class AcceptPeopleTests(AcceptCase):
    def test_cli_acceptor_who_is_in_people_with_the_name_from_the_users_file(self):
        login = 'ola.druga-nowak@example.com'
        users = ps.UsersFile(self.users_path, iterations=ITER, clock=self.clock)
        ps.add_user(users, login, role='acceptor', password='Haslo-Oli-12345', name='Ola Druga-Nowak')
        people = self.listing()['people']
        self.assertNotIn(login, people, 'control: an account without operations is not listed')
        ps.CommentsStore(self.demo, 'demo', clock=self.clock).append([{
            'type': 'accept', 'by': 'agent', 'payload': {
                'scope': 'screen', 'screens': {'s2': self.listing()['screens']['s2']}, 'iteration': 1, 'via': 'cli', 'who': login}}])
        people = self.listing()['people']
        self.assertIn(login, people)
        self.assertEqual((people[login]['firstName'], people[login]['fullName']), ('Ola', 'Ola Druga-Nowak'))
        self.assertEqual(self.listing()['acceptance']['s2']['who'], login)

    def test_ui_acceptor_and_a_foreign_who_in_a_ui_accept_do_not_add_people(self):
        self.check(self.accept(self.acc), 201)
        people = self.listing()['people']
        self.assertIn(C, people, 'the UI acceptor is a person through `by`')
        ps.CommentsStore(self.demo, 'demo', clock=self.clock).append([{
            'type': 'accept', 'by': C2, 'payload': {'scope': 'screen', 'screens': {'s3': self.listing()['screens']['s3']},
                                                    'iteration': 1, 'via': 'ui', 'who': 'ktos.obcy@example.com'}}])
        self.assertNotIn('ktos.obcy@example.com', self.listing()['people'], 'payload.who counts only for via cli')


class AcceptanceValidityTests(AcceptCase):
    """K25: aktualność akceptacji = hash ekranu równy zapisanemu."""

    def setUp(self):
        super().setUp()
        self.check(self.accept(self.acc, 's2'), 201)
        self.check(self.accept(self.acc, 's3'), 201)

    def valid(self):
        return {screen: entry['valid'] for screen, entry in self.acceptance().items()}

    def test_listing_shape_and_fields(self):
        listing = self.listing()
        self.assertEqual(set(listing['acceptance']), {'s2', 's3'})
        for entry in listing['acceptance'].values():
            self.assertEqual(set(entry), {'valid', 'who', 'at', 'iteration', 'via'})
        self.assertEqual(self.valid(), {'s2': True, 's3': True})
        self.assertEqual(listing['acceptance']['s2']['who'], C)
        self.check(self.unaccept(self.acc, 's2'), 201)
        self.assertEqual(set(self.acceptance()), {'s3'}, 'an unaccepted screen has no entry')

    def test_listing_is_public_like_the_rest_of_the_read_api(self):
        self.assertIn('acceptance', self.check(self.anon.operations('demo'), 200).json)

    def test_k25_matrix(self):
        text = self.index_text()
        cases = [
            ('section of s2', 'index.html', text.replace('Tydzień', 'Tydzień dłuższy'), {'s2': False, 's3': True}),
            ('section of s3', 'index.html', text.replace('Inny', 'Inny dłuższy'), {'s2': True, 's3': False}),
            ('shared CSS', 'screens.css', 'body { margin: 1px; padding: 0 }\n', {'s2': False, 's3': False}),
            ('<head> of the page', 'index.html', text.replace('<title>Demo</title>', '<title>Demo 2</title>'),
             {'s2': False, 's3': False}),
            ('README', 'README.md', 'Zupełnie inny opis, dłuższy\n', {'s2': True, 's3': True}),
            ('prototype.json', 'prototype.json', '{"title": "Inny tytuł"}\n', {'s2': True, 's3': True}),
            ('prototype.js', 'prototype.js', '/* inny silnik, dłuższy */\n', {'s2': True, 's3': True}),
        ]
        for name, relative, content, expected in cases:
            with self.subTest(change=name):
                path = os.path.join(self.demo, relative)
                original = Path(path).read_bytes()
                write(path, content)
                self.assertEqual(self.valid(), expected)
                write(path, original)
                self.assertEqual(self.valid(), {'s2': True, 's3': True}, 'control: restoring the file restores validity')

    def test_comments_and_another_screen_do_not_change_validity(self):
        self.seed(self.com, 'th-1', 'm-1')
        self.check(self.com.op('demo', 'reply', 'th-1', {'message': {'id': 'm-2', 'text': 'x'}}), 201)
        self.check(self.accept(self.acc2, 's3'), 201)  # re-accepted by somebody else: s2 untouched
        self.assertEqual(self.valid(), {'s2': True, 's3': True})
        self.assertEqual(self.acceptance()['s3']['who'], C2)
        self.assertEqual(self.acceptance()['s2']['who'], C)

    def test_a_stale_acceptance_can_be_accepted_again_and_undone(self):
        write(os.path.join(self.demo, 'index.html'), self.index_text().replace('Tydzień', 'Tydzień dłuższy'))
        self.assertFalse(self.valid()['s2'])
        self.check(self.accept(self.acc2, 's2'), 201)  # a re-accept of the changed screen
        self.assertTrue(self.valid()['s2'])
        write(os.path.join(self.demo, 'index.html'), self.index_text().replace('Tydzień dłuższy', 'Tydzień jeszcze dłuższy'))
        self.assertFalse(self.valid()['s2'])
        self.check(self.unaccept(self.acc2, 's2'), 201)  # the stale entry is still "current" for unaccept
        self.assertNotIn('s2', self.acceptance())

    def test_removed_screen_drops_out_of_the_listing(self):
        write(os.path.join(self.demo, 'index.html'), page_for('demo', R_S2_V1, '').replace(R_S3, ''))
        self.assertEqual(set(self.acceptance()), {'s2'}, 'the entry of a screen that no longer exists is not listed')


class AcceptanceLostPreviewTests(RestoreServerCase):
    """`restore-preview`: `acceptanceLost` = 1 przy aktualnej akceptacji ekranu i zmieniającym przywróceniu."""

    def accept(self, client, screen='s2'):
        listing = self.check(self.anon.operations('demo'), 200).json
        payload = {'scope': 'screen', 'screens': {screen: listing['screens'][screen]}, 'version': listing['version']}
        return client.op('demo', 'accept', None, payload)

    def lost(self, screen='s2'):
        return self.check(self.preview(self.acc, '?screen=%s&from=1' % screen), 200).json

    def test_one_with_a_current_acceptance_and_zero_without(self):
        self.assertEqual(self.lost()['acceptanceLost'], 0, 'control: nothing accepted yet')
        self.check(self.accept(self.acc), 201)
        data = self.lost()
        self.assertEqual((data['acceptanceLost'], data['unchanged']), (1, False))
        self.assertEqual(self.lost('s3')['acceptanceLost'], 0, 'the acceptance of s2 is not lost by restoring s3')
        self.check(self.accept(self.acc, 's3'), 201)
        self.assertEqual(self.lost('s3')['acceptanceLost'], 1)

    def test_zero_when_the_acceptance_is_stale_or_unaccepted(self):
        self.check(self.accept(self.acc), 201)
        self.assertEqual(self.lost()['acceptanceLost'], 1)
        write(os.path.join(self.demo, 'index.html'), self.index_text().replace('Tydzień', 'Tydzień dłuższy'))
        self.assertEqual(self.lost()['acceptanceLost'], 0, 'a stale acceptance is not valid, so nothing valid is lost')
        self.check(self.accept(self.acc), 201)  # accepted again after the change
        self.assertEqual(self.lost()['acceptanceLost'], 1)
        self.check(self.acc.op('demo', 'unaccept', None, {'screens': ['s2']}), 201)
        self.assertEqual(self.lost()['acceptanceLost'], 0)

    def test_zero_when_the_restore_would_change_nothing(self):
        write(os.path.join(self.demo, 'index.html'), r_page(R_S2_V1, R_S3_V2))  # s2 equals iteration 1
        self.check(self.accept(self.acc), 201)
        data = self.lost()
        self.assertEqual((data['acceptanceLost'], data['unchanged']), (0, True), 'D27: unchanged restores lose nothing')
        write(os.path.join(self.demo, 'index.html'), r_page(R_S2_V2, R_S3_V2))
        self.check(self.accept(self.acc), 201)  # control: the same acceptance on a differing screen is lost
        self.assertEqual(self.lost()['acceptanceLost'], 1)

    def test_the_restore_really_makes_the_acceptance_stale(self):
        self.check(self.accept(self.acc), 201)
        self.assertTrue(self.check(self.anon.operations('demo'), 200).json['acceptance']['s2']['valid'])
        self.check(self.restore(self.acc), 201)
        entry = self.check(self.anon.operations('demo'), 200).json['acceptance']['s2']
        self.assertFalse(entry['valid'], 'the restored s2 differs from the accepted one')


# ═════════════════════════════════════════════════════════════════════════════
# 5-6. GET /project i POST /project/accept (K43)
# ═════════════════════════════════════════════════════════════════════════════

PROJECT_URL = '/_api/v1/project'
PROJECT_ACCEPT_URL = '/_api/v1/project/accept'
# `problems` (S1-1, poprawki po przeglądzie F3): liczba ekranów niehaszowalnych (duplikat id, niezamknięta sekcja); zawsze int
LISTING_FIELDS = {'slug', 'title', 'iteration', 'screens', 'open', 'awaiting', 'version', 'format', 'problems'}


class ProjectCase(AcceptCase):
    """Prototypy: alpha, beta, demo (v2 z ekranami), empty (v2 tylko z przykładem), old (v1)."""

    def setUp(self):
        super().setUp()
        for slug in ('alpha', 'beta'):
            self.make_v2(slug, '{"title": "%s"}\n' % slug.title())
        self.make_v2('empty', '{"title": "Pusty"}\n', screens=False)
        self.directories = dict((slug, os.path.join(self.root, slug)) for slug in ('alpha', 'beta', 'demo', 'empty', 'old'))

    def make_v2(self, slug, meta, screens=True):
        directory = os.path.join(self.root, slug)
        write(os.path.join(directory, 'prototype.json'), meta)
        page = page_for(slug) if screens else (R_HEAD + R_S1 + R_TAIL).replace('data-prototype-id="demo"', 'data-prototype-id="%s"' % slug)
        write(os.path.join(directory, 'index.html'), page)
        write(os.path.join(directory, 'screens.css'), 'body { margin: 0 }\n')
        write_journal(directory, slug, [])

    def edit_screen(self, slug):
        path = os.path.join(self.directories[slug], 'index.html')
        write(path, Path(path).read_text(encoding='utf-8').replace('Tydzień', 'Tydzień zmieniony'))

    def project(self, client=None):
        return self.check((client or self.acc).get(PROJECT_URL), 200).json['prototypes']

    def versions(self):
        return dict((item['slug'], item['version']) for item in self.project() if item['format'] == 2)

    def snapshot(self):
        """Bajty dziennika każdego prototypu (None = brak pliku)."""
        return dict((slug, read_file_bytes(directory)) for slug, directory in self.directories.items())

    def post(self, client=None, cid=None, versions=None, **extra):
        body = {'cid': cid or uid('p'), 'versions': self.versions() if versions is None else versions}
        body.update(extra)
        return (client or self.acc).post(PROJECT_ACCEPT_URL, body)

    def accepts(self, slug):
        return [item for item in file_operations(self.directories[slug]) if item['type'] == 'accept']


class ProjectListingTests(ProjectCase):
    def test_access_401_403_200(self):
        self.check(self.anon.get(PROJECT_URL), 401)
        self.check(self.com.get(PROJECT_URL), 403, 'forbidden')
        self.check(self.acc.get(PROJECT_URL), 200)
        self.check(self.dev.get(PROJECT_URL), 200)

    def test_must_change_may_read(self):
        must = Client(self.server)
        self.check(must.login(ACC2, DEFAULT_PW), 200)
        self.check(must.get(PROJECT_URL), 200)  # GET is allowed, only POST needs the password change first

    def test_shape_order_and_counts(self):
        store = ps.CommentsStore(self.directories['alpha'], 'alpha', clock=self.clock)
        for number, text in enumerate(('a', 'b', 'c'), 1):
            self.check(self.com.op('alpha', 'create', 'th-%d' % number, create_payload('m-%d' % number, text)), 201)
        self.check(self.com.op('alpha', 'resolve', 'th-1', {'reason': 'author'}), 201)  # closed: not counted
        store.append([{'type': 'triage', 'threadId': 'th-2', 'by': 'agent', 'payload': {'class': 'disputed'}}])  # awaiting
        items = self.project()
        self.assertEqual([item['slug'] for item in items], ['alpha', 'beta', 'demo', 'empty', 'old'])
        for item in items:
            self.assertEqual(set(item), LISTING_FIELDS, item['slug'])
        by = dict((item['slug'], item) for item in items)
        alpha = by['alpha']
        self.assertEqual((alpha['title'], alpha['format'], alpha['iteration'], alpha['screens']), ('Alpha', 2, 1, 2))
        self.assertEqual((alpha['open'], alpha['awaiting']), (2, 1), 'open = open + awaiting + decided')
        self.assertEqual(alpha['version'], self.listing('alpha')['version'])
        self.assertEqual((by['empty']['screens'], by['empty']['format']), (0, 2), 'the example screen is not counted')
        self.assertEqual((by['beta']['open'], by['beta']['awaiting']), (0, 0))
        self.assertNotIn('nojson', by, 'a directory without prototype.json is not a prototype')

    def test_v1_has_format_1_and_null_version(self):
        old = dict((item['slug'], item) for item in self.project())['old']
        self.assertEqual((old['format'], old['version'], old['iteration']), (1, None, None))
        self.assertIsNone(old['open'])
        self.assertIsNone(old['awaiting'])
        control = dict((item['slug'], item) for item in self.project())['demo']
        self.assertEqual(control['format'], 2)
        self.assertRegex(control['version'], r'^[0-9a-f]{64}$')

    def test_problems_counts_the_ambiguous_screens_and_is_always_an_int(self):
        for item in self.project():
            self.assertIs(type(item['problems']), int, item['slug'])
            self.assertEqual(item['problems'], 0, item['slug'])
        path = os.path.join(self.directories['beta'], 'index.html')
        write(path, page_for('beta', R_S2_V1 + R_S2_V1, R_S3))  # duplicate id s2: both sections are unusable
        beta = dict((item['slug'], item) for item in self.project())['beta']
        self.assertEqual((beta['problems'], beta['screens']), (1, 1), 'only s3 is hashable')
        write(path, page_for('beta', R_S2_V1, R_S3.replace('</section>\n', '')))  # an unclosed section
        beta = dict((item['slug'], item) for item in self.project())['beta']
        self.assertEqual((beta['problems'], beta['screens']), (1, 1))
        write(path, page_for('beta'))  # control: repaired
        beta = dict((item['slug'], item) for item in self.project())['beta']
        self.assertEqual((beta['problems'], beta['screens']), (0, 2))

    def test_problems_ignore_ambiguities_made_only_of_examples(self):
        path = os.path.join(self.directories['beta'], 'index.html')
        write(path, page_for('beta').replace(R_S1, R_S1 + R_S1))
        beta = dict((item['slug'], item) for item in self.project())['beta']
        self.assertEqual((beta['problems'], beta['screens']), (0, 2))
        write(path, page_for('beta').replace(R_S1, R_S1 + '<section class="screen">\n<div>x</div>\n</section>\n'))
        beta = dict((item['slug'], item) for item in self.project())['beta']
        self.assertEqual(beta['problems'], 1, 'control: a screen section without an id counts')

    def test_listing_follows_the_files(self):
        before = dict((item['slug'], item['version']) for item in self.project())
        self.edit_screen('beta')
        after = dict((item['slug'], item['version']) for item in self.project())
        self.assertNotEqual(before['beta'], after['beta'])
        self.assertEqual(before['alpha'], after['alpha'])


class ProjectAcceptTests(ProjectCase):
    def test_success_accepts_v2_and_skips_v1_and_empty(self):
        before = self.snapshot()
        response = self.check(self.post(), 201)
        self.assertEqual(response.json, {'accepted': ['alpha', 'beta', 'demo'], 'skipped': ['empty', 'old']})
        for slug in ('alpha', 'beta', 'demo'):
            stored = self.accepts(slug)
            self.assertEqual(len(stored), 1, slug)
            hashes = self.listing(slug)['screens']
            self.assertEqual(stored[0]['payload'], {'scope': 'project', 'screens': hashes, 'iteration': 1, 'via': 'ui'})
            self.assertEqual(stored[0]['by'], C)
            self.assertEqual(set(self.acceptance(slug)), {'s2', 's3'})
            self.assertTrue(all(item['valid'] for item in self.acceptance(slug).values()))
            assert_canonical_text(self, read_file_bytes(self.directories[slug]))
        self.assertEqual(read_file_bytes(self.directories['empty']), before['empty'], 'a prototype without screens is not written')
        self.assertEqual(read_file_bytes(self.directories['old']), before['old'])
        self.assertFalse(os.path.exists(os.path.join(self.directories['old'], 'comments.json')))

    def test_roles_session_and_must_change(self):
        before = self.snapshot()
        self.check(self.post(self.anon), 401)
        self.check(self.post(self.com), 403, 'forbidden')
        must = Client(self.server)
        self.check(must.login(ACC2, DEFAULT_PW), 200)
        self.check(self.post(must), 403, 'password_change_required')
        self.check(self.acc.post(PROJECT_ACCEPT_URL, {'cid': uid('p'), 'versions': self.versions()},
                                 headers={'X-Skillsync-CSRF': None}), 403)
        self.assertEqual(self.snapshot(), before)
        self.check(self.post(self.dev), 201)  # control: a developer may

    def test_schema_errors_are_400_without_a_write(self):
        before = self.snapshot()
        versions = self.versions()
        for name, body in (('no cid', {'versions': versions}), ('versions list', {'cid': uid('p'), 'versions': ['x']}),
                           ('bad hash', {'cid': uid('p'), 'versions': dict(versions, alpha='krótki')}),
                           ('extra field', {'cid': uid('p'), 'versions': versions, 'screens': {}}),
                           ('by from the client', {'cid': uid('p'), 'versions': versions, 'by': 'agent'})):
            with self.subTest(name):
                response = self.acc.post(PROJECT_ACCEPT_URL, body)
                self.assertEqual(response.status, 400, response.text)
        self.assertEqual(self.snapshot(), before)
        self.check(self.post(), 201)

    def test_stale_version_is_409_before_the_first_write(self):
        versions = self.versions()
        self.edit_screen('beta')  # the second prototype changed after the window was opened
        before = self.snapshot()
        response = self.check(self.post(versions=versions), 409, 'stale_view')
        self.assertEqual(response.json['stale'], ['beta'])
        self.assertEqual(self.snapshot(), before, 'not a single comments.json changed, not even the first (alpha)')
        self.check(self.post(), 201)  # control: fresh versions

    def test_missing_v2_in_versions_is_stale_and_unknown_or_v1_keys_are_ignored(self):
        versions = self.versions()
        before = self.snapshot()
        for missing in ('demo', 'empty', 'alpha'):
            with self.subTest(missing=missing):
                partial = dict((slug, version) for slug, version in versions.items() if slug != missing)
                response = self.check(self.post(versions=partial), 409, 'stale_view')
                self.assertEqual(response.json['stale'], [missing])
                self.assertEqual(self.snapshot(), before)
        extra = dict(versions, old=HASH, nieznany=HASH)
        self.check(self.post(versions=extra), 201)  # control: unknown and v1 keys are ignored

    def test_repeat_after_success_is_200_with_the_same_body_and_no_new_write(self):
        cid = uid('p')
        first = self.check(self.post(cid=cid), 201).json
        after = self.snapshot()
        # accepted prototypes are recognised by the cid (their versions are not needed any more); `empty` has no
        # operation to recognise, so it is still compared: it is the only version the repeat must carry
        only_empty = {'empty': self.versions()['empty']}
        again = self.check(self.post(cid=cid, versions=only_empty), 200).json
        self.assertEqual(again, first)
        self.assertEqual(self.snapshot(), after)
        self.check(self.post(cid=uid('p')), 201)  # control: a new cid accepts again
        self.assertEqual(len(self.accepts('alpha')), 2)

    def test_null_version_of_a_v1_prototype_is_ignored_and_null_of_a_v2_is_stale(self):
        versions = self.versions()
        self.assertIsNone(dict((item['slug'], item['version']) for item in self.project())['old'], 'GET /project gives null for v1')
        before = self.snapshot()
        response = self.check(self.post(versions=dict(versions, demo=None)), 409, 'stale_view')
        self.assertEqual(response.json['stale'], ['demo'])
        self.assertEqual(self.snapshot(), before)
        # control: the whole listing echoed back, null of v1 included (the window sends what GET /project gave)
        echoed = dict((item['slug'], item['version']) for item in self.project())
        self.assertIn('old', echoed)
        self.check(self.post(versions=echoed), 201)

    def test_screens_with_problems_are_reported_as_skipped_screens(self):
        write(os.path.join(self.directories['beta'], 'index.html'), page_for('beta', R_S2_V1 + R_S2_V1, R_S3))
        cid = uid('p')
        first = self.check(self.post(cid=cid), 201).json
        self.assertEqual(first['accepted'], ['alpha', 'beta', 'demo'])
        self.assertEqual(first['skippedScreens'], [{'slug': 'beta', 'screen': 's2', 'reason': 'duplicate_id'}])
        self.assertEqual(sorted(self.accepts('beta')[0]['payload']['screens']), ['s3'], 'only the hashable screen is accepted')
        again = self.check(self.post(cid=cid), 200).json
        self.assertEqual(again, first, 'the repeat reports the same skipped screens')
        # control: the key appears only when there is something to report
        write(os.path.join(self.directories['beta'], 'index.html'), page_for('beta'))
        plain = self.check(self.post(), 201).json
        self.assertNotIn('skippedScreens', plain)

    def test_unclosed_section_is_reported_too(self):
        write(os.path.join(self.directories['alpha'], 'index.html'), page_for('alpha', R_S2_V1, R_S3.replace('</section>\n', '')))
        body = self.check(self.post(), 201).json
        self.assertEqual(body['skippedScreens'], [{'slug': 'alpha', 'screen': 's3', 'reason': 'unclosed'}])

    def test_two_parallel_requests_with_the_same_cid_write_one_operation_per_prototype(self):
        for round_number in range(4):
            cid = uid('par')
            versions = self.versions()
            barrier = threading.Barrier(2)
            results = []

            def call():
                client = self.client(C)
                barrier.wait(10)
                results.append(client.post(PROJECT_ACCEPT_URL, {'cid': cid, 'versions': versions}))

            threads = [threading.Thread(target=call) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(60)
            self.assertEqual(len(results), 2)
            self.assertLess(max(item.status for item in results), 500, [item.text for item in results])
            self.assertTrue(all(item.status in (200, 201) for item in results), [(i.status, i.text) for i in results])
            self.assertEqual([item.json['accepted'] for item in results], [['alpha', 'beta', 'demo']] * 2)
            for slug in ('alpha', 'beta', 'demo'):
                self.assertEqual(len([item for item in self.accepts(slug) if item['cid'] == cid]), 1, (round_number, slug))
        # control: a different cid is a different operation
        self.check(self.post(cid=uid('par')), 201)
        self.assertEqual(len(self.accepts('alpha')), 5)

    def test_never_two_prototype_locks_at_once(self):
        state = {'active': 0, 'max': 0, 'entered': 0}
        guard = threading.Lock()
        enter, leave = ps.FileLock.__enter__, ps.FileLock.__exit__

        def counted_enter(lock):
            result = enter(lock)
            with guard:
                state['active'] += 1
                state['entered'] += 1
                state['max'] = max(state['max'], state['active'])
            return result

        def counted_exit(lock, *exc):
            with guard:
                state['active'] -= 1
            return leave(lock, *exc)

        with mock.patch.object(ps.FileLock, '__enter__', counted_enter), mock.patch.object(ps.FileLock, '__exit__', counted_exit):
            # the counter itself must be able to see two locks: nested locks are counted as 2 (control of the measurement)
            with ps.FileLock(os.path.join(self.root, 'x1.lock')), ps.FileLock(os.path.join(self.root, 'x2.lock')):
                pass
            self.assertEqual(state['max'], 2)
            state.update(active=0, max=0, entered=0)
            self.check(self.post(), 201)
        self.assertEqual(state['max'], 1, 'each prototype is written under its own lock, never two at once')
        self.assertGreaterEqual(state['entered'], 3)
        self.assertEqual(state['active'], 0)


class ProjectAcceptPartialTests(ProjectCase):
    def hook_transaction(self, slug, action):
        """Przed pierwszym zapisem prototypu `slug` wykonuje `action()` (zmiana wersji między porównaniem a zapisem)."""
        original = ps.CommentsStore.transaction
        fired = []

        def wrapper(store, build, *args, **kwargs):
            if store.slug == slug and not fired:
                fired.append(True)
                action()
            return original(store, build, *args, **kwargs)

        return mock.patch.object(ps.CommentsStore, 'transaction', wrapper), fired

    def test_version_change_between_comparison_and_write_is_409_partial_and_the_retry_completes(self):
        cid = uid('p')
        versions = self.versions()
        patch, fired = self.hook_transaction('beta', lambda: self.edit_screen('beta'))
        with patch:
            response = self.check(self.post(cid=cid, versions=versions), 409, 'partial')
        self.assertTrue(fired)
        self.assertEqual(response.json['accepted'], ['alpha'])
        self.assertEqual(response.json['pending'], ['beta', 'demo'])
        self.assertEqual(len(self.accepts('alpha')), 1)
        self.assertEqual((len(self.accepts('beta')), len(self.accepts('demo'))), (0, 0))
        # retry with the same cid and fresh versions: finishes `pending`, alpha does not get a second operation
        done = self.check(self.post(cid=cid), 201).json
        self.assertEqual(done, {'accepted': ['alpha', 'beta', 'demo'], 'skipped': ['empty', 'old']})
        self.assertEqual([len(self.accepts(slug)) for slug in ('alpha', 'beta', 'demo')], [1, 1, 1])
        self.assertEqual(self.accepts('alpha')[0]['cid'], cid)
        again = self.check(self.post(cid=cid), 200).json
        self.assertEqual(again, done)

    def test_without_the_injected_change_the_same_request_is_201(self):
        patch, fired = self.hook_transaction('beta', lambda: None)
        with patch:
            self.check(self.post(), 201)
        self.assertTrue(fired)

    def test_write_failure_is_500_write_failed_with_accepted_and_pending(self):
        """Sprzed F-SEC-4: OSError przy zapisie = 409 `partial`; teraz: 500 `write_failed` z `accepted`/`pending` (błąd serwera nie jest
        „zmianą wersji”), a ponowienie z tym samym cid dokańcza pracę."""
        cid = uid('p')

        def fail():
            raise OSError(errno.ENOSPC, 'brak miejsca')

        patch, _fired = self.hook_transaction('demo', fail)
        with patch:
            response = self.check(self.post(cid=cid), 500, 'write_failed')
        self.assertEqual(response.json['accepted'], ['alpha', 'beta'])
        self.assertEqual(response.json['pending'], ['demo'])
        self.assertNotIn('Traceback', response.text)
        self.assertNotIn('brak miejsca', response.text, 'the OS message is not echoed')
        self.assertEqual(len(self.accepts('demo')), 0)
        self.check(self.post(cid=cid), 201)
        self.assertEqual([len(self.accepts(slug)) for slug in ('alpha', 'beta', 'demo')], [1, 1, 1])

    def test_lock_timeout_and_version_change_stay_partial_but_other_server_errors_keep_their_own_code(self):
        cases = [
            ('lock timeout', lambda: (_ for _ in ()).throw(ps.LockTimeout()), 409, 'partial', {}),
            ('file too large', lambda: (_ for _ in ()).throw(ps.FileTooLarge(123)), 507, 'file_too_large', {'size': 123}),
            ('invalid log', lambda: (_ for _ in ()).throw(ps.CommentsFileError('zepsuty dziennik')), 409, 'invalid_log', {}),
            ('needs migration', lambda: (_ for _ in ()).throw(ps.NeedsMigration()), 409, 'needs_migration', {}),
        ]
        for name, action, status, error, extra in cases:
            with self.subTest(name):
                cid = uid('p')
                patch, fired = self.hook_transaction('demo', action)
                with patch:
                    response = self.check(self.post(cid=cid), status, error)
                self.assertTrue(fired)
                self.assertEqual(response.json['accepted'], ['alpha', 'beta'])
                self.assertEqual(response.json['pending'], ['demo'])
                for key, value in extra.items():
                    self.assertEqual(response.json[key], value)
                self.check(self.post(cid=cid), 201)  # control: with no failure the same cid completes the work
                self.assertEqual(len(self.accepts('demo')), 1)
                # next case needs a fresh state: forget the three accepts by removing them from the journals
                for slug in ('alpha', 'beta', 'demo'):
                    write_journal(self.directories[slug], slug, [])
        patch, fired = self.hook_transaction('beta', lambda: self.edit_screen('beta'))
        with patch:
            response = self.check(self.post(), 409, 'partial')  # control: a changed version is still `partial`
        self.assertEqual(response.json['pending'], ['beta', 'demo'])

    def test_lock_timeout_keeps_the_retry_after_header(self):
        def timeout():
            raise ps.LockTimeout()

        patch, _fired = self.hook_transaction('beta', timeout)
        with patch:
            response = self.check(self.post(), 409, 'partial')
        self.assertEqual(response.json['accepted'], ['alpha'])
        self.assertEqual(response.json['pending'], ['beta', 'demo'])

    def test_file_too_large_keeps_its_status_header_free_and_pending_list(self):
        patch, _fired = self.hook_transaction('beta', lambda: (_ for _ in ()).throw(ps.FileTooLarge(5)))
        with patch:
            response = self.check(self.post(), 507, 'file_too_large')
        self.assertEqual((response.json['accepted'], response.json['pending']), (['alpha'], ['beta', 'demo']))
        self.assertEqual(len(self.accepts('beta')), 0)

# ═════════════════════════════════════════════════════════════════════════════
# Pętla końcowa W1 (9.1): bezpieczeństwo serwera
# ═════════════════════════════════════════════════════════════════════════════

class SecurityLoopTests(AcceptCase):
    def raw_request(self, request_line):
        import socket
        with socket.create_connection(('127.0.0.1', self.server.port), timeout=10) as connection:
            connection.sendall(request_line + b'\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n')
            data = b''
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                data += chunk
        return data

    def restart_with_log(self):
        stream = io.StringIO()
        self.tearDown()
        self.start_server(log=stream)
        return stream

    # -- F-SEC-1: znaki sterujące w dzienniku ---------------------------------

    def test_control_characters_of_the_path_never_reach_the_log_raw(self):
        stream = self.restart_with_log()
        self.assertIn(b'200', self.raw_request(b'GET /demo/index.html HTTP/1.1').split(b'\r\n')[0])  # control: a plain request is logged as is
        self.raw_request(b'GET /demo/x\x1b[31mFORGED\x7f\x01end HTTP/1.1')
        log = stream.getvalue()
        self.assertIn('GET /demo/index.html 200', log)
        self.assertIn('\\x1b', log)
        self.assertIn('\\x7f', log)
        self.assertIn('\\x01', log)
        for line in log.split('\n'):
            self.assertTrue(all(ord(char) >= 0x20 and ord(char) != 0x7f for char in line), repr(line))
        self.assertNotIn('\x1b', log)

    def test_log_safe_escapes_only_control_characters(self):
        self.assertEqual(ps._log_safe('a\x1b\r\nb\x7f'), 'a\\x1b\\x0d\\x0a' + 'b\\x7f')
        self.assertEqual(ps._log_safe('zażółć /ścieżka?x=1 😀'), 'zażółć /ścieżka?x=1 😀', 'control: ordinary text is unchanged')
        self.assertEqual(ps._log_safe(''), '')

    def test_a_malformed_request_line_does_not_forge_a_log_line(self):
        stream = self.restart_with_log()
        with mock.patch.object(sys, 'stderr', io.StringIO()):
            self.raw_request(b'GET /a\x01\x1bFORGED-LINE HTTP/9.9')
        for line in stream.getvalue().split('\n'):
            self.assertTrue(all(ord(char) >= 0x20 for char in line), repr(line))
            self.assertFalse(line.startswith('FORGED'))

    def test_a_malformed_request_line_does_not_print_a_traceback(self):
        """Zła wersja HTTP: `parse_request` -> `send_error` -> `log_request` przed ustawieniem `self.path` (prototype_server.py `log_request`).
        Asercje nie zależą od wersji Pythona (3.11 odsyła treść bez linii statusu): stderr bez Traceback, serwer odpowiada na kolejne
        żądanie, a log ma zsanityzowaną linię (także z `\\x1b` w linii żądania)."""
        stream = self.restart_with_log()
        errors = io.StringIO()
        with mock.patch.object(sys, 'stderr', errors):
            self.raw_request(b'GET /a HTTP/9.9')
            self.raw_request(b'GET /b\x1bFORGED HTTP/9.9')
            follow_up = self.raw_request(b'GET /demo/index.html HTTP/1.1')   # control: the server still answers a proper request
        self.assertNotIn('Traceback', errors.getvalue())
        self.assertTrue(follow_up.startswith(b'HTTP/1.'), follow_up[:40])
        self.assertIn(b' 200 ', follow_up.split(b'\r\n')[0])
        log = stream.getvalue()
        self.assertTrue(log.strip(), 'the log is not empty')
        self.assertNotIn('\x1b', log)
        for line in log.split('\n'):
            self.assertTrue(all(ord(char) >= 0x20 and ord(char) != 0x7f for char in line), repr(line))
        self.assertIn('GET /demo/index.html 200', log)
        errors = io.StringIO()
        with mock.patch.object(sys, 'stderr', errors):
            self.raw_request(b'GET /demo/index.html HTTP/1.1')  # control: a well-formed request prints nothing
        self.assertEqual(errors.getvalue(), '')

    # -- F-SEC-2: login poza limitem długości ---------------------------------

    def reserve_calls(self):
        calls = []
        real = self.server.app.limiter.reserve

        def counting(login, ip):
            calls.append((login, ip))
            return real(login, ip)

        return calls, mock.patch.object(self.server.app.limiter, 'reserve', counting)

    def test_login_over_320_characters_is_400_before_the_limiter(self):
        calls, patch = self.reserve_calls()
        with patch:
            for bad in ('a' * 321, 'a' * 60000, '😀' * 161, 12345, None, ['x'], {'a': 1}, True):
                with self.subTest(login=str(bad)[:12]):
                    response = Client(self.server).post('/_api/v1/login', {'login': bad, 'password': 'x'})
                    self.check(response, 400, 'invalid_request')
            self.assertEqual(calls, [], 'the limiter was not touched by any of them')
            self.assertEqual(len(self.server.app.limiter._pending_pairs), 0)
            # control: 320 characters reach the authentication (wrong credentials = 401) and the limiter
            self.check(Client(self.server).post('/_api/v1/login', {'login': 'a' * 320, 'password': 'x'}), 401, 'invalid_credentials')
            self.assertEqual(len(calls), 1)
            self.check(Client(self.server).post('/_api/v1/login', {'login': '😀' * 160, 'password': 'x'}), 401)  # 320 UTF-16 units
            self.assertEqual(len(calls), 2)
        self.check(Client(self.server).login(A, PASSWORDS[A]), 200)  # a normal login still works

    def test_long_logins_do_not_fill_the_limiter_table(self):
        before = len(self.server.app.limiter._pairs) if hasattr(self.server.app.limiter, '_pairs') else None
        for _ in range(8):
            Client(self.server).post('/_api/v1/login', {'login': 'b' * 5000, 'password': 'x'})
        if before is not None:
            self.assertEqual(len(self.server.app.limiter._pairs), before)
        for _ in range(5):  # control: wrong passwords of a normal login are counted and then blocked
            Client(self.server).login(A, 'zle-haslo-123')
        self.check(Client(self.server).login(A, PASSWORDS[A]), 429, 'rate_limited')

    # -- F-SEC-3: liczby poza zakresem JavaScript --------------------------------

    def test_integers_above_2_pow_53_are_400_without_a_traceback(self):
        errors = io.StringIO()
        self.seed(self.com, 'th-1', 'm-1')
        before = self.journal()
        limit = 2 ** 53
        with mock.patch.object(sys, 'stderr', errors):
            self.check(self.com.op('demo', 'resolve', 'th-1', {'reason': 'author', 'iteration': limit + 1}), 400)
            for value in (limit * 1000, 10 ** 400):
                self.check(self.com.op('demo', 'resolve', 'th-1', {'reason': 'author', 'iteration': value}), 400)
            raw = json.dumps({'cid': uid('c'), 'type': 'resolve', 'threadId': 'th-1', 'payload': {'reason': 'author', 'iteration': 0}})
            body = raw.replace('"iteration": 0', '"iteration": 1e300').encode('utf-8')
            response = self.com.post('/_api/v1/prototypes/demo/operations', raw=body)
            self.assertEqual(response.status, 400, response.text)
            anchor_value = dict(anchor('screen'), iteration=limit + 1)
            response = self.com.op('demo', 'create', 'th-2', create_payload('m-2', 'x', anchor_value=anchor_value))
            self.assertEqual(response.status, 400, response.text)
            decision = self.dev.op('demo', 'decision', 'th-1', {'kind': 'agent', 'outcome': 'apply', 'text': 'x', 'iteration': limit + 1})
            self.assertIn(decision.status, (400, 403))
        self.assertNotIn('Traceback', errors.getvalue())
        self.assertEqual(self.journal(), before)
        # control: 2^53 itself is the largest accepted value
        self.check(self.com.op('demo', 'resolve', 'th-1', {'reason': 'author', 'iteration': limit}), 201)

    def test_validate_payload_bounds_iteration_numbers_of_the_cli_operations(self):
        limit = 2 ** 53
        builders = {
            'accept': lambda number: {'scope': 'screen', 'screens': {'s2': HASH}, 'iteration': number, 'via': 'cli', 'who': 'a@example.com'},
            'iteration': lambda number: {'number': number, 'path': 'iterations/%d/' % number, 'hash': HASH},
            'resolve': lambda number: {'reason': 'done', 'iteration': number},
        }
        for kind, build in builders.items():
            with self.subTest(kind=kind):
                ps.validate_payload(kind, build(limit), origin='cli')  # control: the largest value is valid
                with self.assertRaises(ps.PrototypeServerError) as caught:
                    ps.validate_payload(kind, build(limit + 1), origin='cli')
                self.assertEqual(caught.exception.status, 400)

    # -- F-SEC-6: nazwy plików bez względu na wielkość liter ----------------------

    def test_lock_and_kept_v1_files_are_404_in_any_letter_case(self):
        excluded = ('comments.v1.js', 'COMMENTS.V1.JS', 'Comments.V1.js', 'comments.json.lock', 'COMMENTS.JSON.LOCK', 'extra.LOCK',
                    'TMP.TMP', '.HIDDEN', '.GIT/config')
        for name in excluded:  # the files really exist under exactly these names, so only the rule can make them 404
            write(os.path.join(self.demo, name), 'tajne\n')
        write(os.path.join(self.demo, 'ok.txt'), 'dozwolone\n')
        write(os.path.join(self.demo, 'Duze-Litery.TXT'), 'dozwolone\n')
        for name in excluded:
            with self.subTest(name=name):
                self.check(self.anon.get('/demo/' + name), 404)
        self.check(self.anon.get('/demo/ok.txt'), 200)               # control: an ordinary file is served
        self.check(self.anon.get('/demo/Duze-Litery.TXT'), 200)      # and upper-case letters alone do not exclude a file

    def test_the_assets_hash_ignores_excluded_names_in_any_case(self):
        base = self.listing()['screens']
        write(os.path.join(self.demo, 'COMMENTS.V1.JS'), 'zmiana\n')
        write(os.path.join(self.demo, 'X.LOCK'), 'zmiana\n')
        self.assertEqual(self.listing()['screens'], base)
        write(os.path.join(self.demo, 'styl-dodatkowy.css'), 'body { color: red }\n')   # control: a real asset moves the hash
        self.assertNotEqual(self.listing()['screens'], base)


# ═════════════════════════════════════════════════════════════════════════════
# 7-8. CLI `accept` (TTY), `list --json` i `index`
# ═════════════════════════════════════════════════════════════════════════════

SECRET_PW = 'Tajne-Haslo-Celiny-7741'
MARKER = 'ZNACZNIK-HASLA-31415'
V1_LOG = 'window.__SKILLSYNC_PROTOTYPE_COMMENTS__ = {"format":"skillsync-prototype-comments","version":1,"operations":[]};\n'


def make_project_users(root, accounts):
    """Plik użytkowników projektu z niską liczbą iteracji PBKDF2; `accounts` = [(login, rola, hasło | None)]."""
    path = Path(root) / '.skillsync' / 'prototype' / 'users.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    users = ps.UsersFile(str(path), iterations=ITER)
    for login, role, password in accounts:
        if password is None:
            ps.add_user(users, login, role=role, default_password=DEFAULT_PW)
        else:
            ps.add_user(users, login, role=role, password=password)
    return path


class CliProject(PrototypeFixture):
    """Projekt z prototypami: alpha i beta (v2, ekrany s2 i s3 oraz przykład s1), empty (tylko przykład), old (v1)."""

    def setUp(self):
        super().setUp()
        for slug in ('alpha', 'beta', 'empty', 'old'):
            self.init(slug)
        for slug in ('alpha', 'beta'):
            self.page(slug)
        self.page('empty', screens=False)
        self.set_comments('old', V1_LOG)

    def page(self, slug, screens=True, s2=R_S2_V1, s3=R_S3):
        text = page_for(slug, s2, s3) if screens else (R_HEAD + R_S1 + R_TAIL).replace('data-prototype-id="demo"', 'data-prototype-id="%s"' % slug)
        (self.proto_dir(slug) / 'index.html').write_text(text, encoding='utf-8')

    def hashes(self, slug):
        return ps.screen_hashes(str(self.proto_dir(slug)))['screens']

    def operations(self, slug):
        return json.loads((self.proto_dir(slug) / 'comments.json').read_text(encoding='utf-8'))['operations']

    def accepts(self, slug):
        return [item for item in self.operations(slug) if item['type'] == 'accept']

    def journal(self, slug):
        path = self.proto_dir(slug) / 'comments.json'
        return path.read_bytes() if path.exists() else None

    def accept_ops(self, slug, *screens, who='celina@example.com', via='cli', iteration=1):
        """Dopisuje akceptację wprost do dziennika (bez TTY)."""
        hashes = self.hashes(slug)
        payload = {'scope': 'screen', 'screens': {screen: hashes[screen] for screen in screens}, 'iteration': iteration, 'via': via}
        if via == 'cli':
            payload['who'] = who
        by = 'agent' if via == 'cli' else who
        ps.CommentsStore(str(self.proto_dir(slug)), slug).append([{'type': 'accept', 'by': by, 'payload': payload}])


@unittest.skipUnless(POSIX, 'the TTY tests need a POSIX pseudo-terminal')
class AcceptCliTests(CliProject):
    def setUp(self):
        super().setUp()
        self.users_path = make_project_users(self.root, [
            ('celina@example.com', 'acceptor', SECRET_PW), ('dorota@example.com', 'developer', SECRET_PW),
            ('ala@example.com', 'commenter', SECRET_PW), ('nowy@example.com', 'acceptor', None)])
        self.outputs = []

    def tearDown(self):
        for text in self.outputs:
            self.assertNotIn(SECRET_PW, text, 'the password leaked into the output')
            self.assertNotIn(DEFAULT_PW, text)
        super().tearDown()

    def argv(self, *args):
        return [sys.executable, str(CLI), 'prototype', 'accept', str(self.root), *args, '--json']

    def env(self):
        return {key: value for key, value in os.environ.items() if key != 'SKILLSYNC_CONFIG'}

    def run_without_tty(self, *args, stdin=None):
        """stdin z rury i brak terminala sterującego (nowa sesja)."""
        result = subprocess.run(self.argv(*args), input=stdin, capture_output=True, text=True, timeout=60, cwd=self.base,
                                env=self.env(), start_new_session=True)
        self.outputs.append(result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        return result.returncode, json.loads(result.stdout), result

    def run_on_tty(self, *args, password=None):
        """Proces z terminalem sterującym (pty); hasło wpisane dopiero po pytaniu. Zwraca (exit, raport, tekst terminala)."""
        import fcntl
        import pty
        import termios
        master, slave = pty.openpty()

        def controlling_terminal():
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        process = subprocess.Popen(self.argv(*args), stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   cwd=self.base, env=self.env(), start_new_session=True, preexec_fn=controlling_terminal)
        os.close(slave)
        seen, sent = b'', False
        deadline = time.monotonic() + 40
        try:
            while process.poll() is None and time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.1)
                if not ready:
                    continue
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                seen += chunk
                if password is not None and not sent and b'Has' in seen and seen.rstrip().endswith(b':'):
                    os.write(master, password.encode('utf-8') + b'\n')
                    sent = True
            stdout, stderr = process.communicate(timeout=30)
            while select.select([master], [], [], 0.05)[0]:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                seen += chunk
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
            os.close(master)
        terminal = seen.decode('utf-8', 'replace')
        self.outputs.extend([stdout, stderr, terminal])
        self.assertNotIn('Traceback', stderr, stderr)
        return process.returncode, json.loads(stdout), terminal

    # -- tty_required, hasło ------------------------------------------------

    def test_without_a_tty_it_is_blocked_before_any_password_prompt(self):
        before = self.journal('alpha')
        code, report, result = self.run_without_tty('alpha', '--screen', 's2', '--by', 'celina@example.com', stdin=SECRET_PW + '\n')
        self.assertEqual((code, report['status'], report['error']), (2, 'BLOCKED', 'tty_required'))
        self.assertNotIn('Hasło', result.stdout + result.stderr, 'it must not ask')
        self.assertEqual(self.journal('alpha'), before)
        self.assertEqual(self.accepts('alpha'), [])
        # control: the same command on a terminal with the right password passes
        code, report, _ = self.run_on_tty('alpha', '--screen', 's2', '--by', 'celina@example.com', password=SECRET_PW)
        self.assertEqual((code, report['status']), (0, 'PASS'))

    def test_closed_stdin_is_not_a_tty_either(self):
        result = subprocess.run(self.argv('alpha', '--all', '--by', 'celina@example.com'), stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=60, cwd=self.base, env=self.env())
        self.assertEqual((result.returncode, json.loads(result.stdout)['error']), (2, 'tty_required'))
        self.assertEqual(self.accepts('alpha'), [])

    def test_wrong_password_is_invalid_credentials_and_the_right_one_writes(self):
        before = self.journal('alpha')
        code, report, terminal = self.run_on_tty('alpha', '--screen', 's2', '--by', 'celina@example.com', password='Zle-haslo-000')
        self.assertEqual((code, report['status'], report['error']), (2, 'BLOCKED', 'invalid_credentials'))
        self.assertIn('Hasło celina@example.com', terminal, 'the prompt was shown on the terminal')
        self.assertEqual(self.journal('alpha'), before)
        code, report, terminal = self.run_on_tty('alpha', '--screen', 's2', '--by', 'celina@example.com', password=SECRET_PW)
        self.assertEqual((code, report['status']), (0, 'PASS'))
        self.assertIn('Hasło celina@example.com', terminal)
        self.assertNotIn(SECRET_PW, terminal, 'no echo of the typed password')

    def test_the_stored_operation_is_by_agent_via_cli_with_who(self):
        code, report, _ = self.run_on_tty('alpha', '--screen', 's2', '--by', 'Celina@Example.com', password=SECRET_PW)
        self.assertEqual(code, 0, report)
        self.assertEqual(report, {'status': 'PASS', 'accepted': [{'slug': 'alpha', 'screens': ['s2']}], 'skipped': [],
                                  'who': 'celina@example.com', 'via': 'cli'})
        stored = self.accepts('alpha')
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]['by'], 'agent')
        self.assertEqual(stored[0]['payload'], {'scope': 'screen', 'screens': {'s2': self.hashes('alpha')['s2']},
                                                'iteration': 1, 'via': 'cli', 'who': 'celina@example.com'})
        entry = ps.fold_operations(self.operations('alpha'))['acceptance']['s2']
        self.assertEqual((entry['who'], entry['by'], entry['via']), ('celina@example.com', 'agent', 'cli'))
        self.assertEqual(self.accepts('beta'), [], 'another prototype is untouched')

    def test_developer_may_accept_too(self):
        code, report, _ = self.run_on_tty('alpha', '--all', '--by', 'dorota@example.com', password=SECRET_PW)
        self.assertEqual((code, report['who']), (0, 'dorota@example.com'))

    # -- zakresy ---------------------------------------------------------------

    def test_screen_is_repeatable_and_all_covers_every_screen_without_the_example(self):
        code, report, _ = self.run_on_tty('alpha', '--screen', 's2', '--screen', 's3', '--by', 'celina@example.com', password=SECRET_PW)
        self.assertEqual((code, report['accepted']), (0, [{'slug': 'alpha', 'screens': ['s2', 's3']}]))
        self.assertEqual(self.accepts('alpha')[0]['payload']['scope'], 'screen')
        code, report, _ = self.run_on_tty('beta', '--all', '--by', 'celina@example.com', password=SECRET_PW)
        self.assertEqual((code, report['accepted']), (0, [{'slug': 'beta', 'screens': ['s2', 's3']}]))
        payload = self.accepts('beta')[0]['payload']
        self.assertEqual((payload['scope'], sorted(payload['screens'])), ('all', ['s2', 's3']))
        self.assertNotIn('s1', payload['screens'], 'the example screen is never accepted')

    def test_project_accepts_every_v2_prototype_and_skips_v1_and_empty(self):
        before_old = self.journal('old')
        code, report, _ = self.run_on_tty('--project', '--by', 'celina@example.com', password=SECRET_PW)
        self.assertEqual(code, 0, report)
        self.assertEqual([item['slug'] for item in report['accepted']], ['alpha', 'beta'])
        self.assertEqual(report['skipped'], ['empty', 'old'])
        for slug in ('alpha', 'beta'):
            payload = self.accepts(slug)[0]['payload']
            self.assertEqual((payload['scope'], payload['via'], payload['who']), ('project', 'cli', 'celina@example.com'))
            self.assertEqual(payload['screens'], self.hashes(slug))
        self.assertEqual(self.accepts('empty'), [])
        self.assertEqual(self.journal('old'), before_old)

    def test_unknown_or_example_screen_is_blocked_without_a_write(self):
        before = self.journal('alpha')
        for screen in ('s9', 's1'):
            code, report, _ = self.run_on_tty('alpha', '--screen', screen, '--by', 'celina@example.com', password=SECRET_PW)
            self.assertEqual((code, report['error']), (2, 'unknown_screen'), screen)
            self.assertEqual(self.journal('alpha'), before)
        code, report, _ = self.run_on_tty('alpha', '--screen', 's2', '--screen', 's9', '--by', 'celina@example.com', password=SECRET_PW)
        self.assertEqual((code, report['error']), (2, 'unknown_screen'), 'one bad screen spoils the whole request')
        self.assertEqual(self.journal('alpha'), before)
        code, _report, _ = self.run_on_tty('alpha', '--screen', 's2', '--by', 'celina@example.com', password=SECRET_PW)
        self.assertEqual(code, 0)

    def test_screens_with_problems_are_skipped_and_reported_but_never_accepted(self):
        self.page('alpha', s2=R_S2_V1 + R_S2_V1)  # duplicate id s2
        code, report, _ = self.run_on_tty('alpha', '--all', '--by', 'celina@example.com', password=SECRET_PW)
        self.assertEqual(code, 0, report)
        self.assertEqual(report['accepted'], [{'slug': 'alpha', 'screens': ['s3']}])
        self.assertEqual(report['skippedScreens'], [{'slug': 'alpha', 'screen': 's2', 'reason': 'duplicate_id'}])
        self.assertEqual(sorted(self.accepts('alpha')[0]['payload']['screens']), ['s3'])
        self.page('beta', s3=R_S3.replace('</section>\n', ''))  # unclosed s3
        code, report, _ = self.run_on_tty('--project', '--by', 'celina@example.com', password=SECRET_PW)
        self.assertEqual(code, 0, report)
        self.assertIn({'slug': 'beta', 'screen': 's3', 'reason': 'unclosed'}, report['skippedScreens'])
        # control: a clean prototype reports no skippedScreens key at all
        self.page('beta')
        code, report, _ = self.run_on_tty('beta', '--all', '--by', 'celina@example.com', password=SECRET_PW)
        self.assertEqual(code, 0)
        self.assertNotIn('skippedScreens', report)

    def test_problem_of_examples_only_is_not_skipped_and_a_screen_without_id_is_reported(self):
        self.page('alpha')
        (self.proto_dir('alpha') / 'index.html').write_text(
            page_for('alpha').replace(R_S1, R_S1 + R_S1), encoding='utf-8')  # two example sections with the same id
        code, report, _ = self.run_on_tty('alpha', '--all', '--by', 'celina@example.com', password=SECRET_PW)
        self.assertEqual(code, 0, report)
        self.assertNotIn('skippedScreens', report, 'an ambiguity made only of examples is not reported')
        no_id = '<section class="screen">\n  <div class="frame">Bez id</div>\n</section>\n'
        (self.proto_dir('beta') / 'index.html').write_text(page_for('beta').replace(R_S3, no_id + R_S3), encoding='utf-8')
        code, report, _ = self.run_on_tty('beta', '--all', '--by', 'celina@example.com', password=SECRET_PW)
        self.assertEqual(code, 0, report)
        self.assertEqual(report['skippedScreens'], [{'slug': 'beta', 'screen': None, 'reason': 'missing_id'}])
        self.assertEqual(report['accepted'], [{'slug': 'beta', 'screens': ['s2', 's3']}])

    def test_unknown_screen_names_the_cause_of_an_ambiguous_screen(self):
        self.page('alpha', s2=R_S2_V1 + R_S2_V1)
        code, report, _ = self.run_on_tty('alpha', '--screen', 's2', '--by', 'celina@example.com', password=SECRET_PW)
        self.assertEqual((code, report['error']), (2, 'unknown_screen'))
        self.assertIn('ambiguous', report['reason'])
        self.assertIn('duplicate_id', report['reason'])
        self.page('alpha')
        code, report, _ = self.run_on_tty('alpha', '--screen', 's9', '--by', 'celina@example.com', password=SECRET_PW)
        self.assertEqual((code, report['error']), (2, 'unknown_screen'))
        self.assertNotIn('duplicate_id', report['reason'], 'control: an unknown screen has no ambiguity cause')

    def test_project_flag_with_a_slug_in_either_order_is_refused_without_a_write(self):
        before = {slug: self.journal(slug) for slug in ('alpha', 'beta', 'empty')}
        for args in (('alpha', '--project'), ('--project', 'alpha')):
            with self.subTest(args=args):
                result = subprocess.run(self.argv(*args, '--by', 'celina@example.com'), stdin=subprocess.DEVNULL, capture_output=True,
                                        text=True, timeout=60, cwd=self.base, env=self.env(), start_new_session=True)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                self.assertNotIn(SECRET_PW, result.stdout + result.stderr)
        self.assertEqual({slug: self.journal(slug) for slug in ('alpha', 'beta', 'empty')}, before)
        code, report, _ = self.run_on_tty('--project', '--by', 'celina@example.com', password=SECRET_PW)  # control
        self.assertEqual(code, 0, report)

    def test_accepting_a_screen_with_a_problem_by_name_is_unknown_screen(self):
        self.page('alpha', s2=R_S2_V1 + R_S2_V1)
        before = self.journal('alpha')
        code, report, _ = self.run_on_tty('alpha', '--screen', 's2', '--by', 'celina@example.com', password=SECRET_PW)
        self.assertEqual((code, report['status'], report['error']), (2, 'BLOCKED', 'unknown_screen'))
        self.assertEqual(self.journal('alpha'), before)
        code, _report, _ = self.run_on_tty('alpha', '--screen', 's3', '--by', 'celina@example.com', password=SECRET_PW)
        self.assertEqual(code, 0, 'control: the healthy screen of the same prototype')

    def test_v1_prototype_needs_migration(self):
        code, report, _ = self.run_on_tty('old', '--all', '--by', 'celina@example.com', password=SECRET_PW)
        self.assertEqual((code, report['status'], report['error']), (2, 'BLOCKED', 'needs_migration'))
        self.assertFalse((self.proto_dir('old') / 'comments.json').exists())

    def test_argument_errors_are_blocked(self):
        for args in (('alpha', '--by', 'celina@example.com'), ('alpha', '--all', '--screen', 's2', '--by', 'celina@example.com'),
                     ('--project', 'alpha', '--by', 'celina@example.com')):
            with self.subTest(args=args):
                result = subprocess.run(self.argv(*args), capture_output=True, text=True, timeout=60, cwd=self.base,
                                        env=self.env(), start_new_session=True)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(self.accepts('alpha'), [])
        code, _report, _ = self.run_on_tty('alpha', '--all', '--by', 'celina@example.com', password=SECRET_PW)
        self.assertEqual(code, 0)  # control

    # -- konto (K42) -----------------------------------------------------------

    def test_must_change_is_blocked_before_the_password_prompt(self):
        code, report, terminal = self.run_on_tty('alpha', '--all', '--by', 'nowy@example.com', password=DEFAULT_PW)
        self.assertEqual((code, report['status'], report['error']), (2, 'BLOCKED', 'password_change_required'))
        self.assertNotIn('Hasło', terminal, 'no password prompt for an account that must change its password')
        self.assertEqual(self.accepts('alpha'), [])
        code, _report, terminal = self.run_on_tty('alpha', '--all', '--by', 'celina@example.com', password=SECRET_PW)  # control
        self.assertEqual(code, 0)
        self.assertIn('Hasło', terminal)

    def test_commenter_and_unknown_account_are_forbidden(self):
        for login in ('ala@example.com', 'nikt@example.com'):
            with self.subTest(login=login):
                code, report, terminal = self.run_on_tty('alpha', '--all', '--by', login, password=SECRET_PW)
                self.assertEqual((code, report['error']), (2, 'forbidden'))
                self.assertNotIn('Hasło', terminal)
        self.assertEqual(self.accepts('alpha'), [])

    def test_missing_users_file_is_forbidden(self):
        self.users_path.unlink()
        code, report, _ = self.run_on_tty('alpha', '--all', '--by', 'celina@example.com', password=SECRET_PW)
        self.assertEqual((code, report['error']), (2, 'forbidden'))

    # -- hasło nie wycieka ------------------------------------------------------

    def test_the_password_never_reaches_stdout_stderr_or_the_files(self):
        code, report, terminal = self.run_on_tty('alpha', '--all', '--by', 'celina@example.com', password=SECRET_PW)
        self.assertEqual(code, 0)
        everything = json.dumps(report) + terminal
        self.assertNotIn(SECRET_PW, everything)
        for path in self.proto_dir('alpha').rglob('*'):
            if path.is_file():
                self.assertNotIn(SECRET_PW.encode(), path.read_bytes(), str(path))
        # control of the check itself: a planted marker is found by the same search
        planted = everything + MARKER
        self.assertIn(MARKER, planted)
        with self.assertRaises(AssertionError):
            self.assertNotIn(MARKER, planted)
        code, report, _ = self.run_on_tty('beta', '--all', '--by', 'celina@example.com', password='Zle-' + MARKER)
        self.assertEqual(report['error'], 'invalid_credentials')
        self.assertNotIn(MARKER, json.dumps(report), 'a wrong password is not echoed in the report either')

    def test_the_password_has_no_other_source_than_the_terminal(self):
        for flag in (['--password', SECRET_PW], ['--password-stdin']):
            result = subprocess.run(self.argv('alpha', '--all', '--by', 'celina@example.com', *flag), input=SECRET_PW + '\n',
                                    capture_output=True, text=True, timeout=60, cwd=self.base, env=self.env(), start_new_session=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.accepts('alpha'), [])


class ListAndIndexAcceptanceTests(CliProject):
    def entry(self, slug):
        return [item for item in self.list()['prototypes'] if item['slug'] == slug][0]

    def gallery(self):
        self.proto('index')
        text = (self.protos / 'index.html').read_text(encoding='utf-8')
        cards = re.findall(r'<li class="card">(.*?)</li>', text, re.S)
        return dict((re.search(r'<dt>Slug</dt><dd><code>([^<]*)</code>', card).group(1),
                     re.search(r'<dt>Akceptacja</dt>\s*<dd>([^<]*)</dd>', card).group(1)) for card in cards)

    def test_v2_lists_screens_accepted_stale_and_v1_is_null(self):
        def per_screen(*states):
            return [{'id': screen, 'state': state} for screen, state in zip(('s2', 's3'), states)]
        self.assertEqual(self.entry('alpha')['acceptance'],
                         {'screens': 2, 'accepted': 0, 'stale': 0, 'perScreen': per_screen('pending', 'pending')}, 'control: nothing accepted')
        self.accept_ops('alpha', 's2')
        self.assertEqual(self.entry('alpha')['acceptance'],
                         {'screens': 2, 'accepted': 1, 'stale': 0, 'perScreen': per_screen('accepted', 'pending')})
        self.accept_ops('alpha', 's3')
        self.page('alpha', s2=R_S2_V2)  # s2 changed after the acceptance
        self.assertEqual(self.entry('alpha')['acceptance'],
                         {'screens': 2, 'accepted': 1, 'stale': 1, 'perScreen': per_screen('stale', 'accepted')})
        self.assertIsNone(self.entry('old')['acceptance'])
        self.assertEqual(self.entry('empty')['acceptance'], {'screens': 0, 'accepted': 0, 'stale': 0, 'perScreen': []})

    def test_per_screen_covers_every_screen_in_document_order_and_sums_match(self):
        self.accept_ops('alpha', 's2')
        self.accept_ops('alpha', 's3')
        self.page('alpha', s2=R_S2_V2)
        for slug in ('alpha', 'beta', 'empty'):
            acceptance = self.entry(slug)['acceptance']
            states = [item['state'] for item in acceptance['perScreen']]
            self.assertEqual(set(item for item in states), set(states) & {'accepted', 'stale', 'pending'}, slug)
            self.assertEqual(len(states), acceptance['screens'], slug)
            self.assertEqual((states.count('accepted'), states.count('stale')), (acceptance['accepted'], acceptance['stale']), slug)
            self.assertEqual(acceptance['accepted'] + acceptance['stale'] + states.count('pending'), acceptance['screens'], slug)
            self.assertTrue(all(set(item) == {'id', 'state'} for item in acceptance['perScreen']), 'no title')
        # document order, not alphabetical: swap the two sections in index.html
        path = self.proto_dir('alpha') / 'index.html'
        text = path.read_text(encoding='utf-8')
        self.assertEqual([item['id'] for item in self.entry('alpha')['acceptance']['perScreen']], ['s2', 's3'])
        self.assertIn(R_S2_V2 + R_S3, text, 'control: the fixture sections are adjacent')
        path.write_text(text.replace(R_S2_V2 + R_S3, R_S3 + R_S2_V2), encoding='utf-8')
        self.assertEqual([item['id'] for item in self.entry('alpha')['acceptance']['perScreen']], ['s3', 's2'])

    def test_gallery_render_ignores_per_screen(self):
        from skillsync_workflow import prototype as engine
        base = {'slug': 'x', 'title': 'X', 'requirements': 'r', 'created': 'c', 'generated_by': 'g', 'path': 'p',
                'screens': 2, 'comments': 0, 'commentsFormat': 2, 'threads': None, 'iteration': 1}
        with_field = dict(base, acceptance={'screens': 2, 'accepted': 1, 'stale': 0,
                                            'perScreen': [{'id': 's2', 'state': 'accepted'}, {'id': 's3', 'state': 'pending'}]})
        without = dict(base, acceptance={'screens': 2, 'accepted': 1, 'stale': 0})
        self.assertEqual(engine.render_gallery([with_field]), engine.render_gallery([without]))
        # skillsync_workflow has no dashboard module in this tree (dashboard.prototype_acceptance is DASH-1 work)

    def test_index_column_shows_k_of_n_and_a_dash_for_v1(self):
        self.accept_ops('alpha', 's2')
        self.accept_ops('beta', 's2', 's3')
        gallery = self.gallery()
        self.assertEqual((gallery['alpha'], gallery['beta'], gallery['empty'], gallery['old']), ('1/2', '2/2', '0/0', '—'))
        self.page('beta', s3=R_S3_V2)  # a stale acceptance does not count as accepted
        self.assertEqual(self.gallery()['beta'], '1/2')

    def test_only_new_fields_were_added_to_the_skippster_contract(self):
        entry = self.entry('alpha')
        base = {'slug', 'title', 'requirements', 'created', 'generated_by', 'path', 'screens', 'comments'}
        self.assertEqual(set(entry) - base, {'commentsFormat', 'threads', 'iteration', 'acceptance'})
        self.assertTrue(base <= set(entry), 'no contract field was removed')
        self.assertIsInstance(entry['screens'], int)
        self.assertIsInstance(entry['comments'], int)


# ═════════════════════════════════════════════════════════════════════════════
# F-INT-5: merge-comments z akceptacjami i łańcuch na prawdziwym szablonie biblioteki
# ═════════════════════════════════════════════════════════════════════════════

def journal_op(number, kind, payload, by='celina@example.com', at=None):
    return {'id': 'op-%026d' % number, 'type': kind, 'at': at or '2026-11-05T09:00:%02d.000Z' % number, 'by': by, 'payload': payload}


class MergeAcceptanceTests(CliProject):
    H2, H2B, H3 = 'a' * 64, 'b' * 64, 'c' * 64

    def operations_pair(self):
        mine = [journal_op(1, 'accept', acc_payload({'s2': self.H2}, via='cli', who='celina@example.com'), by='agent'),
                journal_op(3, 'accept', acc_payload({'s3': self.H3}), by='celina@example.com'),
                journal_op(5, 'restore', {'screen': 's2', 'from': 1, 'frozen': 2, 'reason': 'wróć', 'hashBefore': self.H2, 'hashAfter': self.H2B},
                           by='agent')]
        theirs = [journal_op(0, 'iteration', {'number': 1, 'path': 'iterations/1/', 'hash': 'd' * 64}, by='agent'),
                  journal_op(2, 'accept', acc_payload({'s2': self.H2B}), by='dorota@example.com'),
                  journal_op(4, 'unaccept', {'screens': ['s3']}, by='dorota@example.com')]
        return mine, theirs

    def put(self, slug, operations, name=None):
        text = ps.serialize_document(slug, operations)
        if name is None:
            (self.proto_dir(slug) / 'comments.json').write_text(text, encoding='utf-8')
        else:
            (self.root / name).write_text(text, encoding='utf-8')

    def acceptance_of(self, slug):
        return ps.fold_operations(self.operations(slug))['acceptance']

    def test_acceptance_state_after_the_merge_does_not_depend_on_the_order_of_the_arguments(self):
        mine, theirs = self.operations_pair()
        self.put('alpha', mine)
        self.put('alpha', theirs, 'theirs.json')
        report = self.proto('merge-comments', 'alpha', 'theirs.json')
        self.assertEqual((report['added'], report['total']), (3, 6))
        self.put('beta', theirs)
        self.put('beta', mine, 'mine.json')
        report = self.proto('merge-comments', 'beta', 'mine.json')
        self.assertEqual((report['added'], report['total']), (3, 6))
        left, right = self.acceptance_of('alpha'), self.acceptance_of('beta')
        self.assertEqual(left, right)
        self.assertEqual(sorted(item['id'] for item in self.operations('alpha')), sorted(item['id'] for item in self.operations('beta')))
        # contract: s2 was last accepted by the developer in the UI (t=2), s3 was accepted (t=3) and unaccepted (t=4)
        self.assertEqual(list(left), ['s2'])
        self.assertEqual((left['s2']['hash'], left['s2']['who'], left['s2']['via']), (self.H2B, 'dorota@example.com', 'ui'))
        # control: without the other side's operations the state is different (s3 accepted, s2 accepted by the CLI)
        alone = ps.fold_operations(mine)['acceptance']
        self.assertEqual((list(alone), alone['s2']['via']), (['s2', 's3'], 'cli'))
        # idempotent: the same merge again adds nothing and keeps the state
        again = self.proto('merge-comments', 'alpha', 'theirs.json')
        self.assertEqual(again['added'], 0)
        self.assertEqual(self.acceptance_of('alpha'), left)

    def test_accept_operations_keep_their_bytes_and_a_conflicting_copy_is_refused(self):
        mine, theirs = self.operations_pair()
        self.put('alpha', mine)
        before = self.journal('alpha')
        changed = dict(mine[0], at='2026-11-05T09:30:00.000Z')  # the same id with other content
        self.put('alpha', [changed], 'theirs.json')
        report = self.proto('merge-comments', 'alpha', 'theirs.json', code=2)
        self.assertEqual(report['error'], 'merge_conflict')
        self.assertEqual(self.journal('alpha'), before, 'a conflict writes nothing')
        self.put('alpha', [mine[0]], 'theirs.json')  # control: the identical operation is no conflict
        self.assertEqual(self.proto('merge-comments', 'alpha', 'theirs.json')['added'], 0)


LIBRARY = os.environ.get('SKILLSYNC_TEST_LIBRARY')


@unittest.skipUnless(LIBRARY and POSIX, 'set SKILLSYNC_TEST_LIBRARY to a library with the real prototype template')
class RealTemplateChainTests(PrototypeFixture):
    """init -> two screens -> ids -> iterate -> change -> restore -> acceptance -> gate on the template of the library."""

    def setUp(self):
        super().setUp()
        self.real_assets = Path(LIBRARY) / 'skills' / 'prototype' / 'assets'
        make_project_users(self.root, [('ala.kot@example.com', 'acceptor', SECRET_PW)])

    def index_path(self):
        return self.proto_dir('real') / 'index.html'

    def screen_section(self, number, text):
        return ('<section class="screen" id="s%d">\n  <div class="frame"><h1>Ekran %d</h1><p>%s</p>'
                '<button type="button">Zapisz %d</button></div>\n</section>\n' % (number, number, text, number))

    def write_screens(self, s2='Treść drugiego ekranu', s3='Treść trzeciego ekranu'):
        text = self.index_path().read_text(encoding='utf-8')
        start = text.index('<section class="screen" id="s1"')
        end = text.index('</section>', start) + len('</section>\n')
        text = text[:start] + text[start:end].rstrip('\n') + '\n' + self.screen_section(2, s2) + self.screen_section(3, s3) + text[end:].lstrip('\n')
        self.index_path().write_text(text, encoding='utf-8')

    def change_screen(self, old, new):
        text = self.index_path().read_text(encoding='utf-8')
        self.assertIn(old, text)
        self.index_path().write_text(text.replace(old, new), encoding='utf-8')

    def accept_current(self):
        directory = self.proto_dir('real')
        hashes = ps.screen_hashes(str(directory))['screens']
        self.assertEqual(sorted(hashes), ['s2', 's3'], 'the example screen s1 is not a screen to accept')
        ps.CommentsStore(str(directory), 'real').append([{'type': 'accept', 'by': 'agent', 'payload': {
            'scope': 'all', 'screens': hashes, 'iteration': 2, 'via': 'cli', 'who': 'ala.kot@example.com'}}])

    def gate(self, code):
        path = self.root / 'docs' / 'plans' / 'plan.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# Plan\n\nSpec: docs/specs/x.md\nPrototyp: docs/prototypes/real\n\n## Etap 1\nTreść.\n', encoding='utf-8')
        return self.proto('gate', '--plan', 'docs/plans/plan.md', code=code)

    def test_the_whole_chain_ends_with_a_gate_that_follows_the_screens(self):
        self.proto('init', 'real', '--requirements', 'docs/req.md', '--assets', str(self.real_assets), assets=False)
        self.write_screens()
        self.proto('ids', 'real', '--fix')
        self.assertEqual(self.proto('ids', 'real', '--check')['status'], 'PASS')
        self.assertEqual(self.proto('iterate', 'real')['iteration'], 1)
        self.change_screen('Treść drugiego ekranu', 'Zmieniona treść drugiego ekranu')
        self.proto('ids', 'real', '--fix')
        restored = self.proto('restore', 'real', '--screen', 's2', '--from', '1', '--reason', 'wróć do pierwszej wersji', '--who', 'ala.kot@example.com')
        self.assertEqual(restored['status'], 'PASS')
        self.assertIn('Treść drugiego ekranu', self.index_path().read_text(encoding='utf-8'))
        self.assertNotIn('Zmieniona treść', self.index_path().read_text(encoding='utf-8'), 'the restore brought the screen back')
        before_accept = self.gate(1)
        self.assertEqual([(item['screen'], item['reason']) for item in before_accept['missing']], [('s2', 'brak'), ('s3', 'brak')])
        self.accept_current()
        report = self.gate(0)
        self.assertEqual((report['status'], report['missing']), ('PASS', []))
        self.assertEqual([(item['screen'], item['via'], item['who']) for item in report['acceptances']],
                         [('s2', 'cli', 'ala.kot@example.com'), ('s3', 'cli', 'ala.kot@example.com')])
        self.assertEqual(report['prototypes'][0]['format'], 2)
        # a change of one screen after the acceptance: FAIL for that screen only
        self.change_screen('Treść trzeciego ekranu', 'Inna treść trzeciego ekranu')
        stale = self.gate(1)
        self.assertEqual(stale['missing'], [{'slug': 'real', 'path': 'docs/prototypes/real', 'screen': 's3', 'reason': 'nieaktualna'}])
        self.assertEqual([item['screen'] for item in stale['acceptances']], ['s2'])
        self.change_screen('Inna treść trzeciego ekranu', 'Treść trzeciego ekranu')  # control: restoring the text restores the PASS
        self.assertEqual(self.gate(0)['status'], 'PASS')
        listed = [item for item in self.list()['prototypes'] if item['slug'] == 'real'][0]
        self.assertEqual(listed['acceptance'], {'screens': 2, 'accepted': 2, 'stale': 0, 'perScreen': [
            {'id': 's2', 'state': 'accepted'}, {'id': 's3', 'state': 'accepted'}]})


# ═════════════════════════════════════════════════════════════════════════════
# Bramka `gate` (K26, kontrakt G)
# ═════════════════════════════════════════════════════════════════════════════

BYPASS = 'Bramka prototypu: pominięta — pilna poprawka produkcyjna — Ola, 2026-11-05'
PLAN = 'docs/plans/plan.md'


class GateCase(CliProject):
    """Plany leżą w `docs/plans/` projektu (ścieżka `--plan` musi być wewnątrz projektu)."""

    def plan(self, header_lines=(), body='## Etap 1\nTreść etapu.\n', name='plan.md'):
        text = '# Plan\n\nSpec: docs/specs/x.md\n' + ''.join(line + '\n' for line in header_lines) + '\n' + body
        path = self.root / 'docs' / 'plans' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return 'docs/plans/' + name

    def gate(self, header_lines=(), code=0, body='## Etap 1\nTreść etapu.\n'):
        return self.proto('gate', '--plan', self.plan(header_lines, body), code=code)

    def accept_all(self, slug, **kwargs):
        self.accept_ops(slug, 's2', 's3', **kwargs)

    def line(self, slug):
        return 'Prototyp: docs/prototypes/%s' % slug


class GateTests(GateCase):
    def test_plan_without_a_prototype_line_passes_with_a_note(self):
        report = self.gate()
        self.assertEqual((report['status'], report['prototypes'], report['missing'], report['acceptances']), ('PASS', [], [], []))
        self.assertEqual(report['note'], 'plan bez linii Prototyp:')
        self.assertEqual(report['plan'], PLAN)
        self.assertNotIn('bypassed', report)
        with_line = self.gate([self.line('alpha')], code=1)  # control: a line makes the gate look at the prototype
        self.assertNotIn('note', with_line)

    def test_complete_current_acceptances_pass_with_who_at_via_iteration(self):
        self.accept_ops('alpha', 's2', who='celina@example.com', via='cli', iteration=1)
        self.accept_ops('alpha', 's3', who='dorota@example.com', via='ui', iteration=2)
        report = self.gate([self.line('alpha')])
        self.assertEqual((report['status'], report['missing']), ('PASS', []))
        self.assertEqual([item['screen'] for item in report['acceptances']], ['s2', 's3'])
        first, second = report['acceptances']
        self.assertEqual((first['slug'], first['who'], first['via'], first['iteration']), ('alpha', 'celina@example.com', 'cli', 1))
        self.assertEqual((second['who'], second['via'], second['iteration']), ('dorota@example.com', 'ui', 2))
        self.assertRegex(first['at'], r'^\d{4}-\d\d-\d\dT')
        self.assertEqual(set(first), {'slug', 'screen', 'who', 'at', 'via', 'iteration'})
        self.assertEqual(report['prototypes'], [{'path': 'docs/prototypes/alpha', 'slug': 'alpha', 'format': 2,
                                                 'iteration': 1, 'screens': 2}])

    def test_missing_and_stale_acceptances_fail_with_exit_1(self):
        self.accept_ops('alpha', 's2')
        self.accept_ops('alpha', 's3')
        self.accept_ops('beta', 's2')
        self.page('alpha', s2=R_S2_V2)  # s2 changed after the acceptance
        report = self.gate([self.line('alpha'), self.line('beta')], code=1)
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(report['missing'], [
            {'slug': 'alpha', 'path': 'docs/prototypes/alpha', 'screen': 's2', 'reason': 'nieaktualna'},
            {'slug': 'beta', 'path': 'docs/prototypes/beta', 'screen': 's3', 'reason': 'brak'}])
        self.assertEqual([(item['slug'], item['screen']) for item in report['acceptances']], [('alpha', 's3'), ('beta', 's2')],
                         'only current acceptances are listed')
        # control: accepting the missing and the changed screens turns the same plan into a PASS
        self.accept_ops('alpha', 's2')
        self.accept_ops('beta', 's3')
        self.assertEqual(self.gate([self.line('alpha'), self.line('beta')])['status'], 'PASS')

    def test_the_example_screen_is_skipped(self):
        report = self.gate([self.line('beta')], code=1)
        self.assertEqual([item['screen'] for item in report['missing']], ['s2', 's3'], 's1 (example) is not required')
        self.accept_all('beta')
        report = self.gate([self.line('beta')])
        self.assertEqual(report['status'], 'PASS')
        self.assertNotIn('s1', [item['screen'] for item in report['acceptances']])

    def test_v1_prototype_is_missing_with_needs_migration(self):
        report = self.gate([self.line('old')], code=1)
        self.assertEqual(report['missing'], [{'slug': 'old', 'path': 'docs/prototypes/old', 'screen': None, 'reason': 'needs_migration'}])
        self.assertEqual(report['prototypes'][0]['format'], 1)
        self.accept_all('alpha')
        self.assertEqual(self.gate([self.line('alpha')])['status'], 'PASS')  # control: a v2 prototype is fine

    def test_v2_prototype_without_screens_fails_with_brak_ekranow(self):
        report = self.gate([self.line('empty')], code=1)
        self.assertEqual(report['missing'], [{'slug': 'empty', 'path': 'docs/prototypes/empty', 'screen': None, 'reason': 'brak_ekranow'}])
        self.assertEqual(report['prototypes'][0]['screens'], 0)
        self.page('empty')  # control: with screens it asks for their acceptance instead
        self.accept_all('empty')
        self.assertEqual(self.gate([self.line('empty')])['status'], 'PASS')

    def test_unaccepted_after_unaccept_and_reaccept_follow_the_last_operation(self):
        self.accept_all('alpha')
        ps.CommentsStore(str(self.proto_dir('alpha')), 'alpha').append([{'type': 'unaccept', 'by': 'celina@example.com', 'payload': {'screens': ['s3']}}])
        report = self.gate([self.line('alpha')], code=1)
        self.assertEqual([(item['screen'], item['reason']) for item in report['missing']], [('s3', 'brak')])
        self.accept_ops('alpha', 's3')
        self.assertEqual(self.gate([self.line('alpha')])['status'], 'PASS')

    def test_prototype_path_may_be_in_backticks(self):
        self.accept_all('alpha')
        report = self.gate(['Prototyp: `docs/prototypes/alpha`'])
        self.assertEqual(report['prototypes'][0]['slug'], 'alpha')

    def test_ambiguous_screens_fail_with_niejednoznaczny_ekran_and_the_repaired_html_passes(self):
        cases = {'duplicate id': page_for('alpha', R_S2_V1 + R_S2_V1, R_S3),
                 'unclosed section': page_for('alpha', R_S2_V1, R_S3.replace('</section>\n', ''))}
        healthy = {'duplicate id': 's3', 'unclosed section': 's2'}
        broken_screen = {'duplicate id': 's2', 'unclosed section': 's3'}
        for name, html in cases.items():
            with self.subTest(name):
                (self.proto_dir('alpha') / 'index.html').write_text(html, encoding='utf-8')
                self.accept_ops('alpha', healthy[name])  # the screen that can be hashed is accepted
                report = self.gate([self.line('alpha')], code=1)
                self.assertEqual(report['status'], 'FAIL')
                self.assertEqual(report['missing'], [{'slug': 'alpha', 'path': 'docs/prototypes/alpha',
                                                      'screen': broken_screen[name], 'reason': 'niejednoznaczny_ekran'}])
                self.assertEqual([item['screen'] for item in report['acceptances']], [healthy[name]])
                # control: the repaired HTML (no problem) with both screens accepted passes
                self.page('alpha')
                self.accept_all('alpha')
                fixed = self.gate([self.line('alpha')])
                self.assertEqual((fixed['status'], fixed['missing']), ('PASS', []))
                self.assertEqual([item['screen'] for item in fixed['acceptances']], ['s2', 's3'])
                # reset the journal for the next case
                (self.proto_dir('alpha') / 'comments.json').write_text(ps.serialize_document('alpha', []), encoding='utf-8')

    def test_only_ambiguous_screens_is_not_reported_as_brak_ekranow(self):
        (self.proto_dir('beta') / 'index.html').write_text(page_for('beta', R_S2_V1 + R_S2_V1, ''), encoding='utf-8')
        report = self.gate([self.line('beta')], code=1)
        self.assertEqual([(item['screen'], item['reason']) for item in report['missing']], [('s2', 'niejednoznaczny_ekran')])
        self.page('empty', screens=False)
        self.assertEqual(self.gate([self.line('empty')], code=1)['missing'][0]['reason'], 'brak_ekranow', 'control: no screens at all')

    def test_slug_form_reports_ambiguous_screens_too(self):
        self.page('alpha', s2=R_S2_V1 + R_S2_V1)
        self.accept_ops('alpha', 's3')
        report = self.proto('gate', 'alpha', code=1)
        self.assertEqual([(item['screen'], item['reason']) for item in report['missing']], [('s2', 'niejednoznaczny_ekran')])

    def test_bypass_date_must_be_a_real_ascii_date(self):
        def line(date):
            return 'Bramka prototypu: pominięta — pilne — Ola, %s' % date
        for date in ('2026-13-45', '2026-02-30', '2026-02-29', '2026-00-10', '2026-11-00', '２０２６-１１-０５',
                     '٢٠٢٦-١١-٠٥', '2026-11-5', '26-11-05'):
            with self.subTest(date=date):
                report = self.gate([self.line('alpha'), line(date)], code=2)
                self.assertEqual((report['status'], report['error']), ('BLOCKED', 'invalid_bypass_line'))
        for date in ('2026-11-05', '2028-02-29', '2026-12-31'):
            with self.subTest(valid=date):
                report = self.gate([self.line('alpha'), line(date)])
                self.assertEqual((report['status'], report['bypassed']['date']), ('PASS', date))

    def test_near_miss_lines_are_blocked_and_ordinary_header_lines_are_not(self):
        proto = {
            'English spelling': 'Prototype: docs/prototypes/alpha', 'space before the colon': 'Prototyp : docs/prototypes/alpha',
            'list item': '- Prototyp: docs/prototypes/alpha', 'bold': '**Prototyp:** docs/prototypes/alpha',
            'lower case': 'prototyp: docs/prototypes/alpha', 'asterisk list': '* Prototyp: docs/prototypes/alpha',
            'quote': '> Prototyp: x', 'numbered list': '1. Prototyp: x', 'numbered with a parenthesis': '2) Prototyp: x',
            'italic': '_Prototyp:_ x',
        }
        bypass = {
            'no colon': 'Bramka prototypu pominięta — pilne — Ola, 2026-11-05',
            'list item': '- Bramka prototypu: pominięta — pilne — Ola, 2026-11-05',
            'lower case': 'bramka prototypu: pominięta — pilne — Ola, 2026-11-05',
            'bold': '**Bramka prototypu:** pominięta — pilne — Ola, 2026-11-05',
        }
        for name, line in proto.items():
            with self.subTest(prototype=name):
                report = self.gate([line], code=2)
                self.assertEqual((report['status'], report['error']), ('BLOCKED', 'invalid_prototype_line'), report)
        for name, line in bypass.items():
            with self.subTest(bypass=name):
                report = self.gate([line], code=2)
                self.assertEqual((report['status'], report['error']), ('BLOCKED', 'invalid_bypass_line'), report)
        ordinary = ['Przebieg: przygotowanie prototypu i testy', 'Spec: docs/specs/x.md', 'Repozytoria: a, prototyp b',
                    'Prototypy zrobione.', 'Status: bramka prototypu przechodzi', 'Autor: Ola']
        for line in ordinary:
            with self.subTest(ordinary=line):
                report = self.gate([line])
                self.assertEqual((report['status'], report['prototypes']), ('PASS', []), report)
                self.assertEqual(report['note'], 'plan bez linii Prototyp:')
        self.assertEqual(self.gate(['Prototyp: docs/prototypes/alpha'], code=1)['status'], 'FAIL', 'control: the exact line')

    def html_of(self, slug, text):
        (self.proto_dir(slug) / 'index.html').write_text(text.replace('data-prototype-id="demo"', 'data-prototype-id="%s"' % slug), encoding='utf-8')

    def test_a_problem_made_only_of_examples_is_not_reported_and_a_mixed_one_is(self):
        self.html_of('alpha', R_HEAD + R_S1 + R_S1 + R_S2_V1 + R_S3 + R_TAIL)  # two example sections with the same id
        self.accept_all('alpha')
        report = self.gate([self.line('alpha')])
        self.assertEqual((report['status'], report['missing']), ('PASS', []), 'examples are never required, even ambiguous ones')
        self.assertEqual([item['screen'] for item in report['acceptances']], ['s2', 's3'])
        mixed = R_S1.replace('id="s1" data-prototype-example', 'id="s2" data-prototype-example')  # an example and a regular s2
        self.html_of('alpha', R_HEAD + mixed + R_S2_V1 + R_S3 + R_TAIL)
        report = self.gate([self.line('alpha')], code=1)
        self.assertEqual([(item['screen'], item['reason']) for item in report['missing']], [('s2', 'niejednoznaczny_ekran')],
                         'control: an example next to a regular screen with the same id is still ambiguous')

    def test_a_screen_section_without_an_id_fails_with_a_null_screen(self):
        no_id = '<section class="screen">\n  <div class="frame">Bez id</div>\n</section>\n'
        self.html_of('alpha', R_HEAD + R_S1 + R_S2_V1 + no_id + R_S3 + R_TAIL)
        self.accept_all('alpha')
        report = self.gate([self.line('alpha')], code=1)
        self.assertEqual(report['missing'], [{'slug': 'alpha', 'path': 'docs/prototypes/alpha', 'screen': None,
                                              'reason': 'niejednoznaczny_ekran'}])
        self.page('alpha')  # control: the same prototype with the section repaired
        self.accept_all('alpha')
        self.assertEqual(self.gate([self.line('alpha')])['status'], 'PASS')

    def test_headings_and_similar_words_are_not_gate_lines(self):
        ordinary = ['# Prototyp: nowy ekran planu', '### Prototyp: szczegóły', 'Prototypy: nie dotyczy', 'Prototypowanie: etap 2',
                    'prototypy: brak', '  ## Prototyp: zagnieżdżony']
        for line in ordinary:
            with self.subTest(line=line):
                report = self.gate([line])
                self.assertEqual((report['status'], report['prototypes']), ('PASS', []), report)
                self.assertEqual(report['note'], 'plan bez linii Prototyp:')
        for line in ('- Prototyp: docs/prototypes/alpha', 'Prototype: docs/prototypes/alpha'):  # control: still blocked
            with self.subTest(blocked=line):
                self.assertEqual(self.gate([line], code=2)['error'], 'invalid_prototype_line')

    def test_a_heading_with_the_bypass_words_is_not_a_bypass(self):
        title = '# Bramka prototypu: pominięta — x — Ola, 2026-11-05'
        report = self.gate([self.line('alpha'), title], code=1)
        self.assertEqual(report['status'], 'FAIL')
        self.assertNotIn('bypassed', report, 'a title is not the bypass line')
        report = self.gate([self.line('alpha'), BYPASS])  # control: the exact line bypasses
        self.assertEqual((report['status'], 'bypassed' in report), ('PASS', True))

    def test_a_plan_with_only_single_hash_headings_is_read_as_a_whole(self):
        body = '# Etap 1\nTreść.\n# Etap 2\nTreść.\n'
        path = self.root / 'docs' / 'plans' / 'plan.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# Prototyp: tytuł planu\nSpec: docs/specs/x.md\n' + body, encoding='utf-8')  # no `## ` anywhere
        report = self.proto('gate', '--plan', PLAN)
        self.assertEqual((report['status'], report['prototypes']), ('PASS', []), 'the title line is not a gate line')
        path.write_text('# Prototyp: tytuł planu\nSpec: docs/specs/x.md\nPrototyp: docs/prototypes/alpha\n' + body, encoding='utf-8')
        report = self.proto('gate', '--plan', PLAN, code=1)
        self.assertEqual([item['slug'] for item in report['prototypes']], ['alpha'], 'control: the exact line is found among the headings')

    # -- obejście ------------------------------------------------------------

    def test_bypass_turns_fail_into_pass_and_keeps_missing_as_information(self):
        self.assertEqual(self.gate([self.line('alpha')], code=1)['status'], 'FAIL', 'control: without the bypass it fails')
        report = self.gate([self.line('alpha'), BYPASS])
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['bypassed'], {'reason': 'pilna poprawka produkcyjna', 'by': 'Ola', 'date': '2026-11-05'})
        self.assertEqual([item['screen'] for item in report['missing']], ['s2', 's3'], 'missing stays as information')

    def test_bypass_accepts_any_person_name_or_nick(self):
        for person in ('Marek', 'Anna Nowak', 'jan.k', 'Ola'):
            with self.subTest(person=person):
                line = 'Bramka prototypu: pominięta — pilne — a — b — %s, 2026-11-05' % person
                report = self.gate([self.line('alpha'), line])
                self.assertEqual(report['status'], 'PASS')
                self.assertEqual(report['bypassed'], {'reason': 'pilne — a — b', 'by': person, 'date': '2026-11-05'})

    def test_bypass_without_a_prototype_line_passes(self):
        report = self.gate([BYPASS])
        self.assertEqual((report['status'], report['prototypes']), ('PASS', []))
        self.assertEqual(report['bypassed']['date'], '2026-11-05')
        self.assertNotIn('note', report)

    def test_malformed_bypass_lines_are_blocked_with_exit_2(self):
        bad = {
            'hyphen instead of the dash': 'Bramka prototypu: pominięta - pilne - Ola, 2026-11-05',
            'en dash': 'Bramka prototypu: pominięta – pilne – Ola, 2026-11-05',
            'no date': 'Bramka prototypu: pominięta — pilne — Ola',
            'malformed date': 'Bramka prototypu: pominięta — pilne — Ola, 05.11.2026',
            'no person': 'Bramka prototypu: pominięta — pilne — , 2026-11-05',
            'comma in the person': 'Bramka prototypu: pominięta — pilne — Kowalski, Jan, 2026-11-05',
            'no reason': 'Bramka prototypu: pominięta —  — Ola, 2026-11-05',
            'other wording': 'Bramka prototypu: wyłączona — pilne — Ola, 2026-11-05',
            'trailing text': BYPASS + ' ok',
        }
        for name, line in bad.items():
            with self.subTest(name):
                report = self.gate([self.line('alpha'), line], code=2)
                self.assertEqual((report['status'], report['error']), ('BLOCKED', 'invalid_bypass_line'))
        two = self.gate([BYPASS, BYPASS.replace('pilna', 'inna')], code=2)
        self.assertEqual(two['error'], 'invalid_bypass_line', 'two bypass lines')
        self.assertEqual(self.gate([self.line('alpha'), BYPASS])['status'], 'PASS', 'control: the exact shape')

    def test_a_bypass_line_after_the_first_heading_is_ignored(self):
        report = self.gate([self.line('alpha')], code=1, body='## Etap 1\n' + BYPASS + '\nBramka prototypu: zepsuta\n')
        self.assertNotIn('bypassed', report)
        self.assertEqual(report['status'], 'FAIL')

    # -- linia Prototyp: ----------------------------------------------------------

    def test_bad_prototype_line_or_path_is_blocked(self):
        outside = self.base / 'poza'
        (outside / 'proto').mkdir(parents=True)
        (outside / 'proto' / 'prototype.json').write_text('{}', encoding='utf-8')
        os.symlink(outside / 'proto', self.protos / 'wyjscie')
        (self.protos / 'bezjsona').mkdir()
        cases = {
            'two paths': ('Prototyp: docs/prototypes/alpha docs/prototypes/beta', 'invalid_prototype_line'),
            'parent directory': ('Prototyp: ../x', 'prototype_not_found'),
            'escape through ..': ('Prototyp: docs/prototypes/../../../poza/proto', 'prototype_not_found'),
            'absolute outside': ('Prototyp: %s' % (outside / 'proto'), 'prototype_not_found'),
            'symlink outside': ('Prototyp: docs/prototypes/wyjscie', 'prototype_not_found'),
            'does not exist': ('Prototyp: docs/prototypes/nie-ma', 'prototype_not_found'),
            'no prototype.json': ('Prototyp: docs/prototypes/bezjsona', 'prototype_not_found'),
            'a file': ('Prototyp: docs/plans/plan.md', 'prototype_not_found'),
            'empty value': ('Prototyp:', 'invalid_prototype_line'),
        }
        for name, (line, error) in cases.items():
            with self.subTest(name):
                report = self.gate([line], code=2)
                self.assertEqual((report['status'], report['error']), ('BLOCKED', error), report)
        self.accept_all('alpha')
        self.assertEqual(self.gate([self.line('alpha')])['status'], 'PASS', 'control: a proper line')

    def test_prototype_line_after_the_first_heading_is_ignored(self):
        report = self.gate([], body='## Etap 1\n' + self.line('alpha') + '\nPrototyp: ../x oraz drugi\n')
        self.assertEqual((report['status'], report['prototypes']), ('PASS', []))
        self.assertEqual(report['note'], 'plan bez linii Prototyp:')
        self.assertEqual(self.gate([self.line('alpha')], code=1)['status'], 'FAIL', 'control: the same line in the header counts')

    def test_duplicate_lines_count_once_in_the_order_of_first_appearance(self):
        report = self.gate([self.line('beta'), self.line('alpha'), self.line('beta'), 'Prototyp: `docs/prototypes/alpha`'], code=1)
        self.assertEqual([item['slug'] for item in report['prototypes']], ['beta', 'alpha'])
        self.assertEqual(len(report['missing']), 4, 'two screens of each prototype, no repeats')
        single = self.gate([self.line('beta'), self.line('alpha')], code=1)
        self.assertEqual(single['missing'], report['missing'], 'duplicates change nothing')
        self.assertEqual([item['slug'] for item in single['prototypes']], ['beta', 'alpha'])

    def test_unreadable_plan_and_plan_outside_the_project(self):
        report = self.proto('gate', '--plan', 'docs/plans/nie-ma.md', code=2)
        self.assertEqual(report['status'], 'BLOCKED')
        outside = self.base / 'obcy.md'
        outside.write_text('# Plan\nPrototyp: docs/prototypes/alpha\n', encoding='utf-8')
        self.proto('gate', '--plan', '../obcy.md', code=2)
        self.proto('gate', '--plan', str(outside), code=2)
        self.assertEqual(self.proto('gate', '--plan', self.plan(), code=0)['status'], 'PASS')  # control

    # -- forma <slug> ------------------------------------------------------------

    def test_slug_form_uses_the_prototype_root(self):
        self.accept_all('alpha')
        report = self.proto('gate', 'alpha')
        self.assertEqual((report['status'], report['missing']), ('PASS', []))
        self.assertNotIn('plan', report)
        report = self.proto('gate', 'alpha', 'beta', code=1)
        self.assertEqual([(item['slug'], item['reason']) for item in report['missing']], [('beta', 'brak'), ('beta', 'brak')])

    def test_unknown_slug_and_wrong_argument_combinations_are_blocked(self):
        report = self.proto('gate', 'alpha', 'nie-ma', code=2)
        self.assertEqual((report['status'], report['error']), ('BLOCKED', 'prototype_not_found'))
        self.proto('gate', code=2)  # neither a plan nor a slug
        self.proto('gate', 'alpha', '--plan', self.plan(), code=2)  # both
        self.proto('gate', '../alpha', code=2)
        self.accept_all('alpha')
        self.assertEqual(self.proto('gate', 'alpha')['status'], 'PASS')  # control

    def test_gate_does_not_write(self):
        before = {slug: self.journal(slug) for slug in ('alpha', 'beta', 'old')}
        self.gate([self.line('alpha'), self.line('old')], code=1)
        self.assertEqual({slug: self.journal(slug) for slug in ('alpha', 'beta', 'old')}, before)


# ═════════════════════════════════════════════════════════════════════════════
# Bramka per faza: `gate --phase`, tabela stanów U/P/B (spec feat-demo, K5, K6, K8)
# ═════════════════════════════════════════════════════════════════════════════

UI_TABLE = '## Kroki\n\n| Id | Krok |\n|---|---|\n| F1 [UI] | Ekran |\n| F2 | Backend |\n'
PLAIN_TABLE = '## Kroki\n\n| Id | Krok |\n|---|---|\n| F1 | Kod |\n| F2 | Backend |\n'
NO_TABLE = '## Etap 1\nTreść etapu.\n'


class GatePhaseTests(GateCase):
    """Każdy wiersz tabeli stanów (U = plan ma fazę `[UI]`, P = linia `Prototyp:`, B = obejście) to osobny przypadek;
    kolumny: bez `--phase`, faza `[UI]` (F1), faza bez `[UI]` (F2), nieznana (F9)."""

    def run_gate(self, header, body, phase=None, code=0):
        args = ['gate', '--plan', self.plan(header, body)]
        if phase is not None:
            args += ['--phase', phase]
        return self.proto(*args, code=code)

    def assert_blocked(self, report, error):
        self.assertEqual((report['status'], report['error']), ('BLOCKED', error))

    def assert_unknown_phase(self, header, body):
        report = self.run_gate(header, body, 'F9', code=2)
        self.assert_blocked(report, 'unknown_phase')
        self.assertTrue(report['reason'].startswith('unknown_phase: '))
        self.assertEqual(report['plan'], PLAN)
        return report

    # -- U = nie ------------------------------------------------------------------

    def test_row_no_ui_no_prototype_line(self):
        for body in (NO_TABLE, PLAIN_TABLE):
            with self.subTest(body=body[:12]):
                whole = self.run_gate([], body)
                self.assertEqual((whole['status'], whole['note']), ('PASS', 'plan bez linii Prototyp:'))
                self.assertNotIn('phase', whole)
                self.assertNotIn('uiPhases', whole)
                self.assertNotIn('phases', whole)
                self.assert_unknown_phase([], body)
        known = self.run_gate([], PLAIN_TABLE, 'F2')
        self.assertEqual((known['status'], known['phase'], known['note']), ('PASS', 'F2', 'plan bez linii Prototyp:'))
        self.assertNotIn('uiPhases', known)

    def test_row_no_ui_prototype_line_gives_the_plan_result(self):
        header = [self.line('alpha')]
        whole = self.run_gate(header, PLAIN_TABLE, code=1)
        self.assertEqual(whole['status'], 'FAIL')
        self.assertNotIn('phase', whole)
        self.assertNotIn('uiPhases', whole)
        phased = self.run_gate(header, PLAIN_TABLE, 'F2', code=1)  # K8: the whole plan stays behind the gate
        self.assertEqual((phased['status'], phased['phase'], phased['missing']), ('FAIL', 'F2', whole['missing']))
        self.assertNotIn('uiPhases', phased)
        self.assert_unknown_phase(header, PLAIN_TABLE)
        self.assert_unknown_phase(header, NO_TABLE)
        self.accept_all('alpha')
        ok = self.run_gate(header, PLAIN_TABLE, 'F1')
        self.assertEqual((ok['status'], ok['phase'], len(ok['acceptances'])), ('PASS', 'F1', 2))

    def test_no_ui_with_the_bypass_turns_the_fail_into_a_pass(self):
        header = [self.line('alpha'), BYPASS]
        for phase in (None, 'F1'):
            with self.subTest(phase=phase):
                report = self.run_gate(header, PLAIN_TABLE, phase)
                self.assertEqual(report['status'], 'PASS')
                self.assertEqual(report['bypassed']['by'], 'Ola')
                self.assertEqual(report.get('phase'), phase)

    # -- U = tak, P = nie ----------------------------------------------------------

    def test_row_ui_without_prototype_line_and_bypass_is_blocked_in_every_mode(self):
        for phase in (None, 'F1', 'F2'):
            with self.subTest(phase=phase):
                report = self.run_gate([], UI_TABLE, phase, code=2)
                self.assert_blocked(report, 'ui_phase_without_prototype')
                self.assertEqual(report['uiPhases'], ['F1'])
                self.assertEqual(report['phases'], ['F1', 'F2'])
                self.assertEqual(report.get('phase'), phase)
        report = self.assert_unknown_phase([], UI_TABLE)  # an unknown phase is reported first
        self.assertEqual((report['uiPhases'], report['phases']), (['F1'], ['F1', 'F2']))  # K6: also with --phase

    def test_row_ui_without_prototype_line_with_the_bypass_passes_with_bypassed(self):
        for phase in (None, 'F1', 'F2'):
            with self.subTest(phase=phase):
                report = self.run_gate([BYPASS], UI_TABLE, phase)
                self.assertEqual(report['status'], 'PASS')
                self.assertEqual(report['bypassed'], {'reason': 'pilna poprawka produkcyjna', 'by': 'Ola', 'date': '2026-11-05'})
                self.assertEqual((report['uiPhases'], report['phases']), (['F1'], ['F1', 'F2']))
                self.assertEqual(report.get('phase'), phase)
        self.assert_unknown_phase([BYPASS], UI_TABLE)

    # -- U = tak, P = tak ----------------------------------------------------------

    def test_row_ui_prototype_line_without_bypass(self):
        header = [self.line('alpha')]
        for phase in (None, 'F1'):
            with self.subTest(phase=phase):
                report = self.run_gate(header, UI_TABLE, phase, code=1)
                self.assertEqual(report['status'], 'FAIL')
                self.assertEqual([item['screen'] for item in report['missing']], ['s2', 's3'])
                self.assertEqual((report['uiPhases'], report['phases']), (['F1'], ['F1', 'F2']))
                self.assertEqual(report.get('phase'), phase)
        non_ui = self.run_gate(header, UI_TABLE, 'F2')  # exit 0 although the screens are not accepted
        self.assertEqual((non_ui['status'], non_ui['prototypes'], non_ui['missing'], non_ui['acceptances']), ('PASS', [], [], []))
        self.assertEqual((non_ui['phase'], non_ui['note']), ('F2', 'faza bez znacznika [UI]'))
        self.assertEqual((non_ui['uiPhases'], non_ui['phases']), (['F1'], ['F1', 'F2']))
        self.assert_unknown_phase(header, UI_TABLE)
        self.accept_all('alpha')  # control: accepted screens pass the [UI] phase too
        self.assertEqual(self.run_gate(header, UI_TABLE, 'F1')['status'], 'PASS')

    def test_row_ui_prototype_line_with_the_bypass_passes_with_bypassed(self):
        header = [self.line('alpha'), BYPASS]
        for phase in (None, 'F1', 'F2'):
            with self.subTest(phase=phase):
                report = self.run_gate(header, UI_TABLE, phase)
                self.assertEqual(report['status'], 'PASS')
                self.assertEqual(report['bypassed']['by'], 'Ola')
                self.assertNotIn('note', report)
                self.assertEqual((report['uiPhases'], report['phases']), (['F1'], ['F1', 'F2']))
                self.assertEqual(report.get('phase'), phase)
        self.assert_unknown_phase(header, UI_TABLE)

    # -- błędy i argumenty ---------------------------------------------------------

    def test_bad_lines_and_missing_prototype_are_blocked_in_every_row(self):
        cases = {'two paths': ('Prototyp: docs/prototypes/alpha docs/prototypes/beta', 'invalid_prototype_line'),
                 'bad bypass': ('Bramka prototypu: pominięta - zły myślnik - Ola, 2026-11-05', 'invalid_bypass_line'),
                 'unknown prototype': ('Prototyp: docs/prototypes/nie-ma', 'prototype_not_found')}
        for body in (NO_TABLE, UI_TABLE):
            for name, (line, error) in cases.items():
                for phase in (None, 'F1', 'F9'):
                    with self.subTest(body=body[:12], case=name, phase=phase):
                        self.assert_blocked(self.run_gate([line], body, phase, code=2), error)

    def test_phase_is_matched_literally(self):
        header = [self.line('alpha')]
        self.assert_blocked(self.run_gate(header, UI_TABLE, 'f2', code=2), 'unknown_phase')
        self.assert_blocked(self.run_gate(header, UI_TABLE, ' F2', code=2), 'unknown_phase')
        self.assertEqual(self.run_gate(header, UI_TABLE, 'F2')['phase'], 'F2')  # control

    def test_phase_in_the_slug_form_is_an_argument_error(self):
        self.accept_all('alpha')
        self.proto('gate', 'alpha', '--phase', 'F1', code=2)
        self.assertEqual(self.proto('gate', 'alpha')['status'], 'PASS')  # control: without --phase

    def test_duplicated_phase_ids_leave_the_whole_plan_behind_the_gate(self):
        body = '## Kroki\n\n| Id | Krok |\n|---|---|\n| F1 [UI] | a |\n| F1 | b |\n'
        report = self.run_gate([], body)  # no faze [UI] recognised: like a plan without a table
        self.assertEqual(report['note'], 'plan bez linii Prototyp:')
        self.assertNotIn('uiPhases', report)
        self.assert_blocked(self.run_gate([], body, 'F1', code=2), 'unknown_phase')

    def test_phase_ids_are_read_from_the_whole_plan_text_not_the_header(self):
        body = '## Etap\n\n```\n| Id | Krok |\n| F1 [UI] | przykład |\n```\n'
        report = self.run_gate([], body)
        self.assertNotIn('uiPhases', report)
        self.assertEqual(report['status'], 'PASS')
