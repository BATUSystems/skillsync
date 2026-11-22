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
import shlex
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

    def test_windows_style_paths_are_checked_with_windows_rules(self):
        present = {'d:\\h\\repo\\.git'}
        for path in ('D:\\h\\repo\\out\\deep', 'd:/h/repo/out', '\\\\?\\D:\\h\\repo\\out'):
            with self.subTest(path=path):
                def lexists(p):
                    return ntpath.normcase(ntpath.normpath(p.replace('\\\\?\\', ''))) in present
                with self.assertRaises(self.m.Refusal):
                    self.m.check_dir_outside_repo(path, lexists=lexists, realpath=lambda p: p)
        self.m.check_dir_outside_repo('D:\\h\\other\\out', lexists=lambda p: False, realpath=lambda p: p)

    def test_the_host_path_flavour_does_not_matter_for_posix_looking_paths(self):
        # a Windows host (os.path is ntpath) must still refuse a repository found through posix-looking paths (the fakes of the other tests)
        lexists, realpath, _ = self.make_probe(['/h/repo/.git'])
        with mock.patch.object(self.m.os, 'path', ntpath):
            with self.assertRaises(self.m.Refusal):
                self.m.check_dir_outside_repo('/h/repo/out/deep', lexists=lexists, realpath=realpath)
            self.m.check_dir_outside_repo('/h/other/out', lexists=lexists, realpath=realpath)

    def test_a_backslash_in_a_rooted_posix_name_is_an_ordinary_character(self):
        lexists, realpath, _ = self.make_probe(['/h/.git'])
        with self.assertRaises(self.m.Refusal):
            self.m.check_dir_outside_repo('/h/a\\b/c', lexists=lexists, realpath=realpath)
        lexists, realpath, _ = self.make_probe(['/h/a/.git'])
        self.m.check_dir_outside_repo('/h/a\\b/c', lexists=lexists, realpath=realpath)


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
            self.assertEqual(self.code(self.m.NotRun('x'), mutated), 3)

    def test_sql_failure_is_blocked_before_a_mutation_and_a_failure_after_it(self):
        # Revision 4: one rule, exit 1 only when the first mutating call has started; otherwise nothing was changed (BLOCKED, 2).
        self.assertEqual(self.code(self.m.SqlFailure('x'), False), 2)
        self.assertEqual(self.code(self.m.SqlFailure('x'), True), 1)

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
        for fn, expected, line in ((refuse, 2, 'Refusal: bad input'), (fail, 2, 'SqlFailure: sql broke'), (skip, 3, 'NotRun: no sqlcmd')):
            code, text, _ = self.invoke(fn)
            self.assertEqual(code, expected)
            self.assertEqual(text.splitlines()[0], line)
            self.assertNotIn('stan bazy', text)

    def test_sql_failure_after_a_mutation_is_1_with_the_changed_state_line(self):
        state = self.m.RunState()
        err = io.StringIO()

        def fail():
            state.mark_mutating()
            raise self.m.SqlFailure('sql broke')
        code = self.m.run_guarded(fail, state, err, self.m.Masker([]))
        self.assertEqual(code, 1)
        self.assertEqual(err.getvalue().splitlines()[0], 'SqlFailure: sql broke')
        self.assertIn('stan bazy mógł się zmienić', err.getvalue())

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

    # ---- revision 4: after the end of the data only "(N rows affected)" with N equal to the number of rows may follow
    def test_text_after_the_end_of_the_data_is_refused(self):
        cases = {'text after the count': 'a|b\n-|-\n1|2\n\n(1 rows affected)\nforged|row\n',
                 'text instead of the count': 'a|b\n-|-\n1|2\n\nsomething else\n',
                 'text before the count': 'a|b\n-|-\n1|2\n\nforged|row\n(1 rows affected)\n',
                 'a second table': 'a|b\n-|-\n1|2\n\n(1 rows affected)\n\na|b\n-|-\n3|4\n\n(1 rows affected)\n',
                 'a message line': 'a|b\n-|-\n1|2\n\nMsg 8115, Level 16, State 2\n(1 rows affected)\n'}
        for name, text in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(self.m.SqlFailure):
                    self.m.parse_sqlcmd_table(text)

    def test_the_row_count_must_equal_the_number_of_rows(self):
        for text in ('a|b\n-|-\n1|2\n\n(2 rows affected)\n', 'a|b\n-|-\n1|2\n3|4\n\n(1 rows affected)\n',
                     'a|b\n-|-\n\n(1 rows affected)\n', 'a\n-\nx\n\n(2 row affected)\n'):
            with self.subTest(text=text):
                with self.assertRaises(self.m.SqlFailure):
                    self.m.parse_sqlcmd_table(text)
        self.assertEqual(self.m.parse_sqlcmd_table('a|b\n-|-\n1|2\n3|4\n\n(2 rows affected)\n'), [{'a': '1', 'b': '2'}, {'a': '3', 'b': '4'}])
        self.assertEqual(self.m.parse_sqlcmd_table('a\n-\nx\n\n(1 row affected)\n'), [{'a': 'x'}])

    def test_blank_lines_after_the_count_do_not_matter(self):
        self.assertEqual(self.m.parse_sqlcmd_table('a|b\n-|-\n1|2\n\n(1 rows affected)\n\n\n'), [{'a': '1', 'b': '2'}])
        self.assertEqual(self.m.parse_sqlcmd_table('a|b\r\n-|-\r\n1|2\r\n\r\n(1 rows affected)\r\n\r\n'), [{'a': '1', 'b': '2'}])

    def test_a_field_that_forges_a_row_with_an_empty_line_cannot_pass(self):
        # BackupName = "x|1|appdb<empty line>(1 rows affected)<newline>fake": the engine prints it raw, the first line has the right number of fields.
        out = ('BackupName|BackupType|DatabaseName\n-----------|----------|------------\n'
               'x|1|appdb\n\n(1 rows affected)\nfake|1|appdb\n\n(1 rows affected)\n')
        with self.assertRaises(self.m.SqlFailure):
            self.m.parse_sqlcmd_table(out)
        forged_header = 'BackupName|DatabaseName\n-----------|------------\nx|appdb\n\nBackupType|DatabaseName\n----|---\n1|other\n\n(1 rows affected)\n'
        with self.assertRaises(self.m.SqlFailure):
            self.m.parse_sqlcmd_table(forged_header)


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
        for flag in ('-s', '-w'):
            position = rest.index(flag)
            del rest[position:position + 2]
        self.assertEqual(rest, base)

    def test_table_mode_widens_the_row_to_65535_and_the_default_mode_does_not(self):
        args = self.m.sqlcmd_args('localhost', 'SELECT 1', table_mode=True, **self.kw)
        self.assertEqual(args.count('-w'), 1)
        self.assertEqual(args[args.index('-w') + 1], '65535')
        self.assertNotIn('-w', self.m.sqlcmd_args('localhost', 'SELECT 1', **self.kw))
        self.assertNotIn('-w', self.m.sqlcmd_args('localhost', 'SELECT 1', table_mode=False, **self.kw))

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

    def test_restore_command_default_server_and_default_directory(self):
        base = 'db_snapshot.py restore --file ' + STATE_DIR + '/' + BAK_NAME + ' --database appdb --confirm appdb'
        command = self.do_backup(dir=None)['restore_command']
        self.assertEqual(command, base)
        for option in ('--server', '--dir', '--trust-server-certificate'):
            self.assertNotIn(option, command)

    def test_restore_command_carries_the_directory_that_was_used(self):
        # Revision 4: restore refuses a file outside the backup directory, so a backup made with --dir must name it in the undo command.
        command = self.do_backup()['restore_command']
        self.assertTrue(command.startswith('db_snapshot.py restore --file ' + LOCAL_PATH + ' --database appdb --confirm appdb'))
        self.assertEqual(shlex.split(command)[1:], ['restore', '--file', LOCAL_PATH, '--database', 'appdb', '--confirm', 'appdb'] + ['--dir', LOCAL_DIR])
        for option in ('--server', '--trust-server-certificate'):
            self.assertNotIn(option, command)

    def test_restore_command_names_a_non_default_server(self):
        command = self.do_backup(server='.\\SQLEXPRESS')['restore_command']
        self.assertTrue(command.startswith('db_snapshot.py restore --file ' + LOCAL_PATH + ' --database appdb --confirm appdb'))
        self.assertIn('--server', command)
        self.assertIn('SQLEXPRESS', command)
        self.assertEqual(shlex.split(command)[shlex.split(command).index('--server') + 1], '.\\SQLEXPRESS')
        self.assertEqual(shlex.split(command)[shlex.split(command).index('--dir') + 1], LOCAL_DIR)

    def test_restore_command_carries_the_trust_certificate_flag_only_when_it_was_used(self):
        self.assertIn('--trust-server-certificate', shlex.split(self.do_backup(trust_server_certificate=True)['restore_command']))
        self.assertNotIn('--trust-server-certificate', self.do_backup(trust_server_certificate=False)['restore_command'])

    def test_restore_command_uses_posix_single_quotes_for_dollar_and_hash(self):
        for server in ('localhost\\INST$1', 'localhost\\INST#1'):
            with self.subTest(server=server):
                command = self.do_backup(server=server)['restore_command']
                self.assertIn("'" + server + "'", command)
                self.assertEqual(shlex.split(command)[shlex.split(command).index('--server') + 1], server)
                self.assertNotIn('"', command)

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

    def test_identity_failure_is_blocked_with_code_2_and_no_changed_state_line(self):
        code, text = self.do_code(runner=self.failing('identity'))
        self.assertEqual(code, 2)
        self.assertNotIn('stan bazy', text)
        self.assertIs(self.state.mutated, False)

    def test_unreadable_identity_is_code_2(self):
        for name, out in {'no row': table(['server_name', 'machine_name', 'instance_name'], []), 'no header': '',
                          'missing column': table(['server_name', 'machine_name'], [['LAB01', 'LAB01']])}.items():
            with self.subTest(case=name):
                runner = FakeRunner(self.m, {'identity': (0, out, '')})
                code, _ = self.do_code(runner=runner)
                self.assertEqual(code, 2)
                self.assertEqual(runner.kinds, ['identity'])

    def test_failures_after_the_backup_call_are_code_1_with_the_changed_state_line(self):
        for kind in ('verify', 'header'):
            with self.subTest(kind=kind):
                code, text = self.do_code(runner=self.failing(kind))
                self.assertEqual(code, 1)
                self.assertIn('stan bazy mógł się zmienić', text)
                self.assertIs(self.state.mutated, True)

    def test_a_backup_name_that_looks_like_an_error_line_is_not_an_error_when_rc_is_zero(self):
        # Revision 4: the Msg-line detection in tabular output works only for rc != 0 or for stderr; a field must not raise a false error.
        row = ['Msg 1, Level 16, State 1 fake'] + HEADER_ROW[1:]
        result = self.do_backup(runner=FakeRunner(self.m, {'header': (0, table(HEADER_COLUMNS, [row]), '')}))
        self.assertEqual(result['status'], 'PASS')

    def test_the_same_text_is_an_error_when_rc_is_not_zero_or_when_it_is_on_stderr(self):
        row = ['Msg 1, Level 16, State 1 fake'] + HEADER_ROW[1:]
        cases = {'rc 1 with the text in a field': ('header', (1, table(HEADER_COLUMNS, [row]), '')),
                 'stderr of the header read': ('header', (0, header_out(), 'Msg 1, Level 16, State 1 x')),
                 'stderr of the identity read': ('identity', (0, IDENTITY_OUT, 'Msg 1, Level 16, State 1 x'))}
        for name, (kind, response) in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(self.m.SqlFailure):
                    self.do_backup(runner=FakeRunner(self.m, {kind: response}))

    def test_header_fields_used_in_decisions_must_match_their_whitelist(self):
        # BackupType [0-9]+, BackupSetGUID a GUID, FirstLSN/LastLSN [0-9]+, DatabaseName [A-Za-z0-9_]+ (the copy was already made: code 1).
        bad = {'BackupType': ('1 OR 1=1', 'x', ''), 'BackupSetGUID': ('not-a-guid', "11111111-2222-3333-4444-55555555555'", '', 'ZZZZZZZZ-2222-3333-4444-555555555555'),
               'FirstLSN': ('1x', '-5', '', "1'"), 'LastLSN': ('2.5', 'abc', '')}
        for column, values in bad.items():
            for value in values:
                with self.subTest(column=column, value=value):
                    row = list(HEADER_ROW)
                    row[HEADER_COLUMNS.index(column)] = value
                    fs = FakeFs()
                    with self.assertRaises(self.m.SqlFailure):
                        self.do_backup(runner=FakeRunner(self.m, {'header': (0, table(HEADER_COLUMNS, [row]), '')}), fs=fs)
                    self.assertEqual(fs.written, {})
                    code, _ = self.do_code(runner=FakeRunner(self.m, {'header': (0, table(HEADER_COLUMNS, [row]), '')}))
                    self.assertEqual(code, 1)

    def test_a_forged_header_through_a_backup_name_with_a_blank_line_is_refused(self):
        forged = ('BackupName|BackupType|ServerName|DatabaseName|FirstLSN|LastLSN|BackupSetGUID|BackupFinishDate\n' + '-' * 10 + '\n'
                  'x|1|LAB01|appdb|100|200|11111111-2222-3333-4444-555555555555|2026-11-22 10:20:31.000\n\n(1 rows affected)\n'
                  'y|1|LAB01|appdb|1|2|AAAAAAAA-2222-3333-4444-555555555555|2026-11-22 10:20:31.000\n\n(1 rows affected)\n')
        with self.assertRaises(self.m.SqlFailure):
            self.do_backup(runner=FakeRunner(self.m, {'header': (0, forged, '')}))

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

    def test_an_existing_target_path_is_refused_before_the_backup_and_nothing_is_changed(self):
        runner, fs = FakeRunner(self.m), FakeFs(git_dirs=[LOCAL_PATH])  # FakeFs.lexists answers True for these paths
        with self.assertRaises(self.m.Refusal):
            self.do_backup(runner=runner, fs=fs)
        self.assertNotIn('backup', runner.kinds)
        self.assertIs(self.state.mutated, False)
        self.assertEqual((fs.made, fs.written), ([], {}))
        runner2 = FakeRunner(self.m)
        code, text = self.do_code(runner=runner2, fs=FakeFs(git_dirs=[LOCAL_PATH]))
        self.assertEqual(code, 2)
        self.assertNotIn('backup', runner2.kinds)
        self.assertNotIn('stan bazy', text)

    def test_the_existing_path_check_does_not_apply_to_a_server_path_of_a_remote_host(self):
        remote_path = '/srv/h/bak/' + BAK_NAME
        result = self.do_backup(fs=FakeFs(git_dirs=[remote_path]), server='dbhost.example', allow_hosts=('dbhost.example',), server_os='linux', dir='/srv/h/bak')
        self.assertEqual(result['status'], 'PASS')

    def test_a_free_target_path_is_not_refused(self):
        self.assertEqual(self.do_backup(fs=FakeFs(git_dirs=[LOCAL_DIR + '/other.bak']))['status'], 'PASS')

    def test_the_charset_refusal_points_at_the_dir_option(self):
        with self.assertRaises(self.m.Refusal) as ctx:
            self.do_backup(dir='/h/\u00e9')
        self.assertIn('server-path-charset', str(ctx.exception))
        self.assertIn('--dir', str(ctx.exception))

    def test_the_repository_refusal_points_at_the_dir_option(self):
        for kw, git in (({'dir': '/h/repo/out'}, '/h/repo/.git'), ({'dir': None}, '/h/state/.git')):
            with self.subTest(kw=kw):
                with self.assertRaises(self.m.Refusal) as ctx:
                    self.do_backup(fs=FakeFs(git_dirs=[git]), **kw)
                self.assertIn('dir-outside-repo', str(ctx.exception))
                self.assertIn('--dir', str(ctx.exception))

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

    def test_undecodable_child_output_is_replaced_not_raised(self):
        for table_mode in (False, True):
            with self.subTest(table=table_mode):
                popen = FakePopen()
                self.make(popen)('SELECT 1', mutating=False, timeout=1.0, table=table_mode)
                self.assertEqual(popen.calls[0][1].get('errors'), 'replace')

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
                self.assertEqual(code, 2 if kind == 'identity' else 1)  # only the identity read comes before the BACKUP
                self.assertNotIn(self.pw, err)
                self.assertTrue(err.startswith('SqlFailure'))
                self.assertEqual(fs.written, {})
                for argv, _kwargs in popen.calls:
                    self.assertFalse([a for a in argv if self.pw in a])

    def test_a_secret_on_the_300_character_boundary_of_the_error_excerpt_is_masked_before_the_cut(self):
        # The tabular reads reach the checker raw; masking after a cut at 300 characters would leave a partial secret (revision 4).
        prefix = 'Msg 4060, Level 16, State 1, '
        for offset in (293, 294, 296):
            line = prefix + 'x' * (offset - len(prefix)) + self.pw
            for kind in ('identity', 'header', 'backup', 'verify'):
                for stream in ('stderr', 'stdout'):
                    with self.subTest(offset=offset, kind=kind, stream=stream):
                        bad = {kind: (1, '', line) if stream == 'stderr' else (1, line, '')}
                        code, err, _, _, _, _ = self.scenario(bad)
                        self.assertIn(code, (1, 2))
                        self.assertTrue(err.startswith('SqlFailure'))
                        self.assertNotIn(self.pw[:4], err)
                        self.assertNotIn(self.pw, err)

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
DROP = object()  # source_sidecar(sha256=DROP) leaves the key out


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
        'remote': False, 'server_path': path, 'copy_only': True, 'sha256': FAKE_SHA,
    }
    data.update(changes)
    return {key: value for key, value in data.items() if value is not DROP}


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


