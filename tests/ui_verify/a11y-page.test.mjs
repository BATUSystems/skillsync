// Tests of the pure part of the browser adapter (a11y-page.mjs, a11y-login.mjs) and of its orchestration with fake
// Playwright objects (call order, origin abort, reload after a mouse click, no splicing of selectors). No browser needed.
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { pathToFileURL } from 'node:url';
import {
  A11yPageError, CHUNK_PX, FOCUS_PAD_PX, HIDE_TEXT_CSS, PAGE_SCRIPT, classifyContrastRaw, consoleFindings, contrastFinding, createA11yRunner, createBlockedTracker,
  createConsoleCollector, diffPixels, displayUrl, focusClip, gatherPixels, gridPoints, isFavicon, keyboardFinding, makeRouteHandler, pageScript, planClips,
  redactUrlQueries, routeDecision, selectorCollisions, selectorOf, settleVacuous, stripQuery, tabRecordingDone, targetFindings, toCandidate, toContrastSample, toTabStep,
} from '../../library/skills/ui-verify/scripts/lib/a11y-page.mjs';
import { assembleReport } from '../../library/skills/ui-verify/scripts/a11y.mjs';
import { createMasker } from '../../library/skills/ui-verify/scripts/lib/out.mjs';
import { A11yLoginError, runLoginSteps } from '../../library/skills/ui-verify/scripts/lib/a11y-login.mjs';
import { measureSample } from '../../library/skills/ui-verify/scripts/lib/a11y-contrast.mjs';
import { validateA11yConfig } from '../../library/skills/ui-verify/scripts/lib/a11y-config.mjs';
import { makePixelmatchFlagger } from '../../library/skills/ui-verify/scripts/lib/runtime.mjs';

// A real directory: file: URLs are checked with realpath (symlinks), so the configuration directory must exist.
const CONFIG_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-a11y-page-'));
after(() => fs.rmSync(CONFIG_DIR, { recursive: true, force: true }));
const plain = (v) => JSON.parse(JSON.stringify(v));

// ---------- the page script as a constant text ----------

test('skrypt strony: jeden staly tekst, kompiluje sie, nie zawiera danych konfiguracji', () => {
  assert.equal(typeof PAGE_SCRIPT, 'string');
  assert.equal(PAGE_SCRIPT, String(pageScript));
  assert.doesNotThrow(() => new vm.Script(`(${PAGE_SCRIPT})`));
  for (const needle of ['</script>', 'login', 'valueEnv', 'secretEnv']) assert.ok(!PAGE_SCRIPT.includes(needle), needle);
  assert.ok(HIDE_TEXT_CSS.includes('-webkit-text-fill-color: transparent !important') && HIDE_TEXT_CSS.includes('::placeholder'));
});

function runInFakeDom(args, dom) {
  const sandbox = { document: dom.document, getComputedStyle: dom.getComputedStyle || (() => ({ visibility: 'visible' })) };
  const fn = vm.runInNewContext(`(${PAGE_SCRIPT})`, sandbox);
  return fn(args).then(plain);
}

test('skrypt strony: selektor trafia do querySelectorAll dokladnie jako argument (takze z cudzyslowami), zly selektor to false', async () => {
  const tricky = 'main[title="a\'\\"b"], main';
  const nasty = 'a[title="x"], </script><b>"\'';
  const seen = [];
  const dom = { document: { querySelectorAll(sel) { seen.push(sel); if (sel.includes('<')) throw new SyntaxError('bad selector'); return [{ textContent: 'Hello world', getBoundingClientRect: () => ({ width: 5, height: 5 }) }]; } } };
  assert.deepEqual(await runInFakeDom({ op: 'validate', selectors: ['main', nasty, 42, tricky] }, dom), [true, false, false, true]);
  assert.deepEqual(seen, ['main', nasty, tricky], 'the page received the selector strings unchanged');
  assert.equal(await runInFakeDom({ op: 'assertOk', selector: nasty }, dom), false);
  assert.equal(await runInFakeDom({ op: 'assertOk', selector: tricky, text: 'world' }, dom), true);
  assert.equal(await runInFakeDom({ op: 'assertOk', selector: 'p', text: 'absent' }, dom), false);
  assert.equal(await runInFakeDom({ op: 'assertOk', selector: 'p' }, dom), true);
  assert.equal(await runInFakeDom({ op: 'visible', selector: 'p' }, dom), true);
  await assert.rejects(runInFakeDom({ op: 'nope' }, dom), /unknown page operation/);
});

// ---------- contrast: classification and samples ----------

const rawItem = (over = {}) => ({ kind: 'text', id: 'x', idUnique: true, chain: [], fontSizePx: 16, fontWeight: 400, color: { r: 0, g: 0, b: 0, a: 1 }, opacity: 1, hidden: false, inactive: false, flags: {}, rects: [[0, 0, 4, 2]], ...over });

test('kontrast: kolejnosc decyzji, EXEMPT przed UNVERIFIED, powody pomiarowe', () => {
  assert.deepEqual(classifyContrastRaw(rawItem()), {});
  assert.equal(classifyContrastRaw(rawItem({ hidden: true })).exempt, 'not-rendered');
  assert.equal(classifyContrastRaw(rawItem({ rects: [] })).exempt, 'not-rendered');
  assert.equal(classifyContrastRaw(rawItem({ inactive: true, flags: { filter: true } })).exempt, 'inactive');
  const want = [['filter', 'filter-or-blend'], ['clipText', 'background-clip-text'], ['group', 'opacity-group-with-paint'], ['media', 'media-background'],
    ['important', 'inline-important-color'], ['autofill', 'autofill']];
  for (const [flag, reason] of want) assert.equal(classifyContrastRaw(rawItem({ flags: { [flag]: true } })).unverified, reason, flag);
  assert.equal(classifyContrastRaw(rawItem({ color: null })).unverified, 'color-unconvertible');
  assert.equal(classifyContrastRaw(rawItem({ flags: { hit: 'occluded' } })).unverified, 'occluded');
  assert.equal(classifyContrastRaw(rawItem({ flags: { hit: 'clipped' } })).unverified, 'clipped');
  assert.equal(classifyContrastRaw(rawItem({ flags: { filter: true, group: true } })).unverified, 'filter-or-blend');
});

test('kontrast: raw -> probka -> werdykt (kolor z alfa i opacity nad pikselem tla)', () => {
  const white = Array.from({ length: 4 }, () => [255, 255, 255]);
  const pass = contrastFinding(measureSample(toContrastSample(rawItem({ color: { r: 117, g: 117, b: 117, a: 1 } }), {}, white)));
  assert.equal(pass.verdict, 'PASS');
  assert.ok(Math.abs(pass.measured.ratio - 4.61) < 0.01 && pass.required === 4.5 && pass.rule === 'contrast');
  const faded = contrastFinding(measureSample(toContrastSample(rawItem({ opacity: 0.5 }), {}, white)));
  assert.equal(faded.verdict, 'FAIL'); // black at 50% alpha over white is about 3.9:1
  const layer = contrastFinding(measureSample(toContrastSample(rawItem({ color: { r: 117, g: 117, b: 117, a: 1 }, kind: 'placeholder' }), {}, white)));
  assert.equal(layer.selector, '#x::placeholder');
  assert.deepEqual(contrastFinding(measureSample(toContrastSample(rawItem({ hidden: true }), { exempt: 'not-rendered' }))), { rule: 'contrast', selector: '#x', verdict: 'EXEMPT', reason: 'not-rendered' });
  assert.equal(contrastFinding(measureSample(toContrastSample(rawItem(), { unverified: 'autofill' }))).reason, 'autofill');
  assert.equal(contrastFinding(measureSample(toContrastSample(rawItem(), {}, []))).reason, 'no-samples');
  const s = toContrastSample(rawItem({ fontSizePx: Number.NaN, opacity: 7 }), { exempt: 'inactive' });
  assert.equal(s.fontSizePx, 16);
  assert.equal(s.opacity, 1);
});

test('selektor raportu: #id tylko gdy unikalny i nie dynamiczny, inaczej sciezka; ciecie do 400', () => {
  assert.equal(selectorOf({ id: 'brand', idUnique: true, chain: [] }), '#brand');
  assert.equal(selectorOf({ id: 'brand', idUnique: false, chain: [{ tag: 'HTML' }, { tag: 'body' }, { tag: 'div', nth: 2 }] }), 'html > body > div:nth-of-type(2)');
  assert.equal(selectorOf({ id: ':r1:', idUnique: true, chain: [{ tag: 'p', nth: 1 }] }), 'p:nth-of-type(1)');
  assert.ok(selectorOf({ id: '', chain: Array.from({ length: 64 }, () => ({ tag: 'section', nth: 1 })) }).length <= 400);
});

