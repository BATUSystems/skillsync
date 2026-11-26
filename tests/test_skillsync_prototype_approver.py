"""Silnik ról i sesji serwera prototypów, krok U-1a planu CORE-2000-UNIFIED: rola `approver`, pseudonim `account`, sesje, limitery,
reguła „lokalnie albo TLS”, audyt logowań i klucze i18n.

Kontrakt: spec 2026-11-25-core-2000-unified-jedna-strona-status-i-prototypy.md, sekcja 2 „Sesje i role” (K* dotyczące logowania i sesji).
Serwer startuje na 127.0.0.1:0 w wątku testu; zegar jest wstrzykiwany (`FakeClock`), liczba iteracji PBKDF2 jest mała. Nagłówki
proxy (`X-Forwarded-For`, `X-Forwarded-Proto`) wysyła klient z `ip=...` (jak w teście serwera); „zaufane proxy” to 127.0.0.1 na liście
`trusted_proxies`. Każda asercja negatywna (odmowa, brak sesji, brak wpisu) ma w tym samym teście przypadek kontrolny z poprawnym
wejściem, który przechodzi tą samą ścieżką. Hasła to atrapy składane z fragmentów w czasie wykonania.

Decyzje implementera, których spec nie rozstrzyga i które testy utrwalają (oznaczone „decyzja U-1a”):
kolizja dwóch pseudonimów wyprowadzonych (stare pliki bez pola `account`) jest tolerowana, a kolizja z jawnym polem odrzucana;
nadanie `approver` istniejącemu kontu ustawia `mustChange`; audyt udanych zdarzeń tylko dla kont `approver`.
"""
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from skillsync_workflow import prototype_server as ps  # noqa: E402
from tests.test_skillsync_prototype_server import setUpModule, tearDownModule  # noqa: E402,F401  (strażnik katalogu tymczasowego)
from tests.test_skillsync_prototype_server import Client, FakeClock, ITER, create_payload, uid, write  # noqa: E402

POSIX = os.name != 'nt'

APP = 'zatwierdzajaca@example.com'                 # tylko approver
APP2 = 'zatwierdzajacy.akceptujacy@example.com'    # approver + acceptor
COM = 'komentujaca@example.com'                    # komentujący
ACC = 'akceptujaca@example.com'                    # akceptujący
DEV = 'programistka@example.com'                   # programista
PW = {
    APP: ''.join(['Zatwierdzam', '-Plan-', '2026']),            # 21 znaków
    APP2: ''.join(['Zatwierdzam', '-Akceptuje-', '2026']),
    COM: ''.join(['Komentuje', '-Sobie-', '77']),               # 18 znaków
    ACC: ''.join(['Akceptuje', '-Ekrany-', '88']),
    DEV: ''.join(['Porzadkuje', '-Watki-', '99']),
}
WRONG = ''.join(['zle', '-haslo-', '1234'])
HOST_OUTSIDE = 'status.example.test'


def make_users(path, clock=None):
    users = ps.UsersFile(str(path), iterations=ITER, clock=clock)
    ps.add_user(users, APP, roles=['approver'], password=PW[APP])
    ps.add_user(users, APP2, roles=['approver', 'acceptor'], password=PW[APP2])
    ps.add_user(users, COM, role='commenter', password=PW[COM])
    ps.add_user(users, ACC, role='acceptor', password=PW[ACC])
    ps.add_user(users, DEV, role='developer', password=PW[DEV])
    return users


def signature(response):
    """Odpowiedź bez nagłówka `Date` (porównania „jednakowa dla każdej przyczyny”)."""
    headers = sorted((key.lower(), value) for key, value in response.headers.items() if key.lower() != 'date')
    return response.status, tuple(headers), response.body


class ApproverCase(unittest.TestCase):
    origin_kind = 'page'

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='proto-approver-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.clock = FakeClock()
        self.root = os.path.join(self.tmp, 'root')
        write(os.path.join(self.root, 'demo', 'prototype.json'), '{"title": "Demo"}\n')
        write(os.path.join(self.root, 'demo', 'index.html'),
              '<!doctype html><html data-prototype-id="demo"><body>DEMO</body></html>\n')
        write(os.path.join(self.root, 'demo', 'prototype.js'), '/* engine */\n')
        self.users_path = os.path.join(self.tmp, 'private', 'users.json')
        self.users = make_users(self.users_path, self.clock)
        self.run_dir = os.path.join(self.tmp, 'run')
        self._servers = []
        self.addCleanup(self._stop)

    def _stop(self):
        for server, thread in reversed(self._servers):
            server.shutdown()
            server.server_close()
            thread.join(10)
        self._servers = []

    def config(self, **overrides):
        values = dict(host='127.0.0.1', port=0, clock=self.clock, pbkdf2_iterations=ITER, log=None, git_checks=False,
                      source_writes=False, runtime_dir=self.run_dir, origin_kind=self.origin_kind)
        values.update(overrides)
        return ps.ServerConfig(self.root, self.users_path, **values)

    def start(self, **overrides):
        server = ps.make_server(self.config(**overrides))
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread.start()
        self._servers.append((server, thread))
        self.server = server
        return server

    def start_proxied(self, **overrides):
        """Serwer za zaufanym proxy (127.0.0.1): `Client(ip=...)` wysyła XFF i `X-Forwarded-Proto: https`."""
        overrides.setdefault('trust_proxy', True)
        overrides.setdefault('trusted_proxies', ['127.0.0.1'])
        return self.start(**overrides)

    def client(self, login=None, ip=None):
        client = Client(self.server, ip=ip)
        if login is not None:
            response = client.login(login, PW[login])
            self.assertEqual(response.status, 200, response.text)
        return client

    def attempt(self, login, password=WRONG, ip=None, **kwargs):
        return Client(self.server, ip=ip).login(login, password, **kwargs)

    def session_user(self, client):
        return client.get('/_api/v1/session').json['user']

    def write_op(self, client):
        """Zapis z poprawnym CSRF (komentarz): przedłuża bezczynność sesji `approver`."""
        return client.op('demo', 'create', uid('th'), create_payload(uid('m')))

    def events(self):
        path = os.path.join(self.run_dir, 'audit.jsonl')
        if not os.path.exists(path):
            return []
        with open(path, encoding='utf-8') as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def audit_text(self):
        path = os.path.join(self.run_dir, 'audit.jsonl')
        return open(path, encoding='utf-8').read() if os.path.exists(path) else ''


