"""180-P: `prototype.dark_theme: false` (a light-only prototype) and `sync --check` failing on missing required tokens."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow import prototype  # noqa: E402
from skillsync_workflow import prototype_server as ps  # noqa: E402
from tests.test_skillsync_prototype import PrototypeFixture  # noqa: E402
from tests.test_skillsync_prototype_cli import SpriteFixture  # noqa: E402

PAGE = '<!DOCTYPE html>\n<html lang="pl" data-prototype-id="demo">\n<head>\n<title>T</title>\n</head>\n<body></body>\n</html>\n'


def html_tag(text):
    start = text.index('<html')
    return text[start:text.index('>', start) + 1]


class SettingsTests(unittest.TestCase):
    def test_default_is_true_and_a_bool_is_taken(self):
        self.assertTrue(prototype.resolve_prototype_settings({}, Path('.'))['dark_theme'])
        self.assertFalse(prototype.resolve_prototype_settings({'dark_theme': False}, Path('.'))['dark_theme'])
        self.assertTrue(prototype.resolve_prototype_settings({'dark_theme': True}, Path('.'))['dark_theme'])

    def test_other_types_are_errors_not_truthy_values(self):
        for value in ('false', 'no', 0, 1, None, [], {}):
            with self.subTest(value=value):
                settings = prototype.resolve_prototype_settings({'dark_theme': value}, Path('.'))
                self.assertTrue(any('prototype.dark_theme' in error for error in settings['errors']), settings)
                self.assertTrue(settings['dark_theme'], 'an invalid value keeps the default')

    def test_the_key_is_known(self):
        settings = prototype.resolve_prototype_settings({'dark_theme': False}, Path('.'))
        self.assertEqual(settings['warnings'], [])


class EnsureLightOnlyTests(unittest.TestCase):
    def test_adds_both_attributes_to_the_html_tag_only(self):
        html, status = prototype.ensure_light_only(PAGE, True)
        self.assertEqual(status, 'added')
        self.assertEqual(html_tag(html), '<html lang="pl" data-prototype-id="demo" data-theme="light" data-proto-dark="off">')
        self.assertEqual(html.replace(' data-theme="light" data-proto-dark="off"', ''), PAGE, 'nothing else changes')

    def test_idempotent(self):
        once, _ = prototype.ensure_light_only(PAGE, True)
        twice, status = prototype.ensure_light_only(once, True)
        self.assertEqual((twice, status), (once, 'unchanged'))

    def test_a_hand_written_dark_theme_is_replaced_when_the_option_is_off(self):
        html, _ = prototype.ensure_light_only(PAGE.replace('<html ', '<html data-theme="dark" '), True)
        self.assertEqual(html.count('data-theme'), 1)
        self.assertIn('data-theme="light"', html)

    def test_removal_takes_only_our_marks(self):
        marked, _ = prototype.ensure_light_only(PAGE, True)
        back, status = prototype.ensure_light_only(marked, False)
        self.assertEqual((back, status), (PAGE, 'removed'))

    def test_a_hand_written_theme_stays_when_there_is_no_mark(self):
        own = PAGE.replace('<html ', '<html data-theme="dark" ')
        self.assertEqual(prototype.ensure_light_only(own, False), (own, 'unchanged'))

    def test_without_an_html_tag_nothing_changes(self):
        self.assertEqual(prototype.ensure_light_only('<p>x</p>', True), ('<p>x</p>', 'no_html'))

    def test_a_tag_name_that_only_starts_like_html_is_not_the_tag(self):
        text = '<htmlx a="1"><p>'
        self.assertEqual(prototype.ensure_light_only(text, True), (text, 'no_html'))

    def test_crlf_and_quoting_variants(self):
        html, _ = prototype.ensure_light_only(PAGE.replace('\n', '\r\n').replace('lang="pl"', "lang='pl'"), True)
        self.assertIn("lang='pl' data-prototype-id=\"demo\" data-theme=\"light\" data-proto-dark=\"off\">", html)
        self.assertEqual(html.count('\r\n'), PAGE.count('\n'))


class InitAndMigrateTests(SpriteFixture):
    def test_init_writes_the_marks_only_when_the_option_is_off(self):
        self.init('plain')
        self.assertNotIn('data-proto-dark', self.read('plain', 'index.html'))
        self.set_prototype({'dark_theme': False})
        self.init('light')
        tag = html_tag(self.read('light', 'index.html'))
        self.assertIn('data-theme="light"', tag)
        self.assertIn('data-proto-dark="off"', tag)

    def test_migrate_adds_and_removes_the_marks_and_accepted_screens_stay_accepted(self):
        directory = self.prepare()
        self.migrate()
        before = ps.screen_hashes(str(directory))['screens']
        accepted = self.proto('list')
        self.set_prototype({'dark_theme': False})
        report = self.migrate()
        self.assertIn('light_only', [step['action'] for step in report['steps']])
        self.assertIn('data-proto-dark="off"', html_tag((directory / 'index.html').read_text(encoding='utf-8')))
        self.assertEqual(ps.screen_hashes(str(directory))['screens'], before, 'the screen hashes do not change')
        self.assertEqual(report['acceptance']['lost'], 0, report['acceptance'])
        self.assertEqual(self.proto('list')['prototypes'][0]['acceptance'], accepted['prototypes'][0]['acceptance'])
        self.set_prototype({'dark_theme': True})
        report = self.migrate()
        self.assertNotIn('data-proto-dark', (directory / 'index.html').read_text(encoding='utf-8'))
        self.assertEqual(ps.screen_hashes(str(directory))['screens'], before)
        self.assertEqual(report['acceptance']['lost'], 0)

    def test_migrate_twice_is_a_no_op_for_the_marks(self):
        directory = self.prepare()
        self.set_prototype({'dark_theme': False})
        self.migrate()
        once = (directory / 'index.html').read_text(encoding='utf-8')
        report = self.migrate()
        self.assertEqual((directory / 'index.html').read_text(encoding='utf-8'), once)
        self.assertNotIn('light_only', [step['action'] for step in report['steps']])

    def test_dry_run_writes_nothing(self):
        directory = self.prepare()
        self.set_prototype({'dark_theme': False})
        before = (directory / 'index.html').read_text(encoding='utf-8')
        self.migrate('--dry-run')
        self.assertEqual((directory / 'index.html').read_text(encoding='utf-8'), before)

    def test_sync_check_points_at_migrate_for_a_prototype_without_the_marks(self):
        self.init('demo')
        self.set_prototype({'dark_theme': False})
        report = self.proto('sync', '--check')
        self.assertEqual(report['status'], 'PASS')
        notes = [item for item in report.get('warnings', []) if 'dark_theme' in item]
        self.assertTrue(any('data-proto-dark="off"' in item for item in notes), notes)
        self.assertTrue(any('engine copy' in item for item in notes), notes)

    def test_sync_check_is_quiet_after_migrate(self):
        self.prepare()
        engine = self.assets / 'repo' / 'prototype.js'   # the fixture engine is a stub: give it the mark the real one has
        engine.write_text(engine.read_text(encoding='utf-8') + '\n// data-proto-dark\n', encoding='utf-8')
        self.set_prototype({'dark_theme': False})
        self.migrate()
        report = self.proto('sync', '--check')
        self.assertEqual([item for item in report.get('warnings', []) if 'dark_theme' in item], [])


class WhatEntersTheScreenHashTests(SpriteFixture):
    """The claim of `docs/prototypes.md` about the engine files (checked on the code, 180-P): the style sheets of the prototype change the
    hash of every screen, the engine files `prototype.js` and `prototype.css` do not."""

    def hashes(self, directory):
        return ps.screen_hashes(str(directory))['screens']

    def test_prototype_js_and_prototype_css_do_not_change_a_screen_hash_but_components_and_tokens_do(self):
        directory = self.prepare()
        base = self.hashes(directory)
        for name, changes in (('prototype.js', False), ('prototype.css', False), ('components.css', True), ('screens.css', True),
                              ('tokens.css', True), ('theme.css', True), ('project.css', True)):
            with self.subTest(name=name):
                path = directory / name
                original = path.read_text(encoding='utf-8')
                path.write_text(original + '\n/* changed */\n', encoding='utf-8')
                self.assertEqual(self.hashes(directory) != base, changes)
                path.write_text(original, encoding='utf-8')

    def test_the_attributes_of_the_html_tag_do_not_change_a_screen_hash(self):
        directory = self.prepare()
        base = self.hashes(directory)
        index = directory / 'index.html'
        text = index.read_text(encoding='utf-8')
        index.write_text(prototype.ensure_light_only(text, True)[0], encoding='utf-8')
        self.assertEqual(self.hashes(directory), base)


class MissingTokensTests(PrototypeFixture):
    TOKEN = '--zz-required-token'

    def need_a_token(self):
        required = self.assets / 'theme' / 'required-tokens.txt'
        required.write_text(required.read_text(encoding='utf-8').rstrip('\n') + '\n' + self.TOKEN + '\n', encoding='utf-8')

    def test_check_fails_with_the_list_when_a_required_token_is_missing(self):
        self.init('demo')
        self.need_a_token()
        before = {path.name: path.read_bytes() for path in self.proto_dir().iterdir() if path.is_file()}
        report = self.proto('sync', '--check', code=1)
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(report['missing'], [self.TOKEN])
        self.assertEqual(report.get('stale', []), [])
        self.assertTrue(any('Missing required tokens' in item for item in report['warnings']))
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.proto_dir().iterdir() if path.is_file()})

    def test_plain_sync_only_warns(self):
        self.init('demo')
        self.need_a_token()
        report = self.proto('sync')
        self.assertEqual(report['status'], 'PASS')
        self.assertNotIn('missing', report)
        self.assertTrue(any(self.TOKEN in item for item in report['warnings']))

    def test_check_passes_when_the_project_theme_defines_the_token(self):
        self.init('demo')
        self.need_a_token()
        (self.root / 'src').mkdir()
        (self.root / 'src' / 'theme.css').write_text(':root { %s: 1px; }\n' % self.TOKEN, encoding='utf-8')
        self.set_prototype({'theme': {'tokens_from': 'src/theme.css'}})
        self.proto('sync')
        report = self.proto('sync', '--check')
        self.assertEqual(report['status'], 'PASS')
        self.assertNotIn('missing', report)

    def test_a_complete_default_theme_still_passes(self):
        self.init('demo')
        report = self.proto('sync', '--check')
        self.assertEqual((report['status'], report.get('missing')), ('PASS', None))

    def test_blocked_keeps_precedence_over_fail(self):
        self.init('demo')
        self.need_a_token()
        (self.proto_dir() / 'prototype.json').write_text('{ not json', encoding='utf-8')
        report = self.proto('sync', '--check', code=2)
        self.assertEqual(report['status'], 'BLOCKED')


if __name__ == '__main__':
    unittest.main()
