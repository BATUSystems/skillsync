"""180-G: the grid of phases (`.work`) is spaced from the block above it (the glossary and the result box have a top margin only) by the same gap as the
cards inside the grid (`.phases` gap), in every layout of the stylesheet; without it the glossary touched the first phase card on a narrow screen."""
import re
import unittest
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / 'library' / 'skills' / 'dashboard' / 'assets' / 'app.css'


def declarations(text):
    """[(selectors, {property: value})] of every innermost rule of a stylesheet (comments removed; a rule inside `@container` is listed too, with an
    index of its block so that the rules of one layout can be told from another)."""
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    out, depth_stack, pos = [], [], 0
    blocks = []   # (start, end) of each @container block
    for match in re.finditer(r'@container[^{]*\{', text):
        depth, i = 1, match.end()
        while depth:
            depth += {'{': 1, '}': -1}.get(text[i], 0)
            i += 1
        blocks.append((match.start(), i))
    for match in re.finditer(r'([^{}@]+)\{([^{}]*)\}', text):
        layout = next((n for n, (a, b) in enumerate(blocks) if a < match.start() < b), None)
        props = {k.strip(): v.strip() for k, v in re.findall(r'([\w-]+)\s*:\s*([^;]+);?', match.group(2))}
        out.append(([s.strip() for s in match.group(1).split(',')], props, layout))
    return out


class PhaseGridSpacing(unittest.TestCase):
    def test_work_margin_top_equals_phase_gap_in_every_layout(self):
        rules = declarations(CSS.read_text(encoding='utf-8'))
        by_layout = {}
        for selectors, props, layout in rules:
            for selector in selectors:
                if selector in ('.phases', '.work'):
                    entry = by_layout.setdefault(layout, {})
                    if 'gap' in props and selector == '.phases':
                        entry['gap'] = props['gap']
                    if 'gap' in props and selector == '.work' and 'phases-gap-set-by-work' not in entry:
                        entry.setdefault('work-gap', props['gap'])
                    if 'margin-top' in props and selector == '.work':
                        entry['margin-top'] = props['margin-top']
        self.assertTrue(any('margin-top' in entry for entry in by_layout.values()), '`.work` has no margin-top at all')
        checked = 0
        for layout, entry in by_layout.items():
            gap = entry.get('gap') or entry.get('work-gap')
            if gap is None:
                continue
            checked += 1
            self.assertEqual(entry.get('margin-top'), gap, f'layout {layout}: `.work` margin-top {entry.get("margin-top")!r} != phase gap {gap!r}')
        self.assertGreaterEqual(checked, 3, 'the base layout and the two narrow layouts must be checked')

    def test_mutation_without_the_margin_fails(self):
        text = CSS.read_text(encoding='utf-8')
        broken = re.sub(r'\.work \{ margin-top: [^}]*\}', '', text)
        broken = broken.replace('; margin-top: .8rem; }', '; }').replace('; } .work { margin-top: .5rem; }', '; }')
        self.assertNotEqual(broken, text)
        rules = declarations(broken)
        self.assertFalse(any('margin-top' in props and '.work' in selectors for selectors, props, _ in rules))


if __name__ == '__main__':
    unittest.main()
