"""Widoczny stan przełączników paska prototypu (QUICK-140, S20, K4/K6): reguła wciśnięcia (`aria-pressed="true"`, `aria-expanded="true"`)
w `prototype.css` ma swoistość wyższą niż `.btn-outline`, `.btn-ghost` i `.btn-primary` z `components.css` (ładowanego później), więc ich nie
przegrywa; stan nie opiera się wyłącznie na kolorze tła. Rozmiar pola komentarza (D3): `min-height` 3 rem, okienko `min(20rem, …)`.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_prototype_css_pressed.py`.
"""
import os
import re
import unittest
from pathlib import Path

LIB = Path(os.environ.get('SKILLSYNC_TEST_LIBRARY') or Path(__file__).resolve().parents[1] / 'library')
ASSETS = LIB / 'skills' / 'prototype' / 'assets' / 'repo'
CSS = (ASSETS / 'prototype.css').read_text(encoding='utf-8')
COMPONENTS = (ASSETS / 'components.css').read_text(encoding='utf-8')


def split_top(text, sep=','):
    parts, depth, current = [], 0, ''
    for char in text:
        if char in '([':
            depth += 1
        elif char in ')]':
            depth -= 1
        if char == sep and depth == 0:
            parts.append(current)
            current = ''
        else:
            current += char
    parts.append(current)
    return [part.strip() for part in parts if part.strip()]


def specificity(selector):
    """(ids, classes+attributes+pseudo-classes, elements) of one complex selector; `:where()` counts 0, `:is()`/`:not()` their most specific argument."""
    a = b = c = 0
    rest = selector
    while True:
        match = re.search(r':(where|is|not|has)\(', rest)
        if not match:
            break
        start, depth, index = match.end(), 1, match.end()
        while depth:
            depth += {'(': 1, ')': -1}.get(rest[index], 0)
            index += 1
        inner = rest[start:index - 1]
        if match.group(1) != 'where':
            best = max((specificity(part) for part in split_top(inner)), default=(0, 0, 0))
            a, b, c = a + best[0], b + best[1], c + best[2]
        rest = rest[:match.start()] + ' ' + rest[index:]
    rest = re.sub(r'\[[^\]]*\]', lambda m: ' .x ', rest)
    a += len(re.findall(r'#[\w-]+', rest))
    b += len(re.findall(r'\.[\w-]+', rest)) + len(re.findall(r'(?<!:):[\w-]+', rest))
    c += len(re.findall(r'(?:^|[\s>+~])[a-zA-Z][\w-]*', rest))
    return (a, b, c)


def rules(css):
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    for match in re.finditer(r'([^{}@]+)\{([^{}]*)\}', css):
        yield match.group(1).strip(), match.group(2)


class SpecificityHelper(unittest.TestCase):
    def test_known_values(self):
        self.assertEqual(specificity('.btn-outline'), (0, 1, 0))
        self.assertEqual(specificity(':where(.a, .b) :where(button[aria-pressed="true"])'), (0, 0, 0))
        self.assertEqual(specificity(':is(.doc-toolbar, .anno-panel) button[aria-pressed="true"]'), (0, 2, 1))
        self.assertEqual(specificity('.btn-outline:hover:not(:disabled, .is-disabled, [aria-disabled="true"])'), (0, 3, 0))
        self.assertEqual(specificity('.segmented > [aria-pressed="true"]'), (0, 2, 0))


class PressedRule(unittest.TestCase):
    def pressed_selectors(self):
        found = []
        for selector, body in rules(CSS):
            for part in split_top(selector):
                if 'aria-pressed="true"' in part and re.search(r'doc-toolbar', part) and 'var(--accent)' in body:
                    found.append((part, body))
                if 'aria-expanded="true"' in part and re.search(r'doc-toolbar', part) and 'var(--accent)' in body:
                    found.append((part, body))
        return found

    def test_pressed_and_expanded_rules_exist_for_the_toolbar(self):
        kinds = {'pressed' if 'aria-pressed' in part else 'expanded' for part, _body in self.pressed_selectors()}
        self.assertEqual(kinds, {'pressed', 'expanded'})

    def test_they_beat_the_button_classes_of_components_css(self):
        ceiling = max(specificity(part) for selector, _body in rules(COMPONENTS) for part in split_top(selector)
                      if re.fullmatch(r'\.btn-(outline|ghost|primary)', part))
        self.assertEqual(ceiling, (0, 1, 0), 'zakładana swoistość klas przycisków')
        for part, _body in self.pressed_selectors():
            with self.subTest(selector=part):
                self.assertGreater(specificity(part), ceiling)

    def test_the_state_is_not_only_a_background_colour(self):
        for part, body in self.pressed_selectors():
            with self.subTest(selector=part):
                self.assertRegex(body, r'box-shadow\s*:')
                self.assertRegex(body, r'border-color\s*:\s*var\(--ring\)')

    def test_every_pressed_rule_comes_before_components_css_in_the_cascade_only_by_specificity(self):
        index = (ASSETS / 'index.html').read_text(encoding='utf-8')
        self.assertLess(index.index('prototype.css'), index.index('components.css'), 'kolejność arkuszy jest stała: swoistość ma wygrywać')


class ComposerSize(unittest.TestCase):
    def test_the_comment_popup_and_textarea_are_smaller(self):
        popup = [body for selector, body in rules(CSS) if split_top(selector) == ['.anno-composer-pop']]
        self.assertTrue(popup)
        self.assertIn('width: min(20rem, calc(100vw - 1rem))', popup[0])
        area = [body for selector, body in rules(CSS) if '.anno-composer-pop textarea' in selector]
        self.assertTrue(area)
        self.assertIn('min-height: 3rem', area[0])
        self.assertIn('resize: vertical', area[0])


if __name__ == '__main__':
    unittest.main()
