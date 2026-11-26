// State zero of the accessibility audit (CORE-1150-T2B, K1 level a): what a configuration WITHOUT new keys gives today.
// No browser, no clock, no network. Golden files: library/skills/ui-verify/fixtures/a11y/state-zero/.
// A difference is a change of behaviour for existing users, not a reason to regenerate. Regeneration is explicit only:
//   A11Y_BASELINE_UPDATE=1 node --test tests/ui_verify/a11y-baseline.test.mjs
// A missing golden file is a failure.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { assembleReport } from '../../library/skills/ui-verify/scripts/a11y.mjs';
import { configDigest, validateA11yConfig } from '../../library/skills/ui-verify/scripts/lib/a11y-config.mjs';
import { DEFAULT_THRESHOLDS, LARGE_BOLD_PX, LARGE_TEXT_PX, SOLID_SPREAD } from '../../library/skills/ui-verify/scripts/lib/a11y-contrast.mjs';
import { findingKey, parseExceptionsFile } from '../../library/skills/ui-verify/scripts/lib/a11y-exceptions.mjs';
import { DEFAULT_MIN_DIFF_PX } from '../../library/skills/ui-verify/scripts/lib/a11y-focus.mjs';
import { FOCUS_PAD_PX, PIXEL_THRESHOLD, createA11yRunner } from '../../library/skills/ui-verify/scripts/lib/a11y-page.mjs';
import { renderMarkdown } from '../../library/skills/ui-verify/scripts/lib/a11y-report.mjs';
import { CONFIRMABLE_REASONS, EXEMPT_REASONS, LIMITS, LIMIT_REASONS } from '../../library/skills/ui-verify/scripts/lib/a11y-schema.mjs';
import { MIN_TARGET_PX } from '../../library/skills/ui-verify/scripts/lib/a11y-target.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.resolve(HERE, '..', '..', 'library', 'skills', 'ui-verify', 'fixtures', 'a11y');
const GOLDEN = path.join(FIXTURES, 'state-zero');
const UPDATE = process.env.A11Y_BASELINE_UPDATE === '1';
const NOW = '2026-11-26';
const DIGEST = '000000000000';

// ---------- golden files ----------

// The configuration directory (native path or file URL) becomes "<config-dir>" with forward slashes.
function portable(value) {
  if (typeof value === 'string') {
    let s = value;
    for (const form of [pathToFileURL(FIXTURES).href, FIXTURES]) s = s.split(form).join('<config-dir>');
    return s.startsWith('<config-dir>') ? s.replace(/\\/g, '/') : s;
  }
  if (Array.isArray(value)) return value.map(portable);
  if (value !== null && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, portable(v)]));
  return value;
}

const plain = (v) => portable(JSON.parse(JSON.stringify(v)));
const lf = (s) => s.replace(/\r\n/g, '\n');

function golden(name, actual) {
  const file = path.join(GOLDEN, name);
  const isJson = name.endsWith('.json');
  const text = isJson ? `${JSON.stringify(actual, null, 2)}\n` : lf(actual);
  if (UPDATE) {
    fs.mkdirSync(GOLDEN, { recursive: true });
    fs.writeFileSync(file, text, 'utf8');
  }
  assert.ok(fs.existsSync(file), `golden file state-zero/${name} is missing (it is never created without A11Y_BASELINE_UPDATE=1)`);
  const saved = lf(fs.readFileSync(file, 'utf8'));
  if (isJson) assert.deepStrictEqual(actual, JSON.parse(saved), `state-zero/${name}: behaviour differs from the baseline`);
  else assert.equal(text, saved, `state-zero/${name}: rendered text differs from the baseline`);
}

// ---------- 1. configurations ----------

// The configurations that existed at the baseline. Files added later may use new keys and are not listed here.
const BASELINE_CONFIGS = [
  'clean', 'clip-text', 'console', 'console-error', 'console-only', 'contrast', 'defects', 'empty', 'empty-targets', 'exempt',
  'fixed-sticky', 'frames-shadow', 'huge', 'lazy', 'login', 'modern-colors', 'mouse', 'multi', 'opacity-group', 'overlay',
  'rtl', 'tall', 'too-tall',
];

