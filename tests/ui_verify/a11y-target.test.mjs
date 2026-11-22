import test from 'node:test';
import assert from 'node:assert/strict';
import { distanceToBox, evaluateTargets, spacingOk } from '../../library/skills/ui-verify/scripts/lib/a11y-target.mjs';
import { reportSelector, isDynamicId, buildPath, capSelector } from '../../library/skills/ui-verify/scripts/lib/a11y-schema.mjs';

const t = (selector, x, y, w, h, extra = {}) => ({ selector, box: { x, y, width: w, height: h }, rendered: true, ...extra });
const by = (list, sel) => list.find((r) => r.selector === sel);

test('cel >= 24 x 24 przechodzi, 23,9 jest za maly', () => {
  const r = evaluateTargets([t('#ok', 0, 0, 24, 24), t('#thin', 100, 0, 24, 23.9), t('#next', 100, 10, 10, 10)]);
  assert.equal(by(r, '#ok').verdict, 'PASS');
  assert.equal(by(r, '#ok').exception, undefined);
  assert.equal(by(r, '#thin').verdict, 'FAIL');
});

test('ikona 16 px obok przycisku 100 px w odstepie 2 px NIE przechodzi', () => {
  const r = evaluateTargets([t('#icon', 0, 0, 16, 16), t('#btn', 18, 0, 100, 40)]);
  assert.equal(by(r, '#icon').verdict, 'FAIL');
  assert.equal(by(r, '#btn').verdict, 'PASS');
  assert.equal(by(r, '#icon').required, 24);
});

test('dwa male cele z przecinajacymi sie okregami NIE przechodza', () => {
  const r = evaluateTargets([t('#a', 0, 0, 16, 16), t('#b', 20, 0, 16, 16)]);
  assert.equal(by(r, '#a').verdict, 'FAIL');
  assert.equal(by(r, '#b').verdict, 'FAIL');
});

test('dwa male cele daleko od siebie przechodza z notka spacing', () => {
  const r = evaluateTargets([t('#a', 0, 0, 16, 16), t('#b', 60, 0, 16, 16)]);
  assert.equal(by(r, '#a').verdict, 'PASS');
  assert.equal(by(r, '#a').exception, 'spacing');
  assert.equal(by(r, '#a').note, 'spacing');
});

test('maly cel osamotniony przechodzi z notka spacing', () => {
  const r = evaluateTargets([t('#solo', 500, 500, 20, 20)]);
  assert.equal(r[0].verdict, 'PASS');
  assert.equal(r[0].note, 'spacing');
  assert.equal(r[0].widthPx, 20);
});

test('okrag dotykajacy pudelka sasiada stycznie nie przecina go', () => {
  // srodek (8,8), promien 12; sasiad zaczyna sie w x = 20 -> odleglosc 12
  const a = { selector: '#a', box: { x: 0, y: 0, width: 16, height: 16 } };
  assert.equal(spacingOk(a, [{ selector: '#n', box: { x: 20, y: 0, width: 100, height: 40 } }]), true);
  assert.equal(spacingOk(a, [{ selector: '#n', box: { x: 19.9, y: 0, width: 100, height: 40 } }]), false);
  assert.equal(distanceToBox({ x: 8, y: 8 }, { x: 20, y: 0, width: 5, height: 5 }) > 12, true);
});

test('duzy sasiad liczy sie tylko pudelkiem, maly takze okregiem (odleglosc srodkow < 24)', () => {
  const a = { selector: '#a', box: { x: 0, y: 0, width: 16, height: 16 } };
  // maly sasiad: pudelko poza okregiem (dx = 14 > 12), ale srodki w odleglosci 22 < 24
  assert.equal(spacingOk(a, [{ selector: '#s', box: { x: 22, y: 0, width: 16, height: 16 } }]), false);
  assert.equal(spacingOk(a, [{ selector: '#s', box: { x: 24, y: 0, width: 16, height: 16 } }]), true);
});

test('rownowazny cel: label >= 24 x 24 daje PASS equivalent: label', () => {
  const r = evaluateTargets([t('#cb', 0, 0, 13, 13, { labelBox: { width: 120, height: 30 } }), t('#close', 14, 0, 100, 40)]);
  assert.equal(by(r, '#cb').verdict, 'PASS');
  assert.equal(by(r, '#cb').exception, 'equivalent-label');
  assert.equal(by(r, '#cb').note, 'equivalent: label');
  const small = evaluateTargets([t('#cb', 0, 0, 13, 13, { labelBox: { width: 20, height: 30 } }), t('#close', 14, 0, 100, 40)]);
  assert.equal(by(small, '#cb').verdict, 'FAIL');
});

test('inline w zdaniu jest EXEMPT, a poza zdaniem nie', () => {
  const near = t('#next', 21, 0, 100, 40);
  const r = evaluateTargets([t('#link', 0, 0, 20, 16, { display: 'inline', parentHasText: true }), near]);
  assert.equal(by(r, '#link').verdict, 'EXEMPT');
  assert.equal(by(r, '#link').reason, 'inline');
  const notInSentence = evaluateTargets([t('#link', 0, 0, 20, 16, { display: 'inline', parentHasText: false }), near]);
  assert.equal(by(notInSentence, '#link').verdict, 'FAIL');
});

