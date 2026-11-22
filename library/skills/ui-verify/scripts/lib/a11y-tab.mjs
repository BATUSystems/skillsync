// Analysis of a recorded Tab sequence: where it ends and the rules tab-positive-tabindex, tab-unreachable,
// tab-trap and tab-order-visual. Pure logic over TabStep and candidate lists (see a11y-schema.mjs).
import { SchemaError, validateTabStep } from './a11y-schema.mjs';

export const DEFAULT_MAX_STOPS = 500;
export const DEFAULT_ORDER_TOLERANCE_PX = 32;
export const ORDER_X_TOLERANCE_PX = 1;

// Cuts the raw focus sequence at its end. Ends: wrapped (back to the first step), body, outside (left the document),
// limit (maxStops reached), trap (same element 3 times in a row), cycle (closed on a step other than the first).
export function sequenceEnd(raw, { maxStops = DEFAULT_MAX_STOPS } = {}) {
  const steps = [];
  const seen = new Map();
  let run = 0;
  for (const step of raw || []) {
    const v = validateTabStep(step);
    if (!v.ok) throw new SchemaError(`invalid tab step: ${v.errors.join('; ')}`, v.errors);
    if (step.kind === 'body') return { steps, end: 'body', cycleStart: -1 };
    if (step.kind === 'outside') return { steps, end: 'outside', cycleStart: -1 };
    const last = steps[steps.length - 1];
    run = last && last.key === step.key ? run + 1 : 1;
    if (run >= 3) return { steps, end: 'trap', cycleStart: steps.length - 2 };
    if (steps.length && step.key === steps[0].key && !(last && last.key === step.key)) return { steps, end: 'wrapped', cycleStart: 0 };
    if (seen.has(step.key) && !(last && last.key === step.key)) return { steps, end: 'cycle', cycleStart: seen.get(step.key) };
    if (!seen.has(step.key)) seen.set(step.key, steps.length);
    steps.push(step);
    if (steps.length >= maxStops) return { steps, end: 'limit', cycleStart: -1 };
  }
  return { steps, end: 'limit', cycleStart: -1 };
}

/**
 * Candidate: a rendered target from the target-size pool, for the reachability rule.
 *   key, selector, tabindex (number | null), disabled, inert, inClosedDetails, hidden, inModal (booleans),
 *   radioGroup (string | null), radioChecked (boolean)
 */
export function isTabbable(c) {
  if (typeof c.tabindex === 'number' && c.tabindex < 0) return false;
  return !(c.disabled || c.inert || c.inClosedDetails || c.hidden);
}

// For radio groups only the checked member (or the first when none is checked) is a stop.
export function expectedStops(candidates) {
  const list = (candidates || []).filter(isTabbable);
  const groups = new Map();
  for (const c of list) {
    if (!c.radioGroup) continue;
    const g = groups.get(c.radioGroup) || [];
    g.push(c);
    groups.set(c.radioGroup, g);
  }
  const drop = new Set();
  for (const members of groups.values()) {
    const keep = members.find((m) => m.radioChecked) || members[0];
    for (const m of members) if (m !== keep) drop.add(m);
  }
  return list.filter((c) => !drop.has(c));
}

function sameRowBefore(prev, cur, dir, tol) {
  // true when `cur` sits visually before `prev` by more than the tolerance
  if (cur.y < prev.y - tol) return true;
  if (Math.abs(cur.y - prev.y) <= tol) {
    return dir === 'rtl'
      ? cur.x + cur.width > prev.x + prev.width + ORDER_X_TOLERANCE_PX
      : cur.x < prev.x - ORDER_X_TOLERANCE_PX;
  }
  return false;
}

/**
 * analyzeTab({ raw, candidates, options }) -> { sequence, end, findings, warnings }
 *   options: { maxStops, positiveTabindex: 'fail'|'warn', order: { mode: 'warn'|'fail', tolerancePx }, dir: 'ltr'|'rtl' }
 * Findings carry { rule, verdict, selector?, hashBasis?, basis?, message, note? }; hashBasis feeds the exception key.
 */
