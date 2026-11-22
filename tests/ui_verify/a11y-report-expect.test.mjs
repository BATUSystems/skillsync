import test from 'node:test';
import assert from 'node:assert/strict';
import { buildReport, renderMarkdown } from '../../library/skills/ui-verify/scripts/lib/a11y-report.mjs';
import { cleanText } from '../../library/skills/ui-verify/scripts/lib/a11y-schema.mjs';
import { compareExpected, parseExpected, parseRgb, ExpectError } from '../../library/skills/ui-verify/scripts/lib/a11y-expect.mjs';
import { createMasker } from '../../library/skills/ui-verify/scripts/lib/out.mjs';

const page = (findings, extra = {}) => ({ id: 'home', url: 'file:///x/page.html?token=abc', assertion: 'PASS', checks: ['contrast'], findings, ...extra });
const f = (verdict, extra = {}) => ({ rule: 'contrast', selector: '#a', verdict, ...extra });

test('werdykt biegu: PASS, FAIL, UNVERIFIED i kody 0/1/3', () => {
  assert.equal(buildReport({ pages: [page([f('PASS')])] }).exit, 0);
  assert.equal(buildReport({ pages: [page([f('PASS'), f('FAIL')])] }).exit, 1);
  const unv = buildReport({ pages: [page([f('PASS'), f('UNVERIFIED', { reason: 'occluded' })])] });
  assert.equal(unv.result, 'UNVERIFIED');
  assert.equal(unv.exit, 3);
});

test('EXEMPT, EXCEPTED i INFO nie licza sie, CONFIRMED to PASS', () => {
  assert.equal(buildReport({ pages: [page([f('PASS'), f('EXEMPT'), f('EXCEPTED'), f('INFO')])] }).result, 'PASS');
  assert.equal(buildReport({ pages: [page([f('CONFIRMED')])] }).result, 'PASS');
  // same pominiete elementy nie dowodza niczego
  assert.equal(buildReport({ pages: [page([f('EXEMPT'), f('EXCEPTED')])] }).result, 'UNVERIFIED');
  assert.equal(buildReport({ pages: [page([f('EXCEPTED'), f('FAIL')])] }).result, 'FAIL');
});

test('pusta lista kontroli, brak asercji i pusty bieg to UNVERIFIED (3), asercja niespelniona to FAIL', () => {
  assert.equal(buildReport({ pages: [] }).exit, 3);
  assert.equal(buildReport({ pages: [page([])] }).exit, 3);
  assert.equal(buildReport({ pages: [page([f('PASS')], { checks: [] })] }).exit, 3);
  const none = buildReport({ pages: [page([f('PASS')], { assertion: 'NONE' })] });
  assert.equal(none.exit, 3);
  assert.equal(none.pages[0].findings[0].reason, 'no-assertion');
  const failed = buildReport({ pages: [page([f('PASS')], { assertion: 'FAIL' })] });
  assert.equal(failed.exit, 1);
  assert.equal(failed.pages[0].findings[0].rule, 'assertion-failed');
});

test('ksztalt JSON: pola schematu 1, url bez zapytania, klucze findingow', () => {
  const r = buildReport({ mode: 'audit', configDigest: 'abc123abc123', pages: [page([f('FAIL', { measured: { ratio: 2.17 }, required: 4.5, basis: 'wcag' }), { rule: 'tab-trap', verdict: 'FAIL', hashBasis: '#a>#b', message: 'cycle' }])] });
  for (const k of ['tool', 'version', 'mode', 'config', 'pages', 'exceptions', 'confirmations', 'warnings', 'summary', 'result']) assert.ok(k in r, k);
  assert.equal(r.tool, 'ui-verify a11y');
  assert.equal(r.config, 'abc123abc123');
  assert.equal(r.pages[0].url, 'file:///x/page.html');
  const [a, b] = r.pages[0].findings;
  assert.equal(a.key, 'contrast|home|#a');
  assert.match(b.key, /^tab-trap\|home\|[0-9a-f]{12}$/);
  assert.equal(a.id, 'contrast-1');
  assert.equal(a.pageData, true);
  assert.deepEqual(r.summary.byRule.contrast, { FAIL: 1 });
});

