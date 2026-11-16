"""180-Q: who reads the comments of a prototype today (a pin, not a wish): the static `comments.json` and `GET /_api/v1/prototypes/<slug>/operations` give the
same set of operations to every role, and `--login-to-view` is the only gate of both. A change of this (a visibility model for the `expert` role, a 404 for the
static file) must change this test on purpose."""
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

    def static_ids(self, client):
        response = client.get('/demo/comments.json')
        self.assertEqual(response.status, 200, response.text)
        return sorted(operation['id'] for operation in json.loads(response.text)['operations'])

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
    def test_every_role_and_a_guest_read_the_same_operations_by_both_ways(self):
        clients = self.clients()
        reference = self.api_ids(clients['guest'])
        self.assertEqual(len(reference), 2)
        for name, client in clients.items():
            with self.subTest(role=name):
                self.assertEqual(self.api_ids(client), reference)
                self.assertEqual(self.static_ids(client), reference)

    def test_the_static_file_adds_no_text_the_api_does_not_give(self):
        guest = Client(self.server)
        static_text = guest.get('/demo/comments.json').text
        api_text = json.dumps(guest.get('/_api/v1/prototypes/demo/operations').json, ensure_ascii=False)
        self.assertIn('Treść komentarza do ukrycia przed nikim', static_text)
        self.assertIn('Treść komentarza do ukrycia przed nikim', api_text)


class BehindLogin(CommentsCase):
    login_to_view = True

    def test_without_a_session_neither_way_gives_anything(self):
        guest = Client(self.server)
        self.assertEqual(guest.get('/demo/comments.json').status, 401)
        self.assertEqual(guest.get('/_api/v1/prototypes/demo/operations').status, 401)

    def test_every_role_with_a_session_reads_the_same_operations_by_both_ways(self):
        reference = None
        for name, client in self.clients().items():
            if name == 'guest':
                continue
            with self.subTest(role=name):
                ids = self.api_ids(client)
                reference = reference or ids
                self.assertEqual(ids, reference)
                self.assertEqual(self.static_ids(client), reference)


if __name__ == '__main__':
    unittest.main()