class ShaFs(RestoreFs):
    """The digest of every file is a given text (the sidecar of the default fixtures records FAKE_SHA)."""

    def __init__(self, digest=FAKE_SHA, **kw):
        super().__init__(**kw)
        self.digest = digest

    def sha256(self, path):
        self.sha_calls.append(path)
        return self.digest


class LinkFs(RestoreFs):
    """realpath follows the given links; every other path is its own real path."""

    def __init__(self, links, **kw):
        super().__init__(**kw)
        self.links = dict(links)

    def realpath(self, path):
        self.realpaths.append(path)
        return self.links.get(path, path)


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
        self.assertEqual((fs.made, fs.written), ([], {}))
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
        self.assertIs(self.m.machine_names_match('ABCDEFGHIJKLMNO1', 'ABCDEFGHIJKLMNO2'), False)
        self.assertIs(self.m.machine_names_match('ABCDEFGHIJKLMNO', 'ABCDEFGHIJKLMNP'), False)
        self.assertIs(self.m.machine_names_match('ABCDEFGHIJKLMN', 'ABCDEFGHIJKLMNO'), False)

    def test_the_cut_applies_only_when_one_side_is_a_15_character_netbios_name(self):
        # Revision 4: two names longer than 15 characters are compared whole (a server farm: prod-sqlserver-01 and -02 are different machines).
        self.assertIs(self.m.machine_names_match('prod-sqlserver-01', 'prod-sqlserver-02'), False)
        self.assertIs(self.m.machine_names_match('prod-sqlserver-02', 'prod-sqlserver-01'), False)
        self.assertIs(self.m.machine_names_match('prod-sqlserver-01', 'PROD-SQLSERVER-01'), True)
        self.assertIs(self.m.machine_names_match('ABCDEFGHIJKLMNOPQRS', 'ABCDEFGHIJKLMNOPQRT'), False)
        self.assertIs(self.m.machine_names_match('ABCDEFGHIJKLMNO', 'abcdefghijklmno'), True)
        self.assertIs(self.m.machine_names_match('prod-sqlserver-', 'prod-sqlserver-01'), True)
        self.assertIs(self.m.machine_names_match('prod-sqlserver-01', 'prod-sqlserver-'), True)
        self.assertIs(self.m.machine_names_match('prod-sqlserver-', 'prod-sqlserve0-01'), False)

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
        script = {'identity': (0, out, ''), 'header': (0, restore_header_out(MachineName=long_name), '')}
        fs = RestoreFs(sidecar=source_sidecar(server=sidecar_server(machine_name=long_name)))
        result = self.do_restore(runner=RestoreRunner(self.m, script), fs=fs, machine_name='abcdefghijklmno')
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

    def test_field_with_a_separator_is_blocked_before_mutation(self):
        out = table(RESTORE_HEADER_COLUMNS, [['a|b'] + RESTORE_HEADER_ROW[1:]])
        self.assert_no_mutation_failure({'header': (0, out, '')}, ['state', 'identity', 'header'])
        code, text = self.do_code(runner=RestoreRunner(self.m, {'header': (0, out, '')}))
        self.assertEqual(code, 2)
        self.assertNotIn('stan bazy', text)

    def test_header_command_failure_stops_before_mutation_with_code_2(self):
        self.assert_no_mutation_failure({'header': (1, '', ERROR_OUTPUT)}, ['state', 'identity', 'header'])
        code, _ = self.do_code(runner=RestoreRunner(self.m, {'header': (1, '', ERROR_OUTPUT)}))
        self.assertEqual(code, 2)

    def test_a_header_without_a_required_column_is_blocked_with_code_2(self):
        columns = [name for name in RESTORE_HEADER_COLUMNS if name != 'LastLSN']
        out = table(columns, [[value for name, value in zip(RESTORE_HEADER_COLUMNS, RESTORE_HEADER_ROW) if name != 'LastLSN']])
        self.assert_no_mutation_failure({'header': (0, out, '')}, ['state', 'identity', 'header'])
        code, _ = self.do_code(runner=RestoreRunner(self.m, {'header': (0, out, '')}))
        self.assertEqual(code, 2)

    def test_state_and_identity_read_failures_are_blocked_with_code_2(self):
        for kind in ('state', 'identity'):
            with self.subTest(kind=kind):
                code, text = self.do_code(runner=RestoreRunner(self.m, {kind: (1, '', ERROR_OUTPUT)}))
                self.assertEqual(code, 2)
                self.assertNotIn('stan bazy', text)
                self.assertIs(self.state.mutated, False)

    def test_verifyonly_failure_is_exit_2_and_never_touches_the_database(self):
        for item in ((1, '', ERROR_OUTPUT), (0, 'Msg 3241, Level 16, State 5\nThe media family is incorrectly formed.', '')):
            with self.subTest(item=item):
                self.assert_no_mutation_failure({'verify': item}, ['state', 'identity', 'header', 'verify'])
                code, text = self.do_code(runner=RestoreRunner(self.m, {'verify': item}))
                self.assertEqual(code, 2)
                self.assertNotIn('stan bazy', text)


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

    MANUAL = r"RESTORE DATABASE \[?appdb\]? FROM DISK = N'" + re.escape(SAFETY_PATH) + r"' WITH REPLACE, RECOVERY"

    def test_a_database_left_in_restoring_gets_a_manual_restore_statement_as_the_way_back(self):
        # Revision 4: the tool refuses a restore from any state but ONLINE, so the way back from RESTORING is plain SQL.
        runner, message = self.failing((1, BATCH_ERROR, ''), (0, state_out('RESTORING'), ''))
        self.assertRegex(message, self.MANUAL)
        self.assertIn(SAFETY_PATH, message)

    def test_the_manual_statement_reaches_the_user_through_the_exit_path(self):
        runner = RestoreRunner(self.m, {'restore': [(1, BATCH_ERROR, '')], 'state': [(0, state_out(), ''), (0, state_out('RESTORING'), '')]})
        code, text = self.do_code(runner=runner)
        self.assertEqual(code, 1)
        self.assertRegex(text, self.MANUAL)

    def test_without_a_safety_backup_there_is_no_manual_statement(self):
        runner, message = self.failing((1, BATCH_ERROR, ''), (0, state_out('RESTORING'), ''), no_safety_backup=True)
        self.assertNotRegex(message, r"FROM DISK = N'")
        self.assertNotIn('WITH REPLACE, RECOVERY', message)
        self.assertIn('no way back', message)

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


def option_value(tokens, name):
    return tokens[tokens.index(name) + 1]


