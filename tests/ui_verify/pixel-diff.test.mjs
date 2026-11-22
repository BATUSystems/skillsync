// Proces potomny `node scripts/pixel-diff.mjs`: kody wyjscia, wyjscie, maski, --crop, odpornosc 1 px, fixtury, wyciek sekretu.
// PNG syntetyczne powstaja przez pngjs z UI_VERIFY_TEST_CACHE; bez niego testy z PNG sa pomijane.
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
const SCRIPT = path.join(SKILL, 'scripts', 'pixel-diff.mjs');
const FIXTURES = path.join(SKILL, 'fixtures');
const DEPS_URL = pathToFileURL(path.join(SKILL, 'scripts', 'lib', 'deps.mjs')).href;
const HEADER_URL = pathToFileURL(path.join(SKILL, 'scripts', 'lib', 'header.mjs')).href;

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-pixel-diff-'));
after(() => fs.rmSync(tmpRoot, { recursive: true, force: true }));
let counter = 0;
const newDir = (label = 'd') => {
  const d = path.join(tmpRoot, `${label}-${counter++}`);
  fs.mkdirSync(d, { recursive: true });
  return d;
};

// Korzen cache z podkatalogiem hash (ten, w ktorym lezy node_modules/pngjs).
function findCacheRoot() {
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
const cache = findCacheRoot();
let PNG = null;
if (cache.cacheDir) {
  try {
    const { createLoader } = await import(DEPS_URL);
    ({ PNG } = await createLoader({ cacheDir: cache.cacheDir }).load('pngjs'));
  } catch (e) {
    cache.reason = `needs-ui-verify-deps: ${e.code || e.message}`;
  }
}
const SKIP = PNG ? false : cache.reason;

const emptyCache = () => newDir('emptycache');

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

function makePng(w, h, fn) {
  const p = new PNG({ width: w, height: h });
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) p.data.set(fn(x, y), (y * w + x) * 4);
  return PNG.sync.write(p);
}
const WHITE = [255, 255, 255, 255];
const BLACK = [0, 0, 0, 255];
const bar = (x0) => (x, y) => (x >= x0 && x < x0 + 3 ? BLACK : WHITE);
function save(dir, name, buf) {
  const f = path.join(dir, name);
  fs.writeFileSync(f, buf);
  return f;
}
function pair(w, h, fa, fb, wb = w, hb = h) {
  const d = newDir('png');
  return { dir: d, a: save(d, 'a.png', makePng(w, h, fa)), b: save(d, 'b.png', makePng(wb, hb, fb)) };
}

