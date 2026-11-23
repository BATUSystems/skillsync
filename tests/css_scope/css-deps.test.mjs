// sass.js jako zaleznosc skilla ui-verify (S4): package.json, lockfile, loader (createLoader) i loadSass (runtime).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SKILL = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'library', 'skills', 'ui-verify');
const readJson = (name) => JSON.parse(fs.readFileSync(path.join(SKILL, name), 'utf8'));
const loadDeps = () => import('../../library/skills/ui-verify/scripts/lib/deps.mjs');
const loadRuntime = () => import('../../library/skills/ui-verify/scripts/lib/runtime.mjs');

// ---- package.json i lockfile ----

test('package.json: sass.js dokladnie "0.11.1" (bez ^ ~ i zakresow), istniejace przypiecia bez zmian', () => {
  const deps = readJson('package.json').dependencies;
  assert.equal(deps['sass.js'], '0.11.1');
  assert.match(deps['sass.js'], /^\d+\.\d+\.\d+$/);
  assert.equal(deps['playwright-core'], '1.65.0');
  assert.equal(deps.pngjs, '7.0.0');
  assert.equal(deps.pixelmatch, '8.0.0');
  for (const [n, v] of Object.entries(deps)) assert.match(v, /^\d+\.\d+\.\d+$/, `${n} przypiety dokladnie`);
});

test('package-lock.json: wpis node_modules/sass.js 0.11.1 z integrity sha512, adres rejestru npm', () => {
  const entry = readJson('package-lock.json').packages['node_modules/sass.js'];
  assert.ok(entry, 'brak wpisu node_modules/sass.js');
  assert.equal(entry.version, '0.11.1');
  assert.match(entry.integrity, /^sha512-[A-Za-z0-9+/]{86}==$/);
  assert.equal(entry.resolved, 'https://registry.npmjs.org/sass.js/-/sass.js-0.11.1.tgz');
  assert.ok(!entry.dependencies || Object.keys(entry.dependencies).length === 0, 'sass.js nie ma zaleznosci');
});

test('package-lock.json: korzen zna sass.js 0.11.1; poprzednie przypiecia i integralnosc nietkniete', () => {
  const lock = readJson('package-lock.json');
  assert.equal(lock.packages[''].dependencies['sass.js'], '0.11.1');
  const want = {
    'playwright-core': ['1.65.0', 'sha512-vNSKgH6YqtN7cdT/L334zo0a+YwkrpicyrHJlWWPIm/Q+Nn2SWInil01oT0mbu96U4GgoRtCYQD9CzJQgju+lA=='],
    pngjs: ['7.0.0', 'sha512-LKWqWJRhstyYo9pGvgor/ivk2w94eSjE3RGVuzLGlr3NmD8bf7RcYGze1mNdEHRP6TRP6rMuDHk5t44hnTRyow=='],
    pixelmatch: ['8.0.0', 'sha512-cIqOLfh5k52us715qf3B3+kTOxZv+WDv77HN7xokLScYQGYQHi4m/ynoDIPpvF2LvCyUJGyvzY64i0QCXtzu1g=='],
  };
  for (const [n, [v, integ]] of Object.entries(want)) {
    assert.equal(lock.packages[`node_modules/${n}`].version, v, n);
    assert.equal(lock.packages[`node_modules/${n}`].integrity, integ, n);
    assert.equal(lock.packages[''].dependencies[n], v, n);
  }
});

test('package-lock.json: zgodnosc z package.json (kazda zaleznosc z package.json ma wpis o tej samej wersji)', () => {
  const deps = readJson('package.json').dependencies;
  const lock = readJson('package-lock.json');
  for (const [n, v] of Object.entries(deps)) {
    assert.equal(lock.packages[`node_modules/${n}`]?.version, v, n);
    assert.equal(lock.packages[''].dependencies[n], v, n);
  }
});

test('install-core: weryfikacja instalacji (DEP_NAMES) obejmuje sass.js', () => {
  const src = fs.readFileSync(path.join(SKILL, 'scripts', 'lib', 'install-core.mjs'), 'utf8');
  const m = src.match(/const DEP_NAMES = \[([^\]]*)\]/);
  assert.ok(m, 'brak DEP_NAMES');
  assert.match(m[1], /'sass\.js'/);
});

// ---- createLoader z atrapami ----

