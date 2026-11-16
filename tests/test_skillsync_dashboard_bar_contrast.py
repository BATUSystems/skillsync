"""Two shades of the progress bar (M3 of the review of STATUS-160-A): the light segment has a SHAPE (hatching) and an outline in the accent
colour, the legend swatch uses the same definition, and the accent has a contrast of at least 3:1 against the bar track in both themes
(WCAG 1.4.11), computed from the tokens of app.css."""
from pathlib import Path
import re
import unittest

CSS = (Path(__file__).resolve().parents[1] / 'library' / 'skills' / 'dashboard' / 'assets' / 'app.css').read_text(encoding='utf-8')


def luminance(hex_color):
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def tokens(block_start):
    block = CSS[CSS.index(block_start):]
    block = block[:block.index('}')]
    return {name: value.strip() for name, value in re.findall(r'(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\b', block)}


class BarShades(unittest.TestCase):
    def test_accent_contrast_with_the_track_is_at_least_three_to_one_in_both_themes(self):
        for name, start in (('light', ':root {'), ('dark', ':root[data-theme="dark"] {')):
            with self.subTest(theme=name):
                t = tokens(start)
                self.assertGreaterEqual(contrast(t['--accent'], t['--panel-2']), 3.0)

    def test_the_light_segment_has_a_shape_and_an_outline_and_the_legend_matches(self):
        self.assertIn('--bar-light: repeating-linear-gradient(', CSS)
        for selector in ('.bar .fill-active', '.bar.has-active .fill-pending', '.bar-legend .sw-light'):
            with self.subTest(selector=selector):
                rules = re.findall(re.escape(selector) + r'\s*\{([^}]*)\}', CSS)
                self.assertTrue(any('var(--bar-light)' in rule and 'inset 0 0 0 1px var(--accent)' in rule for rule in rules), rules)
        self.assertEqual(CSS.count('--bar-light: repeating-linear-gradient(135deg, var(--accent) 0 3px, transparent 3px 7px)'), 2)
        self.assertNotIn('color-mix(in srgb, var(--accent) 38%', CSS)


if __name__ == '__main__':
    unittest.main()
