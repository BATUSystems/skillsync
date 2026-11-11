"""WIN-FIX: dwie poprawki produktu z triage testów Windows.

- G4: polskie wyjście CLI nie może paść na `UnicodeEncodeError` przy przekierowanym stdout/stderr w cp1252
  (`fsutil.ensure_utf8_streams`; samodzielny `prototype_server.py` ma własną kopię w `main()`). Bez Windowsa: strumień cp1252
  symulujemy przez `io.TextIOWrapper` i `PYTHONIOENCODING=cp1252`.
- G5: `fsutil.rmtree_force` usuwa drzewo z plikami tylko do odczytu (obiekty `.git` na Windows); wszędzie wymuszamy ścieżkę błędu
  deterministycznym wywołaniem callbacku, a prawdziwy plik tylko do odczytu sprawdzamy tam, gdzie system go respektuje.
"""
import io
import os
import queue
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tests.cli_module import ROOT  # noqa: E402
from skillsync_workflow import fsutil, prototype_server  # noqa: E402  (prototype_server: kopia ensure w samodzielnym pliku)

PL = 'Serwer prototypów: kończy się (żółć)\n'


def cp1252_stream():
    return io.TextIOWrapper(io.BytesIO(), encoding='cp1252', errors='strict', newline='')


class EnsureUtf8StreamsTests(unittest.TestCase):
    def test_cp1252_stream_would_crash_without_the_fix(self):
        with self.assertRaises(UnicodeEncodeError):
            cp1252_stream().write(PL)

    def test_cp1252_streams_are_switched_to_utf8_and_take_polish_text(self):
        out, err = cp1252_stream(), cp1252_stream()
        with mock.patch.object(sys, 'stdout', out), mock.patch.object(sys, 'stderr', err):
            fsutil.ensure_utf8_streams()
            sys.stdout.write(PL)
            sys.stderr.write(PL)
            sys.stdout.flush()
            sys.stderr.flush()
        self.assertEqual(out.buffer.getvalue().decode('utf-8'), PL)
        self.assertEqual(err.buffer.getvalue().decode('utf-8'), PL)

    def test_utf8_stream_and_streams_without_reconfigure_are_left_alone(self):
        utf8 = io.TextIOWrapper(io.BytesIO(), encoding='utf-8')
        plain = io.StringIO()
        with mock.patch.object(sys, 'stdout', utf8), mock.patch.object(sys, 'stderr', plain):
            fsutil.ensure_utf8_streams()
        self.assertEqual(utf8.errors, 'strict')  # już UTF-8: bez zmian
        plain.write(PL)
        self.assertEqual(plain.getvalue(), PL)

    def test_unencodable_text_is_replaced_not_raised(self):
        out = cp1252_stream()
        with mock.patch.object(sys, 'stdout', out):
            fsutil.ensure_utf8_streams()
            sys.stdout.write('ok \ud800\n')  # pojedynczy surogat nie ma kodowania UTF-8
            sys.stdout.flush()
        self.assertTrue(out.buffer.getvalue().startswith(b'ok '))

    def test_cli_run_switches_streams_before_main(self):
        from skillsync_workflow import cli
        seen = []
        out = cp1252_stream()
        with mock.patch.object(sys, 'stdout', out), \
                mock.patch.object(cli, 'main', lambda: seen.append(sys.stdout.encoding) or 0), \
                self.assertRaises(SystemExit):
            cli.run()
        self.assertEqual(seen, ['utf-8'])


