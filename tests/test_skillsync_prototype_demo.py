"""180-P: `prototype serve --demo`, the read-only show without accounts. The server refuses every write by itself (not only the UI), the guest
never gets a login, comment, acceptance or account, nothing is written, and no answer repeats the request."""
import http.client
import json
import os
import re
import socket
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow import prototype_server as ps  # noqa: E402
from tests.test_skillsync_prototype_server import (  # noqa: E402
    A, C, Client, ServerCase, create_payload, raw_create, symlinks_supported, write, write_journal)

HIDDEN_TEXT = 'UKRYTA-TRESC-KOMENTARZA-7731'
HIDDEN_LOGIN = 'tajny.recenzent@example.invalid'
HIDDEN_REQUIREMENTS = 'docs/SEKRETNE-wymagania-4410.md'
MARKER = 'zzmarker-5521'


def tree(root):
    found = {}
    for current, dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(current, name)
            info = os.stat(path)
            found[os.path.relpath(path, root)] = (info.st_size, info.st_mtime_ns)
    return found


class DemoCase(ServerCase):
    """A normal server is started by `ServerCase` (with the accounts file); the show runs on the same tree on another port."""

    def setUp(self):
        super().setUp()
        write(os.path.join(self.demo, 'prototype.json'),
              json.dumps({'slug': 'demo', 'title': 'Pokaz', 'requirements': HIDDEN_REQUIREMENTS, 'created': '2026-11-05T09:00:00Z',
                          'engineAssetHistory': ['deadbeef'], 'note': HIDDEN_LOGIN}))
        for name, text in (('tokens.css', ':root{}'), ('theme.css', ':root{}'), ('components.css', '.btn{}'), ('screens.css', '.doc{}'),
                           ('prototype.css', '.x{}'), ('project.css', '.y{}'), ('logo.svg', '<svg xmlns="http://www.w3.org/2000/svg"/>'),
                           ('data/orders.json', '[1,2]'), ('data/notes.txt', 'plain'), ('data/hidden.bin', 'binary'),
                           ('README.md', 'opis ' + HIDDEN_REQUIREMENTS), ('comments.js', 'window.X = "%s";' % HIDDEN_TEXT),
                           ('comments.v1.js', 'old ' + HIDDEN_TEXT), ('questions.json', HIDDEN_TEXT),
                           ('iterations/1/index.html', '<html>%s</html>' % HIDDEN_LOGIN),
                           ('iterations/1/prototype.json', '{"title": "stary", "by": "%s"}' % HIDDEN_LOGIN),
                           ('notes-on-screens.md', HIDDEN_TEXT), ('.hidden', HIDDEN_TEXT)):
            write(os.path.join(self.demo, name), text)
        if symlinks_supported():
            os.symlink(os.path.join(self.demo, 'comments.json'), os.path.join(self.demo, 'alias.txt'))
            os.symlink(os.path.join(self.demo, 'prototype.json'), os.path.join(self.demo, 'alias.json'))
        operations = [raw_create(1, by=HIDDEN_LOGIN)]
        operations[0]['payload'] = create_payload('m-1', HIDDEN_TEXT)
        operations.append({'id': 'op-' + '2'.zfill(26), 'type': 'accept', 'at': '2026-11-05T09:00:02.000Z', 'by': C,
                           'payload': {'scope': 'project', 'screens': {'s1': 'a' * 64}, 'iteration': 1, 'via': 'ui'}})
        write_journal(self.demo, 'demo', operations)
        write(os.path.join(self.root, 'index.html'), '<html>STATIC-GALLERY %s</html>' % HIDDEN_TEXT)
        self.gallery_calls = []
        self.start_server(demo=True, gallery=lambda: '<html>SHOW-GALLERY</html>')
        self.guest = Client(self.server)
        self.before = tree(self.tmp)

    def raw(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.port, timeout=15)
        try:
            connection.putrequest(method, path, skip_host=bool(headers and 'Host' in headers))
            for key, value in (headers or {}).items():
                connection.putheader(key, value)
            if body is not None:
                connection.putheader('Content-Length', str(len(body)))
            connection.endheaders(body)
            response = connection.getresponse()
            return response.status, response.msg, response.read()
        finally:
            connection.close()


