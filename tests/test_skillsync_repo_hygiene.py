"""Strażnik higieny publicznego rdzenia: specyfikacje i plany zadań nie są śledzone w repozytorium.

Specyfikacje i plany żyją poza rdzeniem (repozytorium operacyjne); do rdzenia trafia kod, dokumentacja bieżącej wersji,
wiedza domenowa i lekcje. Test działa na każdym PR w CI (`git ls-files`); bez gita albo poza checkoutem pomija się z powodem.
"""
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FORBIDDEN = ('docs/specs', 'docs/plans')


def tracked(*prefixes):
    git = shutil.which('git')
    if not git:
        return None
    done = subprocess.run([git, 'ls-files', '-z', '--', *prefixes], cwd=str(REPO), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=60)
    if done.returncode != 0:
        return None
    return [name for name in done.stdout.decode('utf-8', 'replace').split('\0') if name]


class RepoHygieneTests(unittest.TestCase):
    def test_no_tracked_specs_or_plans(self):
        files = tracked(*FORBIDDEN)
        if files is None:
            self.skipTest('brak gita albo katalog nie jest checkoutem repozytorium')
        self.assertEqual(files, [], 'specyfikacje i plany nie należą do publicznego rdzenia (trzymaj je w repozytorium operacyjnym): '
                         + ', '.join(files))

    def test_the_guard_sees_a_tracked_file(self):
        # kontrola: ten sam mechanizm widzi śledzony plik (sam test), więc pusty wynik powyżej nie jest pustą kontrolą
        files = tracked('tests/test_skillsync_repo_hygiene.py')
        if files is None:
            self.skipTest('brak gita albo katalog nie jest checkoutem repozytorium')
        if not files:
            self.skipTest('plik testu jeszcze nie jest śledzony (przed pierwszym commitem)')
        self.assertEqual(files, ['tests/test_skillsync_repo_hygiene.py'])


if __name__ == '__main__':
    unittest.main()
