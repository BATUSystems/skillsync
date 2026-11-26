// Non-text contrast (WCAG 1.4.11): pixel regions of a component or an icon and the verdict from their colours.
// Pure logic: no browser, no files. Rectangles are [x, y, width, height] in document CSS pixels (screenshot scale 1).
// Findings carry no selector (the caller adds it); an UNVERIFIED one carries no `measured`, so its key names no kind.
// Anything that can not be measured reliably is UNVERIFIED, never PASS.
import { NON_TEXT_MIN_RATIO, SOLID_SPREAD, backgroundKindOf, contrastRatio, dominantColor, worstRatio } from './a11y-contrast.mjs';
import { LIMITS, NONTEXT_RULE, SchemaError } from './a11y-schema.mjs';

export const RING_PX = 2;
export const FILL_MARGIN_PX = 2;
export const MIN_REGION_PIXELS = 8;
export const MIN_RING_FRACTION = 0.5; // share of the outer ring that must be neither covered nor off the page
export const MIN_DOMINANT_SHARE = 0.25; // below it the "dominant colour" of a band, fill or glyph says nothing
export const MAX_REGION_POINTS = 4096;

const SIDES = Object.freeze(['top', 'right', 'bottom', 'left']);
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

// ---------- geometry ----------

// sides: { top: { width, style }, ... } from computed style. A side makes a band only when its style is visible and it
// is at least 1 px wide.
export function visibleBorderWidths(sides) {
  return Object.fromEntries(SIDES.map((s) => {
    const { width, style } = sides?.[s] || {};
    return [s, isNum(width) && width >= 1 && style !== 'none' && style !== 'hidden' ? width : 0];
  }));
}

const isBox = (b) => Boolean(b) && [b.x, b.y, b.width, b.height].every(isNum) && b.width > 0 && b.height > 0;
const degenerate = () => ({ ok: false, reason: 'shape-unknown' });

function ringAround({ x, y, width, height }) {
  const r = RING_PX;
  return [[x - r, y - r, width + 2 * r, r], [x - r, y + height, width + 2 * r, r], [x - r, y, r, height], [x + width, y, r, height]];
}

// box: border box { x, y, width, height }; borderWidths: visible widths per side (see visibleBorderWidths).
// band: strips of the border (null without a visible side); fill: the inside without the band and a 2 px margin;
// ring: 2 px outside the border box.
export function componentRegions({ box, borderWidths } = {}) {
  if (!isBox(box) || !borderWidths || !SIDES.every((s) => isNum(borderWidths[s]) && borderWidths[s] >= 0)) return degenerate();
  const { x, y, width, height } = box;
  const { top, right, bottom, left } = borderWidths;
  const m = FILL_MARGIN_PX;
  const fill = [x + left + m, y + top + m, width - left - right - 2 * m, height - top - bottom - 2 * m];
  if (!(fill[2] > 0 && fill[3] > 0)) return degenerate();
  const inner = height - top - bottom;
  const band = [[x, y, width, top], [x, y + height - bottom, width, bottom], [x, y + top, left, inner], [x + width - right, y + top, right, inner]]
    .filter((r) => r[2] > 0 && r[3] > 0);
  return { ok: true, band: band.length ? band : null, fill: [fill], ring: ringAround(box) };
}

export function iconRegions(box) {
  if (!isBox(box)) return degenerate();
  return { ok: true, icon: [[box.x, box.y, box.width, box.height]], ring: ringAround(box) };
}

// Pixels whose centre lies in a rectangle: first index and count along one axis.
const span = (start, size) => {
  const first = Math.ceil(start - 0.5) + 0; // + 0: no negative zero
  return [first, Math.max(0, Math.ceil(start + size - 0.5) - first)];
};
const inside = (r, x, y) => x + 0.5 >= r[0] && x + 0.5 < r[0] + r[2] && y + 0.5 >= r[1] && y + 0.5 < r[1] + r[3];

