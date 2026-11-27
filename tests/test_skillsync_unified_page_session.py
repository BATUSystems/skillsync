"""CORE-2000-UNIFIED U-4.1: the status page signs in with the session of its origin (criteria K3, K5, K7, "History" buttons):
python3 -m unittest tests.test_skillsync_unified_page_session

The REAL page (`app.js` + `dashboard-render.js` of the library, on the shadow DOM of the renderer tests, in node) talks over real HTTP to a REAL
`skillsync dashboard serve` process (`unified_helpers`). Every header and every body field is exactly what the page sends; the harness adds only what a
browser adds itself (`Host`, the cookie jar, and `Origin` with `Sec-Fetch-Site: same-origin` on a write). Two things are not real, and they are stated
where used: `fakeTab` adds a second row to the answer of `/api/tabs` (the History tab exists only with two tabs), and `stubs` answers one route with a
refusal the server only gives in a race (`tabChanged`)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unified_helpers import (  # noqa: E402
    APPROVER, APPROVER_ACCOUNT, APPROVER_NEW_PASSWORD, APPROVER_PASSWORD, BRANCH, COMMENTER, COMMENTER_PASSWORD, DONE_PLAN, HEAD, LAYER, PLAN, RUN2_ID,
    RUN_ID, SPEC2, UnifiedCase, descend)
from tests.test_skillsync_dashboard_renderer import ADAPTER, HARNESS, NODE, NODE_MAJOR, RENDERER, fixture_files  # noqa: E402
from tests.test_skillsync_dashboard_renderer_i18n import run_node  # noqa: E402

# the shadow DOM of the renderer test (it forbids innerHTML and inline handlers) without its scenarios
DOM = HARNESS[:HARNESS.index("const tick = ")]
FAKE_TAB = "0123456789ab"
OLD_KEYS = ("dashboard:approveToken", "dashboard:approveToken:main", "status-board:approveToken")
STALE = "-".join(("old", "consent", "value", "0123456789abcdef"))

PAGE = r"""
const vm = require('vm'), http = require('http');
// `app.js` has one HTML sink (`mountHtml`, for the Markdown the server rendered into a preview box); every other element still refuses HTML
Object.defineProperty(E.prototype, 'innerHTML', { set(v) { if (!/(^| )(wh-doc|md)( |$)/.test(this.getAttribute('class') || '')) bad('innerHTML'); this.mounted = String(v); }, get() { return bad('innerHTML read'); } });
const sc = input;
const doc = makeDoc();
const body = doc.createElement('body');
doc.root = body;
const byId = {};
for (const id of ['wtabs', 'whistory', 'app']) { const el = doc.createElement('div'); el.setAttribute('id', id); body.appendChild(el); byId[id] = el; }
byId.wtabs.hidden = true; byId.whistory.hidden = true;
const docListeners = {}, winListeners = {};
Object.assign(doc, { getElementById: (id) => byId[id] || null, addEventListener: (n, f) => { docListeners[n] = f; }, visibilityState: 'visible', title: '',
  documentElement: { dataset: {}, lang: '', style: { setProperty() {} } } });
const storage = Object.assign({}, sc.storage);
const localStorage = { getItem: (k) => (Object.prototype.hasOwnProperty.call(storage, k) ? storage[k] : null), setItem: (k, v) => { storage[k] = String(v); },
  removeItem: (k) => { delete storage[k]; }, key: (i) => Object.keys(storage)[i] ?? null, get length() { return Object.keys(storage).length; } };
