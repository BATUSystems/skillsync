"""170-B: the page adapter of the "back to top" button (`createToTop` in app.js) and the CSS of `.to-top`.

The adapter is cut out of app.js between the markers `<to-top>` and `</to-top>` and run in node with a fake window: one metrics read per
animation frame, a passive listener, unsubscribe, smooth scrolling and the instant one under prefers-reduced-motion."""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from tests.test_skillsync_dashboard_renderer import NODE, NODE_MAJOR  # noqa: E402

ASSETS = ROOT / 'library' / 'skills' / 'dashboard' / 'assets'
APP = ASSETS / 'app.js'
CSS = ASSETS / 'app.css'

SCRIPT = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const body = src.slice(src.indexOf('// <to-top>'), src.indexOf('// </to-top>'));
const make = (reduce) => {
  const win = { scrollY: 0, innerHeight: 800, calls: [], frames: [], cancelled: [], listeners: {}, removed: [] };
  win.addEventListener = (t, f, o) => { (win.listeners[t] = win.listeners[t] || []).push({ f, o }); };
  win.removeEventListener = (t, f) => { win.removed.push(t); win.listeners[t] = (win.listeners[t] || []).filter((x) => x.f !== f); };
  win.requestAnimationFrame = (f) => { win.frames.push(f); return win.frames.length; };
  win.cancelAnimationFrame = (id) => { win.cancelled.push(id); };
  win.matchMedia = (q) => { win.lastQuery = q; return { matches: reduce }; };
  win.scrollTo = (o) => { win.calls.push(o); };
  return win;
};
const factory = new Function(body + '\nreturn createToTop;')();
const out = {};
{
  const win = make(false); const seen = [];
  const hook = factory(win);
  const unsub = hook.subscribe((m) => seen.push(m));
  out.initial = seen.slice();
  win.scrollY = 300; for (let i = 0; i < 5; i++) win.listeners.scroll.forEach((l) => l.f());
  out.framesQueuedByABurst = win.frames.length; out.seenBeforeFrame = seen.length;
  win.frames.shift()(); out.seenAfterFrame = seen.slice(1);
  win.innerHeight = 600; win.listeners.resize.forEach((l) => l.f()); win.frames.shift()(); out.afterResize = seen[seen.length - 1];
  out.passive = { scroll: win.listeners.scroll[0].o, resize: win.listeners.resize[0].o };
  win.listeners.scroll.forEach((l) => l.f()); const queuedBeforeUnsub = win.frames.length;   // a frame is queued when the hook is dropped
  unsub(); out.cancelledOnUnsub = { queued: queuedBeforeUnsub, cancelled: win.cancelled.slice() }; out.listenersAfterUnsub = { scroll: win.listeners.scroll.length, resize: win.listeners.resize.length, removed: win.removed };
  hook.go(); out.go = win.calls; out.query = win.lastQuery;
}
{
  const win = make(true); factory(win).go(); out.goReduced = win.calls;
  const bare = make(false); delete bare.matchMedia; factory(bare).go(); out.goWithoutMatchMedia = bare.calls;
}
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(NODE_MAJOR >= 18, "set SKILLSYNC_TEST_NODE (or have node on PATH), node >= 18")
class PageAdapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'to_top.cjs'
            script.write_text(SCRIPT, encoding='utf-8')
            proc = subprocess.run([NODE, str(script), str(APP)], capture_output=True, text=True, timeout=60, cwd=tmp)
        assert proc.returncode == 0, proc.stderr[-1500:]
        cls.out = json.loads(proc.stdout)

    def test_subscribe_reads_the_metrics_at_once_and_once_per_frame(self):
        out = self.out
        self.assertEqual(out['initial'], [{'y': 0, 'viewport': 800}])
        self.assertEqual((out['framesQueuedByABurst'], out['seenBeforeFrame']), (1, 1))   # five scroll events, one frame, no read yet
        self.assertEqual(out['seenAfterFrame'], [{'y': 300, 'viewport': 800}])
        self.assertEqual(out['afterResize'], {'y': 300, 'viewport': 600})

    def test_listeners_are_passive_and_removed_on_unsubscribe(self):
        self.assertEqual(self.out['passive'], {'scroll': {'passive': True}, 'resize': {'passive': True}})
        self.assertEqual(self.out['listenersAfterUnsub'], {'scroll': 0, 'resize': 0, 'removed': ['scroll', 'resize']})
        self.assertEqual(self.out['cancelledOnUnsub'], {'queued': 1, 'cancelled': [1]})   # the frame queued at that moment is cancelled: no callback after the unsubscribe

    def test_go_scrolls_smoothly_and_instantly_under_reduced_motion(self):
        self.assertEqual(self.out['go'], [{'top': 0, 'left': 0, 'behavior': 'smooth'}])
        self.assertEqual(self.out['query'], '(prefers-reduced-motion: reduce)')
        self.assertEqual(self.out['goReduced'], [{'top': 0, 'left': 0, 'behavior': 'auto'}])
        self.assertEqual(self.out['goWithoutMatchMedia'], [{'top': 0, 'left': 0, 'behavior': 'smooth'}])