class StandaloneServerCp1252Tests(unittest.TestCase):
    """Proces `prototype_server.py serve` z rurą w cp1252 (to robi CI Windows i zadania bez konsoli) wypisuje adres."""

    def test_serve_prints_its_address_when_stdout_is_cp1252(self):
        from tests.test_skillsync_prototype_server import MODULE, FakeClock, build_world
        tmp = tempfile.mkdtemp(prefix='win-fix-')
        self.addCleanup(shutil.rmtree, tmp, True)
        root, users = build_world(tmp, FakeClock())
        host = os.path.join(tmp, 'host')
        os.makedirs(host)
        module = os.path.join(host, 'prototype_server.py')
        shutil.copy(str(MODULE), module)
        env = dict(os.environ, PYTHONIOENCODING='cp1252', PYTHONUTF8='0')
        process = subprocess.Popen([sys.executable, module, 'serve', '--root', root, '--users', users, '--port', '0'],
                                   cwd=host, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: (process.kill(), process.wait(10), process.stdout.close(), process.stderr.close()))
        lines = queue.Queue()
        threading.Thread(target=lambda: [lines.put(line) for line in iter(process.stdout.readline, b'')], daemon=True).start()
        try:
            line = lines.get(timeout=30).decode('utf-8')
        except queue.Empty:
            process.kill()
            self.fail('serwer nie wypisał adresu: %s' % process.stderr.read().decode('utf-8', 'replace'))
        self.assertIn('http://127.0.0.1:', line)
        self.assertIn('Serwer prototypów', line)


def make_tree(base):
    tree = Path(base) / 'clone'
    objects = tree / '.git' / 'objects' / '04'
    objects.mkdir(parents=True)
    victim = objects / 'abc123'
    victim.write_bytes(b'x')
    (tree / 'README.md').write_text('hi\n')
    return tree, victim


class RmtreeForceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='win-fix-')
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for dirpath, dirnames, filenames in os.walk(self.tmp):
            for name in dirnames + filenames:
                try:
                    os.chmod(os.path.join(dirpath, name), stat.S_IRWXU)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, True)

    def test_the_error_callback_makes_a_read_only_file_removable_and_retries(self):
        _, victim = make_tree(self.tmp)
        victim.chmod(stat.S_IREAD)
        calls = []

        def unlink_once_denied(path):
            calls.append(path)
            if len(calls) == 1:
                raise PermissionError(13, 'Access is denied', path)  # pierwsza próba, jak WinError 5
            os.unlink(path)

        fsutil._retry_after_chmod(unlink_once_denied, str(victim), PermissionError(13, 'denied'))
        self.assertEqual(len(calls), 2)
        self.assertFalse(victim.exists())

    def test_callback_that_still_fails_is_swallowed_only_with_ignore_errors(self):
        def always_denied(path):
            raise PermissionError(13, 'denied', path)

        with self.assertRaises(PermissionError):
            fsutil._retry_after_chmod(always_denied, self.tmp, PermissionError(13, 'denied'), ignore_errors=False)
        fsutil._retry_after_chmod(always_denied, self.tmp, PermissionError(13, 'denied'), ignore_errors=True)

    @unittest.skipIf(os.name != 'nt' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'root ignoruje prawa do plików')
    def test_a_tree_with_read_only_entries_is_removed(self):
        tree, victim = make_tree(self.tmp)
        if os.name == 'nt':
            victim.chmod(stat.S_IREAD)  # atrybut tylko do odczytu blokuje unlink
        else:
            victim.parent.chmod(0o555)  # unlink w katalogu bez prawa zapisu kończy się EACCES
            with self.assertRaises(PermissionError):
                shutil.rmtree(tree)
        fsutil.rmtree_force(tree)
        self.assertFalse(tree.exists())

    def test_ignore_errors_keeps_the_old_contract_for_a_missing_path(self):
        fsutil.rmtree_force(Path(self.tmp) / 'nie-ma', ignore_errors=True)
        with self.assertRaises(FileNotFoundError):
            fsutil.rmtree_force(Path(self.tmp) / 'nie-ma')


