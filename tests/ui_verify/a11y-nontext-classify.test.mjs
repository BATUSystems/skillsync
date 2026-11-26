// CORE-1150-T2B B2: candidates of the non-text contrast check: kinds, exemptions, UNVERIFIED reasons, limit.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  candidateFindings, classifyCandidate, nonTextFindings, UA_CONTROL_TYPES,
} from '../../library/skills/ui-verify/scripts/lib/a11y-nontext.mjs';
import { LIMITS } from '../../library/skills/ui-verify/scripts/lib/a11y-schema.mjs';
import { PAGE_SCRIPT } from '../../library/skills/ui-verify/scripts/lib/a11y-page.mjs';

const side = (width = 1, style = 'solid') => ({ width, style });
const noStates = () => ({ selected: null, pressed: null, checked: null, expanded: null, current: null });
const cand = (over = {}) => ({
  selector: '#a', tag: 'input', type: 'text', role: '', states: noStates(),
  hidden: false, opacity: 1, inactive: false, ariaHidden: false, interactive: true,
  alt: null, named: false, visibleText: false, graphicContent: false, control: null,
  userAgent: false, appearance: 'auto', pseudoControl: false, graphic: null,
  box: { x: 20, y: 20, width: 60, height: 30 },
  borders: { top: side(), right: side(), bottom: side(), left: side() },
  flags: { filter: false, group: false, media: false, hit: null },
  ...over,
});
const vector = (over = {}) => ({ raster: false, gradient: false, filter: false, fills: 1, ...over });
const svg = (over = {}) => cand({
  tag: 'svg', type: '', interactive: false, graphic: vector(), control: { hasVisibleText: false, soleContent: true },
  box: { x: 40, y: 20, width: 16, height: 16 }, borders: { top: side(0, 'none'), right: side(0, 'none'), bottom: side(0, 'none'), left: side(0, 'none') }, ...over,
});
const button = (over = {}) => cand({ tag: 'button', type: 'submit', visibleText: true, ...over });
const states = (over) => ({ ...noStates(), ...over });

const flat = (v, n = 40) => ({ pixels: Array.from({ length: n }, () => [v, v, v]), total: n, excluded: 0, outside: 0 });
const samples = (v = 117) => ({ component: { band: flat(v), fill: flat(255), ring: flat(255) }, icon: { icon: flat(v), ring: flat(255) } });

const brief = (list) => list.map((f) => [f.verdict, f.reason ?? f.measured?.kind]);
const run = (c, s = samples()) => brief(candidateFindings(c, s));

// ---------- kinds ----------

test('form components are judged as boundary', () => {
  for (const type of ['text', 'email', 'password', 'search', 'number', 'checkbox', 'radio', 'range', 'file', 'date', 'color', 'something-new']) {
    assert.deepEqual(run(cand({ type })), [['PASS', 'boundary']], type);
  }
  for (const tag of ['textarea', 'select', 'TEXTAREA']) assert.deepEqual(run(cand({ tag, type: '' })), [['PASS', 'boundary']], tag);
  for (const role of ['textbox', 'combobox', 'searchbox', 'spinbutton', 'listbox', 'slider', 'switch', 'checkbox', 'radio', 'Switch checkbox']) {
    assert.deepEqual(run(cand({ tag: 'div', type: '', role })), [['PASS', 'boundary']], role);
  }
  const f = candidateFindings(cand(), samples(221))[0];
  assert.deepEqual([f.verdict, f.selector, f.rule, f.required], ['FAIL', '#a', 'non-text-contrast', 3]);
  assert.equal(candidateFindings(cand(), samples(117), { thresholds: { nonText: 7 } })[0].verdict, 'FAIL');
});

test('input types with their own text or image are not candidates', () => {
  for (const type of ['hidden', 'submit', 'button', 'reset', 'image', 'SUBMIT']) {
    assert.deepEqual(candidateFindings(cand({ type }), samples()), [], type);
  }
});

