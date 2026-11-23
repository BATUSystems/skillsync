// Proces potomny `node scripts/diff.mjs` (S5): naglowek, --selftest, kody 2/3 bez przegladarki, import bez efektow, wyciek sekretu.
// Porownanie prawdziwych PNG (bez Chrome): pngjs z UI_VERIFY_TEST_CACHE, pomijane z powodem needs-ui-verify-deps.
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import zlib from 'node:zlib';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const SKILL = path.join(ROOT, 'library', 'skills', 'ui-verify');
const SCRIPT = path.join(SKILL, 'scripts', 'diff.mjs');
const HEADER_URL = pathToFileURL(path.join(SKILL, 'scripts', 'lib', 'header.mjs')).href;
const DEPS_URL = pathToFileURL(path.join(SKILL, 'scripts', 'lib', 'deps.mjs')).href;

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

// --- A7: SAME / DIFFERENT / UNVERIFIED na prawdziwych PNG (pngjs z cache testowego, bez Chrome) ---

function findCache() {
  const root = process.env.UI_VERIFY_TEST_CACHE;
  if (!root) return { reason: 'needs-ui-verify-deps: UI_VERIFY_TEST_CACHE nie ustawione' };
  try {
    const sub = fs.readdirSync(root).find((d) => ['pngjs', 'pixelmatch'].every((n) => fs.existsSync(path.join(root, d, 'node_modules', n, 'package.json'))));
    if (!sub) return { reason: 'needs-ui-verify-deps: brak pngjs/pixelmatch w UI_VERIFY_TEST_CACHE' };
    return { root, cacheDir: path.join(root, sub) };
  } catch (e) {
    return { reason: `needs-ui-verify-deps: ${e.code || e.message}` };
  }
}
const realCache = findCache();
let PNG = null;
if (realCache.cacheDir) {
  try {
    const { createLoader } = await import(DEPS_URL);
    ({ PNG } = await createLoader({ cacheDir: realCache.cacheDir }).load('pngjs'));
  } catch (e) {
    realCache.reason = `needs-ui-verify-deps: ${e.code || e.message}`;
  }
}
const PNG_SKIP = PNG ? false : realCache.reason;

const WHITE = [255, 255, 255, 255];
const BLACK = [0, 0, 0, 255];
function makePng(w, h, fn) {
  const p = new PNG({ width: w, height: h });
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) p.data.set(fn(x, y), (y * w + x) * 4);
  return PNG.sync.write(p);
}
const block = (x0, y0 = 20) => (x, y) => (x >= x0 && x < x0 + 30 && y >= y0 && y < y0 + 30 ? BLACK : WHITE);

// Dwa katalogi zrzutow z manifestem i prawdziwymi PNG pod jednym cwd.
function pngSets({ before, after, assertionBefore = 'PASS', assertionAfter = 'PASS' }) {
  const cwd = newDir('pngs');
  const mk = (label, png, assertion) => {
    const d = path.join(cwd, label);
    fs.mkdirSync(d, { recursive: true });
    fs.writeFileSync(path.join(d, 'home__375-light.png'), png);
    fs.writeFileSync(path.join(d, 'manifest.json'), JSON.stringify({ version: 1, shots: [{ name: 'home__375-light.png', pageId: 'home', width: 375, mode: 'light', assertion }] }));
  };
  mk('before', before, assertionBefore);
  mk('after', after, assertionAfter);
  return cwd;
}
const runReal = (args, cwd) => run(args, { cwd, cacheRoot: realCache.root });


// PNG z poprawna sygnatura, IHDR o zadanych wymiarach i malym, poprawnym IDAT: bomba dekompresyjna w naglowku (dane sa male, deklarowane wymiary ogromne).
function bombPng(w, h) {
  const crcTable = Array.from({ length: 256 }, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
  const crc = (buf) => { let c = 0xffffffff; for (const b of buf) c = crcTable[(c ^ b) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
  const chunk = (type, data) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
    const td = Buffer.concat([Buffer.from(type), data]);
    const c = Buffer.alloc(4); c.writeUInt32BE(crc(td));
    return Buffer.concat([len, td, c]);
  };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 6;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(Buffer.alloc(1))), chunk('IEND', Buffer.alloc(0))]);
}

test('A7: te same prawdziwe PNG z asercja PASS po obu stronach => SAME, kod 0', { skip: PNG_SKIP }, () => {
  const png = makePng(200, 100, block(50));
  const cwd = pngSets({ before: png, after: Buffer.from(png) });
  const r = runReal(['before', 'after'], cwd);
  assert.equal(r.code, 0, r.stdout + r.stderr);
  assert.match(r.stdout, /^SAME home__375-light\.png/m);
  assert.doesNotMatch(r.stdout, /DIFFERENT|UNVERIFIED/);
});

test('A7: zmieniona zawartosc PNG (blok przesuniety o 20 px) => DIFFERENT, kod 1', { skip: PNG_SKIP }, () => {
  const cwd = pngSets({ before: makePng(200, 100, block(50)), after: makePng(200, 100, block(70)) });
  const r = runReal(['before', 'after'], cwd);
  assert.equal(r.code, 1, r.stdout + r.stderr);
  assert.match(r.stdout, /^DIFFERENT home__375-light\.png/m);
  assert.doesNotMatch(r.stdout, /\bSAME\b/);
});

