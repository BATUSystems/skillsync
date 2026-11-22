import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';

const load = () => import('../../library/skills/ui-verify/scripts/lib/deps.mjs');

const CACHE = path.resolve('/fake/cache/abc123');
const OVERRIDE_ROOT = path.resolve('/fake/override/playwright-core');
const OVERRIDE_NM = path.resolve('/fake/override/node_modules');

// Atrapa fs: tylko pliki z mapy; zapisuje kazde odwolanie, by wykryc wyjscie poza dozwolone korzenie.
function fakeFs(files) {
  const map = new Map(Object.entries(files));
  const accessed = [];
  const isDir = (p) => [...map.keys()].some((k) => k.startsWith(p + path.sep));
  return {
    accessed,
    existsSync(p) { accessed.push(p); return map.has(p) || isDir(p); },
    readFileSync(p) {
      accessed.push(p);
      if (!map.has(p)) { const e = new Error('ENOENT ' + p); e.code = 'ENOENT'; throw e; }
      return map.get(p);
    },
  };
}

function pkgFiles(root, pkg) {
  return { [path.join(root, 'package.json')]: JSON.stringify(pkg) };
}

function lock(versions) {
  const packages = {};
  for (const [n, v] of Object.entries(versions)) packages[`node_modules/${n}`] = { version: v };
  return { [path.join(CACHE, 'package-lock.json')]: JSON.stringify({ lockfileVersion: 3, packages }) };
}

function recorders() {
  const calls = { requireFactory: [], required: [], imported: [] };
  return {
    calls,
    requireFactory: (pkgJson) => { calls.requireFactory.push(pkgJson); return (spec) => { calls.required.push(spec); return { via: 'cjs', spec }; }; },
    importer: async (url) => { calls.imported.push(url); return { via: 'esm', url }; },
  };
}

const nm = (name) => path.join(CACHE, 'node_modules', name);

async function mkLoader(files, { env = {}, cacheDir = CACHE } = {}) {
  const { createLoader } = await load();
  const f = fakeFs(files);
  const r = recorders();
  const loader = createLoader({ cacheDir, env, fs: f, requireFactory: r.requireFactory, importer: r.importer });
  return { loader, f, ...r };
}

test('DepsError: kod, exit 3 i hint po polsku', async () => {
  const { DepsError } = await load();
  const e = new DepsError('not-installed', 'brak', 'uruchom node install.mjs');
  assert.ok(e instanceof Error);
  assert.equal(e.code, 'not-installed');
  assert.equal(e.exit, 3);
  assert.ok(e.hint.length > 0);
});

test('loader CJS: requireFactory dostaje package.json cacheDir, require sciezka bezwzgledna wejscia (main)', async () => {
  const { loader, calls } = await mkLoader({ ...pkgFiles(nm('pngjs'), { name: 'pngjs', version: '7.0.0', main: './lib/png.js' }) });
  const m = await loader.load('pngjs');
  assert.equal(m.via, 'cjs');
  assert.deepEqual(calls.requireFactory, [path.join(CACHE, 'package.json')]);
  assert.deepEqual(calls.required, [path.join(nm('pngjs'), 'lib', 'png.js')]);
  assert.equal(calls.imported.length, 0);
});

test('loader CJS: brak main i exports => index.js', async () => {
  const { loader, calls } = await mkLoader({ ...pkgFiles(nm('pixelmatch'), { name: 'pixelmatch', version: '5.3.0' }) });
  await loader.load('pixelmatch');
  assert.deepEqual(calls.required, [path.join(nm('pixelmatch'), 'index.js')]);
});

test('loader CJS: exports jako string', async () => {
  const { loader, calls } = await mkLoader({ ...pkgFiles(nm('pngjs'), { name: 'pngjs', version: '1.0.0', exports: './dist/main.cjs' }) });
  await loader.load('pngjs');
  assert.deepEqual(calls.required, [path.join(nm('pngjs'), 'dist', 'main.cjs')]);
});

test('loader CJS: exports["."] obiekt, preferuje require, potem default', async () => {
  const a = await mkLoader({ ...pkgFiles(nm('pngjs'), { name: 'pngjs', version: '1.0.0', exports: { '.': { import: './esm.mjs', require: './cjs.cjs' } } }) });
  await a.loader.load('pngjs');
  assert.deepEqual(a.calls.required, [path.join(nm('pngjs'), 'cjs.cjs')]);
  const b = await mkLoader({ ...pkgFiles(nm('pngjs'), { name: 'pngjs', version: '1.0.0', exports: { '.': { default: './def.js' } } }) });
  await b.loader.load('pngjs');
  assert.deepEqual(b.calls.required, [path.join(nm('pngjs'), 'def.js')]);
});