class RmtreeForceEdgeCaseTests(unittest.TestCase):
    """Uwagi z review: symlinki, katalog nadrzędny korzenia, operacje, których chmod nie naprawia."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='win-fix-')
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for dirpath, dirnames, filenames in os.walk(self.tmp):
            for name in dirnames + filenames:
                try:
                    os.chmod(os.path.join(dirpath, name), stat.S_IRWXU)
                except OSError:
                    pass
        os.chmod(self.tmp, stat.S_IRWXU)
        shutil.rmtree(self.tmp, True)

    posix_user = unittest.skipIf(os.name == 'nt' or (hasattr(os, 'geteuid') and os.geteuid() == 0),
                                 'wymaga POSIX i zwykłego użytkownika')

    @posix_user
    def test_a_symlink_inside_the_tree_is_removed_and_its_target_keeps_its_mode(self):
        target = Path(self.tmp) / 'outside'
        target.mkdir()
        target.chmod(0o700)
        tree = Path(self.tmp) / 'tree'
        tree.mkdir()
        (tree / 'link').symlink_to(target)
        tree.chmod(0o555)  # unlink wpisu w katalogu bez prawa zapisu
        fsutil.rmtree_force(tree)
        self.assertFalse(os.path.lexists(tree))
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)

    @posix_user
    def test_the_parent_of_the_root_is_never_changed(self):
        parent = Path(self.tmp) / 'parent'
        (parent / 'root').mkdir(parents=True)
        parent.chmod(0o555)  # rmdir(root) zawiedzie: brak prawa zapisu w rodzicu
        with self.assertRaises(PermissionError):
            fsutil.rmtree_force(parent / 'root')
        self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o555)
        fsutil.rmtree_force(parent / 'root', ignore_errors=True)  # bez wyjątku
        self.assertTrue((parent / 'root').exists())

    @unittest.skipIf(os.name == 'nt', 'symlinki wymagają uprawnień')
    def test_rmtree_on_a_symlink_is_refused_like_shutil_and_leaves_the_target(self):
        target = Path(self.tmp) / 'target'
        target.mkdir()
        link = Path(self.tmp) / 'link'
        link.symlink_to(target)
        with self.assertRaises(OSError):
            fsutil.rmtree_force(link)
        fsutil.rmtree_force(link, ignore_errors=True)
        self.assertTrue(link.is_symlink() and target.is_dir())

    def test_operations_that_chmod_cannot_fix_do_not_raise_a_type_error(self):
        for func in (os.open, os.path.islink, os.scandir):
            fsutil._retry_after_chmod(func, self.tmp, PermissionError(13, 'denied'), ignore_errors=True)
            with self.assertRaises(PermissionError):
                fsutil._retry_after_chmod(func, self.tmp, PermissionError(13, 'denied'))

    def test_a_missing_path_changes_no_permissions_and_keeps_the_error(self):
        before = stat.S_IMODE(os.stat(self.tmp).st_mode)
        with self.assertRaises(FileNotFoundError):
            fsutil.rmtree_force(Path(self.tmp) / 'nie-ma')
        self.assertEqual(stat.S_IMODE(os.stat(self.tmp).st_mode), before)

    def test_cli_remove_item_removes_a_tree_with_read_only_entries(self):
        from skillsync_workflow import cli
        tree, victim = make_tree(self.tmp)
        if os.name == 'nt':
            victim.chmod(stat.S_IREAD)
        elif hasattr(os, 'geteuid') and os.geteuid() == 0:
            self.skipTest('root ignoruje prawa do katalogów')
        else:
            victim.parent.chmod(0o555)
        cli.remove_item(tree)
        self.assertFalse(tree.exists())


class CloneRollbackTests(unittest.TestCase):
    def test_no_bare_rmtree_is_left_on_the_clone_paths(self):
        """Strażnik: rollback klonu (layers, setup) i `remove_item` (cli) idą przez `rmtree_force`."""
        for rel, forbidden in (('layers.py', 'shutil.rmtree('), ('setup_wizard.py', 'shutil.rmtree(path')):
            text = (ROOT / 'skillsync_workflow' / rel).read_text(encoding='utf-8')
            self.assertNotIn(forbidden, text, rel)
            self.assertIn('rmtree_force(', text, rel)
        cli_text = (ROOT / 'skillsync_workflow' / 'cli.py').read_text(encoding='utf-8')
        self.assertRegex(cli_text, r'def remove_item[^\n]*\n(?:.*\n){1,6}?\s+rmtree_force\(path\)')


if __name__ == '__main__':
    unittest.main()
