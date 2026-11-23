// Czysta logika lib/measure-core.mjs (S4): validateConfig, runMeasure z atrapa adaptera i syntetycznymi obrazami, raport i JSON z maskowaniem.
// Bez przegladarki i bez PNG: obrazy to { width, height, data } RGBA.
import test from 'node:test';
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const LIB = path.join(HERE, '..', '..', 'library', 'skills', 'ui-verify', 'scripts', 'lib');
const lib = (name) => import(pathToFileURL(path.join(LIB, name)).href);

const core = await lib('measure-core.mjs');
const { diffImages } = await lib('pixels.mjs');
const { createMasker } = await lib('out.mjs');
const { validateConfig, runMeasure, formatReport, toJson, DEFAULTS, MeasureError } = core;

const CONFIG_DIR = path.resolve(os.tmpdir(), 'uv-measure-core-cfg');
const URL_REF = 'http://localhost/ref';
const URL_CAND = 'http://localhost/cand';

// --- pomocnicze ---

const WHITE = [255, 255, 255, 255];
const BLACK = [0, 0, 0, 255];
function solid(w, h, fn) {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) data.set(fn(x, y), (y * w + x) * 4);
  return { width: w, height: h, data };
}
// Asymetryczny ksztalt: pelny prostokat 12 px szeroki przy lewej (x0=4) albo prawej krawedzi (lustro).
const block = (x0, w = 40, h = 40) => solid(w, h, (x, y) => (x >= x0 && x < x0 + 12 && y >= 4 && y < h - 4 ? BLACK : WHITE));
const IMG_LEFT = () => block(4);
const IMG_RIGHT = () => block(24);

const STYLES = { color: 'rgb(0, 0, 0)', backgroundColor: 'rgb(255, 255, 255)', borderTopColor: 'rgb(0, 0, 0)', borderTopLeftRadius: '4px', fontSize: '12px', fontFamily: 'Arial' };
const side = (over = {}) => ({ found: true, box: { x: 10, y: 10, width: 40, height: 40 }, styles: { ...STYLES }, image: IMG_LEFT(), ...over });

function pairCfg(over = {}) {
  return { id: 'p', reference: { url: URL_REF, selector: '#t' }, candidate: { url: URL_CAND, selector: '#t' }, ...over };
}
function mkConfig(pairs, top = {}) {
  const v = validateConfig({ version: 1, pairs, ...top }, { configDir: CONFIG_DIR, allowOrigins: [] });
  assert.equal(v.ok, true, `konfiguracja testowa niepoprawna: ${v.errors}`);
  return v.config;
}

// Atrapa adaptera. `data[url] = { found, box, styles, image }`. screenshot(side) akceptuje obiekt strony albo napis
// 'reference'/'candidate' (ostatnio zmierzona para).
function mkAdapter(data, { failOn, refUrls = [URL_REF] } = {}) {
  const calls = [];
  const last = {};
  const refs = new Set(refUrls);
  return {
    calls,
    async measure(s) {
      calls.push(['measure', s.url]);
      if (failOn && failOn(s)) throw new Error('page closed');
      const d = data[s.url];
      assert.ok(d, `brak danych atrapy dla ${s.url}`);
      last[refs.has(s.url) ? 'reference' : 'candidate'] = s.url;
      return { found: d.found, box: d.box, styles: d.styles, ...(d.reason ? { reason: d.reason } : {}) };
    },
    async screenshot(s) {
      const k = typeof s === 'string' ? last[s] : s.url;
      calls.push(['screenshot', k]);
      return data[k].image;
    },
  };
}
const spyDiff = () => {
  const calls = [];
  const fn = (a, b, opts) => { calls.push(opts); return diffImages(a, b, opts); };
  fn.calls = calls;
  return fn;
};
async function measureOne({ ref = side(), cand = side(), pair = {}, top = {} } = {}) {
  const config = mkConfig([pairCfg(pair)], top);
  const adapter = mkAdapter({ [URL_REF]: ref, [URL_CAND]: cand });
  const diffFn = spyDiff();
  const result = await runMeasure({ config, adapter, diffFn });
  return { result, p: result.pairs[0], adapter, diffFn };
}

// --- validateConfig ---

const V = (cfg, opts = {}) => validateConfig(cfg, { configDir: CONFIG_DIR, allowOrigins: [], ...opts });
const okCfg = () => ({ version: 1, pairs: [pairCfg()] });

test('validateConfig: poprawna konfiguracja => ok, brak bledow, uzupelnione DEFAULTS', () => {
  const r = V(okCfg());
  assert.equal(r.ok, true, String(r.errors));
  assert.deepEqual(r.errors, []);
  assert.deepEqual(r.config.viewport, { width: 400, height: 200, deviceScaleFactor: 1 });
  assert.equal(r.config.tolerancePx, 1);
  assert.equal(r.config.sizeTolerancePx, 1);
  assert.equal(r.config.colorTolerance, 0);
  assert.equal(r.config.maxPct, 0.5);
  assert.deepEqual(r.config.styleProps, ['color', 'backgroundColor', 'borderTopColor', 'borderTopLeftRadius', 'fontSize', 'fontFamily']);
  assert.equal(DEFAULTS.tolerancePx, 1);
  assert.equal(DEFAULTS.maxPct, 0.5);
});

test('validateConfig: jawne wartosci nadpisuja domyslne', () => {
  const r = V({ ...okCfg(), tolerancePx: 2, sizeTolerancePx: 3, colorTolerance: 4, maxPct: 1.5, viewport: { width: 800, height: 600, deviceScaleFactor: 2 }, styleProps: ['color'] });
  assert.equal(r.ok, true, String(r.errors));
  assert.equal(r.config.tolerancePx, 2);
  assert.equal(r.config.sizeTolerancePx, 3);
  assert.equal(r.config.colorTolerance, 4);
  assert.equal(r.config.maxPct, 1.5);
  assert.deepEqual(r.config.styleProps, ['color']);
  assert.equal(r.config.viewport.width, 800);
});