test('loader CJS: exports zagniezdzony { require: { default } }', async () => {
  const { loader, calls } = await mkLoader({ ...pkgFiles(nm('pngjs'), { name: 'pngjs', version: '1.0.0', exports: { '.': { require: { default: './nested.cjs' } } } }) });
  await loader.load('pngjs');
  assert.deepEqual(calls.required, [path.join(nm('pngjs'), 'nested.cjs')]);
});

test('loader ESM: importer dostaje adres file:// (pathToFileURL), nie sciezke', async () => {
  const { loader, calls } = await mkLoader({ ...pkgFiles(nm('pixelmatch'), { name: 'pixelmatch', version: '7.0.0', type: 'module', exports: './index.js' }) });
  const m = await loader.load('pixelmatch');
  assert.equal(m.via, 'esm');
  assert.equal(calls.imported.length, 1);
  assert.match(calls.imported[0], /^file:\/\/\//);
  const { pathToFileURL } = await import('node:url');
  assert.equal(calls.imported[0], pathToFileURL(path.join(nm('pixelmatch'), 'index.js')).href);
  assert.equal(calls.required.length, 0);
});

test('loader ESM: exports["."].import oraz main/index.js jako wartosci domyslne', async () => {
  const a = await mkLoader({ ...pkgFiles(nm('pixelmatch'), { name: 'pixelmatch', version: '7.0.0', type: 'module', exports: { '.': { import: { default: './e.mjs' }, require: './c.cjs' } } }) });
  await a.loader.load('pixelmatch');
  assert.ok(a.calls.imported[0].endsWith('/e.mjs'));
  const b = await mkLoader({ ...pkgFiles(nm('pixelmatch'), { name: 'pixelmatch', version: '7.0.0', type: 'module', main: 'm.js' }) });
  await b.loader.load('pixelmatch');
  assert.ok(b.calls.imported[0].endsWith('/m.js'));
  const c = await mkLoader({ ...pkgFiles(nm('pixelmatch'), { name: 'pixelmatch', version: '7.0.0', type: 'module' }) });
  await c.loader.load('pixelmatch');
  assert.ok(c.calls.imported[0].endsWith('/index.js'));
});

test('loader: nazwa spoza listy => TypeError', async () => {
  const { loader } = await mkLoader({});
  await assert.rejects(() => loader.load('lodash'), TypeError);
});

test('loader: brak instalacji => DepsError not-installed z exit 3', async () => {
  const { DepsError } = await load();
  const { loader } = await mkLoader({});
  await assert.rejects(() => loader.load('pngjs'), (e) => e instanceof DepsError && e.code === 'not-installed' && e.exit === 3 && e.hint.length > 0);
  assert.equal(loader.version('pngjs'), null);
});

test('loader: katalog bez package.json => not-installed', async () => {
  const { loader } = await mkLoader({ [path.join(nm('pngjs'), 'index.js')]: '' });
  await assert.rejects(() => loader.load('pngjs'), (e) => e.code === 'not-installed' && e.exit === 3);
});

test('loader: wersja zgodna z lockfile => OK; version() zwraca wersje', async () => {
  const { loader } = await mkLoader({ ...pkgFiles(nm('pngjs'), { name: 'pngjs', version: '7.0.0' }), ...lock({ pngjs: '7.0.0' }) });
  await loader.load('pngjs');
  assert.equal(loader.version('pngjs'), '7.0.0');
});

test('loader: niezgodnosc wersji z lockfile => bad-install (exit 3)', async () => {
  const { loader, calls } = await mkLoader({ ...pkgFiles(nm('pngjs'), { name: 'pngjs', version: '7.0.1' }), ...lock({ pngjs: '7.0.0' }) });
  await assert.rejects(() => loader.load('pngjs'), (e) => e.code === 'bad-install' && e.exit === 3);
  assert.equal(calls.required.length + calls.imported.length, 0, 'nie wolno ladowac paczki o niezgodnej wersji');
});

test('loader: brak lockfile nie blokuje ladowania', async () => {
  const { loader } = await mkLoader({ ...pkgFiles(nm('pngjs'), { name: 'pngjs', version: '7.0.0' }) });
  await loader.load('pngjs');
});

test('loader: wyjatek z paczki => bad-install', async () => {
  const { createLoader } = await load();
  const f = fakeFs(pkgFiles(nm('pngjs'), { name: 'pngjs', version: '1.0.0' }));
  const loader = createLoader({ cacheDir: CACHE, env: {}, fs: f, requireFactory: () => () => { throw new Error('boom'); }, importer: async () => ({}) });
  await assert.rejects(() => loader.load('pngjs'), (e) => e.code === 'bad-install' && e.exit === 3);
});

test('PLAYWRIGHT_MODULE_PATH: korzen paczki playwright-core (wersja nie jest porownywana z lockfile)', async () => {
  const { loader, calls } = await mkLoader({
    ...pkgFiles(OVERRIDE_ROOT, { name: 'playwright-core', version: '9.9.9', main: 'index.js' }),
    ...pkgFiles(nm('playwright-core'), { name: 'playwright-core', version: '1.0.0' }),
    ...lock({ 'playwright-core': '1.0.0' }),
  }, { env: { PLAYWRIGHT_MODULE_PATH: OVERRIDE_ROOT } });
  await loader.load('playwright-core');
  assert.deepEqual(calls.required, [path.join(OVERRIDE_ROOT, 'index.js')]);
});

test('PLAYWRIGHT_MODULE_PATH: katalog node_modules zawierajacy playwright-core', async () => {
  const { loader, calls } = await mkLoader({
    ...pkgFiles(path.join(OVERRIDE_NM, 'playwright-core'), { name: 'playwright-core', version: '2.0.0', main: 'lib/x.js' }),
  }, { env: { PLAYWRIGHT_MODULE_PATH: OVERRIDE_NM } });
  await loader.load('playwright-core');
  assert.deepEqual(calls.required, [path.join(OVERRIDE_NM, 'playwright-core', 'lib', 'x.js')]);
});

test('PLAYWRIGHT_MODULE_PATH: zly katalog albo inna paczka => bad-override (exit 3)', async () => {
  const wrongPkg = path.resolve('/fake/other');
  for (const files of [{}, pkgFiles(wrongPkg, { name: 'inna', version: '1.0.0' }), { [path.join(wrongPkg, 'x.txt')]: '' }]) {
    const { loader } = await mkLoader({ ...files, ...pkgFiles(nm('playwright-core'), { name: 'playwright-core', version: '1.0.0' }) }, { env: { PLAYWRIGHT_MODULE_PATH: wrongPkg } });
    await assert.rejects(() => loader.load('playwright-core'), (e) => e.code === 'bad-override' && e.exit === 3);
  }
});

test('PLAYWRIGHT_MODULE_PATH dotyczy tylko playwright-core', async () => {
  const { loader, calls } = await mkLoader({ ...pkgFiles(nm('pngjs'), { name: 'pngjs', version: '1.0.0' }) }, { env: { PLAYWRIGHT_MODULE_PATH: OVERRIDE_ROOT } });
  await loader.load('pngjs');
  assert.deepEqual(calls.required, [path.join(nm('pngjs'), 'index.js')]);
});

test('playwright-core bez override czytany z cacheDir', async () => {
  const { loader, calls } = await mkLoader({ ...pkgFiles(nm('playwright-core'), { name: 'playwright-core', version: '1.0.0' }) });
  await loader.load('playwright-core');
  assert.deepEqual(calls.required, [path.join(nm('playwright-core'), 'index.js')]);
});

test('loader nie siega do node_modules projektu: wszystkie odwolania fs tylko w cacheDir lub override', async () => {
  const projectNm = path.resolve('/fake/project/node_modules/pngjs');
  const { loader, f } = await mkLoader({
    ...pkgFiles(projectNm, { name: 'pngjs', version: '0.0.1' }),
    ...pkgFiles(nm('pngjs'), { name: 'pngjs', version: '7.0.0' }),
  });
  await loader.load('pngjs');
  loader.version('pngjs');
  const outside = f.accessed.filter((p) => !p.startsWith(CACHE + path.sep) && p !== CACHE && !p.startsWith(OVERRIDE_ROOT));
  assert.deepEqual(outside, []);
  // przy braku instalacji w cache projektowy node_modules nie ratuje
  const miss = await mkLoader({ ...pkgFiles(projectNm, { name: 'pngjs', version: '0.0.1' }) });
  await assert.rejects(() => miss.loader.load('pngjs'), (e) => e.code === 'not-installed');
  assert.deepEqual(miss.f.accessed.filter((p) => p.includes('/fake/project')), []);
});

test('explainMissing: exit 3, krotka instrukcja, bez sciezek spoza cacheDir', async () => {
  const { explainMissing, DepsError } = await load();
  const r = explainMissing(new DepsError('not-installed', 'brak pngjs', 'Uruchom: node scripts/install.mjs'));
  assert.equal(r.exit, 3);
  assert.ok(Array.isArray(r.lines) && r.lines.length > 0);
  const text = r.lines.join('\n');
  assert.match(text, /not-installed/);
  assert.match(text, /install\.mjs/);
  assert.doesNotMatch(text, /\/home\//);
});
