// Opt-in focus indicator rules focus-contrast and focus-appearance (CORE-1150-T2B, A3; spec A1-A5, P9, P14, P15, P17,
// P20, P23, P25, P26; criteria K1-K3): classification, modes, the page contract and the runner with a scripted page.
// No browser, no clock, no network.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { assembleReport } from '../../library/skills/ui-verify/scripts/a11y.mjs';
import { validateA11yConfig } from '../../library/skills/ui-verify/scripts/lib/a11y-config.mjs';
import { parseExceptionsFile } from '../../library/skills/ui-verify/scripts/lib/a11y-exceptions.mjs';
import { classifyFocusRules, focusGeometry, focusRuleModes } from '../../library/skills/ui-verify/scripts/lib/a11y-focus.mjs';
import { PAGE_SCRIPT, createA11yRunner } from '../../library/skills/ui-verify/scripts/lib/a11y-page.mjs';
import { renderMarkdown } from '../../library/skills/ui-verify/scripts/lib/a11y-report.mjs';
import { CONFIRMABLE_REASONS, LIMIT_REASONS, UNVERIFIED_REASONS } from '../../library/skills/ui-verify/scripts/lib/a11y-schema.mjs';

const CONFIG_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-a11y-rules-'));
const NOW = '2026-11-26';
const VIEWPORT = { width: 1280, height: 800 };
const NEW_RULES = ['focus-contrast', 'focus-appearance'];

const info = (over = {}) => ({ vr: { x: 100, y: 100, width: 100, height: 40 }, outlineOffset: 0, outlineWidth: 2, borderRadius: 0, getClientRects: 1, ...over });
const geometry = (over) => focusGeometry(info(over), VIEWPORT, 8);
const metrics = (over = {}) => ({ diffPx: 600, qualifyingStrict: 500, qualifyingAA: 560, ratioMedian: 8.12345, ratioMin: 1.23456, requiredArea: 560, threshold: 516, ...over });
const BOTH = { contrast: 'fail', appearance: 'fail' };
const classify = (over = {}) => classifyFocusRules({ diffPx: 600, minDiffPx: 4, modes: BOTH, geometry: geometry(), metrics: metrics(), ...over });
const verdicts = (list) => list.map((f) => [f.rule, f.verdict, f.reason ?? null]);

// ---------- vocabulary ----------

test('A3: shape-unknown i clipped sa UNVERIFIED i potwierdzalne', () => {
  for (const r of ['shape-unknown', 'clipped']) {
    assert.ok(CONFIRMABLE_REASONS.includes(r) && UNVERIFIED_REASONS.includes(r) && !LIMIT_REASONS.includes(r), r);
  }
});

test('K1: brak kluczy focus.contrast i focus.appearance to off', () => {
  assert.deepEqual(focusRuleModes({ minDiffPx: 4, mouse: null }), { contrast: 'off', appearance: 'off' });
  assert.deepEqual(focusRuleModes({ contrast: 'report' }), { contrast: 'report', appearance: 'off' });
  assert.deepEqual(focusRuleModes({ appearance: 'fail' }), { contrast: 'off', appearance: 'fail' });
});

// ---------- geometry (P26) ----------

test('P26: dotkniecie krawedzi viewportu jest mierzone, wyjscie poza niego to clipped', () => {
  assert.equal(geometry({ vr: { x: 0, y: 0, width: 1280, height: 800 } }).clipped, false);
  for (const vr of [{ x: -0.5, y: 0, width: 10, height: 10 }, { x: 0, y: -1, width: 10, height: 10 }, { x: 1271, y: 0, width: 10, height: 10 }, { x: 0, y: 791, width: 10, height: 10 }]) {
    assert.equal(geometry({ vr }).clipped, true, JSON.stringify(vr));
  }
});

test('P26: outlineOffset + outlineWidth > 8 to clipped, rowne 8 jest mierzone, ujemny offset dozwolony', () => {
  assert.equal(geometry({ outlineOffset: 6, outlineWidth: 2 }).clipped, false);
  assert.equal(geometry({ outlineOffset: 6.5, outlineWidth: 2 }).clipped, true);
  assert.equal(geometry({ outlineOffset: -20, outlineWidth: 4 }).clipped, false);
});