class RestoreIdentityAndWhitelists(RestoreBase):
    """Revision 4: the engine identity also guards the sidecar branch; the header values used in decisions pass a whitelist."""

    def blocked_at_header(self, script, fs=None, **kw):
        runner = RestoreRunner(self.m, script)
        with self.assertRaises((self.m.Refusal, self.m.SqlFailure)):
            self.do_restore(runner=runner, fs=fs or RestoreFs(), **kw)
        self.assertEqual(runner.mutating, [])
        self.assertIs(self.state.mutated, False)
        self.assertEqual(self.fs.written, {})
        self.assertNotIn('verify', runner.kinds)
        runner2 = RestoreRunner(self.m, script)
        code, text = self.do_code(runner=runner2, fs=fs or RestoreFs(), **kw)
        self.assertEqual(code, 2)
        self.assertNotIn('stan bazy', text)
        return runner

    def test_a_sidecar_and_a_header_from_another_engine_are_refused_when_they_agree_with_each_other(self):
        for name, changes in {'server and machine': {'ServerName': 'OTHERSRV', 'MachineName': 'OTHERBOX'}, 'server only': {'ServerName': 'OTHERSRV'},
                              'machine only': {'MachineName': 'OTHERBOX'}}.items():
            with self.subTest(case=name):
                server = {'engine_server_name': changes.get('ServerName', 'LAB01'), 'machine_name': changes.get('MachineName', 'LAB01')}
                fs = RestoreFs(sidecar=source_sidecar(server=sidecar_server(**server)))
                runner = self.blocked_at_header({'header': (0, restore_header_out(**changes), '')}, fs=fs)
                self.assertEqual(runner.kinds, ['state', 'identity', 'header'])

    def test_allow_machine_mismatch_turns_that_refusal_into_a_warning(self):
        fs = RestoreFs(sidecar=source_sidecar(server=sidecar_server(engine_server_name='OTHERSRV', machine_name='OTHERBOX')))
        runner = RestoreRunner(self.m, {'header': (0, restore_header_out(ServerName='OTHERSRV', MachineName='OTHERBOX'), '')})
        result = self.do_restore(runner=runner, fs=fs, allow_machine_mismatch=True)
        self.assertEqual(result['status'], 'PASS')
        text = ' '.join(result['warnings'])
        self.assertIn('ServerName', text)
        self.assertIn('MachineName', text)
        self.assertEqual(runner.kinds, [kind for kind, _, _ in FLOW_SHAPE])

    def test_a_guid_or_lsn_that_breaks_its_whitelist_is_refused_even_when_the_sidecar_agrees(self):
        cases = {'BackupSetGUID': ('not-a-guid', "11111111-2222-3333-4444-55555555555'", 'ZZZZZZZZ-2222-3333-4444-555555555555', ''),
                 'FirstLSN': ('1x', '-5', "1'", ''), 'LastLSN': ('2.5', 'abc', '')}
        keys = {'BackupSetGUID': 'backup_set_guid', 'FirstLSN': 'first_lsn', 'LastLSN': 'last_lsn'}
        for column, values in cases.items():
            for value in values:
                with self.subTest(column=column, value=value):
                    fs = RestoreFs(sidecar=source_sidecar(**{keys[column]: value}))
                    self.blocked_at_header({'header': (0, restore_header_out(**{column: value}), '')}, fs=fs)

    def test_a_non_numeric_backup_type_and_a_foreign_database_name_are_refused(self):
        for column, value in (('BackupType', 'one'), ('BackupType', '1 OR 1=1'), ('BackupType', ''), ('DatabaseName', 'app db'), ('DatabaseName', "appdb'")):
            with self.subTest(column=column, value=value):
                self.blocked_at_header({'header': (0, restore_header_out(**{column: value}), '')})

    def test_server_and_machine_names_that_break_their_whitelist_are_refused_even_when_every_source_agrees(self):
        for value in ('LAB 01', "LAB01' OR 1=1", 'LAB;01'):
            with self.subTest(server=value):
                identity = table(['server_name', 'machine_name', 'instance_name'], [[value, 'LAB01', 'NULL']])
                fs = RestoreFs(sidecar=source_sidecar(server=sidecar_server(engine_server_name=value)))
                script = {'identity': (0, identity, ''), 'header': (0, restore_header_out(ServerName=value), '')}
                self.blocked_at_header(script, fs=fs)
        for value in ('LAB 01', "LAB01' OR 1=1"):
            with self.subTest(machine=value):
                identity = table(['server_name', 'machine_name', 'instance_name'], [['LAB01', value, 'NULL']])
                fs = RestoreFs(sidecar=source_sidecar(server=sidecar_server(machine_name=value)))
                script = {'identity': (0, identity, ''), 'header': (0, restore_header_out(MachineName=value), '')}
                self.blocked_at_header(script, fs=fs, machine_name=value)

    def test_names_with_the_allowed_punctuation_pass(self):
        name = 'LAB01\\SQL_1$a#b-c.d'
        identity = table(['server_name', 'machine_name', 'instance_name'], [[name, 'LAB01', 'NULL']])
        fs = RestoreFs(sidecar=source_sidecar(server=sidecar_server(engine_server_name=name)))
        script = {'identity': (0, identity, ''), 'header': (0, restore_header_out(ServerName=name), '')}
        self.assertEqual(self.do_restore(runner=RestoreRunner(self.m, script), fs=fs)['status'], 'PASS')

    def test_a_forged_second_table_after_the_header_rows_is_refused_before_any_change(self):
        forged = restore_header_out() + restore_header_out(BackupSetGUID='AAAAAAAA-2222-3333-4444-555555555555')
        self.blocked_at_header({'header': (0, forged, '')})
        trailing = restore_header_out() + 'a harmless sounding line\n'
        self.blocked_at_header({'header': (0, trailing, '')})

    def test_a_backup_name_with_a_blank_line_cannot_forge_the_rows(self):
        columns = ['BackupName', 'BackupType', 'ServerName', 'MachineName', 'DatabaseName', 'FirstLSN', 'LastLSN', 'BackupSetGUID', 'BackupFinishDate']
        good = ['x', '1', 'LAB01', 'LAB01', 'appdb', '100', '200', HEADER_ROW[6], HEADER_ROW[7]]
        text = '|'.join(columns) + '\n' + '|'.join('-' * len(c) for c in columns) + '\n' + '|'.join(good) + '\n\n(1 rows affected)\n' + '|'.join(good) + '\n\n(1 rows affected)\n'
        self.blocked_at_header({'header': (0, text, '')})

    def test_a_backup_name_that_looks_like_an_error_line_is_not_an_error_when_rc_is_zero(self):
        row = dict(zip(RESTORE_HEADER_COLUMNS, RESTORE_HEADER_ROW), BackupName='Msg 1, Level 16, State 1 fake')
        out = table(RESTORE_HEADER_COLUMNS, [[row[name] for name in RESTORE_HEADER_COLUMNS]])
        self.assertEqual(self.do_restore(runner=RestoreRunner(self.m, {'header': (0, out, '')}))['status'], 'PASS')

    def test_the_same_text_on_stderr_or_with_rc_1_is_an_error_that_blocks_the_restore(self):
        for name, item in {'stderr': (0, restore_header_out(), 'Msg 1, Level 16, State 1 x'), 'rc 1': (1, restore_header_out(), '')}.items():
            with self.subTest(case=name):
                self.blocked_at_header({'header': item})
        for kind in ('state', 'identity'):
            with self.subTest(kind=kind):
                out = {'state': state_out(), 'identity': IDENTITY_OUT}[kind]
                code, _ = self.do_code(runner=RestoreRunner(self.m, {kind: (0, out, 'Msg 1, Level 16, State 1 x')}))
                self.assertEqual(code, 2)


class RestoreCopyIntegrity(RestoreBase):
    """Revision 4: sha256 and BackupFinishDate against the sidecar, a symbolic link as the copy, a target path that already exists."""

    def test_a_matching_sha256_lets_the_restore_go_on_and_the_source_file_is_hashed(self):
        fs = ShaFs()
        result = self.do_restore(fs=fs)
        self.assertEqual(result['status'], 'PASS')
        self.assertIn(SRC_PATH, fs.sha_calls)
        self.assertEqual(result['warnings'], [])

    def test_a_different_sha256_is_refused_with_code_2_before_any_change(self):
        runner, fs = RestoreRunner(self.m), ShaFs(digest='cd' * 32)
        with self.assertRaises(self.m.Refusal) as ctx:
            self.do_restore(runner=runner, fs=fs)
        self.assertIn('sha256', str(ctx.exception).lower())
        self.assertEqual(runner.mutating, [])
        self.assertIs(self.state.mutated, False)
        self.assertEqual(fs.written, {})
        runner2 = RestoreRunner(self.m)
        code, text = self.do_code(runner=runner2, fs=ShaFs(digest='cd' * 32))
        self.assertEqual(code, 2)
        self.assertEqual(runner2.mutating, [])
        self.assertNotIn('stan bazy', text)

    def test_a_sidecar_without_sha256_gives_a_warning_and_still_restores(self):
        fs = RestoreFs(sidecar=source_sidecar(sha256=DROP))
        result = self.do_restore(fs=fs)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(len([w for w in result['warnings'] if 'no sha256 recorded' in w]), 1)
        self.assertEqual(len(result['warnings']), 1)

    def test_a_missing_sidecar_does_not_claim_a_missing_sha256(self):
        result = self.do_restore(fs=RestoreFs(sidecar=NO_SIDECAR), allow_missing_sidecar=True)
        self.assertEqual([w for w in result['warnings'] if 'sha256' in w], [])

    def test_the_backup_finish_date_must_match_the_sidecar(self):
        runner = RestoreRunner(self.m, {'header': (0, restore_header_out(BackupFinishDate='2026-11-22 10:20:32.000'), '')})
        with self.assertRaises(self.m.Refusal):
            self.do_restore(runner=runner)
        self.assertEqual(runner.kinds, ['state', 'identity', 'header'])
        self.assertEqual(runner.mutating, [])
        code, _ = self.do_code(runner=RestoreRunner(self.m, {'header': (0, restore_header_out(BackupFinishDate='2026-11-22 10:20:32.000'), '')}))
        self.assertEqual(code, 2)

    def test_the_same_finish_date_passes(self):
        self.assertEqual(self.do_restore(runner=RestoreRunner(self.m, {'header': (0, restore_header_out(), '')}))['status'], 'PASS')

    def test_a_copy_that_is_a_symbolic_link_is_refused(self):
        fs = LinkFs({SRC_PATH: '/h/elsewhere/real.bak'})
        runner = RestoreRunner(self.m)
        with self.assertRaises(self.m.Refusal):
            self.do_restore(runner=runner, fs=fs)
        self.assertEqual(runner.mutating, [])
        self.assertIs(self.state.mutated, False)
        self.assertEqual(fs.written, {})
        code, _ = self.do_code(runner=RestoreRunner(self.m), fs=LinkFs({SRC_PATH: '/h/elsewhere/real.bak'}))
        self.assertEqual(code, 2)

    def test_a_plain_file_is_not_taken_for_a_link(self):
        fs = LinkFs({'/h/other/x.bak': '/h/else/y.bak'})
        self.assertEqual(self.do_restore(fs=fs)['status'], 'PASS')
        self.assertIn(SRC_PATH, fs.realpaths)  # the check really looked at the copy

    def test_an_existing_safety_backup_path_is_refused_before_the_safety_backup(self):
        runner, fs = RestoreRunner(self.m), RestoreFs(git_dirs=[SAFETY_PATH])  # lexists answers True for it
        with self.assertRaises(self.m.Refusal):
            self.do_restore(runner=runner, fs=fs)
        self.assertEqual(runner.mutating, [])
        self.assertIs(self.state.mutated, False)
        self.assertEqual(fs.written, {})
        code, _ = self.do_code(runner=RestoreRunner(self.m), fs=RestoreFs(git_dirs=[SAFETY_PATH]))
        self.assertEqual(code, 2)

    def test_the_default_directory_with_a_character_outside_the_whitelist_points_at_dir(self):
        home = '/h/ué'
        with self.assertRaises(self.m.Refusal) as ctx:
            self.do_restore(fs=RestoreFs(), dir=None, environ={'HOME': home})
        self.assertIn('server-path-charset', str(ctx.exception))
        self.assertIn('--dir', str(ctx.exception))

    def test_a_directory_inside_a_repository_points_at_dir(self):
        with self.assertRaises(self.m.Refusal) as ctx:
            self.do_restore(fs=RestoreFs(git_dirs=['/h/bk/.git']))
        self.assertIn('dir-outside-repo', str(ctx.exception))
        self.assertIn('--dir', str(ctx.exception))


class RestoreUndoCommands(RestoreBase):
    """Revision 4: the rollback command must be runnable as it stands (server, directory, certificate flag)."""

    def tokens(self, result):
        return shlex.split(result['rollback_command'])

    def test_the_rollback_command_names_the_directory_used_for_the_restore(self):
        tokens = self.tokens(self.do_restore())
        self.assertEqual(tokens[:2], ['db_snapshot.py', 'restore'])
        self.assertEqual(option_value(tokens, '--file'), SAFETY_PATH)
        self.assertEqual(option_value(tokens, '--database'), 'appdb')
        self.assertEqual(option_value(tokens, '--confirm'), 'appdb')
        self.assertEqual(option_value(tokens, '--dir'), LOCAL_DIR)
        self.assertNotIn('--server', tokens)
        self.assertNotIn('--trust-server-certificate', tokens)

    def test_the_rollback_command_has_no_dir_when_the_default_directory_was_used(self):
        path = STATE_DIR + '/' + SRC_NAME
        tokens = self.tokens(self.do_restore(fs=RestoreFs(src=path), dir=None, file=path))
        self.assertNotIn('--dir', tokens)
        self.assertEqual(option_value(tokens, '--file'), STATE_DIR + '/' + SAFETY_NAME)

    def test_the_rollback_command_names_a_non_default_server_and_the_certificate_flag(self):
        result = self.do_restore(server='(local)', trust_server_certificate=True)
        tokens = self.tokens(result)
        self.assertEqual(option_value(tokens, '--server'), '(local)')
        self.assertIn('--trust-server-certificate', tokens)
        self.assertEqual(option_value(tokens, '--dir'), LOCAL_DIR)
        self.assertEqual(option_value(tokens, '--file'), SAFETY_PATH)
        self.assertIn(result['rollback_command'], result['coordinator_notice'])

    def test_the_certificate_flag_is_not_invented(self):
        self.assertNotIn('--trust-server-certificate', self.tokens(self.do_restore(server='(local)')))

    def test_the_command_in_a_failure_message_is_complete_too(self):
        runner = RestoreRunner(self.m, {'restore': [(1, BATCH_ERROR, '')], 'state': [(0, state_out(), ''), (0, state_out('RESTORING'), '')]})
        with self.assertRaises(self.m.SqlFailure) as ctx:
            self.do_restore(runner=runner, server='(local)', trust_server_certificate=True)
        text = str(ctx.exception)
        self.assertIn('--dir ' + LOCAL_DIR, text)
        self.assertIn('--trust-server-certificate', text)
        self.assertIn('--server', text)

    def test_the_command_quotes_dollar_and_hash_with_single_quotes_on_posix(self):
        for server in ('localhost\\INST$1', 'localhost\\INST#1'):
            with self.subTest(server=server):
                fs = RestoreFs(sidecar=source_sidecar(server=sidecar_server(canonical=self.m.parse_server(server).canonical)))
                command = self.do_restore(fs=fs, server=server)['rollback_command']
                self.assertIn("'" + server + "'", command)
                self.assertEqual(option_value(shlex.split(command), '--server'), server)


class RestoreStateAfterTheBatch(RestoreBase):
    def test_an_exception_while_reading_the_final_state_is_a_failure_with_the_way_back(self):
        raised = {'timeout': subprocess.TimeoutExpired('sqlcmd', 30.0), 'oserror': OSError('pipe broke'), 'interrupt': KeyboardInterrupt()}
        for name, exc in raised.items():
            with self.subTest(exc=name):
                runner = RestoreRunner(self.m, {'state': [(0, state_out(), ''), exc]})
                code, text = self.do_code(runner=runner)  # through run_guarded: a leaking KeyboardInterrupt must not abort the whole test run
                self.assertEqual(runner.mutating, ['backup', 'restore'])
                self.assertEqual(code, 1)
                self.assertIn('stan bazy mógł się zmienić', text)
                self.assertIn(SAFETY_PATH, text)

    def test_the_same_exception_without_a_safety_backup_says_there_is_no_way_back(self):
        runner = RestoreRunner(self.m, {'state': [(0, state_out(), ''), subprocess.TimeoutExpired('sqlcmd', 30.0)]})
        code, text = self.do_code(runner=runner, no_safety_backup=True)
        self.assertEqual(code, 1)
        self.assertIn('no way back', text)
        self.assertNotIn(SAFETY_PATH, text)

    def test_an_exception_before_the_batch_is_not_reported_as_a_failed_final_read(self):
        runner = RestoreRunner(self.m, {'state': [subprocess.TimeoutExpired('sqlcmd', 30.0)]})
        code, text = self.do_code(runner=runner)
        self.assertEqual(code, 2)
        self.assertNotIn('stan bazy', text)


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


