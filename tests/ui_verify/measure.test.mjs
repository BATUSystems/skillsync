// Proces potomny `node scripts/measure.mjs` (S4): naglowek, --selftest, kody 2/3 bez przegladarki, import bez efektow.
// Adapter Playwright jest pokryty w measure-e2e.test.mjs (z prawdziwym Chrome).
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const SKILL = path.join(ROOT, 'library', 'skills', 'ui-verify');
const SCRIPT = path.join(SKILL, 'scripts', 'measure.mjs');
const DEPS_URL = pathToFileURL(path.join(SKILL, 'scripts', 'lib', 'deps.mjs')).href;
const HEADER_URL = pathToFileURL(path.join(SKILL, 'scripts', 'lib', 'header.mjs')).href;

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-measure-'));
after(() => fs.rmSync(tmpRoot, { recursive: true, force: true }));
let counter = 0;
const newDir = (label = 'd') => {
  const d = path.join(tmpRoot, `${label}-${counter++}`);
  fs.mkdirSync(d, { recursive: true });
  return d;
};
const emptyCache = () => newDir('emptycache');

// Cache z playwright-core, pngjs i pixelmatch (opcjonalny; testy zalezne od niego sa pomijane z powodem).
function findCacheRoot() {
  const root = process.env.UI_VERIFY_TEST_CACHE;
  if (!root) return { reason: 'needs-ui-verify-deps: UI_VERIFY_TEST_CACHE nie ustawione' };
  try {
    const sub = fs.readdirSync(root).find((d) => ['playwright-core', 'pngjs', 'pixelmatch'].every((n) => fs.existsSync(path.join(root, d, 'node_modules', n, 'package.json'))));
    if (!sub) return { reason: 'needs-ui-verify-deps: brak playwright-core/pngjs/pixelmatch w UI_VERIFY_TEST_CACHE' };
    return { root, cacheDir: path.join(root, sub) };
  } catch (e) {
    return { reason: `needs-ui-verify-deps: ${e.code || e.message}` };
  }
}
const cache = findCacheRoot();
const SKIP = cache.cacheDir ? false : cache.reason;

function run(args, { cacheRoot, cwd, env = {} } = {}) {
  const base = { ...process.env };
  for (const k of Object.keys(base)) if (k.startsWith('UI_VERIFY_') || k === 'PLAYWRIGHT_MODULE_PATH') delete base[k];
  const r = spawnSync(process.execPath, [SCRIPT, ...args], {
    cwd: cwd ?? newDir('cwd'),
    env: { ...base, UI_VERIFY_CACHE_DIR: cacheRoot ?? cache.root ?? emptyCache(), ...env },
    encoding: 'utf8',
    timeout: 120000,
  });
  return { code: r.status, stdout: r.stdout ?? '', stderr: r.stderr ?? '', error: r.error };
}

// Katalog konfiguracji z poprawna konfiguracja (strona to plik HTML obok niej).
function project(configOver = {}) {
  const dir = newDir('proj');
  fs.writeFileSync(path.join(dir, 'page.html'), '<!doctype html><html><body><div id="t" style="width:10px;height:10px"></div></body></html>');
  const cfg = {
    version: 1,
    pairs: [{ id: 'p', reference: { url: 'page.html', selector: '#t' }, candidate: { url: 'page.html', selector: '#t' } }],
    ...configOver,
  };
  const file = path.join(dir, 'config.json');
  fs.writeFileSync(file, JSON.stringify(cfg));
  return { dir, file };
}

// --- naglowek i import ---

test('naglowek measure.mjs przechodzi parseHeader().ok i zawiera przypiete zaleznosci', async () => {
  assert.ok(fs.existsSync(SCRIPT), 'brak scripts/measure.mjs');
  const { parseHeader } = await import(HEADER_URL);
  const text = fs.readFileSync(SCRIPT, 'utf8');
  const h = parseHeader(text);
  assert.equal(h.ok, true, `brakuje: ${h.missing}, kolejnosc: ${h.order}`);
  const block = /\/\*[\s\S]*?\*\//.exec(text)[0];
  assert.match(block, /playwright-core 1\.65\.0/);
  assert.match(block, /pngjs 7\.0\.0/);
  assert.match(block, /pixelmatch 8\.0\.0/);
  assert.match(block, /Chrome/);
});

