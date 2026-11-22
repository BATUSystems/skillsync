// Adapter Playwright dla `measure`: uruchomienie Chromium i pomiar elementu (pudelko, style, zrzut PNG).
// Nie jest testowany lokalnie bez Chrome; pokrywa go measure-e2e.test.mjs (pomijany z powodem e2e-chrome).
import nodeFs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createLoader } from './deps.mjs';
import { launchOptions } from './chrome.mjs';
import { checkOrigin } from './policy.mjs';
import { applyMasks } from './pixels.mjs';
import { decodePng, encodePng } from './runtime.mjs';

const NAV_TIMEOUT_MS = 30000;

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
      const context = await browser.newContext({
        viewport: { width, height },
        deviceScaleFactor,
        reducedMotion: 'reduce',
        serviceWorkers: 'block',
      });
      let page = null;
      try {
        page = await context.newPage();
        page.setDefaultTimeout(NAV_TIMEOUT_MS);
        await page.goto(side.url, { waitUntil: 'load', timeout: NAV_TIMEOUT_MS });
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

const ACTION_TIMEOUT_MS = 10000;

// Adapter dla runCapture: jeden osobny kontekst na caly bieg (logowanie raz, ciasteczka wspolne dla stron);
// szerokosc i tryb kolorow zmieniane na stronie. Maski selektorem to prostokaty bounding box zamalowane w obrazie PO zrzucie
// (w ukladzie zrzutu: przy fullPage z przesunieciem scroll, razy deviceScaleFactor). Trace i storageState tylko z allowTrace.
// Krotki limit akcji (10 s), by bledny selektor logowania nie wisial 30 s.
export function createCaptureAdapter({ browser, PNG, config, configDir, allowOrigins = [], allowTrace = false }) {
  const { height, deviceScaleFactor } = config.viewport;
  let context = null;
  let loggedIn = false;

  const gate = (url) => {
    const verdict = checkOrigin(url, { configDir, allow: allowOrigins });
    if (!verdict.ok) throw new Error(`origin not allowed: ${verdict.reason}`);
  };
  const ensureContext = async () => {
    if (context) return context;
    context = await browser.newContext({
      viewport: { width: config.widths[0], height },
      deviceScaleFactor,
      reducedMotion: 'reduce',
      colorScheme: 'light',
      serviceWorkers: 'block',
      acceptDownloads: false,
    });
    context.setDefaultTimeout(ACTION_TIMEOUT_MS);
    if (allowTrace) await context.tracing.start({ screenshots: true, snapshots: true });
    return context;
  };

  return {
    async login({ login, env }) {
      gate(login.url);
      const ctx = await ensureContext();
      const page = await ctx.newPage();
      try {
        await page.goto(login.url, { waitUntil: 'load', timeout: NAV_TIMEOUT_MS });
        for (const step of login.steps) {
          if (step.fill) await page.locator(step.fill.selector).first().fill(String(env[step.fill.valueEnv] ?? ''), { timeout: ACTION_TIMEOUT_MS });
          else await page.locator(step.click).first().click({ timeout: ACTION_TIMEOUT_MS });
        }
        try {
          await page.waitForLoadState('load', { timeout: ACTION_TIMEOUT_MS });
        } catch { /* nawigacja po logowaniu mogla juz sie zakonczyc */ }
        loggedIn = true;
      } finally {
        await page.close();
      }
    },

    async capture({ page: spec, width, mode }) {
      gate(spec.url);
      const ctx = await ensureContext();
      const page = await ctx.newPage();
      try {
        await page.setViewportSize({ width, height });
        await page.emulateMedia({ colorScheme: mode, reducedMotion: 'reduce' });
        await page.goto(spec.url, { waitUntil: 'load', timeout: NAV_TIMEOUT_MS });
        if (spec.waitFor) await page.locator(spec.waitFor).first().waitFor({ state: 'visible', timeout: ACTION_TIMEOUT_MS });
        let assertionFound = false;
        if (spec.assert) {
          const { selector, text } = spec.assert;
          assertionFound = text === undefined
            ? (await page.locator(selector).count()) > 0
            : await page.locator(selector).evaluateAll((els, t) => els.some((el) => (el.textContent || '').includes(t)), text);
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
