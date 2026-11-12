"""Konta i role (EXPERT-Q, E1): rola `expert`, pole `roles`, zapis zgodny wstecz, bramki i fakty dla agenta.

Kryteria: .skillsync/runs/EXPERT-Q/spec.md (K1, K2, K8, D16, „Kontrakty → Konta”). Testy obserwują pliki kont,
odpowiedzi HTTP i raporty CLI. Serwer startuje na porcie 0, hasła są atrapami składanymi w czasie wykonania, liczba
iteracji PBKDF2 jest niska. Każdy test negatywny ma przypadek kontrolny, który przechodzi.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_prototype_roles.py`.
"""
import copy
import io
import itertools
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype_server as ps  # noqa: E402
from tests import test_skillsync_prototype_restore as R  # noqa: E402  (moduł, nie klasy: bez dublowania testów)
from tests import test_skillsync_prototype_server as S  # noqa: E402
from tests.test_skillsync_prototype import CLI  # noqa: E402
from tests.test_skillsync_prototype_server import setUpModule, tearDownModule  # noqa: E402,F401  (strażnik katalogu)

EX = 'ewa.ekspert@example.com'        # tylko expert
AE = 'adam.akceptor@example.com'      # acceptor + expert
DE = 'dawid.dev@example.com'          # developer + expert
PW = 'Rola-' + 'Haslo-' + '4821'      # atrapa składana w czasie wykonania
DEFAULT = 'Dom-' + 'Haslo-' + '7710'
HASH = 'pbkdf2-placeholder-not-a-real-hash'


def record(login, **fields):
    base = {'login': login, 'password': HASH, 'mustChange': False}
    base.update(fields)
    return base


# ═════════════════════════════════════════════════════════════════════════════
# 1. Akcesor i walidacja pliku kont (K1)
# ═════════════════════════════════════════════════════════════════════════════

class AccessorAndValidationTests(S.UsersCase):
    def put(self, *records):
        S.write(self.users_path, json.dumps({'version': 1, 'users': list(records)}), 0o600)

    def bytes_on_disk(self):
        with open(self.users_path, 'rb') as handle:
            return handle.read()

    def invalid(self, *records):
        self.put(*records)
        with self.assertRaises(ps.UserError) as caught:
            self.users_file().load()
        self.assertEqual(caught.exception.code, 'invalid_users_file')
        return caught.exception.message

    def test_role_string_and_roles_list_and_both_consistent_load(self):
        self.put(record('a@example.com', role='developer'),
                 record('b@example.com', roles=['expert']),
                 record('c@example.com', role='commenter', roles=['expert']),
                 record('d@example.com', role='developer', roles=['expert', 'developer']))
        users = {item['login']: item for item in self.users_file().load()['users']}
        self.assertEqual(ps.roles_of(users['a@example.com']), ('developer',))
        self.assertEqual(ps.roles_of(users['b@example.com']), ('expert',))
        self.assertEqual(set(ps.roles_of(users['d@example.com'])), {'expert', 'developer'})
        self.assertEqual(ps.primary_role(users['a@example.com']), 'developer')
        self.assertEqual(ps.primary_role(users['b@example.com']), 'expert')
        self.assertEqual(ps.primary_role(users['d@example.com']), 'developer')

    def test_roles_expert_with_role_developer_is_invalid_and_names_login_and_expected_value(self):
        message = self.invalid(record('zly@example.com', role='developer', roles=['expert']))
        self.assertIn('zly@example.com', message)
        self.assertIn('commenter', message, 'oczekiwana wartość pola role')
        # kontrola: ten sam rekord z oczekiwaną wartością ładuje się
        self.put(record('zly@example.com', role='commenter', roles=['expert']))
        self.assertEqual(len(self.users_file().load()['users']), 1)

    def test_role_mismatch_with_a_higher_legacy_role_in_roles_is_invalid(self):
        message = self.invalid(record('x@example.com', role='commenter', roles=['expert', 'acceptor']))
        self.assertIn('x@example.com', message)
        self.assertIn('acceptor', message)

    def test_role_expert_is_rejected_because_the_legacy_set_has_no_expert(self):
        self.invalid(record('e@example.com', role='expert'))
        self.invalid(record('e@example.com', role='expert', roles=['expert']))

    def test_bad_roles_are_rejected(self):
        for roles in ([], ['expert', 'expert'], ['superadmin'], ['expert', 'superadmin'], 'expert', None, [1], [['expert']]):
            with self.subTest(roles=roles):
                self.invalid(record('r@example.com', roles=roles))
        # kontrola: poprawna lista tego samego kształtu ładuje się
        self.put(record('r@example.com', roles=['expert', 'commenter']))
        self.assertEqual(len(self.users_file().load()['users']), 1)

    def test_record_without_role_and_roles_is_rejected_and_unknown_role_too(self):
        self.invalid(record('n@example.com'))
        self.invalid(record('n@example.com', role='admin'))
        self.invalid(record('n@example.com', role=None))

    def test_load_and_accessors_do_not_mutate_file_or_record(self):
        self.put(record('a@example.com', roles=['expert']), record('b@example.com', role='acceptor'),
                 record('c@example.com', role='developer', roles=['expert', 'developer']))
        before = self.bytes_on_disk()
        users = self.users_file()
        loaded = users.load()
        snapshot = copy.deepcopy(loaded)
        for item in loaded['users']:
            ps.roles_of(item)
            ps.primary_role(item)
            ps.has_privileged_role(item)
            ps.public_user(item)
        users.get('a@example.com')
        users.users()
        self.assertEqual(loaded, snapshot, 'akcesory nie zmieniają rekordów w pamięci')
        self.assertEqual(self.bytes_on_disk(), before, 'odczyt nie zapisuje pliku')
        self.assertNotIn('role', loaded['users'][0], 'rekord tylko z roles nie dostał pola role przy odczycie')

    def test_public_user_has_primary_role_and_roles_list(self):
        public = ps.public_user(record('a@example.com', role='commenter', roles=['expert']))
        self.assertEqual((public['role'], public['roles']), ('expert', ['expert']))
        public = ps.public_user(record('b@example.com', role='developer', roles=['expert', 'developer']))
        self.assertEqual(public['role'], 'developer')
        self.assertEqual(set(public['roles']), {'expert', 'developer'})
        public = ps.public_user(record('c@example.com', role='acceptor'))
        self.assertEqual((public['role'], public['roles']), ('acceptor', ['acceptor']))
        self.assertNotIn('password', public)

    def test_primary_role_precedence(self):
        order = ['commenter', 'expert', 'acceptor', 'developer']
        for low, high in itertools.combinations(order, 2):
            with self.subTest(low=low, high=high):
                self.assertEqual(ps.primary_role({'roles': [high, low]}), high)
                self.assertEqual(ps.primary_role({'roles': [low, high]}), high)

    def test_privileged_accessor(self):
        self.assertFalse(ps.has_privileged_role({'roles': ['expert']}))
        self.assertFalse(ps.has_privileged_role({'role': 'commenter'}))
        self.assertTrue(ps.has_privileged_role({'roles': ['expert', 'acceptor']}))
        self.assertTrue(ps.has_privileged_role({'role': 'developer'}))


