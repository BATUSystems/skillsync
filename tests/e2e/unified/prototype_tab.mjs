// Zakładka „Prototypy” strony statusu: widok bez sesji, ramka i nowa karta, izolacja ramki (K17 d, K20), widok z sesją i „Zaloguj w prototypach”.
// ctx: { page, proto, slug, people: {nazwa: napis}, title: {nazwa: napis}, pseudonym, facts: [napisy wiersza] }
import { Suite, connect, env, openPanel } from '../prototype/lib.mjs';
import { ctx, J, sleep, newPage, loginPage, sessionOf } from './common.mjs';

const SANDBOX = 'allow-scripts allow-same-origin allow-forms';
const DENIED = ['approver', 'acceptor', 'developer'];
const PROTO_URL = `${ctx.proto}/${ctx.slug}/`;
const TARGET = ctx.page + '/_login';   // dokąd ramka próbuje przenieść kartę: nawigacja byłaby widoczna w adresie
const ROW = `document.querySelector('[data-proto="${ctx.slug}"]')`;
const FRAME = `document.querySelector('#view-prototypes iframe')`;

const suite = new Suite('prototype_tab');
suite.setup(async () => ({ c: await connect() }));
suite.teardown(async (s) => { if (s) await s.c.closeAll(); });

// ---------------------------------------------------------------- inne karty tej przeglądarki (także otwarte przez stronę)
const targets = async () => (await (await fetch(`http://127.0.0.1:${env.chromePort}/json/list`)).json()).filter((t) => t.type === 'page');
const idsNow = async () => new Set((await targets()).map((t) => t.id));
async function newTab(before, url, timeout = 15000) {
  const t0 = Date.now(); let fresh = [];
  while (Date.now() - t0 < timeout) {
    fresh = (await targets()).filter((t) => !before.has(t.id));
    const hit = fresh.find((t) => t.url === url);
    if (hit) return { hit, fresh };
    await sleep(200);
  }
  return { hit: null, fresh };
}
function inTab(target, expression) {
  return new Promise((ok, fail) => {
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    const timer = setTimeout(() => { ws.close(); fail(new Error('karta nie odpowiada')); }, 10000);
    ws.onopen = () => ws.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: { expression, awaitPromise: true, returnByValue: true } }));
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id !== 1) return; clearTimeout(timer); ws.close(); m.result?.exceptionDetails || m.error ? fail(new Error('eval w karcie')) : ok(m.result.result.value); };
    ws.onerror = () => { clearTimeout(timer); fail(new Error('ws karty')); };
  });
}
const closeTab = (target) => fetch(`http://127.0.0.1:${env.chromePort}/json/close/${target.id}`).catch(() => {});

