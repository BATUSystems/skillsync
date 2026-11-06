// E2E widoku makiety desktop / mobile (D28b, B7a). Niezależny test-writer: scenariusze ze speca
// docs/specs/2026-11-06-prototyp-tryb-widoku.md (K1–K15), kontraktu (scenariusze-e2e.md, sekcja 14) i haków
// (slownik-html-css.md, część C, „Uzupełnienia (widok makiety desktop / mobile…)”), bez czytania implementacji silnika.
// Do B6 scenariusze dają FAIL (oczekiwane). Biegi tylko po GRANT (porty i Chrome).
// D246 (D28c): globalnego przełącznika Desktop / Mobile nie ma (ani w pasku, ani w menu „Więcej”); widok zmienia wyłącznie
// przełącznik lokalny ekranu. „Wszystkie ekrany” w scenariuszach = przełącznik lokalny na każdym ekranie z ramką (`setAll`).
//
// Prototyp testowy tworzy sam skrypt: CLI `init` w katalogu roboczym biegu, ekrany z widok/fixture.mjs, `ids --fix`.
// Macierz: każdy scenariusz przy 1440 i 375 oraz w obu motywach, chyba że opis mówi inaczej.
// Kolejność: V-09 tworzy wątki dla V-10 i V-12; V-15 zamraża iteracje dla V-16 (uruchamiane pojedynczo przez
// SKILLSYNC_E2E_ONLY potrzebują poprzedników).
// SKILLSYNC_E2E_OLD_ENGINE_SHA: commit biblioteki z silnikiem sprzed D28b (V-15, V-16: iteracja sprzed wdrożenia);
// domyślnie efaa3ff.
const OLD_ENGINE_SHA = process.env.SKILLSYNC_E2E_OLD_ENGINE_SHA || 'efaa3ff';
import {
  sleep, env, secret, mail, PORT1, PORT2, connect, workspace, proto, startServer, session, apiOps, apiPost,
  cid, setAcceptance, commentMode, openPanel, closePanelIfOpen, pickFilter, Suite, pageErrors, errors, wasSaved,
  readFileSync, writeFileSync,
} from './lib.mjs';
import { createHash } from 'node:crypto';
import { mkdirSync } from 'node:fs';
import { spawn, execFileSync } from 'node:child_process';
import { SLUG, TITLE, REQUIREMENTS_PATH, REQUIREMENTS, PROJECT_CSS, SCREENS, NAV } from './widok/fixture.mjs';

// ---------------------------------------------------------------- Haki UI (słownik C, punkt „Widok makiety”)
const btnIn = (scope, label) => `[...((${scope})?.querySelectorAll('button') || [])].find((b) => b.textContent.trim() === ${JSON.stringify(label)})`;
const GROUP = `document.querySelector('.doc-toolbar [role="group"][aria-label="Widok"]')`;
const H = {
  group: GROUP,
  // przycisk menu paska rozpoznaje się wyłącznie po nazwie dostępnej „Więcej” (chip konta też ma aria-haspopup)
  menuBtn: `[...document.querySelectorAll('.doc-toolbar [aria-haspopup="menu"]')].find((e) => /Więcej/.test(e.getAttribute('aria-label') || e.textContent))`,
  localSwitch: (sid) => `document.querySelector('#${sid} .screen-meta [data-proto-view-switch]')`,
  localBtn: (sid, label) => btnIn(`document.querySelector('#${sid} .screen-meta [data-proto-view-switch]')`, label),
  iframe: (sid) => `document.querySelector('#${sid} .frame iframe[data-proto-view="mobile"]')`,
  frame: (sid) => `document.querySelector('#${sid} .frame')`,
  card: `.anno-panel .anno-card`,
  badge: '[data-proto-view-badge]',
  viewFilter: (label) => btnIn(`document.querySelector('.anno-panel [role="group"][aria-label="Widok wątków"]')`, label),
  dlgGroupBtn: (label) => btnIn(`document.querySelector('dialog[open] [role="group"][aria-label="Widok"]')`, label),
  stateClasses: ['proto-hotspots', 'proto-pins-hidden', 'proto-presentation'],
  iframeError: (sid) => `!!document.querySelector('#${sid} .frame')?.innerText.includes('Nie udało się wczytać widoku mobile')`,
  retryBtn: (sid) => btnIn(`document.querySelector('#${sid} .frame')`, 'Spróbuj ponownie'),
};
const FRAMED = ['s0', 's1', 's2', 's3', 's5'];   // s0 = ekran przykładowy szablonu; s4 bez ramki
const FRAMELESS = 's4';
const SIZES = [1440, 375];
const THEMES = [false, true];   // dark?

// Dokument iframe widoku (ten sam origin na serwerze; przy file:// dostęp bywa zablokowany — wynik { noDoc: true }).
const inFrame = (sid, body) => `(() => { const f = ${H.iframe(sid)}; const d = f && f.contentDocument; if (!d) return { noDoc: true }; return (${body})(d, f); })()`;
const framePinVisible = (sid) => `(() => { const f = ${H.iframe(sid)}; const d = f && f.contentDocument; return !!d && [...d.querySelectorAll('button.anno-pin')].some((x) => x.getClientRects().length > 0); })()`;
const sha = (path) => createHash('sha256').update(readFileSync(path)).digest('hex').slice(0, 16);

// ---------------------------------------------------------------- Prototyp testowy (CLI init w katalogu roboczym biegu)
async function buildPrototype(ws) {
  mkdirSync(`${ws.proj}/docs/wymagania`, { recursive: true });
  writeFileSync(`${ws.proj}/${REQUIREMENTS_PATH}`, REQUIREMENTS);
  const init = await proto(ws, 'init', SLUG, '--requirements', REQUIREMENTS_PATH, '--title', TITLE);
  if (init.code !== 0) throw new Error('init: ' + (init.err || init.out).slice(0, 300));
  let html = ws.read(SLUG);
  // ekran przykładowy zostaje jako s0 (K2: przełącznik lokalny „także na przykładowym”), własne ekrany s1–s4 za nim
  html = html.replace('id="s1" data-prototype-example aria-labelledby="s1-h"', 'id="s0" data-prototype-example aria-labelledby="s0-h"')
    .replace('<h2 id="s1-h">1. ', '<h2 id="s0-h">0. ').replace('<a href="#s1">1 Lista zgłoszeń</a>', '<a href="#s0">0 Przykład</a>' + NAV);
  const marker = '<!-- Kolejne ekrany wstawiaj tutaj';
  if (!html.includes(marker) || !html.includes('id="s0"')) throw new Error('szablon init: brak znacznika ekranów albo przykładu');
  html = html.replace(marker, SCREENS + '\n  ' + marker);
  writeFileSync(ws.file(SLUG), html);
  writeFileSync(ws.file(SLUG, 'project.css'), readFileSync(ws.file(SLUG, 'project.css'), 'utf8') + PROJECT_CSS);
  const fix = await proto(ws, 'ids', SLUG, '--fix');
  if (fix.code !== 0) throw new Error('ids --fix: ' + (fix.err || fix.out).slice(0, 300));
  const check = await proto(ws, 'ids', SLUG, '--check');
  if (check.code !== 0) throw new Error('ids --check: ' + check.out.slice(0, 300));
}

// ---------------------------------------------------------------- Pomocnicze
async function openPage(c, base, { width, dark, who = null, hash = '#s1', slug = SLUG }) {
  const p = await c.page(`widok-${width}-${dark ? 'd' : 'l'}`, { width, dark });
  if (who) { const s = await session(base, mail(who), secret(who)); await p.setSession(s, base); }
  await p.go(`${base}/${slug}/${hash}`);
  return p;
}
// Prototyp wzorcowy dla V-02: te same ekrany (kopia index.html i project.css), silnik sprzed D28b (4 pliki z OLD_ENGINE_SHA).
const REF_SLUG = 'widok-ref';
async function buildRef(ctx) {
  if (ctx.created.ref) return ctx.created.ref;
  const files = ['prototype.js', 'prototype.css', 'components.css', 'screens.css']; const old = {};
  try { for (const f of files) old[f] = execFileSync('git', ['-C', env.library, 'show', `${OLD_ENGINE_SHA}:skills/prototype/assets/repo/${f}`], { maxBuffer: 16 << 20, stdio: ['ignore', 'pipe', 'ignore'] }); }
  catch (e) { return (ctx.created.ref = { ok: false, reason: `brak silnika sprzed D28b: git show ${OLD_ENGINE_SHA}:skills/prototype/assets/repo/… w ${env.library} nie powiódł się` }); }
  const init = await proto(ctx.ws, 'init', REF_SLUG, '--requirements', REQUIREMENTS_PATH, '--title', TITLE);
  if (init.code !== 0) return (ctx.created.ref = { ok: false, reason: 'init prototypu wzorcowego: ' + (init.err || init.out).slice(0, 160) });
  const html = readFileSync(ctx.ws.file(SLUG), 'utf8').replaceAll(`data-prototype-id="${SLUG}"`, `data-prototype-id="${REF_SLUG}"`).replaceAll(`data-slug="${SLUG}"`, `data-slug="${REF_SLUG}"`);
  writeFileSync(ctx.ws.file(REF_SLUG), html);
  writeFileSync(ctx.ws.file(REF_SLUG, 'project.css'), readFileSync(ctx.ws.file(SLUG, 'project.css')));
  for (const f of files) writeFileSync(ctx.ws.file(REF_SLUG, f), old[f]);
  return (ctx.created.ref = { ok: true, sha: OLD_ENGINE_SHA });
}
// Widok ekranu = stan jego przełącznika lokalnego (aria-pressed „Mobile”); ekran bez przełącznika = desktop.
const viewOf = (p, sid) => p.evaluate(`(${H.localBtn(sid, 'Mobile')})?.getAttribute('aria-pressed') === 'true' ? 'mobile' : 'desktop'`);
const viewsAll = async (p) => Object.fromEntries(await Promise.all(FRAMED.map(async (sid) => [sid, await viewOf(p, sid)])));
const scrollTo = (p, sid) => p.evaluate(`document.getElementById(${JSON.stringify(sid)}).scrollIntoView({ block: 'start', behavior: 'instant' }); true`);
// Ekran faktycznie renderuje widok mobile: po przewinięciu do ekranu iframe widoku jest widoczny.
async function rendersMobile(p, sid, timeout = 8000) {
  await scrollTo(p, sid);
  return (await p.waitFor(`(() => { const f = ${H.iframe(sid)}; return !!f && f.getClientRects().length > 0 && f.getBoundingClientRect().height > 20; })()`, timeout)).ok;
}
// Wysokość iframe widoku przed pomiarem treści (placeholder silnika); K3: potem wysokość według treści.
const PLACEHOLDER_H = 667;
async function waitMobileReady(p, sid, timeout = 8000) {
  await scrollTo(p, sid);
  const ready = await p.waitFor(`(() => { const f = ${H.iframe(sid)}; const d = f && f.contentDocument; return !!d && d.readyState === 'complete' && f.getBoundingClientRect().height > 20; })()`, timeout);
  if (!ready.ok) return ready;
  // K3: czekaj, aż wysokość ramki się ustali: różna od placeholdera i taka sama przez 2 klatki; wysokość równa placeholderowi
  // przyjmujemy dopiero, gdy stoi co najmniej 1 s (treść może mieć dokładnie tyle). Najwyżej 4 s.
  const settle = await p.evaluate(`(async () => { const f = ${H.iframe(sid)}; const frame = () => new Promise((r) => requestAnimationFrame(() => r()));
    const t0 = performance.now(); let prev = null, same = 0, since = t0; const seen = [];
    while (performance.now() - t0 < 4000) { await frame(); const h = Math.round(f.getBoundingClientRect().height * 10) / 10;
      if (h !== prev) { if (seen.length < 8) seen.push(h); prev = h; same = 0; since = performance.now(); } else same += 1;
      if (same >= 2 && (h !== ${PLACEHOLDER_H} || performance.now() - since >= 1000)) return { ok: true, h, ms: Math.round(performance.now() - t0), seen }; }
    return { ok: false, h: prev, ms: 4000, seen }; })()`);
  return { ...ready, settle };
}
const narrowNow = (p) => p.evaluate('innerWidth < 600');
// D246: „wszystkie ekrany” = przełącznik lokalny na każdym ekranie z ramką (globalnego przełącznika nie ma).
async function setAll(p, label) { for (const sid of FRAMED) await p.domClick(H.localBtn(sid, label), `lokalny ${label} ${sid}`); }
// Kontrolki widoku w pasku i w menu „Więcej” (D246: ma ich nie być): grupa „Widok”, przyciski i pozycje menu o nazwie Desktop / Mobile.
const VIEW_CONTROLS = `(() => { const name = (e) => ((e.getAttribute('aria-label') || '') + ' ' + e.textContent).replace(/\\s+/g, ' ').trim();
  const isView = (e) => /^(Desktop|Mobile)$/.test(name(e)) || /^Widok\\b/.test(e.getAttribute('aria-label') || '');
  const bar = document.querySelector('.doc-toolbar'); if (!bar) return { noBar: true };
  const groups = [...bar.querySelectorAll('[role="group"]')].filter((g) => /Widok/.test(g.getAttribute('aria-label') || '')).map((g) => g.getAttribute('aria-label'));
  const ctrls = [...bar.querySelectorAll('button, [role="button"], [role^="menuitem"], select, input')].filter(isView).map(name);
  const menus = [...document.querySelectorAll('[role="menu"]')].filter((m) => m.getClientRects().length);
  const items = menus.flatMap((m) => [...m.querySelectorAll('[role^="menuitem"], button')]).filter((e) => isView(e) || e.getAttribute('role') === 'menuitemradio').map(name);
  return { groups, ctrls, items, menusOpen: menus.length }; })()`;
