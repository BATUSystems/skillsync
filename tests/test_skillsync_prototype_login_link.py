"""Jednorazowe logowanie do serwera prototypów: `skillsync prototype login-link`, `GET /_login/link`, plik stanu serwera.

Kontrakt: spec 2026-11-11-status-przebiegu-narzedzia-prototypy.md (K22-K24, K26, K27, sekcja „Serwer prototypów
i login-link”) oraz pre-implement-r2.md (nazwy: `ServerConfig(runtime_dir=...)`, `<repo>/.skillsync/prototype/run/`,
`server-<port>.json`, `login/<sha256(token)>.json`). Testy opisują zachowanie z kontraktu; format `expires` (liczba
epoki albo ISO-8601) i kod BLOCKED przy limicie oczekujących tokenów nie są w kontrakcie przesądzone, więc testy
tolerują oba formaty `expires` i sprawdzają przy limicie tylko BLOCKED/exit 2.

Świat testowy: projekt (`PrototypeFixture`), serwer w wątku testu na 127.0.0.1:0 (`runtime_dir` = run projektu;
CLI w podprocesie widzi go przez plik stanu), tokeny wydaje prawdziwe CLI. Peera spoza loopbacku symuluje
`socketpair` + `client_address` przekazany handlerowi. Atrapy tokenów składane w czasie wykonania.
Decyzja o odmowie przez proxy/niepetlowego peera: token NIE jest zużywany (kontrola loopbacku i proxy poprzedza
atomowe zużycie, K23); test pokazuje to poprawnym użyciem tego samego tokenu po odmowie.
"""
import contextlib
import hashlib
import http.client
import io
import json
import os
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype_server as ps  # noqa: E402
from tests.test_skillsync_prototype import CLI, PrototypeFixture  # noqa: E402
from tests.test_skillsync_prototype_server import setUpModule, tearDownModule  # noqa: E402,F401  (strażnik katalogu tymczasowego)
from tests.test_skillsync_prototype_server import Client, Resp, create_payload, write  # noqa: E402
from tests.test_skillsync_prototype_server import R_S2_V2, R_S3_V2, r_page, uid  # noqa: E402

POSIX = os.name != 'nt'
needs_posix = unittest.skipUnless(POSIX, 'tryby plików i sygnały tylko na POSIX')
needs_symlinks = unittest.skipUnless(hasattr(os, 'symlink') and getattr(os, 'O_NOFOLLOW', 0),
                                     'dowiązania i O_NOFOLLOW niedostępne (np. Windows)')
ITER = 1000
TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{43}$')
JAN = 'jan.test@example.com'          # komentujący
ANNA = 'anna.test@example.com'        # akceptujący
MUST = 'marek.test@example.com'       # mustChange
PASSWORDS = {JAN: 'Haslo-Jana-4321', ANNA: 'Haslo-Anny-4321'}
DEFAULT_PW = 'Domyslne-Haslo-4321'
TTL_DEFAULT = 60


def fake_token():
    """43 znaki alfabetu base64url, złożone w czasie wykonania (nie literał wyglądający jak token)."""
    return secrets.token_urlsafe(32)


