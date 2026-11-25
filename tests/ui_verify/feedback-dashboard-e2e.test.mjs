// E2E w prawdziwym Chrome: ekran zgloszen FEEDBACK na stronie statusu (CORE-1142-FB-BUTTON, S6, kryterium FB8).
// Fixture: tests/fixtures/feedback_dashboard_fixture.py (projekt z manifestem, warstwa org z sekcja `feedback:`, zaufany cel w konfiguracji
// maszyny, szkice z prawdziwymi podgladami; bez sieci i bez prawdziwego `az`). Serwer: `skillsync dashboard serve` na porcie 127.0.0.1 z puli
// 5151-5199, adres z tokenem z `skillsync dashboard url`; proces konczony po PID z uchwytu (nigdy po nazwie).
// Pomijane z powodem "e2e-chrome: ..." gdy brak Chrome (UI_VERIFY_CHROME albo system) lub UI_VERIFY_TEST_CACHE z playwright-core,
// pngjs i pixelmatch, oraz "needs-python: ..." gdy brak Pythona 3.11+ z PyYAML. W CI to brak srodowiska = blad (strazniki pominiec).
import test, { after, before } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const SKILL = path.join(ROOT, 'library', 'skills', 'ui-verify');
const LIB = pathToFileURL(path.join(SKILL, 'scripts', 'lib')).href;
const FIXTURE = path.join(ROOT, 'tests', 'fixtures', 'feedback_dashboard_fixture.py');
const CLI = path.join(ROOT, 'skillsync');
const PORT_MIN = 5151;
const PORT_MAX = 5199;

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-fbdash-e2e-'));
let counter = 0;
const children = new Set(); // uchwyty serwerow tego pliku: sprzatanie po PID z uchwytu, nigdy po nazwie

// ---------------------------------------------------------------- warunki uruchomienia (jak a11y-e2e)

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

function findPython() {
  for (const name of process.platform === 'win32' ? ['python', 'py'] : ['python3', 'python']) {
    const r = spawnSync(name, ['-c', 'import sys, yaml; sys.exit(0 if sys.version_info >= (3, 11) else 1)'], { encoding: 'utf8' });
    if (r.status === 0) return name;
  }
  return null;
}
const PY = findPython();

async function findChromePath() {
  if (g.reason) return null;
  const { findChrome } = await import(`${LIB}/chrome.mjs`);
  const which = (name) => {
    const r = spawnSync(process.platform === 'win32' ? 'where' : 'which', [name], { encoding: 'utf8' });
    return r.status === 0 ? r.stdout.split(/\r?\n/)[0].trim() || null : null;
  };
  return findChrome({ platform: process.platform, env: process.env, fs, which }).path;
}
const chromePath = await findChromePath();
const SKIP = g.reason || (chromePath ? (PY ? false : 'needs-python: brak Pythona 3.11+ z PyYAML w PATH') : 'e2e-chrome: brak Chrome (ustaw UI_VERIFY_CHROME)');

let browser = null;
before(async () => {
  if (SKIP) return;
  const env = { ...process.env, UI_VERIFY_CACHE_DIR: g.root, UI_VERIFY_CHROME: chromePath };
  const { resolveCacheDir } = await import(`${LIB}/runtime.mjs`);
  const { launchChromium } = await import(`${LIB}/playwright-adapter.mjs`);
  const cacheDir = resolveCacheDir({ skillDir: SKILL, env, platform: process.platform, homedir: os.homedir() });
  browser = await launchChromium({ cacheDir, chromePath, env, fs });
});
after(async () => {
  if (browser) await browser.close().catch(() => {});
  for (const child of [...children]) await stop(child);
  fs.rmSync(tmpRoot, { recursive: true, force: true });
});

// ---------------------------------------------------------------- fixture, serwer, adres

