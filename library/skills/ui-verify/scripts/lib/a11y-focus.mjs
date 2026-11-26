// Focus indicator verdicts from pixel counts around the element: the screenshot with focus against the screenshot taken
// after the same element lost it (blur). The latter is not the state before focus: whatever the page changes on blur,
// or later on its own, inside the clip counts as indicator.
import { FOCUS_MIN_RATIO } from './a11y-focus-contrast.mjs';
import { SchemaError } from './a11y-schema.mjs';

export const DEFAULT_MIN_DIFF_PX = 4;
export const MOUSE_MODES = Object.freeze(['report', 'forbid', 'require']);
export const MAX_MOUSE_SELECTORS = 20;

// Keyboard focus must change at least minDiffPx pixels; transparent outline gives 0 and fails.
export function classifyKeyboard({ diffPx, minDiffPx = DEFAULT_MIN_DIFF_PX }) {
  if (!Number.isFinite(diffPx) || diffPx < 0) throw new SchemaError('diffPx: expected a non-negative number');
  return diffPx >= minDiffPx
    ? { rule: 'focus-keyboard-indicator', verdict: 'PASS', measured: { diffPx }, required: minDiffPx }
    : { rule: 'focus-keyboard-indicator', verdict: 'FAIL', measured: { diffPx }, required: minDiffPx };
}

// Modes of the opt-in rules focus-contrast and focus-appearance; a configuration without the key means "off".
export function focusRuleModes(focus) {
  return { contrast: focus.contrast ?? 'off', appearance: focus.appearance ?? 'off' };
}

const isLength = (v) => Number.isFinite(v) && v >= 0;

// What the numbers reported by the page allow the opt-in rules to judge (the keyboard indicator rule does not use this).
// clipped: the element reaches beyond the viewport (touching its edge is measured) or its outline reaches beyond the
// capture padding; numbers that cannot be read count as clipped. box: the rectangle with its corner radius, taken from
// the focused element, or null when it is not a single non-empty client rectangle with a numeric radius.
export function focusGeometry(info, viewport, pad) {
  const vr = info.vr;
  const rect = Boolean(vr) && Number.isFinite(vr.x) && Number.isFinite(vr.y) && isLength(vr.width) && isLength(vr.height);
  // without an outline its width and offset say nothing about the reach of the indicator
  const reaches = info.signals?.outlineStyle !== 'none' && !(Number.isFinite(info.outlineOffset) && isLength(info.outlineWidth) && info.outlineOffset + info.outlineWidth <= pad);
  const clipped = !rect || reaches
    || vr.x < 0 || vr.y < 0 || vr.x + vr.width > viewport.width || vr.y + vr.height > viewport.height;
  const shaped = rect && vr.width > 0 && vr.height > 0 && info.getClientRects === 1 && isLength(info.borderRadius);
  return { clipped, box: shaped ? { width: vr.width, height: vr.height, borderRadius: info.borderRadius } : null };
}

const round3 = (n) => (Number.isFinite(n) ? Math.round(n * 1000) / 1000 : n);

function contrastOutcome(m, minDiffPx) {
  const passed = m.qualifyingStrict >= minDiffPx;
  return {
    verdict: passed ? 'PASS' : 'FAIL',
    measured: { diffPx: m.diffPx, qualifyingStrict: m.qualifyingStrict, ratioMedian: round3(m.ratioMedian), ratioMin: round3(m.ratioMin) },
    required: FOCUS_MIN_RATIO,
    ...(passed ? {} : { note: `indicator visible but its contrast is too low: fewer than ${minDiffPx} changed pixels reach ${FOCUS_MIN_RATIO}:1` }),
  };
}

const shapeUnknown = (note) => ({ verdict: 'UNVERIFIED', reason: 'shape-unknown', note });

