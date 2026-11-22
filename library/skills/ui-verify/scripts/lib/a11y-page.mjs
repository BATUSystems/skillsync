// Browser adapter of the accessibility audit. Three parts: (1) the fixed page script (page.evaluate, data only as arguments),
// (2) pure helpers that turn raw page data into contrast samples, targets, Tab steps and findings, (3) the Playwright
// orchestration of one page. The page script is a function WITHOUT closures: Playwright sends its source text to the page.
import nodeFs from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { assertRealInside, checkOrigin } from './policy.mjs';
import { decodePng, makePixelmatchFlagger } from './runtime.mjs';
import { LIMITS, cleanText, reportSelector } from './a11y-schema.mjs';
import { measureSample } from './a11y-contrast.mjs';
import { evaluateTargets } from './a11y-target.mjs';
import { classifyKeyboard, classifyMouse } from './a11y-focus.mjs';
import { analyzeTab, sequenceEnd } from './a11y-tab.mjs';
import { runLoginSteps } from './a11y-login.mjs';

export class A11yPageError extends Error {
  constructor(message) {
    super(message);
    this.name = 'A11yPageError';
  }
}

export const NAV_TIMEOUT_MS = 60000;
export const ACTION_TIMEOUT_MS = 10000;
const SHOT_TIMEOUT_MS = 60000;
export const MAX_POINTS_PER_ELEMENT = 2000;
export const FOCUS_PAD_PX = 8;
export const CHUNK_PX = 4000;
export const SINGLE_SHOT_MAX_PX = 16000;
export const MAX_PAGE_PX = 64000;
export const MAX_PAGE_PIXELS = 40000000;
export const MAX_PAGE_WIDTH_PX = 4000;
export const PIXEL_THRESHOLD = 0.1;

// Applied to the screenshot only (page.screenshot style option, so a page CSP cannot block it): every kind of text paint
// becomes transparent, so the screenshot shows the background under the text.
export const HIDE_TEXT_CSS = '*, *::before, *::after, ::first-letter, ::first-line, ::marker, ::placeholder, ::file-selector-button { color: transparent !important; -webkit-text-fill-color: transparent !important; -webkit-text-stroke: 0 !important; text-shadow: none !important; text-decoration-color: transparent !important; text-emphasis-color: transparent !important; caret-color: transparent !important }';