# ═════════════════════════════════════════════════════════════════════════════
# 2-3. Zapis zgodny wstecz (D16) i CLI kont modułu
# ═════════════════════════════════════════════════════════════════════════════

class WriteCompatibilityTests(S.UsersCase):
    def add(self, login, *roles, password=PW):
        argv = ['user', 'add', login, '--users', self.users_path, '--password-stdin']
        for role in roles:
            argv += ['--role', role]
        return self.cli_json(argv, stdin=password + '\n')

    def rec(self, login):
        return [item for item in self.stored()['users'] if item['login'] == login][0]

    def test_expert_only_is_stored_as_commenter_plus_roles(self):
        self.add('e@example.com', 'expert')
        item = self.rec('e@example.com')
        self.assertEqual((item['role'], item['roles']), ('commenter', ['expert']))

    def test_expert_and_developer_store_developer_and_both_roles_in_a_stable_order(self):
        self.add('a@example.com', 'expert', 'developer')
        self.add('b@example.com', 'expert', 'developer')
        self.add('c@example.com', 'developer', 'expert')
        first = self.rec('a@example.com')
        self.assertEqual(first['role'], 'developer')
        self.assertEqual(set(first['roles']), {'developer', 'expert'})
        self.assertEqual(len(first['roles']), 2)
        self.assertEqual(self.rec('b@example.com')['roles'], first['roles'], 'ta sama kolejność flag daje ten sam zapis')
        self.assertEqual(self.rec('c@example.com')['role'], 'developer')

    def test_acceptor_has_no_roles_key_and_default_is_commenter(self):
        self.add('a@example.com', 'acceptor')
        self.add('n@example.com')
        self.assertEqual(self.rec('a@example.com')['role'], 'acceptor')
        self.assertNotIn('roles', self.rec('a@example.com'))
        self.assertEqual(self.rec('n@example.com')['role'], 'commenter')
        self.assertNotIn('roles', self.rec('n@example.com'))

    def test_every_role_subset_keeps_the_legacy_role_valid_and_never_escalates(self):
        for size in range(1, len(ps.ROLES) + 1):
            for subset in itertools.permutations(ps.ROLES, size):
                fields = ps.role_fields(list(subset))
                with self.subTest(subset=subset):
                    self.assertIn(fields['role'], ps.LEGACY_ROLES)
                    self.assertEqual(fields['role'] in ps.PRIVILEGED_ROLES,
                                     any(role in ps.PRIVILEGED_ROLES for role in subset))
                    self.assertEqual('roles' in fields, 'expert' in subset or len(subset) > 1)

    def test_old_validator_view_ignores_roles_and_does_not_escalate(self):
        """Symulacja starszego rdzenia: pole `roles` znika, zostaje `role` ze starego zbioru."""
        cases = {'e@example.com': (['expert'], 'commenter'), 'f@example.com': (['expert', 'developer'], 'developer'),
                 'g@example.com': (['expert', 'acceptor'], 'acceptor'), 'h@example.com': (['expert', 'commenter'], 'commenter')}
        for login, (roles, _expected) in cases.items():
            self.add(login, *roles)
        data = self.stored()
        for item in data['users']:
            item.pop('roles', None)
        S.write(self.users_path, json.dumps(data), 0o600)
        loaded = {item['login']: item for item in self.users_file().load()['users']}
        for login, (_roles, expected) in cases.items():
            with self.subTest(login=login):
                self.assertEqual(loaded[login]['role'], expected)
                self.assertIn(loaded[login]['role'], ps.LEGACY_ROLES)
                self.assertEqual(ps.primary_role(loaded[login]), expected)
        self.assertFalse(ps.has_privileged_role(loaded['e@example.com']), 'expert-only widziany jak komentujący')

    def test_password_change_of_one_account_leaves_other_records_byte_for_byte(self):
        self.add('a@example.com', 'commenter')
        self.add('b@example.com', 'expert', 'developer')
        self.add('c@example.com', 'expert')
        self.add('d@example.com', 'acceptor')
        before = {item['login']: json.dumps(item, sort_keys=True) for item in self.stored()['users']}
        self.cli_json(['user', 'passwd', 'a@example.com', '--users', self.users_path, '--password-stdin'],
                      stdin='Nowe-' + 'Haslo-' + '3355\n')
        after = {item['login']: json.dumps(item, sort_keys=True) for item in self.stored()['users']}
        for login in ('b@example.com', 'c@example.com', 'd@example.com'):
            with self.subTest(login=login):
                self.assertEqual(after[login], before[login])
        self.assertNotEqual(after['a@example.com'], before['a@example.com'], 'kontrola: konto A faktycznie się zmieniło')

    def test_password_change_does_not_migrate_a_legacy_record(self):
        self.add('a@example.com', 'commenter')
        self.add('b@example.com', 'developer')
        self.cli_json(['user', 'passwd', 'a@example.com', '--users', self.users_path, '--password-stdin'],
                      stdin='Nowe-' + 'Haslo-' + '3356\n')
        self.assertNotIn('roles', self.rec('b@example.com'))
        self.assertNotIn('roles', self.rec('a@example.com'))

    def test_argparse_rejects_unknown_role(self):
        code, _out, _err = self.cli(['user', 'add', 'a@example.com', '--users', self.users_path, '--password-stdin',
                                     '--role', 'superadmin'], stdin=PW + '\n')
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(self.users_path))
        self.add('a@example.com', 'expert')  # kontrola