# ═════════════════════════════════════════════════════════════════════════════
# Rola approver: addytywna, bez acceptor i developer
# ═════════════════════════════════════════════════════════════════════════════

class ApproverRoleTests(ApproverCase):
    def test_approver_is_a_known_role_outside_the_privileged_ones(self):
        self.assertIn('approver', ps.ROLES)
        self.assertNotIn('approver', ps.PRIVILEGED_ROLES)
        self.assertNotIn('approver', ps.LEGACY_ROLES)
        record = self.users.get(APP)
        self.assertEqual(ps.roles_of(record), ('approver',))
        self.assertEqual(record['role'], 'commenter', 'legacy `role` dla samego approver = commenter (stary walidator)')
        self.assertFalse(ps.has_privileged_role(record))
        both = self.users.get(APP2)
        self.assertEqual(set(ps.roles_of(both)), {'approver', 'acceptor'}, 'role są addytywne')
        self.assertEqual(both['role'], 'acceptor')

    def test_approver_alone_has_neither_acceptor_nor_developer_rights(self):
        self.start()
        approver = self.client(APP)
        payload = self.session_user(approver)
        self.assertEqual(payload['roles'], ['approver'])
        self.assertEqual(approver.post('/_api/v1/project/accept', {'cid': uid('c')}).status, 403)
        denied = approver.op('demo', 'delete', 'th-1')
        self.assertEqual((denied.status, denied.json['error']), (403, 'forbidden'))
        # kontrola: ta sama ścieżka nie odmawia roli, która ją ma
        self.assertNotEqual(self.client(ACC).post('/_api/v1/project/accept', {'cid': uid('c')}).status, 403)
        self.assertNotEqual(self.client(DEV).op('demo', 'delete', 'th-1').status, 403)
        # kontrola: approver pisze jak komentujący
        self.assertEqual(self.write_op(approver).status, 201)

    def test_approver_with_acceptor_gets_acceptor_but_not_developer(self):
        self.start()
        both = self.client(APP2)
        self.assertEqual(set(self.session_user(both)['roles']), {'approver', 'acceptor'})
        self.assertNotEqual(both.post('/_api/v1/project/accept', {'cid': uid('c')}).status, 403)
        self.assertEqual(both.op('demo', 'delete', 'th-1').status, 403)

    def test_granting_approver_keeps_the_other_roles_and_forces_a_password_change(self):
        record, _ = ps.set_roles(self.users, COM, ['commenter', 'approver'])
        self.assertEqual(set(record['roles']), {'commenter', 'approver'})
        self.assertTrue(record['mustChange'], 'decyzja U-1a: nowe hasło (min. 14) przed pierwszym użyciem')
        record, _ = ps.set_roles(self.users, ACC, ['acceptor'])    # kontrola: bez approver mustChange się nie zmienia
        self.assertFalse(record['mustChange'])


# ═════════════════════════════════════════════════════════════════════════════
# Pseudonim `account`
# ═════════════════════════════════════════════════════════════════════════════

def raw_write(path, data):
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(data, handle)
    os.chmod(path, 0o600)


