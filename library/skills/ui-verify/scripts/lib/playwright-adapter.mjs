// Adapter Playwright dla `measure`: uruchomienie Chromium i pomiar elementu (pudelko, style, zrzut PNG).
// Nie jest testowany lokalnie bez Chrome; pokrywa go measure-e2e.test.mjs (pomijany z powodem e2e-chrome).
import nodeFs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createLoader } from './deps.mjs';
import { launchOptions } from './chrome.mjs';
import { assertRealInside, checkOrigin, PolicyError } from './policy.mjs';
import { applyMasks } from './pixels.mjs';
import { assertCacheSafe, decodePng, encodePng } from './runtime.mjs';

const NAV_TIMEOUT_MS = 30000;
const ACTION_TIMEOUT_MS = 10000;
const SETTLE_MS = 150;
const DEFAULT_ASSERT_TIMEOUT_MS = 5000;

// --- polityka nawigacji (B1), symlinki file:// (B3), zly stan strony (C5) ---

// Pochodzenie do komunikatu: bez sciezki, query i fragmentu (moga nosic sekrety).
function originOf(url) {
  try {
    const u = new URL(String(url));
    if (u.protocol === 'file:') return 'file://';
    return u.origin !== 'null' ? u.origin : u.protocol;
  } catch {
    return 'unparsable URL';
  }
}

const blocked = (url) => new PolicyError('navigation-blocked', `navigation blocked: ${originOf(url)}`);

// Handler context.route: niedozwolona nawigacja (glowna lub ramka) jest przerywana i zapamietywana; operacja konczy sie
// PolicyError (kod 2). Subzasoby (obrazy, skrypty, fetch) NIE sa objete polityka (znana granica).
export function navigationHandler({ configDir, allowOrigins, violations }) {
  return async (route, request) => {
    const req = request || route.request();
    if (req.isNavigationRequest()) {
      const url = req.url();
      let ok = checkOrigin(url, { configDir, allow: allowOrigins }).ok;
      if (ok && String(url).startsWith('file:')) {
        try {
          assertRealInside(configDir, fileURLToPath(url), nodeFs);
        } catch {
          ok = false;
        }
      }
      if (!ok) {
        violations.push(blocked(url));
        await route.abort();
        return;
      }
    }
    await route.continue();
  };
}

async function installNavigationPolicy(context, { configDir, allowOrigins }) {
  const violations = [];
  await context.route('**/*', navigationHandler({ configDir, allowOrigins, violations }));
  return {
    check() {
      if (violations.length) {
        const first = violations[0];
        violations.length = 0;
        throw first;
      }
    },
  };
}

// Adres aktualnej strony musi miec dozwolony origin (po przekierowaniu poza polityke: blad PRZED wpisaniem sekretu / zrzutem).
function assertPageAllowed(page, guard, { configDir, allowOrigins }) {
  guard.check();
  const url = page.url();
  if (url === 'about:blank') return;
  if (!checkOrigin(url, { configDir, allow: allowOrigins }).ok) throw blocked(url);
}

function assertFileInside(url, configDir) {
  if (String(url).startsWith('file:')) assertRealInside(configDir, fileURLToPath(url), nodeFs);
}

function pathKey(url) {
  try {
    const u = new URL(String(url));
    const p = u.protocol === 'file:' ? fileURLToPath(u) : u.pathname;
    return `${u.protocol}//${u.host}${p.length > 1 ? p.replace(/[\\/]+$/, '') : p}`;
  } catch {
    return String(url);
  }
}

// Przekierowanie (inny adres po nawigacji niz zadany; query i fragment pomijane) = zly stan strony.
export const stateMismatch = (requested, finalUrl) => pathKey(requested) !== pathKey(finalUrl);

async function settle(page) {
  try {
    await page.waitForLoadState('load', { timeout: ACTION_TIMEOUT_MS });
  } catch { /* nawigacja mogla juz sie zakonczyc */ }
  try {
    await page.evaluate((ms) => new Promise((resolve) => globalThis.setTimeout(resolve, ms)), SETTLE_MS);
  } catch { /* kontekst zniszczony przez trwajaca nawigacje */ }
}