test('czyszczenie tekstu: znaki sterujace, bidi, zero-width, ciecie 80/300', () => {
  assert.equal(cleanText('a\u0000b\u0007c\nd\te'), 'abc d e');
  assert.equal(cleanText('ab‮cd​ef⁦gh﻿'), 'abcdefgh');
  assert.equal(cleanText('x'.repeat(100)).length, 80);
  assert.ok(cleanText('x'.repeat(100)).endsWith('...'));
  assert.equal(cleanText('x'.repeat(400), 300).length, 300);
  assert.equal(cleanText('x'.repeat(80)).length, 80);
  const r = buildReport({ pages: [page([f('FAIL', { selector: `#${'s'.repeat(200)}`, message: `m‮${'y'.repeat(500)}` })])] });
  assert.equal(r.pages[0].findings[0].selector.length, 80);
  assert.equal(r.pages[0].findings[0].message.length, 300);
  assert.ok(!/[‮]/.test(JSON.stringify(r)));
});

test('maskowanie przez masker z zewnatrz obejmuje komunikaty, selektory i ostrzezenia', () => {
  const secret = ['fake', 'secret', String(process.pid), 'zzzz'].join('-');
  const masker = createMasker({ secrets: [secret] });
  const r = buildReport({ masker, warnings: [{ code: 'x', level: 'warning', message: `leak ${secret}` }], pages: [page([f('FAIL', { message: `console says ${secret}`, selector: `a[href="${secret}"]` })])] });
  assert.ok(!JSON.stringify(r).includes(secret));
  assert.ok(!renderMarkdown(r).includes(secret));
  const unmasked = buildReport({ pages: [page([f('FAIL', { message: `console says ${secret}` })])] });
  assert.ok(JSON.stringify(unmasked).includes(secret), 'kontrola: bez maskera wartosc jest widoczna');
});

test('Markdown: tabela na regule, lista FAIL, dane strony, sekcje wyjatkow i potwierdzen', () => {
  const r = buildReport({
    pages: [page([
      f('FAIL', { measured: { ratio: 2.17, fontSizePx: 16 }, required: 4.5 }),
      f('EXCEPTED', { selector: '#b', exception: { reason: 'logo' } }),
      f('CONFIRMED', { selector: '#c', confirmation: { checkedBy: 'AB', reason: 'measured' } }),
      f('UNVERIFIED', { selector: '#d', reason: 'occluded' }),
    ])],
    warnings: [{ code: 'exception-unused', level: 'info', message: 'unused' }],
  });
  const md = renderMarkdown(r);
  for (const part of ['## Summary by rule', '| contrast |', '## Failures', '#a', '2.17', 'page data', '## Exceptions', 'logo', '## Confirmed manually', '## Unverified', '## Warnings']) {
    assert.ok(md.includes(part), part);
  }
  assert.ok(md.includes('FAIL') && md.includes('(exit 1)'));
});

test('parseRgb: rgb(), hex, tablica i obiekt', () => {
  assert.deepEqual(parseRgb('rgb(117, 117, 117)'), { r: 117, g: 117, b: 117 });
  assert.deepEqual(parseRgb('rgba(1,2,3,0.5)'), { r: 1, g: 2, b: 3 });
  assert.deepEqual(parseRgb('#ffffff'), { r: 255, g: 255, b: 255 });
  assert.deepEqual(parseRgb([1, 2, 3]), { r: 1, g: 2, b: 3 });
  assert.deepEqual(parseRgb({ r: 1, g: 2, b: 3, a: 1 }), { r: 1, g: 2, b: 3 });
  assert.equal(parseRgb('blue'), null);
});

const rgb = (r, g, b) => ({ r, g, b, a: 1 });
const found = (selector, ratio, verdict, over = {}) => ({
  rule: 'contrast', selector, verdict, required: 4.5,
  measured: { ratio, fontSizePx: 16, foreground: rgb(117, 117, 117), effectiveBackground: rgb(255, 255, 255) }, ...over,
});
const el = (selector, ratio, verdict) => ({ selector, fontSizePx: 16, foreground: 'rgb(117,117,117)', effectiveBackground: [255, 255, 255], ratio, required: 4.5, verdict });
const expected = (elements, over = {}) => ({ page: 'contrast/page.html', thresholds: { normal: 4.5, large: 3 }, tolerance: 0.05, elements, ...over });

