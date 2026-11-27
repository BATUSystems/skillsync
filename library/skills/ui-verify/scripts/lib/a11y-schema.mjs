// Shared vocabulary and data contracts of the accessibility audit (a11y.mjs): rules, verdicts, reasons,
// text sanitising, report selectors and validators of the page-measurement samples. Pure logic, no browser.

import { createHash } from 'node:crypto';

export class SchemaError extends Error {
  constructor(message, errors = []) {
    super(message);
    this.name = 'SchemaError';
    this.errors = errors;
  }
}

// Checks selectable in a page configuration (`checks`) and the finding rules each of them produces.
// DEFAULT_CHECKS run when a page has no `checks`; "non-text" (WCAG 1.4.11) runs only when a page lists it.
export const DEFAULT_CHECKS = Object.freeze(['contrast', 'target-size', 'focus', 'tab-order', 'console']);
export const CHECKS = Object.freeze([...DEFAULT_CHECKS, 'non-text']);
export const RULES = Object.freeze([
  'contrast', 'target-size', 'focus-keyboard-indicator', 'focus-mouse-ring', 'focus-contrast', 'focus-appearance',
  'non-text-contrast', 'tab-positive-tabindex', 'tab-unreachable', 'tab-trap', 'tab-order-visual', 'console', 'assertion-failed', 'checks',
]);
// Opt-in focus indicator contrast rules (configuration keys focus.contrast and focus.appearance) and their modes.
export const FOCUS_CONTRAST_RULES = Object.freeze(['focus-contrast', 'focus-appearance']);
export const FOCUS_RULE_MODES = Object.freeze(['off', 'report', 'fail']);
// Rule of the opt-in check "non-text" and the kinds of element it judges (measured.kind, part of the finding key).
export const NONTEXT_RULE = 'non-text-contrast';
export const NONTEXT_KINDS = Object.freeze(['boundary', 'icon', 'state']);
// Report `basis` of a rule when the finding carries none; rules not listed here are "wcag" (level A or AA).
export const RULE_BASIS = Object.freeze({ 'focus-appearance': 'wcag-aaa' });
export const VERDICTS = Object.freeze(['PASS', 'FAIL', 'EXEMPT', 'EXCEPTED', 'CONFIRMED', 'UNVERIFIED', 'INFO']);

// EXEMPT reasons (excluded by WCAG or not rendered) are decided BEFORE any measurement.
export const EXEMPT_REASONS = Object.freeze(['not-rendered', 'inactive', 'decorative', 'text-identifies', 'user-agent']);
// UNVERIFIED reasons that a manual confirmation (confirmations[] in the exceptions file) may settle.
export const CONFIRMABLE_REASONS = Object.freeze([
  'opacity-group-with-paint', 'background-clip-text', 'color-unconvertible', 'occluded', 'clipped',
  'filter-or-blend', 'inline-important-color', 'autofill', 'media-background', 'shape-unknown',
  'pseudo-element-control', 'raster-graphic', 'complex-graphic',
]);
// UNVERIFIED reasons caused by limits or missing input: never a way out through exceptions or confirmations.
export const LIMIT_REASONS = Object.freeze(['page-too-tall', 'layout-shift', 'tab-frame', 'mouse-capture-failed', 'truncated', 'sequence-truncated', 'no-assertion', 'no-checks', 'no-samples']);
export const UNVERIFIED_REASONS = Object.freeze([...CONFIRMABLE_REASONS, ...LIMIT_REASONS]);

export const LIMITS = Object.freeze({
  contrastElements: 5000, targets: 2000, tabStops: 500, messages: 200, textChars: 80, selectorChars: 400, messageChars: 300, nonText: 2000,
});

const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

// --- text coming from the page is untrusted data: strip control, bidi and zero-width characters, then cut ---

