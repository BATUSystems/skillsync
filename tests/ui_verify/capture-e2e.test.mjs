// E2E z prawdziwym Chrome (S5): scripts/capture.mjs i scripts/diff.mjs na stronach lokalnych (file://) pod katalogiem tymczasowym.
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
const CAPTURE = path.join(SKILL, 'scripts', 'capture.mjs');
const DIFF = path.join(SKILL, 'scripts', 'diff.mjs');
const CHROME_URL = pathToFileURL(path.join(SKILL, 'scripts', 'lib', 'chrome.mjs')).href;

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-capture-e2e-'));
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

function exec(script, args, { cwd, env = {} } = {}) {
  const base = { ...process.env };
  for (const k of Object.keys(base)) if (k.startsWith('UI_VERIFY_') || k === 'PLAYWRIGHT_MODULE_PATH') delete base[k];
  const extra = { UI_VERIFY_CACHE_DIR: g.root, UI_VERIFY_CHROME: chromePath || '' };
  if (process.env.UI_VERIFY_NO_SANDBOX) extra.UI_VERIFY_NO_SANDBOX = process.env.UI_VERIFY_NO_SANDBOX;
  const r = spawnSync(process.execPath, [script, ...args], { cwd, env: { ...base, ...extra, ...env }, encoding: 'utf8', timeout: 240000 });
  return { code: r.status, stdout: r.stdout ?? '', stderr: r.stderr ?? '', error: r.error };
}
const capture = (args, o) => exec(CAPTURE, args, o);
const diff = (args, o) => exec(DIFF, args, o);

// Strona: h1, element zmienny (zegar o stalych wymiarach) i blok #app; tryb ciemny zmienia tlo.
const page = ({ clock = '12:00:01', appColor = '#3366cc', h1 = 'Hello' } = {}) => `<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;background:#fff;color:#000;font:16px monospace}
@media (prefers-color-scheme: dark){body{background:#101010;color:#eee}}
#app{width:120px;height:60px;background:${appColor}}
.clock{width:140px;height:30px;background:#eee;color:#000}
</style></head><body><h1>${h1}</h1><div id="app"></div><div class="clock" id="clock">${clock}</div></body></html>`;

function project(files, cfg, cfgName = 'config.json') {
  const dir = newDir('proj');
  for (const [n, c] of Object.entries(files)) fs.writeFileSync(path.join(dir, n), c);
  const file = path.join(dir, cfgName);
  fs.writeFileSync(file, JSON.stringify({ version: 1, ...cfg }));
  return { dir, file };
}
function listFiles(dir) {
  const out = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...listFiles(p)); else out.push(p);
  }
  return out;
}
const readManifest = (dir, out = 'out') => JSON.parse(fs.readFileSync(path.join(dir, out, 'manifest.json'), 'utf8'));
const pngWidth = (file) => fs.readFileSync(file).readUInt32BE(16);
const HOME = { id: 'home', url: 'home.html', waitFor: '#app', assert: { selector: 'h1', text: 'Hello' } };

test('e2e: macierz szerokosci x trybow => osobny PNG na kombinacje, manifest, kod 0', { skip: SKIP }, () => {
  const { dir, file } = project({ 'home.html': page() }, { widths: [375, 1280], modes: ['light', 'dark'], pages: [HOME] });
  const r = capture(['--config', file, 'out'], { cwd: dir });
  assert.equal(r.code, 0, r.stdout + r.stderr);
  const m = readManifest(dir);
  assert.equal(m.shots.length, 4);
  for (const w of [375, 1280]) {
    for (const mode of ['light', 'dark']) {
      const name = `home__${w}-${mode}.png`;
      assert.ok(m.shots.some((s) => s.name === name && s.assertion === 'PASS'), name);
      const f = path.join(dir, 'out', name);
      assert.ok(fs.existsSync(f), name);
      assert.equal(pngWidth(f), w, `${name}: szerokosc PNG`);
    }
  }
  const light = fs.readFileSync(path.join(dir, 'out', 'home__375-light.png'));
  const dark = fs.readFileSync(path.join(dir, 'out', 'home__375-dark.png'));
  assert.ok(!light.equals(dark), 'tryb dark powinien dawac inny obraz niz light');
  assert.ok(fs.existsSync(path.join(dir, 'out', 'capture.txt')));
});

