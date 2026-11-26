// Golden run (CORE-1150-T2B, K1 level b): every accessibility fixture that existed before the change, run in a real Chrome,
// gives the same rule, selector, verdict, reason, summary, result and exit code (measured values are not compared).
// The golden file is recorded ONLY from the scripts of commit 1d73aa92, never from the current tree and never by hand:
//   mkdir -p <empty dir> && git archive 1d73aa92 library/skills/ui-verify | tar -x -C <empty dir>
//   A11Y_GOLDEN_UPDATE=1 A11Y_GOLDEN_SKILL_DIR=<empty dir>/library/skills/ui-verify UI_VERIFY_TEST_CACHE=<cache> \
//     node --test tests/ui_verify/a11y-golden-e2e.test.mjs
// A11Y_GOLDEN_SKILL_DIR alone (without UPDATE) compares those scripts with the golden file (stability check).
// Without UI_VERIFY_TEST_CACHE or Chrome the browser tests are skipped; with them a missing golden file is a failure.
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CURRENT_SKILL = path.resolve(HERE, '..', '..', 'library', 'skills', 'ui-verify');
const GOLDEN = path.join(CURRENT_SKILL, 'fixtures', 'a11y', 'golden-e2e', 'state-zero-e2e.json');
const BASELINE_COMMIT = '1d73aa92';
// sha256 over "<path below scripts/>\0<content with LF>\0" of every .mjs file of library/skills/ui-verify/scripts at 1d73aa92, sorted by path.
const BASELINE_SCRIPTS_SHA256 = '0fb519e700379db3caa2e237d1249d4c9d683bfed1bdea713ff6c1befc591fad';
const NOW = '2026-11-26';

const UPDATE = process.env.A11Y_GOLDEN_UPDATE === '1';
const OVERRIDE = process.env.A11Y_GOLDEN_SKILL_DIR || null;
const SKILL = OVERRIDE ? path.resolve(OVERRIDE) : CURRENT_SKILL;

// ---------- what is compared ----------

const CONFIGS = [
  'clean', 'clip-text', 'console', 'console-error', 'console-only', 'contrast', 'defects', 'empty', 'empty-targets', 'exempt',
  'fixed-sticky', 'frames-shadow', 'huge', 'lazy', 'login', 'modern-colors', 'mouse', 'multi', 'opacity-group', 'overlay',
  'rtl', 'tall', 'too-tall',
];
const RUNS = [
  ...CONFIGS.map((name) => ({ id: name, config: name, flags: [] })),
  ...['console-error', 'console-only', 'opacity-group', 'overlay'].map((name) => ({ id: `${name}+exceptions`, config: name, flags: ['--exceptions', 'exceptions.json'] })),
  { id: 'contrast+expect', config: 'contrast', flags: ['--expect', 'contrast/expected.json'] },
];

const byText = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

// The compared part of a report. Findings are a sorted multiset: console messages arrive in no fixed order.
export function project(report, exit) {
  return {
    exit,
    result: report.result,
    summary: report.summary,
    pages: report.pages.map((p) => ({
      id: p.id,
      assertion: p.assertion,
      findings: p.findings.map((f) => ({ rule: f.rule, selector: f.selector, verdict: f.verdict, reason: f.reason ?? null }))
        .sort((a, b) => byText(JSON.stringify(a), JSON.stringify(b))),
    })),
  };
}

