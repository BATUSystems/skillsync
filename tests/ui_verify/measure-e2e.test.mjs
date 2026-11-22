// E2E z prawdziwym Chrome (S4): scripts/measure.mjs na fixturach z library/skills/ui-verify/fixtures.
// Pomijane z powodem "e2e-chrome: ..." gdy brak Chrome (UI_VERIFY_CHROME albo system) lub UI_VERIFY_TEST_CACHE
// z playwright-core, pngjs i pixelmatch.
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const SKILL = path.join(ROOT, 'library', 'skills', 'ui-verify');
const SCRIPT = path.join(SKILL, 'scripts', 'measure.mjs');
const FIXTURES = path.join(SKILL, 'fixtures');
const CHROME_URL = pathToFileURL(path.join(SKILL, 'scripts', 'lib', 'chrome.mjs')).href;

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-measure-e2e-'));
after(() => fs.rmSync(tmpRoot, { recursive: true, force: true }));
let counter = 0;
const newDir = (label = 'd') => {
  const d = path.join(tmpRoot, `${label}-${counter++}`);
  fs.mkdirSync(d, { recursive: true });
  return d;
};

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
  const { findChrome } = await import(CHROME_URL);
  const which = (name) => {
    const r = spawnSync(process.platform === 'win32' ? 'where' : 'which', [name], { encoding: 'utf8' });
    return r.status === 0 ? r.stdout.split(/\r?\n/)[0].trim() || null : null;
  };
  return findChrome({ platform: process.platform, env: process.env, fs, which }).path;
}
const chromePath = await findChromePath();
const SKIP = g.reason || (chromePath ? false : 'e2e-chrome: brak Chrome (ustaw UI_VERIFY_CHROME)');

function run(args, { cwd, env = {} } = {}) {
  const base = { ...process.env };
  for (const k of Object.keys(base)) if (k.startsWith('UI_VERIFY_') || k === 'PLAYWRIGHT_MODULE_PATH') delete base[k];
  const extra = { UI_VERIFY_CACHE_DIR: g.root, UI_VERIFY_CHROME: chromePath || '' };
  if (process.env.UI_VERIFY_NO_SANDBOX) extra.UI_VERIFY_NO_SANDBOX = process.env.UI_VERIFY_NO_SANDBOX;
  const r = spawnSync(process.execPath, [SCRIPT, ...args], { cwd, env: { ...base, ...extra, ...env }, encoding: 'utf8', timeout: 240000 });
  return { code: r.status, stdout: r.stdout ?? '', stderr: r.stderr ?? '', error: r.error };
}

const CASES = JSON.parse(fs.readFileSync(path.join(FIXTURES, 'cases.json'), 'utf8'));

// Kopia fixtur pod katalogiem konfiguracji (adresy plikowe musza lezec w jego obrebie).
function project(pairs, top = {}) {
  const dir = newDir('proj');
  fs.cpSync(FIXTURES, path.join(dir, 'fx'), { recursive: true });
  const cfg = { version: 1, viewport: CASES.viewport, pairs, ...top };
  const file = path.join(dir, 'config.json');
  fs.writeFileSync(file, JSON.stringify(cfg));
  return { dir, file };
}
const fixturePair = (c, over = {}) => ({
  id: c.id,
  reference: { url: `fx/${c.files.reference}`, selector: c.selector },
  candidate: { url: `fx/${c.files.candidate}`, selector: c.selector },
  ...over,
});
function listFiles(dir) {
  const out = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...listFiles(p)); else out.push(p);
  }
  return out;
}
const readResult = (dir) => JSON.parse(fs.readFileSync(path.join(dir, 'out', 'measure.json'), 'utf8'));

// Wynik oczekiwany: pomiar (pudelko i style) PASS, a ksztalt wg expected.pixelDiff z cases.json.
for (const c of CASES.cases) {
  const wantShape = c.expected.pixelDiff;
  test(`e2e ${c.id}: pomiar ${c.expected.measure}, ksztalt ${wantShape}, kod ${wantShape === 'PASS' ? 0 : 1}`, { skip: SKIP }, () => {
    const { dir, file } = project([fixturePair(c)]);
    const r = run(['--config', file, 'out'], { cwd: dir });
    assert.equal(r.code, wantShape === 'PASS' ? 0 : 1, r.stdout + r.stderr);
    const j = readResult(dir);
    const p = j.pairs.find((x) => x.id === c.id);
    assert.ok(p, 'brak pary w measure.json');
    assert.equal(p.measure.verdict, c.expected.measure);
    assert.equal(p.shape.verdict, wantShape);
    assert.equal(p.verdict, wantShape);
    assert.equal(j.verdict, wantShape);
    if (wantShape === 'FAIL') {
      assert.ok(p.shape.pct > CASES.pixelDiff.maxPct, `pct ${p.shape.pct}`);
      assert.ok(fs.existsSync(path.join(dir, 'out', `${c.id}.diff.png`)), 'brak PNG roznic');
    } else {
      assert.ok(p.shape.pct <= CASES.pixelDiff.maxPct, `pct ${p.shape.pct}`);
    }
    assert.ok(fs.existsSync(path.join(dir, 'out', 'measure.txt')));
    assert.match(fs.readFileSync(path.join(dir, 'out', 'measure.txt'), 'utf8'), new RegExp(`^${wantShape} ${c.id}\\b`, 'm'));
  });
}

