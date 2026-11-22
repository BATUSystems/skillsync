"""Interface language of the prototype server and generator (CORE-1130-I18N, I18N-2).

The catalog of `proto.*` keys lives in `data/i18n/{pl,en}.proto.json`; the standalone server embeds it in a generated block
(`scripts/sync_proto_i18n.py`). Protocol words that programs read stay Polish and are not in the catalog.
"""
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from skillsync_workflow import prototype_server as server  # noqa: E402

CATALOG_DIR = ROOT / 'skillsync_workflow' / 'data' / 'i18n'
POLISH = re.compile('[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]')
PARAM = re.compile(r'\{(\w+)\}')


def fragment(lang):
    return json.loads((CATALOG_DIR / f'{lang}.proto.json').read_text(encoding='utf-8'))


def texts_of(value):
    return list(value.values()) if isinstance(value, dict) else [value]


def load_sync():
    spec = importlib.util.spec_from_file_location('sync_proto_i18n', ROOT / 'scripts' / 'sync_proto_i18n.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CatalogFilesTest(unittest.TestCase):
    def test_both_languages_have_the_same_keys_and_parameters(self):
        pl, en = fragment('pl'), fragment('en')
        self.assertEqual(sorted(pl), sorted(en))
        for key in pl:
            with self.subTest(key):
                self.assertEqual(isinstance(pl[key], dict), isinstance(en[key], dict), 'plural forms on one side only')
                if isinstance(pl[key], dict):
                    self.assertTrue(set(pl[key]) <= set(server._PLURAL_FORMS_ALLOWED) and set(en[key]) <= set(server._PLURAL_FORMS_ALLOWED))
                params = lambda value: {name for text in texts_of(value) for name in PARAM.findall(text)}   # noqa: E731
                self.assertEqual(params(pl[key]) - {'n'}, params(en[key]) - {'n'})

    def test_every_key_is_in_the_proto_namespace_and_the_english_texts_are_english(self):
        for lang in ('pl', 'en'):
            for key in fragment(lang):
                self.assertTrue(key.startswith('proto.'), key)
        for key, value in fragment('en').items():
            for text in texts_of(value):
                self.assertFalse(POLISH.search(text), f'{key}: {text}')

    def test_the_generated_block_of_the_server_is_current(self):
        self.assertEqual(load_sync().main(['--check']), 0, 'run scripts/sync_proto_i18n.py')
        self.assertEqual(server._PROTO_TEXTS, {'pl': fragment('pl'), 'en': fragment('en')})


class TranslationFunctionTest(unittest.TestCase):
    TEXTS = {'pl': {'proto.t.plain': 'Zwykły', 'proto.t.param': 'Konto {login} ({role})', 'proto.t.count': {'one': '{n} ekran', 'few': '{n} ekrany', 'many': '{n} ekranów'},
                    'proto.t.only-pl': 'Tylko polski'},
             'en': {'proto.t.plain': 'Plain', 'proto.t.param': 'Account {login} ({role})', 'proto.t.count': {'one': '{n} screen', 'other': '{n} screens'}}}

    def setUp(self):
        patcher = mock.patch.object(server, '_PROTO_TEXTS', self.TEXTS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(server.set_language, server.DEFAULT_LANGUAGE)

    def test_the_default_language_is_polish_and_a_wrong_value_gives_it(self):
        self.assertEqual(server.set_language(None), 'pl')
        self.assertEqual(server.set_language('de'), 'pl')
        self.assertEqual(server.set_language('en'), 'en')
        self.assertEqual(server.get_language(), 'en')

    def test_text_and_parameters(self):
        self.assertEqual(server.tr('proto.t.plain'), 'Zwykły')
        server.set_language('en')
        self.assertEqual(server.tr('proto.t.plain'), 'Plain')
        self.assertEqual(server.tr('proto.t.param', login='a@b.example', role='expert'), 'Account a@b.example (expert)')
        self.assertEqual(server.tr('proto.t.param', login='{role}', role='x'), 'Account {role} (x)', 'a substituted value is never read again')
        self.assertEqual(server.tr('proto.t.param', login='a'), 'Account a ({role})', 'an unknown placeholder stays')

    def test_plurals_follow_the_rules_of_the_language(self):
        server.set_language('pl')
        self.assertEqual([server.tr('proto.t.count', n=n) for n in (1, 2, 5, 12, 22, 25)],
                         ['1 ekran', '2 ekrany', '5 ekranów', '12 ekranów', '22 ekrany', '25 ekranów'])
        server.set_language('en')
        self.assertEqual([server.tr('proto.t.count', n=n) for n in (0, 1, 2)], ['0 screens', '1 screen', '2 screens'])
        self.assertEqual(server.plural_category('pl', True), 'other')
        self.assertEqual(server.plural_category('pl', -1), 'other')

    def test_a_text_missing_in_a_language_falls_back_to_polish_and_an_unknown_key_is_visible(self):
        server.set_language('en')
        self.assertEqual(server.tr('proto.t.only-pl'), 'Tylko polski')
        self.assertEqual(server.tr('proto.t.nowhere'), 'proto.t.nowhere')

    def test_client_texts_return_the_entries_of_a_prefix_in_the_language(self):
        server.set_language('en')
        found = server.client_texts('proto.t.')
        self.assertEqual(found['proto.t.plain'], 'Plain')
        self.assertEqual(found['proto.t.only-pl'], 'Tylko polski')
        self.assertEqual(server.client_texts('proto.t.', language='pl')['proto.t.plain'], 'Zwykły')
        self.assertEqual(server.client_texts('proto.zzz.'), {})


class LanguageOptionTest(unittest.TestCase):
    def tearDown(self):
        server.set_language(server.DEFAULT_LANGUAGE)

    def test_the_serve_command_takes_a_language_and_defaults_to_polish(self):
        parser = server.build_parser()
        self.assertEqual(parser.parse_args(['serve', '--root', 'r', '--users', 'u']).lang, 'pl')
        self.assertEqual(parser.parse_args(['serve', '--root', 'r', '--users', 'u', '--lang', 'en']).lang, 'en')
        with self.assertRaises(SystemExit):
            parser.parse_args(['serve', '--root', 'r', '--users', 'u', '--lang', 'de'])

    def test_the_config_keeps_a_supported_language_and_make_server_sets_it(self):
        self.assertEqual(server.ServerConfig('r', 'u', language='en').language, 'en')
        self.assertEqual(server.ServerConfig('r', 'u', language='xx').language, 'pl')
        self.assertEqual(server.ServerConfig('r', 'u').language, 'pl')


if __name__ == '__main__':
    unittest.main()
