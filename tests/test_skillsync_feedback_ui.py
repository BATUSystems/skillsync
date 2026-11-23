"""CORE-1142-FB-BUTTON, S5 (FB3): the section "Zgłoszenia do warstw" of the status page (renderer, page adapter, catalog, CSS), without a browser.

Convention (as `test_skillsync_dashboard_renderer*.py`): the renderer `dashboard-render.js` runs in node on the shadow DOM of the renderer test
(it throws on `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `cssText`, `setAttribute('style'|'on…')`); `app.js` runs on a stub of the browser
(as `test_skillsync_dashboard_app_token.py`); the catalog and the CSS are read as text. Looks (layout, contrast, real focus ring, Esc in a real
browser, bidi shaping) are left to the E2E step S6.

DOM contract that these tests fix (the names of the classes and attributes are the interface between the renderer, the CSS and the E2E step):
  section[data-feedback]                      the section; one `.fb-result[aria-live]` inside, present whenever the section is
  .fb-count                                   the number of drafts (`total`)
  .fb-card[data-draft=F<n>][data-state=...]   a tile per draft (newest first, at most 10 outside `details.fb-older`)
    .fb-sha (sha12), .fb-id, .fb-target, .fb-mode, .fb-quote (only for a comment), .fb-title, .fb-tags li, pre.fb-body (tabindex=0 + a label),
    .fb-scan, .fb-reason, .fb-given, .fb-cmd, button.fb-approve (only when `waiting` and the role may consent)
  [role=dialog][aria-modal=true]              the confirmation: .fb-d-sha, .fb-d-draft, .fb-d-target, .fb-d-mode, .fb-generic-warning (generic only),
                                              input[type=checkbox].fb-ack (generic only), button.fb-confirm, button.fb-cancel
Host seam: `actions.feedbackApprove({draft, sha})` -> Promise `{ok: true, already, sha12}` | `{ok: false, text}`; the page adapter implements it
as POST /api/feedback-approve with the headers of `postApprove`. The harness DOM has no `disabled` setter: the renderer sets/removes the attribute.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from skillsync_workflow import i18n  # noqa: E402
from tests.test_skillsync_dashboard_renderer import ADAPTER, HARNESS, NODE, NODE_MAJOR, RENDERER  # noqa: E402

ASSETS = ROOT / "library" / "skills" / "dashboard" / "assets"
CSS = ASSETS / "app.css"
BASE = ROOT / "tests" / "fixtures" / "dashboard" / "cp-ready" / "expected.json"
SKIP = unittest.skipUnless(NODE_MAJOR >= 18, "set SKILLSYNC_TEST_NODE (or have node on PATH), node >= 18")
HEAD = HARNESS[:HARNESS.index("(async () => {\n  const out = { fixtures: {}")]
TOKEN = "".join(("AbCdEfGh", "_ijkl", "MNOP-", "1234567890"))   # assembled: a literal key-like string trips the secret scanner
BIDI_CONTROL = re.compile("[‪-‮⁦-⁩‎‏؜\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x80-\x9f]")


def sha(draft):
    return hashlib.sha256(draft.encode()).hexdigest()


def view(**over):
    base = {"layer": "org", "target": {"organization": "Org One", "project": "Proj One", "area": "Area A", "type": "Task"},
            "mode": "create", "item": None, "item_title": None, "title": "Neutral title", "tags": ["alpha", "beta"],
            "body": "First line\nSecond line", "scan": {"status": "PASS", "coverage": "identity", "warnings": []}, "warning": None}
    base.update(over)
    return base


def element(number, state="waiting", generic=False, **over):
    draft = f"F{number}"
    row = {"draft": draft, "state": state, "reason": None, "sha": sha(draft), "sha12": sha(draft)[:12], "generic": generic,
           "given": None, "view": view(title=f"Title {draft}")}
    if state == "after":
        row["given"] = {"at": "2026-01-12T08:30:15.000000Z", "source": "dashboard"}
    if state == "stale":
        row = {"draft": draft, "state": "stale", "reason": "content changed", "sha12": sha(draft)[:12]}
    if state == "sent":
        row.pop("view")
    if state == "blocked":
        row["reason"] = "scan blocked this draft"
    row.update(over)
    return row


def state_of(elements, total=None, enabled=True):
    return {"enabled": enabled, "total": len(elements) if total is None else total, "drafts": elements}


PRELUDE = r"""
const base = input.base;
const tick3 = async () => { await tick(); await tick(); await tick(); };
function mk(feedback, o) {
  o = o || {};
  const j = JSON.parse(JSON.stringify(base));
  j.approvals.here = o.here !== false;
  if (o.viewer) j.approvals.viewer = true;
  if (o.tokenRequired) j.approvals.tokenRequired = true;
  if (feedback !== undefined) j.feedback = feedback;
  if (o.en) { j.lang = 'en'; j.texts = input.en; }
  const posts = [];
  const actions = { feedbackApprove: (a) => { posts.push(a); return o.post ? o.post(a, posts) : Promise.resolve({ ok: true, already: false, sha12: a.sha.slice(0, 12) }); } };
  const m = mount(j, { now: Date.parse(j.generatedAt), deps: o.noToken ? { hasApproveToken: () => false } : undefined, actions });
  return { m, posts, json: j };
}
const card = (m, id) => m.root.querySelector('.fb-card[data-draft="' + id + '"]');
const cards = (m) => m.root.querySelectorAll('.fb-card');
const dialog = (m) => m.root.querySelector('[role="dialog"]');
const tagSet = (m) => { const s = new Set(); walk(m.root, (n) => { if (n instanceof E) s.add(n.localName); }); return [...s]; };
function snap(c) {
  const q = (s) => c.querySelector(s), t = (s) => text(q(s));
  const b = q('button.fb-approve'), pre = q('pre.fb-body');
  return { state: c.getAttribute('data-state'), text: text(c), sha: t('.fb-sha'), id: t('.fb-id'), target: t('.fb-target'), mode: t('.fb-mode'), quote: t('.fb-quote'),
    title: t('.fb-title'), tags: c.querySelectorAll('.fb-tags li').map(text), body: pre ? pre.textContent : null,
    preTab: pre ? pre.getAttribute('tabindex') : null, preLabel: pre ? (pre.getAttribute('aria-label') || pre.getAttribute('aria-labelledby')) : null,
    scan: t('.fb-scan'), given: t('.fb-given'), cmd: t('.fb-cmd'), reason: t('.fb-reason'),
    button: b ? { disabled: b.disabled, name: b.getAttribute('aria-label') || text(b) } : null };
}
function openDialog(m, id) { const b = card(m, id).querySelector('button.fb-approve'); b.click(); return { button: b, dialog: dialog(m) }; }
function dsnap(m) {
  const d = dialog(m); if (!d) return null;
  const q = (s) => d.querySelector(s), c = q('button.fb-confirm'), k = q('input[type="checkbox"].fb-ack');
  return { text: text(d), modal: d.getAttribute('aria-modal'), name: d.getAttribute('aria-label') || d.getAttribute('aria-labelledby'),
    sha: text(q('.fb-d-sha')), draft: text(q('.fb-d-draft')), target: text(q('.fb-d-target')), mode: text(q('.fb-d-mode')),
    warning: text(q('.fb-generic-warning')), ack: Boolean(k), confirm: c ? { disabled: c.disabled, text: text(c) } : null,
    cancel: q('button.fb-cancel') ? text(q('button.fb-cancel')) : null, focusInside: d.contains(m.doc.focused), liveInside: Boolean(q('[aria-live]')) };
}
function tickAck(m, on) {
  const k = dialog(m).querySelector('input[type="checkbox"].fb-ack');
  k.checked = on;
  for (const type of ['click', 'input', 'change']) k.dispatchEvent(new Ev(type));
}
const confirmBtn = (m) => { const d = dialog(m); return d ? d.querySelector('button.fb-confirm') : null; };
const result = (m) => text(m.root.querySelector('.fb-result'));
const hostile = (m) => tagSet(m).filter((x) => ['img', 'script', 'iframe', 'object', 'embed', 'style', 'link', 'form'].includes(x));
"""


def run_node(tail, payload, timeout=120):
    with tempfile.TemporaryDirectory() as tmp:
        harness, data = Path(tmp) / "harness.cjs", Path(tmp) / "input.json"
        harness.write_text(HEAD + PRELUDE + "\n(async () => {\n  const out = { violations, errors: {} };\n  const step = async (name, fn) => { try { await fn(); } catch (e) { out.errors[name] = String(e && e.message || e); } };\n" + tail + "\n  console.log(JSON.stringify(out));\n})().catch((e) => { console.log(JSON.stringify({ fatal: String(e && e.stack || e) })); });\n",
                           encoding="utf-8")
        data.write_text(json.dumps(payload), encoding="utf-8")
        env = {**os.environ, "TZ": "UTC"}
        proc = subprocess.run([NODE, str(harness), str(RENDERER), str(data)], capture_output=True, text=True, timeout=timeout, cwd=tmp, env=env)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise AssertionError(f"node rc={proc.returncode}: {proc.stderr[-2000:]}")
    out = json.loads(proc.stdout)
    if "fatal" in out:
        raise AssertionError(out["fatal"])
    return out


class Needs(unittest.TestCase):
    """Base: a value of a node scenario that did not run is a failure of the behaviour (with the error of the scenario), not a KeyError."""
    out: dict = {}

    def need(self, key, source=None):
        source = self.out if source is None else source
        if key not in source:
            self.fail(f"the scenario did not produce `{key}`: {self.out.get('errors')}")
        return source[key]


def payload(**extra):
    return {"base": json.loads(BASE.read_text(encoding="utf-8")), "en": i18n.catalog_for_client("en"), **extra}


# ------------------------------------------------------------------------------------------------------------------ rendering, states
RENDER_TAIL = r"""
  out.cases = {};
  for (const [name, sc] of Object.entries(input.cases)) {
    const r = mk(sc.feedback, sc.opts);
    out.cases[name] = { sections: r.m.root.querySelectorAll('section[data-feedback]').length,
      cards: cards(r.m).map((c) => c.getAttribute('data-draft')),
      older: r.m.root.querySelectorAll('details.fb-older .fb-card').map((c) => c.getAttribute('data-draft')),
      olderSummary: r.m.root.querySelector('details.fb-older summary') ? text(r.m.root.querySelector('details.fb-older summary')) : null,
      olderOpen: r.m.root.querySelector('details.fb-older') ? r.m.root.querySelector('details.fb-older').hasAttribute('open') : null,
      count: text(r.m.root.querySelector('.fb-count')), heading: r.m.root.querySelector('section[data-feedback] h2') ? text(r.m.root.querySelector('section[data-feedback] h2')) : null, live: r.m.root.querySelector('.fb-result') ? r.m.root.querySelector('.fb-result').getAttribute('aria-live') : null,
      tiles: Object.fromEntries(cards(r.m).map((c) => [c.getAttribute('data-draft'), snap(c)])), buttons: r.m.root.querySelectorAll('button.fb-approve').length,
      bodies: r.m.root.querySelectorAll('pre.fb-body').length };
  }
