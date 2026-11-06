// Panel komentarzy „zwężenie + skalowanie” (N-R1 … N-R8), powłoka N-S1, pasek na telefonie i menu „⋯” (N-R9, N-R10). Prototyp `plan` (28 ekranów) z serwera, konto zalogowane, ekrany s1, s2, s7, s28, oba motywy.
// Okno >= 1024 px: tryb zwężenia (panel zabiera szerokość, makieta pomniejsza się proporcjonalnie); < 1024 px: tryb nakładki.
// Wszystkie pomiary przez DOM (prostokąty elementów ze słownika i atrybuty); skala liczona z szerokości ramki ekranu po / przed, nie z CSS.
// Reguła M1: `restOk` = dane zebrane (ekran, ramka, panel, kontrolki), `defectObserved = restOk && naruszenie`; to kryteria nowego silnika, nie znane defekty.
// Wariant mobile (D28b, B7b, D244): N-R1…N-R8 i N-R10 biegną drugi raz z mierzonymi ekranami w widoku mobile (przełącznik lokalny
// `[data-proto-view-switch]`, iframe `iframe[data-proto-view="mobile"]` z kontraktu). Ramką ekranu jest wtedy iframe widoku, a elementy
// `data-proto-id` i pinezki pochodzą z dokumentu iframe (współrzędne przeliczone na okno strony). Wynik mobile jest w polu `mobile`;
// `ok` scenariusza wymaga obu wariantów. Bez wariantu mobile: N-R9 (pasek nie zależy od widoku ekranu; pasek bez kontrolki widoku mierzy
// V-02 w widok.mjs) i N-S1 (powtórka według planu D28b, poza N-R1…N-R10).
import { Suite, sleep, workspace, startServer, connect, env, PORT1, session, createThread, sessionPage, openPanel, closePanelIfOpen, errors, pageErrors, commentBtn, apiOps, apiPost, cid } from './lib.mjs';

const suite = new Suite('nr');
let ws; let srv; let c; let ANN; let P;
const url = () => `${srv.base}/plan/`;
const NARROW = [1920, 1440, 1280, 1024]; const OVERLAY = [768, 375]; const THEMES = ['light', 'dark']; const SCREENS = ['s1', 's2', 's7', 's28'];
const ANCHORS = {};   // ekran -> data-proto-id kotwicy wątku
const ANCHORS_M = {}; const SETUP_M = { problems: [] };   // wariant mobile: kotwice wątków mobile z dokumentu iframe widoku

suite.setup(async () => {
  ws = workspace('nr');
  srv = await startServer(ws, { port: PORT1 });
  c = await connect();
  ANN = await session(srv.base, 'anna');
  ({ p: P } = await sessionPage(c, 'nr', srv.base, 'anna'));
  await P.size(1440, 900); await P.go(url());
  for (const sid of SCREENS) {
    const id = await P.evaluate(`(() => { const e = [...document.querySelectorAll('#${sid} [data-proto-id]')].find((x) => { const r = x.getBoundingClientRect(); return r.width > 30 && r.height > 12 && x.getClientRects().length; }); return e ? e.getAttribute('data-proto-id') : null; })()`);
    if (!id) throw new Error('brak elementu z data-proto-id w ' + sid);
    ANCHORS[sid] = id;
    await createThread(ANN, 'plan', sid, id, 'N-R wątek ' + sid);
  }
  // wariant mobile: pinezki wątków desktop nie są w iframe widoku (K6), więc każdy ekran dostaje wątek mobile (payload.view)
  try {
    await P.go(url());
    for (const sid of SCREENS) {
      await P.evaluate(`document.getElementById(${JSON.stringify(sid)}).scrollIntoView({ block: 'start', behavior: 'instant' }); true`);
      if (!(await setMobile(P, [sid]))) { SETUP_M.problems.push(sid + ': brak przełącznika lokalnego Mobile'); continue; }
      if (!(await waitView(P, sid)).ok) { SETUP_M.problems.push(sid + ': iframe widoku nie jest gotowy'); continue; }
      const mid = await P.evaluate(`(() => { const f = ${IFR(sid)}; const d = f && f.contentDocument; if (!d) return null; const e = [...d.querySelectorAll('[data-proto-id]')].find((x) => { const r = x.getBoundingClientRect(); return r.width > 30 && r.height > 12 && x.getClientRects().length; }); return e ? e.getAttribute('data-proto-id') : null; })()`);
      if (!mid) { SETUP_M.problems.push(sid + ': brak elementu z data-proto-id w iframe widoku'); continue; }
      ANCHORS_M[sid] = mid;
      const r = await createMobileThread(ANN, sid, mid, 'N-R wątek mobile ' + sid);
      if (r.status !== 201) SETUP_M.problems.push(sid + ': create view=mobile HTTP ' + r.status);
    }
  } catch (e) { SETUP_M.problems.push('setup mobile: ' + String(e.message || e).slice(0, 160)); }
  return {};
});
suite.teardown(async () => { if (c) await c.closeAll(); if (srv) await srv.stop(); });

const setTheme = async (theme) => {
  for (let i = 0; i < 3; i++) {
    if ((await P.evaluate(`document.documentElement.dataset.theme`)) === theme) return true;
    await P.evaluate(`(() => { const b = [...document.querySelectorAll('.proto-toolbar button')].find((x) => /Motyw/.test(x.textContent + (x.getAttribute('aria-label') || ''))); if (b) b.click(); return 1; })()`); await sleep(250);
  }
  return (await P.evaluate(`document.documentElement.dataset.theme`)) === theme;
};

