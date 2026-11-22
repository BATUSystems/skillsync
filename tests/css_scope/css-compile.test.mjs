// compileScss (S4): logika kompilacji LibSass przez sass.js na atrapie Sass i atrapie fs (bez prawdziwego pakietu).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// Dynamiczny import: brak modulu daje czerwony test, nie blad calego pliku.
const load = () => import('../../library/skills/ui-verify/scripts/lib/css-compile.mjs');

const DIR = path.join('/h', 'proj', 'scss');

// Atrapa fs: plaska mapa nazwa -> zawartosc (string), { dir: true } albo { link: true, to: 'x' } (symlink do pliku).
// Katalog DIR sam jest zwyklym katalogiem, chyba ze podano { dirLink: true }.
function fakeFs(entries, { dirLink = false } = {}) {
  const calls = { readdir: [], read: [], stat: [], lstat: [] };
  const lookup = (p) => {
    if (p === DIR) return { kind: dirLink ? 'link' : 'dir', size: 0 };
    const name = path.relative(DIR, p);
    const e = entries[name];
    if (e === undefined) { const err = new Error('ENOENT ' + p); err.code = 'ENOENT'; throw err; }
    if (typeof e === 'object' && e.dir === true) return { kind: 'dir', size: 0 };
    if (typeof e === 'object' && e.link === true) return { kind: 'link', size: 0 };
    return { kind: 'file', size: Buffer.byteLength(String(e)) };
  };
  // statSync podaza za symlinkiem (jak prawdziwy fs): symlink-plik wyglada jak zwykly plik; lstatSync nie.
  const stat = (p) => { const r = lookup(p); const isDir = r.kind === 'dir' || (r.kind === 'link' && p === DIR); return { isFile: () => !isDir, isDirectory: () => isDir, isSymbolicLink: () => false, size: r.kind === 'link' ? 5 : r.size }; };
  const lstat = (p) => { const r = lookup(p); return { isFile: () => r.kind === 'file', isDirectory: () => r.kind === 'dir', isSymbolicLink: () => r.kind === 'link', size: r.size }; };
  return {
    calls,
    readdirSync(d) { calls.readdir.push(d); return Object.keys(entries); },
    readFileSync(p) { calls.read.push(p); const e = entries[path.relative(DIR, p)]; return typeof e === 'object' ? 'x' : e; },
    statSync(p) { calls.stat.push(p); return stat(p); },
    lstatSync(p) { calls.lstat.push(p); return lstat(p); },
  };
}

// Atrapa Sass: zapisuje kolejnosc wywolan; writeFile moze miec callback (jak w sass.js).
function fakeSass({ result = { status: 0, text: 'a { b: c; }' }, throws = null, rejectWrite = false } = {}) {
  const log = [];
  const Sass = {
    style: { nested: 0, expanded: 1, compact: 2, compressed: 3 },
    writeFile(name, content, cb) {
      log.push({ op: 'write', name, content });
      if (typeof cb === 'function') cb(!rejectWrite);
      return !rejectWrite;
    },
    compileFile(entry, opts, cb) {
      log.push({ op: 'compile', entry, opts });
      if (throws) throw throws;
      setImmediate(() => cb(typeof result === 'function' ? result() : result));
    },
  };
  return { Sass, log };
}
const writes = (log) => log.filter((l) => l.op === 'write');
const compiles = (log) => log.filter((l) => l.op === 'compile');

async function run(entries, { sass = {}, entry = 'main.scss', ...opts } = {}) {
  const { compileScss } = await load();
  const fs = fakeFs(entries);
  const { Sass, log } = fakeSass(sass);
  const r = await compileScss({ Sass, dir: DIR, entry, fs, ...opts });
  return { r, log, fs, Sass };
}

test('compileScss: sukces => ok, css z r.text, files, brak bledu i ostrzezen', async () => {
  const { r } = await run({ 'main.scss': '@import "vars";', '_vars.scss': '$c: red;' });
  assert.deepEqual(Object.keys(r).sort(), ['css', 'error', 'files', 'ok', 'warnings']);
  assert.equal(r.ok, true);
  assert.equal(r.css, 'a { b: c; }');
  assert.equal(r.error, null);
  assert.equal(r.files, 2);
  assert.deepEqual(r.warnings, []);
});