// Sample points of a region, flat [x0, y0, x1, y1, ...] as gatherPixels expects. Large regions are thinned, mainly along
// the longer side, so thin strips are never skipped. total counts every sample; excluded those under an `exclude`
// rectangle (other components), outside those off the page (`bounds` { width, height }); points holds the rest.
export function regionPoints(rects, { exclude = [], bounds = null, cap = MAX_REGION_POINTS } = {}) {
  const spans = (rects || []).filter((r) => Array.isArray(r) && r.length === 4 && r.every(isNum) && r[2] > 0 && r[3] > 0)
    .map((r) => [span(r[0], r[2]), span(r[1], r[3])]).filter(([a, b]) => a[1] > 0 && b[1] > 0);
  const thin = Math.max(1, spans.reduce((n, [a, b]) => n + a[1] * b[1], 0) / cap);
  const out = { points: [], total: 0, excluded: 0, outside: 0 };
  for (const [[x0, nx], [y0, ny]] of spans) {
    const short = Math.max(1, Math.min(Math.floor(Math.sqrt(thin)), Math.floor(Math.min(nx, ny) / 16)));
    const long = Math.max(1, Math.ceil(thin / short));
    const [stepX, stepY] = nx >= ny ? [long, short] : [short, long];
    for (let y = y0; y < y0 + ny; y += stepY) {
      for (let x = x0; x < x0 + nx; x += stepX) {
        out.total++;
        if (x < 0 || y < 0 || (bounds && (x >= bounds.width || y >= bounds.height))) out.outside++;
        else if (exclude.some((r) => inside(r, x, y))) out.excluded++;
        else out.points.push(x, y);
      }
    }
  }
  return out;
}

// ---------- verdicts ----------

// A region sample is { pixels: [[r, g, b]], total, excluded, outside } (counts from regionPoints, pixels gathered from
// the screenshot). Returns the UNVERIFIED reason, or null when the sample can be trusted. Only the ring tolerates
// missing parts (covered or off the page), up to MIN_RING_FRACTION.
function sampleProblem(s, { ring = false } = {}) {
  if (!s || !Array.isArray(s.pixels) || ![s.total, s.excluded, s.outside].every((n) => Number.isInteger(n) && n >= 0)) return 'no-samples';
  if (s.total < MIN_REGION_PIXELS) return 'shape-unknown';
  const channel = (c) => isNum(c) && c >= 0 && c <= 255;
  if (s.pixels.length !== s.total - s.excluded - s.outside || !s.pixels.every((p) => Array.isArray(p) && p.length === 3 && p.every(channel))) return 'no-samples';
  const enough = s.pixels.length >= MIN_REGION_PIXELS && s.pixels.length >= s.total * (ring ? MIN_RING_FRACTION : 1);
  if (enough) return null;
  return s.outside > s.excluded ? 'clipped' : 'occluded';
}

function requiredRatio(thresholds) {
  const required = thresholds?.nonText ?? NON_TEXT_MIN_RATIO;
  if (!isNum(required) || required < 1 || required > 21) throw new SchemaError('thresholds.nonText: expected a number 1..21');
  return required;
}

const rgbOf = (p) => ({ r: p[0], g: p[1], b: p[2] });
const sameColour = (a, b) => a[0] === b.r && a[1] === b.g && a[2] === b.b;
const round3 = (n) => Math.round(n * 1000) / 1000;
const cssRgb = (c) => `rgb(${c.r},${c.g},${c.b})`;

// The dominant colour of a region and whether enough of the region has it.
function regionColour(pixels) {
  const colour = dominantColor(pixels);
  const share = pixels.filter((p) => sameColour(p, colour)).length / pixels.length;
  return { colour, reliable: share >= MIN_DOMINANT_SHARE };
}

// Surroundings of an element: dominant colour, kind and the deciding contrast of a colour against them.
function surroundings(pixels) {
  const colour = dominantColor(pixels);
  const kind = backgroundKindOf(pixels);
  const against = (c) => (kind === 'varying' ? worstRatio(pixels.map((p) => contrastRatio(c, rgbOf(p))), kind) : contrastRatio(c, colour));
  return { colour, kind, against };
}

const unverified = (reason, note) => ({ rule: NONTEXT_RULE, verdict: 'UNVERIFIED', reason, ...(note ? { note } : {}) });

function judged(passed, measured, required, notes) {
  return { rule: NONTEXT_RULE, verdict: passed ? 'PASS' : 'FAIL', measured, required, ...(notes.length ? { note: notes.join(', ') } : {}) };
}