// ---------------------------------------------------------------- wariant mobile: haki z kontraktu (słownik C, „Widok makiety”)
const IFR = (sid) => `document.querySelector('#${sid} .frame iframe[data-proto-view="mobile"]')`;
const LOCAL_MOBILE = (sid) => `[...(document.querySelector('#${sid} .screen-meta [data-proto-view-switch]')?.querySelectorAll('button') || [])].find((b) => b.textContent.trim() === 'Mobile')`;
async function setMobile(page, sids) {
  for (const sid of sids) { try { await page.domClick(LOCAL_MOBILE(sid), 'Mobile ' + sid); } catch (e) { return false; } }
  return true;
}
// Gotowość iframe widoku: dokument wczytany i wysokość ustalona (różna od placeholdera 667 px i stała przez 2 klatki; 667 po ≥ 1 s), K3.
async function waitView(page, sid, timeout = 8000) {
  const ready = await page.waitFor(`(() => { const f = ${IFR(sid)}; const d = f && f.contentDocument; return !!d && d.readyState === 'complete' && f.getBoundingClientRect().height > 20; })()`, timeout);
  if (!ready.ok) return ready;
  const settle = await page.evaluate(`(async () => { const f = ${IFR(sid)}; const frame = () => new Promise((r) => requestAnimationFrame(() => r()));
    const t0 = performance.now(); let prev = null, same = 0, since = t0;
    while (performance.now() - t0 < 4000) { await frame(); const h = Math.round(f.getBoundingClientRect().height * 10) / 10;
      if (h !== prev) { prev = h; same = 0; since = performance.now(); } else same += 1;
      if (same >= 2 && (h !== 667 || performance.now() - since >= 1000)) return { ok: true, h }; }
    return { ok: false, h: prev }; })()`);
  return { ...ready, settle };
}
async function createMobileThread(s, sid, protoId, text) {
  const ops = await apiOps(s.base, 'plan'); const it = ops.iteration?.current ?? ops.iteration ?? 1;
  const threadId = 'th-' + cid('m').slice(2);
  return apiPost(s, 'plan', { cid: cid('cm'), type: 'create', threadId,
    payload: { screen: sid, anchor: { kind: 'element', protoId, x: 0.5, y: 0.5, iteration: it }, message: { id: 'm-' + threadId.slice(3), text }, view: 'mobile' } });
}
// Zrzut stanu ekranu `sid` w widoku mobile: jak SNAP, ale ramka = iframe widoku, a elementy, kotwica i pinezki z dokumentu iframe
// (prostokąty przeliczone na okno strony, także gdy iframe jest skalowany: skala = szerokość prostokąta / offsetWidth).
const SNAP_M = (sid, anchor) => `(() => {
  const vis = (e) => e.getClientRects().length > 0 && e.getBoundingClientRect().width > 0;
  const R = (e) => { const r = e.getBoundingClientRect(); return { l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height }; };
  const de = document.documentElement; const sc = document.getElementById(${JSON.stringify(sid)}); if (!sc) return { error: 'brak ekranu' };
  const f = sc.querySelector('.frame iframe[data-proto-view="mobile"]'); const d = f && f.contentDocument; if (!f || !d) return { error: 'brak iframe widoku' };
  const fb = f.getBoundingClientRect(); const k = f.offsetWidth ? fb.width / f.offsetWidth : 1; const ox = fb.left + f.clientLeft * k, oy = fb.top + f.clientTop * k;
  const RI = (e) => { const r = e.getBoundingClientRect(); return { l: ox + r.left * k, t: oy + r.top * k, r: ox + r.right * k, b: oy + r.bottom * k, w: r.width * k, h: r.height * k }; };
  const els = [...d.querySelectorAll('[data-proto-id]')].filter(vis).map((e) => ({ id: e.getAttribute('data-proto-id'), ...RI(e) }));
  const anchorEl = d.querySelector('[data-proto-id=' + JSON.stringify(${JSON.stringify(anchor)}) + ']');
  const A = anchorEl ? RI(anchorEl) : null;
  const pins = [...d.querySelectorAll('button.anno-pin:not(.anno-pin-static)')].filter(vis).map((e) => ({ ...RI(e), label: e.getAttribute('aria-label') }));
  let pin = null; if (A) { const ax = (A.l + A.r) / 2, ay = (A.t + A.b) / 2; pin = pins.map((q) => ({ q, d: Math.hypot((q.l + q.r) / 2 - ax, (q.t + q.b) / 2 - ay) })).sort((x, y) => x.d - y.d)[0]; pin = pin ? pin.q : null; }
  const panelEl = document.querySelector('.anno-panel.open'); const panel = panelEl ? R(panelEl) : null;
  const bar = document.querySelector('.doc-toolbar');
  const sel = '.doc-toolbar button, .doc-toolbar a[href], .doc-toolbar input, .doc-toolbar select, .doc-toolbar [role="button"]';
  const controls = [...document.querySelectorAll(sel)].filter(vis).map((e) => { const r = R(e); const cx = Math.min(Math.max((r.l + r.r) / 2, 0), innerWidth - 1), cy = Math.min(Math.max((r.t + r.b) / 2, 0), innerHeight - 1);
    const hit = document.elementFromPoint(cx, cy); const text = ((e.getAttribute('aria-label') || '') + ' ' + e.textContent).trim().replace(/\\s+/g, ' ').slice(0, 30);
    const kind = e.classList.contains('anno-threads-btn') ? 'threads' : e.classList.contains('proto-screens-main') ? 'screens' : /Motyw/.test(text) ? 'theme' : e.matches('[data-anno-focus="user-chip"]') || e.closest('.anno-user') ? 'account' : e.closest('[role="group"][aria-label="Tryb"]') ? 'mode' : 'other';
    return { ...r, kind, text, covered: !!(hit && panelEl && panelEl.contains(hit) && !e.contains(hit)), offscreen: r.l < -1 || r.r > innerWidth + 1, hitIsSelf: !!(hit && (e.contains(hit) || hit.contains(e))), menu: e.hasAttribute('aria-haspopup') }; });
  const closeEl = panelEl ? [...panelEl.querySelectorAll('.anno-panel-head button')].find((b) => /zamknij/i.test((b.getAttribute('aria-label') || '') + (b.title || '') + b.textContent)) : null;
  let close = null; if (closeEl) { const r = R(closeEl); const hit = document.elementFromPoint(Math.min(Math.max((r.l + r.r) / 2, 0), innerWidth - 1), Math.min(Math.max((r.t + r.b) / 2, 0), innerHeight - 1)); close = { ...r, inView: r.l >= -1 && r.r <= innerWidth + 1 && r.t >= -1 && r.b <= innerHeight + 1, hitIsSelf: !!(hit && closeEl.contains(hit)) }; }
  return { iw: innerWidth, ih: innerHeight, doc: { sw: de.scrollWidth, cw: de.clientWidth }, top: sc.getBoundingClientRect().top, frame: { ...R(f), sw: d.documentElement.scrollWidth, cw: d.documentElement.clientWidth },
    els, anchor: A, pin, panel, bar: bar ? R(bar) : null, controls, close, menuControl: !!document.querySelector('.doc-toolbar [aria-haspopup]'), iframeScale: k }; })()`;

// Zrzut stanu ekranu `sid` z prostokątów DOM.
const SNAP = (sid, anchor) => `(() => {
  const vis = (e) => e.getClientRects().length > 0 && e.getBoundingClientRect().width > 0;
  const R = (e) => { const r = e.getBoundingClientRect(); return { l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height }; };
  const de = document.documentElement; const sc = document.getElementById(${JSON.stringify(sid)}); if (!sc) return { error: 'brak ekranu' };
  const fr = sc.querySelector('.frame') || sc;
  const els = [...sc.querySelectorAll('[data-proto-id]')].filter(vis).map((e) => ({ id: e.getAttribute('data-proto-id'), ...R(e) }));
  const anchorEl = sc.querySelector('[data-proto-id=' + JSON.stringify(${JSON.stringify(anchor)}) + ']');
  const A = anchorEl ? R(anchorEl) : null;
  const pins = [...document.querySelectorAll('button.anno-pin:not(.anno-pin-static)')].filter(vis).map((e) => ({ ...R(e), label: e.getAttribute('aria-label') }));
  let pin = null; if (A) { const ax = (A.l + A.r) / 2, ay = (A.t + A.b) / 2; pin = pins.map((q) => ({ q, d: Math.hypot((q.l + q.r) / 2 - ax, (q.t + q.b) / 2 - ay) })).sort((x, y) => x.d - y.d)[0]; pin = pin ? pin.q : null; }
  const panelEl = document.querySelector('.anno-panel.open'); const panel = panelEl ? R(panelEl) : null;
  const bar = document.querySelector('.doc-toolbar');
  const sel = '.doc-toolbar button, .doc-toolbar a[href], .doc-toolbar input, .doc-toolbar select, .doc-toolbar [role="button"]';
  const controls = [...document.querySelectorAll(sel)].filter(vis).map((e) => { const r = R(e); const cx = Math.min(Math.max((r.l + r.r) / 2, 0), innerWidth - 1), cy = Math.min(Math.max((r.t + r.b) / 2, 0), innerHeight - 1);
    const hit = document.elementFromPoint(cx, cy); const text = ((e.getAttribute('aria-label') || '') + ' ' + e.textContent).trim().replace(/\\s+/g, ' ').slice(0, 30);
    const kind = e.classList.contains('anno-threads-btn') ? 'threads' : e.classList.contains('proto-screens-main') ? 'screens' : /Motyw/.test(text) ? 'theme' : e.matches('[data-anno-focus="user-chip"]') || e.closest('.anno-user') ? 'account' : e.closest('[role="group"][aria-label="Tryb"]') ? 'mode' : 'other';
    return { ...r, kind, text, covered: !!(hit && panelEl && panelEl.contains(hit) && !e.contains(hit)), offscreen: r.l < -1 || r.r > innerWidth + 1, hitIsSelf: !!(hit && (e.contains(hit) || hit.contains(e))), menu: e.hasAttribute('aria-haspopup') }; });
  const closeEl = panelEl ? [...panelEl.querySelectorAll('.anno-panel-head button')].find((b) => /zamknij/i.test((b.getAttribute('aria-label') || '') + (b.title || '') + b.textContent)) : null;
  let close = null; if (closeEl) { const r = R(closeEl); const hit = document.elementFromPoint(Math.min(Math.max((r.l + r.r) / 2, 0), innerWidth - 1), Math.min(Math.max((r.t + r.b) / 2, 0), innerHeight - 1)); close = { ...r, inView: r.l >= -1 && r.r <= innerWidth + 1 && r.t >= -1 && r.b <= innerHeight + 1, hitIsSelf: !!(hit && closeEl.contains(hit)) }; }
  return { iw: innerWidth, ih: innerHeight, doc: { sw: de.scrollWidth, cw: de.clientWidth }, top: sc.getBoundingClientRect().top, frame: { ...R(fr), sw: fr.scrollWidth, cw: fr.clientWidth },
    els, anchor: A, pin, panel, bar: bar ? R(bar) : null, controls, close, menuControl: !!document.querySelector('.doc-toolbar [aria-haspopup]') }; })()`;

let DATA = null;
async function collect() {
  if (DATA) return DATA;
  DATA = {};
  for (const w of [...NARROW, ...OVERLAY]) {
    for (const theme of THEMES) {
      await P.size(w, w < 600 ? 812 : 900); await P.go(url());
      const themeOk = await setTheme(theme); await sleep(200);
      for (const sid of SCREENS) {
        await closePanelIfOpen(P);
        await P.evaluate(`document.getElementById(${JSON.stringify(sid)}).scrollIntoView({ block: 'start', behavior: 'instant' }); true`); await sleep(400);
        const closed = await P.evaluate(SNAP(sid, ANCHORS[sid]));
        await openPanel(P); await sleep(900);
        const open = await P.evaluate(SNAP(sid, ANCHORS[sid]));
        await closePanelIfOpen(P); await sleep(900);
        const closed2 = await P.evaluate(SNAP(sid, ANCHORS[sid]));
        (DATA[w] = DATA[w] || {})[theme + '/' + sid] = { themeOk, closed, open, closed2 };
      }
    }
  }
  return DATA;
}