"""


@SKIP
class SectionAndTiles(Needs):
    @classmethod
    def setUpClass(cls):
        many = [element(n) for n in range(12, 0, -1)]                      # F12 ... F1, newest first, all waiting
        mixed = [
            element(30),
            element(29, "after"),
            element(28, "stale", view=view(title="STALE-CONTENT-MARKER", body="STALE-CONTENT-MARKER")),   # a stale preview must never be shown
            element(27, "blocked"),
            element(26, "sent"),
            element(25, view=view(mode="comment", item=4242, item_title="Existing tracker title", title="Reply title")),
            element(24, generic=True, view=view(scan={"status": "PASS", "coverage": "generic", "warnings": ["a note"]})),
        ]
        readonly = [{"draft": "F2", "state": "waiting"}, {"draft": "F1", "state": "after"}]
        cls.out = run_node(RENDER_TAIL, payload(cases={
            "many": {"feedback": state_of(many)},
            "mixed": {"feedback": state_of(mixed)},
            "disabled": {"feedback": state_of(many, enabled=False)},
            "empty": {"feedback": state_of([], total=0)},
            "absent": {"feedback": None},
            "no-key": {},
            "readonly": {"feedback": state_of(readonly)},
            "control": {"feedback": state_of([element(5)])},
            "viewer": {"feedback": state_of([element(5)]), "opts": {"viewer": True}},
            "not-here": {"feedback": state_of([element(5)]), "opts": {"here": False}},
            "no-token": {"feedback": state_of([element(5)]), "opts": {"tokenRequired": True, "noToken": True}},
            "en": {"feedback": state_of([element(7)]), "opts": {"en": True}},
        }))["cases"]

    def case(self, name):
        return self.out[name]

    def tile(self, name, draft):
        tiles = self.case(name)["tiles"]
        if draft not in tiles:
            self.fail(f"no tile {draft} in case {name!r}: the section is not rendered (tiles: {sorted(tiles)})")
        return tiles[draft]

    def test_nothing_in_the_dom_unless_enabled_and_total_positive(self):
        for name in ("disabled", "empty", "absent", "no-key"):
            with self.subTest(case=name):
                got = self.case(name)
                self.assertEqual((got["sections"], got["cards"], got["buttons"], got["bodies"]), (0, [], 0, 0))
        self.assertEqual(self.case("many")["sections"], 1)

    def test_newest_first_ten_tiles_and_the_rest_in_a_collapsed_older_list(self):
        got = self.case("many")
        self.assertEqual(got["cards"], [f"F{n}" for n in range(12, 0, -1)])
        self.assertEqual(got["older"], ["F2", "F1"], "tiles 11 and 12 sit inside details.fb-older")
        self.assertFalse(got["olderOpen"], "the older list is collapsed")
        self.assertTrue(got["olderSummary"])
        self.assertEqual(self.case("mixed")["older"], [], "up to 10 drafts: no older list")
        self.assertEqual(self.case("mixed")["olderSummary"], None)
        self.assertIn("12", got["count"])

    def test_a_waiting_tile_shows_the_full_review_content_and_an_active_button(self):
        tile = self.tile("mixed", "F30")
        self.assertEqual(tile["state"], "waiting")
        self.assertEqual(tile["sha"], sha("F30")[:12])
        self.assertEqual(tile["id"], "F30")
        for part in ("Org One", "Proj One", "Area A", "Task"):
            self.assertIn(part, tile["target"])
        self.assertEqual(tile["title"], "Title F30")
        self.assertEqual(tile["tags"], ["alpha", "beta"])
        self.assertEqual(tile["body"], "First line\nSecond line", "the content is in a <pre>, line breaks kept")
        self.assertIn("PASS", tile["scan"])
        self.assertIn("identity", tile["scan"], "the coverage of the scan is shown")
        self.assertIsNotNone(tile["mode"])
        self.assertIsNone(tile["quote"], "a new item quotes nothing")
        self.assertFalse(tile["button"]["disabled"])

    def test_a_comment_names_the_item_and_marks_the_quote_as_coming_from_the_tracker(self):
        tile = self.tile("mixed", "F25")
        self.assertIn("#4242", tile["mode"])
        self.assertIn("Existing tracker title", tile["quote"])
        self.assertIn("CYTAT Z TRACKERA", tile["quote"])
        self.assertEqual(tile["title"], "Reply title")
        self.assertNotIn("CYTAT Z TRACKERA", self.tile("mixed", "F30")["text"])

    def test_after_shows_the_time_the_source_and_the_send_command_without_a_button(self):
        tile = self.tile("mixed", "F29")
        self.assertEqual(tile["state"], "after")
        self.assertRegex(tile["given"], r"(?i)zgoda dana 08:30")
        self.assertIn("dashboard", tile["given"])
        self.assertIn("skillsync feedback send F29", tile["cmd"])
        self.assertIsNone(tile["button"])

    def test_stale_says_so_with_the_draft_command_and_shows_no_content_and_no_button(self):
        tile = self.tile("mixed", "F28")
        self.assertIn("podgląd nieaktualny", tile["text"].lower())
        self.assertIn("skillsync feedback draft F28", tile["text"])
        self.assertNotIn("STALE-CONTENT-MARKER", tile["text"])
        self.assertIsNone(tile["body"])
        self.assertIsNone(tile["button"])

    def test_blocked_gives_the_reason_and_sent_an_information_both_without_a_button(self):
        blocked, sent = self.tile("mixed", "F27"), self.tile("mixed", "F26")
        self.assertIn("scan blocked this draft", blocked["text"])
        self.assertIsNone(blocked["button"])
        self.assertEqual(sent["state"], "sent")
        self.assertIsNone(sent["button"])
        self.assertIsNone(sent["body"], "a sent draft has no content to review")
        self.assertTrue(sent["text"].strip() != "F26", "a sent draft says it was sent, not only its id")
        self.assertEqual(self.case("mixed")["buttons"], 3, "only the three waiting tiles (F30, F25, F24) have a button")

    def test_read_only_role_sees_the_number_and_the_states_but_no_content_and_no_button(self):
        got = self.case("readonly")
        self.assertEqual((got["sections"], got["buttons"], got["bodies"]), (1, 0, 0))
        self.assertIn("2", got["count"])
        waiting, after = self.tile("readonly", "F2"), self.tile("readonly", "F1")
        self.assertNotEqual(waiting["text"], after["text"], "the two states read differently")
        for tile in (waiting, after):
            self.assertIsNone(tile["sha"])
            self.assertIsNone(tile["body"])
            self.assertIsNone(tile["title"])

    def test_no_button_for_a_viewer_a_host_without_consent_rights_or_a_page_without_the_token(self):
        self.assertEqual(self.case("control")["buttons"], 1, "control: the same draft for a role that may consent has the button (the zeros below mean something)")
        for name in ("viewer", "not-here", "no-token"):
            with self.subTest(case=name):
                self.assertEqual(self.case(name)["buttons"], 0)

    def test_accessibility_of_the_pre_and_the_buttons(self):
        got = self.case("mixed")
        tile = self.tile("mixed", "F30")
        self.assertEqual(tile["preTab"], "0", "the long content is reachable by the keyboard")
        self.assertTrue(tile["preLabel"], "the <pre> has a label")
        self.assertTrue(tile["button"]["name"].strip())
        self.assertEqual(got["live"], "polite", "the live region of the result exists as soon as the section does")

    def test_english_page_has_english_texts_and_no_polish_ones(self):
        got = self.case("en")
        self.assertFalse(re.search("[ąćęłńóśźż]", self.tile("en", "F7")["text"] + (got["count"] or "") + (got["heading"] or "")), self.tile("en", "F7")["text"])
        self.assertEqual(got["heading"], i18n.catalog_for_client("en").get("ui.feedback.title"), "the heading is the English catalog text")
        self.assertNotEqual(got["heading"], self.case("many")["heading"])
        self.assertNotIn("CYTAT", self.tile("en", "F7")["text"])


# ------------------------------------------------------------------------------------------------------------------ hostile text
HOSTILE = {
    "title": "<img src=x onerror=alert(1)> ‮detset‬ " + "T" * 10_000,
    "body": "line one\x07\x1b[31m red\n</pre><script>alert(1)</script>\n‮evil‬ ⁦x⁩ end",
    "tags": ["<img src=x onerror=alert(2)>", "</li><script>1</script>", "tag‮flip"],
    "item_title": "<script>alert(3)</script> ‮quoted",
    "target": {"organization": "<img src=x onerror=1>", "project": "P‮", "area": "</span><script>", "type": "T\x00"},
}
XSS_TAIL = r"""
  const bad = input.hostile;
  const feedback = { enabled: true, total: 3, drafts: [
    { draft: 'F3', state: 'waiting', reason: null, sha: input.sha, sha12: input.sha.slice(0, 12), generic: false, given: null,
      view: { layer: 'org', target: bad.target, mode: 'comment', item: 7, item_title: bad.item_title, title: bad.title, tags: bad.tags, body: bad.body,
              scan: { status: 'PASS', coverage: 'identity', warnings: [bad.item_title] }, warning: null } },
    { draft: 'F2', state: 'blocked', reason: bad.title, sha12: 'abcdefabcdef' },
    { draft: 'F1', state: 'after', sha: input.sha, sha12: input.sha.slice(0, 12), given: { at: '2026-01-12T08:30:15.000000Z', source: bad.title }, view: null, generic: false },
  ] };
  await step('hostile', async () => {
  const clean = mk(undefined);
  const r = mk(feedback);
  const c = card(r.m, 'F3');
  out.hostileTags = hostile(r.m);
  out.cleanHostile = hostile(clean.m);
  out.tile = snap(c);
  out.preCount = c.querySelectorAll('pre').length;
  out.sectionText = r.m.root.querySelector('section[data-feedback]').textContent;
  out.cardsText = cards(r.m).map((x) => x.textContent);
  out.after = snap(card(r.m, 'F1'));
  // the dialog texts come from the draft too
  const d = openDialog(r.m, 'F3');
  out.dialogText = dialog(r.m) ? dialog(r.m).textContent : null;
  out.dialogHostile = hostile(r.m);
  });
  // control: the checker finds an element that a careless renderer would have created
  const injected = mk(undefined);
  injected.m.root.appendChild(injected.m.doc.createElement('img'));
  out.control = hostile(injected.m);
