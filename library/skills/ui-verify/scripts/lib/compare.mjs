// Porownania liczb, boxow i stylow CSS oraz zatwierdzone odchylenia (deviations).
import { parseColor, sameColor } from './color.mjs';

const EPS = 1e-9;

export function compareNumber(actual, expected, tolerancePx = 0) {
  const ok = (v) => typeof v === 'number' && Number.isFinite(v);
  if (!ok(actual) || !ok(expected)) return { pass: false, delta: NaN };
  const delta = actual - expected;
  return { pass: Math.abs(delta) <= tolerancePx + EPS, delta };
}

export function compareBox(a, b, tolerancePx = 0) {
  const findings = [];
  for (const prop of ['x', 'y', 'width', 'height']) {
    const r = compareNumber(a?.[prop], b?.[prop], tolerancePx);
    if (!r.pass) findings.push({ prop, actual: a?.[prop], expected: b?.[prop], delta: r.delta });
  }
  return { pass: findings.length === 0, findings };
}

const PX = /^\s*([+-]?(?:\d+\.?\d*|\.\d+))px\s*$/i;

function compareValue(actual, expected, opts) {
  if (typeof actual === 'number' && typeof expected === 'number') {
    return compareNumber(actual, expected, opts.tolerancePx || 0);
  }
  const a = String(actual ?? '');
  const e = String(expected ?? '');
  const pa = PX.exec(a);
  const pe = PX.exec(e);
  if (pa && pe) return compareNumber(Number(pa[1]), Number(pe[1]), opts.tolerancePx || 0);
  const ca = parseColor(a);
  const ce = parseColor(e);
  if (ca && ce) {
    const same = sameColor(ca, ce, opts.colorTolerance || 0) && Math.abs(ca.a - ce.a) < 0.01;
    return { pass: same };
  }
  return { pass: a.trim().toLowerCase() === e.trim().toLowerCase() };
}

// Porownuje wlasciwosci z `expected` (brak w `actual` = roznica).
export function compareStyles(actual, expected, opts = {}) {
  const findings = [];
  for (const prop of Object.keys(expected || {})) {
    const act = actual ? actual[prop] : undefined;
    const r = compareValue(act, expected[prop], opts);
    if (!r.pass) {
      const f = { prop, actual: act, expected: expected[prop] };
      if (r.delta !== undefined) f.delta = r.delta;
      findings.push(f);
    }
  }
  return { pass: findings.length === 0, findings };
}

function covers(dev, f) {
  if (!dev || typeof dev.reason !== 'string' || dev.reason.trim() === '') return false;
  if (dev.prop !== f.prop) return false;
  if (f.selector !== undefined && dev.selector !== f.selector) return false;
  if (dev.selector !== undefined && dev.selector !== f.selector) return false;
  if (dev.actual !== undefined && dev.actual !== f.actual) return false;
  if (dev.expected !== undefined && dev.expected !== f.expected) return false;
  return true;
}

export function applyDeviations(findings, deviations) {
  const devs = Array.isArray(deviations) ? deviations : [];
  const approved = [];
  const unapproved = [];
  for (const f of findings || []) (devs.some((d) => covers(d, f)) ? approved : unapproved).push(f);
  return { unapproved, approved };
}
