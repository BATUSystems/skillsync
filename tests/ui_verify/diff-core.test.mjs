// Czysta logika lib/diff-core.mjs (S5): compareShotSets z wstrzykiwanym readManifest/readPng/diffFn i syntetycznymi obrazami.
import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const LIB = path.join(HERE, '..', '..', 'library', 'skills', 'ui-verify', 'scripts', 'lib');
const lib = (name) => import(pathToFileURL(path.join(LIB, name)).href);

const { compareShotSets, formatDiffReport } = await lib('diff-core.mjs');
const { diffImages } = await lib('pixels.mjs');
const { createMasker } = await lib('out.mjs');

const WHITE = [255, 255, 255, 255];
const BLACK = [0, 0, 0, 255];
function solid(w, h, fn = () => WHITE) {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) data.set(fn(x, y), (y * w + x) * 4);
  return { width: w, height: h, data };
}
const blob = (x0) => solid(40, 40, (x, y) => (x >= x0 && x < x0 + 12 && y >= 4 && y < 36 ? BLACK : WHITE));

const entry = (name, assertion = 'PASS') => ({ name, pageId: name.split('__')[0], width: 375, mode: 'light', assertion });
// sets: { dirName: { manifest: [entries] | null, images: { name: img } } }
function run(sets, over = {}) {
  const readCalls = [];
  const out = compareShotSets({
    before: 'before',
    after: 'after',
    diffFn: diffImages,
    readManifest: (dir) => (sets[dir].manifest ? { version: 1, shots: sets[dir].manifest } : null),
    readPng: (dir, name) => { readCalls.push([dir, name]); return sets[dir].images[name]; },
    maxPct: 0.5,
    ...over,
  });
  return { out, readCalls };
}
const verdictOf = (out, name) => out.entries.find((e) => e.name === name)?.verdict;

test('SAME tylko gdy oba zrzuty maja asercje PASS i obrazy rowne => PASS, exit 0', () => {
  const { out } = run({
    before: { manifest: [entry('home__375-light.png')], images: { 'home__375-light.png': blob(4) } },
    after: { manifest: [entry('home__375-light.png')], images: { 'home__375-light.png': blob(4) } },
  });
  assert.equal(verdictOf(out, 'home__375-light.png'), 'SAME');
  assert.equal(out.verdict, 'PASS');
  assert.equal(out.exit, 0);
});

test('rozne obrazy => DIFFERENT z pct > 0, FAIL, exit 1', () => {
  const { out } = run({
    before: { manifest: [entry('home__375-light.png')], images: { 'home__375-light.png': blob(4) } },
    after: { manifest: [entry('home__375-light.png')], images: { 'home__375-light.png': blob(24) } },
  });
  const e = out.entries[0];
  assert.equal(e.verdict, 'DIFFERENT');
  assert.ok(e.pct > 0.5, `pct ${e.pct}`);
  assert.equal(out.verdict, 'FAIL');
  assert.equal(out.exit, 1);
});

test('brak asercji PASS po jednej ze stron => UNVERIFIED (no-positive-assertion), nigdy SAME, exit 3 - nawet przy identycznych obrazach', () => {
  for (const [a, b] of [['NONE', 'PASS'], ['PASS', 'NONE'], ['FAIL', 'PASS'], ['NONE', 'NONE']]) {
    const { out } = run({
      before: { manifest: [entry('p__375-light.png', a)], images: { 'p__375-light.png': blob(4) } },
      after: { manifest: [entry('p__375-light.png', b)], images: { 'p__375-light.png': blob(4) } },
    });
    assert.equal(verdictOf(out, 'p__375-light.png'), 'UNVERIFIED', `${a}/${b}`);
    assert.equal(out.entries[0].reason, 'no-positive-assertion');
    assert.equal(out.verdict, 'UNVERIFIED');
    assert.equal(out.exit, 3);
  }
});

test('rozmiary rozne => DIFFERENT (size-mismatch), FAIL', () => {
  const { out } = run({
    before: { manifest: [entry('p__375-light.png')], images: { 'p__375-light.png': solid(40, 40) } },
    after: { manifest: [entry('p__375-light.png')], images: { 'p__375-light.png': solid(40, 60) } },
  });
  assert.equal(out.entries[0].verdict, 'DIFFERENT');
  assert.equal(out.entries[0].reason, 'size-mismatch');
  assert.equal(out.exit, 1);
});

