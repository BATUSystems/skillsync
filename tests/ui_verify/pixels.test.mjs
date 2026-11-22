import test from 'node:test';
import assert from 'node:assert/strict';
import {
  compositeOnBackground, cropToCommon, applyMasks, flagDifferences, diffImages,
} from '../../library/skills/ui-verify/scripts/lib/pixels.mjs';

function solid(width, height, [r, g, b, a] = [255, 255, 255, 255]) {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let i = 0; i < width * height; i++) data.set([r, g, b, a], i * 4);
  return { width, height, data };
}
function setPx(img, x, y, [r, g, b, a = 255]) { img.data.set([r, g, b, a], (y * img.width + x) * 4); }
function getPx(img, x, y) { const o = (y * img.width + x) * 4; return Array.from(img.data.slice(o, o + 4)); }
function vline(width, height, x) {
  const img = solid(width, height);
  for (let y = 0; y < height; y++) setPx(img, x, y, [0, 0, 0]);
  return img;
}

test('compositeOnBackground: alfa nad tlem, wynik nieprzezroczysty, wejscie bez zmian', () => {
  const img = solid(2, 1, [255, 0, 0, 128]);
  const before = Array.from(img.data);
  const out = compositeOnBackground(img);
  assert.equal(out.width, 2);
  assert.equal(out.height, 1);
  assert.equal(getPx(out, 0, 0)[3], 255);
  const [r, g, b] = getPx(out, 0, 0);
  assert.equal(r, 255);
  assert.ok(Math.abs(g - 127) <= 1 && Math.abs(b - 127) <= 1, `g=${g} b=${b}`);
  assert.deepEqual(Array.from(img.data), before);
  assert.notEqual(out, img);
  assert.notEqual(out.data, img.data);
});

test('compositeOnBackground: wlasne tlo i pelna przezroczystosc', () => {
  const out = compositeOnBackground(solid(1, 1, [10, 20, 30, 0]), { r: 1, g: 2, b: 3 });
  assert.deepEqual(getPx(out, 0, 0), [1, 2, 3, 255]);
});

test('cropToCommon: wspolne minimum zakotwiczone w lewym gornym rogu', () => {
  const a = solid(4, 3);
  setPx(a, 1, 1, [9, 9, 9]);
  const b = solid(2, 5);
  const r = cropToCommon(a, b);
  assert.equal(r.cropped, true);
  assert.deepEqual([r.a.width, r.a.height, r.b.width, r.b.height], [2, 3, 2, 3]);
  assert.equal(r.a.data.length, 2 * 3 * 4);
  assert.deepEqual(getPx(r.a, 1, 1), [9, 9, 9, 255]);
});

test('cropToCommon: rowne wymiary => cropped false', () => {
  const r = cropToCommon(solid(3, 3), solid(3, 3));
  assert.equal(r.cropped, false);
  assert.equal(r.a.width, 3);
});

test('applyMasks: zamalowuje #ff00ff, nowy obraz', () => {
  const img = solid(5, 5);
  const out = applyMasks(img, [{ x: 1, y: 1, width: 2, height: 2 }]);
  assert.deepEqual(getPx(out, 1, 1), [255, 0, 255, 255]);
  assert.deepEqual(getPx(out, 2, 2), [255, 0, 255, 255]);
  assert.deepEqual(getPx(out, 3, 3), [255, 255, 255, 255]);
  assert.deepEqual(getPx(out, 0, 0), [255, 255, 255, 255]);
  assert.deepEqual(getPx(img, 1, 1), [255, 255, 255, 255]);
});

test('applyMasks: maska wychodzaca poza obraz jest przycinana, calkiem poza nic nie robi', () => {
  const out = applyMasks(solid(4, 4), [{ x: -2, y: 3, width: 4, height: 10 }, { x: 50, y: 50, width: 5, height: 5 }]);
  assert.deepEqual(getPx(out, 0, 3), [255, 0, 255, 255]);
  assert.deepEqual(getPx(out, 1, 3), [255, 0, 255, 255]);
  assert.deepEqual(getPx(out, 2, 3), [255, 255, 255, 255]);
  assert.deepEqual(getPx(out, 0, 2), [255, 255, 255, 255]);
  assert.equal(out.data.length, 4 * 4 * 4);
});

test('applyMasks: ujemne wymiary => blad', () => {
  assert.throws(() => applyMasks(solid(4, 4), [{ x: 0, y: 0, width: -1, height: 2 }]));
  assert.throws(() => applyMasks(solid(4, 4), [{ x: 0, y: 0, width: 2, height: -3 }]));
});

test('flagDifferences: 0/1 na piksel, channelTolerance', () => {
  const a = solid(3, 1);
  const b = solid(3, 1);
  setPx(b, 1, 0, [250, 255, 255]);
  setPx(b, 2, 0, [255, 255, 255, 200]);
  assert.deepEqual(Array.from(flagDifferences(a, b)), [0, 1, 1]);
  assert.deepEqual(Array.from(flagDifferences(a, b, { channelTolerance: 5 })), [0, 0, 1]);
  assert.deepEqual(Array.from(flagDifferences(a, b, { channelTolerance: 55 })), [0, 0, 0]);
});

test('flagDifferences: rozne wymiary => wyjatek', () => {
  assert.throws(() => flagDifferences(solid(2, 2), solid(3, 2)));
});

