// Comparison of the contrast findings with an expected.json (acceptance mode --expect).
// Exit: 0 all listed elements match, 1 a mismatch, 3 a listed element is absent or UNVERIFIED. Elements not listed are ignored.

export class ExpectError extends Error {
  constructor(message) {
    super(message);
    this.name = 'ExpectError';
  }
}

const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
export const CHANNEL_TOLERANCE = 1;
export const DEFAULT_RATIO_TOLERANCE = 0.05;

// "rgb(1, 2, 3)", "#rrggbb", [r, g, b] or { r, g, b } to { r, g, b }; null when not understood.
export function parseRgb(v) {
  if (Array.isArray(v) && v.length >= 3 && v.slice(0, 3).every(isNum)) return { r: v[0], g: v[1], b: v[2] };
  if (isObj(v) && ['r', 'g', 'b'].every((k) => isNum(v[k]))) return { r: v.r, g: v.g, b: v.b };
  if (typeof v === 'string') {
    const m = /^\s*rgba?\(\s*([\d.]+)\s*[, ]\s*([\d.]+)\s*[, ]\s*([\d.]+)/i.exec(v);
    if (m) return { r: Number(m[1]), g: Number(m[2]), b: Number(m[3]) };
    const h = /^\s*#([0-9a-f]{6})\s*$/i.exec(v);
    if (h) return { r: parseInt(h[1].slice(0, 2), 16), g: parseInt(h[1].slice(2, 4), 16), b: parseInt(h[1].slice(4, 6), 16) };
  }
  return null;
}

export function parseExpected(obj) {
  if (!isObj(obj)) throw new ExpectError('expected file: expected a JSON object');
  if (!Array.isArray(obj.elements) || obj.elements.length === 0) throw new ExpectError('expected file: "elements" must be a non-empty list');
  const tolerance = obj.tolerance === undefined ? DEFAULT_RATIO_TOLERANCE : obj.tolerance;
  if (!isNum(tolerance) || tolerance < 0) throw new ExpectError('expected file: "tolerance" must be a number >= 0');
  obj.elements.forEach((e, i) => {
    if (!isObj(e) || typeof e.selector !== 'string' || e.selector === '') throw new ExpectError(`expected file: elements[${i}].selector is required`);
    if (e.ratio !== undefined && !isNum(e.ratio)) throw new ExpectError(`expected file: elements[${i}].ratio must be a number`);
    for (const k of ['foreground', 'effectiveBackground']) {
      if (e[k] !== undefined && parseRgb(e[k]) === null) throw new ExpectError(`expected file: elements[${i}].${k} is not a colour`);
    }
  });
  return { page: typeof obj.page === 'string' ? obj.page : null, thresholds: obj.thresholds ?? null, tolerance, elements: obj.elements };
}

const near = (a, b, tol) => Math.abs(a - b) <= tol + 1e-9;
const sameRgb = (a, b) => near(a.r, b.r, CHANNEL_TOLERANCE) && near(a.g, b.g, CHANNEL_TOLERANCE) && near(a.b, b.b, CHANNEL_TOLERANCE);

// thresholds of expected.json against the configured ones; accepts normal|text and large|largeText.
function thresholdDiffs(expected, actual) {
  if (!isObj(expected) || !actual) return [];
  const out = [];
  const pick = (o, names) => names.map((n) => o[n]).find(isNum);
  for (const [names, key] of [[['normal', 'text', 'normalText'], 'normal'], [['large', 'largeText'], 'large']]) {
    const want = pick(expected, names);
    if (want !== undefined && !near(want, actual[key], 1e-9)) out.push(`thresholds.${key}: expected ${want}, configured ${actual[key]}`);
  }
  return out;
}

/**
 * compareExpected(expected, contrastFindings, { thresholds }) -> { exit, result, entries, thresholdDiffs }
 * contrastFindings: report findings of rule "contrast": { selector, verdict, reason?, measured: { ratio, fontSizePx, foreground, effectiveBackground }, required }.
 */
export function compareExpected(expectedObj, findings, { thresholds = null } = {}) {
  const exp = parseExpected(expectedObj);
  const bySelector = new Map();
  for (const f of findings || []) if (f.rule === 'contrast' && f.selector && !bySelector.has(f.selector)) bySelector.set(f.selector, f);
  const tDiffs = thresholdDiffs(exp.thresholds, thresholds);
  const entries = exp.elements.map((e) => {
    const f = bySelector.get(e.selector);
    if (!f) return { selector: e.selector, status: 'missing', diffs: [] };
    if (f.verdict === 'UNVERIFIED') return { selector: e.selector, status: 'unverified', diffs: [], reason: f.reason };
    const m = f.measured || {};
    const diffs = [];
    if (e.fontSizePx !== undefined && !near(m.fontSizePx, e.fontSizePx, 0.01)) diffs.push(`fontSizePx: expected ${e.fontSizePx}, got ${m.fontSizePx}`);
    for (const k of ['foreground', 'effectiveBackground']) {
      if (e[k] === undefined) continue;
      const want = parseRgb(e[k]);
      const got = parseRgb(m[k]);
      if (!got || !sameRgb(want, got)) diffs.push(`${k}: expected rgb(${want.r},${want.g},${want.b}), got ${got ? `rgb(${got.r},${got.g},${got.b})` : 'none'}`);
    }
    if (e.ratio !== undefined && !(isNum(m.ratio) && near(m.ratio, e.ratio, exp.tolerance))) diffs.push(`ratio: expected ${e.ratio} (+/-${exp.tolerance}), got ${m.ratio}`);
    if (e.required !== undefined && !(isNum(f.required) && near(f.required, e.required, 1e-9))) diffs.push(`required: expected ${e.required}, got ${f.required}`);
    if (e.verdict !== undefined && e.verdict !== f.verdict) diffs.push(`verdict: expected ${e.verdict}, got ${f.verdict}`);
    return { selector: e.selector, status: diffs.length ? 'mismatch' : 'match', diffs };
  });
  const mismatch = entries.some((x) => x.status === 'mismatch') || tDiffs.length > 0;
  const absent = entries.some((x) => x.status === 'missing' || x.status === 'unverified');
  // A real mismatch (FAIL) wins over an absent element, like FAIL wins in combine().
  const exit = mismatch ? 1 : (absent ? 3 : 0);
  return { exit, result: exit === 0 ? 'MATCH' : (exit === 1 ? 'MISMATCH' : 'UNVERIFIED'), entries, thresholdDiffs: tDiffs };
}