// The one documented race of the fixtures: the click on mouse.html#nav starts a blocked navigation, and the browser gives
// either an INFO finding or UNVERIFIED (mouse-capture-failed or clipped); a11y-e2e.test.mjs accepts both. Only that
// finding, and the totals it moves, are folded into one value; anything else it could report stays visible.
export function foldRace(id, snapshot) {
  if (id !== 'mouse') return snapshot;
  const allowed = (f) => (f.verdict === 'INFO' && f.reason === null) || (f.verdict === 'UNVERIFIED' && ['mouse-capture-failed', 'clipped'].includes(f.reason));
  const racy = (f) => f.rule === 'focus-mouse-ring' && f.selector === '#nav' && allowed(f);
  const found = snapshot.pages.flatMap((p) => p.findings).filter(racy);
  if (found.length !== 1) return snapshot;
  const expectedExit = found[0].verdict === 'INFO' ? 0 : 3;
  const ring = { ...snapshot.summary.byRule['focus-mouse-ring'] };
  ring[found[0].verdict] -= 1;
  if (ring[found[0].verdict] === 0) delete ring[found[0].verdict];
  ring.race = 1;
  const consistent = snapshot.exit === expectedExit;
  return {
    ...snapshot,
    ...(consistent ? { exit: 'race', result: 'race' } : {}),
    summary: { ...snapshot.summary, byRule: { ...snapshot.summary.byRule, 'focus-mouse-ring': ring } },
    pages: snapshot.pages.map((p) => ({ ...p, findings: p.findings.map((f) => (racy(f) ? { ...f, verdict: 'race', reason: null } : f)).sort((a, b) => byText(JSON.stringify(a), JSON.stringify(b))) })),
  };
}

export function scriptsDigest(skillDir) {
  const root = path.join(skillDir, 'scripts');
  const files = fs.readdirSync(root, { recursive: true }).map(String).filter((f) => f.endsWith('.mjs'))
    .map((f) => f.split(path.sep).join('/')).sort(byText);
  const hash = crypto.createHash('sha256');
  for (const f of files) hash.update(`${f}\0${fs.readFileSync(path.join(root, f), 'utf8').replace(/\r\n/g, '\n')}\0`);
  return hash.digest('hex');
}

// Why the golden file must not be written from this setup; null when it may.
export function updateRefusal({ override, skillDir, currentDir }) {
  if (!override) return 'A11Y_GOLDEN_UPDATE=1 needs A11Y_GOLDEN_SKILL_DIR pointing to the scripts of the baseline commit';
  if (fs.realpathSync(skillDir) === fs.realpathSync(currentDir)) return 'the golden file is never recorded from the current tree';
  if (scriptsDigest(skillDir) !== BASELINE_SCRIPTS_SHA256) return `the scripts in A11Y_GOLDEN_SKILL_DIR are not those of commit ${BASELINE_COMMIT}`;
  return null;
}

// ---------- tests without a browser ----------

const REPORT = {
  result: 'FAIL',
  summary: { byRule: { contrast: { FAIL: 1, PASS: 1 } }, total: 2 },
  pages: [{ id: 'p', assertion: 'PASS', findings: [
    { rule: 'contrast', selector: '#b', verdict: 'FAIL', measured: { ratio: 2 } },
    { rule: 'contrast', selector: '#a', verdict: 'PASS', measured: { ratio: 5 } },
  ] }],
};
const changed = (edit) => { const copy = structuredClone(REPORT); edit(copy); return project(copy, 1); };

test('golden projection: measured values and finding order are ignored, verdict, reason, rule, selector, summary and exit are not', () => {
  const base = project(REPORT, 1);
  assert.deepEqual(changed((r) => { r.pages[0].findings[0].measured.ratio = 2.4; r.pages[0].findings.reverse(); }), base);
  for (const [label, edit] of [
    ['verdict', (r) => { r.pages[0].findings[0].verdict = 'PASS'; }],
    ['reason', (r) => { r.pages[0].findings[0].reason = 'occluded'; }],
    ['rule', (r) => { r.pages[0].findings[0].rule = 'target-size'; }],
    ['selector', (r) => { r.pages[0].findings[0].selector = '#c'; }],
    ['summary', (r) => { r.summary.total = 3; }],
    ['result', (r) => { r.result = 'PASS'; }],
    ['added finding', (r) => { r.pages[0].findings.push({ rule: 'focus-contrast', selector: '#a', verdict: 'INFO' }); }],
  ]) assert.notDeepStrictEqual(changed(edit), base, label);
  assert.notDeepStrictEqual(project(REPORT, 3), base, 'exit');
});

