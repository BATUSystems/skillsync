"""CHANGELOG.md: każda sekcja „Breaking” wydania ma treść (lista zmian albo „None.”), TECH-140 D6.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_changelog_breaking.py`.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = re.compile(r'^## \[(\d+\.\d+\.\d+)\]', re.M)


def sections(text):
    marks = list(RELEASE.finditer(text))
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        yield mark.group(1), text[mark.end():end]


class ChangelogBreakingTests(unittest.TestCase):
    def test_every_release_has_a_non_empty_breaking_section(self):
        text = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
        problems = []
        for version, body in sections(text):
            match = re.search(r'^### Breaking\s*\n(.*?)(?=^### |\Z)', body, re.M | re.S)
            if match is None:
                problems.append(f'{version}: brak sekcji Breaking')
            elif not match.group(1).strip():
                problems.append(f'{version}: pusta sekcja Breaking (napisz „None.”)')
        self.assertEqual(problems, [])


if __name__ == '__main__':
    unittest.main()