test('siatka probek: do 2000 punktow na element, kazdy prostokat ma co najmniej srodek', () => {
  const pts = gridPoints([[0, 0, 400, 300]]);
  assert.ok(pts.length / 2 <= 2000 && pts.length / 2 > 500);
  assert.deepEqual(gridPoints([[10, 20, 0.5, 0.5]]), [10, 20]);
  assert.deepEqual(gridPoints([[0, 0, 3, 1]], 100), [0, 0, 1, 0, 2, 0]);
  assert.deepEqual(gridPoints([]), []);
  const small = gridPoints([[5, 5, 2, 2], [5, 50, 2, 2]]);
  assert.equal(small.length / 2, 8);
});

test('plan zrzutu: jeden zrzut do 16000 px, klipy po 4000, ponad limity page-too-tall', () => {
  assert.deepEqual(planClips({ width: 800, height: 1200 }), { ok: true, chunked: false, clips: [{ x: 0, y: 0, width: 800, height: 1200 }] });
  assert.equal(planClips({ width: 800, height: 16000 }).chunked, false);
  const tall = planClips({ width: 800, height: 16001 });
  assert.equal(tall.chunked, true);
  assert.equal(tall.clips.length, 5);
  assert.deepEqual(tall.clips[4], { x: 0, y: 16000, width: 800, height: 1 });
  assert.equal(tall.clips[0].height, CHUNK_PX);
  assert.deepEqual(planClips({ width: 800, height: 64001 }), { ok: false, reason: 'page-too-tall' });
  assert.equal(planClips({ width: 4001, height: 100 }).ok, false);
  assert.equal(planClips({ width: 3000, height: 14000 }).ok, false); // 42 million pixels
  assert.equal(planClips({ width: 0, height: 10 }).ok, false);
});

test('zbieranie pikseli z klipu: przesuniecie, granice obrazu', () => {
  const img = { width: 2, height: 2, data: Uint8ClampedArray.from([1, 1, 1, 255, 2, 2, 2, 255, 3, 3, 3, 255, 4, 4, 4, 255]) };
  const out = [];
  gatherPixels(img, { x: 10, y: 100 }, [10, 100, 11, 101, 12, 100, 9, 100, 10, 102], out);
  assert.deepEqual(out, [[1, 1, 1], [4, 4, 4]]);
});

test('sciezki: plik w katalogu konfiguracji staje sie <config-dir>, reszta bez zmian', () => {
  const url = pathToFileURL(path.join(CONFIG_DIR, 'sub', 'p.html')).href;
  assert.equal(displayUrl(url, CONFIG_DIR), '<config-dir>/sub/p.html');
  assert.equal(displayUrl('http://localhost:3000/a', CONFIG_DIR), 'http://localhost:3000/a');
});

// ---------- targets, Tab, focus ----------

test('cele: mapowanie surowych danych na wyniki target-size (wyjatki i para label/input)', () => {
  const t = (over) => ({ id: '', chain: [{ tag: 'a', nth: 1 }], box: { x: 0, y: 0, width: 100, height: 40 }, display: 'block', ...over });
  const big = t({ id: 'big', idUnique: true });
  const small = t({ id: 'tiny', idUnique: true, box: { x: 300, y: 0, width: 16, height: 16 } });
  const cb = t({ id: 'cb', idUnique: true, box: { x: 0, y: 200, width: 13, height: 13 }, labelBox: { width: 120, height: 30 }, pair: 'k9' });
  const lab = t({ id: 'lab', idUnique: true, box: { x: 16, y: 200, width: 120, height: 30 }, pair: 'k9' });
  const r = targetFindings([big, small, cb, lab]);
  assert.deepEqual(r.map((f) => f.verdict), ['PASS', 'PASS', 'PASS', 'PASS']);
  assert.equal(r[1].note, 'spacing');
  assert.equal(r[2].note, 'equivalent: label');
  const near = targetFindings([small, t({ id: 'near', idUnique: true, box: { x: 318, y: 0, width: 100, height: 40 } })]);
  assert.equal(near[0].verdict, 'FAIL');
  assert.equal(near[0].required, 24);
  assert.deepEqual(near[0].measured, { widthPx: 16, heightPx: 16 });
  assert.equal(targetFindings([t({ id: 'i', idUnique: true, display: 'inline', parentHasText: true, box: { x: 0, y: 0, width: 10, height: 10 } }), big])[0].verdict, 'EXEMPT');
});

test('kandydaci i kroki Tab z surowych danych', () => {
  const c = toCandidate({ key: 'k1', id: 'a', idUnique: true, chain: [], tabindex: -1, disabled: false, radioGroup: 'doc|g', radioChecked: true });
  assert.deepEqual(c, { key: 'k1', selector: '#a', tabindex: -1, disabled: false, inert: false, inClosedDetails: false, hidden: false, inModal: false, radioGroup: 'doc|g', radioChecked: true });
  assert.deepEqual(toTabStep({ kind: 'body' }), { key: 'body', kind: 'body', selector: 'body' });
  assert.deepEqual(toTabStep({ kind: 'outside' }).kind, 'outside');
  const s = toTabStep({ kind: 'element', key: 'k2', id: 'b', idUnique: true, chain: [], tabindex: 3, box: { x: 1, y: 2, width: 3, height: 4 }, inDocument: false, inModal: true });
  assert.deepEqual(s, { key: 'k2', kind: 'element', selector: '#b', tabindex: 3, box: { x: 1, y: 2, width: 3, height: 4 }, inDocument: false, inModal: true });
});

test('koniec sekwencji Tab: wrap, body, outside, trap, cykl i limit zatrzymuja nagrywanie', () => {
  const step = (key) => ({ key, kind: 'element', selector: `#${key}`, tabindex: null, box: null, inDocument: true, inModal: false });
  assert.equal(tabRecordingDone([step('a'), step('b')], 500), false);
  assert.equal(tabRecordingDone([step('a'), step('b'), step('a')], 500), true); // wrapped
  assert.equal(tabRecordingDone([step('a'), { key: 'body', kind: 'body', selector: 'body' }], 500), true);
  assert.equal(tabRecordingDone([step('a'), { key: 'outside', kind: 'outside', selector: 'outside' }], 500), true);
  assert.equal(tabRecordingDone([step('a'), step('b'), step('b'), step('b')], 500), true); // trap
  assert.equal(tabRecordingDone([step('a'), step('b'), step('c'), step('b')], 500), true); // cycle
  assert.equal(tabRecordingDone([step('a'), step('b'), step('b')], 500), false);
  assert.equal(tabRecordingDone([step('a'), step('b')], 2), true); // limit
});

test('klip fokusu: element +8 px przyciety do okna, pusty klip to null', () => {
  const vp = { width: 1280, height: 800 };
  assert.deepEqual(focusClip({ x: 20, y: 30, width: 100, height: 20 }, vp), { x: 12, y: 22, width: 116, height: 36 });
  assert.equal(FOCUS_PAD_PX, 8);
  assert.deepEqual(focusClip({ x: 2, y: 2, width: 10.2, height: 10 }, vp), { x: 0, y: 0, width: 21, height: 20 });
  assert.deepEqual(focusClip({ x: 1200, y: 780, width: 200, height: 100 }, vp), { x: 1192, y: 772, width: 88, height: 28 });
  assert.equal(focusClip({ x: 3000, y: 10, width: 10, height: 10 }, vp), null);
  assert.equal(focusClip(null, vp), null);
});

const fakePixelmatch = (a, b, out) => {
  for (let i = 0; i < a.length; i += 4) if (a[i] !== b[i]) { out[i] = 255; out[i + 1] = 0; out[i + 2] = 0; out[i + 3] = 255; }
};
const flat = (w, h, v) => ({ width: w, height: h, data: new Uint8ClampedArray(w * h * 4).fill(v) });