let DATA_M = null;
async function collectMobile() {
  if (DATA_M) return DATA_M;
  DATA_M = {};
  for (const w of [...NARROW, ...OVERLAY]) {
    for (const theme of THEMES) {
      await P.size(w, w < 600 ? 812 : 900); await P.go(url());
      const themeOk = await setTheme(theme); await sleep(200);
      const viewOk = await setMobile(P, SCREENS);
      for (const sid of SCREENS) {
        await closePanelIfOpen(P);
        await P.evaluate(`document.getElementById(${JSON.stringify(sid)}).scrollIntoView({ block: 'start', behavior: 'instant' }); true`);
        const ready = (await waitView(P, sid)).ok; await sleep(400);
        const closed = await P.evaluate(SNAP_M(sid, ANCHORS_M[sid] || null));
        await openPanel(P); await sleep(900);
        const open = await P.evaluate(SNAP_M(sid, ANCHORS_M[sid] || null));
        await closePanelIfOpen(P); await sleep(900);
        const closed2 = await P.evaluate(SNAP_M(sid, ANCHORS_M[sid] || null));
        (DATA_M[w] = DATA_M[w] || {})[theme + '/' + sid] = { themeOk: themeOk && viewOk && ready, closed, open, closed2 };
      }
    }
  }
  return DATA_M;
}

const cases = (widths, data = DATA) => { const out = []; for (const w of widths) for (const key of Object.keys(data[w])) out.push({ w, key, ...data[w][key] }); return out; };
// Wynik scenariusza: wariant desktop (pola jak dotąd) i wariant mobile (pole `mobile`); ok = oba.
const withMobile = (desk, mob) => ({ ...desk, ok: desk.ok && mob.ok, restOk: desk.restOk && mob.restOk, defectObserved: desk.defectObserved || mob.defectObserved,
  mobile: { ...mob, setupProblems: SETUP_M.problems } });
// Ten sam osąd na danych desktop i mobile: judge(all) zwraca { bad, restOk }.
async function judgeBoth(widths, judge) {
  await collect(); const d = cases(widths); const dj = judge(d);
  await collectMobile(); const m = cases(widths, DATA_M); const mj = judge(m);
  return withMobile(evidence(dj.bad, dj.restOk, { cases: d.length }), evidence(mj.bad, mj.restOk && SETUP_M.problems.length === 0, { cases: m.length }));
}
const complete = (x) => x.themeOk && [x.closed, x.open, x.closed2].every((s) => s && !s.error && s.frame && s.frame.w > 0 && s.els.length > 0) && x.open.panel;
const evidence = (bad, restOk, extra = {}) => ({ ok: restOk && bad.length === 0, restOk, defectObserved: restOk && bad.length > 0, violations: bad.length, firstViolations: bad.slice(0, 6), ...extra });
const cluster = (vals, tol = 3) => { const s = [...vals].sort((a, b) => a - b); let n = 0; let last = -1e9; for (const v of s) { if (v - last > tol) n += 1; last = v; } return n; };
const label = (x) => `${x.w}/${x.key}`;

suite.add('N-R1', 'Panel nie przesuwa oglądanego ekranu (zwężenie)', () => judgeBoth(NARROW, (all) => {
  const restOk = all.every(complete); const bad = [];
  for (const x of all.filter(complete)) {
    const d1 = x.open.top - x.closed.top; const d2 = x.closed2.top - x.closed.top;
    if (Math.abs(d1) > 2 || Math.abs(d2) > 2) bad.push({ case: label(x), openShift: Math.round(d1), closeShift: Math.round(d2) });
  }
  return { bad, restOk };
}), { timeout: 900000 });

suite.add('N-R1b', 'Panel przy ostatnim ekranie (s28) i przewinięciu do maksimum', async () => {
  // przypadek brzegowy N-R1: dokument przewinięty do końca, górna krawędź s28 po otwarciu i po zamknięciu panelu (1920 i 1440, oba motywy)
  const bad = []; let restOk = true; let cases = 0; const seen = [];
  for (const w of [1920, 1440]) for (const theme of THEMES) {
    await P.size(w, 900); await P.go(url()); const themeOk = await setTheme(theme); await sleep(200); await closePanelIfOpen(P);
    const toEnd = async () => { await P.evaluate(`window.scrollTo(0, document.documentElement.scrollHeight); true`); await sleep(500); };
    const top = () => P.evaluate(`document.getElementById('s28') ? document.getElementById('s28').getBoundingClientRect().top : null`);
    await toEnd(); const t0 = await top();
    await openPanel(P); await sleep(900); const panelOpen = await P.evaluate(`!!document.querySelector('.anno-panel.open')`); const t1 = await top();
    await closePanelIfOpen(P); await sleep(900); const t2 = await top();
    cases += 1;
    if (!themeOk || t0 === null || t1 === null || t2 === null || !panelOpen) { restOk = false; continue; }
    seen.push(`${w}/${theme}: ${Math.round(t0)} -> ${Math.round(t1)} -> ${Math.round(t2)}`);
    if (Math.abs(t1 - t0) > 2 || Math.abs(t2 - t0) > 2) bad.push({ case: `${w}/${theme}`, topClosed: Math.round(t0), topOpen: Math.round(t1), topAfterClose: Math.round(t2), openShift: Math.round(t1 - t0), closeShift: Math.round(t2 - t0) });
  }
  const desk = evidence(bad, restOk, { cases, tops: seen });
  // wariant mobile: s28 w widoku mobile, dokument przewinięty do końca (te same progi)
  const badM = []; let restOkM = SETUP_M.problems.length === 0; let casesM = 0; const seenM = [];
  for (const w of [1920, 1440]) for (const theme of THEMES) {
    await P.size(w, 900); await P.go(url()); const themeOk = await setTheme(theme); await sleep(200); await closePanelIfOpen(P);
    const viewOk = await setMobile(P, ['s28']);
    const toEnd = async () => { await P.evaluate(`window.scrollTo(0, document.documentElement.scrollHeight); true`); await sleep(500); };
    const top = () => P.evaluate(`document.getElementById('s28') ? document.getElementById('s28').getBoundingClientRect().top : null`);
    await toEnd(); const ready = viewOk && (await waitView(P, 's28')).ok; await toEnd(); const t0 = await top();
    await openPanel(P); await sleep(900); const panelOpen = await P.evaluate(`!!document.querySelector('.anno-panel.open')`); const t1 = await top();
    await closePanelIfOpen(P); await sleep(900); const t2 = await top();
    casesM += 1;
    if (!themeOk || !ready || t0 === null || t1 === null || t2 === null || !panelOpen) { restOkM = false; continue; }
    seenM.push(`${w}/${theme}: ${Math.round(t0)} -> ${Math.round(t1)} -> ${Math.round(t2)}`);
    if (Math.abs(t1 - t0) > 2 || Math.abs(t2 - t0) > 2) badM.push({ case: `${w}/${theme}`, topClosed: Math.round(t0), topOpen: Math.round(t1), topAfterClose: Math.round(t2), openShift: Math.round(t1 - t0), closeShift: Math.round(t2 - t0) });
  }
  return withMobile(desk, evidence(badM, restOkM, { cases: casesM, tops: seenM }));
}, { timeout: 400000 });

suite.add('N-R2', 'Makieta zachowuje układ po zwężeniu', () => judgeBoth(NARROW, (all) => {
  const restOk = all.every(complete); const bad = [];
  for (const x of all.filter(complete)) {
    const k = x.open.frame.w / x.closed.frame.w; const before = new Map(x.closed.els.map((e) => [e.id, e])); const pairs = x.open.els.filter((e) => before.has(e.id)).map((e) => [before.get(e.id), e]);
    if (!pairs.length) { bad.push({ case: label(x), why: 'brak wspólnych elementów' }); continue; }
    const mism = pairs.filter(([b, a]) => Math.abs(a.h / k - b.h) > 2); const hv = mism.length;
    const rows = [cluster(pairs.map(([b]) => b.t - x.closed.frame.t)), cluster(pairs.map(([, a]) => (a.t - x.open.frame.t) / k))];
    const cols = [cluster(pairs.map(([b]) => b.l - x.closed.frame.l)), cluster(pairs.map(([, a]) => (a.l - x.open.frame.l) / k))];
    const outside = [x.closed.els.filter((e) => e.r > x.closed.frame.r + 1).length, x.open.els.filter((e) => e.r > x.open.frame.r + 1).length];
    const newHScroll = x.open.frame.sw > x.open.frame.cw + 1 && !(x.closed.frame.sw > x.closed.frame.cw + 1);
    if (hv || rows[0] !== rows[1] || cols[0] !== cols[1] || outside[1] > outside[0] || newHScroll) bad.push({ case: label(x), scale: Math.round(k * 1000) / 1000, heightMismatch: hv, elements: mism.slice(0, 3).map(([b, a]) => ({ id: b.id, hBefore: Math.round(b.h * 10) / 10, hAfterScaled: Math.round((a.h / k) * 10) / 10, wBefore: Math.round(b.w), wAfterScaled: Math.round(a.w / k) })), rows, cols, outsideRight: outside, newHScroll });
  }
  return { bad, restOk };
}), { timeout: 900000 });

