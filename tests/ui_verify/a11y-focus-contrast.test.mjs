// Focus indicator contrast and area (CORE-1150-T2B, A1; spec P14, P20, P25; criterion K2): pure logic on synthetic images.
// No browser, no clock, no files. The flaggers are small stand-ins for pixelmatch (strict skips marked antialiasing pixels).
import test from 'node:test';
import assert from 'node:assert/strict';
import { FOCUS_MIN_RATIO, effectiveThreshold, focusMetrics, requiredArea } from '../../library/skills/ui-verify/scripts/lib/a11y-focus-contrast.mjs';
import { classifyKeyboard } from '../../library/skills/ui-verify/scripts/lib/a11y-focus.mjs';
import { contrastRatio } from '../../library/skills/ui-verify/scripts/lib/a11y-contrast.mjs';
import { keyboardFinding, measureFocusStep } from '../../library/skills/ui-verify/scripts/lib/a11y-page.mjs';

const WHITE = { r: 255, g: 255, b: 255 };
const gray = (v) => ({ r: v, g: v, b: v });

function image(width, height, paint = () => null) {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const c = paint(x, y) || WHITE;
      data.set([c.r, c.g, c.b, c.a === undefined ? 255 : c.a], (y * width + x) * 4);
    }
  }
  return { width, height, data };
}

const differs = (a, b, i) => [0, 1, 2, 3].some((c) => a.data[i * 4 + c] !== b.data[i * 4 + c]);
// antialiased: indices which the strict flagger ignores (as pixelmatch with includeAA:false does).
function flaggers(antialiased = new Set()) {
  const make = (skip) => (a, b) => Uint8Array.from({ length: a.width * a.height }, (_, i) => (differs(a, b, i) && !skip.has(i) ? 1 : 0));
  return { flaggerStrict: make(antialiased), flaggerAA: make(new Set()) };
}

// Is the point inside a rounded rectangle at (x0, y0)?
function inRounded(px, py, x0, y0, w, h, r) {
  if (w <= 0 || h <= 0 || px < x0 || py < y0 || px > x0 + w || py > y0 + h) return false;
  const dx = Math.max(x0 + r - px, 0, px - (x0 + w - r));
  const dy = Math.max(y0 + r - py, 0, py - (y0 + h - r));
  return dx * dx + dy * dy <= r * r;
}

// A ring of the given thickness inside an element of w x h with corner radius r, rasterised by pixel centres.
function ringImages(w, h, radius, thickness, color = gray(0)) {
  const r = Math.min(radius, Math.min(w, h) / 2);
  const ring = (x, y) => inRounded(x + 0.5, y + 0.5, 0, 0, w, h, r)
    && !inRounded(x + 0.5, y + 0.5, thickness, thickness, w - 2 * thickness, h - 2 * thickness, Math.max(0, r - thickness));
  return { before: image(w, h), after: image(w, h, (x, y) => (ring(x, y) ? color : null)) };
}

// An outline of the given thickness around a w x h element placed pad pixels from the clip edge.
function outline(w, h, thickness, color, pad = 8) {
  const paint = (x, y) => {
    const inOuter = x >= pad - thickness && x < pad + w + thickness && y >= pad - thickness && y < pad + h + thickness;
    const inElement = x >= pad && x < pad + w && y >= pad && y < pad + h;
    return inOuter && !inElement ? color : null;
  };
  return { before: image(w + 2 * pad, h + 2 * pad), after: image(w + 2 * pad, h + 2 * pad, paint) };
}