def declarations(selector):
    text = re.sub(r'/\*.*?\*/', '', CSS.read_text(encoding='utf-8'), flags=re.S)
    for match in re.finditer(r'([^{}]+)\{([^{}]*)\}', text):
        if match.group(1).strip() == selector:
            return {key.strip(): value.strip() for key, value in re.findall(r'([\w-]+)\s*:\s*([^;]+)', match.group(2))}
    raise AssertionError(f'no rule for {selector}')


def luminance(hex_color):
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def tokens(selector):
    css = CSS.read_text(encoding='utf-8')
    start = css.index(selector + ' {')
    block = css[css.index('{', start) + 1:css.index('}', start)]
    return dict(re.findall(r'(--[\w-]+):\s*(#[0-9a-fA-F]{6})', block))


class ButtonCss(unittest.TestCase):
    def test_fixed_44_px_circle_with_the_page_tokens(self):
        rule = declarations('button.to-top')   # `button.` beats [data-tip] and :focus-visible by specificity, not by the order in the file
        self.assertEqual(rule['position'], 'fixed')
        self.assertEqual((rule['width'], rule['height']), ('max(2.75rem, 44px)', 'max(2.75rem, 44px)'))   # 44 px also where the root font is 15 px
        self.assertIn('env(safe-area-inset-bottom)', rule['bottom'])
        self.assertIn('env(safe-area-inset-right)', rule['right'])
        for key, token in (('background', 'var(--panel)'), ('color', 'var(--accent)'), ('box-shadow', 'var(--shadow)')):
            self.assertEqual(rule[key], token, key)
        self.assertIn('var(--line)', rule['border'])
        self.assertNotRegex(' '.join(rule.values()), r'#[0-9a-fA-F]{3,6}\b')   # no fixed colours: both themes come from the tokens

    def test_layer_is_above_the_sticky_bars_and_the_menu_and_below_the_tooltips(self):
        css = re.sub(r'/\*.*?\*/', '', CSS.read_text(encoding='utf-8'), flags=re.S)
        z = int(declarations('button.to-top')['z-index'])
        menu = int(declarations('.menu')['z-index'])
        tip = int(re.search(r'\.desc-tip\s*\{[^}]*z-index:\s*(\d+)', css).group(1))
        self.assertGreater(z, menu)
        self.assertLess(z, tip)

    def test_hidden_attribute_wins_over_the_grid_display(self):
        self.assertEqual(declarations('button.to-top[hidden]')['display'], 'none')

    def test_the_icon_has_at_least_3_to_1_contrast_with_the_background_in_both_themes(self):
        for name, selector in (('light', ':root'), ('dark', ':root[data-theme="dark"]')):
            token = tokens(selector)
            self.assertGreaterEqual(contrast(token['--accent'], token['--panel']), 3.0, name)

    def test_the_document_keeps_room_for_the_button(self):
        self.assertEqual(declarations('.has-to-top #view-docs .doc-view')['padding-bottom'], '4.5rem')   # only where the button can exist

    def test_the_tooltip_opens_above_the_button(self):
        tip = declarations('button.to-top:hover::after, button.to-top:focus-visible::after')
        self.assertEqual((tip['top'], tip['bottom']), ('auto', 'calc(100% + .45rem)'))   # there is no room below a button 16 px from the bottom edge


class Wiring(unittest.TestCase):
    def test_the_page_passes_the_hook_in_its_scroll_adapter(self):
        app = APP.read_text(encoding='utf-8')
        self.assertRegex(app, r'toTop:\s*createToTop\(window\)')
        self.assertIn('// <to-top>', app)
        self.assertIn('// </to-top>', app)


class SheetVersusToTop(unittest.TestCase):
    """170-AB: the bottom sheet of the commits panel (narrow container) must end above the "back to top" button, and only when that
    button can exist (`has-to-top` on the root); without the hook the sheet keeps its place."""

    def css(self):
        return CSS.read_text(encoding='utf-8')

    def test_the_sheet_keeps_its_bottom_without_the_button(self):
        css = self.css()
        base = re.search(r'@container dash \(max-width: 640px\) \{.*?\.commits-panel \{([^}]*)\}', css, re.S)
        self.assertIsNotNone(base, 'the sheet rule in the 640px container block')
        self.assertIn('bottom: 8px', base.group(1))

    def test_the_sheet_margin_depends_on_has_to_top_and_clears_the_button(self):
        css = self.css()
        block = css[css.index('@container dash (max-width: 640px) {'):]
        rule = re.search(r'\.has-to-top \.commits-panel \{([^}]*)\}', block)
        self.assertIsNotNone(rule, 'a rule that depends on the has-to-top class inside the container block')
        bottom = re.search(r'bottom:\s*calc\(([^;]*)\);', rule.group(1)).group(1)
        # the same expressions as the button: its bottom offset and its height, plus a gap
        button = re.search(r'button\.to-top \{([^}]*)\}', css).group(1)
        offset = re.search(r'bottom:\s*(max\([^;]*\));', button).group(1)
        height = re.search(r'height:\s*(max\([^;]*\));', button).group(1)
        self.assertIn(offset, bottom)
        self.assertIn(height, bottom)
        gap = re.search(r'\+\s*([\d.]+)(rem|px)\s*$', bottom.strip())
        self.assertIsNotNone(gap, 'a gap after the button height')
        self.assertGreater(float(gap.group(1)) * (16 if gap.group(2) == 'rem' else 1), 0)


if __name__ == '__main__':
    unittest.main()
