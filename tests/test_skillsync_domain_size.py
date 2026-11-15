"""Rozmiar rekordów domenowych `load: bootstrap` (DEBT-142 D26): router zwraca je w każdym wyniku, więc procedura
(`workflow/references/knowledge.md`) daje limit 12 KB UTF-8 (cel 8 KB); limit nie był pilnowany automatycznie.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_domain_size.py`.
"""
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = ROOT / 'docs' / 'domain'
LIMIT = 12 * 1024


def bootstrap_records():
    index = yaml.safe_load((DOMAIN / 'index.yaml').read_text(encoding='utf-8'))
    entries = index['entries']
    return [entry['path'] for entry in entries if isinstance(entry, dict) and entry.get('load') == 'bootstrap']


class BootstrapRecordSizeTests(unittest.TestCase):
    def test_there_is_a_bootstrap_record_to_guard(self):
        self.assertIn('glossary.md', bootstrap_records())

    def test_every_bootstrap_record_fits_the_procedure_limit(self):
        too_big = {rel: (DOMAIN / rel).stat().st_size for rel in bootstrap_records()
                   if (DOMAIN / rel).stat().st_size > LIMIT}
        self.assertEqual(too_big, {}, f'przenieś szczegóły do rekordów tematycznych (limit {LIMIT} B)')

    def test_the_glossary_keeps_a_margin_below_the_limit(self):
        """A margin of 5%: the next definitions must not push it over the limit at once."""
        self.assertLessEqual((DOMAIN / 'glossary.md').stat().st_size, int(LIMIT * 0.95))


if __name__ == '__main__':
    unittest.main()
