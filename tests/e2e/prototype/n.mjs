// Scenariusze uzupełniające: N-01 … N-09 (klawisze wątków, tryb logowania do podglądu, odpytywanie, ponawianie, kolory statusów, filtry,
// pasek na szerokościach, akceptacja projektu z nieaktualnym prototypem, odrzucone wpisy z banera).
import { rmSync } from 'node:fs';
import {
  Suite, sleep, workspace, startServer, connect, errors, env, PORT1, PORT2, session, apiLogin, apiOps, apiPost, apiRaw, secret, mail, cid, proto, createThread,
  btn, btnExact, byProto, DLG, dlgText, dlgOpen, saveState, panelText, bannerText, wasSaved, openPanel, closePanelIfOpen, openThreadByPin, queueLength, pickFilter,
  countCards, filtersState, sessionPage, setAcceptance, consoleVerdict,
} from './lib.mjs';

const suite = new Suite('n');
let ws; let srv; let c; let ANN; let BAR; let DEV; let ACC;
const url = (slug = 'demo') => `${srv.base}/${slug}/`;
const panelHead = (p) => p.evaluate(`document.querySelector('.anno-panel').innerText.replace(/\\s+/g, ' ').slice(0, 40)`);

suite.setup(async () => {
  ws = workspace('n');
  for (const slug of ['plan', 'migr', 'shell', 'goto']) rmSync(`${ws.proj}/docs/prototypes/${slug}`, { recursive: true, force: true });
  srv = await startServer(ws, { port: PORT1 });
  c = await connect();
  ANN = await session(srv.base, 'anna'); BAR = await session(srv.base, 'bartek'); DEV = await session(srv.base, 'dawid'); ACC = await session(srv.base, 'celina');
  return {};
});
suite.teardown(async () => { if (c) await c.closeAll(); if (srv) await srv.stop(); });

const S = {};
async function seedThreads() {
  if (S.threads) return S.threads;
  const defs = [['s1', 's1-karta-tytul'], ['s1', 's1-usuwany'], ['s1', 's1-dalej'], ['s2', 's2-nazwa'], ['s2', 's2-zapisz']];
  S.threads = [];
  for (const [screen, id] of defs) S.threads.push((await createThread(ANN, 'demo', screen, id, 'N wątek ' + id)).threadId);
  return S.threads;
}

suite.add('N-01', 'Nawigacja wątków [ / ]', async () => {
  await seedThreads();
  const { p } = await sessionPage(c, 'anna', srv.base, 'anna'); await p.go(url());
  await openPanel(p); await p.evaluate(`document.activeElement && document.activeElement.blur(); true`);
  const seq = [];
  for (const k of [']', ']', ']', '[', '[']) { await p.key(k); seq.push(`${k}${(/Wątek #(\d+)/.exec(await panelHead(p)) || [])[1]}`); }
  await p.evaluate(`document.querySelector('.anno-panel textarea[aria-label="Odpowiedź"]').focus(); true`);
  await p.key(']');
  const typed = await p.evaluate(`document.querySelector('.anno-panel textarea[aria-label="Odpowiedź"]').value`);
  const stay = (/Wątek #(\d+)/.exec(await panelHead(p)) || [])[1];
  await p.dispose();
  return { ok: seq.join(',') === ']1,]2,]3,[2,[1' && typed === ']' && stay === '1', sequence: seq, typedInField: typed, threadAfterField: stay };
});

suite.add('N-02', 'serve --login-to-view', async () => {
  const s2 = await startServer(ws, { port: PORT2, extra: ['--login-to-view'] });
  try {
    const get = (path, opt = {}) => fetch(s2.base + path, { redirect: 'manual', headers: { Accept: 'text/html' }, ...opt });
    const doc = await get('/demo/'); const loc = doc.headers.get('location');
    const api = await fetch(`${s2.base}/_api/v1/prototypes/demo/operations?after=0`, { redirect: 'manual' });
    const file = await fetch(`${s2.base}/demo/screens.css`, { redirect: 'manual' });
    const loginPage = await get('/_login'); const sess = await fetch(`${s2.base}/_api/v1/session`, { redirect: 'manual' });
    const bad = await fetch(`${s2.base}/_api/v1/login`, { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: s2.base }, body: JSON.stringify({ login: mail('anna'), password: 'zle-haslo-0000' }) });
    const p = await c.page('anon-login'); await p.go(s2.base + '/demo/');
    const landed = await p.evaluate(`location.pathname`);
    // logowanie w formularzu strony logowania
    await p.evaluate(`(() => { const i = document.querySelector('input[type=email], input[name=login], input[name=email], input[type=text]'); if (i) i.focus(); return !!i; })()`);
    await p.type(mail('anna'));
    await p.evaluate(`(() => { const i = document.querySelector('input[type=password]'); if (i) i.focus(); return !!i; })()`);
    await p.type(secret('anna')); await p.key('Enter');
    const shown = await p.waitFor(`document.querySelectorAll('section.screen').length >= 2`, 10000);
    await p.dispose();
    return { ok: doc.status === 302 && /\/_login/.test(loc || '') && api.status === 401 && file.status === 401 && loginPage.status === 200 && sess.status === 200 && bad.status === 401 && /_login/.test(landed) && shown.ok, document: [doc.status, loc], api: api.status, file: file.status, loginPage: loginPage.status, session: sess.status, badLogin: bad.status, landedOn: landed, contentAfterLogin: shown.ok };
  } finally { await s2.stop(); }
});

