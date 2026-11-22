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


# ---------------------------------------------------------------- S2: backup (fakes and helpers)

FIXED_NOW = datetime.datetime(2026, 11, 22, 10, 20, 30, tzinfo=datetime.timezone.utc)
FIXED_TOKEN = 'a1b2c3'
BAK_NAME = 'appdb-pre-20261122-102030-a1b2c3.bak'
LOCAL_DIR = '/h/bk'
LOCAL_PATH = LOCAL_DIR + '/' + BAK_NAME
STATE_DIR = '/h/state/db-copies'
FAKE_SHA = 'ab' * 32
BAK_SIZE = 5242880
ERROR_OUTPUT = 'Msg 3201, Level 16, State 1, Server lab, Line 1\nCannot open backup device.'


def table(columns, rows):
    """Output of `sqlcmd -W -s "|"`: header, separator, rows, empty line, row count."""
    lines = ['|'.join(columns), '|'.join('-' * len(c) for c in columns)]
    lines.extend('|'.join(row) for row in rows)
    lines.append('')
    lines.append('(' + str(len(rows)) + ' rows affected)')
    return '\n'.join(lines) + '\n'


IDENTITY_OUT = table(['server_name', 'machine_name', 'instance_name'], [['LAB01', 'LAB01', 'NULL']])
HEADER_COLUMNS = ['BackupName', 'BackupType', 'ServerName', 'DatabaseName', 'FirstLSN', 'LastLSN', 'BackupSetGUID', 'BackupFinishDate']
HEADER_ROW = ['NULL', '1', 'LAB01', 'appdb', '100', '200', '11111111-2222-3333-4444-555555555555', '2026-11-22 10:20:31.000']


def header_out(columns=None, rows=None):
    return table(columns or HEADER_COLUMNS, [HEADER_ROW] if rows is None else rows)


def classify(sql):
    if sql.startswith('BACKUP DATABASE'):
        return 'backup'
    if sql.startswith('RESTORE VERIFYONLY'):
        return 'verify'
    if sql.startswith('RESTORE HEADERONLY'):
        return 'header'
    return 'identity'


class FakeRunner:
    """Records every call (sql, mutating, timeout, table) and answers from programmed responses per kind."""

    def __init__(self, m, responses=None, raises=None):
        self.m = m
        self.calls = []
        self.responses = {'identity': (0, IDENTITY_OUT, ''), 'backup': (0, '', ''), 'verify': (0, '', ''), 'header': (0, header_out(), '')}
        self.responses.update(responses or {})
        self.raises = raises or {}
        self.state_at_call = []

    def __call__(self, sql, *, mutating, timeout, table=False):
        kind = classify(sql)
        self.calls.append({'kind': kind, 'sql': sql, 'mutating': mutating, 'timeout': timeout, 'table': table})
        if kind in self.raises:
            raise self.raises[kind]
        rc, out, err = self.responses[kind]
        return self.m.RunResult(rc, out, err)

    @property
    def kinds(self):
        return [c['kind'] for c in self.calls]


class FakeFs:
    def __init__(self, bak_size=BAK_SIZE, git_dirs=(), fail_write=False):
        self.bak_size = bak_size
        self.git_dirs = set(git_dirs)
        self.fail_write = fail_write
        self.made = []
        self.written = {}
        self.write_log = []
        self.checked = []
        self.realpaths = []
        self.sha_calls = []

    def makedirs(self, path, mode):
        self.made.append((path, mode))

    def exists(self, path):
        self.checked.append(('exists', path))
        return self.bak_size is not None and path.endswith('.bak')

    def getsize(self, path):
        self.checked.append(('getsize', path))
        return self.bak_size or 0

    def write_atomic(self, path, data):
        if self.fail_write:
            raise OSError('disk full')
        if not isinstance(data, bytes):
            raise AssertionError('write_atomic needs bytes')
        self.write_log.append(path)
        self.written[path] = data

    def sha256(self, path):
        self.sha_calls.append(path)
        return FAKE_SHA

    def realpath(self, path):
        self.realpaths.append(path)
        return path

    def lexists(self, path):
        return path in self.git_dirs

    @property
    def touched(self):
        return bool(self.made or self.written or self.checked or self.sha_calls)


class BackupBase(Base):
    def opts(self, **kw):
        values = {'database': 'appdb', 'label': 'pre', 'dir': LOCAL_DIR}
        values.update(kw)
        return self.m.BackupOptions(**values)

    def do_backup(self, runner=None, fs=None, guard=True, state=None, platform='linux', environ=None, **kw):
        runner = runner or FakeRunner(self.m)
        fs = fs or FakeFs()
        state = state or self.m.RunState()
        env = {'HOME': '/h/user', 'XDG_STATE_HOME': '/h/state'} if environ is None else environ
        self.runner, self.fs, self.state = runner, fs, state
        call = self.m.guard_runner(runner, state) if guard else runner
        return self.m.run_backup(self.opts(**kw), runner=call, fs=fs, clock=lambda: FIXED_NOW, token_fn=lambda: FIXED_TOKEN,
                                 state=state, environ=env, platform=platform)

    def do_code(self, **kw):
        state = kw.setdefault('state', self.m.RunState())
        err = io.StringIO()
        code = self.m.run_guarded(lambda: self.do_backup(**kw), state, err, self.m.Masker([]))
        return code, err.getvalue()

    def sidecar(self, fs=None):
        fs = fs or self.fs
        paths = [p for p in fs.written if p.endswith('.json')]
        self.assertEqual(len(paths), 1)
        return paths[0], json.loads(fs.written[paths[0]].decode('utf-8'))

    def assert_refused_untouched(self, **kw):
        runner = FakeRunner(self.m)
        fs = FakeFs()
        with self.assertRaises(self.m.Refusal):
            self.do_backup(runner=runner, fs=fs, **kw)
        self.assertEqual(runner.calls, [])
        self.assertFalse(fs.touched)
        self.assertIs(self.state.mutated, False)
        runner2, fs2 = FakeRunner(self.m), FakeFs()
        code, _ = self.do_code(runner=runner2, fs=fs2, **kw)
        self.assertEqual(code, 2)
        self.assertEqual(runner2.calls, [])


class ParseSqlcmdTable(Base):
    def test_header_separator_rows_and_count_line(self):
        out = 'a|b\n-|-\n1|2\n\n(1 rows affected)\n'
        self.assertEqual(self.m.parse_sqlcmd_table(out), [{'a': '1', 'b': '2'}])

    def test_crlf_line_endings(self):
        out = 'a|b\r\n-|-\r\n1|2\r\n3|4\r\n\r\n(2 rows affected)\r\n'
        self.assertEqual(self.m.parse_sqlcmd_table(out), [{'a': '1', 'b': '2'}, {'a': '3', 'b': '4'}])

    def test_singular_and_zero_row_counts_are_ignored(self):
        self.assertEqual(self.m.parse_sqlcmd_table('a\n-\nx\n\n(1 row affected)\n'), [{'a': 'x'}])
        self.assertEqual(self.m.parse_sqlcmd_table('a|b\n-|-\n\n(0 rows affected)\n'), [])

    def test_mapping_is_by_names_when_column_order_changes(self):
        self.assertEqual(self.m.parse_sqlcmd_table('z|a|m\n-|-|-\n1|2|3\n'), [{'z': '1', 'a': '2', 'm': '3'}])
        first = self.m.parse_sqlcmd_table(table(HEADER_COLUMNS, [HEADER_ROW]))[0]
        order = list(reversed(range(len(HEADER_COLUMNS))))
        second = self.m.parse_sqlcmd_table(table([HEADER_COLUMNS[i] for i in order], [[HEADER_ROW[i] for i in order]]))[0]
        self.assertEqual(first, second)

    def test_empty_and_null_values_stay_text(self):
        self.assertEqual(self.m.parse_sqlcmd_table('a|b|c\n-|-|-\nNULL||x\n'), [{'a': 'NULL', 'b': '', 'c': 'x'}])

    def test_separator_character_inside_a_field_is_refused(self):
        with self.assertRaises(self.m.SqlFailure) as ctx:
            self.m.parse_sqlcmd_table('a|b\n-|-\nname|with|pipe\n')
        self.assertIn('cannot read the backup header unambiguously', str(ctx.exception))

    def test_too_few_fields_is_refused(self):
        with self.assertRaises(self.m.SqlFailure):
            self.m.parse_sqlcmd_table('a|b|c\n-|-|-\n1|2\n')

    def test_missing_header_line(self):
        for text in ('', '\n', '(1 rows affected)\n', '\r\n\r\n'):
            with self.subTest(text=text):
                with self.assertRaises(self.m.SqlFailure):
                    self.m.parse_sqlcmd_table(text)


class SqlcmdArgsTableMode(Base):
    kw = {'auth': 'integrated', 'trust_server_certificate': False, 'server_is_local': True}

    def test_default_is_unchanged(self):
        args = self.m.sqlcmd_args('localhost', 'SELECT 1', **self.kw)
        self.assertEqual(args, ['-S', 'localhost', '-d', 'master', '-b', '-x', '-Q', 'SELECT 1', '-E'])
        self.assertEqual(args, self.m.sqlcmd_args('localhost', 'SELECT 1', table_mode=False, **self.kw))

    def test_table_mode_adds_w_and_separator_before_query(self):
        args = self.m.sqlcmd_args('localhost', 'SELECT 1', table_mode=True, **self.kw)
        self.assertIn('-W', args)
        self.assertEqual(args[args.index('-s') + 1], '|')
        self.assertLess(args.index('-W'), args.index('-Q'))
        self.assertLess(args.index('-s'), args.index('-Q'))
        self.assertEqual(args[args.index('-Q') + 1], 'SELECT 1')
        base = self.m.sqlcmd_args('localhost', 'SELECT 1', **self.kw)
        rest = list(args)
        rest.remove('-W')
        position = rest.index('-s')
        del rest[position:position + 2]
        self.assertEqual(rest, base)

    def test_table_mode_keeps_other_flags(self):
        args = self.m.sqlcmd_args('localhost', 'SELECT 1', auth='integrated', trust_server_certificate=True, server_is_local=True, table_mode=True)
        for flag in ('-d', 'master', '-b', '-x', '-E', '-C'):
            self.assertIn(flag, args)
        with self.assertRaises(self.m.Refusal):
            self.m.sqlcmd_args('dbhost.example', 'SELECT 1', auth='integrated', trust_server_certificate=True, server_is_local=False, table_mode=True)


class GuardRunner(Base):
    def test_marks_before_a_mutating_call_not_after(self):
        state = self.m.RunState()
        seen = []

        def inner(sql, *, mutating, timeout, table=False):
            seen.append(state.mutated)
            return self.m.RunResult(0, 'ok', '')
        guarded = self.m.guard_runner(inner, state)
        result = guarded('BACKUP', mutating=True, timeout=5.0)
        self.assertEqual(seen, [True])
        self.assertIs(state.mutated, True)
        self.assertEqual((result.rc, result.stdout), (0, 'ok'))

    def test_reads_do_not_mark(self):
        state = self.m.RunState()
        seen = []

        def inner(sql, *, mutating, timeout, table=False):
            seen.append((state.mutated, mutating, timeout, table))
            return self.m.RunResult(0, '', '')
        guarded = self.m.guard_runner(inner, state)
        guarded('SELECT 1', mutating=False, timeout=7.5, table=True)
        self.assertEqual(seen, [(False, False, 7.5, True)])
        self.assertIs(state.mutated, False)

    def test_marks_even_when_the_call_raises(self):
        state = self.m.RunState()

        def inner(sql, *, mutating, timeout, table=False):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.m.guard_runner(inner, state)('BACKUP', mutating=True, timeout=1.0)
        self.assertIs(state.mutated, True)