function runPy(args, env, { code = 0 } = {}) {
  const r = spawnSync(PY, [...(PY === 'py' ? ['-3'] : []), ...args], { env, encoding: 'utf8', timeout: 120000, cwd: ROOT });
  assert.equal(r.status, code, `${args.join(' ')}\n${r.stdout}${r.stderr}`);
  return r;
}

// Srodowisko procesow potomnych: konfiguracja maszyny fixture, brak sesji karty (karta nie zapisuje zgod), kodowanie UTF-8.
function childEnv(fx) {
  const env = { ...process.env, ...fx.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' };
  delete env.SKILLSYNC_CARD_ALIAS;
  return env;
}

function makeFixture(...kinds) {
  const base = path.join(tmpRoot, `fx-${counter++}`);
  fs.mkdirSync(base);
  const r = runPy([FIXTURE, path.join(base, 'b'), ...kinds], { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' });
  const fx = JSON.parse(r.stdout);
  fx.store = path.join(fx.root, '.skillsync', 'feedback');
  fx.approvals = () => {
    const file = path.join(fx.store, 'approvals.jsonl');
    return fs.existsSync(file) ? fs.readFileSync(file, 'utf8').split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l)) : [];
  };
  return fx;
}

const freePort = (port) => new Promise((resolve) => {
  const srv = net.createServer();
  srv.once('error', () => resolve(false));
  srv.listen(port, '127.0.0.1', () => srv.close(() => resolve(true)));
});

async function pickPorts(count) {
  const found = [];
  const start = PORT_MIN + Math.floor(Math.random() * (PORT_MAX - PORT_MIN + 1));
  for (let i = 0; i <= PORT_MAX - PORT_MIN && found.length < count; i++) {
    const port = PORT_MIN + ((start - PORT_MIN + i) % (PORT_MAX - PORT_MIN + 1));
    if (await freePort(port)) found.push(port);
  }
  assert.equal(found.length, count, 'brak wolnych portow w puli 5151-5199');
  return found;
}

async function healthy(port) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/healthz`);
      if (r.ok) return true;
    } catch { /* jeszcze nie nasluchuje */ }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  return false;
}

async function stop(child) {
  children.delete(child);
  if (child.exitCode !== null || child.signalCode !== null) return;
  const done = new Promise((resolve) => child.once('exit', resolve));
  child.kill('SIGTERM');
  const timer = setTimeout(() => child.kill('SIGKILL'), 8000);
  await done;
  clearTimeout(timer);
}

// `portArgs(ports)` -> argumenty portow; powtarza z innymi portami, gdy serwer sie nie podniesie (zajety port to nie blad testu).
async function startServer(fx, { count = 1, portArgs }) {
  let last = '';
  for (let attempt = 0; attempt < 4; attempt++) {
    const ports = await pickPorts(count);
    const child = spawn(PY, [...(PY === 'py' ? ['-3'] : []), CLI, 'dashboard', 'serve', fx.root, ...portArgs(ports), '--poll', '0.2'],
      { env: childEnv(fx), stdio: ['ignore', 'pipe', 'pipe'], cwd: ROOT });
    children.add(child);
    let log = '';
    child.stdout.on('data', (d) => { log += d; });
    child.stderr.on('data', (d) => { log += d; });
    const early = new Promise((resolve) => child.once('exit', () => resolve(false)));
    const ok = await Promise.race([Promise.all(ports.map(healthy)).then((all) => all.every(Boolean)), early]);
    if (ok) return { child, ports, log: () => log };
    last = log;
    await stop(child);
  }
  assert.fail(`serwer strony statusu sie nie podniosl:\n${last.slice(-1500)}`);
}

// `/healthz` odpowiada, zanim serwer zapisze plik stanu, z ktorego czyta `dashboard url`; krotko po starcie daje on `server_not_running`.
// Czekamy na plik stanu z limitem czasu (tylko ten kod; kazdy inny wynik jest bledem od razu).
const sleepSync = (ms) => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);

function tokenUrl(fx, port, seconds = 20) {
  const deadline = Date.now() + seconds * 1000;
  for (;;) {
    const r = runPy([CLI, 'dashboard', 'url', fx.root, '--port', String(port), '--json'], childEnv(fx));
    const answer = JSON.parse(r.stdout);
    if (answer.status === 'BLOCKED' && answer.code === 'server_not_running' && Date.now() < deadline) {
      sleepSync(150);
      continue;
    }
    assert.equal(answer.status, 'PASS', r.stdout);
    return answer.url;
  }
}

// Jeden serwer w trybie pojedynczego portu (rola z naglowka Host: loopback = zgody) i jego adres z tokenem.
async function serveSingle(fx) {
  const server = await startServer(fx, { portArgs: ([p]) => ['--port', String(p)] });
  return { ...server, url: tokenUrl(fx, server.ports[0]) };
}

// ---------------------------------------------------------------- przegladarka

const errorsOf = new WeakMap();
async function openPage(url, { scheme = 'light', width = 1100, height = 900 } = {}) {
  const context = await browser.newContext({ viewport: { width, height }, colorScheme: scheme, locale: 'pl-PL', reducedMotion: 'reduce' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push(`console: ${m.text()}`); });
  errorsOf.set(page, errors);
  await page.goto(url);
  return { page, context, errors };
}
const card = (draft) => `article.fb-card[data-draft="${draft}"]`;
const state = (draft, st) => `article.fb-card[data-draft="${draft}"][data-state="${st}"]`;
const TIMEOUT = 20000;
const waitState = (page, draft, st) => page.waitForSelector(state(draft, st), { timeout: TIMEOUT });
const text = (page, selector) => page.locator(selector).first().innerText();

// Kontrast: kolory z obliczonego stylu przez kanwe (kazdy format koloru), tlo zlozone z przodkow; zwraca stosunek WCAG.
const lum = ([r, g, b]) => {
  const f = (c) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
};
function ratio(fg, bg) {
  const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x);
  return (a + 0.05) / (b + 0.05);
}
async function colorsOf(page, selector) {
  return page.evaluate((sel) => {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 1;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    const rgba = (css) => {
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = '#000';
      ctx.fillStyle = css;
      ctx.fillRect(0, 0, 1, 1);
      const d = ctx.getImageData(0, 0, 1, 1).data;
      return [d[0], d[1], d[2], d[3] / 255];
    };
    const el = document.querySelector(sel);
    if (!el) return null;
    const layers = [];
    for (let n = el; n; n = n.parentElement) {
      const c = rgba(getComputedStyle(n).backgroundColor);
      if (c[3] > 0) layers.push(c);
      if (c[3] === 1) break;
    }
    let bg = [255, 255, 255];
    for (const c of layers.reverse()) bg = [0, 1, 2].map((i) => c[i] * c[3] + bg[i] * (1 - c[3]));
    return { fg: rgba(getComputedStyle(el).color).slice(0, 3), bg };
  }, selector);
}
async function assertContrast(page, selector, label, min = 4.5) {
  const c = await colorsOf(page, selector);
  assert.ok(c, `${label}: brak elementu ${selector}`);
  const r = ratio(c.fg, c.bg);
  assert.ok(r >= min, `${label} (${selector}): kontrast ${r.toFixed(2)} < ${min} (tekst ${c.fg}, tlo ${c.bg.map(Math.round)})`);
  return r;
}

const inDialog = (page) => page.evaluate(() => Boolean(document.activeElement && document.activeElement.closest('.fb-dialog')));
const activeDescr = (page) => page.evaluate(() => {
  const a = document.activeElement;
  return a ? `${a.tagName.toLowerCase()}${a.id ? '#' + a.id : ''}${a.className ? '.' + String(a.className).split(' ').join('.') : ''}` : 'brak';
});

// ---------------------------------------------------------------- (1) kafelek waiting

test('FB8-1: kafelek waiting pokazuje sha12, cel, tresc w <pre> jako zwykly tekst i aktywny przycisk', { skip: SKIP }, async () => {
  const fx = makeFixture('full');
  const srv = await serveSingle(fx);
  const { page, context, errors } = await openPage(srv.url);
  try {
    await waitState(page, 'F1', 'waiting');
    assert.equal((await text(page, `${card('F1')} .fb-sha`)).trim(), fx.drafts.F1.sha12);
    const target = await text(page, `${card('F1')} .fb-target`);
    assert.match(target, /example-org/);
    assert.match(target, /ExampleProject/);
    assert.match(await text(page, `${card('F1')} .fb-state`), /Czeka na zgodę/);
    const body = await page.$eval(`${card('F1')} pre.fb-body`, (pre) => ({ tag: pre.tagName, children: pre.children.length, text: pre.textContent }));
    assert.equal(body.tag, 'PRE');
    assert.equal(body.children, 0, 'tresc jako zwykly tekst, bez elementow');
    assert.match(body.text, /Add the check to the step\./);
    const button = page.locator(`${card('F1')} button.fb-approve`);
    assert.equal(await button.count(), 1);
    assert.ok(await button.isVisible());
    assert.ok(await button.isEnabled());
    assert.equal(await page.locator('.fb-dialog').count(), 0, 'okno potwierdzenia nie jest otwarte bez klikniecia');
    assert.deepEqual(errors, []);
    assert.deepEqual(fx.approvals(), [], 'samo wyswietlenie nic nie zapisuje');
  } finally {
    await context.close();
    await stop(srv.child);
  }
});

// ---------------------------------------------------------------- (2) zgoda: okno, komunikat, stan after, jeden wiersz, feedback wait

test('FB8-2: klik, okno z sha12, Zgoda: komunikat, stan after, dokladnie jeden wiersz source=dashboard, `feedback wait` konczy 0', { skip: SKIP }, async () => {
  const fx = makeFixture('full');
  const srv = await serveSingle(fx);
  const { page, context, errors } = await openPage(srv.url);
  try {
    await waitState(page, 'F1', 'waiting');
    assert.deepEqual(fx.approvals(), []);
    await page.click(`${card('F1')} button.fb-approve`);
    await page.waitForSelector('.fb-dialog', { timeout: TIMEOUT });
    assert.equal((await text(page, '.fb-dialog .fb-d-sha')).trim(), fx.drafts.F1.sha12);
    assert.match(await text(page, '.fb-dialog .fb-d-target'), /example-org \/ ExampleProject/);
    assert.equal(await page.locator('.fb-dialog .fb-generic-warning').count(), 0, 'pokrycie full: bez ostrzezenia generic');
    assert.deepEqual(fx.approvals(), [], 'samo otwarcie okna nic nie zapisuje');
    await page.click('.fb-dialog button.fb-confirm');
    await page.waitForFunction(() => /Zgoda zapisana/.test(document.querySelector('.fb-result')?.textContent || ''), null, { timeout: TIMEOUT });
    assert.match(await text(page, '.fb-result'), /F1/);
    await waitState(page, 'F1', 'after');
    assert.equal(await page.locator(`${card('F1')} button.fb-approve`).count(), 0, 'po zgodzie nie ma przycisku');
    assert.match(await text(page, `${card('F1')} .fb-given`), /źródło: dashboard/);
    assert.equal(await page.locator('.fb-dialog').count(), 0, 'okno zamkniete po zapisie');
    const rows = fx.approvals();
    assert.equal(rows.length, 1, JSON.stringify(rows));
    assert.equal(rows[0].source, 'dashboard');
    assert.equal(rows[0].draft, 'F1');
    assert.equal(rows[0].sha, fx.drafts.F1.sha, 'pelny sha, nie sha12');
    assert.equal(rows[0].sha.length, 64);
    // wspolna sciezka z CLI: `feedback wait` widzi zgode ze strony (kod 0), nic nie zapisuje
    const wait = spawnSync(PY, [...(PY === 'py' ? ['-3'] : []), CLI, 'feedback', 'wait', 'F1', fx.root, '--timeout', '10', '--interval', '0.5'],
      { env: childEnv(fx), encoding: 'utf8', timeout: 60000, cwd: ROOT });
    assert.equal(wait.status, 0, wait.stdout + wait.stderr);
    assert.equal(fx.approvals().length, 1, 'wait nie dopisuje wierszy');
    assert.deepEqual(errors, []);
  } finally {
    await context.close();
    await stop(srv.child);
  }
});

// ---------------------------------------------------------------- (3) szkic zmieniony po podgladzie: stale

test('FB8-3: szkic zmieniony po podgladzie => po odswiezeniu stale, bez przycisku i tresci, komunikat "podglad nieaktualny"', { skip: SKIP }, async () => {
  const fx = makeFixture('full', 'stale'); // F2 jest stale od poczatku, F1 stanie sie stale w tescie
  const srv = await serveSingle(fx);
  const { page, context, errors } = await openPage(srv.url);
  try {
    await waitState(page, 'F1', 'waiting');
    await waitState(page, 'F2', 'stale');
    // przypadek kontrolny: ten sam kafelek w stanie waiting MA przycisk i tresc (asercje negatywne nizej potrafia padac)
    assert.equal(await page.locator(`${card('F1')} button.fb-approve`).count(), 1);
    assert.equal(await page.locator(`${card('F1')} pre.fb-body`).count(), 1);
    // F2 (stale od poczatku)
    assert.equal(await page.locator(`${card('F2')} button.fb-approve`).count(), 0);
    assert.equal(await page.locator(`${card('F2')} pre.fb-body`).count(), 0);
    assert.match(await text(page, card('F2')), /Podgląd nieaktualny/);
    // zmiana F1 po podgladzie (to robi czlowiek albo agent edytujac szkic), potem odswiezenie strony
    const file = path.join(fx.store, 'F1.json');
    const draft = JSON.parse(fs.readFileSync(file, 'utf8'));
    fs.writeFileSync(file, JSON.stringify({ ...draft, proposal: 'Changed after the preview, so the preview no longer matches.' }));
    await page.reload();
    await waitState(page, 'F1', 'stale');
    assert.equal(await page.locator(`${card('F1')} button.fb-approve`).count(), 0, 'stale: brak przycisku');
    assert.equal(await page.locator(`${card('F1')} pre.fb-body`).count(), 0, 'stale: brak tresci');
    assert.match(await text(page, card('F1')), /Podgląd nieaktualny/);
    assert.match(await text(page, card('F1')), /skillsync feedback draft F1/);
    assert.equal(await page.locator('button.fb-approve').count(), 0, 'zaden kafelek nie ma przycisku');
    assert.deepEqual(fx.approvals(), []);
    assert.deepEqual(errors, []);
  } finally {
    await context.close();
    await stop(srv.child);
  }
});

// ---------------------------------------------------------------- (4) pokrycie generic

test('FB8-4: pokrycie generic: ostrzezenie w oknie, Zgoda zablokowana do zaznaczenia potwierdzenia', { skip: SKIP }, async () => {
  const fx = makeFixture('generic');
  const srv = await serveSingle(fx);
  const { page, context, errors } = await openPage(srv.url);
  try {
    await waitState(page, 'F1', 'waiting');
    await page.click(`${card('F1')} button.fb-approve`);
    await page.waitForSelector('.fb-dialog', { timeout: TIMEOUT });
    assert.match(await text(page, '.fb-dialog .fb-generic-warning'), /nazwy klientów nie są sprawdzane/);
    const confirm = page.locator('.fb-dialog button.fb-confirm');
    assert.ok(await confirm.isDisabled(), 'bez potwierdzenia Zgoda jest nieaktywna');
    assert.equal(await page.locator('.fb-dialog #fbAck').isChecked(), false);
    await page.click('.fb-dialog button.fb-cancel');
    await page.waitForSelector('.fb-dialog', { state: 'detached', timeout: TIMEOUT });
    assert.deepEqual(fx.approvals(), [], 'Anuluj nic nie zapisuje');
    await page.click(`${card('F1')} button.fb-approve`);
    await page.waitForSelector('.fb-dialog', { timeout: TIMEOUT });
    assert.ok(await confirm.isDisabled(), 'nowe okno znowu bez potwierdzenia');
    await page.check('.fb-dialog #fbAck');
    assert.ok(await confirm.isEnabled(), 'po zaznaczeniu Zgoda jest aktywna');
    assert.deepEqual(fx.approvals(), [], 'zaznaczenie samo nic nie zapisuje');
    await page.click('.fb-dialog button.fb-confirm');
    await waitState(page, 'F1', 'after');
    const rows = fx.approvals();
    assert.equal(rows.length, 1);
    assert.equal(rows[0].sha, fx.drafts.F1.sha);
    assert.deepEqual(errors, []);
  } finally {
    await context.close();
    await stop(srv.child);
  }
});

// ---------------------------------------------------------------- (5) motyw jasny i ciemny, kontrast

test('FB8-5: motyw jasny i ciemny bez wyjatkow, kontrast tekstu przyciskow i tresci >= 4.5:1', { skip: SKIP }, async () => {
  const fx = makeFixture('generic');
  const srv = await serveSingle(fx);
  const pageBg = {};
  try {
    for (const scheme of ['light', 'dark']) {
      const { page, context, errors } = await openPage(srv.url, { scheme });
      try {
        await waitState(page, 'F1', 'waiting');
        for (const sel of [`${card('F1')} button.fb-approve`, `${card('F1')} .fb-state`, `${card('F1')} .fb-sha`, `${card('F1')} pre.fb-body`, `${card('F1')} .fb-scan`]) {
          await assertContrast(page, sel, `${scheme}`);
        }
        pageBg[scheme] = (await colorsOf(page, 'body')).bg;
        await page.click(`${card('F1')} button.fb-approve`);
        await page.waitForSelector('.fb-dialog', { timeout: TIMEOUT });
        for (const sel of ['.fb-dialog h3', '.fb-dialog .fb-d-list dt', '.fb-dialog .fb-generic-warning', '.fb-dialog button.fb-cancel', '.fb-dialog .fb-ack-row span']) {
          await assertContrast(page, sel, `${scheme} okno`);
        }
        await page.check('.fb-dialog #fbAck');
        await assertContrast(page, '.fb-dialog button.fb-confirm', `${scheme} okno (aktywne)`);
        assert.deepEqual(errors, [], scheme);
      } finally {
        await context.close();
      }
    }
    // dowod, ze emulacja motywu dziala: tlo ciemnego jest ciemniejsze od jasnego (inaczej oba biegi sprawdzalyby to samo)
    assert.ok(lum(pageBg.dark) < lum(pageBg.light), `tlo dark ${pageBg.dark} nie jest ciemniejsze niz light ${pageBg.light}`);
    assert.deepEqual(fx.approvals(), []);
  } finally {
    await stop(srv.child);
  }
});

// ---------------------------------------------------------------- (6) uklad compact

test('FB8-6: uklad compact (viewport 400 px): przyciski kafelka i okna maja cel dotyku >= 44 px, sekcja miesci sie w oknie', { skip: SKIP }, async () => {
  const fx = makeFixture('generic');
  const srv = await serveSingle(fx);
  const { page, context, errors } = await openPage(srv.url, { width: 400, height: 900 });
  try {
    await waitState(page, 'F1', 'waiting');
    const box = async (sel) => page.locator(sel).first().boundingBox();
    const approve = await box(`${card('F1')} button.fb-approve`);
    assert.ok(approve.height >= 44, `przycisk zgody ma ${approve.height} px`);
    const section = await box('.fb-section');
    assert.ok(section.x >= 0 && section.x + section.width <= 400 + 1, `sekcja wychodzi poza okno: x=${section.x} szer=${section.width}`);
    await page.click(`${card('F1')} button.fb-approve`);
    await page.waitForSelector('.fb-dialog', { timeout: TIMEOUT });
    for (const sel of ['.fb-dialog button.fb-cancel', '.fb-dialog button.fb-confirm']) {
      const b = await box(sel);
      assert.ok(b.height >= 44, `${sel} ma ${b.height} px`);
    }
    const dialog = await box('.fb-dialog');
    assert.ok(dialog.x >= 0 && dialog.x + dialog.width <= 400 + 1, `okno wychodzi poza ekran: x=${dialog.x} szer=${dialog.width}`);
    assert.deepEqual(errors, []);
  } finally {
    await context.close();
    await stop(srv.child);
  }
});

// ---------------------------------------------------------------- (7) fokus, Esc, pulapka Tab

test('FB8-7: klawiatura: Enter otwiera okno, fokus w oknie, Tab i Shift+Tab nie wychodza, Esc zamyka i wraca na przycisk', { skip: SKIP }, async () => {
  const fx = makeFixture('generic');
  const srv = await serveSingle(fx);
  const { page, context, errors } = await openPage(srv.url);
  try {
    await waitState(page, 'F1', 'waiting');
    const trigger = `${card('F1')} button.fb-approve`;
    await page.focus(trigger);
    await page.keyboard.press('Enter');
    await page.waitForSelector('.fb-dialog', { timeout: TIMEOUT });
    assert.equal(await page.getAttribute('.fb-dialog', 'role'), 'dialog');
    assert.equal(await page.getAttribute('.fb-dialog', 'aria-modal'), 'true');
    assert.ok(await inDialog(page), `fokus po otwarciu: ${await activeDescr(page)}`);
    // generic: pierwszy element to pole potwierdzenia; Zgoda jest nieaktywna, wiec poza petla fokusu
    assert.equal(await page.evaluate(() => document.activeElement.id), 'fbAck');
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press('Tab');
      assert.ok(await inDialog(page), `Tab #${i + 1} wyprowadzil fokus: ${await activeDescr(page)}`);
    }
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press('Shift+Tab');
      assert.ok(await inDialog(page), `Shift+Tab #${i + 1} wyprowadzil fokus: ${await activeDescr(page)}`);
    }
    // po zaznaczeniu pola (klawiatura) Zgoda wchodzi do petli: ack -> Anuluj -> Zgoda -> z powrotem na ack
    await page.focus('#fbAck');
    await page.keyboard.press('Space');
    assert.equal(await page.isChecked('#fbAck'), true);
    const order = [];
    for (let i = 0; i < 3; i++) {
      await page.keyboard.press('Tab');
      order.push(await page.evaluate(() => document.activeElement.id || document.activeElement.className));
      assert.ok(await inDialog(page));
    }
    assert.deepEqual(order, ['fb-cancel', 'fb-confirm', 'fbAck']);
    await page.keyboard.press('Escape');
    await page.waitForSelector('.fb-dialog', { state: 'detached', timeout: TIMEOUT });
    assert.equal(await page.evaluate((sel) => document.activeElement === document.querySelector(sel), trigger), true,
      `fokus po Esc: ${await activeDescr(page)}`);
    assert.deepEqual(fx.approvals(), [], 'Esc nic nie zapisuje');
    assert.deepEqual(errors, []);
  } finally {
    await context.close();
    await stop(srv.child);
  }
});