test('compileScss: wszystkie writeFile (nazwa + tresc) przed compileFile; compileFile dostaje nazwe wejscia i styl expanded', async () => {
  const { log, Sass } = await run({ 'main.scss': 'a{b:c}', '_vars.scss': '$c: red;', '_mix.scss': '@mixin m {}' });
  const ops = log.map((l) => l.op);
  assert.deepEqual(ops, ['write', 'write', 'write', 'compile']);
  const w = Object.fromEntries(writes(log).map((x) => [x.name, x.content]));
  assert.deepEqual(w, { 'main.scss': 'a{b:c}', '_vars.scss': '$c: red;', '_mix.scss': '@mixin m {}' });
  const [c] = compiles(log);
  assert.equal(c.entry, 'main.scss');
  assert.equal(c.opts.style, Sass.style.expanded);
});

test('compileScss: zawartosc plikow jest tekstem (Buffer z fs jest zamieniany na string)', async () => {
  const { compileScss } = await load();
  const fs = fakeFs({ 'main.scss': 'x' });
  fs.readFileSync = () => Buffer.from('a { b: c; }');
  const { Sass, log } = fakeSass();
  await compileScss({ Sass, dir: DIR, entry: 'main.scss', fs });
  assert.equal(typeof writes(log)[0].content, 'string');
  assert.equal(writes(log)[0].content, 'a { b: c; }');
});

test('compileScss: czyta tylko pierwszy poziom katalogu; podkatalog (nawet *.scss) jest pomijany', async () => {
  const { r, log } = await run({ 'main.scss': 'a{}', 'nested.scss': { dir: true } });
  assert.deepEqual(writes(log).map((w) => w.name), ['main.scss']);
  assert.equal(r.files, 1);
  assert.equal(r.ok, true);
});

test('compileScss: pliki o nazwach spoza ^[A-Za-z0-9._-]+\\.scss$ sa pomijane z ostrzezeniem, kompilacja trwa', async () => {
  const bad = ['notes.txt', 'style.css', 'with space.scss', 'zażółć.scss', '..scss.bak', 'a;b.scss', 'x.SCSS'];
  const entries = { 'main.scss': 'a{}', '_ok-1.2_x.scss': '$a: 1;' };
  for (const b of bad) entries[b] = '$z: 1;';
  const { r, log } = await run(entries);
  assert.deepEqual(writes(log).map((w) => w.name).sort(), ['_ok-1.2_x.scss', 'main.scss']);
  assert.equal(r.ok, true);
  assert.equal(r.files, 2);
  assert.equal(r.warnings.length, bad.length);
  for (const b of bad) assert.ok(r.warnings.some((w) => w.includes(b)), `ostrzezenie o ${b}`);
});

test('compileScss: nazwa z separatorem sciezki (../x.scss) nigdy nie trafia do Sass.writeFile', async () => {
  const { r, log } = await run({ 'main.scss': 'a{}', '../evil.scss': '$a: 1;', 'sub/x.scss': '$a: 1;' });
  assert.deepEqual(writes(log).map((w) => w.name), ['main.scss']);
  assert.equal(r.warnings.length, 2);
});

test('compileScss: limit domyslny 500 plikow: 500 przechodzi, 501 => ok:false bez compileFile', async () => {
  const mk = (n) => Object.fromEntries(Array.from({ length: n }, (_, i) => [i === 0 ? 'main.scss' : `_p${i}.scss`, 'a{}']));
  const okRun = await run(mk(500));
  assert.equal(okRun.r.ok, true);
  assert.equal(okRun.r.files, 500);
  const over = await run(mk(501));
  assert.equal(over.r.ok, false);
  assert.equal(over.r.css, null);
  assert.match(over.r.error, /\S/);
  assert.equal(compiles(over.log).length, 0);
});

test('compileScss: maxFiles jest parametrem', async () => {
  const { r, log } = await run({ 'main.scss': 'a{}', '_a.scss': '', '_b.scss': '' }, { maxFiles: 2 });
  assert.equal(r.ok, false);
  assert.equal(compiles(log).length, 0);
  const okRun = await run({ 'main.scss': 'a{}', '_a.scss': '' }, { maxFiles: 2 });
  assert.equal(okRun.r.ok, true);
});

