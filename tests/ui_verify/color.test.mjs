import test from 'node:test';
import assert from 'node:assert/strict';
import { parseColor, blend, colorDistance, sameColor } from '../../library/skills/ui-verify/scripts/lib/color.mjs';

test('parseColor: formaty hex', () => {
  assert.deepEqual(parseColor('#f00'), { r: 255, g: 0, b: 0, a: 1 });
  assert.deepEqual(parseColor('#FF8000'), { r: 255, g: 128, b: 0, a: 1 });
  const c = parseColor('#ff800080');
  assert.deepEqual([c.r, c.g, c.b], [255, 128, 0]);
  assert.ok(Math.abs(c.a - 128 / 255) < 0.01);
});

test('parseColor: rgb, rgba, skladnia ze spacjami, transparent', () => {
  assert.deepEqual(parseColor('rgb(1, 2, 3)'), { r: 1, g: 2, b: 3, a: 1 });
  assert.deepEqual(parseColor('rgba(1, 2, 3, 0.5)'), { r: 1, g: 2, b: 3, a: 0.5 });
  assert.deepEqual(parseColor('rgb(1 2 3 / 0.5)'), { r: 1, g: 2, b: 3, a: 0.5 });
  assert.equal(parseColor('transparent').a, 0);
  assert.deepEqual(parseColor('  rgb(10,20,30)  '), { r: 10, g: 20, b: 30, a: 1 });
});

test('parseColor: bledne => null', () => {
  for (const bad of ['', 'red-ish', '#12', '#12345', '#gggggg', 'rgb(1,2)', 'rgb(a,b,c)', 'rgba(1,2,3,2)', 'rgba(1,2,3,-0.1)', 'rgb(300,0,0)', null, undefined, 42]) {
    assert.equal(parseColor(bad), null, String(bad));
  }
});

test('blend: kompozycja alfa i zaokraglenie do calkowitych', () => {
  assert.deepEqual(blend({ r: 255, g: 0, b: 0, a: 1 }, { r: 0, g: 0, b: 255, a: 1 }), { r: 255, g: 0, b: 0, a: 1 });
  assert.deepEqual(blend({ r: 255, g: 0, b: 0, a: 0 }, { r: 0, g: 0, b: 255, a: 1 }), { r: 0, g: 0, b: 255, a: 1 });
  const m = blend({ r: 255, g: 255, b: 255, a: 0.5 }, { r: 0, g: 0, b: 0, a: 1 });
  assert.ok(Number.isInteger(m.r) && Number.isInteger(m.g) && Number.isInteger(m.b));
  assert.ok(m.r === 127 || m.r === 128);
  assert.equal(m.a, 1);
  const h = blend({ r: 10, g: 10, b: 10, a: 0.5 }, { r: 10, g: 10, b: 10, a: 0.5 });
  assert.equal(h.a, 0.75);
});

test('colorDistance: maksimum modulow roznic kanalow, alfa ignorowana', () => {
  assert.equal(colorDistance({ r: 0, g: 0, b: 0, a: 1 }, { r: 3, g: 10, b: 7, a: 1 }), 10);
  assert.equal(colorDistance({ r: 10, g: 0, b: 0, a: 1 }, { r: 0, g: 0, b: 0, a: 1 }), 10);
  assert.equal(colorDistance({ r: 5, g: 5, b: 5, a: 1 }, { r: 5, g: 5, b: 5, a: 0.1 }), 0);
  assert.equal(colorDistance({ r: 0, g: 0, b: 0 }, { r: 255, g: 255, b: 255 }), 255);
});

test('sameColor: tolerancja domyslnie 0', () => {
  const a = { r: 10, g: 10, b: 10, a: 1 };
  const b = { r: 12, g: 10, b: 9, a: 1 };
  assert.equal(sameColor(a, a), true);
  assert.equal(sameColor(a, b), false);
  assert.equal(sameColor(a, b, 1), false);
  assert.equal(sameColor(a, b, 2), true);
});

// A13: dwa w pelni przezroczyste kolory sa rowne niezaleznie od RGB.
test('sameColor: dwa kolory z alfa 0 sa rowne mimo roznych RGB; przezroczysty i nieprzezroczysty nie', () => {
  assert.equal(sameColor({ r: 255, g: 0, b: 0, a: 0 }, { r: 0, g: 0, b: 0, a: 0 }), true);
  assert.equal(sameColor(parseColor('rgba(10, 200, 30, 0)'), parseColor('transparent')), true);
  assert.equal(sameColor({ r: 255, g: 0, b: 0, a: 0 }, { r: 255, g: 0, b: 0, a: 1 }), false);
  assert.equal(sameColor({ r: 255, g: 0, b: 0, a: 1 }, { r: 0, g: 0, b: 0, a: 1 }), false, 'kontrola: nieprzezroczyste o roznym RGB nadal rozne');
});
