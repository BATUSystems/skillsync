// Proces potomny `node scripts/capture.mjs` (S5): naglowek, --selftest, kody 2/3 bez przegladarki, import bez efektow, wyciek sekretu.
// Adapter Playwright jest pokryty w capture-e2e.test.mjs (z prawdziwym Chrome).
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
const SCRIPT = path.join(SKILL, 'scripts', 'capture.mjs');
const HEADER_URL = pathToFileURL(path.join(SKILL, 'scripts', 'lib', 'header.mjs')).href;

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-capture-'));
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

function project(over = {}) {
  const dir = newDir('proj');
  fs.writeFileSync(path.join(dir, 'page.html'), '<!doctype html><html><body><h1>Hello</h1></body></html>');
  const cfg = { version: 1, pages: [{ id: 'home', url: 'page.html', assert: { selector: 'h1' } }], ...over };
  const file = path.join(dir, 'config.json');
  fs.writeFileSync(file, JSON.stringify(cfg));
  return { dir, file };
}

test('naglowek capture.mjs przechodzi parseHeader().ok i zawiera przypiete zaleznosci', async () => {
  assert.ok(fs.existsSync(SCRIPT), 'brak scripts/capture.mjs');
  const { parseHeader } = await import(HEADER_URL);
  const text = fs.readFileSync(SCRIPT, 'utf8');
  const h = parseHeader(text);
  assert.equal(h.ok, true, `brakuje: ${h.missing}, kolejnosc: ${h.order}`);
  const block = /\/\*[\s\S]*?\*\//.exec(text)[0];
  assert.match(block, /playwright-core 1\.65\.0/);
  assert.match(block, /pngjs 7\.0\.0/);
  assert.match(block, /Chrome/);
  assert.match(block, /--allow-trace/);
});

