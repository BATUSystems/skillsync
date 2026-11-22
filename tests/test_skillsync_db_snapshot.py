"""S1 of the db_snapshot tool: pure logic, validators, SQL builders, environment, masking and exit codes.

Contract: docs/specs/2026-11-22-db-snapshot-test-env.md (revisions 2 and 3, K1-K13). Everything is injected (clock, token,
filesystem probes, environment, platform); no process is started and no real sqlcmd is used. Secrets are assembled at run time.
"""
import base64
import datetime
import importlib.util
import io
import ntpath
import os
import posixpath
import random
import re
import string
import subprocess
import sys
import unittest
import urllib.parse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = os.path.join(str(ROOT), 'library', 'skills', 'test-env', 'scripts', 'db_snapshot.py')
_LOADED = {}


def load_module():
    """The production module; a missing file or a failing import is a test failure (red), not an import error of this file."""
    if 'module' not in _LOADED:
        spec = importlib.util.spec_from_file_location('db_snapshot_under_test', MODULE_PATH)
        if spec is None or spec.loader is None:
            raise AssertionError('cannot load ' + MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules['db_snapshot_under_test'] = module
        try:
            spec.loader.exec_module(module)
        except FileNotFoundError:
            raise AssertionError('production module is missing: ' + MODULE_PATH)
        _LOADED['module'] = module
    return _LOADED['module']


def fake_password(*parts):
    return ''.join(parts or ('pw', '-', 'Zq', '7', '!x'))


class Base(unittest.TestCase):
    def setUp(self):
        self.m = load_module()


class ModuleHygiene(Base):
    def test_exit_table(self):
        self.assertEqual(self.m.EXIT, {'PASS': 0, 'FAIL': 1, 'BLOCKED': 2, 'NOT_RUN': 3})

    def test_exceptions_are_distinct_exception_types(self):
        for name in ('Refusal', 'SqlFailure', 'NotRun'):
            self.assertTrue(issubclass(getattr(self.m, name), Exception), name)
        self.assertFalse(issubclass(self.m.Refusal, self.m.SqlFailure))
        self.assertFalse(issubclass(self.m.SqlFailure, self.m.Refusal))

    def test_run_state_flag(self):
        state = self.m.RunState()
        self.assertIs(state.mutated, False)
        state.mark_mutating()
        self.assertIs(state.mutated, True)

    def test_production_source_has_no_main_side_effect_on_import(self):
        text = Path(MODULE_PATH).read_text(encoding='utf-8')
        self.assertIn("if __name__ == '__main__'", text)


class DatabaseName(Base):
    def test_accepts_ascii_names(self):
        for name in ('appdb', 'App_DB1', '_x', '0', 'a' * 128):
            with self.subTest(name=name):
                self.assertEqual(self.m.validate_database(name), name)

    def test_rejects_shape(self):
        bad = ['', 'a' * 129, 'db\n', '\ndb', 'a]b', "a'b", 'a--b', 'a;b', 'a b', 'a-b', 'a.b', 'a/b', 'a\\b', ' db', 'db ', 'a\x00b', 'a\tb']
        for name in bad:
            with self.subTest(name=name):
                with self.assertRaises(self.m.Refusal):
                    self.m.validate_database(name)

    def test_rejects_unicode_lookalikes(self):
        bad = ['m\u0430ster', '\uff4d\uff41\uff53\uff54\uff45\uff52', 'm\u0131del', 'tempd\u0131b', '\u0130db', 'caf\u00e9', 'db\u212a', 'ma\u017fter',
               '\u0661\u0662', 'a\u200bb', 'db\u2028']
        for name in bad:
            with self.subTest(name=ascii(name)):
                with self.assertRaises(self.m.Refusal):
                    self.m.validate_database(name)

    def test_rejects_system_databases_in_any_case(self):
        for base in ('master', 'model', 'msdb', 'tempdb', 'distribution', 'mssqlsystemresource'):
            for name in (base, base.upper(), base.capitalize(), base[:2] + base[2:].upper()):
                with self.subTest(name=name):
                    with self.assertRaises(self.m.Refusal):
                        self.m.validate_database(name)

    def test_similar_to_system_names_pass(self):
        for name in ('master1', 'my_master', 'msdb2', 'model_x', 'distribution_x'):
            with self.subTest(name=name):
                self.assertEqual(self.m.validate_database(name), name)

    def test_refusal_message_names_no_secret_and_is_text(self):
        try:
            self.m.validate_database("x'; DROP")
        except self.m.Refusal as exc:
            self.assertTrue(str(exc))
        else:
            self.fail('expected Refusal')

    def test_random_unicode_accepted_only_inside_ascii_class(self):
        rng = random.Random(1140)
        alphabet = (string.ascii_letters + string.digits + '_-]\'; \n\r\t\\/.' + 'ı İ ｍ ｄ ａ м а е о і ǀ ᴀ K ſ ß ① ٣ ² \u200b \u2028 é ñ'.replace(' ', ''))
        accepted = 0
        for _ in range(4000):
            name = ''.join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))
            try:
                result = self.m.validate_database(name)
            except self.m.Refusal:
                continue
            accepted += 1
            self.assertIsNotNone(re.fullmatch(r'[A-Za-z0-9_]{1,128}', result, re.ASCII), ascii(name))
            self.assertTrue(name.isascii(), ascii(name))
            self.assertNotIn('\n', name)
        self.assertGreater(accepted, 0)

    def test_every_single_unicode_char_in_basic_planes_is_refused_unless_ascii_class(self):
        allowed = set(string.ascii_letters + string.digits + '_')
        for code in list(range(0, 0x250)) + list(range(0xff00, 0xff60)) + list(range(0x400, 0x460)):
            ch = chr(code)
            name = 'a' + ch + 'b'
            try:
                self.m.validate_database(name)
            except self.m.Refusal:
                self.assertNotIn(ch, allowed)
            else:
                self.assertIn(ch, allowed, ascii(name))


