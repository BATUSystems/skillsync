"""D-7 (CORE-1142): the prototype engine counts a token as defined only when a LIGHT block defines it, with the meaning of `theme_check.py`.

`skillsync_workflow.prototype.defined_tokens` uses the parser of the engine; the parser of the prototype skill (`css_tokens.py`, `theme_check.py`) must give the same
light set, so the two checks cannot drift apart again. The corpus covers the selectors that decide it.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'library' / 'skills' / 'prototype' / 'scripts'))
from skillsync_workflow import prototype  # noqa: E402
import css_tokens  # noqa: E402

CORPUS = {
    'light root': (':root { --a: 1; }', {'--a'}),
    'dark attribute only': (':root { --a: 1; } [data-theme="dark"] { --b: 2; }', {'--a'}),
    'dark class only': ('.dark { --a: 1; }', set()),
    'root with the dark attribute': (':root[data-theme="dark"] { --a: 1; }', set()),
    'media dark': ('@media (prefers-color-scheme: dark) { :root { --a: 1; } }', set()),
    'media dark with the fallback selector': ('@media (prefers-color-scheme: dark) { :root:not([data-theme]) { --a: 1; } }', set()),
    'other media': ('@media (min-width: 10px) { :root { --a: 1; } }', set()),
    'layer': ('@layer base { :root { --a: 1; } }', {'--a'}),
    'dark in a layer': ('@layer x { .dark { --a: 1; } } :root { --b: 2; }', {'--b'}),
    'theme': ('@theme { --a: 1; }', {'--a'}),
    'theme inline': ('@theme inline { --a: 1; }', {'--a'}),
    'compound selector': (':root, .dark { --a: 1; }', {'--a'}),
    'comment': ('/* :root { --a: 1; } */ :root { --b: 2; }', {'--b'}),
    'not root': ('html { --a: 1; }', set()),
    'supports': ('@supports (color: red) { :root { --a: 1; } }', set()),
    'mixed case name': (':root { --Ab-1: 1; }', {'--Ab-1'}),
}


class LightTokensTests(unittest.TestCase):
    def test_the_engine_counts_only_light_definitions(self):
        for name, (css, expected) in CORPUS.items():
            with self.subTest(name):
                self.assertEqual(prototype.defined_tokens(css), expected)

    def test_the_skill_parser_gives_the_same_light_set(self):
        for name, (css, expected) in CORPUS.items():
            with self.subTest(name):
                self.assertEqual(set(css_tokens.theme_tokens(css)[0]), expected)
                self.assertEqual(prototype.defined_tokens(css), set(css_tokens.theme_tokens(css)[0]))

    def test_missing_required_tokens_uses_the_light_set_over_all_stylesheets(self):
        required = ['--a', '--b', '--c']
        self.assertEqual(prototype.missing_required_tokens(required, ':root { --a: 1; } .dark { --b: 2; }', ':root { --c: 3; }'), ['--b'])
        self.assertEqual(prototype.missing_required_tokens(required, '.dark { --a: 1; --b: 2; --c: 3; }'), required)

    def test_the_project_theme_css_may_define_a_hook_of_any_class_but_not_only_in_a_dark_block(self):
        self.assertEqual(prototype.missing_required_tokens(['--hook'], ':root { --x: 1; }', '.theme-hook { --hook: 1; }'), [])
        self.assertEqual(prototype.missing_required_tokens(['--hook'], ':root { --x: 1; }', '.dark { --hook: 1; }'), ['--hook'])
        self.assertEqual(prototype.missing_required_tokens(['--hook'], ':root { --x: 1; }', ':root { --hook: 2; } .dark { --hook: 1; }'), [])

    def test_the_theme_css_rules_in_dark_media_strings_and_unclosed_files(self):
        anywhere = lambda css: prototype.defined_tokens(css, anywhere=True)  # noqa: E731
        self.assertEqual(anywhere('@media (prefers-color-scheme: dark) { :root { --a: 1; } .hook { --b: 1; } }'), set())
        self.assertEqual(anywhere('@media (min-width: 10px) { .hook { --a: 1; } }'), {'--a'})
        self.assertEqual(anywhere('.hook { --a: 1; } .dark { --a: 2; }'), {'--a'})
        self.assertEqual(anywhere('.hook, .dark { --a: 1; }'), {'--a'})                  # one selector of the rule is not dark
        self.assertEqual(anywhere('.dark { --a: 1; } [data-theme="dark"] { --b: 1; }'), set())
        self.assertEqual(anywhere('@layer x { .hook { --a: 1; } }'), {'--a'})
        self.assertEqual(anywhere('.hook { content: "--x: 1"; --y: 2; }'), {'--y'})      # a string is not a definition
        self.assertEqual(anywhere('/* .hook { --a: 1; } */ .hook { --b: 1; }'), {'--b'})
        self.assertEqual(anywhere(':root:not([data-theme]) { --a: 1; }'), {'--a'})       # outside a dark media it is a normal block

    def test_an_unclosed_theme_css_is_read_as_text_like_before(self):
        self.assertEqual(prototype.defined_tokens('.hook { --a: 1;', anywhere=True), {'--a'})
        self.assertEqual(prototype.defined_tokens('.hook { --a: 1; } .x {', anywhere=True), {'--a'})

    def test_the_composed_tokens_css_keeps_the_light_values_of_the_project(self):
        default = (ROOT / 'library' / 'skills' / 'prototype' / 'assets' / 'theme' / 'default.css').read_text(encoding='utf-8')
        composed, _ = prototype.compose_tokens_css(default, ':root { --p: 1; } .dark { --q: 2; }')
        defined = prototype.defined_tokens(composed)
        self.assertIn('--p', defined)
        self.assertNotIn('--q', defined)   # defined in a dark block only
        self.assertEqual(defined, set(css_tokens.theme_tokens(composed)[0]))


if __name__ == '__main__':
    unittest.main()