test('brak manifestu po ktorejkolwiek stronie => UNVERIFIED (no-manifest), exit 3, bez czytania PNG', () => {
  for (const missing of ['before', 'after']) {
    const sets = {
      before: { manifest: [entry('p__375-light.png')], images: { 'p__375-light.png': blob(4) } },
      after: { manifest: [entry('p__375-light.png')], images: { 'p__375-light.png': blob(4) } },
    };
    sets[missing].manifest = null;
    const { out, readCalls } = run(sets);
    assert.equal(out.verdict, 'UNVERIFIED', missing);
    assert.equal(out.exit, 3);
    assert.ok(out.entries.some((e) => e.reason === 'no-manifest') || out.reason === 'no-manifest', JSON.stringify(out));
    assert.deepEqual(readCalls, []);
  }
});

test('nazwa tylko w before => MISSING, wynik FAIL exit 1; tylko w after => ADDED, nie psuje wyniku', () => {
  const both = { 'a__375-light.png': blob(4) };
  const missing = run({
    before: { manifest: [entry('a__375-light.png'), entry('gone__375-light.png')], images: { ...both, 'gone__375-light.png': blob(4) } },
    after: { manifest: [entry('a__375-light.png')], images: both },
  }).out;
  assert.equal(verdictOf(missing, 'gone__375-light.png'), 'MISSING');
  assert.equal(missing.verdict, 'FAIL');
  assert.equal(missing.exit, 1);
  const added = run({
    before: { manifest: [entry('a__375-light.png')], images: both },
    after: { manifest: [entry('a__375-light.png'), entry('new__375-light.png')], images: { ...both, 'new__375-light.png': blob(4) } },
  }).out;
  assert.equal(verdictOf(added, 'new__375-light.png'), 'ADDED');
  assert.equal(added.verdict, 'PASS');
  assert.equal(added.exit, 0);
});

test('priorytet laczny: DIFFERENT/MISSING > UNVERIFIED > PASS', () => {
  const imgs = (n) => ({ [n]: blob(4) });
  const { out } = run({
    before: { manifest: [entry('u__375-light.png', 'NONE'), entry('d__375-light.png')], images: { ...imgs('u__375-light.png'), 'd__375-light.png': blob(4) } },
    after: { manifest: [entry('u__375-light.png', 'NONE'), entry('d__375-light.png')], images: { ...imgs('u__375-light.png'), 'd__375-light.png': blob(24) } },
  });
  assert.equal(verdictOf(out, 'u__375-light.png'), 'UNVERIFIED');
  assert.equal(verdictOf(out, 'd__375-light.png'), 'DIFFERENT');
  assert.equal(out.verdict, 'FAIL');
  assert.equal(out.exit, 1);
});

test('pusty zestaw po obu stronach nie daje PASS (brak dowodu)', () => {
  const { out } = run({ before: { manifest: [], images: {} }, after: { manifest: [], images: {} } });
  assert.notEqual(out.verdict, 'PASS');
  assert.notEqual(out.exit, 0);
});

test('maxPct przekazany do diffFn: tolerancja progu', () => {
  const seen = [];
  const diffFn = (a, b, opts) => { seen.push(opts); return diffImages(a, b, opts); };
  run({
    before: { manifest: [entry('p__375-light.png')], images: { 'p__375-light.png': blob(4) } },
    after: { manifest: [entry('p__375-light.png')], images: { 'p__375-light.png': blob(4) } },
  }, { diffFn, maxPct: 1.25 });
  assert.equal(seen.length, 1);
  assert.equal(seen[0].maxPct, 1.25);
});

test('formatDiffReport: nazwy i werdykty, sekret w nazwie maskowany (kontrola bez maskera)', () => {
  const secret = ['fake', 'secret', String(process.pid), 'abcd'].join('-');
  const name = `p-${secret}__375-light.png`;
  const { out } = run({
    before: { manifest: [entry(name)], images: { [name]: blob(4) } },
    after: { manifest: [entry(name)], images: { [name]: blob(24) } },
  });
  const masked = formatDiffReport(out, createMasker({ secrets: [secret] }));
  assert.ok(!masked.includes(secret), masked);
  assert.match(masked, /DIFFERENT/);
  assert.ok(formatDiffReport(out, createMasker()).includes(secret), 'kontrola: bez maskera nazwa jest w raporcie');
});
