import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const load = () => import('../../library/skills/ui-verify/scripts/lib/runtime.mjs');
const loadPaths = () => import('../../library/skills/ui-verify/scripts/lib/paths.mjs');
const loadDeps = () => import('../../library/skills/ui-verify/scripts/lib/deps.mjs');

const SKILL = path.resolve('/fake/skills/ui-verify');
const PKG = '{"name":"ui-verify","version":"1.0.0"}';
const LOCK = '{"lockfileVersion":3,"packages":{}}';

function fakeFs(files) {
  const map = new Map(Object.entries(files));
  const accessed = [];
  const reads = [];
  const ownUid = typeof process.getuid === 'function' ? process.getuid() : 0;
  return {
    accessed,
    reads,
    existsSync(p) { accessed.push(p); return map.has(p) || [...map.keys()].some((k) => k.startsWith(p + path.sep)); },
    // Kontrola bezpieczenstwa cache (B4) dotyka statSync: domyslnie katalog wlasciciela bez zapisu dla g/o.
    statSync() { return { uid: ownUid, mode: 0o40755, isDirectory: () => true }; },
    lstatSync() { return { uid: ownUid, mode: 0o40755, isDirectory: () => true, isSymbolicLink: () => false }; },
    readFileSync(p) {
      accessed.push(p);
      reads.push(p);
      if (!map.has(p)) { const e = new Error('ENOENT ' + p); e.code = 'ENOENT'; throw e; }
      return map.get(p);
    },
  };
}

const skillFiles = (pkg = PKG, lock = LOCK) => ({
  [path.join(SKILL, 'package.json')]: pkg,
  [path.join(SKILL, 'package-lock.json')]: lock,
});

async function expected({ platform, env, homedir, pkg = PKG, lock = LOCK }) {
  const { cacheDirFor, defaultCacheRoot, computeHash } = await loadPaths();
  return cacheDirFor(defaultCacheRoot({ platform, env, homedir }), computeHash([pkg, lock]), platform);
}

test('resolveCacheDir: korzen domyslny + hash z package.json i package-lock.json skilla (linux)', async () => {
  const { resolveCacheDir } = await load();
  const env = { XDG_CACHE_HOME: '/xdg' };
  const fs = fakeFs(skillFiles());
  const got = resolveCacheDir({ skillDir: SKILL, env, platform: 'linux', homedir: '/fake-home/u', fs });
  assert.equal(got, await expected({ platform: 'linux', env, homedir: '/fake-home/u' }));
  assert.ok(got.startsWith('/xdg/skillsync/ui-verify/'), got);
});

test('resolveCacheDir: UI_VERIFY_CACHE_DIR jest korzeniem, hash dopisany jako podkatalog', async () => {
  const { resolveCacheDir } = await load();
  const env = { UI_VERIFY_CACHE_DIR: '/custom/root' };
  const got = resolveCacheDir({ skillDir: SKILL, env, platform: 'linux', homedir: '/fake-home/u', fs: fakeFs(skillFiles()) });
  assert.equal(got, await expected({ platform: 'linux', env, homedir: '/fake-home/u' }));
  assert.ok(got.startsWith('/custom/root/'));
  assert.match(got.slice('/custom/root/'.length), /^[0-9a-f]{16}$/);
});

test('resolveCacheDir: win32 i darwin uzywaja swoich korzeni', async () => {
  const { resolveCacheDir } = await load();
  for (const [platform, env, homedir] of [
    ['win32', { LOCALAPPDATA: 'D:\\h\\AppData\\Local' }, 'D:\\h'],
    ['darwin', {}, '/Users/u'],
  ]) {
    const got = resolveCacheDir({ skillDir: SKILL, env, platform, homedir, fs: fakeFs(skillFiles()) });
    assert.equal(got, await expected({ platform, env, homedir }), platform);
  }
});

