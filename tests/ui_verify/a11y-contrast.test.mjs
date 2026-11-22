import test from 'node:test';
import assert from 'node:assert/strict';
import {
  compositeOver, contrastRatio, isLargeText, measureSample, naiveLayerRatio, relativeLuminance, resolveThresholds, srgbToLinear,
} from '../../library/skills/ui-verify/scripts/lib/a11y-contrast.mjs';
import { validateContrastSample } from '../../library/skills/ui-verify/scripts/lib/a11y-schema.mjs';

const WHITE = [255, 255, 255];
const px = (rgb, n = 4) => Array.from({ length: n }, () => rgb);
const rgb = (r, g, b, a = 1) => ({ r, g, b, a });
const sample = (over) => ({ selector: '#x', fontSizePx: 16, fontWeight: 400, foreground: rgb(0, 0, 0), opacity: 1, pixels: px(WHITE), ...over });
const near = (a, b, tol) => assert.ok(Math.abs(a - b) <= tol, `${a} vs ${b}`);

test('luminancja: prog 0,04045 i skrajne wartosci', () => {
  assert.equal(srgbToLinear(0), 0);
  assert.equal(srgbToLinear(255), 1);
  near(srgbToLinear(10), 10 / 255 / 12.92, 1e-12);
  assert.equal(relativeLuminance({ r: 255, g: 255, b: 255 }), 1);
  near(contrastRatio({ r: 0, g: 0, b: 0 }, { r: 255, g: 255, b: 255 }), 21, 1e-9);
});

test('fikstura: #pass rgb(117,117,117) na bialym = 4,61 PASS', () => {
  const r = measureSample(sample({ foreground: rgb(117, 117, 117) }));
  near(r.ratio, 4.61, 0.05);
  assert.equal(r.verdict, 'PASS');
  assert.equal(r.required, 4.5);
});

test('fikstura: #large-only rgb(138,138,138) 24 px = 3,45, duzy PASS, zwykly FAIL', () => {
  const big = measureSample(sample({ foreground: rgb(138, 138, 138), fontSizePx: 24 }));
  near(big.ratio, 3.45, 0.05);
  assert.equal(big.verdict, 'PASS');
  assert.equal(big.large, true);
  const small = measureSample(sample({ foreground: rgb(138, 138, 138), fontSizePx: 23.9 }));
  assert.equal(small.verdict, 'FAIL');
  assert.equal(small.required, 4.5);
});

test('fikstura: #fail rgb(176,176,176) na bialym = 2,17 FAIL', () => {
  const r = measureSample(sample({ foreground: rgb(176, 176, 176) }));
  near(r.ratio, 2.17, 0.05);
  assert.equal(r.verdict, 'FAIL');
});

test('fikstura: #layer-pass bialy na rgb(26,51,102) = 12,30 PASS', () => {
  const r = measureSample(sample({ foreground: rgb(255, 255, 255), pixels: px([26, 51, 102]) }));
  near(r.ratio, 12.3, 0.05);
  assert.equal(r.verdict, 'PASS');
  assert.deepEqual(r.effectiveBackground, { r: 26, g: 51, b: 102 });
});

test('fikstura: #layer-fail bialy na rgb(133,163,224) = 2,52 FAIL', () => {
  const r = measureSample(sample({ foreground: rgb(255, 255, 255), pixels: px([133, 163, 224]) }));
  near(r.ratio, 2.52, 0.05);
  assert.equal(r.verdict, 'FAIL');
});

test('odczyt koloru samej polprzezroczystej warstwy daje inny werdykt niz efektywne tlo', () => {
  // warstwa rgba(11,71,193,0.5) na bialym daje efektywne tlo rgb(133,163,224)
  const layer = rgb(11, 71, 193, 0.5);
  const effective = compositeOver(layer, { r: 255, g: 255, b: 255 });
  assert.deepEqual([effective.r, effective.g, effective.b], [133, 163, 224]);
  const naive = naiveLayerRatio({ r: layer.r, g: layer.g, b: layer.b }, { r: 255, g: 255, b: 255 });
  assert.ok(naive >= 4.5, `naiwny odczyt ${naive}`);
  const real = measureSample(sample({ foreground: rgb(255, 255, 255), pixels: px([133, 163, 224]) }));
  assert.equal(real.verdict, 'FAIL');
});

test('kolor pierwszego planu z alfa i opacity jest skladany nad pikselem tla', () => {
  const c = compositeOver(rgb(0, 0, 0, 0.5), { r: 255, g: 255, b: 255 });
  assert.deepEqual([c.r, c.g, c.b], [127.5, 127.5, 127.5]);
  // alfa tekstu 0,8 x opacity 0,5 = 0,4 nad bialym: czarny staje sie rgb(153,153,153)
  const r = measureSample(sample({ foreground: rgb(0, 0, 0, 0.8), opacity: 0.5 }));
  near(r.ratio, contrastRatio({ r: 153, g: 153, b: 153 }, { r: 255, g: 255, b: 255 }), 0.001);
  assert.equal(r.verdict, 'FAIL');
  assert.equal(measureSample(sample({ foreground: rgb(0, 0, 0, 0.8), opacity: 1 })).verdict, 'PASS');
});

test('werdykt z niezaokraglonego stosunku: 4,499 to FAIL, ratio z 3 miejscami', () => {
  // dobierz prog tuz nad stosunkiem
  const r0 = measureSample(sample({ foreground: rgb(118, 118, 118) }));
  const exact = r0.ratioExact;
  const above = measureSample(sample({ foreground: rgb(118, 118, 118) }), { normal: exact + 0.0004, large: 3 });
  assert.equal(above.verdict, 'FAIL');
  assert.equal(above.ratio, Math.round(exact * 1000) / 1000);
  const at = measureSample(sample({ foreground: rgb(118, 118, 118) }), { normal: exact, large: 3 });
  assert.equal(at.verdict, 'PASS');
  assert.equal(String(r0.ratio).split('.')[1].length <= 3, true);
});

