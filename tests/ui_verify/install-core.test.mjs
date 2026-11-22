// Czysta logika instalacji zaleznosci (lib/install-core.mjs, S6) na atrapach fs/spawn/clock/process.
// Prawdziwy system plikow w katalogu tymczasowym + wstrzykiwane: spawn (udaje `npm ci`), zegar, proces, wrapper fs (wstrzykiwanie bledow).
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';

const load = () => import('../../library/skills/ui-verify/scripts/lib/install-core.mjs');
const loadPaths = () => import('../../library/skills/ui-verify/scripts/lib/paths.mjs');
const loadOut = () => import('../../library/skills/ui-verify/scripts/lib/out.mjs');

const WIN = process.platform === 'win32';
const POSIX_ONLY = { skip: WIN ? 'needs-posix: bity uid/mode nie dotycza Windows' : false };

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-install-core-'));
after(() => fs.rmSync(tmpRoot, { recursive: true, force: true }));
let counter = 0;
const newDir = (label = 'd') => {
  const d = path.join(tmpRoot, `${label}-${counter++}`);
  fs.mkdirSync(d, { recursive: true });
  return d;
};

// --- fixture skilla: package.json + package-lock.json z trzema zaleznosciami ---

const VERSIONS = { 'playwright-core': '1.0.1', pngjs: '2.0.0', pixelmatch: '3.0.0' };
const PKG_TEXT = JSON.stringify({ name: 'fixture-deps', version: '1.0.0', private: true, dependencies: VERSIONS }, null, 2) + '\n';
const LOCK_TEXT = JSON.stringify({
  name: 'fixture-deps',
  lockfileVersion: 3,
  packages: {
    '': { name: 'fixture-deps', dependencies: VERSIONS },
    ...Object.fromEntries(Object.entries(VERSIONS).map(([n, v]) => [`node_modules/${n}`, { version: v }])),
  },
}, null, 2) + '\n';

async function hashOf() {
  const { computeHash } = await loadPaths();
  return computeHash([PKG_TEXT, LOCK_TEXT]);
}

// Minimalne paczki zgodne z lockfile w <dir>/node_modules (to robi atrapa `npm ci`).
function populateModules(dir, versions = VERSIONS) {
  for (const [name, version] of Object.entries(versions)) {
    const root = path.join(dir, 'node_modules', name);
    fs.mkdirSync(root, { recursive: true });
    fs.writeFileSync(path.join(root, 'package.json'), JSON.stringify({ name, version, main: 'index.js' }));
    fs.writeFileSync(path.join(root, 'index.js'), 'module.exports = {};\n');
  }
}

// --- atrapy ---

// Atrapa spawn: zapisuje wywolania; "npm ci" = utworzenie node_modules w opts.cwd albo blad.
function makeSpawn({ code = 0, stdout = '', stderr = '', error = null, populate = true, partial = false, onRun = null } = {}) {
  const calls = [];
  const spawn = (cmd, args, opts) => {
    calls.push({ cmd, args: [...args], opts });
    const child = new EventEmitter();
    for (const k of ['stdout', 'stderr']) {
      child[k] = new EventEmitter();
      child[k].setEncoding = () => {};
    }
    child.kill = () => true;
    setImmediate(() => {
      try {
        if (onRun) onRun({ cmd, args, opts });
        if (!error && code === 0 && populate) populateModules(opts.cwd);
        if (partial) populateModules(opts.cwd, { pngjs: VERSIONS.pngjs });
      } catch (e) {
        child.emit('error', e);
        return;
      }
      if (stdout) child.stdout.emit('data', Buffer.from(stdout));
      if (stderr) child.stderr.emit('data', Buffer.from(stderr));
      if (error) {
        child.emit('error', error);
        return;
      }
      child.emit('exit', code, null);
      child.emit('close', code, null);
    });
    return child;
  };
  return { spawn, calls };
}

function fakeClock(start = 1_700_000_000_000) {
  const c = {
    t: start,
    start,
    sleeps: [],
    onSleep: null,
    now() { return c.t; },
    async sleep(ms) {
      c.t += ms;
      c.sleeps.push(ms);
      if (c.onSleep) c.onSleep(c.sleeps.length);
    },
  };
  return c;
}

