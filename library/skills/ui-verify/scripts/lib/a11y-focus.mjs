// Focus indicator verdicts from pixel counts measured around the element before and after focus.
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
