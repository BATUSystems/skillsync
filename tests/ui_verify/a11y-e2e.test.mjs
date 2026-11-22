// E2E z prawdziwym Chrome: scripts/a11y.mjs na fixturach z library/skills/ui-verify/fixtures/a11y.
// Pomijane z powodem "e2e-chrome: ..." gdy brak Chrome (UI_VERIFY_CHROME albo system) lub UI_VERIFY_TEST_CACHE
// z playwright-core, pngjs i pixelmatch. Fixtury sa plikami file: (bez serwera); zadanie spoza listy originow idzie
// do nieistniejacego hosta .invalid i jest abortowane w przegladarce, wiec nic nie opuszcza maszyny.
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
const SCRIPT = path.join(SKILL, 'scripts', 'a11y.mjs');
const FIXTURES = path.join(SKILL, 'fixtures', 'a11y');
const CHROME_URL = pathToFileURL(path.join(SKILL, 'scripts', 'lib', 'chrome.mjs')).href;

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-a11y-e2e-'));
after(() => fs.rmSync(tmpRoot, { recursive: true, force: true }));
let counter = 0;

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

// Kopia fixtur; katalog konfiguracji = katalog kopii (outDir i pliki wejsciowe musza byc wzgledne i pod nim).
function project() {
  const dir = path.join(tmpRoot, `proj-${counter++}`);
  fs.cpSync(FIXTURES, dir, { recursive: true });
  return dir;
}

function run(dir, args, env = {}) {
  const base = { ...process.env };
  for (const k of Object.keys(base)) if (k.startsWith('UI_VERIFY_') || k === 'PLAYWRIGHT_MODULE_PATH') delete base[k];
  const extra = { UI_VERIFY_CACHE_DIR: g.root, UI_VERIFY_CHROME: chromePath || '' };
  if (process.env.UI_VERIFY_NO_SANDBOX) extra.UI_VERIFY_NO_SANDBOX = process.env.UI_VERIFY_NO_SANDBOX;
  const r = spawnSync(process.execPath, [SCRIPT, ...args], { cwd: dir, env: { ...base, ...extra, ...env }, encoding: 'utf8', timeout: 240000 });
  return { code: r.status, stdout: r.stdout ?? '', stderr: r.stderr ?? '', error: r.error };
}

// Jeden bieg audytu: `a11y.mjs [flagi] --config <config> out`; wynik z out/a11y.json (gdy powstal).
function audit(config, { dir = project(), flags = [], env } = {}) {
  const r = run(dir, [...flags, '--config', config, 'out'], env);
  const file = path.join(dir, 'out', 'a11y.json');
  const report = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : null;
  return { ...r, dir, report };
}

const all = (report) => report.pages.flatMap((p) => p.findings);
const byRule = (report, rule, verdict) => all(report).filter((f) => f.rule === rule && (verdict === undefined || f.verdict === verdict));
const NOW = ['--now', '2026-11-22'];
const detail = (r) => `kod ${r.code}\n${r.stdout}${r.stderr}`;

// --- (a) przypadek acceptance "contrast": piec elementow ---

test('e2e contrast przez --expect: kod 0 i raport zgodny z expected.json (pieciu elementow), mimo celowych FAIL', { skip: SKIP }, () => {
  const r = audit('contrast.config.json', { flags: ['--expect', 'contrast/expected.json'] });
  assert.equal(r.code, 0, detail(r));
  assert.equal(r.report.mode, 'expect');
  assert.equal(r.report.exit, 0);
  assert.equal(r.report.result, 'PASS');
  assert.equal(r.report.expectResult.exit, 0);
  if (Array.isArray(r.report.expectResult.entries)) {
    assert.equal(r.report.expectResult.entries.length, 5);
    assert.ok(r.report.expectResult.entries.every((e) => e.status === 'match'), JSON.stringify(r.report.expectResult.entries));
  }
  assert.equal(r.report.auditExit, 1, 'wynik zwyklego audytu (celowe FAIL) zostaje w raporcie');
});

test('e2e contrast bez --expect: kod 1, dokladnie dwa FAIL (#fail, #layer-fail), stosunki zgodne z expected.json', { skip: SKIP }, () => {
  const r = audit('contrast.config.json');
  assert.equal(r.code, 1, detail(r));
  const fails = all(r.report).filter((f) => f.verdict === 'FAIL');
  assert.deepEqual(fails.map((f) => f.selector).sort(), ['#fail', '#layer-fail']);
  const expected = JSON.parse(fs.readFileSync(path.join(FIXTURES, 'contrast', 'expected.json'), 'utf8'));
  for (const e of expected.elements) {
    const f = byRule(r.report, 'contrast').find((x) => x.selector === e.selector);
    assert.ok(f, `brak findingu ${e.selector}`);
    assert.equal(f.verdict, e.verdict, e.selector);
    assert.ok(Math.abs(f.measured.ratio - e.ratio) <= expected.tolerance, `${e.selector}: ${f.measured.ratio} vs ${e.ratio}`);
    assert.equal(f.required, e.required, e.selector);
  }
});