test('P26: nieczytelne liczby ze strony nie daja pomiaru: outline to clipped', () => {
  for (const bad of [undefined, null, NaN, Infinity, '2px']) {
    assert.equal(geometry({ outlineOffset: bad }).clipped, true, `offset ${bad}`);
    assert.equal(geometry({ outlineWidth: bad }).clipped, true, `width ${bad}`);
  }
  assert.equal(geometry({ outlineWidth: -1 }).clipped, true);
  for (const vr of [undefined, null, { x: NaN, y: 0, width: 1, height: 1 }, { x: 0, y: 0, width: -1, height: 1 }]) {
    assert.equal(geometry({ vr }).clipped, true, JSON.stringify(vr));
  }
});

test('A4/P17: prostokat i promien tylko dla jednego niepustego prostokata klienta i liczbowego promienia', () => {
  assert.deepEqual(geometry({ borderRadius: 12 }).box, { width: 100, height: 40, borderRadius: 12 });
  for (const over of [
    { getClientRects: 2 }, { getClientRects: 0 }, { getClientRects: undefined }, { getClientRects: 1.5 }, { getClientRects: '1' },
    { borderRadius: null }, { borderRadius: undefined }, { borderRadius: NaN }, { borderRadius: -1 }, { borderRadius: '4px' },
    { vr: { x: 1, y: 1, width: 0, height: 10 } }, { vr: { x: 1, y: 1, width: 10, height: 0 } },
  ]) assert.equal(geometry(over).box, null, JSON.stringify(over));
});

// ---------- classification (A3, A4, P15, P23) ----------

test('K2: pierscien kontrastowy i dosc duzy: obie reguly PASS z zapisanymi miarami', () => {
  const [c, a] = classify();
  assert.deepEqual(verdicts([c, a]), [['focus-contrast', 'PASS', null], ['focus-appearance', 'PASS', null]]);
  assert.deepEqual(c.measured, { diffPx: 600, qualifyingStrict: 500, ratioMedian: 8.123, ratioMin: 1.235 });
  assert.equal(c.required, 3);
  assert.deepEqual(a.measured, { diffPx: 600, qualifyingAA: 560, requiredArea: 560, threshold: 516 });
  assert.equal(a.required, 516);
});

test('A3: focus-contrast PASS od minDiffPx pikseli kwalifikujacych (strict), inaczej FAIL z notatka', () => {
  const run = (qualifyingStrict) => classify({ modes: { contrast: 'fail', appearance: 'off' }, metrics: metrics({ qualifyingStrict, qualifyingAA: 999 }) });
  assert.deepEqual(verdicts(run(4)), [['focus-contrast', 'PASS', null]]);
  const [f] = run(3);
  assert.equal(f.verdict, 'FAIL');
  assert.match(f.note, /contrast/);
  assert.equal(f.message, undefined);
});

test('A4: focus-appearance liczy qualifyingAA wobec progu efektywnego', () => {
  const run = (qualifyingAA) => classify({ modes: { contrast: 'off', appearance: 'fail' }, metrics: metrics({ qualifyingAA, qualifyingStrict: 0 }) });
  assert.deepEqual(verdicts(run(516)), [['focus-appearance', 'PASS', null]]);
  assert.deepEqual(verdicts(run(515)), [['focus-appearance', 'FAIL', null]]);
});

test('P15a: FAIL starej reguly: zadna nowa nie jest emitowana (takze przy clipped i shape-unknown)', () => {
  for (const modes of [BOTH, { contrast: 'report', appearance: 'report' }]) {
    assert.deepEqual(classify({ modes, diffPx: 3 }), []);
    assert.deepEqual(classify({ modes, diffPx: 0, geometry: geometry({ outlineOffset: 20, getClientRects: 3 }), metrics: null }), []);
  }
});

test('tryby off: nic', () => {
  assert.deepEqual(classify({ modes: { contrast: 'off', appearance: 'off' } }), []);
});

test('A3: brak zrzutu, rozne rozmiary albo wyjscie poza klip to UNVERIFIED clipped dla obu regul', () => {
  for (const over of [{ diffPx: null, metrics: null }, { metrics: null }, { geometry: geometry({ outlineOffset: 20 }) }]) {
    assert.deepEqual(verdicts(classify(over)), [['focus-contrast', 'UNVERIFIED', 'clipped'], ['focus-appearance', 'UNVERIFIED', 'clipped']]);
  }
});

test('A4: shape-unknown dotyczy tylko focus-appearance', () => {
  assert.deepEqual(verdicts(classify({ geometry: geometry({ getClientRects: 2 }), metrics: metrics({ requiredArea: null, threshold: null }) })),
    [['focus-contrast', 'PASS', null], ['focus-appearance', 'UNVERIFIED', 'shape-unknown']]);
});

