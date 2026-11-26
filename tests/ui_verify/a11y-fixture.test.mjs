// Acceptance fixture "contrast": samples are composed from the colours declared in page.html, not copied from expected.json.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { LARGE_TEXT_PX, compositeOver, measureSample, naiveLayerRatio, resolveThresholds } from '../../library/skills/ui-verify/scripts/lib/a11y-contrast.mjs';
import { compareExpected } from '../../library/skills/ui-verify/scripts/lib/a11y-expect.mjs';

const DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'library', 'skills', 'ui-verify', 'fixtures', 'a11y', 'contrast');
const expected = JSON.parse(fs.readFileSync(path.join(DIR, 'expected.json'), 'utf8'));
const html = fs.readFileSync(path.join(DIR, 'page.html'), 'utf8');

const hex = (h0) => {
  const h = h0.length === 4 ? `#${[...h0.slice(1)].map((c) => c + c).join('')}` : h0;
  return ({ r: parseInt(h.slice(1, 3), 16), g: parseInt(h.slice(3, 5), 16), b: parseInt(h.slice(5, 7), 16), a: 1 });
};
const rule = (sel) => new RegExp(`${sel.replace('#', '\\#')}\\{([^}]*)\\}`).exec(html)[1];
const prop = (body, name) => new RegExp(`(?:^|;)${name}:([^;]+)`).exec(body)?.[1].trim();
const parseCss = (v) => {
  const m = /^rgba\((\d+),(\d+),(\d+),(\.?\d*\.?\d+)\)$/.exec(v);
  return m ? { r: +m[1], g: +m[2], b: +m[3], a: +m[4] } : hex(v);
};
const round = (c) => [Math.round(c.r), Math.round(c.g), Math.round(c.b)];

// background colour behind each element: page white, or the backdrop colour; translucent layers are composited over it
function backgroundOf(sel) {
  const body = rule(sel);
  const own = prop(body, 'background');
  if (!sel.startsWith('#layer')) return [255, 255, 255];
  const layer = parseCss(own);
  return round(compositeOver(layer, parseCss(prop(rule('.backdrop'), 'background'))));
}

function sampleFor(sel) {
  const body = rule(sel);
  const color = parseCss(prop(body, 'color'));
  const size = prop(body, 'font-size');
  return { selector: sel, fontSizePx: size ? parseFloat(size) : parseFloat(prop(rule('.case'), 'font-size')), fontWeight: 400, foreground: color, opacity: 1, pixels: Array.from({ length: 8 }, () => backgroundOf(sel)) };
}

test('progi z expected.json zgadzaja sie z audytem (largeTextMinPx = LARGE_TEXT_PX)', () => {
  assert.equal(expected.thresholds.largeTextMinPx, LARGE_TEXT_PX);
  assert.equal(expected.thresholds.normalText, 4.5);
  assert.equal(expected.thresholds.largeText, 3);
});

test('tlo warstw skladane z kolorow strony: rgba(0,0,0,.5) i rgba(255,255,255,.4) nad #3366cc', () => {
  assert.deepEqual(backgroundOf('#layer-pass'), [26, 51, 102]);
  assert.deepEqual(backgroundOf('#layer-fail'), [133, 163, 224]);
});

test('piec elementow fikstury: pomiar z probek zgodny z expected.json (exit 0, takze przy celowych FAIL)', () => {
  const th = resolveThresholds({ normal: expected.thresholds.normalText, large: expected.thresholds.largeText });
  const findings = expected.elements.map((e) => {
    const m = measureSample(sampleFor(e.selector), th.thresholds);
    return { rule: 'contrast', selector: e.selector, verdict: m.verdict, required: m.required, measured: { ratio: m.ratio, fontSizePx: m.fontSizePx, foreground: m.foreground, effectiveBackground: m.effectiveBackground } };
  });
  assert.deepEqual(findings.map((f) => f.verdict), ['PASS', 'PASS', 'FAIL', 'PASS', 'FAIL']);
  const r = compareExpected(expected, findings, { thresholds: th.thresholds });
  assert.equal(r.exit, 0, JSON.stringify(r.entries.filter((e) => e.status !== 'match')));
  assert.equal(r.entries.length, 5);
});

test('naiwny odczyt koloru samej warstwy: w fikstury daje te same werdykty (opis), a przypadek z roznica jest osobny', () => {
  // W fikstury naiwny odczyt zgadza sie z pomiarem co do werdyktu: warstwa rgba(0,0,0,.5) odczytana bez alfy to czern
  // (21:1, PASS jak 12,30), warstwa rgba(255,255,255,.4) to biel pod bialym tekstem (1:1, FAIL jak 2,52).
  assert.equal(naiveLayerRatio({ r: 0, g: 0, b: 0 }, { r: 255, g: 255, b: 255 }) >= 4.5, true);
  assert.equal(naiveLayerRatio({ r: 255, g: 255, b: 255 }, { r: 255, g: 255, b: 255 }) < 4.5, true);
  // Przypadek z roznica: biale slowo na rgba(0,0,0,.15) nad bialym. Naiwnie czern = 21:1 PASS, naprawde tlo 217 = FAIL.
  const layer = { r: 0, g: 0, b: 0, a: 0.15 };
  const effective = round(compositeOver(layer, { r: 255, g: 255, b: 255 }));
  const naive = naiveLayerRatio({ r: 0, g: 0, b: 0 }, { r: 255, g: 255, b: 255 });
  const real = measureSample({ selector: '#x', fontSizePx: 16, foreground: { r: 255, g: 255, b: 255, a: 1 }, pixels: [effective] }, undefined);
  assert.ok(naive >= 4.5);
  assert.equal(real.verdict, 'FAIL');
});