test('roznica pikseli: licznik z flaggera, rozne rozmiary to null, werdykt fokusu', () => {
  const flagger = makePixelmatchFlagger(fakePixelmatch, { threshold: 0.1 });
  assert.equal(diffPixels(flagger, flat(3, 3, 255), flat(3, 3, 0)), 9);
  assert.equal(diffPixels(flagger, flat(3, 3, 7), flat(3, 3, 7)), 0);
  assert.equal(diffPixels(flagger, flat(3, 3, 7), flat(3, 4, 7)), null);
  const info = { id: 'go', idUnique: true, chain: [], focusVisible: true, signals: { outlineStyle: 'none' } };
  const fail = keyboardFinding(info, 0, 4);
  assert.deepEqual([fail.rule, fail.verdict, fail.selector, fail.required], ['focus-keyboard-indicator', 'FAIL', '#go', 4]);
  assert.deepEqual(fail.measured, { diffPx: 0, focusVisible: true, signals: { outlineStyle: 'none' } });
  assert.equal(keyboardFinding(info, 4, 4).verdict, 'PASS');
  assert.deepEqual([keyboardFinding(info, null, 4).verdict, keyboardFinding(info, null, 4).reason], ['UNVERIFIED', 'clipped']);
});

// ---------- console, origins ----------

test('adresy: favicon, zapytania i tokeny w komunikatach', () => {
  assert.equal(stripQuery('https://a.test/x?token=abc#frag'), 'https://a.test/x');
  assert.ok(isFavicon('http://localhost:3000/favicon.ico?v=2') && !isFavicon('http://localhost/sub/favicon.ico.png') && !isFavicon('http://localhost/a'));
  const text = redactUrlQueries('Refused https://api.test/v1/me?access_token=SECRETVALUE1 and file:///x/y.js#key=abc');
  assert.ok(!text.includes('SECRETVALUE1') && !text.includes('key=abc') && text.includes('https://api.test/v1/me?<redacted>'));
});

test('decyzja o originie: lokalne i data/blob dozwolone, zewnetrzne odrzucone (host bez zapytania)', () => {
  const o = { configDir: CONFIG_DIR, allowOrigins: ['cdn.example.test'] };
  assert.equal(routeDecision('http://localhost:3000/x', o).allow, true);
  assert.equal(routeDecision('http://127.0.0.1/x', o).allow, true);
  assert.equal(routeDecision('http://[::1]:8080/x', o).allow, true);
  assert.equal(routeDecision(pathToFileURL(path.join(CONFIG_DIR, 'a.css')).href, o).allow, true);
  assert.equal(routeDecision(pathToFileURL(path.resolve(path.sep, 'elsewhere', 'a.css')).href, o).allow, false);
  assert.equal(routeDecision('data:image/png;base64,AAAA', o).allow, true);
  assert.equal(routeDecision('blob:http://localhost/1', o).allow, true);
  assert.equal(routeDecision('https://cdn.example.test/lib.js', o).allow, true);
  const bad = routeDecision('https://tracker.example.org/p?token=abc', o);
  assert.deepEqual(bad, { allow: false, host: 'tracker.example.org' });
});

const msg = (type, text, url = '') => ({ type: () => type, text: () => text, location: () => ({ url }) });
const req = (method, url, errorText) => ({ method: () => method, url: () => url, failure: () => (errorText ? { errorText } : null) });
const res = (status, url, method = 'GET') => ({ status: () => status, url: () => url, request: () => ({ method: () => method }) });

test('kolektor konsoli: bledy, requestfailed, 4xx/5xx; favicon i wlasny abort pomijane; dedupe; ciecie', () => {
  const c = createConsoleCollector({ maxMessages: 4 });
  c.onConsole(msg('error', 'Boom at https://x.test/a?token=SECRETVALUE2'));
  c.onConsole(msg('error', 'Boom at https://x.test/a?token=SECRETVALUE2'));
  c.onConsole(msg('warning', 'be careful'));
  c.onConsole(msg('log', 'ignored'));
  c.onConsole(msg('error', 'Failed to load resource: the server responded with a status of 404 ()', 'http://localhost/api'));
  c.onConsole(msg('error', 'noise', 'http://localhost/favicon.ico'));
  c.onPageError(new Error('uncaught'));
  c.onRequestFailed(req('GET', 'http://localhost/favicon.ico', 'net::ERR_FAILED'));
  c.onRequestFailed(req('GET', 'https://ext.test/p.js?k=v', 'net::ERR_BLOCKED_BY_CLIENT'));
  c.onRequestFailed(req('GET', 'http://localhost/nav', 'net::ERR_ABORTED'));
  c.onRequestFailed(req('GET', 'http://localhost/img.png?sig=zzz', 'net::ERR_CONNECTION_REFUSED'));
  c.onResponse(res(404, 'http://localhost/favicon.ico'));
  c.onResponse(res(200, 'http://localhost/ok'));
  c.onResponse(res(500, 'http://localhost/api?x=1', 'POST'));
  c.onResponse(res(503, 'http://localhost/more'));
  const { list, truncated } = c.result();
  assert.equal(truncated, true);
  assert.deepEqual(list.map((e) => e.message), [
    'console.error: Boom at https://x.test/a?<redacted>', 'console.warning: be careful', 'pageerror: uncaught', 'requestfailed: GET http://localhost/img.png (net::ERR_CONNECTION_REFUSED)',
  ]);
  assert.equal(list[0].count, 2);
  const f = consoleFindings({ list, truncated });
  assert.deepEqual(f.map((x) => x.verdict), ['FAIL', 'INFO', 'FAIL', 'FAIL', 'UNVERIFIED']);
  assert.equal(f[4].reason, 'truncated');
  assert.deepEqual(f[0].measured, { count: 2 });
  assert.ok(!JSON.stringify(f).includes('SECRETVALUE2'));
  const ok = consoleFindings(createConsoleCollector().result());
  assert.deepEqual(ok.map((x) => x.verdict), ['PASS']);
  const warnOnly = createConsoleCollector();
  warnOnly.onConsole(msg('warning', 'deprecated thing'));
  assert.deepEqual(consoleFindings(warnOnly.result()).map((x) => x.verdict), ['INFO', 'PASS']);
  const http = createConsoleCollector();
  http.onResponse(res(404, 'http://localhost/missing'));
  assert.equal(http.result().list[0].message, 'http 404: GET http://localhost/missing');
});

test('licznik zablokowanych originow: ostrzezenie blocked-origin z hostami', () => {
  const b = createBlockedTracker();
  assert.equal(b.take('home'), null);
  for (let i = 0; i < 7; i++) b.add(`h${i}.test`);
  const w = b.take('home');
  assert.equal(w.code, 'blocked-origin');
  assert.match(w.message, /blocked 7 request\(s\)/);
  b.reset();
  assert.equal(b.take('home'), null);
});

// ---------- login ----------

const loginPage = (failOn = null) => {
  const calls = [];
  const loc = (sel) => ({ first: () => ({
    async fill(value, opts) { calls.push(['fill', sel, value, opts.timeout]); if (failOn === 'fill') throw new Error(`cannot type ${value} into page text`); },
    async click(opts) { calls.push(['click', sel, opts.timeout]); if (failOn === 'click') throw new Error('page says: secret banner'); },
  }) });
  return { calls, locator: loc, async waitForLoadState(state) { calls.push(['load', state]); } };
};
const LOGIN = { steps: [{ fill: { selector: '#u', valueEnv: 'UI_VERIFY_A_USER' } }, { fill: { selector: '#p', valueEnv: 'UI_VERIFY_A_PASS' } }, { click: '#go' }] };
const secret = ['fake', 'pw', String(process.pid), 'zzzz'].join('-');

test('logowanie: kroki fill z env i click, na koncu czekanie na load', async () => {
  const page = loginPage();
  await runLoginSteps({ page, login: LOGIN, env: { UI_VERIFY_A_USER: 'alice', UI_VERIFY_A_PASS: secret } });
  assert.deepEqual(page.calls, [['fill', '#u', 'alice', 10000], ['fill', '#p', secret, 10000], ['click', '#go', 10000], ['load', 'load']]);
});

test('logowanie: blad nie cytuje wartosci ani tekstu strony, brak zmiennej to blad', async () => {
  for (const mode of ['fill', 'click']) {
    const err = await runLoginSteps({ page: loginPage(mode), login: LOGIN, env: { UI_VERIFY_A_USER: 'alice', UI_VERIFY_A_PASS: secret } }).catch((e) => e);
    assert.ok(err instanceof A11yLoginError, mode);
    assert.ok(!err.message.includes(secret) && !err.message.includes('banner'), err.message);
    assert.match(err.message, /login step \d \((fill|click)\) failed/);
  }
  const missing = await runLoginSteps({ page: loginPage(), login: LOGIN, env: { UI_VERIFY_A_USER: 'alice' } }).catch((e) => e);
  assert.match(missing.message, /login step 2 \(fill\) failed/);
});