class BackupSuccess(BackupBase):
    def test_exact_call_order_and_sql(self):
        result = self.do_backup()
        self.assertEqual(self.runner.kinds, ['identity', 'backup', 'verify', 'header'])
        self.assertEqual([c['mutating'] for c in self.runner.calls], [False, True, False, False])
        self.assertEqual([c['table'] for c in self.runner.calls], [True, False, False, True])
        sqls = [c['sql'] for c in self.runner.calls]
        self.assertEqual(sqls, [self.m.build_identity_sql(), self.m.build_backup_sql('appdb', LOCAL_PATH),
                                self.m.build_verify_sql(LOCAL_PATH), self.m.build_headeronly_sql(LOCAL_PATH)])
        self.assertIn('COPY_ONLY', sqls[1])
        self.assertEqual(result['status'], 'PASS')

    def test_timeout_is_passed_to_the_backup_call(self):
        self.do_backup(timeout=123.0)
        backup = [c for c in self.runner.calls if c['kind'] == 'backup'][0]
        self.assertEqual(backup['timeout'], 123.0)
        for call in self.runner.calls:
            self.assertGreater(call['timeout'], 0)

    def test_guard_marks_state_only_through_the_wrapper(self):
        self.do_backup(guard=False)
        self.assertIs(self.state.mutated, False)
        self.do_backup(guard=True)
        self.assertIs(self.state.mutated, True)

    def test_directory_is_made_with_mode_0700_only_locally_and_before_backup(self):
        self.do_backup()
        self.assertEqual(self.fs.made, [(LOCAL_DIR, 0o700)])

    def test_result_fields(self):
        result = self.do_backup()
        self.assertEqual(result['path'], LOCAL_PATH)
        self.assertEqual(result['size'], BAK_SIZE)
        self.assertIs(result['remote'], False)
        self.assertIs(result['verified_locally'], True)
        self.assertEqual(result['verify'], 'passed')
        self.assertIn(('exists', LOCAL_PATH), self.fs.checked)
        self.assertIn(('getsize', LOCAL_PATH), self.fs.checked)

    def test_restore_command_default_server(self):
        base = 'db_snapshot.py restore --file ' + LOCAL_PATH + ' --database appdb --confirm appdb'
        command = self.do_backup()['restore_command']
        self.assertEqual(command, base)
        self.assertNotIn('--server', command)

    def test_restore_command_names_a_non_default_server(self):
        command = self.do_backup(server='.\\SQLEXPRESS')['restore_command']
        self.assertTrue(command.startswith('db_snapshot.py restore --file ' + LOCAL_PATH + ' --database appdb --confirm appdb'))
        self.assertIn('--server', command)
        self.assertIn('SQLEXPRESS', command)

    def test_sidecar_schema(self):
        self.do_backup()
        path, data = self.sidecar()
        self.assertEqual(path, LOCAL_PATH + '.json')
        self.assertEqual(set(data), {'schema', 'database', 'label', 'server', 'file', 'size', 'created_utc', 'backup_set_guid', 'first_lsn',
                                     'last_lsn', 'backup_finish_date', 'remote', 'server_path', 'copy_only'})
        self.assertEqual(data['schema'], 1)
        self.assertEqual((data['database'], data['label']), ('appdb', 'pre'))
        self.assertEqual(data['server'], {'raw': 'localhost', 'canonical': 'local', 'engine_server_name': 'LAB01', 'machine_name': 'LAB01',
                                          'instance_name': None if data['server']['instance_name'] is None else data['server']['instance_name']})
        self.assertTrue(data['file'].endswith(BAK_NAME))
        self.assertEqual(data['size'], BAK_SIZE)
        self.assertIn('2026-11-22', data['created_utc'])
        self.assertIn('10:20:30', data['created_utc'])
        self.assertEqual(data['backup_set_guid'], HEADER_ROW[6])
        self.assertEqual((data['first_lsn'], data['last_lsn']), ('100', '200'))
        self.assertEqual(data['backup_finish_date'], HEADER_ROW[7])
        self.assertIs(data['remote'], False)
        self.assertIs(data['copy_only'], True)

    def test_sidecar_is_written_atomically_after_the_checks_and_not_world_readable(self):
        self.do_backup()
        self.assertEqual(self.fs.write_log, [LOCAL_PATH + '.json'])
        self.assertIsInstance(self.fs.written[LOCAL_PATH + '.json'], bytes)

    def test_sidecar_has_no_credentials_and_no_foreign_paths(self):
        pw = fake_password()
        environ = {'HOME': '/h/user', 'XDG_STATE_HOME': '/h/state', 'SQLCMDUSER': 'lab_login', 'SQLCMDPASSWORD': pw}
        self.do_backup(environ=environ, auth='env')
        _, data = self.sidecar()
        text = json.dumps(data)
        for forbidden in (pw, 'lab_login', 'SQLCMDUSER', 'SQLCMDPASSWORD', '/h/user', '/h/state'):
            self.assertNotIn(forbidden, text)
        self.assertNotIn('sha256', data)

    def test_no_verify_skips_verifyonly(self):
        result = self.do_backup(no_verify=True)
        self.assertEqual(self.runner.kinds, ['identity', 'backup', 'header'])
        self.assertEqual(result['verify'], 'skipped')

    def test_sha256_goes_to_the_sidecar_for_local_files(self):
        self.do_backup(sha256=True)
        self.assertEqual(self.fs.sha_calls, [LOCAL_PATH])
        self.assertEqual(self.sidecar()[1]['sha256'], FAKE_SHA)

    def test_no_sha256_by_default(self):
        self.do_backup()
        self.assertEqual(self.fs.sha_calls, [])

    def test_header_database_compared_case_insensitively_and_columns_reordered(self):
        order = list(reversed(range(len(HEADER_COLUMNS))))
        row = [HEADER_ROW[i] for i in order]
        row[order.index(3)] = 'APPDB'
        out = table([HEADER_COLUMNS[i] for i in order], [row])
        result = self.do_backup(runner=FakeRunner(self.m, {'header': (0, out, '')}))
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(self.sidecar()[1]['backup_set_guid'], HEADER_ROW[6])

    def test_informational_message_is_not_an_error(self):
        info = 'Msg 4035, Level 10, State 1\nProcessed 100 pages.'
        result = self.do_backup(runner=FakeRunner(self.m, {'backup': (0, info, '')}))
        self.assertEqual(result['status'], 'PASS')

    def test_default_directory_when_dir_is_missing(self):
        result = self.do_backup(dir=None)
        self.assertEqual(result['path'], STATE_DIR + '/' + BAK_NAME)
        self.assertEqual(self.fs.made, [(STATE_DIR, 0o700)])

    def test_windows_local_default_directory_uses_backslashes(self):
        environ = {'LOCALAPPDATA': 'D:\\h\\local'}
        result = self.do_backup(dir=None, platform='win32', environ=environ)
        self.assertEqual(result['path'], 'D:\\h\\local\\db-copies\\' + BAK_NAME)
        self.assertEqual(self.runner.calls[1]['sql'], self.m.build_backup_sql('appdb', result['path']))


class CoordinatorNotice(BackupBase):
    def test_local_notice_contents(self):
        result = self.do_backup()
        text = result['coordinator_notice']
        self.assertIsInstance(text, str)
        for needed in ('appdb', 'localhost', 'pre', LOCAL_PATH, '2026-11-22', 'UTC', result['restore_command'],
                       'kopia może zawierać dane osobowe i nie jest szyfrowana', 'współdziel'):
            self.assertIn(needed, text)
        self.assertRegex(text, r'5[ ,.]?242[ ,.]?880|5\.0 MB|5 MB|5\.00 MiB|5 MiB|5242880')
        self.assertNotIn('nie zweryfikowana lokalnie', text)

    def test_function_returns_the_same_elements(self):
        result = self.do_backup()
        text = self.m.coordinator_notice(result)
        for needed in ('appdb', LOCAL_PATH, 'dane osobowe'):
            self.assertIn(needed, text)
        self.assertEqual(self.m.coordinator_notice(result, 'backup'), text)

    def test_remote_notice_is_explicit(self):
        result = self.do_backup(server='dbhost.example', allow_hosts=('dbhost.example',), server_os='linux', dir='/srv/h/bak')
        text = result['coordinator_notice']
        self.assertIn('zdalny host', text)
        self.assertIn('ścieżka na serwerze, nie zweryfikowana lokalnie', text)
        self.assertIn('dbhost.example', text)
        self.assertIn('dane osobowe', text)

    def test_notice_has_no_login_password_or_argv(self):
        pw = fake_password()
        environ = {'HOME': '/h/user', 'XDG_STATE_HOME': '/h/state', 'SQLCMDUSER': 'lab_login', 'SQLCMDPASSWORD': pw}
        text = self.do_backup(environ=environ, auth='env')['coordinator_notice']
        for forbidden in (pw, 'lab_login', 'SQLCMD', 'sqlcmd', '-Q', '-S '):
            self.assertNotIn(forbidden, text)


class BackupRemote(BackupBase):
    kw = {'server': 'dbhost.example', 'allow_hosts': ('dbhost.example',), 'server_os': 'linux', 'dir': '/srv/h/bak'}
    remote_path = '/srv/h/bak/' + BAK_NAME

    def test_flow_skips_local_file_checks_and_makedirs(self):
        result = self.do_backup(**self.kw)
        self.assertEqual(self.runner.kinds, ['identity', 'backup', 'verify', 'header'])
        self.assertEqual(self.runner.calls[1]['sql'], self.m.build_backup_sql('appdb', self.remote_path))
        self.assertEqual(self.fs.made, [])
        self.assertEqual([c for c in self.fs.checked], [])
        self.assertEqual(result['path'], self.remote_path)
        self.assertIs(result['remote'], True)
        self.assertIs(result['verified_locally'], False)
        self.assertEqual(result['verify'], 'passed')

    def test_realpath_and_git_check_are_not_applied_to_the_server_directory(self):
        fs = FakeFs(git_dirs=['/srv/h/.git', '/srv/.git'])
        result = self.do_backup(fs=fs, **self.kw)
        self.assertEqual(result['status'], 'PASS')
        self.assertNotIn('/srv/h/bak', fs.realpaths)

    def test_sidecar_goes_to_the_local_state_directory(self):
        self.do_backup(**self.kw)
        path, data = self.sidecar()
        self.assertEqual(path, STATE_DIR + '/' + BAK_NAME + '.json')
        self.assertIs(data['remote'], True)
        self.assertEqual(data['server_path'], self.remote_path)
        self.assertEqual(data['server']['raw'], 'dbhost.example')
        self.assertEqual(data['server']['canonical'], 'dbhost.example')
        self.assertNotIn(self.remote_path, [p for p in self.fs.written if not p.endswith('.json')])

    def test_sha256_is_skipped_for_remote_files(self):
        self.do_backup(sha256=True, **self.kw)
        self.assertEqual(self.fs.sha_calls, [])
        self.assertNotIn('sha256', self.sidecar()[1])

    def test_windows_server_path_uses_backslashes(self):
        result = self.do_backup(server='dbhost.example', allow_hosts=('dbhost.example',), server_os='windows', dir='D:\\h\\bk')
        self.assertEqual(result['path'], 'D:\\h\\bk\\' + BAK_NAME)
        self.assertEqual(self.runner.calls[1]['sql'], self.m.build_backup_sql('appdb', 'D:\\h\\bk\\' + BAK_NAME))
        self.assertEqual(self.fs.made, [])

    def test_allow_list_match_ignores_case_and_ports_come_from_canonical_form(self):
        result = self.do_backup(server='DBHOST.example,1444', allow_hosts=('dbhost.example,1444',), server_os='linux', dir='/srv/h/bak')
        self.assertIs(result['remote'], True)


class BackupFailures(BackupBase):
    def failing(self, kind, rc=1, out='', err=ERROR_OUTPUT):
        return FakeRunner(self.m, {kind: (rc, out, err)})

    def test_backup_nonzero_rc(self):
        runner = self.failing('backup')
        with self.assertRaises(self.m.SqlFailure):
            self.do_backup(runner=runner)
        self.assertEqual(runner.kinds, ['identity', 'backup'])
        self.assertEqual(self.fs.written, {})
        code, _ = self.do_code(runner=self.failing('backup'))
        self.assertEqual(code, 1)

    def test_error_line_in_output_with_rc_zero_is_a_failure(self):
        for level in ('11', '16', '19', '20', '25'):
            with self.subTest(level=level):
                runner = FakeRunner(self.m, {'backup': (0, 'Msg 3201, Level ' + level + ', State 1\nx', '')})
                with self.assertRaises(self.m.SqlFailure):
                    self.do_backup(runner=runner)
        runner = FakeRunner(self.m, {'backup': (0, '', ERROR_OUTPUT)})
        with self.assertRaises(self.m.SqlFailure):
            self.do_backup(runner=runner)

    def test_verify_failure_names_the_copy_and_leaves_it(self):
        runner = self.failing('verify')
        with self.assertRaises(self.m.SqlFailure) as ctx:
            self.do_backup(runner=runner)
        self.assertIn(LOCAL_PATH, str(ctx.exception))
        self.assertEqual(runner.kinds, ['identity', 'backup', 'verify'])
        self.assertEqual(self.fs.written, {})

    def test_verify_error_text_with_rc_zero(self):
        runner = FakeRunner(self.m, {'verify': (0, 'Msg 3241, Level 16, State 5\nThe media family is incorrectly formed.', '')})
        with self.assertRaises(self.m.SqlFailure):
            self.do_backup(runner=runner)

    def test_header_failures(self):
        two = table(HEADER_COLUMNS, [HEADER_ROW, HEADER_ROW])
        none = table(HEADER_COLUMNS, [])
        other_db = list(HEADER_ROW)
        other_db[3] = 'otherdb'
        diff_type = list(HEADER_ROW)
        diff_type[1] = '5'
        cases = {'two rows': two, 'no rows': none, 'other database': table(HEADER_COLUMNS, [other_db]),
                 'differential': table(HEADER_COLUMNS, [diff_type]), 'missing column': table(HEADER_COLUMNS[:3] + HEADER_COLUMNS[4:], [HEADER_ROW[:3] + HEADER_ROW[4:]]),
                 'pipe in a field': table(HEADER_COLUMNS, [['a|b'] + HEADER_ROW[1:]]), 'no header at all': ''}
        for name, out in cases.items():
            with self.subTest(case=name):
                runner = FakeRunner(self.m, {'header': (0, out, '')})
                fs = FakeFs()
                with self.assertRaises(self.m.SqlFailure):
                    self.do_backup(runner=runner, fs=fs)
                self.assertEqual(fs.written, {})
                code, _ = self.do_code(runner=FakeRunner(self.m, {'header': (0, out, '')}))
                self.assertEqual(code, 1)

    def test_header_rc_nonzero(self):
        with self.assertRaises(self.m.SqlFailure):
            self.do_backup(runner=self.failing('header'))

    def test_identity_failure_stops_before_backup(self):
        runner = self.failing('identity')
        with self.assertRaises(self.m.SqlFailure):
            self.do_backup(runner=runner)
        self.assertEqual(runner.kinds, ['identity'])
        self.assertIs(self.state.mutated, False)

    def test_missing_or_empty_file(self):
        for size in (None, 0):
            with self.subTest(size=size):
                fs = FakeFs(bak_size=size)
                with self.assertRaises(self.m.SqlFailure) as ctx:
                    self.do_backup(fs=fs)
                self.assertIn(LOCAL_PATH, str(ctx.exception))
                self.assertEqual(fs.written, {})
                code, _ = self.do_code(fs=FakeFs(bak_size=size))
                self.assertEqual(code, 1)

    def test_sidecar_write_error_names_the_bak_path(self):
        fs = FakeFs(fail_write=True)
        with self.assertRaises(self.m.SqlFailure) as ctx:
            self.do_backup(fs=fs)
        self.assertIn(LOCAL_PATH, str(ctx.exception))
        code, text = self.do_code(fs=FakeFs(fail_write=True))
        self.assertEqual(code, 1)
        self.assertIn(LOCAL_PATH, text)

    def test_failure_exit_reports_changed_state(self):
        code, text = self.do_code(runner=self.failing('backup'))
        self.assertEqual(code, 1)
        self.assertIn('stan bazy mógł się zmienić', text)


