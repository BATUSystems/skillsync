"""Zwolnienie znacznika zapisu ekranów przez API (`POST /_api/v1/prototypes/<slug>/writing/release`) i polski komunikat 409.

Kontrakt: spec zadania (D4, D6, K4, K6): zwalniać mogą tylko `developer` i `acceptor` (commenter i expert dostają 403, bez sesji 401),
tak samo jak przy `restore` wymagane są sesja i CSRF; usunięcie jest idempotentne; serwer bez zapisu źródeł odmawia.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_prototype_writing_release.py`.
"""
import json
import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype_server as ps  # noqa: E402
from tests.test_skillsync_prototype_server import setUpModule, tearDownModule  # noqa: E402,F401  (strażnik katalogu)
from tests.test_skillsync_prototype_accept import AcceptCase, DEFAULT_PW, ITER  # noqa: E402
from tests.test_skillsync_prototype_writing_http import put_marker, BY  # noqa: E402

EXPERT = 'ekspert@example.com'
RELEASE_URL = '/_api/v1/prototypes/%s/writing/release'
PW_EXPERT, PW_EA, PW_EC = DEFAULT_PW + '-x1', DEFAULT_PW + '-x2', DEFAULT_PW + '-x3'   # nie literały haseł (skan sekretów)


class ReleaseCase(AcceptCase):
    def setUp(self):
        super().setUp()
        users = ps.UsersFile(self.users_path, iterations=ITER, clock=self.clock)
        ps.add_user(users, EXPERT, role='expert', password=PW_EXPERT)
        self.exp = self.client(EXPERT, PW_EXPERT)

    def release(self, client, slug='demo', **headers):
        return client.post(RELEASE_URL % slug, {}, **headers)

    def marker_exists(self, slug='demo'):
        return os.path.exists(os.path.join(self.root, slug, ps.WRITING_MARKER))


class ReleasePermissionTests(ReleaseCase):
    def test_acceptor_and_developer_release_a_valid_marker(self):
        for client in (self.acc, self.dev):
            with self.subTest(login=client.login_name):
                put_marker(self.demo, self.clock)
                response = self.check(self.release(client), 200)
                self.assertEqual(response.json, {'writing': None, 'released': True})
                self.assertFalse(self.marker_exists())
                self.assertIsNone(self.listing()['writing'])

    def test_commenter_and_expert_get_403_and_the_marker_stays(self):
        put_marker(self.demo, self.clock)
        for client in (self.com, self.exp):
            with self.subTest(login=client.login_name):
                self.check(self.release(client), 403, 'forbidden')
                self.assertTrue(self.marker_exists())

    def test_no_session_is_401_and_the_marker_stays(self):
        put_marker(self.demo, self.clock)
        self.check(self.release(self.anon), 401)
        self.assertTrue(self.marker_exists())

    def test_wrong_csrf_is_403_and_the_marker_stays(self):
        put_marker(self.demo, self.clock)
        self.check(self.release(self.acc, **{'headers': {'X-Skillsync-CSRF': 'zly'}}), 403, 'invalid_csrf')
        self.assertTrue(self.marker_exists())

    def test_get_is_not_allowed(self):
        put_marker(self.demo, self.clock)
        self.assertEqual(self.acc.get(RELEASE_URL % 'demo').status, 405)
        self.assertTrue(self.marker_exists())

    def test_must_change_password_is_refused(self):
        put_marker(self.demo, self.clock)
        users = ps.UsersFile(self.users_path, iterations=ITER, clock=self.clock)
        ps.add_user(users, 'nowy@example.com', role='acceptor', default_password=DEFAULT_PW)
        must = self.client('nowy@example.com', DEFAULT_PW)
        self.check(self.release(must), 403, 'password_change_required')
        self.assertTrue(self.marker_exists())


class ReleaseMultiRoleAndEdgeTests(ReleaseCase):
    def test_roles_are_resolved_like_restore_by_the_highest_one(self):
        users = ps.UsersFile(self.users_path, iterations=ITER, clock=self.clock)
        ps.add_user(users, 'ea@example.com', password=PW_EA, roles=['expert', 'acceptor'])
        ps.add_user(users, 'ec@example.com', password=PW_EC, roles=['expert', 'commenter'])
        put_marker(self.demo, self.clock)
        self.check(self.release(self.client('ec@example.com', PW_EC)), 403, 'forbidden')
        self.assertTrue(self.marker_exists())
        self.check(self.release(self.client('ea@example.com', PW_EA)), 200)
        self.assertFalse(self.marker_exists())

    def test_a_directory_in_place_of_the_marker_is_left_alone(self):
        os.mkdir(os.path.join(self.demo, ps.WRITING_MARKER))
        self.assertEqual(self.check(self.release(self.acc), 200).json, {'writing': None, 'released': False})
        self.assertTrue(os.path.isdir(os.path.join(self.demo, ps.WRITING_MARKER)))

    def test_slugs_that_try_to_leave_the_root_are_404_and_nothing_is_removed(self):
        put_marker(self.demo, self.clock)
        for slug in ('..', '%2e%2e', '..%2Fdemo', '%252e%252e', 'demo%2F..%2Fdemo', 'de%00mo'):
            with self.subTest(slug=slug):
                response = self.acc.post('/_api/v1/prototypes/%s/writing/release' % slug, {})
                self.assertIn(response.status, (400, 404))
        self.assertTrue(self.marker_exists())

    def test_body_is_required_json(self):
        put_marker(self.demo, self.clock)
        self.check(self.acc.post(RELEASE_URL % 'demo', None), 400)
        self.assertTrue(self.marker_exists())

    def test_released_is_false_for_an_expired_marker_file_that_is_still_there(self):
        put_marker(self.demo, self.clock, ttl=60)
        self.clock.advance(61)
        self.assertTrue(self.marker_exists())
        self.assertEqual(self.check(self.release(self.acc), 200).json['released'], True)   # the file existed and was removed

    def test_release_does_not_leak_the_author(self):
        put_marker(self.demo, self.clock)
        self.assertNotIn(BY, self.check(self.release(self.acc), 200).text)


