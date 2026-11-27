// Zgody ze strony klikane w przeglądarce (CP, READY, GO plan, GO implement): okno zgody, odmowy po zmianie tego, co widział człowiek.
// ctx: { page, project, approvals, word, kind, head, change: [{ id, files: {ścieżka: treść}, code }], plan }
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { Suite, connect } from '../prototype/lib.mjs';
import { ctx, J, sleep, newPage, loginPage, signInOnLoginPage, sessionOf } from './common.mjs';

const BUTTON = `document.querySelector('button[data-approve="${ctx.kind}"]')`;
const WINDOW = `document.querySelector('[data-dialog="consent"]')`;
const rows = () => (existsSync(ctx.approvals) ? readFileSync(ctx.approvals, 'utf8').split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l)) : []);
const windowRows = (p) => p.evaluate(`(() => { const w = ${WINDOW}; if (!w) return null; const out = { labels: [...w.querySelectorAll('dt')].map((x) => x.textContent), passwordFields: w.querySelectorAll('input').length, focus: document.activeElement?.hasAttribute('data-consent-cancel') };
  for (const dd of w.querySelectorAll('dd[data-consent]')) out[dd.getAttribute('data-consent')] = dd.textContent; return out; })()`);
const notice = (p) => p.evaluate(`(() => { const n = document.querySelector('.wait-item.is-err, .wait-item.is-ok'); return n ? { ok: n.classList.contains('is-ok'), text: n.textContent.replace(/\\s+/g, ' ').trim().slice(0, 200) } : null; })()`);
const ready = async (p) => { const w = await p.waitFor(`(() => { const b = ${BUTTON}; return !!b && !b.disabled; })()`, 15000); if (!w.ok) throw new Error('brak aktywnego przycisku zgody ' + ctx.kind); };
async function open(p) {
  await ready(p); await p.domClick(BUTTON);
  const w = await p.waitFor(`!!${WINDOW}`, 4000);
  if (!w.ok) throw new Error('okno zgody się nie otworzyło');
}
const confirm = (p) => p.domClick(`document.querySelector('[data-consent-confirm]')`);
const apply = (files) => { const before = {}; for (const [path, text] of Object.entries(files)) { before[path] = readFileSync(path, 'utf8'); writeFileSync(path, text); } return () => { for (const [path, text] of Object.entries(before)) writeFileSync(path, text); }; };
// Strona przestaje widzieć zmiany (status nieosiągalny), więc o odmowie decyduje serwer, nie samo zamknięcie okna.
const blind = (p) => p.intercept([ctx.page + '/api/status*'], async (ev, api) => api.fail());
const answers = (p) => { const seen = []; p.listeners.push((m) => { if (m.method === 'Network.responseReceived' && m.params.response.url === ctx.page + '/api/approve') seen.push({ id: m.params.requestId, status: m.params.response.status }); }); return seen; };
const codeOf = async (p, seen) => { const last = seen[seen.length - 1]; if (!last) return null; await sleep(200); const body = await p.send('Network.getResponseBody', { requestId: last.id }); return { status: last.status, code: JSON.parse(body.body).code || null }; };

const suite = new Suite('consent_' + ctx.kind);
suite.setup(async () => ({ c: await connect() }));
suite.teardown(async (s) => { if (s) await s.c.closeAll(); });
const id = (n) => `${ctx.word}-${n}`;
async function show(p) {
  await p.go(ctx.page + '/', 1500);
  if (ctx.storage) { await p.evaluate(`localStorage.setItem(${J(ctx.storage[0])}, ${J(ctx.storage[1])}); true`); await p.reload(1500); }
}

suite.add(id('1'), 'anonim nie ma przycisku zgody; „Zaloguj” prowadzi na /_login i z powrotem, potem przycisk działa', async (s) => {
  const p = await newPage(s.c, 'approver'); s.p = p;
  await show(p);
  const loaded = await p.waitFor(`!!document.querySelector('[data-session="login"]')`, 10000);
  const anon = await p.evaluate(`({ buttons: document.querySelectorAll('button[data-approve]').length, state: document.querySelector('[data-session-state]')?.getAttribute('data-session-state') })`);
  await p.domClick(`document.querySelector('[data-session="login"]')`);
  const atLogin = (await p.waitFor(`location.pathname === '/_login'`, 8000)).ok;
  await signInOnLoginPage(p, 'approver');
  const back = (await p.waitFor(`location.pathname === '/' && !!document.querySelector('[data-session-state="user"]')`, 15000)).ok;
  await show(p); await ready(p);
  return { ok: loaded.ok && anon.buttons === 0 && anon.state === 'anonymous' && atLogin && back, anon, atLogin, back };
});

suite.add(id('2'), 'okno zgody: słowo, wersja, cel i tryb, bez pola hasła; fokus na „Anuluj”, Enter i Escape niczego nie zapisują', async ({ p }) => {
  await open(p);
  const shown = await windowRows(p);
  await p.key('Enter'); await sleep(400);
  const afterEnter = await p.evaluate(`!${WINDOW}`);
  await open(p); await p.key('Escape'); await sleep(300);
  const afterEsc = await p.evaluate(`!${WINDOW}`);
  const wanted = Object.entries(ctx.shown || {}).filter(([key, value]) => !(shown[key] || '').includes(value));
  const labels = (ctx.labels || []).filter((l) => !shown.labels.includes(l));
  return { ok: shown.word === ctx.word && shown.passwordFields === 0 && shown.focus === true && wanted.length === 0 && labels.length === 0 && afterEnter && afterEsc && rows().length === 0, shown, mismatched: wanted, missingLabels: labels };
});

