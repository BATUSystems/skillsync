"""CORE-2000 U-1b: trasy sesji na originie strony (mixin w `dashboard_http.Handler`) i kod przekazania sesji na origin prototypów.

Kontrakt (spec CORE-2000-UNIFIED, sekcje 1 i 2): wydanie kodu tylko `POST /_api/v1/handoff` z sesji strony + CSRF; kod >= 128 bit,
w magazynie tylko skrót, 60 s, jednorazowy i atomowy, związany z kontem, sesją strony, hostem prototypów i `state`; wymiana `POST /_handoff`
(formularz), `303` na czysty adres, `Referrer-Policy: no-referrer`, kod poza logiem i audytem; sesja przekazana bez `approver`/`acceptor`/
`developer`, ciasteczko i sesja per origin, kończy się z sesją-rodzicem; origin prototypów nie przyjmuje hasła.

Serwery: strona (`Handler`), czytnik (`ReadHandler`) i prototypy (`PrototypeHTTPServer`, `origin_kind='proto'`) na jednym `PrototypeApp`,
loopback, zegar wstrzykiwany. Klasa `MutationControlTests` dowodzi, że kluczowe asercje potrafią paść (mutant przez monkeypatch, bez edycji kodu).
"""
import base64
import hashlib
import http.client
import io
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from skillsync_workflow import dashboard_http as dh  # noqa: E402
from skillsync_workflow import prototype_server as ps  # noqa: E402
from tests.test_skillsync_dashboard_server import free_port  # noqa: E402

START = 1700000000.0
# atrapy haseł składane w czasie wykonania (skan sekretów)
PW_APPROVER = 'Zatwierdzam-' + 'Plan-' + 'Kroku-' + '2026'
PW_COMMENTER = 'Komentuje-' + 'Sobie-' + '7788'
PW_OTHER = 'Inny-Czlowiek-' + 'Komentuje-' + '4411'
PW_DEFAULT = 'Domyslne-' + 'Haslo-' + '12345'
PW_NEW = 'Nowe-' + 'Haslo-' + 'Komentuje-' + '9'
FAKE_CHALLENGE = 'ab' * 32   # kontrakt U-1c: challenge = SHA-256(nonce) jako 64 znaki hex (zamiast `state`)


class Clock(object):
    def __init__(self):
        self.now = START
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.now

    def advance(self, seconds):
        with self.lock:
            self.now += seconds


def sha_of_inline_script(page):
    scripts = re.findall(r'<script>(.*?)</script>', page, re.S)
    return scripts, ["'sha256-%s'" % base64.b64encode(hashlib.sha256(text.encode('utf-8')).digest()).decode('ascii') for text in scripts]


class Resp(object):
    def __init__(self, status, headers, raw_headers, body):
        self.status, self.headers, self.raw_headers, self.body = status, headers, raw_headers, body

    def json(self):
        return json.loads(self.body.decode('utf-8'))

    def cookies(self):
        return [v for k, v in self.raw_headers if k.lower() == 'set-cookie']

    def cookie(self):
        values = self.cookies()
        return values[0] if values else None

    def cookie_named(self, prefix):
        for value in self.cookies():
            if value.startswith(prefix):
                return value
        return None


def request(port, method, path, body=None, headers=None, form=None, host=None, origin=True, ctype=None):
    """Jedno żądanie na loopback. `body` = JSON, `form` = urlencoded; Origin domyślnie własny (jak przeglądarka), `origin=False` = bez nagłówka."""
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=15)
    send = {'Host': host or ('127.0.0.1:%d' % port)}
    data = None
    if body is not None:
        data = json.dumps(body)
        send['Content-Type'] = ctype or 'application/json'
    if form is not None:
        data = form
        send['Content-Type'] = ctype or 'application/x-www-form-urlencoded'
    if data is not None and origin is True:
        send['Origin'] = 'http://127.0.0.1:%d' % port
    elif isinstance(origin, str):
        send['Origin'] = origin
    send.update(headers or {})
    try:
        conn.request(method, path, body=data, headers=send)
        response = conn.getresponse()
        raw = response.read()
        return Resp(response.status, dict((k.lower(), v) for k, v in response.getheaders()), response.getheaders(), raw)
    finally:
        conn.close()


class Login(object):
    """Zalogowana przeglądarka na originie strony: ciasteczko i token CSRF."""

    def __init__(self, env, response):
        self.env = env
        self.pair = response.cookie().split(';')[0]
        self.csrf = response.json()['csrf']
        self.payload = response.json()['user']

    @property
    def auth(self):
        return {'Cookie': self.pair, 'X-Skillsync-CSRF': self.csrf}

    def token(self):
        return self.pair.split('=', 1)[1]