class WriteRefusalTests(DemoCase):
    METHODS = ('POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS')
    PATHS = ('/', '/index.html', '/demo/', '/demo/index.html', '/_api/v1/session', '/_api/v1/login', '/_api/v1/logout',
             '/_api/v1/password', '/_api/v1/project', '/_api/v1/project/accept', '/_api/v1/questions',
             '/_api/v1/prototypes/demo/operations', '/_api/v1/prototypes/demo/restore', '/_api/v1/prototypes/demo/writing/release',
             '/_api/v1/prototypes/demo/questions/q1/answer', '/_login', '/_login/link?t=x', '/_experts', '/_experts/demo',
             '/demo/comments.json', '/unknown')

    def test_every_method_other_than_get_and_head_is_refused_on_every_route_and_writes_nothing(self):
        for method in self.METHODS:
            for path in self.PATHS:
                with self.subTest(method=method, path=path):
                    status, headers, body = self.raw(method, path, b'{"type":"create"}' if method != 'OPTIONS' else None,
                                                     {'Content-Type': 'application/json'})
                    self.assertEqual(status, 405, body)
                    self.assertEqual(json.loads(body), {'error': 'demo_read_only'})
                    self.assertEqual(headers.get('Allow'), 'GET, HEAD')
                    self.assertFalse(headers.get_all('Set-Cookie'))
        self.assertEqual(tree(self.tmp), self.before, 'nothing is written')

    def test_a_valid_logged_in_looking_post_is_not_a_write_either(self):
        # a session cookie and a CSRF token of the normal server are meaningless here
        normal = Client(self._servers[0][0])
        self.assertEqual(normal.login(C, 'Haslo-Celiny-1234').status, 200)
        self.guest.cookie, self.guest.csrf = normal.cookie, normal.csrf
        response = self.guest.op('demo', 'create', 'th-1', create_payload('m-9', 'x'))
        self.assertEqual(response.status, 405)
        self.assertEqual(tree(self.tmp), self.before)

    def test_login_is_impossible(self):
        for path in ('/_api/v1/login',):
            response = self.guest.post(path, {'login': A, 'password': 'whatever'})
            self.assertEqual(response.status, 405)
        self.assertFalse(response.cookies())

    def test_get_routes_that_have_effects_or_data_are_not_there(self):
        for path in ('/_login', '/_login/link?t=abc', '/_experts', '/_experts/demo', '/_api/v1/project', '/_api/v1/questions',
                     '/_api/v1/prototypes/demo/operations', '/_api/v1/prototypes/demo/restore-preview', '/_api/v1/anything', '/_x'):
            with self.subTest(path=path):
                response = self.guest.get(path)
                self.assertEqual(response.status, 404, response.text)
                self.assertFalse(response.cookies())
        self.assertEqual(tree(self.tmp), self.before)

    def test_the_session_answer_says_demo_and_has_no_user_or_token(self):
        data = self.guest.get('/_api/v1/session').json
        self.assertEqual((data['user'], data['csrf']), (None, None))
        self.assertTrue(data['server']['demo'])
        self.assertFalse(data['server']['sourceWrites'])
        self.assertEqual(data['server']['features'], [])
        self.assertFalse(self.guest.get('/_api/v1/session').cookies())