test('diffImages: identyczne => PASS 0%', () => {
  const r = diffImages(solid(10, 10), solid(10, 10));
  assert.equal(r.verdict, 'PASS');
  assert.equal(r.pct, 0);
  assert.equal(r.diffCount, 0);
  assert.equal(r.total, 100);
  assert.equal(r.sizeMismatch, false);
  assert.equal(r.cropped, false);
});

test('diffImages: rozne wymiary => FAIL size-mismatch bez normalizacji', () => {
  const r = diffImages(solid(10, 10), solid(10, 12));
  assert.equal(r.verdict, 'FAIL');
  assert.equal(r.sizeMismatch, true);
  assert.equal(r.pct, 100);
  assert.equal(r.diffCount, 0);
  assert.equal(r.total, 0);
});

test('diffImages: cropToCommon => przyciecie i cropped true', () => {
  const a = solid(10, 10);
  const b = solid(10, 12);
  setPx(b, 0, 11, [0, 0, 0]); // poza wspolnym obszarem
  const r = diffImages(a, b, { cropToCommon: true });
  assert.equal(r.cropped, true);
  assert.equal(r.sizeMismatch, false);
  assert.equal(r.verdict, 'PASS');
  assert.equal(r.total, 100);
});

test('diffImages: alfa jest kompozytowana na tle przed porownaniem', () => {
  const r = diffImages(solid(5, 5, [255, 255, 255, 0]), solid(5, 5, [255, 255, 255, 255]));
  assert.equal(r.verdict, 'PASS');
  assert.equal(r.diffCount, 0);
});

test('diffImages: maski wycinaja roznice z obu stron', () => {
  const a = solid(20, 20);
  const b = solid(20, 20);
  for (let y = 5; y < 10; y++) for (let x = 5; x < 10; x++) setPx(b, x, y, [0, 0, 0]);
  assert.equal(diffImages(a, b).verdict, 'FAIL');
  const masked = diffImages(a, b, { masks: [{ x: 4, y: 4, width: 7, height: 7 }] });
  assert.equal(masked.verdict, 'PASS');
  assert.equal(masked.diffCount, 0);
});

test('diffImages: odpornosc 1 px - linia pionowa przesunieta o 1 px => PASS', () => {
  const r = diffImages(vline(100, 100, 50), vline(100, 100, 51));
  assert.equal(r.verdict, 'PASS', JSON.stringify(r));
  assert.ok(r.pct <= 0.5);
});

test('diffImages: linia przesunieta o 4 px => FAIL', () => {
  const r = diffImages(vline(100, 100, 50), vline(100, 100, 54));
  assert.equal(r.verdict, 'FAIL', JSON.stringify(r));
  assert.ok(r.pct > 0.5);
  assert.ok(r.diffCount > 0);
});

test('diffImages: tolerate1px false => przesuniecie o 1 px jest roznica', () => {
  const r = diffImages(vline(100, 100, 50), vline(100, 100, 51), { tolerate1px: false });
  assert.equal(r.verdict, 'FAIL');
  assert.equal(r.diffCount, 200);
  assert.equal(r.pct, 2);
});

test('diffImages: pojedynczy piksel w 100x100 przy maxPct 0.5 => PASS (0.01%)', () => {
  const a = solid(100, 100);
  const b = solid(100, 100);
  setPx(b, 40, 40, [0, 0, 0]);
  const r = diffImages(a, b, { maxPct: 0.5 });
  assert.equal(r.verdict, 'PASS');
  assert.equal(r.diffCount, 1);
  assert.equal(r.total, 10000);
  assert.ok(Math.abs(r.pct - 0.01) < 1e-9);
});

test('diffImages: prog maxPct (domyslnie 0.5, granica wlacznie)', () => {
  const a = solid(10, 10);
  const b = solid(10, 10);
  setPx(b, 5, 5, [0, 0, 0]); // 1%
  assert.equal(diffImages(a, b).verdict, 'FAIL');
  assert.equal(diffImages(a, b, { maxPct: 0.5 }).verdict, 'FAIL');
  assert.equal(diffImages(a, b, { maxPct: 1 }).verdict, 'PASS');
  assert.equal(diffImages(a, b, { maxPct: 2 }).verdict, 'PASS');
});

test('diffImages: wstrzykniety flagger jest wolany na obrazach tej samej wielkosci', () => {
  const calls = [];
  const a = solid(10, 10);
  const b = solid(10, 10);
  const allOnes = (x, y) => { calls.push([x.width, x.height, y.width, y.height]); return new Uint8Array(100).fill(1); };
  const r = diffImages(a, b, { flagger: allOnes, tolerate1px: false });
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], [10, 10, 10, 10]);
  assert.equal(r.verdict, 'FAIL');
  assert.equal(r.diffCount, 100);
  assert.equal(r.pct, 100);
  const none = diffImages(a, b, { flagger: () => new Uint8Array(100), tolerate1px: false });
  assert.equal(none.verdict, 'PASS');
  assert.equal(none.diffCount, 0);
});

test('diffImages: channelTolerance wycisza drobne roznice kanalow', () => {
  const a = solid(10, 10, [100, 100, 100, 255]);
  const b = solid(10, 10, [103, 100, 100, 255]);
  assert.equal(diffImages(a, b).verdict, 'FAIL');
  assert.equal(diffImages(a, b, { channelTolerance: 3 }).verdict, 'PASS');
});