"""


@SKIP
class HostileText(Needs):
    @classmethod
    def setUpClass(cls):
        cls.out = run_node(XSS_TAIL, payload(hostile=HOSTILE, sha=sha("F3")))

    def test_the_checker_can_fail(self):
        """The negative assertions below are only worth something if the checker sees a planted element (a control with a real `img`)."""
        self.assertEqual(self.need("control"), ["img"])
        self.assertEqual(self.need("cleanHostile"), [])

    def test_no_element_is_made_from_the_text_of_a_draft(self):
        self.assertEqual(self.need("hostileTags"), [])
        self.assertEqual(self.need("dialogHostile"), [])
        self.assertEqual(self.need("preCount"), 1, "a `</pre>` in the content does not end the <pre>")
        self.assertEqual(self.need("violations"), [], "no HTML sink was touched")

    def test_the_text_is_shown_as_text_verbatim_apart_from_the_removed_characters(self):
        tile = self.need("tile")
        self.assertIn("<img src=x onerror=alert(1)>", tile["title"])
        self.assertIn("</pre><script>alert(1)</script>", tile["body"])
        self.assertIn("<img src=x onerror=alert(2)>", tile["tags"][0])
        self.assertIn("</li><script>1</script>", tile["tags"][1])
        self.assertIn("<script>alert(3)</script>", tile["quote"])
        self.assertIn("<img src=x onerror=1>", tile["target"])
        self.assertEqual(len(tile["tags"]), 3)
        self.assertEqual(tile["body"].count("\n"), 2, "the line breaks of the body survive")
        self.assertIn("red", tile["body"])
        self.assertGreaterEqual(len(tile["title"]), 80, "a very long title does not vanish")

    def test_the_client_removes_bidi_and_control_characters_everywhere_a_draft_text_is_shown(self):
        for label, text in (("section", self.need("sectionText")), ("dialog", self.need("dialogText")), *((f"card{i}", t) for i, t in enumerate(self.need("cardsText")))):
            with self.subTest(place=label):
                self.assertEqual(BIDI_CONTROL.findall(text or ""), [])
        self.assertEqual(self.need("after")["state"], "after")


# ------------------------------------------------------------------------------------------------------------------ confirmation dialog and POST
DIALOG_TAIL = r"""
  const waiting = { enabled: true, total: 2, drafts: [
    { draft: 'F2', state: 'waiting', reason: null, sha: input.sha2, sha12: input.sha2.slice(0, 12), generic: true, given: null,
      view: { layer: 'org', target: { organization: 'Org Two', project: 'Proj Two', area: 'Area B', type: 'Bug' }, mode: 'comment', item: 77, item_title: 'Quoted', title: 'T2', tags: [], body: 'b2',
              scan: { status: 'PASS', coverage: 'generic', warnings: [] }, warning: 'w' } },
    { draft: 'F1', state: 'waiting', reason: null, sha: input.sha1, sha12: input.sha1.slice(0, 12), generic: false, given: null,
      view: { layer: 'org', target: { organization: 'Org One', project: 'Proj One', area: 'Area A', type: 'Task' }, mode: 'create', item: null, item_title: null, title: 'T1', tags: [], body: 'b1',
              scan: { status: 'PASS', coverage: 'identity', warnings: [] }, warning: null } },
  ] };
  const stale = JSON.parse(JSON.stringify(waiting)); stale.drafts[1] = { draft: 'F1', state: 'stale', reason: 'content changed', sha12: input.sha1.slice(0, 12) };
  const focusOnButton = (r, id) => { const f = r.m.doc.focused; return Boolean(f && f.matches && f.matches('button.fb-approve') && f.closest('.fb-card').getAttribute('data-draft') === id); };

  // 1. normal draft: the dialog and its closing ways
  let r;
  const evil = 'Draft changed <img src=x onerror=alert(9)> reload';
  const j = JSON.parse(JSON.stringify(base)); j.approvals.here = true; j.feedback = waiting;
  let release;
  await step('normal', async () => {
  r = mk(waiting);
  out.beforeOpen = { dialog: Boolean(dialog(r.m)), posts: r.posts.length };
  let o = openDialog(r.m, 'F1');
  out.normal = dsnap(r.m);
  r.m.board.handleKey({ key: 'Escape', target: dialog(r.m) });
  out.escHandleKey = { open: Boolean(dialog(r.m)), focusBack: focusOnButton(r, 'F1'), posts: r.posts.length };
  openDialog(r.m, 'F1');
  dialog(r.m).dispatchEvent(new Ev('keydown', { key: 'Escape' }));
  out.escKeydown = { open: Boolean(dialog(r.m)), focusBack: focusOnButton(r, 'F1') };
  openDialog(r.m, 'F1');
  dialog(r.m).querySelector('button.fb-cancel').click();
  out.cancel = { open: Boolean(dialog(r.m)), focusBack: focusOnButton(r, 'F1'), posts: r.posts.length };

  });
  // 2. generic draft: the warning and the extra checkbox
  await step('generic', async () => {
  r = mk(waiting);
  openDialog(r.m, 'F2');
  out.generic = dsnap(r.m);
  confirmBtn(r.m).dispatchEvent(new Ev('click'));
  out.genericDirect = { posts: r.posts.length };
  tickAck(r.m, true);
  out.genericAcked = { disabled: confirmBtn(r.m).disabled };
  tickAck(r.m, false);
  out.genericUnacked = { disabled: confirmBtn(r.m).disabled };
  confirmBtn(r.m).dispatchEvent(new Ev('click'));
  out.genericUnackedClick = { posts: r.posts.length };
  tickAck(r.m, true);
  confirmBtn(r.m).click();
  await tick3();
  out.genericPost = { posts: r.posts };

  });
  // 3. success, the exact request, refresh, no second post on a double click while the request is pending
  await step('pending', async () => {
  r = mk(waiting, { post: () => new Promise((res) => { release = res; }) });
  openDialog(r.m, 'F1');
  confirmBtn(r.m).click();
  confirmBtn(r.m) && confirmBtn(r.m).dispatchEvent(new Ev('click'));
  confirmBtn(r.m) && confirmBtn(r.m).dispatchEvent(new Ev('click'));
  out.pending = { posts: r.posts.length, disabled: confirmBtn(r.m) ? confirmBtn(r.m).disabled : null, refresh: r.m.calls.refresh };
  release({ ok: true, already: false, sha12: input.sha1.slice(0, 12) });
  await tick3();
  out.saved = { posts: r.posts, refresh: r.m.calls.refresh, result: result(r.m), open: Boolean(dialog(r.m)) };

  });
  // 4. already: the same message
  await step('already', async () => {
  r = mk(waiting, { post: () => Promise.resolve({ ok: true, already: true, sha12: input.sha1.slice(0, 12) }) });
  openDialog(r.m, 'F1'); confirmBtn(r.m).click(); await tick3();
  out.already = { result: result(r.m), posts: r.posts.length, refresh: r.m.calls.refresh };

  });
  // 5. refusal from the server: the error text as text; the button is usable again; after a refresh with `stale` there is no button
  await step('refused', async () => {
  r = mk(waiting, { post: () => Promise.resolve({ ok: false, text: evil }) });
  openDialog(r.m, 'F1'); confirmBtn(r.m).click(); await tick3();
  out.refused = { result: result(r.m), hostile: hostile(r.m), posts: r.posts.length };
  if (!dialog(r.m)) openDialog(r.m, 'F1');
  out.refusedAgain = { disabled: confirmBtn(r.m).disabled };
  confirmBtn(r.m).click(); await tick3();
  out.refusedRetry = { posts: r.posts.length };
  r.m.board.update((() => { const j = JSON.parse(JSON.stringify(r.json)); j.feedback = stale; return j; })());
  out.afterStale = { button: Boolean(card(r.m, 'F1').querySelector('button.fb-approve')), dialog: Boolean(dialog(r.m)), state: card(r.m, 'F1').getAttribute('data-state') };

  });
  // 6. the request itself fails (no connection): no exception escapes, the user can try again
  await step('rejected', async () => {
  r = mk(waiting, { post: () => Promise.reject(new Error('boom')) });
  openDialog(r.m, 'F1'); confirmBtn(r.m).click(); await tick3();
  out.rejected = { result: result(r.m), posts: r.posts.length };
  if (!dialog(r.m)) openDialog(r.m, 'F1');
  out.rejectedAgain = { disabled: confirmBtn(r.m).disabled };

  });
  // 7. no feedbackApprove action in the host: the page must not offer a button that cannot work
  await step('noAction', async () => {
  const bare = mount(j, { now: Date.parse(j.generatedAt), actions: { feedbackApprove: undefined } });
  out.noAction = { buttons: bare.root.querySelectorAll('button.fb-approve').length, control: mk(waiting).m.root.querySelectorAll('button.fb-approve').length };
  });