test('kontrolka agenta uzytkownika jest EXEMPT user-agent', () => {
  const r = evaluateTargets([t('#cb', 0, 0, 13, 13, { userAgent: true }), t('#x', 14, 0, 100, 40)]);
  assert.equal(by(r, '#cb').verdict, 'EXEMPT');
  assert.equal(by(r, '#cb').reason, 'user-agent');
});

test('pula celow to tylko wyrenderowane: ukryty input nie psuje sasiadom', () => {
  const hidden = t('#hid', 0, 0, 1, 1, { rendered: false });
  const r = evaluateTargets([hidden, t('#small', 1, 1, 16, 16)]);
  assert.equal(r.length, 1);
  assert.equal(by(r, '#small').verdict, 'PASS');
});

test('para (kontrolka i jej label) nie blokuje odstepu nawzajem', () => {
  const r = evaluateTargets([t('#cb', 0, 0, 16, 16, { pair: 'p1' }), t('label[for=cb]', 18, 0, 16, 16, { pair: 'p1' })]);
  assert.equal(by(r, '#cb').verdict, 'PASS');
});

test('nieprawidlowy cel zglasza blad schematu', () => {
  assert.throws(() => evaluateTargets([{ selector: '#a', box: { x: 0 } }]), /invalid target/);
});

test('reportSelector: #id tylko gdy unikalne i niedynamiczne, inaczej sciezka', () => {
  const chain = [{ tag: 'html' }, { tag: 'body' }, { tag: 'div', nth: 2 }, { tag: 'a', nth: 1 }];
  assert.equal(reportSelector({ id: 'save', idUnique: true, chain }), '#save');
  assert.equal(reportSelector({ id: 'save', idUnique: false, chain }), 'html > body > div:nth-of-type(2) > a:nth-of-type(1)');
  for (const dyn of [':r1:', 'btn-1234', 'a1b2c3d4e5', 'DEADBEEF']) {
    assert.equal(isDynamicId(dyn), true, dyn);
    assert.ok(reportSelector({ id: dyn, idUnique: true, chain }).includes('nth-of-type'), dyn);
  }
  assert.equal(isDynamicId('main-nav'), false);
  assert.equal(isDynamicId('abc123'), false);
  assert.equal(buildPath([{ tag: 'HTML' }, { tag: 'BODY' }, { tag: 'P' }]), 'html > body > p:nth-of-type(1)');
  assert.equal(reportSelector({ id: '1x', idUnique: true, chain: [{ tag: 'p', nth: 1 }] }), '[id="1x"]');
});

test('dedup po identyfikatorze elementu: ten sam element raz, dwa rozne elementy o tym samym selektorze oba zachowane', () => {
  const same = evaluateTargets([t('#a', 0, 0, 30, 30, { uid: 'k1' }), t('#a', 0, 0, 30, 30, { uid: 'k1' }), t('#b', 100, 0, 30, 30, { uid: 'k2' })]);
  assert.deepEqual(same.map((x) => x.selector), ['#a', '#b']);
  const clash = evaluateTargets([t('#a', 0, 0, 20, 20, { uid: 'k1' }), t('#a', 22, 0, 20, 20, { uid: 'k2' })]);
  assert.equal(clash.length, 2, 'different elements are never dropped silently');
  // bez identyfikatora (starsze dane) deduplikacja po selektorze jak dotad
  assert.deepEqual(evaluateTargets([t('#a', 0, 0, 30, 30), t('#a', 0, 0, 30, 30)]).map((x) => x.selector), ['#a']);
});

test('isDynamicId: dwukropek w srodku, UUID i licznik w srodku id sa dynamiczne, zwykle id nie', () => {
  for (const id of ['radix-:r1:', 'a:b', '123e4567-e89b-12d3-a456-426614174000', 'react-select-3-input', 'item-12-label']) assert.equal(isDynamicId(id), true, id);
  for (const id of ['tiny-1', 'main-nav', 'seam-first', 'step-2', 'h1']) assert.equal(isDynamicId(id), false, id);
});

test('capSelector: dlugi selektor zachowuje poczatek, koniec i skrot, rozne elementy maja rozne selektory', () => {
  const deep = Array.from({ length: 60 }, (_, i) => `section-wrapper-${i}:nth-of-type(1)`).join(' > ');
  const a = capSelector(`${deep} > button:nth-of-type(1)`);
  const b = capSelector(`${deep} > button:nth-of-type(2)`);
  assert.ok(a.length <= 400 && b.length <= 400 && a !== b, `${a.length} ${b.length}`);
  assert.ok(a.startsWith('section-wrapper-0') && a.includes('...') && /~[0-9a-f]{8}$/.test(a));
  assert.equal(capSelector('#short'), '#short');
});
