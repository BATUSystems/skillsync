// Ścieżki o podwyższonym ryzyku z przeglądu silnika (R-01 … R-06): start bez sieci, zawieszone żądanie, edycja i nawigacja w panelu,
// kontrast numerów pinezek, klawiatura na `data-goto` w elementach nienatywnych, powrót karty z tła. Reguła M1: `restOk` = przygotowanie
// i dane zebrane, `defectObserved = restOk && naruszenie`. Spec W1 (tryby pracy): offline także przy przekroczonym czasie żądania.
import {
  Suite, sleep, workspace, startServer, connect, errors, PORT1, session, apiOps, createThread, proto,
  saveState, bannerText, wasSaved, openPanel, closePanelIfOpen, openThreadByPin, queueLength, sessionPage, commentOn, btnExact, DLG, dlgText, dlgOpen, clickBtn,
} from './lib.mjs';

const suite = new Suite('ryzyka');
let ws; let srv; let c; let ANN; let BAR;
const url = (slug) => `${srv.base}/${slug}/`;
const REQUEST_TIMEOUT_MS = 20000;   // parametr R-02: silnik uznaje żądanie za nieudane najpóźniej po tym czasie (założenie: <= 20 s)
const toolbarText = `((document.querySelector('.doc-toolbar') || document.body).innerText || '')`;
const countText = (ops, text) => ops.filter((o) => JSON.stringify(o).includes(text)).length;
const evidence = (bad, restOk, extra = {}) => ({ ok: restOk && bad.length === 0, restOk, defectObserved: restOk && bad.length > 0, violations: bad.length, firstViolations: bad.slice(0, 6), ...extra });

suite.setup(async () => {
  ws = workspace('ryzyka');
  srv = await startServer(ws, { port: PORT1 });
  c = await connect();
  ANN = await session(srv.base, 'anna'); BAR = await session(srv.base, 'bartek');
  return {};
});
suite.teardown(async () => { if (c) await c.closeAll(); if (srv) await srv.stop(); });

suite.add('R-01', 'Start bez sieci: tryb Offline, powrót do pełnego bez przeładowania', async () => {
  // prototyp plan; błąd sieci na GET /_api/v1/session przy pierwszym wczytaniu
  const { p } = await sessionPage(c, 'r01', srv.base, 'anna');
  let failed = 0;
  await p.intercept(['*/_api/v1/session*'], async (ev, api) => { failed += 1; api.fail(); });
  await p.go(url('plan')); await sleep(1500);
  const offline = await p.waitFor(`/Offline/.test(${toolbarText})`, 8000);
  await p.evaluate(`window.__r01 = 'bez-przeladowania'; true`);
  // pisanie zablokowane do pierwszej udanej odpowiedzi: tryb komentowania nie otwiera okienka
  await p.evaluate(`window.scrollTo(0, 0); document.activeElement && document.activeElement.blur(); true`); await p.key('c');
  await p.domClick(`document.querySelector('[data-proto-id="s1-tytul"]')`).catch(() => {}); await sleep(500);
  const composerWhileOffline = await p.evaluate(`!!document.querySelector('.anno-composer-pop')`);
  await p.key('Escape'); await p.key('Escape');
  await p.interceptOff();
  const full = await p.waitFor(`!/Offline/.test(${toolbarText}) && /Anna/.test(document.body.innerText)`, 45000, 300);
  const sameDocument = (await p.evaluate(`window.__r01`)) === 'bez-przeladowania';
  const saved = full.ok ? await commentOn(p, 's1-tytul', 'R-01 wpis po powrocie').catch(() => false) : false;
  const ops = await apiOps(srv.base, 'plan');
  await p.dispose();
  const restOk = failed > 0 && offline.ok;
  const bad = [];
  if (composerWhileOffline) bad.push({ why: 'pisanie nie jest zablokowane w trybie Offline' });
  if (!full.ok) bad.push({ why: 'tryb nie wraca do pełnego po udanej odpowiedzi' });
  if (!sameDocument) bad.push({ why: 'strona została przeładowana' });
  if (full.ok && (!saved || countText(ops.operations, 'R-01 wpis po powrocie') !== 1)) bad.push({ why: 'nie da się pisać po powrocie', saved });
  return evidence(bad, restOk, { sessionRequestsFailed: failed, sawOffline: offline.ok });
}, { timeout: 150000 });