suite.add('N-R3', 'Brak poziomego przewijania strony', () => judgeBoth(NARROW, (all) => {
  const restOk = all.every(complete); const bad = [];
  for (const x of all.filter(complete)) for (const st of ['closed', 'open']) {
    const s = x[st]; if (s.doc.sw > s.doc.cw) bad.push({ case: label(x), state: st, scrollWidth: s.doc.sw, clientWidth: s.doc.cw });
  }
  return { bad, restOk };
}), { timeout: 900000 });

suite.add('N-R4', 'Panel nie zakrywa kontrolek paska', () => judgeBoth(NARROW, (all) => {
  const need = ['threads', 'screens', 'theme', 'account', 'mode'];
  const restOk = all.every(complete) && all.every((x) => need.every((k) => x.closed.controls.some((q) => q.kind === k)) && x.closed.bar);
  const bad = [];
  for (const x of all.filter(complete)) {
    if (!x.closed.bar || !x.open.bar) continue;
    const covered = x.open.controls.filter((q) => q.covered).map((q) => q.kind + ':' + q.text);
    const dh = x.open.bar.h - x.closed.bar.h; const rows = [cluster(x.closed.controls.map((q) => q.t)), cluster(x.open.controls.map((q) => q.t))];
    if (covered.length || Math.abs(dh) > 2 || rows[0] !== rows[1]) bad.push({ case: label(x), covered, barHeightChange: Math.round(dh), controlRows: rows });
  }
  return { bad, restOk };
}), { timeout: 900000 });

suite.add('N-R5', 'Pinezki zostają przy swoich elementach', () => judgeBoth(NARROW, (all) => {
  const restOk = all.every(complete) && all.every((x) => x.closed.pin && x.open.pin && x.closed.anchor && x.open.anchor); const bad = [];
  for (const x of all.filter((y) => complete(y) && y.closed.pin && y.open.pin && y.closed.anchor && y.open.anchor)) {
    const k = x.open.frame.w / x.closed.frame.w; const rel = (s) => ({ dx: (s.pin.l + s.pin.r) / 2 - s.anchor.l, dy: (s.pin.t + s.pin.b) / 2 - s.anchor.t });
    const b = rel(x.closed); const a = rel(x.open); const dx = a.dx / k - b.dx; const dy = a.dy / k - b.dy;
    const pc = { x: (x.open.pin.l + x.open.pin.r) / 2, y: (x.open.pin.t + x.open.pin.b) / 2 }; const ac = { x: (x.open.anchor.l + x.open.anchor.r) / 2, y: (x.open.anchor.t + x.open.anchor.b) / 2 };
    const inPanel = (q) => q.x >= x.open.panel.l && q.x <= x.open.panel.r && q.y >= x.open.panel.t && q.y <= x.open.panel.b;
    const anchorVisible = ac.x >= 0 && ac.x <= x.open.iw && ac.y >= 0 && ac.y <= x.open.ih && !inPanel(ac);
    const under = anchorVisible && inPanel(pc);
    if (Math.abs(dx) > 2 || Math.abs(dy) > 2 || under) bad.push({ case: label(x), scale: Math.round(k * 1000) / 1000, driftX: Math.round(dx * 10) / 10, driftY: Math.round(dy * 10) / 10, underPanel: under, pinLabel: x.open.pin.label, relBefore: [Math.round(b.dx), Math.round(b.dy)], relAfter: [Math.round(a.dx), Math.round(a.dy)], pinSizeBefore: [Math.round(x.closed.pin.w), Math.round(x.closed.pin.h)], pinSizeAfter: [Math.round(x.open.pin.w), Math.round(x.open.pin.h)] });
  }
  return { bad, restOk };
}), { timeout: 900000 });

suite.add('N-R6', 'Płynne zwężenie na 1920, bez skoku poziomego', async () => {
  await P.size(1920, 900); await P.go(url()); await setTheme('light'); await sleep(200);
  await closePanelIfOpen(P);
  await P.evaluate(`document.getElementById('s1').scrollIntoView({ block: 'start', behavior: 'instant' }); true`); await sleep(500);
  const samples = await P.evaluate(`(async () => { const fr = () => { const e = document.getElementById('s1'); const f = e.querySelector('.frame') || e; const r = f.getBoundingClientRect(); return { l: r.left, r: r.right, w: r.width }; };
    const out = [fr()]; const btn = document.querySelector('.anno-threads-btn'); btn.click(); const t0 = performance.now();
    while (performance.now() - t0 < 1800) { await new Promise((res) => requestAnimationFrame(res)); out.push(fr()); }
    const panelEl = document.querySelector('.anno-panel.open'); const pr = panelEl ? panelEl.getBoundingClientRect() : null; return { out, panelW: pr ? pr.width : null, iw: innerWidth }; })()`);
  const s = samples.out; const centers = s.map((q) => (q.l + q.r) / 2); const widths = s.map((q) => q.w);
  const restOk = s.length >= 5 && samples.panelW > 0 && widths[0] > 0;
  const delta = centers[0] - centers[centers.length - 1];
  let reversals = 0; for (let i = 1; i < s.length; i++) { if (centers[i] - centers[i - 1] > 1.5 || widths[i] - widths[i - 1] > 1.5) reversals += 1; }
  const bad = [];
  if (!(delta >= -1.5 && delta <= samples.panelW / 2 + 2)) bad.push({ why: 'skok poziomy większy niż wynika ze zwężenia', centerShift: Math.round(delta), allowed: Math.round(samples.panelW / 2) });
  if (reversals > 0) bad.push({ why: 'zawracanie lub migotanie w trakcie przejścia', reversals });
  await closePanelIfOpen(P);
  const desk = evidence(bad, restOk, { samples: s.length, panelWidth: Math.round(samples.panelW || 0), centerFrom: Math.round(centers[0]), centerTo: Math.round(centers[centers.length - 1]), widthFrom: Math.round(widths[0]), widthTo: Math.round(widths[widths.length - 1]) });
  // wariant mobile: s1 w widoku mobile, próbki prostokąta iframe widoku (ramka ekranu w mobile); te same kryteria
  await P.go(url()); await setTheme('light'); await sleep(200); await closePanelIfOpen(P);
  const viewOk = await setMobile(P, ['s1']);
  await P.evaluate(`document.getElementById('s1').scrollIntoView({ block: 'start', behavior: 'instant' }); true`);
  const ready = viewOk && (await waitView(P, 's1')).ok; await sleep(500);
  const mS = await P.evaluate(`(async () => { const fr = () => { const f = ${IFR('s1')}; if (!f) return { l: 0, r: 0, w: 0 }; const r = f.getBoundingClientRect(); return { l: r.left, r: r.right, w: r.width }; };
    const out = [fr()]; const btn = document.querySelector('.anno-threads-btn'); btn.click(); const t0 = performance.now();
    while (performance.now() - t0 < 1800) { await new Promise((res) => requestAnimationFrame(res)); out.push(fr()); }
    const panelEl = document.querySelector('.anno-panel.open'); const pr = panelEl ? panelEl.getBoundingClientRect() : null; return { out, panelW: pr ? pr.width : null }; })()`);
  const sm = mS.out; const cm = sm.map((q) => (q.l + q.r) / 2); const wm = sm.map((q) => q.w);
  const restOkM = ready && SETUP_M.problems.length === 0 && sm.length >= 5 && mS.panelW > 0 && wm[0] > 0;
  const deltaM = cm[0] - cm[cm.length - 1];
  let revM = 0; for (let i = 1; i < sm.length; i++) { if (cm[i] - cm[i - 1] > 1.5 || wm[i] - wm[i - 1] > 1.5) revM += 1; }
  const badM = [];
  if (!(deltaM >= -1.5 && deltaM <= mS.panelW / 2 + 2)) badM.push({ why: 'skok poziomy większy niż wynika ze zwężenia', centerShift: Math.round(deltaM), allowed: Math.round(mS.panelW / 2) });
  if (revM > 0) badM.push({ why: 'zawracanie lub migotanie w trakcie przejścia', reversals: revM });
  await closePanelIfOpen(P);
  return withMobile(desk, evidence(badM, restOkM, { samples: sm.length, panelWidth: Math.round(mS.panelW || 0), centerFrom: Math.round(cm[0]), centerTo: Math.round(cm[cm.length - 1]), widthFrom: Math.round(wm[0]), widthTo: Math.round(wm[wm.length - 1]) }));
}, { timeout: 200000 });

suite.add('N-R7', 'Nakładka poniżej 1024 px: bez przeliczania', () => judgeBoth(OVERLAY, (all) => {
  const restOk = all.every(complete); const bad = [];
  for (const x of all.filter(complete)) {
    const f0 = x.closed.frame; const f1 = x.open.frame;
    const moved = ['l', 't', 'w', 'h'].some((k) => Math.abs(f1[k] - f0[k]) > 1.5) || Math.abs(x.open.top - x.closed.top) > 2 || x.open.doc.sw !== x.closed.doc.sw;
    const overlays = x.open.panel.l < f1.r - 1 && x.open.panel.r > f1.l + 1;
    if (moved || !overlays) bad.push({ case: label(x), frameBefore: [f0.l, f0.t, f0.w, f0.h].map(Math.round), frameAfter: [f1.l, f1.t, f1.w, f1.h].map(Math.round), overlapsContent: overlays });
  }
  return { bad, restOk };
}), { timeout: 900000 });