// ---------- orchestration with fake Playwright objects ----------

// fill of a fake PNG: the fake PNG decoder reads {w, h, fill} and builds a flat RGBA image of that colour.
const FakePNG = { sync: { read: (buf) => { const { w, h, fill } = JSON.parse(buf.toString()); return { width: w, height: h, data: new Uint8Array(w * h * 4).fill(fill) }; } } };
const imaging = { PNG: FakePNG, pixelmatch: fakePixelmatch };

function makeEnv({ pageHtml = 'page.html', focus, checks, assert: assertion = { selector: 'main' }, login, secretEnv } = {}) {
  const cfg = { version: 1, pages: [{ id: 'home', url: pageHtml, ...(assertion ? { assert: assertion } : {}), ...(checks ? { checks } : {}) }], ...(focus ? { focus } : {}), ...(login ? { login } : {}), ...(secretEnv ? { secretEnv } : {}) };
  const v = validateA11yConfig(cfg, { configDir: CONFIG_DIR });
  assert.ok(v.ok, v.errors && v.errors.join('; '));
  return v.config;
}

// A scripted fake page. `ops` maps a page operation to a function (args, state) => value; `shot(opts, state)` gives the fill of a screenshot.
function fakeBrowser({ ops, shot, doc = { width: 10, height: 10 }, withPopup = false, assertPasses = true }) {
  const calls = [];
  const state = { url: '', blurred: false, clicked: false, handlers: {}, routeHandler: null, popupClosed: false, closedPages: 0, closedContext: false, contextOptions: null, routePattern: null };
  const popup = { async close() { state.popupClosed = true; } };
  const page = {
    on(ev, fn) { state.handlers[ev] = fn; },
    url: () => state.url,
    async goto(url, opts) { calls.push(['goto', url, opts.timeout]); state.url = url; state.clicked = false; state.blurred = false; },
    async evaluate(fn, args) {
      calls.push(['evaluate', args.op, fn === pageScript, args]);
      if (args.op === 'blur') state.blurred = true;
      if (args.op === 'refocus') state.blurred = false;
      const handler = ops[args.op];
      if (!handler) throw new Error(`no fake for ${args.op}`);
      return handler(args, state, calls);
    },
    async waitForFunction(fn, args, opts) {
      calls.push(['waitFor', args.op, fn === pageScript, opts.timeout]);
      if (args.op === 'assertOk' && !assertPasses) throw new Error('timeout');
    },
    keyboard: { async press(key) { calls.push(['press', key]); } },
    mouse: {
      async click(x, y) { calls.push(['click', x, y]); state.clicked = true; },
      async move(x, y) { calls.push(['move', x, y]); },
    },
    async screenshot(opts) {
      calls.push(['screenshot', opts]);
      const size = opts.clip ? { w: opts.clip.width, h: opts.clip.height } : { w: doc.width, h: doc.height };
      return Buffer.from(JSON.stringify({ ...size, fill: shot(opts, state) }));
    },
    async close() { state.closedPages += 1; },
  };
  const context = {
    setDefaultTimeout() {},
    async route(pattern, handler) { state.routePattern = pattern; state.routeHandler = handler; },
    async newPage() { calls.push(['newPage']); return page; },
    pages: () => (withPopup ? [page, popup] : [page]),
    async close() { state.closedContext = true; },
  };
  const browser = { async newContext(opts) { state.contextOptions = opts; return context; } };
  return { browser, page, calls, state };
}

const WHITE = 255;
const BLACK = 0;
const el = (key, id, x, y) => ({ kind: 'element', key, id, idUnique: true, chain: [], tabindex: null, box: { x, y, width: 30, height: 30 }, vr: { x, y, width: 30, height: 30 }, inDocument: true, inModal: false, focusVisible: true, signals: {} });
const target = (key, id, x, y) => ({ key, id, idUnique: true, chain: [], box: { x, y, width: 100, height: 40 }, display: 'block', parentHasText: false, userAgent: false, labelBox: null, tabindex: null, disabled: false, inert: false, inClosedDetails: false, inModal: false, radioGroup: null, radioChecked: false });

test('adapter: kolejnosc wywolan, jeden zrzut z ukrytym tekstem, fokus z blur/refocus, konsola, abort originow', async () => {
  const config = makeEnv({ checks: ['contrast', 'target-size', 'focus', 'tab-order', 'console'] });
  const tabs = [el('k1', 'a', 20, 20), el('k2', 'b', 20, 120), el('k1', 'a', 20, 20)];
  let tabCall = 0;
  const ops = {
    validate: (a) => a.selectors.map(() => true),
    recheck: () => ({ height: 10, shifted: 0 }),
    collect: (a, state) => {
      state.handlers.console(msg('error', 'boom happened'));
      assert.equal(state.routePattern instanceof RegExp, true);
      return Promise.resolve(state.routeHandler({ request: () => ({ url: () => 'https://tracker.example.org/p?t=1' }), continue: async () => { throw new Error('must not continue'); }, abort: async (code) => { state.abortCode = code; } })).then(() => ({
        frames: 1, dir: 'ltr',
        targets: { truncated: false, items: [target('k1', 'a', 10, 10), target('k2', 'b', 10, 110)] },
        contrast: { truncated: false, items: [rawItem({ id: 'title', rects: [[2, 2, 4, 2]], color: { r: 117, g: 117, b: 117, a: 1 } })] },
      }));
    },
    settle: () => ({ width: 10, height: 10 }),
    resetFocus: () => true,
    active: () => tabs[tabCall++],
    blur: () => true,
    refocus: () => true,
  };
  const shot = (opts, state) => (opts.fullPage ? WHITE : (state.blurred ? WHITE : BLACK));
  const { browser, calls, state } = fakeBrowser({ ops, shot });
  const runner = createA11yRunner({ browser, imaging, config, configDir: CONFIG_DIR });
  const result = await runner.auditPage(config.pages[0]);
  await runner.close();

  assert.deepEqual(state.contextOptions, { viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1, reducedMotion: 'reduce', colorScheme: 'light', serviceWorkers: 'block', acceptDownloads: false });
  assert.equal(state.abortCode, 'blockedbyclient');
  assert.equal(state.closedContext, true);
  assert.equal(state.closedPages, 1);
  const names = calls.map((c) => (c[0] === 'evaluate' ? `op:${c[1]}` : c[0]));
  const at = (n, from = 0) => names.indexOf(n, from);
  assert.deepEqual(names.slice(0, 6), ['newPage', 'goto', 'op:validate', 'waitFor', 'op:settle', 'op:collect']);
  assert.ok(names.indexOf('screenshot') < names.indexOf('op:recheck'), 'layout re-check after the screenshot');
  assert.equal(calls.find((c) => c[0] === 'goto')[2], 60000);
  assert.ok(calls.filter((c) => c[0] === 'evaluate').every((c) => c[2] === true), 'every evaluate uses the fixed page function');
  const full = calls.filter((c) => c[0] === 'screenshot' && c[1].fullPage);
  assert.equal(full.length, 1);
  assert.equal(full[0][1].style, HIDE_TEXT_CSS);
  assert.deepEqual([full[0][1].animations, full[0][1].scale, full[0][1].type, 'clip' in full[0][1]], ['disabled', 'css', 'png', false]);
  // Tab keystrokes, each step: Tab, read focus, screenshot after, blur, screenshot before, refocus
  const firstTab = at('press');
  assert.deepEqual(names.slice(firstTab - 1, firstTab + 7), ['op:resetFocus', 'press', 'op:active', 'screenshot', 'op:blur', 'screenshot', 'op:refocus', 'press']);
  assert.equal(calls.filter((c) => c[0] === 'press').length, 3);
  const focusShots = calls.filter((c) => c[0] === 'screenshot' && !c[1].fullPage);
  assert.equal(focusShots.length, 4); // two new elements, before and after; the wrap to the first element is not measured again
  assert.deepEqual(focusShots[0][1].clip, { x: 12, y: 12, width: 46, height: 46 });
  assert.equal(focusShots[0][1].caret, 'hide');
  assert.equal(focusShots[0][1].animations, 'disabled');

  const byRule = (rule) => result.findings.filter((f) => f.rule === rule);
  assert.equal(result.assertion, 'PASS');
  assert.equal(result.url, '<config-dir>/page.html');
  assert.deepEqual(byRule('contrast').map((f) => [f.selector, f.verdict]), [['#title', 'PASS']]);
  assert.ok(Math.abs(byRule('contrast')[0].measured.ratio - 4.61) < 0.01);
  assert.deepEqual(byRule('target-size').map((f) => f.verdict), ['PASS', 'PASS']);
  assert.deepEqual(byRule('focus-keyboard-indicator').map((f) => [f.selector, f.verdict, f.measured.diffPx]), [['#a', 'PASS', 2116], ['#b', 'PASS', 2116]]);
  assert.deepEqual(byRule('console').map((f) => f.verdict), ['FAIL']);
  assert.deepEqual(byRule('tab-unreachable').map((f) => f.verdict), ['PASS']);
  assert.deepEqual(result.sequence, ['#a', '#b']);
  const codes = result.warnings.map((w) => w.code);
  assert.ok(codes.includes('blocked-origin') && codes.includes('frames-not-audited') && codes.includes('tab-end'), codes.join());
  assert.ok(!JSON.stringify(result.warnings).includes('t=1'));
  assert.ok(at('press') > at('op:recheck'));
});

