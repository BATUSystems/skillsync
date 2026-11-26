// Acceptance fixture "contrast": samples are composed from the colours declared in page.html, not copied from expected.json.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { LARGE_TEXT_PX, compositeOver, measureSample, naiveLayerRatio, resolveThresholds } from '../../library/skills/ui-verify/scripts/lib/a11y-contrast.mjs';
import { compareExpected } from '../../library/skills/ui-verify/scripts/lib/a11y-expect.mjs';
import { validateA11yConfig } from '../../library/skills/ui-verify/scripts/lib/a11y-config.mjs';

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

// ---------- non-text.html: expected-nontext.json is consistent with the page (no browser) ----------

const nonText = JSON.parse(fs.readFileSync(path.join(FIXTURE_DIR, 'expected-nontext.json'), 'utf8'));
const nonTextHtml = fs.readFileSync(path.join(FIXTURE_DIR, 'non-text.html'), 'utf8');

// WCAG 2.x, written here on purpose: the expected numbers are not checked with the code under test.
const luminance = ({ r, g, b }) => {
  const [lr, lg, lb] = [r, g, b].map((c) => (c / 255 <= 0.04045 ? c / 255 / 12.92 : ((c / 255 + 0.055) / 1.055) ** 2.4));
  return 0.2126 * lr + 0.7152 * lg + 0.0722 * lb;
};
const wcag = (a, b) => (Math.max(luminance(a), luminance(b)) + 0.05) / (Math.min(luminance(a), luminance(b)) + 0.05);

// all declarations of the page that apply to an element by its classes and then its id (the later one wins)
function nonTextStyle(id) {
  const tag = new RegExp(`<[a-z]+\\b[^>]*\\bid="${id}"[^>]*>`).exec(nonTextHtml)[0];
  const classes = /\bclass="([^"]+)"/.exec(tag)?.[1].split(' ') ?? [];
  const out = {};
  for (const selector of [...classes.map((c) => `.${c}`), `#${id}`]) {
    for (const line of nonTextHtml.split('\n').filter((l) => l.startsWith(`${selector}{`))) {
      for (const declaration of line.slice(selector.length + 1, line.indexOf('}')).split(';')) {
        const at = declaration.indexOf(':');
        out[declaration.slice(0, at)] = declaration.slice(at + 1);
      }
    }
  }
  return out;
}
const nonTextIds = () => [...nonTextHtml.matchAll(/<(?:input|button|svg|img|textarea|select|a)\b[^>]*\bid="([^"]+)"/g)].map((m) => m[1]);
const lastColour = (value) => /#[0-9a-f]+$/.exec(value ?? '')?.[0];

test('non-text.html: expected-nontext.json lists every candidate of the page, each once', () => {
  const listed = [...nonText.elements.map((e) => e.id), ...nonText.silent];
  assert.equal(new Set(listed).size, listed.length);
  assert.deepEqual([...listed].sort(), nonTextIds().sort());
  assert.equal([...nonTextHtml.matchAll(/<(?:input|button|svg|img|textarea|select|a)\b/g)].length, listed.length, 'no candidate tag without an id');
  // the silent controls are exactly the buttons whose only content is one graphic
  const iconButtons = [...nonTextHtml.matchAll(/<button\b[^>]*\bid="([^"]+)"[^>]*>(?:<svg\b(?:(?!<\/svg>).)*<\/svg>|<img\b[^>]*>)<\/button>/g)].map((m) => m[1]);
  assert.deepEqual(nonText.silent.slice().sort(), [...iconButtons, 'slot-button'].sort());
});

test('non-text.html: every verdict, kind and reason is complete', () => {
  for (const e of nonText.elements) {
    assert.equal(e.anyOf, undefined, `${e.id}: one exact verdict`);
    assert.ok(['PASS', 'FAIL', 'EXEMPT', 'UNVERIFIED'].includes(e.verdict), e.id);
    const measured = e.verdict === 'PASS' || e.verdict === 'FAIL';
    assert.equal(Boolean(e.kind && e.measured && e.colours), measured, `${e.id}: numbers exactly for measured verdicts`);
    assert.equal(Boolean(e.reason), !measured, `${e.id}: a reason exactly for the others`);
  }
});

