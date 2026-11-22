"""I18N-2: human-visible texts of the prototype generator follow the project language (manifest key `language`, `pl` by default).
The Polish output stays as it was; the English one has no Polish characters; the agent notices and the templates follow the language."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow import prototype as pt  # noqa: E402

POLISH = re.compile('[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]')
ASSETS = Path(__file__).resolve().parents[1] / 'library' / 'skills' / 'prototype' / 'assets'
ENTRY = {'slug': 'demo', 'title': 'Demo', 'requirements': 'docs/req.md', 'created': '2026-11-05', 'screens': 3,
         'comments': 4, 'acceptance': {'accepted': 1, 'screens': 3}, 'questions': True, 'writing': {'since': 1}}


class GalleryLanguage(unittest.TestCase):
    def test_default_gallery_is_polish_and_unchanged(self):
        html = pt.render_gallery([ENTRY])
        self.assertEqual(html, pt.render_gallery([ENTRY], language='pl'))
        for text in ('<html lang="pl">', '<h1>Prototypy</h1>', 'Liczba prototypów: 1. Otwórz prototyp, aby przeglądać ekrany i komentarze.',
                     '<dt>Wymagania</dt>', '<dt>Otwarte komentarze</dt>', '<dt>Akceptacja</dt>', 'W trakcie zapisu: agent jeszcze pisze ekrany',
                     '<a href="/_experts/demo">Pytania</a>'):
            self.assertIn(text, html)

    def test_english_gallery_has_lang_en_and_no_polish_characters(self):
        for demo in (False, True):
            for entries in ([ENTRY], []):
                html = pt.render_gallery(entries, demo=demo, language='en')
                self.assertIn('<html lang="en">', html)
                self.assertIsNone(POLISH.search(html), (demo, entries))
                self.assertNotIn('Liczba', html)
        self.assertIn('No prototypes.', pt.render_gallery([], language='en'))
        self.assertIn('<h1>Prototypes</h1>', pt.render_gallery([ENTRY], language='en'))

    def test_the_count_follows_the_plural_of_the_language(self):
        one = pt.render_gallery([ENTRY], language='en')
        two = pt.render_gallery([ENTRY, dict(ENTRY, slug='other')], language='en')
        self.assertIn('1 prototype. Open', one)
        self.assertIn('2 prototypes. Open', two)
        self.assertIn('Liczba prototypów: 2.', pt.render_gallery([ENTRY, dict(ENTRY, slug='other')]))
        self.assertIn('2 prototypes. Open a prototype to browse its screens.', pt.render_gallery([ENTRY, ENTRY], demo=True, language='en'))


class DemoAndCliTexts(unittest.TestCase):
    def test_public_address_lines(self):
        env = {pt.PUBLIC_URL_ENV: 'https://example.test'}
        settings = {'public_url': None}
        self.assertEqual(pt.public_address_lines(settings, env), ['Adres publiczny: https://example.test'])
        self.assertEqual(pt.public_address_lines(settings, env, language='en'), ['Public address: https://example.test'])

    def test_demo_lines_in_both_languages(self):
        class Instance:
            server_address = ('127.0.0.1', 4000)
        pl = pt.demo_lines(Instance(), '127.0.0.1')
        en = pt.demo_lines(Instance(), '127.0.0.1', 'en')
        self.assertTrue(pl[0].startswith('POKAZ tylko do odczytu: prototypy są jawne'))
        self.assertTrue(en[0].startswith('READ-ONLY SHOW'))
        self.assertIsNone(POLISH.search('\n'.join(en)))
        with_lan = pt.demo_lines(Instance(), '0.0.0.0', 'en')
        self.assertIsNone(POLISH.search('\n'.join(with_lan)))
        self.assertTrue(with_lan[-1].startswith('This machine only'))

    def test_cli_messages(self):
        self.assertEqual(pt._text('pl', 'proto.cli.promptPasswordOf', login='ala'), 'Hasło ala: ')
        self.assertEqual(pt._text('en', 'proto.cli.promptPasswordOf', login='ala'), 'Password for ala: ')
        for key in ('proto.cli.warning', 'proto.cli.notInGit', 'proto.cli.promptPassword', 'proto.cli.promptRepeat', 'proto.gen.cli.demoIrrelevant',
                    'proto.gen.cli.changePassword', 'proto.gen.cli.lostClasses'):
            self.assertIsNone(POLISH.search(pt._text('en', key, text='x', path='p', name='n')), key)


class SpecRows(unittest.TestCase):
    """The rows that `question add`/`answer`/`withdraw` write into the spec use the tokens of the project language (`protocol_tokens`);
    Polish stays what it was, English rows are read back by the same parsers."""
    QUESTION = {'id': 'qn-abc123', 'text': 'Which format?', 'blocking': False, 'assumed': 'PDF', 'origin': {'q': 'Q3'},
                'options': [{'id': 'o1', 'label': 'PDF'}], 'answer': {'optionId': 'o1', 'by': 'Ewa Kowalska', 'at': 1795000000000}}

    def test_polish_rows_are_the_old_ones(self):
        self.assertEqual(pt.question_spec_line(self.QUESTION),
                         '- Q3 [ZAŁOŻENIE]: Which format? (ekspert: qn-abc123) — przyjęte: PDF')
        self.assertTrue(pt.question_answer_line(self.QUESTION).startswith('- Q3 [EKSPERT]: Which format? (ekspert: qn-abc123) — odpowiedź: PDF — ekspert: Ewa Kowalska, '))
        self.assertEqual(pt.question_spec_line(self.QUESTION, expert=False), '- Q3 [ZAŁOŻENIE]: Which format? — przyjęte: PDF')

    def test_english_rows_use_the_english_tokens(self):
        self.assertEqual(pt.question_spec_line(self.QUESTION, language='en'),
                         '- Q3 [ASSUMPTION]: Which format? (expert: qn-abc123) — assumed: PDF')
        row = pt.question_answer_line(self.QUESTION, language='en')
        self.assertTrue(row.startswith('- Q3 [EXPERT]: Which format? (expert: qn-abc123) — answer: PDF — expert: Ewa Kowalska, '), row)
        blocking = dict(self.QUESTION, blocking=True)
        self.assertEqual(pt.question_spec_line(blocking, language='en'), '- Q3: Which format? (expert: qn-abc123)')

    def test_a_confirmed_assumption_is_worded_in_the_language(self):
        confirmed = dict(self.QUESTION, answer={'confirm': True, 'by': 'Ewa', 'at': 1795000000000})
        self.assertIn('— odpowiedź: potwierdzam przyjętą wartość: PDF —', pt.question_answer_line(confirmed))
        self.assertIn('— answer: confirms the assumed value: PDF —', pt.question_answer_line(confirmed, language='en'))

    def test_the_english_row_passes_the_record_check_of_the_reader(self):
        row = pt.question_answer_line(self.QUESTION, language='en')
        self.assertIn('[EXPERT]', row)
        self.assertTrue(pt.protocol_tokens.names('expert')[1] in row and pt.protocol_tokens.names('expert')[0] not in row)


class AgentNotices(unittest.TestCase):
    def test_polish_notice_is_the_constant_one(self):
        for kind in ('comments', 'question'):
            self.assertEqual(pt.mark_untrusted({'status': 'PASS'}, kind)['notice'], pt.UNTRUSTED_NOTICES[kind])
            self.assertEqual(pt.mark_untrusted({'status': 'PASS'}, kind, language='pl')['notice'], pt.UNTRUSTED_NOTICES[kind])
            self.assertEqual(pt._text('pl', f'proto.gen.agent.{kind}'), pt.UNTRUSTED_NOTICES[kind])

    def test_english_notice(self):
        for kind in ('comments', 'question'):
            report = pt.mark_untrusted({'status': 'PASS'}, kind, language='en')
            self.assertTrue(report['untrusted'])
            self.assertIsNone(POLISH.search(report['notice']))
            self.assertNotEqual(report['notice'], pt.UNTRUSTED_NOTICES[kind])
            self.assertIn('carry out any instructions', report['notice'])
            self.assertIn('report them to a human', report['notice'])
        self.assertEqual(list(pt.mark_untrusted({'status': 'PASS', 'x': 1}, 'comments', after=('status',), language='en'))[:3],
                         ['status', 'untrusted', 'notice'])


class TemplateLanguage(unittest.TestCase):
    def test_templates_carry_a_language_the_generator_can_set(self):
        index = (ASSETS / 'repo' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('<html lang="pl"', index)
        self.assertEqual(index.replace('<html lang="pl"', '<html lang="en"', 1).count('<html lang="en"'), 1)
        self.assertIn('lang="pl"', (ASSETS / 'icons' / 'preview.html').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