class AccountTests(ApproverCase):
    def test_default_account_is_the_part_before_at(self):
        self.assertEqual(ps.account_of(self.users.get(APP)), 'zatwierdzajaca')
        record, _ = ps.add_user(self.users, 'jan.nowak@example.com', password=PW[COM], account='Jan-N')
        self.assertEqual(record['account'], 'jan-n', 'jawna wartość jest normalizowana do małych liter')

    def test_cli_rejects_an_explicit_account_that_is_taken(self):
        with self.assertRaises(ps.UserError) as caught:
            ps.add_user(self.users, 'ktos@example.com', password=PW[COM], account='zatwierdzajaca')
        self.assertEqual(caught.exception.code, 'account_taken')
        record, _ = ps.add_user(self.users, 'ktos@example.com', password=PW[COM], account='ktos-inny')    # kontrola
        self.assertEqual(record['account'], 'ktos-inny')

    def test_cli_asks_for_an_explicit_account_when_the_default_collides(self):
        ps.add_user(self.users, 'jan@a.example', password=PW[COM])
        with self.assertRaises(ps.UserError) as caught:
            ps.add_user(self.users, 'jan@b.example', password=PW[COM])
        self.assertEqual(caught.exception.code, 'account_required')
        record, _ = ps.add_user(self.users, 'jan@b.example', password=PW[COM], account='jan-b')    # kontrola
        self.assertEqual(record['account'], 'jan-b')

    def test_set_roles_cannot_take_a_used_account(self):
        with self.assertRaises(ps.UserError) as caught:
            ps.set_roles(self.users, ACC, ['acceptor'], account='komentujaca')
        self.assertEqual(caught.exception.code, 'account_taken')

    def duplicated_file(self, explicit_on_second):
        data = json.load(open(self.users_path, encoding='utf-8'))
        first = next(item for item in data['users'] if item['login'] == COM)
        twin = dict(first, login='komentujaca@other.example')
        if explicit_on_second:
            twin['account'] = ps.account_of(first)
        data['users'].append(twin)
        raw_write(self.users_path, data)

    def test_server_rejects_a_users_file_with_a_duplicate_account(self):
        self.duplicated_file(explicit_on_second=True)
        with self.assertRaises(ps.UserError) as caught:
            ps.UsersFile(self.users_path).load()
        self.assertEqual(caught.exception.code, 'invalid_users_file')
        with self.assertRaises(ps.StartupError) as refused:
            ps.make_server(self.config())
        self.assertTrue(any('komentujaca' in message for message in refused.exception.errors), refused.exception.errors)

    def test_two_derived_accounts_of_legacy_records_are_tolerated(self):
        """decyzja U-1a: stare pliki bez pola `account` działają bez migracji (spec, sekcja 8)."""
        self.duplicated_file(explicit_on_second=False)
        self.assertEqual(len(ps.UsersFile(self.users_path).load()['users']), 6)
        self.start()
        self.assertEqual(self.session_user(self.client(COM))['login'], COM)

    def test_explicit_account_that_equals_a_derived_one_is_rejected(self):
        data = json.load(open(self.users_path, encoding='utf-8'))
        next(item for item in data['users'] if item['login'] == ACC)['account'] = 'komentujaca'
        raw_write(self.users_path, data)
        with self.assertRaises(ps.UserError):
            ps.UsersFile(self.users_path).load()


# ═════════════════════════════════════════════════════════════════════════════
# Długość hasła: approver 14, pozostali 10
# ═════════════════════════════════════════════════════════════════════════════

class PasswordLengthTests(ApproverCase):
    def test_constants(self):
        self.assertEqual((ps.MIN_PASSWORD, ps.MIN_PASSWORD_APPROVER), (10, 14))

    def test_add_user_requires_14_for_approver_and_10_for_the_rest(self):
        with self.assertRaises(ps.UserError) as caught:
            ps.add_user(self.users, 'a1@example.com', roles=['approver'], password='x' * 13)
        self.assertEqual(caught.exception.code, 'weak_password')
        ps.add_user(self.users, 'a2@example.com', roles=['approver'], password='x' * 14)
        with self.assertRaises(ps.UserError):
            ps.add_user(self.users, 'c1@example.com', role='commenter', password='x' * 9)
        ps.add_user(self.users, 'c2@example.com', role='commenter', password='x' * 10)
        with self.assertRaises(ps.UserError):                     # approver wraz z innymi rolami też 14
            ps.add_user(self.users, 'a3@example.com', roles=['approver', 'acceptor'], password='x' * 13)

    def test_passwd_cli_requires_14_for_an_approver_account(self):
        with self.assertRaises(ps.UserError) as caught:
            ps.set_password(self.users, APP, password='y' * 13)
        self.assertEqual(caught.exception.code, 'weak_password')
        ps.set_password(self.users, APP, password='y' * 14)
        ps.set_password(self.users, COM, password='y' * 10)

    def change(self, client, current, new):
        return client.post('/_api/v1/password', {'current': current, 'new': new})

    def test_password_change_through_the_api_follows_the_same_minimum(self):
        self.start()
        approver = self.client(APP)
        response = self.change(approver, PW[APP], 'n' * 13)
        self.assertEqual((response.status, response.json['error']), (400, 'weak_password'))
        self.assertEqual(self.change(approver, PW[APP], 'n' * 14).status, 204)
        commenter = self.client(COM)
        self.assertEqual(self.change(commenter, PW[COM], 'n' * 9).status, 400)
        self.assertEqual(self.change(commenter, PW[COM], 'n' * 10).status, 204)
        self.assertEqual(self.attempt(APP, 'n' * 14, ip=None).status, 200, 'nowe hasło działa')


# ═════════════════════════════════════════════════════════════════════════════
# Origin: approver loguje się tylko na originie strony
# ═════════════════════════════════════════════════════════════════════════════

class OriginKindTests(ApproverCase):
    def test_default_origin_kind_is_the_prototype_origin_and_refuses_approver(self):
        self.assertEqual(ps.ServerConfig(self.root, self.users_path).origin_kind, 'proto')
        self.start(origin_kind='proto')
        self.assertIn(ps.tr('proto.srv.start.approverElsewhere'), self.server.app.warnings)
        for login in (APP, APP2):
            with self.subTest(login=login):
                response = Client(self.server).login(login, PW[login])
                self.assertEqual((response.status, response.json['error']), (403, 'approver_login_elsewhere'))
                self.assertEqual(response.cookies(), [])
        self.assertEqual(self.server.app.sessions.count(), 0)
        # bez enumeracji: zła hasło approvera to to samo 401 co zła hasło komentującego
        self.assertEqual(signature(self.attempt(APP)), signature(self.attempt(COM)))
        # kontrola: komentujący loguje się na tym samym serwerze
        self.assertEqual(Client(self.server).login(COM, PW[COM]).status, 200)

    def test_page_origin_admits_approver_with_a_strict_session_cookie(self):
        self.start(origin_kind='page')
        self.assertNotIn(ps.tr('proto.srv.start.approverElsewhere'), self.server.app.warnings)
        client = Client(self.server)
        response = client.login(APP, PW[APP])
        self.assertEqual(response.status, 200, response.text)
        (cookie,) = response.cookies()
        self.assertTrue(cookie.startswith('skillsync_page_' + self.server.app.server_id + '='), cookie)
        attributes = [part.strip() for part in cookie.split(';')[1:]]
        for expected in ('HttpOnly', 'SameSite=Strict', 'Path=/', 'Max-Age=%d' % (12 * 3600)):
            self.assertIn(expected, attributes)
        self.assertNotIn('Secure', attributes, 'lokalnie, bez TLS')
        self.assertEqual(self.session_user(client)['roles'], ['approver'])

    def test_commenting_cookie_keeps_the_long_lifetime(self):
        self.start(origin_kind='page')
        (cookie,) = Client(self.server).login(COM, PW[COM]).cookies()
        self.assertIn('Max-Age=%d' % (7 * 24 * 3600), [part.strip() for part in cookie.split(';')[1:]])