test('state indicators in the current state', () => {
  const tab = (s) => cand({ tag: 'div', type: '', role: 'tab', states: states(s) });
  for (const s of [{ selected: 'true' }, { pressed: 'true' }, { pressed: 'mixed' }, { checked: 'TRUE' }, { checked: 'mixed' },
    { expanded: 'true' }, { expanded: 'false' }, { current: 'page' }, { current: 'true' }, { current: 'anything' }]) {
    assert.deepEqual(run(tab(s)), [['PASS', 'state']], JSON.stringify(s));
  }
  // a component with a state gives both kinds from the same pixels
  assert.deepEqual(run(cand({ tag: 'div', type: '', role: 'switch', states: states({ checked: 'true' }) }), samples(221)), [['FAIL', 'boundary'], ['FAIL', 'state']]);
  assert.deepEqual(run(cand({ type: 'checkbox', states: states({ checked: 'false' }) })), [['PASS', 'boundary']]);
  const off = candidateFindings(tab({ selected: 'true' }), { component: { ...samples(221).component, offFill: flat(0) } })[0];
  assert.ok(off.measured.offContrast > 3);
});

test('states that are not "on" make no state indicator', () => {
  for (const s of [{ selected: 'false' }, { pressed: 'false' }, { checked: 'false' }, { current: 'false' }, { selected: '' }, { expanded: 'undefined' }]) {
    assert.deepEqual(run(button({ states: states(s) })), [['EXEMPT', 'text-identifies']], JSON.stringify(s));
  }
});

test('the visible text of a button never exempts its state indicator', () => {
  assert.deepEqual(run(button({ states: states({ pressed: 'true' }) }), samples(221)), [['FAIL', 'state']]);
  assert.deepEqual(run(cand({ tag: 'a', type: '', visibleText: true, states: states({ current: 'page' }) })), [['PASS', 'state']]);
});

test('graphics: sole content of a control, or role=img with a name', () => {
  assert.deepEqual(run(svg()), [['PASS', 'icon']]);
  assert.deepEqual(run(svg(), samples(221)), [['FAIL', 'icon']]);
  assert.deepEqual(run(svg({ control: null, role: 'img', named: true })), [['PASS', 'icon']]);
  assert.deepEqual(run(svg({ control: null, tag: 'span', role: 'img', named: true })), [['PASS', 'icon']]);
  assert.deepEqual(run(svg({ control: null, interactive: true })), [['PASS', 'icon']]);
  // outside a control, without role=img and a name: not a candidate of the spec
  assert.deepEqual(run(svg({ control: null })), []);
  assert.deepEqual(run(svg({ control: null, role: 'img' })), []);
  assert.deepEqual(run(svg({ control: null, tag: 'img', named: true, alt: 'chart' })), []);
});

test('graphics: one of several graphics of a control without text is UNVERIFIED, also when aria-hidden', () => {
  const several = { hasVisibleText: false, soleContent: false };
  assert.deepEqual(run(svg({ control: several })), [['UNVERIFIED', 'shape-unknown']]);
  assert.deepEqual(run(svg({ control: several, ariaHidden: true })), [['UNVERIFIED', 'shape-unknown']]);
});

test('buttons and links without text: judged by their graphic, otherwise UNVERIFIED', () => {
  assert.deepEqual(run(button({ visibleText: false, graphicContent: true })), []);
  assert.deepEqual(run(button({ visibleText: false })), [['UNVERIFIED', 'shape-unknown']]);
  assert.deepEqual(run(cand({ tag: 'a', type: '', visibleText: false })), [['UNVERIFIED', 'shape-unknown']]);
});

test('an element that matches no kind is UNVERIFIED, not silently dropped', () => {
  assert.deepEqual(run(cand({ tag: 'div', type: '' })), [['UNVERIFIED', 'shape-unknown']]);
  assert.deepEqual(run(cand({ tag: 'a', type: '', interactive: false, visibleText: true })), [['UNVERIFIED', 'shape-unknown']]);
});

// ---------- exemptions ----------

