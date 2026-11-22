import test from 'node:test';
import assert from 'node:assert/strict';
import { classifyKeyboard, classifyMouse, validateMouseConfig } from '../../library/skills/ui-verify/scripts/lib/a11y-focus.mjs';
import { analyzeTab, expectedStops, sequenceEnd } from '../../library/skills/ui-verify/scripts/lib/a11y-tab.mjs';
import { validateTabStep } from '../../library/skills/ui-verify/scripts/lib/a11y-schema.mjs';

const step = (key, x = 0, y = 0, extra = {}) => ({ key, selector: `#${key}`, tabindex: null, box: { x, y, width: 50, height: 20 }, inDocument: true, inModal: false, kind: 'element', ...extra });
const cand = (key, extra = {}) => ({ key, selector: `#${key}`, tabindex: null, ...extra });
const order = (r) => r.findings.filter((f) => f.rule === 'tab-order-visual');
const rules = (r) => r.findings.map((f) => f.rule);

test('focus klawiatury: >= 4 pikseli PASS, mniej FAIL (przezroczysty outline daje 0)', () => {
  assert.equal(classifyKeyboard({ diffPx: 4 }).verdict, 'PASS');
  assert.equal(classifyKeyboard({ diffPx: 3 }).verdict, 'FAIL');
  assert.equal(classifyKeyboard({ diffPx: 0 }).verdict, 'FAIL');
  assert.equal(classifyKeyboard({ diffPx: 10, minDiffPx: 12 }).verdict, 'FAIL');
  assert.equal(classifyKeyboard({ diffPx: 0 }).rule, 'focus-keyboard-indicator');
  assert.throws(() => classifyKeyboard({ diffPx: -1 }));
});

test('mysz: report to INFO, forbid zamienia widoczna ramke w FAIL, require odwrotnie', () => {
  assert.equal(classifyMouse({ mode: 'report', diffPx: 50 }).verdict, 'INFO');
  assert.equal(classifyMouse({ mode: 'report', diffPx: 0 }).verdict, 'INFO');
  assert.equal(classifyMouse({ mode: 'forbid', diffPx: 50 }).verdict, 'FAIL');
  assert.equal(classifyMouse({ mode: 'forbid', diffPx: 0 }).verdict, 'PASS');
  assert.equal(classifyMouse({ mode: 'require', diffPx: 0 }).verdict, 'FAIL');
  assert.equal(classifyMouse({ mode: 'require', diffPx: 9 }).verdict, 'PASS');
  assert.equal(classifyMouse({ mode: 'forbid', diffPx: 9 }).rule, 'focus-mouse-ring');
  assert.throws(() => classifyMouse({ mode: 'x', diffPx: 1 }));
});

test('konfiguracja myszy: tryb i do 20 selektorow', () => {
  assert.equal(validateMouseConfig(undefined).mouse, null);
  assert.equal(validateMouseConfig({ mode: 'report', selectors: ['#a'] }).ok, true);
  assert.equal(validateMouseConfig({ mode: 'nope', selectors: ['#a'] }).ok, false);
  assert.equal(validateMouseConfig({ mode: 'report', selectors: [] }).ok, false);
  assert.equal(validateMouseConfig({ mode: 'report', selectors: Array.from({ length: 21 }, (_, i) => `#a${i}`) }).ok, false);
  assert.equal(validateMouseConfig({ mode: 'report', selectors: Array.from({ length: 20 }, (_, i) => `#a${i}`) }).ok, true);
  assert.equal(validateMouseConfig({ mode: 'report', selectors: ['#a'], extra: 1 }).ok, false);
});

test('koniec sekwencji: powrot do pierwszego kroku, body, poza dokument, limit', () => {
  const wrapped = sequenceEnd([step('a'), step('b'), step('a')]);
  assert.equal(wrapped.end, 'wrapped');
  assert.equal(wrapped.steps.length, 2);
  assert.equal(sequenceEnd([step('a'), { key: 'body', selector: 'body', kind: 'body' }]).end, 'body');
  assert.equal(sequenceEnd([step('a'), { key: 'o', selector: 'o', kind: 'outside' }]).end, 'outside');
  const limit = sequenceEnd([step('a'), step('b'), step('c'), step('d')], { maxStops: 3 });
  assert.equal(limit.end, 'limit');
  assert.equal(limit.steps.length, 3);
  assert.equal(sequenceEnd([step('a'), step('b')]).end, 'limit');
});

