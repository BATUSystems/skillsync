"""Interface language of the prototype server and generator (CORE-1130-I18N, I18N-2).

The catalog of `proto.*` keys lives in `data/i18n/{pl,en}.proto.json`; the standalone server embeds it in a generated block
(`scripts/sync_proto_i18n.py`). Protocol words that programs read stay Polish and are not in the catalog.
"""
import hashlib
import importlib.util
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from tmp_paths import child  # noqa: E402
from skillsync_workflow import prototype_server as server  # noqa: E402
from tests import test_skillsync_prototype_experts_page as E  # noqa: E402
from tests.test_skillsync_prototype_server import setUpModule, tearDownModule  # noqa: E402,F401  (strażnik katalogu)

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


class PagesTest(unittest.TestCase):
    """The login page and the experts page in both languages (the experts script is pinned by a hash in the CSP, so the policy follows the language)."""

    def tearDown(self):
        server.set_language(server.DEFAULT_LANGUAGE)

    def test_english_pages_have_no_polish_text_and_the_right_lang(self):
        for name in ('render_login_page', 'render_experts_page', 'render_experts_script'):
            page = getattr(server, name)('en')
            with self.subTest(name):
                self.assertIsNone(POLISH.search(page), POLISH.findall(page))
        self.assertIn('<html lang="en"', server.render_login_page('en'))
        self.assertIn('<html lang="en"', server.render_experts_page('en'))
        self.assertIn('<html lang="pl"', server.render_login_page('pl'))
        self.assertIn('<html lang="pl"', server.render_experts_page('pl'))

    def test_the_pages_follow_the_language_of_the_process_when_none_is_given(self):
        self.assertEqual(server.LOGIN_PAGE.encode('utf-8'), server.render_login_page('pl').encode('utf-8'))
        server.set_language('en')
        self.assertEqual(server.LOGIN_PAGE.encode('utf-8'), server.render_login_page('en').encode('utf-8'))
        self.assertEqual(server.EXPERTS_PAGE.encode('utf-8'), server.render_experts_page('en').encode('utf-8'))

    def test_the_policy_allows_exactly_the_script_of_its_language(self):
        for language in ('pl', 'en'):
            policy = server.experts_csp(language)
            script = server.render_experts_script(language)
            self.assertIn(server._csp_hash(script), policy)
            self.assertIn(script, server.render_experts_page(language))
        self.assertNotEqual(server.experts_csp('pl'), server.experts_csp('en'))
        self.assertEqual(server.experts_csp(None), server.experts_csp('pl'))
        server.set_language('en')
        self.assertEqual(server.experts_csp(), server.experts_csp('en'))

    def test_the_status_line_of_the_server_stays_the_one_programs_read(self):
        source = (ROOT / 'skillsync_workflow' / 'prototype_server.py').read_text(encoding='utf-8')
        prototype = (ROOT / 'skillsync_workflow' / 'prototype.py').read_text(encoding='utf-8')
        for text in (source, prototype):
            self.assertIn("'Serwer prototypów: %s (Ctrl+C kończy)\\n'", text)