// goto z polityka: zablokowana nawigacja ma pierwszenstwo przed bledem goto; krotkie "uspokojenie" lapie przekierowania
// wykonane skryptem strony tuz po zdarzeniu load.
async function gotoGuarded(page, url, guard) {
  try {
    await page.goto(url, { waitUntil: 'load', timeout: NAV_TIMEOUT_MS });
  } catch (e) {
    guard.check();
    if (!/interrupted by another navigation/i.test(String(e && e.message))) throw e;
  }
  await settle(page);
  guard.check();
}

// which() po PATH z podanego env (bez procesu potomnego); na Windows rozszerzenia z PATHEXT.
export function makeWhich({ env = process.env, fs = nodeFs, platform = process.platform } = {}) {
  const sep = platform === 'win32' ? ';' : ':';
  const exts = platform === 'win32' ? ['', ...String(env.PATHEXT || '.EXE').split(';').filter(Boolean)] : [''];
  return (name) => {
    for (const dir of String(env.PATH || env.Path || '').split(sep).filter(Boolean)) {
      for (const ext of exts) {
        const candidate = path.join(dir, name + ext);
        try {
          if (fs.existsSync(candidate)) return candidate;
        } catch { /* nieczytelny katalog PATH */ }
      }
    }
    return null;
  };
}

export async function loadPlaywright({ cacheDir, env = {}, fs = nodeFs }) {
  if (!(typeof env.PLAYWRIGHT_MODULE_PATH === 'string' && env.PLAYWRIGHT_MODULE_PATH !== '')) {
    assertCacheSafe({ root: path.dirname(cacheDir), fs });
  }
  const mod = await createLoader({ cacheDir, env, fs }).load('playwright-core');
  const pw = mod && mod.chromium ? mod : mod && mod.default && mod.default.chromium ? mod.default : null;
  if (!pw) throw new Error('playwright-core nie udostepnia chromium');
  return pw;
}

// Uruchamia Chromium z opcjami z chrome.mjs; wywolujacy zamyka przegladarke w `finally`.
export async function launchChromium({ cacheDir, chromePath, env = {}, fs = nodeFs }) {
  const pw = await loadPlaywright({ cacheDir, env, fs });
  return pw.chromium.launch(launchOptions({ chromePath, env }));
}

// Adapter dla runMeasure: measure(side) otwiera osobny kontekst i strone, mierzy element i robi zrzut (animacje wylaczone);
// screenshot(side) zwraca zdekodowany obraz RGBA z tego samego pomiaru. Kontekst i strona sa zamykane w `finally`.
export function createPlaywrightAdapter({ browser, PNG, config, configDir, allowOrigins = [] }) {
  const shots = new WeakMap();
  const { width, height, deviceScaleFactor } = config.viewport;

  return {
    async measure(side) {
      // Polityka origin sprawdzana jeszcze raz tuz przed nawigacja (walidacja konfiguracji nie jest jedyna bramka).
      const verdict = checkOrigin(side.url, { configDir, allow: allowOrigins });
      if (!verdict.ok) throw new Error(`origin not allowed: ${verdict.reason}`);
      assertFileInside(side.url, configDir);
      const context = await browser.newContext({
        viewport: { width, height },
        deviceScaleFactor,
        reducedMotion: 'reduce',
        serviceWorkers: 'block',
        acceptDownloads: false,
      });
      let page = null;
      try {
        const guard = await installNavigationPolicy(context, { configDir, allowOrigins });
        page = await context.newPage();
        page.setDefaultTimeout(NAV_TIMEOUT_MS);
        await gotoGuarded(page, side.url, guard);
        assertPageAllowed(page, guard, { configDir, allowOrigins });
        if (stateMismatch(side.url, page.url())) return { found: false, box: null, styles: {}, reason: 'page-state-mismatch' };
        const all = page.locator(side.selector);
        if ((await all.count()) === 0) return { found: false, box: null, styles: {} };
        const locator = all.first();
        const box = await locator.boundingBox();
        if (!box) return { found: false, box: null, styles: {} };
        const styles = await locator.evaluate((el, props) => {
          const cs = globalThis.getComputedStyle(el);
          const out = {};
          for (const p of props) out[p] = cs[p];
          return out;
        }, config.styleProps);
        assertPageAllowed(page, guard, { configDir, allowOrigins });
        // Ponowna kontrola tuz przed zrzutem: SPA mogla przekierowac po fetch juz po pierwszym sprawdzeniu.
        if (stateMismatch(side.url, page.url())) return { found: false, box: null, styles: {}, reason: 'page-state-mismatch' };
        shots.set(side, await locator.screenshot({ type: 'png', animations: 'disabled' }));
        return { found: true, box: { x: box.x, y: box.y, width: box.width, height: box.height }, styles };
      } finally {
        try {
          if (page) await page.close();
        } finally {
          await context.close();
        }
      }
    },

    async screenshot(side) {
      const buf = shots.get(side);
      if (!buf) throw new Error('no screenshot for this side (measure it first)');
      return decodePng(PNG, buf);
    },
  };
}