test('adapter: asercja niespelniona konczy strone bez pomiarow, brak asercji to NONE', async () => {
  const config = makeEnv({});
  const ops = { validate: () => [true], collect: () => { throw new Error('must not measure'); } };
  const a = fakeBrowser({ ops, shot: () => WHITE, assertPasses: false });
  const failed = await createA11yRunner({ browser: a.browser, imaging, config, configDir: CONFIG_DIR }).auditPage(config.pages[0]);
  assert.deepEqual([failed.assertion, failed.findings.length], ['FAIL', 0]);
  const noAssert = makeEnv({ assert: null, checks: ['console'] });
  const b = fakeBrowser({ ops: {}, shot: () => WHITE });
  const none = await createA11yRunner({ browser: b.browser, imaging, config: noAssert, configDir: CONFIG_DIR }).auditPage(noAssert.pages[0]);
  assert.equal(none.assertion, 'NONE');
  assert.deepEqual(none.findings.map((f) => f.verdict), ['PASS']);
  assert.ok(!b.calls.some((c) => c[0] === 'evaluate'), 'no page script call without selectors or measuring checks');
});

test('adapter: zly selektor to A11yPageError z numerem strony, bez cytowania selektora; waitFor jako argument', async () => {
  const nasty = 'main"]</script>\'';
  const config = makeEnv({ assert: { selector: nasty }, checks: ['console'] });
  const bad = fakeBrowser({ ops: { validate: () => [false] }, shot: () => WHITE });
  const err = await createA11yRunner({ browser: bad.browser, imaging, config, configDir: CONFIG_DIR }).auditPage(config.pages[0]).catch((e) => e);
  assert.ok(err instanceof A11yPageError);
  assert.match(err.message, /page "home": invalid selector in assert/);
  assert.ok(!err.message.includes('script'));
  const validateCall = bad.calls.find((c) => c[0] === 'evaluate' && c[1] === 'validate');
  assert.deepEqual(validateCall[3], { op: 'validate', selectors: [nasty] }, 'selector travels as an argument');

  const withWait = makeEnv({ checks: ['console'] });
  withWait.pages[0].waitFor = '#ready';
  const ok = fakeBrowser({ ops: { validate: (a) => a.selectors.map(() => true) }, shot: () => WHITE });
  await createA11yRunner({ browser: ok.browser, imaging, config: withWait, configDir: CONFIG_DIR }).auditPage(withWait.pages[0]);
  const names = ok.calls.map((c) => c[0]);
  assert.ok(names.indexOf('waitFor') > names.indexOf('goto'));
  assert.deepEqual(ok.calls.find((c) => c[0] === 'waitFor').slice(1), ['visible', true, 10000]);
  assert.ok(ok.calls.some((c) => c[0] === 'waitFor' && c[1] === 'assertOk'), 'the assertion is polled, not checked once');
});

test('adapter: strona wyzsza niz 16000 px dzielona na klipy 4000 px, piksele z wlasciwego klipu', async () => {
  const config = makeEnv({ checks: ['contrast'] });
  const ops = {
    validate: () => [true],
    collect: () => ({ frames: 0, dir: 'ltr', contrast: { truncated: false, items: [rawItem({ id: 'deep', rects: [[2, 4500, 4, 2]] })] } }),
    settle: () => ({ width: 20, height: 20000 }),
    recheck: () => ({ height: 20000, shifted: 0 }),
  };
  const shot = (opts) => (opts.clip ? 50 + opts.clip.y / CHUNK_PX : 0);
  const { browser, calls } = fakeBrowser({ ops, shot, doc: { width: 20, height: 20000 } });
  const result = await createA11yRunner({ browser, imaging, config, configDir: CONFIG_DIR }).auditPage(config.pages[0]);
  const clips = calls.filter((c) => c[0] === 'screenshot').map((c) => c[1].clip);
  assert.deepEqual(clips.map((c) => c.y), [0, 4000, 8000, 12000, 16000]);
  assert.ok(calls.filter((c) => c[0] === 'screenshot').every((c) => c[1].fullPage && c[1].style === HIDE_TEXT_CSS));
  const f = result.findings[0];
  assert.deepEqual(f.measured.effectiveBackground, { r: 51, g: 51, b: 51 }); // clip 1 starts at y 4000
});

test('adapter: strona ponad limitami to UNVERIFIED page-too-tall bez zrzutu, ucieta lista to truncated', async () => {
  const config = makeEnv({ checks: ['contrast'] });
  const ops = {
    validate: () => [true],
    collect: () => ({ frames: 0, dir: 'ltr', contrast: { truncated: true, items: [rawItem(), rawItem({ id: 'hid', hidden: true })] } }),
    settle: () => ({ width: 800, height: 70000 }),
  };
  const { browser, calls } = fakeBrowser({ ops, shot: () => WHITE });
  const result = await createA11yRunner({ browser, imaging, config, configDir: CONFIG_DIR }).auditPage(config.pages[0]);
  assert.ok(!calls.some((c) => c[0] === 'screenshot'));
  assert.deepEqual(result.findings.map((f) => [f.verdict, f.reason]), [['EXEMPT', 'not-rendered'], ['UNVERIFIED', 'page-too-tall'], ['UNVERIFIED', 'truncated']]);
  assert.deepEqual(result.warnings.map((w) => w.code), ['page-too-tall', 'contrast-truncated']);
});

test('adapter: mysz tylko wg konfiguracji, przeladowanie po kliknieciu, popup zamkniety, nawigacja to UNVERIFIED', async () => {
  const config = makeEnv({ checks: ['focus'], focus: { mouse: { mode: 'forbid', selectors: ['#go', '#nav'] } } });
  const ops = {
    validate: (a) => a.selectors.map(() => true),
    resetFocus: () => true, active: () => ({ kind: 'body' }),
    reveal: (a) => ({ found: true, id: a.selector.slice(1), idUnique: true, chain: [], vr: { x: 10, y: 10, width: 50, height: 20 } }),
  };
  const shot = (opts, state) => (state.clicked ? BLACK : WHITE);
  const { browser, calls, page, state } = fakeBrowser({ ops, shot, withPopup: true });
  const origClick = page.mouse.click;
  let clickNo = 0;
  page.mouse.click = async (x, y) => { await origClick(x, y); clickNo += 1; if (clickNo === 2) page.url = () => 'http://localhost/elsewhere'; };
  const result = await createA11yRunner({ browser, imaging, config, configDir: CONFIG_DIR }).auditPage(config.pages[0]);
  const gotos = calls.filter((c) => c[0] === 'goto').length;
  assert.equal(gotos, 3, 'initial load plus a reload before every mouse selector');
  assert.equal(state.popupClosed, true);
  assert.deepEqual(calls.filter((c) => c[0] === 'move').map((c) => c.slice(1)), [[1279, 799], [1279, 799]], 'pointer parked away from the element before the "after" shot');
  const order = calls.map((c) => c[0]).filter((n) => ['click', 'move', 'screenshot'].includes(n)).join();
  assert.match(order, /screenshot,click,move,screenshot/);
  assert.deepEqual(calls.filter((c) => c[0] === 'click').map((c) => c.slice(1)), [[35, 20], [35, 20]]);
  const mouse = result.findings.filter((f) => f.rule === 'focus-mouse-ring');
  assert.deepEqual(mouse.map((f) => [f.selector, f.verdict]), [['#go', 'FAIL'], ['#nav', 'UNVERIFIED']]);
  assert.equal(mouse[0].measured.visible, true);
  const keyboard = result.findings.filter((f) => f.rule === 'focus-keyboard-indicator');
  assert.deepEqual(keyboard.map((f) => f.verdict), ['PASS']);
});

