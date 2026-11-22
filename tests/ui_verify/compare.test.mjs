import test from 'node:test';
import assert from 'node:assert/strict';
import { compareNumber, compareBox, compareStyles, applyDeviations } from '../../library/skills/ui-verify/scripts/lib/compare.mjs';

test('compareNumber: delta i tolerancja', () => {
  assert.deepEqual(compareNumber(10, 10), { pass: true, delta: 0 });
  assert.deepEqual(compareNumber(12, 10), { pass: false, delta: 2 });
  assert.deepEqual(compareNumber(12, 10, 2), { pass: true, delta: 2 });
  assert.deepEqual(compareNumber(8, 10, 1), { pass: false, delta: -2 });
});

test('compareNumber: epsilon 1e-9 dla bledu zmiennoprzecinkowego', () => {
  // 0.1+0.2-0.3 = 5.5e-17 < 1e-9 => pass mimo tolerancji 0
  assert.equal(compareNumber(0.1 + 0.2, 0.3).pass, true);
  assert.equal(compareNumber(0.1 + 0.2, 0.3, 0).pass, true);
  assert.equal(compareNumber(1.5, 1.0, 0.5).pass, true);
  assert.equal(compareNumber(1.5 + 1e-6, 1.0, 0.5).pass, false);
});

test('compareNumber: nie-liczby => pass false, delta NaN', () => {
  for (const [a, e] of [['x', 1], [1, 'y'], [undefined, 1], [null, null], [NaN, 1]]) {
    const r = compareNumber(a, e);
    assert.equal(r.pass, false);
    assert.ok(Number.isNaN(r.delta));
  }
});

test('compareBox: pola x,y,width,height, findings z delta', () => {
  const a = { x: 0, y: 0, width: 100, height: 50 };
  assert.deepEqual(compareBox(a, { ...a }), { pass: true, findings: [] });
  const r = compareBox({ x: 0, y: 0, width: 103, height: 50 }, a);
  assert.equal(r.pass, false);
  assert.equal(r.findings.length, 1);
  assert.deepEqual(r.findings[0], { prop: 'width', actual: 103, expected: 100, delta: 3 });
  assert.equal(compareBox({ x: 1, y: -1, width: 101, height: 49 }, a, 1).pass, true);
  const many = compareBox({ x: 5, y: 5, width: 5, height: 5 }, a, 1);
  assert.deepEqual(many.findings.map((f) => f.prop).sort(), ['height', 'width', 'x', 'y']);
});

test('compareStyles: px liczbowo z tolerancja', () => {
  assert.equal(compareStyles({ width: '100px' }, { width: '100px' }).pass, true);
  assert.equal(compareStyles({ width: '100.0px' }, { width: '100px' }).pass, true);
  const bad = compareStyles({ width: '103px' }, { width: '100px' });
  assert.equal(bad.pass, false);
  assert.equal(bad.findings.length, 1);
  assert.equal(bad.findings[0].prop, 'width');
  assert.equal(compareStyles({ width: '103px' }, { width: '100px' }, { tolerancePx: 3 }).pass, true);
  assert.equal(compareStyles({ width: '104px' }, { width: '100px' }, { tolerancePx: 3 }).pass, false);
});

test('compareStyles: kolory przez parseColor i colorTolerance', () => {
  assert.equal(compareStyles({ color: 'rgb(255, 0, 0)' }, { color: '#f00' }).pass, true);
  assert.equal(compareStyles({ color: 'rgb(250, 0, 0)' }, { color: '#f00' }).pass, false);
  assert.equal(compareStyles({ color: 'rgb(250, 0, 0)' }, { color: '#f00' }, { colorTolerance: 5 }).pass, true);
  assert.equal(compareStyles({ color: 'rgb(250, 0, 0)' }, { color: '#f00' }, { colorTolerance: 4 }).pass, false);
});

test('compareStyles: teksty po trim, slowa kluczowe bez wielkosci liter', () => {
  assert.equal(compareStyles({ display: ' FLEX ' }, { display: 'flex' }).pass, true);
  assert.equal(compareStyles({ display: 'block' }, { display: 'flex' }).pass, false);
  const r = compareStyles({ display: 'block', width: '1px' }, { display: 'flex', width: '1px' });
  assert.deepEqual(r.findings.map((f) => f.prop), ['display']);
});

test('applyDeviations: reason wymagany, dopasowanie prop/selector/actual/expected', () => {
  const findings = [
    { selector: '#a', prop: 'width', actual: '103px', expected: '100px' },
    { selector: '#b', prop: 'color', actual: 'red', expected: 'blue' },
  ];
  const ok = applyDeviations(findings, [{ selector: '#a', prop: 'width', reason: 'zaokraglenie' }]);
  assert.equal(ok.approved.length, 1);
  assert.equal(ok.unapproved.length, 1);
  assert.equal(ok.approved[0].prop, 'width');
  assert.equal(ok.unapproved[0].prop, 'color');
});

test('applyDeviations: wpis bez reason (brak, pusty, spacje) nie zatwierdza', () => {
  const findings = [{ prop: 'width', actual: '1px', expected: '2px' }];
  for (const dev of [{ prop: 'width' }, { prop: 'width', reason: '' }, { prop: 'width', reason: '   ' }]) {
    const r = applyDeviations(findings, [dev]);
    assert.equal(r.approved.length, 0, JSON.stringify(dev));
    assert.equal(r.unapproved.length, 1);
  }
});

test('applyDeviations: niezgodny selector, actual lub expected nie zatwierdza', () => {
  const f = [{ selector: '#a', prop: 'width', actual: '1px', expected: '2px' }];
  assert.equal(applyDeviations(f, [{ selector: '#x', prop: 'width', reason: 'r' }]).approved.length, 0);
  assert.equal(applyDeviations(f, [{ selector: '#a', prop: 'width', actual: '9px', reason: 'r' }]).approved.length, 0);
  assert.equal(applyDeviations(f, [{ selector: '#a', prop: 'width', expected: '9px', reason: 'r' }]).approved.length, 0);
  assert.equal(applyDeviations(f, [{ selector: '#a', prop: 'width', actual: '1px', expected: '2px', reason: 'r' }]).approved.length, 1);
  assert.equal(applyDeviations(f, [{ prop: 'height', reason: 'r' }]).approved.length, 0);
});

test('applyDeviations: finding bez selectora zatwierdza wpis bez selectora', () => {
  const r = applyDeviations([{ prop: 'width', actual: '1px', expected: '2px' }], [{ prop: 'width', reason: 'r' }]);
  assert.equal(r.approved.length, 1);
  assert.equal(r.unapproved.length, 0);
});

// A13: transparent i rgba(..., 0) o dowolnym RGB to ten sam kolor.
test('compareStyles: kolory z alfa 0 (transparent, rgba(255, 0, 0, 0)) sa rowne; alfa 0 vs 1 nie', () => {
  assert.equal(compareStyles({ backgroundColor: 'rgba(255, 0, 0, 0)' }, { backgroundColor: 'transparent' }).pass, true);
  assert.equal(compareStyles({ backgroundColor: 'rgba(0, 0, 0, 0)' }, { backgroundColor: 'rgba(12, 34, 56, 0)' }).pass, true);
  assert.equal(compareStyles({ backgroundColor: 'rgba(255, 0, 0, 0)' }, { backgroundColor: 'rgb(255, 0, 0)' }).pass, false);
  assert.equal(compareStyles({ backgroundColor: 'rgb(255, 0, 0)' }, { backgroundColor: 'rgb(0, 0, 0)' }).pass, false);
});
