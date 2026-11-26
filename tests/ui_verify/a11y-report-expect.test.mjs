import test from 'node:test';
import assert from 'node:assert/strict';
import { buildReport, renderMarkdown } from '../../library/skills/ui-verify/scripts/lib/a11y-report.mjs';
import { LIMITS, RULES, cleanText } from '../../library/skills/ui-verify/scripts/lib/a11y-schema.mjs';
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

test('EXCEPTED i CONFIRMED licza sie jako PASS, gdy cos sie zmierzylo; EXEMPT, INFO i pusta lista nie dowodza niczego', () => {
  const exit = (verdicts, extra) => buildReport({ pages: [page(verdicts.map((v) => f(v, v === 'UNVERIFIED' ? { reason: 'occluded' } : {})), extra)] }).exit;
  assert.equal(exit([]), 3);
  assert.equal(exit(['EXEMPT']), 3);
  assert.equal(exit(['INFO']), 3);
  assert.equal(exit(['EXCEPTED']), 0);
  assert.equal(exit(['CONFIRMED']), 0);
  assert.equal(exit(['PASS', 'EXEMPT', 'EXCEPTED', 'INFO']), 0);
  assert.equal(exit(['EXCEPTED', 'UNVERIFIED']), 3);
  assert.equal(exit(['EXCEPTED', 'FAIL']), 1);
  assert.equal(exit(['EXCEPTED'], { assertion: 'NONE' }), 3);
  assert.equal(exit(['EXEMPT', 'INFO']), 3);
});

test('PASS z wyjatkami jest widoczny: liczniki i klucze w JSON i w Markdown', () => {
  const r = buildReport({ pages: [page([f('PASS'), f('EXCEPTED', { key: 'contrast|home|#brand' }), f('CONFIRMED', { key: 'contrast|home|#hero', selector: '#hero' })])] });
  assert.deepEqual([r.exit, r.summary.excepted, r.summary.confirmed], [0, 1, 1]);
  assert.deepEqual([r.summary.exceptedKeys, r.summary.confirmedKeys], [['contrast|home|#brand'], ['contrast|home|#hero']]);
  const md = renderMarkdown(r);
  assert.match(md, /PASS includes 1 excepted and 1 manually confirmed/);
  assert.ok(md.includes('excepted: `contrast\\|home\\|#brand`') && md.includes('confirmed: `contrast\\|home\\|#hero`'));
  assert.ok(!renderMarkdown(buildReport({ pages: [page([f('PASS')])] })).includes('PASS includes'));
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
  assert.equal(r.pages[0].findings[0].selector.length, 201);
  assert.equal(cleanText(`#${'s'.repeat(500)}`, LIMITS.selectorChars).length, 400);
  assert.equal(cleanText('t'.repeat(500)).length, 80);
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
  assert.throws(() => parseExpected({ elements: [{ selector: '#a' }] }), /verdict/);
  assert.throws(() => parseExpected({ elements: [{ selector: '#a', verdict: 'PASS', colour: 'x' }] }), /unknown key/);
  assert.equal(parseExpected({ elements: [{ selector: '#a', verdict: 'PASS', note: 'n' }] }).tolerance, 0.05);
});

test('raport zawiera expectResult w trybie expect', () => {
  const expectResult = compareExpected(expected([el('#a', 4.61, 'PASS')]), [found('#a', 4.61, 'PASS')]);
  const r = buildReport({ mode: 'expect', pages: [page([found('#a', 4.61, 'PASS')])], expectResult });
  assert.equal(r.mode, 'expect');
  assert.equal(r.expectResult.exit, 0);
});

test('tryb expect: exit i result z porownania, wynik audytu w osobnych polach (celowe FAIL = 0)', () => {
  const expectResult = compareExpected(expected([el('#fail', 2.17, 'FAIL')]), [found('#fail', 2.17, 'FAIL')]);
  const r = buildReport({ mode: 'expect', pages: [page([found('#fail', 2.17, 'FAIL')])], expectResult });
  assert.equal(r.exit, 0);
  assert.equal(r.result, 'PASS');
  assert.equal(r.auditResult, 'FAIL');
  assert.equal(r.auditExit, 1);
  const bad = buildReport({ mode: 'expect', pages: [page([found('#fail', 3.5, 'FAIL')])], expectResult: compareExpected(expected([el('#fail', 2.17, 'FAIL')]), [found('#fail', 3.5, 'FAIL')]) });
  assert.equal(bad.exit, 1);
  const miss = buildReport({ mode: 'expect', pages: [page([])], expectResult: compareExpected(expected([el('#fail', 2.17, 'FAIL')]), []) });
  assert.equal(miss.exit, 3);
  assert.equal(miss.result, 'UNVERIFIED');
});