suite.add('R-02', `Zawieszone żądanie zapisu: Offline po ${REQUEST_TIMEOUT_MS / 1000} s, kolejka, wysyłka po odblokowaniu`, async () => {
  const text = 'R-02 wpis przy zawieszonym żądaniu';
  const th = await createThread(ANN, 'beta', 's1', 'beta-s1-przycisk', 'R-02 wątek');
  const { p } = await sessionPage(c, 'r02', srv.base, 'anna'); await p.go(url('beta')); await sleep(1200);
  await p.evaluate(`window.__r02 = 'bez-przeladowania'; true`);
  const held = [];
  await p.intercept(['*/_api/v1/prototypes/beta/operations*'], async (ev, api) => { if (ev.request.method === 'POST') { held.push({ ev, api }); return; } api.continue(); });   // odpowiedź nie przychodzi
  await openThreadByPin(p, 1);
  await p.evaluate(`document.querySelector('.anno-panel textarea[aria-label="Odpowiedź"]').focus(); true`); await p.type(text); await p.key('Enter', { ctrl: true });
  const t0 = Date.now();
  const offline = await p.waitFor(`/Offline/.test(${saveState})`, REQUEST_TIMEOUT_MS + 12000, 250);
  const offlineAfterMs = Date.now() - t0;
  const queued = await queueLength(p);
  const opsWhileHeld = await apiOps(srv.base, 'beta');
  // odblokowanie: zawieszone żądania kontynuują, przechwytywanie wyłączone; kolejka wysyła się bez przeładowania
  await p.interceptOff(); for (const h of held) h.api.continue();
  const saved = await p.waitFor(`${wasSaved} && !/Offline/.test(${saveState})`, 60000, 300);
  const q2 = await queueLength(p);
  const ops = await apiOps(srv.base, 'beta');
  const same = (await p.evaluate(`window.__r02`)) === 'bez-przeladowania';
  await p.dispose();
  const restOk = held.length >= 1 && th.status < 300;
  const bad = [];
  if (!offline.ok) bad.push({ why: 'pasek nie pokazuje Offline po przekroczeniu czasu żądania', limitMs: REQUEST_TIMEOUT_MS });
  else if (offlineAfterMs > REQUEST_TIMEOUT_MS + 5000) bad.push({ why: 'Offline po zbyt długim czasie', ms: offlineAfterMs, limitMs: REQUEST_TIMEOUT_MS });
  if (queued < 1) bad.push({ why: 'wpis nie został w kolejce', queued });
  if (!saved.ok || q2 > 0) bad.push({ why: 'kolejka nie wysłała się po odblokowaniu', queueAfter: q2 });
  if (countText(ops.operations, text) !== 1) bad.push({ why: 'wpis nie jest na serwerze dokładnie raz', count: countText(ops.operations, text), whileHeld: countText(opsWhileHeld.operations, text) });
  if (!same) bad.push({ why: 'strona została przeładowana' });
  return evidence(bad, restOk, { requestTimeoutMs: REQUEST_TIMEOUT_MS, offlineAfterMs, heldRequests: held.length, queued });
}, { timeout: 200000 });

