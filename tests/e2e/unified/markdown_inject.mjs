// K21: treść dokumentu albo kroku planu nie może dać żadnego elementu ze stylem, który przykrywa stronę ze zgodami.
// Przycisk „jest sobą”, gdy elementFromPoint w jego środku zwraca jego albo jego dziecko.
// ctx: { page, url, signIn: bool, host, doc, consentWindow: bool }
import { Suite, connect } from '../prototype/lib.mjs';
import { ctx, J, sleep, newPage, loginPage, env } from './common.mjs';

const suite = new Suite('markdown_inject');
suite.setup(async () => {
  const c = await connect(); const p = await newPage(c, 'victim');
  if (ctx.signIn) await loginPage(p, ctx.page, 'approver');
  return { c, p };
});
suite.teardown(async (s) => { if (s) await s.c.closeAll(); });

// Co na stronie dostało styl z treści: każdy element z atrybutem style ustawiającym position: fixed (strona sama takich nie pisze), jego prostokąt
// i to, co leży pod pięcioma punktami okna i na przyciskach zgody.
const measure = (p) => p.evaluate(`(() => {
  const bad = [...document.querySelectorAll('[style]')].filter((el) => el.getClientRects().length && /position\\s*:\\s*fixed/i.test(el.getAttribute('style') || ''));
  const w = innerWidth, h = innerHeight;
  const hit = (x, y) => { const e = document.elementFromPoint(x, y); return !!e && bad.some((b) => b === e || b.contains(e)); };
  const points = [[w / 2, h / 2], [10, 10], [w - 10, 10], [10, h - 10], [w - 10, h - 10]].map(([x, y]) => hit(x, y));
  const center = (el) => { const r = el.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; };
  const buttons = [...document.querySelectorAll('button[data-approve], [data-consent-confirm], [data-consent-cancel], [data-session="logout"]')].filter((b) => b.getClientRects().length)
    .map((b) => { const e = document.elementFromPoint(...center(b)); return { button: b.getAttribute('data-approve') || b.getAttribute('data-session') || [...b.attributes].map((x) => x.name).find((n) => n.startsWith('data-')), covered: hit(...center(b)), itself: !!e && (e === b || b.contains(e)) }; });
  return { viewport: { w, h }, links: bad.slice(0, 3).map((a) => { const r = a.getBoundingClientRect(); const cs = getComputedStyle(a); return { tag: a.tagName, href: (a.href || '').slice(0, 80), target: a.target || null, position: cs.position, zIndex: cs.zIndex, box: { x: r.x, y: r.y, width: r.width, height: r.height } }; }), count: bad.length, pointsCovered: points, buttons };
})()`);
const covers = (m) => m.count > 0 && (m.pointsCovered.some(Boolean) || m.buttons.some((b) => b.covered));
const csp = (log) => log.find((x) => x)?.['content-security-policy'] || null;

suite.add('MD-1', 'widok „Postęp” (tytuł kroku z planu): żadnego odnośnika ze stylem nad stroną ani nad przyciskami zgody', async ({ p }) => {
  const headers = [];
  p.listeners.push((m) => { if (m.method === 'Network.responseReceived' && m.params.type === 'Document') headers.push(Object.fromEntries(Object.entries(m.params.response.headers).map(([k, v]) => [k.toLowerCase(), v]))); });
  await p.go(ctx.url, 2500);
  await p.waitFor(`!!document.querySelector('#app nav.tabs [role=tab]')`, 10000); await sleep(1200);
  const m = await measure(p); await p.shot('md-1-postep');
  // „Zatwierdź CP” i „Wyloguj” leżą w swoich punktach (przed przewinięciem: oba są na pierwszym ekranie)
  const own = ctx.signIn ? ['cp', 'logout'].filter((name) => !m.buttons.some((b) => b.button === name && b.itself)) : [];
  return { ok: !covers(m) && m.count === 0 && own.length === 0, notOnTop: own, ...m, csp: csp(headers) };
});

suite.add('MD-2', 'zakładka „Dokumenty” (treść planu): to samo', async ({ p }) => {
  await p.domClick(`document.getElementById('tab-docs')`); await sleep(700);
  await p.evaluate(`(() => { for (const d of document.querySelectorAll('details')) d.open = true; return true; })()`);
  await p.domClick(`[...document.querySelectorAll('button.doc-item[data-doc]')].find((b) => b.getAttribute('data-doc') === ${J(ctx.doc)})`, ctx.doc);
  await p.waitFor(`!!document.querySelector('#view-docs .md')`, 8000); await sleep(1200);
  const m = await measure(p); await p.shot('md-2-dokument');
  return { ok: !covers(m) && m.count === 0, ...m };
});

if (ctx.consentWindow) suite.add('MD-3', 'otwarte okno zgody: nic z treści nie leży nad „Potwierdź”', async ({ p }) => {
  await p.domClick(`document.getElementById('tab-progress')`); await sleep(900);
  const ready = await p.waitFor(`(() => { const b = document.querySelector('button[data-approve="cp"]'); return !!b && !b.disabled; })()`, 10000);
  if (!ready.ok) throw new Error('brak przycisku CP');
  // przycisk bywa przykryty (to właśnie sprawdzamy), więc okno otwiera zdarzenie z DOM, nie kliknięcie wskaźnikiem
  await p.domClick(`document.querySelector('button[data-approve="cp"]')`); await sleep(900);
  const open = await p.evaluate(`!!document.querySelector('[data-dialog="consent"]')`);
  const m = await measure(p); await p.shot('md-3-okno-zgody');
  await p.key('Escape');
  const own = ['data-consent-confirm', 'data-consent-cancel'].filter((name) => !m.buttons.some((b) => b.button === name && b.itself));
  return { ok: open && own.length === 0 && m.count === 0, windowOpen: open, notOnTop: own, ...m };
});

await suite.main();