// ---------------------------------------------------------------------------------------------------------------
// Part 1: the page script. Operations (args.op): validate, visible, assertOk, settle, recheck, collect, resetFocus, active, blur,
// refocus, reveal. Everything that leaves the page is data (numbers, short strings); DOM nodes never do.
// ---------------------------------------------------------------------------------------------------------------
export async function pageScript(args) {
  const STORE = Symbol.for('ui-verify-a11y-store');
  const store = globalThis[STORE] || (globalThis[STORE] = { ids: new WeakMap(), count: 0, last: null });
  const q2 = (n) => Math.round(n * 100) / 100;
  const num = (v, d) => { const n = parseFloat(v); return Number.isFinite(n) ? n : d; };
  const cut = (v, n) => String(v === undefined || v === null ? '' : v).slice(0, n);
  const scrollNow = () => [globalThis.scrollX, globalThis.scrollY];
  const jump = (x, y) => globalThis.scrollTo({ left: x, top: y, behavior: 'instant' });
  const upper = (el) => el.tagName.toUpperCase();
  const keyOf = (el) => {
    let k = store.ids.get(el);
    if (!k) { store.count += 1; k = `k${store.count}`; store.ids.set(el, k); }
    return k;
  };
  const trySelector = (sel) => {
    try { return { list: Array.from(document.querySelectorAll(sel)) }; } catch { return null; }
  };

  // Flat-tree parent: the parent element, or the host of a shadow root, or the frame element of a frame document.
  const up = (n) => {
    if (n.parentElement) return n.parentElement;
    const root = n.getRootNode ? n.getRootNode() : null;
    if (root && root.host) return root.host;
    try { return n.ownerDocument.defaultView.frameElement || null; } catch { return null; }
  };
  const isRelated = (hit, el) => {
    if (el.contains(hit)) return true;
    for (let n = el, i = 0; n && i < 200; n = up(n), i++) if (n === hit) return true;
    return false;
  };
  // Open shadow roots (depth up to 10, no loops) and a count of custom elements that may hide a closed shadow root.
  const rootsInfo = { roots: null, closedGuess: 0 };
  const deepRoots = () => {
    if (rootsInfo.roots) return rootsInfo.roots;
    const roots = [document];
    const depth = new Map([[document, 0]]);
    for (let i = 0; i < roots.length && roots.length < 2000; i++) {
      for (const e of roots[i].querySelectorAll('*')) {
        if (e.shadowRoot) {
          if (!depth.has(e.shadowRoot) && depth.get(roots[i]) < 10) { depth.set(e.shadowRoot, depth.get(roots[i]) + 1); roots.push(e.shadowRoot); }
        } else if (e.tagName.includes('-') && e.childNodes.length === 0) rootsInfo.closedGuess += 1;
      }
    }
    rootsInfo.roots = roots;
    return roots;
  };
  const deepAll = (sel) => deepRoots().flatMap((r) => Array.from(r.querySelectorAll(sel)));
  // Rectangle of an element in the viewport of the top window (frames add the offset of their frame element).
  const absRect = (el) => {
    const r = el.getBoundingClientRect();
    let x = r.left;
    let y = r.top;
    try {
      for (let w = el.ownerDocument.defaultView; w && w !== globalThis && w.frameElement; w = w.parent) {
        const f = w.frameElement;
        const fr = f.getBoundingClientRect();
        const fs = getComputedStyle(f);
        x += fr.left + f.clientLeft + num(fs.paddingLeft, 0);
        y += fr.top + f.clientTop + num(fs.paddingTop, 0);
      }
    } catch { /* cross-origin frame */ }
    return { left: x, top: y, width: r.width, height: r.height, right: x + r.width, bottom: y + r.height };
  };

  // Selector parts for the report (built by the caller): the id (when unique) and the tag/nth-of-type chain.
  let idCounts = null;
  const idUnique = (id) => {
    if (idCounts === null) {
      idCounts = new Map();
      for (const n of document.querySelectorAll('[id]')) idCounts.set(n.id, (idCounts.get(n.id) || 0) + 1);
    }
    return idCounts.get(id) === 1;
  };
  const describe = (el) => {
    const chain = [];
    for (let n = el; n && n.nodeType === 1 && chain.length < 64; n = up(n)) {
      let nth = 1;
      for (let s = n.previousElementSibling; s; s = s.previousElementSibling) if (s.tagName === n.tagName) nth += 1;
      chain.unshift({ tag: cut(n.tagName.toLowerCase(), 200), nth });
    }
    const id = typeof el.id === 'string' && el.id.length <= 200 ? el.id : '';
    return { id, idUnique: id !== '' && el.getRootNode() === document && idUnique(id), chain };
  };

  // Colour to sRGB through a canvas 2D context; null when the value cannot be converted.
  const colorCache = new Map();
  let canvasCtx;
  const ctx2d = () => {
    if (canvasCtx === undefined) {
      try { canvasCtx = document.createElement('canvas').getContext('2d', { willReadFrequently: true }); } catch { canvasCtx = null; }
    }
    return canvasCtx;
  };
  const convertColor = (value) => {
    const c = ctx2d();
    if (!c || typeof value !== 'string' || value === '') return null;
    const probe = (seed) => { c.fillStyle = seed; c.fillStyle = value; return String(c.fillStyle); };
    const a = probe('#010203');
    if (a !== probe('#040506')) return null; // value ignored by the canvas: not a colour it understands
    let m = /^#([0-9a-f]{6})$/i.exec(a);
    if (m) return { r: parseInt(m[1].slice(0, 2), 16), g: parseInt(m[1].slice(2, 4), 16), b: parseInt(m[1].slice(4, 6), 16), a: 1 };
    m = /^rgba\((\d+), (\d+), (\d+), ([0-9.]+)\)$/.exec(a);
    if (m) return { r: Number(m[1]), g: Number(m[2]), b: Number(m[3]), a: Number(m[4]) };
    c.clearRect(0, 0, 1, 1);
    c.fillStyle = value;
    c.fillRect(0, 0, 1, 1);
    const d = c.getImageData(0, 0, 1, 1).data;
    return { r: d[0], g: d[1], b: d[2], a: d[3] / 255 };
  };
  const toRgba = (value) => {
    if (!colorCache.has(value)) colorCache.set(value, convertColor(value));
    return colorCache.get(value);
  };

  // Own paint of one element (background, image, border, shadow); an unreadable colour counts as paint.
  const ownPaint = (s) => {
    const bg = toRgba(s.backgroundColor);
    if (bg === null || bg.a > 0) return true;
    if (s.backgroundImage !== 'none' || s.boxShadow !== 'none') return true;
    return ['Top', 'Right', 'Bottom', 'Left'].some((side) => num(s[`border${side}Width`], 0) > 0 && s[`border${side}Style`] !== 'none' && s[`border${side}Style`] !== 'hidden');
  };

  // One pass up the ancestors: not rendered, opacity product, opacity group with own paint, filter and similar, background-clip: text.
  const walkUp = (el, own) => {
    const out = { opacity: 1, group: false, filter: false, clipText: false, hidden: own.visibility !== 'visible' };
    const path = [];
    for (let n = el; n && n.nodeType === 1; n = up(n)) {
      const s = n === el ? own : getComputedStyle(n);
      path.push(s);
      if (s.display === 'none') out.hidden = true;
      if (s.display !== 'contents' && n !== document.documentElement && n !== document.body) {
        const r = n.getBoundingClientRect();
        if (r.width <= 1 && r.height <= 1 && (s.clip !== 'auto' || s.clipPath !== 'none' || s.overflowX === 'hidden' || s.overflowY === 'hidden')) out.hidden = true;
      }
      const o = num(s.opacity, 1);
      if (o < 1) {
        out.opacity *= o;
        if (path.some(ownPaint)) out.group = true;
      }
      if (s.filter !== 'none' || (s.backdropFilter || s.webkitBackdropFilter || 'none') !== 'none' || s.mixBlendMode !== 'normal' || (s.maskImage || s.webkitMaskImage || 'none') !== 'none') out.filter = true;
      if (s.backgroundClip === 'text' || s.webkitBackgroundClip === 'text') out.clipText = true;
    }
    return out;
  };

  const inactiveOf = (el) => {
    try { if (el.matches(':disabled')) return true; } catch { /* selector unsupported */ }
    return el.closest('[aria-disabled="true"]') !== null;
  };
  const autofillOf = (el) => {
    for (const sel of [':autofill', ':-webkit-autofill']) {
      try { if (el.matches(sel)) return true; } catch { /* selector unsupported */ }
    }
    return false;
  };
  const docRects = (list) => {
    const [sx, sy] = scrollNow();
    return list.slice(0, 40).map((r) => [q2(r.left + sx), q2(r.top + sy), q2(r.width), q2(r.height)]);
  };
  const textRects = (el) => {
    const out = [];
    for (const n of el.childNodes) {
      if (n.nodeType !== 3 || n.data.trim() === '') continue;
      const range = document.createRange();
      range.selectNodeContents(n);
      for (const r of range.getClientRects()) if (r.width > 0 && r.height > 0) out.push(r);
    }
    return out;
  };
  const contentBox = (el, s) => {
    const r = el.getBoundingClientRect();
    const l = num(s.borderLeftWidth, 0) + num(s.paddingLeft, 0);
    const t = num(s.borderTopWidth, 0) + num(s.paddingTop, 0);
    const w = r.width - l - num(s.borderRightWidth, 0) - num(s.paddingRight, 0);
    const h = r.height - t - num(s.borderBottomWidth, 0) - num(s.paddingBottom, 0);
    return w > 0 && h > 0 ? [{ left: r.left + l, top: r.top + t, width: w, height: h }] : [];
  };

  const SKIP_TEXT = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'OPTION', 'OPTGROUP', 'TEXTAREA', 'TITLE']);
  const NO_TEXT_INPUT = new Set(['hidden', 'checkbox', 'radio', 'file', 'range', 'color', 'image']);
  const MEDIA = new Set(['VIDEO', 'CANVAS', 'IFRAME', 'SVG']);

  const contrastItem = (el, kind) => {
    const elCs = getComputedStyle(el);
    const textCs = kind === 'placeholder' ? getComputedStyle(el, '::placeholder') : elCs;
    const rects = kind === 'text' ? textRects(el) : contentBox(el, elCs);
    const up = walkUp(el, elCs);
    const fill = textCs.getPropertyValue('-webkit-text-fill-color') || textCs.color;
    const important = el.hasAttribute('style') && ['color', '-webkit-text-fill-color'].some((p) => el.style.getPropertyPriority(p) === 'important');
    return {
      el,
      kind,
      ...describe(el),
      fontSizePx: num(textCs.fontSize, 16),
      fontWeight: num(textCs.fontWeight, 400),
      color: toRgba(fill),
      opacity: Math.round(up.opacity * (kind === 'placeholder' ? num(textCs.opacity, 1) : 1) * 10000) / 10000,
      hidden: up.hidden,
      inactive: inactiveOf(el),
      flags: { filter: up.filter, clipText: up.clipText, group: up.group, important, autofill: autofillOf(el), media: false, hit: null },
      rects: docRects(rects),
      rectsCut: rects.length > 40,
    };
  };

  // Text under a hit test: the point must hit the element, an ancestor or a descendant; otherwise it is covered or clipped.
  const allowHits = (el) => {
    if (getComputedStyle(el).pointerEvents !== 'none') return () => {};
    const prev = el.style.getPropertyValue('pointer-events');
    const pri = el.style.getPropertyPriority('pointer-events');
    el.style.setProperty('pointer-events', 'auto', 'important');
    return () => { if (prev) el.style.setProperty('pointer-events', prev, pri); else el.style.removeProperty('pointer-events'); };
  };
  const clippedByAncestor = (el, r, sx, sy) => {
    for (let n = up(el); n && n !== document.body && n !== document.documentElement; n = up(n)) {
      const s = getComputedStyle(n);
      if (s.overflowX === 'visible' && s.overflowY === 'visible') continue;
      const b = n.getBoundingClientRect();
      if (r[0] < b.left + sx - 0.5 || r[1] < b.top + sy - 0.5 || r[0] + r[2] > b.right + sx + 0.5 || r[1] + r[3] > b.bottom + sy + 0.5) return true;
    }
    return false;
  };
  const probeRect = (g, sx, sy, vw, vh) => {
    const { it, r } = g;
    const inset = Math.min(1, r[2] / 4, r[3] / 4);
    const pts = [[r[0] + inset, r[1] + inset], [r[0] + r[2] - inset, r[1] + inset], [r[0] + inset, r[1] + r[3] - inset], [r[0] + r[2] - inset, r[1] + r[3] - inset], [g.cx, g.cy]];
    const at = (x, y) => [Math.min(Math.max(x - sx, 0), vw - 1), Math.min(Math.max(y - sy, 0), vh - 1)];
    const restore = allowHits(it.el);
    try {
      for (const [x, y] of pts) {
        const hit = document.elementFromPoint(...at(x, y));
        if (!hit) { it.flags.hit = it.flags.hit || 'clipped'; continue; }
        if (hit !== it.el && !isRelated(hit, it.el) && !hit.contains(it.el)) it.flags.hit = it.flags.hit || (clippedByAncestor(it.el, r, sx, sy) ? 'clipped' : 'occluded');
      }
      if (document.elementsFromPoint(...at(g.cx, g.cy)).some((e) => MEDIA.has(upper(e)))) it.flags.media = true;
    } finally {
      restore();
    }
  };
  const hitPass = (items) => {
    const vw = globalThis.innerWidth;
    const vh = globalThis.innerHeight;
    const docW = Math.max(document.documentElement.scrollWidth, document.body ? document.body.scrollWidth : 0, vw);
    const docH = Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0, vh);
    const cells = new Map();
    for (const it of items) {
      if (it.hidden || it.inactive || it.rects.length === 0) continue;
      const n = it.rects.length;
      for (const k of new Set([0, Math.floor((n - 1) / 2), n - 1])) {
        const r = it.rects[k];
        const cx = r[0] + r[2] / 2;
        const cy = r[1] + r[3] / 2;
        if (cx < 0 || cy < 0 || cx > docW || cy > docH) { it.flags.hit = it.flags.hit || 'clipped'; continue; }
        const key = `${Math.floor(cx / vw)},${Math.floor(cy / vh)}`;
        if (!cells.has(key)) cells.set(key, { x: Math.floor(cx / vw) * vw, y: Math.floor(cy / vh) * vh, list: [] });
        cells.get(key).list.push({ it, r, cx, cy });
      }
    }
    for (const cell of cells.values()) {
      jump(cell.x, cell.y);
      const [sx, sy] = scrollNow();
      for (const g of cell.list) probeRect(g, sx, sy, vw, vh);
    }
    jump(0, 0);
  };

  const collectContrast = (limit) => {
    const owners = new Set();
    for (const root of deepRoots()) {
      const walker = document.createTreeWalker(root === document ? (document.body || document.documentElement) : root, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const p = n.parentElement;
        if (p && n.data.trim() !== '' && !SKIP_TEXT.has(upper(p))) owners.add(p);
      }
    }
    const list = Array.from(owners, (el) => ({ el, kind: 'text' }));
    for (const f of deepAll('input, textarea, select')) {
      const tag = upper(f);
      if (tag === 'INPUT' && NO_TEXT_INPUT.has((f.type || '').toLowerCase())) continue;
      const hasValue = tag === 'SELECT' ? Boolean(f.selectedOptions[0] && f.selectedOptions[0].text.trim() !== '') : f.value !== '';
      if (hasValue) list.push({ el: f, kind: 'value' });
      else if (tag !== 'SELECT' && (f.getAttribute('placeholder') || '').trim() !== '') list.push({ el: f, kind: 'placeholder' });
    }
    const items = list.slice(0, limit).map((x) => contrastItem(x.el, x.kind));
    hitPass(items);
    store.contrast = items.map((it) => ({ el: it.el, kind: it.kind, rect: it.rects[0] || null }));
    return { truncated: list.length > limit, items: items.map(({ el, ...rest }) => rest) };
  };

  const TARGET_SELECTOR = 'a[href], button, input:not([type="hidden"]), select, textarea, summary, label, [role="button"], [role="link"], [role="checkbox"], [role="radio"], [role="tab"], [role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"], [role="switch"], [role="option"], [role="slider"], [role="combobox"], [role="treeitem"]';
  const UA_TYPES = new Set(['checkbox', 'radio', 'range', 'color', 'file', 'date']);
  const uaSizes = new Map();
  // Size of an unstyled native control: measured inside a closed shadow root, so page style sheets do not reach it.
  const uaSize = (type) => {
    if (uaSizes.has(type)) return uaSizes.get(type);
    let size = null;
    let host = null;
    try {
      host = document.createElement('div');
      host.style.cssText = 'all:initial;position:absolute;left:-99999px;top:0;visibility:hidden';
      const root = host.attachShadow({ mode: 'closed' });
      const input = document.createElement('input');
      input.type = type;
      root.appendChild(input);
      document.body.appendChild(host);
      const r = input.getBoundingClientRect();
      size = { width: r.width, height: r.height };
    } catch { size = null; }
    if (host && host.parentNode) host.parentNode.removeChild(host);
    uaSizes.set(type, size);
    return size;
  };
  const boxOf = (el) => {
    const r = absRect(el);
    const [sx, sy] = scrollNow();
    return { x: q2(r.left + sx), y: q2(r.top + sy), width: q2(r.width), height: q2(r.height) };
  };
  const targetItem = (el, cs) => {
    const tag = upper(el);
    const type = tag === 'INPUT' ? (el.type || '').toLowerCase() : '';
    const box = boxOf(el);
    const labels = el.labels ? Array.from(el.labels) : [];
    const control = tag === 'LABEL' ? el.control : null;
    const shown = labels.find((l) => { const r = l.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
    const labelBox = ['checkbox', 'radio', 'file'].includes(type) && shown ? { width: q2(shown.getBoundingClientRect().width), height: q2(shown.getBoundingClientRect().height) } : null;
    let userAgent = false;
    if (UA_TYPES.has(type) && (cs.appearance || cs.webkitAppearance) !== 'none') {
      const ua = uaSize(type);
      userAgent = ua !== null && Math.abs(ua.width - box.width) <= 0.5 && Math.abs(ua.height - box.height) <= 0.5;
    }
    const raw = el.getAttribute('tabindex');
    const ti = raw === null ? NaN : parseInt(raw, 10);
    const details = el.closest('details:not([open])');
    return {
      key: keyOf(el),
      ...describe(el),
      focusable: tag !== 'LABEL',
      box,
      display: cs.display,
      parentHasText: Array.from(el.parentNode ? el.parentNode.childNodes : []).some((n) => n.nodeType === 3 && n.data.trim() !== ''),
      userAgent,
      labelBox,
      pair: control ? keyOf(control) : (labels.length ? keyOf(el) : undefined),
      tabindex: Number.isFinite(ti) ? ti : null,
      disabled: inactiveOf(el),
      inert: el.closest('[inert]') !== null,
      inClosedDetails: Boolean(details) && !(tag === 'SUMMARY' && el.parentElement === details),
      inModal: el.closest('[role="dialog"][aria-modal="true"], dialog[open]') !== null,
      radioGroup: type === 'radio' && el.name ? `${el.form ? keyOf(el.form) : 'doc'}|${cut(el.name, 200)}` : null,
      radioChecked: type === 'radio' && el.checked === true,
    };
  };
  const collectTargets = (limit) => {
    const items = [];
    let truncated = false;
    for (const el of deepAll(TARGET_SELECTOR)) {
      if (upper(el) === 'LABEL' && !el.control) continue;
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0 || walkUp(el, cs).hidden) continue;
      if (items.length >= limit) { truncated = true; break; }
      items.push(targetItem(el, cs));
    }
    return { truncated, items };
  };

  const activeInfo = () => {
    let a = document.activeElement;
    if (!a || a === document.body || a === document.documentElement) return { kind: 'body' };
    // Descend into open shadow roots and same-origin frames: the innermost focused element is the Tab stop.
    for (let depth = 0; depth < 10; depth++) {
      if (a.shadowRoot && a.shadowRoot.activeElement) { a = a.shadowRoot.activeElement; continue; }
      if (['IFRAME', 'FRAME'].includes(upper(a))) {
        let inner = null;
        try { inner = a.contentDocument && a.contentDocument.activeElement; } catch { inner = null; }
        if (inner === null && !a.contentDocument) return { kind: 'outside' };
        if (inner && inner !== inner.ownerDocument.body && inner !== inner.ownerDocument.documentElement) { a = inner; continue; }
      }
      break;
    }
    const r = absRect(a);
    const s = getComputedStyle(a);
    const raw = a.getAttribute('tabindex');
    const ti = raw === null ? NaN : parseInt(raw, 10);
    let focusVisible = false;
    try { focusVisible = a.matches(':focus-visible'); } catch { focusVisible = false; }
    const [sx, sy] = scrollNow();
    return {
      kind: 'element',
      key: keyOf(a),
      ...describe(a),
      tabindex: Number.isFinite(ti) ? ti : null,
      box: boxOf(a),
      vr: { x: r.left, y: r.top, width: r.width, height: r.height },
      inDocument: r.right + sx > 0 && r.bottom + sy > 0,
      inModal: a.closest('[role="dialog"][aria-modal="true"], dialog[open]') !== null,
      focusVisible,
      signals: {
        outlineStyle: cut(s.outlineStyle, 40), outlineWidth: cut(s.outlineWidth, 40), outlineColor: cut(s.outlineColor, 80), boxShadow: cut(s.boxShadow, 80),
        borderColor: cut(s.borderTopColor, 80), backgroundColor: cut(s.backgroundColor, 80), textDecoration: cut(s.textDecorationLine, 40), color: cut(s.color, 80),
      },
    };
  };

  switch (args.op) {
    case 'validate':
      return args.selectors.map((s) => typeof s === 'string' && trySelector(s) !== null);
    case 'visible': {
      const found = typeof args.selector === 'string' ? trySelector(args.selector) : null;
      const el = found && found.list[0];
      if (!el) return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility === 'visible';
    }
    case 'assertOk': {
      const found = typeof args.selector === 'string' ? trySelector(args.selector) : null;
      if (!found) return false;
      if (typeof args.text !== 'string') return found.list.length > 0;
      return found.list.some((e) => (e.textContent || '').includes(args.text));
    }
    case 'settle': {
      // Fonts, then one pass over the whole document (lazy images load when they come into view), then every image decoded.
      if (document.fonts && document.fonts.ready) await document.fonts.ready;
      const vw = globalThis.innerWidth;
      const vh = globalThis.innerHeight;
      const docH = () => Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0, vh);
      const pause = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });
      for (let y = 0, steps = 0; y < docH() && steps < 200; y += vh, steps++) {
        jump(0, y);
        await pause(30);
      }
      jump(0, 0);
      const decode = Promise.all(Array.from(document.images).map((i) => (i.decode ? i.decode().catch(() => undefined) : undefined)));
      await Promise.race([decode, pause(5000)]);
      return { width: Math.max(document.documentElement.scrollWidth, document.body ? document.body.scrollWidth : 0, vw), height: docH() };
    }
    case 'recheck': {
      // After the screenshot: did the document or a sampled rectangle move (late content, scripts)?
      let shifted = 0;
      for (const it of store.contrast || []) {
        if (!it.el.isConnected) { shifted += 1; continue; }
        const rs = it.kind === 'text' ? textRects(it.el) : contentBox(it.el, getComputedStyle(it.el));
        const now = rs.length ? docRects(rs)[0] : null;
        if (!it.rect || !now || now.some((v, k) => Math.abs(v - it.rect[k]) > 1)) shifted += 1;
      }
      return { height: Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0, globalThis.innerHeight), shifted };
    }
    case 'collect': {
      jump(0, 0);
      const out = { frames: document.querySelectorAll('iframe, frame').length, dir: getComputedStyle(document.body || document.documentElement).direction };
      if (args.want.targets) out.targets = collectTargets(args.limits.targets);
      if (args.want.contrast) out.contrast = collectContrast(args.limits.contrast);
      deepRoots();
      out.closedShadowGuess = rootsInfo.closedGuess;
      out.openShadowRoots = rootsInfo.roots.length - 1;
      return out;
    }
    case 'resetFocus':
      if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
      jump(0, 0);
      return true;
    case 'active':
      return activeInfo();
    case 'blur':
      store.last = document.activeElement;
      if (store.last && store.last.blur) store.last.blur();
      return true;
    case 'refocus':
      if (store.last && store.last.isConnected && store.last.focus) store.last.focus({ preventScroll: true });
      store.last = null;
      return true;
    case 'reveal': {
      const found = typeof args.selector === 'string' ? trySelector(args.selector) : null;
      const el = found && found.list[0];
      if (!el) return { found: false };
      el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
      const r = el.getBoundingClientRect();
      const hit = document.elementFromPoint(Math.min(Math.max(r.left + r.width / 2, 0), globalThis.innerWidth - 1), Math.min(Math.max(r.top + r.height / 2, 0), globalThis.innerHeight - 1));
      return { found: true, ...describe(el), covered: !hit || !isRelated(hit, el), vr: { x: r.left, y: r.top, width: r.width, height: r.height } };
    }
    default:
      throw new Error('unknown page operation');
  }
}