test('adapter: bez focus.mouse nie ma klikniec; kontrola focus bez mouse nie wymaga selektorow', async () => {
  const config = makeEnv({ checks: ['focus'] });
  const ops = { validate: () => [true], resetFocus: () => true, active: () => ({ kind: 'body' }) };
  const { browser, calls } = fakeBrowser({ ops, shot: () => WHITE });
  await createA11yRunner({ browser, imaging, config, configDir: CONFIG_DIR }).auditPage(config.pages[0]);
  assert.ok(!calls.some((c) => c[0] === 'click'));
});

test('adapter: handler trasy przepuszcza lokalne i data:, aborcja i licznik dla zewnetrznych', async () => {
  const blocked = createBlockedTracker();
  const handler = makeRouteHandler({ configDir: CONFIG_DIR, allowOrigins: [], blocked });
  const seen = [];
  const route = (url) => ({ request: () => ({ url: () => url }), continue: async () => { seen.push(['continue', url]); }, abort: async (code) => { seen.push(['abort', code]); } });
  await handler(route('http://localhost:5000/app.js'));
  await handler(route('data:text/css,a{}'));
  await handler(route('https://ext.example.org/x.js'));
  assert.deepEqual(seen.map((s) => s[0]), ['continue', 'continue', 'abort']);
  assert.equal(seen[2][1], 'blockedbyclient');
  assert.match(blocked.take('p').message, /ext\.example\.org/);
});

test('adapter: logowanie raz w kontekscie, wartosci z env, blokada originu loginu', async () => {
  const login = { url: 'login.html', steps: [{ fill: { selector: '#u', valueEnv: 'UI_VERIFY_A_USER' } }, { click: '#go' }] };
  const config = makeEnv({ login, checks: ['console'] });
  const calls = [];
  const page = { ...loginPage(), async goto(url, o) { calls.push(['goto', url, o.timeout]); }, async close() { calls.push(['close']); } };
  const context = { setDefaultTimeout() {}, async route() {}, async newPage() { return page; }, async close() {} };
  const runner = createA11yRunner({ browser: { newContext: async () => context }, imaging, config, configDir: CONFIG_DIR, env: { UI_VERIFY_A_USER: 'alice' } });
  await runner.login();
  assert.deepEqual(calls[0].slice(0, 1), ['goto']);
  assert.ok(calls[0][1].startsWith('file:'));
  assert.deepEqual(page.calls.slice(0, 2), [['fill', '#u', 'alice', 10000], ['click', '#go', 10000]]);
  assert.deepEqual(calls[calls.length - 1], ['close']);
  const none = createA11yRunner({ browser: { newContext: async () => { throw new Error('no context needed'); } }, imaging, config: makeEnv({}), configDir: CONFIG_DIR });
  await none.login(); // without a login section nothing happens
});

// ---------- assembling the report from page results ----------

const finding = (selector, verdict, ratio, required = 4.5) => ({ rule: 'contrast', selector, verdict, required, measured: { ratio, fontSizePx: 16, foreground: { r: 0, g: 0, b: 0, a: 1 }, effectiveBackground: { r: 255, g: 255, b: 255 } } });

test('raport z wynikow stron: wyjatek po kluczu, ostrzezenia stron i --now, brak asercji nie daje 0', () => {
  const config = makeEnv({});
  const pages = [{ id: 'home', url: '<config-dir>/page.html', assertion: 'PASS', checks: ['contrast'], findings: [finding('#ok', 'PASS', 8), finding('#brand', 'FAIL', 2.1)], warnings: [{ code: 'tab-end', level: 'info', message: 'page home: ended' }] }];
  const exceptionsData = { exceptions: [{ rule: 'contrast', page: 'home', selector: '#brand', reason: 'logo' }], confirmations: [] };
  const base = { pages, config, exceptionsData, now: '2026-11-22', digest: 'abc', masker: createMasker({}) };
  const plain1 = assembleReport({ ...base, exceptionsData: { exceptions: [], confirmations: [] } });
  assert.deepEqual([plain1.exit, plain1.result], [1, 'FAIL']);
  const ok = assembleReport({ ...base, nowOverridden: '2026-12-31' });
  assert.deepEqual([ok.exit, ok.pages[0].findings.map((f) => f.verdict)], [0, ['PASS', 'EXCEPTED']]);
  assert.ok(ok.warnings.some((w) => w.code === 'now-overridden') && ok.warnings.some((w) => w.code === 'tab-end'));
  assert.equal(ok.pages[0].findings[1].key, 'contrast|home|#brand');
  const none = assembleReport({ ...base, pages: [{ ...pages[0], assertion: 'NONE' }] });
  assert.equal(none.exit, 3);
});

test('raport z --expect: kod z porownania, element spoza listy nie wplywa, brak elementu to 3', () => {
  const config = makeEnv({});
  const pages = [{ id: 'home', url: 'x', assertion: 'PASS', checks: ['contrast'], findings: [finding('#pass', 'PASS', 4.61), finding('#fail', 'FAIL', 2.17)], warnings: [] }];
  const base = { pages, config, exceptionsData: { exceptions: [], confirmations: [] }, now: '2026-11-22', digest: 'abc', masker: createMasker({}) };
  const expected = { elements: [{ selector: '#pass', ratio: 4.61, verdict: 'PASS' }, { selector: '#fail', ratio: 2.17, verdict: 'FAIL' }] };
  const match = assembleReport({ ...base, expected });
  assert.deepEqual([match.mode, match.exit, match.result, match.auditExit], ['expect', 0, 'PASS', 1]);
  const missing = assembleReport({ ...base, expected: { elements: [{ selector: '#absent', verdict: 'PASS' }] } });
  assert.equal(missing.exit, 3);
  const mismatch = assembleReport({ ...base, expected: { elements: [{ selector: '#pass', ratio: 9, verdict: 'PASS' }] } });
  assert.equal(mismatch.exit, 1);
});

test('adapter: cel myszy zakryty przez inny element nie jest klikany, ostrzezenie mouse-occluded', async () => {
  const config = makeEnv({ checks: ['focus'], focus: { mouse: { mode: 'report', selectors: ['#go'] } } });
  const ops = {
    validate: (a) => a.selectors.map(() => true), resetFocus: () => true, active: () => ({ kind: 'body' }),
    reveal: () => ({ found: true, id: 'go', idUnique: true, chain: [], covered: true, vr: { x: 10, y: 10, width: 50, height: 20 } }),
  };
  const { browser, calls } = fakeBrowser({ ops, shot: () => WHITE });
  const result = await createA11yRunner({ browser, imaging, config, configDir: CONFIG_DIR }).auditPage(config.pages[0]);
  assert.ok(!calls.some((c) => c[0] === 'click'));
  assert.ok(result.warnings.some((w) => w.code === 'mouse-occluded'));
  assert.deepEqual(result.findings.filter((f) => f.rule === 'focus-mouse-ring').map((f) => [f.verdict, f.reason]), [['UNVERIFIED', 'occluded']]);
});

test('adapter: zmiana ukladu miedzy pomiarem a zrzutem to UNVERIFIED layout-shift bez wynikow pomiarowych', async () => {
  const config = makeEnv({ checks: ['contrast'] });
  const mk = (recheck) => ({
    validate: () => [true],
    collect: () => ({ frames: 0, dir: 'ltr', contrast: { truncated: false, items: [rawItem(), rawItem({ id: 'hid', hidden: true })] } }),
    settle: () => ({ width: 10, height: 10 }), recheck,
  });
  for (const recheck of [() => ({ height: 12, shifted: 0 }), () => ({ height: 10, shifted: 1 })]) {
    const { browser } = fakeBrowser({ ops: mk(recheck), shot: () => WHITE });
    const r = await createA11yRunner({ browser, imaging, config, configDir: CONFIG_DIR }).auditPage(config.pages[0]);
    assert.deepEqual(r.findings.map((f) => [f.verdict, f.reason]), [['EXEMPT', 'not-rendered'], ['UNVERIFIED', 'layout-shift']]);
    assert.ok(r.warnings.some((w) => w.code === 'layout-shift'));
  }
});