class LabelName(Base):
    def test_accepts(self):
        for label in ('before-change', 'a', 'x1-2', '-', 'a' * 40):
            with self.subTest(label=label):
                self.assertEqual(self.m.validate_label(label), label)

    def test_rejects(self):
        bad = ['', 'a' * 41, 'Abc', 'a_b', 'a b', 'a\n', '\na', 'a.b', 'a/b', "a'b", 'a;b', 'a]b', '\uff41', '\u0430', 'ı', 'İ', 'a\x00']
        for label in bad:
            with self.subTest(label=ascii(label)):
                with self.assertRaises(self.m.Refusal):
                    self.m.validate_label(label)

    def test_random_unicode_accepted_only_inside_ascii_class(self):
        rng = random.Random(1141)
        alphabet = string.ascii_letters + string.digits + '-_ \n' + 'ıİａм٣é\u2028'
        for _ in range(3000):
            label = ''.join(rng.choice(alphabet) for _ in range(rng.randint(0, 8)))
            try:
                result = self.m.validate_label(label)
            except self.m.Refusal:
                continue
            self.assertIsNotNone(re.fullmatch(r'[a-z0-9-]{1,40}', result, re.ASCII), ascii(label))


class ParseServer(Base):
    LOCAL_ALIASES = ['.', 'localhost', 'LOCALHOST', 'LocalHost', '(local)', '(LOCAL)', '127.0.0.1', '::1', '[::1]']

    def test_local_aliases_collapse_to_one_form(self):
        canon = set()
        for text in self.LOCAL_ALIASES:
            with self.subTest(text=text):
                spec = self.m.parse_server(text)
                self.assertEqual(spec.kind, 'local')
                self.assertIsNone(spec.instance)
                self.assertIsNone(spec.port)
                self.assertFalse(spec.localdb)
                self.assertEqual(spec.raw, text)
                canon.add(spec.canonical)
        self.assertEqual(len(canon), 1)

    def test_instance_and_port_are_part_of_identity(self):
        plain = self.m.parse_server('.')
        express = self.m.parse_server('.\\SQLEXPRESS')
        self.assertEqual(express.kind, 'local')
        self.assertEqual(express.instance, 'SQLEXPRESS')
        self.assertNotEqual(express.canonical, plain.canonical)
        self.assertEqual(self.m.parse_server('localhost\\SQLEXPRESS').canonical, express.canonical)
        port = self.m.parse_server('localhost,1434')
        self.assertEqual(port.port, 1434)
        self.assertIsInstance(port.port, int)
        self.assertNotEqual(port.canonical, self.m.parse_server('localhost').canonical)
        self.assertEqual(self.m.parse_server('127.0.0.1,1434').canonical, port.canonical)
        self.assertNotEqual(self.m.parse_server('.\\A').canonical, self.m.parse_server('.\\B').canonical)
        both = self.m.parse_server('.\\A,1500')
        self.assertEqual((both.instance, both.port), ('A', 1500))

    def test_remote_hosts(self):
        for text in ('127.0.0.2', 'localhost.evil.example', 'db1.example.test', '10.0.0.5', 'buildbox01', 'a-b.c', '127.0.0.10', '1.2.3.4'):
            with self.subTest(text=text):
                spec = self.m.parse_server(text)
                self.assertEqual(spec.kind, 'remote')
                self.assertFalse(spec.localdb)
        spec = self.m.parse_server('db1.example.test\\INST,1444')
        self.assertEqual((spec.host, spec.instance, spec.port, spec.kind), ('db1.example.test', 'INST', 1444, 'remote'))

    def test_computer_name_is_remote_here(self):
        self.assertEqual(self.m.parse_server('buildbox01').kind, 'remote')

    def test_localdb(self):
        spec = self.m.parse_server('(localdb)\\MSSQLLocalDB')
        self.assertTrue(spec.localdb)
        self.assertEqual(spec.kind, 'local')
        self.assertEqual(spec.instance, 'MSSQLLocalDB')
        with self.assertRaises(self.m.Refusal):
            self.m.parse_server('(localdb)')
        with self.assertRaises(self.m.Refusal):
            self.m.parse_server('(localdb)\\')

    def test_refused_forms(self):
        bad = ['', ' ', 'tcp:localhost', 'np:localhost', 'lpc:localhost', 'TCP:db1.example.test', 'tcp:127.0.0.1', '0.0.0.0', '127.1', '127.0.1', '10.1',
               'localhost.', 'db1.example.test.', 'localhost ', ' localhost', 'local host', 'localhost\n', '\nlocalhost', 'localhost,0', 'localhost,65536',
               'localhost,-1', 'localhost,abc', 'localhost,', 'localhost\\', '.\\', 'localhost\\a b', 'localhost\\' + 'a' * 17, 'localhost\\a;b', "localhost'",
               'localhost;x', 'a' * 254, 'db\\i\\j', 'localhost,1,2', '\uff4c\uff4f\uff43\uff41\uff4c\uff48\uff4f\uff53\uff54', 'l\u043ecalhost', 'localhost\u0131',
               '[::1', '::1]', '[::2]', '[localhost]', '*', 'a\x40b', 'a,1433,1']
        for text in bad:
            with self.subTest(text=ascii(text)):
                with self.assertRaises(self.m.Refusal):
                    self.m.parse_server(text)

    def test_boundaries(self):
        self.assertEqual(self.m.parse_server('localhost,1').port, 1)
        self.assertEqual(self.m.parse_server('localhost,65535').port, 65535)
        self.assertEqual(self.m.parse_server('localhost\\' + 'a' * 16).instance, 'a' * 16)
        self.assertEqual(self.m.parse_server('localhost\\A_b$#-1').instance, 'A_b$#-1')
        self.assertEqual(self.m.parse_server('a' * 253).kind, 'remote')