test('resolveCacheDir: zmiana zawartosci pliku zmienia hash, ta sama zawartosc daje ten sam katalog', async () => {
  const { resolveCacheDir } = await load();
  const base = { skillDir: SKILL, env: {}, platform: 'linux', homedir: '/fake-home/u' };
  const a = resolveCacheDir({ ...base, fs: fakeFs(skillFiles()) });
  const a2 = resolveCacheDir({ ...base, fs: fakeFs(skillFiles()) });
  const b = resolveCacheDir({ ...base, fs: fakeFs(skillFiles(PKG, LOCK.replace('3', '2'))) });
  const c = resolveCacheDir({ ...base, fs: fakeFs(skillFiles(PKG.replace('1.0.0', '1.0.1'), LOCK)) });
  assert.equal(a, a2);
  assert.notEqual(a, b);
  assert.notEqual(a, c);
});

// Od B4 resolveCacheDir sprawdza tez katalog cache (existsSync/statSync), wiec ograniczenie dotyczy ODCZYTU TRESCI plikow.
test('resolveCacheDir: odczytuje tresc tylko dwoch plikow skilla', async () => {
  const { resolveCacheDir } = await load();
  const fs = fakeFs(skillFiles());
  resolveCacheDir({ skillDir: SKILL, env: {}, platform: 'linux', homedir: '/fake-home/u', fs });
  const allowed = new Set([path.join(SKILL, 'package.json'), path.join(SKILL, 'package-lock.json')]);
  assert.ok(fs.reads.length >= 2);
  for (const p of fs.reads) assert.ok(allowed.has(p), `nieoczekiwany odczyt: ${p}`);
});

test('resolveCacheDir: brak package.json albo lockfile => DepsError not-installed (exit 3)', async () => {
  const { resolveCacheDir } = await load();
  const { DepsError } = await loadDeps();
  const base = { skillDir: SKILL, env: {}, platform: 'linux', homedir: '/fake-home/u' };
  const noLock = { [path.join(SKILL, 'package.json')]: PKG };
  const noPkg = { [path.join(SKILL, 'package-lock.json')]: LOCK };
  for (const files of [{}, noLock, noPkg]) {
    assert.throws(() => resolveCacheDir({ ...base, fs: fakeFs(files) }), (e) => e instanceof DepsError && e.code === 'not-installed' && e.exit === 3);
  }
});

test('skillDirOf: rodzic katalogu scripts (dwa poziomy nad plikiem skryptu)', async () => {
  const { skillDirOf } = await load();
  const file = path.join(path.resolve('/fake/skills/ui-verify'), 'scripts', 'pixel-diff.mjs');
  assert.equal(skillDirOf(pathToFileURL(file).href), path.resolve('/fake/skills/ui-verify'));
});

test('skillDirOf: dekoduje znaki specjalne w URL (spacja, polskie litery)', async () => {
  const { skillDirOf } = await load();
  const root = path.resolve('/fake/moje skille/ui-wer\u00fc');
  const file = path.join(root, 'scripts', 'pixel-diff.mjs');
  assert.equal(skillDirOf(pathToFileURL(file).href), root);
});

// Atrapa pixelmatch: zapisuje (255,0,0,255) tam, gdzie piksele sie roznia, szary w pozostalych; (255,255,0) dla AA.
function fakePixelmatch({ aa = new Set() } = {}) {
  const calls = [];
  const fn = (img1, img2, output, width, height, options) => {
    calls.push({ img1, img2, output, width, height, options });
    let n = 0;
    for (let i = 0; i < width * height; i++) {
      const o = i * 4;
      const differ = [0, 1, 2, 3].some((c) => img1[o + c] !== img2[o + c]);
      let px = [200, 200, 200, 255];
      if (differ && aa.has(i)) px = options.aaColor;
      else if (differ) { px = options.diffColor; n++; }
      output[o] = px[0]; output[o + 1] = px[1]; output[o + 2] = px[2]; output[o + 3] = px[3] ?? 255;
    }
    return n;
  };
  fn.calls = calls;
  return fn;
}

const img = (w, h, fill) => {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let i = 0; i < w * h; i++) data.set(fill(i), i * 4);
  return { width: w, height: h, data };
};

test('makePixelmatchFlagger: wektor 0/1 (rozny = 1), dlugosc width*height', async () => {
  const { makePixelmatchFlagger } = await load();
  const pm = fakePixelmatch();
  const flag = makePixelmatchFlagger(pm, {});
  const a = img(3, 2, () => [10, 10, 10, 255]);
  const b = img(3, 2, (i) => (i === 1 || i === 4 ? [250, 10, 10, 255] : [10, 10, 10, 255]));
  const out = flag(a, b);
  assert.equal(out.length, 6);
  assert.deepEqual([...out], [0, 1, 0, 0, 1, 0]);
  assert.ok(out instanceof Uint8Array);
});