// Every key that exists today, partially filled sections included: nothing new may appear in the normalised result.
const onePage = (over = {}) => [{ id: 'p', url: 'clean.html', assert: { selector: 'main' }, ...over }];
const SYNTHETIC = {
  'focus-empty': { version: 1, pages: onePage(), focus: {} },
  'focus-min-diff-only': { version: 1, pages: onePage(), focus: { minDiffPx: 9 } },
  'focus-mouse-only': { version: 1, pages: onePage(), focus: { mouse: { mode: 'forbid', selectors: ['#a'] } } },
  'thresholds-empty': { version: 1, pages: onePage(), thresholds: {} },
  'thresholds-lowered': { version: 1, pages: onePage(), thresholds: { normal: 3, reason: 'brand palette, accepted by the owner' } },
  'thresholds-raised': { version: 1, pages: onePage(), thresholds: { normal: 7, large: 4.5 } },
  'all-keys': {
    version: 1,
    viewport: { width: 800, height: 600 },
    secretEnv: ['UI_VERIFY_X'],
    pages: onePage({ waitFor: 'main', checks: ['console', 'focus', 'console'], assert: { selector: 'main', text: 'Hi' } }),
    thresholds: { normal: 4.5, large: 3 },
    focus: { minDiffPx: 4, mouse: null },
    tab: { maxStops: 20, positiveTabindex: 'warn' },
    order: { mode: 'fail', tolerancePx: 0 },
  },
};

function normalised(raw, label) {
  const r = validateA11yConfig(raw, { configDir: FIXTURES, env: {} });
  assert.ok(r.ok, `${label}: ${r.errors.join('; ')}`);
  return { digest: configDigest(r.config, FIXTURES), config: plain(r.config) };
}

test('state zero: normalised configuration and digest of every baseline fixture configuration', () => {
  const out = {};
  for (const name of BASELINE_CONFIGS) {
    out[name] = normalised(JSON.parse(fs.readFileSync(path.join(FIXTURES, `${name}.config.json`), 'utf8')), name);
  }
  golden('configs.json', out);
});

test('state zero: normalised configuration and digest when only today\'s keys are given', () => {
  golden('configs-synthetic.json', Object.fromEntries(Object.entries(SYNTHETIC).map(([name, raw]) => [name, normalised(raw, name)])));
});

test('state zero: constants that decide the default result', () => {
  assert.deepStrictEqual({ ...DEFAULT_THRESHOLDS }, { normal: 4.5, large: 3 });
  assert.deepStrictEqual(
    [DEFAULT_MIN_DIFF_PX, FOCUS_PAD_PX, PIXEL_THRESHOLD, MIN_TARGET_PX, LARGE_TEXT_PX, LARGE_BOLD_PX, SOLID_SPREAD],
    [4, 8, 0.1, 24, 24, 56 / 3, 1],
  );
  for (const [key, value] of Object.entries({ contrastElements: 5000, targets: 2000, tabStops: 500, messages: 200, textChars: 80, selectorChars: 400, messageChars: 300 })) {
    assert.equal(LIMITS[key], value, `LIMITS.${key}`);
  }
  // subsets only: new reasons may be added, but an existing one must not change sides
  for (const r of ['opacity-group-with-paint', 'background-clip-text', 'color-unconvertible', 'occluded', 'clipped', 'filter-or-blend', 'inline-important-color', 'autofill', 'media-background']) {
    assert.ok(CONFIRMABLE_REASONS.includes(r) && !LIMIT_REASONS.includes(r), `confirmable: ${r}`);
  }
  for (const r of ['page-too-tall', 'layout-shift', 'tab-frame', 'mouse-capture-failed', 'truncated', 'sequence-truncated', 'no-assertion', 'no-checks', 'no-samples']) {
    assert.ok(LIMIT_REASONS.includes(r) && !CONFIRMABLE_REASONS.includes(r), `limit: ${r}`);
  }
  for (const r of ['not-rendered', 'inactive']) assert.ok(EXEMPT_REASONS.includes(r), `exempt: ${r}`);
});