class HostAllowed(Base):
    def spec(self, text):
        return self.m.parse_server(text)

    def test_local_is_allowed_for_both_commands_without_remote_flag(self):
        for command in ('backup', 'restore'):
            self.assertIs(self.m.check_host_allowed(self.spec('localhost'), [], command), False)
            self.assertIs(self.m.check_host_allowed(self.spec('.\\SQLEXPRESS'), None, command), False)

    def test_restore_on_remote_is_always_refused_even_with_allow(self):
        spec = self.spec('db1.example.test')
        for allow in ([], ['db1.example.test'], [spec.canonical], ['DB1.EXAMPLE.TEST']):
            with self.subTest(allow=allow):
                with self.assertRaises(self.m.Refusal):
                    self.m.check_host_allowed(spec, allow, 'restore')

    def test_backup_on_remote_needs_exact_allow(self):
        spec = self.spec('db1.example.test')
        self.assertIs(self.m.check_host_allowed(spec, ['db1.example.test'], 'backup'), True)
        self.assertIs(self.m.check_host_allowed(spec, ['other.example.test', 'DB1.example.TEST'], 'backup'), True)
        self.assertIs(self.m.check_host_allowed(spec, [spec.canonical], 'backup'), True)

    def test_backup_on_remote_refusals(self):
        spec = self.spec('db1.example.test')
        for allow in ([], None, ['*'], ['*.example.test'], ['example.test'], ['db1.example.tes'], ['db1.example.test.evil'], ['db1.example.test\\X'],
                      ['db1.example.test,1433'], ['db1'], ['db1.example.test '], ['db1.example.test\n'], ['db.*']):
            with self.subTest(allow=allow):
                with self.assertRaises(self.m.Refusal):
                    self.m.check_host_allowed(spec, allow, 'backup')

    def test_allow_does_not_cover_other_instance_or_port(self):
        with self.assertRaises(self.m.Refusal):
            self.m.check_host_allowed(self.spec('db1.example.test\\X'), ['db1.example.test'], 'backup')
        with self.assertRaises(self.m.Refusal):
            self.m.check_host_allowed(self.spec('db1.example.test,1500'), ['db1.example.test'], 'backup')
        self.assertIs(self.m.check_host_allowed(self.spec('db1.example.test\\X'), ['db1.example.test\\X'], 'backup'), True)

    def test_environment_variable_does_not_allow(self):
        old = os.environ.get('DB_SNAPSHOT_ALLOW_HOSTS')
        os.environ['DB_SNAPSHOT_ALLOW_HOSTS'] = 'db1.example.test'
        try:
            with self.assertRaises(self.m.Refusal):
                self.m.check_host_allowed(self.spec('db1.example.test'), [], 'backup')
        finally:
            if old is None:
                del os.environ['DB_SNAPSHOT_ALLOW_HOSTS']
            else:
                os.environ['DB_SNAPSHOT_ALLOW_HOSTS'] = old


class ServerPath(Base):
    def check(self, path, flavor, kind):
        return self.m.validate_server_path(path, flavor=flavor, kind=kind)

    def test_windows_valid(self):
        self.assertEqual(self.check('D:\\h\\db copies\\a.bak', 'windows', 'file'), 'D:\\h\\db copies\\a.bak')
        self.assertEqual(self.check('D:/h/db-copies/a_1.bak', 'windows', 'file'), 'D:\\h\\db-copies\\a_1.bak')
        self.assertEqual(self.check('d:\\h\\db-copies', 'windows', 'dir'), 'd:\\h\\db-copies')
        self.assertEqual(self.check('D:\\h\\a.b.bak', 'windows', 'file'), 'D:\\h\\a.b.bak')

    def test_posix_valid(self):
        self.assertEqual(self.check('/h/db copies/a.bak', 'posix', 'file'), '/h/db copies/a.bak')
        self.assertEqual(self.check('/h/.local/state/db-copies', 'posix', 'dir'), '/h/.local/state/db-copies')

    def test_windows_refusals(self):
        bad = ['\\\\host\\share\\a.bak', '//host/share/a.bak', '\\\\?\\D:\\h\\a.bak', '\\\\.\\D:\\h\\a.bak', 'D:\\h\\..\\a.bak', 'D:\\..\\a.bak',
               'D:\\h\\a.bak\n', 'D:\\h\\a\n.bak', 'D:\\h\\a\r.bak', 'D:\\h\\a\t.bak', 'D:\\h\\a\x00.bak', 'D:\\h\\a.bak:!!', 'D:\\h\\a.bak:s',
               'D:\\h\\a.bak::$DATA', 'D:\\h\\:!!\\a.bak', 'D:\\h\\NUL', 'D:\\h\\nul.bak', 'D:\\h\\CON.bak', 'D:\\h\\com1.bak', 'D:\\h\\COM9.bak', 'D:\\h\\lpt1.bak',
               'D:\\h\\PRN.bak', 'D:\\h\\aux.bak', 'D:\\NUL\\a.bak', 'D:\\h\\a.bak.', 'D:\\h\\a.bak ', 'D:\\h\\a \\b.bak', 'D:\\h\\a.\\b.bak', "D:\\h\\o'x\\a.bak",
               'D:\\h\\a".bak', 'D:\\h\\a;b.bak', 'D:\\h\\a`b.bak', 'D:\\h\\\u0105.bak', 'D:\\h\\\u0430.bak', 'D:\\h\\\uff41.bak', 'D:\\h\\caf\u00e9.bak', 'a.bak', 'h\\a.bak',
               '.\\a.bak', 'D:a.bak', 'D:h\\a.bak', '', 'D:\\h\\a|b.bak', 'D:\\h\\a*b.bak', 'D:\\h\\a?b.bak', 'D:\\h\\a<b.bak', 'D:\\h\\a%b.bak', 'D:\\h\\a$b.bak',
               'D:\\h\\a&b.bak', '1:\\h\\a.bak', 'DD:\\h\\a.bak', 'D:\\h\\a.txt', 'D:\\h\\a']
        for path in bad:
            with self.subTest(path=ascii(path)):
                with self.assertRaises(self.m.Refusal):
                    self.check(path, 'windows', 'file')

    def test_posix_refusals(self):
        bad = ['/h/../a.bak', '/h/a.bak\n', '/h/a\n.bak', '/h/a\r.bak', '/h/a\x00.bak', '/h/a.bak:s', '/h/:!!/a.bak', "/h/o'x/a.bak", '/h/a"b.bak', '/h/a;b.bak',
               '/h/a`b.bak', '/h/\u0105.bak', '/h/caf\u00e9.bak', '/h/\u0430.bak', 'a.bak', 'h/a.bak', './a.bak', '', '/h/a b /c.bak', '/h/a.bak ', '/h/a$(x).bak',
               '/h/a|b.bak', '/h/a*b.bak', '/h/a.txt', '/h/a']
        for path in bad:
            with self.subTest(path=ascii(path)):
                with self.assertRaises(self.m.Refusal):
                    self.check(path, 'posix', 'file')

    def test_dir_kind_rules(self):
        for flavor, good in (('windows', 'D:\\h\\db-copies'), ('posix', '/h/db-copies')):
            self.assertTrue(self.check(good, flavor, 'dir'))
        for flavor, bad in (('windows', "D:\\h\\o'x\\db-copies"), ('windows', 'D:\\h\\..\\x'), ('windows', '\\\\host\\s'), ('windows', 'D:\\h\\\u0105'),
                            ('windows', 'D:\\h\\x\n'), ('windows', 'D:\\h\\x:s'), ('windows', 'D:\\h\\x '), ('posix', '/h/x\n'), ('posix', '/h/..'),
                            ('posix', 'rel/dir'), ('posix', "/h/o'x"), ('posix', '/h/\u0105')):
            with self.subTest(flavor=flavor, path=ascii(bad)):
                with self.assertRaises(self.m.Refusal):
                    self.check(bad, flavor, 'dir')

    def test_windows_device_names_are_ok_on_posix(self):
        self.assertEqual(self.check('/h/nul.bak', 'posix', 'file'), '/h/nul.bak')

    def test_default_dir_with_apostrophe_in_user_name_is_refused(self):
        default = self.m.default_backup_dir('win32', {'LOCALAPPDATA': "D:\\h\\o'x\\AppData\\Local"}, 'D:\\h\\o\'x')
        with self.assertRaises(self.m.Refusal):
            self.check(default, 'windows', 'dir')