// The fixed text of the page script (what Playwright sends); used by tests to check that no configuration data is spliced in.
export const PAGE_SCRIPT = String(pageScript);

const callPage = (page, args) => page.evaluate(pageScript, args);

// ---------------------------------------------------------------------------------------------------------------
// Part 2: pure helpers (no browser)
// ---------------------------------------------------------------------------------------------------------------

export function selectorOf(raw, suffix = '') {
  return cleanText(`${reportSelector(raw)}${suffix}`, LIMITS.selectorChars);
}

// Path shown in the report: a file URL below the configuration directory becomes "<config-dir>/...".
export function displayUrl(url, configDir) {
  const base = pathToFileURL(configDir).href.replace(/\/+$/, '');
  const text = String(url);
  return text === base || text.startsWith(`${base}/`) ? `<config-dir>${text.slice(base.length)}` : text;
}

// ----- contrast -----

// Precedence: EXEMPT (not rendered, inactive), then UNVERIFIED reasons. Returns {} when the element can be measured.
export function classifyContrastRaw(raw) {
  if (raw.hidden === true || !Array.isArray(raw.rects) || raw.rects.length === 0) return { exempt: 'not-rendered' };
  if (raw.inactive === true) return { exempt: 'inactive' };
  const f = raw.flags || {};
  if (f.filter) return { unverified: 'filter-or-blend' };
  if (f.clipText) return { unverified: 'background-clip-text' };
  if (f.group) return { unverified: 'opacity-group-with-paint' };
  if (f.media) return { unverified: 'media-background' };
  if (raw.color === null || raw.color === undefined) return { unverified: 'color-unconvertible' };
  if (f.important) return { unverified: 'inline-important-color' };
  if (f.autofill) return { unverified: 'autofill' };
  if (f.hit === 'occluded' || f.hit === 'clipped') return { unverified: f.hit };
  return {};
}