# ═════════════════════════════════════════════════════════════════════════════
# Wygasanie sesji (zegar wstrzykiwany)
# ═════════════════════════════════════════════════════════════════════════════

H = 3600


class SessionLifetimeTests(ApproverCase):
    def setUp(self):
        super().setUp()
        self.start()

    def test_approver_session_expires_after_two_hours_without_a_write(self):
        client = self.client(APP)
        self.clock.advance(2 * H - 60)
        self.assertIsNotNone(self.session_user(client), 'kontrola: tuż przed limitem sesja żyje')
        self.clock.advance(120)
        self.assertIsNone(self.session_user(client))

    def test_a_write_extends_the_idle_time_of_an_approver_session(self):
        client = self.client(APP)
        self.clock.advance(int(1.5 * H))
        self.assertEqual(self.write_op(client).status, 201)
        self.clock.advance(int(1.5 * H))      # 3 h od logowania, 1,5 h od zapisu
        self.assertIsNotNone(self.session_user(client))
        self.clock.advance(int(0.6 * H))      # 2,1 h od zapisu
        self.assertIsNone(self.session_user(client))

    def test_reads_do_not_extend_an_approver_session_but_do_extend_a_commenting_one(self):
        approver, commenter = self.client(APP), self.client(COM)
        self.clock.advance(int(1.2 * H))
        self.assertIsNotNone(self.session_user(approver))        # odczyt po 1,2 h
        self.assertIsNotNone(self.session_user(commenter))
        self.clock.advance(int(1.2 * H))                         # 2,4 h od logowania, 1,2 h od odczytu
        self.assertIsNone(self.session_user(approver), 'odczyt nie przesunął terminu sesji approver')
        self.assertIsNotNone(self.session_user(commenter))
        for _ in range(2):                                       # komentujący: odczyty co 11 h utrzymują sesję ponad 12 h
            self.clock.advance(11 * H)
            self.assertIsNotNone(self.session_user(commenter))

    def test_approver_session_ends_after_twelve_hours_even_with_recent_writes(self):
        client = self.client(APP)
        for _ in range(7):                    # zapis co 1,5 h do 10,5 h
            self.clock.advance(int(1.5 * H))
            self.assertEqual(self.write_op(client).status, 201)
        self.clock.advance(int(1.5 * H))      # dokładnie 12 h od logowania
        self.assertIsNotNone(self.session_user(client), 'kontrola: 12 h to jeszcze nie „po 12 h”')
        self.clock.advance(120)
        self.assertIsNone(self.session_user(client), 'maksimum bezwzględne mimo zapisu sprzed 1,5 h')

    def test_approver_with_another_role_is_limited_too(self):
        client = self.client(APP2)
        self.clock.advance(2 * H + 60)
        self.assertIsNone(self.session_user(client))

    def test_other_roles_keep_the_old_limits(self):
        sessions = dict((login, self.client(login)) for login in (COM, ACC, DEV))
        self.clock.advance(3 * H)             # dłużej niż dopuszcza approver
        for login, client in sessions.items():
            self.assertIsNotNone(self.session_user(client), login)
        self.clock.advance(12 * H + 60)       # bezczynność > 12 h
        for login, client in sessions.items():
            self.assertIsNone(self.session_user(client), login)
        long = self.client(COM)
        for _ in range(28):                   # odczyt co 6 h przez 7 dni
            self.clock.advance(6 * H)
            self.assertIsNotNone(self.session_user(long))
        self.clock.advance(6 * H)
        self.assertIsNone(self.session_user(long), 'maksimum 7 dni zostaje')


# ═════════════════════════════════════════════════════════════════════════════
# Limiter (konto, źródło), PBKDF2 równolegle, brak enumeracji
# ═════════════════════════════════════════════════════════════════════════════

