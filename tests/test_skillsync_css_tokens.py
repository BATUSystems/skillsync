"""Parser tokenów CSS skilla `prototype` (`library/skills/prototype/scripts/css_tokens.py`).

Testy czytają `REPO/library` (nie zależą od SKILLSYNC_TEST_LIBRARY). Obejmują: definicje bloków zgodne z silnikiem
prototypów (jasny: `:root`, `@theme`, `@theme inline`; ciemny: dokładnie trzy selektory; `@media` osobno), zagnieżdżenia,
drugi `:root`, komentarze, napisy, numery linii, zgodność zbiorów tokenów z `extract_tokens` silnika na fiksturach
`tests/fixtures/prototype/css/*.css` oraz naprawę D-6 w `used_tokens` (deklaracja w atrybucie `style`).
"""
import subprocess
import sys
import unittest
from pathlib import Path

from tests.test_skillsync_prototype_server import setUpModule, tearDownModule  # noqa: E402,F401  (strażnik katalogu tymczasowego)

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / 'library' / 'skills' / 'prototype' / 'scripts'
FIXTURES = REPO / 'tests' / 'fixtures' / 'prototype'
sys.path.insert(0, str(SCRIPTS))

import css_tokens as ct  # noqa: E402
from skillsync_workflow import prototype as engine  # noqa: E402


def tokens(css, **kw):
    return ct.theme_tokens(css, **kw)


class BlockDefinitionTests(unittest.TestCase):
    def test_light_blocks_are_root_theme_and_theme_inline(self):
        light, dark = tokens(':root { --a: 1; }\n@theme { --b: 2; }\n@theme inline { --c: 3; }\n')
        self.assertEqual((light, dark), ({'--a', '--b', '--c'}, set()))

    def test_second_root_block_is_seen(self):
        light, _ = tokens(':root { --a: 1; }\n.x { --no: 1; }\n:root { --b: 2; }\n')
        self.assertEqual(light, {'--a', '--b'})

    def test_layer_is_entered_media_and_supports_are_not(self):
        css = ('@layer base { :root { --in-layer: 1; } .dark { --dark-layer: 1; } }\n'
               '@media (min-width: 1px) { :root { --media: 1; } }\n@supports (display: grid) { :root { --sup: 1; } }\n')
        self.assertEqual(tokens(css), ({'--in-layer'}, {'--dark-layer'}))

    def test_selector_list_with_root_counts(self):
        light, _ = tokens('body, :root { --a: 1; }\n')
        self.assertEqual(light, {'--a'})

    def test_dark_blocks_are_exactly_the_three_selectors(self):
        css = ('.dark { --a: 1; }\n[data-theme=dark] { --b: 1; }\n[data-theme="dark"] { --c: 1; }\n'
               ":root[data-theme='dark'] { --d: 1; }\n")
        self.assertEqual(tokens(css)[1], {'--a', '--b', '--c', '--d'})

    def test_card_dark_and_descendants_are_not_dark_blocks(self):
        css = '.card-dark { --a: 1; }\n.dark .card { --b: 1; }\n:root [data-theme=dark] { --c: 1; }\n.card { --d: 1; }\n'
        self.assertEqual(tokens(css), (set(), set()))

    def test_theme_other_than_plain_or_inline_is_not_light(self):
        self.assertEqual(tokens('@theme static { --a: 1; }\n')[0], set())

    def test_nested_rule_inside_root_is_not_a_root_block(self):
        light, _ = tokens(':root { --a: 1; .child { --inner: 1; } }\n')
        self.assertEqual(light, {'--a'})

    def test_media_dark_is_its_own_kind(self):
        css = '@media (prefers-color-scheme: dark) { :root:not([data-theme]) { --m: 1; } }\n'
        self.assertEqual(tokens(css), (set(), set()))
        self.assertEqual(tokens(css, media_dark_as_dark=True), (set(), {'--m'}))
        kinds = [b.kind for b in ct.parse_theme(css)]
        self.assertEqual(kinds, [ct.MEDIA_DARK])

    def test_media_dark_inside_layer(self):
        css = '@layer x { @media (prefers-color-scheme: dark) { :root { --m: 1; } } }\n'
        self.assertEqual([b.kind for b in ct.parse_theme(css)], [ct.MEDIA_DARK])

    def test_root_and_dark_in_one_list_counts_for_both(self):
        light, dark = tokens(':root, .dark { --a: 1; }\n')
        self.assertEqual((light, dark), ({'--a'}, {'--a'}))