const clamp01 = (n) => (Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : 1);

export function toContrastSample(raw, decision, pixels) {
  const sample = {
    selector: selectorOf(raw, raw.kind === 'placeholder' ? '::placeholder' : ''),
    fontSizePx: Number.isFinite(raw.fontSizePx) && raw.fontSizePx > 0 ? raw.fontSizePx : 16,
    fontWeight: Number.isFinite(raw.fontWeight) && raw.fontWeight >= 1 && raw.fontWeight <= 1000 ? raw.fontWeight : 400,
    opacity: clamp01(raw.opacity),
  };
  if (decision.exempt) return { ...sample, exempt: decision.exempt };
  if (decision.unverified) return { ...sample, unverified: decision.unverified };
  const c = raw.color;
  return { ...sample, foreground: { r: c.r, g: c.g, b: c.b, a: clamp01(c.a) }, pixels: pixels || [] };
}

export function contrastFinding(r) {
  const base = { rule: 'contrast', selector: r.selector, verdict: r.verdict };
  if (r.verdict === 'EXEMPT') return { ...base, reason: r.reason };
  if (r.verdict === 'UNVERIFIED') return { ...base, reason: r.reason };
  return {
    ...base,
    measured: { ratio: r.ratio, fontSizePx: r.fontSizePx, bold: r.bold, large: r.large, backgroundKind: r.backgroundKind, foreground: r.foreground, effectiveBackground: r.effectiveBackground },
    required: r.required,
    ...(r.notes && r.notes.length ? { note: r.notes.join(', ') } : {}),
  };
}

