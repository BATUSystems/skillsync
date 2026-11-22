"""D53: the state of the live indicator is not told by colour alone and touch targets are at least 44 px (2.75rem)."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / 'library' / 'skills' / 'dashboard' / 'assets' / 'app.css'
RENDER = ROOT / 'library' / 'skills' / 'dashboard' / 'assets' / 'dashboard-render.js'
STATES = ('live', 'polling', 'down', 'connecting')
TAP_SELECTORS = ('.btn', '.wait-go', '.act-approve', '.act-doc', '.tabs [role="tab"]')


def rules(text):
    """[(selectors, body)] of the flat rules of a CSS text without comments (nested blocks are returned inside out)."""
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    return [([s.strip() for s in match.group(1).split(',')], match.group(2))
            for match in re.finditer(r'([^{}]+)\{([^{}]*)\}', text)]


def compact_block(text):
    start = text.index('@container dash (max-width: 420px)')
    depth, index = 0, text.index('{', start)
    for position in range(index, len(text)):
        depth += {'{': 1, '}': -1}.get(text[position], 0)
        if depth == 0:
            return text[index + 1:position]
    raise AssertionError('unterminated compact block')


def rem(value):
    return float(re.fullmatch(r'([\d.]+)rem', value.strip()).group(1))


class LiveShape(unittest.TestCase):
    def test_every_state_has_its_own_dot_shape_besides_the_colour(self):
        css = CSS.read_text(encoding='utf-8')
        shape = {}
        for selectors, body in rules(css):
            for state in STATES:
                if f'.live.is-{state} .live-dot' in selectors:
                    keys = ('border-radius', 'clip-path', 'border')
                    shape.setdefault(state, {}).update({k: v.strip() for k, v in re.findall(r'([\w-]+)\s*:\s*([^;]+)', body) if k in keys})
        # the live state keeps the base shape (circle); the others differ from it and from each other
        signatures = {state: tuple(sorted(shape.get(state, {}).items())) for state in STATES}
        self.assertEqual(signatures['live'], (), 'live: the base circle')
        self.assertEqual(len(set(signatures.values())), len(STATES), signatures)

    def test_every_state_set_by_the_page_is_styled(self):
        script = RENDER.read_text(encoding='utf-8')
        for state in STATES:
            self.assertRegex(CSS.read_text(encoding='utf-8'), rf'\.live\.is-{state} \.live-dot')
            self.assertRegex(script, rf'\b{state}:\s*"')   # the key of LIVE_TEXT: the page really uses this state


class CommandFailedState(unittest.TestCase):
    def test_the_command_failed_state_is_told_in_words_not_by_colour_alone(self):
        """CORE-TAP K8: the header of a tests section says it in text (exit code and "no test reported a failure")."""
        script = RENDER.read_text(encoding='utf-8')
        self.assertIn('żaden test nie zgłosił porażki', script)
        self.assertRegex(script, r'kod \{exit\}')   # the exit code is a parameter of the catalog sentence (built-in Polish default)
        self.assertRegex(script, r'tr\("ui\.tests\.cmdFailedTip", \{ exit: ')   # ... and the renderer passes it

    def test_the_full_sentence_is_a_visible_body_line_and_the_chip_wraps(self):
        script = RENDER.read_text(encoding='utf-8')
        self.assertRegex(script, r'class: "tnote cmd-failed-note" \}, tr\("ui\.tests\.cmdFailedTip"')   # visible for keyboard and touch, not only a tooltip
        self.assertIn('wyniki ok dotyczą tylko testów', script)   # the full sentence is in the built-in default catalog
        css = CSS.read_text(encoding='utf-8')
        chip = re.search(r'\.chip\.cmd-failed\s*\{([^}]*)\}', css)
        self.assertIsNotNone(chip)
        self.assertIn('white-space: normal', chip.group(1))   # `.chip` is nowrap: the long text would be clipped in a narrow column


class TapTargets(unittest.TestCase):
    def test_compact_mode_targets_are_at_least_44_px(self):
        block = compact_block(CSS.read_text(encoding='utf-8'))
        found = {}
        for selectors, body in rules(block):
            height = re.search(r'min-height:\s*([\d.]+rem)', body)
            if height:
                for selector in selectors:
                    found[selector] = rem(height.group(1))
        for selector in TAP_SELECTORS:
            self.assertIn(selector, found, selector)
            self.assertGreaterEqual(found[selector], 2.75, selector)


if __name__ == '__main__':
    unittest.main()
