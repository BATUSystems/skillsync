import test from 'node:test';
import assert from 'node:assert/strict';
import { createMasker, createSink, collectSecretsFromEnv } from '../../library/skills/ui-verify/scripts/lib/out.mjs';

// Atrapa skladana w czasie wykonania (gitleaks): zadnego literalu przypominajacego sekret.
const SECRET = ['Zq', 'x7', '/Pa', 's&w', ' o"r', 'd!9'].join('');
const SECRET_PLAIN = ['tok', 'en', 'Val', '42'].join('');

test('createMasker: maskuje wartosc i podaje liczbe sekretow', () => {
  const m = createMasker({ secrets: [SECRET_PLAIN] });
  assert.equal(m.mask(`a ${SECRET_PLAIN} b`), 'a *** b');
  assert.equal(m.mask('nic'), 'nic');
  assert.equal(typeof m.secrets, 'number');
  assert.ok(m.secrets >= 1);
});

test('createMasker: warianty URL-encode, JSON-escape, base64', () => {
  const m = createMasker({ secrets: [SECRET] });
  const variants = [
    SECRET,
    encodeURIComponent(SECRET),
    JSON.stringify(SECRET).slice(1, -1),
    Buffer.from(SECRET).toString('base64'),
  ];
  for (const v of variants) {
    const out = m.mask(`pre ${v} post`);
    assert.ok(!out.includes(v), `wyciek wariantu: ${v}`);
    assert.ok(out.includes('***'));
  }
});

test('createMasker: wartosc w apostrofach, cudzyslowach i w fill("...")', () => {
  const m = createMasker({ secrets: [SECRET_PLAIN] });
  for (const text of [`'${SECRET_PLAIN}'`, `"${SECRET_PLAIN}"`, `page.fill("#pw", "${SECRET_PLAIN}")`, `fill("${SECRET_PLAIN}")`, `locator.fill('${SECRET_PLAIN}')`]) {
    assert.ok(!m.mask(text).includes(SECRET_PLAIN), text);
  }
});

test('createMasker: Error.message i stack przez maskValue, bez mutacji', () => {
  const m = createMasker({ secrets: [SECRET_PLAIN] });
  const err = new Error(`fill("${SECRET_PLAIN}") timeout`);
  const out = m.maskValue(err);
  assert.ok(!String(out.message).includes(SECRET_PLAIN));
  assert.ok(!String(out.stack).includes(SECRET_PLAIN));
  assert.ok(err.message.includes(SECRET_PLAIN), 'wejscie nie moze byc zmutowane');
  assert.ok(err.stack.includes(SECRET_PLAIN));
});

test('createMasker: maskValue rekurencyjnie po obiektach i tablicach, nie mutuje', () => {
  const m = createMasker({ secrets: [SECRET_PLAIN] });
  const input = { a: SECRET_PLAIN, list: ['x', `y ${SECRET_PLAIN}`, { deep: [SECRET_PLAIN] }], n: 5, ok: true, nul: null };
  const snapshot = JSON.stringify(input);
  const out = m.maskValue(input);
  assert.ok(!JSON.stringify(out).includes(SECRET_PLAIN));
  assert.equal(out.n, 5);
  assert.equal(out.ok, true);
  assert.equal(out.nul, null);
  assert.equal(JSON.stringify(input), snapshot);
  assert.notEqual(out, input);
  assert.notEqual(out.list, input.list);
});

test('createMasker: sekrety krotsze niz 4 znaki i puste sa ignorowane', () => {
  const m = createMasker({ secrets: ['abc', '', 'a'] });
  assert.equal(m.mask('abc a abc'), 'abc a abc');
  assert.equal(m.secrets, 0);
});

test('createMasker: extra RegExp tez maskowane', () => {
  const m = createMasker({ secrets: [], extra: [/id-\d+/g] });
  assert.equal(m.mask('x id-123 y'), 'x *** y');
});