class BackupRefusals(BackupBase):
    def test_system_databases(self):
        for name in ('master', 'TempDB', 'msdb', 'model'):
            with self.subTest(name=name):
                self.assert_refused_untouched(database=name)

    def test_bad_database_syntax(self):
        for name in ('a]b', "a'b", '', 'm\u0430ster'):
            with self.subTest(name=ascii(name)):
                self.assert_refused_untouched(database=name)

    def test_bad_labels(self):
        for label in ('', 'Pre', 'a b', 'x' * 41, 'a_b', "a'b"):
            with self.subTest(label=label):
                self.assert_refused_untouched(label=label)

    def test_bad_server_syntax(self):
        for server in ('tcp:localhost', '127.1', '0.0.0.0', ''):
            with self.subTest(server=server):
                self.assert_refused_untouched(server=server)

    def test_foreign_host_without_allow(self):
        self.assert_refused_untouched(server='dbhost.example', dir='/srv/h/bak', server_os='linux')
        self.assert_refused_untouched(server='dbhost.example', allow_hosts=('other.example',), dir='/srv/h/bak', server_os='linux')
        self.assert_refused_untouched(server='localhost.evil.example', dir='/srv/h/bak', server_os='linux')

    def test_foreign_host_allowed_but_without_dir_or_server_os(self):
        base = {'server': 'dbhost.example', 'allow_hosts': ('dbhost.example',)}
        self.assert_refused_untouched(dir='/srv/h/bak', **base)
        self.assert_refused_untouched(dir=None, **base)
        self.assert_refused_untouched(dir=None, server_os='linux', **base)
        self.assert_refused_untouched(dir='/srv/h/bak', server_os='macos', **base)

    def test_directory_inside_a_repository(self):
        for found in ('/h/repo/.git', '/.git'):
            with self.subTest(found=found):
                runner, fs = FakeRunner(self.m), FakeFs(git_dirs=[found])
                with self.assertRaises(self.m.Refusal):
                    self.do_backup(runner=runner, fs=fs, dir='/h/repo/out/deep')
                self.assertEqual(runner.calls, [])
                self.assertEqual((fs.made, fs.written), ([], {}))

    def test_default_directory_inside_a_repository(self):
        runner, fs = FakeRunner(self.m), FakeFs(git_dirs=['/h/state/.git'])
        with self.assertRaises(self.m.Refusal):
            self.do_backup(runner=runner, fs=fs, dir=None)
        self.assertEqual(runner.calls, [])
        self.assertEqual(fs.made, [])

    def test_bad_server_paths(self):
        for directory in ('\\\\h\\share\\bk', 'rel/dir', "/h/o'x", '/h/a;b', '/h/../bk', '/h/bk\n', '/h/\u00e9', 'C:\\h\\bk'):
            with self.subTest(directory=ascii(directory)):
                self.assert_refused_untouched(dir=directory)

    def test_bad_windows_server_paths(self):
        for directory in ('\\\\h\\share\\bk', '\\\\?\\D:\\bk', 'D:\\h\\NUL', 'D:rel', 'D:\\h\\bk:ads', "D:\\h\\o'x", 'D:\\h\\bk.'):
            with self.subTest(directory=directory):
                self.assert_refused_untouched(server='dbhost.example', allow_hosts=('dbhost.example',), server_os='windows', dir=directory)

    def test_refusal_message_has_no_secret(self):
        pw = fake_password()
        environ = {'HOME': '/h/user', 'SQLCMDUSER': 'lab_login', 'SQLCMDPASSWORD': pw}
        with self.assertRaises(self.m.Refusal) as ctx:
            self.do_backup(environ=environ, auth='env', database='master')
        self.assertNotIn(pw, str(ctx.exception))
        self.assertNotIn('lab_login', str(ctx.exception))


class BackupDryRun(BackupBase):
    def attempt(self, **kw):
        runner, fs = FakeRunner(self.m), FakeFs()
        outcome = None
        try:
            outcome = self.do_backup(runner=runner, fs=fs, **kw)
        except self.m.NotRun as exc:
            outcome = getattr(exc, 'result', None)
        else:
            self.assertEqual(outcome['status'], 'NOT_RUN')
        return runner, fs, outcome

    def test_no_calls_no_writes_local(self):
        runner, fs, outcome = self.attempt(dry_run=True)
        self.assertEqual(runner.calls, [])
        self.assertFalse(fs.touched)
        self.assertEqual(fs.realpaths.count('/nonexistent'), 0)
        self.assertIs(self.state.mutated, False)

    def test_no_calls_no_writes_remote_and_default_dir(self):
        for kw in ({'server': 'dbhost.example', 'allow_hosts': ('dbhost.example',), 'server_os': 'linux', 'dir': '/srv/h/bak'}, {'dir': None}):
            with self.subTest(kw=kw):
                runner, fs, _ = self.attempt(dry_run=True, **kw)
                self.assertEqual(runner.calls, [])
                self.assertFalse(fs.touched)

    def test_exit_code_is_3(self):
        runner, fs = FakeRunner(self.m), FakeFs()
        code, _ = self.do_code(runner=runner, fs=fs, dry_run=True)
        self.assertEqual(code, 3)
        self.assertEqual(runner.calls, [])
        self.assertFalse(fs.touched)

    def test_result_carries_a_plan_when_returned(self):
        _, _, outcome = self.attempt(dry_run=True)
        if outcome is not None:
            self.assertEqual(outcome['status'], 'NOT_RUN')
            self.assertIn('plan', outcome)
            plan = json.dumps(outcome['plan'])
            self.assertIn(LOCAL_PATH, plan)
            self.assertIn('appdb', plan)

    def test_dry_run_still_validates(self):
        runner, fs = FakeRunner(self.m), FakeFs()
        with self.assertRaises(self.m.Refusal):
            self.do_backup(runner=runner, fs=fs, dry_run=True, database='master')
        with self.assertRaises(self.m.Refusal):
            self.do_backup(runner=runner, fs=fs, dry_run=True, dir='rel/dir')
        self.assertEqual(runner.calls, [])
        self.assertFalse(fs.touched)


class BackupInterruptions(BackupBase):
    EXC = (lambda: KeyboardInterrupt(), lambda: subprocess.TimeoutExpired(['x'], 5), lambda: OSError('disk'))

    def test_exception_during_backup_is_1_with_state_warning(self):
        for factory in self.EXC:
            name = type(factory()).__name__
            with self.subTest(exc=name):
                runner = FakeRunner(self.m, raises={'backup': factory()})
                code, text = self.do_code(runner=runner)
                self.assertEqual(code, 1)
                self.assertIn('stan bazy mógł się zmienić', text)
                self.assertTrue(text.startswith(name))
                self.assertIs(self.state.mutated, True)
                self.assertEqual(self.fs.written, {})

    def test_exception_after_backup_is_still_1(self):
        for kind in ('verify', 'header'):
            with self.subTest(kind=kind):
                code, text = self.do_code(runner=FakeRunner(self.m, raises={kind: subprocess.TimeoutExpired(['x'], 5)}))
                self.assertEqual(code, 1)
                self.assertIn('stan bazy', text)

    def test_exception_before_mutation_is_2(self):
        for factory in self.EXC:
            with self.subTest(exc=type(factory()).__name__):
                code, text = self.do_code(runner=FakeRunner(self.m, raises={'identity': factory()}))
                self.assertEqual(code, 2)
                self.assertTrue(text.startswith(type(factory()).__name__))
                self.assertNotIn('stan bazy', text)
                self.assertIs(self.state.mutated, False)

    def test_exception_in_validation_is_2(self):
        class BrokenFs(FakeFs):
            def realpath(self, path):
                raise OSError('io error')
        runner = FakeRunner(self.m)
        code, text = self.do_code(runner=runner, fs=BrokenFs())
        self.assertEqual(code, 2)
        self.assertTrue(text.startswith('OSError: io error'))
        self.assertEqual(runner.calls, [])
        self.assertNotIn('stan bazy', text)

    def test_makedirs_error_before_backup_is_2(self):
        class NoDirFs(FakeFs):
            def makedirs(self, path, mode):
                raise PermissionError('denied')
        runner = FakeRunner(self.m)
        code, text = self.do_code(runner=runner, fs=NoDirFs())
        self.assertEqual(code, 2)
        self.assertTrue(text.startswith('PermissionError'))
        self.assertNotIn('backup', runner.kinds)


class FakePopen:
    """Stand-in for subprocess.run; answers by the SQL found after -Q."""

    def __init__(self, replies=None, error=None):
        self.calls = []
        self.replies = {'identity': (0, IDENTITY_OUT, ''), 'backup': (0, '', ''), 'verify': (0, '', ''), 'header': (0, header_out(), '')}
        self.replies.update(replies or {})
        self.error = error

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if self.error is not None:
            raise self.error
        sql = argv[argv.index('-Q') + 1]
        rc, out, err = self.replies[classify(sql)]
        return subprocess.CompletedProcess(argv, rc, out, err)


class SubprocessRunnerTests(Base):
    ENV = {'PATH': '/h/bin', 'LANG': 'C'}

    def make(self, popen, masker=None, env=None, **kw):
        values = {'sqlcmd_path': '/h/bin/sqlcmd', 'server_raw': 'localhost', 'auth': 'integrated', 'env': self.ENV if env is None else env,
                  'masker': masker or self.m.Masker([]), 'cwd': '/h/bk', 'trust_server_certificate': False, 'server_is_local': True, 'popen': popen}
        values.update(kw)
        return self.m.SubprocessRunner(**values)

    def test_argv_and_keyword_arguments(self):
        popen = FakePopen()
        runner = self.make(popen)
        result = runner('SELECT 1', mutating=False, timeout=42.0)
        argv, kwargs = popen.calls[0]
        self.assertEqual(argv, ['/h/bin/sqlcmd'] + self.m.sqlcmd_args('localhost', 'SELECT 1', auth='integrated', trust_server_certificate=False,
                                                                      server_is_local=True, table_mode=False))
        self.assertIs(kwargs['shell'], False)
        self.assertEqual(kwargs['cwd'], '/h/bk')
        self.assertEqual(kwargs['env'], self.ENV)
        self.assertEqual(kwargs['timeout'], 42.0)
        self.assertIs(kwargs['capture_output'], True)
        self.assertIs(kwargs['text'], True)
        self.assertIsInstance(result, self.m.RunResult)
        self.assertNotIn('input', kwargs)

    def test_table_mode_reaches_argv(self):
        popen = FakePopen()
        self.make(popen)('SELECT 1', mutating=False, timeout=1.0, table=True)
        argv = popen.calls[0][0]
        self.assertIn('-W', argv)
        self.assertEqual(argv[argv.index('-s') + 1], '|')

    def test_env_is_exactly_the_given_whitelist(self):
        popen = FakePopen()
        environ = {'PATH': '/h/bin', 'LANG': 'C', 'SQLCMDINI': '/h/x', 'OTHER_VAR': '1'}
        env = self.m.child_env(environ, 'integrated', 'linux')
        self.make(popen, env=env)('SELECT 1', mutating=False, timeout=1.0)
        passed = popen.calls[0][1]['env']
        self.assertEqual(passed, {'PATH': '/h/bin', 'LANG': 'C'})
        self.assertNotIn('SQLCMDINI', passed)

    def test_result_values_and_nonzero_rc_is_returned_not_raised(self):
        popen = FakePopen({'identity': (3, 'out text', 'err text')})
        result = self.make(popen)('SELECT 1', mutating=False, timeout=1.0)
        self.assertEqual((result.rc, result.stdout, result.stderr), (3, 'out text', 'err text'))

    def test_output_is_masked(self):
        pw = fake_password()
        popen = FakePopen({'identity': (1, 'line ' + pw, 'bad ' + pw + ' end')})
        result = self.make(popen, masker=self.m.Masker([pw]))('SELECT 1', mutating=False, timeout=1.0)
        self.assertNotIn(pw, result.stdout + result.stderr)
        self.assertIn('***', result.stdout)
        self.assertIn('***', result.stderr)

    def test_plain_output_is_cut_to_4000_characters(self):
        popen = FakePopen({'identity': (0, 'x' * 9000, 'y' * 5000)})
        result = self.make(popen)('SELECT 1', mutating=False, timeout=1.0, table=False)
        self.assertLessEqual(len(result.stdout), 4000)
        self.assertLessEqual(len(result.stderr), 4000)
        self.assertGreaterEqual(len(result.stdout), 3000)
        short = self.make(FakePopen({'identity': (0, 'z' * 3999, '')}))('SELECT 1', mutating=False, timeout=1.0, table=False)
        self.assertEqual(short.stdout, 'z' * 3999)

    def test_table_output_is_kept_whole_up_to_the_million_character_limit(self):
        big = 'h|k\n-|-\n' + 'v|w\n' * 3000
        result = self.make(FakePopen({'identity': (0, big, '')}))('SELECT 1', mutating=False, timeout=1.0, table=True)
        self.assertEqual(result.stdout, big)
        self.assertGreater(len(result.stdout), 4000)
        limit = self.make(FakePopen({'identity': (0, 'x' * 1000000, '')}))('SELECT 1', mutating=False, timeout=1.0, table=True)
        self.assertEqual(len(limit.stdout), 1000000)
        with self.assertRaises(self.m.SqlFailure) as ctx:
            self.make(FakePopen({'identity': (0, 'x' * 1000001, '')}))('SELECT 1', mutating=False, timeout=1.0, table=True)
        self.assertIn('output too large', str(ctx.exception))

    def test_password_on_the_cut_boundary_is_masked_before_cutting(self):
        pw = fake_password()
        for offset in (3990, 3995, 3998, 4000):
            with self.subTest(offset=offset):
                text = 'x' * offset + pw + 'tail'
                popen = FakePopen({'identity': (0, text, '')})
                result = self.make(popen, masker=self.m.Masker([pw]))('SELECT 1', mutating=False, timeout=1.0)
                self.assertNotIn(pw[:4], result.stdout)

    def test_timeout_is_not_caught(self):
        popen = FakePopen(error=subprocess.TimeoutExpired(['sqlcmd'], 5))
        with self.assertRaises(subprocess.TimeoutExpired):
            self.make(popen)('SELECT 1', mutating=False, timeout=5.0)

    def test_os_error_is_not_caught(self):
        with self.assertRaises(OSError):
            self.make(FakePopen(error=OSError('gone')))('SELECT 1', mutating=False, timeout=5.0)

    def test_trust_certificate_flag_and_local_only(self):
        popen = FakePopen()
        self.make(popen, trust_server_certificate=True)('SELECT 1', mutating=False, timeout=1.0)
        self.assertIn('-C', popen.calls[0][0])
        remote = self.make(FakePopen(), trust_server_certificate=True, server_is_local=False, server_raw='dbhost.example')
        with self.assertRaises(self.m.Refusal):
            remote('SELECT 1', mutating=False, timeout=1.0)