class GoldenPolishTest(unittest.TestCase):
    """Polish output is what it was before the catalog (sha256 taken from `prototype_server.py` @ 48fa505): login page and the three warnings.

    The experts script is not pinned this way on purpose: it was changed in one respect (a single-pass `fmt` for several placeholders), the
    visible texts are the same; its hash and the policy are checked against each other (`PagesTest`, `ExpertsHttpTest`)."""
    OLD_LINE = "show('login-error', 'Zbyt wiele nieudanych prób. Spróbuj ponownie za ' + (data.retryAfter || 60) + ' s.');"
    NEW_LINE = "show('login-error', 'Zbyt wiele nieudanych prób. Spróbuj ponownie za {seconds} s.'.split('{seconds}').join(String(data.retryAfter || 60)));"
    # CORE-2000-UNIFIED U-6, added on purpose: the answers for a server that takes no password of the account; each fragment is in the page once
    ELSEWHERE_LOGIN = 'Konta z rolą approver logują się na stronie statusu (skillsync dashboard serve), nie tutaj.'
    ELSEWHERE_PASSWORD = 'Hasło tego konta zmienia się na stronie statusu (skillsync dashboard serve), nie tutaj.'
    ADDED = (
        ("""    if (data && data.user && data.user.mustChange) {
      if (data.server && data.server.passwordChange === false) { show('login-error', '%s'); return; }
      state.csrf = data.csrf; stepTwo(false);
    }""" % ELSEWHERE_PASSWORD, """    if (data && data.user && data.user.mustChange) { state.csrf = data.csrf; stepTwo(false); }"""),
        ("""        } else if (data.error === 'approver_login_elsewhere' || data.error === 'login_on_page') {
          show('login-error', '%s');
""" % ELSEWHERE_LOGIN, ''),
        ("""        if (data.error === 'approver_login_elsewhere' || data.error === 'login_on_page') {
          state.current = null; changeForm.reset(); changeForm.hidden = true; loginForm.hidden = false;
          show('login-error', '%s');
        } else if (data.error === 'invalid_current')""" % ELSEWHERE_PASSWORD, """        if (data.error === 'invalid_current')"""),
    )

    def login_page_before(self):
        page = server.render_login_page('pl')
        for added, before in self.ADDED:
            self.assertEqual(page.count(added), 1, added)
            page = page.replace(added, before)
        return page.replace(self.NEW_LINE, self.OLD_LINE)

    GOLDEN = {
        # the one line that differs is the formatting of the text from the catalog; the visible text is the old one
        'login page': ('48995b6c3ca2c973a3a9fde318ca8e35e268069a1c9cbd74ff8746ec1751e770', None),
        'PUBLIC_READ_WARNING': ('c2a0f1784e053023f2b08365de85d119d2d1535b55eb7dd0e7c067b10ef78855', lambda: server.PUBLIC_READ_WARNING),
        'LEGACY_QUESTIONS_WARNING': ('4568fddcfc5f5f7dacfce5c582e2f9dea7d291fa13a51a8719c9b45bc31333ae', lambda: server.LEGACY_QUESTIONS_WARNING),
        'EXPERT_PUBLIC_WARNING': ('6a8e51224caf7d3a57f3e31b050bb71edcaaf7d6be6ef8085c4a2c9f237cc048', lambda: server.EXPERT_PUBLIC_WARNING),
    }

    def test_polish_texts_are_unchanged(self):
        server.set_language('pl')
        for name, (digest, produce) in self.GOLDEN.items():
            with self.subTest(name):
                text = self.login_page_before() if produce is None else produce()
                self.assertEqual(hashlib.sha256(text.encode('utf-8')).hexdigest(), digest)


class UsedKeysTest(unittest.TestCase):
    """Every key that the code asks for exists in both languages and every key of the catalog is used (`tr` returns the bare key when it is missing)."""
    CALL = re.compile(r"['\"](proto\.[A-Za-z0-9.]+)['\"]")   # every string literal that looks like a key
    TEMPLATE = re.compile(r"@(?:js)?\{([A-Za-z0-9.]+)\}")

    def sources(self):
        server_text = (ROOT / 'skillsync_workflow' / 'prototype_server.py').read_text(encoding='utf-8')
        server_text = re.sub(r'# BEGIN GENERATED proto-i18n.*?# END GENERATED proto-i18n', '', server_text, flags=re.S)
        generator_text = (ROOT / 'skillsync_workflow' / 'prototype.py').read_text(encoding='utf-8')
        return server_text, generator_text

    def used(self):
        server_text, generator_text = self.sources()
        keys = {key for key in set(self.CALL.findall(server_text)) | set(self.CALL.findall(generator_text)) if not key.endswith('.')}   # a trailing dot is a prefix
        for template, prefix in (('_LOGIN_TEMPLATE', 'proto.page.login.'), ('_EXPERTS_SCRIPT_TEMPLATE', 'proto.page.experts.'),
                                 ('_EXPERTS_PAGE_TEMPLATE', 'proto.page.experts.')):
            body = getattr(server, template)
            keys |= {prefix + name for name in self.TEMPLATE.findall(body)}
        return keys

    DYNAMIC = re.compile(r'^proto\.(gen\.gallery\.|gen\.agent\.|srv\.warn\.|srv\.start\.)')   # keys picked by a variable part, listed by prefix

    def test_every_used_key_is_in_both_languages(self):
        pl, en = fragment('pl'), fragment('en')
        missing = sorted(key for key in self.used() if key not in pl or key not in en)
        self.assertEqual(missing, [])

    def test_every_key_of_the_catalog_is_used(self):
        unused = sorted(key for key in fragment('pl') if key not in self.used() and not self.DYNAMIC.match(key))
        self.assertEqual(unused, [])

    def test_a_catalog_file_has_no_duplicate_keys(self):
        def no_duplicates(pairs):
            keys = [key for key, _ in pairs]
            self.assertEqual(len(keys), len(set(keys)), 'duplicate key')
            return dict(pairs)
        for lang in ('pl', 'en'):
            json.loads((CATALOG_DIR / f'{lang}.proto.json').read_text(encoding='utf-8'), object_pairs_hook=no_duplicates)

    def test_no_page_shows_a_bare_key(self):
        for language in ('pl', 'en'):
            for page in (server.render_login_page(language), server.render_experts_page(language)):
                self.assertNotIn('proto.page.', page)