// ---------- 2. report from fixed findings ----------

const colour = (v) => ({ r: v, g: v, b: v });
const contrast = (selector, verdict, extra = {}) => ({
  rule: 'contrast', selector, verdict,
  ...(verdict === 'PASS' || verdict === 'FAIL'
    ? { measured: { ratio: verdict === 'PASS' ? 4.61 : 2.17, fontSizePx: 16, bold: false, large: false, backgroundKind: 'solid', foreground: { ...colour(117), a: 1 }, effectiveBackground: colour(255) }, required: 4.5 }
    : {}),
  ...extra,
});
const indicator = (selector, verdict, diffPx) => ({ rule: 'focus-keyboard-indicator', selector, verdict, measured: { diffPx, focusVisible: true, signals: { outlineStyle: 'solid' } }, required: 4 });
const unverified = (rule, reason, extra = {}) => ({ rule, verdict: 'UNVERIFIED', reason, ...extra });

// Every rule, every verdict, selector-less findings (hashed keys) and the fields the renderer prints.
const MAIN_FINDINGS = [
  contrast('#ok', 'PASS', { note: 'sampled' }),
  contrast('#bad', 'FAIL'),
  contrast('#brand', 'FAIL'),
  contrast('#old', 'FAIL'),
  contrast('#a | b', 'FAIL'),
  contrast('#hero', 'UNVERIFIED', { reason: 'occluded' }),
  contrast('#stale', 'UNVERIFIED', { reason: 'media-background' }),
  contrast('#hidden', 'EXEMPT', { reason: 'not-rendered' }),
  contrast('#off', 'EXEMPT', { reason: 'inactive' }),
  unverified('contrast', 'page-too-tall', { hashBasis: 'page-too-tall', message: 'page is too large to capture; contrast not measured' }),
  unverified('contrast', 'layout-shift', { hashBasis: 'layout-shift', message: 'the page layout changed while it was measured; contrast not trusted' }),
  unverified('contrast', 'truncated', { hashBasis: 'contrast-truncated', message: 'more than 5000 text elements; the rest was not measured' }),
  { rule: 'target-size', selector: '#big', verdict: 'PASS', measured: { widthPx: 40, heightPx: 40 } },
  { rule: 'target-size', selector: '#spaced', verdict: 'PASS', measured: { widthPx: 16, heightPx: 16 }, exception: 'spacing', note: 'spacing' },
  { rule: 'target-size', selector: '#tiny', verdict: 'FAIL', measured: { widthPx: 16, heightPx: 16 }, required: 24 },
  { rule: 'target-size', selector: '#native', verdict: 'EXEMPT', reason: 'user-agent', measured: { widthPx: 13, heightPx: 13 } },
  indicator('#ring', 'PASS', 120),
  indicator('#noring', 'FAIL', 0),
  unverified('focus-keyboard-indicator', 'clipped', { selector: '#edge', message: 'focus area could not be captured' }),
  { rule: 'focus-mouse-ring', selector: '#nav', verdict: 'INFO', measured: { diffPx: 40, visible: true }, required: 4, note: 'ring visible after click' },
  unverified('focus-mouse-ring', 'mouse-capture-failed', { selector: '#go', message: 'the page could not be captured after the click (navigation blocked or failed)' }),
  { rule: 'tab-positive-tabindex', selector: '#jump', verdict: 'FAIL', basis: 'best-practice', message: 'tabindex="3" overrides the natural order', measured: { tabindex: 3 } },
  { rule: 'tab-unreachable', selector: '#lost', verdict: 'FAIL', message: 'target is not reached by the Tab key', measured: { tabindex: null } },
  unverified('tab-unreachable', 'sequence-truncated', { hashBasis: 'sequence-truncated', message: 'Tab sequence reached the limit of 500 stops; reachability not verified' }),
  { rule: 'tab-trap', verdict: 'FAIL', hashBasis: '#a>#b', message: 'focus cycles among 2 element(s)', measured: { cycleLength: 2, unreached: 1 } },
  { rule: 'tab-order-visual', verdict: 'FAIL', hashBasis: '#x>#y', message: 'focus moves visually backwards (ltr) from #x to #y', measured: { from: '#x', to: '#y' } },
  { rule: 'console', verdict: 'FAIL', basis: 'best-practice', message: 'console.error: fixture failure number 12345 at deadbeefcafe', measured: { count: 2 } },
  { rule: 'console', verdict: 'FAIL', basis: 'best-practice', message: 'http 404: GET http://localhost/missing-resource' },
  { rule: 'console', verdict: 'INFO', basis: 'best-practice', message: 'console.warning: deprecated' },
  unverified('console', 'truncated', { hashBasis: 'console-truncated', message: 'more than 200 different messages; the rest was dropped' }),
];