// Okienko komentarza: czeka na .anno-composer-pop, wpisuje, Ctrl+Enter, czeka na zapis.
async function compose(p, text) {
  const w = await p.waitFor(`!!document.querySelector('.anno-composer-pop')`, 4000);
  if (!w.ok) return false;
  await p.type(text); await p.key('Enter', { ctrl: true });
  const saved = await p.waitFor(`!document.querySelector('.anno-composer-pop') && ${wasSaved}`, 8000);
  await closePanelIfOpen(p);
  return saved.ok;
}
// Punkt w oknie (współrzędne okna) odpowiadający środkowi elementu w dokumencie iframe widoku. Strona jest najpierw
// przewijana tak, żeby ten punkt był na środku okna (iframe widoku nie przewija się w środku, K3); `hit` = punkt trafia
// w iframe (klik CDP poza oknem nigdzie nie trafia). Wołać tuż przed kliknięciem: `commentMode` przewija stronę na górę.
const pointInFrame = (p, sid, sel) => p.evaluate(inFrame(sid, `(d, f) => { const el = d.querySelector(${JSON.stringify(sel)}); if (!el) return null;
  const at = () => { const b = el.getBoundingClientRect(); const fb = f.getBoundingClientRect(); return { x: fb.x + f.clientLeft + b.x + b.width / 2, y: fb.y + f.clientTop + b.y + b.height / 2 }; };
  const p0 = at(); window.scrollBy(0, p0.y - innerHeight / 2); const pt = at();
  const r = (b) => [b.x, b.y, b.width, b.height].map((v) => Math.round(v * 10) / 10);
  const top = document.elementFromPoint(pt.x, pt.y);
  // dowód (bez wpływu na wynik): prostokąty iframe i elementu, element pod punktem kliknięcia, wysokość okna
  const desc = (e) => (e ? { tag: e.tagName.toLowerCase(), cls: String(e.className || '').slice(0, 80), id: e.id || null } : null);
  const diag = { frame: r(f.getBoundingClientRect()), clientTop: f.clientTop, styleHeight: f.style.height || null, el: r(el.getBoundingClientRect()),
    top: desc(top), stack: document.elementsFromPoint(pt.x, pt.y).slice(0, 3).map(desc),
    frameScrollTop: (d.scrollingElement || d.documentElement).scrollTop, innerHeight };
  return { x: pt.x, y: pt.y, hit: pt.y >= 0 && pt.y < innerHeight && pt.x >= 0 && pt.x < innerWidth && top === f, diag }; }`));
const opsList = async (base) => (await apiOps(base, SLUG)).operations || [];
const createWith = (ops, text) => ops.find((o) => o.type === 'create' && String(o.payload?.message?.text ?? '').includes(text));
const screenCommentBtn = (sid) => `document.querySelector('#${sid} .anno-screen-comment')`;

// Macierz 1440/375 × jasny/ciemny: fn(p, meta) zwraca { ok, ... }; wynik zbiorczy z dowodem per komórka.
async function matrix(ctx, fn, { sizes = SIZES, themes = THEMES, who = null, hash } = {}) {
  const cells = [];
  for (const width of sizes) for (const dark of themes) {
    const p = await openPage(ctx.c, ctx.base, { width, dark, who, hash });
    try { const r = await fn(p, { width, dark }); cells.push({ width, dark, ...r }); }
    catch (e) { cells.push({ width, dark, ok: false, error: String(e.message || e).slice(0, 200) }); }
    finally { await p.dispose(); }
  }
  return { ok: cells.every((x) => x.ok), cells };
}

const s = new Suite('widok');
// Każdy scenariusz: wyjątek strony albo console.error w czasie scenariusza = FAIL (consoleErrors), strona i iframe widoku.
// allowConsole: jedyny dozwolony wpis — błąd ładowania iframe widoku z nazwą ekranu (V-20).
// Punkt odniesienia błędów: długość surowej listy `errors` (pageErrors(since) tnie surową listę, a dopiero potem filtruje).
const add = (id, title, fn, { allowConsole = null, ...opts } = {}) => s.add(id, title, async (ctx) => {
  const e0 = errors.length;
  const r = await fn(ctx);
  const errs = pageErrors(e0).filter((e) => !(allowConsole && allowConsole.test(e.text || '')));
  if (errs.length && (r?.status || '') !== 'NOT_RUN') return { ...r, ok: false, status: 'FAIL', consoleErrors: errs.slice(0, 3) };
  return r;
}, opts);
s.setup(async () => {
  const ws = workspace('widok');
  await buildPrototype(ws);
  const srv = await startServer(ws, { port: PORT1 });
  const c = await connect();
  return { ws, srv, c, base: srv.base, created: {} };
});
s.teardown(async (ctx) => { if (ctx) { await ctx.c.closeAll().catch(() => {}); await ctx.srv.stop(); } });

// ---------------------------------------------------------------- K1
add('V-01', 'Start i przeładowanie: każdy ekran z ramką startuje w Desktop; lokalne „Mobile” na każdym ekranie daje iframe widoku, „Desktop” przywraca; przeładowanie = desktop (D246)', (ctx) => matrix(ctx, async (p) => {
  const v0 = await viewsAll(p);
  const local0 = await p.evaluate(`[${FRAMED.map((sid) => `(${H.localBtn(sid, 'Desktop')})?.getAttribute('aria-pressed') ?? null`).join(',')}]`);
  await setAll(p, 'Mobile');
  const vM = await viewsAll(p);
  const rendered = {}; for (const sid of FRAMED) rendered[sid] = await rendersMobile(p, sid);
  await setAll(p, 'Desktop');
  const vD = await viewsAll(p);
  const noIframe = await p.evaluate(`[...document.querySelectorAll('.frame iframe[data-proto-view="mobile"]')].every((f) => !f.getClientRects().length)`);
  await setAll(p, 'Mobile'); await p.reload();
  const vR = await viewsAll(p);
  const all = (v, want) => FRAMED.every((sid) => v[sid] === want);
  const ok = all(v0, 'desktop') && local0.every((x) => x === 'true') && all(vM, 'mobile') && FRAMED.every((sid) => rendered[sid])
    && all(vD, 'desktop') && noIframe && all(vR, 'desktop');
  return { ok, start: { views: v0, localDesktopPressed: local0 }, mobile: { views: vM, rendered }, desktop: { views: vD, noIframe }, afterReload: { views: vR } };
}));

add('V-02', 'Budżet paska bez grupy „Widok” (D246): wysokość paska jak przed D28b (±2 px, prototyp wzorcowy z silnikiem sprzed D28b); 375 i 599 ≤ 2 × najwyższa kontrolka + 32 px (zalogowany i nie)', async (ctx) => {
  const ref = await buildRef(ctx);
  const measure = (p) => p.evaluate(`(() => { const tb = document.querySelector('.doc-toolbar'); if (!tb) return { noBar: true }; const ctl = [...tb.querySelectorAll('button, select, a, input')].filter((e) => e.getClientRects().length);
    const maxH = Math.max(...ctl.map((e) => e.getBoundingClientRect().height)); const h = tb.getBoundingClientRect().height;
    return { h: Math.round(h), maxH: Math.round(maxH), group: !!${H.group}, hscroll: document.documentElement.scrollWidth > document.documentElement.clientWidth }; })()`);
  const rows = [];
  for (const who of [null, 'anna']) for (const width of [375, 599, 600, 768, 1000]) for (const dark of THEMES) {
    const p = await openPage(ctx.c, ctx.base, { width, dark, who });
    let r, rr = null;
    try { r = await measure(p); } finally { await p.dispose(); }
    if (ref.ok) { const q = await openPage(ctx.c, ctx.base, { width, dark, who, slug: REF_SLUG }); try { rr = await measure(q); } finally { await q.dispose(); } }
    const budget = width < 600 ? r.h <= 2 * r.maxH + 32 : true;
    const asBefore = rr ? !rr.noBar && Math.abs(r.h - rr.h) <= 2 : null;
    const ok = !r.noBar && !r.hscroll && !r.group && budget && asBefore !== false;
    rows.push({ who: who || 'anonim', width, dark, ok, ...r, refH: rr ? rr.h : null, asBefore });
  }
  if (!ref.ok && rows.every((x) => x.ok)) return { status: 'NOT_RUN', reason: ref.reason, rows };
  return { ok: rows.every((x) => x.ok) && ref.ok, ref, rows };
});