test('makePixelmatchFlagger: identyczne obrazy => same zera', async () => {
  const { makePixelmatchFlagger } = await load();
  const flag = makePixelmatchFlagger(fakePixelmatch(), {});
  const a = img(4, 4, () => [1, 2, 3, 255]);
  assert.deepEqual([...flag(a, img(4, 4, () => [1, 2, 3, 255]))], new Array(16).fill(0));
});

test('makePixelmatchFlagger: kolor AA (255,255,0) nie jest roznica, dokladnie (255,0,0) jest', async () => {
  const { makePixelmatchFlagger } = await load();
  const flag = makePixelmatchFlagger(fakePixelmatch({ aa: new Set([2]) }), {});
  const a = img(4, 1, () => [0, 0, 0, 255]);
  const b = img(4, 1, (i) => (i === 0 || i === 2 ? [255, 255, 255, 255] : [0, 0, 0, 255]));
  assert.deepEqual([...flag(a, b)], [1, 0, 0, 0]);
});

test('makePixelmatchFlagger: opcje wywolania (domyslne threshold 0.1, includeAA false, kolory, alpha 0, output, wymiary)', async () => {
  const { makePixelmatchFlagger } = await load();
  const pm = fakePixelmatch();
  makePixelmatchFlagger(pm, {})(img(5, 3, () => [0, 0, 0, 255]), img(5, 3, () => [0, 0, 0, 255]));
  assert.equal(pm.calls.length, 1);
  const c = pm.calls[0];
  assert.equal(c.width, 5);
  assert.equal(c.height, 3);
  assert.ok(c.output && c.output.length === 5 * 3 * 4, 'output musi byc buforem diff');
  assert.equal(c.options.threshold, 0.1);
  assert.equal(c.options.includeAA, false);
  assert.deepEqual([...c.options.diffColor], [255, 0, 0]);
  assert.deepEqual([...c.options.aaColor], [255, 255, 0]);
  assert.equal(c.options.alpha, 0);
});

test('makePixelmatchFlagger: nadpisanie threshold i includeAA trafia do pixelmatch', async () => {
  const { makePixelmatchFlagger } = await load();
  const pm = fakePixelmatch();
  const a = img(2, 2, () => [0, 0, 0, 255]);
  makePixelmatchFlagger(pm, { threshold: 0.3, includeAA: true })(a, a);
  assert.equal(pm.calls[0].options.threshold, 0.3);
  assert.equal(pm.calls[0].options.includeAA, true);
});

test('makePixelmatchFlagger: nie mutuje obrazow wejsciowych', async () => {
  const { makePixelmatchFlagger } = await load();
  const a = img(3, 1, () => [0, 0, 0, 255]);
  const b = img(3, 1, (i) => (i === 1 ? [255, 255, 255, 255] : [0, 0, 0, 255]));
  const sa = [...a.data];
  const sb = [...b.data];
  makePixelmatchFlagger(fakePixelmatch(), {})(a, b);
  assert.deepEqual([...a.data], sa);
  assert.deepEqual([...b.data], sb);
});

test('decodePng / encodePng: deleguja do PNG.sync.read / PNG.sync.write', async () => {
  const { decodePng, encodePng } = await load();
  const seen = { read: [], write: [] };
  const PNG = {
    sync: {
      read(buf) { seen.read.push(buf); return { width: 2, height: 1, data: Buffer.from([1, 2, 3, 4, 5, 6, 7, 8]), extra: 'x' }; },
      write(image) { seen.write.push(image); return Buffer.from('encoded'); },
    },
  };
  const input = Buffer.from('raw');
  const dec = decodePng(PNG, input);
  assert.equal(seen.read[0], input);
  assert.equal(dec.width, 2);
  assert.equal(dec.height, 1);
  assert.equal(dec.data.length, 8);
  const enc = encodePng(PNG, { width: 2, height: 1, data: new Uint8ClampedArray(8) });
  assert.ok(Buffer.isBuffer(enc));
  assert.equal(enc.toString(), 'encoded');
  assert.equal(seen.write[0].width, 2);
  assert.equal(seen.write[0].height, 1);
  assert.equal(seen.write[0].data.length, 8);
});

