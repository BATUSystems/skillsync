// E2E z prawdziwym Chrome: scripts/a11y.mjs na fixturach z library/skills/ui-verify/fixtures/a11y.
// Pomijane z powodem "e2e-chrome: ..." gdy brak Chrome (UI_VERIFY_CHROME albo system) lub UI_VERIFY_TEST_CACHE
// z playwright-core, pngjs i pixelmatch. Fixtury sa plikami file: (bez serwera); zadanie spoza listy originow idzie
// do nieistniejacego hosta .invalid i jest abortowane w przegladarce, wiec nic nie opuszcza maszyny.
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import http from 'node:http';
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
  // M1: etykiety z kontrolka nie sa przystankami Tab (brak tab-unreachable), checkbox z etykieta to cel rownowazny
  assert.deepEqual(byRule(r.report, 'tab-unreachable').filter((f) => f.verdict !== 'PASS'), []);
  assert.ok(byRule(r.report, 'target-size').some((f) => f.selector === '#agree' && f.verdict === 'PASS'), 'checkbox z etykieta');
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

// ---------------------------------------------------------------------------------------------------------------
// Rozszerzone fixtury (review A5/A6): wysokie strony, fixed/sticky, lazy obraz, kolory, limity, ramki i shadow DOM,
// mysz, wiele stron, logowanie, rtl, originy (file: poza katalogiem, przekierowanie 302, data:).
// ---------------------------------------------------------------------------------------------------------------

const pageFinding = (report, rule, selector) => byRule(report, rule).find((f) => f.selector === selector);

test('e2e tall.html (~5000 px): wspolrzedne dokumentu, jasny tekst na ciemnym pasie nizej PASS, blady tekst na dole FAIL', { skip: SKIP }, () => {
  const r = audit('tall.config.json');
  assert.equal(r.code, 1, detail(r));
  assert.equal(pageFinding(r.report, 'contrast', '#dark-ok').verdict, 'PASS');
  assert.equal(pageFinding(r.report, 'contrast', '#top-ok').verdict, 'PASS');
  const low = pageFinding(r.report, 'contrast', '#low-fail');
  assert.equal(low.verdict, 'FAIL');
  assert.ok(low.measured.ratio < 2, `ratio ${low.measured.ratio}`);
});

test('e2e huge.html (>20000 px): klipy po 4000 px, elementy na szwach mierzone poprawnie, czas biegu rozsadny', { skip: SKIP }, () => {
  const started = Date.now();
  const r = audit('huge.config.json');
  const seconds = (Date.now() - started) / 1000;
  assert.equal(r.code, 1, detail(r));
  for (const id of ['#seam-first', '#seam-fourth', '#ok-last']) assert.equal(pageFinding(r.report, 'contrast', id).verdict, 'PASS', id);
  assert.equal(pageFinding(r.report, 'contrast', '#fail-deep').verdict, 'FAIL');
  assert.ok(seconds < 200, `bieg trwal ${seconds} s (limit calego biegu to 300 s)`);
  console.log(`# huge.html: ${seconds.toFixed(1)} s`);
});

test('e2e too-tall.html (>64000 px): UNVERIFIED page-too-tall, kod 3, bez PASS kontrastu', { skip: SKIP }, () => {
  const r = audit('too-tall.config.json');
  assert.equal(r.code, 3, detail(r));
  assert.ok(byRule(r.report, 'contrast', 'UNVERIFIED').some((f) => f.reason === 'page-too-tall'));
  assert.equal(byRule(r.report, 'contrast', 'PASS').length, 0);
  assert.ok(r.report.warnings.some((w) => w.code === 'page-too-tall'));
});

test('e2e fixed-sticky.html: element fixed i sticky mierzone w poczatkowym przewinieciu, PASS', { skip: SKIP }, () => {
  const r = audit('fixed-sticky.config.json');
  assert.equal(r.code, 0, detail(r));
  for (const id of ['#fixed-text', '#sticky-text']) assert.equal(pageFinding(r.report, 'contrast', id).verdict, 'PASS', id);
});

test('e2e lazy.html: obraz loading=lazy z data: URI pod tekstem jest zaladowany przed zrzutem (bialy tekst na czarnym PASS)', { skip: SKIP }, () => {
  const r = audit('lazy.config.json');
  assert.equal(r.code, 0, detail(r));
  const f = pageFinding(r.report, 'contrast', '#over');
  assert.equal(f.verdict, 'PASS');
  assert.ok(f.measured.ratio > 15, `ratio ${f.measured.ratio}`);
});