const SPACE_CONTROL_RE = /[\t\n\r\v\f]/g;
const CONTROL_RE = /[\u0000-\u001f\u007f-\u009f]/g;
const INVISIBLE_RE = /[\u00ad\u034f\u061c\u115f\u1160\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\u3164\ufe00-\ufe0f\ufeff\uffa0\u{e0000}-\u{e007f}]/gu;

export function cleanText(text, max = LIMITS.textChars, masker = null) {
  let s = String(text ?? '').replace(SPACE_CONTROL_RE, ' ').replace(CONTROL_RE, '').replace(INVISIBLE_RE, '').replace(/\s+/g, ' ').trim();
  if (masker && typeof masker.mask === 'function') s = masker.mask(s);
  if (s.length > max) s = `${s.slice(0, Math.max(0, max - 3))}...`;
  return s;
}

// --- selectors shown in the report ---

// An id that looks generated: starts with ":", has 4 digits in a row or is 8+ hex characters.
export function isDynamicId(id) {
  const s = String(id ?? '');
  return s.includes(':') || /\d{4}/.test(s) || /^[0-9a-fA-F]{8,}$/.test(s)
    || /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i.test(s) // UUID
    || /[A-Za-z]-\d{1,3}-/.test(s); // counter in the middle (react-select-3-input)
}

// A selector longer than `max` keeps its beginning and its end and a short hash of the whole text, so two different
// elements never end up with the same shortened selector (the tail is the part that tells siblings apart).
export function capSelector(sel, max = LIMITS.selectorChars) {
  const s = String(sel);
  if (s.length <= max) return s;
  const sha = createHash('sha256').update(s, 'utf8').digest('hex').slice(0, 8);
  const tail = Math.floor((max - 12) / 3);
  return `${s.slice(0, max - 12 - tail)}...${s.slice(-tail)}~${sha}`;
}

// chain: [{ tag, nth }] from the root to the element (nth = position among same-tag siblings, 1-based).
export function buildPath(chain) {
  return (chain || []).map((c) => {
    const tag = String(c.tag || '').toLowerCase();
    return tag === 'html' || tag === 'body' ? tag : `${tag}:nth-of-type(${c.nth || 1})`;
  }).join(' > ');
}

// el: { id, idUnique, chain }. "#id" only when the id is unique in the document and not dynamic.
export function reportSelector(el) {
  const id = el && typeof el.id === 'string' ? el.id : '';
  if (id !== '' && el.idUnique === true && !isDynamicId(id)) {
    if (/^[A-Za-z_][A-Za-z0-9_-]*$/.test(id)) return `#${id}`;
    return `[id="${id.replace(/["\\]/g, '\\$&')}"]`;
  }
  return buildPath(el && el.chain);
}

// --- contract of the data the page script hands to the pure logic ---

/**
 * ContrastSample: one text-bearing element measured in the page.
 *   selector     string, report selector (see reportSelector)
 *   fontSizePx   number > 0
 *   fontWeight   number 1..1000 (optional); bold = fontWeight >= 700 when `bold` is absent
 *   foreground   { r, g, b, a }: text fill colour in sRGB (0..255, alpha 0..1)
 *   opacity      number 0..1: product of opacity of the element and its ancestors (default 1)
 *   pixels       Array<[r, g, b]>: opaque screenshot pixels sampled under the text rectangles (text hidden)
 *   exempt       optional EXEMPT reason (not-rendered | inactive): decided before measuring
 *   unverified   optional UNVERIFIED reason (see UNVERIFIED_REASONS): decided before measuring, after exempt
 * With exempt or unverified set, foreground and pixels are optional.
 */