// ---------------------------------------------------------------- K2
add('V-03', 'Przełącznik lokalny: zmienia tylko swój ekran (Enter, Spacja, klik), ekrany są od siebie niezależne', (ctx) => matrix(ctx, async (p) => {
  const present = Object.fromEntries(await Promise.all(FRAMED.map(async (sid) => [sid, await p.evaluate(`!!${H.localSwitch(sid)}`)])));
  const want = (v, mob) => FRAMED.every((x) => v[x] === (mob.includes(x) ? 'mobile' : 'desktop'));
  // lokalny „Mobile” na s2 (Enter)
  await p.evaluate(`(${H.localBtn('s2', 'Mobile')}).focus(); true`); await p.key('Enter');
  const a = await viewsAll(p); const s2Pressed = await p.evaluate(`(${H.localBtn('s2', 'Mobile')})?.getAttribute('aria-pressed')`);
  const s2Rendered = await rendersMobile(p, 's2');
  // lokalny „Mobile” na s3 (Spacja): s2 zostaje mobile
  await p.evaluate(`(${H.localBtn('s3', 'Mobile')}).focus(); true`); await p.key(' ');
  const b = await viewsAll(p);
  // lokalny „Desktop” na s2 (klik): s3 zostaje mobile
  await p.domClick(H.localBtn('s2', 'Desktop')); const c = await viewsAll(p);
  const s2DeskPressed = await p.evaluate(`(${H.localBtn('s2', 'Desktop')})?.getAttribute('aria-pressed')`);
  await p.domClick(H.localBtn('s3', 'Desktop')); const d = await viewsAll(p);
  const ok = FRAMED.every((sid) => present[sid]) && want(a, ['s2']) && s2Pressed === 'true' && s2Rendered && want(b, ['s2', 's3'])
    && want(c, ['s3']) && s2DeskPressed === 'true' && want(d, []);
  return { ok, present, localS2: a, s2Pressed, s2Rendered, localS3: b, deskS2: c, s2DeskPressed, deskS3: d };
}));

add('V-04', 'Ekran bez ramki: brak przełącznika i iframe widoku przy wszystkich ekranach z ramką w „Mobile”; jego pinezki i nowe komentarze są desktop', async (ctx) => {
  const r = await matrix(ctx, async (p) => {
    await setAll(p, 'Mobile'); await sleep(400);
    const noSwitch = await p.evaluate(`!${H.localSwitch(FRAMELESS)}`);
    const noIframe = await p.evaluate(`!document.querySelector('#${FRAMELESS} iframe')`);
    return { ok: noSwitch && noIframe, noSwitch, noIframe };
  });
  const p = await openPage(ctx.c, ctx.base, { width: 1440, dark: false, who: 'anna', hash: '#' + FRAMELESS });
  try {
    await setAll(p, 'Mobile'); await commentMode(p, true);
    await p.domClick(screenCommentBtn(FRAMELESS), 'Komentarz do ekranu ' + FRAMELESS);
    const saved = await compose(p, 'V-04 komentarz do ekranu bez ramki');
    await commentMode(p, false);
    const op = createWith(await opsList(ctx.base), 'V-04 komentarz do ekranu bez ramki');
    const pinOnPage = await p.evaluate(`[...document.querySelectorAll('#${FRAMELESS} button.anno-pin')].some((x) => x.getClientRects().length > 0)`);
    await openPanel(p); await pickFilter(p, 'Wszystkie iteracje').catch(() => {});
    const badge = await p.evaluate(`(() => { const c = [...document.querySelectorAll('${H.card}')].find((x) => x.textContent.includes('V-04 komentarz do ekranu bez ramki')); return c ? c.querySelector('${H.badge}')?.textContent.trim() ?? null : 'brak karty'; })()`);
    const ok = r.ok && saved && !!op && !('view' in (op.payload || {})) && pinOnPage && badge === 'Desktop';
    return { ok, matrix: r.cells, saved, payloadKeys: op ? Object.keys(op.payload) : null, pinOnPage, badge };
  } finally { await p.dispose(); }
});

// ---------------------------------------------------------------- K3
add('V-05', 'Media queries w iframe widoku: [data-test-mobile-only] widoczny, [data-test-desktop-only] ukryty, dokument min(375, dostępna), margines 0; desktop odwrotnie', (ctx) => matrix(ctx, async (p, m) => {
  // desktop (strona jak dziś): odwrotność sprawdzalna tylko przy oknie ≥ 600 px — przy 375 media queries strony same dają układ telefonu (K4)
  const desk = await p.evaluate(`(() => { const v = (sel) => { const e = document.querySelector('#s1 .frame ' + sel); return !!e && e.getClientRects().length > 0; }; return { m: v('[data-test-mobile-only]'), d: v('[data-test-desktop-only]') }; })()`);
  await p.domClick(H.localBtn('s1', 'Mobile')); const ready = await waitMobileReady(p, 's1');
  const avail = await p.evaluate(`(() => { const fr = ${H.frame('s1')}; const cs = getComputedStyle(fr); return Math.round(fr.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)); })()`);
  const inside = await p.evaluate(inFrame('s1', `(d) => { const v = (sel) => { const e = d.querySelector(sel); return !!e && e.getClientRects().length > 0; };
    return { m: v('[data-test-mobile-only]'), d: v('[data-test-desktop-only]'), docW: d.documentElement.clientWidth, bodyMargin: getComputedStyle(d.body).margin }; }`));
  // D1/D2 (spec) i K3: iframe widoku renderuje tylko ramkę ekranu sN — bez paska prototypu, listy ekranów, panelu, nagłówka
  // dokumentu i innych ekranów (opis ekranu i notatki zostają szerokie na stronie, więc w iframe ich nie ma)
  const onlyFrame = await p.evaluate(inFrame('s1', `(d) => { const vis = (e) => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
    const sels = ['.doc-toolbar', 'nav.screen-nav', '[data-proto-nav]', '[class*="proto-screens"]', '.proto-toolbar', '.doc-head', '.anno-panel', '.anno-threads-btn',
      '#s1 .screen-meta', '#s1 .notes', '.screen:not(#s1)'];
    const extra = sels.filter((s) => [...d.querySelectorAll(s)].some(vis));
    const fr = d.querySelector('#s1 .frame'); return { frameVisible: !!fr && vis(fr), extra }; }`));
  const wantW = Math.min(375, avail);
  const deskOk = m.width < 600 ? true : (desk.m === false && desk.d === true);
  const ok = ready.ok && deskOk && inside.m === true && inside.d === false && Math.abs(inside.docW - wantW) <= 1 && /^0px( 0px)*$/.test(inside.bodyMargin || '')
    && onlyFrame.frameVisible === true && Array.isArray(onlyFrame.extra) && onlyFrame.extra.length === 0;
  return { ok, desktopPage: desk, deskChecked: m.width >= 600, avail, wantW, inside, onlyFrame };
}));

add('V-06', 'Wysokość iframe = wysokość treści (±1 px), bez wewnętrznego przewijania; po rozwinięciu treści nowa wysokość; ekran ze 100vh bez pętli zmian', (ctx) => matrix(ctx, async (p) => {
  await p.domClick(H.localBtn('s1', 'Mobile')); await p.domClick(H.localBtn('s2', 'Mobile'));
  await waitMobileReady(p, 's1');
  // bez przewijania w środku (K3): pionowo (sh ≤ ch), poziomo (sw ≤ cw) i bez stałego paska przewijania (cw = szerokość iframe)
  const fit = (sid) => p.evaluate(inFrame(sid, `(d, f) => ({ h: f.clientHeight, sh: d.documentElement.scrollHeight, ch: d.documentElement.clientHeight,
    sw: d.documentElement.scrollWidth, cw: d.documentElement.clientWidth, fw: f.clientWidth })`));
  const fits = (x) => !x.noDoc && Math.abs(x.h - x.sh) <= 1 && x.sh <= x.ch + 1 && x.sw <= x.cw + 1 && x.fw - x.cw <= 1;
  const before = await fit('s1');
  await p.evaluate(inFrame('s1', `(d) => { d.getElementById('wk-more').hidden = false; return true; }`));
  await sleep(800); const after = await fit('s1');
  // informacyjnie (bez warunku): obrys ramki mobile
  const style = await p.evaluate(`(() => { const f = ${H.iframe('s1')}; const cs = getComputedStyle(f); return { radius: cs.borderTopLeftRadius, border: cs.borderTopWidth }; })()`);
  await waitMobileReady(p, 's2');
  const t = []; for (let i = 0; i < 4; i++) { t.push(await fit('s2')); await sleep(700); }
  const stable = t.every((x) => Math.abs(x.h - t[0].h) <= 1);
  // ekran z treścią dużo wyższą niż okno (s5): ramka ma wysokość całej treści, bez paska przewijania w środku
  await p.domClick(H.localBtn('s5', 'Mobile')); await waitMobileReady(p, 's5');
  const long = await fit('s5');
  const winH = await p.evaluate('innerHeight');
  // D243: w iframe żaden element powłoki szablonu nie jest kontenerem przewijania (overflow-y auto/scroll i scrollHeight >
  // clientHeight + 1). Sprawdzane: `.frame`, `.app`, `.app .main`, `.app .page`, `.frame > .page` i ich rodzice do `.frame`;
  // przewijanie w treści autora (modal, siatka tygodnia) jest dozwolone i nie jest sprawdzane.
  const shellScrollers = (sid) => p.evaluate(inFrame(sid, `(d) => { const fr = d.querySelector('#${sid} .frame') || [...d.querySelectorAll('.frame')].find((e) => e.getClientRects().length); if (!fr) return { noFrame: true, scrollers: null };
    const shell = new Set([fr, ...fr.querySelectorAll('.app, .app .main, .app .page, .frame > .page')]);
    for (const e of [...shell]) { let n = e.parentElement; while (n && n !== fr && fr.contains(n)) { shell.add(n); n = n.parentElement; } }
    const scrollers = []; for (const e of shell) { const cs = getComputedStyle(e);
      if (/^(auto|scroll)$/.test(cs.overflowY) && e.scrollHeight > e.clientHeight + 1) scrollers.push({ el: e.tagName.toLowerCase() + [...e.classList].map((c) => '.' + c).join(''), sh: e.scrollHeight, ch: e.clientHeight, overflowY: cs.overflowY }); }
    return { checked: shell.size, scrollers }; }`));
  const shell = { s1: await shellScrollers('s1'), s5: await shellScrollers('s5') };
  const noShellScroll = Object.values(shell).every((x) => Array.isArray(x.scrollers) && x.scrollers.length === 0);
  const ok = fits(before) && fits(after) && after.h >= before.h + 230 && stable && t.every(fits) && fits(long) && long.h > winH && noShellScroll;
  return { ok, before, after, tall: t.map((x) => x.h), stable, long, winH, shell, style };
}));