class BackupSecretsEndToEnd(BackupBase):
    """K4: a password planted in the child environment and echoed by the (fake) tool never reaches any output."""

    def setUp(self):
        super().setUp()
        self.pw = fake_password('pw', '-', 'Qx', '9', '#k')
        self.environ = {'HOME': '/h/user', 'XDG_STATE_HOME': '/h/state', 'PATH': '/h/bin', 'SQLCMDUSER': 'lab_login',
                        'SQLCMDPASSWORD': self.pw, 'UNRELATED': 'keep-out'}

    def scenario(self, replies=None, fs=None, **kw):
        popen = FakePopen(replies)
        masker = self.m.Masker.from_env(self.environ, 'env')
        env = self.m.child_env(self.environ, 'env', 'linux')
        runner = self.m.SubprocessRunner(sqlcmd_path='/h/bin/sqlcmd', server_raw='localhost', auth='env', env=env, masker=masker, cwd=LOCAL_DIR,
                                         trust_server_certificate=False, server_is_local=True, popen=popen)
        state = self.m.RunState()
        fs = fs or FakeFs()
        err = io.StringIO()
        box = {}

        def call():
            box['result'] = self.m.run_backup(self.opts(auth='env', **kw), runner=self.m.guard_runner(runner, state), fs=fs, clock=lambda: FIXED_NOW,
                                              token_fn=lambda: FIXED_TOKEN, state=state, environ=self.environ, platform='linux')
            return 0
        code = self.m.run_guarded(call, state, err, masker)
        return code, err.getvalue(), box.get('result'), fs, popen, masker

    def test_success_outputs_do_not_contain_the_password(self):
        noisy = {'backup': (0, 'processed ' + self.pw, 'warn ' + self.pw), 'verify': (0, 'ok ' + self.pw, '')}
        code, err, result, fs, popen, masker = self.scenario(noisy)
        self.assertEqual(code, 0)
        everything = [err, self.m.format_result(result, True, masker), self.m.format_result(result, False, masker), json.dumps(result)]
        everything.extend(path + ' ' + data.decode('utf-8') for path, data in fs.written.items())
        everything.extend(path for path, _ in fs.made)
        for text in everything:
            self.assertNotIn(self.pw, text)
        for argv, _ in popen.calls:
            for item in argv:
                self.assertNotIn(self.pw, item)
                self.assertNotIn('lab_login', item)

    def test_failure_messages_do_not_contain_the_password(self):
        for kind in ('identity', 'backup', 'verify', 'header'):
            with self.subTest(kind=kind):
                bad = {kind: (1, 'Msg 18456, Level 14, State 1 ' + self.pw, 'Login failed for ' + self.pw + '\n' + ERROR_OUTPUT)}
                code, err, result, fs, popen, _ = self.scenario(bad)
                self.assertEqual(code, 1)
                self.assertNotIn(self.pw, err)
                self.assertTrue(err.startswith('SqlFailure'))
                self.assertEqual(fs.written, {})
                for argv, _kwargs in popen.calls:
                    self.assertFalse([a for a in argv if self.pw in a])

    def test_only_the_whitelist_reaches_the_child(self):
        _, _, _, _, popen, _ = self.scenario()
        for _argv, kwargs in popen.calls:
            self.assertEqual(set(kwargs['env']), {'PATH', 'SQLCMDUSER', 'SQLCMDPASSWORD', 'HOME'})
            self.assertIs(kwargs['shell'], False)

    def test_exception_text_with_the_password_is_masked_by_run_guarded(self):
        masker = self.m.Masker([self.pw])
        runner = FakeRunner(self.m, raises={'backup': OSError('device said ' + self.pw)})
        state = self.m.RunState()
        err = io.StringIO()
        fs = FakeFs()
        code = self.m.run_guarded(lambda: self.m.run_backup(self.opts(), runner=self.m.guard_runner(runner, state), fs=fs, clock=lambda: FIXED_NOW,
                                                            token_fn=lambda: FIXED_TOKEN, state=state, environ=self.environ, platform='linux'),
                                  state, err, masker)
        self.assertEqual(code, 1)
        self.assertNotIn(self.pw, err.getvalue())


class FormatResult(BackupBase):
    def test_json_is_valid_sorted_and_masked(self):
        pw = fake_password()
        masker = self.m.Masker([pw])
        result = {'status': 'PASS', 'path': '/h/bk/' + pw + '.bak', 'size': 7, 'remote': False, 'nested': {'z': 1, 'a': pw}, 'b': 'x'}
        text = self.m.format_result(result, True, masker)
        self.assertNotIn(pw, text)
        pairs = []
        loaded = json.loads(text, object_pairs_hook=lambda p: pairs.append([k for k, _ in p]) or dict(p))
        for keys in pairs:
            self.assertEqual(keys, sorted(keys))
        self.assertEqual(loaded['path'], '/h/bk/***.bak')
        self.assertEqual(loaded['nested'], {'a': '***', 'z': 1})
        self.assertEqual(loaded['size'], 7)

    def test_text_mode_is_masked_and_carries_the_essentials(self):
        pw = fake_password()
        result = self.do_backup()
        text = self.m.format_result(result, False, self.m.Masker([pw]))
        self.assertIsInstance(text, str)
        for needed in ('PASS', LOCAL_PATH, result['restore_command']):
            self.assertIn(needed, text)
        leaky = dict(result, coordinator_notice='note ' + pw)
        self.assertNotIn(pw, self.m.format_result(leaky, False, self.m.Masker([pw])))

    def test_json_of_a_real_result_round_trips(self):
        result = self.do_backup()
        loaded = json.loads(self.m.format_result(result, True, self.m.Masker([])))
        for key in ('status', 'path', 'size', 'remote', 'verified_locally', 'verify', 'restore_command', 'coordinator_notice'):
            self.assertEqual(loaded[key], result[key])

    def test_json_is_deterministic(self):
        result = self.do_backup()
        masker = self.m.Masker([])
        self.assertEqual(self.m.format_result(result, True, masker), self.m.format_result(result, True, masker))



# ---------------------------------------------------------------- S3: restore (sidecar parser, machine names, RealFs, run_restore)

sys.path.insert(0, str(ROOT / 'tests'))
import hashlib  # noqa: E402
import tempfile  # noqa: E402
from unittest import mock  # noqa: E402
import tmp_paths  # noqa: E402

SRC_NAME = 'appdb-snap-20261121-090000-d4e5f6.bak'
SRC_PATH = LOCAL_DIR + '/' + SRC_NAME
SAFETY_NAME = 'appdb-pre-restore-20261122-102030-a1b2c3.bak'
SAFETY_PATH = LOCAL_DIR + '/' + SAFETY_NAME
SECOND_TOKEN = 'f0e1d2'
SECOND_SAFETY_PATH = LOCAL_DIR + '/appdb-pre-restore-20261122-102030-' + SECOND_TOKEN + '.bak'
WIN_DIR = 'D:\\h\\bk'
WIN_SRC = WIN_DIR + '\\' + SRC_NAME
WIN_SAFETY = WIN_DIR + '\\' + SAFETY_NAME
STATE_COLUMNS = ['state_desc', 'user_access_desc', 'recovery_model_desc']
RESTORE_HEADER_COLUMNS = HEADER_COLUMNS + ['MachineName']
RESTORE_HEADER_ROW = HEADER_ROW + ['LAB01']
BATCH_ERROR = 'Msg 3013, Level 16, State 1, Server lab, Line 1\nRESTORE DATABASE is terminating abnormally.'
FATAL_ERROR = 'Msg 596, Level 21, State 1, Server lab, Line 1\nCannot continue the execution because the session is in the kill state.'
UNCERTAIN = re.compile(r'uncertain|niepewn|may have changed|m\u00f3g\u0142 si\u0119 zmieni\u0107', re.IGNORECASE)
NO_SIDECAR = object()
DEFAULT_SIDECAR = object()


def state_out(state='ONLINE', access='MULTI_USER', recovery='SIMPLE'):
    return table(STATE_COLUMNS, [[state, access, recovery]])


def restore_header_out(**changes):
    row = dict(zip(RESTORE_HEADER_COLUMNS, RESTORE_HEADER_ROW))
    row.update(changes)
    return table(RESTORE_HEADER_COLUMNS, [[row[name] for name in RESTORE_HEADER_COLUMNS]])


def source_sidecar(path=SRC_PATH, **changes):
    name = re.split(r'[\\/]', path)[-1]
    data = {
        'schema': 1, 'database': 'appdb', 'label': 'snap',
        'server': {'raw': 'localhost', 'canonical': 'local', 'engine_server_name': 'LAB01', 'machine_name': 'LAB01', 'instance_name': None},
        'file': name, 'size': BAK_SIZE, 'created_utc': '2026-11-21T09:00:00Z',
        'backup_set_guid': HEADER_ROW[6], 'first_lsn': '100', 'last_lsn': '200', 'backup_finish_date': HEADER_ROW[7],
        'remote': False, 'server_path': path, 'copy_only': True,
    }
    data.update(changes)
    return data


def sidecar_server(**changes):
    server = dict(source_sidecar()['server'])
    server.update(changes)
    return server


def classify_restore(sql):
    if sql.startswith('SELECT state_desc'):
        return 'state'
    if sql.startswith('SELECT @@SERVERNAME'):
        return 'identity'
    if sql.startswith('BEGIN TRY'):
        return 'restore'
    return classify(sql)


class RestoreRunner:
    """Records every call; answers per kind from a script (a list of (rc, out, err) tuples or exceptions; the last item repeats)."""

    def __init__(self, m, script=None, masker=None):
        self.m = m
        self.calls = []
        self.masker = masker
        self.seen = {}
        self.script = {'state': [(0, state_out(), '')], 'identity': [(0, IDENTITY_OUT, '')], 'header': [(0, restore_header_out(), '')],
                       'verify': [(0, '', '')], 'backup': [(0, '', '')], 'restore': [(0, '', '')]}
        for kind, items in (script or {}).items():
            self.script[kind] = items if isinstance(items, list) else [items]

    def __call__(self, sql, *, mutating, timeout, table=False):
        kind = classify_restore(sql)
        self.calls.append({'kind': kind, 'sql': sql, 'mutating': mutating, 'timeout': timeout, 'table': table})
        index = self.seen.get(kind, 0)
        self.seen[kind] = index + 1
        items = self.script[kind]
        item = items[min(index, len(items) - 1)]
        if isinstance(item, BaseException):
            raise item
        rc, out, err = item
        if self.masker is not None:
            out, err = self.masker.mask(out), self.masker.mask(err)
        return self.m.RunResult(rc, out, err)

    @property
    def kinds(self):
        return [c['kind'] for c in self.calls]

    @property
    def mutating(self):
        return [c['kind'] for c in self.calls if c['mutating']]