// proces: pid atrapy; kill(pid, 0) rzuca ESRCH dla pid spoza `alive`; EPERM = istnieje.
function fakeProc({ alive = [], pid = 4321 } = {}) {
  const set = new Set([pid, ...alive]);
  return {
    pid,
    getuid: process.getuid ? () => process.getuid() : undefined,
    kill(p) {
      if (set.has(p)) return true;
      const e = new Error('kill ESRCH');
      e.code = 'ESRCH';
      throw e;
    },
    alive: set,
  };
}

// wrapper fs: rejestruje wywolania synchroniczne, pozwala nadpisac wybrane.
function wrapFs(over = {}) {
  const log = [];
  const rec = (name, fn) => (...a) => { log.push([name, ...a]); return (over[name] || fn)(...a); };
  const w = { ...fs, log };
  for (const name of ['openSync', 'renameSync', 'mkdirSync', 'rmSync', 'writeFileSync', 'copyFileSync', 'cpSync', 'unlinkSync', 'rmdirSync']) {
    w[name] = rec(name, fs[name].bind(fs));
  }
  for (const name of ['statSync', 'lstatSync']) {
    if (over[name]) w[name] = over[name];
  }
  return w;
}

function fakeStats(real, patch) {
  return Object.assign(Object.create(Object.getPrototypeOf(real)), real, patch);
}

const SECRET = 's3cr3t-token-value';

async function makeSession(opts = {}) {
  const { createMasker } = await loadOut();
  const base = newDir('s');
  const skillDir = path.join(base, 'skill');
  fs.mkdirSync(skillDir);
  fs.writeFileSync(path.join(skillDir, 'package.json'), PKG_TEXT);
  fs.writeFileSync(path.join(skillDir, 'package-lock.json'), LOCK_TEXT);
  const npmDir = path.join(base, 'npm', 'bin');
  fs.mkdirSync(npmDir, { recursive: true });
  const npmCli = path.join(npmDir, 'npm-cli.js');
  fs.writeFileSync(npmCli, '// atrapa\n');
  const cacheRoot = path.join(base, 'cache');
  if (opts.rootExists !== false) fs.mkdirSync(cacheRoot, { mode: 0o700 });
  if (opts.rootExists !== false && !WIN) fs.chmodSync(cacheRoot, 0o700);
  const hash = await hashOf();
  const s = {
    base, skillDir, cacheRoot, npmCli, hash,
    hashDir: path.join(cacheRoot, hash),
    lockPath: path.join(cacheRoot, `${hash}.lock`),
    tmpDir: path.join(cacheRoot, `${hash}.tmp-4321`),
    clock: fakeClock(),
    proc: fakeProc(opts.proc),
    sp: makeSpawn(opts.spawn),
    fs: opts.fs || wrapFs(),
    env: { UI_VERIFY_CACHE_DIR: cacheRoot, npm_execpath: npmCli, ...opts.env },
    masker: createMasker({ secrets: [SECRET] }),
  };
  s.run = async (extra = {}) => {
    const { installDeps } = await load();
    return installDeps({
      skillDir, env: s.env, platform: process.platform, homedir: base, execPath: '/m/node/bin/node',
      fs: s.fs, spawn: s.sp.spawn, clock: s.clock, proc: s.proc, masker: s.masker, ...extra,
    });
  };
  s.leftovers = () => (fs.existsSync(cacheRoot) ? fs.readdirSync(cacheRoot) : []);
  s.writeLock = ({ pid = 777, ageMs = 0 } = {}) => {
    const t = s.clock.now() - ageMs;
    fs.writeFileSync(s.lockPath, JSON.stringify({ pid, time: t, createdAt: t }));
    fs.utimesSync(s.lockPath, t / 1000, t / 1000);
  };
  return s;
}

// --- instalacja, znacznik, idempotencja ---

test('swieza instalacja: kod 0, <hash> z package.json, lockfile, node_modules i znacznikiem {hash, installedAt}; brak tmp i blokady', async () => {
  const s = await makeSession();
  const r = await s.run();
  assert.equal(r.exit, 0, r.message);
  assert.equal(r.status, 'installed');
  for (const f of ['package.json', 'package-lock.json']) assert.ok(fs.existsSync(path.join(s.hashDir, f)), f);
  assert.ok(fs.existsSync(path.join(s.hashDir, 'node_modules', 'pngjs', 'package.json')));
  const marker = JSON.parse(fs.readFileSync(path.join(s.hashDir, '.ui-verify-ready'), 'utf8'));
  assert.equal(marker.hash, s.hash);
  assert.ok(marker.installedAt, 'installedAt');
  assert.deepEqual(s.leftovers(), [s.hash]);
});