class ExpertsHttpTest(E.ExpertsPageBase):
    """A server started with `language='en'` answers in English and its policy fits the script it serves."""

    def setUp(self):
        super().setUp()
        self.addCleanup(server.set_language, 'pl')
        self.start_server(language='en')

    def test_login_page_is_english(self):
        response = self.anon.get('/_login')
        self.assertEqual(response.status, 200)
        self.assertIn('<html lang="en"', response.text)
        self.assertIsNone(POLISH.search(response.text))

    def test_experts_page_is_english_and_the_policy_is_the_hash_of_its_script(self):
        response = self.page()
        self.assertEqual(response.status, 200)
        self.assertIn('<html lang="en"', response.text)
        self.assertIsNone(POLISH.search(response.text))
        scripts, _ = E.split_page(response.text)
        policy = response.header('Content-Security-Policy')
        self.assertEqual(E.directive(policy, 'script-src'), [E.sha_token(scripts[0][1])])

    def test_the_language_of_another_server_in_the_process_does_not_change_this_one(self):
        before = self.page().text
        server.set_language('pl')   # what another `make_server` in the same process does
        self.assertEqual(self.page().text, before)


class SyncScriptTest(unittest.TestCase):
    def setUp(self):
        self.sync = load_sync()
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.temp), True)
        self.catalog = self.temp / 'i18n'
        self.catalog.mkdir()
        for lang in ('pl', 'en'):
            (self.catalog / f'{lang}.proto.json').write_text('{"proto.a": "x"}\n', encoding='utf-8')
        self.server_copy = self.temp / 'prototype_server.py'
        self.server_copy.write_text(self.sync.block({'pl': {'proto.a': 'x'}, 'en': {'proto.a': 'x'}}), encoding='utf-8')
        for name, value in (('CATALOG_DIR', self.catalog), ('SERVER', self.server_copy)):
            patcher = mock.patch.object(self.sync, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(self.sync._Lock, 'path', self.catalog / '.proto.lock')
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, name, data):
        path = child(self.temp, name)
        path.write_text(json.dumps(data), encoding='utf-8')
        return str(path)

    def test_check_fails_when_the_block_is_stale_and_the_script_repairs_it(self):
        (self.catalog / 'pl.proto.json').write_text('{"proto.a": "y"}\n', encoding='utf-8')
        self.assertEqual(self.sync.main(['--check']), 1)
        self.assertEqual(self.sync.main([]), 0)
        self.assertEqual(self.sync.main(['--check']), 0)

    def test_merge_adds_keys_to_both_languages_and_refuses_a_conflict_or_unequal_keys(self):
        self.assertEqual(self.sync.main(['--merge', self.write('k.json', {'pl': {'proto.b': 'b'}, 'en': {'proto.b': 'B'}})]), 0)
        self.assertEqual(json.loads((self.catalog / 'en.proto.json').read_text(encoding='utf-8'))['proto.b'], 'B')
        self.assertEqual(self.sync.main(['--check']), 0)
        self.assertEqual(self.sync.main(['--merge', self.write('c.json', {'pl': {'proto.a': 'other'}, 'en': {'proto.a': 'x'}})]), 2)
        self.assertEqual(self.sync.main(['--merge', self.write('u.json', {'pl': {'proto.c': 'c'}, 'en': {}})]), 2)
        self.assertNotIn('proto.c', json.loads((self.catalog / 'pl.proto.json').read_text(encoding='utf-8')))

    def test_three_single_quotes_cannot_be_embedded(self):
        self.assertEqual(self.sync.main(['--merge', self.write('q.json', {'pl': {'proto.q': "a'''b"}, 'en': {'proto.q': 'ab'}})]), 2)

    def test_a_stale_lock_is_taken_over_and_removed(self):
        lock = self.catalog / '.proto.lock'
        lock.write_text('', encoding='utf-8')
        old = lock.stat().st_mtime - 1000
        import os
        os.utime(str(lock), (old, old))
        self.assertEqual(self.sync.main(['--merge', self.write('k.json', {'pl': {'proto.b': 'b'}, 'en': {'proto.b': 'B'}})]), 0)
        self.assertFalse(lock.exists())


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