test('compileScss: pliki pominiete przez filtr nazw nie wliczaja sie do maxFiles', async () => {
  const { r } = await run({ 'main.scss': 'a{}', 'a.txt': '', 'b.txt': '', 'c.txt': '' }, { maxFiles: 1 });
  assert.equal(r.ok, true);
  assert.equal(r.files, 1);
});

test('compileScss: plik wiekszy niz maxBytes => ok:false (domyslnie 2000000; rowny limitowi przechodzi)', async () => {
  const exact = await run({ 'main.scss': 'a'.repeat(2000000) });
  assert.equal(exact.r.ok, true);
  const big = await run({ 'main.scss': 'a{}', '_big.scss': 'a'.repeat(2000001) });
  assert.equal(big.r.ok, false);
  assert.equal(big.r.css, null);
  assert.match(big.r.error, /_big\.scss/);
  assert.equal(compiles(big.log).length, 0);
  const custom = await run({ 'main.scss': 'a'.repeat(11) }, { maxBytes: 10 });
  assert.equal(custom.r.ok, false);
});

test('compileScss: entry spoza katalogu (brak pliku, sciezka, inne rozszerzenie) => ok:false bez compileFile', async () => {
  for (const entry of ['missing.scss', '../main.scss', '/h/other/main.scss', 'sub/main.scss', 'main.css']) {
    const { r, log } = await run({ 'main.scss': 'a{}' }, { entry });
    assert.equal(r.ok, false, entry);
    assert.equal(r.css, null, entry);
    assert.match(r.error, /\S/, entry);
    assert.equal(compiles(log).length, 0, entry);
  }
});

test('compileScss: status !== 0 => ok:false, css null, error z r.formatted (przed r.message)', async () => {
  const { r } = await run({ 'main.scss': 'a{' }, { sass: { result: { status: 3, formatted: 'Error: bad\n  on line 1 of main.scss', message: 'bad', line: 1 } } });
  assert.equal(r.ok, false);
  assert.equal(r.css, null);
  assert.equal(r.error, 'Error: bad\n  on line 1 of main.scss');
});

test('compileScss: bez formatted blad pochodzi z r.message', async () => {
  const { r } = await run({ 'main.scss': 'a{' }, { sass: { result: { status: 1, message: 'only message' } } });
  assert.equal(r.ok, false);
  assert.equal(r.error, 'only message');
});

test('compileScss: dowolny status inny niz 0 jest bledem (1, 2, 3, 99, -1)', async () => {
  for (const status of [1, 2, 3, 99, -1]) {
    const { r } = await run({ 'main.scss': 'a{}' }, { sass: { result: { status, formatted: 'f', text: 'ignored' } } });
    assert.equal(r.ok, false, String(status));
    assert.equal(r.css, null, String(status));
  }
});

test('compileScss: status 0 z pustym tekstem to sukces z pustym css (nie null)', async () => {
  const { r } = await run({ 'main.scss': '' }, { sass: { result: { status: 0, text: '' } } });
  assert.equal(r.ok, true);
  assert.equal(r.css, '');
  assert.equal(r.error, null);
});

test('compileScss: dlugi komunikat bledu skracany do 4000 znakow (4000 zostaje, 4001 skrocone)', async () => {
  const exact = await run({ 'main.scss': 'a{' }, { sass: { result: { status: 3, formatted: 'x'.repeat(4000) } } });
  assert.equal(exact.r.error.length, 4000);
  const long = await run({ 'main.scss': 'a{' }, { sass: { result: { status: 3, formatted: 'y'.repeat(50000) } } });
  assert.ok(long.r.error.length <= 4000);
  assert.ok(long.r.error.length >= 3900, 'skrocenie nie obcina wiecej niz potrzeba');
  assert.ok(long.r.error.startsWith('yyyy'));
  const msg = await run({ 'main.scss': 'a{' }, { sass: { result: { status: 3, message: 'z'.repeat(9000) } } });
  assert.ok(msg.r.error.length <= 4000);
});

test('compileScss: wyjatek z Sass.compileFile => ok:false z nazwa typu i komunikatem, bez odrzucenia obietnicy', async () => {
  const { r } = await run({ 'main.scss': 'a{}' }, { sass: { throws: new TypeError('boom happened') } });
  assert.equal(r.ok, false);
  assert.equal(r.css, null);
  assert.match(r.error, /TypeError/);
  assert.match(r.error, /boom happened/);
});