test('e2e clip-text.html: background-clip:text to UNVERIFIED background-clip-text, kod 3', { skip: SKIP }, () => {
  const r = audit('clip-text.config.json');
  assert.equal(r.code, 3, detail(r));
  assert.equal(pageFinding(r.report, 'contrast', '#clip').reason, 'background-clip-text');
});

test('e2e modern-colors.html: oklch, lab, hsl i display-p3 sa zamieniane na sRGB i mierzone (nieprzekształcalny kolor: tylko test jednostkowy)', { skip: SKIP }, () => {
  const r = audit('modern-colors.config.json');
  assert.equal(r.code, 1, detail(r));
  const verdict = (id) => pageFinding(r.report, 'contrast', id).verdict;
  assert.deepEqual(['#oklch-dark', '#lab-dark', '#p3-dark'].map(verdict), ['PASS', 'PASS', 'PASS']);
  assert.deepEqual(['#oklch-pale', '#hsl-pale'].map(verdict), ['FAIL', 'FAIL']);
});

test('e2e limity: 5001 elementow tekstu to UNVERIFIED truncated, maxStops=3 to UNVERIFIED sequence-truncated; kod 3', { skip: SKIP }, () => {
  const dir = project();
  const spans = Array.from({ length: 5001 }, (_, i) => `<span>t${i}</span>`).join(' ');
  fs.writeFileSync(path.join(dir, 'many.html'), `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>many</title></head><body><main>${spans}</main></body></html>`);
  fs.writeFileSync(path.join(dir, 'many.config.json'), JSON.stringify({ version: 1, pages: [{ id: 'many', url: 'many.html', assert: { selector: 'main' }, checks: ['contrast'] }] }));
  const r = audit('many.config.json', { dir });
  assert.equal(r.code, 3, detail(r));
  assert.ok(byRule(r.report, 'contrast', 'UNVERIFIED').some((f) => f.reason === 'truncated'));
  assert.ok(r.report.warnings.some((w) => w.code === 'contrast-truncated'));
  const buttons = Array.from({ length: 6 }, (_, i) => `<button type="button" style="margin:8px;padding:8px 16px">b${i}</button>`).join('');
  fs.writeFileSync(path.join(dir, 'steps.html'), `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>steps</title></head><body><main>${buttons}</main></body></html>`);
  fs.writeFileSync(path.join(dir, 'steps.config.json'), JSON.stringify({ version: 1, tab: { maxStops: 3 }, pages: [{ id: 'steps', url: 'steps.html', assert: { selector: 'main' }, checks: ['tab-order'] }] }));
  const s = audit('steps.config.json', { dir });
  assert.equal(s.code, 3, detail(s));
  assert.ok(byRule(s.report, 'tab-unreachable', 'UNVERIFIED').some((f) => f.reason === 'sequence-truncated'));
});

test('e2e frames-shadow.html: iframe (srcdoc) i host shadow DOM z 3 przystankami to przystanki bez trapu; tekst i cele w shadow root mierzone', { skip: SKIP }, () => {
  const r = audit('frames-shadow.config.json');
  assert.equal(r.code, 0, detail(r));
  assert.deepEqual(byRule(r.report, 'tab-trap'), []);
  assert.deepEqual(byRule(r.report, 'tab-unreachable').filter((f) => f.verdict !== 'PASS'), []);
  assert.ok(byRule(r.report, 'contrast', 'PASS').some((f) => f.selector.includes('my-widget') || f.selector.includes('shadow')), JSON.stringify(byRule(r.report, 'contrast').map((f) => f.selector)));
  assert.ok(byRule(r.report, 'target-size', 'PASS').some((f) => f.selector === '#s1' || f.selector.includes('my-widget')), 'cel w shadow root');
  const seq = r.report.pages[0].sequence;
  assert.ok(seq.length >= 7, `sekwencja ${seq.length}: ${seq.join(' | ')}`); // before, 2 in the frame, 3 in the widget, after
  assert.ok(r.report.warnings.some((w) => w.code === 'frames-not-audited'));
});

test('e2e mouse.html: nawigacja po kliknieciu poza dozwolone originy jest zablokowana, strona przeladowana, kolejny selektor zmierzony', { skip: SKIP }, () => {
  const r = audit('mouse.config.json');
  assert.ok([0, 3].includes(r.code), detail(r));
  const ring = byRule(r.report, 'focus-mouse-ring');
  const plain = ring.find((f) => f.selector === '#plain');
  assert.ok(plain && plain.verdict === 'INFO', JSON.stringify(ring));
  const nav = ring.find((f) => f.selector === '#nav');
  assert.ok(nav && ['UNVERIFIED', 'INFO'].includes(nav.verdict), JSON.stringify(nav));
  if (nav.verdict === 'UNVERIFIED') assert.ok(['mouse-capture-failed', 'clipped'].includes(nav.reason), JSON.stringify(nav));
  assert.ok(r.report.warnings.some((w) => w.code === 'blocked-origin' && /nav-blocked\.example\.invalid/.test(w.message)), JSON.stringify(r.report.warnings));
});