"""


@SKIP
class DialogAndPost(Needs):
    @classmethod
    def setUpClass(cls):
        cls.sha1, cls.sha2 = sha("F1"), sha("F2")
        cls.out = run_node(DIALOG_TAIL, payload(sha1=cls.sha1, sha2=cls.sha2))

    def test_clicking_the_button_opens_the_dialog_without_sending_anything(self):
        self.assertEqual(self.need("beforeOpen"), {"dialog": False, "posts": 0})
        d = self.need("normal")
        self.assertIsNotNone(d)
        self.assertIn(self.sha1[:12], d["sha"])
        self.assertIn("F1", d["draft"])
        for part in ("Org One", "Proj One"):
            self.assertIn(part, d["target"])
        self.assertTrue(d["mode"])
        self.assertTrue(d["confirm"]["text"] and d["cancel"])
        self.assertFalse(d["confirm"]["disabled"], "a normal draft needs no extra checkbox")
        self.assertFalse(d["ack"])
        self.assertIsNone(d["warning"])

    def test_the_dialog_is_modal_named_focused_and_has_a_live_region(self):
        d = self.need("normal")
        self.assertEqual(d["modal"], "true")
        self.assertTrue(d["name"], "role=dialog needs a name")
        self.assertTrue(d["focusInside"], "the focus moves into the dialog")

    def test_escape_and_cancel_close_it_return_the_focus_and_send_nothing(self):
        for name in ("escHandleKey", "escKeydown", "cancel"):
            with self.subTest(way=name):
                self.assertFalse(self.need(name)["open"])
                self.assertTrue(self.need(name)["focusBack"], "the focus returns to the button that opened the dialog")
        self.assertEqual((self.need("escHandleKey")["posts"], self.need("cancel")["posts"]), (0, 0))

    def test_a_generic_draft_shows_the_warning_and_needs_the_checkbox(self):
        d = self.need("generic")
        self.assertIn("nazwy klientów nie są sprawdzane", d["warning"].lower())
        self.assertTrue(d["ack"])
        self.assertTrue(d["confirm"]["disabled"], "without the checkbox the consent is inactive")
        self.assertEqual(self.need("genericDirect")["posts"], 0, "a forced click on the inactive button sends nothing")
        self.assertFalse(self.need("genericAcked")["disabled"])
        self.assertTrue(self.need("genericUnacked")["disabled"], "clearing the checkbox disables it again")
        self.assertEqual(self.need("genericUnackedClick")["posts"], 0)
        self.assertEqual(self.need("genericPost")["posts"], [{"draft": "F2", "sha": self.sha2}])
        self.assertIn("Org Two", d["target"])
        self.assertIn("#77", d["mode"])

    def test_the_request_carries_exactly_the_draft_and_the_full_sha(self):
        self.assertEqual(self.need("saved")["posts"], [{"draft": "F1", "sha": self.sha1}])
        self.assertEqual(len(self.need("saved")["posts"][0]["sha"]), 64)

    def test_a_double_click_while_the_request_is_pending_sends_it_once(self):
        self.assertEqual(self.need("pending")["posts"], 1)
        self.assertEqual(self.need("saved")["posts"].__len__(), 1)

    def test_success_and_already_say_that_the_consent_is_saved_and_refresh_the_state(self):
        for name in ("saved", "already"):
            with self.subTest(case=name):
                self.assertIn("zgoda zapisana", (self.need(name)["result"] or "").lower())
                self.assertEqual(self.need(name)["refresh"], 1)
        self.assertEqual(self.need("already")["posts"], 1)
        self.assertFalse(self.need("saved")["open"], "the dialog closes after the consent")

    def test_a_refusal_shows_the_server_text_as_text_and_lets_the_user_go_on(self):
        self.assertIn("Draft changed <img src=x onerror=alert(9)> reload", self.need("refused")["result"])
        self.assertEqual(self.need("refused")["hostile"], [])
        self.assertFalse(self.need("refusedAgain")["disabled"], "the button is active again")
        self.assertEqual(self.need("refusedRetry")["posts"], 2)
        self.assertEqual(self.need("afterStale"), {"button": False, "dialog": False, "state": "stale"})

    def test_a_failed_request_is_reported_and_does_not_lock_the_button(self):
        self.assertTrue(self.need("rejected")["result"])
        self.assertNotIn("zapisana", self.need("rejected")["result"].lower())
        self.assertFalse(self.need("rejectedAgain")["disabled"])

    def test_a_host_without_the_action_offers_no_button(self):
        self.assertEqual(self.need("noAction")["control"], 2, "control: with the action the same state has its buttons (the zero below means something)")
        self.assertEqual(self.need("noAction")["buttons"], 0)


# ------------------------------------------------------------------------------------------------------------------ page adapter (app.js)
APP_HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
const sc = JSON.parse(process.argv[3]);
const storage = Object.assign({}, sc.storage);
const calls = { fetch: [] };
const location = { hash: '', pathname: '/', search: '', protocol: 'http:', hostname: '127.0.0.1' };
const ctx = {
  location, history: { replaceState() {} },
  localStorage: { getItem: (k) => (k in storage ? storage[k] : null), setItem: (k, v) => { storage[k] = String(v); }, removeItem: (k) => { delete storage[k]; } },
  document: { getElementById: () => ({}), addEventListener() {}, documentElement: { dataset: {} }, visibilityState: 'visible', title: '' },
  matchMedia: () => ({ matches: false, addEventListener() {} }), setInterval: () => 0, clearInterval() {}, setTimeout: () => 0, console,
  fetch: (url, opts) => {
    calls.fetch.push({ url, method: opts && opts.method, headers: (opts && opts.headers) || null, body: opts && opts.body });
    if (url === '/api/feedback-approve') return sc.reject ? Promise.reject(new Error('down')) : Promise.resolve({ ok: sc.status === 200, status: sc.status, json: () => Promise.resolve(sc.body) });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ plans: [] }) });
  },
};
ctx.window = ctx; ctx.addEventListener = () => {};
ctx.DashboardRenderer = { create: (el, deps) => { ctx.deps = deps; return new Proxy({}, { get: () => () => {} }); } };
vm.createContext(ctx);
vm.runInContext(src, ctx);
(async () => {
  const action = ctx.deps.actions.feedbackApprove;
  const res = typeof action === 'function' ? await action({ draft: 'F3', sha: sc.sha }) : 'NO-ACTION';
  await new Promise((r) => setImmediate(r));
  const call = calls.fetch.find((c) => c.url === '/api/feedback-approve') || null;
  console.log(JSON.stringify({ res, call, storage, statusFetches: calls.fetch.filter((c) => c.url.startsWith('/api/status')).length }));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@SKIP
class PageAdapter(unittest.TestCase):
    SHA = sha("F3")

    def run_app(self, **scenario):
        base = {"storage": {}, "status": 200, "sha": self.SHA, "body": {"ok": True, "draft": "F3", "sha12": self.SHA[:12], "approval": "a1", "already": False}}
        with tempfile.TemporaryDirectory(prefix="fb-app-") as tmp:
            harness, page = Path(tmp) / "harness.js", Path(tmp) / "app.js"
            harness.write_text(APP_HARNESS, encoding="utf-8")
            page.write_text(ADAPTER.read_text(encoding="utf-8"), encoding="utf-8")
            done = subprocess.run([NODE, str(harness), str(page), json.dumps({**base, **scenario})], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        self.assertNotEqual(out["res"], "NO-ACTION", "app.js gives the renderer no actions.feedbackApprove")
        return out

    def test_the_post_goes_to_the_route_with_the_headers_of_the_consent_buttons(self):
        out = self.run_app(storage={"dashboard:approveToken": TOKEN})
        call = out["call"]
        self.assertIsNotNone(call, out["res"])
        self.assertEqual(call["method"], "POST")
        self.assertEqual(json.loads(call["body"]), {"draft": "F3", "sha": self.SHA})
        self.assertEqual(call["headers"]["Content-Type"], "application/json")
        self.assertEqual(call["headers"]["X-Status-Board"], "1")
        self.assertEqual(call["headers"]["X-Approve-Token"], TOKEN)
        self.assertEqual((out["res"]["ok"], out["res"]["already"]), (True, False))

    def test_without_a_token_no_token_header_is_sent(self):
        self.assertNotIn("X-Approve-Token", self.run_app()["call"]["headers"])

    def test_already_is_passed_on(self):
        out = self.run_app(body={"ok": True, "draft": "F3", "sha12": self.SHA[:12], "approval": "a1", "already": True})
        self.assertEqual((out["res"]["ok"], out["res"]["already"]), (True, True))

    def test_a_server_error_comes_back_as_its_text(self):
        for status, code in ((409, "content_changed"), (403, "forbidden")):
            with self.subTest(status=status):
                out = self.run_app(status=status, body={"error": "Server says no", "code": code})
                self.assertEqual((out["res"]["ok"], out["res"]["text"]), (False, "Server says no"))

    def test_a_refused_token_is_forgotten_as_for_the_other_consents(self):
        out = self.run_app(storage={"dashboard:approveToken": TOKEN}, status=403, body={"error": "Bad token", "code": "approve_token"})
        self.assertEqual(out["storage"], {})
        self.assertGreaterEqual(out["statusFetches"], 2, "the page refreshes so that the buttons become inactive")

    def test_no_connection_is_a_failed_result_with_a_text(self):
        out = self.run_app(reject=True)
        self.assertFalse(out["res"]["ok"])
        self.assertTrue(out["res"]["text"])


# ------------------------------------------------------------------------------------------------------------------ catalog and CSS
FB_KEY = re.compile(r'"(ui\.feedback\.[A-Za-z0-9_.]+)"')


class Catalog(unittest.TestCase):
    pl, en = i18n.catalog_for_client("pl"), i18n.catalog_for_client("en")

    @staticmethod
    def forms(value):
        return list(value.values()) if isinstance(value, dict) else [value]

    def keys(self, catalog):
        return {key for key in catalog if key.startswith("ui.feedback.")}

    def test_new_keys_exist_in_both_languages_with_the_same_names_and_parameters(self):
        pl, en = self.keys(self.pl), self.keys(self.en)
        self.assertGreaterEqual(len(pl), 12, "title, tile texts, states, dialog, generic warning, result messages")
        self.assertEqual(sorted(pl ^ en), [])
        for key in sorted(pl):
            with self.subTest(key=key):
                self.assertEqual(sorted(set(re.findall(r"\{\w+\}", " ".join(self.forms(self.pl[key]))))),
                                 sorted(set(re.findall(r"\{\w+\}", " ".join(self.forms(self.en[key]))))))

    def test_the_two_languages_say_it_differently(self):
        self.assertTrue(self.keys(self.pl), "no ui.feedback.* keys yet")
        for key in sorted(self.keys(self.pl)):
            with self.subTest(key=key):
                self.assertNotEqual(self.pl[key], self.en[key], "an untranslated text")

    def test_the_agreed_texts_are_in_the_catalog(self):
        pl = " ".join(text for key in self.keys(self.pl) for text in self.forms(self.pl[key]))
        for phrase in ("CYTAT Z TRACKERA", "nazwy klientów nie są sprawdzane", "podgląd nieaktualny", "zgoda dana", "zgoda zapisana", "Zgoda", "Anuluj"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase.lower(), pl.lower())

    def test_the_renderer_uses_every_key_and_only_existing_ones(self):
        src = RENDERER.read_text(encoding="utf-8")
        outside = re.sub(r"// <i18n-defaults>.*?// </i18n-defaults>", "", src, flags=re.S)
        used = set(FB_KEY.findall(outside))
        for key in sorted(self.keys(self.pl)):
            with self.subTest(key=key):
                self.assertIn(key, used, "the text is shown by no code (or built by concatenation instead of a key)")
        self.assertEqual(sorted(used - self.keys(self.pl)), [], "a key used by the renderer that the catalog does not have")
        self.assertTrue(used, "the renderer does not use the feedback texts yet")

    def test_the_builtin_polish_block_has_the_keys(self):
        self.assertTrue(self.keys(self.pl), "no ui.feedback.* keys yet")
        src = RENDERER.read_text(encoding="utf-8")
        block = src[src.index("// <i18n-defaults>"):src.index("// </i18n-defaults>")]
        self.assertEqual(sorted(set(re.findall(r'^    "(ui\.feedback\.[^"]+)":', block, re.M))), sorted(self.keys(self.pl)))


class Css(unittest.TestCase):
    css = re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"), flags=re.S)

    def fb_rules(self):
        return [(sel.strip(), body) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", self.css) if ".fb-" in sel or "data-feedback" in sel]

    def test_the_section_has_styles_for_its_parts(self):
        selectors = " ".join(sel for sel, _ in self.fb_rules())
        for part in (".fb-card", ".fb-body", ".fb-approve", ".fb-dialog", ".fb-result"):
            with self.subTest(part=part):
                self.assertIn(part, selectors)

    def test_colours_come_from_the_theme_tokens_not_from_literals(self):
        rules = self.fb_rules()
        self.assertTrue(rules)
        for selector, body in rules:
            with self.subTest(selector=selector):
                self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(", body), [], "a literal colour: use a token such as var(--panel)")
                for prop, value in re.findall(r"([\w-]+)\s*:\s*([^;]+)", body):
                    if prop in ("color", "background", "background-color", "border-color"):
                        self.assertTrue("var(--" in value or value.strip() in ("inherit", "transparent", "currentColor", "none"), f"{prop}: {value}")

    def test_the_tokens_used_exist_in_both_themes(self):
        light = re.search(r":root\s*\{([^{}]*)\}", self.css).group(1)
        dark = re.search(r':root\[data-theme="dark"\]\s*\{([^{}]*)\}', self.css).group(1)
        defined = set(re.findall(r"(--[\w-]+)\s*:", light))
        used = {tok for _, body in self.fb_rules() for tok in re.findall(r"var\((--[\w-]+)", body)}
        self.assertEqual(sorted(used - defined), [], "a token that the light theme does not define")
        self.assertTrue(used, "the section uses the theme tokens")
        self.assertTrue(dark, "the dark block exists")

    def test_the_dialog_and_the_buttons_have_a_touch_target_in_the_compact_mode(self):
        block = self.css[self.css.index("@container dash (max-width: 420px)"):]
        self.assertTrue(re.search(r"\.fb-approve|\.fb-confirm|\.fb-cancel", block), "compact mode: touch targets of the new buttons (min-height 2.75rem)")


if __name__ == "__main__":
    unittest.main()