class LoginLimiterTests(ApproverCase):
    def setUp(self):
        super().setUp()
        self.start_proxied()

    def test_sixth_attempt_from_one_source_is_429_with_retry_after(self):
        for number in range(5):
            self.assertEqual(self.attempt(APP, ip='10.0.0.1').status, 401, 'próba %d' % (number + 1))
        blocked = self.attempt(APP, PW[APP], ip='10.0.0.1')    # nawet poprawne hasło
        self.assertEqual((blocked.status, blocked.json['error']), (429, 'rate_limited'))
        retry = int(blocked.header('Retry-After'))
        self.assertTrue(1 <= retry <= 900, retry)
        self.assertEqual(blocked.json['retryAfter'], retry)
        self.assertEqual(blocked.cookies(), [])

    def test_another_source_of_the_same_account_is_not_blocked(self):
        for _ in range(6):
            self.attempt(APP, ip='10.0.0.1')
        self.assertEqual(self.attempt(APP, PW[APP], ip='10.0.0.1').status, 429)
        response = self.attempt(APP, PW[APP], ip='10.0.0.2')
        self.assertEqual(response.status, 200, response.text)
        self.assertEqual(response.json['user']['roles'], ['approver'])

    def test_an_anonymous_source_cannot_lock_the_approver_out(self):
        for number in range(40):             # zgadywanie z jednego źródła: konto, potem inne konta (limit adresu)
            self.attempt(APP if number < 10 else 'nikt%d@example.com' % number, ip='10.0.0.66')
        self.assertEqual(self.attempt(APP, PW[APP], ip='10.0.0.66').status, 429, 'kontrola: napastnik jest zablokowany')
        self.assertEqual(self.attempt(COM, PW[COM], ip='10.0.0.66').status, 429)
        self.assertEqual(self.attempt(APP, PW[APP], ip='10.0.0.7').status, 200, 'zatwierdzający z innego źródła loguje się')

    def test_block_ends_after_the_window(self):
        for _ in range(5):
            self.attempt(APP, ip='10.0.0.1')
        self.assertEqual(self.attempt(APP, PW[APP], ip='10.0.0.1').status, 429)
        self.clock.advance(15 * 60 + 1)
        self.assertEqual(self.attempt(APP, PW[APP], ip='10.0.0.1').status, 200)

    def test_ipv6_sources_are_counted_per_slash_64(self):
        for index in range(5):
            self.attempt(APP, ip='fd00:1:2:3:%x::1' % (index + 1))
        self.assertEqual(self.attempt(APP, PW[APP], ip='fd00:1:2:3:ffff::9').status, 429, 'ta sama sieć /64')
        self.assertEqual(self.attempt(APP, PW[APP], ip='fd00:1:2:4::1').status, 200, 'kontrola: inna sieć /64')

    def test_rightmost_untrusted_hop_is_the_source(self):
        for index in range(5):                # lewy skok to fałszerstwo klienta, prawy dodało zaufane proxy
            self.attempt(APP, ip='9.9.9.%d, 10.0.0.5' % index)
        self.assertEqual(self.attempt(APP, PW[APP], ip='8.8.8.8, 10.0.0.5').status, 429)
        self.assertEqual(self.attempt(APP, PW[APP], ip='8.8.8.8, 10.0.0.6').status, 200, 'kontrola: inny skrajny prawy skok')

    def test_forwarded_for_of_an_untrusted_peer_is_ignored(self):
        server = self.start_proxied(trusted_proxies=['203.0.113.7'], allow_plain_login=True)    # peer 127.0.0.1 nie jest na liście
        self.assertTrue(server.app.config.trust_proxy)
        for index in range(5):
            self.assertEqual(self.attempt(COM, ip='10.1.1.%d' % index).status, 401)
        self.assertEqual(self.attempt(COM, PW[COM], ip='10.9.9.9').status, 429, 'wszystkie próby to jedno źródło: peer')


class ParallelHashLimitTests(ApproverCase):
    """Globalny limit równoległych weryfikacji PBKDF2: nadmiar = 503 z `Retry-After` (bez liczenia próby)."""

    def test_default_limit_is_four(self):
        self.assertEqual(ps.PBKDF2_PARALLEL, 4)
        self.assertEqual(ps.ServerConfig(self.root, self.users_path).pbkdf2_parallel, 4)

    def test_excess_checks_get_503_and_do_not_count_as_failures(self):
        self.start(pbkdf2_parallel=2)
        entered, release, results = threading.Semaphore(0), threading.Event(), []
        real = ps.verify_password

        def slow(password, encoded):
            entered.release()
            release.wait(30)
            return real(password, encoded)

        def worker(login):
            results.append(Client(self.server, timeout=60).login(login, WRONG).status)
        with mock.patch.object(ps, 'verify_password', slow):
            threads = [threading.Thread(target=worker, args=(login,)) for login in (COM, ACC)]
            for thread in threads:
                thread.start()
            try:
                for _ in threads:
                    self.assertTrue(entered.acquire(timeout=30), 'dwie weryfikacje muszą być w toku')
                for login in (DEV, 'nikt@example.com', APP):
                    with self.subTest(login=login):
                        busy = Client(self.server).login(login, WRONG)
                        self.assertEqual((busy.status, busy.json['error']), (503, 'busy'))
                        self.assertEqual(busy.header('Retry-After'), '1')
                        self.assertEqual(busy.cookies(), [])
            finally:
                release.set()
                for thread in threads:
                    thread.join(30)
        self.assertEqual(sorted(results), [401, 401], 'kontrola: weryfikacje w toku kończą się zwykłym 401')
        # 503 nie jest błędem logowania: para (DEV, źródło) ma nadal pełne 5 prób
        self.assertEqual([self.attempt(DEV).status for _ in range(6)], [401] * 5 + [429])
        self.assertEqual(self.attempt(APP, PW[APP]).status, 200, 'po zwolnieniu miejsc logowanie działa')