test('e2e wiele stron w jednej konfiguracji: dwa wyniki, FAIL tylko drugiej strony, kod 1', { skip: SKIP }, () => {
  const r = audit('multi.config.json');
  assert.equal(r.code, 1, detail(r));
  assert.deepEqual(r.report.pages.map((p) => p.id), ['clean', 'console-error']);
  assert.deepEqual(r.report.pages[0].findings.filter((f) => f.verdict === 'FAIL'), []);
  assert.ok(r.report.pages[1].findings.some((f) => f.rule === 'console' && f.verdict === 'FAIL'));
});

test('e2e M5: konfiguracja tylko z checks ["console"] i wyjatek daja kod 0, a PASS z wyjatkiem jest widoczny w summary i Markdown', { skip: SKIP }, () => {
  const plain = audit('console-only.config.json', { flags: NOW });
  assert.equal(plain.code, 1, detail(plain));
  const r = audit('console-only.config.json', { flags: [...NOW, '--exceptions', 'exceptions.json'] });
  assert.equal(r.code, 0, detail(r));
  assert.equal(r.report.summary.excepted, 1);
  assert.equal(r.report.summary.exceptedKeys.length, 1);
  assert.match(fs.readFileSync(path.join(r.dir, 'out', 'a11y.md'), 'utf8'), /PASS includes 1 excepted/);
});

test('e2e rtl.html (body dir=rtl): kolejnosc Tab od prawej do lewej nie jest cofnieciem (order.mode=fail), kod 0', { skip: SKIP }, () => {
  const r = audit('rtl.config.json');
  assert.equal(r.code, 0, detail(r));
  assert.deepEqual(byRule(r.report, 'tab-order-visual'), []);
});

test('e2e logowanie ze strony file: (pole, haslo ze zmiennej srodowiska, przycisk): zalogowany widok, haslo maskowane w calym wyniku', { skip: SKIP }, () => {
  const password = ['fake', 'pw', String(process.pid), 'lg1'].join('-');
  const r = audit('login.config.json', { env: { UI_VERIFY_A11Y_FIXTURE_USER: 'fixture-user', UI_VERIFY_A11Y_FIXTURE_PASSWORD: password } });
  // console.error strony aplikacji niesie haslo: to FAIL konsoli (kod 1), ale wartosc musi byc zamaskowana
  assert.equal(r.code, 1, detail(r));
  assert.equal(r.report.pages[0].assertion, 'PASS', 'zalogowany widok (asercja na #who)');
  const text = outputs(r);
  assert.ok(!text.includes(password) && !text.includes(encodeURIComponent(password)), 'haslo w wyniku');
  assert.ok(text.includes('***'), 'wartosc zastapiona maska');
  const missing = audit('login.config.json', { env: { UI_VERIFY_A11Y_FIXTURE_USER: 'fixture-user' } });
  assert.equal(missing.code, 2, detail(missing));
});

// --- originy: file: poza katalogiem konfiguracji, data:, przekierowanie 302 na .invalid (serwer tylko 127.0.0.1) ---

function runAsync(dir, args) {
  const base = { ...process.env };
  for (const k of Object.keys(base)) if (k.startsWith('UI_VERIFY_') || k === 'PLAYWRIGHT_MODULE_PATH') delete base[k];
  const env = { ...base, UI_VERIFY_CACHE_DIR: g.root, UI_VERIFY_CHROME: chromePath || '' };
  if (process.env.UI_VERIFY_NO_SANDBOX) env.UI_VERIFY_NO_SANDBOX = process.env.UI_VERIFY_NO_SANDBOX;
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [SCRIPT, ...args], { cwd: dir, env });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (d) => { stdout += d; });
    child.stderr.on('data', (d) => { stderr += d; });
    const timer = setTimeout(() => child.kill(), 240000);
    child.on('close', (code) => { clearTimeout(timer); resolve({ code, stdout, stderr }); });
  });
}