test('decodePng: blad dekodera propaguje sie (nie jest polykany)', async () => {
  const { decodePng } = await load();
  const PNG = { sync: { read() { throw new Error('bad png'); } } };
  assert.throws(() => decodePng(PNG, Buffer.from('x')), /bad png/);
});

async function loaderFixture(files) {
  const { loadImaging } = await load();
  const calls = { required: [], imported: [] };
  const FakePNG = { sync: {} };
  const fakePm = () => 0;
  const cache = path.resolve('/fake/cache/h');
  const fs = fakeFs(files(cache));
  const run = () => loadImaging({
    cacheDir: cache,
    env: {},
    fs,
    requireFactory: () => (spec) => { calls.required.push(spec); return { PNG: FakePNG }; },
    importer: async (url) => { calls.imported.push(url); return { default: fakePm }; },
  });
  return { run, calls, FakePNG, fakePm };
}

test('loadImaging: zwraca { PNG, pixelmatch } (pngjs CJS -> PNG, pixelmatch ESM -> default)', async () => {
  const { run, calls, FakePNG, fakePm } = await loaderFixture((cache) => ({
    [path.join(cache, 'node_modules', 'pngjs', 'package.json')]: JSON.stringify({ name: 'pngjs', version: '7.0.0', main: 'lib/png.js' }),
    [path.join(cache, 'node_modules', 'pixelmatch', 'package.json')]: JSON.stringify({ name: 'pixelmatch', version: '8.0.0', type: 'module', exports: './index.js' }),
  }));
  const r = await run();
  assert.equal(r.PNG, FakePNG);
  assert.equal(r.pixelmatch, fakePm);
  assert.equal(calls.required.length, 1);
  assert.equal(calls.imported.length, 1);
});

test('loadImaging: brak pngjs albo pixelmatch => DepsError exit 3', async () => {
  const { DepsError } = await loadDeps();
  const { run } = await loaderFixture(() => ({}));
  await assert.rejects(run(), (e) => e instanceof DepsError && e.exit === 3);
});

// --- B4: kontrola cache jak przy instalacji, zanim kod z cache zostanie zaladowany; blad konfiguracji => kod 2 ---

const POSIX_ONLY = { skip: process.platform === 'win32' ? 'needs-posix: bity uid/mode nie dotycza Windows' : false };
const isCacheRefusal = (e) => e && e.name === 'DepsError' && e.exit === 2;

// fs, w ktorym cache istnieje i ma zadane wlasciwosci (statSync/lstatSync).
function fsWithCacheStat(stat) {
  const base = fakeFs(skillFiles());
  const mk = () => ({ isDirectory: () => true, isSymbolicLink: () => false, ...stat });
  return { ...base, existsSync: () => true, statSync: mk, lstatSync: mk };
}
const ownUid = () => (typeof process.getuid === 'function' ? process.getuid() : 0);

test('B4 resolveCacheDir: wzgledna sciezka UI_VERIFY_CACHE_DIR => DepsError z kodem 2 (nie 3)', async () => {
  const { resolveCacheDir } = await load();
  for (const rel of ['relative/cache', './cache', '../cache']) {
    assert.throws(() => resolveCacheDir({ skillDir: SKILL, env: { UI_VERIFY_CACHE_DIR: rel }, platform: 'linux', homedir: '/fake-home/u', fs: fakeFs(skillFiles()) }), isCacheRefusal, rel);
  }
});

test('B4 resolveCacheDir: cache wewnatrz katalogu skilla => DepsError kod 2', async () => {
  const { resolveCacheDir } = await load();
  const env = { UI_VERIFY_CACHE_DIR: path.join(SKILL, 'cache') };
  assert.throws(() => resolveCacheDir({ skillDir: SKILL, env, platform: 'linux', homedir: '/fake-home/u', fs: fakeFs(skillFiles()) }), isCacheRefusal);
});