def sha256_hex(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def epoch_of(value):
    """`expires` jako liczba epoki albo ISO-8601."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()


def assert_no_secret(case, text, secrets_list, label='log'):
    for secret in secrets_list:
        case.assertNotIn(secret, text, '%s zawiera sekret' % label)


def signature(response):
    """Odcisk odpowiedzi do porównań „identyczna dla każdej przyczyny” (bez nagłówka Date)."""
    headers = sorted((key.lower(), value) for key, value in response.headers.items() if key.lower() != 'date')
    return response.status, tuple(headers), response.body


def raw_exchange(server, request, peer):
    """Przepuszcza surowe żądanie przez handler serwera z podanym adresem peera (symulacja klienta spoza loopbacku)."""
    import socket as _socket
    client_sock, server_sock = _socket.socketpair()
    try:
        client_sock.sendall(request)
        client_sock.shutdown(_socket.SHUT_WR)
        ps.PrototypeRequestHandler(server_sock, peer, server)
        server_sock.close()
        chunks = []
        while True:
            chunk = client_sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        client_sock.close()
        with contextlib.suppress(OSError):
            server_sock.close()
    data = b''.join(chunks)
    head, _, body = data.partition(b'\r\n\r\n')
    lines = head.split(b'\r\n')
    status = int(lines[0].split()[1])
    headers = http.client.parse_headers(io.BytesIO(b'\r\n'.join(lines[1:]) + b'\r\n\r\n'))
    return Resp(status, headers, body)


class LinkCase(PrototypeFixture):
    """Projekt z prototypem `demo`, kontami i serwerem z `runtime_dir`."""
    login_to_view = False
    runtime_mode = 0o700

    def setUp(self):
        super().setUp()
        self.protos.mkdir(parents=True, exist_ok=True)
        write(self.protos / 'demo' / 'prototype.json', '{"title": "Demo"}\n')
        write(self.protos / 'demo' / 'index.html',
              '<!doctype html><html data-prototype-id="demo"><body>DEMO-INDEX</body></html>\n')
        write(self.protos / 'demo' / 'prototype.js', '/* engine */\n')
        self.users_path = self.root / '.skillsync' / 'prototype' / 'users.json'
        self.users_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(str(self.users_path.parent), 0o700)
        self.users = ps.UsersFile(str(self.users_path), iterations=ITER)
        ps.add_user(self.users, JAN, role='commenter', password=PASSWORDS[JAN])
        ps.add_user(self.users, ANNA, role='acceptor', password=PASSWORDS[ANNA])
        ps.add_user(self.users, MUST, role='commenter', default_password=DEFAULT_PW)
        self.run_dir = self.root / '.skillsync' / 'prototype' / 'run'
        self.time_offset = 0.0
        self._servers = []
        self.addCleanup(self._stop_servers)

    # -- serwer ---------------------------------------------------------------
    def prepare_run_dir(self):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(str(self.run_dir), self.runtime_mode)

    def clock(self):
        return time.time() + self.time_offset

    def config(self, **overrides):
        values = dict(host='127.0.0.1', port=0, pbkdf2_iterations=ITER, log=None, git_checks=False,
                      login_to_view=self.login_to_view, source_writes=False, clock=self.clock,
                      runtime_dir=str(self.run_dir))
        values.update(overrides)
        return ps.ServerConfig(str(self.protos), str(self.users_path), **values)

    def start_server(self, prepare=True, **overrides):
        if prepare:
            self.prepare_run_dir()
        server = ps.make_server(self.config(**overrides))
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread.start()
        self._servers.append((server, thread))
        self.server = server
        self.anon = Client(server)
        return server

    def stop_server(self, server):
        for index, (item, thread) in enumerate(self._servers):
            if item is server:
                item.shutdown()
                item.server_close()
                thread.join(10)
                del self._servers[index]
                return

    def _stop_servers(self):
        for server, thread in reversed(self._servers):
            server.shutdown()
            server.server_close()
            thread.join(10)
        self._servers = []

    def state_path(self, server=None):
        server = server or self.server
        return self.run_dir / ('server-%d.json' % server.port)

    def read_state(self, server=None):
        path = self.state_path(server)
        deadline = time.time() + 5
        while not path.exists() and time.time() < deadline:
            time.sleep(0.05)
        return json.loads(path.read_text(encoding='utf-8'))

    # -- CLI ------------------------------------------------------------------
    def run_link(self, *args, user=JAN):
        argv = [sys.executable, str(CLI), 'prototype', 'login-link', str(self.root)]
        if user is not None:
            argv += ['--user', user]
        argv += [str(item) for item in args] + ['--json']
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60, cwd=str(self.base))
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        return result

    def report_of(self, result):
        try:
            return json.loads(result.stdout)
        except ValueError:
            self.fail('stdout nie jest JSON-em (exit %s): %r %r' % (result.returncode, result.stdout, result.stderr))

    def blocked(self, result, code):
        report = self.report_of(result)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(report.get('schema'), 1, report)
        self.assertEqual(report.get('status'), 'BLOCKED', report)
        self.assertEqual(report.get('code'), code, report)
        self.assertTrue(isinstance(report.get('reason'), str) and report['reason'].strip(), report)
        return report

    def issue(self, *args, user=JAN):
        """Wydaje token prawdziwym CLI; zwraca `(token, raport, wynik)`."""
        result = self.run_link(*args, user=user)
        report = self.report_of(result)
        self.assertEqual((result.returncode, report.get('status')), (0, 'PASS'), result.stdout + result.stderr)
        token = parse_qs(urlsplit(report['url']).query).get('t', [''])[0]
        return token, report, result

    def token_file(self, token):
        return self.run_dir / 'login' / (sha256_hex(token) + '.json')

    # -- HTTP -----------------------------------------------------------------
    def link_get(self, token, headers=None, path=None, query=None, method='GET', client=None):
        client = client or Client(self.server)
        target = path or '/_login/link'
        target += query if query is not None else '?t=%s' % token
        return client.request(method, target, headers=headers)

    def denial(self):
        """Referencyjna odmowa: poprawny format tokenu, którego nikt nie wydał."""
        return self.link_get(fake_token())

    def assert_denied(self, response, reference=None):
        self.assertEqual(response.status, 302, response.text)
        self.assertEqual(response.header('Location'), '/_login')
        self.assertEqual(response.cookies(), [])
        self.assertEqual(signature(response), signature(reference or self.denial()))

    def cookie_pair(self, response):
        cookies = response.cookies()
        self.assertEqual(len(cookies), 1, cookies)
        return cookies[0].split(';', 1)[0]

    def session_of(self, pair):
        client = Client(self.server)
        client.cookie = pair
        return client, client.get('/_api/v1/session').json


# ═════════════════════════════════════════════════════════════════════════════
# CLI: BLOCKED (K22)
# ═════════════════════════════════════════════════════════════════════════════

class LoginLinkBlockedTests(LinkCase):
    def test_no_server_running_is_blocked_server_not_running(self):
        # K22: brak pliku stanu = serwer niedziałający; żadnego tokenu nie tworzy
        result = self.run_link()
        self.blocked(result, 'server_not_running')
        self.assertFalse((self.run_dir / 'login').exists() and any((self.run_dir / 'login').iterdir()))

    @needs_posix  # na Windows `link_pid_alive` zawsze zwraca True (os.kill zabijałby proces): martwy PID nie jest tam wykrywany
    def test_stale_state_file_with_a_dead_pid_is_not_running_never_own(self):
        # K27: po SIGKILL zostaje plik stanu; martwy PID = „zatrzymany”, nawet gdy ktoś słucha na porcie
        server = self.start_server()
        state = self.read_state()
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        child.wait()
        state['pid'] = child.pid
        self.state_path().write_text(json.dumps(state), encoding='utf-8')
        os.chmod(str(self.state_path()), 0o600)
        self.blocked(self.run_link(), 'server_not_running')
        self.assertTrue(server.port)

    def test_state_file_with_a_mismatched_instance_is_not_running(self):
        # K27: żywy PID, ale `instance` z pliku różni się od tego, który zwraca serwer pod adresem
        self.start_server()
        state = self.read_state()
        state['instance'] = fake_token()
        self.state_path().write_text(json.dumps(state), encoding='utf-8')
        os.chmod(str(self.state_path()), 0o600)
        self.blocked(self.run_link(), 'server_not_running')

    def test_control_matching_state_file_issues_a_link(self):
        self.start_server()
        self.read_state()
        self.issue()

    def test_unknown_user_is_blocked_and_no_account_is_created(self):
        self.start_server()
        before = self.users_path.read_bytes()
        self.blocked(self.run_link(user='nikt.nieznany@example.com'), 'unknown_user')
        self.assertEqual(self.users_path.read_bytes(), before, 'K22: nigdy po cichu utworzone konto')

    def test_user_with_must_change_is_blocked_password_change_required(self):
        # K26/Q3: token nie omija zmiany hasła
        self.start_server()
        self.blocked(self.run_link(user=MUST), 'password_change_required')

    def test_login_is_normalised_to_lower_case(self):
        self.start_server()
        token, _, _ = self.issue(user='JAN.Test@Example.com')
        self.assertRegex(token, TOKEN_RE)

    def test_paths_outside_the_allowed_set_are_blocked_invalid_path(self):
        self.start_server()
        for path in ('/_api/v1/session', '/_api/x', '//evil.example', 'http://evil.example/', '/\\evil.example', 'demo/'):
            with self.subTest(path=path):
                self.blocked(self.run_link('--path', path), 'invalid_path')
        login_dir = self.run_dir / 'login'
        self.assertFalse(login_dir.exists() and any(login_dir.iterdir()), 'odmowa nie zostawia tokenu')
        token, _, _ = self.issue('--path', '/demo/')  # kontrola: poprawna ścieżka przechodzi
        self.assertRegex(token, TOKEN_RE)

    def test_several_servers_of_the_project_need_the_port(self):
        first = self.start_server()
        second = self.start_server(prepare=False)
        self.read_state(first)
        self.read_state(second)
        self.blocked(self.run_link(), 'ambiguous_server')
        _, report, _ = self.issue('--port', second.port)  # kontrola: z --port działa
        self.assertTrue(report['url'].startswith('http://127.0.0.1:%d/_login/link?t=' % second.port), report)

    def test_lan_host_server_is_blocked_link_needs_loopback(self):
        # SYMULACJA: serwer słucha na 127.0.0.1 (bind na prawdziwym adresie LAN nie jest przenośny w CI); host zapisany
        # w pliku stanu to adres LAN (nie loopback, nie wildcard), a CLI decyduje wyłącznie po tym polu.
        self.start_server()
        state = self.read_state()
        state['host'] = '192.168.1.5'
        self.state_path().write_text(json.dumps(state), encoding='utf-8')
        os.chmod(str(self.state_path()), 0o600)
        self.blocked(self.run_link(), 'link_needs_loopback')

    def test_wildcard_host_server_gives_a_loopback_url_never_0_0_0_0(self):
        server = self.start_server(host='0.0.0.0')
        self.read_state()
        _, report, result = self.issue()
        self.assertTrue(report['url'].startswith('http://127.0.0.1:%d/_login/link?t=' % server.port), report)
        self.assertNotIn('0.0.0.0', result.stdout)


# ═════════════════════════════════════════════════════════════════════════════
# CLI: sukces i tokeny na dysku (K22, K24)
# ═════════════════════════════════════════════════════════════════════════════

class LoginLinkIssueTests(LinkCase):
    def setUp(self):
        super().setUp()
        self.start_server()
        self.read_state()

    def test_success_report_url_token_and_expiry(self):
        before = time.time()
        token, report, result = self.issue('--path', '/demo/')
        self.assertEqual((report['schema'], report['status']), (1, 'PASS'))
        self.assertTrue(report['url'].startswith('http://127.0.0.1:%d/_login/link?t=' % self.server.port), report['url'])
        self.assertNotIn('0.0.0.0', report['url'])
        self.assertRegex(token, TOKEN_RE)
        self.assertEqual(urlsplit(report['url']).query, 't=' + token)
        self.assertNotIn('/demo', report['url'], 'ścieżka docelowa zostaje po stronie serwera, nie w URL')
        expires = epoch_of(report['expires'])
        self.assertGreater(expires, before)
        self.assertLessEqual(expires, time.time() + TTL_DEFAULT + 5)

    def test_token_is_only_on_stdout_never_on_stderr(self):
        token, _, result = self.issue()
        self.assertNotIn(token, result.stderr)
        self.assertNotIn(sha256_hex(token), result.stderr)
        self.assertIn(token, result.stdout)

    def test_token_file_is_named_by_the_hash_private_and_holds_no_token(self):
        token, _, _ = self.issue('--path', '/demo/')
        path = self.token_file(token)
        self.assertTrue(path.is_file(), sorted(os.listdir(str(self.run_dir / 'login'))))
        self.assertEqual([item.name for item in (self.run_dir / 'login').iterdir()], [path.name])
        raw = path.read_text(encoding='utf-8')
        self.assertNotIn(token, raw)
        data = json.loads(raw)
        self.assertEqual(set(data), {'login', 'path', 'expires', 'instance'})  # `instance` binds the token to one server
        self.assertEqual(data['instance'], self.read_state()['instance'])
        self.assertEqual((data['login'], data['path']), (JAN, '/demo/'))
        self.assertLessEqual(epoch_of(data['expires']), time.time() + TTL_DEFAULT + 5)
        if POSIX:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.run_dir.stat().st_mode), 0o700)

    def test_every_call_issues_a_different_token(self):
        first, _, _ = self.issue()
        second, _, _ = self.issue()
        self.assertNotEqual(first, second)
        self.assertEqual(len(list((self.run_dir / 'login').iterdir())), 2)

    def test_pending_token_limit_blocks_the_next_one(self):
        # K24: limit liczby oczekujących tokenów (np. 32); kod BLOCKED nie jest przesądzony
        token, _, _ = self.issue()
        content = self.token_file(token).read_text(encoding='utf-8')
        login_dir = self.run_dir / 'login'
        for _ in range(40):
            clone = login_dir / (sha256_hex(fake_token()) + '.json')
            clone.write_text(content, encoding='utf-8')
            os.chmod(str(clone), 0o600)
        result = self.run_link()
        report = self.report_of(result)
        self.assertEqual((result.returncode, report.get('status')), (2, 'BLOCKED'), result.stdout)
        self.assertEqual(report.get('schema'), 1)
        self.assertTrue(report.get('reason'))
        self.assertNotIn('url', report)
        self.assertEqual(len(list(login_dir.iterdir())), 41, 'odmowa nie dokłada tokenu')


# ═════════════════════════════════════════════════════════════════════════════
# Serwer: wymiana tokenu (K23)
# ═════════════════════════════════════════════════════════════════════════════

class LinkExchangeTests(LinkCase):
    def setUp(self):
        super().setUp()
        self.start_server()
        self.read_state()

    def test_valid_token_redirects_with_a_session_cookie_and_logs_in(self):
        token, _, _ = self.issue('--path', '/demo/')
        response = self.link_get(token)
        self.assertEqual(response.status, 302, response.text)
        self.assertEqual(response.header('Location'), '/demo/')
        self.assertNotIn(token, response.header('Location'))
        cookies = response.cookies()
        self.assertEqual(len(cookies), 1)
        name = 'skillsync_proto_%d' % self.server.port
        self.assertTrue(cookies[0].startswith(name + '='), cookies[0])
        self.assertIn('HttpOnly', cookies[0])
        self.assertIn('SameSite=Lax', cookies[0])
        self.assertIn('Path=/', cookies[0])
        self.assertEqual(response.header('Cache-Control'), 'no-store')
        self.assertEqual(response.header('Referrer-Policy'), 'same-origin')
        client, session = self.session_of(self.cookie_pair(response))
        self.assertEqual(session['user']['login'], JAN)
        self.assertTrue(session['csrf'])

    def test_default_path_is_a_server_relative_path(self):
        token, _, _ = self.issue()
        response = self.link_get(token)
        self.assertEqual(response.status, 302)
        location = response.header('Location')
        self.assertTrue(location.startswith('/') and not location.startswith('//'), location)
        self.assertNotEqual(location, '/_login')

    def test_second_use_of_the_token_is_the_uniform_denial(self):
        token, _, _ = self.issue('--path', '/demo/')
        reference = self.denial()
        self.assertEqual(self.link_get(token).status, 302)
        again = self.link_get(token)
        self.assert_denied(again, reference)
        self.assertNotIn(b'token', again.body.lower())
        self.assertFalse(self.token_file(token).exists())

    def test_expired_token_is_the_uniform_denial(self):
        token, _, _ = self.issue()
        reference = self.denial()
        self.time_offset = 3600  # zegar serwera o godzinę do przodu
        self.assert_denied(self.link_get(token), reference)
        self.time_offset = 0
        self.assert_denied(self.link_get(token), reference)  # zużyty/wygasły nie wraca

    def test_forged_and_malformed_tokens_get_the_same_denial_for_every_reason(self):
        issued, _, _ = self.issue()
        reference = self.denial()
        cases = {
            'losowy poprawny format': '?t=' + fake_token(),
            'za krótki': '?t=' + fake_token()[:20],
            'za długi': '?t=' + fake_token() + 'A',
            'znaki spoza alfabetu': '?t=' + fake_token()[:40] + '+/=',
            'kropki i ukośniki': '?t=../../' + fake_token()[:34],
            'pusty': '?t=',
            'brak parametru': '',
            'inny parametr': '?token=' + fake_token(),
            'dwa parametry': '?t=%s&t=%s' % (fake_token(), fake_token()),
        }
        for label, query in cases.items():
            with self.subTest(label):
                self.assert_denied(self.link_get(None, query=query), reference)
        # kontrola: wydany token nadal działa, czyli odmowy wyżej nie wynikały z uszkodzonego świata
        self.assertEqual(self.link_get(issued).status, 302)

    def test_parallel_exchanges_of_one_token_give_exactly_one_session(self):
        token, _, _ = self.issue('--path', '/demo/')
        count = 6
        barrier = threading.Barrier(count)
        results = []

        def worker():
            client = Client(self.server)
            barrier.wait(10)
            results.append(self.link_get(token, client=client))

        threads = [threading.Thread(target=worker) for _ in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertEqual(len(results), count)
        winners = [item for item in results if item.cookies()]
        self.assertEqual(len(winners), 1, [(item.status, item.cookies()) for item in results])
        self.assertEqual(winners[0].status, 302)
        self.assertEqual(winners[0].header('Location'), '/demo/')
        reference = self.denial()
        for item in results:
            if item is not winners[0]:
                self.assert_denied(item, reference)
        self.assertEqual(self.server.app.sessions.count(), 1)

    def test_account_changed_to_must_change_between_issue_and_exchange_is_denied(self):
        token, _, _ = self.issue()
        reference = self.denial()
        ps.set_password(self.users, JAN, default_password=DEFAULT_PW)
        self.assert_denied(self.link_get(token), reference)

    def test_account_removed_between_issue_and_exchange_is_denied(self):
        token, _, _ = self.issue()
        reference = self.denial()
        ps.remove_user(self.users, JAN)
        self.assert_denied(self.link_get(token), reference)

    def test_forged_token_file_cannot_redirect_to_another_host(self):
        # zapisana ścieżka z pliku tokenu nigdy nie staje się adresem zewnętrznym ani protocol-relative
        for forged in ('//evil.example/x', 'http://evil.example/', '/\\evil.example', '\t//evil.example', '/%2F%2Fevil'):
            with self.subTest(path=forged):
                token, _, _ = self.issue('--path', '/demo/')
                path = self.token_file(token)
                data = json.loads(path.read_text(encoding='utf-8'))
                data['path'] = forged
                path.write_text(json.dumps(data), encoding='utf-8')
                response = self.link_get(token)
                location = response.header('Location') or ''
                self.assertEqual(response.status, 302)
                self.assertTrue(location.startswith('/'), location)
                self.assertFalse(location.startswith('//') or location.startswith('/\\') or '://' in location, location)

    def test_post_to_the_link_endpoint_is_refused_and_keeps_the_token(self):
        token, _, _ = self.issue()
        response = self.link_get(token, method='POST')
        self.assertEqual(response.status, 405)
        self.assertEqual(response.header('Allow'), 'GET')
        self.assertEqual(response.cookies(), [])
        self.assertTrue(self.token_file(token).exists(), 'metoda poprzedza zużycie tokenu')
        self.assertEqual(self.link_get(token).status, 302)  # kontrola


class LinkLoginToViewTests(LinkCase):
    login_to_view = True

    def test_exchange_works_with_login_to_view_and_the_page_is_then_visible(self):
        self.start_server()
        self.read_state()
        token, _, _ = self.issue('--path', '/demo/')
        self.assertEqual(Client(self.server).get('/demo/').status, 302)  # kontrola: bez sesji brama działa
        response = self.link_get(token)
        self.assertEqual((response.status, response.header('Location')), (302, '/demo/'))
        client = Client(self.server)
        client.cookie = self.cookie_pair(response)
        page = client.get('/demo/')
        self.assertEqual(page.status, 200)
        self.assertIn('DEMO-INDEX', page.text)


# ═════════════════════════════════════════════════════════════════════════════
# Serwer: peer, proxy, Host (K23)
# ═════════════════════════════════════════════════════════════════════════════

class LinkPeerTests(LinkCase):
    def setUp(self):
        super().setUp()
        self.start_server()
        self.read_state()

    def request_bytes(self, token, extra=None, host=None):
        lines = ['GET /_login/link?t=%s HTTP/1.1' % token, 'Host: %s' % (host or '127.0.0.1:%d' % self.server.port),
                 'Connection: close']
        lines += extra or []
        return ('\r\n'.join(lines) + '\r\n\r\n').encode('ascii')

    def test_loopback_peers_in_every_notation_are_accepted(self):
        peers = {'127.0.0.1': ('127.0.0.1', 5555), '::1': ('::1', 5555, 0, 0),
                 '::ffff:127.0.0.1': ('::ffff:127.0.0.1', 5555, 0, 0)}
        for label, peer in peers.items():
            with self.subTest(peer=label):
                token, _, _ = self.issue('--path', '/demo/')
                response = raw_exchange(self.server, self.request_bytes(token), peer)
                self.assertEqual((response.status, response.header('Location')), (302, '/demo/'), response.text)
                self.assertEqual(len(response.cookies()), 1)

    def test_peers_outside_loopback_are_refused_and_do_not_consume_the_token(self):
        token, _, _ = self.issue('--path', '/demo/')
        reference = self.denial()
        for label, peer in {'LAN': ('192.168.1.5', 5555), 'LAN w ::ffff': ('::ffff:192.168.1.5', 5555, 0, 0),
                            'IPv6 globalny': ('2001:db8::1', 5555, 0, 0)}.items():
            with self.subTest(peer=label):
                response = raw_exchange(self.server, self.request_bytes(token), peer)
                self.assertEqual(response.status, 302)
                self.assertEqual(response.header('Location'), '/_login')
                self.assertEqual(response.cookies(), [])
                self.assertEqual(signature(response), signature(reference))
        # kontrola: ten sam token z loopbacku działa, więc odmowy wynikały z adresu peera, nie z tokenu
        ok = raw_exchange(self.server, self.request_bytes(token), ('127.0.0.1', 5555))
        self.assertEqual((ok.status, ok.header('Location')), (302, '/demo/'))
        self.assertEqual(len(ok.cookies()), 1)

    def test_proxy_headers_from_loopback_are_refused_and_do_not_consume_the_token(self):
        token, _, _ = self.issue('--path', '/demo/')
        reference = self.denial()
        for header in ('X-Forwarded-For: 203.0.113.9', 'X-Forwarded-Host: example.org', 'X-Forwarded-Proto: https',
                       'Forwarded: for=203.0.113.9', 'X-Real-IP: 203.0.113.9', 'Via: 1.1 proxy'):
            with self.subTest(header=header):
                self.assert_denied(self.link_get(token, headers={header.split(':')[0]: header.split(': ', 1)[1]}),
                                   reference)
        self.assertTrue(self.token_file(token).exists())
        ok = self.link_get(token)  # kontrola: bez nagłówków proxy ten sam token działa
        self.assertEqual((ok.status, ok.header('Location')), (302, '/demo/'))
        self.assertEqual(len(ok.cookies()), 1)

    def test_host_header_outside_loopback_is_refused_and_does_not_consume_the_token(self):
        token, _, _ = self.issue('--path', '/demo/')
        reference = self.denial()
        for host in ('example.org', 'example.org:%d' % self.server.port, '192.168.1.5:%d' % self.server.port,
                     '0.0.0.0:%d' % self.server.port):
            with self.subTest(host=host):
                self.assert_denied(self.link_get(token, headers={'Host': host}), reference)
        ok = self.link_get(token, headers={'Host': 'localhost:%d' % self.server.port})
        self.assertEqual(ok.status, 302)
        self.assertEqual(ok.header('Location'), '/demo/')

    def test_trust_proxy_server_has_no_link_endpoint_feature_or_issuing(self):
        # --trust-proxy: plik stanu (żywotność) zostaje z `link: false`, ale bez endpointu, bez feature i bez wydawania
        self.stop_server(self.server)
        server = self.start_server(trust_proxy=True, prepare=False)
        state = self.read_state()
        self.assertIs(state['link'], False)
        session = Client(server).get('/_api/v1/session').json
        self.assertNotIn('login-link', session['server']['features'])
        self.assertTrue(any('trust-proxy' in warning for warning in server.app.warnings), server.app.warnings)
        self.blocked(self.run_link(), 'link_unavailable')
        self.assertFalse((self.run_dir / 'login').exists() and any((self.run_dir / 'login').iterdir()))
        response = Client(server).get('/_login/link?t=%s' % fake_token())
        self.assertEqual((response.status, response.cookies()), (404, []))

    def test_token_of_another_server_instance_is_refused_without_being_used_up(self):
        token, _, _ = self.issue('--path', '/demo/')
        path = self.token_file(token)
        data = json.loads(path.read_text(encoding='utf-8'))
        data['instance'] = fake_token()
        path.write_text(json.dumps(data), encoding='utf-8')
        reference = self.denial()
        self.assert_denied(self.link_get(token), reference)
        self.assertTrue(path.exists(), 'obcy instance nie zużywa tokenu')
        data.pop('instance')
        path.write_text(json.dumps(data), encoding='utf-8')
        self.assert_denied(self.link_get(token), reference)  # brak instance = odmowa
        self.assertTrue(path.exists())

    def test_token_with_an_expiry_beyond_ttl_max_is_refused(self):
        token, _, _ = self.issue()
        path = self.token_file(token)
        data = json.loads(path.read_text(encoding='utf-8'))
        data['expires'] = time.time() + 3600
        path.write_text(json.dumps(data), encoding='utf-8')
        self.assert_denied(self.link_get(token))

    def test_two_host_headers_are_refused_and_do_not_consume_the_token(self):
        token, _, _ = self.issue('--path', '/demo/')
        request = ('GET /_login/link?t=%s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nHost: 127.0.0.1:%d\r\nConnection: close\r\n\r\n'
                   % (token, self.server.port, self.server.port)).encode('ascii')
        response = raw_exchange(self.server, request, ('127.0.0.1', 5555))
        self.assertEqual((response.status, response.header('Location'), response.cookies()), (302, '/_login', []))
        self.assertTrue(self.token_file(token).exists())

    def test_ipv6_host_and_header_name_case_are_handled(self):
        token, _, _ = self.issue('--path', '/demo/')
        proxy = ('GET /_login/link?t=%s HTTP/1.1\r\nHOST: [::1]:%d\r\nx-forwarded-for: 1.2.3.4\r\nConnection: close\r\n\r\n'
                 % (token, self.server.port)).encode('ascii')
        denied = raw_exchange(self.server, proxy, ('127.0.0.1', 5555))
        self.assertEqual((denied.status, denied.header('Location')), (302, '/_login'))
        plain = ('GET /_login/link?t=%s HTTP/1.1\r\nHOST: [::1]:%d\r\nConnection: close\r\n\r\n'
                 % (token, self.server.port)).encode('ascii')
        ok = raw_exchange(self.server, plain, ('127.0.0.1', 5555))
        self.assertEqual((ok.status, ok.header('Location')), (302, '/demo/'))

    def test_head_is_refused_and_keeps_the_token(self):
        token, _, _ = self.issue()
        response = self.link_get(token, method='HEAD')
        self.assertEqual((response.status, response.cookies()), (405, []))
        self.assertTrue(self.token_file(token).exists())
        self.assertEqual(self.link_get(token).status, 302)

    def test_windows_branch_of_the_pid_check_never_calls_os_kill(self):
        with mock.patch.object(ps, '_LINK_POSIX', False), mock.patch.object(ps.os, 'kill', side_effect=AssertionError('kill')):
            self.assertTrue(ps.link_pid_alive(2 ** 22 + 12345))


class LinkSecureCookieTests(LinkCase):
    def test_link_cookie_is_secure_with_secure_cookies(self):
        self.start_server(secure_cookies=True)
        self.read_state()
        token, _, _ = self.issue()
        cookies = self.link_get(token).cookies()
        self.assertEqual(len(cookies), 1)
        self.assertIn('Secure', cookies[0])


class LinkCliErrorTests(LinkCase):
    def test_invalid_port_is_a_coded_blocked_json(self):
        self.start_server()
        for port in ('0', '70000', '99999999999999999999999'):
            with self.subTest(port=port):
                self.blocked(self.run_link('--port', port), 'invalid_port')

    def test_broken_project_is_a_coded_blocked_json(self):
        result = subprocess.run([sys.executable, str(CLI), 'prototype', 'login-link', str(self.base / 'nie-ma-takiego'),
                                 '--user', JAN, '--json'], capture_output=True, text=True, timeout=60, cwd=str(self.base))
        report = json.loads(result.stdout)
        self.assertEqual((result.returncode, report['schema'], report['status'], report['code']), (2, 1, 'BLOCKED', 'invalid_project'))


# ═════════════════════════════════════════════════════════════════════════════
# Serwer: audyt `via=link` i pełne uprawnienia (K26)
# ═════════════════════════════════════════════════════════════════════════════

def mentions_link(value):
    """Czy w operacji (dowolna głębokość) jest klucz `via` równy `link`."""
    if isinstance(value, dict):
        return value.get('via') == 'link' or any(mentions_link(item) for item in value.values())
    if isinstance(value, list):
        return any(mentions_link(item) for item in value)
    return False


class LinkAuditTests(LinkCase):
    def setUp(self):
        super().setUp()
        self.log = io.StringIO()
        self.start_server(log=self.log)
        self.read_state()

    def operations(self):
        path = self.protos / 'demo' / 'comments.json'
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding='utf-8'))['operations']

    def audit_lines(self):
        path = self.run_dir / 'audit.jsonl'
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]

    def create_comment(self, client, mid):
        session = client.get('/_api/v1/session').json
        client.csrf = session['csrf']
        return client.post('/_api/v1/prototypes/demo/operations',
                           {'cid': 'c-' + mid, 'type': 'create', 'threadId': 'th-' + mid,
                            'payload': create_payload(mid, 'Uwaga z linku')})

    def test_action_from_a_link_session_is_allowed_and_audited_locally(self):
        token, _, _ = self.issue('--path', '/demo/')
        response = self.link_get(token)
        client = Client(self.server)
        client.cookie = self.cookie_pair(response)
        before_ops = len(self.operations())
        created = self.create_comment(client, 'm-link')
        self.assertEqual(created.status, 201, created.text)  # pełne uprawnienia roli konta
        operations = self.operations()
        self.assertEqual(len(operations), before_ops + 1)
        self.assertFalse(mentions_link(operations[-1]), 'format comments.json bez klucza via')
        self.assertNotIn('via', operations[-1])
        lines = self.audit_lines()
        self.assertEqual(len(lines), 1, lines)
        self.assertEqual((lines[0]['login'], lines[0]['via'], lines[0]['op']), (JAN, 'link', 'create'))
        self.assertTrue(lines[0]['at'].endswith('Z'))
        raw = (self.run_dir / 'audit.jsonl').read_text(encoding='utf-8')
        assert_no_secret(self, raw, [token, sha256_hex(token)], 'audit.jsonl')
        if POSIX:
            self.assertEqual(stat.S_IMODE((self.run_dir / 'audit.jsonl').stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.run_dir.stat().st_mode), 0o700)

    def test_control_action_from_a_password_session_has_no_audit_and_same_format(self):
        client = Client(self.server)
        self.assertEqual(client.login(JAN, PASSWORDS[JAN]).status, 200)
        before_ops = len(self.operations())
        self.assertEqual(self.create_comment(client, 'm-pass').status, 201)
        operations = self.operations()
        self.assertEqual(len(operations), before_ops + 1)
        self.assertEqual(self.audit_lines(), [])
        # ten sam zestaw kluczy operacji co z sesji linku
        token, _, _ = self.issue()
        linked = Client(self.server)
        linked.cookie = self.cookie_pair(self.link_get(token))
        self.assertEqual(self.create_comment(linked, 'm-link2').status, 201)
        ops = self.operations()
        self.assertEqual(sorted(ops[-1]), sorted(ops[-2]))

    @needs_symlinks
    def test_symlink_in_place_of_the_audit_file_is_not_written_through(self):
        target = self.base / 'elsewhere-audit.jsonl'
        target.write_text('', encoding='utf-8')
        os.symlink(str(target), str(self.run_dir / 'audit.jsonl'))
        token, _, _ = self.issue('--path', '/demo/')
        client = Client(self.server)
        client.cookie = self.cookie_pair(self.link_get(token))
        self.assertEqual(self.create_comment(client, 'm-sym').status, 201)  # zapis dziennika przechodzi
        self.assertEqual(target.read_text(encoding='utf-8'), '', 'nic nie idzie przez dowiązanie')
        self.assertIn('audyt', self.log.getvalue())

    def test_validator_does_not_accept_a_top_level_via_key(self):
        operation = {'id': 'op-00000000000000000000000000', 'type': 'create', 'threadId': 'th-00000000000000000000000000',
                     'at': '2026-11-11T10:00:00.000Z', 'by': JAN, 'payload': create_payload('m-val')}
        ps.validate_operation(dict(operation))   # kontrola dodatnia: bez `via` przechodzi
        with self.assertRaises(ps.OperationError):
            ps.validate_operation(dict(operation, via='link'))

    def test_acceptor_link_session_keeps_the_role_of_the_account(self):
        token, _, _ = self.issue(user=ANNA)
        client, session = self.session_of(self.cookie_pair(self.link_get(token)))
        self.assertEqual((session['user']['login'], session['user']['role']), (ANNA, 'acceptor'))


class LinkAuditWritesTests(LinkCase):
    """`project/accept` i `restore` z sesji linku (także przy błędzie po zapisie) trafiają do audit.jsonl."""

    def setUp(self):
        super().setUp()
        write(self.protos / 'demo' / 'index.html', r_page())
        write(self.protos / 'demo' / 'screens.css', 'body { margin: 0 }\n')
        store = ps.CommentsStore(str(self.protos / 'demo'), 'demo')
        ps.perform_iterate(store, str(self.protos / 'demo'))
        write(self.protos / 'demo' / 'index.html', r_page(R_S2_V2, R_S3_V2))
        self.log = io.StringIO()
        self.start_server(log=self.log, source_writes=True)
        self.read_state()
        token, _, _ = self.issue(user=ANNA)
        self.client = Client(self.server)
        self.client.cookie = self.cookie_pair(self.link_get(token))
        self.client.csrf = self.client.get('/_api/v1/session').json['csrf']

    def audit_lines(self):
        path = self.run_dir / 'audit.jsonl'
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []

    def version(self):
        return self.client.get('/_api/v1/prototypes/demo/operations').json['version']

    def restore_body(self):
        return {'cid': uid('r'), 'screen': 's2', 'from': 1, 'reason': 'wróć', 'version': self.version()}

    def test_project_accept_from_a_link_session_is_audited(self):
        response = self.client.post('/_api/v1/project/accept', {'cid': uid('p'), 'versions': {'demo': self.version()}})
        self.assertEqual(response.status, 201, response.text)
        lines = self.audit_lines()
        self.assertEqual([(item['login'], item['via'], item['op']) for item in lines], [(ANNA, 'link', 'project/accept')])

    def test_restore_from_a_link_session_is_audited_with_the_screen(self):
        response = self.client.post('/_api/v1/prototypes/demo/restore', self.restore_body())
        self.assertEqual(response.status, 201, response.text)
        lines = self.audit_lines()
        self.assertEqual([(item['login'], item['op'], item.get('screen')) for item in lines], [(ANNA, 'restore', 's2')])

    def test_partial_project_accept_is_audited_for_what_was_written(self):
        error = ps.OperationError(409, 'partial', 'not every prototype was accepted', {'accepted': ['demo'], 'pending': ['x']})
        with mock.patch.object(ps, 'accept_project', side_effect=error):
            response = self.client.post('/_api/v1/project/accept', {'cid': uid('p'), 'versions': {'demo': self.version()}})
        self.assertEqual(response.status, 409)
        self.assertEqual([item['op'] for item in self.audit_lines()], ['project/accept'])
        with mock.patch.object(ps, 'accept_project', side_effect=ps.OperationError(409, 'partial', 'x', {'accepted': [], 'pending': ['demo']})):
            self.client.post('/_api/v1/project/accept', {'cid': uid('p'), 'versions': {'demo': self.version()}})
        self.assertEqual(len(self.audit_lines()), 1, 'nic nie zapisano = brak wpisu')

    def test_restore_failing_after_the_operations_were_written_is_audited(self):
        real = ps.perform_restore

        def write_then_fail(*args, **kwargs):
            real(*args, **kwargs)
            raise ps.PrototypeServerError(500, 'write_failed', 'po zapisie dziennika')

        with mock.patch.object(ps, 'perform_restore', side_effect=write_then_fail):
            response = self.client.post('/_api/v1/prototypes/demo/restore', self.restore_body())
        self.assertEqual(response.status, 500)
        self.assertEqual([(item['op'], item.get('screen')) for item in self.audit_lines()], [('restore', 's2')])


# ═════════════════════════════════════════════════════════════════════════════
# Brak tokenu w logach (K24)
# ═════════════════════════════════════════════════════════════════════════════

class LinkLogTests(LinkCase):
    def test_detector_control_catches_a_leaking_line(self):
        token = fake_token()
        with self.assertRaises(AssertionError):
            assert_no_secret(self, 'GET /_login/link?t=%s 302 -' % token, [token])
        with self.assertRaises(AssertionError):
            assert_no_secret(self, 'sha %s' % sha256_hex(token), [sha256_hex(token)])

    def test_no_token_or_hash_in_the_request_log_or_stderr_for_200_302_4xx_5xx(self):
        log = io.StringIO()
        self.start_server(log=log)
        self.read_state()
        err = io.StringIO()
        issued, _, _ = self.issue('--path', '/demo/')
        reused, _, _ = self.issue()
        forged = fake_token()
        secrets_list = []
        for token in (issued, reused, forged):
            secrets_list += [token, sha256_hex(token)]
        with contextlib.redirect_stderr(err):
            self.assertEqual(self.link_get(issued).status, 302)                       # 302 z sesją
            self.assertEqual(self.link_get(issued).status, 302)                       # odmowa
            self.assertEqual(self.link_get(forged).status, 302)                       # podrobiony
            self.link_get(reused, method='POST')                                      # 4xx (metoda)
            self.link_get(None, path='/nie-ma-takiej-strony', query='?t=%s' % forged)  # 404
            raw_exchange(self.server, ('GET /_login/link?t=%s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-Forwarded-For: 1.2.3.4\r\n'
                                       'Connection: close\r\n\r\n' % (reused, self.server.port)).encode('ascii'),
                         ('127.0.0.1', 5555))
            with mock.patch.object(ps.SessionStore, 'create', side_effect=RuntimeError('awaria sesji')):
                self.link_get(reused)                                                 # ścieżka 5xx / traceback
        text = log.getvalue() + err.getvalue()
        self.assertIn('/_login/link', log.getvalue(), 'kontrola: dziennik żądań nie jest pusty i loguje ścieżkę')
        assert_no_secret(self, text, secrets_list)

    def test_bad_request_line_with_a_token_in_the_query_is_not_logged(self):
        # LUKA PS:4482: bez `path` log_request loguje surowy `requestline` z query
        log = io.StringIO()
        self.start_server(log=log)
        token = fake_token()
        err = io.StringIO()
        for line in ('GET /_login/link?t=%s\r\n\r\n' % token,                       # HTTP/0.9
                     'GET\x01/_login/link?t=%s HTTP/1.1 EXTRA\r\n\r\n' % token,
                     'BAD /_login/link?t=%s\r\n\r\n' % token,
                     'GET /_login/link?t=%s HTTP/9.9\r\nHost: x\r\n\r\n' % token):
            with self.subTest(line=line[:20]), contextlib.redirect_stderr(err):
                conn = socket.create_connection(('127.0.0.1', self.server.port), timeout=5)
                try:
                    conn.sendall(line.encode('latin-1'))
                    conn.shutdown(socket.SHUT_WR)
                    while conn.recv(65536):
                        pass
                finally:
                    conn.close()
        time.sleep(0.2)
        self.assertTrue(log.getvalue().strip(), 'kontrola: błędne żądania trafiły do dziennika')
        assert_no_secret(self, log.getvalue() + err.getvalue(), [token, sha256_hex(token)])


# ═════════════════════════════════════════════════════════════════════════════
# Plik stanu serwera i katalog run (K24, K27)
# ═════════════════════════════════════════════════════════════════════════════

class ServerStateFileTests(LinkCase):
    def test_state_file_content_mode_and_session_instance(self):
        server = self.start_server()
        state = self.read_state()
        self.assertEqual(state['pid'], os.getpid())
        self.assertEqual((state['host'], state['port']), ('127.0.0.1', server.port))
        self.assertTrue(isinstance(state['instance'], str) and len(state['instance']) >= 16)
        self.assertTrue(state['version'])
        self.assertEqual(os.path.realpath(state['root']), os.path.realpath(str(self.protos)))
        self.assertEqual(os.path.realpath(state['users']), os.path.realpath(str(self.users_path)))
        if POSIX:
            self.assertEqual(stat.S_IMODE(self.state_path().stat().st_mode), 0o600)
        session = Client(server).get('/_api/v1/session').json
        self.assertEqual(session['server']['instance'], state['instance'])
        self.assertIn('login-link', session['server']['features'])
        self.assertNotIn('password', json.dumps(state))

    def test_state_file_is_removed_when_the_server_stops(self):
        server = self.start_server()
        self.read_state()
        self.assertTrue(self.state_path().exists())
        self.stop_server(server)
        self.assertFalse(self.state_path().exists())

    @needs_posix
    def test_sigint_of_the_real_serve_process_removes_the_state_file_and_the_link_works(self):
        process = subprocess.Popen([sys.executable, str(CLI), 'prototype', 'serve', str(self.root), '--port', '0', '--json'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(self.base))
        watchdog = threading.Timer(40, process.kill)
        watchdog.start()
        lines = []
        try:
            port = None
            for line in process.stderr:
                lines.append(line)
                found = re.search(r'Serwer prototypów: http://127\.0\.0\.1:(\d+)/', line)
                if found:
                    port = int(found.group(1))
                    break
            self.assertIsNotNone(port, 'serwer nie ogłosił adresu: ' + ''.join(lines))
            state_path = self.run_dir / ('server-%d.json' % port)
            deadline = time.time() + 5
            while not state_path.exists() and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(state_path.exists(), 'serve zapisuje plik stanu w <repo>/.skillsync/prototype/run/')
            self.assertEqual(json.loads(state_path.read_text(encoding='utf-8'))['pid'], process.pid)
            token, report, _ = self.issue('--path', '/demo/')
            self.assertTrue(report['url'].startswith('http://127.0.0.1:%d/_login/link?t=' % port))
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
            conn.request('GET', '/_login/link?t=%s' % token)
            response = conn.getresponse()
            response.read()
            conn.close()
            self.assertEqual((response.status, response.getheader('Location')), (302, '/demo/'))
            process.send_signal(signal.SIGINT)
            process.communicate(timeout=20)
        finally:
            watchdog.cancel()
            if process.poll() is None:
                process.kill()
                process.communicate()
        self.assertEqual(process.returncode, 0)
        self.assertFalse(state_path.exists(), 'po SIGINT plik stanu znika')

    @needs_posix
    def test_sigterm_of_the_real_serve_process_removes_the_state_file(self):
        process = subprocess.Popen([sys.executable, str(CLI), 'prototype', 'serve', str(self.root), '--port', '0', '--json'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(self.base))
        watchdog = threading.Timer(40, process.kill)
        watchdog.start()
        try:
            port = None
            for line in process.stderr:
                found = re.search(r'Serwer prototypów: http://127\.0\.0\.1:(\d+)/', line)
                if found:
                    port = int(found.group(1))
                    break
            self.assertIsNotNone(port)
            state_path = self.run_dir / ('server-%d.json' % port)
            deadline = time.time() + 5
            while not state_path.exists() and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(state_path.exists())
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=20)
        finally:
            watchdog.cancel()
            if process.poll() is None:
                process.kill()
                process.communicate()
        self.assertEqual(process.returncode, 0)
        self.assertFalse(state_path.exists(), 'po SIGTERM plik stanu znika')

    @needs_posix
    def test_run_dir_with_loose_permissions_disables_the_link_endpoint_with_a_warning(self):
        self.runtime_mode = 0o755
        server = self.start_server()
        self.assertTrue(any('run' in warning or str(self.run_dir) in warning for warning in server.app.warnings),
                        server.app.warnings)
        self.assertEqual(Client(server).get('/_api/v1/session').status, 200)  # serwer działa
        result = self.run_link()
        self.assertEqual(self.report_of(result)['status'], 'BLOCKED', result.stdout)
        self.assertEqual(result.returncode, 2)
        # kontrola: ten sam układ z prawami 0700 nie ma takiego ostrzeżenia i wydaje link
        self.stop_server(server)
        self.runtime_mode = 0o700
        good = self.start_server()
        self.read_state()
        self.assertFalse(any(str(self.run_dir) in warning for warning in good.app.warnings), good.app.warnings)
        self.issue()

    @needs_posix
    def test_run_dir_symlink_disables_the_link_endpoint_with_a_warning(self):
        real = self.base / 'elsewhere-run'
        real.mkdir(mode=0o700)
        os.chmod(str(real), 0o700)
        self.run_dir.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(str(real), str(self.run_dir))
        server = self.start_server(prepare=False)
        self.assertTrue(server.app.warnings)
        self.assertTrue(any('run' in warning for warning in server.app.warnings), server.app.warnings)
        self.assertEqual(Client(server).get('/_api/v1/session').status, 200)
        result = self.run_link()
        self.assertEqual((result.returncode, self.report_of(result)['status']), (2, 'BLOCKED'), result.stdout)
        self.assertEqual(list(real.iterdir()), [], 'nic nie jest zapisywane przez dowiązanie')


# ═════════════════════════════════════════════════════════════════════════════
# Bez `runtime_dir`: standalone, zachowanie bez zmian (K27)
# ═════════════════════════════════════════════════════════════════════════════

class NoRuntimeDirTests(LinkCase):
    def start_plain(self, **kwargs):
        config = ps.ServerConfig(str(self.protos), str(self.users_path), host='127.0.0.1', port=0,
                                 pbkdf2_iterations=ITER, log=None, git_checks=False, **kwargs)
        server = ps.make_server(config)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread.start()
        self._servers.append((server, thread))
        self.server = server
        return server

    def assert_standalone_has_no_link(self, server):
        token = fake_token()
        response = Client(server).get('/_login/link?t=%s' % token)
        self.assertIn(response.status, (400, 401, 403, 404))
        self.assertEqual(response.cookies(), [])
        leftovers = [str(path) for path in self.root.rglob('server-*.json')]
        self.assertEqual(leftovers, [])
        self.assertFalse(self.run_dir.exists())

    def test_default_config_has_no_state_file_and_no_link_endpoint(self):
        # z natury zielony przed zmianą: opisuje zachowanie, które ma się nie zmienić
        server = self.start_plain()
        self.assertEqual(Client(server).get('/demo/').status, 200)  # kontrola: serwer działa
        self.assert_standalone_has_no_link(server)

    def test_runtime_dir_none_is_the_same_as_no_runtime_dir(self):
        server = self.start_plain(runtime_dir=None)
        self.assertEqual(Client(server).get('/demo/').status, 200)
        self.assert_standalone_has_no_link(server)


if __name__ == '__main__':
    unittest.main()
