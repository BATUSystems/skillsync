// Srodowisko uruchomieniowe narzedzi obrazowych: katalog cache, ladowanie pngjs/pixelmatch, kodowanie PNG, flagger pixelmatch.
import path from 'node:path';
import nodeFs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { cacheDirFor, computeHash, defaultCacheRoot } from './paths.mjs';
import { createLoader, DepsError } from './deps.mjs';
import { checkCacheSafety } from './install-core.mjs';

// Katalog skilla = rodzic katalogu `scripts` (plik skryptu lezy w scripts/).
export function skillDirOf(importMetaUrl) {
  return path.dirname(path.dirname(fileURLToPath(importMetaUrl)));
}

// Ta sama kontrola co przy instalacji (checkCacheSafety), zanim kod z cache zostanie zaladowany; blad to DepsError z kodem 2.
export function assertCacheSafe({ root, skillDir, platform = process.platform, fs = nodeFs }) {
  try {
    checkCacheSafety({ root, skillDir, platform, fs });
  } catch (e) {
    if (e && e.name === 'InstallError') {
      throw new DepsError('unsafe-cache', e.message, 'Wskaz katalog cache nalezacy do biezacego uzytkownika, bez zapisu dla grupy i innych, poza katalogiem skilla.', 2);
    }
    throw e;
  }
}

export function resolveCacheDir({ skillDir, env = {}, platform = process.platform, homedir, fs = nodeFs }) {
  const override = typeof env.UI_VERIFY_CACHE_DIR === 'string' ? env.UI_VERIFY_CACHE_DIR : '';
  if (override !== '' && !(platform === 'win32' ? path.win32 : path.posix).isAbsolute(override)) {
    throw new DepsError('unsafe-cache', 'UI_VERIFY_CACHE_DIR must be an absolute path', 'Ustaw UI_VERIFY_CACHE_DIR na sciezke bezwzgledna.', 2);
  }
  const contents = ['package.json', 'package-lock.json'].map((name) => {
    try {
      return String(fs.readFileSync(path.join(skillDir, name)));
    } catch {
      throw new DepsError('not-installed', `brak pliku skilla ${name}`, 'Zainstaluj skill ponownie (brak package.json lub package-lock.json).');
    }
  });
  const root = defaultCacheRoot({ platform, env, homedir });
  assertCacheSafe({ root, skillDir, platform, fs });
  return cacheDirFor(root, computeHash(contents), platform);
}

export async function loadImaging({ cacheDir, env = {}, fs = nodeFs, requireFactory, importer, skillDir, platform = process.platform }) {
  assertCacheSafe({ root: (platform === 'win32' ? path.win32 : path.posix).dirname(cacheDir), skillDir, platform, fs });
  const opts = { cacheDir, env, fs };
  if (requireFactory) opts.requireFactory = requireFactory;
  if (importer) opts.importer = importer;
  const loader = createLoader(opts);
  const pngMod = await loader.load('pngjs');
  const pmMod = await loader.load('pixelmatch');
  const PNG = pngMod && pngMod.PNG ? pngMod.PNG : pngMod;
  const pixelmatch = pmMod && typeof pmMod.default === 'function' ? pmMod.default : pmMod;
  return { PNG, pixelmatch };
}

// sass.js (LibSass) zawsze z cache zestawu, po tej samej kontroli bezpieczenstwa co loadImaging.
export async function loadSass({ cacheDir, env = {}, fs = nodeFs, requireFactory, importer, skillDir, platform = process.platform }) {
  assertCacheSafe({ root: (platform === 'win32' ? path.win32 : path.posix).dirname(cacheDir), skillDir, platform, fs });
  const opts = { cacheDir, env, fs };
  if (requireFactory) opts.requireFactory = requireFactory;
  if (importer) opts.importer = importer;
  return createLoader(opts).load('sass.js');
}

export function decodePng(PNG, buffer) {
  const p = PNG.sync.read(buffer);
  return { width: p.width, height: p.height, data: new Uint8ClampedArray(p.data) };
}

export function encodePng(PNG, img) {
  return Buffer.from(PNG.sync.write({ width: img.width, height: img.height, data: Buffer.from(img.data) }));
}

// Wektor 0/1: piksel rozny, gdy pixelmatch zapisal w wyjsciu dokladnie diffColor (255,0,0); AA (255,255,0) to nie roznica.
export function makePixelmatchFlagger(pixelmatch, { threshold = 0.1, includeAA = false } = {}) {
  return (a, b) => {
    const { width, height } = a;
    const output = new Uint8ClampedArray(width * height * 4);
    pixelmatch(a.data, b.data, output, width, height, {
      threshold,
      includeAA,
      alpha: 0,
      diffColor: [255, 0, 0],
      aaColor: [255, 255, 0],
    });
    const flags = new Uint8Array(width * height);
    for (let i = 0; i < flags.length; i++) {
      const o = i * 4;
      if (output[o] === 255 && output[o + 1] === 0 && output[o + 2] === 0) flags[i] = 1;
    }
    return flags;
  };
}