// A component passes when its border band OR its fill reaches the threshold against the outer ring. band: null when the
// element has no visible border. Every region that applies must be measurable, even when another one would pass.
export function componentContrast({ band, fill, ring, thresholds, kind = 'boundary' } = {}) {
  const required = requiredRatio(thresholds);
  const regions = [...(band === null ? [] : [['border', band]]), ['fill', fill]];
  const problem = [...regions.map(([, s]) => sampleProblem(s)), sampleProblem(ring, { ring: true })].find(Boolean);
  if (problem) return unverified(problem);
  const around = surroundings(ring.pixels);
  const parts = regions.map(([name, s]) => {
    const { colour, reliable } = regionColour(s.pixels);
    return { name, colour, reliable, ratio: around.against(colour) };
  });
  const passed = parts.some((p) => p.reliable && p.ratio >= required);
  if (!passed && parts.some((p) => !p.reliable)) return unverified('media-background', 'the border or the fill has no dominant colour');
  const [border, inner] = band === null ? [null, parts[0]] : parts;
  const measured = {
    kind,
    border: border && round3(border.ratio),
    fill: round3(inner.ratio),
    borderFill: border && round3(contrastRatio(border.colour, inner.colour)), // information only
    outside: cssRgb(around.colour),
  };
  const notes = around.kind === 'varying' ? ['sampled'] : [];
  if (!passed) {
    const best = parts.reduce((a, b) => (b.ratio > a.ratio ? b : a));
    notes.push(`neither the border nor the fill reaches ${required}:1 against the surroundings; best: ${best.name} ${round3(best.ratio)}`);
  }
  return judged(passed, measured, required, notes);
}

// A state indicator in its current state is judged like a component. offFill (optional sample of the fill of a sibling
// in the "off" state) only adds measured.offContrast: information, never part of the verdict.
export function stateContrast({ offFill, ...input } = {}) {
  const result = componentContrast({ ...input, kind: 'state' });
  if (!result.measured || sampleProblem(offFill)) return result;
  const on = regionColour(input.fill.pixels);
  const off = regionColour(offFill.pixels);
  if (on.reliable && off.reliable) result.measured.offContrast = round3(contrastRatio(on.colour, off.colour));
  return result;
}

// An icon or graphical object: the dominant colour of the pixels inside its box that differ from the local background
// (dominant colour of the ring), against the ring.
export function iconContrast({ icon, ring, thresholds } = {}) {
  const required = requiredRatio(thresholds);
  const problem = sampleProblem(icon) || sampleProblem(ring, { ring: true });
  if (problem) return unverified(problem);
  const around = surroundings(ring.pixels);
  const bg = [around.colour.r, around.colour.g, around.colour.b];
  const glyph = icon.pixels.filter((p) => p.some((c, i) => Math.abs(c - bg[i]) > SOLID_SPREAD));
  if (glyph.length < MIN_REGION_PIXELS) return unverified('no-samples', 'too few pixels differ from the surroundings to tell the graphic apart');
  const { colour, reliable } = regionColour(glyph);
  if (!reliable) return unverified('complex-graphic', 'the graphic has no dominant colour');
  const ratio = around.against(colour);
  const passed = ratio >= required;
  const notes = around.kind === 'varying' ? ['sampled'] : [];
  if (!passed) notes.push(`the graphic does not reach ${required}:1 against the surroundings`);
  return judged(passed, { kind: 'icon', icon: round3(ratio), colour: cssRgb(colour), outside: cssRgb(around.colour) }, required, notes);
}

// ---------- page level ----------

// No vacuous PASS: a page with the check enabled and nothing but EXEMPT (or nothing at all) from it is UNVERIFIED.
export function settleNonText(findings) {
  if (findings.some((f) => f.rule === NONTEXT_RULE && f.verdict !== 'EXEMPT' && f.verdict !== 'INFO')) return [...findings];
  return [...findings, { ...unverified('no-samples'), hashBasis: 'no-nontext', message: 'no non-text component, icon or state indicator was measured' }];
}

// More candidates than LIMITS.nonText: a limit, so there is no exception or confirmation for it.
export function truncatedFinding(limit = LIMITS.nonText) {
  return { ...unverified('truncated'), hashBasis: 'nontext-truncated', message: `more than ${limit} non-text candidates; the rest was not measured` };
}
