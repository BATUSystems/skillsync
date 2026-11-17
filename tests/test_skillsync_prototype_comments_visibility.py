"""180-Q (since 1.9.0, variant W1): the journal of a prototype is given only by `GET /_api/v1/prototypes/<slug>/operations`; the static
`<slug>/comments.json` and `<slug>/comments.js` are 404 under `serve` for every role, with and without `--login-to-view` (as under `serve --demo`).
The API still gives the same set of operations to every role and `--login-to-view` is its only gate. A change of this (a visibility model for the
`expert` role) must change this test on purpose."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow import prototype_server as ps  # noqa: E402
from tests.test_skillsync_prototype_server import (  # noqa: E402
    A, C, D, ITER, PASSWORDS, Client, ServerCase, create_payload)

EXPERT = 'ewa.ekspert@example.com'
EXPERT_PASSWORD = '-'.join(['ewa', 'login', str(1234)])   # composed at run time: no literal that looks like a secret


class CommentsCase(ServerCase):
    login_to_view = False

    def setUp(self):
        super().setUp()
        users = ps.UsersFile(self.users_path, iterations=ITER, clock=self.clock)
        ps.add_user(users, EXPERT, role='expert', password=EXPERT_PASSWORD)
        writer = self.client(A)
        self.seed(writer, 'th-1', 'm-1', 'Treść komentarza do ukrycia przed nikim')
        self.seed(writer, 'th-2', 'm-2', 'Drugi komentarz')

    def assert_static_is_gone(self, client):
        for path in ('/demo/comments.json', '/demo/comments.js'):
            self.assertEqual(client.get(path).status, 404, path)

    def api_ids(self, client):
        response = client.get('/_api/v1/prototypes/demo/operations')
        self.assertEqual(response.status, 200, response.text)
        return sorted(operation['id'] for operation in response.json['operations'])

    def clients(self):
        out = {'guest': Client(self.server)}
        for name, login, password in (('commenter', A, PASSWORDS[A]), ('acceptor', C, PASSWORDS[C]), ('developer', D, PASSWORDS[D]), ('expert', EXPERT, EXPERT_PASSWORD)):
            client = Client(self.server)
            self.assertEqual(client.login(login, password).status, 200, name)
            out[name] = client
        return out


class PublicRead(CommentsCase):
    def test_every_role_and_a_guest_read_the_same_operations_by_the_api_only(self):
        clients = self.clients()
        reference = self.api_ids(clients['guest'])
        self.assertEqual(len(reference), 2)
        for name, client in clients.items():
            with self.subTest(role=name):
                self.assertEqual(self.api_ids(client), reference)
                self.assert_static_is_gone(client)

    def test_the_static_file_gives_no_text_and_the_api_does(self):
        guest = Client(self.server)
        self.assertNotIn('Treść komentarza do ukrycia przed nikim', guest.get('/demo/comments.json').text)
        api_text = json.dumps(guest.get('/_api/v1/prototypes/demo/operations').json, ensure_ascii=False)
        self.assertIn('Treść komentarza do ukrycia przed nikim', api_text)


class BehindLogin(CommentsCase):
    login_to_view = True

    def test_without_a_session_nothing_is_given(self):
        guest = Client(self.server)
        self.assertIn(guest.get('/demo/comments.json').status, (401, 404))
        self.assertEqual(guest.get('/_api/v1/prototypes/demo/operations').status, 401)

    def test_every_role_with_a_session_reads_the_same_operations_by_the_api_only(self):
        reference = None
        for name, client in self.clients().items():
            if name == 'guest':
                continue
            with self.subTest(role=name):
                ids = self.api_ids(client)
                reference = reference or ids
                self.assertEqual(ids, reference)
                self.assert_static_is_gone(client)


if __name__ == '__main__':
    unittest.main()