export function validateContrastSample(s) {
  const errors = [];
  if (!isObj(s)) return { ok: false, errors: ['sample: expected object'] };
  if (typeof s.selector !== 'string' || s.selector === '') errors.push('selector: required non-empty string');
  if (s.exempt !== undefined && !EXEMPT_REASONS.includes(s.exempt)) errors.push(`exempt: unknown reason "${s.exempt}"`);
  if (s.unverified !== undefined && !UNVERIFIED_REASONS.includes(s.unverified)) errors.push(`unverified: unknown reason "${s.unverified}"`);
  const decided = s.exempt !== undefined || s.unverified !== undefined;
  if (!isNum(s.fontSizePx) || s.fontSizePx <= 0) errors.push('fontSizePx: expected a positive number');
  if (s.fontWeight !== undefined && (!isNum(s.fontWeight) || s.fontWeight < 1 || s.fontWeight > 1000)) errors.push('fontWeight: expected 1..1000');
  if (s.opacity !== undefined && (!isNum(s.opacity) || s.opacity < 0 || s.opacity > 1)) errors.push('opacity: expected 0..1');
  const fg = s.foreground;
  const fgOk = isObj(fg) && ['r', 'g', 'b'].every((k) => isNum(fg[k]) && fg[k] >= 0 && fg[k] <= 255) && isNum(fg.a) && fg.a >= 0 && fg.a <= 1;
  if (!fgOk && !decided) errors.push('foreground: expected { r, g, b (0..255), a (0..1) }');
  if (s.pixels !== undefined || !decided) {
    const px = s.pixels;
    const pxOk = Array.isArray(px) && px.every((p) => Array.isArray(p) && p.length === 3 && p.every((c) => isNum(c) && c >= 0 && c <= 255));
    if (!pxOk) errors.push('pixels: expected a list of [r, g, b] triples (0..255)');
  }
  return { ok: errors.length === 0, errors };
}

/**
 * TargetItem: one rendered interactive element, in document coordinates.
 *   selector       string
 *   box            { x, y, width, height } (width and height >= 0)
 *   rendered       boolean (false items are not part of the target pool)
 *   display        string: computed display of the element ("inline" matters)
 *   parentHasText  boolean: the parent has its own non-empty text node (inline-in-sentence)
 *   userAgent      boolean: native control with appearance != none whose size equals the unstyled control
 *   labelBox       { width, height } | null: visible label of a checkbox, radio or file input
 *   pair           string (optional): items with the same value (control and its label) ignore each other
 */
export function validateTargetItem(t) {
  const errors = [];
  if (!isObj(t)) return { ok: false, errors: ['target: expected object'] };
  if (typeof t.selector !== 'string' || t.selector === '') errors.push('selector: required non-empty string');
  const b = t.box;
  if (!isObj(b) || !['x', 'y', 'width', 'height'].every((k) => isNum(b[k])) || b.width < 0 || b.height < 0) errors.push('box: expected { x, y, width >= 0, height >= 0 }');
  if (t.labelBox !== undefined && t.labelBox !== null && !(isObj(t.labelBox) && isNum(t.labelBox.width) && isNum(t.labelBox.height))) errors.push('labelBox: expected { width, height } or null');
  return { ok: errors.length === 0, errors };
}

/**
 * TabStep: one step of the recorded Tab sequence.
 *   key        string: unique identity of the element in this run (same element = same key)
 *   selector   string
 *   tabindex   number | null (the attribute value)
 *   box        { x, y, width, height } | null (document coordinates)
 *   inDocument boolean: false for steps outside the document flow (skip links, off-screen)
 *   inModal    boolean: inside [role=dialog][aria-modal=true] or dialog[open]
 *   kind       "element" | "body" | "outside" (body and outside end the sequence)
 */
export function validateTabStep(s) {
  const errors = [];
  if (!isObj(s)) return { ok: false, errors: ['step: expected object'] };
  if (typeof s.key !== 'string' || s.key === '') errors.push('key: required non-empty string');
  if (s.kind !== undefined && !['element', 'body', 'outside'].includes(s.kind)) errors.push('kind: expected element, body or outside');
  if (s.box !== undefined && s.box !== null && !(isObj(s.box) && ['x', 'y', 'width', 'height'].every((k) => isNum(s.box[k])))) errors.push('box: expected { x, y, width, height } or null');
  return { ok: errors.length === 0, errors };
}