// ---------------------------------------------------------------- (8) gniazdo tylko do odczytu

test('FB8-8: gniazdo tylko do odczytu (dwa porty) pokazuje liczbe i stany, bez tresci, sha i przycisku; zapis = 403', { skip: SKIP }, async () => {
  const fx = makeFixture('full', 'stale');
  const srv = await startServer(fx, { count: 2, portArgs: ([r, a]) => ['--read-port', String(r), '--approve-port', String(a), '--read-bind', '127.0.0.1', '--approve-bind', '127.0.0.1'] });
  const [readPort, approvePort] = srv.ports;
  const secret = 'Add the check to the step.';
  const approveSide = await openPage(tokenUrl(fx, approvePort));
  const readSide = await openPage(`http://127.0.0.1:${readPort}/`);
  try {
    // przypadek kontrolny: gniazdo zgod pokazuje te same szkice z trescia, sha i przyciskiem (asercje negatywne nizej potrafia padac)
    await waitState(approveSide.page, 'F1', 'waiting');
    assert.equal(await approveSide.page.locator('button.fb-approve').count(), 1);
    assert.equal(await approveSide.page.locator('pre.fb-body').count(), 1);
    assert.ok((await approveSide.page.locator('body').innerText()).includes(secret));
    assert.ok((await approveSide.page.locator('body').innerText()).includes(fx.drafts.F1.sha12));
    // gniazdo odczytu
    await waitState(readSide.page, 'F1', 'waiting');
    await waitState(readSide.page, 'F2', 'stale');
    assert.match(await text(readSide.page, '.fb-count'), /2 szkice/);
    assert.equal(await readSide.page.locator('button.fb-approve').count(), 0);
    assert.equal(await readSide.page.locator('pre.fb-body').count(), 0);
    assert.equal(await readSide.page.locator('.fb-sha').count(), 0);
    const all = await readSide.page.locator('body').innerText();
    for (const hidden of [secret, fx.drafts.F1.sha12, fx.drafts.F2.sha12, 'example-org']) assert.ok(!all.includes(hidden), `gniazdo odczytu ujawnia: ${hidden}`);
    // stan tego gniazda i trasa zapisu bez przegladarki
    const status = await (await fetch(`http://127.0.0.1:${readPort}/api/status`)).json();
    assert.deepEqual(status.feedback.drafts.map((d) => Object.keys(d).sort()), [['draft', 'state'], ['draft', 'state']]);
    const post = await fetch(`http://127.0.0.1:${readPort}/api/feedback-approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Status-Board': '1', Origin: `http://127.0.0.1:${readPort}` },
      body: JSON.stringify({ draft: 'F1', sha: fx.drafts.F1.sha }),
    });
    assert.equal(post.status, 404, 'the read socket has no write route at all');
    assert.deepEqual(fx.approvals(), []);
    assert.deepEqual(readSide.errors, []);
    assert.deepEqual(approveSide.errors, []);
  } finally {
    await readSide.context.close();
    await approveSide.context.close();
    await stop(srv.child);
  }
});

