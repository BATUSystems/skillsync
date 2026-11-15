"""B1: `app.js` takes the consent token from the fragment `#zgoda=`, keeps it in localStorage, clears the address bar, sends it as
`X-Approve-Token` and forgets a token the server refuses. The page is run on a stub of the browser in node."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "library" / "skills" / "dashboard" / "assets" / "app.js"
NODE = os.environ.get("SKILLSYNC_TEST_NODE") or shutil.which("node")
TOKEN = "".join(("AbCdEfGh", "_ijkl", "MNOP-", "1234567890"))   # assembled: a literal key-like string trips the secret scanner

HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
const sc = JSON.parse(process.argv[3]);
const storage = Object.assign({}, sc.storage);
const calls = { fetch: [], replaced: [], hashSet: [] };
const location = { hash: sc.hash, pathname: '/', search: '?plan=x', protocol: 'http:', hostname: '127.0.0.1' };
const ctx = {
  location,
  history: { replaceState: (a, b, url) => { calls.replaced.push(url); location.hash = ''; } },
  localStorage: { getItem: (k) => (k in storage ? storage[k] : null), setItem: (k, v) => { storage[k] = String(v); }, removeItem: (k) => { delete storage[k]; } },
  document: { getElementById: () => ({}), addEventListener() {}, documentElement: { dataset: {} }, visibilityState: 'visible', title: '' },
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  setInterval: () => 0, clearInterval() {}, setTimeout: () => 0, console,
  fetch: (url, opts) => {
    calls.fetch.push({ url, headers: (opts && opts.headers) || null });
    if (url === '/api/approve') return Promise.resolve({ ok: sc.ok, status: sc.status, json: () => Promise.resolve(sc.body) });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ plans: [] }) });
  },
};
ctx.window = ctx;
ctx.addEventListener = () => {};
ctx.DashboardRenderer = { create: (el, deps) => { ctx.deps = deps; return new Proxy({}, { get: () => () => {} }); } };
vm.createContext(ctx);
vm.runInContext(src, ctx);
(async () => {
  const result = sc.approve ? await ctx.deps.actions.approve({ kind: 'spec', path: 'p', sha: 's', word: 'GO plan' }) : null;
  await new Promise((r) => setImmediate(r));
  const approveCall = calls.fetch.find((c) => c.url === '/api/approve') || null;
  console.log(JSON.stringify({ storage, replaced: calls.replaced, hash: location.hash, result,
    approveHeaders: approveCall && approveCall.headers, hasToken: ctx.deps.hasApproveToken(),
    statusFetches: calls.fetch.filter((c) => c.url.startsWith('/api/status')).length }));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(NODE, "brak node w środowisku (SKILLSYNC_TEST_NODE)")
class AppToken(unittest.TestCase):
    def run_app(self, **scenario):
        base = {"hash": "", "storage": {}, "approve": True, "ok": True, "status": 200, "body": {"word": "GO plan", "next": "ok"}}
        import tempfile
        with tempfile.TemporaryDirectory(prefix="app-token-") as tmp:
            harness = Path(tmp) / "harness.js"
            harness.write_text(HARNESS, encoding="utf-8", newline="\n")
            page = Path(tmp) / "app.js"
            page.write_text(APP.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
            done = subprocess.run([NODE, str(harness), str(page), json.dumps({**base, **scenario})], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_the_fragment_token_is_stored_the_address_bar_is_cleaned_and_the_header_is_sent(self) -> None:
        out = self.run_app(hash=f"#zgoda={TOKEN}")
        self.assertEqual(out["storage"], {"dashboard:approveToken": TOKEN})
        self.assertEqual(out["replaced"], ["/?plan=x"], "the fragment is removed without a reload and without losing the query")
        self.assertEqual(out["hash"], "")
        self.assertEqual(out["approveHeaders"]["X-Approve-Token"], TOKEN)
        self.assertEqual(out["approveHeaders"]["X-Status-Board"], "1")
        self.assertTrue(out["hasToken"])

    def test_a_stored_token_is_used_without_a_fragment(self) -> None:
        out = self.run_app(storage={"dashboard:approveToken": TOKEN})
        self.assertEqual(out["replaced"], [])
        self.assertEqual(out["approveHeaders"]["X-Approve-Token"], TOKEN)

    def test_without_a_token_no_header_is_sent_and_the_page_knows_it(self) -> None:
        out = self.run_app()
        self.assertNotIn("X-Approve-Token", out["approveHeaders"])
        self.assertFalse(out["hasToken"])

    def test_other_fragments_are_not_a_token(self) -> None:
        for fragment in ("#zgoda=short", "#zgoda=" + "x" * 200, "#zgoda=ab cd/ef" + "g" * 20, "#docs:docs/plans/x.md", "#zgoda=" + TOKEN + "&x=1"):
            with self.subTest(fragment=fragment):
                out = self.run_app(hash=fragment, approve=False)
                self.assertEqual(out["storage"], {})
                self.assertEqual(out["replaced"], [])
                self.assertFalse(out["hasToken"])

    def test_a_refused_token_is_forgotten_and_the_status_is_fetched_again(self) -> None:
        refused = {"error": "Brak albo zły token zgody: otwórz adres z konsoli serwera albo z `skillsync dashboard url`."}
        out = self.run_app(storage={"dashboard:approveToken": TOKEN}, ok=False, status=403, body=refused)
        self.assertEqual(out["storage"], {})
        self.assertFalse(out["hasToken"])
        self.assertEqual(out["result"], {"ok": False, "word": "GO plan", "text": refused["error"]})
        self.assertGreaterEqual(out["statusFetches"], 2, "the page refreshes so that the buttons become inactive")

    def test_another_refusal_keeps_the_token(self) -> None:
        out = self.run_app(storage={"dashboard:approveToken": TOKEN}, ok=False, status=403, body={"error": "Obcy Origin."})
        self.assertEqual(out["storage"], {"dashboard:approveToken": TOKEN})

    def test_the_page_source_never_prints_or_logs_the_token(self) -> None:
        source = APP.read_text(encoding="utf-8")
        for needle in ("console.", "alert(", "innerHTML = token", "textContent = token", ".innerText"):
            self.assertNotIn(needle, source)


if __name__ == "__main__":
    unittest.main()