test('e2e originy: przekierowanie 302 na .invalid jest liczone jako blocked-origin (route albo zdarzenie request)', { skip: SKIP }, async () => {
  const dir = project();
  const server = http.createServer((req, res) => {
    if (req.url.startsWith('/redirect')) { res.writeHead(302, { Location: 'http://redirect-target.example.invalid/x.js' }); res.end(); return; }
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>o</title></head><body><main><p>origins</p><script src="/redirect.js"></script></main></body></html>');
  });
  await new Promise((resolve) => server.listen(Number(process.env.UI_VERIFY_TEST_PORT || 0), '127.0.0.1', resolve));
  try {
    const url = `http://127.0.0.1:${server.address().port}/page.html`;
    fs.writeFileSync(path.join(dir, 'origins.config.json'), JSON.stringify({ version: 1, pages: [{ id: 'origins', url, assert: { selector: 'main' }, checks: ['console'] }] }));
    const r = await runAsync(dir, ['--config', 'origins.config.json', 'out']);
    const file = path.join(dir, 'out', 'a11y.json');
    assert.ok(fs.existsSync(file), `${r.code}\n${r.stdout}${r.stderr}`);
    const report = JSON.parse(fs.readFileSync(file, 'utf8'));
    const warn = report.warnings.find((w) => w.code === 'blocked-origin');
    assert.ok(warn, JSON.stringify(report.warnings));
    assert.match(warn.message, /redirect-target\.example\.invalid/);
    assert.ok(!JSON.stringify(report.pages[0].findings).includes('redirect-target'), 'the blocked hop is not reported as a page error');
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test('e2e originy: file: poza katalogiem konfiguracji jest abortowany i liczony (strona file:), data: przechodzi', { skip: SKIP }, () => {
  const dir = project();
  const outside = fs.mkdtempSync(path.join(tmpRoot, 'outside-'));
  fs.writeFileSync(path.join(outside, 'pic.svg'), '<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"/>');
  fs.writeFileSync(path.join(dir, 'origins-file.html'), `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>o</title></head><body><main><p>origins</p>
<img alt="" src="${pathToFileURL(path.join(outside, 'pic.svg')).href}">
<img alt="" src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='4' height='4'/%3E"></main></body></html>`);
  fs.writeFileSync(path.join(dir, 'origins-file.config.json'), JSON.stringify({ version: 1, pages: [{ id: 'origins-file', url: 'origins-file.html', assert: { selector: 'main' }, checks: ['console'] }] }));
  const r = audit('origins-file.config.json', { dir });
  assert.ok(r.report, detail(r));
  const warn = r.report.warnings.find((w) => w.code === 'blocked-origin');
  assert.ok(warn, JSON.stringify(r.report.warnings));
  assert.match(warn.message, /blocked 1 request/, 'exactly the file: image, not the data: one');
});

test('e2e bezpieczenstwo: selektor poprawny skladniowo z cudzyslowami nie psuje biegu i jest uzyty jako selektor, nie jako kod', { skip: SKIP }, () => {
  const dir = project();
  const selector = 'main[title="a\'\\"b"], main';
  fs.writeFileSync(path.join(dir, 'quote.config.json'), JSON.stringify({ version: 1, pages: [{ id: 'quote', url: 'clean.html', assert: { selector }, checks: ['console'] }] }));
  const r = audit('quote.config.json', { dir });
  assert.equal(r.code, 0, detail(r));
  assert.equal(r.report.pages[0].assertion, 'PASS');
});

// --- review koncowe: proznia, glebokie drzewo, wyjatki celow ---

test('e2e pusta strona (sam <main>): domyslne checks i zawezone target-size daja UNVERIFIED no-samples, kod 3 (konsola sama nie wystarcza)', { skip: SKIP }, () => {
  const a = audit('empty.config.json');
  assert.equal(a.code, 3, detail(a));
  assert.equal(all(a.report).filter((f) => f.verdict === 'PASS' && f.rule !== 'console').length, 0);
  assert.ok(byRule(a.report, 'contrast', 'UNVERIFIED').some((f) => f.reason === 'no-samples'));
  const b = audit('empty-targets.config.json');
  assert.equal(b.code, 3, detail(b));
  assert.deepEqual(byRule(b.report, 'target-size').map((f) => [f.verdict, f.reason]), [['UNVERIFIED', 'no-samples']]);
});

test('e2e strona z samymi EXEMPT tekstami (display:none, opacity:0, visibility:hidden, aria-disabled): kod 3, kontrast no-samples', { skip: SKIP }, () => {
  const r = audit('exempt.config.json');
  assert.equal(r.code, 3, detail(r));
  assert.equal(byRule(r.report, 'contrast', 'PASS').length + byRule(r.report, 'contrast', 'FAIL').length, 0);
  assert.ok(byRule(r.report, 'contrast', 'EXEMPT').length >= 3);
  assert.ok(byRule(r.report, 'contrast', 'UNVERIFIED').some((f) => f.reason === 'no-samples'));
});

test('e2e gleboki DOM (60 poziomow): dwa sasiednie cele 20x20 daja dwa FAIL z roznymi selektorami (<= 400 znakow)', { skip: SKIP }, () => {
  const dir = project();
  const open = Array.from({ length: 60 }, (_, i) => `<section class="level-wrapper-number-${i}">`).join('');
  const close = '</section>'.repeat(60);
  fs.writeFileSync(path.join(dir, 'deep.html'), `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>deep</title><style>button{width:20px;height:20px;padding:0;margin:0 2px 0 0;vertical-align:top}</style></head><body><main>${open}<button type="button" aria-label="One">1</button><button type="button" aria-label="Two">2</button>${close}</main></body></html>`);
  fs.writeFileSync(path.join(dir, 'deep.config.json'), JSON.stringify({ version: 1, pages: [{ id: 'deep', url: 'deep.html', assert: { selector: 'main' }, checks: ['target-size'] }] }));
  const r = audit('deep.config.json', { dir });
  assert.equal(r.code, 1, detail(r));
  const fails = byRule(r.report, 'target-size', 'FAIL');
  assert.equal(fails.length, 2, JSON.stringify(fails.map((f) => f.selector)));
  assert.notEqual(fails[0].selector, fails[1].selector);
  assert.ok(fails.every((f) => f.selector.length <= 400));
  assert.notEqual(fails[0].key, fails[1].key);
});

test('e2e clean.html: wyjatki celow PASS niosa pole exception (spacing, equivalent-label)', { skip: SKIP }, () => {
  const r = audit('clean.config.json');
  assert.equal(r.code, 0, detail(r));
  const ex = byRule(r.report, 'target-size', 'PASS').map((f) => f.exception).filter(Boolean);
  assert.ok(ex.includes('equivalent-label'), JSON.stringify(ex));
});

// ---------------------------------------------------------------------------------------------------------------
// Focus indicator contrast and appearance (CORE-1150-T2B, K4): fixture focus-contrast.html, expectations computed by
// hand in expected-focus.json.
// ---------------------------------------------------------------------------------------------------------------

const FOCUS = JSON.parse(fs.readFileSync(path.join(FIXTURES, 'expected-focus.json'), 'utf8'));
const OPT_IN = ['focus-contrast', 'focus-appearance'];

// A copy of the fixture project with the configuration rewritten to the given mode (null: without the two keys) and,
// with `keep`, only those targets left on the page.
function focusProject(mode, keep = null) {
  const dir = project();
  const config = JSON.parse(fs.readFileSync(path.join(dir, 'focus-contrast.config.json'), 'utf8'));
  if (mode === null) delete config.focus;
  else config.focus = { contrast: mode, appearance: mode };
  fs.writeFileSync(path.join(dir, 'focus-contrast.config.json'), JSON.stringify(config));
  if (keep !== null) {
    const file = path.join(dir, 'focus-contrast.html');
    const lines = fs.readFileSync(file, 'utf8').split('\n');
    const kept = lines.filter((line) => !/^<(button|div) /.test(line) || keep.some((id) => line.includes(`id="${id}"`)));
    assert.equal(kept.filter((line) => /^<(button|div) /.test(line)).length, keep.length, 'every kept target is one line of the fixture');
    fs.writeFileSync(file, kept.join('\n'));
  }
  return dir;
}

const focusRuns = new Map();
function focusRun(mode) {
  if (!focusRuns.has(mode)) focusRuns.set(mode, audit('focus-contrast.config.json', { dir: focusProject(mode) }));
  return focusRuns.get(mode);
}

const focusFinding = (report, rule, id) => {
  const found = byRule(report, rule).filter((f) => f.selector === `#${id}`);
  assert.ok(found.length <= 1, `${rule} #${id}: ${found.length} findings`);
  return found[0] ?? null;
};
const near = (actual, expected, tolerance, label) => assert.ok(Number.isFinite(actual) && Math.abs(actual - expected) <= tolerance, `${label}: ${actual}, expected ${expected} +- ${tolerance}`);

test('e2e focus: every target of focus-contrast.html is reached and the keyboard indicator verdicts are as computed', { skip: SKIP }, () => {
  const r = focusRun('fail');
  assert.equal(r.code, 1, detail(r));
  const got = byRule(r.report, 'focus-keyboard-indicator').map((f) => [f.selector, f.verdict]).sort();
  assert.deepEqual(got, FOCUS.elements.map((e) => [`#${e.id}`, e.keyboard]).sort());
});

for (const e of FOCUS.elements) {
  test(`e2e focus fail mode #${e.id}: focus-contrast and focus-appearance as computed (${e.why})`, { skip: SKIP }, () => {
    const r = focusRun('fail');
    assert.ok(r.report, detail(r));
    const contrast = focusFinding(r.report, 'focus-contrast', e.id);
    const appearance = focusFinding(r.report, 'focus-appearance', e.id);
    if (e.contrast === null) {
      assert.equal(focusFinding(r.report, 'focus-keyboard-indicator', e.id)?.verdict, 'FAIL', 'precondition: the old rule fails');
      assert.deepEqual([contrast, appearance], [null, null], 'no second finding for a missing indicator');
      return;
    }
    assert.ok(contrast && appearance, `both rules report #${e.id}: ${JSON.stringify(all(r.report).filter((f) => f.selector === `#${e.id}`))}`);
    console.log(`# focus #${e.id}: contrast ${contrast.verdict} ${JSON.stringify(contrast.measured)}; appearance ${appearance.verdict} ${appearance.reason ?? ''} ${JSON.stringify(appearance.measured)}`);
    assert.equal(contrast.basis, 'wcag');
    assert.equal(appearance.basis, 'wcag-aaa');
    assert.equal(contrast.verdict, e.contrast.verdict, 'focus-contrast verdict');
    assert.equal(contrast.reason ?? null, e.contrast.reason ?? null, 'focus-contrast reason');
    if (e.appearance.anyOf) assert.ok(e.appearance.anyOf.includes(appearance.verdict), `focus-appearance ${appearance.verdict} ${appearance.reason ?? ''}`);
    else assert.equal(appearance.verdict, e.appearance.verdict, 'focus-appearance verdict');
    assert.equal(appearance.reason ?? null, e.appearance.reason ?? null, 'focus-appearance reason');
    if (e.contrast.verdict !== 'UNVERIFIED') assert.equal(contrast.required, 3);
    if (e.requiredArea !== undefined) {
      near(appearance.measured.requiredArea, e.requiredArea, FOCUS.numberTolerance, 'requiredArea');
      near(appearance.measured.threshold, e.threshold, FOCUS.numberTolerance, 'threshold');
      near(appearance.required, e.threshold, FOCUS.numberTolerance, 'required');
    }
    if (e.ringBeside) {
      // a 1 x 1 control: the pixels are those of the ring on its neighbour, and no area is judged
      near(contrast.measured.diffPx, e.ringBeside.area, e.ringBeside.tolerancePx, 'diffPx');
      assert.equal(appearance.measured, null);
    }
    if (e.ring) {
      // The tolerance is for the measured area only; the verdicts above are exact. Antialiased edge pixels count in full:
      // for #circle-two (2 px ring on a 24 px circle) Chrome on Linux reports 156 px against 138.2 analytic, hence 24.
      near(appearance.measured.qualifyingAA, e.ring.qualifying ?? e.ring.area, e.ring.tolerancePx, 'qualifyingAA');
      // straight rings on integer coordinates have no antialiased pixels: the strict count equals the area too
      if (e.box.radius === 0) {
        near(contrast.measured.diffPx, e.ring.area, e.ring.tolerancePx, 'diffPx');
        near(contrast.measured.qualifyingStrict, e.ring.qualifying ?? e.ring.area, e.ring.tolerancePx, 'qualifyingStrict');
        near(contrast.measured.ratioMedian, e.ring.ratio, 0.05, 'ratioMedian');
      }
    }
  });
}

test('e2e focus report mode: FAIL and UNVERIFIED of the opt-in rules become INFO with the same reason, PASS stays, the old rule is unchanged', { skip: SKIP }, () => {
  const fail = focusRun('fail');
  const report = focusRun('report');
  assert.equal(report.code, 1, detail(report)); // the two keyboard FAILs of the page
  const view = (r) => all(r.report).filter((f) => OPT_IN.includes(f.rule)).map((f) => ({ rule: f.rule, selector: f.selector, verdict: f.verdict, reason: f.reason ?? null }));
  const expected = view(fail).map((f) => ({ ...f, verdict: f.verdict === 'PASS' ? 'PASS' : 'INFO' }));
  assert.ok(expected.some((f) => f.verdict === 'INFO' && f.reason === 'clipped') && expected.some((f) => f.verdict === 'INFO' && f.reason === 'shape-unknown'), JSON.stringify(expected));
  assert.deepEqual(view(report), expected);
  const keyboard = (r) => byRule(r.report, 'focus-keyboard-indicator').map((f) => [f.selector, f.verdict]);
  assert.deepEqual(keyboard(report), keyboard(fail));
  const md = fs.readFileSync(path.join(report.dir, 'out', 'a11y.md'), 'utf8');
  const informational = md.split('## Informational')[1]?.split('\n## ')[0] ?? '';
  assert.match(informational, /\[focus-contrast\][^\n]*#pale/);
  assert.match(informational, /\[focus-appearance\][^\n]*#clipped[^\n]*clipped/);
});

test('e2e focus without the new keys: no opt-in finding, the same keyboard verdicts', { skip: SKIP }, () => {
  const off = focusRun(null);
  const fail = focusRun('fail');
  assert.equal(off.code, 1, detail(off));
  assert.ok(all(fail.report).some((f) => OPT_IN.includes(f.rule)), 'control: with the keys the rules are reported');
  assert.deepEqual(all(off.report).filter((f) => OPT_IN.includes(f.rule)), []);
  const keyboard = (r) => byRule(r.report, 'focus-keyboard-indicator').map((f) => [f.selector, f.verdict, f.measured.diffPx]);
  assert.deepEqual(keyboard(off), keyboard(fail));
});

for (const [name, keep, codes] of [
  ['a pale ring and unmeasurable targets', ['ring-outer', 'pale', 'clipped', 'wrapped-link'], { off: 0, report: 0, fail: 1 }],
  ['only unmeasurable targets', ['ring-outer', 'clipped', 'wrapped-link'], { off: 0, report: 0, fail: 3 }],
]) {
  test(`e2e focus exit codes, ${name}: report never changes the exit code (off ${codes.off}, report ${codes.report}, fail ${codes.fail})`, { skip: SKIP }, () => {
    for (const [label, mode] of [['off', null], ['report', 'report'], ['fail', 'fail']]) {
      const r = audit('focus-contrast.config.json', { dir: focusProject(mode, keep) });
      assert.equal(r.code, codes[label], `${label}: ${detail(r)}`);
    }
  });
}

// ---------------------------------------------------------------------------------------------------------------
// Non-text contrast (CORE-1150-T2B, K7): fixtures non-text.html and non-text-empty.html, expectations computed by
// hand in expected-nontext.json.
// ---------------------------------------------------------------------------------------------------------------

const NONTEXT = JSON.parse(fs.readFileSync(path.join(FIXTURES, 'expected-nontext.json'), 'utf8'));
const NONTEXT_RULE = 'non-text-contrast';

let nonTextRunCache = null;
function nonTextRun() {
  nonTextRunCache ??= audit('non-text.config.json', { flags: NOW });
  return nonTextRunCache;
}
const nonTextView = (report) => all(report).map((f) => [f.selector, f.verdict, f.reason ?? null, f.measured?.kind ?? null]);
const nonTextWarnings = (report) => report.warnings.filter((w) => /nontext|layout-shift|page-too-tall/.test(w.code));

test('e2e non-text: only the listed elements are reported, by the new rule alone, and the FAILs give exit 1', { skip: SKIP }, () => {
  const r = nonTextRun();
  assert.equal(r.code, 1, detail(r));
  assert.equal(r.report.result, 'FAIL');
  assert.deepEqual([...new Set(all(r.report).map((f) => f.rule))], [NONTEXT_RULE]);
  console.log(`# non-text findings: ${JSON.stringify(nonTextView(r.report))}`);
  // one finding per element; controls judged on their graphic (expected.silent) give none
  assert.deepEqual(all(r.report).map((f) => f.selector).sort(), NONTEXT.elements.map((e) => `#${e.id}`).sort());
  assert.deepEqual(nonTextWarnings(r.report), []);
});

for (const e of NONTEXT.elements) {
  test(`e2e non-text #${e.id}: ${e.verdict} ${e.reason ?? e.kind} (${e.why})`, { skip: SKIP }, () => {
    const r = nonTextRun();
    assert.ok(r.report, detail(r));
    const found = byRule(r.report, NONTEXT_RULE).filter((f) => f.selector === `#${e.id}`);
    assert.equal(found.length, 1, `#${e.id}: ${JSON.stringify(found)}`);
    const [f] = found;
    const got = { verdict: f.verdict, reason: f.reason ?? null };
    assert.equal(f.basis, 'wcag');
    assert.deepEqual(got, { verdict: e.verdict, reason: e.reason ?? null });
    // the key names the kind only for a measured finding
    assert.equal(f.key, `${NONTEXT_RULE}|non-text|#${e.id}${e.kind ? `|${e.kind}` : ''}`);
    if (!e.kind) {
      assert.equal(f.measured, null);
      return;
    }
    assert.equal(f.measured.kind, e.kind);
    assert.equal(f.required, NONTEXT.required);
    for (const [name, value] of Object.entries(e.measured)) {
      if (value === null) assert.equal(f.measured[name], null, name);
      else near(f.measured[name], value, e.tolerance?.[name] ?? NONTEXT.ratioTolerance, `${e.id} ${name}`);
    }
    const notes = String(f.note ?? '').split(', ');
    assert.equal(notes.includes('sampled'), e.note === 'sampled', `note: ${f.note}`);
  });
}

test('e2e non-text #field-focus: the reason is the focus, and only that element is affected by it', { skip: SKIP }, () => {
  const r = nonTextRun();
  assert.ok(r.report, detail(r));
  const focused = byRule(r.report, NONTEXT_RULE).filter((f) => /the element had focus while measured/.test(String(f.note)));
  assert.deepEqual(focused.map((f) => f.selector), ['#field-focus']);
});

function nonTextExceptionRun(kind) {
  const dir = project();
  const entry = { rule: NONTEXT_RULE, page: 'non-text', selector: '#field-pale', kind, reason: 'the label identifies the field in the fixture', expires: '2027-06-30' };
  fs.writeFileSync(path.join(dir, 'non-text-exceptions.json'), JSON.stringify({ version: 1, exceptions: [entry] }));
  return audit('non-text.config.json', { dir, flags: [...NOW, '--exceptions', 'non-text-exceptions.json'] });
}

test('e2e non-text exception with kind: exactly that FAIL becomes EXCEPTED, every other finding stays', { skip: SKIP }, () => {
  const plain = nonTextRun();
  const r = nonTextExceptionRun('boundary');
  assert.equal(r.code, 1, detail(r)); // other FAILs of the page remain
  const expected = nonTextView(plain.report).map((v) => (v[0] === '#field-pale' ? [v[0], 'EXCEPTED', v[2], v[3]] : v));
  assert.equal(nonTextView(plain.report).find((v) => v[0] === '#field-pale')?.[1], 'FAIL', 'precondition');
  assert.deepEqual(nonTextView(r.report), expected);
  assert.deepEqual(r.report.exceptions.map((x) => x.status), ['used']);
});

test('e2e non-text exception of another kind: the FAIL stays (control of the exception test)', { skip: SKIP }, () => {
  const r = nonTextExceptionRun('icon');
  assert.equal(r.code, 1, detail(r));
  assert.deepEqual(byRule(r.report, NONTEXT_RULE).filter((f) => f.selector === '#field-pale').map((f) => f.verdict), ['FAIL']);
  assert.deepEqual(byRule(r.report, NONTEXT_RULE, 'EXCEPTED'), []);
  assert.deepEqual(r.report.exceptions.map((x) => x.status), ['unused']);
});

test('e2e non-text K1: the same page without "non-text" in checks reports no non-text-contrast finding', { skip: SKIP }, () => {
  const control = nonTextRun();
  assert.ok(byRule(control.report, NONTEXT_RULE).length > 0, 'control: with the check the rule is reported');
  const dir = project();
  const file = path.join(dir, 'non-text.config.json');
  const config = JSON.parse(fs.readFileSync(file, 'utf8'));
  delete config.pages[0].checks; // the default checks
  fs.writeFileSync(file, JSON.stringify(config));
  const r = audit('non-text.config.json', { dir, flags: NOW });
  assert.ok(r.report, detail(r));
  assert.ok(byRule(r.report, 'contrast').length > 0 && byRule(r.report, 'target-size').length > 0, 'the default checks ran');
  assert.deepEqual(byRule(r.report, NONTEXT_RULE), []);
  assert.deepEqual(nonTextWarnings(r.report).filter((w) => w.code.startsWith('nontext')), []);
});

test('e2e non-text on a page without candidates: UNVERIFIED no-samples and exit 3, never a vacuous PASS', { skip: SKIP }, () => {
  const r = audit('non-text-empty.config.json', { flags: NOW });
  assert.equal(r.code, 3, detail(r));
  assert.equal(r.report.result, 'UNVERIFIED');
  assert.deepEqual(all(r.report).map((f) => [f.rule, f.selector, f.verdict, f.reason]), [[NONTEXT_RULE, null, 'UNVERIFIED', 'no-samples']]);
  assert.deepEqual(nonTextWarnings(r.report), []);
});
