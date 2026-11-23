// Proces potomny `node scripts/install.mjs` (S6): naglowek, --selftest, kody 2/3, idempotencja na gotowej instalacji, import bez efektow.
// Test z prawdziwym npm i siecia dziala tylko z UI_VERIFY_TEST_NETWORK=1 i instaluje wylacznie do katalogu tymczasowego.
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
const SCRIPT = path.join(SKILL, 'scripts', 'install.mjs');
const PIXEL_DIFF = path.join(SKILL, 'scripts', 'pixel-diff.mjs');
const HEADER_URL = pathToFileURL(path.join(SKILL, 'scripts', 'lib', 'header.mjs')).href;
const RUNTIME_URL = pathToFileURL(path.join(SKILL, 'scripts', 'lib', 'runtime.mjs')).href;

const WIN = process.platform === 'win32';
const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-install-'));
const strays = [];
after(() => {
  fs.rmSync(tmpRoot, { recursive: true, force: true });
  for (const p of strays) fs.rmSync(p, { recursive: true, force: true });
});
let counter = 0;
const newDir = (label = 'd') => {
  const d = path.join(tmpRoot, `${label}-${counter++}`);
  fs.mkdirSync(d, { recursive: true, mode: 0o700 });
  if (!WIN) fs.chmodSync(d, 0o700);
  return d;
};

// Srodowisko dziecka bez zmiennych dewelopera; cache ZAWSZE wskazuje katalog tymczasowy (nigdy cache dewelopera).
function run(args, { cacheRoot, cwd, env = {}, clean = false } = {}) {
  const base = { ...process.env };
  for (const k of Object.keys(base)) {
    if (k.startsWith('UI_VERIFY_') || k === 'PLAYWRIGHT_MODULE_PATH' || (clean && k.startsWith('npm_'))) delete base[k];
  }
  const r = spawnSync(process.execPath, [SCRIPT, ...args], {
    cwd: cwd ?? newDir('cwd'),
    env: { ...base, UI_VERIFY_CACHE_DIR: cacheRoot ?? newDir('cache'), ...env },
    encoding: 'utf8',
    timeout: 120000,
  });
  return { code: r.status, stdout: r.stdout ?? '', stderr: r.stderr ?? '', error: r.error };
}

// Gotowa (udawana) instalacja: <root>/<hash>/ z lockfile skilla, paczkami o wersjach z lockfile i znacznikiem gotowosci.
async function fakeInstalled(cacheRoot, { marker = true } = {}) {
  const { resolveCacheDir } = await import(RUNTIME_URL);
  const dir = resolveCacheDir({ skillDir: SKILL, env: { UI_VERIFY_CACHE_DIR: cacheRoot }, platform: process.platform, homedir: tmpRoot });
  fs.mkdirSync(dir, { recursive: true });
  const lockText = fs.readFileSync(path.join(SKILL, 'package-lock.json'), 'utf8');
  fs.writeFileSync(path.join(dir, 'package-lock.json'), lockText);
  fs.copyFileSync(path.join(SKILL, 'package.json'), path.join(dir, 'package.json'));
  const lock = JSON.parse(lockText);
  for (const name of ['playwright-core', 'pngjs', 'pixelmatch', 'sass.js']) {
    const version = lock.packages[`node_modules/${name}`].version;
    const root = path.join(dir, 'node_modules', name);
    fs.mkdirSync(root, { recursive: true });
    fs.writeFileSync(path.join(root, 'package.json'), JSON.stringify({ name, version, main: 'index.js' }));
    fs.writeFileSync(path.join(root, 'index.js'), 'module.exports = {};\n');
  }
  if (marker) fs.writeFileSync(path.join(dir, '.ui-verify-ready'), JSON.stringify({ hash: path.basename(dir), installedAt: new Date().toISOString() }));
  return dir;
}

// --- naglowek i import ---

test('naglowek install.mjs przechodzi parseHeader().ok i wymienia npm oraz Node >= 20.11', async () => {
  assert.ok(fs.existsSync(SCRIPT), 'brak scripts/install.mjs');
  const { parseHeader } = await import(HEADER_URL);
  const text = fs.readFileSync(SCRIPT, 'utf8');
  const h = parseHeader(text);
  assert.equal(h.ok, true, `brakuje: ${h.missing}, kolejnosc: ${h.order}`);
  const block = /\/\*[\s\S]*?\*\//.exec(text)[0];
  assert.match(block, /Dependencies:[^\n]*npm/i);
  assert.match(block, /20\.11/);
});

test('import install.mjs nie uruchamia main(), nic nie wypisuje i nie dotyka cache', () => {
  const cache = newDir('imp-cache');
  const r = spawnSync(process.execPath, ['--input-type=module', '-e', `await import(${JSON.stringify(pathToFileURL(SCRIPT).href)}); process.stdout.write('imported');`], {
    cwd: newDir('imp'), encoding: 'utf8', timeout: 60000, env: { ...process.env, UI_VERIFY_CACHE_DIR: cache },
  });
  assert.equal(r.status, 0, r.stderr);
  assert.equal(r.stdout, 'imported');
  assert.deepEqual(fs.readdirSync(cache), []);
});

// --- --selftest ---

test('--selftest => kod 0 bez npm (pusty PATH, bez npm_execpath) i bez zapisu do cache', () => {
  const cache = newDir('st-cache');
  const r = run(['--selftest'], { cacheRoot: cache, clean: true, env: { PATH: '' } });
  assert.equal(r.code, 0, r.stdout + r.stderr);
  assert.doesNotMatch(r.stdout, /^FAIL /m);
  const m = /selftest [^\n]*?(\d+)\/(\d+) passed/.exec(r.stdout);
  assert.ok(m, `brak podsumowania selftestu: ${r.stdout}`);
  assert.equal(m[1], m[2]);
  assert.ok(Number(m[2]) >= 10, `za malo przypadkow selftestu: ${m[2]}`);
  assert.deepEqual(fs.readdirSync(cache), []);
});