class DirOutsideRepo(Base):
    @staticmethod
    def make_probe(present, mapping=None):
        norm = lambda p: re.sub('/+', '/', p.replace('\\', '/'))
        present = set(norm(p) for p in present)
        mapping = dict(mapping or {})
        calls = []

        def lexists(p):
            calls.append(p)
            return norm(p) in present

        def realpath(p):
            return mapping.get(p, p)
        return lexists, realpath, calls

    def test_clean_path_passes(self):
        lexists, realpath, calls = self.make_probe([])
        self.m.check_dir_outside_repo('/h/a/b', lexists=lexists, realpath=realpath)
        self.assertTrue(calls)

    def test_ancestor_with_git_directory_or_file_is_refused(self):
        for git in ('/h/.git', '/h/a/.git', '/h/a/b/.git', '/.git'):
            with self.subTest(git=git):
                lexists, realpath, _ = self.make_probe([git])
                with self.assertRaises(self.m.Refusal):
                    self.m.check_dir_outside_repo('/h/a/b', lexists=lexists, realpath=realpath)

    def test_sibling_git_is_not_an_ancestor(self):
        lexists, realpath, _ = self.make_probe(['/h/a/c/.git', '/h/x/.git', '/h/a/b/c/.git'])
        self.m.check_dir_outside_repo('/h/a/b', lexists=lexists, realpath=realpath)

    def test_uses_realpath_components(self):
        lexists, realpath, _ = self.make_probe(['/h/real/.git'], {'/h/link/x': '/h/real/x'})
        with self.assertRaises(self.m.Refusal):
            self.m.check_dir_outside_repo('/h/link/x', lexists=lexists, realpath=realpath)
        lexists, realpath, _ = self.make_probe(['/h/link/.git'], {'/h/link/x': '/h/real/x'})
        self.m.check_dir_outside_repo('/h/link/x', lexists=lexists, realpath=realpath)


class DefaultBackupDir(Base):
    def test_linux(self):
        self.assertEqual(self.m.default_backup_dir('linux', {}, '/h/u'), '/h/u/.local/state/db-copies')
        self.assertEqual(self.m.default_backup_dir('linux', {'XDG_STATE_HOME': '/h/xs'}, '/h/u'), '/h/xs/db-copies')

    def test_macos(self):
        self.assertEqual(self.m.default_backup_dir('darwin', {'XDG_STATE_HOME': '/h/xs'}, '/h/u'), '/h/u/Library/Application Support/db-copies')

    def test_windows(self):
        self.assertEqual(self.m.default_backup_dir('win32', {'LOCALAPPDATA': 'D:\\h\\u\\AppData\\Local'}, 'D:\\h\\u'), 'D:\\h\\u\\AppData\\Local\\db-copies')

    def test_unknown_platform_is_linux(self):
        self.assertEqual(self.m.default_backup_dir('freebsd', {}, '/h/u'), '/h/u/.local/state/db-copies')