class RestoreFs(FakeFs):
    def __init__(self, src=SRC_PATH, sidecar=DEFAULT_SIDECAR, **kw):
        super().__init__(**kw)
        self.files = set([src])
        self.blobs = {}
        self.reads = []
        if sidecar is DEFAULT_SIDECAR:
            sidecar = source_sidecar(src)
        if sidecar is not NO_SIDECAR:
            self.blobs[src + '.json'] = sidecar if isinstance(sidecar, bytes) else json.dumps(sidecar).encode('utf-8')

    def isfile(self, path):
        self.reads.append(('isfile', path))
        return path in self.files or path in self.blobs

    def read_bytes(self, path, limit):
        self.reads.append(('read_bytes', path, limit))
        if path not in self.blobs:
            raise FileNotFoundError(path)
        return self.blobs[path][:limit + 1]


class RestoreBase(Base):
    def opts(self, **kw):
        values = {'database': 'appdb', 'file': SRC_PATH, 'confirm': 'appdb', 'dir': LOCAL_DIR}
        values.update(kw)
        return self.m.RestoreOptions(**values)

    def do_restore(self, runner=None, fs=None, guard=True, state=None, platform='linux', environ=None, token=FIXED_TOKEN,
                   machine_name='LAB01', **kw):
        runner = runner or RestoreRunner(self.m)
        fs = fs or RestoreFs()
        state = state or self.m.RunState()
        env = {'HOME': '/h/user', 'XDG_STATE_HOME': '/h/state'} if environ is None else environ
        self.runner, self.fs, self.state = runner, fs, state
        self.emitted = []

        def emit(line):
            self.emitted.append((line, [c['kind'] for c in runner.calls]))
        call = self.m.guard_runner(runner, state) if guard else runner
        return self.m.run_restore(self.opts(**kw), runner=call, fs=fs, clock=lambda: FIXED_NOW, token_fn=lambda: token, state=state,
                                  environ=env, platform=platform, machine_name=machine_name, emit=emit)

    def do_code(self, **kw):
        state = kw.setdefault('state', self.m.RunState())
        err = io.StringIO()
        code = self.m.run_guarded(lambda: self.do_restore(**kw), state, err, self.m.Masker([]))
        return code, err.getvalue()

    def assert_blocked(self, script=None, fs_args=None, no_runner=True, platform='linux', **kw):
        """Refusal (code 2) with no mutating call and no write; with no_runner also without any runner call."""
        fs_args = fs_args or {}
        runner, fs = RestoreRunner(self.m, script), RestoreFs(**fs_args)
        with self.assertRaises(self.m.Refusal) as ctx:
            self.do_restore(runner=runner, fs=fs, platform=platform, **kw)
        self.assertEqual(runner.mutating, [])
        self.assertIs(self.state.mutated, False)
        self.assertEqual((fs.made, fs.written, fs.sha_calls), ([], {}, []))
        if no_runner:
            self.assertEqual(runner.calls, [])
        runner2 = RestoreRunner(self.m, script)
        code, _ = self.do_code(runner=runner2, fs=RestoreFs(**fs_args), platform=platform, **kw)
        self.assertEqual(code, 2)
        self.assertEqual(runner2.mutating, [])
        return runner, str(ctx.exception)

    def assert_no_mutation_failure(self, script, expected_kinds, exc=None, **kw):
        runner = RestoreRunner(self.m, script)
        with self.assertRaises(exc or self.m.SqlFailure):
            self.do_restore(runner=runner, **kw)
        self.assertEqual(runner.kinds, expected_kinds)
        self.assertEqual(runner.mutating, [])
        self.assertIs(self.state.mutated, False)
        self.assertEqual(self.fs.written, {})
        return runner


class SidecarParser(Base):
    def raw(self, **changes):
        return json.dumps(source_sidecar(**changes)).encode('utf-8')

    def test_valid_sidecar_round_trips_to_a_dict(self):
        data = self.m.read_sidecar(self.raw())
        self.assertIsInstance(data, dict)
        self.assertEqual(data, source_sidecar())

    def test_optional_sha256_and_nullable_fields_are_accepted(self):
        self.assertEqual(self.m.read_sidecar(self.raw(sha256=FAKE_SHA))['sha256'], FAKE_SHA)
        self.assertIsNone(self.m.read_sidecar(self.raw(size=None))['size'])
        self.assertEqual(self.m.read_sidecar(self.raw(server=sidecar_server(instance_name='SQLEXPRESS')))['server']['instance_name'], 'SQLEXPRESS')
        self.assertIs(self.m.read_sidecar(self.raw(remote=True))['remote'], True)

    def test_size_limit_is_65536_bytes(self):
        base = self.raw()
        self.assertLess(len(base), 65536)
        exact = base + b' ' * (65536 - len(base))
        self.assertEqual(len(exact), 65536)
        self.assertEqual(self.m.read_sidecar(exact), source_sidecar())
        with self.assertRaises(self.m.Refusal):
            self.m.read_sidecar(exact + b' ')

    def test_oversized_document_is_refused(self):
        with self.assertRaises(self.m.Refusal):
            self.m.read_sidecar(self.raw(label='x' * 70000))

    def test_not_a_json_object_is_refused(self):
        for text in (b'[]', b'"x"', b'1', b'null', b'true', b'[{"schema": 1}]'):
            with self.subTest(text=text):
                with self.assertRaises(self.m.Refusal):
                    self.m.read_sidecar(text)

    def test_broken_documents_are_refused(self):
        for text in (b'', b'{', b'{"schema": 1,}', b'\xff\xfe\x00', b'not json'):
            with self.subTest(text=text):
                with self.assertRaises(self.m.Refusal):
                    self.m.read_sidecar(text)

    def test_schema_must_be_the_integer_1(self):
        for value in ('1', 2, 0, None, 1.5, [1]):
            with self.subTest(value=value):
                with self.assertRaises(self.m.Refusal):
                    self.m.read_sidecar(self.raw(schema=value))
        data = source_sidecar()
        del data['schema']
        with self.assertRaises(self.m.Refusal):
            self.m.read_sidecar(json.dumps(data).encode('utf-8'))

    def test_unknown_top_level_key_is_refused(self):
        for key in ('sidecar_path', 'command', 'extra', 'server_path2'):
            with self.subTest(key=key):
                with self.assertRaises(self.m.Refusal):
                    self.m.read_sidecar(self.raw(**{key: 'x'}))

    def test_unknown_key_inside_server_is_refused(self):
        with self.assertRaises(self.m.Refusal):
            self.m.read_sidecar(self.raw(server=sidecar_server(extra='x')))

    def test_wrong_types_are_refused(self):
        cases = {'database': 5, 'label': 3, 'file': ['a'], 'created_utc': 5, 'backup_set_guid': 7, 'server_path': 9,
                 'size': 'big', 'remote': 'false', 'copy_only': 'yes', 'sha256': 12, 'server': 'local'}
        for key, value in cases.items():
            with self.subTest(key=key):
                with self.assertRaises(self.m.Refusal):
                    self.m.read_sidecar(self.raw(**{key: value}))
        for key in ('raw', 'canonical', 'engine_server_name', 'machine_name'):
            with self.subTest(server_key=key):
                with self.assertRaises(self.m.Refusal):
                    self.m.read_sidecar(self.raw(server=sidecar_server(**{key: 5})))

    def test_server_must_be_an_object(self):
        for value in (None, [], 'x', 3):
            with self.subTest(value=value):
                with self.assertRaises(self.m.Refusal):
                    self.m.read_sidecar(self.raw(server=value))

    def test_paths_in_the_sidecar_are_returned_as_data_not_followed(self):
        data = self.m.read_sidecar(self.raw(server_path='/h/other/evil.bak'))
        self.assertEqual(data['server_path'], '/h/other/evil.bak')


class MachineNamesMatch(Base):
    def test_equal_names_ignoring_case(self):
        self.assertIs(self.m.machine_names_match('LAB01', 'lab01'), True)
        self.assertIs(self.m.machine_names_match('Lab01', 'LAB01'), True)

    def test_different_names(self):
        self.assertIs(self.m.machine_names_match('LAB01', 'LAB02'), False)
        self.assertIs(self.m.machine_names_match('LAB01', 'LAB011'), False)

    def test_both_names_are_cut_to_15_characters(self):
        self.assertIs(self.m.machine_names_match('ABCDEFGHIJKLMNOPQRS', 'abcdefghijklmno'), True)
        self.assertIs(self.m.machine_names_match('abcdefghijklmno', 'ABCDEFGHIJKLMNOPQRS'), True)
        self.assertIs(self.m.machine_names_match('ABCDEFGHIJKLMNO1', 'ABCDEFGHIJKLMNO2'), True)
        self.assertIs(self.m.machine_names_match('ABCDEFGHIJKLMNO', 'ABCDEFGHIJKLMNP'), False)
        self.assertIs(self.m.machine_names_match('ABCDEFGHIJKLMN', 'ABCDEFGHIJKLMNO'), False)

    def test_empty_names_never_match(self):
        self.assertIs(self.m.machine_names_match('', 'LAB01'), False)
        self.assertIs(self.m.machine_names_match('LAB01', ''), False)
        self.assertIs(self.m.machine_names_match('', ''), False)