test('compileScss: wyjatek z dlugim komunikatem jest skracany do 4000 znakow', async () => {
  const { r } = await run({ 'main.scss': 'a{}' }, { sass: { throws: new Error('q'.repeat(20000)) } });
  assert.equal(r.ok, false);
  assert.ok(r.error.length <= 4000);
  assert.match(r.error, /Error/);
});

test('compileScss: wyjatek z Sass.writeFile tez konczy sie ok:false (nie wyjatkiem)', async () => {
  const { compileScss } = await load();
  const { Sass } = fakeSass();
  Sass.writeFile = () => { throw new RangeError('disk full'); };
  const r = await compileScss({ Sass, dir: DIR, entry: 'main.scss', fs: fakeFs({ 'main.scss': 'a{}' }) });
  assert.equal(r.ok, false);
  assert.match(r.error, /RangeError/);
});

test('compileScss: inny wyjatek fs (readdirSync, tu EIO) => ok:false, nie wyjatek (ENOENT/EACCES to blad uzycia: patrz CLI)', async () => {
  const { compileScss } = await load();
  const { Sass, log } = fakeSass();
  const fs = fakeFs({});
  fs.readdirSync = () => { const e = new Error('EIO'); e.code = 'EIO'; throw e; };
  const r = await compileScss({ Sass, dir: DIR, entry: 'main.scss', fs });
  assert.equal(r.ok, false);
  assert.equal(r.css, null);
  assert.match(r.error, /\S/);
  assert.equal(log.length, 0);
});

test('compileScss: pusty plik jest zapisywany i kompilowany (tresc pusta, nie pominieta)', async () => {
  const { r, log } = await run({ 'main.scss': '' });
  assert.equal(writes(log).length, 1);
  assert.equal(writes(log)[0].content, '');
  assert.equal(r.ok, true);
});

test('compileScss: readdirSync dostaje dokladnie dir; pliki czytane sa z dir (bez wyjscia poza katalog)', async () => {
  const { fs } = await run({ 'main.scss': 'a{}', '_v.scss': '$a: 1;' });
  assert.deepEqual(fs.calls.readdir, [DIR]);
  assert.ok(fs.calls.read.length >= 2);
  for (const p of [...fs.calls.read, ...fs.calls.stat]) {
    assert.equal(path.dirname(p), DIR, p);
  }
  for (const p of fs.calls.lstat) assert.ok(p === DIR || path.dirname(p) === DIR, `lstat poza katalogiem: ${p}`);
});

test('compileScss: wynik wywolan jest deterministyczny (dwa biegi daja ten sam wynik)', async () => {
  const entries = { 'main.scss': '@import "v";', '_v.scss': '$a: 1;' };
  const a = await run(entries);
  const b = await run(entries);
  assert.deepEqual(a.r, b.r);
  assert.deepEqual(a.log, b.log);
});

// ---- B2: symlinki (lstatSync) ----