test('walidator kroku Tab odrzuca zly ksztalt', () => {
  assert.equal(validateTabStep({}).ok, false);
  assert.equal(validateTabStep({ key: 'a', kind: 'weird' }).ok, false);
  assert.equal(validateTabStep(step('a')).ok, true);
});

test('tab-positive-tabindex: domyslnie FAIL z basis best-practice, opcja warn daje ostrzezenie', () => {
  const raw = [step('a', 0, 0, { tabindex: 3 }), step('b', 60, 0), step('a', 0, 0, { tabindex: 3 })];
  const fail = analyzeTab({ raw });
  const f = fail.findings.find((x) => x.rule === 'tab-positive-tabindex');
  assert.equal(f.verdict, 'FAIL');
  assert.equal(f.basis, 'best-practice');
  const warn = analyzeTab({ raw, options: { positiveTabindex: 'warn' } });
  assert.equal(warn.findings.some((x) => x.rule === 'tab-positive-tabindex'), false);
  assert.equal(warn.warnings[0].code, 'tab-positive-tabindex');
});

test('tab-unreachable: widoczny fokusowalny cel poza sekwencja to FAIL', () => {
  const raw = [step('a'), step('b', 60), step('a')];
  const r = analyzeTab({ raw, candidates: [cand('a'), cand('b'), cand('c')] });
  const un = r.findings.filter((f) => f.rule === 'tab-unreachable');
  assert.deepEqual(un.map((f) => f.selector), ['#c']);
});

test('tab-unreachable pomija tabindex < 0, disabled, inert, zamkniete details i niezaznaczone radio', () => {
  const raw = [step('a'), step('a')];
  const candidates = [
    cand('neg', { tabindex: -1 }), cand('dis', { disabled: true }), cand('ine', { inert: true }),
    cand('det', { inClosedDetails: true }), cand('hid', { hidden: true }),
    cand('r1', { radioGroup: 'g', radioChecked: false }), cand('r2', { radioGroup: 'g', radioChecked: true }), cand('r3', { radioGroup: 'g', radioChecked: false }),
  ];
  const stops = expectedStops(candidates).map((c) => c.key);
  assert.deepEqual(stops, ['r2']);
  const r = analyzeTab({ raw: [step('r2'), step('x', 60), step('r2')], candidates });
  assert.equal(rules(r).includes('tab-unreachable'), false);
  // grupa bez zaznaczenia: stopem jest pierwszy czlonek
  assert.deepEqual(expectedStops([cand('r1', { radioGroup: 'g' }), cand('r2', { radioGroup: 'g' })]).map((c) => c.key), ['r1']);
  assert.ok(raw.length);
});

test('tab-trap: ten sam element trzy razy z rzedu', () => {
  const r = analyzeTab({ raw: [step('a'), step('b', 60), step('b', 60), step('b', 60)], candidates: [cand('a'), cand('b'), cand('c')] });
  assert.equal(r.end, 'trap');
  const trap = r.findings.find((f) => f.rule === 'tab-trap');
  assert.equal(trap.verdict, 'FAIL');
  assert.equal(typeof trap.hashBasis, 'string');
  assert.equal(rules(r).includes('tab-unreachable'), false);
});

test('tab-trap: cykl zamkniety przed odwiedzeniem osiagalnych celow', () => {
  const r = analyzeTab({ raw: [step('a'), step('b', 60), step('c', 120), step('b', 60)], candidates: [cand('a'), cand('b'), cand('c'), cand('d')] });
  assert.equal(r.end, 'cycle');
  assert.equal(r.findings.some((f) => f.rule === 'tab-trap'), true);
});