class LexingTests(unittest.TestCase):
    def test_comments_are_ignored(self):
        css = '/* :root { --ghost: 1; } */ :root { --a: 1; /* --ghost2: 2; */ --b: /* x */ 2; }\n'
        self.assertEqual(tokens(css)[0], {'--a', '--b'})
        self.assertEqual(dict((n, v) for _s, n, v in ct.css_declarations(css, with_values=True))['--b'], '2')

    def test_comment_marker_inside_string_is_not_a_comment(self):
        css = ':root { --a: "/* not a comment"; --b: 2; }\n'
        self.assertEqual(tokens(css)[0], {'--a', '--b'})

    def test_strings_hide_braces_and_semicolons(self):
        css = ':root { --font: "Inter; sans}serif", \'Fira;Code\'; --b: url("data:x;}"); --c: 3; }\n'
        self.assertEqual(tokens(css)[0], {'--font', '--b', '--c'})

    def test_semicolon_inside_parentheses_does_not_split(self):
        css = ':root { --img: url(data:image/svg+xml;utf8,<svg/>); --b: 1; }\n'
        self.assertEqual(tokens(css)[0], {'--img', '--b'})

    def test_values_have_normalized_whitespace(self):
        decl = ct.css_declarations(':root { --a:   1px\n   2px ; }', with_values=True)
        self.assertEqual(decl, [((':root',), '--a', '1px 2px')])

    def test_line_numbers_follow_the_original_text_with_comments(self):
        css = '/* a\nb */\n:root {\n  --a: 1;\n  /* c\n  d */\n  --b: 2;\n}\n'
        lines = {name: line for _s, name, _v, line in ct.css_declarations_lines(css)}
        self.assertEqual(lines, {'--a': 4, '--b': 7})

    def test_unclosed_block_does_not_crash(self):
        light, dark = tokens(':root { --a: 1;\n.dark { --c: 3;\n')
        self.assertIn('--a', light)

    def test_parse_theme_merges_blocks_of_one_key_and_keeps_the_last_value(self):
        blocks = ct.parse_theme(':root { --a: 1; }\n:root { --a: 2; --b: 3; }\n')
        self.assertEqual(len(blocks), 1)
        self.assertEqual({k: v[0] for k, v in blocks[0].tokens.items()}, {'--a': '2', '--b': '3'})

    def test_per_block_values_of_dark_blocks(self):
        blocks = []
        tokens('.dark { --a: 1; }\n[data-theme="dark"] { --a: 2; }\n', blocks=blocks)
        self.assertEqual(blocks, [('.dark', {'--a': '1'}), ('[data-theme="dark"]', {'--a': '2'})])


class EngineParityTests(unittest.TestCase):
    """Zbiory tokenów jasnych i ciemnych parsera są takie same jak w `extract_tokens` silnika.

    Różnice (świadome): bloki w `@media (prefers-color-scheme: dark)` silnik pomija z ostrzeżeniem, parser zgłasza je
    jako rodzaj `media-dark` (poza `theme_tokens` bez `media_dark_as_dark`, które ich nie liczy, jak silnik);
    niezamknięty blok silnik odrzuca błędem, parser jest tolerancyjny (kontrola motywu ma raportować, nie przerywać).
    """

    def test_same_light_and_dark_sets_on_css_fixtures(self):
        checked = 0
        for path in sorted((FIXTURES / 'css').glob('*.css')):
            css = path.read_text(encoding='utf-8-sig')
            try:
                light, dark, _warnings = engine.extract_tokens(css, path.name)
            except ValueError:
                self.assertIn(path.name, ('unclosed.css', 'unclosed-after-string.css'))
                continue
            with self.subTest(path.name):
                self.assertEqual(tokens(css), (set(light), set(dark)))
                checked += 1
        self.assertGreaterEqual(checked, 6)

    def test_theme_variant_is_covered_by_a_fixture(self):
        css = (FIXTURES / 'css' / 'tailwind.css').read_text(encoding='utf-8-sig')
        self.assertIn('@theme inline', css)
        light, _ = tokens(css)
        self.assertTrue({'--color-brand', '--color-background'} <= light)

    def test_media_only_fixture_differs_only_by_the_media_kind(self):
        css = (FIXTURES / 'css' / 'media-only.css').read_text(encoding='utf-8-sig')
        self.assertEqual(tokens(css), (set(), set()))
        self.assertEqual(tokens(css, media_dark_as_dark=True), (set(), {'--only-media'}))

    def test_unclosed_fixtures_raise_in_the_engine_and_not_in_the_parser(self):
        for name in ('unclosed.css', 'unclosed-after-string.css'):
            css = (FIXTURES / 'css' / name).read_text(encoding='utf-8-sig')
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    engine.extract_tokens(css, name)
                self.assertIn('--a', tokens(css)[0])

    def test_engine_parity_fixtures_same_sets_and_values(self):
        """Niezbalansowany nawias, napis z końcem linii, nazwy Unicode i `!important`: te same zbiory i wartości.
        Wartości porównujemy po normalizacji białych znaków; `!important` zostaje w wartości dosłownie w obu parserach."""
        names = sorted(p.name for p in (FIXTURES / 'css-tokens' / 'engine-parity').glob('*.css'))
        self.assertEqual(names, ['important.css', 'string-newline.css', 'unbalanced-paren.css', 'unicode-name.css'])
        for name in names:
            css = (FIXTURES / 'css-tokens' / 'engine-parity' / name).read_text(encoding='utf-8')
            light, dark, _ = engine.extract_tokens(css, name)
            with self.subTest(name):
                self.assertEqual(tokens(css), (set(light), set(dark)))
                mine = {n: v for b in ct.parse_theme(css) if b.kind == ct.LIGHT for n, (v, _l) in b.tokens.items()}
                self.assertEqual(mine, {k: ' '.join(v.split()) for k, v in light.items()})

    def test_important_stays_in_the_value(self):
        css = (FIXTURES / 'css-tokens' / 'engine-parity' / 'important.css').read_text(encoding='utf-8')
        self.assertEqual(ct.css_declarations(css, with_values=True)[0][2], '1 !important')

    def test_default_theme_same_sets_as_engine(self):
        css = (REPO / 'library' / 'skills' / 'prototype' / 'assets' / 'theme' / 'default.css').read_text(encoding='utf-8')
        light, dark, _ = engine.extract_tokens(css, 'default.css')
        self.assertEqual(tokens(css), (set(light), set(dark)))