const location = { hash: sc.hash || '', pathname: '/', search: sc.search || '', protocol: 'http:', hostname: '127.0.0.1', assigned: [], assign(u) { this.assigned.push(u); } };
const history = { replaceState: (a, b, url) => { const u = new URL(url, 'http://x'); location.pathname = u.pathname; location.search = u.search; location.hash = u.hash; }, pushState: (a, b, url) => history.replaceState(a, b, url) };
let cookie = sc.cookie || '';
const requests = [];
const stubs = {};
function request(url, opts) {
  opts = opts || {};
  const method = opts.method || 'GET';
  const path = url.split('?')[0];
  const sent = Object.assign({}, opts.headers || {});
  const entry = { method, url, headers: sent, body: opts.body ? JSON.parse(opts.body) : null, status: 0, answer: null };
  requests.push(entry);
  if (stubs[path]) { const s = stubs[path]; entry.status = s.status; return Promise.resolve({ ok: false, status: s.status, json: () => Promise.resolve(s.body) }); }
  const headers = Object.assign({ Host: '127.0.0.1:' + sc.port }, sent);
  if (cookie) headers.Cookie = cookie;
  if (method !== 'GET' && method !== 'HEAD') { headers.Origin = 'http://127.0.0.1:' + sc.port; headers['Sec-Fetch-Site'] = 'same-origin'; headers['Content-Length'] = Buffer.byteLength(opts.body || ''); }
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port: sc.port, method, path: url, headers, agent: false }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        for (const line of res.headers['set-cookie'] || []) { const pair = line.split(';')[0]; cookie = /Max-Age=0/.test(line) ? '' : pair; }
        let data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
        if (data && sc.fakeTab && path === '/api/tabs' && Array.isArray(data.tabs)) data.tabs.push({ id: sc.fakeTab, name: 'second', branch: '', kind: 'worktree' });
        entry.status = res.statusCode; entry.answer = data;
        resolve({ ok: res.statusCode >= 200 && res.statusCode < 300, status: res.statusCode, json: () => (data === null && text ? Promise.reject(new Error('json')) : Promise.resolve(data)) });
      });
    });
    req.on('error', reject);
    req.end(opts.body || undefined);
  });
}
const intervals = [];
const ctx = { console, document: doc, localStorage, location, history, fetch: request, DashboardRenderer: R, encodeURIComponent, decodeURIComponent, URLSearchParams,
  matchMedia: () => ({ matches: false, addEventListener() {} }), setInterval: (f, ms) => { intervals.push([ms, f]); return intervals.length; }, clearInterval() {},
  setTimeout: (f, ms) => setTimeout(f, ms), clearTimeout: (t) => clearTimeout(t), scrollTo() {}, scrollY: 0, innerHeight: 800,
  requestAnimationFrame: (f) => { f(); return 0; }, cancelAnimationFrame() {}, addEventListener: (n, f) => { winListeners[n] = f; }, removeEventListener() {} };