class UserRolesCommandTests(S.UsersCase):
    def setUp(self):
        super().setUp()
        for login, roles in (('a@example.com', ['commenter']), ('b@example.com', ['expert', 'developer']),
                             ('c@example.com', ['expert']), ('d@example.com', ['acceptor'])):
            argv = ['user', 'add', login, '--users', self.users_path, '--password-stdin']
            for role in roles:
                argv += ['--role', role]
            self.cli_json(argv, stdin=PW + '\n')

    def rec(self, login):
        return [item for item in self.stored()['users'] if item['login'] == login][0]

    def roles(self, login, *roles, expect=0):
        argv = ['user', 'roles', login, '--users', self.users_path]
        for role in roles:
            argv += ['--role', role]
        return self.cli(argv)

    def test_expert_developer_to_developer_removes_roles(self):
        code, out, err = self.roles('b@example.com', 'developer')
        self.assertEqual(code, 0, out + err)
        item = self.rec('b@example.com')
        self.assertEqual(item['role'], 'developer')
        self.assertNotIn('roles', item)
        self.assertEqual(json.loads(out)['user']['role'], 'developer')

    def test_expert_to_acceptor_replaces_the_set(self):
        code, out, err = self.roles('c@example.com', 'acceptor')
        self.assertEqual(code, 0, out + err)
        item = self.rec('c@example.com')
        self.assertEqual(item['role'], 'acceptor')
        self.assertNotIn('roles', item)

    def test_commenter_to_expert_and_acceptor_adds_roles(self):
        code, out, err = self.roles('a@example.com', 'expert', 'acceptor')
        self.assertEqual(code, 0, out + err)
        item = self.rec('a@example.com')
        self.assertEqual(item['role'], 'acceptor')
        self.assertEqual(set(item['roles']), {'expert', 'acceptor'})

    def test_roles_command_does_not_touch_other_records_or_the_password(self):
        before = {item['login']: json.dumps(item, sort_keys=True) for item in self.stored()['users']}
        self.assertEqual(self.roles('c@example.com', 'expert', 'developer')[0], 0)
        after = {item['login']: json.dumps(item, sort_keys=True) for item in self.stored()['users']}
        for login in ('a@example.com', 'b@example.com', 'd@example.com'):
            self.assertEqual(after[login], before[login])
        changed, original = json.loads(after['c@example.com']), json.loads(before['c@example.com'])
        self.assertEqual(changed['password'], original['password'])
        self.assertNotEqual(after['c@example.com'], before['c@example.com'], 'kontrola: konto C się zmieniło')

    def test_at_least_one_role_is_required(self):
        before = self.stored()
        code, out, err = self.roles('c@example.com')
        self.assertEqual(code, 2, out + err)
        self.assertEqual(json.loads(out)['error'], 'invalid_role')
        self.assertEqual(self.stored(), before)
        self.assertEqual(self.roles('c@example.com', 'expert')[0], 0)  # kontrola

    def test_unknown_account_is_an_error_and_writes_nothing(self):
        before = self.stored()
        code, out, err = self.roles('nikt@example.com', 'expert')
        self.assertEqual(code, 2, out + err)
        self.assertEqual(json.loads(out)['status'], 'BLOCKED')
        self.assertEqual(self.stored(), before)

    def test_unknown_role_is_rejected_by_the_parser(self):
        before = self.stored()
        code, _out, _err = self.roles('c@example.com', 'superadmin')
        self.assertEqual(code, 2)
        self.assertEqual(self.stored(), before)

    def test_missing_users_file_is_blocked(self):
        os.unlink(self.users_path)
        code, out, err = self.roles('c@example.com', 'expert')
        self.assertEqual(code, 2, out + err)