add('V-07', 'Strona w widoku mobile: pasek, opis ekranu i notatki zachowują szerokość z desktop; treść ramki strony display: none; plik bez zmian', async (ctx) => {
  const h0 = sha(ctx.ws.file(SLUG));
  const r = await matrix(ctx, async (p) => {
    const widths = () => p.evaluate(`(() => ({ tb: document.querySelector('.doc-toolbar').getBoundingClientRect().width, meta: document.querySelector('#s1 .screen-meta').getBoundingClientRect().width,
      notes: document.querySelector('#s1 .notes').getBoundingClientRect().width }))()`);
    const w0 = await widths();
    await p.domClick(H.localBtn('s1', 'Mobile')); await waitMobileReady(p, 's1');
    const w1 = await widths();
    const hidden = await p.evaluate(`(() => { const el = document.querySelector('#s1 .frame .page'); if (!el) return { gone: true };
      let n = el; while (n && !n.matches('.frame')) { if (getComputedStyle(n).display === 'none') return { displayNone: true }; n = n.parentElement; } return { displayNone: false }; })()`);
    const same = ['tb', 'meta', 'notes'].every((k) => Math.abs(w0[k] - w1[k]) <= 1);
    return { ok: same && (hidden.displayNone || hidden.gone), desktop: w0, mobile: w1, hidden };
  });
  const h1 = sha(ctx.ws.file(SLUG));
  return { ok: r.ok && h0 === h1, fileUnchanged: h0 === h1, cells: r.cells };
});

// ---------------------------------------------------------------- K4
add('V-08', 'Desktop jak dziś: brak iframe w .frame; po Mobile → Desktop na s1 prostokąty elementów ramki jak na starcie (±1 px)', (ctx) => matrix(ctx, async (p) => {
  const rects = () => p.evaluate(`[...document.querySelectorAll('#s1 .frame [data-proto-id], #s1 .frame .page')].map((e) => { const b = e.getBoundingClientRect(); const sb = document.querySelector('#s1').getBoundingClientRect(); return [b.x - sb.x, b.y - sb.y, b.width, b.height]; })`);
  const noIframe = () => p.evaluate(`[...document.querySelectorAll('.frame iframe')].every((f) => !f.getClientRects().length)`);
  const r0 = await rects(); const n0 = await noIframe();
  await p.domClick(H.localBtn('s1', 'Mobile')); await waitMobileReady(p, 's1'); await p.domClick(H.localBtn('s1', 'Desktop')); await sleep(400);
  const r1 = await rects(); const n1 = await noIframe();
  const maxDiff = r0.length === r1.length ? Math.max(0, ...r0.flatMap((a, i) => a.map((v, k) => Math.abs(v - r1[i][k])))) : Infinity;
  return { ok: n0 && n1 && r0.length > 0 && maxDiff <= 1, n: r0.length, maxDiff: Math.round(maxDiff * 10) / 10, noIframeStart: n0, noIframeAfter: n1 };
}));

// ---------------------------------------------------------------- K5 (dziennik)
add('V-09', 'Znacznik widoku w dzienniku: komentarz do elementu i do ekranu w mobile ma payload.view "mobile", w desktop bez pola', async (ctx) => {
  const p = await openPage(ctx.c, ctx.base, { width: 1440, dark: false, who: 'anna', hash: '#s1' });
  try {
    const saved = {};
    await commentMode(p, true);
    // desktop: element na stronie i ekran
    await p.clickEl(`document.querySelector('#s1 .frame #wk-input')`, 'pole s1 (desktop)'); saved.de = await compose(p, 'V-09 desktop element');
    await p.domClick(screenCommentBtn('s1'), 'Komentarz do ekranu s1'); saved.ds = await compose(p, 'V-09 desktop ekran');
    await commentMode(p, false);
    // mobile: element w iframe (klik w iframe → okienko na stronie, K14) i ekran
    await p.domClick(H.localBtn('s1', 'Mobile')); await waitMobileReady(p, 's1'); await commentMode(p, true);
    const pt = await pointInFrame(p, 's1', '#wk-input');
    if (pt && !pt.noDoc) { await p.click(pt.x, pt.y); saved.me = await compose(p, 'V-09 mobile element'); }
    saved.meHit = pt?.hit ?? null;
    await p.domClick(screenCommentBtn('s1'), 'Komentarz do ekranu s1'); saved.ms = await compose(p, 'V-09 mobile ekran');
    await commentMode(p, false); await p.domClick(H.localBtn('s1', 'Desktop'));
    const ops = await opsList(ctx.base);
    const de = createWith(ops, 'V-09 desktop element'), ds = createWith(ops, 'V-09 desktop ekran');
    const me = createWith(ops, 'V-09 mobile element'), ms = createWith(ops, 'V-09 mobile ekran');
    ctx.created.v09 = { de: de?.threadId, ds: ds?.threadId, me: me?.threadId, ms: ms?.threadId };
    const noView = (o) => !!o && !('view' in (o.payload || {}));
    const ok = noView(de) && noView(ds) && me?.payload?.view === 'mobile' && ms?.payload?.view === 'mobile'
      && de?.payload?.anchor?.kind === 'element' && me?.payload?.anchor?.kind === 'element' && ds?.payload?.anchor?.kind === 'screen' && ms?.payload?.anchor?.kind === 'screen';
    return { ok, saved, desktop: { element: de && Object.keys(de.payload), screen: ds && Object.keys(ds.payload) },
      mobile: { element: me?.payload?.view ?? null, screen: ms?.payload?.view ?? null, elementAnchor: me?.payload?.anchor?.kind ?? null } };
  } finally { await p.dispose(); }
}, { timeout: 180000 });

// ---------------------------------------------------------------- K6
add('V-10', 'Pinezki per widok: mobile tylko w iframe widoku, desktop tylko na stronie; przełączenie bez przeładowania', (ctx) => matrix(ctx, async (p) => {
  await p.evaluate('window.__wkNoReload = 1; true');
  const pagePins = () => p.evaluate(`[...document.querySelectorAll('#s1 button.anno-pin')].filter((x) => x.getClientRects().length).map((x) => x.getAttribute('aria-label') || x.textContent.trim())`);
  const framePins = () => p.evaluate(inFrame('s1', `(d) => [...d.querySelectorAll('button.anno-pin')].filter((x) => x.getClientRects().length).map((x) => x.getAttribute('aria-label') || x.textContent.trim())`));
  const desk = await pagePins();
  await p.domClick(H.localBtn('s1', 'Mobile')); await waitMobileReady(p, 's1'); await sleep(600);
  const mobFrame = await framePins(); const mobPage = await pagePins();
  await p.domClick(H.localBtn('s1', 'Desktop')); await sleep(500);
  const desk2 = await pagePins();
  const sameDoc = await p.evaluate('window.__wkNoReload === 1');
  const frameArr = Array.isArray(mobFrame) ? mobFrame : [];
  const disjoint = frameArr.every((x) => !desk.includes(x));
  const ok = desk.length >= 1 && frameArr.length >= 1 && disjoint && mobPage.filter((x) => desk.includes(x)).length === 0
    && JSON.stringify(desk2) === JSON.stringify(desk) && sameDoc;
  return { ok, desktopPage: desk, mobileFrame: mobFrame, mobilePage: mobPage, desktopAgain: desk2, disjoint, sameDoc };
}, { who: 'anna' }));

add('V-11', 'Stan strony w iframe widoku: klasy proto-hotspots, proto-pins-hidden, proto-presentation i data-theme jak na stronie po każdym przełączeniu', (ctx) => matrix(ctx, async (p) => {
  await p.domClick(H.localBtn('s1', 'Mobile')); await waitMobileReady(p, 's1'); await sleep(500);
  const toggle = async (label) => {
    if (await narrowNow(p)) { await p.domClick(H.menuBtn, 'menu Więcej'); await p.domClick(`[...document.querySelectorAll('[role="menu"] [role^="menuitem"]')].find((e) => e.textContent.trim().startsWith(${JSON.stringify(label)}))`, 'menu: ' + label); }
    else await p.domClick(`[...document.querySelectorAll('.doc-toolbar button')].find((b) => b.textContent.trim().startsWith(${JSON.stringify(label)}))`, 'pasek: ' + label);
    await sleep(500);
  };
  const same = () => p.evaluate(`(() => { const f = ${H.iframe('s1')}; const d = f?.contentDocument; if (!d) return { noDoc: true }; const pick = (h) => ${JSON.stringify(H.stateClasses)}.filter((c) => h.classList.contains(c)).join(' ');
    return { page: pick(document.documentElement), frame: pick(d.documentElement), themePage: document.documentElement.getAttribute('data-theme'), themeFrame: d.documentElement.getAttribute('data-theme') }; })()`);
  const eq = (x) => !x.noDoc && x.page === x.frame && x.themePage === x.themeFrame;
  const steps = {};
  steps.start = await same();
  await toggle('Pokaż hotspoty'); steps.hot = await same(); await toggle('Ukryj hotspoty').catch(() => toggle('Pokaż hotspoty')); steps.hotOff = await same();
  await toggle('Ukryj pinezki'); steps.pins = await same(); await toggle('Pokaż pinezki').catch(() => toggle('Ukryj pinezki')); steps.pinsOff = await same();
  await toggle('Prezentacja'); steps.pres = await same();
  await p.key('Escape'); await sleep(300);
  if (await p.evaluate(`document.documentElement.classList.contains('proto-presentation')`)) await toggle('Prezentacja').catch(() => {});
  steps.presOff = await same();
  const theme0 = steps.start.themePage;
  await toggle('Motyw'); steps.theme = await same();
  const changed = !!steps.hot.page?.includes('proto-hotspots') && !!steps.pins.page?.includes('proto-pins-hidden') && !!steps.pres.page?.includes('proto-presentation') && steps.theme.themePage !== theme0;
  return { ok: Object.values(steps).every(eq) && changed, changed, steps };
}, { who: 'anna' }));