suite.add('N-R8', 'Nakładka: przełącznik i zamknięcie dostępne', () => judgeBoth(OVERLAY, (all) => {
  const restOk = all.every((x) => complete(x) && x.open.close); const bad = [];
  for (const x of all.filter((y) => complete(y) && y.open.close)) {
    const sw = x.open.controls.filter((q) => q.kind === 'screens' || q.kind === 'threads');
    const covered = sw.filter((q) => q.covered).map((q) => q.kind);
    const closeOk = x.open.close.inView && x.open.close.hitIsSelf;
    const off = x.closed.controls.filter((q) => q.offscreen).map((q) => q.kind + ':' + q.text);
    const fits = x.w !== 375 || off.length === 0 || x.closed.menuControl;   // wymóg dotyczy 375: kontrolki mieszczą się w oknie albo jest menu
    if (covered.length || !closeOk || !fits) bad.push({ case: label(x), covered, closeVisible: closeOk, controlsOutsideWindow: off.slice(0, 5), menuAvailable: x.closed.menuControl });
  }
  return { bad, restOk };
}), { timeout: 900000 });

suite.add('N-S1', 'Powłoka aplikacji wypełnia ramkę ekranu', async () => {
  // prototyp `shell`: ekran z paskiem bocznym (nawigacja), okruszkami, nagłówkiem i tabelą/formularzem; panel zamknięty
  const SHELL = `${srv.base}/shell/`; const bad = []; const sidebarWidths = {}; let restOk = true; let cases = 0;
  for (const w of [1440, 1024, 375]) for (const theme of THEMES) {
    await P.size(w, w < 600 ? 812 : 900); await P.go(SHELL); const themeOk = await setTheme(theme); await sleep(200);
    for (const sid of ['s1', 's2']) {
      await P.evaluate(`document.getElementById(${JSON.stringify(sid)}).scrollIntoView({ block: 'start', behavior: 'instant' }); true`); await sleep(300);
      const m = await P.evaluate(`(() => { const sc = document.getElementById(${JSON.stringify(sid)}); const R = (e) => { const r = e.getBoundingClientRect(); return { l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height }; };
        const fr = sc.querySelector('.frame'); const app = sc.querySelector('.app'); const side = sc.querySelector('.sidebar'); const main = sc.querySelector('.main'); const h1 = sc.querySelector('h1');
        if (!fr || !app || !side || !main || !h1) return { missing: true };
        const frl = fr.getBoundingClientRect().left + fr.clientLeft; const frr = frl + fr.clientWidth; const de = document.documentElement;
        const clipped = (e) => { for (let a = e.parentElement; a && a !== app; a = a.parentElement) { const o = getComputedStyle(a).overflowX; if (o !== 'visible' && a.getBoundingClientRect().right <= frr + 1) return true; } return false; };   // kontener z przewijaniem/ucięciem (np. .scroll-x) jest w ramce
        const outside = [...app.querySelectorAll('*')].filter((e) => e.getClientRects().length && e.getBoundingClientRect().width > 0 && e.getBoundingClientRect().right > frr + 1 && !clipped(e)).length;
        const tabbableIn = (root) => { const cand = [...root.querySelectorAll('a[href], button, input, select, textarea, summary, [tabindex], [contenteditable]')].concat(root.hasAttribute('tabindex') ? [root] : []);
          const n = cand.filter((e) => e.getAttribute('tabindex') !== '-1' && !e.disabled && (e.focus(), document.activeElement === e)).length; if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); return n; };   // osiągalny = daje się sfokusować (display none, visibility hidden, inert odpadają)
        const lh = parseFloat(getComputedStyle(h1).lineHeight) || parseFloat(getComputedStyle(h1).fontSize) * 1.2;
        return { frame: { l: frl, r: frr, w: fr.clientWidth }, app: R(app), side: { ...R(side), shown: side.getClientRects().length > 0, display: getComputedStyle(side).display, tabbable: tabbableIn(side) }, main: R(main), h1: { h: h1.getBoundingClientRect().height, lines: Math.round(h1.getBoundingClientRect().height / lh) }, outside, hs: de.scrollWidth > de.clientWidth }; })()`);
      cases += 1;
      if (m.missing || !themeOk) { restOk = false; continue; }
      const issues = [];
      if (Math.abs(m.app.l - m.frame.l) > 2 || Math.abs(m.app.r - m.frame.r) > 2) issues.push('powłoka nie wypełnia ramki (' + Math.round(m.app.l - m.frame.l) + ', ' + Math.round(m.frame.r - m.app.r) + ')');
      if (m.outside > 0) issues.push('elementy poza ramką: ' + m.outside);
      if (m.h1.lines > 2) issues.push('nagłówek ma ' + m.h1.lines + ' linii');
      if (m.hs) issues.push('poziome przewijanie strony');
      const sideBySide = m.side.shown && m.side.r <= m.main.l + 2 && Math.abs(m.side.t - m.main.t) < 6;
      if (w >= 1024) {
        if (!sideBySide) issues.push('pasek boczny nie jest obok głównej części');
        else {
          if (Math.abs(m.side.l - m.app.l) > 2) issues.push('pusty pas przed powłoką: ' + Math.round(m.side.l - m.app.l));
          if (Math.abs(m.main.r - m.app.r) > 2 || Math.abs(m.main.w - (m.app.w - m.side.w)) > 2) issues.push('główna część nie zajmuje reszty (' + Math.round(m.main.w) + ' z ' + Math.round(m.app.w - m.side.w) + ')');
        }
        (sidebarWidths[theme + '/' + sid] = sidebarWidths[theme + '/' + sid] || []).push(Math.round(m.side.w));
      } else {
        // poniżej 600 px: pasek boczny ukryty (display none albo zerowa szerokość), bez szyny; żaden potomek nie jest osiągalny z klawiatury
        const hiddenSide = m.side.display === 'none' || !m.side.shown || m.side.w < 1;
        if (!hiddenSide) issues.push('na 375 pasek boczny jest widoczny (szyna lub pionowo): ' + Math.round(m.side.w) + ' px');
        if (m.side.tabbable > 0) issues.push('na 375 w pasku bocznym jest ' + m.side.tabbable + ' elementów w kolejności Tab');
        if (Math.abs(m.main.l - m.frame.l) > 2 || Math.abs(m.main.r - m.frame.r) > 2) issues.push('główna część nie wypełnia ramki (' + Math.round(m.main.l - m.frame.l) + ', ' + Math.round(m.frame.r - m.main.r) + ')');
      }
      if (issues.length) bad.push({ case: `${w}/${theme}/${sid}`, issues, side: Math.round(m.side.w), main: Math.round(m.main.w), app: Math.round(m.app.w) });
    }
  }
  for (const [k, v] of Object.entries(sidebarWidths)) if (Math.max(...v) - Math.min(...v) > 2) bad.push({ case: k, issues: ['szerokość paska bocznego zmienna: ' + v.join(', ')] });
  return evidence(bad, restOk, { cases });
}, { timeout: 200000 });