const EXCEPTIONS = {
  version: 1,
  exceptions: [
    { rule: 'contrast', page: 'main', selector: '#brand', reason: 'logo type', expires: '2027-06-30' },
    { rule: 'contrast', page: 'main', selector: '#old', reason: 'expired entry', expires: '2026-01-01' },
    { rule: 'console', page: 'main', messageContains: 'fixture failure', reason: 'known fixture error', expires: '2027-06-30' },
    { key: 'target-size|main|#tiny', reason: 'matched by key' },
    { rule: 'focus-keyboard-indicator', selector: '#noring', reason: 'rule and selector without page' },
    { rule: 'contrast', page: 'main', selector: '#nothing', reason: 'matches nothing' },
    { rule: 'tab-unreachable', page: 'main', selector: '#ok', reason: 'matches nothing either' },
  ],
  confirmations: [
    { rule: 'contrast', page: 'main', selector: '#hero', result: 'PASS', checkedBy: 'tester', reason: 'measured by hand', expires: '2027-06-30' },
    { rule: 'contrast', page: 'main', selector: '#stale', result: 'PASS', checkedBy: 'tester', reason: 'old confirmation', expires: '2026-01-01' },
    { rule: 'contrast', page: 'main', selector: '#bad', result: 'PASS', checkedBy: 'tester', reason: 'a confirmation never settles a FAIL', expires: '2027-06-30' },
  ],
};
// An entry for a limit reason must not settle it.
const LIMIT_CONFIRMATION = { key: findingKey({ rule: 'contrast', page: 'main', hashBasis: 'page-too-tall' }), result: 'PASS', checkedBy: 'tester', reason: 'limit', expires: '2027-06-30' };

const pageOf = (id, findings, extra = {}) => ({ id, url: `file:///fixture/${id}.html?token=zzz`, assertion: 'PASS', checks: ['contrast', 'target-size', 'focus', 'tab-order', 'console'], findings, ...extra });

function assemble(pages, { exceptions = { version: 1 }, warnings = [], ...rest } = {}) {
  const parsed = parseExceptionsFile(exceptions, { now: NOW });
  assert.ok(parsed.ok, parsed.errors.join('; '));
  const config = { warnings, pages, thresholds: { normal: 4.5, large: 3 } };
  return assembleReport({ pages, config, exceptionsData: parsed.data, now: NOW, digest: DIGEST, masker: null, ...rest });
}

function snapshotReports(name, reports, expectedExits) {
  // literal expectations first: a regenerated golden file cannot silently change the verdict
  assert.deepStrictEqual(Object.fromEntries(Object.entries(reports).map(([k, r]) => [k, [r.result, r.exit]])), expectedExits);
  golden(`${name}.json`, Object.fromEntries(Object.entries(reports).map(([k, r]) => [k, plain(r)])));
  golden(`${name}.md`, Object.entries(reports).map(([k, r]) => `<!-- ${k} -->\n${renderMarkdown(plain(r))}`).join('\n'));
}