test('npm ci: spawn(execPath, [npmCli, ci, --ignore-scripts, --omit=dev, --no-audit, --no-fund]) w <hash>.tmp-<pid> z lockfile obok', async () => {
  const s = await makeSession();
  await s.run();
  assert.equal(s.sp.calls.length, 1);
  const { cmd, args, opts } = s.sp.calls[0];
  assert.equal(cmd, '/m/node/bin/node');
  assert.equal(args[0], s.npmCli);
  assert.equal(args[1], 'ci');
  for (const f of ['--ignore-scripts', '--omit=dev', '--no-audit', '--no-fund']) assert.ok(args.includes(f), f);
  assert.equal(opts.cwd, s.tmpDir);
  assert.equal(opts.windowsHide, true);
});

test('spawn bez powloki: shell === false, brak golego npm, .cmd i .bat w komendzie i argumentach', async () => {
  const s = await makeSession();
  await s.run();
  const { cmd, args, opts } = s.sp.calls[0];
  assert.strictEqual(opts.shell, false);
  for (const x of [cmd, ...args]) {
    assert.notEqual(x, 'npm');
    assert.doesNotMatch(String(x), /\.(cmd|bat)$/i);
    assert.doesNotMatch(String(x), /(^|[\\/])npm(\.cmd)?$/i);
  }
});

test('srodowisko dziecka: PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1, npm_config_ignore_scripts=true, proxy i PATH zachowane', async () => {
  const s = await makeSession({
    env: {
      HTTPS_PROXY: 'http://proxy.test:3128', HTTP_PROXY: 'http://proxy.test:3128', NO_PROXY: 'localhost', PATH: '/m/bin',
      npm_config_ignore_scripts: 'false',
    },
  });
  await s.run();
  const env = s.sp.calls[0].opts.env;
  assert.equal(env.PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD, '1');
  assert.equal(env.npm_config_ignore_scripts, 'true');
  assert.equal(env.HTTPS_PROXY, 'http://proxy.test:3128');
  assert.equal(env.HTTP_PROXY, 'http://proxy.test:3128');
  assert.equal(env.NO_PROXY, 'localhost');
  assert.equal(env.PATH, '/m/bin');
});

test('znacznik gotowosci zapisany OSTATNI: przy rename tmp zawiera znacznik, a przy npm go nie ma; rename dopiero po znaczniku', async () => {
  const order = [];
  const w = wrapFs({
    writeFileSync: (p, ...a) => {
      if (String(p).endsWith('.ui-verify-ready')) order.push('marker');
      return fs.writeFileSync(p, ...a);
    },
    renameSync: (from, to) => {
      order.push('rename');
      assert.ok(fs.existsSync(path.join(from, '.ui-verify-ready')), 'znacznik musi istniec w tmp przy rename');
      return fs.renameSync(from, to);
    },
  });
  const s = await makeSession({ fs: w, spawn: { onRun: ({ opts }) => { order.push('npm'); assert.equal(fs.existsSync(path.join(opts.cwd, '.ui-verify-ready')), false); } } });
  const r = await s.run();
  assert.equal(r.exit, 0, r.message);
  assert.deepEqual(order, ['npm', 'marker', 'rename']);
});

test('idempotencja: druga instalacja => kod 0, "already installed", bez spawn', async () => {
  const s = await makeSession();
  assert.equal((await s.run()).exit, 0);
  const r = await s.run();
  assert.equal(r.exit, 0, r.message);
  assert.equal(r.status, 'already-installed');
  assert.match(r.message, /already installed/i);
  assert.equal(s.sp.calls.length, 1, 'drugi przebieg nie moze wolac npm');
});

test('uszkodzony katalog: brak znacznika => usuniety i zainstalowany ponownie (smieci znikaja)', async () => {
  const s = await makeSession();
  fs.mkdirSync(s.hashDir);
  fs.writeFileSync(path.join(s.hashDir, 'junk.txt'), 'x');
  const r = await s.run();
  assert.equal(r.exit, 0, r.message);
  assert.equal(r.status, 'installed');
  assert.equal(s.sp.calls.length, 1);
  assert.equal(fs.existsSync(path.join(s.hashDir, 'junk.txt')), false);
  assert.ok(fs.existsSync(path.join(s.hashDir, '.ui-verify-ready')));
});

