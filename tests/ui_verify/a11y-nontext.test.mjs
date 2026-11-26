// CORE-1150-T2B B1: pure logic of the non-text contrast check (WCAG 1.4.11): regions, verdicts, vocabulary, keys.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  componentContrast, componentRegions, iconContrast, iconRegions, regionPoints, settleNonText, stateContrast,
  truncatedFinding, visibleBorderWidths, MIN_REGION_PIXELS,
} from '../../library/skills/ui-verify/scripts/lib/a11y-nontext.mjs';
import { contrastRatio, measureSample, resolveThresholds, worstRatio } from '../../library/skills/ui-verify/scripts/lib/a11y-contrast.mjs';
import {
  CHECKS, CONFIRMABLE_REASONS, DEFAULT_CHECKS, EXEMPT_REASONS, LIMITS, LIMIT_REASONS, NONTEXT_KINDS, RULES,
} from '../../library/skills/ui-verify/scripts/lib/a11y-schema.mjs';
import { validateA11yConfig } from '../../library/skills/ui-verify/scripts/lib/a11y-config.mjs';
import { applyExceptions, findingKey, parseExceptionsFile } from '../../library/skills/ui-verify/scripts/lib/a11y-exceptions.mjs';
import { assembleReport } from '../../library/skills/ui-verify/scripts/a11y.mjs';
import { buildReport, renderMarkdown } from '../../library/skills/ui-verify/scripts/lib/a11y-report.mjs';
import { gatherPixels } from '../../library/skills/ui-verify/scripts/lib/a11y-page.mjs';

const NOW = '2026-11-26';
const grey = (v) => [v, v, v];
const rgb = (v) => ({ r: v, g: v, b: v });
const WHITE = 255;
const PALE = 221; // 1.36:1 on white
const MID = 117; // 4.61:1 on white
const BOX = { x: 20, y: 20, width: 60, height: 30 };
const ONE = { top: 1, right: 1, bottom: 1, left: 1 };
const NONE = { top: 0, right: 0, bottom: 0, left: 0 };

// A 120x80 grey image: page colour, then the border box painted with `border` and its inside with `fill`.
function paint({ page = WHITE, border = null, fill = page, box = BOX, widths = ONE, extra = [] } = {}) {
  const width = 120;
  const height = 80;
  const data = new Uint8Array(width * height * 4).fill(255);
  const rect = (x, y, w, h, v) => {
    for (let j = y; j < y + h; j++) for (let i = x; i < x + w; i++) data.fill(v, (j * width + i) * 4, (j * width + i) * 4 + 3);
  };
  rect(0, 0, width, height, page);
  rect(box.x, box.y, box.width, box.height, border ?? fill);
  if (border !== null) rect(box.x + widths.left, box.y + widths.top, box.width - widths.left - widths.right, box.height - widths.top - widths.bottom, fill);
  for (const e of extra) rect(...e);
  return { width, height, data };
}

function sample(img, rects, exclude = []) {
  const { points, total, excluded, outside } = regionPoints(rects, { exclude, bounds: img });
  const pixels = [];
  gatherPixels(img, { x: 0, y: 0 }, points, pixels);
  return { pixels, total, excluded, outside };
}

function component(img, { box = BOX, borderWidths = ONE, exclude = [] } = {}, over = {}) {
  const r = componentRegions({ box, borderWidths });
  assert.equal(r.ok, true, r.reason);
  return { band: r.band && sample(img, r.band), fill: sample(img, r.fill), ring: sample(img, r.ring, exclude), ...over };
}

const flat = (n, v) => ({ pixels: Array.from({ length: n }, () => grey(v)), total: n, excluded: 0, outside: 0 });

test('vocabulary: non-text is selectable but not default, rule, reasons, limit', () => {
  assert.deepEqual([...DEFAULT_CHECKS], ['contrast', 'target-size', 'focus', 'tab-order', 'console']);
  assert.deepEqual([...CHECKS], [...DEFAULT_CHECKS, 'non-text']);
  assert.ok(RULES.includes('non-text-contrast'));
  assert.deepEqual([...NONTEXT_KINDS], ['boundary', 'icon', 'state']);
  for (const r of ['not-rendered', 'inactive', 'decorative', 'text-identifies', 'user-agent']) assert.ok(EXEMPT_REASONS.includes(r), r);
  for (const r of ['pseudo-element-control', 'raster-graphic', 'complex-graphic', 'shape-unknown', 'occluded', 'clipped', 'filter-or-blend', 'media-background', 'opacity-group-with-paint']) {
    assert.ok(CONFIRMABLE_REASONS.includes(r) && !LIMIT_REASONS.includes(r), r);
  }
  assert.equal(LIMITS.nonText, 2000);
});