# ═════════════════════════════════════════════════════════════════════════════
# 6. Ostrzeżenia o haśle domyślnym (K2)
# ═════════════════════════════════════════════════════════════════════════════

class WarningTests(S.UsersCase):
    def test_add_expert_with_default_password_warns_and_with_own_password_does_not(self):
        code, out, err = self.cli(['user', 'add', 'e@example.com', '--users', self.users_path, '--role', 'expert',
                                   '--default-password-stdin'], stdin=DEFAULT + '\n')
        self.assertEqual(code, 0, out + err)
        self.assertIn('e@example.com', err)
        self.assertIn('ostrzeżenie', err)
        self.assertNotIn(DEFAULT, out + err)
        code, out, err = self.cli(['user', 'add', 'f@example.com', '--users', self.users_path, '--role', 'expert',
                                   '--password-stdin'], stdin=PW + '\n')
        self.assertEqual(code, 0, out + err)
        self.assertNotIn('ostrzeżenie', err, 'kontrola: własne hasło nie wywołuje ostrzeżenia')

    def test_commenter_default_password_does_not_warn(self):
        code, out, err = self.cli(['user', 'add', 'c@example.com', '--users', self.users_path,
                                   '--default-password-stdin'], stdin=DEFAULT + '\n')
        self.assertEqual(code, 0, out + err)
        self.assertNotIn('ostrzeżenie', err)

    def test_passwd_reset_of_an_expert_warns(self):
        self.cli_json(['user', 'add', 'e@example.com', '--users', self.users_path, '--role', 'expert',
                       '--password-stdin'], stdin=PW + '\n')
        code, out, err = self.cli(['user', 'passwd', 'e@example.com', '--users', self.users_path,
                                   '--default-password-stdin'], stdin=DEFAULT + '\n')
        self.assertEqual(code, 0, out + err)
        self.assertIn('e@example.com', err)

    def test_promoting_an_account_with_default_password_warns(self):
        self.cli_json(['user', 'add', 'g@example.com', '--users', self.users_path, '--default-password-stdin'],
                      stdin=DEFAULT + '\n')
        code, out, err = self.cli(['user', 'roles', 'g@example.com', '--users', self.users_path, '--role', 'expert'])
        self.assertEqual(code, 0, out + err)
        self.assertIn('g@example.com', err)
        self.cli_json(['user', 'add', 'h@example.com', '--users', self.users_path, '--password-stdin'],
                      stdin=PW + '\n')
        code, out, err = self.cli(['user', 'roles', 'h@example.com', '--users', self.users_path, '--role', 'expert'])
        self.assertEqual(code, 0, out + err)
        self.assertNotIn('ostrzeżenie', err, 'kontrola: konto z własnym hasłem nie wywołuje ostrzeżenia')

    def test_import_of_experts_warns_for_every_added_account(self):
        lista = os.path.join(self.tmp, 'lista.txt')
        S.write(lista, 'i1@example.com\ni2@example.com\n')
        code, out, err = self.cli(['user', 'import', lista, '--users', self.users_path, '--role', 'expert',
                                   '--default-password-stdin'], stdin=DEFAULT + '\n')
        self.assertEqual(code, 0, out + err)
        self.assertIn('i1@example.com', err)
        self.assertIn('i2@example.com', err)
        stored = {item['login']: item for item in self.stored()['users']}
        self.assertEqual((stored['i1@example.com']['role'], stored['i1@example.com']['roles']), ('commenter', ['expert']))