class ScannerRuleTests(unittest.TestCase):
    def test_paren_counter_resets_at_braces(self):
        self.assertEqual(tokens(':root { --a: (1; --b: 2; }\n:root { --c: 3; }\n')[0], {'--a', '--c'})

    def test_string_ends_at_newline_like_in_the_engine(self):
        light, dark = tokens(':root { --a: "abc\n--b: 2; }\n.dark { --c: 3; }\n')
        self.assertEqual((light, dark), ({'--a'}, {'--c'}))

    def test_unicode_token_names(self):
        self.assertEqual(tokens(':root { --café: 1; }\n')[0], {'--café'})
        used, _ = ct.used_tokens_in_text('a { color: var(--café); }', False)
        self.assertEqual(used, {'--café'})

    def test_nesting_deeper_than_the_limit_is_a_value_error(self):
        with self.assertRaises(ValueError):
            tokens('.a {' * (ct.MAX_NESTING + 1))
        tokens('.a {' * ct.MAX_NESTING)

    def test_apostrophe_in_html_prose_does_not_start_a_string(self):
        html = "<p>It's here</p> /* var(--ghost) */ <i style=\"margin: var(--m)\"></i>\n<style>.a { --loc: 1; color: var(--loc) var(--z); }</style>\n"
        used, local = ct.used_tokens_in_text(html, True)
        self.assertEqual(used - local, {'--m', '--z'})

    def test_dark_hints(self):
        css = ('html[data-theme=dark] { --a: 1; }\n.dark { background: #000; color: red; }\n'
               ':root { --b: 2; }\n.card .dark-x { --c: 3; }\n.dark .card { color: red; }\n')
        self.assertEqual([(h, k) for h, k, _l in ct.dark_hints(css)],
                         [('unrecognized', 'html[data-theme=dark]'), ('plain', '.dark')])

    def test_used_tokens_rejects_oversized_and_non_utf8_assets(self):
        import tempfile
        from pathlib import Path as P
        with tempfile.TemporaryDirectory(prefix='css-tokens-') as tmp:
            (P(tmp) / 'bad.css').write_bytes(b'a { b: \xff; }')
            with self.assertRaises(ValueError) as ctx:
                ct.used_tokens(tmp)
            self.assertIn('bad.css', str(ctx.exception))
            (P(tmp) / 'bad.css').write_bytes(b'x' * (ct.MAX_FILE_BYTES + 1))
            with self.assertRaises(ValueError):
                ct.used_tokens(tmp)