suite.add('R-03', 'Edycja, potem nawigacja w panelu: kolejny widok wątku ma pełną treść', async () => {
  // prototyp alfa; dwa wątki autora: edycja niezapisana w wątku 1 jest szkicem (pytanie „Porzucić szkic?”)
  const m1 = 'R-03 wiadomość pierwszego wątku'; const m2 = 'R-03 wiadomość drugiego wątku';
  await createThread(ANN, 'alfa', 's1', 'alfa-s1-tytul', m1); await createThread(ANN, 'alfa', 's1', 'alfa-s1-przycisk', m2);
  const { p } = await sessionPage(c, 'r03', srv.base, 'anna'); await p.go(url('alfa')); await sleep(1200);
  const view = () => p.evaluate(`(() => { const t = document.querySelector('.anno-panel-title'); const msgs = [...document.querySelectorAll('.anno-panel .anno-msg')].map((m) => m.innerText.replace(/\\s+/g, ' ').trim()); return { title: t ? t.textContent.trim() : '', msgs }; })()`);
  const edit = () => p.domClick(`[...document.querySelectorAll('.anno-panel .anno-msg')][0] && [...[...document.querySelectorAll('.anno-panel .anno-msg')][0].querySelectorAll('button')].find((b) => b.textContent.trim() === 'Edytuj')`);
  const confirmIfAsked = async () => { await sleep(500); const asked = await p.evaluate(dlgOpen); if (asked) { await p.domClick(btnExact('Porzuć szkic', DLG)).catch(() => {}); await sleep(600); } return asked; };
  const full = (v, want) => v.title.length > 0 && v.msgs.length >= 1 && v.msgs.some((x) => x.includes(want));
  await openThreadByPin(p, 1); await sleep(400);
  const v1 = await view(); await edit(); await sleep(400);
  const editing = await p.evaluate(`!!document.querySelector('.anno-panel textarea')`);
  // droga A: „Wszystkie wątki”
  await p.domClick(`[...document.querySelectorAll('.anno-panel button')].find((b) => /Wszystkie wątki/.test((b.getAttribute('aria-label') || '') + b.textContent))`).catch(() => {});
  const askedA = await confirmIfAsked();
  await openThreadByPin(p, 2); await sleep(500); const v2 = await view();
  // droga B: edycja w wątku 2, potem pinezka wątku 1
  await edit(); await sleep(300);
  await p.evaluate(`window.scrollTo(0, 0); true`);
  await p.domClick(`[...document.querySelectorAll('button.anno-pin:not(.anno-pin-static)')].find((x) => x.textContent.trim() === '1')`).catch(() => {});
  const askedB = await confirmIfAsked(); await sleep(500); const v3 = await view();
  await p.dispose();
  const restOk = full(v1, m1) && editing;
  const bad = [];
  if (!askedA) bad.push({ why: 'opuszczenie widoku z niezapisaną edycją („Wszystkie wątki”) nie pyta o szkic' });
  if (!full(v2, m2)) bad.push({ why: 'widok wątku 2 po edycji i „Wszystkie wątki” bez pełnej treści', title: v2.title, msgs: v2.msgs.length });
  if (!full(v3, m1)) bad.push({ why: 'widok wątku 1 po edycji i pinezce innego wątku bez pełnej treści', title: v3.title, msgs: v3.msgs.length, askedB });
  return evidence(bad, restOk, { askedA, askedB, v2: { title: v2.title, msgs: v2.msgs.length }, v3: { title: v3.title, msgs: v3.msgs.length } });
}, { timeout: 120000 });

suite.add('R-04', 'Kontrast numerów pinezek >= 4,5:1 dla wszystkich statusów i motywów', async () => {
  // prototyp demo: wątki we wszystkich pięciu statusach ze słownika (open, awaiting, decided, closed, deferred)
  const defs = [['s1', 's1-karta-tytul'], ['s1', 's1-usuwany'], ['s1', 's1-dalej'], ['s2', 's2-nazwa'], ['s2', 's2-zapisz']]; const th = [];
  for (const [screen, id] of defs) th.push((await createThread(ANN, 'demo', screen, id, 'R-04 ' + id)).threadId);
  const cli = [await proto(ws, 'triage', 'demo', th[1], '--class', 'disputed'), await proto(ws, 'decide', 'demo', th[2], '--kind', 'agent', '--outcome', 'apply', '--text', 'tak'),
    await proto(ws, 'resolve', 'demo', th[3], '--reason', 'done'), await proto(ws, 'decide', 'demo', th[4], '--kind', 'agent', '--outcome', 'defer', '--text', 'później')];
  const { p } = await sessionPage(c, 'r04', srv.base, 'anna'); await p.size(1440, 900); await p.go(url('demo')); await sleep(1200);
  const setTheme = async (theme) => { for (let i = 0; i < 3; i++) { if ((await p.evaluate(`document.documentElement.dataset.theme`)) === theme) return true; await p.evaluate(`(() => { const b = [...document.querySelectorAll('.proto-toolbar button')].find((x) => /Motyw/.test(x.textContent + (x.getAttribute('aria-label') || ''))); if (b) b.click(); return 1; })()`); await sleep(250); } return (await p.evaluate(`document.documentElement.dataset.theme`)) === theme; };
  const MEASURE = `(() => { const cv = document.createElement('canvas'); cv.width = cv.height = 1; const cx = cv.getContext('2d', { willReadFrequently: true });
    const rgba = (css) => { cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#000'; cx.fillStyle = css; cx.fillRect(0, 0, 1, 1); const d = cx.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255]; };
    const over = (f, b) => [0, 1, 2].map((i) => f[i] * f[3] + b[i] * (1 - f[3]));
    const bgOf = (el) => { let acc = null; const layers = []; for (let e = el; e; e = e.parentElement) { const c = rgba(getComputedStyle(e).backgroundColor); if (c[3] > 0) { layers.push(c); if (c[3] >= 0.99) break; } }
      let base = [255, 255, 255]; for (const l of layers.reverse()) base = over(l, base); return base; };
    const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
    return [...document.querySelectorAll('button.anno-pin:not(.anno-pin-static)')].map((b) => { const bg = bgOf(b); const fg0 = rgba(getComputedStyle(b).color); const fg = over(fg0, bg); const l1 = lum(fg), l2 = lum(bg);
      return { label: b.getAttribute('aria-label'), cls: (/anno-st-\\w+/.exec(b.className) || [''])[0], ratio: Math.round(((Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05)) * 100) / 100 }; }); })()`;
  const statuses = ['Otwarty', 'Czeka na decyzję', 'Zdecydowany', 'Zamknięty', 'Odłożony']; const results = {}; const bad = []; let restOk = cli.every((x) => x.code === 0);
  for (const theme of ['light', 'dark']) {
    const set = await setTheme(theme); await sleep(300);
    const pins = await p.evaluate(MEASURE); results[theme] = pins.map((x) => `${x.label}:${x.ratio}`);
    if (!set) restOk = false;
    for (const s of statuses) { const x = pins.find((q) => (q.label || '').includes(s)); if (!x) { restOk = false; continue; } if (x.ratio < 4.5) bad.push({ theme, status: s, ratio: x.ratio, cls: x.cls }); }
  }
  await p.dispose();
  return evidence(bad, restOk, { ratios: results });
}, { timeout: 120000 });