test('e2e mirrored-icon: pudelko i style identyczne, a mimo to wynik FAIL przez ksztalt (sedno kontroli ksztaltu)', { skip: SKIP }, () => {
  const c = CASES.cases.find((x) => x.id === 'mirrored-icon');
  const { dir, file } = project([fixturePair(c)]);
  const r = run(['--json', '--config', file, 'out'], { cwd: dir });
  assert.equal(r.code, 1, r.stdout + r.stderr);
  const p = readResult(dir).pairs[0];
  assert.equal(p.measure.verdict, 'PASS');
  assert.deepEqual(p.measure.unapproved, []);
  assert.equal(p.shape.verdict, 'FAIL');
});

test('e2e: maska zakrywajaca cale pudelko => ksztalt PASS (kontrola, ze maski dochodza do diffu)', { skip: SKIP }, () => {
  const c = CASES.cases.find((x) => x.id === 'mirrored-icon');
  const { dir, file } = project([fixturePair(c, { masks: [{ x: 0, y: 0, width: 400, height: 200 }] })]);
  const r = run(['--config', file, 'out'], { cwd: dir });
  assert.equal(r.code, 0, r.stdout + r.stderr);
});

test('e2e: brak elementu (zly selektor) => UNVERIFIED, kod 3', { skip: SKIP }, () => {
  const c = CASES.cases[0];
  const pair = fixturePair(c);
  pair.candidate.selector = '#nie-ma-takiego';
  const { dir, file } = project([pair]);
  const r = run(['--config', file, 'out'], { cwd: dir });
  assert.equal(r.code, 3, r.stdout + r.stderr);
  assert.match(fs.readFileSync(path.join(dir, 'out', 'measure.txt'), 'utf8'), /^UNVERIFIED /m);
});

// --- wyciek sekretu ---

const secretValue = () => ['fake', 'secret', String(process.pid), 'abcd'].join('-');
function everything(dir, r) {
  const parts = [r.stdout, r.stderr];
  const out = path.join(dir, 'out');
  if (fs.existsSync(out)) {
    for (const f of listFiles(out)) parts.push(path.relative(out, f), fs.readFileSync(f).toString('latin1'));
  }
  return parts.join('\n');
}
function secretProject(secret) {
  const c = CASES.cases[0];
  const pair = fixturePair(c, { id: `pw-${secret}` });
  pair.reference.selector = `#nope-${secret}`;
  pair.candidate.selector = `#nope-${secret}`;
  return project([pair], { secretEnv: ['UV_FAKE_SECRET'] });
}

test('e2e sekret: atrapa hasla (id pary i selektor, celowo bledny selektor) nie wystepuje w outDir ani w stdout/stderr', { skip: SKIP }, () => {
  const secret = secretValue();
  const { dir, file } = secretProject(secret);
  const r = run(['--config', file, 'out'], { cwd: dir, env: { UV_FAKE_SECRET: secret, UI_VERIFY_SECRET_ENV: 'UV_FAKE_SECRET' } });
  assert.ok([2, 3].includes(r.code), `kod ${r.code}: ${r.stdout}${r.stderr}`);
  const all = everything(dir, r);
  for (const v of [secret, encodeURIComponent(secret), Buffer.from(secret).toString('base64'), Buffer.from(secret).toString('base64url')]) {
    assert.ok(!all.includes(v), `wariant sekretu w wyniku: ${v === secret ? 'surowy' : v}`);
  }
});

test('e2e sekret, kontrola: bez maskowania ta sama atrapa trafia do wyniku (test wycieku potrafi paść)', { skip: SKIP }, () => {
  const secret = secretValue();
  const { dir, file } = secretProject(secret);
  const cfg = JSON.parse(fs.readFileSync(file, 'utf8'));
  delete cfg.secretEnv;
  fs.writeFileSync(file, JSON.stringify(cfg));
  const r = run(['--config', file, 'out'], { cwd: dir, env: { UV_FAKE_SECRET: secret } });
  assert.ok([2, 3].includes(r.code), `kod ${r.code}: ${r.stdout}${r.stderr}`);
  assert.ok(everything(dir, r).includes(secret), 'id pary z sekretem powinno trafic do wyniku, gdy nie jest maskowane');
});