test('golden race fold: only the two documented outcomes of mouse#nav are equal; another verdict, selector, config or exit stays different', () => {
  const mouse = (verdict, reason, selector = '#nav') => ({
    result: verdict === 'INFO' ? 'PASS' : 'UNVERIFIED',
    summary: { byRule: { 'focus-mouse-ring': { [verdict]: 1 } }, total: 1 },
    pages: [{ id: 'mouse', assertion: 'PASS', findings: [{ rule: 'focus-mouse-ring', selector, verdict, ...(reason ? { reason } : {}) }] }],
  });
  const fold = (id, r, exit) => foldRace(id, project(r, exit));
  const info = fold('mouse', mouse('INFO'), 0);
  assert.deepEqual(fold('mouse', mouse('UNVERIFIED', 'clipped'), 3), info);
  assert.deepEqual(fold('mouse', mouse('UNVERIFIED', 'mouse-capture-failed'), 3), info);
  assert.notDeepStrictEqual(fold('mouse', mouse('FAIL'), 1), info);
  assert.notDeepStrictEqual(fold('mouse', mouse('UNVERIFIED', 'occluded'), 3), info);
  assert.notDeepStrictEqual(fold('mouse', mouse('INFO'), 1), info);
  assert.notDeepStrictEqual(fold('mouse', mouse('INFO', null, '#plain'), 0), fold('mouse', mouse('UNVERIFIED', 'clipped', '#plain'), 3));
  assert.notDeepStrictEqual(fold('multi', mouse('INFO'), 0), fold('multi', mouse('UNVERIFIED', 'clipped'), 3));
});

test('golden update is refused without an explicit script directory, from the current tree and from changed scripts', () => {
  const args = { skillDir: CURRENT_SKILL, currentDir: CURRENT_SKILL };
  assert.match(updateRefusal({ ...args, override: null }), /needs A11Y_GOLDEN_SKILL_DIR/);
  assert.match(updateRefusal({ ...args, override: CURRENT_SKILL }), /never recorded from the current tree/);
  const copy = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-a11y-golden-guard-'));
  try {
    fs.cpSync(path.join(CURRENT_SKILL, 'scripts'), path.join(copy, 'scripts'), { recursive: true });
    assert.match(updateRefusal({ override: copy, skillDir: copy, currentDir: CURRENT_SKILL }), /not those of commit 1d73aa92/);
  } finally {
    fs.rmSync(copy, { recursive: true, force: true });
  }
});

// ---------- the browser run ----------

function gate() {
  const root = process.env.UI_VERIFY_TEST_CACHE;
  if (!root) return { reason: 'e2e-chrome: UI_VERIFY_TEST_CACHE nie ustawione' };
  let cacheOk = false;
  try {
    cacheOk = fs.readdirSync(root).some((d) => ['playwright-core', 'pngjs', 'pixelmatch'].every((n) => fs.existsSync(path.join(root, d, 'node_modules', n, 'package.json'))));
  } catch { /* brak katalogu */ }
  if (!cacheOk) return { reason: 'e2e-chrome: brak playwright-core/pngjs/pixelmatch w UI_VERIFY_TEST_CACHE' };
  return { root };
}
const g = gate();

async function findChromePath() {
  if (g.reason) return null;
  const { findChrome } = await import(pathToFileURL(path.join(CURRENT_SKILL, 'scripts', 'lib', 'chrome.mjs')).href);
  const which = (name) => {
    const r = spawnSync(process.platform === 'win32' ? 'where' : 'which', [name], { encoding: 'utf8' });
    return r.status === 0 ? r.stdout.split(/\r?\n/)[0].trim() || null : null;
  };
  return findChrome({ platform: process.platform, env: process.env, fs, which }).path;
}
const chromePath = await findChromePath();
const SKIP = g.reason || (chromePath ? false : 'e2e-chrome: brak Chrome (ustaw UI_VERIFY_CHROME)');

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-a11y-golden-'));
after(() => fs.rmSync(tmpRoot, { recursive: true, force: true }));
let counter = 0;

