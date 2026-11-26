"""CORE-1180-MULTIWT M-5: the page of a server with several tabs (worktrees of one project).

Two parts, both run in node on stubs of the browser (no network, no server):

- `TabBarRenderer`: `DashboardRenderer.createTabBar` of `dashboard-render.js` (a pure view): roles and `aria-selected`, roving `tabindex`, the keys of the
  tablist (arrows, Home, End), the indicator of a tab that waits for the user, a tab without a token (read-only), a removed tab, "show all", the alerts,
  data only as text, no needless redraw, PL and EN texts (EN has no Polish character, every `ui.wt.*` key is drawn).
- `TabsPageAdapter`: `app.js` against a fake renderer: `/api/tabs`, the `tab` of every request, the token of a tab (`id.token`, several in one fragment), the
  state per tab (plan, view), one stream for the tab on the page, forgetting a bare token when a second tab appears, a refused or removed tab, an answer
  of a tab the user has left. A server with one tab (or without `/api/tabs`) is the page of today: no `tab` anywhere.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from tests.test_skillsync_dashboard_renderer import NODE, NODE_MAJOR, RENDERER, fixture_files, ms  # noqa: E402
from tests.test_skillsync_dashboard_renderer_i18n import HEAD, POLISH, clone, run_node, ui_catalog  # noqa: E402

APP = RENDERER.parent / "app.js"
SKIP = unittest.skipUnless(NODE_MAJOR >= 18, "set SKILLSYNC_TEST_NODE (or have node on PATH), node >= 18")
TAB_A, TAB_B, TAB_C = "main", "3fa9c1d20b7e", "9b21e07c44aa"
# assembled: a literal key-like string trips the secret scanner
HEX = "".join(("0123456789", "abcdef", "0123456789", "abcdef", "0123456789", "abcdef", "01"))   # 64 characters, the shape of an HMAC
PAIR_A, PAIR_B = f"{TAB_A}.{HEX}", f"{TAB_B}.{HEX[::-1]}"
BARE = "".join(("AbCdEfGh", "_ijkl", "MNOP-", "1234567890"))


# --------------------------------------------------------------------------------------------------------------------------------------- the bar
BAR_TAIL = r"""
(async () => {
  const out = { runs: {}, violations };
  const used = new Set();
  const cat = input.catalogs[input.lang];
  const t = (key, params) => {
    used.add(key);
    const v = cat[key];
    return (typeof v === 'string' ? v : key).replace(/\{(\w+)\}/g, (w, n) => (params && Object.prototype.hasOwnProperty.call(params, n) ? String(params[n]) : w));
  };
  const doc = makeDoc();
  const mk = (extra) => {
    const root = doc.createElement('div'); doc.root = root;
    const calls = { select: [], all: 0, dismiss: [] };
    const panel = doc.createElement('div');
    const bar = R.createTabBar(root, Object.assign({ t, panelId: 'app', panel, onSelect: (id) => calls.select.push(id), onToggleAll: () => { calls.all++; },
      onDismiss: (key) => calls.dismiss.push(key) }, extra));
    return { root, bar, calls, panel };
  };
  const SHOW = ['title', 'aria-label'];
  const dump = (root) => { const parts = []; walk(root, (n) => { if (n instanceof T) parts.push(n.data); else if (n instanceof E) for (const k of SHOW) if (n.attrs.has(k)) parts.push(n.attrs.get(k)); }); return parts.join('\n'); };
  const describe = (root) => ({
    hidden: root.hidden,
    list: root.querySelector('[role="tablist"]') ? root.querySelector('[role="tablist"]').getAttribute('aria-label') : null,
    tabs: root.querySelectorAll('[role="tab"]').__asArray().map((b) => ({ id: b.getAttribute('data-wt-tab'), selected: b.getAttribute('aria-selected'), tabindex: b.getAttribute('tabindex'),
      cls: b.getAttribute('class'), text: text(b), title: b.getAttribute('title'), controls: b.getAttribute('aria-controls'), elementId: b.getAttribute('id'), dot: b.querySelectorAll('.wt-dot').length,
      ro: text(b.querySelector('.wt-ro')) })),
    all: root.querySelector('.wt-all') ? { text: text(root.querySelector('.wt-all')), expanded: root.querySelector('.wt-all').getAttribute('aria-expanded') } : null,
    rows: root.querySelectorAll('.wt-table tbody tr').__asArray().map((tr) => tr.querySelectorAll('td').__asArray().map(text)),
    heads: root.querySelectorAll('.wt-table th').__asArray().map(text),
    sep: root.querySelectorAll('.wt-sep').length,
    note: root.querySelector('.wt-note') ? { text: text(root.querySelector('.wt-note')), role: root.querySelector('.wt-note').getAttribute('role'), code: root.querySelectorAll('.wt-note code').__asArray().map(text) } : null,
    alerts: root.querySelectorAll('.wt-alert').__asArray().map((a) => ({ cls: a.getAttribute('class'), role: a.getAttribute('role'), text: text(a), code: a.querySelectorAll('code').__asArray().map(text),
      close: a.querySelectorAll('button').length })),
  });
  const model = input.model;
  const key = (id, name, init) => new Ev('keydown', Object.assign({ key: name, target: init }, {}));

  // the whole model, in the language of the run
  {
    const m = mk(); m.bar.update(model);
    out.runs.full = describe(m.root); out.runs.fullText = dump(m.root);
    // show all
    m.bar.update(Object.assign({}, model, { showAll: true }));
    out.runs.shown = describe(m.root);
    m.root.querySelector('.wt-all').click();
    out.runs.allCalls = m.calls.all;
    // click on a tab
    m.root.querySelectorAll('[role="tab"]')[0].click();
    out.runs.clicked = { select: m.calls.select.slice(), focused: doc.focused ? doc.focused.getAttribute('data-wt-tab') : null };
    // alerts dismiss
    const closers = m.root.querySelectorAll('[data-wt-dismiss]');
    doc.focused = null;
    closers.forEach((c) => c.click());
    out.runs.dismissed = m.calls.dismiss.slice();
    out.runs.afterDismiss = doc.focused ? doc.focused.getAttribute('data-wt-focus') : null;
    out.runs.usedKeys = [...used].sort();
    m.bar.destroy();
  }
  if (input.lang === 'pl') {
    // keys of the tablist: the target is the focused tab
    const m = mk(); m.bar.update(model);
    out.runs.panel = { role: m.panel.getAttribute('role'), labelled: m.panel.getAttribute('aria-labelledby') };
    m.bar.update({ tabs: [] }); out.runs.panelOff = { role: m.panel.getAttribute('role'), labelled: m.panel.getAttribute('aria-labelledby') };
    m.bar.update(model);
    const list = m.root.querySelector('[role="tablist"]');
    const ids = m.root.querySelectorAll('[role="tab"]').__asArray().map((b) => b.getAttribute('data-wt-tab'));
    const press = (name, from, mods) => {
      const ev = new Ev('keydown', Object.assign({ key: name, target: m.root.querySelector('[data-wt-tab="' + from + '"]') }, mods || {}));
      m.calls.select.length = 0;
      list.dispatchEvent(ev);
      return { selected: m.calls.select.slice(), prevented: ev.defaultPrevented, focused: doc.focused ? doc.focused.getAttribute('data-wt-tab') : null };
    };
    out.runs.ids = ids;
    out.runs.keys = { right: press('ArrowRight', ids[0]), rightFromActive: press('ArrowRight', ids[1]), wrap: press('ArrowRight', ids[ids.length - 1]), left: press('ArrowLeft', ids[0]),
      home: press('Home', ids[2]), end: press('End', ids[0]), ctrl: press('ArrowRight', ids[0], { ctrlKey: true }), other: press('a', ids[0]), meta: press('Home', ids[1], { metaKey: true }) };
    m.bar.destroy();
    // the same model again redraws nothing (a focus on a tab survives the polling)
    const n = mk(); n.bar.update(model);
    const before = n.root.querySelector('.wt-tabs');
    n.bar.update(JSON.parse(JSON.stringify(model)));
    out.runs.sameNode = before === n.root.querySelector('.wt-tabs');
    n.bar.update(Object.assign({}, model, { showAll: true }));
    out.runs.newNode = before !== n.root.querySelector('.wt-tabs');
    // a tab that has the focus keeps it when the bar is drawn again (the harness has no `:focus-within`, so it is told)
    {
      const f = mk(); f.bar.update(model);
      f.root.matches = (sel) => sel === ':focus-within';
      const tab = f.root.querySelector('[data-wt-tab="main"]');
      tab.dispatchEvent(new Ev('focusin', { target: tab }));
      doc.focused = null;
      f.bar.update(Object.assign({}, model, { showAll: true }));
      out.runs.refocused = doc.focused ? doc.focused.getAttribute('data-wt-tab') : null;
      f.root.dispatchEvent(new Ev('focusout', {}));
      doc.focused = null;
      f.bar.update(Object.assign({}, model, { showAll: false }));
      out.runs.notRefocused = doc.focused ? doc.focused.getAttribute('data-wt-tab') : null;
      // the other controls the keyboard can stay on: "show all" and the dismiss button of an alert
      for (const [name, sel] of [['all', '.wt-all'], ['dismiss', '[data-wt-dismiss]']]) {
        const g = mk(); g.bar.update(model);
        g.root.matches = (q) => q === ':focus-within';
        const ctl = g.root.querySelector(sel);
        ctl.dispatchEvent(new Ev('focusin', { target: ctl }));
        doc.focused = null;
        g.bar.update(Object.assign({}, model, { showAll: !model.showAll }));
        out.runs['refocused_' + name] = doc.focused ? doc.focused.getAttribute('data-wt-focus') : null;
        g.bar.destroy();
      }
      f.bar.destroy();
    }
    // an alert that stays the same keeps its element (no new `role="alert"` for a screen reader at every change of the model); a changed one is drawn again
    {
      const a = mk(); a.bar.update(model);
      const first = a.root.querySelectorAll('.wt-alert').__asArray();
      a.bar.update(Object.assign({}, model, { showAll: !model.showAll, note: null }));
      const second = a.root.querySelectorAll('.wt-alert').__asArray();
      const extra = { kind: 'error', title: ['ui.wt.lostTitle', {}], body: null, dismiss: null };
      a.bar.update(Object.assign({}, model, { alerts: model.alerts.slice(0, 2).concat([extra]) }));
      const third = a.root.querySelectorAll('.wt-alert').__asArray();
      a.bar.update(Object.assign({}, model, { alerts: [] }));
      out.runs.alertIdentity = { count: first.length, same: first.length > 0 && first.every((el, i) => el === second[i]) && second.length === first.length,
        keptPrefix: third[0] === first[0] && third[1] === first[1], replacedTail: third.length === 3 && third[2] !== first[2],
        emptied: a.root.querySelectorAll('.wt-alert').length, hostHidden: a.root.querySelector('.wt-alerts').hidden };
      a.bar.destroy();
    }
    // the language changed: the same model is drawn again with the new texts
    n.bar.relabel(); out.runs.relabelKeepsTabs = n.root.querySelectorAll('[role="tab"]').length;
    // empty model hides the bar and draws nothing
    n.bar.update({ tabs: [] }); out.runs.empty = { hidden: n.root.hidden, children: n.root.childNodes.length };
    n.bar.update(model); out.runs.again = { hidden: n.root.hidden, tabs: n.root.querySelectorAll('[role="tab"]').length };
    n.bar.destroy();
    // the active tab is the history one
    const h = mk(); h.bar.update(Object.assign({}, model, { archive: { count: 3, active: true } }));
    out.runs.historyActive = describe(h.root).tabs.map((b) => [b.id, b.selected, b.tabindex]);
    h.bar.destroy();
    // the counter of the History tab: shown with a title and an aria-label for a positive count, hidden for 0 and for no count
    out.runs.histCounter = {};
    for (const [name, archive] of [['three', { count: 3, active: false }], ['zero', { count: 0, active: false }], ['none', { count: null, active: false }]]) {
      const c = mk(); c.bar.update(Object.assign({}, model, { archive }));
      const el = c.root.querySelector('.wt-tab-hist .wt-count');
      out.runs.histCounter[name] = el ? { text: text(el), title: el.getAttribute('title'), aria: el.getAttribute('aria-label') } : null;
      c.bar.destroy();
    }
    // data stays text
    const x = mk();
    x.bar.update({ tabs: [{ id: 'main', name: '<img src=x onerror=1>', sub: '</script><script>alert(1)</script>', active: true, waiting: false, readOnly: false, gone: false },
      { id: 'a"][x="', name: 'quote', sub: '', active: false }, { id: 'ok-id', name: '`code`', sub: '{n}', active: false, waiting: true }],
      hidden: [{ name: '<b>x</b>', lastActivity: 'not a date', reason: 'weird' }], showAll: true, alerts: [{ kind: 'x', title: ['ui.wt.goneTitle', { name: '`<script>`' }], body: null, dismiss: null }] });
    out.runs.hostile = { dump: dump(x.root), tags: (() => { const tags = []; walk(x.root, (n) => { if (n instanceof E && ['script', 'img', 'b'].includes(n.localName)) tags.push(n.localName); }); return tags; })(),
      ids: x.root.querySelectorAll('[role="tab"]').__asArray().map((b) => b.getAttribute('data-wt-tab')), alert: describe(x.root).alerts };
    x.bar.destroy();
    try { R.createTabBar(null, {}); out.runs.noRoot = 'no error'; } catch (e) { out.runs.noRoot = e.constructor.name; }
  }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e) })); });
