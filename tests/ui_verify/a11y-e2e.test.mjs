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
  for (const id of ['#seam-4000', '#seam-16000', '#ok-last']) assert.equal(pageFinding(r.report, 'contrast', id).verdict, 'PASS', id);
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
  const r = audit('login.config.json', { env: { A11Y_FIXTURE_USER: 'fixture-user', A11Y_FIXTURE_PASSWORD: password } });
  // console.error strony aplikacji niesie haslo: to FAIL konsoli (kod 1), ale wartosc musi byc zamaskowana
  assert.equal(r.code, 1, detail(r));
  assert.equal(r.report.pages[0].assertion, 'PASS', 'zalogowany widok (asercja na #who)');
  const text = outputs(r);
  assert.ok(!text.includes(password) && !text.includes(encodeURIComponent(password)), 'haslo w wyniku');
  assert.ok(text.includes('***'), 'wartosc zastapiona maska');
  const missing = audit('login.config.json', { env: { A11Y_FIXTURE_USER: 'fixture-user' } });
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

test('e2e originy: file: poza katalogiem konfiguracji i przekierowanie 302 na .invalid sa abortowane, data: przechodzi', { skip: SKIP }, async () => {
  const dir = project();
  const outside = fs.mkdtempSync(path.join(tmpRoot, 'outside-'));
  fs.writeFileSync(path.join(outside, 'pic.svg'), '<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"/>');
  const hits = [];
  const server = http.createServer((req, res) => {
    hits.push(req.url);
    if (req.url.startsWith('/redirect')) { res.writeHead(302, { Location: 'http://redirect-target.example.invalid/x.js' }); res.end(); return; }
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end(`<!doctype html><html lang="en"><head><meta charset="utf-8"><title>o</title></head><body><main><p>origins</p>
<img alt="" src="${pathToFileURL(path.join(outside, 'pic.svg')).href}">
<img alt="" src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='4' height='4'/%3E">
<script src="/redirect.js"></script></main></body></html>`);
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
    assert.match(warn.message, /file/);
    assert.ok(!report.warnings.some((w) => /data:/.test(w.message)), 'data: nie jest blokowane');
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test('e2e bezpieczenstwo: selektor poprawny skladniowo z cudzyslowami nie psuje biegu i jest uzyty jako selektor, nie jako kod', { skip: SKIP }, () => {
  const dir = project();
  const selector = 'main[title="a\'\\"b"], main';
  fs.writeFileSync(path.join(dir, 'quote.config.json'), JSON.stringify({ version: 1, pages: [{ id: 'quote', url: 'clean.html', assert: { selector }, checks: ['console'] }] }));
  const r = audit('quote.config.json', { dir });
  assert.equal(r.code, 0, detail(r));
  assert.equal(r.report.pages[0].assertion, 'PASS');
});