const near = (actual, expected, tolerance = 0.05) => assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected} (+-${tolerance})`);

// ---------- required area and threshold (P25) ----------

// [name, box, required area, effective threshold, analytic area of a 2 px inner ring, of a 1 px inner ring]
const P25 = [
  ['pill 100x40 r=20', { width: 100, height: 40, borderRadius: 20 }, 491.33, 450.8, 478.8, 242.5],
  ['100x40 r=12', { width: 100, height: 40, borderRadius: 12 }, 518.8, 476.9, 506.2, 256.3],
  ['circle 24x24 r=12', { width: 24, height: 24, borderRadius: 12 }, 150.8, 127.3, 138.2, 72.3],
  ['square 24x24 r=0', { width: 24, height: 24, borderRadius: 0 }, 192, 166.4, 176, 92],
];

for (const [name, box, area, threshold, two, one] of P25) {
  test(`P25 ${name}: wymagane pole i prog efektywny`, () => {
    near(requiredArea(box), area);
    near(effectiveThreshold(requiredArea(box)), threshold);
    assert.ok(two >= effectiveThreshold(requiredArea(box)) && one < effectiveThreshold(requiredArea(box)));
  });

  test(`P25 ${name}: pierscien 2 px wewnatrz przechodzi, 1 px nie`, () => {
    for (const [thickness, analytic, passes] of [[2, two, true], [1, one, false]]) {
      const { before, after } = ringImages(box.width, box.height, box.borderRadius, thickness);
      const m = focusMetrics(flaggers(), before, after, box);
      // sanity of the synthetic ring only: a hard-edged raster of a curve is off the analytic area (260 for 242.5, 64 for 72.3)
      near(m.qualifyingAA, analytic, analytic * 0.12);
      assert.equal(m.qualifyingAA >= m.threshold, passes, `${thickness} px: ${m.qualifyingAA} vs ${m.threshold}`);
    }
  });
}

test('P25: bez borderRadius jak r=0, promien obciety do polowy krotszego boku', () => {
  assert.equal(requiredArea({ width: 100, height: 40 }), 560);
  assert.equal(requiredArea({ width: 100, height: 40, borderRadius: 50 }), requiredArea({ width: 100, height: 40, borderRadius: 20 }));
});

test('P25: prog efektywny nie schodzi ponizej zera', () => {
  assert.equal(effectiveThreshold(8), 0);
  assert.equal(effectiveThreshold(0), 0);
});

test('P25: brak prostokata albo zly promien to null, nie liczba', () => {
  for (const box of [undefined, null, {}, { width: 0, height: 10 }, { width: 10, height: -1 }, { width: NaN, height: 10 }, { width: '10', height: 10 },
    { width: 10, height: 10, borderRadius: -1 }, { width: 10, height: 10, borderRadius: NaN }, { width: 10, height: 10, borderRadius: '4px' }]) {
    assert.equal(requiredArea(box), null, JSON.stringify(box));
  }
  assert.equal(effectiveThreshold(null), null);
});

// ---------- metrics (K2) ----------

const BOX = { width: 24, height: 24, borderRadius: 0 };

test('K2: pierscien o kontrascie >= 3: wszystkie zmienione piksele sie licza', () => {
  const { before, after } = outline(24, 24, 2, gray(0));
  const m = focusMetrics(flaggers(), before, after, BOX);
  assert.equal(m.diffPx, 208);
  assert.equal(m.qualifyingStrict, 208);
  assert.equal(m.qualifyingAA, 208);
  near(m.ratioMedian, 21, 1e-9);
  near(m.ratioMin, 21, 1e-9);
  assert.equal(m.requiredArea, 192);
  near(m.threshold, 166.4, 1e-9);
});

test('K2: blady pierscien >= 4 px zmienionych, ale ponizej 3: widoczny dla starej reguly, nic sie nie kwalifikuje', () => {
  const { before, after } = outline(24, 24, 2, gray(204));
  const m = focusMetrics(flaggers(), before, after, BOX);
  assert.equal(m.diffPx, 208);
  assert.equal(classifyKeyboard({ diffPx: m.diffPx }).verdict, 'PASS');
  assert.deepEqual([m.qualifyingStrict, m.qualifyingAA], [0, 0]);
  near(m.ratioMedian, contrastRatio(WHITE, gray(204)), 1e-9);
});

test('K2: prog 3.0 w pelnej arytmetyce (148 przechodzi, 149 nie)', () => {
  assert.equal(FOCUS_MIN_RATIO, 3);
  for (const [value, counted] of [[148, 208], [149, 0]]) {
    const { before, after } = outline(24, 24, 2, gray(value));
    assert.equal(focusMetrics(flaggers(), before, after, BOX).qualifyingStrict, counted, String(value));
  }
});

test('K2: kontrast liczony miedzy stanem przed i po, nie wobec bieli', () => {
  const before = image(10, 1, () => gray(0));
  const after = image(10, 1, (x) => (x < 4 ? gray(60) : (x < 6 ? WHITE : gray(0))));
  const m = focusMetrics(flaggers(), before, after, BOX);
  assert.deepEqual([m.diffPx, m.qualifyingStrict], [6, 2]);
  near(m.ratioMin, contrastRatio(gray(0), gray(60)), 1e-9);
});

test('K2: mediana (parzysta i nieparzysta liczba) i minimum po zmienionych pikselach', () => {
  const values = [0, 100, 204, 230];
  const before = image(5, 1);
  const even = focusMetrics(flaggers(), before, image(5, 1, (x) => (x < 4 ? gray(values[x]) : null)), BOX);
  near(even.ratioMedian, (contrastRatio(WHITE, gray(100)) + contrastRatio(WHITE, gray(204))) / 2, 1e-9);
  near(even.ratioMin, contrastRatio(WHITE, gray(230)), 1e-9);
  const odd = focusMetrics(flaggers(), before, image(5, 1, (x) => (x < 3 ? gray(values[x]) : null)), BOX);
  near(odd.ratioMedian, contrastRatio(WHITE, gray(100)), 1e-9);
});

test('K2: brak zmiany: zera, stosunki null, tylko stara regula daje FAIL', () => {
  const same = image(20, 20);
  const m = focusMetrics(flaggers(), same, image(20, 20), BOX);
  assert.deepEqual([m.diffPx, m.qualifyingStrict, m.qualifyingAA, m.ratioMedian, m.ratioMin], [0, 0, 0, null, null]);
  assert.equal(classifyKeyboard({ diffPx: m.diffPx }).verdict, 'FAIL');
});

test('K2: antyaliasing: strict pomija piksele AA, AA liczy te o kontrascie >= 3', () => {
  // 4 solid pixels, 2 dark antialiasing pixels (>= 3), 2 pale antialiasing pixels (< 3)
  const after = image(10, 1, (x) => (x < 4 ? gray(0) : (x < 6 ? gray(100) : (x < 8 ? gray(220) : null))));
  const m = focusMetrics(flaggers(new Set([4, 5, 6, 7])), image(10, 1), after, BOX);
  assert.deepEqual([m.diffPx, m.qualifyingStrict, m.qualifyingAA], [4, 4, 6]);
  near(m.ratioMin, 21, 1e-9);
});

test('K2: obrazy o roznych rozmiarach to null (flaggery nie sa wolane)', () => {
  const never = () => { throw new Error('must not be called'); };
  const f = { flaggerStrict: never, flaggerAA: never };
  assert.equal(focusMetrics(f, image(10, 10), image(10, 11), BOX), null);
  assert.equal(focusMetrics(f, image(11, 10), image(10, 10), BOX), null);
});

test('K2: flaggery dostaja obrazy przed i po, w tej kolejnosci', () => {
  const before = image(3, 1);
  const after = image(3, 1, () => gray(0));
  const seen = [];
  const spy = (a, b) => { seen.push([a === before, b === after]); return new Uint8Array(3); };
  focusMetrics({ flaggerStrict: spy, flaggerAA: spy }, before, after, BOX);
  assert.deepEqual(seen, [[true, true], [true, true]]);
});

test('K2: piksel z alfa jest skladany na nieprzezroczystym bialym tle', () => {
  const before = image(1, 1);
  const after = image(1, 1, () => ({ r: 0, g: 0, b: 0, a: 51 }));
  const m = focusMetrics(flaggers(), before, after, BOX);
  near(m.ratioMin, contrastRatio(WHITE, gray(204)), 1e-9);
  assert.equal(m.qualifyingStrict, 0);
});

test('K2: bez prostokata kontrast jest liczony, pole i prog to null', () => {
  const { before, after } = outline(24, 24, 2, gray(0));
  const m = focusMetrics(flaggers(), before, after, undefined);
  assert.deepEqual([m.qualifyingStrict, m.requiredArea, m.threshold], [208, null, null]);
});

// ---------- measureFocusStep returns the decoded images (A1, P9) ----------

const FakePNG = { sync: { read: (buf) => { const { w, h, fill } = JSON.parse(buf.toString()); return { width: w, height: h, data: new Uint8Array(w * h * 4).fill(fill) }; } } };

function stepEnv(sizes) {
  const ops = [];
  let shots = 0;
  const page = {
    async evaluate(fn, args) { ops.push(args.op); return true; },
    async screenshot({ clip }) {
      const n = shots++;
      ops.push('screenshot');
      const [w, h] = sizes ? sizes[n] : [clip.width, clip.height];
      return Buffer.from(JSON.stringify({ w, h, fill: n === 0 ? 0 : 255 }));
    },
  };
  return { env: { page, config: { viewport: { width: 100, height: 100 } }, imaging: { PNG: FakePNG }, flagger: flaggers().flaggerStrict }, ops };
}

test('measureFocusStep: liczba jak dotad, osobnym polem obrazy przed (po blur) i po (z fokusem)', async () => {
  const { env, ops } = stepEnv();
  const step = await measureFocusStep(env, { vr: { x: 20, y: 20, width: 4, height: 4 } });
  assert.deepEqual(ops, ['screenshot', 'blur', 'screenshot', 'refocus']);
  assert.equal(step.diffPx, 400);
  assert.deepEqual([step.images.before.width, step.images.before.height, step.images.before.data[0]], [20, 20, 255]);
  assert.deepEqual([step.images.after.width, step.images.after.height, step.images.after.data[0]], [20, 20, 0]);
  assert.equal(keyboardFinding({ id: 'a', idUnique: true, chain: [] }, step.diffPx, 4).verdict, 'PASS');
});

test('measureFocusStep: brak klipu to diffPx null i brak obrazow, bez zrzutow', async () => {
  const { env, ops } = stepEnv();
  assert.deepEqual(await measureFocusStep(env, { vr: { x: 500, y: 500, width: 4, height: 4 } }), { diffPx: null, images: null });
  assert.deepEqual(ops, []);
});

test('measureFocusStep: rozne rozmiary zrzutow to diffPx null, obrazy zostaja (focusMetrics da null)', async () => {
  const { env } = stepEnv([[4, 4], [4, 5]]);
  const step = await measureFocusStep(env, { vr: { x: 20, y: 20, width: 4, height: 4 } });
  assert.equal(step.diffPx, null);
  assert.equal(focusMetrics(flaggers(), step.images.before, step.images.after, BOX), null);
});
