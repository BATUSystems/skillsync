"""Układ okien dialogowych silnika prototypów (CORE-LOGIN-DLG): okno „Zaloguj się, żeby komentować” i „Ustaw własne hasło” mają jeden kontener
bez karty z `.form-group` (components.css), stopkę z przyciskami po prawej z odstępem i jedną obwódkę fokusu pola. Kontrola na arkuszu
`prototype.css`; wygląd w przeglądarce sprawdza się zrzutami (dowód w raporcie karty).
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_prototype_css_dialog.py`.
"""
import os
import re
import unittest
from pathlib import Path

LIB = Path(os.environ.get('SKILLSYNC_TEST_LIBRARY') or Path(__file__).resolve().parents[1] / 'library')
ASSETS = LIB / 'skills' / 'prototype' / 'assets' / 'repo'
CSS = re.sub(r'/\*.*?\*/', '', (ASSETS / 'prototype.css').read_text(encoding='utf-8'), flags=re.S)
JS = (ASSETS / 'prototype.js').read_text(encoding='utf-8')


def split_top(text):
    parts, depth, current = [], 0, ''
    for char in text:
        depth += (char in '([') - (char in ')]')
        if char == ',' and depth == 0:
            parts.append(current)
            current = ''
        else:
            current += char
    return parts + [current]


def rules(selector):
    """Declarations (dict) of every top-level rule whose selector list contains `selector` (spaces normalised), in file order."""
    found = []
    for chunk in CSS.split('}'):
        if '{' not in chunk or chunk.lstrip().startswith('@'):
            continue
        head, body = chunk.rsplit('{', 1)
        selectors = [' '.join(part.split()) for part in split_top(head)]
        if selector in selectors:
            found.append(dict((k.strip(), ' '.join(v.split())) for k, v in (item.split(':', 1) for item in body.split(';') if ':' in item)))
    return found


class DialogLayoutTests(unittest.TestCase):
    def test_the_footer_of_engine_dialogs_is_a_right_aligned_row_with_a_gap(self):
        self.assertIn("'anno-dialog-actions'", JS)
        declarations = rules('dialog .anno-dialog-actions')
        self.assertEqual(len(declarations), 1)
        self.assertEqual(declarations[0]['display'], 'flex')
        self.assertEqual(declarations[0]['justify-content'], 'flex-end')
        self.assertEqual(declarations[0]['flex-wrap'], 'wrap')
        self.assertTrue(declarations[0]['gap'].endswith('rem') and declarations[0]['gap'] != '0rem')
        self.assertNotEqual(declarations[0]['margin'].split()[0], '0', 'the footer needs room above the buttons')

    def test_the_fields_of_the_login_dialog_are_not_a_card(self):
        self.assertIn("class: 'form-group'", JS, 'the login dialog still wraps its fields in .form-group')
        declarations = rules('dialog .anno-dialog-body .form-group')
        self.assertEqual(len(declarations), 1)
        override = declarations[0]
        self.assertEqual(override['border'], '0')
        self.assertEqual(override['padding'], '0')
        self.assertEqual(override['border-radius'], '0')
        self.assertEqual(override['background'], 'transparent')

    def test_fields_have_an_even_rhythm(self):
        declarations = rules('dialog .anno-dialog-body input:not([type="checkbox"], [type="radio"])')
        self.assertEqual(len(declarations), 1)
        self.assertEqual(declarations[0]['margin-bottom'], '0.75rem')

    def test_the_focus_of_a_dialog_field_is_a_single_ring(self):
        final = rules('dialog input:focus')[-1]
        self.assertIn('transparent', final['outline'], 'outline must not add a second ring (kept transparent for forced colors)')
        self.assertEqual(final['border-color'], 'var(--ring)')
        self.assertEqual(final['box-shadow'], 'var(--shadow-focus)')


if __name__ == '__main__':
    unittest.main()