// ---------------------------------------------------------------- K7
add('V-12', 'Panel: plakietka na każdej karcie, filtr „Widok wątków” (Mobile ∪ Desktop = Wszystkie), liczniki po filtrze, „Otwarte” + „Mobile”, klik w kartę mobile', (ctx) => matrix(ctx, async (p) => {
  await openPanel(p);
  const cards = () => p.evaluate(`[...document.querySelectorAll('${H.card}')].filter((c) => c.getClientRects().length).map((c) => ({ badge: c.querySelector('${H.badge}')?.textContent.trim() || null }))`);
  const pressed = (label) => p.evaluate(`(${H.viewFilter(label)})?.getAttribute('aria-pressed') ?? null`);
  const counter = (name) => p.evaluate(`(() => { const b = [...document.querySelectorAll('.anno-filters button')].find((x) => x.textContent.trim().startsWith(${JSON.stringify(name)})); const m = b && b.textContent.match(/(\\d+)\\s*$/); return m ? Number(m[1]) : null; })()`);
  await pickFilter(p, 'Wszystkie iteracje').catch(() => {});
  const startPressed = await pressed('Wszystkie');
  const all = await cards(); const cAll = await counter('Otwarte');
  await p.domClick(H.viewFilter('Mobile'), 'filtr widoku Mobile'); const onlyMobile = await cards(); const cMob = await counter('Otwarte');
  await p.domClick(H.viewFilter('Desktop'), 'filtr widoku Desktop'); const onlyDesktop = await cards(); const cDesk = await counter('Otwarte');
  await p.domClick(H.viewFilter('Wszystkie'), 'filtr widoku Wszystkie');
  // iloczyn z filtrem statusu
  await pickFilter(p, 'Otwarte'); await p.domClick(H.viewFilter('Mobile'), 'filtr widoku Mobile');
  const openMobile = await cards();
  const openMobileStatus = await p.evaluate(`[...document.querySelectorAll('${H.card}')].filter((c) => c.getClientRects().length).every((c) => !!c.querySelector('.anno-st-open') || c.classList.contains('anno-st-open'))`);
  await p.domClick(H.viewFilter('Wszystkie')); await pickFilter(p, 'Otwarte').catch(() => {});
  // klik w kartę mobile → ekran s1 w mobile, przewinięty, pinezka w iframe
  await p.domClick(H.localBtn('s1', 'Desktop')).catch(() => {});
  await p.evaluate('window.scrollTo(0, 0); true');
  await p.domClick(`[...document.querySelectorAll('${H.card}')].find((c) => c.getClientRects().length && c.querySelector('${H.badge}')?.textContent.trim() === 'Mobile')`, 'karta Mobile');
  await sleep(800);
  const after = { view: await viewOf(p, 's1'),
    inView: await p.evaluate(`(() => { const b = document.querySelector('#s1 .frame').getBoundingClientRect(); return b.bottom > 0 && b.top < innerHeight; })()`),
    pin: (await p.waitFor(framePinVisible('s1'), 6000)).ok };
  const ok = startPressed === 'true' && all.length >= 4 && all.every((c) => c.badge === 'Desktop' || c.badge === 'Mobile')
    && onlyMobile.length >= 1 && onlyMobile.every((c) => c.badge === 'Mobile') && onlyDesktop.length >= 1 && onlyDesktop.every((c) => c.badge === 'Desktop')
    && onlyMobile.length + onlyDesktop.length === all.length
    && cAll != null && cMob != null && cDesk != null && cMob + cDesk === cAll
    && openMobile.every((c) => c.badge === 'Mobile') && openMobileStatus && cMob === openMobile.length
    && after.view === 'mobile' && after.inView && after.pin;
  return { ok, startPressed, cards: { all: all.length, mobile: onlyMobile.length, desktop: onlyDesktop.length }, counters: { all: cAll, mobile: cMob, desktop: cDesk },
    openMobile: openMobile.length, openMobileStatus, after };
}, { who: 'anna' }));

// ---------------------------------------------------------------- K9
add('V-13', 'Serwer: view tylko "desktop"/"mobile" (inne = 400); bez „views” w server.features komentarz w mobile zablokowany z komunikatem, bez kolejki i odrzuconych; desktop działa', async (ctx) => {
  const sA = await session(ctx.base, mail('anna'), secret('anna'));
  const sess = await (await fetch(`${ctx.base}/_api/v1/session`)).json();
  const features = sess?.server?.features || [];
  const ops0 = await apiOps(ctx.base, SLUG);
  const iteration = ops0.iteration?.current ?? ops0.iteration ?? 1;
  const post = (view, tag) => { const threadId = 'th-' + cid('v13' + tag).slice(2);
    const payload = { message: { id: 'm-' + threadId.slice(3), text: 'V-13 API ' + tag }, screen: 's1', anchor: { kind: 'screen', x: 0.5, y: 0.05, iteration } };
    if (view !== undefined) payload.view = view;
    return apiPost(sA, SLUG, { cid: cid('v13'), type: 'create', threadId, payload }); };
  // kontrola pozytywna: ten sam kształt z poprawnym view przechodzi (inaczej 400 dla złych wartości niczego nie dowodzi)
  const good = { desktop: (await post('desktop', 'desktop')).status, mobile: (await post('mobile', 'mobile')).status, none: (await post(undefined, 'brak')).status };
  const bad = [];
  for (const [i, v] of ['tablet', 'Mobile', 5, null, ['mobile'], true].entries()) bad.push({ v, status: (await post(v, 'zle' + i)).status });
  const p = await openPage(ctx.c, ctx.base, { width: 1440, dark: false, who: 'anna', hash: '#s1' });
  let ui = {};
  try {
    await dropViewsFeature(p);
    await p.reload();
    const featuresSeen = await p.evaluate(`fetch('/_api/v1/session').then((r) => r.json()).then((j) => j?.server?.features || null).catch(() => 'błąd')`);
    await p.domClick(H.localBtn('s1', 'Mobile')); await waitMobileReady(p, 's1'); await commentMode(p, true);
    // komunikat: nowy tekst w miejscach komunikatów ze słownika (role="status"/"alert", .toast, okienko komentarza, banery);
    // kontrakt nie podaje treści komunikatu, więc test nie wymaga konkretnych słów
    const notices = `[...document.querySelectorAll('[role="status"], [role="alert"], .toast, .anno-composer-pop, .anno-banners')].filter((e) => e.getClientRects().length).map((e) => e.innerText.trim()).join('\\n')`;
    const textBefore = await p.evaluate(notices);
    const n0 = (await opsList(ctx.base)).length;
    const pt = await pointInFrame(p, 's1', '#wk-input');
    if (pt && !pt.noDoc) await p.click(pt.x, pt.y);
    await sleep(500);
    if (await p.evaluate(`!!document.querySelector('.anno-composer-pop')`)) { await p.type('V-13 nie powinno się zapisać'); await p.key('Enter', { ctrl: true }); }
    await sleep(1500);
    const n1 = (await opsList(ctx.base)).length;
    const textAfter = await p.evaluate(notices);
    const before = new Set(textBefore.split('\n').map((x) => x.trim()));
    const msg = textAfter.split('\n').map((x) => x.trim()).filter((x) => x && !before.has(x)).slice(0, 3);
    const storage = await p.evaluate(`(() => { const out = {}; for (const k of Object.keys(localStorage)) { if (/^skillsync-prototype-(queue|rejected):v2:/.test(k)) out[k.split(':')[0]] = (localStorage.getItem(k) || '').includes('V-13 nie powinno'); } return out; })()`);
    await p.key('Escape'); await commentMode(p, false);
    await p.domClick(H.localBtn('s1', 'Desktop')); await commentMode(p, true);
    await p.domClick(screenCommentBtn('s1'), 'Komentarz do ekranu s1'); const deskSaved = await compose(p, 'V-13 desktop bez flagi');
    await commentMode(p, false);
    const dsk = createWith(await opsList(ctx.base), 'V-13 desktop bez flagi');
    ui = { featuresSeen, clickHit: pt?.hit ?? null, blocked: n1 === n0, msg, storageHasEntry: storage, desktopSaved: deskSaved && !!dsk && !('view' in dsk.payload) };
  } finally { await p.dispose(); }
  const noStore = Object.values(ui.storageHasEntry || {}).every((x) => x === false);
  const ok = features.includes('views') && good.desktop === 201 && good.mobile === 201 && good.none === 201 && bad.every((b) => b.status === 400)
    && Array.isArray(ui.featuresSeen) && !ui.featuresSeen.includes('views') && ui.blocked && ui.msg?.length > 0 && noStore && ui.desktopSaved;
  return { ok, features, good, bad, ui };
});

// Przechwycenie odpowiedzi sesji na etapie Response (zachowuje ciasteczko i nagłówki żądania strony).
async function dropViewsFeature(p) {
  const l = async (m) => {
    if (m.method !== 'Fetch.requestPaused' || m.params.responseStatusCode == null) return;
    const id = m.params.requestId;
    try {
      const body = await p.send('Fetch.getResponseBody', { requestId: id });
      const text = body.base64Encoded ? Buffer.from(body.body, 'base64').toString('utf8') : body.body;
      const j = JSON.parse(text); if (j.server) j.server.features = (j.server.features || []).filter((x) => x !== 'views');
      await p.send('Fetch.fulfillRequest', { requestId: id, responseCode: m.params.responseStatusCode, responseHeaders: m.params.responseHeaders,
        body: Buffer.from(JSON.stringify(j)).toString('base64') });
    } catch (e) { await p.send('Fetch.continueRequest', { requestId: id }).catch(() => {}); }
  };
  p._fetchListener = l; p.listeners.push(l);
  await p.send('Fetch.enable', { patterns: [{ urlPattern: '*/_api/v1/session*', requestStage: 'Response' }] });
}

// ---------------------------------------------------------------- K11
add('V-14', 'Akceptacja i hashe: przełączanie widoków nie zmienia hashy ekranów, version ani chipu „Akceptacja k/n”', async (ctx) => {
  const sC = await session(ctx.base, mail('celina'), secret('celina'));
  const sD = await session(ctx.base, mail('dawid'), secret('dawid'));
  await setAcceptance(sC, sD, ctx.base, SLUG, 's1', true);
  const before = await apiOps(ctx.base, SLUG);
  const chip = (p) => p.evaluate(`(document.querySelector('.anno-accept-count') || document.querySelector('.anno-accept'))?.textContent.replace(/\\s+/g, ' ').trim() ?? null`);
  const r = await matrix(ctx, async (p) => {
    const c0 = await chip(p);
    await setAll(p, 'Mobile'); await waitMobileReady(p, 's1'); await p.domClick(H.localBtn('s2', 'Desktop'));
    const cM = await chip(p);
    await setAll(p, 'Desktop'); const cD = await chip(p);
    return { ok: !!c0 && /\d+\s*\/\s*\d+/.test(c0) && c0 === cM && c0 === cD, chip: [c0, cM, cD] };
  }, { who: 'celina' });
  const after = await apiOps(ctx.base, SLUG);
  const sameScreens = JSON.stringify(before.screens) === JSON.stringify(after.screens);
  const sameVersion = before.version === after.version;
  const acc = (x) => JSON.stringify(x.acceptance?.s1 || null);
  return { ok: r.ok && sameScreens && sameVersion && acc(before) === acc(after) && !!before.acceptance?.s1, sameScreens, sameVersion, acceptanceKept: acc(before) === acc(after), cells: r.cells };
});