test('P33 non-text.html: rounded controls are never FAIL, the pale rectangular field stays FAIL', () => {
  // straight edges of the sides below half of the perimeter of the rectangle
  const rounded = (id) => {
    const s = nonTextStyle(id);
    const [w, h] = [parseFloat(s.width), parseFloat(s.height)];
    const text = s['border-radius'] ?? '0';
    const r = Math.min(text.endsWith('%') ? (parseFloat(text) * Math.min(w, h)) / 100 : parseFloat(text), w / 2, h / 2);
    return 2 * Math.max(0, w - 2 * r) + 2 * Math.max(0, h - 2 * r) < w + h;
  };
  const ids = ['radio-round', 'radio-round-pale', 'checkbox-pill'];
  assert.deepEqual(nonText.elements.filter((e) => nonTextStyle(e.id)['border-radius'] && rounded(e.id)).map((e) => e.id), ids);
  const brief = (id) => { const e = nonText.elements.find((x) => x.id === id); return [e.verdict, e.reason ?? e.kind]; };
  assert.deepEqual(ids.map(brief), [['UNVERIFIED', 'shape-unknown'], ['UNVERIFIED', 'shape-unknown'], ['PASS', 'boundary']]);
  assert.equal(lastColour(nonTextStyle('radio-round').border), '#000');
  assert.equal(nonTextStyle('radio-round-pale')['border-color'], '#ccc');
  assert.equal(rounded('field-pale'), false);
  assert.deepEqual(brief('field-pale'), ['FAIL', 'boundary']);
});

test('non-text.html: the slot, focus and hidden-input cases are what the expectations say', () => {
  const brief = (id) => { const e = nonText.elements.find((x) => x.id === id); return [e.verdict, e.reason ?? e.kind]; };
  assert.deepEqual(['icon-slot', 'field-focus', 'label-check', 'orphan-check'].map(brief),
    [['FAIL', 'icon'], ['UNVERIFIED', 'shape-unknown'], ['UNVERIFIED', 'pseudo-element-control'], ['EXEMPT', 'not-rendered']]);
  assert.equal([...nonTextHtml.matchAll(/\bautofocus\b/g)].length, 1);
  assert.match(nonTextHtml, /<input\b[^>]*\bid="field-focus"[^>]*\bautofocus>/);
  assert.match(nonTextHtml, /<template shadowrootmode="open">/);
  assert.match(nonTextHtml, /<label id="hidden-label"><input id="label-check" type="checkbox" hidden>[^<]+<\/label>/);
  assert.match(nonTextHtml, /<div><input id="orphan-check" type="checkbox" hidden><\/div>/);
});