ctx.window = ctx;
// the renderer is loaded outside the sandbox: it gets the frame callbacks of the page there too
global.requestAnimationFrame = ctx.requestAnimationFrame; global.cancelAnimationFrame = ctx.cancelAnimationFrame;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(sc.app, 'utf8'), ctx);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const clean = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : null);
const find = (op) => byId[op.root || 'app'].querySelectorAll(op.selector).__asArray().find((el) => !op.text || el.textContent.includes(op.text)) || null;
function snapshot() {
  const app = byId.app;
  const all = (sel) => app.querySelectorAll(sel).__asArray();
  const dialog = app.querySelector('.dlg-modal');
  const rows = {};
  if (dialog) for (const dd of dialog.querySelectorAll('dd').__asArray()) rows[dd.getAttribute('data-consent')] = clean(dd);
  return { account: clean(app.querySelector('#account')), bar: clean(app.querySelector('#sessionBar')), banner: clean(app.querySelector('#waitBanner')),
    gates: all('button[data-gate]').map((b) => ({ gate: b.getAttribute('data-gate'), state: b.getAttribute('data-state'), disabled: b.disabled })),
    dialog: dialog ? { name: dialog.getAttribute('data-dialog'), text: clean(dialog), rows, passwordFields: dialog.querySelectorAll('input[type="password"]').length,
      focused: doc.focused ? clean(doc.focused) : null } : null,
    history: clean(byId.whistory), assigned: location.assigned.slice(), hash: location.hash, storage: Object.assign({}, storage) };
}
(async () => {
  const out = { shots: {}, requests, failed: null };
  for (const op of sc.steps) {
    if (op.op === 'wait' || op.op === 'gone') {
      const end = Date.now() + 30000;   // generous: the station may be loaded by other builds
      const ready = () => (op.status ? requests.some((r) => r.url.startsWith(op.status[0]) && r.status === op.status[1]) : Boolean(find(op)) === (op.op === 'wait'));
      while (!ready() && Date.now() < end) await sleep(40);
      if (!ready()) { out.failed = { op, shot: snapshot() }; break; }
    } else if (op.op === 'click') {
      const el = find(op);
      if (!el || el.disabled) { out.failed = { op, shot: snapshot() }; break; }
      el.click();
    } else if (op.op === 'type') { find(op).value = op.value; }
    else if (op.op === 'sleep') { await sleep(op.ms); }
    else if (op.op === 'write') { fs.writeFileSync(op.path, op.text); }
    else if (op.op === 'popstate') { history.replaceState(null, '', op.url); winListeners.popstate(); }
    else if (op.op === 'tick') { for (const [ms, f] of intervals.slice()) if (ms === op.ms) f(); }
    else if (op.op === 'stub') { stubs[op.path] = { status: op.status_, body: op.body }; }
    else if (op.op === 'unstub') { delete stubs[op.path]; }
    else if (op.op === 'logoutOutside') {   // the session ends elsewhere (another tab, idle): the page does not know yet
      const s = await request('/_api/v1/session');
      const csrf = (await s.json()).csrf;
      await request('/_api/v1/logout', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Skillsync-CSRF': csrf }, body: '{}' });
      requests.splice(-2);
    } else if (op.op === 'shot') { await sleep(150); out.shots[op.name] = snapshot(); }
  }
  out.violations = violations; out.cookie = cookie;
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
})().catch((e) => { process.stdout.write(JSON.stringify({ failed: String(e && e.stack || e) }), () => process.exit(0)); });
"""


def wait(selector, **extra):
    return {"op": "wait", "selector": selector, **extra}


def click(selector, **extra):
    return {"op": "click", "selector": selector, **extra}


def answered(url, status):
    return {"op": "wait", "selector": "-", "status": [url, status]}


def shot(name):
    return {"op": "shot", "name": name}


@unittest.skipUnless(NODE_MAJOR >= 18, "set SKILLSYNC_TEST_NODE (or have node on PATH), node >= 18")
class PageCase(UnifiedCase):
    ACCOUNTS = ((APPROVER, "approver", APPROVER_PASSWORD, APPROVER_ACCOUNT), (COMMENTER, "commenter", COMMENTER_PASSWORD, None))

    def page(self, server, steps, cookie: str = "", **scenario) -> dict:
        harness, data = self.tmp / "page.cjs", self.tmp / "page.json"
        harness.write_text(DOM + PAGE, encoding="utf-8")
        data.write_text(json.dumps({"app": str(ADAPTER), "port": server.port, "cookie": cookie, "steps": steps, **scenario}), encoding="utf-8")
        done = subprocess.run([NODE, str(harness), str(RENDERER), str(data)], capture_output=True, text=True, encoding="utf-8", timeout=120,
                              cwd=str(self.tmp), env=dict(os.environ))
        self.assertEqual(done.returncode, 0, done.stderr[-3000:])
        out = json.loads(done.stdout)
        self.assertIsNone(out["failed"], json.dumps(out["failed"], ensure_ascii=False)[:3000])
        self.assertEqual(out["violations"], [])
        return out

    def signed_in(self, server, login: str = APPROVER, password: str = APPROVER_PASSWORD):
        browser = server.browser()
        reply = browser.login(login, password)
        self.assertEqual(reply.status, 200, reply.text)
        return browser

    @staticmethod
    def writes(out, path):
        return [item for item in out["requests"] if item["method"] == "POST" and item["url"] == path]

    def assert_page_headers(self, call, csrf) -> None:
        self.assertEqual(call["headers"], {"Content-Type": "application/json", "X-Status-Board": "1", "X-Skillsync-CSRF": csrf})


class AnonymousPageTests(PageCase):
    def test_an_anonymous_page_hides_the_consent_buttons_asks_to_sign_in_and_forgets_the_old_token(self) -> None:
        self.spec_waits_for_go_plan()
        server = self.serve()
        out = self.page(server, [wait("#sessionBar button[data-session=\"login\"]"), shot("start"),
                                 click("#sessionBar button[data-session=\"login\"]"), shot("clicked")],
                        hash="#zgoda=" + STALE, storage={**{key: STALE for key in OLD_KEYS}, "dashboard:theme": "dark"})
        start = out["shots"]["start"]
        self.assertEqual(start["gates"], [], "no consent button without a session")
        self.assertIn("Zaloguj się, aby zatwierdzać.", start["bar"])
        self.assertIn("już nie działa", start["bar"])
        self.assertIn("odczyt bez logowania", start["account"])
        self.assertEqual(start["hash"], "")
        self.assertEqual(start["storage"], {"dashboard:theme": "dark"}, "every old token is removed, other settings stay")
        self.assertEqual(out["shots"]["clicked"]["assigned"], ["/_login?next=%2F"])
        self.assertNotIn(STALE, json.dumps(out["requests"]))
        self.assertEqual([item for item in out["requests"] if item["method"] != "GET"], [])
        self.assertEqual(server.browser().get("/_login?next=%2F").status, 200, "the page the button opens exists on this origin")

    def test_a_commenter_sees_the_buttons_inactive_with_the_reason(self) -> None:
        self.spec_waits_for_go_plan()
        server = self.serve()
        browser = self.signed_in(server, COMMENTER, COMMENTER_PASSWORD)
        out = self.page(server, [wait("button[data-gate=\"spec\"]"), wait("#account button[data-session=\"logout\"]"), shot("start")], cookie=browser.cookie)
        start = out["shots"]["start"]
        self.assertTrue(start["gates"])
        self.assertTrue(all(gate["disabled"] for gate in start["gates"]), start["gates"])
        self.assertIn("Zgody wymagają roli approver. Twoje role: commenter.", start["bar"])
        self.assertIn("commenter", start["account"])


class ConsentPageTests(PageCase):
    def test_go_plan_opens_the_window_without_a_password_and_the_server_accepts_what_the_page_sends(self) -> None:
        self.spec_waits_for_go_plan()
        server = self.serve()
        browser = self.signed_in(server)
        sha = self.blob_sha(SPEC2)
        out = self.page(server, [wait("button[data-approve=\"spec\"]"), click("button[data-approve=\"spec\"]"), shot("window"),
                                 click("button[data-consent-cancel]"), shot("cancelled"), click("button[data-approve=\"spec\"]"),
                                 click("button[data-consent-confirm]"), answered("/api/approve", 200), wait(".wait-item.is-ok"), shot("done")],
                        cookie=browser.cookie)
        window = out["shots"]["window"]["dialog"]
        self.assertEqual((window["name"], window["passwordFields"], window["focused"]), ("consent", 0, "Anuluj"))
        self.assertEqual({key: window["rows"][key] for key in ("word", "sha", "target", "mode")}, {"word": "GO plan", "sha": sha[:7], "target": BRANCH, "mode": "direct"})
        self.assertIn(SPEC2, window["rows"]["doc"])
        self.assertTrue(window["rows"]["tab"])
        self.assertIsNone(out["shots"]["cancelled"]["dialog"])
        calls = self.writes(out, "/api/approve")
        self.assertEqual(len(calls), 1, "cancel sends nothing")
        self.assert_page_headers(calls[0], browser.csrf)
        self.assertEqual({key: calls[0]["body"][key] for key in ("kind", "path", "sha", "branch", "target", "mode")},
                         {"kind": "spec", "path": SPEC2, "sha": sha, "branch": BRANCH, "target": BRANCH, "mode": "direct"})
        self.assertEqual((calls[0]["status"], calls[0]["answer"]["word"]), (200, "GO plan"))
        self.assertEqual([(row["word"], row["by"], row["session"]) for row in self.rows(RUN2_ID)], [("GO plan", APPROVER_ACCOUNT, browser.digest)])

    def test_cp_with_a_changed_delivery_target_is_refused_explained_and_given_after_the_refresh(self) -> None:
        self.session_waits_for("CP")
        server = self.serve()
        browser = self.signed_in(server)
        plan = descend(self.project, PLAN)
        moved = plan.read_text(encoding="utf-8").replace("\n## ", "\nOddanie: main\n\n## ", 1)
        out = self.page(server, [wait("button[data-approve=\"cp\"]"), click("button[data-approve=\"cp\"]"), shot("window"),
                                 {"op": "write", "path": str(plan), "text": moved}, {"op": "sleep", "ms": 2500},
                                 click("button[data-consent-confirm]"), answered("/api/approve", 409), wait(".wait-item.is-err"), shot("refused"),
                                 {"op": "sleep", "ms": 500}, click("button[data-approve=\"cp\"]"), shot("again"), click("button[data-consent-confirm]"),
                                 answered("/api/approve", 200), shot("done")], cookie=browser.cookie)
        self.assertEqual(out["shots"]["window"]["dialog"]["rows"]["target"], BRANCH)
        self.assertIn("Gałąź, cel albo tryb dostarczania zmieniły się", out["shots"]["refused"]["banner"])
        self.assertEqual(out["shots"]["again"]["dialog"]["rows"]["target"], "main", "the page was refreshed: the window shows the new target")
        first, second = self.writes(out, "/api/approve")
        self.assertEqual((first["status"], first["answer"]["code"], first["body"]["target"]), (409, "delivery_changed", BRANCH))
        self.assertEqual((second["status"], second["body"]["target"], second["body"]["sha"]), (200, "main", HEAD))
        after = out["requests"][out["requests"].index(first) + 1:]
        self.assertTrue(any(item["url"].startswith("/api/status") for item in after))
        self.assertEqual([(row["word"], row["target"], row["by"]) for row in self.rows()], [("CP", "main", APPROVER_ACCOUNT)])

    def test_a_session_that_ended_elsewhere_gives_nothing_and_the_page_says_to_sign_in_again(self) -> None:
        self.spec_waits_for_go_plan()
        server = self.serve()
        browser = self.signed_in(server)
        out = self.page(server, [wait("button[data-approve=\"spec\"]"), click("button[data-approve=\"spec\"]"), {"op": "logoutOutside"},
                                 click("button[data-consent-confirm]"), answered("/api/approve", 401),
                                 wait("#sessionBar button[data-session=\"login\"]"), shot("expired")], cookie=browser.cookie)
        expired = out["shots"]["expired"]
        self.assertIn("Sesja wygasła, zaloguj się ponownie.", expired["bar"])
        self.assertEqual(expired["gates"], [])
        self.assertEqual(self.rows(RUN2_ID), [])

    def test_tab_changed_is_explained_and_the_status_and_the_tabs_are_read_again(self) -> None:
        self.spec_waits_for_go_plan()
        server = self.serve()
        browser = self.signed_in(server)
        stub = {"op": "stub", "path": "/api/approve", "status_": 409, "body": {"code": "tabChanged", "error": "x"}}   # a race on the server, stubbed
        out = self.page(server, [wait("button[data-approve=\"spec\"]"), click("button[data-approve=\"spec\"]"), stub,
                                 click("button[data-consent-confirm]"), wait(".wait-item.is-err"), {"op": "sleep", "ms": 500}, shot("refused")],
                        cookie=browser.cookie)
        self.assertIn("Worktree zakładki zmienił się", out["shots"]["refused"]["banner"])
        call = self.writes(out, "/api/approve")[0]
        after = [item["url"].split("?")[0] for item in out["requests"][out["requests"].index(call) + 1:]]
        self.assertIn("/api/status", after)
        self.assertIn("/api/tabs", after)

    def test_a_window_whose_document_changed_closes_without_sending(self) -> None:
        self.spec_waits_for_go_plan()
        server = self.serve()
        browser = self.signed_in(server)
        spec = descend(self.project, SPEC2)
        out = self.page(server, [wait("button[data-approve=\"spec\"]"), click("button[data-approve=\"spec\"]"),
                                 {"op": "write", "path": str(spec), "text": spec.read_text(encoding="utf-8") + "\nmore\n"}, {"op": "sleep", "ms": 2500},
                                 {"op": "tick", "ms": 5000}, wait(".wait-item.is-err"), shot("closed")], cookie=browser.cookie)
        self.assertIsNone(out["shots"]["closed"]["dialog"])
        self.assertIn("nic nie wysłano", out["shots"]["closed"]["banner"])
        self.assertEqual(self.writes(out, "/api/approve"), [])


    def test_an_open_window_does_not_survive_another_tab_back_or_the_history(self) -> None:
        self.spec_waits_for_go_plan()
        server = self.serve()
        browser = self.signed_in(server)
        main = server.browser().get("/api/tabs").data["current"]
        opened = [wait("button[data-approve=\"spec\"]"), click("button[data-approve=\"spec\"]"), shot("open")]
        ways = {"tab": [click(f"button[data-wt-tab=\"{FAKE_TAB}\"]", root="wtabs")],
                "back": [{"op": "popstate", "url": "/?tab=" + FAKE_TAB}],
                "history": [{"op": "popstate", "url": "/?tab=history"}, {"op": "popstate", "url": "/?tab=" + main}]}
        for name, way in ways.items():
            with self.subTest(way=name):
                out = self.page(server, [wait(f"button[data-wt-tab=\"{FAKE_TAB}\"]", root="wtabs"), *opened, *way, shot("after")], cookie=browser.cookie,
                                fakeTab=FAKE_TAB, search="?tab=" + main)
                self.assertEqual(out["shots"]["open"]["dialog"]["name"], "consent", "control: the window was open")
                self.assertIsNone(out["shots"]["after"]["dialog"])
                self.assertEqual(self.writes(out, "/api/approve"), [])
        self.assertEqual(self.rows(RUN2_ID), [])


class AccountPageTests(PageCase):
    def test_password_change_and_sign_out_through_the_page(self) -> None:
        self.spec_waits_for_go_plan()
        server = self.serve()
        browser = self.signed_in(server)
        field = lambda name, value: {"op": "type", "selector": f"input[data-pw=\"{name}\"]", "value": value}   # noqa: E731
        short = "x" * 12   # long enough for other accounts, too short for an approver
        out = self.page(server, [wait("#account button[data-session=\"password\"]"), click("#account button[data-session=\"password\"]"),
                                 field("current", APPROVER_PASSWORD), field("next", short), field("repeat", short), click("button[data-pw-save]"), shot("short"),
                                 field("next", APPROVER_NEW_PASSWORD), field("repeat", APPROVER_NEW_PASSWORD), click("button[data-pw-save]"),
                                 answered("/_api/v1/password", 204), {"op": "gone", "selector": ".dlg-modal"}, wait(".wait-item.is-ok"), shot("changed"),
                                 click("button[data-approve=\"spec\"]"), shot("still"), click("button[data-consent-cancel]"),
                                 click("#account button[data-session=\"logout\"]"), answered("/_api/v1/logout", 204),
                                 wait("#account button[data-session=\"login\"]"), shot("out")], cookie=browser.cookie)
        self.assertIn("co najmniej 14", out["shots"]["short"]["dialog"]["text"])
        self.assertEqual(len(self.writes(out, "/_api/v1/password")), 1, "the short password was never sent")
        change = self.writes(out, "/_api/v1/password")[0]
        self.assertEqual(change["body"], {"current": APPROVER_PASSWORD, "new": APPROVER_NEW_PASSWORD})
        self.assertEqual(change["headers"]["X-Skillsync-CSRF"], browser.csrf)
        self.assertIn("Hasło zmienione", out["shots"]["changed"]["banner"])
        self.assertIn("odczyt bez logowania", out["shots"]["out"]["account"])
        self.assertNotIn("Sesja wygasła", out["shots"]["out"]["bar"] or "", "a sign-out asked for is not an expiry")
        logout = self.writes(out, "/_api/v1/logout")[0]
        self.assertNotEqual(logout["headers"]["X-Skillsync-CSRF"], browser.csrf, "the token of the new session")
        self.assertEqual(out["cookie"], "")
        self.assertNotIn(APPROVER_NEW_PASSWORD, json.dumps(out["shots"]))
        self.assertEqual(out["shots"]["out"]["storage"], {})
        self.assertEqual(server.browser().login(APPROVER, APPROVER_NEW_PASSWORD).status, 200)
        self.assertEqual(server.browser().login(APPROVER, APPROVER_PASSWORD).status, 401)


    def test_a_login_link_session_of_an_approver_is_told_to_sign_in_with_the_password_and_knows_the_real_minimum(self) -> None:
        self.spec_waits_for_go_plan()
        server = self.serve()
        linked = server.browser()
        issued = self.cli("prototype", "login-link", str(self.project), "--user", APPROVER, "--port", str(server.port), *LAYER)
        self.assertEqual(issued.returncode, 0, issued.stdout + issued.stderr)
        url = json.loads(issued.stdout)["url"]
        followed = linked.get(url[url.index("/_login/link"):])
        self.assertEqual(followed.status, 302, followed.text)
        linked.cookie = followed.set_cookie.split(";")[0]
        facts = lambda browser: {key: browser.get("/_api/v1/session").data["server"].get(key) for key in ("passwordMin", "passwordSignIn")}   # noqa: E731
        self.assertEqual(facts(linked), {"passwordMin": 14, "passwordSignIn": True})
        self.assertEqual(facts(self.signed_in(server)), {"passwordMin": 14, "passwordSignIn": False})
        self.assertEqual(facts(self.signed_in(server, COMMENTER, COMMENTER_PASSWORD)), {"passwordMin": 10, "passwordSignIn": False})
        self.assertEqual(facts(server.browser()), {"passwordMin": None, "passwordSignIn": None}, "nothing about any account without a session")
        field = lambda name, value: {"op": "type", "selector": f"input[data-pw=\"{name}\"]", "value": value}   # noqa: E731
        short = "x" * 12
        out = self.page(server, [wait("#account button[data-session=\"password\"]"), wait("#sessionBar button[data-session=\"login\"]"), wait("button[data-gate]"), shot("bar"),
                                 click("#account button[data-session=\"password\"]"), field("current", APPROVER_PASSWORD), field("next", short),
                                 field("repeat", short), click("button[data-pw-save]"), shot("short")], cookie=linked.cookie)
        bar = out["shots"]["bar"]
        self.assertIn("zaloguj się hasłem", bar["bar"])
        self.assertTrue(bar["gates"])
        self.assertTrue(all(gate["disabled"] for gate in bar["gates"]), bar["gates"])
        self.assertIn("co najmniej 14", out["shots"]["short"]["dialog"]["text"])
        self.assertEqual(self.writes(out, "/_api/v1/password"), [])


class HistoryPageTests(PageCase):
    """The buttons of the History tab (release condition): the server accepts exactly the headers and fields the page sends."""

    def history_steps(self, name):
        return [wait("button[data-wt-tab=\"history\"]", root="wtabs"), click("button[data-wt-tab=\"history\"]", root="wtabs"),
                wait(".wh-row", root="whistory"), click(".wh-row", root="whistory", text=name)]

    def rows_of_audit(self):
        path = descend(self.project, ".skillsync/audit/plan-status.jsonl")
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_archive_and_restore_from_the_page_write_the_account_as_by(self) -> None:
        server = self.serve()
        browser = self.signed_in(server)
        confirm = [wait("button[data-ah-focus=\"act\"]", root="whistory"), click("button[data-ah-focus=\"act\"]", root="whistory"),
                   click("button[data-ah-focus=\"confirm\"]", root="whistory")]
        out = self.page(server, self.history_steps(DONE_PLAN) + confirm
                        + [answered("/api/plan-status", 200), wait(".wh-row .wh-chip", root="whistory"), {"op": "sleep", "ms": 2500}]
                        + [click(".wh-row", root="whistory", text=DONE_PLAN), shot("archived")] + confirm + [{"op": "sleep", "ms": 1000}, shot("restored")],
                        cookie=browser.cookie, fakeTab=FAKE_TAB)
        first, second = self.writes(out, "/api/plan-status")
        for call, action in ((first, "archive"), (second, "unarchive")):
            self.assert_page_headers(call, browser.csrf)
            self.assertEqual(sorted(call["body"]), ["action", "path", "sha", "tab"])
            self.assertEqual((call["body"]["action"], call["body"]["path"], call["status"]), (action, DONE_PLAN, 200), call["answer"])
        self.assertIn("Status: done", descend(self.project, DONE_PLAN).read_text(encoding="utf-8"))
        audit = self.rows_of_audit()
        self.assertEqual([(row["phase"], row["from"], row["to"]) for row in audit],
                         [("intent", "done", "archived"), ("done", "done", "archived"), ("intent", "archived", "done"), ("done", "archived", "done")])
        self.assertEqual({row["by"] for row in audit}, {APPROVER_ACCOUNT}, "the account of the session, not the user of the system")
        self.assertNotIn(APPROVER, json.dumps(audit))

    def test_without_the_role_the_history_offers_no_action_and_anonymously_a_sign_in(self) -> None:
        server = self.serve()
        commenter = self.signed_in(server, COMMENTER, COMMENTER_PASSWORD)
        out = self.page(server, self.history_steps(DONE_PLAN) + [wait(".wh-hint", root="whistory"), shot("role")], cookie=commenter.cookie, fakeTab=FAKE_TAB)
        self.assertIn("Zmiana statusu wymaga roli approver.", out["shots"]["role"]["history"])
        self.assertEqual(self.writes(out, "/api/plan-status"), [])
        out = self.page(server, self.history_steps(DONE_PLAN) + [wait("button[data-ah-focus=\"login\"]", root="whistory"),
                                                                 click("button[data-ah-focus=\"login\"]", root="whistory"), shot("anon")], fakeTab=FAKE_TAB)
        self.assertIn("wymaga zalogowania", out["shots"]["anon"]["history"])
        self.assertTrue(out["shots"]["anon"]["assigned"][0].startswith("/_login?next=%2F%3Ftab%3Dhistory"))
        self.assertEqual(self.rows_of_audit(), [])


# ------------------------------------------------------------------------------------------------------- the windows, the renderer alone (no server)
WINDOWS = r"""
(async () => {
  const out = { violations };
  const account = () => ({ state: 'user', name: 'Ola', roles: ['approver'], approver: true, mustChange: false, loginAllowed: true });
  const base = () => { const j = JSON.parse(JSON.stringify(input.fixture)); j.approvals.here = true;
    j.delivery = Object.assign({}, j.delivery, { mode: input.mode, base: 'main', branch: 'feat/x', target: input.mode === 'pr' ? null : 'feat/x' });
    j.approvals.actions = [{ kind: input.kind, state: 'waiting', word: input.kind.toUpperCase(), path: 'run-1', sha: 'a'.repeat(40), planState: 'in_progress' }];
    j.waiting = [{ what: 'w', source: 'checkpoint', approve: input.kind, action: 'a', link: null }]; return j; };
  const changes = { none: () => {}, branch: (j) => { j.delivery.branch = 'feat/y'; }, target: (j) => { j.delivery.target = 'develop'; },
    planState: (j) => { j.approvals.actions[0].planState = 'done'; }, mode: (j) => { j.delivery.mode = 'invalid'; }, base: (j) => { j.delivery.base = 'develop'; },
    session: null };
  const rows = (m) => { const found = {}; for (const dd of m.root.querySelectorAll('.dlg-modal dd').__asArray()) found[dd.getAttribute('data-consent')] = text(dd);
    return found; };
  for (const name of Object.keys(changes)) {
    let session = account();
    const m = mount(base(), { now: Date.parse(input.fixture.generatedAt), deps: { session: () => session } });
    m.root.querySelector('button[data-approve]').click();
    const opened = rows(m);
    const labels = m.root.querySelectorAll('.dlg-modal dt').__asArray().map(text);
    if (name === 'session') { session = { state: 'anonymous', roles: [], approver: false, loginAllowed: true }; m.board.sessionChanged(); }
    else { const next = base(); changes[name](next); m.board.update(next); }
    const open = Boolean(m.root.querySelector('.dlg-modal'));
    const banner = text(m.root.querySelector('#waitBanner'));
    if (open) m.root.querySelector('button[data-consent-confirm]').click();
    await tick(); await tick();
    out[name] = { opened, labels, open, banner, sent: m.calls.approve };
    m.board.destroy();
  }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e) })); });