test('e2e: asercja pozytywna PASS => kod 0; brak elementu => FAIL, kod 1 (zrzut mimo to powstaje)', { skip: SKIP }, () => {
  const ok = project({ 'home.html': page() }, { pages: [HOME] });
  assert.equal(capture(['--config', ok.file, 'out'], { cwd: ok.dir }).code, 0);
  assert.equal(readManifest(ok.dir).shots[0].assertion, 'PASS');
  const bad = project({ 'home.html': page() }, { pages: [{ ...HOME, assert: { selector: '#nie-ma-takiego' } }] });
  const r = capture(['--config', bad.file, 'out'], { cwd: bad.dir });
  assert.equal(r.code, 1, r.stdout + r.stderr);
  assert.equal(readManifest(bad.dir).shots[0].assertion, 'FAIL');
  assert.match(r.stdout + fs.readFileSync(path.join(bad.dir, 'out', 'capture.txt'), 'utf8'), /assertion-failed/);
  const wrongText = project({ 'home.html': page() }, { pages: [{ ...HOME, assert: { selector: 'h1', text: 'Inny tekst' } }] });
  assert.equal(capture(['--config', wrongText.file, 'out'], { cwd: wrongText.dir }).code, 1);
});

test('e2e: strona bez assert => UNVERIFIED, kod 3, manifest assertion NONE', { skip: SKIP }, () => {
  const { dir, file } = project({ 'home.html': page() }, { pages: [{ id: 'home', url: 'home.html' }] });
  const r = capture(['--config', file, 'out'], { cwd: dir });
  assert.equal(r.code, 3, r.stdout + r.stderr);
  assert.equal(readManifest(dir).shots[0].assertion, 'NONE');
});

function twoRuns({ a, b, pageA = HOME, pageB = HOME }) {
  const dir = newDir('two');
  fs.writeFileSync(path.join(dir, 'a.html'), a);
  fs.writeFileSync(path.join(dir, 'b.html'), b);
  fs.writeFileSync(path.join(dir, 'ca.json'), JSON.stringify({ version: 1, pages: [{ ...pageA, url: 'a.html' }] }));
  fs.writeFileSync(path.join(dir, 'cb.json'), JSON.stringify({ version: 1, pages: [{ ...pageB, url: 'b.html' }] }));
  const ra = capture(['--config', 'ca.json', 'before'], { cwd: dir });
  const rb = capture(['--config', 'cb.json', 'after'], { cwd: dir });
  return { dir, ra, rb };
}

test('e2e: maska selektorem zamalowuje element zmienny: dwa zrzuty z roznym zegarem => diff SAME, kod 0; bez maski DIFFERENT', { skip: SKIP }, () => {
  const masked = { ...HOME, masks: [{ selector: '.clock' }] };
  const { dir, ra, rb } = twoRuns({ a: page({ clock: '12:00:01' }), b: page({ clock: '23:59:58' }), pageA: masked, pageB: masked });
  assert.equal(ra.code, 0, ra.stdout + ra.stderr);
  assert.equal(rb.code, 0, rb.stdout + rb.stderr);
  const d = diff(['before', 'after'], { cwd: dir });
  assert.equal(d.code, 0, d.stdout + d.stderr);
  assert.match(d.stdout, /SAME/);
  assert.doesNotMatch(d.stdout, /DIFFERENT/);
  // kontrola: bez maski ten sam zegar daje roznice
  const plain = twoRuns({ a: page({ clock: '12:00:01' }), b: page({ clock: '23:59:58' }) });
  const dp = diff(['before', 'after'], { cwd: plain.dir });
  assert.equal(dp.code, 1, dp.stdout + dp.stderr);
  assert.match(dp.stdout, /DIFFERENT/);
});