// --- (b) jeden swiadomy blad na regule ---

test('e2e defects.html: target-size, focus-keyboard-indicator, tab-positive-tabindex, console i contrast (varying) to FAIL, kod 1', { skip: SKIP }, () => {
  const r = audit('defects.config.json');
  assert.equal(r.code, 1, detail(r));
  const targets = byRule(r.report, 'target-size', 'FAIL').map((f) => f.selector);
  for (const id of ['#tiny-1', '#tiny-2', '#tiny-3']) assert.ok(targets.includes(id), `target-size FAIL ${id}: ${targets}`);
  assert.ok(byRule(r.report, 'focus-keyboard-indicator', 'FAIL').some((f) => f.selector === '#no-focus'), 'focus na #no-focus');
  assert.ok(byRule(r.report, 'tab-positive-tabindex', 'FAIL').length >= 1, 'tab-positive-tabindex');
  const consoleFail = byRule(r.report, 'console', 'FAIL');
  assert.ok(consoleFail.some((f) => String(f.message).includes('fixture-defect-console-error')), 'console');
  const c = byRule(r.report, 'contrast', 'FAIL').find((f) => f.selector === '#bg-text');
  assert.ok(c, 'contrast na #bg-text');
  assert.equal(c.measured.backgroundKind, 'varying');
});

test('e2e clean.html: wersja poprawna, brak FAIL i UNVERIFIED, kod 0', { skip: SKIP }, () => {
  const r = audit('clean.config.json');
  assert.equal(r.code, 0, detail(r));
  assert.equal(r.report.result, 'PASS');
  assert.deepEqual(all(r.report).filter((f) => f.verdict === 'FAIL' || f.verdict === 'UNVERIFIED'), []);
  for (const rule of ['target-size', 'focus-keyboard-indicator', 'console', 'contrast']) assert.ok(byRule(r.report, rule, 'PASS').length >= 1, `PASS ${rule}`);
});

// --- (c) wyjatki z pliku ---

test('e2e wyjatek z pliku: console FAIL staje sie EXCEPTED, kod 0 (to jedyny FAIL)', { skip: SKIP }, () => {
  const plain = audit('console-error.config.json', { flags: NOW });
  assert.equal(plain.code, 1, detail(plain));
  assert.equal(byRule(plain.report, 'console', 'FAIL').length, 1);
  const r = audit('console-error.config.json', { flags: [...NOW, '--exceptions', 'exceptions.json'] });
  assert.equal(r.code, 0, detail(r));
  assert.equal(byRule(r.report, 'console', 'EXCEPTED').length, 1);
  assert.equal(byRule(r.report, 'console', 'FAIL').length, 0);
  assert.equal(r.report.exceptions.filter((e) => e.status === 'used').length, 1);
  assert.match(fs.readFileSync(path.join(r.dir, 'out', 'a11y.md'), 'utf8'), /known console error of the fixture/);
});

test('e2e wyjatek po terminie nie dziala: FAIL zostaje (kod 1) i ostrzezenie exception-expired', { skip: SKIP }, () => {
  const r = audit('console-error.config.json', { flags: ['--now', '2027-07-01', '--exceptions', 'exceptions.json'] });
  assert.equal(r.code, 1, detail(r));
  assert.equal(byRule(r.report, 'console', 'FAIL').length, 1);
  assert.ok(r.report.warnings.some((w) => w.code === 'exception-expired'), JSON.stringify(r.report.warnings));
});

test('e2e wpis wyjatku bez reason: kod 2', { skip: SKIP }, () => {
  const dir = project();
  fs.writeFileSync(path.join(dir, 'no-reason.json'), JSON.stringify({ version: 1, exceptions: [{ rule: 'console', page: 'console-error', messageContains: 'fixture-defect-console-error' }] }));
  const r = audit('console-error.config.json', { dir, flags: [...NOW, '--exceptions', 'no-reason.json'] });
  assert.equal(r.code, 2, detail(r));
});

// --- (d) UNVERIFIED i potwierdzenia reczne ---

for (const [config, id, selector, reason] of [
  ['opacity-group.config.json', 'opacity-group', '#txt', 'opacity-group-with-paint'],
  ['overlay.config.json', 'overlay', '#under', 'occluded'],
]) {
  test(`e2e ${id}: UNVERIFIED ${reason} i kod 3, a potwierdzenie z pliku daje CONFIRMED i kod 0`, { skip: SKIP }, () => {
    const dir = project();
    const r = audit(config, { dir, flags: NOW });
    assert.equal(r.code, 3, detail(r));
    const f = byRule(r.report, 'contrast', 'UNVERIFIED').find((x) => x.selector === selector);
    assert.ok(f, JSON.stringify(all(r.report)));
    assert.equal(f.reason, reason);
    assert.equal(byRule(r.report, 'contrast', 'PASS').length, 0, 'UNVERIFIED nigdy nie staje sie PASS');

    const c = audit(config, { flags: [...NOW, '--exceptions', 'exceptions.json'] });
    assert.equal(c.code, 0, detail(c));
    const cf = byRule(c.report, 'contrast', 'CONFIRMED').find((x) => x.selector === selector);
    assert.ok(cf, JSON.stringify(all(c.report)));
    assert.equal(cf.confirmation.checkedBy, 'fixture');
    assert.match(fs.readFileSync(path.join(c.dir, 'out', 'a11y.md'), 'utf8'), /Confirmed manually/);
  });
}