"""


@unittest.skipUnless(NODE_MAJOR >= 18, "set SKILLSYNC_TEST_NODE (or have node on PATH), node >= 18")
class ConsentWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tail = HARNESS[:HARNESS.index("(async () => {\n  const out = { fixtures: {}")]
        fixture = fixture_files()["cp-ready"]
        cls.runs = {kind: run_node(tail + WINDOWS, {"fixtures": {}, "now": {}, "fixture": fixture, "kind": kind, "mode": mode})
                    for kind, mode in (("cp", "direct"), ("ready", "pr"))}

    def test_the_window_closes_without_sending_when_what_it_shows_changes(self):
        for kind, changed in (("cp", ("branch", "target", "planState", "mode", "session")), ("ready", ("branch", "planState", "mode", "base", "session"))):
            out = self.runs[kind]
            self.assertEqual(out["violations"], [])
            self.assertTrue(out["none"]["open"], "control: an unchanged status keeps the window")
            self.assertEqual(len(out["none"]["sent"]), 1)
            for name in changed:
                with self.subTest(kind=kind, change=name):
                    self.assertEqual((out[name]["open"], out[name]["sent"]), (False, []))
                    if name != "session":
                        self.assertIn("nic nie wysłano", out[name]["banner"])

    def test_the_ready_window_names_the_base_of_the_pr_as_the_target_and_the_branch_as_the_working_one(self):
        out = self.runs["ready"]["none"]
        self.assertEqual((out["opened"]["target"], out["opened"]["branch"], out["opened"]["mode"]), ("main", "feat/x", "pr"))
        self.assertIn("Baza PR (cel)", out["labels"])
        self.assertIn("Gałąź robocza", out["labels"])
        self.assertNotIn("Gałąź docelowa", out["labels"])
        sent = out["sent"][0]
        self.assertEqual({key: sent[key] for key in ("branch", "target", "mode")}, {"branch": "feat/x", "target": None, "mode": "pr"},
                         "what is sent and checked is the delivery as before; the base is only shown")

    def test_the_cp_window_keeps_one_row_when_the_target_is_the_working_branch(self):
        out = self.runs["cp"]["none"]
        self.assertEqual(out["opened"]["target"], "feat/x")
        self.assertNotIn("branch", out["opened"])
        self.assertIn("Gałąź docelowa", out["labels"])


if __name__ == "__main__":
    unittest.main()