test('e2e: maska prostokatem tez dziala w diffie', { skip: SKIP }, () => {
  const rect = (pg) => ({ ...pg, masks: [{ x: 0, y: 0, width: 400, height: 400 }] });
  const { dir } = twoRuns({ a: page({ appColor: '#3366cc' }), b: page({ appColor: '#cc3333' }), pageA: rect(HOME), pageB: rect(HOME) });
  assert.equal(diff(['before', 'after'], { cwd: dir }).code, 0);
});

test('e2e: zmiana strony => diff DIFFERENT, kod 1', { skip: SKIP }, () => {
  const { dir } = twoRuns({ a: page({ appColor: '#3366cc' }), b: page({ appColor: '#cc3333' }) });
  const d = diff(['before', 'after'], { cwd: dir });
  assert.equal(d.code, 1, d.stdout + d.stderr);
  assert.match(d.stdout, /DIFFERENT/);
});

test('e2e: identyczne strony z asercjami PASS => SAME i kod 0; ten sam zrzut bez asercji => UNVERIFIED, kod 3 (nigdy SAME)', { skip: SKIP }, () => {
  const same = twoRuns({ a: page(), b: page() });
  const d = diff(['before', 'after'], { cwd: same.dir });
  assert.equal(d.code, 0, d.stdout + d.stderr);
  const bare = { id: 'home', url: 'x' };
  const none = twoRuns({ a: page(), b: page(), pageA: bare, pageB: bare });
  assert.equal(none.ra.code, 3);
  const dn = diff(['before', 'after'], { cwd: none.dir });
  assert.equal(dn.code, 3, dn.stdout + dn.stderr);
  assert.doesNotMatch(dn.stdout, /\bSAME\b/);
  assert.match(dn.stdout, /UNVERIFIED/);
});

test('e2e: diff zestawow o roznej liczbie zrzutow => MISSING daje kod 1', { skip: SKIP }, () => {
  const dir = newDir('miss');
  fs.writeFileSync(path.join(dir, 'a.html'), page());
  fs.writeFileSync(path.join(dir, 'c1.json'), JSON.stringify({ version: 1, pages: [{ ...HOME, url: 'a.html' }, { ...HOME, id: 'second', url: 'a.html' }] }));
  fs.writeFileSync(path.join(dir, 'c2.json'), JSON.stringify({ version: 1, pages: [{ ...HOME, url: 'a.html' }] }));
  assert.equal(capture(['--config', 'c1.json', 'before'], { cwd: dir }).code, 0);
  assert.equal(capture(['--config', 'c2.json', 'after'], { cwd: dir }).code, 0);
  const d = diff(['before', 'after'], { cwd: dir });
  assert.equal(d.code, 1, d.stdout + d.stderr);
  assert.match(d.stdout, /MISSING/);
});

// --- logowanie i sekret ---

const LOGIN = `<!doctype html><html><body><form onsubmit="return false"><input id="user"><input id="pass" type="password"><button id="go" type="button" onclick="document.body.dataset.done='1'">go</button></form><h1>Hello</h1></body></html>`;
const everything = (dir, r) => {
  const parts = [r.stdout, r.stderr];
  const out = path.join(dir, 'out');
  if (fs.existsSync(out)) for (const f of listFiles(out)) parts.push(path.relative(out, f), fs.readFileSync(f).toString('latin1'));
  return parts.join('\n');
};
const variants = (s) => [s, encodeURIComponent(s), Buffer.from(s).toString('base64'), Buffer.from(s).toString('base64url')];

