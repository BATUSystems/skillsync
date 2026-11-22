// Uruchamianie skryptow jako proces glowny (S10): strazник isMain przez symlinki (A1), kod 2 przy nieobsluzonym
// wyjatku w PRAWDZIWYM procesie (A8) i maskowanie komunikatu przez guard masker (B2). Bez Chrome i bez zaleznosci z cache.
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
const SCRIPTS_DIR = path.join(SKILL, 'scripts');
const LIB_URL = (name) => pathToFileURL(path.join(SCRIPTS_DIR, 'lib', name)).href;
const ENTRYPOINTS = ['pixel-diff', 'measure', 'capture', 'diff', 'install'];

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-entry-'));
after(() => fs.rmSync(tmpRoot, { recursive: true, force: true }));
let counter = 0;
const newDir = (label = 'd') => {
  const d = path.join(tmpRoot, `${label}-${counter++}`);
  fs.mkdirSync(d, { recursive: true });
  return d;
};

// Tworzenie symlinkow bywa zablokowane (Windows bez uprawnien): wtedy testy sa pomijane z powodem needs-symlink.
const SYMLINK_SKIP = (() => {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-entry-probe-'));
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

function cleanEnv(extra = {}) {
  const base = { ...process.env };
  for (const k of Object.keys(base)) if (k.startsWith('UI_VERIFY_') || k === 'PLAYWRIGHT_MODULE_PATH') delete base[k];
  return { ...base, UI_VERIFY_CACHE_DIR: newDir('emptycache'), ...extra };
}
function node(args, { cwd, env } = {}) {
  const r = spawnSync(process.execPath, args, { cwd: cwd ?? newDir('cwd'), env: env ?? cleanEnv(), encoding: 'utf8', timeout: 120000 });
  return { code: r.status, stdout: r.stdout ?? '', stderr: r.stderr ?? '', error: r.error };
}

// --- A1: wywolanie przez symlink nie moze dawac kodu 0 bez wyniku ---

for (const name of ENTRYPOINTS) {
  const direct = () => node([path.join(SCRIPTS_DIR, `${name}.mjs`), '--selftest']);

  test(`A1 ${name}.mjs: node <symlink do pliku> --selftest => to samo wyjscie i kod co bezposrednio (niepuste, nie ciche 0)`, { skip: SYMLINK_SKIP }, () => {
    const dir = newDir('link');
    const link = path.join(dir, `${name}.mjs`);
    fs.symlinkSync(path.join(SCRIPTS_DIR, `${name}.mjs`), link, 'file');
    const want = direct();
    assert.ok(want.stdout.trim() !== '', `bezposrednie uruchomienie bez wyjscia: ${want.stderr}`);
    const got = node([link, '--selftest']);
    assert.notEqual(got.stdout.trim() + got.stderr.trim(), '', 'symlink: kod bez zadnego wyjscia (main() nie wykonany)');
    assert.equal(got.code, want.code, `symlink: kod ${got.code} zamiast ${want.code}\n${got.stdout}${got.stderr}`);
    assert.match(got.stdout, /selftest/);
  });

  test(`A1 ${name}.mjs: node <katalog-symlink>/${name}.mjs --selftest => selftest sie wykonuje`, { skip: SYMLINK_SKIP }, () => {
    const dir = newDir('dirlink');
    const link = path.join(dir, 'scripts-link');
    fs.symlinkSync(SCRIPTS_DIR, link, 'junction');
    const want = direct();
    const got = node([path.join(link, `${name}.mjs`), '--selftest']);
    assert.match(got.stdout, /selftest/, `brak wyniku selftestu: ${got.stdout}${got.stderr}`);
    assert.equal(got.code, want.code, `${got.stdout}${got.stderr}`);
  });

  test(`A1 ${name}.mjs: sciezka wzgledna z cwd => selftest sie wykonuje`, () => {
    const want = direct();
    const got = node([path.join('library', 'skills', 'ui-verify', 'scripts', `${name}.mjs`), '--selftest'], { cwd: ROOT });
    assert.match(got.stdout, /selftest/, `${got.stdout}${got.stderr}`);
    assert.equal(got.code, want.code);
  });
}

// --- A8: prawdziwy proces z installGuards - nieobsluzony wyjatek/odrzucenie => kod 2, nigdy 1 ---

function guardScript(body, { setup = '' } = {}) {
  const dir = newDir('guard');
  const file = path.join(dir, 'guard.mjs');
  fs.writeFileSync(file, `import { installGuards } from ${JSON.stringify(LIB_URL('verdict.mjs'))};\n${setup}\ninstallGuards(process);\n${body}\n`);
  return file;
}
const NO_GUARD_BODY = (body) => {
  const dir = newDir('noguard');
  const file = path.join(dir, 'noguard.mjs');
  fs.writeFileSync(file, `${body}\n`);
  return file;
};

for (const [label, body] of [
  ['uncaughtException (throw w zdarzeniu)', "setTimeout(() => { throw new Error('boom'); }, 0);"],
  ['unhandledRejection (Error)', "Promise.reject(new Error('rejected'));"],
  ['unhandledRejection (napis)', "Promise.reject('plain string');"],
  ['wyjatek w async main', "(async () => { await null; throw new Error('async boom'); })();"],
]) {
  test(`A8 installGuards w prawdziwym procesie: ${label} => kod 2 (nie 1)`, () => {
    const r = node([guardScript(body)]);
    assert.equal(r.code, 2, `kod ${r.code}\n${r.stdout}${r.stderr}`);
  });

  test(`A8 kontrola: ten sam kod bez installGuards konczy sie kodem 1 (test odroznia 2 od domyslnego 1)`, () => {
    const r = node([NO_GUARD_BODY(body)]);
    assert.equal(r.code, 1, `kod ${r.code}\n${r.stdout}${r.stderr}`);
  });
}

// --- B2: komunikat bledu zgloszony przez handler globalny jest maskowany ---

const guardSecret = () => ['proc', 'secret', String(process.pid), 'mnop'].join('-');

test('B2: sekret z UI_VERIFY_SECRET_ENV w komunikacie nieobsluzonego bledu nie trafia na stderr/stdout; kod 2', () => {
  const secret = guardSecret();
  const file = guardScript('setTimeout(() => { throw new Error(`login failed for ${process.env.UV_PROC_SECRET}`); }, 0);');
  const r = node([file], { env: cleanEnv({ UV_PROC_SECRET: secret, UI_VERIFY_SECRET_ENV: 'UV_PROC_SECRET' }) });
  assert.equal(r.code, 2, r.stdout + r.stderr);
  assert.ok(!r.stderr.includes(secret) && !r.stdout.includes(secret), `sekret w wyjsciu: ${r.stderr}`);
  assert.notEqual(r.stderr.trim(), '', 'blad powinien byc zgloszony (zamaskowany) na stderr');
  assert.match(r.stderr, /\*\*\*/);
});

test('B2 kontrola: ta sama zmienna niezadeklarowana w UI_VERIFY_SECRET_ENV trafia na stderr (test wycieku potrafi paść)', () => {
  const secret = guardSecret();
  const file = guardScript('setTimeout(() => { throw new Error(`login failed for ${process.env.UV_PROC_SECRET}`); }, 0);');
  const r = node([file], { env: cleanEnv({ UV_PROC_SECRET: secret }) });
  assert.equal(r.code, 2, r.stdout + r.stderr);
  assert.ok(r.stderr.includes(secret), 'bez deklaracji sekretu komunikat jest widoczny');
});

test('B2: setGuardMasker z maskerem skryptu (sekrety z konfiguracji) obejmuje komunikat handlera', () => {
  const secret = guardSecret();
  const outUrl = LIB_URL('out.mjs');
  const setup = `import { createMasker, setGuardMasker } from ${JSON.stringify(outUrl)};\nsetGuardMasker(createMasker({ secrets: [process.env.UV_PROC_SECRET] }));`;
  const file = guardScript('Promise.reject(new Error(`token ${process.env.UV_PROC_SECRET} rejected`));', { setup });
  const r = node([file], { env: cleanEnv({ UV_PROC_SECRET: secret }) });
  assert.equal(r.code, 2, r.stdout + r.stderr);
  assert.ok(!r.stderr.includes(secret), `sekret w stderr: ${r.stderr}`);
  assert.match(r.stderr, /\*\*\*/);
});

// --- N3: kod wyjscia DepsError w prawdziwym procesie: 2 = niebezpieczny cache, 3 = brak zaleznosci ---

function depsProject() {
  const dir = newDir('depsproj');
  fs.writeFileSync(path.join(dir, 'page.html'), '<!doctype html><html><body><h1 id="t">x</h1></body></html>');
  const measureCfg = path.join(dir, 'measure.json');
  fs.writeFileSync(measureCfg, JSON.stringify({ version: 1, pairs: [{ id: 'p', reference: { url: 'page.html', selector: '#t' }, candidate: { url: 'page.html', selector: '#t' } }] }));
  const captureCfg = path.join(dir, 'capture.json');
  fs.writeFileSync(captureCfg, JSON.stringify({ version: 1, pages: [{ id: 'home', url: 'page.html', assert: { selector: 'h1' } }] }));
  fs.writeFileSync(path.join(dir, 'a.png'), 'x');
  fs.writeFileSync(path.join(dir, 'b.png'), 'x');
  fs.mkdirSync(path.join(dir, 'before'));
  fs.mkdirSync(path.join(dir, 'after'));
  return {
    dir,
    cases: {
      measure: ['--config', 'measure.json', 'out'],
      capture: ['--config', 'capture.json', 'out'],
      diff: ['before', 'after'],
      'pixel-diff': ['a.png', 'b.png'],
    },
  };
}

for (const name of ['measure', 'capture', 'diff', 'pixel-diff']) {
  test(`N3 ${name}.mjs: wzgledny UI_VERIFY_CACHE_DIR => kod 2, pusty bezwzgledny katalog => kod 3 (prawdziwy proces)`, () => {
    const { dir, cases } = depsProject();
    const script = path.join(SCRIPTS_DIR, `${name}.mjs`);
    const rel = node([script, ...cases[name]], { cwd: dir, env: cleanEnv({ UI_VERIFY_CACHE_DIR: 'wzgledny/cache' }) });
    assert.equal(rel.code, 2, `rel: ${rel.code}\n${rel.stderr}`);
    const empty = node([script, ...cases[name]], { cwd: dir, env: cleanEnv() });
    assert.equal(empty.code, 3, `empty: ${empty.code}\n${empty.stderr}`);
  });
}