// --- kody 2 (uzycie, cache niebezpieczny) ---

test('nieznana flaga => kod 2 i komunikat na stderr', () => {
  const r = run(['--bogus']);
  assert.equal(r.code, 2, r.stdout + r.stderr);
  assert.notEqual(r.stderr.trim(), '');
});

test('--lock-timeout bez wartosci albo z nieliczba => kod 2', () => {
  assert.equal(run(['--lock-timeout']).code, 2);
  assert.equal(run(['--lock-timeout', 'abc']).code, 2);
});

test('UI_VERIFY_CACHE_DIR wewnatrz katalogu skilla => kod 2, katalog nie powstaje', () => {
  const inside = path.join(SKILL, `.uv-test-cache-${process.pid}`);
  strays.push(inside);
  const r = run([], { cacheRoot: inside });
  assert.equal(r.code, 2, r.stdout + r.stderr);
  assert.equal(fs.existsSync(inside), false);
  const c = run(['--check'], { cacheRoot: inside });
  assert.equal(c.code, 2, c.stdout + c.stderr);
});

test('cache z bitem zapisu dla innych => kod 2 (kod z cache bylby wykonywany z prawami uzytkownika)', { skip: WIN ? 'needs-posix: bity mode nie dotycza Windows' : false }, () => {
  const cache = newDir('open-cache');
  fs.chmodSync(cache, 0o777);
  const r = run([], { cacheRoot: cache });
  assert.equal(r.code, 2, r.stdout + r.stderr);
  assert.deepEqual(fs.readdirSync(cache), []);
});

// --- --check ---

test('--check bez instalacji (pusty cache) => kod 3 i nic nie powstaje w cache', () => {
  const cache = newDir('check-empty');
  const r = run(['--check'], { cacheRoot: cache, clean: true, env: { PATH: '' } });
  assert.equal(r.code, 3, r.stdout + r.stderr);
  assert.notEqual((r.stdout + r.stderr).trim(), '');
  assert.deepEqual(fs.readdirSync(cache), []);
});

test('--check na gotowej instalacji => kod 0; --json daje poprawny JSON', async () => {
  const cache = newDir('check-ok');
  await fakeInstalled(cache);
  const r = run(['--check'], { cacheRoot: cache });
  assert.equal(r.code, 0, r.stdout + r.stderr);
  const j = run(['--check', '--json'], { cacheRoot: cache });
  assert.equal(j.code, 0, j.stdout + j.stderr);
  assert.doesNotThrow(() => JSON.parse(j.stdout), j.stdout);
});

test('--check: katalog bez znacznika gotowosci => kod 3 i katalog nietkniety', async () => {
  const cache = newDir('check-nomarker');
  const dir = await fakeInstalled(cache, { marker: false });
  const r = run(['--check'], { cacheRoot: cache });
  assert.equal(r.code, 3, r.stdout + r.stderr);
  assert.ok(fs.existsSync(path.join(dir, 'node_modules', 'pngjs', 'package.json')), '--check nie wolno usuwac');
});

test('--check: wersja niezgodna z lockfile => kod 3', async () => {
  const cache = newDir('check-badver');
  const dir = await fakeInstalled(cache);
  fs.writeFileSync(path.join(dir, 'node_modules', 'pixelmatch', 'package.json'), JSON.stringify({ name: 'pixelmatch', version: '0.0.1', main: 'index.js' }));
  assert.equal(run(['--check'], { cacheRoot: cache }).code, 3);
});

// --- idempotencja bez npm ---

test('instalacja na gotowej instalacji => kod 0 "already installed" bez npm i bez sieci', async () => {
  const cache = newDir('idem');
  const dir = await fakeInstalled(cache);
  const before = fs.readFileSync(path.join(dir, '.ui-verify-ready'), 'utf8');
  const r = run([], { cacheRoot: cache, clean: true, env: { PATH: '' } });
  assert.equal(r.code, 0, r.stdout + r.stderr);
  assert.match(r.stdout + r.stderr, /already installed/i);
  assert.equal(fs.readFileSync(path.join(dir, '.ui-verify-ready'), 'utf8'), before);
  assert.deepEqual(fs.readdirSync(cache).filter((n) => n.includes('.lock') || n.includes('.tmp-')), []);
});

// --- integracja z prawdziwym npm i siecia ---

const NET_SKIP = process.env.UI_VERIFY_TEST_NETWORK === '1' ? false : 'needs-network: ustaw UI_VERIFY_TEST_NETWORK=1 (prawdziwy npm i siec)';

test('integracja: prawdziwy npm instaluje do katalogu tymczasowego; --check => 0; pixel-diff --selftest => 0', { skip: NET_SKIP, timeout: 600000 }, () => {
  const cache = newDir('net-cache');
  const inst = spawnSync(process.execPath, [SCRIPT], {
    cwd: newDir('net-cwd'), encoding: 'utf8', timeout: 540000,
    env: { ...process.env, UI_VERIFY_CACHE_DIR: cache },
  });
  assert.equal(inst.status, 0, inst.stdout + inst.stderr);
  assert.equal(run(['--check'], { cacheRoot: cache }).code, 0);
  const px = spawnSync(process.execPath, [PIXEL_DIFF, '--selftest'], {
    cwd: newDir('net-px'), encoding: 'utf8', timeout: 120000,
    env: { ...process.env, UI_VERIFY_CACHE_DIR: cache },
  });
  assert.equal(px.status, 0, px.stdout + px.stderr);
});