test('uszkodzony katalog: znacznik jest, ale brak node_modules (weryfikacja nie przechodzi) => reinstalacja', async () => {
  const s = await makeSession();
  fs.mkdirSync(s.hashDir);
  fs.writeFileSync(path.join(s.hashDir, '.ui-verify-ready'), JSON.stringify({ hash: s.hash, installedAt: 'x' }));
  fs.writeFileSync(path.join(s.hashDir, 'package-lock.json'), LOCK_TEXT);
  const r = await s.run();
  assert.equal(r.exit, 0, r.message);
  assert.equal(r.status, 'installed');
  assert.equal(s.sp.calls.length, 1);
  assert.ok(fs.existsSync(path.join(s.hashDir, 'node_modules', 'pngjs', 'package.json')));
});

test('uszkodzony katalog: wersja modulu niezgodna z lockfile => reinstalacja', async () => {
  const s = await makeSession();
  assert.equal((await s.run()).exit, 0);
  const pj = path.join(s.hashDir, 'node_modules', 'pngjs', 'package.json');
  fs.writeFileSync(pj, JSON.stringify({ name: 'pngjs', version: '9.9.9', main: 'index.js' }));
  const r = await s.run();
  assert.equal(r.exit, 0, r.message);
  assert.equal(r.status, 'installed');
  assert.equal(s.sp.calls.length, 2);
  assert.equal(JSON.parse(fs.readFileSync(pj, 'utf8')).version, VERSIONS.pngjs);
});

test('pozostalosc <hash>.tmp-<pid> z poprzedniego przerwanego przebiegu nie przeszkadza', async () => {
  const s = await makeSession();
  fs.mkdirSync(path.join(s.tmpDir, 'node_modules', 'zombie'), { recursive: true });
  const r = await s.run();
  assert.equal(r.exit, 0, r.message);
  assert.deepEqual(s.leftovers(), [s.hash]);
  assert.equal(fs.existsSync(path.join(s.hashDir, 'node_modules', 'zombie')), false);
});

// --- atomowosc ---

test('npm konczy sie kodem != 0 (np. brak sieci) => kod 3, brak <hash> i tmp, blokada zwolniona', async () => {
  const s = await makeSession({ spawn: { code: 1, stderr: 'npm ERR! network request failed', partial: true } });
  const r = await s.run();
  assert.equal(r.exit, 3, r.message);
  assert.deepEqual(s.leftovers(), []);
  assert.equal(fs.existsSync(s.hashDir), false);
});

test('blad uruchomienia npm (ENOENT na spawn) => kod 3, sprzatanie', async () => {
  const err = Object.assign(new Error('spawn ENOENT'), { code: 'ENOENT' });
  const s = await makeSession({ spawn: { error: err } });
  const r = await s.run();
  assert.equal(r.exit, 3, r.message);
  assert.deepEqual(s.leftovers(), []);
});

test('npm zwraca 0, ale moduly sie nie laduja (weryfikacja) => kod 2 albo 3 i brak <hash> oraz tmp', async () => {
  const s = await makeSession({ spawn: { populate: false } });
  const r = await s.run();
  assert.ok(r.exit === 2 || r.exit === 3, `exit=${r.exit}`);
  assert.deepEqual(s.leftovers(), []);
});

test('blad zapisu znacznika w srodku instalacji => kod 2, nie zostaje <hash> ani tmp', async () => {
  const w = wrapFs({
    writeFileSync: (p, ...a) => {
      if (String(p).endsWith('.ui-verify-ready')) throw Object.assign(new Error('disk full'), { code: 'ENOSPC' });
      return fs.writeFileSync(p, ...a);
    },
  });
  const s = await makeSession({ fs: w });
  const r = await s.run();
  assert.equal(r.exit, 2, r.message);
  assert.deepEqual(s.leftovers(), []);
});

test('blad rename (inny niz wyscig) => kod 2, tmp sprzatniete, brak <hash>', async () => {
  const w = wrapFs({ renameSync: () => { throw Object.assign(new Error('boom'), { code: 'EIO' }); } });
  const s = await makeSession({ fs: w });
  const r = await s.run();
  assert.equal(r.exit, 2, r.message);
  assert.deepEqual(s.leftovers(), []);
});