test('P15b/P23: report zamienia FAIL i UNVERIFIED na INFO z ta sama przyczyna, PASS zostaje', () => {
  const modes = { contrast: 'report', appearance: 'report' };
  assert.deepEqual(verdicts(classify({ modes })), [['focus-contrast', 'PASS', null], ['focus-appearance', 'PASS', null]]);
  const failed = classify({ modes, metrics: metrics({ qualifyingStrict: 0, qualifyingAA: 0 }) });
  assert.deepEqual(verdicts(failed), [['focus-contrast', 'INFO', null], ['focus-appearance', 'INFO', null]]);
  assert.ok(failed.every((f) => f.note && f.message === undefined && f.measured && Number.isFinite(f.required)));
  const cut = classify({ modes, geometry: geometry({ outlineOffset: 20 }) });
  assert.deepEqual(verdicts(cut), [['focus-contrast', 'INFO', 'clipped'], ['focus-appearance', 'INFO', 'clipped']]);
  assert.ok(cut.every((f) => f.note && f.message === undefined));
  assert.deepEqual(verdicts(classify({ modes, geometry: geometry({ borderRadius: null }), metrics: metrics({ requiredArea: null, threshold: null }) })).at(-1),
    ['focus-appearance', 'INFO', 'shape-unknown']);
});

// ---------- page contract (P17, P20, P26) ----------

function activeInfo({ style = {}, rect = { left: 10, top: 20, width: 100, height: 40 }, rects = 1 } = {}) {
  const el = {
    tagName: 'A', nodeType: 1, id: 'go', previousElementSibling: null, parentElement: null, isConnected: true,
    getRootNode: () => document,
    getBoundingClientRect: () => ({ ...rect, right: rect.left + rect.width, bottom: rect.top + rect.height }),
    getClientRects: () => ({ length: rects }),
    getAttribute: () => null, closest: () => null, matches: () => true,
  };
  el.ownerDocument = { defaultView: null };
  const document = { activeElement: el, body: {}, documentElement: {}, querySelectorAll: () => [el] };
  const computed = { outlineStyle: 'solid', outlineWidth: '3px', outlineOffset: '-2px', borderTopLeftRadius: '0px', borderTopRightRadius: '0px', borderBottomRightRadius: '0px', borderBottomLeftRadius: '0px', ...style };
  const sandbox = { document, getComputedStyle: () => computed, scrollX: 0, scrollY: 0 };
  const fn = vm.runInNewContext(`(${PAGE_SCRIPT})`, sandbox);
  return fn({ op: 'active' }).then((v) => JSON.parse(JSON.stringify(v)));
}
const corners = (tl, tr = tl, br = tl, bl = tl) => ({ borderTopLeftRadius: tl, borderTopRightRadius: tr, borderBottomRightRadius: br, borderBottomLeftRadius: bl });
const SQUARE = { left: 0, top: 0, width: 24, height: 24 };

test('kontrakt strony: outlineOffset, outlineWidth, borderRadius i getClientRects to liczby', async () => {
  const r = await activeInfo({ style: corners('12px'), rects: 2 });
  assert.deepEqual([r.outlineOffset, r.outlineWidth, r.borderRadius, r.getClientRects], [-2, 3, 12, 2]);
});

test('kontrakt strony: najmniejszy z czterech rogow po przycieciu do min(w,h)/2, procenty wzgledem wymiarow', async () => {
  const radius = async (style, rect) => (await activeInfo({ style, ...(rect ? { rect } : {}) })).borderRadius;
  assert.equal(await radius(corners('50px')), 20);
  assert.equal(await radius(corners('9999px')), 20);
  assert.equal(await radius(corners('20px', '12px', '16px', '14px')), 12);
  assert.equal(await radius(corners('20px', '0px')), 0);
  assert.equal(await radius(corners('50%'), SQUARE), 12);
  assert.equal(await radius(corners('25%'), SQUARE), 6);
  assert.equal(await radius(corners('10px 10px')), 10);
});

test('kontrakt strony: rog eliptyczny albo nieczytelny to borderRadius null', async () => {
  for (const style of [corners('50%'), corners('10px 20px'), corners('12px', '10% 10%'), corners('calc(50% - 2px)'), corners(''), corners('-4px'), corners('1px 1px 1px')]) {
    assert.equal((await activeInfo({ style })).borderRadius, null, JSON.stringify(style));
  }
});

