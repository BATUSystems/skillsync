// Czysta logika lib/capture-core.mjs (S5): validateCaptureConfig, shotName, runCapture z atrapa adaptera, manifest i raport z maskowaniem.
// Bez przegladarki i bez PNG: adapter zwraca bajty atrapy.
import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const LIB = path.join(HERE, '..', '..', 'library', 'skills', 'ui-verify', 'scripts', 'lib');
const lib = (name) => import(pathToFileURL(path.join(LIB, name)).href);

const core = await lib('capture-core.mjs');
const { createMasker } = await lib('out.mjs');
const { validateCaptureConfig, runCapture, shotName, manifest, formatCaptureReport, DEFAULTS, CaptureError } = core;

const CONFIG_DIR = path.join(path.sep, 'tmp', 'uv-capture-core-cfg');
const URL_HOME = 'http://localhost/home';

const validate = (cfg, allowOrigins = []) => validateCaptureConfig(cfg, { configDir: CONFIG_DIR, allowOrigins });
const pageCfg = (over = {}) => {
  const p = { id: 'home', url: URL_HOME, assert: { selector: 'h1' }, ...over };
  for (const k of Object.keys(p)) if (p[k] === undefined) delete p[k];
  return p;
};
function mkConfig(top = {}, pages = [pageCfg()]) {
  const v = validate({ version: 1, pages, ...top });
  assert.equal(v.ok, true, `konfiguracja testowa niepoprawna: ${v.errors}`);
  return v.config;
}
const PNG_BYTES = Buffer.from([137, 80, 78, 71, 1, 2, 3, 4]);
function mkAdapter({ found = true, failOn } = {}) {
  const calls = [];
  return {
    calls,
    async login() {},
    async capture(arg) {
      calls.push({ page: arg.page.id ?? arg.page, width: arg.width, mode: arg.mode });
      if (failOn && failOn(arg)) throw new Error('page closed');
      return { png: PNG_BYTES, assertionFound: typeof found === 'function' ? found(arg) : found };
    },
  };
}

// --- walidacja ---

test('walidacja: minimalna konfiguracja ok, DEFAULTS eksportowane', () => {
  const r = validate({ version: 1, pages: [pageCfg()] });
  assert.equal(r.ok, true, String(r.errors));
  assert.deepEqual(r.errors, []);
  assert.ok(DEFAULTS && typeof DEFAULTS === 'object');
});

test('walidacja: wersja inna niz 1, nieznany klucz (takze na stronie i w login) => bledy', () => {
  assert.equal(validate({ version: 2, pages: [pageCfg()] }).ok, false);
  const top = validate({ version: 1, pages: [pageCfg()], widthz: [300] });
  assert.equal(top.ok, false);
  assert.ok(top.errors.some((e) => e.includes('widthz')), String(top.errors));
  const pg = validate({ version: 1, pages: [pageCfg({ fullpage: true })] });
  assert.equal(pg.ok, false);
  assert.ok(pg.errors.some((e) => e.includes('fullpage')), String(pg.errors));
  assert.equal(validate(null).ok, false);
});

test('walidacja: id stron unikalne i tylko [A-Za-z0-9._-]', () => {
  assert.equal(validate({ version: 1, pages: [pageCfg(), pageCfg()] }).ok, false, 'duplikat id');
  for (const id of ['a b', 'a/b', '..', '', 'a#1']) {
    assert.equal(validate({ version: 1, pages: [pageCfg({ id })] }).ok, false, `id ${JSON.stringify(id)}`);
  }
  assert.equal(validate({ version: 1, pages: [] }).ok, false, 'brak stron');
});

test('walidacja: niedozwolony origin => blad; allowOrigins go dopuszcza; login.url tez jest sprawdzany', () => {
  const bad = validate({ version: 1, pages: [pageCfg({ url: 'https://app.example.test/' })] });
  assert.equal(bad.ok, false);
  assert.ok(bad.errors.some((e) => /origin|allow/i.test(e)), String(bad.errors));
  assert.equal(validate({ version: 1, pages: [pageCfg({ url: 'https://app.example.test/' })] }, ['app.example.test']).ok, true);
  const login = validate({ version: 1, login: { url: 'https://login.example.test/', steps: [{ click: '#go' }] }, pages: [pageCfg()] });
  assert.equal(login.ok, false);
});