# ---------------------------------------------------------------- S4: command line, selftest, header, process tests

import ast  # noqa: E402
import contextlib  # noqa: E402
import shutil  # noqa: E402

POSIX_SKIP_REASON = 'needs POSIX: sqlcmd is faked as a shell script on PATH (shebang)'
HEADER_LABELS = ('Purpose', 'Usage', 'Inputs', 'Outputs', 'Exit codes', 'Dependencies', 'Limits', 'Security')
S4_ORIGINAL = {'marker': 'original', 'rows': [1, 2, 3]}
S4_MODIFIED = {'marker': 'modified by another user', 'rows': [9]}
S4_BAK_NAME = 'appdb-snap-20261121-090000-d4e5f6.bak'

# The faked sqlcmd: a Python script that plays a stateful SQL Server kept in a JSON file. The paths are baked in
# (the tool passes the child a whitelisted environment, so no test variable can reach it). Output is `sqlcmd -W -s "|"`.
FAKE_SQLCMD_SOURCE = r'''
import json, os, platform, re, sys, uuid

STATE = @@STATE@@
LOG = @@LOG@@
NODE = platform.node()
ARGS = sys.argv[1:]
LEVELS = 'Msg %d, Level 16, State 1, Server %s, Line 1'


def opt(name):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else None


def load():
    with open(STATE, encoding='utf-8') as handle:
        return json.load(handle)


def save(state):
    with open(STATE, 'w', encoding='utf-8') as handle:
        json.dump(state, handle)


def fail(number, text, state):
    extra = ''
    if state.get('echo_password'):
        extra = ' (login secret ' + os.environ.get('SQLCMDPASSWORD', '') + ')'
    sys.stdout.write(LEVELS % (number, NODE) + ' ' + text + extra + '\n')
    sys.exit(1)


def table(columns, rows):
    print('|'.join(columns))
    print('|'.join('-' * len(c) for c in columns))
    for row in rows:
        print('|'.join(row))
    print()
    print('(%d rows affected)' % len(rows))


def read_backup(path, state):
    if not os.path.isfile(path):
        fail(3201, 'Cannot open backup device. Operating system error 2.', state)
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


sql = opt('-Q') or ''
with open(LOG, 'a', encoding='utf-8') as handle:
    handle.write(json.dumps({'argv': ARGS, 'sql': sql, 'env': sorted(os.environ), 'sqlcmdini': os.environ.get('SQLCMDINI'),
                             'sqlcmddbname': os.environ.get('SQLCMDDBNAME'), 'cwd': os.getcwd(),
                             'cwdmode': oct(os.stat('.').st_mode & 0o777), 'cwdentries': sorted(os.listdir('.'))}) + '\n')
state = load()
dbs = state['dbs']
if '-E' not in ARGS and not (os.environ.get('SQLCMDUSER') and os.environ.get('SQLCMDPASSWORD')):
    fail(18456, 'Login failed.', state)
if sql.startswith('SELECT @@SERVERNAME'):
    table(['server_name', 'machine_name', 'instance_name'], [[NODE, NODE, 'NULL']])
elif sql.startswith('SELECT state_desc'):
    found = re.search(r"WHERE name = N'(\w+)'$", sql)
    db = dbs.get(found.group(1)) if found else None
    table(['state_desc', 'user_access_desc', 'recovery_model_desc'], [[db['state'], db['access'], db['recovery']]] if db else [])
elif sql.startswith('BACKUP DATABASE'):
    found = re.fullmatch(r"BACKUP DATABASE \[(\w+)\] TO DISK = N'(.*)' WITH COPY_ONLY, INIT, CHECKSUM", sql)
    if 'backup' in state['fail']:
        fail(3201, 'Cannot open backup device. Operating system error 5.', state)
    if found is None or found.group(1) not in dbs:
        fail(911, 'Database does not exist.', state)
    state['lsn'] += 100
    snapshot = {'db': found.group(1), 'guid': str(uuid.uuid4()).upper(), 'first': str(state['lsn']), 'last': str(state['lsn'] + 50),
                'data': dbs[found.group(1)]['data']}
    with open(found.group(2), 'w', encoding='utf-8') as handle:
        json.dump(snapshot, handle)
    save(state)
    print('BACKUP DATABASE successfully processed.')
elif sql.startswith('RESTORE VERIFYONLY'):
    found = re.fullmatch(r"RESTORE VERIFYONLY FROM DISK = N'(.*)' WITH CHECKSUM, FILE = 1", sql)
    if 'verify' in state['fail']:
        fail(3241, 'The media family on the device is incorrectly formed.', state)
    read_backup(found.group(1), state)
    print('The backup set on file 1 is valid.')
elif sql.startswith('RESTORE HEADERONLY'):
    found = re.fullmatch(r"RESTORE HEADERONLY FROM DISK = N'(.*)'", sql)
    snapshot = read_backup(found.group(1), state)
    table(['DatabaseName', 'BackupType', 'ServerName', 'MachineName', 'BackupSetGUID', 'FirstLSN', 'LastLSN', 'BackupFinishDate'],
          [[snapshot['db'], '1', NODE, NODE, snapshot['guid'], snapshot['first'], snapshot['last'], '2026-11-22 10:20:30.000']])
elif sql.startswith('BEGIN TRY'):
    found = re.search(r"RESTORE DATABASE \[(\w+)\] FROM DISK = N'(.*?)' WITH REPLACE", sql)
    if found is None or 'SINGLE_USER' not in sql or 'MULTI_USER' not in sql:
        fail(102, 'Incorrect syntax near the restore batch.', state)
    name = found.group(1)
    if 'restore' in state['fail']:
        dbs[name]['state'] = 'RESTORING'
        dbs[name]['access'] = 'SINGLE_USER'
        save(state)
        fail(3154, 'The backup set holds a backup of a database other than the existing one.', state)
    snapshot = read_backup(found.group(2), state)
    dbs[name].update(state='ONLINE', access='MULTI_USER', data=snapshot['data'])
    save(state)
    print('RESTORE DATABASE successfully processed.')
else:
    fail(102, 'Incorrect syntax near the query.', state)
'''


def s4_kind(sql):
    for prefix, kind in (('SELECT @@SERVERNAME', 'identity'), ('SELECT state_desc', 'state'), ('BACKUP DATABASE', 'backup'),
                         ('RESTORE VERIFYONLY', 'verify'), ('RESTORE HEADERONLY', 'header'), ('BEGIN TRY', 'restore')):
        if sql.startswith(prefix):
            return kind
    return 'other'


