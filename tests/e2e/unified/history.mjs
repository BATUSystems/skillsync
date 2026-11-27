// Przyciski zakładki „Historia” (serwer z wieloma worktree) w przeglądarce: Archiwizuj, Przywróć, Porzuć przez sesję i CSRF.
// ctx: { page, audit, rows: {done, archived, approved, stale, session, hostile: {path, file}} }
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { Suite, connect } from '../prototype/lib.mjs';
import { ctx, J, sleep, newPage, loginPage, hits, domAudit } from './common.mjs';

const suite = new Suite('history');
suite.setup(async () => ({ c: await connect() }));
suite.teardown(async (s) => { if (s) await s.c.closeAll(); });

const read = (row) => readFileSync(row.file, 'utf8');
const status = (row) => /^Status: *(\S+)/m.exec(read(row))?.[1];
// wpisy audytu ze WSZYSTKICH katalogów roboczych: każda zmiana ma leżeć w katalogu właściciela planu
const auditOf = (file) => (existsSync(file) ? readFileSync(file, 'utf8').split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l)) : []);
const audit = () => ctx.audits.flatMap(auditOf);
const rowEl = (row) => `[...document.querySelectorAll('#whistory [data-ah-focus^="row:"]')].find((b) => b.getAttribute('data-ah-focus').endsWith(${J('|' + row.path)}))`;
const part = (name) => `document.querySelector('#whistory [data-ah-focus="${name}"]')`;
const CONFIRM_BOX = `document.querySelector('#whistory .wh-confirm')`;
const text = (p) => p.evaluate(`document.querySelector('#whistory').textContent.replace(/\\s+/g, ' ')`);
async function openHistory(p) {
  await p.go(ctx.page + '/', 1500);
  const tab = await p.waitFor(`!!document.querySelector('button[data-wt-tab="history"]')`, 15000);
  if (!tab.ok) throw new Error('brak zakładki Historia');
  await p.domClick(`document.querySelector('button[data-wt-tab="history"]')`);
  const listed = await p.waitFor(`document.querySelectorAll('#whistory [data-ah-focus^="row:"]').length > 0`, 15000);
  if (!listed.ok) throw new Error('lista Historii pusta');
}
async function select(p, row) {
  const w = await p.waitFor(`!!(${rowEl(row)})`, 10000);
  if (!w.ok) throw new Error('brak wiersza ' + row.path);
  await p.domClick(rowEl(row), row.path); await sleep(700);
}
async function ask(p, row) {
  await select(p, row);
  const w = await p.waitFor(`!!(${part('act')})`, 8000);
  if (!w.ok) throw new Error('brak przycisku akcji dla ' + row.path);
  await p.domClick(part('act'));
  if (!(await p.waitFor(`!!${CONFIRM_BOX}`, 4000)).ok) throw new Error('brak pytania o potwierdzenie');
}
const answers = (p) => { const seen = []; p.listeners.push((m) => { if (m.method === 'Network.responseReceived' && m.params.response.url === ctx.page + '/api/plan-status') seen.push({ id: m.params.requestId, status: m.params.response.status }); }); return seen; };
const codeOf = async (p, seen) => { const last = seen[seen.length - 1]; if (!last) return null; await sleep(200); const body = await p.send('Network.getResponseBody', { requestId: last.id }); return { status: last.status, code: JSON.parse(body.body).code || null }; };
const directPost = (p, row) => p.evaluate(`fetch('/_api/v1/session').then((r) => r.json()).then((x) => fetch('/api/history').then((r) => r.json()).then((h) => {
  const it = h.items.find((i) => i.path === ${J(row.path)}) || {};
  return fetch('/api/plan-status', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Status-Board': '1', ...(x.csrf ? { 'X-Skillsync-CSRF': x.csrf } : {}) }, body: JSON.stringify({ tab: it.tab, path: it.path, sha: it.sha, action: it.action || 'archive' }) });
})).then(async (r) => ({ status: r.status, body: (await r.text()).slice(0, 160) }))`);

// Pytanie o potwierdzenie → anulowanie nic nie zmienia → potwierdzenie zmienia status w katalogu właściciela i zostawia wpis audytu.
async function change(p, row, from, expected) {
  await openHistory(p); await ask(p, row);
  const box = await p.evaluate(`${CONFIRM_BOX}.textContent.replace(/\\s+/g, ' ')`);
  const original = read(row); const entries = audit().length; const own = auditOf(row.audit || ctx.audits[0]).length;
  await p.domClick(part('cancel')); await sleep(500);
  const cancelled = (await p.evaluate(`!${CONFIRM_BOX}`)) && read(row) === original && audit().length === entries;
  await ask(p, row);
  const seen = answers(p);
  await p.domClick(part('confirm')); await sleep(1800);
  const answer = await codeOf(p, seen);
  const written = auditOf(row.audit || ctx.audits[0]).slice(own);
  const elsewhere = audit().length - entries - written.length;
  const said = await text(p);
  return { ok: cancelled && box.includes(row.title) && answer?.status === 200 && status(row) === expected && written.length >= 1 && elsewhere === 0 && written.map((e) => e.phase).join() === 'intent,done' && written.every((e) => e.by === ctx.account && e.path === row.path && e.from === from && e.to === expected) && said.includes(row.title),
    cancelled, answer, planStatus: status(row), elsewhere, audit: written.map((e) => ({ by: e.by, from: e.from, to: e.to, phase: e.phase })), noticeNamesPlan: said.includes(row.title) };
}