test('walidacja: modes tylko light|dark, widths calkowite 200..4000', () => {
  assert.equal(validate({ version: 1, modes: ['light', 'dark'], pages: [pageCfg()] }).ok, true);
  assert.equal(validate({ version: 1, modes: ['sepia'], pages: [pageCfg()] }).ok, false);
  assert.equal(validate({ version: 1, modes: 'dark', pages: [pageCfg()] }).ok, false);
  assert.equal(validate({ version: 1, widths: [200, 4000], pages: [pageCfg()] }).ok, true);
  for (const w of [199, 4001, 300.5, '300', 0, -5]) {
    assert.equal(validate({ version: 1, widths: [w], pages: [pageCfg()] }).ok, false, `szerokosc ${w}`);
  }
});

test('walidacja: viewport i liczby dodatnie', () => {
  assert.equal(validate({ version: 1, viewport: { width: 0, height: 800 }, pages: [pageCfg()] }).ok, false);
  assert.equal(validate({ version: 1, viewport: { width: 1280, height: -1 }, pages: [pageCfg()] }).ok, false);
});

test('walidacja: login.steps tylko fill/click; fill wymaga valueEnv, literalne value => blad (sekret w konfiguracji zabroniony)', () => {
  const ok = validate({ version: 1, login: { url: URL_HOME, steps: [{ fill: { selector: '#u', valueEnv: 'ENV1' } }, { click: '#go' }] }, pages: [pageCfg()] });
  assert.equal(ok.ok, true, String(ok.errors));
  const literal = validate({ version: 1, login: { url: URL_HOME, steps: [{ fill: { selector: '#u', value: 'tajne' } }] }, pages: [pageCfg()] });
  assert.equal(literal.ok, false);
  assert.ok(literal.errors.some((e) => /value/i.test(e)), String(literal.errors));
  assert.ok(!literal.errors.some((e) => e.includes('tajne')), 'blad nie moze powtarzac literalnego sekretu');
  assert.equal(validate({ version: 1, login: { url: URL_HOME, steps: [{ fill: { selector: '#u' } }] }, pages: [pageCfg()] }).ok, false, 'fill bez valueEnv');
  assert.equal(validate({ version: 1, login: { url: URL_HOME, steps: [{ eval: 'x' }] }, pages: [pageCfg()] }).ok, false, 'nieznany krok');
});

test('walidacja: assert wymaga selector; maski to selektor albo prostokat o dodatnich wymiarach', () => {
  assert.equal(validate({ version: 1, pages: [pageCfg({ assert: { text: 'x' } })] }).ok, false);
  assert.equal(validate({ version: 1, pages: [pageCfg({ assert: { selector: 'h1', text: 'Hello' } })] }).ok, true);
  assert.equal(validate({ version: 1, pages: [pageCfg({ masks: [{ selector: '.clock' }, { x: 0, y: 0, width: 10, height: 10 }] })] }).ok, true);
  assert.equal(validate({ version: 1, pages: [pageCfg({ masks: [{ x: 0, y: 0, width: 0, height: 10 }] })] }).ok, false);
  assert.equal(validate({ version: 1, pages: [pageCfg({ masks: [{}] })] }).ok, false);
  assert.equal(validate({ version: 1, pages: [pageCfg({ waitFor: '#app', fullPage: true })] }).ok, true);
});

// --- shotName ---

test('shotName: <id>__<szerokosc>-<tryb>.png', () => {
  assert.equal(shotName('home', 375, 'dark'), 'home__375-dark.png');
  assert.equal(shotName('a.b_c-1', 1280, 'light'), 'a.b_c-1__1280-light.png');
});

// --- runCapture ---

test('runCapture: macierz strony x szerokosci x tryby, kazda kombinacja raz, wszystkie PASS => exit 0', async () => {
  const config = mkConfig({ widths: [375, 1280], modes: ['light', 'dark'] }, [pageCfg(), pageCfg({ id: 'other' })]);
  const adapter = mkAdapter();
  const r = await runCapture({ config, adapter, env: {} });
  assert.equal(r.verdict, 'PASS');
  assert.equal(r.exit, 0);
  assert.equal(r.shots.length, 8);
  assert.equal(adapter.calls.length, 8);
  assert.deepEqual(new Set(r.shots.map((s) => s.name)).size, 8);
  assert.ok(r.shots.some((s) => s.name === 'home__375-dark.png' && s.width === 375 && s.mode === 'dark' && s.pageId === 'home'));
  for (const s of r.shots) assert.equal(s.assertion, 'PASS');
});