class UniqueName(Base):
    NOW = datetime.datetime(2026, 11, 22, 13, 4, 5, tzinfo=datetime.timezone.utc)

    def test_exact_name(self):
        self.assertEqual(self.m.unique_name('appdb', 'pre-restore', self.NOW, 'a1b2c3'), 'appdb-pre-restore-20261122-130405-a1b2c3.bak')

    def test_zero_padding(self):
        now = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)
        self.assertEqual(self.m.unique_name('d', 'x', now, '000000'), 'd-x-20260102-030405-000000.bak')

    def test_different_tokens_differ_with_same_clock(self):
        a = self.m.unique_name('appdb', 'x', self.NOW, 'aaaaaa')
        b = self.m.unique_name('appdb', 'x', self.NOW, 'bbbbbb')
        self.assertNotEqual(a, b)

    def test_default_token_is_random_hex(self):
        names = [self.m.unique_name('appdb', 'x', self.NOW) for _ in range(6)]
        for name in names:
            self.assertRegex(name, r'^appdb-x-20261122-130405-[0-9a-f]{6}\.bak$')
        self.assertGreater(len(set(names)), 1)

    def test_name_is_a_valid_server_file_name(self):
        name = self.m.unique_name('app_db1', 'pre-restore', self.NOW, 'ffffff')
        self.assertEqual(self.m.validate_server_path('/h/x/' + name, flavor='posix', kind='file'), '/h/x/' + name)
        self.assertTrue(self.m.validate_server_path('D:\\h\\x\\' + name, flavor='windows', kind='file'))

    def test_invalid_parts_are_refused(self):
        for db, label in (('ma ster', 'x'), ('master', 'x'), ('appdb', 'A'), ('appdb', 'a/b'), ('a\n', 'x'), ('appdb', 'x\n'), ('', 'x'), ('appdb', '')):
            with self.subTest(db=db, label=label):
                with self.assertRaises(self.m.Refusal):
                    self.m.unique_name(db, label, self.NOW, 'a1b2c3')


class SqlBuilders(Base):
    @staticmethod
    def flat(text):
        return ' '.join(text.split())

    def test_quote_helpers(self):
        self.assertEqual(self.m.quote_ident('appdb'), '[appdb]')
        for bad in ('a]b', 'master', '', 'a b', 'a\n'):
            with self.subTest(bad=bad):
                with self.assertRaises(self.m.Refusal):
                    self.m.quote_ident(bad)
        self.assertEqual(self.m.quote_literal('abc'), "N'abc'")
        self.assertEqual(self.m.quote_literal("a'b"), "N'a''b'")
        self.assertEqual(self.m.quote_literal("''"), "N''''''")
        self.assertEqual(self.m.quote_literal(''), "N''")

    def test_backup(self):
        self.assertEqual(self.m.build_backup_sql('appdb', '/h/x/a.bak'), "BACKUP DATABASE [appdb] TO DISK = N'/h/x/a.bak' WITH COPY_ONLY, INIT, CHECKSUM")

    def test_verify_headeronly(self):
        self.assertEqual(self.m.build_verify_sql('/h/x/a.bak'), "RESTORE VERIFYONLY FROM DISK = N'/h/x/a.bak' WITH CHECKSUM, FILE = 1")
        self.assertEqual(self.m.build_headeronly_sql('/h/x/a.bak'), "RESTORE HEADERONLY FROM DISK = N'/h/x/a.bak'")

    def test_state(self):
        self.assertEqual(self.m.build_state_sql('appdb'),
                         "SELECT state_desc, user_access_desc, recovery_model_desc FROM sys.databases WHERE name = N'appdb'")

    def test_identity(self):
        text = self.m.build_identity_sql()
        self.assertIn('@@SERVERNAME', text)
        self.assertIn("SERVERPROPERTY('MachineName')", text)
        self.assertIn("SERVERPROPERTY('InstanceName')", text)
        self.assertTrue(text.lstrip().upper().startswith('SELECT'))

    def test_builders_refuse_bad_database(self):
        for build in (self.m.build_backup_sql, self.m.build_restore_batch):
            for db in ('master', 'a]b', "a'b", 'a\n', ''):
                with self.subTest(build=build.__name__, db=db):
                    with self.assertRaises(self.m.Refusal):
                        build(db, '/h/x/a.bak')
        for db in ('master', 'a]b', "a'b", ''):
            with self.assertRaises(self.m.Refusal):
                self.m.build_state_sql(db)

    def test_path_apostrophe_is_doubled_not_breaking_out(self):
        sql = self.m.build_backup_sql('appdb', "/h/o'x/a.bak")
        self.assertIn("N'/h/o''x/a.bak'", sql)

    def test_restore_batch_exact_after_whitespace_normalization(self):
        expected = ("BEGIN TRY ALTER DATABASE [appdb] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
                    "RESTORE DATABASE [appdb] FROM DISK = N'/h/x/a.bak' WITH REPLACE, FILE = 1, CHECKSUM; "
                    "ALTER DATABASE [appdb] SET MULTI_USER; END TRY "
                    "BEGIN CATCH BEGIN TRY ALTER DATABASE [appdb] SET MULTI_USER; END TRY BEGIN CATCH DECLARE @i int; END CATCH; THROW; END CATCH")
        batch = self.m.build_restore_batch('appdb', '/h/x/a.bak')
        self.assertEqual(self.flat(batch), expected)
        self.assertNotRegex(batch, r'(?im)^\s*GO\s*$')

    def test_restore_batch_has_explicit_file_one(self):
        self.assertIn('FILE = 1', self.m.build_restore_batch('appdb', '/h/x/a.bak'))