// Sample points inside the text rectangles ([x, y, w, h] in document pixels): a regular grid of at most `cap` points,
// flat [x0, y0, x1, y1, ...]; every rectangle gets at least its centre.
export function gridPoints(rects, cap = MAX_POINTS_PER_ELEMENT) {
  const list = (rects || []).filter((r) => r[2] > 0 && r[3] > 0);
  const area = list.reduce((s, r) => s + r[2] * r[3], 0);
  const step = Math.max(1, Math.ceil(Math.sqrt(area / Math.max(1, cap))));
  const out = [];
  for (const r of list) {
    const x1 = r[0] + r[2];
    const y1 = r[1] + r[3];
    let any = false;
    for (let y = Math.floor(r[1]); y < y1 && out.length < cap * 2; y += step) {
      for (let x = Math.floor(r[0]); x < x1 && out.length < cap * 2; x += step) {
        out.push(x, y);
        any = true;
      }
    }
    if (!any && out.length < cap * 2) out.push(Math.floor(r[0] + r[2] / 2), Math.floor(r[1] + r[3] / 2));
  }
  return out;
}

// Whole-page screenshot plan. Up to 16000 px high: one shot; above: clips of 4000 px; beyond the limits: not measurable.
export function planClips({ width, height }) {
  if (!(width > 0) || !(height > 0)) return { ok: false, reason: 'page-too-tall' };
  if (height > MAX_PAGE_PX || width > MAX_PAGE_WIDTH_PX || width * height > MAX_PAGE_PIXELS) return { ok: false, reason: 'page-too-tall' };
  const w = Math.ceil(width);
  const h = Math.ceil(height);
  if (h <= SINGLE_SHOT_MAX_PX) return { ok: true, chunked: false, clips: [{ x: 0, y: 0, width: w, height: h }] };
  const clips = [];
  for (let y = 0; y < h; y += CHUNK_PX) clips.push({ x: 0, y, width: w, height: Math.min(CHUNK_PX, h - y) });
  return { ok: true, chunked: true, clips };
}

// img: { width, height, data } decoded PNG whose top-left corner is the document point (clip.x, clip.y).
export function gatherPixels(img, clip, points, out) {
  for (let k = 0; k + 1 < points.length; k += 2) {
    const x = points[k] - clip.x;
    const y = points[k + 1] - clip.y;
    if (x < 0 || y < 0 || x >= img.width || y >= img.height) continue;
    const o = (y * img.width + x) * 4;
    out.push([img.data[o], img.data[o + 1], img.data[o + 2]]);
  }
}

// ----- targets -----

export function toTargetItem(raw) {
  return {
    selector: selectorOf(raw),
    box: raw.box,
    rendered: true,
    display: raw.display,
    parentHasText: raw.parentHasText === true,
    userAgent: raw.userAgent === true,
    labelBox: raw.labelBox || null,
    ...(raw.pair !== undefined && raw.pair !== null ? { pair: raw.pair } : {}),
  };
}

export function targetFinding(r) {
  const base = { rule: 'target-size', selector: r.selector, verdict: r.verdict, measured: { widthPx: r.widthPx, heightPx: r.heightPx } };
  if (r.verdict === 'EXEMPT') return { ...base, reason: r.reason };
  if (r.verdict === 'FAIL') return { ...base, required: r.required };
  return r.note ? { ...base, note: r.note } : base;
}

export function targetFindings(raws) {
  return evaluateTargets((raws || []).map(toTargetItem)).map(targetFinding);
}

export function toCandidate(raw) {
  return {
    key: raw.key, selector: selectorOf(raw), tabindex: raw.tabindex ?? null, disabled: raw.disabled === true, inert: raw.inert === true,
    inClosedDetails: raw.inClosedDetails === true, hidden: raw.focusable === false, inModal: raw.inModal === true, radioGroup: raw.radioGroup || null, radioChecked: raw.radioChecked === true,
  };
}

// ----- Tab sequence and focus -----

export function toTabStep(info) {
  if (info.kind === 'body' || info.kind === 'outside') return { key: info.kind, kind: info.kind, selector: info.kind };
  return { key: info.key, kind: 'element', selector: selectorOf(info), tabindex: info.tabindex ?? null, box: info.box || null, inDocument: info.inDocument !== false, inModal: info.inModal === true };
}

// The recording stops when the pure analysis finds an end (wrap, body, outside, trap, cycle) or the stop limit.
export function tabRecordingDone(raw, maxStops) {
  const s = sequenceEnd(raw, { maxStops });
  return s.end !== 'limit' || s.steps.length >= maxStops;
}

// Clip of the element rectangle (viewport pixels) grown by `pad`, clamped to the viewport; null when nothing is left.
export function focusClip(vr, viewport, pad = FOCUS_PAD_PX) {
  if (!vr) return null;
  const x0 = Math.max(0, Math.floor(vr.x - pad));
  const y0 = Math.max(0, Math.floor(vr.y - pad));
  const x1 = Math.min(viewport.width, Math.ceil(vr.x + vr.width + pad));
  const y1 = Math.min(viewport.height, Math.ceil(vr.y + vr.height + pad));
  return x1 > x0 && y1 > y0 ? { x: x0, y: y0, width: x1 - x0, height: y1 - y0 } : null;
}

