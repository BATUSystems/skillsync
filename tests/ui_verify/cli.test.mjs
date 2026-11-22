import test from 'node:test';
import assert from 'node:assert/strict';
import { createMasker } from '../../library/skills/ui-verify/scripts/lib/out.mjs';
import { parseArgs, runSelftest } from '../../library/skills/ui-verify/scripts/lib/cli.mjs';

const SPEC = {
  string: ['config'],
  boolean: ['selftest', 'allow-trace', 'help'],
  repeatable: ['allow-origin'],
};

test('parseArgs: flagi z wartoscia i logiczne', () => {
  const r = parseArgs(['--config', 'ui.json', '--selftest', '--allow-trace'], SPEC);
  assert.deepEqual(r.errors, []);
  assert.equal(r.values.config, 'ui.json');
  assert.equal(r.values.selftest, true);
  assert.equal(r.values['allow-trace'], true);
  assert.deepEqual(r.positionals, []);
});

test('parseArgs: powtarzalne --allow-origin zbiera liste', () => {
  const r = parseArgs(['--allow-origin', 'https://a.example', '--allow-origin', '*.b.example'], SPEC);
  assert.deepEqual(r.errors, []);
  assert.deepEqual(r.values['allow-origin'], ['https://a.example', '*.b.example']);
});

test('parseArgs: pozycyjne argumenty', () => {
  const r = parseArgs(['one', '--help', 'two'], SPEC);
  assert.deepEqual(r.positionals, ['one', 'two']);
  assert.equal(r.values.help, true);
});

test('parseArgs: nieznana flaga trafia do errors', () => {
  const r = parseArgs(['--nope', '--selftest'], SPEC);
  assert.ok(r.errors.length >= 1);
  assert.ok(r.errors.join(' ').includes('--nope'));
  assert.equal(r.values.selftest, true);
});

test('parseArgs: flaga z wartoscia bez wartosci => errors', () => {
  const r = parseArgs(['--config'], SPEC);
  assert.ok(r.errors.length >= 1);
});

function io() {
  const out = [];
  return { out, stdout: { write: (t) => { out.push(String(t)); return true; } }, stderr: { write: (t) => { out.push(String(t)); return true; } } };
}

test('runSelftest: wszystkie przeszly => 0, drukuje PASS <name>', async () => {
  const i = io();
  const code = await runSelftest('x', [{ name: 'a', fn: () => {} }, { name: 'b', fn: async () => {} }], i);
  assert.equal(code, 0);
  const text = i.out.join('');
  assert.ok(/PASS a/.test(text));
  assert.ok(/PASS b/.test(text));
});

test('runSelftest: wyjatek lub odrzucenie => 1 i FAIL <name>', async () => {
  const i = io();
  const code = await runSelftest('x', [
    { name: 'ok', fn: () => {} },
    { name: 'bad', fn: () => { throw new Error('boom'); } },
    { name: 'rej', fn: async () => { throw new Error('nope'); } },
  ], i);
  assert.equal(code, 1);
  const text = i.out.join('');
  assert.ok(/PASS ok/.test(text));
  assert.ok(/FAIL bad/.test(text));
  assert.ok(/FAIL rej/.test(text));
});

test('runSelftest: pusta lista => 2', async () => {
  assert.equal(await runSelftest('x', [], io()), 2);
});

test('runSelftest: wiadomosc wyjatku jest maskowana (io.masker) i nie wycieka', async () => {
  const secret = ['pw', 'Val', 'ue', '77'].join('');
  const i = io();
  i.masker = createMasker({ secrets: [secret] });
  const code = await runSelftest('x', [{ name: 'leak', fn: () => { throw new Error(`fill("${secret}")`); } }], i);
  assert.equal(code, 1);
  assert.ok(!i.out.join('').includes(secret));
});