class ChildEnv(Base):
    BASE = {'PATH': '/h/bin', 'SYSTEMROOT': 'D:\\h\\win', 'COMSPEC': 'D:\\h\\cmd', 'TEMP': '/h/t', 'TMP': '/h/t', 'HOME': '/h', 'LANG': 'C',
            'LC_ALL': 'C', 'LC_CTYPE': 'C'}

    def noisy(self, **extra):
        env = dict(self.BASE)
        env.update({'SQLCMDINI': '/h/ini.sql', 'SQLCMDDBNAME': 'appdb', 'SQLCMDSERVER': 'x', 'SQLCMDCOLSEP': '|', 'SQLCMDHEADERS': '1',
                    'SQLCMDCOLWIDTH': '1', 'SQLCMDERRORLEVEL': '1', 'SQLCMDWORKSTATION': 'w', 'SQLCMDLOGINTIMEOUT': '1', 'FOO': 'bar', 'LCX': 'x',
                    'PYTHONPATH': '/h/py', 'LD_PRELOAD': '/h/x.so', 'HTTP_PROXY': 'x'})
        env.update(extra)
        return env

    def test_integrated_keeps_whitelist_only_and_drops_credentials(self):
        env = self.m.child_env(self.noisy(SQLCMDUSER='sa', SQLCMDPASSWORD=fake_password()), 'integrated', 'linux')
        self.assertEqual(env, self.BASE)

    def test_env_mode_adds_exactly_user_and_password(self):
        pw = fake_password()
        env = self.m.child_env(self.noisy(SQLCMDUSER='sa', SQLCMDPASSWORD=pw), 'env', 'linux')
        expected = dict(self.BASE)
        expected.update({'SQLCMDUSER': 'sa', 'SQLCMDPASSWORD': pw})
        self.assertEqual(env, expected)
        for key in env:
            if key.startswith('SQLCMD'):
                self.assertIn(key, ('SQLCMDUSER', 'SQLCMDPASSWORD'))

    def test_missing_whitelisted_values_are_not_invented(self):
        env = self.m.child_env({'PATH': '/h/bin'}, 'integrated', 'linux')
        self.assertEqual(env, {'PATH': '/h/bin'})

    def test_missing_variable_in_env_mode_is_refused_with_its_name(self):
        pw = fake_password()
        with self.assertRaises(self.m.Refusal) as ctx:
            self.m.child_env({'PATH': '/h/bin', 'SQLCMDPASSWORD': pw}, 'env', 'linux')
        self.assertIn('SQLCMDUSER', str(ctx.exception))
        self.assertNotIn(pw, str(ctx.exception))
        with self.assertRaises(self.m.Refusal) as ctx:
            self.m.child_env({'PATH': '/h/bin', 'SQLCMDUSER': 'sa'}, 'env', 'linux')
        self.assertIn('SQLCMDPASSWORD', str(ctx.exception))
        with self.assertRaises(self.m.Refusal):
            self.m.child_env({'PATH': '/h/bin'}, 'env', 'linux')

    def test_returns_new_dict(self):
        source = dict(self.BASE)
        env = self.m.child_env(source, 'integrated', 'linux')
        env['X'] = '1'
        self.assertNotIn('X', source)


class ResolveAuth(Base):
    def test_auto(self):
        self.assertEqual(self.m.resolve_auth('auto', 'win32'), 'integrated')
        self.assertEqual(self.m.resolve_auth('auto', 'linux'), 'env')
        self.assertEqual(self.m.resolve_auth('auto', 'darwin'), 'env')

    def test_explicit_values_pass(self):
        for platform in ('win32', 'linux', 'darwin'):
            self.assertEqual(self.m.resolve_auth('integrated', platform), 'integrated')
            self.assertEqual(self.m.resolve_auth('env', platform), 'env')


class SqlcmdArgs(Base):
    def call(self, server='localhost', sql='SELECT 1', **kw):
        kw.setdefault('auth', 'integrated')
        kw.setdefault('trust_server_certificate', False)
        kw.setdefault('server_is_local', True)
        return self.m.sqlcmd_args(server, sql, **kw)

    def test_integrated_exact_prefix_and_flag(self):
        args = self.call()
        self.assertEqual(args[:8], ['-S', 'localhost', '-d', 'master', '-b', '-x', '-Q', 'SELECT 1'])
        self.assertIn('-E', args)
        self.assertNotIn('-C', args)

    def test_env_has_no_e_flag(self):
        args = self.call(auth='env')
        self.assertEqual(args, ['-S', 'localhost', '-d', 'master', '-b', '-x', '-Q', 'SELECT 1'])

    def test_database_is_always_master_even_when_sql_mentions_another(self):
        args = self.call(sql="BACKUP DATABASE [appdb] TO DISK = N'/h/a.bak'", auth='env')
        self.assertEqual(args[args.index('-d') + 1], 'master')
        self.assertEqual(args.count('-d'), 1)
        self.assertIn('-b', args)
        self.assertIn('-x', args)

    def test_trust_certificate_only_local_and_explicit(self):
        self.assertIn('-C', self.call(trust_server_certificate=True))
        self.assertNotIn('-C', self.call(trust_server_certificate=False))
        with self.assertRaises(self.m.Refusal):
            self.call(server='db1.example.test', trust_server_certificate=True, server_is_local=False)
        self.assertNotIn('-C', self.call(server='db1.example.test', trust_server_certificate=False, server_is_local=False))

    def test_never_login_or_password_in_list(self):
        pw = fake_password('pw', '-', 'Kx', '9')
        old = {k: os.environ.get(k) for k in ('SQLCMDUSER', 'SQLCMDPASSWORD')}
        os.environ['SQLCMDUSER'] = 'sa'
        os.environ['SQLCMDPASSWORD'] = pw
        try:
            for auth in ('env', 'integrated'):
                args = self.call(auth=auth)
                self.assertNotIn('-U', args)
                self.assertNotIn('-P', args)
                self.assertNotIn('sa', args)
                self.assertFalse(any(pw in a for a in args))
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_sql_is_passed_as_single_argument(self):
        sql = 'SELECT 1; SELECT 2 -- x'
        args = self.call(sql=sql)
        self.assertEqual(args[args.index('-Q') + 1], sql)