// Asercja pozytywna: element (z tekstem, gdy podany) musi pojawic sie w limicie; 0 = sprawdzenie natychmiast
// (w Playwright timeout 0 oznacza brak limitu, dlatego ten przypadek jest obslugiwany jawnie).
async function checkAssertion(page, { selector, text }, timeoutMs) {
  const hasText = () => page.locator(selector).evaluateAll((els, t) => els.some((el) => (el.textContent || '').includes(t)), text);
  if (timeoutMs === 0) {
    return text === undefined ? (await page.locator(selector).count()) > 0 : hasText();
  }
  const deadline = Date.now() + timeoutMs;
  try {
    await page.locator(selector).first().waitFor({ state: 'attached', timeout: timeoutMs });
  } catch (e) {
    if (e && e.name === 'TimeoutError') return false;
    throw e;
  }
  if (text === undefined) return true;
  for (;;) {
    if (await hasText()) return true;
    if (Date.now() >= deadline) return false;
    await new Promise((resolve) => globalThis.setTimeout(resolve, 100));
  }
}

// Adapter dla runCapture: jeden osobny kontekst na caly bieg (logowanie raz, ciasteczka wspolne dla stron);
// szerokosc i tryb kolorow zmieniane na stronie. Maski selektorem to prostokaty bounding box zamalowane w obrazie PO zrzucie
// (w ukladzie zrzutu: przy fullPage z przesunieciem scroll, razy deviceScaleFactor). Trace i storageState tylko z allowTrace.
// Krotki limit akcji (10 s), by bledny selektor logowania nie wisial 30 s.
export function createCaptureAdapter({ browser, PNG, config, configDir, allowOrigins = [], allowTrace = false }) {
  const { height, deviceScaleFactor } = config.viewport;
  let context = null;
  let guard = null;
  let loggedIn = false;
  const policy = { configDir, allowOrigins };

  const gate = (url) => {
    const verdict = checkOrigin(url, { configDir, allow: allowOrigins });
    if (!verdict.ok) throw new Error(`origin not allowed: ${verdict.reason}`);
  };
  const ensureContext = async () => {
    if (context) return context;
    const created = await browser.newContext({
      viewport: { width: config.widths[0], height },
      deviceScaleFactor,
      reducedMotion: 'reduce',
      colorScheme: 'light',
      serviceWorkers: 'block',
      acceptDownloads: false,
    });
    context = created;
    guard = await installNavigationPolicy(context, policy);
    context.setDefaultTimeout(ACTION_TIMEOUT_MS);
    if (allowTrace) await context.tracing.start({ screenshots: true, snapshots: true });
    return context;
  };

  return {
    async login({ login, env }) {
      gate(login.url);
      assertFileInside(login.url, configDir);
      const ctx = await ensureContext();
      const page = await ctx.newPage();
      try {
        await gotoGuarded(page, login.url, guard);
        for (const step of login.steps) {
          // Przed kazdym wpisem/kliknieciem: strona musi byc nadal w polityce (przekierowanie = blad 2, sekret nie wpisany).
          assertPageAllowed(page, guard, policy);
          if (step.fill) await page.locator(step.fill.selector).first().fill(String(env[step.fill.valueEnv] ?? ''), { timeout: ACTION_TIMEOUT_MS });
          else await page.locator(step.click).first().click({ timeout: ACTION_TIMEOUT_MS });
        }
        try {
          await page.waitForLoadState('load', { timeout: ACTION_TIMEOUT_MS });
        } catch { /* nawigacja po logowaniu mogla juz sie zakonczyc */ }
        assertPageAllowed(page, guard, policy);
        loggedIn = true;
      } finally {
        await page.close();
      }
    },

    async capture({ page: spec, width, mode }) {
      gate(spec.url);
      assertFileInside(spec.url, configDir);
      const ctx = await ensureContext();
      const page = await ctx.newPage();
      try {
        await page.setViewportSize({ width, height });
        await page.emulateMedia({ colorScheme: mode, reducedMotion: 'reduce' });
        await gotoGuarded(page, spec.url, guard);
        assertPageAllowed(page, guard, policy);
        if (stateMismatch(spec.url, page.url())) {
          const png = await page.screenshot({ type: 'png', fullPage: spec.fullPage === true, animations: 'disabled' });
          return { png, assertionFound: false, stateMismatch: true };
        }
        if (spec.waitFor) await page.locator(spec.waitFor).first().waitFor({ state: 'visible', timeout: ACTION_TIMEOUT_MS });
        let assertionFound = false;
        if (spec.assert) {
          assertionFound = await checkAssertion(page, spec.assert, spec.assertTimeoutMs ?? config.assertTimeoutMs ?? DEFAULT_ASSERT_TIMEOUT_MS);
        }
        const rects = [];
        const scroll = spec.fullPage ? await page.evaluate(() => [globalThis.scrollX, globalThis.scrollY]) : [0, 0];
        for (const m of spec.masks) {
          if (m.selector) {
            for (const loc of await page.locator(m.selector).all()) {
              const box = await loc.boundingBox();
              if (box) rects.push({ x: box.x + scroll[0], y: box.y + scroll[1], width: box.width, height: box.height });
            }
          } else {
            rects.push({ x: m.x, y: m.y, width: m.width, height: m.height });
          }
        }
        assertPageAllowed(page, guard, policy);
        // Ponowna kontrola tuz przed zrzutem (przekierowanie SPA po fetch, po pierwszym sprawdzeniu).
        if (stateMismatch(spec.url, page.url())) {
          const shot = await page.screenshot({ type: 'png', fullPage: spec.fullPage === true, animations: 'disabled' });
          return { png: shot, assertionFound: false, stateMismatch: true };
        }
        let png = await page.screenshot({ type: 'png', fullPage: spec.fullPage === true, animations: 'disabled' });
        if (rects.length) {
          const k = deviceScaleFactor;
          const img = applyMasks(decodePng(PNG, png), rects.map((r) => ({ x: r.x * k, y: r.y * k, width: r.width * k, height: r.height * k })));
          png = encodePng(PNG, img);
        }
        return { png, assertionFound };
      } finally {
        await page.close();
      }
    },

    // Zwraca artefakty wymagajace --allow-trace ({ trace: Buffer, storageState: string|null}); bez allowTrace nic nie zbiera.
    async finish() {
      if (!allowTrace || !context) return { trace: null, storageState: null };
      const dir = nodeFs.mkdtempSync(path.join(os.tmpdir(), 'uv-trace-'));
      try {
        const file = path.join(dir, 'trace.zip');
        await context.tracing.stop({ path: file });
        const storage = loggedIn ? JSON.stringify(await context.storageState(), null, 2) : null;
        return { trace: nodeFs.readFileSync(file), storageState: storage };
      } finally {
        nodeFs.rmSync(dir, { recursive: true, force: true });
      }
    },

    async close() {
      if (context) {
        const c = context;
        context = null;
        await c.close();
      }
    },
  };
}
