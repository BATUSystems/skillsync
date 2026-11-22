"""Rekordy wiedzy domenowej repozytorium (docs/domain): frontmatter parsuje się jak w `skillsync doctor`, a indeks ma tylko dozwolone klucze `load`.

Doctor sprawdza frontmatter rekordów i indeks, ale żaden test nie uruchamiał tej kontroli na rekordach samego repozytorium,
więc rekord z niecytowanym „: ” w `source:` i wpis `load: on-demand` przechodziły przez pełny zestaw testów (do 1.12.0).
"""
import unittest
from pathlib import Path

import yaml

DOMAIN = Path(__file__).resolve().parents[1] / 'docs' / 'domain'
REQUIRED = ('title', 'tags', 'paths')
LOAD_VALUES = ('bootstrap',)   # tak jak w `quality.doctor`: `load` ma być `bootstrap` albo pominięty


def frontmatter(path):
    content = path.read_text(encoding='utf-8')
    if not content.startswith('---\n'):
        raise ValueError('brak frontmatteru YAML')
    # Ten sam podział co w skillsync_workflow/quality.py (doctor).
    return yaml.safe_load(content.split('---', 2)[1])


def records():
    return sorted(path for path in DOMAIN.glob('*.md') if path.name != 'README.md')


class DomainFrontmatterTests(unittest.TestCase):
    def test_every_record_has_parsable_frontmatter_with_required_keys(self):
        files = records()
        self.assertTrue(files, 'brak rekordów w docs/domain')
        problems = []
        for path in files:
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

    def test_index_and_record_files_match_and_load_is_bootstrap_or_omitted(self):
        index = yaml.safe_load((DOMAIN / 'index.yaml').read_text(encoding='utf-8'))
        entries = index['entries']
        indexed = [entry['path'] for entry in entries]
        self.assertEqual(len(indexed), len(set(indexed)), 'zdublowany wpis w indeksie wiedzy domenowej')
        files = {path.name for path in records()}
        self.assertEqual(sorted(set(indexed) - files), [], 'wpis indeksu bez pliku rekordu')
        self.assertEqual(sorted(files - set(indexed)), [], 'rekord bez wpisu w indeksie')
        wrong = [f"{entry['path']}: load: {entry['load']!r}" for entry in entries if 'load' in entry and entry['load'] not in LOAD_VALUES]
        self.assertEqual(wrong, [], 'load musi być `bootstrap` albo pominięty (doctor: "Domain record load must be bootstrap or omitted")')


if __name__ == '__main__':
    unittest.main()