test('config: default checks unchanged, non-text accepted explicitly, nothing added to thresholds', () => {
  const v = (extra, page = {}) => validateA11yConfig({ version: 1, pages: [{ id: 'p', url: 'a.html', ...page }], ...extra }, { configDir: '/tmp/cfg' });
  assert.deepEqual(v({}).config.pages[0].checks, [...DEFAULT_CHECKS]);
  assert.deepEqual(v({}, { checks: ['non-text'] }).config.pages[0].checks, ['non-text']);
  assert.deepEqual(v({ thresholds: { normal: 7 } }).config.thresholds, { normal: 7, large: 3 });
  assert.deepEqual(v({ thresholds: { nonText: 4.5 } }).config.thresholds, { normal: 4.5, large: 3, nonText: 4.5 });
  assert.deepEqual(v({ thresholds: { nonText: 4.5 } }).config.warnings, []);
});

test('thresholds.nonText: 1..21, lowering below 3 needs a reason and warns; text measurement is unaffected', () => {
  for (const bad of [0.5, 22, '3', NaN, null]) assert.equal(resolveThresholds({ nonText: bad }).ok, false, String(bad));
  assert.equal(resolveThresholds({ nonText: 2 }).ok, false);
  const low = resolveThresholds({ nonText: 2, reason: 'accepted' });
  assert.equal(low.ok, true);
  assert.equal(low.warnings[0].code, 'threshold-lowered');
  assert.match(low.warnings[0].message, /nonText=2/);
  assert.equal(low.thresholds.nonText, 2);
  assert.deepEqual(resolveThresholds({ nonText: 3 }).warnings, []);
  assert.equal(Object.hasOwn(resolveThresholds({}).thresholds, 'nonText'), false);
  const s = { selector: '#x', fontSizePx: 16, foreground: { ...rgb(MID), a: 1 }, pixels: [grey(WHITE)] };
  assert.deepEqual(measureSample(s, low.thresholds), measureSample(s, undefined));
});

test('worstRatio: minimum, or the 1st percentile of a varying background with 100 samples or more', () => {
  const ratios = Array.from({ length: 200 }, (_, i) => i + 1);
  assert.equal(worstRatio(ratios, 'varying'), 3);
  assert.equal(worstRatio(ratios, 'solid'), 1);
  assert.equal(worstRatio(ratios.slice(0, 99), 'varying'), 1);
});

test('visibleBorderWidths: only a visible style of at least 1 px makes a band', () => {
  assert.deepEqual(visibleBorderWidths({
    top: { width: 2, style: 'solid' }, right: { width: 2, style: 'none' }, bottom: { width: 0.5, style: 'solid' }, left: { width: 3, style: 'hidden' },
  }), { top: 2, right: 0, bottom: 0, left: 0 });
});

test('componentRegions: band, fill without band and 2 px margin, 2 px outer ring', () => {
  const r = componentRegions({ box: BOX, borderWidths: ONE });
  assert.deepEqual(r.band, [[20, 20, 60, 1], [20, 49, 60, 1], [20, 21, 1, 28], [79, 21, 1, 28]]);
  assert.deepEqual(r.fill, [[23, 23, 54, 24]]);
  assert.deepEqual(r.ring, [[18, 18, 64, 2], [18, 50, 64, 2], [18, 20, 2, 30], [80, 20, 2, 30]]);
  assert.equal(componentRegions({ box: BOX, borderWidths: NONE }).band, null);
  assert.deepEqual(componentRegions({ box: BOX, borderWidths: { ...NONE, bottom: 2 } }).band, [[20, 48, 60, 2]]);
});

test('componentRegions: degenerate input is shape-unknown', () => {
  const cases = [
    { box: { ...BOX, width: 0 } }, { box: { ...BOX, height: -1 } }, { box: { ...BOX, x: NaN } }, { box: { ...BOX, width: Infinity } },
    { box: { ...BOX, width: 6 } }, { box: { ...BOX, height: 5 } }, // smaller than border and margins
    { box: null }, { borderWidths: { ...ONE, top: NaN } }, { borderWidths: { ...ONE, left: -1 } }, { borderWidths: null },
    { borderWidths: { ...ONE, top: 40 } },
  ];
  for (const over of cases) {
    const r = componentRegions({ box: BOX, borderWidths: ONE, ...over });
    assert.deepEqual([r.ok, r.reason], [false, 'shape-unknown'], JSON.stringify(over));
  }
});