// ---------------------------------------------------------------- strona i jej ramka
// Konteksty skryptów karty (także ramek): kod w kontekście ramki działa z jej originem i pod jej sandboxem.
function watchContexts(p) {
  const contexts = [];
  p.listeners.push((m) => { if (m.method === 'Runtime.executionContextCreated' && m.params.context.auxData?.isDefault) contexts.push(m.params.context); });
  return contexts;
}
async function frameContext(p, contexts) {
  const t0 = Date.now();
  while (Date.now() - t0 < 10000) {
    const tree = await p.send('Page.getFrameTree');
    const child = (tree.frameTree.childFrames || []).find((f) => f.frame.url === PROTO_URL);
    const found = child && [...contexts].reverse().find((c) => c.auxData.frameId === child.frame.id);
    if (found) {
      const run = async (expression, userGesture = false) => {
        const r = await p.send('Runtime.evaluate', { expression, contextId: found.id, awaitPromise: true, returnByValue: true, userGesture });
        if (r.exceptionDetails) throw new Error('eval w ramce: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text).slice(0, 300));
        return r.result.value;
      };
      try { if (await run(`document.readyState === 'complete' && !!document.querySelector('.anno-threads-btn')`)) return { run, id: found.id }; } catch (e) { /* kontekst poprzedniego dokumentu */ }
    }
    await sleep(200);
  }
  throw new Error('ramka prototypu się nie wczytała');
}
async function protoTab(p) {
  const shown = await p.waitFor(`!!document.getElementById('tab-prototypes') && !document.getElementById('tab-prototypes').hidden`, 10000);
  if (!shown.ok) throw new Error('brak zakładki „Prototypy”');
  await p.domClick(`document.getElementById('tab-prototypes')`);
  const row = await p.waitFor(`!!${ROW} && ${ROW}.getClientRects().length > 0`, 8000);
  if (!row.ok) throw new Error('brak wiersza prototypu');
  await sleep(600);
}
const view = (p) => p.evaluate(`(() => { const v = document.getElementById('view-prototypes'); const row = ${ROW};
  return { count: document.getElementById('pCount').textContent,
    facts: [...row.querySelectorAll('.q-facts > *')].map((e) => e.textContent.trim()),
    name: row.querySelector('.q-name')?.textContent || '',
    controls: [...v.querySelectorAll('a[href], button, input, select, textarea')].filter((e) => e.getClientRects().length > 0)
      .map((e) => [...e.attributes].map((a) => a.name).find((n) => n.startsWith('data-proto') || n === 'data-session') || ('inne: ' + e.textContent.trim().slice(0, 40))).sort(),
    consents: document.querySelectorAll('button[data-approve]').length,
    actions: document.querySelectorAll('[data-proto-act], [data-proto-manage], [data-proto-project], [data-proto-handoff], [data-proto-confirm]').length }; })()`);
const whole = (p) => p.evaluate(`document.documentElement.outerHTML + ' ' + [...document.querySelectorAll('input,textarea')].map((e) => e.value).join(' ') + ' ' + document.title`);
const present = (text, values) => Object.entries(values).filter(([, value]) => text.includes(value)).map(([name]) => name);

// ---------------------------------------------------------------- scenariusze
suite.add('ANON-6A', 'bez sesji: wiersz z akceptacjami i wątkami, tylko „Otwórz”, ramka i logowanie; żadnych akcji, e-maili ani ścieżek', async (s) => {
  const p = await newPage(s.c, 'anon'); s.contexts = watchContexts(p); s.anon = p;
  await p.go(ctx.page + '/', 1800); await protoTab(p);
  const v = await view(p);
  const leaked = present(await whole(p), { ...ctx.people, ...ctx.title });
  const missing = ctx.facts.filter((f) => !v.facts.includes(f));
  const wanted = ['data-proto-embed', 'data-proto-open', 'data-session'];
  return { ok: v.count === '1' && missing.length === 0 && v.name.includes('KAN-title-') && J(v.controls) === J(wanted) && v.actions === 0 && v.consents === 0 && leaked.length === 0, view: v, missing, leaked };
});

suite.add('ANON-6B', '„Pokaż w zakładce”: ramka z sandboxem pokazuje prototyp i wątek; nie czyta strony, nie otwiera okien, nie nawiguje top; postMessage bez skutku', async ({ anon: p, contexts }) => {
  await p.domClick(`document.querySelector('[data-proto-embed="${ctx.slug}"]')`);
  const opened = await p.waitFor(`!!${FRAME}`, 5000);
  if (!opened.ok) return { ok: false, why: 'brak ramki' };
  const attrs = await p.evaluate(`(() => { const f = ${FRAME}; return { sandbox: f.getAttribute('sandbox'), referrerpolicy: f.getAttribute('referrerpolicy'), src: f.getAttribute('src'), frames: document.querySelectorAll('iframe').length }; })()`);
  const frame = await frameContext(p, contexts);
  await frame.run(`(() => { document.querySelector('.anno-threads-btn').click(); return true; })()`, true); await sleep(800);
  const inside = await frame.run(`({ origin: location.origin, referrer: document.referrer, text: document.body.innerText, html: document.documentElement.outerHTML })`);
  const threadShown = inside.text.includes('KAN-comment-');
  const identities = present(inside.html, ctx.people);
  const posts = () => p.net.filter((l) => l.startsWith('POST ' + ctx.page)).length;
  const before = { posts: posts(), tabs: await idsNow() };
  const reach = await frame.run(`(async () => { const out = {}; const t = (k, f) => { try { f(); out[k] = 'READ'; } catch (e) { out[k] = e.name; } };
    t('parentDocument', () => parent.document.title); t('topDocument', () => top.document.body.innerHTML); t('topAddress', () => top.location.href); t('frameElement', () => { if (!window.frameElement) throw new DOMException('', 'NoFrameElement'); });
    try { await fetch(${J(ctx.page + '/api/status')}, { credentials: 'include' }); out.status = 'READ'; } catch (e) { out.status = e.name; }
    parent.postMessage({ type: 'approve', kind: 'cp' }, '*'); return out; })()`);
  const tries = {
    location: `(() => { try { top.location.href = ${J(TARGET)}; return 'brak wyjątku'; } catch (e) { return e.name; } })()`,
    link: `(() => { const a = document.createElement('a'); a.href = ${J(TARGET)}; a.target = '_top'; document.body.appendChild(a); a.click(); a.remove(); return 'kliknięty'; })()`,
    form: `(() => { const f = document.createElement('form'); f.method = 'get'; f.action = ${J(TARGET)}; f.target = '_top'; document.body.appendChild(f); f.submit(); f.remove(); return 'wysłany'; })()`,
    popup: `(() => { window.open(${J(TARGET)}, '_blank'); return 'wywołane'; })()`,   // sam wynik open() nic nie mówi (COOP strony); liczą się karty (newTabs)
  };
  const navigation = {};
  for (const [name, expression] of Object.entries(tries)) {
    const said = await frame.run(expression, true); await sleep(900);
    navigation[name] = { said, pageAt: await p.evaluate('location.origin + location.pathname'), frameKept: await p.evaluate(`!!${FRAME}`) };
  }
  const newTabs = (await targets()).filter((t) => !before.tabs.has(t.id)).map((t) => t.url);
  const newPosts = posts() - before.posts;
  await p.domClick(`document.getElementById('tab-progress')`); await sleep(500);
  const removedWithTab = await p.evaluate(`!document.querySelector('iframe')`);
  const ok = attrs.sandbox === SANDBOX && attrs.referrerpolicy === 'no-referrer' && attrs.src === PROTO_URL && attrs.frames === 1
    && inside.origin === ctx.proto && inside.referrer === '' && threadShown && identities.length === 0
    && ['parentDocument', 'topDocument', 'topAddress', 'status'].every((k) => reach[k] !== 'READ') && reach.frameElement === 'NoFrameElement'
    && Object.values(navigation).every((n) => n.pageAt === ctx.page + '/' && n.frameKept)
    && newTabs.length === 0 && newPosts === 0 && removedWithTab;
  return { ok, attrs, origin: inside.origin, referrer: inside.referrer, threadShown, identities, reach, navigation, newTabs, newPosts, removedWithTab };
});

suite.add('ANON-6C', '„Otwórz”: nowa karta na originie prototypów, bez opener i Referer; karta strony zostaje', async ({ anon: p }) => {
  await protoTab(p);
  const before = await idsNow();
  const link = await p.evaluate(`(() => { const a = document.querySelector('[data-proto-open="${ctx.slug}"]'); return { href: a.href, target: a.target, rel: a.rel }; })()`);
  await p.front(); await p.clickEl(`document.querySelector('[data-proto-open="${ctx.slug}"]')`);
  const { hit, fresh } = await newTab(before, PROTO_URL);
  if (!hit) return { ok: false, link, fresh: fresh.map((t) => t.url) };
  await sleep(1200);
  const there = await inTab(hit, `({ at: location.href, opener: window.opener === null, referrer: document.referrer, top: window === window.top, engine: !!document.querySelector('.anno-threads-btn'), html: document.documentElement.outerHTML })`);
  const identities = present(there.html, ctx.people); delete there.html;
  await closeTab(hit);
  const stayed = await p.evaluate('location.origin + location.pathname');
  return { ok: link.target === '_blank' && /noopener/.test(link.rel) && /noreferrer/.test(link.rel) && fresh.length === 1 && there.at === PROTO_URL && there.opener && there.referrer === '' && there.top && there.engine && identities.length === 0 && stayed === ctx.page + '/', link, there, identities, stayed };
});

suite.add('ANON-6D', 'kontrola dla 6B: ta sama ramka bez atrybutu sandbox otwiera okno i nawiguje top (więc 6B mierzy sandbox)', async ({ anon: p, contexts }) => {
  await protoTab(p);
  await p.domClick(`document.querySelector('[data-proto-embed="${ctx.slug}"]')`);
  const first = await frameContext(p, contexts);
  const seen = contexts.length;
  await p.evaluate(`(() => { const f = ${FRAME}; f.removeAttribute('sandbox'); f.src = f.src; return true; })()`);
  await sleep(1500);
  const frame = await frameContext(p, contexts.slice(seen));
  const before = await idsNow();
  await frame.run(`(() => { window.open(${J(TARGET)}, '_blank'); return true; })()`, true); await sleep(900);
  const popups = (await targets()).filter((t) => !before.has(t.id));
  for (const t of popups) await closeTab(t);
  await p.front();
  await frame.run(`(() => { try { top.location.href = ${J(TARGET)}; } catch (e) { return e.name; } return 'ok'; })()`, true).catch(() => 'kontekst zamknięty nawigacją');
  const moved = await p.waitFor(`location.href === ${J(TARGET)}`, 8000);
  return { ok: first.id !== frame.id && popups.length === 1 && moved.ok, reloaded: first.id !== frame.id, popups: popups.length, pageAt: await p.evaluate('location.origin + location.pathname') };
});

suite.add('ANON-6E', 'kontrola dla 6A i widok z sesją: akceptacje i wątki na stronie (pseudonimy), akcje dostępne, przy ramce bez przycisków zgód', async (s) => {
  const p = await newPage(s.c, 'approver'); s.signed = p;
  await loginPage(p, ctx.page, 'approver'); await p.go(ctx.page + '/', 1800);
  await p.waitFor(`!!document.querySelector('button[data-approve]')`, 10000);
  const consents = () => p.evaluate(`document.querySelectorAll('button[data-approve]').length`);
  const before = await consents();
  await protoTab(p);
  const v = await view(p);
  const titleShown = present(v.name, ctx.title);
  await p.domClick(`document.querySelector('[data-proto-embed="${ctx.slug}"]')`); await sleep(1000);
  const framed = { frame: await p.evaluate(`!!${FRAME}`), consents: await consents() };
  await p.domClick(`document.querySelector('[data-proto-frame-close]')`); await sleep(800);
  const after = await consents();
  await p.domClick(`document.querySelector('[data-proto-manage="${ctx.slug}"]')`);
  const loaded = await p.waitFor(`!!document.querySelector('#protoDetail [data-screen="s2"]') && !!document.querySelector('#protoDetail [data-thread]')`, 10000);
  if (!loaded.ok) return { ok: false, why: 'brak widoku akceptacji i wątków', view: v };
  const detail = await p.evaluate(`(() => { const d = document.getElementById('protoDetail'); const screen = (id) => { const r = d.querySelector('[data-screen="' + id + '"]'); return { text: r.textContent, acts: [...r.querySelectorAll('[data-proto-act]')].map((b) => b.getAttribute('data-proto-act')).sort() }; };
    const t = d.querySelector('[data-thread]'); return { s2: screen('s2'), s3: screen('s3'), threads: d.querySelectorAll('[data-thread]').length, thread: t.textContent, author: t.querySelector('.q-run')?.textContent || '', frames: document.querySelectorAll('iframe').length }; })()`);
  const emails = present(await whole(p), { 'author-login': ctx.people['author-login'] });
  const wanted = ['data-proto-embed', 'data-proto-handoff', 'data-proto-manage', 'data-proto-open', 'data-proto-project'];
  const ok = before > 0 && framed.frame && framed.consents === 0 && after === before && J(v.controls) === J(wanted) && titleShown.length === Object.keys(ctx.title).length
    && detail.s2.text.includes('akceptacja: ' + ctx.pseudonym) && !detail.s3.text.includes('akceptacja:') && J(detail.s2.acts) === J(['unaccept']) && J(detail.s3.acts) === J(['accept'])
    && detail.threads === 1 && detail.thread.includes('KAN-comment-') && detail.author.length > 0 && !detail.author.includes('@') && emails.length === 0 && detail.frames === 0;
  return { ok, consents: { before, framed, after }, view: v, titleShown, detail, emails };
});

suite.add('ANON-6F', '„Zaloguj w prototypach”: kończy w galerii originu prototypów, z sesją bez approver, acceptor i developer; karta strony zostaje', async ({ signed: p }) => {
  await p.go(ctx.page + '/', 1800); await protoTab(p);
  const boardRoles = (await sessionOf(p)).user?.roles || [];   // kontrola: konto miało na stronie role, które przekazana sesja ma stracić
  const before = await idsNow();
  const link = await p.evaluate(`(() => { const a = document.querySelector('[data-proto-handoff]'); return { href: a.href, text: a.textContent.trim() }; })()`);
  await p.front(); await p.clickEl(`document.querySelector('[data-proto-handoff]')`);
  const { hit, fresh } = await newTab(before, ctx.proto + '/', 20000);
  if (!hit) return { ok: false, link, fresh: fresh.map((t) => t.url.split('#')[0]) };
  await sleep(1500);
  const there = await inTab(hit, `fetch('/_api/v1/session').then((r) => r.json()).then((s) => ({ at: location.href, opener: window.opener === null, top: window === window.top, gallery: !!document.querySelector('a[href*="${ctx.slug}/"]'), roles: s.user ? s.user.roles : null }))`);
  await closeTab(hit);
  const stayed = await p.evaluate('location.origin + location.pathname');
  const roles = there.roles || [];
  return { ok: boardRoles.includes('approver') && boardRoles.includes('acceptor') && link.text === 'Zaloguj w prototypach' && link.href === ctx.proto + '/_handoff/begin' && fresh.length === 1 && there.at === ctx.proto + '/' && there.opener && there.top && there.gallery && !!there.roles && !DENIED.some((r) => roles.includes(r)) && stayed === ctx.page + '/', boardRoles, link, there, stayed };
});

await suite.main();
