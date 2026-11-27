// K21/K8 na originie strony: treść z repozytorium, przebiegów i prototypów (ładunki w polach) nie wykonuje się ani nie zatwierdza.
// ctx: { page, expected: {anon: [...], approver: [...]}, payloads: [...] }
import { Suite, connect } from '../prototype/lib.mjs';
import { ctx, J, sleep, newPage, hits, domAudit, markers, loginPage } from './common.mjs';

const suite = new Suite('xss_page');
suite.setup(async () => ({ c: await connect() }));
suite.teardown(async (s) => { if (s) await s.c.closeAll(); });

// Przechodzi wszystkie widoki strony; po każdym zbiera wykonania, ślady w DOM i widoczne znaczniki.
async function walk(p) {
  const found = new Set(); const bad = []; const views = [];
  const collect = async (view) => {
    for (const m of await markers(p)) found.add(m);
    for (const b of await domAudit(p)) bad.push(view + ': ' + b);
    views.push(view);
  };
  // dymki i podpowiedzi: fokus i najechanie
  const hover = () => p.evaluate(`(() => { for (const el of [...document.querySelectorAll('[data-desc],[data-tip]')].slice(0, 80)) { el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true })); el.dispatchEvent(new FocusEvent('focusin', { bubbles: true })); } return true; })()`);
  await hover(); await collect('start');
  const tabs = await p.evaluate(`[...document.querySelectorAll('nav.tabs [role=tab]')].filter((b) => !b.hidden).map((b) => b.id)`);
  for (const id of tabs) {
    await p.domClick(`document.getElementById(${J(id)})`); await sleep(700);
    // rozwijalne wiersze (wyniki testów, grupy dokumentów, szczegóły)
    await p.evaluate(`(() => { for (const d of document.querySelectorAll('details')) d.open = true; for (const b of [...document.querySelectorAll('[role=tabpanel]:not([hidden]) [aria-expanded="false"]')].slice(0, 60)) b.click(); return true; })()`);
    await sleep(700); await hover(); await collect(id);
    if (id === 'tab-docs') {
      const docs = await p.evaluate(`[...document.querySelectorAll('button.doc-item[data-doc]')].map((b) => b.getAttribute('data-doc'))`);
      for (const doc of docs) {
        await p.domClick(`[...document.querySelectorAll('button.doc-item[data-doc]')].find((b) => b.getAttribute('data-doc') === ${J(doc)})`, doc);
        await p.waitFor(`!!document.querySelector('#view-docs .md')`, 5000); await sleep(400);
        // każdy odnośnik treści zostaje kliknięty: także te, których renderer nie powinien był zrobić odnośnikiem
        await p.evaluate(`(() => { for (const a of [...document.querySelectorAll('#view-docs .md a:not(.md-doc), #view-docs .md .md-link-off, #view-docs .md .md-img')]) a.click(); return true; })()`);
        await sleep(200); await collect('doc ' + doc);
      }
    }
  }
  return { found: [...found].sort(), bad: [...new Set(bad)].slice(0, 20), views };
}

async function visit(s, viewer) {
  const p = await newPage(s.c, viewer);
  if (viewer === 'approver') await loginPage(p, ctx.page, 'approver');
  await p.go(ctx.page + '/', 1800);
  const rendered = await p.waitFor(`!!document.querySelector('#app nav.tabs [role=tab]')`, 10000);
  if (!rendered.ok) throw new Error('strona nie narysowała widoku');
  const result = await walk(p);
  const executed = await hits(p);
  const missing = ctx.expected[viewer].filter((m) => !result.found.includes(m));
  const url = await p.evaluate('location.pathname');
  return { ok: executed.length === 0 && result.bad.length === 0 && missing.length === 0 && url === '/', executed, domTraces: result.bad, missing, reached: result.found, views: result.views, url };
}

suite.add('XSS-P1', 'anonim: żaden ładunek na stronie nie wykonuje się ani nie trafia do DOM jako HTML', (s) => visit(s, 'anon'));
suite.add('XSS-P2', 'zatwierdzający: to samo z sesją approver', (s) => visit(s, 'approver'));

// Wstawia surowe ładunki tak, jak zrobiłby to dziurawy renderer.
const inject = (p) => p.evaluate(`(async () => { const box = document.createElement('div'); document.body.appendChild(box); box.innerHTML = ${J(ctx.payloads.join(''))};
  for (const a of box.querySelectorAll('a')) a.click(); await new Promise((r) => setTimeout(r, 1200)); return window.__hits; })()`);

suite.add('XSS-P3', 'CSP blokuje wstrzyknięty HTML; kontrola negatywna bez CSP wykonuje te same ładunki', async (s) => {
  const guarded = await newPage(s.c, 'csp'); await guarded.go(ctx.page + '/', 1200);
  const blocked = await inject(guarded);
  const violations = await guarded.evaluate('[...new Set(window.__violations)]');
  const open = await newPage(s.c, 'nocsp', { bypassCSP: true }); await open.go(ctx.page + '/', 1200);
  const ran = await inject(open);
  const wanted = ctx.controlFields;
  const notRun = wanted.filter((f) => !ran.includes(f));
  return { ok: blocked.length === 0 && violations.some((v) => v.startsWith('script-src')) && notRun.length === 0, blockedByCsp: blocked, violations, ranWithoutCsp: ran, notRun };
});

await suite.main();