function fakeFs() {
  const files = new Map();
  const dirs = [];
  return {
    files, dirs,
    writeFileSync(p, d) { files.set(p, d); },
    mkdirSync(p) { dirs.push(p); },
    promises: {
      async writeFile(p, d) { files.set(p, d); },
      async mkdir(p) { dirs.push(p); },
    },
  };
}
function mkSink(secrets = [SECRET_PLAIN]) {
  const fs = fakeFs();
  const out = []; const err = [];
  const sink = createSink({
    masker: createMasker({ secrets }),
    fs,
    root: '/work/out',
    stdout: { write: (t) => { out.push(String(t)); return true; } },
    stderr: { write: (t) => { err.push(String(t)); return true; } },
  });
  return { sink, fs, out, err };
}

test('createSink.writeFile: zapis pod root, tekst maskowany', async () => {
  const { sink, fs } = mkSink();
  await sink.writeFile('report.json', `{"x":"${SECRET_PLAIN}"}`);
  const entries = [...fs.files.entries()];
  assert.equal(entries.length, 1);
  assert.ok(entries[0][0].startsWith('/work/out/'), entries[0][0]);
  assert.ok(entries[0][0].endsWith('report.json'));
  assert.ok(!String(entries[0][1]).includes(SECRET_PLAIN));
});

test('createSink.writeFile: odmowa poza root, nic nie zapisane', async () => {
  const { sink, fs } = mkSink();
  for (const rel of ['../evil.txt', '/etc/passwd', 'a/../../b']) {
    await assert.rejects(Promise.resolve().then(() => sink.writeFile(rel, 'x')), (e) => e.code === 'outside-root', rel);
  }
  assert.equal(fs.files.size, 0);
});

test('createSink.writeFile: nazwa pliku maskowana', async () => {
  const { sink, fs } = mkSink();
  await sink.writeFile(`shot-${SECRET_PLAIN}.txt`, 'x');
  for (const p of fs.files.keys()) assert.ok(!p.includes(SECRET_PLAIN), p);
  assert.equal(fs.files.size, 1);
});

test('createSink.writeFile: Buffer zapisany bez maskowania tresci binarnej', async () => {
  const { sink, fs } = mkSink();
  const buf = Buffer.from([0x89, 0x50, 0x4e, 0x47, ...Buffer.from(SECRET_PLAIN)]);
  await sink.writeFile('shot.png', buf);
  const stored = [...fs.files.values()][0];
  assert.ok(Buffer.isBuffer(stored));
  assert.ok(Buffer.compare(stored, buf) === 0);
});

test('createSink.write: stdout i stderr maskowane', async () => {
  const { sink, out, err } = mkSink();
  await sink.write('stdout', `a ${SECRET_PLAIN}\n`);
  await sink.write('stderr', `b ${SECRET_PLAIN}\n`);
  assert.equal(out.length, 1);
  assert.equal(err.length, 1);
  assert.ok(!out.join('').includes(SECRET_PLAIN));
  assert.ok(!err.join('').includes(SECRET_PLAIN));
  assert.ok(out.join('').includes('***'));
});

test('createSink.error: message i stack maskowane na stderr', async () => {
  const { sink, out, err } = mkSink();
  await sink.error(new Error(`fill("${SECRET_PLAIN}") failed`));
  const all = out.join('') + err.join('');
  assert.ok(all.length > 0);
  assert.ok(!all.includes(SECRET_PLAIN));
});

test('collectSecretsFromEnv: wartosci wskazanych zmiennych, puste i brakujace pomijane', () => {
  const env = { A_PW: SECRET_PLAIN, B_PW: '', C_OTHER: 'zzzz9', D: undefined };
  assert.deepEqual(collectSecretsFromEnv(env, ['A_PW', 'B_PW', 'MISSING', 'D']), [SECRET_PLAIN]);
  assert.deepEqual(collectSecretsFromEnv(env, []), []);
});
