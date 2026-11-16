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
  const win = { scrollY: 0, innerHeight: 800, document: { documentElement: { scrollHeight: 5000 } }, calls: [], frames: [], listeners: {}, removed: [] };
  win.addEventListener = (t, f, o) => { (win.listeners[t] = win.listeners[t] || []).push({ f, o }); };
  win.removeEventListener = (t, f) => { win.removed.push(t); win.listeners[t] = (win.listeners[t] || []).filter((x) => x.f !== f); };
  win.requestAnimationFrame = (f) => { win.frames.push(f); return win.frames.length; };
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
  unsub(); out.listenersAfterUnsub = { scroll: win.listeners.scroll.length, resize: win.listeners.resize.length, removed: win.removed };
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
        self.assertEqual(out['initial'], [{'y': 0, 'viewport': 800, 'height': 5000}])
        self.assertEqual((out['framesQueuedByABurst'], out['seenBeforeFrame']), (1, 1))   # five scroll events, one frame, no read yet
        self.assertEqual(out['seenAfterFrame'], [{'y': 300, 'viewport': 800, 'height': 5000}])
        self.assertEqual(out['afterResize'], {'y': 300, 'viewport': 600, 'height': 5000})

    def test_listeners_are_passive_and_removed_on_unsubscribe(self):
        self.assertEqual(self.out['passive'], {'scroll': {'passive': True}, 'resize': {'passive': True}})
        self.assertEqual(self.out['listenersAfterUnsub'], {'scroll': 0, 'resize': 0, 'removed': ['scroll', 'resize']})

    def test_go_scrolls_smoothly_and_instantly_under_reduced_motion(self):
        self.assertEqual(self.out['go'], [{'top': 0, 'left': 0, 'behavior': 'smooth'}])
        self.assertEqual(self.out['query'], '(prefers-reduced-motion: reduce)')
        self.assertEqual(self.out['goReduced'], [{'top': 0, 'left': 0, 'behavior': 'auto'}])
        self.assertEqual(self.out['goWithoutMatchMedia'], [{'top': 0, 'left': 0, 'behavior': 'smooth'}])


class Wiring(unittest.TestCase):
    def test_the_page_passes_the_hook_in_its_scroll_adapter(self):
        app = APP.read_text(encoding='utf-8')
        self.assertRegex(app, r'toTop:\s*createToTop\(window\)')
        self.assertIn('// <to-top>', app)
        self.assertIn('// </to-top>', app)


if __name__ == '__main__':
    unittest.main()