class NoEnumerationTests(ApproverCase):
    def setUp(self):
        super().setUp()
        self.start_proxied()

    def counted(self, action):
        calls, real = [], hashlib.pbkdf2_hmac

        def counting(*args, **kwargs):
            calls.append(args[3] if len(args) > 3 else kwargs.get('iterations'))
            return real(*args, **kwargs)
        with mock.patch.object(hashlib, 'pbkdf2_hmac', counting):
            response = action()
        return response, calls

    def test_unknown_login_costs_the_same_work_and_answers_the_same(self):
        known, known_calls = self.counted(lambda: self.attempt(COM, ip='10.0.1.1'))
        approver, approver_calls = self.counted(lambda: self.attempt(APP, ip='10.0.1.2'))
        unknown, unknown_calls = self.counted(lambda: self.attempt('nie.istnieje@example.com', ip='10.0.1.3'))
        self.assertEqual(known.status, 401)
        self.assertEqual(signature(known), signature(unknown))
        self.assertEqual(signature(approver), signature(unknown))
        self.assertEqual(len(known_calls), 1, 'kontrola: poprawne konto liczy jedno PBKDF2')
        self.assertEqual(unknown_calls, known_calls, 'nieznany login: jedno PBKDF2 z tą samą liczbą iteracji (sztuczny hash)')
        self.assertEqual(approver_calls, known_calls)

    def test_limit_looks_the_same_for_known_and_unknown_logins(self):
        for login, ip in ((COM, '10.0.2.1'), ('nie.istnieje@example.com', '10.0.2.2')):
            for _ in range(5):
                self.assertEqual(self.attempt(login, ip=ip).status, 401)
        known, unknown = self.attempt(COM, ip='10.0.2.1'), self.attempt('nie.istnieje@example.com', ip='10.0.2.2')
        self.assertEqual(known.status, 429)
        self.assertEqual(signature(known), signature(unknown))


# ═════════════════════════════════════════════════════════════════════════════
# Reguła „lokalnie albo TLS”
# ═════════════════════════════════════════════════════════════════════════════

class LocalOrTlsTests(ApproverCase):
    def setUp(self):
        super().setUp()
        self.start()

    def login_post(self, login, **kwargs):
        return Client(self.server).login(login, PW[login], **kwargs)

    def test_local_request_gets_the_form_and_the_login(self):
        page = Client(self.server).get('/_login')
        self.assertEqual(page.status, 200)
        self.assertIn('type="password"', page.text)
        for host in ('localhost', 'localhost:4000', '127.0.0.1', '[::1]:80'):
            with self.subTest(host=host):
                self.assertEqual(self.login_post(COM, headers={'Host': host}).status, 200)

    def test_host_outside_the_loopback_refuses_form_and_login(self):
        for host in (HOST_OUTSIDE, 'localhost.evil.example', '127.0.0.1.evil.example', '192.168.0.10:5140'):
            with self.subTest(host=host):
                page = Client(self.server).get('/_login', headers={'Host': host})
                self.assertEqual(page.status, 403)
                self.assertNotIn('<form', page.text)
                self.assertNotIn('type="password"', page.text)
                for login in (COM, APP):
                    response = self.login_post(login, headers={'Host': host})
                    self.assertEqual((response.status, response.json['error']), (403, 'secure_connection_required'))
                    self.assertEqual(response.cookies(), [])
        self.assertEqual(self.server.app.sessions.count(), 0)
        self.assertEqual(self.login_post(COM).status, 200, 'kontrola: ten sam login z Host loopback')

    def test_proxy_headers_make_a_loopback_request_non_local(self):
        for header in ('X-Forwarded-For', 'X-Forwarded-Host', 'X-Forwarded-Proto', 'Forwarded', 'X-Real-IP'):
            with self.subTest(header=header):
                extra = {header: '10.1.2.3'}
                self.assertEqual(Client(self.server).get('/_login', headers=extra).status, 403)
                response = self.login_post(COM, headers=extra)
                self.assertEqual((response.status, response.json['error']), (403, 'secure_connection_required'))
        self.assertEqual(self.server.app.sessions.count(), 0)
        self.assertEqual(self.login_post(COM).status, 200, 'kontrola: bez nagłówków proxy')

    def test_allow_plain_login_admits_commenters_but_never_an_approver_session(self):
        self.start(allow_plain_login=True)
        self.assertIn(ps.tr('proto.srv.warn.plainLogin'), self.server.app.warnings)
        outside = {'Host': HOST_OUTSIDE}
        self.assertEqual(Client(self.server).get('/_login', headers=outside).status, 200)
        commenter = self.login_post(COM, headers=outside)
        self.assertEqual(commenter.status, 200, commenter.text)
        before = self.server.app.sessions.count()
        for login in (APP, APP2):
            with self.subTest(login=login):
                response = self.login_post(login, headers=outside)
                self.assertEqual((response.status, response.json['error']), (403, 'secure_connection_required'))
                self.assertEqual(response.cookies(), [])
        self.assertEqual(self.server.app.sessions.count(), before, 'sesja approver nie powstała')
        self.assertEqual(self.login_post(APP).status, 200, 'kontrola: ten sam approver lokalnie')

    def plain_world(self):
        """Serwer z `allow_plain_login` i kontami: expert, wielorolowe (acceptor+expert, commenter+developer)."""
        users = ps.UsersFile(self.users_path, iterations=ITER, clock=self.clock)
        self.EXP, self.MULTI, self.MULTI2 = 'ekspert@example.com', 'wielorolowy@example.com', 'komentuje.programuje@example.com'
        ps.add_user(users, self.EXP, role='expert', password=PW[COM])
        ps.add_user(users, self.MULTI, roles=['acceptor', 'expert'], password=PW[COM])
        ps.add_user(users, self.MULTI2, roles=['commenter', 'developer'], password=PW[COM])
        self.start(allow_plain_login=True)
        return {'Host': HOST_OUTSIDE}

    def test_plain_login_admits_only_commenter_and_expert(self):
        outside = self.plain_world()
        for login in (COM, self.EXP):
            with self.subTest(login=login):
                self.assertEqual(Client(self.server).login(login, PW[COM] if login == self.EXP else PW[login], headers=outside).status, 200)

    def test_plain_login_refuses_privileged_accounts_without_a_session(self):
        outside = self.plain_world()
        before = self.server.app.sessions.count()
        for login, password in ((ACC, PW[ACC]), (DEV, PW[DEV]), (self.MULTI, PW[COM]), (self.MULTI2, PW[COM])):
            with self.subTest(login=login):
                response = Client(self.server).login(login, password, headers=outside)
                self.assertEqual((response.status, response.json['error']), (403, 'secure_connection_required'))
                self.assertEqual(response.cookies(), [])
        self.assertEqual(self.server.app.sessions.count(), before)
        for login, password in ((ACC, PW[ACC]), (self.MULTI, PW[COM])):   # kontrola: lokalnie to samo konto się loguje
            self.assertEqual(Client(self.server).login(login, password).status, 200)

    def test_plain_password_change_follows_the_same_roles(self):
        outside = self.plain_world()
        new = PW[COM] + '-nowe'
        for login, password, expected in ((COM, PW[COM], 204), (self.EXP, PW[COM], 204), (ACC, PW[ACC], 403),
                                          (DEV, PW[DEV], 403), (self.MULTI, PW[COM], 403)):
            with self.subTest(login=login):
                client = Client(self.server)
                self.assertEqual(client.login(login, password).status, 200)    # sesja lokalna (kontrola)
                response = client.post('/_api/v1/password', {'current': password, 'new': new}, headers=outside)
                self.assertEqual(response.status, expected, response.text)
                if expected == 403:
                    self.assertEqual(response.json['error'], 'secure_connection_required')
                    self.assertEqual(Client(self.server).login(login, password).status, 200, 'hasło niezmienione')
                    self.assertEqual(client.post('/_api/v1/password', {'current': password, 'new': new}).status, 204,
                                     'kontrola: lokalnie zmiana przechodzi')

    def test_tls_through_a_trusted_proxy_admits_an_approver_with_a_secure_host_cookie(self):
        self.start_proxied()
        response = self.attempt(APP, PW[APP], ip='10.0.0.1')
        self.assertEqual(response.status, 200, response.text)
        (cookie,) = response.cookies()
        self.assertTrue(cookie.startswith('__Host-skillsync_page_' + self.server.app.server_id + '='), cookie)
        attributes = [part.strip() for part in cookie.split(';')[1:]]
        self.assertIn('Secure', attributes)
        self.assertIn('SameSite=Strict', attributes)
        # to samo proxy bez https: brak TLS, więc approver nie loguje się (kontrola poprzedniego przypadku)
        plain = self.attempt(APP, PW[APP], ip='10.0.0.1', headers={'X-Forwarded-Proto': 'http'})
        self.assertEqual((plain.status, plain.json['error']), (403, 'secure_connection_required'))