test('validateConfig: bledy sa lista napisow, a nie wyjatek', () => {
  const r = V({ version: 2, pairs: [] });
  assert.equal(r.ok, false);
  assert.ok(Array.isArray(r.errors) && r.errors.length >= 2);
  assert.ok(r.errors.every((e) => typeof e === 'string' && e !== ''));
});

for (const [name, cfg] of [
  ['brak version', { pairs: [pairCfg()] }],
  ['version 2', { version: 2, pairs: [pairCfg()] }],
  ['version jako napis', { version: '1', pairs: [pairCfg()] }],
  ['brak pairs', { version: 1 }],
  ['pairs puste', { version: 1, pairs: [] }],
  ['pairs nie jest lista', { version: 1, pairs: {} }],
  ['konfiguracja null', null],
]) {
  test(`validateConfig: ${name} => blad`, () => {
    const r = V(cfg);
    assert.equal(r.ok, false);
    assert.ok(r.errors.length >= 1);
  });
}

test('validateConfig: nieznany klucz najwyzszego poziomu (literowka) => blad z nazwa klucza', () => {
  const r = V({ ...okCfg(), tolerancePxx: 1 });
  assert.equal(r.ok, false);
  assert.ok(r.errors.some((e) => e.includes('tolerancePxx')), String(r.errors));
});

for (const [name, id] of [['spacja', 'a b'], ['ukosnik', 'a/b'], ['pusty', ''], ['kropki w gore', '../x'], ['polskie znaki', 'zażółć'], ['za dlugi (121)', 'a'.repeat(121)]]) {
  test(`validateConfig: id niezgodne z [A-Za-z0-9._-]{1,120} (${name}) => blad`, () => {
    const r = V({ version: 1, pairs: [pairCfg({ id })] });
    assert.equal(r.ok, false, `id=${JSON.stringify(id)}`);
  });
}

test('validateConfig: id o dlugosci 120 i znaki . _ - sa dozwolone', () => {
  assert.equal(V({ version: 1, pairs: [pairCfg({ id: 'a'.repeat(120) })] }).ok, true);
  assert.equal(V({ version: 1, pairs: [pairCfg({ id: 'Ab.c_d-1' })] }).ok, true);
});

test('validateConfig: duplikat id => blad', () => {
  const r = V({ version: 1, pairs: [pairCfg({ id: 'x' }), pairCfg({ id: 'x' })] });
  assert.equal(r.ok, false);
  assert.ok(r.errors.some((e) => /x/.test(e)), String(r.errors));
});

test('validateConfig: niedozwolony origin (http zewnetrzny) => blad, dozwolony przez allowOrigins => ok', () => {
  const ext = pairCfg({ reference: { url: 'https://app.example.test/page', selector: '#t' } });
  const bad = V({ version: 1, pairs: [ext] });
  assert.equal(bad.ok, false);
  assert.ok(bad.errors.some((e) => /origin|allow/i.test(e)), String(bad.errors));
  const good = V({ version: 1, pairs: [ext] }, { allowOrigins: ['app.example.test'] });
  assert.equal(good.ok, true, String(good.errors));
});

test('validateConfig: niedozwolony origin w candidate (nie tylko w reference) => blad', () => {
  const r = V({ version: 1, pairs: [pairCfg({ candidate: { url: 'https://other.example.test/', selector: '#t' } })] });
  assert.equal(r.ok, false);
});

test('validateConfig: schemat spoza http(s)/pliku (ftp, javascript) => blad', () => {
  for (const url of ['ftp://localhost/x', 'javascript:alert(1)']) {
    const r = V({ version: 1, pairs: [pairCfg({ reference: { url, selector: '#t' } })] });
    assert.equal(r.ok, false, url);
  }
});