test('import capture.mjs nie uruchamia main() i nic nie wypisuje; nie laduje zaleznosci', () => {
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

test('--selftest nie wymaga --config ani outDir; --allow-trace jest akceptowane', () => {
  assert.equal(run(['--selftest', '--allow-trace']).code, 0);
});

test('brak argumentow, brak --config, brak outDir albo nieznana flaga => kod 2 i komunikat na stderr', () => {
  const { dir, file } = project();
  const none = run([]);
  assert.equal(none.code, 2, none.stdout + none.stderr);
  assert.notEqual(none.stderr.trim(), '');
  assert.equal(run([newDir('o')]).code, 2, 'brak --config');
  assert.equal(run(['--config', file], { cwd: dir }).code, 2, 'brak outDir');
  assert.equal(run(['--bogus', '--config', file, 'out'], { cwd: dir }).code, 2);
});

test('zla konfiguracja => kod 2 (nie 1 ani 3), brak wynikow', () => {
  const cases = {
    'nieznany klucz': { widthz: [300] },
    'zly tryb': { modes: ['sepia'] },
    'szerokosc poza zakresem': { widths: [100] },
    'literalna wartosc fill': { login: { url: 'page.html', steps: [{ fill: { selector: '#u', value: 'tajne' } }] } },
    'fill bez valueEnv': { login: { url: 'page.html', steps: [{ fill: { selector: '#u' } }] } },
    'brak zmiennej srodowiska valueEnv': { login: { url: 'page.html', steps: [{ fill: { selector: '#u', valueEnv: 'UV_NIE_USTAWIONA' } }] } },
  };
  for (const [label, over] of Object.entries(cases)) {
    const { dir, file } = project(over);
    const r = run(['--config', file, 'out'], { cwd: dir });
    assert.equal(r.code, 2, `${label}: ${r.stdout}${r.stderr}`);
    assert.equal(fs.existsSync(path.join(dir, 'out', 'manifest.json')), false, label);
    assert.ok(!(r.stdout + r.stderr).includes('tajne'), `${label}: literalna wartosc w wyjsciu`);
  }
});

test('plik konfiguracji nie istnieje albo nie jest JSON => kod 2', () => {
  const dir = newDir('badjson');
  assert.equal(run(['--config', path.join(dir, 'brak.json'), 'out'], { cwd: dir }).code, 2);
  const junk = path.join(dir, 'junk.json');
  fs.writeFileSync(junk, '{ to nie jest json');
  assert.equal(run(['--config', junk, 'out'], { cwd: dir }).code, 2);
});

test('niedozwolony origin => kod 2; --allow-origin go dopuszcza (wtedy brak zaleznosci => 3)', () => {
  const { dir, file } = project({ pages: [{ id: 'home', url: 'https://app.example.test/', assert: { selector: 'h1' } }] });
  assert.equal(run(['--config', file, 'out'], { cwd: dir }).code, 2);
  const allowed = run(['--allow-origin', 'app.example.test', '--config', file, 'out'], { cwd: dir });
  assert.equal(allowed.code, 3, allowed.stdout + allowed.stderr);
});

test('outDir poza katalogiem roboczym (../ albo sciezka bezwzgledna) => kod 2 i brak zapisu', () => {
  const { dir, file } = project();
  const outside = path.join(path.dirname(dir), `outside-${counter++}`);
  const r1 = run(['--config', file, path.join('..', 'escaped')], { cwd: dir });
  assert.equal(r1.code, 2, r1.stdout + r1.stderr);
  assert.equal(fs.existsSync(path.join(path.dirname(dir), 'escaped')), false);
  const r2 = run(['--config', file, outside], { cwd: dir });
  assert.equal(r2.code, 2, r2.stdout + r2.stderr);
  assert.equal(fs.existsSync(outside), false);
});

test('brak zaleznosci (pusty UI_VERIFY_CACHE_DIR) przy poprawnej konfiguracji => kod 3, nigdy 0/1/2; bez manifestu', () => {
  const { dir, file } = project();
  const r = run(['--config', file, 'out'], { cwd: dir });
  assert.equal(r.code, 3, r.stdout + r.stderr);
  assert.doesNotMatch(r.stdout, /^(PASS|FAIL) /m);
  assert.equal(fs.existsSync(path.join(dir, 'out', 'manifest.json')), false);
});

test('sekret z UI_VERIFY_SECRET_ENV w sciezce konfiguracji nie trafia do stdout/stderr (kod 2); kontrola bez maskowania', () => {
  const secret = secretValue();
  const dir = newDir('leak');
  const cfg = path.join(dir, `${secret}.json`);
  const r = run(['--config', cfg, 'out'], { cwd: dir, env: { UV_FAKE_SECRET: secret, UI_VERIFY_SECRET_ENV: 'UV_FAKE_SECRET' } });
  assert.equal(r.code, 2, r.stdout + r.stderr);
  assert.ok(!r.stdout.includes(secret) && !r.stderr.includes(secret), 'sekret w wyjsciu');
  const control = run(['--config', cfg, 'out'], { cwd: dir, env: { UV_FAKE_SECRET: secret } });
  assert.ok((control.stdout + control.stderr).includes(secret), 'kontrola: bez maskowania sciezka jest w bledzie');
});

test('sekret z valueEnv (login) w komunikacie bledu konfiguracji jest maskowany bez UI_VERIFY_SECRET_ENV; kontrola bez login', () => {
  const secret = secretValue();
  const dir = newDir('leak2');
  fs.writeFileSync(path.join(dir, 'page.html'), '<h1>x</h1>');
  const page = { id: 'home', url: 'page.html', [`k-${secret}`]: 1 };
  const withLogin = path.join(dir, 'with.json');
  fs.writeFileSync(withLogin, JSON.stringify({ version: 1, login: { url: 'page.html', steps: [{ fill: { selector: '#u', valueEnv: 'UV_FAKE_SECRET' } }] }, pages: [page] }));
  const r = run(['--config', withLogin, 'out'], { cwd: dir, env: { UV_FAKE_SECRET: secret } });
  assert.equal(r.code, 2, r.stdout + r.stderr);
  assert.ok(!(r.stdout + r.stderr).includes(secret), 'sekret valueEnv w wyjsciu bledu konfiguracji');
  const without = path.join(dir, 'without.json');
  fs.writeFileSync(without, JSON.stringify({ version: 1, pages: [page] }));
  const control = run(['--config', without, 'out'], { cwd: dir, env: { UV_FAKE_SECRET: secret } });
  assert.equal(control.code, 2);
  assert.ok((control.stdout + control.stderr).includes(secret), 'kontrola: bez deklaracji sekretu klucz jest w bledzie');
});