test('state zero: report, summary, exception keys and Markdown from fixed findings', () => {
  const pass = [contrast('#ok', 'PASS')];
  const reports = {
    everything: assemble([
      pageOf('main', MAIN_FINDINGS, { sequence: ['#ring', '#noring', 'body'] }),
      pageOf('asserted-fail', [], { assertion: 'FAIL' }),
      pageOf('no-assertion', pass, { assertion: 'NONE' }),
      pageOf('no-checks', [], { checks: [] }),
    ], {
      exceptions: { ...EXCEPTIONS, confirmations: [...EXCEPTIONS.confirmations, LIMIT_CONFIRMATION] },
      warnings: [{ code: 'threshold-lowered', level: 'warning', message: 'threshold lowered (normal=3): fixture' }],
      nowOverridden: NOW,
    }),
    unverified: assemble([pageOf('p', [...pass, contrast('#hero', 'UNVERIFIED', { reason: 'occluded' })])]),
    onlyNeutral: assemble([pageOf('p', [contrast('#hidden', 'EXEMPT', { reason: 'not-rendered' }), { rule: 'console', verdict: 'INFO', message: 'console.warning: x' }])]),
    passWithSettled: assemble([pageOf('main', [
      ...pass, contrast('#brand', 'FAIL'), contrast('#hero', 'UNVERIFIED', { reason: 'occluded' }),
      { rule: 'console', verdict: 'INFO', message: 'console.warning: x' },
    ])], { exceptions: { version: 1, exceptions: [EXCEPTIONS.exceptions[0]], confirmations: [EXCEPTIONS.confirmations[0]] } }),
    broadException: assemble([pageOf('a', [contrast('#brand', 'FAIL')]), pageOf('b', [contrast('#brand', 'FAIL')])], {
      exceptions: { version: 1, exceptions: [{ rule: 'contrast', selector: '#brand', reason: 'everywhere' }] },
    }),
    expectMismatch: assemble([pageOf('p', pass)], { expected: { elements: [{ selector: '#ok', verdict: 'FAIL' }] }, expectPage: 'p' }),
    expectAbsent: assemble([pageOf('p', pass)], { expected: { elements: [{ selector: '#gone', verdict: 'PASS' }] }, expectPage: 'p' }),
    expectMatch: assemble([pageOf('p', [contrast('#bad', 'FAIL')])], { expected: { elements: [{ selector: '#bad', verdict: 'FAIL', ratio: 2.17, required: 4.5 }] }, expectPage: 'p' }),
  };
  assert.deepStrictEqual(new Set(reports.everything.pages.flatMap((p) => p.findings.map((f) => f.verdict))), new Set(['PASS', 'FAIL', 'EXEMPT', 'EXCEPTED', 'CONFIRMED', 'UNVERIFIED', 'INFO']));
  assert.equal(Object.keys(reports.everything.summary.byRule).length, 11, 'all current rules are present');
  snapshotReports('report-findings', reports, {
    everything: ['FAIL', 1], unverified: ['UNVERIFIED', 3], onlyNeutral: ['UNVERIFIED', 3], passWithSettled: ['PASS', 0],
    broadException: ['PASS', 0], expectMismatch: ['FAIL', 1], expectAbsent: ['UNVERIFIED', 3], expectMatch: ['PASS', 0],
  });
});

// ---------- 3. the real orchestration with a scripted page (default checks, no new keys) ----------

// A fake PNG: { w, h, fill, right? }; with `right` the columns from x = 32 on have that value (a varying background).
const FakePNG = { sync: { read: (buf) => {
  const { w, h, fill, right } = JSON.parse(buf.toString());
  const data = new Uint8Array(w * h * 4).fill(fill);
  if (right !== undefined) for (let y = 0; y < h; y++) data.fill(right, (y * w + 32) * 4, (y * w + w) * 4);
  return { width: w, height: h, data };
} } };
const fakePixelmatch = (a, b, out) => {
  for (let i = 0; i < a.length; i += 4) if (a[i] !== b[i]) { out[i] = 255; out[i + 1] = 0; out[i + 2] = 0; out[i + 3] = 255; }
};