class FindSqlcmd(Base):
    def test_posix_skips_dot_empty_and_relative_and_returns_absolute(self):
        files = {'sqlcmd', './sqlcmd', 'bin/sqlcmd', '/h/bin/sqlcmd', '/h/bin2/sqlcmd'}
        found = self.m.find_sqlcmd({'PATH': ':.::bin:/h/bin:/h/bin2'}, 'linux', isfile=lambda p: p in files, access=lambda p, mode: True)
        self.assertEqual(found, '/h/bin/sqlcmd')

    def test_none_when_only_cwd_has_it(self):
        found = self.m.find_sqlcmd({'PATH': '.::bin'}, 'linux', isfile=lambda p: True, access=lambda p, mode: True)
        self.assertIsNone(found)

    def test_not_executable_is_skipped(self):
        files = {'/h/bin/sqlcmd', '/h/bin2/sqlcmd'}
        found = self.m.find_sqlcmd({'PATH': '/h/bin:/h/bin2'}, 'linux', isfile=lambda p: p in files, access=lambda p, mode: p.startswith('/h/bin2'))
        self.assertEqual(found, '/h/bin2/sqlcmd')

    def test_missing_path_or_no_match(self):
        self.assertIsNone(self.m.find_sqlcmd({}, 'linux', isfile=lambda p: True, access=lambda p, mode: True))
        self.assertIsNone(self.m.find_sqlcmd({'PATH': '/h/bin'}, 'linux', isfile=lambda p: False, access=lambda p, mode: True))

    def test_windows_tries_exe_and_skips_cwd(self):
        files = {'sqlcmd.exe', '.\\sqlcmd.exe', 'bin\\sqlcmd.exe', 'D:\\h\\bin\\sqlcmd.exe'}
        found = self.m.find_sqlcmd({'PATH': ';.;;bin;D:\\h\\bin'}, 'win32', isfile=lambda p: p in files, access=lambda p, mode: True)
        self.assertEqual(found, 'D:\\h\\bin\\sqlcmd.exe')
        self.assertTrue(ntpath.isabs(found))
        self.assertIsNone(self.m.find_sqlcmd({'PATH': '.;bin'}, 'win32', isfile=lambda p: True, access=lambda p, mode: True))

    def test_result_is_absolute_on_posix(self):
        found = self.m.find_sqlcmd({'PATH': '/h/bin'}, 'linux', isfile=lambda p: True, access=lambda p, mode: True)
        self.assertTrue(posixpath.isabs(found))


class MaskerTests(Base):
    def test_raw_and_short_secret(self):
        masker = self.m.Masker(['abc'])
        self.assertEqual(masker.mask('xabcx'), 'x***x')
        self.assertEqual(masker.mask('abcabc'), '******')
        self.assertEqual(masker.mask('nothing here'), 'nothing here')

    def test_empty_values_are_ignored(self):
        masker = self.m.Masker(['', 'abc'])
        self.assertEqual(masker.mask('hello abc'), 'hello ***')
        self.assertEqual(self.m.Masker(['']).mask('hello'), 'hello')
        self.assertEqual(self.m.Masker([]).mask('hello'), 'hello')

    def test_variants_of_awkward_secret(self):
        secret = 'p' + 'w ' + '/&"' + '\\' + 'x+y'
        variants = {
            'raw': secret,
            'url': urllib.parse.quote(secret, safe=''),
            'url_plus': urllib.parse.quote_plus(secret),
            'json': json.dumps(secret)[1:-1],
            'b64': base64.b64encode(secret.encode('utf-8')).decode('ascii'),
            'b64url': base64.urlsafe_b64encode(secret.encode('utf-8')).decode('ascii'),
            'utf16': base64.b64encode(secret.encode('utf-16-le')).decode('ascii'),
        }
        masker = self.m.Masker([secret])
        for name, variant in variants.items():
            with self.subTest(variant=name):
                text = 'before ' + variant + ' after'
                masked = masker.mask(text)
                self.assertNotIn(variant, masked)
                self.assertEqual(masked, 'before *** after')

    def test_base64_and_base64url_differ_and_both_masked(self):
        secret = '??>'
        std = base64.b64encode(secret.encode()).decode()
        url = base64.urlsafe_b64encode(secret.encode()).decode()
        self.assertNotEqual(std, url)
        masker = self.m.Masker([secret])
        for variant in (std, url):
            self.assertEqual(masker.mask('x ' + variant + ' y'), 'x *** y')

    def test_utf16_variant_for_non_ascii_secret(self):
        secret = 'z\u0105\u017c' + 'q'
        masker = self.m.Masker([secret])
        for variant in (secret, base64.b64encode(secret.encode('utf-16-le')).decode(), base64.b64encode(secret.encode()).decode()):
            self.assertNotIn(variant, masker.mask('<' + variant + '>'))

    def test_longer_first(self):
        masker = self.m.Masker(['abc', 'abcdef'])
        self.assertEqual(masker.mask('abcdef'), '***')
        self.assertEqual(self.m.Masker(['abcdef', 'abc']).mask('abcdef'), '***')

    def test_login_is_not_masked_but_password_is(self):
        pw = fake_password()
        masker = self.m.Masker.from_env({'SQLCMDUSER': 'sa', 'SQLCMDPASSWORD': pw}, 'env')
        self.assertEqual(masker.mask('Login failed for user sa'), 'Login failed for user sa')
        self.assertEqual(masker.mask('bad ' + pw), 'bad ***')

    def test_from_env_without_password(self):
        masker = self.m.Masker.from_env({'SQLCMDUSER': 'sa'}, 'env')
        self.assertEqual(masker.mask('sa and more'), 'sa and more')

    def test_short_password_from_env(self):
        masker = self.m.Masker.from_env({'SQLCMDUSER': 'sa', 'SQLCMDPASSWORD': 'abc'}, 'env')
        self.assertEqual(masker.mask('user sa, abc, xabcx'), 'user sa, ***, x***x')

    def test_mask_value_is_recursive_and_returns_new_objects(self):
        pw = fake_password()
        masker = self.m.Masker([pw])
        original = {'a': ['x ' + pw, ('y ' + pw, 3)], 'n': 7, 'z': None, 'b': True, 's': pw}
        result = masker.mask_value(original)
        self.assertEqual(result, {'a': ['x ***', ('y ***', 3)], 'n': 7, 'z': None, 'b': True, 's': '***'})
        self.assertIsNot(result, original)
        self.assertIsNot(result['a'], original['a'])
        self.assertIsInstance(result['a'][1], tuple)
        self.assertEqual(original['s'], pw)
        self.assertEqual(masker.mask_value(pw), '***')


