// Adapter Playwright dla `measure`: uruchomienie Chromium i pomiar elementu (pudelko, style, zrzut PNG).
// Nie jest testowany lokalnie bez Chrome; pokrywa go measure-e2e.test.mjs (pomijany z powodem e2e-chrome).
import nodeFs from 'node:fs';
import path from 'node:path';
import { createLoader } from './deps.mjs';
import { launchOptions } from './chrome.mjs';
import { checkOrigin } from './policy.mjs';
import { decodePng } from './runtime.mjs';

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
