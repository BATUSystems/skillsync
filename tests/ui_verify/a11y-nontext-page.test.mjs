// Check "non-text" in the browser adapter (CORE-1150-T2B, B3; spec B2, B4, B5, B7, P5, P7, P9, P18; criteria K1, K5, K6,
// K9): the contract of the page operation on a fake DOM and the runner phase with a scripted page and synthetic images.
// No browser, no clock, no network.
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { assembleReport } from '../../library/skills/ui-verify/scripts/a11y.mjs';
import { validateA11yConfig } from '../../library/skills/ui-verify/scripts/lib/a11y-config.mjs';
import { classifyCandidate } from '../../library/skills/ui-verify/scripts/lib/a11y-nontext.mjs';
import { PAGE_SCRIPT, createA11yRunner } from '../../library/skills/ui-verify/scripts/lib/a11y-page.mjs';

const CONFIG_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-a11y-nontext-page-'));
after(() => fs.rmSync(CONFIG_DIR, { recursive: true, force: true }));

// ---------- a small fake DOM ----------

const BASE_STYLE = {
  display: 'block', visibility: 'visible', opacity: '1', filter: 'none', mixBlendMode: 'normal', backgroundImage: 'none', boxShadow: 'none',
  backgroundColor: 'rgba(0, 0, 0, 0)', color: 'rgb(0, 0, 0)', overflowX: 'visible', overflowY: 'visible', clip: 'auto', clipPath: 'none',
  position: 'static', transform: 'none', pointerEvents: 'auto', appearance: 'auto', content: 'normal', fill: 'rgb(0, 0, 0)', stroke: 'none', strokeWidth: '1px',
  ...Object.fromEntries(['Top', 'Right', 'Bottom', 'Left'].flatMap((s) => [[`border${s}Width`, '0px'], [`border${s}Style`, 'none']])),
};
const border = (px, style = 'solid') => Object.fromEntries(['Top', 'Right', 'Bottom', 'Left'].flatMap((s) => [[`border${s}Width`, `${px}px`], [`border${s}Style`, style]]));

const text = (data) => ({ nodeType: 3, data });

// h(tag, { attrs, style, pseudo, rect: [x, y, w, h], rects, type, disabled }, ...children)
function h(tag, over = {}, ...children) {
  const { attrs = {}, style = {}, pseudo = {}, rect = [10, 10, 40, 20], rects = 1, type, disabled = false } = over;
  const el = {
    nodeType: 1, tagName: tag === 'svg' || tag === 'path' || tag === 'use' || tag === 'image' ? tag : tag.toUpperCase(),
    attrs: { ...attrs }, style_: { ...BASE_STYLE, ...style }, pseudo, rect, childNodes: children, parentElement: null, disabled,
    id: attrs.id || '', shadowRoot: null, writes: [],
    ...(type !== undefined ? { type } : {}),
    getAttribute(n) { return Object.hasOwn(this.attrs, n) ? this.attrs[n] : null; },
    hasAttribute(n) { return Object.hasOwn(this.attrs, n); },
    setAttribute(n) { this.writes.push(n); },
    removeAttribute(n) { this.writes.push(n); },
    matches(sel) { return sel === ':disabled' && this.disabled; },
    closest(sel) {
      for (let n = this; n; n = n.parentElement) {
        if (sel === '[aria-disabled="true"]' && n.getAttribute('aria-disabled') === 'true') return n;
        if (sel === 'details:not([open])' && n.tagName === 'DETAILS' && !n.hasAttribute('open')) return n;
      }
      return null;
    },
    contains(o) { for (let n = o; n; n = n.parentElement) if (n === this) return true; return false; },
    getBoundingClientRect() { const [x, y, w, hh] = this.rect; return { left: x, top: y, width: w, height: hh, right: x + w, bottom: y + hh }; },
    getClientRects() { return Array.from({ length: rects }, () => this.getBoundingClientRect()); },
    getRootNode() { return this.doc; },
    get children() { return this.childNodes.filter((c) => c.nodeType === 1); },
    get nextElementSibling() { const s = this.parentElement ? this.parentElement.children : []; return s[s.indexOf(this) + 1] || null; },
    get previousElementSibling() { const s = this.parentElement ? this.parentElement.children : []; return s[s.indexOf(this) - 1] || null; },
  };
  el.style = new Proxy({}, { get: (_, k) => (k === 'setProperty' || k === 'removeProperty' ? () => el.writes.push('style') : undefined), set: () => { el.writes.push('style'); return true; } });
  for (const c of children) c.parentElement = el;
  return el;
}