// ---------------------------------------------------------------- N-R9, N-R10: pasek prototypu na telefonie i menu „⋯” (D194)
// Pomiar tylko przez DOM, role i atrybuty z kontraktu: `.doc-toolbar`, `[aria-haspopup="menu"]` z nazwą „Więcej”, `role="menu"`, `menuitem`/`menuitemcheckbox`,
// `aria-expanded`, `data-theme`, `--proto-toolbar-height`, `button.anno-pin`. Teksty przycisków (Motyw, Prezentacja, hotspoty, pinezki) rozpoznawane po nazwie dostępnej.
const LABELS = { hotspots: /hotspot/i, present: /Prezentacja/i, pins: /pinezk/i, theme: /Motyw/i };
const LABELS_SRC = JSON.stringify(Object.fromEntries(Object.entries(LABELS).map(([k, rx]) => [k, { src: rx.source, flags: rx.flags }])));
const BAR_PROBE = `(() => {
  const iw = innerWidth; const de = document.documentElement;
  const vis = (e) => { if (!e.getClientRects().length) return false; const r = e.getBoundingClientRect(); if (r.width <= 0 || r.height <= 0) return false; const s = getComputedStyle(e); return s.visibility !== 'hidden' && s.display !== 'none'; };
  const name = (e) => ((e.getAttribute('aria-label') || '') + ' ' + e.textContent).replace(/\\s+/g, ' ').trim();
  const bar = document.querySelector('.doc-toolbar'); if (!bar) return { noBar: true };
  const inWin = (e) => { const r = e.getBoundingClientRect(); return r.left >= -1 && r.right <= iw + 1; };
  const ctrls = [...bar.querySelectorAll('button, a[href], input, select, [role="button"]')].filter((e) => !e.closest('[role="menu"]') && vis(e) && inWin(e));
  const br = bar.getBoundingClientRect(); const maxH = Math.max(0, ...ctrls.map((e) => e.getBoundingClientRect().height));
  const small = ctrls.filter((e) => e.matches('button, select, a, [role="button"]')).map((e) => { const r = e.getBoundingClientRect(); return { n: name(e).slice(0, 30) || e.tagName, w: Math.round(r.width * 10) / 10, h: Math.round(r.height * 10) / 10 }; }).filter((q) => q.w < 24 || q.h < 24);
  const switcher = [...bar.querySelectorAll('[data-screen-step], .proto-screens-main')].filter(vis).map((e) => ({ n: name(e).slice(0, 30) || e.tagName, h: Math.round(e.getBoundingClientRect().height * 10) / 10 })).filter((q) => q.h < 44);
  const shown = {}; for (const [k, rx] of Object.entries(${LABELS_SRC})) shown[k] = ctrls.some((e) => new RegExp(rx.src, rx.flags).test(name(e)));
  const more = [...bar.querySelectorAll('[aria-haspopup="menu"]')].find((e) => /Więcej/.test(e.getAttribute('aria-label') || e.textContent));   // tylko po nazwie dostępnej; inne menu (chip konta) pomijamy
  let moreInfo = null; if (more) { const nm = more.getAttribute('aria-label') || more.textContent.trim(); const v = vis(more); let focusable = false;
    if (!v) { more.focus(); focusable = document.activeElement === more; if (focusable) more.blur(); } moreInfo = { name: nm, visible: v, inWin: v && inWin(more), expanded: more.getAttribute('aria-expanded'), focusableWhenHidden: focusable, tabindex: more.getAttribute('tabindex') }; }
  const holder = [de, document.body, bar].find((e) => getComputedStyle(e).getPropertyValue('--proto-toolbar-height').trim() !== '');
  let cssVar = null; if (holder) { const t = document.createElement('div'); t.style.cssText = 'position:absolute;visibility:hidden;width:1px;height:var(--proto-toolbar-height)'; holder.appendChild(t); cssVar = t.getBoundingClientRect().height; t.remove(); }
  return { iw, bar: { w: br.width, h: br.height }, maxH, small, switcher, shown, more: moreInfo, cssVar, hs: de.scrollWidth > de.clientWidth, ctrlCount: ctrls.length, loggedIn: !!document.querySelector('.anno-user') }; })()`;
const barProbe = (page) => page.evaluate(BAR_PROBE);

suite.add('N-R9', 'Pasek prototypu na telefonie', async () => {
  const bad = []; let restOk = true; let cases = 0; const seen = [];
  const anon = await c.page('nr-anon');
  try {
    for (const [who, page] of [['zalogowany', P], ['niezalogowany', anon]]) {
      for (const theme of THEMES) for (const w of [375, 599, 600, 768]) {
        await page.size(1440, 900); await page.go(url());
        if (!(await setThemeOn(page, theme))) { restOk = false; continue; }
        await page.size(w, 812); await sleep(500);
        const m = await barProbe(page); cases += 1; const tag = `${who}/${theme}/${w}`;
        if (m.noBar || !m.ctrlCount || m.loggedIn !== (who === 'zalogowany')) { restOk = false; bad.push({ case: tag, why: 'brak danych: pasek, kontrolki albo stan konta niezgodny z przypadkiem', loggedIn: m.loggedIn }); continue; }
        const issues = [];
        if (m.hs) issues.push('poziome przewijanie strony');
        if (m.cssVar === null) issues.push('brak --proto-toolbar-height'); else if (Math.abs(m.cssVar - m.bar.h) > 1) issues.push(`--proto-toolbar-height ${Math.round(m.cssVar)} px ≠ wysokość paska ${Math.round(m.bar.h)} px`);
        if (w < 600) {
          if (m.bar.h > 2 * m.maxH + 32) issues.push(`pasek ${Math.round(m.bar.h)} px > 2 × ${Math.round(m.maxH)} + 32`);
          for (const q of m.small) issues.push(`kontrolka „${q.n}” ${q.w} x ${q.h} px < 24 x 24`);
          for (const q of m.switcher) issues.push(`przełącznik ekranów „${q.n}” ${q.h} px < 44 px wysokości`);
          const inBar = Object.keys(LABELS).filter((k) => m.shown[k]); if (inBar.length) issues.push('w pasku zostały: ' + inBar.join(', '));
          if (!m.more) issues.push('brak przycisku menu z nazwą „Więcej” ([aria-haspopup="menu"]) w pasku');
          else {
            if (!/Więcej/.test(m.more.name)) issues.push('nazwa przycisku menu: „' + m.more.name + '”');
            if (!m.more.visible || !m.more.inWin) issues.push('przycisk „Więcej” niewidoczny albo poza oknem');
            if (m.more.expanded !== 'false') issues.push('aria-expanded zamkniętego menu = ' + m.more.expanded);
          }
        } else {
          const missing = Object.keys(LABELS).filter((k) => !m.shown[k]); if (missing.length) issues.push('brak w pasku: ' + missing.join(', '));
          if (m.more && (m.more.visible || m.more.focusableWhenHidden)) issues.push('przycisk „Więcej” widoczny albo w kolejności Tab' + (m.more.visible ? ' (widoczny)' : ' (fokusowalny)'));
        }
        seen.push(`${tag}: pasek ${Math.round(m.bar.h)}/${Math.round(m.maxH)}`);
        if (issues.length) bad.push({ case: tag, issues, barH: Math.round(m.bar.h), maxControlH: Math.round(m.maxH) });
      }
    }
  } finally { await anon.dispose().catch(() => {}); await P.size(1440, 900); }
  return evidence(bad, restOk && cases === 16, { cases, bars: seen });
}, { timeout: 300000 });

// setTheme działający na dowolnej karcie (przycisk „Motyw” w pasku szerokim)
async function setThemeOn(page, theme) {
  for (let i = 0; i < 3; i++) {
    if ((await page.evaluate(`document.documentElement.dataset.theme`)) === theme) return true;
    await page.evaluate(`(() => { const b = [...document.querySelectorAll('.doc-toolbar button')].find((x) => !x.closest('[role="menu"]') && /Motyw/.test(x.textContent + (x.getAttribute('aria-label') || ''))); if (b) b.click(); return 1; })()`); await sleep(250);
  }
  return (await page.evaluate(`document.documentElement.dataset.theme`)) === theme;
}

const MORE_EL = `[...document.querySelectorAll('.doc-toolbar [aria-haspopup="menu"]')].find((e) => /Więcej/.test(e.getAttribute('aria-label') || e.textContent))`;
const visFn = `const vis = (e) => { if (!e.getClientRects().length) return false; const r = e.getBoundingClientRect(); if (r.width <= 0 || r.height <= 0) return false; const s = getComputedStyle(e); return s.visibility !== 'hidden' && s.display !== 'none'; };`;
const menuState = () => P.evaluate(`(() => { ${visFn}
  const more = ${MORE_EL}; const m = [...document.querySelectorAll('[role="menu"]')].find(vis); const r = m ? m.getBoundingClientRect() : null;
  const items = m ? [...m.querySelectorAll('[role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"]')].filter(vis) : []; const act = document.activeElement;
  return { has: !!more, expanded: more ? more.getAttribute('aria-expanded') : null, open: !!m, inWin: !!r && r.left >= -1 && r.top >= -1 && r.right <= innerWidth + 1 && r.bottom <= innerHeight + 1,
    n: items.length, idx: items.indexOf(act), texts: items.map((e) => (e.getAttribute('aria-label') || e.textContent).replace(/\\s+/g, ' ').trim()), onMore: !!more && act === more, theme: document.documentElement.dataset.theme,
    rect: r ? { l: r.left, t: r.top, r: r.right, b: r.bottom } : null, moreRect: more ? (() => { const q = more.getBoundingClientRect(); return { l: q.left, t: q.top, r: q.right, b: q.bottom }; })() : null }; })()`);
const pinCount = () => P.evaluate(`(() => { ${visFn} return [...document.querySelectorAll('button.anno-pin:not(.anno-pin-static)')].filter(vis).length; })()`);
const focusMore = () => P.evaluate(`(() => { const m = ${MORE_EL}; if (!m) return false; m.focus(); return document.activeElement === m; })()`);
// stan strony widoczny w DOM: klasy i data-* na html, body, pasku i ekranach (bez data-theme); do porównania efektu „Prezentacja” z menu i z przycisku
const STATE_SIG = `(() => { const t = new Set(); const add = (n, e) => { for (const c of e.classList) t.add(n + '.' + c); for (const a of e.attributes) if (a.name.startsWith('data-') && a.name !== 'data-theme') t.add(n + '[' + a.name + ']=' + a.value); };
  add('html', document.documentElement); add('body', document.body); const bar = document.querySelector('.doc-toolbar'); if (bar) add('bar', bar);
  const cls = {}; for (const e of document.querySelectorAll('section.screen')) for (const c of e.classList) cls[c] = (cls[c] || 0) + 1; for (const [k, v] of Object.entries(cls)) t.add('screens.' + k + '=' + v);
  return [...t].sort(); })()`;