let step = 3;
for (const change of ctx.change) {
  suite.add(id(String(step++)), `${change.id}: widoczna zmiana zamyka otwarte okno, nic nie zapisano`, async ({ p }) => {
    await show(p); await open(p);
    const seen = answers(p);
    const restore = apply(change.files);
    try {
      const closed = await p.waitFor(`!${WINDOW}`, 45000, 300);
      await sleep(300);
      const said = await notice(p);
      return { ok: closed.ok && !!said && !said.ok && seen.length === 0 && rows().length === 0, closedMs: closed.ms, notice: said, posts: seen.length };
    } finally { restore(); }
  });
  suite.add(id(String(step++)), `${change.id}: zmiana niewidoczna dla strony, potwierdzenie dostaje 409 ${change.code}`, async ({ p }) => {
    await show(p); await open(p);
    const seen = answers(p);
    await blind(p);
    const restore = apply(change.files);
    try {
      const still = await p.evaluate(`!!${WINDOW}`);
      if (!still) return { ok: false, why: 'okno zamknęło się przed potwierdzeniem' };
      await confirm(p); await sleep(1500);
      const answer = await codeOf(p, seen);
      const said = await notice(p);
      return { ok: answer?.status === 409 && answer.code === change.code && !!said && !said.ok && rows().length === 0, answer, notice: said };
    } finally { restore(); await p.interceptOff(); }
  });
}

suite.add(id(String(step++)), 'sesja zakończona w innej karcie przy otwartym oknie: potwierdzenie odrzucone, nic nie zapisano', async (s) => {
  const p = s.p; await show(p); await open(p);
  const seen = answers(p);
  await blind(p);
  const other = await newPage(s.c, 'other-tab', { shared: p.ctx });
  await other.go(ctx.page + '/_login', 600);
  const out = await other.evaluate(`fetch('/_api/v1/session').then((r) => r.json()).then((x) => fetch('/_api/v1/logout', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Skillsync-CSRF': x.csrf }, body: '{}' })).then((r) => r.status)`);
  const open_ = await p.evaluate(`!!${WINDOW}`);
  if (open_) { await confirm(p); await sleep(1500); }
  const answer = await codeOf(p, seen);
  await p.interceptOff();
  await loginPage(other, ctx.page, 'approver'); await other.dispose();
  return { ok: out < 300 && open_ && !!answer && [401, 403].includes(answer.status) && rows().length === 0, logout: out, answer };
});

suite.add(id(String(step++)), 'konto bez roli approver: przycisk nieaktywny, własne żądanie z CSRF odrzucone', async (s) => {
  const p = await newPage(s.c, 'commenter');
  await loginPage(p, ctx.page, 'commenter'); await show(p);
  await p.waitFor(`!!${BUTTON} || !!document.querySelector('[data-gate="${ctx.kind}"]')`, 10000);
  const gate = await p.evaluate(`(() => { const b = document.querySelector('[data-gate="${ctx.kind}"]'); return b ? { disabled: b.disabled } : null; })()`);
  const bar = await p.evaluate(`document.querySelector('#sessionBar')?.textContent || ''`);
  const reply = await p.evaluate(`fetch('/_api/v1/session').then((r) => r.json()).then((x) => fetch('/api/status').then((r) => r.json()).then((st) => {
    const a = (st.approvals.actions || []).find((y) => y.kind === ${J(ctx.kind)}) || {}; const d = st.delivery || {};
    return fetch('/api/approve', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Status-Board': '1', 'X-Skillsync-CSRF': x.csrf }, body: JSON.stringify({ kind: ${J(ctx.kind)}, path: a.path || 'x', sha: a.sha || 'x', branch: d.branch, target: d.target, mode: d.mode, planState: a.planState }) });
  })).then(async (r) => ({ status: r.status, body: (await r.text()).slice(0, 160) }))`);
  return { ok: !!gate && gate.disabled && /approver/.test(bar) && reply.status === 403 && rows().length === 0, gate, bar: bar.slice(0, 160), reply };
});

suite.add(id(String(step++)), 'potwierdzenie w oknie zapisuje dokładnie jedną zgodę na pokazaną wersję', async (s) => {
  const p = s.p; await show(p); await open(p);
  const shown = await windowRows(p);
  const seen = answers(p);
  await confirm(p); await sleep(2000);
  const answer = await codeOf(p, seen);
  const said = await notice(p);
  const written = rows();
  const row = written[0] || {};
  const still = await p.evaluate(`!!${BUTTON}`);
  return { ok: answer?.status === 200 && !!said?.ok && written.length === 1 && row.word === ctx.word && row.role === 'approver' && String(row.sha || '').startsWith(shown.sha) && !still, answer, notice: said, row: { word: row.word, role: row.role, mode: row.mode, target: row.target, branch: row.branch, source: row.source }, buttonStillThere: still };
});

await suite.main();