const CACHE = path.resolve('/h/cache/abc123');
const nm = (name) => path.join(CACHE, 'node_modules', name);

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
const sassPkg = (over = {}) => ({ name: 'sass.js', version: '0.11.1', main: './dist/sass.sync.js', ...over });
const sassFiles = (pkg = sassPkg(), lockVersion = '0.11.1') => ({
  [path.join(nm('sass.js'), 'package.json')]: JSON.stringify(pkg),
  [path.join(CACHE, 'package-lock.json')]: JSON.stringify({ lockfileVersion: 3, packages: { 'node_modules/sass.js': { version: lockVersion } } }),
});
function recorders(moduleValue = { marker: 'sass-module' }) {
  const calls = { requireFactory: [], required: [], imported: [] };
  return {
    calls,
    requireFactory: (p) => { calls.requireFactory.push(p); return (spec) => { calls.required.push(spec); return moduleValue; }; },
    importer: async (u) => { calls.imported.push(u); return { via: 'esm' }; },
  };
}
async function mkLoader(files, env = {}) {
  const { createLoader } = await loadDeps();
  const f = fakeFs(files);
  const r = recorders();
  return { loader: createLoader({ cacheDir: CACHE, env, fs: f, requireFactory: r.requireFactory, importer: r.importer }), f, ...r };
}

test('createLoader: load("sass.js") laduje CJS z pola main przez requireFactory(package.json cache)', async () => {
  const { loader, calls } = await mkLoader(sassFiles());
  const m = await loader.load('sass.js');
  assert.equal(m.marker, 'sass-module');
  assert.deepEqual(calls.requireFactory, [path.join(CACHE, 'package.json')]);
  assert.deepEqual(calls.required, [path.join(nm('sass.js'), 'dist', 'sass.sync.js')]);
  assert.deepEqual(calls.imported, []);
});

test('createLoader: sass.js bez main laduje index.js; main bez ./ rowniez rozwiazywany wzgledem korzenia paczki', async () => {
  const noMain = await mkLoader(sassFiles(sassPkg({ main: undefined })));
  await noMain.loader.load('sass.js');
  assert.deepEqual(noMain.calls.required, [path.join(nm('sass.js'), 'index.js')]);
  const bare = await mkLoader(sassFiles(sassPkg({ main: 'dist/sass.sync.js' })));
  await bare.loader.load('sass.js');
  assert.deepEqual(bare.calls.required, [path.join(nm('sass.js'), 'dist', 'sass.sync.js')]);
});

test('createLoader: wersja sass.js niezgodna z lockfile => DepsError bad-install exit 3, nic nie ladowane', async () => {
  const { DepsError } = await loadDeps();
  const { loader, calls } = await mkLoader(sassFiles(sassPkg({ version: '0.11.0' }), '0.11.1'));
  await assert.rejects(loader.load('sass.js'), (e) => e instanceof DepsError && e.code === 'bad-install' && e.exit === 3);
  assert.deepEqual(calls.required, []);
});

test('createLoader: brak zainstalowanego sass.js => DepsError not-installed exit 3 z podpowiedzia instalacji', async () => {
  const { DepsError } = await loadDeps();
  const { loader } = await mkLoader({});
  await assert.rejects(loader.load('sass.js'), (e) => e instanceof DepsError && e.code === 'not-installed' && e.exit === 3 && /install/.test(e.hint));
});

test('createLoader: wyjatek przy require paczki => DepsError bad-install (nie surowy wyjatek)', async () => {
  const { createLoader, DepsError } = await loadDeps();
  const loader = createLoader({
    cacheDir: CACHE,
    env: {},
    fs: fakeFs(sassFiles()),
    requireFactory: () => () => { throw new SyntaxError('broken bundle'); },
    importer: async () => ({}),
  });
  await assert.rejects(loader.load('sass.js'), (e) => e instanceof DepsError && e.code === 'bad-install');
});

test('createLoader: sass.js zawsze z cache - zmienne srodowiska (np. PLAYWRIGHT_MODULE_PATH, SASS_MODULE_PATH) nie podmieniaja korzenia', async () => {
  const other = path.resolve('/h/elsewhere');
  const files = { ...sassFiles(), [path.join(other, 'package.json')]: JSON.stringify(sassPkg({ name: 'sass.js' })) };
  const { loader, f, calls } = await mkLoader(files, { PLAYWRIGHT_MODULE_PATH: other, SASS_MODULE_PATH: other, SASS_JS_PATH: other });
  await loader.load('sass.js');
  assert.deepEqual(calls.required, [path.join(nm('sass.js'), 'dist', 'sass.sync.js')]);
  assert.ok(f.accessed.every((p) => !p.startsWith(other)), 'zadne odwolanie poza cache');
});

test('createLoader: version("sass.js") zwraca wersje z cache; brak => null; nieznana nazwa nadal TypeError', async () => {
  const { loader } = await mkLoader(sassFiles());
  assert.equal(loader.version('sass.js'), '0.11.1');
  const missing = await mkLoader({});
  assert.equal(missing.loader.version('sass.js'), null);
  await assert.rejects(loader.load('sass'), TypeError);
  await assert.rejects(loader.load('sass.js/dist/x'), TypeError);
});

test('createLoader: dotychczasowe zaleznosci nadal dzialaja obok sass.js', async () => {
  const files = { ...sassFiles(), [path.join(nm('pngjs'), 'package.json')]: JSON.stringify({ name: 'pngjs', version: '7.0.0', main: './lib/png.js' }) };
  const { loader, calls } = await mkLoader(files);
  await loader.load('pngjs');
  await loader.load('sass.js');
  assert.equal(calls.required.length, 2);
});