class RealFsTests(Base):
    def setUp(self):
        super().setUp()
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name).resolve()
        self.fs = self.m.RealFs()

    def path(self, name):
        return str(tmp_paths.child(self.root, name))

    def listing(self):
        return sorted(os.listdir(str(self.root)))

    def no_umask(self):
        previous = os.umask(0)
        self.addCleanup(os.umask, previous)

    @unittest.skipIf(os.name == 'nt', 'POSIX file modes')
    def test_makedirs_creates_the_leaf_with_mode_0700(self):
        self.no_umask()
        target = self.path('copies')
        self.fs.makedirs(target, 0o700)
        self.assertTrue(os.path.isdir(target))
        self.assertEqual(os.stat(target).st_mode & 0o777, 0o700)

    def test_makedirs_is_idempotent_and_makes_nested_directories(self):
        target = str(tmp_paths.descend(self.root, 'a/b/c'))
        self.fs.makedirs(target, 0o700)
        self.fs.makedirs(target, 0o700)
        self.assertTrue(os.path.isdir(target))

    def test_write_atomic_writes_the_bytes_and_leaves_no_temporary_file(self):
        target = self.path('x.bak.json')
        self.fs.write_atomic(target, b'{"a": 1}')
        with open(target, 'rb') as handle:
            self.assertEqual(handle.read(), b'{"a": 1}')
        self.assertEqual(self.listing(), ['x.bak.json'])

    @unittest.skipIf(os.name == 'nt', 'POSIX file modes')
    def test_write_atomic_file_mode_is_0600_even_with_a_permissive_umask(self):
        self.no_umask()
        target = self.path('new.json')
        self.fs.write_atomic(target, b'1')
        self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)
        old = self.path('old.json')
        with open(old, 'wb') as handle:
            handle.write(b'old')
        os.chmod(old, 0o644)
        self.fs.write_atomic(old, b'new')
        self.assertEqual(os.stat(old).st_mode & 0o777, 0o600)

    def test_write_atomic_replaces_an_existing_file(self):
        target = self.path('r.json')
        self.fs.write_atomic(target, b'one')
        self.fs.write_atomic(target, b'two')
        with open(target, 'rb') as handle:
            self.assertEqual(handle.read(), b'two')
        self.assertEqual(self.listing(), ['r.json'])

    def test_write_atomic_swaps_in_a_complete_file_with_one_replace(self):
        target = self.path('s.json')
        self.fs.write_atomic(target, b'old')
        seen = {}
        real_replace = os.replace

        def spy(src, dst):
            with open(dst, 'rb') as handle:
                seen['target_before'] = handle.read()
            with open(src, 'rb') as handle:
                seen['tmp_content'] = handle.read()
            seen['same_dir'] = os.path.dirname(os.path.abspath(src)) == os.path.dirname(os.path.abspath(dst))
            seen['listing'] = self.listing()
            return real_replace(src, dst)
        with mock.patch('os.replace', side_effect=spy):
            self.fs.write_atomic(target, b'new content')
        self.assertEqual(seen['target_before'], b'old')
        self.assertEqual(seen['tmp_content'], b'new content')
        self.assertIs(seen['same_dir'], True)
        self.assertEqual(len(seen['listing']), 2)
        with open(target, 'rb') as handle:
            self.assertEqual(handle.read(), b'new content')

    def test_failed_replace_keeps_the_original_and_removes_the_temporary_file(self):
        target = self.path('k.json')
        self.fs.write_atomic(target, b'keep')
        with mock.patch('os.replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                self.fs.write_atomic(target, b'lost')
        with open(target, 'rb') as handle:
            self.assertEqual(handle.read(), b'keep')
        self.assertEqual(self.listing(), ['k.json'])

    def test_failed_first_write_leaves_nothing_behind(self):
        target = self.path('n.json')
        with mock.patch('os.replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                self.fs.write_atomic(target, b'x')
        self.assertEqual(self.listing(), [])

    def test_text_instead_of_bytes_fails_without_leftovers(self):
        target = self.path('t.json')
        with self.assertRaises(TypeError):
            self.fs.write_atomic(target, 'text')
        self.assertEqual(self.listing(), [])

    def test_write_atomic_creates_a_missing_parent_directory_with_mode_0700(self):
        self.no_umask()
        parent = self.path('fresh')
        self.fs.write_atomic(os.path.join(parent, 'f.json'), b'1')
        self.assertEqual(os.listdir(parent), ['f.json'])
        if os.name != 'nt':
            self.assertEqual(os.stat(parent).st_mode & 0o777, 0o700)

    def test_sha256_of_known_and_chunked_content(self):
        known = self.path('abc.bin')
        with open(known, 'wb') as handle:
            handle.write(b'abc')
        self.assertEqual(self.fs.sha256(known), hashlib.sha256(b'abc').hexdigest())
        empty = self.path('empty.bin')
        with open(empty, 'wb') as handle:
            handle.write(b'')
        self.assertEqual(self.fs.sha256(empty), hashlib.sha256(b'').hexdigest())
        big = self.path('big.bin')
        data = bytes(bytearray(range(256))) * (10 * 1024 + 7)
        with open(big, 'wb') as handle:
            handle.write(data)
        self.assertGreater(len(data), 2 * 1024 * 1024)
        self.assertEqual(self.fs.sha256(big), hashlib.sha256(data).hexdigest())

    def test_read_bytes_reads_at_most_limit_plus_one_bytes(self):
        target = self.path('r.bin')
        with open(target, 'wb') as handle:
            handle.write(b'0123456789' * 10)
        self.assertEqual(self.fs.read_bytes(target, 10), b'0123456789' + b'0')
        self.assertEqual(len(self.fs.read_bytes(target, 99)), 100)
        self.assertEqual(len(self.fs.read_bytes(target, 100)), 100)
        self.assertEqual(len(self.fs.read_bytes(target, 1000)), 100)
        self.assertLessEqual(len(self.fs.read_bytes(target, 0)), 1)

    def test_read_bytes_of_a_missing_file_raises_oserror(self):
        with self.assertRaises(OSError):
            self.fs.read_bytes(self.path('missing.bin'), 10)

    def test_isfile(self):
        target = self.path('f.bin')
        with open(target, 'wb') as handle:
            handle.write(b'1')
        directory = str(tmp_paths.make_dir(self.root, 'dir'))
        self.assertIs(self.fs.isfile(target), True)
        self.assertIs(self.fs.isfile(directory), False)
        self.assertIs(self.fs.isfile(self.path('missing.bin')), False)


FLOW_SHAPE = [('state', False, True), ('identity', False, True), ('header', False, True), ('verify', False, False),
              ('identity', False, True), ('backup', True, False), ('verify', False, False), ('header', False, True),
              ('restore', True, False), ('state', False, True)]


class RestoreSuccess(RestoreBase):
    def test_exact_call_order_flags_and_sql(self):
        result = self.do_restore()
        self.assertEqual([(c['kind'], c['mutating'], c['table']) for c in self.runner.calls], FLOW_SHAPE)
        self.assertEqual([c['sql'] for c in self.runner.calls], [
            self.m.build_state_sql('appdb'), self.m.build_identity_sql(),
            self.m.build_headeronly_sql(SRC_PATH), self.m.build_verify_sql(SRC_PATH),
            self.m.build_identity_sql(), self.m.build_backup_sql('appdb', SAFETY_PATH),
            self.m.build_verify_sql(SAFETY_PATH), self.m.build_headeronly_sql(SAFETY_PATH),
            self.m.build_restore_batch('appdb', SRC_PATH), self.m.build_state_sql('appdb')])
        self.assertEqual(result['status'], 'PASS')

    def test_destructive_batch_is_the_only_restore_and_uses_file_1(self):
        self.do_restore()
        batch = [c for c in self.runner.calls if c['kind'] == 'restore']
        self.assertEqual(len(batch), 1)
        self.assertIn('FILE = 1', batch[0]['sql'])
        self.assertIn('REPLACE', batch[0]['sql'])
        self.assertIs(batch[0]['table'], False)
        self.assertEqual(self.runner.mutating, ['backup', 'restore'])

    def test_timeouts_are_routed_to_the_right_steps(self):
        self.do_restore(restore_timeout=777.0, timeout=123.0, state_timeout=11.0)
        by_kind = {}
        for call in self.runner.calls:
            by_kind.setdefault(call['kind'], []).append(call['timeout'])
        self.assertEqual(by_kind['restore'], [777.0])
        self.assertEqual(by_kind['backup'], [123.0])
        for call in self.runner.calls:
            self.assertGreater(call['timeout'], 0)

    def test_result_fields(self):
        result = self.do_restore()
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['database'], 'appdb')
        self.assertEqual(result['file'], SRC_PATH)
        self.assertEqual(result['safety_backup_path'], SAFETY_PATH)
        self.assertIn('ONLINE', str(result['previous_state']))
        self.assertEqual(result['warnings'], [])
        self.assertIsInstance(result['coordinator_notice'], str)
        self.assertTrue(result['coordinator_notice'])

    def test_state_marks_the_run_as_mutated(self):
        self.do_restore()
        self.assertIs(self.state.mutated, True)

    def test_safety_backup_is_a_real_backup_with_a_sidecar_and_the_source_is_untouched(self):
        self.do_restore()
        self.assertEqual(self.fs.write_log, [SAFETY_PATH + '.json'])
        written = json.loads(self.fs.written[SAFETY_PATH + '.json'].decode('utf-8'))
        self.assertEqual(written['label'], 'pre-restore')
        self.assertEqual(written['database'], 'appdb')
        self.assertIn('COPY_ONLY', self.runner.calls[5]['sql'])
        self.assertNotIn(SRC_PATH, self.fs.written)
        self.assertNotIn(SRC_PATH + '.json', self.fs.written)

    def test_safety_backup_path_is_emitted_before_the_destructive_batch(self):
        self.do_restore()
        lines = [(line, kinds) for line, kinds in self.emitted if SAFETY_PATH in line and 'safety backup' in line.lower()]
        self.assertTrue(lines, self.emitted)
        for _, kinds in lines:
            self.assertNotIn('restore', kinds)

    def test_two_restores_on_the_same_day_do_not_overwrite_the_safety_backup(self):
        fs = RestoreFs()
        first = self.do_restore(fs=fs, token=FIXED_TOKEN)
        runner = self.runner
        second = self.do_restore(fs=fs, token=SECOND_TOKEN)
        self.assertEqual(first['safety_backup_path'], SAFETY_PATH)
        self.assertEqual(second['safety_backup_path'], SECOND_SAFETY_PATH)
        self.assertNotEqual(first['safety_backup_path'], second['safety_backup_path'])
        self.assertIn(SAFETY_PATH + '.json', fs.written)
        self.assertIn(SECOND_SAFETY_PATH + '.json', fs.written)
        self.assertEqual(len(fs.write_log), 2)
        self.assertEqual(self.runner.calls[5]['sql'], self.m.build_backup_sql('appdb', SECOND_SAFETY_PATH))
        self.assertEqual(runner.calls[5]['sql'], self.m.build_backup_sql('appdb', SAFETY_PATH))

    def test_sidecar_is_read_with_a_size_limit_and_no_path_from_it_is_touched(self):
        fs = RestoreFs(sidecar=source_sidecar(server_path='/h/other/evil.bak'))
        self.do_restore(fs=fs)
        for entry in fs.reads:
            self.assertNotIn('/h/other', entry[1])
        for kind, path in fs.checked:
            self.assertNotIn('/h/other', path)
        reads = [entry for entry in fs.reads if entry[0] == 'read_bytes']
        self.assertEqual(reads, [('read_bytes', SRC_PATH + '.json', 65537)])
        self.assertEqual(self.runner.calls[2]['sql'], self.m.build_headeronly_sql(SRC_PATH))

    def test_database_name_in_the_sidecar_is_compared_ignoring_case(self):
        result = self.do_restore(fs=RestoreFs(sidecar=source_sidecar(database='APPDB')))
        self.assertEqual(result['status'], 'PASS')

    def test_machine_name_comparison_ignores_case_and_the_netbios_limit(self):
        for engine in ('lab01', 'LAB01'):
            with self.subTest(engine=engine):
                out = table(['server_name', 'machine_name', 'instance_name'], [['LAB01', engine, 'NULL']])
                self.assertEqual(self.do_restore(runner=RestoreRunner(self.m, {'identity': (0, out, '')}))['status'], 'PASS')
        long_name = 'ABCDEFGHIJKLMNOPQRS'
        out = table(['server_name', 'machine_name', 'instance_name'], [['LAB01', long_name, 'NULL']])
        result = self.do_restore(runner=RestoreRunner(self.m, {'identity': (0, out, '')}), machine_name='abcdefghijklmno')
        self.assertEqual(result['status'], 'PASS')

    def test_default_directory_is_used_when_dir_is_missing(self):
        path = STATE_DIR + '/' + SRC_NAME
        result = self.do_restore(fs=RestoreFs(src=path), dir=None, file=path)
        self.assertEqual(result['safety_backup_path'], STATE_DIR + '/' + SAFETY_NAME)

    def test_windows_paths_use_backslashes(self):
        fs = RestoreFs(src=WIN_SRC)
        result = self.do_restore(fs=fs, platform='win32', dir=WIN_DIR, file=WIN_SRC, environ={'LOCALAPPDATA': 'D:\\h\\local'})
        self.assertEqual(result['safety_backup_path'], WIN_SAFETY)
        self.assertEqual(self.runner.calls[2]['sql'], self.m.build_headeronly_sql(WIN_SRC))
        self.assertEqual(self.runner.calls[8]['sql'], self.m.build_restore_batch('appdb', WIN_SRC))

    def test_full_recovery_model_gives_a_warning_and_still_restores(self):
        runner = RestoreRunner(self.m, {'state': (0, state_out(recovery='FULL'), '')})
        result = self.do_restore(runner=runner)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(len(result['warnings']), 1)
        self.assertEqual(runner.kinds, [kind for kind, _, _ in FLOW_SHAPE])

    def test_no_safety_backup_skips_the_copy_and_warns(self):
        result = self.do_restore(no_safety_backup=True)
        self.assertEqual(self.runner.kinds, ['state', 'identity', 'header', 'verify', 'restore', 'state'])
        self.assertEqual(self.runner.mutating, ['restore'])
        self.assertIsNone(result['safety_backup_path'])
        self.assertEqual(len(result['warnings']), 1)
        self.assertEqual(self.fs.written, {})
        for line, _ in self.emitted:
            self.assertNotIn(SAFETY_PATH, line)


class RestoreValidationRefusals(RestoreBase):
    def test_remote_hosts_are_always_refused(self):
        for server in ('remote-host', 'dbhost.example,1433', 'localhost.evil.example', '10.1.2.3', 'tcp:localhost'):
            with self.subTest(server=server):
                self.assert_blocked(server=server)

    def test_confirm_must_equal_the_database_exactly(self):
        for confirm in (None, '', 'APPDB', 'appdb ', 'other', 'appd'):
            with self.subTest(confirm=confirm):
                self.assert_blocked(confirm=confirm)

    def test_bad_database_names(self):
        for name in ('master', 'tempdb', "a'b", 'a]b', '', 'm\u0430ster'):
            with self.subTest(name=ascii(name)):
                self.assert_blocked(database=name, confirm=name)

    def test_file_outside_the_backup_directory(self):
        for path in ('/h/other/' + SRC_NAME, LOCAL_DIR + '/sub/' + SRC_NAME, '/h/' + SRC_NAME):
            with self.subTest(path=path):
                _, message = self.assert_blocked(file=path, fs_args={'src': path})
                self.assertIn('backup directory', message)

    def test_file_outside_the_default_directory(self):
        self.assert_blocked(dir=None, file=SRC_PATH)

    def test_traversal_in_the_file_path(self):
        self.assert_blocked(file=LOCAL_DIR + '/../other/' + SRC_NAME, fs_args={'src': LOCAL_DIR + '/../other/' + SRC_NAME})

    def test_not_a_regular_file(self):
        runner, fs = RestoreRunner(self.m), RestoreFs()
        fs.files = set()
        with self.assertRaises(self.m.Refusal):
            self.do_restore(runner=runner, fs=fs)
        self.assertEqual(runner.calls, [])
        self.assertEqual((fs.made, fs.written), ([], {}))
        runner2, fs2 = RestoreRunner(self.m), RestoreFs()
        fs2.files = set()
        code, _ = self.do_code(runner=runner2, fs=fs2)
        self.assertEqual(code, 2)

    def test_bad_server_paths(self):
        for path in ("/h/bk/it's.bak", 'bk/' + SRC_NAME, SRC_NAME, '/h/bk/a;b.bak', '/h/bk/a\nb.bak', '/h/bk/' + SRC_NAME + '.'):
            with self.subTest(path=ascii(path)):
                self.assert_blocked(file=path, fs_args={'src': path})

    def test_bad_windows_paths(self):
        for path in ('\\\\h\\share\\bk\\' + SRC_NAME, '\\\\?\\D:\\h\\bk\\' + SRC_NAME, 'bk\\' + SRC_NAME, WIN_DIR + '\\NUL.bak',
                     WIN_DIR + '\\' + SRC_NAME + ':ads', "D:\\h\\bk\\it's.bak"):
            with self.subTest(path=path):
                self.assert_blocked(file=path, platform='win32', dir=WIN_DIR, fs_args={'src': path}, environ={'LOCALAPPDATA': 'D:\\h\\local'})

    def test_backup_directory_inside_a_repository(self):
        runner, fs = RestoreRunner(self.m), RestoreFs(git_dirs=['/h/bk/.git'])
        with self.assertRaises(self.m.Refusal):
            self.do_restore(runner=runner, fs=fs)
        self.assertEqual(runner.calls, [])
        self.assertEqual((fs.made, fs.written), ([], {}))

    def test_bad_backup_directory(self):
        for directory in ('rel/dir', "/h/o'x", '/h/../bk'):
            with self.subTest(directory=directory):
                self.assert_blocked(dir=directory, file=directory + '/' + SRC_NAME, fs_args={'src': directory + '/' + SRC_NAME})

    def test_refusal_message_has_no_secret(self):
        pw = fake_password()
        environ = {'HOME': '/h/user', 'SQLCMDUSER': 'lab_login', 'SQLCMDPASSWORD': pw}
        _, message = self.assert_blocked(environ=environ, auth='env', confirm='other')
        self.assertNotIn(pw, message)
        self.assertNotIn('lab_login', message)


class RestoreSidecarChecks(RestoreBase):
    def test_missing_sidecar_is_refused_before_any_runner_call(self):
        _, message = self.assert_blocked(fs_args={'sidecar': NO_SIDECAR})
        self.assertIn('sidecar', message.lower())

    def test_invalid_sidecars_are_refused_before_any_runner_call(self):
        bad = {'not json': b'{', 'not an object': b'[]', 'oversized': json.dumps(source_sidecar(label='x' * 70000)).encode('utf-8'),
               'unknown key': json.dumps(source_sidecar(extra='x')).encode('utf-8'),
               'wrong schema': json.dumps(source_sidecar(schema=2)).encode('utf-8')}
        for name, blob in bad.items():
            with self.subTest(case=name):
                self.assert_blocked(fs_args={'sidecar': blob})

    def test_sidecar_of_another_database(self):
        self.assert_blocked(fs_args={'sidecar': source_sidecar(database='otherdb')})

    def test_sidecar_of_another_server(self):
        for server in (sidecar_server(canonical='remote-host'), sidecar_server(canonical='local:SQLEXPRESS')):
            with self.subTest(canonical=server['canonical']):
                _, message = self.assert_blocked(fs_args={'sidecar': source_sidecar(server=server)})
                self.assertIn('different server', message)

    def test_sidecar_with_another_size(self):
        self.assert_blocked(fs_args={'sidecar': source_sidecar(size=BAK_SIZE + 1)})
        self.assert_blocked(fs_args={'sidecar': source_sidecar(size=None)})

    def test_sidecar_naming_another_file(self):
        self.assert_blocked(fs_args={'sidecar': source_sidecar(file='other-20260101-000000-abcdef.bak')})

    def test_allow_missing_sidecar_proceeds_with_a_warning_and_compares_with_the_target_engine(self):
        result = self.do_restore(fs=RestoreFs(sidecar=NO_SIDECAR), allow_missing_sidecar=True)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(len(result['warnings']), 1)
        self.assertEqual(self.runner.kinds, [kind for kind, _, _ in FLOW_SHAPE])

    def test_allow_missing_sidecar_still_refuses_a_header_from_another_engine(self):
        for column, value in (('ServerName', 'OTHERSRV'), ('MachineName', 'OTHERBOX')):
            with self.subTest(column=column):
                runner, _ = self.assert_blocked(script={'header': (0, restore_header_out(**{column: value}), '')}, no_runner=False,
                                                fs_args={'sidecar': NO_SIDECAR}, allow_missing_sidecar=True)
                self.assertEqual(runner.kinds, ['state', 'identity', 'header'])

    def test_allow_machine_mismatch_turns_the_header_mismatch_into_a_warning(self):
        for column, value in (('ServerName', 'OTHERSRV'), ('MachineName', 'OTHERBOX')):
            with self.subTest(column=column):
                runner = RestoreRunner(self.m, {'header': (0, restore_header_out(**{column: value}), '')})
                result = self.do_restore(runner=runner, fs=RestoreFs(sidecar=NO_SIDECAR), allow_missing_sidecar=True, allow_machine_mismatch=True)
                self.assertEqual(result['status'], 'PASS')
                self.assertGreaterEqual(len(result['warnings']), 2)


class RestoreEngineChecks(RestoreBase):
    def test_database_that_does_not_exist_is_refused(self):
        runner, message = self.assert_blocked(script={'state': (0, table(STATE_COLUMNS, []), '')}, no_runner=False)
        self.assertEqual(runner.kinds, ['state'])
        self.assertIn('non-existent', message)

    def test_database_that_is_not_online_is_refused(self):
        for state in ('OFFLINE', 'RESTORING', 'SUSPECT', 'RECOVERING', 'EMERGENCY', 'RECOVERY_PENDING'):
            with self.subTest(state=state):
                runner, message = self.assert_blocked(script={'state': (0, state_out(state=state), '')}, no_runner=False)
                self.assertEqual(runner.kinds, ['state'])
                self.assertIn(state, message)

    def test_machine_name_of_the_engine_must_match_this_machine(self):
        out = table(['server_name', 'machine_name', 'instance_name'], [['LAB01', 'OTHERBOX', 'NULL']])
        runner, _ = self.assert_blocked(script={'identity': (0, out, '')}, no_runner=False)
        self.assertEqual(runner.kinds, ['state', 'identity'])

    def test_empty_local_machine_name_is_a_mismatch(self):
        runner, _ = self.assert_blocked(no_runner=False, machine_name='')
        self.assertEqual(runner.kinds, ['state', 'identity'])

    def test_allow_machine_mismatch_proceeds_with_a_warning(self):
        out = table(['server_name', 'machine_name', 'instance_name'], [['LAB01', 'OTHERBOX', 'NULL']])
        baseline = self.do_restore()
        result = self.do_restore(runner=RestoreRunner(self.m, {'identity': (0, out, '')}), allow_machine_mismatch=True)
        self.assertEqual(result['status'], 'PASS')
        self.assertGreater(len(result['warnings']), len(baseline['warnings']))

    def header_refusal(self, header):
        runner, message = self.assert_blocked(script={'header': (0, header, '')}, no_runner=False)
        self.assertEqual(runner.kinds, ['state', 'identity', 'header'])
        return message

    def test_two_backup_sets_are_refused(self):
        row = list(RESTORE_HEADER_ROW)
        message = self.header_refusal(table(RESTORE_HEADER_COLUMNS, [row, row]))
        self.assertIn('multiple backup sets', message)

    def test_header_with_no_rows_stops_before_any_mutation(self):
        runner = self.assert_no_mutation_failure({'header': (0, table(RESTORE_HEADER_COLUMNS, []), '')}, ['state', 'identity', 'header'],
                                                 exc=(self.m.Refusal, self.m.SqlFailure))
        self.assertEqual(runner.mutating, [])

    def test_backup_type_other_than_full_is_refused(self):
        for value in ('2', '5', '0'):
            with self.subTest(value=value):
                self.header_refusal(restore_header_out(BackupType=value))

    def test_backup_of_another_database_is_refused(self):
        message = self.header_refusal(restore_header_out(DatabaseName='otherdb'))
        self.assertIn('different database', message)

    def test_header_database_is_compared_ignoring_case(self):
        out = restore_header_out(DatabaseName='APPDB')
        self.assertEqual(self.do_restore(runner=RestoreRunner(self.m, {'header': (0, out, '')}))['status'], 'PASS')

    def test_header_identity_must_match_the_sidecar(self):
        for column, value in (('ServerName', 'OTHERSRV'), ('MachineName', 'OTHERBOX'),
                              ('BackupSetGUID', '99999999-2222-3333-4444-555555555555'), ('FirstLSN', '101'), ('LastLSN', '201')):
            with self.subTest(column=column):
                self.header_refusal(restore_header_out(**{column: value}))

    def test_field_with_a_separator_is_a_failure_before_mutation(self):
        out = table(RESTORE_HEADER_COLUMNS, [['a|b'] + RESTORE_HEADER_ROW[1:]])
        self.assert_no_mutation_failure({'header': (0, out, '')}, ['state', 'identity', 'header'])
        code, _ = self.do_code(runner=RestoreRunner(self.m, {'header': (0, out, '')}))
        self.assertEqual(code, 1)

    def test_header_command_failure_stops_before_mutation(self):
        self.assert_no_mutation_failure({'header': (1, '', ERROR_OUTPUT)}, ['state', 'identity', 'header'])

    def test_verifyonly_failure_is_exit_1_and_never_touches_the_database(self):
        for item in ((1, '', ERROR_OUTPUT), (0, 'Msg 3241, Level 16, State 5\nThe media family is incorrectly formed.', '')):
            with self.subTest(item=item):
                self.assert_no_mutation_failure({'verify': item}, ['state', 'identity', 'header', 'verify'])
        code, _ = self.do_code(runner=RestoreRunner(self.m, {'verify': (1, '', ERROR_OUTPUT)}))
        self.assertEqual(code, 1)


class RestoreSafetyBackup(RestoreBase):
    def stops_before_restore(self, script):
        runner = RestoreRunner(self.m, script)
        with self.assertRaises(self.m.SqlFailure):
            self.do_restore(runner=runner)
        self.assertNotIn('restore', runner.kinds)
        self.assertEqual(runner.mutating, ['backup'])
        self.assertIs(self.state.mutated, True)
        code, _ = self.do_code(runner=RestoreRunner(self.m, script))
        self.assertEqual(code, 1)
        return runner

    def test_safety_backup_failure_stops_the_restore(self):
        runner = self.stops_before_restore({'backup': (1, '', ERROR_OUTPUT)})
        self.assertEqual(runner.kinds[-1], 'backup')

    def test_safety_backup_error_text_with_rc_zero_stops_the_restore(self):
        self.stops_before_restore({'backup': (0, BATCH_ERROR, '')})

    def test_safety_verify_failure_stops_the_restore(self):
        runner = self.stops_before_restore({'verify': [(0, '', ''), (1, '', ERROR_OUTPUT)]})
        self.assertEqual(runner.kinds[-1], 'verify')

    def test_safety_header_failure_stops_the_restore(self):
        runner = self.stops_before_restore({'header': [(0, restore_header_out(), ''), (1, '', ERROR_OUTPUT)]})
        self.assertEqual(runner.kinds[-1], 'header')


class RestoreDryRun(RestoreBase):
    def attempt(self, **kw):
        runner, fs = RestoreRunner(self.m), RestoreFs()
        with self.assertRaises(self.m.NotRun) as ctx:
            self.do_restore(runner=runner, fs=fs, dry_run=True, **kw)
        return runner, fs, ctx.exception

    def test_dry_run_calls_nothing_and_writes_nothing(self):
        runner, fs, exc = self.attempt()
        self.assertEqual(runner.calls, [])
        self.assertEqual((fs.made, fs.written, fs.write_log, fs.sha_calls), ([], {}, [], []))
        self.assertIs(self.state.mutated, False)

    def test_dry_run_result_is_not_run_with_a_plan(self):
        _, _, exc = self.attempt()
        self.assertEqual(exc.result['status'], 'NOT_RUN')
        self.assertTrue(exc.result['plan'])
        self.assertGreaterEqual(len(exc.result['plan']), 3)

    def test_dry_run_exit_code_is_3(self):
        code, _ = self.do_code(runner=RestoreRunner(self.m), fs=RestoreFs(), dry_run=True)
        self.assertEqual(code, 3)

    def test_dry_run_still_validates_first(self):
        self.assert_blocked(dry_run=True, confirm='other')
        self.assert_blocked(dry_run=True, fs_args={'sidecar': NO_SIDECAR})
        self.assert_blocked(dry_run=True, server='remote-host')

    def test_dry_run_with_no_safety_backup(self):
        runner, _, exc = self.attempt(no_safety_backup=True)
        self.assertEqual(runner.calls, [])
        self.assertEqual(exc.result['status'], 'NOT_RUN')


class RestoreDestructiveFailures(RestoreBase):
    def failing(self, restore_item, state_item, **kw):
        runner = RestoreRunner(self.m, {'restore': [restore_item], 'state': [(0, state_out(), ''), state_item]})
        kw.setdefault('state_timeout', 11.0)
        with self.assertRaises(self.m.SqlFailure) as ctx:
            self.do_restore(runner=runner, **kw)
        return runner, str(ctx.exception)

    def assert_shape(self, runner, message, safety=SAFETY_PATH):
        kinds = runner.kinds
        position = kinds.index('restore')
        self.assertEqual(kinds[position + 1:], ['state'])
        last = runner.calls[-1]
        self.assertEqual(last['timeout'], 11.0)
        self.assertIs(last['mutating'], False)
        self.assertIs(last['table'], True)
        self.assertEqual(last['sql'], self.m.build_state_sql('appdb'))
        self.assertEqual(runner.mutating, ['backup', 'restore'])
        for call in runner.calls:
            if call['kind'] != 'restore':
                self.assertNotIn('MULTI_USER', call['sql'])
        if safety:
            self.assertIn(safety, message)
        self.assertIs(self.state.mutated, True)

    def test_rc_1_with_msg_3013_reports_the_actual_restoring_state(self):
        runner, message = self.failing((1, BATCH_ERROR, ''), (0, state_out('RESTORING'), ''))
        self.assert_shape(runner, message)
        self.assertIn('RESTORING', message)

    def test_error_line_with_rc_0_is_a_failure_too(self):
        for item in ((0, BATCH_ERROR, ''), (0, '', BATCH_ERROR), (1, '', BATCH_ERROR)):
            with self.subTest(item=item):
                runner, message = self.failing(item, (0, state_out('RESTORING'), ''))
                self.assert_shape(runner, message)
                self.assertIn('RESTORING', message)

    def test_foreign_session_holding_single_user_is_reported(self):
        runner, message = self.failing((1, BATCH_ERROR, ''), (0, state_out('ONLINE', 'SINGLE_USER'), ''))
        self.assert_shape(runner, message)
        self.assertIn('SINGLE_USER', message)

    def test_database_back_online_after_the_failure_is_still_a_failure(self):
        runner, message = self.failing((1, BATCH_ERROR, ''), (0, state_out(), ''))
        self.assert_shape(runner, message)
        self.assertIn('ONLINE', message)

    def test_unreadable_state_is_reported_as_unknown(self):
        items = {'rc 1': (1, '', ERROR_OUTPUT), 'no row': (0, table(STATE_COLUMNS, []), ''),
                 'timeout': subprocess.TimeoutExpired('sqlcmd', 11.0), 'oserror': OSError('gone')}
        for name, item in items.items():
            with self.subTest(case=name):
                runner, message = self.failing((1, BATCH_ERROR, ''), item)
                self.assert_shape(runner, message)
                self.assertIn('unknown', message)

    def test_severity_20_or_more_only_reads_the_state(self):
        runner, message = self.failing((1, FATAL_ERROR, ''), (0, state_out('RESTORING'), ''))
        self.assert_shape(runner, message)
        self.assertEqual(runner.mutating, ['backup', 'restore'])

    def test_timeout_keyboard_interrupt_and_oserror_in_the_destructive_step(self):
        pw = fake_password()
        raised = {'timeout': subprocess.TimeoutExpired(['sqlcmd', '-P', pw], 3600.0), 'interrupt': KeyboardInterrupt(), 'oserror': OSError('pipe broke')}
        for name, exc in raised.items():
            with self.subTest(exc=name):
                runner, message = self.failing(exc, (0, state_out('RESTORING'), ''))
                self.assert_shape(runner, message)
                self.assertIn('RESTORING', message)
                self.assertNotIn(pw, message)
                self.assertRegex(message, UNCERTAIN)

    def test_exit_code_is_1_with_the_changed_state_line(self):
        raised = {'timeout': subprocess.TimeoutExpired('sqlcmd', 3600.0), 'interrupt': KeyboardInterrupt(), 'oserror': OSError('x')}
        for name, exc in raised.items():
            with self.subTest(exc=name):
                runner = RestoreRunner(self.m, {'restore': [exc], 'state': [(0, state_out(), ''), (0, state_out('RESTORING'), '')]})
                code, text = self.do_code(runner=runner)
                self.assertEqual(code, 1)
                self.assertIn('stan bazy mógł się zmienić', text)
                self.assertIn(SAFETY_PATH, text)
        runner = RestoreRunner(self.m, {'restore': [(1, BATCH_ERROR, '')], 'state': [(0, state_out(), ''), (0, state_out('RESTORING'), '')]})
        code, text = self.do_code(runner=runner)
        self.assertEqual(code, 1)
        self.assertIn('stan bazy mógł się zmienić', text)

    def test_second_interrupt_while_reading_the_state_gives_up_without_retry(self):
        runner, message = self.failing(KeyboardInterrupt(), KeyboardInterrupt())
        self.assert_shape(runner, message)
        self.assertIn('unknown', message)
        self.assertEqual(runner.kinds.count('state'), 2)
        code, text = self.do_code(runner=RestoreRunner(self.m, {'restore': [KeyboardInterrupt()],
                                                                  'state': [(0, state_out(), ''), KeyboardInterrupt()]}))
        self.assertEqual(code, 1)
        self.assertIn(SAFETY_PATH, text)

    def test_failure_without_a_safety_backup_names_no_safety_path(self):
        runner, message = self.failing((1, BATCH_ERROR, ''), (0, state_out('RESTORING'), ''), no_safety_backup=True)
        self.assertEqual(runner.kinds[runner.kinds.index('restore') + 1:], ['state'])
        self.assertEqual(runner.mutating, ['restore'])
        self.assertNotIn(SAFETY_PATH, message)
        self.assertIn('RESTORING', message)

    def test_state_after_a_successful_batch_must_be_online_and_multi_user(self):
        for name, item in {'single user': (0, state_out('ONLINE', 'SINGLE_USER'), ''), 'restoring': (0, state_out('RESTORING'), ''),
                           'offline': (0, state_out('OFFLINE'), ''), 'unreadable': (1, '', ERROR_OUTPUT)}.items():
            with self.subTest(case=name):
                runner = RestoreRunner(self.m, {'state': [(0, state_out(), ''), item]})
                with self.assertRaises(self.m.SqlFailure) as ctx:
                    self.do_restore(runner=runner)
                self.assertEqual(runner.kinds[-1], 'state')
                self.assertEqual(runner.kinds.count('restore'), 1)
                self.assertEqual(runner.mutating, ['backup', 'restore'])
                expected = {'single user': 'SINGLE_USER', 'restoring': 'RESTORING', 'offline': 'OFFLINE'}.get(name)
                if expected:
                    self.assertIn(expected, str(ctx.exception))


class RestoreGuardCodes(RestoreBase):
    def test_unexpected_exception_before_any_mutation_is_blocked(self):
        for kind in ('state', 'identity', 'header', 'verify'):
            with self.subTest(kind=kind):
                runner = RestoreRunner(self.m, {kind: [RuntimeError('internal')]})
                code, _ = self.do_code(runner=runner)
                self.assertEqual(code, 2)
                self.assertIs(self.state.mutated, False)
                self.assertEqual(runner.mutating, [])

    def test_unexpected_exception_in_the_safety_backup_is_a_failure(self):
        cases = {'backup': {'backup': [RuntimeError('internal')]}, 'verify': {'verify': [(0, '', ''), RuntimeError('internal')]},
                 'header': {'header': [(0, restore_header_out(), ''), RuntimeError('internal')]}}
        for name, script in cases.items():
            with self.subTest(step=name):
                runner = RestoreRunner(self.m, script)
                code, text = self.do_code(runner=runner)
                self.assertEqual(code, 1)
                self.assertIs(self.state.mutated, True)
                self.assertNotIn('restore', runner.kinds)
                self.assertIn('stan bazy mógł się zmienić', text)

    def test_unexpected_exception_after_the_batch_is_a_failure(self):
        runner = RestoreRunner(self.m, {'state': [(0, state_out(), ''), RuntimeError('internal')]})
        code, _ = self.do_code(runner=runner, no_safety_backup=True)
        self.assertEqual(code, 1)
        self.assertIs(self.state.mutated, True)

    def test_mutation_starts_only_at_the_first_mutating_call(self):
        runner = RestoreRunner(self.m, {'verify': (1, '', ERROR_OUTPUT)})
        self.do_code(runner=runner)
        self.assertIs(self.state.mutated, False)
        runner = RestoreRunner(self.m)
        self.do_code(runner=runner, no_safety_backup=True)
        self.assertIs(self.state.mutated, True)

    def test_success_exit_code_is_0(self):
        state = self.m.RunState()
        err = io.StringIO()
        result = []
        code = self.m.run_guarded(lambda: result.append(self.do_restore(state=state)), state, err, self.m.Masker([]))
        self.assertEqual(code, 0)
        self.assertEqual(err.getvalue(), '')


class RestoreSecrets(RestoreBase):
    def setUp(self):
        super().setUp()
        self.pw = fake_password()
        self.environ = {'HOME': '/h/user', 'XDG_STATE_HOME': '/h/state', 'SQLCMDUSER': 'lab_login', 'SQLCMDPASSWORD': self.pw}
        self.masker = self.m.Masker([self.pw])

    def test_password_is_absent_from_a_successful_run(self):
        runner = RestoreRunner(self.m, masker=self.masker)
        result = self.do_restore(runner=runner, environ=self.environ, auth='env')
        text = json.dumps(result)
        for forbidden in (self.pw, 'SQLCMDPASSWORD', 'lab_login'):
            self.assertNotIn(forbidden, text)
            self.assertNotIn(forbidden, result['coordinator_notice'])
            for line, _ in self.emitted:
                self.assertNotIn(forbidden, line)

    def test_password_echoed_by_the_engine_does_not_reach_messages(self):
        leaky = BATCH_ERROR + '\nLogin failed for user lab_login with ' + self.pw
        for restore_item in ((1, leaky, ''), (1, '', leaky)):
            with self.subTest(item=restore_item[1:]):
                runner = RestoreRunner(self.m, {'restore': [restore_item], 'state': [(0, state_out(), ''), (0, state_out('RESTORING'), '')]},
                                       masker=self.masker)
                code, text = self.do_code(runner=runner, environ=self.environ, auth='env')
                self.assertEqual(code, 1)
                self.assertNotIn(self.pw, text)
                for line, _ in self.emitted:
                    self.assertNotIn(self.pw, line)
        runner = RestoreRunner(self.m, {'verify': [(1, leaky, '')]}, masker=self.masker)
        with self.assertRaises(self.m.SqlFailure) as ctx:
            self.do_restore(runner=runner, environ=self.environ, auth='env')
        self.assertNotIn(self.pw, str(ctx.exception))

    def test_password_is_absent_from_the_dry_run_plan(self):
        with self.assertRaises(self.m.NotRun) as ctx:
            self.do_restore(environ=self.environ, auth='env', dry_run=True)
        self.assertNotIn(self.pw, json.dumps(ctx.exception.result))
        self.assertNotIn('lab_login', json.dumps(ctx.exception.result))


class RestoreNoticeAndFormat(RestoreBase):
    def test_restore_notice_has_the_required_elements_in_polish(self):
        result = self.do_restore(server='localhost', runner=RestoreRunner(self.m, {'state': (0, state_out(recovery='FULL'), '')}))
        text = self.m.coordinator_notice(result, 'restore')
        self.assertIsInstance(text, str)
        for needed in ('appdb', 'localhost', SRC_PATH, SAFETY_PATH, '2026-11-22'):
            self.assertIn(needed, text)
        self.assertRegex(text, r'[Pp]rzywr|[Cc]ofni|[Kk]opi')
        self.assertIn('restore', text)
        for warning in result['warnings']:
            self.assertIn(warning, text)

    def test_restore_notice_has_no_secrets(self):
        pw = fake_password()
        environ = {'HOME': '/h/user', 'SQLCMDUSER': 'lab_login', 'SQLCMDPASSWORD': pw}
        result = self.do_restore(environ=environ, auth='env')
        text = self.m.coordinator_notice(result, 'restore')
        for forbidden in (pw, 'lab_login', 'SQLCMDPASSWORD'):
            self.assertNotIn(forbidden, text)

    def test_restore_notice_without_a_safety_backup(self):
        result = self.do_restore(no_safety_backup=True)
        text = self.m.coordinator_notice(result, 'restore')
        self.assertIsInstance(text, str)
        self.assertNotIn(SAFETY_PATH, text)
        for warning in result['warnings']:
            self.assertIn(warning, text)

    def test_text_format_of_a_restore_result(self):
        result = self.do_restore()
        text = self.m.format_result(result, False, self.m.Masker([]))
        for needed in ('PASS', SRC_PATH, SAFETY_PATH):
            self.assertIn(needed, text)

    def test_json_format_of_a_restore_result(self):
        result = self.do_restore(runner=RestoreRunner(self.m, {'state': (0, state_out(recovery='FULL'), '')}))
        loaded = json.loads(self.m.format_result(result, True, self.m.Masker([])))
        for key in ('status', 'database', 'file', 'safety_backup_path', 'previous_state', 'warnings', 'coordinator_notice'):
            self.assertEqual(loaded[key], result[key])
        self.assertIsInstance(loaded['warnings'], list)
        self.assertTrue(loaded['warnings'])

    def test_json_format_with_a_null_safety_backup_and_masking(self):
        pw = fake_password()
        result = self.do_restore(no_safety_backup=True)
        result['warnings'] = list(result['warnings']) + ['note ' + pw]
        loaded = json.loads(self.m.format_result(result, True, self.m.Masker([pw])))
        self.assertIsNone(loaded['safety_backup_path'])
        self.assertNotIn(pw, json.dumps(loaded))

    def test_format_of_a_dry_run_result(self):
        with self.assertRaises(self.m.NotRun) as ctx:
            self.do_restore(dry_run=True)
        text = self.m.format_result(ctx.exception.result, False, self.m.Masker([]))
        self.assertIn('NOT_RUN', text)
        loaded = json.loads(self.m.format_result(ctx.exception.result, True, self.m.Masker([])))
        self.assertEqual(loaded['status'], 'NOT_RUN')

if __name__ == '__main__':
    unittest.main()
