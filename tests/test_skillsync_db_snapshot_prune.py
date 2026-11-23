"""CORE-1142-T6, Dostawa 1: retencja migawek (`prune`) w db_snapshot.py i seam systemu plików (K9).

Spec: docs/specs/2026-11-23-db-snapshot-move-i-retencja.md (rewizja 5), część B. Wszystko jest wstrzykiwane (system plików, zegar, środowisko);
żaden proces ani `sqlcmd` nie jest uruchamiany. Symlinki i błędy `unlink` testujemy atrapą (bez pominięć testów na Windows: `check_skips`).
"""
import datetime
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import tmp_paths
from tests.test_skillsync_db_snapshot import Base


class RealFsSeam(Base):
    """K9: `listdir`, `lstat` (rodzaj, rozmiar, mtime), `isdir`, `unlink` w `RealFs`."""

    def setUp(self):
        super().setUp()
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name).resolve()
        self.fs = self.m.RealFs()

    def path(self, name):
        return str(tmp_paths.child(self.root, name))

    def test_listdir_returns_the_names_of_the_direct_entries(self):
        tmp_paths.write_file(self.root, 'a.bak', 'x')
        tmp_paths.write_file(self.root, 'b.bak.json', '{}')
        tmp_paths.make_dir(self.root, 'sub')
        self.assertEqual(sorted(self.fs.listdir(str(self.root))), ['a.bak', 'b.bak.json', 'sub'])

    def test_listdir_of_a_missing_directory_raises_oserror(self):
        with self.assertRaises(OSError):
            self.fs.listdir(self.path('missing'))

    def test_lstat_of_a_file_has_kind_file_size_and_mtime(self):
        target = tmp_paths.write_file(self.root, 'f.bak', '12345')
        os.utime(str(target), (1_700_000_000, 1_700_000_000))
        entry = self.fs.lstat(str(target))
        self.assertEqual((entry.kind, entry.size), ('file', 5))
        self.assertEqual(entry.mtime, 1_700_000_000)

    def test_lstat_of_a_directory_is_dir(self):
        self.assertEqual(self.fs.lstat(str(tmp_paths.make_dir(self.root, 'd.bak'))).kind, 'dir')

    def test_lstat_of_a_missing_path_raises_oserror(self):
        with self.assertRaises(OSError):
            self.fs.lstat(self.path('missing.bak'))

    def test_lstat_maps_the_mode_bits_to_the_kind_without_following_links(self):
        for mode, expected in ((stat.S_IFLNK, 'link'), (stat.S_IFREG, 'file'), (stat.S_IFDIR, 'dir'), (stat.S_IFIFO, 'other'), (stat.S_IFCHR, 'other')):
            fake = os.stat_result((mode | 0o644, 0, 0, 1, 0, 0, 7, 0, 1_700_000_000, 0))
            with mock.patch.object(os, 'lstat', return_value=fake) as patched:
                entry = self.fs.lstat('/any/path')
            patched.assert_called_once_with('/any/path')
            self.assertEqual((entry.kind, entry.size, entry.mtime), (expected, 7, 1_700_000_000), expected)

    def test_isdir_distinguishes_directories_files_and_missing_paths(self):
        target = tmp_paths.write_file(self.root, 'f.bak', 'x')
        self.assertIs(self.fs.isdir(str(self.root)), True)
        self.assertIs(self.fs.isdir(str(target)), False)
        self.assertIs(self.fs.isdir(self.path('missing')), False)

    def test_unlink_removes_the_file_and_only_the_file(self):
        target = tmp_paths.write_file(self.root, 'f.bak', 'x')
        other = tmp_paths.write_file(self.root, 'g.bak', 'y')
        self.fs.unlink(str(target))
        self.assertFalse(os.path.exists(str(target)))
        self.assertTrue(os.path.exists(str(other)))

    def test_unlink_of_a_missing_file_raises_filenotfounderror(self):
        with self.assertRaises(FileNotFoundError):
            self.fs.unlink(self.path('missing.bak'))

    def test_unlink_does_not_remove_a_directory(self):
        directory = tmp_paths.make_dir(self.root, 'd.bak')
        with self.assertRaises(OSError):
            self.fs.unlink(str(directory))
        self.assertTrue(os.path.isdir(str(directory)))


class MemFsSeam(Base):
    """The in-memory file system of the selftest has the same seam."""

    def test_listdir_lstat_isdir_and_unlink(self):
        fs = self.m._MemFs()
        fs.makedirs('/h/c', 0o700)
        fs.files['/h/c/a.bak'] = b'abc'
        fs.mtimes['/h/c/a.bak'] = 1_700_000_000.0
        fs.links.add('/h/c/l.bak')
        fs.files['/h/c/l.bak'] = b''
        fs.files['/h/other/x.bak'] = b'zzz'
        self.assertEqual(sorted(fs.listdir('/h/c')), ['a.bak', 'l.bak'])
        entry = fs.lstat('/h/c/a.bak')
        self.assertEqual((entry.kind, entry.size, entry.mtime), ('file', 3, 1_700_000_000.0))
        self.assertEqual(fs.lstat('/h/c/l.bak').kind, 'link')
        self.assertEqual(fs.lstat('/h/c').kind, 'dir')
        self.assertIs(fs.isdir('/h/c'), True)
        self.assertIs(fs.isdir('/h/c/a.bak'), False)
        with self.assertRaises(FileNotFoundError):
            fs.lstat('/h/c/missing.bak')
        with self.assertRaises(FileNotFoundError):
            fs.listdir('/h/nowhere')
        fs.unlink('/h/c/a.bak')
        self.assertNotIn('/h/c/a.bak', fs.files)
        with self.assertRaises(FileNotFoundError):
            fs.unlink('/h/c/a.bak')


if __name__ == '__main__':
    unittest.main()