test('kontrakt strony: nieczytelny outline to null, nie tekst ze strony', async () => {
  const r = await activeInfo({ style: { outlineWidth: 'medium', outlineOffset: undefined } });
  assert.deepEqual([r.outlineOffset, r.outlineWidth], [null, null]);
});

// ---------- runner with a scripted page ----------

// Opaque flat images, as screenshots are.
const FakePNG = { sync: { read: (buf) => {
  const { w, h, fill } = JSON.parse(buf.toString());
  const data = new Uint8Array(w * h * 4).fill(fill);
  for (let i = 3; i < data.length; i += 4) data[i] = 255;
  return { width: w, height: h, data };
} } };
// With includeAA the stand-in marks differences the same way; the calls are recorded to show which flaggers ran.
function fakeImaging(seen) {
  return {
    PNG: FakePNG,
    pixelmatch: (a, b, out, w, h, opts) => {
      seen.push(opts.includeAA);
      for (let i = 0; i < a.length; i += 4) if (a[i] !== b[i]) { out[i] = 255; out[i + 1] = 0; out[i + 2] = 0; out[i + 3] = 255; }
    },
  };
}

// lit: grey level of the focused screenshot (255 = no indicator, 204 = pale, 0 = strong).
const stop = (key, lit, over = {}) => ({ kind: 'element', key, id: key, idUnique: true, chain: [], tabindex: null, box: { x: 100, y: 100, width: 30, height: 30 }, inDocument: true, inModal: false, focusVisible: true, signals: {}, ...info({ vr: { x: 100, y: 100, width: 30, height: 30 } }), lit, ...over });
const STOPS = [
  stop('strong', 0),
  stop('pale', 204),
  stop('none', 255),
  stop('wrapped', 0, { getClientRects: 2 }),
  stop('far', 0, { outlineOffset: 10 }),
  stop('off', 0, { vr: { x: 3000, y: 100, width: 30, height: 30 } }),
];

async function audit(focus, { stops = STOPS, exceptions = { version: 1 } } = {}) {
  const cfg = { version: 1, pages: [{ id: 'p', url: 'a.html', checks: ['focus'], assert: { selector: 'main' } }], ...(focus ? { focus } : {}) };
  const v = validateA11yConfig(cfg, { configDir: CONFIG_DIR });
  assert.ok(v.ok, v.errors.join('; '));
  const state = { step: 0, current: null, blurred: false, ops: [], shots: 0 };
  const ops = {
    validate: (a) => a.selectors.map(() => true),
    resetFocus: () => true,
    active: () => { state.current = stops[state.step++] || { kind: 'body' }; return state.current; },
    blur: () => { state.blurred = true; return true; },
    refocus: () => { state.blurred = false; return true; },
  };
  const page = {
    on() {},
    url: () => 'x',
    async goto() {},
    async evaluate(fn, args) { state.ops.push(args.op); assert.ok(ops[args.op], `unexpected page operation ${args.op}`); return ops[args.op](args); },
    async waitForFunction() {},
    keyboard: { async press() {} },
    async screenshot({ clip }) { state.shots += 1; return Buffer.from(JSON.stringify({ w: clip.width, h: clip.height, fill: state.blurred ? 255 : state.current.lit })); },
    async close() {},
  };
  const context = { setDefaultTimeout() {}, async route() {}, async newPage() { return page; }, pages: () => [page], async close() {} };
  const seen = [];
  const runner = createA11yRunner({ browser: { newContext: async () => context }, imaging: fakeImaging(seen), config: v.config, configDir: CONFIG_DIR });
  const result = await runner.auditPage(v.config.pages[0]);
  await runner.close();
  const parsed = parseExceptionsFile(exceptions, { now: NOW });
  assert.ok(parsed.ok, parsed.errors.join('; '));
  const report = assembleReport({ pages: [result], config: { ...v.config, pages: v.config.pages }, exceptionsData: parsed.data, now: NOW, digest: 'x', masker: null });
  const of = (rule) => report.pages[0].findings.filter((f) => f.rule === rule).map((f) => [f.selector, f.verdict, f.reason ?? null]);
  return { report, of, seen, state };
}

test('runner K1: bez kluczy i z off: te same wyniki, bez nowych regul i bez drugiego flaggera', async () => {
  const none = await audit();
  const off = await audit({ contrast: 'off', appearance: 'off' });
  for (const run of [none, off]) {
    assert.ok(!run.report.pages[0].findings.some((f) => NEW_RULES.includes(f.rule)));
    assert.ok(run.seen.every((aa) => aa === false));
  }
  assert.deepEqual(off.report.pages, none.report.pages);
  assert.deepEqual(none.state.ops, off.state.ops);
});