test('regionPoints: pixel centres, exclusions, page bounds and a cap that keeps thin strips', () => {
  const r = regionPoints([[0.4, 0, 2, 1]], { bounds: { width: 10, height: 10 } });
  assert.deepEqual(r.points, [0, 0, 1, 0]);
  const x = regionPoints([[-2, 0, 4, 1]], { exclude: [[1, 0, 5, 5]], bounds: { width: 10, height: 10 } });
  assert.deepEqual([x.total, x.outside, x.excluded, x.points], [4, 2, 1, [0, 0]]);
  const big = regionPoints([[0, 0, 100000, 1], [0, 0, 1, 100000], [0, 5, 3000, 3000]], { bounds: { width: 200000, height: 200000 } });
  assert.ok(big.total > 1000 && big.total < 20000, String(big.total));
  const ys = new Set(); for (let i = 0; i < big.points.length; i += 2) if (big.points[i] === 0) ys.add(big.points[i + 1]);
  assert.ok(ys.size > 40, String(ys.size));
  assert.equal(regionPoints([[0, 0, 0.3, 0.3]], {}).total, 0);
});

test('component: a contrasting border alone passes', () => {
  const f = componentContrast(component(paint({ border: MID })));
  assert.equal(f.verdict, 'PASS');
  assert.deepEqual([f.rule, f.required, f.measured.kind], ['non-text-contrast', 3, 'boundary']);
  assert.ok(Math.abs(f.measured.border - 4.61) < 0.01);
  assert.equal(f.measured.fill, 1);
});

test('component: a contrasting fill alone passes, with or without a pale border', () => {
  assert.equal(componentContrast(component(paint({ fill: MID }), { borderWidths: NONE })).verdict, 'PASS');
  assert.equal(componentContrast(component(paint({ border: PALE, fill: MID }))).verdict, 'PASS');
});

test('component: both pale fails with three ratios, kind and the best one in the note', () => {
  const f = componentContrast(component(paint({ border: PALE, fill: 250 })));
  assert.equal(f.verdict, 'FAIL');
  assert.equal(f.measured.kind, 'boundary');
  assert.ok(Math.abs(f.measured.border - contrastRatio(rgb(PALE), rgb(WHITE))) < 0.001);
  assert.ok(Math.abs(f.measured.fill - contrastRatio(rgb(250), rgb(WHITE))) < 0.001);
  assert.ok(Math.abs(f.measured.borderFill - contrastRatio(rgb(PALE), rgb(250))) < 0.001);
  assert.equal(f.measured.outside, 'rgb(255,255,255)');
  assert.match(f.note, /border 1\.3/);
});

test('component: border contrasting only with the fill still fails', () => {
  // dark page, dark-ish border and white fill would pass by fill; here both are close to the page, far from each other
  const f = componentContrast({ band: flat(40, 120), fill: flat(40, 40), ring: flat(40, 75) });
  assert.equal(f.verdict, 'FAIL');
  assert.ok(f.measured.borderFill > 3);
});

test('component: the configured threshold applies', () => {
  const input = component(paint({ border: MID }));
  assert.equal(componentContrast({ ...input, thresholds: { nonText: 7 } }).verdict, 'FAIL');
  assert.equal(componentContrast({ ...input, thresholds: { nonText: 7 } }).required, 7);
  for (const bad of [0, NaN, 30]) assert.throws(() => componentContrast({ ...input, thresholds: { nonText: bad } }), /nonText/);
});

test('component: varying surroundings use the 1st percentile and are noted as sampled', () => {
  const ring = { pixels: [...flat(190, WHITE).pixels, ...flat(10, MID).pixels], total: 200, excluded: 0, outside: 0 };
  const f = componentContrast({ band: flat(40, MID), fill: flat(40, WHITE), ring });
  assert.equal(f.verdict, 'FAIL');
  assert.match(f.note, /sampled/);
  const few = { pixels: [...flat(999, WHITE).pixels, grey(MID)], total: 1000, excluded: 0, outside: 0 };
  assert.equal(componentContrast({ band: flat(40, MID), fill: flat(40, WHITE), ring: few }).verdict, 'PASS');
});