class StaticInventoryTests(DemoCase):
    def test_the_guest_gets_the_screens_the_engine_the_styles_and_media(self):
        for path in ('/demo/index.html', '/demo/prototype.js', '/demo/tokens.css', '/demo/theme.css', '/demo/components.css',
                     '/demo/screens.css', '/demo/prototype.css', '/demo/project.css', '/demo/logo.svg', '/demo/data/orders.json',
                     '/demo/data/notes.txt', '/demo/'):
            with self.subTest(path=path):
                self.assertEqual(self.guest.get(path).status, 200)

    def test_the_comment_log_and_everything_else_that_is_not_for_a_guest_is_not_served(self):
        for path in ('/demo/comments.json', '/demo/comments.js', '/demo/comments.v1.js', '/demo/README.md', '/demo/questions.json',
                     '/demo/iterations/1/index.html', '/demo/iterations/1/prototype.json', '/demo/iterations/', '/demo/notes-on-screens.md',
                     '/demo/data/hidden.bin', '/demo/.hidden', '/demo/alias.txt', '/demo/alias.json', '/questions.json',
                     '/demo/Comments.JSON', '/demo/COMMENTS.json', '/demo/%63omments.json', '/demo/%2e%2e/comments.json',
                     '/demo/..%2fdemo%2fcomments.json', '/demo\\comments.json', '/demo/comments.json/', '/demo/comments.json%00.png',
                     '/old/comments.js'):
            with self.subTest(path=path):
                self.assertEqual(self.guest.get(path).status, 404)
                self.assertEqual(self.guest.request('HEAD', path).status, 404)

    def test_prototype_json_is_trimmed_to_slug_and_title(self):
        data = self.guest.get('/demo/prototype.json').json
        self.assertEqual(data, {'slug': 'demo', 'title': 'Pokaz'})

    def test_the_gallery_is_the_show_variant_and_the_static_file_is_never_the_fallback(self):
        self.assertIn('SHOW-GALLERY', self.guest.get('/').text)
        self.assertIn('SHOW-GALLERY', self.guest.get('/index.html').text)
        self.start_server(demo=True, gallery=None)
        broken = Client(self.server)
        self.assertEqual(broken.get('/').status, 404)
        self.assertEqual(broken.get('/index.html').status, 404)

    def test_the_physical_gallery_file_is_not_served_under_another_case_of_its_name(self):
        write(os.path.join(self.root, 'INDEX.HTML'), '<html>Otwarte komentarze %s</html>' % HIDDEN_TEXT)
        for path in ('/INDEX.HTML', '/Index.html', '/index.HTML'):
            with self.subTest(path=path):
                response = self.guest.get(path)
                self.assertNotIn(HIDDEN_TEXT, response.text)
                self.assertNotIn('Otwarte komentarze', response.text)

    def test_no_answer_of_the_show_contains_a_login_comment_text_or_the_requirements_path(self):
        seen = []
        paths = ['/', '/index.html', '/_api/v1/session', '/_api/v1/prototypes/demo/operations', '/demo/', '/demo/prototype.json',
                 '/demo/comments.json', '/demo/iterations/1/index.html', '/demo/README.md']
        for current, _, files in os.walk(self.demo):
            for name in files:
                paths.append('/demo/' + os.path.relpath(os.path.join(current, name), self.demo).replace(os.sep, '/'))
        for path in paths:
            for method in ('GET', 'HEAD'):
                status, headers, body = self.raw(method, path)
                seen.append((path, str(headers).encode() + body))
        for path, data in seen:
            for secret in (HIDDEN_TEXT, HIDDEN_LOGIN, HIDDEN_REQUIREMENTS, 'deadbeef', C):
                self.assertNotIn(secret.encode(), data, (path, secret))

    def test_the_gallery_of_the_show_has_no_comment_count_or_acceptance(self):
        from skillsync_workflow import prototype
        entries = [{'slug': 'demo', 'title': 'Pokaz', 'requirements': HIDDEN_REQUIREMENTS, 'created': '2026-11-05', 'screens': 3,
                    'comments': 4, 'acceptance': {'accepted': 1, 'screens': 3}, 'questions': True, 'writing': {'since': 1}}]
        shown = prototype.render_gallery(entries, demo=True)
        for text in (HIDDEN_REQUIREMENTS, 'Otwarte komentarze', 'Akceptacja', '/_experts', 'W trakcie zapisu', 'i komentarze'):
            self.assertNotIn(text, shown)
        self.assertIn('Pokaz', shown)
        self.assertIn(HIDDEN_REQUIREMENTS, prototype.render_gallery(entries))   # the normal gallery keeps it