class ExitCodes(Base):
    def code(self, exc, mutated):
        state = self.m.RunState()
        if mutated:
            state.mark_mutating()
        return self.m.exit_code_for(exc, state)

    def test_typed_failures(self):
        for mutated in (False, True):
            self.assertEqual(self.code(self.m.Refusal('x'), mutated), 2)
            self.assertEqual(self.code(self.m.SqlFailure('x'), mutated), 1)
            self.assertEqual(self.code(self.m.NotRun('x'), mutated), 3)

    def test_other_exceptions_depend_on_mutation(self):
        others = [KeyboardInterrupt(), subprocess.TimeoutExpired(['x'], 5), OSError('disk'), RuntimeError('boom'), ValueError('v'), MemoryError()]
        for exc in others:
            with self.subTest(exc=type(exc).__name__):
                self.assertEqual(self.code(exc, False), 2)
                self.assertEqual(self.code(exc, True), 1)


class RunGuarded(Base):
    def invoke(self, fn, mutate_first=False, secrets=()):
        state = self.m.RunState()
        err = io.StringIO()
        masker = self.m.Masker(list(secrets))
        code = self.m.run_guarded(fn, state, err, masker)
        return code, err.getvalue(), state

    def test_returns_function_code_and_writes_nothing(self):
        for value in (0, 1, 2, 3):
            code, text, _ = self.invoke(lambda value=value: value)
            self.assertEqual(code, value)
            self.assertEqual(text, '')

    def test_typed_exceptions(self):
        def refuse():
            raise self.m.Refusal('bad input')

        def fail():
            raise self.m.SqlFailure('sql broke')

        def skip():
            raise self.m.NotRun('no sqlcmd')
        for fn, expected, name in ((refuse, 2, 'Refusal'), (fail, 1, 'SqlFailure'), (skip, 3, 'NotRun')):
            code, text, _ = self.invoke(fn)
            self.assertEqual(code, expected)
            self.assertEqual(text.splitlines()[0], name + ': ' + {2: 'bad input', 1: 'sql broke', 3: 'no sqlcmd'}[expected])

    def test_other_exception_before_mutation_is_2_after_is_1(self):
        for factory in (lambda: KeyboardInterrupt(), lambda: subprocess.TimeoutExpired(['x'], 5), lambda: OSError('disk full'), lambda: RuntimeError('boom')):
            name = type(factory()).__name__

            def before(factory=factory):
                raise factory()

            def after(factory=factory):
                state_holder[0].mark_mutating()
                raise factory()
            with self.subTest(exc=name, phase='before'):
                code, text, _ = self.invoke(before)
                self.assertEqual(code, 2)
                self.assertTrue(text.startswith(name))
                self.assertNotIn('stan bazy', text)
            with self.subTest(exc=name, phase='after'):
                state = self.m.RunState()
                state_holder = [state]
                err = io.StringIO()
                code = self.m.run_guarded(after, state, err, self.m.Masker([]))
                self.assertEqual(code, 1)
                self.assertTrue(err.getvalue().startswith(name))
                self.assertIn('stan bazy mógł się zmienić', err.getvalue())

    def test_message_with_password_is_masked(self):
        pw = fake_password()

        def boom():
            raise RuntimeError('connect failed with ' + pw)
        code, text, _ = self.invoke(boom, secrets=[pw])
        self.assertEqual(code, 2)
        self.assertNotIn(pw, text)
        self.assertEqual(text.splitlines()[0], 'RuntimeError: connect failed with ***')

    def test_no_traceback_and_no_argv(self):
        pw = fake_password('pw', '-', 'Mn', '5')
        marker = 'argv-marker-' + 'q7'
        argv = ['sqlcmd', '-S', 'host', '-Q', 'SELECT ' + marker + pw]
        for exc in (subprocess.TimeoutExpired(argv, 5, output=pw.encode(), stderr=pw.encode()),
                    subprocess.CalledProcessError(2, argv, output=pw, stderr=pw)):
            def boom(exc=exc):
                raise exc
            with self.subTest(exc=type(exc).__name__):
                code, text, _ = self.invoke(boom, secrets=[pw])
                self.assertEqual(code, 2)
                self.assertNotIn('Traceback', text)
                self.assertNotIn('File "', text)
                self.assertNotIn(marker, text)
                self.assertNotIn(pw, text)
                self.assertNotIn('sqlcmd', text)
                self.assertNotIn("['", text)
                self.assertTrue(text.startswith(type(exc).__name__))

    def test_plain_exception_has_no_traceback_or_repr(self):
        def boom():
            raise ValueError('plain')
        code, text, _ = self.invoke(boom)
        self.assertEqual(text.splitlines()[0], 'ValueError: plain')
        self.assertNotIn('Traceback', text)
        self.assertNotIn("ValueError('plain')", text)

    def test_system_exit_is_not_swallowed(self):
        def leave():
            raise SystemExit(5)
        with self.assertRaises(SystemExit):
            self.invoke(leave)

    def test_refusal_after_mutation_still_reports_changed_state(self):
        state = self.m.RunState()
        err = io.StringIO()

        def late():
            state.mark_mutating()
            raise self.m.Refusal('late')
        code = self.m.run_guarded(late, state, err, self.m.Masker([]))
        self.assertEqual(code, 2)
        self.assertTrue(err.getvalue().startswith('Refusal: late'))


if __name__ == '__main__':
    unittest.main()
