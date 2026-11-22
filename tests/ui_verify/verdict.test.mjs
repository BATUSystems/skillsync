import test from 'node:test';
import assert from 'node:assert/strict';
import { EXIT, combine, exitFor, installGuards } from '../../library/skills/ui-verify/scripts/lib/verdict.mjs';

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