test('text-identifies: a button or link with visible text, and a named icon next to text', () => {
  for (const c of [button(), cand({ tag: 'a', type: '', visibleText: true }), cand({ tag: 'div', type: '', role: 'button', visibleText: true }),
    cand({ tag: 'span', type: '', role: 'link', visibleText: true }), button({ role: 'tab' }),
    svg({ role: 'img', named: true, control: { hasVisibleText: true, soleContent: false } })]) {
    assert.deepEqual(run(c), [['EXEMPT', 'text-identifies']], `${c.tag} ${c.role}`);
  }
  // text never identifies a form component
  assert.deepEqual(run(button({ role: 'switch' })), [['PASS', 'boundary']]);
  assert.deepEqual(run(cand({ tag: 'div', type: '', role: 'combobox', visibleText: true })), [['PASS', 'boundary']]);
});

test('decorative: only for what is neither interactive nor the identification of a control', () => {
  const text = { hasVisibleText: true, soleContent: false };
  for (const c of [
    svg({ control: text }), svg({ control: null, ariaHidden: true }), svg({ control: null, role: 'presentation' }),
    svg({ control: null, role: 'none' }), svg({ control: null, tag: 'img', alt: '' }), svg({ control: text, ariaHidden: true, named: true }),
    cand({ tag: 'div', type: '', interactive: false, ariaHidden: true, states: states({ selected: 'true' }) }),
  ]) assert.deepEqual(run(c), [['EXEMPT', 'decorative']], `${c.tag} ${c.role}`);
});

test('decorative: the only graphic of a button without text is measured whatever hides it from assistive technology', () => {
  for (const over of [{ ariaHidden: true }, { role: 'presentation' }, { role: 'none' }, { tag: 'img', alt: '' }]) {
    const out = run(svg(over), samples(221));
    assert.deepEqual(out, [over.tag === 'img' ? ['UNVERIFIED', 'raster-graphic'] : ['FAIL', 'icon']], JSON.stringify(over));
  }
});

test('decorative: an interactive element with aria-hidden or role=presentation never disappears', () => {
  assert.deepEqual(run(cand({ ariaHidden: true }), samples(221)), [['FAIL', 'boundary']]);
  assert.deepEqual(run(cand({ role: 'presentation', interactive: false })), [['PASS', 'boundary']]); // a form control
  assert.deepEqual(run(button({ ariaHidden: true, visibleText: false })), [['UNVERIFIED', 'shape-unknown']]);
  assert.deepEqual(run(button({ ariaHidden: true })), [['EXEMPT', 'text-identifies']]);
  assert.deepEqual(run(svg({ control: null, interactive: true, ariaHidden: true })), [['PASS', 'icon']]);
  assert.deepEqual(run(cand({ tag: 'div', type: '', ariaHidden: true, states: states({ selected: 'true' }) })), [['PASS', 'state']]);
  const note = candidateFindings(cand({ ariaHidden: true }), samples())[0].note;
  assert.match(note, /aria-hidden/);
});

test('user-agent: exactly the flag on a native control of the listed types with its appearance', () => {
  for (const type of UA_CONTROL_TYPES) assert.deepEqual(run(cand({ type, userAgent: true })), [['EXEMPT', 'user-agent']], type);
  for (const over of [{ type: 'text' }, { type: 'checkbox', appearance: 'none' }, { type: 'checkbox', appearance: '' }, { tag: 'div', type: 'checkbox', role: 'checkbox' }]) {
    assert.deepEqual(run(cand({ userAgent: true, ...over })), [['PASS', 'boundary']], JSON.stringify(over));
  }
  assert.deepEqual(run(cand({ type: 'checkbox', userAgent: false })), [['PASS', 'boundary']]);
});

test('user-agent types are the ones the page script uses for targets', () => {
  assert.ok(PAGE_SCRIPT.includes(`new Set([${UA_CONTROL_TYPES.map((t) => `'${t}'`).join(', ')}])`));
});