suite.add('N-03', 'Odpytywanie przy ukrytej karcie', async () => {
  const B = (await sessionPage(c, 'bartek-poll', srv.base, 'bartek')).p;
  const times = []; B.listeners.push((m) => { if (m.method === 'Network.requestWillBeSent' && m.params.request.method === 'GET' && /\/operations/.test(m.params.request.url)) times.push(Date.now()); });
  await B.go(url()); await openPanel(B);
  const t0 = Date.now();
  await createThread(ANN, 'demo', 's2', 's2-typ', 'N-03 wpis od A');
  const seen = await B.waitFor(`/N-03 wpis od A/.test(${panelText})`, 15000, 100);
  // ukryta karta
  await B.evaluate(`(() => { Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' }); Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange')); return 1; })()`);
  await sleep(2000); times.length = 0;
  await sleep(80000);
  const gaps = times.slice(1).map((t, i) => Math.round((t - times[i]) / 1000));
  await B.dispose();
  return { ok: seen.ok && seen.ms <= 6000 && times.length >= 2 && gaps.every((g) => g >= 20 && g <= 45), visibleMs: seen.ms, hiddenGetsIn80s: times.length, gapsSeconds: gaps };
}, { timeout: 140000 });

suite.add('N-04', 'Ponawianie 5xx z narastającą przerwą', async () => {
  await seedThreads();
  const { p } = await sessionPage(c, 'anna-5xx', srv.base, 'anna'); await p.go(url()); await openThreadByPin(p, 1);
  const posts = []; let failures = 3;
  await p.intercept(['*/_api/v1/prototypes/demo/operations*'], async (ev, api) => { if (ev.request.method === 'POST') { posts.push(Date.now()); if (failures > 0) { failures--; return api.fulfill(503, { error: 'unavailable' }); } } api.continue(); });
  await p.evaluate(`document.querySelector('.anno-panel textarea[aria-label="Odpowiedź"]').focus(); true`); await p.type('N-04 ponawiany wpis'); await p.key('Enter', { ctrl: true });
  await sleep(1500); const queued = { ok: !/Błąd zapisu/.test(await p.evaluate(saveState)) };
  const midQueue = await queueLength(p);
  const done = await p.waitFor(`${wasSaved}`, 100000, 500);
  const gaps = posts.slice(1).map((t, i) => t - posts[i]);
  const rejected = await p.evaluate(`Object.keys(localStorage).filter((k) => k.includes(':rejected:') || k.includes('rejected')).map((k) => { try { const v = JSON.parse(localStorage.getItem(k)); return (Array.isArray(v) ? v : v.items || []).length; } catch (e) { return 0; } }).reduce((a, b) => a + b, 0)`);
  const copies = (await apiOps(srv.base, 'demo')).operations.filter((o) => JSON.stringify(o.payload || {}).includes('N-04 ponawiany wpis')).length;
  await p.interceptOff(); await p.dispose();
  const rising = gaps.length >= 3 && gaps.every((g, i) => i === 0 || g >= gaps[i - 1] * 0.9) && gaps[gaps.length - 1] > gaps[0];
  return { ok: queued.ok && midQueue === 1 && done.ok && rising && rejected === 0 && copies === 1 && posts.length === 4, queuedChip: queued.ok, queueLengthMid: midQueue, savedAfter: done.ok, rising, attempts: posts.length, gapsMs: gaps, rejectedEntries: rejected, copiesOnServer: copies };
}, { timeout: 150000 });