const textItem = (id, over = {}) => ({ kind: 'text', id, idUnique: true, chain: [], fontSizePx: 16, fontWeight: 400, color: { r: 0, g: 0, b: 0, a: 1 }, opacity: 1, hidden: false, inactive: false, flags: {}, rects: [[2, 2, 8, 4]], ...over });
const TEXTS = [
  textItem('t-pass'),
  textItem('t-fail', { color: { r: 176, g: 176, b: 176, a: 1 } }),
  textItem('t-large', { color: { r: 138, g: 138, b: 138, a: 1 }, fontSizePx: 24 }),
  textItem('t-varying', { rects: [[8, 8, 48, 8]], color: { r: 118, g: 118, b: 118, a: 1 } }),
  textItem('t-cut', { rectsCut: true }),
  textItem('t-placeholder', { kind: 'placeholder' }),
  textItem('t-hidden', { hidden: true }),
  textItem('t-transparent', { opacity: 0 }),
  textItem('t-inactive', { inactive: true }),
  ...Object.entries({ filter: true, clipText: true, group: true, media: true, important: true, autofill: true }).map(([k, v]) => textItem(`t-${k}`, { flags: { [k]: v } })),
  textItem('t-color', { color: null }),
  textItem('t-occluded', { flags: { hit: 'occluded' } }),
  textItem('t-clipped', { flags: { hit: 'clipped' } }),
];

const targetItem = (key, box, over = {}) => ({ key, id: key, idUnique: true, chain: [], box, display: 'block', parentHasText: false, userAgent: false, labelBox: null, tabindex: null, disabled: false, inert: false, inClosedDetails: false, inModal: false, radioGroup: null, radioChecked: false, ...over });
const TARGETS = [
  targetItem('ring', { x: 10, y: 10, width: 100, height: 40 }),
  targetItem('noring', { x: 10, y: 100, width: 100, height: 40 }),
  targetItem('offscreen', { x: 10, y: 200, width: 100, height: 40 }),
  targetItem('edge', { x: 10, y: 300, width: 100, height: 40 }, { tabindex: 2 }),
  targetItem('tiny', { x: 400, y: 400, width: 16, height: 16 }),
  targetItem('tiny2', { x: 418, y: 400, width: 16, height: 16 }),
  targetItem('native', { x: 440, y: 400, width: 13, height: 13 }, { userAgent: true }),
];

// Focus steps. `edge` carries geometry that newer measurements may judge (outline far outside the 8 px padding, several
// client rectangles, element reaching beyond the viewport): without new keys it is measured exactly as today.
const stop = (key, vr, over = {}) => ({ kind: 'element', key, id: key, idUnique: true, chain: [], tabindex: null, box: { ...vr }, vr, inDocument: true, inModal: false, focusVisible: true, signals: { outlineStyle: 'solid' }, outlineOffset: 0, outlineWidth: 2, borderRadius: 0, getClientRects: 1, ...over });
const STOPS = [
  stop('ring', { x: 20, y: 200, width: 30, height: 30 }),
  stop('noring', { x: 20, y: 100, width: 30, height: 30 }, { focusVisible: false, signals: { outlineStyle: 'none' } }),
  stop('offscreen', { x: 3000, y: 120, width: 30, height: 30 }),
  stop('edge', { x: 1270, y: 790, width: 40, height: 30 }, { tabindex: 2, outlineOffset: 20, outlineWidth: 4, getClientRects: 3 }),
];
STOPS.push(STOPS[0]);