export function analyzeTab({ raw, candidates = [], options = {} }) {
  const maxStops = options.maxStops || DEFAULT_MAX_STOPS;
  const posMode = options.positiveTabindex === 'warn' ? 'warn' : 'fail';
  const orderMode = options.order && options.order.mode === 'fail' ? 'fail' : 'warn';
  const tol = options.order && Number.isFinite(options.order.tolerancePx) ? options.order.tolerancePx : DEFAULT_ORDER_TOLERANCE_PX;
  const dir = options.dir === 'rtl' ? 'rtl' : 'ltr';
  const seq = sequenceEnd(raw, { maxStops });
  const findings = [];
  const warnings = [];
  const note = (rule, f) => {
    if (f.mode === 'warn') warnings.push({ code: rule, level: 'warning', selector: f.selector, message: f.message });
    else findings.push({ rule, verdict: 'FAIL', ...f.finding });
  };

  const reported = new Set();
  for (const s of seq.steps) {
    if (typeof s.tabindex === 'number' && s.tabindex > 0 && !reported.has(s.key)) {
      reported.add(s.key);
      const msg = `tabindex="${s.tabindex}" overrides the natural order`;
      note('tab-positive-tabindex', { mode: posMode, selector: s.selector, message: msg, finding: { selector: s.selector, basis: 'best-practice', message: msg, measured: { tabindex: s.tabindex } } });
    }
  }

  const visited = new Set(seq.steps.map((s) => s.key));
  const stops = expectedStops(candidates);
  const unvisited = stops.filter((c) => !visited.has(c.key));
  let trap = false;
  if (seq.end === 'trap' || seq.end === 'cycle') {
    const cycleSteps = seq.steps.slice(Math.max(0, seq.cycleStart));
    const inModal = cycleSteps.length > 0 && cycleSteps.every((s) => s.inModal === true);
    const distinct = new Set(seq.steps.map((s) => s.key)).size;
    // A repeat of one element 3 times, or a cycle closed without ever leaving it, is a trap whenever at least two
    // different elements were visited (a page with a single focusable element is legitimate); a modal dialog is exempt.
    if (!inModal && distinct >= 2) {
      trap = true;
      const first = cycleSteps[0] ? cycleSteps[0].selector : '';
      const lastSel = cycleSteps.length ? cycleSteps[cycleSteps.length - 1].selector : '';
      findings.push({
        rule: 'tab-trap', verdict: 'FAIL', hashBasis: `${first}>${lastSel}`, selector: undefined,
        message: `focus cycles among ${new Set(cycleSteps.map((s) => s.key)).size} element(s) and never leaves them (${unvisited.length} other target(s) not reached)`,
        measured: { cycleLength: new Set(cycleSteps.map((s) => s.key)).size, unreached: unvisited.length },
      });
    }
  }

  if (seq.end === 'limit') {
    findings.push({ rule: 'tab-unreachable', verdict: 'UNVERIFIED', reason: 'sequence-truncated', selector: undefined, hashBasis: 'sequence-truncated', message: `Tab sequence reached the limit of ${maxStops} stops; reachability not verified` });
  } else if (!trap) {
    const modalOnly = seq.steps.length > 0 && seq.steps.every((s) => s.inModal === true);
    for (const c of unvisited) {
      if (modalOnly && !c.inModal) continue;
      findings.push({ rule: 'tab-unreachable', verdict: 'FAIL', selector: c.selector, message: 'target is not reached by the Tab key', measured: { tabindex: c.tabindex ?? null } });
    }
  }

  // Heuristic of the visual order check: two steps are "in the same row" when their top edges differ by at most
  // tolerancePx (default 32); within a row the horizontal direction (per dir) must not go back by more than 1 px,
  // otherwise a step is flagged only when it lands more than tolerancePx ABOVE the previous one. A repeated step
  // (same element twice in a row) is collapsed first, so it cannot produce a pair of its own.
  const ordered = seq.steps.filter((s, i, a) => s.inDocument !== false && s.box && !(i > 0 && a[i - 1].key === s.key));
  for (let i = 1; i < ordered.length; i++) {
    const prev = ordered[i - 1];
    const cur = ordered[i];
    if (sameRowBefore(prev.box, cur.box, dir, tol)) {
      const msg = `focus moves visually backwards (${dir}) from ${prev.selector} to ${cur.selector}`;
      note('tab-order-visual', { mode: orderMode, selector: cur.selector, message: msg, finding: { selector: undefined, hashBasis: `${prev.selector}>${cur.selector}`, message: msg, measured: { from: prev.selector, to: cur.selector } } });
    }
  }

  return { sequence: seq.steps.map((s) => s.selector), end: seq.end, findings, warnings };
}