test('runCapture: domyslnie jedna szerokosc z viewportu i tryb light', async () => {
  const config = mkConfig({ viewport: { width: 1000, height: 700, deviceScaleFactor: 1 } });
  const r = await runCapture({ config, adapter: mkAdapter(), env: {} });
  assert.equal(r.shots.length, 1);
  assert.equal(r.shots[0].name, 'home__1000-light.png');
});

test('runCapture: strona bez assert => assertion NONE, UNVERIFIED, exit 3 (nigdy PASS)', async () => {
  const config = mkConfig({}, [pageCfg({ assert: undefined })]);
  const r = await runCapture({ config, adapter: mkAdapter({ found: true }), env: {} });
  assert.equal(r.shots[0].assertion, 'NONE');
  assert.equal(r.verdict, 'UNVERIFIED');
  assert.equal(r.exit, 3);
});

test('runCapture: assert podany, a element nie znaleziony => FAIL assertion-failed, exit 1', async () => {
  const r = await runCapture({ config: mkConfig(), adapter: mkAdapter({ found: false }), env: {} });
  assert.equal(r.shots[0].assertion, 'FAIL');
  assert.equal(r.verdict, 'FAIL');
  assert.equal(r.exit, 1);
  assert.match(formatCaptureReport(r, createMasker()), /assertion-failed/);
});

test('runCapture: FAIL wygrywa z UNVERIFIED i PASS; UNVERIFIED wygrywa z PASS', async () => {
  const pages = [pageCfg({ id: 'ok' }), pageCfg({ id: 'none', assert: undefined }), pageCfg({ id: 'bad' })];
  const config = mkConfig({}, pages);
  const mixed = await runCapture({ config, adapter: mkAdapter({ found: (a) => (a.page.id ?? a.page) !== 'bad' }), env: {} });
  assert.equal(mixed.verdict, 'FAIL');
  assert.equal(mixed.exit, 1);
  const noFail = await runCapture({ config: mkConfig({}, pages.slice(0, 2)), adapter: mkAdapter(), env: {} });
  assert.equal(noFail.verdict, 'UNVERIFIED');
  assert.equal(noFail.exit, 3);
});

test('runCapture: bajty PNG dostepne w shots[i].png, ale nieenumerowalne (nie trafiaja do JSON ani Object.keys)', async () => {
  const r = await runCapture({ config: mkConfig(), adapter: mkAdapter(), env: {} });
  const s = r.shots[0];
  assert.deepEqual(Buffer.from(s.png), PNG_BYTES);
  assert.ok(!Object.keys(s).includes('png'));
  assert.ok(!JSON.stringify(r).includes('"png"'));
});

test('runCapture: wyjatek adaptera => odrzucenie CaptureError z code "adapter" (nie wynik FAIL)', async () => {
  const adapter = mkAdapter({ failOn: () => true });
  await assert.rejects(runCapture({ config: mkConfig(), adapter, env: {} }), (err) => {
    assert.ok(err instanceof CaptureError, String(err));
    assert.equal(err.code, 'adapter');
    return true;
  });
});

test('manifest: tylko name/pageId/width/mode/assertion, bez bajtow', async () => {
  const r = await runCapture({ config: mkConfig({ widths: [375], modes: ['dark'] }), adapter: mkAdapter(), env: {} });
  const m = manifest(r);
  assert.equal(m.version, 1);
  assert.deepEqual(m.shots, [{ name: 'home__375-dark.png', pageId: 'home', width: 375, mode: 'dark', assertion: 'PASS' }]);
  assert.ok(!JSON.stringify(m).includes('"png"'));
});

test('formatCaptureReport: maskuje sekret zlozony w czasie wykonania (id strony), kontrola bez maskera', async () => {
  const secret = ['fake', 'secret', String(process.pid), 'abcd'].join('-');
  const config = mkConfig({}, [pageCfg({ id: `p-${secret}` })]);
  const r = await runCapture({ config, adapter: mkAdapter(), env: { UV_FAKE_SECRET: secret } });
  const masked = formatCaptureReport(r, createMasker({ secrets: [secret] }));
  assert.ok(!masked.includes(secret), masked);
  assert.ok(formatCaptureReport(r, createMasker()).includes(secret), 'kontrola: bez maskera id jest w raporcie');
});