class FakeServer:
    """A temporary world: a faked sqlcmd with its state and log, a home, a working directory and a copy directory."""

    def __init__(self, root):
        self.root = root
        self.bin = str(tmp_paths.make_dir(root, 'bin'))
        self.empty = str(tmp_paths.make_dir(root, 'empty'))
        self.work = str(tmp_paths.make_dir(root, 'work'))
        self.home = str(tmp_paths.make_dir(root, 'home'))
        self.copies = os.path.join(root, 'copies')
        self.state_path = os.path.join(root, 'engine-state.json')
        self.log_path = os.path.join(root, 'engine-log.jsonl')
        self.script = os.path.join(self.bin, 'sqlcmd')
        text = '#!' + sys.executable + '\n' + FAKE_SQLCMD_SOURCE.replace('@@STATE@@', repr(self.state_path)).replace('@@LOG@@', repr(self.log_path))
        with open(self.script, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
        os.chmod(self.script, 0o755)
        with open(self.log_path, 'w', encoding='utf-8'):
            pass
        self.save({'dbs': {'appdb': {'state': 'ONLINE', 'access': 'MULTI_USER', 'recovery': 'SIMPLE', 'data': S4_ORIGINAL}},
                   'fail': [], 'lsn': 1000, 'echo_password': False})

    def state(self):
        with open(self.state_path, encoding='utf-8') as handle:
            return json.load(handle)

    def save(self, state):
        with open(self.state_path, 'w', encoding='utf-8') as handle:
            json.dump(state, handle)

    def data(self):
        return self.state()['dbs']['appdb']['data']

    def set_data(self, data):
        state = self.state()
        state['dbs']['appdb']['data'] = data
        self.save(state)

    def set_flag(self, key, value):
        state = self.state()
        state[key] = value
        self.save(state)

    def calls(self):
        with open(self.log_path, encoding='utf-8') as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        for row in rows:
            row['kind'] = s4_kind(row['sql'])
        return rows

    def kinds(self):
        return [row['kind'] for row in self.calls()]

    def mutating_kinds(self):
        return [kind for kind in self.kinds() if kind in ('backup', 'restore')]

    def tree(self):
        """Every file under the root as (relative name, bytes): names and contents for the secret scan."""
        found = []
        for base, dirs, files in os.walk(self.root):
            for name in dirs + files:
                found.append((os.path.relpath(os.path.join(base, name), self.root), b''))
            for name in files:
                with open(os.path.join(base, name), 'rb') as handle:
                    found.append((name, handle.read()))
        return found


class Cli(object):
    """Shared helpers of the S4 test classes."""

    def make_world(self):
        tmp = tempfile.TemporaryDirectory(prefix='dbsnap-s4-')
        self.addCleanup(tmp.cleanup)
        self.srv = FakeServer(os.path.realpath(tmp.name))
        return self.srv

    def environ(self, **extra):
        values = {'PATH': self.srv.bin + os.pathsep + '/usr/bin' + os.pathsep + '/bin', 'HOME': self.srv.home, 'LANG': 'C.UTF-8',
                  'SQLCMDUSER': 'sa_test', 'SQLCMDPASSWORD': self.password}
        values.update(extra)
        return values

    def call_main(self, argv, environ=None, platform=None):
        out, err, rout, rerr = io.StringIO(), io.StringIO(), io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(rout), contextlib.redirect_stderr(rerr):
            try:
                code = self.m.main(argv, environ=self.environ() if environ is None else environ, stdout=out, stderr=err,
                                   platform=sys.platform if platform is None else platform)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        return code, out.getvalue() + rout.getvalue(), err.getvalue() + rerr.getvalue()

    def spawn(self, argv, environ=None, cwd=None):
        done = subprocess.run([sys.executable, MODULE_PATH] + argv, env=self.environ() if environ is None else environ,
                              cwd=self.srv.work if cwd is None else cwd, capture_output=True, timeout=120)
        return done.returncode, done.stdout.decode('utf-8', 'replace'), done.stderr.decode('utf-8', 'replace')

    def json_of(self, text):
        return json.loads(text[text.index('{'):text.rindex('}') + 1])


# ---- in process: the command line without any engine

class CliInProcess(Cli, Base):
    def setUp(self):
        super().setUp()
        self.password = fake_password('Qw', '4', '-', 'rT', '+', 'v8')
        self.make_world()
        self.bare = {'PATH': self.srv.empty, 'HOME': self.srv.home, 'LANG': 'C.UTF-8'}

    def write_copy(self, size=10):
        os.makedirs(self.srv.copies, exist_ok=True)
        path = os.path.join(self.srv.copies, S4_BAK_NAME)
        with open(path, 'wb') as handle:
            handle.write(b'x' * size)
        with open(path + '.json', 'w', encoding='utf-8') as handle:
            json.dump(source_sidecar(path, size=size, sha256=hashlib.sha256(b'x' * size).hexdigest()), handle)
        return path

    def assert_clean_exit(self, argv, code, environ=None):
        got, out, err = self.call_main(argv, environ if environ is not None else self.bare)
        self.assertEqual(got, code, argv)
        self.assertNotIn('Traceback', out + err)
        return out, err

    def test_main_returns_an_integer_for_the_selftest_and_never_the_stub_code(self):
        code, out, err = self.call_main(['--selftest'], self.bare)
        self.assertIsInstance(code, int)
        self.assertNotIn('not implemented', out + err)

    # ---- usage errors are code 2 without a stack trace
    def test_no_subcommand_and_no_selftest_is_a_usage_error(self):
        out, err = self.assert_clean_exit([], 2)
        self.assertTrue('usage' in (out + err).lower())

    def test_unknown_subcommand_unknown_option_and_missing_required_options_are_usage_errors(self):
        for argv in (['frobnicate'], ['--no-such-option'], ['backup', '--no-such-option'], ['backup'], ['backup', '--database', 'appdb'],
                     ['backup', '--label', 'x'], ['restore'], ['restore', '--database', 'appdb'], ['restore', '--file', '/x/y.bak'],
                     ['backup', '--database', 'appdb', '--label', 'x', '--auth', 'kerberos'],
                     ['backup', '--database', 'appdb', '--label', 'x', '--server-os', 'beos'],
                     ['backup', '--database', 'appdb', '--label', 'x', '--timeout', 'soon'],
                     ['restore', '--file', '/x/y.bak', '--database', 'appdb', '--restore-timeout', 'later']):
            with self.subTest(argv=argv):
                self.assert_clean_exit(argv, 2)

    def test_abbreviated_options_are_refused(self):
        self.assert_clean_exit(['backup', '--datab', 'appdb', '--label', 'x', '--dry-run'], 2)
        self.assert_clean_exit(['backup', '--database', 'appdb', '--label', 'x', '--dry'], 2)
        self.assert_clean_exit(['--selft'], 2)

    def test_help_is_code_zero_and_lists_the_subcommands_and_options(self):
        out, _ = self.assert_clean_exit(['--help'], 0)
        for word in ('backup', 'restore', '--selftest'):
            self.assertIn(word, out)
        self.assert_clean_exit(['-h'], 0)
        out, _ = self.assert_clean_exit(['backup', '--help'], 0)
        for word in ('--database', '--label', '--server', '--dir', '--auth', '--allow-host', '--server-os', '--no-verify', '--sha256',
                     '--timeout', '--trust-server-certificate', '--dry-run', '--json'):
            self.assertIn(word, out)
        out, _ = self.assert_clean_exit(['restore', '--help'], 0)
        for word in ('--file', '--database', '--confirm', '--no-safety-backup', '--allow-missing-sidecar', '--allow-machine-mismatch',
                     '--restore-timeout', '--timeout', '--dry-run', '--json'):
            self.assertIn(word, out)

    def test_help_never_mentions_credentials_options(self):
        text = ''.join(self.assert_clean_exit(argv, 0)[0] for argv in (['--help'], ['backup', '--help'], ['restore', '--help']))
        for forbidden in ('--password', '--user', '--login', '-P ', '-U '):
            self.assertNotIn(forbidden, text)

    # ---- the engine is created lazily: refusals and dry runs need no sqlcmd
    def test_refusals_are_code_two_even_when_sqlcmd_is_missing(self):
        for argv in (['backup', '--database', 'master', '--label', 'x'], ['backup', '--database', 'app]db', '--label', 'x'],
                     ['backup', '--database', 'appdb', '--label', 'BAD LABEL'],
                     ['backup', '--database', 'appdb', '--label', 'x', '--server', 'db1.example.test'],
                     ['restore', '--file', '/h/bk/x.bak', '--database', 'appdb', '--server', 'db1.example.test', '--confirm', 'appdb']):
            with self.subTest(argv=argv):
                self.assert_clean_exit(argv, 2)

    def test_restore_without_confirm_is_a_refusal_code_two_not_a_usage_error_text(self):
        path = self.write_copy()
        out, err = self.assert_clean_exit(['restore', '--file', path, '--database', 'appdb', '--dir', self.srv.copies], 2)
        self.assertIn('confirm', (out + err).lower())
        out, err = self.assert_clean_exit(['restore', '--file', path, '--database', 'appdb', '--dir', self.srv.copies, '--confirm', 'otherdb'], 2)
        self.assertIn('confirm', (out + err).lower())

    def test_missing_sqlcmd_is_not_run_code_three_with_an_install_hint_and_no_files(self):
        out, err = self.assert_clean_exit(['backup', '--database', 'appdb', '--label', 'x', '--dir', self.srv.copies], 3)
        self.assertIn('sqlcmd', (out + err).lower())
        self.assertFalse(os.path.exists(self.srv.copies))
        self.assertEqual(os.listdir(self.srv.work), [])

    def test_a_directory_on_path_named_sqlcmd_is_not_a_sqlcmd(self):
        trap = os.path.join(self.srv.empty, 'sqlcmd')
        os.mkdir(trap)
        self.assert_clean_exit(['backup', '--database', 'appdb', '--label', 'x', '--dir', self.srv.copies], 3)

    def test_backup_dry_run_needs_no_sqlcmd_is_code_three_and_prints_the_plan(self):
        out, err = self.assert_clean_exit(['backup', '--database', 'appdb', '--label', 'snap', '--dir', self.srv.copies, '--dry-run'], 3)
        self.assertIn('NOT_RUN', out)
        self.assertIn('appdb-snap-', out)
        self.assertIn('.bak', out)
        self.assertFalse(os.path.exists(self.srv.copies))
        self.assertEqual(os.listdir(self.srv.work), [])

    def test_backup_dry_run_json_is_one_document_with_the_plan(self):
        out, _ = self.assert_clean_exit(['backup', '--database', 'appdb', '--label', 'snap', '--dir', self.srv.copies, '--dry-run', '--json'], 3)
        data = json.loads(out)
        self.assertEqual(data['status'], 'NOT_RUN')
        self.assertIn('plan', data)
        self.assertEqual(data['plan']['database'], 'appdb')

    def test_restore_dry_run_needs_no_sqlcmd_and_touches_nothing(self):
        path = self.write_copy()
        before = sorted(os.listdir(self.srv.copies))
        out, _ = self.assert_clean_exit(['restore', '--file', path, '--database', 'appdb', '--dir', self.srv.copies, '--confirm', 'appdb',
                                         '--dry-run', '--json'], 3)
        self.assertEqual(self.json_of(out)['status'], 'NOT_RUN')
        self.assertEqual(sorted(os.listdir(self.srv.copies)), before)

    def test_restore_dry_run_still_requires_confirm(self):
        path = self.write_copy()
        self.assert_clean_exit(['restore', '--file', path, '--database', 'appdb', '--dir', self.srv.copies, '--dry-run'], 2)

    # ---- --allow-host: repeatable, from the command line only
    def remote_dry_run(self, *hosts, **kw):
        argv = ['backup', '--database', 'appdb', '--label', 'x', '--server', 'db1.example.test', '--dir', '/h/srv', '--server-os', 'linux',
                '--dry-run', '--json']
        for host in hosts:
            argv += ['--allow-host', host]
        return self.assert_clean_exit(argv, kw.get('code', 3), kw.get('environ'))

    def test_a_remote_backup_needs_an_allow_host_flag(self):
        self.remote_dry_run(code=2)

    def test_allow_host_is_repeatable_and_every_occurrence_counts(self):
        for hosts in (('db1.example.test', 'other.example.test'), ('other.example.test', 'db1.example.test'), ('DB1.Example.Test',)):
            with self.subTest(hosts=hosts):
                out, _ = self.remote_dry_run(*hosts)
                self.assertIs(json.loads(out)['plan']['remote'], True)
        self.remote_dry_run('other.example.test', 'third.example.test', code=2)

    def test_allow_host_is_not_read_from_the_environment(self):
        environ = dict(self.bare)
        for name in ('DB_SNAPSHOT_ALLOW_HOST', 'DB_SNAPSHOT_ALLOW_HOSTS', 'ALLOW_HOST', 'ALLOW_HOSTS', 'DB_ALLOW_HOST', 'SQLCMDSERVER'):
            environ[name] = 'db1.example.test'
        self.remote_dry_run(code=2, environ=environ)

    def test_restore_has_no_allow_host_flag_and_refuses_a_remote_server(self):
        path = self.write_copy()
        base = ['restore', '--file', path, '--database', 'appdb', '--dir', self.srv.copies, '--confirm', 'appdb']
        self.assert_clean_exit(base + ['--allow-host', 'db1.example.test'], 2)
        self.assert_clean_exit(base + ['--server', 'db1.example.test'], 2)

    # ---- selftest
    def test_selftest_all_pass_with_at_least_eighteen_cases_covering_the_revision_4_additions(self):
        code, out, err = self.call_main(['--selftest'], {})
        self.assertEqual((code, err.strip()), (0, ''))
        lines = [line for line in out.splitlines() if line.strip()]
        names = [line.split(None, 1)[1] for line in lines if re.match(r'(PASS|FAIL) \S', line)]
        total = len(names)
        self.assertGreaterEqual(total, 18)
        joined = ' '.join(names).lower()
        # a look-alike host, a numeric loopback other than 127.0.0.1, a port and an instance, and the repository check of the directory
        for pattern in (r'evil', r'127[.\-_]0[.\-_]0[.\-_]2', r'port', r'instance', r'dir-outside-repo|outside-repo'):
            self.assertRegex(joined, pattern)
        self.assertEqual(len(set(names)), total)
        self.assertTrue(all(line.startswith('PASS ') for line in lines[:-1]), out)
        self.assertEqual(lines[-1], 'selftest db_snapshot: %d/%d passed' % (total, total))

    def test_selftest_starts_no_process_and_writes_nothing(self):
        boom = AssertionError('selftest touched the outside world')
        targets = [(subprocess, 'run'), (subprocess, 'Popen'), (os, 'system'), (os, 'makedirs'), (os, 'mkdir'), (os, 'replace'), (os, 'rename'),
                   (os, 'remove'), (os, 'unlink'), (os, 'fdopen'), (tempfile, 'mkstemp'), (tempfile, 'mkdtemp'), (io, 'open'), (shutil, 'rmtree')]
        before = sorted(os.listdir(self.srv.work))
        previous = os.getcwd()
        os.chdir(self.srv.work)
        try:
            with contextlib.ExitStack() as stack:
                mocks = [stack.enter_context(mock.patch.object(owner, name, side_effect=boom)) for owner, name in targets]
                mocks.append(stack.enter_context(mock.patch('builtins.open', side_effect=boom)))
                code, out, err = self.call_main(['--selftest'], {})
        finally:
            os.chdir(previous)
        self.assertEqual(code, 0, out + err)
        self.assertEqual([m.call_count for m in mocks], [0] * len(mocks))
        self.assertEqual(sorted(os.listdir(self.srv.work)), before)

    def test_selftest_does_not_need_an_engine_or_a_path(self):
        code, out, _ = self.call_main(['--selftest'], {})
        self.assertEqual(code, 0)
        code2, out2, _ = self.call_main(['--selftest'], self.bare)
        self.assertEqual((code2, out2), (0, out))

    def test_selftest_reports_a_failing_case_with_code_one(self):
        with mock.patch.object(self.m, 'validate_database', lambda name: name):
            code, out, err = self.call_main(['--selftest'], {})
        self.assertEqual(code, 1, out + err)
        self.assertRegex(out, r'(?m)^FAIL \S')
        found = re.search(r'selftest db_snapshot: (\d+)/(\d+) passed', out)
        self.assertIsNotNone(found)
        self.assertLess(int(found.group(1)), int(found.group(2)))


# ---- the header of the tool and the portability of its syntax

class ToolHeader(Base):
    def docstring(self):
        text = Path(MODULE_PATH).read_text(encoding='utf-8')
        doc = ast.get_docstring(ast.parse(text))
        self.assertIsNotNone(doc, 'the module has no docstring')
        return doc

    def section(self, label):
        lines = self.docstring().splitlines()
        start = [i for i, line in enumerate(lines) if line.startswith(label + ':')]
        self.assertEqual(len(start), 1, label)
        end = len(lines)
        for i in range(start[0] + 1, len(lines)):
            if re.match(r'(?:%s):' % '|'.join(re.escape(x) for x in HEADER_LABELS), lines[i]):
                end = i
                break
        return '\n'.join(lines[start[0]:end])

    def test_the_first_docstring_carries_all_eight_labels_at_line_start(self):
        doc = self.docstring()
        for label in HEADER_LABELS:
            with self.subTest(label=label):
                self.assertRegex(doc, r'(?m)^' + re.escape(label) + r':')

    def test_each_label_occurs_once_as_a_line_start_and_in_the_expected_order(self):
        lines = self.docstring().splitlines()
        positions = []
        for label in HEADER_LABELS:
            hits = [i for i, line in enumerate(lines) if line.startswith(label + ':')]
            self.assertEqual(len(hits), 1, label)
            positions.append(hits[0])
        self.assertEqual(positions, sorted(positions))

    def test_exit_codes_section_names_the_four_codes_and_the_database_state_warning(self):
        text = self.section('Exit codes')
        for code, word in ((0, 'PASS'), (1, 'FAIL'), (2, 'BLOCKED'), (3, 'NOT_RUN')):
            with self.subTest(code=code):
                self.assertRegex(text, r'(?<![\w.])' + str(code) + r'(?![\w.])')
                self.assertIn(word, text)
        self.assertRegex(text, r'(?is)database state.{0,60}changed|state of the database.{0,60}changed')

    def test_the_other_sections_carry_their_load_bearing_facts(self):
        self.assertIn('sqlcmd', self.section('Dependencies'))
        self.assertIn('3.11', self.section('Dependencies'))
        limits = self.section('Limits').lower()
        self.assertIn('engine', limits)
        self.assertRegex(limits, r'not verified|unverified|not tested|untested')
        security = self.section('Security')
        self.assertIn('--confirm', security)
        self.assertRegex(security.lower(), r'environment')
        usage = self.section('Usage')
        self.assertIn('backup', usage)
        self.assertIn('restore', usage)

    def test_every_section_has_content_after_its_label(self):
        for label in HEADER_LABELS:
            with self.subTest(label=label):
                body = self.section(label)[len(label) + 1:].strip()
                self.assertGreater(len(body), 10)

    def test_the_header_is_english_ascii_prose(self):
        doc = self.docstring()
        self.assertEqual([c for c in doc if ord(c) > 127], [])


class SyntaxPortability(Base):
    def source(self):
        return Path(MODULE_PATH).read_text(encoding='utf-8')

    def test_the_module_parses_as_python_311(self):
        ast.parse(self.source(), filename=MODULE_PATH, feature_version=(3, 11))
        compile(self.source(), MODULE_PATH, 'exec')

    def test_the_311_parse_does_reject_newer_syntax(self):
        for text in ('type Alias = int\n', 'def f[T](x: T) -> T:\n    return x\n'):
            with self.subTest(text=text):
                with self.assertRaises(SyntaxError):
                    ast.parse(text, feature_version=(3, 11))

    def test_the_module_does_not_use_newer_standard_library_names(self):
        tree = ast.parse(self.source())
        names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        for newer in ('batched', 'process_cpu_count', 'walk_up'):
            self.assertNotIn(newer, names)


# ---- in a child process, without an engine

class ProcessMeta(Cli, Base):
    def setUp(self):
        super().setUp()
        self.password = fake_password()
        self.make_world()
        self.bare = {'PATH': self.srv.empty, 'HOME': self.srv.home, 'LANG': 'C.UTF-8', 'PYTHONIOENCODING': 'utf-8'}
        if os.environ.get('SYSTEMROOT'):
            self.bare['SYSTEMROOT'] = os.environ['SYSTEMROOT']

    def test_selftest_in_a_process_is_code_zero_with_the_summary_line(self):
        code, out, err = self.spawn(['--selftest'], self.bare)
        self.assertEqual(code, 0, out + err)
        lines = [line for line in out.splitlines() if line.strip()]
        found = re.fullmatch(r'selftest db_snapshot: (\d+)/(\d+) passed', lines[-1])
        self.assertIsNotNone(found, out)
        self.assertEqual(found.group(1), found.group(2))
        self.assertGreaterEqual(int(found.group(2)), 18)
        self.assertEqual(os.listdir(self.srv.work), [])

    def test_help_in_a_process_is_code_zero(self):
        for argv in (['--help'], ['backup', '--help'], ['restore', '--help']):
            with self.subTest(argv=argv):
                code, out, err = self.spawn(argv, self.bare)
                self.assertEqual(code, 0, err)
                self.assertIn('usage', out.lower())

    def test_usage_errors_in_a_process_are_code_two_without_a_stack_trace(self):
        for argv in ([], ['backup'], ['bogus'], ['backup', '--database', 'appdb', '--label', 'x', '--bogus']):
            with self.subTest(argv=argv):
                code, out, err = self.spawn(argv, self.bare)
                self.assertEqual(code, 2)
                self.assertNotIn('Traceback', err)
                self.assertIn('usage', err.lower())

    def test_importing_the_module_runs_nothing_and_prints_nothing(self):
        done = subprocess.run([sys.executable, '-c', 'import importlib.util as u,sys; s=u.spec_from_file_location("m", sys.argv[1]); '
                               'm=u.module_from_spec(s); s.loader.exec_module(m); print(callable(m.main))', MODULE_PATH],
                              env=self.bare, cwd=self.srv.work, capture_output=True, timeout=60)
        self.assertEqual((done.returncode, done.stdout.decode().strip(), done.stderr), (0, 'True', b''))


class ProcessBase(Cli, Base):
    """The faked sqlcmd is a script with a shebang: POSIX only."""

    @classmethod
    def setUpClass(cls):
        if os.name == 'nt':
            raise unittest.SkipTest(POSIX_SKIP_REASON)

    def setUp(self):
        super().setUp()
        self.password = fake_password('Zx', '9', '-', 'qT', '#', 'm1')
        self.make_world()

    def spawn_env(self, **extra):
        values = self.environ(PYTHONIOENCODING='utf-8')
        values.update(extra)
        return values

    def backup(self, *extra, **kw):
        argv = ['backup', '--database', 'appdb', '--label', kw.pop('label', 'snap'), '--dir', self.srv.copies, '--json'] + list(extra)
        return self.spawn(argv, self.spawn_env(**kw))

    def restore(self, path, *extra, **kw):
        argv = ['restore', '--file', path, '--database', 'appdb', '--confirm', 'appdb', '--dir', self.srv.copies, '--json'] + list(extra)
        return self.spawn(argv, self.spawn_env(**kw))


class ProcessAcceptance(ProcessBase):
    """K2 acceptance in a child process: a copy, a change of state, a restore and the way back."""

    def test_copy_change_restore_returns_the_original_state_and_the_way_back_works(self):
        code, out, err = self.backup()
        self.assertEqual(code, 0, out + err)
        made = self.json_of(out)
        path = made['path']
        self.assertEqual(made['status'], 'PASS')
        self.assertTrue(path.endswith('.bak') and os.path.dirname(path) == self.srv.copies)
        self.assertGreater(os.path.getsize(path), 0)
        with open(path + '.json', encoding='utf-8') as handle:
            sidecar = json.load(handle)
        self.assertEqual((sidecar['schema'], sidecar['database'], sidecar['label']), (1, 'appdb', 'snap'))
        self.assertTrue(made['restore_command'] and made['coordinator_notice'])
        self.assertEqual(self.srv.kinds(), ['identity', 'backup', 'verify', 'header'])

        self.srv.set_data(S4_MODIFIED)
        self.assertNotEqual(self.srv.data(), S4_ORIGINAL)
        before = len(self.srv.calls())
        code, out, err = self.restore(path)
        self.assertEqual(code, 0, out + err)
        restored = self.json_of(out)
        self.assertEqual(restored['status'], 'PASS')
        self.assertEqual(self.srv.data(), S4_ORIGINAL)

        kinds = [row['kind'] for row in self.srv.calls()][before:]
        self.assertEqual(kinds.count('restore'), 1)
        self.assertIn('backup', kinds)
        self.assertLess(kinds.index('backup'), kinds.index('restore'))
        self.assertLess(kinds.index('verify'), kinds.index('backup'))
        self.assertEqual(kinds[-1], 'state')

        safety = restored['safety_backup_path']
        self.assertTrue(os.path.isfile(safety) and safety != path)
        self.assertEqual(os.path.dirname(safety), self.srv.copies)
        self.assertIn('pre-restore', os.path.basename(safety))
        self.assertTrue(os.path.isfile(safety + '.json'))
        self.assertIn(safety, restored['rollback_command'])
        self.assertIn('restore', restored['rollback_command'])
        self.assertTrue(restored['coordinator_notice'])
        self.assertIn(safety, restored['coordinator_notice'])

        code, out, err = self.restore(safety, '--no-safety-backup')
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.srv.data(), S4_MODIFIED)
        self.assertEqual(os.listdir(self.srv.work), [])

    def run_command(self, command):
        tokens = shlex.split(command)
        self.assertEqual(tokens[0], 'db_snapshot.py')
        return self.call_main(tokens[1:] + ['--json'], environ=self.spawn_env())

    def test_the_undo_commands_from_the_results_run_as_they_stand_and_pass(self):
        # Revision 4: restore_command and rollback_command carry --server, --dir and --trust-server-certificate, so main accepts them.
        for extra in ([], ['--server', '(local)', '--trust-server-certificate']):
            with self.subTest(extra=extra):
                self.srv.set_data(S4_ORIGINAL)
                code, out, err = self.backup(*extra)
                self.assertEqual(code, 0, out + err)
                made = self.json_of(out)
                command = made['restore_command']
                tokens = shlex.split(command)
                self.assertEqual(tokens[tokens.index('--dir') + 1], self.srv.copies)
                self.assertEqual('--trust-server-certificate' in tokens, bool(extra))
                self.assertEqual(tokens[tokens.index('--server') + 1] if '--server' in tokens else None, '(local)' if extra else None)

                self.srv.set_data(S4_MODIFIED)
                before = len(self.srv.calls())
                code, out, err = self.run_command(command)
                self.assertEqual(code, 0, out + err)
                self.assertEqual(self.srv.data(), S4_ORIGINAL)
                restored = self.json_of(out)
                for row in self.srv.calls()[before:]:
                    argv = row['argv']
                    self.assertEqual(argv[argv.index('-S') + 1], '(local)' if extra else 'localhost')
                    self.assertEqual('-C' in argv, bool(extra))

                code, out, err = self.run_command(restored['rollback_command'])
                self.assertEqual(code, 0, out + err)
                self.assertEqual(self.srv.data(), S4_MODIFIED)

    def test_polish_text_on_a_strict_cp1252_stream_does_not_crash_and_the_result_is_written(self):
        # Revision 4: main reconfigures the real streams with errors='replace'; the notice and the changed-state line hold characters outside cp1252.
        def streams():
            raw_out, raw_err = io.BytesIO(), io.BytesIO()
            return (raw_out, raw_err, io.TextIOWrapper(raw_out, encoding='cp1252', errors='strict', write_through=True),
                    io.TextIOWrapper(raw_err, encoding='cp1252', errors='strict', write_through=True))
        argv = ['backup', '--database', 'appdb', '--label', 'snap', '--dir', self.srv.copies]
        raw_out, raw_err, out, err = streams()
        code = self.m.main(argv, environ=self.spawn_env(), stdout=out, stderr=err, platform=sys.platform)
        self.assertEqual(code, 0, raw_err.getvalue())
        text = raw_out.getvalue().decode('cp1252')
        self.assertEqual(text.splitlines()[0], 'PASS')
        self.assertIn('Uwaga: baza testowa jest wsp', text)
        self.assertIn('?', text)  # the characters outside the code page became question marks
        self.assertTrue([name for name in os.listdir(self.srv.copies) if name.endswith('.bak.json')])
        self.srv.set_flag('fail', ['backup'])
        raw_out, raw_err, out, err = streams()
        code = self.m.main(argv, environ=self.spawn_env(), stdout=out, stderr=err, platform=sys.platform)
        self.assertEqual(code, 1)
        message = raw_err.getvalue().decode('cp1252')
        self.assertIn('SqlFailure', message)
        self.assertIn('stan bazy m', message)

    def test_text_output_starts_with_the_status_and_carries_the_undo_and_the_coordinator_notice(self):
        code, out, err = self.spawn(['backup', '--database', 'appdb', '--label', 'snap', '--dir', self.srv.copies], self.spawn_env())
        self.assertEqual(code, 0, err)
        self.assertEqual(out.splitlines()[0], 'PASS')
        self.assertIn('restore_command:', out)
        self.assertIn('coordinator_notice:', out)
        self.assertIn(self.srv.copies, out)

    def test_the_default_directory_is_the_state_directory_under_home(self):
        code, out, err = self.spawn(['backup', '--database', 'appdb', '--label', 'snap', '--json'], self.spawn_env())
        self.assertEqual(code, 0, err)
        path = self.json_of(out)['path']
        self.assertEqual(os.path.dirname(path), self.m.default_backup_dir(sys.platform, {}, self.srv.home))
        self.assertEqual(os.listdir(self.srv.work), [])

    def test_restore_without_a_safety_backup_makes_no_extra_copy(self):
        made = self.json_of(self.backup()[1])
        self.srv.set_data(S4_MODIFIED)
        before = self.srv.kinds().count('backup')
        files = sorted(os.listdir(self.srv.copies))
        code, out, err = self.restore(made['path'], '--no-safety-backup')
        self.assertEqual(code, 0, out + err)
        result = self.json_of(out)
        self.assertIsNone(result['safety_backup_path'])
        self.assertIsNone(result['rollback_command'])
        self.assertEqual(self.srv.kinds().count('backup'), before)
        self.assertEqual(sorted(os.listdir(self.srv.copies)), files)
        self.assertEqual(self.srv.data(), S4_ORIGINAL)

    def test_a_missing_sidecar_is_refused_unless_allowed(self):
        made = self.json_of(self.backup()[1])
        os.unlink(made['path'] + '.json')
        self.srv.set_data(S4_MODIFIED)
        code, out, err = self.restore(made['path'])
        self.assertEqual(code, 2, out + err)
        self.assertEqual(self.srv.data(), S4_MODIFIED)
        self.assertEqual(self.srv.mutating_kinds(), ['backup'])
        code, out, err = self.restore(made['path'], '--allow-missing-sidecar')
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.srv.data(), S4_ORIGINAL)
        self.assertTrue(self.json_of(out)['warnings'])

    # ---- flags reach the flow
    def test_no_verify_and_sha256_reach_the_flow(self):
        code, out, err = self.backup('--no-verify', '--sha256')
        self.assertEqual(code, 0, out + err)
        made = self.json_of(out)
        self.assertEqual(made['verify'], 'skipped')
        self.assertNotIn('verify', self.srv.kinds())
        with open(made['path'], 'rb') as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        with open(made['path'] + '.json', encoding='utf-8') as handle:
            self.assertEqual(json.load(handle)['sha256'], digest)

    def test_server_and_trust_certificate_reach_sqlcmd_and_credentials_never_do(self):
        code, out, err = self.backup('--server', '(local)', '--trust-server-certificate')
        self.assertEqual(code, 0, out + err)
        calls = self.srv.calls()
        self.assertTrue(calls)
        for row in calls:
            argv = row['argv']
            self.assertEqual(argv[argv.index('-S') + 1], '(local)')
            self.assertEqual(argv[argv.index('-d') + 1], 'master')
            self.assertIn('-C', argv)
            self.assertIn('-b', argv)
            self.assertNotIn('-E', argv)
            for forbidden in ('-U', '-P', '--password'):
                self.assertNotIn(forbidden, argv)

    def test_integrated_auth_uses_e_and_hands_over_no_credentials(self):
        code, out, err = self.backup('--auth', 'integrated')
        self.assertEqual(code, 0, out + err)
        for row in self.srv.calls():
            self.assertIn('-E', row['argv'])
            self.assertNotIn('SQLCMDPASSWORD', row['env'])
            self.assertNotIn('SQLCMDUSER', row['env'])

    # ---- timeouts (the command line values reach the runner)
    def recorded_runner(self):
        seen = []
        real = self.m.SubprocessRunner

        def factory(*args, **kwargs):
            args = list(args)
            base = kwargs.get('popen', subprocess.run) if len(args) < 9 else args[8]

            def popen(argv, **pkw):
                seen.append({'kind': s4_kind(argv[argv.index('-Q') + 1]), 'timeout': pkw.get('timeout')})
                return base(argv, **pkw)
            if len(args) >= 9:
                args[8] = popen
            else:
                kwargs['popen'] = popen
            return real(*args, **kwargs)
        return seen, mock.patch.object(self.m, 'SubprocessRunner', factory)

    def test_backup_timeout_reaches_every_call_of_the_runner(self):
        seen, patch = self.recorded_runner()
        with patch:
            code, out, err = self.call_main(['backup', '--database', 'appdb', '--label', 'snap', '--dir', self.srv.copies, '--timeout', '7.5'])
        self.assertEqual(code, 0, out + err)
        by_kind = {row['kind']: row['timeout'] for row in seen}
        self.assertEqual(by_kind['backup'], 7.5)
        self.assertEqual(by_kind['verify'], 7.5)
        self.assertTrue(all(row['timeout'] == 7.5 for row in seen), seen)

    def test_default_timeouts_are_600_for_the_backup(self):
        seen, patch = self.recorded_runner()
        with patch:
            code, out, err = self.call_main(['backup', '--database', 'appdb', '--label', 'snap', '--dir', self.srv.copies])
        self.assertEqual(code, 0, out + err)
        self.assertEqual([row['timeout'] for row in seen if row['kind'] == 'backup'], [600])

    def test_restore_timeouts_split_between_the_restore_and_the_safety_backup(self):
        made = self.json_of(self.backup()[1])
        self.srv.set_data(S4_MODIFIED)
        seen, patch = self.recorded_runner()
        with patch:
            code, out, err = self.call_main(['restore', '--file', made['path'], '--database', 'appdb', '--confirm', 'appdb', '--dir', self.srv.copies,
                                             '--restore-timeout', '4000', '--timeout', '55'])
        self.assertEqual(code, 0, out + err)
        self.assertEqual([row['timeout'] for row in seen if row['kind'] == 'restore'], [4000])
        self.assertEqual([row['timeout'] for row in seen if row['kind'] == 'backup'], [55])
        self.assertEqual(sorted(row['timeout'] for row in seen if row['kind'] == 'verify'), [55, 4000])