// ---------------------------------------------------------------- K12
add('V-15', 'Iteracje zamrożone: strona iteracji z nowym silnikiem ma widoki; iteracja ze starym silnikiem tylko desktop, bez grupy i iframe, 0 błędów', async (ctx) => {
  const it = await proto(ctx.ws, 'iterate', SLUG);
  const n = it.json?.iteration;
  ctx.created.iterNew = n;
  const r = await matrix(ctx, async (p) => {
    await p.go(`${ctx.base}/${SLUG}/iterations/${n}/#s1`);
    const group = await p.evaluate(`!!${H.localSwitch('s1')}`);   // D246: na stronie iteracji jest przełącznik lokalny
    await p.domClick(H.localBtn('s1', 'Mobile')); const ready = await waitMobileReady(p, 's1');
    await p.domClick(H.localBtn('s2', 'Desktop')).catch(() => {}); const local = await viewOf(p, 's2');
    return { ok: group && ready.ok && (await viewOf(p, 's1')) === 'mobile' && local === 'desktop', group, ready: ready.ok, local };
  });
  // Iteracja sprzed wdrożenia: druga zamrożona kopia z prototype.js ze starego commitu biblioteki.
  let old = { status: 'NOT_RUN' };
  let oldJs = null;
  try { oldJs = execFileSync('git', ['-C', env.library, 'show', `${OLD_ENGINE_SHA}:skills/prototype/assets/repo/prototype.js`], { maxBuffer: 16 << 20, stdio: ['ignore', 'pipe', 'ignore'] }); }
  catch (e) { old.reason = `brak starego silnika: git show ${OLD_ENGINE_SHA}:skills/prototype/assets/repo/prototype.js w ${env.library} nie powiódł się`; }
  if (oldJs) {
    const it2 = await proto(ctx.ws, 'iterate', SLUG); const m = it2.json?.iteration;
    writeFileSync(`${ctx.ws.proj}/docs/prototypes/${SLUG}/iterations/${m}/prototype.js`, oldJs);
    ctx.created.iterOld = m;
    const cells = [];
    for (const width of SIZES) {
      const p = await openPage(ctx.c, ctx.base, { width, dark: false, hash: '#s1' });
      try {
        const e0 = errors.length;
        await p.go(`${ctx.base}/${SLUG}/iterations/${m}/#s1`);
        const c = { width, group: await p.evaluate(`!!${H.group}`), radio: await p.evaluate(`!!document.querySelector('[role="menuitemradio"]')`), local: await p.evaluate(`!!document.querySelector('[data-proto-view-switch]')`),
          iframe: await p.evaluate(`!!document.querySelector('.frame iframe')`), errors: pageErrors(e0).length,
          frameVisible: await p.evaluate(`document.querySelector('#s1 .frame').getClientRects().length > 0`) };
        c.ok = !c.group && !c.radio && !c.local && !c.iframe && c.errors === 0 && c.frameVisible;
        cells.push(c);
      } finally { await p.dispose(); }
    }
    old = { status: cells.every((c) => c.ok) ? 'PASS' : 'FAIL', iteration: m, cells };
  }
  if (old.status === 'NOT_RUN' && it.code === 0 && r.ok) return { status: 'NOT_RUN', reason: old.reason, iteration: n, cells: r.cells };
  return { ok: it.code === 0 && r.ok && old.status === 'PASS', iteration: n, cells: r.cells, oldIteration: old };
});

add('V-16', 'Porównanie i podgląd przywracania: w dialog[open] grupa „Widok” (Desktop domyślnie); „Mobile” = iframe podglądu min(375, dostępna) dla iteracji nowej i starej', async (ctx) => {
  const iters = [['nowa', ctx.created.iterNew], ['stara', ctx.created.iterOld]].filter(([, n]) => n != null);
  if (!iters.length) return { status: 'NOT_RUN', reason: 'brak iteracji z V-15 (uruchom V-15 przed V-16)' };
  const pick = (p, sel, n) => p.evaluate(`(() => { const s = document.querySelector(${JSON.stringify(sel)}); if (!s) return null; const o = [...s.options].find((x) => x.value === ${JSON.stringify(String(n))} || x.textContent.trim().includes(${JSON.stringify(String(n))}));
    if (!o) return { missing: [...s.options].map((x) => x.value) }; s.value = o.value; s.dispatchEvent(new Event('change', { bubbles: true })); return { value: s.value }; })()`);
  const measure = async (p, opener, sel, n, what) => {
    await p.domClick(opener, what);
    if (!(await p.waitFor(`!!document.querySelector('dialog[open]')`, 4000)).ok) return { ok: false, what, error: 'okno się nie otworzyło' };
    const picked = await pick(p, sel, n); await sleep(600);
    await p.waitFor(`[...document.querySelectorAll('dialog[open] iframe')].some((x) => x.getClientRects().length)`, 6000);
    const pressed0 = await p.evaluate(`(${H.dlgGroupBtn('Desktop')})?.getAttribute('aria-pressed') ?? null`);
    await p.domClick(H.dlgGroupBtn('Mobile'), what + ': Mobile').catch(() => {}); await sleep(700);
    const st = await p.evaluate(`(() => { const fs = [...document.querySelectorAll('dialog[open] iframe')].filter((x) => x.getClientRects().length);
      return { pressed: (${H.dlgGroupBtn('Mobile')})?.getAttribute('aria-pressed') ?? null, frames: fs.map((f) => { const par = f.parentElement; const cs = getComputedStyle(par);
        const avail = par.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight); return { w: Math.round(f.getBoundingClientRect().width), avail: Math.round(avail) }; }) }; })()`);
    await p.key('Escape'); await sleep(300);
    const widthsOk = st.frames.length > 0 && st.frames.every((f) => Math.abs(f.w - Math.min(375, f.avail)) <= 1);
    return { ok: !!picked?.value && pressed0 === 'true' && st.pressed === 'true' && widthsOk, what, picked, pressed0, ...st };
  };
  const cmpOpener = `[...document.querySelectorAll('#s1 button, #s1 .anno-screen-tools button')].find((b) => b.textContent.includes('Porównaj z iteracją') && b.getClientRects().length > 0)`;
  const rstOpener = `[...document.querySelectorAll('#s1 button')].find((b) => b.textContent.includes('Przywróć') && b.getClientRects().length > 0)`;
  const r = await matrix(ctx, async (p) => {
    const res = [];
    for (const [label, n] of iters) {
      res.push(await measure(p, cmpOpener, '.anno-compare-pick select', n, `Porównaj z iteracją (${label} ${n})`));
      res.push(await measure(p, rstOpener, '#anno-restore-from', n, `Przywróć… (${label} ${n})`));
    }
    return { ok: res.every((x) => x.ok), res };
  }, { who: 'dawid' });
  // brak iteracji ze starym silnikiem (V-15 NOT_RUN): część „stara” NOT_RUN z powodem
  if (iters.length < 2 && r.ok) return { status: 'NOT_RUN', reason: 'brak iteracji ze starym silnikiem (V-15: NOT_RUN)', cells: r.cells };
  return r;
});

// ---------------------------------------------------------------- K13
add('V-17', 'file:// i serwer statyczny: lokalny „Mobile” na s1 renderuje iframe widoku, media queries działają, konsola bez błędów', async (ctx) => {
  const dir = `${ctx.ws.proj}/docs/prototypes`;
  const stat = spawn('python3', ['-m', 'http.server', String(PORT2), '--bind', '127.0.0.1', '--directory', dir], { stdio: 'ignore', detached: true }); stat.unref();
  await sleep(1200);
  const out = [];
  try {
    for (const url of [`file://${dir}/${SLUG}/index.html#s1`, `http://127.0.0.1:${PORT2}/${SLUG}/#s1`]) {
      for (const width of SIZES) {
        const e0 = errors.length;
        const p = await ctx.c.page('widok-static', { width });
        try {
          await p.go(url); await p.domClick(H.localBtn('s1', 'Mobile'));
          const rendered = await rendersMobile(p, 's1');
          // oba tryby: iframe widoku istnieje, szerokość min(375, dostępna), wysokość > 0, w 5 s bez komunikatu K15 (było `ready`)
          await sleep(5500);
          const geo = await p.evaluate(`(() => { const f = ${H.iframe('s1')}; const fr = ${H.frame('s1')}; if (!f || !fr) return null; const cs = getComputedStyle(fr); const b = f.getBoundingClientRect();
            return { w: Math.round(b.width), h: Math.round(b.height), avail: Math.round(fr.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)) }; })()`);
          const k15 = await p.evaluate(H.iframeError('s1'));
          const geoOk = !!geo && Math.abs(geo.w - Math.min(375, geo.avail)) <= 1 && geo.h > 0;
          // media queries w dokumencie iframe tylko na serwerze statycznym (file://: dokument iframe może być niedostępny — osobny origin)
          const isFile = url.startsWith('file://');
          const mq = await p.evaluate(inFrame('s1', `(d) => { const v = (sel) => { const e = d.querySelector(sel); return !!e && e.getClientRects().length > 0; }; return { m: v('[data-test-mobile-only]'), d: v('[data-test-desktop-only]') }; }`));
          const mqOk = isFile ? true : (!mq.noDoc && mq.m === true && mq.d === false);
          out.push({ mode: isFile ? 'file' : 'static', width, rendered, geo, geoOk, k15, mq: mq.noDoc ? null : mq, mqChecked: !isFile, mqOk, errors: pageErrors(e0).length });
        } finally { await p.dispose(); }
      }
    }
  } finally { try { process.kill(-stat.pid); } catch (e) { try { process.kill(stat.pid); } catch (e2) { /* zakończony */ } } }
  return { ok: out.length === 4 && out.every((x) => x.rendered && x.geoOk && !x.k15 && x.mqOk && x.errors === 0), out,
    note: 'media queries w iframe sprawdzane tylko na serwerze statycznym; pinezki i komentowanie w file:// i na serwerze statycznym poza pomiarem (kontrakt: „jak dziś”, bez wzorca w tym skrypcie)' };
});

// ---------------------------------------------------------------- K14
add('V-18', 'Okienko komentarza w mobile: na stronie przy kliknięciu, w całości w oknie (także niska ramka); zapis tworzy wątek mobile; Esc oddaje fokus do elementu w iframe', (ctx) => matrix(ctx, async (p, m) => {
  const res = {};
  for (const [sid, sel] of [['s1', '#wk-input'], ['s3', '#wk-low']]) {
    await p.domClick(H.localBtn(sid, 'Mobile')); const rdy = await waitMobileReady(p, sid); await commentMode(p, true);
    const pt = await pointInFrame(p, sid, sel);
    if (!pt || pt.noDoc) { res[sid] = { ok: false, error: 'brak elementu w iframe widoku' }; await commentMode(p, false); continue; }
    await p.click(pt.x, pt.y); await sleep(500);
    const pop = await p.evaluate(`(() => { const e = document.querySelector('.anno-composer-pop'); if (!e) return null; const b = e.getBoundingClientRect();
      return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height), inWin: b.top >= 0 && b.bottom <= innerHeight + 1 && b.left >= 0 && b.right <= innerWidth + 1 }; })()`);
    // „przy klikniętym miejscu”: punkt kliknięcia w odległości ≤ 48 px od prostokąta okienka (tolerancja dopasowania do okna)
    const near = !!pop && (pt.y >= pop.y - 48 && pt.y <= pop.y + pop.h + 48) && (pt.x >= pop.x - 48 && pt.x <= pop.x + pop.w + 48);
    await p.key('Escape'); await sleep(300);
    const focus = await p.evaluate(`(() => { const f = ${H.iframe(sid)}; const a = f?.contentDocument?.activeElement; return document.activeElement === f && !!a && a.matches(${JSON.stringify(sel)}); })()`);
    let saved = null, view = null;
    if (sid === 's3') {
      const pt2 = await pointInFrame(p, sid, sel); await p.click(pt2.x, pt2.y);
      const text = `V-18 mobile ${m.width} ${m.dark ? 'ciemny' : 'jasny'}`;
      saved = await compose(p, text);
      const op = createWith(await opsList(ctx.base), text); view = op?.payload?.view ?? null;
    }
    await commentMode(p, false);
    res[sid] = { ok: !!pop && pop.inWin && near && focus && (sid !== 's3' || (saved && view === 'mobile')), click: { x: Math.round(pt.x), y: Math.round(pt.y), hit: pt.hit, diag: pt.diag, settle: rdy.settle ?? null }, pop, near, focus, saved, view };
  }
  return { ok: !!res.s1?.ok && !!res.s3?.ok, ...res };
}, { who: 'anna' }));