// Minimalny PNG 2x2 bez zaleznosci (do przypadku bez cache).
function tinyPng() {
  const crcTable = Array.from({ length: 256 }, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
  const crc = (buf) => { let c = 0xffffffff; for (const b of buf) c = crcTable[(c ^ b) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
  const chunk = (type, data) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
    const td = Buffer.concat([Buffer.from(type), data]);
    const c = Buffer.alloc(4); c.writeUInt32BE(crc(td));
    return Buffer.concat([len, td, c]);
  };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(2, 0); ihdr.writeUInt32BE(2, 4); ihdr[8] = 8; ihdr[9] = 6;
  const raw = Buffer.alloc(2 * (1 + 8), 255);
  raw[0] = 0; raw[9] = 0;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}

// --- naglowek i import bez efektow ---

test('naglowek pixel-diff.mjs przechodzi parseHeader().ok i zawiera przypiete zaleznosci', async () => {
  const { parseHeader } = await import(HEADER_URL);
  const text = fs.readFileSync(SCRIPT, 'utf8');
  const h = parseHeader(text);
  assert.equal(h.ok, true, `brakuje: ${h.missing}, kolejnosc: ${h.order}`);
  const block = /\/\*[\s\S]*?\*\//.exec(text)[0];
  assert.match(block, /pngjs 7\.0\.0/);
  assert.match(block, /pixelmatch 8\.0\.0/);
  assert.match(block, /Node >= 20\.11/);
});

test('import pixel-diff.mjs nie uruchamia main() i nic nie wypisuje', () => {
  const r = spawnSync(process.execPath, ['--input-type=module', '-e', `await import(${JSON.stringify(pathToFileURL(SCRIPT).href)}); process.stdout.write('imported');`], { cwd: newDir('imp'), encoding: 'utf8', timeout: 60000 });
  assert.equal(r.status, 0, r.stderr);
  assert.equal(r.stdout, 'imported');
});

// --- kody 2 i 3 bez PNG (nie sa pomijane) ---

test('brak argumentow => kod 2 i komunikat uzycia na stderr', () => {
  const r = run([], { cacheRoot: emptyCache() });
  assert.equal(r.code, 2, r.stderr);
  assert.notEqual(r.stderr.trim(), '');
});

test('jeden argument pozycyjny zamiast dwoch => kod 2', () => {
  const d = newDir('one');
  const f = save(d, 'a.png', tinyPng());
  assert.equal(run([f], { cacheRoot: emptyCache(), cwd: d }).code, 2);
});

test('nieznana flaga => kod 2', () => {
  const d = newDir('flag');
  const f = save(d, 'a.png', tinyPng());
  assert.equal(run(['--bogus', f, f], { cacheRoot: emptyCache(), cwd: d }).code, 2);
});

test('brak zaleznosci (UI_VERIFY_CACHE_DIR = pusty katalog) => kod 3, nigdy 0/1/2', () => {
  const d = newDir('nodeps');
  const a = save(d, 'a.png', tinyPng());
  const b = save(d, 'b.png', tinyPng());
  const r = run([a, b], { cacheRoot: emptyCache(), cwd: d });
  assert.equal(r.code, 3, r.stdout + r.stderr);
  assert.doesNotMatch(r.stdout, /^(PASS|FAIL) pixel-diff/m);
});

test('--selftest bez zaleznosci => kod 3', () => {
  const r = run(['--selftest'], { cacheRoot: emptyCache() });
  assert.equal(r.code, 3, r.stdout + r.stderr);
});

test('--selftest z cache => kod 0', { skip: SKIP }, () => {
  const r = run(['--selftest']);
  assert.equal(r.code, 0, r.stdout + r.stderr);
  assert.doesNotMatch(r.stdout, /^FAIL /m);
});

// --- kody 0/1 i wyjscie (potrzebuja pngjs/pixelmatch) ---

test('identyczne obrazy => kod 0, linia PASS pixel-diff 0% (0/total)', { skip: SKIP }, () => {
  const { a, b, dir } = pair(40, 40, bar(10), bar(10));
  const r = run([a, b], { cwd: dir });
  assert.equal(r.code, 0, r.stdout + r.stderr);
  assert.match(r.stdout, /^PASS pixel-diff 0(\.0+)?% \(0\/1600\)\s*$/m);
});

test('przesuniecie o 1 px => PASS dzieki 3x3; z --no-tolerate-1px => FAIL (kod 1)', { skip: SKIP }, () => {
  const { a, b, dir } = pair(40, 40, bar(10), bar(11));
  assert.equal(run([a, b], { cwd: dir }).code, 0);
  const r = run(['--no-tolerate-1px', a, b], { cwd: dir });
  assert.equal(r.code, 1, r.stdout + r.stderr);
  assert.match(r.stdout, /^FAIL pixel-diff [\d.]+% \(80\/1600\)/m);
});

test('przesuniecie o 4 px => FAIL (kod 1), 15% (240/1600)', { skip: SKIP }, () => {
  const { a, b, dir } = pair(40, 40, bar(10), bar(14));
  const r = run(['--json', a, b], { cwd: dir });
  assert.equal(r.code, 1, r.stdout + r.stderr);
  const j = JSON.parse(r.stdout);
  assert.equal(j.verdict, 'FAIL');
  assert.equal(j.diffCount, 240);
  assert.equal(j.total, 1600);
  assert.ok(Math.abs(j.pct - 15) < 1e-6, String(j.pct));
  assert.equal(j.sizeMismatch, false);
  assert.equal(j.cropped, false);
});

test('prog --max-pct: pojedynczy piksel (0,0625%) PASS domyslnie, FAIL przy --max-pct 0.01', { skip: SKIP }, () => {
  const { a, b, dir } = pair(40, 40, () => WHITE, (x, y) => (x === 20 && y === 20 ? BLACK : WHITE));
  const ok = run(['--json', a, b], { cwd: dir });
  assert.equal(ok.code, 0, ok.stdout + ok.stderr);
  assert.equal(JSON.parse(ok.stdout).diffCount, 1);
  const bad = run(['--json', '--max-pct', '0.01', a, b], { cwd: dir });
  assert.equal(bad.code, 1, bad.stdout + bad.stderr);
  assert.equal(JSON.parse(bad.stdout).verdict, 'FAIL');
});

test('size-mismatch => kod 1, linia i JSON z sizeMismatch', { skip: SKIP }, () => {
  const { a, b, dir } = pair(40, 40, bar(10), bar(10), 40, 30);
  const r = run([a, b], { cwd: dir });
  assert.equal(r.code, 1, r.stdout + r.stderr);
  assert.match(r.stdout, /^FAIL pixel-diff size-mismatch \(40x40 vs 40x30\)\s*$/m);
  const j = JSON.parse(run(['--json', a, b], { cwd: dir }).stdout);
  assert.equal(j.verdict, 'FAIL');
  assert.equal(j.sizeMismatch, true);
});

test('--crop: obrazy o roznych rozmiarach porownane na wspolnym obszarze, cropped: true', { skip: SKIP }, () => {
  const { a, b, dir } = pair(40, 40, bar(10), bar(10), 40, 30);
  const r = run(['--crop', '--json', a, b], { cwd: dir });
  assert.equal(r.code, 0, r.stdout + r.stderr);
  const j = JSON.parse(r.stdout);
  assert.equal(j.verdict, 'PASS');
  assert.equal(j.cropped, true);
  assert.equal(j.sizeMismatch, false);
  assert.equal(j.total, 40 * 30);
});

test('--mask zakrywajacy roznice => PASS; maska obok roznicy => FAIL; maski powtarzalne', { skip: SKIP }, () => {
  const { a, b, dir } = pair(40, 40, bar(10), bar(14));
  assert.equal(run(['--mask', '8,0,5,40', a, b], { cwd: dir }).code, 1, 'maska tylko na lewym pasku nie kryje prawego');
  assert.equal(run(['--mask', '8,0,5,40', '--mask', '13,0,5,40', a, b], { cwd: dir }).code, 0);
  assert.equal(run(['--mask', '0,0,40,40', a, b], { cwd: dir }).code, 0);
  assert.equal(run(['--mask', '0,0,5,5', a, b], { cwd: dir }).code, 1, 'maska w rogu nie kryje roznicy');
});

test('--mask wzgledem lewego gornego rogu: maska na dolnej polowie nie kryje roznicy z gornej', { skip: SKIP }, () => {
  const top = (x, y) => (y < 10 && x >= 5 && x < 15 ? BLACK : WHITE);
  const { a, b, dir } = pair(40, 40, () => WHITE, top);
  assert.equal(run(['--mask', '0,20,40,20', a, b], { cwd: dir }).code, 1);
  assert.equal(run(['--mask', '0,0,40,12', a, b], { cwd: dir }).code, 0);
});

test('bledny format --mask albo --max-pct => kod 2', { skip: SKIP }, () => {
  const { a, b, dir } = pair(10, 10, () => WHITE, () => WHITE);
  assert.equal(run(['--mask', '1,2,3', a, b], { cwd: dir }).code, 2);
  assert.equal(run(['--mask', 'a,b,c,d', a, b], { cwd: dir }).code, 2);
  assert.equal(run(['--max-pct', 'abc', a, b], { cwd: dir }).code, 2);
});

test('nieczytelny PNG (smieci) albo brakujacy plik => kod 2, nie 1', { skip: SKIP }, () => {
  const { a, dir } = pair(10, 10, () => WHITE, () => WHITE);
  const junk = save(dir, 'junk.png', Buffer.from('to nie jest png'));
  const r = run([a, junk], { cwd: dir });
  assert.equal(r.code, 2, r.stdout + r.stderr);
  assert.doesNotMatch(r.stdout, /^(PASS|FAIL) pixel-diff/m);
  assert.equal(run([a, path.join(dir, 'brak.png')], { cwd: dir }).code, 2);
});

// --- --diff-out ---

test('--diff-out wewnatrz cwd zapisuje PNG o wymiarach obrazow', { skip: SKIP }, () => {
  const { a, b, dir } = pair(40, 40, bar(10), bar(14));
  const r = run(['--diff-out', path.join('out', 'diff.png'), a, b], { cwd: dir });
  assert.equal(r.code, 1, r.stdout + r.stderr);
  const out = path.join(dir, 'out', 'diff.png');
  assert.ok(fs.existsSync(out), 'brak pliku diff');
  const png = PNG.sync.read(fs.readFileSync(out));
  assert.equal(png.width, 40);
  assert.equal(png.height, 40);
});

test('--diff-out poza cwd (../ i sciezka bezwzgledna) => kod 2 i brak pliku', { skip: SKIP }, () => {
  const { a, b, dir } = pair(10, 10, () => WHITE, () => WHITE);
  const work = path.join(dir, 'work');
  fs.mkdirSync(work);
  const outside = path.join(dir, 'outside.png');
  const r1 = run(['--diff-out', path.join('..', 'outside.png'), a, b], { cwd: work });
  assert.equal(r1.code, 2, r1.stdout + r1.stderr);
  const r2 = run(['--diff-out', outside, a, b], { cwd: work });
  assert.equal(r2.code, 2, r2.stdout + r2.stderr);
  assert.equal(fs.existsSync(outside), false);
});

test('N8: --diff-out poza cwd => blad uzycia: jedna linia na stderr, kod 2, bez stosu', { skip: SKIP }, () => {
  const { a, b, dir } = pair(10, 10, () => WHITE, () => WHITE);
  const work = path.join(dir, 'work');
  fs.mkdirSync(work);
  for (const target of [path.join('..', 'outside.png'), path.join(dir, 'outside.png')]) {
    const r = run(['--diff-out', target, a, b], { cwd: work });
    assert.equal(r.code, 2, r.stdout + r.stderr);
    const lines = r.stderr.split(/\r?\n/).filter((l) => l.trim() !== '');
    assert.equal(lines.length, 1, `stderr powinien miec jedna linie, jest ${lines.length}: ${r.stderr}`);
    assert.doesNotMatch(r.stderr, /^\s+at\s/m, 'stos wywolan w stderr');
    assert.equal(r.stdout.trim(), '');
  }
});

// --- fixtury z cases.json ---

const CASES = JSON.parse(fs.readFileSync(path.join(FIXTURES, 'cases.json'), 'utf8')).cases;
for (const c of CASES) {
  test(`fixtura ${c.id}: werdykt ${c.expected.pixelDiff}, pct w ${JSON.stringify(c.expected.pixelDiffPctRange)}`, { skip: SKIP }, () => {
    const ref = path.join(FIXTURES, c.id, 'reference.png');
    const cand = path.join(FIXTURES, c.id, 'candidate.png');
    const r = run(['--json', ref, cand], { cwd: ROOT });
    assert.equal(r.code, c.expected.pixelDiff === 'PASS' ? 0 : 1, r.stdout + r.stderr);
    const j = JSON.parse(r.stdout);
    assert.equal(j.verdict, c.expected.pixelDiff);
    const [lo, hi] = c.expected.pixelDiffPctRange;
    assert.ok(j.pct >= lo && j.pct <= hi, `pct ${j.pct} poza [${lo}, ${hi}]`);
  });
}

test('fixtury: mirrored-icon ~9,3% (11,9% bez odpornosci 1 px), shifted-flag ~16,1%, near-identical ~0,035%', { skip: SKIP }, () => {
  const want = { 'mirrored-icon': [9.3, 1], 'shifted-flag': [16.1, 1], 'near-identical': [0.035, 0.01] };
  for (const [id, [pct, tol]] of Object.entries(want)) {
    const r = run(['--json', path.join(FIXTURES, id, 'reference.png'), path.join(FIXTURES, id, 'candidate.png')], { cwd: ROOT });
    const j = JSON.parse(r.stdout);
    assert.ok(Math.abs(j.pct - pct) <= tol, `${id}: ${j.pct}`);
  }
  const raw = JSON.parse(run(['--json', '--no-tolerate-1px', path.join(FIXTURES, 'mirrored-icon', 'reference.png'), path.join(FIXTURES, 'mirrored-icon', 'candidate.png')], { cwd: ROOT }).stdout);
  assert.ok(Math.abs(raw.pct - 11.9) <= 0.1, `mirrored-icon bez tolerancji: ${raw.pct}`);
});

// --- wyciek sekretu ---

// Atrapa skladana w czasie wykonania (bez literalu wygladajacego jak token).
const secretValue = () => ['fake', 'secret', String(process.pid), 'abcd'].join('-');

test('sekret z UI_VERIFY_SECRET_ENV w sciezce bledu nie trafia do stdout/stderr (bez zaleznosci)', () => {
  const secret = secretValue();
  const d = newDir('leak0');
  const r = run([path.join(d, `${secret}.png`), path.join(d, 'b.png')], {
    cacheRoot: emptyCache(),
    cwd: d,
    env: { UV_FAKE_SECRET: secret, UI_VERIFY_SECRET_ENV: 'UV_FAKE_SECRET' },
  });
  assert.ok([2, 3].includes(r.code), `kod ${r.code}`);
  assert.ok(!r.stdout.includes(secret) && !r.stderr.includes(secret), 'sekret w wyjsciu');
});

test('sekret z UI_VERIFY_SECRET_ENV w nazwie pliku nie wycieka (blad odczytu, kod 2) i jest zamaskowany', { skip: SKIP }, () => {
  const secret = secretValue();
  const { a, dir } = pair(10, 10, () => WHITE, () => WHITE);
  const env = { UV_FAKE_SECRET: secret, UI_VERIFY_SECRET_ENV: 'UV_FAKE_SECRET' };
  const r = run([a, path.join(dir, `${secret}.png`)], { cwd: dir, env });
  assert.equal(r.code, 2, r.stdout + r.stderr);
  assert.ok(!r.stdout.includes(secret) && !r.stderr.includes(secret), 'sekret w wyjsciu');
  for (const v of [encodeURIComponent(secret), Buffer.from(secret).toString('base64')]) {
    assert.ok(!r.stderr.includes(v) && !r.stdout.includes(v), 'wariant sekretu w wyjsciu');
  }
});

test('kontrola: sekret spoza UI_VERIFY_SECRET_ENV nie jest maskowany (dowod, ze maskowanie zalezy od listy)', { skip: SKIP }, () => {
  const secret = secretValue();
  const { a, dir } = pair(10, 10, () => WHITE, () => WHITE);
  const r = run([a, path.join(dir, `${secret}.png`)], { cwd: dir, env: { UV_FAKE_SECRET: secret } });
  assert.equal(r.code, 2, r.stdout + r.stderr);
  assert.ok((r.stdout + r.stderr).includes(secret), 'sciezka z nazwa pliku powinna byc w komunikacie bledu, by test wycieku mial sens');
});
