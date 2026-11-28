// K2/Q2 w przeglądarce: widok bez sesji nie pokazuje adresów e-mail, ścieżek bezwzględnych ani autorów zgód (także w atrybutach DOM).
// ctx: { page, forbidden: {nazwa: napis}, control: [nazwy widoczne po zalogowaniu], reached: [znaczniki KAN]}
import { Suite, connect } from '../prototype/lib.mjs';
import { ctx, J, sleep, newPage, loginPage, markers } from './common.mjs';

const suite = new Suite('anonymous');
suite.setup(async () => ({ c: await connect() }));
suite.teardown(async (s) => { if (s) await s.c.closeAll(); });

// Przechodzi widoki i dokumenty; zbiera, w których występują zakazane napisy (w całym HTML dokumentu i wartościach pól).
async function sweep(p) {
  const seen = {}; const found = new Set(); const views = [];
  const collect = async (view) => {
    views.push(view);
    const text = await p.evaluate(`document.documentElement.outerHTML + ' ' + [...document.querySelectorAll('input,textarea')].map((e) => e.value).join(' ') + ' ' + document.title`);
    for (const [name, value] of Object.entries(ctx.forbidden)) if (text.includes(value)) (seen[name] = seen[name] || []).push(view);
    for (const m of await markers(p)) found.add(m);
  };
  const hover = () => p.evaluate(`(() => { for (const el of [...document.querySelectorAll('[data-desc],[data-tip]')].slice(0, 80)) { el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true })); el.dispatchEvent(new FocusEvent('focusin', { bubbles: true })); } return true; })()`);
  await hover(); await collect('start');
  for (const id of await p.evaluate(`[...document.querySelectorAll('nav.tabs [role=tab]')].filter((b) => !b.hidden).map((b) => b.id)`)) {
    await p.domClick(`document.getElementById(${J(id)})`); await sleep(700);
    await p.evaluate(`(() => { for (const d of document.querySelectorAll('details')) d.open = true; return true; })()`);
    await hover(); await sleep(300); await collect(id);
    if (id !== 'tab-docs') continue;
    for (const doc of await p.evaluate(`[...document.querySelectorAll('button.doc-item[data-doc]')].map((b) => b.getAttribute('data-doc'))`)) {
      await p.domClick(`[...document.querySelectorAll('button.doc-item[data-doc]')].find((b) => b.getAttribute('data-doc') === ${J(doc)})`, doc);
      await p.waitFor(`!!document.querySelector('#view-docs .md')`, 5000); await sleep(400); await collect('doc ' + doc);
    }
  }
  return { seen, found: [...found].sort(), views };
}
async function visit(p) {
  await p.go(ctx.page + '/', 1800);
  const rendered = await p.waitFor(`!!document.querySelector('#app nav.tabs [role=tab]')`, 10000);
  if (!rendered.ok) throw new Error('strona nie narysowała widoku');
  return sweep(p);
}

// Treść dokumentów (`/api/doc`) spec pokazuje anonimowi jawnie („redakcja sekretów bez zmian”), a ścieżki i pseudonimy z Q2 dotyczą pól statusu.
// Wystąpienia w zakładce „Dokumenty” są więc zapisywane w wyniku (do decyzji), ale nie oblewają scenariusza.
const inDocuments = (view) => view === 'tab-docs' || view.startsWith('doc ');

suite.add('ANON-1', 'bez sesji: poza treścią dokumentów żadnego adresu e-mail, ścieżki bezwzględnej ani autora zgody; treść dociera', async (s) => {
  const p = await newPage(s.c, 'anon');
  const r = await visit(p);
  const missing = ctx.reached.filter((m) => !r.found.includes(m));
  const leaked = {}; const documents = [];
  for (const [name, views] of Object.entries(r.seen)) {
    const status = views.filter((v) => !inDocuments(v));
    if (status.length) leaked[name] = status;
    if (views.some(inDocuments)) documents.push(name);
  }
  // autor zgody i login nie pochodzą z dokumentów: nie mogą być nigdzie
  const people = ['login', 'approval-by'].filter((name) => r.seen[name]);
  return { ok: Object.keys(leaked).length === 0 && people.length === 0 && missing.length === 0, leaked, shownInDocuments: documents.sort(), people, missing, views: r.views };
});

suite.add('ANON-2', 'kontrola: po zalogowaniu te same dane są widoczne, więc ANON-1 miał co ukrywać', async (s) => {
  const p = await newPage(s.c, 'approver');
  await loginPage(p, ctx.page, 'approver');
  const r = await visit(p);
  const hidden = ctx.control.filter((name) => !r.seen[name]);
  return { ok: hidden.length === 0, visible: Object.keys(r.seen).sort(), expectedButMissing: hidden };
});

suite.add('ANON-3', 'po wylogowaniu otwarta strona przestaje pokazywać dane konta', async (s) => {
  const p = await newPage(s.c, 'leaving');
  await loginPage(p, ctx.page, 'approver'); await p.go(ctx.page + '/', 1800);
  await p.waitFor(`!!document.querySelector('[data-session="logout"]')`, 10000);
  await p.domClick(`document.querySelector('[data-session="logout"]')`);
  const out = await p.waitFor(`!!document.querySelector('[data-session-state="anonymous"]')`, 10000);
  await sleep(2500);
  const html = await p.evaluate('document.documentElement.outerHTML');
  const left = Object.entries(ctx.forbidden).filter(([, value]) => html.includes(value)).map(([name]) => name);   // widok startowy, bez dokumentów
  return { ok: out.ok && left.length === 0, loggedOut: out.ok, stillShown: left };
});

await suite.main();