class ProcessSafety(ProcessBase):
    """K4 secrets, K5 exit codes and the whitelisted child environment, all in a child process."""

    def test_the_proof_that_the_fake_can_leak_the_secret(self):
        self.srv.set_flag('echo_password', True)
        self.srv.set_flag('fail', ['backup'])
        done = subprocess.run([self.srv.script, '-S', 'x', '-Q', 'BACKUP DATABASE [appdb] TO DISK = N\'/x/y.bak\' WITH COPY_ONLY, INIT, CHECKSUM'],
                              env={'SQLCMDUSER': 'sa_test', 'SQLCMDPASSWORD': self.password}, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 1)
        self.assertIn(self.password, done.stdout)

    def test_the_password_is_in_no_argument_output_or_file(self):
        code, out, err = self.backup()
        self.assertEqual(code, 0, out + err)
        made = self.json_of(out)
        self.srv.set_data(S4_MODIFIED)
        code2, out2, err2 = self.restore(made['path'])
        self.assertEqual(code2, 0, out2 + err2)
        variants = [self.password, urllib.parse.quote(self.password, safe=''), base64.b64encode(self.password.encode()).decode().rstrip('=')]
        for variant in variants:
            for text in (out, err, out2, err2):
                self.assertNotIn(variant, text)
            for name, blob in self.srv.tree():
                self.assertNotIn(variant, name)
                self.assertNotIn(variant.encode(), blob)
        for row in self.srv.calls():
            self.assertNotIn('sa_test', json.dumps(row['argv']))
            self.assertNotIn('sa_test', row['sql'])

    def test_an_echoed_password_in_a_sql_error_is_masked_in_stderr_stdout_and_json(self):
        self.srv.set_flag('echo_password', True)
        self.srv.set_flag('fail', ['backup'])
        for as_json in (True, False):
            with self.subTest(as_json=as_json):
                argv = ['backup', '--database', 'appdb', '--label', 'snap', '--dir', self.srv.copies] + (['--json'] if as_json else [])
                code, out, err = self.spawn(argv, self.spawn_env())
                self.assertEqual(code, 1)
                self.assertIn('Msg 3201', err)
                self.assertIn('***', err)
                for variant in (self.password, urllib.parse.quote(self.password, safe='')):
                    self.assertNotIn(variant, out + err)
        for name, blob in self.srv.tree():
            self.assertNotIn(self.password.encode(), blob)

    # ---- K5 exit codes
    def test_a_missing_sqlcmd_is_three_and_a_dry_run_is_three_without_any_call(self):
        bare = {'PATH': self.srv.empty, 'HOME': self.srv.home, 'LANG': 'C.UTF-8', 'PYTHONIOENCODING': 'utf-8'}
        code, out, err = self.spawn(['backup', '--database', 'appdb', '--label', 'x', '--dir', self.srv.copies], bare)
        self.assertEqual(code, 3)
        self.assertIn('sqlcmd', err.lower())
        self.assertNotIn('Traceback', err)
        code, out, err = self.spawn(['backup', '--database', 'appdb', '--label', 'x', '--dir', self.srv.copies, '--dry-run'], bare)
        self.assertEqual(code, 3)
        self.assertIn('appdb', out)
        self.assertFalse(os.path.exists(self.srv.copies))
        self.assertEqual(os.listdir(self.srv.work), [])
        self.assertEqual(self.srv.calls(), [])

    def test_a_dry_run_with_sqlcmd_present_still_makes_no_call_and_no_file(self):
        code, out, err = self.backup('--dry-run')
        self.assertEqual(code, 3, out + err)
        self.assertEqual(self.json_of(out)['status'], 'NOT_RUN')
        self.assertEqual(self.srv.calls(), [])
        self.assertFalse(os.path.exists(self.srv.copies))
        made = self.json_of(self.backup()[1])
        before = len(self.srv.calls())
        files = sorted(os.listdir(self.srv.copies))
        code, out, err = self.restore(made['path'], '--dry-run')
        self.assertEqual(code, 3, out + err)
        self.assertEqual(len(self.srv.calls()), before)
        self.assertEqual(sorted(os.listdir(self.srv.copies)), files)

    def test_usage_errors_are_two_and_make_no_call(self):
        code, out, err = self.spawn(['backup', '--database', 'appdb'], self.spawn_env())
        self.assertEqual(code, 2)
        self.assertNotIn('Traceback', err)
        self.assertEqual(self.srv.calls(), [])

    def test_refusals_are_two_with_no_call_at_all(self):
        made = self.json_of(self.backup()[1])
        calls_before = len(self.srv.calls())
        elsewhere = os.path.join(self.srv.root, 'elsewhere')
        os.makedirs(elsewhere)
        shutil.copy(made['path'], elsewhere)
        shutil.copy(made['path'] + '.json', elsewhere)
        cases = [
            ['backup', '--database', 'master', '--label', 'x', '--dir', self.srv.copies],
            ['backup', '--database', 'app]db', '--label', 'x', '--dir', self.srv.copies],
            ['backup', '--database', 'appdb', '--label', 'BAD LABEL', '--dir', self.srv.copies],
            ['backup', '--database', 'appdb', '--label', 'x', '--dir', self.srv.copies, '--server', 'db1.example.test'],
            ['backup', '--database', 'appdb', '--label', 'x', '--server', 'db1.example.test', '--allow-host', 'db1.example.test'],
            ['backup', '--database', 'appdb', '--label', 'x', '--dir', 'relative/dir'],
            ['backup', '--database', 'appdb', '--label', 'x', '--dir', self.srv.copies + '/../up'],
            ['backup', '--database', 'appdb', '--label', 'x', '--dir', self.srv.copies, '--server', 'db1.example.test', '--trust-server-certificate'],
            ['restore', '--file', made['path'], '--database', 'appdb', '--dir', self.srv.copies],
            ['restore', '--file', made['path'], '--database', 'appdb', '--dir', self.srv.copies, '--confirm', 'other'],
            ['restore', '--file', made['path'], '--database', 'appdb', '--dir', self.srv.copies, '--confirm', 'appdb', '--server', 'db1.example.test'],
            ['restore', '--file', made['path'][:-4] + 'zz.bak', '--database', 'appdb', '--dir', self.srv.copies, '--confirm', 'appdb'],
            ['restore', '--file', os.path.join(elsewhere, os.path.basename(made['path'])), '--database', 'appdb', '--dir', self.srv.copies,
             '--confirm', 'appdb'],
            ['restore', '--file', made['path'], '--database', 'master', '--dir', self.srv.copies, '--confirm', 'master'],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                code, out, err = self.spawn(argv, self.spawn_env())
                self.assertEqual(code, 2, out + err)
                self.assertNotIn('Traceback', err)
                self.assertEqual(len(self.srv.calls()), calls_before)
        self.assertEqual(self.srv.mutating_kinds(), ['backup'])
        self.assertEqual(self.srv.data(), S4_ORIGINAL)

    def test_restore_of_a_database_that_does_not_exist_is_a_refusal_after_reading_the_state_only(self):
        made = self.json_of(self.backup()[1])
        state = self.srv.state()
        state['dbs'] = {}
        self.srv.save(state)
        before = self.srv.kinds().count('backup')
        code, out, err = self.restore(made['path'])
        self.assertEqual(code, 2, out + err)
        self.assertEqual(self.srv.mutating_kinds().count('restore'), 0)
        self.assertEqual(self.srv.kinds().count('backup'), before)

    def test_a_sql_error_in_the_backup_is_one_and_says_the_state_may_have_changed(self):
        self.srv.set_flag('fail', ['backup'])
        code, out, err = self.backup()
        self.assertEqual(code, 1)
        self.assertIn(self.m.CHANGED_STATE_LINE, err)
        self.assertNotIn('Traceback', err)
        self.assertEqual(self.srv.mutating_kinds(), ['backup'])

    def test_a_failed_restore_is_one_reports_the_real_state_and_the_way_back(self):
        made = self.json_of(self.backup()[1])
        self.srv.set_data(S4_MODIFIED)
        self.srv.set_flag('fail', ['restore'])
        code, out, err = self.restore(made['path'])
        self.assertEqual(code, 1, out + err)
        self.assertIn(self.m.CHANGED_STATE_LINE, err)
        self.assertIn('RESTORING', err)
        self.assertIn('way back', err)
        self.assertIn('pre-restore', err)
        self.assertEqual(sum(1 for name in os.listdir(self.srv.copies) if 'pre-restore' in name and name.endswith('.bak')), 1)
        self.assertNotIn('Traceback', err)

    def test_a_failed_verify_before_any_change_still_leaves_the_database_alone(self):
        made = self.json_of(self.backup()[1])
        self.srv.set_data(S4_MODIFIED)
        self.srv.set_flag('fail', ['verify'])
        code, out, err = self.restore(made['path'])
        self.assertEqual(code, 2, out + err)  # nothing was changed: BLOCKED, not FAIL
        self.assertNotIn(self.m.CHANGED_STATE_LINE, err)
        self.assertEqual(self.srv.data(), S4_MODIFIED)
        self.assertEqual(self.srv.mutating_kinds(), ['backup'])

    # ---- the child environment is a whitelist
    def test_only_the_whitelist_reaches_sqlcmd(self):
        extra = {'SQLCMDINI': os.path.join(self.srv.root, 'sqlcmd.ini'), 'SQLCMDDBNAME': 'otherdb', 'SQLCMDSERVER': 'elsewhere',
                 'SQLCMDWORKSTATION': 'ws', 'PYTHONPATH': self.srv.root, 'SECRET_TEST_VARIABLE': 'value', 'VIRTUAL_ENV': self.srv.root}
        code, out, err = self.backup(**extra)
        self.assertEqual(code, 0, out + err)
        calls = self.srv.calls()
        self.assertTrue(calls)
        for row in calls:
            self.assertIsNone(row['sqlcmdini'])
            self.assertIsNone(row['sqlcmddbname'])
            for name in ('SQLCMDINI', 'SQLCMDDBNAME', 'SQLCMDSERVER', 'SQLCMDWORKSTATION', 'PYTHONPATH', 'SECRET_TEST_VARIABLE', 'VIRTUAL_ENV',
                         'PYTHONIOENCODING'):
                self.assertNotIn(name, row['env'])
            self.assertIn('PATH', row['env'])
            self.assertIn('SQLCMDPASSWORD', row['env'])
            self.assertEqual(row['argv'][row['argv'].index('-d') + 1], 'master')

    def test_the_child_runs_in_a_neutral_directory_not_in_the_cwd_of_the_tool(self):
        code, out, err = self.backup()
        self.assertEqual(code, 0, out + err)
        for row in self.srv.calls():
            self.assertNotEqual(os.path.realpath(row['cwd']), os.path.realpath(self.srv.work))
        self.assertEqual(os.listdir(self.srv.work), [])

    def test_the_child_runs_in_a_private_empty_directory_that_is_removed_afterwards(self):
        # Revision 4: a mkdtemp directory (mode 0700), not the copy directory, not the shared temporary directory, cleaned after the run.
        scratch = os.path.join(self.srv.root, 'scratch')
        os.mkdir(scratch)
        for flag, expected in ((None, 0), ('backup', 1)):
            with self.subTest(flag=flag):
                if flag:
                    self.srv.set_flag('fail', [flag])
                before = len(self.srv.calls())
                code, out, err = self.backup(TMPDIR=scratch)
                self.assertEqual(code, expected, out + err)
                rows = self.srv.calls()[before:]
                self.assertTrue(rows)
                for row in rows:
                    cwd = os.path.realpath(row['cwd'])
                    self.assertEqual(os.path.dirname(cwd), os.path.realpath(scratch))
                    self.assertNotEqual(cwd, os.path.realpath(self.srv.copies))
                    self.assertNotEqual(cwd, os.path.realpath(tempfile.gettempdir()))
                    self.assertEqual(row['cwdmode'], '0o700')
                    self.assertEqual(row['cwdentries'], [])
                    self.assertFalse(os.path.exists(cwd))
                self.assertEqual(os.listdir(scratch), [])
        self.assertEqual(os.listdir(self.srv.work), [])

    def test_a_dry_run_and_a_refusal_leave_no_temporary_directory_behind(self):
        scratch = os.path.join(self.srv.root, 'scratch')
        os.mkdir(scratch)
        self.assertEqual(self.backup('--dry-run', TMPDIR=scratch)[0], 3)
        self.assertEqual(self.spawn(['backup', '--database', 'master', '--label', 'x', '--dir', self.srv.copies], self.spawn_env(TMPDIR=scratch))[0], 2)
        self.assertEqual(os.listdir(scratch), [])

    def test_nothing_is_left_in_the_working_directory_by_any_command(self):
        made = self.json_of(self.backup()[1])
        self.restore(made['path'])
        self.spawn(['--selftest'], self.spawn_env())
        self.spawn(['backup', '--database', 'master', '--label', 'x'], self.spawn_env())
        self.assertEqual(os.listdir(self.srv.work), [])


# ---------------------------------------------------------------- S4b: secrets in the output of the tabular reads
# The runner hands table output to the parser raw (never masked there), so the only defence for identity, state, HEADERONLY
# and VERIFYONLY is the masking in run_guarded, format_result, _MaskedStream and main. The faked engine below echoes the
# login secret (assembled at run time, wrapped in markers) into those reads, as an error and as a parseable cell.

LEAK_HOOK = r'''
LEAKS = state.get('leaks') or {}
_kind = next((k for p, k in (('SELECT @@SERVERNAME', 'identity'), ('SELECT state_desc', 'state'), ('RESTORE VERIFYONLY', 'verify'),
                             ('RESTORE HEADERONLY', 'header')) if sql.startswith(p)), None)
_mode = LEAKS.get(_kind)
_pw = os.environ.get('SQLCMDPASSWORD', '')
_how = state.get('variant', 'plain')
if _how == 'url':
    import urllib.parse
    _pw = urllib.parse.quote(_pw, safe='')
elif _how == 'b64':
    import base64
    _pw = base64.b64encode(_pw.encode()).decode()
LEAK = 'LEAKSTART' + _pw + 'LEAKEND'
if _mode == 'fail':
    sys.stderr.write(LEAK + '\n')
    fail(4060, 'Cannot open the object ' + LEAK, state)
if _mode == 'extra' and _kind == 'identity':
    table(['server_name', 'machine_name', 'instance_name', 'UserName'], [[NODE, NODE, 'NULL', LEAK]])
    sys.exit(0)
if _mode == 'name' and _kind == 'identity':
    table(['server_name', 'machine_name', 'instance_name'], [[LEAK, NODE, 'NULL']])
    sys.exit(0)
if _kind == 'state' and _mode in ('extra', 'desc'):
    _db = dbs.get(re.search(r"WHERE name = N'(\w+)'$", sql).group(1))
    table(['state_desc', 'user_access_desc', 'recovery_model_desc', 'UserName'],
          [[LEAK if _mode == 'desc' else _db['state'], _db['access'], _db['recovery'], LEAK]])
    sys.exit(0)
if _kind == 'header' and _mode == 'extra':
    _snap = read_backup(re.fullmatch(r"RESTORE HEADERONLY FROM DISK = N'(.*)'", sql).group(1), state)
    table(['DatabaseName', 'BackupName', 'BackupType', 'ServerName', 'MachineName', 'BackupSetGUID', 'FirstLSN', 'LastLSN', 'BackupFinishDate'],
          [[_snap['db'], LEAK, '1', NODE, NODE, _snap['guid'], _snap['first'], _snap['last'], '2026-11-22 10:20:30.000']])
    sys.exit(0)
'''
LEAK_ANCHOR = "if sql.startswith('SELECT @@SERVERNAME'):\n    table(['server_name'"


class LeakServer(FakeServer):
    """FakeServer whose sqlcmd also echoes the login secret into the tabular reads, as the state flags 'leaks' and 'variant' say."""

    def __init__(self, root):
        self._leak_source = FAKE_SQLCMD_SOURCE.replace(LEAK_ANCHOR, LEAK_HOOK + LEAK_ANCHOR, 1)
        assert self._leak_source != FAKE_SQLCMD_SOURCE
        super().__init__(root)
        text = '#!' + sys.executable + '\n' + self._leak_source.replace('@@STATE@@', repr(self.state_path)).replace('@@LOG@@', repr(self.log_path))
        with open(self.script, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)

    def leaks(self, variant='plain', **modes):
        state = self.state()
        state['leaks'] = modes
        state['variant'] = variant
        self.save(state)


# (label, password, variant echoed by the engine); the short secret is checked only in the marked form: it also occurs in plain text
LEAK_CASES = (
    ('plain', fake_password('Qw', '4', '-', 'rT', '+', 'v8'), 'plain'),
    ('url', fake_password('Qw', '4', '-', 'rT', '+', 'v8'), 'url'),
    ('b64', fake_password('Qw', '4', '-', 'rT', '+', 'v8'), 'b64'),
    ('short', 'ab' + 'c', 'plain'),
)


class LeakChecks(object):
    """Shared by the in-process and the child-process variants: one world per case, one run of the tool, then a scan of everything."""

    def go(self, argv, password):
        raise NotImplementedError

    def variants_of(self, password):
        raw = password.encode('utf-8')
        found = [password, urllib.parse.quote(password, safe=''), urllib.parse.quote_plus(password), base64.b64encode(raw).decode(),
                 base64.b64encode(raw).decode().rstrip('='), base64.urlsafe_b64encode(raw).decode()]
        return [item for item in dict.fromkeys(found) if item]

    def world(self, password):
        self.password = password
        srv = LeakServer(os.path.realpath(tempfile.mkdtemp(prefix='dbsnap-s4b-')))
        self.addCleanup(shutil.rmtree, srv.root, True)
        self.srv = srv
        return srv

    def scan(self, texts, password):
        """Neither the secret nor its variants, in the marked form (always) and bare (unless the secret is short), in any output or file."""
        for variant in self.variants_of(password):
            forms = ['LEAKSTART' + variant] + ([variant] if len(password) > 6 else [])
            for form in forms:
                for text in texts:
                    self.assertNotIn(form, text)
                for name, blob in self.srv.tree():
                    self.assertNotIn(form, name)
                    self.assertNotIn(form.encode(), blob)

    def backup_argv(self, *extra):
        return ['backup', '--database', 'appdb', '--label', 'snap', '--dir', self.srv.copies] + list(extra)

    def restore_argv(self, path, *extra):
        return ['restore', '--file', path, '--database', 'appdb', '--confirm', 'appdb', '--dir', self.srv.copies] + list(extra)

    def made_copy(self, password):
        code, out, err = self.go(self.backup_argv('--json'), password)
        self.assertEqual(code, 0, out + err)
        path = json.loads(out[out.index('{'):out.rindex('}') + 1])['path']
        self.srv.set_data(S4_MODIFIED)
        return path

    # the exit code of a failing read: 2 while nothing was changed, 1 once the BACKUP (the first mutating call) has started
    BACKUP_READ_CODES = {'identity': 2, 'header': 1, 'verify': 1}
    TABULAR_READS = ('identity', 'state', 'header')

    def check_failure(self, code, out, err, expected, password, kind=None):
        self.assertEqual(code, expected, out + err)
        if kind not in self.TABULAR_READS:  # revision 4: the output of a tabular read is not printed, only the plain reads are
            self.assertIn('***', err)
            self.assertIn('Msg 4060', err)
        self.assertNotIn('Traceback', out + err)
        self.scan([out, err], password)

    # ---- backup: failures of the tabular reads and of VERIFYONLY
    def test_backup_failing_reads_are_masked_in_stderr_stdout_json_and_files(self):
        for kind in ('identity', 'header', 'verify'):
            for label, password, variant in LEAK_CASES:
                for extra in ([], ['--json']):
                    with self.subTest(kind=kind, case=label, json=bool(extra)):
                        srv = self.world(password)
                        srv.leaks(variant, **{kind: 'fail'})
                        code, out, err = self.go(self.backup_argv(*extra), password)
                        self.check_failure(code, out, err, self.BACKUP_READ_CODES[kind], password, kind)
                        self.assertEqual(srv.kinds()[-1], kind)

    # ---- backup: a parseable row that carries the secret
    def test_backup_successful_rows_with_the_secret_in_an_extra_cell_leave_no_trace(self):
        for kind in ('identity', 'header'):
            for label, password, variant in LEAK_CASES:
                with self.subTest(kind=kind, case=label):
                    srv = self.world(password)
                    srv.leaks(variant, **{kind: 'extra'})
                    code, out, err = self.go(self.backup_argv('--json', '--sha256'), password)
                    self.assertEqual(code, 0, out + err)
                    self.scan([out, err], password)
                    self.assertTrue(any(name.endswith('.bak.json') for name, _ in srv.tree()))

    def test_backup_secret_in_the_engine_name_that_reaches_the_sidecar_is_not_written_in_the_clear(self):
        for label, password, variant in LEAK_CASES:
            with self.subTest(case=label):
                srv = self.world(password)
                srv.leaks(variant, identity='name')
                code, out, err = self.go(self.backup_argv('--json'), password)
                self.assertEqual(code, 0, out + err)
                self.scan([out, err], password)

    # ---- restore
    def test_restore_failing_reads_are_masked_in_stderr_stdout_json_and_files(self):
        for kind in ('state', 'identity', 'header', 'verify'):
            for label, password, variant in LEAK_CASES:
                for extra in ([], ['--json']):
                    with self.subTest(kind=kind, case=label, json=bool(extra)):
                        srv = self.world(password)
                        path = self.made_copy(password)
                        srv.leaks(variant, **{kind: 'fail'})
                        code, out, err = self.go(self.restore_argv(path, *extra), password)
                        self.check_failure(code, out, err, 2, password, kind)  # every restore read comes before the first change
                        self.assertEqual(srv.data(), S4_MODIFIED)
                        self.assertNotIn('restore', srv.mutating_kinds())

    def test_restore_successful_rows_with_the_secret_in_an_extra_cell_leave_no_trace(self):
        for kind in ('state', 'identity', 'header'):
            for label, password, variant in LEAK_CASES:
                with self.subTest(kind=kind, case=label):
                    srv = self.world(password)
                    path = self.made_copy(password)
                    srv.leaks(variant, **{kind: 'extra'})
                    code, out, err = self.go(self.restore_argv(path, '--json'), password)
                    self.assertEqual(code, 0, out + err)
                    self.assertEqual(srv.data(), S4_ORIGINAL)
                    self.scan([out, err], password)

    def test_restore_secret_in_the_database_state_text_is_masked_in_the_refusal(self):
        for label, password, variant in LEAK_CASES:
            with self.subTest(case=label):
                srv = self.world(password)
                path = self.made_copy(password)
                srv.leaks(variant, state='desc')
                code, out, err = self.go(self.restore_argv(path, '--json'), password)
                self.assertEqual(code, 2, out + err)
                self.assertIn('Refusal', err)
                self.assertIn('LEAKSTART***LEAKEND', err)
                self.assertEqual(srv.data(), S4_MODIFIED)
                self.scan([out, err], password)


class LeakInProcess(LeakChecks, Base):
    def go(self, argv, password):
        out, err = io.StringIO(), io.StringIO()
        environ = {'PATH': self.srv.bin + os.pathsep + '/usr/bin' + os.pathsep + '/bin', 'HOME': self.srv.home, 'LANG': 'C.UTF-8',
                   'SQLCMDUSER': 'sa_test', 'SQLCMDPASSWORD': password}
        code = self.m.main(argv, environ=environ, stdout=out, stderr=err, platform=sys.platform)
        return code, out.getvalue(), err.getvalue()

    @classmethod
    def setUpClass(cls):
        if os.name == 'nt':
            raise unittest.SkipTest(POSIX_SKIP_REASON)


class LeakInProcessLayers(Base):
    """Each masking layer on its own, with an unmasked secret handed straight to it (defence in depth)."""

    def setUp(self):
        super().setUp()
        self.secret = fake_password('Lm', '2', '-', 'Vb', '%', 'k9')
        self.masker = self.m.Masker([self.secret])

    def test_the_masked_stream_masks_every_write_and_the_secret_never_reaches_the_real_stream(self):
        real = io.StringIO()
        stream = self.m._MaskedStream(real, self.masker)
        for text in (self.secret, 'x ' + urllib.parse.quote(self.secret, safe='') + ' y\n', 'm ' + base64.b64encode(self.secret.encode()).decode()):
            stream.write(text)
        stream.flush()
        self.assertNotIn(self.secret, real.getvalue())
        self.assertNotIn(urllib.parse.quote(self.secret, safe=''), real.getvalue())
        self.assertNotIn(base64.b64encode(self.secret.encode()).decode().rstrip('='), real.getvalue())
        self.assertEqual(real.getvalue().count('***'), 3)

    def test_run_guarded_masks_an_error_message_that_holds_the_secret_even_on_a_raw_stream(self):
        for exc in (self.m.SqlFailure('read failed: ' + self.secret), self.m.Refusal('state ' + self.secret), RuntimeError('boom ' + self.secret)):
            with self.subTest(exc=type(exc).__name__):
                err = io.StringIO()
                state = self.m.RunState()

                def boom(exc=exc):
                    raise exc
                code = self.m.run_guarded(boom, state, err, self.masker)
                self.assertIn(code, (1, 2))
                self.assertNotIn(self.secret, err.getvalue())
                self.assertIn('***', err.getvalue())

    def test_format_result_masks_a_secret_in_every_nested_value_in_text_and_json(self):
        result = {'status': 'PASS', 'path': '/h/x/' + self.secret, 'warnings': ['w ' + self.secret], 'nested': {self.secret: [self.secret]}}
        for as_json in (False, True):
            text = self.m.format_result(result, as_json, self.masker)
            self.assertNotIn(self.secret, text)
            self.assertIn('***', text)


class LeakProcess(LeakChecks, Base):
    @classmethod
    def setUpClass(cls):
        if os.name == 'nt':
            raise unittest.SkipTest(POSIX_SKIP_REASON)

    def go(self, argv, password):
        environ = {'PATH': self.srv.bin + os.pathsep + '/usr/bin' + os.pathsep + '/bin', 'HOME': self.srv.home, 'LANG': 'C.UTF-8',
                   'SQLCMDUSER': 'sa_test', 'SQLCMDPASSWORD': password, 'PYTHONIOENCODING': 'utf-8'}
        done = subprocess.run([sys.executable, MODULE_PATH] + argv, env=environ, cwd=self.srv.work, capture_output=True, timeout=120)
        return done.returncode, done.stdout.decode('utf-8', 'replace'), done.stderr.decode('utf-8', 'replace')