for (const code of ['ENOTEMPTY', 'EEXIST']) {
  test(`wyscig na rename (${code}): cel zdazyl powstac poprawny => kod 0, tmp sprzatniete, cudzy katalog nietkniety`, async () => {
    const donor = await makeSession();
    assert.equal((await donor.run()).exit, 0);
    let raced = false;
    const s = await makeSession({
      fs: wrapFs({
        renameSync: (from, to) => {
          if (!raced && to === s.hashDir) {
            raced = true;
            fs.cpSync(donor.hashDir, s.hashDir, { recursive: true });
            fs.writeFileSync(path.join(s.hashDir, 'sentinel'), 'inna-sesja');
            throw Object.assign(new Error('target exists'), { code });
          }
          return fs.renameSync(from, to);
        },
      }),
    });
    const r = await s.run();
    assert.equal(raced, true, 'wyscig musi zostac sprowokowany');
    assert.equal(r.exit, 0, r.message);
    assert.deepEqual(s.leftovers(), [s.hash]);
    assert.equal(fs.readFileSync(path.join(s.hashDir, 'sentinel'), 'utf8'), 'inna-sesja');
  });
}

// --- blokada ---

test('blokada tworzona atomowo przez openSync(<hash>.lock, "wx") z pid i czasem; trzymana w trakcie npm, zwolniona po', async () => {
  let held = null;
  const s = await makeSession({
    spawn: { onRun: () => { held = JSON.parse(fs.readFileSync(path.join(path.dirname(s.hashDir), `${s.hash}.lock`), 'utf8')); } },
  });
  const r = await s.run();
  assert.equal(r.exit, 0, r.message);
  const opens = s.fs.log.filter(([n, p]) => n === 'openSync' && p === s.lockPath);
  assert.equal(opens.length >= 1, true, 'brak openSync na pliku blokady');
  assert.equal(opens[0][2], 'wx');
  assert.equal(held.pid, s.proc.pid);
  assert.ok(held.time || held.createdAt, 'czas w blokadzie');
  assert.equal(fs.existsSync(s.lockPath), false);
});

test('blokada nie jest tworzona przez writeFileSync (nieatomowe exists+write)', async () => {
  const s = await makeSession();
  await s.run();
  assert.equal(s.fs.log.some(([n, p]) => n === 'writeFileSync' && p === s.lockPath), false);
});

test('blokada zwolniona w finally takze po bledzie instalacji', async () => {
  const s = await makeSession({ spawn: { code: 1 } });
  const r = await s.run();
  assert.equal(r.exit, 3);
  assert.equal(fs.existsSync(s.lockPath), false);
});

test('blokada zwolniona takze gdy wyjatek wypada z weryfikacji/zapisu (kod 2)', async () => {
  const s = await makeSession({
    fs: wrapFs({ writeFileSync: (p, ...a) => { if (String(p).endsWith('.ui-verify-ready')) throw new Error('boom'); return fs.writeFileSync(p, ...a); } }),
  });
  assert.equal((await s.run()).exit, 2);
  assert.equal(fs.existsSync(s.lockPath), false);
});

test('druga sesja czeka z odpytywaniem, az blokada zniknie, potem instaluje', async () => {
  const s = await makeSession({ proc: { alive: [777] } });
  s.writeLock({ pid: 777, ageMs: 0 });
  s.clock.onSleep = (n) => { if (n === 3) fs.rmSync(s.lockPath); };
  const r = await s.run();
  assert.equal(r.exit, 0, r.message);
  assert.ok(s.clock.sleeps.length >= 3, `czekanie: ${s.clock.sleeps.length}`);
  assert.equal(s.sp.calls.length, 1);
  assert.equal(fs.existsSync(s.lockPath), false);
});

test('druga sesja po zwolnieniu blokady widzi gotowa instalacje i jej nie powtarza', async () => {
  const donor = await makeSession();
  assert.equal((await donor.run()).exit, 0);
  const s = await makeSession({ proc: { alive: [777] } });
  s.writeLock({ pid: 777, ageMs: 0 });
  s.clock.onSleep = () => {
    if (fs.existsSync(s.lockPath)) {
      fs.cpSync(donor.hashDir, s.hashDir, { recursive: true });
      fs.rmSync(s.lockPath);
    }
  };
  const r = await s.run();
  assert.equal(r.exit, 0, r.message);
  assert.equal(r.status, 'already-installed');
  assert.equal(s.sp.calls.length, 0);
});

test('nieaktualna blokada: proces nie istnieje (ESRCH) => przejeta bez czekania do limitu', async () => {
  const s = await makeSession();
  s.writeLock({ pid: 777, ageMs: 1000 });
  const r = await s.run();
  assert.equal(r.exit, 0, r.message);
  assert.equal(s.sp.calls.length, 1);
  assert.equal(fs.existsSync(s.lockPath), false);
  assert.ok(s.clock.now() - s.clock.start < 60_000, 'nie wolno czekac do limitu na martwa blokade');
});

