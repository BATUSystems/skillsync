// K2: które kanarki widać w DOM przeglądarki bez sesji. Wynik jest zapisem (ocenia go strona pythonowa).
// ctx: { urls: [...], tabs: [id zakładek strony], panel: bool (panel wątków originu prototypów), canaries: {nazwa: napis} }
import { Suite, connect, openPanel } from '../prototype/lib.mjs';
import { ctx, J, sleep, newPage } from './common.mjs';

const suite = new Suite('anonymous_dom');
suite.setup(async () => ({ c: await connect() }));
suite.teardown(async (s) => { if (s) await s.c.closeAll(); });

suite.add('DOM-1', 'przejście widoków bez sesji', async (s) => {
  const p = await newPage(s.c, 'anon'); const seen = {}; const views = [];
  const collect = async (view) => {
    views.push(view);
    const text = await p.evaluate(`document.documentElement.outerHTML + ' ' + (document.body ? document.body.innerText : '') + ' ' + [...document.querySelectorAll('input,textarea')].map((e) => e.value).join(' ')`);
    for (const [name, value] of Object.entries(ctx.canaries)) {
      const escaped = value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      if (text.includes(value) || text.includes(escaped)) (seen[name] = seen[name] || []).push(view);
    }
  };
  const expand = () => p.evaluate(`(() => { for (const d of document.querySelectorAll('details')) d.open = true; for (const b of [...document.querySelectorAll('[role=tabpanel]:not([hidden]) [aria-expanded="false"]')].slice(0, 60)) b.click(); return true; })()`);
  for (const url of ctx.urls) {
    await p.go(url, 1800); await collect(url);
    for (const id of ctx.tabs || []) {
      const shown = await p.waitFor(`!!document.getElementById(${J(id)}) && !document.getElementById(${J(id)}).hidden`, 10000);
      if (!shown.ok) throw new Error('brak zakładki ' + id);
      await p.domClick(`document.getElementById(${J(id)})`); await sleep(800);
      await expand(); await sleep(1000); await expand(); await sleep(800); await collect(id);
    }
    if (ctx.panel && (await p.evaluate(`!!document.querySelector('.anno-threads-btn')`))) {
      await openPanel(p); await sleep(600); await collect(url + ' panel');
      const cards = await p.evaluate(`document.querySelectorAll('.anno-panel .anno-card').length`);
      for (let i = 0; i < cards; i++) { await p.evaluate(`(() => { const c = document.querySelectorAll('.anno-panel .anno-card')[${i}]; if (c) c.click(); return true; })()`); await sleep(400); await collect(url + ' wątek ' + i); }
    }
  }
  return { ok: true, seen, views };
});

await suite.main();