test('pulapka dialogu modalnego jest prawidlowa, a cele poza nim nie sa nieosiagalne', () => {
  const raw = [step('m1', 0, 0, { inModal: true }), step('m2', 60, 0, { inModal: true }), step('m1', 0, 0, { inModal: true }), step('m2', 60, 0, { inModal: true })];
  const r = analyzeTab({ raw, candidates: [cand('m1', { inModal: true }), cand('m2', { inModal: true }), cand('page1')] });
  assert.equal(r.end, 'wrapped');
  assert.equal(r.findings.length, 0);
  const cycle = analyzeTab({ raw: [step('m0', 0, 0, { inModal: true }), step('m1', 60, 0, { inModal: true }), step('m2', 120, 0, { inModal: true }), step('m1', 60, 0, { inModal: true })], candidates: [cand('m0', { inModal: true }), cand('page1')] });
  assert.equal(cycle.end, 'cycle');
  assert.equal(cycle.findings.length, 0);
});

test('limit sekwencji daje UNVERIFIED sequence-truncated, nie PASS ani FAIL nieosiagalnosci', () => {
  const r = analyzeTab({ raw: [step('a'), step('b', 60), step('c', 120)], candidates: [cand('z')], options: { maxStops: 3 } });
  const f = r.findings.find((x) => x.rule === 'tab-unreachable');
  assert.equal(f.verdict, 'UNVERIFIED');
  assert.equal(f.reason, 'sequence-truncated');
});

test('tab-order-visual: domyslnie ostrzezenie, order.mode fail daje FAIL, para selektorow w hashBasis', () => {
  const raw = [step('a', 0, 200), step('b', 0, 0), step('a', 0, 200)];
  const warn = analyzeTab({ raw });
  assert.equal(warn.findings.length, 0);
  assert.equal(warn.warnings.some((w) => w.code === 'tab-order-visual'), true);
  const fail = analyzeTab({ raw, options: { order: { mode: 'fail' } } });
  const f = fail.findings.find((x) => x.rule === 'tab-order-visual');
  assert.equal(f.verdict, 'FAIL');
  assert.equal(f.hashBasis, '#a>#b');
});

test('tab-order-visual: tolerancja 32 px pionowo i 1 px poziomo w tym samym rzedzie', () => {
  const opt = { order: { mode: 'fail' } };
  // ten sam rzad (dy = 20 <= 32): cofniecie w poziomie o 1 px jest tolerowane, o 2 px nie
  assert.equal(order(analyzeTab({ raw: [step('a', 100, 20), step('b', 99, 0)], options: opt })).length, 0);
  assert.equal(order(analyzeTab({ raw: [step('a', 100, 20), step('b', 98, 0)], options: opt })).length, 1);
  // wyzej o 32 px to jeszcze ten sam rzad, o 33 px to cofniecie
  assert.equal(order(analyzeTab({ raw: [step('a', 0, 32), step('b', 0, 0)], options: opt })).length, 0);
  assert.equal(order(analyzeTab({ raw: [step('a', 0, 33), step('b', 0, 0)], options: opt })).length, 1);
  // zmiana tolerancji
  assert.equal(order(analyzeTab({ raw: [step('a', 0, 33), step('b', 0, 0)], options: { order: { mode: 'fail', tolerancePx: 40 } } })).length, 0);
  // normalny przebieg w dol i w prawo nie jest cofnieciem
  assert.equal(order(analyzeTab({ raw: [step('a', 0, 0), step('b', 60, 0), step('c', 0, 100)], options: opt })).length, 0);
});

test('tab-order-visual: rtl odwraca kierunek poziomy', () => {
  const opt = { order: { mode: 'fail' }, dir: 'rtl' };
  assert.equal(order(analyzeTab({ raw: [step('a', 100, 0), step('b', 40, 0)], options: opt })).length, 0);
  assert.equal(order(analyzeTab({ raw: [step('a', 100, 0), step('b', 300, 0)], options: opt })).length, 1);
  assert.equal(order(analyzeTab({ raw: [step('a', 100, 0), step('b', 300, 0)], options: { order: { mode: 'fail' } } })).length, 0);
});

test('tab-order-visual pomija kroki poza dokumentem (skip-link) i bez prostokata', () => {
  const raw = [step('skip', -999, -999, { inDocument: false }), step('a', 0, 100), step('x', 0, 0, { box: null }), step('b', 60, 100)];
  assert.equal(order(analyzeTab({ raw, options: { order: { mode: 'fail' } } })).length, 0);
});