test('adapter: etykieta z kontrolka nie jest przystankiem Tab (kandydat ukryty), notka sampled przy uciecich prostokatach', async () => {
  assert.equal(toCandidate({ key: 'k1', id: 'l', idUnique: true, chain: [], focusable: false }).hidden, true);
  assert.equal(toCandidate({ key: 'k1', id: 'l', idUnique: true, chain: [] }).hidden, false);
  const config = makeEnv({ checks: ['contrast'] });
  const ops = {
    validate: () => [true],
    collect: () => ({ frames: 0, dir: 'ltr', contrast: { truncated: false, items: [rawItem({ rectsCut: true }), rawItem({ id: 'b', rects: [[0, 0, 4, 2]] })] } }),
    settle: () => ({ width: 10, height: 10 }), recheck: () => ({ height: 10, shifted: 0 }),
  };
  const { browser } = fakeBrowser({ ops, shot: () => WHITE });
  const r = await createA11yRunner({ browser, imaging, config, configDir: CONFIG_DIR }).auditPage(config.pages[0]);
  assert.deepEqual(r.findings.map((f) => f.note), ['sampled', undefined]);
});

test('adapter: ostrzezenia o ramkach i zamknietym shadow DOM, plik z symlinku poza katalogiem jest blokowany', async () => {
  const config = makeEnv({ checks: ['contrast'] });
  const ops = {
    validate: () => [true], settle: () => ({ width: 10, height: 10 }),
    collect: () => ({ frames: 2, dir: 'ltr', closedShadowGuess: 3, openShadowRoots: 1, contrast: { truncated: false, items: [] } }),
  };
  const { browser } = fakeBrowser({ ops, shot: () => WHITE });
  const r = await createA11yRunner({ browser, imaging, config, configDir: CONFIG_DIR }).auditPage(config.pages[0]);
  assert.deepEqual(r.warnings.map((w) => w.code), ['frames-not-audited', 'shadow-not-audited']);
  assert.match(r.warnings[1].message, /3 custom element/);
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-a11y-outside-'));
  fs.writeFileSync(path.join(outside, 'secret.txt'), 'x');
  try {
    fs.symlinkSync(outside, path.join(CONFIG_DIR, 'link'));
    assert.equal(routeDecision(pathToFileURL(path.join(CONFIG_DIR, 'link', 'secret.txt')).href, { configDir: CONFIG_DIR }).allow, false);
  } catch (e) {
    if (e.code !== 'EPERM') throw e; // symlinks may be unavailable (Windows without privileges)
  } finally {
    fs.rmSync(outside, { recursive: true, force: true });
  }
});

test('konsola: wlasny komunikat aplikacji zaczynajacy sie od "Failed to load resource" jest wykrywany, wzorzec Chrome nie; dlugi tekst z wieloma adresami szybko', () => {
  const c = createConsoleCollector();
  c.onConsole(msg('error', 'Failed to load resource: the server responded with a status of 404 ()', 'http://localhost/x'));
  c.onConsole(msg('error', 'Failed to load resource: net::ERR_CONNECTION_REFUSED', 'http://localhost/y'));
  c.onConsole(msg('error', 'Failed to load resource bundle for locale pl'));
  assert.deepEqual(c.result().list.map((e) => e.message), ['console.error: Failed to load resource bundle for locale pl']);
  const hostile = 'http://'.repeat(300000);
  const started = process.hrtime.bigint();
  const text = redactUrlQueries(hostile);
  c.onConsole(msg('error', hostile));
  const ms = Number(process.hrtime.bigint() - started) / 1e6;
  assert.ok(text.length <= 2000 + 20 && ms < 1000, `${text.length} chars, ${ms} ms`);
  assert.ok(c.result().list.every((e) => e.message.length < 2100));
});

test('adapter: blad zrzutu po kliknieciu to UNVERIFIED mouse-capture-failed, bieg trwa dalej (przeladowanie przed kolejnym selektorem)', async () => {
  const config = makeEnv({ checks: ['focus'], focus: { mouse: { mode: 'report', selectors: ['#nav', '#plain'] } } });
  const ops = {
    validate: (a) => a.selectors.map(() => true), resetFocus: () => true, active: () => ({ kind: 'body' }),
    reveal: (a) => ({ found: true, id: a.selector.slice(1), idUnique: true, chain: [], covered: false, vr: { x: 10, y: 10, width: 50, height: 20 } }),
  };
  const { browser, calls, page } = fakeBrowser({ ops, shot: () => WHITE });
  const origShot = page.screenshot;
  page.screenshot = async (o) => { if (calls.some((c) => c[0] === 'click') && !calls.some((c) => c[0] === 'goto' && c[1] === 'again')) { if (!page.failedOnce) { page.failedOnce = true; throw new Error('Unable to capture screenshot'); } } return origShot(o); };
  const result = await createA11yRunner({ browser, imaging, config, configDir: CONFIG_DIR }).auditPage(config.pages[0]);
  const ring = result.findings.filter((f) => f.rule === 'focus-mouse-ring');
  assert.deepEqual(ring.map((f) => [f.selector, f.verdict, f.reason]), [['#nav', 'UNVERIFIED', 'mouse-capture-failed'], ['#plain', 'INFO', undefined]]);
  assert.equal(calls.filter((c) => c[0] === 'goto').length, 3);
});

test('adapter: odmowa dekodera PNG (ImageLimitError) po kliknieciu NIE jest polykana jako mouse-capture-failed: auditPage odrzuca z ImageLimitError (kod 2)', async () => {
  const config = makeEnv({ checks: ['focus'], focus: { mouse: { mode: 'report', selectors: ['#nav'] } } });
  const ops = {
    validate: (a) => a.selectors.map(() => true), resetFocus: () => true, active: () => ({ kind: 'body' }),
    reveal: (a) => ({ found: true, id: a.selector.slice(1), idUnique: true, chain: [], covered: false, vr: { x: 10, y: 10, width: 50, height: 20 } }),
  };
  const { browser, calls, page } = fakeBrowser({ ops, shot: () => WHITE });
  const origShot = page.screenshot;
  const sig = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  const bomb = Buffer.alloc(33); sig.copy(bomb, 0); bomb.writeUInt32BE(13, 8); bomb.write('IHDR', 12, 'latin1'); bomb.writeUInt32BE(16385, 16); bomb.writeUInt32BE(1, 20);
  page.screenshot = async (o) => (calls.some((c) => c[0] === 'click') ? bomb : origShot.call(page, o));
  await assert.rejects(createA11yRunner({ browser, imaging, config, configDir: CONFIG_DIR }).auditPage(config.pages[0]), (e) => e.name === 'ImageLimitError');
});

test('adapter: zadanie do niedozwolonego originu widziane tylko jako zdarzenie request (przekierowanie) tez daje blocked-origin, bez bledu strony', async () => {
  const config = makeEnv({ checks: ['console'] });
  const ops = { validate: () => [true] };
  const { browser, state } = fakeBrowser({ ops, shot: () => WHITE });
  ops.validate = (a) => { state.handlers.request(req('GET', 'http://hop.example.invalid/x.js')); state.handlers.request(req('GET', 'http://hop.example.invalid/x.js')); state.handlers.requestfailed(req('GET', 'http://hop.example.invalid/x.js', 'net::ERR_NAME_NOT_RESOLVED')); return a.selectors.map(() => true); };
  const result = await createA11yRunner({ browser, imaging, config, configDir: CONFIG_DIR }).auditPage(config.pages[0]);
  const w = result.warnings.find((x) => x.code === 'blocked-origin');
  assert.match(w.message, /blocked 1 request\(s\).*hop\.example\.invalid/);
  assert.deepEqual(result.findings.map((f) => f.verdict), ['PASS']);
});

// ---------- review: selectors, vacuous checks, opacity 0, exception field ----------

