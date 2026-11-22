// Proces potomny `node scripts/diff.mjs` (S5): naglowek, --selftest, kody 2/3 bez przegladarki, import bez efektow, wyciek sekretu.
// Porownanie prawdziwych PNG jest w capture-e2e.test.mjs.
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
const SCRIPT = path.join(SKILL, 'scripts', 'diff.mjs');
const HEADER_URL = pathToFileURL(path.join(SKILL, 'scripts', 'lib', 'header.mjs')).href;

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-diff-'));
after(() => fs.rmSync(tmpRoot, { recursive: true, force: true }));
let counter = 0;
const newDir = (label = 'd') => {
  const d = path.join(tmpRoot, `${label}-${counter++}`);
  fs.mkdirSync(d, { recursive: true });
  return d;
};
const emptyCache = () => newDir('emptycache');

function run(args, { cacheRoot, cwd, env = {} } = {}) {
  const base = { ...process.env };
  for (const k of Object.keys(base)) if (k.startsWith('UI_VERIFY_') || k === 'PLAYWRIGHT_MODULE_PATH') delete base[k];
  const r = spawnSync(process.execPath, [SCRIPT, ...args], {
    cwd: cwd ?? newDir('cwd'),
    env: { ...base, UI_VERIFY_CACHE_DIR: cacheRoot ?? emptyCache(), ...env },
    encoding: 'utf8',
    timeout: 120000,
  });
  return { code: r.status, stdout: r.stdout ?? '', stderr: r.stderr ?? '', error: r.error };
}

const importProbe = (env) => spawnSync(process.execPath, ['--input-type=module', '-e', `await import(${JSON.stringify(pathToFileURL(SCRIPT).href)}); process.stdout.write('imported');`], {
  cwd: newDir('imp'), encoding: 'utf8', timeout: 60000, env: env ?? process.env,
});
const secretValue = () => ['fake', 'secret', String(process.pid), 'abcd'].join('-');

// Katalog zrzutow z manifestem (bez prawdziwych PNG: wystarcza do sciezek bledow i braku zaleznosci).
function shotsDir(parent, label, assertion = 'PASS') {
  const d = path.join(parent, label);
  fs.mkdirSync(d, { recursive: true });
  fs.writeFileSync(path.join(d, 'home__375-light.png'), Buffer.from([137, 80, 78, 71]));
  fs.writeFileSync(path.join(d, 'manifest.json'), JSON.stringify({ version: 1, shots: [{ name: 'home__375-light.png', pageId: 'home', width: 375, mode: 'light', assertion }] }));
  return d;
}

test('naglowek diff.mjs przechodzi parseHeader().ok i zawiera przypiete zaleznosci', async () => {
  assert.ok(fs.existsSync(SCRIPT), 'brak scripts/diff.mjs');
  const { parseHeader } = await import(HEADER_URL);
  const text = fs.readFileSync(SCRIPT, 'utf8');
  const h = parseHeader(text);
  assert.equal(h.ok, true, `brakuje: ${h.missing}, kolejnosc: ${h.order}`);
  const block = /\/\*[\s\S]*?\*\//.exec(text)[0];
  assert.match(block, /pngjs 7\.0\.0/);
  assert.match(block, /pixelmatch 8\.0\.0/);
});

test('import diff.mjs nie uruchamia main() i nic nie wypisuje; nie laduje zaleznosci', () => {
  for (const env of [undefined, { ...process.env, UI_VERIFY_CACHE_DIR: emptyCache() }]) {
    const r = importProbe(env);
    assert.equal(r.status, 0, r.stderr);
    assert.equal(r.stdout, 'imported');
  }
});

test('--selftest bez cache zaleznosci => kod 0, wszystkie przypadki przechodza', () => {
  const r = run(['--selftest']);
  assert.equal(r.code, 0, r.stdout + r.stderr);
  assert.doesNotMatch(r.stdout, /^FAIL /m);
  const m = /selftest [^\n]*?(\d+)\/(\d+) passed/.exec(r.stdout);
  assert.ok(m, `brak podsumowania selftestu: ${r.stdout}`);
  assert.equal(m[1], m[2]);
  assert.ok(Number(m[2]) >= 6, `za malo przypadkow selftestu: ${m[2]}`);
});

test('brak argumentow, jeden katalog, trzy katalogi, nieznana flaga albo zle --max-pct => kod 2', () => {
  const cwd = newDir('cwd');
  const a = shotsDir(cwd, 'a');
  const r0 = run([], { cwd });
  assert.equal(r0.code, 2, r0.stdout + r0.stderr);
  assert.notEqual(r0.stderr.trim(), '');
  assert.equal(run(['a'], { cwd }).code, 2);
  assert.equal(run(['a', 'a', 'a'], { cwd }).code, 2);
  assert.equal(run(['--bogus', 'a', 'a'], { cwd }).code, 2);
  assert.equal(run(['--max-pct', 'abc', 'a', 'a'], { cwd }).code, 2);
  assert.equal(run(['--max-pct', '-1', 'a', 'a'], { cwd }).code, 2);
  assert.ok(fs.existsSync(a));
});

test('katalog poza cwd (../ albo sciezka bezwzgledna) => kod 2', () => {
  const parent = newDir('par');
  const cwd = path.join(parent, 'work');
  fs.mkdirSync(cwd);
  shotsDir(cwd, 'in');
  const outside = shotsDir(parent, 'outside');
  assert.equal(run(['in', path.join('..', 'outside')], { cwd }).code, 2);
  assert.equal(run(['in', outside], { cwd }).code, 2);
  assert.equal(run([outside, 'in'], { cwd }).code, 2);
});

test('brak zaleznosci (pusty UI_VERIFY_CACHE_DIR) przy poprawnych katalogach => kod 3, nigdy 0/1/2', () => {
  const cwd = newDir('cwd');
  shotsDir(cwd, 'a');
  shotsDir(cwd, 'b');
  const r = run(['a', 'b'], { cwd });
  assert.equal(r.code, 3, r.stdout + r.stderr);
  assert.doesNotMatch(r.stdout, /^SAME\b/m);
});

test('sekret z UI_VERIFY_SECRET_ENV w sciezce katalogu poza cwd nie trafia do stdout/stderr (kod 2); kontrola bez maskowania', () => {
  const secret = secretValue();
  const cwd = newDir('cwd');
  shotsDir(cwd, 'a');
  const bad = path.join(path.dirname(cwd), `${secret}-dir`);
  const r = run(['a', bad], { cwd, env: { UV_FAKE_SECRET: secret, UI_VERIFY_SECRET_ENV: 'UV_FAKE_SECRET' } });
  assert.equal(r.code, 2, r.stdout + r.stderr);
  assert.ok(!r.stdout.includes(secret) && !r.stderr.includes(secret), 'sekret w wyjsciu');
  const control = run(['a', bad], { cwd, env: { UV_FAKE_SECRET: secret } });
  assert.ok((control.stdout + control.stderr).includes(secret), 'kontrola: bez maskowania sciezka jest w bledzie');
});