// ---------------------------------------------------------------- (9) tekst ze szkicu tylko jako tekst

test('FB8-9: tekst szkicu z znacznikami jest pokazany jako tekst (bez elementow i skryptow)', { skip: SKIP }, async () => {
  const fx = makeFixture('xss');
  const srv = await serveSingle(fx);
  const { page, context, errors } = await openPage(srv.url);
  try {
    await waitState(page, 'F1', 'waiting');
    // dowod, ze niebezpieczny tekst dotarl na ekran (jako tekst), wiec brak elementow nie wynika z jego zgubienia
    assert.ok((await page.locator(`${card('F1')} pre.fb-body`).textContent()).includes('&lt;img'), 'znacznik powinien byc widoczny jako tekst (opis HTML pokazany jako zwykly tekst, znaki ucieczki widoczne)');
    assert.match(await text(page, `${card('F1')} .fb-title`), /<\/pre>/);
    assert.equal(await page.locator(`${card('F1')} pre.fb-body *`).count(), 0);
    assert.equal(await page.locator('.fb-section img, .fb-section script').count(), 0);
    assert.equal(await page.locator(`${card('F1')} pre.fb-body`).count(), 1, 'jeden <pre>: `</pre>` ze szkicu go nie zamknal');
    assert.equal(await page.evaluate(() => window.__xss), undefined);
    assert.deepEqual(errors, []);
  } finally {
    await context.close();
    await stop(srv.child);
  }
});