test('expect: zgodny FAIL elementu to zgodnosc (kod 0)', () => {
  const r = compareExpected(expected([el('#fail', 2.17, 'FAIL'), el('#pass', 4.61, 'PASS')]), [found('#fail', 2.17, 'FAIL'), found('#pass', 4.611, 'PASS')], { thresholds: { normal: 4.5, large: 3 } });
  assert.equal(r.exit, 0);
  assert.equal(r.result, 'MATCH');
});

test('expect: niezgodnosc wartosci to kod 1, tolerancja ratio i kanalow', () => {
  assert.equal(compareExpected(expected([el('#a', 2.17, 'FAIL')]), [found('#a', 2.23, 'FAIL')]).exit, 1);
  assert.equal(compareExpected(expected([el('#a', 2.17, 'FAIL')]), [found('#a', 2.21, 'FAIL')]).exit, 0);
  assert.equal(compareExpected(expected([el('#a', 4.61, 'PASS')]), [found('#a', 4.61, 'FAIL')]).exit, 1);
  const shifted = found('#a', 4.61, 'PASS');
  shifted.measured.effectiveBackground = rgb(254, 255, 255);
  assert.equal(compareExpected(expected([el('#a', 4.61, 'PASS')]), [shifted]).exit, 0);
  shifted.measured.effectiveBackground = rgb(253, 255, 255);
  const r = compareExpected(expected([el('#a', 4.61, 'PASS')]), [shifted]);
  assert.equal(r.exit, 1);
  assert.ok(r.entries[0].diffs[0].includes('effectiveBackground'));
  assert.equal(compareExpected(expected([{ ...el('#a', 4.61, 'PASS'), required: 3 }]), [found('#a', 4.61, 'PASS')]).exit, 1);
  assert.equal(compareExpected(expected([{ ...el('#a', 4.61, 'PASS'), fontSizePx: 24 }]), [found('#a', 4.61, 'PASS')]).exit, 1);
});

test('expect: element nieobecny albo UNVERIFIED to kod 3', () => {
  assert.equal(compareExpected(expected([el('#gone', 4.61, 'PASS')]), [found('#a', 4.61, 'PASS')]).exit, 3);
  const unv = compareExpected(expected([el('#a', 4.61, 'PASS')]), [{ rule: 'contrast', selector: '#a', verdict: 'UNVERIFIED', reason: 'occluded' }]);
  assert.equal(unv.exit, 3);
  assert.equal(unv.entries[0].status, 'unverified');
});

test('expect: elementy spoza listy nie wplywaja na wynik; niezgodnosc wygrywa z brakiem', () => {
  assert.equal(compareExpected(expected([el('#a', 4.61, 'PASS')]), [found('#a', 4.61, 'PASS'), found('#extra', 1.2, 'FAIL')]).exit, 0);
  assert.equal(compareExpected(expected([el('#a', 4.61, 'FAIL'), el('#gone', 1, 'FAIL')]), [found('#a', 4.61, 'PASS')]).exit, 1);
});

test('expect: progi z expected.json porownywane z konfiguracja', () => {
  const r = compareExpected(expected([el('#a', 4.61, 'PASS')]), [found('#a', 4.61, 'PASS')], { thresholds: { normal: 7, large: 3 } });
  assert.equal(r.exit, 1);
  assert.equal(r.thresholdDiffs.length, 1);
});

test('expect: zly plik expected.json to ExpectError', () => {
  assert.throws(() => parseExpected([]), ExpectError);
  assert.throws(() => parseExpected({ elements: [] }), ExpectError);
  assert.throws(() => parseExpected({ elements: [{ ratio: 1 }] }), ExpectError);
  assert.throws(() => parseExpected({ elements: [{ selector: '#a', foreground: 'blue' }] }), ExpectError);
  assert.throws(() => parseExpected({ elements: [{ selector: '#a' }], tolerance: -1 }), ExpectError);
  assert.equal(parseExpected({ elements: [{ selector: '#a' }] }).tolerance, 0.05);
});

test('raport zawiera expectResult w trybie expect', () => {
  const expectResult = compareExpected(expected([el('#a', 4.61, 'PASS')]), [found('#a', 4.61, 'PASS')]);
  const r = buildReport({ mode: 'expect', pages: [page([found('#a', 4.61, 'PASS')])], expectResult });
  assert.equal(r.mode, 'expect');
  assert.equal(r.expectResult.exit, 0);
});
