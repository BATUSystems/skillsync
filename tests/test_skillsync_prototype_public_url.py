"""Adres publiczny serwera prototypów (TECH-140, D1): `prototype.public_url` i SKILLSYNC_PROTOTYPE_PUBLIC_URL
dla `question url`, pola `url` w `question add` i komunikatu `prototype serve`.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_prototype_public_url.py`.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype as pt  # noqa: E402
from tests.test_skillsync_prototype_question_cli import (EWA, QA, QuestionCase,  # noqa: E402,F401
                                                         setUpModule, tearDownModule)

ENV = 'SKILLSYNC_PROTOTYPE_PUBLIC_URL'
PUBLIC = 'https://proto.example.org:8443'


class SettingsTests(unittest.TestCase):
    def errors(self, value):
        return pt.resolve_prototype_settings({'public_url': value}, ROOT)['errors']

    def test_valid_values_are_normalised_without_a_trailing_slash(self):
        for value, expected in ((PUBLIC, PUBLIC), (PUBLIC + '/', PUBLIC), ('http://127.0.0.1:5130', 'http://127.0.0.1:5130')):
            settings = pt.resolve_prototype_settings({'public_url': value}, ROOT)
            self.assertEqual(settings['errors'], [], value)
            self.assertEqual(settings['public_url'], expected)

    def test_default_is_none(self):
        self.assertIsNone(pt.resolve_prototype_settings({}, ROOT)['public_url'])
        self.assertIsNone(pt.resolve_prototype_settings(None, ROOT)['public_url'])

    def test_invalid_values_name_the_key(self):
        for value in ('ftp://a.example', 'proto.example.org', 'https://u:p@a.example', 'https://a.example/?x=1',
                      'https://a.example/#f', 'https://', '', 5, True):
            errors = self.errors(value)
            self.assertTrue(errors and errors[0].startswith('prototype.public_url'), (value, errors))

    def test_key_is_known(self):
        settings = pt.resolve_prototype_settings({'public_url': PUBLIC}, ROOT)
        self.assertEqual([w for w in settings['warnings'] if 'unknown key' in w], [])


class QuestionUrlTests(QuestionCase):
    def setUp(self):
        super().setUp()
        self.users((EWA, ['expert'], None))

    def test_manifest_value_is_the_base_of_the_experts_url(self):
        self.set_prototype({'public_url': PUBLIC + '/'})
        report = self.q('url')
        self.assertEqual(report['url'], PUBLIC + '/_experts')
        self.assertFalse(report['running'], 'running to wynik sprawdzenia lokalnego serwera')

    def test_environment_wins_over_the_manifest(self):
        self.set_prototype({'public_url': PUBLIC})
        report = self.q('url', env={ENV: 'https://env.example.org'})
        self.assertEqual(report['url'], 'https://env.example.org/_experts')

    def test_invalid_environment_value_is_blocked(self):
        report = self.q('url', env={ENV: 'not a url'}, code=2)
        self.assertEqual(report['error'], 'invalid_public_url')
        self.assertIn(ENV, report['reason'])

    def test_without_a_setting_the_url_stays_local(self):
        self.assertTrue(self.q('url')['url'].startswith('http://127.0.0.1:'))

    def test_add_reports_the_public_address_of_the_question_page_even_without_a_local_server(self):
        self.set_prototype({'public_url': PUBLIC})
        report = self.add()
        self.assertEqual(report['url'], '%s/_experts/%s#%s' % (PUBLIC, report['slug'], QA))
        self.assertFalse(self.q('url')['running'])

    def test_add_without_a_setting_and_without_a_server_has_no_url(self):
        self.assertNotIn('url', self.add())


class ServeMessageTests(unittest.TestCase):
    def test_startup_line_names_the_public_address(self):
        self.assertEqual(pt.public_address_note('https://proto.example.org'), 'Adres publiczny: https://proto.example.org')
        self.assertIsNone(pt.public_address_note(None))


if __name__ == '__main__':
    unittest.main()