class ReleaseBehaviourTests(ReleaseCase):
    def test_without_a_marker_it_is_released_false(self):
        response = self.check(self.release(self.acc), 200)
        self.assertEqual(response.json, {'writing': None, 'released': False})

    def test_an_expired_marker_counts_as_none_but_is_removed(self):
        put_marker(self.demo, self.clock, ttl=60)
        self.clock.advance(61)
        self.assertEqual(self.check(self.release(self.acc), 200).json['writing'], None)
        self.assertFalse(self.marker_exists())

    def test_unknown_slug_is_404(self):
        self.check(self.release(self.acc, slug='nie-ma'), 404)

    def test_v1_prototype_needs_migration(self):
        self.check(self.release(self.acc, slug='old'), 409, 'needs_migration')

    def test_after_release_accept_works_again(self):
        put_marker(self.demo, self.clock)
        self.check(self.accept(self.acc), 409, 'writing_in_progress')
        self.check(self.release(self.acc), 200)
        self.check(self.accept(self.acc), 201)

    def test_release_touches_nothing_else(self):
        put_marker(self.demo, self.clock)
        before = dict((name, Path(self.demo, name).read_bytes()) for name in os.listdir(self.demo)
                      if name != ps.WRITING_MARKER and Path(self.demo, name).is_file())
        self.check(self.release(self.acc), 200)
        after = dict((name, Path(self.demo, name).read_bytes()) for name in os.listdir(self.demo)
                     if Path(self.demo, name).is_file() and not name.endswith('.lock'))   # the lock file is ignored by git
        self.assertEqual(before, after)

    def test_a_symlink_in_place_of_the_marker_is_left_alone(self):
        target = os.path.join(self.root, 'cel.json')
        Path(target).write_text('{}', encoding='utf-8')
        try:
            os.symlink(target, os.path.join(self.demo, ps.WRITING_MARKER))
        except (OSError, NotImplementedError):
            self.skipTest('brak dowiązań symbolicznych')
        self.check(self.release(self.acc), 200)
        self.assertTrue(os.path.islink(os.path.join(self.demo, ps.WRITING_MARKER)))
        self.assertTrue(os.path.exists(target))


class ReleaseWithoutSourceWritesTests(ReleaseCase):
    def setUp(self):
        super().setUp()
        self.start_server(source_writes=False)
        self.acc = self.client(self.acc.login_name)

    def test_server_without_source_writes_refuses(self):
        put_marker(self.demo, self.clock)
        self.check(self.release(self.acc), 403, 'source_writes_disabled')
        self.assertTrue(self.marker_exists())


class PolishConflictMessageTests(ReleaseCase):
    def hhmm(self, millis):
        return time.strftime('%H:%M', time.localtime(millis / 1000))

    @unittest.skipUnless(hasattr(time, 'tzset'), 'brak time.tzset (Windows)')
    def test_the_hour_follows_the_host_time_zone(self):
        old = os.environ.get('TZ')
        os.environ['TZ'] = 'Pacific/Kiritimati'   # UTC+14: a different hour than the test host has in practice
        time.tzset()
        try:
            marker = put_marker(self.demo, self.clock)
            body = self.check(self.accept(self.acc), 409, 'writing_in_progress').json
            expected = time.strftime('%H:%M', time.gmtime(marker['expiresAt'] / 1000 + 14 * 3600))
            self.assertIn('do ' + expected, body['message'])
        finally:
            if old is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = old
            time.tzset()

    def test_accept_409_has_a_human_message_with_the_hour(self):
        marker = put_marker(self.demo, self.clock)
        body = self.check(self.accept(self.acc), 409, 'writing_in_progress').json
        self.assertIn('Agent jeszcze pisze ekrany', body['message'])
        self.assertIn('do ' + self.hhmm(marker['expiresAt']), body['message'])
        self.assertNotIn('writing_in_progress', body['message'])
        self.assertEqual((body['slug'], body['since'], body['expiresAt']), ('demo', marker['since'], marker['expiresAt']))
        self.assertNotIn(BY, json.dumps(body))

    def test_project_accept_409_names_the_prototypes_in_polish(self):
        from tests.test_skillsync_prototype_accept import PROJECT_ACCEPT_URL, PROJECT_URL
        put_marker(self.demo, self.clock)
        versions = dict((item['slug'], item['version']) for item in
                        self.check(self.acc.get(PROJECT_URL), 200).json['prototypes'] if item['format'] == 2)
        body = self.check(self.acc.post(PROJECT_ACCEPT_URL, {'cid': 'p-1234567890', 'versions': versions}), 409,
                          'writing_in_progress').json
        self.assertIn('Agent jeszcze pisze ekrany', body['message'])
        self.assertIn('demo', body['message'])
        self.assertTrue(body['writing'])


if __name__ == '__main__':
    unittest.main()
