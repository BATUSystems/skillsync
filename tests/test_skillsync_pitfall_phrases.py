"""Strażnik fraz skilla agent-runtime-pitfalls (DEBT-142 D54): pułapka okna chrome --headless=new (minimum 500 px) i obejście.
Biblioteka z SKILLSYNC_TEST_LIBRARY. Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_pitfall_phrases.py`.
"""
import os
import re
import unittest
from pathlib import Path

LIB = os.environ.get('SKILLSYNC_TEST_LIBRARY')
SKIP = unittest.skipUnless(LIB, 'set SKILLSYNC_TEST_LIBRARY to a library with the skills')
PITFALLS = 'skills/agent-runtime-pitfalls/SKILL.md'

REQUIRED = [
    ('chrome-minimalne-okno', r'`chrome --headless=new` ma minimalne okno 500 px'),
    ('chrome-headless-shell', r'Do 360 px używaj `chrome-headless-shell`'),
    ('chrome-skad-wziac', r'playwright albo puppeteer'),
    ('chrome-objaw', r'`window\.innerWidth` zostaje 500'),
    ('czas-z-date', r'Godzin nie wpisuj z pamięci ani z szacunku'),
    ('czas-linia-date', r'Raport do dyspozytora zaczyna się linią `date: <dosłowny wynik polecenia date>`'),
]
# the report rule lives in the multi-session protocol too (the card report template)
PROTOCOL = 'skills/feature/references/multi-session.md'
REQUIRED_PROTOCOL = [
    ('protokol-czas-z-date', r'Czas w raporcie pochodzi z `date`'),
    ('protokol-pierwsza-linia', r'Pierwsza linia każdej wiadomości karty do dyspozytora, która niesie czas lub wynik'),
    ('protokol-wzorzec', r'date: <wynik polecenia date> STATUS <paczka> <akt> <czeka-na>'),
    ('protokol-odrzucenie', r'REPORT REJECTED powód=brak linii date'),
]


@SKIP
class PitfallPhraseTests(unittest.TestCase):
    def text(self):
        return re.sub(r'\s+', ' ', (Path(LIB) / PITFALLS).read_text(encoding='utf-8'))

    def test_required_phrases_present(self):
        text = self.text()
        self.assertEqual([item[0] for item in REQUIRED if not re.search(item[1], text)], [])

    def test_report_template_phrases_present_in_protocol(self):
        text = re.sub(r'\s+', ' ', (Path(LIB) / PROTOCOL).read_text(encoding='utf-8'))
        self.assertEqual([item[0] for item in REQUIRED_PROTOCOL if not re.search(item[1], text)], [])
        for ident, pattern in REQUIRED_PROTOCOL:
            with self.subTest(phrase=ident):
                match = re.search(pattern, text)
                self.assertIsNone(re.search(pattern, text.replace(match.group(0), '', 1)))

    def test_each_pattern_fails_when_removed_from_real_text(self):
        text = self.text()
        for ident, pattern in REQUIRED:
            with self.subTest(phrase=ident):
                match = re.search(pattern, text)
                self.assertIsNotNone(match)
                self.assertIsNone(re.search(pattern, text.replace(match.group(0), '', 1)))


if __name__ == '__main__':
    unittest.main()