test('order: not-rendered, inactive, decorative, text-identifies, user-agent', () => {
  const all = { hidden: true, inactive: true, interactive: false, ariaHidden: true };
  const named = { role: 'img', named: true, control: { hasVisibleText: true, soleContent: false } };
  assert.deepEqual(run(svg({ ...all, ...named })), [['EXEMPT', 'not-rendered']]);
  assert.deepEqual(run(svg({ ...all, ...named, hidden: false })), [['EXEMPT', 'inactive']]);
  assert.deepEqual(run(svg({ ...all, ...named, hidden: false, inactive: false })), [['EXEMPT', 'decorative']]);
  assert.deepEqual(run(svg(named)), [['EXEMPT', 'text-identifies']]);
  assert.deepEqual(run(cand({ opacity: 0 })), [['EXEMPT', 'not-rendered']]);
  assert.deepEqual(run(cand({ type: 'checkbox', userAgent: true, inactive: true })), [['EXEMPT', 'inactive']]);
  // exemptions come before the measurement flags
  assert.deepEqual(run(cand({ inactive: true, flags: { filter: true, group: true, media: true, hit: 'occluded' } })), [['EXEMPT', 'inactive']]);
  const f = candidateFindings(cand({ hidden: true }), samples())[0];
  assert.deepEqual([f.selector, f.measured], ['#a', undefined]);
});

// ---------- UNVERIFIED ----------

test('pseudo-element control: a visually hidden input is not "not rendered" and not "user-agent"', () => {
  for (const over of [{}, { hidden: true }, { opacity: 0 }, { userAgent: true }, { flags: { filter: false, group: true, media: false, hit: 'occluded' } }]) {
    assert.deepEqual(run(cand({ type: 'checkbox', pseudoControl: true, ...over })), [['UNVERIFIED', 'pseudo-element-control']], JSON.stringify(over));
  }
  assert.deepEqual(run(cand({ type: 'radio', pseudoControl: true, hidden: true, inactive: true })), [['EXEMPT', 'inactive']]);
  assert.deepEqual(run(cand({ tag: 'div', type: '', role: 'checkbox', pseudoControl: true, hidden: true })), [['UNVERIFIED', 'no-samples']]);
});

test('measurement flags, in the order of the text check', () => {
  const flags = (over) => cand({ flags: { filter: false, group: false, media: false, hit: null, ...over } });
  assert.deepEqual(run(flags({ filter: true, group: true })), [['UNVERIFIED', 'filter-or-blend']]);
  assert.deepEqual(run(flags({ group: true, media: true })), [['UNVERIFIED', 'opacity-group-with-paint']]);
  assert.deepEqual(run(flags({ media: true, hit: 'clipped' })), [['UNVERIFIED', 'media-background']]);
  assert.deepEqual(run(flags({ hit: 'occluded' })), [['UNVERIFIED', 'occluded']]);
  assert.deepEqual(run(flags({ hit: 'clipped' })), [['UNVERIFIED', 'clipped']]);
  // one finding for the element, not one per kind
  assert.deepEqual(run(cand({ role: 'switch', states: states({ checked: 'true' }), flags: { filter: true, group: false, media: false, hit: null } })), [['UNVERIFIED', 'filter-or-blend']]);
});

test('raster and complex graphics', () => {
  for (const tag of ['img', 'canvas', 'IMG']) assert.deepEqual(run(svg({ tag })), [['UNVERIFIED', 'raster-graphic']], tag);
  assert.deepEqual(run(svg({ graphic: vector({ raster: true }) })), [['UNVERIFIED', 'raster-graphic']]);
  for (const over of [{ gradient: true }, { filter: true }, { fills: 7 }]) {
    assert.deepEqual(run(svg({ graphic: vector(over) })), [['UNVERIFIED', 'complex-graphic']], JSON.stringify(over));
  }
  assert.deepEqual(run(svg({ graphic: vector({ fills: 6 }) })), [['PASS', 'icon']]);
  assert.deepEqual(run(svg({ graphic: null })), [['UNVERIFIED', 'no-samples']]);
  // the state of the same element is still judged
  // (an svg without a border and with a white fill on white: the state fails)
  assert.deepEqual(run(svg({ tag: 'img', states: states({ pressed: 'true' }) })), [['UNVERIFIED', 'raster-graphic'], ['FAIL', 'state']]);
});