// --- (e) origin spoza listy ---

test('e2e console.html: zadanie spoza listy jest abortowane, w warnings blocked-origin, bez FAIL z tego powodu', { skip: SKIP }, () => {
  const r = audit('console.config.json');
  assert.equal(r.code, 0, detail(r));
  const w = r.report.warnings.find((x) => x.code === 'blocked-origin');
  assert.ok(w, JSON.stringify(r.report.warnings));
  assert.match(w.message, /blocked\.example\.invalid/);
  assert.deepEqual(byRule(r.report, 'console', 'FAIL'), []);
  assert.ok(byRule(r.report, 'console', 'PASS').length >= 1);
});

// --- (f) maskowanie sekretu ---

const secretValue = () => ['fake', 'secret', String(process.pid), 'e2e1'].join('-');

function secretProject(secret, { masked }) {
  const dir = project();
  const html = fs.readFileSync(path.join(dir, 'console-error.html'), 'utf8').replace('fixture-defect-console-error', `value ${secret}`);
  fs.writeFileSync(path.join(dir, 'secret.html'), html);
  const cfg = { version: 1, pages: [{ id: 'secret', url: 'secret.html', assert: { selector: 'main' } }], ...(masked ? { secretEnv: ['A11Y_FAKE_SECRET'] } : {}) };
  fs.writeFileSync(path.join(dir, 'secret.config.json'), JSON.stringify(cfg));
  return dir;
}
const outputs = (r) => [r.stdout, r.stderr, ...['a11y.json', 'a11y.md'].map((n) => fs.readFileSync(path.join(r.dir, 'out', n), 'utf8'))].join('\n');

test('e2e sekret: wartosc ze zmiennej srodowiska wypisana przez strone w console.error nie trafia do a11y.json, a11y.md ani stdout/stderr', { skip: SKIP }, () => {
  const secret = secretValue();
  const dir = secretProject(secret, { masked: true });
  const r = audit('secret.config.json', { dir, env: { A11Y_FAKE_SECRET: secret } });
  assert.equal(r.code, 1, detail(r));
  assert.equal(byRule(r.report, 'console', 'FAIL').length, 1);
  const text = outputs(r);
  for (const v of [secret, encodeURIComponent(secret), Buffer.from(secret).toString('base64'), Buffer.from(secret).toString('base64url')]) {
    assert.ok(!text.includes(v), `wariant sekretu w wyniku: ${v === secret ? 'surowy' : v}`);
  }
});

test('e2e sekret, kontrola: bez secretEnv ta sama atrapa trafia do raportu (test wycieku potrafi paść)', { skip: SKIP }, () => {
  const secret = secretValue();
  const dir = secretProject(secret, { masked: false });
  const r = audit('secret.config.json', { dir, env: { A11Y_FAKE_SECRET: secret } });
  assert.equal(r.code, 1, detail(r));
  assert.ok(outputs(r).includes(secret), 'komunikat konsoli z atrapa powinien trafic do raportu, gdy nie jest maskowany');
});

// --- (g) selektor z konfiguracji nie jest wykonywany ---

test('e2e bezpieczenstwo: selektor z cudzyslowami i </script> daje kod 2 albo brak dopasowania, nigdy wykonanie', { skip: SKIP }, () => {
  const dir = project();
  // Gdyby selektor zostal wklejony do kodu, wstrzykniety skrypt wypisalby "INJECTED-MARKER" (zrodlo ma plus, wiec literal sie nie powtarza).
  const selector = 'x"]\'</script><script>console.error("INJECTED-"+"MARKER")</script>[a="';
  const cfg = { version: 1, pages: [{ id: 'sec', url: 'clean.html', assert: { selector } }] };
  fs.writeFileSync(path.join(dir, 'sec.config.json'), JSON.stringify(cfg));
  const r = audit('sec.config.json', { dir });
  assert.ok([1, 2].includes(r.code), detail(r));
  const text = [r.stdout, r.stderr, r.report ? JSON.stringify(r.report) : ''].join('\n');
  assert.ok(!text.includes('INJECTED-MARKER'), 'wstrzykniety kod nie moze sie wykonac');
  if (r.report) assert.deepEqual(byRule(r.report, 'console', 'FAIL'), []);
});