test('A7: rozne wymiary PNG => DIFFERENT size-mismatch, kod 1', { skip: PNG_SKIP }, () => {
  const cwd = pngSets({ before: makePng(200, 100, block(50)), after: makePng(200, 120, block(50)) });
  const r = runReal(['before', 'after'], cwd);
  assert.equal(r.code, 1, r.stdout + r.stderr);
  assert.match(r.stdout, /DIFFERENT home__375-light\.png size-mismatch/);
});

test('A7: identyczne PNG, ale jedna strona bez asercji pozytywnej => UNVERIFIED, kod 3, nigdy SAME', { skip: PNG_SKIP }, () => {
  const png = makePng(200, 100, block(50));
  for (const over of [{ assertionBefore: 'NONE' }, { assertionAfter: 'FAIL' }]) {
    const cwd = pngSets({ before: png, after: Buffer.from(png), ...over });
    const r = runReal(['before', 'after'], cwd);
    assert.equal(r.code, 3, `${JSON.stringify(over)}: ${r.stdout}${r.stderr}`);
    assert.doesNotMatch(r.stdout, /\bSAME\b/);
    assert.match(r.stdout, /UNVERIFIED/);
  }
});

test('A7: pojedynczy piksel roznicy w 200x100 => domyslnie (max-pct 0) DIFFERENT; --max-pct 0.5 => SAME', { skip: PNG_SKIP }, () => {
  const a = makePng(200, 100, () => WHITE);
  const b = makePng(200, 100, (x, y) => (x === 100 && y === 50 ? BLACK : WHITE));
  const cwd = pngSets({ before: a, after: b });
  const strict = runReal(['before', 'after'], cwd);
  assert.equal(strict.code, 1, strict.stdout + strict.stderr);
  assert.match(strict.stdout, /DIFFERENT/);
  const lax = runReal(['--max-pct', '0.5', 'before', 'after'], cwd);
  assert.equal(lax.code, 0, lax.stdout + lax.stderr);
  assert.match(lax.stdout, /SAME/);
});

// --- limit rozmiaru dekodera PNG (CORE-1142-UIV-SEC): odmowa z kodem 2 (nie UNVERIFIED i nie werdykt) przed dekodowaniem ---

test('bomba w naglowku PNG po jednej ze stron => kod 2 z limitem w komunikacie, bez werdyktu i bez dekodowania', { skip: PNG_SKIP }, () => {
  for (const side of ['before', 'after']) {
    const good = makePng(200, 100, block(50));
    const cwd = pngSets({ before: side === 'before' ? bombPng(20000, 20000) : good, after: side === 'after' ? bombPng(20000, 20000) : Buffer.from(good) });
    const started = Date.now();
    const r = runReal(['before', 'after'], cwd);
    assert.equal(r.code, 2, `${side}: ${r.stdout}${r.stderr}`);
    assert.match(r.stderr, /^diff: cannot read screenshot home__375-light\.png in (before|after): PNG header declares 20000x20000 pixels/m);
    assert.match(r.stderr, /over the limit/);
    assert.doesNotMatch(r.stdout, /SAME|DIFFERENT|UNVERIFIED/);
    assert.ok(Date.now() - started < 20000);
  }
});

// PNG ze zlozonych chunkow (CRC zero: odmowa pada przed dekodowaniem, wiec pngjs go nie sprawdza).
function craftPng(...chunks) {
  const mk = (type, data) => { const b = Buffer.alloc(12 + data.length); b.writeUInt32BE(data.length, 0); b.write(type, 4, 'latin1'); data.copy(b, 8); return b; };
  const ihdr = (w, h, { interlace = 0, extra = 0 } = {}) => { const d = Buffer.alloc(13 + extra); d.writeUInt32BE(w, 0); d.writeUInt32BE(h, 4); d[8] = 8; d[9] = 6; d[12] = interlace; return d; };
  const idat = zlib.deflateSync(Buffer.alloc(1));
  const parts = chunks.map(([t, ...a]) => (t === 'IHDR' ? mk('IHDR', ihdr(...a)) : t === 'IDAT' ? mk('IDAT', idat) : mk('IEND', Buffer.alloc(0))));
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), ...parts]);
}

test('diff: IHDR dlugosci 14 / drugi IHDR / przeplot po jednej ze stron => kod 2 z komunikatem, bez werdyktu', { skip: PNG_SKIP }, () => {
  const bads = [craftPng(['IHDR', 100000, 100000, { extra: 1 }], ['IDAT'], ['IEND']), craftPng(['IHDR', 1, 1], ['IHDR', 100000, 100000], ['IDAT'], ['IEND']), craftPng(['IHDR', 1, 1, { interlace: 1 }], ['IDAT'], ['IEND'])];
  for (const bad of bads) {
    const good = makePng(200, 100, block(50));
    const cwd = pngSets({ before: bad, after: good });
    const r = runReal(['before', 'after'], cwd);
    assert.equal(r.code, 2, r.stdout + r.stderr);
    assert.match(r.stderr, /^diff: cannot read screenshot home__375-light\.png in before: PNG (does not start with a standard IHDR|has more than one IHDR|is interlaced)/m);
    assert.doesNotMatch(r.stdout, /SAME|DIFFERENT|UNVERIFIED/);
  }
});
