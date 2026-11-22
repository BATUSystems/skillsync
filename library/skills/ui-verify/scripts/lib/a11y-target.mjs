// WCAG 2.5.8 (Target Size, Minimum) on measured boxes: size, equivalent label, spacing circle, inline, user agent control.
import { SchemaError, validateTargetItem } from './a11y-schema.mjs';

export const MIN_TARGET_PX = 24;

const isSmall = (box, min) => box.width < min || box.height < min;
const center = (b) => ({ x: b.x + b.width / 2, y: b.y + b.height / 2 });

// Distance from a point to the closest point of a rectangle (0 when inside).
export function distanceToBox(pt, box) {
  const dx = Math.max(box.x - pt.x, 0, pt.x - (box.x + box.width));
  const dy = Math.max(box.y - pt.y, 0, pt.y - (box.y + box.height));
  return Math.hypot(dx, dy);
}

// The 24 px circle centred on `item` crosses no other target box and no circle of another target smaller than 24 px.
export function spacingOk(item, others, min = MIN_TARGET_PX) {
  const r = min / 2;
  const c = center(item.box);
  for (const o of others) {
    if (distanceToBox(c, o.box) < r) return false;
    if (isSmall(o.box, min)) {
      const oc = center(o.box);
      if (Math.hypot(c.x - oc.x, c.y - oc.y) < min) return false;
    }
  }
  return true;
}

// items: TargetItem[]. The pool is the rendered items only. Returns one result per pool item, in input order.
export function evaluateTargets(items, { minPx = MIN_TARGET_PX } = {}) {
  const pool = [];
  const seen = new Set();
  for (const t of items || []) {
    const v = validateTargetItem(t);
    if (!v.ok) throw new SchemaError(`invalid target: ${v.errors.join('; ')}`, v.errors);
    // One result per element: the page-assigned `uid` identifies an element; without it the selector does.
    // Two DIFFERENT elements with one selector are both kept (the caller warns about the collision).
    const id = t.uid !== undefined ? `uid:${t.uid}` : `sel:${t.selector}`;
    if (t.rendered === false || seen.has(id)) continue;
    seen.add(id);
    pool.push(t);
  }
  return pool.map((t) => {
    const out = { selector: t.selector, widthPx: t.box.width, heightPx: t.box.height };
    if (!isSmall(t.box, minPx)) return { ...out, verdict: 'PASS' };
    const lb = t.labelBox;
    if (lb && lb.width >= minPx && lb.height >= minPx) return { ...out, verdict: 'PASS', exception: 'equivalent-label', note: 'equivalent: label' };
    const others = pool.filter((o) => o !== t && !(t.pair !== undefined && o.pair === t.pair));
    if (spacingOk(t, others, minPx)) return { ...out, verdict: 'PASS', exception: 'spacing', note: 'spacing' };
    if (t.display === 'inline' && t.parentHasText === true) return { ...out, verdict: 'EXEMPT', reason: 'inline' };
    if (t.userAgent === true) return { ...out, verdict: 'EXEMPT', reason: 'user-agent' };
    return { ...out, verdict: 'FAIL', required: minPx };
  });
}