test('B4 resolveCacheDir: cache obcego wlasciciela => DepsError kod 2', POSIX_ONLY, async () => {
  const { resolveCacheDir } = await load();
  const fs = fsWithCacheStat({ uid: ownUid() + 1, mode: 0o40700 });
  assert.throws(() => resolveCacheDir({ skillDir: SKILL, env: { UI_VERIFY_CACHE_DIR: '/abs/cache' }, platform: 'linux', homedir: '/fake-home/u', fs }), isCacheRefusal);
});

test('B4 resolveCacheDir: cache z bitami zapisu dla grupy/innych => DepsError kod 2 (070/007 i 077)', POSIX_ONLY, async () => {
  const { resolveCacheDir } = await load();
  for (const mode of [0o40775, 0o40757, 0o40777]) {
    const fs = fsWithCacheStat({ uid: ownUid(), mode });
    assert.throws(() => resolveCacheDir({ skillDir: SKILL, env: { UI_VERIFY_CACHE_DIR: '/abs/cache' }, platform: 'linux', homedir: '/fake-home/u', fs }), isCacheRefusal, mode.toString(8));
  }
});

test('B4 resolveCacheDir: bezpieczny cache (swoj, 0700 i 0755) => zwraca sciezke (kontrola, ze kontrola nie odrzuca wszystkiego)', POSIX_ONLY, async () => {
  const { resolveCacheDir } = await load();
  for (const mode of [0o40700, 0o40755]) {
    const fs = fsWithCacheStat({ uid: ownUid(), mode });
    const got = resolveCacheDir({ skillDir: SKILL, env: { UI_VERIFY_CACHE_DIR: '/abs/cache' }, platform: 'linux', homedir: '/fake-home/u', fs });
    assert.ok(got.startsWith('/abs/cache'), got);
  }
});

test('B4 resolveCacheDir: Windows pomija uid i bity zapisu, ale nadal odrzuca cache wewnatrz skilla', { skip: process.platform === 'win32' ? false : 'needs-windows: sciezki win32 w atrapie wymagaja node:path win32' }, async () => {
  const { resolveCacheDir } = await load();
  const winSkill = 'D:\\h\\skill';
  const files = { [path.win32.join(winSkill, 'package.json')]: PKG, [path.win32.join(winSkill, 'package-lock.json')]: LOCK };
  const wfs = {
    ...fakeFs({}),
    existsSync: () => true,
    statSync: () => ({ uid: 0, mode: 0o40777, isDirectory: () => true }),
    lstatSync: () => ({ uid: 0, mode: 0o40777, isDirectory: () => true, isSymbolicLink: () => false }),
    readFileSync: (p) => { if (p in files) return files[p]; throw Object.assign(new Error('ENOENT'), { code: 'ENOENT' }); },
  };
  assert.doesNotThrow(() => resolveCacheDir({ skillDir: winSkill, env: { UI_VERIFY_CACHE_DIR: 'D:\\h\\cache' }, platform: 'win32', homedir: 'D:\\h', fs: wfs }));
  assert.throws(() => resolveCacheDir({ skillDir: winSkill, env: { UI_VERIFY_CACHE_DIR: 'D:\\h\\skill\\c' }, platform: 'win32', homedir: 'D:\\h', fs: wfs }), isCacheRefusal);
});

test('B4 loadImaging: niebezpieczny cache (obcy wlasciciel albo zapis dla g/o) => DepsError kod 2 i ZADEN kod z cache nie jest ladowany', POSIX_ONLY, async () => {
  const { loadImaging } = await load();
  for (const stat of [{ uid: ownUid() + 1, mode: 0o40700 }, { uid: ownUid(), mode: 0o40777 }]) {
    const calls = { required: 0, imported: 0 };
    const cache = path.resolve('/fake/cache/h');
    const fs = fsWithCacheStat(stat);
    await assert.rejects(loadImaging({
      cacheDir: cache,
      env: {},
      fs,
      requireFactory: () => () => { calls.required++; return {}; },
      importer: async () => { calls.imported++; return {}; },
    }), isCacheRefusal, JSON.stringify(stat));
    assert.deepEqual(calls, { required: 0, imported: 0 });
  }
});
