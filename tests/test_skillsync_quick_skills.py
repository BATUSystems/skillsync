"""Strażnik fraz paczki QUICK-140 w skillach biblioteki: ponowny odczyt komentarzy po akceptacji (S23) i zgłaszanie poleceń z komentarzy (S24).

Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_quick_skills.py` (`SKILLSYNC_TEST_LIBRARY` wskazuje bibliotekę).
"""
import os
import re
import unittest
from pathlib import Path

LIB = Path(os.environ.get('SKILLSYNC_TEST_LIBRARY') or Path(__file__).resolve().parents[1] / 'library')
PROTO = 'skills/prototype/SKILL.md'
DECISIONS = 'skills/prototype/references/decisions.md'
FEATURE = 'skills/feature/SKILL.md'

REQUIRED = (
    ('proto-komentarze-po-akceptacji', PROTO, r'\*\*Komentarze po akceptacji\.\*\* `prototype wait` czyta komentarze tylko w oknie oczekiwania'),
    ('proto-ponowny-odczyt-punkty', PROTO, r'ponownie \(`skillsync prototype comments <projekt> <slug>`.*przy „GO plan”, przy „GO implement”\s+i przed pierwszą fazą interfejsu'),
    ('proto-nowy-watek-nie-zmienia-zakresu', PROTO, r'nie zmienia zakresu po cichu'),
    ('feature-komentarze-po-akceptacji', FEATURE, r'Komentarze po akceptacji: przy „GO plan”, przy „GO implement” i przed pierwszą fazą interfejsu przeczytaj komentarze prototypu ponownie'),
)


def norm(text):
    return re.sub(r'\s+', ' ', text)


def missing(root):
    gone = []
    for key, rel, pattern in REQUIRED:
        path = Path(root) / rel
        text = norm(path.read_text(encoding='utf-8')) if path.is_file() else ''
        if not re.search(norm(pattern).replace(r'\s+', ' '), text):
            gone.append(key)
    return gone


class QuickSkillPhrases(unittest.TestCase):
    def test_required_phrases_present(self):
        self.assertEqual(missing(LIB), [])

    def test_each_phrase_can_fail(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(len(missing(tmp)), len(REQUIRED))


if __name__ == '__main__':
    unittest.main()