test('import measure.mjs nie uruchamia main() i nic nie wypisuje', () => {
  const r = spawnSync(process.execPath, ['--input-type=module', '-e', `await import(${JSON.stringify(pathToFileURL(SCRIPT).href)}); process.stdout.write('imported');`], { cwd: newDir('imp'), encoding: 'utf8', timeout: 60000 });
  assert.equal(r.status, 0, r.stderr);
  assert.equal(r.stdout, 'imported');
});

test('import measure.mjs nie laduje zaleznosci (brak cache nie przeszkadza)', () => {
  const r = spawnSync(process.execPath, ['--input-type=module', '-e', `await import(${JSON.stringify(pathToFileURL(SCRIPT).href)}); process.stdout.write('imported');`], {
    cwd: newDir('imp2'), encoding: 'utf8', timeout: 60000, env: { ...process.env, UI_VERIFY_CACHE_DIR: emptyCache() },
  });
  assert.equal(r.status, 0, r.stderr);
  assert.equal(r.stdout, 'imported');
});

// --- --selftest ---

test('--selftest bez cache zaleznosci => kod 0 (atrapa adaptera, bez PNG i przegladarki)', () => {
  const r = run(['--selftest'], { cacheRoot: emptyCache() });
  assert.equal(r.code, 0, r.stdout + r.stderr);
  assert.doesNotMatch(r.stdout, /^FAIL /m);
  const m = /selftest [^\n]*?(\d+)\/(\d+) passed/.exec(r.stdout);
  assert.ok(m, `brak podsumowania selftestu: ${r.stdout}`);
  assert.equal(m[1], m[2]);
  assert.ok(Number(m[2]) >= 9, `za malo przypadkow selftestu: ${m[2]}`);
});

test('--selftest nie wymaga --config ani outDir; --allow-trace jest akceptowane', () => {
  assert.equal(run(['--selftest', '--allow-trace'], { cacheRoot: emptyCache() }).code, 0);
});

// --- kody 2 (uzycie, konfiguracja) ---

test('brak --config => kod 2 i komunikat uzycia na stderr', () => {
  const r = run([newDir('o')], { cacheRoot: emptyCache() });
  assert.equal(r.code, 2, r.stdout + r.stderr);
  assert.notEqual(r.stderr.trim(), '');
});

test('brak argumentow, brak outDir albo nieznana flaga => kod 2', () => {
  const { file } = project();
  assert.equal(run([], { cacheRoot: emptyCache() }).code, 2);
  assert.equal(run(['--config', file], { cacheRoot: emptyCache() }).code, 2, 'brak outDir');
  assert.equal(run(['--bogus', '--config', file, 'out'], { cacheRoot: emptyCache() }).code, 2);
});

test('zla konfiguracja (nieznany klucz) => kod 2, nie 1 ani 3; wynik nie powstaje', () => {
  const { dir, file } = project({ tolerancePxx: 1 });
  const r = run(['--config', file, 'out'], { cacheRoot: emptyCache(), cwd: dir });
  assert.equal(r.code, 2, r.stdout + r.stderr);
  assert.match(r.stderr, /tolerancePxx/);
  assert.equal(fs.existsSync(path.join(dir, 'out', 'measure.json')), false);
});

test('odstepstwo bez reason => kod 2', () => {
  const { dir, file } = project({
    pairs: [{ id: 'p', reference: { url: 'page.html', selector: '#t' }, candidate: { url: 'page.html', selector: '#t' }, deviations: [{ prop: 'width' }] }],
  });
  assert.equal(run(['--config', file, 'out'], { cacheRoot: emptyCache(), cwd: dir }).code, 2);
});