test('nieaktualna blokada: proces zyje, ale plik starszy niz 10 min => przejeta', async () => {
  const s = await makeSession({ proc: { alive: [777] } });
  s.writeLock({ pid: 777, ageMs: 11 * 60 * 1000 });
  const r = await s.run();
  assert.equal(r.exit, 0, r.message);
  assert.equal(s.sp.calls.length, 1);
});

test('swieza blokada zywego procesu NIE jest przejmowana (EPERM z kill oznacza, ze proces istnieje)', async () => {
  const s = await makeSession();
  s.proc.kill = (p) => {
    if (p === s.proc.pid) return true;
    throw Object.assign(new Error('kill EPERM'), { code: 'EPERM' });
  };
  s.writeLock({ pid: 777, ageMs: 9 * 60 * 1000 });
  const r = await s.run({ lockTimeoutMs: 2000 });
  assert.equal(r.exit, 3, r.message);
  assert.equal(s.sp.calls.length, 0);
});

test('limit czasu blokady (lockTimeoutMs) => kod 3 z komunikatem; cudza blokada nietknieta, npm nie uruchomiony', async () => {
  const s = await makeSession({ proc: { alive: [777] } });
  s.writeLock({ pid: 777, ageMs: 0 });
  const r = await s.run({ lockTimeoutMs: 3000 });
  assert.equal(r.exit, 3, r.message);
  assert.match(r.message, /lock|blokad|timeout/i);
  assert.equal(s.sp.calls.length, 0);
  assert.equal(JSON.parse(fs.readFileSync(s.lockPath, 'utf8')).pid, 777);
  assert.ok(s.clock.now() - s.clock.start >= 3000, 'zegar atrapy musi dojsc do limitu');
  assert.deepEqual(s.leftovers().filter((n) => n.includes('.tmp-')), []);
});

test('domyslny limit blokady to 120 s', async () => {
  const s = await makeSession({ proc: { alive: [777] } });
  s.writeLock({ pid: 777, ageMs: 0 });
  const r = await s.run();
  assert.equal(r.exit, 3, r.message);
  const waited = s.clock.now() - s.clock.start;
  assert.ok(waited >= 120_000 && waited < 150_000, `waited=${waited}`);
});

// --- findNpmCli ---

function pathFs(existing) {
  const set = new Set(existing);
  const enoent = (p) => Object.assign(new Error(`ENOENT ${p}`), { code: 'ENOENT' });
  return {
    existsSync: (p) => set.has(p),
    statSync: (p) => { if (!set.has(p)) throw enoent(p); return { isFile: () => true, isDirectory: () => false }; },
    accessSync: (p) => { if (!set.has(p)) throw enoent(p); },
    readFileSync: (p) => { if (!set.has(p)) throw enoent(p); return ''; },
  };
}

test('findNpmCli Linux: <execDir>/../lib/node_modules/npm/bin/npm-cli.js', async () => {
  const { findNpmCli } = await load();
  const want = '/m/node-v22/lib/node_modules/npm/bin/npm-cli.js';
  const got = findNpmCli({ env: {}, execPath: '/m/node-v22/bin/node', platform: 'linux', fs: pathFs([want]) });
  assert.equal(got, want);
});

test('findNpmCli macOS: inny prefiks instalacji, ten sam uklad lib/node_modules', async () => {
  const { findNpmCli } = await load();
  const want = '/m/opt/node/lib/node_modules/npm/bin/npm-cli.js';
  const got = findNpmCli({ env: {}, execPath: '/m/opt/node/bin/node', platform: 'darwin', fs: pathFs([want]) });
  assert.equal(got, want);
});

test('findNpmCli Windows: <execDir>\\node_modules\\npm\\bin\\npm-cli.js (bez bin, sciezki Windows)', async () => {
  const { findNpmCli } = await load();
  const want = 'D:\\h\\nodejs\\node_modules\\npm\\bin\\npm-cli.js';
  const got = findNpmCli({ env: {}, execPath: 'D:\\h\\nodejs\\node.exe', platform: 'win32', fs: pathFs([want]) });
  assert.equal(got, want);
});