test('runner fail: werdykty nowych regul, brak dublowania FAIL, kod 1; te same operacje strony i zrzuty co przy off', async () => {
  const off = await audit();
  const run = await audit({ contrast: 'fail', appearance: 'fail' });
  assert.deepEqual(run.of('focus-keyboard-indicator'), off.of('focus-keyboard-indicator'));
  assert.deepEqual(run.of('focus-contrast'), [
    ['#strong', 'PASS', null], ['#pale', 'FAIL', null], ['#wrapped', 'PASS', null], ['#far', 'UNVERIFIED', 'clipped'], ['#off', 'UNVERIFIED', 'clipped'],
  ]);
  assert.deepEqual(run.of('focus-appearance'), [
    ['#strong', 'PASS', null], ['#pale', 'FAIL', null], ['#wrapped', 'UNVERIFIED', 'shape-unknown'], ['#far', 'UNVERIFIED', 'clipped'], ['#off', 'UNVERIFIED', 'clipped'],
  ]);
  assert.equal(run.report.exit, 1);
  assert.deepEqual(run.state.ops, off.state.ops);
  assert.equal(run.state.shots, off.state.shots);
  assert.ok(run.seen.includes(true), 'the antialiasing flagger ran');
  const byKey = Object.fromEntries(run.report.pages[0].findings.map((f) => [f.key, f]));
  const a = byKey['focus-appearance|p|#strong'];
  assert.equal(a.basis, 'wcag-aaa');
  assert.deepEqual([a.measured.requiredArea, a.measured.threshold, a.required], [240, 212, 212]);
  assert.equal(a.measured.qualifyingAA, 46 * 46);
  assert.equal(byKey['focus-contrast|p|#strong'].basis, 'wcag');
});

test('runner: tryby sa niezalezne', async () => {
  const run = await audit({ contrast: 'fail' });
  assert.equal(run.of('focus-appearance').length, 0);
  assert.equal(run.of('focus-contrast').length, 5);
  const other = await audit({ appearance: 'report' });
  assert.equal(other.of('focus-contrast').length, 0);
  assert.equal(other.of('focus-appearance').length, 5);
});

test('runner fail: same UNVERIFIED nowych regul daja kod 3', async () => {
  const stops = [stop('wrapped', 0, { getClientRects: 2 })];
  assert.equal((await audit(undefined, { stops })).report.exit, 0);
  assert.equal((await audit({ appearance: 'fail' }, { stops })).report.exit, 3);
});