add('V-19', 'Klawiatura: przełącznik lokalny (Enter/Spacja), pasek i pinezka w iframe osiągalne Tabem; fokus nie ginie strona ↔ iframe', (ctx) => matrix(ctx, async (p, m) => {
  const narrow = m.width < 600;
  // aktywacja z klawiatury (D246: tylko przełącznik lokalny): Spacja na „Mobile” s0, Enter na „Desktop” s0
  await p.evaluate(`(${H.localBtn('s0', 'Mobile')}).focus(); true`); await p.key(' ');
  const kbLocalMobile = await viewOf(p, 's0');
  await p.evaluate(`(${H.localBtn('s0', 'Desktop')}).focus(); true`); await p.key('Enter');
  const kbLocal = await viewOf(p, 's0');
  await p.domClick(H.localBtn('s1', 'Mobile')); await waitMobileReady(p, 's1'); await sleep(400);
  // obieg Tabem od znanego elementu: pierwszej widocznej kontrolki paska. Pasek, ekran s0 i s1 (z iframe widoku) leżą przed
  // końcem dokumentu, więc obieg kończy się, zanim przeglądarka zawinie fokus na body; każde body po starcie = utrata fokusu.
  const started = await p.evaluate(`(() => { window.scrollTo(0, 0); const el = [...document.querySelectorAll('.doc-toolbar button, .doc-toolbar a[href], .doc-toolbar select, .doc-toolbar input')]
    .find((e) => e.getClientRects().length && !e.disabled && e.tabIndex >= 0); if (!el) return false; el.focus(); return document.activeElement === el; })()`);
  const seen = new Set(); let lost = 0; let prev = null; const trail = [];
  for (let i = 0; i < 90; i++) {
    await p.key('Tab');
    const w = await p.evaluate(`(() => { const a = document.activeElement; if (!a || a === document.body || a === document.documentElement) return 'body';
      if (a.tagName === 'IFRAME') { const x = a.contentDocument?.activeElement; if (!x || x === a.contentDocument.body) return 'iframe-body'; return x.classList.contains('anno-pin') ? 'pin-iframe' : 'iframe'; }
      if (a.closest('.doc-toolbar')) return 'toolbar'; if (a.closest('[data-proto-view-switch]')) return 'local'; return 'other'; })()`);
    if (w === 'body' || w === 'iframe-body') lost++;
    if (w !== prev) trail.push(w);
    seen.add(w); prev = w;
    if (seen.has('pin-iframe') && seen.has('local') && seen.has('toolbar') && i > 10) break;
  }
  // pinezka w iframe z klawiatury: fokus na pinezce, Enter otwiera wątek
  await p.evaluate(`(${H.iframe('s1')}).focus(); true`);
  const pinFocused = await p.evaluate(inFrame('s1', `(d) => { const b = d.querySelector('button.anno-pin'); if (b) b.focus(); return !!b && d.activeElement === b; }`));
  await p.key('Enter'); await sleep(600);
  const pinOpen = await p.evaluate(`!!document.querySelector('.anno-panel.open .anno-thread, .anno-panel.open textarea[aria-label="Odpowiedź"]')`);
  const ok = kbLocalMobile === 'mobile' && kbLocal === 'desktop'
    && started && seen.has('local') && seen.has('pin-iframe') && seen.has('toolbar') && lost === 0 && pinFocused === true && pinOpen;
  return { ok, kbLocalMobile, kbLocal, started, seen: [...seen], trail: trail.slice(0, 40), lost, pinFocused, pinOpen };
}, { who: 'anna' }));

// ---------------------------------------------------------------- K15
add('V-20', 'iframe widoku bez gotowości: po 5 s „Nie udało się wczytać widoku mobile” i „Spróbuj ponownie”; po odblokowaniu działa; w konsoli jeden błąd z nazwą ekranu', (ctx) => matrix(ctx, async (p) => {
  let block = true;
  await p.intercept([`*embed=view*`], async (ev, api) => { if (block) { /* brak odpowiedzi: żądanie wisi */ return; } await api.continue(); });
  const e0 = errors.length;
  // start z góry strony: przełączenie s1 na mobile, potem przewinięcie do ekranu
  await p.evaluate('window.scrollTo(0, 0); true');
  await p.domClick(H.localBtn('s1', 'Mobile'));
  const t0 = Date.now();
  await scrollTo(p, 's1');
  const err = await p.waitFor(H.iframeError('s1'), 10000); const after = Date.now() - t0;
  const retry = await p.evaluate(`!!(${H.retryBtn('s1')})`);
  const errs = pageErrors(e0);
  block = false; await p.domClick(H.retryBtn('s1'), 'Spróbuj ponownie').catch(() => {});
  const ready = await waitMobileReady(p, 's1', 9000);
  const msgGone = await p.evaluate(`!(${H.iframeError('s1')})`);
  await p.interceptOff();
  const oneErr = errs.length === 1 && /\bs1\b/.test(errs[0].text || '') && errs[0].kind === 'console.error';
  return { ok: err.ok && after >= 4500 && retry && ready.ok && msgGone && oneErr, errAfterMs: after, retry, readyAfterRetry: ready.ok, msgGone,
    console: errs.map((e) => ({ kind: e.kind, text: (e.text || '').slice(0, 120) })) };
}), { allowConsole: /\bs1\b/ });