function appearanceOutcome(m, box) {
  if (m.threshold === null) return shapeUnknown('no single rectangle with a numeric corner radius: the required area is unknown');
  // a visually hidden control (a side of 1 px or less) or an area the allowances reduce to nothing says nothing about
  // the size of the indicator
  if (m.threshold <= 0 || (box && Math.min(box.width, box.height) <= 1)) return shapeUnknown('the element is too small for a meaningful required area');
  // a threshold below 1 px still needs one qualifying pixel
  const passed = m.qualifyingAA >= Math.max(1, m.threshold);
  const threshold = round3(m.threshold);
  return {
    verdict: passed ? 'PASS' : 'FAIL',
    measured: { diffPx: m.diffPx, qualifyingAA: m.qualifyingAA, requiredArea: round3(m.requiredArea), threshold },
    required: threshold,
    ...(passed ? {} : { note: `indicator area with ${FOCUS_MIN_RATIO}:1 contrast is smaller than required` }),
  };
}

// Findings (without selector) of the opt-in rules for one focused element. diffPx is the count of the keyboard indicator
// rule: when that rule fails, nothing is added. geometry: focusGeometry(); metrics: focusMetrics() or null when the
// screenshots could not be compared. In "report" mode a FAIL or an UNVERIFIED becomes INFO and PASS stays.
export function classifyFocusRules({ diffPx, minDiffPx = DEFAULT_MIN_DIFF_PX, modes, geometry, metrics }) {
  if (diffPx !== null && classifyKeyboard({ diffPx, minDiffPx }).verdict === 'FAIL') return [];
  const clipped = diffPx === null || geometry.clipped || !metrics;
  const findings = [];
  for (const [key, outcome] of [['contrast', () => contrastOutcome(metrics, minDiffPx)], ['appearance', () => appearanceOutcome(metrics, geometry.box)]]) {
    if (modes[key] === 'off') continue;
    const f = clipped
      ? { verdict: 'UNVERIFIED', reason: 'clipped', note: 'the focus indicator may reach outside the captured area: not measured' }
      : outcome();
    if (modes[key] === 'report' && f.verdict !== 'PASS') f.verdict = 'INFO';
    findings.push({ rule: `focus-${key}`, ...f });
  }
  return findings;
}

export function validateMouseConfig(m) {
  const errors = [];
  if (m === undefined || m === null) return { ok: true, errors, mouse: null };
  if (typeof m !== 'object' || Array.isArray(m)) return { ok: false, errors: ['focus.mouse: expected { mode, selectors }'], mouse: null };
  for (const k of Object.keys(m)) if (k !== 'mode' && k !== 'selectors') errors.push(`focus.mouse.${k}: unknown key`);
  if (!MOUSE_MODES.includes(m.mode)) errors.push('focus.mouse.mode: expected report, forbid or require');
  if (!Array.isArray(m.selectors) || m.selectors.length === 0 || m.selectors.length > MAX_MOUSE_SELECTORS || !m.selectors.every((s) => typeof s === 'string' && s.trim() !== '')) {
    errors.push(`focus.mouse.selectors: expected a list of 1..${MAX_MOUSE_SELECTORS} non-empty selectors`);
  }
  return { ok: errors.length === 0, errors, mouse: errors.length ? null : { mode: m.mode, selectors: [...m.selectors] } };
}

// Focus ring after a mouse click. report: INFO only; forbid: visible ring is FAIL; require: missing ring is FAIL.
export function classifyMouse({ mode, diffPx, minDiffPx = DEFAULT_MIN_DIFF_PX }) {
  if (!MOUSE_MODES.includes(mode)) throw new SchemaError(`unknown mouse mode "${mode}"`);
  if (!Number.isFinite(diffPx) || diffPx < 0) throw new SchemaError('diffPx: expected a non-negative number');
  const visible = diffPx >= minDiffPx;
  const base = { rule: 'focus-mouse-ring', measured: { diffPx, visible }, required: minDiffPx };
  if (mode === 'report') return { ...base, verdict: 'INFO', note: visible ? 'ring visible after click' : 'no ring after click' };
  if (mode === 'forbid') return { ...base, verdict: visible ? 'FAIL' : 'PASS' };
  return { ...base, verdict: visible ? 'PASS' : 'FAIL' };
}
