"""LINK-WIN: `is_library_symlink` rozpoznaje wiszący symlink biblioteki niezależnie od formy ścieżki (`\\\\?\\` na Windows).

Na Linuxie formę Windows wymuszamy: `Path.resolve` zwraca ścieżkę z prefiksem `\\\\?\\`, a szew `_WINDOWS_PATHS` włącza
zdejmowanie prefiksu w `_link_text`. Test czerwony na bazie: porównanie `relative_to` na surowych formach odrzuca taki symlink
(po przemianowaniu skilla w bibliotece `link` zostawiał osierocony `agentflow`).
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.cli_module import skillsync  # noqa: E402
from tests.layers_helpers import needs_os_symlinks  # noqa: E402

PREFIX = '\\\\?\\'


@needs_os_symlinks
class DanglingLibrarySymlinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='link-win-')).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.library = self.tmp / 'library'
        (self.library / 'skills' / 'feature').mkdir(parents=True)
        self.skills = self.tmp / 'project' / '.claude' / 'skills'
        self.skills.mkdir(parents=True)
        self.dangling = self.skills / 'agentflow'
        self.dangling.symlink_to(self.library / 'skills' / 'agentflow', target_is_directory=True)  # cel nie istnieje
        self.live = self.skills / 'feature'
        self.live.symlink_to(self.library / 'skills' / 'feature', target_is_directory=True)
        self.foreign = self.skills / 'obce'
        self.foreign.symlink_to(self.tmp / 'gdzie-indziej' / 'skill', target_is_directory=True)

    def with_windows_resolve(self):
        """`Path.resolve` wiszącego symlinku zwraca formę `\\\\?\\<ścieżka>`, a istniejących ścieżek (korzeń biblioteki) zwykłą:
        tak rozjeżdżają się formy na Windows i `relative_to` rzuca `ValueError`."""
        real = Path.resolve
        cache = {}  # wyniki liczone przed ewentualnym wyłączeniem `os.readlink` (realpath sam go używa)

        def windows_form(path, strict=False):
            if path not in cache:
                cache[path] = real(path, strict=strict)
            resolved = cache[path]
            return resolved if resolved.exists() else Path(PREFIX + str(resolved))

        for item in (self.dangling, self.live, self.foreign, self.library):
            windows_form(item)

        return mock.patch.object(Path, 'resolve', windows_form)

    def test_dangling_symlink_is_recognised_when_resolve_returns_the_extended_form(self):
        # bez kandydata z `readlink`: test wymusza samą ścieżkę `resolve` + zdejmowanie prefiksu
        with self.with_windows_resolve(), mock.patch.object(skillsync, '_WINDOWS_PATHS', True, create=True), \
                mock.patch.object(os, 'readlink', side_effect=OSError):
            self.assertTrue(skillsync.is_library_symlink(self.dangling, [self.library]))
            self.assertTrue(skillsync.is_library_symlink(self.live, [self.library]))

    def test_foreign_symlink_is_not_managed_in_the_extended_form_either(self):
        with self.with_windows_resolve(), mock.patch.object(skillsync, '_WINDOWS_PATHS', True, create=True):
            self.assertFalse(skillsync.is_library_symlink(self.foreign, [self.library]))

    def test_dangling_symlink_is_recognised_by_its_target_when_resolve_gives_an_unrelated_form(self):
        real = Path.resolve

        def unrelated_for_dangling(path, strict=False):
            resolved = real(path, strict=strict)
            return resolved if resolved.exists() else Path('X:/nieporownywalne')

        with mock.patch.object(Path, 'resolve', unrelated_for_dangling):
            self.assertTrue(skillsync.is_library_symlink(self.dangling, [self.library]))
            self.assertFalse(skillsync.is_library_symlink(self.foreign, [self.library]))

    def test_a_target_leaving_the_library_through_dotdot_is_not_managed(self):
        escaping = self.skills / 'ucieka'
        escaping.symlink_to(self.library / '..' / 'inne' / 'skill', target_is_directory=True)  # wiszący, poza biblioteką
        relative = self.skills / 'wzgledny'
        relative.symlink_to(Path('..') / '..' / '..' / 'library' / '..' / 'inne', target_is_directory=True)
        self.assertFalse(skillsync.is_library_symlink(escaping, [self.library]))
        self.assertFalse(skillsync.is_library_symlink(relative, [self.library]))

    def test_dangling_symlink_written_with_a_short_8dot3_form_is_still_managed(self):
        """LINK-WIN-2: cel wiszącego symlinku zostaje w formie z chwili utworzenia (np. `RUNNER~1`), a `resolve()` nie rozwija nieistniejącego celu;
        korzeń biblioteki ma formę długą. Atrapa: katalog nadrzędny w krótkiej formie to dowiązanie do długiej, `Path.resolve()` wiszącego linku oddaje tekst celu."""
        long_dir = self.tmp / 'Users-long'
        (long_dir / 'library' / 'skills').mkdir(parents=True)
        short = self.tmp / 'USERS~1'
        short.symlink_to(long_dir, target_is_directory=True)  # alias 8.3 istniejącego katalogu
        link = self.skills / 'migrated'
        link.symlink_to(short / 'library' / 'skills' / 'gone', target_is_directory=True)  # cel nie istnieje, forma krótka
        real = Path.resolve

        def windows_resolve(path, strict=False):
            if path == link:
                return Path(os.readlink(link))  # nierozwinięty tekst celu, jak realpath wiszącego linku na Windows
            return real(path, strict=strict)

        with mock.patch.object(Path, 'resolve', windows_resolve):
            self.assertTrue(skillsync.is_library_symlink(link, [long_dir / 'library']))
            self.assertFalse(skillsync.is_library_symlink(self.foreign, [long_dir / 'library']))

    def test_plain_posix_behaviour_is_unchanged(self):
        self.assertTrue(skillsync.is_library_symlink(self.dangling, [self.library]))
        self.assertTrue(skillsync.is_library_symlink(self.live, self.library))
        self.assertFalse(skillsync.is_library_symlink(self.foreign, [self.library]))
        regular = self.skills / 'zwykly'
        regular.mkdir()
        self.assertFalse(skillsync.is_library_symlink(regular, [self.library]))

    def test_prune_managed_removes_the_dangling_symlink_but_keeps_the_foreign_one(self):
        with self.with_windows_resolve(), mock.patch.object(skillsync, '_WINDOWS_PATHS', True, create=True):
            skillsync.prune_managed(self.skills, 'skill', {'feature'}, [self.library], {})
        self.assertFalse(os.path.lexists(self.dangling))
        self.assertTrue(self.live.is_symlink() and self.foreign.is_symlink())


class LinkTextSeamTests(unittest.TestCase):
    def test_link_text_strips_the_extended_prefix_only_with_windows_paths(self):
        with mock.patch.object(skillsync, '_WINDOWS_PATHS', True, create=True):
            self.assertEqual(skillsync._link_text(PREFIX + '/lib/x'), os.path.normcase('/lib/x'))
        with mock.patch.object(skillsync, '_WINDOWS_PATHS', False, create=True):
            self.assertEqual(skillsync._link_text(PREFIX + '/lib/x'), os.path.normcase(PREFIX + '/lib/x'))


if __name__ == '__main__':
    unittest.main()