// ---------------------------------------------------------------- D2a, bezpieczeństwo (D245)
// Spec D2a: odbiorca `postMessage` przyjmuje tylko typy z listy i tylko z `event.source` równym oknu własnego iframe widoku
// (albo rodzica); instancja osadzona `embed=view` nie zapisuje i nie odpytuje serwera (dane i zapisy ma tylko strona).
add('V-21', 'postMessage: podrobiony „pick” od strony i od obcej ramki nie otwiera okienka ani nie zapisuje; iframe widoku nie wysyła żądań /_api/', async (ctx) => {
  const res = [];
  for (const width of SIZES) {
    const p = await ctx.c.page(`widok-v21-${width}`, { width });
    // licznik sieci przez CDP: każde żądanie z identyfikatorem ramki (strona albo iframe widoku)
    const reqs = []; const ctxs = [];
    p.listeners.push((m) => { if (m.method === 'Network.requestWillBeSent') reqs.push({ frameId: m.params.frameId, method: m.params.request.method, url: m.params.request.url }); });
    // konteksty wykonania ramek (CDP): kod uruchomiony w kontekście ramki ma `event.source` = okno tej ramki. `contentWindow.eval`
    // ze skryptu strony dziedziczy stronę (incumbent settings object), więc źródłem byłaby strona.
    p.listeners.push((m) => { if (m.method === 'Runtime.executionContextCreated') ctxs.push(m.params.context); if (m.method === 'Runtime.executionContextsCleared') ctxs.length = 0; });
    const frames = async () => { const tree = await p.send('Page.getFrameTree'); const out = []; const walk = (n) => { for (const ch of n.childFrames || []) { out.push(ch.frame); walk(ch); } }; walk(tree.frameTree); return { main: tree.frameTree.frame.id, children: out }; };
    const evalIn = async (frameId, expression) => { const c = ctxs.filter((x) => x.auxData && x.auxData.frameId === frameId && x.auxData.isDefault).pop(); if (!c) return { noContext: true };
      const r = await p.send('Runtime.evaluate', { expression, contextId: c.id, returnByValue: true }); return r.exceptionDetails ? { error: (r.exceptionDetails.text || '').slice(0, 120) } : { value: r.result.value }; };
    const viewFrameOf = async (sid) => { for (const fr of (await frames()).children) { if (!/[?&]embed=view\b/.test(fr.url || '')) continue; const h = await evalIn(fr.id, 'location.hash'); if (h.value === '#' + sid) return fr.id; } return null; };
    try {
      const sA = await session(ctx.base, mail('anna'), secret('anna')); await p.setSession(sA, ctx.base);
      await p.go(`${ctx.base}/${SLUG}/#s1`);
      await p.domClick(H.localBtn('s1', 'Mobile')); await p.domClick(H.localBtn('s3', 'Mobile'));
      const ready = (await waitMobileReady(p, 's1')).ok && (await waitMobileReady(p, 's3')).ok;
      await commentMode(p, true);
      const protoId = await p.evaluate(inFrame('s1', `(d) => d.querySelector('#wk-input')?.getAttribute('data-proto-id') || null`));
      const composerOpen = () => p.evaluate(`!!document.querySelector('.anno-composer-pop')`);
      const n0 = (await opsList(ctx.base)).length;
      // (a1) strona podszywa się pod iframe: wiadomość „pick” z event.source = okno strony (kilka kształtów, typ z listy D2a)
      // Kształt „pick” przyjmowany przez stronę: `anchor` na wierzchu i `rect` {left, top, right, bottom} (ustalenie review rundy 2 D28b);
      // pierwszy element listy ma ten kształt, pozostałe są dodatkowymi wariantami. Kontrola dodatnia kształtu (niżej): ta sama
      // wiadomość z okna prawdziwego iframe widoku otwiera okienko, więc brak efektu ze strony i obcej ramki wynika z `event.source`.
      const PICK = { type: 'skillsync-proto:pick', anchor: { kind: 'element', protoId, x: 0.5, y: 0.5 }, label: 'V-21', rect: { left: 10, top: 10, right: 130, bottom: 42 } };
      const forged = (src) => `(() => { const valid = ${JSON.stringify(PICK)}; const pick = { screen: 's1', protoId: valid.anchor.protoId, anchor: valid.anchor, x: 0.5, y: 0.5, rect: valid.rect };
        const msgs = [valid, { type: 'skillsync-proto:pick', payload: pick }, { type: 'skillsync-proto:pick', data: pick }];
        ${src}; return msgs.length; })()`;
      await p.evaluate(forged(`for (const m of msgs) { window.postMessage(m, '*'); window.postMessage(m, location.origin); }`));
      await sleep(1000);
      const a1 = { composer: await composerOpen() };
      // Esc tylko przy otwartym okienku: Esc przy zamkniętym wyłącza tryb „Komentarze”
      const closeComposer = async () => { if (await composerOpen()) { await p.key('Escape'); await sleep(400); } };
      await closeComposer();
      // (a2) obca ramka tego samego pochodzenia (nie iframe widoku) wysyła „pick” do strony z własnego kontekstu (CDP)
      await p.evaluate(`(() => { const fr = document.createElement('iframe'); fr.id = 'wk-foreign'; fr.name = 'wk-foreign'; fr.style.cssText = 'position:absolute;width:1px;height:1px;left:-10px;top:-10px'; document.body.appendChild(fr);
        fr.contentDocument.open(); fr.contentDocument.write('<!doctype html><title>obca</title><body>obca ramka</body>'); fr.contentDocument.close(); return true; })()`);
      await sleep(400);
      const foreignId = ((await frames()).children.find((fr) => fr.name === 'wk-foreign') || {}).id || null;
      const a2send = foreignId ? await evalIn(foreignId, forged(`for (const m of msgs) { parent.postMessage(m, '*'); parent.postMessage(m, location.origin); }`)) : { noFrame: true };
      await sleep(1000);
      const a2 = { composer: await composerOpen(), sent: a2send.value ?? null, error: a2send.error || a2send.noContext || a2send.noFrame || null };
      await closeComposer();
      const nForged = (await opsList(ctx.base)).length;
      // kontrola dodatnia kształtu: ta sama wiadomość z kontekstu iframe widoku s1 (CDP), więc event.source = ten iframe
      const viewId = await viewFrameOf('s1');
      const shapeSend = viewId ? await evalIn(viewId, `(() => { parent.postMessage(${JSON.stringify(PICK)}, location.origin); return true; })()`) : { noFrame: true };
      const shapeAcceptedFromView = (await p.waitFor(`!!document.querySelector('.anno-composer-pop')`, 3000)).ok;
      await closeComposer();
      const nAfterShape = (await opsList(ctx.base)).length;
      // kontrola dodatnia: prawdziwy klik w element iframe widoku otwiera okienko (test nie przechodzi przez ślepotę harnessu)
      const modeOn = await commentMode(p, true);
      const pt = await pointInFrame(p, 's1', '#wk-input');
      if (pt && !pt.noDoc) await p.click(pt.x, pt.y);
      const realOpens = (await p.waitFor(`!!document.querySelector('.anno-composer-pop')`, 3000)).ok;
      // (b) zapis komentarza mobile idzie przez stronę; potem czekamy na odpytywanie, żeby złapać ewentualne żądania iframe
      const text = `V-21 mobile ${width}`;
      const saved = realOpens ? await compose(p, text) : false;
      await sleep(7000);
      const op = createWith(await opsList(ctx.base), text);
      const tree = await p.send('Page.getFrameTree');
      const viewFrames = []; const walk = (n) => { for (const ch of n.childFrames || []) { if (/[?&]embed=view\b/.test(ch.frame.url || '')) viewFrames.push(ch.frame.id); walk(ch); } }; walk(tree.frameTree);
      const mainId = tree.frameTree.frame.id;
      const api = reqs.filter((r) => /\/_api\//.test(r.url));
      const fromView = api.filter((r) => viewFrames.includes(r.frameId)).map((r) => r.method + ' ' + r.url.replace(/^https?:\/\/[^/]+/, ''));
      const fromMain = api.filter((r) => r.frameId === mainId).length;
      await commentMode(p, false);
      const ok = ready && !!protoId && !a1.composer && !a2.composer && a2.sent != null && nForged === n0 && !!viewId && shapeAcceptedFromView && nAfterShape === n0 && modeOn && realOpens && saved && !!op && op.payload?.view === 'mobile'
        && viewFrames.length >= 2 && fromView.length === 0 && fromMain > 0;
      res.push({ width, ok, ready, protoId, forged: { pageAsSource: a1, foreignFrame: a2, newOps: nForged - n0 }, viewFrameS1: !!viewId, shapeSend: shapeSend.error || shapeSend.noContext || shapeSend.noFrame || 'ok', shapeAcceptedFromView, newOpsAfterShape: nAfterShape - n0, commentModeOn: modeOn, realClickOpens: realOpens, saved, savedView: op?.payload?.view ?? null,
        viewFrames: viewFrames.length, apiFromView: fromView.slice(0, 5), apiFromMain: fromMain });
    } finally { await p.dispose(); }
  }
  return { ok: res.every((r) => r.ok), cells: res };
}, { timeout: 180000 });

// ---------------------------------------------------------------- D246
add('V-22', 'Brak globalnego przełącznika widoku (D246): w pasku i w menu „Więcej” nie ma kontrolki Desktop / Mobile przy 375, 599, 600, 768, 1440; przełączniki lokalne są', async (ctx) => {
  const rows = [];
  for (const who of [null, 'anna']) for (const width of [375, 599, 600, 768, 1440]) {
    const p = await openPage(ctx.c, ctx.base, { width, dark: false, who });
    try {
      const closed = await p.evaluate(VIEW_CONTROLS);
      // menu „Więcej”: otwarte (jeśli widoczne przy tej szerokości), pozycje sprawdzane, potem Esc
      const moreVisible = await p.evaluate(`(() => { const m = ${H.menuBtn}; return !!m && m.getClientRects().length > 0; })()`);
      let opened = null;
      if (moreVisible) { await p.domClick(H.menuBtn, 'menu Więcej'); await sleep(250); opened = await p.evaluate(VIEW_CONTROLS); await p.key('Escape'); await sleep(200); }
      const locals = await p.evaluate(`[${FRAMED.map((sid) => `!!${H.localSwitch(sid)}`).join(',')}]`);
      const menuChecked = !moreVisible || (opened && opened.menusOpen > 0);
      const ok = !closed.noBar && closed.groups.length === 0 && closed.ctrls.length === 0 && (!opened || (opened.groups.length === 0 && opened.ctrls.length === 0 && opened.items.length === 0))
        && menuChecked && locals.every(Boolean);
      rows.push({ who: who || 'anonim', width, ok, closed, moreVisible, opened, menuChecked, locals: locals.every(Boolean) });
    } finally { await p.dispose(); }
  }
  return { ok: rows.every((x) => x.ok), rows };
});

// ---------------------------------------------------------------- D248
// Oglądany ekran nie skacze, gdy iframe widoku nad nim zmienia wysokość (placeholder → treść, wzrost treści, zmniejszenie).
add('V-23', 'Brak skoku strony: górna krawędź oglądanego ekranu nie zmienia się (≤ 2 px), gdy iframe mobile nad nim zmieniają wysokość (placeholder → treść, wzrost, zmniejszenie)', async (ctx) => {
  const ABOVE = ['s1', 's2', 's3']; const VIEWED = 's5'; const res = [];
  for (const width of SIZES) {
    const p = await openPage(ctx.c, ctx.base, { width, dark: false, hash: '#s1' });
    try {
      const held = []; let hold = true;
      // opóźnienie odpowiedzi iframe widoku (jak w V-20), zwolnione dopiero po ustaleniu pozycji oglądanego ekranu (krócej niż 5 s: bez K15)
      await p.intercept([`*embed=view*`], async (ev, api) => { if (hold) { held.push(api); return; } await api.continue(); });
      for (const sid of ABOVE) await p.domClick(H.localBtn(sid, 'Mobile'), 'Mobile ' + sid);
      await sleep(300);
      await scrollTo(p, VIEWED); await sleep(500);
      const top = () => p.evaluate(`document.getElementById('${VIEWED}').getBoundingClientRect().top`);
      const heights = () => p.evaluate(`[${ABOVE.map((sid) => `(() => { const f = ${H.iframe(sid)}; return f ? Math.round(f.getBoundingClientRect().height * 10) / 10 : null; })()`).join(',')}]`);
      // największe odchylenie górnej krawędzi od wartości początkowej w czasie `ms` (próbki co klatkę)
      const watch = (ms, t) => p.evaluate(`(async () => { const el = document.getElementById('${VIEWED}'); const t0 = performance.now(); let dev = 0; while (performance.now() - t0 < ${ms}) { await new Promise((r) => requestAnimationFrame(r)); dev = Math.max(dev, Math.abs(el.getBoundingClientRect().top - ${t})); } return dev; })()`);
      const settle = async (timeout = 7000) => { const t0 = Date.now(); let prev = null; let same = 0;
        while (Date.now() - t0 < timeout) { await sleep(300); const h = JSON.stringify(await heights()); same = h === prev ? same + 1 : 0; prev = h; if (same >= 3) return true; } return false; };
      const phase = async (name, action) => { const tBefore = await top(); const hBefore = await heights(); const w = watch(4500, tBefore);
        await action(); const settled = await settle(); const maxDev = Math.round((await w) * 10) / 10;
        const tAfter = await top(); const hAfter = await heights();
        return { name, topBefore: Math.round(tBefore), topAfter: Math.round(tAfter), delta: Math.round((tAfter - tBefore) * 10) / 10, maxTransientDeviation: maxDev, settled,
          heightsBefore: hBefore, heightsAfter: hAfter, heightDelta: hAfter.map((v, i) => (v == null || hBefore[i] == null ? null : Math.round((v - hBefore[i]) * 10) / 10)) }; };
      const t0 = await top(); const inWindow = t0 >= 0 && t0 < (await p.evaluate('innerHeight'));
      const phases = [];
      phases.push(await phase('placeholder → treść', async () => { hold = false; for (const a of held.splice(0)) await a.continue(); }));
      // wzrost od treści: rozwinięcie ukrytego bloku w iframe s1 (+240 px); potem zmniejszenie z powrotem
      const toggle = (hidden) => p.evaluate(inFrame('s1', `(d) => { const e = d.getElementById('wk-more'); if (e) e.hidden = ${hidden}; return !!e; }`));
      phases.push(await phase('wzrost treści', async () => { await toggle(false); }));
      phases.push(await phase('zmniejszenie treści', async () => { await toggle(true); }));
      const grew = phases.some((x) => x.heightDelta.some((d) => d != null && d >= 200));
      const shrank = phases.some((x) => x.heightDelta.some((d) => d != null && d <= -20));
      const heightsChanged = phases.every((x) => x.heightDelta.some((d) => d != null && Math.abs(d) >= 20));
      // Znany defekt (D249): poprawka skoku cofnięta, więc wymagane zachowanie (|delta| ≤ 2 po każdej fazie) nie jest spełnione.
      // `restOk` = wszystko poza tym warunkiem; `defectObserved` = restOk i jakaś faza ma |delta| > 2. Czerwony (ok=false) również wtedy,
      // gdy defekt nie wystąpi (runner: unexpected PASS = usuń wpis z KNOWN_DEFECTS i przywróć asercję |delta| ≤ 2).
      const restOk = inWindow && heightsChanged && grew && shrank && phases.every((x) => x.settled);
      const defectObserved = restOk && phases.some((x) => Math.abs(x.delta) > 2);
      const ok = restOk && !defectObserved;
      res.push({ width, ok, restOk, defectObserved, inWindow, topStart: Math.round(t0), grew, shrank, heightsChanged, phases });
      await p.interceptOff();
    } finally { await p.dispose(); }
  }
  const restOkAll = res.every((r) => r.restOk);
  return { ok: res.every((r) => r.ok), restOk: restOkAll, defectObserved: restOkAll && res.some((r) => r.defectObserved), cells: res };
}, { timeout: 240000 });

await s.main();