test('findNpmCli: pierwszenstwo ma <execDir>/node_modules/npm/bin/npm-cli.js nad ../lib/...', async () => {
  const { findNpmCli } = await load();
  const first = '/m/n/bin/node_modules/npm/bin/npm-cli.js';
  const second = '/m/n/lib/node_modules/npm/bin/npm-cli.js';
  assert.equal(findNpmCli({ env: {}, execPath: '/m/n/bin/node', platform: 'linux', fs: pathFs([first, second]) }), first);
});

test('findNpmCli: env.npm_execpath konczace sie na npm-cli.js wygrywa; inne (yarn, pnpm) sa ignorowane', async () => {
  const { findNpmCli } = await load();
  const viaEnv = '/m/env/npm/bin/npm-cli.js';
  const layout = '/m/node-v22/lib/node_modules/npm/bin/npm-cli.js';
  const fsx = pathFs([viaEnv, layout]);
  assert.equal(findNpmCli({ env: { npm_execpath: viaEnv }, execPath: '/m/node-v22/bin/node', platform: 'linux', fs: fsx }), viaEnv);
  for (const other of ['/m/yarn/bin/yarn.js', '/m/pnpm/bin/pnpm.cjs']) {
    assert.equal(findNpmCli({ env: { npm_execpath: other }, execPath: '/m/node-v22/bin/node', platform: 'linux', fs: fsx }), layout);
  }
});

test('findNpmCli: brak npm w ktorymkolwiek ukladzie => wartosc falsy (nie goly "npm")', async () => {
  const { findNpmCli } = await load();
  for (const [execPath, platform] of [['/m/x/bin/node', 'linux'], ['/m/x/bin/node', 'darwin'], ['D:\\h\\x\\node.exe', 'win32']]) {
    const got = findNpmCli({ env: {}, execPath, platform, fs: pathFs([]) });
    assert.ok(!got, `${platform}: ${got}`);
  }
});

test('brak npm => kod 3 z instrukcja, spawn nie jest wolany, nic nie zostaje w cache', async () => {
  const s = await makeSession({ env: { npm_execpath: '' } });
  const r = await s.run({ execPath: path.join(s.base, 'no-node', 'bin', 'node') });
  assert.equal(r.exit, 3, r.message);
  assert.match(r.message, /npm/i);
  assert.equal(s.sp.calls.length, 0);
  assert.deepEqual(s.leftovers(), []);
});

// --- wyjscie npm: skracanie i maskowanie ---

test('wyjscie npm w bledzie: maskowany sekret i skrocone do ok. 4000 znakow', async () => {
  const filler = 'x'.repeat(10_000);
  const s = await makeSession({ spawn: { code: 1, stdout: `${SECRET} ${filler}`, stderr: `${filler} npm ERR! token ${SECRET}` } });
  const r = await s.run();
  assert.equal(r.exit, 3);
  assert.doesNotMatch(r.message, new RegExp(SECRET));
  assert.match(r.message, /\*\*\*/);
  assert.ok(r.message.length <= 4000 + 1000, `message=${r.message.length}`);
});

test('krotkie wyjscie npm nie jest obcinane', async () => {
  const s = await makeSession({ spawn: { code: 1, stderr: 'npm ERR! network request failed' } });
  const r = await s.run();
  assert.match(r.message, /npm ERR! network request failed/);
});

// --- bezpieczenstwo cache ---

test('cache: tworzony z mode 0o700 i recursive, gdy go brak', async () => {
  const s = await makeSession({ rootExists: false });
  const r = await s.run();
  assert.equal(r.exit, 0, r.message);
  const mk = s.fs.log.filter(([n, p]) => n === 'mkdirSync' && p === s.cacheRoot);
  assert.ok(mk.length >= 1, 'mkdirSync(root)');
  assert.equal(mk[0][2].mode, 0o700);
  assert.equal(mk[0][2].recursive, true);
});

test('cache nalezacy do obcego uid => kod 2, nic nie instalowane', POSIX_ONLY, async () => {
  const s0 = await makeSession();
  const foreign = process.getuid() + 1;
  const stat = (p, ...a) => {
    const real = fs.statSync(p, ...a);
    return path.resolve(p) === path.resolve(s0.cacheRoot) ? fakeStats(real, { uid: foreign }) : real;
  };
  s0.fs = wrapFs({ statSync: stat, lstatSync: stat });
  const r = await s0.run();
  assert.equal(r.exit, 2, r.message);
  assert.match(r.message, /cache/i);
  assert.equal(s0.sp.calls.length, 0);
  assert.deepEqual(s0.leftovers(), []);
});