@unittest.skipUnless(symlinks_supported(), 'brak dowiązań symbolicznych w tym systemie')
class LinkTests(DemoCase):
    """Links inside a prototype must not give a guest a file the positive list refuses (review S1, N2, N3)."""

    def link(self, name, target):
        path = os.path.join(self.demo, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.symlink(target, path)

    def test_a_gallery_file_that_is_a_link_to_the_comment_log_is_not_served_even_when_the_gallery_fails(self):
        os.unlink(os.path.join(self.root, 'index.html'))
        os.symlink(os.path.join(self.demo, 'comments.json'), os.path.join(self.root, 'index.html'))
        for gallery in (None, lambda: 1 / 0):
            self.start_server(demo=True, gallery=gallery)
            guest = Client(self.server)
            for path in ('/index.html', '/'):
                with self.subTest(gallery=gallery, path=path):
                    response = guest.get(path)
                    self.assertNotIn(HIDDEN_TEXT, response.text)
                    self.assertNotIn(HIDDEN_LOGIN, response.text)

    def test_a_directory_whose_index_html_is_a_link_to_the_comment_log_is_not_served(self):
        self.link('data/index.html', os.path.join(self.demo, 'comments.json'))
        self.assertEqual(self.guest.get('/demo/data/').status, 404)
        self.assertEqual(self.guest.get('/demo/data/index.html').status, 404)

    def test_a_prototype_link_to_a_frozen_iteration_is_not_served(self):
        os.symlink(os.path.join(self.demo, 'iterations', '1'), os.path.join(self.root, 'arch'))
        self.assertEqual(self.guest.get('/arch/').status, 404)
        self.assertEqual(self.guest.get('/arch/index.html').status, 404)
        self.assertEqual(self.guest.get('/arch/prototype.json').status, 404)

    def test_a_nested_prototype_json_is_not_served_untrimmed(self):
        write(os.path.join(self.demo, 'sub', 'prototype.json'), json.dumps({'slug': 'x', 'requirements': HIDDEN_REQUIREMENTS}))
        self.assertEqual(self.guest.get('/demo/sub/prototype.json').status, 404)

    def test_a_link_with_a_harmless_name_to_a_file_of_another_kind_is_not_served(self):
        write(os.path.join(self.demo, 'notes.yaml'), 'secret: ' + HIDDEN_TEXT)
        self.link('x.png', os.path.join(self.demo, 'notes.yaml'))
        self.link('data/y.txt', os.path.join(self.demo, 'notes.yaml'))
        self.assertEqual(self.guest.get('/demo/x.png').status, 404)
        self.assertEqual(self.guest.get('/demo/data/y.txt').status, 404)

    def test_a_link_to_an_allowed_file_still_works(self):
        self.link('data/copy.txt', os.path.join(self.demo, 'data', 'notes.txt'))
        self.assertEqual(self.guest.get('/demo/data/copy.txt').status, 200)

    def test_the_redirect_of_a_directory_does_not_repeat_the_query(self):
        status, headers, body = self.raw('GET', '/demo/data?q=%s' % MARKER)
        self.assertEqual(status, 301)
        self.assertNotIn(MARKER, headers.get('Location'))


class GateMutationTests(DemoCase):
    def test_the_matrix_would_notice_a_missing_gate(self):
        from unittest import mock

        def no_gate(handler, method, path, query):
            handler._static(path, query)   # the show without its gate: a POST would be answered like any path
        with mock.patch.object(ps.PrototypeRequestHandler, '_demo', no_gate):
            status, headers, body = self.raw('POST', '/_api/v1/prototypes/demo/operations', b'{}')
        self.assertNotEqual(status, 405)
        status, headers, body = self.raw('POST', '/_api/v1/prototypes/demo/operations', b'{}')
        self.assertEqual(status, 405)


class ErrorTextTests(DemoCase):
    def test_error_answers_do_not_repeat_the_request_or_show_a_stack(self):
        probes = ['/%s/x' % MARKER, '/demo/%s.png' % MARKER, '/_api/v1/%s' % MARKER, '/_%s' % MARKER, '/demo/%s/comments.json' % MARKER]
        for path in probes:
            for method in ('GET', 'POST', 'PUT'):
                status, headers, body = self.raw(method, path, b'{}' if method != 'GET' else None)
                text = (str(headers) + body.decode('utf-8', 'replace'))
                self.assertNotIn(MARKER, text, (method, path))
                self.assertNotIn('Traceback', text)
                self.assertNotIn('File "', text)

    def test_a_broken_request_line_gets_a_fixed_answer(self):
        with socket.create_connection(('127.0.0.1', self.server.port), timeout=10) as connection:
            connection.sendall(('GARBAGE %s\r\n\r\n' % MARKER).encode())
            data = b''
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                data += chunk
        self.assertNotIn(MARKER.encode(), data)
        self.assertTrue(data.startswith(b'HTTP/1.'), data[:40])

    def test_a_real_http_0_9_line_is_answered_in_the_0_9_way_and_nothing_is_echoed(self):
        """Only a genuine `GET <path>` line (two words) is HTTP/0.9: the bare body; the same on every Python."""
        with socket.create_connection(('127.0.0.1', self.server.port), timeout=10) as connection:
            connection.sendall(('GET /%s\r\n\r\n' % MARKER).encode())
            data = b''
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                data += chunk
        self.assertNotIn(MARKER.encode(), data)
        self.assertNotIn(b'Traceback', data)

    def test_every_other_broken_request_line_has_a_status_line(self):
        for raw in (b'GARBAGE\r\n\r\n', b'GARBAGE A B C\r\n\r\n', b'GET /x HTTP/9.9\r\n\r\n', b'GET /x NOTHTTP\r\n\r\n'):
            with self.subTest(raw=raw), socket.create_connection(('127.0.0.1', self.server.port), timeout=10) as connection:
                connection.sendall(raw)
                data = b''
                while True:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    data += chunk
            self.assertTrue(data.startswith(b'HTTP/1.'), (raw, data[:40]))
            self.assertTrue(data.endswith(b'Error\n'), (raw, data[-20:]))

    def test_an_unsupported_method_name_is_not_echoed(self):
        status, headers, body = self.raw('PROPFIND' + MARKER[:6], '/')
        self.assertEqual(status, 405)
        self.assertNotIn(MARKER[:6].encode(), body)

    def test_an_internal_error_is_a_fixed_code(self):
        from unittest import mock
        with mock.patch.object(ps.PrototypeRequestHandler, '_static', side_effect=RuntimeError('boom ' + MARKER)):
            status, headers, body = self.raw('GET', '/demo/index.html')
        self.assertEqual(status, 500)
        self.assertNotIn(MARKER.encode(), body)
        self.assertEqual(json.loads(body), {'error': 'internal_error'})


class HostAndStartTests(DemoCase):
    def test_a_foreign_host_header_gets_the_same_answers_as_the_right_one(self):
        # decision (spec 3.9): the show has no credentials a rebinding page could use, so there is no Host check; the answers and the
        # absence of private data are the same whatever the Host says
        for host in ('evil.example.invalid', 'evil.example.invalid:80', '[::1]', ''):
            with self.subTest(host=host):
                status, headers, body = self.raw('GET', '/demo/index.html', headers={'Host': host})
                self.assertEqual(status, 200)
                status, headers, body = self.raw('GET', '/demo/comments.json', headers={'Host': host})
                self.assertEqual(status, 404)
                status, headers, body = self.raw('POST', '/_api/v1/prototypes/demo/operations', b'{}', headers={'Host': host})
                self.assertEqual(status, 405)
        self.assertEqual(tree(self.tmp), self.before)

    def test_the_show_starts_without_a_users_file_and_ignores_one(self):
        config = ps.ServerConfig(self.root, None, host='127.0.0.1', port=0, git_checks=False, demo=True)
        self.assertEqual(ps.startup_checks(config), ([], []))
        server = ps.make_server(config)
        try:
            self.assertIsNone(server.app.link_dir)
            self.assertIsNone(server.app.state_file)
            self.assertFalse(config.source_writes or config.recover or config.login_to_view)
        finally:
            server.server_close()

    def test_a_missing_root_is_still_an_error(self):
        config = ps.ServerConfig(os.path.join(self.tmp, 'nope'), None, port=0, git_checks=False, demo=True)
        errors, _ = ps.startup_checks(config)
        self.assertEqual(len(errors), 1)

    def test_nothing_is_written_by_a_whole_visit(self):
        for path in ('/', '/demo/', '/demo/index.html', '/_api/v1/session', '/demo/prototype.json', '/demo/logo.svg'):
            self.guest.get(path)
        self.assertEqual(tree(self.tmp), self.before)
        self.assertFalse(re.search(r'\.lock$|\.tmp$', ' '.join(tree(self.tmp))))

    def test_the_normal_server_is_unchanged_by_the_option_existing(self):
        # the first server of the case is the normal one: accounts, login, comments
        normal = Client(self._servers[0][0])
        self.assertEqual(normal.get('/demo/comments.json').status, 200)   # the finding of spec 3.11, not changed here
        self.assertEqual(normal.post('/_api/v1/login', {'login': 'x', 'password': 'y'}).status, 401)


class GateOrderTests(unittest.TestCase):
    def test_the_gate_runs_before_the_session_and_every_route(self):
        import inspect
        source = inspect.getsource(ps.PrototypeRequestHandler._dispatch)
        self.assertIn('self._demo(method, path, split.query)', source)
        self.assertLess(source.index('self._demo('), source.index('self._session()'))
        self.assertLess(source.index('self._demo('), source.index('self._api('))

    def test_a_new_route_cannot_be_reached_in_the_show(self):
        # every path that starts with `/_` and every method but GET/HEAD is answered by the gate; so a route added later is refused by
        # construction, not by a table that somebody has to remember
        source = inspect_source()
        self.assertIn("path.startswith('/_')", source)
        self.assertIn("method not in ('GET', 'HEAD')", source)


def inspect_source():
    import inspect
    return inspect.getsource(ps.PrototypeRequestHandler._demo)


if __name__ == '__main__':
    unittest.main()