test('component: a region without a dominant colour can not give PASS or FAIL by itself', () => {
  const noise = { pixels: Array.from({ length: 100 }, (_, i) => grey(i)), total: 100, excluded: 0, outside: 0 };
  const u = componentContrast({ band: null, fill: noise, ring: flat(100, WHITE) });
  assert.deepEqual([u.verdict, u.reason], ['UNVERIFIED', 'media-background']);
  assert.equal(componentContrast({ band: flat(40, PALE), fill: noise, ring: flat(100, WHITE) }).verdict, 'UNVERIFIED');
  assert.equal(componentContrast({ band: flat(40, MID), fill: noise, ring: flat(100, WHITE) }).verdict, 'PASS');
});

test('component: occluded or clipped surroundings are UNVERIFIED', () => {
  const img = paint({ border: MID });
  const covered = componentContrast(component(img, { exclude: [[0, 0, 120, 40], [0, 0, 60, 80]] }));
  assert.deepEqual([covered.verdict, covered.reason], ['UNVERIFIED', 'occluded']);
  assert.equal(componentContrast(component(img, { exclude: [[0, 0, 120, 20]] })).verdict, 'PASS');
  const box = { x: 0, y: 0, width: 60, height: 30 };
  const edge = componentContrast(component(paint({ border: MID, box }), { box: { ...box, x: -30 } }));
  assert.deepEqual([edge.verdict, edge.reason], ['UNVERIFIED', 'clipped']);
});

test('component: degenerate samples are UNVERIFIED without measured.kind, even next to a passing region', () => {
  const ok = { band: flat(40, MID), fill: flat(40, MID), ring: flat(40, WHITE) };
  const few = MIN_REGION_PIXELS - 1;
  const bad = [
    [{ fill: flat(0, MID) }, 'shape-unknown'], [{ fill: flat(few, MID) }, 'shape-unknown'], [{ band: flat(few, MID) }, 'shape-unknown'],
    [{ ring: flat(0, WHITE) }, 'shape-unknown'], [{ ring: { pixels: [], total: 40, excluded: 0, outside: 0 } }, 'no-samples'],
    [{ fill: { pixels: flat(20, MID).pixels, total: 40, excluded: 0, outside: 0 } }, 'no-samples'],
    [{ fill: { pixels: flat(20, MID).pixels, total: 40, excluded: 0, outside: 20 } }, 'clipped'],
    [{ fill: undefined }, 'no-samples'], [{ ring: undefined }, 'no-samples'], [{ band: undefined }, 'no-samples'],
    [{ fill: { pixels: [...flat(39, MID).pixels, [NaN, 0, 0]], total: 40, excluded: 0, outside: 0 } }, 'no-samples'],
    [{ ring: { pixels: [...flat(39, WHITE).pixels, [1, 2]], total: 40, excluded: 0, outside: 0 } }, 'no-samples'],
    [{ ring: { ...flat(40, WHITE), total: NaN } }, 'no-samples'],
  ];
  for (const [over, reason] of bad) {
    const f = componentContrast({ ...ok, ...over });
    assert.deepEqual([f.verdict, f.reason, f.measured, f.rule], ['UNVERIFIED', reason, undefined, 'non-text-contrast'], reason);
  }
  assert.deepEqual([componentContrast({}).verdict, componentContrast().verdict], ['UNVERIFIED', 'UNVERIFIED']);
});

const icon = (img, box = { x: 40, y: 20, width: 16, height: 16 }) => {
  const r = iconRegions(box);
  assert.equal(r.ok, true);
  return { icon: sample(img, r.icon), ring: sample(img, r.ring) };
};

test('icon: glyph colour against the surroundings passes and fails', () => {
  const draw = (v, page = WHITE) => paint({ page, box: { x: 44, y: 24, width: 8, height: 8 }, fill: v });
  const pass = iconContrast(icon(draw(MID)));
  assert.deepEqual([pass.verdict, pass.measured.kind], ['PASS', 'icon']);
  const fail = iconContrast(icon(draw(PALE)));
  assert.deepEqual([fail.verdict, fail.measured.kind, fail.measured.outside], ['FAIL', 'icon', 'rgb(255,255,255)']);
  assert.ok(fail.measured.icon < 3);
});