"""

MODEL = {
    "tabs": [
        {"id": TAB_A, "name": "Skillsync", "sub": "main", "active": False, "waiting": False, "readOnly": False, "gone": False},
        {"id": TAB_B, "name": "ENROLL-6431", "sub": "feature/enroll", "active": True, "waiting": True, "readOnly": True, "gone": False},
        {"id": TAB_C, "name": "ENROLL-6409", "sub": "", "active": False, "waiting": False, "readOnly": True, "gone": True},
    ],
    "archive": {"count": 3, "active": False},
    "hidden": [{"name": "ENROLL-6402", "lastActivity": "2026-11-13T10:00:00+00:00", "reason": "inactive"},
               {"name": "scratch", "lastActivity": None, "reason": "trace"}, {"name": "far", "lastActivity": "2026-11-01T10:00:00+00:00", "reason": "limit"}],
    "showAll": False,
    "lang": "pl",
    "note": ["ui.wt.readOnlyNote", {"name": "ENROLL-6431"}],
    "alerts": [
        {"kind": "error", "title": ["ui.wt.goneTitle", {"name": "ENROLL-6409"}], "body": ["ui.wt.goneBody", {}], "dismiss": None},
        {"kind": "warn", "title": ["ui.wt.staleTitle", {}], "body": ["ui.wt.staleBody", {}], "dismiss": None},
        {"kind": "info", "title": ["ui.wt.noteTitle", {}], "body": ["ui.wt.noteBody", {}], "dismiss": "note"},
        {"kind": "error", "title": ["ui.wt.lostTitle", {}], "body": ["ui.wt.lostBody", {}], "dismiss": None},
        {"kind": "info", "title": ["ui.wt.otherDone", {"word": "CP", "name": "ENROLL-6431"}], "body": None, "dismiss": "outcome"},
        {"kind": "warn", "title": ["ui.wt.otherRefused", {"word": "CP", "name": "ENROLL-6431"}], "body": ["ui.wt.otherReason", {"reason": "sha changed"}], "dismiss": "outcome"},
        {"kind": "info", "title": ["ui.wt.consentScope", {"name": "ENROLL-6431", "sha": "4f2a9c1d"}], "body": None, "dismiss": None},
    ],
}


def bar_run(lang: str) -> dict:
    model = json.loads(json.dumps(MODEL))
    model["lang"] = lang
    return run_node(HEAD + BAR_TAIL, {"lang": lang, "model": model, "catalogs": {"pl": ui_catalog("pl"), "en": ui_catalog("en")}})


@SKIP
class TabBarRenderer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pl = bar_run("pl")["runs"]
        cls.en = bar_run("en")["runs"]

    def test_the_tablist_has_roles_selection_and_a_roving_tabindex(self):
        full = self.pl["full"]
        self.assertEqual(full["list"], "Zakładki projektu")
        self.assertEqual([t["id"] for t in full["tabs"]], [TAB_A, TAB_B, TAB_C, "history"])
        self.assertEqual([t["selected"] for t in full["tabs"]], ["false", "true", "false", "false"])
        self.assertEqual([t["tabindex"] for t in full["tabs"]], ["-1", "0", "-1", "-1"], "only the selected tab is in the tab order")
        self.assertEqual({t["controls"] for t in full["tabs"]}, {"app"})
        self.assertEqual(full["tabs"][1]["elementId"], f"wt-tab-{TAB_B}")
        self.assertEqual(full["sep"], 1, "the history tab stands apart at the end")
        self.assertIn("wt-tab-hist", full["tabs"][3]["cls"])

    def test_the_page_of_the_tab_is_the_tab_panel_only_while_the_bar_is_shown(self):
        self.assertEqual(self.pl["panel"], {"role": "tabpanel", "labelled": f"wt-tab-{TAB_B}"})
        self.assertEqual(self.pl["panelOff"], {"role": None, "labelled": None})

    def test_a_tab_that_waits_shows_the_indicator_and_says_it_in_words(self):
        tabs = {t["id"]: t for t in self.pl["full"]["tabs"]}
        self.assertEqual(tabs[TAB_B]["dot"], 1)
        self.assertIn("czeka na Ciebie", tabs[TAB_B]["text"], "the dot is not the only carrier of the information")
        self.assertEqual(tabs[TAB_B]["title"].count("`"), 0)
        self.assertEqual(tabs[TAB_A]["dot"], 0)

    def test_a_tab_without_a_token_is_marked_read_only_with_a_hint(self):
        tabs = {t["id"]: t for t in self.pl["full"]["tabs"]}
        self.assertEqual(tabs[TAB_B]["ro"], "tylko odczyt", "the badge is on the tab that is shown")
        self.assertIsNone(tabs[TAB_C]["ro"])
        self.assertEqual(tabs[TAB_A]["ro"], None)
        self.assertIn("skillsync dashboard url", tabs[TAB_B]["title"])

    def test_a_removed_tab_is_marked(self):
        tab = next(t for t in self.pl["full"]["tabs"] if t["id"] == TAB_C)
        self.assertIn("is-gone", tab["cls"])
        self.assertIn("usunięty", tab["text"])

    def test_show_all_lists_the_worktrees_without_a_tab(self):
        full, shown = self.pl["full"], self.pl["shown"]
        self.assertEqual(full["all"], {"text": "pokaż wszystkie (+3)", "expanded": "false"})
        self.assertEqual(full["rows"], [])
        self.assertEqual(shown["all"], {"text": "ukryj nieaktywne", "expanded": "true"})
        self.assertEqual(shown["heads"], ["worktree", "ostatnia aktywność", "powód ukrycia"])
        self.assertEqual([row[0] for row in shown["rows"]], ["ENROLL-6402", "scratch", "far"])
        self.assertEqual([row[2] for row in shown["rows"]], ["nieaktywny", "bez śladu skillsync", "poza limitem widocznych zakładek"])
        self.assertEqual(shown["rows"][1][1], "brak", "no activity at all")
        self.assertEqual(self.pl["allCalls"], 1)

    def test_clicking_a_tab_selects_it_and_keeps_the_focus_on_it(self):
        self.assertEqual(self.pl["clicked"], {"select": [TAB_A], "focused": TAB_A})

    def test_an_unchanged_alert_keeps_its_element_and_a_changed_tail_is_drawn_again(self):
        got = self.pl["alertIdentity"]
        self.assertEqual(got["count"], 7)
        self.assertTrue(got["same"], "the same alerts: the same elements, no new role=alert at every change of the model")
        self.assertTrue(got["keptPrefix"], "the alerts before the first change stay")
        self.assertTrue(got["replacedTail"])
        self.assertEqual(got["emptied"], 0)
        self.assertTrue(got["hostHidden"])

    def test_the_alerts_have_a_role_a_kind_and_a_dismiss_only_when_asked(self):
        alerts = self.pl["full"]["alerts"]
        self.assertEqual([a["role"] for a in alerts], ["alert", "status", "status", "alert", "status", "status", "status"])
        self.assertTrue(all(f"wt-alert-{kind}" in a["cls"] for a, kind in zip(alerts, ("error", "warn", "info", "error", "info", "warn", "info"))))
        self.assertEqual([a["close"] for a in alerts], [0, 0, 1, 0, 1, 1, 0])
        self.assertIn("Worktree ENROLL-6409 został usunięty", alerts[0]["text"])
        self.assertEqual(alerts[1]["code"], ["skillsync dashboard url"], "the command is a code fragment, the backticks are not drawn")
        self.assertNotIn("`", alerts[1]["text"])
        self.assertEqual(self.pl["dismissed"], ["note", "outcome", "outcome"])

    def test_the_keys_of_the_tablist(self):
        keys, ids = self.pl["keys"], self.pl["ids"]
        self.assertEqual(ids, [TAB_A, TAB_B, TAB_C, "history"])
        self.assertEqual(keys["right"]["selected"], [TAB_B])
        self.assertEqual(keys["rightFromActive"]["selected"], [TAB_C], "the arrow goes from the focused tab")
        self.assertEqual(keys["wrap"]["selected"], [TAB_A], "past the last tab the arrow wraps")
        self.assertEqual(keys["left"]["selected"], ["history"], "before the first tab the arrow wraps")
        self.assertEqual(keys["home"]["selected"], [TAB_A])
        self.assertEqual(keys["end"]["selected"], ["history"])
        for name in ("right", "rightFromActive", "wrap", "left", "home", "end"):
            self.assertTrue(keys[name]["prevented"], name)
            self.assertEqual(keys[name]["focused"], keys[name]["selected"][0], f"{name}: the focus follows the selection")
        for name in ("ctrl", "other", "meta"):
            self.assertEqual(keys[name]["selected"], [], name)
            self.assertFalse(keys[name]["prevented"], name)

    def test_the_same_model_draws_nothing_again_and_a_new_one_does(self):
        self.assertTrue(self.pl["sameNode"], "polling must not rebuild the bar (the focus of a keyboard user would be lost)")
        self.assertTrue(self.pl["newNode"])
        self.assertEqual(self.pl["relabelKeepsTabs"], 4)

    def test_a_focused_tab_keeps_the_focus_when_the_bar_is_drawn_again_and_a_blurred_one_does_not_take_it(self):
        self.assertEqual(self.pl["refocused"], TAB_A)
        self.assertIsNone(self.pl["notRefocused"])

    def test_the_all_button_and_the_dismiss_button_keep_the_focus_too(self):
        self.assertEqual(self.pl["refocused_all"], "all")
        self.assertEqual(self.pl["refocused_dismiss"], "dismiss:note")

    def test_after_a_dismiss_the_focus_goes_to_the_selected_tab(self):
        self.assertEqual(self.pl["afterDismiss"], f"tab:{TAB_B}")

    def test_the_history_counter_has_a_title_and_an_aria_label_and_hides_at_zero(self):
        counter = self.pl["histCounter"]
        self.assertEqual(counter["three"], {"text": "3", "title": "Zakończone plany: 3", "aria": "Zakończone plany: 3"})
        self.assertIsNone(counter["zero"])
        self.assertIsNone(counter["none"])
        note = self.pl["full"]["note"]
        self.assertEqual(note["role"], "status")
        self.assertIn("jest tylko do odczytu", note["text"])
        self.assertEqual(note["code"], ["skillsync dashboard url"], "visible text, not only a title")
        catalog = {lang: json.loads((ROOT / "skillsync_workflow" / "data" / "i18n" / f"{lang}.json").read_text(encoding="utf-8")) for lang in ("pl", "en")}
        self.assertEqual(catalog["en"]["ui.wt.historyCount"], "Finished plans: {n}")
        self.assertEqual(catalog["pl"]["ui.wt.historyCount"], "Zakończone plany: {n}")
        self.assertTrue(catalog["en"]["ui.wt.historyCount"].isascii(), "English has no Polish characters")

    def test_an_empty_model_hides_the_bar(self):
        self.assertEqual(self.pl["empty"], {"hidden": True, "children": 0})
        self.assertEqual(self.pl["again"], {"hidden": False, "tabs": 4})

    def test_the_history_tab_can_be_the_selected_one(self):
        self.assertEqual(self.pl["historyActive"], [[TAB_A, "false", "-1"], [TAB_B, "false", "-1"], [TAB_C, "false", "-1"], ["history", "true", "0"]])

    def test_data_of_the_project_stays_text_and_a_bad_id_is_dropped(self):
        got = self.pl["hostile"]
        self.assertEqual(got["tags"], [])
        self.assertIn("<img src=x onerror=1>", got["dump"])
        self.assertIn("</script><script>alert(1)</script>", got["dump"])
        self.assertIn("<b>x</b>", got["dump"])
        self.assertEqual(got["ids"], ["main", "ok-id"], "an id outside the pattern never reaches the DOM")
        self.assertEqual(got["alert"][0]["text"], "Worktree <script> został usunięty", "an unknown kind is an info alert; the backticks of a parameter make a code span only")
        self.assertIn("wt-alert-info", got["alert"][0]["cls"])
        self.assertEqual(self.pl["noRoot"], "TypeError")

    def test_english_run_has_no_polish_character_and_draws_every_wt_key(self):
        text = self.en["fullText"]
        self.assertIsNone(POLISH.search(text), text)
        self.assertIn("Project tabs", text)
        self.assertIn("show all (+3)", self.en["full"]["all"]["text"])
        self.assertEqual(self.en["shown"]["heads"], ["worktree", "last activity", "why hidden"])
        keys = {key for key in ui_catalog("en") if key.startswith("ui.wt.")}
        self.assertGreater(len(keys), 20)
        for key in sorted(keys):   # also the keys that the runs below do not draw
            self.assertIsNone(POLISH.search(ui_catalog("en")[key]), key)
        # `ui.wt.why.other` and `ui.wt.never` are drawn by a model with an odd reason / no date (the hostile model), the rest by the full model
        missing = sorted(keys - set(self.en["usedKeys"]) - {"ui.wt.why.other", "ui.wt.unknownTab"})
        self.assertEqual(missing, [], "keys of the bar that no run draws (add them to the model of the test)")

    def test_both_catalogs_have_the_same_wt_keys_with_the_same_parameters(self):
        pl, en = ui_catalog("pl"), ui_catalog("en")
        keys = {key for key in pl if key.startswith("ui.wt.")}
        self.assertEqual(keys, {key for key in en if key.startswith("ui.wt.")})
        for key in keys:
            self.assertEqual(sorted(re.findall(r"\{\w+\}", pl[key])), sorted(re.findall(r"\{\w+\}", en[key])), key)


# ------------------------------------------------------------------------------------------------------------------------------ the page adapter
APP_HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
const sc = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));   // a file, not an argument: a long scenario exceeds the command line limit of Windows (32767 characters)
const storage = Object.assign({}, sc.storage);
const log = { themes: [], archive: [], plans: [], fetch: [], replaced: [], pushed: [], streams: [], models: [], board: [], closed: 0, attrs: [], hashes: [], creates: 0, vars: [], pending: {}, initials: [], observed: 0, cleared: 0 };
const pending = {};
const location = { hash: sc.hash || '', pathname: '/', search: sc.search || '', protocol: 'http:', hostname: '127.0.0.1' };
const apply = (url) => { const m = /^([^?#]*)(\?[^#]*)?(#.*)?$/.exec(url); location.pathname = m[1] || '/'; location.search = m[2] || ''; location.hash = m[3] || ''; };
const routes = Object.assign({}, sc.routes);
const holds = {};
const timers = [];
const timeouts = [];
const clock = { now: Date.now() };
const respond = (spec) => ({ ok: spec.status >= 200 && spec.status < 300, status: spec.status, json: () => (spec.badJson ? Promise.reject(new Error('bad json')) : Promise.resolve(spec.body)) });
const roots = { app: { ownerDocument: {}, id: 'app', hidden: false, setAttribute: (k, v) => log.attrs.push([k, v]), removeAttribute: (k) => log.attrs.push([k, null]) },
  whistory: { ownerDocument: {}, id: 'whistory', hidden: true, setAttribute: (k, v) => log.attrs.push(['history:' + k, v]), removeAttribute: (k) => log.attrs.push(['history:' + k, null]) } };
const listeners_doc = {};
const doc = { documentElement: { dataset: {}, lang: '', style: { setProperty: (k, v) => log.vars.push([k, v]) } }, visibilityState: 'visible', title: '', addEventListener(name, fn) { listeners_doc[name] = fn; },
  getElementById: (id) => (id === 'wtabs' ? { ownerDocument: {}, id, hidden: false, firstElementChild: { firstElementChild: { offsetHeight: 40 } } } : roots[id] || { ownerDocument: {}, id, setAttribute() {}, removeAttribute() {} }) };
class FakeStream {
  constructor(url) { this.url = url; this.readyState = 1; this.handlers = {}; log.streams.push(url); streams.push(this); }
  addEventListener(name, fn) { this.handlers[name] = fn; }
  close() { this.readyState = 2; log.closed++; }
}
const streams = [];
const ctx = {
  location, history: { replaceState: (a, b, url) => { log.replaced.push(url); apply(url); }, pushState: (a, b, url) => { log.pushed.push(url); apply(url); } },
  localStorage: { getItem: (k) => (k in storage ? storage[k] : null), setItem: (k, v) => { storage[k] = String(v); }, removeItem: (k) => { delete storage[k]; } },
  Date: class extends Date { constructor(...a) { super(...(a.length ? a : [clock.now])); } static now() { return clock.now; } },
  document: doc, matchMedia: () => ({ matches: false, addEventListener() {} }), console, URLSearchParams, EventSource: FakeStream,
  setInterval: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearInterval() {}, setTimeout: (fn, ms) => { timeouts.push({ fn, ms }); return timeouts.length; }, clearTimeout: () => { log.cleared++; },
  ResizeObserver: class { constructor(fn) { this.fn = fn; } observe() { log.observed++; } disconnect() {} },
  fetch: (url, opts) => {
    const path = url.split('?')[0];
    log.fetch.push({ url, method: (opts && opts.method) || 'GET', headers: (opts && opts.headers) || null, body: opts && opts.body ? JSON.parse(opts.body) : null, priority: (opts && opts.priority) || null });
    const spec = routes[path];
    if (!spec) return Promise.resolve(respond({ status: 404, body: { error: 'no route' } }));
    if (spec.throw) return Promise.reject(new TypeError('network down'));
    if (spec.hold) return new Promise((resolve) => { (holds[spec.hold] = holds[spec.hold] || []).push((body, status) => resolve(respond({ status: status || 200, body }))); });
    return Promise.resolve(respond(spec));
  },
};
ctx.window = ctx;
const listeners = {};
ctx.addEventListener = (name, fn) => { listeners[name] = fn; };
const makeStub = (n) => new Proxy({}, { get: (_, name) => {
  if (name === 't') return (key, params) => key + (params ? JSON.stringify(params) : '');
  if (name === 'lang') return () => 'pl';
  return (...args) => { if (name === 'update') log.plans.push(args[0] && args[0].plans ? args[0].plans.map((p) => p.path) : null); log.board.push([String(name), ...args.map((a) => (a && typeof a === 'object' ? (name === 'update' ? { plan: a.plan && a.plan.path, who: a.who } : a) : a))]); };
} });
ctx.DashboardRenderer = {
  create: (el, deps) => { ctx.deps = deps; log.creates++; log.initials.push(deps.initial || null); log.board.push(['create', log.creates]); return makeStub(log.creates); },
};
if (!sc.noArchive) ctx.DashboardRenderer.createArchiveView = (el, deps) => { ctx.archDeps = deps; return { update: (m) => { log.archive.push(JSON.parse(JSON.stringify(m))); }, relabel() {} }; };
if (!sc.noBar) ctx.DashboardRenderer.createTabBar = (el, deps) => { ctx.barDeps = deps; return { update: (m) => { log.models.push(JSON.parse(JSON.stringify(m))); }, relabel() {} }; };
vm.createContext(ctx);
const settle = async () => { for (let i = 0; i < 12; i++) await new Promise((r) => setImmediate(r)); };
(async () => {
  vm.runInContext(src, ctx);
  await settle();
  const results = [];
  for (const step of sc.steps || []) {
    if (step.op === 'select') ctx.barDeps.onSelect(step.id);
    else if (step.op === 'route') routes[step.path] = step.value;
    else if (step.op === 'poll') { for (const timer of timers) if (timer.ms === 5000) { timer.fn(); } }
    else if (step.op === 'hash') { location.hash = step.value; listeners.hashchange && listeners.hashchange(); }
    else if (step.op === 'popstate') { apply(step.url); listeners.popstate && listeners.popstate(); }
    else if (step.op === 'approve') results.push(await ctx.deps.actions.approve({ kind: 'cp', path: 'p', sha: 's', word: 'CP' }));
    else if (step.op === 'feedback') results.push(await ctx.deps.actions.feedbackApprove({ draft: 'F1', sha: 's' }));
    else if (step.op === 'doc') results.push(await ctx.deps.actions.loadDoc('docs/x.md'));
    else if (step.op === 'tests') results.push(await ctx.deps.actions.loadTests({ run: 'r', step: 's' }));
    else if (step.op === 'selectPlan') ctx.deps.actions.selectPlan(step.path);
    else if (step.op === 'storage') results.push(ctx.deps.storage.get(step.key));
    else if (step.op === 'navigate') ctx.deps.onNavigate(step.hash);
    else if (step.op === 'title') ctx.deps.onTitle(step.value);
    else if (step.op === 'release') holds[step.name].shift()(step.body, step.status);
    else if (step.op === 'timeouts') { for (const timer of timeouts.splice(0)) if (timer.ms === 3000) timer.fn(); }
    else if (step.op === 'startDoc' || step.op === 'startTests' || step.op === 'startApprove') {
      const call = step.op === 'startDoc' ? ctx.deps.actions.loadDoc('docs/x.md') : step.op === 'startTests' ? ctx.deps.actions.loadTests({ run: 'r', step: 'one', group: 'g', attempt: 1 })
        : ctx.deps.actions.approve({ kind: 'cp', path: 'p', sha: 's', word: 'CP' });
      call.then((v) => { log.pending[step.name] = { ok: true, value: v }; }, (e) => { log.pending[step.name] = { error: String(e && e.message || e) }; });
    }
    else if (step.op === 'bar') ctx.barDeps[step.name](...(step.args || []));
    else if (step.op === 'arch') ctx.archDeps[step.name](...(step.args || []));
    else if (step.op === 'key') { const target = step.inInput ? { closest: () => ({}) } : { closest: () => null }; listeners_doc.keydown && listeners_doc.keydown({ key: step.key, ctrlKey: Boolean(step.ctrlKey), target }); log.themes = doc.documentElement.dataset.theme ? [doc.documentElement.dataset.theme] : []; }
    else if (step.op === 'tick') { for (const timer of timers) if (timer.ms === step.ms) timer.fn(); }
    else if (step.op === 'visibility') doc.visibilityState = step.value;
    else if (step.op === 'visibilitychange') listeners_doc.visibilitychange && listeners_doc.visibilitychange();
    else if (step.op === 'advance') clock.now += step.ms;
    else if (step.op === 'streamError') { const s = streams[streams.length - 1]; s.readyState = 2; s.onerror && s.onerror(); }
    else if (step.op === 'timeouts5') { for (const timer of timeouts.splice(0)) if (timer.ms === 5000) timer.fn(); }
    else if (step.op === 'mount') { const el = {}; ctx.archDeps.mountHtml(el, step.html); results.push(el.innerHTML); }
    else if (step.op === 'stream') { const s = streams[streams.length - 1]; s.handlers[step.name] && s.handlers[step.name](); }
    await settle();
  }
  log.hashes.push(location.hash);
  log.panels = { app: roots.app.hidden, history: roots.whistory.hidden };
  console.log(JSON.stringify({ storage, log, results, hasToken: ctx.deps.hasApproveToken(), title: doc.title, location: { search: location.search, hash: location.hash } }));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def tabs_body(*tabs, current=None, **extra):
    names = {TAB_A: ("Skillsync", "main"), TAB_B: ("ENROLL-6431", "feature/enroll"), TAB_C: ("ENROLL-6409", "feature/old")}
    rows = [{"id": tab, "name": names[tab][0], "path": f"/work/{names[tab][0]}", "kind": "main" if tab == TAB_A else "worktree", "branch": names[tab][1], "head": "abc", "current": tab == (current or tabs[0])}
            for tab in tabs]
    return {"status": 200, "body": {"tabs": rows, "current": current or tabs[0], **extra}}


def status_body(**extra):
    return {"status": 200, "body": {"plans": [{"path": "docs/plans/a.md"}, {"path": "docs/plans/b.md"}], "lang": "pl", **extra}}


def run_page(**scenario) -> dict:
    base = {"hash": "", "search": "", "storage": {}, "routes": {"/api/status": status_body()}, "steps": []}
    with tempfile.TemporaryDirectory(prefix="tabs-page-") as tmp:
        harness = Path(tmp) / "harness.js"
        harness.write_text(APP_HARNESS, encoding="utf-8", newline="\n")
        page = Path(tmp) / "app.js"
        page.write_text(APP.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        scenario_file = Path(tmp) / "scenario.json"
        scenario_file.write_text(json.dumps({**base, **scenario}), encoding="utf-8", newline="\n")
        done = subprocess.run([NODE, str(harness), str(page), str(scenario_file)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    # a token is never in an address, a stream, the title or what the bar is told (it travels in a header only)
    for secret in (HEX, HEX[::-1], BARE):
        for call in out["log"]["fetch"]:
            assert secret not in call["url"], call["url"]
        assert not any(secret in url for url in out["log"]["streams"] + out["log"]["pushed"] + out["log"]["replaced"])
        assert secret not in out["title"] and secret not in json.dumps(out["log"]["models"]) and secret not in json.dumps(out["log"]["board"]) and secret not in json.dumps(out["log"]["archive"]), secret
    return out


def urls(out, prefix):
    """Requests of the page; the background read of the History counter (priority low) is not one of them, see `background`."""
    return [call["url"] for call in out["log"]["fetch"] if call["url"].startswith(prefix) and call["priority"] != "low"]


def background(out):
    return [call["url"] for call in out["log"]["fetch"] if call["priority"] == "low"]


def last_model(out):
    return out["log"]["models"][-1]


@SKIP
class TabsPageAdapter(unittest.TestCase):
    # ---------------------------------------------------------------------------------------------------------------- a server with one tab
    def test_a_server_with_one_tab_is_the_page_of_today(self):
        routes = {"/api/tabs": tabs_body(TAB_A), "/api/status": status_body(), "/api/approve": {"status": 200, "body": {"word": "CP", "next": "ok"}}}
        routes.update({"/api/doc": status_body(), "/api/tests": status_body()})
        out = run_page(routes=routes, storage={"dashboard:approveToken": BARE, "dashboard:plan": "docs/plans/b.md"}, search="?tab=" + TAB_B, steps=[{"op": "approve"}, {"op": "doc"}, {"op": "tests"}])
        self.assertEqual(urls(out, "/api/status"), ["/api/status?plan=docs%2Fplans%2Fb.md"], "no tab parameter, the plan of the single key")
        self.assertEqual([s for s in out["log"]["streams"]], ["/events"])
        approve = next(call for call in out["log"]["fetch"] if call["url"] == "/api/approve")
        self.assertNotIn("tab", approve["body"])
        self.assertEqual(approve["headers"]["X-Approve-Token"], BARE)
        self.assertTrue(all("tab=" not in url for url in urls(out, "/api/doc") + urls(out, "/api/tests")))
        self.assertEqual(last_model(out), {"tabs": []}, "no bar")
        self.assertEqual(out["log"]["pushed"], [])

    def test_a_server_without_the_tabs_route_is_the_page_of_today_and_is_not_asked_again(self):
        out = run_page(routes={"/api/status": status_body()}, steps=[{"op": "poll"}, {"op": "poll"}])
        self.assertEqual(len(urls(out, "/api/tabs")), 1)
        self.assertEqual(urls(out, "/api/status"), ["/api/status"])
        out = run_page(routes={"/api/tabs": {"status": 200, "body": {"plans": []}}, "/api/status": status_body()}, steps=[{"op": "poll"}])
        self.assertEqual(len(urls(out, "/api/tabs")), 1, "another shape is a server without tabs")

    # ---------------------------------------------------------------------------------------------------------------- a server with tabs
    def test_every_request_carries_the_tab_and_the_stream_is_one_for_that_tab(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body(), "/api/doc": status_body(), "/api/tests": status_body(),
                  "/api/approve": {"status": 200, "body": {"word": "CP", "next": "ok"}}, "/api/feedback-approve": {"status": 200, "body": {"ok": True}}}
        out = run_page(routes=routes, search="?tab=" + TAB_B, hash="#zgoda=" + PAIR_B,
                       steps=[{"op": "approve"}, {"op": "feedback"}, {"op": "doc"}, {"op": "tests"}])
        self.assertEqual(urls(out, "/api/status"), ["/api/status?tab=" + TAB_B])
        self.assertEqual(out["log"]["streams"], ["/events?tab=" + TAB_B])
        self.assertEqual(urls(out, "/api/doc"), ["/api/doc?path=docs%2Fx.md&tab=" + TAB_B])
        self.assertTrue(urls(out, "/api/tests")[0].endswith("&tab=" + TAB_B))
        approve = next(call for call in out["log"]["fetch"] if call["url"] == "/api/approve")
        self.assertEqual(approve["body"]["tab"], TAB_B)
        self.assertEqual(approve["headers"]["X-Approve-Token"], PAIR_B, "the pair, as the server wants it on a server with several tabs")
        feedback = next(call for call in out["log"]["fetch"] if call["url"] == "/api/feedback-approve")
        self.assertEqual(feedback["body"]["tab"], TAB_B)
        self.assertEqual(feedback["headers"]["X-Approve-Token"], PAIR_B)

    def test_without_a_tab_in_the_address_the_default_tab_of_the_server_is_shown(self):
        out = run_page(routes={"/api/tabs": tabs_body(TAB_B, TAB_A, current=TAB_B), "/api/status": status_body()})
        model = last_model(out)
        self.assertEqual([(tab["id"], tab["active"]) for tab in model["tabs"]], [(TAB_B, True), (TAB_A, False)])
        self.assertEqual(urls(out, "/api/status"), ["/api/status?tab=" + TAB_B])

    def test_the_bar_model_names_the_tabs_and_the_branches(self):
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()})
        model = last_model(out)
        self.assertEqual([(tab["name"], tab["sub"]) for tab in model["tabs"]], [("Skillsync", "main"), ("ENROLL-6431", "feature/enroll")])
        self.assertEqual(model["archive"], {"count": None, "active": False}, "the History tab is there, without a count until it was opened")
        self.assertIsNone(model["hidden"])
        self.assertEqual(out["title"], "", "no title yet: the renderer has not named the page")

    # ---------------------------------------------------------------------------------------------------------------- the History counter
    @staticmethod
    def with_history(body, *counts):
        for row, count in zip(body["body"]["tabs"], counts):
            if count is not None:
                row["history"] = count
        return body

    def test_the_counter_is_the_count_of_the_list_not_the_sum_of_the_hints(self):
        # three tabs with the same three plans: the hints sum to 9, the list has 3 rows
        routes = {"/api/tabs": self.with_history(tabs_body(TAB_A, TAB_B, TAB_C), 3, 3, 3), "/api/status": status_body(), "/api/history": {"status": 200, "body": {"items": [], "tabs": [], "count": 3}}}
        out = run_page(routes=routes)
        self.assertEqual(last_model(out)["archive"], {"count": 3, "active": False})
        self.assertEqual(background(out), ["/api/history"], "one read, no tab, no token in the address")
        self.assertEqual(urls(out, "/api/history"), [], "the list itself is not read until the History is opened")

    def test_until_the_list_answers_or_when_it_fails_the_bar_shows_the_largest_hint(self):
        hinted = self.with_history(tabs_body(TAB_A, TAB_B, TAB_C), 2, 5, 3)
        for answer in ({"status": 500, "body": {}}, {"status": 200, "body": {"count": "x"}}):
            out = run_page(routes={"/api/tabs": hinted, "/api/status": status_body(), "/api/history": answer})
            self.assertEqual(last_model(out)["archive"]["count"], 5, "max, never the sum (10)")
        out = run_page(routes={"/api/tabs": self.with_history(tabs_body(TAB_A, TAB_B), None, None), "/api/status": status_body(), "/api/history": {"status": 500, "body": {}}})
        self.assertIsNone(last_model(out)["archive"]["count"])

    def test_the_background_read_is_throttled_and_a_one_tab_server_makes_none(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body(), "/api/history": {"status": 200, "body": {"count": 2}}}
        out = run_page(routes=routes, steps=[{"op": "poll"}, {"op": "poll"}, {"op": "poll"}])
        self.assertEqual(background(out), ["/api/history"], "not more often than every 60 s")
        later = run_page(routes=routes, steps=[{"op": "advance", "ms": 20000}, {"op": "poll"}, {"op": "advance", "ms": 30000}, {"op": "poll"}])
        self.assertEqual(background(later), ["/api/history"], "50 s after the first read: still one")
        later = run_page(routes=routes, steps=[{"op": "advance", "ms": 61000}, {"op": "poll"}, {"op": "poll"}])
        self.assertEqual(background(later), ["/api/history", "/api/history"], "after 60 s one more read, not two")
        one = run_page(routes={"/api/tabs": tabs_body(TAB_A), "/api/status": status_body(), "/api/history": {"status": 200, "body": {"count": 2}}})
        self.assertEqual(background(one), [])

    def test_a_hidden_page_makes_no_background_read_and_a_visible_one_catches_up(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body(), "/api/history": {"status": 200, "body": {"count": 2}}}
        out = run_page(routes=routes, steps=[{"op": "visibility", "value": "hidden"}, {"op": "advance", "ms": 120000}, {"op": "poll"}, {"op": "poll"}])
        self.assertEqual(background(out), ["/api/history"], "a hidden page does not read in the background")
        self.assertEqual(last_model(out)["archive"]["count"], 2)
        out = run_page(routes=routes, steps=[{"op": "visibility", "value": "hidden"}, {"op": "advance", "ms": 120000}, {"op": "poll"},
                                             {"op": "visibility", "value": "visible"}, {"op": "visibilitychange"}])
        self.assertEqual(background(out), ["/api/history", "/api/history"], "the page is visible again: one read, after the interval")
        out = run_page(routes=routes, steps=[{"op": "visibility", "value": "hidden"}, {"op": "visibility", "value": "visible"}, {"op": "visibilitychange"}])
        self.assertEqual(background(out), ["/api/history"], "visible again before the interval: no read")

    def test_a_tab_without_a_token_says_it_in_text_under_the_bar(self):
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()})
        self.assertEqual(last_model(out)["note"], ["ui.wt.readOnlyNote", {"name": "Skillsync"}])
        with_token = run_page(routes={"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}, hash="#zgoda=" + PAIR_A, search="?tab=" + TAB_A)
        self.assertIsNone(last_model(with_token)["note"])

    def test_a_zero_hint_hides_the_counter_and_a_missing_field_is_as_before(self):
        zero = run_page(routes={"/api/tabs": self.with_history(tabs_body(TAB_A, TAB_B), 0, 0), "/api/status": status_body()})
        self.assertEqual(last_model(zero)["archive"]["count"], 0, "0 goes to the renderer, which does not draw it")
        absent = run_page(routes={"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()})
        self.assertIsNone(last_model(absent)["archive"]["count"])
        junk = run_page(routes={"/api/tabs": self.with_history(tabs_body(TAB_A, TAB_B), "5", -1), "/api/status": status_body()})
        self.assertIsNone(last_model(junk)["archive"]["count"], "a hint that is not a non-negative integer is no hint")

    def test_a_hint_missing_in_one_tab_counts_the_others(self):
        out = run_page(routes={"/api/tabs": self.with_history(tabs_body(TAB_A, TAB_B), None, 4), "/api/status": status_body()})
        self.assertEqual(last_model(out)["archive"]["count"], 4)

    def test_the_counter_falls_back_to_the_largest_hint_after_a_refresh_of_the_tabs(self):
        routes = {"/api/tabs": self.with_history(tabs_body(TAB_A, TAB_B), 1, 1), "/api/status": status_body()}
        out = run_page(routes=routes, steps=[{"op": "route", "path": "/api/tabs", "value": self.with_history(tabs_body(TAB_A, TAB_B), 3, 1)}, {"op": "poll"}])
        counts = [m["archive"]["count"] for m in out["log"]["models"]]
        self.assertEqual(counts[0], 1)
        self.assertEqual(counts[-1], 3)

    def test_the_counter_is_in_no_address_token_or_request(self):
        routes = {"/api/tabs": self.with_history(tabs_body(TAB_A, TAB_B), 11, 22), "/api/status": status_body()}
        out = run_page(routes=routes, hash="#zgoda=" + PAIR_B, search="?tab=" + TAB_B)
        blob = json.dumps([out["log"]["fetch"], out["log"]["streams"], out["log"]["pushed"], out["log"]["replaced"], out["storage"], out["location"]])
        self.assertNotIn("history=", blob)
        self.assertNotIn("count=", blob)

    # ---------------------------------------------------------------------------------------------------------------- tokens
    def test_a_fragment_with_a_dotted_pair_is_stored_under_the_tab_and_the_bar_is_cleaned(self):
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}, hash="#zgoda=" + PAIR_B, search="?tab=" + TAB_B)
        self.assertEqual(out["storage"], {f"dashboard:approveToken:{TAB_B}": PAIR_B})
        self.assertEqual(out["log"]["replaced"], ["/?tab=" + TAB_B])
        self.assertEqual(out["location"]["hash"], "")
        self.assertTrue(out["hasToken"])

    def test_one_fragment_can_carry_the_pairs_of_several_tabs(self):
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}, hash=f"#zgoda={PAIR_A},{PAIR_B}", search="?tab=" + TAB_A)
        self.assertEqual(out["storage"], {f"dashboard:approveToken:{TAB_A}": PAIR_A, f"dashboard:approveToken:{TAB_B}": PAIR_B})
        self.assertEqual([tab["readOnly"] for tab in last_model(out)["tabs"]], [False, False])

    def test_bad_fragments_are_not_tokens(self):
        for fragment in (f"#zgoda={PAIR_A},short", "#zgoda=" + ",".join([PAIR_A] * 17), f"#zgoda={PAIR_A};{PAIR_B}", f"#zgoda={PAIR_A}&x=1", "#zgoda=" + "x" * 200,
                         f"#zgoda={TAB_A}.", "#zgoda=" + "." * 20 + ",,", "#zgoda=" + HEX[:15], "#zgoda=" + "a b" * 10):
            with self.subTest(fragment=fragment[:60]):
                out = run_page(routes={"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}, hash=fragment)
                self.assertEqual(out["storage"], {})
                self.assertEqual(out["log"]["replaced"], ["/"], "a rejected token does not stay in the address bar either")
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}, hash="#docs:docs/plans/x.md")
        self.assertEqual(out["log"]["replaced"], [], "another fragment is left alone")

    def test_a_bare_token_is_still_accepted_and_one_with_a_dot_inside_is_taken_whole(self):
        dotted = "chosen.by.the.caller.0123456789"
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A), "/api/status": status_body()}, hash="#zgoda=" + dotted)
        self.assertEqual(out["storage"], {"dashboard:approveToken": dotted}, "its head is not a tab id, so it is a bare token")

    def test_a_tab_without_a_token_is_read_only_and_sends_no_header(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body(), "/api/approve": {"status": 200, "body": {"word": "CP"}}}
        out = run_page(routes=routes, search="?tab=" + TAB_B, storage={f"dashboard:approveToken:{TAB_A}": PAIR_A}, steps=[{"op": "approve"}])
        approve = next(call for call in out["log"]["fetch"] if call["url"] == "/api/approve")
        self.assertNotIn("X-Approve-Token", approve["headers"], "the token of another tab is not sent")
        self.assertFalse(out["hasToken"])
        self.assertEqual([(tab["id"], tab["readOnly"]) for tab in last_model(out)["tabs"]], [(TAB_A, False), (TAB_B, True)])

    def test_a_bare_token_does_not_fit_a_server_with_several_tabs_and_is_forgotten(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}
        out = run_page(routes=routes, storage={"dashboard:approveToken": BARE})
        self.assertEqual(out["storage"], {}, "forgotten at once, not after the first refusal")
        self.assertFalse(out["hasToken"])
        alerts = last_model(out)["alerts"]
        self.assertEqual(alerts[0]["title"], ["ui.wt.staleTitle", {}], "the page asks for a new address from `dashboard url`")
        self.assertEqual(alerts[0]["kind"], "warn")

    def test_the_second_tab_that_appears_later_makes_the_page_forget_the_bare_token(self):
        routes = {"/api/tabs": tabs_body(TAB_A), "/api/status": status_body()}
        out = run_page(routes=routes, storage={"dashboard:approveToken": BARE},
                       steps=[{"op": "route", "path": "/api/tabs", "value": tabs_body(TAB_A, TAB_B)}, {"op": "poll"}])
        self.assertEqual(out["storage"], {})
        self.assertEqual(last_model(out)["alerts"][0]["title"], ["ui.wt.staleTitle", {}])
        self.assertEqual(out["log"]["streams"], ["/events", "/events?tab=" + TAB_A], "the stream is reopened for the tab")
        self.assertEqual(urls(out, "/api/status")[-1], "/api/status?tab=" + TAB_A)

    def test_a_pair_for_the_tab_ends_the_warning_about_the_old_token(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}
        out = run_page(routes=routes, storage={"dashboard:approveToken": BARE}, steps=[{"op": "hash", "value": "#zgoda=" + PAIR_A}])
        self.assertEqual(out["storage"], {f"dashboard:approveToken:{TAB_A}": PAIR_A})
        self.assertTrue(out["hasToken"])
        self.assertEqual([alert["title"][0] for alert in last_model(out)["alerts"]], ["ui.wt.noteTitle"])

    def test_a_refused_pair_is_forgotten_and_a_refusal_of_another_kind_is_not(self):
        refused = {"error": "no", "code": "approve_token"}
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body(), "/api/approve": {"status": 403, "body": refused}}
        stored = {f"dashboard:approveToken:{TAB_A}": PAIR_A, f"dashboard:approveToken:{TAB_B}": PAIR_B}
        out = run_page(routes=routes, storage=dict(stored), search="?tab=" + TAB_B, steps=[{"op": "approve"}])
        self.assertEqual(out["storage"], {f"dashboard:approveToken:{TAB_A}": PAIR_A}, "only the pair of the tab that was refused")
        self.assertEqual(len(urls(out, "/api/status")), 2, "the page refreshes so that the buttons become inactive")
        routes["/api/approve"] = {"status": 403, "body": {"error": "Obcy Origin."}}
        out = run_page(routes=routes, storage=dict(stored), search="?tab=" + TAB_B, steps=[{"op": "approve"}])
        self.assertEqual(out["storage"], stored)

    # ---------------------------------------------------------------------------------------------------------------- switching
    def test_selecting_a_tab_changes_the_address_the_stream_and_the_status(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}
        out = run_page(routes=routes, search="?x=1", steps=[{"op": "select", "id": TAB_B}])
        self.assertEqual(out["log"]["pushed"], ["/?tab=" + TAB_B + "&x=1"], "the other parameters stay")
        self.assertEqual(out["log"]["streams"], ["/events?tab=" + TAB_A, "/events?tab=" + TAB_B])
        self.assertEqual(out["log"]["closed"], 1, "one stream at a time")
        self.assertEqual(urls(out, "/api/status"), ["/api/status?tab=" + TAB_A, "/api/status?tab=" + TAB_B])
        self.assertEqual([tab["active"] for tab in last_model(out)["tabs"]], [False, True])

    def test_selecting_the_same_tab_or_a_bad_id_does_nothing(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}
        out = run_page(routes=routes, steps=[{"op": "select", "id": TAB_A}, {"op": "select", "id": "../x"}, {"op": "select", "id": "ABCDEF123456"}])
        self.assertEqual(out["log"]["pushed"], [])
        self.assertEqual(len(out["log"]["streams"]), 1)

    def test_the_plan_and_the_view_are_remembered_per_tab(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}
        steps = [{"op": "selectPlan", "path": "docs/plans/b.md"}, {"op": "storage", "key": "plan"}, {"op": "navigate", "hash": "docs:docs/plans/b.md"},
                 {"op": "select", "id": TAB_B}, {"op": "storage", "key": "plan"}, {"op": "navigate", "hash": "tests"}, {"op": "select", "id": TAB_A}]
        out = run_page(routes=routes, storage={f"dashboard:plan:{TAB_A}": "docs/plans/a.md", f"dashboard:view:{TAB_B}": "questions"}, steps=steps)
        self.assertEqual(out["results"], ["docs/plans/a.md", None], "the plan of the other tab is its own")
        self.assertEqual(urls(out, "/api/status")[0], "/api/status?plan=docs%2Fplans%2Fa.md&tab=" + TAB_A)
        self.assertEqual(out["storage"][f"dashboard:view:{TAB_A}"], "docs:docs/plans/b.md")
        self.assertEqual(out["storage"][f"dashboard:view:{TAB_B}"], "tests")
        views = [call for call in out["log"]["board"] if call[0] == "setView"]
        self.assertIn(["setView", "questions", False], views, "the remembered view of the tab is applied when it is shown")
        self.assertEqual(views[-1], ["setView", "docs", False], "back on the first tab its own document is shown")
        self.assertIn(["setDoc", "docs/plans/b.md"], [call[:2] for call in out["log"]["board"]])

    def test_a_single_tab_keeps_the_plan_and_the_view_in_the_old_keys(self):
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A), "/api/status": status_body()}, steps=[{"op": "selectPlan", "path": "docs/plans/b.md"}, {"op": "navigate", "hash": "tests"}])
        self.assertFalse([key for key in out["storage"] if "view" in key or ":" in key.split("dashboard:", 1)[1]])

    def test_the_old_plan_key_serves_the_default_tab_the_first_time(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}
        out = run_page(routes=routes, storage={"dashboard:plan": "docs/plans/b.md"})
        self.assertEqual(urls(out, "/api/status"), ["/api/status?plan=docs%2Fplans%2Fb.md&tab=" + TAB_A])
        out = run_page(routes=routes, storage={"dashboard:plan": "docs/plans/b.md"}, search="?tab=" + TAB_B)
        self.assertEqual(urls(out, "/api/status"), ["/api/status?tab=" + TAB_B], "another tab does not inherit it")

    def test_back_and_forward_follow_the_address(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}
        out = run_page(routes=routes, steps=[{"op": "select", "id": TAB_B}, {"op": "popstate", "url": "/?tab=" + TAB_A}])
        self.assertEqual(out["log"]["pushed"], ["/?tab=" + TAB_B], "going back pushes nothing")
        self.assertEqual([tab["active"] for tab in last_model(out)["tabs"]], [True, False])
        self.assertEqual(out["log"]["streams"][-1], "/events?tab=" + TAB_A)

    def test_the_answer_of_a_tab_the_user_has_left_is_not_drawn(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": {"hold": "first"}}
        out = run_page(routes=routes, steps=[{"op": "select", "id": TAB_B}, {"op": "route", "path": "/api/status", "value": status_body(who="B")},
                                              {"op": "release", "name": "first", "body": {"plans": [], "who": "A"}}])
        updates = [call[1] for call in out["log"]["board"] if call[0] == "update"]
        self.assertEqual([u["who"] for u in updates], ["B"], "the late answer of the first tab is dropped")
        self.assertEqual(urls(out, "/api/status")[-1], "/api/status?tab=" + TAB_B)

    # ---------------------------------------------------------------------------------------------------------------- one renderer per tab
    def test_switching_destroys_the_renderer_and_makes_a_new_one_with_the_page_settings(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body(), "/api/descriptions": {"status": 200, "body": {"x": 1}}}
        out = run_page(routes=routes, steps=[{"op": "select", "id": TAB_B}])
        names = [call[0] if call[0] != "create" else f"create{call[1]}" for call in out["log"]["board"]]
        self.assertEqual(out["log"]["creates"], 2, "a new renderer for the other tab: an armed CP/READY/GO, an open dialog and the caches of documents and tests are gone with the old one")
        self.assertLess(names.index("destroy"), names.index("create2"))
        after = names[names.index("create2"):]
        for call in ("setDescriptions", "syncTheme", "setLive", "setVisible"):
            self.assertIn(call, after, f"{call} is given to the new renderer again")
        self.assertNotIn("update", after[:after.index("setVisible")], "empty until the status of the tab arrives")

    def test_nothing_of_the_old_tab_is_drawn_after_the_switch(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body(who="A")}
        out = run_page(routes=routes, steps=[{"op": "route", "path": "/api/status", "value": {"hold": "second"}}, {"op": "select", "id": TAB_B}])
        names = [call[0] if call[0] != "create" else f"create{call[1]}" for call in out["log"]["board"]]
        self.assertEqual([n for n in names[names.index("create2"):] if n == "update"], [], "no status of the new tab yet: the new renderer shows nothing")

    def test_a_late_answer_of_the_document_or_the_tests_of_the_left_tab_is_cut_off(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body(), "/api/doc": {"hold": "doc"}, "/api/tests": {"hold": "tests"}}
        out = run_page(routes=routes, steps=[{"op": "startDoc", "name": "doc"}, {"op": "startTests", "name": "tests"}, {"op": "select", "id": TAB_B},
                                              {"op": "release", "name": "doc", "body": {"path": "docs/x.md", "html": "A"}}, {"op": "release", "name": "tests", "body": {"tests": []}}])
        self.assertEqual(out["log"]["pending"], {"doc": {"error": "tab changed"}, "tests": {"error": "tab changed"}})
        self.assertTrue(urls(out, "/api/doc")[0].endswith("tab=" + TAB_A) and urls(out, "/api/tests")[0].endswith("tab=" + TAB_A))

    def test_the_same_step_and_group_in_two_tabs_go_to_each_tab_and_the_answer_stays_with_its_tab(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body(), "/api/tests": {"status": 200, "body": {"tests": []}}}
        out = run_page(routes=routes, steps=[{"op": "tests"}, {"op": "select", "id": TAB_B}, {"op": "tests"}])
        self.assertEqual([url.rsplit("&", 1)[-1] for url in urls(out, "/api/tests")], ["tab=" + TAB_A, "tab=" + TAB_B], "two requests, one per tab, nothing shared by the page")
        self.assertEqual(out["log"]["creates"], 2, "and the cache of the renderer is not shared either")

    def test_a_refused_approval_forgets_the_token_of_the_tab_it_was_made_for(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body(), "/api/approve": {"hold": "ap"}}
        stored = {f"dashboard:approveToken:{TAB_A}": PAIR_A, f"dashboard:approveToken:{TAB_B}": PAIR_B}
        out = run_page(routes=routes, storage=dict(stored), steps=[{"op": "startApprove", "name": "ap"}, {"op": "select", "id": TAB_B},
                                                                    {"op": "release", "name": "ap", "status": 403, "body": {"error": "no", "code": "approve_token"}}])
        self.assertEqual(out["storage"], {f"dashboard:approveToken:{TAB_B}": PAIR_B}, "the pair of A is forgotten, the pair of B (the tab on the page now) stays")
        routes["/api/approve"] = {"hold": "ap"}
        out = run_page(routes=routes, storage=dict(stored), steps=[{"op": "startApprove", "name": "ap"}, {"op": "select", "id": TAB_B},
                                                                    {"op": "route", "path": "/api/tabs", "value": tabs_body(TAB_B, TAB_C)},
                                                                    {"op": "release", "name": "ap", "status": 410, "body": {"error": "gone", "code": "tabGone"}}])
        self.assertNotIn(True, [tab["gone"] for tab in last_model(out)["tabs"] if tab["id"] == TAB_B], "a removed A is not B")

    # ---------------------------------------------------------------------------------------------------------------- the list of the tabs
    def test_a_failing_tabs_route_is_tried_again_and_only_a_404_ends_the_asking(self):
        out = run_page(routes={"/api/tabs": {"status": 500, "body": {}}, "/api/status": status_body()}, steps=[{"op": "poll"}, {"op": "poll"}])
        self.assertEqual(len(urls(out, "/api/tabs")), 3)

    def test_a_server_that_does_not_answer_the_list_in_time_is_a_one_tab_server_until_it_does(self):
        routes = {"/api/tabs": {"hold": "tabs"}, "/api/status": status_body()}
        out = run_page(routes=routes, steps=[{"op": "timeouts"}, {"op": "release", "name": "tabs", "body": tabs_body(TAB_A, TAB_B)["body"]}])
        self.assertEqual(urls(out, "/api/status"), ["/api/status", "/api/status?tab=" + TAB_A], "the page started as today and followed the list when it came")
        self.assertEqual(out["log"]["streams"], ["/events", "/events?tab=" + TAB_A])

    # ---------------------------------------------------------------------------------------------------------------- the bar is sticky
    def test_the_bar_is_sticky_and_the_header_sticks_below_it(self):
        css = (RENDERER.parent / "app.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.wt-bar \{ display: contents; \}")
        row = re.search(r"\.wt-row \{[^}]*\}", css).group(0)
        self.assertIn("position: sticky", row)
        self.assertIn("top: 0", row)
        self.assertNotIn("position: sticky", re.search(r"\.wt-alerts \{[^}]*\}", css).group(0), "the alerts and the list of hidden worktrees scroll away")
        self.assertNotIn("position: sticky", re.search(r"\.wt-hidden \{[^}]*\}", css).group(0))
        self.assertRegex(css, r"\.top \{[^}]*position: sticky; top: var\(--wt-h, 0px\)")
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()})
        self.assertEqual(out["log"]["vars"][-1], ["--wt-h", "40px"])
        self.assertGreaterEqual(out["log"]["observed"], 1, "the height follows the row when the fonts load or the window is resized")
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A), "/api/status": status_body()})
        self.assertEqual(out["log"]["vars"][-1], ["--wt-h", "0px"])

    # ---------------------------------------------------------------------------------------------------------------- tokens, more
    def test_a_refused_token_is_forgotten_with_its_fallback_key_too(self):
        refused = {"error": "no", "code": "approve_token"}
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A), "/api/status": status_body(), "/api/approve": {"status": 403, "body": refused}},
                       storage={"status-board:approveToken": BARE}, steps=[{"op": "approve"}])
        self.assertEqual(out["storage"], {})

    def test_a_bare_token_pasted_on_a_server_with_several_tabs_is_not_stored_and_the_page_warns(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}
        out = run_page(routes=routes, steps=[{"op": "hash", "value": "#zgoda=" + BARE}])
        self.assertEqual(out["storage"], {})
        self.assertEqual(out["log"]["replaced"], ["/"])
        self.assertEqual(last_model(out)["alerts"][0]["title"], ["ui.wt.staleTitle", {}])
        self.assertFalse(out["hasToken"])

    def test_a_pair_on_a_server_with_one_tab_is_used(self):
        routes = {"/api/tabs": tabs_body(TAB_A), "/api/status": status_body(), "/api/approve": {"status": 200, "body": {"word": "CP"}}}
        out = run_page(routes=routes, storage={f"dashboard:approveToken:{TAB_A}": PAIR_A}, steps=[{"op": "approve"}])
        approve = next(call for call in out["log"]["fetch"] if call["url"] == "/api/approve")
        self.assertEqual(approve["headers"]["X-Approve-Token"], PAIR_A)
        self.assertNotIn("tab", approve["body"], "no tab parameter on a one-tab server, the pair names the tab")
        self.assertTrue(out["hasToken"])


    # ---------------------------------------------------------------------------------------------------------------- review 2
    def test_an_unknown_tab_in_the_address_is_replaced_in_the_address_and_back_does_not_land_on_it(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}
        ghost = "aaaaaaaaaaaa"
        out = run_page(routes=routes, search="?x=1&tab=" + ghost, steps=[{"op": "popstate", "url": "/?tab=" + ghost}])
        self.assertEqual(out["log"]["replaced"], ["/?tab=" + TAB_A + "&x=1"])
        self.assertEqual(out["log"]["creates"], 1, "no new renderer, no ghost tab")
        self.assertEqual(out["log"]["pushed"], [])
        self.assertEqual([tab["active"] for tab in last_model(out)["tabs"]], [True, False])

    def test_a_list_that_comes_late_for_a_known_tab_starts_it_over_with_its_own_renderer_and_view(self):
        routes = {"/api/tabs": {"hold": "tabs"}, "/api/status": status_body()}
        steps = [{"op": "timeouts"}, {"op": "release", "name": "tabs", "body": tabs_body(TAB_A, TAB_B)["body"]}]
        out = run_page(routes=routes, search="?tab=" + TAB_B, storage={f"dashboard:view:{TAB_B}": "questions"}, steps=steps)
        self.assertEqual(out["log"]["creates"], 2, "what was drawn was the default tab")
        self.assertEqual(urls(out, "/api/status")[-1], "/api/status?tab=" + TAB_B)
        self.assertIn(["setView", "questions", False], out["log"]["board"])
        self.assertEqual([tab["gone"] for tab in last_model(out)["tabs"]], [False, False])

    def test_a_list_that_comes_late_for_an_unknown_tab_falls_back_and_does_not_mark_it_removed(self):
        routes = {"/api/tabs": {"hold": "tabs"}, "/api/status": status_body()}
        ghost = "aaaaaaaaaaaa"
        out = run_page(routes=routes, search="?tab=" + ghost, steps=[{"op": "timeouts"}, {"op": "release", "name": "tabs", "body": tabs_body(TAB_A, TAB_B)["body"]}])
        model = last_model(out)
        self.assertEqual([(tab["id"], tab["active"], tab["gone"]) for tab in model["tabs"]], [(TAB_A, True, False), (TAB_B, False, False)])
        self.assertEqual(model["alerts"][0]["title"], ["ui.wt.unknownTab", {"id": ghost}])
        self.assertEqual(out["log"]["replaced"], ["/?tab=" + TAB_A])
        self.assertEqual(out["log"]["creates"], 1)

    def test_the_remembered_view_is_applied_after_the_status_of_the_new_tab_not_before(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}
        steps = [{"op": "route", "path": "/api/status", "value": {"hold": "second"}}, {"op": "select", "id": TAB_B}]
        out = run_page(routes=routes, storage={f"dashboard:view:{TAB_B}": "questions"}, steps=steps)
        self.assertNotIn(["setView", "questions", False], out["log"]["board"], "no status of the new tab yet")
        out = run_page(routes=routes, storage={f"dashboard:view:{TAB_B}": "questions"}, steps=steps + [{"op": "release", "name": "second", "body": {"plans": []}}])
        names = [call[0] if call[0] != "create" else f"create{call[1]}" for call in out["log"]["board"]]
        self.assertLess(names.index("update", names.index("create2")), names.index("setView", names.index("create2")), "the view follows the status of its tab")

    def test_the_new_renderer_starts_in_the_language_of_the_page(self):
        texts = {"ui.title.loading": "Loading"}
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body(lang="en", texts=texts)}
        out = run_page(routes=routes, steps=[{"op": "select", "id": TAB_B}])
        self.assertEqual(out["log"]["initials"], [None, {"lang": "en", "texts": texts}])

    def test_an_approval_that_ends_after_the_switch_is_reported_on_the_bar_only(self):
        stored = {f"dashboard:approveToken:{TAB_A}": PAIR_A, "dashboard:terminalNote": "1"}
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body(), "/api/approve": {"hold": "ap"}}
        steps = [{"op": "startApprove", "name": "ap"}, {"op": "select", "id": TAB_B}]
        out = run_page(routes=routes, storage=dict(stored), steps=steps + [{"op": "release", "name": "ap", "body": {"word": "CP", "next": "ok"}}])
        self.assertEqual(last_model(out)["alerts"], [{"kind": "info", "title": ["ui.wt.otherDone", {"word": "CP", "name": "Skillsync"}], "body": None, "dismiss": "outcome"}])
        self.assertEqual(urls(out, "/api/status")[-1], "/api/status?tab=" + TAB_B, "the flags are fetched again, for the tab on the page")
        out = run_page(routes=routes, storage=dict(stored), steps=steps + [{"op": "release", "name": "ap", "status": 409, "body": {"error": "sha changed"}}])
        alert = last_model(out)["alerts"][0]
        self.assertEqual((alert["kind"], alert["title"], alert["body"]), ("warn", ["ui.wt.otherRefused", {"word": "CP", "name": "Skillsync"}], ["ui.wt.otherReason", {"reason": "sha changed"}]))
        out = run_page(routes=routes, storage=dict(stored), steps=steps + [{"op": "release", "name": "ap", "status": 409, "body": {"error": "sha changed"}}, {"op": "bar", "name": "onDismiss", "args": ["outcome"]}])
        self.assertEqual(last_model(out)["alerts"], [])

    def test_an_approval_on_the_tab_that_is_shown_has_no_alert_of_the_bar(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body(), "/api/approve": {"status": 409, "body": {"error": "sha changed"}}}
        out = run_page(routes=routes, storage={"dashboard:terminalNote": "1"}, steps=[{"op": "approve"}])
        self.assertEqual(last_model(out)["alerts"], [], "the page of the tab says it itself")

    def test_the_patience_timer_of_the_start_is_cleared(self):
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()})
        self.assertEqual(out["log"]["cleared"], 1)

    # ---------------------------------------------------------------------------------------------------------------- the others
    def test_the_waiting_flag_of_the_other_tabs_comes_from_the_light_list_and_the_shown_one_from_its_status(self):
        light = [{"id": TAB_A, "waiting": False}, {"id": TAB_B, "waiting": True}]
        status = status_body(worktrees=light, waiting=[{"what": "CP", "approve": "cp"}], approvals={"actions": [{"kind": "cp", "state": "before"}]})
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status})
        self.assertEqual([(tab["id"], tab["waiting"]) for tab in last_model(out)["tabs"]], [(TAB_A, True), (TAB_B, True)])
        status = status_body(worktrees=light, waiting=[{"what": "CP", "approve": "cp"}], approvals={"actions": [{"kind": "cp", "state": "after"}]})
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status})
        self.assertEqual([(tab["id"], tab["waiting"]) for tab in last_model(out)["tabs"]], [(TAB_A, False), (TAB_B, True)], "a given consent no longer waits; the light flag is used for the other tab only")

    def test_an_unknown_tab_in_the_address_falls_back_to_the_default_one_with_a_notice(self):
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}, search="?tab=" + TAB_C)
        model = last_model(out)
        self.assertEqual([tab["active"] for tab in model["tabs"]], [True, False])
        self.assertEqual(model["alerts"][0], {"kind": "warn", "title": ["ui.wt.unknownTab", {"id": TAB_C}], "body": None, "dismiss": "unknown"})
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}, search="?tab=" + TAB_C, steps=[{"op": "bar", "name": "onDismiss", "args": ["unknown"]}])
        self.assertNotIn("ui.wt.unknownTab", json.dumps(last_model(out)))

    def test_a_removed_tab_stays_as_a_marked_tab_until_the_user_leaves_it(self):
        stored = {f"dashboard:approveToken:{TAB_B}": PAIR_B, "dashboard:terminalNote": "1"}
        # the tab vanished from the list while the page was open (the poll), other tabs remain
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B, TAB_C), "/api/status": status_body()}
        gone = [{"op": "route", "path": "/api/tabs", "value": tabs_body(TAB_A, TAB_C)}, {"op": "poll"}]
        out = run_page(routes=routes, search="?tab=" + TAB_B, storage=dict(stored), steps=gone)
        model = last_model(out)
        self.assertEqual([(tab["id"], tab["gone"], tab["active"]) for tab in model["tabs"]], [(TAB_A, False, False), (TAB_C, False, False), (TAB_B, True, True)])
        self.assertEqual(model["alerts"], [{"kind": "error", "title": ["ui.wt.goneTitle", {"name": "ENROLL-6431"}], "body": ["ui.wt.goneBody", {}], "dismiss": None}])
        out = run_page(routes=routes, search="?tab=" + TAB_B, storage=dict(stored), steps=gone + [{"op": "select", "id": TAB_A}])
        self.assertNotIn(True, [tab["gone"] for tab in last_model(out)["tabs"]], "the marked tab goes when the user leaves it")
        # a consent for a tab the server removed: 410, nothing is stored, and the tab is marked even before the poll
        routes["/api/approve"] = {"status": 410, "body": {"error": "gone", "code": "tabGone"}}
        out = run_page(routes=routes, search="?tab=" + TAB_B, storage=dict(stored), steps=[{"op": "route", "path": "/api/tabs", "value": tabs_body(TAB_A, TAB_C)}, {"op": "approve"}])
        self.assertEqual(out["results"][0]["ok"], False)
        self.assertEqual([(tab["id"], tab["gone"]) for tab in last_model(out)["tabs"]], [(TAB_A, False), (TAB_C, False), (TAB_B, True)])
        self.assertEqual(out["storage"], stored, "a removed tab is not a refused token: the token stays")

    def test_a_status_that_says_the_tab_is_unknown_marks_it_removed_without_a_connection_error(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": {"status": 404, "body": {"error": "no tab", "code": "unknownTab"}}}
        out = run_page(routes=routes, search="?tab=" + TAB_B)
        self.assertIn(["setLive", "connecting"], [call[:2] for call in out["log"]["board"]])
        self.assertNotIn(["setLive", "down"], [call[:2] for call in out["log"]["board"]])
        self.assertEqual(last_model(out)["tabs"][1]["id"], TAB_B)

    def test_a_removed_tab_makes_no_connection_alert_no_stream_and_no_poll_after_the_silence(self):
        # the page asks for a tab the server does not list any more (it knew it before): the list has two tabs first, then loses it
        first = tabs_body(TAB_A, TAB_B)
        out = run_page(routes={"/api/tabs": first, "/api/status": status_body()}, search="?tab=" + TAB_B,
                       steps=[{"op": "route", "path": "/api/tabs", "value": tabs_body(TAB_A, TAB_C)}, {"op": "route", "path": "/api/status", "value": {"status": 404, "body": {"error": "no tab", "code": "unknownTab"}}},
                              {"op": "poll"}, {"op": "advance", "ms": 40000}, {"op": "streamError"}, {"op": "timeouts5"}, {"op": "poll"}, {"op": "tick", "ms": 5000}])
        models = out["log"]["models"]
        self.assertTrue(any(tab["gone"] for tab in models[-1]["tabs"]))
        self.assertEqual([a for m in models for a in m["alerts"] if a["title"][0] == "ui.wt.lostTitle"], [], "a removed tab is not a lost connection")
        self.assertNotIn(["setLive", "down"], [call[:2] for call in out["log"]["board"]])
        self.assertEqual(len(out["log"]["streams"]), 1, "no new stream for the removed tab")
        self.assertEqual(len([c for c in out["log"]["fetch"] if c["url"].startswith("/api/status?")]), 1, "the status is not asked for again once the tab is gone")

    def test_a_status_that_says_the_tab_is_unknown_keeps_the_connection_fresh(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": {"status": 404, "body": {"error": "no tab", "code": "unknownTab"}}}
        out = run_page(routes=routes, search="?tab=" + TAB_B, steps=[{"op": "advance", "ms": 40000}, {"op": "streamError"}, {"op": "poll"}])
        self.assertEqual([a for m in out["log"]["models"] for a in m["alerts"] if a["title"][0] == "ui.wt.lostTitle"], [])

    def test_the_hidden_worktrees_of_the_server_reach_the_bar_without_a_token(self):
        hidden = [{"name": "ENROLL-6402", "lastActivity": "2026-11-13T10:00:00+00:00", "reason": "inactive", "token": "x" * 40, "path": "/secret"}]
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A, TAB_B, hidden=hidden), "/api/status": status_body()})
        self.assertEqual(last_model(out)["hidden"], [{"name": "ENROLL-6402", "lastActivity": "2026-11-13T10:00:00+00:00", "reason": "inactive"}])
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A, TAB_B, hidden=hidden), "/api/status": status_body()}, steps=[{"op": "bar", "name": "onToggleAll"}])
        self.assertTrue(last_model(out)["showAll"])

    def test_the_note_about_the_server_terminal_is_shown_until_it_is_dismissed(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}
        out = run_page(routes=routes)
        self.assertEqual(last_model(out)["alerts"][-1], {"kind": "info", "title": ["ui.wt.noteTitle", {}], "body": ["ui.wt.noteBody", {}], "dismiss": "note"})
        out = run_page(routes=routes, steps=[{"op": "bar", "name": "onDismiss", "args": ["note"]}])
        self.assertEqual(last_model(out)["alerts"], [])
        self.assertEqual(out["storage"], {"dashboard:terminalNote": "1"})
        out = run_page(routes=routes, storage={"dashboard:terminalNote": "1"})
        self.assertEqual(last_model(out)["alerts"], [])

    def test_losing_the_connection_is_said_for_every_tab(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": {"status": 500, "body": {}}}
        out = run_page(routes=routes, storage={"dashboard:terminalNote": "1"})
        self.assertEqual([alert["title"] for alert in last_model(out)["alerts"]], [["ui.wt.lostTitle", {}]])
        self.assertEqual(last_model(out)["alerts"][0]["kind"], "error")

    def test_the_title_follows_the_tab(self):
        routes = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}
        out = run_page(routes=routes, search="?tab=" + TAB_B, steps=[{"op": "title", "value": "Status przebiegu"}])
        self.assertEqual(out["title"], "Status przebiegu · ENROLL-6431")
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A), "/api/status": status_body()}, steps=[{"op": "title", "value": "Status przebiegu"}])
        self.assertEqual(out["title"], "Status przebiegu")

    def test_without_the_bar_function_the_page_still_works(self):
        out = run_page(noBar=True, routes={"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}, search="?tab=" + TAB_B)
        self.assertEqual(urls(out, "/api/status"), ["/api/status?tab=" + TAB_B])

    def test_the_source_never_prints_or_logs_a_token(self):
        source = APP.read_text(encoding="utf-8")
        for needle in ("console.", "alert(", "innerHTML = token", "textContent = token", ".innerText"):
            self.assertNotIn(needle, source)


# ------------------------------------------------------------------------------------------------------------------ the real renderer, twice on one root
SWITCH_TAIL = r"""
(async () => {
  const base = () => JSON.parse(JSON.stringify(input.data));
  const deps = (extra) => Object.assign({ actions: { approve: () => Promise.resolve({ ok: true, word: 'CP', text: 'x' }) }, now: () => input.nowMs, raf: (f) => { f(); return 0; },
    storage: { get: () => null, set() {} }, mountHtml() {}, onTitle() {}, onNavigate() {}, hasApproveToken: () => true,
    scroll: { save: () => 0, restore() {}, reveal() {}, toTop: { subscribe: (cb) => { cb({ y: 0, viewport: 0 }); return () => {}; }, go() {} } } }, extra || {});
  const doc = makeDoc(); const root = doc.createElement('div'); doc.root = root;
  const btn = () => root.querySelector('#acts button[data-approve="cp"]');
  const first = R.create(root, deps()); first.setDescriptions({}); first.update(base());
  const idle = { text: text(btn()), state: btn().getAttribute('data-state'), armed: first.state().armed, sprites: root.querySelectorAll('symbol').length };
  btn().click();
  const armed = { text: text(btn()), armedFlag: first.state().armed };
  first.destroy();
  const cleared = { children: root.childNodes.length, buttons: root.querySelectorAll('button').length, acts: root.querySelectorAll('#acts').length };
  // the second renderer on the same root: skeleton in the language of the page, before any status
  const second = R.create(root, deps({ initial: { lang: 'en', texts: input.en } }));
  second.setDescriptions({});
  const skeleton = { title: text(root.querySelector('#title')), buttons: root.querySelectorAll('#acts button[data-approve]').length };
  second.update(Object.assign(base(), { lang: 'en', texts: input.en }));
  const again = { text: text(btn()), state: btn().getAttribute('data-state'), armed: second.state().armed, acts: root.querySelectorAll('#acts').length, sprites: root.querySelectorAll('symbol').length };
  const plain = doc.createElement('div'); doc.root = plain;
  const third = R.create(plain, deps()); third.setDescriptions({});
  const polish = text(plain.querySelector('#title'));
  second.destroy(); third.destroy();
  process.stdout.write(JSON.stringify({ idle, armed, cleared, skeleton, again, polish, violations }));
})().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e) })); });
"""


@SKIP
class RealRendererSwitch(unittest.TestCase):
    """What the page does on a switch of the tab (`destroy`, then `create` on the same root), with the real renderer on the DOM stub of the renderer tests."""

    @classmethod
    def setUpClass(cls):
        data = clone(fixture_files()["cp-ready"])
        data["approvals"]["here"] = True
        cls.out = run_node(HEAD + SWITCH_TAIL, {"fixtures": {}, "now": {}, "data": data, "nowMs": ms(data["generatedAt"]), "en": ui_catalog("en")})

    def test_the_consent_armed_in_the_first_renderer_is_armed_in_no_other(self):
        out = self.out
        self.assertNotEqual(out["armed"]["text"], out["idle"]["text"], "the first click arms the button")
        self.assertTrue(out["armed"]["armedFlag"])
        self.assertEqual(out["again"]["text"], 'Approve "CP" \u2192 feat/demo', "the idle button of the English page, not the armed one")
        self.assertEqual(out["again"]["state"], out["idle"]["state"])
        self.assertFalse(out["again"]["armed"])

    def test_destroy_leaves_nothing_of_the_old_page_and_the_new_one_is_drawn_once(self):
        self.assertEqual(self.out["cleared"], {"children": 0, "buttons": 0, "acts": 0})
        self.assertEqual(self.out["again"]["acts"], 1)
        self.assertEqual(self.out["again"]["sprites"], self.out["idle"]["sprites"], "one sprite, not two")

    def test_the_skeleton_before_the_first_status_has_no_button_and_speaks_the_language_of_the_page(self):
        self.assertEqual(self.out["skeleton"]["buttons"], 0, "no clickable consent before the status of the tab")
        self.assertNotEqual(self.out["skeleton"]["title"], self.out["polish"], "the same skeleton without `initial` is Polish")
        self.assertIsNone(POLISH.search(self.out["skeleton"]["title"] or ""))
        self.assertEqual(self.out["violations"], [])


if __name__ == "__main__":
    unittest.main()