const noAbs = (text) => {
  assert.ok(!text.includes(DIR), `sciezka bezwzgledna w komunikacie: ${text}`);
  assert.doesNotMatch(text, /(^|[\s'"(])\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+/, `sciezka bezwzgledna w komunikacie: ${text}`);
};

test('compileScss: symlink-plik w katalogu => ok:false z angielskim komunikatem bez sciezek bezwzglednych, bez compileFile', async () => {
  const { r, log } = await run({ 'main.scss': 'a{}', '_evil.scss': { link: true } });
  assert.equal(r.ok, false);
  assert.equal(r.css, null);
  assert.match(r.error, /symlink|symbolic/i);
  assert.match(r.error, /_evil\.scss/);
  noAbs(r.error);
  assert.equal(compiles(log).length, 0);
  assert.equal(writes(log).length, 0, 'zawartosc symlinku nie moze trafic do Sass');
});

test('compileScss: symlink jako plik wejsciowy => ok:false (tez gdy to jedyny plik)', async () => {
  const { r, log } = await run({ 'main.scss': { link: true } });
  assert.equal(r.ok, false);
  assert.equal(compiles(log).length, 0);
  assert.equal(writes(log).length, 0);
});

test('compileScss: katalog SCSS bedacy symlinkiem => ok:false, nic nie czytane', async () => {
  const { compileScss } = await load();
  const { Sass, log } = fakeSass();
  const fsx = fakeFs({ 'main.scss': 'a{}' }, { dirLink: true });
  const r = await compileScss({ Sass, dir: DIR, entry: 'main.scss', fs: fsx });
  assert.equal(r.ok, false);
  assert.equal(r.css, null);
  assert.match(r.error, /symlink|symbolic/i);
  noAbs(r.error);
  assert.equal(log.length, 0);
  assert.deepEqual(fsx.calls.read, []);
});

test('compileScss: zwykly plik i podkatalog nadal przechodza (kontrola: lstat nie odrzuca wszystkiego)', async () => {
  const { r } = await run({ 'main.scss': 'a{}', '_v.scss': '$a: 1;', 'sub': { dir: true } });
  assert.equal(r.ok, true, r.error);
  assert.equal(r.files, 2);
});

const SYM = (() => {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'css-compile-probe-'));
  try {
    fs.writeFileSync(path.join(d, 't'), 'x');
    fs.symlinkSync(path.join(d, 't'), path.join(d, 'l'), 'file');
    return false;
  } catch (e) {
    return `needs-symlink: ${e.code || e.message}`;
  } finally {
    fs.rmSync(d, { recursive: true, force: true });
  }
})();

test('compileScss na prawdziwym fs: symlink-plik i symlink-katalog sa odrzucane, plik docelowy nie jest czytany', { skip: SYM }, async () => {
  const { compileScss } = await load();
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'css-compile-sym-'));
  try {
    const secretDir = path.join(base, 'outside');
    fs.mkdirSync(secretDir);
    fs.writeFileSync(path.join(secretDir, 'secret.scss'), '.leak { c: d }');
    const dir = path.join(base, 'scss');
    fs.mkdirSync(dir);
    fs.writeFileSync(path.join(dir, 'main.scss'), 'a{}');
    fs.symlinkSync(path.join(secretDir, 'secret.scss'), path.join(dir, '_link.scss'), 'file');
    const a = fakeSass();
    const r1 = await compileScss({ Sass: a.Sass, dir, entry: 'main.scss', fs });
    assert.equal(r1.ok, false);
    assert.ok(!r1.error.includes(base), r1.error);
    assert.equal(a.log.length, 0);
    const linkDir = path.join(base, 'linked');
    fs.symlinkSync(dir, linkDir, 'dir');
    fs.rmSync(path.join(dir, '_link.scss'));
    const b = fakeSass();
    const r2 = await compileScss({ Sass: b.Sass, dir: linkDir, entry: 'main.scss', fs });
    assert.equal(r2.ok, false);
    assert.ok(!r2.error.includes(base), r2.error);
    assert.equal(b.log.length, 0);
    const c = fakeSass();
    const r3 = await compileScss({ Sass: c.Sass, dir, entry: 'main.scss', fs });
    assert.equal(r3.ok, true, r3.error);
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

// ---- B9: limit sumaryczny ----

test('compileScss: maxTotalBytes jest parametrem; suma rowna limitowi przechodzi, o bajt wiecej => ok:false bez compileFile', async () => {
  const entries = { 'main.scss': 'a'.repeat(10), '_a.scss': 'b'.repeat(10), '_b.scss': 'c'.repeat(10) };
  const exact = await run(entries, { maxTotalBytes: 30 });
  assert.equal(exact.r.ok, true, exact.r.error);
  const over = await run(entries, { maxTotalBytes: 29 });
  assert.equal(over.r.ok, false);
  assert.equal(over.r.css, null);
  assert.match(over.r.error, /\S/);
  assert.equal(compiles(over.log).length, 0);
  assert.equal(writes(over.log).length, 0, 'nic nie trafia do Sass po przekroczeniu limitu sumarycznego');
});

test('compileScss: domyslny maxTotalBytes 16 000 000: 8 plikow po 1 900 000 B przechodzi (15,2 MB), 9 (17,1 MB) => ok:false mimo ze kazdy plik < maxBytes', async () => {
  const mk = (n) => Object.fromEntries(Array.from({ length: n }, (_, i) => [i === 0 ? 'main.scss' : `_p${i}.scss`, 'x'.repeat(1900000)]));
  const ok = await run(mk(8));
  assert.equal(ok.r.ok, true, ok.r.error);
  const over = await run(mk(9));
  assert.equal(over.r.ok, false);
  assert.equal(compiles(over.log).length, 0);
});