suite.add('N-05', 'Kolory pinezek według statusu', async () => {
  const th = await seedThreads();
  const r = [await proto(ws, 'triage', 'demo', th[1], '--class', 'disputed'), await proto(ws, 'decide', 'demo', th[2], '--kind', 'agent', '--outcome', 'apply', '--text', 'tak'),
    await proto(ws, 'resolve', 'demo', th[3], '--reason', 'done'), await proto(ws, 'decide', 'demo', th[4], '--kind', 'agent', '--outcome', 'defer', '--text', 'później')];
  const { p } = await sessionPage(c, 'anna-st', srv.base, 'anna'); await p.go(url());
  const pins = await p.evaluate(`[...document.querySelectorAll('button.anno-pin:not(.anno-pin-static)')].map((b) => ({ label: b.getAttribute('aria-label'), cls: (/anno-st-\\w+/.exec(b.className) || [''])[0], bg: getComputedStyle(b).backgroundColor }))`);
  await openPanel(p); await pickFilter(p, 'Wszystkie');
  const cards = await countCards(p);
  await p.dispose();
  const statuses = ['Otwarty', 'Czeka na decyzję', 'Zdecydowany', 'Zamknięty', 'Odłożony'];
  const byStatus = statuses.map((s) => pins.find((x) => (x.label || '').includes(s)));
  const classes = new Set(byStatus.map((x) => x && x.cls)); const colors = new Set(byStatus.map((x) => x && x.bg));
  return { ok: r.every((x) => x.code === 0) && byStatus.every(Boolean) && classes.size === 5 && colors.size === 5 && cards >= 5, cliExits: r.map((x) => x.code), pins: pins.map((x) => `${x.label}|${x.cls}|${x.bg}`), allFilterCards: cards };
});

suite.add('N-06', 'Filtry Decyzje agenta i Nowe z licznikiem > 1', async () => {
  const th = await seedThreads();
  const { p } = await sessionPage(c, 'anna-nf', srv.base, 'anna'); await p.go(url()); await sleep(1500);
  await p.go('about:blank');
  for (const t of th.slice(0, 3)) await apiPost(BAR, 'demo', { cid: cid('r'), type: 'reply', threadId: t, payload: { message: { id: 'm-' + cid('m').slice(2), text: 'N-06 nowy wpis B' } } });
  await p.go(url()); await openPanel(p);
  const f = await filtersState(p); const num = (name) => Number(((f.find((x) => x.t.startsWith(name)) || {}).t || '').replace(/\D+/g, ''));
  const res = {};
  for (const name of ['Nowe od ostatniej wizyty', 'Decyzje agenta']) { await pickFilter(p, name); res[name] = { counter: num(name), cards: await countCards(p) }; }
  await p.dispose();
  return { ok: Object.values(res).every((x) => x.counter > 1 && x.counter === x.cards), res };
});

suite.add('N-07', 'Pasek na 1280 i 1440 z zalogowanym chipem', async () => {
  const { p } = await sessionPage(c, 'anna-bar', srv.base, 'anna'); await p.go(url());
  const snap = () => p.evaluate(`(() => { const bar = document.querySelector('.doc-toolbar'); const items = [...bar.querySelectorAll(':scope > *, .proto-toolbar > *')].filter((e) => e.getClientRects().length && e.getBoundingClientRect().width > 0); const rects = items.map((e) => { const r = e.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)]; });
    const leaf = items.filter((e) => !items.some((o) => o !== e && e.contains(o))); let overlap = 0; for (let i = 0; i < leaf.length; i++) for (let j = i + 1; j < leaf.length; j++) { const a = leaf[i].getBoundingClientRect(), b = leaf[j].getBoundingClientRect(); if (!(a.right <= b.left + 1 || b.right <= a.left + 1 || a.bottom <= b.top + 1 || b.bottom <= a.top + 1)) overlap++; }
    return { overlap, barH: bar.offsetHeight, varH: getComputedStyle(document.documentElement).getPropertyValue('--proto-toolbar-height').trim(), hscroll: document.documentElement.scrollWidth > document.documentElement.clientWidth, sig: JSON.stringify(rects) }; })()`);
  const seq = []; const covers = [];
  for (const w of [1440, 1280, 1440, 1280, 1440]) { await p.size(w, 900); await sleep(700); seq.push({ w, ...(await snap()) });
    // chip konta przy otwartym panelu Wątki: element w jego środku musi być chipem albo jego potomkiem
    await openPanel(p); await sleep(300);
    const cov = await p.evaluate(`(() => { const chip = document.querySelector('[data-anno-focus="user-chip"]'); if (!chip) return { chip: false }; const r = chip.getBoundingClientRect(); const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); const open = !!document.querySelector('.anno-panel.open, .anno-panel[open]'); return { chip: true, open, covered: !(top && (top === chip || chip.contains(top))), by: top ? (top.className && top.className.toString ? top.className.toString().slice(0, 60) : top.tagName) : null }; })()`);
    covers.push({ w, ...cov }); await closePanelIfOpen(p);
  }
  await p.dispose();
  const stable = seq[0].sig === seq[2].sig && seq[2].sig === seq[4].sig && seq[1].sig === seq[3].sig;
  const chipPresent = covers.every((x) => x.chip && x.open);   // brak chipa albo panelu = zwykły FAIL, nie znany defekt
  const covered = covers.some((x) => x.covered);
  const chipFree = chipPresent && !covered;
  const restOk = seq.every((x) => x.overlap === 0 && !x.hscroll && x.varH === x.barH + 'px') && stable && chipPresent;
  return { ok: restOk && !covered, overlaps: seq.map((x) => x.overlap), heights: seq.map((x) => x.barH), stable, chipFree, restOk, defectObserved: restOk && covered, chipCover: covers };
});