test('icon: invisible, tiny, multi-shade or degenerate graphics are UNVERIFIED', () => {
  const invisible = iconContrast(icon(paint({ box: { x: 44, y: 24, width: 8, height: 8 }, fill: WHITE })));
  assert.deepEqual([invisible.verdict, invisible.reason], ['UNVERIFIED', 'no-samples']);
  const speck = iconContrast(icon(paint({ box: { x: 44, y: 24, width: 2, height: 2 }, fill: 0 })));
  assert.deepEqual([speck.verdict, speck.reason], ['UNVERIFIED', 'no-samples']);
  const shades = { pixels: Array.from({ length: 100 }, (_, i) => grey(i)), total: 100, excluded: 0, outside: 0 };
  const many = iconContrast({ icon: shades, ring: flat(100, WHITE) });
  assert.deepEqual([many.verdict, many.reason], ['UNVERIFIED', 'complex-graphic']);
  assert.equal(iconContrast({ icon: flat(4, 0), ring: flat(40, WHITE) }).reason, 'shape-unknown');
  assert.equal(iconContrast({}).verdict, 'UNVERIFIED');
  for (const box of [null, { x: 0, y: 0, width: 0, height: 5 }, { x: NaN, y: 0, width: 5, height: 5 }]) assert.equal(iconRegions(box).reason, 'shape-unknown');
});

test('state: component method in the current state; the off sibling is information only', () => {
  const on = { band: flat(40, PALE), fill: flat(40, PALE), ring: flat(40, WHITE) };
  const f = stateContrast({ ...on, offFill: flat(40, 0) });
  assert.deepEqual([f.verdict, f.measured.kind], ['FAIL', 'state']);
  assert.ok(f.measured.offContrast > 3);
  const p = stateContrast({ ...on, fill: flat(40, MID), offFill: flat(40, MID) });
  assert.deepEqual([p.verdict, p.measured.offContrast], ['PASS', 1]);
  for (const offFill of [undefined, flat(0, 0)]) assert.equal(Object.hasOwn(stateContrast({ ...on, offFill }).measured, 'offContrast'), false);
  assert.equal(stateContrast({ ...on, fill: flat(0, 0) }).verdict, 'UNVERIFIED');
});

test('no-samples: only exempt or no findings of the rule give UNVERIFIED, a measured one does not', () => {
  const mk = (verdict) => ({ rule: 'non-text-contrast', verdict, selector: '#a' });
  for (const list of [[], [mk('EXEMPT')], [{ rule: 'console', verdict: 'PASS' }]]) {
    const out = settleNonText(list);
    assert.equal(out.length, list.length + 1);
    assert.deepEqual([out.at(-1).verdict, out.at(-1).reason, out.at(-1).selector], ['UNVERIFIED', 'no-samples', undefined]);
  }
  for (const v of ['PASS', 'FAIL', 'UNVERIFIED', 'EXCEPTED', 'CONFIRMED']) assert.equal(settleNonText([mk(v)]).length, 1, v);
});

test('truncated: reason truncated with its own key and no way out', () => {
  const t = truncatedFinding();
  assert.deepEqual([t.rule, t.verdict, t.reason, t.hashBasis], ['non-text-contrast', 'UNVERIFIED', 'truncated', 'nontext-truncated']);
  const key = findingKey({ ...t, page: 'p' });
  const data = { exceptions: [], confirmations: [{ key, result: 'PASS', checkedBy: 'x', reason: 'x', expires: '2027-01-01' }] };
  assert.equal(applyExceptions([{ ...t, page: 'p', key }], data, { now: NOW }).findings[0].verdict, 'UNVERIFIED');
});

const report = (checks, findings, assertion = 'PASS') => buildReport({ pages: [{ id: 'p', url: 'file:///x', assertion, checks, findings }] });

test('report: enabling non-text without any measurement never gives PASS', () => {
  const consoleOk = { rule: 'console', verdict: 'PASS', message: 'no console errors' };
  const r = report(['console', 'non-text'], [consoleOk]);
  assert.deepEqual([r.result, r.exit], ['UNVERIFIED', 3]);
  assert.equal(r.pages[0].findings.at(-1).reason, 'no-samples');
  assert.equal(report(['non-text'], []).exit, 3);
  assert.equal(report(['console'], [consoleOk]).exit, 0);
  assert.equal(report(['non-text'], [{ rule: 'non-text-contrast', verdict: 'PASS', selector: '#a', measured: { kind: 'boundary' } }]).exit, 0);
  assert.equal(report(['non-text'], [], 'FAIL').pages[0].findings.length, 1);
});