class Env(object):
    def __init__(self, case, app=True, prototype_url=True, page_url=True, local_only=False, runtime=True, enable_link=True, verbose=False):
        self.tmp = tempfile.TemporaryDirectory()
        case.addCleanup(self.tmp.cleanup)
        self.clock = Clock()
        base = Path(self.tmp.name)
        self.repo = base / 'repo'
        (self.repo / '.skillsync' / 'prototype').mkdir(parents=True)
        self.users_path = str(self.repo / '.skillsync' / 'prototype' / 'users.json')
        users = ps.UsersFile(self.users_path, iterations=1000)
        ps.add_user(users, 'ap@example.com', roles=['approver', 'acceptor', 'developer', 'expert'], password=PW_APPROVER, via_prompt=True)   # kontrakt U-1d
        ps.add_user(users, 'com@example.com', role='commenter', password=PW_COMMENTER)
        ps.add_user(users, 'two@example.com', role='commenter', password=PW_OTHER)
        ps.add_user(users, 'acc@example.com', roles=['acceptor', 'developer', 'commenter'], password=PW_COMMENTER)
        ps.add_user(users, 'must@example.com', role='commenter', default_password=PW_DEFAULT)
        self.prototypes = self.repo / 'prototypes'
        (self.prototypes / 'demo').mkdir(parents=True)
        (self.prototypes / 'demo' / 'prototype.json').write_text('{"title":"Demo"}')
        (self.prototypes / 'demo' / 'index.html').write_text('<html data-prototype-id="demo"></html>')
        subprocess.run(['git', 'init', '-q'], cwd=str(self.repo), check=True)
        self.log = io.StringIO()
        self.run_dir = str(base / 'run')
        self.config = ps.ServerConfig(str(self.prototypes), self.users_path, origin_kind='page', pbkdf2_iterations=1000, git_checks=False,
                                      source_writes=False, recover=False, clock=self.clock, local_only=local_only, log=self.log,
                                      runtime_dir=self.run_dir if runtime else None)
        self.app = ps.PrototypeApp(self.config) if app else None
        board = dh.Board(self.repo)
        self.servers = []
        self.page = self._start(dh.make_server(board, '127.0.0.1', free_port(), verbose=verbose, proto_app=self.app))
        self.read = self._start(dh.make_server(board, '127.0.0.1', free_port(), role='read', proto_app=self.app))
        self.proto = None
        if app:
            self.proto = self._start(ps.PrototypeHTTPServer(('127.0.0.1', 0), ps.PrototypeRequestHandler, self.app, origin_kind='proto'))
            if page_url:
                self.config.page_url = 'http://127.0.0.1:%d' % self.page.server_address[1]
            if prototype_url:
                self.config.prototype_url = 'http://127.0.0.1:%d' % self.proto.server_address[1]
            if runtime and enable_link:
                self.app.enable_login_link(SimpleNamespace(server_address=self.page.server_address, port=self.page.server_address[1]))
        case.addCleanup(self.stop)

    def _start(self, server):
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread.start()
        self.servers.append(server)
        return server

    def stop(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()

    @property
    def pp(self):
        return self.page.server_address[1]

    @property
    def rp(self):
        return self.read.server_address[1]

    @property
    def xp(self):
        return self.proto.server_address[1]

    def page_req(self, method, path, **kw):
        return request(self.pp, method, path, **kw)

    def proto_req(self, method, path, **kw):
        return request(self.xp, method, path, **kw)

    def login(self, login, password):
        response = self.page_req('POST', '/_api/v1/login', body={'login': login, 'password': password})
        assert response.status == 200, (response.status, response.body)
        return Login(self, response)

    # kontrakt U-1c (protokół A): GET <proto>/_handoff/begin -> cookie z nonce + 303 z `challenge` = SHA-256(nonce);
    # POST <strona>/_api/v1/handoff {challenge}; POST <proto>/_handoff (form: code) z cookie nonce; brak `state`.
    def begin(self, **kw):
        """Przeglądarka otwiera begin na originie prototypów: `(odpowiedź, 'nazwa=nonce', challenge)`."""
        response = self.proto_req('GET', '/_handoff/begin', **kw)
        assert response.status == 303, (response.status, response.body)
        pair = response.cookie().split(';')[0]
        challenge = re.search(r'challenge=([0-9a-f]{64})$', response.headers['location']).group(1)
        return response, pair, challenge

    def issue(self, who, challenge=None, **kw):
        """Wydanie kodu. Kontrakt U-1d: challenge musi być żywym nonce z `begin` i dostaje dokładnie jeden kod, więc bez `challenge`
        helper robi własny, świeży `begin` (jego nonce pod `self.last_pair`)."""
        if challenge is None:
            _response, self.last_pair, challenge = self.begin()
        return self.page_req('POST', '/_api/v1/handoff', body={'challenge': challenge}, headers=who.auth, **kw)

    def exchange(self, code, nonce_pair=None, headers=None, **kw):
        send = dict(headers or {})
        if nonce_pair:
            send['Cookie'] = nonce_pair
        return self.proto_req('POST', '/_handoff', form='code=%s' % quote(code or '', safe=''), headers=send, **kw)

    def flow(self, who):
        """Begin w przeglądarce + wydanie kodu ze strony: `(nonce_pair, challenge, odpowiedź wydania)`."""
        _response, pair, challenge = self.begin()
        return pair, challenge, self.issue(who, challenge)

    def handed(self, who):
        """Pełne przekazanie: begin -> wydanie -> wymiana; zwraca `nazwa=token` ciasteczka sesji originu prototypów."""
        pair, _challenge, issued = self.flow(who)
        assert issued.status == 200, (issued.status, issued.body)
        done = self.exchange(issued.json()['code'], pair)
        assert done.status == 303, (done.status, done.body)
        return done.cookie_named('skillsync_proto_').split(';')[0]


class Base(unittest.TestCase):
    def env(self, **kw):
        return Env(self, **kw)


class PageRoutesTests(Base):
    def test_login_page_has_csp_with_hash_of_its_only_inline_script(self):
        env = self.env()
        response = env.page_req('GET', '/_login')
        self.assertEqual(response.status, 200)
        page = response.body.decode('utf-8')
        scripts, hashes = sha_of_inline_script(page)
        self.assertEqual(len(scripts), 1, 'strona logowania ma dokładnie jeden skrypt inline')
        csp = response.headers['content-security-policy']
        self.assertIn(hashes[0], csp)
        script_src = [part.strip() for part in csp.split(';') if part.strip().startswith('script-src')]
        self.assertEqual(len(script_src), 1)
        self.assertNotIn('unsafe-inline', script_src[0])
        self.assertNotIn('unsafe-eval', script_src[0])
        self.assertNotIn("'self'", script_src[0], 'skrypt tylko z hasha, nic z originu')
        self.assertEqual(response.headers.get('x-frame-options'), 'DENY')
        self.assertIn('no-store', response.headers.get('cache-control', ''))

    def test_session_anonymous_and_login_cookie_flags(self):
        env = self.env()
        anonymous = env.page_req('GET', '/_api/v1/session')
        self.assertEqual(anonymous.status, 200)
        self.assertIsNone(anonymous.json()['user'])
        self.assertIsNone(anonymous.json()['csrf'])
        who = env.login('com@example.com', PW_COMMENTER)
        cookie = env.page_req('POST', '/_api/v1/login', body={'login': 'com@example.com', 'password': PW_COMMENTER}).cookie()
        self.assertTrue(cookie.startswith('skillsync_page_%s=' % env.app.server_id), cookie)
        for flag in ('HttpOnly', 'SameSite=Strict', 'Path=/'):
            self.assertIn(flag, cookie)
        me = env.page_req('GET', '/_api/v1/session', headers={'Cookie': who.pair}).json()
        self.assertEqual(me['user']['login'], 'com@example.com')
        self.assertEqual(me['csrf'], who.csrf)

    def test_login_refusals(self):
        env = self.env()
        bad = {'login': 'com@example.com', 'password': PW_COMMENTER + 'x'}
        self.assertEqual(env.page_req('POST', '/_api/v1/login', body=bad).status, 401)
        good = {'login': 'com@example.com', 'password': PW_COMMENTER}
        foreign = env.page_req('POST', '/_api/v1/login', body=good, origin='http://evil.example')
        self.assertEqual((foreign.status, foreign.json()['error']), (403, 'invalid_origin'))
        wrong_type = env.page_req('POST', '/_api/v1/login', body=good, ctype='text/plain')
        self.assertEqual((wrong_type.status, wrong_type.json()['error']), (403, 'invalid_content_type'))
        self.assertEqual(env.page_req('GET', '/_api/v1/login').status, 405)
        self.assertEqual(env.page_req('POST', '/_api/v1/login', body={'login': 'com@example.com'}).status, 400)

    def test_logout_needs_session_csrf_and_origin(self):
        env = self.env()
        self.assertEqual(env.page_req('POST', '/_api/v1/logout', body={}).status, 401)
        who = env.login('com@example.com', PW_COMMENTER)
        no_csrf = env.page_req('POST', '/_api/v1/logout', body={}, headers={'Cookie': who.pair})
        self.assertEqual((no_csrf.status, no_csrf.json()['error']), (403, 'invalid_csrf'))
        foreign = env.page_req('POST', '/_api/v1/logout', body={}, headers=who.auth, origin='http://evil.example')
        self.assertEqual((foreign.status, foreign.json()['error']), (403, 'invalid_origin'))
        self.assertEqual(env.page_req('GET', '/_api/v1/session', headers={'Cookie': who.pair}).json()['user']['login'], 'com@example.com')
        done = env.page_req('POST', '/_api/v1/logout', body={}, headers=who.auth)
        self.assertEqual(done.status, 204)
        self.assertIn('Max-Age=0', done.cookie())
        self.assertIsNone(env.page_req('GET', '/_api/v1/session', headers={'Cookie': who.pair}).json()['user'])

    def test_password_change(self):
        env = self.env()
        self.assertEqual(env.page_req('POST', '/_api/v1/password', body={'current': PW_COMMENTER, 'new': PW_NEW}).status, 401)
        who = env.login('com@example.com', PW_COMMENTER)
        wrong = env.page_req('POST', '/_api/v1/password', body={'current': PW_OTHER, 'new': PW_NEW}, headers=who.auth)
        self.assertEqual((wrong.status, wrong.json()['error']), (403, 'invalid_current'))
        weak = env.page_req('POST', '/_api/v1/password', body={'current': PW_COMMENTER, 'new': 'krotkie'}, headers=who.auth)
        self.assertEqual((weak.status, weak.json()['error']), (400, 'weak_password'))
        done = env.page_req('POST', '/_api/v1/password', body={'current': PW_COMMENTER, 'new': PW_NEW}, headers=who.auth)
        self.assertEqual(done.status, 204)
        fresh = done.cookie().split(';')[0]
        self.assertNotEqual(fresh, who.pair)
        self.assertIsNone(env.page_req('GET', '/_api/v1/session', headers={'Cookie': who.pair}).json()['user'], 'stary token nie działa')
        self.assertEqual(env.page_req('GET', '/_api/v1/session', headers={'Cookie': fresh}).json()['user']['login'], 'com@example.com')
        self.assertEqual(env.page_req('POST', '/_api/v1/login', body={'login': 'com@example.com', 'password': PW_COMMENTER}).status, 401)

    def test_must_change_allows_only_session_and_password(self):
        env = self.env()
        who = env.login('must@example.com', PW_DEFAULT)
        self.assertTrue(who.payload['mustChange'])
        session = env.page_req('GET', '/_api/v1/session', headers={'Cookie': who.pair})
        self.assertEqual(session.status, 200)
        self.assertTrue(session.json()['user']['mustChange'])
        refused = env.issue(who)
        self.assertEqual((refused.status, refused.json()['error']), (403, 'password_change_required'))
        self.assertEqual(env.app.handoffs.count(), 0)
        changed = env.page_req('POST', '/_api/v1/password', body={'current': PW_DEFAULT, 'new': PW_NEW}, headers=who.auth)
        self.assertEqual(changed.status, 204)
        fresh = Login(env, env.page_req('POST', '/_api/v1/login', body={'login': 'must@example.com', 'password': PW_NEW}))
        self.assertEqual(env.handed(fresh).split('=')[0], 'skillsync_proto_%s' % env.app.server_id, 'po zmianie hasła przekazanie działa')

    def test_must_change_may_still_sign_out(self):
        # kontrakt U-1c: handoff przy mustChange = 403, wylogowanie dozwolone
        env = self.env()
        who = env.login('must@example.com', PW_DEFAULT)
        self.assertEqual(env.issue(who).status, 403)
        self.assertEqual(env.page_req('POST', '/_api/v1/logout', body={}, headers=who.auth).status, 204)
        self.assertIsNone(env.page_req('GET', '/_api/v1/session', headers={'Cookie': who.pair}).json()['user'])

    def test_status_routes_unchanged_by_session_routes(self):
        with_app, without_app = self.env(), self.env(app=False)
        for path in ('/healthz', '/api/status', '/api/tabs'):
            a, b = with_app.page_req('GET', path), without_app.page_req('GET', path)
            self.assertEqual(a.status, b.status, path)
            self.assertEqual(a.headers.get('content-type'), b.headers.get('content-type'), path)
        self.assertEqual(with_app.page_req('GET', '/healthz').status, 200)
        self.assertEqual(with_app.page_req('GET', '/api/status').status, 200)
        self.assertEqual(sorted(with_app.page_req('GET', '/api/status').json()), sorted(without_app.page_req('GET', '/api/status').json()))
        self.assertEqual(with_app.page_req('GET', '/api/nie-ma').status, without_app.page_req('GET', '/api/nie-ma').status)

    def test_read_socket_has_no_login_routes(self):
        env = self.env()
        self.assertEqual(env.page_req('GET', '/_api/v1/session').status, 200)   # kontrola: ta sama trasa na stronie istnieje
        for path in ('/_login', '/_api/v1/session', '/_api/v1/handoff', '/_api/v1/login'):
            response = request(env.rp, 'GET', path)
            self.assertIn(response.status, (404, 405), path)
            self.assertNotIn(b'"csrf"', response.body, path)
        for path in ('/_api/v1/login', '/_api/v1/logout', '/_api/v1/password', '/_api/v1/handoff'):
            response = request(env.rp, 'POST', path, body={'login': 'com@example.com', 'password': PW_COMMENTER, 'challenge': FAKE_CHALLENGE})
            self.assertIn(response.status, (404, 405), path)
            self.assertIsNone(response.cookie(), path)
        self.assertEqual(request(env.rp, 'GET', '/healthz').status, 200)
        self.assertEqual(request(env.rp, 'GET', '/api/status').status, 200)

    def test_without_prototype_app_session_routes_are_off(self):
        env = self.env(app=False)
        for method, path in (('GET', '/_login'), ('GET', '/_api/v1/session'), ('POST', '/_api/v1/login'), ('POST', '/_api/v1/logout'),
                             ('POST', '/_api/v1/password'), ('POST', '/_api/v1/handoff')):
            response = env.page_req(method, path, body={'login': 'com@example.com', 'password': PW_COMMENTER} if method == 'POST' else None)
            self.assertEqual(response.status, 404, (method, path))
            self.assertIsNone(response.cookie())
        self.assertEqual(env.page_req('GET', '/healthz').status, 200)

    def test_local_only_refuses_non_loopback_peer_on_every_route(self):
        env = self.env(local_only=True)
        self.assertEqual(env.page_req('GET', '/healthz').status, 200)              # kontrola: peer z loopbacku przechodzi
        self.assertEqual(request(env.rp, 'GET', '/healthz').status, 200)
        self.assertEqual(env.page_req('GET', '/_api/v1/session').status, 200)
        with mock.patch.object(ps, 'link_is_loopback_peer', lambda address: False):
            for port in (env.pp, env.rp):
                for path in ('/healthz', '/api/status', '/_login', '/_api/v1/session', '/'):
                    response = request(port, 'GET', path)
                    self.assertEqual(response.status, 403, (port, path))
                    answer = response.json()
                    self.assertEqual(answer['code'], 'local_only', (port, path))   # kontrakt U-3a: kod w `code`, `error` to tekst dla człowieka
                    self.assertIn('--local-only', answer['error'], (port, path))
            for path in ('/_api/v1/login', '/_api/v1/handoff', '/api/approve'):
                response = request(env.pp, 'POST', path, body={'login': 'com@example.com', 'password': PW_COMMENTER})
                self.assertEqual(response.status, 403, path)
                self.assertIsNone(response.cookie())
        self.assertEqual(env.page_req('GET', '/healthz').status, 200)

    def test_without_local_only_a_remote_peer_is_not_refused_by_that_rule(self):
        env = self.env()
        with mock.patch.object(ps, 'link_is_loopback_peer', lambda address: False):
            self.assertEqual(request(env.pp, 'GET', '/healthz').status, 200)

    def test_host_rule_still_applies_to_session_routes(self):
        env = self.env()
        for method, path in (('GET', '/_login'), ('GET', '/_api/v1/session'), ('POST', '/_api/v1/login'), ('GET', '/healthz')):
            response = env.page_req(method, path, host='evil.example', origin=False,
                                    body={'login': 'com@example.com', 'password': PW_COMMENTER} if method == 'POST' else None)
            self.assertEqual(response.status, 403, (method, path))
            self.assertIsNone(response.cookie())
        self.assertEqual(env.page_req('GET', '/_api/v1/session', host='localhost:%d' % env.pp).status, 200)


class HandoffIssueTests(Base):
    def test_issue_requires_session_csrf_origin_and_same_site(self):
        env = self.env()
        challenge = env.begin()[2]   # kontrakt U-1d: challenge musi być żywym nonce z `begin`
        body = {'challenge': challenge}
        anonymous = env.page_req('POST', '/_api/v1/handoff', body=body)
        self.assertEqual(anonymous.status, 401)
        self.assertNotIn('code', anonymous.json())
        who = env.login('com@example.com', PW_COMMENTER)
        self.assertEqual(env.page_req('GET', '/_api/v1/handoff', headers=who.auth).status, 405)
        self.assertEqual(env.page_req('GET', '/_api/v1/handoff?challenge=%s' % challenge, headers={'Cookie': who.pair}).status, 405)
        no_csrf = env.page_req('POST', '/_api/v1/handoff', body=body, headers={'Cookie': who.pair})
        self.assertEqual((no_csrf.status, no_csrf.json()['error']), (403, 'invalid_csrf'))
        wrong_csrf = env.page_req('POST', '/_api/v1/handoff', body=body, headers={'Cookie': who.pair, 'X-Skillsync-CSRF': 'x' * 43})
        self.assertEqual(wrong_csrf.status, 403)
        foreign = env.page_req('POST', '/_api/v1/handoff', body=body, headers=who.auth, origin='http://127.0.0.1:%d' % env.xp)
        self.assertEqual((foreign.status, foreign.json()['error']), (403, 'invalid_origin'), 'żądanie z originu prototypów nie wydaje kodu')
        wrong_type = env.page_req('POST', '/_api/v1/handoff', body=body, headers=who.auth, ctype='text/plain')
        self.assertEqual(wrong_type.status, 403)
        for site in ('cross-site', 'same-site', 'none'):
            refused = env.page_req('POST', '/_api/v1/handoff', body=body, headers=dict(who.auth, **{'Sec-Fetch-Site': site}))
            self.assertEqual(refused.status, 403, site)
            self.assertNotIn('code', refused.json())
        self.assertEqual(env.app.handoffs.count(), 0, 'żadna odmowa nie zostawiła kodu w magazynie')
        same = env.page_req('POST', '/_api/v1/handoff', body=body, headers=dict(who.auth, **{'Sec-Fetch-Site': 'same-origin'}))
        self.assertEqual(same.status, 200, 'kontrola: poprawne żądanie wydaje kod')

    def test_issue_only_on_the_host_of_page_url(self):
        # kontrakt U-1c (m2): wydanie z innego hosta niż `page_url` = odmowa
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        other = 'localhost:%d' % env.pp
        refused = env.page_req('POST', '/_api/v1/handoff', body={'challenge': FAKE_CHALLENGE}, headers=who.auth, host=other, origin='http://' + other)
        self.assertEqual((refused.status, refused.json()['error']), (403, 'invalid_origin'))
        self.assertEqual(env.app.handoffs.count(), 0)
        self.assertEqual(env.issue(who).status, 200, 'kontrola: ten sam host co page_url')

    def test_challenge_must_be_a_sha256_hex(self):
        # kontrakt U-1c: `challenge` (64 znaki hex) zamiast `state`; stary `state` nie wydaje kodu
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        for bad in ('', 'krotki', 'a' * 63, 'a' * 65, 'g' * 64, 'A' * 64, 'ab' * 31 + ' x', 'S' + 'tAtE' * 6):
            self.assertEqual(env.issue(who, bad).status, 400, repr(bad))
        for bad in (None, 5, ['ab' * 32]):
            self.assertEqual(env.page_req('POST', '/_api/v1/handoff', body={'challenge': bad}, headers=who.auth).status, 400)
        self.assertEqual(env.page_req('POST', '/_api/v1/handoff', body={}, headers=who.auth).status, 400)
        self.assertEqual(env.page_req('POST', '/_api/v1/handoff', body={'state': 'S' + 'tAtE' * 6}, headers=who.auth).status, 400)
        self.assertEqual(env.app.handoffs.count(), 0)

    def test_no_prototype_url_means_404(self):
        env = self.env(prototype_url=False)
        who = env.login('com@example.com', PW_COMMENTER)
        response = env.issue(who)
        self.assertEqual((response.status, response.json()['error']), (404, 'handoff_unavailable'))
        self.assertEqual(env.app.handoffs.count(), 0)

    def test_code_is_strong_and_only_its_hash_is_stored(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        _pair, challenge, response = env.flow(who)
        self.assertEqual(response.status, 200)
        body = response.json()
        code = body['code']
        self.assertEqual(sorted(body), ['code', 'expires', 'target'])
        self.assertEqual(body['expires'], 60)
        self.assertEqual(body['target'], 'http://127.0.0.1:%d/_handoff' % env.xp, 'cel z konfiguracji, nie z żądania')
        self.assertRegex(code, r'^[A-Za-z0-9_-]+$')
        raw = base64.urlsafe_b64decode(code + '=' * (-len(code) % 4))
        self.assertGreaterEqual(len(raw) * 8, 128)
        other = env.issue(who).json()['code']   # kontrakt U-1d: jeden kod na challenge, drugi kod = świeży begin
        self.assertNotEqual(code, other)
        self.assertEqual(len(env.app.handoffs._items), 2)
        for key, entry in env.app.handoffs._items.items():
            self.assertNotIn(code, key)
            self.assertNotEqual(code, key)
            for value in entry.values():
                if isinstance(value, str):
                    self.assertNotIn(code, value)

    def test_nonce_table_keeps_only_its_hash(self):
        env = self.env()
        _response, pair, challenge = env.begin()
        nonce = pair.split('=', 1)[1]
        self.assertEqual(challenge, hashlib.sha256(nonce.encode('ascii')).hexdigest(), 'challenge = SHA-256(nonce)')
        raw = base64.urlsafe_b64decode(nonce + '=' * (-len(nonce) % 4))
        self.assertGreaterEqual(len(raw) * 8, 128)
        self.assertEqual(env.app.handoffs.nonce_count(), 1)
        self.assertNotIn(nonce, list(env.app.handoffs._nonces))
        self.assertIn(challenge, env.app.handoffs._nonces)

    def test_redirect_target_cannot_be_chosen_by_request(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        challenge = env.begin()[2]
        response = env.page_req('POST', '/_api/v1/handoff?return=http://evil.example/', body={
            'challenge': challenge, 'return': 'http://evil.example/', 'target': 'http://evil.example/'}, headers=who.auth)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.json()['target'], 'http://127.0.0.1:%d/_handoff' % env.xp)
        self.assertNotIn(b'evil', response.body)

    def test_limits_per_account_and_total(self):
        env = self.env()
        self.assertEqual((ps.HANDOFF_PER_LOGIN, ps.HANDOFF_MAX_PENDING), (8, 256))
        who = env.login('com@example.com', PW_COMMENTER)
        for _ in range(8):
            self.assertEqual(env.issue(who).status, 200)
        ninth = env.issue(who)
        self.assertEqual((ninth.status, ninth.json()['error']), (429, 'rate_limited'))
        self.assertTrue(ninth.headers.get('retry-after'))
        other = env.login('two@example.com', PW_OTHER)
        self.assertEqual(env.issue(other).status, 200, 'limit jest per konto')
        store = ps.HandoffStore(clock=env.clock)
        issued = 0
        for number in range(40):
            fake = SimpleNamespace(login='user%d' % number, password_ref='x')
            for index in range(8):
                _nonce, live = store.begin('src-%d-%d' % (number, index))   # kontrakt U-1d: kod tylko dla żywego challenge
                result = store.issue(fake, live, 'a', 'b')
                self.assertIsNot(result, False)
                if result is not None:
                    issued += 1
        self.assertEqual(issued, 256, 'limit łączny 256 kodów')
        self.assertEqual(store.count(), 256)

    def test_expired_codes_free_the_limit(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        for _ in range(8):
            env.issue(who)
        self.assertEqual(env.issue(who).status, 429)
        env.clock.advance(61)
        who2 = env.login('com@example.com', PW_COMMENTER)
        self.assertEqual(env.issue(who2).status, 200)


class HandoffBeginTests(Base):
    def test_begin_sets_nonce_cookie_and_redirects_to_fixed_page_origin(self):
        env = self.env()
        response, pair, challenge = env.begin()
        self.assertEqual(response.headers['location'], 'http://127.0.0.1:%d/_handoff?challenge=%s' % (env.pp, challenge))
        self.assertEqual(response.headers['referrer-policy'], 'no-referrer')
        cookie = response.cookie()
        self.assertTrue(cookie.startswith('skillsync_hs_%s=' % env.app.server_id), cookie)
        for flag in ('HttpOnly', 'SameSite=Strict', 'Path=/_handoff', 'Max-Age=120'):
            self.assertIn(flag, cookie)
        self.assertEqual(env.app.handoffs.nonce_count(), 1)
        _r2, pair2, challenge2 = env.begin()
        self.assertNotEqual((pair, challenge), (pair2, challenge2), 'każdy begin ma świeży nonce')

    def test_begin_target_ignores_request_parameters(self):
        env = self.env()
        response = env.proto_req('GET', '/_handoff/begin?return=http://evil.example/&next=http://evil.example/')
        self.assertTrue(response.headers['location'].startswith('http://127.0.0.1:%d/_handoff?challenge=' % env.pp))
        self.assertNotIn('evil', response.headers['location'])

    def test_begin_only_on_proto_origin_with_page_url(self):
        env = self.env()
        self.assertEqual(env.page_req('GET', '/_handoff/begin').status, 404)
        self.assertEqual(env.read and request(env.rp, 'GET', '/_handoff/begin').status, 404)
        self.assertEqual(env.proto_req('POST', '/_handoff/begin', form='').status, 405)
        bare = self.env(page_url=False)
        self.assertEqual(bare.proto_req('GET', '/_handoff/begin').status, 404)
        self.assertEqual(bare.app.handoffs.nonce_count(), 0)

    def test_begin_is_limited_per_source(self):
        # kontrakt U-1d (N3): 20 begin na minutę ze źródła, potem 429 z Retry-After
        env = self.env()
        for _ in range(20):
            self.assertEqual(env.proto_req('GET', '/_handoff/begin').status, 303)
        limited = env.proto_req('GET', '/_handoff/begin')
        self.assertEqual(limited.status, 429)
        self.assertTrue(limited.headers.get('retry-after'), limited.headers)
        self.assertIsNone(limited.cookie_named('skillsync_hs_'), 'odmowa nie daje nonce')
        env.clock.advance(61)
        self.assertEqual(env.proto_req('GET', '/_handoff/begin').status, 303, 'okno minęło')

    def test_nonces_per_source_drop_the_oldest_of_that_source(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        _r, first_pair, first_challenge = env.begin()
        code = env.issue(who, first_challenge).json()['code']
        for _ in range(ps.HANDOFF_NONCES_PER_SOURCE):
            env.begin()
        self.assertEqual(env.app.handoffs.nonce_count(), ps.HANDOFF_NONCES_PER_SOURCE)
        self.assertEqual(env.exchange(code, first_pair).status, 400, 'najstarszy nonce źródła wypadł z tabeli')

    def test_full_table_never_evicts_the_nonce_of_another_source(self):
        store = ps.HandoffStore(clock=Clock())
        victim_nonce, victim_challenge = store.begin('victim')
        for number in range(40):
            for index in range(8):
                nonce, _challenge = store.begin('flood-%d' % number)
                self.assertIsNotNone(nonce)
        self.assertEqual(store.nonce_count(), ps.HANDOFF_MAX_NONCES, 'tabela pełna')
        self.assertIn(victim_challenge, store._nonces, 'nonce innego źródła przeżył zalew')
        # kontrola: źródło ponad swój udział traci własne nonce
        sources = [entry['source'] for entry in store._nonces.values()]
        self.assertEqual(sources.count('victim'), 1)

    def test_nonce_expires_after_120_seconds(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        _r, pair, challenge = env.begin()
        env.clock.advance(100)
        code = env.issue(who, challenge).json()['code']
        env.clock.advance(19)    # nonce ma 119 s, kod 19 s
        self.assertEqual(env.exchange(code, pair).status, 303, 'kontrola: przed 120 s działa')
        _r, pair, challenge = env.begin()
        env.clock.advance(100)
        code = env.issue(who, challenge).json()['code']
        env.clock.advance(25)    # nonce ma 125 s, kod tylko 25 s
        late = env.exchange(code, pair)
        self.assertEqual(late.status, 400, 'nonce po 120 s nie wymienia kodu')
        self.assertIsNone(late.cookie_named('skillsync_proto_'))


class HandoffExchangeTests(Base):
    def test_exchange_page_get(self):
        env = self.env()
        response = env.proto_req('GET', '/_handoff')
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers['referrer-policy'], 'same-origin')   # `no-referrer` makes a browser post the form with `Origin: null`
        page = response.body.decode('utf-8')
        scripts, hashes = sha_of_inline_script(page)
        self.assertEqual(len(scripts), 1)
        self.assertIn(hashes[0], response.headers['content-security-policy'])
        self.assertNotIn('unsafe-inline', response.headers['content-security-policy'])
        self.assertIn('method="post"', page)
        self.assertNotIn('name="state"', page, 'kontrakt U-1c: brak pola state')
        self.assertIn('no-store', response.headers.get('cache-control', ''))

    def test_exchange_without_page_url_is_404(self):
        env = self.env(page_url=False)
        self.assertEqual(env.proto_req('GET', '/_handoff').status, 404)
        self.assertEqual(env.proto_req('POST', '/_handoff', form='code=%s' % ('a' * 43)).status, 404)

    def test_handoff_page_on_the_page_origin(self):
        # kontrakt U-1c: GET /_handoff na originie strony jest teraz stroną, która prosi o kod (200, CSP z hashem skryptu);
        # wymiana (POST) tam nie istnieje.
        env = self.env()
        response = env.page_req('GET', '/_handoff?challenge=%s' % FAKE_CHALLENGE)
        self.assertEqual(response.status, 200)
        scripts, hashes = sha_of_inline_script(response.body.decode('utf-8'))
        self.assertEqual(len(scripts), 1)
        csp = response.headers['content-security-policy']
        self.assertIn(hashes[0], csp)
        script_src = [part.strip() for part in csp.split(';') if part.strip().startswith('script-src')][0]
        self.assertNotIn('unsafe-inline', script_src)
        self.assertNotIn(FAKE_CHALLENGE, response.body.decode('utf-8'), 'challenge czyta skrypt z URL, nie serwer')
        bare = self.env(prototype_url=False)
        self.assertEqual(bare.page_req('GET', '/_handoff').status, 404, 'bez prototype_url strona przekazania nie istnieje')
        post = env.page_req('POST', '/_handoff', form='code=%s' % ('a' * 43))
        self.assertIn(post.status, (404, 405))
        self.assertIsNone(post.cookie())

    def test_full_exchange_303_clean_address_and_cookies(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        pair, _challenge, issued = env.flow(who)
        code = issued.json()['code']
        response = env.exchange(code, pair)
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers['location'], '/')
        self.assertEqual(response.headers['referrer-policy'], 'no-referrer')
        cookie = response.cookie_named('skillsync_proto_')
        self.assertTrue(cookie.startswith('skillsync_proto_%s=' % env.app.server_id), cookie)
        for flag in ('HttpOnly', 'SameSite=Strict', 'Path=/'):
            self.assertIn(flag, cookie)
        spent = response.cookie_named('skillsync_hs_')
        self.assertIsNotNone(spent, 'cookie nonce kasowane w odpowiedzi')
        self.assertIn('Max-Age=0', spent)
        self.assertNotIn(code, json.dumps(response.raw_headers))
        me = env.proto_req('GET', '/_api/v1/session', headers={'Cookie': cookie.split(';')[0]}).json()
        self.assertEqual(me['user']['login'], 'com@example.com')

    def test_exchange_takes_code_from_form_only(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        pair, _challenge, issued = env.flow(who)
        code = issued.json()['code']
        by_query = env.proto_req('POST', '/_handoff?code=%s' % code, form='', headers={'Cookie': pair})
        self.assertEqual(by_query.status, 400)
        self.assertIsNone(by_query.cookie_named('skillsync_proto_'))
        by_get = env.proto_req('GET', '/_handoff?code=%s' % code, headers={'Cookie': pair})
        self.assertEqual(by_get.status, 200)
        self.assertIsNone(by_get.cookie_named('skillsync_proto_'))
        by_json = env.proto_req('POST', '/_handoff', body={'code': code}, headers={'Cookie': pair})
        self.assertEqual(by_json.status, 400)
        self.assertIsNone(by_json.cookie_named('skillsync_proto_'))

    def test_exchange_needs_same_origin_post(self):
        # kontrakt U-1c (m1): Origin musi być tym Hostem albo Sec-Fetch-Site same-origin; brak obu = 400
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        pair, challenge, issued = env.flow(who)
        code = issued.json()['code']
        foreign = env.exchange(code, pair, origin='http://evil.example')
        self.assertEqual(foreign.status, 403)
        self.assertIsNone(foreign.cookie_named('skillsync_proto_'))
        mismatch = env.exchange(code, pair, origin='http://127.0.0.1:%d' % env.pp)
        self.assertEqual(mismatch.status, 403, 'Origin strony to nie Origin prototypów')
        self.assertIsNone(mismatch.cookie_named('skillsync_proto_'))
        bare = env.exchange(code, pair, origin=False)
        self.assertEqual(bare.status, 400, 'brak Origin i Sec-Fetch-Site')
        self.assertIsNone(bare.cookie_named('skillsync_proto_'))
        cross = env.exchange(code, pair, origin=False, headers={'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(cross.status, 400)
        ok = env.exchange(code, pair, origin=False, headers={'Sec-Fetch-Site': 'same-origin'})
        self.assertEqual(ok.status, 303, 'kontrola: same-origin bez Origin przechodzi, kod nie został spalony odmowami')

    def test_attacker_link_without_nonce_cookie_is_refused(self):
        # link napastnika niesie cudzy kod, ale ofiara nie ma cookie nonce z begin
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        _pair, _challenge, issued = env.flow(who)
        code = issued.json()['code']
        response = env.exchange(code)
        self.assertEqual(response.status, 400)
        self.assertIsNone(response.cookie_named('skillsync_proto_'))
        victim_nonce = env.begin()[1]   # ofiara ma własny begin, ale nonce nie pasuje do challenge kodu
        again = env.exchange(code, victim_nonce)
        self.assertEqual(again.status, 400)
        self.assertIsNone(again.cookie_named('skillsync_proto_'))
        garbage = env.exchange(code, 'skillsync_hs_%s=%s' % (env.app.server_id, 'x' * 22))
        self.assertEqual(garbage.status, 400)

    def test_other_browsers_nonce_is_refused_and_spent(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        _r, pair_a, challenge_a = env.begin()
        _r, pair_b, challenge_b = env.begin()
        code_a = env.issue(who, challenge_a).json()['code']
        wrong = env.exchange(code_a, pair_b)
        self.assertEqual(wrong.status, 400, 'nonce innej przeglądarki nie wymienia kodu')
        self.assertIsNone(wrong.cookie_named('skillsync_proto_'))
        self.assertEqual(env.issue(who, challenge_b).status, 400, 'nonce B zużyty także w nieudanej próbie (nie dostaje już kodu)')
        self.assertEqual(env.exchange(code_a, pair_a).status, 400, 'kod A spalony próbą z cudzym nonce')
        pair_c, _challenge_c, issued = env.flow(who)
        self.assertEqual(env.exchange(issued.json()['code'], pair_c).status, 303, 'kontrola: właściwa para kod+nonce działa')

    def test_nonce_is_spent_in_every_outcome(self):
        # kontrakt U-1d: zużyty nonce nie jest już żywym challenge, więc nie dostaje kodu (400) ani po porażce, ani po sukcesie
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        _r, pair, challenge = env.begin()
        self.assertEqual(env.app.handoffs.nonce_count(), 1)
        self.assertEqual(env.exchange('A' * 43, pair).status, 400)   # nieudana wymiana
        self.assertEqual(env.app.handoffs.nonce_count(), 0)
        again = env.issue(who, challenge)
        self.assertEqual((again.status, again.json()['error']), (400, 'invalid_request'), 'zużyty nonce nie dostaje kodu')
        self.assertEqual(env.app.handoffs.count(), 0)
        pair, challenge, issued = env.flow(who)
        self.assertEqual(env.exchange(issued.json()['code'], pair).status, 303)    # udana wymiana
        self.assertEqual(env.app.handoffs.nonce_count(), 0)
        self.assertEqual(env.issue(who, challenge).status, 400, 'nonce nie służy drugi raz po sukcesie')
        self.assertEqual(env.exchange(issued.json()['code'], pair).status, 400)

    def test_code_is_single_use(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        pair, challenge, issued = env.flow(who)
        code = issued.json()['code']
        self.assertEqual(env.exchange(code, pair).status, 303)
        _r, pair2, challenge2 = env.begin()
        again = env.exchange(code, pair2)   # nowy nonce, ten sam kod: kod już zużyty
        self.assertEqual(again.status, 400)
        self.assertIsNone(again.cookie_named('skillsync_proto_'))
        self.assertEqual(env.app.handoffs.count(), 0)

    def test_code_race_exactly_one_wins(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        pair, _challenge, issued = env.flow(who)
        code = issued.json()['code']
        results = []
        barrier = threading.Barrier(6)

        def worker():
            barrier.wait()
            results.append(env.exchange(code, pair).status)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 6)
        self.assertEqual(results.count(303), 1, results)
        self.assertEqual(results.count(400), 5, results)

    def test_code_expires_after_60_seconds(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        pair_old, _c, old = env.flow(who)
        pair_young, _c, young = env.flow(who)
        env.clock.advance(59)
        self.assertEqual(env.exchange(young.json()['code'], pair_young).status, 303, 'po 59 s kod jeszcze działa')
        env.clock.advance(2)    # nonce ma 61 s (TTL 120), więc odmowa wynika z TTL kodu
        late = env.exchange(old.json()['code'], pair_old)
        self.assertEqual(late.status, 400, 'po 61 s kod nie działa')
        self.assertIsNone(late.cookie_named('skillsync_proto_'))

    def test_refusals_look_the_same(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        pair, _challenge, issued = env.flow(who)
        _r, pair_b, _cb = env.begin()
        wrong_nonce = env.exchange(issued.json()['code'], pair_b)
        unknown = env.exchange('A' * 43, pair)
        malformed = env.exchange('krotki')
        self.assertEqual({wrong_nonce.status, unknown.status, malformed.status}, {400})
        self.assertEqual(wrong_nonce.body, unknown.body)
        self.assertEqual(unknown.body, malformed.body)

    def test_code_bound_to_account(self):
        env = self.env()
        first = env.login('com@example.com', PW_COMMENTER)
        second = env.login('two@example.com', PW_OTHER)
        cookie = env.handed(first)
        self.assertEqual(env.proto_req('GET', '/_api/v1/session', headers={'Cookie': cookie}).json()['user']['login'], 'com@example.com')
        cookie2 = env.handed(second)
        self.assertEqual(env.proto_req('GET', '/_api/v1/session', headers={'Cookie': cookie2}).json()['user']['login'], 'two@example.com')

    def test_code_bound_to_origin_pair(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        pair, _challenge, issued = env.flow(who)
        elsewhere = env.exchange(issued.json()['code'], pair, host='localhost:%d' % env.xp, origin='http://localhost:%d' % env.xp)
        self.assertEqual(elsewhere.status, 400, 'inny Host originu prototypów nie wymienia kodu')
        self.assertIsNone(elsewhere.cookie_named('skillsync_proto_'))
        self.assertEqual(env.handed(who).split('=')[0], 'skillsync_proto_%s' % env.app.server_id)

    def test_code_dies_with_page_logout_and_password_change(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        pair, _challenge, issued = env.flow(who)
        self.assertEqual(env.page_req('POST', '/_api/v1/logout', body={}, headers=who.auth).status, 204)
        self.assertEqual(env.exchange(issued.json()['code'], pair).status, 400, 'kod wydany z wylogowanej sesji nie działa')
        again = env.login('com@example.com', PW_COMMENTER)
        pair, _challenge, issued = env.flow(again)
        self.assertEqual(env.page_req('POST', '/_api/v1/password', body={'current': PW_COMMENTER, 'new': PW_NEW}, headers=again.auth).status, 204)
        self.assertEqual(env.exchange(issued.json()['code'], pair).status, 400, 'kod sprzed zmiany hasła nie działa')

    def test_secrets_not_in_logs_or_audit(self):
        env = self.env(runtime=True)
        who = env.login('ap@example.com', PW_APPROVER)
        pair, _challenge, issued = env.flow(who)
        code = issued.json()['code']
        nonce = pair.split('=', 1)[1]
        done = env.exchange(code, pair)
        self.assertEqual(done.status, 303)
        token = done.cookie_named('skillsync_proto_').split(';')[0].split('=', 1)[1]
        env.exchange(code, pair)
        audit = (Path(env.run_dir) / 'audit.jsonl').read_text()
        events = [json.loads(line)['event'] for line in audit.splitlines() if line.strip()]
        self.assertIn('handoff_issued', events)
        self.assertIn('handoff_redeemed', events)
        for secret in (code, nonce, token, who.token(), who.csrf):
            self.assertNotIn(secret, audit)
            self.assertNotIn(secret, env.log.getvalue())
        self.assertNotIn(code, json.dumps(done.raw_headers))
        self.assertNotIn(code, json.dumps(issued.raw_headers))


class HandedSessionTests(Base):
    def test_handed_session_has_no_privileged_roles(self):
        env = self.env()
        who = env.login('ap@example.com', PW_APPROVER)
        self.assertEqual(set(who.payload['roles']), {'approver', 'acceptor', 'developer', 'expert'}, 'kontrola: strona widzi pełne role')
        cookie = env.handed(who)
        me = env.proto_req('GET', '/_api/v1/session', headers={'Cookie': cookie}).json()
        self.assertEqual(me['user']['login'], 'ap@example.com')
        for role in ('approver', 'acceptor', 'developer'):
            self.assertNotIn(role, me['user']['roles'])
        self.assertIn('expert', me['user']['roles'], 'pozostałe role zostają')
        self.assertNotIn(me['user']['role'], ('approver', 'acceptor', 'developer'))
        session, _user = env.app.sessions.lookup(cookie.split('=', 1)[1], env.app.users, 'proto')
        self.assertEqual((session.via, session.kind), ('handoff', 'proto'))
        self.assertFalse(session.limited, 'sesja przekazana nie ma limitu approvera')

    def test_account_with_only_privileged_roles_becomes_commenter(self):
        env = self.env()
        who = env.login('acc@example.com', PW_COMMENTER)
        self.assertIn('acceptor', who.payload['roles'])
        cookie = env.handed(who)
        roles = env.proto_req('GET', '/_api/v1/session', headers={'Cookie': cookie}).json()['user']['roles']
        self.assertEqual(roles, ['commenter'])

    def test_handed_session_cannot_accept_a_project(self):
        body = {'cid': 'cid-handoff-1', 'versions': {}}
        # kontrola: ta sama rola acceptor/developer zalogowana bezpośrednio na samodzielnym originie prototypów przechodzi bramkę roli
        direct = self.env(page_url=False)
        login = direct.proto_req('POST', '/_api/v1/login', body={'login': 'acc@example.com', 'password': PW_COMMENTER})
        self.assertEqual(login.status, 200, login.body)
        csrf = login.json()['csrf']
        allowed = direct.proto_req('POST', '/_api/v1/project/accept', body=body, headers={'Cookie': login.cookie().split(';')[0], 'X-Skillsync-CSRF': csrf})
        self.assertNotEqual((allowed.status, allowed.json().get('error')), (403, 'forbidden'), allowed.body)
        env = self.env()
        who = env.login('acc@example.com', PW_COMMENTER)
        cookie = env.handed(who)
        csrf = env.proto_req('GET', '/_api/v1/session', headers={'Cookie': cookie}).json()['csrf']
        response = env.proto_req('POST', '/_api/v1/project/accept', body=body, headers={'Cookie': cookie, 'X-Skillsync-CSRF': csrf})
        self.assertEqual((response.status, response.json()['error']), (403, 'forbidden'), response.body)

    def test_cookie_is_origin_specific_both_ways(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        proto_cookie = env.handed(who)
        page_name = 'skillsync_page_%s' % env.app.server_id
        proto_name = 'skillsync_proto_%s' % env.app.server_id
        self.assertEqual(who.pair.split('=')[0], page_name)
        self.assertEqual(proto_cookie.split('=')[0], proto_name)
        self.assertNotEqual(page_name, proto_name)
        under_proto_name = '%s=%s' % (proto_name, who.token())
        self.assertIsNone(env.proto_req('GET', '/_api/v1/session', headers={'Cookie': under_proto_name}).json()['user'])
        under_page_name = '%s=%s' % (page_name, proto_cookie.split('=', 1)[1])
        self.assertIsNone(env.page_req('GET', '/_api/v1/session', headers={'Cookie': under_page_name}).json()['user'])
        self.assertEqual(env.page_req('GET', '/_api/v1/session', headers={'Cookie': who.pair}).json()['user']['login'], 'com@example.com')
        self.assertEqual(env.proto_req('GET', '/_api/v1/session', headers={'Cookie': proto_cookie}).json()['user']['login'], 'com@example.com')
        self.assertIsNone(env.proto_req('GET', '/_api/v1/session', headers={'Cookie': who.pair}).json()['user'])
        self.assertIsNone(env.page_req('GET', '/_api/v1/session', headers={'Cookie': proto_cookie}).json()['user'])

    def test_page_logout_ends_handed_sessions(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        cookie = env.handed(who)
        self.assertEqual(env.proto_req('GET', '/_api/v1/session', headers={'Cookie': cookie}).json()['user']['login'], 'com@example.com')
        self.assertEqual(env.page_req('POST', '/_api/v1/logout', body={}, headers=who.auth).status, 204)
        self.assertIsNone(env.proto_req('GET', '/_api/v1/session', headers={'Cookie': cookie}).json()['user'])

    def test_logout_of_one_page_session_keeps_children_of_another(self):
        env = self.env()
        first = env.login('com@example.com', PW_COMMENTER)
        second = env.login('com@example.com', PW_COMMENTER)
        cookie_second = env.handed(second)
        self.assertEqual(env.page_req('POST', '/_api/v1/logout', body={}, headers=first.auth).status, 204)
        self.assertEqual(env.proto_req('GET', '/_api/v1/session', headers={'Cookie': cookie_second}).json()['user']['login'], 'com@example.com')

    def test_page_password_change_ends_handed_sessions(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        cookie = env.handed(who)
        other = env.login('two@example.com', PW_OTHER)
        other_cookie = env.handed(other)
        done = env.page_req('POST', '/_api/v1/password', body={'current': PW_COMMENTER, 'new': PW_NEW}, headers=who.auth)
        self.assertEqual(done.status, 204)
        self.assertIsNone(env.proto_req('GET', '/_api/v1/session', headers={'Cookie': cookie}).json()['user'])
        self.assertEqual(env.proto_req('GET', '/_api/v1/session', headers={'Cookie': other_cookie}).json()['user']['login'], 'two@example.com')

    def test_parent_expiry_ends_handed_session(self):
        # kontrakt U-1c (W4): wygasły rodzic kończy potomka także wtedy, gdy rodzic nie został usunięty z magazynu
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        cookie = env.handed(who)
        hour = 3600
        env.clock.advance(11 * hour)   # rodzic nieaktywny (12 h bezczynności), potomek używany
        self.assertEqual(env.proto_req('GET', '/_api/v1/session', headers={'Cookie': cookie}).json()['user']['login'], 'com@example.com')
        env.clock.advance(2 * hour)    # rodzic ma 13 h bezczynności
        # potomek najpierw: nikt nie dotknął rodzica, więc nie został przy tym usunięty z magazynu
        self.assertIsNone(env.proto_req('GET', '/_api/v1/session', headers={'Cookie': cookie}).json()['user'], 'potomek kończy się z rodzicem')
        self.assertIsNone(env.page_req('GET', '/_api/v1/session', headers={'Cookie': who.pair}).json()['user'], 'kontrola: rodzic wygasł')

    def test_approver_parent_expiry_ends_handed_session(self):
        env = self.env(runtime=True)   # kontrakt U-1c (W5): logowanie approvera wymaga katalogu audytu
        who = env.login('ap@example.com', PW_APPROVER)
        cookie = env.handed(who)
        env.clock.advance(3600)
        self.assertEqual(env.proto_req('GET', '/_api/v1/session', headers={'Cookie': cookie}).json()['user']['login'], 'ap@example.com')
        env.clock.advance(3600 + 60)   # 2 h 1 min bez zapisu na stronie: sesja approvera wygasła
        self.assertIsNone(env.proto_req('GET', '/_api/v1/session', headers={'Cookie': cookie}).json()['user'], 'potomek kończy się z rodzicem')
        self.assertIsNone(env.page_req('GET', '/_api/v1/session', headers={'Cookie': who.pair}).json()['user'], 'kontrola: rodzic wygasł')

    def test_child_cookie_max_age_never_exceeds_the_parent(self):
        # kontrakt U-1c (W4): Max-Age ciasteczka dziecka <= pozostały czas rodzica
        env = self.env()
        commenter = env.login('com@example.com', PW_COMMENTER)
        pair, _challenge, issued = env.flow(commenter)
        done = env.exchange(issued.json()['code'], pair)
        age = int(re.search(r'Max-Age=(\d+)', done.cookie_named('skillsync_proto_')).group(1))
        self.assertTrue(1 <= age <= 12 * 3600, age)
        self.assertLess(age, ps.SESSION_MAX)
        approver = env.login('ap@example.com', PW_APPROVER)
        for _ in range(11):            # zapisy podtrzymują bezczynność approvera (2 h); do 12 h maksimum zostaje godzina
            env.clock.advance(3600)
            session, _user = env.app.sessions.lookup(approver.token(), env.app.users, 'page')
            env.app.sessions.touch(session)   # zapis człowieka na stronie (samo wydanie kodu nie odnawia bezczynności)
            self.assertEqual(env.issue(approver).status, 200)
        pair, _challenge, issued = env.flow(approver)
        done = env.exchange(issued.json()['code'], pair)
        age = int(re.search(r'Max-Age=(\d+)', done.cookie_named('skillsync_proto_')).group(1))
        self.assertTrue(1 <= age <= 3600, age)

    def test_link_and_handoff_sessions_carry_narrowed_roles_in_routes(self):
        # kontrakt U-1c (W3): efektywne role sesji link i handoff są zawężone w `_session()` i w trasach
        env = self.env()
        who = env.login('ap@example.com', PW_APPROVER)
        cookie = env.handed(who)
        session, user = env.app.sessions.lookup(cookie.split('=', 1)[1], env.app.users, 'proto')
        narrowed = env.app.restrict_user(session, user)
        self.assertEqual(sorted(narrowed['roles']), ['expert'])
        self.assertEqual(sorted(user['roles']), ['acceptor', 'approver', 'developer', 'expert'], 'rekord w pliku zostaje pełny')
        for via in ('link', 'handoff'):
            fake = SimpleNamespace(via=via)
            self.assertEqual(env.app.effective_roles(user, fake), ['expert'], via)
            only = env.app.users.get('acc@example.com')
            self.assertEqual(env.app.effective_roles(only, fake), ['commenter'], 'pusty zestaw = commenter (%s)' % via)
        self.assertEqual(sorted(env.app.effective_roles(user, SimpleNamespace(via=None))), ['acceptor', 'approver', 'developer', 'expert'])

    def test_proto_origin_refuses_login_and_password_when_page_is_configured(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        cookie = env.handed(who)
        login = env.proto_req('POST', '/_api/v1/login', body={'login': 'com@example.com', 'password': PW_COMMENTER})
        self.assertEqual((login.status, login.json()['error']), (403, 'login_on_page'))
        self.assertIsNone(login.cookie())
        csrf = env.proto_req('GET', '/_api/v1/session', headers={'Cookie': cookie}).json()['csrf']
        change = env.proto_req('POST', '/_api/v1/password', body={'current': PW_COMMENTER, 'new': PW_NEW}, headers={'Cookie': cookie, 'X-Skillsync-CSRF': csrf})
        self.assertEqual((change.status, change.json()['error']), (403, 'login_on_page'))
        env.app.users.authenticate('com@example.com', PW_COMMENTER)   # hasło bez zmian (raise = błąd)
        self.assertEqual(env.page_req('POST', '/_api/v1/login', body={'login': 'com@example.com', 'password': PW_COMMENTER}).status, 200)

    def test_proto_login_page_redirects_to_page_origin(self):
        env = self.env()
        response = env.proto_req('GET', '/_login')
        self.assertEqual(response.status, 302)
        self.assertEqual(response.headers['location'], 'http://127.0.0.1:%d/_login' % env.pp)
        response = env.proto_req('GET', '/_login?next=http://evil.example/')
        self.assertEqual(response.headers['location'], 'http://127.0.0.1:%d/_login' % env.pp, 'stały cel z konfiguracji')

    def test_proto_origin_has_no_handoff_issue_route(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        cookie = env.handed(who)
        csrf = env.proto_req('GET', '/_api/v1/session', headers={'Cookie': cookie}).json()['csrf']
        response = env.proto_req('POST', '/_api/v1/handoff', body={'challenge': FAKE_CHALLENGE}, headers={'Cookie': cookie, 'X-Skillsync-CSRF': csrf})
        self.assertNotEqual(response.status, 200)
        self.assertNotIn(b'"code"', response.body, 'origin prototypów nie wydaje kodów (sesja przekazana nie przekazuje dalej)')


class LinkRolesTests(Base):
    """Kontrakt U-1c (W3): sesja linku i sesja przekazana mają efektywne role bez approver/acceptor/developer."""

    def _session_cookie(self, env, login, via, kind):
        user = env.app.users.get(login)
        session = env.app.sessions.create(login, user['password'], via=via, kind=kind)
        return '%s=%s' % (env.app.cookie_name_for(False, kind), session.token)

    def test_link_session_is_narrowed_in_routes(self):
        env = self.env()
        cookie = self._session_cookie(env, 'ap@example.com', 'link', 'page')
        me = env.page_req('GET', '/_api/v1/session', headers={'Cookie': cookie}).json()
        self.assertEqual(me['user']['roles'], ['expert'])
        proto = self._session_cookie(env, 'acc@example.com', 'link', 'proto')
        csrf = env.proto_req('GET', '/_api/v1/session', headers={'Cookie': proto}).json()['csrf']
        refused = env.proto_req('POST', '/_api/v1/project/accept', body={'cid': 'cid-link-1', 'versions': {}},
                                headers={'Cookie': proto, 'X-Skillsync-CSRF': csrf})
        self.assertEqual((refused.status, refused.json()['error']), (403, 'forbidden'), 'sesja linku nie akceptuje projektu')
        control = self._session_cookie(env, 'acc@example.com', None, 'proto')
        csrf = env.proto_req('GET', '/_api/v1/session', headers={'Cookie': control}).json()['csrf']
        allowed = env.proto_req('POST', '/_api/v1/project/accept', body={'cid': 'cid-link-2', 'versions': {}},
                                headers={'Cookie': control, 'X-Skillsync-CSRF': csrf})
        self.assertNotEqual((allowed.status, allowed.json().get('error')), (403, 'forbidden'), 'kontrola: zwykła sesja tego konta przechodzi bramkę')

    def test_empty_effective_set_is_commenter(self):
        env = self.env()
        ps.add_user(env.app.users, 'solo@example.com', roles=['acceptor', 'developer'], password=PW_COMMENTER)
        for via in ('link', 'handoff'):
            cookie = self._session_cookie(env, 'solo@example.com', via, 'page')
            me = env.page_req('GET', '/_api/v1/session', headers={'Cookie': cookie}).json()
            self.assertEqual(me['user']['roles'], ['commenter'], via)

    def test_password_change_and_audit_count_roles_from_the_file(self):
        env = self.env()
        cookie = self._session_cookie(env, 'ap@example.com', 'link', 'page')
        csrf = env.page_req('GET', '/_api/v1/session', headers={'Cookie': cookie}).json()['csrf']
        auth = {'Cookie': cookie, 'X-Skillsync-CSRF': csrf}
        weak = env.page_req('POST', '/_api/v1/password', body={'current': PW_APPROVER, 'new': 'Krotkie-' + '12'}, headers=auth)
        self.assertEqual((weak.status, weak.json()['error']), (400, 'weak_password'), 'konto z approverem w pliku: minimum jak dla approvera')
        longer = 'Nowe-' + 'Haslo-' + 'Approvera-' + '2026'
        done = env.page_req('POST', '/_api/v1/password', body={'current': PW_APPROVER, 'new': longer}, headers=auth)
        self.assertEqual(done.status, 204, done.body)
        audit = (Path(env.run_dir) / 'audit.jsonl').read_text()
        self.assertIn('password_changed', audit, 'audyt liczy role z pliku, nie z zawężonego widoku sesji')


class ApproverAuditTests(Base):
    """Kontrakt U-1c (W5): logowanie approvera jest fail-closed na brak audytu (503 `audit_unavailable`)."""

    def test_approver_without_audit_directory_gets_503(self):
        env = self.env(runtime=False)
        refused = env.page_req('POST', '/_api/v1/login', body={'login': 'ap@example.com', 'password': PW_APPROVER})
        self.assertEqual((refused.status, refused.json()['error']), (503, 'audit_unavailable'))
        self.assertIsNone(refused.cookie())
        self.assertEqual(env.app.sessions.count(), 0)
        again = env.page_req('POST', '/_api/v1/login', body={'login': 'ap@example.com', 'password': PW_APPROVER})
        self.assertEqual(again.status, 503, 'brak cache: kolejna próba powtarza kontrolę')
        commenter = env.page_req('POST', '/_api/v1/login', body={'login': 'com@example.com', 'password': PW_COMMENTER})
        self.assertEqual(commenter.status, 200, 'konto bez approvera loguje się bez audytu')

    def test_approver_with_run_dir_signs_in_and_is_audited(self):
        env = self.env(runtime=True)
        response = env.page_req('POST', '/_api/v1/login', body={'login': 'ap@example.com', 'password': PW_APPROVER})
        self.assertEqual(response.status, 200)
        audit = (Path(env.run_dir) / 'audit.jsonl').read_text()
        self.assertIn('"event": "login"', audit)
        self.assertNotIn(PW_APPROVER, audit)

    def test_audit_is_enabled_lazily_when_users_appeared_after_start(self):
        # serwer wystartował bez users.json (enable_login_link nie zadziałał), konto powstało potem
        env = self.env(runtime=True, enable_link=False)
        self.assertIsNone(env.app.audit_dir)
        self.assertFalse(os.path.exists(env.run_dir))
        response = env.page_req('POST', '/_api/v1/login', body={'login': 'ap@example.com', 'password': PW_APPROVER})
        self.assertEqual(response.status, 200, response.body)
        self.assertEqual(env.app.audit_dir, env.run_dir)
        self.assertIn('"event": "login"', (Path(env.run_dir) / 'audit.jsonl').read_text())
        if os.name == 'posix':
            self.assertEqual(os.stat(env.run_dir).st_mode & 0o077, 0, 'katalog audytu prywatny')


class ChallengeContractTests(Base):
    """Kontrakt U-1d (N4): kod tylko dla żywego challenge, dokładnie jeden na challenge, challenge zużyty przy wydaniu."""

    def test_unknown_challenge_gets_no_code(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        response = env.issue(who, FAKE_CHALLENGE)
        self.assertEqual((response.status, response.json()['error']), (400, 'invalid_request'))
        self.assertNotIn('code', response.json())
        self.assertEqual(env.app.handoffs.count(), 0)

    def test_one_code_per_challenge_and_challenge_is_spent_by_the_issue(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        pair, challenge, first = env.flow(who)
        self.assertEqual(first.status, 200)
        second = env.issue(who, challenge)
        self.assertEqual(second.status, 400, 'drugi kod na ten sam challenge')
        self.assertEqual(env.app.handoffs.count(), 1)
        self.assertEqual(env.exchange(first.json()['code'], pair).status, 303, 'pierwszy kod nadal działa (wymiana zużywa nonce)')

    def test_expired_challenge_gets_no_code(self):
        env = self.env()
        who = env.login('com@example.com', PW_COMMENTER)
        _r, _pair, challenge = env.begin()
        env.clock.advance(121)
        self.assertEqual(env.issue(who, challenge).status, 400)

    def test_another_account_cannot_reuse_a_spent_challenge(self):
        env = self.env()
        first = env.login('com@example.com', PW_COMMENTER)
        second = env.login('two@example.com', PW_OTHER)
        _pair, challenge, issued = env.flow(first)
        self.assertEqual(issued.status, 200)
        self.assertEqual(env.issue(second, challenge).status, 400)


class HardeningTests(Base):
    """Kontrakt U-1d: N6 (równoległy audyt), N7/m6 (dummy_hash), N2 (rola pod blokadą), m3 (log bez query), m5 (świeża sesja z parent)."""

    def test_concurrent_approver_logins_never_get_a_false_503(self):
        env = self.env(runtime=True, enable_link=False)
        self.assertIsNone(env.app.audit_dir)
        barrier = threading.Barrier(8)
        problems = []

        def worker():
            barrier.wait()
            problems.append(env.app.ensure_audit())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(problems, [None] * 8)
        self.assertEqual(env.app.audit_dir, env.run_dir)
        statuses = []
        env2 = self.env(runtime=True, enable_link=False)
        barrier2 = threading.Barrier(6)

        def login():
            barrier2.wait()
            statuses.append(env2.page_req('POST', '/_api/v1/login', body={'login': 'ap@example.com', 'password': PW_APPROVER}).status)

        threads = [threading.Thread(target=login) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(statuses, [200] * 6, statuses)

    def test_dummy_hash_follows_the_iterations_of_a_users_file_that_appears_later(self):
        # m6: plik pojawia się po starcie z innymi iteracjami niż konfiguracja; hash atrapy ma koszt zapisanych haseł
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'users.json')
            users = ps.UsersFile(path, iterations=1000)
            before = users.dummy_hash()
            self.assertEqual(before.split('$')[1], '1000', 'bez pliku: iteracje z konfiguracji')
            writer = ps.UsersFile(path, iterations=2345)
            ps.add_user(writer, 'later@example.com', role='commenter', password=PW_COMMENTER)
            after = users.dummy_hash()
            self.assertEqual(after.split('$')[1], '2345', 'iteracje zapisanych hashy, nie konfiguracji')
            self.assertEqual(users.dummy_hash(), after, 'odświeżony raz')

    def test_role_is_decided_under_the_lock_not_from_the_first_read(self):
        # N2: rola approver nadana między odczytami; decyduje rekord pod blokadą
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'users.json')
            users = ps.UsersFile(path, iterations=1000)
            ps.add_user(users, 'later.approver@example.com', roles=['approver'], password='x' * 20, via_prompt=True)
            stale = dict(users.get('later.approver@example.com'))
            stale['roles'] = ['commenter']
            stale['role'] = 'commenter'
            before = users.get('later.approver@example.com')['password']
            with mock.patch.object(users, 'get', return_value=stale):
                with self.assertRaises(ps.UserError) as caught:
                    ps.set_password(users, 'later.approver@example.com', password='z' * 20)
                self.assertEqual(caught.exception.code, 'approver_password_prompt_required')
                with self.assertRaises(ps.UserError) as caught:
                    ps.set_password(users, 'later.approver@example.com', password='z' * 10, via_prompt=True)
                self.assertEqual(caught.exception.code, 'weak_password', 'minimum 14 liczone z roli pod blokadą')
            self.assertEqual(users.get('later.approver@example.com')['password'], before, 'hasło bez zmian')
            ps.set_password(users, 'later.approver@example.com', password='z' * 20, via_prompt=True)   # kontrola: z promptem przechodzi

    def test_standalone_passwd_stdin_on_an_approver_is_blocked(self):
        # N1: `prototype_server.py user passwd --password-stdin` na approverze = BLOCKED, exit 2, hasło bez zmian
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'users.json')
            users = ps.UsersFile(path, iterations=1000)
            ps.add_user(users, 'ap.cli@example.com', roles=['approver'], password='x' * 20, via_prompt=True)
            ps.add_user(users, 'com.cli@example.com', role='commenter', password='y' * 12)
            before = users.get('ap.cli@example.com')['password']
            script = str(ROOT / 'skillsync_workflow' / 'prototype_server.py')

            def run(login):
                return subprocess.run([sys.executable, script, 'user', 'passwd', login, '--users', path, '--password-stdin'],
                                      input='n' * 20 + '\n', capture_output=True, text=True, timeout=60)
            blocked = run('ap.cli@example.com')
            self.assertEqual(blocked.returncode, 2, blocked.stdout + blocked.stderr)
            report = json.loads(blocked.stdout)
            self.assertEqual((report['status'], report['error']), ('BLOCKED', 'approver_password_prompt_required'))
            self.assertEqual(users.get('ap.cli@example.com')['password'], before)
            ok = run('com.cli@example.com')
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)   # kontrola: komentujący może przez stdin

    def test_request_log_never_holds_the_query(self):
        # m3: Handler.log_message przy verbose bez query (challenge, token linku)
        env = self.env(verbose=True)
        _r, _pair, challenge = env.begin()
        lines = []
        with mock.patch.object(dh, 'log', lambda text: lines.append(text)):
            response = env.page_req('GET', '/_handoff?challenge=%s' % challenge)
        self.assertEqual(response.status, 200)
        deadline = 0
        while not lines and deadline < 50:
            threading.Event().wait(0.05)
            deadline += 1
        text = '\n'.join(lines)
        self.assertIn('/_handoff', text, 'kontrola: żądanie jest w logu')
        self.assertNotIn(challenge, text)
        self.assertNotIn('challenge=', text)

    def test_password_change_keeps_the_parent_of_the_fresh_session(self):
        # m5: bezpośrednio, sesja przekazana na originie strony (kind page) zmienia hasło: świeża sesja ma tego samego rodzica
        # (przez HTTP bezużyteczne: zmiana hasła kończy też rodzica, więc świeża sesja zaraz wygasa)
        env = self.env()
        parent = env.login('com@example.com', PW_COMMENTER)
        parent_session, _user = env.app.sessions.lookup(parent.token(), env.app.users, 'page')
        user = env.app.users.get('com@example.com')
        child = env.app.sessions.create('com@example.com', user['password'], via='handoff', kind='page', parent=parent_session)
        cookie = '%s=%s' % (env.app.cookie_name_for(False, 'page'), child.token)
        csrf = env.page_req('GET', '/_api/v1/session', headers={'Cookie': cookie}).json()['csrf']
        done = env.page_req('POST', '/_api/v1/password', body={'current': PW_COMMENTER, 'new': PW_NEW}, headers={'Cookie': cookie, 'X-Skillsync-CSRF': csrf})
        self.assertEqual(done.status, 204)
        fresh_token = done.cookie().split(';')[0].split('=', 1)[1]
        fresh = env.app.sessions._items.get(fresh_token)
        self.assertIsNotNone(fresh)
        self.assertEqual((fresh.via, fresh.kind), ('handoff', 'page'))
        self.assertIs(fresh.parent, parent_session)


# ── dowód skuteczności: mutant przez monkeypatch, kluczowa asercja musi paść ─────────────────────────────

def _mutant_redeem_keeps_code(self, code, nonce, proto_host):
    """Mutant: kod nie jest zużywany."""
    if not isinstance(code, str) or not ps.HANDOFF_CODE_RE.fullmatch(code):
        return None
    entry = self._items.get(self._digest(code))
    return entry if entry is not None and entry['expires'] > self.clock() else None


def _mutant_redeem_ignores_nonce(self, code, nonce, proto_host):
    """Mutant: wymiana bez sprawdzenia cookie nonce (link napastnika wystarcza)."""
    if not isinstance(code, str) or not ps.HANDOFF_CODE_RE.fullmatch(code):
        return None
    with self._lock:
        entry = self._items.pop(self._digest(code), None)
    return entry if entry is not None and entry['expires'] > self.clock() else None


def _mutant_redeem_ignores_challenge(self, code, nonce, proto_host):
    """Mutant: dowolny ważny nonce wystarcza (kod nie jest związany z challenge)."""
    if not isinstance(code, str) or not ps.HANDOFF_CODE_RE.fullmatch(code) or not isinstance(nonce, str) or not nonce:
        return None
    with self._lock:
        entry = self._items.pop(self._digest(code), None)
        expiry = self._nonces.pop(self._digest(nonce), None)
    if entry is None or expiry is None or entry['expires'] <= self.clock() or expiry <= self.clock():
        return None
    return entry


def _mutant_redeem_keeps_nonce(self, code, nonce, proto_host):
    """Mutant: nonce nie jest zużywany."""
    if not isinstance(code, str) or not ps.HANDOFF_CODE_RE.fullmatch(code) or not isinstance(nonce, str) or not nonce:
        return None
    with self._lock:
        entry = self._items.pop(self._digest(code), None)
        expiry = self._nonces.get(self._digest(nonce))
    if entry is None or expiry is None or entry['expires'] <= self.clock() or expiry <= self.clock():
        return None
    return entry if entry['challenge'] == self._digest(nonce) else None


def _mutant_redeem_ignores_ttl(self, code, nonce, proto_host):
    """Mutant: ważność kodu nie jest sprawdzana."""
    if not isinstance(code, str) or not ps.HANDOFF_CODE_RE.fullmatch(code) or not isinstance(nonce, str) or not nonce:
        return None
    with self._lock:
        entry = self._items.pop(self._digest(code), None)
        expiry = self._nonces.pop(self._digest(nonce), None)
    if entry is None or expiry is None or expiry <= self.clock() or entry['challenge'] != self._digest(nonce):
        return None
    return entry


def _mutant_redeem_ignores_nonce_ttl(self, code, nonce, proto_host):
    """Mutant: ważność nonce nie jest sprawdzana."""
    if not isinstance(code, str) or not ps.HANDOFF_CODE_RE.fullmatch(code) or not isinstance(nonce, str) or not nonce:
        return None
    with self._lock:
        entry = self._items.pop(self._digest(code), None)
        expiry = self._nonces.pop(self._digest(nonce), None)
    if entry is None or expiry is None or entry['expires'] <= self.clock() or entry['challenge'] != self._digest(nonce):
        return None
    return entry


def _mutant_redeem_not_atomic(self, code, nonce, proto_host):
    """Mutant: odczyt i usunięcie rozdzielone (okno wyścigu)."""
    import time
    if not isinstance(code, str) or not ps.HANDOFF_CODE_RE.fullmatch(code) or not isinstance(nonce, str) or not nonce:
        return None
    key = self._digest(code)
    entry = self._items.get(key)
    expiry = self._nonces.get(self._digest(nonce))
    time.sleep(0.2)
    self._items.pop(key, None)
    self._nonces.pop(self._digest(nonce), None)
    if entry is None or expiry is None or entry['expires'] <= self.clock() or entry['challenge'] != self._digest(nonce):
        return None
    return entry


_ORIGINAL_LOOKUP = ps.SessionStore.lookup
_ORIGINAL_BEGIN = ps.HandoffStore.begin
_ORIGINAL_CREATE = ps.SessionStore.create
_ORIGINAL_MAX_AGE = ps.SessionStore.max_age
_ORIGINAL_EFFECTIVE = ps.PrototypeApp.effective_roles
_ORIGINAL_RESTRICT = ps.PrototypeApp.restrict_user


def _mutant_lookup_any_kind(self, token, users_file, kind=None):
    """Mutant: sesja nie zna originu, więc token jednego originu działa pod nazwą ciasteczka drugiego."""
    return _ORIGINAL_LOOKUP(self, token, users_file, None)


def _mutant_lookup_parent_presence_only(self, token, users_file, kind=None):
    """Mutant (stary błąd W4): potomek żyje, dopóki rodzic jest w magazynie, choćby wygasł."""
    with self._lock:
        session = self._items.get(token)
        parent = session.parent if session is not None else None
        if parent is not None and self._items.get(parent.token) is parent:
            parent.last_seen = self.clock()
            parent.created = self.clock()
    return _ORIGINAL_LOOKUP(self, token, users_file, kind)


def _mutant_max_age_ignores_parent(self, session):
    return int(self.approver_maximum if session.limited else self.maximum)


def _mutant_effective_roles_link_keeps_privileges(self, user, session=None):
    """Mutant (stary kontrakt): sesja linku traci tylko approver, zachowuje acceptor i developer."""
    roles = list(ps.roles_of(user))
    if session is not None and session.via == 'link':
        roles = [role for role in roles if role != ps.APPROVER] or ['commenter']
    elif session is not None and session.via == 'handoff':
        roles = [role for role in roles if role not in ps.HANDOFF_DENIED_ROLES] or ['commenter']
    return roles


def _mutant_begin_without_limiter(self, source='-'):
    """Mutant (N3): brak limitera begin i limitu nonce na źródło (każdy begin jako nowe źródło)."""
    return _ORIGINAL_BEGIN(self, 'src-%s' % secrets.token_hex(4))


def _mutant_issue_without_live_challenge(self, session, challenge, page_host, proto_host):
    """Mutant (N4): kod dla dowolnego challenge, bez zużywania go."""
    now = self.clock()
    code = secrets.token_urlsafe(32)
    entry = {'login': session.login, 'password_ref': session.password_ref, 'session': session, 'challenge': challenge,
             'page_host': page_host, 'proto_host': proto_host, 'expires': now + self.ttl}
    with self._lock:
        if len(self._items) >= self.maximum or sum(1 for item in self._items.values() if item['login'] == session.login) >= self.per_login:
            return None
        self._items[self._digest(code)] = entry
    return code


def _mutant_dummy_hash_from_config(self):
    if self._dummy is None:
        self._dummy = ps.hash_password('mutant', self.iterations)
    return self._dummy


def _mutant_ensure_audit_always_ok(self):
    return None


def _mutant_ensure_audit_never_enables(self):
    return 'mutant'


def _mutant_create_without_parent(self, login, password_ref, via=None, limited=False, kind=None, parent=None):
    """Mutant: sesja przekazana nie zna rodzica (nie kończy się z nim)."""
    return _ORIGINAL_CREATE(self, login, password_ref, via, limited, kind, None)


def _mutant_server_handler_on_read(self, route):
    """Mutant: trasy sesji także na gnieździe tylko-do-odczytu."""
    if route not in dh.SESSION_ROUTES or self.app is None:
        return False
    try:
        return self._session_route(self.command, route, dh.urlparse(self.path).query)
    except ps.PrototypeServerError as error:
        self._pa_error(error)
        return True


class MutationControlTests(unittest.TestCase):
    """Każdy test: kontrola (test bez mutanta przechodzi) i mutant (ten sam test pada)."""

    def _run(self, case, name):
        suite = unittest.TestSuite([case(name)])
        return unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)

    def _check(self, case, name, patcher):
        clean = self._run(case, name)
        self.assertTrue(clean.wasSuccessful(), 'kontrola bez mutanta: %s %s' % (name, clean.failures + clean.errors))
        self.assertEqual(clean.testsRun, 1)
        with patcher:
            mutated = self._run(case, name)
        self.assertEqual(mutated.testsRun, 1)
        self.assertGreater(len(mutated.failures) + len(mutated.errors), 0, 'mutant przeżył: %s' % name)

    def test_mutant_code_not_consumed(self):
        self._check(HandoffExchangeTests, 'test_code_is_single_use', mock.patch.object(ps.HandoffStore, 'redeem', _mutant_redeem_keeps_code))

    def test_mutant_nonce_cookie_not_checked(self):
        self._check(HandoffExchangeTests, 'test_attacker_link_without_nonce_cookie_is_refused',
                    mock.patch.object(ps.HandoffStore, 'redeem', _mutant_redeem_ignores_nonce))

    def test_mutant_code_not_bound_to_challenge(self):
        self._check(HandoffExchangeTests, 'test_other_browsers_nonce_is_refused_and_spent',
                    mock.patch.object(ps.HandoffStore, 'redeem', _mutant_redeem_ignores_challenge))

    def test_mutant_nonce_not_spent(self):
        self._check(HandoffExchangeTests, 'test_nonce_is_spent_in_every_outcome',
                    mock.patch.object(ps.HandoffStore, 'redeem', _mutant_redeem_keeps_nonce))

    def test_mutant_ttl_ignored(self):
        self._check(HandoffExchangeTests, 'test_code_expires_after_60_seconds', mock.patch.object(ps.HandoffStore, 'redeem', _mutant_redeem_ignores_ttl))

    def test_mutant_nonce_ttl_ignored(self):
        self._check(HandoffBeginTests, 'test_nonce_expires_after_120_seconds',
                    mock.patch.object(ps.HandoffStore, 'redeem', _mutant_redeem_ignores_nonce_ttl))

    def test_mutant_take_not_atomic(self):
        self._check(HandoffExchangeTests, 'test_code_race_exactly_one_wins', mock.patch.object(ps.HandoffStore, 'redeem', _mutant_redeem_not_atomic))

    def test_mutant_exchange_origin_not_checked(self):
        self._check(HandoffExchangeTests, 'test_exchange_needs_same_origin_post',
                    mock.patch.object(ps.PrototypeRequestHandler, '_check_origin', lambda self: None))

    def test_mutant_privileged_roles_kept(self):
        self._check(HandedSessionTests, 'test_handed_session_has_no_privileged_roles', mock.patch.object(ps, 'HANDOFF_DENIED_ROLES', ()))

    def test_mutant_link_session_keeps_acceptor_and_developer(self):
        self._check(HandedSessionTests, 'test_link_and_handoff_sessions_carry_narrowed_roles_in_routes',
                    mock.patch.object(ps.PrototypeApp, 'effective_roles', _mutant_effective_roles_link_keeps_privileges))

    def test_mutant_restrict_user_skips_link(self):
        self._check(LinkRolesTests, 'test_link_session_is_narrowed_in_routes',
                    mock.patch.object(ps.PrototypeApp, 'restrict_user', lambda self, session, user: user if session is None or session.via == 'link'
                                      else _ORIGINAL_RESTRICT(self, session, user)))

    def test_mutant_cookie_not_origin_specific(self):
        self._check(HandedSessionTests, 'test_cookie_is_origin_specific_both_ways', mock.patch.object(ps.SessionStore, 'lookup', _mutant_lookup_any_kind))

    def test_mutant_session_routes_on_read_socket(self):
        self._check(PageRoutesTests, 'test_read_socket_has_no_login_routes', mock.patch.object(dh.Handler, '_serve_session', _mutant_server_handler_on_read))

    def test_mutant_handed_session_without_parent(self):
        self._check(HandedSessionTests, 'test_page_logout_ends_handed_sessions', mock.patch.object(ps.SessionStore, 'create', _mutant_create_without_parent))

    def test_mutant_parent_expiry_not_checked(self):
        self._check(HandedSessionTests, 'test_parent_expiry_ends_handed_session',
                    mock.patch.object(ps.SessionStore, 'lookup', _mutant_lookup_parent_presence_only))

    def test_mutant_child_max_age_ignores_parent(self):
        self._check(HandedSessionTests, 'test_child_cookie_max_age_never_exceeds_the_parent',
                    mock.patch.object(ps.SessionStore, 'max_age', _mutant_max_age_ignores_parent))

    def test_mutant_begin_not_limited(self):
        self._check(HandoffBeginTests, 'test_begin_is_limited_per_source', mock.patch.object(ps.HandoffStore, 'begin', _mutant_begin_without_limiter))

    def test_mutant_nonces_per_source_not_bounded(self):
        self._check(HandoffBeginTests, 'test_nonces_per_source_drop_the_oldest_of_that_source',
                    mock.patch.object(ps.HandoffStore, 'begin', _mutant_begin_without_limiter))

    def test_mutant_issue_without_live_challenge(self):
        self._check(ChallengeContractTests, 'test_unknown_challenge_gets_no_code',
                    mock.patch.object(ps.HandoffStore, 'issue', _mutant_issue_without_live_challenge))

    def test_mutant_second_code_per_challenge(self):
        self._check(ChallengeContractTests, 'test_one_code_per_challenge_and_challenge_is_spent_by_the_issue',
                    mock.patch.object(ps.HandoffStore, 'issue', _mutant_issue_without_live_challenge))

    def test_mutant_dummy_hash_uses_config_iterations(self):
        self._check(HardeningTests, 'test_dummy_hash_follows_the_iterations_of_a_users_file_that_appears_later',
                    mock.patch.object(ps.UsersFile, 'dummy_hash', _mutant_dummy_hash_from_config))

    def test_mutant_audit_not_fail_closed(self):
        self._check(ApproverAuditTests, 'test_approver_without_audit_directory_gets_503',
                    mock.patch.object(ps.PrototypeApp, 'ensure_audit', _mutant_ensure_audit_always_ok))

    def test_mutant_audit_never_enabled_lazily(self):
        self._check(ApproverAuditTests, 'test_audit_is_enabled_lazily_when_users_appeared_after_start',
                    mock.patch.object(ps.PrototypeApp, 'ensure_audit', _mutant_ensure_audit_never_enables))


if __name__ == '__main__':
    unittest.main()