test('geometry and samples: degenerate shape and missing pixels are UNVERIFIED, once per element', () => {
  const tiny = { x: 0, y: 0, width: 4, height: 4 };
  assert.deepEqual(run(cand({ box: tiny, states: states({ checked: 'true' }) })), [['UNVERIFIED', 'shape-unknown']]);
  assert.deepEqual(run(svg({ box: { ...tiny, width: 0 } })), [['UNVERIFIED', 'shape-unknown']]);
  for (const s of [undefined, null, 'x', {}, { component: null }, { component: { band: flat(117) } }]) {
    // candidateFindings directly: the default of run() would replace an undefined sample
    assert.deepEqual(brief(candidateFindings(cand({ states: states({ checked: 'true' }) }), s)), [['UNVERIFIED', 'no-samples']], JSON.stringify(s));
  }
  assert.deepEqual(run(svg(), { component: samples().component }), [['UNVERIFIED', 'no-samples']]);
});

test('band: taken only when the element has a visible border, and then required', () => {
  const bare = { top: side(0), right: side(0), bottom: side(2, 'none'), left: side(3, 'hidden') };
  const s = { component: { band: flat(0), fill: flat(255), ring: flat(255) } };
  assert.deepEqual(run(cand({ borders: bare }), s), [['FAIL', 'boundary']]); // a dark "band" of no border never passes it
  assert.deepEqual(run(cand(), { component: { band: null, fill: flat(0), ring: flat(255) } }), [['UNVERIFIED', 'no-samples']]);
  const plan = classifyCandidate(cand());
  assert.deepEqual(plan.measure, ['boundary']);
  assert.equal(plan.regions.component.band.length, 4);
  assert.equal(plan.regions.icon, null);
  assert.equal(classifyCandidate(cand({ borders: bare })).regions.component.band, null);
  assert.equal(classifyCandidate(svg()).regions.icon.ok, true);
  assert.deepEqual(classifyCandidate(cand({ inactive: true })).measure, []);
});

// ---------- untrusted data ----------

const bad = [undefined, null, NaN, 'yes', 1, {}, []];

test('contract: a missing, mistyped or unknown field is UNVERIFIED without a way out, never EXEMPT or PASS', () => {
  const exempting = cand({ type: 'checkbox', userAgent: true, hidden: true, inactive: true });
  const check = (c, label) => {
    const out = candidateFindings(c, samples());
    assert.deepEqual(brief(out), [['UNVERIFIED', 'no-samples']], label);
    assert.equal(out[0].measured, undefined, label);
  };
  for (const base of [cand(), exempting, svg(), button()]) {
    for (const key of Object.keys(base)) {
      const missing = { ...base };
      delete missing[key];
      check(missing, `missing ${key}`);
    }
  }
  const wrong = {
    selector: ['', 5, null], tag: ['', 5, null], type: [null, 5], role: [null, 5],
    hidden: bad, inactive: bad, ariaHidden: bad, interactive: bad, named: bad, visibleText: bad, graphicContent: bad, userAgent: bad, pseudoControl: bad,
    opacity: [undefined, null, NaN, -1, 2, '0'], alt: [undefined, 0, false], appearance: [null, 0],
    control: [undefined, 0, 'x', [], {}, { hasVisibleText: true }, { hasVisibleText: 'true', soleContent: false }, { hasVisibleText: true, soleContent: true }],
    graphic: [undefined, 0, 'x', [], {}, vector({ raster: 'no' }), vector({ gradient: undefined }), vector({ filter: 0 }), vector({ fills: -1 }), vector({ fills: 1.5 }), vector({ fills: NaN })],
    states: [undefined, null, [], {}, states({ selected: 'yes' }), states({ pressed: true }), states({ checked: '1' }), states({ expanded: 'mixed' }), states({ current: 5 })],
    box: [undefined, null, {}, { x: 0, y: 0, width: NaN, height: 5 }, { x: 0, y: 0, width: -1, height: 5 }],
    borders: [undefined, null, {}, { top: side() }, { top: side(-1), right: side(), bottom: side(), left: side() }, { top: side(1, 'wavy'), right: side(), bottom: side(), left: side() }, { top: side('1'), right: side(), bottom: side(), left: side() }],
    flags: [undefined, null, {}, { filter: false, group: false, media: false }, { filter: 0, group: false, media: false, hit: null }, { filter: false, group: false, media: false, hit: 'covered' }],
  };
  for (const [key, values] of Object.entries(wrong)) {
    for (const v of values) {
      check(cand({ [key]: v }), `${key}=${JSON.stringify(v)}`);
      check({ ...exempting, [key]: v }, `exempting ${key}=${JSON.stringify(v)}`);
    }
  }
});