test('non-text.html: measured ratios and verdicts follow from the colours of the page', () => {
  const white = hex('#fff');
  assert.match(nonTextHtml, /^html,body\{margin:0;background:#fff;/m);
  const measured = nonText.elements.filter((e) => e.measured && e.note !== 'sampled');
  assert.equal(measured.length, 9);
  for (const e of measured) {
    const c = e.colours;
    assert.deepEqual(hex(c.outside), white, `${e.id}: surroundings are the white page`);
    const style = nonTextStyle(e.id);
    if (e.id === 'icon-slot') {
      assert.match(nonTextHtml, /<button id="slot-button"[^>]*><slot><\/slot><\/button><\/template><svg id="icon-slot"[^>]*><path fill="currentColor"/);
      assert.equal(nonTextStyle('slot-host').color, c.icon, e.id);
    } else if (e.kind === 'icon') {
      const button = new RegExp(`<button\\b[^>]*\\bid="([^"]+)"[^>]*><svg id="${e.id}"[^>]*><path fill="currentColor"`).exec(nonTextHtml)?.[1];
      assert.ok(button, `${e.id} paints with currentColor inside a button`);
      assert.equal(nonTextStyle(button).color, c.icon, e.id);
    } else {
      assert.equal(style.background, c.fill, `${e.id} fill`);
      assert.equal(lastColour(style.border), c.border, `${e.id} border`);
      assert.equal(e.measured.border === null, c.border === undefined, e.id);
    }
    for (const name of Object.keys(e.measured).filter((k) => k !== 'offContrast' && e.measured[k] !== null)) {
      assert.ok(Math.abs(wcag(hex(c[name]), white) - e.measured[name]) <= 0.001, `${e.id} ${name}: ${wcag(hex(c[name]), white)}`);
    }
    if (e.measured.offContrast !== undefined) {
      assert.equal(nonTextStyle(e.id.replace('tab-on', 'tab-off')).background, c.off, `${e.id} off sibling`);
      assert.ok(Math.abs(wcag(hex(c.fill), hex(c.off)) - e.measured.offContrast) <= 0.001, `${e.id} offContrast`);
    }
    const best = Math.max(...Object.entries(e.measured).filter(([k, v]) => k !== 'offContrast' && v !== null).map(([, v]) => v));
    assert.equal(e.verdict, best >= nonText.required ? 'PASS' : 'FAIL', e.id);
  }
});

test('#field-gradient: the sampled ratios follow from the gradient and the geometry of the page', () => {
  const e = nonText.elements.find((x) => x.id === 'field-gradient');
  const panel = nonTextStyle('shade-panel');
  const stops = /^linear-gradient\(90deg,(#[0-9a-f]+),(#[0-9a-f]+)\)$/.exec(panel.background);
  const [from, to] = [hex(stops[1]).r, hex(stops[2]).r];
  const field = nonTextStyle(e.id);
  const [left, width, panelWidth] = [parseFloat(field.left), parseFloat(field.width), parseFloat(panel.width)];
  const grey = (v) => ({ r: v, g: v, b: v });
  const column = (x) => Math.round(from + ((to - from) * (x + 0.5)) / panelWidth);
  const darkest = column(left + width + 1);
  const lightest = column(left - 2);
  assert.deepEqual([hex(e.colours.darkest), hex(e.colours.lightest)].map((c) => c.r), [darkest, lightest]);
  assert.ok(Math.abs(wcag(hex(e.colours.border), grey(darkest)) - e.measured.border) <= 0.001);
  assert.ok(Math.abs(wcag(hex(e.colours.fill), grey(lightest)) - e.measured.fill) <= 0.001);
  assert.equal(lastColour(field.border), e.colours.border);
  assert.equal(nonTextStyle(e.id).background, e.colours.fill);
  assert.ok(e.measured.border >= nonText.required && e.measured.fill < nonText.required && e.verdict === 'PASS');
});

test('non-text.html: positioned elements keep at least 20 px apart, so no outer ring meets another element', () => {
  const panels = { 'tab-on-pass': 'tabs-pass', 'tab-off-pass': 'tabs-pass', 'tab-on-fail': 'tabs-fail', 'tab-off-fail': 'tabs-fail', 'field-gradient': 'shade-panel' };
  // elements inside a button or a label are placed with their container
  const placed = nonTextIds().filter((id) => nonTextStyle(id).left !== undefined);
  assert.equal(placed.length, 20);
  const boxes = [...placed, 'pseudo-label', 'slot-host', 'hidden-label'].map((id) => {
    const s = nonTextStyle(id);
    const parent = panels[id] ? nonTextStyle(panels[id]) : { left: '0', top: '0' };
    const x = parseFloat(s.left) + parseFloat(parent.left);
    const y = parseFloat(s.top) + parseFloat(parent.top);
    // the native checkbox is 13 px, the loose icon 24 px and the label with its text at most 160 x 32 px
    const fallback = { 'native-check': [20, 20], 'icon-decor': [24, 24], 'pseudo-label': [160, 32], 'hidden-label': [160, 32] }[id] ?? [];
    const [w, h] = [parseFloat(s.width ?? fallback[0]), parseFloat(s.height ?? fallback[1])];
    assert.ok([x, y, w, h].every(Number.isInteger), `${id}: ${[x, y, w, h]}`);
    return { id, x, y, w, h };
  });
  for (const [i, a] of boxes.entries()) {
    assert.ok(a.x >= 2 && a.y >= 2 && a.x + a.w <= 1000 && a.y + a.h <= 600, `${a.id} inside the first viewport`);
    for (const b of boxes.slice(i + 1)) {
      const gap = Math.max(b.x - (a.x + a.w), a.x - (b.x + b.w), b.y - (a.y + a.h), a.y - (b.y + b.h));
      assert.ok(gap >= 20, `${a.id} and ${b.id}: ${gap} px`);
    }
  }
});

test('non-text fixtures load nothing, and non-text-empty.html has no candidate', () => {
  const empty = fs.readFileSync(path.join(FIXTURE_DIR, 'non-text-empty.html'), 'utf8');
  for (const page of [nonTextHtml, empty]) {
    assert.ok(!/<(script|link|iframe)\b|https?:|url\((?!#)/.test(page));
    assert.ok([...page.matchAll(/\bsrc="([^"]*)"/g)].every((m) => m[1].startsWith('data:image/png;base64,')));
  }
  assert.ok(!/<(input|textarea|select|button|a|svg|img|canvas)\b|\brole=|\baria-/.test(empty));
  for (const name of ['non-text', 'non-text-empty']) {
    const config = JSON.parse(fs.readFileSync(path.join(FIXTURE_DIR, `${name}.config.json`), 'utf8'));
    assert.deepEqual(config.pages.map((p) => [p.id, p.url, p.checks]), [[name, `${name}.html`, ['non-text']]]);
    assert.ok(validateA11yConfig(config, { configDir: FIXTURE_DIR, env: {} }).ok, name);
  }
});