test('runner report: nigdy nie zmienia kodu wyjscia, INFO w sekcji Informational', async () => {
  const report = { contrast: 'report', appearance: 'report' };
  for (const stops of [STOPS, STOPS.filter((s) => s.key !== 'none' && s.key !== 'off'), [STOPS[0]], [STOPS[1]], [STOPS[2]]]) {
    const off = await audit(undefined, { stops });
    const run = await audit(report, { stops });
    assert.deepEqual([run.report.result, run.report.exit], [off.report.result, off.report.exit], stops.map((s) => s.key).join());
    assert.ok(run.report.pages[0].findings.filter((f) => NEW_RULES.includes(f.rule)).every((f) => f.verdict === 'PASS' || f.verdict === 'INFO'));
  }
  const run = await audit(report);
  const md = renderMarkdown(run.report);
  assert.match(md, /## Informational/);
  assert.match(md, /\[focus-contrast\] page p: `#pale`.*contrast/);
  assert.match(md, /\[focus-appearance\] page p: `#wrapped` \(shape-unknown\)/);
});

test('K3: wyjatek zdejmuje FAIL, potwierdzenie zdejmuje clipped i shape-unknown', async () => {
  const confirm = (rule, selector) => ({ rule, page: 'p', selector, result: 'PASS', checkedBy: 'tester', reason: 'measured by hand', expires: '2027-06-30' });
  const stops = [stop('pale', 204), stop('wrapped', 0, { getClientRects: 2 }), stop('far', 0, { outlineOffset: 10 })];
  const run = await audit({ contrast: 'fail', appearance: 'fail' }, {
    stops,
    exceptions: {
      version: 1,
      exceptions: [{ rule: 'focus-contrast', page: 'p', selector: '#pale', reason: 'accepted' }, { key: 'focus-appearance|p|#pale', reason: 'accepted' }],
      confirmations: [confirm('focus-appearance', '#wrapped'), confirm('focus-contrast', '#far'), confirm('focus-appearance', '#far')],
    },
  });
  assert.deepEqual(run.of('focus-contrast'), [['#pale', 'EXCEPTED', null], ['#wrapped', 'PASS', null], ['#far', 'CONFIRMED', 'clipped']]);
  assert.deepEqual(run.of('focus-appearance'), [['#pale', 'EXCEPTED', null], ['#wrapped', 'CONFIRMED', 'shape-unknown'], ['#far', 'CONFIRMED', 'clipped']]);
  assert.equal(run.report.exit, 0);
});

test('P32 (zastepuje wynik P29 dla progu 0): prog efektywny <= 0 to UNVERIFIED shape-unknown, nigdy PASS, takze bez zadnego piksela', () => {
  const appearance = (qualifyingAA, over = {}) => classify({ modes: { contrast: 'off', appearance: 'fail' }, metrics: metrics({ qualifyingAA, requiredArea: 8, threshold: 0 }), ...over });
  for (const n of [0, 1, 9999]) assert.deepEqual(verdicts(appearance(n)), [['focus-appearance', 'UNVERIFIED', 'shape-unknown']], `qualifyingAA ${n}`);
  assert.deepEqual(verdicts(appearance(1, { modes: { contrast: 'off', appearance: 'report' } })), [['focus-appearance', 'INFO', 'shape-unknown']]);
});

test('P29: prog ulamkowy ponizej 1 bez ani jednego kwalifikujacego piksela nie daje PASS', () => {
  const appearance = (qualifyingAA) => classify({ modes: { contrast: 'off', appearance: 'fail' }, metrics: metrics({ qualifyingAA, requiredArea: 18, threshold: 0.5 }) });
  assert.deepEqual(verdicts(appearance(0)), [['focus-appearance', 'FAIL', null]]);
  assert.deepEqual(verdicts(appearance(1)), [['focus-appearance', 'PASS', null]]);
});

test('P32: bok prostokata <= 1 px to shape-unknown tylko dla focus-appearance, takze przy dodatnim progu; bok ponad 1 px jest mierzony', () => {
  const run = (width, height) => classify({ geometry: geometry({ vr: { x: 100, y: 100, width, height } }), metrics: metrics({ qualifyingAA: 9999 }) });
  for (const [w, h] of [[1, 1], [1, 30], [30, 1], [0.5, 30]]) {
    const [c, a] = run(w, h);
    assert.deepEqual(verdicts([c, a]), [['focus-contrast', 'PASS', null], ['focus-appearance', 'UNVERIFIED', 'shape-unknown']], `${w}x${h}`);
    assert.ok(a.note && a.measured === undefined && a.required === undefined);
  }
  assert.deepEqual(verdicts(run(1.5, 30)).at(-1), ['focus-appearance', 'PASS', null]);
});

test('P30: outline-style none nie liczy sie do clipped, nawet z niezerowa szerokoscia i offsetem', () => {
  assert.equal(geometry({ outlineOffset: 20, outlineWidth: 3, signals: { outlineStyle: 'none' } }).clipped, false);
  assert.equal(geometry({ outlineOffset: 20, outlineWidth: 3, signals: { outlineStyle: 'solid' } }).clipped, true);
  assert.equal(geometry({ outlineOffset: 20, outlineWidth: 3 }).clipped, true);
});

test('P32 runner: kontrolka 1x1 z widocznym wskaznikiem obok: focus-contrast PASS, focus-appearance shape-unknown (fail: kod 3, report: INFO)', async () => {
  // the scripted screenshot changes the whole 17 x 17 clip: pixels beside the 1 x 1 element
  const stops = [stop('sr', 0, { vr: { x: 100, y: 100, width: 1, height: 1 } })];
  const run = await audit({ contrast: 'fail', appearance: 'fail' }, { stops });
  assert.deepEqual(run.of('focus-keyboard-indicator'), [['#sr', 'PASS', null]]);
  assert.deepEqual(run.of('focus-contrast'), [['#sr', 'PASS', null]]);
  assert.deepEqual(run.of('focus-appearance'), [['#sr', 'UNVERIFIED', 'shape-unknown']]);
  assert.equal(run.report.exit, 3);
  const soft = await audit({ appearance: 'report' }, { stops });
  assert.deepEqual(soft.of('focus-appearance'), [['#sr', 'INFO', 'shape-unknown']]);
  assert.equal(soft.report.exit, 0);
});