// A pointer position in the viewport corner farthest from the clip.
export function parkPoint(clip, viewport) {
  return { x: clip.x + clip.width / 2 > viewport.width / 2 ? 1 : viewport.width - 1, y: clip.y + clip.height / 2 > viewport.height / 2 ? 1 : viewport.height - 1 };
}

// Number of different pixels between two decoded images of the same size; null when the sizes differ.
export function diffPixels(flagger, a, b) {
  if (a.width !== b.width || a.height !== b.height) return null;
  let n = 0;
  for (const f of flagger(a, b)) n += f ? 1 : 0;
  return n;
}

export function keyboardFinding(info, diffPx, minDiffPx) {
  const selector = selectorOf(info);
  if (diffPx === null) return { rule: 'focus-keyboard-indicator', verdict: 'UNVERIFIED', reason: 'clipped', selector, message: 'focus area could not be captured' };
  const r = classifyKeyboard({ diffPx, minDiffPx });
  return { ...r, selector, measured: { ...r.measured, focusVisible: info.focusVisible === true, signals: info.signals || {} } };
}

// ----- console, requests and origins -----

const MAX_MESSAGE_SOURCE = 2000;
export const stripQuery = (url) => String(url).split(/[?#]/)[0];
export const isFavicon = (url) => /^[a-z][a-z0-9+.-]*:\/\/[^/]*\/favicon\.ico$/i.test(stripQuery(url));

// A query string or fragment of a URL inside a message can carry a token: it is replaced.
export function redactUrlQueries(text) {
  return String(text).slice(0, MAX_MESSAGE_SOURCE).replace(/(\b(?:https?|file|wss?):\/\/[^\s?#'"]*)[?#][^\s'"]*/gi, '$1?<redacted>');
}

// A file: URL must stay inside the configuration directory also after resolving symlinks.
function fileInside(url, configDir) {
  if (!/^file:/i.test(String(url))) return true;
  try {
    assertRealInside(configDir, fileURLToPath(url), nodeFs);
    return true;
  } catch {
    return false;
  }
}

// Requests to other origins are aborted; data:, blob: and about: carry no network access.
export function routeDecision(url, { configDir, allowOrigins = [] } = {}) {
  if (/^(?:data|blob|about):/i.test(String(url))) return { allow: true };
  if (checkOrigin(url, { configDir, allow: allowOrigins }).ok && fileInside(url, configDir)) return { allow: true };
  let host = 'unknown';
  try {
    host = new URL(String(url)).hostname || String(url).split(':')[0];
  } catch { /* unparsable URL */ }
  return { allow: false, host };
}

export function createBlockedTracker() {
  let count = 0;
  let hosts = new Set();
  return {
    add(host) { count += 1; hosts.add(host); },
    reset() { count = 0; hosts = new Set(); },
    take(pageId) {
      if (count === 0) return null;
      const list = [...hosts].slice(0, 5).join(', ');
      return { code: 'blocked-origin', level: 'warning', message: `page ${pageId}: blocked ${count} request(s) to origins outside the allowed list (${list}${hosts.size > 5 ? ', ...' : ''})` };
    },
  };
}

// Collects console errors, page errors, failed requests and 4xx/5xx responses (allowed origins only: the others are aborted).
export function createConsoleCollector({ maxMessages = LIMITS.messages } = {}) {
  const seen = new Map();
  const list = [];
  let truncated = false;
  const add = (level, text) => {
    const message = redactUrlQueries(String(text).slice(0, MAX_MESSAGE_SOURCE));
    const k = `${level}|${message}`;
    if (seen.has(k)) { seen.get(k).count += 1; return; }
    if (list.length >= maxMessages) { truncated = true; return; }
    const entry = { level, message, count: 1 };
    seen.set(k, entry);
    list.push(entry);
  };
  const locationUrl = (msg) => {
    try { return msg.location().url || ''; } catch { return ''; }
  };
  return {
    onConsole(msg) {
      const type = msg.type();
      if (type !== 'error' && type !== 'warning') return;
      const text = String(msg.text());
      if (/^Failed to load resource: (?:the server responded with a status of|net::ERR_)/i.test(text) || isFavicon(locationUrl(msg))) return; // network failures are reported by the request events
      add(type === 'error' ? 'error' : 'warning', `console.${type}: ${text}`);
    },
    onPageError(err) { add('error', `pageerror: ${err && err.message ? err.message : String(err)}`); },
    onRequestFailed(req) {
      const f = req.failure();
      const reason = f && f.errorText ? f.errorText : 'failed';
      if (/ERR_BLOCKED_BY_CLIENT|ERR_ABORTED/.test(reason)) return; // our own abort or a navigation that replaced the request
      if (req.method() === 'GET' && isFavicon(req.url())) return;
      add('error', `requestfailed: ${req.method()} ${stripQuery(req.url())} (${reason})`);
    },
    onResponse(res) {
      const status = res.status();
      if (status < 400) return;
      const req = res.request();
      if (req.method() === 'GET' && isFavicon(res.url())) return;
      add('error', `http ${status}: ${req.method()} ${stripQuery(res.url())}`);
    },
    result() { return { list, truncated }; },
  };
}

export function consoleFindings({ list, truncated }) {
  const out = list.map((e) => ({
    rule: 'console', verdict: e.level === 'error' ? 'FAIL' : 'INFO', message: e.message, basis: 'best-practice', ...(e.count > 1 ? { measured: { count: e.count } } : {}),
  }));
  if (truncated) out.push({ rule: 'console', verdict: 'UNVERIFIED', reason: 'truncated', hashBasis: 'console-truncated', message: `more than ${LIMITS.messages} different messages; the rest was dropped` });
  else if (!list.some((e) => e.level === 'error')) out.push({ rule: 'console', verdict: 'PASS', hashBasis: 'console-ok', message: 'no console errors', basis: 'best-practice' });
  return out;
}

export function makeRouteHandler({ configDir, allowOrigins = [], blocked }) {
  return async (route) => {
    const d = routeDecision(route.request().url(), { configDir, allowOrigins });
    try {
      if (d.allow) await route.continue();
      else {
        blocked.add(d.host);
        await route.abort('blockedbyclient');
      }
    } catch { /* the page may already be closed */ }
  };
}

export function attachCollector(page, collector) {
  page.on('console', (m) => collector.onConsole(m));
  page.on('pageerror', (e) => collector.onPageError(e));
  page.on('requestfailed', (r) => collector.onRequestFailed(r));
  page.on('response', (r) => collector.onResponse(r));
  page.on('dialog', (d) => { Promise.resolve(d.dismiss()).catch(() => {}); });
}

// ---------------------------------------------------------------------------------------------------------------
// Part 3: orchestration (Playwright)
// ---------------------------------------------------------------------------------------------------------------

async function validateSelectors(page, spec, config) {
  const list = [];
  if (spec.assert) list.push(['assert', spec.assert.selector]);
  if (spec.waitFor) list.push(['waitFor', spec.waitFor]);
  if (config.focus.mouse && spec.checks.includes('focus')) config.focus.mouse.selectors.forEach((s, i) => list.push([`focus.mouse.selectors[${i}]`, s]));
  if (list.length === 0) return;
  const ok = await callPage(page, { op: 'validate', selectors: list.map((x) => x[1]) });
  const bad = list.findIndex((_, i) => !Array.isArray(ok) || ok[i] !== true);
  if (bad >= 0) throw new A11yPageError(`page "${spec.id}": invalid selector in ${list[bad][0]}`);
}

async function runAssertion(page, spec) {
  if (!spec.assert) return 'NONE';
  try {
    await page.waitForFunction(pageScript, { op: 'assertOk', selector: spec.assert.selector, text: spec.assert.text }, { timeout: ACTION_TIMEOUT_MS });
    return 'PASS';
  } catch {
    return 'FAIL';
  }
}

async function phaseContrast(env, raw, warnings) {
  const items = raw.items;
  const decisions = items.map(classifyContrastRaw);
  const todo = items.map((_, i) => i).filter((i) => !decisions[i].exempt && !decisions[i].unverified);
  const pixels = new Map(todo.map((i) => [i, []]));
  const findings = [];
  let tooTall = false;
  if (todo.length > 0) {
    const plan = planClips(env.docSize);
    tooTall = !plan.ok;
    if (plan.ok) await sampleClips(env, plan, items, todo, pixels);
  }
  let blocked = tooTall ? 'page-too-tall' : null;
  if (!blocked && todo.length > 0) {
    const after = await callPage(env.page, { op: 'recheck' });
    if (after.height !== env.docSize.height || after.shifted > 0) blocked = 'layout-shift';
  }
  items.forEach((it, i) => {
    if (blocked && todo.includes(i)) return;
    const f = contrastFinding(measureSample(toContrastSample(it, decisions[i], pixels.get(i)), env.config.thresholds));
    if (it.rectsCut && (f.verdict === 'PASS' || f.verdict === 'FAIL')) f.note = [f.note, 'sampled'].filter(Boolean).join(', ');
    findings.push(f);
  });
  if (blocked === 'page-too-tall') {
    findings.push({ rule: 'contrast', verdict: 'UNVERIFIED', reason: 'page-too-tall', hashBasis: 'page-too-tall', message: 'page is too large to capture; contrast not measured' });
    warnings.push({ code: 'page-too-tall', level: 'warning', message: `page ${env.spec.id}: too large for the contrast screenshot (limits ${MAX_PAGE_PX} px height, ${MAX_PAGE_WIDTH_PX} px width, ${MAX_PAGE_PIXELS} pixels)` });
  } else if (blocked === 'layout-shift') {
    findings.push({ rule: 'contrast', verdict: 'UNVERIFIED', reason: 'layout-shift', hashBasis: 'layout-shift', message: 'the page layout changed while it was measured; contrast not trusted' });
    warnings.push({ code: 'layout-shift', level: 'warning', message: `page ${env.spec.id}: document height or text rectangles changed between measuring and the screenshot` });
  }
  if (raw.truncated) {
    findings.push({ rule: 'contrast', verdict: 'UNVERIFIED', reason: 'truncated', hashBasis: 'contrast-truncated', message: `more than ${LIMITS.contrastElements} text elements; the rest was not measured` });
    warnings.push({ code: 'contrast-truncated', level: 'warning', message: `page ${env.spec.id}: only the first ${LIMITS.contrastElements} text elements were measured` });
  } else if (items.length === 0) {
    findings.push({ rule: 'contrast', verdict: 'UNVERIFIED', reason: 'no-samples', hashBasis: 'no-text', message: 'no text elements found' });
  }
  return findings;
}

async function sampleClips(env, plan, items, todo, pixels) {
  const points = new Map(todo.map((i) => [i, gridPoints(items[i].rects)]));
  for (const clip of plan.clips) {
    const opts = { type: 'png', fullPage: true, style: HIDE_TEXT_CSS, animations: 'disabled', scale: 'css', timeout: SHOT_TIMEOUT_MS };
    if (plan.chunked) opts.clip = clip;
    const img = decodePng(env.imaging.PNG, await env.page.screenshot(opts));
    for (const [i, list] of points) gatherPixels(img, clip, list, pixels.get(i));
  }
}

function phaseTargets(env, raw, warnings) {
  const findings = targetFindings(raw.items);
  if (raw.truncated) {
    findings.push({ rule: 'target-size', verdict: 'UNVERIFIED', reason: 'truncated', hashBasis: 'targets-truncated', message: `more than ${LIMITS.targets} targets; the rest was not measured` });
    warnings.push({ code: 'targets-truncated', level: 'warning', message: `page ${env.spec.id}: only the first ${LIMITS.targets} targets were measured` });
  } else if (findings.length === 0) {
    findings.push({ rule: 'target-size', verdict: 'PASS', hashBasis: 'no-targets', message: 'no interactive targets found' });
  }
  return findings;
}

// Screenshot of the element area before (after blur of the same element) and after focus, in the same scroll position.
async function measureFocusStep(env, info) {
  const clip = focusClip(info.vr, env.config.viewport);
  if (!clip) return null;
  const shot = async () => decodePng(env.imaging.PNG, await env.page.screenshot({ type: 'png', clip, caret: 'hide', animations: 'disabled', scale: 'css' }));
  const after = await shot();
  await callPage(env.page, { op: 'blur' });
  const before = await shot();
  await callPage(env.page, { op: 'refocus' });
  return diffPixels(env.flagger, before, after);
}

async function recordTabs(env, measureFocus) {
  const maxStops = env.config.tab.maxStops;
  const raw = [];
  const focus = [];
  const measured = new Set();
  await callPage(env.page, { op: 'resetFocus' });
  for (let i = 0; i < maxStops * 3; i++) {
    await env.page.keyboard.press('Tab');
    const info = await callPage(env.page, { op: 'active' });
    raw.push(toTabStep(info));
    if (measureFocus && info.kind === 'element' && !measured.has(info.key)) {
      measured.add(info.key);
      focus.push(keyboardFinding(info, await measureFocusStep(env, info), env.config.focus.minDiffPx));
    }
    if (tabRecordingDone(raw, maxStops)) break;
  }
  return { raw, focus };
}

async function phaseKeyboard(env, targets, dir, want, warnings) {
  const { raw, focus } = await recordTabs(env, want.focus);
  const findings = [...focus];
  const result = { findings, sequence: undefined };
  if (want.focus && focus.length === 0) findings.push({ rule: 'focus-keyboard-indicator', verdict: 'PASS', hashBasis: 'no-focusable', message: 'no focusable elements reached by Tab' });
  if (want.tabOrder) {
    const a = analyzeTab({ raw, candidates: targets.map(toCandidate), options: { maxStops: env.config.tab.maxStops, positiveTabindex: env.config.tab.positiveTabindex, order: env.config.order, dir } });
    findings.push(...a.findings);
    warnings.push(...a.warnings.map((w) => ({ ...w, message: `page ${env.spec.id}: ${w.message}` })));
    warnings.push({ code: 'tab-end', level: 'info', message: `page ${env.spec.id}: Tab sequence ended by ${a.end} after ${a.sequence.length} stop(s)` });
    if (!a.findings.some((f) => f.rule === 'tab-trap' || f.rule === 'tab-unreachable')) findings.push({ rule: 'tab-unreachable', verdict: 'PASS', hashBasis: 'tab-ok', message: `all expected targets reached by Tab (${a.sequence.length} stop(s))`, basis: 'best-practice' });
    result.sequence = a.sequence;
  }
  return result;
}

// Mouse focus (opt-in, explicit selectors): scroll to the element, screenshot, click, screenshot; the page is reloaded after each click.
async function phaseMouse(env, warnings) {
  const { mode, selectors } = env.config.focus.mouse;
  const findings = [];
  for (const selector of selectors) {
    await gotoPage(env.page, env.spec);
    const info = await callPage(env.page, { op: 'reveal', selector });
    if (!info || info.found !== true) {
      findings.push({ rule: 'focus-mouse-ring', verdict: 'UNVERIFIED', reason: 'no-samples', hashBasis: `mouse-missing:${selector}`, message: 'configured mouse selector matched no element' });
      continue;
    }
    if (info.covered === true) {
      warnings.push({ code: 'mouse-occluded', level: 'warning', message: `page ${env.spec.id}: a configured mouse target is covered by another element and was not clicked` });
      findings.push({ rule: 'focus-mouse-ring', verdict: 'UNVERIFIED', reason: 'occluded', selector: selectorOf(info), message: 'mouse target covered by another element; not clicked' });
      continue;
    }
    const clip = focusClip(info.vr, env.config.viewport);
    const urlBefore = env.page.url();
    let diffPx = null;
    if (clip) {
      const shot = async () => decodePng(env.imaging.PNG, await env.page.screenshot({ type: 'png', clip, caret: 'hide', animations: 'disabled', scale: 'css' }));
      const before = await shot();
      await env.page.mouse.click(info.vr.x + info.vr.width / 2, info.vr.y + info.vr.height / 2);
      for (const p of env.context.pages()) if (p !== env.page) await p.close();
      const park = parkPoint(clip, env.config.viewport);
      await env.page.mouse.move(park.x, park.y); // move the pointer away so :hover does not distort the "after" shot
      if (env.page.url() === urlBefore) diffPx = diffPixels(env.flagger, before, await shot());
    }
    const sel = selectorOf(info);
    if (diffPx === null) findings.push({ rule: 'focus-mouse-ring', verdict: 'UNVERIFIED', reason: 'clipped', selector: sel, message: 'mouse focus ring could not be measured (no visible area or the click navigated)' });
    else findings.push({ ...classifyMouse({ mode, diffPx, minDiffPx: env.config.focus.minDiffPx }), selector: sel });
  }
  return findings;
}

async function gotoPage(page, spec) {
  try {
    await page.goto(spec.url, { waitUntil: 'load', timeout: NAV_TIMEOUT_MS });
  } catch {
    throw new A11yPageError(`page "${spec.id}": navigation failed or timed out`);
  }
  if (spec.waitFor) {
    try {
      await page.waitForFunction(pageScript, { op: 'visible', selector: spec.waitFor }, { timeout: ACTION_TIMEOUT_MS });
    } catch {
      throw new A11yPageError(`page "${spec.id}": waitFor element not visible in time`);
    }
  }
}

async function runPage(env) {
  const { page, spec, config } = env;
  const has = (c) => spec.checks.includes(c);
  const warnings = [];
  await gotoPage(page, spec);
  await validateSelectors(page, spec, config);
  const result = { id: spec.id, url: displayUrl(spec.url, env.configDir), assertion: await runAssertion(page, spec), checks: spec.checks, findings: [], warnings };
  if (result.assertion === 'FAIL') return result;
  const want = { contrast: has('contrast'), targets: has('target-size') || has('tab-order') };
  if (want.contrast || want.targets) env.docSize = await callPage(page, { op: 'settle' });
  const raw = want.contrast || want.targets ? await callPage(page, { op: 'collect', want, limits: { contrast: LIMITS.contrastElements, targets: LIMITS.targets } }) : {};
  if (raw.frames > 0) warnings.push({ code: 'frames-not-audited', level: 'warning', message: `page ${spec.id}: ${raw.frames} frame(s): their content is not measured for contrast and target size (Tab does go through same-origin frames)` });
  if (raw.closedShadowGuess > 0) warnings.push({ code: 'shadow-not-audited', level: 'warning', message: `page ${spec.id}: ${raw.closedShadowGuess} custom element(s) without readable content (closed shadow DOM is not audited; ${raw.openShadowRoots || 0} open shadow root(s) were)` });
  if (want.contrast) result.findings.push(...await phaseContrast(env, raw.contrast, warnings));
  if (has('target-size')) result.findings.push(...phaseTargets(env, raw.targets, warnings));
  if (has('focus') || has('tab-order')) {
    const k = await phaseKeyboard(env, raw.targets ? raw.targets.items : [], raw.dir, { focus: has('focus'), tabOrder: has('tab-order') }, warnings);
    result.findings.push(...k.findings);
    if (k.sequence) result.sequence = k.sequence;
  }
  if (has('focus') && config.focus.mouse) result.findings.push(...await phaseMouse(env, warnings));
  if (has('console')) result.findings.push(...consoleFindings(env.collector.result()));
  const blocked = env.blocked.take(spec.id);
  if (blocked) warnings.push(blocked);
  return result;
}

// One browser context per run (login once, cookies shared by the pages); every request is checked against the origin policy.
export function createA11yRunner({ browser, imaging, config, configDir, allowOrigins = [], env = {} }) {
  const blocked = createBlockedTracker();
  const flagger = makePixelmatchFlagger(imaging.pixelmatch, { threshold: PIXEL_THRESHOLD });
  let context = null;
  const gate = (url) => {
    const v = checkOrigin(url, { configDir, allow: allowOrigins });
    if (!v.ok || !fileInside(url, configDir)) throw new A11yPageError(`origin not allowed: ${v.ok ? 'file URL outside config directory' : v.reason}`);
  };
  const ensureContext = async () => {
    if (context) return context;
    const { width, height } = config.viewport;
    context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1, reducedMotion: 'reduce', colorScheme: 'light', serviceWorkers: 'block', acceptDownloads: false });
    context.setDefaultTimeout(ACTION_TIMEOUT_MS);
    await context.route(/.*/, makeRouteHandler({ configDir, allowOrigins, blocked }));
    return context;
  };
  return {
    async login() {
      if (!config.login) return;
      gate(config.login.url);
      const page = await (await ensureContext()).newPage();
      try {
        try {
          await page.goto(config.login.url, { waitUntil: 'load', timeout: NAV_TIMEOUT_MS });
        } catch {
          throw new A11yPageError('login page: navigation failed or timed out');
        }
        await runLoginSteps({ page, login: config.login, env, actionTimeoutMs: ACTION_TIMEOUT_MS, loadTimeoutMs: ACTION_TIMEOUT_MS });
      } finally {
        await page.close();
      }
    },
    async auditPage(spec) {
      gate(spec.url);
      const ctx = await ensureContext();
      const page = await ctx.newPage();
      const collector = createConsoleCollector();
      blocked.reset();
      attachCollector(page, collector);
      try {
        return await runPage({ page, context: ctx, spec, config, configDir, imaging, flagger, collector, blocked });
      } finally {
        await page.close();
      }
    },
    async close() {
      if (!context) return;
      const c = context;
      context = null;
      await c.close();
    },
  };
}