class StartupWarningTests(S.ServerCase):
    def test_expert_with_default_password_is_listed_like_privileged_accounts(self):
        users = ps.UsersFile(self.users_path, iterations=S.ITER)
        ps.add_user(users, 'ekspert.domyslny@example.com', roles=['expert'], default_password=S.DEFAULT_PW)
        ps.add_user(users, 'ekspert.wlasny@example.com', roles=['expert'], password=PW)
        _e, warnings = ps.startup_checks(self.config())
        joined = '\n'.join(warnings)
        self.assertIn('ekspert.domyslny@example.com', joined)
        self.assertNotIn('ekspert.wlasny@example.com', joined, 'kontrola: ekspert z własnym hasłem nie jest wymieniony')
        self.assertNotIn(S.MUST, joined, 'komentujący z hasłem domyślnym nadal nie jest wymieniany')
        self.assertNotIn(S.DEFAULT_PW, joined)


# ═════════════════════════════════════════════════════════════════════════════
# 4-5. Sesja, API i bramki serwera
# ═════════════════════════════════════════════════════════════════════════════

def add_expert_accounts(users_path, clock=None):
    users = ps.UsersFile(users_path, iterations=S.ITER, clock=clock)
    ps.add_user(users, EX, roles=['expert'], password=PW)
    ps.add_user(users, AE, roles=['expert', 'acceptor'], password=PW)
    ps.add_user(users, DE, roles=['expert', 'developer'], password=PW)


class SessionTests(S.ServerCase):
    def setUp(self):
        super().setUp()
        add_expert_accounts(self.users_path, self.clock)

    def test_login_and_session_return_primary_role_and_roles(self):
        expected = {EX: ('expert', ['expert']), AE: ('acceptor', {'expert', 'acceptor'}),
                    DE: ('developer', {'expert', 'developer'}), S.A: ('commenter', ['commenter']),
                    S.D: ('developer', ['developer'])}
        for login, (role, roles) in expected.items():
            with self.subTest(login=login):
                client = S.Client(self.server)
                password = PW if login in (EX, AE, DE) else S.PASSWORDS[login]
                response = self.check(client.login(login, password), 200)
                user = response.json['user']
                self.assertEqual(user['role'], role)
                self.assertEqual(set(user['roles']), set(roles))
                self.assertEqual(len(user['roles']), len(set(roles)))
                session = self.check(client.get('/_api/v1/session'), 200).json['user']
                self.assertEqual((session['role'], set(session['roles'])), (role, set(roles)))
                self.assertNotIn('password', response.text)
                self.assertNotIn('pbkdf2', response.text)

    def test_session_user_keys(self):
        client = S.Client(self.server)
        self.check(client.login(EX, PW), 200)
        user = self.check(client.get('/_api/v1/session'), 200).json['user']
        self.assertEqual(set(user), {'login', 'name', 'firstName', 'role', 'roles', 'mustChange'})


def Client_login(case, login):
    client = S.Client(case.server)
    case.check(client.login(login, PW), 200)
    return client