class ProxyStartupTests(ApproverCase):
    def test_trust_proxy_without_a_list_refuses_to_start(self):
        with self.assertRaises(ps.StartupError) as refused:
            ps.make_server(self.config(trust_proxy=True))
        self.assertIn(ps.tr('proto.srv.start.trustProxyList'), refused.exception.errors)
        with self.assertRaises(ps.StartupError):
            ps.make_server(self.config(trust_proxy=True, trusted_proxies=[]))
        self.start(trust_proxy=True, trusted_proxies=['127.0.0.1'])     # kontrola: z listą startuje
        self.start(trusted_proxies=['127.0.0.1'])                        # lista bez --trust-proxy nie przeszkadza

    def test_an_invalid_proxy_entry_refuses_to_start(self):
        with self.assertRaises(ps.StartupError) as refused:
            ps.make_server(self.config(trust_proxy=True, trusted_proxies=['127.0.0.1', 'nie-adres']))
        self.assertIn(ps.tr('proto.srv.start.proxyInvalid', item='nie-adres'), refused.exception.errors)

    def test_cli_exit_code_two_and_flags(self):
        args = ['serve', '--root', self.root, '--users', self.users_path, '--port', '0', '--no-source-writes']
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = ps.main(args + ['--trust-proxy'])
        self.assertEqual(code, 2)
        self.assertIn('--proxy-address', err.getvalue())
        parsed = ps.build_parser().parse_args(args + ['--trust-proxy', '--proxy-address', '10.0.0.1,10.0.0.2',
                                                       '--proxy-address', '10.1.0.0/16', '--allow-plain-login'])
        self.assertEqual(ps.split_roles(parsed.proxy_address), ['10.0.0.1', '10.0.0.2', '10.1.0.0/16'])
        self.assertTrue(parsed.allow_plain_login)


# ═════════════════════════════════════════════════════════════════════════════
# Audyt run/audit.jsonl
# ═════════════════════════════════════════════════════════════════════════════