function runAudit({ config, flags }) {
  const dir = path.join(tmpRoot, `proj-${counter++}`);
  fs.cpSync(path.join(SKILL, 'fixtures', 'a11y'), dir, { recursive: true });
  const base = { ...process.env };
  for (const k of Object.keys(base)) if (k.startsWith('UI_VERIFY_') || k === 'PLAYWRIGHT_MODULE_PATH') delete base[k];
  const env = {
    ...base, UI_VERIFY_CACHE_DIR: g.root, UI_VERIFY_CHROME: chromePath,
    UI_VERIFY_A11Y_FIXTURE_USER: 'fixture-user', UI_VERIFY_A11Y_FIXTURE_PASSWORD: ['fake', 'pw', 'golden'].join('-'),
  };
  if (process.env.UI_VERIFY_NO_SANDBOX) env.UI_VERIFY_NO_SANDBOX = process.env.UI_VERIFY_NO_SANDBOX;
  const args = [path.join(SKILL, 'scripts', 'a11y.mjs'), '--now', NOW, ...flags, '--config', `${config}.config.json`, 'out'];
  const r = spawnSync(process.execPath, args, { cwd: dir, env, encoding: 'utf8', timeout: 240000 });
  const file = path.join(dir, 'out', 'a11y.json');
  assert.ok(fs.existsSync(file), `no report: exit ${r.status}\n${r.stdout}${r.stderr}`);
  return JSON.parse(JSON.stringify({ report: JSON.parse(fs.readFileSync(file, 'utf8')), exit: r.status }));
}

const recorded = {};
const saved = !UPDATE && fs.existsSync(GOLDEN) ? JSON.parse(fs.readFileSync(GOLDEN, 'utf8')) : null;

test('golden e2e setup: update only from the baseline scripts; otherwise the golden file exists and covers every run', { skip: SKIP }, () => {
  if (UPDATE) {
    assert.equal(updateRefusal({ override: OVERRIDE, skillDir: SKILL, currentDir: CURRENT_SKILL }), null);
    return;
  }
  assert.ok(saved, `golden file ${path.relative(CURRENT_SKILL, GOLDEN)} is missing: record it from commit ${BASELINE_COMMIT} (see the header of this file)`);
  assert.equal(saved.source.scriptsSha256, BASELINE_SCRIPTS_SHA256, 'the golden file was not recorded from the baseline scripts');
  assert.deepEqual(Object.keys(saved.runs).sort(), RUNS.map((r) => r.id).sort());
});

for (const run of RUNS) {
  test(`golden e2e ${run.id}: rule, selector, verdict, reason, summary, result and exit equal to ${BASELINE_COMMIT}`, { skip: SKIP }, () => {
    if (UPDATE) assert.equal(updateRefusal({ override: OVERRIDE, skillDir: SKILL, currentDir: CURRENT_SKILL }), null);
    else assert.ok(saved, 'golden file is missing');
    const { report, exit } = runAudit(run);
    const actual = foldRace(run.id, project(report, exit));
    if (UPDATE) recorded[run.id] = actual;
    else assert.deepStrictEqual(actual, saved.runs[run.id]);
  });
}

// Registered only for a recording, so a comparison run has no skip of its own.
if (UPDATE) test('golden e2e record: the file is written only when every run produced a report', { skip: SKIP }, () => {
  assert.equal(updateRefusal({ override: OVERRIDE, skillDir: SKILL, currentDir: CURRENT_SKILL }), null);
  assert.deepEqual(Object.keys(recorded).sort(), RUNS.map((r) => r.id).sort());
  fs.mkdirSync(path.dirname(GOLDEN), { recursive: true });
  fs.writeFileSync(GOLDEN, `${JSON.stringify({ source: { commit: BASELINE_COMMIT, scriptsSha256: BASELINE_SCRIPTS_SHA256, now: NOW }, runs: recorded }, null, 1)}\n`, 'utf8');
});