test('contract: something that is not a record gives an UNVERIFIED finding with its own key basis', () => {
  for (const c of [undefined, null, 5, 'x', []]) {
    const out = candidateFindings(c, samples());
    assert.deepEqual([out.length, out[0].verdict, out[0].reason, out[0].selector, out[0].hashBasis], [1, 'UNVERIFIED', 'no-samples', undefined, 'nontext-invalid']);
  }
  assert.equal(candidateFindings(cand({ selector: 7 }), samples())[0].selector, undefined);
  assert.equal(candidateFindings(cand({ hidden: 'x' }), samples())[0].selector, '#a');
});

// ---------- the list and its limit ----------

test('list: findings of every candidate in order; a broken list never gives an empty result', () => {
  const entry = (over) => ({ candidate: cand(over), samples: samples() });
  assert.deepEqual(nonTextFindings([entry(), entry({ selector: '#b', inactive: true })]).map((f) => [f.selector, f.verdict]), [['#a', 'PASS'], ['#b', 'EXEMPT']]);
  assert.deepEqual(nonTextFindings([]), []);
  for (const list of [undefined, null, {}, 'x']) assert.deepEqual(brief(nonTextFindings(list)), [['UNVERIFIED', 'no-samples']]);
  assert.deepEqual(brief(nonTextFindings([null])), [['UNVERIFIED', 'no-samples']]);
});

test('list: more than the limit, or a truncated collection, adds nontext-truncated', () => {
  const entry = { candidate: cand({ inactive: true }), samples: null };
  const full = nonTextFindings(Array(LIMITS.nonText).fill(entry));
  assert.equal(full.length, LIMITS.nonText);
  assert.ok(full.every((f) => f.reason !== 'truncated'));
  const over = nonTextFindings(Array(LIMITS.nonText + 3).fill(entry));
  assert.equal(over.length, LIMITS.nonText + 1);
  assert.deepEqual([over.at(-1).reason, over.at(-1).hashBasis], ['truncated', 'nontext-truncated']);
  for (const truncated of [true, 'false', 0, null]) assert.equal(nonTextFindings([entry], { truncated }).at(-1).reason, 'truncated', String(truncated));
  assert.equal(nonTextFindings([entry], { truncated: false }).length, 1);
});

test('an element collected only for a state that is off is not a candidate', () => {
  const el = (tag, role, s) => cand({ tag, type: '', role, states: states(s), visibleText: true });
  for (const c of [el('div', 'tab', { selected: 'false' }), el('li', 'option', { selected: 'false' }), el('div', '', { current: 'false' }),
    el('div', 'tab', { selected: '' }), el('span', '', { pressed: 'false' }), el('div', '', { checked: 'undefined' })]) {
    assert.deepEqual(candidateFindings(c, samples()), [], `${c.tag} ${JSON.stringify(c.states)}`);
  }
  // a button keeps its own rule, and an element without any state attribute is still unknown
  assert.deepEqual(run(button({ role: 'tab', states: states({ selected: 'false' }) })), [['EXEMPT', 'text-identifies']]);
  assert.deepEqual(run(el('div', 'tab', {})), [['UNVERIFIED', 'shape-unknown']]);
  // the state on is still judged
  assert.deepEqual(run(el('div', 'tab', { selected: 'true' })), [['PASS', 'state']]);
});
