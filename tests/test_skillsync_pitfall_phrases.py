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
]


@SKIP
class PitfallPhraseTests(unittest.TestCase):
    def text(self):
        return re.sub(r'\s+', ' ', (Path(LIB) / PITFALLS).read_text(encoding='utf-8'))

    def test_required_phrases_present(self):
        text = self.text()
        self.assertEqual([item[0] for item in REQUIRED if not re.search(item[1], text)], [])

    def test_each_pattern_fails_when_removed_from_real_text(self):
        text = self.text()
        for ident, pattern in REQUIRED:
            with self.subTest(phrase=ident):
                match = re.search(pattern, text)
                self.assertIsNotNone(match)
                self.assertIsNone(re.search(pattern, text.replace(match.group(0), '', 1)))


if __name__ == '__main__':
    unittest.main()