const failOf = (kind) => ({ rule: 'non-text-contrast', selector: '#a', verdict: 'FAIL', measured: { kind, border: 1.2 }, required: 3, note: `note-${kind}` });
const run = (exceptions, findings, confirmations = []) => {
  const parsed = parseExceptionsFile({ version: 1, exceptions, confirmations }, { now: NOW });
  assert.ok(parsed.ok, parsed.errors.join('; '));
  return assembleReport({ pages: [{ id: 'p', url: 'file:///x', assertion: 'PASS', checks: ['non-text'], findings }], config: { warnings: [], pages: [] }, exceptionsData: parsed.data, now: NOW, digest: '0', masker: null });
};
const verdicts = (r) => r.pages[0].findings.map((f) => f.verdict);

test('keys: kind is part of the key of non-text-contrast only', () => {
  const base = { rule: 'non-text-contrast', page: 'p', selector: '#a' };
  assert.equal(findingKey({ ...base, measured: { kind: 'icon' } }), 'non-text-contrast|p|#a|icon');
  assert.equal(findingKey(base), 'non-text-contrast|p|#a');
  assert.equal(findingKey({ ...base, measured: { kind: 'bogus' } }), 'non-text-contrast|p|#a');
  assert.equal(findingKey({ ...base, rule: 'contrast', measured: { kind: 'icon' } }), 'contrast|p|#a');
  const r = run([], [failOf('boundary'), failOf('state')]);
  assert.deepEqual(r.pages[0].findings.map((f) => f.key), ['non-text-contrast|p|#a|boundary', 'non-text-contrast|p|#a|state']);
  assert.equal(r.pages[0].findings[0].basis, 'wcag');
  assert.equal(buildReport({ pages: [{ id: 'p', assertion: 'PASS', checks: ['non-text'], findings: [failOf('icon')] }] }).pages[0].findings[0].key, 'non-text-contrast|p|#a|icon');
  assert.match(renderMarkdown(r), /note-boundary/);
});

test('exceptions: kind narrows an entry, no kind covers every kind, an old key without kind is unused', () => {
  const both = [failOf('boundary'), failOf('state')];
  const narrow = run([{ rule: 'non-text-contrast', page: 'p', selector: '#a', kind: 'state', reason: 'r' }], both);
  assert.deepEqual(verdicts(narrow), ['FAIL', 'EXCEPTED']);
  const wide = run([{ rule: 'non-text-contrast', page: 'p', selector: '#a', reason: 'r' }], both);
  assert.deepEqual(verdicts(wide), ['EXCEPTED', 'EXCEPTED']);
  assert.deepEqual(wide.warnings, []);
  assert.deepEqual(verdicts(run([{ key: 'non-text-contrast|p|#a|boundary', reason: 'r' }], both)), ['EXCEPTED', 'FAIL']);
  const old = run([{ key: 'non-text-contrast|p|#a', reason: 'r' }], both);
  assert.deepEqual(verdicts(old), ['FAIL', 'FAIL']);
  assert.deepEqual(old.warnings.map((w) => w.code), ['exception-unused']);
  const other = run([{ rule: 'non-text-contrast', page: 'p', selector: '#a', kind: 'icon', reason: 'r' }], both);
  assert.deepEqual(other.warnings.map((w) => w.code), ['exception-unused']);
  assert.match(other.warnings[0].message, /\|icon/);
});

test('exceptions: kind is rejected where it would be ignored', () => {
  const bad = [
    { rule: 'contrast', selector: '#a', kind: 'icon', reason: 'r' },
    { rule: 'non-text-contrast', selector: '#a', kind: 'all', reason: 'r' },
    { key: 'non-text-contrast|p|#a', kind: 'icon', reason: 'r' },
    { rule: 'non-text-contrast', key: 'non-text-contrast|p|#a', kind: 'icon', reason: 'r' },
    { selector: '#a', kind: 'icon', reason: 'r' },
  ];
  for (const e of bad) assert.equal(parseExceptionsFile({ version: 1, exceptions: [e] }, { now: NOW }).ok, false, JSON.stringify(e));
  const conf = { rule: 'non-text-contrast', selector: '#a', kind: 'icon', result: 'PASS', checkedBy: 't', reason: 'r', expires: '2027-01-01' };
  assert.equal(parseExceptionsFile({ version: 1, confirmations: [conf] }, { now: NOW }).ok, false);
});

test('confirmations: settle an UNVERIFIED of the element whatever its kind', () => {
  const unv = { rule: 'non-text-contrast', selector: '#a', verdict: 'UNVERIFIED', reason: 'raster-graphic' };
  const r = run([], [unv], [{ rule: 'non-text-contrast', page: 'p', selector: '#a', result: 'PASS', checkedBy: 't', reason: 'r', expires: '2027-01-01' }]);
  assert.deepEqual(verdicts(r), ['CONFIRMED']);
});
