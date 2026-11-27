// K8 na originie prototypów: treść cudzych komentarzy, odpowiedzi, nazw ekranów, tytułów i data-proto-id nie wykonuje się u czytającego.
// ctx: { proto, slug, expected: [...], payloads: [...], controlFields: [...] }
import { Suite, connect, openPanel, openThreadByPin } from '../prototype/lib.mjs';
import { ctx, J, sleep, newPage, hits, domAudit, markers } from './common.mjs';

const suite = new Suite('xss_proto');
suite.setup(async () => ({ c: await connect() }));
suite.teardown(async (s) => { if (s) await s.c.closeAll(); });

suite.add('XSS-O1', 'silnik prototypów: wątki, odpowiedzi, uchwyty i nazwy ekranów bez wykonania', async (s) => {
  const p = await newPage(s.c, 'reader');
  await p.go(`${ctx.proto}/${ctx.slug}/index.html`, 1800);
  const found = new Set(); const bad = [];
  const collect = async (view) => { for (const m of await markers(p)) found.add(m); for (const b of await domAudit(p)) bad.push(view + ': ' + b); };
  await p.waitFor(`!!document.querySelector('.anno-threads-btn')`, 10000);
  await collect('ekran');
  await openPanel(p); await sleep(500); await collect('panel');
  for (const filter of await p.evaluate(`[...document.querySelectorAll('.anno-filters button')].map((b, i) => i)`)) {
    await p.domClick(`document.querySelectorAll('.anno-filters button')[${filter}]`); await sleep(250); await collect('filtr ' + filter);
  }
  const cards = await p.evaluate(`document.querySelectorAll('.anno-panel .anno-card').length`);
  for (let i = 0; i < cards; i++) {
    await p.evaluate(`(() => { const c = document.querySelectorAll('.anno-panel .anno-card')[${i}]; if (c) c.click(); return !!c; })()`); await sleep(300); await collect('karta ' + i);
  }
  const pins = await p.evaluate(`[...new Set([...document.querySelectorAll('button.anno-pin:not(.anno-pin-static)')].map((b) => b.textContent.trim()))]`);
  for (const n of pins) { await openThreadByPin(p, n); await collect('pinezka ' + n); }
  // tryb komentowania: etykieta elementu z data-proto-id (podświetlenie i okienko nowego komentarza)
  await p.evaluate(`(() => { document.activeElement && document.activeElement.blur(); return true; })()`);
  await p.key('c');
  const ids = await p.evaluate(`document.querySelectorAll('.screen [data-proto-id]').length`);
  for (let i = 0; i < ids; i++) {
    await p.evaluate(`(() => { const el = document.querySelectorAll('.screen [data-proto-id]')[${i}]; el.scrollIntoView({ block: 'center' }); for (const t of ['mouseover', 'mousemove', 'click']) el.dispatchEvent(new MouseEvent(t, { bubbles: true, clientX: el.getBoundingClientRect().x + 2, clientY: el.getBoundingClientRect().y + 2 })); return true; })()`);
    await sleep(300); await collect('uchwyt ' + i); await p.key('Escape'); await p.key('c');
  }
  const executed = await hits(p);
  const missing = ctx.expected.filter((m) => !found.has(m));
  return { ok: executed.length === 0 && bad.length === 0 && missing.length === 0 && cards > 0, executed, domTraces: [...new Set(bad)].slice(0, 20), missing, reached: [...found].sort(), cards, pins, protoIds: ids };
});

suite.add('XSS-O2', 'galeria i pytania ekspertów originu prototypów bez wykonania', async (s) => {
  const p = await newPage(s.c, 'gallery');
  const found = new Set(); const bad = []; const executed = [];
  for (const path of ['/', '/_experts']) {
    await p.go(ctx.proto + path, 1500);
    for (const m of await markers(p)) found.add(m);
    for (const b of await domAudit(p)) bad.push(path + ': ' + b);
    executed.push(...await hits(p));
  }
  const missing = ctx.expectedGallery.filter((m) => !found.has(m));
  return { ok: executed.length === 0 && bad.length === 0 && missing.length === 0, executed, domTraces: bad.slice(0, 20), missing, reached: [...found].sort() };
});

suite.add('XSS-O3', 'kontrola detektora: surowe ładunki wstawione jako HTML na originie prototypów wykonują się', async (s) => {
  const p = await newPage(s.c, 'control'); await p.go(`${ctx.proto}/${ctx.slug}/index.html`, 1200);
  const ran = await p.evaluate(`(async () => { const box = document.createElement('div'); document.body.appendChild(box); box.innerHTML = ${J(ctx.payloads.join(''))};
    for (const a of box.querySelectorAll('a')) a.click(); await new Promise((r) => setTimeout(r, 1200)); return window.__hits; })()`);
  const notRun = ctx.controlFields.filter((f) => !ran.includes(f));
  return { ok: notRun.length === 0, ran, notRun };
});

await suite.main();
