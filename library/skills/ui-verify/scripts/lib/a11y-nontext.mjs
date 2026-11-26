// Non-text contrast (WCAG 1.4.11): pixel regions of a component or an icon and the verdict from their colours.
// Pure logic: no browser, no files. Rectangles are [x, y, width, height] in document CSS pixels (screenshot scale 1).
// Findings of the measurement functions carry no selector (candidateFindings adds it); an UNVERIFIED one carries no
// `measured`, so its key names no kind.
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

// The band is made of rectangles. When the straight parts of the sides are less than half of the rectangle's perimeter
// (or the radius is unknown, null), those rectangles do not describe the outline.
function roundedForMeasurement({ width, height }, radius) {
  if (radius === null) return true;
  const r = Math.min(radius, width / 2, height / 2);
  return 2 * Math.max(0, width - 2 * r) + 2 * Math.max(0, height - 2 * r) < width + height;
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
    .map((r) => [span(r[0], r[2]), span(r[1], r[3]), r]).filter(([a, b]) => a[1] > 0 && b[1] > 0);
  const thin = Math.max(1, spans.reduce((n, [a, b]) => n + a[1] * b[1], 0) / cap);
  const out = { points: [], total: 0, excluded: 0, outside: 0 };
  for (const [[x0, nx], [y0, ny], rect] of spans) {
    // only the exclusions that reach this rectangle are tested per point (1 px of slack for rounding)
    const near = exclude.filter((e) => e[0] < rect[0] + rect[2] + 1 && e[0] + e[2] > rect[0] - 1 && e[1] < rect[1] + rect[3] + 1 && e[1] + e[3] > rect[1] - 1);
    const short = Math.max(1, Math.min(Math.floor(Math.sqrt(thin)), Math.floor(Math.min(nx, ny) / 16)));
    const long = Math.max(1, Math.ceil(thin / short));
    const [stepX, stepY] = nx >= ny ? [long, short] : [short, long];
    for (let y = y0; y < y0 + ny; y += stepY) {
      for (let x = x0; x < x0 + nx; x += stepX) {
        out.total++;
        if (x < 0 || y < 0 || (bounds && (x >= bounds.width || y >= bounds.height))) out.outside++;
        else if (near.some((r) => inside(r, x, y))) out.excluded++;
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

// ---------- candidates ----------
//
// NonTextCandidate: one element the page script collects for the check "non-text". All data from the page is untrusted:
// EVERY field below is required (use null / false / '' where it does not apply). A missing or mistyped field, or an
// unknown value of a closed set (states, border style, hit), makes the whole record UNVERIFIED "no-samples" (no
// exception or confirmation settles it); it is never EXEMPT or PASS. Unknown extra keys are ignored.
//   selector        non-empty string, report selector (selectorOf), computed on the Node side
//   tag             non-empty string, element name (any case)
//   type            string: the `type` property of an input, '' otherwise
//   role            string: the `role` attribute, '' when absent (the first token counts)
//   states          { selected, pressed, checked, expanded, current }: the aria-* attribute values, null when absent;
//                   selected: true|false|undefined|'', pressed and checked: also mixed, expanded: true|false|undefined|'',
//                   current: any string ('' and false mean "not current")
//   hidden          boolean: not rendered (display, visibility, clipped to 1 px) or without a box
//   opacity         number 0..1: product over the element and its ancestors (0 = not rendered)
//   inactive        boolean: the element or its control is :disabled or under [aria-disabled="true"]
//   ariaHidden      boolean: the element or an ancestor has aria-hidden="true"
//   interactive     boolean: operable or focusable (native control, link with href, widget role, tabindex)
//   alt             string | null: the alt attribute (null when absent)
//   named           boolean: the element has a non-empty accessible name (alt, aria-label, aria-labelledby, svg title)
//   visibleText     boolean: the element itself shows text (visually hidden text does not count)
//   graphicContent  boolean: the element contains a graphic that is collected as a candidate of its own and names this
//                   element as its control
//   control         null | { hasVisibleText, soleContent, buttonOrLink }: for a graphic, the nearest interactive
//                   ancestor in the flat tree; soleContent = the graphic is all that the control shows (never together
//                   with hasVisibleText); buttonOrLink = that ancestor is a button or a link
//   userAgent       boolean: computed exactly as for targets (UA_CONTROL_TYPES, appearance, unstyled size)
//   appearance      string: computed appearance
//   pseudoControl   boolean, true only for an input: the input itself is visually hidden and the control may be drawn
//                   elsewhere: a VISIBLE pseudo-element with `content` near it, or, for a checkbox or radio, a visible
//                   label or a visible sibling that paints (false when the whole control is not shown)
//   graphic         null | { raster, gradient, filter, fills }: booleans and the number of distinct fill colours
//   focused         boolean: the element, an ancestor or a descendant of it had the focus while it was collected
//   box             { x, y, width >= 0, height >= 0 }: border box in document CSS pixels
//   borders         { top, right, bottom, left }, each { width >= 0, style: CSS border style }
//   borderRadius    number >= 0 | null: one corner radius in px for the four corners; null when a corner is elliptical
//                   or can not be read
//   flags           { filter, group, media: booleans, hit: null | 'occluded' | 'clipped' }, as in the text check;
//                   `media` means media under the element other than the candidate graphic itself
//
// The page script collects only: form components, graphics, elements with a state attribute, and buttons and links.

export const UA_CONTROL_TYPES = Object.freeze(['checkbox', 'radio', 'range', 'color', 'file', 'date']);
const EXCLUDED_INPUT_TYPES = new Set(['hidden', 'submit', 'button', 'reset', 'image']);
const COMPONENT_ROLES = new Set(['textbox', 'combobox', 'searchbox', 'spinbutton', 'listbox', 'slider', 'switch', 'checkbox', 'radio']);
const GRAPHIC_TAGS = new Set(['svg', 'img', 'canvas']);
const RASTER_TAGS = new Set(['img', 'canvas']);
const DECORATIVE_ROLES = new Set(['presentation', 'none']);
const BORDER_STYLES = new Set(['none', 'hidden', 'dotted', 'dashed', 'solid', 'double', 'groove', 'ridge', 'inset', 'outset']);
export const MAX_ICON_FILLS = 6;
const OFF = ['false', 'undefined', ''];
// Per state attribute: the accepted values (null = any string) and those that make a state indicator.
const STATE_VALUES = Object.freeze({
  selected: { known: ['true', ...OFF], on: (v) => v === 'true' },
  pressed: { known: ['true', 'mixed', ...OFF], on: (v) => v === 'true' || v === 'mixed' },
  checked: { known: ['true', 'mixed', ...OFF], on: (v) => v === 'true' || v === 'mixed' },
  expanded: { known: ['true', ...OFF], on: (v) => v === 'true' || v === 'false' }, // both are states the user must see
  current: { known: null, on: (v) => !OFF.includes(v) }, // ARIA: an unknown token of aria-current means "true"
});

const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const isBool = (v) => typeof v === 'boolean';
const isStr = (v) => typeof v === 'string';
const isSize = (v) => isNum(v) && v >= 0;
const token = (v) => v.trim().toLowerCase();

const FIELDS = Object.freeze({
  selector: (v) => isStr(v) && v !== '',
  tag: (v) => isStr(v) && v !== '',
  type: isStr,
  role: isStr,
  states: (v) => isObj(v) && Object.entries(STATE_VALUES).every(([k, { known }]) => v[k] === null || (isStr(v[k]) && (known === null || known.includes(token(v[k]))))),
  hidden: isBool,
  opacity: (v) => isNum(v) && v >= 0 && v <= 1,
  inactive: isBool,
  ariaHidden: isBool,
  interactive: isBool,
  alt: (v) => v === null || isStr(v),
  named: isBool,
  visibleText: isBool,
  graphicContent: isBool,
  control: (v) => v === null || (isObj(v) && [v.hasVisibleText, v.soleContent, v.buttonOrLink].every(isBool) && !(v.hasVisibleText && v.soleContent)),
  userAgent: isBool,
  appearance: isStr,
  pseudoControl: isBool,
  focused: isBool,
  graphic: (v) => v === null || (isObj(v) && [v.raster, v.gradient, v.filter].every(isBool) && Number.isInteger(v.fills) && v.fills >= 0),
  box: (v) => isObj(v) && isNum(v.x) && isNum(v.y) && isSize(v.width) && isSize(v.height),
  borders: (v) => isObj(v) && SIDES.every((s) => isObj(v[s]) && isSize(v[s].width) && BORDER_STYLES.has(v[s].style)),
  borderRadius: (v) => v === null || isSize(v),
  flags: (v) => isObj(v) && [v.filter, v.group, v.media].every(isBool) && [null, 'occluded', 'clipped'].includes(v.hit),
});

const validCandidate = (c) => isObj(c) && Object.entries(FIELDS).every(([k, ok]) => Object.hasOwn(c, k) && ok(c[k]))
  && (!c.pseudoControl || token(c.tag) === 'input');

const exempt = (reason) => ({ rule: NONTEXT_RULE, verdict: 'EXEMPT', reason });

// Decides everything that needs no pixels. Returns { findings, measure, regions }: findings already decided (without a
// selector), the kinds still to be measured and the rectangles to sample for them (componentRegions / iconRegions).
function decide(c) {
  const done = (...findings) => ({ findings, measure: [], regions: { component: null, icon: null } });
  if (!validCandidate(c)) return done(unverified('no-samples', 'incomplete or invalid candidate data from the page'));

  const tag = token(c.tag);
  const type = token(c.type);
  const role = token(c.role).split(/\s+/)[0];
  const excludedInput = tag === 'input' && EXCLUDED_INPUT_TYPES.has(type);
  const component = !excludedInput && (['input', 'textarea', 'select'].includes(tag) || COMPONENT_ROLES.has(role));
  const graphic = GRAPHIC_TAGS.has(tag) || role === 'img';
  const buttonLike = !component && (tag === 'button' || role === 'button' || role === 'link' || (tag === 'a' && c.interactive));
  const state = Object.entries(STATE_VALUES).some(([k, { on }]) => c.states[k] !== null && on(token(c.states[k])));
  // The graphic is what identifies its control: no attribute makes it decorative then.
  const identifying = graphic && c.control !== null && !c.control.hasVisibleText;
  const besideText = graphic && c.control !== null && c.control.hasVisibleText && c.control.buttonOrLink && !c.interactive;
  const hiddenFromAt = c.ariaHidden || DECORATIVE_ROLES.has(role) || (tag === 'img' && c.alt === '');
  const icon = graphic && (c.control !== null || c.interactive || (role === 'img' && c.named) || hiddenFromAt);

  // Not candidates by the specification: inputs that carry their own text or image, loose graphics without a role and name.
  if (!component && !state && !icon && (excludedInput || (graphic && !buttonLike))) return done();

  // EXEMPT, in the order of the specification.
  if (!c.pseudoControl && (c.hidden || c.opacity === 0)) return done(exempt('not-rendered'));
  if (c.inactive) return done(exempt('inactive'));
  // Interactive elements and form controls stay visible and operable whatever aria-hidden or role say.
  if (hiddenFromAt && !c.interactive && !component && !buttonLike && !identifying) return done(exempt('decorative'));
  const textExempt = besideText ? exempt(c.named ? 'text-identifies' : 'decorative') : null;
  const kinds = [];
  if (component) kinds.push('boundary');
  if (icon && !textExempt) kinds.push('icon');
  if (state) kinds.push('state'); // text never tells the state: a state indicator is measured in any case
  if (kinds.length === 0) {
    if (textExempt) return done(textExempt);
    if (buttonLike && c.visibleText) return done(exempt('text-identifies'));
    if (buttonLike && c.graphicContent) return done(); // judged on the record of the graphic
    // collected only for a state attribute that is off: not a state indicator, so not a candidate
    if (!buttonLike && !graphic && Object.values(c.states).some((v) => v !== null)) return done();
    return done(unverified('shape-unknown', 'nothing that identifies this element can be measured'));
  }
  if (component && c.userAgent && !c.pseudoControl && tag === 'input' && UA_CONTROL_TYPES.includes(type) && !['', 'none'].includes(token(c.appearance))) {
    return done(exempt('user-agent'));
  }

  // UNVERIFIED before measuring.
  if (c.pseudoControl) return done(unverified('pseudo-element-control'));
  const f = c.flags;
  const flagged = (f.filter && 'filter-or-blend') || (f.group && 'opacity-group-with-paint') || (f.media && 'media-background') || f.hit;
  if (flagged) return done(unverified(flagged));

  const plan = done();
  for (const kind of kinds) {
    let problem = null;
    if (kind === 'icon') {
      if (identifying && !c.control.soleContent) problem = unverified('shape-unknown', 'one of several graphics of a control without text');
      else if (RASTER_TAGS.has(tag) || c.graphic?.raster) problem = unverified('raster-graphic');
      else if (c.graphic === null) problem = unverified('no-samples', 'no data about the graphic');
      else if (c.graphic.gradient || c.graphic.filter || c.graphic.fills > MAX_ICON_FILLS) problem = unverified('complex-graphic');
      else plan.regions.icon = iconRegions(c.box);
    } else {
      plan.regions.component = componentRegions({ box: c.box, borderWidths: visibleBorderWidths(c.borders) });
    }
    const regions = kind === 'icon' ? plan.regions.icon : plan.regions.component;
    if (!problem && !regions.ok) problem = unverified(regions.reason);
    if (!problem) plan.measure.push(kind);
    else plan.findings.push(problem);
  }
  return plan;
}

// One UNVERIFIED per reason: such findings of one element share their key.
const oncePerReason = (findings) => findings.filter((f, i) => f.verdict !== 'UNVERIFIED' || findings.findIndex((o) => o.verdict === f.verdict && o.reason === f.reason) === i);

const identified = (c, f) => (isObj(c) && FIELDS.selector(c.selector) ? { ...f, selector: c.selector } : { ...f, hashBasis: 'nontext-invalid' });

// What the page side needs before the screenshot is sampled: the decided findings (with the selector), the kinds to
// measure and their regions.
export function classifyCandidate(candidate) {
  const plan = decide(candidate);
  return { ...plan, findings: oncePerReason(plan.findings).map((f) => identified(candidate, f)) };
}

// samples: { component: { band, fill, ring, offFill? }, icon: { icon, ring } }, each part a region sample (see
// sampleProblem) of the rectangles classifyCandidate returned; band is null when regions.component.band is null.
export function candidateFindings(candidate, samples, { thresholds } = {}) {
  const { findings, measure, regions } = decide(candidate);
  const measureKind = (kind) => {
    if (kind === 'icon') return iconContrast({ icon: samples?.icon?.icon, ring: samples?.icon?.ring, thresholds });
    const s = samples?.component;
    // whether there is a border comes from the candidate, never from the samples
    const input = { band: regions.component.band === null ? null : s?.band ?? undefined, fill: s?.fill, ring: s?.ring, thresholds };
    return kind === 'state' ? stateContrast({ ...input, offFill: s?.offFill }) : componentContrast(input);
  };
  const measured = measure.map((kind) => {
    const result = measureKind(kind);
    // No state of the page is changed (no blur), so a focus indicator may be part of what was measured.
    if (candidate.focused && result.verdict !== 'UNVERIFIED') return unverified('shape-unknown', 'the element had focus while measured; the focus indicator may be in the sample');
    // A rectangular band can only prove a PASS of a rounded outline: its FAIL is unknown.
    if (kind === 'icon' || result.verdict !== 'FAIL' || regions.component.band === null || !roundedForMeasurement(candidate.box, candidate.borderRadius)) return result;
    return unverified('shape-unknown', 'the rectangular border band does not describe the rounded outline');
  });
  const noted = measured.map((f) => (candidate.ariaHidden ? { ...f, note: [f.note, 'aria-hidden on an element that is still judged'].filter(Boolean).join(', ') } : f));
  return oncePerReason([...findings, ...noted]).map((f) => identified(candidate, f));
}

// entries: [{ candidate, samples }] in page order. truncated: the page script's flag (anything but false or absent
// counts as truncated). The caller still applies settleNonText (buildReport does).
export function nonTextFindings(entries, { thresholds, truncated = false } = {}) {
  if (!Array.isArray(entries)) return [identified(null, unverified('no-samples', 'no list of non-text candidates'))];
  const out = entries.slice(0, LIMITS.nonText).flatMap((e) => candidateFindings(e?.candidate, e?.samples, { thresholds }));
  if (truncated !== false || entries.length > LIMITS.nonText) out.push(truncatedFinding());
  return out;
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