suite.add('HIS-1', 'bez sesji: lista pogrupowana po katalogach, bez przycisku zmiany, z „Zaloguj”', async (s) => {
  const p = await newPage(s.c, 'anon'); await openHistory(p);
  const groups = await p.evaluate(`document.querySelectorAll('#whistory .wh-group').length`);
  await select(p, ctx.rows.done);
  const act = await p.evaluate(`!!(${part('act')})`); const login = await p.evaluate(`!!(${part('login')})`);
  const before = read(ctx.rows.done);
  const reply = await directPost(p, ctx.rows.done);
  return { ok: groups >= 2 && !act && login && reply.status === 401 && read(ctx.rows.done) === before, groups, actionButton: act, loginButton: login, reply };
});

suite.add('HIS-2', 'konto bez roli approver: brak przycisku, własne żądanie z CSRF odrzucone', async (s) => {
  const p = await newPage(s.c, 'commenter'); await loginPage(p, ctx.page, 'commenter'); await openHistory(p); await select(p, ctx.rows.done);
  const act = await p.evaluate(`!!(${part('act')})`); const said = await text(p);
  const before = read(ctx.rows.done); const entries = audit().length;
  const reply = await directPost(p, ctx.rows.done);
  return { ok: !act && /approver/.test(said) && reply.status === 403 && read(ctx.rows.done) === before && audit().length === entries, actionButton: act, reply };
});

suite.add('HIS-3', 'approver: Archiwizuj (anulowanie bez zmian, potem zmiana i audyt z kontem sesji)', async (s) => {
  const p = await newPage(s.c, 'approver'); s.p = p; await loginPage(p, ctx.page, 'approver');
  return change(p, ctx.rows.done, 'done', 'archived');
});

suite.add('HIS-4', 'approver: Przywróć z archiwum w innym worktree zmienia plik właściciela, nie kopię w katalogu głównym', async ({ p }) => {
  const mainCopy = readFileSync(ctx.rows.archived.mainCopy, 'utf8');
  const r = await change(p, ctx.rows.archived, 'archived', 'done');
  const untouched = readFileSync(ctx.rows.archived.mainCopy, 'utf8') === mainCopy;
  return { ...r, ok: r.ok && untouched, mainCopyUntouched: untouched };
});

suite.add('HIS-5', 'approver: Porzuć zatwierdzony plan, potem przywróć go do „approved”', async ({ p }) => {
  const abandoned = await change(p, ctx.rows.approved, 'approved', 'archived');
  const line = /^Porzucony:/m.test(read(ctx.rows.approved));
  const restored = await change(p, ctx.rows.approved, 'archived', 'approved');
  return { ok: abandoned.ok && line && restored.ok, abandoned, abandonedLine: line, restored };
});

suite.add('HIS-6', 'plik zmieniony po otwarciu pytania (strona tego nie widzi): odmowa, plik bez zmian', async ({ p }) => {
  await openHistory(p); await ask(p, ctx.rows.stale);
  await p.intercept([ctx.page + '/api/history*'], async (ev, api) => api.fail());
  const changed = read(ctx.rows.stale) + '\nDopisane po otwarciu pytania.\n';
  writeFileSync(ctx.rows.stale.file, changed);
  const entries = audit().length; const seen = answers(p);
  const open = await p.evaluate(`!!${CONFIRM_BOX}`);
  if (open) { await p.domClick(part('confirm')); await sleep(1800); }
  const answer = await codeOf(p, seen);
  await p.interceptOff();
  const said = await text(p);
  return { ok: open && answer?.status === 409 && read(ctx.rows.stale) === changed && audit().length === entries, questionStayed: open, answer, said: said.slice(0, 160) };
});

suite.add('HIS-7', 'sesja zakończona w innej karcie przy otwartym pytaniu: odmowa, plik bez zmian', async ({ c, p }) => {
  await openHistory(p); await ask(p, ctx.rows.session);
  await p.intercept([ctx.page + '/api/history*'], async (ev, api) => api.fail());
  const other = await newPage(c, 'other-tab', { shared: p.ctx });
  await other.go(ctx.page + '/_login', 600);
  const out = await other.evaluate(`fetch('/_api/v1/session').then((r) => r.json()).then((x) => fetch('/_api/v1/logout', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Skillsync-CSRF': x.csrf }, body: '{}' })).then((r) => r.status)`);
  const before = read(ctx.rows.session); const entries = audit().length; const seen = answers(p);
  const open = await p.evaluate(`!!${CONFIRM_BOX}`);
  if (open) { await p.domClick(part('confirm')); await sleep(1800); }
  const answer = await codeOf(p, seen);
  await p.interceptOff(); await other.dispose();
  return { ok: out < 300 && open && !!answer && [401, 403].includes(answer.status) && read(ctx.rows.session) === before && audit().length === entries, logout: out, questionStayed: open, answer };
});

suite.add('HIS-8', 'wrogi tytuł planu w liście i podglądzie Historii nie wykonuje się', async ({ c }) => {
  const p = await newPage(c, 'xss'); await openHistory(p); await select(p, ctx.rows.hostile); await sleep(800);
  const said = await text(p); const traces = await domAudit(p); const executed = await hits(p);
  const reached = said.includes('KAN-histitle-') && said.includes('KAN-histbody-');
  return { ok: executed.length === 0 && traces.length === 0 && reached, executed, domTraces: traces, reached };
});

await suite.main();