suite.add('N-08', 'Akceptacja projektu z nieaktualnym prototypem (UI)', async () => {
  await setAcceptance(ACC, DEV, srv.base, 'alfa', 's1', false); await setAcceptance(ACC, DEV, srv.base, 'alfa', 's2', false);
  await setAcceptance(ACC, DEV, srv.base, 'beta', 's1', false); await setAcceptance(ACC, DEV, srv.base, 'beta', 's2', false);
  const { p } = await sessionPage(c, 'celina-stale', srv.base, 'celina'); await p.go(url('alfa') + '#s2');
  await p.domClick(`document.querySelector('#s2 .anno-accept-open')`); await p.waitFor(dlgOpen, 4000);
  await p.domClick(`document.getElementById('anno-accept-project')`);
  await p.waitFor(`/beta/.test(${dlgText})`, 8000);
  const orig = ws.read('beta'); ws.write('beta', 'index.html', orig.replace('Treść s2.', 'Treść s2 zmieniona w trakcie.'));
  await sleep(600);
  await p.domClick(btnExact('Akceptuję', DLG));
  const shown = await p.waitFor(`/nieaktualn|zmieniono|przeładuj/i.test(${dlgText}) && /beta/.test(${dlgText})`, 10000);
  const text = await p.evaluate(dlgText); const stillOpen = await p.evaluate(dlgOpen);
  ws.write('beta', 'index.html', orig);
  const acc = {}; for (const slug of ['alfa', 'beta']) acc[slug] = Object.values((await apiOps(srv.base, slug)).acceptance || {}).filter((x) => x.valid).length;
  await p.dispose();
  return { ok: shown.ok && stillOpen && acc.alfa === 0 && acc.beta === 0, dialog: (text || '').slice(0, 240), stillOpen, acceptedAfter: acc };
});

suite.add('N-09', 'Odrzucone wpisy przez baner', async () => {
  await seedThreads();
  const { p } = await sessionPage(c, 'anna-rej', srv.base, 'anna'); await p.go(url()); await openThreadByPin(p, 1);
  await p.intercept(['*/_api/v1/prototypes/demo/operations*'], async (ev, api) => { if (ev.request.method === 'POST') return api.fulfill(403, { error: 'forbidden', message: 'forbidden' }); api.continue(); });
  await p.evaluate(`document.querySelector('.anno-panel textarea[aria-label="Odpowiedź"]').focus(); true`); await p.type('N-09 odrzucony wpis'); await p.key('Enter', { ctrl: true });
  const chip = await p.waitFor(`/Błąd zapisu \\(1\\)/.test(${saveState})`, 15000);
  const banner = await p.evaluate(bannerText);
  await p.domClick(`[...document.querySelectorAll('.anno-banners button')].find((b) => /Pokaż|odrzucone/i.test(b.textContent))`);
  const viaBanner = await p.waitFor(`/Odrzucone wpisy/.test(${dlgText})`, 5000); const t1 = await p.evaluate(dlgText);
  await p.key('Escape'); await sleep(300);
  await p.domClick(`document.querySelector('[data-anno-focus="user-chip"]')`); await sleep(400);
  const menuItem = await p.evaluate(`!![...document.querySelectorAll('.anno-user-menu button')].find((b) => /odrzucone/i.test(b.textContent))`);
  let viaMenu = { ok: false }; let t2 = null;
  if (menuItem) {
    await p.domClick(`[...document.querySelectorAll('.anno-user-menu button')].find((b) => /odrzucone/i.test(b.textContent))`);
    viaMenu = await p.waitFor(`/Odrzucone wpisy/.test(${dlgText})`, 5000); t2 = await p.evaluate(dlgText);
  }
  await p.key('Escape'); await p.interceptOff(); await p.dispose();
  const restOk = chip.ok && /Serwer odrzucił 1 wpis/.test(banner) && viaBanner.ok && /odpowiedź/.test(t1 || '') && /403/.test(t1 || '');
  return { ok: restOk && menuItem && viaMenu.ok && t1 === t2, restOk, banner: banner.slice(0, 160), menuEntry: menuItem, sameList: t1 === t2, defectObserved: restOk && !menuItem, dialogPreview: (t1 || '').slice(0, 400) };
});

await suite.main();