const symdiff = (a, b) => a.filter((x) => !b.includes(x)).map((x) => '+' + x).concat(b.filter((x) => !a.includes(x)).map((x) => '-' + x));

suite.add('N-R10', 'Menu „⋯” z klawiatury i myszy', async () => {
  const bad = []; let restOk = true; const e0 = errors.length; const log = [];
  const fail = (tag, msg) => bad.push({ case: tag, issue: msg });
  const openByEnter = async () => { await focusMore(); await P.key('Enter'); await sleep(250); return menuState(); };
  const gotoItem = async (rx) => { let s = await menuState(); for (let i = 0; i <= s.n && s.open; i++) { if (s.idx >= 0 && rx.test(s.texts[s.idx])) return s; await P.key('ArrowDown'); s = await menuState(); } return s; };
  for (const theme of THEMES) {
    const tag = `375/${theme}`;
    await P.size(1440, 900); await P.go(url());
    if (!(await setThemeOn(P, theme))) { restOk = false; continue; }
    await P.size(375, 812); await sleep(500);
    let s = await menuState();
    if (!s.has) { fail(tag, 'brak przycisku menu z nazwą „Więcej” w .doc-toolbar'); continue; }
    if (!(await focusMore())) { fail(tag, 'przycisk „Więcej” nie przyjmuje fokusu'); continue; }
    // otwarcie Enterem
    await P.key('Enter'); await sleep(250); s = await menuState();
    if (s.expanded !== 'true') fail(tag, 'Enter: aria-expanded = ' + s.expanded);
    if (!s.open) { fail(tag, 'Enter: nie ma widocznego role="menu"'); continue; }
    if (!s.inWin) fail(tag, 'menu wychodzi poza okno ' + JSON.stringify(s.rect));
    if (s.idx !== 0) fail(tag, 'po otwarciu fokus na pozycji ' + s.idx + ' zamiast 0');
    const n = s.n; log.push(`${tag}: ${n} pozycji: ${s.texts.join(' | ')}`);
    if (n < 4 || !['hotspot', 'Prezentacja', 'pinezk', 'Motyw'].every((k) => s.texts.some((t) => new RegExp(k, 'i').test(t)))) { fail(tag, 'pozycje menu: ' + s.texts.join(' | ')); continue; }
    // strzałki w kółko, Home, End
    const seq = []; for (let i = 0; i < n; i++) { await P.key('ArrowDown'); seq.push((await menuState()).idx); }
    const wantDown = Array.from({ length: n }, (_, i) => (i + 1) % n); if (JSON.stringify(seq) !== JSON.stringify(wantDown)) fail(tag, 'ArrowDown po kolei: ' + seq.join(',') + ' oczekiwano ' + wantDown.join(','));
    await P.key('ArrowUp'); const up = (await menuState()).idx; if (up !== n - 1) fail(tag, 'ArrowUp z pierwszej pozycji: ' + up + ' oczekiwano ' + (n - 1));
    await P.key('ArrowUp'); const up2 = (await menuState()).idx; if (up2 !== n - 2) fail(tag, 'ArrowUp: ' + up2 + ' oczekiwano ' + (n - 2));
    await P.key('Home'); const hm = (await menuState()).idx; if (hm !== 0) fail(tag, 'Home: ' + hm);
    await P.key('End'); const en = (await menuState()).idx; if (en !== n - 1) fail(tag, 'End: ' + en);
    // Esc zamyka i oddaje fokus
    await P.key('Escape'); await sleep(250); s = await menuState();
    if (s.open || s.expanded !== 'false') fail(tag, 'Esc nie zamknął menu (open=' + s.open + ', aria-expanded=' + s.expanded + ')');
    if (!s.onMore) fail(tag, 'Esc nie oddał fokusu na „Więcej”');
    // Spacja otwiera ponownie
    await P.key(' '); await sleep(250); s = await menuState();
    if (s.expanded !== 'true' || !s.open) fail(tag, 'Spacja nie otworzyła menu (aria-expanded=' + s.expanded + ', open=' + s.open + ')'); else if (s.idx !== 0) fail(tag, 'Spacja: fokus na pozycji ' + s.idx);
    // Motyw Enterem
    s = await gotoItem(LABELS.theme); const before = s.theme;
    if (!(s.idx >= 0 && LABELS.theme.test(s.texts[s.idx]))) fail(tag, 'nie udało się ustawić fokusu na pozycji „Motyw”');
    await P.key('Enter'); await sleep(300); s = await menuState();
    if (s.theme === before) fail(tag, 'Motyw Enterem nie zmienił data-theme (' + before + ')');
    if (s.open || s.expanded !== 'false') fail(tag, 'po wyborze „Motyw” menu zostało otwarte');
    // „Ukryj pinezki” i odwrotnie
    await P.evaluate(`window.scrollTo(0, 0); true`);
    const pins0 = await pinCount();
    if (pins0 < 1) { restOk = false; fail(tag, 'brak widocznych pinezek do sprawdzenia przełącznika'); }
    else {
      await openByEnter(); await gotoItem(LABELS.pins); await P.key('Enter'); await sleep(300);
      const pinsHidden = await pinCount(); s = await menuState();
      if (pinsHidden !== 0) fail(tag, 'po „Ukryj pinezki” widocznych pinezek: ' + pinsHidden + ' z ' + pins0);
      if (s.open) fail(tag, 'menu nie zamknęło się po „Ukryj pinezki”');
      await openByEnter(); await gotoItem(LABELS.pins); await P.key('Enter'); await sleep(300);
      const pinsBack = await pinCount(); if (pinsBack !== pins0) fail(tag, 'po ponownym wyborze pinezek: ' + pinsBack + ' zamiast ' + pins0);
    }
    // (a) Tab i Shift+Tab przy otwartym menu zamykają je, a fokus przechodzi dalej (nie zostaje w menu ani na body)
    for (const shift of [false, true]) {
      const nm = shift ? 'Shift+Tab' : 'Tab'; s = await openByEnter();
      if (!s.open) { fail(tag, nm + ': nie udało się otworzyć menu'); continue; }
      await P.key('Tab', shift ? { shift: true } : {}); await sleep(200); s = await menuState();
      const where = await P.evaluate(`(() => { const a = document.activeElement; return { body: !a || a === document.body || a === document.documentElement, inMenu: !!a && !!a.closest('[role="menu"]') }; })()`);
      if (s.open || s.expanded !== 'false') fail(tag, nm + ' nie zamknął menu (open=' + s.open + ', aria-expanded=' + s.expanded + ')');
      if (where.body || where.inMenu) fail(tag, nm + ': fokus nie przeszedł dalej (' + (where.body ? 'body' : 'w menu') + ')');
    }
    // (b) stan pozycji „Ukryj pinezki” w menu: po użyciu zmienia się (aria-checked na menuitemcheckbox albo tekst), drugie użycie przywraca
    const pinItem = async () => { await openByEnter(); const q = await P.evaluate(`(() => { const m = [...document.querySelectorAll('[role="menu"]')].find((e) => e.getClientRects().length); if (!m) return null;
      const it = [...m.querySelectorAll('[role="menuitem"], [role="menuitemcheckbox"]')].find((e) => /pinezk/i.test(e.getAttribute('aria-label') || e.textContent)); return it ? { role: it.getAttribute('role'), checked: it.getAttribute('aria-checked'), text: it.textContent.replace(/\\s+/g, ' ').trim() } : null; })()`);
      return q; };
    const usePins = async () => { await gotoItem(LABELS.pins); await P.key('Enter'); await sleep(300); };
    if (pins0 >= 1) {
      const p0 = await pinItem(); await usePins(); const p1 = await pinItem(); await usePins(); const p2 = await pinItem();
      if (!p0 || !p1 || !p2) fail(tag, 'brak pozycji pinezek w menu przy sprawdzaniu stanu');
      else {
        const key = (q) => (q.role === 'menuitemcheckbox' ? 'checked=' + q.checked : 'text=' + q.text);
        if (p0.role === 'menuitemcheckbox' && p0.checked !== 'true' && p0.checked !== 'false') fail(tag, 'menuitemcheckbox bez aria-checked true/false: ' + p0.checked);
        if (key(p0) === key(p1)) fail(tag, 'stan pozycji pinezek nie zmienił się po użyciu (' + key(p0) + ')');
        if (key(p0) !== key(p2)) fail(tag, 'drugie użycie nie przywróciło stanu pozycji (' + key(p0) + ' -> ' + key(p2) + ')');
      }
      await P.key('Escape'); await sleep(200);
    }
    // (c) przy otwartym menu skróty jednoklawiszowe nie działają: `c` nie zmienia aria-pressed „Komentarze”, ArrowRight nie zmienia bieżącego ekranu
    s = await openByEnter();
    if (!s.open) fail(tag, 'nie udało się otworzyć menu przed sprawdzeniem skrótów');
    else {
      const snap = () => P.evaluate(`(() => { const b = ${commentBtn}; const cur = document.querySelector('section.screen.is-current'); return { pressed: b ? b.getAttribute('aria-pressed') : 'brak', cur: cur ? cur.id : null, curAria: cur ? cur.getAttribute('aria-current') : null,
        navCur: (document.querySelector('.screen-nav [aria-current="location"]') || {}).textContent || null }; })()`);
      const k0 = await snap(); await P.key('c'); const k1 = await snap(); await P.key('ArrowRight'); const k2 = await snap();
      if (k0.pressed === 'brak') { restOk = false; fail(tag, 'brak przycisku „Komentarze”'); }
      else if (k1.pressed !== k0.pressed || k2.pressed !== k0.pressed) fail(tag, 'skrót c zmienił aria-pressed „Komentarze”: ' + k0.pressed + ' -> ' + k2.pressed);
      if (JSON.stringify(k0) !== JSON.stringify({ ...k2, pressed: k0.pressed })) fail(tag, 'ArrowRight przy otwartym menu zmienił ekran bieżący: ' + JSON.stringify(k0) + ' -> ' + JSON.stringify(k2));
      await P.key('Escape'); await sleep(200);
    }
    // klik poza menu zamyka
    s = await openByEnter();
    if (!s.open) fail(tag, 'nie udało się otworzyć menu przed klikiem poza nim');
    else {
      const pt = await P.evaluate(`(() => { const m = [...document.querySelectorAll('[role="menu"]')].find((e) => e.getClientRects().length); const more = ${MORE_EL};
        for (const [x, y] of [[8, innerHeight - 8], [innerWidth - 8, innerHeight - 8], [innerWidth / 2, innerHeight - 8], [8, innerHeight / 2]]) { const h = document.elementFromPoint(x, y); if (h && !m.contains(h) && !more.contains(h) && !h.closest('a[href], button, input, select, textarea, [role="button"]')) return { x, y }; }
        return null; })()`);
      if (!pt) { restOk = false; fail(tag, 'brak punktu poza menu do kliknięcia'); }
      else { await P.click(pt.x, pt.y); await sleep(250); s = await menuState(); if (s.open || s.expanded !== 'false') fail(tag, 'klik poza menu go nie zamknął'); }
    }
    // menu konta (chip `.anno-user`, przycisk z aria-haspopup) mieści się w oknie przy 375
    const acct = await P.evaluate(`(async () => { const b = document.querySelector('.anno-user [aria-haspopup], .anno-user[aria-haspopup]'); if (!b) return { noChip: true }; b.click(); await new Promise((r) => setTimeout(r, 300));
      const m = [...document.querySelectorAll('[role="menu"]')].find((e) => e !== document.querySelector('x') && e.getClientRects().length && e.getBoundingClientRect().width > 0 && !e.contains(${MORE_EL} || document.body)); const r = m ? m.getBoundingClientRect() : null;
      return { open: !!m, l: r && r.left, r: r && r.right, iw: innerWidth }; })()`);
    if (acct.noChip) { restOk = false; fail(tag, 'brak chipu konta (.anno-user z aria-haspopup)'); }
    else if (!acct.open) fail(tag, 'klik chipu konta nie otworzył menu');
    else if (acct.l < -1 || acct.r > acct.iw + 1) fail(tag, `menu konta poza oknem: left ${Math.round(acct.l)}, right ${Math.round(acct.r)}, okno ${acct.iw}`);
    await P.key('Escape'); await sleep(250);
    // zmiana szerokości na 768 przy otwartym menu
    s = await openByEnter();
    if (!s.open) fail(tag, 'nie udało się otworzyć menu przed zmianą szerokości');
    await P.size(768, 900); await sleep(600);
    const wide = await menuState(); const bar = await barProbe(P);
    if (wide.open || wide.expanded === 'true') fail(tag, 'po zmianie na 768 menu zostało otwarte');
    const missing = Object.keys(LABELS).filter((k) => !bar.shown[k]); if (missing.length) fail(tag, 'po zmianie na 768 brak w pasku: ' + missing.join(', '));
  }
  // „Prezentacja” z menu daje ten sam efekt co przycisk paska (porównanie zmian stanu DOM względem stanu wyjściowego)
  const effect = async (width, via) => {
    await P.size(1440, 900); await P.go(url()); await P.size(width, 812); await sleep(500);
    const s0 = await P.evaluate(STATE_SIG);
    if (via === 'menu') { await openByEnter(); const s = await gotoItem(LABELS.present); if (!(s.idx >= 0 && LABELS.present.test(s.texts[s.idx]))) return null; await P.key('Enter'); }
    else await P.domClick(`[...document.querySelectorAll('.doc-toolbar button')].find((b) => !b.closest('[role="menu"]') && b.getClientRects().length && /Prezentacja/i.test(b.textContent + (b.getAttribute('aria-label') || '')))`, 'przycisk Prezentacja');
    await sleep(500); return symdiff(s0, await P.evaluate(STATE_SIG));
  };
  const viaMenu = await effect(375, 'menu'); const viaButton = await effect(768, 'button');
  if (!viaMenu || !viaButton) { restOk = false; fail('prezentacja', 'nie udało się wykonać „Prezentacja” (menu=' + !!viaMenu + ', przycisk=' + !!viaButton + ')'); }
  else if (!viaButton.length) { restOk = false; fail('prezentacja', 'przycisk „Prezentacja” nie zmienia obserwowalnego stanu DOM (html/body/pasek/ekrany), porównanie niemożliwe'); }
  else if (JSON.stringify(viaMenu) !== JSON.stringify(viaButton)) fail('prezentacja', 'efekt z menu ' + JSON.stringify(viaMenu).slice(0, 200) + ' ≠ efekt przycisku ' + JSON.stringify(viaButton).slice(0, 200));
  await P.size(1440, 900); await P.go(url());
  const errs = pageErrors(e0); if (errs.length) fail('konsola', errs.slice(0, 3).map((x) => x.kind + ': ' + x.text).join(' || '));
  const desk = evidence(bad, restOk, { log });
  // wariant mobile: s1 w widoku mobile; „Ukryj pinezki” z menu „Więcej” ukrywa pinezki w iframe widoku i ponowny wybór je przywraca.
  // Pozostałe kroki N-R10 dotyczą samego paska i menu, niezależnych od widoku ekranu (części desktop nie powtarzamy).
  const badM = []; let restOkM = SETUP_M.problems.length === 0; const e1 = errors.length; const logM = [];
  const framePins = () => P.evaluate(`(() => { const f = ${IFR('s1')}; const d = f && f.contentDocument; if (!d) return -1;
    return [...d.querySelectorAll('button.anno-pin:not(.anno-pin-static)')].filter((e) => { if (!e.getClientRects().length) return false; const r = e.getBoundingClientRect(); if (r.width <= 0 || r.height <= 0) return false;
      const s = e.ownerDocument.defaultView.getComputedStyle(e); return s.visibility !== 'hidden' && s.display !== 'none'; }).length; })()`);
  for (const theme of THEMES) {
    const tag = `375/${theme}/mobile`;
    await P.size(1440, 900); await P.go(url());
    if (!(await setThemeOn(P, theme))) { restOkM = false; continue; }
    await P.size(375, 812); await sleep(500);
    const viewOk = await setMobile(P, ['s1']);
    await P.evaluate(`document.getElementById('s1').scrollIntoView({ block: 'start', behavior: 'instant' }); true`);
    if (!viewOk || !(await waitView(P, 's1')).ok) { restOkM = false; badM.push({ case: tag, issue: 'iframe widoku s1 nie jest gotowy' }); continue; }
    const m0 = await framePins();
    if (m0 < 1) { restOkM = false; badM.push({ case: tag, issue: 'brak widocznych pinezek w iframe widoku s1' }); continue; }
    await openByEnter(); await gotoItem(LABELS.pins); await P.key('Enter'); await sleep(300);
    const m1 = await framePins(); const st = await menuState();
    await openByEnter(); await gotoItem(LABELS.pins); await P.key('Enter'); await sleep(300);
    const m2 = await framePins();
    logM.push(`${tag}: pinezki w iframe ${m0} -> ${m1} -> ${m2}`);
    if (m1 !== 0) badM.push({ case: tag, issue: 'po „Ukryj pinezki” widocznych pinezek w iframe widoku: ' + m1 + ' z ' + m0 });
    if (st.open) badM.push({ case: tag, issue: 'menu nie zamknęło się po „Ukryj pinezki”' });
    if (m2 !== m0) badM.push({ case: tag, issue: 'po ponownym wyborze pinezek w iframe widoku: ' + m2 + ' zamiast ' + m0 });
  }
  await P.size(1440, 900); await P.go(url());
  const errsM = pageErrors(e1); if (errsM.length) badM.push({ case: 'konsola', issue: errsM.slice(0, 3).map((x) => x.kind + ': ' + x.text).join(' || ') });
  return withMobile(desk, evidence(badM, restOkM, { log: logM }));
}, { timeout: 480000 });

await suite.main();
