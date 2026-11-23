// CORE-1142-UIV-SEC (D1): zapis artefaktow wrazliwych (trace.zip, storageState.json) z --allow-trace: tryb 0600, .gitignore w outDir tylko gdy go nie bylo,
// konkretne ostrzezenie bez zludnych gwarancji. Prawdziwy system plikow w katalogu tymczasowym, bez przegladarki.
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const LIB = path.resolve(HERE, '..', '..', 'library', 'skills', 'ui-verify', 'scripts', 'lib');
const load = () => import(pathToFileURL(path.join(LIB, 'artifacts.mjs')).href);
const loadOut = () => import(pathToFileURL(path.join(LIB, 'out.mjs')).href);

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-artifacts-'));
after(() => fs.rmSync(tmp, { recursive: true, force: true }));
let n = 0;
const POSIX = process.platform !== 'win32';

async function setup(art, { outRel = 'out', prepare } = {}) {
  const root = path.join(tmp, `cfg-${n++}`);
  fs.mkdirSync(path.join(root, outRel), { recursive: true });
  if (prepare) prepare(root);
  const { createSink, createMasker } = await loadOut();
  const sink = createSink({ masker: createMasker({ secrets: [] }), fs, root, stdout: { write() {} }, stderr: { write() {} } });
  const { writeSensitiveArtifacts } = await load();
  const result = await writeSensitiveArtifacts({ sink, fs, root, outRel, art });
  return { root, outDir: path.join(root, outRel), result };
}
const modeOf = (p) => fs.statSync(p).mode & 0o777;
const TRACE = Buffer.from([0x50, 0x4b, 3, 4, 0, 255, 1, 2]);
const STATE = Buffer.from('{"cookies":[{"name":"sid","value":"s3cr3t"}]}');

test('trace i storageState: zapis bajt w bajt, tryb 0600, .gitignore z dwiema nazwami, ostrzezenie z zawartoscia i granica', async () => {
  const { outDir, result } = await setup({ trace: TRACE, storageState: STATE });
  assert.deepEqual(fs.readFileSync(path.join(outDir, 'trace.zip')), TRACE);
  assert.deepEqual(fs.readFileSync(path.join(outDir, 'storageState.json')), STATE);
  if (POSIX) {
    assert.equal(modeOf(path.join(outDir, 'trace.zip')), 0o600);
    assert.equal(modeOf(path.join(outDir, 'storageState.json')), 0o600);
  }
  assert.equal(fs.readFileSync(path.join(outDir, '.gitignore'), 'utf8'), 'trace.zip\nstorageState.json\n');
  assert.deepEqual(result.written, ['trace.zip', 'storageState.json']);
  assert.equal(result.gitignore, 'created');
  const w = result.warning;
  assert.match(w, /^capture: warning: --allow-trace wrote trace\.zip and storageState\.json to out/);
  for (const part of [/form field values/i, /passwords/i, /headers and bodies/i, /cookies and tokens/i, /screen frames/i, /does NOT cover/, /absence of a known secret value proves nothing/i, /Do not commit or share/i, /created out[\\/]\.gitignore/i, /0600/, /Windows/]) {
    assert.match(w, part);
  }
  assert.doesNotMatch(w, /safe|redact(ed)?\b|masked\b(?! by)/i, 'zadnej obietnicy bezpieczenstwa');
});

test('plik, ktory juz istnial z szerokimi uprawnieniami, ma po zapisie 0600', { skip: !POSIX }, async () => {
  const { outDir } = await setup({ trace: TRACE }, { prepare: (root) => {
    const f = path.join(root, 'out', 'trace.zip');
    fs.writeFileSync(f, 'old', { mode: 0o644 });
    fs.chmodSync(f, 0o644);
  } });
  assert.equal(modeOf(path.join(outDir, 'trace.zip')), 0o600);
  assert.deepEqual(fs.readFileSync(path.join(outDir, 'trace.zip')), TRACE);
});