// ---------- focus-contrast.html: the hand-computed numbers of expected-focus.json are consistent (no browser) ----------

const FIXTURE_DIR = path.resolve(DIR, '..');
const focus = JSON.parse(fs.readFileSync(path.join(FIXTURE_DIR, 'expected-focus.json'), 'utf8'));
const focusHtml = fs.readFileSync(path.join(FIXTURE_DIR, 'focus-contrast.html'), 'utf8');

test('focus-contrast.html: expected-focus.json lists exactly the targets of the page', () => {
  const ids = [...focusHtml.matchAll(/<(?:button|a)\b[^>]*\bid="([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(focus.elements.map((e) => e.id).sort(), ids.sort());
});

test('expected-focus.json: area, threshold, contrast and verdicts follow from the written formulas', () => {
  const measured = focus.elements.filter((e) => e.ring);
  assert.equal(measured.length, 10);
  for (const e of measured) {
    const { width: w, height: h, radius } = e.box;
    const r = Math.min(radius, Math.min(w, h) / 2);
    const area = 2 * (2 * (w + h) - (8 - 2 * Math.PI) * r);
    assert.ok(Math.abs(area - e.requiredArea) <= focus.numberTolerance, `${e.id} requiredArea ${area}`);
    assert.ok(Math.abs(area - 16 - 0.05 * area - e.threshold) <= focus.numberTolerance, `${e.id} threshold`);
    const ratio = naiveLayerRatio({ r: e.ring.color[0], g: e.ring.color[1], b: e.ring.color[2] }, { r: 255, g: 255, b: 255 });
    assert.ok(Math.abs(ratio - e.ring.ratio) <= 0.005, `${e.id} ratio ${ratio}`);
    assert.equal(e.contrast.verdict, ratio >= 3 ? 'PASS' : 'FAIL', `${e.id} contrast`);
    const qualifying = e.ring.qualifying ?? e.ring.area;
    assert.equal(qualifying, ratio >= 3 ? e.ring.area : 0, `${e.id} qualifying`);
    if (e.appearance.verdict !== 'UNVERIFIED') assert.equal(e.appearance.verdict, qualifying >= e.threshold ? 'PASS' : 'FAIL', `${e.id} appearance`);
  }
});

// the declarations of one rule of the page, by its whole selector
const focusRule = (selector) => {
  const found = focusHtml.split('\n').find((line) => line.startsWith(`${selector}{`));
  assert.ok(found, `rule ${selector}`);
  return found.slice(selector.length + 1, found.indexOf('}'));
};
const px = (selector, name) => parseFloat(prop(focusRule(selector), name));
const tag = (id) => new RegExp(`<button\\b[^>]*\\bid="${id}"[^>]*>`).exec(focusHtml)[0];
const expectation = (id) => focus.elements.find((e) => e.id === id);

test('#blur-border: no indicator with focus, the border appears on blur only; the documented false PASS follows from the page', () => {
  assert.equal(prop(focusRule('#blur-border:focus'), 'outline'), 'none');
  const e = expectation('blur-border');
  const button = tag('blur-border');
  const colour = (handler) => new RegExp(`${handler}="this\\.style\\.borderColor='(#[0-9a-f]+)'"`).exec(button)?.[1];
  assert.deepEqual([colour('onfocus'), colour('onblur')], ['#fff', '#c00']);
  assert.deepEqual(hex(colour('onblur')), { r: e.ring.color[0], g: e.ring.color[1], b: e.ring.color[2], a: 1 });
  const border = parseFloat(prop(focusRule('#blur-border'), 'border'));
  const { width: w, height: h } = e.box;
  assert.equal(e.ring.area, w * h - (w - 2 * border) * (h - 2 * border));
  assert.deepEqual([e.keyboard, e.contrast.verdict, e.appearance.verdict], ['PASS', 'PASS', 'PASS']);
  // only its own style is touched, and the page loads nothing
  assert.ok(!/getElementById|querySelector|document\./.test(focusHtml));
  assert.ok(!/<(script|link|img|iframe)\b|url\(|https?:/.test(focusHtml));
});

test('#sr-only: a 1 x 1 control with the ring on its neighbour, inside the capture padding', () => {
  const e = expectation('sr-only');
  assert.deepEqual([px('#sr-only', 'width'), px('#sr-only', 'height')], [1, 1]);
  assert.equal(prop(focusRule('#sr-only:focus'), 'outline'), 'none');
  const ring = parseFloat(prop(focusRule('#sr-only:focus+#sr-twin'), 'outline'));
  const [w, h] = [px('#sr-twin', 'width'), px('#sr-twin', 'height')];
  assert.equal(e.ringBeside.area, (w + 2 * ring) * (h + 2 * ring) - w * h);
  const at = (id) => {
    const style = new RegExp(`id="${id}"[^>]*style="left:(\\d+)px;top:(\\d+)px"`).exec(focusHtml);
    return [Number(style[1]), Number(style[2])];
  };
  const [[x, y], [tx, ty]] = [at('sr-only'), at('sr-twin')];
  assert.ok(tx - ring >= x - 8 && tx + w + ring <= x + 1 + 8 && ty - ring >= y - 8 && ty + h + ring <= y + 1 + 8, 'the whole ring is captured');
  assert.ok(tx - ring >= x + 1, 'the ring is beside the control');
  assert.deepEqual([e.keyboard, e.contrast.verdict, e.appearance.verdict, e.appearance.reason], ['PASS', 'PASS', 'UNVERIFIED', 'shape-unknown']);
});
