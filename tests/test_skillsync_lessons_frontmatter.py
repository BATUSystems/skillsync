"""Lekcje repozytorium (docs/lessons): frontmatter parsuje się jak w `skillsync doctor` i ma wymagane klucze.

Doctor sprawdza frontmatter lekcji, ale żaden test nie uruchamiał tej kontroli na lekcjach samego repozytorium,
więc lekcja z niecytowanym „: ” w wartości przechodziła przez pełny zestaw testów.
"""
import unittest
from pathlib import Path

import yaml

LESSONS = Path(__file__).resolve().parents[1] / 'docs' / 'lessons'
REQUIRED = ('title', 'tags', 'paths')


def frontmatter(path):
    content = path.read_text(encoding='utf-8')
    if not content.startswith('---\n'):
        raise ValueError('brak frontmatteru YAML')
    # Ten sam podział co w skillsync_workflow/quality.py (doctor).
    return yaml.safe_load(content.split('---', 2)[1])


class LessonFrontmatterTests(unittest.TestCase):
    def test_every_lesson_has_parsable_frontmatter_with_required_keys(self):
        lessons = sorted(LESSONS.glob('*.md'))
        self.assertTrue(lessons, 'brak lekcji w docs/lessons')
        problems = []
        for path in lessons:
            try:
                metadata = frontmatter(path)
            except (yaml.YAMLError, ValueError) as error:
                problems.append(f'{path.name}: {str(error).splitlines()[0]}')
                continue
            if not isinstance(metadata, dict):
                problems.append(f'{path.name}: frontmatter nie jest mapą')
                continue
            for key in REQUIRED:
                if key not in metadata:
                    problems.append(f'{path.name}: brak klucza {key}')
            if not isinstance(metadata.get('title'), str) or not metadata.get('title'):
                problems.append(f'{path.name}: title nie jest niepustym tekstem')
            for key in ('tags', 'paths'):
                value = metadata.get(key)
                if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
                    problems.append(f'{path.name}: {key} nie jest listą niepustych tekstów')
        self.assertEqual(problems, [])

    def test_index_and_lesson_files_match(self):
        index = yaml.safe_load((LESSONS / 'index.yaml').read_text(encoding='utf-8'))
        indexed = [entry['path'] for entry in index['lessons']]
        self.assertEqual(len(indexed), len(set(indexed)), 'zdublowany wpis w indeksie lekcji')
        files = {path.name for path in LESSONS.glob('*.md') if path.name != 'README.md'}
        self.assertEqual(sorted(set(indexed) - files), [], 'wpis indeksu bez pliku lekcji')
        self.assertEqual(sorted(files - set(indexed)), [], 'lekcja bez wpisu w indeksie')


if __name__ == '__main__':
    unittest.main()