test('niedozwolony origin w konfiguracji => kod 2; --allow-origin go dopuszcza (wtedy brak zaleznosci => 3)', () => {
  const ext = { version: 1, pairs: [{ id: 'p', reference: { url: 'https://app.example.test/', selector: '#t' }, candidate: { url: 'page.html', selector: '#t' } }] };
  const { dir, file } = project(ext);
  assert.equal(run(['--config', file, 'out'], { cacheRoot: emptyCache(), cwd: dir }).code, 2);
  const allowed = run(['--allow-origin', 'app.example.test', '--config', file, 'out'], { cacheRoot: emptyCache(), cwd: dir });
  assert.equal(allowed.code, 3, allowed.stdout + allowed.stderr);
});

test('plik konfiguracji nie istnieje albo nie jest JSON => kod 2', () => {
  const dir = newDir('badjson');
  assert.equal(run(['--config', path.join(dir, 'brak.json'), 'out'], { cacheRoot: emptyCache(), cwd: dir }).code, 2);
  const junk = path.join(dir, 'junk.json');
  fs.writeFileSync(junk, '{ to nie jest json');
  assert.equal(run(['--config', junk, 'out'], { cacheRoot: emptyCache(), cwd: dir }).code, 2);
});

test('outDir poza katalogiem konfiguracji (../ albo sciezka bezwzgledna) => kod 2 i brak zapisu', () => {
  const { dir, file } = project();
  const outside = path.join(path.dirname(dir), `outside-${counter++}`);
  const r1 = run(['--config', file, path.join('..', 'escaped')], { cacheRoot: emptyCache(), cwd: dir });
  assert.equal(r1.code, 2, r1.stdout + r1.stderr);
  assert.equal(fs.existsSync(path.join(path.dirname(dir), 'escaped')), false);
  const r2 = run(['--config', file, outside], { cacheRoot: emptyCache(), cwd: dir });
  assert.equal(r2.code, 2, r2.stdout + r2.stderr);
  assert.equal(fs.existsSync(outside), false);
});

// --- kod 3 (zaleznosci, Chrome) ---

test('brak zaleznosci (UI_VERIFY_CACHE_DIR = pusty katalog) przy poprawnej konfiguracji => kod 3, nigdy 0/1/2', () => {
  const { dir, file } = project();
  const r = run(['--config', file, 'out'], { cacheRoot: emptyCache(), cwd: dir });
  assert.equal(r.code, 3, r.stdout + r.stderr);
  assert.doesNotMatch(r.stdout, /^(PASS|FAIL) /m);
  assert.equal(fs.existsSync(path.join(dir, 'out', 'measure.json')), false);
});

test('brak Chrome (UI_VERIFY_CHROME na nieistniejacy plik, PATH pusty) => kod 3, nigdy 0/1/2', { skip: SKIP || (process.platform === 'linux' ? false : 'needs-linux: systemowy Chrome na innych systemach jest szukany poza PATH') }, () => {
  const { dir, file } = project();
  const r = run(['--config', file, 'out'], {
    cwd: dir,
    env: { UI_VERIFY_CHROME: path.join(dir, 'nie-ma-chrome'), CHROME_EXECUTABLE_PATH: '', PATH: newDir('emptypath') },
  });
  assert.equal(r.code, 3, r.stdout + r.stderr);
  assert.doesNotMatch(r.stdout, /^(PASS|FAIL) /m);
  assert.match(r.stdout + r.stderr, /chrome/i);
});

test('sekret z UI_VERIFY_SECRET_ENV w sciezce konfiguracji nie trafia do stdout/stderr (kod 2)', () => {
  const secret = ['fake', 'secret', String(process.pid), 'abcd'].join('-');
  const dir = newDir('leak');
  const r = run(['--config', path.join(dir, `${secret}.json`), 'out'], {
    cacheRoot: emptyCache(), cwd: dir, env: { UV_FAKE_SECRET: secret, UI_VERIFY_SECRET_ENV: 'UV_FAKE_SECRET' },
  });
  assert.equal(r.code, 2, r.stdout + r.stderr);
  assert.ok(!r.stdout.includes(secret) && !r.stderr.includes(secret), 'sekret w wyjsciu');
  const control = run(['--config', path.join(dir, `${secret}.json`), 'out'], { cacheRoot: emptyCache(), cwd: dir, env: { UV_FAKE_SECRET: secret } });
  assert.ok((control.stdout + control.stderr).includes(secret), 'kontrola: bez maskowania sciezka jest w bledzie');
});