test('istniejacy .gitignore nie jest zmieniany (tresc i czas), a ostrzezenie mowi, ze go nie ruszono', async () => {
  const original = '*.log\n';
  const { outDir, result } = await setup({ trace: TRACE }, { prepare: (root) => fs.writeFileSync(path.join(root, 'out', '.gitignore'), original) });
  assert.equal(fs.readFileSync(path.join(outDir, '.gitignore'), 'utf8'), original);
  assert.equal(result.gitignore, 'exists');
  assert.match(result.warning, /out[\\/]\.gitignore already exists and was not changed/i);
  assert.match(result.warning, /trace\.zip and storageState\.json/);
});

test('.gitignore bedacy dowiazaniem symbolicznym tez nie jest dotykany (nic nie jest zapisywane przez dowiazanie)', { skip: !POSIX }, async () => {
  let target;
  const { outDir, result } = await setup({ trace: TRACE }, { prepare: (root) => {
    target = path.join(root, 'elsewhere.txt');
    fs.writeFileSync(target, 'keep\n');
    fs.symlinkSync(target, path.join(root, 'out', '.gitignore'));
  } });
  assert.equal(fs.readFileSync(target, 'utf8'), 'keep\n');
  assert.ok(fs.lstatSync(path.join(outDir, '.gitignore')).isSymbolicLink());
  assert.equal(result.gitignore, 'exists');
});

test('outDir rowny katalogowi konfiguracji (outRel pusty): .gitignore nie jest zakladany, ostrzezenie prosi o reczny wpis', async () => {
  const { root, result } = await setup({ trace: TRACE, storageState: STATE }, { outRel: '' });
  assert.ok(!fs.existsSync(path.join(root, '.gitignore')));
  assert.equal(result.gitignore, 'skipped');
  assert.match(result.warning, /to \./);
  assert.match(result.warning, /no \.gitignore was created/i);
  assert.match(result.warning, /add trace\.zip and storageState\.json to \.gitignore/i);
});

test('tylko trace (bez zalogowania): powstaje trace.zip, nie storageState.json, a .gitignore wymienia oba pliki', async () => {
  const { outDir, result } = await setup({ trace: TRACE });
  assert.deepEqual(fs.readdirSync(outDir).sort(), ['.gitignore', 'trace.zip']);
  assert.deepEqual(result.written, ['trace.zip']);
  assert.equal(fs.readFileSync(path.join(outDir, '.gitignore'), 'utf8'), 'trace.zip\nstorageState.json\n');
  assert.match(result.warning, /wrote trace\.zip to out/);
});

test('brak artefaktow: nic nie jest zapisane, brak .gitignore i brak ostrzezenia', async () => {
  for (const art of [{}, { trace: null, storageState: null }, { trace: Buffer.alloc(0) }]) {
    const { outDir, result } = await setup(art);
    assert.deepEqual(fs.readdirSync(outDir), [], JSON.stringify(art));
    assert.deepEqual(result.written, []);
    assert.equal(result.warning, null);
    assert.equal(result.gitignore, 'none');
  }
});

test('sink.writeFile z opcja mode: chmod PRZED zapisem (istniejacy plik) i po zapisie; ENOENT przed zapisem jest ignorowany; bez opcji bez chmod', async () => {
  const { createSink, createMasker } = await loadOut();
  const calls = [];
  const enoent = () => { const e = new Error('ENOENT'); e.code = 'ENOENT'; return e; };
  const existing = new Set();
  const fakeFs = {
    realpathSync: (p) => p, lstatSync: () => { throw enoent(); },
    promises: {
      async mkdir() {}, async realpath(p) { return p; },
      async lstat() { throw enoent(); },
      async writeFile(p, d, o) { calls.push(['write', path.basename(p), o]); existing.add(p); },
      async chmod(p, m) { calls.push(['chmod', path.basename(p), m]); if (!existing.has(p)) throw enoent(); },
    },
  };
  const sink = createSink({ masker: createMasker({ secrets: [] }), fs: fakeFs, root: path.join(tmp, 'fake-root'), stdout: { write() {} }, stderr: { write() {} } });
  await sink.writeFile('a.bin', Buffer.from('x'), { mode: 0o600 });   // nowy plik: chmod przed zapisem da ENOENT (ignorowany)
  await sink.writeFile('a.bin', Buffer.from('y'), { mode: 0o600 });   // istniejacy: chmod przed zapisem dziala
  await sink.writeFile('b.txt', 'y');
  assert.deepEqual(calls, [
    ['chmod', 'a.bin', 0o600], ['write', 'a.bin', { mode: 0o600 }], ['chmod', 'a.bin', 0o600],
    ['chmod', 'a.bin', 0o600], ['write', 'a.bin', { mode: 0o600 }], ['chmod', 'a.bin', 0o600],
    ['write', 'b.txt', undefined],
  ]);
});