for (const [label, mode] of [['grupy', 0o775], ['innych', 0o757], ['grupy i innych', 0o777]]) {
  test(`cache z bitem zapisu dla ${label} => kod 2, nic nie instalowane`, POSIX_ONLY, async () => {
    const s = await makeSession();
    fs.chmodSync(s.cacheRoot, mode);
    const r = await s.run();
    assert.equal(r.exit, 2, r.message);
    assert.equal(s.sp.calls.length, 0);
    assert.deepEqual(s.leftovers(), []);
  });
}

test('cache 0o700 i 0o755 (bez zapisu g/o) jest akceptowany', POSIX_ONLY, async () => {
  for (const mode of [0o700, 0o755]) {
    const s = await makeSession();
    fs.chmodSync(s.cacheRoot, mode);
    const r = await s.run();
    assert.equal(r.exit, 0, `mode ${mode.toString(8)}: ${r.message}`);
  }
});

test('UI_VERIFY_CACHE_DIR wewnatrz katalogu skilla => kod 2, katalog nie powstaje, npm nie startuje', async () => {
  const s = await makeSession();
  const inside = path.join(s.skillDir, 'cache');
  const r = await s.run({ env: { ...s.env, UI_VERIFY_CACHE_DIR: inside } });
  assert.equal(r.exit, 2, r.message);
  assert.equal(fs.existsSync(inside), false);
  assert.equal(s.sp.calls.length, 0);
  assert.match(r.message, /cache/i);
});

test('cache wewnatrz skilla takze przez ".." i jako sam katalog skilla => kod 2', async () => {
  const s = await makeSession();
  for (const dir of [path.join(s.skillDir, 'a', '..', 'b'), s.skillDir]) {
    const r = await s.run({ env: { ...s.env, UI_VERIFY_CACHE_DIR: dir } });
    assert.equal(r.exit, 2, `${dir}: ${r.message}`);
  }
  assert.equal(fs.existsSync(path.join(s.skillDir, 'b')), false);
});

test('cache obok katalogu skilla (wspolny prefiks nazwy) nie jest "wewnatrz"', async () => {
  const s = await makeSession();
  const sibling = `${s.skillDir}-cache`;
  fs.mkdirSync(sibling, { mode: 0o700 });
  if (!WIN) fs.chmodSync(sibling, 0o700);
  const r = await s.run({ env: { ...s.env, UI_VERIFY_CACHE_DIR: sibling } });
  assert.equal(r.exit, 0, r.message);
});

test('Windows pomija sprawdzenie uid i bitow zapisu (checkCacheSafety)', async () => {
  const { checkCacheSafety } = await load();
  const root = 'D:\\h\\cache';
  const wfs = {
    existsSync: () => true,
    statSync: () => ({ uid: 0, mode: 0o40777, isDirectory: () => true }),
    lstatSync: () => ({ uid: 0, mode: 0o40777, isDirectory: () => true }),
  };
  assert.doesNotThrow(() => checkCacheSafety({ root, skillDir: 'D:\\h\\skill', platform: 'win32', fs: wfs, proc: {} }));
});

test('checkCacheSafety na Windows nadal odmawia cache wewnatrz katalogu skilla', async () => {
  const { checkCacheSafety } = await load();
  const wfs = { existsSync: () => false, statSync: () => { throw new Error('nie dotyka'); }, lstatSync: () => { throw new Error('nie dotyka'); } };
  assert.throws(() => checkCacheSafety({ root: 'D:\\h\\skill\\cache', skillDir: 'D:\\h\\skill', platform: 'win32', fs: wfs, proc: {} }));
});

test('modul eksportuje installDeps, findNpmCli i checkCacheSafety jako funkcje (import bez efektow)', async () => {
  const m = await load();
  for (const n of ['installDeps', 'findNpmCli', 'checkCacheSafety']) assert.equal(typeof m[n], 'function', n);
});

test('N5 installDeps: wzgledny UI_VERIFY_CACHE_DIR => kod 2, takze w trybie check; bez spawn i bez katalogu', async () => {
  for (const check of [false, true]) {
    const s = await makeSession({ env: { UI_VERIFY_CACHE_DIR: path.join('wzgledny', 'cache') } });
    const r = await s.run({ check });
    assert.equal(r.exit, 2, `check=${check}: ${r.message}`);
    assert.equal(r.status, 'error');
    assert.match(r.message, /absolute/);
    assert.equal(s.sp.calls.length, 0);
  }
});
