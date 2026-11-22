import test from 'node:test';
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import * as verdictLib from '../../library/skills/ui-verify/scripts/lib/verdict.mjs';
import { createMasker } from '../../library/skills/ui-verify/scripts/lib/out.mjs';

const { EXIT, combine, exitFor, installGuards } = verdictLib;
// isMain: namespace, by brak eksportu dawal czerwone testy, a nie blad skladni calego pliku.
const isMain = (...a) => verdictLib.isMain(...a);

test('EXIT ma kody 0/1/2/3', () => {
  assert.deepEqual({ ...EXIT }, { PASS: 0, FAIL: 1, ERROR: 2, NOT_RUN: 3 });
});

test('combine: tabela werdyktow', () => {
  const rows = [
    [['PASS'], 'PASS'],
    [['PASS', 'PASS', 'PASS'], 'PASS'],
    [['PASS', 'FAIL'], 'FAIL'],
    [['FAIL', 'UNVERIFIED'], 'FAIL'],
    [['UNVERIFIED', 'FAIL', 'PASS'], 'FAIL'],
    [['PASS', 'UNVERIFIED'], 'UNVERIFIED'],
    [['UNVERIFIED'], 'UNVERIFIED'],
    [[], 'UNVERIFIED'],
    [['PASS', 'MAYBE'], 'UNVERIFIED'],
    [['MAYBE'], 'UNVERIFIED'],
    [['pass'], 'UNVERIFIED'],
  ];
  for (const [input, want] of rows) assert.equal(combine(input), want, JSON.stringify(input));
});

test('exitFor: PASS=0, FAIL=1, UNVERIFIED=3, ERROR=2, NOT_RUN=3, nieznany=2', () => {
  assert.equal(exitFor('PASS'), 0);
  assert.equal(exitFor('FAIL'), 1);
  assert.equal(exitFor('UNVERIFIED'), 3);
  assert.equal(exitFor('ERROR'), 2);
  assert.equal(exitFor('NOT_RUN'), 3);
  assert.equal(exitFor('whatever'), 2);
  assert.equal(exitFor(undefined), 2);
});

function fakeProc() {
  const handlers = new Map();
  const exits = [];
  return {
    handlers, exits,
    on(ev, fn) { handlers.set(ev, fn); return this; },
    off(ev, fn) { if (handlers.get(ev) === fn) handlers.delete(ev); return this; },
    removeListener(ev, fn) { if (handlers.get(ev) === fn) handlers.delete(ev); return this; },
    exit(code) { exits.push(code); },
  };
}

test('installGuards: oba handlery koncza exit(2), nigdy 1', () => {
  const proc = fakeProc();
  installGuards(proc);
  assert.ok(proc.handlers.has('uncaughtException'));
  assert.ok(proc.handlers.has('unhandledRejection'));
  proc.handlers.get('uncaughtException')(new Error('boom'));
  proc.handlers.get('unhandledRejection')(new Error('reject'));
  proc.handlers.get('unhandledRejection')('tekst');
  assert.deepEqual(proc.exits, [2, 2, 2]);
  assert.ok(!proc.exits.includes(1));
});

test('installGuards: hooks.onError dostaje blad przed exit(2)', () => {
  const proc = fakeProc();
  const order = [];
  const origExit = proc.exit;
  proc.exit = (c) => { order.push('exit'); origExit.call(proc, c); };
  const seen = [];
  installGuards(proc, { onError: (e) => { order.push('onError'); seen.push(e); } });
  const err = new Error('x');
  proc.handlers.get('uncaughtException')(err);
  assert.deepEqual(seen, [err]);
  assert.deepEqual(order, ['onError', 'exit']);
  assert.deepEqual(proc.exits, [2]);
});

test('installGuards: zwraca funkcje odpinajaca', () => {
  const proc = fakeProc();
  const off = installGuards(proc);
  assert.equal(typeof off, 'function');
  off();
  assert.equal(proc.handlers.has('uncaughtException'), false);
  assert.equal(proc.handlers.has('unhandledRejection'), false);
});