class AuditTests(ApproverCase):
    def setUp(self):
        super().setUp()
        self.start_proxied()

    def names(self):
        return [item['event'] for item in self.events() if 'event' in item]

    def test_failures_refusals_and_limits_are_always_written(self):
        self.attempt(COM, ip='10.0.0.1')                          # zły login komentującego
        self.attempt('nie.istnieje@example.com', ip='10.0.0.1')   # nieznany login
        failed = [item for item in self.events() if item.get('event') == 'login_failed']
        self.assertEqual(len(failed), 2)
        self.assertEqual(failed[0]['account'], 'komentujaca', 'pseudonim, nie e-mail')
        self.assertIsNone(failed[1]['account'])
        self.attempt(APP, PW[APP], ip='10.0.0.1', headers={'X-Forwarded-Proto': 'http'})        # bez TLS
        self.assertIn('login_refused', self.names())
        for _ in range(5):
            self.attempt(COM, ip='10.0.0.9')
        self.assertEqual(self.attempt(COM, PW[COM], ip='10.0.0.9').status, 429)
        self.assertIn('login_limited', self.names())
        server = self.start_proxied(origin_kind='proto')           # approver na originie prototypów
        self.assertEqual(Client(server).login(APP, PW[APP]).status, 403)
        refused = [item for item in self.events() if item.get('event') == 'login_refused']
        self.assertEqual(sorted(item['reason'] for item in refused), ['approver_elsewhere', 'connection'])
        for item in self.events():
            if 'event' in item:
                self.assertIn('at', item)

    def test_a_series_of_failures_on_an_approver_account_raises_an_alert(self):
        for index in range(3):
            self.attempt(APP, ip='10.0.0.%d' % (index + 20))
        alerts = [item for item in self.events() if item.get('event') == 'approver_alert']
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]['account'], 'zatwierdzajaca')
        self.assertEqual(len(self.server.app.alerts), 1)
        other = len(self.server.app.alerts)
        for index in range(3):                                      # kontrola: komentujący nie wywołuje alertu approver
            self.attempt(COM, ip='10.0.0.%d' % (index + 40))
        self.assertEqual(len(self.server.app.alerts), other)

    def test_successful_events_are_written_for_approver_accounts_only(self):
        commenter = Client(self.server, ip='10.0.0.1')
        self.assertEqual(commenter.login(COM, PW[COM]).status, 200)
        self.assertEqual(commenter.post('/_api/v1/password', {'current': PW[COM], 'new': PW[COM] + '-nowe'}).status, 204)
        self.assertEqual(commenter.post('/_api/v1/logout').status, 204)
        self.assertEqual(self.names(), [], 'komentujący: brak wpisów o udanych zdarzeniach')
        approver = Client(self.server, ip='10.0.0.2')
        self.assertEqual(approver.login(APP, PW[APP]).status, 200)
        self.assertEqual(approver.post('/_api/v1/logout').status, 204)
        self.assertEqual(approver.login(APP, PW[APP]).status, 200)
        self.assertEqual(approver.post('/_api/v1/password', {'current': PW[APP], 'new': PW[APP] + '-nowe'}).status, 204)
        self.assertEqual(self.names(), ['login', 'logout', 'login', 'password_changed'])
        self.assertEqual(set(item['account'] for item in self.events()), {'zatwierdzajaca'})

    def test_the_audit_holds_no_passwords_tokens_or_logins(self):
        client = Client(self.server, ip='10.0.0.2')
        client.login(APP, PW[APP])
        secrets_seen = [client.cookie.split('=', 1)[1], client.csrf]
        new_password = PW[APP] + '-nowe'
        client.post('/_api/v1/password', {'current': PW[APP], 'new': new_password})
        secrets_seen += [client.cookie.split('=', 1)[1], client.csrf]
        self.attempt(APP, WRONG, ip='10.0.0.3')
        self.attempt('Nie.Istnieje@example.com', WRONG, ip='10.0.0.3')
        text = self.audit_text()
        self.assertTrue(text, 'kontrola: audyt zawiera wpisy')
        for secret in list(PW.values()) + [WRONG, new_password] + secrets_seen:
            self.assertNotIn(secret, text)
        self.assertNotIn('@', text, 'ani e-maili, ani wpisany nieznany login')
        self.assertNotIn('nie.istnieje', text.lower())
        if POSIX:
            mode = stat.S_IMODE(os.stat(os.path.join(self.run_dir, 'audit.jsonl')).st_mode)
            self.assertEqual(mode, 0o600)


# ═════════════════════════════════════════════════════════════════════════════
# i18n: nowe klucze
# ═════════════════════════════════════════════════════════════════════════════

NEW_KEYS = ('proto.srv.start.originKind', 'proto.srv.start.trustProxyList', 'proto.srv.start.proxyInvalid',
            'proto.srv.start.approverElsewhere', 'proto.srv.warn.plainLogin', 'proto.srv.login.secureRequired',
            'proto.srv.log.approverAlert')
CATALOG = ROOT / 'skillsync_workflow' / 'data' / 'i18n'


class I18nKeysTests(unittest.TestCase):
    def test_every_new_key_exists_in_polish_and_english_with_the_same_placeholders(self):
        catalogs = dict((lang, json.loads((CATALOG / ('%s.proto.json' % lang)).read_text(encoding='utf-8')))
                        for lang in ('pl', 'en'))
        for key in NEW_KEYS:
            with self.subTest(key=key):
                texts = {}
                for lang, catalog in catalogs.items():
                    self.assertIn(key, catalog, lang)
                    self.assertIsInstance(catalog[key], str)
                    self.assertTrue(catalog[key].strip(), lang)
                    texts[lang] = catalog[key]
                self.assertNotEqual(texts['pl'], texts['en'])
                self.assertEqual(sorted(re.findall(r'\{(\w+)\}', texts['pl'])), sorted(re.findall(r'\{(\w+)\}', texts['en'])))

    def test_the_standalone_server_resolves_them_in_both_languages(self):
        params = {'kinds': 'proto, page', 'item': 'x', 'count': 3, 'account': 'a'}
        for key in NEW_KEYS:
            for lang in ('pl', 'en'):
                with self.subTest(key=key, lang=lang):
                    text = ps.tr(key, language=lang, **params)
                    self.assertNotEqual(text, key)
                    self.assertNotIn('{', text)


if __name__ == '__main__':
    unittest.main()