suite.add('R-05', 'Klawiatura na data-goto w elementach nienatywnych (tryb Klikalny)', async () => {
  // prototyp goto: s1 ma `tr[data-goto="s2"]` i `div[data-goto="s3"]`; Tab osiąga element, Enter albo Spacja przechodzi do celu
  const p = await c.page('r05'); await p.go(url('goto')); await sleep(1200);
  await p.domClick(clickBtn).catch(() => {});
  const targets = [['tr', '[data-proto-id="goto-wiersz"]', 's2'], ['div', '[data-proto-id="goto-karta"]', 's3']]; const bad = []; let restOk = true; const seen = [];
  for (const [kind, sel, dest] of targets) for (const key of ['Enter', ' ']) {
    await p.evaluate(`location.hash = '#s1'; window.scrollTo(0, 0); document.activeElement && document.activeElement.blur && document.activeElement.blur(); true`); await sleep(1200);
    const info = await p.evaluate(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); return e ? { role: e.getAttribute('role'), tabindex: e.getAttribute('tabindex') } : null; })()`);
    if (!info) { restOk = false; continue; }
    await p.evaluate(`document.body.focus(); true`);
    let reached = false;
    for (let i = 0; i < 120 && !reached; i++) { await p.key('Tab'); reached = await p.evaluate(`document.activeElement === document.querySelector(${JSON.stringify(sel)})`); }
    let current = null;
    if (reached) { await p.key(key); await sleep(1500); current = await p.evaluate(`(document.querySelector('.screen.is-current') || {}).id || location.hash.slice(1)`); }
    seen.push(`${kind}/${key === ' ' ? 'Spacja' : key}:${reached ? current : 'nieosiągalny'}`);
    if (info.role !== 'button') bad.push({ element: kind, why: 'brak role="button"', role: info.role });
    if (!reached) bad.push({ element: kind, key, why: 'Tab nie osiąga elementu' });
    else if (current !== dest) bad.push({ element: kind, key, why: 'klawisz nie przechodzi do celu', expected: dest, got: current });
  }
  await p.dispose();
  return evidence(bad, restOk, { results: seen });
}, { timeout: 200000 });

suite.add('R-06', 'Powrót karty z tła: dane odświeżają się w <= 6 s', async () => {
  // karta „w tle” (visibilityState=hidden), w tym czasie inna osoba dodaje wątek; po powrocie (visible + visibilitychange) pinezka pojawia się w <= 6 s
  const { p } = await sessionPage(c, 'r06', srv.base, 'anna'); await p.go(url('beta')); await sleep(1500);
  const setVisible = (hidden) => p.evaluate(`(() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => ${hidden} }); Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => ${JSON.stringify(hidden ? 'hidden' : 'visible')} }); document.dispatchEvent(new Event('visibilitychange')); return 1; })()`);
  const pins = () => p.evaluate(`document.querySelectorAll('button.anno-pin:not(.anno-pin-static)').length`);
  const before = await pins();
  await setVisible(true); await sleep(500);
  const th = await createThread(BAR, 'beta', 's2', 'beta-s2-przycisk', 'R-06 wątek dodany w tle');
  await sleep(1500);
  const t0 = Date.now(); await setVisible(false);
  const back = await p.waitFor(`document.querySelectorAll('button.anno-pin:not(.anno-pin-static)').length > ${before}`, 6000, 150);
  const ms = Date.now() - t0;
  await p.dispose();
  return evidence(back.ok ? [] : [{ why: 'brak odświeżenia po powrocie karty w 6 s', pinsBefore: before }], th.status < 300 && before >= 0, { refreshMs: ms, limitMs: 6000 });
}, { timeout: 60000, critical: false });

suite.add('R-07', 'Motyw systemowy bez zapisanego wyboru: ciemny/jasny od pierwszego renderu i bez JS', async () => {
  // prefers-color-scheme (CDP) i pusty localStorage: (a) bez JS tło odpowiada motywowi systemu, (b) z JS od pierwszego renderu, bez mignięcia przeciwnym motywem
  const BG = (scheme) => `(function () { const cv = document.createElement('canvas'); cv.width = cv.height = 1; const cx = cv.getContext('2d', { willReadFrequently: true });
    const rgba = (css) => { cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#000'; cx.fillStyle = css; cx.fillRect(0, 0, 1, 1); const d = cx.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255]; };
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; const lum = (c) => 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
    return function () { for (const e of [document.body, document.documentElement]) { if (!e) continue; const c = rgba(getComputedStyle(e).backgroundColor); if (c[3] >= 0.99) return { lum: lum(c), via: e.tagName }; }
      const cs = getComputedStyle(document.documentElement).colorScheme || ''; const l = cs.includes(${JSON.stringify(scheme)}) ? (${JSON.stringify(scheme)} === 'dark' ? 0 : 1) : (cs.includes('dark') && !cs.includes('light') ? 0 : 1); return { lum: l, via: 'canvas:' + cs }; }; })()`;
  const SAMPLER = (scheme) => `(() => { window.__bgs = []; window.__painted = false; const s = ${BG(scheme)}; const take = (t) => { try { if (document.documentElement) { const r = s(); window.__bgs.push({ t, lum: r.lum, via: r.via, painted: window.__painted }); } } catch (e) { /* za wcześnie */ } };
    document.addEventListener('readystatechange', () => take('rs:' + document.readyState)); new MutationObserver(() => take('mut')).observe(document, { childList: true, subtree: true });
    let n = 0; const loop = () => { window.__painted = true; take('raf'); if (++n < 40) requestAnimationFrame(loop); }; requestAnimationFrame(loop); take('start'); })()`;
  const bad = []; const seen = {}; let restOk = true;
  for (const scheme of ['dark', 'light']) {
    const dark = scheme === 'dark'; const okLum = (l) => (dark ? l < 0.3 : l > 0.7);
    const p = await c.page('r07-' + scheme);
    await p.media('prefers-color-scheme', scheme);
    await p.go(url('alfa')); await p.evaluate(`localStorage.clear(); true`);
    // (a) bez JS
    await p.send('Emulation.setScriptExecutionDisabled', { value: true });
    let a = null;
    try { await p.go(url('alfa')); a = await p.evaluate(`(${BG(scheme)})()`); } finally { await p.send('Emulation.setScriptExecutionDisabled', { value: false }); }
    // (b) z JS: próbki od początku dokumentu
    const reg = await p.send('Page.addScriptToEvaluateOnNewDocument', { source: SAMPLER(scheme) });
    await p.go(url('alfa'), 1500);
    const samples = await p.evaluate(`window.__bgs || []`); const after = await p.evaluate(`(${BG(scheme)})()`);
    await p.send('Page.removeScriptToEvaluateOnNewDocument', { identifier: reg.identifier }).catch(() => {});
    const painted = samples.filter((x) => x.painted);
    await p.dispose();
    if (!a || !painted.length) { restOk = false; continue; }
    seen[scheme] = { noJs: Math.round(a.lum * 1000) / 1000, painted: painted.length, minLum: Math.round(Math.min(...painted.map((x) => x.lum)) * 1000) / 1000, maxLum: Math.round(Math.max(...painted.map((x) => x.lum)) * 1000) / 1000, afterStart: Math.round(after.lum * 1000) / 1000 };
    if (!okLum(a.lum)) bad.push({ scheme, mode: 'bez JS', lum: a.lum, via: a.via });
    const flash = painted.filter((x) => !okLum(x.lum));
    if (flash.length) bad.push({ scheme, mode: 'z JS: mignięcie przeciwnym motywem', samples: flash.slice(0, 3).map((x) => `${x.t}:${Math.round(x.lum * 100) / 100}:${x.via}`) });
    if (!okLum(after.lum)) bad.push({ scheme, mode: 'z JS po starcie silnika', lum: after.lum });
  }
  return evidence(bad, restOk, { measured: seen });
}, { timeout: 120000, critical: false });

await suite.main();