// --- isMain (A1): strazник uruchomienia jako skrypt glowny, odporny na symlinki ---

const SCRIPT_PATH = path.resolve(os.tmpdir(), 'uv-ismain', 'real', 'tool.mjs');
const SCRIPT_URL = pathToFileURL(SCRIPT_PATH).href;
const LINK_PATH = path.resolve(os.tmpdir(), 'uv-ismain', 'link', 'tool.mjs');
const OTHER_PATH = path.resolve(os.tmpdir(), 'uv-ismain', 'real', 'other.mjs');

// Atrapa fs: realpathSync mapuje symlink na cel, reszta to tozsamosc.
const fsWith = (links = {}) => ({ realpathSync: (p) => links[p] ?? p });
const fsBroken = () => ({ realpathSync: () => { throw Object.assign(new Error('ENOENT'), { code: 'ENOENT' }); } });

test('isMain: ta sama sciezka => true', () => {
  assert.equal(isMain(SCRIPT_URL, SCRIPT_PATH, fsWith()), true);
});

test('isMain: argv[1] to symlink na ten plik => true (realpath obu stron)', () => {
  assert.equal(isMain(SCRIPT_URL, LINK_PATH, fsWith({ [LINK_PATH]: SCRIPT_PATH })), true);
});

test('isMain: symlink na inny plik albo inny skrypt => false (kontrola negatywna)', () => {
  assert.equal(isMain(SCRIPT_URL, LINK_PATH, fsWith({ [LINK_PATH]: OTHER_PATH })), false);
  assert.equal(isMain(SCRIPT_URL, OTHER_PATH, fsWith()), false);
});

test('isMain: brak argv[1] (import z REPL/-e) => false', () => {
  assert.equal(isMain(SCRIPT_URL, undefined, fsWith()), false);
  assert.equal(isMain(SCRIPT_URL, '', fsWith()), false);
});

test('isMain: blad realpath => porownanie leksykalne (rowne => true, rozne => false), bez wyjatku', () => {
  assert.equal(isMain(SCRIPT_URL, SCRIPT_PATH, fsBroken()), true);
  assert.equal(isMain(SCRIPT_URL, OTHER_PATH, fsBroken()), false);
  assert.equal(isMain(SCRIPT_URL, LINK_PATH, fsBroken()), false);
});

// --- installGuards z maskowaniem komunikatu (B2) ---

function fakeProcWithStderr() {
  const proc = fakeProc();
  proc.written = [];
  proc.stderr = { write: (t) => { proc.written.push(String(t)); return true; } };
  return proc;
}
const guardSecret = () => ['guard', 'secret', String(process.pid), 'wxyz'].join('-');

test('installGuards({ mask }): komunikat bledu na stderr przechodzi przez maskowanie, exit(2)', () => {
  const secret = guardSecret();
  const proc = fakeProcWithStderr();
  installGuards(proc, { mask: createMasker({ secrets: [secret] }).mask });
  proc.handlers.get('uncaughtException')(new Error(`login failed for ${secret}`));
  proc.handlers.get('unhandledRejection')(`rejected with ${secret}`);
  const all = proc.written.join('');
  assert.ok(all.length > 0, 'blad powinien byc zgloszony na stderr');
  assert.ok(!all.includes(secret), `sekret w stderr: ${all}`);
  assert.match(all, /\*\*\*/);
  assert.deepEqual(proc.exits, [2, 2]);
});

test('installGuards: kontrola - bez maskowania sekretu ten sam blad trafia na stderr (test wycieku potrafi paść)', () => {
  const secret = guardSecret();
  const proc = fakeProcWithStderr();
  installGuards(proc, { mask: createMasker({ secrets: [] }).mask });
  proc.handlers.get('uncaughtException')(new Error(`login failed for ${secret}`));
  assert.ok(proc.written.join('').includes(secret));
});