// ---- loadSass ----

const POSIX_ONLY = { skip: process.platform === 'win32' ? 'needs-posix: bity uid/mode nie dotycza Windows' : false };
const ownUid = () => (typeof process.getuid === 'function' ? process.getuid() : 0);

function runtimeFs(files, stat = {}) {
  const base = fakeFs(files);
  const mk = () => ({ uid: ownUid(), mode: 0o40755, isDirectory: () => true, isSymbolicLink: () => false, ...stat });
  return { ...base, statSync: mk, lstatSync: mk };
}

test('loadSass: eksport runtime i sukces => zwraca modul sass.js z cache (Promise<Sass>)', async () => {
  const { loadSass } = await loadRuntime();
  assert.equal(typeof loadSass, 'function');
  const Sass = { compileFile() {}, writeFile() {}, style: { expanded: 1 } };
  const calls = { required: [], factory: [] };
  const p = loadSass({
    cacheDir: CACHE,
    env: {},
    fs: runtimeFs(sassFiles()),
    requireFactory: (pj) => { calls.factory.push(pj); return (spec) => { calls.required.push(spec); return Sass; }; },
    importer: async () => { throw new Error('sass.js to CJS - importer nie powinien byc uzyty'); },
  });
  assert.ok(p instanceof Promise);
  assert.equal(await p, Sass);
  assert.deepEqual(calls.required, [path.join(nm('sass.js'), 'dist', 'sass.sync.js')]);
});

test('loadSass: brak pakietu w cache => DepsError exit 3', async () => {
  const { loadSass } = await loadRuntime();
  const { DepsError } = await loadDeps();
  await assert.rejects(
    loadSass({ cacheDir: CACHE, env: {}, fs: runtimeFs({}), requireFactory: () => () => ({}), importer: async () => ({}) }),
    (e) => e instanceof DepsError && e.exit === 3,
  );
});

test('loadSass: niezgodnosc z lockfile => DepsError (exit 3), modul nie jest zwracany', async () => {
  const { loadSass } = await loadRuntime();
  const { DepsError } = await loadDeps();
  await assert.rejects(
    loadSass({ cacheDir: CACHE, env: {}, fs: runtimeFs(sassFiles(sassPkg({ version: '0.10.0' }))), requireFactory: () => () => ({}), importer: async () => ({}) }),
    (e) => e instanceof DepsError && e.code === 'bad-install' && e.exit === 3,
  );
});

test('loadSass: niebezpieczny cache (obcy wlasciciel albo zapis dla g/o) => DepsError exit 2 i ZADEN kod z cache nie jest ladowany', POSIX_ONLY, async () => {
  const { loadSass } = await loadRuntime();
  const { DepsError } = await loadDeps();
  for (const stat of [{ uid: ownUid() + 1, mode: 0o40700 }, { uid: ownUid(), mode: 0o40777 }]) {
    const calls = { required: 0, imported: 0, factory: 0 };
    await assert.rejects(
      loadSass({
        cacheDir: CACHE,
        env: {},
        fs: runtimeFs(sassFiles(), stat),
        requireFactory: () => { calls.factory++; return () => { calls.required++; return {}; }; },
        importer: async () => { calls.imported++; return {}; },
      }),
      (e) => e instanceof DepsError && e.exit === 2,
      JSON.stringify(stat),
    );
    assert.deepEqual(calls, { required: 0, imported: 0, factory: 0 });
  }
});

// ---- zakaz golych specyfikatorow ----

const BARE_SASS = /(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|^\s*import\s*)(['"])sass\.js(?:\/[^'"]*)?\1/m;

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    return e.isDirectory() ? walk(p) : e.name.endsWith('.mjs') ? [p] : [];
  });
}

test('zakaz golych specyfikatorow sass.js w scripts/**/*.mjs (w tym nowy css-compile.mjs)', () => {
  const scripts = path.join(SKILL, 'scripts');
  assert.ok(fs.existsSync(path.join(scripts, 'lib', 'css-compile.mjs')), 'brak scripts/lib/css-compile.mjs');
  const files = walk(scripts);
  assert.ok(files.length > 5);
  for (const f of files) {
    assert.doesNotMatch(fs.readFileSync(f, 'utf8'), BARE_SASS, `goly specyfikator sass.js w ${path.relative(scripts, f)}`);
  }
});

test('zakaz golych specyfikatorow sass.js: wzorzec wykrywa podlozone naruszenia i nie lapie nazwy w tekscie', () => {
  for (const bad of ["import Sass from 'sass.js';", "const S = require('sass.js');", 'await import("sass.js")', "import 'sass.js';", "import S from 'sass.js/dist/sass.sync.js';"]) {
    assert.match(bad, BARE_SASS, bad);
  }
  assert.doesNotMatch("const NAMES = new Set(['sass.js']); // tylko nazwa", BARE_SASS);
});