function fakeCanvas() {
  let fill = '#000000';
  return { getContext: () => ({
    get fillStyle() { return fill; },
    set fillStyle(v) {
      if (/^#[0-9a-f]{6}$/i.test(v)) { fill = v; return; }
      const m = /^rgba?\((\d+), (\d+), (\d+)(?:, ([0-9.]+))?\)$/.exec(v);
      if (!m) return;
      fill = m[4] !== undefined && Number(m[4]) < 1 ? `rgba(${m[1]}, ${m[2]}, ${m[3]}, ${m[4]})` : `#${[m[1], m[2], m[3]].map((n) => Number(n).toString(16).padStart(2, '0')).join('')}`;
    },
  }) };
}

// hit(x, y, all) overrides the topmost element; by default the deepest, last element whose box holds the point.
function runOp(children, { args = { op: 'nonText', limit: 2000 }, hit = null, stack = null, ua = [13, 13], height = 300 } = {}) {
  const body = h('body', { rect: [0, 0, 400, 300] }, ...children);
  const all = [];
  const walk = (e) => { all.push(e); for (const c of e.children) walk(c); };
  walk(body);
  const topmost = (x, y) => [...all].reverse().find((e) => { const r = e.getBoundingClientRect(); return e.style_.pointerEvents !== 'none' && x >= r.left && x < r.right && y >= r.top && y < r.bottom; }) || body;
  const scrolls = [];
  const added = [];
  const document = {
    body, documentElement: { scrollWidth: 400, scrollHeight: height },
    querySelectorAll: (sel) => (sel === '[id]' ? all.filter((e) => e.id) : all),
    createElement: (tag) => {
      if (tag === 'canvas') return fakeCanvas();
      if (tag === 'input') return { type: '', getBoundingClientRect: () => ({ width: ua[0], height: ua[1] }) };
      const host = { style: {}, parentNode: null, shadow: null, attachShadow() { return { appendChild: (c) => { this.child = c; } }; } };
      return host;
    },
    createRange: () => { let node; return { selectNodeContents(n) { node = n; }, getClientRects: () => [node.parentElement.getBoundingClientRect()] }; },
    elementFromPoint: (x, y) => (hit ? hit(x, y, all, topmost) : topmost(x, y)),
    elementsFromPoint: (x, y) => (stack ? stack(x, y, all) : [topmost(x, y)]),
  };
  Object.assign(body, { scrollWidth: 400, scrollHeight: 300 });
  body.appendChild = (n) => { n.parentNode = { removeChild: () => { added.splice(added.indexOf(n), 1); n.parentNode = null; } }; added.push(n); };
  for (const e of all) { e.doc = document; e.ownerDocument = { defaultView: null }; }
  const sandbox = {
    document, scrollX: 0, scrollY: 0, innerWidth: 400, innerHeight: 300,
    scrollTo: ({ left, top }) => scrolls.push([left, top]),
    getComputedStyle: (e, p) => (p ? { ...BASE_STYLE, content: 'none', ...(e.pseudo[p] || {}) } : e.style_),
  };
  const fn = vm.runInNewContext(`(${PAGE_SCRIPT})`, sandbox);
  const call = (a) => fn(a).then((v) => JSON.parse(JSON.stringify(v)));
  return call(args).then((out) => ({ out, all, scrolls, added, call }));
}

const asCandidate = (item) => ({ ...item, selector: item.id || 'x' });
const collect = async (children, options) => (await runOp(children, options)).out.items;
const one = async (children, id, options) => {
  const item = (await collect(children, options)).find((i) => i.id === id);
  assert.ok(item, `no candidate ${id}`);
  return item;
};
const decided = (item) => classifyCandidate(asCandidate(item));
const reasons = (item) => decided(item).findings.map((f) => f.reason);

// ---------- page operation: shape ----------

test('operacja: pole formularza w ksztalcie rekordu, mierzalne bez zadnych decyzji', async () => {
  const item = await one([h('input', { attrs: { id: 'name' }, type: 'text', style: border(2) })], 'name');
  assert.deepEqual([item.tag, item.type, item.role, item.hidden, item.opacity, item.inactive, item.ariaHidden, item.interactive], ['input', 'text', '', false, 1, false, false, true]);
  assert.deepEqual(item.states, { selected: null, pressed: null, checked: null, expanded: null, current: null });
  assert.deepEqual([item.alt, item.named, item.visibleText, item.graphicContent, item.control, item.userAgent, item.pseudoControl, item.graphic], [null, false, false, false, null, false, false, null]);
  assert.deepEqual(item.box, { x: 10, y: 10, width: 40, height: 20 });
  assert.deepEqual(item.borders.top, { width: 2, style: 'solid' });
  assert.deepEqual(item.flags, { filter: false, group: false, media: false, hit: null });
  assert.deepEqual(decided(item).measure, ['boundary']);
});

test('operacja: zbiera tylko komponenty, grafiki, elementy ze stanem oraz przyciski i linki', async () => {
  const items = await collect([
    h('div', { attrs: { id: 'plain' } }), h('input', { attrs: { id: 'secret' }, type: 'hidden' }), h('div', { attrs: { id: 'region', role: 'region' } }),
    h('div', { attrs: { id: 'sw', role: 'switch' } }), h('div', { attrs: { id: 'sel', 'aria-selected': 'false' } }), h('a', { attrs: { id: 'link', href: '#' } }),
    h('a', { attrs: { id: 'anchor' } }), h('button', { attrs: { id: 'go' } }), h('canvas', { attrs: { id: 'cv' } }),
    h('svg', { attrs: { id: 'outer' } }, h('svg', { attrs: { id: 'inner' } })),
  ]);
  assert.deepEqual(items.map((i) => i.id).sort(), ['cv', 'go', 'link', 'outer', 'sel', 'sw']);
});

test('operacja: limit i truncated', async () => {
  const buttons = ['a', 'b', 'c'].map((id) => h('button', { attrs: { id } }));
  const { out } = await runOp(buttons, { args: { op: 'nonText', limit: 2 } });
  assert.deepEqual([out.truncated, out.items.length], [true, 2]);
  assert.equal((await runOp([h('button')])).out.truncated, false);
});

test('K9: tresc pol, teksty i nazwy nie opuszczaja strony', async () => {
  const secret = ['fake', 'value', 'qqq'].join('-');
  const field = h('input', { attrs: { id: 'f', value: secret, 'aria-label': secret, placeholder: secret }, type: 'text' });
  Object.defineProperty(field, 'value', { get() { throw new Error('value must not be read'); } });
  const { out } = await runOp([field, h('img', { attrs: { id: 'i', alt: secret } }), h('button', { attrs: { id: 'b' } }, text(secret))]);
  assert.ok(!JSON.stringify(out).includes(secret));
  assert.equal(out.items.find((i) => i.id === 'f').named, true);
});

test('K6: bez zmian stanu strony; przewijanie wraca na poczatek, tymczasowy element uaSize usuniety', async () => {
  const box = h('input', { attrs: { id: 'c' }, type: 'checkbox', rect: [10, 10, 13, 13], style: { pointerEvents: 'none' } });
  const { all, scrolls, added } = await runOp([box, h('button', { attrs: { id: 'far' }, rect: [10, 900, 40, 20] })]);
  assert.deepEqual(all.flatMap((e) => e.writes), []);
  assert.deepEqual(scrolls.at(-1), [0, 0]);
  assert.deepEqual(added, []);
});

test('P5/P18: userAgent liczony samodzielnie jak dla celow', async () => {
  const mk = (over) => h('input', { attrs: { id: 'c' }, type: 'checkbox', rect: [10, 10, 13, 13], ...over });
  assert.equal((await one([mk()], 'c')).userAgent, true);
  assert.equal((await one([mk({ rect: [10, 10, 20, 20] })], 'c')).userAgent, false);
  assert.equal((await one([mk({ style: { appearance: 'none' } })], 'c')).userAgent, false);
});

// ---------- page operation: rendering, state, accessibility data ----------

test('operacja: hidden, opacity, aria-hidden, inactive (takze kontrolki grafiki)', async () => {
  assert.equal((await one([h('button', { attrs: { id: 'a' }, style: { display: 'none' }, rect: [0, 0, 0, 0] })], 'a')).hidden, true);
  assert.equal((await one([h('button', { attrs: { id: 'a' }, style: { visibility: 'hidden' } })], 'a')).hidden, true);
  const faded = await one([h('div', { style: { opacity: '0.5' } }, h('button', { attrs: { id: 'a' } }))], 'a');
  assert.equal(faded.opacity, 0.5);
  assert.equal((await one([h('div', { attrs: { 'aria-hidden': 'true' } }, h('svg', { attrs: { id: 's' } }))], 's')).ariaHidden, true);
  const svg = await one([h('button', { attrs: { id: 'b' }, disabled: true }, h('svg', { attrs: { id: 's' } }))], 's');
  assert.equal(svg.inactive, true);
  assert.deepEqual(reasons(svg), ['inactive']);
});

test('operacja: tekst widoczny, a tekst wizualnie ukryty nie liczy sie', async () => {
  const withText = (inner) => one([h('button', { attrs: { id: 'b' } }, inner)], 'b');
  assert.equal((await withText(text('Save'))).visibleText, true);
  for (const style of [{ display: 'none' }, { visibility: 'hidden' }, { opacity: '0' }, { color: 'rgba(0, 0, 0, 0)' }, { clipPath: 'inset(50%)' }]) {
    assert.equal((await withText(h('span', { style }, text('Save')))).visibleText, false, JSON.stringify(style));
  }
  assert.equal((await withText(h('span', { style: { overflowX: 'hidden', overflowY: 'hidden' }, rect: [10, 10, 1, 1] }, text('Save')))).visibleText, false);
  assert.equal((await withText(h('span', { rect: [-999, 10, 40, 20] }, text('Save')))).visibleText, false);
  assert.equal((await withText(text(String.fromCharCode(0xe001)))).visibleText, false); // an icon font glyph
});

test('operacja: ikona jako jedyna zawartosc kontrolki, obok tekstu i obok drugiej grafiki', async () => {
  const icon = () => h('svg', { attrs: { id: 's' }, rect: [14, 12, 16, 16] }, h('path'));
  const sole = await collect([h('button', { attrs: { id: 'b' } }, icon())]);
  const s = sole.find((i) => i.id === 's');
  assert.deepEqual(s.control, { hasVisibleText: false, soleContent: true });
  assert.deepEqual(s.graphic, { raster: false, gradient: false, filter: false, fills: 1 });
  assert.deepEqual(s.flags, { filter: false, group: false, media: false, hit: null }, 'the svg itself is not media under it');
  assert.deepEqual(decided(s).measure, ['icon']);
  assert.equal(sole.find((i) => i.id === 'b').graphicContent, true);
  assert.deepEqual((await one([h('button', {}, icon(), text('Save'))], 's')).control, { hasVisibleText: true, soleContent: false });
  assert.deepEqual((await one([h('button', {}, icon(), h('img'))], 's')).control, { hasVisibleText: false, soleContent: false });
  const pseudo = h('button', { pseudo: { '::after': { content: '"x"' } } }, icon());
  assert.equal((await one([pseudo], 's')).control.soleContent, false);
  // a hidden graphic does not stand for its button: the button stays unverified
  const hidden = await one([h('button', { attrs: { id: 'b' } }, h('svg', { style: { display: 'none' }, rect: [0, 0, 0, 0] }))], 'b');
  assert.equal(hidden.graphicContent, false);
  assert.deepEqual(reasons(hidden), ['shape-unknown']);
});

test('operacja: kontener z tabindex=-1 nie jest kontrolka grafiki', async () => {
  const svg = await one([h('main', { attrs: { tabindex: '-1' } }, h('svg', { attrs: { id: 's', role: 'img', 'aria-label': 'Chart' } }, h('path')), text('Report'))], 's');
  assert.deepEqual([svg.control, svg.named], [null, true]);
});

test('operacja: dane grafiki: gradient, filtr, raster, wiele kolorow, zewnetrzny use', async () => {
  const graphic = async (...children) => (await one([h('button', {}, h('svg', { attrs: { id: 's' } }, ...children))], 's')).graphic;
  assert.equal((await graphic(h('path', { style: { fill: 'url("#g")' } }))).gradient, true);
  assert.equal((await graphic(h('path', { style: { filter: 'blur(2px)' } }))).filter, true);
  assert.equal((await graphic(h('image'))).raster, true);
  const seven = Array.from({ length: 7 }, (_, i) => h('path', { style: { fill: `rgb(${i}, 0, 0)` } }));
  assert.equal((await graphic(...seven)).fills, 7);
  assert.equal((await graphic(h('use', { attrs: { href: 'sprite.svg#a' } }))).gradient, true);
});

test('operacja: pseudoControl wykrywany niezaleznie od testu hidden (input przyciety do 1 px)', async () => {
  const input = () => h('input', { attrs: { id: 'c' }, type: 'checkbox', rect: [10, 10, 1, 1], style: { overflowX: 'hidden', overflowY: 'hidden' } });
  const box = { '::before': { content: '""', ...border(2) } };
  const item = await one([h('div', {}, input(), h('label', { pseudo: box, rect: [10, 10, 60, 20] }, text('Agree')))], 'c');
  assert.deepEqual([item.hidden, item.pseudoControl], [true, true]);
  assert.deepEqual(reasons(item), ['pseudo-element-control']);
  const whole = await one([h('div', { style: { display: 'none' } }, input(), h('label', { pseudo: box }, text('Agree')))], 'c');
  assert.equal(whole.pseudoControl, false);
  const none = await one([h('div', {}, input(), h('label', {}, text('Agree')))], 'c');
  assert.equal(none.pseudoControl, false);
});

test('operacja: tresc textarea nie jest czytana; link z tabindex=-1 jest kontrolka swojej ikony', async () => {
  const content = { nodeType: 3, get data() { throw new Error('field content must not be read'); } };
  assert.equal((await one([h('textarea', { attrs: { id: 't' } }, content)], 't')).visibleText, false);
  const svg = await one([h('a', { attrs: { tabindex: '-1' } }, h('svg', { attrs: { id: 's' } }, h('path')))], 's');
  assert.deepEqual(svg.control, { hasVisibleText: false, soleContent: true });
});

test('operacja: ukryty definicja symbolu w use nadal liczy sie do gradientu; starsze clip ukrywa tekst', async () => {
  const symbol = h('symbol', { attrs: { id: 'sym' }, style: { display: 'none' }, rect: [0, 0, 0, 0] }, h('path', { style: { fill: 'url("#g")' } }));
  const { call, all } = await runOp([h('button', {}, h('svg', { attrs: { id: 's' } }, h('use', { attrs: { href: '#sym' } }))), symbol]);
  for (const e of all) e.getRootNode = () => ({ getElementById: (id) => all.find((x) => x.id === id) || null });
  const items = (await call({ op: 'nonText', limit: 10 })).items;
  assert.equal(items.find((i) => i.id === 's' || i.tag === 'svg').graphic.gradient, true);
  const clipped = await one([h('button', { attrs: { id: 'b' } }, h('span', { style: { clip: 'rect(0px, 0px, 0px, 0px)' } }, text('Save')))], 'b');
  assert.equal(clipped.visibleText, false);
});

// ---------- page operation: hit test ----------

test('hit-test: obcy element nad komponentem to occluded, przodek w srodku tez', async () => {
  const cover = h('div', { attrs: { id: 'cover' }, rect: [0, 0, 100, 100] });
  assert.equal((await one([h('input', { attrs: { id: 'f' }, type: 'text' }), cover], 'f')).flags.hit, 'occluded');
  const ancestor = await one([h('div', {}, h('input', { attrs: { id: 'f' }, type: 'text' }))], 'f', { hit: (x, y, all) => all[1] });
  assert.equal(ancestor.flags.hit, 'occluded');
});

test('hit-test: pointer-events none nie jest zaslonieciem i nie zmienia stylu', async () => {
  const svg = await one([h('button', {}, h('svg', { attrs: { id: 's' }, style: { pointerEvents: 'none' } }, h('path', { style: { pointerEvents: 'none' } })))], 's');
  assert.equal(svg.flags.hit, null);
});

test('hit-test: przyciecie przez przodka z overflow lub clip-path to clipped; media pod elementem', async () => {
  for (const style of [{ overflowX: 'hidden', overflowY: 'hidden' }, { clipPath: 'circle(10px)' }]) {
    const item = await one([h('div', { style, rect: [10, 10, 20, 10] }, h('input', { attrs: { id: 'f' }, type: 'text' }))], 'f');
    assert.equal(item.flags.hit, 'clipped', JSON.stringify(style));
  }
  const media = await one([h('video', { rect: [0, 0, 100, 100] }), h('input', { attrs: { id: 'f' }, type: 'text' })], 'f', { stack: (x, y, all) => [all[2], all[1]] });
  assert.equal(media.flags.media, true);
});

test('operacja: element wieloliniowy, obrocony albo przyklejony nie daje pomiaru', async () => {
  const state = { 'aria-current': 'page', href: '#', id: 'a' };
  const wrapped = await one([h('a', { attrs: state, rects: 2 }, text('x'))], 'a');
  assert.deepEqual(reasons(wrapped), ['shape-unknown']);
  const rotated = await one([h('div', { style: { transform: 'matrix(0.7, 0.7, -0.7, 0.7, 0, 0)' } }, h('input', { attrs: { id: 'f' }, type: 'text' }))], 'f');
  assert.deepEqual(reasons(rotated), ['shape-unknown']);
  const pinned = () => [h('div', { style: { position: 'fixed' } }, h('input', { attrs: { id: 'f' }, type: 'text' }))];
  assert.equal((await one(pinned(), 'f')).flags.hit, null, 'a page that fits the viewport');
  assert.equal((await one(pinned(), 'f', { height: 900 })).flags.hit, 'clipped', 'fixed boxes move in a full-page screenshot of a taller page');
});

test('P7 recheck dla non-text: przesuniety, zmieniony lub usuniety kandydat; recheck kontrastu bez zmian', async () => {
  const fields = ['a', 'b', 'c', 'd'].map((id, i) => h('input', { attrs: { id }, type: 'text', rect: [10, 10 + 30 * i, 40, 20] }));
  for (const f of fields) f.isConnected = true;
  const { call } = await runOp(fields);
  assert.deepEqual(await call({ op: 'recheck', set: 'nonText' }), { width: 400, height: 300, shifted: 0 });
  fields[0].rect = [10.5, 10, 40, 20];
  fields[1].rect = [10, 40, 41, 20];
  fields[2].isConnected = false;
  assert.equal((await call({ op: 'recheck', set: 'nonText' })).shifted, 3);
  assert.deepEqual(await call({ op: 'recheck' }), { height: 300, shifted: 0 });
});

// ---------- runner ----------

// The fake screenshot is white with the painted rectangles [x, y, w, h, grey] of the document; clips are honoured.
const FakePNG = { sync: { read: (buf) => {
  const { x, y, w, h: height, paint } = JSON.parse(buf.toString());
  const data = new Uint8Array(w * height * 4).fill(255);
  for (const [px, py, pw, ph, v] of paint) {
    for (let j = Math.max(py, y); j < Math.min(py + ph, y + height); j++) {
      for (let i = Math.max(px, x); i < Math.min(px + pw, x + w); i++) data.fill(v, ((j - y) * w + (i - x)) * 4, ((j - y) * w + (i - x)) * 4 + 3);
    }
  }
  return { width: w, height, data };
} } };
const imaging = { PNG: FakePNG, pixelmatch: () => {} };

const side = (width = 2, style = 'solid') => ({ width, style });
const rawField = (id, over = {}) => ({
  id, idUnique: true, chain: [], key: id, parent: 'p0', within: [],
  tag: 'input', type: 'text', role: '', states: { selected: null, pressed: null, checked: null, expanded: null, current: null },
  hidden: false, opacity: 1, inactive: false, ariaHidden: false, interactive: true, alt: null, named: false, visibleText: false, graphicContent: false,
  control: null, userAgent: false, appearance: 'auto', pseudoControl: false, graphic: null,
  box: { x: 10, y: 10, width: 40, height: 20 }, borders: { top: side(), right: side(), bottom: side(), left: side() },
  flags: { filter: false, group: false, media: false, hit: null }, ...over,
});
// A 2 px frame inside the box.
const frame = ([x, y, w, h], v) => [[x, y, w, 2, v], [x, y + h - 2, w, 2, v], [x, y, 2, h, v], [x + w - 2, y, 2, h, v]];

async function audit({ items = [], truncated = false, paint = [], checks = ['non-text'], doc = { width: 100, height: 60 }, ops: extra = {}, shot = null, thresholds } = {}) {
  const cfg = { version: 1, pages: [{ id: 'p', url: 'a.html', checks, assert: { selector: 'main' } }], ...(thresholds ? { thresholds } : {}) };
  const v = validateA11yConfig(cfg, { configDir: CONFIG_DIR });
  assert.ok(v.ok, v.errors.join('; '));
  const calls = [];
  const shots = [];
  const ops = {
    validate: (a) => a.selectors.map(() => true),
    settle: () => doc,
    collect: () => ({ frames: 0, dir: 'ltr', contrast: { truncated: false, items: [] } }),
    nonText: () => ({ truncated, items }),
    recheck: () => ({ ...doc, shifted: 0 }),
    ...extra,
  };
  const page = {
    on() {}, url: () => 'x', async goto() {}, async waitForFunction() {},
    async evaluate(fn, args) { calls.push(args); assert.ok(ops[args.op], `unexpected page operation ${args.op}`); return ops[args.op](args); },
    keyboard: { async press() {} },
    async screenshot(opts) {
      shots.push(opts);
      if (shot) return shot(opts);
      const c = opts.clip || { x: 0, y: 0, ...doc };
      return Buffer.from(JSON.stringify({ x: c.x, y: c.y, w: c.width, h: c.height, paint }));
    },
    async close() {},
  };
  const context = { setDefaultTimeout() {}, async route() {}, async newPage() { return page; }, pages: () => [page], async close() {} };
  const runner = createA11yRunner({ browser: { newContext: async () => context }, imaging, config: v.config, configDir: CONFIG_DIR });
  const result = await runner.auditPage(v.config.pages[0]);
  await runner.close();
  const report = assembleReport({ pages: [result], config: v.config, exceptionsData: { exceptions: [], confirmations: [] }, now: '2026-11-26', digest: 'x', masker: null });
  const findings = report.pages[0].findings.filter((f) => f.rule === 'non-text-contrast');
  return { result, report, calls, shots, findings, got: findings.map((f) => [f.selector, f.verdict, f.reason ?? f.measured?.kind ?? null]) };
}

test('runner PASS i FAIL: wyrazne i blade obramowanie, drugi zrzut bez ukrywania tekstu', async () => {
  const items = [rawField('dark'), rawField('pale', { box: { x: 55, y: 10, width: 40, height: 20 } })];
  const run = await audit({ items, paint: [...frame([10, 10, 40, 20], 0), ...frame([55, 10, 40, 20], 221)] });
  assert.deepEqual(run.got, [['#dark', 'PASS', 'boundary'], ['#pale', 'FAIL', 'boundary']]);
  assert.equal(run.report.exit, 1);
  assert.equal(run.shots.length, 1);
  assert.deepEqual([run.shots[0].fullPage, run.shots[0].animations, run.shots[0].scale, run.shots[0].caret, 'style' in run.shots[0]], [true, 'disabled', 'css', 'hide', false]);
  assert.deepEqual(run.calls.map((c) => c.op), ['validate', 'settle', 'collect', 'nonText', 'recheck']);
  assert.deepEqual(run.calls.at(-1), { op: 'recheck', set: 'nonText' });
  assert.equal(run.calls[3].limit, 2000);
});

test('runner z kontrola contrast: dwa osobne zrzuty, najpierw z ukrytym tekstem', async () => {
  const title = { kind: 'text', id: 't', idUnique: true, chain: [], fontSizePx: 16, fontWeight: 400, color: { r: 0, g: 0, b: 0, a: 1 }, opacity: 1, hidden: false, inactive: false, flags: {}, rects: [[60, 40, 4, 2]] };
  const run = await audit({ checks: ['contrast', 'non-text'], items: [rawField('dark')], paint: frame([10, 10, 40, 20], 0), ops: { collect: () => ({ frames: 0, dir: 'ltr', contrast: { truncated: false, items: [title] } }) } });
  assert.deepEqual(run.shots.map((s) => 'style' in s), [true, false]);
  assert.deepEqual(run.calls.map((c) => c.op), ['validate', 'settle', 'collect', 'recheck', 'nonText', 'recheck']);
  assert.deepEqual(run.calls[3], { op: 'recheck' });
  assert.equal(run.report.exit, 0);
});

test('K1: bez non-text nie ma operacji nonText ani drugiego zrzutu', async () => {
  const run = await audit({ checks: ['contrast'], items: [rawField('dark')] });
  assert.deepEqual(run.calls.map((c) => c.op), ['validate', 'settle', 'collect']);
  assert.equal(run.shots.length, 0);
});

test('runner: EXEMPT i UNVERIFIED bez pikseli nie robia zrzutu; same EXEMPT to jeden no-samples, kod 3', async () => {
  const run = await audit({ items: [rawField('off', { inactive: true })] });
  assert.deepEqual(run.got, [['#off', 'EXEMPT', 'inactive'], [null, 'UNVERIFIED', 'no-samples']]);
  assert.equal(run.shots.length, 0);
  assert.equal(run.report.exit, 3);
  const pseudo = await audit({ items: [rawField('c', { type: 'checkbox', hidden: true, pseudoControl: true })] });
  assert.deepEqual(pseudo.got, [['#c', 'UNVERIFIED', 'pseudo-element-control']]);
});

test('runner: strona bez kandydatow to dokladnie jeden UNVERIFIED no-samples, takze obok innych PASS', async () => {
  const run = await audit({ checks: ['non-text', 'console'] });
  assert.deepEqual(run.got, [[null, 'UNVERIFIED', 'no-samples']]);
  assert.equal(run.result.findings.filter((f) => f.rule === 'non-text-contrast').length, 0);
  assert.equal(run.report.exit, 3);
});

test('runner: element poza dokumentem to clipped, nigdy PASS', async () => {
  const run = await audit({ items: [rawField('out', { box: { x: 80, y: 10, width: 40, height: 20 } })], paint: frame([80, 10, 40, 20], 0) });
  assert.deepEqual(run.got, [['#out', 'UNVERIFIED', 'clipped']]);
});

test('runner: inny komponent nad polem to occluded; kontener i przodek nie wykluczaja', async () => {
  const paint = frame([10, 10, 40, 20], 0);
  const over = rawField('over', { tag: 'button', type: '', visibleText: true, box: { x: 30, y: 5, width: 30, height: 30 } });
  const covered = await audit({ items: [rawField('dark'), over], paint });
  assert.deepEqual(covered.got[0], ['#dark', 'UNVERIFIED', 'occluded']);
  const wide = { tag: 'button', type: '', visibleText: true, box: { x: 0, y: 0, width: 90, height: 50 } };
  const contained = await audit({ items: [rawField('dark'), rawField('wrap', wide)], paint });
  assert.deepEqual(contained.got[0], ['#dark', 'PASS', 'boundary']);
  const ancestor = rawField('anc', { ...wide, box: { x: 30, y: 5, width: 30, height: 30 } });
  const related = await audit({ items: [ancestor, rawField('dark', { within: [0] })], paint });
  assert.deepEqual(related.got.find((g) => g[0] === '#dark'), ['#dark', 'PASS', 'boundary']);
  const child = await audit({ items: [rawField('dark'), rawField('kid', { tag: 'button', type: '', visibleText: true, within: [0], box: { x: 20, y: 15, width: 40, height: 5 } })], paint });
  assert.deepEqual(child.got[0], ['#dark', 'PASS', 'boundary']);
  const unseen = await audit({ items: [rawField('dark'), { ...over, hidden: true }], paint });
  assert.deepEqual(unseen.got[0], ['#dark', 'PASS', 'boundary']);
});

test('runner: ikona w przycisku, PASS i FAIL', async () => {
  const icon = (id, x) => rawField(id, { tag: 'svg', type: '', interactive: false, borders: { top: side(0, 'none'), right: side(0, 'none'), bottom: side(0, 'none'), left: side(0, 'none') }, control: { hasVisibleText: false, soleContent: true }, graphic: { raster: false, gradient: false, filter: false, fills: 1 }, box: { x, y: 10, width: 16, height: 16 } });
  const run = await audit({ items: [icon('dark', 10), icon('pale', 60)], paint: [[12, 12, 12, 12, 0], [62, 12, 12, 12, 221]] });
  assert.deepEqual(run.got.map((g) => g.slice(0, 2)), [['#dark', 'PASS'], ['#pale', 'FAIL']]);
});

test('runner: stan z rodzenstwem off zapisuje offContrast', async () => {
  const states = (selected) => ({ selected, pressed: null, checked: null, expanded: null, current: null });
  const tab = (id, selected, x) => rawField(id, { tag: 'div', type: '', role: 'tab', states: states(selected), visibleText: true, box: { x, y: 10, width: 30, height: 20 }, borders: { top: side(0, 'none'), right: side(0, 'none'), bottom: side(0, 'none'), left: side(0, 'none') } });
  const run = await audit({ items: [tab('on', 'true', 10), tab('other', 'false', 60)], paint: [[10, 10, 30, 20, 0]] });
  assert.equal(run.findings[0].verdict, 'PASS');
  assert.equal(run.findings[0].measured.offContrast, 21);
});

test('runner: strona wyzsza niz 16000 px: piksele regionu z dwoch klipow w jednej probce', async () => {
  const box = { x: 10, y: 3990, width: 40, height: 20 };
  const run = await audit({ items: [rawField('seam', { box })], paint: frame([10, 3990, 40, 20], 0), doc: { width: 100, height: 20000 } });
  assert.deepEqual(run.got, [['#seam', 'PASS', 'boundary']]);
  assert.deepEqual(run.shots.map((s) => s.clip.y), [0, 4000, 8000, 12000, 16000]);
});

test('runner: pominiete piksele (zrzut mniejszy od dokumentu) nie daja PASS', async () => {
  const small = () => Buffer.from(JSON.stringify({ x: 0, y: 0, w: 30, h: 60, paint: frame([10, 10, 40, 20], 0) }));
  const run = await audit({ items: [rawField('dark')], shot: small });
  assert.deepEqual(run.got, [['#dark', 'UNVERIFIED', 'no-samples']]);
});

test('P7 runner: przesuniecie ukladu to UNVERIFIED layout-shift bez werdyktow z pikseli', async () => {
  for (const after of [{ width: 100, height: 60, shifted: 1 }, { width: 100, height: 80, shifted: 0 }, { width: 120, height: 60, shifted: 0 }, {}]) {
    const run = await audit({ items: [rawField('off', { inactive: true }), rawField('dark')], paint: frame([10, 10, 40, 20], 0), ops: { recheck: () => after } });
    assert.deepEqual(run.got, [['#off', 'EXEMPT', 'inactive'], [null, 'UNVERIFIED', 'layout-shift']], JSON.stringify(after));
    assert.ok(run.result.warnings.some((w) => w.code === 'layout-shift'));
    assert.equal(run.report.exit, 3);
  }
});

test('P7 runner: strona ponad limitami to page-too-tall bez zrzutu', async () => {
  const run = await audit({ items: [rawField('dark')], truncated: true, doc: { width: 100, height: 70000 } });
  assert.deepEqual(run.got, [[null, 'UNVERIFIED', 'page-too-tall'], [null, 'UNVERIFIED', 'truncated']]);
  assert.equal(run.shots.length, 0);
  assert.deepEqual(run.result.warnings.map((w) => w.code), ['page-too-tall', 'nontext-truncated']);
});

test('runner: truncated dopisuje nontext-truncated i ostrzezenie', async () => {
  const run = await audit({ items: [rawField('dark')], truncated: true, paint: frame([10, 10, 40, 20], 0) });
  assert.deepEqual(run.got, [['#dark', 'PASS', 'boundary'], [null, 'UNVERIFIED', 'truncated']]);
  assert.ok(run.result.warnings.some((w) => w.code === 'nontext-truncated'));
  assert.equal(run.report.exit, 3);
});

test('runner: blad operacji lub zrzutu to UNVERIFIED no-samples bez cytowania bledu; limit dekodera PNG przechodzi dalej', async () => {
  const boom = () => { throw new Error('page says: secret banner'); };
  for (const over of [{ ops: { nonText: boom } }, { ops: { nonText: () => null } }, { items: [rawField('dark')], shot: boom }, { items: [rawField('dark')], ops: { recheck: boom } }]) {
    const run = await audit(over);
    assert.deepEqual(run.got, [[null, 'UNVERIFIED', 'no-samples']]);
    assert.ok(run.result.warnings.some((w) => w.code === 'nontext-failed'));
    assert.ok(!JSON.stringify(run.report).includes('secret banner'));
  }
  const bomb = Buffer.alloc(33);
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(bomb, 0); bomb.writeUInt32BE(13, 8); bomb.write('IHDR', 12, 'latin1'); bomb.writeUInt32BE(16385, 16); bomb.writeUInt32BE(1, 20);
  await assert.rejects(audit({ items: [rawField('dark')], shot: () => bomb }), (e) => e.name === 'ImageLimitError');
});

test('runner: smieciowy rekord strony to UNVERIFIED, thresholds.nonText dziala', async () => {
  const bad = await audit({ items: [null, { id: 'x', idUnique: true, chain: [] }] });
  assert.ok(bad.findings.every((f) => f.verdict === 'UNVERIFIED' && f.reason === 'no-samples'));
  const run = await audit({ items: [rawField('pale')], paint: frame([10, 10, 40, 20], 221), thresholds: { nonText: 1.2, reason: 'test' } });
  assert.deepEqual(run.got, [['#pale', 'PASS', 'boundary']]);
});