class ProjectAndRestoreGateTests(S.RestoreServerCase):
    def setUp(self):
        super().setUp()
        add_expert_accounts(self.users_path, self.clock)
        self.ex, self.ae, self.de = (Client_login(self, login) for login in (EX, AE, DE))

    def accept_body(self, screen='s2'):
        listing = self.anon.operations('demo').json
        return {'scope': 'screen', 'screens': {screen: listing['screens'][screen]}, 'version': listing['version']}

    def test_expert_only_cannot_read_project_or_accept(self):
        before = self.journal()
        self.check(self.ex.get('/_api/v1/project'), 403, 'forbidden')
        self.check(self.ex.op('demo', 'accept', None, self.accept_body()), 403, 'forbidden')
        self.assertEqual(self.journal(), before, 'odmowa nie zapisuje')
        # kontrola: acceptor+expert i developer+expert przechodzą te same żądania
        self.check(self.ae.get('/_api/v1/project'), 200)
        self.check(self.de.get('/_api/v1/project'), 200)
        self.check(self.ae.op('demo', 'accept', None, self.accept_body()), 201)

    def test_developer_expert_accepts_like_a_developer(self):
        self.check(self.de.op('demo', 'accept', None, self.accept_body('s3')), 201)

    def test_expert_only_cannot_restore_and_nothing_is_written(self):
        before = self.files()
        self.check(self.restore(self.ex), 403, 'forbidden')
        self.check(self.preview(self.ex), 403, 'forbidden')
        self.assert_untouched(before)

    def test_acceptor_expert_and_developer_expert_restore(self):
        self.check(self.restore(self.ae), 201)
        self.check(self.restore(self.de, **{'from': 2}), 201)

    def test_expert_only_cannot_delete_a_thread(self):
        self.seed(self.client(S.A), 'th-1', 'm-1')
        before = self.journal()
        self.check(self.ex.op('demo', 'delete', 'th-1', {}), 403)
        self.assertEqual(self.journal(), before)
        self.check(self.de.op('demo', 'delete', 'th-1', {}), 201)  # kontrola: programista+ekspert usuwa jak programista


# ═════════════════════════════════════════════════════════════════════════════
# 5, 7. CLI: bramki restore/accept/decide i fakty dla agenta (K8)
# ═════════════════════════════════════════════════════════════════════════════

class CliRolesCase(R.RestoreFixture):
    def setUp(self):
        super().setUp()
        users = ps.UsersFile(str(self.root / '.skillsync' / 'prototype' / 'users.json'), iterations=1000)
        ps.add_user(users, EX, roles=['expert'], password=PW)
        ps.add_user(users, AE, roles=['expert', 'acceptor'], password=PW)
        ps.add_user(users, DE, roles=['expert', 'developer'], password=PW)
        self.thread('th-x', 'm-x', 's2', 's2-btn-a', by=R.COMMENTER)

    def prepare(self):
        self.iterate()
        self.write_page(R.page(R.S2_V2, R.S3_V2))

    def run_wait(self, *args, timeout='0'):
        argv = [sys.executable, str(CLI), 'prototype', 'wait', str(self.root), 'demo', '--timeout', timeout,
                '--interval', '0.2', '--json', *args]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60, cwd=self.base)
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        return result.returncode, json.loads(result.stdout)

    def comment_by(self, who, thread_id, text='uwaga'):
        self.store().append([{'type': 'create', 'threadId': thread_id, 'by': who,
                              'payload': {'message': {'id': 'm-' + thread_id, 'text': text}, 'screen': 's3',
                                          'anchor': {'kind': 'screen', 'x': 0.1, 'y': 0.1, 'iteration': 1}}}])

    def waited(self, who, thread_id):
        last = self.ops()[-1]['id']
        self.comment_by(who, thread_id)
        code, report = self.run_wait('--after', last)
        self.assertEqual((code, report['event']), (0, 'comment'), report)
        return report['comment']