test('tab-trap: A,B,B,B bez nieodwiedzonych celow to nadal pulapka', () => {
  const r = analyzeTab({ raw: [step('a'), step('b', 60), step('b', 60), step('b', 60)], candidates: [cand('a'), cand('b')] });
  assert.equal(r.end, 'trap');
  assert.equal(r.findings.filter((f) => f.rule === 'tab-trap').length, 1);
});

test('tab-trap: strona z jednym fokusowalnym elementem nie jest pulapka', () => {
  const r = analyzeTab({ raw: [step('a'), step('a'), step('a')], candidates: [cand('a')] });
  assert.equal(r.findings.some((f) => f.rule === 'tab-trap'), false);
});

test('tab-trap: cykl z cycleStart > 0 zamkniety bez wyjscia, takze gdy wszystko odwiedzone', () => {
  const r = analyzeTab({ raw: [step('a'), step('b', 60), step('c', 120), step('b', 60)], candidates: [cand('a'), cand('b'), cand('c')] });
  assert.equal(r.end, 'cycle');
  assert.ok(r.findings.some((f) => f.rule === 'tab-trap'));
  assert.ok(sequenceEnd([step('a'), step('b', 60), step('c', 120), step('b', 60)]).cycleStart > 0);
});

test('tab-positive-tabindex raz na element, krok powtorzony nie tworzy pary kolejnosci', () => {
  const raw = [step('a', 0, 0, { tabindex: 2 }), step('a', 0, 0, { tabindex: 2 }), step('b', 60, 0), step('a', 0, 0, { tabindex: 2 }), step('b', 60, 0)];
  const r = analyzeTab({ raw, options: { order: { mode: 'fail' } } });
  assert.equal(r.findings.filter((f) => f.rule === 'tab-positive-tabindex').length, 1);
  assert.equal(r.findings.filter((f) => f.rule === 'tab-order-visual').length, 0);
});

test('classifyMouse waliduje diffPx jak klawiatura', () => {
  assert.throws(() => classifyMouse({ mode: 'report', diffPx: -1 }));
  assert.throws(() => classifyMouse({ mode: 'report', diffPx: NaN }));
});

test('Tab wchodzi do ramki, ktorej nie da sie sledzic (outside): nieodwiedzone cele to UNVERIFIED tab-frame, nie FAIL', () => {
  const raw = [step('a'), step('b'), { key: 'outside', selector: 'outside', kind: 'outside' }];
  const r = analyzeTab({ raw, candidates: [cand('a'), cand('b'), cand('c'), cand('d')] });
  assert.equal(r.end, 'outside');
  assert.deepEqual(r.findings.map((f) => [f.rule, f.verdict, f.reason]), [['tab-unreachable', 'UNVERIFIED', 'tab-frame']]);
  assert.match(r.findings[0].message, /2 target\(s\)/);
  assert.equal(analyzeTab({ raw, candidates: [cand('a'), cand('b')] }).findings.length, 0, 'everything visited: nothing to verify');
  // koniec przez body (wrap) zachowuje stare zachowanie: nieodwiedzony cel to FAIL
  const wrapped = analyzeTab({ raw: [step('a'), { key: 'body', selector: 'body', kind: 'body' }], candidates: [cand('a'), cand('c')] });
  assert.deepEqual(wrapped.findings.map((f) => f.verdict), ['FAIL']);
});

test('host shadow DOM i same-origin iframe: kroki wewnatrz maja rozne klucze, wiec trzy przystanki hosta nie sa pulapka', () => {
  const inner = (n) => step(`h${n}`, 0, n * 30, { selector: `div > my-widget > button:nth-of-type(${n})` });
  const raw = [step('a'), inner(1), inner(2), inner(3), step('z'), step('a')];
  const r = analyzeTab({ raw, candidates: [cand('a'), cand('h1'), cand('h2'), cand('h3'), cand('z')] });
  assert.equal(r.end, 'wrapped');
  assert.deepEqual(r.findings.filter((f) => f.rule === 'tab-trap'), []);
  assert.deepEqual(r.findings.filter((f) => f.verdict === 'FAIL'), []);
});
