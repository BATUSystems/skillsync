"""Strażnik higieny publicznego rdzenia: specyfikacje i plany zadań nie są śledzone w repozytorium.

Specyfikacje i plany żyją poza rdzeniem (repozytorium operacyjne); do rdzenia trafia kod, dokumentacja bieżącej wersji,
wiedza domenowa i lekcje. Test działa na każdym PR w CI (`git ls-files`). W CI (zmienne `CI` albo `GITHUB_ACTIONS`) brak gita
lub błąd gita to FAIL, nie pominięcie; lokalnie bez gita test pomija się z powodem.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FORBIDDEN = ('docs/specs', 'docs/plans')
# odwołanie do konkretnego (datowanego) pliku specyfikacji albo planu; ogólna konwencja katalogów projektu (`docs/specs/*.md`) jest dozwolona
DATED = re.compile(r'docs/(?:specs|plans)/[0-9]{4}-[0-9]{2}-[0-9]{2}[A-Za-z0-9_.-]*')
IN_CI = bool(os.environ.get('CI') or os.environ.get('GITHUB_ACTIONS'))


def tracked(*prefixes, cwd=REPO):
    """Śledzone pliki pod prefiksami albo None, gdy gita nie ma lub `git ls-files` się nie powiódł."""
    git = shutil.which('git')
    if not git:
        return None
    done = subprocess.run([git, 'ls-files', '-z', '--', *prefixes], cwd=str(cwd), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=60)
    if done.returncode != 0:
        return None
    return [name for name in done.stdout.decode('utf-8', 'replace').split('\0') if name]


class RepoHygieneTests(unittest.TestCase):
    def need(self, files):
        if files is None:
            if IN_CI:
                self.fail('w CI musi działać git ls-files (brak gita albo błąd gita): strażnik nic by nie sprawdził')
            self.skipTest('brak gita albo katalog nie jest checkoutem repozytorium')
        return files

    def test_no_tracked_specs_or_plans(self):
        files = self.need(tracked(*FORBIDDEN))
        self.assertEqual(files, [], 'specyfikacje i plany nie należą do publicznego rdzenia (trzymaj je w repozytorium operacyjnym): '
                         + ', '.join(files))

    def test_no_references_to_dated_specs_or_plans_outside_tests(self):
        # `tests/` zostaje poza kontrolą: dane syntetyczne testów (katalogi tymczasowe projektów) używają takich nazw
        files = self.need(tracked('.'))
        hits = []
        for name in files:
            if name.startswith('tests/'):
                continue
            try:
                text = (REPO / name).read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if DATED.search(line):
                    hits.append('%s:%d' % (name, number))
        self.assertEqual(hits, [], 'odwołania do specyfikacji i planów, których rdzeń nie przechowuje (użyj opisu bez ścieżki): ' + ', '.join(hits[:20]))

    def test_the_dated_reference_pattern_matches_what_it_should(self):
        self.assertTrue(DATED.search('plan docs/plans/2026-11-05-warstwy-generator.md (K1)'))
        self.assertTrue(DATED.search('`docs/specs/2026-01-02-a.md`'))
        self.assertFalse(DATED.search('katalog docs/specs/*.md w projekcie'))
        self.assertFalse(DATED.search('--spec docs/specs/zwroty.md'))

    def test_the_guard_lists_nested_files_of_a_directory_prefix(self):
        # kontrola: prefiks katalogu bez gwiazdki zwraca pliki zagnieżdżone, a na tym opiera się strażnik
        files = self.need(tracked('tests'))
        if not files:
            self.skipTest('katalog tests nie jest jeszcze śledzony (przed pierwszym commitem)')
        self.assertIn('tests/test_skillsync_repo_hygiene.py', files)

    def test_the_guard_flags_a_spec_in_a_scratch_repository(self):
        with tempfile.TemporaryDirectory(prefix='hygiene-') as tmp:
            git = shutil.which('git')
            if not git:
                self.need(None)
            subprocess.run([git, 'init', '-q', tmp], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            (Path(tmp) / 'docs' / 'specs' / 'zagniezdzony').mkdir(parents=True)
            spec = Path(tmp) / 'docs' / 'specs' / 'zagniezdzony' / 'plik ze spacją.md'
            spec.write_text('x\n', encoding='utf-8')
            subprocess.run([git, 'add', '--', str(spec.relative_to(tmp))], cwd=tmp, check=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=60)
            files = self.need(tracked(*FORBIDDEN, cwd=tmp))
            self.assertEqual(files, ['docs/specs/zagniezdzony/plik ze spacją.md'])
            self.assertEqual(tracked('docs/plans', cwd=tmp), [], 'control: a directory without tracked files is clean')


if __name__ == '__main__':
    unittest.main()