class CliGateTests(CliRolesCase):
    def test_restore_who_expert_only_is_refused_and_writes_nothing(self):
        self.prepare()
        before = self.snapshot()
        report = self.restore(who=EX, code=2)
        self.assertIn('forbidden', json.dumps(report))
        self.assertEqual(self.snapshot(), before)
        self.restore(who=AE)  # kontrola: acceptor+expert wykonuje to samo polecenie

    def test_restore_who_developer_expert_works_like_a_developer(self):
        self.prepare()
        self.restore(who=DE)
        self.assertEqual(self.ops('restore')[-1]['payload']['who'], DE)

    def test_decide_who_expert_only_is_refused_and_expert_with_developer_is_accepted(self):
        before = self.snapshot()
        report = self.proto('decide', 'demo', 'th-x', '--kind', 'developer', '--who', EX, '--outcome', 'apply',
                            '--text', 'tak', code=2)
        self.assertIn('forbidden', json.dumps(report))
        self.assertEqual(self.snapshot(), before)
        self.proto('decide', 'demo', 'th-x', '--kind', 'developer', '--who', DE, '--outcome', 'apply', '--text', 'tak')
        decision = self.ops('decide')[-1]['payload'] if self.ops('decide') else None
        self.assertEqual(self.store().snapshot().fold['byId']['th-x']['decisions'][-1]['who'], DE, decision)
        self.proto('decide', 'demo', 'th-x', '--kind', 'developer', '--who', AE, '--outcome', 'keep', '--text', 'nie')

    def on_tty(self, who, password):
        """`accept --all --by <who>` w procesie z terminalem sterującym; hasło wpisane po pytaniu."""
        import fcntl
        import pty
        import select
        import termios
        import time
        argv = [sys.executable, str(CLI), 'prototype', 'accept', str(self.root), 'demo', '--all', '--by', who, '--json']
        master, slave = pty.openpty()
        process = subprocess.Popen(argv, stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   cwd=self.base, start_new_session=True,
                                   preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
        os.close(slave)
        seen, sent = b'', False
        deadline = time.monotonic() + 40
        try:
            while process.poll() is None and time.monotonic() < deadline:
                if not select.select([master], [], [], 0.1)[0]:
                    continue
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                seen += chunk
                if not sent and b'Has' in seen and seen.rstrip().endswith(b':'):
                    os.write(master, password.encode('utf-8') + b'\n')
                    sent = True
            stdout, stderr = process.communicate(timeout=30)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
            os.close(master)
        self.assertNotIn('Traceback', stderr, stderr)
        self.assertNotIn(PW, stdout + stderr)
        return process.returncode, json.loads(stdout)

    @unittest.skipUnless(os.name != 'nt', 'the TTY test needs a POSIX pseudo-terminal')
    def test_accept_by_expert_only_is_forbidden_and_experts_with_privileges_accept(self):
        code, report = self.on_tty(EX, PW)
        self.assertEqual((code, report.get('error')), (2, 'forbidden'), report)
        self.assertEqual(self.ops('accept'), [], 'odmowa nic nie zapisuje')
        for who in (AE, DE):
            with self.subTest(who=who):
                code, report = self.on_tty(who, PW)  # kontrola: to samo polecenie dla konta z uprawnieniem
                self.assertEqual(code, 0, report)
                self.assertEqual(self.ops('accept')[-1]['payload']['who'], who)


class CliAgentFactsTests(CliRolesCase):
    def test_wait_reports_author_role_string_and_authorroles_list(self):
        c = self.waited(EX, 'th-e')
        self.assertEqual((c['authorRole'], c['authorRoles']), ('expert', ['expert']))
        c = self.waited(DE, 'th-de')
        self.assertEqual(c['authorRole'], 'developer')
        self.assertIsInstance(c['authorRole'], str)
        self.assertEqual(set(c['authorRoles']), {'expert', 'developer'})
        c = self.waited(R.COMMENTER, 'th-k')
        self.assertEqual((c['authorRole'], c['authorRoles']), ('commenter', ['commenter']))
        c = self.waited(R.DEVELOPER, 'th-d')
        self.assertEqual((c['authorRole'], c['authorRoles']), ('developer', ['developer']))

    def test_reaction_expert_only_is_reply_and_expert_with_developer_is_like_a_developer(self):
        self.assertEqual(self.waited(EX, 'th-e')['reaction'], 'reply')
        self.assertEqual(self.waited(DE, 'th-de')['reaction'], 'order?')
        self.assertEqual(self.waited(AE, 'th-ae')['reaction'], 'order?')
        self.assertEqual(self.waited(R.DEVELOPER, 'th-d')['reaction'], 'order?')  # kontrola: reguła dla zwykłego programisty
        self.assertEqual(self.waited(R.COMMENTER, 'th-k')['reaction'], 'reply')

    def test_decision_by_expert_with_developer_counts_as_human_and_its_roles_are_listed(self):
        self.proto('decide', 'demo', 'th-x', '--kind', 'developer', '--who', DE, '--outcome', 'apply', '--text', 'tak')
        self.store().append([{'type': 'reply', 'threadId': 'th-x', 'by': R.COMMENTER,
                              'payload': {'message': {'id': 'm-x2', 'text': 'dzięki'}}}])
        last = self.ops()[-2]['id']
        code, report = self.run_wait('--after', last)
        c = report['comment']
        self.assertEqual((c['reaction'], c['decision']['whoRole']), ('implement', 'developer'), report)
        self.assertEqual(set(c['decision']['whoRoles']), {'expert', 'developer'})

    def test_new_operations_have_no_role_fields_and_journal_format_is_unchanged(self):
        self.comment_by(EX, 'th-e')
        self.proto('decide', 'demo', 'th-x', '--kind', 'developer', '--who', DE, '--outcome', 'apply', '--text', 'tak')
        text = (self.dir / 'comments.json').read_text(encoding='utf-8')
        self.assertNotIn('authorRole', text)
        self.assertNotIn('whoRole', text)
        self.assertNotIn('"roles"', text)
        data = json.loads(text)
        self.assertEqual((data['format'], data['version']), ('skillsync-prototype-comments', 2))

    def test_comments_command_is_read_only_with_expert_accounts(self):
        self.comment_by(EX, 'th-e')
        before = (self.dir / 'comments.json').read_bytes()
        report = self.proto('comments', 'demo')
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual((self.dir / 'comments.json').read_bytes(), before)


class CliUserRolesTests(R.RestoreFixture):
    """`prototype user add/roles` w projekcie (CLI główne)."""

    def setUp(self):
        super().setUp()
        self.config = self.base / 'machine.yaml'
        self.config.write_text('prototype:\n  default_password: %s\n' % DEFAULT, encoding='utf-8')
        os.chmod(self.config, 0o600)
        self.users = self.root / '.skillsync' / 'prototype' / 'users.json'

    def user(self, *args, code=0, stdin=None):
        environ = dict(os.environ, SKILLSYNC_CONFIG=str(self.config))
        argv = [sys.executable, str(CLI), 'prototype', 'user', args[0], str(self.root), *args[1:], '--json']
        result = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=60, cwd=self.base, env=environ)
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        self.assertNotIn(DEFAULT, result.stdout + result.stderr)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def stored(self, login):
        data = json.loads(self.users.read_text(encoding='utf-8'))
        return [item for item in data['users'] if item['login'] == login][0]

    def test_add_expert_default_password_warns_and_stores_commenter_plus_roles(self):
        report = self.user('add', 'nowy.ekspert@example.com', '--role', 'expert')
        self.assertTrue(any('nowy.ekspert@example.com' in warning for warning in report.get('warnings', [])), report)
        item = self.stored('nowy.ekspert@example.com')
        self.assertEqual((item['role'], item['roles']), ('commenter', ['expert']))
        self.assertEqual((report['user']['role'], report['user']['roles']), ('expert', ['expert']))
        self.assertNotIn('password', report['user'])

    def test_add_without_role_is_commenter_and_repeated_role_stores_both(self):
        self.user('add', 'k@example.com')
        self.assertEqual(self.stored('k@example.com')['role'], 'commenter')
        self.user('add', 'm@example.com', '--role', 'expert', '--role', 'developer')
        item = self.stored('m@example.com')
        self.assertEqual(item['role'], 'developer')
        self.assertEqual(set(item['roles']), {'expert', 'developer'})

    def test_roles_command_reports_and_validates(self):
        self.user('add', 'a@example.com', '--role', 'expert', '--role', 'developer')
        report = self.user('roles', 'a@example.com', '--role', 'acceptor')
        self.assertEqual((report['status'], report['action']), ('PASS', 'roles'))
        item = self.stored('a@example.com')
        self.assertEqual(item['role'], 'acceptor')
        self.assertNotIn('roles', item)
        self.user('roles', 'a@example.com', code=2)                       # brak --role
        self.user('roles', 'nikt@example.com', '--role', 'expert', code=2)  # nieznane konto
        self.assertEqual(self.stored('a@example.com')['role'], 'acceptor')

    def test_import_expert_stores_roles_and_warns(self):
        self.write('lista.txt', 'ekspert1@example.com\n')
        report = self.user('import', 'lista.txt', '--role', 'expert')
        self.assertEqual(report['added'], ['ekspert1@example.com'])
        self.assertEqual(self.stored('ekspert1@example.com')['roles'], ['expert'])
        self.assertTrue(report.get('warnings'), report)

    def test_list_shows_primary_role_and_roles(self):
        self.user('add', 'a@example.com', '--role', 'expert')
        listed = {item['login']: item for item in self.user('list')['users']}
        self.assertEqual((listed['a@example.com']['role'], listed['a@example.com']['roles']), ('expert', ['expert']))


if __name__ == '__main__':
    unittest.main()