function loginProject(passSelector, extra = {}) {
  return project({ 'login.html': LOGIN, 'home.html': page() }, {
    login: { url: 'login.html', steps: [{ fill: { selector: '#user', valueEnv: 'UV_FAKE_USER' } }, { fill: { selector: passSelector, valueEnv: 'UV_FAKE_PASS' } }, { click: '#go' }] },
    pages: [HOME],
    ...extra,
  });
}

test('e2e sekret: haslo ze zmiennej (valueEnv) nie wystepuje w outDir ani w stdout/stderr przy bledzie logowania (zly selektor)', { skip: SKIP }, () => {
  const secret = secretValue();
  const { dir, file } = loginProject(`#nope-${secret}`);
  const r = capture(['--config', file, 'out'], { cwd: dir, env: { UV_FAKE_USER: 'user-x', UV_FAKE_PASS: secret } });
  assert.ok([2, 3].includes(r.code), `kod ${r.code}: ${r.stdout}${r.stderr}`);
  const all = everything(dir, r);
  for (const v of variants(secret)) assert.ok(!all.includes(v), `wariant sekretu w wyniku: ${v === secret ? 'surowy' : v}`);
});

test('e2e sekret, kontrola: ta sama atrapa niezadeklarowana jako sekret trafia do wyniku (test wycieku potrafi paść)', { skip: SKIP }, () => {
  const secret = secretValue();
  const { dir, file } = loginProject(`#nope-${secret}`);
  const r = capture(['--config', file, 'out'], { cwd: dir, env: { UV_FAKE_USER: 'user-x', UV_FAKE_PASS: 'inne-haslo-zupelnie' } });
  assert.ok([2, 3].includes(r.code), `kod ${r.code}: ${r.stdout}${r.stderr}`);
  assert.ok(everything(dir, r).includes(secret), 'selektor z atrapa powinien trafic do bledu, gdy nie jest maskowany');
});

test('e2e sekret: udane logowanie - haslo nie trafia do outDir ani stdout/stderr', { skip: SKIP }, () => {
  const secret = secretValue();
  const { dir, file } = loginProject('#pass');
  const r = capture(['--config', file, 'out'], { cwd: dir, env: { UV_FAKE_USER: 'user-x', UV_FAKE_PASS: secret } });
  assert.equal(r.code, 0, r.stdout + r.stderr);
  const all = everything(dir, r);
  for (const v of variants(secret)) assert.ok(!all.includes(v), 'sekret w wyniku');
});

test('e2e trace: bez --allow-trace brak trace.zip i storageState.json; z flaga sa, jest ostrzezenie i zalecenie .gitignore', { skip: SKIP }, () => {
  const secret = secretValue();
  const env = { UV_FAKE_USER: 'user-x', UV_FAKE_PASS: secret };
  const a = loginProject('#pass');
  const ra = capture(['--config', a.file, 'out'], { cwd: a.dir, env });
  assert.equal(ra.code, 0, ra.stdout + ra.stderr);
  const namesA = listFiles(path.join(a.dir, 'out')).map((f) => path.basename(f));
  assert.ok(!namesA.includes('trace.zip') && !namesA.includes('storageState.json'), namesA.join(','));
  assert.doesNotMatch(ra.stderr, /allow-trace|\.gitignore/i);
  const b = loginProject('#pass');
  const rb = capture(['--allow-trace', '--config', b.file, 'out'], { cwd: b.dir, env });
  assert.equal(rb.code, 0, rb.stdout + rb.stderr);
  const namesB = listFiles(path.join(b.dir, 'out')).map((f) => path.basename(f));
  assert.ok(namesB.includes('trace.zip'), namesB.join(','));
  assert.ok(namesB.includes('storageState.json'), namesB.join(','));
  assert.match(rb.stderr, /gitignore/i);
  assert.match(rb.stderr, /trace/i);
});

function secretValue() { return ['fake', 'secret', String(process.pid), 'abcd'].join('-'); }