test('sink.writeFile: inny blad chmod przed zapisem (np. EPERM) przerywa zapis zanim wrazliwa tresc trafi do pliku', async () => {
  const { createSink, createMasker } = await loadOut();
  const written = [];
  const fakeFs = {
    realpathSync: (p) => p, lstatSync: () => { const e = new Error('ENOENT'); e.code = 'ENOENT'; throw e; },
    promises: {
      async mkdir() {}, async realpath(p) { return p; },
      async lstat() { const e = new Error('ENOENT'); e.code = 'ENOENT'; throw e; },
      async writeFile(p, d) { written.push(p); },
      async chmod() { const e = new Error('EPERM'); e.code = 'EPERM'; throw e; },
    },
  };
  const sink = createSink({ masker: createMasker({ secrets: [] }), fs: fakeFs, root: path.join(tmp, 'fake-root2'), stdout: { write() {} }, stderr: { write() {} } });
  await assert.rejects(sink.writeFile('a.bin', Buffer.from('secret'), { mode: 0o600 }), (e) => e.code === 'EPERM');
  assert.deepEqual(written, []);
});

test('sink.writeFile z flag wx nie nadpisuje istniejacego pliku (EEXIST), a .gitignore z wyscigu zostaje nietkniety', async () => {
  const { outDir, result } = await setup({ trace: TRACE }, { prepare: () => {} });
  assert.equal(result.gitignore, 'created');
  // druga proba na tym samym outDir: .gitignore juz jest, tresc nie jest nadpisywana
  fs.writeFileSync(path.join(outDir, '.gitignore'), 'zmienione-przez-uzytkownika\n');
  const { createSink, createMasker } = await loadOut();
  const { writeSensitiveArtifacts } = await load();
  const sink = createSink({ masker: createMasker({ secrets: [] }), fs, root: path.dirname(outDir), stdout: { write() {} }, stderr: { write() {} } });
  const again = await writeSensitiveArtifacts({ sink, fs, root: path.dirname(outDir), outRel: path.basename(outDir), art: { trace: TRACE } });
  assert.equal(again.gitignore, 'exists');
  assert.equal(fs.readFileSync(path.join(outDir, '.gitignore'), 'utf8'), 'zmienione-przez-uzytkownika\n');
  await assert.rejects(sink.writeFile(path.join(path.basename(outDir), '.gitignore'), 'x', { flag: 'wx' }), (e) => e.code === 'EEXIST');
});

test('blad zapisu drugiego artefaktu po zapisie pierwszego: ostrzezenie o pliku, ktory juz lezy na dysku, i wyjatek leci dalej', async () => {
  const { writeSensitiveArtifacts } = await load();
  const stderr = [];
  const sink = {
    async writeFile(rel) { if (rel.endsWith('storageState.json')) throw Object.assign(new Error('disk full'), { code: 'ENOSPC' }); },
    async write(stream, text) { if (stream === 'stderr') stderr.push(text); },
  };
  await assert.rejects(writeSensitiveArtifacts({ sink, fs, root: tmp, outRel: 'out', art: { trace: TRACE, storageState: STATE } }), (e) => e.code === 'ENOSPC');
  assert.equal(stderr.length, 1);
  assert.match(stderr[0], /trace\.zip was already written to out before an error stopped --allow-trace/);
  assert.match(stderr[0], /Do not commit or share it/);
  // blad PIERWSZEGO zapisu: nic nie lezy na dysku, wiec bez ostrzezenia
  const stderr2 = [];
  const failing = { async writeFile() { throw new Error('nope'); }, async write(s, t) { stderr2.push(t); } };
  await assert.rejects(writeSensitiveArtifacts({ sink: failing, fs, root: tmp, outRel: 'out', art: { trace: TRACE } }), /nope/);
  assert.deepEqual(stderr2, []);
});