test('gleboki DOM (40 poziomow): dwa sasiednie cele 20x20 daja dwa FAIL z roznymi selektorami, ogon nie jest ucinany', () => {
  const chain = (nth) => [...Array.from({ length: 40 }, (_, i) => ({ tag: `section-wrapper-${i}`, nth: 1 })), { tag: 'button', nth }];
  const target = (key, nth, x) => ({ key, id: '', idUnique: false, chain: chain(nth), box: { x, y: 0, width: 20, height: 20 }, display: 'inline-block' });
  const raws = [target('k1', 1, 0), target('k2', 2, 22)];
  const f = targetFindings(raws);
  assert.deepEqual(f.map((x) => x.verdict), ['FAIL', 'FAIL']);
  assert.notEqual(f[0].selector, f[1].selector);
  assert.ok(f.every((x) => x.selector.length <= 400 && x.selector.startsWith('html') === false));
  assert.deepEqual(selectorCollisions(raws), []);
  const same = [{ ...raws[0] }, { ...raws[0], key: 'k9', box: { x: 50, y: 0, width: 20, height: 20 } }];
  assert.equal(targetFindings(same).length, 2, 'two different elements with one selector are both kept');
  assert.equal(selectorCollisions(same).length, 1);
});

test('wyjatki celow PASS zachowuja pole exception (spacing, equivalent-label) obok noty', () => {
  const t = (id, box, extra = {}) => ({ id, idUnique: true, chain: [], box, display: 'block', ...extra });
  const f = targetFindings([t('tiny', { x: 0, y: 0, width: 16, height: 16 }), t('cb', { x: 300, y: 0, width: 13, height: 13 }, { labelBox: { width: 100, height: 30 }, pair: 'p' })]);
  assert.deepEqual(f.map((x) => x.exception), ['spacing', 'equivalent-label']);
  assert.equal(f[0].note, 'spacing');
});

test('tekst z efektywna alfa 0 (opacity:0) to EXEMPT not-rendered', () => {
  assert.equal(classifyContrastRaw(rawItem({ opacity: 0 })).exempt, 'not-rendered');
  assert.deepEqual(classifyContrastRaw(rawItem({ opacity: 0.4 })), {});
});

test('prozniowe PASS: bez zmierzonej kontroli tresci staja sie UNVERIFIED no-samples, ze zmierzona zostaja PASS z nota', () => {
  const vac = (rule) => ({ rule, verdict: 'PASS', vacuous: true, hashBasis: 'x', message: 'nothing' });
  const real = { rule: 'contrast', verdict: 'PASS', selector: '#a' };
  const none = settleVacuous([vac('target-size'), vac('focus-keyboard-indicator')]);
  assert.deepEqual(none.map((f) => [f.verdict, f.reason, 'vacuous' in f]), [['UNVERIFIED', 'no-samples', false], ['UNVERIFIED', 'no-samples', false]]);
  const kept = settleVacuous([vac('target-size'), real]);
  assert.deepEqual(kept.map((f) => [f.verdict, 'vacuous' in f]), [['PASS', false], ['PASS', false]]);
  assert.equal(settleVacuous([vac('target-size'), { rule: 'contrast', verdict: 'FAIL' }])[0].verdict, 'PASS');
  assert.equal(settleVacuous([vac('target-size'), { rule: 'contrast', verdict: 'EXEMPT' }])[0].verdict, 'UNVERIFIED');
});

async function auditWith(ops, checks) {
  const config = makeEnv({ checks });
  const { browser } = fakeBrowser({ ops: { validate: () => [true], resetFocus: () => true, active: () => ({ kind: 'body' }), settle: () => ({ width: 10, height: 10 }), recheck: () => ({ height: 10, shifted: 0 }), ...ops }, shot: () => WHITE });
  return createA11yRunner({ browser, imaging, config, configDir: CONFIG_DIR }).auditPage(config.pages[0]);
}
const verdicts = (r) => r.findings.map((f) => [f.rule, f.verdict, f.reason]);

test('pusta strona: domyslne checks daja UNVERIFIED (konsola sama nie wystarcza), zawezone target-size tez, samo console zostaje PASS', async () => {
  const emptyCollect = () => ({ frames: 0, dir: 'ltr', targets: { truncated: false, items: [] }, contrast: { truncated: false, items: [] } });
  const all = await auditWith({ collect: emptyCollect }, ['contrast', 'target-size', 'focus', 'tab-order', 'console']);
  assert.ok(all.findings.every((f) => f.verdict === 'UNVERIFIED' || f.rule === 'console'), JSON.stringify(verdicts(all)));
  assert.ok(!all.findings.some((f) => f.verdict === 'PASS' && f.rule !== 'console'));
  assert.deepEqual(verdicts(await auditWith({ collect: emptyCollect }, ['target-size'])), [['target-size', 'UNVERIFIED', 'no-samples']]);
  assert.deepEqual(verdicts(await auditWith({}, ['console'])), [['console', 'PASS', undefined]]);
});

test('strona z samymi EXEMPT tekstami: kontrast UNVERIFIED no-samples, nie PASS', async () => {
  const collect = () => ({ frames: 0, dir: 'ltr', contrast: { truncated: false, items: [rawItem({ hidden: true }), rawItem({ id: 'd', inactive: true }), rawItem({ id: 'z', opacity: 0 })] } });
  const r = await auditWith({ collect }, ['contrast']);
  assert.deepEqual(verdicts(r), [['contrast', 'EXEMPT', 'not-rendered'], ['contrast', 'EXEMPT', 'inactive'], ['contrast', 'EXEMPT', 'not-rendered'], ['contrast', 'UNVERIFIED', 'no-samples']]);
});

// ---------- settle: lazy images (CORE-1170-LAZY) ----------

// A fake page for the `settle` operation: an image whose lazy load never started (the browser decides it asynchronously, so after the scroll pass
// it can still be not loaded). Like a browser, decode() of a not loaded lazy image rejects at once; an eager image loads on its own after `loadMs`.
function settleDom({ loadMs = 20, lazy = true } = {}) {
  const listeners = {};
  const img = {
    complete: false, started: false,
    addEventListener(name, fn) { (listeners[name] = listeners[name] || []).push(fn); },
    decode() { return this.complete ? Promise.resolve() : Promise.reject(new Error('EncodingError')); },
  };
  const startLoad = () => {
    if (img.started) return;
    img.started = true;
    setTimeout(() => { img.complete = true; (listeners.load || []).forEach((fn) => fn()); }, loadMs);
  };
  // a browser starts the load of an image that is not lazy at once; a lazy one only when `loading` becomes eager
  if (!lazy) startLoad();
  Object.defineProperty(img, 'loading', { get: () => img._loading, set: (v) => { img._loading = v; if (v === 'eager') startLoad(); } });
  img._loading = lazy ? 'lazy' : 'eager';
  const doc = { fonts: { ready: Promise.resolve() }, documentElement: { scrollHeight: 3500, scrollWidth: 1280 }, body: { scrollHeight: 3500, scrollWidth: 1280 }, images: [img] };
  const scrolls = [];
  const sandbox = { document: doc, innerWidth: 1280, innerHeight: 800, scrollTo: (o) => scrolls.push(o.top), setTimeout, getComputedStyle: () => ({}) };
  const fn = vm.runInNewContext(`(${PAGE_SCRIPT})`, sandbox);
  return { img, scrolls, settle: () => fn({ op: 'settle' }).then(plain) };
}

test('settle: lazy obraz, ktorego ladowanie nie ruszylo w czasie przewijania, jest zaladowany przed zrzutem (nie tylko decode())', async () => {
  const dom = settleDom();
  const size = await dom.settle();
  assert.equal(dom.img.complete, true, 'obraz musi byc zaladowany po settle');
  assert.equal(dom.img.loading, 'eager', 'niezaladowany lazy obraz jest wymuszony do ladowania');
  assert.deepEqual(size, { width: 1280, height: 3500 });
  assert.equal(dom.scrolls.at(-1), 0, 'settle konczy na gorze dokumentu');
});

test('settle: obraz juz zaladowany albo nie-lazy zostaje bez zmian, a obraz z bledem ladowania nie wiesza settle', async () => {
  const done = settleDom({ lazy: false, loadMs: 0 });
  await done.settle();
  assert.equal(done.img.loading, 'eager');
  assert.equal(done.img.complete, true);
  const broken = settleDom();
  broken.img.addEventListener = (name, fn) => { if (name === 'error') setTimeout(fn, 5); };   // no `load`, only `error`
  const t0 = Date.now();
  await broken.settle();
  assert.ok(Date.now() - t0 < 3000, 'blad ladowania konczy czekanie');
});