test('ostrzezenia: code i selector czyszczone, expectResult czyszczony i maskowany, now-overridden', () => {
  const secret = ['fake', 'secret', String(process.pid), 'qqqq'].join('-');
  const masker = createMasker({ secrets: [secret] });
  const expectResult = { exit: 1, result: 'MISMATCH', entries: [{ selector: `#a${secret}`, status: 'mismatch', diffs: [`got ${secret}\u202e`] }] };
  const r = buildReport({ masker, mode: 'expect', expectResult, nowOverridden: '2026-01-02', warnings: [{ code: `c\u202e${secret}`, level: 'warning', selector: `#s\u0007${secret}`, message: 'm' }], pages: [page([f('PASS')])] });
  const json = JSON.stringify(r);
  assert.ok(!json.includes(secret));
  assert.ok(!/[\u202e\u0007]/.test(json));
  assert.ok(r.warnings.some((w) => w.code === 'now-overridden'));
  assert.equal(buildReport({ pages: [page([f('PASS')])] }).warnings.length, 0);
});

test('cleanText: znaki tag, soft hyphen, CGJ, selektory wariantow, wypelniacze Hangul', () => {
  const junk = '\u{e0041}\u{e007f}\u00ad\u034f\u180e\ufe0f\ufe00\u115f\u1160\u3164\uffa0';
  assert.equal(cleanText(`a${junk}b`), 'ab');
});

const focusContrast = (verdict, extra = {}) => ({ rule: 'focus-contrast', selector: '#faint', verdict, measured: { diffPx: 40, qualifyingStrict: 0, ratioMedian: 1.4, ratioMin: 1.2 }, required: 3, ...extra });
const focusAppearance = (verdict, extra = {}) => ({ rule: 'focus-appearance', selector: '#thin', verdict, measured: { qualifyingAA: 72, requiredArea: 192 }, required: 166.4, ...extra });
const informational = (md) => md.split('## Informational\n')[1]?.split('\n## ')[0] ?? null;

test('reguly fokusu sa w RULES', () => {
  assert.ok(RULES.includes('focus-contrast') && RULES.includes('focus-appearance'));
});

test('basis: focus-appearance to wcag-aaa, focus-contrast i pozostale to wcag, jawne basis wygrywa', () => {
  const r = buildReport({ pages: [page([focusAppearance('FAIL'), focusContrast('FAIL'), f('FAIL'), f('FAIL', { basis: 'best-practice' })])] });
  assert.deepEqual(r.pages[0].findings.map((x) => x.basis), ['wcag-aaa', 'wcag', 'wcag', 'best-practice']);
  const info = buildReport({ pages: [page([focusAppearance('INFO'), focusAppearance('PASS')])] });
  assert.deepEqual(info.pages[0].findings.map((x) => x.basis), ['wcag-aaa', 'wcag-aaa']);
});

test('Markdown: sekcja Informational dla INFO nowych regul fokusu, z przyczyna i bez wplywu na kod wyjscia', () => {
  const r = buildReport({ pages: [page([f('PASS'), focusContrast('INFO', { note: 'visible | faint' }), focusAppearance('INFO', { selector: '#cut', reason: 'clipped', measured: undefined, required: undefined })])] });
  assert.equal(r.exit, 0);
  const section = informational(renderMarkdown(r));
  assert.ok(section, 'section is present');
  assert.ok(section.includes('- [focus-contrast] page home: `#faint` measured diffPx=40, qualifyingStrict=0, ratioMedian=1.4, ratioMin=1.2, required 3: visible \\| faint'), section);
  assert.ok(section.includes('- [focus-appearance] page home: `#cut` (clipped)\n'), section);
});

test('Markdown: bez INFO nowych regul fokusu nie ma sekcji Informational', () => {
  const mouse = { rule: 'focus-mouse-ring', selector: '#nav', verdict: 'INFO', measured: { diffPx: 40 }, note: 'ring visible after click' };
  const consoleInfo = { rule: 'console', verdict: 'INFO', message: 'console.warning: x' };
  const md = renderMarkdown(buildReport({ pages: [page([f('PASS'), mouse, consoleInfo, focusContrast('PASS'), focusAppearance('FAIL')])] }));
  assert.ok(!md.includes('## Informational'));
  assert.ok(md.includes('- [focus-appearance] page home: `#thin`'));
});

test('Markdown Failures: notatka tylko dla focus-contrast i focus-appearance, pozostale reguly bez zmian', () => {
  const finding = (rule, extra = {}) => ({ rule, selector: `#${rule}`, verdict: 'FAIL', measured: { diffPx: 9 }, required: 3, ...extra });
  const report = buildReport({ pages: [{ id: 'home', assertion: 'PASS', findings: [
    finding('focus-contrast', { note: 'too pale | really' }), finding('focus-appearance', { note: 'too small' }),
    finding('focus-keyboard-indicator', { note: 'not listed' }), finding('contrast', { note: 'sampled', message: 'low' }),
  ] }] });
  const section = renderMarkdown(report).split('## Failures')[1].split('\n## ')[0];
  assert.ok(section.includes('`#focus-contrast` measured diffPx=9, required 3: too pale \\| really\n'), section);
  assert.ok(section.includes('`#focus-appearance` measured diffPx=9, required 3: too small\n'), section);
  assert.ok(section.includes('`#focus-keyboard-indicator` measured diffPx=9, required 3\n'), section);
  assert.ok(section.includes('`#contrast` measured diffPx=9, required 3: low\n'), section);
  assert.ok(!section.includes('not listed') && !section.includes('sampled'), section);
});