function scriptedRunner(config, { texts = TEXTS, targets = TARGETS, stops = STOPS, errors = true } = {}) {
  const state = { url: '', blurred: false, clicked: false, focus: null, step: 0, handlers: {} };
  const ops = {
    validate: (a) => a.selectors.map(() => true),
    settle: () => ({ width: 64, height: 64 }),
    collect: () => {
      if (errors) {
        const msg = (type, text) => ({ type: () => type, text: () => text, location: () => ({ url: '' }) });
        state.handlers.console(msg('error', 'boom'));
        state.handlers.console(msg('error', 'boom'));
        state.handlers.console(msg('warning', 'careful'));
      }
      return { frames: 1, closedShadowGuess: 1, openShadowRoots: 0, dir: 'ltr', targets: { truncated: false, items: targets }, contrast: { truncated: false, items: texts } };
    },
    recheck: () => ({ height: 64, shifted: 0 }),
    resetFocus: () => { state.step = 0; return true; },
    active: () => {
      const s = stops[state.step++] || { kind: 'body' };
      state.focus = s.key;
      return s;
    },
    blur: () => { state.blurred = true; return true; },
    refocus: () => { state.blurred = false; return true; },
    reveal: ({ selector }) => (selector === '#missing' ? { found: false } : { ...stops[0], found: true, covered: selector === '#covered' }),
  };
  const page = {
    on(ev, fn) { state.handlers[ev] = fn; },
    url: () => state.url,
    async goto(url) { state.url = url; state.clicked = false; state.blurred = false; },
    async evaluate(_fn, args) {
      assert.ok(ops[args.op], `the default configuration ran an unexpected page operation "${args.op}"`);
      return ops[args.op](args);
    },
    async waitForFunction() {},
    keyboard: { async press() {} },
    mouse: { async click() { state.clicked = true; }, async move() {} },
    async screenshot(opts) {
      if (opts.fullPage) return Buffer.from(JSON.stringify({ w: 64, h: 64, fill: 255, right: 200 }));
      const lit = state.clicked || (state.focus !== null && state.focus !== 'noring' && !state.blurred);
      return Buffer.from(JSON.stringify({ w: opts.clip.width, h: opts.clip.height, fill: lit ? 0 : 255 }));
    },
    async close() {},
  };
  const context = { setDefaultTimeout() {}, async route() {}, async newPage() { return page; }, pages: () => [page], async close() {} };
  return createA11yRunner({ browser: { newContext: async () => context }, imaging: { PNG: FakePNG, pixelmatch: fakePixelmatch }, config, configDir: FIXTURES });
}

async function audit(raw, script) {
  const v = validateA11yConfig({ version: 1, pages: [{ id: 'p', url: 'clean.html', assert: { selector: 'main' } }], ...raw }, { configDir: FIXTURES, env: {} });
  assert.ok(v.ok, v.errors.join('; '));
  const runner = scriptedRunner(v.config, script);
  const pages = [await runner.auditPage(v.config.pages[0])];
  await runner.close();
  return assemble(pages, { warnings: v.config.warnings });
}

test('state zero: runner with default checks on a scripted page gives the same findings, warnings, summary and exit', async () => {
  const clean = { texts: [TEXTS[0]], targets: [TARGETS[0]], stops: [STOPS[0], STOPS[0]], errors: false };
  const reports = {
    defaults: await audit({}),
    mouseAndOptions: await audit({
      focus: { minDiffPx: 100000, mouse: { mode: 'require', selectors: ['#ring', '#missing', '#covered'] } },
      thresholds: { normal: 3, reason: 'fixture' },
      order: { mode: 'fail' },
      tab: { positiveTabindex: 'warn' },
    }),
    clean: await audit({}, clean),
    empty: await audit({}, { texts: [], targets: [], stops: [], errors: false }),
  };
  snapshotReports('report-runner', reports, {
    defaults: ['FAIL', 1], mouseAndOptions: ['FAIL', 1], clean: ['PASS', 0], empty: ['UNVERIFIED', 3],
  });
});

test('state zero: golden files carry no machine-specific paths', () => {
  const names = fs.readdirSync(GOLDEN).sort();
  assert.deepStrictEqual(names, ['configs-synthetic.json', 'configs.json', 'report-findings.json', 'report-findings.md', 'report-runner.json', 'report-runner.md']);
  const repo = path.resolve(HERE, '..', '..');
  for (const name of names) {
    const text = fs.readFileSync(path.join(GOLDEN, name), 'utf8');
    for (const form of [repo, repo.replace(/\\/g, '/'), JSON.stringify(repo).slice(1, -1), pathToFileURL(repo).href]) {
      assert.ok(!text.includes(form), `${name} contains the repository path`);
    }
  }
});
