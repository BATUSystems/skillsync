// Narzędzie biblioteki `skills/prototype/scripts/layout-probe.js` (z SKILLSYNC_TEST_LIBRARY): jedna funkcja `layoutProbe(options)` wstrzykiwana
// do strony prototypu (CDP, Chrome ze środowiska E2E); zwraca obiekt JSON z `ok`. Skrypt sprawdza interfejs (opcje, pola wyniku, sens liczb),
// nie logikę narzędzia. Narzędzie nie ma wiersza poleceń ani kodu wyjścia: błąd wejścia (brak paska, nieznany ekran) to `ok: false` z polem `error`.
import { Suite, sleep, workspace, connect, env, readFileSync } from './lib.mjs';

const suite = new Suite('layout-probe');
let ws; let c; let p;
const url = () => 'file://' + ws.file('plan');
const SRC = () => readFileSync(`${env.library}/skills/prototype/scripts/layout-probe.js`, 'utf8');
const probe = (options = {}) => p.evaluate(`(async () => { ${SRC()}\n return await layoutProbe(${JSON.stringify(options)}); })()`);
const isNum = (x) => typeof x === 'number' && Number.isFinite(x);
const isOkValue = (x) => x === true || x === false || x === null;

suite.setup(async () => { ws = workspace('lp'); c = await connect(); p = await c.page('lp'); await p.size(1920, 1080); await p.go(url()); return {}; });
suite.teardown(async () => { if (c) await c.closeAll(); });

suite.add('LP-01', 'Pomiar domyślny: kształt wyniku', async () => {
  await p.size(1920, 1080); await p.go(url());
  const dom = await p.evaluate(`({ h: document.querySelector('.doc-toolbar').offsetHeight, ih: window.innerHeight, iw: window.innerWidth, screens: document.querySelectorAll('.screen').length, theme: document.documentElement.getAttribute('data-theme'), css: getComputedStyle(document.documentElement).getPropertyValue('--proto-toolbar-height').trim() || null, y: window.scrollY })`);
  const r = await probe();
  const after = await p.evaluate(`window.scrollY`);
  const v = r.viewport || {}; const t = r.toolbar || {};
  const shape = typeof r.ok === 'boolean' && isNum(v.width) && isNum(v.height) && (v.theme === null || typeof v.theme === 'string') && typeof v.panelOpen === 'boolean'
    && isNum(t.height) && isNum(t.bottom) && isNum(t.share) && (t.cssVar === null || typeof t.cssVar === 'string') && isNum(t.screens);
  const sense = v.width === dom.iw && v.height === dom.ih && t.height === dom.h && t.screens === dom.screens && t.share === Math.round((dom.h / dom.ih) * 1000) / 1000 && t.cssVar === dom.css && v.theme === dom.theme;
  const measures = [r.a1, r.a2].every((m) => m && isOkValue(m.ok) && (m.ok !== null || typeof m.skipped === 'string'));
  const noOptional = !('current' in r) && !('target' in r) && !('focusRing' in r) && !('compareSingle' in r);
  return { ok: shape && sense && measures && noOptional && r.ok === true && after === dom.y, shape, sense, measures, noOptional, resultOk: r.ok, scrollRestored: after === dom.y, viewport: v, toolbar: t };
});

suite.add('LP-02', 'Opcja target: ekran pod paskiem', async () => {
  await p.go(url());
  const r = await probe({ target: 's3', a1: false, a2: false });
  const dom = await p.evaluate(`(() => { const h = document.querySelector('#s3 .screen-meta h2').getBoundingClientRect(); return { top: Math.round(h.top), bottom: Math.round(h.bottom), barBottom: Math.round(document.querySelector('.doc-toolbar').getBoundingClientRect().bottom), y: window.scrollY, ih: innerHeight }; })()`);
  const t = r.target || {};
  const shape = typeof t.ok === 'boolean' && t.screen === 's3' && isNum(t.h2Top) && isNum(t.h2Bottom) && isNum(t.toolbarBottom) && (t.ariaCurrent === null || typeof t.ariaCurrent === 'string');
  const sense = Math.abs(t.h2Top - dom.top) <= 1 && Math.abs(t.toolbarBottom - dom.barBottom) <= 1 && dom.y > 0;   // przewinięte do ekranu i nie przywrócone
  const meaning = t.ok === (t.h2Top >= t.toolbarBottom - 0.5 && t.h2Bottom <= dom.ih);
  return { ok: shape && sense && meaning && t.ok === true && r.ok === true && !('a1' in r) && !('a2' in r), shape, sense, meaning, target: t, resultOk: r.ok, dom };
});