test('najgorszy piksel; solid gdy rozrzut <= 1, inaczej varying', () => {
  const solid = measureSample(sample({ foreground: rgb(117, 117, 117), pixels: [[255, 255, 255], [254, 255, 255]] }));
  assert.equal(solid.backgroundKind, 'solid');
  const varying = measureSample(sample({ foreground: rgb(117, 117, 117), pixels: [[255, 255, 255], [200, 200, 200]] }));
  assert.equal(varying.backgroundKind, 'varying');
  assert.equal(varying.verdict, 'FAIL');
  near(varying.ratio, contrastRatio({ r: 117, g: 117, b: 117 }, { r: 200, g: 200, b: 200 }), 0.001);
  assert.deepEqual(varying.notes, ['sampled']);
});

test('varying: dla n >= 100 najgorszy 1 percentyl odrzuca szum, dla n < 100 minimum', () => {
  const many = [...px(WHITE, 150), [0, 0, 0]];
  const noisy = measureSample(sample({ foreground: rgb(0, 0, 0), pixels: many }));
  // czarny na czarnym to najgorszy piksel (stosunek 1), ale jest jednym z 151 (< 1 percentyla = indeks 1)
  assert.equal(noisy.verdict, 'PASS');
  const few = measureSample(sample({ foreground: rgb(0, 0, 0), pixels: [...px(WHITE, 5), [0, 0, 0]] }));
  assert.equal(few.verdict, 'FAIL');
  near(few.ratio, 1, 1e-9);
});

test('effectiveBackground to najczestszy kolor probek', () => {
  const r = measureSample(sample({ foreground: rgb(0, 0, 0), pixels: [[10, 10, 10], [250, 250, 250], [250, 250, 250]] }));
  assert.deepEqual(r.effectiveBackground, { r: 250, g: 250, b: 250 });
});

test('duzy tekst: 24 px albo bold >= 700 od 56/3 px', () => {
  assert.equal(isLargeText({ fontSizePx: 24, fontWeight: 400 }), true);
  assert.equal(isLargeText({ fontSizePx: 23.99, fontWeight: 400 }), false);
  assert.equal(isLargeText({ fontSizePx: 56 / 3, fontWeight: 700 }), true);
  assert.equal(isLargeText({ fontSizePx: 18.66, fontWeight: 700 }), false);
  assert.equal(isLargeText({ fontSizePx: 20, fontWeight: 600 }), false);
  assert.equal(isLargeText({ fontSizePx: 20, bold: true }), true);
});

test('progi ponizej 4,5 / 3,0 wymagaja reason, z reason jest ostrzezenie threshold-lowered', () => {
  const bad = resolveThresholds({ normal: 4.0 });
  assert.equal(bad.ok, false);
  assert.ok(bad.errors.some((e) => e.includes('reason')));
  const good = resolveThresholds({ normal: 4.0, reason: 'brand palette, accepted by design review' });
  assert.equal(good.ok, true);
  assert.equal(good.thresholds.normal, 4);
  assert.equal(good.warnings[0].code, 'threshold-lowered');
  assert.equal(resolveThresholds({ normal: 7 }).ok, true);
  assert.equal(resolveThresholds({ nope: 1 }).ok, false);
  assert.equal(resolveThresholds(undefined).thresholds.large, 3);
});

test('EXEMPT i UNVERIFIED maja pierwszenstwo przed pomiarem, EXEMPT przed UNVERIFIED', () => {
  assert.deepEqual(measureSample(sample({ exempt: 'not-rendered', pixels: [] })).verdict, 'EXEMPT');
  assert.equal(measureSample(sample({ exempt: 'inactive', unverified: 'occluded' })).verdict, 'EXEMPT');
  const u = measureSample(sample({ unverified: 'background-clip-text', foreground: rgb(255, 255, 255) }));
  assert.equal(u.verdict, 'UNVERIFIED');
  assert.equal(u.reason, 'background-clip-text');
  for (const reason of ['opacity-group-with-paint', 'color-unconvertible', 'clipped', 'filter-or-blend', 'inline-important-color', 'autofill', 'media-background', 'page-too-tall']) {
    assert.equal(measureSample(sample({ unverified: reason })).verdict, 'UNVERIFIED', reason);
  }
});

test('brak probek tla to UNVERIFIED, nigdy PASS', () => {
  const r = measureSample(sample({ pixels: [] }));
  assert.equal(r.verdict, 'UNVERIFIED');
  assert.equal(r.reason, 'no-samples');
});

test('walidator probki: blad opisuje pole, nieznana przyczyna jest odrzucona', () => {
  assert.equal(validateContrastSample({}).ok, false);
  assert.equal(validateContrastSample(sample({ exempt: 'whatever' })).ok, false);
  assert.equal(validateContrastSample(sample({ unverified: 'whatever' })).ok, false);
  assert.equal(validateContrastSample(sample({ pixels: [[1, 2]] })).ok, false);
  assert.equal(validateContrastSample(sample({ foreground: { r: 1, g: 2, b: 3 } })).ok, false);
  assert.equal(validateContrastSample(sample({ opacity: 2 })).ok, false);
  assert.equal(validateContrastSample(sample({})).ok, true);
  assert.throws(() => measureSample({ selector: '' }), /invalid contrast sample/);
});