test('validateConfig: sciezka wzgledna => file:// pod configDir; ucieczka ../ i sciezka bezwzgledna => blad', () => {
  const r = V({ version: 1, pairs: [pairCfg({ reference: { url: 'fx/ref.html', selector: '#t' } })] });
  assert.equal(r.ok, true, String(r.errors));
  const u = r.config.pairs[0].reference.url;
  assert.match(u, /^file:\/\//);
  assert.equal(fileURLToPath(u), path.join(CONFIG_DIR, 'fx', 'ref.html'));
  for (const url of ['../outside.html', 'a/../../outside.html', '/etc/passwd']) {
    const bad = V({ version: 1, pairs: [pairCfg({ reference: { url, selector: '#t' } })] });
    assert.equal(bad.ok, false, url);
  }
});

test('validateConfig: brak url albo selector w stronie => blad', () => {
  assert.equal(V({ version: 1, pairs: [pairCfg({ reference: { selector: '#t' } })] }).ok, false);
  assert.equal(V({ version: 1, pairs: [pairCfg({ candidate: { url: URL_CAND } })] }).ok, false);
  assert.equal(V({ version: 1, pairs: [pairCfg({ reference: undefined })] }).ok, false);
});

for (const [name, over] of [
  ['tolerancePx ujemne', { tolerancePx: -1 }],
  ['sizeTolerancePx ujemne', { sizeTolerancePx: -0.5 }],
  ['colorTolerance ujemne', { colorTolerance: -1 }],
  ['maxPct ujemne', { maxPct: -0.1 }],
  ['tolerancePx nie jest liczba', { tolerancePx: '1' }],
  ['maxPct NaN-owy napis', { maxPct: 'abc' }],
]) {
  test(`validateConfig: ${name} => blad (liczby nieujemne)`, () => {
    assert.equal(V({ ...okCfg(), ...over }).ok, false);
  });
}

test('validateConfig: zero jest dozwolone dla tolerancji i maxPct', () => {
  const r = V({ ...okCfg(), tolerancePx: 0, sizeTolerancePx: 0, colorTolerance: 0, maxPct: 0 });
  assert.equal(r.ok, true, String(r.errors));
});

test('validateConfig: odstepstwo bez reason (brak, pusty, same spacje, nie-napis) => blad walidacji', () => {
  for (const dev of [{ prop: 'width' }, { prop: 'width', reason: '' }, { prop: 'width', reason: '   ' }, { prop: 'width', reason: 5 }]) {
    const r = V({ version: 1, pairs: [pairCfg({ deviations: [dev] })] });
    assert.equal(r.ok, false, JSON.stringify(dev));
    assert.ok(r.errors.some((e) => /reason/i.test(e)), String(r.errors));
  }
});

test('validateConfig: odstepstwo z reason jest poprawne', () => {
  const r = V({ version: 1, pairs: [pairCfg({ deviations: [{ selector: '#t', prop: 'width', expected: 40, actual: 42, reason: 'zatwierdzone przez projektanta' }] })] });
  assert.equal(r.ok, true, String(r.errors));
});

test('validateConfig: maski z ujemnymi/nieliczbowymi wymiarami => blad', () => {
  for (const m of [{ x: 0, y: 0, width: -1, height: 5 }, { x: 'a', y: 0, width: 1, height: 1 }]) {
    assert.equal(V({ version: 1, pairs: [pairCfg({ masks: [m] })] }).ok, false, JSON.stringify(m));
  }
  assert.equal(V({ version: 1, pairs: [pairCfg({ masks: [{ x: 0, y: 0, width: 5, height: 5 }] })] }).ok, true);
});

test('validateConfig: nie modyfikuje wejscia i nie rzuca dla dziwnych typow', () => {
  const input = okCfg();
  const before = JSON.stringify(input);
  V(input);
  assert.equal(JSON.stringify(input), before);
  for (const weird of [undefined, 5, 'x', [], { version: 1, pairs: [null] }, { version: 1, pairs: [5] }]) {
    assert.equal(V(weird).ok, false);
  }
});

// --- runMeasure ---

test('runMeasure: identyczne pary => PASS, exit 0, pomiar PASS, ksztalt PASS (cropped false)', async () => {
  const { result, p, diffFn } = await measureOne();
  assert.equal(result.verdict, 'PASS');
  assert.equal(result.exit, 0);
  assert.equal(p.id, 'p');
  assert.equal(p.verdict, 'PASS');
  assert.equal(p.measure.verdict, 'PASS');
  assert.deepEqual(p.measure.unapproved, []);
  assert.equal(p.shape.verdict, 'PASS');
  assert.equal(p.shape.cropped, false);
  assert.equal(p.shape.pct, 0);
  assert.equal(diffFn.calls.length, 1);
});

test('runMeasure: diffFn dostaje cropToCommon:true, maxPct z konfiguracji i maski pary', async () => {
  const masks = [{ x: 0, y: 0, width: 4, height: 4 }];
  const { diffFn } = await measureOne({ pair: { masks }, top: { maxPct: 2.5 } });
  assert.equal(diffFn.calls.length, 1);
  assert.equal(diffFn.calls[0].cropToCommon, true);
  assert.equal(diffFn.calls[0].maxPct, 2.5);
  assert.deepEqual(diffFn.calls[0].masks, masks);
});

test('runMeasure: przesuniecie pudelka w granicach tolerancji (1 px) => PASS; powyzej => FAIL z findingiem x', async () => {
  const ok = await measureOne({ cand: side({ box: { x: 11, y: 10, width: 40, height: 40 } }) });
  assert.equal(ok.p.verdict, 'PASS');
  assert.equal(ok.p.measure.verdict, 'PASS');
  const bad = await measureOne({ cand: side({ box: { x: 13, y: 10, width: 40, height: 40 } }) });
  assert.equal(bad.p.measure.verdict, 'FAIL');
  assert.equal(bad.p.verdict, 'FAIL');
  assert.equal(bad.result.exit, 1);
  assert.ok(bad.p.measure.unapproved.some((f) => f.prop === 'x'), JSON.stringify(bad.p.measure.unapproved));
});

test('runMeasure: tolerancePx z konfiguracji jest respektowane (0 => 1 px juz FAIL)', async () => {
  const { p } = await measureOne({ cand: side({ box: { x: 11, y: 10, width: 40, height: 40 } }), top: { tolerancePx: 0 } });
  assert.equal(p.measure.verdict, 'FAIL');
});

test('runMeasure: roznica stylu (kolor tla) => FAIL z findingiem backgroundColor; kolor w colorTolerance => PASS', async () => {
  const cand = side({ styles: { ...STYLES, backgroundColor: 'rgb(250, 255, 255)' } });
  const bad = await measureOne({ cand });
  assert.equal(bad.p.measure.verdict, 'FAIL');
  assert.ok(bad.p.measure.unapproved.some((f) => f.prop === 'backgroundColor'));
  const ok = await measureOne({ cand, top: { colorTolerance: 5 } });
  assert.equal(ok.p.measure.verdict, 'PASS');
});

test('runMeasure: tylko styleProps z konfiguracji sa porownywane', async () => {
  const cand = side({ styles: { ...STYLES, fontSize: '30px' } });
  const { p } = await measureOne({ cand, top: { styleProps: ['color'] } });
  assert.equal(p.measure.verdict, 'PASS');
});

test('runMeasure: odstepstwo z powodem zatwierdza finding (approved), a wynik jest PASS', async () => {
  const cand = side({ styles: { ...STYLES, fontSize: '14px' } });
  const dev = { prop: 'fontSize', reason: 'nowy rozmiar zatwierdzony' };
  const { p, result } = await measureOne({ cand, pair: { deviations: [dev] } });
  assert.equal(p.measure.verdict, 'PASS');
  assert.deepEqual(p.measure.unapproved, []);
  assert.ok(p.measure.approved.some((f) => f.prop === 'fontSize'), JSON.stringify(p.measure.approved));
  assert.equal(result.verdict, 'PASS');
  assert.match(formatReport(result, createMasker({})), /nowy rozmiar zatwierdzony/);
});

test('runMeasure: odstepstwo dla innej wlasciwosci nie zatwierdza (kontrola negatywna)', async () => {
  const cand = side({ styles: { ...STYLES, fontSize: '14px' } });
  const { p } = await measureOne({ cand, pair: { deviations: [{ prop: 'color', reason: 'inny powod' }] } });
  assert.equal(p.measure.verdict, 'FAIL');
  assert.ok(p.measure.unapproved.some((f) => f.prop === 'fontSize'));
});

test('runMeasure: odstepstwo z actual/expected niepasujacymi do wartosci nie zatwierdza', async () => {
  const cand = side({ styles: { ...STYLES, fontSize: '14px' } });
  const dev = { prop: 'fontSize', expected: '12px', actual: '99px', reason: 'inna wartosc' };
  const { p } = await measureOne({ cand, pair: { deviations: [dev] } });
  assert.equal(p.measure.verdict, 'FAIL');
});

test('runMeasure: KSZTALT FAIL przy identycznych pudelkach i stylach (lustrzany obraz) => para FAIL, exit 1', async () => {
  const { p, result } = await measureOne({ ref: side({ image: IMG_LEFT() }), cand: side({ image: IMG_RIGHT() }) });
  assert.equal(p.measure.verdict, 'PASS', 'pomiar pudelek i stylow nie widzi roznicy');
  assert.equal(p.shape.verdict, 'FAIL');
  assert.ok(p.shape.pct > 0.5, String(p.shape.pct));
  assert.equal(p.verdict, 'FAIL');
  assert.equal(result.verdict, 'FAIL');
  assert.equal(result.exit, 1);
});

test('runMeasure: ksztalt miesci sie w maxPct => PASS (jedna roznica ponizej progu)', async () => {
  const one = solid(40, 40, (x, y) => (x === 20 && y === 20 ? BLACK : WHITE));
  const none = solid(40, 40, () => WHITE);
  const { p } = await measureOne({ ref: side({ image: none }), cand: side({ image: one }) });
  assert.equal(p.shape.verdict, 'PASS');
  assert.ok(p.shape.pct > 0 && p.shape.pct <= 0.5, String(p.shape.pct));
});

test('runMeasure: maska pary zakrywajaca roznice => ksztalt PASS', async () => {
  const masks = [{ x: 0, y: 0, width: 40, height: 40 }];
  const { p } = await measureOne({ ref: side({ image: IMG_LEFT() }), cand: side({ image: IMG_RIGHT() }), pair: { masks } });
  assert.equal(p.shape.verdict, 'PASS');
});

test('runMeasure: cropped:true gdy pudelka rozni sie w sizeTolerancePx, a obrazy maja rozne wymiary', async () => {
  const ref = side({ image: block(4, 40, 40) });
  const cand = side({ box: { x: 10, y: 10, width: 41, height: 40 }, image: block(4, 41, 40) });
  const { p } = await measureOne({ ref, cand });
  assert.equal(p.measure.verdict, 'PASS');
  assert.equal(p.shape.cropped, true);
  assert.equal(p.shape.verdict, 'PASS');
});

test('runMeasure: wymiary pudelek roznia sie ponad sizeTolerancePx => pomiar FAIL, ksztalt SKIPPED box-mismatch, diffFn nie wolany', async () => {
  const cand = side({ box: { x: 10, y: 10, width: 60, height: 40 }, image: block(4, 60, 40) });
  const { p, result, diffFn } = await measureOne({ cand });
  assert.equal(p.measure.verdict, 'FAIL');
  assert.deepEqual({ verdict: p.shape.verdict, reason: p.shape.reason }, { verdict: 'SKIPPED', reason: 'box-mismatch' });
  assert.equal(diffFn.calls.length, 0);
  assert.equal(p.verdict, 'FAIL');
  assert.equal(result.exit, 1);
});

test('runMeasure: sizeTolerancePx z konfiguracji (3) dopuszcza 3 px roznicy szerokosci i ocenia ksztalt', async () => {
  const cand = side({ box: { x: 10, y: 10, width: 43, height: 40 }, image: block(4, 43, 40) });
  const { p, diffFn } = await measureOne({ cand, top: { sizeTolerancePx: 3, tolerancePx: 3 } });
  assert.notEqual(p.shape.verdict, 'SKIPPED');
  assert.equal(diffFn.calls.length, 1);
});

test('runMeasure: brak elementu po stronie reference albo candidate => UNVERIFIED (element-not-found), exit 3, nie FAIL/PASS', async () => {
  for (const [ref, cand] of [[side({ found: false, box: null, styles: {}, image: null }), side()], [side(), side({ found: false, box: null, styles: {}, image: null })]]) {
    const { p, result } = await measureOne({ ref, cand });
    assert.equal(p.verdict, 'UNVERIFIED');
    assert.match(JSON.stringify(p), /element-not-found/);
    assert.equal(result.verdict, 'UNVERIFIED');
    assert.equal(result.exit, 3);
  }
});

test('runMeasure: wiele par — FAIL wygrywa z UNVERIFIED (exit 1), UNVERIFIED z PASS daje UNVERIFIED (exit 3)', async () => {
  const URL_A = 'http://localhost/a-ref'; const URL_B = 'http://localhost/b-ref';
  const mk = (id, ref, cand) => ({ id, reference: { url: ref, selector: '#t' }, candidate: { url: cand, selector: '#t' } });
  const missing = side({ found: false, box: null, styles: {}, image: null });
  const run = async (bData) => {
    const data = { [URL_A]: side(), 'http://localhost/a-cand': side(), ...bData };
    const adapter = mkAdapter(data, { refUrls: [URL_A, URL_B] });
    const config = mkConfig([mk('a', URL_A, 'http://localhost/a-cand'), mk('b', URL_B, 'http://localhost/b-cand')]);
    return runMeasure({ config, adapter, diffFn: spyDiff() });
  };
  const unv = await run({ [URL_B]: missing, 'http://localhost/b-cand': side() });
  assert.deepEqual(unv.pairs.map((x) => x.verdict), ['PASS', 'UNVERIFIED']);
  assert.equal(unv.verdict, 'UNVERIFIED');
  assert.equal(unv.exit, 3);
  const failBox = side({ box: { x: 90, y: 10, width: 40, height: 40 } });
  const fail = await run({ [URL_B]: side(), 'http://localhost/b-cand': failBox });
  assert.equal(fail.verdict, 'FAIL');
  assert.equal(fail.exit, 1);
});

test('runMeasure: wyjatek adaptera => odrzucenie MeasureError z code "adapter" (nie wynik FAIL)', async () => {
  const config = mkConfig([pairCfg()]);
  const adapter = mkAdapter({ [URL_REF]: side(), [URL_CAND]: side() }, { failOn: (s) => s.url === URL_CAND });
  await assert.rejects(runMeasure({ config, adapter, diffFn: spyDiff() }), (err) => {
    assert.ok(err instanceof MeasureError, String(err));
    assert.equal(err.code, 'adapter');
    return true;
  });
});

test('runMeasure: wyjatek z screenshot rowniez => MeasureError adapter', async () => {
  const config = mkConfig([pairCfg()]);
  const adapter = mkAdapter({ [URL_REF]: side(), [URL_CAND]: side() });
  adapter.screenshot = async () => { throw new Error('timeout'); };
  await assert.rejects(runMeasure({ config, adapter, diffFn: spyDiff() }), (err) => err instanceof MeasureError && err.code === 'adapter');
});

// --- formatReport / toJson i sekret ---

const secretValue = () => ['fake', 'secret', String(process.pid), 'wxyz'].join('-');

async function resultWithSecret(secret) {
  const cand = side({ styles: { ...STYLES, fontFamily: `Font ${secret}` } });
  return (await measureOne({ cand, pair: { id: `id-${secret}` } })).result;
}

test('formatReport: linie PASS|FAIL|UNVERIFIED <id>, findingi z prop/actual/expected, ksztalt z pct', async () => {
  const cand = side({ styles: { ...STYLES, fontSize: '14px' } });
  const { result } = await measureOne({ cand, pair: { id: 'ikona' } });
  const text = formatReport(result, createMasker({}));
  assert.match(text, /^FAIL ikona\b/m);
  assert.match(text, /fontSize/);
  assert.match(text, /14px/);
  assert.match(text, /12px/);
  const missing = await measureOne({ ref: side({ found: false, box: null, styles: {}, image: null }), pair: { id: 'brak' } });
  assert.match(formatReport(missing.result, createMasker({})), /^UNVERIFIED brak\b/m);
  const ok = await measureOne({ pair: { id: 'ok' } });
  assert.match(formatReport(ok.result, createMasker({})), /^PASS ok\b/m);
});

test('formatReport: raport kropnietego ksztaltu wspomina cropped', async () => {
  const cand = side({ box: { x: 10, y: 10, width: 41, height: 40 }, image: block(4, 41, 40) });
  const { result } = await measureOne({ cand });
  assert.match(formatReport(result, createMasker({})), /cropped/i);
});

test('formatReport i toJson maskuja sekret zlozony w czasie wykonania (wartosc, warianty URL/base64)', async () => {
  const secret = secretValue();
  const result = await resultWithSecret(secret);
  const masker = createMasker({ secrets: [secret] });
  const text = formatReport(result, masker);
  const json = JSON.stringify(toJson(result, masker));
  for (const v of [secret, encodeURIComponent(secret), Buffer.from(secret).toString('base64')]) {
    assert.ok(!text.includes(v), 'sekret w raporcie tekstowym');
    assert.ok(!json.includes(v), 'sekret w JSON');
  }
  assert.match(text, /\*\*\*/);
  assert.match(json, /\*\*\*/);
});

test('kontrola: bez sekretu na liscie maskera wartosc trafia do raportu i JSON (test wycieku potrafi paść)', async () => {
  const secret = secretValue();
  const result = await resultWithSecret(secret);
  const masker = createMasker({});
  assert.ok(formatReport(result, masker).includes(secret));
  assert.ok(JSON.stringify(toJson(result, masker)).includes(secret));
});

test('toJson: wynik jest obiektem JSON-serializowalnym z verdict i pairs', async () => {
  const { result } = await measureOne();
  const j = toJson(result, createMasker({}));
  assert.equal(j.verdict, 'PASS');
  assert.ok(Array.isArray(j.pairs) && j.pairs.length === 1);
  assert.doesNotThrow(() => JSON.stringify(j));
});

// =====================================================================================
// S10: A2 (ksztalt SKIPPED nie daje PASS), A3 (literowka w styleProps), A9 (nieznane klucze),
// C1 (shiftPx), C2 (shapeAlign), C3 (maski per strona), C4 (acceptShape), C5 (page-state-mismatch)
// =====================================================================================

const vlineImg = (w, h, x0) => solid(w, h, (x) => (x === x0 ? BLACK : WHITE));
const DPR2 = { viewport: { width: 400, height: 200, deviceScaleFactor: 2 } };

// --- A2 ---

test('A2: zatwierdzone odstepstwo na width (ksztalt SKIPPED box-mismatch) => para UNVERIFIED, exit 3, nie PASS', async () => {
  const cand = side({ box: { x: 10, y: 10, width: 60, height: 40 }, image: block(4, 60, 40) });
  const { p, result, diffFn } = await measureOne({ cand, pair: { deviations: [{ prop: 'width', reason: 'szerszy przycisk zatwierdzony' }] } });
  assert.equal(p.measure.verdict, 'PASS', 'pomiar przechodzi dzieki odstepstwu');
  assert.ok(p.measure.approved.some((f) => f.prop === 'width'));
  assert.equal(p.shape.verdict, 'SKIPPED');
  assert.equal(p.shape.reason, 'box-mismatch');
  assert.equal(diffFn.calls.length, 0);
  assert.equal(p.verdict, 'UNVERIFIED');
  assert.equal(result.verdict, 'UNVERIFIED');
  assert.equal(result.exit, 3);
  assert.match(formatReport(result, createMasker({})), /^UNVERIFIED p\b/m);
});

test('A2: tolerancePx > sizeTolerancePx (width +4 px przy 5/1) => pomiar PASS, ksztalt SKIPPED, para UNVERIFIED', async () => {
  const cand = side({ box: { x: 10, y: 10, width: 44, height: 40 }, image: block(4, 44, 40) });
  const { p, result } = await measureOne({ cand, top: { tolerancePx: 5, sizeTolerancePx: 1 } });
  assert.equal(p.measure.verdict, 'PASS');
  assert.equal(p.shape.verdict, 'SKIPPED');
  assert.equal(p.verdict, 'UNVERIFIED');
  assert.equal(result.exit, 3);
});

test('A2: FAIL pomiaru nadal wygrywa z SKIPPED (kontrola: bez odstepstwa para FAIL, exit 1)', async () => {
  const cand = side({ box: { x: 10, y: 10, width: 60, height: 40 }, image: block(4, 60, 40) });
  const { p, result } = await measureOne({ cand });
  assert.equal(p.verdict, 'FAIL');
  assert.equal(result.exit, 1);
});

// --- C4 ---

test('C4: acceptShape + ksztalt SKIPPED (box-mismatch) i pomiar PASS => ksztalt ACCEPTED z powodem, para PASS, exit 0', async () => {
  const cand = side({ box: { x: 10, y: 10, width: 60, height: 40 }, image: block(4, 60, 40) });
  const pair = { deviations: [{ prop: 'width', reason: 'szerszy przycisk zatwierdzony' }], acceptShape: { reason: 'inna szerokosc wg projektu' } };
  const { p, result } = await measureOne({ cand, pair });
  assert.equal(p.shape.verdict, 'ACCEPTED');
  assert.equal(p.verdict, 'PASS');
  assert.equal(result.verdict, 'PASS');
  assert.equal(result.exit, 0);
  assert.ok(JSON.stringify(p.shape).includes('inna szerokosc wg projektu'), JSON.stringify(p.shape));
  assert.ok(formatReport(result, createMasker({})).includes('inna szerokosc wg projektu'), 'powod w raporcie tekstowym');
  assert.ok(JSON.stringify(toJson(result, createMasker({}))).includes('inna szerokosc wg projektu'), 'powod w measure.json');
});

test('C4: acceptShape + ksztalt FAIL (lustro) => ACCEPTED, para PASS; bez acceptShape ten sam wejscie to FAIL', async () => {
  const input = { ref: side({ image: IMG_LEFT() }), cand: side({ image: IMG_RIGHT() }) };
  const accepted = await measureOne({ ...input, pair: { acceptShape: { reason: 'lustrzana ikona zatwierdzona' } } });
  assert.equal(accepted.p.shape.verdict, 'ACCEPTED');
  assert.equal(accepted.p.verdict, 'PASS');
  assert.equal(accepted.result.exit, 0);
  const plain = await measureOne(input);
  assert.equal(plain.p.shape.verdict, 'FAIL');
  assert.equal(plain.p.verdict, 'FAIL');
});

test('C4: acceptShape nie ratuje FAIL pomiaru; ksztalt PASS zostaje PASS (nie ACCEPTED)', async () => {
  const bad = await measureOne({ cand: side({ styles: { ...STYLES, fontSize: '30px' } }), pair: { acceptShape: { reason: 'x ksztalt' } } });
  assert.equal(bad.p.verdict, 'FAIL');
  const ok = await measureOne({ pair: { acceptShape: { reason: 'x ksztalt' } } });
  assert.equal(ok.p.shape.verdict, 'PASS');
  assert.equal(ok.p.verdict, 'PASS');
});

test('C4: acceptShape bez niepustego reason (brak, pusty, spacje, nie-napis, nie-obiekt) albo z obcym kluczem => blad walidacji', () => {
  for (const bad of [{}, { reason: '' }, { reason: '   ' }, { reason: 5 }, 'tak', true, { reason: 'ok', extra: 1 }]) {
    const r = V({ version: 1, pairs: [pairCfg({ acceptShape: bad })] });
    assert.equal(r.ok, false, JSON.stringify(bad));
    assert.ok(r.errors.some((e) => /acceptShape/.test(e)), String(r.errors));
  }
  assert.equal(V({ version: 1, pairs: [pairCfg({ acceptShape: { reason: 'zatwierdzone' } })] }).ok, true);
});

// --- A3 ---

test('A3: styleProps z literowka (brak wartosci we wzorcu) => MeasureError code config z nazwa wlasciwosci, nie cichy PASS', async () => {
  for (const refStyles of [{ ...STYLES }, { ...STYLES, colour: '' }]) {
    const config = mkConfig([pairCfg()], { styleProps: ['colour'] });
    const adapter = mkAdapter({ [URL_REF]: side({ styles: refStyles }), [URL_CAND]: side({ styles: refStyles }) });
    await assert.rejects(runMeasure({ config, adapter, diffFn: spyDiff() }), (err) => {
      assert.ok(err instanceof MeasureError, String(err));
      assert.equal(err.code, 'config');
      assert.match(err.message, /colour/);
      return true;
    });
  }
});

test('A3: poprawna wlasciwosc brakujaca tylko u kandydata to nadal finding (FAIL), a nie blad konfiguracji', async () => {
  const cand = side({ styles: { ...STYLES, fontSize: undefined } });
  const { p } = await measureOne({ cand });
  assert.equal(p.measure.verdict, 'FAIL');
  assert.ok(p.measure.unapproved.some((f) => f.prop === 'fontSize'));
});

// --- A9 ---

for (const [label, mutate, fragment, key] of [
  ['pair', (pr) => ({ ...pr, bogus: 1 }), 'pairs[0]', 'bogus'],
  ['reference', (pr) => ({ ...pr, reference: { ...pr.reference, bogusRef: 1 } }), 'pairs[0].reference', 'bogusRef'],
  ['candidate', (pr) => ({ ...pr, candidate: { ...pr.candidate, bogusCand: 1 } }), 'pairs[0].candidate', 'bogusCand'],
  ['deviations[*]', (pr) => ({ ...pr, deviations: [{ prop: 'width', reason: 'r', bogusDev: 1 }] }), 'pairs[0].deviations[0]', 'bogusDev'],
  ['masks[*]', (pr) => ({ ...pr, masks: [{ x: 0, y: 0, width: 1, height: 1, bogusMask: 1 }] }), 'pairs[0].masks[0]', 'bogusMask'],
  ['masksReference[*]', (pr) => ({ ...pr, masksReference: [{ x: 0, y: 0, width: 1, height: 1, bogusMr: 1 }] }), 'pairs[0].masksReference[0]', 'bogusMr'],
  ['masksCandidate[*]', (pr) => ({ ...pr, masksCandidate: [{ x: 0, y: 0, width: 1, height: 1, bogusMc: 1 }] }), 'pairs[0].masksCandidate[0]', 'bogusMc'],
]) {
  test(`A9 validateConfig: nieznany klucz w ${label} => blad z nazwa klucza i sciezka`, () => {
    const r = V({ version: 1, pairs: [mutate(pairCfg())] });
    assert.equal(r.ok, false);
    assert.ok(r.errors.some((e) => e.includes(key) && e.includes(fragment)), String(r.errors));
  });
}

test('A9 validateConfig: literowka w kluczu opcjonalnym (np. deviation.reasn) to blad, a nie cicho pominiete odstepstwo', () => {
  const r = V({ version: 1, pairs: [pairCfg({ deviations: [{ prop: 'width', reason: 'r', actual: 1, expected: 2, selector: '#t' }] })] });
  assert.equal(r.ok, true, String(r.errors));
  const bad = V({ version: 1, pairs: [pairCfg({ deviations: [{ prop: 'width', reasn: 'r', reason: 'r' }] })] });
  assert.equal(bad.ok, false);
});

// --- C1 ---

test('C1: shiftPx domyslnie ceil(deviceScaleFactor) trafia do diffFn (DPR 1 => 1, DPR 2 => 2, DPR 1.5 => 2); jawne shiftPx wygrywa (takze 0)', async () => {
  const dpr = async (top) => (await measureOne({ top })).diffFn.calls[0].shiftPx;
  assert.equal(await dpr({}), 1);
  assert.equal(await dpr(DPR2), 2);
  assert.equal(await dpr({ viewport: { width: 400, height: 200, deviceScaleFactor: 1.5 } }), 2);
  assert.equal(await dpr({ ...DPR2, shiftPx: 3 }), 3);
  assert.equal(await dpr({ shiftPx: 0 }), 0);
});

test('C1: DPR 2 - przesuniecie o 2 px ekranu (1 px CSS) PASS, o 4 px FAIL', async () => {
  const ref = side({ image: vlineImg(100, 100, 50) });
  const ok = await measureOne({ ref, cand: side({ image: vlineImg(100, 100, 52) }), top: DPR2 });
  assert.equal(ok.p.shape.verdict, 'PASS', JSON.stringify(ok.p.shape));
  assert.equal(ok.p.verdict, 'PASS');
  const bad = await measureOne({ ref, cand: side({ image: vlineImg(100, 100, 54) }), top: DPR2 });
  assert.equal(bad.p.shape.verdict, 'FAIL');
});

test('C1: DPR 1 - przesuniecie o 2 px FAIL (domyslne shiftPx 1)', async () => {
  const { p } = await measureOne({ ref: side({ image: vlineImg(100, 100, 50) }), cand: side({ image: vlineImg(100, 100, 52) }) });
  assert.equal(p.shape.verdict, 'FAIL');
});

for (const [name, v] of [['ujemne', -1], ['ulamkowe', 1.5], ['napis', '2'], ['NaN-owe', Number.NaN]]) {
  test(`C1 validateConfig: shiftPx ${name} => blad (calkowita >= 0)`, () => {
    const r = V({ ...okCfg(), shiftPx: v });
    assert.equal(r.ok, false);
    assert.ok(r.errors.some((e) => e.includes('shiftPx')), String(r.errors));
  });
}

test('C1 validateConfig: shiftPx 0 i 3 sa poprawne', () => {
  assert.equal(V({ ...okCfg(), shiftPx: 0 }).ok, true);
  assert.equal(V({ ...okCfg(), shiftPx: 3 }).ok, true);
});

// --- C2 ---

test('C2: diffFn dostaje align center domyslnie; shapeAlign start nadpisuje', async () => {
  assert.equal((await measureOne()).diffFn.calls[0].align, 'center');
  assert.equal((await measureOne({ top: { shapeAlign: 'start' } })).diffFn.calls[0].align, 'start');
  assert.equal((await measureOne({ top: { shapeAlign: 'center' } })).diffFn.calls[0].align, 'center');
});

test('C2: obrazy o roznej szerokosci (pudelka w sizeTolerancePx) - ksztalt wyrownany do srodka PASS; shapeAlign start FAIL', async () => {
  const ref = side({ image: vlineImg(16, 10, 8) });
  const cand = side({ box: { x: 10, y: 10, width: 41, height: 40 }, image: vlineImg(10, 10, 5) });
  const centered = await measureOne({ ref, cand });
  assert.equal(centered.p.shape.verdict, 'PASS', JSON.stringify(centered.p.shape));
  assert.equal(centered.p.shape.cropped, true);
  const start = await measureOne({ ref, cand, top: { shapeAlign: 'start' } });
  assert.equal(start.p.shape.verdict, 'FAIL');
});

for (const [name, v] of [['right', 'right'], ['wielkie litery', 'Center'], ['liczba', 1], ['null', null]]) {
  test(`C2 validateConfig: shapeAlign ${name} => blad (center|start)`, () => {
    const r = V({ ...okCfg(), shapeAlign: v });
    assert.equal(r.ok, false);
    assert.ok(r.errors.some((e) => e.includes('shapeAlign')), String(r.errors));
  });
}

// --- C3 ---

const sameRect = (a, b) => ['x', 'y', 'width', 'height'].every((k) => a[k] === b[k]);

test('C3: diffFn dostaje SUME masks, masksReference i masksCandidate (kazdy prostokat raz)', async () => {
  const shared = { x: 0, y: 0, width: 4, height: 4 };
  const mr = { x: 10, y: 10, width: 5, height: 5 };
  const mc = { x: 20, y: 20, width: 6, height: 6 };
  const { diffFn } = await measureOne({ pair: { masks: [shared], masksReference: [mr], masksCandidate: [mc] } });
  const got = diffFn.calls[0].masks;
  assert.equal(got.length, 3, JSON.stringify(got));
  for (const want of [shared, mr, mc]) assert.ok(got.some((g) => sameRect(g, want)), `brak maski ${JSON.stringify(want)}`);
});

test('C3: element zmienny tylko u wzorca (maska w masksReference) nie daje roznicy; bez maski FAIL', async () => {
  const withExtra = solid(40, 40, (x, y) => (x >= 10 && x < 30 && y >= 10 && y < 30 ? BLACK : WHITE));
  const blank = solid(40, 40, () => WHITE);
  const region = { x: 8, y: 8, width: 24, height: 24 };
  const masked = await measureOne({ ref: side({ image: withExtra }), cand: side({ image: blank }), pair: { masksReference: [region] } });
  assert.equal(masked.p.shape.verdict, 'PASS', JSON.stringify(masked.p.shape));
  assert.equal(masked.p.shape.diffCount, 0);
  const plain = await measureOne({ ref: side({ image: withExtra }), cand: side({ image: blank }) });
  assert.equal(plain.p.shape.verdict, 'FAIL');
});

test('C3: maska z masksCandidate tez zamalowuje obie strony (wynik symetryczny)', async () => {
  const withExtra = solid(40, 40, (x, y) => (x >= 10 && x < 30 && y >= 10 && y < 30 ? BLACK : WHITE));
  const blank = solid(40, 40, () => WHITE);
  const region = { x: 8, y: 8, width: 24, height: 24 };
  const { p } = await measureOne({ ref: side({ image: blank }), cand: side({ image: withExtra }), pair: { masksCandidate: [region] } });
  assert.equal(p.shape.verdict, 'PASS', JSON.stringify(p.shape));
});

test('C3 validateConfig: masksReference/masksCandidate poprawne prostokaty ok; ujemne wymiary albo nie-lista => blad', () => {
  const rect = { x: 0, y: 0, width: 5, height: 5 };
  assert.equal(V({ version: 1, pairs: [pairCfg({ masksReference: [rect], masksCandidate: [rect] })] }).ok, true);
  for (const key of ['masksReference', 'masksCandidate']) {
    assert.equal(V({ version: 1, pairs: [pairCfg({ [key]: [{ x: 0, y: 0, width: -1, height: 5 }] })] }).ok, false, `${key} ujemny`);
    assert.equal(V({ version: 1, pairs: [pairCfg({ [key]: rect })] }).ok, false, `${key} nie lista`);
  }
});

// --- C5 ---

test('C5: adapter zglasza page-state-mismatch (przekierowanie) => para UNVERIFIED z powodem page-state-mismatch, exit 3, nie PASS/FAIL', async () => {
  const mismatch = { found: false, box: null, styles: {}, image: null, reason: 'page-state-mismatch' };
  for (const [ref, cand] of [[side(), side(mismatch)], [side(mismatch), side()]]) {
    const { p, result } = await measureOne({ ref, cand });
    assert.equal(p.verdict, 'UNVERIFIED');
    assert.equal(p.reason, 'page-state-mismatch');
    assert.equal(result.verdict, 'UNVERIFIED');
    assert.equal(result.exit, 3);
    assert.match(formatReport(result, createMasker({})), /^UNVERIFIED p page-state-mismatch/m);
    assert.equal(toJson(result, createMasker({})).pairs[0].reason, 'page-state-mismatch');
  }
});

test('C5: zwykly brak elementu zachowuje powod element-not-found (kontrola, ze powody sa rozrozniane)', async () => {
  const { p } = await measureOne({ cand: side({ found: false, box: null, styles: {}, image: null }) });
  assert.equal(p.reason, 'element-not-found');
});

test('N6 validateConfig: duplikat id bez rozroznienia wielkosci liter oraz id "." i ".." sa odrzucane', () => {
  const dup = V({ version: 1, pairs: [pairCfg({ id: 'Home' }), pairCfg({ id: 'home' })] });
  assert.equal(dup.ok, false);
  assert.ok(dup.errors.some((e) => /duplicate id/.test(e)), dup.errors.join('; '));
  for (const id of ['.', '..']) {
    const r = V({ version: 1, pairs: [pairCfg({ id })] });
    assert.equal(r.ok, false, id);
    assert.ok(r.errors.some((e) => /\.id/.test(e)), r.errors.join('; '));
  }
  assert.equal(V({ version: 1, pairs: [pairCfg({ id: 'a.b' }), pairCfg({ id: 'a-b' })] }).ok, true);
});