suite.add('LP-03', 'Opcja measureCurrent: bieżący ekran', async () => {
  await p.go(url() + '#s2'); await sleep(1500);
  const cur = await p.evaluate(`(document.querySelector('.screen.is-current') || {}).id || null`);
  const sectionAria = await p.evaluate(`(document.querySelector('.screen.is-current') || { getAttribute: () => null }).getAttribute('aria-current')`);   // hak: aria-current="location" także na sekcji bieżącego ekranu
  const y0 = await p.evaluate(`window.scrollY`);
  const r = await probe({ measureCurrent: true, a1: false, a2: false });
  const y1 = await p.evaluate(`window.scrollY`);
  const k = r.current || {};
  const shape = typeof k.ok === 'boolean' && typeof k.screen === 'string' && isNum(k.h2Top) && isNum(k.h2Bottom) && isNum(k.toolbarBottom);
  return { ok: shape && k.screen === cur && k.ariaCurrent === 'location' && sectionAria === 'location' && k.ok === true && r.ok === true && y0 === y1, shape, domCurrent: cur, sectionAriaCurrent: sectionAria, current: k, scrolled: y0 !== y1, resultOk: r.ok };
});

suite.add('LP-04', 'Opcja focusRing i focusMargin', async () => {
  await p.size(1920, 1080); await p.go(url());
  const r = await probe({ focusRing: true, a1: false, a2: false });
  const buttons = await p.evaluate(`[...document.querySelectorAll('.proto-toolbar button')].filter((b) => b.getBoundingClientRect().width > 0 && b.getBoundingClientRect().height > 0).length`);
  const f = r.focusRing || {};
  const entryOk = (b) => typeof b.ok === 'boolean' && typeof b.label === 'string' && b.space && ['left', 'right', 'top', 'bottom'].every((s) => isNum(b.space[s]));
  const shape = typeof f.ok === 'boolean' && f.margin === 4 && Array.isArray(f.buttons) && f.buttons.length === buttons && buttons > 0 && f.buttons.every(entryOk);
  const meaning = f.ok === f.buttons.every((b) => b.ok) && f.buttons.every((b) => b.ok === Object.values(b.space).every((x) => x >= f.margin));
  // kontrola: ogromny wymagany margines nie może przejść (pomiar potrafi paść)
  const strict = await probe({ focusRing: true, focusMargin: 1000, a1: false, a2: false });
  const custom = (strict.focusRing || {});
  return { ok: shape && meaning && f.ok === true && custom.margin === 1000 && custom.ok === false && strict.ok === false, shape, meaning, margin: f.margin, buttons: f.buttons && f.buttons.length, domButtons: buttons, strictMargin: custom.margin, strictOk: custom.ok, strictResultOk: strict.ok };
});

suite.add('LP-05', 'Opcja compareSingle: wysokość paska z jednym ekranem', async () => {
  await p.go(url());
  const r = await probe({ compareSingle: true, a1: false, a2: false });
  const dom = await p.evaluate(`({ screens: document.querySelectorAll('.screen').length, links: document.querySelectorAll('.screen-nav a').length })`);
  const k = r.compareSingle || {};
  const side = (x) => !!x && isNum(x.height) && isNum(x.screens) && isNum(x.links);
  const shape = typeof k.ok === 'boolean' && side(k.before) && side(k.after) && isNum(k.diff) && typeof k.note === 'string';
  const sense = k.before.screens === 28 && k.after.screens === 1 && dom.screens === 1 && k.diff === k.after.height - k.before.height && k.ok === (k.before.height === k.after.height) && k.after.links < k.before.links;
  return { ok: shape && sense && k.ok === true && r.ok === true, shape, sense, compareSingle: k, resultOk: r.ok, domAfter: dom };
});

suite.add('LP-06', 'Błędne wejście: ok false i komunikat', async () => {
  // strona bez paska prototypu: ok:false z opisem, bez wyjątku
  await p.go('data:text/html,<p>nie prototyp</p>'); await sleep(300);
  const none = await probe();
  // nieznany ekran: ok:false w pomiarze i w wyniku, komunikat zawiera nazwę
  await p.go(url());
  const bad = await probe({ target: 's999', a1: false, a2: false });
  const t = bad.target || {};
  // wyłączone pomiary domyślne nie występują w wyniku
  const off = await probe({ a1: false, a2: false });
  return { ok: none.ok === false && typeof none.error === 'string' && /doc-toolbar/.test(none.error) && bad.ok === false && t.ok === false && /s999/.test(t.error || '') && off.ok === true && !('a1' in off) && !('a2' in off),
    noToolbar: none, unknownTarget: t, unknownResultOk: bad.ok, optionsOffKeys: Object.keys(off) };
});

await suite.main();