class UsedTokensTests(unittest.TestCase):
    """D-6: deklaracja `--x:` w atrybucie `style` obowiązuje tylko dla tego atrybutu."""

    ASSETS = FIXTURES / 'css-tokens' / 'style-attr'

    def test_style_attribute_declaration_does_not_mask_use_elsewhere(self):
        self.assertEqual(ct.used_tokens(self.ASSETS), {'--shared', '--ink'})

    def test_local_var_in_the_same_attribute_is_not_a_theme_token(self):
        self.assertNotIn('--gap', ct.used_tokens(self.ASSETS))

    def test_stylesheet_declaration_still_masks(self):
        used, local = ct.used_tokens_in_text('.a { --x: 1; margin: var(--x) var(--y); }', False)
        self.assertEqual(used - local, {'--y'})

    def test_style_block_in_html_still_masks(self):
        used, local = ct.used_tokens_in_text('<style>.a { --x: 1; margin: var(--x); }</style>', True)
        self.assertEqual(used - local, set())

    def test_check_tokens_print_used_shows_the_fix(self):
        proc = subprocess.run([sys.executable, '-I', str(SCRIPTS / 'check_tokens.py'), '--print-used', '--assets',
                               str(self.ASSETS)], capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.split(), ['--ink', '--shared'])

    def test_engine_prefix_tokens_are_ignored(self):
        used, local = ct.used_tokens_in_text('.a { width: var(--proto-x); }', False)
        self.assertEqual(used, {'--proto-x'})


class GuardModeTests(unittest.TestCase):
    """Strażnik skilla liczy `@media` ciemny jako blok ciemny i zawęża `is_dark` do selektorów silnika."""

    def test_guard_counts_media_dark_and_not_card_dark(self):
        css = '.card-dark { --x: 1; }\n@media (prefers-color-scheme: dark) { :root { --m: 1; } }\n.dark { --d: 1; }\n'
        self.assertEqual(tokens(css, media_dark_as_dark=True), (set(), {'--m', '--d'}))

    def test_check_tokens_reexports_the_parser(self):
        sys.path.insert(0, str(SCRIPTS))
        import check_tokens
        self.assertIs(check_tokens.theme_tokens, ct.theme_tokens)
        self.assertIs(check_tokens.used_tokens, ct.used_tokens)


class ReadRequiredTests(unittest.TestCase):
    """CORE-1190-C8B: `read_required` (the same file as `prototype.parse_required_tokens`) must not cut an entry at VT, FF, NEL, LS or PS."""

    def read(self, text):
        import tempfile
        from pathlib import Path as P
        with tempfile.TemporaryDirectory(prefix='css-tokens-') as tmp:
            target = P(tmp) / 'required-tokens.txt'
            target.write_bytes(text.encode('utf-8'))
            return ct.read_required(target)

    def test_a_rare_separator_inside_an_entry_does_not_split_it(self):
        for name, sep in {'VT': '\x0b', 'FF': '\x0c', 'FS': '\x1c', 'GS': '\x1d', 'RS': '\x1e', 'NEL': '\x85', 'LS': '\u2028', 'PS': '\u2029'}.items():
            with self.subTest(separator=name):
                self.assertEqual(self.read('--a%sb\n--c\n' % sep), ['--a%sb' % sep, '--c'])

    def test_crlf_lf_and_a_lone_cr_separate_entries_comments_and_blanks_are_skipped(self):
        self.assertEqual(self.read('\ufeff# c\r\n--a\r\n\r\n  --b  \n--c\r--d'), ['--a', '--b', '--c', '--d'])
        self.assertEqual(self.read(''), [])

    def test_the_library_file_gives_the_same_list_as_the_engine(self):
        path = REPO / 'library' / 'skills' / 'prototype' / 'assets' / 'theme' / 'required-tokens.txt'
        self.assertTrue(ct.read_required(path))
        self.assertEqual(ct.read_required(path), engine.parse_required_tokens(path.read_text(encoding='utf-8')))


class UnquotedStyleAttributeTests(unittest.TestCase):
    """CORE-1200-T4FIX (M1): `var(--x)` in a `style` attribute without quotes counts as a use of the token (no silent miss)."""

    def test_unquoted_and_unterminated_values_are_read(self):
        for html in ('<p style=color:var(--a)>x</p>', '<p style="color:var(--a)>x</p>', "<p style='color:var(--a)>x</p>", '<p STYLE = color:var(--a) >x</p>'):
            with self.subTest(html=html):
                used, local = ct.used_tokens_in_text(html, True)
                self.assertEqual((used, local), ({'--a'}, set()))
        used, _ = ct.used_tokens_in_text('<p style=--b:1;color:var(--b)>x</p>', True)
        self.assertEqual(used, set())   # its own declaration in the same attribute is not a use of the theme token

    def test_a_quoted_value_still_works_and_the_helper_strips_the_quotes(self):
        self.assertEqual(ct.style_attr_body('"a:b"'), 'a:b')
        self.assertEqual(ct.style_attr_body("'a:b'"), 'a:b')
        self.assertEqual(ct.style_attr_body('a:b'), 'a:b')
        self.assertEqual(ct.style_attr_body('"a:b'), 'a:b')


if __name__ == '__main__':
    unittest.main()
